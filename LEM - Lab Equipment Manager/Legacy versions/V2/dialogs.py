#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dialogs.py — Settings (Tests + Daily Report), Box Editor, Info dialog, and Report Preview dialog.
"""

from __future__ import annotations

import csv
from typing import List, Optional, Tuple

from PyQt5.QtCore import Qt, QTime
from PyQt5.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QDoubleSpinBox, QPushButton,
    QHBoxLayout, QVBoxLayout, QLabel, QWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QMessageBox, QComboBox, QCheckBox, QTimeEdit, QGroupBox
)

from models import TestSpec, BoxConfig
from data_source import BoxEvaluation


class SettingsDialog(QDialog):
    """
    Global settings dialog for TestSpec catalog and Daily Report.
    """
    def __init__(self, parent, tests: List[TestSpec],
                 report_enabled: bool, report_time: str, report_dir: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(960)
        self._tests = [TestSpec.from_dict(t.serialize()) for t in tests]  # copy

        root = QVBoxLayout()

        # ---- Tests catalog
        tests_box = QGroupBox("Tests Catalog")
        t_layout = QVBoxLayout()
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "Name", "Sample ID Column", "Sample ID Value", "Value Column",
            "Expected", "σ", "k", "Units"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        t_layout.addWidget(self.table)

        t_btns = QHBoxLayout()
        add = QPushButton("Add")
        remove = QPushButton("Remove")
        add.clicked.connect(self._add_row)
        remove.clicked.connect(self._remove_selected)
        t_btns.addWidget(add); t_btns.addWidget(remove); t_btns.addStretch(1)
        t_layout.addLayout(t_btns)
        tests_box.setLayout(t_layout)
        root.addWidget(tests_box)

        for t in self._tests:
            self._append_test_row(t)

        # ---- Daily report section
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
        browse = QPushButton("Browse…")
        def pick_dir():
            d = QFileDialog.getExistingDirectory(self, "Select Destination Folder")
            if d:
                self.rep_dir.setText(d)
        browse.clicked.connect(pick_dir)
        h.addWidget(self.rep_dir); h.addWidget(browse)
        r_form.addRow("Destination folder:", h)

        rep_box.setLayout(r_form)
        root.addWidget(rep_box)

        # ---- OK/Cancel
        btns = QHBoxLayout()
        ok = QPushButton("OK"); cancel = QPushButton("Cancel")
        ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
        btns.addStretch(1); btns.addWidget(ok); btns.addWidget(cancel)
        root.addLayout(btns)

        self.setLayout(root)

    # ---- tests table helpers
    def _append_test_row(self, t: TestSpec) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        cells = [
            QTableWidgetItem(t.name),
            QTableWidgetItem(t.sample_id_col),
            QTableWidgetItem(t.sample_id_val),
            QTableWidgetItem(t.value_col),
            QTableWidgetItem(str(t.expected)),
            QTableWidgetItem(str(t.std_dev)),
            QTableWidgetItem(str(t.k)),
            QTableWidgetItem(t.units),
        ]
        for cidx, item in enumerate(cells):
            self.table.setItem(r, cidx, item)

    def _add_row(self) -> None:
        self._append_test_row(TestSpec(
            name="New Test", sample_id_col="Lab ID", sample_id_val="AO24",
            value_col="Value", expected=0.0, std_dev=0.5, k=2.0, units=""
        ))

    def _remove_selected(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)

    def get_tests(self) -> List[TestSpec]:
        out: List[TestSpec] = []
        for r in range(self.table.rowCount()):
            try:
                t = TestSpec(
                    name=self.table.item(r, 0).text().strip(),
                    sample_id_col=self.table.item(r, 1).text().strip(),
                    sample_id_val=self.table.item(r, 2).text().strip(),
                    value_col=self.table.item(r, 3).text().strip(),
                    expected=float(self.table.item(r, 4).text()),
                    std_dev=float(self.table.item(r, 5).text()),
                    k=float(self.table.item(r, 6).text()),
                    units=self.table.item(r, 7).text().strip()
                )
                if t.name:
                    out.append(t)
            except Exception:
                continue
        return out

    def get_report_settings(self) -> Tuple[bool, str, str]:
        enabled = self.rep_enable.isChecked()
        t = self.rep_time.time()
        time_str = f"{t.hour():02d}:{t.minute():02d}"
        directory = self.rep_dir.text().strip()
        return enabled, time_str, directory


class BoxEditor(QDialog):
    """
    Box editor: choose CSV, timestamp col, QC expiry, and select watched tests (from catalog).
    """
    def __init__(self, parent, tests: List[TestSpec], box: Optional[BoxConfig] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Box Settings")
        self.setMinimumWidth(600)
        self._all_tests = tests
        self._box = box

        form = QFormLayout()

        self.title_edit = QLineEdit(box.title if box else "Machine")
        form.addRow("Title:", self.title_edit)

        h = QHBoxLayout()
        self.csv_edit = QLineEdit(box.csv_path if box else "")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_csv)
        h.addWidget(self.csv_edit); h.addWidget(browse)
        form.addRow("CSV Path:", h)

        self.tcol_edit = QLineEdit(box.timestamp_col if box else "")
        form.addRow("Timestamp Column (optional):", self.tcol_edit)

        self.qc_hours = QDoubleSpinBox()
        self.qc_hours.setDecimals(1)
        self.qc_hours.setRange(0.5, 9999.0)
        self.qc_hours.setValue(box.qc_expire_hours if box else 24.0)
        form.addRow("QC Expiry (hours):", self.qc_hours)

        # Watched tests (multi-row with removable entries)
        form.addRow(QLabel("Watched Tests:"))
        self._watch_rows: List[Tuple[QHBoxLayout, QComboBox, QPushButton]] = []
        wrp = QVBoxLayout()
        addt = QPushButton("Add Test")
        addt.clicked.connect(self._add_watch_row)
        wrp.addWidget(addt)
        self._watch_container = QVBoxLayout()
        wrp.addLayout(self._watch_container)
        form.addRow(wrp)

        if box and box.watched_tests:
            for tname in box.watched_tests:
                self._add_watch_row(prefill=tname)
        else:
            self._add_watch_row()

        # Locked
        self.locked_edit = QLineEdit("yes" if (box.locked if box else False) else "no")
        form.addRow(QLabel("Locked? (yes/no)"))
        form.addRow(self.locked_edit)

        btns = QHBoxLayout()
        ok = QPushButton("OK"); cancel = QPushButton("Cancel")
        ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
        btns.addStretch(1); btns.addWidget(ok); btns.addWidget(cancel)

        layout = QVBoxLayout()
        layout.addLayout(form); layout.addLayout(btns)
        self.setLayout(layout)

    def _browse_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select CSV", "", "CSV Files (*.csv);;All Files (*.*)")
        if path:
            self.csv_edit.setText(path)

    def _add_watch_row(self, prefill: Optional[str] = None) -> None:
        row = QHBoxLayout()
        cb = QComboBox(); cb.setEditable(False)
        cb.addItems([t.name for t in self._all_tests])
        if prefill:
            idx = cb.findText(prefill)
            if idx >= 0:
                cb.setCurrentIndex(idx)
        rm = QPushButton("✖")
        rm.setToolTip("Remove this test")

        def do_rm():
            for i, (ly, combo, btn) in enumerate(self._watch_rows):
                if combo is cb:
                    self._watch_rows.pop(i)
                    combo.deleteLater(); btn.deleteLater()
                    while ly.count():
                        item = ly.takeAt(0)
                        w = item.widget()
                        if w:
                            w.setParent(None)
                    self._watch_container.removeItem(ly)
                    break

        rm.clicked.connect(do_rm)

        row.addWidget(cb, 1)
        row.addWidget(rm, 0)
        self._watch_container.addLayout(row)
        self._watch_rows.append((row, cb, rm))

    def get_box(self, existing_uid: Optional[str]) -> Optional[BoxConfig]:
        title = self.title_edit.text().strip() or "Machine"
        csv_path = self.csv_edit.text().strip()
        tcol = self.tcol_edit.text().strip()
        qc_hours = float(self.qc_hours.value())
        locked = (self.locked_edit.text().strip().lower() in ("yes", "true", "1"))

        if not csv_path:
            QMessageBox.warning(self, "Missing CSV", "Please choose a CSV file path.")
            return None

        watched = []
        for _, cb, _ in self._watch_rows:
            name = cb.currentText().strip()
            if name:
                watched.append(name)
        if not watched:
            QMessageBox.warning(self, "No Tests", "Please add at least one watched test from Settings.")
            return None

        uid = existing_uid or f"box_{id(self)}"
        pos = self._box.pos if self._box else (20.0, 20.0)
        manual = self._box.manual_override if self._box else ""

        return BoxConfig(
            uid=uid, title=title, csv_path=csv_path, timestamp_col=tcol,
            qc_expire_hours=qc_hours, watched_tests=watched, pos=pos,
            size=(20.0, 20.0),  # overridden on creation to grid size
            locked=locked, manual_override=manual
        )


class InfoDialog(QDialog):
    """
    Show computed evaluation details for a box.
    """
    def __init__(self, parent, box: BoxConfig, eval: BoxEvaluation) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Info — {box.title}")
        self.setMinimumWidth(640)

        layout = QVBoxLayout()
        layout.addWidget(QLabel(f"<b>Title:</b> {box.title}"))
        layout.addWidget(QLabel(f"<b>CSV:</b> {box.csv_path}"))
        if box.timestamp_col:
            layout.addWidget(QLabel(f"<b>Timestamp column:</b> {box.timestamp_col}"))
        layout.addWidget(QLabel(f"<b>Status:</b> {eval.status}"))
        layout.addWidget(QLabel(f"<b>Reason:</b> {eval.reason or '—'}"))

        last_qc = eval.last_good_qc.isoformat(sep=' ') if eval.last_good_qc else "—"
        latest_row_t = eval.latest_match_time.isoformat(sep=' ') if eval.latest_match_time else "—"
        layout.addWidget(QLabel(f"<b>Last in-spec QC time:</b> {last_qc}"))
        layout.addWidget(QLabel(f"<b>Latest matching row time:</b> {latest_row_t}"))

        table = QTableWidget(0, 7)
        table.setHorizontalHeaderLabels(["Test", "Expected", "±k·σ", "Range", "Latest", "In Spec", "Note"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for pr in eval.results:
            row = table.rowCount(); table.insertRow(row)
            if pr.test:
                tol = pr.test.k * pr.test.std_dev
                units = f" {pr.test.units}" if pr.test.units else ""
                rng = f"[{pr.low:.6g}, {pr.high:.6g}]" if pr.low is not None else "—"
                latest = "—" if pr.latest_value is None else f"{pr.latest_value:.6g}{units}"
                insp = "—" if pr.in_spec is None else ("YES" if pr.in_spec else "NO")
                table.setItem(row, 0, QTableWidgetItem(pr.test.name))
                table.setItem(row, 1, QTableWidgetItem(f"{pr.test.expected:.6g}{units}"))
                table.setItem(row, 2, QTableWidgetItem(f"{tol:.6g}"))
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
        btns.addStretch(1); btns.addWidget(close)
        layout.addLayout(btns)

        self.setLayout(layout)


class ReportPreviewDialog(QDialog):
    """
    Simple preview of the report rows with a one-click CSV export.
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
        export_btn = QPushButton("Export…")
        close_btn = QPushButton("Close")
        export_btn.clicked.connect(self._export_now)
        close_btn.clicked.connect(self.accept)
        h.addStretch(1); h.addWidget(export_btn); h.addWidget(close_btn)
        v.addLayout(h)

        self.setLayout(v)

    def _export_now(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Report CSV", "LabManagerReport_preview.csv",
                                              "CSV Files (*.csv);;All Files (*.*)")
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
