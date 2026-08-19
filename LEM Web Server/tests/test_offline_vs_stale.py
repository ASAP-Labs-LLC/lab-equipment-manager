""""LabCore offline" must mean LabCore is unreachable — nothing else.

Ryan, 2026-08-03: "it keeps saying labcore offline ... I thought the one server
thing was supposed to hold the information until it itself refreshes".

Measured against production, the cause was not what it looked like. The reachability
ping is fine — 0.12s, up every time. The **batched read was timing out at exactly
8.00s, four times in six**. And the read itself is not slow: run in isolation it
takes 0.12s and returns 103 rows, every arm under half a second.

It was **queue congestion**. `read_sql` POSTs to `/api/queue/write`, so a read waits
its turn behind every write in the lab — `pending: 28` while this was happening, 0
once it drained. Six modules publishing status, sub-status, heartbeats and specs
each poll is enough to bury an 8s client timeout.

Two things follow:

* The background refresher gets a **generous timeout**. It is a thread; waiting is
  free, and waiting turns "banner plus stale data" into "fresh data, a moment
  later".
* A failed read is **stale, not offline**. The snapshot already keeps the last good
  answer and reports its age — which is exactly the behaviour Ryan expected. Only
  an unreachable LabCore is offline.
"""
import pytest

from labcore_gateway import FakeLabCoreGateway


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


@pytest.fixture
def gw():
    g = FakeLabCoreGateway()
    g.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
          "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
          "reason TEXT, updated_at TEXT)")
    g.sql("INSERT INTO lem_machine_status VALUES "
          "('m1','OptiMPP 1','GREEN','ok','2026-08-03T21:00:00')")
    return g


@pytest.fixture
def client(gw):
    from web_app import create_app
    app = create_app(gw, authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client()


def body(client):
    return client.get("/api/machines").get_json()


# ── the read gets room to breathe ───────────────────────────────────────────

class TestTheBackgroundReadWaits:
    def test_it_asks_for_a_generous_timeout(self, gw):
        """8s is the client default and it is not enough behind a busy queue."""
        from snapshot_service import READ_TIMEOUT, SnapshotService
        seen = {}
        real = gw.read_sql

        def spy(sql, args=None, **kw):
            seen.update(kw)
            return real(sql, args)

        gw.read_sql = spy
        SnapshotService(gw).read_tables()
        assert seen.get("timeout") == READ_TIMEOUT
        assert READ_TIMEOUT >= 30, "still tight enough to trip on a busy queue"

    def test_the_fallback_reads_wait_too(self, gw):
        from snapshot_service import READ_TIMEOUT, SnapshotService
        timeouts = []
        real = gw.read_sql

        def spy(sql, args=None, **kw):
            timeouts.append(kw.get("timeout"))
            if "UNION ALL" in sql:
                return {"error": "busy", "busy": True}
            return real(sql, args)

        gw.read_sql = spy
        svc = SnapshotService(gw)
        svc.ensure_schema()          # boot reads happen first; not what is measured
        timeouts.clear()
        svc.read_tables()
        assert timeouts and all(t == READ_TIMEOUT for t in timeouts), timeouts


# ── a slow read is stale, not offline ───────────────────────────────────────

class TestAFailedReadIsNotOffline:
    def test_a_timed_out_read_does_not_report_offline(self, gw, client):
        client.get("/api/machines")            # one good snapshot first
        snaps = None
        from flask import current_app
        real = gw.read_sql
        gw.read_sql = lambda *a, **k: {"error": "HTTPSConnectionPool: read timed out"}
        client.get("/api/machines?fresh=1")
        assert body(client)["labcore_online"] is True, \
            "a slow queue is not an unreachable server"

    def test_the_last_good_answer_is_still_served(self, gw, client):
        client.get("/api/machines")
        gw.read_sql = lambda *a, **k: {"error": "read timed out"}
        client.get("/api/machines?fresh=1")
        got = body(client)
        assert [m["title"] for m in got["machines"]] == ["OptiMPP 1"]

    def test_it_says_how_stale_it_is(self, gw, client):
        client.get("/api/machines")
        gw.read_sql = lambda *a, **k: {"error": "read timed out"}
        client.get("/api/machines?fresh=1")
        got = body(client)
        assert got.get("age_seconds") is not None
        assert got.get("stale") is not None

    def test_an_unreachable_labcore_IS_offline(self, gw, client):
        """The banner has to still work for the case it exists for. A real outage
        fails the read AND the ping — that pair is what distinguishes it from a
        congested queue, where the ping still answers."""
        client.get("/api/machines")
        gw.read_sql = lambda *a, **k: {"error": "connection refused"}
        gw.is_running = lambda: False
        client.get("/api/machines?fresh=1")
        assert body(client)["labcore_online"] is False

    def test_a_congested_queue_is_told_apart_from_an_outage(self, gw, client):
        """Same failed read, but the ping answers — so it is stale, not offline."""
        client.get("/api/machines")
        gw.read_sql = lambda *a, **k: {"error": "read timed out"}
        gw.is_running = lambda: True
        client.get("/api/machines?fresh=1")
        assert body(client)["labcore_online"] is True

    def test_reachability_is_not_re_probed_on_every_request(self, gw, client):
        """The ping is cheap but it is not free, and requests must not do I/O."""
        client.get("/api/machines")
        pings = []
        gw.is_running = lambda: (pings.append(1), True)[1]
        for _ in range(10):
            client.get("/api/machines")
        assert pings == []


# ── the floor shows the difference ──────────────────────────────────────────

class TestTheFloorDistinguishesThem:
    def src(self):
        import pathlib
        return (pathlib.Path(__file__).resolve().parent.parent
                / "templates" / "floor.html").read_text(encoding="utf-8")

    def test_the_offline_banner_keys_off_reachability(self):
        s = self.src()
        assert "labcore_online" in s

    def test_staleness_is_shown_separately_from_offline(self):
        """A lab reading "OFFLINE" while the floor is plainly still updating
        teaches everyone to ignore the banner."""
        s = self.src()
        assert "age_seconds" in s or "stale" in s


class TestThePollerKeepsItsCadence:
    """A refresh that waits on a busy queue must not also cost the next interval.

    The loop was `refresh(); wait(interval)`, so a 45s refresh made the real cycle
    57s and the snapshot's age compounded — soak-measured at 92s. Waiting
    `interval - elapsed` keeps the cadence at the interval regardless.
    """

    def test_a_slow_refresh_does_not_add_to_the_wait(self, gw):
        from snapshot_service import SnapshotService
        svc = SnapshotService(gw, interval=10.0)
        assert svc.next_wait(elapsed=7.0) == pytest.approx(3.0)

    def test_a_fast_refresh_waits_the_remainder(self, gw):
        from snapshot_service import SnapshotService
        svc = SnapshotService(gw, interval=10.0)
        assert svc.next_wait(elapsed=0.2) == pytest.approx(9.8)

    def test_a_refresh_longer_than_the_interval_goes_again_promptly(self, gw):
        """Not zero: a tight loop against a congested queue would make it worse."""
        from snapshot_service import SnapshotService
        svc = SnapshotService(gw, interval=10.0)
        wait = svc.next_wait(elapsed=45.0)
        assert 0 < wait <= 2.0, wait

    def test_the_loop_uses_it(self):
        import inspect

        from snapshot_service import SnapshotService
        src = inspect.getsource(SnapshotService._loop)
        assert "next_wait" in src
        assert "self.interval)" not in src.replace("next_wait", ""), \
            "still waiting a flat interval"
