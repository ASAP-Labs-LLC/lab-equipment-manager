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

EVERY ANSWER FROM THE GATEWAY IS READ (2026-08-25)
--------------------------------------------------
`save()` and `delete()` used to throw LabCore's answer away, and both reads
turned any error into "there is nothing there". LabCore's write queue refuses
past ~100 pending BY ANSWERING — the recorded shape is an error dict carrying
`busy` and `retry_after` (notes.md; lem_station_module.py:495), returned
normally rather than raised — so a config the floor reported as saved could
simply never have been written, and a module adopting that machine would come
back to its picker with the old mappings or none at all.

The reads were worse in one specific way: `get()` returning None on a read
error made the route answer **404, "No configuration for that machine"**, about
a configuration that exists — and `duplicate()` reads through `get()`, so a
blip made it refuse to copy a real config. "Could not ask" served as "does not
exist" is exactly how a save becomes a 404 about something real.

Writes now go through `confirm_write`, reads through `rows`, and each read
path says in a comment whether a missing table may honestly mean empty.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from labcore_result import (
    LabCoreError,
    LabCoreRefused,
    LabCoreUnavailable,
    confirm_write,
    rows,
)

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


class MachineConfigError(LabCoreError):
    """The configuration store could not be trusted — one name for the routes.

    Subclasses `LabCoreError` so a caller that treats every store alike still
    catches it, and the two children below keep the split `labcore_result`
    exists for: a route can only honestly offer "try again" if it can tell
    "LabCore said no" from "LabCore could not be asked".
    """


class ConfigWriteRefused(MachineConfigError, LabCoreRefused):
    """A configuration was not written. The caller must not report "saved"."""


class ConfigReadUnavailable(MachineConfigError, LabCoreUnavailable):
    """A configuration could not be read, so its existence is unknown.

    Explicitly NOT "there is no such configuration" — that distinction is the
    difference between a 503 and a 404 about a machine the lab is running.
    """


def _confirm(res, what: str) -> None:
    """Confirm one write, or raise naming the configuration that was lost."""
    try:
        confirm_write(res)
    except LabCoreRefused as exc:
        # The ANSWER travels with the re-label, not just the sentence.
        # Re-raising the text alone drops `busy` and `retry_after`, so a
        # full queue reaches the browser as 502 "this will never work"
        # instead of 503 with a Retry-After — the one distinction a client
        # cannot recover by reading English.
        raise ConfigWriteRefused(
            "{0} — not saved: {1}".format(what, exc),
            getattr(exc, "result", None)) from exc


def _read(res, what: str, *, missing_ok: bool):
    """Rows from a read, or a raise saying which read could not be answered."""
    try:
        return rows(res, missing_ok=missing_ok)
    except LabCoreUnavailable as exc:
        raise ConfigReadUnavailable("{0}: {1}".format(what, exc)) from exc


class MachineConfigStore:
    """Owns `lem_machine_config` — written by the floor and by every module."""

    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self._ready = False

    def ensure_schema(self) -> None:
        """Declare `lem_machine_config`, or raise. WRITES ONLY.

        `_ready` is set only once the CREATE is ACKNOWLEDGED. Setting it
        unconditionally meant a boot during a full write queue left the store
        believing its table existed for the life of the process, writing every
        config afterwards into a table that was never made.

        `list()` and `get()` used to call this and no longer do (2026-08-25).
        A refused CREATE from a full WRITE queue was failing the picker and the
        adopt dialog for a table that had existed for months — a read taken
        down by a write, during exactly the congestion this store was hardened
        for. A SELECT needs no declaration; it answers "no such table" itself.
        """
        if not self._ready:
            _confirm(self._sql(CONFIG_DDL, what="creating lem_machine_config"),
                     "creating lem_machine_config")
            self._ready = True

    def _read_sql(self, sql: str, args: Optional[list] = None, *,
                  what: str) -> dict:
        """Issue a read, turning a RAISED transport error into an ANSWER.

        The read calls sat bare inside `_read(...)`, which converts the ANSWER
        and can convert nothing when the client THROWS instead. So a socket
        error escaped `machine_configs` entirely, sailed past every
        `except ConfigReadUnavailable` in web_app, and became a bare 500 —
        "Internal Server Error" about a configuration whose existence is
        exactly what the caller was asking about.
        """
        try:
            return self.gateway.read_sql(sql, args or [])
        except Exception as exc:
            return {"error": "{0} — LabCore could not be read ({1}: "
                             "{2})".format(what, type(exc).__name__, exc)}

    def _sql(self, sql: str, args: Optional[list] = None, *, what: str) -> dict:
        """Issue a write, turning a RAISED transport error into our own type.

        Without this the `gateway.sql(...)` call sat bare inside `_confirm(...)`
        and only the ANSWER was converted — so a socket error, which is a write
        that equally did not happen, escaped as a raw `OSError` past every
        `except MachineConfigError` in web_app and became a bare 500. The
        stores with `_write` helpers (checklists, lab_schedule,
        maintenance_store) already did this; these did not.
        """
        try:
            return self.gateway.sql(sql, args or [])
        except Exception as exc:
            raise ConfigWriteRefused(
                "{0} — not saved: LabCore could not be written to ({1}: "
                "{2})".format(what, type(exc).__name__, exc)) from exc

    # ── read ───────────────────────────────────────────────────────────
    def list(self) -> List[dict]:
        """Names and timestamps only — the picker doesn't need every mapping
        in the lab."""
        # No `ensure_schema()` — see the note there. missing_ok: no table means
        # no module has ever registered, and an
        # empty picker is then the truth. Any other error raises, because an
        # empty picker during a blip invites an operator to create a SECOND
        # configuration for an instrument that already has one — a duplicate
        # nobody asked for, which is the equipment-document bug in a new coat.
        found = _read(
            self._read_sql(
                "SELECT machine_uid, title, updated_at, updated_by "
                "FROM lem_machine_config ORDER BY title",
                what="listing the equipment configurations"),
            "listing the equipment configurations", missing_ok=True)
        return [{"machine_uid": str(r.get("machine_uid") or ""),
                 "title": str(r.get("title") or ""),
                 "updated_at": str(r.get("updated_at") or ""),
                 "updated_by": str(r.get("updated_by") or "")}
                for r in found]

    def get(self, machine_uid: str) -> Optional[dict]:
        # No `ensure_schema()` — see the note there.
        # missing_ok: with no table there is genuinely no configuration for
        # anyone, so None is honest. A read ERROR is not: None here becomes the
        # route's 404 "No configuration for that machine" about a machine that
        # is running right now, and duplicate() reads through this method, so a
        # blip would make it refuse to copy a config that plainly exists.
        found = _read(
            self._read_sql(
                "SELECT machine_uid, title, config, updated_at, updated_by "
                "FROM lem_machine_config WHERE machine_uid = ?", [machine_uid],
                what="reading the configuration of {0}".format(machine_uid)),
            "reading the configuration of {0}".format(machine_uid),
            missing_ok=True)
        if not found:
            return None
        row = found[0]
        try:
            config = json.loads(row.get("config") or "{}")
            if not isinstance(config, dict):
                config = {}
        except (TypeError, ValueError):
            # Kept deliberately, and it is about the DATA, not the answer: a
            # hand-edited or truncated blob is one broken config, and taking
            # the whole floor down for it would be the larger failure. LabCore
            # answered; what it holds is unreadable.
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
            raise ValueError("A configuration needs an equipment name.")
        self.ensure_schema()
        when = datetime.now().isoformat(timespec="seconds")
        # The return value below is the route's "ok, saved" payload, so it
        # must not be built from an unread answer: a refused write here loses a
        # whole bench's mappings, QC wiring and PM tasks while the floor shows
        # the new name.
        _confirm(
            self._sql(
                "INSERT INTO lem_machine_config (machine_uid, title, config, "
                "updated_at, updated_by) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(machine_uid) DO UPDATE SET title=excluded.title, "
                "config=excluded.config, updated_at=excluded.updated_at, "
                "updated_by=excluded.updated_by",
                [machine_uid, title, json.dumps(config or {}), when, by],
                what="saving the configuration of {0} ({1})".format(
                    title, machine_uid)),
            "saving the configuration of {0} ({1})".format(title, machine_uid))
        return {"machine_uid": machine_uid, "title": title,
                "updated_at": when, "updated_by": by}

    def create(self, title: str, by: str = "") -> dict:
        """Register a brand-new machine with an empty config."""
        title = (title or "").strip()
        if not title:
            raise ValueError("New equipment needs a name.")
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
        # rows_affected 0 is an acknowledgement and confirm_write accepts it:
        # deleting a config that was already gone DID happen. A refusal is the
        # opposite — the config survives and offers itself in the module's
        # picker again, so a bench can adopt a machine the floor says it
        # retired.
        self.ensure_schema()
        _confirm(
            self._sql(
                "DELETE FROM lem_machine_config WHERE machine_uid = ?",
                [machine_uid],
                what="deleting the configuration of {0}".format(machine_uid)),
            "deleting the configuration of {0}".format(machine_uid))
