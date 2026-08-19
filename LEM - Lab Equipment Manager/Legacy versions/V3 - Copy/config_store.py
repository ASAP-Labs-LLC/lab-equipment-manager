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


CONFIG_FILE = "lab_manager_config.json"
CURRENT_VERSION = 5  # â† bumped for view state fields


def load_config() -> AppConfig:
    default = AppConfig(version=CURRENT_VERSION, poll_minutes=5, map_locked=False, tests=[], boxes=[])
    if not os.path.exists(CONFIG_FILE):
        return default
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        cfg = AppConfig.from_dict(raw)
        if cfg.version != CURRENT_VERSION:
            cfg.version = CURRENT_VERSION
        return cfg
    except Exception:
        return default


def save_config(cfg: AppConfig) -> Tuple[bool, str]:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg.serialize(), f, indent=2)
        return True, "OK"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
