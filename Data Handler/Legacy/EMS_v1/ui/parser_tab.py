# File: csv_parser_app/ui/parser_tab.py

import threading
import pandas as pd
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QTableView, QMessageBox
from PyQt5.QtCore import QTimer, pyqtSignal
from ..models.pandas_model import PandasModel
from ..parsers.data_handler import (
    process_single_csv, process_multi_csv, process_com_port
)
from ..utils.logging_utils import log_message

class ParserTab(QWidget):
    """
    A tab representing a single parser, which can be of type 'single', 'multi', or 'COM'.
    """
    update_status_signal = pyqtSignal(str)
    critical_error_signal = pyqtSignal(str)

    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self.stop_event = threading.Event()
        self.com_thread = None

        self.init_ui()
        self.update_status_signal.connect(self.update_status)
        self.critical_error_signal.connect(self.handle_critical_error)

        # Decide how to refresh
        if self.config['parser_type'] == 'COM':
            # Start COM background
            self.start_com_processing()
            # Also poll data on intervals (if needed)
            self.timer = QTimer()
            self.timer.timeout.connect(self.load_and_parse_data)
            interval_ms = self.config.get('update_interval', 60) * 1000
            self.timer.start(interval_ms)
        else:
            # Single or multi
            self.load_and_parse_data()
            self.timer = QTimer()
            self.timer.timeout.connect(self.load_and_parse_data)
            interval_ms = self.config.get('update_interval', 60) * 1000
            self.timer.start(interval_ms)

    def init_ui(self) -> None:
        """Initialize the user interface for the parser tab."""
        layout = QVBoxLayout()
        self.status_label = QLabel("Initializing...")
        self.table_view = QTableView()
        layout.addWidget(self.status_label)
        layout.addWidget(self.table_view)
        self.setLayout(layout)

    def start_com_processing(self) -> None:
        """Start the thread to process COM port data."""
        self.com_thread = threading.Thread(
            target=process_com_port,
            args=(self.config['COM'], self.config['config_folder'], self.update_status_signal),
            daemon=True
        )
        self.com_thread.start()
        log_message('info', f"Started COM port processing thread for '{self.config['machine_name']}'")

    def load_and_parse_data(self) -> None:
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
                if dataframes:
                    _, df = dataframes[-1]
                    self.display_data(df)
                    self.update_status_signal.emit("Data updated from multiple CSVs.")
                else:
                    self.update_status_signal.emit("No new CSV files to process.")
            elif parser_type == 'COM':
                self.update_status_signal.emit("Listening to COM port...")
            else:
                self.update_status_signal.emit(f"Unknown parser type: {parser_type}")
                log_message('warning', f"Unknown parser type: {parser_type}")
        except Exception as e:
            error_message = f"Error in data processing: {e}"
            self.update_status_signal.emit(error_message)
            log_message('error', error_message)
            self.critical_error_signal.emit(error_message)

    def display_data(self, df: pd.DataFrame) -> None:
        """Display the data in the table view."""
        if df.empty:
            self.update_status_signal.emit("No data to display.")
            return
        model = PandasModel(df)
        self.table_view.setModel(model)
        self.table_view.resizeColumnsToContents()

    def update_status(self, message: str) -> None:
        """Update the status label with the provided message."""
        if message == 'ACCESS_DENIED':
            self.handle_access_denied()
        else:
            self.status_label.setText(message)

    def handle_access_denied(self) -> None:
        """Handle access denied error for COM port."""
        reply = QMessageBox.question(
            self,
            "Access Denied",
            f"Access denied to the COM port '{self.config['COM']['port']}'.\nDo you want to try reconnecting?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.Yes:
            self.start_com_processing()
        else:
            self.update_status("COM port access denied.")

    def handle_critical_error(self, message: str) -> None:
        """Handle critical errors by showing a message box."""
        QMessageBox.critical(self, "Critical Error", message)
        log_message('error', f"Critical error: {message}")

    def closeEvent(self, event) -> None:
        """Handle the tab being closed."""
        self.stop_event.set()
        if self.com_thread and self.com_thread.is_alive():
            self.com_thread.join(timeout=5)
        super().closeEvent(event)
