"""The REAL station module against the REAL v2 bench API: what a filing poll
costs LabCore once the bench speaks v2 (transfer spec §6.3, gate row E1).

Why this file exists. Measured by the gate on the v4 module before it spoke
v2 at all: a bench printing one reading per poll cost LabCore 5.43 ops per
poll, 3.0 more than the same bench idle — the reading's machine-log row, the
identity read and the batch, on top of an idle floor of heartbeats, config,
override, QC-library and maintenance reads. LabCore's queue serialises at
about 1.5 ops/s for the whole lab, so that per-bench cost is what multiplies
by every bench Ryan adds. The spec's answer is that a v2 bench sends its own
world (log rows, status, specs, heartbeat, config) to LEM over
`POST /api/v2/bench/<uid>/sync` and reads its configuration from
`GET /api/v2/bench/<uid>/config`, so the ONLY LabCore traffic left on a
filing poll is the results road: one identity-and-cell read, one batch.

Everything here is real except the network: the module's urlopen is routed
to the Flask test client by host, the way the gate's HServer does it. The
bench's LabCore and the server's store are two different databases, so a
statement that reaches LabCore is counted and cannot hide in the store.

The bench's LEM traffic runs on its uploader thread, never on the poll, so
after a bind, a poll or a pulse each test gives the uploader the time a real
poll interval would (`settle`) before it counts anything — otherwise a test
would be counting a race.

What it does NOT claim: road selection under faults, enrolment approval and
blind mode have their own tests (`LEM Station Module/tests/test_v2_road.py`
and the server's enrolment tests).
"""
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import pytest

import bench_v2_kit as kit
from labcore_counter import CountingLabCore

MODULE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                          "LEM Station Module"))
UID = "b1"
LAN = "192.168.1.5:5557"
PUBLIC = "lem.asaplabs.net"
T0 = datetime(2026, 10, 1, 9, 0, 0)


def _module():
    for p in (os.path.join(MODULE_DIR, "tests"), MODULE_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    import lem_station_module as m
    import test_module_qt
    return m, test_module_qt


class BenchLabCore:
    """The LabCore a bench writes to: LabCore's core tables plus the `lem_*`
    tables v3.9 kept there, `batch` answering as LabCore_main._wop_batch does
    (`ok` with one entry per index), and every call counted."""

    def __init__(self, samples):
        import labcore_gateway
        import snapshot_service
        self.fake = labcore_gateway.InMemoryLabCore()
        for ddl in snapshot_service.SCHEMA_DDL:
            assert "error" not in self.fake.sql(ddl)
        for lab in samples:
            self.fake.sql("INSERT OR IGNORE INTO samples (lab_id) VALUES (?)",
                          [lab])
        self.calls = []

    def publish_live(self, url, token):
        """What web_server.pyw's boot step writes, as the server would."""
        from live_presence import META_DDL, META_UPSERT, LIVE_URL_KEY, \
            LIVE_TOKEN_KEY
        self.fake.sql(META_DDL)
        self.fake.sql(META_UPSERT, [LIVE_URL_KEY, url])
        self.fake.sql(META_UPSERT, [LIVE_TOKEN_KEY, token])

    def mark(self):
        return len(self.calls)

    def since(self, mark):
        return self.calls[mark:]

    def sql(self, sql, args=None, **kw):
        self.calls.append(("sql", sql))
        return self.fake.sql(sql, args)

    def read_sql(self, sql, args=None, **kw):
        self.calls.append(("read", sql))
        return self.fake.read_sql(sql, args)

    def write(self, operation, params=None, source="", **kw):
        self.calls.append(("write", operation))
        if operation != "batch":
            return self.fake.write(operation, params or {})
        results = []
        for i, op in enumerate((params or {}).get("operations") or []):
            r = self.fake.write(op.get("operation"), op.get("params") or {})
            results.append(dict(index=i, **({"error": r["error"]}
                                            if r.get("error") else {"ok": True})))
        return {"ok": True, "results": results}

    def is_running(self):
        return True

    def cell(self, lab, test):
        r = self.fake.read_sql("SELECT result FROM sample_tests WHERE lab_id=? "
                               "AND test_name=?", [lab, test])
        return r["rows"][0]["result"] if r["rows"] else None


class Roads:
    """urlopen, routed by host to the Flask test client. `down` refuses the
    connection (nothing reaches the app); `old` answers 404 like a v3.9
    server that has no v2."""

    def __init__(self, client):
        self.client = client
        self.mode = "up"
        self.requests = []

    def syncs(self):
        return sum(1 for m, p in self.requests
                   if m == "POST" and p.endswith("/sync"))

    def urlopen(self, req, data=None, timeout=None, **kw):
        if isinstance(req, str):
            req = urllib.request.Request(req, data=data)
        parts = urllib.parse.urlsplit(req.full_url)
        assert parts.netloc in (LAN, PUBLIC), "unrouted host " + parts.netloc
        method = req.get_method()
        self.requests.append((method, parts.path))
        if self.mode == "down":
            raise urllib.error.URLError(ConnectionRefusedError(61, "refused"))
        if self.mode == "old":
            raise urllib.error.HTTPError(req.full_url, 404, "NOT FOUND", {},
                                         io.BytesIO(b"{}"))
        path = parts.path + ("?" + parts.query if parts.query else "")
        resp = self.client.open(path, method=method,
                                data=req.data if req.data is not None else data,
                                headers=dict(req.header_items()))
        body = resp.get_data()
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(req.full_url, resp.status_code,
                                         resp.status, dict(resp.headers),
                                         io.BytesIO(body))

        class R(io.BytesIO):
            status = resp.status_code
            headers = dict(resp.headers)

            def getheader(self, k, d=None):
                return self.headers.get(k, d)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return R(body)


@pytest.fixture
def world(tmp_path, monkeypatch):
    m, qt = _module()
    monkeypatch.setenv("LEM_JOURNAL_DIR", str(tmp_path / "journal"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    import labcore_gateway
    store = labcore_gateway.FakeLabCoreGateway()      # the store (conftest)
    kit.seed_machine(store, uid=UID, title="Bench b1")
    app = kit.make_app(store, labcore=CountingLabCore())
    app.config["SNAPSHOTS"].refresh()
    roads = Roads(app.test_client())
    monkeypatch.setattr(urllib.request, "urlopen", roads.urlopen)
    labs = ["100126-%05d" % (10000 + i) for i in range(200)]
    lab = BenchLabCore(labs)
    lab.publish_live("http://" + LAN, kit.SHARED_TOKEN)
    for name, fn in (("labcore_write", lab.write), ("labcore_sql", lab.sql),
                     ("labcore_read_sql", lab.read_sql),
                     ("labcore_is_running", lab.is_running)):
        monkeypatch.setitem(m.__dict__, name, fn)
    monkeypatch.delitem(m.__dict__, "_run_in_thread", raising=False)
    # One clock: a bind wakes the uploader at bench_now(), the polls run on
    # T0 + 30 s steps (as the gate pins it).
    monkeypatch.setattr(m, "bench_now", lambda: T0)
    path = tmp_path / "f.csv"
    path.write_text("")
    bench = qt.make_module()
    bench.set_machine(m.Machine(
        uid=UID, title="Bench b1", source_type="single_csv",
        csv_path=str(path), delimiter=",",
        lab_id=m.Selector(mode="cell", index=0),
        mappings=[m.MethodMapping(methods=["Density"],
                                  selector=m.Selector(mode="cell", index=1))]),
        publish=True)

    def settle():
        assert bench._uploader_wait_idle(30.0), "the uploader never went idle"
        up = bench._uploader
        assert up is None or up.errors == 0, up.last_error
    settle()

    class W:
        pass
    w = W()
    w.m, w.bench, w.lab, w.store, w.roads, w.path, w.labs = \
        m, bench, lab, store, roads, path, labs
    w.n = 0

    def poll(k, prints=1):
        with open(path, "a") as f:
            for _ in range(prints):
                f.write("%s,0.%04d\n" % (labs[w.n], 8000 + w.n))
                w.n += 1
        bench.process_now(T0 + timedelta(seconds=30 * k))
        settle()
    w.poll, w.settle = poll, settle
    yield w
    bench.shutdown()
    store.close()


def _bench_log_rows(store):
    res = store.read_sql("SELECT kind, lab_id FROM lem_machine_log WHERE "
                         "machine_uid = ? AND kind = 'run'", [UID])
    assert "error" not in res, res
    return res["rows"]


class TestAFilingPollInV2:
    def test_a_filing_poll_costs_labcore_the_results_road_and_nothing_else(
            self, world):
        """E1: ≤ 2 LabCore ops per filing poll, and they are the identity
        read and the batch. Measured from poll 2 on, as the gate's steady
        state is; the first poll is E2's and is pinned below."""
        world.poll(0)
        per_poll = []
        for k in range(1, 21):
            mark = world.lab.mark()
            world.poll(k)
            per_poll.append(world.lab.since(mark))
        assert all(len(ops) <= 2 for ops in per_poll), per_poll
        everything = [op for ops in per_poll for op in ops]
        assert not [op for op in everything if "LEM_" in op[1].upper()], \
            everything
        assert sorted({op[0] for op in everything}) == ["read", "write"]
        assert {op[1] for op in everything if op[0] == "write"} == {"batch"}
        assert world.roads.syncs() >= 20

    def test_every_reading_reaches_the_record_and_the_results(self, world):
        """The ops did not go away by dropping the work: every print is a run
        row in LEM's record (through the sync) and a filed cell in LabCore."""
        for k in range(10):
            world.poll(k)
        world.poll(10, prints=0)
        rows = _bench_log_rows(world.store)
        assert sorted(r["lab_id"] for r in rows) == sorted(world.labs[:10])
        for i in range(10):
            assert world.lab.cell(world.labs[i], "Density") == "0.%04d" % (8000 + i)

    def test_the_first_poll_costs_labcore_nothing(self, world):
        """E2: the lem_meta read happened at bind (the mixed-fleet allowance,
        §6.2); the first poll enrols, syncs and reads its config from LEM."""
        mark = world.lab.mark()
        world.poll(0, prints=0)
        assert world.lab.since(mark) == []
        assert world.roads.syncs() >= 1

    def test_an_idle_hour_costs_labcore_nothing(self, world):
        """E0: heartbeats ride the sync; config, override and QC library come
        from LEM's config road."""
        world.poll(0, prints=0)
        mark = world.lab.mark()
        for k in range(1, 121):
            world.poll(k, prints=0)
            if k % 10 == 0:
                world.bench._send_pulse(T0 + timedelta(seconds=30 * k))
                world.settle()
        assert world.lab.since(mark) == []


# ── Adoption at the first v4 start, through LEM's digest (§10.2) ─────────────

def _legacy(world, lines, corrections=None):
    """What v3.9 left in LEM's store (imported from LabCore, §10.1) and in
    LabCore's cells: one row per line, written by the module's own
    `run_log_events`, which is the shape v3.9 wrote."""
    m = world.m
    machine = world.bench.machine()
    saved = machine.corrections
    machine.corrections = dict(corrections or {})
    try:
        for k, text in enumerate(lines):
            at = T0 - timedelta(days=1) + timedelta(minutes=k)
            rows = m.apply_row_corrections(
                [m.parse_print(machine, text).to_row(at)], machine.corrections)
            for row, kind, lab, test, value, detail in m.run_log_events(
                    machine, rows, "analyst", None):
                res = world.store.sql(
                    "INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
                    "test_name, value, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [UID, at.strftime("%Y-%m-%d %H:%M:%S"), kind, lab, test,
                     value, json.dumps(detail)])
                assert "error" not in res, res
                world.lab.fake.sql("INSERT OR REPLACE INTO sample_tests "
                                   "(lab_id, test_name, result) VALUES (?, "
                                   "'Density', ?)", [lab, str(row["Density"])])
    finally:
        machine.corrections = saved


def _line(world, i, value=None):
    return "%s,%s" % (world.labs[i], value or "0.%04d" % (8000 + i))


class TestAdoptionThroughLEM:
    def test_U1_U2_one_recovered_row_no_replay_and_no_labcore_read(self, world):
        """30 lines LEM's record already holds (imported from v3.9) and one
        printed while LabStation was down for the upgrade. Through LEM's
        digest: the 30 cost nothing — no new row, no cell — the one is
        recorded once as `recovered` and not filed, and LabCore is never
        asked about the bench's history (0 LabCore reads of lem_machine_log;
        U5's 15-read budget is the legacy road's, not this one's)."""
        lines = [_line(world, i) for i in range(30)]
        _legacy(world, lines)
        with open(world.path, "a") as f:
            f.write("\n".join(lines + [_line(world, 30)]) + "\n")
        mark = world.lab.mark()
        for k in range(4):
            world.poll(k, prints=0)
        rows = world.store.read_sql(
            "SELECT lab_id, origin FROM lem_machine_log WHERE machine_uid = ? "
            "AND kind = 'run' ORDER BY id", [UID])["rows"]
        assert len(rows) == 31
        assert rows[-1] == {"lab_id": world.labs[30], "origin": "recovered"}
        ops = world.lab.since(mark)
        assert not [op for op in ops if "LEM_MACHINE_LOG" in op[1].upper()], ops
        assert not [op for op in ops if op[0] == "write"], ops
        assert world.lab.cell(world.labs[30], "Density") is None
        assert any(p.endswith("/adoption") for _m, p in world.roads.requests)
        # §10.2 step 5 through the digest's `recent`: the ledger knows what
        # v3.9 filed, so a re-run of one of these is LEM's own value to
        # supersede, not a conflict.
        journal = world.m.BenchJournal(world.m.journal_dir(UID), UID)
        assert journal.ledger_value(world.labs[5], "Density") == "0.8005"

    def test_U3_a_factor_changed_since_logging_recovers_nothing(self, world):
        lines = [_line(world, i) for i in range(30)]
        _legacy(world, lines, corrections={"Density": 0.01})
        world.bench.machine().corrections = {"Density": 0.02}
        with open(world.path, "a") as f:
            f.write("\n".join(lines) + "\n")
        for k in range(4):
            world.poll(k, prints=0)
        assert len(_bench_log_rows(world.store)) == 30

    def test_U3_a_qc_print_under_a_changed_machine_factor_recovers_nothing(
            self, world):
        """The same hole on the v2 road: a QC standard logged under a
        machine-level factor kept no raw; the factor has changed. Through the
        digest (no-raw verdicts keyed on standard and test, matched by count)
        no line is recovered — and a row LEM cannot read is named
        (`unreadable_labs`), so its line is not recovered either."""
        m = world.m
        spec = m.TestSpec(name="Density", value_col="Density", expected=0.85,
                          std_dev=0.05, k=2.0, sample_id="QC-D")
        machine = world.bench.machine()
        machine.tests = [spec]
        lines = [_line(world, i) for i in range(30)] + ["QC-D,0.8500"]
        _legacy(world, lines[:7] + lines[8:], corrections={"Density": 0.01})
        res = world.store.sql(
            "INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
            "test_name, value, detail) VALUES (?, '2026-09-30 09:00:00', 'run', "
            "?, '', '', '{not json')", [UID, world.labs[7]])
        assert "error" not in res, res
        machine.corrections = {"Density": 0.02}
        with open(world.path, "a") as f:
            f.write("\n".join(lines) + "\n")
        for k in range(4):
            world.poll(k, prints=0)
        journal = m.BenchJournal(m.journal_dir(UID), UID)
        (rec,) = [r for r in journal._scan() if r["kind"] == "adoption"]
        assert (rec["road"], rec["recovered"], rec["unreadable"]) == ("lem", 0, 1)
        assert len(_bench_log_rows(world.store)) == 30     # 29 runs + the unreadable


def test_the_module_and_the_server_hash_every_row_alike():
    """The bench hashes its lines and LEM hashes its rows; one function on
    two sides of a wire. Every shape of recorded row, through both."""
    import bench_api
    m, _qt = _module()
    rows = [
        {"kind": "run", "lab_id": "L-1", "test_name": "", "value": "",
         "detail": {"values": {"Density": "0.8000"}}},
        {"kind": "run", "lab_id": " L-2 ", "test_name": "", "value": "",
         "detail": {"values": {"Density": "0.8100", "Sulfur": "12"},
                    "raw": {"Density": 0.8}, "corrections": {"Density": 0.01}}},
        {"kind": "qc", "lab_id": "QC-1", "test_name": "Density", "value": "0.81",
         "detail": {"raw_value": 0.8, "correction": 0.01}},
        {"kind": "qc", "lab_id": "QC-1", "test_name": "Flash", "value": "41",
         "detail": {"in_spec": True}},
        {"kind": "run", "lab_id": "L-3", "test_name": "", "value": "",
         "detail": {"values": {"Note": " ok ", "Flash": "1e2"}}},
        # v3.9 under a machine-level factor: a verdict with no raw, as text.
        {"kind": "qc", "lab_id": "QC-D", "test_name": "Density", "value": "0.86",
         "detail": json.dumps({"in_spec": True, "operator": None})},
    ]
    for r in rows:
        server = bench_api.adoption_hash(r["lab_id"], bench_api.adoption_raw_values(
            r["kind"], r["test_name"], r["value"],
            bench_api._adoption_detail(r["detail"])))
        assert server == m.legacy_row_adoption_key(r), r
    # And both sides call the same rows unreadable (no key at all).
    for detail in ("{not json", "[1, 2]", json.dumps({"no_values": 1}), None):
        r = {"kind": "run", "lab_id": "L-9", "test_name": "", "value": "",
             "detail": detail}
        d = bench_api._adoption_detail(detail)
        server = None if d is None else bench_api.adoption_raw_values(
            "run", "", "", d)
        assert server is None and m.legacy_row_adoption_key(r) is None, detail
