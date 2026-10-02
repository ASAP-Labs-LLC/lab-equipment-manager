"""W1 (bridge on), W4, M2, M6 and DG1 on a server with a LEM store and the
mixed-fleet bridge (transfer §10.1, §10.4, §12.2; piece P9).

Each is asked of the target's own code — `legacy_import`, `bridge`,
`bench_api` through `create_app(LocalStoreGateway, labcore=...)` — against
phase 1's `run_web.WGateway` as LabCore (counting every op, with LabCore's
real watchdog text injected through its `plan`). LabCore's `lem_machine_log`
is declared exactly as v3.9.0 declares it: a rowid table, no key.

Where a scenario needs a v3.9 BENCH (M2's writes, DG1's restart) it is the
real v3.9.0 `lem_station_module`, taken from the tag with `git show` into the
gate's temp root — not a model of it.

Raises `Unsupported` when the target has no bridge (v3.9, or v4 before P9).
"""
import hashlib
import importlib.util
import itertools
import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timedelta

from .world import Unsupported

_serial = itertools.count()

V39_LOG_DDL = ("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
               "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
               "detail TEXT)")
V39_LOG_INDEX = ("CREATE INDEX IF NOT EXISTS idx_lem_log_uid_kind_ts ON "
                 "lem_machine_log(machine_uid, kind, ts DESC)")
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
)
UIDS = ("gc-2", "pac-flash-1")
LOG_COLS = ("machine_uid", "ts", "kind", "lab_id", "test_name", "value",
            "detail")


def _need_bridge():
    try:
        import bridge                                   # noqa: F401
        import legacy_import                            # noqa: F401
    except ImportError:
        raise Unsupported("needs the mixed-fleet bridge and the import tool "
                          "(bridge.py, legacy_import.py: P9)")


def _store(tag):
    from lem_store import LocalStoreGateway
    base = os.path.dirname(os.environ["LEM_STORE_PATH"])
    os.makedirs(base, exist_ok=True)
    return LocalStoreGateway(os.path.join(
        base, "fleet-%s-%03d.sqlite3" % (tag, next(_serial))))


def _lab(rw):
    lab = rw.WGateway()
    for ddl in (V39_LOG_DDL, V39_LOG_INDEX) + V39_TABLES:
        res = lab.fake.sql(ddl)
        assert not res.get("error"), res
    for uid in UIDS:
        lab.fake.sql("INSERT OR IGNORE INTO lem_machine_config VALUES (?, ?, ?, "
                     "'2026-09-23T10:00:00', 'ryan')",
                     [uid, uid, json.dumps({"source_type": "single_csv",
                                            "last_position": 5120})])
        lab.fake.sql("INSERT OR IGNORE INTO lem_machine_status VALUES (?, ?, "
                     "'GREEN', '', '2026-10-01T09:00:00')", [uid, uid])
    return lab


def _ops(lab):
    return sum(lab.counts.values())


def _verified(store, lab):
    import legacy_import
    out = legacy_import.Importer(store, lab, sleep=lambda s: None,
                                 chunk=1000).run()
    if out["state"] != "verified":
        raise AssertionError("import did not verify: %s" % out)
    return out


def _once(match, action="watchdog", kind="read", nth=1):
    seen = {"n": 0}

    def plan(k, cat, sql):
        if k == kind and match in str(sql):
            seen["n"] += 1
            if seen["n"] == nth:
                return action
        return None
    return plan


def _lc_multiset(lab):
    res = lab.fake.read_sql("SELECT machine_uid, ts, kind, lab_id, test_name, "
                            "value, detail FROM lem_machine_log")
    return Counter(tuple(r[c] for c in LOG_COLS) for r in res["rows"])


def _store_multiset(store, where="origin = 'legacy_labcore'"):
    res = store.read_sql("SELECT machine_uid, ts, kind, lab_id, test_name, "
                         "value, detail FROM lem_machine_log_effective WHERE "
                         + where)
    assert not res.get("error"), res
    return Counter(tuple(r[c] for c in LOG_COLS) for r in res["rows"])


def _lost_dup(truth, stored):
    lost = sum(max(0, n - stored[k]) for k, n in truth.items())
    dup = sum(max(0, n - truth[k]) for k, n in stored.items())
    return lost, dup


_V39 = {}


def v39_module():
    """The real tagged v3.9.0 station module, from `git show`."""
    if "m" in _V39:
        return _V39["m"]
    here = os.path.dirname(os.path.abspath(__file__))
    out = subprocess.run(["git", "-C", here, "show",
                          "v3.9.0:LEM Station Module/lem_station_module.py"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if out.returncode != 0:
        raise AssertionError("git show v3.9.0 failed: %s"
                             % out.stderr.decode(errors="replace")[:200])
    base = os.path.dirname(os.environ["LEM_STORE_PATH"])
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, "lem_station_module_v390.py")
    with open(path, "wb") as f:
        f.write(out.stdout)
    spec = importlib.util.spec_from_file_location("lem_station_module_v390",
                                                  path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    _V39["m"] = m
    return m


# ── W1 with the bridge on ───────────────────────────────────────────────────

def w1_bridge_on(rw):
    """30 bridge cycles at 12 s (6 minutes): the state arms once per cycle,
    the log once per 60 s. Then the same 30 with ONE watchdog on a state
    read and ONE on a pull: the extra is what a kill costs."""
    _need_bridge()
    import bridge
    lab = _lab(rw)
    seen = []
    inner = lab.read_sql

    def read_sql(sql, args=None, **kw):
        seen.append(str(sql))
        return inner(sql, args, **kw)
    lab.read_sql = read_sql
    store = _store("w1")
    _verified(store, lab)
    br = bridge.Bridge(store, lab, clock=lambda: 0.0)
    br.cycle(now=0.0)
    a, s0 = _ops(lab), len(seen)
    for i in range(30):
        br.cycle(now=12.0 * (i + 1))
    healthy = _ops(lab) - a
    reads = seen[s0:]
    state_plan = _once("UNION ALL", nth=3)
    pull_plan = _once("FROM lem_machine_log", nth=2)
    lab.plan = lambda k, c, s: state_plan(k, c, s) or pull_plan(k, c, s)
    b = _ops(lab)
    for i in range(30):
        br.cycle(now=372.0 + 12.0 * i)
    lab.plan = None
    after = _ops(lab) - b
    st = br.status()
    return {"ops_in_6_min_healthy": healthy,
            "reads_per_12s": round(sum("UNION ALL" in q for q in reads) / 30.0,
                                   2),
            "pull_reads_per_60s": round(
                sum("FROM lem_machine_log" in q for q in reads) / 6.0, 2),
            "writes_healthy": healthy - len(reads),
            "after_one_watchdog": {"ops_next_30_cycles": after,
                                   "extra_ops_vs_healthy": after - healthy},
            "recovered": st["state"]["last_error"] is None
            and st["pull"]["last_error"] is None}


# ── W4 ──────────────────────────────────────────────────────────────────────

def w4(rw):
    """The bridge's legacy pull killed on its 2nd chunk (100 rows a chunk),
    then resumed: rows held, and unique on legacy_key."""
    _need_bridge()
    import bridge
    lab = _lab(rw)
    store = _store("w4")
    _verified(store, lab)
    for i in range(250):
        lab.fake.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, "
                     "lab_id, test_name, value, detail) VALUES (?,?,?,?,?,?,?)",
                     ["m1", "2026-10-01T09:%02d:%02d" % (i // 60, i % 60),
                      "run", "L%d" % i, "", "", "{}"])
    br = bridge.Bridge(store, lab, chunk=100, clock=lambda: 0.0)
    br.pull()
    lab.plan = _once("FROM lem_machine_log", nth=1)
    killed = br.pull()
    lab.plan = None

    def keys():
        res = store.read_sql("SELECT legacy_key FROM lem_machine_log WHERE "
                             "legacy_key IS NOT NULL")
        assert not res.get("error"), res
        return [r["legacy_key"] for r in res["rows"]]
    after_fail = len(keys())
    for _ in range(10):
        if not br.pull().get("rows"):
            break
    k = keys()
    return {"first_pull": "ok", "second_pull": (killed.get("error") or
                                                "no error")[:100],
            "rows_after_failed_pull": after_fail,
            "rows_after_resume": len(k), "source_rows": 250,
            "unique_legacy_keys": len(set(k)),
            "lost_dup": list(_lost_dup(_lc_multiset(lab),
                                       _store_multiset(store)))}


# ── M2: v4 server, v3.9 bench ───────────────────────────────────────────────

def m2(rw):
    """The real v3.9.0 module's INSERTs into LabCore, pulled by the bridge
    through a watchdog, an N3 double write, a 25-row replay burst and a
    server restart. Truth is LabCore's table."""
    _need_bridge()
    import bridge
    v39 = v39_module()
    lab = _lab(rw)
    store = _store("m2")
    _verified(store, lab)
    br = bridge.Bridge(store, lab, chunk=50, clock=lambda: 0.0)
    t0 = datetime(2026, 10, 2, 8, 0, 0)
    written = []
    for poll in range(60):
        ts = t0 + timedelta(seconds=30 * poll, microseconds=poll)
        batch = [v39.build_log_insert(
            "gc-2", "run", ts, lab_id="L-%05d" % (poll * 3 + i),
            detail={"values": {"Sulfur": "%.4f" % (poll + i / 10)}})[1]
            for i in range(3)]
        sql, args = v39.build_log_batch(batch)
        lab.fake.sql(sql, args)
        written += batch
        if poll == 20:
            lab.fake.sql(sql, args)
        if poll == 40:
            rts = t0 + timedelta(hours=2)
            again = [[a[0], rts.isoformat()] + a[2:] for a in written[:25]]
            lab.fake.sql(*v39.build_log_batch(again))
        if poll == 25:
            lab.plan = _once("FROM lem_machine_log")
        if poll == 33:
            br = bridge.Bridge(store, lab, chunk=50, clock=lambda: 0.0)
        if poll % 2 == 1:
            br.pull()
    lab.plan = None
    for _ in range(20):
        if not br.pull().get("rows"):
            break
    lost, dup = _lost_dup(_lc_multiset(lab), _store_multiset(store))
    res = store.read_sql("SELECT COUNT(*) AS n FROM log_annotation WHERE "
                         "label = 'replay_candidate'")
    return {"lost": lost, "effective_dup": dup,
            "labcore_rows": sum(_lc_multiset(lab).values()),
            "replay_candidates_visible": res["rows"][0]["n"]}


# ── M6: v3.9 → v4 server, v4 bench in legacy projection ─────────────────────

def _bench_app(store, lab):
    import web_app
    from live_presence import LivePresence

    class _Auth:
        def login(self, u, p):
            return (None, "", "bad")

        def logout(self, t):
            pass
    app = web_app.create_app(store, labcore=lab, authenticator=_Auth(),
                             secret="s", live=LivePresence(),
                             live_token="gate-live-token")
    app.config["TESTING"] = True
    return app


class _Bench:
    """A v2 bench speaking §6.1 with the server's own recipes."""

    def __init__(self, client, uid, epoch="ep-1"):
        import bench_api
        self.api = bench_api
        self.client, self.uid, self.epoch = client, uid, epoch
        r = client.post("/api/v2/bench/%s/enroll" % uid,
                        headers={"X-LEM-Token": "gate-live-token"},
                        json={"machine_uid": uid, "module_version": "4.0.0"})
        assert r.status_code == 200, r.get_json()
        self.token = r.get_json()["token"]
        self.records, self.acked = [], 0
        self.digests = [bench_api.DIGEST_ZERO]

    def journal(self, body):
        body = dict(body, seq=len(self.records) + 1, epoch=self.epoch,
                    uid=self.uid)
        self.records.append(body)
        self.digests.append(self.api.chain(self.digests[-1],
                                           self.api.canonical(body)))

    def sync(self, sources=None):
        recs = []
        for b in self.records[self.acked:self.acked + 100]:
            r = dict(b)
            r["crc"] = self.api.record_crc(self.api.canonical(b))
            recs.append(r)
        doc = {"machine_uid": self.uid, "epoch": self.epoch, "proto": 2,
               "module_version": "4.0.0", "from_seq": self.acked + 1,
               "records": recs,
               "stats": {"unacked": len(self.records) - self.acked,
                         "road": "public",
                         "digest": self.digests[self.acked]}}
        if sources is not None:
            doc["sources"] = sources
        r = self.client.post("/api/v2/bench/%s/sync" % self.uid, json=doc,
                             headers={"X-LEM-Bench-Token": self.token,
                                      "User-Agent": "LEM-Station/4.0.0",
                                      "X-LEM-Proto": "2"})
        if r.status_code in (200, 409):
            self.acked = int(r.get_json()["acked"])
        return r

    def drain(self):
        for _ in range(1000):
            if self.acked >= len(self.records):
                return
            r = self.sync()
            assert r.status_code in (200, 409), (r.status_code, r.get_json())


def _run_body(seq, uid, rows=2):
    return {"kind": "run", "ts": "2026-10-01T09:00:%02d-07:00" % (seq % 60),
            "module": "4.0.0", "src": "file", "lab_id": "L-%05d" % seq,
            "values": {"Flash": str(seq)}, "raw": {}, "corrections": {},
            "origin": "live",
            "lh": hashlib.sha256(("%s" % seq).encode()).hexdigest()[:32],
            "log": [[uid, "2026-10-01 09:00:%02d" % (seq % 60), "run",
                     "L-%05d" % seq, "T%d" % i, str(seq + i),
                     json.dumps({"n": i})] for i in range(rows)]}


def _projected(uid, epoch, body, seq):
    import bench_api
    b = dict(body, seq=seq, epoch=epoch, uid=uid)
    return [(uid, ts, kind, lab_id, test, value,
             bench_api._row_detail(detail, "%s:%d" % (epoch, seq)))
            for ts, kind, lab_id, test, value, detail in
            bench_api.log_rows_for(b)]


def m6(rw):
    """Records 1–40 projected to LabCore by a v4 bench under a v3.9 server
    (2 rows each, `detail.jk`); the server goes v4 and imports them; the
    bench's first v2 sync sends its epoch from seq 1. Then records 41–60
    are projected AND synced before the bridge's pull reaches them, and
    record 61 is split by the pull's chunk boundary around a sync."""
    _need_bridge()
    import bridge
    uid = "pac-flash-1"
    lab = _lab(rw)
    bodies = {s: _run_body(s, uid) for s in range(1, 62)}
    for s in range(1, 41):
        for vals in _projected(uid, "ep-1", bodies[s], s):
            lab.fake.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, "
                         "lab_id, test_name, value, detail) VALUES "
                         "(?,?,?,?,?,?,?)", list(vals))
    store = _store("m6")
    _verified(store, lab)
    client = _bench_app(store, lab).test_client()
    bench = _Bench(client, uid)
    for s in range(1, 41):
        bench.journal(bodies[s])
    bench.drain()
    for s in range(41, 61):
        for vals in _projected(uid, "ep-1", bodies[s], s):
            lab.fake.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, "
                         "lab_id, test_name, value, detail) VALUES "
                         "(?,?,?,?,?,?,?)", list(vals))
        bench.journal(bodies[s])
    bench.drain()
    br = bridge.Bridge(store, lab, chunk=1000, clock=lambda: 0.0)
    br.pull()
    for vals in _projected(uid, "ep-1", bodies[61], 61):
        lab.fake.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, "
                     "lab_id, test_name, value, detail) VALUES (?,?,?,?,?,?,?)",
                     list(vals))
    br.chunk = 1
    br.pull()                                   # first row of record 61 only
    bench.journal(bodies[61])
    bench.drain()
    for _ in range(10):
        if not br.pull().get("rows"):
            break
    truth = Counter()
    for s in range(1, 62):
        for vals in _projected(uid, "ep-1", bodies[s], s):
            truth[(vals[3], vals[4], vals[5])] += 1
    res = store.read_sql("SELECT lab_id, test_name, value FROM "
                         "lem_machine_log_effective WHERE machine_uid = ?",
                         [uid])
    stored = Counter((r["lab_id"], r["test_name"], r["value"])
                     for r in res["rows"])
    lost, dup = _lost_dup(truth, stored)
    return {"lost": lost, "effective_dup": dup,
            "records": 61, "rows_truth": sum(truth.values()),
            "rows_stored": sum(stored.values()), "acked": bench.acked}


# ── DG1 ─────────────────────────────────────────────────────────────────────

def dg1(rw):
    """A v2 bench printing every 30 s for 3 h, syncing its source offset;
    the bridge cycling every 6 s. At 12 moments — including the last cycle
    before each mirror is due — the real v3.9.0 module restarts from
    LabCore's config and tails the file: the age of the oldest print it
    reads again is the replay."""
    _need_bridge()
    import bridge
    v39 = v39_module()
    uid = "pac-flash-1"
    lab = _lab(rw)
    store = _store("dg1")
    _verified(store, lab)
    client = _bench_app(store, lab).test_client()
    bench = _Bench(client, uid)
    br = bridge.Bridge(store, lab, clock=lambda: 0.0)
    base = os.path.dirname(os.environ["LEM_STORE_PATH"])
    path = os.path.join(base, "dg1-%03d.csv" % next(_serial))
    open(path, "w").close()
    printed = []
    checks = {600, 2100, 3570, 3594, 4500, 5394, 5400, 6300, 7170, 8100,
              9000, 10794}
    replays = []

    def replayed(now):
        sql, args = v39.build_config_fetch(uid)
        row = lab.fake.read_sql(sql, args)["rows"][0]
        m = v39.machine_from_config_payload(row["config"], uid)
        text, _ = v39.tail_new_text(path, m.last_position)
        n = len([ln for ln in text.splitlines() if ln.strip()])
        return 0.0 if not n else (now - printed[len(printed) - n][1]) / 60.0

    t = 0.0
    while t <= 3 * 3600:
        if int(t) % 30 == 0:
            with open(path, "a") as f:
                f.write("L-%05d,%.2f\n" % (len(printed), 60 + len(printed)
                                           % 7 / 10))
            printed.append((os.path.getsize(path), t))
            bench.journal({"kind": "run", "ts": "2026-10-02T09:00:00-07:00",
                           "module": "4.0.0", "src": "file",
                           "lab_id": "L-%05d" % len(printed),
                           "values": {"Flash": "60"}, "raw": {},
                           "corrections": {}, "origin": "live"})
            r = bench.sync(sources=[{"src": "C:/data/flash.csv",
                                     "cursor": {"offset": printed[-1][0]}}])
            assert r.status_code == 200, r.get_json()
        br.cycle(now=t)
        if int(t) in checks:
            replays.append(round(replayed(t), 2))
        t += 6.0
    # Today: no bridge, so LabCore keeps the offset saved on 09-23.
    lab_today = _lab(rw)
    sql, args = v39.build_config_fetch(uid)
    m = v39.machine_from_config_payload(
        lab_today.fake.read_sql(sql, args)["rows"][0]["config"], uid)
    text, _ = v39.tail_new_text(path, m.last_position)
    n = len([ln for ln in text.splitlines() if ln.strip()])
    return {"replayed_minutes": max(replays), "at_each_downgrade": replays,
            "today_without_bridge_minutes":
                (t - 6.0 - printed[len(printed) - n][1]) / 60.0 if n else 0.0}
