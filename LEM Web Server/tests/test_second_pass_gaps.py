#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""What the second pass over `fix/confirm-every-write` found still judging by hand.

Same family as everything else on this branch, in the places the first pass
missed. Each of these was a hand-rolled verdict about a LabCore answer — most
of them `res.get("error")`, which is blind to a refusal that reports itself any
other way, and one of them the opposite mistake of DEMANDING a positive `ok`
from a read.

Every suite here drives BOTH refusal shapes (see tests/refusal_shapes.py):
the recorded busy dict, and a synthetic answer with no "error" key. The second
is the one that matters, because `{"error": ...}` is precisely the shape the
old code already coped with — a test that only drives it proves nothing.
"""
import json

import pytest

import refusal_shapes
from labcore_gateway import FakeLabCoreGateway
from web_app import create_app

pytestmark = pytest.mark.usefixtures("both_refusal_shapes")


class StubAuth:
    def login(self, username, password):
        return ("kaden", "tok", "")

    def logout(self, token):
        pass


class Selective:
    """A real gateway that refuses the statements a test names.

    Answers `refusal_shapes.current()`, so every test runs once per shape.
    """

    def __init__(self, real, fail_read=lambda s: False,
                 fail_write=lambda s: False):
        self.real = real
        self.fail_read = fail_read
        self.fail_write = fail_write
        self.wrote = []

    def sql(self, sql, args=None, **kw):
        self.wrote.append(sql)
        if self.fail_write(sql):
            return refusal_shapes.current()
        return self.real.sql(sql, args, **kw)

    def read_sql(self, sql, args=None, **kw):
        if self.fail_read(sql):
            return refusal_shapes.current()
        return self.real.read_sql(sql, args, **kw)


def _client(gateway):
    app = create_app(gateway, authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/api/login", json={"username": "kaden", "password": "p"})
    return c, app


@pytest.fixture
def lab():
    gw = FakeLabCoreGateway()
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
           "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
           "detail TEXT)")
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
           "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
           "reason TEXT, updated_at TEXT)")
    gw.sql("INSERT INTO lem_machine_status VALUES "
           "('m1','OptiMPP 1','GREEN','ok','2026-08-03T09:00:00')")
    gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
           ["m1", "2026-07-02T09:00:00", "qc", "CP", "Cloud Point", "-7.2",
            json.dumps({"in_spec": True})])
    return gw


# ── the log read ────────────────────────────────────────────────────────────

class TestTheLogReadIsJudgedByTheSharedRule:
    """`_log_rows` still tested `res.get("error")` by hand.

    A refusal carrying no "error" key therefore read as a successful read of
    zero rows: /api/logs answered 200 with no events and NO banner, and
    /api/logs.csv served a header row and nothing under it — the version of
    this bug that leaves the building and gets filed as the lab's history.
    """

    def test_a_refused_log_read_raises_the_banner(self, lab):
        gw = Selective(lab, fail_read=lambda s: "lem_machine_log" in s)
        client, _app = _client(gw)
        body = client.get("/api/logs").get_json()
        assert body.get("error"), (
            "an unreadable log served as an empty one, with nothing saying so")
        assert body["events"] == []

    def test_a_refused_log_read_withholds_the_csv(self, lab):
        gw = Selective(lab, fail_read=lambda s: "lem_machine_log" in s)
        client, _app = _client(gw)
        res = client.get("/api/logs.csv")
        assert res.status_code == 503

    def test_a_lab_with_no_log_table_yet_is_still_empty_not_an_error(self):
        """The one degrade that stays. `lem_machine_log` is created centrally at
        boot, so a read before that has run is honestly looking at nothing."""
        client, _app = _client(FakeLabCoreGateway())
        body = client.get("/api/logs").get_json()
        assert body["events"] == [] and not body.get("error")

    def test_a_readable_log_still_arrives(self, lab):
        client, _app = _client(lab)
        body = client.get("/api/logs").get_json()
        assert [e["lab_id"] for e in body["events"]] == ["CP"]
        assert not body.get("error")


# ── the method picker ───────────────────────────────────────────────────────

class TestTestNamesDoesNotDegradeToAnEmptyPicker:
    """`/api/test-names` had TWO hand-rolled judgements between LabCore and the
    picker, and the picker is where a QC standard's test names come from —
    LabCore's methods are the only allowed ones (CLAUDE.md: LEM has no test
    names of its own).

    `FakeLabCoreGateway.get_test_names` required a positive `ok`, which is the
    rule `labcore_result` documents as unsafe, and the route's own DISTINCT
    fallback took `res.get("rows") or []` without looking at the answer at all.
    Between them, a busy queue produced `{"tests": []}` with HTTP 200 — a picker
    that offers nothing, on a page where offering nothing looks exactly like a
    lab that has not set its methods up yet.
    """

    def test_a_refused_lookup_is_not_an_empty_method_list(self):
        gw = Selective(FakeLabCoreGateway(),
                       fail_read=lambda s: "test_name" in s)
        client, _app = _client(gw)
        res = client.get("/api/test-names")
        assert res.status_code == 503
        body = res.get_json()
        assert body.get("retry") is True
        assert "tests" not in body or body["tests"] == []

    def test_an_answer_with_rows_and_no_verdict_is_still_an_answer(self):
        """The other direction: demanding `ok` throws away a perfectly good
        read, and nothing records what LabCore's read actually answers."""
        real = FakeLabCoreGateway()
        real.sql("INSERT INTO sample_tests VALUES ('L-1','Flash Point','1','x')")

        class NoVerdict:
            def sql(self, sql, args=None, **kw):
                return real.sql(sql, args, **kw)

            def read_sql(self, sql, args=None, **kw):
                answer = dict(real.read_sql(sql, args, **kw))
                answer.pop("ok", None)
                return answer

            def get_test_names(self, **kw):
                return None                   # forces the DISTINCT fallback

            def is_running(self):
                return True

        client, _app = _client(NoVerdict())
        assert client.get("/api/test-names").get_json()["tests"] == \
            ["Flash Point"]

    def test_a_lab_with_no_methods_yet_still_answers_empty(self):
        client, _app = _client(FakeLabCoreGateway())
        res = client.get("/api/test-names")
        assert res.status_code == 200 and res.get_json()["tests"] == []

    def test_a_failed_lookup_is_never_cached_as_the_answer(self):
        """`_test_name_cache` is filled from this route. A degraded empty list
        cached at boot would outlive the blip that caused it."""
        gw = Selective(FakeLabCoreGateway(),
                       fail_read=lambda s: "test_name" in s)
        client, app = _client(gw)
        client.get("/api/test-names")
        gw.fail_read = lambda s: False
        gw.real.sql("INSERT INTO sample_tests VALUES ('L-1','Cloud Point','1','x')")
        assert client.get("/api/test-names").get_json()["tests"] == \
            ["Cloud Point"]

    def test_the_floor_keeps_the_list_it_had(self):
        """The page half: `loadTests` blanked TESTS on any failure, so one
        refused poll emptied the picker that was already on screen."""
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        floor = open(os.path.join(here, "templates", "floor.html"),
                     encoding="utf-8").read()
        block = floor.split("async function loadTests()", 1)[1][:400]
        assert "TESTS = (b && b.tests) || []" not in block


# ── the audit trail ─────────────────────────────────────────────────────────

class TestAnUnrecordedAuditLineIsVisibleToTheOperator:
    """`_audit` swallows a refused audit write with only a `logger.warning`.

    Swallowing the EXCEPTION is right — the operator's change already happened
    and an audit line cannot undo it. Swallowing the KNOWLEDGE is not: this is
    the trail that answers "who changed that band", the route answered a clean
    `{"ok": true}`, and on the target platform that warning went to a stderr
    that does not exist. The change succeeds; the record of who made it
    vanishes; nobody finds out until an auditor asks.
    """

    def _lab(self):
        gw = FakeLabCoreGateway()
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
               "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
               "reason TEXT, updated_at TEXT)")
        gw.sql("INSERT INTO lem_machine_status VALUES "
               "('m1','OptiMPP 1','GREEN','ok','2026-08-03T09:00:00')")
        return gw

    def test_the_change_still_succeeds(self):
        gw = Selective(self._lab(),
                       fail_write=lambda s: "lem_machine_log" in s)
        client, _app = _client(gw)
        res = client.post("/api/machines/m1/corrections",
                          json={"test_name": "Cloud Point", "correction": 0.4})
        assert res.status_code == 200
        assert res.get_json()["correction"] == 0.4

    def test_and_the_answer_says_the_trail_did_not_take_it(self):
        gw = Selective(self._lab(),
                       fail_write=lambda s: "lem_machine_log" in s)
        client, _app = _client(gw)
        body = client.post("/api/machines/m1/corrections",
                           json={"test_name": "Cloud Point",
                                 "correction": 0.4}).get_json()
        assert body.get("audit") is False
        assert body.get("warning"), "nothing on screen said the record is missing"

    def test_a_recorded_change_says_nothing_extra(self):
        client, _app = _client(self._lab())
        body = client.post("/api/machines/m1/corrections",
                           json={"test_name": "Cloud Point",
                                 "correction": 0.4}).get_json()
        assert "warning" not in body and body.get("audit") is not False


# ── the schema's own reads ──────────────────────────────────────────────────

class TestTheSchemaReadsAreJudgedTheSameWay:
    """Two hand-rolled verdicts left inside `snapshot_service`, in the code
    that decides which tables and columns to declare."""

    def _service(self, gateway):
        import snapshot_service
        return snapshot_service.SnapshotService(
            gateway=gateway, interval=999, builder=lambda t: {"machines": []})

    def test_a_refused_pragma_is_not_a_table_with_no_columns(self):
        """`_migrate` judged its `pragma_table_info` read with
        `if not res or res.get("error")`. Under the other refusal shape that
        reads as a successful answer listing NO columns — so every migration
        column looks missing, and the service issues an ALTER for each one into
        the queue that just refused, on every retry."""
        real = FakeLabCoreGateway()
        # The migration's table has to already exist, or `_migrate` skips it as
        # "just created, so it already has the column".
        import snapshot_service
        for ddl in snapshot_service.SCHEMA_DDL:
            real.sql(ddl)
        gw = Selective(real, fail_read=lambda s: "pragma_table_info" in s)
        svc = self._service(gw)
        svc.ensure_schema()
        assert "could not be inspected" in svc.schema_error
        assert not any("ALTER" in sql.upper() for sql in gw.wrote), \
            "it guessed the columns were missing and issued the ALTERs anyway"

    def test_a_refused_table_list_does_not_reissue_every_create(self):
        """`existing_tables` answers None both for "this LabCore is too old to
        ask" and for "the queue refused the question", and `ensure_schema`
        treated both as "declare everything" — ten CREATEs into a queue that is
        refusing BECAUSE it is full, every retry, for the whole life of the
        outage."""
        real = FakeLabCoreGateway()
        gw = Selective(real, fail_read=lambda s: "pragma_table_list" in s
                       or "sqlite_master" in s)
        svc = self._service(gw)
        svc.ensure_schema()
        assert not any(sql.strip().upper().startswith("CREATE")
                       for sql in gw.wrote), \
            "{0} statements issued blind".format(len(gw.wrote))
        assert svc.schema_error, "and it said nothing about why"
        assert svc.schema_ready is False

    def test_and_it_recovers_once_the_queue_drains(self):
        real = FakeLabCoreGateway()
        gw = Selective(real, fail_read=lambda s: "pragma_table_list" in s
                       or "sqlite_master" in s)
        clock = [1000.0]
        svc = self._service(gw)
        svc._clock = lambda: clock[0]
        svc.ensure_schema()
        gw.fail_read = lambda s: False
        clock[0] += 3600
        svc.ensure_schema()
        assert svc.schema_ready is True
        assert svc.schema_error == ""

    def test_a_labcore_too_old_to_answer_the_question_still_gets_its_tables(self):
        """The case that must NOT change. Both probe forms failing with a
        syntax/unknown-object error means the question cannot be asked of this
        LabCore at all — declaring blind is exactly right there, and refusing
        to would leave a fresh lab with no tables forever."""
        real = FakeLabCoreGateway()

        class TooOld(Selective):
            def read_sql(self, sql, args=None, **kw):
                if "pragma_table_list" in sql or "sqlite_master" in sql:
                    return {"error": "OperationalError: no such table: "
                                     "pragma_table_list"}
                return self.real.read_sql(sql, args, **kw)

        gw = TooOld(real)
        svc = self._service(gw)
        svc.ensure_schema()
        assert any(sql.strip().upper().startswith("CREATE") for sql in gw.wrote)
        assert svc.schema_ready is True


# ── a config row nobody can parse ───────────────────────────────────────────

class TestAnUnreadableConfigRowIsNotQuietlyDeleted:
    """`_read_rows` drops a row whose JSON will not parse, and `save()` then
    prunes every id it was not handed — so one corrupt blob is silently DELETED
    by the next save of anything else. The row may be recoverable by hand; once
    the prune has run it is not recoverable at all.
    """

    def _store(self):
        from db_config_store import DbConfigStore
        gw = FakeLabCoreGateway()
        store = DbConfigStore(gw)
        return gw, store

    def test_a_corrupt_row_survives_the_next_save(self):
        from models import AppConfig, BoxConfig

        gw, store = self._store()
        store.save(AppConfig(version=5, poll_minutes=5, map_locked=False,
                             boxes=[BoxConfig(uid="good", title="Multitek",
                                              csv_path="")]))
        gw.sql("INSERT INTO lem_boxes (uid, data) VALUES ('hurt', '{not json')")
        cfg = store.load()
        assert [b.uid for b in cfg.boxes] == ["good"]
        cfg.boxes.append(BoxConfig(uid="new", title="OptiMPP", csv_path=""))
        store.save(cfg)
        rows = gw.read_sql("SELECT uid FROM lem_boxes").get("rows")
        assert {r["uid"] for r in rows} == {"good", "hurt", "new"}, \
            "the row nobody could parse was deleted by a save of something else"

    def test_it_is_still_reported(self):
        gw, store = self._store()
        gw.sql("INSERT INTO lem_boxes (uid, data) VALUES ('hurt', '{not json')")
        with _capture() as seen:
            store.load()
        assert any("hurt" in line for line in seen), \
            "a row was dropped from the config and nothing said so"

    def test_a_normal_save_still_prunes(self):
        from models import AppConfig, BoxConfig

        gw, store = self._store()
        store.save(AppConfig(version=5, poll_minutes=5, map_locked=False,
                             boxes=[BoxConfig(uid="a", title="A", csv_path=""),
                                    BoxConfig(uid="b", title="B", csv_path="")]))
        store.save(AppConfig(version=5, poll_minutes=5, map_locked=False,
                             boxes=[BoxConfig(uid="b", title="B", csv_path="")]))
        rows = gw.read_sql("SELECT uid FROM lem_boxes").get("rows")
        assert {r["uid"] for r in rows} == {"b"}


class _capture:
    """Collect log records emitted inside the block."""

    def __enter__(self):
        import logging
        self.lines = []
        self.handler = logging.Handler()
        self.handler.emit = lambda record: self.lines.append(record.getMessage())
        logging.getLogger("db_config_store").addHandler(self.handler)
        logging.getLogger("db_config_store").setLevel(logging.WARNING)
        return self.lines

    def __exit__(self, *exc):
        import logging
        logging.getLogger("db_config_store").removeHandler(self.handler)
        return False


# ── a CSV cannot carry a banner, so it carries a line ───────────────────────

class TestTheExportsSayWhenTheirNamesAreMissing:
    """Both CSV exports fall back to the uid (or a blank) when the machine list
    cannot be read, and reported it ONLY through `logger.warning` — which, on
    the target platform, went nowhere at all.

    Serving the file is right: the rows are the record and the names decorate
    it. But the file is what leaves the building, and a column of uids with no
    explanation is read as the lab's own labelling.
    """

    def test_the_log_export_says_it_in_the_file(self, lab):
        gw = Selective(lab, fail_read=lambda s: "lem_machine_status" in s)
        client, _app = _client(gw)
        res = client.get("/api/logs.csv")
        assert res.status_code == 200
        text = res.get_data(as_text=True)
        assert "timestamp,machine,kind" in text
        assert "m1" in text
        assert "machine names could not be read" in text.lower()

    def test_the_qc_export_says_it_too(self, lab):
        gw = Selective(lab, fail_read=lambda s: "lem_machine_status" in s)
        client, _app = _client(gw)
        text = client.get("/api/export/qc.csv").get_data(as_text=True)
        assert "machine names could not be read" in text.lower()

    def test_a_healthy_export_carries_no_note(self, lab):
        client, _app = _client(lab)
        text = client.get("/api/logs.csv").get_data(as_text=True)
        assert "could not be read" not in text.lower()
        assert "OptiMPP 1" in text

    def test_the_note_never_displaces_the_header(self, lab):
        """It goes at the END. A comment line above the header breaks every
        parser that reads the file by column name — which is the point of
        serving it at all."""
        gw = Selective(lab, fail_read=lambda s: "lem_machine_status" in s)
        client, _app = _client(gw)
        lines = [l for l in client.get("/api/logs.csv").get_data(
            as_text=True).splitlines() if l.strip()]
        assert lines[0].startswith("timestamp,machine,kind")
        assert "could not be read" in lines[-1].lower()
