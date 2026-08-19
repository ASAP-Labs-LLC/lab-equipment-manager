# csv_parser.py
import pandas as pd
import json
import os
from utils import generate_default_headers, load_json_config, save_json_config

def parse_csv(df, config_folder):
    """
    Parse CSV data based on the parser configuration.

    Parameters:
    - df (pd.DataFrame): The DataFrame to parse.
    - config_folder (str): Path to the folder containing the parser_config.json.

    Returns:
    - pd.DataFrame: The parsed DataFrame.
    """
    try:
        config_file = os.path.join(config_folder, 'parser_config.json')
        config = load_json_config(config_file)

        # Retrieve data actions; ensure it's a list
        data_actions = config.get('data', [])
        if not isinstance(data_actions, list):
            raise ValueError("Data actions should be a list.")

        # Define action priority to ensure 'force_to_cell' is before 'remove'
        action_priority = {'force_to_cell': 1, 'remove': 2, 'reorder': 3, 'find_replace': 4, 'math_operations': 5}
        # Sort actions based on priority
        data_actions_sorted = sorted(data_actions, key=lambda x: action_priority.get(x.get('action', ''), 99))

        for action in data_actions_sorted:
            action_type = action.get('action')
            if not action_type:
                continue  # Skip if action type is not defined

            if action_type == 'force_to_cell':
                column = action.get('column')
                value = action.get('value')
                if column and value is not None:
                    if column in df.columns:
                        df[column] = value
                    else:
                        print(f"Warning: Column '{column}' not found in DataFrame. Skipping 'force_to_cell' action.")
                else:
                    print("Warning: 'force_to_cell' action missing 'column' or 'value'. Skipping.")

            elif action_type == 'remove':
                columns_to_remove = action.get('columns_to_remove', [])
                if columns_to_remove:
                    df.drop(columns=columns_to_remove, inplace=True, errors='ignore')
                else:
                    print("Warning: 'remove' action missing 'columns_to_remove'. Skipping.")

            elif action_type == 'reorder':
                new_order = action.get('new_order', [])
                if new_order:
                    # Ensure indices are within the range
                    max_index = len(df.columns) - 1
                    valid_order = [idx for idx in new_order if 0 <= idx <= max_index]
                    if valid_order:
                        # Reorder columns based on provided indices
                        df = df.iloc[:, valid_order]
                    else:
                        print("Warning: 'reorder' action has invalid indices. Skipping.")
                else:
                    print("Warning: 'reorder' action missing 'new_order'. Skipping.")

            elif action_type == 'find_replace':
                find = action.get('find')
                replace = action.get('replace')
                if find is not None and replace is not None:
                    df.replace(to_replace=find, value=replace, inplace=True)
                else:
                    print("Warning: 'find_replace' action missing 'find' or 'replace'. Skipping.")

            elif action_type == 'math_operations':
                column = action.get('column')
                operation = action.get('operation')  # Expected format: "add 10", "multiply 2", etc.
                if column and operation:
                    if column in df.columns:
                        try:
                            op, value = operation.split()
                            value = float(value)
                            if op.lower() == 'add':
                                df[column] = pd.to_numeric(df[column], errors='coerce') + value
                            elif op.lower() == 'subtract':
                                df[column] = pd.to_numeric(df[column], errors='coerce') - value
                            elif op.lower() == 'multiply':
                                df[column] = pd.to_numeric(df[column], errors='coerce') * value
                            elif op.lower() == 'divide':
                                df[column] = pd.to_numeric(df[column], errors='coerce') / value
                            else:
                                print(f"Warning: Unsupported operation '{op}' in 'math_operations'. Skipping.")
                        except ValueError:
                            print("Warning: 'math_operations' action has invalid format. Expected 'operation value'. Skipping.")
                    else:
                        print(f"Warning: Column '{column}' not found in DataFrame. Skipping 'math_operations' action.")
                else:
                    print("Warning: 'math_operations' action missing 'column' or 'operation'. Skipping.")

            else:
                print(f"Warning: Unsupported action type '{action_type}'. Skipping.")

        return df

    except Exception as e:
        error_message = f"ERROR: Issue in parse_csv: {e}"
        safe_error_message = error_message.encode('ascii', errors='replace').decode('ascii')
        print(safe_error_message)
        raise
