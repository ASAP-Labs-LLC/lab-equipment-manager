import os
import threading
import time
import pandas as pd
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QTableView, QMessageBox
from PyQt5.QtCore import QTimer, pyqtSignal
import logging
from pandas_model import PandasModel

LOG_LOCK = threading.Lock()
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

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

        # Signals for status updates and critical errors
        self.update_status_signal.connect(self.update_status)
        self.critical_error_signal.connect(self.handle_critical_error)

        # For COM or CSV background processing
        self.com_thread = None
        self.data_thread = None
        self.stop_event = threading.Event()

        if self.config['parser_type'] == 'COM':
            self.start_com_processing()
        else:
            self.start_data_processing()

        # --- QTimer approach to refresh UI automatically ---
        self.timer = QTimer()
        self.timer.timeout.connect(self.load_and_display_data)
        update_interval = self.config.get('update_interval', 60) * 1000  # ms
        self.timer.start(update_interval)

    def init_ui(self):
        layout = QVBoxLayout()
        self.status_label = QLabel("Initializing...")
        self.table_view = QTableView()
        layout.addWidget(self.status_label)
        layout.addWidget(self.table_view)
        self.setLayout(layout)

    def start_com_processing(self):
        from data_handler import process_com_port
        self.com_thread = threading.Thread(
            target=process_com_port,
            args=(self.config['COM'], self.config['config_folder'], self.update_status_signal, self.stop_event),
            daemon=True
        )
        self.com_thread.start()
        log_message('info', f"Started COM port processing thread for '{self.config['machine_name']}'")

    def start_data_processing(self):
        """
        Start a background thread to repeatedly process single or multi CSV,
        but do *not* block the UI. The QTimer in the GUI thread handles refresh.
        """
        from data_handler import process_single_csv, process_multi_csv

        def data_loop():
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
                        msg = f"Unknown parser type: {self.config['parser_type']}"
                        self.update_status_signal.emit(msg)
                        log_message('warning', msg)

                    # Sleep for update_interval seconds to avoid tight loop
                    interval = self.config.get('update_interval', 60)
                    for _ in range(interval):
                        if self.stop_event.is_set():
                            break
                        time.sleep(1)

                except Exception as e:
                    err = f"Error in data processing: {e}"
                    self.update_status_signal.emit(err)
                    log_message('error', err)
                    self.critical_error_signal.emit(err)
                    break

        self.data_thread = threading.Thread(target=data_loop, daemon=True)
        self.data_thread.start()
        log_message('info', f"Started data processing thread for '{self.config['machine_name']}'")

    def load_and_display_data(self):
        """
        Called periodically by QTimer in the GUI thread
        to load the CSV output into the table.
        """
        try:
            parser_type = self.config['parser_type']
            if parser_type == 'single':
                output_csv = self.config['single_csv']['output']
            elif parser_type == 'multi':
                output_csv = self.config['multi']['output_file']
            elif parser_type == 'COM':
                output_csv = self.config['COM']['output']
            else:
                msg = f"Unknown parser type: {parser_type}"
                self.update_status_signal.emit(msg)
                log_message('warning', msg)
                return

            if os.path.exists(output_csv):
                # If your pandas is 2.x, use encoding_errors='replace' instead of errors='replace'
                df = pd.read_csv(output_csv, engine='python', on_bad_lines='skip')
                self.display_data(df)
                self.update_status_signal.emit(f"Data updated from {os.path.basename(output_csv)}.")
            else:
                msg = f"Output CSV not found: {output_csv}"
                self.update_status_signal.emit(msg)
                log_message('warning', msg)
        except Exception as e:
            error_message = f"Error loading data from CSV: {e}"
            self.update_status_signal.emit(error_message)
            log_message('error', error_message)
            self.critical_error_signal.emit(error_message)

    def display_data(self, df):
        if df.empty:
            self.update_status_signal.emit("No data to display.")
            return
        model = PandasModel(df)
        self.table_view.setModel(model)
        self.table_view.resizeColumnsToContents()

    def update_status(self, message):
        self.status_label.setText(message)

    def stop_all_processing(self):
        """Stop any threads for COM or data parsing."""
        self.stop_event.set()
        if self.com_thread and self.com_thread.is_alive():
            self.com_thread.join(timeout=5)
            self.com_thread = None
        if self.data_thread and self.data_thread.is_alive():
            self.data_thread.join(timeout=5)
            self.data_thread = None

    def handle_critical_error(self, message):
        QMessageBox.critical(self, "Critical Error", message)
        log_message('error', f"Critical error: {message}")

    def closeEvent(self, event):
        """Called when the tab is closed or app is closed."""
        self.stop_all_processing()
        # Also stop the QTimer
        if hasattr(self, 'timer'):
            self.timer.stop()
        event.accept()
