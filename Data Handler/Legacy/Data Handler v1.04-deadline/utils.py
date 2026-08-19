# utils.py
import json

def generate_default_headers(count=80):
    """Generate a list of default headers."""
    return [f"Column{i}" for i in range(1, count + 1)]

def load_json_config(config_path):
    """Load JSON configuration from a file."""
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Failed to load config {config_path}: {e}")
        return {}

def save_json_config(config_path, config_data):
    """Save JSON configuration to a file."""
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=4)
    except Exception as e:
        print(f"Failed to save config {config_path}: {e}")
