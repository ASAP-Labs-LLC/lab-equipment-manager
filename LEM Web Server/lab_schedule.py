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
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time
from labcore_gateway import check_write
from typing import Dict, List, Optional

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

    Never raises on a LabCore outage: a floor that can't render because the
    calendar is unreachable is worse than one that assumes Monday-to-Friday.
    """

    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self._ready = False

    def ensure_schema(self) -> None:
        if self._ready:
            return
        try:
            self.gateway.sql(SCHEDULE_DDL)
            self.gateway.sql(HOLIDAY_DDL)
            self._ready = True
        except Exception:
            pass                       # retried next call; defaults meanwhile

    def load(self) -> LabSchedule:
        self.ensure_schema()
        schedule = LabSchedule()
        try:
            res = self.gateway.read_sql(
                "SELECT working_days, opens, closes FROM lem_lab_schedule "
                "WHERE id = 1")
        except Exception:
            return schedule
        rows = [] if res.get("error") else (res.get("rows") or [])
        if rows:
            row = rows[0]
            try:
                days = json.loads(row.get("working_days") or "null")
            except (TypeError, ValueError):
                days = None
            if isinstance(days, list) and days:
                schedule.working_days = [int(d) for d in days]
            schedule.opens = str(row.get("opens") or "")
            schedule.closes = str(row.get("closes") or "")
        try:
            res = self.gateway.read_sql(
                "SELECT day, name FROM lem_lab_holidays ORDER BY day")
        except Exception:
            return schedule
        if not res.get("error"):
            schedule.holidays = {str(r.get("day")): str(r.get("name") or "")
                                 for r in res.get("rows") or [] if r.get("day")}
        return schedule

    def save(self, schedule: LabSchedule) -> LabSchedule:
        schedule.validate()
        self.ensure_schema()
        # Opening hours decide whether a silent bench reads as STOPPED or as
        # CLOSED. Saved-but-not-saved is either a floor full of alarms nobody
        # asked for, or none on the day they were wanted.
        check_write(self.gateway.sql(
            "INSERT INTO lem_lab_schedule (id, working_days, opens, closes) "
            "VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "working_days=excluded.working_days, opens=excluded.opens, "
            "closes=excluded.closes",
            [json.dumps([int(d) for d in schedule.working_days]),
             schedule.opens, schedule.closes]),
            what="the opening hours were not saved")
        # Holidays are a set, not a patch: saving a schedule that names them
        # replaces the list, so removing one in the UI actually removes it.
        if schedule.holidays:
            # Past the hours, which have already landed. Refused here, the
            # caller is told what did and did not go in rather than being left
            # to assume both halves succeeded together — there is no
            # transaction across queue ops to make that true.
            check_write(
                self.gateway.sql("DELETE FROM lem_lab_holidays"),
                what="the opening hours were saved but the holiday list "
                     "was not replaced",
                partial=True, landed=["the opening hours"],
                not_landed=["the holiday list"])
            for day, name in schedule.holidays.items():
                self.add_holiday(day, name)
        return schedule

    def add_holiday(self, day: str, name: str = "") -> None:
        day = parse_day(day)
        self.ensure_schema()
        check_write(
            self.gateway.sql(
                "INSERT INTO lem_lab_holidays (day, name) VALUES (?, ?) "
                "ON CONFLICT(day) DO UPDATE SET name=excluded.name",
                [day, (name or "").strip() or "Holiday"]),
            what=f"{day} was not added to the holiday list")

    def remove_holiday(self, day: str) -> None:
        self.ensure_schema()
        check_write(
            self.gateway.sql("DELETE FROM lem_lab_holidays WHERE day = ?",
                             [parse_day(day)]),
            what=f"{parse_day(day)} was not removed from the holiday list")
