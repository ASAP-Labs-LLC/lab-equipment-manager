import sys
import os
import logging.config
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from backend.config_manager import ConfigManager
from ui.main_window import MainWindow

# Determine the absolute path to the directory containing main.py
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Construct the absolute path to the logging configuration file
LOG_CONF = os.path.join(BASE_DIR, 'configs', 'logging.conf')

# Set up logging configuration using the absolute path
logging.config.fileConfig(LOG_CONF, disable_existing_loggers=False)
LOG = logging.getLogger(__name__)

if __name__ == '__main__':
    # Enable High DPI scaling
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)

    # Load settings
    config_manager = ConfigManager()
    settings = config_manager.settings

    # Create and show the main window
    window = MainWindow(settings, base_dir=BASE_DIR)
    window.show()

    # Run the application event loop
    exit_code = app.exec_()
    LOG.info("Application exiting with code: %d", exit_code)
    sys.exit(exit_code)
