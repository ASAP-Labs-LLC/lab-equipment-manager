# File: csv_parser_app/dialogs/advanced_com_settings_dialog.py

from PyQt5.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QPushButton, QHBoxLayout
)
from PyQt5.QtCore import Qt
from typing import Dict

class AdvancedCOMSettingsDialog(QDialog):
    """Dialog for advanced COM settings."""

    def __init__(self, existing_settings: Dict = None):
        super().__init__()
        self.setWindowTitle("Advanced COM Settings")
        self.settings = existing_settings.copy() if existing_settings else {}
        self.init_ui()

    def init_ui(self) -> None:
        layout = QFormLayout()

        self.parity_input = QLineEdit(self.settings.get('parity', 'N'))
        self.stop_bits_input = QLineEdit(str(self.settings.get('stop_bits', 1)))
        self.byte_size_input = QLineEdit(str(self.settings.get('byte_size', 8)))
        self.timeout_input = QLineEdit(str(self.settings.get('timeout', 1)))

        layout.addRow("Parity (N, E, O):", self.parity_input)
        layout.addRow("Stop Bits (1, 1.5, 2):", self.stop_bits_input)
        layout.addRow("Byte Size (5, 6, 7, 8):", self.byte_size_input)
        layout.addRow("Timeout (seconds):", self.timeout_input)

        # Save and Cancel Buttons
        buttons_layout = QHBoxLayout()
        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_settings)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        buttons_layout.addWidget(save_button)
        buttons_layout.addWidget(cancel_button)
        layout.addRow(buttons_layout)

        self.setLayout(layout)

    def save_settings(self) -> None:
        self.settings['parity'] = self.parity_input.text().strip()
        self.settings['stop_bits'] = float(self.stop_bits_input.text().strip())
        self.settings['byte_size'] = int(self.byte_size_input.text().strip())
        self.settings['timeout'] = float(self.timeout_input.text().strip())
        self.accept()

    def get_settings(self) -> Dict:
        return self.settings
