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

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, List, Optional

from labcore_result import (
    LabCoreError,
    LabCoreRefused,
    LabCoreUnavailable,
    confirm_write,
    rows,
)

STATUS_COLORS = {
    "GREEN": "#21c071",
    "YELLOW": "#f5c542",
    "RED": "#f85b5b",
    "DEAD-LINE": "#0f172a",
    "SERVICE": "#8d99ae",
    "UNKNOWN": "#718096",
}

VALID_OVERRIDES = ("", "SERVICE", "DEAD-LINE")


# ── What this store raises ───────────────────────────────────────────────────
#
# Every `gateway.sql()` here used to be fire-and-forget. LabCore's write queue
# serialises at ~1.5 writes/sec and refuses past 100 pending by ANSWERING —
# no exception, no "error" key — so a refused write looked exactly like a
# successful one and the operator was told their QC band was saved. On this
# table that is the worst version of the bug in the app: `lem_qc_specs` is what
# every station module reads to judge its own instrument, so a dropped write
# means a bench is checked against a band the lab believes it set and did not.


class QcSpecStoreError(LabCoreError):
    """Something this store asked LabCore to do cannot be reported as done.

    Named so a route can catch this store specifically, and a subclass of
    `LabCoreError` so a route that folds every LabCore problem into one status
    still catches it with a single `except`. The two below keep the distinction
    `labcore_result` draws, because a route has to tell "ask again in a moment"
    apart from "LabCore answered and the band was not written" to choose between
    503 and 502 — and because they subclass the labcore_result pair as well, a
    caller may equally catch `LabCoreRefused` and get this.
    """


class QcSpecUnavailable(QcSpecStoreError, LabCoreUnavailable):
    """LabCore could not be asked, so the QC bands are unknown — not empty."""


class QcSpecRefused(QcSpecStoreError, LabCoreRefused):
    """LabCore answered, and the write did not happen."""


@contextmanager
def _doing(what: str):
    """Re-label `labcore_result`'s verdict with the operation that failed.

    "LabCore did not acknowledge the write" is true and useless on a floor;
    "Could not save the QC band for m1 / Cloud Point" is what an operator needs
    to see before running another standard against it.
    """
    try:
        yield
    except QcSpecStoreError:
        raise                      # already labelled; do not wrap twice
    except LabCoreUnavailable as exc:
        raise QcSpecUnavailable("Could not {}: {}".format(what, exc)) from exc
    except LabCoreRefused as exc:
        raise QcSpecRefused("Could not {}: {}".format(what, exc)) from exc


def _sql(gateway, sql: str, args=None) -> dict:
    """Issue one write, turning a RAISED transport error into an ANSWER.

    `confirm_write(gateway.sql(...))` reads the answer but leaves the CALL
    bare, so a socket error — a write that equally did not happen — escaped
    past every `except QcSpecStoreError` as a raw OSError and became a bare
    500. "Internal Server Error" does not tell an operator whether the band was
    saved. Handing it back in the shape `labcore_result` already refuses keeps
    one rule instead of two.
    """
    try:
        return gateway.sql(sql, args or [])
    except Exception as exc:                       # transport, not logic
        return {"error": "LabCore could not be written to ({0}: {1})".format(
            type(exc).__name__, exc)}


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
        """Make sure `lem_qc_specs` exists, or say why it might not.

        `_schema_ready` is set only after the CREATE is ACKNOWLEDGED. Caching it
        on an unread answer is the same bug one level up: a refused CREATE would
        be remembered as done, and every save for the rest of the process would
        write into a table that is not there.
        """
        if self._schema_ready:
            return
        with _doing("create lem_qc_specs"):
            confirm_write(_sql(self.gateway, QC_SPECS_DDL))
        self._schema_ready = True

    def save(self, spec: QcSpec) -> None:
        if not spec.test_name.strip():
            raise ValueError("A QC spec needs a LabCore test method.")
        if spec.std_dev < 0:
            raise ValueError("Standard deviation cannot be negative.")
        if spec.k <= 0:
            raise ValueError("k must be greater than zero.")
        self.ensure_schema()
        with _doing("save the QC band for {} / {}".format(
                spec.machine_uid, spec.test_name.strip())):
            confirm_write(_sql(
                self.gateway,
                "INSERT INTO lem_qc_specs (machine_uid, test_name, sample_id, "
                "expected, std_dev, k, units) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(machine_uid, test_name) DO UPDATE SET "
                "sample_id=excluded.sample_id, expected=excluded.expected, "
                "std_dev=excluded.std_dev, k=excluded.k, units=excluded.units",
                [spec.machine_uid, spec.test_name.strip(),
                 spec.sample_id.strip(), float(spec.expected),
                 float(spec.std_dev), float(spec.k), spec.units],
            ))

    def delete(self, machine_uid: str, test_name: str) -> None:
        """Remove a band. Raises unless LabCore says the DELETE ran.

        `confirm_write`, not `wrote_rows`: matching no rows is a fact about the
        data (the band was already gone) and the caller asked for it to be gone.
        Not being told whether it ran is the failure — a band left in place while
        the floor says it was removed keeps a bench judging against it.
        """
        self.ensure_schema()
        with _doing("delete the QC band for {} / {}".format(
                machine_uid, test_name)):
            confirm_write(_sql(
                self.gateway,
                "DELETE FROM lem_qc_specs WHERE machine_uid = ? "
                "AND test_name = ?", [machine_uid, test_name]))

    def list_specs(self, machine_uid: Optional[str] = None,
                   *, missing_ok: bool = True) -> List[QcSpec]:
        """The stored bands, or an exception saying why they are not known.

        MISSING TABLE MAY DEGRADE TO EMPTY (`missing_ok`, the default): nothing
        has ever saved a band, so "no QC assigned" is the truth — the honest grey
        state described in CLAUDE.md, not a guess.

        EVERY OTHER ERROR RAISES. This used to be `if res.get("error"): return
        []`, which during a routine LabCore blip made the floor report "No QC
        assigned" for instruments it was actively judging — the same lie
        `lem_machine_specs` was added to stop telling.

        Pass `missing_ok=False` from a path that is about to WRITE off the back
        of this list: there, "could not ask" served as "does not exist" is how a
        save becomes a 404 about something real.

        A READ DECLARES NOTHING (2026-08-25). The first cut of this called a
        best-effort `_schema_for_read()` which swallowed a refusal but still
        ISSUED the CREATE — one more op per read into a queue that was already
        past 100 pending, forever, since the flag can never latch while it is
        being refused. The declaration belongs to `save()`/`delete()`, which
        genuinely need the table before they INSERT. A SELECT says "no such
        table" for itself, and that is the one error `rows()` may call empty.
        """
        if machine_uid:
            res = self.gateway.read_sql(
                "SELECT machine_uid, test_name, sample_id, expected, std_dev, "
                "k, units FROM lem_qc_specs WHERE machine_uid = ? "
                "ORDER BY test_name", [machine_uid])
        else:
            res = self.gateway.read_sql(
                "SELECT machine_uid, test_name, sample_id, expected, std_dev, "
                "k, units FROM lem_qc_specs ORDER BY machine_uid, test_name")
        with _doing("read the QC bands"):
            return [QcSpec.from_row(row)
                    for row in rows(res, missing_ok=missing_ok)]

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
        # MISSING TABLE MAY DEGRADE TO EMPTY: no station module has ever
        # reported, so there genuinely are no machines to list.
        # ANYTHING ELSE RAISES. This list is not only shown — `plan_import`
        # matches spreadsheet rows against it and then WRITES the completions it
        # matched, so a blip degraded to `[]` would report every historic PM as
        # "unmatched equipment" and import nothing, about machines that exist.
        with _doing("read the machine list"):
            listed = rows(res)
        machines = []
        for row in listed:
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
        # MISSING TABLE MAY DEGRADE TO EMPTY: the modules create and own this
        # table, so no table means no module has ever run and nothing is live.
        # ANYTHING ELSE RAISES, because this answer GUARDS A DELETE: the config
        # picker marks a row `in_use` from a fresh beat, and an empty map during
        # a blip would say "no parser is live on any bench" and clear the way to
        # delete the configuration of a machine that is running right now.
        with _doing("read the module heartbeats"):
            beats = rows(res)
        return {str(r.get("machine_uid")): {
                    "last_poll": str(r.get("last_poll") or "") or None,
                    "watching": str(r.get("watching") or "")}
                for r in beats}

    def last_activity(self) -> Dict[str, str]:
        """Newest log entry per machine. The status row is only rewritten
        when the status changes, so it is NOT a liveness signal — this is."""
        res = self.gateway.read_sql(
            "SELECT machine_uid, MAX(ts) AS ts FROM lem_machine_log "
            "GROUP BY machine_uid")
        # MISSING TABLE MAY DEGRADE TO EMPTY: nothing has ever been logged, so
        # "no activity" is the truth rather than a shrug.
        # ANYTHING ELSE RAISES: this is a liveness signal, and an empty map reads
        # as "every bench has gone quiet" — the lab-wide alarm state, invented
        # out of one timed-out read.
        with _doing("read the last activity per machine"):
            latest = rows(res)
        return {str(r.get("machine_uid")): str(r.get("ts") or "")
                for r in latest if r.get("ts")}

    def sub_statuses(self) -> Dict[str, dict]:
        """QC / PM / CAL per machine, as the station modules publish them.
        Machines that haven't reported the breakdown read UNKNOWN."""
        res = self.gateway.read_sql(
            "SELECT machine_uid, qc, pm, calibration FROM lem_machine_substatus")
        # MISSING TABLE MAY DEGRADE TO EMPTY: no module has published a
        # breakdown, which the caller already renders as UNKNOWN.
        # ANYTHING ELSE RAISES: UNKNOWN across the whole floor is indistinguishable
        # from a lab where every module has stopped publishing, and this read is
        # cheap to retry.
        with _doing("read the QC/PM/calibration breakdown"):
            published = rows(res)
        out = {}
        for row in published:
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
        # MISSING TABLE MAY DEGRADE TO EMPTY: no module has ever written a log
        # row, so this machine has no history.
        # ANYTHING ELSE RAISES. An empty history panel for an instrument with
        # months of runs behind it is a claim, and one an operator acts on —
        # "this bench has never reported" is the reason people go and touch it.
        with _doing("read the history for {}".format(machine_uid)):
            return [dict(row) for row in rows(res)]

    def recent_events(self, limit: int = 50) -> List[dict]:
        res = self.gateway.read_sql(
            "SELECT machine_uid, ts, kind, lab_id, test_name, value, detail "
            "FROM lem_machine_log ORDER BY ts DESC LIMIT ?", [int(limit)])
        # MISSING TABLE MAY DEGRADE TO EMPTY: nothing has ever been logged.
        # ANYTHING ELSE RAISES — same reason as `events`, and this is the deep
        # request the log viewer makes, where a silently truncated (here: empty)
        # answer reads as a quiet lab.
        with _doing("read the recent activity"):
            return [dict(row) for row in rows(res)]

    def set_override(self, machine_uid: str, override: str,
                     comment: str = "") -> None:
        override = (override or "").strip().upper() if override else ""
        if override not in VALID_OVERRIDES:
            raise ValueError(
                f"Override must be one of {VALID_OVERRIDES!r}, got {override!r}")
        from datetime import datetime

        # Both writes are confirmed. This is a command TO a bench: the module
        # polls `lem_machine_control` and takes itself to SERVICE / DEAD-LINE.
        # Dropped silently, the floor shows an instrument as out of service
        # while the bench keeps running samples on it — and the reverse, an
        # unheard "clear", leaves a healthy machine locked out.
        with _doing("create lem_machine_control"):
            confirm_write(_sql(self.gateway, CONTROL_DDL))
        with _doing("set the override for {}".format(machine_uid)):
            confirm_write(_sql(
                self.gateway,
                "INSERT INTO lem_machine_control (machine_uid, "
                "manual_override, comment, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(machine_uid) DO UPDATE SET "
                "manual_override=excluded.manual_override, "
                "comment=excluded.comment, updated_at=excluded.updated_at",
                [machine_uid, override, comment,
                 datetime.now().isoformat()]))
