import os
import logging
import pandas as pd
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QTableView
from PyQt5.QtCore import QTimer, pyqtSignal
from .pandas_model import PandasModel
from backend.data_handler import process_single_csv, process_multi_csv, process_com_port
from backend.process_manager import ProcessManager
from backend.error_handler import show_critical_error

LOG = logging.getLogger(__name__)

class ParserTab(QWidget):
    update_status_signal = pyqtSignal(str)
    critical_error_signal = pyqtSignal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.init_ui()
        self.update_status_signal.connect(self.update_status)
        self.critical_error_signal.connect(lambda msg: show_critical_error(self, msg))

        self.manager = ProcessManager()
        self.start_data_processing()

        self.timer = QTimer()
        update_interval = self.config.get('update_interval', 10) * 1000
        self.timer.timeout.connect(self.load_and_display_data)
        self.timer.start(update_interval)

    def init_ui(self):
        layout = QVBoxLayout()
        self.status_label = QLabel("Initializing...")
        self.table_view = QTableView()
        layout.addWidget(self.status_label)
        layout.addWidget(self.table_view)
        self.setLayout(layout)

    def start_data_processing(self):
        ptype = self.config['parser_type']
        if ptype == 'single':
            self.manager.start_task(self.run_single)
        elif ptype == 'multi':
            self.manager.start_task(self.run_multi)
        elif ptype == 'COM':
            self.manager.start_task(self.run_com)
        else:
            self.update_status_signal.emit(f"Unknown parser type: {ptype}")

    def run_single(self):
        while not self.manager.stop_event.is_set():
            try:
                process_single_csv(
                    self.config['single_csv']['input'],
                    self.config['single_csv']['output'],
                    self.config['config_folder']
                )
                self.update_status_signal.emit("Processed single CSV.")
                if self.manager.stop_event.wait(self.config.get('update_interval', 10)):
                    break
            except Exception as e:
                self.critical_error_signal.emit(str(e))
                break

    def run_multi(self):
        while not self.manager.stop_event.is_set():
            try:
                process_multi_csv(
                    self.config['multi']['input'],
                    self.config['multi']['move'],
                    self.config['multi']['output'],
                    self.config['config_folder']
                )
                self.update_status_signal.emit("Processed multiple CSVs.")
                if self.manager.stop_event.wait(self.config.get('update_interval', 10)):
                    break
            except Exception as e:
                self.critical_error_signal.emit(str(e))
                break

    def run_com(self):
        # For COM port, might be continuous reading. Ensure stop_event integrated in process_com_port.
        process_com_port(
            self.config['COM'],
            self.config['config_folder'],
            status_callback=self.update_status_signal,
            stop_event=self.manager.stop_event
        )

    def load_and_display_data(self):
        ptype = self.config['parser_type']
        if ptype == 'single':
            output_csv = self.config['single_csv']['output']
        elif ptype == 'multi':
            output_csv = self.config['multi']['output_file']
        elif ptype == 'COM':
            output_csv = self.config['COM']['output']
        else:
            self.update_status_signal.emit(f"Unknown parser type: {ptype}")
            return

        if os.path.exists(output_csv):
            try:
                df = pd.read_csv(output_csv)
                self.display_data(df)
                self.update_status_signal.emit(f"Data updated from {os.path.basename(output_csv)}.")
            except Exception as e:
                self.critical_error_signal.emit(f"Error loading data: {e}")
        else:
            self.update_status_signal.emit(f"No output CSV found: {output_csv}")

    def display_data(self, df):
        if df.empty:
            self.update_status_signal.emit("No data to display.")
            return
        model = PandasModel(df)
        self.table_view.setModel(model)
        self.table_view.resizeColumnsToContents()

    def update_status(self, message):
        self.status_label.setText(message)

    def stop_processes(self):
        self.manager.stop_all()
