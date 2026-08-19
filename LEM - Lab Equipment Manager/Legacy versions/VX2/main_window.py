#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Enhanced PyQt front end for the Lab Manager server.

This window restores the interactive map (drag/drop boxes, zoom, overrides)
while delegating all business logic to the FastAPI backend.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer, QLineF
from PyQt5.QtGui import QPainter, QColor, QBrush, QFont, QPen
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QAction,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFormLayout,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
    QTextEdit,
    QHeaderView,
)

from models import (
    AppConfig,
    BoxConfig,
    STATUS_DEAD,
    STATUS_GREEN,
    STATUS_RED,
    STATUS_SERVICE,
    STATUS_UNKNOWN,
    STATUS_YELLOW,
)
from server_client import ServerClient

try:
    from theme import theme_manager  # type: ignore[reportMissingImports]
except Exception:  # pragma: no cover
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(__file__))
    from theme import theme_manager  # type: ignore[reportMissingImports]

def human_tdelta(td: timedelta) -> str:
    """Pretty delta used for info banners."""
    neg = td.total_seconds() < 0
    s = int(abs(td.total_seconds()))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    if d:
        out = f"{d}d {h}h"
    elif h:
        out = f"{h}h {m}m"
    else:
        out = f"{m}m"
    return f"-{out}" if neg else out


class ZoomableGraphicsView(QGraphicsView):
    """Simple zoom/pan view with limits and shortcuts."""

    def __init__(self, scene: QGraphicsScene) -> None:
        super().__init__(scene)
        self._zoom = 1.0
        self._min_zoom = 0.2
        self._max_zoom = 5.0
        self._view_locked = False
        self.setRenderHints(self.renderHints() | QPainter.Antialiasing | QPainter.SmoothPixmapTransform)

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
        if not self._view_locked:
            self._apply_zoom(1.15)

    def zoom_out(self) -> None:
        if not self._view_locked:
            self._apply_zoom(1 / 1.15)

    def fit_to_scene(self) -> None:
        rect = self.scene().itemsBoundingRect()  # type: ignore[arg-type]
        if rect.isNull():
            rect = self.sceneRect()
        margin = 20.0
        rect = rect.adjusted(-margin, -margin, margin, margin)
        if rect.isValid():
            self.fitInView(rect, Qt.KeepAspectRatio)
            self._zoom = 1.0

    def set_view_locked(self, locked: bool) -> None:
        self._view_locked = locked

    def get_view_state(self) -> List[float]:
        center = self.mapToScene(self.viewport().rect().center())
        return [center.x(), center.y(), float(self._zoom)]

    def apply_view_state(self, center_x: float, center_y: float, zoom: float) -> None:
        try:
            z = float(zoom)
        except Exception:
            z = 1.0
        z = max(self._min_zoom, min(self._max_zoom, z))
        self.resetTransform()
        self._zoom = 1.0
        self._apply_zoom(z)
        self.centerOn(center_x, center_y)


class MachineScene(QGraphicsScene):
    GRID_SIZE = 20.0

    def __init__(self, parent_window: "MainWindow") -> None:
        super().__init__(QRectF(0, 0, 5000, 3000))
        self.parent_window = parent_window
        self.map_locked = False
        self.apply_theme()

    def apply_theme(self) -> None:
        tm = theme_manager()
        self.setBackgroundBrush(QBrush(tm.color("scene_bg", "#1f1f1f")))

    def drawBackground(self, painter, rect) -> None:  # type: ignore[override]
        painter.setRenderHint(painter.Antialiasing, False)
        grid = float(self.GRID_SIZE)
        left = math.floor(rect.left() / grid) * grid
        top = math.floor(rect.top() / grid) * grid
        right = rect.right()
        bottom = rect.bottom()
        pen = QPen(theme_manager().color("scene_grid", "#2c2c2c"))
        pen.setWidthF(0.0)
        painter.setPen(pen)
        x = left
        while x <= right:
            painter.drawLine(QLineF(x, top, x, bottom))
            x += grid
        y = top
        while y <= bottom:
            painter.drawLine(QLineF(left, y, right, y))
            y += grid


class MachineItem(QGraphicsRectItem):
    """Graphics item representing a machine on the map."""

    def __init__(self, window: "MainWindow", box: BoxConfig) -> None:
        super().__init__()
        self.window = window
        self.box = box
        self._status = STATUS_UNKNOWN
        self._reason = ""
        self._info_lines: List[str] = []
        self._manual_override = box.manual_override or ""

        self.setPen(QPen(theme_manager().color("border_color", "#212121"), 2))
        self.setFlag(self.ItemIsSelectable, True)
        self.setFlag(self.ItemIsMovable, True)
        self.setAcceptHoverEvents(True)

        title_font = QFont()
        title_font.setPointSize(10)
        title_font.setBold(True)
        self.title_item = QGraphicsTextItem(self.box.title, self)
        self.title_item.setFont(title_font)
        self.title_item.setPos(6, 4)

        detail_font = QFont()
        detail_font.setPointSize(8)
        self.detail_item = QGraphicsTextItem("", self)
        self.detail_item.setFont(detail_font)
        self.detail_item.setPos(6, 24)
        self.detail_item.setDefaultTextColor(theme_manager().color("machine_text_default", "#1e1e1e"))
        self.detail_item.setTextWidth(max(60.0, self.box.size[0] - 12))

        self.update_geometry(box)
        self.apply_status()

    def update_geometry(self, box: BoxConfig) -> None:
        self.box = box
        self.prepareGeometryChange()
        self.setRect(0, 0, box.size[0], box.size[1])
        self.setPos(QPointF(box.pos[0], box.pos[1]))
        self.title_item.setPlainText(box.title)
        self.detail_item.setTextWidth(max(60.0, box.size[0] - 12))
        self._update_movable_state()

    def update_status(self, status_payload: Dict[str, Any]) -> None:
        self._status = status_payload.get("status", STATUS_UNKNOWN)
        self._reason = status_payload.get("reason", "")
        self._manual_override = status_payload.get("manual_override", "") or ""

        lines: List[str] = []
        if self._manual_override:
            lines.append(f"Override: {self._manual_override}")
        if self._reason:
            lines.append(self._reason)
        last_good = status_payload.get("last_good_qc")
        if last_good:
            try:
                qc_dt = datetime.fromisoformat(str(last_good))
                delta = datetime.now() - qc_dt
                lines.append(f"In spec {human_tdelta(delta)} ago")
            except Exception:
                pass
        params = status_payload.get("parameter_results", [])
        for pr in params[:3]:
            sample = pr.get("sample") or "?"
            test = pr.get("test") or "?"
            value = pr.get("latest_value")
            units = pr.get("units") or ""
            if value is None:
                lines.append(f"{sample}/{test}: no data")
            else:
                lines.append(f"{sample}/{test}: {value:.4g}{units}")
        self._info_lines = lines
        self.detail_item.setPlainText("\n".join(lines))
        self.apply_status()

    def apply_status(self) -> None:
        tm = theme_manager()
        if self._manual_override == STATUS_DEAD:
            self.setBrush(QBrush(tm.color("machine_black", "#000000")))
            color = tm.color("machine_black_text", "#dc143c")
        elif self._manual_override == STATUS_SERVICE:
            self.setBrush(QBrush(tm.color("machine_service_bg", "#e6e6e6")))
            color = tm.color("machine_service_text", "#3c3c3c")
        elif self._status == STATUS_GREEN:
            self.setBrush(QBrush(tm.color("machine_green", "#2ecc71")))
            color = tm.color("machine_text_white", "#ffffff")
        elif self._status == STATUS_RED:
            self.setBrush(QBrush(tm.color("machine_red", "#e74c3c")))
            color = tm.color("machine_text_white", "#ffffff")
        elif self._status == STATUS_YELLOW:
            self.setBrush(QBrush(tm.color("machine_yellow", "#f1c40f")))
            color = tm.color("machine_text_default", "#1e1e1e")
        else:
            self.setBrush(QBrush(tm.color("machine_default_bg", "#bdc3c7")))
            color = tm.color("machine_text_default", "#1e1e1e")
        self.title_item.setDefaultTextColor(color)
        self.detail_item.setDefaultTextColor(color)
        self._update_movable_state()

    def _update_movable_state(self) -> None:
        movable = (not self.window.cfg.map_locked) and (not getattr(self.box, "locked", False))
        self.setFlag(self.ItemIsMovable, movable)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        super().mouseReleaseEvent(event)
        if not self.scene() or self.window.cfg.map_locked:
            return
        self.box.pos = (self.pos().x(), self.pos().y())
        self.window.persist_layout_change(self.box)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        super().mouseDoubleClickEvent(event)
        self.window.show_box_details(self.box.uid)

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        menu = QMenu()
        info_act = menu.addAction("Details...")
        lock_act = menu.addAction("Unlock" if getattr(self.box, "locked", False) else "Lock")
        menu.addSeparator()
        dead_act = menu.addAction("Override DEAD-LINE")
        serv_act = menu.addAction("Override SERVICE")
        clear_act = menu.addAction("Clear Override") if self._manual_override else None
        chosen = menu.exec_(event.screenPos())
        if not chosen:
            return
        if chosen == info_act:
            self.window.show_box_details(self.box.uid)
        elif chosen == lock_act:
            self.window.toggle_box_lock(self.box)
        elif clear_act and chosen == clear_act:
            self.window.clear_manual_override(self.box)
        elif chosen in (dead_act, serv_act):
            mode = STATUS_DEAD if chosen == dead_act else STATUS_SERVICE
            self.window.prompt_manual_override(self.box, mode)


class MaintenanceDialog(QDialog):
    """Minimal maintenance manager UI backed by server endpoints."""

    def __init__(self, client: ServerClient, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._client = client
        self.setWindowTitle("Maintenance Tasks")
        self.resize(820, 420)

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Task ID",
            "Machine",
            "Task",
            "Status",
            "Next Due",
            "Kind",
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        btn_row.addWidget(refresh_btn)
        start_btn = QPushButton("Start")
        start_btn.clicked.connect(self._start_task)
        btn_row.addWidget(start_btn)
        complete_btn = QPushButton("Complete")
        complete_btn.clicked.connect(self._complete_task)
        btn_row.addWidget(complete_btn)
        comment_btn = QPushButton("Comment")
        comment_btn.clicked.connect(self._comment_task)
        btn_row.addWidget(comment_btn)
        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._delete_task)
        btn_row.addWidget(delete_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        create_btn = QPushButton("Create Task")
        create_btn.clicked.connect(self._create_task)
        layout.addWidget(create_btn)

        self.refresh()

    def _selected_task(self) -> Optional[Dict[str, Any]]:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Maintenance", "Select a task first.")
            return None
        item = self.table.item(row, 0)
        if not item:
            return None
        return item.data(Qt.UserRole)

    def refresh(self) -> None:
        try:
            tasks = self._client.maintenance_tasks()
        except Exception as exc:
            QMessageBox.warning(self, "Maintenance", f"Failed to load tasks: {exc}")
            return
        self.table.setRowCount(0)
        for task in tasks:
            row = self.table.rowCount()
            self.table.insertRow(row)
            cell = QTableWidgetItem(task.get("id", ""))
            cell.setData(Qt.UserRole, task)
            self.table.setItem(row, 0, cell)
            self.table.setItem(row, 1, QTableWidgetItem(task.get("box_title", "")))
            self.table.setItem(row, 2, QTableWidgetItem(task.get("name", "")))
            self.table.setItem(row, 3, QTableWidgetItem(task.get("status", "")))
            self.table.setItem(row, 4, QTableWidgetItem(task.get("next_due", "")))
            self.table.setItem(row, 5, QTableWidgetItem(task.get("kind", "")))

    def _create_task(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Create Maintenance Task")
        form = QFormLayout(dlg)
        box_uid = QLineEdit(dlg)
        box_title = QLineEdit(dlg)
        name = QLineEdit(dlg)
        kind = QLineEdit(dlg)
        start_date = QLineEdit(dlg)
        start_date.setPlaceholderText("YYYY-MM-DD")
        repeat_value = QLineEdit(dlg)
        repeat_unit = QLineEdit(dlg)
        notes = QLineEdit(dlg)
        form.addRow("Box UID", box_uid)
        form.addRow("Box Title", box_title)
        form.addRow("Task Name", name)
        form.addRow("Kind", kind)
        form.addRow("Start Date", start_date)
        form.addRow("Repeat Value", repeat_value)
        form.addRow("Repeat Unit", repeat_unit)
        form.addRow("Notes", notes)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dlg)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec_() != QDialog.Accepted:
            return
        try:
            payload = {
                "box_uid": box_uid.text().strip(),
                "box_title": box_title.text().strip(),
                "name": name.text().strip(),
                "kind": kind.text().strip() or "pm",
                "start_date": start_date.text().strip() or datetime.now().strftime("%Y-%m-%d"),
                "repeat_value": int(repeat_value.text() or "1"),
                "repeat_unit": repeat_unit.text().strip() or "months",
                "notes": notes.text().strip(),
            }
            self._client.maintenance_create(payload)
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Create Task", f"Failed to create task: {exc}")

    def _start_task(self) -> None:
        task = self._selected_task()
        if not task:
            return
        try:
            self._client.maintenance_start(task["id"])
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Start Task", f"Failed to start task: {exc}")

    def _complete_task(self) -> None:
        task = self._selected_task()
        if not task:
            return
        user, ok = QInputDialog.getText(self, "Complete Task", "Technician:")
        if not ok:
            return
        comment, ok = QInputDialog.getMultiLineText(self, "Complete Task", "Comment:")
        if not ok or not comment.strip():
            QMessageBox.warning(self, "Complete Task", "Comment is required.")
            return
        try:
            self._client.maintenance_complete(task["id"], user, comment)
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Complete Task", f"Failed to complete task: {exc}")

    def _comment_task(self) -> None:
        task = self._selected_task()
        if not task:
            return
        comment, ok = QInputDialog.getMultiLineText(self, "Add Comment", "Comment:")
        if not ok or not comment.strip():
            return
        user, ok = QInputDialog.getText(self, "Add Comment", "Technician:")
        if not ok:
            return
        try:
            self._client.maintenance_comment(
                task["id"], task.get("box_uid", ""), task.get("box_title", ""), comment, user
            )
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Add Comment", f"Failed to add comment: {exc}")

    def _delete_task(self) -> None:
        task = self._selected_task()
        if not task:
            return
        confirm = QMessageBox.question(self, "Delete Task", f"Delete task '{task.get('name')}'?")
        if confirm != QMessageBox.Yes:
            return
        try:
            self._client.maintenance_delete(task["id"])
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Delete Task", f"Failed to delete task: {exc}")


class BoxInfoDialog(QDialog):
    """Simple dialog displaying status payload for a box."""

    def __init__(self, box: BoxConfig, status_payload: Dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{box.title} - Details")
        self.resize(520, 360)

        layout = QVBoxLayout(self)
        summary = QLabel()
        summary.setWordWrap(True)
        summary_lines = [f"Status: {status_payload.get('status', STATUS_UNKNOWN)}"]
        reason = status_payload.get("reason")
        if reason:
            summary_lines.append(reason)
        override = status_payload.get("manual_override")
        if override:
            summary_lines.append(f"Manual override: {override}")
        evaluated = status_payload.get("evaluated_at")
        if evaluated:
            summary_lines.append(f"Evaluated at: {evaluated}")
        summary.setText("\n".join(summary_lines))
        layout.addWidget(summary)

        table = QTableWidget(0, 6)
        table.setHorizontalHeaderLabels([
            "Sample",
            "Test",
            "Latest",
            "In Spec",
            "Low",
            "High",
        ])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        params = status_payload.get("parameter_results", [])
        for pr in params:
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(str(pr.get("sample", ""))))
            table.setItem(row, 1, QTableWidgetItem(str(pr.get("test", ""))))
            latest = pr.get("latest_value")
            table.setItem(row, 2, QTableWidgetItem("" if latest is None else f"{latest:.4g}"))
            insp = pr.get("in_spec")
            table.setItem(row, 3, QTableWidgetItem("" if insp is None else ("YES" if insp else "NO")))
            low = pr.get("low")
            high = pr.get("high")
            table.setItem(row, 4, QTableWidgetItem("" if low is None else f"{low:.4g}"))
            table.setItem(row, 5, QTableWidgetItem("" if high is None else f"{high:.4g}"))
        layout.addWidget(table)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)


class MainWindow(QMainWindow):
    """Primary window hosting the map and status table."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Lab Manager Console")
        self.resize(1280, 760)

        self.client = ServerClient()
        self.cfg: AppConfig = AppConfig.from_dict({"version": 5, "poll_minutes": 5, "map_locked": False, "samples": [], "boxes": []})
        self.box_items: Dict[str, MachineItem] = {}
        self.status_snapshot: Dict[str, Dict[str, Any]] = {}

        self.scene = MachineScene(self)
        self.view = ZoomableGraphicsView(self.scene)
        self.setCentralWidget(self.view)

        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)

        self._setup_toolbar()
        self._setup_status_table()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_snapshot)

        self.refresh_snapshot(initial=True)
    def _setup_toolbar(self) -> None:
        tb = QToolBar("Main", self)
        tb.setMovable(False)
        self.addToolBar(tb)

        refresh_act = QAction("Refresh", self)
        refresh_act.triggered.connect(lambda: self.refresh_snapshot(force_refresh=True))
        tb.addAction(refresh_act)

        tb.addSeparator()
        zoom_in_act = QAction("Zoom +", self)
        zoom_in_act.triggered.connect(self.view.zoom_in)
        zoom_out_act = QAction("Zoom -", self)
        zoom_out_act.triggered.connect(self.view.zoom_out)
        fit_act = QAction("Fit", self)
        fit_act.triggered.connect(self.view.fit_to_scene)
        tb.addActions([zoom_in_act, zoom_out_act, fit_act])

        tb.addSeparator()
        maint_act = QAction("Maintenance", self)
        maint_act.triggered.connect(self.open_maintenance)
        tb.addAction(maint_act)

        tb.addSeparator()
        map_lock_act = QAction("Lock Map", self)
        map_lock_act.setCheckable(True)
        map_lock_act.triggered.connect(self._toggle_map_lock)
        self.map_lock_action = map_lock_act
        tb.addAction(map_lock_act)

        tb.addSeparator()
        tb.addWidget(QLabel(" Poll (min): "))
        self.poll_spin = QSpinBox(self)
        self.poll_spin.setRange(1, 240)
        self.poll_spin.valueChanged.connect(self._update_poll_minutes)
        tb.addWidget(self.poll_spin)

    def _setup_status_table(self) -> None:
        dock = QDockWidget("Status Table", self)
        dock.setObjectName("StatusTableDock")
        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(["Machine", "Status", "Override", "Last Good", "Reason"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        dock.setWidget(table)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.status_table = table
    def refresh_snapshot(self, force_refresh: bool = False, initial: bool = False) -> None:
        try:
            if force_refresh:
                self.client.trigger_refresh()
            snapshot = self.client.fetch_snapshot()
        except Exception as exc:
            self.status_bar.showMessage(f"Failed to contact server: {exc}", 8000)
            return

        config_payload = snapshot.get("config", {})
        self.cfg = AppConfig.from_dict(config_payload)
        self.status_snapshot = snapshot.get("boxes", {})

        self.map_lock_action.blockSignals(True)
        self.map_lock_action.setChecked(bool(self.cfg.map_locked))
        self.map_lock_action.blockSignals(False)
        self.view.set_view_locked(bool(self.cfg.map_locked))

        poll_value = max(1, int(getattr(self.cfg, "poll_minutes", 5) or 5))
        if self.poll_spin.value() != poll_value:
            self.poll_spin.blockSignals(True)
            self.poll_spin.setValue(poll_value)
            self.poll_spin.blockSignals(False)
        interval_ms = max(1, poll_value) * 60_000
        if not self.refresh_timer.isActive():
            self.refresh_timer.start(interval_ms)
        else:
            self.refresh_timer.setInterval(interval_ms)

        self._sync_boxes()
        self._update_status_table()
        generated = snapshot.get("generated_at", datetime.utcnow().isoformat())
        self.status_bar.showMessage(f"Snapshot updated at {generated}", 6000)

        if initial:
            try:
                cx, cy = self.cfg.view_center
                self.view.apply_view_state(cx, cy, getattr(self.cfg, "view_zoom", 1.0))
            except Exception:
                pass

    def _sync_boxes(self) -> None:
        current_ids = set(self.box_items.keys())
        new_ids = {box.uid for box in self.cfg.boxes}

        for uid in current_ids - new_ids:
            item = self.box_items.pop(uid)
            self.scene.removeItem(item)

        for box in self.cfg.boxes:
            item = self.box_items.get(box.uid)
            if not item:
                item = MachineItem(self, box)
                self.box_items[box.uid] = item
                self.scene.addItem(item)
            else:
                item.update_geometry(box)
            status_payload = self.status_snapshot.get(box.uid, {})
            item.update_status(status_payload)

    def _update_status_table(self) -> None:
        table = self.status_table
        table.setRowCount(0)
        for box in self.cfg.boxes:
            status_payload = self.status_snapshot.get(box.uid, {})
            row = table.rowCount()
            table.insertRow(row)
            title_item = QTableWidgetItem(box.title)
            title_item.setData(Qt.UserRole, box.uid)
            table.setItem(row, 0, title_item)
            table.setItem(row, 1, QTableWidgetItem(status_payload.get("status", STATUS_UNKNOWN)))
            table.setItem(row, 2, QTableWidgetItem(status_payload.get("manual_override", "") or "-"))
            table.setItem(row, 3, QTableWidgetItem(status_payload.get("last_good_qc", "-") or "-"))
            reason = status_payload.get("reason", "")
            reason_item = QTableWidgetItem(reason)
            reason_item.setToolTip(reason)
            table.setItem(row, 4, reason_item)
    def persist_layout_change(self, box: BoxConfig) -> None:
        try:
            payload = {
                "pos": list(box.pos),
                "size": list(box.size),
                "locked": bool(getattr(box, "locked", False)),
            }
            self.client.update_box_layout(box.uid, payload)
        except Exception as exc:
            self.status_bar.showMessage(f"Failed to save layout: {exc}", 8000)

    def toggle_box_lock(self, box: BoxConfig) -> None:
        box.locked = not bool(getattr(box, "locked", False))
        self.persist_layout_change(box)
        self.box_items[box.uid]._update_movable_state()

    def _toggle_map_lock(self, checked: bool) -> None:
        self.cfg.map_locked = bool(checked)
        self.view.set_view_locked(self.cfg.map_locked)
        for item in self.box_items.values():
            item._update_movable_state()
        self.save_config()

    def _update_poll_minutes(self, value: int) -> None:
        self.cfg.poll_minutes = max(1, int(value))
        try:
            self.client.update_config({"poll_minutes": self.cfg.poll_minutes})
            self.status_bar.showMessage(f"Poll interval set to {self.cfg.poll_minutes} minute(s)", 6000)
        except Exception as exc:
            QMessageBox.warning(self, "Poll Interval", f"Failed to update poll minutes: {exc}")

    def save_config(self) -> None:
        try:
            center_x, center_y, zoom = self.view.get_view_state()
        except Exception:
            center_x = center_y = 0.0
            zoom = 1.0
        self.cfg.view_center = (center_x, center_y)
        self.cfg.view_zoom = zoom
        try:
            self.client.update_config({
                "view_center": [center_x, center_y],
                "view_zoom": zoom,
                "map_locked": bool(self.cfg.map_locked),
                "poll_minutes": int(self.cfg.poll_minutes or 5),
            })
        except Exception as exc:
            self.status_bar.showMessage(f"Failed to persist view: {exc}", 6000)
    def prompt_manual_override(self, box: BoxConfig, mode: str) -> None:
        existing = self.status_snapshot.get(box.uid, {})
        already = existing.get("manual_override") == mode
        action_text = "Disable" if already else "Enable"
        dlg = QDialog(self)
        dlg.setWindowTitle("Manual Override")
        form = QFormLayout(dlg)
        user_edit = QLineEdit(dlg)
        note_edit = QTextEdit(dlg)
        note_edit.setFixedHeight(80)
        form.addRow("Action", QLabel(f"{action_text} {mode}"))
        form.addRow("Name", user_edit)
        form.addRow("Comment", note_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dlg)
        buttons.button(QDialogButtonBox.Ok).setText("Apply")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec_() != QDialog.Accepted:
            return
        user = user_edit.text().strip()
        note = note_edit.toPlainText().strip()
        if not user or not note:
            QMessageBox.warning(self, "Manual Override", "Name and comment are required.")
            return
        try:
            if already:
                self.client.clear_override(box.uid, user, note)
            else:
                self.client.set_override(box.uid, mode, user, note)
            self.refresh_snapshot()
        except Exception as exc:
            QMessageBox.warning(self, "Manual Override", f"Override failed: {exc}")

    def clear_manual_override(self, box: BoxConfig) -> None:
        user, ok = QInputDialog.getText(self, "Clear Override", "Technician:")
        if not ok:
            return
        note, ok = QInputDialog.getMultiLineText(self, "Clear Override", "Comment:")
        if not ok or not note.strip():
            QMessageBox.warning(self, "Clear Override", "Comment is required.")
            return
        try:
            self.client.clear_override(box.uid, user, note)
            self.refresh_snapshot()
        except Exception as exc:
            QMessageBox.warning(self, "Clear Override", f"Failed to clear override: {exc}")

    def open_maintenance(self) -> None:
        dlg = MaintenanceDialog(self.client, self)
        dlg.exec_()

    def show_box_details(self, uid: str) -> None:
        box = next((b for b in self.cfg.boxes if b.uid == uid), None)
        if not box:
            QMessageBox.warning(self, "Details", "Box not found in snapshot.")
            return
        payload = self.status_snapshot.get(uid, {})
        dlg = BoxInfoDialog(box, payload, self)
        dlg.exec_()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        try:
            self.save_config()
        except Exception:
            pass
        try:
            self.refresh_timer.stop()
        except Exception:
            pass
        try:
            self.client.close()
        except Exception:
            pass
        super().closeEvent(event)


__all__ = ["MainWindow"]
