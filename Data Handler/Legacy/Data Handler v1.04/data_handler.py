# data_handler.py
import os
import time
import io
import json
import pandas as pd
import threading
import csv
import shutil
import serial
from datetime import datetime
from csv_parser import parse_csv

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
    """Append data to the master CSV file, moving old files to 'Past Data' if needed."""
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
        df.to_csv(master_file, mode='a', header=write_header, index=False, encoding='utf-8')
    except Exception as e:
        error_message = f"ERROR: Issue writing to the master CSV file '{master_file}': {e}"
        safe_error_message = error_message.encode('ascii', errors='replace').decode('ascii')
        print(safe_error_message)
        raise

def process_com_port(com_port_config, config_folder, status_callback=None):
    """Process data from the COM port."""
    port = com_port_config.get('port')
    output_file = com_port_config.get('output')

    if not port:
        message = "COM port not specified in configuration."
        print(message)
        if status_callback:
            status_callback.emit(message)
        return

    stop_event = threading.Event()
    buffer = []
    last_data_time = time.time()
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
                            last_data_time = time.time()
                    else:
                        time.sleep(0.1)
                else:
                    time.sleep(1)
            except Exception as e:
                message = f"Error reading from COM port: {e}"
                safe_message = message.encode('ascii', errors='replace').decode('ascii')
                print(safe_message)
                if status_callback:
                    status_callback.emit(safe_message)
                ser = None
                time.sleep(1)
                connect_to_port()

    def connect_to_port():
        nonlocal ser
        while not stop_event.is_set():
            try:
                ser = serial.Serial(port=port, baudrate=9600, timeout=1)
                message = f"Connected to {port}"
                print(message)
                if status_callback:
                    status_callback.emit(message)
                break
            except Exception as e:
                message = f"Failed to connect to {port}: {e}"
                safe_message = message.encode('ascii', errors='replace').decode('ascii')
                print(safe_message)
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
            if buffer and (time.time() - last_data_time > 2):
                # No new data for 2 seconds, process the buffer
                data_to_process = buffer.copy()
                buffer.clear()
        if data_to_process:
            # Process data_to_process
            # Shift vertical data sideways (transpose)
            # Assuming data_to_process is a list of strings
            # Split each line into cells
            rows = [line.split(',') for line in data_to_process]
            # Transpose the rows
            try:
                transposed_data = list(map(list, zip(*rows)))
            except Exception as e:
                message = f"Error transposing data: {e}"
                safe_message = message.encode('ascii', errors='replace').decode('ascii')
                print(safe_message)
                if status_callback:
                    status_callback.emit(safe_message)
                continue
            # Convert to CSV string
            csv_buffer = io.StringIO()
            csv_writer = csv.writer(csv_buffer)
            csv_writer.writerows(transposed_data)
            csv_buffer.seek(0)
            # Parse the data
            try:
                df = parse_csv(csv_buffer, config_folder)
                # Append to master CSV
                append_to_master_csv(output_file, df)
                message = f"Appended data to {output_file}"
                print(message)
                if status_callback:
                    status_callback.emit(message)
            except Exception as e:
                message = f"Error processing COM port data: {e}"
                safe_message = message.encode('ascii', errors='replace').decode('ascii')
                print(safe_message)
                if status_callback:
                    status_callback.emit(safe_message)

    if ser and ser.is_open:
        ser.close()
    reader_thread.join()