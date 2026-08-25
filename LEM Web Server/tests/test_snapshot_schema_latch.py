#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One transient at boot must not maim the floor for the life of the process.

`SnapshotService.ensure_schema()` set `_schema_ready = True` BEFORE issuing a
single CREATE, and wrapped each one in `except Exception: pass`. `_migrate` did
the same to its ALTERs. Read against what LabCore actually does, both of those
are decoration: the write queue refuses past ~100 pending by ANSWERING, never
by raising, so the `except` catches nothing that happens in practice and the
flag latches on work that was refused.

What that costs is not a stack trace. It is silence:

  * the batched one-op read (`batched_machine_sql`) unions every lem_* table.
    One table missing makes the whole statement fail, `batched_ok` flips off,
    and the floor falls back to FIFTEEN reads per refresh — every 12 seconds,
    against the same queue that was too busy to accept a CREATE. The service
    made the congestion worse and then stayed that way;
  * `_migrate` recording a refused ALTER as applied means `correction` is
    missing from `lem_machine_specs`, which is the column the corrections
    feature reads. The floor shows uncorrected numbers with nothing to say so;
  * and nothing anywhere reports it. `/healthz` answers `status: ok` and the
    release goes out. RELEASING.md §5 is explicit that nothing in the deploy
    pipeline catches a release that starts perfectly and shows the wrong thing.

The rule these tests pin: confirm each statement, do not mark ready until it
landed, retry what did not, and say so on /healthz.

Driven with the EVIDENCED refusal — an error dict carrying `busy`, returned
normally. That is the shape the old `except Exception` could never have seen.
"""
import pytest

from labcore_gateway import FakeLabCoreGateway
from snapshot_service import SnapshotService

BUSY = {"error": "LabCore is busy, try again later", "busy": True,
        "retry_after": 4}


class QueueFullThenClears:
    """Refuses every write until `clear()`, then behaves.

    Reads pass straight through, because a busy write queue does not stop
    LabCore answering a SELECT — that asymmetry is the whole reason a refused
    CREATE is survivable in the first place.
    """

    def __init__(self, real):
        self.real = real
        self.blocked = True
        self.writes = []

    def clear(self):
        self.blocked = False

    def sql(self, sql, args=None, **kw):
        self.writes.append(sql)
        if self.blocked:
            return dict(BUSY)
        return self.real.sql(sql, args, **kw)

    def read_sql(self, sql, args=None, **kw):
        return self.real.read_sql(sql, args, **kw)

    def is_running(self):
        return True


class FakeClock:
    """Monotonic seconds under the test's control.

    Real sleeps would make this suite slow and flaky; the throttle is a
    decision about elapsed time and can be tested as one.
    """

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def _throttled(gw):
    clock = FakeClock()
    return SnapshotService(gw, clock=clock), clock


def _tables(gw):
    """The lem_* tables only — the fake gateway ships LabCore's own."""
    res = gw.read_sql("SELECT name FROM sqlite_master WHERE type='table'")
    return {r["name"] for r in res.get("rows") or []
            if str(r["name"]).startswith("lem_")}


class TestARefusedCreateIsNotForgotten:
    def test_the_service_does_not_call_itself_ready(self):
        """`_schema_ready` was set on line one, before anything was attempted.

        Anything downstream that asks "is the schema there?" was being told yes
        about ten tables that had just been refused.
        """
        gw = QueueFullThenClears(FakeLabCoreGateway())
        svc = SnapshotService(gw)
        svc.ensure_schema()
        assert svc.schema_ready is False

    def test_it_retries_the_tables_that_did_not_land(self):
        """The latch. One busy minute at boot used to mean the CREATEs were
        never attempted again for the life of the process.

        The clock is advanced because a refused round now buys a cooldown
        (`TestARefusedDeclarationIsNotRetriedOnEveryCall`): retrying is not the
        same as retrying on every call, and this test is about the first.
        """
        real = FakeLabCoreGateway()
        gw = QueueFullThenClears(real)
        svc, clock = _throttled(gw)
        svc.ensure_schema()
        assert _tables(real) == set(), "nothing should have been created yet"

        gw.clear()
        clock.advance(SnapshotService.SCHEMA_RETRY_MAX + 1)
        svc.ensure_schema()
        assert svc.schema_ready is True
        assert "lem_machine_status" in _tables(real)
        assert "lem_machine_specs" in _tables(real)

    def test_a_clean_boot_still_declares_once_and_stops(self):
        """The cost this whole method exists to avoid: ten CREATEs through a
        queue that serialises at ~1.5 ops/sec, on every tray restart."""
        real = FakeLabCoreGateway()
        gw = QueueFullThenClears(real)
        gw.clear()
        svc = SnapshotService(gw)
        svc.ensure_schema()
        first = len(gw.writes)
        assert first > 0
        for _ in range(5):
            svc.ensure_schema()
        assert len(gw.writes) == first

    def test_the_refusal_is_reported_not_swallowed(self):
        """`except Exception: pass` left nothing for anyone to find."""
        gw = QueueFullThenClears(FakeLabCoreGateway())
        svc = SnapshotService(gw)
        svc.ensure_schema()
        assert svc.schema_error
        assert "busy" in svc.schema_error.lower()

    def test_a_read_still_happens_while_the_schema_is_degraded(self):
        """The service must not refuse to work — a floor drawn from tables that
        DO exist beats a blank one. Degraded is a state to report, not to fail
        on."""
        real = FakeLabCoreGateway()
        SnapshotService(real).ensure_schema()      # a lab that already exists
        gw = QueueFullThenClears(real)
        svc = SnapshotService(gw)
        svc.refresh()
        assert svc.get().get("ready") is True


class TestARefusedAlterIsNotRecordedAsMigrated:
    def test_the_column_is_added_once_the_queue_clears(self):
        """`_migrate` wrote the column into its own `checked` set inside a
        `try` whose `except` a queue refusal never reaches, so a refused ALTER
        was remembered as applied. `correction` missing from
        lem_machine_specs is the corrections feature reading nothing, on a
        floor with no way to know."""
        real = FakeLabCoreGateway()
        # A pre-migration table: exists, but without `correction`.
        real.sql("CREATE TABLE IF NOT EXISTS lem_machine_specs ("
                 "machine_uid TEXT NOT NULL, test_name TEXT NOT NULL, "
                 "sample_id TEXT, expected REAL, std_dev REAL, k REAL, "
                 "units TEXT, low REAL, high REAL, last_qc_at TEXT, "
                 "last_qc_value REAL, last_qc_in_spec INTEGER, "
                 "updated_at TEXT, PRIMARY KEY (machine_uid, test_name))")

        gw = QueueFullThenClears(real)
        svc, clock = _throttled(gw)
        svc.ensure_schema()
        cols = {r["name"] for r in real.read_sql(
            "SELECT name FROM pragma_table_info('lem_machine_specs')")["rows"]}
        assert "correction" not in cols
        assert svc.schema_ready is False

        gw.clear()
        clock.advance(SnapshotService.SCHEMA_RETRY_MAX + 1)
        svc.ensure_schema()
        cols = {r["name"] for r in real.read_sql(
            "SELECT name FROM pragma_table_info('lem_machine_specs')")["rows"]}
        assert "correction" in cols, (
            "a refused ALTER was recorded as migrated and never retried")

    def test_a_refused_ALTER_ALONE_still_leaves_the_service_degraded(self):
        """The precise defect, isolated — and this test exists because the
        first one above did NOT catch it.

        Reverting `_migrate` to record the column unconditionally left that
        test green, because every CREATE was ALSO being refused there, so
        `_schema_ready` stayed False for the other reason and the ALTER got
        retried by accident. The dangerous case is the one where the tables all
        exist and ONLY the ALTER is turned away: nothing else holds the flag
        down, so the service latches "ready" with `correction` missing from
        lem_machine_specs, and the corrections feature reads a column that
        is not there for the life of the process.
        """
        real = FakeLabCoreGateway()
        SnapshotService(real).ensure_schema()          # a fully built lab...
        real.sql("ALTER TABLE lem_machine_specs DROP COLUMN correction")

        class RefusesOnlyAlters:
            def __init__(self, inner):
                self.inner = inner
                self.alters = 0

            def sql(self, sql, args=None, **kw):
                if sql.strip().upper().startswith("ALTER"):
                    self.alters += 1
                    return dict(BUSY)
                return self.inner.sql(sql, args, **kw)

            def read_sql(self, sql, args=None, **kw):
                return self.inner.read_sql(sql, args, **kw)

            def is_running(self):
                return True

        gw = RefusesOnlyAlters(real)
        svc, clock = _throttled(gw)
        svc.ensure_schema()
        assert gw.alters == 1
        assert svc.schema_ready is False, (
            "the service called itself ready with a column that was refused")
        assert "correction" in svc.schema_error

        clock.advance(SnapshotService.SCHEMA_RETRY_MAX + 1)
        svc.ensure_schema()
        assert gw.alters == 2, "a refused ALTER was never retried"

    def test_an_already_applied_migration_is_not_a_failure(self):
        """"duplicate column name" means the work is done. Treating it as a
        refusal would keep the service permanently degraded on every boot after
        the first."""
        real = FakeLabCoreGateway()
        svc = SnapshotService(real)
        svc.ensure_schema()
        again = SnapshotService(real)
        again.ensure_schema()
        assert again.schema_ready is True
        assert not again.schema_error


class TestHealthzSaysSo:
    """A degraded schema that nothing reports is the same silence one layer up.

    /healthz is what the updater asks before a release goes live, and
    RELEASING.md §5 says nothing else in the pipeline catches a release that
    starts perfectly and shows the wrong thing.
    """

    def _client(self, gw):
        from web_app import create_app
        app = create_app(gw, secret="s")
        app.config["TESTING"] = True
        return app, app.test_client()

    def test_a_healthy_server_says_ok(self):
        """Driven through `refresh()` — the path a running server actually
        takes — rather than by calling `ensure_schema()` from the test.

        Reaching into the service was how this test hid the bug below: it made
        the one state /healthz got wrong impossible to reach.
        """
        gw = FakeLabCoreGateway()
        app, client = self._client(gw)
        app.config["SNAPSHOTS"].refresh()
        body = client.get("/healthz").get_json()
        assert body["status"] == "ok"
        assert body["schema"] == "ok"
        assert body["schema_error"] == ""

    def test_a_freshly_booted_server_is_not_reported_degraded(self):
        """THE FALSE ALARM. A server that has not refreshed yet has not asked
        LabCore anything, so `schema_ready` is False for the most innocent
        reason there is — and /healthz called that `degraded`, with an EMPTY
        `schema_error`, on a perfectly good release.

        That is not cosmetic. RELEASING.md's updater starts a candidate on a
        scratch port and probes it before it is live, which is precisely the
        moment before the first refresh. A degraded reading there blocks a
        release that works, and a health check that cries wolf gets ignored —
        taking the real signal with it.
        """
        gw = FakeLabCoreGateway()
        _app, client = self._client(gw)
        body = client.get("/healthz").get_json()
        assert body["status"] == "ok"
        assert body["schema"] != "degraded", (
            "a server that has not looked yet was reported as broken")
        assert body["schema_error"] == ""

    def test_degraded_is_never_reported_without_a_reason(self):
        """The tell. "degraded" with nothing in `schema_error` means nobody
        asked, and that is a different fact."""
        for gw in (FakeLabCoreGateway(),
                   QueueFullThenClears(FakeLabCoreGateway())):
            app, client = self._client(gw)
            app.config["SNAPSHOTS"].refresh()
            body = client.get("/healthz").get_json()
            if body["schema"] == "degraded":
                assert body["schema_error"], body

    def test_a_degraded_schema_is_visible(self):
        gw = QueueFullThenClears(FakeLabCoreGateway())
        app, client = self._client(gw)
        app.config["SNAPSHOTS"].ensure_schema()
        body = client.get("/healthz").get_json()
        assert body["schema"] == "degraded"
        assert "busy" in (body.get("schema_error") or "").lower()

    def test_healthz_still_answers_200(self):
        """It must not fail the release. A degraded schema is a thing to SEE —
        answering 500 here would make a working server look broken, which is
        the mistake the route's own docstring already warns about."""
        gw = QueueFullThenClears(FakeLabCoreGateway())
        app, client = self._client(gw)
        app.config["SNAPSHOTS"].ensure_schema()
        assert client.get("/healthz").status_code == 200


# ── the storm the latch fix left behind (2026-08-25) ─────────────────────────
#
# Removing "set `_schema_ready` first" removed the only thing that stopped
# `ensure_schema()` re-issuing the WHOLE DDL set. Nothing else throttled it,
# and everything calls it: `read_tables()` on every 12-second refresh, every
# audit line, every PM completion, every maintenance import.
#
# So on a lab whose CREATEs are being refused, this branch shovels ten writes
# per refresh — dozens a minute — into a queue that is refusing precisely
# BECAUSE it is full, and it does it while ignoring the `retry_after` LabCore
# sent with the refusal. The latch was a bug; the unthrottled retry is the same
# bug's opposite, and both are paid for by the same queue.
#
# The property the latch fix won has to survive the throttle: a refused CREATE
# is still retried eventually, and is still never recorded as done.

class TestARefusedDeclarationIsNotRetriedOnEveryCall:
    def test_a_second_call_straight_away_writes_nothing(self):
        """The storm, at its smallest. `read_tables` calls this every refresh
        and every audit write calls it too."""
        gw = QueueFullThenClears(FakeLabCoreGateway())
        svc, _clock = _throttled(gw)
        svc.ensure_schema()
        first = len(gw.writes)
        assert first > 0
        for _ in range(20):
            svc.ensure_schema()
        assert len(gw.writes) == first, (
            "the refused DDL set was re-issued into a queue that is refusing "
            "because it is already full")

    def test_it_is_still_retried_once_the_cooldown_passes(self):
        """The half the latch fix won. Throttled is not abandoned."""
        real = FakeLabCoreGateway()
        gw = QueueFullThenClears(real)
        svc, clock = _throttled(gw)
        svc.ensure_schema()
        assert svc.schema_ready is False

        gw.clear()
        clock.advance(SnapshotService.SCHEMA_RETRY_MAX + 1)
        svc.ensure_schema()
        assert svc.schema_ready is True
        assert "lem_machine_status" in _tables(real)

    def test_a_refusal_is_never_recorded_as_done_while_it_waits(self):
        """Skipping the retry must not look like success to anything that
        asks — /healthz reads exactly this flag."""
        gw = QueueFullThenClears(FakeLabCoreGateway())
        svc, _clock = _throttled(gw)
        svc.ensure_schema()
        svc.ensure_schema()
        assert svc.schema_ready is False
        assert svc.schema_error

    def test_the_queues_own_retry_after_is_honoured_when_it_is_longer(self):
        """`labcore_result.retry_after` exists so a caller can honour this.
        A queue that says "come back in ten minutes" must not be asked again
        in five."""
        long_wait = dict(BUSY)
        long_wait["retry_after"] = SnapshotService.SCHEMA_RETRY_MAX * 4

        class SaysComeBackLater(QueueFullThenClears):
            def sql(self, sql, args=None, **kw):
                self.writes.append(sql)
                return dict(long_wait)

        gw = SaysComeBackLater(FakeLabCoreGateway())
        svc, clock = _throttled(gw)
        svc.ensure_schema()
        first = len(gw.writes)

        clock.advance(SnapshotService.SCHEMA_RETRY_MAX + 1)
        svc.ensure_schema()
        assert len(gw.writes) == first, (
            "LabCore asked for longer than that and was ignored")

        clock.advance(long_wait["retry_after"])
        svc.ensure_schema()
        assert len(gw.writes) > first, "and it must come back afterwards"

    def test_the_wait_grows_while_LabCore_keeps_refusing(self):
        """A queue still full after the first retry is not helped by asking at
        the same rate forever."""
        gw = QueueFullThenClears(FakeLabCoreGateway())
        svc, clock = _throttled(gw)
        svc.ensure_schema()
        waits = []
        for _ in range(4):
            before = len(gw.writes)
            waited = 0.0
            for _step in range(200):
                clock.advance(SnapshotService.SCHEMA_RETRY_MIN / 4.0)
                waited += SnapshotService.SCHEMA_RETRY_MIN / 4.0
                svc.ensure_schema()
                if len(gw.writes) > before:
                    break
            waits.append(waited)
        assert waits[0] >= SnapshotService.SCHEMA_RETRY_MIN
        assert waits[-1] > waits[0], "the back-off never grew: {0}".format(waits)
        assert max(waits) <= SnapshotService.SCHEMA_RETRY_MAX * 1.5

    def test_a_healthy_lab_pays_nothing_on_the_steady_state_path(self):
        """No clock, no read, no write once everything is acknowledged — this
        runs inside every 12-second refresh."""
        real = FakeLabCoreGateway()
        gw = QueueFullThenClears(real)
        gw.clear()
        svc, clock = _throttled(gw)
        svc.ensure_schema()
        settled = len(gw.writes)

        class Explodes:
            def __call__(self):
                raise AssertionError("the steady-state path consulted the clock")

        svc._clock = Explodes()
        for _ in range(10):
            svc.ensure_schema()
        assert len(gw.writes) == settled

    def test_the_floor_is_still_drawn_while_the_retry_waits(self):
        """Throttling the DDL must not throttle the READ it sits in front of."""
        real = FakeLabCoreGateway()
        SnapshotService(real).ensure_schema()          # a lab that exists
        gw = QueueFullThenClears(real)
        svc, _clock = _throttled(gw)
        svc.refresh()
        svc.refresh()
        assert svc.get().get("ready") is True
