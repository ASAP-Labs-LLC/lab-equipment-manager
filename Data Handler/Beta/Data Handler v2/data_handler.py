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
from csv_parser import (
    parse_csv,
    safe_open,
    log_message,
    load_parser_config,
    update_parser_config_cache,
    invalidate_config_cache,
    acquire_file_lock,
)
from queue import Queue, Empty, Full
from concurrent.futures import ThreadPoolExecutor

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

_MASTER_WRITER_LOCK = threading.Lock()
_MULTI_EXECUTOR_LOCK = threading.Lock()
_MULTI_IN_FLIGHT_LOCK = threading.Lock()
_MULTI_IN_FLIGHT = set()
_MULTI_EXECUTOR: ThreadPoolExecutor | None = None
_MULTI_FAIL_COUNT_LOCK = threading.Lock()
_MULTI_FAIL_COUNT: dict[str, int] = {}
_MULTI_MAX_RETRIES = 5

class MasterCSVWriter:
    _queues: dict[str, Queue] = {}
    _threads: dict[str, threading.Thread] = {}

    @classmethod
    def enqueue(cls, master_file: str, df_new_chunk: pd.DataFrame, machine_name: str) -> None:
        normalized = os.path.normcase(os.path.abspath(master_file))
        with _MASTER_WRITER_LOCK:
            queue = cls._queues.get(normalized)
            if queue is None:
                queue = Queue(maxsize=100)
                cls._queues[normalized] = queue
                thread = threading.Thread(
                    target=cls._worker,
                    args=(normalized, queue),
                    name=f"master-writer-{os.path.basename(master_file)}",
                    daemon=True,
                )
                cls._threads[normalized] = thread
                thread.start()
        queue.put((df_new_chunk.copy(deep=True), machine_name))
        if queue.qsize() > int(queue.maxsize * 0.8):
            log_message('warning', f"Write backlog for '{master_file}' is {queue.qsize()}/{queue.maxsize}.")

    MAX_WRITE_RETRIES = 3

    @classmethod
    def _worker(cls, master_file: str, queue: Queue) -> None:
        while True:
            df_chunk, machine_name = queue.get()
            try:
                for attempt in range(1, cls.MAX_WRITE_RETRIES + 1):
                    try:
                        cls._write(master_file, df_chunk, machine_name)
                        break  # success
                    except Exception as exc:
                        if attempt >= cls.MAX_WRITE_RETRIES:
                            log_message('error',
                                f"Failed to append to '{master_file}' after "
                                f"{cls.MAX_WRITE_RETRIES} attempts: {exc}"
                            )
                        else:
                            log_message('warning',
                                f"Write attempt {attempt}/{cls.MAX_WRITE_RETRIES} "
                                f"failed for '{master_file}': {exc}. Retrying..."
                            )
                            time.sleep(attempt)  # escalating backoff: 1s, 2s
            finally:
                queue.task_done()

    @staticmethod
    def _write(master_file: str, df_chunk: pd.DataFrame, machine_name: str) -> None:
        lock = acquire_file_lock(master_file)
        with lock:
            master_dir = os.path.dirname(master_file)
            if master_dir:
                os.makedirs(master_dir, exist_ok=True)
            past_data_folder = os.path.join(master_dir, 'Past Data')
            os.makedirs(past_data_folder, exist_ok=True)

            file_exists = os.path.exists(master_file)
            write_header = not file_exists
            if file_exists:
                file_mod_time = datetime.fromtimestamp(os.path.getmtime(master_file))
                file_mod_date_str = file_mod_time.strftime('%Y-%m-%d')
                today_date = datetime.now().strftime('%Y-%m-%d')
                if file_mod_date_str != today_date:
                    base_name = os.path.splitext(os.path.basename(master_file))[0]
                    new_file_name = f"{base_name}_{file_mod_date_str}.csv"
                    new_file_path = os.path.join(past_data_folder, new_file_name)
                    shutil.move(master_file, new_file_path)
                    log_message('info', f"Rotated master CSV '{master_file}' -> '{new_file_path}'.")
                    write_header = True
                elif os.path.getsize(master_file) == 0:
                    write_header = True
                    log_message('debug', f"Existing master CSV '{master_file}' is empty; forcing header write.")

            # Record pre-write size for verification
            pre_size = os.path.getsize(master_file) if os.path.exists(master_file) else 0

            with safe_open(master_file, 'a', encoding='utf-8', errors='replace') as f:
                df_chunk.to_csv(f, header=write_header, index=False)
                f.flush()
                os.fsync(f.fileno())

            # Verify the file actually grew
            post_size = os.path.getsize(master_file)
            if post_size <= pre_size:
                raise IOError(
                    f"Write verification failed for '{master_file}': "
                    f"size did not increase ({pre_size} -> {post_size})"
                )

        log_message('info',
            f"Appended {len(df_chunk)} row(s) from {machine_name} to '{master_file}' "
            f"(+{post_size - pre_size} bytes, verified)."
        )


def _get_multi_executor() -> ThreadPoolExecutor:
    global _MULTI_EXECUTOR
    with _MULTI_EXECUTOR_LOCK:
        if _MULTI_EXECUTOR is None:
            workers = max(2, min(8, (os.cpu_count() or 4)))
            _MULTI_EXECUTOR = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="multi-csv")
    return _MULTI_EXECUTOR


def _mark_inflight(path: str) -> bool:
    normalized = os.path.normcase(os.path.abspath(path))
    with _MULTI_IN_FLIGHT_LOCK:
        if normalized in _MULTI_IN_FLIGHT:
            return False
        _MULTI_IN_FLIGHT.add(normalized)
        return True


def _release_inflight(path: str) -> None:
    normalized = os.path.normcase(os.path.abspath(path))
    with _MULTI_IN_FLIGHT_LOCK:
        _MULTI_IN_FLIGHT.discard(normalized)


def _save_config(config_folder: str, config: dict) -> None:
    """Write config to disk and update the in-memory cache atomically."""
    config_file = os.path.join(config_folder, 'parser_config.json')
    config_lock = acquire_file_lock(config_file)
    with config_lock:
        with safe_open(config_file, 'w') as f:
            json.dump(config, f, indent=4)
    update_parser_config_cache(config_folder, config)


def _process_multi_csv_file(csv_path: str, move_folder: str, config_folder: str) -> None:
    normalized = os.path.normcase(os.path.abspath(csv_path))
    try:
        config = load_parser_config(config_folder)
        multi_cfg = config.get('multi', {})
        delimiter = multi_cfg.get('delimiter', ',')
        has_header = multi_cfg.get('has_header', False)
        output_file = multi_cfg.get('output_file')
        if not output_file:
            log_message('warning', f"No 'output_file' configured for multi parser in '{config_folder}'.")
            return

        with safe_open(csv_path, 'r') as file:
            lines = file.readlines()

        if has_header and lines:
            lines = lines[1:]

        file_content = ''.join(lines)
        file_like = io.StringIO(file_content)

        df_new_chunk = parse_csv(file_like, config_folder, delimiter=delimiter)
        if df_new_chunk is None:
            log_message('info', f"parse_csv returned None for {os.path.basename(csv_path)}, user canceled Lab ID. Skipping move.")
            return

        machine_name = config.get('machine_name', 'Unknown')
        append_to_master_csv(output_file, df_new_chunk, machine_name)

        os.makedirs(move_folder, exist_ok=True)
        destination = os.path.join(move_folder, os.path.basename(csv_path))
        shutil.move(csv_path, destination)
        log_message('info', f"Processed '{os.path.basename(csv_path)}' and moved to '{move_folder}'.")

        # Clear failure count on success
        with _MULTI_FAIL_COUNT_LOCK:
            _MULTI_FAIL_COUNT.pop(normalized, None)

    except Exception as exc:
        # Track failures per file
        with _MULTI_FAIL_COUNT_LOCK:
            count = _MULTI_FAIL_COUNT.get(normalized, 0) + 1
            _MULTI_FAIL_COUNT[normalized] = count

        if count >= _MULTI_MAX_RETRIES:
            # Move to a "Failed" folder so it stops blocking the pipeline
            failed_folder = os.path.join(os.path.dirname(csv_path), 'Failed')
            try:
                os.makedirs(failed_folder, exist_ok=True)
                failed_dest = os.path.join(failed_folder, os.path.basename(csv_path))
                shutil.move(csv_path, failed_dest)
                log_message('error',
                    f"Multi CSV '{os.path.basename(csv_path)}' failed {count} times. "
                    f"Moved to '{failed_folder}' for manual review. Last error: {exc}"
                )
            except Exception as move_exc:
                log_message('error',
                    f"Failed to move problematic file '{csv_path}' to Failed folder: {move_exc}"
                )
            with _MULTI_FAIL_COUNT_LOCK:
                _MULTI_FAIL_COUNT.pop(normalized, None)
        else:
            log_message('warning',
                f"Error processing multi CSV '{csv_path}' "
                f"(attempt {count}/{_MULTI_MAX_RETRIES}): {exc}"
            )
    finally:
        _release_inflight(csv_path)


def process_single_csv(input_csv, output_csv, config_folder):
    try:
        config = load_parser_config(config_folder)
        single_cfg = config.get('single_csv', {})
        last_position = single_cfg.get('last_position', 0)
        with safe_open(input_csv, 'r') as file:
            file.seek(last_position)
            new_data = file.read()
            current_position = file.tell()

        if not new_data.strip():
            log_message('debug', f"No new data to process in '{input_csv}'.")
            return

        # ---- Parse BEFORE updating position ----
        # If parsing fails, position stays unchanged so we retry next cycle
        file_like = io.StringIO(new_data)
        df_new_chunk = parse_csv(file_like, config_folder, delimiter=single_cfg.get('delimiter', ','))
        if df_new_chunk is None:
            log_message('info', "parse_csv returned None (user canceled Lab ID?), skipping append.")
            return

        if df_new_chunk.empty:
            log_message('warning', f"parse_csv returned empty DataFrame for '{input_csv}'; not advancing file position.")
            return

        machine_name = config.get('machine_name', 'Unknown')
        append_to_master_csv(output_csv, df_new_chunk, machine_name)

        # ---- Only update position AFTER successful parse + enqueue ----
        # Re-read config to avoid overwriting concurrent changes from UI
        config = load_parser_config(config_folder)
        single_cfg = config.get('single_csv', {})
        single_cfg['last_position'] = current_position
        config['single_csv'] = single_cfg
        _save_config(config_folder, config)

        log_message('info', f"Processed {len(df_new_chunk)} row(s) from '{input_csv}' and appended to '{output_csv}'.")

    except Exception as e:
        error_message = f"Error during single file processing: {e}"
        log_message('error', error_message)
        raise

def process_multi_csv(input_folder, move_folder, output_folder, config_folder):
    try:
        if not os.path.isdir(input_folder):
            log_message('warning', f"Input folder '{input_folder}' does not exist.")
            return

        if output_folder:
            os.makedirs(output_folder, exist_ok=True)

        csv_files = [f for f in os.listdir(input_folder) if f.lower().endswith('.csv')]
        if not csv_files:
            return

        executor = _get_multi_executor()
        for csv_file in csv_files:
            csv_path = os.path.join(input_folder, csv_file)
            if not _mark_inflight(csv_path):
                continue
            log_message('debug', f"Queued '{csv_file}' for multi-CSV processing.")
            executor.submit(_process_multi_csv_file, csv_path, move_folder, config_folder)
    except Exception as e:
        error_message = f"Error during multi-file processing: {e}"
        log_message('error', error_message)
        raise

def append_to_master_csv(master_file, df_new_chunk, machine_name="Unknown"):
    """
    Schedule df_new_chunk to be appended to master_file using the shared writer.
    """
    if df_new_chunk is None or df_new_chunk.empty:
        log_message('debug', "No data to append to the master CSV.")
        return
    MasterCSVWriter.enqueue(master_file, df_new_chunk, machine_name)


def process_com_port(com_port_cfg, cfg_folder,
                     status_callback=None, stop_event=None):
    """
    Stream RS-232 data framed by STX/ETX, push each ticket to a print-queue,
    parse it immediately, **and keep listening** – no 2-second buffer.   """

    port = com_port_cfg['port']
    baudrate = int(com_port_cfg.get('baud_rate', 9600))

    parity_raw = str(com_port_cfg.get('parity', 'N')).strip().upper()
    parity_map = {
        'N': serial.PARITY_NONE,
        'E': serial.PARITY_EVEN,
        'O': serial.PARITY_ODD,
        'M': serial.PARITY_MARK,
        'S': serial.PARITY_SPACE,
    }
    if parity_raw not in parity_map:
        log_message('warning', f"Unsupported parity '{parity_raw}' for {port}; defaulting to 'N'.")
        parity_raw = 'N'
    parity_value = parity_map[parity_raw]

    stop_bits_raw = com_port_cfg.get('stop_bits', 1)
    try:
        stop_bits_numeric = float(stop_bits_raw)
    except (TypeError, ValueError):
        log_message('warning', f"Invalid stop bits '{stop_bits_raw}' for {port}; defaulting to 1.")
        stop_bits_numeric = 1.0
    stop_bits_map = {
        1.0: serial.STOPBITS_ONE,
        1.5: serial.STOPBITS_ONE_POINT_FIVE,
        2.0: serial.STOPBITS_TWO,
    }
    stop_bits_value = stop_bits_map.get(stop_bits_numeric)
    if stop_bits_value is None:
        log_message('warning', f"Unsupported stop bits '{stop_bits_numeric}' for {port}; defaulting to 1.")
        stop_bits_numeric = 1.0
        stop_bits_value = serial.STOPBITS_ONE

    byte_size_raw = com_port_cfg.get('byte_size', 8)
    try:
        byte_size_numeric = int(byte_size_raw)
    except (TypeError, ValueError):
        log_message('warning', f"Invalid byte size '{byte_size_raw}' for {port}; defaulting to 8.")
        byte_size_numeric = 8
    bytesize_map = {
        5: serial.FIVEBITS,
        6: serial.SIXBITS,
        7: serial.SEVENBITS,
        8: serial.EIGHTBITS,
    }
    byte_size_value = bytesize_map.get(byte_size_numeric)
    if byte_size_value is None:
        log_message('warning', f"Unsupported byte size '{byte_size_numeric}' for {port}; defaulting to 8.")
        byte_size_numeric = 8
        byte_size_value = serial.EIGHTBITS

    try:
        timeout = float(com_port_cfg.get('timeout', 0.2))
    except (TypeError, ValueError):
        log_message('warning', f"Invalid timeout '{com_port_cfg.get('timeout')}' for {port}; defaulting to 0.2.")
        timeout = 0.2

    try:
        idle_gap = float(com_port_cfg.get('idle_gap', 0.5))
    except (TypeError, ValueError):
        log_message('warning', f"Invalid idle_gap '{com_port_cfg.get('idle_gap')}' for {port}; defaulting to 0.5.")
        idle_gap = 0.5
    if idle_gap <= 0:
        log_message('warning', f"idle_gap <= 0 for {port}; defaulting to 0.5.")
        idle_gap = 0.5

    out_csv = com_port_cfg['output']
    machine = com_port_cfg.get('machine_name', 'Unnamed')

    if stop_event is None:
        log_message('warning', "process_com_port called without a stop_event; creating internal event.")
        stop_event = threading.Event()

    log_message(
        'info',
        (
            f"Opening {port}: {baudrate} baud, {byte_size_numeric} data bits, parity {parity_raw}, "
            f"{stop_bits_numeric} stop bit(s); idle gap {idle_gap}s."
        )
    )

    q = Queue(maxsize=200)            # producer = reader, consumers = parser workers
    default_workers = max(2, min(4, (os.cpu_count() or 2)))
    worker_count_raw = com_port_cfg.get('worker_threads', default_workers)
    try:
        worker_count = max(1, int(worker_count_raw))
    except (TypeError, ValueError):
        worker_count = default_workers

    def _ticket_to_single_row(raw_text: str) -> tuple[str, int]:
        lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
        if not lines:
            return '', 0
        buffer = io.StringIO()
        csv.writer(buffer).writerow(lines)
        return buffer.getvalue(), len(lines)

    def _enqueue_ticket(ticket_text: str, field_count: int) -> None:
        try:
            q.put(ticket_text, timeout=1.0)
        except Full:
            log_message(
                'warning',
                f"COM queue full for {port}; dropping frame with {field_count} field(s)."
            )

    # ---------- producer: read raw bytes, build framed records ----------
    def reader():
        try:
            with serial.Serial(port, baudrate, parity=parity_value,
                               stopbits=stop_bits_value, bytesize=byte_size_value,
                               timeout=timeout) as ser:
                ser.reset_input_buffer()
                if status_callback: status_callback.emit(f"Connected to {port}")
                log_message('info', f"Connected to {port}")

                frame = bytearray()
                in_frame = False
                last_activity = time.monotonic()

                while not stop_event.is_set():
                    chunk = ser.read(128)    # up to 128 bytes, returns b'' if timeout
                    now = time.monotonic()
                    if chunk:
                        last_activity = now
                        for b in chunk:
                            if b == CTRL_STX:
                                frame.clear()
                                in_frame = True
                            elif b == CTRL_ETX and in_frame:
                                _text = frame.decode('ascii', 'replace')
                                _text = _text.replace('\r', '').strip('\n')
                                if _text:
                                    formatted, field_count = _ticket_to_single_row(_text)
                                    if formatted:
                                        _enqueue_ticket(formatted, field_count)
                                        log_message(
                                            'info',
                                            f"Framed {field_count} fields from {port} via ETX."
                                        )
                                frame.clear()
                                in_frame = False
                            else:
                                if not in_frame and (b >= 32 or b in (CTRL_CR, CTRL_LF)):
                                    in_frame = True
                                if not in_frame:
                                    continue
                                if b in (CTRL_LF, CTRL_CR):
                                    frame.append(0x0A)
                                elif b >= 32:
                                    frame.append(b)
                                else:
                                    continue
                    else:
                        if in_frame and frame and (now - last_activity) >= idle_gap:
                            text = frame.decode('ascii', 'replace').replace('\r', '').strip('\n')
                            if text:
                                formatted, field_count = _ticket_to_single_row(text)
                                if formatted:
                                    _enqueue_ticket(formatted, field_count)
                                    log_message(
                                        'info',
                                        f"Framed {field_count} fields from {port} via idle gap."
                                    )
                            frame.clear()
                            in_frame = False
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
    reader_thread = threading.Thread(
        target=reader,
        name=f"com-reader-{port}",
        daemon=True,
    )
    reader_thread.start()

    consumers = []
    for idx in range(worker_count):
        t = threading.Thread(
            target=consumer,
            name=f"com-consumer-{port}-{idx}",
            daemon=True,
        )
        t.start()
        consumers.append(t)
    log_message('debug', f"Spawned {worker_count} COM consumer thread(s) for {port}.")

