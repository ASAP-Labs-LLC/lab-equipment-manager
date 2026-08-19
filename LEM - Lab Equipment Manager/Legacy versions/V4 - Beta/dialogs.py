#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dialogs.py â€” Settings (Tests + Daily Report), Box Editor, Info dialog, and Report Preview dialog.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from typing import List, Optional, Tuple, Dict

from PyQt5.QtCore import Qt, QTime, QDate
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QDoubleSpinBox, QPushButton,
    QHBoxLayout, QVBoxLayout, QLabel, QWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QMessageBox, QComboBox, QCheckBox, QTimeEdit, QGroupBox, QListWidget,
    QTabWidget, QDateEdit, QSpinBox, QTextEdit, QListWidgetItem, QAbstractItemView, QSlider, QInputDialog
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
                 correction_factor_dir: str = "",
                 theme_mode: str = "light",
                 app_font_family: str = "",
                 app_font_size: int = 10,
                 custom_qss_path: str = "",
                 ui_scale: float = 1.0,
                 samples_editable: bool = True) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(1024)
        self._samples = [SampleSpec.from_dict(s.serialize()) for s in samples]  # copy
        self._sample_id_column = sample_id_column or "Lab ID"
        self._current_index = -1
        self._reassignments: List[Tuple[str, str]] = []  # (old_sample_name, new_sample_name)

        root = QVBoxLayout()
        tabs = QTabWidget()

        # ---- Sample manager (separate tab)
        samples_page = QWidget()
        samples_box = QGroupBox("Sample Manager")
        samples_layout = QVBoxLayout()
        # Unlock button for Sample Manager
        unlock_row = QHBoxLayout()
        unlock_row.addStretch(1)
        unlock_btn = QPushButton("Unlock")
        def try_unlock():
            from PyQt5.QtWidgets import QInputDialog
            pwd, ok = QInputDialog.getText(self, "Password", "Enter admin password:", QLineEdit.Password)
            if ok and pwd == "Admin1":
                self._set_sample_manager_enabled(True)
        unlock_btn.clicked.connect(try_unlock)
        unlock_row.addWidget(unlock_btn)
        samples_layout.addLayout(unlock_row)
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
        self.changeover_btn = QPushButton("Changeover QC")
        self.changeover_btn.clicked.connect(self._changeover_qc)
        sample_btns.addWidget(self.add_sample_btn)
        sample_btns.addWidget(self.remove_sample_btn)
        sample_btns.addWidget(self.changeover_btn)
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

        # Lock down Sample Manager if not editable (admin gate)
        self._sample_manager_controls = [
            lambda en: self.sample_id_column_edit.setEnabled(en),
            lambda en: self.sample_list.setEnabled(en),
            lambda en: self.add_sample_btn.setEnabled(en),
            lambda en: self.remove_sample_btn.setEnabled(en),
            lambda en: self.changeover_btn.setEnabled(en),
            lambda en: self.sample_name_edit.setEnabled(en),
            lambda en: self.sample_id_val_edit.setEnabled(en),
            lambda en: self.tests_table.setEnabled(en),
            lambda en: self.add_test_btn.setEnabled(en),
            lambda en: self.remove_test_btn.setEnabled(en),
        ]
        self._set_sample_manager_enabled(bool(samples_editable))

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

        # ---- Actions (map lock, layout, report)
        act_box = QGroupBox("Actions")
        a_layout = QFormLayout()
        self.map_lock_cb = QCheckBox("Lock Map")
        # default unchecked; caller should read and apply on accept
        a_layout.addRow(self.map_lock_cb)
        hl = QHBoxLayout();
        preview = QPushButton("Preview Report"); export_now = QPushButton("Export Now")
        def _preview():
            try:
                self.parent().preview_report()
            except Exception:
                pass
        def _export():
            try:
                self.parent().export_report_now()
            except Exception:
                pass
        preview.clicked.connect(_preview); export_now.clicked.connect(_export)
        hl.addWidget(preview); hl.addWidget(export_now)
        a_layout.addRow("Report:", hl)
        hl2 = QHBoxLayout();
        save_layout_btn = QPushButton("Save Layout"); restore_layout_btn = QPushButton("Restore Layout")
        def _save_layout():
            try:
                self.parent().save_config()
            except Exception:
                pass
        def _restore_layout():
            try:
                self.parent().restore_layout()
            except Exception:
                pass
        save_layout_btn.clicked.connect(_save_layout); restore_layout_btn.clicked.connect(_restore_layout)
        hl2.addWidget(save_layout_btn); hl2.addWidget(restore_layout_btn)
        a_layout.addRow("Layout:", hl2)
        act_box.setLayout(a_layout)
        gen_root.addWidget(act_box)

        # ---- Status change logging
        log_box = QGroupBox("Status Change Log")
        l_form = QFormLayout()
        lh = QHBoxLayout()
        self.status_dir = QLineEdit(status_log_dir or "")
        self.status_dir.setReadOnly(True)
        lbrowse = QPushButton("Browse...")
        def pick_status_dir():
            if not self._require_admin_password("Enter admin password to change the status log folder."):
                return
            d = QFileDialog.getExistingDirectory(self, "Select Status Log Folder")
            if d:
                self.status_dir.setText(d)
        lbrowse.clicked.connect(pick_status_dir)
        lh.addWidget(self.status_dir); lh.addWidget(lbrowse)
        l_form.addRow("Log folder:", lh)
        log_box.setLayout(l_form)
        gen_root.addWidget(log_box)

        # ---- Correction factors
        cf_box = QGroupBox("Correction Factors")
        cf_form = QFormLayout()
        ch = QHBoxLayout()
        self.correction_dir = QLineEdit(correction_factor_dir or "")
        self.correction_dir.setReadOnly(True)
        cbrowse = QPushButton("Browse...")
        def pick_correction_dir():
            if not self._require_admin_password("Enter admin password to change the correction factor folder."):
                return
            d = QFileDialog.getExistingDirectory(self, "Select Correction Factor Base Folder")
            if not d:
                return
            target = self._correction_subdir_path(d)
            try:
                os.makedirs(target, exist_ok=True)
            except Exception as exc:
                QMessageBox.critical(
                    self,
                    "Folder Error",
                    f"Unable to create correction factor folder:\n{target}\n\n{type(exc).__name__}: {exc}"
                )
                return
            self.correction_dir.setText(target)
        cbrowse.clicked.connect(pick_correction_dir)
        ch.addWidget(self.correction_dir); ch.addWidget(cbrowse)
        cf_form.addRow("Folder:", ch)
        cf_box.setLayout(cf_form)
        gen_root.addWidget(cf_box)

    def get_report_settings(self) -> Tuple[bool, str, str]:
        return (
            bool(self.rep_enable.isChecked()),
            self.rep_time.time().toString("HH:mm"),
            self.rep_dir.text().strip(),
        )

    def get_status_log_dir(self) -> str:
        return self.status_dir.text().strip()

    def get_correction_factor_dir(self) -> str:
        return self.correction_dir.text().strip()

    # ---- admin helpers ----
    def _require_admin_password(self, message: str) -> bool:
        pwd, ok = QInputDialog.getText(self, "Admin Password", message, QLineEdit.Password)
        if not ok:
            return False
        if pwd != "Admin1":
            QMessageBox.warning(self, "Access Denied", "Incorrect admin password.")
            return False
        return True

    def _correction_subdir_path(self, base_dir: str) -> str:
        target_name = "EQM_Correction Factor"
        try:
            candidate = os.path.basename(os.path.normpath(base_dir))
        except Exception:
            candidate = ""
        if candidate.lower() == target_name.lower():
            return base_dir
        return os.path.join(base_dir, target_name)

    def get_theme_mode(self) -> str:
        label = self.theme_cb.currentText()
        tag = self._theme_map.get(label, '')
        if tag == 'builtin:dark':
            return 'dark'
        if tag == 'builtin:light':
            return 'light'
        return 'custom'

    def get_custom_qss_path(self) -> str:
        label = self.theme_cb.currentText()
        tag = self._theme_map.get(label, '')
        if tag.startswith('builtin:'):
            return self._custom_qss_path
        return tag or self._custom_qss_path

    def get_ui_scale(self) -> float:
        return max(0.5, min(2.0, self.scale_slider.value() / 100.0))

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
        self._owner = owner
        self._box = box
        self._read_only = bool(getattr(owner, "view_only", False))
        self._evaluation = eval
        self._corrections = self._load_corrections()
        self.setWindowTitle(f"Info - {box.title}")
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
        if getattr(eval, "from_cache", False):
            if eval.latest_match_time:
                detail_layout.addWidget(QLabel(
                    f"<b>Last detected sample:</b> {eval.latest_match_time.strftime('%Y-%m-%d %H:%M')} (cached)"
                ))
            else:
                detail_layout.addWidget(QLabel("<b>Data source:</b> Cached results (no current CSV rows)"))

        last_qc = eval.last_good_qc.isoformat(sep=' ') if eval.last_good_qc else '-'
        latest_row_t = eval.latest_match_time.isoformat(sep=' ') if eval.latest_match_time else '-'
        detail_layout.addWidget(QLabel(f"<b>Last in-spec QC time:</b> {last_qc}"))
        detail_layout.addWidget(QLabel(f"<b>Latest matching row time:</b> {latest_row_t}"))

        self._correction_section = QWidget()
        self._correction_layout = QVBoxLayout(self._correction_section)
        self._correction_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(self._correction_section)
        self._update_correction_summary()

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.create_cf_btn = QPushButton("Create Correction Factor")
        self.create_cf_btn.clicked.connect(self._create_correction_factor)
        if self._read_only:
            self.create_cf_btn.setEnabled(False)
            self.create_cf_btn.setToolTip("Viewer mode is read-only")
        btn_row.addWidget(self.create_cf_btn)
        detail_layout.addLayout(btn_row)

        self._table = QTableWidget(0, 7)
        table = self._table
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
                table.setItem(row, 1, self._make_center_item(f"{pr.test.expected:.6g}{units}"))
                table.setItem(row, 2, self._make_center_item(f"{tol:.6g}"))
                table.setItem(row, 3, self._make_center_item(rng))
                table.setItem(row, 4, self._make_center_item(latest))
                table.setItem(row, 5, self._make_center_item(insp))
                note = pr.note or ''
                if getattr(pr, 'from_cache', False):
                    note = f"{note} (cached)" if note else '(cached)'
                ts_source = getattr(pr, 'timestamp_source', '')
                if ts_source:
                    note = f"{note} [ts:{ts_source}]" if note else f"ts:{ts_source}"
                table.setItem(row, 6, QTableWidgetItem(note))
            else:
                label = pr.sample or '(missing sample)'
                table.setItem(row, 0, QTableWidgetItem(f"{label} / (missing test)"))
                note = pr.note or ''
                if getattr(pr, 'from_cache', False):
                    note = f"{note} (cached)" if note else '(cached)'
                ts_source = getattr(pr, 'timestamp_source', '')
                if ts_source:
                    note = f"{note} [ts:{ts_source}]" if note else f"ts:{ts_source}"
                table.setItem(row, 6, QTableWidgetItem(note))
        detail_layout.addWidget(table)
        try:
            self.create_cf_btn.setEnabled(bool(self._collect_available_tests()))
        except Exception:
            self.create_cf_btn.setEnabled(False)
        tabs.addTab(details, "Details")

        self.maintenance_tab = MachineMaintenanceTab(maintenance, owner, box, read_only=self._read_only)
        tabs.addTab(self.maintenance_tab, "Maintenance")
        self.comments_tab = MachineCommentsTab(maintenance, owner, box, read_only=self._read_only)
        tabs.addTab(self.comments_tab, "Comments")

        layout = QVBoxLayout()
        layout.addWidget(tabs)
        btns = QHBoxLayout()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        btns.addStretch(1); btns.addWidget(close)
        layout.addLayout(btns)
        self.setLayout(layout)

    def _make_center_item(self, text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        return item

    def _load_corrections(self) -> Dict[str, float]:
        owner = getattr(self, "_owner", None)
        cfg = getattr(owner, "cfg", None) if owner else None
        directory = ""
        if cfg is not None:
            directory = str(getattr(cfg, "correction_factor_dir", "") or "").strip()
        if not directory:
            return {}
        json_path = os.path.join(directory, "correction_factors.json")
        if not os.path.exists(json_path):
            return {}
        machine_name = getattr(self._box, "title", "") or getattr(self._box, "uid", "")
        try:
            with open(json_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            machine_entry = raw.get(machine_name, {})
            if not isinstance(machine_entry, dict):
                return {}
            out: Dict[str, float] = {}
            for test_name, payload in machine_entry.items():
                if not isinstance(payload, dict):
                    continue
                try:
                    out[test_name] = float(payload.get("correction_value"))
                except Exception:
                    continue
            return out
        except Exception:
            return {}

    def _require_admin_password(self) -> bool:
        pwd, ok = QInputDialog.getText(
            self,
            "Admin Password",
            "Enter admin password to modify correction factors:",
            QLineEdit.Password
        )
        if not ok:
            return False
        if pwd != "Admin1":
            QMessageBox.warning(self, "Access Denied", "Incorrect admin password.")
            return False
        return True

    def _update_correction_summary(self) -> None:
        section = getattr(self, "_correction_section", None)
        layout = getattr(self, "_correction_layout", None)
        if section is None or layout is None:
            return

        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        if not self._corrections:
            section.hide()
            return

        labels_by_test: Dict[str, str] = {}
        for pr in getattr(self._evaluation, "results", []):
            if getattr(pr, "test", None):
                labels_by_test[pr.test.name] = f"{pr.sample} / {pr.test.name}"

        header_added = False
        for test_name, value in sorted(self._corrections.items()):
            label = labels_by_test.get(test_name, test_name)
            if not header_added:
                layout.addWidget(QLabel("<b>Correction factors:</b>"))
                header_added = True
            layout.addWidget(QLabel(f"{label}: {value:.6g}"))

        section.setVisible(header_added)

    def _collect_available_tests(self) -> Dict[str, Tuple[SampleSpec, SampleTestSpec]]:
        owner = getattr(self, "_owner", None)
        box = getattr(self, "_box", None)
        results: Dict[str, Tuple[SampleSpec, SampleTestSpec]] = {}
        if owner is None or box is None:
            return results
        samples_by_name = getattr(owner, "samples_by_name", {})
        for target in getattr(box, "watched_targets", []):
            sample = samples_by_name.get(getattr(target, "sample", ""))
            if sample is None:
                continue
            tests_map = sample.tests_by_name()
            test_spec = tests_map.get(getattr(target, "test", ""))
            if test_spec is None:
                continue
            label = f"{sample.name} / {test_spec.name}"
            if label not in results:
                results[label] = (sample, test_spec)
        return results

    def _create_correction_factor(self) -> None:
        owner = getattr(self, "_owner", None)
        if owner is None:
            return
        cfg = getattr(owner, "cfg", None)
        correction_dir = ""
        if cfg is not None:
            correction_dir = str(getattr(cfg, "correction_factor_dir", "") or "").strip()
        if not correction_dir:
            QMessageBox.warning(
                self,
                "Correction Folder Not Set",
                "Please set the correction factor folder path in Settings before creating a correction factor."
            )
            return
        try:
            os.makedirs(correction_dir, exist_ok=True)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Folder Error",
                f"Unable to access the correction factor folder:\n{correction_dir}\n\n{type(exc).__name__}: {exc}"
            )
            return

        tests = self._collect_available_tests()
        if not tests:
            QMessageBox.information(self, "No Monitored Tests", "No monitored tests are available for this machine.")
            return

        if not self._require_admin_password():
            return

        labels = sorted(tests.keys())
        if len(labels) == 1:
            selected_label = labels[0]
        else:
            selected_label, ok = QInputDialog.getItem(
                self,
                "Select Test",
                "Choose which test to apply a correction factor to:",
                labels,
                0,
                False
            )
            if not ok or not selected_label:
                return

        sample, test_spec = tests[selected_label]
        machine_name = getattr(self._box, "title", "") or getattr(self._box, "uid", "Machine")
        test_name = test_spec.name
        value_column = test_spec.value_col
        file_destination = getattr(self._box, "csv_path", "")

        json_path = os.path.join(correction_dir, "correction_factors.json")
        data: Dict[str, Dict[str, dict]] = {}
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                    if isinstance(raw, dict):
                        data = raw
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "Read Warning",
                    f"Existing correction data could not be read. A new file will be created.\n\n{type(exc).__name__}: {exc}"
                )
                data = {}

        machine_entry = data.get(machine_name, {})
        if not isinstance(machine_entry, dict):
            machine_entry = {}
        prev_entry = machine_entry.get(test_name, {})
        prev_value_raw = prev_entry.get("correction_value", 0.0)
        try:
            prev_value = float(prev_value_raw)
        except Exception:
            prev_value = 0.0

        value, ok = QInputDialog.getDouble(
            self,
            "Correction Factor",
            f"Enter the correction factor for {selected_label}:",
            prev_value,
            -1_000_000.0,
            1_000_000.0,
            6
        )
        if not ok:
            return

        new_value = float(value)
        machine_entry[test_name] = {
            "equipment": machine_name,
            "test": test_name,
            "value_column": value_column,
            "file_destination": file_destination,
            "correction_value": new_value
        }
        data[machine_name] = machine_entry

        try:
            with open(json_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Save Error",
                f"Failed to save correction factors:\n{json_path}\n\n{type(exc).__name__}: {exc}"
            )
            return
        self._corrections[test_name] = new_value

        latest_value = None
        evaluation = getattr(self, "_evaluation", None)
        if evaluation is not None:
            for pr in getattr(evaluation, "results", []):
                if getattr(pr, "test", None) and pr.test.name == test_name and pr.sample == sample.name:
                    latest_value = pr.latest_value
                    break

        log_path = os.path.join(correction_dir, "correction_factor_changes.csv")
        need_header = not os.path.exists(log_path)
        try:
            with open(log_path, "a", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                if need_header:
                    writer.writerow([
                        "timestamp",
                        "equipment",
                        "test",
                        "value_column",
                        "file_destination",
                        "previous_correction",
                        "new_correction",
                        "latest_result_value"
                    ])
                writer.writerow([
                    datetime.now().isoformat(timespec="seconds"),
                    machine_name,
                    test_name,
                    value_column,
                    file_destination,
                    f"{prev_value:.6g}",
                    f"{new_value:.6g}",
                    "" if latest_value is None else f"{latest_value:.6g}"
                ])
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Log Warning",
                f"Correction factor saved, but the change log could not be updated.\n{type(exc).__name__}: {exc}"
            )
        else:
            QMessageBox.information(
                self,
                "Correction Factor Saved",
                f"Correction factor for {selected_label} saved.\nJSON: {json_path}\nLog: {log_path}"
            )
        self._update_correction_summary()



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
    def __init__(self, manager: MaintenanceManager, owner, box: BoxConfig, read_only: bool = False) -> None:
        super().__init__()
        self.manager = manager
        self.owner = owner
        self.box = box
        self.read_only = bool(read_only)

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

        if self.read_only:
            for btn in (self.add_cal_btn, self.add_pm_btn, self.start_btn, self.complete_btn, self.delete_btn):
                btn.setEnabled(False)
                btn.setToolTip("Viewer mode is read-only")
            self.task_table.setToolTip("Viewer mode is read-only")

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
        if self.read_only:
            self.start_btn.setEnabled(False)
            self.complete_btn.setEnabled(False)
            return
        tid = self._selected_task_id()
        disable_start = False
        if tid:
            tpl = self.manager.templates.get(tid)
            if tpl and tpl.status == 'IN_PROGRESS':
                disable_start = True
        self.start_btn.setEnabled(not disable_start)

    def _blocked(self, action: str) -> bool:
        if not self.read_only:
            return False
        QMessageBox.information(self, "Viewer Mode", f"{action} is disabled in the viewer.")
        return True

    def _add_task(self, default_kind: str) -> None:
        if self._blocked("Adding maintenance tasks"):
            return
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
        if self._blocked("Starting maintenance tasks"):
            return
        task_id = self._selected_task_id()
        if not task_id:
            QMessageBox.information(self, "Select Task", "Please select a task to start.")
            return
        self.owner.start_maintenance_task(task_id)
        self.refresh()

    def _complete_task(self) -> None:
        if self._blocked("Completing maintenance tasks"):
            return
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
        if self._blocked("Deleting maintenance tasks"):
            return
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
    def __init__(self, manager: MaintenanceManager, owner, box: BoxConfig, read_only: bool = False) -> None:
        super().__init__()
        self.manager = manager
        self.owner = owner
        self.box = box
        self.read_only = bool(read_only)

        layout = QVBoxLayout(self)
        self.comment_list = QListWidget()
        layout.addWidget(self.comment_list)
        form = QFormLayout()
        self.comment_user = QLineEdit()
        self.comment_text = QTextEdit()
        form.addRow("User:", self.comment_user)
        form.addRow("Comment:", self.comment_text)
        layout.addLayout(form)
        self.add_comment_btn = QPushButton("Add Comment")
        self.add_comment_btn.clicked.connect(self._add_comment)
        if self.read_only:
            self.add_comment_btn.setEnabled(False)
            self.comment_user.setReadOnly(True)
            self.comment_text.setReadOnly(True)
            self.add_comment_btn.setToolTip("Viewer mode is read-only")
        layout.addWidget(self.add_comment_btn)
        self.refresh()

    def _add_comment(self) -> None:
        if self.read_only:
            QMessageBox.information(self, "Viewer Mode", "Adding comments is disabled in the viewer.")
            return
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




















