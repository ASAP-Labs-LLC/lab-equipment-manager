#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lab Manager Map — visual QC watcher for lab equipment

What it does
============
• Place movable boxes on a canvas, each representing a machine.
• Each box watches a CSV file every N minutes (default 5).
• For each box, you define:
    - CSV path
    - Sample ID column (e.g., "Lab ID") and the specific ID to watch (e.g., "AO24")
    - One or more parameters to evaluate (e.g., Flash Point, API, Bio), each with:
        expected value, std deviation, and k (multiplier; default 2) → tolerance = k*σ
    - QC expiry time (hours). If the **last good (in-spec)** QC is older than this, box turns YELLOW.
      (Red always overrides yellow; only an in-spec machine can become yellow from staleness.)
    - Optional timestamp column in CSV; if missing, file mtime is used.
• Status logic:
    - GREEN  = all watched parameters are within expected ± k·σ
    - RED    = any watched parameter is out of spec
    - YELLOW = last good QC older than expiry (hours). Only applies if last known state was GREEN.
    - DEAD-LINE (manual override) = black box with red text
    - SERVICE  (manual override)   = light gray box with dark gray text
• Right-click a box → Info (shows expectations vs latest), Edit, Lock, Manual overrides, Remove
• Lock the whole map (or each box) to prevent moving/editing
• List view mode: see all boxes in a sortable table with status & last QC
• Auto-saves layout and settings to JSON (lab_map_config.json) in the script’s folder

Notes & assumptions
===================
• CSV must have headers. Matching of columns is case-insensitive.
• For timestamp: If you specify a timestamp column in the box settings, the app will parse
  the latest row by that column. Otherwise, it uses the last matching row in the file, and
  timestamps “last QC” using the file’s mtime as a fallback.
• “None of those should change unless a value comes in…” vs “turn yellow after 1 day”:
  This implementation follows the lab’s general rule you’ve used elsewhere:
  - Only an in-spec machine can go to YELLOW due to time staleness.
  - An out-of-spec machine stays RED until a new, in-spec QC arrives.

Author: you + ChatGPT (GPT-5 Thinking)
"""

import csv
import json
import os
import sys
import traceback
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import (
    Qt, QTimer, QPointF, QRectF, QSize, QDateTime, QThread, pyqtSignal, QObject
)
from PyQt5.QtGui import (
    QBrush, QColor, QPen, QFont
)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QGraphicsView, QGraphicsScene, QGraphicsRectItem,
    QGraphicsTextItem, QFileDialog, QAction, QToolBar, QMessageBox, QDialog,
    QFormLayout, QLineEdit, QDoubleSpinBox, QSpinBox, QPushButton, QHBoxLayout,
    QVBoxLayout, QLabel, QMenu, QCheckBox, QWidget, QStackedWidget, QTableWidget,
    QTableWidgetItem, QHeaderView, QComboBox
)

APP_TITLE = "Lab Manager Map"

CONFIG_FILE = "lab_map_config.json"
DEFAULT_TIMER_MINUTES = 5

# Colors
COLOR_GREEN = QColor(46, 204, 113)      # green
COLOR_RED = QColor(231, 76, 60)         # red
COLOR_YELLOW = QColor(241, 196, 15)     # yellow
COLOR_BLACK = QColor(0, 0, 0)           # deadline override
COLOR_BLACK_TEXT = QColor(220, 20, 60)  # crimson on black
COLOR_SERVICE_BG = QColor(230, 230, 230) # light gray
COLOR_SERVICE_TEXT = QColor(60, 60, 60)  # dark gray
COLOR_TEXT_DEFAULT = QColor(30, 30, 30)
COLOR_TEXT_WHITE = QColor(255, 255, 255)
COLOR_BORDER = QColor(33, 33, 33)

# Status constants
STATUS_GREEN = "GREEN"
STATUS_RED = "RED"
STATUS_YELLOW = "YELLOW"
STATUS_DEAD = "DEAD-LINE"
STATUS_SERVICE = "SERVICE"
STATUS_UNKNOWN = "UNKNOWN"

# Small helpers
def now_utc() -> datetime:
    return datetime.utcnow()

def parse_float(s) -> Optional[float]:
    try:
        return float(str(s).strip())
    except Exception:
        return None

def human_tdelta(td: timedelta) -> str:
    if td.total_seconds() < 0:
        td = -td
        prefix = "-"
    else:
        prefix = ""
    days = td.days
    secs = td.seconds
    hrs = secs // 3600
    mins = (secs % 3600) // 60
    if days > 0:
        return f"{prefix}{days}d {hrs}h"
    if hrs > 0:
        return f"{prefix}{hrs}h {mins}m"
    return f"{prefix}{mins}m"

# Data classes
@dataclass
class ParameterRule:
    name: str                     # Column name in CSV (case-insensitive match)
    expected: float
    std_dev: float
    k: float = 2.0
    units: str = ""

    def serialize(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ParameterRule":
        return ParameterRule(
            name=d.get("name", ""),
            expected=float(d.get("expected", 0.0)),
            std_dev=float(d.get("std_dev", 0.0)),
            k=float(d.get("k", 2.0)),
            units=str(d.get("units", "")),
        )

@dataclass
class BoxConfig:
    uid: str
    title: str
    csv_path: str
    sample_id_col: str
    sample_id_value: str
    parameters: List[ParameterRule] = field(default_factory=list)
    qc_expire_hours: float = 24.0
    timestamp_col: str = ""          # optional column name
    pos: Tuple[float, float] = (0.0, 0.0)
    size: Tuple[float, float] = (220.0, 120.0)
    locked: bool = False
    manual_override: str = ""        # "", "DEAD-LINE", "SERVICE"

    def serialize(self) -> dict:
        return {
            "uid": self.uid,
            "title": self.title,
            "csv_path": self.csv_path,
            "sample_id_col": self.sample_id_col,
            "sample_id_value": self.sample_id_value,
            "parameters": [p.serialize() for p in self.parameters],
            "qc_expire_hours": self.qc_expire_hours,
            "timestamp_col": self.timestamp_col,
            "pos": list(self.pos),
            "size": list(self.size),
            "locked": self.locked,
            "manual_override": self.manual_override,
        }

    @staticmethod
    def from_dict(d: dict) -> "BoxConfig":
        return BoxConfig(
            uid=d.get("uid", ""),
            title=d.get("title", "Machine"),
            csv_path=d.get("csv_path", ""),
            sample_id_col=d.get("sample_id_col", "Lab ID"),
            sample_id_value=d.get("sample_id_value", ""),
            parameters=[ParameterRule.from_dict(p) for p in d.get("parameters", [])],
            qc_expire_hours=float(d.get("qc_expire_hours", 24.0)),
            timestamp_col=d.get("timestamp_col", ""),
            pos=tuple(d.get("pos", [0.0, 0.0])),
            size=tuple(d.get("size", [220.0, 120.0])),
            locked=bool(d.get("locked", False)),
            manual_override=str(d.get("manual_override", "")),
        )

@dataclass
class ParameterResult:
    rule: ParameterRule
    latest_value: Optional[float]
    in_spec: Optional[bool]
    low: Optional[float]
    high: Optional[float]
    note: str = ""

@dataclass
class BoxEvaluation:
    status: str
    results: List[ParameterResult]
    last_qc_time: Optional[datetime]
    latest_row_time: Optional[datetime]
    reason: str = ""

# CSV reading worker (threaded)
class CsvReadWorker(QObject):
    finished = pyqtSignal(dict)  # path -> list(rows)
    error = pyqtSignal(str, str) # path, message

    def __init__(self, paths: List[str]):
        super().__init__()
        self.paths = paths

    def run(self):
        out: Dict[str, List[dict]] = {}
        for path in self.paths:
            try:
                rows = []
                if not os.path.exists(path):
                    out[path] = []
                    continue
                with open(path, "r", newline="", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        rows.append(row)
                out[path] = rows
            except Exception as e:
                self.error.emit(path, f"{type(e).__name__}: {e}")
        self.finished.emit(out)

def _ci_lookup(row: dict, key: str) -> Optional[str]:
    """Case-insensitive lookup in a CSV row by header name."""
    if not key:
        return None
    lk = key.strip().lower()
    for k, v in row.items():
        if k.strip().lower() == lk:
            return v
    return None

def _best_timestamp_for_row(row: dict, tcol: str, path: str) -> datetime:
    # Try explicit timestamp column (parse common formats)
    if tcol:
        sval = _ci_lookup(row, tcol)
        if sval:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M", "%Y-%m-%dT%H:%M:%S", "%d-%b-%Y %H:%M:%S"):
                try:
                    return datetime.strptime(sval.strip(), fmt)
                except Exception:
                    pass
    # Fallback: file mtime (approximate)
    try:
        mtime = os.path.getmtime(path)
        return datetime.utcfromtimestamp(mtime)
    except Exception:
        return now_utc()

def evaluate_box_from_rows(
    box: BoxConfig, rows: List[dict]
) -> BoxEvaluation:
    """
    Evaluate a single box against provided CSV rows for that file.
    latest matching row (by timestamp col if present, else last occurrence in file).
    """
    results: List[ParameterResult] = []
    reason = ""
    status = STATUS_UNKNOWN
    latest_row_time: Optional[datetime] = None
    last_good_qc: Optional[datetime] = None

    # Find matching rows by sample ID
    sid_col = box.sample_id_col.strip()
    sid_val = str(box.sample_id_value).strip()

    matches: List[Tuple[dict, datetime]] = []
    for r in rows:
        v = _ci_lookup(r, sid_col)
        if v is not None and str(v).strip() == sid_val:
            ts = _best_timestamp_for_row(r, box.timestamp_col, box.csv_path)
            matches.append((r, ts))

    if not matches:
        # No new row for this QC sample; compute staleness based on last_good_qc (unknown here)
        # With no persisted state, we can't compute a fresh YELLOW; mark UNKNOWN and explain.
        return BoxEvaluation(
            status=STATUS_YELLOW if False else STATUS_UNKNOWN,
            results=[],
            last_qc_time=None,
            latest_row_time=None,
            reason="No matching rows found for sample ID; unable to evaluate until data arrives."
        )

    # Choose latest by timestamp
    matches.sort(key=lambda x: x[1])
    latest_row, latest_row_time = matches[-1]

    # Evaluate each parameter
    any_fail = False
    all_have_values = True
    for rule in box.parameters:
        raw = _ci_lookup(latest_row, rule.name)
        val = parse_float(raw)
        low = rule.expected - rule.k * rule.std_dev
        high = rule.expected + rule.k * rule.std_dev

        if val is None:
            all_have_values = False
            results.append(ParameterResult(rule=rule, latest_value=None,
                                           in_spec=None, low=low, high=high,
                                           note=f"No numeric value in column “{rule.name}”."))
            continue

        in_spec = (low <= val <= high)
        if not in_spec:
            any_fail = True
        results.append(ParameterResult(rule=rule, latest_value=val, in_spec=in_spec, low=low, high=high))

    # Compute base status (ignoring staleness + overrides first)
    if any_fail:
        status = STATUS_RED
        reason = "At least one parameter is out of spec."
        last_good_qc = None
    else:
        status = STATUS_GREEN
        reason = "All parameters within expected ranges."
        last_good_qc = latest_row_time

    # Determine staleness → YELLOW only if currently GREEN and expired
    if status == STATUS_GREEN and last_good_qc is not None:
        age = now_utc() - last_good_qc
        if age > timedelta(hours=box.qc_expire_hours):
            status = STATUS_YELLOW
            reason = f"Last in-spec QC is stale: {human_tdelta(age)} old (expiry {box.qc_expire_hours:.0f}h)."

    return BoxEvaluation(
        status=status,
        results=results,
        last_qc_time=last_good_qc,
        latest_row_time=latest_row_time,
        reason=reason
    )

# Graphics item for a machine box
class MachineBoxItem(QGraphicsRectItem):
    def __init__(self, box: BoxConfig):
        super().__init__()
        self.box = box
        self.setRect(QRectF(0, 0, box.size[0], box.size[1]))
        self.setPos(QPointF(box.pos[0], box.pos[1]))
        self.setPen(QPen(COLOR_BORDER, 2))
        self.setFlags(
            QGraphicsRectItem.ItemIsSelectable |
            QGraphicsRectItem.ItemSendsGeometryChanges
        )
        # movement flag set according to locked
        self.set_movable(not box.locked)

        # Title text
        self.titleItem = QGraphicsTextItem(self)
        f = QFont()
        f.setPointSize(10)
        f.setBold(True)
        self.titleItem.setFont(f)
        self.titleItem.setDefaultTextColor(COLOR_TEXT_DEFAULT)

        # Subtext for status
        self.subItem = QGraphicsTextItem(self)
        f2 = QFont()
        f2.setPointSize(9)
        self.subItem.setFont(f2)
        self.subItem.setDefaultTextColor(COLOR_TEXT_DEFAULT)

        self.status = STATUS_UNKNOWN
        self.status_reason = ""
        self.latest_info_lines: List[str] = []

        self.refresh_text_layout()
        self.apply_visuals()

    def set_movable(self, movable: bool):
        self.setFlag(QGraphicsRectItem.ItemIsMovable, movable)

    def update_size(self, w: float, h: float):
        self.setRect(QRectF(0, 0, w, h))
        self.refresh_text_layout()

    def refresh_text_layout(self):
        rect = self.rect()
        padding = 6
        self.titleItem.setPlainText(self.box.title)
        self.titleItem.setPos(padding, padding)

        self.subItem.setPlainText("\n".join(self.latest_info_lines))
        self.subItem.setPos(padding, padding + 20)

    def set_status(self, status: str, reason: str, info_lines: List[str]):
        self.status = status
        self.status_reason = reason
        self.latest_info_lines = info_lines
        self.refresh_text_layout()
        self.apply_visuals()

    def apply_visuals(self):
        # Manual overrides first
        if self.box.manual_override == STATUS_DEAD:
            self.setBrush(QBrush(COLOR_BLACK))
            self.titleItem.setDefaultTextColor(COLOR_BLACK_TEXT)
            self.subItem.setDefaultTextColor(COLOR_BLACK_TEXT)
            return
        if self.box.manual_override == STATUS_SERVICE:
            self.setBrush(QBrush(COLOR_SERVICE_BG))
            self.titleItem.setDefaultTextColor(COLOR_SERVICE_TEXT)
            self.subItem.setDefaultTextColor(COLOR_SERVICE_TEXT)
            return

        # Computed status
        if self.status == STATUS_GREEN:
            self.setBrush(QBrush(COLOR_GREEN))
            self.titleItem.setDefaultTextColor(COLOR_TEXT_WHITE)
            self.subItem.setDefaultTextColor(COLOR_TEXT_WHITE)
        elif self.status == STATUS_RED:
            self.setBrush(QBrush(COLOR_RED))
            self.titleItem.setDefaultTextColor(COLOR_TEXT_WHITE)
            self.subItem.setDefaultTextColor(COLOR_TEXT_WHITE)
        elif self.status == STATUS_YELLOW:
            self.setBrush(QBrush(COLOR_YELLOW))
            self.titleItem.setDefaultTextColor(COLOR_TEXT_DEFAULT)
            self.subItem.setDefaultTextColor(COLOR_TEXT_DEFAULT)
        else:
            # unknown
            self.setBrush(QBrush(QColor(200, 200, 200)))
            self.titleItem.setDefaultTextColor(COLOR_TEXT_DEFAULT)
            self.subItem.setDefaultTextColor(COLOR_TEXT_DEFAULT)

    def contextMenuEvent(self, event):
        menu = QMenu()
        info_act = menu.addAction("Info…")
        edit_act = menu.addAction("Edit Box…")
        lock_act = menu.addAction("Lock" if not self.box.locked else "Unlock")
        menu.addSeparator()
        override_menu = menu.addMenu("Manual Override")
        off_act = override_menu.addAction("Off")
        dead_act = override_menu.addAction("DEAD-LINE")
        serv_act = override_menu.addAction("SERVICE")
        menu.addSeparator()
        remove_act = menu.addAction("Remove")

        chosen = menu.exec_(event.screenPos())
        if chosen is None:
            return

        if chosen == info_act:
            self.scene().parent_window.show_box_info(self.box, self.status, self.status_reason, self.latest_info_lines)
        elif chosen == edit_act:
            self.scene().parent_window.edit_box(self.box)
        elif chosen == lock_act:
            self.box.locked = not self.box.locked
            self.set_movable(not self.box.locked)
            self.scene().parent_window.save_config()
        elif chosen == remove_act:
            if QMessageBox.question(None, "Remove Box", f"Remove '{self.box.title}'?") == QMessageBox.Yes:
                self.scene().parent_window.remove_box(self.box.uid)
        elif chosen in (off_act, dead_act, serv_act):
            if chosen == off_act:
                self.box.manual_override = ""
            elif chosen == dead_act:
                self.box.manual_override = STATUS_DEAD
            else:
                self.box.manual_override = STATUS_SERVICE
            self.apply_visuals()
            self.scene().parent_window.save_config()

    def itemChange(self, change, value):
        if change == QGraphicsRectItem.ItemPositionHasChanged:
            # persist position
            self.box.pos = (self.pos().x(), self.pos().y())
            if hasattr(self.scene(), "parent_window"):
                self.scene().parent_window.save_config()
        return super().itemChange(change, value)

class MachineScene(QGraphicsScene):
    def __init__(self, parent_window):
        super().__init__()
        self.parent_window = parent_window
        self.setBackgroundBrush(QBrush(QColor(245, 245, 245)))

# Editor dialog for a box
class BoxEditor(QDialog):
    def __init__(self, parent, box: Optional[BoxConfig] = None):
        super().__init__(parent)
        self.setWindowTitle("Box Settings")
        self.setMinimumWidth(520)
        self.box = box

        form = QFormLayout()

        self.title_edit = QLineEdit(box.title if box else "")
        form.addRow("Title:", self.title_edit)

        # CSV path
        h = QHBoxLayout()
        self.csv_edit = QLineEdit(box.csv_path if box else "")
        btn = QPushButton("Browse…")
        btn.clicked.connect(self._browse_csv)
        h.addWidget(self.csv_edit)
        h.addWidget(btn)
        form.addRow("CSV Path:", h)

        self.sample_col = QLineEdit(box.sample_id_col if box else "Lab ID")
        self.sample_val = QLineEdit(box.sample_id_value if box else "")
        form.addRow("Sample ID Column:", self.sample_col)
        form.addRow("Sample ID Value:", self.sample_val)

        self.tcol_edit = QLineEdit(box.timestamp_col if box else "")
        form.addRow("Timestamp Column (optional):", self.tcol_edit)

        self.qc_hours = QDoubleSpinBox()
        self.qc_hours.setDecimals(1)
        self.qc_hours.setRange(0.5, 9999.0)
        self.qc_hours.setValue(box.qc_expire_hours if box else 24.0)
        form.addRow("QC Expiry (hours):", self.qc_hours)

        # Parameter editor (simple rows)
        self.param_container = QVBoxLayout()
        form.addRow(QLabel("Watched Parameters:"))
        self._param_rows: List[Tuple[QLineEdit, QDoubleSpinBox, QDoubleSpinBox, QDoubleSpinBox, QLineEdit]] = []

        param_buttons = QHBoxLayout()
        addp = QPushButton("Add Parameter")
        addp.clicked.connect(self._add_param_row)
        param_buttons.addWidget(addp)
        form.addRow(param_buttons)
        form.addRow(self._param_container_widget())

        if box and box.parameters:
            for p in box.parameters:
                self._add_param_row(p)
        else:
            # one starter row
            self._add_param_row()

        # Width/Height
        self.width_spin = QSpinBox()
        self.height_spin = QSpinBox()
        self.width_spin.setRange(120, 800)
        self.height_spin.setRange(80, 600)
        if box:
            self.width_spin.setValue(int(box.size[0]))
            self.height_spin.setValue(int(box.size[1]))
        else:
            self.width_spin.setValue(220)
            self.height_spin.setValue(120)
        form.addRow("Box Width:", self.width_spin)
        form.addRow("Box Height:", self.height_spin)

        # Locked
        self.locked_chk = QCheckBox("Locked")
        if box:
            self.locked_chk.setChecked(box.locked)
        form.addRow(self.locked_chk)

        btns = QHBoxLayout()
        ok = QPushButton("OK")
        cancel = QPushButton("Cancel")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        btns.addStretch(1)
        btns.addWidget(ok)
        btns.addWidget(cancel)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addLayout(btns)
        self.setLayout(layout)

    def _param_container_widget(self) -> QWidget:
        w = QWidget()
        w.setLayout(self.param_container)
        return w

    def _add_param_row(self, proto: Optional[ParameterRule] = None):
        row = QHBoxLayout()
        name = QLineEdit(proto.name if proto else "")
        name.setPlaceholderText("Column name (e.g., Flash Point)")
        expected = QDoubleSpinBox()
        expected.setDecimals(6)
        expected.setRange(-1e9, 1e9)
        expected.setValue(proto.expected if proto else 0.0)
        std = QDoubleSpinBox()
        std.setDecimals(6)
        std.setRange(0.0, 1e9)
        std.setValue(proto.std_dev if proto else 0.5)
        k = QDoubleSpinBox()
        k.setDecimals(3)
        k.setRange(0.1, 1000.0)
        k.setValue(proto.k if proto else 2.0)
        units = QLineEdit(proto.units if proto else "")
        units.setPlaceholderText("Units (optional)")

        rm = QPushButton("✖")
        rm.setToolTip("Remove parameter")
        def do_rm():
            # remove row
            for i, row_tuple in enumerate(self._param_rows):
                if row_tuple[0] is name:
                    self._param_rows.pop(i)
                    # delete widgets
                    for w in (name, expected, std, k, units, rm):
                        w.deleteLater()
                    self.param_container.removeItem(row)
                    break
        rm.clicked.connect(do_rm)

        # labels inline
        row.addWidget(QLabel("Name:"))
        row.addWidget(name, 2)
        row.addWidget(QLabel("Expected:"))
        row.addWidget(expected, 1)
        row.addWidget(QLabel("σ:"))
        row.addWidget(std, 1)
        row.addWidget(QLabel("k:"))
        row.addWidget(k, 1)
        row.addWidget(QLabel("Units:"))
        row.addWidget(units, 1)
        row.addWidget(rm)

        self.param_container.addLayout(row)
        self._param_rows.append((name, expected, std, k, units))

    def _browse_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select CSV", "", "CSV Files (*.csv);;All Files (*.*)")
        if path:
            self.csv_edit.setText(path)

    def get_box(self, existing_uid: Optional[str]) -> Optional[BoxConfig]:
        title = self.title_edit.text().strip() or "Machine"
        csv_path = self.csv_edit.text().strip()
        sample_col = self.sample_col.text().strip() or "Lab ID"
        sample_val = self.sample_val.text().strip()
        tcol = self.tcol_edit.text().strip()
        qc_hours = float(self.qc_hours.value())
        width = float(self.width_spin.value())
        height = float(self.height_spin.value())
        locked = self.locked_chk.isChecked()

        if not csv_path:
            QMessageBox.warning(self, "Missing CSV", "Please choose a CSV file path.")
            return None
        if not sample_val:
            QMessageBox.warning(self, "Missing Sample ID", "Please provide a Sample ID value to watch (e.g., AO24).")
            return None

        params: List[ParameterRule] = []
        for (name, expected, std, k, units) in self._param_rows:
            n = name.text().strip()
            if not n:
                continue
            pr = ParameterRule(
                name=n,
                expected=float(expected.value()),
                std_dev=float(std.value()),
                k=float(k.value()),
                units=units.text().strip(),
            )
            params.append(pr)
        if not params:
            QMessageBox.warning(self, "No Parameters", "Please add at least one watched parameter.")
            return None

        uid = existing_uid or f"box_{int(datetime.utcnow().timestamp()*1000)}"
        if self.box:
            pos = self.box.pos
        else:
            pos = (20.0, 20.0)

        return BoxConfig(
            uid=uid,
            title=title,
            csv_path=csv_path,
            sample_id_col=sample_col,
            sample_id_value=sample_val,
            parameters=params,
            qc_expire_hours=qc_hours,
            timestamp_col=tcol,
            pos=pos,
            size=(width, height),
            locked=locked,
            manual_override=self.box.manual_override if self.box else ""
        )

class InfoDialog(QDialog):
    def __init__(self, parent, box: BoxConfig, eval: BoxEvaluation):
        super().__init__(parent)
        self.setWindowTitle(f"Info — {box.title}")
        self.setMinimumWidth(580)
        layout = QVBoxLayout()

        h = QHBoxLayout()
        h.addWidget(QLabel(f"<b>Title:</b> {box.title}"), 3)
        h.addWidget(QLabel(f"<b>Status:</b> {eval.status}"), 1)
        layout.addLayout(h)

        layout.addWidget(QLabel(f"<b>CSV:</b> {box.csv_path}"))
        layout.addWidget(QLabel(f"<b>Sample:</b> {box.sample_id_col} = {box.sample_id_value}"))
        if box.timestamp_col:
            layout.addWidget(QLabel(f"<b>Timestamp column:</b> {box.timestamp_col}"))

        last_qc = eval.last_qc_time.isoformat(sep=' ') if eval.last_qc_time else "—"
        latest_row_t = eval.latest_row_time.isoformat(sep=' ') if eval.latest_row_time else "—"
        layout.addWidget(QLabel(f"<b>Last in-spec QC time:</b> {last_qc}"))
        layout.addWidget(QLabel(f"<b>Latest matching row time:</b> {latest_row_t}"))
        layout.addWidget(QLabel(f"<b>Reason:</b> {eval.reason or '—'}"))

        layout.addWidget(QLabel("<b>Parameters:</b>"))
        table = QTableWidget(0, 6)
        table.setHorizontalHeaderLabels(["Name", "Expected", "±k·σ", "Range", "Latest", "In Spec"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for pr in eval.results:
            row = table.rowCount()
            table.insertRow(row)
            units = f" {pr.rule.units}" if pr.rule.units else ""
            tol = pr.rule.k * pr.rule.std_dev if pr.rule.std_dev is not None else None
            table.setItem(row, 0, QTableWidgetItem(pr.rule.name))
            table.setItem(row, 1, QTableWidgetItem(f"{pr.rule.expected}{units}"))
            table.setItem(row, 2, QTableWidgetItem(f"{tol:.6g}" if tol is not None else "—"))
            rng = f"[{pr.low:.6g}, {pr.high:.6g}]" if pr.low is not None and pr.high is not None else "—"
            table.setItem(row, 3, QTableWidgetItem(rng))
            latest = "—" if pr.latest_value is None else f"{pr.latest_value:.6g}{units}"
            table.setItem(row, 4, QTableWidgetItem(latest))
            ins = "—" if pr.in_spec is None else ("YES" if pr.in_spec else "NO")
            table.setItem(row, 5, QTableWidgetItem(ins))
        layout.addWidget(table)

        hb = QHBoxLayout()
        open_csv = QPushButton("Open CSV")
        open_dir = QPushButton("Open Folder")
        close = QPushButton("Close")
        open_csv.clicked.connect(lambda: self._open_path(box.csv_path))
        open_dir.clicked.connect(lambda: self._open_path(os.path.dirname(box.csv_path)))
        close.clicked.connect(self.accept)
        hb.addStretch(1)
        hb.addWidget(open_csv)
        hb.addWidget(open_dir)
        hb.addWidget(close)
        layout.addLayout(hb)
        self.setLayout(layout)

    def _open_path(self, path: str):
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore
            elif sys.platform == "darwin":
                os.system(f'open "{path}"')
            else:
                os.system(f'xdg-open "{path}"')
        except Exception as e:
            QMessageBox.warning(self, "Open failed", f"Could not open:\n{path}\n\n{e}")

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1100, 700)

        self.boxes: Dict[str, BoxConfig] = {}
        self.items: Dict[str, MachineBoxItem] = {}
        self.poll_minutes = DEFAULT_TIMER_MINUTES
        self.map_locked = False

        self._load_config()

        # UI
        self.toolbar = QToolBar("Main")
        self.addToolBar(self.toolbar)
        self._setup_actions()

        self.stack = QStackedWidget()
        self.scene = MachineScene(self)
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHints(self.view.renderHints())
        self.stack.addWidget(self.view)

        # List view
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Title", "Status", "Override", "Last QC", "Expires In", "CSV", "Watched"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.stack.addWidget(self.table)

        self.setCentralWidget(self.stack)

        # Populate existing boxes
        for uid, box in self.boxes.items():
            self._add_box_item(box)

        # Timer
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_all)
        self._apply_poll_minutes(self.poll_minutes)

        # Initial refresh
        QTimer.singleShot(200, self.refresh_all)

    def _setup_actions(self):
        add_act = QAction("Add Box", self)
        add_act.triggered.connect(self.add_box)
        self.toolbar.addAction(add_act)

        refresh_act = QAction("Refresh Now", self)
        refresh_act.triggered.connect(self.refresh_all)
        self.toolbar.addAction(refresh_act)

        self.toolbar.addSeparator()

        self.lock_map_act = QAction("Lock Map", self, checkable=True)
        self.lock_map_act.setChecked(self.map_locked)
        self.lock_map_act.toggled.connect(self._toggle_map_lock)
        self.toolbar.addAction(self.lock_map_act)

        self.view_mode_cb = QComboBox()
        self.view_mode_cb.addItems(["Map", "List"])
        self.view_mode_cb.currentIndexChanged.connect(self._switch_view)
        self.toolbar.addWidget(QLabel(" View: "))
        self.toolbar.addWidget(self.view_mode_cb)

        self.toolbar.addSeparator()

        self.poll_spin = QSpinBox()
        self.poll_spin.setRange(1, 120)
        self.poll_spin.setValue(self.poll_minutes)
        self.poll_spin.valueChanged.connect(self._change_poll_minutes)
        self.toolbar.addWidget(QLabel(" Poll (min): "))
        self.toolbar.addWidget(self.poll_spin)

        self.toolbar.addSeparator()

        save_act = QAction("Save Layout", self)
        save_act.triggered.connect(self.save_config)
        self.toolbar.addAction(save_act)

    def _switch_view(self, idx: int):
        self.stack.setCurrentIndex(idx)
        if idx == 1:
            self._refresh_table()

    def _toggle_map_lock(self, locked: bool):
        self.map_locked = locked
        # Additionally restrict movement of items when map is locked (but individual lock still applies)
        for item in self.items.values():
            item.set_movable(not (self.map_locked or item.box.locked))

    def _change_poll_minutes(self, v: int):
        self.poll_minutes = int(v)
        self._apply_poll_minutes(self.poll_minutes)
        self.save_config()

    def _apply_poll_minutes(self, minutes: int):
        self.timer.stop()
        self.timer.start(minutes * 60 * 1000)

    # Config I/O
    def _default_config(self) -> dict:
        return {
            "version": 1,
            "poll_minutes": DEFAULT_TIMER_MINUTES,
            "map_locked": False,
            "boxes": []
        }

    def _load_config(self):
        cfg = self._default_config()
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                traceback.print_exc()

        self.poll_minutes = int(cfg.get("poll_minutes", DEFAULT_TIMER_MINUTES))
        self.map_locked = bool(cfg.get("map_locked", False))
        boxes = cfg.get("boxes", [])
        self.boxes = {}
        for b in boxes:
            box = BoxConfig.from_dict(b)
            self.boxes[box.uid] = box

    def save_config(self):
        cfg = {
            "version": 1,
            "poll_minutes": self.poll_minutes,
            "map_locked": self.map_locked,
            "boxes": [b.serialize() for b in self.boxes.values()]
        }
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "Save failed", f"Could not save config:\n{e}")

    # Box operations
    def _add_box_item(self, box: BoxConfig):
        item = MachineBoxItem(box)
        self.scene.addItem(item)
        self.items[box.uid] = item
        item.set_movable(not (self.map_locked or box.locked))

    def add_box(self):
        dlg = BoxEditor(self, None)
        if dlg.exec_() == QDialog.Accepted:
            new_box = dlg.get_box(existing_uid=None)
            if not new_box:
                return
            # Random-ish placement
            new_box.pos = (20 + 30 * (len(self.items) % 15), 20 + 30 * (len(self.items) % 10))
            self.boxes[new_box.uid] = new_box
            self._add_box_item(new_box)
            self.save_config()
            self.refresh_all()

    def edit_box(self, box: BoxConfig):
        dlg = BoxEditor(self, box)
        if dlg.exec_() == QDialog.Accepted:
            updated = dlg.get_box(existing_uid=box.uid)
            if not updated:
                return
            # keep position
            updated.pos = box.pos
            # keep override
            updated.manual_override = box.manual_override
            # replace
            self.boxes[box.uid] = updated
            # update item
            item = self.items[box.uid]
            item.box = updated
            item.update_size(updated.size[0], updated.size[1])
            item.set_movable(not (self.map_locked or updated.locked))
            item.refresh_text_layout()
            self.save_config()
            self.refresh_all()

    def remove_box(self, uid: str):
        item = self.items.get(uid)
        if item:
            self.scene.removeItem(item)
            del self.items[uid]
        if uid in self.boxes:
            del self.boxes[uid]
        self.save_config()
        self._refresh_table()

    # Info dialog
    def show_box_info(self, box: BoxConfig, status: str, reason: str, lines: List[str]):
        # We also compute a fresh eval to show full table
        rows_by_path = getattr(self, "_last_rows_cache", {})
        rows = rows_by_path.get(box.csv_path, [])
        eval = evaluate_box_from_rows(box, rows)
        # If override exists, force display status to override but keep computed table
        if box.manual_override == STATUS_DEAD:
            eval.status = STATUS_DEAD
            eval.reason = "Manual override: DEAD-LINE"
        elif box.manual_override == STATUS_SERVICE:
            eval.status = STATUS_SERVICE
            eval.reason = "Manual override: SERVICE"

        dlg = InfoDialog(self, box, eval)
        dlg.exec_()

    # Refresh logic
    def refresh_all(self):
        # Group CSV paths
        paths = sorted(set(b.csv_path for b in self.boxes.values() if b.csv_path))
        if not paths:
            return
        # Threaded read
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

    def _on_rows_error(self, path: str, msg: str):
        # Show once in status bar (non-blocking)
        self.statusBar().showMessage(f"CSV error for {os.path.basename(path)}: {msg}", 8000)

    def _on_rows_loaded(self, rows_by_path: Dict[str, List[dict]]):
        # cache for info dialogs
        self._last_rows_cache = rows_by_path

        for uid, box in self.boxes.items():
            rows = rows_by_path.get(box.csv_path, [])
            eval = evaluate_box_from_rows(box, rows)

            # Apply manual overrides
            if box.manual_override == STATUS_DEAD:
                status = STATUS_DEAD
                reason = "Manual override: DEAD-LINE"
            elif box.manual_override == STATUS_SERVICE:
                status = STATUS_SERVICE
                reason = "Manual override: SERVICE"
            else:
                status = eval.status
                reason = eval.reason

            # Build info lines (fits inside the box)
            lines: List[str] = []
            if eval.results:
                for pr in eval.results[:4]:  # show first up to 4 lines; full details in Info
                    tok = pr.rule.k * pr.rule.std_dev
                    rng = f"[{pr.low:.6g}, {pr.high:.6g}]" if pr.low is not None else "—"
                    vtxt = "—" if pr.latest_value is None else f"{pr.latest_value:.6g}"
                    flag = "" if pr.in_spec is None else ("✓" if pr.in_spec else "✗")
                    units = f" {pr.rule.units}" if pr.rule.units else ""
                    lines.append(f"{pr.rule.name}: {vtxt}{units} {flag}  tol±{tok:.6g}  {rng}")
                if len(eval.results) > 4:
                    lines.append(f"+{len(eval.results) - 4} more…")
            else:
                lines.append("(no parameters)")

            if eval.last_qc_time:
                age = now_utc() - eval.last_qc_time
                lines.append(f"Last QC: {human_tdelta(age)} ago")
            elif eval.latest_row_time:
                age = now_utc() - eval.latest_row_time
                lines.append(f"Last row: {human_tdelta(age)} ago")

            item = self.items.get(uid)
            if item:
                item.set_status(status, reason, lines)

        if self.stack.currentIndex() == 1:
            self._refresh_table()

    def _refresh_table(self):
        self.table.setRowCount(0)
        for box in self.boxes.values():
            item = self.items.get(box.uid)
            if not item:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)

            # Title
            self.table.setItem(row, 0, QTableWidgetItem(box.title))
            # Status
            self.table.setItem(row, 1, QTableWidgetItem(item.status))
            # Override
            self.table.setItem(row, 2, QTableWidgetItem(box.manual_override or "—"))

            # Last QC & Expires
            rows_by_path = getattr(self, "_last_rows_cache", {})
            eval = evaluate_box_from_rows(box, rows_by_path.get(box.csv_path, []))
            if box.manual_override == STATUS_DEAD:
                eval.status = STATUS_DEAD
                eval.reason = "Manual override: DEAD-LINE"
            elif box.manual_override == STATUS_SERVICE:
                eval.status = STATUS_SERVICE
                eval.reason = "Manual override: SERVICE"

            last_qc_str = eval.last_qc_time.isoformat(sep=' ') if eval.last_qc_time else "—"
            self.table.setItem(row, 3, QTableWidgetItem(last_qc_str))

            if eval.status in (STATUS_GREEN, STATUS_YELLOW) and eval.last_qc_time:
                time_since = now_utc() - eval.last_qc_time
                ttl = timedelta(hours=box.qc_expire_hours) - time_since
                self.table.setItem(row, 4, QTableWidgetItem(human_tdelta(ttl)))
            else:
                self.table.setItem(row, 4, QTableWidgetItem("—"))

            # CSV path
            self.table.setItem(row, 5, QTableWidgetItem(box.csv_path))

            # Watched params
            ptext = ", ".join([p.name for p in box.parameters]) if box.parameters else "—"
            self.table.setItem(row, 6, QTableWidgetItem(ptext))

        self.table.resizeRowsToContents()

def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)

    w = MainWindow()
    w.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
