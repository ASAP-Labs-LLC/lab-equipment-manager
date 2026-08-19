#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
server_state.py - Core state engine for the Lab Manager Map server.

Responsibilities:
 - Own the canonical AppConfig and persist changes.
 - Monitor CSV sources on an interval and compute box statuses.
 - Track maintenance data, manual overrides, and reporting state.

The networking layer (FastAPI, legacy HTTP server, etc.) imports this module
and wires its handlers to the public methods on `State`.
"""

from __future__ import annotations

import csv
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from typing import Callable, Dict, List, Optional, Tuple

from models import AppConfig, BoxConfig, STATUS_DEAD, STATUS_SERVICE, STATUS_GREEN, STATUS_YELLOW
from config_store import load_config, save_config
from data_source import evaluate_box, build_sample_index, SampleIndex
from maintenance import MaintenanceManager, MaintenanceTemplate


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class CsvCacheEntry:
    path: str
    mtime: Optional[float]
    sample_id_column: str
    rows: List[dict]
    sample_index: SampleIndex


class State:
    def __init__(self, poll_seconds: int, event_sink: Optional[Callable[[dict], None]] = None) -> None:
        self.lock = threading.RLock()
        self.cfg: AppConfig = load_config()
        print(f"[SVR] loaded config: boxes={len(self.cfg.boxes)} samples={len(self.cfg.samples)} status_log_dir={self.cfg.status_log_dir!r}", flush=True)
        self._last_rows_cache: Dict[str, List[dict]] = {}
        self._last_index_cache: Dict[str, SampleIndex] = {}
        self._last_status_by_uid: Dict[str, str] = {}
        self._box_payload_cache: Dict[str, dict] = {}
        self._csv_cache: Dict[str, CsvCacheEntry] = {}
        self._stop = threading.Event()
        self._poll_seconds = max(1, int(poll_seconds))
        self._thread: Optional[threading.Thread] = None
        self._event_sink = event_sink
        self._inspec_dirty = False
        # Maintenance (PMs)
        base_dir = os.path.join(os.path.dirname(__file__), "Maintenance")
        # Disable legacy/global fallback so deleted PMs don't reappear from old files
        self.maintenance = MaintenanceManager(base_dir, use_global_fallback=False)
        self._sync_maintenance_dirs()

    def start(self) -> None:
        if self._thread is None:
            self._stop.clear()
            t = threading.Thread(target=self._loop, name="csv-monitor", daemon=True)
            t.start()
            self._thread = t
            print(f"[SVR] background monitor thread started poll_seconds={self._poll_seconds}", flush=True)

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=3)
        self._thread = None

    def set_poll_seconds(self, seconds: int) -> None:
        with self.lock:
            self._poll_seconds = max(1, int(seconds))
            print(f"[SVR] set_poll_seconds -> {self._poll_seconds}", flush=True)

    def _sync_maintenance_dirs(self) -> None:
        box_dirs: Dict[str, str] = {}
        for b in self.cfg.boxes:
            if b.csv_path:
                box_dirs[b.uid] = os.path.dirname(b.csv_path)
        self.maintenance.set_box_dirs(box_dirs)
        print(f"[SVR] sync_maintenance_dirs count={len(box_dirs)}", flush=True)

    def _read_csv_cached(self, path: str, sample_id_column: str) -> CsvCacheEntry:
        current_mtime: Optional[float] = None
        try:
            if os.path.exists(path):
                current_mtime = os.path.getmtime(path)
        except Exception as exc:
            print(f"[SVR] stat error for CSV {path}: {exc}", flush=True)
        cached = self._csv_cache.get(path)
        if cached and cached.mtime == current_mtime and cached.sample_id_column == sample_id_column:
            return cached

        rows: List[dict] = []
        try:
            if os.path.exists(path):
                print(f"[SVR] reading CSV (changed): {path}", flush=True)
                with open(path, "r", newline="", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    rows.extend(reader)
                print(f"[SVR] read rows: {len(rows)} from {path}", flush=True)
            else:
                print(f"[SVR] CSV missing: {path}", flush=True)
        except Exception as exc:
            print(f"[SVR] ERROR reading CSV {path}: {exc}", flush=True)
        sample_index = build_sample_index(rows, sample_id_column)
        entry = CsvCacheEntry(path=path, mtime=current_mtime, sample_id_column=sample_id_column, rows=rows, sample_index=sample_index)
        self._csv_cache[path] = entry
        return entry

    def _loop(self) -> None:
        # initial immediate run
        try:
            self.refresh_all()
        except Exception as exc:
            print(f"[SVR] initial refresh failed: {exc}", flush=True)
        # periodic
        while not self._stop.wait(self._poll_seconds):
            try:
                self.refresh_all()
            except Exception as exc:
                print(f"[SVR] periodic refresh failed: {exc}", flush=True)
                continue

    def _emit_event(self, topic: str, payload: dict) -> None:
        if not self._event_sink:
            return
        try:
            self._event_sink({
                "topic": topic,
                "payload": payload,
                "timestamp": _now_iso(),
            })
        except Exception as exc:
            print(f"[SVR] event sink error topic={topic}: {exc}", flush=True)

    # ----- first in-spec tracking (fallback clock) -----
    def _ensure_first_inspec_epoch_locked(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if self.cfg.first_inspec_date != today:
            self.cfg.first_inspec_date = today
            self.cfg.first_inspec_map = {}
            self._inspec_dirty = True

    def _set_first_inspec_if_missing_locked(self, uid: str, when: datetime) -> None:
        if uid not in self.cfg.first_inspec_map:
            self.cfg.first_inspec_map[uid] = when.replace(microsecond=0).isoformat(sep=' ')
            self._inspec_dirty = True

    def _get_first_inspec_locked(self, uid: str) -> Optional[datetime]:
        iso = self.cfg.first_inspec_map.get(uid)
        if not iso:
            return None
        try:
            return datetime.fromisoformat(iso)
        except Exception:
            return None

    def _effective_last_good_locked(self, box: BoxConfig, eval_res) -> Optional[datetime]:
        effective = eval_res.last_good_qc
        if eval_res.used_parsed:
            return effective
        stored = self._get_first_inspec_locked(box.uid)
        if eval_res.status in (STATUS_GREEN, STATUS_YELLOW):
            now_local = datetime.now().replace(microsecond=0)
            self._set_first_inspec_if_missing_locked(box.uid, now_local)
            stored = self._get_first_inspec_locked(box.uid)
        return stored or effective

    def _log_status_change(self, box: BoxConfig, prev_status: str, new_status: str, reason: str) -> None:
        try:
            out_dir = (self.cfg.status_log_dir or "").strip()
            if not out_dir:
                return
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, "status_changes.csv")
            file_exists = os.path.exists(path)
            import csv
            with open(path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["timestamp", "box_uid", "box_title", "prev_status", "new_status", "reason"])
                writer.writerow([
                    _now_iso(),
                    box.uid,
                    box.title,
                    prev_status,
                    new_status,
                    reason or "",
                ])
        except Exception as exc:
            print(f"[SVR] status log write failed for {box.uid}: {exc}", flush=True)

    def _log_manual_override(self, box: BoxConfig, action: str, user: str, note: str) -> None:
        try:
            out_dir = (self.cfg.status_log_dir or "").strip()
            if not out_dir:
                return
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, "manual_overrides.csv")
            file_exists = os.path.exists(path)
            import csv
            with open(path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["timestamp", "box_uid", "box_title", "action", "user", "note"])
                writer.writerow([
                    _now_iso(),
                    box.uid,
                    box.title,
                    action or "",
                    user or "",
                    note or "",
                ])
        except Exception as exc:
            print(f"[SVR] manual override log failed for {box.uid}: {exc}", flush=True)

    def refresh_all(self) -> None:
        with self.lock:
            sample_id_column = self.cfg.sample_id_column or "Lab ID"
        paths = sorted({b.csv_path for b in self.cfg.boxes if b.csv_path})
        print(f"[SVR] refresh_all paths={len(paths)}", flush=True)
        rows_by_path: Dict[str, List[dict]] = {}
        index_by_path: Dict[str, SampleIndex] = {}
        for path in paths:
            entry = self._read_csv_cached(path, sample_id_column)
            rows_by_path[path] = entry.rows
            index_by_path[path] = entry.sample_index

        with self.lock:
            self._inspec_dirty = False
            self._ensure_first_inspec_epoch_locked()
            self._last_rows_cache = rows_by_path
            self._last_index_cache = index_by_path
            # compute statuses per box
            samples_by_name = {s.name: s for s in self.cfg.samples}
            for box in self.cfg.boxes:
                rows = rows_by_path.get(box.csv_path, [])
                idx = index_by_path.get(box.csv_path)
                ev = evaluate_box(box, samples_by_name, sample_id_column, rows, idx)
                status = ev.status
                reason = ev.reason
                if box.manual_override == STATUS_DEAD:
                    status = STATUS_DEAD
                    reason = "Manual override: DEAD-LINE"
                elif box.manual_override == STATUS_SERVICE:
                    status = STATUS_SERVICE
                    reason = "Manual override: SERVICE"

                effective_last_good = self._effective_last_good_locked(box, ev)
                # stale check (mirror client logic)
                if status == STATUS_GREEN and effective_last_good:
                    if (datetime.now() - effective_last_good) > timedelta(hours=box.qc_expire_hours):
                        status = STATUS_YELLOW
                        reason = "Last in-spec QC is stale."

                prev_status = self._last_status_by_uid.get(box.uid)
                if prev_status and prev_status != status:
                    self._log_status_change(box, prev_status, status, reason)
                self._last_status_by_uid[box.uid] = status
                payload = {
                    "box_uid": box.uid,
                    "status": status,
                    "reason": reason,
                    "manual_override": box.manual_override,
                    "warnings": ev.warnings,
                }
                prev_payload = self._box_payload_cache.get(box.uid)
                self._box_payload_cache[box.uid] = payload
                if payload != prev_payload:
                    print(f"[SVR] box {box.uid} '{box.title}' status={status} reason={reason}", flush=True)
                    self._emit_event("box.updated", payload)

            # Refresh maintenance (PMs) and write active list CSV
            try:
                self._sync_maintenance_dirs()
                self.maintenance.refresh_statuses(save=False)
                self._write_active_pms_csv()
                print(f"[SVR] maintenance refreshed: templates={len(self.maintenance.templates)} log_rows={len(self.maintenance.log)}", flush=True)
            except Exception as e:
                print(f"[SVR] maintenance refresh error: {e}", flush=True)

            try:
                self._maybe_run_daily_report_locked(rows_by_path, index_by_path)
            except Exception as e:
                print(f"[SVR] daily report error: {e}", flush=True)

            if self._inspec_dirty:
                try:
                    save_config(self.cfg)
                except Exception as e:
                    print(f"[SVR] first in-spec save error: {e}", flush=True)
                finally:
                    self._inspec_dirty = False

    def _write_active_pms_csv(self) -> None:
        out_dir = (self.cfg.status_log_dir or "").strip()
        if not out_dir:
            return
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "active_pms.csv")
        # Build active list: tasks with status IN_PROGRESS
        active = [tpl for tpl in self.maintenance.templates.values() if tpl.status == "IN_PROGRESS"]
        # Build lookup for last start timestamp per task
        last_start: Dict[str, str] = {}
        for e in self.maintenance.log:
            if e.action == "start" and e.task_id:
                last_start[e.task_id] = e.timestamp
        import csv as _csv
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = _csv.writer(f)
            writer.writerow(["generated_at", "box_uid", "box_title", "task_id", "task_name", "kind", "started_at", "next_due", "status"])
            now = _now_iso()
            for t in active:
                writer.writerow([
                    now,
                    t.box_uid,
                    t.box_title,
                    t.id,
                    t.name,
                    t.kind,
                    last_start.get(t.id, ""),
                    t.next_due,
                    t.status,
                ])
        print(f"[SVR] wrote active_pms.csv -> {path} count={len(active)}", flush=True)

    def _serialize_task(self, tpl: MaintenanceTemplate) -> dict:
        return {
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
        }

    def _maintenance_snapshot_locked(self) -> Dict[str, List[dict]]:
        self.maintenance.refresh_statuses(save=False)
        active = []
        last_start: Dict[str, str] = {}
        for e in self.maintenance.log:
            if getattr(e, "action", "") == "start" and getattr(e, "task_id", ""):
                last_start[e.task_id] = e.timestamp
        for t in self.maintenance.templates.values():
            if t.status == "IN_PROGRESS":
                active.append({
                    "box_uid": t.box_uid,
                    "box_title": t.box_title,
                    "task_id": t.id,
                    "task_name": t.name,
                    "kind": t.kind,
                    "started_at": last_start.get(t.id, ""),
                    "next_due": t.next_due,
                    "status": t.status,
                })
        tasks = [self._serialize_task(t) for t in self.maintenance.templates.values()]
        try:
            tasks.sort(key=lambda x: (x["box_uid"], x["next_due"]))
        except Exception:
            pass
        return {"active": active, "tasks": tasks}

    def get_maintenance_snapshot(self) -> Dict[str, List[dict]]:
        with self.lock:
            return self._maintenance_snapshot_locked()

    def _parse_start_date(self, value: str) -> datetime:
        if not value:
            return datetime.now()
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value, fmt)
            except Exception:
                continue
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return datetime.now()

    def _emit_maintenance_refresh(self) -> None:
        try:
            self._sync_maintenance_dirs()
            self.maintenance.refresh_statuses(save=False)
            self._write_active_pms_csv()
        except Exception as exc:
            print(f"[SVR] maintenance sync error: {exc}", flush=True)

    def add_maintenance_task(self, payload: dict) -> Optional[dict]:
        with self.lock:
            box_uid = str(payload.get("box_uid", ""))
            box = next((b for b in self.cfg.boxes if b.uid == box_uid), None)
            if not box:
                return None
            name = str(payload.get("name", "")).strip()
            if not name:
                return None
            kind = str(payload.get("kind", "pm")).strip() or "pm"
            repeat_value = int(payload.get("repeat_value", 1) or 1)
            repeat_unit = str(payload.get("repeat_unit", "weeks") or "weeks")
            start_str = str(payload.get("start_date", "")).strip()
            start_dt = self._parse_start_date(start_str)
            tpl = self.maintenance.create_task(
                box.uid,
                box.title,
                name,
                kind,
                start_dt,
                repeat_value,
                repeat_unit,
            )
            if not tpl:
                return None
            self._emit_maintenance_refresh()
            data = self._serialize_task(tpl)
            self._emit_event("maintenance.updated", {"action": "created", "task": data})
            return data

    def start_maintenance_task(self, task_id: str) -> Optional[dict]:
        with self.lock:
            tpl = self.maintenance.start_task(task_id)
            if not tpl:
                return None
            box = next((b for b in self.cfg.boxes if b.uid == tpl.box_uid), None)
            if box:
                box.manual_override = STATUS_SERVICE
                save_config(self.cfg)
            self._emit_maintenance_refresh()
            data = self._serialize_task(tpl)
            self._emit_event("maintenance.updated", {"action": "start", "task": data})
            return data

    def complete_maintenance_task(self, task_id: str, user: str, comment: str) -> Optional[dict]:
        with self.lock:
            tpl = self.maintenance.complete_task(task_id, user, comment)
            if not tpl:
                return None
            box = next((b for b in self.cfg.boxes if b.uid == tpl.box_uid), None)
            if box:
                in_progress = any(
                    t.status == "IN_PROGRESS" and t.box_uid == tpl.box_uid
                    for t in self.maintenance.templates.values()
                )
                if not in_progress and box.manual_override == STATUS_SERVICE:
                    box.manual_override = ""
                save_config(self.cfg)
            self._emit_maintenance_refresh()
            data = self._serialize_task(tpl)
            self._emit_event("maintenance.updated", {"action": "complete", "task": data})
            return data

    def add_maintenance_comment(self, box_uid: str, box_title: str, comment: str, user: str) -> bool:
        if not comment.strip():
            return False
        with self.lock:
            self.maintenance.add_comment(box_uid, box_title, comment, user)
            self._emit_event("maintenance.comment", {
                "box_uid": box_uid,
                "user": user,
                "comment": comment,
            })
            return True

    # ----- Reporting -----
    def _build_report_rows_locked(self, rows_by_path: Dict[str, List[dict]], index_by_path: Optional[Dict[str, SampleIndex]] = None) -> Tuple[List[str], List[List[str]]]:
        headers = [
            "Box Title", "Box UID", "Box Status", "Override",
            "CSV Path", "QC Expiry (h)", "Last In-Spec QC / Fallback", "Latest Match Time",
            "Reason", "Used Parsed Time",
            "Sample", "Test Name", "Expected", "k*StdDev", "Low", "High", "Latest Value", "In Spec", "Units"
        ]
        rows_out: List[List[str]] = []
        samples_by_name = {s.name: s for s in self.cfg.samples}
        sample_id_column = self.cfg.sample_id_column or "Lab ID"
        for box in self.cfg.boxes:
            eval_res = evaluate_box(
                box,
                samples_by_name,
                sample_id_column,
                rows_by_path.get(box.csv_path, []),
                (index_by_path or {}).get(box.csv_path),
            )
            status = eval_res.status
            reason = eval_res.reason or ""
            if box.manual_override == STATUS_DEAD:
                status = STATUS_DEAD
                reason = "Manual override: DEAD-LINE"
            elif box.manual_override == STATUS_SERVICE:
                status = STATUS_SERVICE
                reason = "Manual override: SERVICE"

            effective_last_good = self._effective_last_good_locked(box, eval_res)
            last_qc = effective_last_good.isoformat(sep=' ') if effective_last_good else ""
            last_row = eval_res.latest_match_time.isoformat(sep=' ') if eval_res.latest_match_time else ""
            used_parsed = "YES" if eval_res.used_parsed else "NO"

            if eval_res.results:
                for pr in eval_res.results:
                    sample_name = pr.sample or ""
                    if pr.test:
                        tol = pr.test.k * pr.test.std_dev
                        low = "" if pr.low is None else f"{pr.low:.6g}"
                        high = "" if pr.high is None else f"{pr.high:.6g}"
                        expected = f"{pr.test.expected:.6g}"
                        latest = "" if pr.latest_value is None else f"{pr.latest_value:.6g}"
                        insp = "" if pr.in_spec is None else ("YES" if pr.in_spec else "NO")
                        rows_out.append([
                            box.title, box.uid, status, (box.manual_override or ""),
                            box.csv_path, f"{box.qc_expire_hours:.2f}", last_qc, last_row,
                            reason, used_parsed,
                            sample_name, pr.test.name, expected, f"{tol:.6g}",
                            low, high, latest, insp, pr.test.units or "",
                        ])
                    else:
                        rows_out.append([
                            box.title, box.uid, status, (box.manual_override or ""),
                            box.csv_path, f"{box.qc_expire_hours:.2f}", last_qc, last_row,
                            reason, used_parsed,
                            sample_name, "", "", "", "", "", "", "", "",
                        ])
            else:
                rows_out.append([
                    box.title, box.uid, status, (box.manual_override or ""),
                    box.csv_path, f"{box.qc_expire_hours:.2f}", last_qc, last_row,
                    reason, used_parsed,
                    "", "", "", "", "", "", "", "", "",
                ])
        return headers, rows_out

    def _export_report_locked(self, rows_by_path: Dict[str, List[dict]], today_str: str,
                              formats: Tuple[str, ...], manual: bool,
                              index_by_path: Optional[Dict[str, SampleIndex]] = None) -> Dict[str, str]:
        report_dir = (self.cfg.report_dir or "").strip()
        if not report_dir:
            raise ValueError("Report directory is not configured.")
        os.makedirs(report_dir, exist_ok=True)
        headers, rows = self._build_report_rows_locked(rows_by_path, index_by_path)
        outputs: Dict[str, str] = {}
        if "csv" in formats:
            csv_path = os.path.join(report_dir, f"LabManagerReport_{today_str}.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                for row in rows:
                    writer.writerow(row)
            outputs["csv"] = csv_path
        if "html" in formats:
            html_path = os.path.join(report_dir, f"LabManagerReport_{today_str}.html")
            lines = [
                "<html><head><meta charset=\"utf-8\" />",
                f"<title>Lab Manager Report - {today_str}</title>",
                "<style>table{border-collapse:collapse;font-family:Segoe UI,Arial,sans-serif;}th,td{border:1px solid #999;padding:4px 8px;font-size:12px;}</style>",
                "</head><body>",
                f"<h2>Lab Manager Report — {today_str}</h2>",
                "<table>",
                "<thead><tr>" + "".join(f"<th>{escape(h)}</th>" for h in headers) + "</tr></thead>",
                "<tbody>",
            ]
            for row in rows:
                cells = "".join(f"<td>{escape(str(cell))}</td>" for cell in row)
                lines.append(f"<tr>{cells}</tr>")
            lines.append("</tbody></table></body></html>")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            outputs["html"] = html_path
        self.cfg.last_report_date = today_str
        save_config(self.cfg)
        self._emit_event("report.generated", {
            "date": today_str,
            "paths": outputs,
            "manual": manual,
        })
        return outputs

    def _maybe_run_daily_report_locked(self, rows_by_path: Dict[str, List[dict]], index_by_path: Optional[Dict[str, SampleIndex]] = None) -> None:
        if not self.cfg.report_enabled:
            return
        if not (self.cfg.report_dir or "").strip():
            return
        try:
            hh, mm = [int(x) for x in str(self.cfg.report_time or "17:00").split(":")[:2]]
        except Exception:
            hh, mm = 17, 0
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if self.cfg.last_report_date == today:
            return
        if (now.hour, now.minute) < (hh, mm):
            return
        self._export_report_locked(rows_by_path, today, ("csv", "html"), manual=False, index_by_path=index_by_path)

    def build_report_preview(self) -> Dict[str, List[List[str]]]:
        with self.lock:
            headers, rows = self._build_report_rows_locked(self._last_rows_cache, self._last_index_cache)
            return {"headers": headers, "rows": rows}

    def generate_report(self, force: bool = True, formats: Optional[List[str]] = None) -> Dict[str, str]:
        fmt = tuple(x.lower() for x in formats) if formats else ("csv", "html")
        with self.lock:
            today = datetime.now().strftime("%Y-%m-%d")
            if not force and self.cfg.last_report_date == today:
                raise ValueError("Report already generated today.")
            return self._export_report_locked(dict(self._last_rows_cache), today, fmt, manual=True, index_by_path=dict(self._last_index_cache))

    # ----- Maintenance actions -----
    def pm_delete(self, task_id: str, user: str, reason: str) -> bool:
        return self.delete_maintenance_task(task_id, user, reason)

    def delete_maintenance_task(self, task_id: str, user: str, reason: str) -> bool:
        with self.lock:
            tpl = self.maintenance.templates.get(task_id)
            if not tpl:
                return False
            try:
                self.maintenance.log_delete(tpl.box_uid, tpl.box_title, tpl.id, tpl.name, user or "", reason or "")
            except Exception:
                pass
            self.maintenance.remove_task(task_id)
            self._emit_maintenance_refresh()
            self._emit_event("maintenance.deleted", {"task_id": task_id})
            return True

    def serialize_state(self) -> dict:
        with self.lock:
            # Keep maintenance fresh on every state read so UI reflects recent file edits/deletes
            try:
                self._sync_maintenance_dirs()
                self.maintenance.reload()
                self.maintenance.refresh_statuses(save=False)
                self._write_active_pms_csv()
            except Exception:
                pass
            # Build a lightweight view for clients
            rows_by_path = self._last_rows_cache
            out_boxes: List[dict] = []
            samples_by_name = {s.name: s for s in self.cfg.samples}
            sample_id_column = self.cfg.sample_id_column or "Lab ID"
            for box in self.cfg.boxes:
                rows = rows_by_path.get(box.csv_path, [])
                idx = self._last_index_cache.get(box.csv_path)
                ev = evaluate_box(box, samples_by_name, sample_id_column, rows, idx)
                status = ev.status
                reason = ev.reason
                if box.manual_override == STATUS_DEAD:
                    status = STATUS_DEAD
                    reason = "Manual override: DEAD-LINE"
                elif box.manual_override == STATUS_SERVICE:
                    status = STATUS_SERVICE
                    reason = "Manual override: SERVICE"

                # Info lines (mirror client)
                lines: List[str] = []
                for pr in ev.results[:4]:
                    if pr.test:
                        tol = pr.test.k * pr.test.std_dev
                        rng = f"[{pr.low:.6g}, {pr.high:.6g}]" if pr.low is not None else "-"
                        vtxt = "-" if pr.latest_value is None else f"{pr.latest_value:.6g}"
                        flag = "" if pr.in_spec is None else ("✓" if pr.in_spec else "✗")
                        units = f" {pr.test.units}" if pr.test.units else ""
                        lines.append(f"{pr.test.name}: {vtxt}{units} {flag}  ±{tol:.6g}  {rng}")
                    else:
                        lines.append("(missing test)")
                if len(ev.results) > 4:
                    lines.append(f"+{len(ev.results)-4} more…")

                out_boxes.append({
                    "uid": box.uid,
                    "title": box.title,
                    "pos": list(box.pos),
                    "size": list(box.size),
                    "locked": bool(box.locked),
                    "manual_override": box.manual_override,
                    "status": status,
                    "reason": reason,
                    "lines": lines,
                    "warnings": list(ev.warnings),
                })

            snapshot = self._maintenance_snapshot_locked()

            state = {
                "last_updated": _now_iso(),
                "map_locked": bool(self.cfg.map_locked),
                "poll_minutes": int(self.cfg.poll_minutes),
                "sample_id_column": self.cfg.sample_id_column,
                "boxes": out_boxes,
                "maintenance": snapshot,
                # include theme bits clients may want; clients can ignore
                "theme_mode": self.cfg.theme_mode,
                "ui_scale": self.cfg.ui_scale,
                "reports": {
                    "enabled": bool(self.cfg.report_enabled),
                    "report_time": self.cfg.report_time,
                    "last_report_date": self.cfg.last_report_date,
                    "report_dir": self.cfg.report_dir,
                },
            }
            # Persist canonical JSON snapshot
            try:
                out_dir = (self.cfg.status_log_dir or os.path.dirname(__file__)).strip()
                os.makedirs(out_dir, exist_ok=True)
                with open(os.path.join(out_dir, "state.json"), "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2)
            except Exception:
                pass
            return state

    # ----- Actions -----
    def update_box_pos_size(self, uid: str, pos: Optional[List[float]], size: Optional[List[float]], locked: Optional[bool]) -> Tuple[bool, bool]:
        """
        Returns (found, changed).
        found = True if a box with the uid exists; changed indicates if we modified anything.
        """
        found = False
        changed = False
        with self.lock:
            for b in self.cfg.boxes:
                if b.uid == uid:
                    found = True
                    if pos and (tuple(pos) != b.pos):
                        b.pos = (float(pos[0]), float(pos[1]))
                        changed = True
                    if size and (tuple(size) != b.size):
                        b.size = (float(size[0]), float(size[1]))
                        changed = True
                    if locked is not None and bool(locked) != bool(b.locked):
                        b.locked = bool(locked)
                        changed = True
                    break
        if changed:
            save_config(self.cfg)
        return found, changed

    def manual_override(self, uid: str, mode: str, user: str, note: str) -> bool:
        print(f"[SVR] manual_override uid={uid} mode={mode} user={user!r}", flush=True)
        with self.lock:
            for b in self.cfg.boxes:
                if b.uid == uid:
                    if mode == "OFF":
                        b.manual_override = ""
                        action = "OFF"
                    elif mode == STATUS_DEAD:
                        b.manual_override = STATUS_DEAD
                        action = STATUS_DEAD
                    elif mode == STATUS_SERVICE:
                        b.manual_override = STATUS_SERVICE
                        action = STATUS_SERVICE
                    else:
                        return False
                    save_config(self.cfg)
                    try:
                        self._log_manual_override(b, f"{action}", user, note)
                    except Exception:
                        pass
                    self._emit_event("box.override", {
                        "box_uid": b.uid,
                        "mode": b.manual_override or "AUTO",
                        "user": user,
                    })
                    return True
        return False

    def add_or_edit_box(self, box_dict: dict) -> bool:
        print(f"[SVR] add_or_edit_box keys={list((box_dict or {}).keys())}", flush=True)
        try:
            new_box = BoxConfig.from_dict(box_dict)
        except Exception:
            return False
        with self.lock:
            created = False
            replaced = False
            for i, b in enumerate(self.cfg.boxes):
                if b.uid == new_box.uid and new_box.uid:
                    self.cfg.boxes[i] = new_box
                    replaced = True
                    break
            if not replaced:
                if not new_box.uid:
                    new_box.uid = f"box_{int(time.time() * 1000)}"
                self.cfg.boxes.append(new_box)
                created = True
            save_config(self.cfg)
            # Keep maintenance dirs current if csv_path changed or new box added
            try:
                self._sync_maintenance_dirs()
                self.maintenance.refresh_statuses(save=False)
                self._write_active_pms_csv()
            except Exception as e:
                print(f"[SVR] add_or_edit_box maintenance sync error: {e}", flush=True)
            self._emit_event("box.created" if created else "box.updated", {
                "box_uid": new_box.uid,
                "title": new_box.title,
                "csv_path": new_box.csv_path,
            })
        return True

    def remove_box(self, uid: str) -> bool:
        print(f"[SVR] remove_box uid={uid}", flush=True)
        with self.lock:
            before = len(self.cfg.boxes)
            self.cfg.boxes = [b for b in self.cfg.boxes if b.uid != uid]
            if len(self.cfg.boxes) != before:
                save_config(self.cfg)
                # Remove any maintenance data for this box uid, and update active PMs
                try:
                    self.maintenance.remove_all_for_box(uid)
                    self._sync_maintenance_dirs()
                    self.maintenance.refresh_statuses(save=False)
                    self._write_active_pms_csv()
                except Exception as e:
                    print(f"[SVR] remove_box maintenance cleanup error: {e}", flush=True)
                self._emit_event("box.deleted", {"box_uid": uid})
                return True
        return False

    def update_settings(self, payload: dict) -> None:
        print(f"[SVR] update_settings payload_keys={list((payload or {}).keys())}", flush=True)
        with self.lock:
            if "map_locked" in payload:
                self.cfg.map_locked = bool(payload.get("map_locked"))
            if "poll_minutes" in payload:
                self.cfg.poll_minutes = int(payload.get("poll_minutes") or 5)
                # also apply to server-side seconds
                self._poll_seconds = max(1, int(self.cfg.poll_minutes) * 60)
            if "status_log_dir" in payload:
                self.cfg.status_log_dir = str(payload.get("status_log_dir") or "")
            if "theme_mode" in payload:
                self.cfg.theme_mode = str(payload.get("theme_mode") or "light")
            if "ui_scale" in payload:
                try:
                    self.cfg.ui_scale = float(payload.get("ui_scale"))
                except Exception:
                    pass
            if "report_enabled" in payload:
                self.cfg.report_enabled = bool(payload.get("report_enabled"))
            if "report_time" in payload:
                self.cfg.report_time = str(payload.get("report_time") or "17:00")
            if "report_dir" in payload:
                self.cfg.report_dir = str(payload.get("report_dir") or "")
            save_config(self.cfg)
