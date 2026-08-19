import os
import json

# Configuration file path
CONFIG_FILE = 'eq.config'

# Default configuration structure (without default parser_type)
default_config = {
    "multi": {
        "input": "",
        "move": "",
        "output": "",
        "config_folder": ""
    },
    "single_csv": {
        "input": "",
        "output": "",
        "config_folder": ""
    },
    "COM": {
        "com_port": "COM7",
        "baudrate": 9600,
        "parity": "N",
        "stopbits": 1,
        "bytesize": 8,
        "timeout": 1,
        "output": ".",
        "adjustable_delay": 2
    },
    "header": [],
    "data": [],
    "instructions": {}
}

def load_config(config_file=CONFIG_FILE):
    """Load the configuration from the JSON file."""
    if os.path.exists(config_file):
        with open(config_file, 'r') as f:
            config = json.load(f)
    else:
        print(f"ERROR: Configuration file '{config_file}' does not exist.")
        print("Please run the setup to create one.")
        config = default_config  # Start with default empty config
    return config

def save_config(config, config_file=CONFIG_FILE):
    """Save the configuration to the JSON file."""
    with open(config_file, 'w') as f:
        json.dump(config, f, indent=4)

def set_parser_type(config, parser_type):
    """Set the parser type in the configuration."""
    config['parser_type'] = parser_type

def set_multi_config(config, input_folder, move_folder, output_folder, config_folder):
    """Set the multi CSV parser configuration."""
    config['multi']['input'] = input_folder
    config['multi']['move'] = move_folder
    config['multi']['output'] = output_folder
    config['multi']['config_folder'] = config_folder

def set_single_csv_config(config, input_csv, output_csv, config_folder):
    """Set the single CSV parser configuration."""
    config['single_csv']['input'] = input_csv
    config['single_csv']['output'] = output_csv
    config['single_csv']['config_folder'] = config_folder

def set_COM_config(config, com_port, baudrate, parity, stopbits, bytesize, timeout, output, adjustable_delay=2):
    """Set the COM port parser configuration."""
    config['COM']['com_port'] = com_port
    config['COM']['baudrate'] = baudrate
    config['COM']['parity'] = parity
    config['COM']['stopbits'] = stopbits
    config['COM']['bytesize'] = bytesize
    config['COM']['timeout'] = timeout
    config['COM']['output'] = output
    config['COM']['adjustable_delay'] = adjustable_delay

def set_header(config, header_list):
    """Set the header configuration."""
    config['header'] = header_list

def add_data_action(config, action_dict):
    """Add an action to the data processing steps."""
    if 'data' not in config:
        config['data'] = []
    config['data'].append(action_dict)

def clear_data_actions(config):
    """Clear all data processing actions."""
    config['data'] = []

def set_instructions(config, instructions_dict):
    """Set the instructions configuration."""
    config['instructions'] = instructions_dict

# The interactive terminal interface for testing
if __name__ == '__main__':
    config = load_config()

    print("Configuration Testing via Terminal")
    print("Press Enter to keep current values.\n")

    # Parser Type
    print(f"Current parser type: {config.get('parser_type', 'Not set')}")
    parser_type = input("Enter parser type (single, multi, COM): ").strip()
    if parser_type:
        set_parser_type(config, parser_type)
    elif 'parser_type' in config:
        parser_type = config['parser_type']
    else:
        print("ERROR: 'parser_type' is not set in the configuration.")
        exit(1)

    # Depending on the parser type, get the relevant settings
    if parser_type == 'single':
        # Single CSV settings
        input_csv = input(f"Enter input CSV path [{config['single_csv']['input']}]: ").strip() or config['single_csv']['input']
        output_csv = input(f"Enter output CSV path [{config['single_csv']['output']}]: ").strip() or config['single_csv']['output']
        config_folder = input(f"Enter config folder path [{config['single_csv']['config_folder']}]: ").strip() or config['single_csv']['config_folder']
        set_single_csv_config(config, input_csv, output_csv, config_folder)
    elif parser_type == 'multi':
        # Multi CSV settings
        input_folder = input(f"Enter input folder path [{config['multi']['input']}]: ").strip() or config['multi']['input']
        move_folder = input(f"Enter move folder path [{config['multi']['move']}]: ").strip() or config['multi']['move']
        output_folder = input(f"Enter output folder path [{config['multi']['output']}]: ").strip() or config['multi']['output']
        config_folder = input(f"Enter config folder path [{config['multi']['config_folder']}]: ").strip() or config['multi']['config_folder']
        set_multi_config(config, input_folder, move_folder, output_folder, config_folder)
    elif parser_type == 'COM':
        # COM Port settings
        com_port = input(f"Enter COM port [{config['COM']['com_port']}]: ").strip() or config['COM']['com_port']
        baudrate = input(f"Enter baudrate [{config['COM']['baudrate']}]: ").strip() or config['COM']['baudrate']
        parity = input(f"Enter parity [{config['COM']['parity']}]: ").strip() or config['COM']['parity']
        stopbits = input(f"Enter stopbits [{config['COM']['stopbits']}]: ").strip() or config['COM']['stopbits']
        bytesize = input(f"Enter bytesize [{config['COM']['bytesize']}]: ").strip() or config['COM']['bytesize']
        timeout = input(f"Enter timeout [{config['COM']['timeout']}]: ").strip() or config['COM']['timeout']
        output = input(f"Enter output file path [{config['COM']['output']}]: ").strip() or config['COM']['output']
        adjustable_delay = input(f"Enter adjustable delay [{config['COM'].get('adjustable_delay', 2)}]: ").strip() or config['COM'].get('adjustable_delay', 2)
        # Convert numerical inputs to appropriate types
        try:
            baudrate = int(baudrate)
            stopbits = int(stopbits)
            bytesize = int(bytesize)
            timeout = int(timeout)
            adjustable_delay = int(adjustable_delay)
            set_COM_config(config, com_port, baudrate, parity, stopbits, bytesize, timeout, output, adjustable_delay)
        except ValueError:
            print("ERROR: Invalid numerical input for COM configuration.")
            exit(1)
    else:
        print("ERROR: Invalid parser type specified.")
        exit(1)

    # Header configuration
    current_headers = ', '.join(config.get('header', []))
    header_input = input(f"Enter headers as comma-separated values [{current_headers}]: ").strip()
    if header_input:
        header_list = [h.strip() for h in header_input.split(',')]
        set_header(config, header_list)

    # Data processing steps
    print("\nConfigure data processing steps.")
    clear_data_actions(config)
    while True:
        print("\nAvailable actions:")
        for action in ['force_to_cell', 'reorder', 'remove', 'find_replace', 'math_operations']:
            print(f"- {action}")
        action_choice = input("Enter action to add (or 'done' to finish): ").strip()
        if action_choice.lower() == 'done':
            break
        elif action_choice == 'force_to_cell':
            force_dict = {}
            while True:
                text = input("Enter text to force into a cell (or 'done' to finish): ").strip()
                if text.lower() == 'done':
                    break
                index = input(f"Enter cell index for '{text}': ").strip()
                if not index.isdigit():
                    print("Please enter a valid integer index.")
                    continue
                index = int(index)
                force_dict[text] = index
            if force_dict:
                action_dict = {"action": "force_to_cell", "force_to_cell": force_dict}
                add_data_action(config, action_dict)
        elif action_choice == 'reorder':
            order_input = input("Enter new column order as comma-separated indices (e.g., 1,2,3): ").strip()
            try:
                order = [int(idx.strip()) for idx in order_input.split(',')]
                action_dict = {"action": "reorder", "order": order}
                add_data_action(config, action_dict)
            except ValueError:
                print("Invalid input. Please enter integers separated by commas.")
        elif action_choice == 'remove':
            values = []
            while True:
                value = input("Enter value to remove (or 'done' to finish): ").strip()
                if value.lower() == 'done':
                    break
                values.append(value)
            if values:
                action_dict = {"action": "remove", "value": values}
                add_data_action(config, action_dict)
        elif action_choice == 'find_replace':
            find = input("Enter text to find: ").strip()
            replace = input("Enter replacement text: ").strip()
            if find:
                action_dict = {"action": "find_replace", "find": find, "replace": replace}
                add_data_action(config, action_dict)
            else:
                print("Find text cannot be empty.")
        elif action_choice == 'math_operations':
            operations = []
            while True:
                row = input("Enter row (number or 'all') for the operation (or 'done' to finish): ").strip()
                if row.lower() == 'done':
                    break
                column = input("Enter target column name: ").strip()
                operation = input("Enter Python expression for the operation: ").strip()
                if row and column and operation:
                    op_dict = {"row": row, "column": column, "operation": operation}
                    operations.append(op_dict)
                else:
                    print("Row, column, and operation cannot be empty.")
            if operations:
                action_dict = {"action": "math_operations", "operations": operations}
                add_data_action(config, action_dict)
        else:
            print("Invalid action. Please choose a valid action.")

    # Save updated configuration
    save_config(config)
    print("\nConfiguration saved to", CONFIG_FILE)
