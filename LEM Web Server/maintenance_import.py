#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
maintenance_import.py — bring years of PM/calibration history in from a sheet.

The lab's completed maintenance lives in spreadsheets that predate machine
uids, so rows are keyed by **equipment name**. That makes matching the whole
problem: the lab already has `opimpp 1`, `Optimpp 1` and `OtpiMPP 2` as three
separate registrations, so anything cleverer than an exact match would file real
history against the wrong instrument.

Hence the rules here:

* **exact name match**, case- and punctuation-sensitive. Only surrounding
  whitespace is forgiven, because that's a spreadsheet artefact rather than a
  different instrument. Unmatched rows come back named so a human decides.
* **a template** listing every active machine (with its uid) so a row can't fail
  on a typo in the first place.
* **graceful failure**: a bad row is reported with its line number; the good
  rows in the same file still land.
* **idempotent**: an entry already in the history is skipped, so re-running the
  same file changes nothing.
* an imported completion **moves the schedule** from its completion date — but
  only forwards, so importing 2023 history can't make a current machine look
  overdue.

Pure functions only. The HTTP layer in web_app.py does the reading and writing.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

COLUMNS = ("equipment", "task", "kind", "completed_date", "performed_by",
           "note")

TEMPLATE_HEADER = ["equipment", "task", "kind", "completed_date",
                   "performed_by", "note", "machine_uid (reference only)"]

KINDS = {"pm": "pm", "preventive": "pm", "preventative": "pm",
         "maintenance": "pm", "cal": "calibration",
         "calibration": "calibration"}


def _norm_header(name: str) -> str:
    return str(name or "").strip().lower().replace(" ", "_")


def parse_import_csv(text: str) -> Tuple[List[dict], List[dict]]:
    """CSV text → (rows, errors). Never raises.

    Errors carry the 1-based data line number so someone can find the row in
    their spreadsheet, and one bad row never costs the others.
    """
    text = (text or "").strip()
    if not text:
        return [], [{"line": 0, "error": "The file is empty."}]

    reader = csv.reader(io.StringIO(text))
    try:
        raw_header = next(reader)
    except StopIteration:
        return [], [{"line": 0, "error": "The file is empty."}]

    header = [_norm_header(h) for h in raw_header]
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        return [], [{"line": 0,
                     "error": "Missing column(s): " + ", ".join(missing)
                              + ". Start from the template."}]
    index = {c: header.index(c) for c in COLUMNS}

    rows: List[dict] = []
    errors: List[dict] = []
    for n, raw in enumerate(reader, start=1):
        if not any(str(cell).strip() for cell in raw):
            continue                                   # blank spreadsheet line

        def cell(name: str) -> str:
            i = index[name]
            return str(raw[i]).strip() if i < len(raw) else ""

        equipment, task = cell("equipment"), cell("task")
        kind_raw, when = cell("kind"), cell("completed_date")
        if not equipment:
            errors.append({"line": n, "error": "No equipment name."})
            continue
        if not task:
            errors.append({"line": n, "error": "No task name."})
            continue
        kind = KINDS.get(kind_raw.strip().lower())
        if not kind:
            errors.append({"line": n,
                           "error": f"Kind {kind_raw!r} is not pm or "
                                    f"calibration."})
            continue
        try:
            completed = date.fromisoformat(when).isoformat()
        except ValueError:
            errors.append({"line": n,
                           "error": f"Completed date {when!r} is not a date — "
                                    f"use YYYY-MM-DD."})
            continue
        rows.append({"line": n, "equipment": equipment, "task": task,
                     "kind": kind, "completed_date": completed,
                     "performed_by": cell("performed_by"),
                     "note": cell("note")})
    return rows, errors


def template_csv_rows(machines: Iterable[dict]) -> Tuple[List[str], List[list]]:
    """The header plus one pre-filled row per active machine.

    The name is filled in and the rest left blank: the point is that nobody has
    to type an equipment name, because a typo is the one error this whole format
    can't recover from. The uid rides along so three machines with near-identical
    names can still be told apart by eye.
    """
    rows = []
    for m in machines or []:
        title = str((m or {}).get("title") or "").strip()
        if not title:
            continue
        rows.append([title, "", "", "", "", "",
                     str((m or {}).get("machine_uid") or "")])
    rows.sort(key=lambda r: r[0].lower())
    return list(TEMPLATE_HEADER), rows


def plan_import(rows: Sequence[dict], machines: Iterable[dict],
                existing: Set[tuple], tasks: Dict[str, list]) -> dict:
    """Work out exactly what an import would do, without doing any of it.

    `existing` is a set of (machine_uid, task, completed) already in the history.
    `tasks` maps machine_uid → scheduled MaintTaskRecords, used to decide which
    schedules move.
    """
    by_name = {}
    for m in machines or []:
        title = str((m or {}).get("title") or "").strip()
        if title:
            by_name[title] = str((m or {}).get("machine_uid") or "")

    create: List[dict] = []
    skipped: List[dict] = []
    unmatched: List[dict] = []
    seen: Set[tuple] = set(existing or ())
    newest: Dict[Tuple[str, str], str] = {}

    for row in rows or []:
        uid = by_name.get(row["equipment"])
        if not uid:
            unmatched.append(row)
            continue
        key = (uid, row["task"], row["completed_date"])
        if key in seen:
            skipped.append(row)
            continue
        seen.add(key)
        create.append({"machine_uid": uid, "machine_title": row["equipment"],
                       "task": row["task"], "kind": row["kind"],
                       "completed": row["completed_date"],
                       "by": row["performed_by"], "note": row["note"],
                       "line": row["line"]})
        # Track the newest completion per machine+task so the schedule lands on
        # the latest date in the file, whatever order the rows are in.
        tkey = (uid, row["task"])
        if row["completed_date"] > newest.get(tkey, ""):
            newest[tkey] = row["completed_date"]

    reschedule: List[dict] = []
    for (uid, task_name), when in sorted(newest.items()):
        for task in tasks.get(uid) or []:
            if task.name != task_name:
                continue
            # Forwards only: importing old history must never drag a current
            # machine backwards into looking overdue.
            if when > (task.last_done or ""):
                reschedule.append({"uid": task.uid, "machine_uid": uid,
                                   "task": task.name, "last_done": when})
            break

    return {"create": create, "skipped": skipped, "unmatched": unmatched,
            "reschedule": reschedule}
