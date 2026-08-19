"""The live road's memory: what a bench said about itself, and for how long.

Deliberately not a database of anything. It holds what a module pushed, hands it
back until it ages out, and dies with the process — LabCore keeps the record.

The TTL is per machine because the module offers a 5-minute poll interval. A
fixed 90s window would make such a bench read live for 90s and from-record for
the remaining 3½ minutes, every cycle, and the floor would visibly flap.
"""
import pytest

from live_presence import (
    LIVE_TOKEN_KEY,
    LIVE_URL_KEY,
    LivePresence,
    publish_live_config,
    resolve_token,
    ttl_for,
)


class Clock:
    """A hand-wound monotonic clock, so ageing is tested without sleeping."""

    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def push(status="GREEN", at="2026-08-05T14:00:00", **extra):
    payload = {"status": status, "reason": "", "at": at}
    payload.update(extra)
    return payload


class TestWhatABenchSaid:
    def test_a_pushed_status_is_readable(self):
        p = LivePresence()
        p.record("m1", push())
        assert p.get("m1")["status"] == "GREEN"

    def test_a_machine_that_never_pushed_is_absent(self):
        assert LivePresence().get("m1") is None

    def test_the_latest_push_wins(self):
        p = LivePresence()
        p.record("m1", push("GREEN", at="2026-08-05T14:00:00"))
        p.record("m1", push("RED", at="2026-08-05T14:01:00"))
        assert p.get("m1")["status"] == "RED"

    def test_the_parse_details_are_kept(self):
        p = LivePresence()
        p.record("m1", push(last_parse_at="2026-08-05T14:00:02",
                            lab_id="L-1234"))
        entry = p.get("m1")
        assert entry["last_parse_at"] == "2026-08-05T14:00:02"
        assert entry["lab_id"] == "L-1234"


class TestAgeingOut:
    def test_an_entry_expires_at_its_ttl(self):
        clock = Clock()
        p = LivePresence(clock=clock)
        p.record("m1", push())
        clock.advance(89)
        assert p.get("m1") is not None
        clock.advance(2)
        assert p.get("m1") is None

    def test_a_slow_bench_stays_live_between_its_own_polls(self):
        """The module's intervals are 15s, 30s, 60s and 5 min."""
        assert ttl_for(300) == 750.0
        assert ttl_for(60) == 150.0
        assert ttl_for(15) == 90.0

    def test_an_unstated_interval_gets_the_floor(self):
        assert ttl_for(None) == 90.0
        assert ttl_for("") == 90.0
        assert ttl_for("nonsense") == 90.0

    def test_a_bogus_interval_cannot_pin_a_dead_bench_as_live(self):
        assert ttl_for(100000) == 1200.0

    def test_the_ttl_follows_the_interval_the_bench_reported(self):
        clock = Clock()
        p = LivePresence(clock=clock)
        p.record("m1", push(interval_seconds=300))
        clock.advance(700)
        assert p.get("m1") is not None, "a 5-minute bench went stale too soon"
        clock.advance(100)
        assert p.get("m1") is None

    def test_all_omits_what_has_expired(self):
        clock = Clock()
        p = LivePresence(clock=clock)
        p.record("m1", push())
        p.record("m2", push(interval_seconds=300))
        clock.advance(120)
        assert list(p.all()) == ["m2"]


class TestOrderAndLimits:
    def test_an_out_of_order_push_is_discarded(self):
        """A delayed POST must not overwrite a newer state."""
        p = LivePresence()
        p.record("m1", push("RED", at="2026-08-05T14:05:00"))
        p.record("m1", push("GREEN", at="2026-08-05T14:00:00"))
        assert p.get("m1")["status"] == "RED"

    def test_an_equal_timestamp_is_accepted(self):
        """A bench that repeats itself is not out of order."""
        p = LivePresence()
        p.record("m1", push("RED", at="2026-08-05T14:05:00"))
        p.record("m1", push("GREEN", at="2026-08-05T14:05:00"))
        assert p.get("m1")["status"] == "GREEN"

    def test_a_missing_timestamp_does_not_lose_the_push(self):
        p = LivePresence()
        p.record("m1", {"status": "GREEN"})
        assert p.get("m1")["status"] == "GREEN"

    def test_the_store_is_capped(self):
        p = LivePresence()
        for i in range(300):
            p.record(f"m{i}", push())
        assert len(p.all()) <= 256

    def test_the_cap_drops_the_oldest_not_the_newest(self):
        p = LivePresence()
        for i in range(300):
            p.record(f"m{i}", push())
        assert p.get("m299") is not None
        assert p.get("m0") is None

    def test_a_machine_uid_is_required(self):
        p = LivePresence()
        assert p.record("", push()) is False
        assert p.all() == {}


class TestThreadSafety:
    def test_concurrent_pushes_do_not_corrupt_it(self):
        """The Flask dev server is threaded — same requirement as the fake
        gateway."""
        import threading
        p = LivePresence()

        def worker(n):
            for i in range(50):
                p.record(f"m{n}", push(at=f"2026-08-05T14:{i:02d}:00"))

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(p.all()) == 8


class TestTheTokenNobodyHasToType:
    """Zero operator input: the server settles the token and publishes it with
    its own address into lem_meta; modules read it from LabCore, which they are
    already talking to. A bench that moves to another PC needs no setup."""

    def test_an_explicit_token_is_used_as_given(self):
        assert resolve_token("from-env") == "from-env"

    def test_a_missing_token_is_generated_not_left_blank(self):
        first, second = resolve_token(None), resolve_token("")
        assert len(first) >= 32
        assert first != second, "each call must not reuse one guessable value"

    def test_publishing_writes_the_address_and_the_token(self):
        from labcore_gateway import FakeLabCoreGateway
        gw = FakeLabCoreGateway()

        publish_live_config(gw, "http://10.0.0.5:5557", "tok")

        rows = gw.read_sql("SELECT key, value FROM lem_meta").get("rows") or []
        published = {r["key"]: r["value"] for r in rows}
        assert published[LIVE_URL_KEY] == "http://10.0.0.5:5557"
        assert published[LIVE_TOKEN_KEY] == "tok"

    def test_publishing_again_replaces_rather_than_duplicates(self):
        from labcore_gateway import FakeLabCoreGateway
        gw = FakeLabCoreGateway()

        publish_live_config(gw, "http://10.0.0.5:5557", "tok")
        publish_live_config(gw, "http://10.0.0.9:5557", "tok2")

        rows = gw.read_sql("SELECT key, value FROM lem_meta").get("rows") or []
        published = {r["key"]: r["value"] for r in rows}
        assert len(rows) == 2
        assert published[LIVE_URL_KEY] == "http://10.0.0.9:5557"

    def test_a_refusing_labcore_does_not_stop_the_server_booting(self):
        """Publishing is a convenience. A queue that is full at boot must not
        take the floor down with it."""
        class Dead:
            def sql(self, *a, **k):
                raise RuntimeError("queue full")

        publish_live_config(Dead(), "http://x", "t")   # must not raise

    def test_nothing_is_published_without_an_address(self):
        from labcore_gateway import FakeLabCoreGateway
        gw = FakeLabCoreGateway()

        publish_live_config(gw, "", "tok")

        rows = gw.read_sql("SELECT key, value FROM lem_meta").get("rows") or []
        assert rows == []
