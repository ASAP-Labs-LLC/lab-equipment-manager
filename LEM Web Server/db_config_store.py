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

EVERY ANSWER IS READ, AND THE REWRITE CANNOT EMPTY A TABLE (2026-08-25)
----------------------------------------------------------------------
This store was missed when the rest of the app was converted to
``labcore_result``, and it was the worst one to miss. It judged writes by
``if "error" in res`` — a hand-rolled key test rather than the shared rule —
and it wrapped a DELETE-then-re-INSERT full-table rewrite with no transaction
across the statements, because LabCore's queue takes one at a time. A refusal
between the two halves emptied the table and answered ``(True, "OK")``.

Both halves are fixed: the verdict comes from ``confirm_write``, and
``_rewrite_rows`` upserts first and prunes last, so the worst a refusal can do
is leave stale rows behind. See its docstring for why that ordering is the
substance of the fix and confirmation alone is not.

AND THE READS, WHICH WERE MISSED AGAIN (2026-08-25)
---------------------------------------------------
The first pass converted this store's writes and left its reads judging by
hand: ``if res.get("ok")``, else ``{}`` / ``[]``. That is the "require an
acknowledgement" rule — the one ``labcore_result`` documents as unsafe —
sitting in a READ, so the evidenced refusal (``{"error": "LabCore is busy…",
"busy": true}``) came back as a lab with no boxes, no QC standards and no
users.

Alone that is the familiar blank-floor bug. Next to the new prune it is data
loss: ``/api/boxes`` loads the config, appends one box and saves it back, so a
config invented from a refused read tells ``_rewrite_rows`` to delete every
standard, user and checklist in the lab — and it does, correctly, because the
list it was handed is the instruction. Both reads go through
``labcore_result.rows`` now, ``load()`` raises, and no caller can build a save
out of a read that never happened.
"""

from __future__ import annotations

import json
import logging
from typing import Tuple

from labcore_result import (LabCoreUnavailable, confirm_write,
                            refusal_of)
from labcore_result import rows as read_rows
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
            # `refusal_of`, not `if "error" in res`. The queue refuses past
            # ~100 pending BY ANSWERING, and not always with an "error" key —
            # a `{"ok": false}` read as a successful CREATE would latch
            # `_schema_ready` on a table that was never made.
            refused = refusal_of(res)
            if refused is not None:
                logger.warning("LEM schema deferred: %s", refused)
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

    def _rewrite_rows(self, table: str, id_col: str, id_field: str,
                      items: list) -> None:
        """Make `table` hold exactly `items` — UPSERT FIRST, PRUNE LAST.

        This was `DELETE FROM {table}` followed by one INSERT per item, and it
        is the reason db_config_store was a blocker rather than a reporting
        nit. LabCore's queue takes one statement at a time with no transaction
        across them, so a refusal landing between the wipe and the re-inserts
        left the table EMPTY — every QC standard, box and user in LEM's config
        gone — while `save()` returned `(True, "OK")` because nobody read the
        answers.

        Confirming the answers alone would only have made the loss LOUD. The
        order is what makes it survivable:

          1. upsert every wanted row, each confirmed. A refusal here leaves the
             old config in place plus whatever updates landed — a superset, and
             the operator repeats the save.
          2. only then delete the rows that are no longer wanted. A refusal
             here leaves a stale extra row, which is visible in the UI and
             fixed by saving again.

        Neither failure loses data the lab cannot get back, which is the whole
        difference. A full rewrite is still a full rewrite: step 2 really does
        remove what was dropped — `test_a_save_that_is_accepted_still_removes_
        what_was_deleted` holds that half so this cannot decay into "never
        delete anything".
        """
        keep = []
        for item in items:
            ident = str(item.get(id_field, ""))
            keep.append(ident)
            self._check(self._gw.sql(
                f"INSERT INTO {table} ({id_col}, data) VALUES (?, ?) "
                f"ON CONFLICT({id_col}) DO UPDATE SET data=excluded.data",
                [ident, json.dumps(item)],
            ))
        if not keep:
            # Emptying the list is a deliberate act — the accident above was
            # emptying it on the way to refilling it.
            self._check(self._gw.sql(f"DELETE FROM {table}"))
            return
        holes = ",".join("?" * len(keep))
        self._check(self._gw.sql(
            f"DELETE FROM {table} WHERE {id_col} NOT IN ({holes})", keep))

    @staticmethod
    def _check(res: dict) -> None:
        """Raise unless LabCore acknowledged the write.

        `if "error" in res` was the rule `labcore_result` exists to abolish:
        it is one key test standing in for a verdict, so it is blind to a
        gateway that answers `None` and to any refusal that reports itself
        another way, and it treats a falsy `error` key as a failure. The
        verdict is not re-derived here — see labcore_result.confirm_write.
        """
        confirm_write(res)

    # ── load ─────────────────────────────────────────────────────────
    def load(self) -> AppConfig:
        """The stored config, or `LabCoreError` if it could not be read.

        Raising is the point. Every caller of this either draws the lab or
        writes it back, and both of those turn a swallowed read into a
        statement about the lab: "no instruments" on the floor, or — through
        `save()`'s prune — an empty config table.
        """
        # NO `_ensure_schema()` here (2026-08-25). A read declares nothing —
        # `_read` swallows exactly one error, "no such table", which on a
        # LabCore where LEM has never saved a config is the honest empty. The
        # declaration was cheap on a built lab (it asks `existing_tables`
        # first) and not cheap in the two cases that matter: a lab where the
        # tables really are missing, and one where that question could not be
        # answered — both push five CREATEs into the queue from a path that is
        # only trying to READ, and on a full queue they are five refusals.
        # `save()` still declares, strictly.
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

    def _read(self, sql: str, args: list = None) -> list:
        """One read, judged by the shared rule instead of by hand.

        `missing_ok=True` swallows exactly one error — a table that does not
        exist yet — because LEM has to come up on a LabCore it has never run
        against, and a table nobody created genuinely holds nothing. Every
        other failure raises: `{}` here is not a config, it is an instruction
        to delete one.
        """
        try:
            res = self._gw.read_sql(sql, args or [])
        except Exception as exc:                      # transport, not logic
            # A call that blew up is even less of an answer than a refusal.
            # Named the same way so callers handle one fact, not two.
            raise LabCoreUnavailable(
                "LabCore could not be read ({0}: {1})".format(
                    type(exc).__name__, exc)) from exc
        return read_rows(res, missing_ok=True)

    def _read_meta(self, key: str) -> dict:
        found = self._read("SELECT value FROM lem_meta WHERE key = ?", [key])
        if not found:
            return {}
        try:
            return json.loads(found[0]["value"])
        except (ValueError, TypeError, KeyError):
            # LabCore answered and the blob is unusable. That is bad DATA, not
            # an unreachable LabCore, and the honest reading of it is the
            # default settings — which is what `{}` means to `load()`.
            return {}

    def _read_rows(self, table: str) -> list:
        out = []
        for row in self._read(f"SELECT data FROM {table}"):
            try:
                out.append(json.loads(row["data"]))
            except (ValueError, TypeError, KeyError):
                continue
        return out
