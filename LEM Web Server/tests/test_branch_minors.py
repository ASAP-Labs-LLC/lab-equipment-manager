#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The small things two critics found, each with a real cost behind it.

None of these is a blocker. Every one of them is the same family: a rule
re-derived where a shared one exists, a read that declares a schema, a wait
with no ceiling, or an answer that cannot tell "it failed" from "there is
nothing".
"""
import pytest

import refusal_shapes
from labcore_gateway import FakeLabCoreGateway

BUSY = refusal_shapes.EVIDENCED
BLIP = {"error": "HTTPSConnectionPool(host='labvision'): Read timed out"}


# ── the wait is read once, not re-derived ───────────────────────────────────
class TestRetryAfterComesFromTheSharedRule:
    """`checklists._retry_after` re-implemented `labcore_result.retry_after`,
    in the file that already imports from that module. Two readings of one
    field is how the app ended up with three answers to "did that write
    happen" in a single week."""

    def test_it_is_LITERALLY_the_shared_one(self):
        """Not "agrees with" — IS. Two implementations that agree today are how
        this app got three answers to one question in a week."""
        import checklists
        import labcore_result

        assert checklists._retry_after is labcore_result.retry_after

    def test_the_shared_one_answers_every_case_the_local_copy_did(self):
        from labcore_result import retry_after

        assert retry_after(BUSY, 2.0) == 4.0
        assert retry_after({"retry_after": "9"}, 2.0) == 9.0
        assert retry_after({}, 2.0) == 2.0
        assert retry_after(None, 2.0) == 2.0
        assert retry_after({"retry_after": None}, 2.0) == 2.0
        assert retry_after({"retry_after": "soon"}, 2.0) == 2.0

    def test_zero_still_means_come_straight_back(self):
        import checklists
        assert checklists._retry_after({"retry_after": 0}, 2.0) == 0.0

    def test_a_nonsense_wait_falls_back(self):
        import checklists
        assert checklists._retry_after({"retry_after": -5}, 2.0) == 2.0


# ── a bulk import cannot hold a request thread for minutes ──────────────────
class TestTheImportHasABudget:
    """`import_state` runs on the Flask request thread. Six escalating waits
    per batch, capped at 15s each, times as many batches as the file has, is a
    request that can sit there for minutes — with the browser waiting, a
    gunicorn/waitress worker held, and nothing to say how far it got.

    A deadline does not make the import less honest: an exhausted batch still
    raises, and re-running is an upsert that skips what landed.
    """

    class AlwaysBusy:
        def __init__(self):
            self.calls = 0

        def sql(self, sql, args=None, **kw):
            self.calls += 1
            return dict(BUSY)

        def read_sql(self, sql, args=None, **kw):
            return {"ok": True, "rows": []}

    def _rows(self, n):
        return [{"day": "2026-01-%02d" % (i + 1), "checklist_uid": "c1",
                 "item_uid": "i1", "checked": True, "user": "r",
                 "at": "2026-01-01T09:00:00", "value": ""} for i in range(n)]

    class FakeTime:
        """Sleeping advances the clock, the way it does outside a test."""

        def __init__(self):
            self.t = 1000.0

        def monotonic(self):
            return self.t

        def sleep(self, seconds):
            self.t += seconds

    def test_it_gives_up_inside_its_budget(self, monkeypatch):
        import checklists
        from checklists import ChecklistStore, ChecklistWriteError

        clock = self.FakeTime()
        monkeypatch.setattr(checklists, "time", clock)
        store = ChecklistStore(self.AlwaysBusy())
        store._ready = True
        with pytest.raises(ChecklistWriteError):
            store.import_state(self._rows(500), batch=10, attempts=6,
                               pause=0.0, budget=5.0)
        waited = clock.t - 1000.0
        assert waited <= 6.0, (
            "the request thread waited {0:.0f}s on a queue that was saying "
            "no".format(waited))

    def test_without_a_budget_it_would_have_waited_far_longer(self):
        """The measurement the fix is against — 500 rows in batches of ten,
        every batch refused, is minutes of held request thread."""
        clock = self.FakeTime()
        import checklists

        real = checklists.time
        checklists.time = clock
        try:
            store = ChecklistStore = checklists.ChecklistStore(self.AlwaysBusy())
            store._ready = True
            with pytest.raises(checklists.ChecklistWriteError):
                store.import_state(self._rows(500), batch=10, attempts=6,
                                   pause=0.0, budget=10_000.0)
        finally:
            checklists.time = real
        assert clock.t - 1000.0 >= 30.0

    def test_the_budget_is_named_in_the_refusal(self, monkeypatch):
        import checklists
        from checklists import ChecklistStore, ChecklistWriteError

        monkeypatch.setattr(checklists, "time", self.FakeTime())
        store = ChecklistStore(self.AlwaysBusy())
        store._ready = True
        with pytest.raises(ChecklistWriteError) as caught:
            store.import_state(self._rows(50), batch=10, attempts=6,
                               pause=0.0, budget=0.0)
        message = str(caught.value)
        assert "landed" in message, message
        assert "again" in message, "and it has to say the import is repeatable"

    def test_a_healthy_import_is_untouched(self):
        from checklists import ChecklistStore

        gw = FakeLabCoreGateway()
        store = ChecklistStore(gw)
        store.ensure_schema()
        assert store.import_state(self._rows(25), batch=10, pause=0.0) == 25


# ── a read declares nothing, including this one ─────────────────────────────
class TestTheScheduleReadDeclaresNothing:
    """The last read path in the app still issuing CREATEs.

    `load()` called `ensure_schema()`, which raises when a CREATE is refused —
    so a full WRITE queue made the lab's opening hours unreadable, for two
    tables that have existed for months, and pushed two more writes into the
    queue while doing it. That is the exact regression
    test_reads_survive_a_full_write_queue.py exists for; this path was missed.
    """

    class WriteQueueFull:
        def __init__(self, real):
            self.real = real
            self.writes = []

        def sql(self, sql, args=None, **kw):
            self.writes.append(sql)
            return dict(BUSY)

        def read_sql(self, sql, args=None, **kw):
            return self.real.read_sql(sql, args, **kw)

        def is_running(self):
            return True

    def test_the_hours_are_still_readable_while_writes_are_refused(self):
        from lab_schedule import LabSchedule, LabScheduleStore

        gw = FakeLabCoreGateway()
        LabScheduleStore(gw).save(LabSchedule(working_days=[0, 1, 2, 3, 4, 5],
                                              opens="06:00", closes="18:00",
                                              holidays={"2026-12-25": "Xmas"}))
        blocked = self.WriteQueueFull(gw)
        loaded = LabScheduleStore(blocked).load()
        assert loaded.opens == "06:00"
        assert loaded.working_days == [0, 1, 2, 3, 4, 5]
        assert loaded.holidays == {"2026-12-25": "Xmas"}

    def test_it_issues_no_writes_at_all(self):
        from lab_schedule import LabSchedule, LabScheduleStore

        gw = FakeLabCoreGateway()
        LabScheduleStore(gw).save(LabSchedule())
        blocked = self.WriteQueueFull(gw)
        LabScheduleStore(blocked).load()
        assert blocked.writes == [], (
            "a read pushed {0} more statements into a full queue".format(
                len(blocked.writes)))

    def test_a_lab_that_never_set_its_hours_gets_the_default_week(self):
        """The tables genuinely do not exist. That is the one error a read may
        call empty, and it is what a fresh LabCore looks like."""
        from lab_schedule import LabScheduleStore

        loaded = LabScheduleStore(FakeLabCoreGateway()).load()
        assert loaded.working_days == [0, 1, 2, 3, 4]

    def test_an_unreadable_schedule_still_raises(self):
        """The half that must not be lost: `save()` fills its blanks from
        this, so a degraded read would post back a Mon-Fri week with every
        holiday deleted."""
        from labcore_result import LabCoreError
        from lab_schedule import LabScheduleStore

        class Unreadable:
            def sql(self, *a, **k):
                return {"ok": True}

            def read_sql(self, *a, **k):
                return dict(BLIP)

        with pytest.raises(LabCoreError):
            LabScheduleStore(Unreadable()).load()

    def test_saving_still_declares_the_tables(self):
        """A write into a table that may not exist is the bug this branch
        fixed, and it stays fixed."""
        from lab_schedule import LabSchedule, LabScheduleStore
        from labcore_result import LabCoreError

        blocked = self.WriteQueueFull(FakeLabCoreGateway())
        with pytest.raises(LabCoreError):
            LabScheduleStore(blocked).save(LabSchedule())
        assert any("CREATE TABLE" in s for s in blocked.writes)


# ── a filter list that failed is not a lab with no history ──────────────────
class TestTheLogKindsCacheKnowsWhichItIs:
    """`kinds_now()` answered `[]` both when the DISTINCT failed and when the
    log is genuinely empty, and judged the answer with `res.get("error")` —
    the hand-rolled test this branch exists to remove, so a refusal carrying
    no "error" key was read as "this lab has never logged anything".

    The cost is small and it is not nothing: the page's filter drop-down comes
    up empty during congestion, so the one tool for finding a single machine's
    QC in a busy log disappears exactly when the log is busiest. And nothing
    said why.
    """

    class StubAuth:
        def login(self, username, password):
            return ("kaden", "tok", "")

        def logout(self, token):
            pass

    def _client(self, gateway):
        from web_app import create_app
        app = create_app(gateway, authenticator=self.StubAuth(), secret="s")
        app.config["TESTING"] = True
        return app.test_client()

    class FailsTheDistinct:
        def __init__(self, real, answer):
            self.real = real
            self.answer = answer

        def sql(self, sql, args=None, **kw):
            return self.real.sql(sql, args, **kw)

        def read_sql(self, sql, args=None, **kw):
            if "DISTINCT kind" in sql:
                return dict(self.answer)
            return self.real.read_sql(sql, args, **kw)

        def is_running(self):
            return True

        def get_test_names(self):
            return self.real.get_test_names()

        def get_samples(self, **kw):
            return self.real.get_samples(**kw)

    @pytest.fixture
    def lab(self):
        from snapshot_service import SnapshotService

        gw = FakeLabCoreGateway()
        SnapshotService(gw).ensure_schema()
        gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
               "test_name, value, detail) VALUES ('m1', ?, 'qc', 'L1', "
               "'Flash Point', '63.7', '{}')", ["2026-08-20T09:00:00"])
        return gw

    def test_a_lab_with_no_log_yet_says_so(self, lab):
        gw = FakeLabCoreGateway()
        body = self._client(gw).get("/api/logs").get_json()
        assert body["kinds"] == []
        assert body.get("kinds_known") is True, (
            "an empty log IS an answer, and must not read as a failure")

    def test_a_failed_distinct_is_not_an_empty_vocabulary(self, lab):
        for answer in (BLIP, BUSY, {"ok": False}):
            client = self._client(self.FailsTheDistinct(lab, answer))
            body = client.get("/api/logs").get_json()
            assert body.get("kinds_known") is False, answer
            assert body["kinds"], (
                "the filter drop-down went empty because a read failed: "
                "{0}".format(answer))

    def test_a_failed_distinct_is_never_cached(self, lab):
        """`_page` caches whatever it is handed, and only a config write drops
        this key — so one bad read could empty the filter for days."""
        broken = self.FailsTheDistinct(lab, BLIP)
        client = self._client(broken)
        client.get("/api/logs")
        broken.answer = None                       # LabCore comes back

        def healthy(sql, args=None, **kw):
            return lab.read_sql(sql, args, **kw)

        broken.read_sql = healthy
        body = client.get("/api/logs").get_json()
        assert body["kinds"] == ["qc"]
        assert body.get("kinds_known") is True

    def test_the_events_still_arrive(self, lab):
        body = self._client(lab).get("/api/logs").get_json()
        assert [e["kind"] for e in body["events"]] == ["qc"]


# ── the dev seeder judges its own writes ────────────────────────────────────
class TestTheDemoSeederIsJudgedToo:
    """Three `gw.write(...)` calls and one `save()` whose answers nobody read.

    JUDGED rather than deleted, which is a call worth defending. `--dev --seed`
    is what a person runs to look at the floor with data on it, and it is the
    one place in this tree where a write's answer was still being dropped. It
    costs four lines to read them, and a seeder that half-worked and said
    nothing produces exactly the confusing empty demo it exists to avoid.
    Deleting the writes instead would remove the demo; leaving them unread
    would leave one live example of the habit the whole branch is about.
    """

    def _seeder(self):
        import importlib.util
        import os

        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "web_server.pyw")
        spec = importlib.util.spec_from_loader(
            "lem_web_server_seeder",
            importlib.machinery.SourceFileLoader("lem_web_server_seeder",
                                                 path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_a_healthy_seed_still_works(self):
        module = self._seeder()
        gw = FakeLabCoreGateway()
        module._seed_demo(gw)
        rows = gw.read_sql("SELECT uid FROM lem_boxes")["rows"]
        assert [r["uid"] for r in rows] == ["gc1"]

    def test_a_refused_QC_ROW_does_not_pass_for_a_seeded_demo(self):
        """Only the three named-operation writes are refused, so the config
        save still succeeds. Otherwise this passes on the config store's
        verdict and proves nothing about the writes it is aimed at — which is
        what the first draft of it did.
        """
        module = self._seeder()

        class RefusesTheSampleRows:
            def __init__(self, real):
                self.real = real

            def sql(self, sql, args=None, **kw):
                return self.real.sql(sql, args, **kw)

            def read_sql(self, sql, args=None, **kw):
                return self.real.read_sql(sql, args, **kw)

            def write(self, operation, params, **kw):
                return dict(BUSY)

            def is_running(self):
                return True

        with pytest.raises(RuntimeError) as caught:
            module._seed_demo(RefusesTheSampleRows(FakeLabCoreGateway()))
        assert "insert_sample" in str(caught.value)

    def test_a_refused_CONFIG_save_is_reported_too(self):
        module = self._seeder()

        class RefusesEverything:
            def __init__(self, real):
                self.real = real

            def sql(self, sql, args=None, **kw):
                return dict(BUSY)

            def read_sql(self, sql, args=None, **kw):
                return self.real.read_sql(sql, args, **kw)

            def write(self, operation, params, **kw):
                return self.real.write(operation, params, **kw)

            def is_running(self):
                return True

        with pytest.raises(RuntimeError) as caught:
            module._seed_demo(RefusesEverything(FakeLabCoreGateway()))
        assert "configuration" in str(caught.value)


# ── the LAST read path that declared a schema ───────────────────────────────
class TestTheConfigReadDeclaresNothingEither:
    """`DbConfigStore.load()` called `_ensure_schema()` too.

    It is cheaper than the schedule store's version — it asks
    `existing_tables()` first and skips what is there — but the two cases that
    remain are the bad ones: a LabCore where the tables do not exist yet, and
    one where that question could not be answered (`existing_tables` returns
    None, which means "declare everything"). Both push five CREATEs into the
    queue from a path that is only trying to READ, and on a full queue they are
    five refusals.

    The declaration buys the read nothing now that `_read` swallows "no such
    table" — a lab that has never saved its config genuinely has no config.
    `save()` still declares, strictly, because a write into a table that may
    not exist is the bug this branch fixed.
    """

    class CountingWrites:
        def __init__(self, real, blind=False):
            self.real = real
            self.blind = blind          # `existing_tables` cannot be answered
            self.writes = []

        def sql(self, sql, args=None, **kw):
            self.writes.append(sql)
            return dict(BUSY)

        def read_sql(self, sql, args=None, **kw):
            if self.blind and ("pragma_table_list" in sql
                               or "sqlite_master" in sql):
                return dict(BLIP)
            return self.real.read_sql(sql, args, **kw)

        def is_running(self):
            return True

    def test_loading_a_config_writes_nothing(self):
        from db_config_store import DbConfigStore

        gw = FakeLabCoreGateway()
        DbConfigStore(gw).save(_a_config())
        blocked = self.CountingWrites(gw)
        store = DbConfigStore(blocked)
        blocked.writes.clear()              # construction may still declare
        assert [s.name for s in store.load().samples] == ["Diesel - AO25"]
        assert blocked.writes == [], (
            "a config READ pushed {0} statements into a full queue".format(
                len(blocked.writes)))

    def test_it_writes_nothing_even_when_it_cannot_see_the_table_list(self):
        from db_config_store import DbConfigStore

        gw = FakeLabCoreGateway()
        DbConfigStore(gw).save(_a_config())
        blocked = self.CountingWrites(gw, blind=True)
        store = DbConfigStore(blocked)
        blocked.writes.clear()
        store.load()
        assert blocked.writes == []

    def test_a_fresh_LabCore_still_loads_the_default(self):
        from db_config_store import DbConfigStore

        cfg = DbConfigStore(FakeLabCoreGateway()).load()
        assert cfg.boxes == [] and cfg.samples == []

    def test_saving_still_declares(self):
        from db_config_store import DbConfigStore

        blocked = self.CountingWrites(FakeLabCoreGateway())
        store = DbConfigStore(blocked)
        blocked.writes.clear()
        ok, _why = store.save(_a_config())
        assert ok is False
        assert any("CREATE TABLE" in s for s in blocked.writes)


def _a_config():
    from models import AppConfig, BoxConfig, SampleSpec

    return AppConfig(
        version=5, poll_minutes=5, map_locked=False, sample_id_column="Lab ID",
        samples=[SampleSpec(name="Diesel - AO25", sample_id_val="STD-1",
                            tests=[])],
        boxes=[BoxConfig(uid="m1", title="Multitek NS", csv_path="")])
