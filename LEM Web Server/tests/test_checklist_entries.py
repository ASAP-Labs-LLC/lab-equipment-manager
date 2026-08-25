"""Checklist items that record a reading, and the V4 rounds imported.

Two things here.

**Data entry.** A tick alone loses the number. V4's own round says "Check and
Record Gas Levels (Replace if under 300 PSI)" — and recorded nothing, so nobody
could see a cylinder trending down. An item can now carry a field:

  * `number` — for tracking. Helium: `2900` PSI. Rejects non-numbers, because a
    trend built from "about half" is not a trend.
  * `text`   — for logging anything. Waste tank: `half full`.

**Importing V4.** 60 real items across Opening and Closing, with headers,
subtasks and weekday scoping, live in `lab_manager_config.json`. Two junk
checklists in the same file have zero items and must not come across.
"""
import json
import re
from datetime import date

import pytest

from checklists import (Checklist, ChecklistItem, ChecklistStore,
                        ChecklistWriteError, import_v4_checklists)
from labcore_gateway import FakeLabCoreGateway

V4_JSON = json.dumps({"checklists": [
    {"uid": "junk1", "name": "Opening Checklsit", "due_time": "12:00",
     "items": []},
    {"uid": "junk2", "name": "Opening", "due_time": "12:00", "items": []},
    {"uid": "v4open", "name": "Opening", "due_time": "09:30", "items": [
        {"uid": "h1", "text": "Start the lab", "item_type": "header",
         "days_active": [0, 1, 2, 3, 4, 5, 6]},
        {"uid": "i1", "text": "Power on fans & Ventilation Systems",
         "item_type": "item", "days_active": [0, 1, 2, 3, 4]},
        {"uid": "i2", "text": "Check and Record Gas Levels "
                              "(Replace if under 300 PSI)",
         "item_type": "item", "days_active": [0, 1, 2, 3, 4]},
        {"uid": "s1", "text": "Sulfur (Solvent: Toluene)",
         "item_type": "subtask", "parent_uid": "i2",
         "days_active": [0, 1, 2, 3, 4]},
    ]},
    {"uid": "v4close", "name": "Closing", "due_time": "18:00", "items": [
        {"uid": "c1", "text": "Check gas levels", "item_type": "item",
         "days_active": [0, 1, 2, 3, 4]},
        {"uid": "c2", "text": "Dispose of retained samples older than 30 days",
         "item_type": "item", "days_active": [4]},
    ]},
]})


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


def gas_list():
    return Checklist(uid="c1", name="Opening", slot="opening", due_time="09:30",
                     items=[
        ChecklistItem(uid="h", text="Check gas levels", item_type="header"),
        ChecklistItem(uid="he", text="Helium", item_type="subtask",
                      parent_uid="h", entry_type="number", units="PSI"),
        ChecklistItem(uid="wt", text="Waste tank", entry_type="text"),
        ChecklistItem(uid="plain", text="Vacuum the lab"),
    ])


# ── the model ───────────────────────────────────────────────────────────────

class TestEntryFields:
    def test_an_item_defaults_to_no_field(self):
        assert ChecklistItem(uid="i", text="x").entry_type == "none"

    def test_a_number_field_round_trips(self, store):
        store.save(gas_list())
        he = [i for i in store.all()[0].items if i.uid == "he"][0]
        assert he.entry_type == "number" and he.units == "PSI"

    def test_a_text_field_round_trips(self, store):
        store.save(gas_list())
        wt = [i for i in store.all()[0].items if i.uid == "wt"][0]
        assert wt.entry_type == "text"

    def test_an_unknown_entry_type_falls_back_to_none(self):
        """A hand-edited blob must not invent a widget nobody can render."""
        item = ChecklistItem.from_dict({"uid": "i", "text": "x",
                                       "entry_type": "colour-picker"})
        assert item.entry_type == "none"

    def test_an_item_with_a_field_still_counts_towards_completion(self):
        assert ChecklistItem(uid="i", text="x",
                             entry_type="number").counts_towards_completion()


class TestRecordingValues:
    def test_a_number_is_recorded_with_who_and_when(self, store):
        store.save(gas_list())
        store.set_value("c1", "he", "2900", "2026-08-03", "kaden")
        st = store.state("2026-08-03")["c1"]["he"]
        assert st["value"] == "2900"
        assert st["user"] == "kaden" and st["at"]

    def test_recording_a_value_ticks_the_item(self, store):
        """Entering the reading IS doing the job; making someone also tick it is
        a second chore that will get skipped."""
        store.save(gas_list())
        store.set_value("c1", "he", "2900", "2026-08-03", "kaden")
        assert store.state("2026-08-03")["c1"]["he"]["checked"] is True

    def test_clearing_a_value_unticks_it(self, store):
        store.save(gas_list())
        store.set_value("c1", "he", "2900", "2026-08-03", "kaden")
        store.set_value("c1", "he", "", "2026-08-03", "kaden")
        st = store.state("2026-08-03")["c1"]["he"]
        assert st["value"] == "" and st["checked"] is False

    def test_free_text_is_kept_verbatim(self, store):
        store.save(gas_list())
        store.set_value("c1", "wt", "half full", "2026-08-03", "kaden")
        assert store.state("2026-08-03")["c1"]["wt"]["value"] == "half full"

    def test_a_ticked_item_with_no_field_has_no_value(self, store):
        store.save(gas_list())
        store.set_tick("c1", "plain", True, "2026-08-03", "kaden")
        assert store.state("2026-08-03")["c1"]["plain"]["value"] == ""

    def test_values_are_per_day(self, store):
        store.save(gas_list())
        store.set_value("c1", "he", "2900", "2026-08-03", "kaden")
        store.set_value("c1", "he", "2750", "2026-08-04", "sam")
        assert store.state("2026-08-04")["c1"]["he"]["value"] == "2750"
        assert store.state("2026-08-03")["c1"]["he"]["value"] == "2900"

    def test_an_older_state_table_gains_the_value_column(self, gw):
        """The table shipped before values existed; upgrading must not need a
        hand-run migration."""
        gw.sql("CREATE TABLE IF NOT EXISTS lem_checklist_state ("
               "day TEXT NOT NULL, checklist_uid TEXT NOT NULL, "
               "item_uid TEXT NOT NULL, checked INTEGER, user TEXT, at TEXT, "
               "PRIMARY KEY (day, checklist_uid, item_uid))")
        store = ChecklistStore(gw)
        store.save(gas_list())
        store.set_value("c1", "he", "2900", "2026-08-03", "kaden")
        assert store.state("2026-08-03")["c1"]["he"]["value"] == "2900"


class TestNumberTracking:
    def test_readings_come_back_as_a_dated_series(self, store):
        store.save(gas_list())
        for day, v in [("2026-08-01", "3000"), ("2026-08-02", "2900"),
                       ("2026-08-03", "2750")]:
            store.set_value("c1", "he", v, day, "kaden")
        series = store.values("c1", "he")
        assert [p["day"] for p in series] == ["2026-08-01", "2026-08-02",
                                              "2026-08-03"]
        assert [p["value"] for p in series] == [3000.0, 2900.0, 2750.0]

    def test_unreadable_readings_are_left_out_of_the_series(self, store):
        """A trend built from "about half" is not a trend."""
        store.save(gas_list())
        store.set_value("c1", "he", "3000", "2026-08-01", "k")
        store.set_value("c1", "he", "about half", "2026-08-02", "k")
        assert [p["value"] for p in store.values("c1", "he")] == [3000.0]

    def test_an_item_never_filled_in_has_no_series(self, store):
        store.save(gas_list())
        assert store.values("c1", "he") == []


# ── importing V4 ────────────────────────────────────────────────────────────

class TestV4Import:
    def test_both_real_rounds_come_across(self):
        lists = import_v4_checklists(V4_JSON)
        assert sorted(c.name for c in lists) == ["Closing", "Opening"]

    def test_empty_checklists_are_left_behind(self):
        """Two junk stubs share the file, including one with a typo'd name."""
        lists = import_v4_checklists(V4_JSON)
        assert len(lists) == 2
        assert not any(c.items == [] for c in lists)

    def test_the_slot_is_inferred_from_the_name(self):
        by = {c.name: c for c in import_v4_checklists(V4_JSON)}
        assert by["Opening"].slot == "opening"
        assert by["Closing"].slot == "closing"

    def test_due_times_come_across(self):
        by = {c.name: c for c in import_v4_checklists(V4_JSON)}
        assert by["Opening"].due_time == "09:30"
        assert by["Closing"].due_time == "18:00"

    def test_headers_and_subtasks_keep_their_shape(self):
        opening = [c for c in import_v4_checklists(V4_JSON)
                   if c.name == "Opening"][0]
        kinds = {i.uid: i.item_type for i in opening.items}
        assert kinds["h1"] == "header"
        assert kinds["s1"] == "subtask"

    def test_a_subtask_keeps_its_parent(self):
        opening = [c for c in import_v4_checklists(V4_JSON)
                   if c.name == "Opening"][0]
        sub = [i for i in opening.items if i.uid == "s1"][0]
        assert sub.parent_uid == "i2"

    def test_weekday_scoping_comes_across(self):
        """"Dispose of retained samples older than 30 days" runs on Fridays."""
        closing = [c for c in import_v4_checklists(V4_JSON)
                   if c.name == "Closing"][0]
        friday = [i for i in closing.items if i.uid == "c2"][0]
        assert friday.days_active == [4]

    def test_imported_items_start_with_no_entry_field(self):
        """V4 had no such concept; they're added afterwards, deliberately."""
        for cl in import_v4_checklists(V4_JSON):
            assert all(i.entry_type == "none" for i in cl.items)

    def test_junk_input_is_an_empty_import_not_a_crash(self):
        assert import_v4_checklists("{not json") == []
        assert import_v4_checklists(json.dumps({"nope": 1})) == []
        assert import_v4_checklists("") == []

    def test_a_checklist_with_no_name_is_skipped(self):
        text = json.dumps({"checklists": [
            {"name": "", "items": [{"text": "x"}]}]})
        assert import_v4_checklists(text) == []


class TestImportEndpoint:
    def test_it_needs_an_account(self, client):
        r = client.post("/api/checklists/import-v4", json={"json": V4_JSON})
        assert r.status_code == 401

    def test_a_dry_run_writes_nothing(self, signed_in):
        body = signed_in.post("/api/checklists/import-v4?dry_run=1",
                              json={"json": V4_JSON}).get_json()
        assert body["count"] == 2
        assert signed_in.get("/api/checklists").get_json()["checklists"] == []

    def test_importing_saves_both_rounds(self, signed_in):
        signed_in.post("/api/checklists/import-v4", json={"json": V4_JSON})
        names = [c["name"] for c in
                 signed_in.get("/api/checklists").get_json()["checklists"]]
        assert sorted(names) == ["Closing", "Opening"]

    def test_the_items_arrive_too(self, signed_in, open_for_business):
        signed_in.post("/api/checklists/import-v4", json={"json": V4_JSON})
        opening = [c for c in signed_in.get("/api/checklists").get_json()
                   ["checklists"] if c["name"] == "Opening"][0]
        texts = [i["text"] for i in opening["items"]]
        assert "Power on fans & Ventilation Systems" in texts

    def test_re_importing_replaces_rather_than_duplicates(self, signed_in):
        signed_in.post("/api/checklists/import-v4", json={"json": V4_JSON})
        signed_in.post("/api/checklists/import-v4", json={"json": V4_JSON})
        assert len(signed_in.get("/api/checklists").get_json()
                   ["checklists"]) == 2

    def test_a_missing_payload_is_a_clear_400(self, signed_in):
        r = signed_in.post("/api/checklists/import-v4", json={})
        assert r.status_code == 400 and r.get_json()["error"]

    def test_the_import_is_audited(self, signed_in):
        signed_in.post("/api/checklists/import-v4", json={"json": V4_JSON})
        entries = signed_in.get("/api/logs?kind=config").get_json()["events"]
        assert any("checklist" in e["action"] and "import" in e["action"]
                   for e in entries)


# ── over HTTP: entering values ──────────────────────────────────────────────

class TestValueEndpoint:
    def test_it_needs_an_account(self, client, gw):
        ChecklistStore(gw).save(gas_list())
        r = client.post("/api/checklists/c1/value",
                        json={"item_uid": "he", "value": "2900"})
        assert r.status_code == 401

    def test_a_number_is_accepted(self, gw, signed_in):
        ChecklistStore(gw).save(gas_list())
        r = signed_in.post("/api/checklists/c1/value",
                           json={"item_uid": "he", "value": "2900"})
        assert r.status_code == 200
        st = signed_in.get("/api/checklists").get_json()["state"]["c1"]["he"]
        assert st["value"] == "2900" and st["checked"] is True

    def test_a_non_number_in_a_number_field_is_refused(self, gw, signed_in):
        """Otherwise the trend silently stops being a trend."""
        ChecklistStore(gw).save(gas_list())
        r = signed_in.post("/api/checklists/c1/value",
                           json={"item_uid": "he", "value": "about half"})
        assert r.status_code == 400
        assert "number" in r.get_json()["error"].lower()

    def test_free_text_takes_anything(self, gw, signed_in):
        ChecklistStore(gw).save(gas_list())
        r = signed_in.post("/api/checklists/c1/value",
                           json={"item_uid": "wt", "value": "half full"})
        assert r.status_code == 200

    def test_clearing_a_number_is_allowed(self, gw, signed_in):
        ChecklistStore(gw).save(gas_list())
        signed_in.post("/api/checklists/c1/value",
                       json={"item_uid": "he", "value": "2900"})
        r = signed_in.post("/api/checklists/c1/value",
                           json={"item_uid": "he", "value": ""})
        assert r.status_code == 200

    def test_an_item_with_no_field_is_refused(self, gw, signed_in):
        ChecklistStore(gw).save(gas_list())
        r = signed_in.post("/api/checklists/c1/value",
                           json={"item_uid": "plain", "value": "x"})
        assert r.status_code == 400

    def test_an_unknown_item_is_a_404(self, gw, signed_in):
        ChecklistStore(gw).save(gas_list())
        r = signed_in.post("/api/checklists/c1/value",
                           json={"item_uid": "ghost", "value": "1"})
        assert r.status_code == 404

    def test_the_series_is_exposed(self, gw, signed_in):
        ChecklistStore(gw).save(gas_list())
        signed_in.post("/api/checklists/c1/value",
                       json={"item_uid": "he", "value": "2900"})
        body = signed_in.get("/api/checklists/c1/values?item=he").get_json()
        assert body["series"][0]["value"] == 2900.0
        assert body["units"] == "PSI"


# ── the page ────────────────────────────────────────────────────────────────

class TestThePage:
    def test_it_can_import_v4(self, client):
        assert "import-v4" in client.get("/checklists").get_data(as_text=True)

    def test_it_renders_entry_fields(self, client):
        body = client.get("/checklists").get_data(as_text=True)
        assert "data-entry" in body

    def test_it_has_an_editor(self, client):
        body = client.get("/checklists").get_data(as_text=True)
        assert 'id="editDlg"' in body

    def test_the_editor_offers_both_field_types(self, client):
        body = client.get("/checklists").get_data(as_text=True)
        assert 'value="number"' in body and 'value="text"' in body

    def test_the_editor_offers_headers_and_subtasks(self, client):
        body = client.get("/checklists").get_data(as_text=True)
        assert 'value="header"' in body and 'value="subtask"' in body

    def test_weekdays_are_editable(self, client):
        assert 'id="edDays"' in client.get("/checklists").get_data(as_text=True)


# ── the editor, finished ────────────────────────────────────────────────────
#
# The first cut could only edit the FIRST checklist in a slot, couldn't reorder
# anything, couldn't turn a heading back into an item, and — worst — gave a new
# subtask no parent, which left parent→child ticking unreachable from the UI.

class TestEditorIsComplete:
    @pytest.fixture
    def page(self, client):
        return client.get("/checklists").get_data(as_text=True)

    def test_you_can_choose_which_checklist_to_edit(self, page):
        assert 'id="edPick"' in page

    def test_items_can_be_reordered(self, page):
        """V4's rounds are ordered on purpose — power down the bath before the
        lights."""
        assert "data-move" in page

    def test_a_subtask_can_be_given_a_parent(self, page):
        """Without this, ticking a parent can never tick its children, because
        nothing can create the relationship."""
        assert 'data-f="parent_uid"' in page

    def test_a_heading_can_be_turned_back_into_an_item(self, page):
        """A heading used to render as a text box alone — a one-way door. Every
        row now comes from one template that always carries the type select."""
        m = re.search(r"function edItemRow\(.*?\n\}", page, re.S)
        assert m, "row builder not found"
        row = m.group(0)
        assert 'data-f="item_type"' in row
        # and no early return that skips it for headers
        assert not re.search(r"if\s*\(item\.item_type\s*===\s*'header'\)\s*\{"
                             r"\s*return", row)

    def test_the_selected_row_is_visible(self, page):
        assert "edrow.sel" in page or 'class="edrow sel' in page

    def test_the_day_chips_name_the_item_they_apply_to(self, page):
        assert "edDays" in page


class TestParentChildStillWorks:
    """The behaviour the editor now has to be able to express."""

    def test_a_parent_ticks_its_children(self, gw, signed_in):
        ChecklistStore(gw).save(Checklist(
            uid="c1", name="Opening", slot="opening", items=[
                ChecklistItem(uid="p", text="Power down equipment"),
                ChecklistItem(uid="a", text="Copper bath",
                              item_type="subtask", parent_uid="p"),
                ChecklistItem(uid="b", text="Thermal bath",
                              item_type="subtask", parent_uid="p")]))
        signed_in.post("/api/checklists/c1/toggle",
                       json={"item_uid": "p", "checked": True})
        st = signed_in.get("/api/checklists").get_json()["state"]["c1"]
        assert st["a"]["checked"] and st["b"]["checked"]


# ── the archive ─────────────────────────────────────────────────────────────

class TestArchive:
    @pytest.fixture
    def page(self, client):
        return client.get("/checklists").get_data(as_text=True)

    def test_there_is_an_archived_button(self, page):
        assert 'id="btnArchive"' in page

    def test_it_draws_a_square_per_day(self, page):
        assert 'id="arcGrid"' in page and "data-day=" in page

    def test_a_day_can_be_opened(self, page):
        assert 'id="arcDay"' in page

    def test_it_reads_the_history_and_the_day(self, page):
        assert "/api/checklists/history" in page
        assert "day=${encodeURIComponent(day)}" in page

    def test_the_importer_takes_the_history_file(self, page):
        assert 'id="impState"' in page

    def test_history_lands_through_the_endpoint(self, gw, signed_in):
        state = json.dumps({"2026-01-08": {
            "v4open|i1": {"user": "ryan", "time": "09:12", "checked": True},
            "v4open|i2": {"user": "ryan", "time": "09:15", "checked": False}}})
        body = signed_in.post("/api/checklists/import-v4",
                              json={"json": V4_JSON, "state": state}).get_json()
        assert body["history_rows"] == 2 and body["history_days"] == 1
        day = signed_in.get("/api/checklists?day=2026-01-08").get_json()
        st = day["state"]["v4open"]
        assert st["i1"]["checked"] is True and st["i1"]["user"] == "ryan"
        assert st["i2"]["checked"] is False

    def test_a_dry_run_reports_history_without_writing_it(self, signed_in):
        state = json.dumps({"2026-01-08": {
            "v4open|i1": {"user": "ryan", "time": "09:12", "checked": True}}})
        body = signed_in.post("/api/checklists/import-v4?dry_run=1",
                              json={"json": V4_JSON, "state": state}).get_json()
        assert body["history_rows"] == 1
        assert signed_in.get(
            "/api/checklists?day=2026-01-08").get_json()["state"] == {}

    def test_legacy_positional_keys_are_resolved(self, gw, signed_in):
        """The oldest V4 entries key items by index, not uid."""
        state = json.dumps({"2026-01-08": {
            "v4open|1": {"user": "admin", "time": "08:00", "checked": True}}})
        signed_in.post("/api/checklists/import-v4",
                       json={"json": V4_JSON, "state": state})
        st = signed_in.get(
            "/api/checklists?day=2026-01-08").get_json()["state"]["v4open"]
        assert st["i1"]["checked"] is True      # index 1 of the imported list

    def test_history_for_a_checklist_we_did_not_import_is_dropped(self,
                                                                 signed_in):
        state = json.dumps({"2026-01-08": {
            "ghost-uid|x": {"user": "a", "time": "08:00", "checked": True}}})
        body = signed_in.post("/api/checklists/import-v4",
                              json={"json": V4_JSON, "state": state}).get_json()
        assert body["history_rows"] == 0

    def test_the_archive_summary_reflects_imported_history(self, signed_in):
        state = json.dumps({"2026-01-08": {
            "v4open|i1": {"user": "r", "time": "09:00", "checked": True},
            "v4open|i2": {"user": "r", "time": "09:00", "checked": False}}})
        signed_in.post("/api/checklists/import-v4",
                       json={"json": V4_JSON, "state": state})
        days = signed_in.get("/api/checklists/history").get_json()["days"]
        entry = [d for d in days if d["day"] == "2026-01-08"][0]
        assert entry["total"] == 2 and entry["checked"] == 1


class TestBulkImportIsHonest:
    """LabCore reports a full write queue as an error DICT, not an exception —
    counting those as successes is how 3094 ticks 'imported' and none landed.

    Updated 2026-08-24 with `labcore_result`. Two things changed:

    * The refusal used below carries an "error" key, which is the ONE shape the
      old `if not res.get("error")` already handled. Past ~100 pending the real
      queue answers `{"queued": false, "pending": 137}` with no "error" key at
      all, so the class now refuses in that shape too — otherwise it is testing
      the case the bug cannot occur in.
    * An exhausted batch RAISES instead of returning a short count. Re-running
      the import is an upsert keyed on (day, checklist_uid, item_uid) and is
      therefore safe, whereas a smaller number in a JSON payload is not how you
      tell someone half their history never arrived.
    """

    class Busy:
        def __init__(self, fail_times=0, refusal=None):
            self.fail_times = fail_times
            self.calls = 0
            self.refusal = refusal or {"error": "LabCore is busy",
                                       "busy": True, "retry_after": 0}

        def sql(self, sql, args=None, **kw):
            self.calls += 1
            if self.calls <= self.fail_times:
                return dict(self.refusal)
            return {"ok": True}

        def read_sql(self, sql, args=None, **kw):
            return {"ok": True, "rows": []}

    def rows(self, n=3):
        return [{"day": "2026-01-0%d" % (i + 1), "checklist_uid": "c1",
                 "item_uid": "i1", "checked": True, "user": "r",
                 "at": "2026-01-01T09:00:00", "value": ""} for i in range(n)]

    def test_a_rejected_batch_is_not_counted_as_imported(self):
        """Refused in the queue's real shape: no exception, no "error" key."""
        gw = self.Busy(refusal={"queued": False, "pending": 137})
        store = ChecklistStore(gw)
        store.ensure_schema()                 # declared while LabCore is well
        gw.fail_times = gw.calls + 99
        with pytest.raises(ChecklistWriteError) as caught:
            store.import_state(self.rows(), pause=0, attempts=2)
        assert "0 rows landed" in str(caught.value)

    def test_a_busy_queue_is_retried_then_succeeds(self):
        """The back-off stays: busy is temporary, and losing a retryable batch
        would be the opposite mistake."""
        gw = self.Busy()
        store = ChecklistStore(gw)
        store.ensure_schema()                 # the DDL is not what is under test
        gw.fail_times = gw.calls + 2          # the next two writes are refused
        assert store.import_state(self.rows(), pause=0, attempts=5) == 3

    def test_rows_go_up_in_batches_not_one_at_a_time(self):
        """3000 single inserts at ~0.7s each would take half an hour."""
        gw = self.Busy()
        store = ChecklistStore(gw)
        store.import_state(self.rows(250), batch=100, pause=0)
        inserts = gw.calls - 3                # minus schema DDL + migration
        assert inserts <= 4, f"{inserts} write ops for 250 rows"
