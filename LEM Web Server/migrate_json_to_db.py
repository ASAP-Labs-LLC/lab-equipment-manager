#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
migrate_json_to_db.py — seed the central DB from a V4 lab_manager_config.json.

One-shot importer: read the existing file-based config, and write it into the
LabCore-hosted lem_* tables via DbConfigStore. Safe to re-run (save() is a full
rewrite).

CLI:
    python migrate_json_to_db.py [path/to/lab_manager_config.json]

Defaults to LABCORE_HOST/LABCORE_PORT for the live gateway; the JSON path
defaults to the V4 beta-stable config next to this repo.
"""

from __future__ import annotations

import json
import os
import sys

from db_config_store import DbConfigStore
from models import AppConfig


def migrate_file(gateway, json_path: str) -> AppConfig:
    """Load ``json_path``, persist it into the DB via the gateway, return the config."""
    if not os.path.exists(json_path):
        raise FileNotFoundError(json_path)
    with open(json_path, "r", encoding="utf-8-sig") as fh:
        raw = json.load(fh)
    cfg = AppConfig.from_dict(raw)
    store = DbConfigStore(gateway)
    ok, msg = store.save(cfg)
    if not ok:
        raise RuntimeError(f"Migration save failed: {msg}")
    return cfg


def _default_json_path() -> str:
    here = os.path.dirname(__file__)
    return os.path.normpath(os.path.join(
        here, "..", "V4.0.3.1 - Beta Stable", "lab_manager_config.json"))


def main(argv: list) -> int:
    json_path = argv[1] if len(argv) > 1 else _default_json_path()
    from labcore_gateway import HttpLabCoreGateway

    gateway = HttpLabCoreGateway()  # LABCORE_URL → https://labvision.asaplabs.net
    if not gateway.is_running():
        print(f"LabCore not reachable at {gateway.base_url}. Start LabCore and retry.", file=sys.stderr)
        return 2
    cfg = migrate_file(gateway, json_path)
    print(f"Migrated {len(cfg.boxes)} boxes and {len(cfg.samples)} samples from {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
