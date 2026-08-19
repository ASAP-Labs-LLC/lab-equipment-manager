#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db_config_store.py — LEM configuration persisted in the central LabCore DB.

Drop-in replacement for V4's file-based config_store. Instead of a local
lab_manager_config.json, the full AppConfig lives in namespaced ``lem_*`` tables
inside LabCore's central database, written through the LabCore write queue so
every other lab program can read LEM's equipment/QC configuration from one
shared source of truth.

Storage model (JSON-blob-per-row): each list element keeps its dataclass
``serialize()`` shape verbatim, so persistence is lossless. Scalar app settings
live as a single JSON blob in ``lem_meta``. The tables are still real relational
rows other programs can query (e.g. ``SELECT uid FROM lem_boxes``).
"""

from __future__ import annotations

import json
import logging
from typing import Tuple

from models import AppConfig

logger = logging.getLogger(__name__)

CURRENT_VERSION = 5

# Keys that are stored as their own rows/tables rather than in the settings blob.
_LIST_KEYS = ("samples", "boxes", "users", "checklists")

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS lem_meta (key TEXT PRIMARY KEY, value TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_boxes (uid TEXT PRIMARY KEY, data TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_samples (name TEXT PRIMARY KEY, data TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_users (username TEXT PRIMARY KEY, data TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_checklists (uid TEXT PRIMARY KEY, data TEXT)",
)


class DbConfigStore:
    """Load/save the full AppConfig against the central DB via the gateway."""

    def __init__(self, gateway, source: str = "LabEquipmentManager") -> None:
        self._gw = gateway
        self._source = source
        self._schema_ready = False
        # Try now, but never fail construction: LEM has to come up and SAY
        # LabCore is down, not refuse to start because it is.
        self._ensure_schema()

    def _ensure_schema(self) -> bool:
        """Create the LEM tables if we can reach LabCore. Returns whether the
        schema is known-good; retried on the next call when it isn't."""
        if self._schema_ready:
            return True
        # Ask what is already there rather than declaring five tables into the
        # shared write queue on every restart. See labcore_gateway.existing_tables.
        from labcore_gateway import existing_tables

        present = existing_tables(self._gw)
        for stmt in _SCHEMA:
            if present is not None and "IF NOT EXISTS" in stmt:
                name = stmt.split("IF NOT EXISTS", 1)[1].split("(", 1)[0].strip()
                if name in present:
                    continue
            try:
                res = self._gw.sql(stmt)
            except Exception as exc:                     # transport blew up
                logger.warning("LEM schema deferred: %s", exc)
                return False
            if "error" in res:
                logger.warning("LEM schema deferred: %s", res["error"])
                return False
        self._schema_ready = True
        return True

    # ── save ─────────────────────────────────────────────────────────
    def save(self, cfg: AppConfig) -> Tuple[bool, str]:
        if not self._ensure_schema():
            return False, "LabCore is unreachable — nothing was saved."
        try:
            data = cfg.serialize()
            settings = {k: v for k, v in data.items() if k not in _LIST_KEYS}

            self._rewrite_meta("settings", json.dumps(settings))
            self._rewrite_rows("lem_boxes", "uid", "uid", data.get("boxes", []))
            self._rewrite_rows("lem_samples", "name", "name", data.get("samples", []))
            self._rewrite_rows("lem_users", "username", "username", data.get("users", []))
            self._rewrite_rows("lem_checklists", "uid", "uid", data.get("checklists", []))
            return True, "OK"
        except Exception as exc:  # noqa: BLE001 - report, don't crash the caller
            return False, f"{type(exc).__name__}: {exc}"

    def _rewrite_meta(self, key: str, value: str) -> None:
        self._check(self._gw.sql(
            "INSERT INTO lem_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            [key, value],
        ))

    def _rewrite_rows(self, table: str, id_col: str, id_field: str, items: list) -> None:
        # Full rewrite so deletions propagate (mirrors V4's whole-file save).
        self._check(self._gw.sql(f"DELETE FROM {table}"))
        for item in items:
            self._check(self._gw.sql(
                f"INSERT INTO {table} ({id_col}, data) VALUES (?, ?)",
                [str(item.get(id_field, "")), json.dumps(item)],
            ))

    @staticmethod
    def _check(res: dict) -> None:
        if isinstance(res, dict) and "error" in res:
            raise RuntimeError(res["error"])

    # ── load ─────────────────────────────────────────────────────────
    def load(self) -> AppConfig:
        self._ensure_schema()      # picks the tables up when LabCore returns
        settings = self._read_meta("settings")
        data = dict(settings) if settings else {}
        data["boxes"] = self._read_rows("lem_boxes")
        data["samples"] = self._read_rows("lem_samples")
        data["users"] = self._read_rows("lem_users")
        data["checklists"] = self._read_rows("lem_checklists")
        if not settings and not any(data[k] for k in _LIST_KEYS):
            return AppConfig(version=CURRENT_VERSION, poll_minutes=5, map_locked=False,
                             samples=[], boxes=[])
        cfg = AppConfig.from_dict(data)
        cfg.version = CURRENT_VERSION
        return cfg

    def _read_meta(self, key: str) -> dict:
        res = self._gw.read_sql("SELECT value FROM lem_meta WHERE key = ?", [key])
        if res.get("ok") and res.get("rows"):
            try:
                return json.loads(res["rows"][0]["value"])
            except (ValueError, TypeError):
                return {}
        return {}

    def _read_rows(self, table: str) -> list:
        res = self._gw.read_sql(f"SELECT data FROM {table}")
        if not res.get("ok"):
            return []
        out = []
        for row in res["rows"]:
            try:
                out.append(json.loads(row["data"]))
            except (ValueError, TypeError):
                continue
        return out
