import os
import io
import json
import csv
import shutil
import time
import serial
import logging
from datetime import datetime
from .config_manager import ConfigManager
from .parser_actions import parse_csv

LOG = logging.getLogger(__name__)

def process_single_csv(input_csv, output_csv, config_folder):
    """
    Process a single CSV file incrementally.
    Reads new data from input_csv based on last_position,
    parses it, then appends parsed data to output_csv.
    """
    try:
        config_file = os.path.join(config_folder, 'parser_config.json')
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)

        last_position = config.get('single_csv', {}).get('last_position', 0)
        with open(input_csv, 'r', encoding='utf-8') as infile:
            infile.seek(last_position)
            new_data = infile.read()
            current_position = infile.tell()

        if not new_data.strip():
            LOG.info(f"No new data to process in '{input_csv}'.")
            return  # No new data

        # Update last_position in the config
        config['single_csv']['last_position'] = current_position
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)

        file_like = io.StringIO(new_data)
        df = parse_csv(file_like, config_folder, delimiter=config['single_csv'].get('delimiter', ','), config=config)

        append_to_master_csv(output_csv, df)
        LOG.info(f"Processed new data from '{input_csv}' and appended to '{output_csv}'.")

    except Exception as e:
        LOG.error(f"Error during single file processing: {e}", exc_info=True)
        raise

def process_multi_csv(input_folder, move_folder, output_folder, config_folder):
    """
    Process multiple CSV files in the input_folder.
    After parsing, each processed file is moved to move_folder.
    Parsed data is appended to output_folder's master CSV file.
    """
    try:
        config_file = os.path.join(config_folder, 'parser_config.json')
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)

        csv_files = [f for f in os.listdir(input_folder) if f.lower().endswith('.csv')]
        if not csv_files:
            LOG.info(f"No CSV files found in '{input_folder}' to process.")
            return

        has_header = config.get('multi', {}).get('has_header', False)
        master_file = config['multi']['output_file']
        delimiter = config['multi'].get('delimiter', ',')

        for csv_file in csv_files:
            csv_path = os.path.join(input_folder, csv_file)
            with open(csv_path, 'r', encoding='utf-8') as infile:
                lines = infile.readlines()
                if has_header and lines:
                    lines = lines[1:]  # Skip header if present
                file_content = ''.join(lines)
                file_like = io.StringIO(file_content)

            df = parse_csv(file_like, config_folder, delimiter=delimiter, config=config)
            append_to_master_csv(master_file, df)

            # Move processed file
            shutil.move(csv_path, os.path.join(move_folder, csv_file))
            LOG.info(f"Processed '{csv_file}' and moved to '{move_folder}'.")

    except Exception as e:
        LOG.error(f"Error during multi-file processing: {e}", exc_info=True)
        raise

def process_com_port(com_port_config, config_folder, status_callback=None, stop_event=None):
    """
    Read data from a COM port, accumulate it until idle, then parse and append to output CSV.
    If access is denied or another error occurs, it logs and optionally notifies via status_callback.
    """
    import threading

    port = com_port_config.get('port')
    baud_rate = int(com_port_config.get('baud_rate', 9600))
    parity = com_port_config.get('parity', 'N')
    stop_bits = com_port_config.get('stop_bits', 1)
    byte_size = com_port_config.get('byte_size', 8)
    timeout = com_port_config.get('timeout', 1)
    output_file = com_port_config.get('output')

    if not port:
        message = "COM port not specified in configuration."
        LOG.error(message)
        if status_callback:
            status_callback.emit(message)
        return

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
                LOG.info(message)
                if status_callback:
                    status_callback.emit(message)
                break
            except serial.SerialException as e:
                err_str = str(e)
                if 'Access is denied' in err_str or 'could not open port' in err_str:
                    message = f"Access denied to {port}: {e}"
                    LOG.error(message)
                    if status_callback:
                        status_callback.emit('ACCESS_DENIED')
                    break  # Stop trying; user must handle this
                else:
                    message = f"Failed to connect to {port}: {e}"
                    LOG.error(message)
                    if status_callback:
                        status_callback.emit(message)
                    time.sleep(1)
            except Exception as e:
                message = f"Unexpected error connecting to {port}: {e}"
                LOG.error(message, exc_info=True)
                if status_callback:
                    status_callback.emit(message)
                time.sleep(1)

    def read_from_port():
        nonlocal last_data_time
        while not stop_event.is_set():
            if ser and ser.is_open:
                try:
                    line = ser.readline().decode('utf-8', errors='replace').strip()
                    if line:
                        # Replace unknown chars with N/A
                        line = line.replace('�', 'N/A')
                        with data_lock:
                            buffer.append(line)
                            last_data_time = datetime.now()
                    else:
                        time.sleep(0.1)
                except Exception as e:
                    message = f"Error reading from COM port: {e}"
                    LOG.error(message, exc_info=True)
                    if status_callback:
                        status_callback.emit(message)
                    ser = None
                    time.sleep(1)
                    if stop_event.is_set():
                        break
                    connect_to_port()
            else:
                time.sleep(1)

    ser = None
    connect_to_port()
    if not ser or not ser.is_open:
        return  # Could not connect

    reader_thread = threading.Thread(target=read_from_port, daemon=True)
    reader_thread.start()

    while not stop_event.is_set():
        time.sleep(1)
        data_to_process = []
        with data_lock:
            if buffer and (datetime.now() - last_data_time).total_seconds() > 2:
                # No new data for 2 seconds, process buffer
                data_to_process = buffer.copy()
                buffer.clear()

        if data_to_process:
            try:
                # Rotate vertical data to horizontal by transposing
                rows = [line.split(',') for line in data_to_process]
                transposed_data = list(map(list, zip(*rows)))
                csv_buffer = io.StringIO()
                csv_writer = csv.writer(csv_buffer)
                csv_writer.writerows(transposed_data)
                csv_buffer.seek(0)
                df = parse_csv(csv_buffer, config_folder, config=com_port_config)
                append_to_master_csv(output_file, df)
                message = f"Appended data to {output_file}"
                LOG.info(message)
                if status_callback:
                    status_callback.emit(message)
            except Exception as e:
                message = f"Error processing COM port data: {e}"
                LOG.error(message, exc_info=True)
                if status_callback:
                    status_callback.emit(message)

    if ser and ser.is_open:
        ser.close()
    reader_thread.join(timeout=5)

def append_to_master_csv(master_file, df):
    """
    Append data from df to a master CSV file, rotating the master file daily.
    """
    if df.empty:
        LOG.info("No data to append to the master CSV.")
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
            base_name = os.path.splitext(os.path.basename(master_file))[0]
            new_file_name = f"{base_name}_{file_mod_date_str}.csv"
            new_file_path = os.path.join(past_data_folder, new_file_name)
            shutil.move(master_file, new_file_path)
            LOG.info(f"Rotated master CSV file to '{new_file_path}'.")

    write_header = not os.path.exists(master_file)
    df.to_csv(master_file, mode='a', header=write_header, index=False, encoding='utf-8')
    LOG.info(f"Appended data to master CSV '{master_file}'.")
