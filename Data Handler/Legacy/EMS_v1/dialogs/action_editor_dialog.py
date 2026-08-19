# File: csv_parser_app/dialogs/action_editor_dialog.py

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QComboBox, QFormLayout,
    QLineEdit, QTextEdit, QPushButton, QHBoxLayout, QMessageBox
)
from PyQt5.QtCore import Qt
from typing import Dict, List

class ActionEditorDialog(QDialog):
    """Dialog to add or edit a single data action."""

    def __init__(self, action: Dict = None):
        super().__init__()
        self.setWindowTitle("Edit Action")
        self.action = action.copy() if action else {}
        self.init_ui()

    def init_ui(self) -> None:
        layout = QVBoxLayout()

        # Action Type
        action_type_label = QLabel("Action Type:")
        self.action_type_combo = QComboBox()
        self.action_type_combo.addItems(["force_to_cell", "reorder", "remove", "math_operations"])
        layout.addWidget(action_type_label)
        layout.addWidget(self.action_type_combo)

        # Parameters Layout
        self.parameters_layout = QVBoxLayout()
        layout.addLayout(self.parameters_layout)

        self.action_type_combo.currentTextChanged.connect(self.update_parameters_fields)
        self.update_parameters_fields(self.action_type_combo.currentText())

        # Save and Cancel Buttons
        buttons_layout = QHBoxLayout()
        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_action)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        buttons_layout.addWidget(save_button)
        buttons_layout.addWidget(cancel_button)
        layout.addLayout(buttons_layout)

        self.setLayout(layout)

        # If editing existing action, populate fields
        if self.action:
            self.populate_fields()

    def update_parameters_fields(self, action_type: str) -> None:
        """Update the parameters fields based on the selected action type."""
        # Clear previous parameter fields
        while self.parameters_layout.count():
            item = self.parameters_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            else:
                sub_layout = item.layout()
                if sub_layout is not None:
                    while sub_layout.count():
                        sub_item = sub_layout.takeAt(0)
                        sub_widget = sub_item.widget()
                        if sub_widget is not None:
                            sub_widget.deleteLater()

        if action_type == "reorder":
            form_layout = QFormLayout()
            self.order_input = QLineEdit()
            form_layout.addRow("New Order (comma-separated column indices):", self.order_input)
            self.parameters_layout.addLayout(form_layout)
        elif action_type == "remove":
            form_layout = QFormLayout()
            self.substring_input = QLineEdit()
            form_layout.addRow("Substring to Remove:", self.substring_input)
            self.parameters_layout.addLayout(form_layout)
        elif action_type == "force_to_cell":
            form_layout = QFormLayout()
            self.substring_input = QLineEdit()
            self.target_column_input = QLineEdit()
            form_layout.addRow("Substring to Detect:", self.substring_input)
            form_layout.addRow("Target Column (1-based index):", self.target_column_input)
            self.parameters_layout.addLayout(form_layout)
        elif action_type == "math_operations":
            form_layout = QFormLayout()
            self.operations_input = QTextEdit()
            form_layout.addRow("Operations (one per line):", self.operations_input)
            self.parameters_layout.addLayout(form_layout)

    def populate_fields(self) -> None:
        """Populate fields with existing action data."""
        action_type = self.action.get('action', '')
        self.action_type_combo.setCurrentText(action_type)

        if action_type == "reorder":
            order = self.action.get('order', [])
            self.order_input.setText(','.join(map(str, order)))
        elif action_type == "remove":
            substring = self.action.get('substring', '')
            self.substring_input.setText(substring)
        elif action_type == "force_to_cell":
            substring = self.action.get('substring', '')
            target_column = self.action.get('target_column', '')
            self.substring_input.setText(substring)
            self.target_column_input.setText(str(target_column))
        elif action_type == "math_operations":
            operations = self.action.get('operations', [])
            self.operations_input.setText('\n'.join(operations))

    def save_action(self) -> None:
        """Save the action data."""
        action_type = self.action_type_combo.currentText()
        self.action['action'] = action_type

        if action_type == "reorder":
            order_text = self.order_input.text()
            try:
                order = [int(idx.strip()) for idx in order_text.split(',') if idx.strip()]
                self.action['order'] = order
            except ValueError:
                QMessageBox.warning(self, "Invalid Input", "Please enter valid comma-separated integers for the new order.")
                return
        elif action_type == "remove":
            substring = self.substring_input.text().strip()
            if not substring:
                QMessageBox.warning(self, "Invalid Input", "Substring cannot be empty.")
                return
            self.action['substring'] = substring
        elif action_type == "force_to_cell":
            substring = self.substring_input.text().strip()
            target_column = self.target_column_input.text().strip()
            if not substring or not target_column:
                QMessageBox.warning(self, "Invalid Input", "Substring and Target Column cannot be empty.")
                return
            if not target_column.isdigit():
                QMessageBox.warning(self, "Invalid Input", "Target Column must be an integer.")
                return
            self.action['substring'] = substring
            self.action['target_column'] = int(target_column)
        elif action_type == "math_operations":
            operations_text = self.operations_input.toPlainText()
            operations = [line.strip() for line in operations_text.split('\n') if line.strip()]
            self.action['operations'] = operations

        self.accept()

    def get_action(self) -> Dict:
        """Return the action data."""
        return self.action
