#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main_window.py â€” Main window, scene (grid), camera restore, list view, zoom/pan,
reporting (preview/export + daily scheduled + startup catch-up), and orchestration.
"""

from __future__ import annotations

import os
import csv
from typing import Dict, List, Tuple, Optional

from datetime import datetime, timedelta

from PyQt5.QtCore import QTimer, QThread, Qt, QRectF, QLineF, QPoint
from PyQt5.QtGui import QPainter, QPen, QColor
from PyQt5.QtWidgets import (
    QMainWindow, QGraphicsView, QGraphicsScene, QTableWidget, QTableWidgetItem,
    QHeaderView, QStackedWidget, QToolBar, QAction, QLabel, QSpinBox, QComboBox,
    QDialog, QMenu, QFileDialog, QMessageBox, QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QCheckBox
)
from models import (
    AppConfig, BoxConfig, SampleSpec,
    STATUS_DEAD, STATUS_SERVICE, STATUS_GREEN, STATUS_RED, STATUS_YELLOW
)
from config_store import load_config, save_config
from data_source import CsvReadWorker, evaluate_box, BoxEvaluation
from box_item import (
    MachineBoxItem,
    COLOR_GREEN, COLOR_RED, COLOR_YELLOW,
    COLOR_SERVICE_BG, COLOR_SERVICE_TEXT,
    COLOR_BLACK, COLOR_BLACK_TEXT,
    COLOR_TEXT_WHITE
)
from dialogs import SettingsDialog, BoxEditor, InfoDialog, ReportPreviewDialog, MachineInfoDialog


# ------------- helpers -----------------

def human_tdelta(td: timedelta) -> str:
    """Compact humanized delta like '2d 4h', '5h 12m', '7m'."""
    neg = td.total_seconds() < 0
    s = int(abs(td.total_seconds()))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    out = f"{d}d {h}h" if d else (f"{h}h {m}m" if h else f"{m}m")
    return f"-{out}" if neg else out


# ------------- view & scene -----------------

class ZoomableGraphicsView(QGraphicsView):
    """
    GraphicsView with Ctrl+wheel zoom, Shift+wheel horizontal pan, and 'fit to screen'.
    Persists a logical _zoom factor for camera restore.
    """
    def __init__(self, scene: QGraphicsScene) -> None:
        super().__init__(scene)
        self._zoom = 1.0
        self._min_zoom = 0.2
        self._max_zoom = 5.0
        self.setRenderHints(self.renderHints() | QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.RubberBandDrag)

    def wheelEvent(self, event):
        mods = event.modifiers()
        delta = event.angleDelta().y()
        if mods & Qt.ControlModifier:
            if delta > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
            return
        if mods & Qt.ShiftModifier:
            # Horizontal pan with wheel
            sb = self.horizontalScrollBar()
            sb.setValue(sb.value() - delta)
            event.accept()
            return
        super().wheelEvent(event)

    def _apply_zoom(self, factor: float) -> None:
        new_zoom = self._zoom * factor
        if new_zoom < self._min_zoom:
            factor = self._min_zoom / self._zoom
            new_zoom = self._min_zoom
        elif new_zoom > self._max_zoom:
            factor = self._max_zoom / self._zoom
            new_zoom = self._max_zoom
        self.scale(factor, factor)
        self._zoom = new_zoom

    def zoom_in(self) -> None:
        self._apply_zoom(1.15)

    def zoom_out(self) -> None:
        self._apply_zoom(1 / 1.15)

    def fit_to_scene(self) -> None:
        rect = self.scene().itemsBoundingRect()
        if rect.isNull():
            rect = self.sceneRect()
        margin = 20.0
        rect = rect.adjusted(-margin, -margin, margin, margin)
        if rect.isValid():
            self.fitInView(rect, Qt.KeepAspectRatio)
            self._zoom = 1.0

    def set_zoom_and_center(self, zoom: float, cx: float, cy: float) -> None:
        """Reset transform, apply zoom, and center on a scene point."""
        self.resetTransform()
        self._zoom = 1.0
        factor = max(0.01, float(zoom))
        self.scale(factor, factor)
        self._zoom = factor
        self.centerOn(cx, cy)


class MachineScene(QGraphicsScene):
    """Scene with visible snap grid."""
    GRID_SIZE = 20.0

    def __init__(self, parent_window) -> None:
        super().__init__()
        self.parent_window = parent_window
        self.map_locked = False
        self.setSceneRect(QRectF(0, 0, 5000, 3000))
        self.setBackgroundBrush(QColor(245, 245, 245))

    def drawBackground(self, painter: QPainter, rect) -> None:
        from math import floor
        super().drawBackground(painter, rect)

        g = float(self.GRID_SIZE)
        if g <= 0:
            return

        left   = floor(rect.left() / g) * g
        right  = rect.right()
        top    = floor(rect.top() / g) * g
        bottom = rect.bottom()

        pen_minor = QPen(QColor(220, 220, 220))
        pen_major = QPen(QColor(200, 200, 200))

        painter.setRenderHint(QPainter.Antialiasing, False)

        # Minor grid
        painter.setPen(pen_minor)
        x = left
        while x <= right:
            painter.drawLine(QLineF(x, top, x, bottom))
            x += g
        y = top
        while y <= bottom:
            painter.drawLine(QLineF(left, y, right, y))
            y += g

        # Major grid
        painter.setPen(pen_major)
        step = g * 5.0
        x = left
        while x <= right:
            painter.drawLine(QLineF(x, top, x, bottom))
            x += step
        y = top
        while y <= bottom:
            painter.drawLine(QLineF(left, y, right, y))
            y += step


# ------------- Main window -----------------

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Lab Manager Map")
        self.resize(1200, 750)

        # State & config
        self.cfg: AppConfig = load_config()
        self.samples_by_name: Dict[str, SampleSpec] = {s.name: s for s in getattr(self.cfg, 'samples', [])}
        self.box_items: Dict[str, MachineBoxItem] = {}
        self._last_rows_cache: Dict[str, List[dict]] = {}
        self._first_run = True  # for startup catch-up export
        self._last_status_map: Dict[str, str] = {}
        self._refresh_active = False
        self._thread = None
        self._worker = None

        # Scene / views
        self.scene = MachineScene(self)
        self.scene.map_locked = self.cfg.map_locked
        self.view = ZoomableGraphicsView(self.scene)
        try:
            self.scene.selectionChanged.connect(self._on_scene_selection_changed)
        except Exception:
            pass

        # List view
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Title", "Status", "Override", "Last QC", "Expires In", "CSV", "Watched"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)

        # Stack (map/list)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.table)
        self.setCentralWidget(self.stack)

        # Maintenance dock (upcoming scheduled PM)
        self._setup_maintenance_dock()

        # Toolbar
        self._setup_toolbar()

        # Add configured boxes to scene
        for b in self.cfg.boxes:
            self._add_box_item(b)

        # Restore camera (zoom + center) after boxes exist
        self._apply_view_state()

        # Polling
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_all)
        self._apply_poll_minutes(self.cfg.poll_minutes)

        # Initial refresh
        QTimer.singleShot(200, self.refresh_all)

        # Flash timer for overdue rows in maintenance panel
        self._flash_on = False
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(800)
        self._flash_timer.timeout.connect(self._tick_flash)
        self._flash_timer.start()

        # Update maintenance panel initially
        QTimer.singleShot(400, self._refresh_maintenance_sidebar)

    # ----- toolbar / actions -----
    def _setup_toolbar(self) -> None:
        tb = QToolBar("Main")
        self.addToolBar(tb)

        add_act = QAction("Add Box", self); add_act.triggered.connect(self.add_box)
        refresh_act = QAction("Refresh Now", self); refresh_act.triggered.connect(self.refresh_all)
        settings_act = QAction("Settings", self); settings_act.triggered.connect(self.open_settings)

        self.lock_map_act = QAction("Lock Map", self, checkable=True)
        self.lock_map_act.setChecked(self.cfg.map_locked)
        self.lock_map_act.toggled.connect(self._toggle_map_lock)

        tb.addAction(add_act)
        tb.addAction(refresh_act)
        tb.addSeparator()
        tb.addAction(settings_act)
        tb.addSeparator()
        tb.addAction(self.lock_map_act)

        tb.addSeparator()
        tb.addWidget(QLabel(" View: "))
        self.view_mode_cb = QComboBox(); self.view_mode_cb.addItems(["Map", "List"])
        self.view_mode_cb.currentIndexChanged.connect(self._switch_view)
        tb.addWidget(self.view_mode_cb)

        tb.addSeparator()
        tb.addWidget(QLabel(" Poll (min): "))
        self.poll_spin = QSpinBox(); self.poll_spin.setRange(1, 120)
        self.poll_spin.setValue(self.cfg.poll_minutes)
        self.poll_spin.valueChanged.connect(self._change_poll_minutes)
        tb.addWidget(self.poll_spin)

        tb.addSeparator()
        # Zoom controls
        zoom_in_act = QAction("Zoom In", self);  zoom_in_act.triggered.connect(self.view.zoom_in)
        zoom_out_act = QAction("Zoom Out", self); zoom_out_act.triggered.connect(self.view.zoom_out)
        fit_act = QAction("Fit to Screen", self); fit_act.triggered.connect(self.view.fit_to_scene)
        tb.addAction(zoom_in_act); tb.addAction(zoom_out_act); tb.addAction(fit_act)

        tb.addSeparator()
        # Reports
        preview_act = QAction("Preview Report", self); preview_act.triggered.connect(self.preview_report)
        export_now_act = QAction("Export Report Now", self); export_now_act.triggered.connect(self.export_report_now)
        tb.addAction(preview_act); tb.addAction(export_now_act)

        tb.addSeparator()
        # Save layout persists camera & boxes
        save_act = QAction("Save Layout", self); save_act.triggered.connect(self.save_config)
        tb.addAction(save_act)
        # Toggle maintenance sidebar
        maint_act = QAction("Maintenance Panel", self); maint_act.setCheckable(True)
        maint_act.setChecked(True)
        maint_act.toggled.connect(lambda v: self.maint_dock.setVisible(v))
        tb.addSeparator(); tb.addAction(maint_act)

    def _switch_view(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        if idx == 1:
            self._refresh_table()

    def _toggle_map_lock(self, locked: bool) -> None:
        self.cfg.map_locked = locked
        self.scene.map_locked = locked
        for item in self.box_items.values():
            item.set_movable(not (locked or item.box.locked))
            item.sync_lock_state()  # hide/show resize handle
        self.save_config()

    def _change_poll_minutes(self, v: int) -> None:
        self.cfg.poll_minutes = int(v)
        self._apply_poll_minutes(self.cfg.poll_minutes)
        self.save_config()

    def _apply_poll_minutes(self, minutes: int) -> None:
        self.timer.stop()
        self.timer.start(max(1, minutes) * 60 * 1000)

    # ----- view state capture/restore -----
    def _capture_view_state(self) -> None:
        """Store current zoom and viewport center (scene coords) into cfg."""
        try:
            center_scene = self.view.mapToScene(self.view.viewport().rect().center())
            self.cfg.view_zoom = float(getattr(self.view, "_zoom", 1.0))
            self.cfg.view_center = (float(center_scene.x()), float(center_scene.y()))
        except Exception:
            pass  # never block save on view capture

    def _apply_view_state(self) -> None:
        """Restore zoom and center if we have something meaningful saved."""
        try:
            z = float(self.cfg.view_zoom or 1.0)
            cx, cy = self.cfg.view_center if self.cfg.view_center else (0.0, 0.0)
            if z > 0 and (cx != 0.0 or cy != 0.0):
                self.view.set_zoom_and_center(z, cx, cy)
        except Exception:
            pass

    # ----- config I/O -----
    def save_config(self) -> None:
        # Capture camera before saving so Save Layout remembers view
        self._capture_view_state()
        ok, msg = save_config(self.cfg)
        if not ok:
            self.statusBar().showMessage(f"Save failed: {msg}", 8000)


    def closeEvent(self, event) -> None:
        try:
            # Stop timers
            try:
                self.timer.stop()
            except Exception:
                pass
            try:
                self._flash_timer.stop()
            except Exception:
                pass
            # Gracefully stop any running refresh thread
            th = getattr(self, "_thread", None)
            if th and isinstance(th, QThread):
                try:
                    if th.isRunning():
                        th.quit()
                        th.wait(3000)
                except Exception:
                    pass
        finally:
            super().closeEvent(event)

    # ----- box ops -----
    def _add_box_item(self, box: BoxConfig) -> None:
        item = MachineBoxItem(box)
        self.scene.addItem(item)
        self.box_items[box.uid] = item
        item.set_movable(not (self.cfg.map_locked or box.locked))
        item.sync_lock_state()

    def add_box(self) -> None:
        dlg = BoxEditor(self, list(self.samples_by_name.values()), None)
        if dlg.exec_() == QDialog.Accepted:
            new_box = dlg.get_box(existing_uid=None)
            if not new_box:
                return

            # Ensure strong uniqueness of UID
            if new_box.uid in self.box_items or any(b.uid == new_box.uid for b in self.cfg.boxes):
                from uuid import uuid4
                new_box.uid = f"box_{uuid4().hex}"

            # Default size 1Ã—1 grid
            g = float(self.scene.GRID_SIZE)
            new_box.size = (g, g)

            # Gentle stagger placement
            offset = 30 * (len(self.box_items) % 10)
            new_box.pos = (round((20.0 + offset) / g) * g, round((20.0 + offset) / g) * g)

            self.cfg.boxes.append(new_box)
            self._add_box_item(new_box)
            self.save_config()
            self.refresh_all()

    def edit_box(self, box: BoxConfig) -> None:
        dlg = BoxEditor(self, list(self.samples_by_name.values()), box)
        if dlg.exec_() == QDialog.Accepted:
            updated = dlg.get_box(existing_uid=box.uid)
            if not updated:
                return

            # Keep identity & geometry and manual override
            updated.uid = box.uid
            updated.pos = box.pos
            updated.size = box.size
            updated.manual_override = box.manual_override

            # Replace only the matching box in cfg
            for i, b in enumerate(self.cfg.boxes):
                if b.uid == box.uid:
                    self.cfg.boxes[i] = updated
                    break

            # Update existing scene item (no new item; prevents "overwrites")
            item = self.box_items.get(box.uid)
            if item:
                item.box = updated
                item.update_size(updated.size[0], updated.size[1])
                item.set_movable(not (self.cfg.map_locked or updated.locked))
                item.sync_lock_state()
                item.apply_visuals()

            self.save_config()
            self.refresh_all()

    def remove_box(self, uid: str) -> None:
        item = self.box_items.get(uid)
        if item:
            self.scene.removeItem(item)
            del self.box_items[uid]
        self.cfg.boxes = [b for b in self.cfg.boxes if b.uid != uid]
        self.save_config()
        self._refresh_table()

    def open_box_info(self, box: BoxConfig, status: str, reason: str, lines: List[str]) -> None:
        rows = self._last_rows_cache.get(box.csv_path, [])
        eval_res = evaluate_box(box, self.samples_by_name, self.cfg.sample_id_col, rows)
        if box.manual_override == STATUS_DEAD:
            eval_res.status = STATUS_DEAD
            eval_res.reason = "Manual override: DEAD-LINE"
        elif box.manual_override == STATUS_SERVICE:
            eval_res.status = STATUS_SERVICE
            eval_res.reason = "Manual override: SERVICE"
        dlg = MachineInfoDialog(self, box, eval_res)
    def _log_path_for(self, box: BoxConfig) -> str:
        base_dir = os.path.dirname(box.csv_path) if box.csv_path else os.getcwd()
        return os.path.join(base_dir, f"{self._sanitize_title(box.title)}_maintenance_log.csv")

    def _append_maint_entry(self, box: BoxConfig, category: str, person: str, next_due: str, comment: str) -> None:
        try:
            path = self._log_path_for(box)
            # Ensure header
            import csv as _csv, os as _os
            if not _os.path.exists(path) or _os.path.getsize(path) == 0:
                _os.makedirs(_os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "w", newline="", encoding="utf-8") as f:
                    _csv.writer(f).writerow(["timestamp","box_uid","box_title","category","person","next_due","comment"])
            from datetime import datetime as _dt
            ts = _dt.now().replace(microsecond=0).isoformat(sep=" ")
            with open(path, "a", newline="", encoding="utf-8") as f:
                _csv.writer(f).writerow([ts, box.uid, box.title, category, person, next_due, comment])
        except Exception:
            pass
    # ----- list mode right-click -----
    def _on_table_context_menu(self, pos: QPoint) -> None:

        idx = self.table.indexAt(pos)

        if not idx.isValid():

            return

        row = idx.row()

        title_item = self.table.item(row, 0)

        if title_item is None:

            return



        uid = title_item.data(Qt.UserRole)

        box = next((b for b in self.cfg.boxes if b.uid == uid), None)

        if not box:

            title = title_item.text()

            box = next((b for b in self.cfg.boxes if b.title == title), None)

        if not box:

            return



        menu = QMenu(self)

        info_act = menu.addAction("Machine Info...")

        edit_act = menu.addAction("Edit Box...")

        lock_act = menu.addAction("Lock" if not box.locked else "Unlock")

        menu.addSeparator()

        ov_menu = menu.addMenu("Manual Override")

        off_act = ov_menu.addAction("Off")

        dead_act = ov_menu.addAction("DEAD-LINE")

        serv_act = ov_menu.addAction("SERVICE")

        menu.addSeparator()

        rem_act = menu.addAction("Remove")



        chosen = menu.exec_(self.table.viewport().mapToGlobal(pos))

        if not chosen:

            return



        if chosen == info_act:

            self.open_box_info(box, "", "", [])

        elif chosen == edit_act:

            self.edit_box(box)

        elif chosen == lock_act:

            box.locked = not box.locked

            self.box_items[box.uid].sync_lock_state()

            self.save_config(); self._refresh_table()

        elif chosen == rem_act:

            self.remove_box(box.uid)

        elif chosen in (off_act, dead_act, serv_act):

            prev = box.manual_override or ""

            if chosen == off_act:

                box.manual_override = ""

                if prev in (STATUS_DEAD, STATUS_SERVICE):

                    txt, ok = QInputDialog.getMultiLineText(self, "Override Off", f"{box.title}: Describe remedy done to return to service:")

                    if ok and (txt or "").strip():

                        self._append_maint_entry(box, category="Override Off", person="", next_due="", comment=(txt or "").strip())

            elif chosen == dead_act:

                box.manual_override = STATUS_DEAD

                txt, ok = QInputDialog.getMultiLineText(self, "Set DEAD-LINE", f"{box.title}: Why is this being deadlined?")

                if ok and (txt or "").strip():

                    self._append_maint_entry(box, category="Override: DEAD-LINE", person="", next_due="", comment=(txt or "").strip())

            else:

                box.manual_override = STATUS_SERVICE

                txt, ok = QInputDialog.getMultiLineText(self, "Set SERVICE", f"{box.title}: Why is this in service mode?")

                if ok and (txt or "").strip():

                    self._append_maint_entry(box, category="Override: SERVICE", person="", next_due="", comment=(txt or "").strip())

            self.box_items[box.uid].apply_visuals()

            self.save_config(); self._refresh_table(); self._refresh_maintenance_sidebar()


    # ----- settings -----
    def open_settings(self) -> None:
        dlg = SettingsDialog(
            self,
            report_enabled=self.cfg.report_enabled,
            report_time=self.cfg.report_time,
            report_dir=self.cfg.report_dir,
            samples=list(self.samples_by_name.values()),
            sample_id_col=self.cfg.sample_id_col,
        )
        if dlg.exec_() == QDialog.Accepted:
            samples, sample_id_col = dlg.get_samples_and_column()
            self.cfg.samples = samples
            self.cfg.sample_id_col = sample_id_col
            self.samples_by_name = {s.name: s for s in self.cfg.samples}
            enabled, time_str, directory = dlg.get_report_settings()
            self.cfg.report_enabled = enabled
            self.cfg.report_time = time_str
            self.cfg.report_dir = directory
            self.save_config()
            self.refresh_all()

    # ----- first-in-spec fallback clock (when parsed_date/time not present) -----
    def _ensure_first_inspec_epoch(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if self.cfg.first_inspec_date != today:
            self.cfg.first_inspec_date = today
            self.cfg.first_inspec_map = {}
            self.save_config()

    def _get_first_inspec(self, uid: str) -> Optional[datetime]:
        iso = self.cfg.first_inspec_map.get(uid)
        if not iso:
            return None
        try:
            return datetime.fromisoformat(iso)  # local naive
        except Exception:
            return None

    def _set_first_inspec_if_missing(self, uid: str, when: datetime) -> None:
        if uid not in self.cfg.first_inspec_map:
            self.cfg.first_inspec_map[uid] = when.replace(microsecond=0).isoformat(sep=' ')
            self.save_config()

    # ----- refresh & reporting -----
    def refresh_all(self) -> None:
        if self._refresh_active:
            # Avoid overlapping refresh threads
            return
        paths = sorted({b.csv_path for b in self.cfg.boxes if b.csv_path})
        if not paths:
            # Allow startup catch-up even if no paths (e.g., nothing configured yet)
            self._maybe_run_daily_report({}, force_if_missed=self._first_run)
            self._first_run = False
            return

        self._thread = QThread(self)
        self._worker = CsvReadWorker(paths)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_rows_loaded)
        self._worker.error.connect(self._on_rows_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._on_refresh_thread_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_refresh_thread_finished(self) -> None:
        self._refresh_active = False
        self._thread = None
        self._worker = None

    def _on_rows_error(self, path: str, msg: str) -> None:
        self.statusBar().showMessage(f"CSV error for {path}: {msg}", 8000)

    def _on_rows_loaded(self, rows_by_path: Dict[str, List[dict]]) -> None:
        self._last_rows_cache = rows_by_path

        # Startup catch-up: export today's report immediately if missed (before updating today's statuses)
        if self._first_run:
            self._maybe_run_daily_report(rows_by_path, force_if_missed=True)
            self._first_run = False

        self._ensure_first_inspec_epoch()

        for box in self.cfg.boxes:
            rows = rows_by_path.get(box.csv_path, [])
            eval_res: BoxEvaluation = evaluate_box(box, self.samples_by_name, self.cfg.sample_id_col, rows)

            # Fallback in-spec clock: if no parsed_date/time used, remember first time it went GREEN today
            effective_last_good = eval_res.last_good_qc
            if not getattr(eval_res, "used_parsed", False):
                if eval_res.status in (STATUS_GREEN, STATUS_YELLOW):
                    now_local = datetime.now()
                    self._set_first_inspec_if_missing(box.uid, now_local)
                    effective_last_good = self._get_first_inspec(box.uid) or eval_res.last_good_qc
                else:
                    effective_last_good = self._get_first_inspec(box.uid) or eval_res.last_good_qc

            # Apply manual overrides to *status* only
            if box.manual_override == STATUS_DEAD:
                status = STATUS_DEAD
                reason = "Manual override: DEAD-LINE"
            elif box.manual_override == STATUS_SERVICE:
                status = STATUS_SERVICE
                reason = "Manual override: SERVICE"
            else:
                status = eval_res.status
                reason = eval_res.reason

            # If GREEN and effective clock says stale, flip to YELLOW
            if status == STATUS_GREEN and effective_last_good:
                if (datetime.now() - effective_last_good) > timedelta(hours=box.qc_expire_hours):
                    status = STATUS_YELLOW
                    reason = "Last in-spec QC is stale (fallback clock)."

            # Build info lines
            lines: List[str] = []
            if eval_res.results:
                for pr in eval_res.results[:4]:
                    if pr.test_name:
                        tol = ((pr.high - pr.low) / 2.0) if (pr.low is not None and pr.high is not None) else None
                        rng = f"[{pr.low:.6g}, {pr.high:.6g}]" if pr.low is not None else "â€”"
                        vtxt = "â€”" if pr.latest_value is None else f"{pr.latest_value:.6g}"
                        flag = "" if pr.in_spec is None else ("âœ“" if pr.in_spec else "âœ—")
                        units = f" {pr.units}" if pr.units else ""
                        tol_txt = (f"{tol:.6g}" if tol is not None else "â€”")
                        sfx = f" ({pr.sample_name})" if getattr(pr, 'sample_name', None) else ""
                        lines.append(f"{pr.test_name}{sfx}: {vtxt}{units} {flag}  tolÂ±{tol_txt}  {rng}")
                    else:
                        lines.append("(missing test)")
                if len(eval_res.results) > 4:
                    lines.append(f"+{len(eval_res.results)-4} moreâ€¦")
            else:
                lines.append("(no tests)")

            # Add age line using effective clock
            if effective_last_good:
                age = datetime.now() - effective_last_good
                lines.append(f"In spec: {human_tdelta(age)} ago")
            elif eval_res.latest_match_time:
                age = datetime.utcnow() - eval_res.latest_match_time
                lines.append(f"Last row: {human_tdelta(age)} ago")

            # Detect status transitions for Status Changes dock
            try:
                prev = self._last_status_map.get(box.uid)
                if prev == STATUS_RED and status == STATUS_GREEN:
                    self._add_status_change_notice(box)
            except Exception:
                pass
            self._last_status_map[box.uid] = status

            item = self.box_items.get(box.uid)
            if item:
                # If using per-test affects list and it's empty, preserve previous visual status
                affects_tests = getattr(box, 'affects_tests', None)
                if isinstance(affects_tests, list):
                    vis_status = status if len(affects_tests) > 0 else getattr(item, "_status", status)
                else:
                    vis_status = status if getattr(box, "affects_status", True) else getattr(item, "_status", status)
                item.set_status(vis_status, reason, lines)

        if self.stack.currentIndex() == 1:
            self._refresh_table()

        # Regular scheduled export (after updating UI)
        self._maybe_run_daily_report(rows_by_path)

    # ----- table (list mode) -----
    def _refresh_table(self) -> None:
        self.table.setRowCount(0)
        for box in self.cfg.boxes:
            item = self.box_items.get(box.uid)
            if not item:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            rows = self._last_rows_cache.get(box.csv_path, [])
            eval_res = evaluate_box(box, self.samples_by_name, self.cfg.sample_id_col, rows)
            status = eval_res.status
            if box.manual_override == STATUS_DEAD:
                status = STATUS_DEAD
            elif box.manual_override == STATUS_SERVICE:
                status = STATUS_SERVICE

            title = QTableWidgetItem(box.title)
            title.setData(Qt.UserRole, box.uid)  # keep uid for context menu
            self.table.setItem(row, 0, title)

            self.table.setItem(row, 1, QTableWidgetItem(status))
            self.table.setItem(row, 2, QTableWidgetItem(box.manual_override or "â€”"))
            self.table.setItem(row, 3, QTableWidgetItem(
                eval_res.last_good_qc.isoformat(sep=' ') if eval_res.last_good_qc else "â€”"
            ))

            if eval_res.last_good_qc and eval_res.status in (STATUS_GREEN, STATUS_YELLOW):
                ttl = (eval_res.last_good_qc + timedelta(hours=box.qc_expire_hours)) - datetime.utcnow()
                ttl_txt = f"{int(ttl.total_seconds()//3600)}h {int((ttl.total_seconds()%3600)//60)}m"
            else:
                ttl_txt = "â€”"
            self.table.setItem(row, 4, QTableWidgetItem(ttl_txt))

            self.table.setItem(row, 5, QTableWidgetItem(box.csv_path))
            self.table.setItem(row, 6, QTableWidgetItem(", ".join(box.watched_tests) if box.watched_tests else "â€”"))

            self._apply_row_color(row, status)

        self.table.resizeRowsToContents()

    def _apply_row_color(self, row: int, status: str) -> None:
        if status == STATUS_GREEN:
            bg = COLOR_GREEN; fg = COLOR_TEXT_WHITE
        elif status == STATUS_RED:
            bg = COLOR_RED; fg = COLOR_TEXT_WHITE
        elif status == STATUS_YELLOW:
            bg = COLOR_YELLOW; fg = QColor(30, 30, 30)
        elif status == STATUS_DEAD:
            bg = COLOR_BLACK; fg = QColor(220, 20, 60)
        elif status == STATUS_SERVICE:
            bg = COLOR_SERVICE_BG; fg = COLOR_SERVICE_TEXT
        else:
            bg = QColor(200, 200, 200); fg = QColor(30, 30, 30)
        for col in range(self.table.columnCount()):
            item = self.table.item(row, col)
            if item is None:
                item = QTableWidgetItem(" "); self.table.setItem(row, col, item)
            item.setBackground(bg)
            item.setForeground(fg)

    # ----- report building / preview / export -----
    def _build_report(self, rows_by_path: Dict[str, List[dict]]) -> Tuple[List[str], List[List[str]]]:
        headers = [
            "Box Title", "Box UID", "Box Status", "Override",
            "CSV Path", "QC Expiry (h)", "Last In-Spec QC / Fallback", "Latest Match Time",
            "Reason", "Used Parsed Time",
            "Test Name", "Expected", "k*Ïƒ", "Low", "High", "Latest Value", "In Spec", "Units"
        ]
        out_rows: List[List[str]] = []
        for box in self.cfg.boxes:
            rows = rows_by_path.get(box.csv_path, [])
            ev = evaluate_box(box, self.samples_by_name, self.cfg.sample_id_col, rows)
            status = ev.status
            if box.manual_override == STATUS_DEAD:
                status = STATUS_DEAD
            elif box.manual_override == STATUS_SERVICE:
                status = STATUS_SERVICE

            # Effective/fallback last_good timestamp to include in report
            self._ensure_first_inspec_epoch()
            effective = ev.last_good_qc
            if not getattr(ev, "used_parsed", False):
                first = self._get_first_inspec(box.uid)
                if first:
                    effective = first

            last_qc = effective.isoformat(sep=' ') if effective else ""
            last_mt = ev.latest_match_time.isoformat(sep=' ') if ev.latest_match_time else ""
            reason = ev.reason or ""
            used_parsed_str = "YES" if getattr(ev, "used_parsed", False) else "NO"

            if ev.results:
                for pr in ev.results:
                    if pr.test_name:
                        units = pr.units or ""
                        tol = ((pr.high - pr.low) / 2.0) if (pr.low is not None and pr.high is not None) else None
                        low = f"{pr.low:.6g}" if pr.low is not None else ""
                        high = f"{pr.high:.6g}" if pr.high is not None else ""
                        latest = "" if pr.latest_value is None else f"{pr.latest_value:.6g}"
                        insp = "" if pr.in_spec is None else ("YES" if pr.in_spec else "NO")
                        out_rows.append([
                            box.title, box.uid, status, (box.manual_override or ""),
                            box.csv_path, f"{box.qc_expire_hours:.2f}", last_qc, last_mt,
                            reason, used_parsed_str,
                            pr.test_name + ("" if not getattr(pr, 'sample_name', None) else f" ({pr.sample_name})"),
                            ("" if tol is None or pr.low is None or pr.high is None else f"{((pr.low+pr.high)/2.0):.6g}"), ("" if tol is None else f"{tol:.6g}"),
                            low, high, latest, insp, units
                        ])
            else:
                out_rows.append([
                    box.title, box.uid, status, (box.manual_override or ""),
                    box.csv_path, f"{box.qc_expire_hours:.2f}", last_qc, last_mt,
                    reason, used_parsed_str, "", "", "", "", "", "", "", ""
                ])
        return headers, out_rows

    def preview_report(self) -> None:
        headers, rows = self._build_report(self._last_rows_cache)
        ReportPreviewDialog(self, headers, rows).exec_()

    def export_report_now(self) -> None:
        if not self.cfg.report_dir:
            d = QFileDialog.getExistingDirectory(self, "Select Destination Folder")
            if not d:
                return
            self.cfg.report_dir = d
            self.save_config()
        os.makedirs(self.cfg.report_dir, exist_ok=True)
        today_str = datetime.now().strftime("%Y-%m-%d")
        out_path = os.path.join(self.cfg.report_dir, f"LabManagerReport_{today_str}.csv")
        headers, rows = self._build_report(self._last_rows_cache)
        try:
            with open(out_path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(headers)
                for r in rows:
                    w.writerow(r)
            self.cfg.last_report_date = today_str
            self.save_config()
            self.statusBar().showMessage(f"Report exported: {out_path}", 8000)
        except Exception as e:
            QMessageBox.warning(self, "Export failed", f"{e}")

    # ----- automatic daily report (scheduled + startup catch-up) -----
    def _maybe_run_daily_report(self, rows_by_path: Dict[str, List[dict]], force_if_missed: bool = False) -> None:
        if not self.cfg.report_enabled:
            return
        # scheduled time
        try:
            hh, mm = [int(x) for x in self.cfg.report_time.split(":")[:2]]
        except Exception:
            hh, mm = 17, 0
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")

        # Forced (startup catch-up): export immediately if we haven't exported today
        if force_if_missed and self.cfg.last_report_date != today_str:
            self._export_daily(rows_by_path, today_str)
            return

        # Normal schedule: only once per day at or after scheduled time
        if self.cfg.last_report_date == today_str:
            return
        if (now.hour, now.minute) < (hh, mm):
            return

        self._export_daily(rows_by_path, today_str)

    def _export_daily(self, rows_by_path: Dict[str, List[dict]], today_str: str) -> None:
        if not self.cfg.report_dir:
            return
        os.makedirs(self.cfg.report_dir, exist_ok=True)
        out_path = os.path.join(self.cfg.report_dir, f"LabManagerReport_{today_str}.csv")
        headers, rows = self._build_report(rows_by_path)
        try:
            with open(out_path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(headers)
                for r in rows:
                    w.writerow(r)
            self.cfg.last_report_date = today_str
            self.save_config()
            self.statusBar().showMessage(f"Daily report exported: {out_path}", 8000)
        except Exception as e:
            self.statusBar().showMessage(f"Daily report failed: {e}", 10000)



    # ----- Status Changes dock -----
    def _setup_maintenance_dock(self) -> None:
        self.maint_dock = QDockWidget("Maintenance", self)
        self.maint_dock.setObjectName("MaintenanceDock")
        body = QWidget(); v = QVBoxLayout(body)
        top = QHBoxLayout()
        self.maint_filter_chk = QCheckBox("Filter to selection")
        top.addWidget(self.maint_filter_chk); top.addStretch(1)
        v.addLayout(top)
        self.maint_table = QTableWidget(0, 6)
        self.maint_table.setHorizontalHeaderLabels(["Machine","Task","Next Due","Every","Last Completed","Status"]) 
        self.maint_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        try:
            self.maint_table.setEditTriggers(QTableWidget.NoEditTriggers)
        except Exception:
            pass
        v.addWidget(self.maint_table)
        body.setLayout(v)
        self.maint_dock.setWidget(body)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.maint_dock)
        try:
            self.table.itemSelectionChanged.connect(self._on_table_selection_changed)
            self.maint_table.itemDoubleClicked.connect(self._on_maint_row_activated)
        except Exception:
            pass
        self._maint_filter_uid: Optional[str] = None

    def _sanitize_title(self, s: str) -> str:
        import re
        s2 = re.sub(r"[^A-Za-z0-9_]+", "_", (s or "machine").strip())
        s2 = s2.strip("_") or "machine"
        return s2

    def _tasks_path_for(self, box: BoxConfig) -> str:
        base_dir = os.path.dirname(box.csv_path) if box.csv_path else os.getcwd()
        return os.path.join(base_dir, f"{self._sanitize_title(box.title)}_pm_tasks.csv")

    def _gather_all_tasks(self) -> List[Tuple[BoxConfig, dict]]:
        out: List[Tuple[BoxConfig, dict]] = []
        for box in self.cfg.boxes:
            path = self._tasks_path_for(box)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", newline="", encoding="utf-8") as f:
                    rd = csv.DictReader(f)
                    for r in rd:
                        if r.get("active", "1") == "0":
                            continue
                        out.append((box, r))
            except Exception:
                continue
        return out
    def _compute_next_due(self, rec: dict) -> Optional[datetime]:
        """Compute next due based on last_completed or creation id and interval."""
        try:
            unit = (rec.get("freq_unit") or "days").strip().lower()
            interval = max(1, int(rec.get("freq_interval", "1") or 1))
            base: Optional[datetime] = None
            lc = (rec.get("last_completed") or "").strip()
            if lc:
                try:
                    base = datetime.fromisoformat(lc)
                except Exception:
                    base = None
            if base is None:
                tid = (rec.get("id") or "").strip()
                try:
                    base = datetime.strptime(tid, "%Y%m%d%H%M%S%f")
                except Exception:
                    base = datetime.now()
            if unit == "days":
                due = base + timedelta(days=interval)
            elif unit == "weeks":
                due = base + timedelta(weeks=interval)
            else:
                due = base + timedelta(days=30*interval)
            return due
        except Exception:
            return None

    def _refresh_maintenance_sidebar(self) -> None:
        rows = self._gather_all_tasks()
        sel_uid = self._maint_filter_uid if self.maint_filter_chk.isChecked() else None
        if sel_uid:
            rows = [(b, r) for (b, r) in rows if b.uid == sel_uid]
        def key_due(tup):
            from datetime import datetime as _dt
            r = tup[1]
            try:
                return _dt.strptime(r.get("due_date", "9999-12-31"), "%Y-%m-%d")
            except Exception:
                return _dt.max
        rows.sort(key=key_due)

        self.maint_table.setRowCount(0)
        now = datetime.now()
        for (box, r) in rows:
            ridx = self.maint_table.rowCount(); self.maint_table.insertRow(ridx)
            every = f"every {r.get('freq_interval','')} {r.get('freq_unit','')}"
            due_s = r.get("due_date", "")
            status = ""
            overdue = False
            try:
                if due_s:
                    due_dt = datetime.strptime(due_s, "%Y-%m-%d")
                    if due_dt <= now:
                        status = "DUE"; overdue = True
            except Exception:
                pass
            vals = [box.title, r.get("task", ""), due_s, every, r.get("last_completed", ""), status]
            for c, val in enumerate(vals):
                self.maint_table.setItem(ridx, c, QTableWidgetItem(str(val)))
            if overdue:
                for c in range(self.maint_table.columnCount()):
                    it = self.maint_table.item(ridx, c)
                    if it:
                        it.setBackground(QColor(255, 230, 230))
                pass
            vals = [box.title, r.get("task", ""), due_s, every, r.get("last_completed", ""), status]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(str(val))
                if c == 0:
                    it.setData(Qt.UserRole, box.uid)
                self.maint_table.setItem(ridx, c, it)
            if overdue:
                for c in range(self.maint_table.columnCount()):
                    it = self.maint_table.item(ridx, c)
                    it.setBackground(QColor(255, 230, 230))

    def _tick_flash(self) -> None:
        self._flash_on = not self._flash_on
        rows = self.maint_table.rowCount()
        for r in range(rows):
            status_item = self.maint_table.item(r, 5)
            if status_item and status_item.text().strip().upper() == "DUE":
                color = QColor(255, 120, 120) if self._flash_on else QColor(255, 230, 230)
                for c in range(self.maint_table.columnCount()):
                    it = self.maint_table.item(r, c)
                    if it:
                        it.setBackground(color)

    def _on_scene_selection_changed(self) -> None:
        items = [it for it in self.scene.selectedItems() if isinstance(it, MachineBoxItem)]
        if items:
            self._maint_filter_uid = items[0].box.uid
        else:
            self._maint_filter_uid = None
        self._refresh_maintenance_sidebar()

    def _on_table_selection_changed(self) -> None:
        idx = self.table.currentRow()
        if idx < 0:
            self._maint_filter_uid = None
            self._refresh_maintenance_sidebar()
            return
        title_item = self.table.item(idx, 0)
        if not title_item:
            self._maint_filter_uid = None
        else:
            self._maint_filter_uid = title_item.data(Qt.UserRole)
        self._refresh_maintenance_sidebar()
    def _on_maint_row_activated(self, item: QTableWidgetItem) -> None:
        try:
            row = item.row()
            first = self.maint_table.item(row, 0)
            if not first:
                return
            uid = first.data(Qt.UserRole)
            box = next((b for b in self.cfg.boxes if b.uid == uid), None)
            if not box:
                return
            # Build latest evaluation for dialog
            rows = self._last_rows_cache.get(box.csv_path, [])
            ev = evaluate_box(box, self.samples_by_name, self.cfg.sample_id_col, rows)
            if box.manual_override == STATUS_DEAD:
                ev.status = STATUS_DEAD; ev.reason = "Manual override: DEAD-LINE"
            elif box.manual_override == STATUS_SERVICE:
                ev.status = STATUS_SERVICE; ev.reason = "Manual override: SERVICE"
            dlg = MachineInfoDialog(self, box, ev)
            # Focus Maintenance Log -> Scheduled PMs tab if available
            try:
                if hasattr(dlg, "focus_pm_tab"):
                    dlg.focus_pm_tab()
            except Exception:
                pass
            dlg.exec_()
            self._refresh_maintenance_sidebar()
        except Exception:
            pass















































