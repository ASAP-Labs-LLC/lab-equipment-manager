# csv_parser.py
import os
import json
import pandas as pd
import numexpr as ne
import csv
import datetime
import re

def load_config_files(config_folder):
    try:
        config_file = os.path.join(config_folder, 'parser_config.json')
        if not os.path.exists(config_file):
            raise FileNotFoundError(f"Configuration file '{config_file}' does not exist.")
        with open(config_file, 'r') as f:
            config = json.load(f)
        return config
    except Exception as e:
        print(f"ERROR: Issue loading configuration files in folder '{config_folder}': {e}")
        raise

def parse_csv(file_like, config_folder, delimiter=','):
    try:
        config = load_config_files(config_folder)
        header_columns = config.get('header', [])

        if not header_columns:
            raise ValueError("Header configuration is missing or empty in the config file.")

        # Prepare for logging
        log_file_path = os.path.join(config_folder, 'parsing_steps.log')
        log_entries = []

        # Log the original data
        file_like.seek(0)
        original_data = file_like.read()
        log_entries.append(f"=== Original Data ({datetime.datetime.now()}): ===\n{original_data}\n")

        # Reset the file pointer after reading
        file_like.seek(0)

        # Read CSV data line by line using the specified delimiter
        reader = csv.reader(file_like, delimiter=delimiter)
        data = [row for row in reader]

        log_entries.append(f"=== Data After Initial Reading ({datetime.datetime.now()}): ===\n{data}\n")

        # Apply data actions on the data before assigning headers
        for action_config in config.get('data', []):
            action = action_config.get('action')
            if action == 'reorder':
                data = apply_reorder_to_data(data, action_config)
                log_entries.append(f"=== Data After 'reorder' Action ({datetime.datetime.now()}): ===\n{data}\n")
            elif action in ['remove', 'find_replace', 'force_to_cell']:
                data = apply_line_based_action(data, action_config)
                log_entries.append(f"=== Data After '{action}' Action ({datetime.datetime.now()}): ===\n{data}\n")

        # Now assign headers to the data
        # Ensure each row has the same number of columns as headers
        expected_columns = len(header_columns)
        adjusted_data = []
        for row in data:
            if len(row) > expected_columns:
                row = row[:expected_columns]
            elif len(row) < expected_columns:
                row.extend([''] * (expected_columns - len(row)))
            adjusted_data.append(row)
        data = adjusted_data

        log_entries.append(f"=== Data After Assigning Headers ({datetime.datetime.now()}): ===\n{data}\n")

        # Create DataFrame from the data with headers
        df = pd.DataFrame(data, columns=header_columns)

        # Apply 'math_operations' on the DataFrame
        for action_config in config.get('data', []):
            action = action_config.get('action')
            if action == 'math_operations':
                df = apply_math_operations(df, action_config)
                log_entries.append(f"=== DataFrame After 'math_operations' Action ({datetime.datetime.now()}): ===\n{df}\n")

        # Write logs to file
        with open(log_file_path, 'a', encoding='utf-8') as log_file:
            for entry in log_entries:
                log_file.write(entry + '\n')

        return df
    except Exception as e:
        error_message = f"ERROR: Issue parsing CSV data: {e}"
        safe_error_message = error_message.encode('ascii', errors='replace').decode('ascii')
        print(safe_error_message)
        raise

def apply_reorder_to_data(data, action_config):
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

def apply_line_based_action(data, action_config):
    action = action_config.get('action')
    if action == 'remove':
        remove_values = action_config.get('value', [])
        data = [[cell for cell in row if cell not in remove_values] for row in data]
    elif action == 'find_replace':
        find_value = action_config.get('find')
        replace_value = action_config.get('replace')
        if find_value is not None and replace_value is not None:
            data = [[cell.replace(find_value, replace_value) for cell in row] for row in data]
    elif action == 'force_to_cell':
        force_to_cell = action_config.get('force_to_cell', {})
        for row in data:
            for value, index in force_to_cell.items():
                if value in row:
                    row.remove(value)
                    index = int(index)
                    if index < len(row):
                        row.insert(index, value)
                    else:
                        # Pad the row with empty strings if necessary
                        row.extend([''] * (index - len(row)))
                        row.append(value)
    return data

def apply_math_operations(df, action_config):
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
        row_spec = operation.get('row')
        column = operation.get('column')
        expression = operation.get('operation')

        # Determine if the expression contains string literals
        if re.search(r'\'|\"', expression):
            # Handle string operations
            expression_original = expression  # Keep the original expression for logging
            # Replace original column names in the expression with df_renamed['<col_name>'].astype(str)
            for orig_col, valid_col in col_name_mapping.items():
                expression = re.sub(re.escape(orig_col), f"df_renamed['{valid_col}'].astype(str)", expression)
            # Evaluate the expression
            try:
                df_renamed[column] = eval(expression)
            except Exception as e:
                print(f"ERROR: Failed to evaluate string operation '{expression_original}' for column '{column}': {e}")
        else:
            # Handle math operations with numexpr
            expression_original = expression  # Keep the original expression for logging
            # Replace original column names in the expression with valid variable names
            for orig_col, valid_col in col_name_mapping.items():
                # Use re.escape to safely match column names
                expression = re.sub(r'\b' + re.escape(orig_col) + r'\b', valid_col, expression)

            target_column_valid = re.sub(r'\W|^(?=\d)', '_', column)

            if target_column_valid not in df_renamed.columns:
                df_renamed[target_column_valid] = ''  # Create the column if it doesn't exist

            if row_spec == 'all':
                try:
                    # Prepare local variables: column names mapped to Series
                    local_dict = {col: pd.to_numeric(df_renamed[col], errors='coerce') for col in df_renamed.columns}
                    result = ne.evaluate(expression, local_dict)
                    df_renamed[target_column_valid] = result
                except Exception as e:
                    print(f"ERROR: Failed to evaluate math operation '{expression_original}' on all rows for column '{column}': {e}")
            else:
                try:
                    row_index = int(row_spec)
                    if row_index >= len(df_renamed):
                        print(f"WARNING: Row index {row_index} is out of bounds.")
                        continue
                    local_dict = {}
                    for col in df_renamed.columns:
                        value = df_renamed.at[row_index, col]
                        try:
                            local_dict[col] = float(value)
                        except (ValueError, TypeError):
                            continue
                    result = ne.evaluate(expression, local_dict)
                    df_renamed.at[row_index, target_column_valid] = result
                except Exception as e:
                    print(f"ERROR: Failed to evaluate math operation '{expression_original}' on row {row_index}, column '{column}': {e}")

    # Rename columns back to original names
    df = df_renamed.rename(columns=inverse_col_name_mapping)

    return df
