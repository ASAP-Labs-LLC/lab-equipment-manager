# ui.py
import sys
import os
import json
import shutil
import threading
import logging
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QFileDialog, QLineEdit, QMessageBox,
    QTabWidget, QInputDialog, QAction
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from pandas_model import PandasModel
from settings_manager import SettingsManager
from config_dialogs import ConfigDialog
from parser_tab import ParserTab
from settings_dialog import SettingsDialog

# Define directories
CONFIG_DIR = os.path.join(os.path.expanduser('~'), 'csv_parser_configs')
THEMES_DIR = os.path.join(CONFIG_DIR, 'themes')
SETTINGS_FILE = os.path.join(CONFIG_DIR, 'settings.json')

# Ensure directories exist
os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(THEMES_DIR, exist_ok=True)

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
        logger = logging.getLogger()
        if level == 'info':
            logger.info(message)
        elif level == 'warning':
            logger.warning(message)
        elif level == 'error':
            logger.error(message)
        else:
            logger.debug(message)

class MainWindow(QMainWindow):
    """The main window of the application."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CSV Parser")
        self.parsers = {}  # Dictionary to keep track of parsers by machine_name
        self.settings_manager = SettingsManager(SETTINGS_FILE)
        self.scale_factor = self.settings_manager.get_setting('scale_factor', 1.0)
        self.current_theme = self.settings_manager.get_setting('current_theme', 'default.css')
        self.ensure_themes_directory()
        self.init_ui()
        self.apply_theme()
        self.apply_scale()

    def init_ui(self):
        self.tab_widget = QTabWidget()
        self.setCentralWidget(self.tab_widget)
        self.load_existing_configs()
        self.init_menu()
        self.statusBar().showMessage("Ready")

    def ensure_themes_directory(self):
        """Ensure that the themes directory exists and contains default themes."""
        if not os.path.exists(THEMES_DIR):
            os.makedirs(THEMES_DIR)
        self.create_default_themes()

    def create_default_themes(self):
        """Create default theme files if they don't exist."""
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

    def default_theme_content(self):
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

    def dark_theme_content(self):
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

    def light_theme_content(self):
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
            self.setStyleSheet(self.default_theme_content())
            self.statusBar().showMessage("Applied default theme.")
            log_message('warning', "Applied default theme due to missing theme file.")

    def add_parser(self):
        """Open the ConfigDialog to add a new parser."""
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

    def edit_parser(self):
        """Edit an existing parser configuration."""
        parser_name, ok = self.select_parser_dialog("Select Parser to Edit")
        if ok and parser_name in self.parsers:
            tab, config = self.parsers[parser_name]
            dialog = ConfigDialog(existing_config=config)
            if dialog.exec_():
                updated_config = dialog.get_config()
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
