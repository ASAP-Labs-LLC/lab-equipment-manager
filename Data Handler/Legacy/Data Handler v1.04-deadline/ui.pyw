# ui.py
import sys
import os
import json
import shutil
import pandas as pd
import threading
import csv
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QFileDialog, QLineEdit, QMessageBox,
    QTabWidget, QDialog, QTableView, QInputDialog, QCheckBox, QAction,
    QListWidget, QListWidgetItem, QSlider, QTextEdit, QFormLayout
)
from PyQt5.QtCore import QAbstractTableModel, Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QFont
from data_handler import process_single_csv, process_multi_csv, process_com_port, append_to_master_csv

CONFIG_DIR = os.path.join(os.path.expanduser('~'), 'csv_parser_configs')
THEMES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'themes')

# Ensure that the CONFIG_DIR exists
os.makedirs(CONFIG_DIR, exist_ok=True)

# ============================
# Global Utility Functions
# ============================

def generate_default_headers(count=80):
    """Generate a list of default headers."""
    return [f"Column{i}" for i in range(1, count + 1)]

def load_json_config(config_path):
    """Load JSON configuration from a file."""
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Failed to load config {config_path}: {e}")
        return {}

def save_json_config(config_path, config_data):
    """Save JSON configuration to a file."""
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=4)
    except Exception as e:
        print(f"Failed to save config {config_path}: {e}")

# ============================
# Pandas Model for QTableView
# ============================

class PandasModel(QAbstractTableModel):
    """A model to interface a pandas DataFrame with QTableView."""
    def __init__(self, data):
        super(PandasModel, self).__init__()
        self._data = data

    def rowCount(self, parent=None):
        return len(self._data.index)

    def columnCount(self, parent=None):
        return len(self._data.columns)

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid():
            if role == Qt.DisplayRole:
                value = self._data.iloc[index.row(), index.column()]
                return str(value)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self._data.columns[section]
        return None

# ============================
# Multi-Line Input Dialog
# ============================

class MultiLineInputDialog(QDialog):
    """Custom dialog for multi-line text input."""
    def __init__(self, title, label, default_text=""):
        super().__init__()
        self.setWindowTitle(title)
        self.text = default_text
        self.init_ui(label)

    def init_ui(self, label_text):
        layout = QVBoxLayout()
        label = QLabel(label_text)
        self.text_edit = QTextEdit()
        self.text_edit.setText(self.text)
        layout.addWidget(label)
        layout.addWidget(self.text_edit)

        # Save and Cancel Buttons
        buttons_layout = QHBoxLayout()
        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        buttons_layout.addWidget(save_button)
        buttons_layout.addWidget(cancel_button)
        layout.addLayout(buttons_layout)

        self.setLayout(layout)

    def save(self):
        self.text = self.text_edit.toPlainText()
        self.accept()

    def get_text(self):
        return self.text

# ============================
# Data Actions Dialogs
# ============================

class DataActionsDialog(QDialog):
    """Dialog to edit data actions."""
    def __init__(self, existing_actions=None):
        super().__init__()
        self.setWindowTitle("Edit Data Actions")
        self.actions = existing_actions.copy() if existing_actions else []
        self.init_ui()

    def init_ui(self):
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

    def add_action(self):
        dialog = ActionEditorDialog()
        if dialog.exec_():
            action = dialog.get_action()
            self.actions.append(action)
            self.actions_list.addItem(QListWidgetItem(json.dumps(action)))

    def edit_action(self):
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

    def remove_action(self):
        selected_items = self.actions_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select an action to remove.")
            return
        selected_index = self.actions_list.row(selected_items[0])
        self.actions.pop(selected_index)
        self.actions_list.takeItem(selected_index)

    def save_actions(self):
        self.accept()

    def get_actions(self):
        return self.actions

class ActionEditorDialog(QDialog):
    """Dialog to add or edit a single data action."""
    def __init__(self, action=None):
        super().__init__()
        self.setWindowTitle("Edit Action")
        self.action = action.copy() if action else {}
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # Action Type
        action_type_label = QLabel("Action Type:")
        self.action_type_combo = QComboBox()
        self.action_type_combo.addItems(["force_to_cell", "remove", "reorder", "find_replace", "math_operations"])
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

    def update_parameters_fields(self, action_type):
        """Update the parameters fields based on the selected action type."""
        # Clear previous parameter fields
        while self.parameters_layout.count():
            item = self.parameters_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if action_type == "force_to_cell":
            form_layout = QFormLayout()
            self.column_input = QLineEdit()
            self.value_input = QLineEdit()
            form_layout.addRow("Column to Modify:", self.column_input)
            form_layout.addRow("Value to Force:", self.value_input)
            self.parameters_layout.addLayout(form_layout)
        elif action_type == "remove":
            form_layout = QFormLayout()
            self.columns_input = QLineEdit()
            form_layout.addRow("Columns to Remove (comma-separated):", self.columns_input)
            self.parameters_layout.addLayout(form_layout)
        elif action_type == "reorder":
            form_layout = QFormLayout()
            self.order_input = QLineEdit()
            form_layout.addRow("New Order (comma-separated indices):", self.order_input)
            self.parameters_layout.addLayout(form_layout)
        elif action_type == "find_replace":
            form_layout = QFormLayout()
            self.find_input = QLineEdit()
            self.replace_input = QLineEdit()
            form_layout.addRow("Find:", self.find_input)
            form_layout.addRow("Replace with:", self.replace_input)
            self.parameters_layout.addLayout(form_layout)
        elif action_type == "math_operations":
            form_layout = QFormLayout()
            self.column_input = QLineEdit()
            self.operation_input = QLineEdit()
            form_layout.addRow("Column for Operation:", self.column_input)
            form_layout.addRow("Operation (e.g., add 10, multiply 2):", self.operation_input)
            self.parameters_layout.addLayout(form_layout)

    def populate_fields(self):
        """Populate fields with existing action data."""
        action_type = self.action.get('action', '')
        self.action_type_combo.setCurrentText(action_type)

        if action_type == "force_to_cell":
            column = self.action.get('column', '')
            value = self.action.get('value', '')
            self.column_input.setText(column)
            self.value_input.setText(value)
        elif action_type == "remove":
            columns = self.action.get('columns_to_remove', [])
            self.columns_input.setText(','.join(columns))
        elif action_type == "reorder":
            order = self.action.get('new_order', [])
            self.order_input.setText(','.join(map(str, order)))
        elif action_type == "find_replace":
            find = self.action.get('find', '')
            replace = self.action.get('replace', '')
            self.find_input.setText(find)
            self.replace_input.setText(replace)
        elif action_type == "math_operations":
            column = self.action.get('column', '')
            operation = self.action.get('operation', '')
            self.column_input.setText(column)
            self.operation_input.setText(operation)

    def save_action(self):
        """Save the action data."""
        action_type = self.action_type_combo.currentText()
        self.action['action'] = action_type

        if action_type == "force_to_cell":
            column = self.column_input.text().strip()
            value = self.value_input.text().strip()
            if not column:
                QMessageBox.warning(self, "Invalid Input", "Column name cannot be empty.")
                return
            self.action['column'] = column
            self.action['value'] = value
        elif action_type == "remove":
            columns_text = self.columns_input.text()
            columns = [col.strip() for col in columns_text.split(',') if col.strip()]
            self.action['columns_to_remove'] = columns
        elif action_type == "reorder":
            order_text = self.order_input.text()
            try:
                order = [int(idx.strip()) for idx in order_text.split(',') if idx.strip().isdigit()]
                self.action['new_order'] = order
            except ValueError:
                QMessageBox.warning(self, "Invalid Input", "Please enter valid comma-separated integers for the new order.")
                return
        elif action_type == "find_replace":
            find = self.find_input.text()
            replace = self.replace_input.text()
            self.action['find'] = find
            self.action['replace'] = replace
        elif action_type == "math_operations":
            column = self.column_input.text().strip()
            operation = self.operation_input.text().strip()
            if not column or not operation:
                QMessageBox.warning(self, "Invalid Input", "Column and operation cannot be empty.")
                return
            self.action['column'] = column
            self.action['operation'] = operation

        self.accept()

    def get_action(self):
        """Return the action data."""
        return self.action

# ============================
# Config Dialog
# ============================

class ConfigDialog(QDialog):
    """Dialog to add or edit parser configurations."""
    def __init__(self, existing_config=None):
        super().__init__()
        self.setWindowTitle("Parser Configuration")
        self.config = existing_config.copy() if existing_config else {}
        self.original_last_position = self.config.get('last_position', 0)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # Machine Name
        machine_name_label = QLabel("Machine Name:")
        self.machine_name_input = QLineEdit()
        layout.addWidget(machine_name_label)
        layout.addWidget(self.machine_name_input)

        # Parser Type
        parser_type_label = QLabel("Parser Type:")
        self.parser_type_combo = QComboBox()
        self.parser_type_combo.addItems(["single", "multi", "COM"])
        self.parser_type_combo.currentTextChanged.connect(self.update_parser_type_fields)
        layout.addWidget(parser_type_label)
        layout.addWidget(self.parser_type_combo)

        # Parser-specific fields
        self.parser_fields_layout = QVBoxLayout()
        layout.addLayout(self.parser_fields_layout)

        # Update Interval
        update_interval_label = QLabel("Update Interval (seconds):")
        self.update_interval_input = QLineEdit()
        self.update_interval_input.setText("60")  # Default value
        layout.addWidget(update_interval_label)
        layout.addWidget(self.update_interval_input)

        # Last Position (Editable)
        last_position_label = QLabel("Last Position:")
        self.last_position_input = QLineEdit()
        self.last_position_input.setText(str(self.original_last_position))
        layout.addWidget(last_position_label)
        layout.addWidget(self.last_position_input)

        # Header and Data Actions Buttons
        self.header_button = QPushButton("Edit Header")
        self.header_button.clicked.connect(self.edit_header)
        self.data_actions_button = QPushButton("Edit Data Actions")
        self.data_actions_button.clicked.connect(self.edit_data_actions)
        layout.addWidget(self.header_button)
        layout.addWidget(self.data_actions_button)

        # Save and Cancel Buttons
        buttons_layout = QHBoxLayout()
        save_button = QPushButton("Save and Apply")
        save_button.clicked.connect(self.save_configuration)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        buttons_layout.addWidget(save_button)
        buttons_layout.addWidget(cancel_button)
        layout.addLayout(buttons_layout)

        self.setLayout(layout)

        # Initialize fields if editing existing config
        if self.config:
            self.populate_fields()
        else:
            # Set default parser type fields
            self.update_parser_type_fields(self.parser_type_combo.currentText())

    def create_file_selector(self, label_text):
        layout = QHBoxLayout()
        label = QLabel(label_text)
        line_edit = QLineEdit()
        browse_button = QPushButton("Browse")
        def browse():
            path, _ = QFileDialog.getOpenFileName(self, "Select File")
            if path:
                line_edit.setText(path)
        browse_button.clicked.connect(browse)
        layout.addWidget(label)
        layout.addWidget(line_edit)
        layout.addWidget(browse_button)
        container = QWidget()
        container.setLayout(layout)
        container.line_edit = line_edit  # Attach for later access
        return container

    def create_directory_selector(self, label_text):
        layout = QHBoxLayout()
        label = QLabel(label_text)
        line_edit = QLineEdit()
        browse_button = QPushButton("Browse")
        def browse():
            path = QFileDialog.getExistingDirectory(self, "Select Directory")
            if path:
                line_edit.setText(path)
        browse_button.clicked.connect(browse)
        layout.addWidget(label)
        layout.addWidget(line_edit)
        layout.addWidget(browse_button)
        container = QWidget()
        container.setLayout(layout)
        container.line_edit = line_edit  # Attach for later access
        return container

    def create_delimiter_selector(self):
        layout = QHBoxLayout()
        label = QLabel("Delimiter:")
        delimiter_combo = QComboBox()
        delimiter_combo.addItems([",", ";", "\t", "|"])
        layout.addWidget(label)
        layout.addWidget(delimiter_combo)
        container = QWidget()
        container.setLayout(layout)
        container.delimiter_combo = delimiter_combo  # Attach delimiter_combo to container
        return container

    def create_header_checkbox(self):
        layout = QHBoxLayout()
        header_checkbox = QCheckBox("Input CSVs have headers")
        layout.addWidget(header_checkbox)
        container = QWidget()
        container.setLayout(layout)
        container.header_checkbox = header_checkbox  # Attach header_checkbox to container
        return container

    def update_parser_type_fields(self, parser_type):
        """Update the parser-specific fields based on the selected parser type."""
        # Clear previous fields
        while self.parser_fields_layout.count():
            item = self.parser_fields_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if parser_type == "single":
            self.single_input_field = self.create_file_selector("Input CSV File:")
            self.single_output_field = self.create_directory_selector("Output Directory:")
            self.delimiter_field = self.create_delimiter_selector()
            self.parser_fields_layout.addWidget(self.single_input_field)
            self.parser_fields_layout.addWidget(self.single_output_field)
            self.parser_fields_layout.addWidget(self.delimiter_field)
        elif parser_type == "multi":
            self.multi_input_field = self.create_directory_selector("Input Folder:")
            self.multi_move_field = self.create_directory_selector("Move Processed Files To:")
            self.multi_output_field = self.create_directory_selector("Output Directory:")
            self.delimiter_field = self.create_delimiter_selector()
            self.header_checkbox_field = self.create_header_checkbox()
            self.parser_fields_layout.addWidget(self.multi_input_field)
            self.parser_fields_layout.addWidget(self.multi_move_field)
            self.parser_fields_layout.addWidget(self.multi_output_field)
            self.parser_fields_layout.addWidget(self.delimiter_field)
            self.parser_fields_layout.addWidget(self.header_checkbox_field)
        elif parser_type == "COM":
            port_label = QLabel("COM Port:")
            self.port_input = QLineEdit()
            self.com_output_field = self.create_directory_selector("Output Directory:")
            self.parser_fields_layout.addWidget(port_label)
            self.parser_fields_layout.addWidget(self.port_input)
            self.parser_fields_layout.addWidget(self.com_output_field)

    def edit_header(self):
        """Open a dialog to edit header columns."""
        header = self.config.get('header', [])
        if not header:
            header = generate_default_headers()
        dialog = MultiLineInputDialog("Edit Header", "Enter header columns (one per line):", "\n".join(header))
        if dialog.exec_():
            header = [line.strip() for line in dialog.get_text().split('\n') if line.strip()]
            if not header:
                header = generate_default_headers()
            self.config['header'] = header

    def edit_data_actions(self):
        """Open a dialog to edit data actions."""
        data_actions = self.config.get('data', [])
        dialog = DataActionsDialog(data_actions)
        if dialog.exec_():
            self.config['data'] = dialog.get_actions()

    def save_configuration(self):
        """Save the parser configuration."""
        machine_name = self.machine_name_input.text().strip()
        parser_type = self.parser_type_combo.currentText()
        config_folder = os.path.join(CONFIG_DIR, machine_name)
        self.config['config_folder'] = config_folder

        if not machine_name:
            QMessageBox.warning(self, "Validation Error", "Machine Name is required.")
            return

        if not os.path.exists(config_folder):
            try:
                os.makedirs(config_folder)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to create config folder: {e}")
                return

        self.config['machine_name'] = machine_name
        self.config['parser_type'] = parser_type

        update_interval = self.update_interval_input.text().strip()
        if not update_interval.isdigit():
            QMessageBox.warning(self, "Validation Error", "Update Interval must be a positive integer.")
            return
        self.config['update_interval'] = int(update_interval)

        # Handle last_position
        last_position_text = self.last_position_input.text().strip()
        if last_position_text.isdigit():
            last_position = int(last_position_text)
            self.config['last_position'] = last_position
        else:
            QMessageBox.warning(self, "Validation Error", "Last Position must be a non-negative integer.")
            return

        if parser_type == "single":
            input_csv = self.single_input_field.line_edit.text()
            output_dir = self.single_output_field.line_edit.text()
            output_csv = os.path.join(output_dir, f"{machine_name}_parsed.csv")
            delimiter = self.delimiter_field.delimiter_combo.currentText()
            self.config['single_csv'] = {
                'input': input_csv,
                'output': output_csv,
                'delimiter': delimiter,
                'last_position': self.config.get('last_position', 0),
            }
            # Remove multi and COM keys if they exist
            self.config.pop('multi', None)
            self.config.pop('COM', None)
        elif parser_type == "multi":
            input_folder = self.multi_input_field.line_edit.text()
            move_folder = self.multi_move_field.line_edit.text()
            output_dir = self.multi_output_field.line_edit.text()
            output_csv = os.path.join(output_dir, f"{machine_name}_parsed.csv")
            delimiter = self.delimiter_field.delimiter_combo.currentText()
            has_header = self.header_checkbox_field.header_checkbox.isChecked()
            self.config['multi'] = {
                'input': input_folder,
                'move': move_folder,
                'output': output_dir,
                'output_file': output_csv,
                'delimiter': delimiter,
                'has_header': has_header,
                'last_position': self.config.get('last_position', 0),
            }
            # Remove single and COM keys if they exist
            self.config.pop('single_csv', None)
            self.config.pop('COM', None)
        elif parser_type == "COM":
            port = self.port_input.text().strip()
            output_dir = self.com_output_field.line_edit.text()
            output_file = os.path.join(output_dir, f"{machine_name}_parsed.csv")
            self.config['COM'] = {
                'port': port,
                'output': output_file,
                'last_position': self.config.get('last_position', 0),
            }
            # Remove single and multi keys if they exist
            self.config.pop('single_csv', None)
            self.config.pop('multi', None)

        # Save theme selection
        theme = self.config.get('theme', 'default.css')
        self.config['theme'] = theme

        # Save config to file
        config_file = os.path.join(config_folder, 'parser_config.json')
        try:
            save_json_config(config_file, self.config)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save config file: {e}")
            return

        self.accept()

    def populate_fields(self):
        """Populate fields with existing configuration data."""
        self.machine_name_input.setText(self.config.get('machine_name', ''))
        parser_type = self.config.get('parser_type', 'single')
        self.parser_type_combo.setCurrentText(parser_type)
        self.update_parser_type_fields(parser_type)

        if parser_type == "single":
            self.single_input_field.line_edit.setText(self.config['single_csv'].get('input', ''))
            self.single_output_field.line_edit.setText(os.path.dirname(self.config['single_csv'].get('output', '')))
            delimiter = self.config['single_csv'].get('delimiter', ',')
            self.delimiter_field.delimiter_combo.setCurrentText(delimiter)
        elif parser_type == "multi":
            self.multi_input_field.line_edit.setText(self.config['multi'].get('input', ''))
            self.multi_move_field.line_edit.setText(self.config['multi'].get('move', ''))
            self.multi_output_field.line_edit.setText(self.config['multi'].get('output', ''))
            delimiter = self.config['multi'].get('delimiter', ',')
            self.delimiter_field.delimiter_combo.setCurrentText(delimiter)
            has_header = self.config['multi'].get('has_header', False)
            self.header_checkbox_field.header_checkbox.setChecked(has_header)
        elif parser_type == "COM":
            self.port_input.setText(self.config['COM'].get('port', ''))
            self.com_output_field.line_edit.setText(os.path.dirname(self.config['COM'].get('output', '')))

        update_interval = self.config.get('update_interval', 60)
        self.update_interval_input.setText(str(update_interval))

        # Populate last_position
        last_position = self.config.get('last_position', 0)
        self.last_position_input.setText(str(last_position))

# ============================
# Settings Dialog
# ============================

class SettingsDialog(QDialog):
    """Dialog to adjust settings like scale and theme."""
    def __init__(self, parent):
        super().__init__()
        self.setWindowTitle("Settings")
        self.parent = parent
        self.scale_factor = parent.scale_factor
        self.current_theme = parent.current_theme
        self.init_ui()

    def init_ui(self):
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

        # Save and Cancel Buttons
        buttons_layout = QHBoxLayout()
        apply_button = QPushButton("Apply")
        apply_button.clicked.connect(self.apply_settings)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        buttons_layout.addWidget(apply_button)
        buttons_layout.addWidget(cancel_button)
        layout.addLayout(buttons_layout)

        self.setLayout(layout)

    def scale_changed(self, value):
        """Update the scale value label as the slider moves."""
        self.scale_value_label.setText(f"{value}%")

    def load_themes(self):
        """Load available themes from the THEMES_DIR."""
        self.parent.ensure_themes_directory()
        theme_files = [f for f in os.listdir(THEMES_DIR) if f.endswith('.css')]
        self.theme_combo.addItems(theme_files)

    def apply_settings(self):
        """Apply the selected scale and theme settings."""
        # Apply scale
        scale_value = self.scale_slider.value()
        self.parent.scale_factor = scale_value / 100.0
        self.parent.apply_scale()

        # Apply theme
        selected_theme = self.theme_combo.currentText()
        self.parent.current_theme = selected_theme
        self.parent.apply_theme()

        # Save theme to all parser configs
        for machine_name, (tab, config) in self.parent.parsers.items():
            config['theme'] = selected_theme
            config_file = os.path.join(config['config_folder'], 'parser_config.json')
            save_json_config(config_file, config)

        self.accept()

# ============================
# Parser Tab
# ============================

class ParserTab(QWidget):
    """A tab representing a single parser."""
    update_status_signal = pyqtSignal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.init_ui()
        self.update_status_signal.connect(self.update_status)
        self.com_thread = None

        if self.config['parser_type'] == 'COM':
            self.start_com_processing()
        else:
            self.load_and_parse_data()
            self.timer = QTimer()
            self.timer.timeout.connect(self.load_and_parse_data)
            update_interval = self.config.get('update_interval', 60) * 1000  # Convert to milliseconds
            self.timer.start(update_interval)

    def init_ui(self):
        layout = QVBoxLayout()
        self.table_view = QTableView()
        self.status_label = QLabel("Idle")
        layout.addWidget(self.table_view)
        layout.addWidget(self.status_label)
        self.setLayout(layout)

    def start_com_processing(self):
        """Start processing data from the COM port in a separate thread."""
        self.com_thread = threading.Thread(target=self.process_com_port, daemon=True)
        self.com_thread.start()

    def process_com_port(self):
        """Process data from the COM port and update the GUI."""
        try:
            process_com_port(
                self.config['COM'],
                self.config['config_folder'],
                self.update_status_signal
            )
        except Exception as e:
            error_message = f"Error in COM port processing: {e}"
            safe_message = error_message.encode('ascii', errors='replace').decode('ascii')
            self.update_status_signal.emit(safe_message)

    def load_and_parse_data(self):
        """Load and parse CSV data based on the parser type."""
        try:
            self.update_status("Processing...")
            config_type = self.config['parser_type']
            config_folder = self.config['config_folder']

            if config_type == 'single':
                input_csv = self.config['single_csv']['input']
                output_csv = self.config['single_csv']['output']
                delimiter = self.config['single_csv']['delimiter']
                df = process_single_csv(input_csv, output_csv, config_folder)
                df_full = pd.read_csv(output_csv, on_bad_lines='skip', encoding='utf-8')
                model = PandasModel(df_full)
                self.table_view.setModel(model)
                self.update_status(f"Parsed data saved to {output_csv}")
            elif config_type == 'multi':
                input_folder = self.config['multi']['input']
                move_folder = self.config['multi']['move']
                output_folder = self.config['multi']['output']
                delimiter = self.config['multi']['delimiter']
                has_header = self.config['multi']['has_header']
                output_csv = self.config['multi'].get('output_file')

                df_list = process_multi_csv(input_folder, move_folder, output_folder, config_folder)
                if os.path.exists(output_csv):
                    df_full = pd.read_csv(output_csv, on_bad_lines='skip', encoding='utf-8')
                    model = PandasModel(df_full)
                    self.table_view.setModel(model)
                    self.update_status(f"Parsed data saved to {output_csv}")
                else:
                    self.update_status("No new files to process.")
            elif config_type == 'COM':
                output_file = self.config['COM'].get('output')
                if os.path.exists(output_file):
                    df_full = pd.read_csv(output_file, on_bad_lines='skip', encoding='utf-8')
                    model = PandasModel(df_full)
                    self.table_view.setModel(model)
                    self.update_status(f"Displaying data from {output_file}")
                else:
                    self.update_status("No data available.")
            else:
                self.update_status("Invalid parser type specified.")
        except Exception as e:
            error_message = f"Error in parser '{self.config.get('machine_name', 'Unnamed')}': {e}"
            safe_message = error_message.encode('ascii', errors='replace').decode('ascii')
            self.update_status(safe_message)

    def update_status(self, message):
        """Update the status label with the provided message."""
        self.status_label.setText(message)

# ============================
# Main Window
# ============================

class MainWindow(QMainWindow):
    """The main window of the application."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CSV Parser")
        self.parsers = {}  # Dictionary to keep track of parsers by machine_name
        self.scale_factor = 1.0  # Default scale factor
        self.current_theme = 'default.css'  # Default theme
        self.ensure_themes_directory()  # Ensure themes directory exists
        self.init_ui()

    def init_ui(self):
        self.tab_widget = QTabWidget()
        self.setCentralWidget(self.tab_widget)
        # Load existing configurations
        self.load_existing_configs()
        # Add menu actions
        self.init_menu()
        # Apply theme
        self.apply_theme()
        # Apply scale
        self.apply_scale()
        # Initialize status bar
        self.statusBar().showMessage("Ready")

    def ensure_themes_directory(self):
        """Ensure that the themes directory exists and contains default themes."""
        if not os.path.exists(THEMES_DIR):
            os.makedirs(THEMES_DIR)
            self.create_default_themes()

    def create_default_themes(self):
        """Create default theme files if they don't exist."""
        # Default Theme
        default_theme = """
        /* default.css */
        QTabWidget::pane {  
            background-color: #ffffff; 
            border-radius: 10px; 
        }
        QTabBar::tab { 
            background-color: qlineargradient(
                x1: 0, y1: 0, x2: 0, y2: 2,
                stop: 0 #e3e3e3, stop: 1 #b3b3b3);
            color: #363636;
            border-top-left-radius: 5px;
            border-top-right-radius: 5px;
            min-width: 80px;
            padding: 5px;
            margin-left: 1px;
        }
        QTabBar::tab:selected { 
            background-color: #ffffff;
            color: #4d4d4d;
            border-top-left-radius: 5px;
            border-top-right-radius: 5px;
            margin-top: 3px;
        }
        QTabBar::tab:hover:!selected { 
            background-color: #b3b3b3;
            color: #575757;
        }
        QTabBar::tab:!selected {
            margin-top: 10px;
        }
        """

        # Dark Theme
        dark_theme = """
        /* dark.css */
        QWidget {
            background-color: #2b2b2b;
            color: #ffffff;
        }
        QTabWidget::pane {  
            background-color: #2b2b2b; 
            border-radius: 10px; 
        }
        QTabBar::tab { 
            background-color: #3c3c3c;
            color: #ffffff;
            border-top-left-radius: 5px;
            border-top-right-radius: 5px;
            min-width: 80px;
            padding: 5px;
            margin-left: 1px;
        }
        QTabBar::tab:selected { 
            background-color: #2b2b2b;
            color: #ffffff;
            border-top-left-radius: 5px;
            border-top-right-radius: 5px;
            margin-top: 3px;
        }
        QTabBar::tab:hover:!selected { 
            background-color: #555555;
            color: #ffffff;
        }
        QTabBar::tab:!selected {
            margin-top: 10px;
        }
        """

        # Light Theme
        light_theme = """
        /* light.css */
        QWidget {
            background-color: #f0f0f0;
            color: #000000;
        }
        QTabWidget::pane {  
            background-color: #ffffff; 
            border-radius: 10px; 
        }
        QTabBar::tab { 
            background-color: #dcdcdc;
            color: #000000;
            border-top-left-radius: 5px;
            border-top-right-radius: 5px;
            min-width: 80px;
            padding: 5px;
            margin-left: 1px;
        }
        QTabBar::tab:selected { 
            background-color: #ffffff;
            color: #000000;
            border-top-left-radius: 5px;
            border-top-right-radius: 5px;
            margin-top: 3px;
        }
        QTabBar::tab:hover:!selected { 
            background-color: #c0c0c0;
            color: #000000;
        }
        QTabBar::tab:!selected {
            margin-top: 10px;
        }
        """

        themes = {
            'default.css': default_theme,
            'dark.css': dark_theme,
            'light.css': light_theme
        }

        for filename, content in themes.items():
            theme_path = os.path.join(THEMES_DIR, filename)
            if not os.path.exists(theme_path):
                with open(theme_path, 'w', encoding='utf-8') as f:
                    f.write(content)

    def load_existing_configs(self):
        """Load existing parser configurations and create tabs for them."""
        for folder_name in os.listdir(CONFIG_DIR):
            config_folder = os.path.join(CONFIG_DIR, folder_name)
            if os.path.isdir(config_folder):
                config_file = os.path.join(config_folder, 'parser_config.json')
                if os.path.exists(config_file):
                    try:
                        config = load_json_config(config_file)
                        tab = ParserTab(config)
                        machine_name = config.get('machine_name', 'Unnamed')
                        self.tab_widget.addTab(tab, machine_name)
                        self.parsers[machine_name] = (tab, config)
                        tab.update_status_signal.connect(self.display_status_message)
                    except Exception as e:
                        self.statusBar().showMessage(f"Failed to load config for {folder_name}: {e}")
                        continue

    def init_menu(self):
        """Initialize the menu bar with actions."""
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("File")

        # Add Parser Action
        add_parser_action = QAction("Add Parser", self)
        add_parser_action.triggered.connect(self.add_parser)
        file_menu.addAction(add_parser_action)

        # Edit Parser Action
        edit_parser_action = QAction("Edit Parser", self)
        edit_parser_action.triggered.connect(self.edit_parser)
        file_menu.addAction(edit_parser_action)

        # Delete Parser Action
        delete_parser_action = QAction("Delete Parser", self)
        delete_parser_action.triggered.connect(self.delete_parser)
        file_menu.addAction(delete_parser_action)

        # Settings Action
        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self.open_settings)
        file_menu.addAction(settings_action)

    def apply_scale(self):
        """Apply the scaling factor to the entire application."""
        font = QFont()
        font.setPointSizeF(10 * self.scale_factor)  # Base font size is 10
        QApplication.setFont(font)

    def apply_theme(self):
        """Apply the selected theme to the application."""
        theme_file = os.path.join(THEMES_DIR, self.current_theme)
        if os.path.exists(theme_file):
            with open(theme_file, 'r', encoding='utf-8') as f:
                style = f.read()
                self.setStyleSheet(style)
            self.statusBar().showMessage(f"Applied theme: {self.current_theme}")
        else:
            # Default theme if file not found
            self.setStyleSheet("""
                QTabWidget::pane {  
                    background-color: #ffffff; 
                    border-radius: 10px; 
                }
                QTabBar::tab { 
                    background-color: qlineargradient(
                        x1: 0, y1: 0, x2: 0, y2: 2,
                        stop: 0 #e3e3e3, stop: 1 #b3b3b3);
                    color: #363636;
                    border-top-left-radius: 5px;
                    border-top-right-radius: 5px;
                    min-width: 80px;
                    padding: 5px;
                    margin-left: 1px;
                }
                QTabBar::tab:selected { 
                    background-color: #ffffff;
                    color: #4d4d4d;
                    border-top-left-radius: 5px;
                    border-top-right-radius: 5px;
                    margin-top: 3px;
                }
                QTabBar::tab:hover:!selected { 
                    background-color: #b3b3b3;
                    color: #575757;
                }
                QTabBar::tab:!selected {
                    margin-top: 10px;
                }
            """)
            self.statusBar().showMessage("Applied default theme.")

    def add_parser(self):
        """Open the ConfigDialog to add a new parser."""
        dialog = ConfigDialog()
        if dialog.exec_():
            config = dialog.config
            machine_name = config.get('machine_name', 'Unnamed')
            config_folder = config.get('config_folder')
            config_file = os.path.join(config_folder, 'parser_config.json')
            try:
                save_json_config(config_file, config)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to save config file: {e}")
                return
            tab = ParserTab(config)
            self.tab_widget.addTab(tab, machine_name)
            self.parsers[machine_name] = (tab, config)
            tab.update_status_signal.connect(self.display_status_message)
            self.statusBar().showMessage(f"Added parser: {machine_name}")

    def edit_parser(self):
        """Edit an existing parser configuration."""
        parser_name, ok = self.select_parser_dialog("Select Parser to Edit")
        if ok and parser_name in self.parsers:
            tab, config = self.parsers[parser_name]
            dialog = ConfigDialog(existing_config=config)
            if dialog.exec_():
                updated_config = dialog.config
                new_machine_name = updated_config.get('machine_name', 'Unnamed')
                new_config_folder = updated_config.get('config_folder')

                # Save updated config
                new_config_file = os.path.join(new_config_folder, 'parser_config.json')
                try:
                    save_json_config(new_config_file, updated_config)
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"Failed to save updated config: {e}")
                    return

                # Update the tab
                new_tab = ParserTab(updated_config)
                index = self.tab_widget.indexOf(tab)
                self.tab_widget.removeTab(index)
                self.tab_widget.insertTab(index, new_tab, new_machine_name)
                self.parsers.pop(parser_name)
                self.parsers[new_machine_name] = (new_tab, updated_config)
                new_tab.update_status_signal.connect(self.display_status_message)
                self.statusBar().showMessage(f"Edited parser: {new_machine_name}")

    def delete_parser(self):
        """Delete an existing parser configuration."""
        parser_name, ok = self.select_parser_dialog("Select Parser to Delete")
        if ok and parser_name in self.parsers:
            reply = QMessageBox.question(
                self, "Delete Parser",
                f"Are you sure you want to delete parser '{parser_name}'?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                tab, config = self.parsers.pop(parser_name)
                index = self.tab_widget.indexOf(tab)
                self.tab_widget.removeTab(index)
                config_folder = config.get('config_folder', '')
                if os.path.exists(config_folder):
                    try:
                        shutil.rmtree(config_folder)
                        self.statusBar().showMessage(f"Deleted parser: {parser_name}")
                    except Exception as e:
                        self.statusBar().showMessage(f"Failed to delete parser folder: {e}")
                else:
                    self.statusBar().showMessage(f"Parser folder does not exist: {config_folder}")

    def select_parser_dialog(self, title):
        """Open a dialog to select a parser."""
        parsers = list(self.parsers.keys())
        if not parsers:
            QMessageBox.warning(self, title, "No parsers are currently available.")
            return None, False
        item, ok = QInputDialog.getItem(self, title, "Select a parser:", parsers, 0, False)
        return item, ok

    def open_settings(self):
        """Open the SettingsDialog to adjust settings."""
        dialog = SettingsDialog(self)
        if dialog.exec_():
            self.statusBar().showMessage("Settings applied.")

    def display_status_message(self, message):
        """Display a message in the status bar."""
        self.statusBar().showMessage(message)

# ============================
# Main Function
# ============================

def main():
    """Main function to start the application."""
    import ctypes
    # Enable DPI awareness on Windows
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    # Enable High DPI scaling
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    # Set application style
    app.setStyle('Fusion')

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
