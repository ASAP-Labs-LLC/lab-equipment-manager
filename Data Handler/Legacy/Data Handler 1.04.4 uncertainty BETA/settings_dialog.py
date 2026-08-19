import os
import shutil
import threading
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QHBoxLayout, QSlider,
    QComboBox, QPushButton, QFileDialog, QMessageBox, QLineEdit, QCheckBox, qApp
)
from PyQt5.QtCore import Qt
import logging
from settings_manager import SettingsManager

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
LOG_LOCK = threading.Lock()

def log_message(level, message):
    with LOG_LOCK:
        if level == 'info':
            logger.info(message)
        elif level == 'warning':
            logger.warning(message)
        elif level == 'error':
            logger.error(message)
        else:
            logger.debug(message)

CONFIG_DIR = os.path.join(os.path.expanduser('~'), 'csv_parser_configs')
THEMES_DIR = os.path.join(CONFIG_DIR, 'themes')

class SettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__()
        self.setWindowTitle("Settings")
        self.parent = parent
        self.scale_factor = parent.scale_factor
        self.current_theme = parent.current_theme
        self.settings_manager = parent.settings_manager  # We assume the MainWindow has a settings_manager

        self.iso_uncertainty_cfg = self.settings_manager.get_setting("iso_uncertainty", {
            "enabled": False,
            "keyword": "",
            "column_name": "",
            "output_directory": ""
        })

        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # Scale factor controls (existing)
        scale_layout = QHBoxLayout()
        scale_label = QLabel("Scale:")
        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setMinimum(50)
        self.scale_slider.setMaximum(150)
        self.scale_slider.setValue(int(self.scale_factor * 100))
        self.scale_slider.setTickInterval(10)
        self.scale_slider.setTickPosition(QSlider.TicksBelow)
        self.scale_value_label = QLabel(f"{self.scale_slider.value()}%")
        self.scale_slider.valueChanged.connect(self.scale_changed)
        scale_layout.addWidget(scale_label)
        scale_layout.addWidget(self.scale_slider)
        scale_layout.addWidget(self.scale_value_label)
        layout.addLayout(scale_layout)

        # Theme controls (existing)
        theme_layout = QHBoxLayout()
        theme_label = QLabel("Theme:")
        self.theme_combo = QComboBox()
        self.load_themes()
        self.theme_combo.setCurrentText(self.current_theme)
        theme_layout.addWidget(theme_label)
        theme_layout.addWidget(self.theme_combo)
        layout.addLayout(theme_layout)

        # Custom Theme button (existing)
        custom_theme_button = QPushButton("Load Custom Theme")
        custom_theme_button.clicked.connect(self.load_custom_theme)
        layout.addWidget(custom_theme_button)

        # --- NEW: ISO Uncertainty Section ---
        iso_label = QLabel("ISO Uncertainty Settings:")
        iso_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(iso_label)

        self.iso_enabled_checkbox = QCheckBox("Enable ISO Uncertainty Feature")
        self.iso_enabled_checkbox.setChecked(self.iso_uncertainty_cfg.get("enabled", False))
        layout.addWidget(self.iso_enabled_checkbox)

        # Keyword
        kw_layout = QHBoxLayout()
        kw_label = QLabel("Keyword to Look For:")
        self.keyword_input = QLineEdit(self.iso_uncertainty_cfg.get("keyword", ""))
        kw_layout.addWidget(kw_label)
        kw_layout.addWidget(self.keyword_input)
        layout.addLayout(kw_layout)

        # Column name
        col_layout = QHBoxLayout()
        col_label = QLabel("Column Name:")
        self.column_input = QLineEdit(self.iso_uncertainty_cfg.get("column_name", ""))
        col_layout.addWidget(col_label)
        col_layout.addWidget(self.column_input)
        layout.addLayout(col_layout)

        # Output directory
        out_layout = QHBoxLayout()
        out_label = QLabel("Output Directory for XLSX:")
        self.output_dir_edit = QLineEdit(self.iso_uncertainty_cfg.get("output_directory", ""))
        out_browse = QPushButton("Browse")
        out_browse.clicked.connect(self.browse_output_directory)
        out_layout.addWidget(out_label)
        out_layout.addWidget(self.output_dir_edit)
        out_layout.addWidget(out_browse)
        layout.addLayout(out_layout)

        # Save/Cancel
        buttons_layout = QHBoxLayout()
        apply_button = QPushButton("Apply")
        apply_button.clicked.connect(self.apply_settings)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        buttons_layout.addWidget(apply_button)
        buttons_layout.addWidget(cancel_button)
        layout.addLayout(buttons_layout)

        self.setLayout(layout)

    def browse_output_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Directory")
        if directory:
            self.output_dir_edit.setText(directory)

    def scale_changed(self, value):
        self.scale_value_label.setText(f"{value}%")

    def load_themes(self):
        if not os.path.exists(THEMES_DIR):
            os.makedirs(THEMES_DIR)
        theme_files = [f for f in os.listdir(THEMES_DIR) if f.endswith('.css')]
        self.theme_combo.addItems(theme_files)

    def load_custom_theme(self):
        theme_file, _ = QFileDialog.getOpenFileName(self, "Select Theme File", "", "CSS Files (*.css)")
        if theme_file:
            try:
                import shutil
                theme_name = os.path.basename(theme_file)
                destination = os.path.join(THEMES_DIR, theme_name)
                shutil.copyfile(theme_file, destination)
                self.theme_combo.addItem(theme_name)
                self.theme_combo.setCurrentText(theme_name)
                log_message('info', f"Loaded custom theme: {theme_name}")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to load custom theme: {e}")
                log_message('error', f"Failed to load custom theme '{theme_file}': {e}")

    def apply_settings(self):
        # Scale
        scale_value = self.scale_slider.value()
        self.parent.scale_factor = scale_value / 100.0
        self.parent.apply_scale()

        # Theme
        selected_theme = self.theme_combo.currentText()
        self.parent.settings_manager.set_setting("current_theme", selected_theme)
        self.parent.current_theme = selected_theme
        self.parent.apply_theme()

        # ISO Uncertainty
        self.iso_uncertainty_cfg["enabled"] = self.iso_enabled_checkbox.isChecked()
        self.iso_uncertainty_cfg["keyword"] = self.keyword_input.text().strip()
        self.iso_uncertainty_cfg["column_name"] = self.column_input.text().strip()
        self.iso_uncertainty_cfg["output_directory"] = self.output_dir_edit.text().strip()
        self.setStyleSheet(qApp.styleSheet())

        self.parent.settings_manager.set_setting("iso_uncertainty", self.iso_uncertainty_cfg)

        self.accept()
