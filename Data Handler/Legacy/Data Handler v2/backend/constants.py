import os

HOME_DIR = os.path.expanduser('~')
CONFIG_DIR = os.path.join(HOME_DIR, 'csv_parser_configs')
THEMES_DIR = os.path.join(CONFIG_DIR, 'themes')
LOG_FILE = os.path.join(CONFIG_DIR, 'parser.log')
SETTINGS_FILE = os.path.join(CONFIG_DIR, 'settings.json')

# Ensure directories
os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(THEMES_DIR, exist_ok=True)
