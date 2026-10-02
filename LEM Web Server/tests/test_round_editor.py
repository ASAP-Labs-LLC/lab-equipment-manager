"""The round editor (ia-final §3.4, piece 10) and the save it posts.

P2, the bug this page exists to end (baseline lem-ui.md §P2): on an empty
slot, typing three items and pressing Enter after each made THREE checklists
called "Closing round", one item each. The empty-state input had no
checklist to belong to, so every Add posted a brand-new checklist. The
operator saw one round being built; the record got three.

So defining a round moved off the live round onto its own page, and the
page posts the whole definition ONCE, with every item in `items[]`. Enter
adds a row inside the editor; nothing is sent until Save. And a new round
carries a uid the browser minted when the page opened, so a double-click on
Save, or a Save pressed again because the answer was lost, writes the same
round twice — an upsert — instead of two rounds. P2 is impossible by
construction rather than by care.

What the one Save also carries, because the editor is now the only place a
round is defined:

* **Limits on a number**, optional, typed as text. Blank is "no limit",
  never 0.0: a 0.0 default is a minimum nobody set, and on a cylinder
  pressure it is a limit every reading passes. Reversed limits are refused
  (every reading would be both too high and too low), and so is a limit that
  is not a number. A refusal names the item and writes NOTHING.
* **Track this reading** (backlog 9: tracked things were API-only). On, the
  item feeds a tracked thing, matched by name across rounds so the nitrogen
  read at opening and at closing is one series; its limits live on that
  thing, ONCE, because two copies of a limit is how they come to disagree.
  Off, the item keeps its own series and its own limits.

The pages: `/checklists/edit` lists the rounds, `/checklists/edit/<uid>` and
`/checklists/edit/new?slot=` are the editor. The old LEM import lives in
Settings › Imports now, not here. Every dialog is a styled `dialog.sheet`
(P8: the old ones rendered as white browser defaults on a dark page).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

import web_app
from labcore_gateway import FakeLabCoreGateway
from labcore_result import LabCoreUnavailable
from web_app import create_app

ROOT = Path(__file__).resolve().parent.parent
T = ROOT / "templates"


class StubAuth:
    def login(self, u, p):
        return (u, "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


def _app(gw=None, tmp_path=None):
    app = create_app(gw or FakeLabCoreGateway(), authenticator=StubAuth(), secret="s",
                     documents_root=str(tmp_path) if tmp_path else None)
    app.config["TESTING"] = True
    return app


def _signed_in(app, who="Ana"):
    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = who
    return c


@pytest.fixture
def app(tmp_path):
    return _app(tmp_path=tmp_path)


@pytest.fixture
def c(app):
    return _signed_in(app)


def _defs(c):
    r = c.get("/api/checklists")
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["checklists"]


def _html(c, path, status=200):
    r = c.get(path)
    assert r.status_code == status, (path, r.status_code, r.get_data(as_text=True)[:400])
    return r.get_data(as_text=True)


def text(fragment: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", fragment).split())


def T4A(uid="a1b2c3d4e5f6", **over):
    """What the editor posts for T4a: an Opening round, three items, the
    second a PSI reading. Uids minted by the browser."""
    body = {"uid": uid, "name": "Opening round", "slot": "opening", "due_time": "07:30",
            "items": [
                {"uid": "i1", "text": "Check nitrogen generator", "entry_type": "none"},
                {"uid": "i2", "text": "Helium cylinder pressure", "entry_type": "number",
                 "units": "PSI", "min": "", "max": "", "track": False},
                {"uid": "i3", "text": "Empty waste tank", "entry_type": "none"},
            ]}
    body.update(over)
    return body


# ── P2: one Save, one round ─────────────────────────────────────────────────

class TestOneSaveIsOneRound:
    def test_three_items_in_one_post_make_one_checklist(self, c):
        """The bar's API check: after T4a there is exactly one new checklist,
        and it holds all three items."""
        assert _defs(c) == []
        r = c.post("/api/checklists", json=T4A())
        assert r.status_code == 200, r.get_data(as_text=True)
        lists = _defs(c)
        assert len(lists) == 1
        assert [i["text"] for i in lists[0]["items"]] == [
            "Check nitrogen generator", "Helium cylinder pressure", "Empty waste tank"]
        assert lists[0]["items"][1]["entry_type"] == "number"
        assert lists[0]["items"][1]["units"] == "PSI"

    def test_save_pressed_twice_is_still_one_round(self, c):
        """A double-click, or Save pressed again because the answer was lost on
        the way back: the browser minted the uid, so the second post upserts
        the same round instead of making a second one (P2 by another road)."""
        for _ in range(3):
            assert c.post("/api/checklists", json=T4A()).status_code == 200
        lists = _defs(c)
        assert len(lists) == 1 and len(lists[0]["items"]) == 3

    def test_item_uids_the_browser_minted_are_kept(self, c):
        """Readings are filed under (round uid, item uid). An item whose uid
        changed on every save would orphan every reading taken before it."""
        c.post("/api/checklists", json=T4A())
        assert [i["uid"] for i in _defs(c)[0]["items"]] == ["i1", "i2", "i3"]

    def test_two_items_with_one_uid_are_refused(self, c):
        body = T4A()
        body["items"][2]["uid"] = "i1"
        r = c.post("/api/checklists", json=body)
        assert r.status_code == 400
        assert _defs(c) == []

    def test_a_blank_item_label_is_refused_and_named(self, c):
        body = T4A()
        body["items"][1]["text"] = "   "
        r = c.post("/api/checklists", json=body)
        assert r.status_code == 400
        assert r.get_json()["item"] == 1
        assert _defs(c) == []


# ── limits: optional, typed, never 0.0, never reversed ──────────────────────

class TestLimits:
    def _item(self, c, **fields):
        body = T4A()
        body["items"][1].update(fields)
        return c.post("/api/checklists", json=body)

    def test_limits_are_kept_on_an_untracked_number(self, c):
        r = self._item(c, min="500", max="3000")
        assert r.status_code == 200, r.get_data(as_text=True)
        item = _defs(c)[0]["items"][1]
        assert (item["min"], item["max"]) == (500.0, 3000.0)

    def test_blank_limits_are_no_limits_not_zero(self, c):
        """A 0.0 default is a minimum nobody set — and on a pressure, a limit
        every reading passes, so it can never warn about anything."""
        assert self._item(c, min="", max=" ").status_code == 200
        item = _defs(c)[0]["items"][1]
        assert item["min"] is None and item["max"] is None

    def test_one_sided_limit(self, c):
        assert self._item(c, min="", max="10").status_code == 200
        item = _defs(c)[0]["items"][1]
        assert (item["min"], item["max"]) == (None, 10.0)

    def test_reversed_limits_are_refused_and_nothing_is_written(self, c):
        """Reversed limits make every reading too high AND too low."""
        r = self._item(c, min="3000", max="500")
        assert r.status_code == 400
        body = r.get_json()
        assert body["item"] == 1 and body["field"] == "min"
        assert "Helium cylinder pressure" in body["error"]
        assert "3000" in body["error"] and "500" in body["error"]
        assert _defs(c) == [], "a refused save wrote the round anyway"

    @pytest.mark.parametrize("raw", ["abc", "1,000", "nan", "inf"])
    def test_a_limit_that_is_not_a_number_is_refused(self, c, raw):
        r = self._item(c, max=raw)
        assert r.status_code == 400
        assert r.get_json()["field"] == "max"
        assert _defs(c) == []

    def test_limits_on_a_tick_item_are_dropped(self, c):
        """A tick has no reading to judge; a limit on it would be a number on
        the record that means nothing."""
        body = T4A()
        body["items"][0].update(min="1", max="2")
        assert c.post("/api/checklists", json=body).status_code == 200
        item = _defs(c)[0]["items"][0]
        assert item.get("min") is None and item.get("max") is None


# ── Track this reading ──────────────────────────────────────────────────────

class TestTrackThisReading:
    def _tracked(self, c):
        return c.get("/api/tracked").get_json()["tracked"]

    def test_tracking_creates_one_thing_with_the_limits(self, c):
        body = T4A()
        body["items"][1].update(track=True, min="500", max="3000")
        r = c.post("/api/checklists", json=body)
        assert r.status_code == 200, r.get_data(as_text=True)
        things = self._tracked(c)
        assert len(things) == 1
        t = things[0]
        assert (t["name"], t["units"], t["min"], t["max"]) == (
            "Helium cylinder pressure", "PSI", 500.0, 3000.0)
        item = _defs(c)[0]["items"][1]
        assert item["track_uid"] == t["uid"]
        # said once: a tracked item's limits live on the thing, not on the item
        assert item.get("min") is None and item.get("max") is None

    def test_the_same_reading_on_two_rounds_is_one_series(self, c):
        """Ryan: "Opening and closing need to intersect." The cylinder read at
        opening and at closing is one cylinder; case and spacing do not make
        two of them."""
        a = T4A()
        a["items"][1].update(track=True)
        assert c.post("/api/checklists", json=a).status_code == 200
        b = {"uid": "closing00001", "name": "Closing round", "slot": "closing", "due_time": "17:00",
             "items": [{"uid": "c1", "text": "helium  cylinder Pressure", "entry_type": "number",
                        "units": "PSI", "track": True}]}
        assert c.post("/api/checklists", json=b).status_code == 200
        things = self._tracked(c)
        assert len(things) == 1
        uids = {i["track_uid"] for cl in _defs(c) for i in cl["items"] if i["entry_type"] == "number"}
        assert uids == {things[0]["uid"]}

    def test_switching_it_off_unlinks_and_moves_no_reading(self, c):
        a = T4A()
        a["items"][1].update(track=True)
        c.post("/api/checklists", json=a)
        a["items"][1].update(track=False)
        assert c.post("/api/checklists", json=a).status_code == 200
        assert _defs(c)[0]["items"][1]["track_uid"] == ""

    def test_a_post_without_the_switch_keeps_what_the_item_tracked(self, c):
        """Older callers (the import, the convert tool) post `track_uid` and no
        switch. They must not be unlinked by a field they never sent."""
        a = T4A()
        a["items"][1].update(track=True)
        c.post("/api/checklists", json=a)
        uid = _defs(c)[0]["items"][1]["track_uid"]
        b = T4A()
        b["items"][1].pop("track")
        b["items"][1]["track_uid"] = uid
        assert c.post("/api/checklists", json=b).status_code == 200
        assert _defs(c)[0]["items"][1]["track_uid"] == uid

    def test_an_unreadable_tracked_list_writes_nothing(self, app, c):
        """Which thing to link to is decided by a read. A failed read is not
        "no things yet": creating a second Nitrogen out of it would split the
        series the switch exists to join."""
        import checklists as cl_mod

        def boom(self):
            raise LabCoreUnavailable("the store did not answer")

        orig = cl_mod.TrackedStore.all
        cl_mod.TrackedStore.all = boom
        try:
            body = T4A()
            body["items"][1].update(track=True)
            r = c.post("/api/checklists", json=body)
        finally:
            cl_mod.TrackedStore.all = orig
        assert r.status_code >= 500
        assert _defs(c) == []
        assert self._tracked(c) == []


# ── the list page ───────────────────────────────────────────────────────────

class TestTheListPage:
    def test_it_lists_rounds_with_links_and_a_new_round_action(self, c):
        c.post("/api/checklists", json=T4A())
        page = _html(c, "/checklists/edit")
        assert re.search(r'href="/checklists/edit/a1b2c3d4e5f6"', page)
        assert "Opening round" in page
        row = re.search(r'<tr[^>]*data-uid="a1b2c3d4e5f6"[^>]*>(.*?)</tr>', page, re.S)
        assert row, "no row for the round"
        words = text(row.group(1))
        assert "Opening" in words and "07:30" in words and "3 items" in words and "1 reading" in words
        top = re.search(r'<header class="topbar"[^>]*>(.*?)</header>', page, re.S).group(1)
        new = re.search(r'<a [^>]*href="/checklists/edit/new"[^>]*>(.*?)</a>', top, re.S)
        assert new and text(new.group(1)) == "New round"

    def test_last_edited_says_who_and_when(self, c):
        c.post("/api/checklists", json=T4A())
        page = _html(c, "/checklists/edit")
        row = re.search(r'<tr[^>]*data-uid="a1b2c3d4e5f6"[^>]*>(.*?)</tr>', page, re.S).group(1)
        assert "Ana" in text(row)

    def test_the_archive_chip_counts_recorded_days(self, c):
        c.post("/api/checklists", json=T4A())
        for day in ("2026-09-28", "2026-09-29", "2026-09-30"):
            r = c.post("/api/checklists/a1b2c3d4e5f6/toggle",
                       json={"item_uid": "i1", "checked": True, "day": day})
            assert r.status_code == 200, r.get_data(as_text=True)
        page = _html(c, "/checklists/edit")
        chip = re.search(r'<button[^>]*id="archive-chip"[^>]*>(.*?)</button>', page, re.S)
        assert chip and text(chip.group(1)) == "Archived (3)"

    def test_empty_is_a_sentence_about_the_lab_with_a_way_forward(self, c):
        page = _html(c, "/checklists/edit")
        empty = re.search(r'id="rounds-empty"[^>]*>(.*?)</section>', page, re.S)
        assert empty, "no empty state"
        assert "No rounds are set up" in text(empty.group(1))
        assert 'href="/checklists/edit/new?slot=opening"' in empty.group(1)
        # the old LEM import lives in Settings now, and the empty state says where
        assert 'href="/settings#imports"' in empty.group(1)

    def test_a_failed_read_is_not_an_empty_list(self, app, c, monkeypatch):
        import checklists as cl_mod

        def boom(self):
            raise LabCoreUnavailable("the store did not answer")

        monkeypatch.setattr(cl_mod.ChecklistStore, "all", boom)
        page = _html(c, "/checklists/edit")
        assert "No rounds are set up" not in text(page)
        failed = re.search(r'id="rounds-failed"[^>]*>(.*?)</section>', page, re.S)
        assert failed and "could not be read" in text(failed.group(1))

    def test_import_is_not_on_this_page(self, c):
        """Moved to Settings › Imports (ia-final §3.7). Defining rounds and
        bulk-importing them are different jobs with different permissions."""
        c.post("/api/checklists", json=T4A())
        page = _html(c, "/checklists/edit")
        assert "import-v4" not in page and "impDlg" not in page

    def test_it_links_to_the_readings(self, c):
        assert 'href="/checklists/trends"' in _html(c, "/checklists/edit")


# ── the editor ──────────────────────────────────────────────────────────────

class TestTheNewRoundEditor:
    def _hours(self, c, opens, closes):
        r = c.post("/api/schedule", json={"opens": opens, "closes": closes})
        assert r.status_code == 200, r.get_data(as_text=True)

    def test_name_and_due_are_prefilled_from_lab_hours(self, c):
        """T4a's walk types no name and no time: an opening round is due half
        an hour after the lab opens, a closing round when it closes."""
        self._hours(c, "07:00", "17:30")
        page = _html(c, "/checklists/edit/new?slot=opening")
        assert re.search(r'id="ed-name"[^>]*value="Opening round"', page)
        assert re.search(r'id="ed-due"[^>]*value="07:30"', page)
        assert "07:00" in text(by_id(page, "ed-due-why"))
        page = _html(c, "/checklists/edit/new?slot=closing")
        assert re.search(r'id="ed-name"[^>]*value="Closing round"', page)
        assert re.search(r'id="ed-due"[^>]*value="17:30"', page)

    def test_without_lab_hours_the_due_time_is_blank_and_says_why(self, c):
        page = _html(c, "/checklists/edit/new?slot=opening")
        assert re.search(r'id="ed-due"[^>]*value=""', page)
        assert "Lab hours" in text(by_id(page, "ed-due-why"))

    def test_the_slot_seg_is_preset(self, c):
        page = _html(c, "/checklists/edit/new?slot=closing")
        checked = re.findall(r'data-slot="(\w+)"[^>]*aria-checked="true"', page)
        assert checked == ["closing"]

    def test_it_starts_with_one_empty_item_ready_to_type(self, c):
        """T4a: item 1 is typed straight away, with no click to find the box."""
        page = without_templates(_html(c, "/checklists/edit/new?slot=opening"))
        rows = re.findall(r'<li class="ed-item"', page)
        assert len(rows) == 1
        assert re.search(r'class="ed-label"[^>]*autofocus', page)

    def test_a_new_round_carries_a_uid_minted_once(self, c):
        page = _html(c, "/checklists/edit/new?slot=opening")
        m = re.search(r'data-uid="([0-9a-f]{12})"', page)
        assert m, "the editor has no uid for the round it is creating"

    def test_one_primary_and_it_is_save(self, c):
        page = _html(c, "/checklists/edit/new?slot=opening")
        page = re.search(r'<main\b.*?</main>', page, re.S).group(0)
        primaries = re.findall(r'<(?:button|a)[^>]*class="[^"]*btn-primary[^"]*"[^>]*>(.*?)</(?:button|a)>', page, re.S)
        assert [text(p) for p in primaries] == ["Save"]

    def test_the_kind_seg_is_tick_number_text(self, c):
        page = _html(c, "/checklists/edit/new?slot=opening")
        assert re.findall(r'data-kind="(\w+)"', by_class_first(page, "ed-kinds")) == ["none", "number", "text"]


class TestEditingARound:
    def test_it_renders_the_definition(self, c):
        body = T4A()
        body["items"][1].update(min="500", max="3000")
        c.post("/api/checklists", json=body)
        page = without_templates(_html(c, "/checklists/edit/a1b2c3d4e5f6"))
        assert re.search(r"<h1[^>]*>\s*Opening round\s*</h1>", page)
        assert "3 items · 1 reading" in text(by_id(page, "ed-meta"))
        assert len(re.findall(r'<li class="ed-item"', page)) == 3
        assert re.search(r'class="ed-min"[^>]*value="500"', page)
        assert re.search(r'class="ed-max"[^>]*value="3000"', page)
        # an existing round links back to doing it
        top = re.search(r'<header class="topbar"[^>]*>(.*?)</header>', page, re.S).group(1)
        assert 'href="/checklists/opening"' in top

    def test_headings_subtasks_and_weekdays_survive_a_save(self, c):
        """The editor shows a label and a kind; it must not drop what it does
        not show. A Saturday-only item that came across from the old LEM
        stays Saturday-only."""
        body = {"uid": "r00000000001", "name": "Closing round", "slot": "closing", "due_time": "",
                "items": [{"uid": "h", "text": "Gas", "item_type": "header"},
                          {"uid": "s", "text": "Helium off", "item_type": "subtask",
                           "parent_uid": "h", "days_active": [5]}]}
        c.post("/api/checklists", json=body)
        page = _html(c, "/checklists/edit/r00000000001")
        data = json.loads(re.search(r"data-round='([^']*)'", page).group(1).replace("&#39;", "'")
                          .replace("&#34;", '"').replace("&quot;", '"').replace("&amp;", "&"))
        items = {i["uid"]: i for i in data["items"]}
        assert items["h"]["item_type"] == "header"
        assert items["s"]["days_active"] == [5] and items["s"]["parent_uid"] == "h"

    def test_an_unknown_round_is_a_404_with_a_way_back(self, c):
        page = _html(c, "/checklists/edit/nope", status=404)
        assert 'href="/checklists/edit"' in page
        assert "No such round" in text(page)

    def test_a_failed_read_is_a_503_not_a_missing_round(self, c, monkeypatch):
        import checklists as cl_mod

        def boom(self, uid):
            raise LabCoreUnavailable("the store did not answer")

        monkeypatch.setattr(cl_mod.ChecklistStore, "get", boom)
        page = _html(c, "/checklists/edit/a1b2c3d4e5f6", status=503)
        assert "No such round" not in text(page)
        assert "could not be read" in text(page)

    def test_the_archive_section_counts_this_rounds_days(self, c):
        c.post("/api/checklists", json=T4A())
        for day in ("2026-09-29", "2026-09-30"):
            c.post("/api/checklists/a1b2c3d4e5f6/toggle",
                   json={"item_uid": "i1", "checked": True, "day": day})
        page = _html(c, "/checklists/edit/a1b2c3d4e5f6")
        sec = re.search(r'<section class="sec" id="archive"(.*?)</section>', page, re.S)
        assert sec and "2 days" in text(sec.group(1)) and "30 Sep 2026" in text(sec.group(1))


# ── P8: every dialog is a styled sheet ──────────────────────────────────────

class TestEveryDialogIsASheet:
    @pytest.mark.parametrize("path", ["/checklists/edit", "/checklists/edit/new?slot=opening",
                                      "/checklists/trends"])
    def test_dialogs_are_sheets(self, c, path):
        page = _html(c, path)
        for tag in re.findall(r"<dialog\b[^>]*>", page):
            assert re.search(r'class="[^"]*\bsheet\b', tag), (path, tag)

    def test_the_per_item_trend_dialog_is_gone(self, c):
        """The Readings page answers `?item=`; a second, smaller copy of it in
        a dialog was one more place for the same fact (§10)."""
        for path in ("/checklists/edit", "/checklists/edit/new?slot=opening",
                     "/checklists/trends", "/checklists/opening"):
            assert "trendDlg" not in _html(c, path)
        for f in T.glob("*.html"):
            assert "trendDlg" not in f.read_text(encoding="utf-8"), f.name


# ── Readings (/checklists/trends, restyled) ─────────────────────────────────

class TestReadings:
    def test_it_is_a_shell_page_called_readings(self, c):
        page = _html(c, "/checklists/trends")
        assert 'class="sb-mark" href="/"' in page
        assert re.search(r"<h1[^>]*>\s*Readings\s*</h1>", page)
        assert len(re.findall(r"<h1\b", page)) == 1

    def test_a_tracked_series_names_the_items_that_feed_it(self, c):
        """`?item=<uid>` jumps to an item's chart, and a tracked item's chart
        is its thing's: the page needs to know which items feed which thing."""
        body = T4A()
        body["items"][1].update(track=True)
        c.post("/api/checklists", json=body)
        t = c.get("/api/checklists/trends").get_json()["trends"]
        assert len(t) == 1 and t[0]["item_uids"] == ["i2"]

    def test_an_untracked_item_with_limits_is_judged_and_one_without_is_not(self, c):
        body = T4A()
        body["items"][1].update(min="500", max="3000")
        body["items"].append({"uid": "i4", "text": "Bath temperature", "entry_type": "number",
                              "units": "°C"})
        c.post("/api/checklists", json=body)
        for uid, v in (("i2", "2900"), ("i4", "40")):
            assert c.post("/api/checklists/a1b2c3d4e5f6/value",
                          json={"item_uid": uid, "value": v}).status_code == 200
        t = {x["text"]: x for x in c.get("/api/checklists/trends").get_json()["trends"]}
        psi = t["Helium cylinder pressure"]
        assert (psi["min"], psi["max"], psi["state"]) == (500.0, 3000.0, "IN RANGE")
        assert psi["item_uids"] == ["i2"]
        assert "state" not in t["Bath temperature"], "a reading nobody set limits on was judged"


# ── the bookmark when nothing is set up ─────────────────────────────────────

class TestNothingSetUpYet:
    def test_a_lab_with_no_rounds_lands_on_opening(self, app, c, monkeypatch):
        """At 14:00 the clock says closing. But a lab with no rounds at all is
        setting up, and setup starts with the opening round: landing on Closing
        sends the first person to define the wrong one (T4a)."""
        when = datetime.now().replace(hour=14, minute=0, second=0, microsecond=0)
        monkeypatch.setattr(web_app, "_now", lambda: when)
        c.get("/api/checklists")                    # today, in memory: no rounds
        r = c.get("/checklists")
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/checklists/opening")


# ── what the old editor promised, kept ──────────────────────────────────────

class TestWhatTheOldEditorPromised:
    """From tests/test_checklist_editor_ui.py (retired with checklists.html):
    Ryan asked "It is permanent is it not?" and "let me click and drag to
    rearrange, moving an arrow a million times is tedious". Both still hold."""

    def test_it_says_a_save_is_for_every_day_and_keeps_recorded_days(self, c):
        page = text(_html(c, "/checklists/edit/new?slot=opening"))
        assert "every day" in page and "already recorded" in page

    def test_rows_are_dragged_by_a_grip_and_arrows_stay_for_keyboards(self, c):
        page = _html(c, "/checklists/edit/new?slot=opening")
        assert re.search(r'class="ed-n"[^>]*draggable="true"', page)
        assert 'class="icon-btn ed-up"' in page and 'class="icon-btn ed-down"' in page

    def test_headings_subtasks_and_days_are_editable(self, c):
        """The first cut of the old editor gave a new subtask no parent, so
        parent-to-child ticking was unreachable. The options sheet makes a row
        a heading or a subtask under an item above, and scopes its days."""
        page = _html(c, "/checklists/edit/new?slot=opening")
        sheet = re.search(r'<dialog class="sheet" id="item-sheet".*?</dialog>', page, re.S).group(0)
        assert re.findall(r'data-type="(\w+)"', sheet) == ["item", "header", "subtask"]
        assert 'id="opt-parent"' in sheet
        assert len(re.findall(r'class="chip opt-day"', sheet)) == 7

    def test_no_emoji_in_the_editor(self, c):
        page = _html(c, "/checklists/edit/new?slot=opening")
        for emoji in ("📅", "🗓", "⬆", "⬇", "🔼", "🔽", "✕", "🗑"):
            assert emoji not in page, emoji


# ── helpers ─────────────────────────────────────────────────────────────────

def without_templates(page: str) -> str:
    return re.sub(r"<template\b.*?</template>", "", page, flags=re.S)


def by_id(page: str, ident: str) -> str:
    m = re.search(r'<(\w+)[^>]*\bid="%s"[^>]*>(.*?)</\1>' % re.escape(ident), page, re.S)
    assert m, "no #%s" % ident
    return m.group(2)


def by_class_first(page: str, cls: str) -> str:
    m = re.search(r'<(\w+)[^>]*\bclass="[^"]*\b%s\b[^"]*"[^>]*>(.*?)</\1>' % re.escape(cls), page, re.S)
    assert m, "no .%s" % cls
    return m.group(2)
