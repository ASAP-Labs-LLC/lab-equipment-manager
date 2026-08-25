"""Opening and closing rounds — the workflow V5 never rebuilt.

V4's model (V4.0.3.1 - Beta Stable/models.py) is the reference: a checklist has
a name and a due time; items are scoped to weekdays, can be headers or subtasks,
and ticking a parent ticks its children. Every tick records who and when, and
each day's state stands alone so yesterday's round doesn't carry over.

What V4 kept in `checklist_state.json` on one PC now lives in LabCore, so every
screen in the lab sees the same round.
"""
from datetime import date, datetime

import pytest

from checklists import (Checklist, ChecklistItem, ChecklistStore,
                        active_items, completion)
from labcore_gateway import FakeLabCoreGateway


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


@pytest.fixture
def gw():
    return FakeLabCoreGateway()


@pytest.fixture
def store(gw):
    return ChecklistStore(gw)


@pytest.fixture
def client(gw):
    from web_app import create_app
    app = create_app(gw, authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def signed_in(client):
    client.post("/api/login", json={"username": "k", "password": "good"})
    return client


def a_list(**over):
    base = dict(uid="c1", name="Opening round", slot="opening",
                due_time="09:00",
                items=[ChecklistItem(uid="i1", text="Check nitrogen"),
                       ChecklistItem(uid="i2", text="Run Cloud CRM")])
    base.update(over)
    return Checklist(**base)


# ── the model ───────────────────────────────────────────────────────────────

class TestWeekdayScoping:
    def test_a_weekday_item_shows_on_a_weekday(self):
        item = ChecklistItem(uid="i1", text="x", days_active=[0, 1, 2, 3, 4])
        # 2026-08-03 is a Monday.
        assert [i.uid for i in active_items([item], date(2026, 8, 3))] == ["i1"]

    def test_it_is_hidden_at_the_weekend(self):
        item = ChecklistItem(uid="i1", text="x", days_active=[0, 1, 2, 3, 4])
        assert active_items([item], date(2026, 8, 8)) == []

    def test_a_weekend_item_shows_at_the_weekend(self):
        item = ChecklistItem(uid="i1", text="x", days_active=[5, 6])
        assert len(active_items([item], date(2026, 8, 8))) == 1

    def test_an_item_with_no_days_shows_every_day(self):
        """Empty means "always", not "never" — never is what deleting is for."""
        item = ChecklistItem(uid="i1", text="x", days_active=[])
        assert len(active_items([item], date(2026, 8, 8))) == 1

    def test_headers_always_show(self):
        """A header scoped off would orphan the items beneath it."""
        head = ChecklistItem(uid="h", text="Gas", item_type="header",
                             days_active=[0])
        assert len(active_items([head], date(2026, 8, 8))) == 1


class TestCompletion:
    def test_nothing_ticked_is_zero(self):
        items = [ChecklistItem(uid="i1", text="a"),
                 ChecklistItem(uid="i2", text="b")]
        assert completion(items, {}) == (0, 2, 0)

    def test_all_ticked_is_a_hundred(self):
        items = [ChecklistItem(uid="i1", text="a"),
                 ChecklistItem(uid="i2", text="b")]
        state = {"i1": {"checked": True}, "i2": {"checked": True}}
        assert completion(items, state) == (2, 2, 100)

    def test_headers_do_not_count_towards_the_total(self):
        """A heading isn't work; counting it makes a finished round read 80%."""
        items = [ChecklistItem(uid="h", text="Gas", item_type="header"),
                 ChecklistItem(uid="i1", text="a")]
        assert completion(items, {"i1": {"checked": True}}) == (1, 1, 100)

    def test_an_unticked_entry_is_not_counted_as_done(self):
        items = [ChecklistItem(uid="i1", text="a")]
        assert completion(items, {"i1": {"checked": False}}) == (0, 1, 0)

    def test_an_empty_list_does_not_divide_by_zero(self):
        assert completion([], {}) == (0, 0, 0)


# ── persistence ─────────────────────────────────────────────────────────────

class TestStore:
    def test_nothing_saved_is_no_checklists(self, store):
        assert store.all() == []

    def test_a_checklist_round_trips(self, store):
        store.save(a_list())
        got = store.all()[0]
        assert got.name == "Opening round" and got.due_time == "09:00"
        assert [i.text for i in got.items] == ["Check nitrogen",
                                               "Run Cloud CRM"]

    def test_saving_twice_replaces_rather_than_duplicates(self, store):
        store.save(a_list())
        store.save(a_list(name="Opening round v2"))
        assert len(store.all()) == 1
        assert store.all()[0].name == "Opening round v2"

    def test_a_checklist_needs_a_name(self, store):
        with pytest.raises(ValueError):
            store.save(a_list(name="  "))

    def test_a_bad_due_time_is_refused(self, store):
        with pytest.raises(ValueError):
            store.save(a_list(due_time="half nine"))

    def test_a_blank_due_time_is_allowed(self, store):
        """Not every round has a deadline."""
        store.save(a_list(due_time=""))
        assert store.all()[0].due_time == ""

    def test_items_get_uids_if_missing(self, store):
        store.save(a_list(items=[ChecklistItem(uid="", text="No uid")]))
        assert store.all()[0].items[0].uid

    def test_delete_removes_it(self, store):
        store.save(a_list())
        store.delete("c1")
        assert store.all() == []

    def test_a_corrupt_items_blob_yields_an_empty_list_not_a_crash(self, store,
                                                                  gw):
        store.save(a_list())
        gw.sql("UPDATE lem_checklist_defs SET items = '{bad' WHERE uid = 'c1'")
        assert store.all()[0].items == []

    def test_a_broken_backend_is_an_error_rather_than_no_checklists(self):
        """This test used to assert the opposite, and the assertion was the bug.

        Nobody looking at a round with no items concludes LabCore is down; they
        conclude the round is done or was never set up, and the closing checks
        do not get run. `get()` is built on this too, and `get()` is what the
        tick route uses to answer "no such checklist" — a read deciding a write.
        """
        class Dead:
            def sql(self, *a, **k):
                raise RuntimeError("down")

            def read_sql(self, *a, **k):
                return {"error": "down"}

        with pytest.raises(LabCoreError):
            ChecklistStore(Dead()).all()


class TestDailyState:
    def test_ticking_records_who_and_when(self, store):
        store.save(a_list())
        store.set_tick("c1", "i1", True, "2026-08-03", "kaden")
        state = store.state("2026-08-03")["c1"]
        assert state["i1"]["checked"] is True
        assert state["i1"]["user"] == "kaden"
        assert state["i1"]["at"]

    def test_unticking_works(self, store):
        store.save(a_list())
        store.set_tick("c1", "i1", True, "2026-08-03", "kaden")
        store.set_tick("c1", "i1", False, "2026-08-03", "sam")
        assert store.state("2026-08-03")["c1"]["i1"]["checked"] is False

    def test_each_day_stands_alone(self, store):
        """Yesterday's round must not carry over."""
        store.save(a_list())
        store.set_tick("c1", "i1", True, "2026-08-03", "kaden")
        assert store.state("2026-08-04") == {}

    def test_a_day_with_no_ticks_is_empty(self, store):
        store.save(a_list())
        assert store.state("2026-08-03") == {}

    def test_history_reports_completion_per_day(self, store):
        store.save(a_list())
        store.set_tick("c1", "i1", True, "2026-08-03", "kaden")
        store.set_tick("c1", "i2", True, "2026-08-03", "kaden")
        days = {d["day"]: d for d in store.history()}
        assert days["2026-08-03"]["checked"] == 2


# ── over HTTP ───────────────────────────────────────────────────────────────

class TestEndpoints:
    def test_reading_needs_no_account(self, client):
        r = client.get("/api/checklists")
        assert r.status_code == 200 and r.get_json()["checklists"] == []

    def test_a_saved_checklist_comes_back_with_its_items(self, signed_in):
        signed_in.post("/api/checklists", json={
            "uid": "c1", "name": "Opening round", "slot": "opening",
            "due_time": "09:00",
            "items": [{"uid": "i1", "text": "Check nitrogen"}]})
        body = signed_in.get("/api/checklists").get_json()
        assert body["checklists"][0]["name"] == "Opening round"
        assert body["checklists"][0]["items"][0]["text"] == "Check nitrogen"

    def test_it_reports_the_day_it_is_showing(self, client):
        assert client.get("/api/checklists").get_json()["day"]

    def test_saving_needs_an_account(self, client):
        r = client.post("/api/checklists", json={"name": "x", "items": []})
        assert r.status_code == 401

    def test_a_nameless_checklist_is_refused(self, signed_in):
        r = signed_in.post("/api/checklists", json={"name": "", "items": []})
        assert r.status_code == 400 and r.get_json()["error"]

    def test_ticking_needs_an_account(self, client, gw):
        ChecklistStore(gw).save(a_list())
        r = client.post("/api/checklists/c1/toggle",
                        json={"item_uid": "i1", "checked": True})
        assert r.status_code == 401

    def test_ticking_over_http(self, gw, signed_in):
        ChecklistStore(gw).save(a_list())
        r = signed_in.post("/api/checklists/c1/toggle",
                           json={"item_uid": "i1", "checked": True})
        assert r.status_code == 200
        state = signed_in.get("/api/checklists").get_json()["state"]
        assert state["c1"]["i1"]["checked"] is True
        assert state["c1"]["i1"]["user"] == "kaden"

    def test_ticking_a_parent_ticks_its_children(self, gw, signed_in):
        """V4 did this, and a parent left half-ticked reads as unfinished."""
        ChecklistStore(gw).save(a_list(items=[
            ChecklistItem(uid="p", text="Gas checks"),
            ChecklistItem(uid="s1", text="Nitrogen", item_type="subtask",
                          parent_uid="p"),
            ChecklistItem(uid="s2", text="Helium", item_type="subtask",
                          parent_uid="p")]))
        signed_in.post("/api/checklists/c1/toggle",
                       json={"item_uid": "p", "checked": True})
        state = signed_in.get("/api/checklists").get_json()["state"]["c1"]
        assert state["s1"]["checked"] is True
        assert state["s2"]["checked"] is True

    def test_unticking_a_parent_unticks_its_children(self, gw, signed_in):
        ChecklistStore(gw).save(a_list(items=[
            ChecklistItem(uid="p", text="Gas checks"),
            ChecklistItem(uid="s1", text="Nitrogen", item_type="subtask",
                          parent_uid="p")]))
        signed_in.post("/api/checklists/c1/toggle",
                       json={"item_uid": "p", "checked": True})
        signed_in.post("/api/checklists/c1/toggle",
                       json={"item_uid": "p", "checked": False})
        state = signed_in.get("/api/checklists").get_json()["state"]["c1"]
        assert state["s1"]["checked"] is False

    def test_ticking_an_unknown_checklist_is_a_404(self, signed_in):
        r = signed_in.post("/api/checklists/ghost/toggle",
                           json={"item_uid": "i1", "checked": True})
        assert r.status_code == 404

    def test_deleting_needs_an_account(self, client, gw):
        ChecklistStore(gw).save(a_list())
        assert client.delete("/api/checklists/c1").status_code == 401

    def test_deleting_over_http(self, gw, signed_in):
        ChecklistStore(gw).save(a_list())
        assert signed_in.delete("/api/checklists/c1").status_code == 200
        assert signed_in.get("/api/checklists").get_json()["checklists"] == []

    def test_progress_comes_back_per_checklist(self, gw, signed_in):
        ChecklistStore(gw).save(a_list())
        signed_in.post("/api/checklists/c1/toggle",
                       json={"item_uid": "i1", "checked": True})
        cl = signed_in.get("/api/checklists").get_json()["checklists"][0]
        assert cl["checked"] == 1 and cl["total"] == 2 and cl["pct"] == 50

    def test_a_specific_day_can_be_asked_for(self, gw, signed_in):
        ChecklistStore(gw).save(a_list())
        signed_in.post("/api/checklists/c1/toggle",
                       json={"item_uid": "i1", "checked": True})
        body = signed_in.get("/api/checklists?day=1999-01-01").get_json()
        assert body["state"] == {}

    def test_history_is_exposed(self, gw, signed_in):
        ChecklistStore(gw).save(a_list())
        signed_in.post("/api/checklists/c1/toggle",
                       json={"item_uid": "i1", "checked": True})
        body = signed_in.get("/api/checklists/history").get_json()
        assert body["days"] and body["days"][0]["checked"] == 1

    def test_changes_are_audited(self, signed_in):
        signed_in.post("/api/checklists", json={
            "uid": "c1", "name": "Opening round", "items": []})
        entries = signed_in.get("/api/logs?kind=config").get_json()["events"]
        assert any("checklist" in e["action"] for e in entries)


# ── the page ────────────────────────────────────────────────────────────────

class TestThePage:
    def test_it_no_longer_claims_to_be_unbuilt(self, client):
        body = client.get("/checklists").get_data(as_text=True).lower()
        assert "not been implemented" not in body

    def test_it_has_an_opening_and_a_closing_slot(self, client):
        body = client.get("/checklists").get_data(as_text=True)
        assert 'data-slot="opening"' in body
        assert 'data-slot="closing"' in body

    def test_it_reads_the_endpoint(self, client):
        assert "/api/checklists" in client.get("/checklists").get_data(
            as_text=True)

    def test_it_can_get_back(self, client):
        assert 'href="/"' in client.get("/checklists").get_data(as_text=True)

    def test_it_can_add_items(self, client):
        body = client.get("/checklists").get_data(as_text=True)
        assert 'id="newItem"' in body

    def test_it_survives_labcore_being_down(self):
        from web_app import create_app

        class Dead:
            base_url = "https://labcore.example"

            def is_running(self):
                return False

            def sql(self, *a, **k):
                return {"error": "unreachable"}

            def write(self, *a, **k):
                return {"error": "unreachable"}

            def read_sql(self, *a, **k):
                return {"error": "unreachable"}

            def get_samples(self, **k):
                return None

            def get_test_names(self, **k):
                return None

        app = create_app(Dead(), authenticator=StubAuth(), secret="s")
        app.config["TESTING"] = True
        assert app.test_client().get("/checklists").status_code == 200


# ── the write queue says no, and the store must not say yes ─────────────────
#
# LabCore's queue refuses past ~100 pending BY ANSWERING: nothing raises, and
# the answer need not carry an "error" key. `import_state` used to ask
# `if not res.get("error")` — true for the refusal below — and counted every
# rejected batch as rows imported. Refusing with {"error": ...} instead would be
# testing the one shape the old code already handled.

from checklists import ChecklistWriteError
from labcore_result import LabCoreError, LabCoreUnavailable


import refusal_shapes                                   # noqa: E402

pytestmark = pytest.mark.usefixtures("both_refusal_shapes")

# SYNTHETIC — see refusal_shapes.
REFUSAL = refusal_shapes.NO_ERROR_KEY


class QueueFull:
    """A real gateway until it refuses. Reads keep working, so a test that a
    write raised can then show the stored ticks are exactly as they were."""

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
            return refusal_shapes.current()
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


def warm(gw, checklist=None):
    """A store with its schema declared and one saved round, then refusing."""
    store = ChecklistStore(gw)
    store.save(checklist or a_list())
    gw.refuse()
    return store


def tick_rows(n, day="2026-08-03"):
    return [{"day": day, "checklist_uid": "c1", "item_uid": f"i{i}",
             "checked": True, "user": "kaden", "at": f"{day}T09:0{i}:00",
             "value": ""} for i in range(n)]


class TestARefusedTickIsNeverReportedAsDone:
    """A tick IS the record that the job was done. A dropped one is not a stale
    screen, it is an audit trail saying nobody checked the nitrogen."""

    def test_set_tick_raises_and_the_item_stays_unticked(self):
        gw = QueueFull()
        store = warm(gw)
        with pytest.raises(ChecklistWriteError):
            store.set_tick("c1", "i1", True, "2026-08-03", "kaden")
        gw.allow()
        assert store.state("2026-08-03") == {}

    def test_unticking_raises_and_the_original_tick_stands(self):
        gw = QueueFull()
        store = ChecklistStore(gw)
        store.save(a_list())
        store.set_tick("c1", "i1", True, "2026-08-03", "kaden")
        gw.refuse()
        with pytest.raises(ChecklistWriteError):
            store.set_tick("c1", "i1", False, "2026-08-03", "sam")
        gw.allow()
        state = store.state("2026-08-03")["c1"]["i1"]
        assert state["checked"] is True and state["user"] == "kaden"

    def test_set_value_raises_and_no_reading_is_recorded(self):
        gw = QueueFull()
        store = warm(gw)
        with pytest.raises(ChecklistWriteError):
            store.set_value("c1", "i1", "1800", "2026-08-03", "kaden")
        gw.allow()
        assert store.state("2026-08-03") == {}
        assert store.values("c1", "i1") == []

    def test_toggle_raises_on_the_parent_and_nothing_is_ticked(self):
        gw = QueueFull()
        store = warm(gw, a_list(items=[
            ChecklistItem(uid="p", text="Gas checks"),
            ChecklistItem(uid="s1", text="Nitrogen", item_type="subtask",
                          parent_uid="p")]))
        with pytest.raises(ChecklistWriteError):
            store.toggle(store_checklist(store, gw), "p", True, "2026-08-03",
                         "kaden")
        gw.allow()
        assert store.state("2026-08-03") == {}

    def test_save_raises_and_the_round_is_not_changed(self):
        gw = QueueFull()
        store = warm(gw)
        with pytest.raises(ChecklistWriteError):
            store.save(a_list(name="Opening round v2"))
        gw.allow()
        assert store.all()[0].name == "Opening round"

    def test_delete_raises_and_the_round_survives(self):
        gw = QueueFull()
        store = warm(gw)
        with pytest.raises(ChecklistWriteError):
            store.delete("c1")
        gw.allow()
        assert [c.uid for c in store.all()] == ["c1"]

    def test_a_refused_create_table_raises_rather_than_latching_ready(self):
        """The old body swallowed everything and set `_ready` inside the try, so
        a refused CREATE meant three more writes into the refusing queue on
        every later call while `save()` reported success."""
        gw = QueueFull()
        gw.refuse()
        store = ChecklistStore(gw)
        with pytest.raises(ChecklistWriteError):
            store.ensure_schema()
        assert store._ready is False

    def test_the_store_recovers_once_the_queue_drains(self):
        gw = QueueFull()
        gw.refuse()
        store = ChecklistStore(gw)
        with pytest.raises(ChecklistWriteError):
            store.save(a_list())
        gw.allow()
        store.save(a_list())
        assert [c.uid for c in store.all()] == ["c1"]

    def test_an_already_applied_migration_is_still_swallowed(self):
        """The one deliberate swallow: `ADD COLUMN value` on a table that has it
        is the normal case on every boot after the first. It must not raise —
        and it must not stop the schema being marked ready."""
        gw = FakeLabCoreGateway()
        store = ChecklistStore(gw)
        store.ensure_schema()
        second = ChecklistStore(gw)
        second.ensure_schema()             # the column already exists
        assert second._ready is True

    def test_the_refusal_is_catchable_as_one_labcore_error(self):
        gw = QueueFull()
        gw.refuse()
        with pytest.raises(LabCoreError):
            ChecklistStore(gw).save(a_list())


def store_checklist(store, gw):
    """The saved round, read back while reads still work."""
    was, gw.refusing = gw.refusing, False
    try:
        return store.get("c1")
    finally:
        gw.refusing = was


class TestImportStateCountsOnlyWhatLanded:
    def test_a_refused_batch_raises_instead_of_being_counted_as_imported(self):
        """The headline bug in this file. `if not res.get("error")` is TRUE
        for any refusal that reports itself some other way, so every rejected
        batch was added to the total and the operator was told 3,096 historical
        ticks had arrived when hundreds never left. `QueueFull` refuses in both
        shapes this module runs — see tests/refusal_shapes.py."""
        gw = QueueFull()
        store = ChecklistStore(gw)
        store.ensure_schema()
        gw.refuse()
        with pytest.raises(ChecklistWriteError):
            store.import_state(tick_rows(3), batch=100, pause=0, attempts=1)
        gw.allow()
        assert store.state("2026-08-03") == {}

    def test_the_error_says_how_many_rows_did_land(self):
        """A partial import is the dangerous one: the first batches are real
        history and the rest are missing, so the number has to be in the words
        the operator sees."""
        gw = QueueFull()
        store = ChecklistStore(gw)
        store.ensure_schema()
        gw.refuse_after = gw.writes + 1        # batch one lands, batch two does not
        with pytest.raises(ChecklistWriteError) as caught:
            store.import_state(tick_rows(4), batch=2, pause=0, attempts=1)
        assert "2 rows landed" in str(caught.value)
        gw.allow()
        assert len(store.state("2026-08-03")["c1"]) == 2

    def test_a_batch_that_lands_after_a_retry_is_counted_once(self):
        """The back-off is deliberate and stays: a busy queue is temporary, and
        losing a retryable batch would be the opposite mistake. It is counted
        once, not once per attempt."""
        gw = QueueFull()
        store = ChecklistStore(gw)
        store.ensure_schema()

        refused = {"n": 0}
        real_sql = gw.sql

        def busy_once(sql, args=None, **kw):
            if "VALUES (?, ?, ?, ?, ?, ?, ?)" in sql and refused["n"] == 0:
                refused["n"] = 1
                return {"busy": True, "retry_after": 0}
            return real_sql(sql, args)

        gw.sql = busy_once
        assert store.import_state(tick_rows(2), batch=100, pause=0,
                                  attempts=3) == 2
        gw.sql = real_sql
        assert refused["n"] == 1
        assert len(store.state("2026-08-03")["c1"]) == 2

    def test_nothing_to_import_is_not_a_write(self):
        gw = QueueFull()
        gw.refuse()
        assert ChecklistStore(gw).import_state([]) == 0


class TestCouldNotAskIsNotNothingToDo:
    def test_state_raises_rather_than_showing_an_untouched_round(self):
        """An empty answer shows every item unticked; whoever is on shift
        re-ticks, and `set_tick` overwrites the real record's name and time."""
        with pytest.raises(LabCoreUnavailable):
            ChecklistStore(Unreadable()).state("2026-08-03")

    def test_get_raises_rather_than_answering_none(self):
        """`None` here is the tick route's 404 about a round that exists."""
        with pytest.raises(LabCoreUnavailable):
            ChecklistStore(Unreadable()).get("c1")

    def test_history_does_not_report_an_archive_with_no_days(self):
        with pytest.raises(LabCoreUnavailable):
            ChecklistStore(Unreadable()).history()

    def test_values_does_not_draw_a_cylinder_that_was_never_measured(self):
        with pytest.raises(LabCoreUnavailable):
            ChecklistStore(Unreadable()).values("c1", "i1")

    def test_a_corrupt_blob_is_still_tolerated(self):
        """The one read-side swallow that stays: LabCore ANSWERED and one row's
        JSON is unreadable. That is bad data, not an unreachable LabCore, and
        one hand-edited row must not take the page down."""
        gw = FakeLabCoreGateway()
        store = ChecklistStore(gw)
        store.save(a_list())
        gw.sql("UPDATE lem_checklist_defs SET items = '{bad' WHERE uid = 'c1'")
        assert store.all()[0].items == []
