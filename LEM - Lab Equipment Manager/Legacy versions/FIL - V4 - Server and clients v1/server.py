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
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional, Tuple

from models import AppConfig, BoxConfig, STATUS_DEAD, STATUS_SERVICE, STATUS_GREEN, STATUS_YELLOW
from config_store import load_config, save_config
from data_source import evaluate_box, CsvReadWorker  # use evaluate_box only


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class State:
    def __init__(self, poll_seconds: int) -> None:
        self.lock = threading.RLock()
        self.cfg: AppConfig = load_config()
        self._last_rows_cache: Dict[str, List[dict]] = {}
        self._last_status_by_uid: Dict[str, str] = {}
        self._stop = threading.Event()
        self._poll_seconds = max(1, int(poll_seconds))
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is None:
            self._stop.clear()
            t = threading.Thread(target=self._loop, name="csv-monitor", daemon=True)
            t.start()
            self._thread = t

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=3)
        self._thread = None

    def set_poll_seconds(self, seconds: int) -> None:
        with self.lock:
            self._poll_seconds = max(1, int(seconds))

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
        rows_by_path: Dict[str, List[dict]] = {}
        for path in paths:
            try:
                import csv
                rows: List[dict] = []
                if os.path.exists(path):
                    with open(path, "r", newline="", encoding="utf-8-sig") as f:
                        reader = csv.DictReader(f)
                        rows.extend(reader)
                rows_by_path[path] = rows
            except Exception:
                rows_by_path[path] = []

        with self.lock:
            self._last_rows_cache = rows_by_path
            # compute statuses per box
            samples_by_name = {s.name: s for s in self.cfg.samples}
            sample_id_column = self.cfg.sample_id_column or "Lab ID"
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

                # stale check (mirror client logic)
                if status == STATUS_GREEN and ev.last_good_qc:
                    if (datetime.utcnow() - ev.last_good_qc) > timedelta(hours=box.qc_expire_hours):
                        status = STATUS_YELLOW
                        reason = "Last in-spec QC is stale."

                prev_status = self._last_status_by_uid.get(box.uid)
                if prev_status and prev_status != status:
                    self._log_status_change(box, prev_status, status, reason)
                self._last_status_by_uid[box.uid] = status

    def serialize_state(self) -> dict:
        with self.lock:
            # Build a lightweight view for clients
            rows_by_path = self._last_rows_cache
            out_boxes: List[dict] = []
            samples_by_name = {s.name: s for s in self.cfg.samples}
            sample_id_column = self.cfg.sample_id_column or "Lab ID"
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
                })

            return {
                "last_updated": _now_iso(),
                "map_locked": bool(self.cfg.map_locked),
                "poll_minutes": int(self.cfg.poll_minutes),
                "sample_id_column": self.cfg.sample_id_column,
                "boxes": out_boxes,
                # include theme bits clients may want; clients can ignore
                "theme_mode": self.cfg.theme_mode,
                "ui_scale": self.cfg.ui_scale,
            }

    # ----- Actions -----
    def update_box_pos_size(self, uid: str, pos: Optional[List[float]], size: Optional[List[float]], locked: Optional[bool]) -> bool:
        changed = False
        with self.lock:
            for b in self.cfg.boxes:
                if b.uid == uid:
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
        return changed

    def manual_override(self, uid: str, mode: str, user: str, note: str) -> bool:
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
                    return True
        return False

    def add_or_edit_box(self, box_dict: dict) -> bool:
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
        return True

    def remove_box(self, uid: str) -> bool:
        with self.lock:
            before = len(self.cfg.boxes)
            self.cfg.boxes = [b for b in self.cfg.boxes if b.uid != uid]
            if len(self.cfg.boxes) != before:
                save_config(self.cfg)
                return True
        return False

    def update_settings(self, payload: dict) -> None:
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
            st = GLOBAL_STATE
            if not st:
                self._json(503, {"error": "server not ready"})
                return
            self._json(200, st.serialize_state())
            return
        if self.path.startswith("/config"):
            st = GLOBAL_STATE
            if not st:
                self._json(503, {"error": "server not ready"})
                return
            with st.lock:
                self._json(200, st.cfg.serialize())
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):  # type: ignore[N802]
        st = GLOBAL_STATE
        if not st:
            self._json(503, {"error": "server not ready"})
            return
        body = self._read_json()
        if self.path == "/action/update_view":
            # Currently ignored server-wide
            self._json(200, {"ok": True})
            return
        if self.path == "/action/update_box_pos_size":
            ok = st.update_box_pos_size(
                uid=str(body.get("uid", "")),
                pos=body.get("pos"),
                size=body.get("size"),
                locked=body.get("locked"),
            )
            self._json(200 if ok else 400, {"ok": ok})
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
