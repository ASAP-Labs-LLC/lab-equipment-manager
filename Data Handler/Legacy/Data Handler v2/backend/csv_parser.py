import os
import csv
import datetime
import pandas as pd
import logging
import shutil
from io import StringIO

from .data_cleaner import clean_data
from .parser_actions import (apply_force_to_cell, apply_remove_action, apply_reorder_to_data, 
                             assign_headers, apply_math_operations)
from .feature_flags import FeatureFlags

LOG = logging.getLogger(__name__)

def parse_csv(file_like_object, config_folder, delimiter=',', config=None):
    if config is None:
        config_file = os.path.join(config_folder, 'parser_config.json')
        with open(config_file, 'r') as f:
            config = json.load(f)
    
    flags = FeatureFlags(config)
    # Save raw data if enabled
    if flags.is_enabled("enable_raw_data_logging"):
        save_raw_data(file_like_object, config)

    # Rewind to start parsing
    file_like_object.seek(0)
    reader = csv.reader(file_like_object, delimiter=delimiter)
    data = list(reader)
    LOG.info(f"Original data read from CSV: {data}")

    data = clean_data(data)
    LOG.info(f"Data after cleaning: {data}")

    data, protected_columns = apply_force_to_cell(data, config)
    data = apply_remove_action(data, config)
    data = apply_reorder_to_data(data, config, protected_columns)
    header_columns = config.get('header', [])
    data = assign_headers(data, header_columns)
    df = pd.DataFrame(data, columns=header_columns)

    df = apply_math_operations(df, config)
    LOG.info(f"Final parsed data:\n{df}")

    return df

def save_raw_data(file_like_object, config):
    machine_name = config.get('machine_name', 'Unnamed')
    config_folder = config.get('config_folder', '')
    raw_dir = os.path.join(config_folder, 'Raw Data')
    os.makedirs(raw_dir, exist_ok=True)

    raw_file = os.path.join(raw_dir, f"{machine_name}_raw_data.csv")
    file_like_object.seek(0)
    raw_lines = file_like_object.read()

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

    write_header = not os.path.exists(raw_file)
    with open(raw_file, 'a', encoding='utf-8') as f:
        if write_header:
            f.write("RawData\n")
        for line in raw_lines.split('\n'):
            if line.strip():
                f.write(line + '\n')
