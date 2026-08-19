#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
viewer_server.py - Lightweight LAN viewer for Lab Manager status.

Run:
    python viewer_server.py --host 0.0.0.0 --port 8080 --refresh 30

The server renders a read-only dashboard backed by the same CSV + config data
used by the desktop app. It automatically refreshes data on a timer and serves
JSON + HTML so multiple browsers can monitor machine status across the network.
"""

from __future__ import annotations

import argparse
import csv
import os
import threading
import time
from datetime import datetime
from typing import Dict, List, Tuple

from flask import Flask, jsonify, render_template_string

from config_store import load_config
from data_source import BoxEvaluation, ParameterResult, build_sample_index, evaluate_box
from models import (
    BoxConfig,
    SampleSpec,
    STATUS_DEAD,
    STATUS_GREEN,
    STATUS_RED,
    STATUS_SERVICE,
    STATUS_UNKNOWN,
    STATUS_YELLOW,
)


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Lab Manager Viewer</title>
  <style>
    * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; background: #f5f5f5; color: #222; }
    header { background: #1d3557; color: #fff; padding: 1rem 2rem; display: flex; flex-wrap: wrap; align-items: center; }
    header h1 { margin: 0; font-size: 1.6rem; flex: 1 1 auto; }
    header .meta { font-size: 0.95rem; }
    #errors { background: #ffe3e3; color: #a30000; margin: 1rem 2rem 0; padding: 0.75rem 1rem; border-radius: 6px; display: none; }
    #cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 1rem; padding: 1rem 2rem 2rem; }
    .card { border-radius: 10px; box-shadow: 0 2px 6px rgba(0,0,0,0.08); overflow: hidden; border: 2px solid transparent; background: #fff; display: flex; flex-direction: column; }
    .card-header { padding: 0.8rem 1rem; color: #fff; font-weight: 600; display: flex; justify-content: space-between; align-items: center; }
    .status-badge { font-size: 0.85rem; padding: 0.2rem 0.6rem; border-radius: 999px; background: rgba(255,255,255,0.25); color: #fff; }
    .card-body { padding: 1rem; flex: 1 1 auto; display: flex; flex-direction: column; gap: 0.6rem; }
    .reason { font-size: 0.9rem; color: #333; }
    .info-line { font-size: 0.85rem; color: #666; }
    .spec-list, .result-list { list-style: none; padding: 0; margin: 0; }
    .spec-list li { font-size: 0.85rem; color: #555; }
    .result-row { display: flex; justify-content: space-between; font-size: 0.85rem; padding: 0.2rem 0; border-bottom: 1px solid #e5e5e5; }
    .result-row:last-child { border-bottom: none; }
    .result-row span { max-width: 50%; }
    footer { text-align: center; font-size: 0.85rem; color: #777; padding: 0.5rem 1rem 1.5rem; }
    .muted { color: #888; font-size: 0.85rem; }
    .manual-tag { font-size: 0.8rem; font-weight: 600; padding: 0.15rem 0.45rem; border-radius: 4px; background: rgba(255,255,255,0.2); margin-left: 0.5rem; }
  </style>
</head>
<body>
  <header>
    <h1>Lab Manager Viewer</h1>
    <div class="meta">
      Last update: <span id="updatedAt">--</span><br>
      Refresh interval: <span id="interval">{{ refresh_seconds }}</span> s
    </div>
  </header>
  <section id="errors"></section>
  <section id="cards"></section>
  <footer>Served by viewer_server.py — read-only snapshot of Lab Manager data.</footer>
  <script>
    const REFRESH_MS = {{ refresh_ms }};
    async function fetchStatus() {
      try {
        const res = await fetch('/api/status');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        renderDashboard(data);
      } catch (err) {
        renderError('Failed to load status: ' + err.message);
      }
    }

    function renderDashboard(data) {
      document.getElementById('updatedAt').textContent = data.generated_at || '--';
      const errors = document.getElementById('errors');
      if (data.errors && data.errors.length) {
        errors.style.display = 'block';
        errors.innerHTML = data.errors.map(e => '<div><strong>' + (e.path || 'server') + ':</strong> ' + e.error + '</div>').join('');
      } else {
        errors.style.display = 'none';
        errors.textContent = '';
      }

      const cards = document.getElementById('cards');
      if (!data.boxes || !data.boxes.length) {
        cards.innerHTML = '<p class="muted">No machines configured.</p>';
        return;
      }
      cards.innerHTML = data.boxes.map(box => cardHtml(box)).join('');
    }

    function escapeHtml(str) {
      return (str || '').toString()
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function cardHtml(box) {
      const results = (box.results || []).map(r => {
        const trend = r.in_spec === true ? '✔️' : (r.in_spec === false ? '⚠️' : '•');
        const val = r.value_display || '-';
        const ts = r.timestamp ? ('<span class="muted">' + escapeHtml(r.timestamp) + '</span>') : '';
        return '<div class="result-row"><span>' + trend + ' ' + escapeHtml(r.label) + '</span><span style="text-align:right">' + escapeHtml(val) + '<br>' + ts + '</span></div>';
      }).join('');

      const specs = (box.spec || []).map(item => '<li>' + escapeHtml(item.sample + ' / ' + item.test) + '</li>').join('') || '<li class="muted">No watched tests</li>';
      const manual = box.manual_override ? '<span class="manual-tag">' + escapeHtml(box.manual_override) + '</span>' : '';
      const latest = box.latest_match_time ? ('Latest row: ' + escapeHtml(box.latest_match_time)) : 'No recent rows';

      return `
        <article class="card" style="border-color:${box.status_color}">
          <div class="card-header" style="background:${box.status_color}">
            <span>${escapeHtml(box.title)}</span>
            <span class="status-badge">${escapeHtml(box.status)}${manual}</span>
          </div>
          <div class="card-body">
            <div class="reason">${escapeHtml(box.reason || 'No reason')}</div>
            <div class="info-line">${escapeHtml(latest)}</div>
            <div class="info-line">Source: ${escapeHtml(box.csv_name)}</div>
            <div>
              <strong>Watched Tests</strong>
              <ul class="spec-list">${specs}</ul>
            </div>
            <div>
              <strong>Latest Results</strong>
              <div class="result-list">
                ${results || '<div class="muted">No results available</div>'}
              </div>
            </div>
          </div>
        </article>
      `;
    }

    function renderError(message) {
      const errors = document.getElementById('errors');
      errors.style.display = 'block';
      errors.textContent = message;
    }

    fetchStatus();
    setInterval(fetchStatus, REFRESH_MS);
  </script>
</body>
</html>
"""

STATUS_COLORS = {
    STATUS_GREEN: "#2ecc71",
    STATUS_YELLOW: "#f1c40f",
    STATUS_RED: "#e74c3c",
    STATUS_DEAD: "#111111",
    STATUS_SERVICE: "#7f8c8d",
STATUS_UNKNOWN: "#95a5a6",
}

CSV_CACHE: Dict[str, dict] = {}


def format_timestamp(dt: datetime | None) -> str | None:
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


def result_to_dict(pr: ParameterResult) -> dict:
    label = pr.sample
    if pr.test and pr.test.name:
        label = f"{pr.sample} / {pr.test.name}"
    return {
        "label": label,
        "value": pr.latest_value,
        "value_display": format_value(pr.latest_value, pr.test.units if pr.test else ""),
        "in_spec": pr.in_spec,
        "low": pr.low,
        "high": pr.high,
        "note": pr.note,
        "timestamp": format_timestamp(pr.latest_time),
        "timestamp_source": pr.timestamp_source,
    }


def load_csv_rows(paths: List[str]) -> Tuple[Dict[str, List[dict]], Dict[str, str]]:
    rows: Dict[str, List[dict]] = {}
    errors: Dict[str, str] = {}
    for path in paths:
        if not path:
            continue
        cache_entry = None
        mtime = None
        try:
            mtime = os.path.getmtime(path)
            cache_entry = CSV_CACHE.get(path)
        except Exception:
            cache_entry = None
        if cache_entry and cache_entry.get("mtime") == mtime:
            rows[path] = cache_entry.get("rows", [])
            continue
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                parsed_rows = list(reader)
                rows[path] = parsed_rows
                CSV_CACHE[path] = {"mtime": mtime, "rows": parsed_rows}
        except Exception as exc:
            rows[path] = []
            errors[path] = f"{type(exc).__name__}: {exc}"
            CSV_CACHE.pop(path, None)
    return rows, errors


def apply_manual_override(box: BoxConfig, evaluation: BoxEvaluation) -> Tuple[str, str]:
    status = evaluation.status
    reason = evaluation.reason
    if box.manual_override == STATUS_DEAD:
        status = STATUS_DEAD
        reason = "Manual override: DEAD-LINE"
    elif box.manual_override == STATUS_SERVICE:
        status = STATUS_SERVICE
        reason = "Manual override: SERVICE"
    return status, reason


def build_status_snapshot() -> dict:
    cfg = load_config()
    sample_id_column = cfg.sample_id_column or "Lab ID"
    samples_by_name: Dict[str, SampleSpec] = {s.name: s for s in cfg.samples}
    csv_paths = sorted({b.csv_path for b in cfg.boxes if b.csv_path})
    rows_by_path, path_errors = load_csv_rows(csv_paths)
    sample_indexes = {path: build_sample_index(rows, sample_id_column) for path, rows in rows_by_path.items()}
    row_time_caches: Dict[str, Dict[int, Tuple[datetime, str]]] = {path: {} for path in rows_by_path}

    boxes_payload: List[dict] = []
    for box in cfg.boxes:
        rows = rows_by_path.get(box.csv_path, [])
        evaluation = evaluate_box(
            box,
            samples_by_name,
            sample_id_column,
            rows,
            sample_index=sample_indexes.get(box.csv_path),
            row_time_cache=row_time_caches.get(box.csv_path),
        )
        status, reason = apply_manual_override(box, evaluation)
        payload = {
            "uid": box.uid,
            "title": box.title,
            "status": status,
            "status_color": STATUS_COLORS.get(status, "#607d8b"),
            "reason": reason,
            "manual_override": box.manual_override or "",
            "latest_match_time": format_timestamp(evaluation.latest_match_time),
            "csv_path": box.csv_path,
            "csv_name": os.path.basename(box.csv_path) if box.csv_path else "(none)",
            "spec": [{"sample": wt.sample, "test": wt.test} for wt in box.watched_targets],
            "results": [result_to_dict(pr) for pr in evaluation.results],
        }
        boxes_payload.append(payload)

    errors_payload = [{"path": path, "error": err} for path, err in path_errors.items()]

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "boxes": boxes_payload,
        "errors": errors_payload,
    }


class StatusAggregator:
    """Background refresher so HTTP handlers stay fast."""

    def __init__(self, refresh_seconds: int = 30):
        self.refresh_seconds = max(5, int(refresh_seconds))
        self._lock = threading.Lock()
        self._snapshot: dict = {"generated_at": None, "boxes": [], "errors": []}
        self._stop = threading.Event()
        self.refresh()  # prime cache synchronously
        self._thread = threading.Thread(target=self._loop, name="status-refresh", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.refresh_seconds):
            self.refresh()

    def refresh(self) -> None:
        try:
            snapshot = build_status_snapshot()
        except Exception as exc:
            snapshot = {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "boxes": [],
                "errors": [{"path": "[server]", "error": f"{type(exc).__name__}: {exc}"}],
            }
        with self._lock:
            self._snapshot = snapshot

    def get_snapshot(self) -> dict:
        with self._lock:
            return dict(self._snapshot)

    def shutdown(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)


def create_app(aggregator: StatusAggregator) -> Flask:
    app = Flask(__name__)

    @app.route("/api/status")
    def api_status():
        data = aggregator.get_snapshot()
        data["refresh_seconds"] = aggregator.refresh_seconds
        return jsonify(data)

    @app.route("/")
    def index():
        return render_template_string(
            HTML_TEMPLATE,
            refresh_ms=aggregator.refresh_seconds * 1000,
            refresh_seconds=aggregator.refresh_seconds,
        )

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Lab Manager Viewer web server.")
    parser.add_argument("--host", default="0.0.0.0", help="Host/IP to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    parser.add_argument(
        "--refresh",
        type=int,
        default=30,
        help="Seconds between CSV refreshes (minimum 5, default 30).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    aggregator = StatusAggregator(refresh_seconds=args.refresh)
    app = create_app(aggregator)
    try:
        app.run(host=args.host, port=args.port, threaded=True)
    finally:
        aggregator.shutdown()


if __name__ == "__main__":
    main()
