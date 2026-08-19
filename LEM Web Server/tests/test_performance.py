"""Pages were taking many seconds to load, and the server was a bad neighbour.

Measured against the live LabCore: one `read_sql` round-trip is **194ms at best,
1.35s on average, 3.5s at worst**, and one refresh of the pages a lab leaves open
cost **17 ops** — so a single wall display polling every 30s meant ~34 ops/min
into a queue shared with LabStation and LabEntry. Three screens was enough to
make that queue start rejecting work.

Two earlier attempts are gone, and this file no longer tests them:
per-request parallel reads, and a 4s response cache. Both still scaled with the
number of viewers. What replaced them is `SnapshotService` — one background
refresher, all requests served from memory — so the tests here are about the
property that actually matters: **LabCore load must not depend on how many people
are looking, and a request must never wait on a round-trip.**
"""
import threading
import time

import pytest

from labcore_gateway import FakeLabCoreGateway


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


class Counting(FakeLabCoreGateway):
    def __init__(self, latency: float = 0.0):
        super().__init__()
        self.reads = []
        self.writes = []
        self.latency = latency
        self.lock = threading.Lock()

    def read_sql(self, sql, args=None, **kw):
        with self.lock:
            self.reads.append(sql)
        if self.latency:
            time.sleep(self.latency)
        return super().read_sql(sql, args, **kw)

    def sql(self, sql, args=None, **kw):
        self.writes.append(sql)
        return super().sql(sql, args, **kw)

    def is_running(self):
        return True


@pytest.fixture
def gw():
    g = Counting()
    g.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
          "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
          "reason TEXT, updated_at TEXT)")
    for i in range(3):
        g.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
              [f"m{i}", f"Machine {i}", "GREEN", "ok", "2026-08-03T09:00:00"])
    return g


def make(gateway):
    from web_app import create_app
    app = create_app(gateway, authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client(), app


# ── load must not scale with viewers ────────────────────────────────────────

class TestLoadIsIndependentOfViewers:
    def test_the_floor_is_served_without_touching_labcore(self, gw):
        client, _ = make(gw)
        client.get("/api/machines")          # first call builds the snapshot
        gw.reads.clear()
        for _ in range(25):
            client.get("/api/machines")
        assert gw.reads == [], f"{len(gw.reads)} ops for 25 viewers"

    def test_twenty_concurrent_viewers_cost_nothing_extra(self, gw):
        client, _ = make(gw)
        client.get("/api/machines")
        gw.reads.clear()
        threads = [threading.Thread(target=lambda: client.get("/api/machines"))
                   for _ in range(20)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert gw.reads == []

    def test_the_whole_floor_needs_one_op_not_ten(self, gw):
        """Ten tables in a single UNION ALL — verified against live LabCore at
        0.27s for the whole floor.

        Measured after start-up: the first refresh also asks which tables exist,
        once per process, so that the ten `CREATE TABLE IF NOT EXISTS` writes are
        skipped on every restart thereafter.
        """
        client, _ = make(gw)
        client.get("/api/machines")
        gw.reads.clear()
        client.get("/api/machines?fresh=1")
        assert len(gw.reads) == 1, [r[:40] for r in gw.reads]
        assert "UNION ALL" in gw.reads[0]

    def test_schema_creation_does_not_repeat(self, gw):
        client, _ = make(gw)
        client.get("/api/machines")
        gw.writes.clear()
        client.get("/api/machines?fresh=1")
        assert [w for w in gw.writes if "CREATE TABLE" in w.upper()] == []


# ── a request must never wait on LabCore ────────────────────────────────────

class TestRequestsAreFast:
    def test_a_served_request_does_no_io_however_slow_labcore_is(self, gw):
        gw.latency = 0.25
        client, _ = make(gw)
        client.get("/api/machines")           # pays for the build, once
        start = time.time()
        for _ in range(10):
            client.get("/api/machines")
        elapsed = time.time() - start
        assert elapsed < 0.3, f"10 requests took {elapsed*1000:.0f}ms"

    def test_the_answer_is_complete(self, gw):
        """Speed must not cost correctness."""
        client, _ = make(gw)
        body = client.get("/api/machines").get_json()
        assert len(body["machines"]) == 3
        for m in body["machines"]:
            for key in ("qc_specs", "sub_statuses", "module_state", "pos",
                        "maintenance", "qc_targets", "last_activity"):
                assert key in m, key

    def test_it_says_how_old_the_answer_is(self, gw):
        """Serving from memory without saying when it was read is how a stopped
        module passes for a live one."""
        client, _ = make(gw)
        body = client.get("/api/machines").get_json()
        assert "age_seconds" in body

    def test_a_failing_read_does_not_take_the_page_down(self, gw):
        real = gw.read_sql

        def flaky(sql, args=None, **kw):
            if "UNION ALL" in sql:
                raise RuntimeError("batched read unhappy")
            return real(sql, args, **kw)

        gw.read_sql = flaky
        client, _ = make(gw)
        r = client.get("/api/machines")
        assert r.status_code == 200
        assert r.get_json()["machines"], "fell back to nothing"


# ── one machine-list read per request, where a request still reads ──────────

class TestNoDuplicateWork:
    def machine_list_reads(self, gw):
        return [s for s in gw.reads
                if "lem_machine_status" in s and "UNION ALL" not in s]

    def test_the_logs_page_reads_the_machine_list_once(self, gw):
        client, _ = make(gw)
        client.get("/api/logs")
        gw.reads.clear()
        client.get("/api/logs")
        assert len(self.machine_list_reads(gw)) <= 1

    def test_the_fleet_maintenance_reads_it_once(self, gw):
        client, _ = make(gw)
        client.get("/api/maintenance")
        gw.reads.clear()
        client.get("/api/maintenance")
        assert len(self.machine_list_reads(gw)) <= 1


# ── a change made through the UI shows up ───────────────────────────────────

class TestWritesBecomeVisible:
    def test_assigning_qc_appears_without_waiting_for_the_interval(self, gw):
        client, _ = make(gw)
        client.post("/api/login", json={"username": "k", "password": "good"})
        client.get("/api/machines")
        client.post("/api/machines/m0/qc-targets",
                    json={"targets": [{"sample": "Cloud CRM", "test": "CP"}]})
        body = client.get("/api/machines").get_json()
        m0 = [m for m in body["machines"] if m["machine_uid"] == "m0"][0]
        assert m0["qc_targets"], "the operator could not see their own change"

    def test_moving_an_instrument_appears(self, gw):
        client, _ = make(gw)
        client.post("/api/login", json={"username": "k", "password": "good"})
        client.get("/api/machines")
        client.post("/api/machines/m0/position", json={"x": 4.1, "y": 2.05})
        body = client.get("/api/machines").get_json()
        m0 = [m for m in body["machines"] if m["machine_uid"] == "m0"][0]
        assert m0["pos"] == [4.1, 2.05]

    def test_a_module_writing_straight_to_labcore_needs_a_poll_or_fresh(self, gw):
        """The honest limit of this design: the modules write to LabCore
        directly, so the server cannot know until it next looks. Within the
        refresh interval, or with ?fresh=1, it does."""
        client, _ = make(gw)
        client.get("/api/machines")
        gw.sql("INSERT INTO lem_machine_status VALUES "
               "('m9','Newcomer','GREEN','ok','2026-08-03T10:00:00')")
        cached = client.get("/api/machines").get_json()["machines"]
        assert "Newcomer" not in [m["title"] for m in cached]
        fresh = client.get("/api/machines?fresh=1").get_json()["machines"]
        assert "Newcomer" in [m["title"] for m in fresh]
