# data_handler.py
import os
import time
import io
import json
import pandas as pd
from csv_parser import parse_csv
from datetime import datetime
import shutil

def process_single_csv(input_csv, output_csv, config_folder):
    config_file = os.path.join(config_folder, 'parser_config.json')
    with open(config_file, 'r') as f:
        config = json.load(f)
    try:
        last_position = config['single_csv'].get('last_position', 0)
        with open(input_csv, 'r') as file:
            file.seek(last_position)
            new_data = file.read()
            current_position = file.tell()
        if not new_data.strip():
            return pd.DataFrame()  # No new data
        config['single_csv']['last_position'] = current_position
        with open(config_file, 'w') as f:
            json.dump(config, f, indent=4)
        file_like = io.StringIO(new_data)
        df = parse_csv(file_like, config_folder, delimiter=config['single_csv'].get('delimiter', ','))
        append_to_master_csv(output_csv, df)
        return df
    except Exception as e:
        print(f"ERROR: Error during single file processing: {e}")
        raise

def process_multi_csv(input_folder, move_folder, output_folder, config_folder):
    dataframes = []
    try:
        config_file = os.path.join(config_folder, 'parser_config.json')
        with open(config_file, 'r') as f:
            config = json.load(f)
        csv_files = [f for f in os.listdir(input_folder) if f.lower().endswith('.csv')]
        if not csv_files:
            return dataframes  # No CSV files to process

        has_header = config['multi'].get('has_header', False)

        for csv_file in csv_files:
            csv_path = os.path.join(input_folder, csv_file)
            with open(csv_path, 'r') as file:
                lines = file.readlines()
                if has_header:
                    lines = lines[1:]  # Skip header
                file_content = ''.join(lines)
                file_like = io.StringIO(file_content)

            df = parse_csv(file_like, config_folder, delimiter=config['multi'].get('delimiter', ','))
            dataframes.append((csv_file, df))

            output_csv = config['multi'].get('output_file')
            append_to_master_csv(output_csv, df)

            # Move processed file
            shutil.move(csv_path, os.path.join(move_folder, csv_file))
        return dataframes
    except Exception as e:
        print(f"ERROR: Error during multi-file processing: {e}")
        raise

def append_to_master_csv(master_file, df):
    try:
        if df.empty:
            return  # No data to append

        # Check if the file needs to be renamed and moved to the "Past Data" folder based on the date
        master_dir = os.path.dirname(master_file)
        past_data_folder = os.path.join(master_dir, 'Past Data')
        os.makedirs(past_data_folder, exist_ok=True)

        if os.path.exists(master_file):
            file_mod_time = datetime.fromtimestamp(os.path.getmtime(master_file))
            file_mod_date_str = file_mod_time.strftime('%Y-%m-%d')
            today_date = datetime.now().strftime('%Y-%m-%d')
            if file_mod_date_str != today_date:
                # Add last modified date to the file name
                new_file_name = f"{os.path.splitext(os.path.basename(master_file))[0]}_{file_mod_date_str}.csv"
                new_file_path = os.path.join(past_data_folder, new_file_name)
                shutil.move(master_file, new_file_path)
                print(f"Moved old file to: {new_file_path}")

        write_header = not os.path.exists(master_file)
        df.to_csv(master_file, mode='a', header=write_header, index=False)
    except Exception as e:
        print(f"ERROR: Issue writing to the master CSV file '{master_file}': {e}")
        raise

def process_com_port(com_port_config):
    pass  # Implement COM port processing as needed
