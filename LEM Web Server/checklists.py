#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
checklists.py — opening and closing rounds.

The one V4 workflow V5 never rebuilt. The model is V4's
(`V4.0.3.1 - Beta Stable/models.py`): a checklist has a name and a due time;
items are scoped to weekdays, can be headers or subtasks, and ticking a parent
ticks its children. Every tick records who and when.

Two differences from V4, both deliberate:

* V4 kept the day's ticks in `checklist_state.json` on one PC, so two screens in
  the lab disagreed. State lives in LabCore now.
* Each day stands alone (`lem_checklist_state` is keyed by day), so yesterday's
  round can never look like today's — V4 had the same intent but the file made
  it easy to get wrong.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from labcore_gateway import LabCoreRefused, check_write
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# NOT `lem_checklists` — that name is already taken by db_config_store, which
# holds V4's AppConfig.checklists as (uid, data) blobs AND rewrites the whole
# table on every config save. Sharing it would let saving the config silently
# wipe every round in the lab.
CHECKLIST_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_checklist_defs ("
    "uid TEXT PRIMARY KEY, name TEXT NOT NULL, slot TEXT, due_time TEXT, "
    "items TEXT)"
)

STATE_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_checklist_state ("
    "day TEXT NOT NULL, checklist_uid TEXT NOT NULL, item_uid TEXT NOT NULL, "
    "checked INTEGER, user TEXT, at TEXT, value TEXT, "
    "PRIMARY KEY (day, checklist_uid, item_uid))"
)

# The state table shipped before items could record a reading. Upgrading must
# not need a hand-run migration, so add the column if it isn't there and ignore
# the "duplicate column" complaint when it is.
STATE_MIGRATIONS = (
    "ALTER TABLE lem_checklist_state ADD COLUMN value TEXT",
)

# What an item can ask for. `number` is for tracking (a cylinder trending down);
# `text` is for logging anything ("waste tank: half full").
ENTRY_TYPES = ("none", "number", "text")

SLOTS = ("opening", "closing", "other")


def _hhmm(raw: str) -> str:
    """Validate a due time. Blank is allowed — not every round has a deadline."""
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        hh, mm = text.split(":")[:2]
        if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError(f"{raw!r} is not a time — use HH:MM, e.g. 09:00.")
    return f"{int(hh):02d}:{int(mm):02d}"


@dataclass
class ChecklistItem:
    """One line of a round."""

    uid: str = ""
    text: str = ""
    # Weekday numbers (0 = Monday). EMPTY MEANS EVERY DAY: "never" is what
    # deleting the item is for.
    days_active: List[int] = field(default_factory=list)
    item_type: str = "item"            # "item" | "header" | "subtask"
    parent_uid: Optional[str] = None
    # A tick alone loses the number. V4's own round said "Check and Record Gas
    # Levels" and recorded nothing, so nobody could see a cylinder trending down.
    entry_type: str = "none"           # "none" | "number" | "text"
    units: str = ""                    # shown beside the field, e.g. PSI

    def to_dict(self) -> dict:
        return {"uid": self.uid, "text": self.text,
                "days_active": [int(d) for d in self.days_active],
                "item_type": self.item_type, "parent_uid": self.parent_uid,
                "entry_type": self.entry_type, "units": self.units}

    @classmethod
    def from_dict(cls, data: dict) -> "ChecklistItem":
        data = data or {}
        days = data.get("days_active") or []
        if not isinstance(days, list):
            days = []
        return cls(uid=str(data.get("uid") or ""),
                   text=str(data.get("text") or ""),
                   days_active=[int(d) for d in days
                                if str(d).strip().lstrip("-").isdigit()
                                and 0 <= int(d) <= 6],
                   item_type=str(data.get("item_type") or "item"),
                   parent_uid=data.get("parent_uid") or None,
                   # An unrecognised type must not invent a widget nobody can
                   # render, so it degrades to a plain tick.
                   entry_type=(str(data.get("entry_type") or "none")
                               if str(data.get("entry_type") or "none")
                               in ENTRY_TYPES else "none"),
                   units=str(data.get("units") or ""))

    def counts_towards_completion(self) -> bool:
        """A heading isn't work — counting it makes a finished round read 80%."""
        return self.item_type != "header"


@dataclass
class Checklist:
    uid: str = ""
    name: str = ""
    slot: str = "other"                # opening | closing | other
    due_time: str = ""
    items: List[ChecklistItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"uid": self.uid, "name": self.name, "slot": self.slot,
                "due_time": self.due_time,
                "items": [i.to_dict() for i in self.items]}

    @classmethod
    def from_dict(cls, data: dict) -> "Checklist":
        data = data or {}
        raw_items = data.get("items")
        if not isinstance(raw_items, list):
            raw_items = []
        slot = str(data.get("slot") or "other").strip().lower()
        return cls(uid=str(data.get("uid") or ""),
                   name=str(data.get("name") or ""),
                   slot=slot if slot in SLOTS else "other",
                   due_time=str(data.get("due_time") or ""),
                   items=[ChecklistItem.from_dict(i) for i in raw_items])


def active_items(items: Iterable[ChecklistItem],
                 when: Optional[date] = None) -> List[ChecklistItem]:
    """The items that apply on a given day.

    Headers always show: a heading scoped off would orphan everything under it.
    """
    when = when or date.today()
    weekday = when.weekday()
    out = []
    for item in items or []:
        if item.item_type == "header" or not item.days_active:
            out.append(item)
        elif weekday in [int(d) for d in item.days_active]:
            out.append(item)
    return out


def completion(items: Sequence[ChecklistItem],
               state: Dict[str, dict]) -> Tuple[int, int, int]:
    """(checked, total, percent) over the items that count."""
    countable = [i for i in items or [] if i.counts_towards_completion()]
    total = len(countable)
    checked = sum(1 for i in countable
                  if (state or {}).get(i.uid, {}).get("checked"))
    pct = round(100 * checked / total) if total else 0
    return checked, total, pct


class ChecklistStore:
    """Owns `lem_checklist_defs` + `lem_checklist_state`.

    Reads never raise: a lab that can't reach LabCore should see an empty round
    and a warning, not a stack trace on the wall display.
    """

    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self._ready = False

    def ensure_schema(self) -> None:
        if self._ready:
            return
        try:
            self.gateway.sql(CHECKLIST_DDL)
            self.gateway.sql(STATE_DDL)
            for migration in STATE_MIGRATIONS:
                try:
                    self.gateway.sql(migration)
                except Exception:
                    pass               # already applied
            self._ready = True
        except Exception:
            pass                       # retried on the next call

    # ── definitions ────────────────────────────────────────────────────
    def all(self) -> List[Checklist]:
        self.ensure_schema()
        try:
            res = self.gateway.read_sql(
                "SELECT uid, name, slot, due_time, items FROM lem_checklist_defs "
                "ORDER BY slot, name")
        except Exception:
            return []
        out = []
        for row in ([] if res.get("error") else (res.get("rows") or [])):
            try:
                items = json.loads(row.get("items") or "[]")
                if not isinstance(items, list):
                    items = []
            except (TypeError, ValueError):
                items = []             # a hand-edited blob must not 500
            out.append(Checklist(
                uid=str(row.get("uid") or ""),
                name=str(row.get("name") or ""),
                slot=str(row.get("slot") or "other"),
                due_time=str(row.get("due_time") or ""),
                items=[ChecklistItem.from_dict(i) for i in items]))
        return out

    def get(self, uid: str) -> Optional[Checklist]:
        for cl in self.all():
            if cl.uid == uid:
                return cl
        return None

    def save(self, checklist: Checklist) -> Checklist:
        if not (checklist.name or "").strip():
            raise ValueError("A checklist needs a name.")
        checklist.due_time = _hhmm(checklist.due_time)
        checklist.uid = checklist.uid or uuid.uuid4().hex[:12]
        if checklist.slot not in SLOTS:
            checklist.slot = "other"
        for item in checklist.items:
            item.uid = item.uid or uuid.uuid4().hex[:12]
        self.ensure_schema()
        check_write(self.gateway.sql(
            "INSERT INTO lem_checklist_defs (uid, name, slot, due_time, items) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(uid) DO UPDATE SET "
            "name=excluded.name, slot=excluded.slot, "
            "due_time=excluded.due_time, items=excluded.items",
            [checklist.uid, checklist.name.strip(), checklist.slot,
             checklist.due_time,
             json.dumps([i.to_dict() for i in checklist.items])]),
            what=f"the checklist “{checklist.name.strip()}” was not saved")
        return checklist

    def delete(self, uid: str) -> None:
        self.ensure_schema()
        # Two statements, no transaction across queue ops. The DEFINITION goes
        # first on purpose: if the second is refused the checklist is gone from
        # every round and some orphan ticks remain, which is recoverable and
        # invisible. The other order leaves a live checklist whose history has
        # been erased, which is neither.
        check_write(
            self.gateway.sql("DELETE FROM lem_checklist_defs WHERE uid = ?",
                             [uid]),
            what="the checklist was not deleted")
        check_write(
            self.gateway.sql("DELETE FROM lem_checklist_state "
                             "WHERE checklist_uid = ?", [uid]),
            what="the checklist was removed but its ticks were not",
            partial=True, landed=["the checklist"],
            not_landed=["its recorded ticks"])

    # ── one day's ticks ────────────────────────────────────────────────
    def state(self, day: str) -> Dict[str, Dict[str, dict]]:
        self.ensure_schema()
        try:
            res = self.gateway.read_sql(
                "SELECT checklist_uid, item_uid, checked, user, at, value "
                "FROM lem_checklist_state WHERE day = ?", [day])
        except Exception:
            return {}
        out: Dict[str, Dict[str, dict]] = {}
        for row in ([] if res.get("error") else (res.get("rows") or [])):
            out.setdefault(str(row.get("checklist_uid")), {})[
                str(row.get("item_uid"))] = {
                    "checked": bool(row.get("checked")),
                    "user": str(row.get("user") or ""),
                    "at": str(row.get("at") or ""),
                    "value": str(row.get("value") or "")}
        return out

    def set_tick(self, checklist_uid: str, item_uid: str, checked: bool,
                 day: str, user: str, now: Optional[datetime] = None) -> None:
        self.ensure_schema()
        stamp = (now or datetime.now()).isoformat(timespec="seconds")
        # A tick is the record that the round was done. Told it was saved when
        # it was not, the archive shows a gap nobody can account for and the
        # operator has no reason to go back and tick it again.
        check_write(self.gateway.sql(
            "INSERT INTO lem_checklist_state (day, checklist_uid, item_uid, "
            "checked, user, at, value) VALUES (?, ?, ?, ?, ?, ?, "
            "COALESCE((SELECT value FROM lem_checklist_state WHERE day = ? "
            "AND checklist_uid = ? AND item_uid = ?), '')) "
            "ON CONFLICT(day, checklist_uid, item_uid) DO UPDATE SET "
            "checked=excluded.checked, user=excluded.user, at=excluded.at",
            [day, checklist_uid, item_uid, 1 if checked else 0, user, stamp,
             day, checklist_uid, item_uid]), what="the tick was not recorded")

    def set_value(self, checklist_uid: str, item_uid: str, value: str,
                  day: str, user: str,
                  now: Optional[datetime] = None) -> None:
        """Record a reading. Entering it IS doing the job, so it ticks the item
        too — making someone also tick it is a second chore that gets skipped.
        Clearing the value unticks it again."""
        self.ensure_schema()
        stamp = (now or datetime.now()).isoformat(timespec="seconds")
        text = (value or "").strip()
        check_write(self.gateway.sql(
            "INSERT INTO lem_checklist_state (day, checklist_uid, item_uid, "
            "checked, user, at, value) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(day, checklist_uid, item_uid) DO UPDATE SET "
            "checked=excluded.checked, user=excluded.user, at=excluded.at, "
            "value=excluded.value",
            [day, checklist_uid, item_uid, 1 if text else 0, user, stamp,
             text]), what="the reading was not recorded")

    def values(self, checklist_uid: str, item_uid: str,
               limit: int = 180) -> List[dict]:
        """A numeric item's readings over time, oldest first — the reason
        `number` exists. Unreadable entries are left out: a trend built from
        "about half" is not a trend."""
        self.ensure_schema()
        try:
            res = self.gateway.read_sql(
                "SELECT day, value, user FROM lem_checklist_state "
                "WHERE checklist_uid = ? AND item_uid = ? "
                "AND value IS NOT NULL AND TRIM(value) != '' "
                "ORDER BY day DESC LIMIT ?",
                [checklist_uid, item_uid, int(limit)])
        except Exception:
            return []
        out = []
        for row in ([] if res.get("error") else (res.get("rows") or [])):
            try:
                number = float(str(row.get("value")).strip())
            except (TypeError, ValueError):
                continue
            out.append({"day": str(row.get("day") or ""), "value": number,
                        "user": str(row.get("user") or "")})
        out.sort(key=lambda p: p["day"])
        return out

    def toggle(self, checklist: Checklist, item_uid: str, checked: bool,
               day: str, user: str) -> List[str]:
        """Tick an item, and its children if it has any. Returns what changed."""
        touched = [item_uid]
        for item in checklist.items:
            if item.parent_uid and item.parent_uid == item_uid:
                touched.append(item.uid)
        # One statement per item, no transaction across them. Ticking a parent
        # ticks its children, so a refusal partway leaves a parent ticked over
        # children that are not — which reads on the page as a round that was
        # done when part of it was not recorded. Named rather than smoothed
        # over: `set_tick`'s own message says only "the tick was not recorded",
        # which is true of one item and misleading about the group.
        done: List[str] = []
        for uid in touched:
            try:
                self.set_tick(checklist.uid, uid, checked, day, user)
            except LabCoreRefused as exc:
                if not done:
                    raise
                raise LabCoreRefused(
                    exc.result,
                    what=f"{len(done)} of {len(touched)} item(s) were "
                         f"recorded — the rest of this group was not",
                    partial=True, landed=list(done),
                    not_landed=touched[len(done):]) from exc
            done.append(uid)
        return touched

    def import_state(self, rows: List[dict], batch: int = 100,
                     pause: float = 0.35, attempts: int = 6) -> int:
        """Bulk-load historical ticks. Returns how many actually landed.

        LabCore serialises its write queue at roughly 1.5 operations a second
        and rejects new work once ~100 are pending, with
        ``{"busy": true, "retry_after": n}``. It reports that as an error *dict*
        rather than raising — so a naive loop both floods the queue and counts
        the rejections as successes. Hence: multi-row inserts to keep the op
        count down, a pause between them, and a real back-off that re-tries a
        rejected batch instead of losing it.
        """
        if not rows:
            return 0
        self.ensure_schema()
        done = 0
        for chunk in _batched(rows, batch):
            values = ",".join(["(?, ?, ?, ?, ?, ?, ?)"] * len(chunk))
            args: List[object] = []
            for r in chunk:
                args += [r["day"], r["checklist_uid"], r["item_uid"],
                         1 if r["checked"] else 0, r.get("user") or "",
                         r.get("at") or "", r.get("value") or ""]
            sql = ("INSERT INTO lem_checklist_state (day, checklist_uid, "
                   f"item_uid, checked, user, at, value) VALUES {values} "
                   "ON CONFLICT(day, checklist_uid, item_uid) DO UPDATE SET "
                   "checked=excluded.checked, user=excluded.user, "
                   "at=excluded.at")
            for attempt in range(attempts):
                try:
                    res = self.gateway.sql(sql, args) or {}
                except Exception:
                    res = {"error": "raised"}
                if not res.get("error"):
                    done += len(chunk)
                    break
                # Busy is temporary; wait the queue's own suggestion, backing
                # off if it keeps saying no.
                wait = float(res.get("retry_after") or 2) * (attempt + 1)
                time.sleep(min(wait, 15))
            time.sleep(pause)
        return done

    def history(self, limit: int = 60) -> List[dict]:
        """Per-day tick counts, newest first — the "did we do the rounds?" view."""
        self.ensure_schema()
        try:
            res = self.gateway.read_sql(
                "SELECT day, COUNT(*) AS total, "
                "SUM(CASE WHEN checked THEN 1 ELSE 0 END) AS checked "
                "FROM lem_checklist_state GROUP BY day "
                "ORDER BY day DESC LIMIT ?", [int(limit)])
        except Exception:
            return []
        out = []
        for row in ([] if res.get("error") else (res.get("rows") or [])):
            total = int(row.get("total") or 0)
            checked = int(row.get("checked") or 0)
            out.append({"day": str(row.get("day") or ""), "total": total,
                        "checked": checked,
                        "pct": round(100 * checked / total) if total else 0})
        return out


def import_v4_checklists(text) -> List[Checklist]:
    """V4's `lab_manager_config.json` → Checklists ready to save.

    The real file carries two junk stubs alongthe two real rounds — one with a
    typo'd name — both with zero items. They are left behind: an empty checklist
    is a false start, not a round.

    The slot is inferred from the name, which is how V4 distinguished them; a
    name that says neither lands in "other" rather than guessing. Imported items
    get no entry field: V4 had no such concept, so those are added deliberately
    afterwards rather than assumed here.
    """
    if isinstance(text, dict):
        data = text
    else:
        try:
            data = json.loads(text or "{}")
        except (TypeError, ValueError):
            return []
    if not isinstance(data, dict):
        return []
    raw = data.get("checklists")
    if not isinstance(raw, list):
        return []

    out: List[Checklist] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        items = entry.get("items")
        if not name or not isinstance(items, list) or not items:
            continue
        lowered = name.lower()
        slot = ("opening" if "open" in lowered
                else "closing" if "clos" in lowered else "other")
        try:
            due = _hhmm(str(entry.get("due_time") or ""))
        except ValueError:
            due = ""
        checklist = Checklist(
            uid=str(entry.get("uid") or "") or uuid.uuid4().hex[:12],
            name=name, slot=slot, due_time=due,
            items=[ChecklistItem.from_dict(i) for i in items
                   if isinstance(i, dict) and str(i.get("text") or "").strip()])
        for item in checklist.items:
            item.uid = item.uid or uuid.uuid4().hex[:12]
        out.append(checklist)
    return out


def import_v4_state(text, checklists: Optional[List[Checklist]] = None
                    ) -> List[dict]:
    """V4's `checklist_state.json` → rows ready for `lem_checklist_state`.

    V4 kept the day's ticks in a file keyed `"{checklist_uid}|{item}"`, where
    `item` is either an item uid or — in the oldest entries — a plain integer
    index into the checklist's item list. Both are handled: the index is
    resolved against the checklist it belongs to, and dropped if that checklist
    isn't among the ones imported, because an index with no list is meaningless.

    The stored time is only `HH:MM`, so it's combined with the day to make a
    timestamp. 3096 entries across 169 days came through this.
    """
    if isinstance(text, dict):
        data = text
    else:
        try:
            data = json.loads(text or "{}")
        except (TypeError, ValueError):
            return []
    if not isinstance(data, dict):
        return []

    items_by_list: Dict[str, List[ChecklistItem]] = {
        c.uid: list(c.items) for c in (checklists or [])}

    rows: List[dict] = []
    for day, entries in data.items():
        try:
            date.fromisoformat(str(day))
        except (TypeError, ValueError):
            continue                       # not a date key
        if not isinstance(entries, dict):
            continue
        for key, val in entries.items():
            if not isinstance(val, dict) or "|" not in str(key):
                continue
            checklist_uid, _, item_key = str(key).rpartition("|")
            if not checklist_uid:
                continue
            item_uid = item_key
            if item_key.isdigit():
                # Legacy positional key: resolve it against the real list.
                items = items_by_list.get(checklist_uid)
                index = int(item_key)
                if not items or index >= len(items):
                    continue
                item_uid = items[index].uid
            hhmm = str(val.get("time") or "").strip()
            at = f"{day}T{hhmm}:00" if len(hhmm) == 5 else f"{day}T00:00:00"
            rows.append({"day": str(day), "checklist_uid": checklist_uid,
                         "item_uid": item_uid,
                         "checked": bool(val.get("checked")),
                         "user": str(val.get("user") or ""),
                         "at": at, "value": ""})
    return rows


def _unused_state_import_sql() -> str:
    return ("INSERT INTO lem_checklist_state (day, checklist_uid, item_uid, "
            "checked, user, at, value) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(day, checklist_uid, item_uid) DO UPDATE SET "
            "checked=excluded.checked, user=excluded.user, at=excluded.at")


def _batched(rows: List[dict], size: int = 200):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]
