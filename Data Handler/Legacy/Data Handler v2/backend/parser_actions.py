import os
import json
import pandas as pd
import numexpr as ne
import csv
import datetime
import unicodedata
import re
import shutil
import logging
from collections import defaultdict
from io import StringIO

LOG = logging.getLogger(__name__)

def parse_csv(file_like_object, config_folder, delimiter=',', config=None):
    """
    Parse CSV data from file_like_object using the given configuration.
    Includes cleaning, data actions, math operations, and raw data logging.
    """
    try:
        if config is None:
            config_file = os.path.join(config_folder, 'parser_config.json')
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)

        # Save raw data before parsing if feature is enabled
        features = config.get('features', {})
        if features.get('enable_raw_data_logging', True):
            save_raw_data(file_like_object, config)

        # Reset the pointer after save_raw_data
        file_like_object.seek(0)

        reader = csv.reader(file_like_object, delimiter=delimiter)
        data = list(reader)
        LOG.debug(f"Original data: {data}")

        data = clean_data(data)
        data, protected_columns = apply_force_to_cell(data, config)
        data = apply_remove_action(data, config)
        data = apply_reorder_to_data(data, config, protected_columns)

        header_columns = config.get('header', [])
        data = assign_headers(data, header_columns)

        df = pd.DataFrame(data, columns=header_columns)
        LOG.debug(f"DataFrame before math operations: {df}")
        df = apply_math_operations(df, config)
        LOG.debug(f"DataFrame after math operations: {df}")

        LOG.info("Parsed CSV data successfully.")
        return df

    except Exception as e:
        LOG.error(f"Issue parsing CSV data: {e}", exc_info=True)
        raise

def save_raw_data(file_like_object, config):
    """
    Save raw data lines to a raw_data CSV file, rotate daily if needed.
    """
    machine_name = config.get('machine_name', 'Unnamed')
    config_folder = config.get('config_folder', '')
    raw_dir = os.path.join(config_folder, 'Raw Data')
    os.makedirs(raw_dir, exist_ok=True)

    raw_file = os.path.join(raw_dir, f"{machine_name}_raw_data.csv")

    # Rotate daily if needed
    if os.path.exists(raw_file):
        file_mod_time = datetime.datetime.fromtimestamp(os.path.getmtime(raw_file))
        file_mod_date_str = file_mod_time.strftime('%Y-%m-%d')
        today_date = datetime.datetime.now().strftime('%Y-%m-%d')
        if file_mod_date_str != today_date:
            past_data_folder = os.path.join(raw_dir, 'Past Data')
            os.makedirs(past_data_folder, exist_ok=True)
            new_file_name = f"{machine_name}_raw_data_{file_mod_date_str}.csv"
            shutil.move(raw_file, os.path.join(past_data_folder, new_file_name))
            LOG.info(f"Rotated raw data file to '{new_file_name}'.")

    file_like_object.seek(0)
    raw_lines = file_like_object.read().split('\n')

    write_header = not os.path.exists(raw_file)
    with open(raw_file, 'a', encoding='utf-8') as f:
        if write_header:
            f.write("RawData\n")
        for line in raw_lines:
            if line.strip():
                f.write(line + '\n')
    file_like_object.seek(0)  # Reset position after writing

def clean_data(data):
    """
    Clean and format the data: remove non-printables, trim spaces, normalize unicode.
    """
    cleaned_data = []
    for row_idx, row in enumerate(data):
        new_row = []
        for cell_idx, cell in enumerate(row):
            if not isinstance(cell, str):
                cell = str(cell) if cell is not None else ''
            original_cell = cell
            cell = unicodedata.normalize('NFKD', cell)
            cell = ''.join(c for c in cell if c.isprintable())
            cell = ' '.join(cell.strip().split())

            if cell != original_cell:
                LOG.debug(f"Row {row_idx}, Cell {cell_idx}: Cleaned '{original_cell}' -> '{cell}'")
            new_row.append(cell)
        cleaned_data.append(new_row)
    return cleaned_data

def apply_force_to_cell(data, config):
    """
    Apply 'force_to_cell' actions to place certain substrings into specific columns.
    """
    actions = [a for a in config.get('data', []) if a.get('action') == 'force_to_cell']
    if not actions:
        return data, set()

    target_to_substrings = defaultdict(list)
    for action_config in actions:
        substring = action_config.get('substring')
        target_column = action_config.get('target_column')
        if substring is None or target_column is None:
            LOG.warning("Skipping 'force_to_cell' action due to missing parameters.")
            continue
        target_idx = int(target_column) - 1
        target_to_substrings[target_idx].append(substring)

    target_columns = set(target_to_substrings.keys())

    for row_idx, row in enumerate(data):
        for target_idx, substrings in target_to_substrings.items():
            column_set = False
            for substring in substrings:
                for i, cell in enumerate(row):
                    if substring in cell:
                        if target_idx >= len(row):
                            row.extend([''] * (target_idx - len(row) + 1))
                        row[target_idx] = cell
                        column_set = True
                        break
                if column_set:
                    break
            if not column_set:
                if target_idx >= len(row):
                    row.extend([''] * (target_idx - len(row) + 1))
                row[target_idx] = ''

    return data, target_columns

def apply_reorder_to_data(data, config, protected_columns=set()):
    """
    Apply 'reorder' action, rearranging columns except those protected by 'force_to_cell'.
    """
    actions = [a for a in config.get('data', []) if a.get('action') == 'reorder']
    if not actions:
        return data
    action_config = actions[0]
    order = action_config.get('order', [])
    order = [i - 1 for i in order]  # zero-based

    reordered_data = []
    for row_idx, row in enumerate(data):
        new_row = [''] * max(len(row), len(order) + len(protected_columns))
        # preserve protected columns
        for idx in protected_columns:
            if idx < len(row):
                new_row[idx] = row[idx]

        dest_idx = 0
        for src_idx in order:
            while dest_idx in protected_columns:
                dest_idx += 1
            if src_idx < len(row):
                new_row[dest_idx] = row[src_idx]
            else:
                new_row[dest_idx] = ''
            dest_idx += 1
        reordered_data.append(new_row)
    return reordered_data

def apply_remove_action(data, config):
    """
    Apply 'remove' action to remove specific substrings from cells.
    """
    actions = [a for a in config.get('data', []) if a.get('action') == 'remove']
    if not actions:
        return data

    substrings = [a.get('substring') for a in actions if a.get('substring')]
    for row_idx, row in enumerate(data):
        for i, cell in enumerate(row):
            if not isinstance(cell, str):
                continue
            original_cell = cell
            for substring in substrings:
                cell = cell.replace(substring, '')
            cell = cell.strip()
            if cell != original_cell:
                LOG.debug(f"Row {row_idx}, Cell {i}: Removed substrings -> '{cell}'")
            row[i] = cell
    return data

def assign_headers(data, header_columns):
    """
    Ensure each row aligns with header columns count.
    """
    expected_columns = len(header_columns)
    adjusted_data = []
    for row_idx, row in enumerate(data):
        original_length = len(row)
        if len(row) > expected_columns:
            row = row[:expected_columns]
        elif len(row) < expected_columns:
            row.extend([''] * (expected_columns - len(row)))
        adjusted_data.append(row)
    return adjusted_data

def apply_math_operations(df, config):
    """
    Apply math operations defined in config[data] to the DataFrame columns.
    """
    actions = [a for a in config.get('data', []) if a.get('action') == 'math_operations']
    if not actions:
        return df

    action_config = actions[0]
    operations = action_config.get('operations', [])

    col_index_mapping = {f'C{i+1}': col for i, col in enumerate(df.columns)}
    col_name_mapping = {col: re.sub(r'\W|^(?=\d)', '_', col) for col in df.columns}

    local_dict = {}
    for idx_label, col_name in col_index_mapping.items():
        local_dict[idx_label] = pd.to_numeric(df[col_name], errors='coerce')
    for col, valid_name in col_name_mapping.items():
        local_dict[valid_name] = pd.to_numeric(df[col], errors='coerce')

    for operation in operations:
        if '=' not in operation:
            LOG.warning(f"Invalid math operation format: '{operation}'")
            continue
        target_column, expression = operation.split('=', 1)
        target_column = target_column.strip()
        expression = expression.strip()

        # Replace references with valid names
        for col, valid_name in col_name_mapping.items():
            expression = re.sub(r'\b' + re.escape(col) + r'\b', valid_name, expression)
        for idx_label in col_index_mapping.keys():
            expression = re.sub(r'\b' + re.escape(idx_label) + r'\b', idx_label, expression)

        try:
            result = ne.evaluate(expression, local_dict)
            if target_column in col_index_mapping:
                actual_column_name = col_index_mapping[target_column]
            elif target_column in col_name_mapping.values():
                # If target is a valid_name
                actual_column_name = [k for k, v in col_name_mapping.items() if v == target_column][0]
            else:
                actual_column_name = target_column

            df[actual_column_name] = result
            # Update local_dict for subsequent operations
            new_col_valid_name = re.sub(r'\W|^(?=\d)', '_', actual_column_name)
            local_dict[new_col_valid_name] = pd.to_numeric(df[actual_column_name], errors='coerce')
            LOG.debug(f"Applied math operation '{operation}' to '{actual_column_name}'")

        except Exception as e:
            LOG.error(f"Failed to evaluate math operation '{operation}': {e}", exc_info=True)

    return df
