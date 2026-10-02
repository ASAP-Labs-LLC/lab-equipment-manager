"""The round, `/checklists/<slot>` (ia-final §3.3, piece 9), by route and source.

What a person at the bench is promised, and why each line is tested:

* **`/checklists` is a bookmark to the round that is open now.** The tablet
  by the door is bookmarked once. Before 3 Oct it opened a page with both
  slots and the wrong one selected half the day; now it 302s to the slot due
  next (the same rule the nav's "Opening 3/5" uses, ``ui_live.round_summary``,
  so the nav and the redirect cannot point at different rounds). When today's
  round is not in memory yet it does NOT read LabCore to decide: the clock
  decides (opening before noon), because a redirect that waits on a slow
  queue is a blank tablet.
* **One heading.** Crumbs ``Checklists › Opening round`` in the top bar and
  one ``<h1>`` in the page head. The judges saw two titles on the tablet.
* **No add-item box on a live round** (P2, P15). Typing into a round you are
  doing is how three one-item "Closing round" checklists were made.
* **The tick is a ``<button aria-pressed>``**, drawn by static/js/round.js
  (tests/js/round.mjs is its harness). The page carries no inline script.
* **Drawing the page costs LabCore nothing.** The day's ticks come from the
  page cache when they are in memory and from the browser's own GET when
  they are not; the render never reads. A tablet left on the round, reloaded
  by a person, must not be a load on the queue the benches write through.
* **A cold cache is not an empty round.** With nothing in memory the page
  says it is reading, never "No opening round is set up".
* **Undo is logged as a new state.** The state row is one per item per day,
  so an untick overwrites who ticked it and when. The machine log keeps the
  line ("Cody's 07:58 tick undone by Ana"), so history is never rewritten.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

import web_app
from labcore_gateway import FakeLabCoreGateway
from web_app import create_app

ROOT = Path(__file__).resolve().parent.parent


class StubAuth:
    def login(self, u, p):
        return (u, "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


class CountingGateway(FakeLabCoreGateway):
    def __init__(self):
        super().__init__()
        self.calls = []

    def sql(self, *a, **k):
        self.calls.append(("sql", str(a[0])[:60] if a else ""))
        return super().sql(*a, **k)

    def read_sql(self, *a, **k):
        self.calls.append(("read_sql", str(a[0])[:60] if a else ""))
        return super().read_sql(*a, **k)

    def write(self, *a, **k):
        self.calls.append(("write", ""))
        return super().write(*a, **k)


def _app(gw=None, tmp_path=None):
    app = create_app(gw or FakeLabCoreGateway(), authenticator=StubAuth(), secret="s",
                     documents_root=str(tmp_path) if tmp_path else None)
    app.config["TESTING"] = True
    return app


def _at(monkeypatch, hh, mm=0):
    when = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
    monkeypatch.setattr(web_app, "_now", lambda: when)
    return when


def _signed_in(app, who="Cody"):
    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = who
    return c


def _round(c, slot, items, name=None, due="09:30"):
    r = c.post("/api/checklists", json={
        "name": name or slot.capitalize() + " round", "slot": slot, "due_time": due,
        "items": [{"text": t} for t in items]})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["checklist"]


def _html(c, path):
    r = c.get(path)
    assert r.status_code == 200, (path, r.status_code)
    return r.get_data(as_text=True)


# ── /checklists is a bookmark ───────────────────────────────────────────────

class TestTheBookmark:
    def test_it_redirects_to_the_round_due_next(self, tmp_path, monkeypatch):
        _at(monkeypatch, 8)
        app = _app(tmp_path=tmp_path)
        c = _signed_in(app)
        _round(c, "opening", ["Lights"])
        _round(c, "closing", ["Lights off"], due="17:00")
        c.get("/api/checklists")                          # today's round is in memory
        r = c.get("/checklists")
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/checklists/opening")

    def test_a_finished_opening_round_sends_you_to_closing(self, tmp_path, monkeypatch):
        _at(monkeypatch, 8)
        app = _app(tmp_path=tmp_path)
        c = _signed_in(app)
        op = _round(c, "opening", ["Lights"])
        _round(c, "closing", ["Lights off"], due="17:00")
        assert c.post(f"/api/checklists/{op['uid']}/toggle",
                      json={"item_uid": op["items"][0]["uid"], "checked": True}).status_code == 200
        c.get("/api/checklists")
        assert c.get("/checklists").headers["Location"].endswith("/checklists/closing")

    @pytest.mark.parametrize("hour, slot", [(7, "opening"), (11, "opening"), (12, "closing"), (18, "closing")])
    def test_a_cold_cache_is_decided_by_the_clock_not_by_a_read(self, tmp_path, monkeypatch, hour, slot):
        _at(monkeypatch, hour)
        gw = CountingGateway()
        app = _app(gw, tmp_path)
        before = len(gw.calls)
        r = app.test_client().get("/checklists")
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/checklists/" + slot)
        assert gw.calls[before:] == [], "the redirect read LabCore to decide where to go"

    def test_the_query_string_comes_along(self, tmp_path, monkeypatch):
        _at(monkeypatch, 8)
        r = _app(tmp_path=tmp_path).test_client().get("/checklists?kiosk=1")
        assert r.headers["Location"].endswith("/checklists/opening?kiosk=1")

    def test_only_opening_and_closing_are_rounds(self, tmp_path):
        c = _app(tmp_path=tmp_path).test_client()
        assert c.get("/checklists/lunch").status_code == 404


# ── the page ────────────────────────────────────────────────────────────────

@pytest.fixture
def page(tmp_path, monkeypatch):
    _at(monkeypatch, 8)
    app = _app(tmp_path=tmp_path)
    c = _signed_in(app)
    cl = _round(c, "opening", ["Lights and fume hoods on", "Nitrogen generator running"])
    c.get("/api/checklists")
    return {"app": app, "c": c, "cl": cl, "html": _html(c, "/checklists/opening")}


class TestTheRoundPage:
    def test_crumbs_then_the_seg_then_edit_this_round(self, page):
        top = re.search(r'<header class="topbar".*?</header>', page["html"], re.S).group(0)
        crumbs = re.search(r'<nav class="crumbs".*?</nav>', top, re.S).group(0)
        assert re.sub(r"<[^>]+>|\s+", " ", crumbs).split() == ["Checklists", "›", "Opening", "round"]
        assert 'href="/checklists/opening"' in top and 'href="/checklists/closing"' in top
        assert re.search(r'<a[^>]*href="/checklists/opening"[^>]*aria-current="page"', top), \
            "the seg must say which round this is"
        assert "Edit this round" in top
        # order: crumbs, seg, then the ghost
        assert top.index("crumbs") < top.index('id="slotOpening"') < top.index("Edit this round")

    def test_one_heading(self, page):
        assert len(re.findall(r"<h1\b", page["html"])) == 1
        assert re.search(r"<h1[^>]*>\s*Opening round\s*</h1>", page["html"])

    def test_the_closing_page_is_the_same_page_for_closing(self, page):
        h = _html(page["c"], "/checklists/closing")
        assert re.search(r"<h1[^>]*>\s*Closing round\s*</h1>", h)
        assert re.search(r'<a[^>]*href="/checklists/closing"[^>]*aria-current="page"', h)

    def test_no_add_item_box_on_a_live_round(self, page):
        h = page["html"].lower()
        for banned in ('id="newitem"', 'id="additem"', "add an item", "first item"):
            assert banned not in h, banned

    def test_the_bench_bar_is_there_and_live(self, page):
        h = page["html"]
        bar = re.search(r'<div class="bench-bar".*?</div>\s*</div>', h, re.S)
        assert bar, "no bench bar"
        assert 'id="bb-who"' in h and 'id="bb-live"' in h and 'id="bb-saved"' in h
        assert re.search(r'id="bb-live"[^>]*role="status"', h) or re.search(r'role="status"[^>]*id="bb-live"', h)
        assert "Not you?" in h
        assert "bench" in re.search(r'<body[^>]*class="([^"]*)"', h).group(1), \
            "the body must say the bench bar replaces the words strip"

    def test_ticking_as_is_the_signed_in_person_on_first_paint(self, page):
        assert re.search(r'id="bb-name"[^>]*>Cody<', page["html"])

    def test_signed_out_the_round_is_still_shown(self, page, tmp_path):
        anon = page["app"].test_client()
        h = _html(anon, "/checklists/opening")
        assert "Lights and fume hoods on" in h, "signed out, the round must still be visible"
        assert "Not signed in" in h

    def test_the_page_script_is_round_js_and_nothing_inline(self, page):
        h = page["html"]
        assert re.search(r'<script src="/static/js/round\.js\?v=', h)
        assert not re.search(r"<script(?![^>]*\bsrc=)", h), "inline script"
        assert not re.search(r"\son[a-z]+=", h), "inline handler"

    def test_the_days_ticks_ride_in_on_the_page_when_they_are_in_memory(self, page):
        m = re.search(r"data-round='([^']*)'", page["html"])
        assert m, "the round was not handed to the page"
        import html as _h
        day = json.loads(_h.unescape(m.group(1)))
        assert [cl["uid"] for cl in day["checklists"]] == [page["cl"]["uid"]]
        assert "state" in day

    def test_the_first_paint_has_the_rows_already(self, page):
        # server-drawn rows, so there is no "Loading…" flash on a warm cache
        rows = re.findall(r'class="item rrow[^"]*" data-item="([^"]+)"', page["html"])
        assert rows == [i["uid"] for i in page["cl"]["items"]]
        assert re.search(r'<button[^>]*class="tick"[^>]*aria-pressed="false"', page["html"])

    def test_a_cold_cache_says_it_is_reading_not_that_nothing_is_set_up(self, tmp_path, monkeypatch):
        _at(monkeypatch, 8)
        gw = CountingGateway()
        app = _app(gw, tmp_path)
        before = len(gw.calls)
        h = _html(_signed_in(app), "/checklists/opening")
        assert gw.calls[before:] == [], f"drawing the round read LabCore: {gw.calls[before:]}"
        assert 'data-state="reading"' in h
        assert "Reading today" in h
        # the "not set up" sentence is in the page (for round.js) but NOT shown
        assert re.search(r'id="round-empty"[^>]*\shidden', h), "a cold cache showed 'not set up'"
        assert not re.search(r'id="round-reading"[^>]*\shidden', h)

    def test_drawing_a_warm_page_reads_nothing(self, tmp_path, monkeypatch):
        _at(monkeypatch, 8)
        gw = CountingGateway()
        app = _app(gw, tmp_path)
        c = _signed_in(app)
        _round(c, "opening", ["Lights"])
        c.get("/api/checklists")
        before = len(gw.calls)
        _html(c, "/checklists/opening")
        assert gw.calls[before:] == []

    def test_an_empty_slot_says_so_and_offers_set_it_up(self, page):
        h = _html(page["c"], "/checklists/closing")
        assert "No closing round is set up." in h
        assert not re.search(r'id="round-empty"[^>]*\shidden', h), "an empty slot must say so"
        assert 'href="/checklists/edit/new?slot=closing"' in h

    def test_the_round_has_its_own_class_on_the_body(self, page):
        assert 'data-slot="opening"' in page["html"]


# ── Undo is logged ──────────────────────────────────────────────────────────

class TestUndoIsLogged:
    def test_an_untick_leaves_a_line_in_the_log(self, tmp_path, monkeypatch):
        from labcore_result import rows as read_rows
        _at(monkeypatch, 8)
        gw = FakeLabCoreGateway()
        app = _app(gw, tmp_path)
        c = _signed_in(app, "Cody")
        cl = _round(c, "opening", ["Lights and fume hoods on"])
        item = cl["items"][0]["uid"]
        assert c.post(f"/api/checklists/{cl['uid']}/toggle",
                      json={"item_uid": item, "checked": True}).status_code == 200
        ana = _signed_in(app, "Ana")
        assert ana.post(f"/api/checklists/{cl['uid']}/toggle",
                        json={"item_uid": item, "checked": False}).status_code == 200
        rows = read_rows(gw.read_sql(
            "SELECT test_name, detail FROM lem_machine_log WHERE kind = 'config'"))
        lines = [r for r in rows if r.get("test_name") == "checklist tick undone"]
        assert len(lines) == 1, rows
        d = json.loads(lines[0]["detail"])
        assert d["by"] == "Ana"
        assert d["item"] == "Lights and fume hoods on"
        assert d["checklist"] == "Opening round"
        assert d["was_by"] == "Cody", "who ticked it is the part the state row forgets"
        assert d["was_at"]

    def test_a_tick_costs_no_extra_write(self, tmp_path, monkeypatch):
        _at(monkeypatch, 8)
        gw = CountingGateway()
        app = _app(gw, tmp_path)
        c = _signed_in(app)
        cl = _round(c, "opening", ["Lights"])
        c.get("/api/checklists")
        before = len(gw.calls)
        c.post(f"/api/checklists/{cl['uid']}/toggle", json={"item_uid": cl["items"][0]["uid"], "checked": True})
        writes = [x for x in gw.calls[before:] if "lem_machine_log" in x[1]]
        assert writes == [], "a tick wrote a log line; only Undo is logged"
