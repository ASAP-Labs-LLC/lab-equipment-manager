#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The gaps three critics found in `fix/confirm-every-write`.

Each of these is the same failure the branch exists to remove, in a place the
conversion missed: an answer nobody read, or a "could not ask" served to the
operator as a fact about the lab.

  F  /api/machine-configs/<uid> caught only `ConfigReadUnavailable`, so any
     other failure from `get()` — including a transport error raised out of
     `read_sql` — escaped as a bare 500. "Internal Server Error" tells nobody
     whether the configuration exists.

  G  /api/maintenance answered 200 with `{"tasks": [], "due_count": 0}` when
     LabCore could not be reached, and the page renders that as "Nothing
     scheduled anywhere". Its per-machine sibling already 503s. A lab told it
     has no PM due because a queue was busy is the same class of lie as a
     write reported as saved.

  the minors: /api/schedule degrading to a silent default week; the log CSV
  refusing a whole download when only the machine NAMES were unreadable; and
  the last holiday never being deleted.
"""
import pytest

from labcore_gateway import FakeLabCoreGateway
from web_app import create_app

BUSY = {"error": "LabCore is busy, try again later", "busy": True,
        "retry_after": 4}
BLIP = {"error": "HTTPSConnectionPool(host='labvision'): Read timed out"}

UID = "m1"


class StubAuth:
    def login(self, username, password):
        return ("kaden", "tok", "")

    def logout(self, token):
        pass


def _client(gateway):
    app = create_app(gateway, authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/api/login", json={"username": "kaden", "password": "p"})
    return c, app


class Selective:
    """Reads and writes pass through unless `fail_read`/`fail_write` says so.

    `raise_read` makes the chosen reads THROW rather than answer, which is the
    case the routes were missing: `labcore_result` converts an ANSWER, and a
    client that raises never produces one.
    """

    def __init__(self, real, fail_read=lambda s: False, raise_read=False,
                 fail_write=lambda s: False):
        self.real = real
        self.fail_read = fail_read
        self.raise_read = raise_read
        self.fail_write = fail_write
        self.wrote = []

    def sql(self, sql, args=None, **kw):
        self.wrote.append((sql, list(args or [])))
        if self.fail_write(sql):
            return dict(BUSY)
        return self.real.sql(sql, args, **kw)

    def read_sql(self, sql, args=None, **kw):
        if self.fail_read(sql):
            if self.raise_read:
                raise OSError("connection reset by peer")
            return dict(BLIP)
        return self.real.read_sql(sql, args, **kw)

    def write(self, operation, params, **kw):
        return self.real.write(operation, params, **kw)

    def is_running(self):
        return True

    def get_test_names(self):
        return self.real.get_test_names()

    def get_samples(self, **kw):
        return self.real.get_samples(**kw)


@pytest.fixture
def lab():
    gw = FakeLabCoreGateway()
    healthy, _app = _client(gw)
    from snapshot_service import SnapshotService
    SnapshotService(gw).ensure_schema()
    gw.sql("INSERT INTO lem_machine_status (machine_uid, title, status, "
           "reason, updated_at) VALUES (?, 'Multitek NS', 'GREEN', '', ?)",
           [UID, "2026-08-20T09:00:00"])
    healthy.post(f"/api/machine-configs/{UID}",
                 json={"title": "Multitek NS", "config": {"uid": UID}})
    healthy.post(f"/api/machines/{UID}/maintenance", json={
        "name": "Annual calibration", "kind": "calibration",
        "interval_days": 365, "last_done": "2026-01-05"})
    return gw


# ── F ────────────────────────────────────────────────────────────────────────
class TestOneMachinesConfigurationNeverAnswers500:
    def test_a_raised_transport_error_is_not_a_bare_500(self, lab):
        """`get()` reads through `gateway.read_sql(...)` with nothing around
        the CALL, so a client that raises went straight past every
        `except MachineConfigError` in the route."""
        gw = Selective(lab, fail_read=lambda s: "lem_machine_config" in s,
                       raise_read=True)
        client, _app = _client(gw)
        res = client.get(f"/api/machine-configs/{UID}")
        assert res.status_code in (502, 503), (
            "a bare 500 tells the module nothing about whether its "
            "configuration exists")
        assert res.get_json().get("retry") is True

    def test_it_is_still_not_a_404(self, lab):
        """THE 404 TRAP, restated for the raised case. A module that has been
        parsing all morning must never be told it was never configured — it
        would offer to make a second configuration."""
        gw = Selective(lab, fail_read=lambda s: "lem_machine_config" in s,
                       raise_read=True)
        client, _app = _client(gw)
        assert client.get(f"/api/machine-configs/{UID}").status_code != 404

    def test_any_store_failure_is_answered_in_words(self, lab, monkeypatch):
        """The route's contract with its store, pinned directly.

        `except ConfigReadUnavailable` named ONE of the store's three error
        types, so anything else `get()` could raise fell through to a bare 500.
        Today `get()` declares no schema, so `ConfigWriteRefused` is not
        reachable from it — which is exactly why this is worth a test rather
        than an assumption: the narrow catch was correct-by-accident, and the
        next person to put a write back on this read path would restore the
        500 with nothing failing.

        (Checked: reverting the route to `except ConfigReadUnavailable` leaves
        every other test in this file green. This is the one that goes red.)
        """
        from machine_configs import ConfigWriteRefused, MachineConfigStore

        def boom(self, machine_uid):
            raise ConfigWriteRefused("creating lem_machine_config — not saved")

        monkeypatch.setattr(MachineConfigStore, "get", boom)
        client, _app = _client(lab)
        res = client.get(f"/api/machine-configs/{UID}")
        assert res.status_code in (502, 503)
        assert res.get_json().get("error")

    def test_a_configuration_that_really_is_absent_is_still_a_404(self, lab):
        client, _app = _client(lab)
        assert client.get("/api/machine-configs/nobody").status_code == 404


# ── G ────────────────────────────────────────────────────────────────────────
class TestNothingScheduledAnywhereIsNeverInvented:
    def test_an_unreadable_fleet_schedule_is_not_an_empty_one(self, lab):
        gw = Selective(lab, fail_read=lambda s: "lem_maintenance" in s)
        client, _app = _client(gw)
        res = client.get("/api/maintenance")
        assert res.status_code in (502, 503), (
            'the maintenance page renders {"tasks": [], "due_count": 0} as '
            '"Nothing scheduled anywhere"')
        assert "tasks" not in res.get_json()

    def test_it_matches_its_per_machine_sibling(self, lab):
        """The inconsistency the critics named: two routes reading the same
        table, one honest and one not."""
        gw = Selective(lab, fail_read=lambda s: "lem_maintenance" in s)
        client, _app = _client(gw)
        assert (client.get("/api/maintenance").status_code
                == client.get(f"/api/machines/{UID}/maintenance").status_code)

    def test_a_lab_with_genuinely_no_pm_still_answers_200(self):
        """The fix must not turn "nothing scheduled" into an error. A fresh lab
        really has no PM, and that is a 200 with an empty list."""
        gw = FakeLabCoreGateway()
        from snapshot_service import SnapshotService
        SnapshotService(gw).ensure_schema()
        client, _app = _client(gw)
        res = client.get("/api/maintenance")
        assert res.status_code == 200
        body = res.get_json()
        assert body["tasks"] == [] and body["due_count"] == 0

    def test_the_tasks_are_still_there_when_LabCore_answers(self, lab):
        client, _app = _client(lab)
        body = client.get("/api/maintenance").get_json()
        assert [t["name"] for t in body["tasks"]] == ["Annual calibration"]
        assert body["machines_named"] is not False

    def test_unreadable_NAMES_do_not_withhold_the_schedule(self, lab):
        """The sibling rule, applied consistently: a name decorates, the task
        is the record. A PM list labelled by uid is ugly and complete; refusing
        to serve it would withhold the data over its decoration.

        But it must SAY so, or a row reading "m1" looks like a machine called
        m1 rather than one whose name could not be read.
        """
        gw = Selective(lab, fail_read=lambda s: "lem_machine_status" in s)
        client, _app = _client(gw)
        res = client.get("/api/maintenance")
        assert res.status_code == 200
        body = res.get_json()
        assert [t["name"] for t in body["tasks"]] == ["Annual calibration"]
        assert body["machines_named"] is False


# ── minors ───────────────────────────────────────────────────────────────────
class TestTheScheduleSaysWhenItIsGuessing:
    def test_a_degraded_week_is_flagged(self, lab):
        """`/api/map` already ships `known: false` for exactly this. The
        schedule degraded to Mon–Fri with nothing to say it was a fallback, so
        a lab that works Saturdays would see its own hours quietly wrong and
        every silent module on a Saturday reported `closed`."""
        gw = Selective(lab, fail_read=lambda s: "lem_lab_schedule" in s
                       or "lem_lab_holidays" in s)
        client, _app = _client(gw)
        body = client.get("/api/schedule").get_json()
        assert body.get("known") is False
        assert body.get("working_days") == [0, 1, 2, 3, 4]

    def test_a_real_answer_is_flagged_known(self, lab):
        client, _app = _client(lab)
        assert client.get("/api/schedule").get_json().get("known") is True


class TestTheLogExportFollowsTheRuleTheQcExportFollows:
    def test_unreadable_NAMES_do_not_refuse_the_download(self, lab):
        """`_log_entries` folded the titles failure into the same `failed` flag
        as the log read, and the CSV refuses on that flag — so a blip on the
        machine LIST withheld the whole history. /api/export/qc.csv, reading
        the same log, serves the file with a blank name column.

        Two rules for one question in one file is how this branch's bugs
        started."""
        gw = Selective(lab, fail_read=lambda s: "lem_machine_status" in s)
        client, _app = _client(gw)
        res = client.get("/api/logs.csv")
        assert res.status_code == 200, (
            "the QC export serves the record with an unnamed machine column; "
            "the log export must not withhold it")
        assert "timestamp,machine,kind" in res.get_data(as_text=True)

    def test_an_unreadable_LOG_still_refuses_the_download(self, lab):
        """The half that must not move. A CSV cannot carry a banner, and a
        download with a header row and nothing under it leaves the building and
        gets filed as the lab's history."""
        gw = Selective(lab, fail_read=lambda s: "lem_machine_log" in s)
        client, _app = _client(gw)
        assert client.get("/api/logs.csv").status_code == 503

    def test_the_json_log_still_reports_missing_names(self, lab):
        """The page CAN carry a banner, so it still gets one — the export is
        the only place the rule differs."""
        gw = Selective(lab, fail_read=lambda s: "lem_machine_status" in s)
        client, _app = _client(gw)
        body = client.get("/api/logs").get_json()
        assert body.get("error")


class TestRemovingTheLastHolidayActuallyRemovesIt:
    def test_saving_an_empty_holiday_list_clears_the_table(self, lab):
        """`save()` guarded the wipe with `if schedule.holidays:`, so clearing
        the last one issued no DELETE at all and the lab stayed shut on a day
        it was open — silently, since the POST answered ok."""
        client, _app = _client(lab)
        assert client.post("/api/holidays",
                           json={"day": "2026-12-25",
                                 "name": "Christmas"}).status_code == 200
        assert client.get("/api/schedule").get_json()["holidays"]

        res = client.post("/api/schedule", json={
            "working_days": [0, 1, 2, 3, 4], "opens": "", "closes": "",
            "holidays": {}})
        assert res.status_code == 200
        assert client.get("/api/schedule").get_json()["holidays"] == {}

    def test_a_refused_wipe_is_still_reported(self, lab):
        """Now that the DELETE always runs, it always has to be confirmed."""
        client, _app = _client(lab)
        client.post("/api/holidays",
                    json={"day": "2026-12-25", "name": "Christmas"})
        gw = Selective(lab, fail_write=lambda s: "DELETE FROM lem_lab_holidays"
                       in s)
        blocked, _app2 = _client(gw)
        res = blocked.post("/api/schedule", json={
            "working_days": [0, 1, 2, 3, 4], "opens": "", "closes": "",
            "holidays": {}})
        assert res.status_code in (502, 503)
        assert client.get("/api/schedule").get_json()["holidays"]


class TestAWriteThatTHREWIsAlsoAWriteThatDidNotHappen:
    """The last minor, and the one most likely to come back.

    `machine_map`, `machine_configs`, `qc_specs` and `qc_samples` all wrote as
    `confirm_write(self.gateway.sql(...))`. That reads the ANSWER — and a
    client that RAISES never produces one, so a socket error, a DNS failure or
    a client bug went straight past every `except MapWriteRefused` /
    `except QcSpecStoreError` in web_app and surfaced as a bare 500.

    Nothing about that is more forgiving than a queue refusal: the row is
    equally not written. But the operator is told "Internal Server Error"
    instead of "the QC band was NOT saved — try again", and the two send them
    to completely different places. `checklists`, `lab_schedule` and
    `maintenance_store` already had `_write` helpers converting a raised
    transport error into the store's own type; these four did not, so one
    question had two answers depending on which store you happened to be in.
    """

    class Broken:
        """Reads work; every WRITE raises the way a dead socket does."""

        def __init__(self, real):
            self.real = real

        def sql(self, sql, args=None, **kw):
            raise OSError("connection reset by peer")

        def read_sql(self, sql, args=None, **kw):
            return self.real.read_sql(sql, args, **kw)

        def is_running(self):
            return True

        def get_test_names(self):
            return self.real.get_test_names()

        def get_samples(self, **kw):
            return self.real.get_samples(**kw)

    WRITES = [
        ("moving an instrument on the floor",
         f"/api/machines/{UID}/position", {"x": 3.0, "y": 1.0}),
        ("assigning QC to an instrument",
         f"/api/machines/{UID}/qc-targets",
         {"targets": [{"sample": "Diesel - AO25", "test": "Flash Point"}]}),
        ("saving a QC band", "/api/qc-specs",
         {"machine_uid": UID, "test_name": "Flash Point", "sample_id": "STD-1",
          "expected": 63.72, "std_dev": 1.05, "k": 2.0, "units": "°C"}),
        ("saving a QC standard", "/api/qc-samples",
         {"name": "Diesel - AO25", "sample_id_val": "STD-1", "tests": []}),
        ("saving a machine configuration", f"/api/machine-configs/{UID}",
         {"title": "Multitek NS", "config": {"uid": UID}}),
        ("locking the map", "/api/map", {"locked": True}),
    ]

    @pytest.mark.parametrize("what,url,body", WRITES,
                             ids=[w[0] for w in WRITES])
    def test_it_is_reported_in_words_not_as_a_500(self, lab, what, url, body):
        client, _app = _client(self.Broken(lab))
        res = client.post(url, json=body)
        assert res.status_code in (502, 503), (
            f"{what} answered {res.status_code}; 'Internal Server Error' does "
            f"not tell an operator whether to try again")
        assert res.get_json().get("error"), "and it has to say what was lost"


class TestTheWritesTheROUTEIssuesItself:
    """The same defect, in the writes web_app does NOT hand to a store.

    `TestAWriteThatTHREWIsAlsoAWriteThatDidNotHappen` above fixed the four
    stores. It could not reach these, because they are `confirm_write(
    gateway.sql(...))` written inline in the route: the ANSWER is judged and
    the CALL is bare, so a socket error, a DNS failure or a proxy 502 sails
    past `except LabCoreError` and out as "Internal Server Error".

    Where that lands matters more than usual:

      * the CORRECTION FACTOR is added to every measurement the bench reports,
        not only its QC. ISO/IEC 17025 §7.8.2 makes it a claim about every
        result. "Internal Server Error" leaves the operator unable to tell
        whether the lab is now correcting or still reporting raw;
      * retiring a machine is seven deletes, and the route's whole design is to
        stop at the first refusal and say how far it got. A RAISED failure skips
        that and reports nothing, leaving a half-retired machine with no
        instruction;
      * a completion is already rescheduled by the time its history line is
        written, so a 500 there tells the operator the completion failed when
        it did not.
    """

    @pytest.fixture
    def seeded(self, lab):
        """The lab, plus the rows these routes need to have something to lose."""
        client, _app = _client(lab)
        client.post(f"/api/machines/{UID}/corrections",
                    json={"test_name": "Flash Point", "correction": -3.0})
        return lab

    def _task_uid(self, gateway):
        client, _app = _client(gateway)
        body = client.get(f"/api/machines/{UID}/maintenance").get_json()
        return body["tasks"][0]["uid"]

    def test_the_correction_factor_is_not_a_bare_500(self, seeded):
        client, _app = _client(TestAWriteThatTHREWIsAlsoAWriteThatDidNotHappen
                               .Broken(seeded))
        res = client.post(f"/api/machines/{UID}/corrections",
                          json={"test_name": "Flash Point", "correction": -4.0})
        assert res.status_code in (502, 503), res.status_code
        body = res.get_json()
        assert body.get("saved") is False
        assert "correction" in body["error"].lower()

    def test_removing_a_correction_is_not_a_bare_500(self, seeded):
        client, _app = _client(TestAWriteThatTHREWIsAlsoAWriteThatDidNotHappen
                               .Broken(seeded))
        res = client.delete(f"/api/machines/{UID}/corrections/Flash Point")
        assert res.status_code in (502, 503), res.status_code
        assert res.get_json().get("saved") is False

    def test_retiring_a_machine_is_not_a_bare_500(self, seeded):
        client, _app = _client(TestAWriteThatTHREWIsAlsoAWriteThatDidNotHappen
                               .Broken(seeded))
        res = client.delete(f"/api/machines/{UID}")
        assert res.status_code in (502, 503), res.status_code
        body = res.get_json()
        assert body.get("complete") is False, (
            "a half-retired machine has to say where it stopped")

    def test_a_completion_whose_history_line_THREW_says_so_and_stays_done(
            self, seeded):
        """The reschedule has already happened and cannot be taken back.

        The route knows that — it answers 200 with `logged: false` when the log
        write is REFUSED. A raised one has to reach the same place, or the
        operator is told the completion failed while LabCore has it marked done
        and no longer due.
        """
        uid = self._task_uid(seeded)

        class BreaksOnlyTheLog:
            def __init__(self, real):
                self.real = real

            def sql(self, sql, args=None, **kw):
                if "lem_machine_log" in sql:
                    raise OSError("connection reset by peer")
                return self.real.sql(sql, args, **kw)

            def read_sql(self, sql, args=None, **kw):
                return self.real.read_sql(sql, args, **kw)

            def is_running(self):
                return True

            def get_test_names(self):
                return self.real.get_test_names()

            def get_samples(self, **kw):
                return self.real.get_samples(**kw)

        client, _app = _client(BreaksOnlyTheLog(seeded))
        res = client.post(f"/api/maintenance/{uid}/complete",
                          json={"when": "2026-08-20", "note": "done"})
        assert res.status_code == 200, res.status_code
        body = res.get_json()
        assert body["ok"] is True and body["logged"] is False
        assert body.get("warning")

    def test_an_import_that_THREW_reports_how_far_it_got(self, seeded):
        """It already does this for a refusal, down to "run it again — what
        already landed is skipped". A raise threw all of that away."""
        client, _app = _client(TestAWriteThatTHREWIsAlsoAWriteThatDidNotHappen
                               .Broken(seeded))
        csv = ("equipment,task,kind,completed_date,performed_by,note\n"
               "Multitek NS,Annual calibration,calibration,2026-05-02,sam,x\n")
        res = client.post("/api/maintenance-import", json={"csv": csv})
        assert res.status_code in (502, 503), res.status_code
        body = res.get_json()
        assert body.get("incomplete") is True
        assert body.get("created") == 0
