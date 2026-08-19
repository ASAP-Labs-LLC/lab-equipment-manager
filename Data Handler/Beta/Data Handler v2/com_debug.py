"""
Lightweight COM/RS-232 debug viewer.

Reads the existing csv_parser_configs/<machine>/parser_config.json to pull the COM
settings, opens the port using the same framing logic (STX..ETX with CR/LF handled),
and streams the captured frames to a tiny PyQt5 UI while mirroring debug messages
to the terminal.
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime

import serial
from PyQt5 import QtCore, QtWidgets


CTRL_STX = 0x02
CTRL_ETX = 0x03
CTRL_LF = 0x0A
CTRL_CR = 0x0D


log = logging.getLogger("com_debug")


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def discover_com_configs(config_dir: str):
    if not os.path.isdir(config_dir):
        raise FileNotFoundError(f"Config directory '{config_dir}' does not exist.")

    candidates = []
    for entry in os.scandir(config_dir):
        if not entry.is_dir():
            continue
        config_path = os.path.join(entry.path, "parser_config.json")
        if not os.path.isfile(config_path):
            continue
        try:
            with open(config_path, "r", encoding="utf-8") as handle:
                cfg = json.load(handle)
        except Exception as exc:
            log.warning("Skipping %s: unable to read config (%s)", entry.name, exc)
            continue
        if cfg.get("parser_type") != "COM":
            continue
        candidates.append((entry.name, config_path, cfg))
    return candidates


def resolve_com_config(config_dir: str, machine: str | None):
    candidates = discover_com_configs(config_dir)
    if not candidates:
        raise RuntimeError(
            f"No COM parser configurations found under '{config_dir}'."
        )

    if machine:
        for machine_name, path, cfg in candidates:
            if machine_name.lower() == machine.lower():
                log.info("Using configuration for machine '%s' (%s)", machine_name, path)
                return cfg
        raise RuntimeError(
            f"Machine '{machine}' not found. Available: "
            + ", ".join(name for name, _, _ in candidates)
        )

    machine_name, path, cfg = candidates[0]
    log.info(
        "Machine not specified; defaulting to first COM config '%s' (%s)",
        machine_name,
        path,
    )
    return cfg


def map_serial_settings(com_cfg: dict):
    port = com_cfg["port"]
    baud = int(com_cfg.get("baud_rate", 9600))

    parity_raw = str(com_cfg.get("parity", "N")).strip().upper() or "N"
    parity_map = {
        "N": serial.PARITY_NONE,
        "E": serial.PARITY_EVEN,
        "O": serial.PARITY_ODD,
        "M": serial.PARITY_MARK,
        "S": serial.PARITY_SPACE,
    }
    if parity_raw not in parity_map:
        log.warning("Unsupported parity '%s'; defaulting to 'N'.", parity_raw)
        parity_raw = "N"
    parity_value = parity_map[parity_raw]

    stop_bits_raw = com_cfg.get("stop_bits", 1)
    try:
        stop_bits_numeric = float(stop_bits_raw)
    except (TypeError, ValueError):
        log.warning("Invalid stop bits '%s'; defaulting to 1.", stop_bits_raw)
        stop_bits_numeric = 1.0

    stop_bits_map = {
        1.0: serial.STOPBITS_ONE,
        1.5: serial.STOPBITS_ONE_POINT_FIVE,
        2.0: serial.STOPBITS_TWO,
    }
    stop_bits_value = stop_bits_map.get(stop_bits_numeric)
    if stop_bits_value is None:
        log.warning("Unsupported stop bits '%s'; defaulting to 1.", stop_bits_numeric)
        stop_bits_numeric = 1.0
        stop_bits_value = serial.STOPBITS_ONE

    byte_size_raw = com_cfg.get("byte_size", 8)
    try:
        byte_size_numeric = int(byte_size_raw)
    except (TypeError, ValueError):
        log.warning("Invalid byte size '%s'; defaulting to 8.", byte_size_raw)
        byte_size_numeric = 8

    bytesize_map = {
        5: serial.FIVEBITS,
        6: serial.SIXBITS,
        7: serial.SEVENBITS,
        8: serial.EIGHTBITS,
    }
    byte_size_value = bytesize_map.get(byte_size_numeric)
    if byte_size_value is None:
        log.warning("Unsupported byte size '%s'; defaulting to 8.", byte_size_numeric)
        byte_size_numeric = 8
        byte_size_value = serial.EIGHTBITS

    try:
        timeout = float(com_cfg.get("timeout", 0.2))
    except (TypeError, ValueError):
        log.warning("Invalid timeout '%s'; defaulting to 0.2s.", com_cfg.get("timeout"))
        timeout = 0.2

    summary = (
        f"{baud} baud, {byte_size_numeric} data bits, "
        f"parity {parity_raw}, {stop_bits_numeric} stop bit(s), timeout {timeout}s"
    )

    return {
        "port": port,
        "baudrate": baud,
        "parity": parity_value,
        "stopbits": stop_bits_value,
        "bytesize": byte_size_value,
        "timeout": timeout,
        "summary": summary,
    }


class SerialReader(QtCore.QThread):
    data_received = QtCore.pyqtSignal(str)
    status_update = QtCore.pyqtSignal(str)
    error_signal = QtCore.pyqtSignal(str)

    def __init__(self, com_cfg: dict, parent=None, idle_gap: float = 0.5):
        super().__init__(parent)
        self.com_cfg = com_cfg
        self.idle_gap = idle_gap
        self._stop_event = threading.Event()

    def run(self):
        serial_params = map_serial_settings(self.com_cfg)
        port = serial_params.pop("port")
        summary = serial_params.pop("summary")
        self.status_update.emit(f"Opening {port}: {summary}")
        log.info("Opening %s with settings: %s", port, summary)

        try:
            with serial.Serial(port=port, **serial_params) as ser:
                ser.reset_input_buffer()
                self.status_update.emit(f"Connected to {port}")
                log.info("Connected to %s", port)
                frame = bytearray()
                in_frame = False
                last_activity = time.monotonic()

                while not self._stop_event.is_set():
                    chunk = ser.read(128)
                    now = time.monotonic()

                    if chunk:
                        last_activity = now
                        hex_dump = " ".join(f"{b:02X}" for b in chunk)
                        ascii_preview = "".join(
                            chr(b) if 32 <= b <= 126 else "." for b in chunk
                        )
                        log.debug("Chunk received (hex): %s", hex_dump)
                        self.data_received.emit(
                            f"[RAW] {hex_dump} | {ascii_preview}"
                        )

                        for raw_byte in chunk:
                            if raw_byte == CTRL_STX:
                                frame.clear()
                                in_frame = True
                                log.debug("STX detected, starting new frame.")
                            elif raw_byte == CTRL_ETX and in_frame:
                                self._emit_frame(frame)
                                frame.clear()
                                in_frame = False
                            else:
                                if not in_frame and raw_byte >= 32:
                                    in_frame = True
                                if not in_frame:
                                    continue
                                if raw_byte in (CTRL_LF, CTRL_CR):
                                    frame.append(CTRL_LF)
                                elif raw_byte >= 32:
                                    frame.append(raw_byte)
                                else:
                                    log.debug("Ignored control byte: 0x%02X", raw_byte)
                    else:
                        if in_frame and frame and (now - last_activity) >= self.idle_gap:
                            log.debug(
                                "Idle gap %.2fs reached, flushing buffered frame.",
                                now - last_activity,
                            )
                            self._emit_frame(frame)
                            frame.clear()
                            in_frame = False

        except serial.SerialException as exc:
            error_msg = f"Serial error: {exc}"
            self.error_signal.emit(error_msg)
            log.error(error_msg)
        except Exception as exc:  # pragma: no cover - defensive
            error_msg = f"Unexpected error: {exc}"
            self.error_signal.emit(error_msg)
            log.exception(error_msg)
        finally:
            self.status_update.emit("Disconnected.")
            log.info("Serial reader shut down.")

    def stop(self):
        self._stop_event.set()
        self.wait(2000)

    def _emit_frame(self, frame: bytearray):
        decoded = frame.decode("ascii", errors="replace").strip()
        if not decoded:
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        message = f"[{timestamp}] {decoded}"
        self.data_received.emit(message)
        log.info("Frame received (%d bytes)", len(frame))


class DebugWindow(QtWidgets.QWidget):
    def __init__(self, machine_name: str, com_cfg: dict):
        super().__init__()
        self.setWindowTitle(f"COM Debug - {machine_name}")
        self.resize(720, 480)

        layout = QtWidgets.QVBoxLayout(self)

        self.status_label = QtWidgets.QLabel("Starting…")
        self.text_output = QtWidgets.QPlainTextEdit()
        self.text_output.setReadOnly(True)
        self.text_output.setMaximumBlockCount(1000)

        button_bar = QtWidgets.QHBoxLayout()
        self.clear_button = QtWidgets.QPushButton("Clear")
        self.copy_button = QtWidgets.QPushButton("Copy Last Frame")

        button_bar.addWidget(self.clear_button)
        button_bar.addWidget(self.copy_button)
        button_bar.addStretch()

        layout.addWidget(self.status_label)
        layout.addLayout(button_bar)
        layout.addWidget(self.text_output)

        self.clear_button.clicked.connect(self.text_output.clear)
        self.copy_button.clicked.connect(self.copy_last_frame)

        self._last_frame = ""
        self.worker = SerialReader(com_cfg)
        self.worker.data_received.connect(self.on_data_received)
        self.worker.status_update.connect(self.status_label.setText)
        self.worker.error_signal.connect(self.on_error)
        self.worker.start()

    def copy_last_frame(self):
        if not self._last_frame:
            return
        clipboard = QtWidgets.QApplication.clipboard()
        clipboard.setText(self._last_frame)

    @QtCore.pyqtSlot(str)
    def on_data_received(self, message: str):
        self._last_frame = message
        self.text_output.appendPlainText(message)

    @QtCore.pyqtSlot(str)
    def on_error(self, message: str):
        QtWidgets.QMessageBox.critical(self, "Serial Error", message)
        self.text_output.appendPlainText(f"[ERROR] {message}")

    def closeEvent(self, event):  # noqa: N802 (Qt override)
        if self.worker.isRunning():
            self.worker.stop()
        super().closeEvent(event)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Standalone COM/RS-232 debug viewer.")
    parser.add_argument(
        "--config-dir",
        default=os.path.join(os.path.expanduser("~"), "csv_parser_configs"),
        help="Directory containing machine parser subfolders (default: %(default)s)",
    )
    parser.add_argument(
        "--machine",
        help="Machine name (subfolder under config dir). Defaults to the first COM config found.",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose/debug logging."
    )
    parser.add_argument(
        "--idle-gap",
        type=float,
        default=0.5,
        help="Seconds of silence before buffered text is treated as a complete frame (default: %(default)s).",
    )
    args = parser.parse_args(argv)

    setup_logging(args.verbose)

    cfg = resolve_com_config(args.config_dir, args.machine)
    machine_name = cfg.get("machine_name", args.machine or "Unknown Machine")
    com_cfg = cfg.get("COM")
    if not com_cfg:
        raise RuntimeError("Selected configuration does not contain a 'COM' section.")

    # propagate machine name for logging clarity
    com_cfg = dict(com_cfg)
    com_cfg.setdefault("machine_name", machine_name)

    app = QtWidgets.QApplication(sys.argv)
    window = DebugWindow(machine_name, com_cfg)
    window.worker.idle_gap = max(0.1, args.idle_gap)
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
