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
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit
from PyQt5.QtCore import Qt
import time
import chardet
import shutil

LOG_LOCK = threading.Lock()
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

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
    So we don't overwrite row #1, we open in 'a' mode.
    """
    from datetime import datetime

    if os.path.exists(raw_file):
        file_mod_time = datetime.fromtimestamp(os.path.getmtime(raw_file))
        file_mod_date_str = file_mod_time.strftime('%Y-%m-%d')
        today_date = datetime.now().strftime('%Y-%m-%d')
        if file_mod_date_str != today_date:
            # Rotate
            raw_dir = os.path.dirname(raw_file)
            past_data_folder = os.path.join(raw_dir, 'Past Data')
            os.makedirs(past_data_folder, exist_ok=True)
            base_name, ext = os.path.splitext(os.path.basename(raw_file))
            new_file_name = f"{base_name}_{file_mod_date_str}{ext}"
            new_file_path = os.path.join(past_data_folder, new_file_name)
            shutil.move(raw_file, new_file_path)
            log_message('info', f"Rotated raw CSV from '{raw_file}' to '{new_file_path}'.")

    # Now append
    with open(raw_file, 'a', encoding='utf-8', newline='') as f:
        if os.path.getsize(raw_file) > 0:
            f.write('\n')
        f.write(new_text)

def parse_csv(file_like_object, config_folder, delimiter=','):
    """
    Parse CSV data from a file-like object using the provided config.
    1) Append raw data to *_RAW.csv (rotate daily).
    2) Expand single-col rows if we see semicolons.
    3) Possibly prompt Lab ID, only override the configured column, preserving the rest.
    4) Reorder, remove, etc. Then return a DataFrame.
    """
    try:
        config_file = os.path.join(config_folder, 'parser_config.json')
        log_message('info', f"Loading configuration from {config_file}")
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
        log_message('debug', f"Configuration: {config}")

        # 1) Read raw data
        raw_csv_data = file_like_object.read()

        # 2) Determine the raw file name
        parser_type = config.get('parser_type')
        machine_name = config.get('machine_name', 'Unnamed')
        if parser_type == 'single':
            raw_file = append_raw_suffix(config['single_csv']['output'])
        elif parser_type == 'multi':
            raw_file = append_raw_suffix(config['multi']['output_file'])
        elif parser_type == 'COM':
            raw_file = append_raw_suffix(config['COM']['output'])
        else:
            raw_file = os.path.join(config_folder, f"{machine_name}_parsed_RAW.csv")

        # 3) Append to raw csv
        append_to_raw_csv(raw_file, raw_csv_data)

        # 4) Convert raw_csv_data to a CSV list
        parse_buffer = StringIO(raw_csv_data)
        reader = csv.reader(parse_buffer, delimiter=delimiter)
        raw_rows = list(reader)

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

        log_message('info', f"Data after reading from CSV (and expanding single-col semicolon):\n{data}")

        # 5) Clean data
        data = clean_data(data)
        log_message('info', f"Data after cleaning:\n{data}")

        # 6) Possibly prompt Lab ID
        lab_id_prompting = config.get('lab_id_prompting', False)
        lab_id_col_1based = config.get('lab_id_column', 1)
        lab_id_column = lab_id_col_1based - 1  # zero-based

        if lab_id_prompting:
            dialog = LabIDPromptDialog()
            # If the user cancels or closes, we return None
            if dialog.exec_() == 0:
                log_message('info', "Lab ID prompt canceled by user; returning None.")
                return None
            lab_id_val = dialog.result_value

            # Put that ID into the target_column of each row, preserving other columns
            for row_idx, row in enumerate(data):
                if lab_id_column >= len(row):
                    row.extend([''] * (lab_id_column - len(row) + 1))
                # Overwrite just that cell
                row[lab_id_column] = lab_id_val

            log_message('info', f"Data after applying Lab ID ({lab_id_val}):\n{data}")

        # 7) Apply data actions (force_to_cell, remove, reorder)
        data, protected_cols = apply_force_to_cell(data, config)
        log_message('info', f"Data after force_to_cell:\n{data}")

        data = apply_remove_action(data, config)
        log_message('info', f"Data after remove action:\n{data}")

        data = apply_reorder_to_data(data, config, protected_cols)
        log_message('info', f"Data after reorder:\n{data}")

        # 8) Assign headers
        header_columns = config.get('header', [])
        data = assign_headers(data, header_columns)
        log_message('info', f"Data after assigning headers:\n{data}")

        # 9) Convert to DataFrame, add date/time
        df = pd.DataFrame(data, columns=header_columns)
        now = datetime.datetime.now()
        df["parsed_date"] = now.strftime("%Y-%m-%d")
        df["parsed_time"] = now.strftime("%H:%M:%S")
        log_message('info', f"DataFrame columns before math operations: {df.columns.tolist()}")

        df = apply_math_operations(df, config)
        log_message('info', f"Data after math operations:\n{df}")

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

def detect_encoding(file_path, default='utf-8'):
    with open(file_path, 'rb') as raw:
        data = raw.read(4096)
    result = chardet.detect(data)
    encoding = result['encoding']
    confidence = result['confidence']
    if not encoding:
        return default, 0.0
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
                    log_message('info', f"Detected encoding: {actual_encoding} (confidence={confidence})")
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
        if '=' not in operation:
            log_message('warning', f"Invalid math operation format: '{operation}'")
            continue
        target_column, expression = operation.split('=', 1)
        target_column = target_column.strip()
        expression = expression.strip()

        # Check if round(...) usage
        match = re.match(r'^round\s*\(\s*(.+)\s*,\s*(\d+)\s*\)$', expression)
        if match:
            inner_expr = match.group(1)
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
                log_message('error', f"Failed to evaluate round() operation '{operation}': {e}")
        else:
            # Normal expression
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
                log_message('error', f"Failed to evaluate math operation '{operation}': {e}")

    return df
