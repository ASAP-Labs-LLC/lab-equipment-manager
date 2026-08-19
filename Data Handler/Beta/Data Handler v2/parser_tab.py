import os
import csv
import threading
import time
import pandas as pd
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableView, QPushButton, QMessageBox
from PyQt5.QtCore import QTimer, pyqtSignal, QUrl
from PyQt5.QtGui import QDesktopServices
import logging
from pandas_model import PandasModel
from csv_parser import append_raw_suffix, resolve_output_destination

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

    # Error budget: pause parser after this many consecutive failures
    MAX_CONSECUTIVE_ERRORS = 10
    # Watchdog: consider thread dead if no heartbeat for this many seconds
    HEARTBEAT_TIMEOUT_FACTOR = 3  # multiplied by update_interval

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.parsed_csv = self._resolve_parsed_path()
        self.raw_csv = append_raw_suffix(self.parsed_csv)
        self.data_dir = os.path.dirname(self.parsed_csv)
        self.init_ui()

        # Signals for status updates and critical errors
        self.update_status_signal.connect(self.update_status)
        self.critical_error_signal.connect(self.handle_critical_error)

        # For COM or CSV background processing
        self.com_thread = None
        self.data_thread = None
        self.stop_event = threading.Event()

        # Heartbeat & error tracking
        self._last_heartbeat = time.monotonic()
        self._consecutive_errors = 0
        self._total_errors = 0
        self._paused = False

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
        self.status_label = QLabel("Initializing…")

        # ----- new button bar -----------------------------------------------
        btn_layout = QHBoxLayout()
        self.btn_raw    = QPushButton("Open RAW CSV")
        self.btn_parsed = QPushButton("Open Parsed CSV")
        self.btn_folder = QPushButton("Open Folder")
        self.btn_resume = QPushButton("Resume Parser")
        self.btn_resume.setToolTip("Un-pause parser after repeated errors")
        self.btn_resume.setVisible(False)
        for b in (self.btn_raw, self.btn_parsed, self.btn_folder, self.btn_resume):
            btn_layout.addWidget(b)
        # --------------------------------------------------------------------

        self.table_view = QTableView()
        layout.addWidget(self.status_label)
        layout.addLayout(btn_layout)          # <-- insert above the table
        layout.addWidget(self.table_view)
        self.setLayout(layout)
        # wire up signals
        self.btn_raw.clicked.connect(self.open_raw_csv)
        self.btn_parsed.clicked.connect(self.open_parsed_csv)
        self.btn_folder.clicked.connect(self.open_folder)
        self.btn_resume.clicked.connect(self._on_resume_clicked)

    def start_com_processing(self):
        from serial_manager import SerialWorker
        self.com_thread = SerialWorker(self.config['COM'])
        self.com_thread.status_update.connect(self.update_status)
        self.com_thread.connected.connect(lambda p: self.update_status(f"Connected to {p}"))
        self.com_thread.data_received.connect(self.handle_serial_data)
        self.com_thread.error_occurred.connect(lambda e: log_message('error', e))
        
        self.com_thread.start()
        log_message('info', f"Started SerialWorker for '{self.config['machine_name']}'")

    def handle_serial_data(self, ticket_text):
        """Handle incoming serial data (already framed)."""
        import io
        from data_handler import append_to_master_csv, parse_csv
        try:
            # Ticket is clean text (may be multi-line CSV)
            csv_buf = io.StringIO(ticket_text)
            df = parse_csv(csv_buf, self.config['config_folder'])
            
            if df is not None and not df.empty:
                output_file = self.config['COM']['output']
                machine = self.config.get('machine_name', 'Unnamed')
                append_to_master_csv(output_file, df, machine)
                
                msg = f"Appended {len(df)} rows from {machine}"
                self.update_status(msg)
                log_message('info', msg)
        except Exception as e:
            err = f"COM parse error: {e}"
            log_message('error', err)
            self.update_status(err)

    def start_data_processing(self):
        """
        Start a background thread to repeatedly process single or multi CSV,
        but do *not* block the UI. The QTimer in the GUI thread handles refresh.
        """
        from data_handler import process_single_csv, process_multi_csv

        def data_loop():
            while not self.stop_event.is_set():
                # Check if paused due to error budget exhaustion
                if self._paused:
                    log_message('warning', f"Parser '{self.config['machine_name']}' is paused due to repeated errors.")
                    for _ in range(10):
                        if self.stop_event.is_set():
                            return
                        time.sleep(1)
                    continue

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

                    # Success — reset error counter and update heartbeat
                    self._consecutive_errors = 0
                    self._last_heartbeat = time.monotonic()

                    # Sleep for update_interval seconds to avoid tight loop
                    interval = self.config.get('update_interval', 60)
                    for _ in range(interval):
                        if self.stop_event.is_set():
                            break
                        time.sleep(1)

                except Exception as e:
                    self._consecutive_errors += 1
                    self._total_errors += 1
                    self._last_heartbeat = time.monotonic()
                    err = f"Error in data processing (attempt {self._consecutive_errors}): {e}"
                    self.update_status_signal.emit(err)
                    log_message('error', err)

                    if self._consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                        self._paused = True
                        pause_msg = (
                            f"Parser '{self.config['machine_name']}' paused after "
                            f"{self._consecutive_errors} consecutive errors. "
                            f"Last error: {e}"
                        )
                        log_message('error', pause_msg)
                        self.critical_error_signal.emit(pause_msg)
                    else:
                        # Back off briefly before retrying (escalating delay)
                        backoff = min(self._consecutive_errors * 2, 30)
                        for _ in range(backoff):
                            if self.stop_event.is_set():
                                break
                            time.sleep(1)

        self.data_thread = threading.Thread(target=data_loop, daemon=True)
        self.data_thread.start()
        log_message('info', f"Started data processing thread for '{self.config['machine_name']}'")

    def resume_parser(self):
        """Un-pause a parser that was stopped by the error budget."""
        if not self._paused:
            return
        self._paused = False
        self._consecutive_errors = 0
        machine = self.config.get('machine_name', 'Unknown')
        log_message('info', f"Parser '{machine}' resumed by user.")
        self.update_status_signal.emit(f"Parser '{machine}' resumed.")
        # If the thread died, restart it
        if self.data_thread is None or not self.data_thread.is_alive():
            self.stop_event.clear()
            self.start_data_processing()

    def _check_thread_health(self):
        """Watchdog: detect dead or stalled threads and auto-restart."""
        machine = self.config.get('machine_name', 'Unknown')
        parser_type = self.config.get('parser_type', '')

        if parser_type == 'COM':
            # Check SerialWorker health
            if self.com_thread and hasattr(self.com_thread, 'is_running') and not self.com_thread.is_running:
                log_message('warning', f"SerialWorker for '{machine}' is not running. Restarting...")
                self.update_status_signal.emit(f"Restarting COM connection for '{machine}'...")
                self.start_com_processing()
            return

        # For single/multi parsers, check the data thread
        if self.data_thread is None or not self.data_thread.is_alive():
            if not self.stop_event.is_set() and not self._paused:
                log_message('warning', f"Data thread for '{machine}' died unexpectedly. Restarting...")
                self.update_status_signal.emit(f"Restarting parser thread for '{machine}'...")
                self.stop_event.clear()
                self.start_data_processing()
            return

        # Check heartbeat staleness
        interval = self.config.get('update_interval', 60)
        timeout = interval * self.HEARTBEAT_TIMEOUT_FACTOR
        elapsed = time.monotonic() - self._last_heartbeat
        if elapsed > timeout and not self._paused:
            log_message('warning',
                f"Parser '{machine}' heartbeat stale ({elapsed:.0f}s > {timeout}s). "
                f"Thread alive={self.data_thread.is_alive()}. Attempting restart..."
            )
            self.update_status_signal.emit(f"Parser '{machine}' appears stalled. Restarting...")
            self.stop_event.set()
            self.data_thread.join(timeout=5)
            self.stop_event.clear()
            self._last_heartbeat = time.monotonic()
            self.start_data_processing()

    def load_and_display_data(self):
        """
        Called periodically by QTimer in the GUI thread
        to load the CSV output into the table.
        """
        # Watchdog check every UI refresh cycle
        self._check_thread_health()

        try:
            output_csv = self._resolve_parsed_path()
            if output_csv != self.parsed_csv:
                self.parsed_csv = output_csv
                self.raw_csv = append_raw_suffix(output_csv)
                self.data_dir = os.path.dirname(output_csv)

            self._ensure_output_csv_exists(output_csv)

            expected_columns = self._expected_columns()

            if os.path.getsize(output_csv) == 0:
                df = pd.DataFrame(columns=expected_columns)
            else:
                df = self._read_csv_with_fallback(output_csv, expected_columns)

            self.display_data(df)
            self.update_status_signal.emit(f"Data updated from {os.path.basename(output_csv)}.")
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
        
        # Stop SerialWorker if active
        if self.com_thread and hasattr(self.com_thread, 'stop'):
             self.com_thread.stop()
             self.com_thread = None
        # Fallback for old thread if any (though we replaced it)
        elif self.com_thread and isinstance(self.com_thread, threading.Thread) and self.com_thread.is_alive():
            self.com_thread.join(timeout=2)
            self.com_thread = None
            
        if self.data_thread and self.data_thread.is_alive():
            self.data_thread.join(timeout=5)
            self.data_thread = None

    def handle_critical_error(self, message):
        if self._paused:
            self.btn_resume.setVisible(True)
        QMessageBox.critical(self, "Critical Error", message)
        log_message('error', f"Critical error: {message}")

    def closeEvent(self, event):
        """Called when the tab is closed or app is closed."""
        self.stop_all_processing()
        # Also stop the QTimer
        if hasattr(self, 'timer'):
            self.timer.stop()
        event.accept()
    
    def _open_path(self, path):
        if not os.path.exists(path):
            QMessageBox.warning(self, "File not found",
                                f"'{path}' does not exist yet.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _on_resume_clicked(self):
        self.btn_resume.setVisible(False)
        self.resume_parser()

    def open_raw_csv(self):
        self._open_path(self.raw_csv)

    def open_parsed_csv(self):
        self._open_path(self.parsed_csv)

    def open_folder(self):
        self._open_path(self.data_dir)

    def _resolve_parsed_path(self):
        parser_type = self.config.get('parser_type')
        config_folder = self.config.get('config_folder', '')
        machine_name = self.config.get('machine_name', 'Unnamed')
        return resolve_output_destination(self.config, parser_type, config_folder, machine_name)

    def _ensure_output_csv_exists(self, output_csv):
        os.makedirs(os.path.dirname(output_csv), exist_ok=True)
        if not os.path.exists(output_csv):
            try:
                open(output_csv, 'a', encoding='utf-8').close()
            except OSError as exc:
                msg = f"Unable to initialize parsed CSV '{output_csv}': {exc}"
                self.update_status_signal.emit(msg)
                log_message('error', msg)

    def _expected_columns(self):
        columns = list(self.config.get('header', []))
        data_actions = self.config.get('data', [])
        for action in data_actions:
            if action.get('action') == 'math_operations':
                for operation in action.get('operations', []):
                    if '=' not in operation:
                        continue
                    target_column = operation.split('=', 1)[0].strip()
                    # Skip column index references like C1 which map back to existing headers
                    if target_column.startswith('C') and target_column[1:].isdigit():
                        continue
                    if target_column not in columns:
                        columns.append(target_column)
        columns.extend(['parsed_date', 'parsed_time'])
        return columns

    def _detect_column_count(self, output_csv):
        try:
            with open(output_csv, 'r', encoding='utf-8', newline='') as handle:
                reader = csv.reader(handle)
                counts = [len(row) for row in reader if row]
            return max(counts) if counts else 0
        except OSError as exc:
            log_message('warning', f"Unable to inspect CSV '{output_csv}': {exc}")
            return 0

    def _generate_extra_column_names(self, extras_needed, existing_columns):
        extra_names = []
        known_suffixes = ['parsed_date', 'parsed_time']
        for name in known_suffixes:
            if name not in existing_columns:
                extra_names.append(name)
            if len(extra_names) == extras_needed:
                return extra_names
        counter = 1
        while len(extra_names) < extras_needed:
            candidate = f"extra_{counter}"
            if candidate not in existing_columns:
                extra_names.append(candidate)
            counter += 1
        return extra_names

    def _read_csv_with_fallback(self, output_csv, expected_columns):
        actual_column_count = self._detect_column_count(output_csv)

        try:
            df = pd.read_csv(output_csv, engine='python', on_bad_lines='skip')
            if not actual_column_count or len(df.columns) == actual_column_count:
                return df
        except Exception as exc:
            log_message('warning', f"Primary CSV read failed for '{output_csv}': {exc}")

        column_names = list(expected_columns)
        if actual_column_count and actual_column_count > len(column_names):
            extras = self._generate_extra_column_names(actual_column_count - len(column_names), column_names)
            column_names.extend(extras)
        elif actual_column_count and actual_column_count < len(column_names):
            column_names = column_names[:actual_column_count]

        header_missing = False

        try:
            df = pd.read_csv(
                output_csv,
                engine='python',
                header=None,
                names=column_names,
                on_bad_lines='skip'
            )
        except Exception as exc:
            log_message('error', f"Fallback CSV read failed for '{output_csv}': {exc}")
            return pd.DataFrame(columns=column_names)

        if df.shape[1] < len(column_names):
            df = df.reindex(columns=column_names)

        if not df.empty:
            row0 = df.iloc[0]
            if all(str(row0[col]) == str(col_name) for col, col_name in zip(df.columns, column_names)):
                df = df.iloc[1:].reset_index(drop=True)
                header_missing = True

        if header_missing:
            self._rewrite_csv_with_header(output_csv, df)

        return df

    def _rewrite_csv_with_header(self, output_csv, df):
        try:
            df.to_csv(output_csv, index=False)
        except Exception as exc:
            msg = f"Failed to repair header for '{output_csv}': {exc}"
            self.update_status_signal.emit(msg)
            log_message('warning', msg)
