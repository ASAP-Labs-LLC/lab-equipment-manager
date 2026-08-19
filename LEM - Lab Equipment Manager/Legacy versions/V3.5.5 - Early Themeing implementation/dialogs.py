#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dialogs.py â€” Settings (Tests + Daily Report), Box Editor, Info dialog, and Report Preview dialog.
"""

from __future__ import annotations

import csv
from datetime import datetime
from typing import List, Optional, Tuple, Dict

from PyQt5.QtCore import Qt, QTime, QDate
from PyQt5.QtGui import QColor, QFontDatabase
from PyQt5.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QDoubleSpinBox, QPushButton,
    QHBoxLayout, QVBoxLayout, QLabel, QWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QMessageBox, QComboBox, QCheckBox, QTimeEdit, QGroupBox, QListWidget,
    QTabWidget, QDateEdit, QSpinBox, QTextEdit, QListWidgetItem, QAbstractItemView, QFontComboBox
)
import os
import shutil

from models import SampleSpec, SampleTestSpec, BoxConfig, WatchedTarget
try:
    from theme import theme_manager  # type: ignore[reportMissingImports]
except Exception:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(__file__))
    from theme import theme_manager  # type: ignore[reportMissingImports]
from maintenance import MaintenanceManager
from data_source import BoxEvaluation


class SettingsDialog(QDialog):
    """
    Global settings dialog for managing samples with nested tests and Daily Report options.
    """
    def __init__(self, parent, samples: List[SampleSpec],
                 sample_id_column: str,
                 report_enabled: bool, report_time: str, report_dir: str,
                 status_log_dir: str = "",
                 theme_mode: str = "light",
                 app_font_family: str = "",
                 app_font_size: int = 10,
                 custom_qss_path: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(1024)
        self._samples = [SampleSpec.from_dict(s.serialize()) for s in samples]  # copy
        self._sample_id_column = sample_id_column or "Lab ID"
        self._current_index = -1

        root = QVBoxLayout()
        tabs = QTabWidget()

        # ---- Sample manager (separate tab)
        samples_page = QWidget()
        samples_box = QGroupBox("Sample Manager")
        samples_layout = QVBoxLayout()
        col_form = QFormLayout()
        self.sample_id_column_edit = QLineEdit(self._sample_id_column)
        col_form.addRow("Sample ID Column:", self.sample_id_column_edit)
        samples_layout.addLayout(col_form)


        split = QHBoxLayout()
        left = QVBoxLayout()
        self.sample_list = QListWidget()
        self.sample_list.currentRowChanged.connect(self._on_sample_selected)
        left.addWidget(self.sample_list)

        sample_btns = QHBoxLayout()
        self.add_sample_btn = QPushButton("Add Sample")
        self.remove_sample_btn = QPushButton("Remove Sample")
        self.add_sample_btn.clicked.connect(self._add_sample)
        self.remove_sample_btn.clicked.connect(self._remove_sample)
        sample_btns.addWidget(self.add_sample_btn)
        sample_btns.addWidget(self.remove_sample_btn)
        sample_btns.addStretch(1)
        left.addLayout(sample_btns)
        split.addLayout(left, 1)

        self.detail_widget = QWidget()
        detail_layout = QVBoxLayout()
        form = QFormLayout()
        self.sample_name_edit = QLineEdit()
        self.sample_name_edit.textChanged.connect(self._on_sample_name_changed)
        self.sample_id_val_edit = QLineEdit()
        form.addRow("Sample Name:", self.sample_name_edit)
        form.addRow("Sample ID Value:", self.sample_id_val_edit)
        detail_layout.addLayout(form)

        self.tests_table = QTableWidget(0, 6)
        self.tests_table.setHorizontalHeaderLabels([
            "Test Name", "Value Column", "Expected", "Std Dev", "Sigma Multiplier", "Units"
        ])
        self.tests_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        detail_layout.addWidget(self.tests_table)

        test_btns = QHBoxLayout()
        self.add_test_btn = QPushButton("Add Test")
        self.remove_test_btn = QPushButton("Remove Test")
        self.add_test_btn.clicked.connect(self._add_test_row)
        self.remove_test_btn.clicked.connect(self._remove_test_row)
        test_btns.addWidget(self.add_test_btn)
        test_btns.addWidget(self.remove_test_btn)
        test_btns.addStretch(1)
        detail_layout.addLayout(test_btns)

        self.detail_widget.setLayout(detail_layout)
        split.addWidget(self.detail_widget, 3)

        samples_layout.addLayout(split)
        samples_box.setLayout(samples_layout)
        sp_layout = QVBoxLayout(samples_page)
        sp_layout.addWidget(samples_box)
        tabs.addTab(samples_page, "Sample Manager")

        self._refresh_sample_list(target_index=0 if self._samples else None)
        if not self._samples:
            self._set_detail_enabled(False)

        # ---- Daily report section
        # ---- General tab
        general_page = QWidget()
        gen_root = QVBoxLayout(general_page)
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
        browse = QPushButton("Browse...")
        def pick_dir():
            d = QFileDialog.getExistingDirectory(self, "Select Destination Folder")
            if d:
                self.rep_dir.setText(d)
        browse.clicked.connect(pick_dir)
        h.addWidget(self.rep_dir); h.addWidget(browse)
        r_form.addRow("Destination folder:", h)

        rep_box.setLayout(r_form)
        gen_root.addWidget(rep_box)

        # ---- Status change logging
        log_box = QGroupBox("Status Change Log")
        l_form = QFormLayout()
        lh = QHBoxLayout()
        self.status_dir = QLineEdit(status_log_dir or "")
        lbrowse = QPushButton("Browse...")
        def pick_status_dir():
            d = QFileDialog.getExistingDirectory(self, "Select Status Log Folder")
            if d:
                self.status_dir.setText(d)
        lbrowse.clicked.connect(pick_status_dir)
        lh.addWidget(self.status_dir); lh.addWidget(lbrowse)
        l_form.addRow("Log folder:", lh)
        log_box.setLayout(l_form)
        gen_root.addWidget(log_box)

        # ---- Appearance
        app_box = QGroupBox("Appearance")
        a_form = QFormLayout()
        # Theme
        self.theme_cb = QComboBox()
        self._theme_map: Dict[str, str] = {}
        base = theme_manager().base_dir
        # Built-ins
        self.theme_cb.addItem("Light"); self._theme_map["Light"] = "builtin:light"
        self.theme_cb.addItem("Dark");  self._theme_map["Dark"] = "builtin:dark"
        # Additional QSS in themes folder
        try:
            for fname in sorted(os.listdir(base)):
                if not fname.lower().endswith('.qss'):
                    continue
                if fname.lower() in ("light.qss", "dark.qss"):
                    continue
                label = os.path.splitext(fname)[0].replace('_', ' ').title()
                self.theme_cb.addItem(label)
                self._theme_map[label] = os.path.join(base, fname)
        except Exception:
            pass
        # Select current
        tm = (theme_mode or 'light').lower()
        sel_label = "Light"
        if tm == 'dark':
            sel_label = "Dark"
        elif tm == 'custom' and custom_qss_path:
            bname = os.path.basename(custom_qss_path)
            label_guess = os.path.splitext(bname)[0].replace('_', ' ').title()
            if label_guess in self._theme_map:
                sel_label = label_guess
        idx = max(0, self.theme_cb.findText(sel_label))
        self.theme_cb.setCurrentIndex(idx)
        a_form.addRow("Theme:", self.theme_cb)
        # Font family + size
        self.font_combo = QFontComboBox()
        if app_font_family:
            try:
                self.font_combo.setCurrentFont(app_font_family)
            except Exception:
                pass
        self.font_size = QSpinBox(); self.font_size.setRange(6, 36); self.font_size.setValue(int(app_font_size or 10))
        a_form.addRow("Font:", self.font_combo)
        a_form.addRow("Font size:", self.font_size)
        # Import theme button (QSS)
        imp_btn = QPushButton("Import Theme (QSS)...")
        self._custom_qss_path: str = custom_qss_path or ""
        def import_theme():
            path, _ = QFileDialog.getOpenFileName(self, "Select QSS File", filter="Qt Style Sheet (*.qss)")
            if not path:
                return
            # Copy into themes folder
            try:
                base_dir = theme_manager().base_dir
                os.makedirs(base_dir, exist_ok=True)
                name = os.path.basename(path)
                dest = os.path.join(base_dir, name)
                root_name, ext = os.path.splitext(name)
                i = 1
                while os.path.exists(dest):
                    dest = os.path.join(base_dir, f"{root_name}_{i}{ext}")
                    i += 1
                shutil.copy2(path, dest)
                self._custom_qss_path = dest
                # Update dropdown
                label = os.path.splitext(os.path.basename(dest))[0].replace('_', ' ').title()
                if label not in self._theme_map:
                    self.theme_cb.addItem(label)
                    self._theme_map[label] = dest
                self.theme_cb.setCurrentText(label)
            except Exception:
                pass
            # Apply immediately
            try:
                from PyQt5.QtWidgets import QApplication
                from theme import theme_manager
                theme_manager().apply_file(QApplication.instance(), self._custom_qss_path or path)
            except Exception:
                pass
            QMessageBox.information(self, "Theme", "Theme applied. It will be used after saving settings as well.")
        imp_btn.clicked.connect(import_theme)
        a_form.addRow(imp_btn)
        app_box.setLayout(a_form)
        gen_root.addWidget(app_box)

        tabs.addTab(general_page, "General")
        root.addWidget(tabs)

        # ---- OK/Cancel
        btns = QHBoxLayout()
        ok = QPushButton("OK"); cancel = QPushButton("Cancel")
        ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
        btns.addStretch(1); btns.addWidget(ok); btns.addWidget(cancel)
        root.addLayout(btns)

        self.setLayout(root)

    # ---- sample helpers
    def _set_detail_enabled(self, enabled: bool) -> None:
        self.detail_widget.setEnabled(enabled)
        self.remove_sample_btn.setEnabled(enabled and bool(self._samples))

    def _refresh_sample_list(self, target_index: Optional[int] = None) -> None:
        current = self._current_index if target_index is None else target_index
        self.sample_list.blockSignals(True)
        self.sample_list.clear()
        for s in self._samples:
            self.sample_list.addItem(s.name if s.name else "(unnamed)")
        self.sample_list.blockSignals(False)
        if self._samples:
            idx = 0 if current is None else max(0, min(current, len(self._samples) - 1))
            self.sample_list.setCurrentRow(idx)
        else:
            self.sample_list.setCurrentRow(-1)
            self._current_index = -1
            self._clear_sample_fields()
        self._set_detail_enabled(bool(self._samples))

    def _on_sample_selected(self, row: int) -> None:
        if row == self._current_index:
            return
        self._save_current_sample()
        self._current_index = row
        if 0 <= row < len(self._samples):
            self._load_sample(self._samples[row])
            self._set_detail_enabled(True)
        else:
            self._clear_sample_fields()
            self._set_detail_enabled(False)

    def _save_current_sample(self) -> None:
        idx = self._current_index
        if 0 <= idx < len(self._samples):
            sample = self._samples[idx]
            sample.name = self.sample_name_edit.text().strip()
            sample.sample_id_val = self.sample_id_val_edit.text().strip()
            sample.tests = self._collect_tests()
            self._samples[idx] = sample
            item = self.sample_list.item(idx)
            if item:
                item.setText(sample.name if sample.name else "(unnamed)")

    def _load_sample(self, sample: SampleSpec) -> None:
        self.sample_name_edit.setText(sample.name)
        self.sample_id_val_edit.setText(sample.sample_id_val)
        self._load_tests_table(sample.tests)

    def _clear_sample_fields(self) -> None:
        self.sample_name_edit.clear()
        self.sample_id_val_edit.clear()
        self.tests_table.setRowCount(0)

    def _add_sample(self) -> None:
        self._save_current_sample()
        default_test = SampleTestSpec(
            name="New Test", value_col="Value", expected=0.0, std_dev=0.5, k=2.0, units=""
        )
        new_sample = SampleSpec(
            name="New Sample", sample_id_val="", tests=[default_test]
        )
        self._samples.append(new_sample)
        self._refresh_sample_list(target_index=len(self._samples) - 1)

    def _remove_sample(self) -> None:
        row = self.sample_list.currentRow()
        if 0 <= row < len(self._samples):
            del self._samples[row]
            self._current_index = -1
            self._refresh_sample_list(target_index=min(row, len(self._samples) - 1))

    def _on_sample_name_changed(self, text: str) -> None:
        idx = self.sample_list.currentRow()
        if idx >= 0:
            item = self.sample_list.item(idx)
            if item:
                item.setText(text if text else "(unnamed)")

    def _add_test_row(self) -> None:
        row = self.tests_table.rowCount()
        self.tests_table.insertRow(row)
        defaults = [
            QTableWidgetItem("New Test"),
            QTableWidgetItem("Value"),
            QTableWidgetItem("0.0"),
            QTableWidgetItem("0.5"),
            QTableWidgetItem("2.0"),
            QTableWidgetItem(""),
        ]
        for col, item in enumerate(defaults):
            self.tests_table.setItem(row, col, item)

    def _remove_test_row(self) -> None:
        row = self.tests_table.currentRow()
        if row >= 0:
            self.tests_table.removeRow(row)

    def _load_tests_table(self, tests: List[SampleTestSpec]) -> None:
        self.tests_table.setRowCount(0)
        for test in tests:
            row = self.tests_table.rowCount()
            self.tests_table.insertRow(row)
            cells = [
                QTableWidgetItem(test.name),
                QTableWidgetItem(test.value_col),
                QTableWidgetItem(f"{test.expected:.6g}"),
                QTableWidgetItem(f"{test.std_dev:.6g}"),
                QTableWidgetItem(f"{test.k:.6g}"),
                QTableWidgetItem(test.units),
            ]
            for col, item in enumerate(cells):
                self.tests_table.setItem(row, col, item)

    def _collect_tests(self) -> List[SampleTestSpec]:
        tests: List[SampleTestSpec] = []
        for row in range(self.tests_table.rowCount()):
            def _text(col: int) -> str:
                it = self.tests_table.item(row, col)
                return it.text().strip() if it else ""
            def _float(col: int, default: float) -> float:
                try:
                    return float(_text(col) or default)
                except Exception:
                    return default
            name = _text(0)
            value_col = _text(1) or name
            expected = _float(2, 0.0)
            std_dev = _float(3, 0.0)
            k = _float(4, 2.0)
            units = _text(5)
            if name or value_col:
                tests.append(SampleTestSpec(
                    name=name or value_col,
                    value_col=value_col,
                    expected=expected,
                    std_dev=std_dev,
                    k=k,
                    units=units,
                ))
        return tests

    def get_samples(self) -> List[SampleSpec]:
        self._save_current_sample()
        return [SampleSpec.from_dict(s.serialize()) for s in self._samples]

    def get_sample_id_column(self) -> str:
        col = self.sample_id_column_edit.text().strip()
        return col or "Lab ID"

    def get_report_settings(self) -> Tuple[bool, str, str]:
        return (
            bool(self.rep_enable.isChecked()),
            self.rep_time.time().toString("HH:mm"),
            self.rep_dir.text().strip(),
        )

    def get_status_log_dir(self) -> str:
        return self.status_dir.text().strip()

    def get_theme_mode(self) -> str:
        label = self.theme_cb.currentText()
        tag = self._theme_map.get(label, '')
        if tag == 'builtin:dark':
            return 'dark'
        if tag == 'builtin:light':
            return 'light'
        return 'custom'

    def get_font_settings(self) -> Tuple[str, int]:
        return (self.font_combo.currentText().strip(), int(self.font_size.value()))

    def get_imported_fonts(self) -> List[str]:
        return []

    def get_custom_qss_path(self) -> str:
        label = self.theme_cb.currentText()
        tag = self._theme_map.get(label, '')
        if tag.startswith('builtin:'):
            return self._custom_qss_path
        return tag or self._custom_qss_path

class BoxEditor(QDialog):
    """
    Box editor: choose CSV, timestamp col, QC expiry, and select watched sample/test pairs.
    """
    def __init__(self, parent, samples: List[SampleSpec], box: Optional[BoxConfig] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Box Settings")
        self.setMinimumWidth(600)
        self._samples = samples
        self._box = box

        form = QFormLayout()

        self.title_edit = QLineEdit(box.title if box else "Machine")
        form.addRow("Title:", self.title_edit)

        h = QHBoxLayout()
        self.csv_edit = QLineEdit(box.csv_path if box else "")
        browse = QPushButton("Browse...")
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

        # Watched sample/tests (multi-row with removable entries)
        form.addRow(QLabel("Watched Sample/Test Pairs:"))
        self._watch_rows: List[Tuple[QHBoxLayout, QComboBox, QComboBox, QPushButton]] = []
        wrp = QVBoxLayout()
        addt = QPushButton("Add Pair")
        addt.clicked.connect(lambda: self._add_watch_row())
        wrp.addWidget(addt)
        self._watch_container = QVBoxLayout()
        wrp.addLayout(self._watch_container)
        form.addRow(wrp)

        if box and box.watched_targets:
            for wt in box.watched_targets:
                self._add_watch_row(prefill=wt)
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

    def _add_watch_row(self, prefill: Optional[WatchedTarget] = None) -> None:
        row = QHBoxLayout()
        sample_cb = QComboBox(); sample_cb.setEditable(False)
        sample_names = [s.name for s in self._samples]
        sample_cb.addItems(sample_names)
        if prefill and prefill.sample and prefill.sample not in sample_names:
            sample_cb.addItem(prefill.sample)
        if prefill and prefill.sample:
            idx = sample_cb.findText(prefill.sample)
            if idx >= 0:
                sample_cb.setCurrentIndex(idx)
        test_cb = QComboBox(); test_cb.setEditable(False)
        self._populate_test_combo(sample_cb, test_cb, prefill.test if prefill else None)

        def handle_sample_change(_index: int) -> None:
            self._populate_test_combo(sample_cb, test_cb)
        sample_cb.currentIndexChanged.connect(handle_sample_change)

        rm = QPushButton("Remove")
        rm.setToolTip("Remove this pair")

        def do_rm() -> None:
            for i, (ly, scb, tcb, btn) in enumerate(self._watch_rows):
                if scb is sample_cb:
                    self._watch_rows.pop(i)
                    scb.deleteLater(); tcb.deleteLater(); btn.deleteLater()
                    while ly.count():
                        item = ly.takeAt(0)
                        widget = item.widget()
                        if widget:
                            widget.setParent(None)
                    self._watch_container.removeItem(ly)
                    break

        rm.clicked.connect(do_rm)

        row.addWidget(sample_cb, 1)
        row.addWidget(test_cb, 1)
        row.addWidget(rm, 0)
        self._watch_container.addLayout(row)
        self._watch_rows.append((row, sample_cb, test_cb, rm))

    def _populate_test_combo(self, sample_cb: QComboBox, test_cb: QComboBox, preferred: Optional[str] = None) -> None:
        sample_name = sample_cb.currentText()
        tests = self._tests_for_sample(sample_name)
        current = preferred if preferred is not None else test_cb.currentText()
        test_cb.blockSignals(True)
        test_cb.clear()
        names = [t.name for t in tests]
        test_cb.addItems(names)
        test_cb.blockSignals(False)
        if current and current in names:
            test_cb.setCurrentIndex(names.index(current))
        elif names:
            test_cb.setCurrentIndex(0)

    def _tests_for_sample(self, sample_name: str) -> List[SampleTestSpec]:
        for s in self._samples:
            if s.name == sample_name:
                return s.tests
        return []

    def get_box(self, existing_uid: Optional[str]) -> Optional[BoxConfig]:
        title = self.title_edit.text().strip() or "Machine"
        csv_path = self.csv_edit.text().strip()
        tcol = self.tcol_edit.text().strip()
        qc_hours = float(self.qc_hours.value())
        locked = (self.locked_edit.text().strip().lower() in ("yes", "true", "1"))

        if not csv_path:
            QMessageBox.warning(self, "Missing CSV", "Please choose a CSV file path.")
            return None

        watched: List[WatchedTarget] = []
        for _, sample_cb, test_cb, _ in self._watch_rows:
            sample = sample_cb.currentText().strip()
            test = test_cb.currentText().strip()
            if sample and test:
                watched.append(WatchedTarget(sample=sample, test=test))
        if not watched:
            QMessageBox.warning(self, "No Tests", "Please add at least one watched sample/test pair from Settings.")
            return None

        uid = existing_uid or f"box_{id(self)}"
        pos = self._box.pos if self._box else (20.0, 20.0)
        manual = self._box.manual_override if self._box else ""

        return BoxConfig(
            uid=uid, title=title, csv_path=csv_path, timestamp_col=tcol,
            qc_expire_hours=qc_hours, watched_targets=watched, pos=pos,
            size=(20.0, 20.0),  # overridden on creation to grid size
            locked=locked, manual_override=manual
        )


class InfoDialog(QDialog):
    """Show computed evaluation details for a box, including maintenance."""
    def __init__(self, parent, box: BoxConfig, eval: BoxEvaluation,
                 maintenance: MaintenanceManager, owner) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Info – {box.title}")
        self.setMinimumWidth(720)

        tabs = QTabWidget()

        details = QWidget()
        detail_layout = QVBoxLayout(details)
        detail_layout.addWidget(QLabel(f"<b>Title:</b> {box.title}"))
        detail_layout.addWidget(QLabel(f"<b>CSV:</b> {box.csv_path}"))
        if box.timestamp_col:
            detail_layout.addWidget(QLabel(f"<b>Timestamp column:</b> {box.timestamp_col}"))
        detail_layout.addWidget(QLabel(f"<b>Status:</b> {eval.status}"))
        detail_layout.addWidget(QLabel(f"<b>Reason:</b> {eval.reason or '-'}"))

        last_qc = eval.last_good_qc.isoformat(sep=' ') if eval.last_good_qc else '-'
        latest_row_t = eval.latest_match_time.isoformat(sep=' ') if eval.latest_match_time else '-'
        detail_layout.addWidget(QLabel(f"<b>Last in-spec QC time:</b> {last_qc}"))
        detail_layout.addWidget(QLabel(f"<b>Latest matching row time:</b> {latest_row_t}"))

        table = QTableWidget(0, 7)
        table.setHorizontalHeaderLabels(["Sample/Test", "Expected", "k*StdDev", "Range", "Latest", "In Spec", "Note"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for pr in eval.results:
            row = table.rowCount(); table.insertRow(row)
            if pr.test:
                tol = pr.test.k * pr.test.std_dev
                units = f" {pr.test.units}" if pr.test.units else ''
                rng = f"[{pr.low:.6g}, {pr.high:.6g}]" if pr.low is not None else '-'
                latest = '-' if pr.latest_value is None else f"{pr.latest_value:.6g}{units}"
                insp = '' if pr.in_spec is None else ('YES' if pr.in_spec else 'NO')
                label = f"{pr.sample} / {pr.test.name}"
                table.setItem(row, 0, QTableWidgetItem(label))
                table.setItem(row, 1, QTableWidgetItem(f"{pr.test.expected:.6g}{units}"))
                table.setItem(row, 2, QTableWidgetItem(f"{tol:.6g}"))
                table.setItem(row, 3, QTableWidgetItem(rng))
                table.setItem(row, 4, QTableWidgetItem(latest))
                table.setItem(row, 5, QTableWidgetItem(insp))
                table.setItem(row, 6, QTableWidgetItem(pr.note or ''))
            else:
                label = pr.sample or '(missing sample)'
                table.setItem(row, 0, QTableWidgetItem(f"{label} / (missing test)"))
                table.setItem(row, 6, QTableWidgetItem(pr.note or ''))
        detail_layout.addWidget(table)
        tabs.addTab(details, "Details")

        self.maintenance_tab = MachineMaintenanceTab(maintenance, owner, box)
        tabs.addTab(self.maintenance_tab, "Maintenance")
        self.comments_tab = MachineCommentsTab(maintenance, owner, box)
        tabs.addTab(self.comments_tab, "Comments")

        layout = QVBoxLayout()
        layout.addWidget(tabs)
        btns = QHBoxLayout()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        btns.addStretch(1); btns.addWidget(close)
        layout.addLayout(btns)
        self.setLayout(layout)



class AddMaintenanceDialog(QDialog):
    def __init__(self, parent, box_title: str, default_kind: Optional[str] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Maintenance Task")
        self.setMinimumWidth(360)
        self._kind = (default_kind or 'pm').lower()

        form = QFormLayout()
        self.name_edit = QLineEdit()
        form.addRow("Task Name:", self.name_edit)
        # Calibration defaults name and locks field
        if self._kind.startswith('cal'):
            self.name_edit.setText("Calibration"); self.name_edit.setReadOnly(True)

        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        form.addRow("Start Date:", self.date_edit)

        self.repeat_spin = QSpinBox()
        self.repeat_spin.setRange(1, 999)
        self.repeat_spin.setValue(6)
        form.addRow("Repeat every:", self.repeat_spin)

        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["Days", "Weeks", "Months", "Years"])
        form.addRow("Unit:", self.unit_combo)

        btns = QHBoxLayout()
        ok = QPushButton("OK"); cancel = QPushButton("Cancel")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        btns.addStretch(1); btns.addWidget(ok); btns.addWidget(cancel)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addLayout(btns)
        self.setLayout(layout)

    def get_data(self) -> Optional[Dict[str, object]]:
        if self.exec_() != QDialog.Accepted:
            return None
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing name", "Please enter a task name.")
            return None
        start_dt = datetime.combine(self.date_edit.date().toPyDate(), datetime.min.time())
        repeat_value = int(self.repeat_spin.value())
        repeat_unit = self.unit_combo.currentText().lower()
        return {
            "name": name,
            "kind": ("calibration" if self._kind.startswith('cal') else "pm"),
            "start": start_dt,
            "repeat_value": repeat_value,
            "repeat_unit": repeat_unit,
        }

class MaintenanceCompleteDialog(QDialog):
    def __init__(self, parent, task_name: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Complete Task")
        self.setMinimumWidth(360)
        form = QFormLayout()
        form.addRow(QLabel(f"<b>Task:</b> {task_name}"))
        self.user_edit = QLineEdit()
        form.addRow("Completed by:", self.user_edit)
        self.comment_edit = QTextEdit()
        form.addRow("Comments:", self.comment_edit)

        btns = QHBoxLayout()
        ok = QPushButton("OK"); cancel = QPushButton("Cancel")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        btns.addStretch(1); btns.addWidget(ok); btns.addWidget(cancel)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addLayout(btns)
        self.setLayout(layout)

    def get_details(self) -> Optional[Tuple[str, str]]:
        if self.exec_() != QDialog.Accepted:
            return None
        return self.user_edit.text().strip(), self.comment_edit.toPlainText().strip()


class MachineMaintenanceTab(QWidget):
    def __init__(self, manager: MaintenanceManager, owner, box: BoxConfig) -> None:
        super().__init__()
        self.manager = manager
        self.owner = owner
        self.box = box

        layout = QVBoxLayout(self)
        self.task_table = QTableWidget(0, 5)
        self.task_table.setHorizontalHeaderLabels(["Task", "Type", "Next Due", "Repeat", "Status"])
        self.task_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.task_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.task_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.task_table)

        btns = QHBoxLayout()
        self.add_cal_btn = QPushButton("Add Calibration")
        self.add_pm_btn = QPushButton("Add PM")
        self.start_btn = QPushButton("Start Task")
        self.complete_btn = QPushButton("Complete Task")
        self.delete_btn = QPushButton("Delete Task")
        btns.addWidget(self.add_cal_btn)
        btns.addWidget(self.add_pm_btn)
        btns.addStretch(1)
        btns.addWidget(self.start_btn)
        btns.addWidget(self.complete_btn)
        btns.addWidget(self.delete_btn)
        layout.addLayout(btns)

        self.add_cal_btn.clicked.connect(lambda: self._add_task('calibration'))
        self.add_pm_btn.clicked.connect(lambda: self._add_task('pm'))
        self.start_btn.clicked.connect(self._start_task)
        self.complete_btn.clicked.connect(self._complete_task)
        self.delete_btn.clicked.connect(self._delete_task)

        self.refresh()
        try:
            self.task_table.itemSelectionChanged.connect(self._update_buttons)
        except Exception:
            pass
        self._update_buttons()

    def _selected_task_id(self) -> Optional[str]:
        row = self.task_table.currentRow()
        if row < 0:
            return None
        item = self.task_table.item(row, 0)
        return None if item is None else item.data(Qt.UserRole)

    def _update_buttons(self) -> None:
        tid = self._selected_task_id()
        disable_start = False
        if tid:
            tpl = self.manager.templates.get(tid)
            if tpl and tpl.status == 'IN_PROGRESS':
                disable_start = True
        self.start_btn.setEnabled(not disable_start)

    def _add_task(self, default_kind: str) -> None:
        dlg = AddMaintenanceDialog(self, self.box.title, default_kind)
        data = dlg.get_data()
        if not data:
            return
        ok = self.owner.add_maintenance_task(self.box, data['name'], data['kind'],
                                        data['start'], data['repeat_value'], data['repeat_unit'])
        if not ok:
            if str(data.get('kind','')).lower() == 'calibration':
                QMessageBox.warning(self, "Calibration Exists",
                                    "A calibration task already exists for this machine. Only one is allowed.")
            else:
                QMessageBox.warning(self, "Add Task Failed",
                                    "The maintenance task could not be added.")
            return
        self.refresh()

    def _start_task(self) -> None:
        task_id = self._selected_task_id()
        if not task_id:
            QMessageBox.information(self, "Select Task", "Please select a task to start.")
            return
        self.owner.start_maintenance_task(task_id)
        self.refresh()

    def _complete_task(self) -> None:
        task_id = self._selected_task_id()
        if not task_id:
            QMessageBox.information(self, "Select Task", "Please select a task to complete.")
            return
        tpl = self.manager.templates.get(task_id)
        dlg = MaintenanceCompleteDialog(self, tpl.name if tpl else "Task")
        details = dlg.get_details()
        if not details:
            return
        user, comment = details
        if not comment.strip():
            QMessageBox.information(self, "Comment", "Please enter a comment before completing.")
            return
        if not self.owner.complete_maintenance_task(task_id, user, comment):
            return
        self.refresh()


    def _delete_task(self) -> None:
        task_id = self._selected_task_id()
        if not task_id:
            QMessageBox.information(self, "Select Task", "Please select a task to delete.")
            return
        tpl = self.manager.templates.get(task_id)
        if not tpl:
            QMessageBox.warning(self, "Delete Task", "Task no longer exists.")
            self.refresh()
            return
        # Early confirmation so user can cancel before password prompt
        first = QMessageBox.question(
            self,
            "Delete Task",
            f"Delete \"{tpl.name}\"?",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.No,
        )
        if first != QMessageBox.Yes:
            return
        from PyQt5.QtWidgets import QInputDialog
        pwd, ok = QInputDialog.getText(self, "Password", "Enter admin password:", QLineEdit.Password)
        if not ok:
            return
        if pwd != "Admin1":
            QMessageBox.warning(self, "Unauthorized", "Incorrect password.")
            return
        prompt = QDialog(self)
        prompt.setWindowTitle("Delete Task Details")
        prompt.setMinimumWidth(360)
        form = QFormLayout()
        user_edit = QLineEdit(); reason_edit = QTextEdit()
        form.addRow("Name:", user_edit); form.addRow("Reason:", reason_edit)
        btns = QHBoxLayout(); okb = QPushButton("Delete"); cb = QPushButton("Cancel")
        okb.clicked.connect(prompt.accept); cb.clicked.connect(prompt.reject)
        btns.addStretch(1); btns.addWidget(okb); btns.addWidget(cb)
        layout = QVBoxLayout(prompt)
        layout.addLayout(form); layout.addLayout(btns)
        if prompt.exec_() != QDialog.Accepted:
            return
        user = user_edit.text().strip(); reason = reason_edit.toPlainText().strip()
        if not user or not reason:
            QMessageBox.information(self, "Missing Info", "Please provide name and reason.")
            return
        if QMessageBox.question(self, "Confirm Delete", f"Are you sure you want to delete \"{tpl.name}\"?", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        if self.owner.delete_maintenance_task(task_id, user, reason):
            self.refresh()


    def refresh(self) -> None:
        self._refresh_tasks()
        self._update_buttons()

    def _refresh_tasks(self) -> None:
        tasks = self.manager.get_tasks(self.box.uid)
        self.task_table.setRowCount(len(tasks))
        for row, tpl in enumerate(tasks):
            task_item = QTableWidgetItem(tpl.name)
            task_item.setData(Qt.UserRole, tpl.id)
            task_item.setData(Qt.UserRole + 1, tpl.box_uid)
            type_item = QTableWidgetItem("Calibration" if tpl.kind == 'calibration' else "Scheduled PM")
            due_item = QTableWidgetItem(tpl.next_due)
            repeat_item = QTableWidgetItem(f"{tpl.repeat_value} {tpl.repeat_unit}")
            status_item = QTableWidgetItem(tpl.status.replace('_', ' ').title())
            for col, item in enumerate([task_item, type_item, due_item, repeat_item, status_item]):
                self.task_table.setItem(row, col, item)
            bg = None
            if tpl.status == 'OVERDUE':
                bg = QColor(200, 60, 60)
            elif tpl.status == 'DUE':
                bg = QColor(230, 120, 60)
            elif tpl.status == 'SOON':
                bg = QColor(255, 200, 120)
            elif tpl.status == 'IN_PROGRESS':
                bg = QColor(100, 140, 200)
            if bg:
                for col in range(self.task_table.columnCount()):
                    item = self.task_table.item(row, col)
                    if item:
                        item.setBackground(bg)

    
class MachineCommentsTab(QWidget):
    def __init__(self, manager: MaintenanceManager, owner, box: BoxConfig) -> None:
        super().__init__()
        self.manager = manager
        self.owner = owner
        self.box = box

        layout = QVBoxLayout(self)
        self.comment_list = QListWidget()
        layout.addWidget(self.comment_list)
        form = QFormLayout()
        self.comment_user = QLineEdit()
        self.comment_text = QTextEdit()
        form.addRow("User:", self.comment_user)
        form.addRow("Comment:", self.comment_text)
        layout.addLayout(form)
        add_comment_btn = QPushButton("Add Comment")
        add_comment_btn.clicked.connect(self._add_comment)
        layout.addWidget(add_comment_btn)
        self.refresh()

    def _add_comment(self) -> None:
        comment = self.comment_text.toPlainText().strip()
        user = self.comment_user.text().strip()
        if not comment:
            QMessageBox.information(self, "Comment", "Please enter a comment before saving.")
            return
        self.owner.add_maintenance_comment(self.box, comment, user)
        self.comment_text.clear()
        self.refresh()

    def refresh(self) -> None:
        self.comment_list.clear()
        for entry in self.manager.get_comments(self.box.uid):
            prefix = entry.timestamp
            if entry.user:
                prefix += f" - {entry.user}"
            text = f"{prefix}: {entry.comment}"
            self.comment_list.addItem(QListWidgetItem(text))
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
        export_btn = QPushButton("Exportâ€¦")
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




















