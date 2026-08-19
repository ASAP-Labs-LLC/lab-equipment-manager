# ui.py
import sys
import os
import json
import pandas as pd
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QFileDialog, QLineEdit, QMessageBox,
    QTabWidget, QDialog, QTableView, QInputDialog, QListWidget, QListWidgetItem, QCheckBox, QSpinBox
)
from PyQt5.QtCore import QAbstractTableModel, Qt, QTimer
from data_handler import process_single_csv, process_multi_csv, process_com_port

CONFIG_DIR = os.path.join(os.path.expanduser('~'), 'csv_parser_configs')

class PandasModel(QAbstractTableModel):
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
    def __init__(self, existing_config=None):
        super().__init__()
        self.setWindowTitle("Parser Configuration")
        self.config = existing_config if existing_config else {}
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

        # Header and Data Actions Buttons
        self.header_button = QPushButton("Edit Header")
        self.header_button.clicked.connect(self.edit_header)
        self.data_actions_button = QPushButton("Edit Data Actions")
        self.data_actions_button.clicked.connect(self.edit_data_actions)
        layout.addWidget(self.header_button)
        layout.addWidget(self.data_actions_button)

        # Save and Cancel Buttons
        buttons_layout = QHBoxLayout()
        save_button = QPushButton("Save")
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
        if 'header' in self.config:
            header = self.config['header']
        else:
            header = []
        text, ok = QInputDialog.getMultiLineText(self, "Edit Header", "Enter header columns (one per line):", "\n".join(header))
        if ok:
            header = [line.strip() for line in text.split('\n') if line.strip()]
            self.config['header'] = header

    def edit_data_actions(self):
        data_actions = self.config.get('data', [])
        dialog = DataActionsDialog(data_actions)
        if dialog.exec_():
            self.config['data'] = dialog.get_actions()

    def save_configuration(self):
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

        if parser_type == "single":
            output_dir = self.single_output_field.line_edit.text()
            output_csv = os.path.join(output_dir, f"{machine_name}_parsed.csv")
            self.config['single_csv'] = {
                'input': self.single_input_field.line_edit.text(),
                'output': output_csv,
                'delimiter': self.delimiter_field.delimiter_combo.currentText(),
                'last_position': 0,
            }
        elif parser_type == "multi":
            output_dir = self.multi_output_field.line_edit.text()
            output_csv = os.path.join(output_dir, f"{machine_name}_parsed.csv")
            self.config['multi'] = {
                'input': self.multi_input_field.line_edit.text(),
                'move': self.multi_move_field.line_edit.text(),
                'output': output_dir,
                'output_file': output_csv,
                'delimiter': self.delimiter_field.delimiter_combo.currentText(),
                'has_header': self.header_checkbox_field.header_checkbox.isChecked(),
            }
        elif parser_type == "COM":
            output_dir = self.com_output_field.line_edit.text()
            output_file = os.path.join(output_dir, f"{machine_name}_parsed.csv")
            self.config['COM'] = {
                'port': self.port_input.text().strip(),
                'output': output_file,
            }

        # Save config to file
        config_file = os.path.join(config_folder, 'parser_config.json')
        with open(config_file, 'w') as f:
            json.dump(self.config, f, indent=4)

        self.accept()

    def populate_fields(self):
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

class DataActionsDialog(QDialog):
    def __init__(self, actions):
        super().__init__()
        self.setWindowTitle("Edit Data Actions")
        self.actions = actions
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        self.actions_list = QListWidget()
        for action in self.actions:
            action_text = self.action_to_text(action)
            item = QListWidgetItem(action_text)
            self.actions_list.addItem(item)
        layout.addWidget(self.actions_list)
        add_button = QPushButton("Add Action")
        add_button.clicked.connect(self.add_action)
        edit_button = QPushButton("Edit Selected Action")
        edit_button.clicked.connect(self.edit_action)
        remove_button = QPushButton("Remove Selected Action")
        remove_button.clicked.connect(self.remove_action)
        buttons_layout = QHBoxLayout()
        buttons_layout.addWidget(add_button)
        buttons_layout.addWidget(edit_button)
        buttons_layout.addWidget(remove_button)
        layout.addLayout(buttons_layout)
        save_button = QPushButton("Save and Close")
        save_button.clicked.connect(self.accept)
        layout.addWidget(save_button)
        self.setLayout(layout)

    def action_to_text(self, action):
        action_type = action.get('action', '')
        if action_type == 'reorder':
            order = action.get('order', [])
            return f"Reorder columns to {order}"
        elif action_type == 'remove':
            values = action.get('value', [])
            return f"Remove values {values}"
        elif action_type == 'find_replace':
            find_value = action.get('find')
            replace_value = action.get('replace')
            return f"Find '{find_value}' and replace with '{replace_value}'"
        elif action_type == 'force_to_cell':
            force_dict = action.get('force_to_cell', {})
            return f"Force values to cells: {force_dict}"
        elif action_type == 'math_operations':
            operations = action.get('operations', [])
            return f"Math operations: {operations}"
        else:
            return json.dumps(action)

    def add_action(self):
        dialog = ActionEditorDialog()
        if dialog.exec_():
            action = dialog.get_action()
            self.actions.append(action)
            action_text = self.action_to_text(action)
            self.actions_list.addItem(QListWidgetItem(action_text))

    def edit_action(self):
        current_item = self.actions_list.currentItem()
        if current_item:
            index = self.actions_list.row(current_item)
            action = self.actions[index]
            dialog = ActionEditorDialog(action)
            if dialog.exec_():
                action = dialog.get_action()
                self.actions[index] = action
                action_text = self.action_to_text(action)
                current_item.setText(action_text)

    def remove_action(self):
        current_item = self.actions_list.currentItem()
        if current_item:
            index = self.actions_list.row(current_item)
            self.actions.pop(index)
            self.actions_list.takeItem(index)

    def get_actions(self):
        return self.actions

class ActionEditorDialog(QDialog):
    def __init__(self, action=None):
        super().__init__()
        self.setWindowTitle("Edit Action")
        self.action = action if action else {}
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        action_type_label = QLabel("Action Type:")
        self.action_type_combo = QComboBox()
        self.action_type_combo.addItems(["reorder", "remove", "find_replace", "force_to_cell", "math_operations"])
        self.action_type_combo.currentTextChanged.connect(self.update_action_fields)
        layout.addWidget(action_type_label)
        layout.addWidget(self.action_type_combo)

        self.action_fields_layout = QVBoxLayout()
        layout.addLayout(self.action_fields_layout)

        buttons_layout = QHBoxLayout()
        save_button = QPushButton("Save")
        save_button.clicked.connect(self.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        buttons_layout.addWidget(save_button)
        buttons_layout.addWidget(cancel_button)
        layout.addLayout(buttons_layout)

        self.setLayout(layout)

        # Initialize fields if editing existing action
        if self.action:
            action_type = self.action.get('action', 'reorder')
            self.action_type_combo.setCurrentText(action_type)
            self.update_action_fields(action_type)
            self.populate_fields()
        else:
            self.update_action_fields(self.action_type_combo.currentText())

    def update_action_fields(self, action_type):
        # Clear previous fields
        while self.action_fields_layout.count():
            item = self.action_fields_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if action_type == "reorder":
            instruction_label = QLabel("Enter the new order of columns as a comma-separated list (e.g., 3,1,2):")
            self.order_input = QLineEdit()
            self.action_fields_layout.addWidget(instruction_label)
            self.action_fields_layout.addWidget(self.order_input)
        elif action_type == "remove":
            instruction_label = QLabel("Enter the values to remove, separated by commas:")
            self.values_input = QLineEdit()
            self.action_fields_layout.addWidget(instruction_label)
            self.action_fields_layout.addWidget(self.values_input)
        elif action_type == "find_replace":
            find_label = QLabel("Find:")
            self.find_input = QLineEdit()
            replace_label = QLabel("Replace with:")
            self.replace_input = QLineEdit()
            self.action_fields_layout.addWidget(find_label)
            self.action_fields_layout.addWidget(self.find_input)
            self.action_fields_layout.addWidget(replace_label)
            self.action_fields_layout.addWidget(self.replace_input)
        elif action_type == "force_to_cell":
            instruction_label = QLabel("Specify the value and the index (cell position) to move it to.")
            value_label = QLabel("Value:")
            self.value_input = QLineEdit()
            index_label = QLabel("Index (starting from 1):")
            self.index_input = QSpinBox()
            self.index_input.setMinimum(1)
            self.action_fields_layout.addWidget(instruction_label)
            self.action_fields_layout.addWidget(value_label)
            self.action_fields_layout.addWidget(self.value_input)
            self.action_fields_layout.addWidget(index_label)
            self.action_fields_layout.addWidget(self.index_input)
        elif action_type == "math_operations":
            instruction_label = QLabel("Define the math operation to perform.")
            row_label = QLabel("Row ('all' or specific row number):")
            self.row_input = QLineEdit()
            self.row_input.setText('all')
            column_label = QLabel("Target Column Name:")
            self.column_input = QLineEdit()
            operation_label = QLabel("Operation (e.g., (Column1 + Column2) / 2):")
            self.operation_input = QLineEdit()
            self.action_fields_layout.addWidget(instruction_label)
            self.action_fields_layout.addWidget(row_label)
            self.action_fields_layout.addWidget(self.row_input)
            self.action_fields_layout.addWidget(column_label)
            self.action_fields_layout.addWidget(self.column_input)
            self.action_fields_layout.addWidget(operation_label)
            self.action_fields_layout.addWidget(self.operation_input)

    def populate_fields(self):
        action_type = self.action.get('action', '')
        if action_type == "reorder":
            order = self.action.get('order', [])
            self.order_input.setText(','.join(map(str, order)))
        elif action_type == "remove":
            values = self.action.get('value', [])
            self.values_input.setText(','.join(values))
        elif action_type == "find_replace":
            find_value = self.action.get('find', '')
            replace_value = self.action.get('replace', '')
            self.find_input.setText(find_value)
            self.replace_input.setText(replace_value)
        elif action_type == "force_to_cell":
            force_dict = self.action.get('force_to_cell', {})
            if force_dict:
                value, index = next(iter(force_dict.items()))
                self.value_input.setText(value)
                self.index_input.setValue(int(index) + 1)  # Adjust for zero-based index
        elif action_type == "math_operations":
            operations = self.action.get('operations', [])
            if operations:
                operation = operations[0]
                row_spec = operation.get('row', '')
                column = operation.get('column', '')
                op_expr = operation.get('operation', '')
                self.row_input.setText(str(row_spec))
                self.column_input.setText(column)
                self.operation_input.setText(op_expr)

    def get_action(self):
        action_type = self.action_type_combo.currentText()
        if action_type == "reorder":
            order_text = self.order_input.text()
            order = [int(num.strip()) for num in order_text.split(',') if num.strip().isdigit()]
            return {'action': 'reorder', 'order': order}
        elif action_type == "remove":
            values_text = self.values_input.text()
            values = [val.strip() for val in values_text.split(',') if val.strip()]
            return {'action': 'remove', 'value': values}
        elif action_type == "find_replace":
            find_value = self.find_input.text()
            replace_value = self.replace_input.text()
            return {'action': 'find_replace', 'find': find_value, 'replace': replace_value}
        elif action_type == "force_to_cell":
            value = self.value_input.text()
            index = self.index_input.value() - 1  # Adjust for zero-based index
            return {'action': 'force_to_cell', 'force_to_cell': {value: index}}
        elif action_type == "math_operations":
            row_spec = self.row_input.text()
            column = self.column_input.text()
            operation_expr = self.operation_input.text()
            operation = {'row': row_spec, 'column': column, 'operation': operation_expr}
            return {'action': 'math_operations', 'operations': [operation]}
        else:
            return {}

class ParserTab(QWidget):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.init_ui()
        self.timer = QTimer()
        self.timer.timeout.connect(self.load_and_parse_data)
        self.timer.start(60000)  # Run every 60 seconds

    def init_ui(self):
        layout = QVBoxLayout()
        self.status_label = QLabel("Idle")
        self.table_view = QTableView()
        layout.addWidget(self.status_label)
        layout.addWidget(self.table_view)
        self.setLayout(layout)

    def load_and_parse_data(self):
        try:
            self.status_label.setText("Processing...")
            config_type = self.config['parser_type']
            config_folder = self.config['config_folder']
            if config_type == 'single':
                input_csv = self.config['single_csv']['input']
                output_csv = self.config['single_csv']['output']
                df = process_single_csv(input_csv, output_csv, config_folder)
                # Read the entire parsed_data.csv file
                df_full = pd.read_csv(output_csv, on_bad_lines='skip')
                model = PandasModel(df_full)
                self.table_view.setModel(model)
                self.status_label.setText(f"Parsed data saved to {output_csv}")
            elif config_type == 'multi':
                input_folder = self.config['multi']['input']
                move_folder = self.config['multi']['move']
                output_folder = self.config['multi']['output']
                df_list = process_multi_csv(input_folder, move_folder, output_folder, config_folder)
                output_csv = self.config['multi'].get('output_file')
                if os.path.exists(output_csv):
                    df_full = pd.read_csv(output_csv, on_bad_lines='skip')
                    model = PandasModel(df_full)
                    self.table_view.setModel(model)
                    self.status_label.setText(f"Parsed data saved to {output_csv}")
                else:
                    self.status_label.setText("No new files to process.")
            elif config_type == 'COM':
                # For COM parser, implement as needed
                pass
            else:
                self.status_label.setText("Invalid parser type specified.")
        except Exception as e:
            print(f"Error in parser '{self.config['machine_name']}': {e}")
            self.status_label.setText(f"Error: {e}")

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CSV Parser")
        self.init_ui()

    def init_ui(self):
        self.tab_widget = QTabWidget()
        self.setCentralWidget(self.tab_widget)
        # Load existing configurations
        self.load_existing_configs()
        # Add menu actions
        self.init_menu()

    def load_existing_configs(self):
        if not os.path.exists(CONFIG_DIR):
            os.makedirs(CONFIG_DIR)
        for folder_name in os.listdir(CONFIG_DIR):
            config_folder = os.path.join(CONFIG_DIR, folder_name)
            if os.path.isdir(config_folder):
                config_file = os.path.join(config_folder, 'parser_config.json')
                if os.path.exists(config_file):
                    with open(config_file, 'r') as f:
                        config = json.load(f)
                    tab = ParserTab(config)
                    self.tab_widget.addTab(tab, config.get('machine_name', 'Unnamed'))

    def init_menu(self):
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("File")
        add_parser_action = file_menu.addAction("Add Parser")
        add_parser_action.triggered.connect(self.add_parser)
        delete_parser_action = file_menu.addAction("Delete Parser")
        delete_parser_action.triggered.connect(self.delete_parser)

    def add_parser(self):
        dialog = ConfigDialog()
        if dialog.exec_():
            config = dialog.config
            # Save the config
            config_folder = config['config_folder']
            if not os.path.exists(config_folder):
                os.makedirs(config_folder)
            config_file = os.path.join(config_folder, 'parser_config.json')
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=4)
            # Add new tab
            tab = ParserTab(config)
            self.tab_widget.addTab(tab, config.get('machine_name', 'Unnamed'))

    def delete_parser(self):
        current_index = self.tab_widget.currentIndex()
        if current_index == -1:
            QMessageBox.warning(self, "Delete Parser", "No parser is currently selected.")
            return
        reply = QMessageBox.question(self, "Delete Parser", "Are you sure you want to delete the selected parser?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            tab = self.tab_widget.widget(current_index)
            config = tab.config
            machine_name = config.get('machine_name', '')
            config_folder = config.get('config_folder', '')
            # Remove tab
            self.tab_widget.removeTab(current_index)
            # Delete configuration folder
            if os.path.exists(config_folder):
                import shutil
                shutil.rmtree(config_folder)
                print(f"Deleted configuration folder: {config_folder}")
            QMessageBox.information(self, "Delete Parser", f"Parser '{machine_name}' has been deleted.")

def main():
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

    # Optionally adjust font size
    font = app.font()
    font.setPointSize(10)  # Adjust the font size as needed
    app.setFont(font)

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
