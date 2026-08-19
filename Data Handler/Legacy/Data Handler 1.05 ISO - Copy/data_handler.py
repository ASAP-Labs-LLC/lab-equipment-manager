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
from iso_uncertainty import check_iso_uncertainty
from queue import Queue, Empty

CTRL_STX = 0x02   # 2
CTRL_ETX = 0x03   # 3
CTRL_LF  = 0x0A   # 10
CTRL_CR  = 0x0D   # 13

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

def process_com_port(com_port_cfg, cfg_folder,
                     status_callback=None, stop_event=None):
    """
    Stream RS-232 data framed by STX/ETX, push each ticket to a print-queue,
    parse it immediately, **and keep listening** – no 2-second buffer.   """

    port       = com_port_cfg['port']
    baudrate   = int(com_port_cfg.get('baud_rate', 9600))
    parity     = com_port_cfg.get('parity', 'N')
    stop_bits  = com_port_cfg.get('stop_bits', 1)
    byte_size  = com_port_cfg.get('byte_size', 8)
    timeout    = com_port_cfg.get('timeout', 0.2)      # short, non-blocking
    out_csv    = com_port_cfg['output']
    machine    = com_port_cfg.get('machine_name', 'Unnamed')

    q = Queue(maxsize=50)            # producer = reader, consumer = parser

    # ---------- producer: read raw bytes, build framed records ----------
    def reader():
        try:
            with serial.Serial(port, baudrate, parity=parity,
                               stopbits=stop_bits, bytesize=byte_size,
                               timeout=timeout) as ser:
                if status_callback: status_callback.emit(f"Connected to {port}")
                log_message('info', f"Connected to {port}")

                frame = bytearray()
                in_frame = False

                while not stop_event.is_set():
                    chunk = ser.read(128)    # up to 128 bytes, returns b'' if timeout
                    if not chunk:
                        continue

                    for b in chunk:
                        if b == CTRL_STX:
                            frame.clear(); in_frame = True
                        elif b == CTRL_ETX and in_frame:
                            q.put(frame.decode('ascii', 'replace'))
                            in_frame = False
                        elif in_frame:
                            # keep LF/CR as real line breaks, drop other ctrls
                            if b in (CTRL_LF, CTRL_CR):
                                frame.append(0x0A)      # normal newline
                            elif b >= 32:               # printable
                                frame.append(b)
                            # else ignore control padding
        except serial.SerialException as e:
            msg = f"Serial error on {port}: {e}"
            log_message('error', msg)
            if status_callback: status_callback.emit(msg)

    # ---------- consumer: dequeue, parse, append ----------
    def consumer():
        while not stop_event.is_set():
            try:
                ticket = q.get(timeout=0.5)
            except Empty:
                continue
            try:
                # ticket is already clean CSV text (may be multi-line)
                csv_buf = io.StringIO(ticket)
                df = parse_csv(csv_buf, cfg_folder)
                if df is not None and not df.empty:
                    append_to_master_csv(out_csv, df, machine)
                    msg = f"Appended {len(df)} rows from {machine}"
                    log_message('info', msg)
                    if status_callback: status_callback.emit(msg)
            except Exception as e:
                log_message('error', f"COM parse error: {e}")
            finally:
                q.task_done()

    # spin threads
    threading.Thread(target=reader,   daemon=True).start()
    threading.Thread(target=consumer, daemon=True).start()