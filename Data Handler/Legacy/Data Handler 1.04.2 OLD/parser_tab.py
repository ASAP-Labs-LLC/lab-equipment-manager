#parser_tab.py
import os
import threading  # Ensure threading is imported
import pandas as pd
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QTableView, QMessageBox
from PyQt5.QtCore import QTimer, pyqtSignal
from pandas_model import PandasModel
from data_handler import process_single_csv, process_multi_csv, process_com_port
import logging

# Configure logging
LOG_LOCK = threading.Lock()  # Use threading.Lock()

def log_message(level, message):
    with LOG_LOCK:
        logger = logging.getLogger(__name__)
        if level == 'info':
            logger.info(message)
        elif level == 'warning':
            logger.warning(message)
        elif level == 'error':
            logger.error(message)
        else:
            logger.debug(message)

class ParserTab(QWidget):
    """A tab representing a single parser."""
    update_status_signal = pyqtSignal(str)
    critical_error_signal = pyqtSignal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.init_ui()
        self.update_status_signal.connect(self.update_status)
        self.critical_error_signal.connect(self.handle_critical_error)
        self.com_thread = None
        self.stop_event = threading.Event()

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
        """Initialize the user interface for the parser tab."""
        layout = QVBoxLayout()
        self.status_label = QLabel("Initializing...")
        self.table_view = QTableView()
        layout.addWidget(self.status_label)
        layout.addWidget(self.table_view)
        self.setLayout(layout)

    def start_com_processing(self):
        """Start the thread to process COM port data."""
        self.com_thread = threading.Thread(
            target=process_com_port,
            args=(self.config['COM'], self.config['config_folder'], self.update_status_signal),
            daemon=True
        )
        self.com_thread.start()
        log_message('info', f"Started COM port processing thread for '{self.config['machine_name']}'")

    def load_and_parse_data(self):
        """Load and parse data based on the parser configuration."""
        try:
            parser_type = self.config['parser_type']
            if parser_type == 'single':
                df = process_single_csv(
                    self.config['single_csv']['input'],
                    self.config['single_csv']['output'],
                    self.config['config_folder']
                )
                self.display_data(df)
                self.update_status_signal.emit("Data updated from single CSV.")
            elif parser_type == 'multi':
                dataframes = process_multi_csv(
                    self.config['multi']['input'],
                    self.config['multi']['move'],
                    self.config['multi']['output'],
                    self.config['config_folder']
                )
                # Display the last processed dataframe
                if dataframes:
                    _, df = dataframes[-1]
                    self.display_data(df)
                    self.update_status_signal.emit("Data updated from multiple CSVs.")
                else:
                    self.update_status_signal.emit("No new CSV files to process.")
            elif parser_type == 'COM':
                # COM port data is processed in the background
                self.update_status_signal.emit("Listening to COM port...")
            else:
                self.update_status_signal.emit(f"Unknown parser type: {parser_type}")
                log_message('warning', f"Unknown parser type: {parser_type}")
        except Exception as e:
            error_message = f"Error in data processing: {e}"
            self.update_status_signal.emit(error_message)
            log_message('error', error_message)
            self.critical_error_signal.emit(error_message)

    def display_data(self, df):
        """Display the data in the table view."""
        if df.empty:
            self.update_status_signal.emit("No data to display.")
            return
        model = PandasModel(df)
        self.table_view.setModel(model)
        self.table_view.resizeColumnsToContents()

    def update_status(self, message):
        """Update the status label with the provided message."""
        if message == 'ACCESS_DENIED':
            self.handle_access_denied()
        else:
            self.status_label.setText(message)

    def handle_access_denied(self):
        """Handle access denied error for COM port."""
        reply = QMessageBox.question(
            self,
            "Access Denied",
            f"Access denied to the COM port '{self.config['COM']['port']}'.\nDo you want to try reconnecting?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.Yes:
            # Attempt to reconnect
            self.start_com_processing()
        else:
            self.update_status("COM port access denied.")

    def handle_critical_error(self, message):
        """Handle critical errors by showing a message box."""
        QMessageBox.critical(self, "Critical Error", message)
        log_message('error', f"Critical error: {message}")

    def closeEvent(self, event):
        """Handle the tab being closed."""
        self.stop_event.set()
        if self.com_thread and self.com_thread.is_alive():
            self.com_thread.join(timeout=5)
        event.accept()
