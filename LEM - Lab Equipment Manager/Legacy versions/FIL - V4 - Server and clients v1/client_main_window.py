#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
client_main_window.py - Client-side window that syncs with server.py

Wraps the existing MainWindow and overrides data operations to use HTTP.
"""

from __future__ import annotations

import os
from typing import Dict, List

from PyQt5.QtCore import QTimer

from main_window import MainWindow
from models import AppConfig, BoxConfig
from remote_store import get_state, get_config, post_action


class ClientMainWindow(MainWindow):
    def __init__(self) -> None:
        # Build with minimal local config first
        super().__init__()

        # Replace timer with server poller
        try:
            self.timer.stop()
        except Exception:
            pass
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_all)
        self.timer.start(2000)  # 2s client poll; server independently polls CSV

        self._suppress_save = False

        # Bootstrap from server config (samples for dialogs, etc.)
        try:
            raw_cfg = get_config() or {}
            if raw_cfg:
                self.cfg = AppConfig.from_dict(raw_cfg)
                self.samples_by_name = {s.name: s for s in self.cfg.samples}
        except Exception:
            pass

        # Initial fetch
        self.refresh_all()

    # ----- override persistence to server -----
    def save_config(self) -> None:  # type: ignore[override]
        if getattr(self, "_suppress_save", False):
            return
        try:
            # Push box positions/sizes/locks
            for uid, item in list(self.box_items.items()):
                post_action("update_box_pos_size", {
                    "uid": uid,
                    "pos": [float(item.pos().x()), float(item.pos().y())],
                    "size": [float(item.rect().width()), float(item.rect().height())],
                    "locked": bool(item.box.locked),
                })
            # Push selected server-side settings
            post_action("settings", {
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
        try:
            post_action("manual_override", {
                "uid": box.uid,
                "mode": mode,
                "user": user,
                "note": note,
            })
        except Exception:
            pass

    # ----- override refresh to pull from server -----
    def refresh_all(self) -> None:  # type: ignore[override]
        state = get_state() or {}
        if not state:
            return
        try:
            self._suppress_save = True
            # Map lock from server
            srv_locked = bool(state.get("map_locked", False))
            if bool(self.cfg.map_locked) != srv_locked:
                self._toggle_map_lock(srv_locked)

            boxes: List[dict] = state.get("boxes", [])
            seen: Dict[str, bool] = {}
            for b in boxes:
                uid = b.get("uid")
                if not uid:
                    continue
                seen[uid] = True
                item = self.box_items.get(uid)
                if not item:
                    # build a BoxConfig shell to feed UI
                    bc = BoxConfig(
                        uid=uid,
                        title=str(b.get("title", "Machine")),
                        csv_path="",
                        pos=tuple(b.get("pos", [20.0, 20.0])),
                        size=tuple(b.get("size", [240.0, 130.0])),
                        locked=bool(b.get("locked", False)),
                        manual_override=str(b.get("manual_override", "")),
                    )
                    self._add_box_item(bc)
                    item = self.box_items.get(uid)

                # Update geometry/lock/override from server
                if item:
                    pos = b.get("pos", [item.box.pos[0], item.box.pos[1]])
                    size = b.get("size", [item.box.size[0], item.box.size[1]])
                    # Update size first (uses update_size to keep layout)
                    try:
                        w, h = float(size[0]), float(size[1])
                        if (w, h) != tuple(item.box.size):
                            item.update_size(w, h)
                    except Exception:
                        pass
                    try:
                        x, y = float(pos[0]), float(pos[1])
                        if (x, y) != tuple(item.box.pos):
                            item.setPos(x, y)
                            item.box.pos = (x, y)
                    except Exception:
                        pass
                    # Lock state
                    locked = bool(b.get("locked", item.box.locked))
                    if bool(item.box.locked) != locked:
                        item.box.locked = locked
                        item.sync_lock_state()
                    # Manual override state is applied in visuals via set_status

                    # Status + details
                    item.set_status(
                        str(b.get("status", "UNKNOWN")),
                        str(b.get("reason", "")),
                        list(b.get("lines", [])),
                    )

            # Remove any deleted boxes (present locally but not on server)
            to_remove = [uid for uid in self.box_items.keys() if uid not in seen]
            for uid in to_remove:
                it = self.box_items.pop(uid, None)
                if it:
                    try:
                        self.scene.removeItem(it)
                    except Exception:
                        pass
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
            try:
                post_action("add_box", {"box": new_box.serialize()})
            finally:
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
            try:
                post_action("edit_box", {"box": updated.serialize()})
            finally:
                self.refresh_all()

    def remove_box(self, uid: str) -> None:  # type: ignore[override]
        try:
            post_action("remove_box", {"uid": uid})
        finally:
            self.refresh_all()
