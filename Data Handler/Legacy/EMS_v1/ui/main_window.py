# File: csv_parser_app/ui/main_window.py

import os
import json
import shutil
from PyQt5.QtWidgets import (
    QMainWindow, QTabWidget, QMessageBox, QAction, QInputDialog
)
from PyQt5.QtGui import QFont
from PyQt5.QtCore import Qt
from ..dialogs.config_dialogs import ConfigDialog
from ..ui.parser_tab import ParserTab
from ..utils.settings_manager import SettingsManager
from ..utils.logging_utils import log_message
from ..dialogs.settings_dialog import SettingsDialog

CONFIG_DIR = os.path.join(os.path.expanduser('~'), 'csv_parser_configs')
THEMES_DIR = os.path.join(CONFIG_DIR, 'themes')
SETTINGS_FILE = os.path.join(CONFIG_DIR, 'settings.json')

class MainWindow(QMainWindow):
    """The main window of the application."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CSV Parser")
        self.parsers = {}
        self.settings_manager = SettingsManager(SETTINGS_FILE)
        self.scale_factor = self.settings_manager.get_setting('scale_factor', 1.0)
        self.current_theme = self.settings_manager.get_setting('current_theme', 'default.css')
        self.ensure_themes_directory()

        self.tab_widget = QTabWidget()
        self.setCentralWidget(self.tab_widget)

        self.load_existing_configs()
        self.init_menu()
        self.apply_theme()
        self.apply_scale()
        self.statusBar().showMessage("Ready")

    def ensure_themes_directory(self) -> None:
        if not os.path.exists(THEMES_DIR):
            os.makedirs(THEMES_DIR)
        self.create_default_themes()

    def create_default_themes(self) -> None:
        themes = {
            'default.css': self.default_theme_content(),
            'dark.css': self.dark_theme_content(),
            'light.css': self.light_theme_content()
        }
        for filename, content in themes.items():
            theme_path = os.path.join(THEMES_DIR, filename)
            if not os.path.exists(theme_path):
                with open(theme_path, 'w', encoding='utf-8') as f:
                    f.write(content)

    def default_theme_content(self) -> str:
        return """
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

    def dark_theme_content(self) -> str:
        return """
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

    def light_theme_content(self) -> str:
        return """
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

    def load_existing_configs(self) -> None:
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

    def init_menu(self) -> None:
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("File")

        add_parser_action = QAction("Add Parser", self)
        add_parser_action.triggered.connect(self.add_parser)
        file_menu.addAction(add_parser_action)

        edit_parser_action = QAction("Edit Parser", self)
        edit_parser_action.triggered.connect(self.edit_parser)
        file_menu.addAction(edit_parser_action)

        delete_parser_action = QAction("Delete Parser", self)
        delete_parser_action.triggered.connect(self.delete_parser)
        file_menu.addAction(delete_parser_action)

        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self.open_settings)
        file_menu.addAction(settings_action)

    def apply_scale(self) -> None:
        """Apply the scaling factor to the entire application."""
        font = QFont()
        base_size = 10
        font.setPointSizeF(base_size * self.scale_factor)
        self.setFont(font)

    def apply_theme(self) -> None:
        """Apply the selected theme to the application."""
        theme_file = os.path.join(THEMES_DIR, self.current_theme)
        if os.path.exists(theme_file):
            with open(theme_file, 'r', encoding='utf-8') as f:
                style = f.read()
            self.setStyleSheet(style)
            self.statusBar().showMessage(f"Applied theme: {self.current_theme}")
            log_message('info', f"Applied theme: {self.current_theme}")
        else:
            self.setStyleSheet(self.default_theme_content())
            self.statusBar().showMessage("Applied default theme.")
            log_message('warning', "Applied default theme due to missing theme file.")

    def add_parser(self) -> None:
        dialog = ConfigDialog()
        if dialog.exec_():
            config = dialog.get_config()
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

    def edit_parser(self) -> None:
        parser_name, ok = self.select_parser_dialog("Select Parser to Edit")
        if ok and parser_name in self.parsers:
            tab, config = self.parsers[parser_name]
            dialog = ConfigDialog(existing_config=config)
            if dialog.exec_():
                updated_config = dialog.get_config()
                new_machine_name = updated_config.get('machine_name', 'Unnamed')
                new_config_folder = updated_config.get('config_folder')
                new_config_file = os.path.join(new_config_folder, 'parser_config.json')

                try:
                    with open(new_config_file, 'w', encoding='utf-8') as f:
                        json.dump(updated_config, f, indent=4)
                    log_message('info', f"Updated parser configuration for '{new_machine_name}'")
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"Failed to save updated config: {e}")
                    log_message('error', f"Failed to save updated config for '{new_machine_name}': {e}")
                    return

                new_tab = ParserTab(updated_config)
                index = self.tab_widget.indexOf(tab)
                self.tab_widget.removeTab(index)
                self.tab_widget.insertTab(index, new_tab, new_machine_name)
                self.parsers.pop(parser_name)
                self.parsers[new_machine_name] = (new_tab, updated_config)
                new_tab.update_status_signal.connect(self.display_status_message)
                self.statusBar().showMessage(f"Edited parser: {new_machine_name}")
                log_message('info', f"Edited parser: {new_machine_name}")

    def delete_parser(self) -> None:
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

    def select_parser_dialog(self, title: str):
        parsers = list(self.parsers.keys())
        if not parsers:
            QMessageBox.warning(self, title, "No parsers are currently available.")
            return None, False
        item, ok = QInputDialog.getItem(self, title, "Select a parser:", parsers, 0, False)
        return item, ok

    def open_settings(self) -> None:
        dialog = SettingsDialog(self)
        if dialog.exec_():
            self.statusBar().showMessage("Settings applied.")

    def display_status_message(self, message: str) -> None:
        self.statusBar().showMessage(message)
