# File: csv_parser_app/utils/settings_manager.py

import json
import os
import threading
from .logging_utils import log_message

LOG_LOCK = threading.Lock()

class SettingsManager:
    """Manages global application settings (e.g., theme, scale factor)."""

    def __init__(self, settings_file: str):
        """
        Initialize the SettingsManager with the path to the settings file.
        
        :param settings_file: Path to the JSON file where settings are stored.
        """
        self.settings_file = settings_file
        self.settings = {}
        self.load_settings()

    def load_settings(self) -> None:
        """Load settings from the settings file."""
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    self.settings = json.load(f)
                log_message('info', f"Loaded settings from {self.settings_file}")
            except Exception as e:
                log_message('error', f"Failed to load settings: {e}")
        else:
            log_message('info', f"Settings file does not exist at {self.settings_file}. Using default settings.")

    def save_settings(self) -> None:
        """Save settings to the settings file."""
        try:
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=4)
            log_message('info', f"Settings saved to {self.settings_file}")
        except Exception as e:
            log_message('error', f"Failed to save settings: {e}")

    def get_setting(self, key: str, default=None):
        """
        Get a setting value by key, returning a default if not found.
        
        :param key: The setting key to retrieve.
        :param default: Default value if key is not in the settings.
        :return: Value of the setting or default.
        """
        return self.settings.get(key, default)

    def set_setting(self, key: str, value) -> None:
        """
        Set a setting value by key and save immediately.
        
        :param key: The setting key.
        :param value: The value to store.
        """
        self.settings[key] = value
        self.save_settings()
