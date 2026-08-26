#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lab_schedule.py — when the lab is actually open.

A station module proves it is alive by checking in (`lem_machine_heartbeat`).
Absence of a heartbeat therefore means one of two very different things: the
module died, or nobody is here. Without knowing the lab's hours the floor has
to guess, and it guessed "stopped" — so every Monday morning opened on a wall
of red instruments that were behaving perfectly.

This holds the opening schedule: which weekdays the lab runs, optional hours
either side, and a list of holidays. Outside those, a quiet module is reported
`closed` rather than `stopped`. A module that IS checking in stays `running` —
a holiday never makes a live module dead.

Stored in LabCore like everything else, so every screen agrees.

Every answer LabCore gives goes through `labcore_result` (2026-08-24). This file
used to be the worst offender in the app: `ensure_schema` wrapped both CREATEs in
`except Exception: pass`, `load` wrapped both reads the same way and then asked
`if not res.get("error")` — the exact test `labcore_result` exists to abolish,
because LabCore's write queue refuses past ~100 pending BY ANSWERING, with no
exception and not necessarily an "error" key. Four writes (the schedule upsert,
the holiday wipe, and each holiday insert) reported success without ever reading
the answer.

What that cost in practice: the working-hours panel says saved, the row is not
there, and next Monday the floor is red again — the precise symptom this module
was written to remove.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Dict, List, Optional

from labcore_result import (LabCoreError, LabCoreRefused, LabCoreUnavailable,
                            confirm_write, wrote_rows)
from labcore_result import rows as read_rows

SCHEDULE_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_lab_schedule ("
    "id INTEGER PRIMARY KEY, working_days TEXT, opens TEXT, closes TEXT)"
)

HOLIDAY_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_lab_holidays ("
    "day TEXT PRIMARY KEY, name TEXT)"
)

# Monday-to-Friday, all hours. The safe default: it only ever *suppresses*
# a false "stopped", so being wrong here costs a missed alert on a weekend,
# never a spurious one mid-week.
DEFAULT_WORKING_DAYS = [0, 1, 2, 3, 4]

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                 "Saturday", "Sunday")


class ScheduleWriteError(LabCoreRefused):
    """A working-hours or holiday write LabCore did not acknowledge.

    A subclass of `LabCoreRefused` so a route can catch this, `LabCoreRefused`,
    or `LabCoreError` and get the right thing either way.
    """


def parse_hhmm(raw: str) -> Optional[time]:
    """"07:30" → time(7, 30). Blank means "no bound"."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        hh, mm = text.split(":")[:2]
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        raise ValueError(f"{raw!r} is not a time — use HH:MM, e.g. 07:30.")


def parse_day(raw: str) -> str:
    """Validate an ISO date and hand it back normalised."""
    text = (raw or "").strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValueError(f"{raw!r} is not a date — use YYYY-MM-DD.")


@dataclass
class LabSchedule:
    """The lab's working pattern."""

    working_days: List[int] = field(
        default_factory=lambda: list(DEFAULT_WORKING_DAYS))
    opens: str = ""                       # "" = from midnight
    closes: str = ""                      # "" = until midnight
    holidays: Dict[str, str] = field(default_factory=dict)

    def validate(self) -> "LabSchedule":
        for day in self.working_days:
            if int(day) not in range(7):
                raise ValueError(
                    f"{day!r} is not a weekday — use 0 (Monday) to 6 (Sunday).")
        parse_hhmm(self.opens)
        parse_hhmm(self.closes)
        for day in self.holidays:
            parse_day(day)
        return self

    # ── the question everything else asks ──────────────────────────────
    def is_open(self, when: Optional[datetime] = None) -> bool:
        return not self.why_closed(when)

    def why_closed(self, when: Optional[datetime] = None) -> str:
        """Empty string when open; otherwise a reason fit to show an operator."""
        when = when or datetime.now()
        holiday = self.holidays.get(when.date().isoformat())
        if holiday:
            return f"Lab closed — {holiday}"
        weekday = when.weekday()
        if weekday not in [int(d) for d in self.working_days]:
            label = "the weekend" if weekday >= 5 else WEEKDAY_NAMES[weekday]
            return f"Lab closed — {label}"
        opens, closes = parse_hhmm(self.opens), parse_hhmm(self.closes)
        clock = when.time()
        if opens and clock < opens:
            return f"Lab closed — opens at {self.opens}"
        if closes and clock >= closes:
            return f"Lab closed — closed at {self.closes}"
        return ""

    def to_dict(self, when: Optional[datetime] = None) -> dict:
        when = when or datetime.now()
        reason = self.why_closed(when)
        return {"working_days": [int(d) for d in self.working_days],
                "opens": self.opens, "closes": self.closes,
                "holidays": dict(self.holidays),
                "open_now": not reason,
                "closed_reason": reason}


class LabScheduleStore:
    """Owns `lem_lab_schedule` + `lem_lab_holidays`.

    It used to promise "never raises on a LabCore outage: a floor that can't
    render because the calendar is unreachable is worse than one that assumes
    Monday-to-Friday". Half of that is still true and half of it was a bug.

    The bug: `save()` in web_app fills the fields the operator did not type from
    `load()`. So a `load()` that quietly degrades to defaults during a blip means
    someone editing only the opening time also resets the lab's working days to
    Mon-Fri and drops every holiday from the form they are about to POST back —
    a read deciding a write, served from "could not ask". `load()` therefore
    raises now.

    The true half survives, but it lives in the ROUTE now, not here. There was
    a `load(degrade_to_default=True)` for it; nothing ever called it, and its
    docstring described a rule no code exercised — which is the same failure
    mode as the invented refusal shape, one layer down. `/api/schedule` does
    the degrade itself and ships `known: false` with it, which the flag could
    not do: a plausible week that does not say it is a guess is how a lab that
    works Saturdays saw its own hours quietly replaced by Mon-Fri.
    """

    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self._ready = False

    # ── talking to LabCore ────────────────────────────────────────────
    def _write(self, sql: str, args: Optional[list] = None) -> dict:
        try:
            res = self.gateway.sql(sql, args or [])
        except Exception as exc:                     # transport, not logic
            raise ScheduleWriteError(
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
            raise ScheduleWriteError(str(exc), res) from exc
        return res

    def _write_rows(self, sql: str, args: Optional[list] = None) -> int:
        """Row count of a confirmed write. 0 means "already gone", not "failed"
        — removing a holiday somebody else removed first still happened."""
        return wrote_rows(self._write(sql, args))

    def _read(self, sql: str, args: Optional[list] = None,
              missing_ok: bool = False) -> List[dict]:
        try:
            res = self.gateway.read_sql(sql, args or [])
        except Exception as exc:
            raise LabCoreUnavailable(
                f"LabCore could not be read ({type(exc).__name__}: "
                f"{exc})") from exc
        # `missing_ok=False` by default: after `ensure_schema` has confirmed
        # both CREATEs, a "no such table" would contradict an acknowledged
        # write rather than mean "nothing created yet". `load()` asks for the
        # other reading, because it no longer declares anything — see there.
        return read_rows(res, missing_ok=missing_ok)

    def ensure_schema(self) -> None:
        """Declare both tables, and raise if LabCore will not.

        The old body swallowed everything with `except Exception: pass` and the
        comment "retried next call; defaults meanwhile" — but `_ready` was set
        inside the try, so a refusal was retried forever while `save()` went on
        INSERTing into tables that might not exist and telling the operator it
        had worked. Now `_ready` is set only after both CREATEs are
        acknowledged, and a store that cannot declare its tables says so.
        """
        if self._ready:
            return
        self._write(SCHEDULE_DDL)
        self._write(HOLIDAY_DDL)
        self._ready = True

    def load(self) -> LabSchedule:
        """The lab's hours as LabCore holds them. Raises if it cannot be asked.

        A READ DECLARES NOTHING (2026-08-25). This called `ensure_schema()`,
        which raises when a CREATE is refused — so a full WRITE queue made the
        lab's opening hours unreadable, for two tables that have existed for
        months, and pushed two more statements into the queue on the way. That
        is precisely the regression `tests/test_reads_survive_a_full_write_
        queue.py` was written for; this was the read path it missed.

        The two tables genuinely may not exist, on a LabCore where LEM has
        never saved its hours. `missing_ok=True` is the honest reading of that:
        a lab that has never set its hours has the default week. Every other
        read failure still raises, because `save()` fills the fields the
        operator did not type from this, and a degraded read would post back a
        Mon-Fri week with every holiday deleted.
        """
        try:
            schedule = LabSchedule()
            rows = self._read(
                "SELECT working_days, opens, closes FROM lem_lab_schedule "
                "WHERE id = 1", missing_ok=True)
            if rows:
                row = rows[0]
                try:
                    days = json.loads(row.get("working_days") or "null")
                except (TypeError, ValueError):
                    # A hand-edited blob is bad data, not an unreachable
                    # LabCore: LabCore answered, the answer is unusable, and
                    # falling back to the default week is the honest reading.
                    days = None
                if isinstance(days, list) and days:
                    schedule.working_days = [int(d) for d in days]
                schedule.opens = str(row.get("opens") or "")
                schedule.closes = str(row.get("closes") or "")
            schedule.holidays = {
                str(r.get("day")): str(r.get("name") or "")
                for r in self._read(
                    "SELECT day, name FROM lem_lab_holidays ORDER BY day",
                    missing_ok=True)
                if r.get("day")}
            return schedule
        except LabCoreError:
            # No `degrade_to_default` any more. The one caller that wants a
            # plausible week does it in the route, where it can also say
            # `known: false` — see web_app.api_schedule.
            raise

    def save(self, schedule: LabSchedule) -> LabSchedule:
        schedule.validate()
        self.ensure_schema()
        self._write(
            "INSERT INTO lem_lab_schedule (id, working_days, opens, closes) "
            "VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "working_days=excluded.working_days, opens=excluded.opens, "
            "closes=excluded.closes",
            [json.dumps([int(d) for d in schedule.working_days]),
             schedule.opens, schedule.closes])
        # Holidays are a set, not a patch: saving a schedule that names them
        # replaces the list, so removing one in the UI actually removes it.
        #
        # UPSERT FIRST, PRUNE LAST (2026-08-25) — the same shape, and the same
        # fix, as `QcTargetStore.assign` and `db_config_store._rewrite_rows`.
        # This used to be `DELETE FROM lem_lab_holidays` followed by one INSERT
        # per holiday, and there is no transaction across them: LabCore's queue
        # takes a statement at a time. Every statement was CONFIRMED, so a
        # refusal in between raised — but it raised over a holiday list that
        # had already been emptied, and the lab is then reported open on
        # Christmas Day by a schedule that looks perfectly normal.
        #
        # Now a refusal during the upserts leaves the old holidays plus
        # whatever landed, and a refused prune leaves one holiday too many.
        # Both are visible (the lab reads closed on a day it is open, and
        # somebody says so) and both are fixed by saving again. The old order
        # could lose a day nobody notices until it is worked.
        for day, name in schedule.holidays.items():
            self.add_holiday(day, name)
        keep = [parse_day(day) for day in schedule.holidays]
        if not keep:
            # Removing the last holiday is a deliberate instruction, not an
            # absence of one — see the note this replaces.
            self._write("DELETE FROM lem_lab_holidays")
            return schedule
        holes = ",".join("?" * len(keep))
        self._write(
            "DELETE FROM lem_lab_holidays WHERE day NOT IN ({0})".format(holes),
            keep)
        return schedule

    def add_holiday(self, day: str, name: str = "") -> None:
        day = parse_day(day)
        self.ensure_schema()
        self._write(
            "INSERT INTO lem_lab_holidays (day, name) VALUES (?, ?) "
            "ON CONFLICT(day) DO UPDATE SET name=excluded.name",
            [day, (name or "").strip() or "Holiday"])

    def remove_holiday(self, day: str) -> int:
        """Returns the rows removed. 0 is fine — a holiday deleted on another
        screen a moment ago is gone, which is what was asked for."""
        self.ensure_schema()
        return self._write_rows(
            "DELETE FROM lem_lab_holidays WHERE day = ?", [parse_day(day)])
