# File: csv_parser_app/dialogs/data_actions_dialog.py

import json
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QListWidget, QListWidgetItem,
    QHBoxLayout, QPushButton, QMessageBox
)
from PyQt5.QtCore import Qt
from typing import List, Dict
from .action_editor_dialog import ActionEditorDialog

class DataActionsDialog(QDialog):
    """Dialog to edit data actions."""

    def __init__(self, existing_actions: List[Dict] = None):
        super().__init__()
        self.setWindowTitle("Edit Data Actions")
        self.actions = existing_actions.copy() if existing_actions else []
        self.init_ui()

    def init_ui(self) -> None:
        layout = QVBoxLayout()

        # List of actions
        self.actions_list = QListWidget()
        for action in self.actions:
            item = QListWidgetItem(json.dumps(action))
            self.actions_list.addItem(item)
        layout.addWidget(self.actions_list)

        # Buttons to add, edit, remove actions
        buttons_layout = QHBoxLayout()
        add_button = QPushButton("Add Action")
        edit_button = QPushButton("Edit Action")
        remove_button = QPushButton("Remove Action")
        buttons_layout.addWidget(add_button)
        buttons_layout.addWidget(edit_button)
        buttons_layout.addWidget(remove_button)
        layout.addLayout(buttons_layout)

        add_button.clicked.connect(self.add_action)
        edit_button.clicked.connect(self.edit_action)
        remove_button.clicked.connect(self.remove_action)

        # Save and Cancel Buttons
        save_cancel_layout = QHBoxLayout()
        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_actions)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        save_cancel_layout.addWidget(save_button)
        save_cancel_layout.addWidget(cancel_button)
        layout.addLayout(save_cancel_layout)

        self.setLayout(layout)

    def add_action(self) -> None:
        dialog = ActionEditorDialog()
        if dialog.exec_():
            action = dialog.get_action()
            self.actions.append(action)
            self.actions_list.addItem(QListWidgetItem(json.dumps(action)))

    def edit_action(self) -> None:
        selected_items = self.actions_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select an action to edit.")
            return
        selected_index = self.actions_list.row(selected_items[0])
        action = self.actions[selected_index]
        dialog = ActionEditorDialog(action)
        if dialog.exec_():
            updated_action = dialog.get_action()
            self.actions[selected_index] = updated_action
            self.actions_list.item(selected_index).setText(json.dumps(updated_action))

    def remove_action(self) -> None:
        selected_items = self.actions_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select an action to remove.")
            return
        selected_index = self.actions_list.row(selected_items[0])
        self.actions.pop(selected_index)
        self.actions_list.takeItem(selected_index)

    def save_actions(self) -> None:
        self.accept()

    def get_actions(self) -> List[Dict]:
        return self.actions
