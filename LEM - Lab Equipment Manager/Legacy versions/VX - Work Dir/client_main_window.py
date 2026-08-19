#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
client_main_window.py - Client-side window that syncs with server.py

Wraps the existing MainWindow and overrides data operations to use HTTP.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from PyQt5.QtCore import QTimer

from main_window import MainWindow
from models import AppConfig, BoxConfig
from remote_store import get_state, get_config, post_action


class ClientMainWindow(MainWindow):
    def __init__(self) -> None:
        raw_cfg: Dict[str, object] = {}
        server_cfg: Optional[AppConfig] = None
        try:
            raw_cfg = get_config() or {}
            if raw_cfg:
                server_cfg = AppConfig.from_dict(raw_cfg)
        except Exception:
            raw_cfg = {}
            server_cfg = None
        if server_cfg is None:
            server_cfg = AppConfig(version=5, poll_minutes=5, map_locked=False)

        super().__init__(initial_config=server_cfg)

        # Ensure local sample lookup reflects server seed (or default fallback)
        self.samples_by_name = {s.name: s for s in self.cfg.samples}
        self._server_config_payload = raw_cfg if raw_cfg else None

        # Replace timer with server poller
        try:
            self.timer.stop()
        except Exception:
            pass
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_all)
        self.timer.start(2000)  # 2s client poll; server independently polls CSV

        self._suppress_save = False
        self._online = True
        self._fail_count = 0
        self._pending_actions = []  # list of (name, payload)
        self._pending_pos = {}      # uid -> payload
        self._last_state_version = -1

        # Initial fetch
        self.refresh_all()
        if self._server_config_payload is None:
            self._refresh_server_config(force=True)

    # ----- override persistence to server -----
    def save_config(self) -> None:  # type: ignore[override]
        if getattr(self, "_suppress_save", False):
            return
        try:
            # Push box positions/sizes/locks
            for uid, item in list(self.box_items.items()):
                payload = {
                    "uid": uid,
                    "pos": [float(item.pos().x()), float(item.pos().y())],
                    "size": [float(item.rect().width()), float(item.rect().height())],
                    "locked": bool(item.box.locked),
                }
                self._post_or_queue("update_box_pos_size", payload, coalesce_pos=True)
            # Push selected server-side settings
            self._post_or_queue("settings", {
                "map_locked": bool(self.cfg.map_locked),
                "poll_minutes": int(getattr(self.cfg, 'poll_minutes', 5) or 5),
                "status_log_dir": str(getattr(self.cfg, 'status_log_dir', '') or ''),
                "theme_mode": str(getattr(self.cfg, 'theme_mode', 'light') or 'light'),
                "ui_scale": float(getattr(self.cfg, 'ui_scale', 1.0) or 1.0),
            })
        except Exception:
            pass

    def _log_manual_override(self, box: BoxConfig, action: str, user: str, note: str) -> None:  # type: ignore[override]
        mode = "OFF"
        if "DEAD-LINE" in action:
            mode = "DEAD-LINE" if "ON" in action else "OFF"
        if "SERVICE" in action:
            mode = "SERVICE" if "ON" in action else "OFF"
        self._post_or_queue("manual_override", {
            "uid": box.uid,
            "mode": mode,
            "user": user,
            "note": note,
        })

    # ----- override refresh to pull from server -----
    def refresh_all(self) -> None:  # type: ignore[override]
        state = get_state() or {}
        if not state:
            self._set_online(False)
            return
        self._set_online(True)
        try:
            self._suppress_save = True

            if self._server_config_payload is None:
                self._refresh_server_config()

            version_raw = state.get("state_version", -1)
            try:
                state_version = int(version_raw)
            except (TypeError, ValueError):
                state_version = -1
            if state_version >= 0 and state_version == self._last_state_version:
                self._flush_queue()
                return

            srv_locked = bool(state.get("map_locked", False))
            if bool(self.cfg.map_locked) != srv_locked:
                self._toggle_map_lock(srv_locked)
            else:
                self.cfg.map_locked = srv_locked

            poll_minutes = int(state.get("poll_minutes", getattr(self.cfg, "poll_minutes", 5)))
            if poll_minutes != getattr(self.cfg, "poll_minutes", poll_minutes):
                self.cfg.poll_minutes = poll_minutes
                try:
                    self.poll_spin.blockSignals(True)
                    self.poll_spin.setValue(poll_minutes)
                except Exception:
                    pass
                finally:
                    try:
                        self.poll_spin.blockSignals(False)
                    except Exception:
                        pass

            self.cfg.theme_mode = str(state.get("theme_mode", self.cfg.theme_mode))
            try:
                self.cfg.ui_scale = float(state.get("ui_scale", self.cfg.ui_scale))
            except Exception:
                pass
            self.cfg.sample_id_column = str(state.get("sample_id_column", self.cfg.sample_id_column or "Lab ID") or "Lab ID")
            self.sample_id_column = self.cfg.sample_id_column or "Lab ID"

            boxes: List[dict] = state.get("boxes", [])
            seen_order: List[str] = []
            cfg_by_uid = {b.uid: b for b in self.cfg.boxes}

            for entry in boxes:
                uid = str(entry.get("uid") or "")
                if not uid:
                    continue
                if uid not in seen_order:
                    seen_order.append(uid)

                item = self.box_items.get(uid)
                title = str(entry.get("title", item.box.title if item else "Machine"))

                pos_vals = entry.get("pos")
                if not pos_vals:
                    pos_vals = [item.box.pos[0], item.box.pos[1]] if item else [20.0, 20.0]
                size_vals = entry.get("size")
                if not size_vals:
                    size_vals = [item.box.size[0], item.box.size[1]] if item else [240.0, 130.0]

                try:
                    pos_tuple = (float(pos_vals[0]), float(pos_vals[1]))
                except Exception:
                    pos_tuple = tuple(item.box.pos) if item else (20.0, 20.0)
                try:
                    size_tuple = (float(size_vals[0]), float(size_vals[1]))
                except Exception:
                    size_tuple = tuple(item.box.size) if item else (240.0, 130.0)

                locked = bool(entry.get("locked", item.box.locked if item else False))
                manual_override = str(entry.get("manual_override", item.box.manual_override if item else ""))

                if not item:
                    bc = BoxConfig(
                        uid=uid,
                        title=title,
                        csv_path="",
                        pos=pos_tuple,
                        size=size_tuple,
                        locked=locked,
                        manual_override=manual_override,
                    )
                    self._add_box_item(bc)
                    item = self.box_items.get(uid)

                if item:
                    if item.box.title != title:
                        item.box.title = title
                        try:
                            item._refresh_text_layout()  # type: ignore[attr-defined]
                        except Exception:
                            pass
                    if tuple(item.box.size) != size_tuple:
                        try:
                            item.update_size(size_tuple[0], size_tuple[1])
                        except Exception:
                            pass
                        item.box.size = size_tuple
                    if tuple(item.box.pos) != pos_tuple:
                        try:
                            item.setPos(pos_tuple[0], pos_tuple[1])
                        except Exception:
                            pass
                        item.box.pos = pos_tuple
                    if bool(item.box.locked) != locked:
                        item.box.locked = locked
                        item.sync_lock_state()
                    item.box.manual_override = manual_override
                    item.set_status(
                        str(entry.get("status", "UNKNOWN")),
                        str(entry.get("reason", "")),
                        list(entry.get("lines", [])),
                    )

                cfg_box = cfg_by_uid.get(uid)
                if not cfg_box:
                    cfg_box = BoxConfig(
                        uid=uid,
                        title=title,
                        csv_path="",
                        pos=pos_tuple,
                        size=size_tuple,
                        locked=locked,
                        manual_override=manual_override,
                    )
                    self.cfg.boxes.append(cfg_box)
                    cfg_by_uid[uid] = cfg_box
                else:
                    cfg_box.title = title
                    cfg_box.pos = pos_tuple
                    cfg_box.size = size_tuple
                    cfg_box.locked = locked
                    cfg_box.manual_override = manual_override

            for uid in list(self.box_items.keys()):
                if uid not in seen_order:
                    item = self.box_items.pop(uid, None)
                    if item:
                        try:
                            self.scene.removeItem(item)
                        except Exception:
                            pass
                    cfg_by_uid.pop(uid, None)

            if seen_order:
                self.cfg.boxes = [cfg_by_uid[uid] for uid in seen_order if uid in cfg_by_uid]
            else:
                self.cfg.boxes = []

            if state_version >= 0:
                self._last_state_version = state_version
            else:
                self._last_state_version = -1

            self._flush_queue()
        finally:
            self._suppress_save = False

    # ----- box ops to server -----
    def add_box(self) -> None:  # type: ignore[override]
        from dialogs import BoxEditor  # local dialog to build a BoxConfig
        dlg = BoxEditor(self, list(self.samples_by_name.values()), None)
        if dlg.exec_() == dlg.Accepted:
            new_box = dlg.get_box(existing_uid=None)
            if not new_box:
                return
            # let server assign grid-aligned defaults if needed; send our initial too
            if not self._post_or_queue("add_box", {"box": new_box.serialize()}):
                try:
                    from PyQt5.QtWidgets import QMessageBox
                    QMessageBox.information(self, "Offline", "Server is offline. The new box will be created when the connection is restored.")
                except Exception:
                    pass
            self.refresh_all()

    def edit_box(self, box: BoxConfig) -> None:  # type: ignore[override]
        from dialogs import BoxEditor
        dlg = BoxEditor(self, list(self.samples_by_name.values()), box)
        if dlg.exec_() == dlg.Accepted:
            updated = dlg.get_box(existing_uid=box.uid)
            if not updated:
                return
            # Preserve current pos/size/override from UI to avoid snapping
            updated.pos = box.pos
            updated.size = box.size
            updated.manual_override = box.manual_override
            if not self._post_or_queue("edit_box", {"box": updated.serialize()}):
                try:
                    from PyQt5.QtWidgets import QMessageBox
                    QMessageBox.information(self, "Offline", "Server is offline. Your edits are queued and will be applied when reconnected.")
                except Exception:
                    pass
            self.refresh_all()

    def remove_box(self, uid: str) -> None:  # type: ignore[override]
        if not self._post_or_queue("remove_box", {"uid": uid}):
            try:
                from PyQt5.QtWidgets import QMessageBox
                QMessageBox.information(self, "Offline", "Server is offline. The removal is queued and will be applied when reconnected.")
            except Exception:
                pass
        self.refresh_all()

    # ----- maintenance ops routed to server -----
    def delete_maintenance_task(self, task_id: str, user: str, reason: str) -> bool:  # type: ignore[override]
        res = post_action("pm_delete", {"task_id": task_id, "user": user, "reason": reason}) or {}
        ok = bool(res.get("ok", False))
        if ok:
            # refresh view from server state
            self.refresh_all()
            try:
                # Reload local maintenance cache from disk (shared path) to reflect server-side deletion
                self.maintenance.reload()
            except Exception:
                pass
            try:
                self.maintenance_panel.update_items()
            except Exception:
                pass
        return ok

    # ----- connectivity helpers -----
    def _set_online(self, online: bool) -> None:
        prev = getattr(self, "_online", True)
        self._online = bool(online)
        if self._online:
            self._fail_count = 0
            if not prev:
                self._refresh_server_config(force=True)
            try:
                self.statusBar().showMessage("Connected to server", 3000)
            except Exception:
                pass
            try:
                if "Offline" in self.windowTitle():
                    self.setWindowTitle(self.windowTitle().replace(" ??" Offline", ""))
            except Exception:
                pass
        else:
            self._fail_count += 1
            try:
                self.statusBar().showMessage("Server unreachable. Retrying???", 3000)
            except Exception:
                pass
            try:
                if "Offline" not in self.windowTitle():
                    self.setWindowTitle(self.windowTitle() + " ??" Offline")
            except Exception:
                pass

    def _post_or_queue(self, name: str, payload: dict, coalesce_pos: bool = False) -> bool:
        if not getattr(self, "_online", True):
            if coalesce_pos and name == "update_box_pos_size":
                uid = payload.get("uid")
                if uid:
                    self._pending_pos[uid] = payload
            else:
                self._pending_actions.append((name, payload))
            return False
        try:
            res = post_action(name, payload) or {}
            ok = bool(res.get("ok", True))
        except Exception:
            ok = False
        if not ok:
            # Queue for later and mark offline
            self._set_online(False)
            if coalesce_pos and name == "update_box_pos_size":
                uid = payload.get("uid")
                if uid:
                    self._pending_pos[uid] = payload
            else:
                self._pending_actions.append((name, payload))
        return ok

    def _flush_queue(self) -> None:
        if not getattr(self, "_online", True):
            return
        # First coalesced position updates
        if self._pending_pos:
            items = list(self._pending_pos.items())
            self._pending_pos.clear()
            for _, payload in items:
                if not self._post_or_queue("update_box_pos_size", payload):
                    return  # went offline again, stop flushing
        # Then FIFO of other actions
        i = 0
        while i < len(self._pending_actions):
            name, payload = self._pending_actions[i]
            if not self._post_or_queue(name, payload):
                # Went offline again; stop
                return
            i += 1
        # All sent; clear
        if i:
            self._pending_actions = self._pending_actions[i:]

    def _refresh_server_config(self, force: bool = False) -> None:
        if not getattr(self, "_online", True):
            return
        if not force and self._server_config_payload is not None:
            return
        try:
            raw_cfg = get_config() or {}
        except Exception:
            return
        if not raw_cfg:
            return
        self._server_config_payload = raw_cfg
        cfg = AppConfig.from_dict(raw_cfg)
        self.cfg.version = cfg.version
        self.cfg.status_log_dir = cfg.status_log_dir
        self.cfg.samples = cfg.samples
        self.samples_by_name = {s.name: s for s in cfg.samples}
        self.cfg.sample_id_column = cfg.sample_id_column
        self.sample_id_column = self.cfg.sample_id_column or "Lab ID"
        server_boxes = {b.uid: b for b in cfg.boxes}
        for local in self.cfg.boxes:
            srv_box = server_boxes.get(local.uid)
            if srv_box:
                local.csv_path = srv_box.csv_path
                local.timestamp_col = srv_box.timestamp_col
                local.qc_expire_hours = srv_box.qc_expire_hours
                local.watched_targets = srv_box.watched_targets

