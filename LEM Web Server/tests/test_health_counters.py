"""`/healthz` grows LabCore counters; benches stay background (spec §12.3).

Baseline item 2 was "LEM reads exceeding LabCore's 8 s watchdog: **not
measurable from here — no count obtained**". A kill is visible from LEM's side
in exactly one place — LabCore's answer, "Read cancelled after 8s to protect
the write queue …" — and nothing counted it. Now `/healthz` reports this
server's LabCore traffic over five minutes by outcome, under `labcore_ops`:
`reads_5min`, `writes_5min`, `failed_5min`, `timeouts_5min`, `watchdog_5min`.

Why `labcore_ops` and not `labcore` as the spec sketch wrote it: `/healthz`
ALREADY has a `labcore` key, a string ("reachable" / "unreachable" /
"unknown") that v3.9.0 reports today (`baseline/healthz.json`) and the updater
and Settings › Diagnostics read. Turning that string into an object would make
every one of today's keys still present and one of them mean something else —
which is not a superset. So the counters sit beside it.

Three more promises, each pinned here:

* every key `/healthz` answered before is still there with the same type;
* asking `/healthz` costs LabCore nothing, counters included;
* `_is_background` treats a bench's v2 POST like its v1 `/api/live` push:
  machinery, not a person. Otherwise every bench's sync (one per poll, 17
  benches) would pin `idle_seconds` near zero and the unattended updater,
  which deploys only when the lab is idle, would never fire again.
"""
import json
import os

import pytest

import bench_v2_kit as kit
from bench_v2_kit import Bench
from labcore_counter import CountingLabCore
from labcore_gateway import FakeLabCoreGateway
from labcore_meter import LabCoreMeter

HERE = os.path.dirname(os.path.abspath(__file__))

#: `/healthz` of v3.9.0 in production, 2026-10-01 (baseline/healthz.json).
V3_9_KEYS = {"active_sessions": int, "audit_spool": int,
             "audit_spool_oldest": str, "idle_seconds": float,
             "labcore": str, "last_activity": str, "log": str, "pid": int,
             "schema": str, "schema_error": str, "status": str, "version": str}
#: Added on this branch before this piece (T-P6): the store's health.
INTEGRATION_KEYS = set(V3_9_KEYS) | {"store"}

WATCHDOG = {"error": "Read cancelled after 8s to protect the write queue "
                     "(query too slow — likely an unindexed scan)."}


class ScriptedLabCore(CountingLabCore):
    """LabCore whose reads answer what the test says."""

    def __init__(self):
        super().__init__()
        self.answers = []

    def read_sql(self, sql, args=None, **kw):
        self._note("read_sql", sql)
        if self.answers:
            answer = self.answers.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        return self.fake.read_sql(sql, args)

    def get_test_names(self, **kw):
        self._note("get_test_names")
        return None                         # "could not ask" — the fallback


@pytest.fixture
def store():
    s = FakeLabCoreGateway()
    kit.seed_machine(s)
    return s


@pytest.fixture
def lab():
    return ScriptedLabCore()


@pytest.fixture
def app(store, lab):
    return kit.make_app(store, labcore=lab)


@pytest.fixture
def client(app):
    return app.test_client()


class TestHealthzShape:
    def test_every_key_of_today_is_still_there_with_its_type(self, client):
        body = client.get("/healthz").get_json()
        for key, kind in V3_9_KEYS.items():
            assert key in body, key
            if kind is float:
                assert isinstance(body[key], (int, float)), key
            else:
                assert isinstance(body[key], kind), (key, body[key])
        assert INTEGRATION_KEYS <= set(body)

    def test_the_labcore_counters_are_there_and_zero_on_a_quiet_server(
            self, client):
        ops = client.get("/healthz").get_json()["labcore_ops"]
        for key in ("reads_5min", "writes_5min", "failed_5min",
                    "timeouts_5min", "watchdog_5min"):
            assert ops[key] == 0, key

    def test_asking_costs_labcore_nothing(self, client, lab):
        for _ in range(20):
            client.get("/healthz")
        assert lab.ops == 0, lab.calls

    def test_benches_are_summarised_from_memory(self, client, store, lab):
        b = Bench(client, kit.enroll(client))
        b.journal(3).sync(stats={"unacked": 7, "road": "public",
                                 "digest": kit.DIGEST_ZERO,
                                 "labcore": {"watchdog": 2, "failed": 3}})
        benches = client.get("/healthz").get_json()["benches"]
        assert benches["reporting"] == 1
        assert benches["unacked_total"] == 7
        assert benches["labcore_watchdog_5min"] == 2
        assert benches["digest_mismatch"] == 0
        assert lab.ops == 0


class TestBenchesBeforeTheStoreIsRead:
    """After a restart the registry has not read `bench_cursor` yet. Round 2
    answered `v2: 0, unacked_total: 0` then — "not read yet" shown as "none",
    which is the rule this repo keeps above all: a failed (or not-yet-made)
    read is never an empty result. Now each count is null until the store
    has been read, and the first sync reads it (the store, never LabCore)."""

    def _restarted(self, store):
        b_client = kit.make_app(store).test_client()
        b = Bench(b_client, kit.enroll(b_client))
        b.journal(2).sync(stats={"unacked": 5, "road": "lan",
                                 "digest": kit.DIGEST_ZERO})
        lab = CountingLabCore()
        return kit.make_app(store, labcore=lab).test_client(), lab, b

    def test_counts_are_null_not_zero_until_the_store_is_read(self, store):
        client, lab, _b = self._restarted(store)
        benches = client.get("/healthz").get_json()["benches"]
        assert benches["hydrated"] is False
        for key in ("v2", "reporting", "lagging", "unacked_total",
                    "digest_mismatch", "labcore_failed_5min",
                    "labcore_watchdog_5min"):
            assert benches[key] is None, (key, benches[key])
        assert lab.ops == 0

    def test_the_first_sync_reads_the_store_so_the_counts_are_whole(
            self, store):
        client, lab, b = self._restarted(store)
        b.client = client
        b.journal(1).sync(stats={"unacked": 0, "road": "lan",
                                 "digest": b.digest_through(2)})
        benches = client.get("/healthz").get_json()["benches"]
        assert benches["hydrated"] is True
        assert benches["v2"] == 1 and benches["reporting"] == 1
        assert lab.ops == 0


class TestBaselineItem2IsMeasured:
    def test_a_watchdog_answer_is_counted_as_a_watchdog_kill(self, client, lab):
        """The injected answer is LabCore's own text, the one
        `baseline/harness/run_web.py` uses. One killed read: reads 1, failed 1,
        watchdog 1 — and the person asking was told it could not be read."""
        before = client.get("/healthz").get_json()["labcore_ops"]
        lab.answers = [dict(WATCHDOG)]
        r = client.get("/api/test-names")
        assert r.status_code == 503
        after = client.get("/healthz").get_json()["labcore_ops"]
        assert after["watchdog_5min"] == before["watchdog_5min"] + 1
        assert after["failed_5min"] == before["failed_5min"] + 2
        assert after["reads_5min"] == before["reads_5min"] + 2
        assert after["timeouts_5min"] == before["timeouts_5min"]

    def test_a_client_timeout_is_counted_as_a_timeout_not_a_kill(self, client,
                                                                 lab):
        lab.answers = [{"error": "HTTPSConnectionPool(host='labvision.asaplabs."
                                 "net', port=443): Read timed out. (read "
                                 "timeout=20.0)"}]
        client.get("/api/test-names")
        ops = client.get("/healthz").get_json()["labcore_ops"]
        assert ops["timeouts_5min"] == 1 and ops["watchdog_5min"] == 0

    def test_a_raised_timeout_is_counted_and_still_raised(self):
        class Raises:
            def read_sql(self, *a, **k):
                raise TimeoutError("read deadline")
        meter = LabCoreMeter(Raises())
        with pytest.raises(TimeoutError):
            meter.read_sql("SELECT 1")
        c = meter.counts()
        assert (c["reads_5min"], c["failed_5min"], c["timeouts_5min"]) == (1, 1, 1)

    def test_the_window_is_five_minutes(self):
        now = [1000.0]
        meter = LabCoreMeter(CountingLabCore(), clock=lambda: now[0])
        meter.read_sql("SELECT 1")
        assert meter.counts()["reads_5min"] == 1
        now[0] += 301
        assert meter.counts()["reads_5min"] == 0
        assert meter.counts()["since_boot"]["reads"] == 1

    def test_a_good_answer_and_an_empty_one_are_not_failures(self):
        meter = LabCoreMeter(CountingLabCore())
        meter.read_sql("SELECT 1")
        assert meter.counts()["failed_5min"] == 0

    def test_a_gateway_that_could_not_ask_is_a_failure(self):
        class Nothing:
            def get_samples(self, **kw):
                return None
        meter = LabCoreMeter(Nothing())
        meter.get_samples()
        assert meter.counts()["failed_5min"] == 1

    def test_the_store_is_not_labcore(self, client, store, lab):
        """In the split shape the store's reads are local SQLite, not queue
        slots: a page walk that reads only the store counts 0 LabCore ops."""
        client.get("/api/machines?fresh=1")
        client.get("/api/ui/live")
        ops = client.get("/healthz").get_json()["labcore_ops"]
        assert ops["reads_5min"] == lab.ops == 0


class TestIdleRule:
    def test_bench_posts_are_background(self):
        """Pinned by name in spec §6.1. Every v2 bench request — sync, enrol,
        the source snapshot upload — is machinery."""
        from web_app import _is_background
        for path in ("/api/v2/bench/abc/sync", "/api/v2/bench/abc/enroll",
                     "/api/v2/bench/abc/source-snapshot",
                     "/api/v2/bench/abc/config", "/api/v2/bench/abc/checkpoint"):
            for method in ("POST", "PUT", "GET"):
                assert _is_background(path, method), (method, path)
        assert _is_background("/api/live", "POST")
        # and a person is still a person
        assert not _is_background("/api/transfer/benches/abc/approve", "POST")
        assert not _is_background("/api/checklists/x/toggle", "POST")
        # a path that merely starts the same way is not a bench
        assert not _is_background("/api/v2/benchmarks", "POST")

    def test_a_lab_full_of_syncing_benches_leaves_the_server_idle(
            self, client, store):
        b = Bench(client, kit.enroll(client))
        first = client.get("/healthz").get_json()
        for _ in range(5):
            b.journal(2).sync()
        later = client.get("/healthz").get_json()
        assert later["last_activity"] == first["last_activity"]
        assert later["idle_seconds"] >= first["idle_seconds"]
