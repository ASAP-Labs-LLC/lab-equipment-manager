# data_handler.py
import os
import io
import json
import pandas as pd
import threading
import csv
import shutil
import serial
import time
import logging
from datetime import datetime
from csv_parser import parse_csv

# Define directories
CONFIG_DIR = os.path.join(os.path.expanduser('~'), 'csv_parser_configs')
LOG_FILE = os.path.join(CONFIG_DIR, 'parser.log')

# Configure logging
LOG_LOCK = threading.Lock()
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(filename)s - %(funcName)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def log_message(level, message):
    with LOG_LOCK:
        logger = logging.getLogger(__name__)
        if level == 'info':
            logger.info(message)
        elif level == 'warning':
            logger.warning(message)
        elif level == 'error':
            logger.error(message)
        else:
            logger.debug(message)

def process_single_csv(input_csv, output_csv, config_folder):
    """Process a single CSV file, appending new data to the master CSV."""
    try:
        config_file = os.path.join(config_folder, 'parser_config.json')
        with open(config_file, 'r') as f:
            config = json.load(f)

        last_position = config['single_csv'].get('last_position', 0)
        with open(input_csv, 'r', encoding='utf-8') as file:
            file.seek(last_position)
            new_data = file.read()
            current_position = file.tell()

        if not new_data.strip():
            log_message('info', f"No new data to process in '{input_csv}'.")
            return  # No new data

        # Update last_position in the config
        config['single_csv']['last_position'] = current_position
        with open(config_file, 'w') as f:
            json.dump(config, f, indent=4)

        file_like = io.StringIO(new_data)
        df = parse_csv(file_like, config_folder, delimiter=config['single_csv'].get('delimiter', ','))
        append_to_master_csv(output_csv, df)
        log_message('info', f"Processed new data from '{input_csv}' and appended to '{output_csv}'.")
    except Exception as e:
        error_message = f"Error during single file processing: {e}"
        log_message('error', error_message)
        raise

def process_multi_csv(input_folder, move_folder, output_folder, config_folder):
    """Process multiple CSV files from a folder, moving processed files."""
    try:
        config_file = os.path.join(config_folder, 'parser_config.json')
        with open(config_file, 'r') as f:
            config = json.load(f)

        csv_files = [f for f in os.listdir(input_folder) if f.lower().endswith('.csv')]
        if not csv_files:
            log_message('info', f"No CSV files found in '{input_folder}' to process.")
            return  # No CSV files to process

        has_header = config['multi'].get('has_header', False)

        for csv_file in csv_files:
            csv_path = os.path.join(input_folder, csv_file)
            with open(csv_path, 'r', encoding='utf-8') as file:
                lines = file.readlines()
                if has_header:
                    lines = lines[1:]  # Skip header
                file_content = ''.join(lines)
                file_like = io.StringIO(file_content)

            df = parse_csv(file_like, config_folder, delimiter=config['multi'].get('delimiter', ','))
            append_to_master_csv(config['multi']['output_file'], df)

            # Move processed file
            shutil.move(csv_path, os.path.join(move_folder, csv_file))
            log_message('info', f"Processed '{csv_file}' and moved to '{move_folder}'.")

    except Exception as e:
        error_message = f"Error during multi-file processing: {e}"
        log_message('error', error_message)
        raise

def append_to_master_csv(master_file, df):
    """Append data to the master CSV file, rotating files based on the date."""
    try:
        if df.empty:
            log_message('info', "No data to append to the master CSV.")
            return  # No data to append

        master_dir = os.path.dirname(master_file)
        past_data_folder = os.path.join(master_dir, 'Past Data')
        os.makedirs(past_data_folder, exist_ok=True)

        # Rotate master file if it's a new day
        if os.path.exists(master_file):
            file_mod_time = datetime.fromtimestamp(os.path.getmtime(master_file))
            file_mod_date_str = file_mod_time.strftime('%Y-%m-%d')
            today_date = datetime.now().strftime('%Y-%m-%d')
            if file_mod_date_str != today_date:
                # Move old master file to Past Data folder
                new_file_name = f"{os.path.splitext(os.path.basename(master_file))[0]}_{file_mod_date_str}.csv"
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

def process_com_port(com_port_config, config_folder, status_callback=None, stop_event=None):
    """Process data from the COM port."""
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

    buffer = []
    last_data_time = datetime.now()
    data_lock = threading.Lock()

    def read_from_port():
        nonlocal last_data_time, ser
        while not stop_event.is_set():
            try:
                if ser and ser.is_open:
                    line = ser.readline().decode('utf-8', errors='replace').strip()
                    if line:
                        # Replace unknown characters (represented by '�') with "N/A"
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
                ser = None
                time.sleep(1)
                if stop_event.is_set():
                    break
                connect_to_port()

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
                    break  # Stop trying to reconnect; let the user decide
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

    ser = None
    connect_to_port()

    # Start reading thread
    reader_thread = threading.Thread(target=read_from_port, daemon=True)
    reader_thread.start()

    while not stop_event.is_set():
        time.sleep(1)
        data_to_process = []
        with data_lock:
            if buffer and (datetime.now() - last_data_time).total_seconds() > 2:
                # No new data for 2 seconds, process the buffer
                data_to_process = buffer.copy()
                buffer.clear()
        if data_to_process:
            # Process data_to_process
            # Shift vertical data sideways (transpose)
            try:
                # Split each line into cells
                rows = [line.split(',') for line in data_to_process]
                # Transpose the rows
                transposed_data = list(map(list, zip(*rows)))
                # Convert to CSV string
                csv_buffer = io.StringIO()
                csv_writer = csv.writer(csv_buffer)
                csv_writer.writerows(transposed_data)
                csv_buffer.seek(0)
                # Parse the data
                df = parse_csv(csv_buffer, config_folder)
                # Append to master CSV
                append_to_master_csv(output_file, df)
                message = f"Appended data to {output_file}"
                log_message('info', message)
                if status_callback:
                    status_callback.emit(message)
            except Exception as e:
                message = f"Error processing COM port data: {e}"
                log_message('error', message)
                if status_callback:
                    status_callback.emit(message)

    if ser and ser.is_open:
        ser.close()
    reader_thread.join()
