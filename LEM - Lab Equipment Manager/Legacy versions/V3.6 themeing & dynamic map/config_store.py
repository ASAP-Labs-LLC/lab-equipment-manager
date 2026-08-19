#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config_store.py â€” Load/save application configuration JSON, with basic validation.
"""

from __future__ import annotations

import json
import os
from typing import Tuple

from models import AppConfig


BASE_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.path.join(BASE_DIR, "lab_manager_config.json")
CURRENT_VERSION = 5


def load_config() -> AppConfig:
    default = AppConfig(version=CURRENT_VERSION, poll_minutes=5, map_locked=False, samples=[], boxes=[])

    def _read(path: str) -> AppConfig | None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            cfg = AppConfig.from_dict(raw)
            cfg.version = CURRENT_VERSION
            return cfg
        except Exception:
            return None

    # Primary location (next to app files)
    cfg = _read(CONFIG_FILE) if os.path.exists(CONFIG_FILE) else None

    # Fallback: migrate from older dropped-in version folder if present and has data
    if cfg is None or (not cfg.boxes and not cfg.samples):
        legacy_dir = os.path.join(BASE_DIR, "V3.5 - retain status data, and status logging")
        legacy_path = os.path.join(legacy_dir, "lab_manager_config.json")
        if os.path.exists(legacy_path):
            legacy = _read(legacy_path)
            if legacy and (legacy.boxes or legacy.samples):
                # Save migrated copy to primary location for future runs
                try:
                    ok, _ = save_config(legacy)
                    if ok:
                        return legacy
                except Exception:
                    pass
                return legacy

    if cfg is None:
        return default
    return cfg


def save_config(cfg: AppConfig) -> Tuple[bool, str]:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg.serialize(), f, indent=2)
        return True, "OK"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
