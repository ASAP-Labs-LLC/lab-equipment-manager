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
from csv_parser import parse_csv, safe_open, log_message
from iso_uncertainty import check_iso_uncertainty  # NEW import, updated usage

CONFIG_DIR = os.path.join(os.path.expanduser('~'), 'csv_parser_configs')
LOG_FILE = os.path.join(CONFIG_DIR, 'parser.log')

LOG_LOCK = threading.Lock()
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(filename)s - %(funcName)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def process_single_csv(input_csv, output_csv, config_folder):
    try:
        config_file = os.path.join(config_folder, 'parser_config.json')
        with safe_open(config_file, 'r') as f:
            config = json.load(f)

        last_position = config['single_csv'].get('last_position', 0)
        with safe_open(input_csv, 'r') as file:
            file.seek(last_position)
            new_data = file.read()
            current_position = file.tell()

        if not new_data.strip():
            log_message('info', f"No new data to process in '{input_csv}'.")
            return

        config['single_csv']['last_position'] = current_position
        with safe_open(config_file, 'w') as f:
            json.dump(config, f, indent=4)

        file_like = io.StringIO(new_data)
        df_new_chunk = parse_csv(file_like, config_folder, delimiter=config['single_csv'].get('delimiter', ','))
        if df_new_chunk is None:
            log_message('info', "parse_csv returned None (user canceled Lab ID?), skipping append.")
            return

        machine_name = config.get('machine_name', 'Unknown')
        append_to_master_csv(output_csv, df_new_chunk, machine_name)
        log_message('info', f"Processed new data from '{input_csv}' and appended to '{output_csv}'.")

    except Exception as e:
        error_message = f"Error during single file processing: {e}"
        log_message('error', error_message)
        raise

def process_multi_csv(input_folder, move_folder, output_folder, config_folder):
    try:
        config_file = os.path.join(config_folder, 'parser_config.json')
        with safe_open(config_file, 'r') as f:
            config = json.load(f)

        csv_files = [f for f in os.listdir(input_folder) if f.lower().endswith('.csv')]
        if not csv_files:
            return

        has_header = config['multi'].get('has_header', False)

        for csv_file in csv_files:
            csv_path = os.path.join(input_folder, csv_file)
            with safe_open(csv_path, 'r') as file:
                lines = file.readlines()

            if has_header and len(lines) > 0:
                lines = lines[1:]

            file_content = ''.join(lines)
            file_like = io.StringIO(file_content)

            df_new_chunk = parse_csv(file_like, config_folder, delimiter=config['multi'].get('delimiter', ','))
            if df_new_chunk is None:
                log_message('info', f"parse_csv returned None for {csv_file}, user canceled Lab ID. Skipping move.")
                continue

            machine_name = config.get('machine_name', 'Unknown')
            append_to_master_csv(config['multi']['output_file'], df_new_chunk, machine_name)

            shutil.move(csv_path, os.path.join(move_folder, csv_file))
            log_message('info', f"Processed '{csv_file}' and moved to '{move_folder}'.")

    except Exception as e:
        error_message = f"Error during multi-file processing: {e}"
        log_message('error', error_message)
        raise

def append_to_master_csv(master_file, df_new_chunk, machine_name="Unknown"):
    """
    Appends df_new_chunk to the master_file, rotating daily. Then calls check_iso_uncertainty
    but only passing the df_new_chunk to avoid re-copying old rows.
    """
    try:
        if df_new_chunk.empty:
            log_message('info', "No data to append to the master CSV.")
            return

        master_dir = os.path.dirname(master_file)
        past_data_folder = os.path.join(master_dir, 'Past Data')
        os.makedirs(past_data_folder, exist_ok=True)

        # Rotate if it's a new day
        if os.path.exists(master_file):
            file_mod_time = datetime.fromtimestamp(os.path.getmtime(master_file))
            file_mod_date_str = file_mod_time.strftime('%Y-%m-%d')
            today_date = datetime.now().strftime('%Y-%m-%d')
            if file_mod_date_str != today_date:
                base_name = os.path.splitext(os.path.basename(master_file))[0]
                new_file_name = f"{base_name}_{file_mod_date_str}.csv"
                new_file_path = os.path.join(past_data_folder, new_file_name)
                shutil.move(master_file, new_file_path)
                log_message('info', f"Rotated master CSV file to '{new_file_path}'.")

        write_header = not os.path.exists(master_file)
        with safe_open(master_file, 'a', encoding='utf-8', errors='replace') as f:
            df_new_chunk.to_csv(f, header=write_header, index=False)

        log_message('info', f"Appended data to master CSV '{master_file}'.")

        # Now, only pass the newly appended chunk to iso_uncertainty, so it won't copy duplicates
        from iso_uncertainty import check_iso_uncertainty
        check_iso_uncertainty(df_new_chunk, machine_name)

    except Exception as e:
        error_message = f"Error writing to master CSV '{master_file}': {e}"
        log_message('error', error_message)
        raise

def process_com_port(com_port_config, config_folder, status_callback=None, stop_event=None):
    """
    Process data from the COM port. We read lines until 2 seconds of inactivity,
    then treat them all as one record by concatenating them into a single CSV row.
    Then we parse that row, append to the master CSV, etc.
    """
    port = com_port_config.get('port')
    baud_rate = int(com_port_config.get('baud_rate', 9600))
    parity = com_port_config.get('parity', 'N')
    stop_bits = com_port_config.get('stop_bits', 1)
    byte_size = com_port_config.get('byte_size', 8)
    timeout = com_port_config.get('timeout', 1)
    output_file = com_port_config.get('output')
    machine_name = com_port_config.get('machine_name', 'Unnamed')

    if not port:
        message = "COM port not specified in configuration."
        log_message('error', message)
        if status_callback:
            status_callback.emit(message)
        return

    buffer = []
    last_data_time = datetime.now()
    data_lock = threading.Lock()
    ser = None

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
                msg = f"Connected to {port}"
                log_message('info', msg)
                if status_callback:
                    status_callback.emit(msg)
                break
            except serial.SerialException as e:
                if 'Access is denied' in str(e) or 'could not open port' in str(e):
                    msg = f"Access denied to {port}: {e}"
                    log_message('error', msg)
                    if status_callback:
                        status_callback.emit('ACCESS_DENIED')
                    break  # stop trying
                else:
                    msg = f"Failed to connect to {port}: {e}"
                    log_message('error', msg)
                    if status_callback:
                        status_callback.emit(msg)
                    time.sleep(1)
            except Exception as e:
                msg = f"Unexpected error connecting to {port}: {e}"
                log_message('error', msg)
                if status_callback:
                    status_callback.emit(msg)
                time.sleep(1)

    def read_from_port():
        nonlocal last_data_time, ser
        while not stop_event.is_set():
            try:
                if ser and ser.is_open:
                    line = ser.readline().decode('utf-8', errors='replace').strip()
                    if line:
                        with data_lock:
                            buffer.append(line)
                            last_data_time = datetime.now()
                    else:
                        time.sleep(0.1)
                else:
                    time.sleep(1)
            except Exception as e:
                msg = f"Error reading from COM port: {e}"
                log_message('error', msg)
                if status_callback:
                    status_callback.emit(msg)
                ser = None
                time.sleep(1)
                if stop_event.is_set():
                    break
                connect_to_port()

    # Try connecting initially
    connect_to_port()

    # Start a thread to read lines from the port continuously
    reader_thread = threading.Thread(target=read_from_port, daemon=True)
    reader_thread.start()

    # Main loop: every second, check if we have a chunk of data that’s 2 seconds old
    while not stop_event.is_set():
        time.sleep(1)
        data_to_process = []
        with data_lock:
            if buffer and (datetime.now() - last_data_time).total_seconds() > 2:
                # We consider that chunk complete
                data_to_process = buffer.copy()
                buffer.clear()

        if data_to_process:
            try:
                # Instead of transposing, we combine all lines into one CSV row
                all_cells = []
                for line in data_to_process:
                    parts = line.split(',')
                    all_cells.extend(parts)

                # Make a single row
                single_row_data = [all_cells]  # 2D list with one row

                # Convert to CSV text
                import io
                csv_buffer = io.StringIO()
                csv_writer = csv.writer(csv_buffer)
                csv_writer.writerows(single_row_data)
                csv_buffer.seek(0)

                # Parse
                df_new_chunk = parse_csv(csv_buffer, config_folder)
                if df_new_chunk is None:
                    log_message('info', "User canceled Lab ID for COM data, skipping append.")
                    continue

                # Append to master CSV
                from data_handler import append_to_master_csv
                append_to_master_csv(output_file, df_new_chunk, machine_name)

                msg = f"Appended data to {output_file}"
                log_message('info', msg)
                if status_callback:
                    status_callback.emit(msg)

            except Exception as e:
                msg = f"Error processing COM port data: {e}"
                log_message('error', msg)
                if status_callback:
                    status_callback.emit(msg)

    # Cleanup
    if ser and ser.is_open:
        ser.close()
    reader_thread.join()
