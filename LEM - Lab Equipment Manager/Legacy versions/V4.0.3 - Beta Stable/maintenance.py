#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maintenance management helpers for Lab Manager."""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

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
    status: str             # UPCOMING / SOON / DUE / OVERDUE / IN_PROGRESS
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
        self.base_dir = base_dir  # fallback location
        os.makedirs(base_dir, exist_ok=True)
        self.templates: Dict[str, MaintenanceTemplate] = {}
        self.log: List[MaintenanceLogEntry] = []
        # Map of box_uid -> directory to store its maintenance files
        self.box_dirs: Dict[str, str] = {}
        self._load()

    # ----- configuration -----
    def set_box_dirs(self, mapping: Dict[str, str]) -> None:
        # Expect mapping to directories where we will create a per-machine folder
        self.box_dirs = dict(mapping or {})
        self._load()

    # ----- persistence -----
    def _paths_for(self, box_uid: str) -> Tuple[str, str]:
        # Create a per-machine folder inside the provided directory
        base = self.box_dirs.get(box_uid)
        if base:
            # place under Maintenance/<uid>
            d = os.path.join(base, "Maintenance", box_uid)
        else:
            d = self.base_dir
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "PM & Calibration.csv"), os.path.join(d, "maintenance_log.csv")

    def _load(self) -> None:
        self.templates.clear(); self.log.clear()
        # Aggregate from all known per-machine locations
        loaded_template_paths = set()
        loaded_log_paths = set()

        # Helper to load templates
        def load_templates_from(path: str) -> None:
            if not os.path.exists(path) or path in loaded_template_paths:
                return
            loaded_template_paths.add(path)
            with open(path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
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
                            status=("UPCOMING" if row.get("status", "GREEN").upper()=="GREEN" else row.get("status", "UPCOMING")),
                            notes=row.get("notes", ""),
                        )
                    except Exception:
                        continue
                    self.templates[tpl.id] = tpl

        def load_log_from(path: str) -> None:
            if not os.path.exists(path) or path in loaded_log_paths:
                return
            loaded_log_paths.add(path)
            with open(path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
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
                    except Exception:
                        continue
                    self.log.append(entry)

        # Load from each known box directory
        for uid, base in self.box_dirs.items():
            d = os.path.join(base, "Maintenance", uid)
            load_templates_from(os.path.join(d, "PM & Calibration.csv"))
            load_log_from(os.path.join(d, "maintenance_log.csv"))

        # Also load from legacy/global base_dir for backward compatibility
        load_templates_from(os.path.join(self.base_dir, "PM & Calibration.csv"))
        load_log_from(os.path.join(self.base_dir, "maintenance_log.csv"))

        self.refresh_statuses(save=False)

    def _save_templates(self) -> None:
        fieldnames = [
            "id", "box_uid", "box_title", "name", "kind", "start_date",
            "repeat_value", "repeat_unit", "next_due", "status", "notes"
        ]
        # Group by box_uid and save to each machine folder
        grouped: Dict[str, List[MaintenanceTemplate]] = {}
        for tpl in self.templates.values():
            grouped.setdefault(tpl.box_uid, []).append(tpl)
        for box_uid, items in grouped.items():
            tpl_path, _ = self._paths_for(box_uid)
            with open(tpl_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for t in items:
                    writer.writerow({
                        "id": t.id,
                        "box_uid": t.box_uid,
                        "box_title": t.box_title,
                        "name": t.name,
                        "kind": t.kind,
                        "start_date": t.start_date,
                        "repeat_value": t.repeat_value,
                        "repeat_unit": t.repeat_unit,
                        "next_due": t.next_due,
                        "status": t.status,
                        "notes": t.notes,
                    })

    def _append_log(self, entry: MaintenanceLogEntry) -> None:
        _, log_path = self._paths_for(entry.box_uid)
        file_exists = os.path.exists(log_path)
        fieldnames = ["timestamp", "box_uid", "box_title", "task_id", "task_name", "action", "user", "comment"]
        with open(log_path, "a", newline="", encoding="utf-8") as f:
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
                tpl.status = "SOON" if delta <= 14 else "UPCOMING"
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
                    start_date: datetime, repeat_value: int, repeat_unit: str) -> Optional[MaintenanceTemplate]:
        # Enforce single calibration task per machine
        if kind.lower().startswith("cal"):
            for t in self.templates.values():
                if t.box_uid == box_uid and t.kind == "calibration":
                    return None
        tpl_id = f"mt_{int(datetime.now().timestamp()*1000)}"
        next_due = start_date.strftime(DATE_FMT)
        tpl = MaintenanceTemplate(
            id=tpl_id,
            box_uid=box_uid,
            box_title=box_title,
            name=name,
            kind=("calibration" if kind.lower().startswith("cal") else "pm"),
            start_date=start_date.strftime(DATE_FMT),
            repeat_value=repeat_value,
            repeat_unit=repeat_unit,
            next_due=next_due,
            status="UPCOMING",
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
        if tpl.status != "IN_PROGRESS":
            return None
        if not (comment or "").strip():
            return None
        now = datetime.now()
        tpl.status = "UPCOMING"
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

    def update_task(self, task_id: str, data: dict) -> Optional[MaintenanceTemplate]:
        tpl = self.templates.get(task_id)
        if not tpl:
            return None
        
        # Update fields
        if "name" in data: tpl.name = data["name"]
        if "start_date" in data: 
            tpl.start_date = data["start_date"]
            # Synch start_date and next_due because the UI presents "Next Due Date" but sends "start_date"
            tpl.next_due = data["start_date"]
        if "repeat_value" in data: tpl.repeat_value = int(data["repeat_value"])
        if "repeat_unit" in data: tpl.repeat_unit = data["repeat_unit"]
        if "notes" in data: tpl.notes = data["notes"]
        
        self._save_templates()
        return tpl

    def cancel_task(self, task_id: str, user: str, comment: str) -> Optional[MaintenanceTemplate]:
        tpl = self.templates.get(task_id)
        if not tpl:
            return None
        if tpl.status != "IN_PROGRESS":
            return None # Can only cancel in-progress tasks
            
        tpl.status = "UPCOMING" # Revert to upcoming
        self._save_templates()
        self._append_log(MaintenanceLogEntry(
            timestamp=datetime.now().isoformat(timespec='seconds'),
            box_uid=tpl.box_uid,
            box_title=tpl.box_title,
            task_id=tpl.id,
            task_name=tpl.name,
            action="cancel",
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

    def remove_task(self, task_id: str) -> bool:
        if task_id in self.templates:
            del self.templates[task_id]
            self._save_templates()
            return True
        return False

    def delete_task(self, task_id: str) -> bool:
        return self.remove_task(task_id)




    def log_manual_override(self, box_uid: str, box_title: str, user: str, comment: str) -> None:
        self._append_log(MaintenanceLogEntry(
            timestamp=datetime.now().isoformat(timespec='seconds'),
            box_uid=box_uid,
            box_title=box_title,
            task_id='',
            task_name='',
            action='manual_override',
            user=user,
            comment=(comment or '').strip(),
        ))

    def log_delete(self, box_uid: str, box_title: str, task_id: str, task_name: str, user: str, comment: str) -> None:
        self._append_log(MaintenanceLogEntry(
            timestamp=datetime.now().isoformat(timespec='seconds'),
            box_uid=box_uid,
            box_title=box_title,
            task_id=task_id,
            task_name=task_name,
            action='delete',
            user=user,
            comment=(comment or '').strip(),
        ))

    def remove_all_for_box(self, box_uid: str) -> None:
        # Remove from in-memory templates
        to_delete = [tid for tid, tpl in list(self.templates.items()) if tpl.box_uid == box_uid]
        for tid in to_delete:
            try:
                del self.templates[tid]
            except KeyError:
                pass
        self._save_templates()
        # Remove on-disk per-machine files
        tpl_path, log_path = self._paths_for(box_uid)
        try:
            if os.path.exists(tpl_path):
                os.remove(tpl_path)
        except Exception:
            pass
        try:
            # Keep log for audit; comment the next lines if you want to remove logs too
            # if os.path.exists(log_path):
            #     os.remove(log_path)
            pass
        except Exception:
            pass
        # Attempt to remove the directory if empty
        try:
            d = os.path.dirname(tpl_path)
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
        except Exception:
            pass
