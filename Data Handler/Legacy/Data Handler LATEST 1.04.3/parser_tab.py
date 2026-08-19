# parser_tab.py
import os
import threading
import pandas as pd
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QTableView, QMessageBox
from PyQt5.QtCore import QTimer, pyqtSignal
from pandas_model import PandasModel
import logging
import time

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
        else:
            self.start_data_processing()

        # Timer to update display
        self.timer = QTimer()
        self.timer.timeout.connect(self.load_and_display_data)
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
        from data_handler import process_com_port

        self.com_thread = threading.Thread(
            target=process_com_port,
            args=(self.config['COM'], self.config['config_folder'], self.update_status_signal, self.stop_event),
            daemon=True
        )
        self.com_thread.start()
        log_message('info', f"Started COM port processing thread for '{self.config['machine_name']}'")

    def start_data_processing(self):
        """Start the thread to process single or multi CSV data."""
        from data_handler import process_single_csv, process_multi_csv

        def data_processing_thread():
            while not self.stop_event.is_set():
                try:
                    if self.config['parser_type'] == 'single':
                        process_single_csv(
                            self.config['single_csv']['input'],
                            self.config['single_csv']['output'],
                            self.config['config_folder']
                        )
                        self.update_status_signal.emit("Processed single CSV.")
                    elif self.config['parser_type'] == 'multi':
                        process_multi_csv(
                            self.config['multi']['input'],
                            self.config['multi']['move'],
                            self.config['multi']['output'],
                            self.config['config_folder']
                        )
                        self.update_status_signal.emit("Processed multiple CSVs.")
                    else:
                        self.update_status_signal.emit(f"Unknown parser type: {self.config['parser_type']}")
                        log_message('warning', f"Unknown parser type: {self.config['parser_type']}")
                    time.sleep(self.config.get('update_interval', 60))
                except Exception as e:
                    error_message = f"Error in data processing: {e}"
                    self.update_status_signal.emit(error_message)
                    log_message('error', error_message)
                    self.critical_error_signal.emit(error_message)
                    break  # Exit the loop on critical error

        self.data_thread = threading.Thread(target=data_processing_thread, daemon=True)
        self.data_thread.start()
        log_message('info', f"Started data processing thread for '{self.config['machine_name']}'")

    def load_and_display_data(self):
        """Load and display data from the parsed CSV file."""
        try:
            parser_type = self.config['parser_type']
            if parser_type == 'single':
                output_csv = self.config['single_csv']['output']
            elif parser_type == 'multi':
                output_csv = self.config['multi']['output_file']
            elif parser_type == 'COM':
                output_csv = self.config['COM']['output']
            else:
                self.update_status_signal.emit(f"Unknown parser type: {parser_type}")
                log_message('warning', f"Unknown parser type: {parser_type}")
                return

            if os.path.exists(output_csv):
                # Read the CSV file into a DataFrame
                df = pd.read_csv(output_csv)
                self.display_data(df)
                self.update_status_signal.emit(f"Data updated from {os.path.basename(output_csv)}.")
            else:
                self.update_status_signal.emit(f"Output CSV not found: {output_csv}")
                log_message('warning', f"Output CSV not found: {output_csv}")
        except Exception as e:
            error_message = f"Error loading data from CSV: {e}"
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
            # Close the COM port and wait 3 seconds before reconnecting
            self.stop_com_processing()
            time.sleep(3)
            self.start_com_processing()
        else:
            self.update_status("COM port access denied.")

    def stop_com_processing(self):
        """Stop COM port processing."""
        if self.com_thread and self.com_thread.is_alive():
            self.stop_event.set()
            self.com_thread.join(timeout=5)
            self.com_thread = None
            self.stop_event.clear()
            log_message('info', f"Stopped COM port processing for '{self.config['machine_name']}'")

    def handle_critical_error(self, message):
        """Handle critical errors by showing a message box."""
        QMessageBox.critical(self, "Critical Error", message)
        log_message('error', f"Critical error: {message}")

    def closeEvent(self, event):
        """Handle the tab being closed."""
        self.stop_event.set()
        if self.com_thread and self.com_thread.is_alive():
            self.com_thread.join(timeout=5)
        if hasattr(self, 'data_thread') and self.data_thread.is_alive():
            self.data_thread.join(timeout=5)
        event.accept()
