#!/usr/bin/env python3
"""devworld.py — a rich, deterministic offline lab for building the 3D floor.

Runs the real LEM web app against a FakeLabCoreGateway seeded with a fleet that
looks like the real lab: seven instruments, every status the floor can paint,
sub-statuses, effective QC specs, maintenance, and a background thread that
writes a `run` event every few seconds so parse traffic (the trains) is
constantly in flight.

    python devworld.py --port 5599
"""
import argparse
import os
import random
import sys
import threading
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.expanduser("~/LAB-lem/LEM Web Server")
sys.path.insert(0, SERVER)

from labcore_gateway import FakeLabCoreGateway  # noqa: E402
import snapshot_service  # noqa: E402
import web_app  # noqa: E402

FLEET = [
    # uid,          title,           status,      qc,        pm,        cal,       pos
    ("multitek-ns", "Multitek NS",   "GREEN",     "GREEN",   "GREEN",   "GREEN",   (0.0, 0.0)),
    ("multitek-s",  "Multitek S",    "YELLOW",    "YELLOW",  "GREEN",   "UNKNOWN", (2.05, 0.0)),
    ("optimpp-1",   "OptiMPP 1",     "GREEN",     "GREEN",   "GREEN",   "YELLOW",  (4.10, 0.0)),
    ("optimpp-2",   "OptiMPP 2",     "RED",       "RED",     "GREEN",   "GREEN",   (0.0, 2.05)),
    ("pac-flash-1", "PAC Flash 1",   "SERVICE",   "UNKNOWN", "RED",     "GREEN",   (2.05, 2.05)),
    ("pac-flash-2", "PAC Flash 2",   "DEAD-LINE", "RED",     "RED",     "RED",     (4.10, 2.05)),
    ("koehler-cp",  "Koehler CP",    "UNKNOWN",   "UNKNOWN", "UNKNOWN", "UNKNOWN", (6.15, 0.0)),
]

REASONS = {
    "GREEN": "Flash Point 64.9 C — in spec (63.7 ± 2.10).",
    "YELLOW": "Last QC 26 hours ago — the standard is due.",
    "RED": "Cloud Point -12.4 C — outside 63.7 ± 2.10.",
    "SERVICE": "Taken out of service by rcurtis — pump rebuild.",
    "DEAD-LINE": "Dead-lined: three consecutive QC failures.",
    "UNKNOWN": "No QC assigned.",
}

TESTS = {
    "multitek-ns": [("Sulfur", 315.0, 300.0, 330.0, "ppm", 316.4),
                    ("Nitrogen", 12.0, 10.5, 13.5, "ppm", 12.2)],
    "multitek-s":  [("Sulfur", 315.0, 300.0, 330.0, "ppm", 331.8)],
    "optimpp-1":   [("Cloud Point", -14.0, -16.0, -12.0, "C", -14.3),
                    ("Pour Point", -21.0, -24.0, -18.0, "C", -20.5)],
    "optimpp-2":   [("Cloud Point", -14.0, -16.0, -12.0, "C", -9.8)],
    "pac-flash-1": [("Flash Point", 63.72, 61.62, 65.82, "C", 64.1)],
    "pac-flash-2": [("Flash Point", 63.72, 61.62, 65.82, "C", 58.4)],
    "koehler-cp":  [],
}


def _stamp(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def seed(gw):
    now = datetime.now()
    for ddl in snapshot_service.SNAPSHOT_TABLES if hasattr(
            snapshot_service, "SNAPSHOT_TABLES") else []:
        gw.sql(ddl)
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status (machine_uid TEXT "
           "PRIMARY KEY, title TEXT, status TEXT, reason TEXT, updated_at TEXT)")
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_heartbeat (machine_uid TEXT "
           "PRIMARY KEY, last_poll TEXT, watching TEXT)")
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_substatus (machine_uid TEXT "
           "PRIMARY KEY, qc TEXT, pm TEXT, calibration TEXT)")
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_layout (machine_uid TEXT "
           "PRIMARY KEY, pos_x REAL, pos_y REAL)")
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_specs (machine_uid TEXT, "
           "test_name TEXT, low REAL, high REAL, expected REAL, units TEXT, "
           "sample_id TEXT, last_qc_value REAL, correction REAL, "
           "last_qc_at TEXT, last_qc_in_spec INTEGER, "
           "PRIMARY KEY (machine_uid, test_name))")
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
           "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
           "detail TEXT)")
    gw.sql("CREATE TABLE IF NOT EXISTS lem_maintenance (machine_uid TEXT, "
           "uid TEXT PRIMARY KEY, name TEXT, kind TEXT, interval_days INTEGER, "
           "last_done TEXT, note TEXT)")
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_targets (machine_uid TEXT, "
           "sample_name TEXT, test_name TEXT, "
           "PRIMARY KEY (machine_uid, sample_name, test_name))")

    for uid, title, status, qc, pm, cal, pos in FLEET:
        beat = now - timedelta(seconds=25)
        if uid == "koehler-cp":
            beat = now - timedelta(hours=6)          # a module that stopped
        gw.sql("INSERT OR REPLACE INTO lem_machine_status VALUES (?,?,?,?,?)",
               [uid, title, status, REASONS[status], _stamp(now - timedelta(minutes=2))])
        gw.sql("INSERT OR REPLACE INTO lem_machine_heartbeat VALUES (?,?,?)",
               [uid, _stamp(beat), "C:/LabData/%s" % uid])
        gw.sql("INSERT OR REPLACE INTO lem_machine_substatus VALUES (?,?,?,?)",
               [uid, qc, pm, cal])
        gw.sql("INSERT OR REPLACE INTO lem_machine_layout VALUES (?,?,?)",
               [uid, pos[0], pos[1]])
        for name, exp, low, high, units, last in TESTS[uid]:
            gw.sql("INSERT OR REPLACE INTO lem_machine_specs VALUES "
                   "(?,?,?,?,?,?,?,?,?,?,?)",
                   [uid, name, low, high, exp, units, "STD-1", last, 0.0,
                    _stamp(now - timedelta(hours=2)), int(low <= last <= high)])
            gw.sql("INSERT OR REPLACE INTO lem_machine_targets VALUES (?,?,?)",
                   [uid, "Diesel - AO25", name])
        gw.sql("INSERT OR REPLACE INTO lem_maintenance VALUES (?,?,?,?,?,?,?)",
               [uid, uid + "-pm", "Monthly PM", "pm", 30,
                _stamp(now - timedelta(days=12)), ""])

    # A little history so the feed and the trains have something to replay.
    for i in range(40):
        uid = FLEET[i % len(FLEET)][0]
        gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
               [uid, _stamp(now - timedelta(minutes=i * 3)), "run",
                "L-%04d" % (2200 + i), "Flash Point", "64.%d" % (i % 10), ""])


def traffic(gw, every=4.0):
    """A parse every few seconds, so trains are always running."""
    rng = random.Random(7)
    n = 3000
    while True:
        time.sleep(every)
        uid = rng.choice([m[0] for m in FLEET if m[2] not in ("DEAD-LINE",)])
        n += 1
        gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
               [uid, _stamp(datetime.now()), "run", "L-%04d" % n,
                "Flash Point", "%.1f" % (63 + rng.random() * 3), ""])


def main():
    ap = argparse.ArgumentParser()
    # 5599 is what tests/test_tray.py binds — squatting on it fails the suite.
    ap.add_argument("--port", type=int, default=5601)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-traffic", action="store_true")
    args = ap.parse_args()

    gw = FakeLabCoreGateway()
    seed(gw)
    app = web_app.create_app(gw)
    # Flask caches templates when debug is off, so an edit to floor.html is
    # invisible until a restart — which cost real time twice while building the
    # 3D floor, once with a stale `col is not defined` that had already been
    # fixed on disk. This is a dev harness; always re-read.
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True
    app.config["SNAPSHOTS"].start()
    from live_presence import start_live_channel
    start_live_channel(app, gw, args.host, args.port)
    if not args.no_traffic:
        threading.Thread(target=traffic, args=(gw,), daemon=True).start()
    print("devworld on http://%s:%d/floor" % (args.host, args.port), flush=True)
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
