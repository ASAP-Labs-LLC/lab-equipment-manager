# File: csv_parser_app/parsers/data_handler.py

import os
import io
import json
import pandas as pd
import threading
import csv
import shutil
import serial
import time
from datetime import datetime
from typing import Optional, List, Tuple, Dict  # <-- Added Dict here
from .csv_parser import parse_csv
from ..utils.logging_utils import log_message

def process_single_csv(input_csv: str, output_csv: str, config_folder: str) -> pd.DataFrame:
    """
    Process a single CSV file, appending new data to the master CSV.
    
    :param input_csv: Path to the input CSV file.
    :param output_csv: Path to the output (master) CSV.
    :param config_folder: Path to the folder containing parser_config.json.
    :return: DataFrame of newly appended data.
    """
    try:
        config_file = os.path.join(config_folder, 'parser_config.json')
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)

        last_position = config['single_csv'].get('last_position', 0)
        with open(input_csv, 'r', encoding='utf-8') as file:
            file.seek(last_position)
            new_data = file.read()
            current_position = file.tell()

        if not new_data.strip():
            log_message('info', f"No new data to process in '{input_csv}'.")
            return pd.DataFrame()  # No new data

        # Update last_position
        config['single_csv']['last_position'] = current_position
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)

        file_like = io.StringIO(new_data)
        df = parse_csv(file_like, config_folder, delimiter=config['single_csv'].get('delimiter', ','))
        append_to_master_csv(output_csv, df)
        log_message('info', f"Processed new data from '{input_csv}' and appended to '{output_csv}'.")
        return df
    except Exception as e:
        error_message = f"Error during single file processing: {e}"
        log_message('error', error_message)
        raise

def process_multi_csv(input_folder: str, move_folder: str, output_folder: str, config_folder: str) -> List[Tuple[str, pd.DataFrame]]:
    """
    Process multiple CSV files from a folder, moving processed files.
    
    :param input_folder: Folder containing CSV files.
    :param move_folder: Folder to move files after processing.
    :param output_folder: (Unused param in this snippet, but kept for consistency).
    :param config_folder: Path to parser_config.json folder.
    :return: List of (filename, DataFrame) for each processed file.
    """
    dataframes = []
    try:
        config_file = os.path.join(config_folder, 'parser_config.json')
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)

        csv_files = [f for f in os.listdir(input_folder) if f.lower().endswith('.csv')]
        if not csv_files:
            log_message('info', f"No CSV files found in '{input_folder}' to process.")
            return dataframes

        has_header = config['multi'].get('has_header', False)

        for csv_file in csv_files:
            csv_path = os.path.join(input_folder, csv_file)
            with open(csv_path, 'r', encoding='utf-8') as file:
                lines = file.readlines()
                # Skip header if specified
                if has_header:
                    lines = lines[1:]
                file_content = ''.join(lines)
                file_like = io.StringIO(file_content)

            df = parse_csv(file_like, config_folder, delimiter=config['multi'].get('delimiter', ','))
            dataframes.append((csv_file, df))

            output_csv = config['multi'].get('output_file')
            append_to_master_csv(output_csv, df)

            # Move processed file
            shutil.move(csv_path, os.path.join(move_folder, csv_file))
            log_message('info', f"Processed '{csv_file}' and moved to '{move_folder}'.")

        return dataframes
    except Exception as e:
        error_message = f"Error during multi-file processing: {e}"
        log_message('error', error_message)
        raise

def append_to_master_csv(master_file: str, df: pd.DataFrame) -> None:
    """
    Append data to the master CSV file, rotating files based on the date.
    
    :param master_file: Path to the master CSV file.
    :param df: DataFrame to append.
    """
    try:
        if df.empty:
            log_message('info', "No data to append to the master CSV.")
            return

        master_dir = os.path.dirname(master_file)
        past_data_folder = os.path.join(master_dir, 'Past Data')
        os.makedirs(past_data_folder, exist_ok=True)

        # Rotate master file if it's a new day
        if os.path.exists(master_file):
            file_mod_time = datetime.fromtimestamp(os.path.getmtime(master_file))
            file_mod_date_str = file_mod_time.strftime('%Y-%m-%d')
            today_date = datetime.now().strftime('%Y-%m-%d')
            if file_mod_date_str != today_date:
                # Move old master file
                base_name, ext = os.path.splitext(os.path.basename(master_file))
                new_file_name = f"{base_name}_{file_mod_date_str}{ext}"
                new_file_path = os.path.join(past_data_folder, new_file_name)
                shutil.move(master_file, new_file_path)
                log_message('info', f"Rotated master CSV file to '{new_file_path}'.")

        write_header = not os.path.exists(master_file)
        df.to_csv(master_file, mode='a', header=write_header, index=False, encoding='utf-8')
        log_message('info', f"Appended data to master CSV '{master_file}'.")
    except Exception as e:
        error_message = f"Error writing to master CSV '{master_file}': {e}"
        log_message('error', error_message)
        raise

def process_com_port(com_port_config: Dict, config_folder: str, status_callback=None) -> None:
    """
    Process data from a COM port in a dedicated thread.
    
    :param com_port_config: Dictionary with port, baud_rate, and so on.
    :param config_folder: Folder containing parser_config.json.
    :param status_callback: Optional PyQt signal or callback to update UI status.
    """
    port = com_port_config.get('port')
    baud_rate = int(com_port_config.get('baud_rate', 9600))
    parity = com_port_config.get('parity', 'N')
    stop_bits = com_port_config.get('stop_bits', 1)
    byte_size = com_port_config.get('byte_size', 8)
    timeout = com_port_config.get('timeout', 1)
    output_file = com_port_config.get('output')

    if not port:
        message = "COM port not specified in configuration."
        log_message('error', message)
        if status_callback:
            status_callback.emit(message)
        return

    stop_event = threading.Event()
    buffer = []
    last_data_time = datetime.now()
    data_lock = threading.Lock()

    def connect_to_port():
        nonlocal ser
        while not stop_event.is_set():
            try:
                ser = serial.Serial(
                    port=port,
                    baudrate=baud_rate,
                    parity=parity,
                    stopbits=stop_bits,
                    bytesize=byte_size,
                    timeout=timeout
                )
                message = f"Connected to {port}"
                log_message('info', message)
                if status_callback:
                    status_callback.emit(message)
                break
            except serial.SerialException as e:
                if 'Access is denied' in str(e) or 'could not open port' in str(e):
                    message = f"Access denied to {port}: {e}"
                    log_message('error', message)
                    if status_callback:
                        status_callback.emit('ACCESS_DENIED')
                    break
                else:
                    message = f"Failed to connect to {port}: {e}"
                    log_message('error', message)
                    if status_callback:
                        status_callback.emit(message)
                    time.sleep(1)
            except Exception as e:
                message = f"Unexpected error connecting to {port}: {e}"
                log_message('error', message)
                if status_callback:
                    status_callback.emit(message)
                time.sleep(1)

    def read_from_port():
        nonlocal last_data_time
        while not stop_event.is_set():
            try:
                if ser and ser.is_open:
                    line = ser.readline().decode('utf-8', errors='replace').strip()
                    if line:
                        line = line.replace('�', 'N/A')
                        with data_lock:
                            buffer.append(line)
                            last_data_time = datetime.now()
                    else:
                        time.sleep(0.1)
                else:
                    time.sleep(1)
            except Exception as e:
                message = f"Error reading from COM port: {e}"
                log_message('error', message)
                if status_callback:
                    status_callback.emit(message)
                try:
                    ser.close()
                except:
                    pass
                time.sleep(1)
                connect_to_port()

    ser = None
    connect_to_port()

    reader_thread = threading.Thread(target=read_from_port, daemon=True)
    reader_thread.start()

    while not stop_event.is_set():
        time.sleep(1)
        data_to_process = []
        with data_lock:
            if buffer and (datetime.now() - last_data_time).total_seconds() > 2:
                data_to_process = buffer.copy()
                buffer.clear()
        if data_to_process:
            try:
                rows = [line.split(',') for line in data_to_process]
                # Transpose
                transposed_data = list(map(list, zip(*rows)))
                csv_buffer = io.StringIO()
                csv_writer = csv.writer(csv_buffer)
                csv_writer.writerows(transposed_data)
                csv_buffer.seek(0)
                df = parse_csv(csv_buffer, config_folder)
                append_to_master_csv(output_file, df)
                msg = f"Appended data to {output_file}"
                log_message('info', msg)
                if status_callback:
                    status_callback.emit(msg)
            except Exception as e:
                message = f"Error processing COM port data: {e}"
                log_message('error', message)
                if status_callback:
                    status_callback.emit(message)

    if ser and ser.is_open:
        ser.close()
    reader_thread.join()
