#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qc_specs.py — the QC bridge between this master view and the LEM Station
modules running in LabStation at each machine.

Contract (LabCore is the bus; nothing talks directly):

    lem_qc_specs        written HERE, read by the station modules. A module
                        self-checks every QC run against the spec for its
                        machine + LabCore test method.
    lem_machine_status  written by the modules, read HERE for the dashboard.
    lem_machine_log     written by the modules — each machine's full history
                        (runs, QC verdicts, status changes, overrides,
                        comments, PM/calibration completions).
    lem_machine_control written HERE, polled by the modules, to force a
                        machine to SERVICE / DEAD-LINE (or clear it).

All access goes through the injected LabCore gateway — never a raw DB
connection — so this works identically against the live HTTP LabCore and
the in-memory fake used by the tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from labcore_gateway import check_write
from typing import Dict, List, Optional

STATUS_COLORS = {
    "GREEN": "#21c071",
    "YELLOW": "#f5c542",
    "RED": "#f85b5b",
    "DEAD-LINE": "#0f172a",
    "SERVICE": "#8d99ae",
    "UNKNOWN": "#718096",
}

VALID_OVERRIDES = ("", "SERVICE", "DEAD-LINE")

QC_SPECS_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_qc_specs ("
    "machine_uid TEXT NOT NULL, test_name TEXT NOT NULL, sample_id TEXT, "
    "expected REAL, std_dev REAL, k REAL, units TEXT, "
    "PRIMARY KEY (machine_uid, test_name))"
)

CONTROL_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_machine_control ("
    "machine_uid TEXT PRIMARY KEY, manual_override TEXT, comment TEXT, "
    "updated_at TEXT)"
)


@dataclass
class QcSpec:
    """One QC rule: a machine's test passes when the QC sample's result is
    within expected ± k·std_dev."""

    machine_uid: str
    test_name: str
    sample_id: str
    expected: float
    std_dev: float
    k: float = 2.0
    units: str = ""

    def to_dict(self) -> dict:
        return {
            "machine_uid": self.machine_uid,
            "test_name": self.test_name,
            "sample_id": self.sample_id,
            "expected": self.expected,
            "std_dev": self.std_dev,
            "k": self.k,
            "units": self.units,
        }

    @classmethod
    def from_row(cls, row: dict) -> "QcSpec":
        return cls(
            machine_uid=str(row.get("machine_uid") or ""),
            test_name=str(row.get("test_name") or ""),
            sample_id=str(row.get("sample_id") or ""),
            expected=float(row.get("expected") or 0.0),
            std_dev=float(row.get("std_dev") or 0.0),
            k=float(row.get("k") or 2.0),
            units=str(row.get("units") or ""),
        )

    def limits(self) -> tuple:
        margin = self.k * self.std_dev
        return self.expected - margin, self.expected + margin


class QcSpecStore:
    """Owns `lem_qc_specs` — the table every station module reads."""

    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self._schema_ready = False

    def ensure_schema(self) -> None:
        if not self._schema_ready:
            self.gateway.sql(QC_SPECS_DDL)
            self._schema_ready = True

    def save(self, spec: QcSpec) -> None:
        if not spec.test_name.strip():
            raise ValueError("A QC spec needs a LabCore test method.")
        if spec.std_dev < 0:
            raise ValueError("Standard deviation cannot be negative.")
        if spec.k <= 0:
            raise ValueError("k must be greater than zero.")
        self.ensure_schema()
        # A spec is the expected value and the limits every reading on this
        # bench is judged against. Saved-but-not-saved leaves the module
        # judging against the OLD limits while the editor shows the new ones —
        # the same shape as a dropped correction factor, and just as invisible.
        check_write(self.gateway.sql(
            "INSERT INTO lem_qc_specs (machine_uid, test_name, sample_id, "
            "expected, std_dev, k, units) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(machine_uid, test_name) DO UPDATE SET "
            "sample_id=excluded.sample_id, expected=excluded.expected, "
            "std_dev=excluded.std_dev, k=excluded.k, units=excluded.units",
            [spec.machine_uid, spec.test_name.strip(), spec.sample_id.strip(),
             float(spec.expected), float(spec.std_dev), float(spec.k),
             spec.units],
        ), what=f"the QC spec for “{spec.test_name.strip()}” was not saved")

    def delete(self, machine_uid: str, test_name: str) -> None:
        self.ensure_schema()
        # Removing a spec stops the bench being QC-judged on that method at all.
        # Reported as done and not done, it goes on failing against limits
        # somebody deliberately retired.
        check_write(
            self.gateway.sql(
                "DELETE FROM lem_qc_specs WHERE machine_uid = ? "
                "AND test_name = ?", [machine_uid, test_name]),
            what=f"the QC spec for “{test_name}” was not removed")

    def list_specs(self, machine_uid: Optional[str] = None) -> List[QcSpec]:
        self.ensure_schema()
        if machine_uid:
            res = self.gateway.read_sql(
                "SELECT machine_uid, test_name, sample_id, expected, std_dev, "
                "k, units FROM lem_qc_specs WHERE machine_uid = ? "
                "ORDER BY test_name", [machine_uid])
        else:
            res = self.gateway.read_sql(
                "SELECT machine_uid, test_name, sample_id, expected, std_dev, "
                "k, units FROM lem_qc_specs ORDER BY machine_uid, test_name")
        if res.get("error"):
            return []
        return [QcSpec.from_row(row) for row in res.get("rows") or []]

    def specs_by_machine(self) -> Dict[str, List[QcSpec]]:
        grouped: Dict[str, List[QcSpec]] = {}
        for spec in self.list_specs():
            grouped.setdefault(spec.machine_uid, []).append(spec)
        return grouped


class MachineStateReader:
    """Reads what the station modules published, and pushes commands back."""

    def __init__(self, gateway) -> None:
        self.gateway = gateway

    def machines(self) -> List[dict]:
        res = self.gateway.read_sql(
            "SELECT machine_uid, title, status, reason, updated_at "
            "FROM lem_machine_status ORDER BY updated_at DESC")
        if res.get("error"):
            return []  # no station module has reported yet
        machines = []
        for row in res.get("rows") or []:
            status = str(row.get("status") or "UNKNOWN")
            machines.append({
                "machine_uid": row.get("machine_uid"),
                "title": row.get("title") or row.get("machine_uid"),
                "status": status,
                "status_color": STATUS_COLORS.get(status,
                                                  STATUS_COLORS["UNKNOWN"]),
                "reason": row.get("reason") or "",
                "updated_at": row.get("updated_at") or "",
            })
        return machines

    # A module is considered running if it has checked in within this many
    # seconds — a couple of missed 5-minute beats before we call it stopped.
    HEARTBEAT_GRACE = 900

    def heartbeats(self) -> Dict[str, dict]:
        """When each station module last checked in, and what it watches.
        Data writes are event-driven, so this is the only liveness signal:
        without it a stopped module looks exactly like an idle bench."""
        res = self.gateway.read_sql(
            "SELECT machine_uid, last_poll, watching FROM lem_machine_heartbeat")
        if res.get("error"):
            return {}
        return {str(r.get("machine_uid")): {
                    "last_poll": str(r.get("last_poll") or "") or None,
                    "watching": str(r.get("watching") or "")}
                for r in res.get("rows") or []}

    def last_activity(self) -> Dict[str, str]:
        """Newest log entry per machine. The status row is only rewritten
        when the status changes, so it is NOT a liveness signal — this is."""
        res = self.gateway.read_sql(
            "SELECT machine_uid, MAX(ts) AS ts FROM lem_machine_log "
            "GROUP BY machine_uid")
        if res.get("error"):
            return {}
        return {str(r.get("machine_uid")): str(r.get("ts") or "")
                for r in res.get("rows") or [] if r.get("ts")}

    def sub_statuses(self) -> Dict[str, dict]:
        """QC / PM / CAL per machine, as the station modules publish them.
        Machines that haven't reported the breakdown read UNKNOWN."""
        res = self.gateway.read_sql(
            "SELECT machine_uid, qc, pm, calibration FROM lem_machine_substatus")
        if res.get("error"):
            return {}
        out = {}
        for row in res.get("rows") or []:
            out[str(row.get("machine_uid"))] = {
                "qc": str(row.get("qc") or "UNKNOWN"),
                "pm": str(row.get("pm") or "UNKNOWN"),
                "calibration": str(row.get("calibration") or "UNKNOWN"),
            }
        return out

    def events(self, machine_uid: str, limit: int = 100) -> List[dict]:
        res = self.gateway.read_sql(
            "SELECT machine_uid, ts, kind, lab_id, test_name, value, detail "
            "FROM lem_machine_log WHERE machine_uid = ? "
            "ORDER BY ts DESC LIMIT ?", [machine_uid, int(limit)])
        if res.get("error"):
            return []
        return [dict(row) for row in res.get("rows") or []]

    def recent_events(self, limit: int = 50) -> List[dict]:
        res = self.gateway.read_sql(
            "SELECT machine_uid, ts, kind, lab_id, test_name, value, detail "
            "FROM lem_machine_log ORDER BY ts DESC LIMIT ?", [int(limit)])
        if res.get("error"):
            return []
        return [dict(row) for row in res.get("rows") or []]

    def set_override(self, machine_uid: str, override: str,
                     comment: str = "") -> None:
        override = (override or "").strip().upper() if override else ""
        if override not in VALID_OVERRIDES:
            raise ValueError(
                f"Override must be one of {VALID_OVERRIDES!r}, got {override!r}")
        from datetime import datetime

        # The DDL is deliberately NOT guarded: `CREATE TABLE IF NOT EXISTS` is
        # a declaration, it is retried on the next call, and a refusal here will
        # be reported by the INSERT below anyway — which is the statement that
        # carries the operator's decision.
        self.gateway.sql(CONTROL_DDL)
        # A manual override is how an instrument is taken OUT of service.
        # Reported as applied and silently dropped, the bench stays green on the
        # floor and somebody runs a customer sample on a machine that is broken.
        check_write(self.gateway.sql(
            "INSERT INTO lem_machine_control (machine_uid, manual_override, "
            "comment, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(machine_uid) DO UPDATE SET "
            "manual_override=excluded.manual_override, "
            "comment=excluded.comment, updated_at=excluded.updated_at",
            [machine_uid, override, comment, datetime.now().isoformat()]),
            what=("the override was not cleared" if not override
                  else f"{machine_uid} was not set to {override}"))
