"""Offline harness: the CURRENT LEM station module (and web-server pieces)
driven against the web server's FakeLabCoreGateway, with faults injected.

Nothing in the LEM repository is modified: the
module and the gateway are imported read-only (bytecode writing is disabled so
no __pycache__ lands in the repo). No network: urlopen is refused exactly as
the module's own conftest refuses it, so the live road / floor road are absent
-- which is also what production showed on 2026-10-01 (17/17 machines
`live: false` on lem.asaplabs.net/api/machines).

Run with the web server's venv (it has PySide6 + requests):
  "<repo>/LEM Web Server/.venv/bin/python" run_faults.py

Vendored into gauntlet-harness/baseline/harness (finding F1, 2026-10-06) from
the phase-1 scratchpad, which was deleted. The only change from phase 1 is the
code-path block below: it used to hard-code the main checkout
(~/Projects/lab-equipment-manager) onto the front of sys.path, which
works on one machine only. Now the repo is found relative to this file (or
LEM_HARNESS_REPO), and NOTHING is inserted when the LEM modules are already
imported -- which is how the gate runs: gharness.target.load() imports the
target's own modules first, and those must be the ones that run.
"""
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.dont_write_bytecode = True
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.environ.get("LEM_HARNESS_REPO") or os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
STATION = os.path.join(REPO, "LEM Station Module")
STATION_TESTS = os.path.join(STATION, "tests")
WEB = os.path.join(REPO, "LEM Web Server")
if "lem_station_module" not in sys.modules:      # standalone run only
    for p in (WEB, STATION_TESTS, STATION):
        if p not in sys.path:
            sys.path.insert(0, p)


def _refuse(*a, **k):
    raise OSError("harness: no network")


import urllib.request  # noqa: E402
urllib.request.urlopen = _refuse

from PySide6 import QtWidgets  # noqa: E402
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

import lem_station_module as mod  # noqa: E402
from test_module_qt import make_module  # noqa: E402  (FakeContext/FakeBaseModule)
from labcore_gateway import FakeLabCoreGateway  # noqa: E402


class Kill(BaseException):
    """The process dies here. BaseException so no `except Exception` in the
    module can swallow it -- a real kill is not catchable either."""


BUSY = {"error": "LabCore is busy — write queue is deep. Retry shortly.",
        "busy": True, "retry_after": 5, "pending": 140}


def categorize(kind, sql="", operation=""):
    s = " ".join(str(sql or "").split())
    u = s.upper()
    if kind == "write":
        return "write:" + operation
    if u.startswith("CREATE") or u.startswith("ALTER"):
        return kind + ":DDL"
    for key, name in (
            ("KIND = 'CALIBRATION'", "calibration_epoch"),
            ("KIND = 'QC'", "last_qc"),
            ("LEM_MACHINE_HEARTBEAT", "heartbeat"),
            ("LEM_MACHINE_LOG", "machine_log"),
            ("LEM_MACHINE_STATUS", "status"),
            ("LEM_MACHINE_SUBSTATUS", "substatus"),
            ("LEM_MACHINE_SPECS", "effective_specs"),
            ("LEM_HELD_RESULTS", "held"),
            ("LEM_MACHINE_CONFIG", "config"),
            ("LEM_MACHINE_CONTROL", "override"),
            ("LEM_QC_SAMPLES", "qc_samples"),
            ("LEM_MACHINE_TARGETS", "qc_targets"),
            ("LEM_QC_SPECS", "qc_specs"),
            ("LEM_MAINTENANCE", "maintenance"),
            ("LEM_CORRECTION_FACTORS", "corrections"),
            ("KIND = 'CALIBRATION'", "calibration_epoch"),
            ("KIND = 'QC'", "last_qc"),
            ("LEM_META", "lem_meta"),
            ('"SAMPLES"', "identity"),
            ("PRAGMA", "pragma"),
            ("LEM_", "lem_other")):
        if key in u:
            return kind + ":" + name
    return kind + ":" + s[:40]


class HGateway:
    """FakeLabCoreGateway + an op counter + a fault plan.

    `plan(kind, category, sql_or_op, params)` returns None (normal), or one of
      "refuse"       -> answer BUSY without executing (LabCore's real refusal shape)
      "raise_before" -> raise ConnectionError; nothing executed (request lost)
      "raise_after"  -> execute, then raise TimeoutError (response lost; write LANDED)
      "kill_before"  -> raise Kill; nothing executed
      "kill_after"   -> execute, then raise Kill
    """

    def __init__(self):
        self.fake = FakeLabCoreGateway()
        # Production has the web server's lem_* tables; without them every
        # config read errors, `_config_read_at` never stamps, and the bench
        # re-reads its configuration on every poll -- an artefact, not today.
        import snapshot_service
        for ddl in snapshot_service.SCHEMA_DDL:
            r = self.fake.sql(ddl)
            if r.get("error"):
                raise RuntimeError("schema: " + r["error"])
        for ddl in getattr(mod, "LOG_TABLE_DDL", ()) and [mod.LOG_TABLE_DDL]:
            self.fake.sql(ddl)
        self.counts = Counter()
        self.trace = []
        self.plan = None
        self.running = True
        self.cell_sends = Counter()      # (lab_id, test, value) -> times SENT
        self.cell_lands = Counter()      # -> times it reached the DB

    def _fault(self, kind, cat, payload):
        return self.plan(kind, cat, payload) if self.plan else None

    def is_running(self):
        return self.running

    def _apply(self, action, fn):
        if action == "refuse":
            return dict(BUSY)
        if action == "raise_before":
            raise ConnectionError("harness: connection dropped")
        if action == "kill_before":
            raise Kill("before")
        res = fn()
        if action == "raise_after":
            raise TimeoutError("harness: read timed out (write landed)")
        if action == "kill_after":
            raise Kill("after")
        return res

    def sql(self, sql, args=None, source="", **kw):
        cat = categorize("sql", sql)
        self.counts[cat] += 1
        self.trace.append(cat)
        return self._apply(self._fault("sql", cat, sql),
                           lambda: self.fake.sql(sql, args))

    def read_sql(self, sql, args=None, **kw):
        cat = categorize("read", sql)
        self.counts[cat] += 1
        self.trace.append(cat)
        return self._apply(self._fault("read", cat, sql),
                           lambda: self.fake.read_sql(sql, args))

    def write(self, operation, params=None, source="", **kw):
        cat = categorize("write", operation=operation)
        self.counts[cat] += 1
        self.trace.append(cat)
        ops = (params or {}).get("operations", []) if operation == "batch" else []
        for o in ops:
            p = o.get("params") or {}
            self.cell_sends[(p.get("lab_id"), p.get("test_name"), p.get("value"))] += 1

        def run():
            if operation != "batch":
                return self.fake.write(operation, params or {})
            for o in ops:
                r = self.fake.write(o["operation"], o.get("params") or {})
                if r.get("error"):
                    return r
                p = o.get("params") or {}
                self.cell_lands[(p.get("lab_id"), p.get("test_name"), p.get("value"))] += 1
            return {"ok": True}
        return self._apply(self._fault("write", cat, operation), run)

    # ── accounting ──
    def ops_total(self):
        return sum(self.counts.values())

    def by_kind(self):
        out = Counter()
        for k, v in self.counts.items():
            out[k.split(":", 1)[0]] += v
        return out

    def snapshot(self):
        return Counter(self.counts)

    # ── ground truth ──
    def seed_samples(self, lab_ids):
        for lid in lab_ids:
            self.fake.sql("INSERT OR IGNORE INTO samples (lab_id, first_seen_at) "
                          "VALUES (?, ?)", [lid, "2026-10-01 08:00:00"])

    def log_runs(self, uid=None):
        sql = ("SELECT lab_id, COUNT(*) AS n FROM lem_machine_log WHERE kind='run'"
               + (" AND machine_uid=?" if uid else "") + " GROUP BY lab_id")
        res = self.fake.read_sql(sql, [uid] if uid else [])
        if res.get("error"):
            if "no such table" in res["error"]:
                return {}
            raise RuntimeError("ground-truth read failed: " + res["error"])
        return {r["lab_id"]: int(r["n"]) for r in res["rows"]}

    def results(self, test):
        res = self.fake.read_sql("SELECT lab_id, result FROM sample_tests "
                                 "WHERE test_name=?", [test])
        if res.get("error"):
            raise RuntimeError("ground-truth read failed: " + res["error"])
        return {r["lab_id"]: r["result"] for r in res["rows"]}


def install(gw):
    mod.__dict__["labcore_write"] = gw.write
    mod.__dict__["labcore_sql"] = gw.sql
    mod.__dict__["labcore_read_sql"] = gw.read_sql
    mod.__dict__["labcore_is_running"] = gw.is_running
    mod.__dict__.pop("_run_in_thread", None)   # _in_thread -> synchronous


def density_machine(uid, path, source_type="single_csv", **kw):
    base = dict(uid=uid, title="Bench " + uid, source_type=source_type,
                csv_path=str(path), delimiter=",",
                lab_id=mod.Selector(mode="cell", index=0),
                mappings=[mod.MethodMapping(
                    methods=["Density"],
                    selector=mod.Selector(mode="cell", index=1))])
    base.update(kw)
    return mod.Machine(**base)


def new_bench(gw, machine=None, restore_uid=None):
    """A module as LabStation builds it. `machine` binds a fresh config (and
    publishes it, as the setup dialog does); `restore_uid` is a restart: the
    canvas file holds only the uid, and the config comes back from LabCore."""
    install(gw)
    m = make_module()
    if machine is not None:
        m.set_machine(machine, publish=True)
    if restore_uid:
        m.restore_state({"machine_uid": restore_uid, "poll_seconds": 30})
    return m


def poll(m, now):
    """One poll, synchronously: ingest -> parse -> evaluate -> LabCore sync ->
    show. Exactly `process_now`, which is the same body poll_now runs on the
    worker."""
    m.process_now(now)


def lab_id(i):
    return "100126-%05d" % (10000 + i)


def print_line(i, value=None):
    return "%s,%s\n" % (lab_id(i), value if value is not None else "0.%04d" % (8000 + i))
