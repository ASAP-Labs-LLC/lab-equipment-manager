# data_handler.py
import os
import time
import io
import json
import pandas as pd
import threading
import csv
import shutil
from datetime import datetime
from csv_parser import parse_csv  # Ensure csv_parser.py does not import from data_handler.py
import serial
from utils import generate_default_headers, load_json_config, save_json_config

def process_single_csv(input_csv, output_csv, config_folder):
    """Process a single CSV file."""
    try:
        if not os.path.exists(input_csv):
            raise FileNotFoundError(f"Input CSV file does not exist: {input_csv}")
        
        # Read existing data if output_csv exists
        if os.path.exists(output_csv):
            df_existing = pd.read_csv(output_csv, encoding='utf-8', on_bad_lines='skip')
            last_position = df_existing.shape[0]
        else:
            df_existing = pd.DataFrame()
            last_position = 0
        
        # Read new data
        with open(input_csv, 'r', encoding='utf-8', errors='replace') as f:
            reader = csv.reader(f, delimiter=',')  # Adjust delimiter as needed
            headers = next(reader, None)
            if not headers:
                headers = generate_default_headers()
            for _ in range(last_position):
                next(reader, None)  # Skip already processed rows
            new_rows = list(reader)
        
        if new_rows:
            df_new = pd.DataFrame(new_rows, columns=headers if headers else generate_default_headers())
            # Apply data actions here if any
            df_parsed = parse_csv(df_new, config_folder)
            # Append to master CSV
            append_to_master_csv(output_csv, df_parsed)
            print(f"Appended {len(df_parsed)} rows to {output_csv}")
    except Exception as e:
        error_message = f"ERROR: Issue processing single CSV '{input_csv}': {e}"
        safe_error_message = error_message.encode('ascii', errors='replace').decode('ascii')
        print(safe_error_message)
        raise

def process_multi_csv(input_folder, move_folder, output_folder, config_folder):
    """Process multiple CSV files in a folder."""
    try:
        if not os.path.exists(input_folder):
            raise FileNotFoundError(f"Input folder does not exist: {input_folder}")
        if not os.path.exists(move_folder):
            os.makedirs(move_folder, exist_ok=True)
        if not os.path.exists(output_folder):
            os.makedirs(output_folder, exist_ok=True)
        
        csv_files = [f for f in os.listdir(input_folder) if f.endswith('.csv')]
        for csv_file in csv_files:
            input_csv = os.path.join(input_folder, csv_file)
            output_csv = os.path.join(output_folder, csv_file)
            process_single_csv(input_csv, output_csv, config_folder)
            # Move the processed file
            shutil.move(input_csv, os.path.join(move_folder, csv_file))
            print(f"Moved processed file to {move_folder}")
    except Exception as e:
        error_message = f"ERROR: Issue processing multiple CSVs in '{input_folder}': {e}"
        safe_error_message = error_message.encode('ascii', errors='replace').decode('ascii')
        print(safe_error_message)
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
        df.to_csv(master_file, mode='a', header=write_header, index=False, encoding='utf-8', errors='replace')
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
