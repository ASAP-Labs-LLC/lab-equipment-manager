# File: csv_parser_app/dialogs/settings_dialog.py

import os
import shutil
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QHBoxLayout, QSlider, QComboBox,
    QPushButton, QFileDialog, QMessageBox
)
from PyQt5.QtCore import Qt
from typing import TYPE_CHECKING
from ..utils.logging_utils import log_message

if TYPE_CHECKING:
    from ..ui.main_window import MainWindow

CONFIG_DIR = os.path.join(os.path.expanduser('~'), 'csv_parser_configs')
THEMES_DIR = os.path.join(CONFIG_DIR, 'themes')

class SettingsDialog(QDialog):
    """Dialog to adjust settings like scale and theme."""

    def __init__(self, parent: 'MainWindow'):
        super().__init__()
        self.setWindowTitle("Settings")
        self.parent = parent
        self.scale_factor = parent.scale_factor
        self.current_theme = parent.current_theme
        self.init_ui()

    def init_ui(self) -> None:
        layout = QVBoxLayout()

        # Scale Slider
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

        # Theme Selector
        theme_layout = QHBoxLayout()
        theme_label = QLabel("Theme:")
        self.theme_combo = QComboBox()
        self.load_themes()
        self.theme_combo.setCurrentText(self.current_theme)
        theme_layout.addWidget(theme_label)
        theme_layout.addWidget(self.theme_combo)
        layout.addLayout(theme_layout)

        # Custom Theme Button
        custom_theme_button = QPushButton("Load Custom Theme")
        custom_theme_button.clicked.connect(self.load_custom_theme)
        layout.addWidget(custom_theme_button)

        # Apply and Cancel Buttons
        buttons_layout = QHBoxLayout()
        apply_button = QPushButton("Apply")
        apply_button.clicked.connect(self.apply_settings)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        buttons_layout.addWidget(apply_button)
        buttons_layout.addWidget(cancel_button)
        layout.addLayout(buttons_layout)

        self.setLayout(layout)

    def scale_changed(self, value: int) -> None:
        self.scale_value_label.setText(f"{value}%")

    def load_themes(self) -> None:
        if not os.path.exists(THEMES_DIR):
            os.makedirs(THEMES_DIR)
        theme_files = [f for f in os.listdir(THEMES_DIR) if f.endswith('.css')]
        self.theme_combo.addItems(theme_files)

    def load_custom_theme(self) -> None:
        theme_file, _ = QFileDialog.getOpenFileName(self, "Select Theme File", "", "CSS Files (*.css)")
        if theme_file:
            try:
                theme_name = os.path.basename(theme_file)
                destination = os.path.join(THEMES_DIR, theme_name)
                shutil.copyfile(theme_file, destination)
                self.theme_combo.addItem(theme_name)
                self.theme_combo.setCurrentText(theme_name)
                log_message('info', f"Loaded custom theme: {theme_name}")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to load custom theme: {e}")
                log_message('error', f"Failed to load custom theme '{theme_file}': {e}")

    def apply_settings(self) -> None:
        # Apply scale
        scale_value = self.scale_slider.value()
        self.parent.scale_factor = scale_value / 100.0
        self.parent.apply_scale()

        # Apply theme
        selected_theme = self.theme_combo.currentText()
        self.parent.current_theme = selected_theme
        self.parent.apply_theme()

        # Save settings
        self.parent.settings_manager.set_setting('scale_factor', self.parent.scale_factor)
        self.parent.settings_manager.set_setting('current_theme', self.parent.current_theme)

        self.accept()
