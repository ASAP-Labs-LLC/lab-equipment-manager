import json
import os
import threading
import logging

LOG_LOCK = threading.Lock()
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

class SettingsManager:
    """Manages application settings."""
    def __init__(self, settings_file):
        self.settings_file = settings_file
        self.settings = {}
        self.load_settings()

    def load_settings(self):
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    self.settings = json.load(f)
                self.log_message('info', "Loaded settings from file.")
            except Exception as e:
                self.log_message('error', f"Failed to load settings: {e}")
        else:
            self.settings = {}
            self.log_message('info', "Settings file does not exist. Using default settings.")

    def save_settings(self):
        try:
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=4)
            self.log_message('info', "Settings saved successfully.")
        except Exception as e:
            self.log_message('error', f"Failed to save settings: {e}")

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value
        self.save_settings()

    def log_message(self, level, message):
        with LOG_LOCK:
            if level == 'info':
                logger.info(message)
            elif level == 'warning':
                logger.warning(message)
            elif level == 'error':
                logger.error(message)
            else:
                logger.debug(message)
