#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
maintenance_store.py — PM and calibration schedules, held centrally.

V4 tracked maintenance per machine: a name, a repeat interval, and the date
it was last done. The station modules already evaluate those tasks and drive
the PM and CAL pills — but a task that can only be created on the instrument's
own LabStation is in the wrong place for a lab manager. These live in LabCore
so they can be set from the floor and read by every module.

Every answer LabCore gives now goes through `labcore_result` (2026-08-24).
Before that it went through nothing at all: `save`, `complete`, `delete` and
`forget` discarded the gateway's answer, and `_rows` turned any read error into
`[]`. LabCore's write queue serialises at roughly 1.5 writes a second and
refuses past ~100 pending BY ANSWERING — no exception raised, and not always an
"error" key either — so a dropped write was indistinguishable from a saved one.
Concretely: an annual calibration could be marked complete on the floor, the
screen could say saved, and the instrument would still be RED-overdue in the
record every module reads. That is the failure this file no longer permits.

The read side matters just as much, because these reads decide writes. `get()`
is what the completion route uses to answer "no such task", and `all()` is what
the CSV importer diffs against; a read that degrades to `[]` during a blip turns
"could not ask" into "does not exist", which is how a completion becomes a 404
about a task that is really there, and how an import re-creates every task the
lab already has.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from labcore_result import (LabCoreRefused, LabCoreUnavailable, confirm_write,
                            wrote_rows)
from labcore_result import rows as read_rows

MAINTENANCE_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_maintenance ("
    "uid TEXT PRIMARY KEY, machine_uid TEXT NOT NULL, name TEXT NOT NULL, "
    "kind TEXT, interval_days INTEGER, last_done TEXT, note TEXT)"
)

GREEN, YELLOW, RED = "GREEN", "YELLOW", "RED"


class MaintenanceWriteError(LabCoreRefused):
    """A PM/Cal write LabCore did not acknowledge, so it did not happen.

    A subclass of `LabCoreRefused` on purpose: a route that wants to answer 503
    for every store in the app catches `LabCoreError`, one that cares about
    refusals catches `LabCoreRefused`, and one that wants to say "the
    calibration was not recorded" in words catches this. All three work without
    anybody having to know which store raised.
    """


@dataclass
class MaintTaskRecord:
    """One repeating job on one instrument."""

    uid: str
    machine_uid: str
    name: str
    kind: str = "pm"                 # "pm" | "calibration"
    interval_days: int = 30
    last_done: str = ""              # ISO date; "" = never
    note: str = ""

    def next_due(self) -> Optional[date]:
        if not self.last_done:
            return None
        try:
            return (date.fromisoformat(self.last_done)
                    + timedelta(days=max(1, self.interval_days)))
        except ValueError:
            return None

    def status(self, today: date) -> Tuple[str, str]:
        due = self.next_due()
        if due is None:
            return YELLOW, f"Not completed yet: {self.name}"
        if due < today:
            return RED, f"Overdue: {self.name} (was due {due.isoformat()})"
        if due == today:
            return YELLOW, f"Due today: {self.name}"
        return GREEN, f"{self.name}: next due {due.isoformat()}"

    def to_dict(self, today: Optional[date] = None) -> dict:
        today = today or date.today()
        state, reason = self.status(today)
        due = self.next_due()
        return {"uid": self.uid, "machine_uid": self.machine_uid,
                "name": self.name, "kind": self.kind,
                "interval_days": self.interval_days,
                "last_done": self.last_done, "note": self.note,
                "status": state, "reason": reason,
                "next_due": due.isoformat() if due else None}


class MaintenanceStore:
    """Owns `lem_maintenance` — read by the floor and by every module."""

    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self._ready = False

    # ── talking to LabCore ────────────────────────────────────────────
    # Two helpers so the rule is stated once. Every write in this file goes
    # through `_write`/`_write_rows`; every read goes through `_read`.

    def _write(self, sql: str, args: Optional[list] = None) -> dict:
        """Issue a write and raise unless LabCore acknowledged it."""
        try:
            res = self.gateway.sql(sql, args or [])
        except Exception as exc:                     # transport, not logic
            # A call that blew up is even more certainly a write that did not
            # happen than a refusal is. Making routes handle two shapes for one
            # fact is how the swallowing started, so it is named the same way.
            raise MaintenanceWriteError(
                f"LabCore could not be written to ({type(exc).__name__}: "
                f"{exc})") from exc
        try:
            confirm_write(res)
        except LabCoreRefused as exc:
        # The ANSWER travels with the re-label, not just the sentence.
        # Re-raising the text alone drops `busy` and `retry_after`, so a
        # full queue reaches the browser as 502 "this will never work"
        # instead of 503 with a Retry-After — the one distinction a client
        # cannot recover by reading English.
            raise MaintenanceWriteError(str(exc), res) from exc
        return res

    def _write_rows(self, sql: str, args: Optional[list] = None) -> int:
        """A confirmed write's row count. Zero is a real answer, not a failure:
        deleting a task that is already gone happened, it just matched
        nothing."""
        return wrote_rows(self._write(sql, args))

    def _read(self, sql: str, args: Optional[list] = None) -> List[dict]:
        try:
            res = self.gateway.read_sql(sql, args or [])
        except Exception as exc:
            raise LabCoreUnavailable(
                f"LabCore could not be read ({type(exc).__name__}: "
                f"{exc})") from exc
        # missing_ok=True, corrected 2026-08-25. It was False, justified by
        # "`ensure_schema` runs first and CONFIRMS its CREATE, so the table
        # provably exists" — and that justification was the regression: it
        # required every READ to declare a schema, so a `CREATE TABLE IF NOT
        # EXISTS` refused by a full WRITE queue made the PM dialog fail for a
        # table that had existed for months. Reads declare nothing now, which
        # puts "no such table" back on the table as a real answer, and its
        # honest reading is empty: nobody has ever scheduled any maintenance.
        #
        # Every other error still raises — a timeout, a busy answer, a
        # non-answer. Those are the shapes that would report "nothing
        # scheduled" about an instrument with a calibration due.
        return read_rows(res, missing_ok=True)

    def ensure_schema(self) -> None:
        """Declare the table, and raise if LabCore will not. WRITES ONLY.

        `_ready` is only set once the CREATE is acknowledged. It used to be set
        unconditionally, so a refused CREATE latched "ready" for the life of the
        process and every INSERT afterwards was aimed at a table that did not
        exist — and reported success.

        Called from the writing methods and from nowhere else. `_rows()` used
        to call it, which meant merely LOOKING at a schedule failed while the
        write queue was full. A SELECT does not need the table declared: it
        says "no such table" itself, which is the one error a read may call
        empty.
        """
        if not self._ready:
            self._write(MAINTENANCE_DDL)
            self._ready = True

    @staticmethod
    def _normalise_kind(kind: str) -> str:
        return "calibration" if "cal" in (kind or "").lower() else "pm"

    def save(self, task: MaintTaskRecord) -> MaintTaskRecord:
        if not task.name.strip():
            raise ValueError("A maintenance task needs a name.")
        if int(task.interval_days) < 1:
            raise ValueError("The repeat interval must be at least one day.")
        task.uid = task.uid or uuid.uuid4().hex[:12]
        task.kind = self._normalise_kind(task.kind)
        self.ensure_schema()
        self._write(
            "INSERT INTO lem_maintenance (uid, machine_uid, name, kind, "
            "interval_days, last_done, note) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET machine_uid=excluded.machine_uid, "
            "name=excluded.name, kind=excluded.kind, "
            "interval_days=excluded.interval_days, "
            "last_done=excluded.last_done, note=excluded.note",
            [task.uid, task.machine_uid, task.name.strip(), task.kind,
             int(task.interval_days), task.last_done, task.note])
        # Returned only after the row is acknowledged. The caller audits this
        # record and shows it back to the operator as "saved"; handing it over
        # before LabCore agreed is the whole bug.
        return task

    def complete(self, uid: str, when: str, note: str = "") -> None:
        """Stamp a task done. The single most expensive write here to lose: a
        calibration marked complete but never stored stays overdue in the
        record while the floor shows it green until the next snapshot, and an
        auditor reads a gap where the work was."""
        self.ensure_schema()
        self._write(
            "UPDATE lem_maintenance SET last_done = ?, note = ? WHERE uid = ?",
            [when, note, uid])

    def delete(self, uid: str) -> int:
        """Remove one task. Returns how many rows it matched — 0 is legitimate
        (someone deleted it on another screen first) and is NOT an error."""
        self.ensure_schema()
        return self._write_rows("DELETE FROM lem_maintenance WHERE uid = ?",
                                [uid])

    def forget(self, machine_uid: str) -> int:
        """Drop a retired instrument's whole schedule.

        Part of deleting a machine, and worth raising over: PM rows left behind
        by a silently refused DELETE re-attach to the uid if that instrument is
        ever registered again, and turn up on the "what is overdue anywhere"
        list belonging to a machine nobody can find.
        """
        self.ensure_schema()
        return self._write_rows(
            "DELETE FROM lem_maintenance WHERE machine_uid = ?", [machine_uid])

    def _rows(self, where: str = "", args: Optional[list] = None) -> List[dict]:
        # No `ensure_schema()`: see the note there. Reading a schedule must not
        # fail because the write queue is full.
        return self._read(
            "SELECT uid, machine_uid, name, kind, interval_days, last_done, "
            "note FROM lem_maintenance " + where + " ORDER BY kind, name",
            args or [])

    @staticmethod
    def _record(row: dict) -> MaintTaskRecord:
        return MaintTaskRecord(
            uid=str(row.get("uid") or ""),
            machine_uid=str(row.get("machine_uid") or ""),
            name=str(row.get("name") or ""),
            kind=str(row.get("kind") or "pm"),
            interval_days=int(row.get("interval_days") or 30),
            last_done=str(row.get("last_done") or ""),
            note=str(row.get("note") or ""))

    def for_machine(self, machine_uid: str) -> List[MaintTaskRecord]:
        # Does not degrade. "This instrument has no PM scheduled" and "I could
        # not ask" look identical in the dialog, and the first invites someone
        # to create a duplicate of a task that already exists.
        return [self._record(r) for r in
                self._rows("WHERE machine_uid = ?", [machine_uid])]

    def get(self, uid: str) -> Optional[MaintTaskRecord]:
        """The task, or None if there genuinely isn't one.

        A READ THAT DECIDES A WRITE: the completion route answers 404 from
        `None` here, and the CSV importer decides whether to reschedule. So the
        only way to get None is an answered read with no matching row —
        anything else raises rather than being served as "does not exist".
        """
        rows = self._rows("WHERE uid = ?", [uid])
        return self._record(rows[0]) if rows else None

    def all(self) -> Dict[str, List[MaintTaskRecord]]:
        # Also read-that-decides-a-write: `plan_import` diffs the CSV against
        # this, so an empty answer during a blip plans a fresh copy of every
        # task in the lab.
        grouped: Dict[str, List[MaintTaskRecord]] = {}
        for row in self._rows():
            rec = self._record(row)
            grouped.setdefault(rec.machine_uid, []).append(rec)
        return grouped
