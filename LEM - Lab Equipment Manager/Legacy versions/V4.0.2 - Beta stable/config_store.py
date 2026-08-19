#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config_store.py â€” Load/save application configuration JSON, with basic validation.
"""

from __future__ import annotations

import json
import os
from typing import Tuple
import time

from models import AppConfig


BASE_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.path.join(BASE_DIR, "lab_manager_config.json")
CURRENT_VERSION = 5


def load_config() -> AppConfig:
    default = AppConfig(version=CURRENT_VERSION, poll_minutes=5, map_locked=False, samples=[], boxes=[])

    def _read(path: str) -> AppConfig | None:
        try:
            # Accept files with or without UTF-8 BOM
            with open(path, "r", encoding="utf-8-sig") as f:
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

    # Ensure unique box UIDs to avoid UI/state inconsistencies when mapping items
    def _ensure_unique_box_uids(c: AppConfig) -> bool:
        changed = False
        seen = set()
        # start from a time-based seed to reduce collision chance across runs
        next_id = int(time.time() * 1_000_000)
        for b in c.boxes:
            uid = (b.uid or "").strip()
            if not uid or uid in seen:
                # assign a new unique id
                while True:
                    cand = f"box_{next_id}"
                    next_id += 1
                    if cand not in seen:
                        b.uid = cand
                        changed = True
                        break
            seen.add(b.uid)
        # prune first_inspec_map entries that no longer correspond to any box uid
        pruned = {k: v for k, v in c.first_inspec_map.items() if k in seen}
        if len(pruned) != len(c.first_inspec_map):
            c.first_inspec_map = pruned
            changed = True
        return changed

    try:
        if _ensure_unique_box_uids(cfg):
            # persist repaired config
            save_config(cfg)
    except Exception:
        pass

    return cfg


def save_config(cfg: AppConfig) -> Tuple[bool, str]:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg.serialize(), f, indent=2)
        return True, "OK"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
