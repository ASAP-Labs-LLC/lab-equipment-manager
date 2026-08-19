import logging
from PyQt5.QtWidgets import QMessageBox

LOG = logging.getLogger(__name__)

def show_critical_error(parent, message):
    LOG.error(message)
    QMessageBox.critical(parent, "Critical Error", message)

def show_warning(parent, message):
    LOG.warning(message)
    QMessageBox.warning(parent, "Warning", message)

def show_info(parent, message):
    LOG.info(message)
    QMessageBox.information(parent, "Info", message)
