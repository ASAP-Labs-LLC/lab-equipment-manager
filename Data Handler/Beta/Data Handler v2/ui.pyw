import sys
import os
import json
import shutil
import threading
import logging
import time

from PyQt5 import QtCore  # <-- Use QtCore for setAttribute
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QFileDialog, QLineEdit, QMessageBox,
    QTabWidget, QInputDialog, QAction, QSplashScreen, qApp
)
from PyQt5.QtGui import QFont, QPixmap, QGuiApplication, QIcon

from pandas_model import PandasModel
from settings_manager import SettingsManager
from config_dialogs import ConfigDialog
from parser_tab import ParserTab
from settings_dialog import SettingsDialog
from csv_parser import invalidate_config_cache

CONFIG_DIR = os.path.join(os.path.expanduser('~'), 'csv_parser_configs')
THEMES_DIR = os.path.join(CONFIG_DIR, 'themes')
SETTINGS_FILE = os.path.join(CONFIG_DIR, 'settings.json')

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(THEMES_DIR, exist_ok=True)

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
    """Main window of the application."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CSV Parser")
        self.parsers = {}
        self.scale_factor = 1.0
        self.current_theme = 'default.css'
        self.settings_manager = SettingsManager(SETTINGS_FILE)
        self.load_settings()
        self.init_ui()
        self.apply_scale()
        self.apply_theme()
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

    def load_settings(self):
        self.scale_factor = self.settings_manager.get_setting('scale_factor', 1.0)
        self.current_theme = self.settings_manager.get_setting('current_theme', 'default.css')

    def apply_scale(self):
        font = self.font()
        font.setPointSize(int(8 * self.scale_factor))
        self.setFont(font)
        for widget in self.findChildren(QWidget):
            widget.setFont(font)

    def apply_theme(self):
        theme_file = os.path.join(THEMES_DIR, self.current_theme)
        if os.path.exists(theme_file):
            with open(theme_file, 'r', encoding='utf-8') as f:
                style = f.read()
            qApp.setStyleSheet(style)
        else:
            qApp.setStyleSheet("")

    def load_parsers(self):
        parser_dirs = [
            os.path.join(CONFIG_DIR, d) for d in os.listdir(CONFIG_DIR)
            if os.path.isdir(os.path.join(CONFIG_DIR, d)) and d != 'themes'
        ]
        for parser_dir in parser_dirs:
            config_file = os.path.join(parser_dir, 'parser_config.json')
            if os.path.exists(config_file):
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                machine_name = config.get('machine_name', 'Unnamed')
                tab = ParserTab(config)
                self.tab_widget.addTab(tab, machine_name)
                self.parsers[machine_name] = (tab, config)
                tab.update_status_signal.connect(self.display_status_message)
                log_message('info', f"Loaded parser: {machine_name}")

    def add_parser(self):
        dialog = ConfigDialog()
        if dialog.exec_():
            config = dialog.get_config()
            machine_name = config.get('machine_name', 'Unnamed')
            config_folder = config.get('config_folder', '')
            if machine_name in self.parsers:
                QMessageBox.warning(self, "Error", f"A parser with the name '{machine_name}' already exists.")
                return
            else:
                config_file = os.path.join(config_folder, 'parser_config.json')
                try:
                    with open(config_file, 'w', encoding='utf-8') as f:
                        json.dump(config, f, indent=4)
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
        parser_name, ok = self.select_parser_dialog("Select Parser to Edit")
        if ok and parser_name in self.parsers:
            tab, config = self.parsers[parser_name]
            tab.stop_all_processing()

            dialog = ConfigDialog(existing_config=config)
            if dialog.exec_():
                updated_config = dialog.get_config()
                new_machine_name = updated_config.get('machine_name', 'Unnamed')
                new_config_folder = updated_config.get('config_folder', '')

                new_config_file = os.path.join(new_config_folder, 'parser_config.json')
                try:
                    with open(new_config_file, 'w', encoding='utf-8') as f:
                        json.dump(updated_config, f, indent=4)
                    # Force cache refresh so background threads pick up changes immediately
                    invalidate_config_cache(new_config_folder)
                    log_message('info', f"Updated parser configuration for '{new_machine_name}'")
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"Failed to save updated config: {e}")
                    log_message('error', f"Failed to save updated config for '{new_machine_name}': {e}")
                    return

                index = self.tab_widget.indexOf(tab)
                self.tab_widget.removeTab(index)
                self.parsers.pop(parser_name)

                new_tab = ParserTab(updated_config)
                self.tab_widget.insertTab(index, new_tab, new_machine_name)
                self.parsers[new_machine_name] = (new_tab, updated_config)
                new_tab.update_status_signal.connect(self.display_status_message)
                self.statusBar().showMessage(f"Edited parser: {new_machine_name}")
                log_message('info', f"Edited parser: {new_machine_name}")

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
                tab.stop_all_processing()
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
        parsers = list(self.parsers.keys())
        if not parsers:
            QMessageBox.warning(self, title, "No parsers are currently available.")
            return None, False
        item, ok = QInputDialog.getItem(self, title, "Select a parser:", parsers, 0, False)
        return item, ok

    def open_settings(self):
        dialog = SettingsDialog(self)
        if dialog.exec_():
            self.statusBar().showMessage("Settings applied.")

    def display_status_message(self, message):
        self.statusBar().showMessage(message)

def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    # Now reference QtCore.Qt instead of Qt
    QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    # Load .css for global style
    RUN_DIR = os.path.dirname(os.path.abspath(__file__))
    theme_path = os.path.join(RUN_DIR, 'asap_dark.css')
    if os.path.exists(theme_path):
        with open(theme_path, 'r', encoding='utf-8') as f:
            global_style_str = f.read()
        app.setStyleSheet(global_style_str)

    # Optional splash
    splash_image = os.path.join(RUN_DIR, 'splash_image.png')
    if os.path.exists(splash_image):
        splash_pix = QPixmap(splash_image)
        if not splash_pix.isNull():
            screen_geometry = QGuiApplication.primaryScreen().availableGeometry()
            target_w = int(screen_geometry.width() * 0.25)
            target_h = int(screen_geometry.height() * 0.25)
            scaled_pix = splash_pix.scaled(
                target_w, target_h, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
            )
            splash = QSplashScreen(scaled_pix, QtCore.Qt.WindowStaysOnTopHint)
            splash.show()
            app.processEvents()
            time.sleep(3)
        else:
            splash = None
    else:
        splash = None

    window = MainWindow()
    window.show()

    if splash:
        splash.finish(window)

    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
