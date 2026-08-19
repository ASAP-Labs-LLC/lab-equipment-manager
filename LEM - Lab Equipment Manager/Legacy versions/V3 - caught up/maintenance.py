#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maintenance management helpers for Lab Manager."""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

DATE_FMT = "%Y-%m-%d"


@dataclass
class MaintenanceTemplate:
    id: str
    box_uid: str
    box_title: str
    name: str
    kind: str               # "calibration" or "pm"
    start_date: str
    repeat_value: int
    repeat_unit: str        # days / weeks / months / years
    next_due: str
    status: str             # GREEN / SOON / DUE / OVERDUE / IN_PROGRESS
    notes: str = ""

    def due_datetime(self) -> datetime:
        try:
            return datetime.fromisoformat(self.next_due)
        except Exception:
            return datetime.now()


@dataclass
class MaintenanceLogEntry:
    timestamp: str
    box_uid: str
    box_title: str
    task_id: str
    task_name: str
    action: str             # start / complete / comment / status
    user: str
    comment: str


def _add_interval(base: datetime, value: int, unit: str) -> datetime:
    unit = unit.lower()
    if unit.startswith("day"):
        return base + timedelta(days=value)
    if unit.startswith("week"):
        return base + timedelta(weeks=value)
    if unit.startswith("month"):
        month = base.month - 1 + value
        year = base.year + month // 12
        month = month % 12 + 1
        day = min(base.day, [31,
                              29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                              31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
        return base.replace(year=year, month=month, day=day)
    if unit.startswith("year"):
        try:
            return base.replace(year=base.year + value)
        except ValueError:
            # handle Feb 29 etc
            return base.replace(month=2, day=28, year=base.year + value)
    # default fallback days
    return base + timedelta(days=value)


class MaintenanceManager:
    """Manage maintenance templates and log storage."""

    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
        self.templates_path = os.path.join(base_dir, "PM & Calibration.csv")
        self.log_path = os.path.join(base_dir, "maintenance_log.csv")
        os.makedirs(base_dir, exist_ok=True)
        self.templates: Dict[str, MaintenanceTemplate] = {}
        self.log: List[MaintenanceLogEntry] = []
        self._load()

    # ----- persistence -----
    def _load(self) -> None:
        self.templates.clear(); self.log.clear()
        if os.path.exists(self.templates_path):
            with open(self.templates_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    tpl = MaintenanceTemplate(
                        id=row.get("id", ""),
                        box_uid=row.get("box_uid", ""),
                        box_title=row.get("box_title", ""),
                        name=row.get("name", ""),
                        kind=row.get("kind", "pm"),
                        start_date=row.get("start_date", datetime.now().strftime(DATE_FMT)),
                        repeat_value=int(row.get("repeat_value", "1") or 1),
                        repeat_unit=row.get("repeat_unit", "months"),
                        next_due=row.get("next_due", row.get("start_date", datetime.now().strftime(DATE_FMT))),
                        status=row.get("status", "GREEN"),
                        notes=row.get("notes", ""),
                    )
                    self.templates[tpl.id] = tpl
        if os.path.exists(self.log_path):
            with open(self.log_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    entry = MaintenanceLogEntry(
                        timestamp=row.get("timestamp", datetime.now().isoformat()),
                        box_uid=row.get("box_uid", ""),
                        box_title=row.get("box_title", ""),
                        task_id=row.get("task_id", ""),
                        task_name=row.get("task_name", ""),
                        action=row.get("action", "status"),
                        user=row.get("user", ""),
                        comment=row.get("comment", ""),
                    )
                    self.log.append(entry)
        self.refresh_statuses(save=False)

    def _save_templates(self) -> None:
        fieldnames = [
            "id", "box_uid", "box_title", "name", "kind", "start_date",
            "repeat_value", "repeat_unit", "next_due", "status", "notes"
        ]
        with open(self.templates_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for tpl in self.templates.values():
                writer.writerow({
                    "id": tpl.id,
                    "box_uid": tpl.box_uid,
                    "box_title": tpl.box_title,
                    "name": tpl.name,
                    "kind": tpl.kind,
                    "start_date": tpl.start_date,
                    "repeat_value": tpl.repeat_value,
                    "repeat_unit": tpl.repeat_unit,
                    "next_due": tpl.next_due,
                    "status": tpl.status,
                    "notes": tpl.notes,
                })

    def _append_log(self, entry: MaintenanceLogEntry) -> None:
        file_exists = os.path.exists(self.log_path)
        fieldnames = ["timestamp", "box_uid", "box_title", "task_id", "task_name", "action", "user", "comment"]
        with open(self.log_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerow(entry.__dict__)
        self.log.append(entry)

    # ----- utilities -----
    def refresh_statuses(self, save: bool = True) -> None:
        now = datetime.now()
        for tpl in self.templates.values():
            if tpl.status == "IN_PROGRESS":
                continue
            due = tpl.due_datetime()
            if due.date() < now.date():
                tpl.status = "OVERDUE"
            elif due.date() == now.date():
                tpl.status = "DUE"
            else:
                delta = (due - now).days
                tpl.status = "SOON" if delta <= 3 else "GREEN"
        if save:
            self._save_templates()

    # ----- accessors -----
    def get_tasks(self, box_uid: Optional[str] = None) -> List[MaintenanceTemplate]:
        self.refresh_statuses(save=False)
        items = list(self.templates.values())
        if box_uid:
            items = [tpl for tpl in items if tpl.box_uid == box_uid]
        items.sort(key=lambda tpl: tpl.due_datetime())
        return items

    def get_comments(self, box_uid: Optional[str] = None) -> List[MaintenanceLogEntry]:
        comments = [e for e in self.log if e.action == "comment"]
        if box_uid:
            comments = [e for e in comments if e.box_uid == box_uid]
        comments.sort(key=lambda e: e.timestamp, reverse=True)
        return comments

    # ----- modification -----
    def create_task(self, box_uid: str, box_title: str, name: str, kind: str,
                    start_date: datetime, repeat_value: int, repeat_unit: str) -> MaintenanceTemplate:
        tpl_id = f"mt_{int(datetime.now().timestamp()*1000)}"
        next_due = start_date.strftime(DATE_FMT)
        tpl = MaintenanceTemplate(
            id=tpl_id,
            box_uid=box_uid,
            box_title=box_title,
            name=name,
            kind=kind,
            start_date=start_date.strftime(DATE_FMT),
            repeat_value=repeat_value,
            repeat_unit=repeat_unit,
            next_due=next_due,
            status="GREEN",
        )
        self.templates[tpl.id] = tpl
        self._save_templates()
        return tpl

    def start_task(self, task_id: str) -> Optional[MaintenanceTemplate]:
        tpl = self.templates.get(task_id)
        if not tpl:
            return None
        tpl.status = "IN_PROGRESS"
        self._save_templates()
        self._append_log(MaintenanceLogEntry(
            timestamp=datetime.now().isoformat(timespec='seconds'),
            box_uid=tpl.box_uid,
            box_title=tpl.box_title,
            task_id=tpl.id,
            task_name=tpl.name,
            action="start",
            user="",
            comment="",
        ))
        return tpl

    def complete_task(self, task_id: str, user: str, comment: str) -> Optional[MaintenanceTemplate]:
        tpl = self.templates.get(task_id)
        if not tpl:
            return None
        now = datetime.now()
        tpl.status = "GREEN"
        next_due = _add_interval(now, tpl.repeat_value, tpl.repeat_unit)
        tpl.next_due = next_due.strftime(DATE_FMT)
        self._save_templates()
        self._append_log(MaintenanceLogEntry(
            timestamp=now.isoformat(timespec='seconds'),
            box_uid=tpl.box_uid,
            box_title=tpl.box_title,
            task_id=tpl.id,
            task_name=tpl.name,
            action="complete",
            user=user,
            comment=comment,
        ))
        return tpl

    def add_comment(self, box_uid: str, box_title: str, comment: str, user: str = "") -> None:
        if not comment.strip():
            return
        self._append_log(MaintenanceLogEntry(
            timestamp=datetime.now().isoformat(timespec='seconds'),
            box_uid=box_uid,
            box_title=box_title,
            task_id="",
            task_name="",
            action="comment",
            user=user,
            comment=comment.strip(),
        ))

    def remove_task(self, task_id: str) -> None:
        if task_id in self.templates:
            del self.templates[task_id]
            self._save_templates()
