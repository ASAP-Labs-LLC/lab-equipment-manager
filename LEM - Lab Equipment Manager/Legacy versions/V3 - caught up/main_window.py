#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main_window.py â€” Main window, scene (grid), list view, toolbar, zoom, reporting, and orchestration.
"""

from __future__ import annotations

import os
import csv
from typing import Dict, List, Tuple, Optional

from datetime import datetime, timedelta

from PyQt5.QtCore import QTimer, QThread, Qt, QRectF, QLineF, QPoint, pyqtSignal
from PyQt5.QtGui import QPainter, QPen, QColor
from PyQt5.QtWidgets import (
    QMainWindow, QGraphicsView, QGraphicsScene, QTableWidget, QTableWidgetItem,
    QHeaderView, QStackedWidget, QToolBar, QAction, QLabel, QSpinBox, QComboBox,
    QDialog, QMenu, QFileDialog, QMessageBox, QDockWidget, QWidget, QVBoxLayout,
    QAbstractItemView, QPushButton
)

from models import AppConfig, BoxConfig, SampleSpec, STATUS_DEAD, STATUS_SERVICE, STATUS_GREEN, STATUS_RED, STATUS_YELLOW
from config_store import load_config, save_config
from data_source import CsvReadWorker, evaluate_box, BoxEvaluation
from box_item import MachineBoxItem, COLOR_GREEN, COLOR_RED, COLOR_YELLOW, COLOR_SERVICE_BG, COLOR_SERVICE_TEXT, COLOR_BLACK, COLOR_BLACK_TEXT, COLOR_TEXT_WHITE
from dialogs import SettingsDialog, BoxEditor, InfoDialog, ReportPreviewDialog
from maintenance import MaintenanceManager


def human_tdelta(td: timedelta) -> str:
    neg = td.total_seconds() < 0
    s = int(abs(td.total_seconds()))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    out = f"{d}d {h}h" if d else (f"{h}h {m}m" if h else f"{m}m")
    return f"-{out}" if neg else out


class ZoomableGraphicsView(QGraphicsView):
    """
    GraphicsView with Ctrl+wheel zoom, Shift+wheel horizontal pan, and 'fit to screen'.
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


class MachineScene(QGraphicsScene):
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

        painter.setPen(pen_minor)
        x = left
        while x <= right:
            painter.drawLine(QLineF(x, top, x, bottom))
            x += g
        y = top
        while y <= bottom:
            painter.drawLine(QLineF(left, y, right, y))
            y += g

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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Lab Manager Map")
        self.resize(1200, 750)

        self.cfg: AppConfig = load_config()
        self.sample_id_column: str = self.cfg.sample_id_column or "Lab ID"
        if not self.cfg.sample_id_column:
            self.cfg.sample_id_column = self.sample_id_column
        self.samples_by_name: Dict[str, SampleSpec] = {s.name: s for s in self.cfg.samples}
        self.box_items: Dict[str, MachineBoxItem] = {}
        self._last_rows_cache: Dict[str, List[dict]] = {}
        self._first_run = True  # startup catch-up

        self.scene = MachineScene(self)
        self.scene.map_locked = self.cfg.map_locked
        self.view = ZoomableGraphicsView(self.scene)

        # List view
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Title", "Status", "Override", "Last QC", "Expires In", "CSV", "Watched"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.table)
        self.setCentralWidget(self.stack)

        maintenance_dir = os.path.join(os.path.dirname(__file__), "Maintenance")
        self.maintenance = MaintenanceManager(maintenance_dir)
        self.maintenance_panel = MaintenancePanel(self.maintenance, self)
        self.addDockWidget(Qt.RightDockWidgetArea, self.maintenance_panel)
        self.maintenance_panel.taskActivated.connect(self._open_task_from_panel)

        self._setup_toolbar()
        for b in self.cfg.boxes:
            self._add_box_item(b)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_all)
        self._apply_poll_minutes(self.cfg.poll_minutes)

        QTimer.singleShot(200, self.refresh_all)

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
        zoom_in_act = QAction("Zoom In", self);  zoom_in_act.triggered.connect(self.view.zoom_in)
        zoom_out_act = QAction("Zoom Out", self); zoom_out_act.triggered.connect(self.view.zoom_out)
        fit_act = QAction("Fit to Screen", self); fit_act.triggered.connect(self.view.fit_to_scene)
        tb.addAction(zoom_in_act); tb.addAction(zoom_out_act); tb.addAction(fit_act)

        tb.addSeparator()
        # Report actions
        preview_act = QAction("Preview Report", self); preview_act.triggered.connect(self.preview_report)
        export_now_act = QAction("Export Report Now", self); export_now_act.triggered.connect(self.export_report_now)
        tb.addAction(preview_act); tb.addAction(export_now_act)

        tb.addSeparator()
        save_act = QAction("Save Layout", self); save_act.triggered.connect(self.save_config)
        tb.addAction(save_act)

    def _switch_view(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        if idx == 1:
            self._refresh_table()

    def _toggle_map_lock(self, locked: bool) -> None:
        self.cfg.map_locked = locked
        self.scene.map_locked = locked
        for item in self.box_items.values():
            item.set_movable(not (locked or item.box.locked))
            item.sync_lock_state()
        self.save_config()

    def _change_poll_minutes(self, v: int) -> None:
        self.cfg.poll_minutes = int(v)
        self._apply_poll_minutes(self.cfg.poll_minutes)
        self.save_config()

    def _apply_poll_minutes(self, minutes: int) -> None:
        self.timer.stop()
        self.timer.start(max(1, minutes) * 60 * 1000)

    # ----- config I/O -----
    def save_config(self) -> None:
        ok, msg = save_config(self.cfg)
        if not ok:
            self.statusBar().showMessage(f"Save failed: {msg}", 8000)

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
            g = float(self.scene.GRID_SIZE)
            new_box.size = (g, g)
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
            updated.pos = box.pos
            updated.manual_override = box.manual_override
            updated.size = box.size
            for i, b in enumerate(self.cfg.boxes):
                if b.uid == box.uid:
                    self.cfg.boxes[i] = updated
                    break
            item = self.box_items[box.uid]
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
        eval_res = evaluate_box(box, self.samples_by_name, self.sample_id_column, rows)
        if box.manual_override == STATUS_DEAD:
            eval_res.status = STATUS_DEAD
            eval_res.reason = "Manual override: DEAD-LINE"
        elif box.manual_override == STATUS_SERVICE:
            eval_res.status = STATUS_SERVICE
            eval_res.reason = "Manual override: SERVICE"
        self.maintenance_panel.set_machine_filter(box.uid)
        dlg = InfoDialog(self, box, eval_res, self.maintenance, self)
        dlg.exec_()
        self.maintenance_panel.set_machine_filter(None)
        self.maintenance_panel.update_items()


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
        info_act = menu.addAction("Infoâ€¦")
        edit_act = menu.addAction("Edit Boxâ€¦")
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
            if chosen == off_act:
                box.manual_override = ""
            elif chosen == dead_act:
                box.manual_override = STATUS_DEAD
            else:
                box.manual_override = STATUS_SERVICE
            self.box_items[box.uid].apply_visuals()
            self.save_config(); self._refresh_table()

    # ----- settings -----
    def open_settings(self) -> None:
        dlg = SettingsDialog(
            self,
            list(self.samples_by_name.values()),
            sample_id_column=self.cfg.sample_id_column,
            report_enabled=self.cfg.report_enabled,
            report_time=self.cfg.report_time,
            report_dir=self.cfg.report_dir,
        )
        if dlg.exec_() == QDialog.Accepted:
            self.cfg.samples = dlg.get_samples()
            self.samples_by_name = {s.name: s for s in self.cfg.samples}
            self.cfg.sample_id_column = dlg.get_sample_id_column()
            self.sample_id_column = self.cfg.sample_id_column or "Lab ID"
            enabled, time_str, directory = dlg.get_report_settings()
            self.cfg.report_enabled = enabled
            self.cfg.report_time = time_str
            self.cfg.report_dir = directory
            self.save_config()
            self.refresh_all()

    # ----- refresh & (startup catch-up) & daily report -----
    def refresh_all(self) -> None:
        paths = sorted({b.csv_path for b in self.cfg.boxes if b.csv_path})
        if not paths:
            self._maybe_run_daily_report({}, force_if_missed=self._first_run)  # still allow catch-up report on empty
            self._first_run = False
            return
        self._thread = QThread()
        self._worker = CsvReadWorker(paths)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_rows_loaded)
        self._worker.error.connect(self._on_rows_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_rows_error(self, path: str, msg: str) -> None:
        self.statusBar().showMessage(f"CSV error for {path}: {msg}", 8000)

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
            # store/local time; interpret as local naive
            return datetime.fromisoformat(iso)
        except Exception:
            return None

    def _set_first_inspec_if_missing(self, uid: str, when: datetime) -> None:
        if uid not in self.cfg.first_inspec_map:
            self.cfg.first_inspec_map[uid] = when.replace(microsecond=0).isoformat(sep=' ')
            self.save_config()

    def _on_rows_loaded(self, rows_by_path: Dict[str, List[dict]]) -> None:
        self._last_rows_cache = rows_by_path

        # Startup catch-up: export today's report immediately if missed (before drawing today's statuses)
        if self._first_run:
            self._maybe_run_daily_report(rows_by_path, force_if_missed=True)
            self._first_run = False

        self._ensure_first_inspec_epoch()

        for box in self.cfg.boxes:
            rows = rows_by_path.get(box.csv_path, [])
            eval_res: BoxEvaluation = evaluate_box(box, self.samples_by_name, self.sample_id_column, rows)

            # Fallback in-spec clock: if no parsed_date/time used, remember first time it went GREEN today
            effective_last_good = eval_res.last_good_qc
            if not eval_res.used_parsed:
                if eval_res.status in (STATUS_GREEN, STATUS_YELLOW):
                    # record first in-spec if missing
                    now_local = datetime.now()
                    self._set_first_inspec_if_missing(box.uid, now_local)
                    effective_last_good = self._get_first_inspec(box.uid) or eval_res.last_good_qc
                else:
                    effective_last_good = self._get_first_inspec(box.uid) or eval_res.last_good_qc

            # Manual overrides override visual status, but we still compute age lines from effective clock
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

            # Build box info lines
            lines: List[str] = []
            if eval_res.results:
                for pr in eval_res.results[:4]:
                    if pr.test:
                        tol = pr.test.k * pr.test.std_dev
                        rng = f"[{pr.low:.6g}, {pr.high:.6g}]" if pr.low is not None else "â€”"
                        vtxt = "â€”" if pr.latest_value is None else f"{pr.latest_value:.6g}"
                        flag = "" if pr.in_spec is None else ("âœ“" if pr.in_spec else "âœ—")
                        units = f" {pr.test.units}" if pr.test.units else ""
                        lines.append(f"{pr.test.name}: {vtxt}{units} {flag}  tolÂ±{tol:.6g}  {rng}")
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
                # show last row age as a fallback
                age = datetime.utcnow() - (eval_res.latest_match_time)
                lines.append(f"Last row: {human_tdelta(age)} ago")

            item = self.box_items.get(box.uid)
            if item:
                item.set_status(status, reason, lines)

        if self.stack.currentIndex() == 1:
            self._refresh_table()

        # Regular scheduled export (after updating UI)
        self._maybe_run_daily_report(rows_by_path)

    def _refresh_table(self) -> None:
        self.table.setRowCount(0)
        for box in self.cfg.boxes:
            item = self.box_items.get(box.uid)
            if not item:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            rows = self._last_rows_cache.get(box.csv_path, [])
            eval_res = evaluate_box(box, self.samples_by_name, self.sample_id_column, rows)
            status = eval_res.status
            if box.manual_override == STATUS_DEAD:
                status = STATUS_DEAD
            elif box.manual_override == STATUS_SERVICE:
                status = STATUS_SERVICE

            title = QTableWidgetItem(box.title)
            title.setData(Qt.UserRole, box.uid)
            self.table.setItem(row, 0, title)

            self.table.setItem(row, 1, QTableWidgetItem(status))
            self.table.setItem(row, 2, QTableWidgetItem(box.manual_override or '-'))
            self.table.setItem(row, 3, QTableWidgetItem(
                eval_res.last_good_qc.isoformat(sep=' ') if eval_res.last_good_qc else '-'
            ))

            if eval_res.last_good_qc and eval_res.status in (STATUS_GREEN, STATUS_YELLOW):
                ttl = (eval_res.last_good_qc + timedelta(hours=box.qc_expire_hours)) - datetime.utcnow()
                ttl_txt = f"{int(ttl.total_seconds()//3600)}h {int((ttl.total_seconds()%3600)//60)}m"
            else:
                ttl_txt = '-'
            self.table.setItem(row, 4, QTableWidgetItem(ttl_txt))

            self.table.setItem(row, 5, QTableWidgetItem(box.csv_path))
            watched_text = ', '.join(f"{wt.sample}/{wt.test}" for wt in box.watched_targets) if box.watched_targets else '-'
            self.table.setItem(row, 6, QTableWidgetItem(watched_text))

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
            "Sample", "Test Name", "Expected", "k*StdDev", "Low", "High", "Latest Value", "In Spec", "Units"
        ]
        out_rows: List[List[str]] = []
        for box in self.cfg.boxes:
            rows = rows_by_path.get(box.csv_path, [])
            ev = evaluate_box(box, self.samples_by_name, self.sample_id_column, rows)
            status = ev.status
            if box.manual_override == STATUS_DEAD:
                status = STATUS_DEAD
            elif box.manual_override == STATUS_SERVICE:
                status = STATUS_SERVICE

            self._ensure_first_inspec_epoch()
            effective = ev.last_good_qc
            if not ev.used_parsed:
                first = self._get_first_inspec(box.uid)
                if first:
                    effective = first

            last_qc = effective.isoformat(sep=' ') if effective else ""
            last_mt = ev.latest_match_time.isoformat(sep=' ') if ev.latest_match_time else ""
            reason = ev.reason or ""
            used_parsed_str = "YES" if ev.used_parsed else "NO"

            if ev.results:
                for pr in ev.results:
                    sample_name = pr.sample
                    if pr.test:
                        units = pr.test.units or ""
                        tol = pr.test.k * pr.test.std_dev
                        low = f"{pr.low:.6g}" if pr.low is not None else ""
                        high = f"{pr.high:.6g}" if pr.high is not None else ""
                        expected = f"{pr.test.expected:.6g}"
                        latest = "" if pr.latest_value is None else f"{pr.latest_value:.6g}"
                        insp = "" if pr.in_spec is None else ("YES" if pr.in_spec else "NO")
                        out_rows.append([
                            box.title, box.uid, status, (box.manual_override or ""),
                            box.csv_path, f"{box.qc_expire_hours:.2f}", last_qc, last_mt,
                            reason, used_parsed_str,
                            sample_name, pr.test.name, expected, f"{tol:.6g}",
                            low, high, latest, insp, units
                        ])
                    else:
                        out_rows.append([
                            box.title, box.uid, status, (box.manual_override or ""),
                            box.csv_path, f"{box.qc_expire_hours:.2f}", last_qc, last_mt,
                            reason, used_parsed_str,
                            sample_name, "", "", "", "", "", "", "", ""
                        ])
            else:
                out_rows.append([
                    box.title, box.uid, status, (box.manual_override or ""),
                    box.csv_path, f"{box.qc_expire_hours:.2f}", last_qc, last_mt,
                    reason, used_parsed_str,
                    "", "", "", "", "", "", "", "", ""
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

        # If forced (startup catch-up), export immediately if we haven't exported today yet
        if force_if_missed and self.cfg.last_report_date != today_str:
            self._export_daily(rows_by_path, today_str)
            return

        # Otherwise: only export once per day, at or after scheduled time
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






class MaintenancePanel(QDockWidget):
    taskActivated = pyqtSignal(str, str)

    def __init__(self, manager: MaintenanceManager, owner: 'MainWindow') -> None:
        super().__init__("Maintenance")
        self.manager = manager
        self.owner = owner
        self.filter_uid: Optional[str] = None
        self._flash_state = False
        self._flash_rows: List[Tuple[int, QColor]] = []

        container = QWidget()
        layout = QVBoxLayout(container)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Due", "Machine", "Task", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self._activate_selected)
        layout.addWidget(self.table)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.update_items)
        layout.addWidget(refresh_btn)

        self.setWidget(container)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._pulse)
        self._timer.start(800)
        self.update_items()

    def set_machine_filter(self, uid: Optional[str]) -> None:
        self.filter_uid = uid
        self.update_items()

    def update_items(self) -> None:
        tasks = self.manager.get_tasks(self.filter_uid)
        self.table.setRowCount(len(tasks))
        self._flash_rows = []
        for row, tpl in enumerate(tasks):
            due_item = QTableWidgetItem(tpl.next_due)
            due_item.setData(Qt.UserRole, tpl.id)
            due_item.setData(Qt.UserRole + 1, tpl.box_uid)
            machine_item = QTableWidgetItem(tpl.box_title)
            task_item = QTableWidgetItem(tpl.name)
            status_item = QTableWidgetItem(tpl.status.replace('_', ' ').title())

            self.table.setItem(row, 0, due_item)
            self.table.setItem(row, 1, machine_item)
            self.table.setItem(row, 2, task_item)
            self.table.setItem(row, 3, status_item)

            bg = None
            if tpl.status == 'OVERDUE':
                bg = QColor(200, 60, 60)
            elif tpl.status == 'DUE':
                bg = QColor(230, 120, 60)
                self._flash_rows.append((row, QColor(230, 120, 60)))
            elif tpl.status == 'SOON':
                bg = QColor(255, 200, 120)
                self._flash_rows.append((row, QColor(255, 200, 120)))
            elif tpl.status == 'IN_PROGRESS':
                bg = QColor(100, 140, 200)
            if bg is not None:
                for col in range(4):
                    item = self.table.item(row, col)
                    if item:
                        item.setBackground(bg)

    def _activate_selected(self) -> None:
        idx = self.table.currentRow()
        if idx < 0:
            return
        item = self.table.item(idx, 0)
        if not item:
            return
        task_id = item.data(Qt.UserRole) or ""
        box_uid = item.data(Qt.UserRole + 1) or ""
        self.taskActivated.emit(box_uid, task_id)

    def _pulse(self) -> None:
        self._flash_state = not self._flash_state
        for row, base in self._flash_rows:
            for col in range(4):
                item = self.table.item(row, col)
                if not item:
                    continue
                if self._flash_state:
                    item.setBackground(QColor(255, 240, 150))
                else:
                    item.setBackground(base)


    def _find_box(self, uid: str) -> Optional[BoxConfig]:
        for box in self.cfg.boxes:
            if box.uid == uid:
                return box
        return None

    def add_maintenance_task(self, box: BoxConfig, name: str, kind: str,
                             start_date: datetime, repeat_value: int, repeat_unit: str) -> None:
        self.maintenance.create_task(box.uid, box.title, name, kind, start_date, repeat_value, repeat_unit)
        self.maintenance_panel.update_items()
        self.save_config()

    def start_maintenance_task(self, task_id: str) -> None:
        tpl = self.maintenance.start_task(task_id)
        if not tpl:
            return
        box = self._find_box(tpl.box_uid)
        if box:
            box.manual_override = STATUS_SERVICE
        self.save_config()
        self.maintenance_panel.update_items()
        self.refresh_all()

    def complete_maintenance_task(self, task_id: str, user: str, comment: str) -> None:
        tpl = self.maintenance.complete_task(task_id, user, comment)
        if not tpl:
            return
        box = self._find_box(tpl.box_uid)
        if box:
            # Only clear override if no other tasks in progress
            in_progress = any(t.status == 'IN_PROGRESS' and t.box_uid == tpl.box_uid
                              for t in self.maintenance.templates.values())
            if not in_progress:
                box.manual_override = ""
        self.save_config()
        self.maintenance_panel.update_items()
        self.refresh_all()

    def add_maintenance_comment(self, box: BoxConfig, comment: str, user: str) -> None:
        self.maintenance.add_comment(box.uid, box.title, comment, user)
        self.maintenance_panel.update_items()

    def _open_task_from_panel(self, box_uid: str, task_id: str) -> None:
        box = self._find_box(box_uid)
        if not box:
            return
        rows = self._last_rows_cache.get(box.csv_path, [])
        eval_res = evaluate_box(box, self.samples_by_name, self.sample_id_column, rows)
        self.maintenance_panel.set_machine_filter(box.uid)
        dlg = InfoDialog(self, box, eval_res, self.maintenance, self)
        dlg.exec_()
        self.maintenance_panel.set_machine_filter(None)
        self.maintenance_panel.update_items()






# --- Bind maintenance helpers to MainWindow (ensure available) ---

def _mw_find_box(self, uid: str) -> Optional[BoxConfig]:
    for box in self.cfg.boxes:
        if box.uid == uid:
            return box
    return None

def _mw_add_maintenance_task(self, box: BoxConfig, name: str, kind: str,
                             start_date: datetime, repeat_value: int, repeat_unit: str) -> None:
    self.maintenance.create_task(box.uid, box.title, name, kind, start_date, repeat_value, repeat_unit)
    self.maintenance_panel.update_items()
    self.save_config()

def _mw_start_maintenance_task(self, task_id: str) -> None:
    tpl = self.maintenance.start_task(task_id)
    if not tpl:
        return
    box = _mw_find_box(self, tpl.box_uid)
    if box:
        box.manual_override = STATUS_SERVICE
    self.save_config()
    self.maintenance_panel.update_items()
    self.refresh_all()

def _mw_complete_maintenance_task(self, task_id: str, user: str, comment: str) -> None:
    tpl = self.maintenance.complete_task(task_id, user, comment)
    if not tpl:
        return
    box = _mw_find_box(self, tpl.box_uid)
    if box:
        in_progress = any(t.status == 'IN_PROGRESS' and t.box_uid == tpl.box_uid
                          for t in self.maintenance.templates.values())
        if not in_progress:
            box.manual_override = ""
    self.save_config()
    self.maintenance_panel.update_items()
    self.refresh_all()

def _mw_add_maintenance_comment(self, box: BoxConfig, comment: str, user: str) -> None:
    self.maintenance.add_comment(box.uid, box.title, comment, user)
    self.maintenance_panel.update_items()

def _mw_open_task_from_panel(self, box_uid: str, task_id: str) -> None:
    box = _mw_find_box(self, box_uid)
    if not box:
        return
    rows = self._last_rows_cache.get(box.csv_path, [])
    eval_res = evaluate_box(box, self.samples_by_name, self.sample_id_column, rows)
    self.maintenance_panel.set_machine_filter(box.uid)
    dlg = InfoDialog(self, box, eval_res, self.maintenance, self)
    dlg.exec_()
    self.maintenance_panel.set_machine_filter(None)
    self.maintenance_panel.update_items()

# Attach if missing
try:
    MainWindow
except NameError:
    pass
else:
    if not hasattr(MainWindow, '_find_box'):
        MainWindow._find_box = _mw_find_box
    if not hasattr(MainWindow, 'add_maintenance_task'):
        MainWindow.add_maintenance_task = _mw_add_maintenance_task
    if not hasattr(MainWindow, 'start_maintenance_task'):
        MainWindow.start_maintenance_task = _mw_start_maintenance_task
    if not hasattr(MainWindow, 'complete_maintenance_task'):
        MainWindow.complete_maintenance_task = _mw_complete_maintenance_task
    if not hasattr(MainWindow, 'add_maintenance_comment'):
        MainWindow.add_maintenance_comment = _mw_add_maintenance_comment
    if not hasattr(MainWindow, '_open_task_from_panel'):
        MainWindow._open_task_from_panel = _mw_open_task_from_panel
