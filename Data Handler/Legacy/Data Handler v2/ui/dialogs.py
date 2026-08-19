import os
import json
import shutil
import logging

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QHBoxLayout, QSlider, QComboBox, QPushButton,
    QFileDialog, QMessageBox, QLineEdit, QCheckBox, QFormLayout, QListWidget,
    QListWidgetItem, QTextEdit, QInputDialog
)
from PyQt5.QtCore import Qt

from backend.config_manager import ConfigManager

LOG = logging.getLogger(__name__)
CONFIG_DIR = os.path.join(os.path.expanduser('~'), 'csv_parser_configs')
THEMES_DIR = os.path.join(CONFIG_DIR, 'themes')

class ConfigDialog(QDialog):
    """
    Dialog to add or edit parser configurations.
    Integrates logic from the original config_dialogs.py.
    """
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
        data_actions_layout.addWidget(data_actions_label)
        data_actions_layout.addWidget(self.data_actions_button)
        layout.addLayout(data_actions_layout)

        # Update Interval
        update_interval_layout = QHBoxLayout()
        update_interval_label = QLabel("Update Interval (seconds):")
        default_interval = ConfigManager().settings.get('default_interval', 10)
        current_interval = self.config.get('update_interval', default_interval)
        self.update_interval_input = QLineEdit(str(current_interval))
        update_interval_layout.addWidget(update_interval_label)
        update_interval_layout.addWidget(self.update_interval_input)
        layout.addLayout(update_interval_layout)

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

    def create_file_selector(self, label_text):
        layout = QHBoxLayout()
        label = QLabel(label_text)
        line_edit = QLineEdit()
        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(lambda: self.browse_file(line_edit))
        layout.addWidget(label)
        layout.addWidget(line_edit)
        layout.addWidget(browse_button)
        return layout, line_edit

    def create_directory_selector(self, label_text):
        layout = QHBoxLayout()
        label = QLabel(label_text)
        line_edit = QLineEdit()
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
            input_layout, self.single_input_file = self.create_file_selector("Input CSV File:")
            output_layout, self.single_output_file = self.create_directory_selector("Output Directory:")
            delimiter_layout = QHBoxLayout()
            delimiter_label = QLabel("Delimiter:")
            self.single_delimiter_input = QLineEdit(self.config.get('single_csv', {}).get('delimiter', ','))
            delimiter_layout.addWidget(delimiter_label)
            delimiter_layout.addWidget(self.single_delimiter_input)

            self.parser_fields_layout.addLayout(input_layout)
            self.parser_fields_layout.addLayout(output_layout)
            self.parser_fields_layout.addLayout(delimiter_layout)

        elif parser_type == "multi":
            input_layout, self.multi_input_dir = self.create_directory_selector("Input Directory:")
            move_layout, self.multi_move_dir = self.create_directory_selector("Move Directory:")
            output_layout, self.multi_output_dir = self.create_directory_selector("Output Directory:")
            delimiter_layout = QHBoxLayout()
            delimiter_label = QLabel("Delimiter:")
            self.multi_delimiter_input = QLineEdit(self.config.get('multi', {}).get('delimiter', ','))
            delimiter_layout.addWidget(delimiter_label)
            delimiter_layout.addWidget(self.multi_delimiter_input)
            self.has_header_checkbox = QCheckBox("CSV files have header row")
            self.has_header_checkbox.setChecked(self.config.get('multi', {}).get('has_header', False))

            self.parser_fields_layout.addLayout(input_layout)
            self.parser_fields_layout.addLayout(move_layout)
            self.parser_fields_layout.addLayout(output_layout)
            self.parser_fields_layout.addLayout(delimiter_layout)
            self.parser_fields_layout.addWidget(self.has_header_checkbox)

        elif parser_type == "COM":
            form_layout = QFormLayout()
            self.port_input = QLineEdit(self.config.get('COM', {}).get('port', ''))
            self.baud_rate_input = QLineEdit(str(self.config.get('COM', {}).get('baud_rate', 9600)))
            output_layout, self.com_output_dir = self.create_directory_selector("Output Directory:")
            self.advanced_button = QPushButton("Advanced COM Settings")
            self.advanced_button.clicked.connect(self.open_advanced_com_settings)

            form_layout.addRow("COM Port:", self.port_input)
            form_layout.addRow("Baud Rate:", self.baud_rate_input)
            self.parser_fields_layout.addLayout(form_layout)
            self.parser_fields_layout.addLayout(output_layout)
            self.parser_fields_layout.addWidget(self.advanced_button)

    def open_advanced_com_settings(self):
        existing_settings = self.config.get('COM', {})
        dialog = AdvancedCOMSettingsDialog(existing_settings)
        if dialog.exec_():
            if 'COM' not in self.config:
                self.config['COM'] = {}
            self.config['COM'].update(dialog.get_settings())

    def edit_data_actions(self):
        existing_actions = self.config.get('data', [])
        dialog = DataActionsDialog(existing_actions)
        if dialog.exec_():
            self.config['data'] = dialog.get_actions()

    def save_config(self):
        machine_name = self.machine_name_input.text().strip()
        if not machine_name:
            QMessageBox.warning(self, "Input Error", "Machine name cannot be empty.")
            return

        parser_type = self.parser_type_combo.currentText()
        self.config['machine_name'] = machine_name
        self.config['parser_type'] = parser_type

        config_folder = os.path.join(CONFIG_DIR, machine_name)
        if not os.path.exists(config_folder):
            os.makedirs(config_folder)
        self.config['config_folder'] = config_folder

        header_text = self.header_input.text().strip()
        self.config['header'] = [h.strip() for h in header_text.split(',') if h.strip()]

        # Update Interval
        try:
            update_interval = int(self.update_interval_input.text().strip())
            self.config['update_interval'] = update_interval
        except ValueError:
            QMessageBox.warning(self, "Input Error", "Update interval must be an integer.")
            return

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
            self.config['single_csv'] = {
                'input': input_file,
                'output': output_csv,
                'delimiter': delimiter,
                'last_position': 0
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
            self.config['COM'] = {
                'port': port,
                'baud_rate': int(baud_rate),
                'output': output_csv,
                'parity': 'N',
                'stop_bits': 1,
                'byte_size': 8,
                'timeout': 1
            }

        # Save configuration to file
        config_file = os.path.join(config_folder, 'parser_config.json')
        try:
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4)
            LOG.info(f"Saved configuration for '{machine_name}'")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save configuration: {e}")
            LOG.error(f"Failed to save configuration for '{machine_name}': {e}", exc_info=True)
            return

        self.accept()

    def get_config(self):
        return self.config

class AdvancedCOMSettingsDialog(QDialog):
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
    def __init__(self, existing_actions=None):
        super().__init__()
        self.setWindowTitle("Edit Data Actions")
        self.actions = existing_actions.copy() if existing_actions else []
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        self.actions_list = QListWidget()
        for action in self.actions:
            item = QListWidgetItem(json.dumps(action))
            self.actions_list.addItem(item)
        layout.addWidget(self.actions_list)

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
    def __init__(self, action=None):
        super().__init__()
        self.setWindowTitle("Edit Action")
        self.action = action.copy() if action else {}
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        action_type_label = QLabel("Action Type:")
        self.action_type_combo = QComboBox()
        self.action_type_combo.addItems(["force_to_cell", "reorder", "remove", "math_operations"])
        layout.addWidget(action_type_label)
        layout.addWidget(self.action_type_combo)

        self.parameters_layout = QVBoxLayout()
        layout.addLayout(self.parameters_layout)

        self.action_type_combo.currentTextChanged.connect(self.update_parameters_fields)
        self.update_parameters_fields(self.action_type_combo.currentText())

        buttons_layout = QHBoxLayout()
        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_action)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        buttons_layout.addWidget(save_button)
        buttons_layout.addWidget(cancel_button)
        layout.addLayout(buttons_layout)

        self.setLayout(layout)

        if self.action:
            self.populate_fields()

    def update_parameters_fields(self, action_type):
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
            example_label = QLabel(
                "Use 'C' followed by column index to refer to columns.\n"
                "Example:\n"
                "C1 = C2 + C3\n"
                "C2 = C2 * 0.01\n"
                "NewColumn = C1 * 3.14\n"
                "Indices are after reordering. Numeric constants as usual."
            )
            form_layout.addRow("Operations (one per line):", self.operations_input)
            form_layout.addRow(example_label)
            self.parameters_layout.addLayout(form_layout)

    def populate_fields(self):
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

    def save_action(self):
        action_type = self.action_type_combo.currentText()
        self.action['action'] = action_type

        if action_type == "reorder":
            order_text = self.order_input.text()
            try:
                order = [int(idx.strip()) for idx in order_text.split(',') if idx.strip()]
                self.action['order'] = order
            except ValueError:
                QMessageBox.warning(self, "Invalid Input", "Please enter valid integers for the new order.")
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

    def get_action(self):
        return self.action

class SettingsDialog(QDialog):
    """
    Dialog for adjusting settings such as scale and theme.
    Integrates logic from the original settings_dialog.py.
    """
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.parent = parent
        self.settings = settings
        self.scale_factor = self.settings.get('scale_factor', 1.5)
        self.current_theme = self.settings.get('current_theme', 'default.css')
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # Scale Slider
        scale_layout = QHBoxLayout()
        scale_label = QLabel("Scale:")
        self.scale_slider = QSlider(Qt.Horizontal)
        # Allow more drastic scaling: range from 50% to 300%
        self.scale_slider.setMinimum(50)
        self.scale_slider.setMaximum(300)
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
                theme_name = os.path.basename(theme_file)
                destination = os.path.join(THEMES_DIR, theme_name)
                shutil.copyfile(theme_file, destination)
                self.theme_combo.addItem(theme_name)
                self.theme_combo.setCurrentText(theme_name)
                LOG.info(f"Loaded custom theme: {theme_name}")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to load custom theme: {e}")
                LOG.error(f"Failed to load custom theme '{theme_file}': {e}", exc_info=True)

    def apply_settings(self):
        scale_value = self.scale_slider.value()
        self.parent.scale_factor = scale_value / 100.0
        self.parent.apply_scale()

        selected_theme = self.theme_combo.currentText()
        self.parent.current_theme = selected_theme

        # Update settings
        config_manager = ConfigManager()
        new_settings = config_manager.settings
        new_settings['scale_factor'] = self.parent.scale_factor
        new_settings['current_theme'] = self.parent.current_theme
        config_manager.save_settings(new_settings)

        self.accept()
        pass
