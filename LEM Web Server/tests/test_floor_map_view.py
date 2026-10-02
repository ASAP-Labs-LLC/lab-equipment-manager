"""The floor map, inside the shell: ``/?view=map`` (ia-final §3.2, piece 12).

Until this piece the map was ``/floor``: 7,523 lines that were the wall, the
record and the admin console at once, with a drag lock that defaulted to
UNLOCKED the moment anybody signed in (lem-ui P15). ``/?view=map`` used to
302 there. Now it is the Instruments page's other view: the same page-head,
the same Needs-you answer (one computation, §0.2), a plan sized to the
placed bays, and an Arrange mode that has to be entered on purpose.

What the server owes the page, and what these tests hold:

* **The map is a view of Instruments, not a redirect.** Same template, same
  JSON island, the seg says which view is current, and there is still one h1.
* **Where each instrument stands rides in the answer it already reads.**
  ``where.pos`` is the saved bay (or null: "Not on the map"), and the answer
  says which level the lab opens on, so the plan does not need a second
  request (and so cannot disagree with the table about where anything is).
* **Nothing is draggable as the page arrives.** Arrange is a button,
  gated by sign-in, and the page carries no ``draggable`` element: moving an
  instrument is a mode somebody enters, never a side effect of a click that
  slipped (P15).
* **The 3D site is gone** (Ryan, decisions.md: "3D Site view: DELETE"). Not
  severed behind a switch any more: no files, no import map, no canvas, no
  toggle, nothing the floor page could fetch.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import demo_floor
from tests.test_ui_instruments import CountingGateway, _app, _seeded, build, machine

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATIC = ROOT / "static"


def _island(body: str) -> dict:
    m = re.search(r'<script type="application/json" id="instruments-data">(.*?)</script>',
                  body, re.S)
    assert m, "the page must carry its first paint's data"
    return json.loads(m.group(1))


class TestTheMapIsAViewOfInstruments:
    def test_it_is_served_in_place_not_redirected_to_the_old_floor(self, tmp_path):
        """The redirect was a placeholder ("until it lands the floor page IS
        the map"). It has landed: a 200, on the Instruments page."""
        app, _ = _seeded(tmp_path)
        r = app.test_client().get("/?view=map")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert 'data-view="map"' in body
        assert 'data-testid="floor-map"' in body
        assert "/static/js/plan.js" in body

    def test_the_seg_says_which_view_is_current(self, tmp_path):
        """One seg, two links, and aria-current on the view you are in, so
        the page never shows "List" pressed over a map."""
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        on_map = c.get("/?view=map").get_data(as_text=True)
        seg = re.search(r'<nav class="seg[^"]*view-seg".*?</nav>', on_map, re.S).group(0)
        assert re.search(r'<a href="/\?view=map"[^>]*aria-current="page"', seg), seg
        assert not re.search(r'<a href="/"[^>]*aria-current', seg), seg
        on_list = c.get("/").get_data(as_text=True)
        seg = re.search(r'<nav class="seg[^"]*view-seg".*?</nav>', on_list, re.S).group(0)
        assert re.search(r'<a href="/"[^>]*aria-current="page"', seg), seg
        assert 'href="/floor"' not in seg, "the seg must not send people to the old floor"

    def test_one_h1_and_the_same_needs_you_answer(self, tmp_path):
        """Say it once (§0.2): the map's Needs-you column reads the same
        island the list does, so "what needs me" cannot differ by view."""
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        body = c.get("/?view=map").get_data(as_text=True)
        assert len(re.findall(r"<h1\b", body)) == 1
        assert 'data-testid="needs-you"' in body
        assert _island(body)["needs_you"] == _island(c.get("/").get_data(as_text=True))["needs_you"]

    def test_the_list_table_is_not_drawn_under_the_map(self, tmp_path):
        """A map with the table hidden beneath it would be two pages in one
        document, and the table's ids would answer the list's script."""
        app, _ = _seeded(tmp_path)
        body = app.test_client().get("/?view=map").get_data(as_text=True)
        assert 'id="inst-tbl"' not in body
        assert 'data-testid="inst-table"' not in body

    def test_a_failed_read_says_so_on_the_map_too(self, tmp_path):
        """A failed read is never an empty result: before the first read the
        map view shows the same "not read yet" sentence as the list, never
        an empty plan that would read as "no instruments on this floor"."""
        app = _app(CountingGateway(), tmp_path)
        body = app.test_client().get("/?view=map").get_data(as_text=True)
        assert "Not read from LabCore yet." in body
        assert re.search(r'data-testid="floor-map"[^>]*\bhidden\b', body)


class TestWhereEachInstrumentStands:
    def test_the_answer_carries_the_saved_bay_or_null(self):
        """`where.pos` is the saved bay, verbatim; an instrument nobody
        placed is null, which the map says as "Not on the map"."""
        out = build([machine("a", pos=(4.1, 2.05)), machine("b", pos=None)])
        rows = {r["uid"]: r for r in out["instruments"]}
        assert rows["a"]["where"]["pos"] == [4.1, 2.05]
        assert rows["a"]["where"]["placed"] is True
        assert rows["b"]["where"]["pos"] is None
        assert rows["b"]["where"]["placed"] is False

    def test_a_bay_that_is_not_two_numbers_is_not_a_bay(self):
        """A half-written layout row ([x, None], or text) must not be drawn
        at the origin as if somebody had put it there."""
        out = build([machine("a", pos=None)])
        m = machine("b")
        m["pos"] = [1.0, None]
        out = build([m])
        assert out["instruments"][0]["where"]["pos"] is None
        assert out["instruments"][0]["where"]["placed"] is False

    def test_the_answer_says_which_level_the_lab_opens_on(self, tmp_path):
        """The map opens on the lab's default level (Settings › Floor and
        levels), the same one the old floor opened on."""
        app, _ = _seeded(tmp_path)
        data = app.test_client().get("/api/ui/instruments").get_json()
        snap = app.config["SNAPSHOTS"].get()
        assert data["default_level"] == snap["default_level"]
        assert data["default_level"] in {lv["uid"] for lv in data["levels"]}

    def test_unread_answers_carry_no_level_either(self):
        """Nothing read, nothing claimed: no default level out of thin air."""
        import ui_instruments
        assert ui_instruments.unread(None)["default_level"] == ""


class TestNothingMovesUntilArrangeIsEntered:
    def test_arrange_is_a_gated_button_and_nothing_is_draggable(self, tmp_path):
        """P15: the old floor's drag lock defaulted to UNLOCKED once you
        signed in, so a slipped click rearranged the lab. Here Arrange is a
        button that asks for sign-in, and no element arrives draggable."""
        app, _ = _seeded(tmp_path)
        body = app.test_client().get("/?view=map").get_data(as_text=True)
        btn = re.search(r'<button[^>]*id="arrange"[^>]*>', body)
        assert btn, "the map needs an Arrange button"
        assert 'data-gated="arrange the floor"' in btn.group(0)
        assert 'aria-pressed="false"' in btn.group(0)
        assert 'draggable="true"' not in body

    def test_the_arrange_bar_and_its_done_button_start_hidden(self, tmp_path):
        app, _ = _seeded(tmp_path)
        body = app.test_client().get("/?view=map").get_data(as_text=True)
        bar = re.search(r'<div[^>]*id="arrange-bar"[^>]*>', body)
        assert bar and "hidden" in bar.group(0)
        assert re.search(r'<button[^>]*id="arrange-done"', body)

    def test_a_signed_out_move_is_refused(self, tmp_path):
        """The page's mode is a convenience; the server is the gate."""
        app, _ = _seeded(tmp_path)
        r = app.test_client().post("/api/machines/x/position", json={"x": 0, "y": 0})
        assert r.status_code == 401


class TestTheDemoFloorUsesTheSavedPitch:
    def test_every_seeded_bay_is_a_whole_number_of_bays_at_the_floors_pitch(self, tmp_path):
        """Production saves bays 2.05 apart (4.1, 6.15, …) and the map reads
        a bay as round(v / 2.05). The demo used 0, 1, 2, … so on the map two
        neighbours (2 and 3) were the SAME bay, and one of them was spilled
        somewhere it was never put. The demo writes what production writes."""
        app, gw = _seeded(tmp_path)
        data = app.test_client().get("/api/ui/instruments").get_json()
        for r in data["instruments"]:
            pos = r["where"]["pos"]
            assert pos is not None, r["title"]
            for v in pos:
                k = v / 2.05
                assert abs(k - round(k)) < 1e-6, (r["title"], pos)


class TestThe3DSiteIsDeleted:
    """decisions.md: "3D Site view: DELETE (static/world/, static/vendor/
    three*.js and the Site toggle)". 3.1 MB that no route has drawn since
    2026-08-24, kept behind `SITE_VIEW = false`."""

    def test_the_files_are_gone(self):
        assert not (STATIC / "world").exists()
        assert not list((STATIC / "vendor").glob("three*.js")) if (STATIC / "vendor").exists() else True

    def test_the_floor_page_cannot_ask_for_it(self, tmp_path):
        app, _ = _seeded(tmp_path)
        for path in ("/floor", "/floor/classic"):
            self._cannot_ask(app.test_client().get(path).get_data(as_text=True))

    @staticmethod
    def _cannot_ask(body):
        assert '<script type="importmap">' not in body
        assert "world/index.js" not in body
        assert 'id="world"' not in body
        assert "SITE_VIEW" not in body
        assert 'id="btnView"' not in body, "the Plan/Site toggle goes with the site"

    def test_nothing_serves_or_names_the_world(self):
        """No template global builds an import map for files that are gone,
        and no template or script names them."""
        src = (ROOT / "web_app.py").read_text(encoding="utf-8")
        assert "worldmap" not in src and "/static/world/" not in src
        for p in list((ROOT / "templates").glob("*.html")) + list((STATIC / "js").glob("*.js")):
            text = p.read_text(encoding="utf-8")
            assert "static/world" not in text and "three.module" not in text, p.name

    def test_a_request_for_the_old_files_is_a_404(self, tmp_path):
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        assert c.get("/static/world/index.js").status_code == 404
        assert c.get("/static/vendor/three.module.min.js").status_code == 404
