#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dialogs.py â€“ Settings (Tests + Daily Report), Box Editor, Info dialog, and Report Preview.
"""

from __future__ import annotations

import os
import re
import csv
from uuid import uuid4
from typing import List, Optional, Tuple, Dict
from datetime import datetime, timedelta

from PyQt5.QtCore import Qt, QTime, QUrl, QDate
from PyQt5.QtGui import QDesktopServices, QColor
from PyQt5.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QDoubleSpinBox, QPushButton,
    QHBoxLayout, QVBoxLayout, QLabel, QWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QMessageBox, QComboBox, QCheckBox, QTimeEdit, QGroupBox,
    QTabWidget, QPlainTextEdit, QDateEdit, QInputDialog
)

from models import SampleSpec, SampleTestValue, BoxConfig
from data_source import BoxEvaluation


# =========================
# Settings (Tests + Report)
# =========================

class SettingsDialog(QDialog):
    """
    Global settings dialog for:
      â€¢ Test Definitions (value columns + units)
      â€¢ Sample Catalog (sample id + per-test expected/tolerance)
      â€¢ Automatic Daily Report (enable, time, destination)
    """
    def __init__(self, parent,
                 report_enabled: bool, report_time: str, report_dir: str,
                 samples: Optional[List[SampleSpec]] = None,
                 sample_id_col: str = "Lab ID") -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(960)

        # Work on a copy so cancel won't mutate caller state
        self._samples = [SampleSpec.from_dict(s.serialize()) for s in (samples or [])]
        self._sample_id_col = sample_id_col or "Lab ID"

        root = QVBoxLayout()

        # ---- Sample catalog (expandable) ----
        samples_box = QGroupBox("Sample Catalog")
        s_layout = QVBoxLayout()

        sid_row = QHBoxLayout()
        sid_row.addWidget(QLabel("Sample ID Column:"))
        self.sid_col = QLineEdit(self._sample_id_col)
        sid_row.addWidget(self.sid_col)
        sid_row.addStretch(1)
        s_layout.addLayout(sid_row)

        from PyQt5.QtWidgets import QToolBox
        self.toolbox = QToolBox()
        s_layout.addWidget(self.toolbox)

        sb = QHBoxLayout()
        add_sample_btn = QPushButton("Add Sample")
        rem_sample_btn = QPushButton("Remove Sample")
        sb.addWidget(add_sample_btn)
        sb.addWidget(rem_sample_btn)
        sb.addStretch(1)
        s_layout.addLayout(sb)

        def add_sample_page(sample: Optional[SampleSpec] = None):
            name = sample.name if sample else "Sample"
            wrapper = QWidget()
            v = QVBoxLayout(wrapper)
            name_row = QHBoxLayout()
            name_row.addWidget(QLabel("Sample Name (ID Value):"))
            name_edit = QLineEdit(name)
            name_row.addWidget(name_edit)
            v.addLayout(name_row)

            table = QTableWidget(0, 6)
            table.setHorizontalHeaderLabels(["Test Name", "Value Column", "Expected", "Ïƒ", "k", "Units"])
            table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            v.addWidget(table)

            row_btns = QHBoxLayout()
            add_row_btn = QPushButton("Add Test")
            del_row_btn = QPushButton("Remove Test")
            row_btns.addWidget(add_row_btn)
            row_btns.addWidget(del_row_btn)
            row_btns.addStretch(1)
            v.addLayout(row_btns)

            def append_tv(tv: Optional[SampleTestValue] = None):
                r = table.rowCount()
                table.insertRow(r)
                if tv is None:
                    cells = [QTableWidgetItem(""), QTableWidgetItem("Value"), QTableWidgetItem("0"), QTableWidgetItem("0.5"), QTableWidgetItem("2.0"), QTableWidgetItem("")]
                else:
                    cells = [QTableWidgetItem(tv.test_name), QTableWidgetItem(tv.value_col), QTableWidgetItem(str(tv.expected)), QTableWidgetItem(str(tv.std_dev)), QTableWidgetItem(str(tv.k)), QTableWidgetItem(tv.units)]
                for c, it in enumerate(cells):
                    table.setItem(r, c, it)

            add_row_btn.clicked.connect(lambda: append_tv(None))
            def remove_selected_row():
                r = table.currentRow()
                if r >= 0:
                    table.removeRow(r)
            del_row_btn.clicked.connect(remove_selected_row)

            if sample:
                for tv in sample.tests:
                    append_tv(tv)

            idx = self.toolbox.addItem(wrapper, name)

            def sync_title(text: str):
                self.toolbox.setItemText(idx, text or "Sample")
            name_edit.textChanged.connect(sync_title)

            wrapper._name_edit = name_edit
            wrapper._table = table

        def remove_current_sample():
            i = self.toolbox.currentIndex()
            if i >= 0:
                w = self.toolbox.widget(i)
                self.toolbox.removeItem(i)
                w.deleteLater()

        add_sample_btn.clicked.connect(lambda: add_sample_page(None))
        rem_sample_btn.clicked.connect(remove_current_sample)

        samples_box.setLayout(s_layout)
        root.addWidget(samples_box)

        # populate sample pages
        for s in self._samples:
            add_sample_page(s)

        # ---- Daily report ----
        rep_box = QGroupBox("Automatic Daily Report")
        r_form = QFormLayout()

        self.rep_enable = QCheckBox("Enable")
        self.rep_enable.setChecked(bool(report_enabled))
        r_form.addRow(self.rep_enable)

        self.rep_time = QTimeEdit()
        try:
            hh, mm = [int(x) for x in str(report_time).split(":")[:2]]
        except Exception:
            hh, mm = 17, 0
        self.rep_time.setTime(QTime(hh, mm))
        self.rep_time.setDisplayFormat("HH:mm")
        r_form.addRow("Export time (local):", self.rep_time)

        h = QHBoxLayout()
        self.rep_dir = QLineEdit(report_dir or "")
        browse = QPushButton("Browseâ€¦")

        def pick_dir():
            d = QFileDialog.getExistingDirectory(self, "Select Destination Folder")
            if d:
                self.rep_dir.setText(d)

        browse.clicked.connect(pick_dir)
        h.addWidget(self.rep_dir)
        h.addWidget(browse)
        r_form.addRow("Destination folder:", h)

        rep_box.setLayout(r_form)
        root.addWidget(rep_box)

        # ---- OK/Cancel ----
        btns = QHBoxLayout()
        ok = QPushButton("OK")
        cancel = QPushButton("Cancel")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        btns.addStretch(1)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        root.addLayout(btns)

        self.setLayout(root)

    # -- tests table helpers --

    def get_samples_and_column(self) -> Tuple[List[SampleSpec], str]:
        samples: List[SampleSpec] = []
        for i in range(self.toolbox.count()):
            w = self.toolbox.widget(i)
            name = w._name_edit.text().strip()
            if not name:
                continue
            table: QTableWidget = w._table
            tvs: List[SampleTestValue] = []
            for r in range(table.rowCount()):
                try:
                    tname = table.item(r, 0).text().strip()
                    vcol = table.item(r, 1).text().strip()
                    exp = float(table.item(r, 2).text())
                    sd = float(table.item(r, 3).text())
                    kk = float(table.item(r, 4).text())
                    units = table.item(r, 5).text().strip()
                except Exception:
                    continue
                if tname:
                    tvs.append(SampleTestValue(test_name=tname, value_col=vcol or "Value", expected=exp, std_dev=sd, k=kk, units=units))
            samples.append(SampleSpec(name=name, sample_id_val=name, tests=tvs))
        sample_id_col = self.sid_col.text().strip() or "Lab ID"
        return samples, sample_id_col

    def get_report_settings(self) -> Tuple[bool, str, str]:
        enabled = self.rep_enable.isChecked()
        t = self.rep_time.time()
        time_str = f"{t.hour():02d}:{t.minute():02d}"
        directory = self.rep_dir.text().strip()
        return enabled, time_str, directory


# =========
# Box edit
# =========

class BoxEditor(QDialog):
    """
    Box editor: CSV path, optional timestamp column, QC expiry, watched tests.

    Notes:
    - No size controls (boxes default to 1A-1 grid; resize by handle on canvas).
    - Watched tests are removable rows with a dropdown referencing global catalog.
    """
    def __init__(self, parent, samples: List[SampleSpec], box: Optional[BoxConfig] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Box Settings")
        self.setMinimumWidth(600)
        # Be tolerant if callers pass None
        self._all_samples = samples or []
        self._box = box

        form = QFormLayout()

        self.title_edit = QLineEdit(box.title if box else "Machine")
        form.addRow("Title:", self.title_edit)

        h = QHBoxLayout()
        self.csv_edit = QLineEdit(box.csv_path if box else "")
        browse = QPushButton("Browseâ€¦")
        browse.clicked.connect(self._browse_csv)
        h.addWidget(self.csv_edit)
        h.addWidget(browse)
        form.addRow("CSV Path:", h)

        self.tcol_edit = QLineEdit(box.timestamp_col if box else "")
        form.addRow("Timestamp Column (optional):", self.tcol_edit)

        self.qc_hours = QDoubleSpinBox()
        self.qc_hours.setDecimals(1)
        self.qc_hours.setRange(0.5, 9999.0)
        self.qc_hours.setValue(box.qc_expire_hours if box else 24.0)
        form.addRow("QC Expiry (hours):", self.qc_hours)

        # Sample selection (multiple)
        form.addRow(QLabel("Samples:"))
        self._sample_rows: List[Tuple[QHBoxLayout, QComboBox, QPushButton]] = []
        s_wrap = QVBoxLayout()
        add_s_btn = QPushButton("Add Sample")
        def add_sample_row(prefill: Optional[str] = None):
            row = QHBoxLayout()
            cb = QComboBox()
            cb.addItem("")
            for s in self._all_samples:
                cb.addItem(s.name)
            if prefill:
                i = cb.findText(prefill)
                if i >= 0:
                    cb.setCurrentIndex(i)
            rm = QPushButton("Remove")
            def rm_this():
                self._sample_rows.remove((row, cb, rm))
                self._samples_container.removeItem(row)
                QWidget().setLayout(row)
            rm.clicked.connect(rm_this)
            row.addWidget(cb)
            row.addWidget(rm)
            self._samples_container.addLayout(row)
            self._sample_rows.append((row, cb, rm))
        add_s_btn.clicked.connect(lambda: add_sample_row(None))
        s_wrap.addWidget(add_s_btn)
        self._samples_container = QVBoxLayout()
        s_wrap.addLayout(self._samples_container)
        form.addRow(s_wrap)

        # Watched tests (multi-row, removable) with per-test 'Affects Status'
        form.addRow(QLabel("Watched Tests:"))
        self._watch_rows: List[Tuple[QHBoxLayout, QComboBox, QCheckBox, QPushButton]] = []
        wrp = QVBoxLayout()
        addt = QPushButton("Add Test")
        addt.clicked.connect(self._add_watch_row)
        wrp.addWidget(addt)
        self._watch_container = QVBoxLayout()
        wrp.addLayout(self._watch_container)
        form.addRow(wrp)

        # Prefill tests
        if box and box.watched_tests:
            affects_set = set(getattr(box, 'affects_tests', []) or (box.watched_tests if getattr(box, 'affects_status', True) else []))
            for tname in box.watched_tests:
                self._add_watch_row(prefill=tname, affects=(tname in affects_set))
        else:
            self._add_watch_row()

        # Prefill samples
        if box and getattr(box, 'sample_refs', None):
            for sname in box.sample_refs:
                add_sample_row(sname)
        else:
            add_sample_row("")

        # Locked (simple yes/no text; you can toggle later via context menu)
        self.locked_edit = QLineEdit("yes" if (box.locked if box else False) else "no")
        form.addRow(QLabel("Locked? (yes/no)"))
        form.addRow(self.locked_edit)

        # Buttons
        btns = QHBoxLayout()
        ok = QPushButton("OK")
        cancel = QPushButton("Cancel")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        btns.addStretch(1)
        btns.addWidget(ok)
        btns.addWidget(cancel)

        v = QVBoxLayout()
        v.addLayout(form)
        v.addLayout(btns)
        self.setLayout(v)

    def _browse_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select CSV File", "", "CSV Files (*.csv);;All Files (*.*)")
        if path:
            self.csv_edit.setText(path)

    def _add_watch_row(self, prefill: Optional[str] = None, affects: Optional[bool] = None) -> None:
        row = QHBoxLayout()
        cb = QComboBox()
        cb.addItem("")
        names=set();
        for s in self._all_samples:
            for tv in s.tests:
                names.add(tv.test_name)
        for n in sorted(names):
            cb.addItem(n)
        if prefill:
            idx = cb.findText(prefill)
            if idx >= 0:
                cb.setCurrentIndex(idx)
        affect_chk = QCheckBox("Affects Status")
        affect_chk.setChecked(True if affects is None else bool(affects))
        rm = QPushButton("Remove")
        def rm_this():
            self._watch_container.removeItem(row)
            for i, tup in enumerate(self._watch_rows):
                if tup[0] == row:
                    self._watch_rows.pop(i)
                    break
            QWidget().setLayout(row)  # dispose
        rm.clicked.connect(rm_this)
        row.addWidget(cb)
        row.addWidget(affect_chk)
        row.addWidget(rm)
        self._watch_container.addLayout(row)
        self._watch_rows.append((row, cb, affect_chk, rm))

    def get_box(self, existing_uid: Optional[str]) -> Optional[BoxConfig]:
        uid = existing_uid or f"box_{uuid4().hex}"
        title = self.title_edit.text().strip() or "Machine"
        csv_path = self.csv_edit.text().strip()
        tcol = self.tcol_edit.text().strip()
        qc_hours = float(self.qc_hours.value())
        watched = []
        affects_tests: List[str] = []
        for _, cb, affect_chk, _ in self._watch_rows:
            name = cb.currentText().strip()
            if name:
                watched.append(name)
                if affect_chk.isChecked():
                    affects_tests.append(name)
        locked = (self.locked_edit.text().strip().lower().startswith("y"))

        pos = self._box.pos if self._box else (20.0, 20.0)
        manual = self._box.manual_override if self._box else ""

        # Gather selected samples
        sample_refs: List[str] = []
        for _, cb, _ in getattr(self, '_sample_rows', []):
            n = cb.currentText().strip()
            if n:
                sample_refs.append(n)

        return BoxConfig(
            uid=uid, title=title, csv_path=csv_path, timestamp_col=tcol,
            qc_expire_hours=qc_hours, watched_tests=watched, pos=pos,
            size=(20.0, 20.0),  # overridden to 1A-1 grid in MainWindow.add_box
            locked=locked, manual_override=manual,
            sample_refs=sample_refs,
            affects_tests=affects_tests
        )


# ==========
# Info dialog
# ==========

class InfoDialog(QDialog):
    """
    Show computed evaluation details for a box.
    """
    def __init__(self, parent, box: BoxConfig, eval: BoxEvaluation) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Info â€“ {box.title}")
        self.setMinimumWidth(640)

        layout = QVBoxLayout()

        layout.addWidget(QLabel(f"<b>Title:</b> {box.title}"))
        layout.addWidget(QLabel(f"<b>CSV:</b> {box.csv_path}"))
        if box.timestamp_col:
            layout.addWidget(QLabel(f"<b>Timestamp column:</b> {box.timestamp_col}"))
        layout.addWidget(QLabel(f"<b>Status:</b> {eval.status}"))
        layout.addWidget(QLabel(f"<b>Reason:</b> {eval.reason or 'â€”'}"))

        last_qc = eval.last_good_qc.isoformat(sep=' ') if eval.last_good_qc else "â€”"
        latest_row_t = eval.latest_match_time.isoformat(sep=' ') if eval.latest_match_time else "â€”"
        layout.addWidget(QLabel(f"<b>Last in-spec QC time:</b> {last_qc}"))
        layout.addWidget(QLabel(f"<b>Latest matching row time:</b> {latest_row_t}"))

        table = QTableWidget(0, 7)
        table.setHorizontalHeaderLabels(["Test", "Expected", "k*Ïƒ", "Range", "Latest", "In Spec", "Note"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        for pr in eval.results:
            row = table.rowCount()
            table.insertRow(row)
            if pr.test_name:
                tol = ((pr.high - pr.low) / 2.0) if (pr.low is not None and pr.high is not None) else None
                units = f" {pr.units}" if pr.units else ""
                rng = f"[{pr.low:.6g}, {pr.high:.6g}]" if pr.low is not None else "â€”"
                latest = "â€”" if pr.latest_value is None else f"{pr.latest_value:.6g}{units}"
                insp = "â€”" if pr.in_spec is None else ("YES" if pr.in_spec else "NO")
                table.setItem(row, 0, QTableWidgetItem(pr.test_name))
                exp_txt = ("â€”" if tol is None else f"{((pr.low+pr.high)/2.0):.6g}{units}")
                tol_txt = ("â€”" if tol is None else f"{tol:.6g}")
                table.setItem(row, 1, QTableWidgetItem(exp_txt))
                table.setItem(row, 2, QTableWidgetItem(tol_txt))
                table.setItem(row, 3, QTableWidgetItem(rng))
                table.setItem(row, 4, QTableWidgetItem(latest))
                table.setItem(row, 5, QTableWidgetItem(insp))
                table.setItem(row, 6, QTableWidgetItem(pr.note or ""))
            else:
                table.setItem(row, 0, QTableWidgetItem("(missing test)"))
                table.setItem(row, 6, QTableWidgetItem(pr.note or ""))

        layout.addWidget(table)

        btns = QHBoxLayout()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        btns.addStretch(1)
        btns.addWidget(close)
        layout.addLayout(btns)

        self.setLayout(layout)


# ==================
# Report preview dlg
# ==================

class ReportPreviewDialog(QDialog):
    """
    Preview of the report with one-click CSV export.
    """
    def __init__(self, parent, headers: List[str], rows: List[List[str]]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Report Preview")
        self.setMinimumWidth(980)
        self._headers = headers
        self._rows = rows

        v = QVBoxLayout()

        table = QTableWidget(len(rows), len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                table.setItem(r, c, QTableWidgetItem(str(val)))
        v.addWidget(table)

        h = QHBoxLayout()
        export_btn = QPushButton("Exportâ€¦")
        close_btn = QPushButton("Close")
        export_btn.clicked.connect(self._export_now)
        close_btn.clicked.connect(self.accept)
        h.addStretch(1)
        h.addWidget(export_btn)
        h.addWidget(close_btn)
        v.addLayout(h)

        self.setLayout(v)

    def _export_now(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Report CSV", "LabManagerReport_preview.csv",
            "CSV Files (*.csv);;All Files (*.*)"
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(self._headers)
                for row in self._rows:
                    w.writerow(row)
            QMessageBox.information(self, "Export", f"Saved:\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "Export failed", f"{e}")





class MachineInfoDialog(QDialog):
    """ Machine info with tabs: Status and Maintenance Log. """
    def __init__(self, parent, box: BoxConfig, ev: BoxEvaluation) -> None:
        super().__init__(parent)
        self._box = box
        self._ev = ev
        self.setWindowTitle(f"Machine Info - {box.title}")
        self.setMinimumWidth(760)

        tabs = QTabWidget(self)
        tabs.addTab(self._build_status_tab(), "Status")
        tabs.addTab(self._build_maint_tab2(), "Maintenance Log")

        v = QVBoxLayout()
        v.addWidget(tabs)
        self.setLayout(v)

    def _build_status_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        dash = "-"
        v.addWidget(QLabel(f"<b>Title:</b> {self._box.title}"))
        v.addWidget(QLabel(f"<b>CSV:</b> {self._box.csv_path}"))
        if self._box.timestamp_col:
            v.addWidget(QLabel(f"<b>Timestamp column:</b> {self._box.timestamp_col}"))
        v.addWidget(QLabel(f"<b>Status:</b> {self._ev.status}"))
        v.addWidget(QLabel(f"<b>Reason:</b> {self._ev.reason or dash}"))

        last_qc = self._ev.last_good_qc.isoformat(sep=' ') if self._ev.last_good_qc else dash
        latest_row_t = self._ev.latest_match_time.isoformat(sep=' ') if self._ev.latest_match_time else dash
        v.addWidget(QLabel(f"<b>Last in-spec QC time:</b> {last_qc}"))
        v.addWidget(QLabel(f"<b>Latest matching row time:</b> {latest_row_t}"))

        table = QTableWidget(0, 7)
        table.setHorizontalHeaderLabels(["Test", "Expected", "Tol", "Range", "Latest", "In Spec", "Note"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        for pr in self._ev.results:
            row = table.rowCount(); table.insertRow(row)
            if pr.test_name:
                tol = ((pr.high - pr.low) / 2.0) if (pr.low is not None and pr.high is not None) else None
                units = f" {pr.units}" if pr.units else ""
                rng = f"[{pr.low:.6g}, {pr.high:.6g}]" if pr.low is not None and pr.high is not None else dash
                latest_val = dash if pr.latest_value is None else f"{pr.latest_value:.6g}{units}"
                if getattr(pr, 'sample_name', None):
                    sfx = f" ({pr.sample_name})"
                    latest_val = (latest_val + sfx) if latest_val != dash else dash
                insp = dash if pr.in_spec is None else ("YES" if pr.in_spec else "NO")
                table.setItem(row, 0, QTableWidgetItem(pr.test_name))
                exp_txt = (dash if tol is None or pr.low is None or pr.high is None else f"{((pr.low+pr.high)/2.0):.6g}{units}")
                tol_txt = (dash if tol is None else f"{tol:.6g}")
                table.setItem(row, 1, QTableWidgetItem(exp_txt))
                table.setItem(row, 2, QTableWidgetItem(tol_txt))
                table.setItem(row, 3, QTableWidgetItem(rng))
                table.setItem(row, 4, QTableWidgetItem(latest_val))
                table.setItem(row, 5, QTableWidgetItem(insp))
                table.setItem(row, 6, QTableWidgetItem(pr.note or ""))
            else:
                table.setItem(row, 0, QTableWidgetItem("(missing test)"))
                table.setItem(row, 6, QTableWidgetItem(pr.note or ""))

        v.addWidget(table)
        return w
    def _build_maint_tab2(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        tabs = QTabWidget()
        tabs.addTab(self._build_comments_tab(), "Comments")
        tabs.addTab(self._build_pm_tab(), "Scheduled PM")
        v.addWidget(tabs)
        return w
    def _build_comments_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        form = QFormLayout()
        self.cat_cb = QComboBox(); self.cat_cb.addItems(["General", "Calibration", "Repair", "QC Note"]) 
        self.person_edit = QLineEdit()
        self.comment_edit = QPlainTextEdit(); self.comment_edit.setPlaceholderText("Enter maintenance comment…")
        form.addRow("Category:", self.cat_cb)
        form.addRow("Person:", self.person_edit)
        form.addRow("Comment:", self.comment_edit)
        v.addLayout(form)

        btns = QHBoxLayout()
        save_btn = QPushButton("Save Comment")
        refresh_btn = QPushButton("Refresh")
        open_btn = QPushButton("Open File…")
        btns.addStretch(1)
        btns.addWidget(open_btn)
        btns.addWidget(refresh_btn)
        btns.addWidget(save_btn)
        v.addLayout(btns)

        self.comments_table = QTableWidget(0, 7)
        self.comments_table.setHorizontalHeaderLabels(["timestamp","box_uid","box_title","category","person","next_due","comment"])
        self.comments_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.comments_table.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.comments_table)

        save_btn.clicked.connect(self._on_save_comment)
        refresh_btn.clicked.connect(self._reload_comments_table)
        open_btn.clicked.connect(self._open_comments_file)

        self._reload_comments_table()
        return w

    def _build_pm_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        form = QFormLayout()
        self.pm_title = QLineEdit()
        self.pm_due = QDateEdit(); self.pm_due.setCalendarPopup(True); self.pm_due.setDisplayFormat("yyyy-MM-dd")

        from PyQt5.QtWidgets import QSpinBox
        self.pm_every = QSpinBox(); self.pm_every.setRange(1, 3650); self.pm_every.setValue(30)
        self.pm_unit = QComboBox(); self.pm_unit.addItems(["days", "weeks", "months"])
        self.pm_note = QPlainTextEdit(); self.pm_note.setPlaceholderText("Optional task note.")
        form.addRow("Task:", self.pm_title)
        form.addRow("Due:", self.pm_due)
        h = QHBoxLayout(); h.addWidget(QLabel("Every")); h.addWidget(self.pm_every); h.addWidget(self.pm_unit); h.addStretch(1)
        form.addRow("Frequency:", h)
        form.addRow("Note:", self.pm_note)
        v.addLayout(form)

        btns = QHBoxLayout()
        add_btn = QPushButton("Add Task")
        complete_btn = QPushButton("Complete Selected")
        refresh_btn = QPushButton("Refresh")
        btns.addStretch(1)
        btns.addWidget(refresh_btn)
        btns.addWidget(complete_btn)
        btns.addWidget(add_btn)
        v.addLayout(btns)

        self.pm_table = QTableWidget(0, 5)
        self.pm_table.setHorizontalHeaderLabels(["task_id","Task","Next Due","Every","Last Completed"])  # hide id column
        self.pm_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.pm_table.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.pm_table)
        self.pm_table.setColumnHidden(0, True)

        add_btn.clicked.connect(self._on_add_pm)
        complete_btn.clicked.connect(self._on_complete_pm)
        refresh_btn.clicked.connect(self._reload_pm_table)

        self._reload_pm_table()
        return w

    # ---- Comments actions ----
    def _on_save_comment(self) -> None:
        txt = self.comment_edit.toPlainText().strip()
        if not txt:
            QMessageBox.warning(self, "Validation", "Comment is required.")
            return
        try:
            path = self._log_path_for(self._box)
            cat = self.cat_cb.currentText().strip()
            person = self.person_edit.text().strip()
            self._append_entry(path, self._box, cat, person, "", txt)
            self.comment_edit.clear()
            self._reload_comments_table()
            QMessageBox.information(self, "Saved", "Comment saved.")
        except Exception as e:
            QMessageBox.warning(self, "Save failed", f"{e}")

    def _reload_comments_table(self) -> None:
        try:
            path = self._log_path_for(self._box)
            if not os.path.exists(path):
                self.comments_table.setRowCount(0)
                return
            rows: List[List[str]] = []
            with open(path, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                headers = next(reader, None)
                for r in reader:
                    if len(r) < 7:
                        r = (r + [""] * 7)[:7]
                    rows.append(r)
            rows.reverse()
            rows = rows[:500]
            self.comments_table.setRowCount(0)
            self.comments_table.setColumnCount(7)
            self.comments_table.setHorizontalHeaderLabels(["timestamp","box_uid","box_title","category","person","next_due","comment"])
            for r in rows:
                row_i = self.comments_table.rowCount()
                self.comments_table.insertRow(row_i)
                for c, val in enumerate(r):
                    self.comments_table.setItem(row_i, c, QTableWidgetItem(str(val)))
            try:
                self.comments_table.setColumnHidden(1, True)
            except Exception:
                pass
            self.comments_table.resizeRowsToContents()
        except Exception as e:
            QMessageBox.warning(self, "Load failed", f"{e}")

    def _open_comments_file(self) -> None:
        try:
            path = self._log_path_for(self._box)
            self._ensure_header(path)
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception as e:
            QMessageBox.warning(self, "Open failed", f"{e}")

    # ---- PM storage + actions ----
    def _tasks_path_for(self, box: BoxConfig) -> str:
        base_dir = os.path.dirname(box.csv_path) if box.csv_path else os.getcwd()
        return os.path.join(base_dir, f"{self._sanitize(box.title)}_pm_tasks.csv")

    def _ensure_tasks_header(self, path: str) -> None:
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(["id","task","due_date","freq_unit","freq_interval","last_completed","note","active"])

    def _load_tasks(self) -> List[Dict[str, str]]:
        p = self._tasks_path_for(self._box)
        if not os.path.exists(p):
            return []
        out: List[Dict[str, str]] = []
        try:
            with open(p, "r", newline="", encoding="utf-8") as f:
                rd = csv.DictReader(f)
                for r in rd:
                    out.append(r)
        except Exception:
            pass
        return out

    def _save_tasks(self, rows: List[Dict[str, str]]) -> None:
        p = self._tasks_path_for(self._box)
        self._ensure_tasks_header(p)
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["id","task","due_date","freq_unit","freq_interval","last_completed","note","active"])
            w.writeheader()
            for r in rows:
                w.writerow(r)

    def _on_add_pm(self) -> None:
        title = self.pm_title.text().strip()
        if not title:
            QMessageBox.warning(self, "Validation", "Task name is required.")
            return
        unit = self.pm_unit.currentText()
        interval = str(int(self.pm_every.value()))
        note = self.pm_note.toPlainText().strip()
        try:
            p = self._tasks_path_for(self._box)
            self._ensure_tasks_header(p)
            tid = datetime.now().strftime("%Y%m%d%H%M%S%f")
            with open(p, "a", newline="", encoding="utf-8") as f:
                due = self.pm_due.date().toString("yyyy-MM-dd")
                csv.writer(f).writerow([tid, title, due, unit, interval, "", note, "1"])
            self.pm_title.clear(); self.pm_note.clear()
            self._reload_pm_table()
            QMessageBox.information(self, "Saved", "PM task added.")
            QMessageBox.information(self, "Saved", "PM task added.")
        except Exception as e:
            QMessageBox.warning(self, "Save failed", f"{e}")
        tid = self.pm_table.item(r, 0).text()
    def _on_complete_pm(self) -> None:
        now = datetime.now().replace(microsecond=0)
        for rec in tasks:
            if rec.get("id") == tid:
                rec["last_completed"] = now.isoformat(sep=" ")
                # Prompt for person and details
                try:
                    who, ok1 = QInputDialog.getText(self, "Complete Maintenance", "Completed by (name):")
                    details, ok2 = QInputDialog.getText(self, "Complete Maintenance", "Tasks done / Results (optional):")
                except Exception:
                    who, details, ok1, ok2 = "", "", True, True
                try:
                    base = f"PM Completed: {rec.get('task','')} (every {rec.get('freq_interval','')} {rec.get('freq_unit','')})"
                    extra = rec.get('note','')
                    combined = base + ("\n" + extra if extra else "") + ("\n" + details if details else "")
                    self._append_entry(self._log_path_for(self._box), self._box, "PM Complete", who or "", "", combined)
                except Exception:
                    pass
                break
        try:
            self._save_tasks(tasks)
            self._reload_pm_table()
            self._reload_comments_table()
        except Exception as e:
            QMessageBox.warning(self, "Save failed", f"{e}")

    def _reload_pm_table(self) -> None:
        rows = self._load_tasks()
        self.pm_table.setRowCount(0)
        def sort_key(rec: Dict[str, str]):
            try:
                return datetime.strptime(rec.get("due_date", "9999-12-31"), "%Y-%m-%d")
            except Exception:
                return datetime.max
                lc = (rec.get("last_completed") or "").strip()
                if lc:
                    base = datetime.fromisoformat(lc)
                else:
                    base = datetime.strptime((rec.get("id") or "").strip(), "%Y%m%d%H%M%S%f")
                if unit == "days":
                    due = base + timedelta(days=interval)
                elif unit == "weeks":
                    due = base + timedelta(weeks=interval)
                else:
                    due = base + timedelta(days=30*interval)
                return due
            except Exception:
                return datetime.max
        rows.sort(key=sort_key)
        for rec in rows:
            ridx = self.pm_table.rowCount()
            self.pm_table.insertRow(ridx)
            vals = [
                rec.get("id", ""),
                rec.get("task", ""),
                rec.get("task", ""),
                rec.get("due_date", ""),
                rec.get("last_completed", ""),
            ]
            for c, val in enumerate(vals):
                self.pm_table.setItem(ridx, c, QTableWidgetItem(str(val)))
            try:
                due_s = rec.get("due_date", "")
                if due_s:
                    due_dt = datetime.strptime(due_s, "%Y-%m-%d")
                    if due_dt <= datetime.now():
                        for c in range(self.pm_table.columnCount()):
                            it = self.pm_table.item(ridx, c)
                            if it:
                                it.setBackground(QColor(255, 230, 230))
            except Exception:
                pass

        form = QFormLayout()
        self.cat_cb = QComboBox(); self.cat_cb.addItems(["General", "Calibration", "Repair", "QC Note"])
        self.person_edit = QLineEdit()
        self.next_due = QDateEdit(); self.next_due.setCalendarPopup(True); self.next_due.setDisplayFormat("yyyy-MM-dd")
        # Allow blank by using special value text on minimum date
        self.next_due.setSpecialValueText("")
        self.next_due.setMinimumDate(QDate(1, 1, 1))
        self.next_due.setDate(QDate(1, 1, 1))
        self.comment_edit = QPlainTextEdit(); self.comment_edit.setPlaceholderText("Enter maintenance note…")

        form.addRow("Category:", self.cat_cb)
        form.addRow("Person:", self.person_edit)
        form.addRow("Next Due:", self.next_due)
        form.addRow("Comment:", self.comment_edit)
        v.addLayout(form)

        # Buttons
        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save")
        refresh_btn = QPushButton("Refresh")
        open_btn = QPushButton("Open File…")
        btn_row.addStretch(1)
        btn_row.addWidget(open_btn)
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(save_btn)
        v.addLayout(btn_row)

        # Log table
        self.log_table = QTableWidget(0, 7)
        self.log_table.setHorizontalHeaderLabels(["timestamp","box_uid","box_title","category","person","next_due","comment"])
        self.log_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.log_table.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.log_table)

        # Wire buttons
        save_btn.clicked.connect(self._on_save_entry)
        refresh_btn.clicked.connect(self._reload_log_table)
        open_btn.clicked.connect(self._open_log_file)

        # Initial load
        self._reload_log_table()
        return w

    # ---- helpers (maintenance) ----
    @staticmethod
    def _sanitize(name: str) -> str:
        s = re.sub(r"[^A-Za-z0-9_]+", "_", (name or "machine").strip())
        s = s.strip("_") or "machine"
        return s

    def _log_path_for(self, box: BoxConfig) -> str:
        base_dir = os.path.dirname(box.csv_path) if box.csv_path else os.getcwd()
        return os.path.join(base_dir, f"{self._sanitize(box.title)}_maintenance_log.csv")

    @staticmethod
    def _ensure_header(path: str) -> None:
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([
                    "timestamp","box_uid","box_title","category","person","next_due","comment"
                ])

    def _append_entry(self, path: str, box: BoxConfig, category: str, person: str, next_due: str, comment: str) -> None:
        self._ensure_header(path)
        ts = datetime.now().replace(microsecond=0).isoformat(sep=" ")
        with open(path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([ts, box.uid, box.title, category, person, next_due, comment])

    # ---- actions ----
    def _on_save_entry(self) -> None:
        txt = self.comment_edit.toPlainText().strip()
        if not txt:
            QMessageBox.warning(self, "Validation", "Comment is required.")
            return
        try:
            path = self._log_path_for(self._box)
            cat = self.cat_cb.currentText().strip()
            person = self.person_edit.text().strip()
            # Blankable date using special value (min date)
            nd = self.next_due.date()
            next_due = nd.toString("yyyy-MM-dd") if nd != QDate(1, 1, 1) else ""
            self._append_entry(path, self._box, cat, person, next_due, txt)
            self.comment_edit.clear()
            self._reload_log_table()
            QMessageBox.information(self, "Saved", "Maintenance entry saved.")
        except Exception as e:
            QMessageBox.warning(self, "Save failed", f"{e}")

    def _reload_log_table(self) -> None:
        try:
            path = self._log_path_for(self._box)
            if not os.path.exists(path):
                # Show empty table but do not create file on read
                self.log_table.setRowCount(0)
                return
            rows: List[List[str]] = []
            with open(path, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                headers = next(reader, None)
                for r in reader:
                    if len(r) < 7:
                        # pad malformed rows, but keep going
                        r = (r + [""] * 7)[:7]
                    rows.append(r)
            # Newest first; cap to latest 500
            rows.reverse()
            rows = rows[:500]
            self.log_table.setRowCount(0)
            self.log_table.setColumnCount(7)
            self.log_table.setHorizontalHeaderLabels(["timestamp","box_uid","box_title","category","person","next_due","comment"])
            for r in rows:
                row_i = self.log_table.rowCount()
                self.log_table.insertRow(row_i)
                for c, val in enumerate(r):
                    self.log_table.setItem(row_i, c, QTableWidgetItem(str(val)))
            self.log_table.resizeRowsToContents()
        except Exception as e:
            QMessageBox.warning(self, "Load failed", f"{e}")

    def _open_log_file(self) -> None:
        try:
            path = self._log_path_for(self._box)
            # Ensure file exists with header so open succeeds
            self._ensure_header(path)
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception as e:
            QMessageBox.warning(self, "Open failed", f"{e}")



















