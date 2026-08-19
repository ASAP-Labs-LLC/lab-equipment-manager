import json
import logging
import os
from .constants import SETTINGS_FILE

LOG = logging.getLogger(__name__)

class ConfigManager:
    def __init__(self):
        self.settings = self.load_settings()

    def load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                LOG.error(f"Failed to load settings: {e}")
                return self.default_settings()
        else:
            LOG.info("Settings file does not exist. Using default.")
            return self.default_settings()

    def default_settings(self):
        return {
            "scale_factor": 1.5,
            "current_theme": "default.css",
            "default_interval": 10,
            "features": {
                "enable_experimental": True,
                "enable_raw_data_logging": True
            }
        }

    def save_settings(self, new_settings):
        try:
            with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(new_settings, f, indent=4)
            self.settings = new_settings
            LOG.info("Settings saved successfully.")
        except Exception as e:
            LOG.error(f"Failed to save settings: {e}")
