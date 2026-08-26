#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
machine_configs.py — equipment configuration, held centrally.

A machine's configuration (its source, its capture-and-map mappings, its QC
wiring, its PM/CAL tasks) used to live only inside the LabStation module
instance on that one bench. Reinstall or update LabStation and it was gone.
There was no way to re-purpose a config for a second identical instrument, and
no way to clean up the ones nobody wanted — so export/import files became the
workaround, and a second source of truth.

`lem_machine_config` is now the store. The station module reads and writes it
directly through its injected labcore_* helpers, exactly like every other seam;
this master view exposes it over HTTP so the floor can list, duplicate and
delete configs, and so a module starting up can offer a real choice: adopt an
existing machine, duplicate one, or create a new one.

The one rule that matters: **runtime state never travels.** A copy carrying the
source's uid, file position or standing override would corrupt both machines.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from labcore_gateway import check_write
from typing import Dict, List, Optional

CONFIG_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_machine_config ("
    "machine_uid TEXT PRIMARY KEY, title TEXT NOT NULL, config TEXT, "
    "updated_at TEXT, updated_by TEXT)"
)

# Where the module is up to, and what an operator has forced — per-instrument
# facts, not configuration. Mirrors what import_machine_config() clears in the
# module; keep the two in step.
RUNTIME_KEYS = frozenset({
    "last_position",
    "last_mtime",
    "last_result_file",
    "manual_override",
    "override_comment",
})


def new_uid() -> str:
    return uuid.uuid4().hex[:12]


def strip_runtime(config: dict) -> dict:
    """A config fit to hand to a different instrument."""
    return {k: v for k, v in (config or {}).items() if k not in RUNTIME_KEYS}


class MachineConfigStore:
    """Owns `lem_machine_config` — written by the floor and by every module."""

    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self._ready = False

    def ensure_schema(self) -> None:
        if not self._ready:
            self.gateway.sql(CONFIG_DDL)
            self._ready = True

    # ── read ───────────────────────────────────────────────────────────
    def list(self) -> List[dict]:
        """Names and timestamps only — the picker doesn't need every mapping
        in the lab."""
        self.ensure_schema()
        res = self.gateway.read_sql(
            "SELECT machine_uid, title, updated_at, updated_by "
            "FROM lem_machine_config ORDER BY title")
        if res.get("error"):
            return []
        return [{"machine_uid": str(r.get("machine_uid") or ""),
                 "title": str(r.get("title") or ""),
                 "updated_at": str(r.get("updated_at") or ""),
                 "updated_by": str(r.get("updated_by") or "")}
                for r in res.get("rows") or []]

    def get(self, machine_uid: str) -> Optional[dict]:
        self.ensure_schema()
        res = self.gateway.read_sql(
            "SELECT machine_uid, title, config, updated_at, updated_by "
            "FROM lem_machine_config WHERE machine_uid = ?", [machine_uid])
        if res.get("error"):
            return None
        rows = res.get("rows") or []
        if not rows:
            return None
        row = rows[0]
        try:
            config = json.loads(row.get("config") or "{}")
            if not isinstance(config, dict):
                config = {}
        except (TypeError, ValueError):
            # A hand-edited or truncated blob must not blank the floor.
            config = {}
        return {"machine_uid": str(row.get("machine_uid") or ""),
                "title": str(row.get("title") or ""),
                "config": config,
                "updated_at": str(row.get("updated_at") or ""),
                "updated_by": str(row.get("updated_by") or "")}

    # ── write ──────────────────────────────────────────────────────────
    def save(self, machine_uid: str, title: str, config: dict,
             by: str = "") -> dict:
        machine_uid = (machine_uid or "").strip()
        title = (title or "").strip()
        if not machine_uid:
            raise ValueError("A configuration needs a machine uid.")
        if not title:
            raise ValueError("A configuration needs a machine name.")
        self.ensure_schema()
        when = datetime.now().isoformat(timespec="seconds")
        # The config IS the parser's mapping — which column is which method. A
        # save reported as landed that did not leaves the bench parsing with the
        # old mapping while the editor shows the new one, and the difference
        # only ever surfaces as results filed against the wrong test.
        #
        # Raised rather than returned because this method's return value is the
        # endpoint's success body (`{"ok": true, **saved}`), and adding a
        # failure key to it would change the shape of a write that WORKED — the
        # one thing the floor UI is entitled to rely on.
        check_write(self.gateway.sql(
            "INSERT INTO lem_machine_config (machine_uid, title, config, "
            "updated_at, updated_by) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(machine_uid) DO UPDATE SET title=excluded.title, "
            "config=excluded.config, updated_at=excluded.updated_at, "
            "updated_by=excluded.updated_by",
            [machine_uid, title, json.dumps(config or {}), when, by]),
            what=f"the configuration for “{title}” was not saved")
        return {"machine_uid": machine_uid, "title": title,
                "updated_at": when, "updated_by": by}

    def create(self, title: str, by: str = "") -> dict:
        """Register a brand-new machine with an empty config."""
        title = (title or "").strip()
        if not title:
            raise ValueError("A new machine needs a name.")
        uid = new_uid()
        return self.save(uid, title, {"uid": uid, "title": title}, by=by)

    def duplicate(self, source_uid: str, title: str, by: str = "") -> dict:
        """Clone a config onto a new uid, leaving the original untouched."""
        title = (title or "").strip()
        if not title:
            raise ValueError("A duplicate needs a name.")
        source = self.get(source_uid)
        if source is None:
            raise LookupError(f"No configuration for {source_uid!r}.")
        uid = new_uid()
        config = strip_runtime(source["config"])
        config["uid"] = uid
        config["title"] = title
        return self.save(uid, title, config, by=by)

    def delete(self, machine_uid: str) -> None:
        self.ensure_schema()
        check_write(
            self.gateway.sql(
                "DELETE FROM lem_machine_config WHERE machine_uid = ?",
                [machine_uid]),
            what="the configuration was not deleted")
