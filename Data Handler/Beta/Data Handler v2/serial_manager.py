import time
import threading
import logging
import serial
from PyQt5.QtCore import QObject, pyqtSignal, QThread

# Configure logging
logger = logging.getLogger(__name__)

class SerialWorker(QObject):
    """
    Worker class to handle serial port communication in a separate thread.
    Features:
    - Auto-reconnection loop
    - Configurable framing (STX/ETX, etc.)
    - Qt signals for UI updates
    """
    # Signals
    connected = pyqtSignal(str)       # Emits port name when connected
    disconnected = pyqtSignal()      # Emits when disconnected
    data_received = pyqtSignal(str)   # Emits raw ticket/frame data
    status_update = pyqtSignal(str)   # Emits status messages for UI log
    error_occurred = pyqtSignal(str)  # Emits error messages

    # Control Constants
    CTRL_STX = 0x02
    CTRL_ETX = 0x03
    CTRL_LF = 0x0A
    CTRL_CR = 0x0D

    def __init__(self, config):
        super().__init__()
        self.config = config
        self._stop_event = threading.Event()
        self._thread = None
        
        # Extract settings
        self.port = config.get('port')
        self.baudrate = int(config.get('baud_rate', 9600))
        self.output_csv = config.get('output', '') # Destination for data handler
        self.machine_name = config.get('machine_name', 'Unknown')
        
        # Advanced settings
        self.parity = self._map_parity(config.get('parity', 'N'))
        self.stopbits = self._map_stopbits(config.get('stop_bits', 1))
        self.bytesize = self._map_bytesize(config.get('byte_size', 8))
        self.timeout = float(config.get('timeout', 1.0))
        self.idle_gap = float(config.get('idle_gap', 0.5))
        
        # Thread management
        self.is_running = False

    def start(self):
        """Start the worker thread."""
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name=f"SerialWorker-{self.port}")
        self._thread.start()
        self.is_running = True
        logger.info(f"Started SerialWorker for {self.port}")

    def stop(self):
        """Stop the worker thread gracefully."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self.is_running = False
        self.disconnected.emit()
        logger.info(f"Stopped SerialWorker for {self.port}")

    def _run_loop(self):
        """Main loop: connect -> read -> handle errors -> reconnect."""
        while not self._stop_event.is_set():
            try:
                self.status_update.emit(f"Attempting to connect to {self.port}...")
                with serial.Serial(
                    port=self.port,
                    baudrate=self.baudrate,
                    parity=self.parity,
                    stopbits=self.stopbits,
                    bytesize=self.bytesize,
                    timeout=self.timeout
                ) as ser:
                    ser.reset_input_buffer()
                    self.connected.emit(self.port)
                    self.status_update.emit(f"Connected to {self.port}")
                    logger.info(f"Connected to {self.port}")
                    
                    # Read loop
                    frame_buffer = bytearray()
                    in_frame = False
                    last_activity = time.monotonic()

                    while not self._stop_event.is_set():
                        try:
                            chunk = ser.read(64) # Read small chunks
                        except serial.SerialException as e:
                            logger.error(f"Serial read error on {self.port}: {e}")
                            self.status_update.emit(f"Connection lost: {e}")
                            break # Break inner loop to trigger reconnect

                        now = time.monotonic()
                        
                        if chunk:
                            last_activity = now
                            for byte in chunk:
                                # Framing Logic (STX/ETX + Idle fallback)
                                if byte == self.CTRL_STX:
                                    frame_buffer.clear()
                                    in_frame = True
                                elif byte == self.CTRL_ETX and in_frame:
                                    self._process_frame(frame_buffer)
                                    frame_buffer.clear()
                                    in_frame = False
                                else:
                                    # If not strictly in frame but we see valid chars (optional leniency)
                                    if not in_frame and (byte >= 32 or byte in (self.CTRL_CR, self.CTRL_LF)):
                                        in_frame = True
                                    
                                    if in_frame:
                                        # Filter mostly... allow printable and CR/LF
                                        if byte >= 32 or byte in (self.CTRL_CR, self.CTRL_LF):
                                            frame_buffer.append(byte if byte != self.CTRL_CR else self.CTRL_LF) # Normalize CR to LF? or keep raw? 
                                            # Let's keep it somewhat raw but normalize CR->LF is common for line based
                                            # Actually, let's keep exact bytes except maybe mapped CR->LF if desired
                                            # transform CR to LF for internal consistency if needed, 
                                            # but usually easiest to just append.
                                            # Let's stick to the logic from old data_handler:
                                            # "if b in (CTRL_LF, CTRL_CR): frame.append(0x0A)"
                                            pass
                        else:
                            # Timeout / IDLE check
                            if in_frame and frame_buffer and (now - last_activity > self.idle_gap):
                                logger.debug(f"Idle gap detected on {self.port}, flushing frame.")
                                self._process_frame(frame_buffer)
                                frame_buffer.clear()
                                in_frame = False
            
            except serial.SerialException as e:
                self.error_occurred.emit(str(e))
                self.status_update.emit(f"Serial Error: {e}. Retrying in 5s...")
                # Wait before reconnecting
                self._wait_interruptible(5.0)
            except Exception as e:
                self.error_occurred.emit(f"Unexpected error: {e}")
                self.status_update.emit(f"Error: {e}. Retrying in 5s...")
                self._wait_interruptible(5.0)

    def _process_frame(self, frame_bytes):
        """Decode and emit valid frames."""
        try:
            # Decode ASCII, ignore errors
            text = frame_bytes.decode('ascii', errors='ignore').strip()
            if text:
                logger.debug(f"Frame received from {self.port}: {repr(text)}")
                self.data_received.emit(text)
        except Exception as e:
            logger.error(f"Error processing frame: {e}")

    def _wait_interruptible(self, seconds):
        """Sleep for `seconds` but wake up if stop event is set."""
        self._stop_event.wait(timeout=seconds)

    # --- Helpers for Serial Config Mapping ---
    def _map_parity(self, char):
        mapping = {
            'N': serial.PARITY_NONE, 'E': serial.PARITY_EVEN, 
            'O': serial.PARITY_ODD, 'M': serial.PARITY_MARK, 'S': serial.PARITY_SPACE
        }
        return mapping.get(str(char).upper(), serial.PARITY_NONE)

    def _map_stopbits(self, val):
        try:
            val = float(val)
            if val == 1.5: return serial.STOPBITS_ONE_POINT_FIVE
            if val == 2: return serial.STOPBITS_TWO
        except:
            pass
        return serial.STOPBITS_ONE

    def _map_bytesize(self, val):
        try:
            val = int(val)
            if val == 5: return serial.FIVEBITS
            if val == 6: return serial.SIXBITS
            if val == 7: return serial.SEVENBITS
        except:
            pass
        return serial.EIGHTBITS
