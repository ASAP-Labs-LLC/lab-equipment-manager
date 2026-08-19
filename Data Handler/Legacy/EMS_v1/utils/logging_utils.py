# File: csv_parser_app/utils/logging_utils.py

import logging
import threading
import os

# Thread-safe global logger setup
LOG_LOCK = threading.Lock()
logger = None

def get_logger(log_file: str = "parser.log", logger_name: str = "csv_parser_app") -> logging.Logger:
    """
    Return a thread-safe, singleton-style logger instance.
    
    :param log_file: Path to the file where logs will be saved.
    :param logger_name: Name for the logger (useful if you want multiple loggers).
    :return: Configured logger instance.
    """
    global logger
    with LOG_LOCK:
        if logger is None:
            logger = logging.getLogger(logger_name)
            logger.setLevel(logging.DEBUG)

            # Set a file handler
            fh = logging.FileHandler(log_file, encoding='utf-8')
            fh.setLevel(logging.DEBUG)
            formatter = logging.Formatter(
                '%(asctime)s [%(levelname)s] %(filename)s - %(funcName)s: %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            fh.setFormatter(formatter)
            logger.addHandler(fh)

            # (Optional) also log to console
            ch = logging.StreamHandler()
            ch.setLevel(logging.INFO)
            ch.setFormatter(formatter)
            logger.addHandler(ch)

        return logger

def log_message(level: str, message: str):
    """
    Thread-safe convenience function to log a message.

    :param level: One of 'info', 'warning', 'error', 'debug'.
    :param message: The message string to log.
    """
    global logger
    with LOG_LOCK:
        if logger is None:
            logger = get_logger()  # Default initialization
        if level == 'info':
            logger.info(message)
        elif level == 'warning':
            logger.warning(message)
        elif level == 'error':
            logger.error(message)
        else:
            logger.debug(message)
