#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
web_server.py - Full web version of the Lab Manager application.

Features:
 - Live status dashboard with map layout and per-box QC results.
 - Authenticated editing for machines, samples/tests, layout, report + log settings.
 - Maintenance task management and manual override logging.
 - Server-Sent Events (SSE) push for live updates; REST API for mutations.

Run:
    python web_server.py --host 0.0.0.0 --port 8000
Environment:
    LABMGR_ADMIN_PASSWORD: admin password (default "Admin1")
    LABMGR_SECRET: Flask session secret (default generated string)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import queue
import threading
import time
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    make_response,
    render_template,
    render_template_string,
    request,
    send_file,
    session,
)

from config_store import load_config, save_config
from data_source import build_sample_index, evaluate_box
from last_seen_cache import LastSeenCache, build_evaluation_from_entry
from maintenance import MaintenanceManager
from models import (
    AppConfig,
    BoxConfig,
    ChecklistItem,
    ChecklistSpec,
    SampleSpec,
    SampleTestSpec,
    UserSpec,
    WatchedTarget,
    STATUS_DEAD,
    STATUS_GREEN,
    STATUS_RED,
    STATUS_SERVICE,
    STATUS_UNKNOWN,
    STATUS_YELLOW,
)

# Allow browsing from the drive root or specific shares
ALLOWED_ROOTS = [
    "//asapserver/Labsharedrive",
    "C:/",
    "D:/",
    # Add other roots as needed
]

BASE_DIR = os.path.dirname(__file__)
ADMIN_PASSWORD = os.environ.get("LABMGR_ADMIN_PASSWORD", "Admin1")
DEFAULT_SECRET = os.environ.get("LABMGR_SECRET", "lab-manager-web-secret")

STATUS_COLORS = {
    STATUS_GREEN: "#21c071",
    STATUS_YELLOW: "#f5c542",
    STATUS_RED: "#f85b5b",
    STATUS_DEAD: "#0f172a",
    STATUS_SERVICE: "#8d99ae",
    STATUS_UNKNOWN: "#718096",
}


# ---- Helpers -----------------------------------------------------------------

def log_config_change(user: str, action: str, details: str):
    """Log configuration changes to CSV."""
    cfg = ENGINE.get_config()
    log_dir = cfg.status_log_dir
    if not log_dir or not os.path.exists(log_dir):
        return  # Or log to stderr
    
    csv_path = os.path.join(log_dir, "config_log.csv")
    exists = os.path.exists(csv_path)
    
    try:
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not exists:
                writer.writerow(["Timestamp", "User", "Action", "Details"])
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                user,
                action,
                details
            ])
    except Exception as e:
        print(f"Failed to log config change: {e}")

def get_current_user() -> str:
    """Get the currently logged in user, or 'admin' or 'anonymous'."""
    # Since we don't have a robust session user store yet aside from the auth flag,
    # we might need to store the username in the session during login.
    # For now, we'll try to get it from the session if I add it there.
    return session.get("username", "admin" if session.get("auth") else "anonymous")


def authed() -> bool:
    return bool(session.get("auth"))


def clone_config(cfg: AppConfig) -> AppConfig:
    """Deep copy using existing serializers."""
    return AppConfig.from_dict(cfg.serialize())


def format_timestamp(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    try:
        return dt.isoformat(timespec="seconds")
    except Exception:
        return dt.isoformat()


def format_value(val, units: str = "") -> str:
    if val is None:
        return "-"
    try:
        num = float(val)
    except Exception:
        return str(val)
    text = f"{num:.4g}"
    return f"{text} {units}".strip()


def serialize_sample(sample: SampleSpec) -> dict:
    return sample.serialize()


def serialize_box(box: BoxConfig) -> dict:
    return {
        "uid": box.uid,
        "title": box.title,
        "csv_path": box.csv_path,
        "timestamp_col": box.timestamp_col,
        "qc_expire_hours": box.qc_expire_hours,
        "watched_targets": [wt.serialize() for wt in box.watched_targets],
        "pos": list(box.pos),
        "size": list(box.size),
        "locked": box.locked,
        "manual_override": box.manual_override,
    }


def serialize_config(cfg: AppConfig) -> dict:
    return {
        "version": cfg.version,
        "poll_minutes": cfg.poll_minutes,
        "map_locked": cfg.map_locked,
        "sample_id_column": cfg.sample_id_column,
        "samples": [serialize_sample(s) for s in cfg.samples],
        "boxes": [serialize_box(b) for b in cfg.boxes],
        "report_enabled": cfg.report_enabled,
        "report_time": cfg.report_time,
        "report_dir": cfg.report_dir,
        "last_report_date": cfg.last_report_date,
        "status_log_dir": cfg.status_log_dir,
        "correction_factor_dir": cfg.correction_factor_dir,
        "theme_mode": cfg.theme_mode,
        "app_font_family": cfg.app_font_family,
        "app_font_size": cfg.app_font_size,
        "ui_scale": cfg.ui_scale,
        "custom_qss_path": cfg.custom_qss_path,
        "checklists": [c.serialize() for c in cfg.checklists],
    }


def apply_manual_override(box: BoxConfig, evaluation) -> Tuple[str, str]:
    status = getattr(evaluation, "status", STATUS_UNKNOWN)
    reason = getattr(evaluation, "reason", "")
    
    if box.manual_override == STATUS_DEAD:
        status = STATUS_DEAD
        reason = "Manual override: DEAD-LINE"
        if hasattr(evaluation, "overall_explanation"):
            evaluation.overall_explanation = f"Overridden to DEAD-LINE. Underlying: {evaluation.overall_explanation}"
    elif box.manual_override == STATUS_SERVICE:
        status = STATUS_SERVICE
        reason = "Manual override: SERVICE"
        if hasattr(evaluation, "overall_explanation"):
            evaluation.overall_explanation = f"Overridden to SERVICE. Underlying: {evaluation.overall_explanation}"
    return status, reason


def log_status_change(cfg: AppConfig, box: BoxConfig, prev_status: str, new_status: str, reason: str) -> None:
    out_dir = (cfg.status_log_dir or "").strip()
    if not out_dir:
        return
    try:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "status_changes.csv")
        file_exists = os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["timestamp", "box_uid", "box_title", "prev_status", "new_status", "reason"])
            writer.writerow([
                datetime.now().isoformat(timespec="seconds"),
                box.uid,
                box.title,
                prev_status,
                new_status,
                reason or "",
            ])
    except Exception:
        pass


def log_manual_override(cfg: AppConfig, box: BoxConfig, action: str, user: str, note: str) -> None:
    out_dir = (cfg.status_log_dir or "").strip()
    if not out_dir:
        return
    try:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "manual_overrides.csv")
        file_exists = os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["timestamp", "box_uid", "box_title", "action", "user", "note"])
            writer.writerow([
                datetime.now().isoformat(timespec="seconds"),
                box.uid,
                box.title,
                action or "",
                user or "",
                note or "",
            ])
    except Exception:
        pass


def parse_watched_targets(raw: Iterable[dict]) -> List[WatchedTarget]:
    targets: List[WatchedTarget] = []
    if not raw:
        return targets
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        sample = str(entry.get("sample", "")).strip()
        test = str(entry.get("test", "")).strip()
        if sample or test:
            targets.append(WatchedTarget(sample=sample, test=test))
    return targets


def evaluate_with_cache(cache: Optional[LastSeenCache],
                        box: BoxConfig,
                        samples_by_name: Dict[str, SampleSpec],
                        sample_id_column: str,
                        rows: List[dict],
                        sample_index: Optional[Dict[str, List[dict]]] = None,
                        row_time_cache: Optional[Dict[int, Tuple[datetime, str]]] = None,
                        file_mtime: Optional[float] = None,
                        maintenance_tasks: Optional[List[object]] = None):
    evaluation = evaluate_box(
        box,
        samples_by_name,
        sample_id_column,
        rows,
        sample_index=sample_index,
        row_time_cache=row_time_cache,
        file_mtime=file_mtime,
        maintenance_tasks=maintenance_tasks,
    )
    if cache is None:
        return evaluation
    if evaluation.latest_match_time:
        cache.update_box(box, evaluation)
        return evaluation
    entry = cache.get_box_entry(box.uid)
    if entry:
        cached = build_evaluation_from_entry(entry, box, samples_by_name)
        if cached:
            return cached
    return evaluation


def read_csv_rows(path: str) -> List[dict]:
    rows: List[dict] = []
    if not path or not os.path.exists(path):
        return rows
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        rows.extend(csv.DictReader(f))
    return rows


def read_log(path: str) -> List[dict]:
    out: List[dict] = []
    if not path or not os.path.exists(path):
        return out
    try:
        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                out.append(row)
    except Exception:
        return []
    return out


# ----- event stream ----------------------------------------------------------

class EventBus:
    def __init__(self) -> None:
        self._subs: List[queue.Queue] = []
        self._lock = threading.Lock()

    def publish(self, event: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except Exception:
                continue

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass


BUS = EventBus()


# ----- status engine ---------------------------------------------------------

class StatusEngine:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.cfg: AppConfig = load_config()
        self.sample_id_column = self.cfg.sample_id_column or "Lab ID"
        self.cache = LastSeenCache(BASE_DIR)
        self.cache.sync_config(self.cfg)
        self.maintenance = MaintenanceManager(BASE_DIR)
        self._sync_maintenance_dirs()
        self._last_status: Dict[str, str] = {}
        self._csv_cache: Dict[str, dict] = {}
        self._snapshot: dict = {"generated_at": None, "boxes": [], "errors": []}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="status-engine", daemon=True)
        self.refresh(force=True)
        self._thread.start()

    def _sync_maintenance_dirs(self) -> None:
        box_dirs = {}
        for b in self.cfg.boxes:
            if b.csv_path:
                box_dirs[b.uid] = os.path.dirname(b.csv_path)
        self.maintenance.set_box_dirs(box_dirs)

    def _load_csv_rows(self, paths: Iterable[str]) -> Tuple[Dict[str, List[dict]], Dict[str, str]]:
        rows: Dict[str, List[dict]] = {}
        errors: Dict[str, str] = {}
        for path in paths:
            if not path:
                continue
            try:
                mtime = os.path.getmtime(path)
            except Exception:
                mtime = None
            cache_entry = self._csv_cache.get(path)
            if cache_entry and cache_entry.get("mtime") == mtime:
                rows[path] = cache_entry.get("rows", [])
                continue
            try:
                parsed = read_csv_rows(path)
                rows[path] = parsed
                self._csv_cache[path] = {"mtime": mtime, "rows": parsed}
            except Exception as exc:
                rows[path] = []
                errors[path] = f"{type(exc).__name__}: {exc}"
                self._csv_cache.pop(path, None)
        return rows, errors

    def _maybe_run_daily_report(self, rows_by_path: Dict[str, List[dict]]) -> None:
        cfg = self.cfg
        if not cfg.report_enabled:
            return
        if not cfg.report_dir:
            return
        try:
            hh, mm = [int(x) for x in cfg.report_time.split(":")[:2]]
        except Exception:
            hh, mm = 17, 0
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        if cfg.last_report_date == today_str and not getattr(self, "_first_refresh", False):
            return
        if getattr(self, "_first_refresh", False) or (now.hour, now.minute) >= (hh, mm):
            headers, rows = build_report_rows(cfg, rows_by_path, self.sample_id_column,
                                              {s.name: s for s in cfg.samples}, self.cache)
            os.makedirs(cfg.report_dir, exist_ok=True)
            out_path = os.path.join(cfg.report_dir, f"LabManagerReport_{today_str}.csv")
            try:
                with open(out_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(headers)
                    writer.writerows(rows)
                
                # Export Daily Logs (Checklists & Config)
                self._export_daily_logs(today_str, cfg.report_dir)
                
                cfg.last_report_date = today_str
                save_config(cfg)
            except Exception as e:
                print(f"Report generation failed: {e}")

    def _export_daily_logs(self, today_str: str, report_dir: str):
        """Export separate log files for the day."""
        # 1. Checklist Log
        try:
            with CHECKLIST_LOCK:
                # CHECKLIST_STATE structure: { date: { (uid, idx): data } }
                day_data = CHECKLIST_STATE.get(today_str, {})
            
                if day_data:
                    cl_path = os.path.join(report_dir, f"Checklists_{today_str}.csv")
                    cfg = self.cfg
                    # We need to map UID to Title.
                    cl_map = {c.uid: c for c in cfg.checklists}
                    
                    with open(cl_path, "w", newline="", encoding="utf-8") as f:
                        writer = csv.writer(f)
                        writer.writerow(["Checklist", "Item", "User", "Time", "Checked"])
                        
                        # Helper to get sort key: (checklist_title, item_index)
                        def get_sort_key(k):
                            cl_uid, item_key = k
                            cl = cl_map.get(cl_uid)
                            cl_title = cl.name if cl else ""
                            idx = 999
                            if cl:
                                # If key is integer, it's index. If string, it's UID.
                                if isinstance(item_key, int):
                                    idx = item_key
                                else:
                                    # Find index by UID
                                    for i, it in enumerate(cl.items):
                                        if it.uid == item_key:
                                            idx = i
                                            break
                            return (cl_title, idx)

                        sorted_keys = sorted(day_data.keys(), key=get_sort_key)
                        
                        for uid, item_key in sorted_keys:
                            entry = day_data[(uid, item_key)]
                            cl = cl_map.get(uid)
                            title = cl.name if cl else uid
                            # Try to get item text from config
                            item_text = f"Item {item_key}"
                            
                            if cl:
                                if isinstance(item_key, int) and item_key < len(cl.items):
                                    item_text = cl.items[item_key].text
                                else:
                                    found = next((it for it in cl.items if it.uid == item_key), None)
                                    if found: item_text = found.text
                                
                            writer.writerow([
                                title,
                                item_text,
                                entry.get("user", ""),
                                entry.get("time", ""),
                                entry.get("checked", False)
                            ])
        except Exception as e:
            print(f"Checklist export failed: {e}")

        # 2. Config Log
        # Read from status_log_dir/config_log.csv
        log_dir = self.cfg.status_log_dir
        if log_dir and os.path.exists(os.path.join(log_dir, "config_log.csv")):
            try:
                src = os.path.join(log_dir, "config_log.csv")
                dst = os.path.join(report_dir, f"ConfigChanges_{today_str}.csv")
                
                with open(src, "r", encoding="utf-8") as fin:
                    reader = csv.reader(fin)
                    header = next(reader, None)
                    rows = []
                    if header:
                        # Index 0 is Timestamp "YYYY-MM-DD HH:MM:SS"
                        for r in reader:
                            if r and r[0].startswith(today_str):
                                rows.append(r)
                                
                    if rows:
                        with open(dst, "w", newline="", encoding="utf-8") as fout:
                            writer = csv.writer(fout)
                            if header: writer.writerow(header)
                            writer.writerows(rows)
            except Exception as e:
                print(f"Config log export failed: {e}")


    def get_snapshot(self) -> dict:
        with self._lock:
            return dict(self._snapshot)

    def get_config(self) -> AppConfig:
        with self._lock:
            return self.cfg

    def update_config(self, mutator) -> AppConfig:
        with self._lock:
            cfg = clone_config(self.cfg)
            mutator(cfg)
            ok, msg = save_config(cfg)
            if not ok:
                raise RuntimeError(f"save failed: {msg}")
            self.cfg = cfg
            self.sample_id_column = cfg.sample_id_column or "Lab ID"
            try:
                self.cache.sync_config(cfg)
                self.cache.flush()
            except Exception:
                pass
            self._sync_maintenance_dirs()
        self.refresh(force=True)
        BUS.publish({"type": "config", "data": serialize_config(self.cfg)})
        return self.cfg

    def refresh(self, force: bool = False) -> None:
        with self._lock:
            cfg = self.cfg
        csv_paths = sorted({b.csv_path for b in cfg.boxes if b.csv_path})
        rows_by_path, path_errors = self._load_csv_rows(csv_paths)
        sample_id_column = self.sample_id_column
        samples_by_name: Dict[str, SampleSpec] = {s.name: s for s in cfg.samples}
        sample_indexes = {path: build_sample_index(rows, sample_id_column) for path, rows in rows_by_path.items()}
        row_time_caches: Dict[str, Dict[int, Tuple[datetime, str]]] = {path: {} for path in rows_by_path}

        boxes_payload: List[dict] = []
        for box in cfg.boxes:
            rows = rows_by_path.get(box.csv_path, [])
            # Optimization: pass mtime to avoid repeated disk reads in best_row_time
            file_mtime = None
            if box.csv_path:
                try:
                    ent = self._csv_cache.get(box.csv_path)
                    if ent: file_mtime = ent.get("mtime")
                except: pass

            # Retrieve maintenance tasks for this box
            tasks = self.maintenance.get_tasks(box.uid)
            
            evaluation = evaluate_with_cache(
                self.cache,
                box,
                samples_by_name,
                sample_id_column,
                rows,
                sample_index=sample_indexes.get(box.csv_path),
                row_time_cache=row_time_caches.get(box.csv_path),
                file_mtime=file_mtime,
                maintenance_tasks=tasks, # Pass tasks for PM/Cal eval
            )
            status, reason = apply_manual_override(box, evaluation)
            prev_status = self._last_status.get(box.uid, STATUS_UNKNOWN)
            if prev_status != status:
                log_status_change(cfg, box, prev_status, status, reason)
                self._last_status[box.uid] = status
                
            # Construct Payload
            payload = {
                "uid": box.uid,
                "title": box.title,
                "status": status,
                "status_color": STATUS_COLORS.get(status, "#607d8b"),
                "reason": reason,
                "manual_override": box.manual_override or "",
                
                # New Fields
                "sub_statuses": getattr(evaluation, "sub_statuses", {}),
                "context_results": getattr(evaluation, "context_results", {}),
                "overall_explanation": getattr(evaluation, "overall_explanation", reason),
                
                "latest_match_time": format_timestamp(getattr(evaluation, "latest_match_time", None)),
                "last_good_qc": format_timestamp(getattr(evaluation, "last_good_qc", None)),
                "csv_path": box.csv_path,
                "csv_name": os.path.basename(box.csv_path) if box.csv_path else "(none)",
                "spec": [{"sample": wt.sample, "test": wt.test} for wt in box.watched_targets],
                "results": [],
                "pos": list(box.pos),
                "size": list(box.size),
                "locked": box.locked,
                "qc_expire_hours": box.qc_expire_hours,
                "file_status": "ok" if box.csv_path and box.csv_path in rows_by_path and not path_errors.get(box.csv_path) else ("missing" if path_errors.get(box.csv_path) or (box.csv_path and box.csv_path not in rows_by_path) else "none"),
                "file_error": path_errors.get(box.csv_path, ""),
                "tasks": [t.__dict__ for t in tasks],
            }
            for pr in getattr(evaluation, "results", []):
                label = pr.sample
                if pr.test and pr.test.name:
                    label = f"{pr.sample} / {pr.test.name}"
                payload["results"].append({
                    "label": label,
                    "value": pr.latest_value,
                    "value_display": format_value(pr.latest_value, pr.test.units if pr.test else ""),
                    "in_spec": pr.in_spec,
                    "expected": pr.test.expected if pr.test else None,
                    "low": pr.low,
                    "high": pr.high,
                    "note": pr.note,
                    "timestamp": format_timestamp(pr.latest_time),
                    "timestamp_source": getattr(pr, "timestamp_source", ""),
                })
            boxes_payload.append(payload)

        errors_payload = [{"path": path, "error": err} for path, err in path_errors.items()]
        snapshot = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "boxes": boxes_payload,
            "errors": errors_payload,
            "refresh_seconds": max(60, int(cfg.poll_minutes) * 60),
        }
        with self._lock:
            self._snapshot = snapshot
        try:
            self.cache.flush()
        except Exception:
            pass
        BUS.publish({"type": "status", "data": snapshot})
        self._maybe_run_daily_report(rows_by_path)

    def _loop(self) -> None:
        self._first_refresh = True
        while not self._stop.wait(max(60, int(self.cfg.poll_minutes) * 60)):
            self._first_refresh = False
            try:
                self.refresh()
            except Exception:
                continue

    def shutdown(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)





# ----- Flask app -------------------------------------------------------------

app = Flask(__name__)
app.secret_key = DEFAULT_SECRET

@app.before_request
def debug_before():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] API Call: {request.method} {request.path}")


def authed() -> bool:
    return bool(session.get("auth"))


def require_auth():
    if not authed():
        abort(make_response(jsonify({"error": "auth required"}), 401))


def json_body() -> dict:
    try:
        return request.get_json(force=True) or {}
    except Exception:
        return {}


@app.route("/api/me")
def whoami():
    return jsonify({"authed": authed()})


@app.post("/api/login")
def login():
    data = json_body()
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    
    cfg = ENGINE.get_config()
    
    # Legacy fall-back
    if not cfg.users:
        if password != ADMIN_PASSWORD:
            return jsonify({"ok": False, "error": "Invalid password"}), 401
        session["auth"] = True
        return jsonify({"ok": True})

    # User check
    user = next((u for u in cfg.users if u.username.lower() == username.lower()), None)
    if user and user.password == password:
        session["auth"] = True
        session["username"] = user.username  # Store for logging
        return jsonify({"ok": True})

    return jsonify({"ok": False, "error": "Invalid credentials"}), 401


@app.get("/api/users")
def api_list_users():
    require_auth()
    cfg = ENGINE.get_config()
    return jsonify({"users": [u.username for u in cfg.users]})


@app.post("/api/users")
def api_add_user():
    require_auth()
    data = json_body()
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    def mutate(cfg: AppConfig):
        if any(u.username.lower() == username.lower() for u in cfg.users):
            raise ValueError("User already exists")
        cfg.users.append(UserSpec(username=username, password=password))
    
    try:
        cfg = _load_cfg_mutation(mutate)
        return jsonify({"users": [u.username for u in cfg.users]})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.delete("/api/users/<username>")
def api_delete_user(username):
    require_auth()
    
    def mutate(cfg: AppConfig):
        initial = len(cfg.users)
        cfg.users = [u for u in cfg.users if u.username.lower() != username.lower()]
        if len(cfg.users) == initial:
            raise ValueError("User not found")
            
    try:
        cfg = _load_cfg_mutation(mutate)
        return jsonify({"users": [u.username for u in cfg.users]})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


# ---- Checklists --------------------------------------------------------------

# In-memory state for today's checklist items
# In-memory state for today's checklist items
# Structure: { date_str: { (checklist_uid, item_key): { "user": str, "time": str, "checked": bool } } }
# item_key can be int (index, legacy) or str (uid)
CHECKLIST_STATE: Dict[str, Dict[Tuple[str, object], dict]] = {}
CHECKLIST_LOCK = threading.Lock()
CHECKLIST_STATE_FILE = os.path.join(BASE_DIR, "checklist_state.json")
_checklist_loaded = False

def _load_checklist_persistence():
    global _checklist_loaded
    if _checklist_loaded: return
    if not os.path.exists(CHECKLIST_STATE_FILE):
        _checklist_loaded = True
        return
    try:
        with open(CHECKLIST_STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
            # Deserialize keys: "date" -> "uid|idx" -> dict
            for date_key, items in raw.items():
                if date_key not in CHECKLIST_STATE:
                    CHECKLIST_STATE[date_key] = {}
                for key_str, val in items.items():
                    try:
                        uid, key_2_str = key_str.rsplit("|", 1)
                        # Try int (legacy index), if fail assume UID (str)
                        try:
                            key_2 = int(key_2_str)
                        except ValueError:
                            key_2 = key_2_str
                        CHECKLIST_STATE[date_key][(uid, key_2)] = val
                    except ValueError: pass
    except Exception as e:
        print(f"Failed to load checklist state: {e}")
    _checklist_loaded = True

def _save_checklist_persistence():
    try:
        # Serialize: keys to "uid|idx"
        out = {}
        with CHECKLIST_LOCK:
            for date_key, items in CHECKLIST_STATE.items():
                out[date_key] = {}
                for (uid, idx), val in items.items():
                    out[date_key][f"{uid}|{idx}"] = val
        
        with open(CHECKLIST_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(out, f)
    except Exception as e:
        print(f"Failed to save checklist state: {e}")

def _get_today_state():
    with CHECKLIST_LOCK:
        if not _checklist_loaded:
             _load_checklist_persistence()
             
    today = datetime.now().strftime("%Y-%m-%d")
    with CHECKLIST_LOCK:
        if today not in CHECKLIST_STATE:
            CHECKLIST_STATE[today] = {}
        return CHECKLIST_STATE[today]

@app.get("/api/checklists")
def api_get_checklists():
    cfg = ENGINE.get_config()
    state = _get_today_state()
    
    # Serialize state for frontend
    # Convert tuple keys (uid, idx) to string keys "uid_idx"
    serial_state = {}
    for (uid, item_key), val in state.items():
        # item_key could be int (legacy index) or str (UID)
        serial_state[f"{uid}_{item_key}"] = val
        
    return jsonify({
        "config": [c.serialize() for c in cfg.checklists],
        "state": serial_state,
        "date": datetime.now().strftime("%Y-%m-%d")
    })

@app.post("/api/checklists/toggle")
def api_toggle_checklist_item():
    require_auth()
    data = json_body()
    uid = data.get("uid")
    item_uid = data.get("item_uid")
    idx = data.get("index") 
    checked = data.get("checked") 
    
    # Support both item_uid and legacy index
    key_2 = item_uid if item_uid else idx

    if uid is None or key_2 is None or checked is None:
        return jsonify({"error": "Missing fields"}), 400
        
    user = get_current_user()
    today = datetime.now().strftime("%Y-%m-%d")
    current_time = datetime.now().strftime("%H:%M")
    
    # Get the checklist config to find children if this is a parent
    cfg = ENGINE.get_config()
    checklist = next((cl for cl in cfg.checklists if cl.uid == uid), None)
    
    affected_items = [(uid, key_2)]  # Start with the clicked item
    
    # If checking a parent, find all children and check them too
    if checklist and item_uid and checked:
        for item in checklist.items:
            if item.parent_uid == item_uid:
                # This is a child of the item being checked
                affected_items.append((uid, item.uid))
    
    with CHECKLIST_LOCK:
        if today not in CHECKLIST_STATE:
            CHECKLIST_STATE[today] = {}
        
        # Update state for all affected items
        for checklist_uid, item_key in affected_items:
            CHECKLIST_STATE[today][(checklist_uid, item_key)] = {
                "user": user,
                "time": current_time,
                "checked": bool(checked)
            }
    
    # Save state
    threading.Thread(target=_save_checklist_persistence).start()
    
    return api_get_checklists()

@app.post("/api/checklists/config")
def api_save_checklists():
    require_auth()
    data = json_body() # Expecting list of checklist specs
    
    def mutate(cfg: AppConfig):
        # Full replace of checklists for simplicity
        new_lists = []
        for item in data.get("checklists", []):
             new_lists.append(ChecklistSpec.from_dict(item))
        cfg.checklists = new_lists
        print(f"[Checklists] Saved {len(new_lists)} checklists for user {get_current_user()}")
        log_config_change(get_current_user(), "Config Update", f"Updated Checklists ({len(new_lists)} lists)")

    try:
        cfg = _load_cfg_mutation(mutate)
        return jsonify({"ok": True, "config": [c.serialize() for c in cfg.checklists]})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/status")
def api_status():
    return jsonify(ENGINE.get_snapshot())


@app.route("/api/config")
def api_config():
    return jsonify(serialize_config(ENGINE.get_config()))


@app.post("/api/status/refresh")
def api_refresh_now():
    require_auth()
    ENGINE.refresh(force=True)
    return jsonify({"ok": True})


def _load_cfg_mutation(mutator):
    try:
        cfg = ENGINE.update_config(mutator)
        return cfg
    except Exception as exc:
        abort(make_response(jsonify({"error": str(exc)}), 500))


@app.post("/api/boxes")
def api_add_box():
    require_auth()
    data = json_body()

    def mutate(cfg: AppConfig) -> None:
        uid = data.get("uid") or f"box_{int(time.time() * 1000)}"
        wt = parse_watched_targets(data.get("watched_targets") or [])
        cfg.boxes.append(BoxConfig(
            uid=str(uid),
            title=str(data.get("title") or "Machine"),
            csv_path=str(data.get("csv_path") or ""),
            timestamp_col=str(data.get("timestamp_col") or ""),
            qc_expire_hours=float(data.get("qc_expire_hours") or 24.0),
            watched_targets=wt,
            pos=tuple(data.get("pos") or (20.0, 20.0)),
            size=tuple(data.get("size") or (240.0, 130.0)),
            locked=bool(data.get("locked") or False),
            manual_override=str(data.get("manual_override") or ""),
        ))

    cfg = _load_cfg_mutation(mutate)
    return jsonify({"ok": True, "config": serialize_config(cfg)})


@app.patch("/api/boxes/<uid>")
def api_update_box(uid: str):
    require_auth()
    data = json_body()

    def mutate(cfg: AppConfig) -> None:
        for i, b in enumerate(cfg.boxes):
            if b.uid == uid:
                if "watched_targets" in data:
                    wt = parse_watched_targets(data["watched_targets"])
                else:
                    wt = b.watched_targets

                cfg.boxes[i] = BoxConfig(
                    uid=b.uid,
                    title=str(data.get("title", b.title)),
                    csv_path=str(data.get("csv_path", b.csv_path)),
                    timestamp_col=str(data.get("timestamp_col", b.timestamp_col)),
                    qc_expire_hours=float(data.get("qc_expire_hours", b.qc_expire_hours)),
                    watched_targets=wt,
                    pos=tuple(data.get("pos", b.pos)),
                    size=tuple(data.get("size", b.size)),
                    locked=bool(data.get("locked", b.locked)),
                    manual_override=str(data.get("manual_override", b.manual_override)),
                )
                return
        raise ValueError(f"Box {uid} not found")

    cfg = _load_cfg_mutation(mutate)
    return jsonify({"ok": True, "config": serialize_config(cfg)})


@app.delete("/api/boxes/<uid>")
def api_delete_box(uid: str):
    require_auth()

    def mutate(cfg: AppConfig) -> None:
        cfg.boxes = [b for b in cfg.boxes if b.uid != uid]

    cfg = _load_cfg_mutation(mutate)
    return jsonify({"ok": True, "config": serialize_config(cfg)})


@app.post("/api/boxes/<uid>/override")
def api_box_override(uid: str):
    require_auth()
    data = json_body()
    mode = str(data.get("mode", "")).upper()
    note = str(data.get("note", "")).strip()
    user = str(data.get("user", "web")).strip()

    def mutate(cfg: AppConfig) -> None:
        for b in cfg.boxes:
            if b.uid == uid:
                if mode == "DEAD-LINE":
                    b.manual_override = STATUS_DEAD
                elif mode == "SERVICE":
                    b.manual_override = STATUS_SERVICE
                else:
                    b.manual_override = ""
                log_manual_override(cfg, b, f"{mode or 'CLEAR'}", user, note)
                return
        raise ValueError(f"Box {uid} not found")

    cfg = _load_cfg_mutation(mutate)
    return jsonify({"ok": True, "config": serialize_config(cfg)})


@app.get("/api/fs/list")
def api_fs_list():
    require_auth()
    path = request.args.get("path", "")
    
    # Default to first allowed root or CWD
    if not path:
        path = ALLOWED_ROOTS[0] if ALLOWED_ROOTS else os.getcwd()

    if not os.path.exists(path):
         return jsonify({"error": "Path not found"}), 404
    
    if not os.path.isdir(path):
         return jsonify({"error": "Not a directory"}), 400

    items = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                is_dir = entry.is_dir()
                # Filter: Show folders and CSV files
                if not is_dir and not entry.name.lower().endswith(".csv"):
                    continue

                items.append({
                    "name": entry.name,
                    "path": entry.path.replace("\\", "/"),
                    "is_dir": is_dir,
                    "size": entry.stat().st_size if not is_dir else 0,
                    "mtime": entry.stat().st_mtime
                })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    
    parent = os.path.dirname(path)
    if parent == path:
        parent = ""
        
    return jsonify({
        "current": path.replace("\\", "/"),
        "parent": parent.replace("\\", "/"),
        "items": items,
        "roots": ALLOWED_ROOTS
    })


# ----- Correction Factor APIs ------------------------------------------------

def _get_correction_factor_paths(cfg: AppConfig) -> Tuple[str, str]:
    """Return (json_path, log_path) for correction factors, or ('', '') if not configured."""
    cf_dir = (cfg.correction_factor_dir or "").strip()
    if not cf_dir:
        return "", ""
    json_path = os.path.join(cf_dir, "correction_factors.json")
    log_path = os.path.join(cf_dir, "correction_factor_changes.csv")
    return json_path, log_path


def _load_correction_factors(json_path: str) -> dict:
    """Load correction factors from JSON file."""
    if not json_path or not os.path.exists(json_path):
        return {}
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_correction_factors(json_path: str, data: dict) -> None:
    """Save correction factors to JSON file."""
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _log_correction_change(log_path: str, machine_name: str, test_name: str,
                           value_column: str, file_destination: str,
                           prev_value: float, new_value: float,
                           latest_result: Optional[float] = None) -> None:
    """Append a change record to the correction factor log CSV."""
    need_header = not os.path.exists(log_path)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    try:
        with open(log_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if need_header:
                writer.writerow([
                    "timestamp", "equipment", "test", "value_column",
                    "file_destination", "previous_correction", "new_correction",
                    "latest_result_value"
                ])
            writer.writerow([
                datetime.now().isoformat(timespec="seconds"),
                machine_name,
                test_name,
                value_column,
                file_destination,
                f"{prev_value:.6g}",
                f"{new_value:.6g}",
                f"{latest_result:.6g}" if latest_result is not None else ""
            ])
    except Exception:
        pass


@app.get("/api/boxes/<uid>/corrections")
def api_get_corrections(uid: str):
    """Get all correction factors for a specific machine."""
    cfg = ENGINE.get_config()
    box = next((b for b in cfg.boxes if b.uid == uid), None)
    if not box:
        return jsonify({"error": f"Box {uid} not found"}), 404
    
    json_path, _ = _get_correction_factor_paths(cfg)
    if not json_path:
        return jsonify({"corrections": [], "message": "Correction factor directory not configured"})
    
    all_data = _load_correction_factors(json_path)
    machine_name = box.title or box.uid
    machine_data = all_data.get(machine_name, {})
    
    corrections = []
    for test_name, entry in machine_data.items():
        if isinstance(entry, dict):
            corrections.append({
                "test_name": test_name,
                "correction_value": entry.get("correction_value", 0.0),
                "value_column": entry.get("value_column", ""),
                "file_destination": entry.get("file_destination", "")
            })
    
    return jsonify({"corrections": corrections, "machine_name": machine_name})


@app.post("/api/boxes/<uid>/corrections")
def api_save_correction(uid: str):
    """Create or update a correction factor for a machine."""
    require_auth()
    cfg = ENGINE.get_config()
    box = next((b for b in cfg.boxes if b.uid == uid), None)
    if not box:
        return jsonify({"error": f"Box {uid} not found"}), 404
    
    json_path, log_path = _get_correction_factor_paths(cfg)
    if not json_path:
        return jsonify({"error": "Correction factor directory not configured"}), 400
    
    data = json_body()
    test_name = str(data.get("test_name", "")).strip()
    if not test_name:
        return jsonify({"error": "test_name is required"}), 400
    
    try:
        correction_value = float(data.get("correction_value", 0.0))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid correction_value"}), 400
    
    value_column = str(data.get("value_column", "")).strip()
    
    machine_name = box.title or box.uid
    all_data = _load_correction_factors(json_path)
    
    if machine_name not in all_data:
        all_data[machine_name] = {}
    
    prev_entry = all_data[machine_name].get(test_name, {})
    prev_value = float(prev_entry.get("correction_value", 0.0)) if isinstance(prev_entry, dict) else 0.0
    
    all_data[machine_name][test_name] = {
        "equipment": machine_name,
        "test": test_name,
        "value_column": value_column,
        "file_destination": box.csv_path,
        "correction_value": correction_value
    }
    
    try:
        _save_correction_factors(json_path, all_data)
    except Exception as exc:
        return jsonify({"error": f"Failed to save: {exc}"}), 500
    
    # Log the change
    if log_path:
        _log_correction_change(
            log_path, machine_name, test_name, value_column,
            box.csv_path, prev_value, correction_value
        )
    
    return jsonify({"ok": True, "test_name": test_name, "correction_value": correction_value})


@app.delete("/api/boxes/<uid>/corrections/<test_name>")
def api_delete_correction(uid: str, test_name: str):
    """Delete a correction factor for a machine."""
    require_auth()
    cfg = ENGINE.get_config()
    box = next((b for b in cfg.boxes if b.uid == uid), None)
    if not box:
        return jsonify({"error": f"Box {uid} not found"}), 404
    
    json_path, log_path = _get_correction_factor_paths(cfg)
    if not json_path:
        return jsonify({"error": "Correction factor directory not configured"}), 400
    
    machine_name = box.title or box.uid
    all_data = _load_correction_factors(json_path)
    
    if machine_name not in all_data or test_name not in all_data.get(machine_name, {}):
        return jsonify({"error": f"Correction factor for '{test_name}' not found"}), 404
    
    prev_entry = all_data[machine_name].get(test_name, {})
    prev_value = float(prev_entry.get("correction_value", 0.0)) if isinstance(prev_entry, dict) else 0.0
    value_column = prev_entry.get("value_column", "") if isinstance(prev_entry, dict) else ""
    
    del all_data[machine_name][test_name]
    
    # Clean up empty machine entry
    if not all_data[machine_name]:
        del all_data[machine_name]
    
    try:
        _save_correction_factors(json_path, all_data)
    except Exception as exc:
        return jsonify({"error": f"Failed to save: {exc}"}), 500
    
    # Log the deletion (new_value = 0 indicates deletion)
    if log_path:
        _log_correction_change(
            log_path, machine_name, test_name, value_column,
            box.csv_path, prev_value, 0.0
        )
    
    return jsonify({"ok": True, "deleted": test_name})


@app.post("/api/samples")
def api_add_sample():
    require_auth()
    data = json_body()

    def mutate(cfg: AppConfig) -> None:
        tests = [SampleTestSpec.from_dict(t) for t in data.get("tests", [])]
        cfg.samples.append(SampleSpec(
            name=str(data.get("name") or f"Sample {len(cfg.samples) + 1}"),
            sample_id_val=str(data.get("sample_id_val") or ""),
            tests=tests,
        ))
        log_config_change(get_current_user(), "Sample Add", f"Added sample {data.get('name')}")

    cfg = _load_cfg_mutation(mutate)
    return jsonify({"ok": True, "config": serialize_config(cfg)})


@app.patch("/api/samples/<name>")
def api_update_sample(name: str):
    require_auth()
    data = json_body()

    def mutate(cfg: AppConfig) -> None:
        for i, s in enumerate(cfg.samples):
            if s.name == name:
                tests = [SampleTestSpec.from_dict(t) for t in data.get("tests", s.serialize().get("tests", []))]
                cfg.samples[i] = SampleSpec(
                    name=str(data.get("name", s.name)),
                    sample_id_val=str(data.get("sample_id_val", s.sample_id_val)),
                    tests=tests,
                )
                if "sample_id_column" in data:
                    cfg.sample_id_column = str(data.get("sample_id_column") or cfg.sample_id_column)
                return
                return
        raise ValueError(f"Sample {name} not found")
        
    cfg = _load_cfg_mutation(mutate)
    log_config_change(get_current_user(), "Sample Edit", f"Edited sample {name}")
    return jsonify({"ok": True, "config": serialize_config(cfg)})


@app.delete("/api/samples/<name>")
def api_delete_sample(name: str):
    require_auth()

    def mutate(cfg: AppConfig) -> None:
        cfg.samples = [s for s in cfg.samples if s.name != name]
        log_config_change(get_current_user(), "Sample Delete", f"Deleted sample {name}")

    cfg = _load_cfg_mutation(mutate)
    return jsonify({"ok": True, "config": serialize_config(cfg)})


@app.post("/api/samples/changeover")
def api_changeover_sample():
    require_auth()
    data = json_body()
    old_name = str(data.get("old_name") or "").strip()
    new_name = str(data.get("new_name") or "").strip()
    new_id_val = str(data.get("new_id_val") or "").strip()

    if not old_name or not new_name:
        return jsonify({"ok": False, "error": "Missing sample names"}), 400

    updated_count = 0

    def mutate(cfg: AppConfig) -> None:
        nonlocal updated_count
        # Check if new name exists
        if any(s.name == new_name for s in cfg.samples):
            raise ValueError(f"Sample '{new_name}' already exists")

        # Find old sample
        old_sample = next((s for s in cfg.samples if s.name == old_name), None)
        if not old_sample:
            raise ValueError(f"Sample '{old_name}' not found")

        # Create new sample
        new_sample = SampleSpec.from_dict(old_sample.serialize())
        new_sample.name = new_name
        new_sample.sample_id_val = new_id_val
        cfg.samples.append(new_sample)

        # Reassign machines
        for box in cfg.boxes:
            changed = False
            for wt in box.watched_targets:
                if wt.sample == old_name:
                    wt.sample = new_name
                    changed = True
            if changed:
                updated_count += 1

    cfg = _load_cfg_mutation(mutate)
    return jsonify({"ok": True, "config": serialize_config(cfg), "updated_count": updated_count})


@app.post("/api/settings")
def api_settings():
    require_auth()
    data = json_body()

    def mutate(cfg: AppConfig) -> None:
        for key in ("poll_minutes", "map_locked", "report_enabled", "report_time",
                    "report_dir", "status_log_dir", "correction_factor_dir",
                    "theme_mode", "app_font_family", "app_font_size",
                    "ui_scale", "custom_qss_path", "sample_id_column"):
            if key in data:
                setattr(cfg, key, data[key])

    cfg = _load_cfg_mutation(mutate)
    return jsonify({"ok": True, "config": serialize_config(cfg)})


@app.get("/api/maintenance")
def api_maintenance():
    manager = ENGINE.maintenance
    box_uid = request.args.get("box_uid") or None
    tasks = manager.get_tasks(box_uid=box_uid)
    comments = manager.get_comments(box_uid=box_uid)
    return jsonify({
        "tasks": [t.__dict__ for t in tasks],
        "comments": [c.__dict__ for c in comments],
    })


@app.post("/api/maintenance")
def api_create_task():
    require_auth()
    data = json_body()
    mgr = ENGINE.maintenance
    box_uid = str(data.get("box_uid", ""))
    box_title = str(data.get("box_title", ""))
    name = str(data.get("name", "Task"))
    kind = str(data.get("kind", "pm"))
    repeat_value = int(data.get("repeat_value", 1))
    repeat_unit = str(data.get("repeat_unit", "months"))
    try:
        start = datetime.fromisoformat(str(data.get("start_date")))
    except Exception:
        start = datetime.now()
    tpl = mgr.create_task(box_uid, box_title, name, kind, start, repeat_value, repeat_unit)
    if tpl:
        ENGINE.refresh(force=True)
    return jsonify({"ok": bool(tpl), "task": tpl.__dict__ if tpl else None})


@app.post("/api/maintenance/<task_id>/start")
def api_start_task(task_id: str):
    require_auth()
    tpl = ENGINE.maintenance.start_task(task_id)
    ENGINE.refresh(force=True)
    return jsonify({"ok": bool(tpl)})


@app.post("/api/maintenance/<task_id>/complete")
def api_complete_task(task_id: str):
    require_auth()
    data = json_body()
    user = str(data.get("user", "web"))
    comment = str(data.get("comment", ""))
    tpl = ENGINE.maintenance.complete_task(task_id, user, comment)
    if tpl:
        def mutate(cfg: AppConfig) -> None:
            for b in cfg.boxes:
                if b.uid == tpl.box_uid:
                    in_progress = any(t.status == "IN_PROGRESS" and t.box_uid == tpl.box_uid
                                      for t in ENGINE.maintenance.templates.values())
                    if not in_progress:
                        b.manual_override = ""
        ENGINE.update_config(mutate)
    ENGINE.refresh(force=True)
    return jsonify({"ok": bool(tpl)})


@app.post("/api/maintenance/comment")
def api_add_comment():
    require_auth()
    data = json_body()
    box_uid = str(data.get("box_uid", ""))
    box_title = str(data.get("box_title", ""))
    comment = str(data.get("comment", ""))
    user = str(data.get("user", "web"))
    ENGINE.maintenance.add_comment(box_uid, box_title, comment, user)
    ENGINE.refresh(force=True)
    return jsonify({"ok": True})


@app.post("/api/maintenance/<task_id>/update")
def api_update_task(task_id: str):
    require_auth()
    data = json_body()
    tpl = ENGINE.maintenance.update_task(task_id, data)
    ENGINE.refresh(force=True)
    return jsonify({"ok": bool(tpl), "task": tpl.__dict__ if tpl else None})


@app.post("/api/maintenance/<task_id>/delete")
def api_delete_task(task_id: str):
    require_auth()
    success = ENGINE.maintenance.delete_task(task_id)
    ENGINE.refresh(force=True)
    return jsonify({"ok": success})


@app.post("/api/maintenance/<task_id>/cancel")
def api_cancel_task(task_id: str):
    require_auth()
    data = json_body()
    user = str(data.get("user", "web"))
    comment = str(data.get("comment", ""))
    tpl = ENGINE.maintenance.cancel_task(task_id, user, comment)
    
    if tpl:
        # If we cancel, we might need to clear manual override if it was set
        def mutate(cfg: AppConfig) -> None:
             for b in cfg.boxes:
                if b.uid == tpl.box_uid:
                    # check if any OTHER task is in progress
                    in_progress = any(t.status == "IN_PROGRESS" and t.box_uid == tpl.box_uid and t.id != task_id
                                      for t in ENGINE.maintenance.templates.values())
                    if not in_progress:
                        b.manual_override = ""
        ENGINE.update_config(mutate)

    ENGINE.refresh(force=True)
    return jsonify({"ok": bool(tpl)})


@app.get("/api/logs")
def api_logs():
    cfg = ENGINE.get_config()
    base = (cfg.status_log_dir or "").strip()
    status_path = os.path.join(base, "status_changes.csv") if base else ""
    manual_path = os.path.join(base, "manual_overrides.csv") if base else ""
    return jsonify({
        "status_changes": read_log(status_path),
        "manual_overrides": read_log(manual_path),
    })


def build_report_rows(cfg: AppConfig,
                      rows_by_path: Dict[str, List[dict]],
                      sample_id_column: str,
                      samples_by_name: Dict[str, SampleSpec],
                      cache: Optional[LastSeenCache]) -> Tuple[List[str], List[List[str]]]:
    headers = [
        "Box Title", "Box UID", "Box Status", "Override",
        "CSV Path", "QC Expiry (h)", "Last In-Spec QC / Fallback", "Latest Match Time",
        "Reason", "Used Parsed Time",
        "Sample", "Test Name", "Expected", "k*StdDev", "Low", "High", "Latest Value", "Timestamp Source", "In Spec", "Units"
    ]
    out_rows: List[List[str]] = []
    sample_indexes = {path: build_sample_index(rows, sample_id_column) for path, rows in rows_by_path.items()}
    time_caches = {path: {} for path in rows_by_path}
    for box in cfg.boxes:
        rows = rows_by_path.get(box.csv_path, [])
        ev = evaluate_with_cache(
            cache,
            box,
            samples_by_name,
            sample_id_column,
            rows,
            sample_index=sample_indexes.get(box.csv_path),
            row_time_cache=time_caches.get(box.csv_path),
        )
        status, reason = apply_manual_override(box, ev)
        last_qc = ev.last_good_qc.isoformat(sep=" ") if ev.last_good_qc else ""
        latest_match = ev.latest_match_time.isoformat(sep=" ") if ev.latest_match_time else ""
        used_parsed_str = "YES" if getattr(ev, "used_parsed", False) else "NO"
        if ev.results:
            for pr in ev.results:
                sample_name = pr.sample
                test = pr.test
                if test:
                    tol = test.k * test.std_dev
                    low = f"{pr.low:.6g}" if pr.low is not None else ""
                    high = f"{pr.high:.6g}" if pr.high is not None else ""
                    expected = f"{test.expected:.6g}"
                    latest = "" if pr.latest_value is None else f"{pr.latest_value:.6g}"
                    insp = "" if pr.in_spec is None else ("YES" if pr.in_spec else "NO")
                    ts_source = getattr(pr, "timestamp_source", "")
                    out_rows.append([
                        box.title, box.uid, status, (box.manual_override or ""),
                        box.csv_path, f"{box.qc_expire_hours:.2f}", last_qc, latest_match,
                        reason, used_parsed_str,
                        sample_name, test.name, expected, f"{tol:.6g}",
                        low, high, latest, ts_source, insp, test.units,
                    ])
                else:
                    out_rows.append([
                        box.title, box.uid, status, (box.manual_override or ""),
                        box.csv_path, f"{box.qc_expire_hours:.2f}", last_qc, latest_match,
                        reason, used_parsed_str,
                        sample_name, "", "", "", "", "", "", "", "", "",
                    ])
        else:
            out_rows.append([
                box.title, box.uid, status, (box.manual_override or ""),
                box.csv_path, f"{box.qc_expire_hours:.2f}", last_qc, latest_match,
                reason, used_parsed_str,
                "", "", "", "", "", "", "", "", "", "",
            ])
    return headers, out_rows


@app.get("/api/report")
def api_report_preview():
    cfg = ENGINE.get_config()
    sample_id_column = cfg.sample_id_column or "Lab ID"
    samples_by_name = {s.name: s for s in cfg.samples}
    rows_by_path = {b.csv_path: read_csv_rows(b.csv_path) for b in cfg.boxes if b.csv_path}
    headers, rows = build_report_rows(cfg, rows_by_path, sample_id_column, samples_by_name, ENGINE.cache)
    if request.args.get("download") == "1":
        out = os.path.join(BASE_DIR, "report_preview.csv")
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)
        return send_file(out, as_attachment=True, download_name="report.csv")
    return jsonify({"headers": headers, "rows": rows})


@app.route("/api/events")
def api_events():
    def stream():
        q = BUS.subscribe()
        try:
            while True:
                try:
                    event = q.get(timeout=25)
                except queue.Empty:
                    yield "event: ping\ndata: {}\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            BUS.unsubscribe(q)
    return Response(stream(), mimetype="text/event-stream")


# ----- frontend --------------------------------------------------------------



@app.route("/")
def index():
    return render_template("dashboard.html")

@app.route("/v2")
def index_v2():
    return render_template("dashboard.html")


@app.route("/favicon.ico")
def favicon():
    return "", 204


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Lab Manager web server.")
    parser.add_argument("--host", default="0.0.0.0", help="Host/IP to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        global ENGINE
        print("\n\n")
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        print("!!!  VERSION 2.0 LOADED - MD STATUS UPDATE           !!!")
        print("!!!  PORT CHANGED TO 8000                            !!!")
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        print("\n\n")
        ENGINE = StatusEngine()
        # Enable debug mode for auto-reloading templates
        app.run(host=args.host, port=args.port, threaded=True, debug=True)
    finally:
        try:
            ENGINE.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()
