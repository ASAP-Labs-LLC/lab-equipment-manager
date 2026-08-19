import copy
import os
import json
import pandas as pd
import numexpr as ne
import csv
import datetime
import unicodedata
import re
import logging
import threading
from collections import defaultdict
from io import StringIO
from PyQt5 import QtWidgets
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit
from PyQt5.QtCore import Qt, QObject, QThread, pyqtSignal, pyqtSlot
import time
import chardet
import shutil

SETTINGS_DIR = os.path.join(os.path.expanduser('~'), 'csv_parser_configs')
SETTINGS_FILE = os.path.join(SETTINGS_DIR, 'settings.json')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CORRECTION_FACTOR_FILE = os.path.join(
    BASE_DIR, 'EQM_Correction Factor', 'correction_factors.json'
)

LOG_LOCK = threading.Lock()
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

_CONFIG_CACHE_LOCK = threading.Lock()
_CONFIG_CACHE: dict[str, dict[str, object]] = {}

_ENCODING_CACHE_LOCK = threading.Lock()
_ENCODING_CACHE: dict[str, dict[str, object]] = {}

_FILE_LOCKS_LOCK = threading.Lock()
_FILE_LOCKS: dict[str, threading.RLock] = {}

_LAB_ID_SEMAPHORE = threading.Semaphore(1)
_LAB_ID_BRIDGE_LOCK = threading.Lock()
_LAB_ID_BRIDGE = None


def acquire_file_lock(path: str) -> threading.RLock:
    """Return a re-entrant lock scoped to the normalized file path."""
    normalized = os.path.normcase(os.path.abspath(path))
    with _FILE_LOCKS_LOCK:
        lock = _FILE_LOCKS.get(normalized)
        if lock is None:
            lock = threading.RLock()
            _FILE_LOCKS[normalized] = lock
    return lock


def load_parser_config(config_folder: str) -> dict:
    """Load parser_config.json with thread-safe caching."""
    config_folder = os.path.abspath(config_folder)
    config_file = os.path.join(config_folder, 'parser_config.json')
    try:
        stat = os.stat(config_file)
    except OSError as exc:
        raise FileNotFoundError(f"Parser config missing: {config_file}") from exc

    cache_key = os.path.normcase(config_file)
    with _CONFIG_CACHE_LOCK:
        cached = _CONFIG_CACHE.get(cache_key)
        if cached and cached.get('mtime') == stat.st_mtime and cached.get('size') == stat.st_size:
            return copy.deepcopy(cached['data'])

    with open(config_file, 'r', encoding='utf-8') as handle:
        data = json.load(handle)

    with _CONFIG_CACHE_LOCK:
        _CONFIG_CACHE[cache_key] = {
            'mtime': stat.st_mtime,
            'size': stat.st_size,
            'data': copy.deepcopy(data),
        }
    return data


def update_parser_config_cache(config_folder: str, config: dict) -> None:
    """Persist the in-memory cache entry after writing to disk."""
    config_folder = os.path.abspath(config_folder)
    config_file = os.path.join(config_folder, 'parser_config.json')
    try:
        stat = os.stat(config_file)
    except OSError:
        return
    cache_key = os.path.normcase(config_file)
    with _CONFIG_CACHE_LOCK:
        _CONFIG_CACHE[cache_key] = {
            'mtime': stat.st_mtime,
            'size': stat.st_size,
            'data': copy.deepcopy(config),
        }


class _LabIDPromptBridge(QObject):
    request = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.request.connect(self._handle_request)

    @pyqtSlot(object)
    def _handle_request(self, payload):
        container = payload.get('result')
        event = payload.get('event')
        container['value'] = _prompt_lab_id_dialog()
        if event:
            event.set()


def _ensure_lab_id_bridge():
    global _LAB_ID_BRIDGE
    app = QtWidgets.QApplication.instance()
    if app is None:
        return None
    with _LAB_ID_BRIDGE_LOCK:
        if _LAB_ID_BRIDGE is None:
            bridge = _LabIDPromptBridge()
            bridge.moveToThread(app.thread())
            _LAB_ID_BRIDGE = bridge
    return _LAB_ID_BRIDGE


def _prompt_lab_id_dialog():
    app = QtWidgets.QApplication.instance()
    if app is None:
        log_message('warning', "Lab ID prompt requested but no QApplication is running.")
        return None
    dialog = LabIDPromptDialog()
    if dialog.exec_() == 0:
        return None
    return dialog.result_value


def request_lab_id():
    """Prompt for Lab ID from any thread, serializing concurrent prompts."""
    _LAB_ID_SEMAPHORE.acquire()
    try:
        app = QtWidgets.QApplication.instance()
        if app is None:
            return None

        if QThread.currentThread() is app.thread():
            return _prompt_lab_id_dialog()

        bridge = _ensure_lab_id_bridge()
        if bridge is None:
            return _prompt_lab_id_dialog()

        result_container = {}
        event = threading.Event()
        bridge.request.emit({'result': result_container, 'event': event})
        event.wait()
        return result_container.get('value')
    finally:
        _LAB_ID_SEMAPHORE.release()


def log_message(level, message):
    with LOG_LOCK:
        safe_message = message.encode(errors='replace').decode(errors='replace')
        if level == 'info':
            logger.info(safe_message)
        elif level == 'warning':
            logger.warning(safe_message)
        elif level == 'error':
            logger.error(safe_message)
        else:
            logger.debug(safe_message)

class LabIDPromptDialog(QDialog):
    """
    A simple blocking dialog that asks for a numeric Lab ID
    and returns it when user presses Enter (like a scanner input).
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Enter Lab ID")
        self.result_value = None
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        self.label = QLabel("Please enter a numeric Lab ID (then press Enter):")
        layout.addWidget(self.label)

        self.input_line = QLineEdit()
        self.input_line.returnPressed.connect(self.accept_value)
        layout.addWidget(self.input_line)

        self.setLayout(layout)

    def accept_value(self):
        text_val = self.input_line.text().strip()
        # Attempt to ensure numeric:
        if not re.match(r'^\d{1,10}$', text_val):
            # e.g. requiring up to 10 digits, tweak as needed
            self.label.setText("Please enter NUMBERS only (1-10 digits), then press Enter:")
            return
        self.result_value = text_val
        self.accept()

def append_to_raw_csv(raw_file, new_text):
    """
    Append new_text to raw_file, rotating daily if needed.

    Yesterday's *_RAW.csv is moved to /Past Data Raw/<file>_YYYY-MM-DD.csv

    This keeps raw archives separate from the cleaned 'Past Data' folder.
    """
    from datetime import datetime
    import shutil

    lock = acquire_file_lock(raw_file)
    with lock:
        raw_dir = os.path.dirname(raw_file)
        if raw_dir:
            os.makedirs(raw_dir, exist_ok=True)

        # --- rotate yesterday's file ------------------------------------------------
        if os.path.exists(raw_file):
            file_mod_time = datetime.fromtimestamp(os.path.getmtime(raw_file))
            if file_mod_time.strftime('%Y-%m-%d') != datetime.now().strftime('%Y-%m-%d'):
                past_raw_folder = os.path.join(raw_dir, 'Past Data Raw')
                os.makedirs(past_raw_folder, exist_ok=True)

                base, ext = os.path.splitext(os.path.basename(raw_file))
                rotated_name = f"{base}_{file_mod_time.strftime('%Y-%m-%d')}{ext}"
                rotated_path = os.path.join(past_raw_folder, rotated_name)

                shutil.move(raw_file, rotated_path)
                log_message('info', f"Rotated raw CSV from '{raw_file}' -> '{rotated_path}'.")

        # --- append today's text ----------------------------------------------------
        with open(raw_file, 'a', encoding='utf-8', newline='') as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell() > 0:
                fh.write('\n')
            fh.write(new_text)


def parse_csv(file_like_object, config_folder, delimiter=','):
    """
    Parse CSV data from a file-like object using the provided config.
    1) Append raw data to *_RAW.csv (rotate daily).
    2) Expand single-col rows if we see semicolons.
    3) Possibly prompt Lab ID, only override the configured column, preserving the rest.
    4) Reorder, remove, etc. Then return a DataFrame.
    """
    try:
        config = load_parser_config(config_folder)
        log_message('debug', f"Loaded parser configuration for '{config_folder}'.")

        # 1) Read raw data
        raw_csv_data = file_like_object.read()
        if not isinstance(raw_csv_data, str):
            raw_csv_data = '' if raw_csv_data is None else str(raw_csv_data)

        # 2) Determine the raw file name
        parser_type = config.get('parser_type')
        machine_name = config.get('machine_name', 'Unnamed')
        output_destination = resolve_output_destination(
            config, parser_type, config_folder, machine_name
        )
        raw_file = append_raw_suffix(output_destination)

        # 3) Append to raw csv
        append_to_raw_csv(raw_file, raw_csv_data)

        # 4) Convert raw_csv_data to a CSV list
        def _read_rows(buffer_text):
            parse_buffer = StringIO(buffer_text)
            return list(csv.reader(parse_buffer, delimiter=delimiter))

        try:
            raw_rows = _read_rows(raw_csv_data)
        except csv.Error as exc:
            normalized_text = _normalize_line_endings(raw_csv_data)
            if normalized_text == raw_csv_data:
                log_message('error', f"CSV parsing failed without recoverable newline fix: {exc}")
                raise
            log_message(
                'warning',
                f"Detected malformed line endings in CSV data ({exc}); normalizing and retrying."
            )
            raw_csv_data = normalized_text
            try:
                raw_rows = _read_rows(raw_csv_data)
            except csv.Error as second_exc:
                log_message('error', f"CSV parsing still failing after normalization: {second_exc}")
                raise

        # If each row is a single cell that contains semicolons, let's expand it:
        data = []
        for row in raw_rows:
            if len(row) == 1 and ';' in row[0]:
                # Expand by semicolon
                expanded = row[0].split(';')
                # Also strip each cell
                expanded = [cell.strip() for cell in expanded]
                data.append(expanded)
            else:
                data.append(row)

        log_message('debug', f"Data after reading from CSV (and expanding single-col semicolon):\n{data}")

        # 5) Clean data
        data = clean_data(data)
        log_message('debug', f"Data after cleaning:\n{data}")

        # 6) Possibly prompt Lab ID
        lab_id_prompting = config.get('lab_id_prompting', False)
        lab_id_col_1based = config.get('lab_id_column', 1)
        lab_id_column = lab_id_col_1based - 1  # zero-based

        if lab_id_prompting:
            lab_id_val = request_lab_id()
            if lab_id_val is None:
                log_message('info', "Lab ID prompt canceled or unavailable; returning None.")
                return None

            # Put that ID into the target_column of each row, preserving other columns
            for row_idx, row in enumerate(data):
                if lab_id_column >= len(row):
                    row.extend([''] * (lab_id_column - len(row) + 1))
                # Overwrite just that cell
                row[lab_id_column] = lab_id_val

            log_message('debug', f"Data after applying Lab ID ({lab_id_val}):\n{data}")

        # 7) Apply data actions (force_to_cell, remove, reorder)
        data, protected_cols = apply_force_to_cell(data, config)
        log_message('debug', f"Data after force_to_cell:\n{data}")

        data = apply_remove_action(data, config)
        log_message('debug', f"Data after remove action:\n{data}")

        data = apply_reorder_to_data(data, config, protected_cols)
        log_message('debug', f"Data after reorder:\n{data}")

        # 8) Assign headers
        header_columns = config.get('header', [])
        data = assign_headers(data, header_columns)
        log_message('debug', f"Data after assigning headers:\n{data}")

        # 9) Convert to DataFrame, add date/time
        df = pd.DataFrame(data, columns=header_columns)
        if config.get('apply_correction_factors', False):
            df = apply_correction_factors(df, output_destination, config)
        now = datetime.datetime.now()
        df["parsed_date"] = now.strftime("%Y-%m-%d")
        df["parsed_time"] = now.strftime("%H:%M:%S")
        log_message('info', f"DataFrame columns before math operations: {df.columns.tolist()}")

        df = apply_math_operations(df, config)
        log_message('debug', f"Data after math operations:\n{df}")

        # 10) Log final DataFrame to parsed_data.log
        parsed_data_log = os.path.join(config_folder, 'parsed_data.log')
        with safe_open(parsed_data_log, 'a', encoding='utf-8', errors='replace') as log_file:
            entry = f"{datetime.datetime.now()} - Parsed Data:\n{df}\n"
            log_file.write(entry)

        log_message('info', "Parsed CSV data successfully.")
        return df

    except Exception as e:
        err = f"Issue parsing CSV data: {e}"
        log_message('error', err)
        raise

def append_raw_suffix(output_path):
    base, ext = os.path.splitext(output_path)
    return base + "_RAW" + ext


def _normalize_line_endings(text: str) -> str:
    """
    Replace lone carriage returns with newlines to avoid csv.Error about
    new line characters in unquoted fields.
    """
    if '\r' not in text:
        return text
    return text.replace('\r\n', '\n').replace('\r', '\n')


def resolve_output_destination(config, parser_type, config_folder, machine_name):
    """
    Determine the final parsed CSV destination for the current parser.
    Falls back to the config folder if the parser type is unknown or misconfigured.
    """
    try:
        if parser_type == 'single':
            return config['single_csv']['output']
        if parser_type == 'multi':
            return config['multi']['output_file']
        if parser_type == 'COM':
            return config['COM']['output']
    except KeyError as exc:
        log_message('warning', f"Missing output path in parser config: {exc}")

    fallback_name = f"{machine_name}_parsed.csv"
    return os.path.join(config_folder, fallback_name)


def apply_correction_factors(df, output_destination, config):
    """
    Apply correction factors to the DataFrame when a matching file destination
    is configured in the correction factor JSON file.
    """
    normalized_destination = _normalize_path(output_destination)
    if not normalized_destination:
        return df

    correction_file = _get_correction_file_path()
    if not correction_file or not os.path.exists(correction_file):
        log_message('debug', f"No correction factor file found at '{correction_file}'. Skipping.")
        return df

    try:
        with open(correction_file, 'r', encoding='utf-8') as handle:
            correction_data = json.load(handle)
    except Exception as exc:
        log_message('error', f"Failed to load correction factors from '{correction_file}': {exc}")
        return df

    if not isinstance(correction_data, dict):
        log_message('warning', f"Correction factor file '{correction_file}' has unexpected format.")
        return df

    corrections_to_apply = []
    for machine_key, tests in correction_data.items():
        if not isinstance(tests, dict):
            continue
        for test_key, entry in tests.items():
            if not isinstance(entry, dict):
                continue
            destination = _normalize_path(entry.get('file_destination'))
            if not destination or destination != normalized_destination:
                continue
            value_column = entry.get('value_column')
            correction_value = entry.get('correction_value')
            if value_column is None or correction_value is None:
                continue
            try:
                correction_value = float(correction_value)
            except (TypeError, ValueError):
                log_message(
                    'warning',
                    f"Invalid correction value '{correction_value}' "
                    f"for machine '{machine_key}' test '{test_key}'."
                )
                continue

            corrections_to_apply.append(
                (value_column, correction_value, machine_key, test_key)
            )

    if not corrections_to_apply:
        return df

    for value_column, correction_value, machine_key, test_key in corrections_to_apply:
        if value_column not in df.columns:
            log_message(
                'warning',
                f"Configured correction column '{value_column}' not present in parsed data "
                f"for machine '{config.get('machine_name', 'Unknown')}'."
            )
            continue
        rows_updated = _apply_correction_to_column(df, value_column, correction_value)
        if rows_updated:
            log_message(
                'info',
                f"Applied correction factor {correction_value} to column '{value_column}' "
                f"({machine_key} / {test_key}) affecting {rows_updated} row(s)."
            )
        else:
            log_message(
                'debug',
                f"Correction factor for column '{value_column}' "
                f"({machine_key} / {test_key}) matched but no numeric rows were updated."
            )

    return df


def _get_correction_file_path():
    """
    Resolve the correction factor file path, honoring settings override and defaulting
    to the bundled location when unset.
    """
    configured_path = ''
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as settings_handle:
                settings_data = json.load(settings_handle)
            configured_path = settings_data.get('correction_factors_path', '')
        except Exception as exc:
            log_message('error', f"Unable to read settings for correction factors: {exc}")
            configured_path = ''

    if not isinstance(configured_path, str):
        configured_path = str(configured_path) if configured_path else ''
    configured_path = configured_path.strip()

    candidate = configured_path or DEFAULT_CORRECTION_FACTOR_FILE
    candidate = os.path.expandvars(os.path.expanduser(candidate))
    if not os.path.isabs(candidate):
        candidate = os.path.abspath(os.path.join(os.path.dirname(SETTINGS_FILE), candidate))

    return candidate


def _normalize_path(path_value):
    if not path_value:
        return None
    expanded = os.path.expandvars(os.path.expanduser(str(path_value).strip()))
    if not os.path.isabs(expanded):
        expanded = os.path.abspath(expanded)
    return os.path.normcase(os.path.normpath(expanded))


def _apply_correction_to_column(df, column_name, correction_value):
    numeric_series = pd.to_numeric(df[column_name], errors='coerce')
    mask = numeric_series.notna()
    if not mask.any():
        return 0

    corrected_numeric = numeric_series[mask] + correction_value
    original_values = df.loc[mask, column_name]
    corrected_formatted = corrected_numeric.combine(
        original_values,
        lambda new_val, original: _format_corrected_value(original, new_val),
    )

    df.loc[mask, column_name] = corrected_formatted
    return int(mask.sum())


def _format_corrected_value(original_cell, corrected_value):
    if isinstance(original_cell, str):
        stripped = original_cell.strip()
        match = re.match(r'^-?\d+(?:\.(\d+))?$', stripped)
        if match:
            decimals = len(match.group(1)) if match.group(1) else 0
            format_str = f"{{:.{decimals}f}}"
            return format_str.format(corrected_value)
    return corrected_value


def detect_encoding(file_path, default='utf-8'):
    cache_key = os.path.normcase(os.path.abspath(file_path))
    try:
        stat = os.stat(file_path)
    except OSError:
        return default, 0.0

    with _ENCODING_CACHE_LOCK:
        cached = _ENCODING_CACHE.get(cache_key)
        if cached and cached.get('mtime') == stat.st_mtime and cached.get('size') == stat.st_size:
            return cached['encoding'], cached['confidence']

    try:
        with open(file_path, 'rb') as raw:
            data = raw.read(4096)
    except OSError:
        return default, 0.0

    result = chardet.detect(data)
    encoding = result.get('encoding') or default
    confidence = result.get('confidence') or 0.0

    with _ENCODING_CACHE_LOCK:
        _ENCODING_CACHE[cache_key] = {
            'encoding': encoding,
            'confidence': confidence,
            'mtime': stat.st_mtime,
            'size': stat.st_size,
        }

    return encoding, confidence

def safe_open(filepath, mode='r', encoding=None, errors='strict', newline=''):
    attempts = 0
    while attempts < 5:
        try:
            actual_encoding = encoding
            if actual_encoding is None and 'r' in mode:
                detected_enc, confidence = detect_encoding(filepath)
                if confidence < 0.5 or not detected_enc:
                    log_message('warning', f"Low confidence ({confidence}); fallback to utf-8 replace.")
                    actual_encoding = 'utf-8'
                    errors = 'replace'
                else:
                    actual_encoding = detected_enc
                    log_message('debug', f"Detected encoding: {actual_encoding} (confidence={confidence})")
            return open(filepath, mode, encoding=actual_encoding, errors=errors, newline=newline)
        except PermissionError:
            log_message('warning', f"File locked or in use: {filepath}. Retrying...")
            time.sleep(1)
            attempts += 1
        except OSError as e:
            log_message('warning', f"OS Error opening {filepath}: {e}. Retrying...")
            time.sleep(1)
            attempts += 1
    raise OSError(f"Could not open file after multiple retries: {filepath}")

def clean_data(data):
    """Replace non-printable characters with '?', do NFKD, trim, etc."""
    cleaned_data = []
    for row_idx, row in enumerate(data):
        new_row = []
        for cell_idx, cell in enumerate(row):
            if not isinstance(cell, str):
                cell = str(cell) if cell is not None else ''
            original_cell = cell

            # NFKD
            cell = unicodedata.normalize('NFKD', cell)
            # Replace unknown or non-printable with '?'
            buf = []
            for c in cell:
                if c.isprintable():
                    buf.append(c)
                else:
                    buf.append('?')
            cell = ''.join(buf)

            # Trim
            cell = cell.strip()
            # Collapse multiple spaces
            cell = ' '.join(cell.split())

            if cell != original_cell:
                log_message('debug', f"Row {row_idx}, Cell {cell_idx}: '{original_cell}' -> '{cell}'")

            new_row.append(cell)
        cleaned_data.append(new_row)
    return cleaned_data

def apply_force_to_cell(data, config):
    log_message('debug', "Starting apply_force_to_cell function")
    actions = [a for a in config.get('data', []) if a.get('action') == 'force_to_cell']
    if not actions:
        return data, set()

    from collections import defaultdict
    target_to_substrings = defaultdict(list)
    for action_config in actions:
        substring = action_config.get('substring')
        target_column = action_config.get('target_column')
        if substring is None or target_column is None:
            log_message('warning', "Skipping force_to_cell with missing substring/target_column")
            continue
        target_idx = int(target_column) - 1
        target_to_substrings[target_idx].append(substring)

    protected_columns = set(target_to_substrings.keys())

    for row_idx, row in enumerate(data):
        for target_idx, substrings in target_to_substrings.items():
            column_set = False
            for substring in substrings:
                for i, cell in enumerate(row):
                    if substring in cell:
                        # Found the substring => set row[target_idx] = that cell
                        if target_idx >= len(row):
                            row.extend([''] * (target_idx - len(row) + 1))
                        row[target_idx] = cell
                        column_set = True
                        break
                if column_set:
                    break
            # If no substring matched => set row[target_idx] = '' but preserve the rest
            if not column_set:
                if target_idx >= len(row):
                    row.extend([''] * (target_idx - len(row) + 1))
                row[target_idx] = ''
    return data, protected_columns

def apply_remove_action(data, config):
    log_message('debug', "Starting apply_remove_action function")
    actions = [a for a in config.get('data', []) if a.get('action') == 'remove']
    if not actions:
        return data
    substrings = []
    for a in actions:
        substring = a.get('substring')
        if substring:
            substrings.append(substring)
    for row_idx, row in enumerate(data):
        for i, cell in enumerate(row):
            if not isinstance(cell, str):
                continue
            original_cell = cell
            for substring in substrings:
                if substring in cell:
                    cell = cell.replace(substring, '')
            cell = cell.strip()
            if cell != original_cell:
                log_message('debug', f"Row {row_idx}, Cell {i}: '{original_cell}' -> '{cell}'")
            row[i] = cell
    return data

def apply_reorder_to_data(data, config, protected_columns=set()):
    log_message('debug', "Starting apply_reorder_to_data function")
    actions = [a for a in config.get('data', []) if a.get('action') == 'reorder']
    if not actions:
        return data

    action_config = actions[0]
    order = action_config.get('order', [])
    log_message('info', f"Applying reorder with order={order}")
    # zero-based
    order = [i - 1 for i in order]

    reordered_data = []
    for row_idx, row in enumerate(data):
        # new row must have enough columns
        new_row = [''] * max(len(row), len(order), max(protected_columns or [0]) + 1)

        # keep the data in protected columns
        for idx in protected_columns:
            if idx < len(row):
                new_row[idx] = row[idx]

        dest_idx = 0
        for src_idx in order:
            # skip over protected columns in the new row
            while dest_idx in protected_columns:
                dest_idx += 1
            if src_idx < len(row):
                new_row[dest_idx] = row[src_idx]
            else:
                new_row[dest_idx] = ''
            dest_idx += 1
        reordered_data.append(new_row)

    return reordered_data

def assign_headers(data, header_columns):
    log_message('debug', "Starting assign_headers function")
    expected_columns = len(header_columns)
    adjusted_data = []
    for row_idx, row in enumerate(data):
        original_length = len(row)
        if len(row) > expected_columns:
            row = row[:expected_columns]
            log_message('debug', f"Row {row_idx}: Truncated from {original_length} to {expected_columns} columns")
        elif len(row) < expected_columns:
            row.extend([''] * (expected_columns - len(row)))
            log_message('debug', f"Row {row_idx}: Extended from {original_length} to {expected_columns} columns")
        adjusted_data.append(row)
    return adjusted_data


def _sanitize_expression(expr: str) -> str:
    """
    Replace control characters in an expression string to keep numexpr happy.

    Numexpr rejects expressions that contain ASCII control characters
    (e.g. from accidental copy/paste), raising a 'forbidden control characters'
    error. We defensively strip those out before evaluation so configs with
    hidden characters don't crash parsing.
    """
    cleaned_chars = []
    had_bad = False
    for ch in expr:
        code = ord(ch)
        # Replace ASCII control chars (0–31, 127) with a space
        if code < 32 or code == 127 or not ch.isprintable():
            cleaned_chars.append(' ')
            had_bad = True
        else:
            cleaned_chars.append(ch)
    cleaned = ''.join(cleaned_chars)
    if had_bad and cleaned != expr:
        log_message('warning', f"Sanitized control characters in math expression: '{expr}' -> '{cleaned}'")
    return cleaned

def _sanitize_expression_ascii(expr: str) -> str:
    cleaned_chars = []
    had_bad = False
    for ch in expr:
        code = ord(ch)
        if code < 32 or code == 127 or code > 126:
            cleaned_chars.append(' ')
            had_bad = True
        else:
            cleaned_chars.append(ch)
    cleaned = ''.join(cleaned_chars)
    if had_bad and cleaned != expr:
        log_message('warning', f"Sanitized control characters in math expression: '{expr}' -> '{cleaned}'")
    return cleaned

def apply_math_operations(df, config):
    log_message('debug', "Starting apply_math_operations function")
    actions = [a for a in config.get('data', []) if a.get('action') == 'math_operations']
    if not actions:
        return df

    action_config = actions[0]
    operations = action_config.get('operations', [])
    log_message('debug', f"Math operations to apply: {operations}")

    col_index_mapping = {f'C{i+1}': col for i, col in enumerate(df.columns)}
    col_name_mapping = {}
    for col in df.columns:
        valid_name = re.sub(r'\W|^(?=\d)', '_', col)
        col_name_mapping[col] = valid_name

    local_dict = {}
    for idx_label, col_name in col_index_mapping.items():
        local_dict[idx_label] = pd.to_numeric(df[col_name], errors='coerce')
    for col, valid_name in col_name_mapping.items():
        local_dict[valid_name] = pd.to_numeric(df[col], errors='coerce')

    for operation in operations:
        sanitized_op = _sanitize_expression_ascii(operation)
        log_message('debug', f"Math op raw: '{operation}'")
        log_message('debug', f"Math op sanitized: '{sanitized_op}' "
                             f"codes={','.join(str(ord(c)) for c in sanitized_op)}")
        if '=' not in sanitized_op:
            log_message('warning', f"Invalid math operation format: '{operation}'")
            continue
        target_column, expression = sanitized_op.split('=', 1)
        target_column = target_column.strip()
        expression = expression.strip()

        # Check if round(...) usage
        match = re.match(r'^round\s*\(\s*(.+)\s*,\s*(\d+)\s*\)$', expression)
        if match:
            inner_expr = _sanitize_expression_ascii(match.group(1))
            log_message('debug', f"Inner expr sanitized: '{inner_expr}' "
                                 f"codes={','.join(str(ord(c)) for c in inner_expr)}")
            decimals = int(match.group(2))
            # Replace col references
            for col, valid_name in col_name_mapping.items():
                inner_expr = re.sub(r'\b' + re.escape(col) + r'\b', valid_name, inner_expr)
            for idx_label in col_index_mapping.keys():
                inner_expr = re.sub(r'\b' + re.escape(idx_label) + r'\b', idx_label, inner_expr)
            try:
                temp_result = ne.evaluate(inner_expr, local_dict)
                temp_result = temp_result.round(decimals)
                actual_col = col_index_mapping.get(target_column, target_column)
                df[actual_col] = temp_result
                local_dict[target_column] = temp_result
                if target_column in col_index_mapping:
                    local_dict[col_index_mapping[target_column]] = temp_result
            except Exception as e:
                log_message('error', f"Failed to evaluate round() operation '{sanitized_op}': {e}")
        else:
            # Normal expression
            expression = _sanitize_expression_ascii(expression)
            for col, valid_name in col_name_mapping.items():
                expression = re.sub(r'\b' + re.escape(col) + r'\b', valid_name, expression)
            for idx_label in col_index_mapping.keys():
                expression = re.sub(r'\b' + re.escape(idx_label) + r'\b', idx_label, expression)
            try:
                result = ne.evaluate(expression, local_dict)
                actual_col = col_index_mapping.get(target_column, target_column)
                df[actual_col] = result
                local_dict[target_column] = result
                if target_column in col_index_mapping:
                    local_dict[col_index_mapping[target_column]] = result
            except Exception as e:
                log_message('error', f"Failed to evaluate math operation '{sanitized_op}': {e}")

    return df

