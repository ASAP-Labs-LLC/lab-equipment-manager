# csv_parser.py
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

# Configure logging
LOG_LOCK = threading.Lock()
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
# Ensure that logging outputs to the console
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s [%(LEVELNAME)s] %(message)s')
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

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

def parse_csv(file_like_object, config_folder, delimiter=','):
    """Parse CSV data from a file-like object using the provided configuration."""
    try:
        # Load configuration
        config_file = os.path.join(config_folder, 'parser_config.json')
        log_message('info', f"Loading configuration from {config_file}")
        with open(config_file, 'r') as f:
            config = json.load(f)
        log_message('debug', f"Configuration: {config}")

        # Read CSV data
        reader = csv.reader(file_like_object, delimiter=delimiter)
        data = list(reader)
        log_message('info', "Original data read from CSV:")
        log_message('info', f"{data}")

        # Clean data
        data = clean_data(data)
        log_message('info', "Data after cleaning:")
        log_message('info', f"{data}")

        # Proceed with other data actions
        
        data, protected_columns = apply_force_to_cell(data, config)
        log_message('info', "Data after force_to_cell:")
        log_message('info', f"{data}")
        log_message('debug', f"Protected columns: {protected_columns}")

        data = apply_remove_action(data, config)
        log_message('info', "Data after remove action:")
        log_message('info', f"{data}")

        data = apply_reorder_to_data(data, config, protected_columns)
        log_message('info', "Data after reorder:")
        log_message('info', f"{data}")

        # Assign headers
        header_columns = config.get('header', [])
        log_message('debug', f"Header columns: {header_columns}")
        data = assign_headers(data, header_columns)
        log_message('info', "Data after assigning headers:")
        log_message('info', f"{data}")

        # Convert to DataFrame
        df = pd.DataFrame(data, columns=header_columns)
        log_message('info', "Data converted to DataFrame:")
        log_message('info', f"{df}")

        #Print Dataframe before Math Operations
        log_message('info', f"DataFrame columns before math operations: {df.columns.tolist()}")

        # Apply math operations
        df = apply_math_operations(df, config)
        log_message('info', "Data after math operations:")
        log_message('info', f"{df}")

        # Log the parsed data
        parsed_data_log = os.path.join(config_folder, 'parsed_data.log')
        with open(parsed_data_log, 'a') as log_file:
            entry = f"{datetime.datetime.now()} - Parsed Data:\n{df}\n"
            log_file.write(entry)

        log_message('info', "Parsed CSV data successfully.")
        return df
    except Exception as e:
        error_message = f"Issue parsing CSV data: {e}"
        log_message('error', error_message)
        raise


def clean_data(data):
    """Clean and format the data before any data actions are applied."""
    cleaned_data = []
    for row_idx, row in enumerate(data):
        new_row = []
        for cell_idx, cell in enumerate(row):
            # Convert cell to string if it's not already
            if not isinstance(cell, str):
                cell = str(cell) if cell is not None else ''
            original_cell = cell  # Keep original cell for logging

            # Normalize Unicode characters
            cell = unicodedata.normalize('NFKD', cell)

            # Remove non-printable characters
            cell = ''.join(c for c in cell if c.isprintable())

            # Trim leading and trailing whitespace
            cell = cell.strip()

            # Replace multiple spaces with a single space
            cell = ' '.join(cell.split())

            # Log changes if any
            if cell != original_cell:
                log_message('debug', f"Row {row_idx}, Cell {cell_idx}: Cleaned cell from '{original_cell}' to '{cell}'")
            new_row.append(cell)
        cleaned_data.append(new_row)
    return cleaned_data


def apply_force_to_cell(data, config):
    """Apply the 'force_to_cell' action to the data."""
    log_message('debug', "Starting apply_force_to_cell function")
    actions = [action for action in config.get('data', []) if action.get('action') == 'force_to_cell']
    if not actions:
        log_message('debug', "No 'force_to_cell' actions found in configuration")
        return data, set()
    
    # Group substrings by target column
    target_to_substrings = defaultdict(list)
    for action_config in actions:
        substring = action_config.get('substring')
        target_column = action_config.get('target_column')
        if substring is None or target_column is None:
            log_message('warning', "Skipping 'force_to_cell' action due to missing 'substring' or 'target_column'")
            continue  # Skip if necessary parameters are missing
        target_idx = int(target_column) - 1  # Convert to 0-based index
        target_to_substrings[target_idx].append(substring)
        log_message('debug', f"Adding substring '{substring}' to target column {target_idx}")
    
    target_columns = set(target_to_substrings.keys())
    
    for row_idx, row in enumerate(data):
        for target_idx, substrings in target_to_substrings.items():
            column_set = False  # Flag to check if any substring matched
            for substring in substrings:
                for i, cell in enumerate(row):
                    if substring in cell:
                        log_message('debug', f"Row {row_idx}, Cell {i}: Found substring '{substring}' for target column {target_idx}")
                        # Ensure the target index is within bounds
                        if target_idx >= len(row):
                            row.extend([''] * (target_idx - len(row) + 1))
                            log_message('debug', f"Row {row_idx}: Extended row to accommodate target index {target_idx}")
                        row[target_idx] = cell  # Set the target column to the cell's content
                        # Optionally, you can remove the substring from the original cell
                        # row[i] = cell.replace(substring, '')
                        column_set = True
                        log_message('debug', f"Row {row_idx}: Set target column {target_idx} to '{row[target_idx]}'")
                        break  # Stop after first match for this column
                if column_set:
                    break  # Move to the next target column after a successful match
            if not column_set:
                # Set target column to empty string if no substrings matched
                if target_idx >= len(row):
                    row.extend([''] * (target_idx - len(row) + 1))
                    log_message('debug', f"Row {row_idx}: Extended row to accommodate target index {target_idx}")
                row[target_idx] = ''
                log_message('debug', f"Row {row_idx}: No substrings matched for target column {target_idx}; set to empty string")
    
    return data, target_columns


def apply_reorder_to_data(data, config, protected_columns=set()):
    """Apply the 'reorder' action to the data, skipping protected columns."""
    log_message('debug', "Starting apply_reorder_to_data function")
    actions = [action for action in config.get('data', []) if action.get('action') == 'reorder']
    if not actions:
        return data
    action_config = actions[0]  # Assuming only one reorder action
    order = action_config.get('order', [])
    # Adjust indices to zero-based
    order = [i - 1 for i in order]
    log_message('debug', f"Reorder order (zero-based): {order}")
    reordered_data = []
    for row_idx, row in enumerate(data):
        # Initialize new_row with existing data
        new_row = [''] * max(len(row), len(order) + len(protected_columns))
        # Keep data in protected columns
        for idx in protected_columns:
            if idx < len(row):
                new_row[idx] = row[idx]
                log_message('debug', f"Row {row_idx}: Preserved protected column {idx}")
        dest_idx = 0  # Index in new_row
        for src_idx in order:
            # Skip protected columns in destination
            while dest_idx in protected_columns:
                dest_idx += 1
            if src_idx < len(row):
                new_row[dest_idx] = row[src_idx]
                log_message('debug', f"Row {row_idx}: Moved data from source index {src_idx} to destination index {dest_idx}")
            else:
                new_row[dest_idx] = ''
                log_message('debug', f"Row {row_idx}: Source index {src_idx} out of range; set destination index {dest_idx} to empty string")
            dest_idx += 1
        reordered_data.append(new_row)
    return reordered_data

def apply_remove_action(data, config):
    """Apply the 'remove' action to the data."""
    log_message('debug', "Starting apply_remove_action function")
    actions = [action for action in config.get('data', []) if action.get('action') == 'remove']
    if not actions:
        return data
    substrings = []
    for action_config in actions:
        substring = action_config.get('substring')
        if substring is None:
            continue  # Skip if necessary parameter is missing
        substrings.append(substring)
    log_message('debug', f"Substrings to remove: {substrings}")
    for row_idx, row in enumerate(data):
        for i, cell in enumerate(row):
            if not isinstance(cell, str):
                continue  # Skip non-string cells
            original_cell = cell  # Keep original cell for logging
            for substring in substrings:
                if substring in cell:
                    cell = cell.replace(substring, '')
                    log_message('debug', f"Row {row_idx}, Cell {i}: Removed substring '{substring}'")
            cell = cell.strip()
            if cell != original_cell:
                log_message('debug', f"Row {row_idx}, Cell {i}: Changed from '{original_cell}' to '{cell}'")
            row[i] = cell
    return data

def assign_headers(data, header_columns):
    """Ensure each row has the same number of columns as headers and assign headers."""
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
    """Apply mathematical operations to the DataFrame."""
    log_message('debug', "Starting apply_math_operations function")
    actions = [action for action in config.get('data', []) if action.get('action') == 'math_operations']
    if not actions:
        log_message('debug', "No 'math_operations' actions found in configuration")
        return df
    action_config = actions[0]
    operations = action_config.get('operations', [])
    log_message('debug', f"Math operations to apply: {operations}")

    # Create mappings
    col_index_mapping = {f'C{i+1}': col for i, col in enumerate(df.columns)}
    col_name_mapping = {}
    for col in df.columns:
        valid_name = re.sub(r'\W|^(?=\d)', '_', col)
        col_name_mapping[col] = valid_name

    # Prepare local variables
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

        # Replace column names and indices with variable names
        for col, valid_name in col_name_mapping.items():
            expression = re.sub(r'\b' + re.escape(col) + r'\b', valid_name, expression)
        for idx_label in col_index_mapping.keys():
            expression = re.sub(r'\b' + re.escape(idx_label) + r'\b', idx_label, expression)

        log_message('debug', f"Evaluating expression for '{target_column}': {expression}")

        # Evaluate the expression
        try:
            result = ne.evaluate(expression, local_dict)
            # Map target_column if it's a column index like 'C1'
            if target_column in col_index_mapping:
                actual_column_name = col_index_mapping[target_column]
            elif target_column in col_name_mapping:
                actual_column_name = target_column
            else:
                actual_column_name = target_column  # New column or custom name

            df[actual_column_name] = result
            log_message('debug', f"Applied math operation to column '{actual_column_name}'")
        except Exception as e:
            log_message('error', f"Failed to evaluate math operation '{operation}': {e}")

    return df