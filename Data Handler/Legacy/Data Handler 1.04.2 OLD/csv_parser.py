# csv_parser.py
import os
import json
import pandas as pd
import numexpr as ne
import csv
import datetime
import re
import logging
import threading  # Add this import
from io import StringIO

# Configure logging
LOG_LOCK = threading.Lock()  # Ensure threading.Lock() is used

def log_message(level, message):
    with LOG_LOCK:
        logger = logging.getLogger()
        if level == 'info':
            logger.info(message)
        elif level == 'warning':
            logger.warning(message)
        elif level == 'error':
            logger.error(message)
        else:
            logger.debug(message)
            
def load_config_files(config_folder):
    """Load parser configuration files from the specified folder."""
    try:
        config_file = os.path.join(config_folder, 'parser_config.json')
        if not os.path.exists(config_file):
            raise FileNotFoundError(f"Configuration file '{config_file}' does not exist.")
        with open(config_file, 'r') as f:
            config = json.load(f)
        return config
    except Exception as e:
        message = f"Issue loading configuration files in folder '{config_folder}': {e}"
        log_message('error', message)
        raise

def parse_csv(file_like, config_folder, delimiter=','):
    """Parse CSV data according to the configuration."""
    try:
        config = load_config_files(config_folder)
        header_columns = config.get('header', [])

        if not header_columns:
            raise ValueError("Header configuration is missing or empty in the config file.")

        # Prepare for logging
        log_entries = []

        # Log the original data
        file_like.seek(0)
        original_data = file_like.read()
        log_entries.append(f"=== Original Data ({datetime.datetime.now()}): ===\n{original_data}\n")
        file_like.seek(0)

        # Read CSV data line by line using the specified delimiter
        reader = csv.reader(file_like, delimiter=delimiter)
        data = [row for row in reader]

        log_entries.append(f"=== Data After Initial Reading ({datetime.datetime.now()}): ===\n{data}\n")

        # Apply data actions in the desired order
        data = apply_force_to_cell(data, config)
        log_entries.append(f"=== Data After 'force_to_cell' Action ({datetime.datetime.now()}): ===\n{data}\n")

        data = apply_reorder_to_data(data, config)
        log_entries.append(f"=== Data After 'reorder' Action ({datetime.datetime.now()}): ===\n{data}\n")

        data = apply_remove_action(data, config)
        log_entries.append(f"=== Data After 'remove' Action ({datetime.datetime.now()}): ===\n{data}\n")

        # Now assign headers to the data
        data = assign_headers(data, header_columns)
        log_entries.append(f"=== Data After Assigning Headers ({datetime.datetime.now()}): ===\n{data}\n")

        # Create DataFrame from the data with headers
        df = pd.DataFrame(data, columns=header_columns)

        # Apply 'math_operations' on the DataFrame
        df = apply_math_operations(df, config)
        log_entries.append(f"=== DataFrame After 'math_operations' ({datetime.datetime.now()}): ===\n{df}\n")

        # Write logs to file
        log_file_path = os.path.join(config_folder, 'parsing_steps.log')
        with open(log_file_path, 'a', encoding='utf-8') as log_file:
            for entry in log_entries:
                log_file.write(entry + '\n')

        log_message('info', "Parsed CSV data successfully.")
        return df
    except Exception as e:
        error_message = f"Issue parsing CSV data: {e}"
        log_message('error', error_message)
        raise

def assign_headers(data, header_columns):
    """Ensure each row has the same number of columns as headers and assign headers."""
    expected_columns = len(header_columns)
    adjusted_data = []
    for row in data:
        if len(row) > expected_columns:
            row = row[:expected_columns]
        elif len(row) < expected_columns:
            row.extend([''] * (expected_columns - len(row)))
        adjusted_data.append(row)
    return adjusted_data

def apply_force_to_cell(data, config):
    """Apply the 'force_to_cell' action to the data."""
    actions = [action for action in config.get('data', []) if action.get('action') == 'force_to_cell']
    if not actions:
        return data
    for action_config in actions:
        substring = action_config.get('substring')
        target_column = action_config.get('target_column')
        if substring is None or target_column is None:
            continue  # Skip if necessary parameters are missing
        for row in data:
            for i, cell in enumerate(row):
                if substring in cell:
                    # Move the entire cell content to the target column
                    target_idx = int(target_column) - 1  # 1-based indexing
                    if target_idx >= len(row):
                        row.extend([''] * (target_idx - len(row) + 1))
                    row[target_idx] += cell
                    row[i] = cell.replace(substring, '')
    return data

def apply_reorder_to_data(data, config):
    """Apply the 'reorder' action to the data."""
    actions = [action for action in config.get('data', []) if action.get('action') == 'reorder']
    if not actions:
        return data
    action_config = actions[0]  # Assuming only one reorder action
    order = action_config.get('order', [])
    # Adjust indices to zero-based
    order = [i - 1 for i in order]
    reordered_data = []
    for row in data:
        reordered_row = []
        for idx in order:
            if idx < len(row):
                reordered_row.append(row[idx])
            else:
                reordered_row.append('')  # Pad with empty string if index is out of bounds
        reordered_data.append(reordered_row)
    return reordered_data

def apply_remove_action(data, config):
    """Apply the 'remove' action to the data."""
    actions = [action for action in config.get('data', []) if action.get('action') == 'remove']
    if not actions:
        return data
    for action_config in actions:
        substring = action_config.get('substring')
        if substring is None:
            continue  # Skip if necessary parameter is missing
        for row in data:
            for i, cell in enumerate(row):
                if substring in cell:
                    row[i] = cell.replace(substring, '')
    return data

def apply_math_operations(df, config):
    """Apply mathematical operations to the DataFrame."""
    actions = [action for action in config.get('data', []) if action.get('action') == 'math_operations']
    if not actions:
        return df
    action_config = actions[0]  # Assuming only one math_operations action
    operations = action_config.get('operations', [])

    # Create a mapping from original column names to valid variable names
    col_name_mapping = {}
    for col in df.columns:
        valid_name = re.sub(r'\W|^(?=\d)', '_', col)
        col_name_mapping[col] = valid_name

    # Invert the mapping for easy lookup
    inverse_col_name_mapping = {v: k for k, v in col_name_mapping.items()}

    # Rename columns in the DataFrame to valid variable names
    df_renamed = df.rename(columns=col_name_mapping)

    for operation in operations:
        # Expected format: "column = expression"
        if '=' not in operation:
            log_message('warning', f"Invalid math operation format: '{operation}'")
            continue
        column, expression = operation.split('=', 1)
        column = column.strip()
        expression = expression.strip()

        # Replace original column names in the expression with valid variable names
        for orig_col, valid_col in col_name_mapping.items():
            expression = re.sub(r'\b' + re.escape(orig_col) + r'\b', valid_col, expression)

        target_column_valid = re.sub(r'\W|^(?=\d)', '_', column)

        if target_column_valid not in df_renamed.columns:
            df_renamed[target_column_valid] = ''  # Create the column if it doesn't exist

        try:
            # Prepare local variables: column names mapped to Series
            local_dict = {col: pd.to_numeric(df_renamed[col], errors='coerce') for col in df_renamed.columns}
            result = ne.evaluate(expression, local_dict)
            df_renamed[target_column_valid] = result
        except Exception as e:
            log_message('error', f"Failed to evaluate math operation '{operation}': {e}")

    # Rename columns back to original names
    df = df_renamed.rename(columns=inverse_col_name_mapping)

    return df
