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
    w.poll = poll
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
        assert world.lab.since(mark) == []
