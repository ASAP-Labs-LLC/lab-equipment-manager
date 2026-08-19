#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
client_main_window.py - Client-side window that syncs with server.py

Wraps the existing MainWindow and overrides data operations to use HTTP.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import (
    QAction,
    QFileDialog,
    QMessageBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFormLayout,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QWidget,
    QVBoxLayout,
)

from dialogs import ReportPreviewDialog
from main_window import MainWindow
from models import AppConfig, BoxConfig, STATUS_GREEN, STATUS_RED
from remote_store import (
    generate_report,
    get_config,
    create_maintenance_task,
    start_maintenance_task as api_start_maintenance_task,
    complete_maintenance_task as api_complete_maintenance_task,
    delete_maintenance_task as api_delete_maintenance_task,
    add_maintenance_comment as api_add_maintenance_comment,
    get_report_preview,
    get_state,
    post_action,
)
from event_stream import ServerEventStream
from client_settings import ClientSettings, DEFAULT_SERVER_URL, get_client_settings, save_client_settings
from maintenance import MaintenanceTemplate


class ClientMainWindow(MainWindow):
    def __init__(self) -> None:
        self.box_warnings: Dict[str, List[str]] = {}
        self._box_meta: Dict[str, Dict[str, str]] = {}
        self._state_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="srv-sync")
        self._state_future: Optional[Future] = None
        self._refresh_retry_pending = False
        self._server_action_added = False
        self._server_settings_action: Optional[QAction] = None
        self._last_server_state: Dict[str, Any] = {}
        self._last_state_boxes: Dict[str, Dict[str, Any]] = {}
        self._last_maintenance_snapshot: Dict[str, Any] = {}
        # Build with minimal local config first
        super().__init__()
        self._install_server_settings_action()

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
        self._event_refresh_pending = False
        self._event_stream: Optional[ServerEventStream] = None
        self.errors_panel = ErrorsPanel(self)
        try:
            self.addDockWidget(Qt.RightDockWidgetArea, self.errors_panel)
        except Exception:
            pass

        # Bootstrap from server config (samples for dialogs, etc.)
        try:
            raw_cfg = get_config() or {}
            if raw_cfg:
                self.cfg = AppConfig.from_dict(raw_cfg)
                self.samples_by_name = {s.name: s for s in self.cfg.samples}
        except Exception:
            pass

        # Initial fetch
        self.refresh_all(force=True)

        # Realtime event stream (best-effort)
        self._start_event_stream()

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
                "report_enabled": bool(getattr(self.cfg, 'report_enabled', False)),
                "report_time": str(getattr(self.cfg, 'report_time', '17:00') or '17:00'),
                "report_dir": str(getattr(self.cfg, 'report_dir', '') or ''),
            })
        except Exception:
            pass

    def _log_manual_override(self, box: BoxConfig, action: str, user: str, note: str) -> None:  # type: ignore[override]
        mode = "OFF"
        if "DEAD-LINE" in action:
            mode = "DEAD-LINE" if "ON" in action else "OFF"
        if "SERVICE" in action:
            mode = "SERVICE" if "ON" in action else "OFF"
        ok = self._post_or_queue("manual_override", {
            "uid": box.uid,
            "mode": mode,
            "user": user,
            "note": note,
        })
        if ok:
            self.refresh_all(force=True)

    # ----- override refresh to pull from server -----
    def refresh_all(self, force: bool = False) -> None:  # type: ignore[override]
        executor = getattr(self, "_state_executor", None)
        if executor is None:
            return
        if self._state_future and not self._state_future.done():
            if force:
                self._refresh_retry_pending = True
            return
        self._refresh_retry_pending = False
        try:
            future = executor.submit(get_state)
        except Exception:
            return
        self._state_future = future
        future.add_done_callback(self._deliver_state_result)

    def _deliver_state_result(self, future: Future) -> None:
        try:
            state = future.result() or {}
        except Exception:
            state = {}

        def _apply() -> None:
            self._apply_state_snapshot(state)

        QTimer.singleShot(0, _apply)

    def _apply_state_snapshot(self, state: Dict[str, object]) -> None:
        self._state_future = None
        if not state:
            self._set_online(False)
        else:
            self._last_server_state = state
            self._apply_server_state(state)
        if self._refresh_retry_pending:
            self._refresh_retry_pending = False
            self.refresh_all()

    def _apply_server_state(self, state: Dict[str, object]) -> None:
        self._set_online(True)
        try:
            self._suppress_save = True
            # Map lock from server
            srv_locked = bool(state.get("map_locked", False))
            if bool(self.cfg.map_locked) != srv_locked:
                self._toggle_map_lock(srv_locked)

            maintenance_snapshot = state.get("maintenance") or {}
            if maintenance_snapshot:
                self._last_maintenance_snapshot = maintenance_snapshot
                self._apply_maintenance_snapshot(maintenance_snapshot)

            boxes: List[dict] = state.get("boxes", [])
            self._last_state_boxes = {}
            seen: Dict[str, bool] = {}
            cfg_by_uid = {box.uid: box for box in self.cfg.boxes}
            for b in boxes:
                uid = str(b.get("uid") or "")
                if not uid:
                    continue
                self._last_state_boxes[uid] = dict(b)
                seen[uid] = True
                cfg_box = cfg_by_uid.get(uid)
                if cfg_box:
                    cfg_box.title = str(b.get("title", cfg_box.title))
                    cfg_box.pos = tuple(b.get("pos", list(cfg_box.pos)))
                    cfg_box.size = tuple(b.get("size", list(cfg_box.size)))
                    cfg_box.locked = bool(b.get("locked", cfg_box.locked))
                    cfg_box.manual_override = str(b.get("manual_override", cfg_box.manual_override))
                item = self.box_items.get(uid)
                if not item:
                    # build a BoxConfig shell to feed UI
                    bc = BoxConfig(
                        uid=uid,
                        title=str(b.get("title", "Machine")),
                        csv_path=cfg_box.csv_path if cfg_box else "",
                        pos=tuple(b.get("pos", [20.0, 20.0])),
                        size=tuple(b.get("size", [240.0, 130.0])),
                        locked=bool(b.get("locked", False)),
                        manual_override=str(b.get("manual_override", "")),
                    )
                    self.cfg.boxes.append(bc)
                    cfg_by_uid[uid] = bc
                    self._add_box_item(bc)
                    item = self.box_items.get(uid)

                # Update geometry/lock/override from server
                if item:
                    title = str(b.get("title", item.box.title))
                    item.box.title = title
                    self._box_meta[uid] = {"title": title}
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
                    warnings = list(b.get("warnings", []))
                    self.box_warnings[uid] = warnings
                    status = str(b.get("status", "UNKNOWN"))
                    reason = str(b.get("reason", ""))
                    item.set_status(
                        status,
                        reason,
                        list(b.get("lines", [])),
                        warnings,
                    )
                    self._handle_status_transition(uid, item.box, status, reason)

            # Remove any deleted boxes (present locally but not on server)
            to_remove = [uid for uid in self.box_items.keys() if uid not in seen]
            for uid in to_remove:
                it = self.box_items.pop(uid, None)
                if it:
                    try:
                        self.scene.removeItem(it)
                    except Exception:
                        pass
                self.box_warnings.pop(uid, None)
                self._box_meta.pop(uid, None)
            if to_remove:
                self.cfg.boxes = [b for b in self.cfg.boxes if b.uid in seen]
            # Try flushing any queued/coalesced actions now that we're online
            self._update_errors_panel()
            try:
                self._refresh_table()
            except Exception:
                pass
            self._flush_queue()
        finally:
            self._suppress_save = False

    def _handle_status_transition(self, uid: str, box: BoxConfig, new_status: str, reason: str) -> None:
        prev_status = getattr(self, "_last_status_by_uid", {}).get(uid)
        self._last_status_by_uid[uid] = new_status
        if not prev_status or prev_status == new_status:
            return
        if prev_status == STATUS_RED and new_status == STATUS_GREEN:
            desc = "Returned to spec (Red -> Green)"
            when = datetime.now().replace(microsecond=0)
            try:
                if hasattr(self, "status_panel") and self.status_panel:
                    self.status_panel.add_update(uid, box.title, when, desc)
            except Exception:
                pass
            try:
                self.statusBar().showMessage(f"{box.title}: {desc} at {when.strftime('%H:%M:%S')}", 8000)
            except Exception:
                pass

    def _install_server_settings_action(self) -> None:
        if self._server_action_added:
            return
        toolbar: Optional[QToolBar]
        try:
            toolbar = self.findChild(QToolBar, "MainToolbar")
        except Exception:
            toolbar = None
        if not toolbar:
            return
        action = QAction("Server", self)
        action.setToolTip("Configure server URL and token")
        action.triggered.connect(self._open_server_settings_dialog)
        toolbar.addSeparator()
        toolbar.addAction(action)
        self._server_settings_action = action
        self._server_action_added = True

    def _open_server_settings_dialog(self) -> None:
        dlg = ServerSettingsDialog(self, get_client_settings())
        if dlg.exec_() != dlg.Accepted:
            return
        new_settings = dlg.get_settings()
        ok, msg = save_client_settings(new_settings)
        if not ok:
            QMessageBox.warning(self, "Server Settings", f"Unable to save settings: {msg}")
            return
        self._restart_remote_links()

    def _restart_remote_links(self) -> None:
        self._start_event_stream()
        self.refresh_all(force=True)

    def _start_event_stream(self) -> None:
        try:
            if self._event_stream:
                self._event_stream.stop()
        except Exception:
            pass
        try:
            stream = ServerEventStream()
            stream.eventReceived.connect(self._on_server_event)
            stream.statusChanged.connect(self._on_event_stream_status)
            stream.start()
            self._event_stream = stream
        except Exception:
            self._event_stream = None

    def closeEvent(self, event) -> None:  # type: ignore[override]
        try:
            if self._event_stream:
                self._event_stream.stop()
        except Exception:
            pass
        try:
            if self._state_executor:
                self._state_executor.shutdown(wait=False)
        except Exception:
            pass
        self._state_executor = None
        super().closeEvent(event)

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
            self.refresh_all(force=True)

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
            self.refresh_all(force=True)

    def remove_box(self, uid: str) -> None:  # type: ignore[override]
        if not self._post_or_queue("remove_box", {"uid": uid}):
            try:
                from PyQt5.QtWidgets import QMessageBox
                QMessageBox.information(self, "Offline", "Server is offline. The removal is queued and will be applied when reconnected.")
            except Exception:
                pass
        self.refresh_all(force=True)

    # ----- maintenance ops routed to server -----
    def add_maintenance_task(self, box: BoxConfig, name: str, kind: str,
                             start_date: datetime, repeat_value: int, repeat_unit: str) -> bool:  # type: ignore[override]
        payload = {
            "box_uid": box.uid,
            "name": name,
            "kind": kind,
            "start_date": start_date.strftime("%Y-%m-%d"),
            "repeat_value": repeat_value,
            "repeat_unit": repeat_unit,
        }
        res = create_maintenance_task(payload) or {}
        ok = bool(res.get("ok"))
        if ok:
            self.refresh_all(force=True)
            self._refresh_maintenance_cache()
        else:
            QMessageBox.warning(self, "Maintenance", "Unable to create maintenance task (check for duplicates).")
        return ok

    def start_maintenance_task(self, task_id: str) -> None:  # type: ignore[override]
        res = api_start_maintenance_task(task_id) or {}
        if res.get("ok"):
            self.refresh_all(force=True)
            self._refresh_maintenance_cache()
        else:
            QMessageBox.warning(self, "Maintenance", "Unable to start the selected task.")

    def complete_maintenance_task(self, task_id: str, user: str, comment: str) -> None:  # type: ignore[override]
        res = api_complete_maintenance_task(task_id, user, comment) or {}
        if res.get("ok"):
            self.refresh_all(force=True)
            self._refresh_maintenance_cache()
        else:
            QMessageBox.warning(self, "Maintenance", "Unable to complete the task. Ensure it is in progress and comment is provided.")

    def add_maintenance_comment(self, box: BoxConfig, comment: str, user: str) -> None:  # type: ignore[override]
        res = api_add_maintenance_comment(box.uid, box.title, comment, user) or {}
        if res.get("ok"):
            self._refresh_maintenance_cache()
        else:
            QMessageBox.warning(self, "Maintenance", "Unable to add comment.")

    def delete_maintenance_task(self, task_id: str, user: str, reason: str) -> bool:  # type: ignore[override]
        res = api_delete_maintenance_task(task_id, user, reason) or {}
        ok = bool(res.get("ok"))
        if not ok:
            # Fallback to legacy action for compatibility
            res = post_action("pm_delete", {"task_id": task_id, "user": user, "reason": reason}) or {}
            ok = bool(res.get("ok", False))
        if ok:
            self.refresh_all(force=True)
            self._refresh_maintenance_cache()
        else:
            QMessageBox.warning(self, "Maintenance", "Unable to delete the task.")
        return ok

    def _refresh_table(self) -> None:  # type: ignore[override]
        if getattr(self, "_last_state_boxes", {}):
            entries = sorted(
                self._last_state_boxes.items(),
                key=lambda kv: str(kv[1].get("title") or kv[0]).lower(),
            )
            column_count = self.table.columnCount()
            self.table.setRowCount(len(entries))
            for row, (uid, payload) in enumerate(entries):
                title = str(payload.get("title") or uid)
                title_item = QTableWidgetItem(title)
                title_item.setData(Qt.UserRole, uid)
                self.table.setItem(row, 0, title_item)
                status_item = QTableWidgetItem(str(payload.get("status", "UNKNOWN")))
                self.table.setItem(row, 1, status_item)
                override = str(payload.get("manual_override") or '').strip() or '-'
                self.table.setItem(row, 2, QTableWidgetItem(override))
                for col in range(3, column_count):
                    self.table.setItem(row, col, QTableWidgetItem('-'))
                tooltip_parts = []
                reason = str(payload.get("reason", '') or '')
                if reason:
                    tooltip_parts.append(reason)
                tooltip_parts.extend(str(line) for line in (payload.get("lines") or []) if line)
                tooltip_parts.extend(str(msg) for msg in (payload.get("warnings") or []) if msg)
                tooltip = '\n'.join(tp for tp in tooltip_parts if tp)
                if tooltip:
                    for col in range(column_count):
                        cell = self.table.item(row, col)
                        if cell:
                            cell.setToolTip(tooltip)
        else:
            super()._refresh_table()

        warning_marker = '[!]'
        for row in range(self.table.rowCount()):
            title_item = self.table.item(row, 0)
            if not title_item:
                continue
            uid = title_item.data(Qt.UserRole)
            warns = self.box_warnings.get(uid, [])
            base_text = title_item.text()
            marker = f' {warning_marker}'
            if warns and not base_text.endswith(marker):
                title_item.setText(f"{base_text}{marker}")
            elif not warns and base_text.endswith(marker):
                title_item.setText(base_text[:-len(marker)])
            warn_text = '\n'.join(warns)
            combined_tooltip = '\n'.join(
                part for part in [title_item.toolTip(), warn_text] if part
            )
            for col in range(self.table.columnCount()):
                cell = self.table.item(row, col)
                if not cell:
                    continue
                if combined_tooltip:
                    cell.setToolTip(combined_tooltip)
                elif not warns:
                    cell.setToolTip('')

    # ----- connectivity helpers -----
    def _set_online(self, online: bool) -> None:
        prev = getattr(self, "_online", True)
        self._online = bool(online)
        if self._online:
            self._fail_count = 0
            try:
                self.statusBar().showMessage("Connected to server", 3000)
            except Exception:
                pass
            try:
                if "Offline" in self.windowTitle():
                    self.setWindowTitle(self.windowTitle().replace(" — Offline", ""))
            except Exception:
                pass
        else:
            self._fail_count += 1
            try:
                self.statusBar().showMessage("Server unreachable. Retrying…", 3000)
            except Exception:
                pass
            try:
                if "Offline" not in self.windowTitle():
                    self.setWindowTitle(self.windowTitle() + " — Offline")
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

    def _apply_maintenance_snapshot(self, snapshot: Dict[str, Any]) -> None:
        if not snapshot:
            return
        try:
            templates: Dict[str, MaintenanceTemplate] = {}
            for raw in snapshot.get("tasks", []):
                tpl = MaintenanceTemplate(
                    id=str(raw.get("id", "")),
                    box_uid=str(raw.get("box_uid", "")),
                    box_title=str(raw.get("box_title", "")),
                    name=str(raw.get("name", "")),
                    kind=str(raw.get("kind", "")),
                    start_date=str(raw.get("start_date", "")),
                    repeat_value=int(raw.get("repeat_value") or 0),
                    repeat_unit=str(raw.get("repeat_unit", "")),
                    next_due=str(raw.get("next_due", "")),
                    status=str(raw.get("status", "UPCOMING")),
                    notes=str(raw.get("notes", "")),
                )
                templates[tpl.id] = tpl
            self.maintenance.templates = templates
            self.maintenance.refresh_statuses(save=False)
            if hasattr(self, "maintenance_panel") and self.maintenance_panel:
                self.maintenance_panel.update_items()
        except Exception:
            pass

    def _refresh_maintenance_cache(self) -> None:
        snapshot = (
            self._last_maintenance_snapshot
            or (self._last_server_state.get("maintenance") if self._last_server_state else None)
            or {}
        )
        if snapshot:
            self._apply_maintenance_snapshot(snapshot)
            return
        try:
            self.maintenance.reload()
        except Exception:
            pass
        try:
            self.maintenance_panel.update_items()
        except Exception:
            pass

    def _update_errors_panel(self) -> None:
        if not hasattr(self, "errors_panel") or self.errors_panel is None:
            return
        entries: List[Tuple[str, str, str]] = []
        for uid, warns in self.box_warnings.items():
            if not warns:
                continue
            meta = self._box_meta.get(uid, {})
            title = meta.get("title") or uid
            for msg in warns:
                entries.append((uid, title, msg))
        self.errors_panel.set_errors(entries)

    # ----- realtime events -----
    def _on_server_event(self, event: dict) -> None:
        topic = str(event.get("topic", ""))
        if not topic:
            return
        if topic.startswith("box.") or topic.startswith("maintenance.") or topic.startswith("report."):
            self._schedule_event_refresh()

    def _schedule_event_refresh(self) -> None:
        if self._event_refresh_pending:
            return
        self._event_refresh_pending = True
        QTimer.singleShot(250, self._refresh_from_event)

    def _refresh_from_event(self) -> None:
        self._event_refresh_pending = False
        self.refresh_all(force=True)

    def _on_event_stream_status(self, status: str) -> None:
        msg = ""
        if status == "connected":
            msg = "Realtime link connected"
        elif status == "disconnected":
            msg = "Realtime link disconnected"
        elif status.startswith("error"):
            msg = f"Realtime error: {status.split(':', 1)[-1].strip()}"
        if msg:
            try:
                self.statusBar().showMessage(msg, 4000)
            except Exception:
                pass

    # ----- reporting via server -----
    def preview_report(self) -> None:  # type: ignore[override]
        data = get_report_preview() or {}
        headers = data.get("headers") or []
        rows = data.get("rows") or []
        if not headers:
            QMessageBox.warning(self, "Preview failed", "Server did not return any report data.")
            return
        dlg = ReportPreviewDialog(self, headers, rows)
        dlg.exec_()

    def export_report_now(self) -> None:  # type: ignore[override]
        if not (self.cfg.report_dir or "").strip():
            directory = QFileDialog.getExistingDirectory(self, "Select Destination Folder")
            if not directory:
                return
            self.cfg.report_dir = directory
            self.save_config()
        res = generate_report({"formats": ["csv", "html"], "force": True}) or {}
        if not res.get("ok"):
            QMessageBox.warning(self, "Export failed", "Server could not generate the report. Check report settings and try again.")
            return
        outputs = res.get("outputs") or {}
        details = "\n".join(f"{k.upper()}: {v}" for k, v in outputs.items())
        if details:
            QMessageBox.information(self, "Report generated", details)
        else:
            QMessageBox.information(self, "Report generated", "Daily report created successfully.")
        try:
            self.statusBar().showMessage("Daily report generated on server", 6000)
        except Exception:
            pass


class ErrorsPanel(QDockWidget):
    def __init__(self, owner: ClientMainWindow) -> None:
        super().__init__("Errors")
        self.owner = owner
        container = QWidget()
        layout = QVBoxLayout(container)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Machine", "Warning"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)
        self.setWidget(container)

    def set_errors(self, entries: List[Tuple[str, str, str]]) -> None:
        self.table.setRowCount(len(entries))
        for row, (uid, title, msg) in enumerate(entries):
            item = QTableWidgetItem(title)
            item.setData(Qt.UserRole, uid)
            self.table.setItem(row, 0, item)
            self.table.setItem(row, 1, QTableWidgetItem(msg))


class ServerSettingsDialog(QDialog):
    def __init__(self, parent: QWidget, settings: ClientSettings) -> None:
        super().__init__(parent)
        self.setWindowTitle("Server Connection")
        layout = QFormLayout(self)
        self.url_edit = QLineEdit(settings.server_url or DEFAULT_SERVER_URL)
        self.url_edit.setPlaceholderText(DEFAULT_SERVER_URL)
        self.token_edit = QLineEdit(settings.auth_token)
        self.token_edit.setEchoMode(QLineEdit.Password)
        layout.addRow("Server URL", self.url_edit)
        layout.addRow("API Token", self.token_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_settings(self) -> ClientSettings:
        return ClientSettings(
            server_url=self.url_edit.text().strip() or DEFAULT_SERVER_URL,
            auth_token=self.token_edit.text().strip(),
        )
