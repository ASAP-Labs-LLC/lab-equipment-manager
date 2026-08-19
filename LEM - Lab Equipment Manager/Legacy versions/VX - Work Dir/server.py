#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
server.py - Lightweight local server for Lab Manager Map

Responsibilities:
 - Owns the canonical AppConfig and persists to lab_manager_config.json
 - Monitors CSV sources on an interval and computes box statuses
 - Logs status changes and manual overrides
 - Exposes a small HTTP+JSON API for clients to read state and post actions

No external dependencies: uses http.server and threading.

API (all JSON; Content-Type: application/json):
 - GET  /state                         -> {cfg, boxes: [{uid, title, pos, size, locked, manual_override, status, reason, lines}], map_locked}
 - POST /action/update_view            <- {center:[x,y], zoom:float}            (ignored server-side for now)
 - POST /action/update_box_pos_size    <- {uid, pos:[x,y], size:[w,h], locked?}
 - POST /action/manual_override        <- {uid, mode:"DEAD-LINE"|"SERVICE"|"OFF", user, note}
 - POST /action/add_box                <- {box: BoxConfig.serialize()}
 - POST /action/edit_box               <- {box: BoxConfig.serialize()}
 - POST /action/remove_box             <- {uid}
 - POST /action/settings               <- {map_locked?, poll_minutes?, status_log_dir?, theme_mode?, ...}

Run:  python server.py  [--host 127.0.0.1] [--port 8787] [--poll-seconds 60]
"""

from __future__ import annotations

import json
import os
import copy
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional, Tuple

from models import AppConfig, BoxConfig, STATUS_DEAD, STATUS_SERVICE, STATUS_GREEN, STATUS_YELLOW, STATUS_UNKNOWN
from config_store import load_config, save_config
from data_source import evaluate_box, CsvReadWorker  # use evaluate_box only
from maintenance import MaintenanceManager


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class State:
    def __init__(self, poll_seconds: int) -> None:
        self.lock = threading.RLock()
        self.cfg: AppConfig = load_config()
        print(f"[SVR] loaded config: boxes={len(self.cfg.boxes)} samples={len(self.cfg.samples)} status_log_dir={self.cfg.status_log_dir!r}", flush=True)
        self._last_rows_cache: Dict[str, List[dict]] = {}
        self._last_status_by_uid: Dict[str, str] = {}
        self._stop = threading.Event()
        self._poll_seconds = max(1, int(poll_seconds))
        self._thread: Optional[threading.Thread] = None
        # Maintenance (PMs)
        base_dir = os.path.join(os.path.dirname(__file__), "Maintenance")
        # Disable legacy/global fallback so deleted PMs don't reappear from old files
        self.maintenance = MaintenanceManager(base_dir, use_global_fallback=False)
        self._sync_maintenance_dirs()
        self._state_payload: Dict[str, object] = {}
        self._state_dirty = True
        self._state_version = 0
        self._initialize_state_cache()

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

    def _initialize_state_cache(self) -> None:
        with self.lock:
            base_boxes = []
            for box in self.cfg.boxes:
                base_boxes.append({
                    "uid": box.uid,
                    "title": box.title,
                    "pos": list(box.pos),
                    "size": list(box.size),
                    "locked": bool(box.locked),
                    "manual_override": box.manual_override,
                    "status": STATUS_UNKNOWN,
                    "reason": "initializing",
                    "lines": [],
                })
            self._state_payload = {
                "last_updated": _now_iso(),
                "state_version": self._state_version,
                "map_locked": bool(self.cfg.map_locked),
                "poll_minutes": int(self.cfg.poll_minutes),
                "sample_id_column": self.cfg.sample_id_column,
                "boxes": base_boxes,
                "maintenance": {"active": [], "tasks": []},
                "theme_mode": self.cfg.theme_mode,
                "ui_scale": self.cfg.ui_scale,
            }
            self._state_dirty = True
            self._persist_state_snapshot_locked()

    def _refresh_maintenance_locked(self) -> None:
        try:
            self._sync_maintenance_dirs()
            self.maintenance.reload()
            self.maintenance.refresh_statuses(save=False)
            self._write_active_pms_csv()
        except Exception as exc:
            print(f"[SVR] maintenance refresh error: {exc}", flush=True)

    def _build_maintenance_snapshot_locked(self) -> dict:
        active = []
        last_start: Dict[str, str] = {}
        for entry in self.maintenance.log:
            if getattr(entry, "action", "") == "start" and getattr(entry, "task_id", ""):
                last_start[entry.task_id] = entry.timestamp
        for tpl in self.maintenance.templates.values():
            if tpl.status == "IN_PROGRESS":
                active.append({
                    "box_uid": tpl.box_uid,
                    "box_title": tpl.box_title,
                    "task_id": tpl.id,
                    "task_name": tpl.name,
                    "kind": tpl.kind,
                    "started_at": last_start.get(tpl.id, ""),
                    "next_due": tpl.next_due,
                    "status": tpl.status,
                })
        tasks = [{
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
        } for tpl in self.maintenance.templates.values()]
        try:
            tasks.sort(key=lambda x: (x["box_uid"], x.get("next_due", "")))
        except Exception:
            pass
        return {"active": active, "tasks": tasks}

    def _persist_state_snapshot_locked(self) -> None:
        try:
            out_dir = (self.cfg.status_log_dir or os.path.dirname(__file__)).strip()
            if not out_dir:
                return
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, "state.json"), "w", encoding="utf-8") as fh:
                json.dump(self._state_payload, fh, indent=2)
        except Exception:
            pass

    def _compute_state_locked(self, rows_by_path: Optional[Dict[str, List[dict]]] = None) -> None:
        if rows_by_path is None:
            rows_by_path = self._last_rows_cache
        samples_by_name = {s.name: s for s in self.cfg.samples}
        sample_id_column = self.cfg.sample_id_column or "Lab ID"
        out_boxes: List[dict] = []
        for box in self.cfg.boxes:
            rows = rows_by_path.get(box.csv_path, [])
            ev = evaluate_box(box, samples_by_name, sample_id_column, rows)
            status = ev.status
            reason = ev.reason
            if box.manual_override == STATUS_DEAD:
                status = STATUS_DEAD
                reason = "Manual override: DEAD-LINE"
            elif box.manual_override == STATUS_SERVICE:
                status = STATUS_SERVICE
                reason = "Manual override: SERVICE"
            if status == STATUS_GREEN and ev.last_good_qc:
                if (datetime.utcnow() - ev.last_good_qc) > timedelta(hours=box.qc_expire_hours):
                    status = STATUS_YELLOW
                    reason = "Last in-spec QC is stale."
            prev_status = self._last_status_by_uid.get(box.uid)
            if prev_status and prev_status != status:
                self._log_status_change(box, prev_status, status, reason)
            self._last_status_by_uid[box.uid] = status
            lines: List[str] = []
            for pr in ev.results[:4]:
                if pr.test:
                    tol = pr.test.k * pr.test.std_dev
                    rng = f"[{pr.low:.6g}, {pr.high:.6g}]" if pr.low is not None else "-"
                    vtxt = "-" if pr.latest_value is None else f"{pr.latest_value:.6g}"
                    flag = "" if pr.in_spec is None else ("OK" if pr.in_spec else "OUT")
                    units = f" {pr.test.units}" if pr.test.units else ""
                    lines.append(f"{pr.test.name}: {vtxt}{units} {flag}  +/-{tol:.6g}  {rng}".strip())
                else:
                    lines.append("(missing test)")
            if len(ev.results) > 4:
                lines.append(f"+{len(ev.results) - 4} more...")
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
            })
        maintenance = self._build_maintenance_snapshot_locked()
        self._state_version += 1
        self._state_payload = {
            "last_updated": _now_iso(),
            "state_version": self._state_version,
            "map_locked": bool(self.cfg.map_locked),
            "poll_minutes": int(self.cfg.poll_minutes),
            "sample_id_column": self.cfg.sample_id_column,
            "boxes": out_boxes,
            "maintenance": maintenance,
            "theme_mode": self.cfg.theme_mode,
            "ui_scale": self.cfg.ui_scale,
        }
        self._state_dirty = False
        self._persist_state_snapshot_locked()

    def _mark_state_dirty(self) -> None:
        self._state_dirty = True

    def _loop(self) -> None:
        # initial immediate run
        try:
            self.refresh_all()
        except Exception:
            pass
        # periodic
        while not self._stop.wait(self._poll_seconds):
            try:
                self.refresh_all()
            except Exception:
                continue

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
        except Exception:
            pass

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
        except Exception:
            pass

    def refresh_all(self) -> None:
        paths = sorted({b.csv_path for b in self.cfg.boxes if b.csv_path})
        print(f"[SVR] refresh_all paths={len(paths)}", flush=True)
        rows_by_path: Dict[str, List[dict]] = {}
        for path in paths:
            try:
                import csv
                rows: List[dict] = []
                if os.path.exists(path):
                    print(f"[SVR] reading CSV: {path}", flush=True)
                    with open(path, "r", newline="", encoding="utf-8-sig") as f:
                        reader = csv.DictReader(f)
                        rows.extend(reader)
                rows_by_path[path] = rows
                print(f"[SVR] read rows: {len(rows)} from {path}", flush=True)
            except Exception as exc:
                rows_by_path[path] = []
                print(f"[SVR] ERROR reading CSV: {path} ({exc})", flush=True)
        with self.lock:
            self._last_rows_cache = rows_by_path
            try:
                self._refresh_maintenance_locked()
            except Exception:
                pass
            self._compute_state_locked(rows_by_path)

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

    # ----- Maintenance actions -----
    def pm_delete(self, task_id: str, user: str, reason: str) -> bool:
        with self.lock:
            try:
                self.maintenance.reload()
            except Exception:
                pass
            tpl = self.maintenance.templates.get(task_id)
            if not tpl:
                return False
            try:
                self.maintenance.log_delete(tpl.box_uid, tpl.box_title, tpl.id, tpl.name, user or "", reason or "")
            except Exception:
                pass
            self.maintenance.remove_task(task_id)
            try:
                self._write_active_pms_csv()
            except Exception:
                pass
            self._mark_state_dirty()
            return True

    def serialize_state(self) -> dict:
        with self.lock:
            if self._state_dirty:
                try:
                    self._refresh_maintenance_locked()
                except Exception:
                    pass
                self._compute_state_locked()
            return copy.deepcopy(self._state_payload)


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
                self._mark_state_dirty()
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
                    self._mark_state_dirty()
                    try:
                        self._log_manual_override(b, f"{action}", user, note)
                    except Exception:
                        pass
                    return True
        return False

    def add_or_edit_box(self, box_dict: dict) -> bool:
        print(f"[SVR] add_or_edit_box keys={list((box_dict or {}).keys())}", flush=True)
        try:
            new_box = BoxConfig.from_dict(box_dict)
        except Exception:
            return False
        with self.lock:
            replaced = False
            for i, b in enumerate(self.cfg.boxes):
                if b.uid == new_box.uid and new_box.uid:
                    self.cfg.boxes[i] = new_box
                    replaced = True
                    break
            if not replaced:
                self.cfg.boxes.append(new_box)
            save_config(self.cfg)
            self._mark_state_dirty()
            # Keep maintenance dirs current if csv_path changed or new box added
            try:
                self._sync_maintenance_dirs()
                self.maintenance.refresh_statuses(save=False)
                self._write_active_pms_csv()
            except Exception as e:
                print(f"[SVR] add_or_edit_box maintenance sync error: {e}", flush=True)
        return True

    def remove_box(self, uid: str) -> bool:
        print(f"[SVR] remove_box uid={uid}", flush=True)
        with self.lock:
            before = len(self.cfg.boxes)
            self.cfg.boxes = [b for b in self.cfg.boxes if b.uid != uid]
            if len(self.cfg.boxes) != before:
                save_config(self.cfg)
                self._mark_state_dirty()
                # Remove any maintenance data for this box uid, and update active PMs
                try:
                    self.maintenance.remove_all_for_box(uid)
                    self._sync_maintenance_dirs()
                    self.maintenance.refresh_statuses(save=False)
                    self._write_active_pms_csv()
                except Exception as e:
                    print(f"[SVR] remove_box maintenance cleanup error: {e}", flush=True)
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
            save_config(self.cfg)
            self._mark_state_dirty()


GLOBAL_STATE: Optional[State] = None


class Handler(BaseHTTPRequestHandler):
    server_version = "LabMapServer/1.0"

    def _json(self, status: int, obj: dict) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):  # type: ignore[N802]
        if self.path.startswith("/state"):
            print(f"[SVR] GET /state", flush=True)
            st = GLOBAL_STATE
            if not st:
                self._json(503, {"error": "server not ready"})
                return
            self._json(200, st.serialize_state())
            return
        if self.path.startswith("/config"):
            print(f"[SVR] GET /config", flush=True)
            st = GLOBAL_STATE
            if not st:
                self._json(503, {"error": "server not ready"})
                return
            with st.lock:
                self._json(200, st.cfg.serialize())
            return
        if self.path.startswith("/active_pms"):
            print(f"[SVR] GET /active_pms", flush=True)
            st = GLOBAL_STATE
            if not st:
                self._json(503, {"error": "server not ready"})
                return
            # Return current active PMs as JSON
            out = []
            with st.lock:
                # Force reload from disk to reflect immediate deletes/edits
                try:
                    st.maintenance.reload()
                except Exception:
                    pass
                st.maintenance.refresh_statuses(save=False)
                last_start = {}
                for e in st.maintenance.log:
                    if e.action == "start" and e.task_id:
                        last_start[e.task_id] = e.timestamp
                for t in st.maintenance.templates.values():
                    if t.status == "IN_PROGRESS":
                        out.append({
                            "box_uid": t.box_uid,
                            "box_title": t.box_title,
                            "task_id": t.id,
                            "task_name": t.name,
                            "kind": t.kind,
                            "started_at": last_start.get(t.id, ""),
                            "next_due": t.next_due,
                            "status": t.status,
                        })
            self._json(200, {"generated_at": _now_iso(), "active": out})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):  # type: ignore[N802]
        st = GLOBAL_STATE
        if not st:
            self._json(503, {"error": "server not ready"})
            return
        body = self._read_json()
        print(f"[SVR] POST {self.path} body_keys={list((body or {}).keys())}", flush=True)
        if self.path == "/action/update_view":
            # Currently ignored server-wide
            self._json(200, {"ok": True})
            return
        if self.path == "/action/update_box_pos_size":
            found, changed = st.update_box_pos_size(
                uid=str(body.get("uid", "")),
                pos=body.get("pos"),
                size=body.get("size"),
                locked=body.get("locked"),
            )
            # Always 200; report details to avoid noisy logs when nothing changed
            self._json(200, {"ok": bool(found), "changed": bool(changed)})
            return
        if self.path == "/action/manual_override":
            ok = st.manual_override(
                uid=str(body.get("uid", "")),
                mode=str(body.get("mode", "")),
                user=str(body.get("user", "")),
                note=str(body.get("note", "")),
            )
            self._json(200 if ok else 400, {"ok": ok})
            return
        if self.path == "/action/add_box" or self.path == "/action/edit_box":
            ok = st.add_or_edit_box(body.get("box") or {})
            self._json(200 if ok else 400, {"ok": ok})
            return
        if self.path == "/action/remove_box":
            ok = st.remove_box(str(body.get("uid", "")))
            self._json(200 if ok else 400, {"ok": ok})
            return
        if self.path == "/action/settings":
            st.update_settings(body or {})
            self._json(200, {"ok": True})
            return
        if self.path == "/action/pm_delete":
            ok = st.pm_delete(
                task_id=str(body.get("task_id", "")),
                user=str(body.get("user", "")),
                reason=str(body.get("reason", "")),
            )
            # Return updated maintenance snapshot for clients to refresh immediately
            self._json(200 if ok else 404, {"ok": ok, "maintenance": st.serialize_state().get("maintenance", {})})
            return
        self._json(404, {"error": "not found"})


def run(host: str = "127.0.0.1", port: int = 8787, poll_seconds: int = 60) -> None:
    global GLOBAL_STATE
    GLOBAL_STATE = State(poll_seconds=poll_seconds)
    GLOBAL_STATE.start()
    httpd = ThreadingHTTPServer((host, int(port)), Handler)
    try:
        print(f"LabMap server running on http://{host}:{port}  (poll: {poll_seconds}s)")
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        GLOBAL_STATE.stop()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--poll-seconds", type=int, default=60)
    args = ap.parse_args()
    run(args.host, args.port, args.poll_seconds)
