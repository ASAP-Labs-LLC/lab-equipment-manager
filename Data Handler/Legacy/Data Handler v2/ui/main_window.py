import os
import json
import shutil
import logging
from PyQt5.QtWidgets import QMainWindow, QTabWidget, QAction, QMessageBox, QWidget, QApplication
from PyQt5.QtGui import QFont
from PyQt5.QtCore import Qt

from backend.config_manager import ConfigManager
from ui.dialogs import ConfigDialog, SettingsDialog
from ui.parser_tab_widget import ParserTab

LOG = logging.getLogger(__name__)

class MainWindow(QMainWindow):
    def __init__(self, settings, base_dir):
        super().__init__()
        self.settings = settings
        self.base_dir = base_dir
        self.parsers = {}
        self.scale_factor = self.settings.get('scale_factor', 1.5)
        self.current_theme = self.settings.get('current_theme', 'default.css')

        self.setWindowTitle("CSV Parser")

        self.init_ui()
        self.apply_scale()
        self.apply_theme()  # Apply theme on startup
        self.load_parsers()

    def init_ui(self):
        self.tab_widget = QTabWidget()
        self.setCentralWidget(self.tab_widget)

        add_parser_action = QAction("Add Parser", self)
        add_parser_action.triggered.connect(self.add_parser)
        edit_parser_action = QAction("Edit Parser", self)
        edit_parser_action.triggered.connect(self.edit_parser)
        delete_parser_action = QAction("Delete Parser", self)
        delete_parser_action.triggered.connect(self.delete_parser)
        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self.open_settings)

        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        file_menu.addAction(add_parser_action)
        file_menu.addAction(edit_parser_action)
        file_menu.addAction(delete_parser_action)
        file_menu.addSeparator()
        file_menu.addAction(settings_action)

    def apply_scale(self):
        font = QFont()
        font.setPointSize(int(12 * self.scale_factor))
        self.setFont(font)
        for widget in self.findChildren(QWidget):
            widget.setFont(font)

    def apply_theme(self):
        # Attempt to apply the current theme to the entire app
        theme_path = os.path.join(self.base_dir, 'resources', 'themes', self.current_theme)
        LOG.debug(f"Trying to apply theme from: {theme_path}")

        if os.path.exists(theme_path):
            with open(theme_path, 'r', encoding='utf-8') as f:
                style = f.read()
                # Apply to the entire application
                app = QApplication.instance()
                if app:
                    app.setStyleSheet(style)
                    LOG.info(f"Applied theme: {self.current_theme}")
                else:
                    LOG.warning("No QApplication instance found to apply theme to.")
        else:
            LOG.warning(f"Theme file not found: {theme_path}. Reverting to default.")
            app = QApplication.instance()
            if app:
                app.setStyleSheet("")

    def load_parsers(self):
        config_manager = ConfigManager()
        config_dir = os.path.join(os.path.expanduser('~'), 'csv_parser_configs')
        if not os.path.exists(config_dir):
            LOG.info("No parser configs directory found.")
            return

        parser_dirs = [
            os.path.join(config_dir, d) for d in os.listdir(config_dir)
            if os.path.isdir(os.path.join(config_dir, d)) and d not in ('themes',)
        ]

        for parser_dir in parser_dirs:
            config_file = os.path.join(parser_dir, 'parser_config.json')
            if os.path.exists(config_file):
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                machine_name = config.get('machine_name', 'Unnamed')
                self.create_tab_for_parser(config)
                LOG.info(f"Loaded parser: {machine_name}")

    def create_tab_for_parser(self, config):
        tab = ParserTab(config)
        machine_name = config.get('machine_name', 'Unnamed')
        self.tab_widget.addTab(tab, machine_name)
        self.parsers[machine_name] = (tab, config)
        tab.update_status_signal.connect(self.statusBar().showMessage)

    def add_parser(self):
        dialog = ConfigDialog()
        if dialog.exec_():
            config = dialog.get_config()
            machine_name = config.get('machine_name', 'Unnamed')
            config_folder = config.get('config_folder', '')

            if machine_name in self.parsers:
                QMessageBox.warning(self, "Error", f"A parser with the name '{machine_name}' already exists.")
                return

            config_file = os.path.join(config_folder, 'parser_config.json')
            try:
                with open(config_file, 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=4)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to save config file: {e}")
                LOG.error(f"Failed to save config file for '{machine_name}': {e}", exc_info=True)
                return

            self.create_tab_for_parser(config)
            self.statusBar().showMessage(f"Added parser: {machine_name}")
            LOG.info(f"Added parser: {machine_name}")

    def edit_parser(self):
        parser_name, ok = self.select_parser_dialog("Select Parser to Edit")
        if ok and parser_name in self.parsers:
            tab, config = self.parsers[parser_name]
            dialog = ConfigDialog(existing_config=config)
            if dialog.exec_():
                updated_config = dialog.get_config()
                new_machine_name = updated_config.get('machine_name', 'Unnamed')
                new_config_folder = updated_config.get('config_folder', '')

                new_config_file = os.path.join(new_config_folder, 'parser_config.json')
                try:
                    with open(new_config_file, 'w', encoding='utf-8') as f:
                        json.dump(updated_config, f, indent=4)
                    LOG.info(f"Updated parser configuration for '{new_machine_name}'")
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"Failed to save updated config: {e}")
                    LOG.error(f"Failed to save updated config for '{new_machine_name}': {e}", exc_info=True)
                    return

                index = self.tab_widget.indexOf(tab)
                self.tab_widget.removeTab(index)
                new_tab = ParserTab(updated_config)
                self.tab_widget.insertTab(index, new_tab, new_machine_name)
                self.parsers.pop(parser_name)
                self.parsers[new_machine_name] = (new_tab, updated_config)
                new_tab.update_status_signal.connect(self.statusBar().showMessage)
                self.statusBar().showMessage(f"Edited parser: {new_machine_name}")
                LOG.info(f"Edited parser: {new_machine_name}")

    def delete_parser(self):
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
                        LOG.info(f"Deleted parser: {parser_name}")
                    except Exception as e:
                        self.statusBar().showMessage(f"Failed to delete parser folder: {e}")
                        LOG.error(f"Failed to delete parser folder for '{parser_name}': {e}", exc_info=True)
                else:
                    self.statusBar().showMessage(f"Parser folder does not exist: {config_folder}")
                    LOG.warning(f"Parser folder does not exist: {config_folder}")

    def select_parser_dialog(self, title):
        from PyQt5.QtWidgets import QInputDialog
        parsers = list(self.parsers.keys())
        if not parsers:
            QMessageBox.warning(self, title, "No parsers are currently available.")
            return None, False
        item, ok = QInputDialog.getItem(self, title, "Select a parser:", parsers, 0, False)
        return item, ok

    def open_settings(self):
        dialog = SettingsDialog(self, self.settings)
        if dialog.exec_():
            # Reload settings from ConfigManager
            config_manager = ConfigManager()
            self.settings = config_manager.settings
            self.scale_factor = self.settings.get('scale_factor', 1.5)
            self.current_theme = self.settings.get('current_theme', 'default.css')
            self.apply_scale()
            self.apply_theme()  # Re-apply theme after settings change
            self.statusBar().showMessage("Settings applied.")

    def closeEvent(self, event):
        for parser_name, (tab, config) in self.parsers.items():
            tab.stop_processes()
        event.accept()
