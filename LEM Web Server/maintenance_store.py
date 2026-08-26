#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
maintenance_store.py — PM and calibration schedules, held centrally.

V4 tracked maintenance per machine: a name, a repeat interval, and the date
it was last done. The station modules already evaluate those tasks and drive
the PM and CAL pills — but a task that can only be created on the instrument's
own LabStation is in the wrong place for a lab manager. These live in LabCore
so they can be set from the floor and read by every module.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from labcore_gateway import check_write
from typing import Dict, List, Optional, Tuple

MAINTENANCE_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_maintenance ("
    "uid TEXT PRIMARY KEY, machine_uid TEXT NOT NULL, name TEXT NOT NULL, "
    "kind TEXT, interval_days INTEGER, last_done TEXT, note TEXT)"
)

GREEN, YELLOW, RED = "GREEN", "YELLOW", "RED"


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

    def ensure_schema(self) -> None:
        if not self._ready:
            self.gateway.sql(MAINTENANCE_DDL)
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
        # A PM or calibration task that did not save is an interval nobody is
        # tracking — the floor shows it scheduled, LabCore has never heard of
        # it, and the first anyone knows is an assessor asking for the record.
        check_write(self.gateway.sql(
            "INSERT INTO lem_maintenance (uid, machine_uid, name, kind, "
            "interval_days, last_done, note) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET machine_uid=excluded.machine_uid, "
            "name=excluded.name, kind=excluded.kind, "
            "interval_days=excluded.interval_days, "
            "last_done=excluded.last_done, note=excluded.note",
            [task.uid, task.machine_uid, task.name.strip(), task.kind,
             int(task.interval_days), task.last_done, task.note]),
            what=f"the task “{task.name.strip()}” was not saved")
        return task

    def complete(self, uid: str, when: str, note: str = "") -> None:
        self.ensure_schema()
        # Marking work done is the write the whole schedule hangs off. Dropped
        # silently, the task stays overdue on the floor and somebody does it
        # twice — or, worse, the record of the calibration that WAS done is not
        # there when it is asked for.
        check_write(
            self.gateway.sql(
                "UPDATE lem_maintenance SET last_done = ?, note = ? "
                "WHERE uid = ?", [when, note, uid]),
            what="the completion was not recorded")

    def delete(self, uid: str) -> None:
        self.ensure_schema()
        check_write(
            self.gateway.sql("DELETE FROM lem_maintenance WHERE uid = ?",
                             [uid]),
            what="the task was not deleted")

    def forget(self, machine_uid: str) -> None:
        self.ensure_schema()
        self.gateway.sql("DELETE FROM lem_maintenance WHERE machine_uid = ?",
                         [machine_uid])

    def _rows(self, where: str = "", args: Optional[list] = None) -> List[dict]:
        self.ensure_schema()
        res = self.gateway.read_sql(
            "SELECT uid, machine_uid, name, kind, interval_days, last_done, "
            "note FROM lem_maintenance " + where + " ORDER BY kind, name",
            args or [])
        return [] if res.get("error") else (res.get("rows") or [])

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
        return [self._record(r) for r in
                self._rows("WHERE machine_uid = ?", [machine_uid])]

    def get(self, uid: str) -> Optional[MaintTaskRecord]:
        rows = self._rows("WHERE uid = ?", [uid])
        return self._record(rows[0]) if rows else None

    def all(self) -> Dict[str, List[MaintTaskRecord]]:
        grouped: Dict[str, List[MaintTaskRecord]] = {}
        for row in self._rows():
            rec = self._record(row)
            grouped.setdefault(rec.machine_uid, []).append(rec)
        return grouped
