import os
import json
import shutil
import threading
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QComboBox, QPushButton,
    QHBoxLayout, QFileDialog, QMessageBox, QTextEdit, QListWidget,
    QListWidgetItem, QFormLayout, QCheckBox, QSpinBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QSizePolicy, QWidget, QFrame, QSplitter, QInputDialog
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QBrush
import logging

# Configure logging
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

class ConfigDialog(QDialog):
    """Dialog to add or edit parser configurations."""
    def __init__(self, existing_config=None):
        super().__init__()
        self.setWindowTitle("Parser Configuration")
        self.config = existing_config.copy() if existing_config else {}
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # Machine Name
        machine_layout = QHBoxLayout()
        machine_label = QLabel("Machine Name:")
        self.machine_name_input = QLineEdit(self.config.get('machine_name', ''))
        machine_layout.addWidget(machine_label)
        machine_layout.addWidget(self.machine_name_input)
        layout.addLayout(machine_layout)

        # Parser Type
        parser_type_layout = QHBoxLayout()
        parser_type_label = QLabel("Parser Type:")
        self.parser_type_combo = QComboBox()
        self.parser_type_combo.addItems(["single", "multi", "COM"])
        self.parser_type_combo.setCurrentText(self.config.get('parser_type', 'single'))
        self.parser_type_combo.currentTextChanged.connect(self.update_parser_type_fields)
        parser_type_layout.addWidget(parser_type_label)
        parser_type_layout.addWidget(self.parser_type_combo)
        layout.addLayout(parser_type_layout)

        # Parser-specific Fields
        self.parser_fields_layout = QVBoxLayout()
        layout.addLayout(self.parser_fields_layout)
        self.update_parser_type_fields(self.parser_type_combo.currentText())

        # Header Fields
        header_layout = QVBoxLayout()
        header_label = QLabel("Header (comma-separated):")
        self.header_input = QLineEdit(','.join(self.config.get('header', [])))
        header_layout.addWidget(header_label)
        header_layout.addWidget(self.header_input)
        layout.addLayout(header_layout)

        # Data Actions
        data_actions_layout = QHBoxLayout()
        data_actions_label = QLabel("Data Actions:")
        self.data_actions_button = QPushButton("Edit Data Actions")
        self.data_actions_button.clicked.connect(self.edit_data_actions)
        self.visual_editor_button = QPushButton("Visual Editor")
        self.visual_editor_button.clicked.connect(self.open_visual_editor)
        data_actions_layout.addWidget(data_actions_label)
        data_actions_layout.addWidget(self.data_actions_button)
        data_actions_layout.addWidget(self.visual_editor_button)
        layout.addLayout(data_actions_layout)

        # Update Interval
        update_interval_layout = QHBoxLayout()
        update_interval_label = QLabel("Update Interval (seconds):")
        self.update_interval_input = QLineEdit(str(self.config.get('update_interval', 60)))
        update_interval_layout.addWidget(update_interval_label)
        update_interval_layout.addWidget(self.update_interval_input)
        layout.addLayout(update_interval_layout)

        # --- NEW: Lab ID Prompting ---
        lab_id_layout = QHBoxLayout()
        self.lab_id_checkbox = QCheckBox("Enable Lab ID Prompting")
        self.lab_id_checkbox.setChecked(self.config.get('lab_id_prompting', False))
        lab_id_layout.addWidget(self.lab_id_checkbox)

        lab_id_layout.addWidget(QLabel("Lab ID Column:"))
        self.lab_id_column_input = QSpinBox()
        self.lab_id_column_input.setMinimum(1)
        self.lab_id_column_input.setValue(self.config.get('lab_id_column', 1))
        lab_id_layout.addWidget(self.lab_id_column_input)

        layout.addLayout(lab_id_layout)
        # --- End ---

        # --- Apply Correction Factors toggle ---
        self.correction_factors_checkbox = QCheckBox("Apply Correction Factors")
        self.correction_factors_checkbox.setChecked(self.config.get('apply_correction_factors', False))
        layout.addWidget(self.correction_factors_checkbox)
        # --- End ---

        # Save and Cancel Buttons
        buttons_layout = QHBoxLayout()
        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_config)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        buttons_layout.addWidget(save_button)
        buttons_layout.addWidget(cancel_button)
        layout.addLayout(buttons_layout)

        self.setLayout(layout)

    def create_file_selector(self, label_text, default_path=""):
        layout = QHBoxLayout()
        label = QLabel(label_text)
        line_edit = QLineEdit(default_path)
        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(lambda: self.browse_file(line_edit))
        layout.addWidget(label)
        layout.addWidget(line_edit)
        layout.addWidget(browse_button)
        return layout, line_edit

    def create_directory_selector(self, label_text, default_path=""):
        layout = QHBoxLayout()
        label = QLabel(label_text)
        line_edit = QLineEdit(default_path)
        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(lambda: self.browse_directory(line_edit))
        layout.addWidget(label)
        layout.addWidget(line_edit)
        layout.addWidget(browse_button)
        return layout, line_edit

    def browse_file(self, line_edit):
        file_name, _ = QFileDialog.getOpenFileName(self, "Select File")
        if file_name:
            line_edit.setText(file_name)

    def browse_directory(self, line_edit):
        directory = QFileDialog.getExistingDirectory(self, "Select Directory")
        if directory:
            line_edit.setText(directory)

    def update_parser_type_fields(self, parser_type):
        """Update the parser-specific fields based on the selected parser type."""
        # Clear previous fields
        while self.parser_fields_layout.count():
            item = self.parser_fields_layout.takeAt(0)
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

        if parser_type == "single":
            single_cfg = self.config.get('single_csv', {})
            input_path = single_cfg.get('input', '')
            output_path = os.path.dirname(single_cfg.get('output', ''))
            delimiter = single_cfg.get('delimiter', ',')

            input_layout, self.single_input_file = self.create_file_selector("Input CSV File:", input_path)
            output_layout, self.single_output_file = self.create_directory_selector("Output Directory:", output_path)
            delimiter_layout = QHBoxLayout()
            delimiter_label = QLabel("Delimiter:")
            self.single_delimiter_input = QLineEdit(delimiter)
            delimiter_layout.addWidget(delimiter_label)
            delimiter_layout.addWidget(self.single_delimiter_input)

            # NEW: checkbox to re-parse entire file
            self.reparse_checkbox = QCheckBox("Re-parse Entire File?")
            self.reparse_checkbox.setChecked(False)

            self.parser_fields_layout.addLayout(input_layout)
            self.parser_fields_layout.addLayout(output_layout)
            self.parser_fields_layout.addLayout(delimiter_layout)
            self.parser_fields_layout.addWidget(self.reparse_checkbox)

        elif parser_type == "multi":
            multi_cfg = self.config.get('multi', {})
            input_dir = multi_cfg.get('input', '')
            move_dir = multi_cfg.get('move', '')
            output_dir = multi_cfg.get('output', '')
            delimiter = multi_cfg.get('delimiter', ',')
            has_header = multi_cfg.get('has_header', False)

            input_layout, self.multi_input_dir = self.create_directory_selector("Input Directory:", input_dir)
            move_layout, self.multi_move_dir = self.create_directory_selector("Move Directory:", move_dir)
            output_layout, self.multi_output_dir = self.create_directory_selector("Output Directory:", output_dir)
            delimiter_layout = QHBoxLayout()
            delimiter_label = QLabel("Delimiter:")
            self.multi_delimiter_input = QLineEdit(delimiter)
            delimiter_layout.addWidget(delimiter_label)
            delimiter_layout.addWidget(self.multi_delimiter_input)
            self.has_header_checkbox = QCheckBox("CSV files have header row")
            self.has_header_checkbox.setChecked(has_header)

            self.parser_fields_layout.addLayout(input_layout)
            self.parser_fields_layout.addLayout(move_layout)
            self.parser_fields_layout.addLayout(output_layout)
            self.parser_fields_layout.addLayout(delimiter_layout)
            self.parser_fields_layout.addWidget(self.has_header_checkbox)

        elif parser_type == "COM":
            com_cfg = self.config.get('COM', {})
            port_val = com_cfg.get('port', '')
            baud_val = str(com_cfg.get('baud_rate', 9600))
            output_dir = os.path.dirname(com_cfg.get('output', ''))

            form_layout = QFormLayout()
            self.port_input = QLineEdit(port_val)
            self.baud_rate_input = QLineEdit(baud_val)
            output_layout, self.com_output_dir = self.create_directory_selector("Output Directory:", output_dir)
            self.advanced_button = QPushButton("Advanced COM Settings")
            self.advanced_button.clicked.connect(self.open_advanced_com_settings)

            form_layout.addRow("COM Port:", self.port_input)
            form_layout.addRow("Baud Rate:", self.baud_rate_input)
            self.parser_fields_layout.addLayout(form_layout)
            self.parser_fields_layout.addLayout(output_layout)
            self.parser_fields_layout.addWidget(self.advanced_button)

    def open_advanced_com_settings(self):
        existing_settings = self.config.get('COM', {}).copy()
        dialog = AdvancedCOMSettingsDialog(existing_settings)
        if dialog.exec_():
            self.config.setdefault('COM', {})
            self.config['COM'].update(dialog.get_settings())

    def edit_data_actions(self):
        existing_actions = self.config.get('data', [])

        # Extract current input file and delimiter from the active UI fields
        input_file = ''
        delimiter = ','
        parser_type = self.parser_type_combo.currentText()
        if parser_type == 'single' and hasattr(self, 'single_input_file'):
            input_file = self.single_input_file.text().strip()
            if hasattr(self, 'single_delimiter_input'):
                delimiter = self.single_delimiter_input.text().strip() or ','
        elif parser_type == 'multi' and hasattr(self, 'multi_input_dir'):
            input_dir = self.multi_input_dir.text().strip()
            if os.path.isdir(input_dir):
                csvs = sorted(f for f in os.listdir(input_dir) if f.lower().endswith('.csv'))
                if csvs:
                    input_file = os.path.join(input_dir, csvs[0])
            if hasattr(self, 'multi_delimiter_input'):
                delimiter = self.multi_delimiter_input.text().strip() or ','

        dialog = DataActionsDialog(existing_actions, input_file=input_file, delimiter=delimiter)
        if dialog.exec_():
            self.config['data'] = dialog.get_actions()

    def open_visual_editor(self):
        """Open the visual table editor to arrange columns and set header names."""
        input_file = ''
        delimiter = ','
        parser_type = self.parser_type_combo.currentText()
        if parser_type == 'single' and hasattr(self, 'single_input_file'):
            input_file = self.single_input_file.text().strip()
            if hasattr(self, 'single_delimiter_input'):
                delimiter = self.single_delimiter_input.text().strip() or ','
        elif parser_type == 'multi' and hasattr(self, 'multi_input_dir'):
            input_dir = self.multi_input_dir.text().strip()
            if os.path.isdir(input_dir):
                csvs = sorted(f for f in os.listdir(input_dir) if f.lower().endswith('.csv'))
                if csvs:
                    input_file = os.path.join(input_dir, csvs[0])
            if hasattr(self, 'multi_delimiter_input'):
                delimiter = self.multi_delimiter_input.text().strip() or ','

        existing_header = [h.strip() for h in self.header_input.text().split(',') if h.strip()]
        existing_actions = self.config.get('data', [])

        dialog = VisualParserEditorDialog(
            input_file=input_file,
            delimiter=delimiter,
            existing_header=existing_header,
            existing_actions=existing_actions,
            parent=self
        )
        if dialog.exec_():
            new_header, new_actions = dialog.get_result()
            self.header_input.setText(','.join(new_header))
            self.config['data'] = new_actions

    def save_config(self):
        """Save the configuration and close the dialog."""
        machine_name = self.machine_name_input.text().strip()
        if not machine_name:
            QMessageBox.warning(self, "Input Error", "Machine name cannot be empty.")
            return

        parser_type = self.parser_type_combo.currentText()
        self.config['machine_name'] = machine_name
        self.config['parser_type'] = parser_type

        # Create configuration folder
        config_folder = os.path.join(CONFIG_DIR, machine_name)
        if not os.path.exists(config_folder):
            os.makedirs(config_folder)
        self.config['config_folder'] = config_folder

        # Header
        header_text = self.header_input.text().strip()
        self.config['header'] = [h.strip() for h in header_text.split(',') if h.strip()]

        # Update Interval
        try:
            update_interval = int(self.update_interval_input.text().strip())
            self.config['update_interval'] = update_interval
        except ValueError:
            QMessageBox.warning(self, "Input Error", "Update interval must be an integer.")
            return

        # --- Save Lab ID Prompting ---
        self.config['lab_id_prompting'] = self.lab_id_checkbox.isChecked()
        self.config['lab_id_column'] = self.lab_id_column_input.value()

        # --- Save Apply Correction Factors toggle ---
        self.config['apply_correction_factors'] = self.correction_factors_checkbox.isChecked()

        if parser_type == "single":
            input_file = self.single_input_file.text().strip()
            output_dir = self.single_output_file.text().strip()
            delimiter = self.single_delimiter_input.text().strip()

            if not input_file or not os.path.isfile(input_file):
                QMessageBox.warning(self, "Input Error", "Invalid input CSV file.")
                return
            if not output_dir or not os.path.isdir(output_dir):
                QMessageBox.warning(self, "Input Error", "Invalid output directory.")
                return

            output_csv = os.path.join(output_dir, f"{machine_name}_parsed.csv")

            # If re-parse entire file is checked, reset last_position to 0
            last_pos = 0
            single_cfg = self.config.get('single_csv', {})
            if 'last_position' in single_cfg:
                last_pos = single_cfg['last_position']
            if getattr(self, 'reparse_checkbox', None):
                if self.reparse_checkbox.isChecked():
                    last_pos = 0  # reset

            self.config['single_csv'] = {
                'input': input_file,
                'output': output_csv,
                'delimiter': delimiter,
                'last_position': last_pos
            }

        elif parser_type == "multi":
            input_dir = self.multi_input_dir.text().strip()
            move_dir = self.multi_move_dir.text().strip()
            output_dir = self.multi_output_dir.text().strip()
            delimiter = self.multi_delimiter_input.text().strip()
            has_header = self.has_header_checkbox.isChecked()

            if not input_dir or not os.path.isdir(input_dir):
                QMessageBox.warning(self, "Input Error", "Invalid input directory.")
                return
            if not move_dir or not os.path.isdir(move_dir):
                QMessageBox.warning(self, "Input Error", "Invalid move directory.")
                return
            if not output_dir or not os.path.isdir(output_dir):
                QMessageBox.warning(self, "Input Error", "Invalid output directory.")
                return

            output_csv = os.path.join(output_dir, f"{machine_name}_parsed.csv")
            self.config['multi'] = {
                'input': input_dir,
                'move': move_dir,
                'output': output_dir,
                'output_file': output_csv,
                'delimiter': delimiter,
                'has_header': has_header
            }

        elif parser_type == "COM":
            port = self.port_input.text().strip()
            baud_rate = self.baud_rate_input.text().strip()
            output_dir = self.com_output_dir.text().strip()

            if not port:
                QMessageBox.warning(self, "Input Error", "COM port cannot be empty.")
                return
            if not baud_rate.isdigit():
                QMessageBox.warning(self, "Input Error", "Baud rate must be an integer.")
                return
            if not output_dir or not os.path.isdir(output_dir):
                QMessageBox.warning(self, "Input Error", "Invalid output directory.")
                return

            output_csv = os.path.join(output_dir, f"{machine_name}_parsed.csv")
            existing_com = self.config.get('COM', {})

            parity_value = str(existing_com.get('parity', 'N')).strip().upper() or 'N'

            stop_bits_value = existing_com.get('stop_bits', 1)
            try:
                stop_bits_value = float(stop_bits_value)
            except (TypeError, ValueError):
                stop_bits_value = 1

            byte_size_value = existing_com.get('byte_size', 8)
            try:
                byte_size_value = int(byte_size_value)
            except (TypeError, ValueError):
                byte_size_value = 8

            timeout_value = existing_com.get('timeout', 1)
            try:
                timeout_value = float(timeout_value)
            except (TypeError, ValueError):
                timeout_value = 1.0

            idle_gap_value = existing_com.get('idle_gap', 0.5)
            try:
                idle_gap_value = float(idle_gap_value)
            except (TypeError, ValueError):
                idle_gap_value = 0.5
            if idle_gap_value <= 0:
                idle_gap_value = 0.5

            self.config['COM'] = {
                'port': port,
                'baud_rate': int(baud_rate),
                'output': output_csv,
                'parity': parity_value,
                'stop_bits': stop_bits_value,
                'byte_size': byte_size_value,
                'timeout': timeout_value,
                'idle_gap': idle_gap_value
            }

        # Save configuration to file
        config_file = os.path.join(config_folder, 'parser_config.json')
        try:
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4)
            log_message('info', f"Saved configuration for '{machine_name}'")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save configuration: {e}")
            log_message('error', f"Failed to save configuration for '{machine_name}': {e}")
            return

        self.accept()

    def get_config(self):
        """Return the updated configuration."""
        return self.config

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

        self.idle_gap_input = QLineEdit(str(self.settings.get('idle_gap', 0.5)))

        layout.addRow("Parity (N, E, O):", self.parity_input)
        layout.addRow("Stop Bits (1, 1.5, 2):", self.stop_bits_input)
        layout.addRow("Byte Size (5, 6, 7, 8):", self.byte_size_input)
        layout.addRow("Timeout (seconds):", self.timeout_input)
        layout.addRow("Idle Gap (seconds):", self.idle_gap_input)

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
        try:
            self.settings['timeout'] = float(self.timeout_input.text().strip())
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Timeout must be numeric.")
            return
        try:
            idle_gap_val = float(self.idle_gap_input.text().strip())
            if idle_gap_val <= 0:
                raise ValueError
            self.settings['idle_gap'] = idle_gap_val
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Idle gap must be a positive number.")
            return
        self.accept()

    def get_settings(self):
        return self.settings

class DataActionsDialog(QDialog):
    """Dialog to edit data actions."""
    def __init__(self, existing_actions=None, input_file='', delimiter=','):
        super().__init__()
        self.setWindowTitle("Edit Data Actions")
        self.actions = existing_actions.copy() if existing_actions else []
        self.input_file = input_file
        self.delimiter = delimiter
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
        dialog = ActionEditorDialog(input_file=self.input_file, delimiter=self.delimiter)
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
        dialog = ActionEditorDialog(action, input_file=self.input_file, delimiter=self.delimiter)
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
    def __init__(self, action=None, input_file='', delimiter=','):
        super().__init__()
        self.setWindowTitle("Edit Action")
        self.action = action.copy() if action else {}
        self.input_file = input_file
        self.delimiter = delimiter if delimiter else ','
        self._using_reorder_list = False
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
        while self.parameters_layout.count():
            item = self.parameters_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            else:
                sub_layout = item.layout()
                if sub_layout:
                    while sub_layout.count():
                        sub_item = sub_layout.takeAt(0)
                        sub_widget = sub_item.widget()
                        if sub_widget is not None:
                            sub_widget.deleteLater()

        if action_type == "reorder":
            self._using_reorder_list = True

            first_row = self._read_first_row()
            existing_order = self.action.get('order', [])

            if first_row:
                num_cols = len(first_row)
                col_map = {i + 1: first_row[i] for i in range(num_cols)}
            else:
                # No file available — derive column count from existing order
                valid_indices = [i for i in existing_order if i != 9999]
                num_cols = max(valid_indices) if valid_indices else 10
                col_map = {}

            info_label = QLabel(
                "Drag rows to set column order. Use 'Remove' to exclude a column.\n"
                "Use 'Add Empty' to insert a blank column placeholder."
                + ("" if first_row else "\n(No input file found — showing column indices only.)")
            )
            info_label.setStyleSheet("font-style: italic; color: gray;")
            self.parameters_layout.addWidget(info_label)

            self.reorder_list = QListWidget()
            self.reorder_list.setDragDropMode(QListWidget.InternalMove)
            self.reorder_list.setDefaultDropAction(Qt.MoveAction)
            self.reorder_list.setSelectionMode(QListWidget.SingleSelection)
            self.reorder_list.setMinimumHeight(200)

            # Build ordered column list: existing order first, then remaining file columns
            ordered_cols = []
            seen = set()
            for idx in existing_order:
                if idx not in seen:
                    ordered_cols.append(idx)
                    seen.add(idx)
            for idx in range(1, num_cols + 1):
                if idx not in seen:
                    ordered_cols.append(idx)
                    seen.add(idx)

            for col_num in ordered_cols:
                if col_num == 9999 or col_num > num_cols:
                    display = "Empty Column"
                    col_num = 9999
                elif col_map:
                    cell_val = col_map.get(col_num, '')
                    display = f"Col {col_num}: {cell_val}" if cell_val.strip() else f"Col {col_num}: (empty)"
                else:
                    display = f"Col {col_num}"
                item = QListWidgetItem(display)
                item.setData(Qt.UserRole, col_num)
                self.reorder_list.addItem(item)

            self.parameters_layout.addWidget(self.reorder_list)

            btn_layout = QHBoxLayout()
            add_empty_btn = QPushButton("Add Empty")
            remove_btn = QPushButton("Remove Selected")
            add_empty_btn.clicked.connect(self._add_empty_column)
            remove_btn.clicked.connect(self._remove_selected_column)
            btn_layout.addWidget(add_empty_btn)
            btn_layout.addWidget(remove_btn)
            self.parameters_layout.addLayout(btn_layout)

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
            example_label = QLabel(
                "Use 'C' followed by column index to refer to columns.\n"
                "Example:\n"
                "C1 = C2 + C3\n"
                "C2 = round(C2 * 0.01, 2)\n"
                "NewColumn = C1 * 3.14\n"
                "Use 'round(expr, decimals)' to apply rounding.\n"
            )
            example_label.setStyleSheet("font-style: italic; color: gray;")
            form_layout.addRow("Operations (one per line):", self.operations_input)
            form_layout.addRow(example_label)
            self.parameters_layout.addLayout(form_layout)

    def populate_fields(self):
        """Populate fields with existing action data."""
        action_type = self.action.get('action', '')
        self.action_type_combo.setCurrentText(action_type)

        if action_type == "reorder":
            pass  # list is pre-ordered in update_parameters_fields

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

    def save_action(self):
        """Save the action data."""
        action_type = self.action_type_combo.currentText()
        self.action['action'] = action_type

        if action_type == "reorder":
            if self._using_reorder_list:
                order = []
                for i in range(self.reorder_list.count()):
                    item = self.reorder_list.item(i)
                    order.append(item.data(Qt.UserRole))
                if not order:
                    QMessageBox.warning(self, "Invalid Input", "Please include at least one column.")
                    return
                self.action['order'] = order
            else:
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
            if not operations:
                QMessageBox.warning(self, "Invalid Input", "Please enter at least one math operation.")
                return
            self.action['operations'] = operations

        self.accept()

    def _read_first_row(self):
        """Read the first non-empty row from the configured input CSV file."""
        if not self.input_file or not os.path.isfile(self.input_file):
            return None
        try:
            import csv as _csv
            with open(self.input_file, 'r', encoding='utf-8', errors='replace') as f:
                reader = _csv.reader(f, delimiter=self.delimiter)
                for row in reader:
                    if any(cell.strip() for cell in row):
                        return row
        except Exception:
            pass
        return None

    def _add_empty_column(self):
        item = QListWidgetItem("Empty Column")
        item.setData(Qt.UserRole, 9999)
        self.reorder_list.addItem(item)

    def _remove_selected_column(self):
        for item in self.reorder_list.selectedItems():
            self.reorder_list.takeItem(self.reorder_list.row(item))

    def get_action(self):
        """Return the action data."""
        return self.action


class VisualParserEditorDialog(QDialog):
    """
    Visual editor for parser column configuration.

    Left pane  — drag-reorderable column list; remove-substring rules.
    Right pane — read-only CSV preview; per-column settings panel
                 (header name, math formula, force-to-cell substring).
    Toolbar    — Undo (unlimited) and Reset buttons.

    On save, generates: reorder, force_to_cell, remove, and
    math_operations actions, plus an updated header list.
    """

    _HEADER_BG = QColor(50, 90, 150)
    _HEADER_FG = QColor(255, 255, 255)

    def __init__(self, input_file='', delimiter=',', existing_header=None,
                 existing_actions=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Visual Parser Editor")
        self.resize(1080, 700)
        self.input_file = input_file
        self.delimiter = delimiter or ','
        self.existing_header = list(existing_header) if existing_header else []
        self.existing_actions = list(existing_actions) if existing_actions else []

        # Column state: each column is a dict stored by unique integer ID.
        # _col_map[id] = {_id, csv_col, header, formula, force_substring}
        # csv_col: 1-based CSV source column, or 9999 for empty placeholder.
        self._col_map = {}
        self._next_id = 0
        self._remove_rules = []   # list of substrings to strip from all cells
        self._undo_stack = []     # list of state snapshots
        self._selected_id = None  # ID of currently selected column
        self._panel_lock = False  # guard against recursive widget updates
        self._result_header = None
        self._result_actions = None

        self._raw_rows = self._load_csv_rows()
        initial_order = self._init_from_existing()
        self._init_ui(initial_order)

    # ── Internal helpers ──────────────────────────────────────────────────

    def _new_col(self, csv_col, header, formula='', force_substring=''):
        """Create a new column entry and return its ID."""
        cid = self._next_id
        self._next_id += 1
        self._col_map[cid] = {
            '_id': cid,
            'csv_col': csv_col,
            'header': header,
            'formula': formula,
            'force_substring': force_substring,
        }
        return cid

    def _load_csv_rows(self, max_rows=10):
        if not self.input_file or not os.path.isfile(self.input_file):
            return []
        try:
            import csv as _csv
            rows = []
            with open(self.input_file, 'r', encoding='utf-8', errors='replace') as f:
                reader = _csv.reader(f, delimiter=self.delimiter)
                for i, row in enumerate(reader):
                    if i >= max_rows:
                        break
                    rows.append(row)
            return rows
        except Exception:
            return []

    def _init_from_existing(self):
        """
        Parse existing_actions + header config into internal column state.
        Returns the initial ordered list of column IDs.
        """
        reorder_order = []
        force_map = {}       # 0-based output index -> substring
        math_formulas = {}   # 0-based output index -> rhs expression
        self._remove_rules = []

        for a in self.existing_actions:
            act = a.get('action', '')
            if act == 'reorder':
                reorder_order = list(a.get('order', []))
            elif act == 'force_to_cell':
                idx0 = max(0, a.get('target_column', 1) - 1)
                force_map[idx0] = a.get('substring', '')
            elif act == 'remove':
                s = a.get('substring', '').strip()
                if s:
                    self._remove_rules.append(s)
            elif act == 'math_operations':
                for op in a.get('operations', []):
                    parts = op.split('=', 1)
                    if len(parts) == 2:
                        lhs, rhs = parts[0].strip(), parts[1].strip()
                        if lhs.upper().startswith('C') and lhs[1:].isdigit():
                            math_formulas[int(lhs[1:]) - 1] = rhs  # 0-based

        num_csv = max((len(r) for r in self._raw_rows), default=0)
        if not reorder_order:
            if num_csv:
                reorder_order = list(range(1, num_csv + 1))
            else:
                reorder_order = list(range(1, len(self.existing_header) + 1))

        order = []
        for i, csv_col in enumerate(reorder_order):
            header = (
                self.existing_header[i]
                if i < len(self.existing_header)
                else ('Empty' if csv_col == 9999 else f'Col {csv_col}')
            )
            cid = self._new_col(
                csv_col=csv_col,
                header=header,
                formula=math_formulas.get(i, ''),
                force_substring=force_map.get(i, ''),
            )
            order.append(cid)

        # Append any CSV columns that weren't in the existing reorder so the
        # user can see and drag in every available column from the input file.
        included = set(c for c in reorder_order if c != 9999)
        for csv_col in range(1, num_csv + 1):
            if csv_col not in included:
                cid = self._new_col(
                    csv_col=csv_col,
                    header=f'Col {csv_col}',
                    formula='',
                    force_substring='',
                )
                order.append(cid)

        return order

    def _item_label(self, col):
        """Build the display string for a column list item."""
        name = col['header'].strip() or ('Empty' if col['csv_col'] == 9999 else f"Col {col['csv_col']}")
        src = '[ ]' if col['csv_col'] == 9999 else f"[{col['csv_col']}]"
        badges = []
        if col['formula'].strip():
            preview = col['formula'].strip()
            if len(preview) > 22:
                preview = preview[:19] + '...'
            badges.append(f"= {preview}")
        if col['force_substring'].strip():
            preview = col['force_substring'].strip()
            if len(preview) > 15:
                preview = preview[:12] + '...'
            badges.append(f'⟵ "{preview}"')
        suffix = '   ' + '  |  '.join(badges) if badges else ''
        return f"{src}  {name}{suffix}"

    # ── Undo / Reset ──────────────────────────────────────────────────────

    def _snapshot(self):
        import copy
        return {
            'order': self._get_current_order(),
            'col_map': copy.deepcopy(self._col_map),
            'remove_rules': list(self._remove_rules),
            'next_id': self._next_id,
        }

    def _push_undo(self, *_):
        self._undo_stack.append(self._snapshot())
        self._undo_btn.setEnabled(True)

    def _undo(self):
        if not self._undo_stack:
            return
        import copy
        snap = self._undo_stack.pop()
        self._col_map = copy.deepcopy(snap['col_map'])
        self._remove_rules = list(snap['remove_rules'])
        self._next_id = snap['next_id']
        self._undo_btn.setEnabled(bool(self._undo_stack))
        self._rebuild_col_list(snap['order'])
        self._refresh_remove_list()
        self._clear_panel()

    def _reset(self):
        if QMessageBox.question(
            self, "Reset",
            "Reset all changes back to the original configuration?",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self._push_undo()
        self._col_map.clear()
        self._next_id = 0
        self._remove_rules = []
        order = self._init_from_existing()
        self._rebuild_col_list(order)
        self._refresh_remove_list()
        self._clear_panel()

    # ── Column list helpers ───────────────────────────────────────────────

    def _get_current_order(self):
        return [self.col_list.item(i).data(Qt.UserRole)
                for i in range(self.col_list.count())]

    def _rebuild_col_list(self, order):
        self.col_list.blockSignals(True)
        self.col_list.clear()
        for cid in order:
            col = self._col_map.get(cid)
            if col is None:
                continue
            item = QListWidgetItem(self._item_label(col))
            item.setData(Qt.UserRole, cid)
            if col['csv_col'] == 9999:
                item.setForeground(QBrush(QColor(150, 150, 150)))
            self.col_list.addItem(item)
        self.col_list.blockSignals(False)

    def _refresh_col_item(self, cid):
        """Refresh just the label of one list item."""
        for i in range(self.col_list.count()):
            item = self.col_list.item(i)
            if item.data(Qt.UserRole) == cid:
                item.setText(self._item_label(self._col_map[cid]))
                break

    # ── Column list slots ─────────────────────────────────────────────────

    def _on_col_selected(self, item):
        cid = item.data(Qt.UserRole)
        self._selected_id = cid
        col = self._col_map.get(cid, {})
        is_empty = col.get('csv_col') == 9999

        self._panel_lock = True
        label = col.get('header') or ('Empty Column' if is_empty else f"Col {col.get('csv_col')}")
        self._col_settings_label.setText(f"<b>Column settings:</b>  {label}")
        self._header_edit.setText(col.get('header', ''))
        self._header_edit.setEnabled(True)
        self._formula_edit.setText(col.get('formula', ''))
        self._formula_edit.setEnabled(not is_empty)
        has_force = bool(col.get('force_substring', '').strip())
        self._force_check.setChecked(has_force)
        self._force_check.setEnabled(not is_empty)
        self._force_edit.setText(col.get('force_substring', ''))
        self._force_edit.setEnabled(not is_empty and has_force)
        self._panel_lock = False

        # Highlight the matching column in the preview table.
        # Block signals to prevent the selection change from re-entering _on_col_selected.
        if hasattr(self, 'preview_table') and not is_empty:
            csv_col_0 = col.get('csv_col', 1) - 1
            if 0 <= csv_col_0 < self.preview_table.columnCount():
                self.preview_table.blockSignals(True)
                self.preview_table.clearSelection()
                self.preview_table.selectColumn(csv_col_0)
                self.preview_table.blockSignals(False)

    def _add_empty_col(self):
        self._push_undo()
        cid = self._new_col(csv_col=9999, header='', formula='', force_substring='')
        item = QListWidgetItem(self._item_label(self._col_map[cid]))
        item.setData(Qt.UserRole, cid)
        item.setForeground(QBrush(QColor(150, 150, 150)))
        self.col_list.addItem(item)

    def _remove_col(self):
        selected = self.col_list.selectedItems()
        if not selected:
            return
        self._push_undo()
        for item in selected:
            cid = item.data(Qt.UserRole)
            self.col_list.takeItem(self.col_list.row(item))
            self._col_map.pop(cid, None)
        self._clear_panel()

    # ── Column settings panel slots ───────────────────────────────────────

    def _on_header_edited(self, text):
        if self._panel_lock or self._selected_id is None:
            return
        self._col_map[self._selected_id]['header'] = text
        self._refresh_col_item(self._selected_id)

    def _on_formula_edited(self, text):
        if self._panel_lock or self._selected_id is None:
            return
        self._col_map[self._selected_id]['formula'] = text
        self._refresh_col_item(self._selected_id)

    def _on_force_toggled(self, checked):
        if self._panel_lock:
            return
        self._force_edit.setEnabled(checked)
        if not checked and self._selected_id is not None:
            self._col_map[self._selected_id]['force_substring'] = ''
            self._force_edit.setText('')
            self._refresh_col_item(self._selected_id)

    def _on_force_edited(self, text):
        if self._panel_lock or self._selected_id is None:
            return
        self._col_map[self._selected_id]['force_substring'] = text
        self._refresh_col_item(self._selected_id)

    def _clear_panel(self):
        self._selected_id = None
        self._panel_lock = True
        self._col_settings_label.setText(
            "<i>Click a column in the list on the left to edit its settings.</i>"
        )
        self._header_edit.setText('')
        self._header_edit.setEnabled(False)
        self._formula_edit.setText('')
        self._formula_edit.setEnabled(False)
        self._force_check.setChecked(False)
        self._force_check.setEnabled(False)
        self._force_edit.setText('')
        self._force_edit.setEnabled(False)
        self._panel_lock = False

    # ── Remove rules slots ────────────────────────────────────────────────

    def _refresh_remove_list(self):
        self.remove_list.clear()
        for rule in self._remove_rules:
            self.remove_list.addItem(QListWidgetItem(rule))

    def _add_remove_rule(self):
        text, ok = QInputDialog.getText(
            self, "Add Remove Rule",
            "Substring to strip from every cell in every row:"
        )
        if ok and text.strip():
            self._push_undo()
            self._remove_rules.append(text.strip())
            self.remove_list.addItem(QListWidgetItem(text.strip()))

    def _delete_remove_rule(self):
        indices = sorted(
            [self.remove_list.row(item) for item in self.remove_list.selectedItems()],
            reverse=True,
        )
        if not indices:
            return
        self._push_undo()
        for idx in indices:
            self.remove_list.takeItem(idx)
            if idx < len(self._remove_rules):
                self._remove_rules.pop(idx)

    # ── Preview table slot ────────────────────────────────────────────────

    def _on_preview_col_clicked(self):
        if not hasattr(self, 'preview_table'):
            return
        cols = list(set(idx.column() for idx in self.preview_table.selectedIndexes()))
        if not cols:
            return
        csv_col = cols[0] + 1  # convert to 1-based
        for i in range(self.col_list.count()):
            item = self.col_list.item(i)
            cid = item.data(Qt.UserRole)
            if self._col_map.get(cid, {}).get('csv_col') == csv_col:
                self.col_list.setCurrentItem(item)
                self._on_col_selected(item)
                break

    # ── Save ──────────────────────────────────────────────────────────────

    def _save(self):
        order = self._get_current_order()
        new_header = []
        reorder_list = []
        math_ops = []
        force_actions = []

        for out_idx, cid in enumerate(order):
            col = self._col_map.get(cid)
            if not col:
                continue
            csv_col = col['csv_col']
            header = col['header'].strip() or ('Empty' if csv_col == 9999 else f'Col {csv_col}')
            new_header.append(header)
            reorder_list.append(csv_col)

            if col['formula'].strip() and csv_col != 9999:
                math_ops.append(f"C{out_idx + 1} = {col['formula'].strip()}")

            if col['force_substring'].strip() and csv_col != 9999:
                force_actions.append({
                    'action': 'force_to_cell',
                    'substring': col['force_substring'].strip(),
                    'target_column': out_idx + 1,
                })

        # Build action list in processing order:
        # force_to_cell → remove → reorder → math_operations
        new_actions = list(force_actions)
        for rule in self._remove_rules:
            new_actions.append({'action': 'remove', 'substring': rule})
        new_actions.append({'action': 'reorder', 'order': reorder_list})
        if math_ops:
            new_actions.append({'action': 'math_operations', 'operations': math_ops})

        # Preserve any unrecognised action types we don't manage here
        known = {'force_to_cell', 'remove', 'reorder', 'math_operations'}
        for a in self.existing_actions:
            if a.get('action') not in known:
                new_actions.append(a)

        self._result_header = new_header
        self._result_actions = new_actions
        self.accept()

    def get_result(self):
        """Returns (new_header: list[str], new_actions: list[dict])."""
        return (
            self._result_header if self._result_header is not None else self.existing_header,
            self._result_actions if self._result_actions is not None else self.existing_actions,
        )

    # ── UI construction ───────────────────────────────────────────────────

    def _init_ui(self, initial_order):
        root = QVBoxLayout()
        root.setSpacing(6)

        # ── Toolbar ──────────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel(
            "<b>Visual Parser Editor</b> — arrange output columns, "
            "set headers and transformations."
        ))
        toolbar.addStretch()
        self._undo_btn = QPushButton("↩  Undo")
        self._undo_btn.setToolTip("Undo last structural change (column add/remove/reorder, rule changes)")
        self._undo_btn.setEnabled(False)
        self._undo_btn.clicked.connect(self._undo)
        reset_btn = QPushButton("↺  Reset")
        reset_btn.setToolTip("Restore original configuration (adds undo point first)")
        reset_btn.clicked.connect(self._reset)
        toolbar.addWidget(self._undo_btn)
        toolbar.addWidget(reset_btn)
        root.addLayout(toolbar)

        sep0 = QFrame()
        sep0.setFrameShape(QFrame.HLine)
        root.addWidget(sep0)

        # ── Main splitter ─────────────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)

        # ── LEFT PANE: column list + remove rules ─────────────────────────
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(4, 4, 4, 4)
        ll.setSpacing(4)

        ll.addWidget(QLabel(
            "<b>Output Column Order</b>"
            " <small><i>— drag to reorder, click to edit</i></small>"
        ))

        self.col_list = QListWidget()
        self.col_list.setDragDropMode(QListWidget.InternalMove)
        self.col_list.setDefaultDropAction(Qt.MoveAction)
        self.col_list.setSelectionMode(QListWidget.SingleSelection)
        self.col_list.setSpacing(1)
        self.col_list.currentItemChanged.connect(
            lambda cur, _prev: self._on_col_selected(cur) if cur else self._clear_panel()
        )
        # Push undo BEFORE a drag reorder happens
        self.col_list.model().rowsAboutToBeMoved.connect(self._push_undo)
        ll.addWidget(self.col_list, 1)  # stretch=1 so it fills available height

        col_btns = QHBoxLayout()
        add_empty_btn = QPushButton("+ Empty Column")
        add_empty_btn.setToolTip("Append a blank placeholder column to the output")
        add_empty_btn.clicked.connect(self._add_empty_col)
        rm_col_btn = QPushButton("− Remove")
        rm_col_btn.setToolTip("Remove the selected column from the output")
        rm_col_btn.clicked.connect(self._remove_col)
        col_btns.addWidget(add_empty_btn)
        col_btns.addWidget(rm_col_btn)
        col_btns.addStretch()
        ll.addLayout(col_btns)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.HLine)
        ll.addWidget(sep1)

        ll.addWidget(QLabel(
            "<b>Remove Substrings</b>"
            " <small><i>— stripped from every cell</i></small>"
        ))
        self.remove_list = QListWidget()
        self.remove_list.setMaximumHeight(100)
        self.remove_list.setMinimumHeight(50)
        for rule in self._remove_rules:
            self.remove_list.addItem(QListWidgetItem(rule))
        ll.addWidget(self.remove_list)

        rule_btns = QHBoxLayout()
        add_rule_btn = QPushButton("+ Add Rule")
        add_rule_btn.clicked.connect(self._add_remove_rule)
        del_rule_btn = QPushButton("− Delete")
        del_rule_btn.clicked.connect(self._delete_remove_rule)
        rule_btns.addWidget(add_rule_btn)
        rule_btns.addWidget(del_rule_btn)
        rule_btns.addStretch()
        ll.addLayout(rule_btns)

        splitter.addWidget(left)

        # ── RIGHT PANE: vertical splitter (preview top, settings bottom) ────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 4, 4, 4)
        rl.setSpacing(0)

        right_vsplit = QSplitter(Qt.Vertical)

        # ── Top half: input file preview ─────────────────────────────────
        preview_container = QWidget()
        pvl = QVBoxLayout(preview_container)
        pvl.setContentsMargins(0, 2, 0, 2)
        pvl.setSpacing(3)

        if self._raw_rows:
            pvl.addWidget(QLabel(
                "<b>Input File Preview</b>"
                " <small><i>(click a column to jump to it in the list)</i></small>"
            ))
            num_pcols = max(len(r) for r in self._raw_rows)
            self.preview_table = QTableWidget(len(self._raw_rows), num_pcols)
            self.preview_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.preview_table.setSelectionBehavior(QAbstractItemView.SelectColumns)
            self.preview_table.setHorizontalHeaderLabels(
                [str(i + 1) for i in range(num_pcols)]
            )
            for r, row_data in enumerate(self._raw_rows):
                for c in range(num_pcols):
                    val = row_data[c] if c < len(row_data) else ''
                    self.preview_table.setItem(r, c, QTableWidgetItem(val))
            self.preview_table.resizeColumnsToContents()
            self.preview_table.itemSelectionChanged.connect(self._on_preview_col_clicked)
            pvl.addWidget(self.preview_table, 1)
        else:
            note = QLabel("No input file found — preview unavailable.")
            note.setStyleSheet("color: orange;")
            pvl.addWidget(note)

        right_vsplit.addWidget(preview_container)

        # ── Bottom half: column settings panel ───────────────────────────
        settings_container = QWidget()
        scl = QVBoxLayout(settings_container)
        scl.setContentsMargins(0, 4, 0, 2)
        scl.setSpacing(6)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        scl.addWidget(sep2)

        self._col_settings_label = QLabel(
            "<i>Click a column in the list on the left to edit its settings.</i>"
        )
        self._col_settings_label.setWordWrap(True)
        scl.addWidget(self._col_settings_label)

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setVerticalSpacing(8)

        # Header name
        self._header_edit = QLineEdit()
        self._header_edit.setPlaceholderText("Output column name")
        self._header_edit.setEnabled(False)
        self._header_edit.textEdited.connect(self._on_header_edited)
        form.addRow("Header Name:", self._header_edit)

        # Formula
        self._formula_edit = QLineEdit()
        self._formula_edit.setPlaceholderText("e.g.  round(C3 * 1000, 2)   or   C1 + C2")
        self._formula_edit.setEnabled(False)
        self._formula_edit.setToolTip(
            "Math expression applied to this column after reordering.\n"
            "CN = Nth output column (1-based, matching list order).\n"
            "Examples:  round(C3 * 1.8 + 32, 2)   C1 + C2   abs(C5)"
        )
        self._formula_edit.textEdited.connect(self._on_formula_edited)
        form.addRow("Formula (=):", self._formula_edit)

        # Force to cell
        force_row = QHBoxLayout()
        self._force_check = QCheckBox("Force to Cell")
        self._force_check.setEnabled(False)
        self._force_check.setToolTip(
            "Scan every cell in the incoming row for this substring.\n"
            "When found, copy that cell's entire value into THIS output column.\n"
            "Useful for values that can appear in any column (e.g. 'PSI', 'Sample')."
        )
        self._force_check.toggled.connect(self._on_force_toggled)
        self._force_edit = QLineEdit()
        self._force_edit.setPlaceholderText("Substring to detect in any cell")
        self._force_edit.setEnabled(False)
        self._force_edit.textEdited.connect(self._on_force_edited)
        force_row.addWidget(self._force_check)
        force_row.addWidget(self._force_edit, 1)
        form.addRow("", force_row)

        scl.addLayout(form)
        scl.addStretch(1)

        right_vsplit.addWidget(settings_container)

        # Distribute: ~55% preview, ~45% settings
        right_vsplit.setSizes([350, 280])
        rl.addWidget(right_vsplit, 1)

        splitter.addWidget(right)
        splitter.setSizes([360, 580])
        root.addWidget(splitter)

        # Save / Cancel
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)
        root.addLayout(btn_row)

        self.setLayout(root)

        # Populate column list with initial state
        self._rebuild_col_list(initial_order)
