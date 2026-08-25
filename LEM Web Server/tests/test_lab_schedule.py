"""When the lab is shut, silence is not a fault.

A module that stops checking in gets called `stopped` — correct on a Tuesday
afternoon, wrong at 3am, and wrong all weekend. Every Monday morning the floor
was covered in red for instruments that were behaving perfectly.

So the lab gets an opening schedule: which weekdays it runs, optional hours,
and a list of holidays. Outside those, a quiet module reads `closed` instead of
`stopped`. A module that IS checking in still reads `running` — a holiday
doesn't make a live module dead.
"""
from datetime import date, datetime

import pytest

from labcore_gateway import FakeLabCoreGateway
from lab_schedule import LabSchedule, LabScheduleStore


@pytest.fixture
def gw():
    return FakeLabCoreGateway()


@pytest.fixture
def store(gw):
    return LabScheduleStore(gw)


# ── the schedule itself ─────────────────────────────────────────────────────

class TestOpenAndClosed:
    def test_a_weekday_inside_hours_is_open(self):
        s = LabSchedule(working_days=[0, 1, 2, 3, 4], opens="07:00",
                        closes="18:00")
        assert s.is_open(datetime(2026, 8, 3, 9, 0)) is True   # Monday 09:00

    def test_saturday_is_closed_by_default(self):
        s = LabSchedule()
        assert s.is_open(datetime(2026, 8, 8, 9, 0)) is False  # Saturday

    def test_sunday_is_closed_by_default(self):
        s = LabSchedule()
        assert s.is_open(datetime(2026, 8, 9, 9, 0)) is False

    def test_before_opening_is_closed(self):
        s = LabSchedule(opens="07:00", closes="18:00")
        assert s.is_open(datetime(2026, 8, 3, 6, 30)) is False

    def test_after_closing_is_closed(self):
        s = LabSchedule(opens="07:00", closes="18:00")
        assert s.is_open(datetime(2026, 8, 3, 18, 30)) is False

    def test_blank_hours_mean_all_day(self):
        """A lab running three shifts sets no hours at all."""
        s = LabSchedule(opens="", closes="")
        assert s.is_open(datetime(2026, 8, 3, 3, 0)) is True

    def test_a_holiday_is_closed_even_on_a_working_day(self):
        s = LabSchedule(holidays={"2026-09-07": "Labor Day"})
        assert s.is_open(datetime(2026, 9, 7, 9, 0)) is False   # Monday

    def test_a_weekend_shift_can_be_opened(self):
        s = LabSchedule(working_days=[0, 1, 2, 3, 4, 5])
        assert s.is_open(datetime(2026, 8, 8, 9, 0)) is True    # Saturday

    def test_it_says_why_it_is_closed(self):
        s = LabSchedule(holidays={"2026-09-07": "Labor Day"})
        assert "Labor Day" in s.why_closed(datetime(2026, 9, 7, 9, 0))
        assert "weekend" in s.why_closed(datetime(2026, 8, 8, 9, 0)).lower()

    def test_an_open_moment_has_no_reason(self):
        assert LabSchedule().why_closed(datetime(2026, 8, 3, 9, 0)) == ""


# ── persistence ─────────────────────────────────────────────────────────────

class TestStore:
    def test_defaults_before_anything_is_saved(self, store):
        s = store.load()
        assert s.working_days == [0, 1, 2, 3, 4]
        assert s.holidays == {}

    def test_a_saved_schedule_round_trips(self, store):
        store.save(LabSchedule(working_days=[0, 1, 2], opens="08:00",
                               closes="16:30"))
        s = store.load()
        assert s.working_days == [0, 1, 2]
        assert s.opens == "08:00" and s.closes == "16:30"

    def test_saving_twice_replaces_rather_than_accumulates(self, store):
        store.save(LabSchedule(working_days=[0]))
        store.save(LabSchedule(working_days=[3, 4]))
        assert store.load().working_days == [3, 4]

    def test_holidays_round_trip(self, store):
        store.save(LabSchedule(holidays={"2026-12-25": "Christmas"}))
        assert store.load().holidays == {"2026-12-25": "Christmas"}

    def test_holidays_can_be_added_and_removed_one_at_a_time(self, store):
        store.add_holiday("2026-12-25", "Christmas")
        store.add_holiday("2026-07-04", "Independence Day")
        assert len(store.load().holidays) == 2
        store.remove_holiday("2026-07-04")
        assert list(store.load().holidays) == ["2026-12-25"]

    def test_a_bad_weekday_is_refused(self, store):
        with pytest.raises(ValueError):
            store.save(LabSchedule(working_days=[9]))

    def test_a_bad_time_is_refused(self, store):
        with pytest.raises(ValueError):
            store.save(LabSchedule(opens="half seven"))

    def test_a_bad_holiday_date_is_refused(self, store):
        with pytest.raises(ValueError):
            store.add_holiday("25/12/2026", "Christmas")

    def test_a_broken_backend_no_longer_pretends_it_is_monday_to_friday(self):
        """This test used to assert the opposite, and the assertion was the bug.

        `api_save_schedule` fills the fields the operator did not type from
        `load()`. So a `load()` that silently degrades means editing only the
        opening time also resets the working days to Mon-Fri and posts back a
        form with every holiday missing — a read deciding a write, served from
        "could not ask". It raises now.
        """
        class Dead:
            def sql(self, *a, **k):
                raise RuntimeError("LabCore down")

            def read_sql(self, *a, **k):
                return {"error": "LabCore down"}

        with pytest.raises(LabCoreError):
            LabScheduleStore(Dead()).load()

    def test_a_display_path_can_still_ask_for_the_default_by_name(self):
        """The true half of the old doctrine survives — as an opt-in. A wall
        display would rather draw a plausible week than an error; nothing that
        goes on to write may use this."""
        class Dead:
            def sql(self, *a, **k):
                raise RuntimeError("LabCore down")

            def read_sql(self, *a, **k):
                return {"error": "LabCore down"}

        s = LabScheduleStore(Dead()).load(degrade_to_default=True)
        assert s.working_days == [0, 1, 2, 3, 4]
        assert s.holidays == {}


# ── what it changes about a machine ─────────────────────────────────────────

class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


class TestModuleStateRespectsTheSchedule:
    @pytest.fixture
    def client(self, gw):
        from web_app import create_app
        app = create_app(gw, authenticator=StubAuth(), secret="s")
        app.config["TESTING"] = True
        return app.test_client()

    def seed_quiet_machine(self, gw, last_poll):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
               "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
               "reason TEXT, updated_at TEXT)")
        gw.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
               ["m1", "OptiMPP 1", "GREEN", "ok", last_poll])
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_heartbeat ("
               "machine_uid TEXT PRIMARY KEY, last_poll TEXT, watching TEXT)")
        gw.sql("INSERT INTO lem_machine_heartbeat VALUES (?,?,?)",
               ["m1", last_poll, "Cloud Point"])

    def test_a_quiet_module_is_closed_not_stopped_when_the_lab_is_shut(
            self, gw, client, monkeypatch):
        import web_app
        # Saturday morning; the module last beat on Friday evening.
        monkeypatch.setattr(web_app, "_now",
                            lambda: datetime(2026, 8, 8, 9, 0))
        self.seed_quiet_machine(gw, "2026-08-07T17:02:00")
        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["module_state"] == "closed"
        assert m["module_running"] is False
        assert "weekend" in m["closed_reason"].lower()

    def test_the_same_machine_is_stopped_on_a_working_day(
            self, gw, client, monkeypatch):
        import web_app
        monkeypatch.setattr(web_app, "_now",
                            lambda: datetime(2026, 8, 10, 9, 0))  # Monday
        self.seed_quiet_machine(gw, "2026-08-07T17:02:00")
        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["module_state"] == "stopped"

    def test_a_live_module_is_running_even_on_a_holiday(
            self, gw, client, monkeypatch):
        import web_app
        now = datetime(2026, 9, 7, 9, 0)
        monkeypatch.setattr(web_app, "_now", lambda: now)
        client.post("/api/login", json={"username": "k", "password": "good"})
        client.post("/api/schedule",
                    json={"holidays": {"2026-09-07": "Labor Day"}})
        self.seed_quiet_machine(gw, now.isoformat())
        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["module_state"] == "running"

    def test_a_module_that_never_beat_is_still_unknown_not_closed(
            self, gw, client, monkeypatch):
        """Never checking in is a different problem from a shut lab."""
        import web_app
        monkeypatch.setattr(web_app, "_now",
                            lambda: datetime(2026, 8, 8, 9, 0))
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
               "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
               "reason TEXT, updated_at TEXT)")
        gw.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
               ["m9", "Orphan", "UNKNOWN", "", "2026-07-01T10:00:00"])
        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["module_state"] == "unknown"


# ── the endpoint the settings UI uses ───────────────────────────────────────

class TestScheduleEndpoint:
    @pytest.fixture
    def client(self, gw):
        from web_app import create_app
        app = create_app(gw, authenticator=StubAuth(), secret="s")
        app.config["TESTING"] = True
        return app.test_client()

    def signed_in(self, client):
        client.post("/api/login", json={"username": "k", "password": "good"})
        return client

    def test_it_reports_the_current_schedule(self, client):
        body = client.get("/api/schedule").get_json()
        assert body["working_days"] == [0, 1, 2, 3, 4]
        assert "open_now" in body

    def test_saving_needs_an_account(self, client):
        r = client.post("/api/schedule", json={"working_days": [0]})
        assert r.status_code == 401

    def test_it_saves(self, client):
        self.signed_in(client)
        r = client.post("/api/schedule", json={"working_days": [0, 1, 2, 3, 4, 5],
                                               "opens": "06:00",
                                               "closes": "22:00"})
        assert r.status_code == 200
        body = client.get("/api/schedule").get_json()
        assert body["working_days"] == [0, 1, 2, 3, 4, 5]
        assert body["opens"] == "06:00"

    def test_it_rejects_nonsense(self, client):
        self.signed_in(client)
        r = client.post("/api/schedule", json={"opens": "elevenish"})
        assert r.status_code == 400
        assert r.get_json()["error"]

    def test_holidays_can_be_managed_over_http(self, client):
        self.signed_in(client)
        client.post("/api/holidays", json={"day": "2026-12-25",
                                          "name": "Christmas"})
        assert client.get("/api/schedule").get_json()["holidays"] == {
            "2026-12-25": "Christmas"}
        client.delete("/api/holidays/2026-12-25")
        assert client.get("/api/schedule").get_json()["holidays"] == {}


# ── the write queue says no, and the store must not say yes ─────────────────
#
# LabCore's queue refuses past ~100 pending BY ANSWERING: nothing raises, and
# the answer need not carry an "error" key. This file used to ask
# `if not res.get("error")` — true for the refusal below — across four writes.
# Refusing with {"error": ...} instead would test the one shape the old code
# already handled.

from labcore_result import LabCoreError, LabCoreUnavailable
from lab_schedule import ScheduleWriteError


REFUSAL = {"queued": False, "pending": 137}


class QueueFull:
    """A real gateway until it refuses. Reads keep working, so a test can show
    the stored schedule is exactly as it was before the refused write."""

    def __init__(self, refuse_after=None):
        self.real = FakeLabCoreGateway()
        self.refusing = False
        self.refuse_after = refuse_after
        self.writes = 0

    def refuse(self):
        self.refusing = True

    def allow(self):
        self.refusing = False
        self.refuse_after = None

    def sql(self, sql, args=None, **kw):
        self.writes += 1
        if self.refusing or (self.refuse_after is not None
                             and self.writes > self.refuse_after):
            return dict(REFUSAL)
        return self.real.sql(sql, args)

    def read_sql(self, sql, args=None, **kw):
        return self.real.read_sql(sql, args)


class Unreadable:
    def __init__(self):
        self.real = FakeLabCoreGateway()

    def sql(self, sql, args=None, **kw):
        return self.real.sql(sql, args)

    def read_sql(self, sql, args=None, **kw):
        return {"error": "HTTPSConnectionPool(...): Read timed out"}


class TestARefusedScheduleWriteIsNeverReportedAsSaved:
    def test_save_raises_and_the_old_hours_stand(self):
        """The symptom this whole module exists to remove: the panel says saved,
        the row is not there, and next Monday the floor is red again."""
        gw = QueueFull()
        store = LabScheduleStore(gw)
        store.save(LabSchedule(working_days=[0, 1, 2], opens="08:00",
                               closes="16:30"))
        gw.refuse()
        with pytest.raises(ScheduleWriteError):
            store.save(LabSchedule(working_days=[5, 6], opens="22:00",
                                   closes="23:00"))
        gw.allow()
        s = store.load()
        assert s.working_days == [0, 1, 2]
        assert s.opens == "08:00" and s.closes == "16:30"

    def test_a_refused_holiday_wipe_raises_instead_of_emptying_the_list(self):
        """Wipe-then-reinsert is not atomic and never was — LabCore's queue
        takes one statement at a time. What changed is that the failure is now
        visible instead of returning "ok" over an emptied holiday list, which
        reports the lab open on Christmas Day."""
        gw = QueueFull()
        store = LabScheduleStore(gw)
        store.save(LabSchedule(holidays={"2026-12-25": "Christmas"}))
        gw.refuse_after = gw.writes + 1        # the hours land, the wipe does not
        with pytest.raises(ScheduleWriteError):
            store.save(LabSchedule(
                working_days=[0], holidays={"2026-07-04": "Independence Day"}))
        gw.allow()
        assert store.load().holidays == {"2026-12-25": "Christmas"}

    def test_add_holiday_raises_and_stores_nothing(self):
        gw = QueueFull()
        store = LabScheduleStore(gw)
        store.ensure_schema()
        gw.refuse()
        with pytest.raises(ScheduleWriteError):
            store.add_holiday("2026-12-25", "Christmas")
        gw.allow()
        assert store.load().holidays == {}

    def test_remove_holiday_raises_and_the_holiday_survives(self):
        gw = QueueFull()
        store = LabScheduleStore(gw)
        store.add_holiday("2026-12-25", "Christmas")
        gw.refuse()
        with pytest.raises(ScheduleWriteError):
            store.remove_holiday("2026-12-25")
        gw.allow()
        assert store.load().holidays == {"2026-12-25": "Christmas"}

    def test_a_refused_create_table_raises_rather_than_latching_ready(self):
        """The old body was `except Exception: pass` with `_ready` set inside
        the try, so a refusal was retried forever while `save()` INSERTed into
        tables that might not exist and said it had worked."""
        gw = QueueFull()
        gw.refuse()
        store = LabScheduleStore(gw)
        with pytest.raises(ScheduleWriteError):
            store.ensure_schema()
        assert store._ready is False

    def test_the_store_recovers_once_the_queue_drains(self):
        gw = QueueFull()
        gw.refuse()
        store = LabScheduleStore(gw)
        with pytest.raises(ScheduleWriteError):
            store.save(LabSchedule(working_days=[0]))
        gw.allow()
        store.save(LabSchedule(working_days=[0]))
        assert store.load().working_days == [0]

    def test_the_refusal_is_catchable_as_one_labcore_error(self):
        gw = QueueFull()
        gw.refuse()
        with pytest.raises(LabCoreError):
            LabScheduleStore(gw).add_holiday("2026-12-25", "Christmas")


class TestCouldNotAskIsNotMondayToFriday:
    def test_load_raises_on_an_unreadable_labcore(self):
        with pytest.raises(LabCoreUnavailable):
            LabScheduleStore(Unreadable()).load()

    def test_the_holidays_read_is_not_allowed_to_vanish_on_its_own(self):
        """The hours row can be read and the holidays row cannot: the old code
        returned the hours with an empty holiday list, so a lab with real
        working hours read as having no holidays at all."""
        class HalfReadable(Unreadable):
            def read_sql(self, sql, args=None, **kw):
                if "lem_lab_holidays" in sql:
                    return {"error": "Read timed out"}
                return self.real.read_sql(sql, args)

        gw = HalfReadable()
        LabScheduleStore(gw.real).save(
            LabSchedule(working_days=[0], holidays={"2026-12-25": "Christmas"}))
        with pytest.raises(LabCoreUnavailable):
            LabScheduleStore(gw).load()
