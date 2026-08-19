"""
iso_uncertainty.py   – v2  (CSV-per-machine, rotates only when keyword changes)

Whenever `check_iso_uncertainty()` receives the freshly-appended DataFrame
chunk, it filters rows whose <column_name> contains <keyword>.  
Those rows are **appended to one CSV per machine**:

    <output_directory>/
        └─ ISO_<machine>_<safe-keyword>.csv

Rotation rule → *only* when the keyword changes:
    • File name embeds the current keyword, so a new file is created the first
      time you save with a new keyword.
    • Older files stay in place as your historical record; nothing happens on
      clock-based rollovers.

This removes the openpyxl dependency; add `openpyxl` to *requirements.txt*
*only* if you still need Excel elsewhere.
"""

import os
import csv
import logging
import threading
from datetime import datetime
import pandas as pd
from settings_manager import SettingsManager

# ── logging helper (unchanged) ──────────────────────────────────────────────
logger    = logging.getLogger(__name__)
LOG_LOCK  = threading.Lock()
if not logger.handlers:
    handler = logging.FileHandler("iso_uncertainty.log", mode="a", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

def log_message(level, message):
    with LOG_LOCK:
        getattr(logger, level, logger.info)(message)

# ── public API  ─────────────────────────────────────────────────────────────
def check_iso_uncertainty(df_new_chunk: pd.DataFrame, machine_name: str):
    """
    • Reads global ISO-uncertainty settings.
    • If enabled, filters df_new_chunk by <keyword> in <column_name>.
    • Appends matching rows to   ISO_<machine>_<keyword>.csv
    """
    # 1) Load config
    settings_file = os.path.join(
        os.path.expanduser("~"), "csv_parser_configs", "settings.json"
    )
    manager     = SettingsManager(settings_file)
    iso_config  = manager.get_setting("iso_uncertainty", {})

    if not iso_config.get("enabled", False):
        return

    keyword      = iso_config.get("keyword", "").strip()
    column_name  = iso_config.get("column_name", "").strip()
    output_dir   = iso_config.get("output_directory", "").strip()

    if not (keyword and column_name and output_dir):
        log_message("warning", "ISO settings incomplete; skipping.")
        return
    if column_name not in df_new_chunk.columns:
        log_message("warning",
                    f"ISO column '{column_name}' missing from incoming DataFrame.")
        return
    if df_new_chunk.empty:
        return

    # 2) Filter the new chunk
    matches = df_new_chunk[
        df_new_chunk[column_name].astype(str).str.contains(keyword, case=False, na=False)
    ]
    if matches.empty:
        return

    # 3) Build per-machine CSV path  (keyword is part of file-name = rotation key)
    safe_kw   = "".join(c for c in keyword if c.isalnum() or c in (" ", "_", "-")).strip()
    csv_name  = f"ISO_{machine_name}_{safe_kw}.csv"
    csv_path  = os.path.join(output_dir, csv_name)

    os.makedirs(output_dir, exist_ok=True)

    # 4) Append
    write_header = not os.path.exists(csv_path)
    try:
        matches.to_csv(csv_path, mode="a", header=write_header, index=False, quoting=csv.QUOTE_MINIMAL)
        log_message("info",
                    f"Appended {len(matches)} row(s) to ISO CSV: {csv_path}")
    except Exception as e:
        log_message("error", f"Failed to append to '{csv_path}': {e}")
