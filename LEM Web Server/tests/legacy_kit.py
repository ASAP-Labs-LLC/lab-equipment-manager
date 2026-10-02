"""A LabCore for the import tool and the bridge tests (transfer §10.1, §10.4).

Everything here is offline. "LabCore" is `labcore_gateway.InMemoryLabCore`
(the in-memory fake, NOT the store-backed `FakeLabCoreGateway` conftest swaps
in) holding `lem_machine_log` exactly as v3.9 declares it on LabCore: a plain
rowid table, seven columns, no key, the three indexes v3.9's server creates.
Rows are loaded from a COPY of the log mirror (`fixtures/log-mirror-copy.
sqlite3`, the phase-1 copy) with each row at its own LabCore rowid, so the
rowids the importer verifies against are the ones the mirror recorded.

`LabCore` counts every queue op and can answer any one of them with LabCore's
real refusal texts: the 8 s watchdog (`LabCore_main._wop_read_sql`) and the
busy answer with `retry_after`. "A watchdog on chunk 2" is `fail_on("rowid >=",
nth=2)`: the second read whose SQL contains that text gets the watchdog.
"""
import json
import os
import random
import sqlite3
from collections import Counter

import labcore_gateway

HERE = os.path.dirname(os.path.abspath(__file__))
MIRROR_FIXTURE = os.path.join(HERE, "fixtures", "log-mirror-copy.sqlite3")

#: v3.9.0's own text (snapshot_service.SCHEMA_DDL at the tag).
V39_LOG_DDL = ("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
               "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
               "detail TEXT)")
V39_LOG_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_lem_log_ts ON lem_machine_log(ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_lem_log_uid_kind_ts "
    "ON lem_machine_log(machine_uid, kind, ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_lem_log_lab_ts "
    "ON lem_machine_log(lab_id, ts DESC)",
)
#: The v3.9 tables the bridge projects into and the state arms it reads, with
#: v3.9's DDL, plus two the import must copy that the bridge never touches.
V39_TABLES = (
    "CREATE TABLE IF NOT EXISTS lem_machine_config (machine_uid TEXT PRIMARY "
    "KEY, title TEXT NOT NULL, config TEXT, updated_at TEXT, updated_by TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_machine_status (machine_uid TEXT PRIMARY "
    "KEY, title TEXT, status TEXT, reason TEXT, updated_at TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_machine_heartbeat (machine_uid TEXT "
    "PRIMARY KEY, last_poll TEXT, watching TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_machine_substatus (machine_uid TEXT "
    "PRIMARY KEY, qc TEXT, pm TEXT, calibration TEXT, updated_at TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_machine_specs (machine_uid TEXT NOT NULL, "
    "test_name TEXT NOT NULL, sample_id TEXT, expected REAL, std_dev REAL, "
    "k REAL, units TEXT, low REAL, high REAL, last_qc_at TEXT, last_qc_value "
    "REAL, last_qc_in_spec INTEGER, correction REAL DEFAULT 0.0, updated_at "
    "TEXT, PRIMARY KEY (machine_uid, test_name))",
    "CREATE TABLE IF NOT EXISTS lem_correction_factors (machine_uid TEXT NOT "
    "NULL, test_name TEXT NOT NULL, correction REAL NOT NULL DEFAULT 0.0, "
    "units TEXT, updated_at TEXT, updated_by TEXT, PRIMARY KEY (machine_uid, "
    "test_name))",
    "CREATE TABLE IF NOT EXISTS lem_machine_control (machine_uid TEXT PRIMARY "
    "KEY, manual_override TEXT, comment TEXT, updated_at TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_qc_samples (name TEXT PRIMARY KEY, "
    "sample_id_val TEXT, tests TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_machine_targets (machine_uid TEXT NOT "
    "NULL, sample_name TEXT NOT NULL, test_name TEXT NOT NULL, PRIMARY KEY "
    "(machine_uid, sample_name, test_name))",
    "CREATE TABLE IF NOT EXISTS lem_meta (key TEXT PRIMARY KEY, value TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_held_results (machine_uid TEXT, lab_id "
    "TEXT, test_name TEXT, value TEXT, held_at TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_checklist_state (day TEXT, item TEXT, "
    "done INTEGER, by TEXT, at TEXT)",
)

WATCHDOG = {"error": "Read cancelled after 8s to protect the write queue "
                     "(query too slow — likely an unindexed scan)."}
BUSY = {"error": "LabCore is busy (queue depth 120); retry shortly.",
        "busy": True, "retry_after": 5}

LOG_COLS = ("machine_uid", "ts", "kind", "lab_id", "test_name", "value",
            "detail")


class LabCore:
    """LabCore, counted, with planned refusals."""

    def __init__(self):
        self.fake = labcore_gateway.InMemoryLabCore()
        self.calls = []                 # (kind, sql)
        self._faults = []               # [match, kind, nth, answer, seen]

    # ── counting ──
    @property
    def reads(self):
        return sum(1 for k, _ in self.calls if k == "read")

    @property
    def writes(self):
        return sum(1 for k, _ in self.calls if k == "write")

    def ops_since(self, mark):
        return len(self.calls) - mark

    # ── faults ──
    def fail_on(self, match, nth=1, answer=None, kind="read", times=1):
        """The nth op (counted from now) of `kind` whose SQL contains `match`
        is answered with `answer` (the watchdog by default), `times` times."""
        self._faults.append([match, kind, nth, dict(answer or WATCHDOG), 0,
                             times])

    def _fault(self, kind, sql):
        for f in self._faults:
            match, fkind, nth, answer, seen, times = f
            if fkind != kind or match not in sql or times <= 0:
                continue
            f[4] = seen = seen + 1
            if seen >= nth:
                f[5] = times - 1
                return dict(answer)
        return None

    # ── LabCore's surface ──
    def is_running(self):
        return True

    def read_sql(self, sql, args=None, **kw):
        self.calls.append(("read", str(sql)))
        bad = self._fault("read", str(sql))
        if bad is not None:
            return bad
        return self.fake.read_sql(sql, args)

    def sql(self, sql, args=None, **kw):
        self.calls.append(("write", str(sql)))
        bad = self._fault("write", str(sql))
        if bad is not None:
            return bad
        return self.fake.sql(sql, args)

    def write(self, operation, params=None, **kw):
        params = params or {}
        if operation == "read_sql":
            return self.read_sql(params.get("sql", ""), params.get("args"))
        return self.sql(params.get("sql", operation), params.get("args"))

    def get_test_names(self, **kw):
        self.calls.append(("read", "get_test_names"))
        return self.fake.get_test_names()

    def get_samples(self, **kw):
        self.calls.append(("read", "get_samples"))
        return self.fake.get_samples()

    # ── direct access for the test (not counted) ──
    def q(self, sql, args=None):
        res = self.fake.read_sql(sql, args)
        assert "error" not in res, res
        return res["rows"]

    def x(self, sql, args=None):
        res = self.fake.sql(sql, args)
        assert "error" not in res, res
        return res


# ── loading LabCore ─────────────────────────────────────────────────────────

def declare_log(lab):
    lab.x(V39_LOG_DDL)
    for ddl in V39_LOG_INDEXES:
        lab.x(ddl)


def load_log(lab, rows):
    """rows: [(rowid, (machine_uid, ts, kind, lab_id, test_name, value,
    detail))], each landing at its own rowid."""
    declare_log(lab)
    for rid, vals in rows:
        lab.x("INSERT INTO lem_machine_log (rowid, machine_uid, ts, kind, "
              "lab_id, test_name, value, detail) VALUES (?,?,?,?,?,?,?,?)",
              [rid] + list(vals))


def append_log(lab, vals):
    """A v3.9-shaped INSERT (no rowid named): what a bench does."""
    lab.x("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
          "test_name, value, detail) VALUES (?,?,?,?,?,?,?)", list(vals))


def mirror_rows(path=MIRROR_FIXTURE):
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        return [(r[0], tuple(r[1:])) for r in con.execute(
            "SELECT rowid_src, machine_uid, ts, kind, lab_id, test_name, "
            "value, detail FROM log ORDER BY rowid_src")]
    finally:
        con.close()


def make_mirror(path, rows, filled=True):
    """A log mirror in v3.9's LogMirror format."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE log (rowid_src INTEGER PRIMARY KEY, machine_uid "
                "TEXT, ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value "
                "TEXT, detail TEXT)")
    con.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO log VALUES (?,?,?,?,?,?,?,?)",
                    [(rid,) + tuple(v) for rid, v in rows])
    if filled:
        con.execute("INSERT INTO meta VALUES ('filled_at', "
                    "'2026-10-01T20:15:43+00:00')")
    con.commit()
    con.close()
    return path


def labcore_from_mirror(path=MIRROR_FIXTURE, tables=True):
    lab = LabCore()
    load_log(lab, mirror_rows(path))
    if tables:
        seed_v39_tables(lab)
    return lab


def seed_v39_tables(lab, checklist_rows=40):
    for ddl in V39_TABLES:
        lab.x(ddl)
    lab.x("INSERT INTO lem_machine_config VALUES ('pac-flash-1', 'PAC Flash "
          "1', ?, '2026-09-23T10:00:00', 'ryan')",
          [json.dumps({"title": "PAC Flash 1", "source_type": "single_csv",
                       "csv_path": "C:/data/flash.csv", "last_position": 5120,
                       "interval_seconds": 30})])
    lab.x("INSERT INTO lem_machine_config VALUES ('gc-2', 'Agilent GC 2', ?, "
          "'2026-09-24T09:00:00', 'ryan')",
          [json.dumps({"title": "Agilent GC 2", "source_type": "single_csv",
                       "csv_path": "C:/gc2/out.csv", "last_position": 0})])
    lab.x("INSERT INTO lem_machine_status VALUES ('pac-flash-1', 'PAC Flash "
          "1', 'GREEN', 'ok', '2026-10-01T09:00:00')")
    lab.x("INSERT INTO lem_machine_status VALUES ('gc-2', 'Agilent GC 2', "
          "'YELLOW', 'QC due', '2026-10-01T09:00:00')")
    lab.x("INSERT INTO lem_machine_heartbeat VALUES ('pac-flash-1', "
          "'2026-10-01T09:00:00', 'C:/data/flash.csv')")
    lab.x("INSERT INTO lem_correction_factors VALUES ('pac-flash-1', "
          "'Flash Point', 0.5, 'C', '2026-09-01T08:00:00', 'ryan')")
    lab.x("INSERT INTO lem_machine_control VALUES ('gc-2', '', '', "
          "'2026-09-01T08:00:00')")
    lab.x("INSERT INTO lem_qc_samples VALUES ('AF26', 'STD-1', ?)",
          [json.dumps([{"test_name": "Flash Point", "expected": 63.7}])])
    lab.x("INSERT INTO lem_machine_targets VALUES ('pac-flash-1', 'AF26', "
          "'Flash Point')")
    lab.x("INSERT INTO lem_meta VALUES ('live_url', 'http://192.168.1.5:5557')")
    lab.x("INSERT INTO lem_held_results VALUES ('gc-2', 'L-1', 'Sulfur', "
          "'0.1', '2026-09-30T10:00:00')")
    for i in range(checklist_rows):
        lab.x("INSERT INTO lem_checklist_state VALUES (?, ?, 1, 'sam', ?)",
              ["2026-09-%02d" % (1 + i % 28), "item-%d" % i,
               "2026-09-%02dT08:00:00" % (1 + i % 28)])


def vacuum_renumber(lab, delete_rowids=()):
    """What VACUUM does to a rowid table without an INTEGER PRIMARY KEY once
    rows have been deleted: the survivors are packed into 1..n, in their old
    order. Done the long way so the test controls it exactly."""
    for rid in delete_rowids:
        lab.x("DELETE FROM lem_machine_log WHERE rowid = ?", [rid])
    rows = lab.q("SELECT machine_uid, ts, kind, lab_id, test_name, value, "
                 "detail FROM lem_machine_log ORDER BY rowid")
    lab.x("DROP TABLE lem_machine_log")
    load_log(lab, [(i + 1, tuple(r[c] for c in LOG_COLS))
                   for i, r in enumerate(rows)])


# ── comparing ───────────────────────────────────────────────────────────────

def lc_multiset(lab):
    return Counter(tuple(r[c] for c in LOG_COLS) for r in lab.q(
        "SELECT machine_uid, ts, kind, lab_id, test_name, value, detail "
        "FROM lem_machine_log"))


def store_multiset(store, effective=True, legacy_only=True):
    view = "lem_machine_log_effective" if effective else "lem_machine_log"
    where = " WHERE origin = 'legacy_labcore'" if legacy_only else ""
    res = store.read_sql(
        # raw-log: the tests compare the whole record, hidden rows included
        "SELECT machine_uid, ts, kind, lab_id, test_name, value, detail "
        "FROM %s%s" % (view, where))
    assert "error" not in res, res
    return Counter(tuple(r[c] for c in LOG_COLS) for r in res["rows"])


def lost_and_dup(truth, stored):
    """§9.4's tally, no term zeroed: lost = Σ max(0, truth − stored),
    dup = Σ max(0, stored − truth)."""
    lost = sum(max(0, n - stored[k]) for k, n in truth.items())
    dup = sum(max(0, n - truth[k]) for k, n in stored.items())
    return lost, dup


def store_rows(store, sql, args=None):
    res = store.read_sql(sql, args)
    assert "error" not in res, res
    return res["rows"]


def synthetic_rows(n, uids=("gc-1", "gc-2", "eraspec", "nir"), start=1,
                   seed=7):
    """n v3.9-shaped log rows, rowids start..start+n-1, with N3 doubles."""
    rnd = random.Random(seed)
    out = []
    rid = start
    while len(out) < n:
        uid = rnd.choice(uids)
        sec = rid * 7
        vals = (uid, "2026-09-%02dT%02d:%02d:%02d.%06d" % (
            1 + (sec // 86400) % 28, (sec // 3600) % 24, (sec // 60) % 60,
            sec % 60, rid % 1000000), rnd.choice(("run", "run", "qc")),
            "L-%05d" % rnd.randint(1, 99999), "Sulfur",
            "%.4f" % rnd.random(), json.dumps({"values": {"S": rid}}))
        out.append((rid, vals))
        rid += 1
        if rnd.random() < 0.01 and len(out) < n:      # N3: the same row twice
            out.append((rid, vals))
            rid += 1
    return out


# ── the real v3.9.0 module, for the mixed-fleet tests ──────────────────────

_V39 = {}


def v39_module():
    """`lem_station_module` exactly as tagged v3.9.0, loaded under its own
    name from `git show` — so M2's writes and DG1's restart are the real
    v3.9 code's, not a model of it. Fails (never skips) if git cannot
    produce it: a mixed-fleet claim with no v3.9 in it is not one."""
    if "m" in _V39:
        return _V39["m"]
    import importlib.util
    import subprocess
    import tempfile
    out = subprocess.run(
        ["git", "-C", HERE, "show",
         "v3.9.0:LEM Station Module/lem_station_module.py"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert out.returncode == 0, out.stderr.decode(errors="replace")
    path = os.path.join(tempfile.mkdtemp(prefix="lem-v390-"),
                        "lem_station_module_v390.py")
    with open(path, "wb") as f:
        f.write(out.stdout)
    spec = importlib.util.spec_from_file_location("lem_station_module_v390",
                                                  path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _V39["m"] = mod
    return mod
