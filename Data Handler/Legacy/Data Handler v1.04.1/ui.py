# ui.py
import sys
import os
import json
import shutil
import pandas as pd
import threading
import logging
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
SETTINGS_FILE = os.path.join(CONFIG_DIR, 'settings.json')

# Ensure that the CONFIG_DIR exists
os.makedirs(CONFIG_DIR, exist_ok=True)

# Configure logging
LOG_FILE = os.path.join(CONFIG_DIR, 'parser.log')
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(filename)s - %(funcName)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
LOG_LOCK = threading.Lock()

def log_message(level, message):
    with LOG_LOCK:
        if level == 'info':
            logging.info(message)
        elif level == 'warning':
            logging.warning(message)
        elif level == 'error':
            logging.error(message)
        else:
            logging.debug(message)

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

class ConfigDialog(QDialog):
    """Dialog to add or edit parser configurations."""
    def __init__(self, existing_config=None):
        super().__init__()
        self.setWindowTitle("Parser Configuration")
        self.config = existing_config.copy() if existing_config else {}
        self.previous_last_position = self.config.get('single_csv', {}).get('last_position', 0)
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
            # Last Position Controls
            self.last_position_label = QLabel("Last Position:")
            self.last_position_input = QLineEdit()
            self.last_position_input.setText(str(self.previous_last_position))
            layout = QHBoxLayout()
            reset_button = QPushButton("Reset to Zero")
            reset_button.clicked.connect(self.reset_last_position)
            layout.addWidget(self.last_position_label)
            layout.addWidget(self.last_position_input)
            layout.addWidget(reset_button)
            self.last_position_container = QWidget()
            self.last_position_container.setLayout(layout)

            self.parser_fields_layout.addWidget(self.single_input_field)
            self.parser_fields_layout.addWidget(self.single_output_field)
            self.parser_fields_layout.addWidget(self.delimiter_field)
            self.parser_fields_layout.addWidget(self.last_position_container)
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
            form_layout = QFormLayout()
            self.port_input = QLineEdit()
            self.baud_rate_input = QLineEdit()
            self.baud_rate_input.setText("9600")
            self.com_output_field = self.create_directory_selector("Output Directory:")
            advanced_button = QPushButton("Advanced COM Settings")
            advanced_button.clicked.connect(self.open_advanced_com_settings)
            form_layout.addRow("COM Port:", self.port_input)
            form_layout.addRow("Baud Rate:", self.baud_rate_input)
            self.parser_fields_layout.addLayout(form_layout)
            self.parser_fields_layout.addWidget(self.com_output_field)
            self.parser_fields_layout.addWidget(advanced_button)

    def reset_last_position(self):
        self.last_position_input.setText("0")

    def open_advanced_com_settings(self):
        dialog = AdvancedCOMSettingsDialog(self.config.get('COM', {}))
        if dialog.exec_():
            self.config['COM'].update(dialog.get_settings())

    def edit_header(self):
        """Open a dialog to edit header columns."""
        header = self.config.get('header', [])
        dialog = MultiLineInputDialog("Edit Header", "Enter header columns (one per line):", "\n".join(header))
        if dialog.exec_():
            header = [line.strip() for line in dialog.get_text().split('\n') if line.strip()]
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

        if parser_type == "single":
            input_csv = self.single_input_field.line_edit.text()
            output_dir = self.single_output_field.line_edit.text()
            output_csv = os.path.join(output_dir, f"{machine_name}_parsed.csv")
            delimiter = self.delimiter_field.delimiter_combo.currentText()
            last_position = self.last_position_input.text()
            if not last_position.isdigit():
                QMessageBox.warning(self, "Validation Error", "Last Position must be a non-negative integer.")
                return
            last_position = int(last_position)
            self.config['single_csv'] = {
                'input': input_csv,
                'output': output_csv,
                'delimiter': delimiter,
                'last_position': last_position,
            }
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
            }
        elif parser_type == "COM":
            port = self.port_input.text().strip()
            baud_rate = self.baud_rate_input.text().strip()
            output_dir = self.com_output_field.line_edit.text()
            output_file = os.path.join(output_dir, f"{machine_name}_parsed.csv")
            self.config['COM'] = {
                'port': port,
                'baud_rate': baud_rate,
                'output': output_file,
            }

        # Save config to file
        config_file = os.path.join(config_folder, 'parser_config.json')
        try:
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4)
            log_message('info', f"Configuration saved for machine '{machine_name}' in '{config_file}'")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save config file: {e}")
            log_message('error', f"Failed to save configuration for machine '{machine_name}': {e}")
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
            last_position = self.config['single_csv'].get('last_position', 0)
            self.last_position_input.setText(str(last_position))
            self.previous_last_position = last_position
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
            self.baud_rate_input.setText(self.config['COM'].get('baud_rate', '9600'))
            self.com_output_field.line_edit.setText(os.path.dirname(self.config['COM'].get('output', '')))

        update_interval = self.config.get('update_interval', 60)
        self.update_interval_input.setText(str(update_interval))

class AdvancedCOMSettingsDialog(QDialog):
    """Dialog for advanced COM settings."""
    def __init__(self, existing_settings=None):
        super().__init__()
        self.setWindowTitle("Advanced COM Settings")
        self.settings = existing_settings.copy() if existing_settings else {}
        self.init_ui()

    def init_ui(self):
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

    def save_settings(self):
        self.settings['parity'] = self.parity_input.text().strip()
        self.settings['stop_bits'] = float(self.stop_bits_input.text().strip())
        self.settings['byte_size'] = int(self.byte_size_input.text().strip())
        self.settings['timeout'] = float(self.timeout_input.text().strip())
        self.accept()

    def get_settings(self):
        return self.settings

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

    def update_parameters_fields(self, action_type):
        """Update the parameters fields based on the selected action type."""
        # Clear previous parameter fields
        while self.parameters_layout.count():
            item = self.parameters_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

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
            form_layout.addRow("Target Column:", self.target_column_input)
            self.parameters_layout.addLayout(form_layout)
        elif action_type == "math_operations":
            form_layout = QFormLayout()
            self.operations_input = QTextEdit()
            form_layout.addRow("Operations (one per line):", self.operations_input)
            self.parameters_layout.addLayout(form_layout)

    def populate_fields(self):
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
            self.target_column_input.setText(target_column)
        elif action_type == "math_operations":
            operations = self.action.get('operations', [])
            self.operations_input.setText('\n'.join(operations))

    def save_action(self):
        """Save the action data."""
        action_type = self.action_type_combo.currentText()
        self.action['action'] = action_type

        if action_type == "reorder":
            order_text = self.order_input.text()
            try:
                order = [int(idx.strip()) for idx in order_text.split(',') if idx.strip().isdigit()]
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
            self.action['substring'] = substring
            self.action['target_column'] = target_column
        elif action_type == "math_operations":
            operations_text = self.operations_input.toPlainText()
            operations = [line.strip() for line in operations_text.split('\n') if line.strip()]
            self.action['operations'] = operations

        self.accept()

    def get_action(self):
        """Return the action data."""
        return self.action

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

        # Save settings
        settings = {
            'scale_factor': self.parent.scale_factor,
            'current_theme': self.parent.current_theme,
        }
        try:
            with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=4)
            log_message('info', "Settings saved successfully.")
        except Exception as e:
            log_message('error', f"Failed to save settings: {e}")

        self.accept()

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
            self.timer = QTimer()
            self.timer.timeout.connect(self.load_and_parse_data)
            update_interval = self.config.get('update_interval', 60) * 1000  # Convert to milliseconds
            self.timer.start(update_interval)
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
        self.com_thread = threading.Thread(target=self.process_com_port_thread, daemon=True)
        self.com_thread.start()

    def process_com_port_thread(self):
        """Process data from the COM port."""
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
            log_message('error', safe_message)

    def load_and_parse_data(self):
        """Load and parse CSV data based on the parser type."""
        try:
            self.update_status("Processing...")
            config_type = self.config['parser_type']
            config_folder = self.config['config_folder']

            if config_type == 'single':
                input_csv = self.config['single_csv']['input']
                output_csv = self.config['single_csv']['output']
                df = process_single_csv(input_csv, output_csv, config_folder)
                df_full = pd.read_csv(output_csv, on_bad_lines='skip', encoding='utf-8')
                model = PandasModel(df_full)
                self.table_view.setModel(model)
                self.update_status(f"Parsed data saved to {output_csv}")
                log_message('info', f"Parsed data saved to {output_csv}")
            elif config_type == 'multi':
                input_folder = self.config['multi']['input']
                move_folder = self.config['multi']['move']
                output_folder = self.config['multi']['output']
                output_csv = self.config['multi'].get('output_file')

                df_list = process_multi_csv(input_folder, move_folder, output_folder, config_folder)
                if os.path.exists(output_csv):
                    df_full = pd.read_csv(output_csv, on_bad_lines='skip', encoding='utf-8')
                    model = PandasModel(df_full)
                    self.table_view.setModel(model)
                    self.update_status(f"Parsed data saved to {output_csv}")
                    log_message('info', f"Parsed data saved to {output_csv}")
                else:
                    self.update_status("No new files to process.")
                    log_message('info', "No new files to process.")
            elif config_type == 'COM':
                output_file = self.config['COM'].get('output')
                if os.path.exists(output_file):
                    df_full = pd.read_csv(output_file, on_bad_lines='skip', encoding='utf-8')
                    model = PandasModel(df_full)
                    self.table_view.setModel(model)
                    self.update_status(f"Displaying data from {output_file}")
                    log_message('info', f"Displaying data from {output_file}")
                else:
                    self.update_status("No data available.")
                    log_message('info', "No data available.")
            else:
                self.update_status("Invalid parser type specified.")
                log_message('warning', "Invalid parser type specified.")
        except Exception as e:
            error_message = f"Error in parser '{self.config.get('machine_name', 'Unnamed')}': {e}"
            safe_message = error_message.encode('ascii', errors='replace').decode('ascii')
            self.update_status(safe_message)
            log_message('error', safe_message)

    def update_status(self, message):
        """Update the status label with the provided message."""
        self.status_label.setText(message)

class MainWindow(QMainWindow):
    """The main window of the application."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CSV Parser")
        self.parsers = {}  # Dictionary to keep track of parsers by machine_name
        self.scale_factor = 1.0  # Default scale factor
        self.current_theme = 'default.css'  # Default theme
        self.ensure_themes_directory()  # Ensure themes directory exists
        self.load_settings()
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
                        with open(config_file, 'r', encoding='utf-8') as f:
                            config = json.load(f)
                        tab = ParserTab(config)
                        machine_name = config.get('machine_name', 'Unnamed')
                        self.tab_widget.addTab(tab, machine_name)
                        self.parsers[machine_name] = (tab, config)
                        tab.update_status_signal.connect(self.display_status_message)
                        log_message('info', f"Loaded parser configuration for '{machine_name}'")
                    except Exception as e:
                        self.statusBar().showMessage(f"Failed to load config for {folder_name}: {e}")
                        log_message('error', f"Failed to load config for '{folder_name}': {e}")
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
            log_message('info', f"Applied theme: {self.current_theme}")
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
            log_message('warning', "Applied default theme due to missing theme file.")

    def add_parser(self):
        """Open the ConfigDialog to add a new parser."""
        dialog = ConfigDialog()
        if dialog.exec_():
            config = dialog.config
            machine_name = config.get('machine_name', 'Unnamed')
            config_folder = config.get('config_folder')
            config_file = os.path.join(config_folder, 'parser_config.json')
            try:
                with open(config_file, 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=4)
                log_message('info', f"Added parser configuration for '{machine_name}'")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to save config file: {e}")
                log_message('error', f"Failed to save config file for '{machine_name}': {e}")
                return
            tab = ParserTab(config)
            self.tab_widget.addTab(tab, machine_name)
            self.parsers[machine_name] = (tab, config)
            tab.update_status_signal.connect(self.display_status_message)
            self.statusBar().showMessage(f"Added parser: {machine_name}")
            log_message('info', f"Added parser: {machine_name}")

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
                    with open(new_config_file, 'w', encoding='utf-8') as f:
                        json.dump(updated_config, f, indent=4)
                    log_message('info', f"Updated parser configuration for '{new_machine_name}'")
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"Failed to save updated config: {e}")
                    log_message('error', f"Failed to save updated config for '{new_machine_name}': {e}")
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
                log_message('info', f"Edited parser: {new_machine_name}")

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
                        log_message('info', f"Deleted parser: {parser_name}")
                    except Exception as e:
                        self.statusBar().showMessage(f"Failed to delete parser folder: {e}")
                        log_message('error', f"Failed to delete parser folder for '{parser_name}': {e}")
                else:
                    self.statusBar().showMessage(f"Parser folder does not exist: {config_folder}")
                    log_message('warning', f"Parser folder does not exist: {config_folder}")

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

    def load_settings(self):
        """Load settings from the settings file."""
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                self.scale_factor = settings.get('scale_factor', 1.0)
                self.current_theme = settings.get('current_theme', 'default.css')
                log_message('info', "Loaded settings from file.")
            except Exception as e:
                log_message('error', f"Failed to load settings: {e}")

def main():
    """Main function to start the application."""
    import ctypes
    import sys
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
