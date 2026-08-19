#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The 3D floor's static assets, and how the page reaches them.

The floor is a real-time 3D world now, not an SVG drawing. It loads as ES
modules, which means every module resolves through an import map — and that map
is the ONLY place a version can be attached, because a static `import` cannot
carry a query string of its own. Get this wrong and a lab screen runs last
week's terrain against this week's renderer, which is the exact failure
`static_version` was added for (see CLAUDE.md).
"""
import json
import os
import re

import pytest

from web_app import create_app
from labcore_gateway import FakeLabCoreGateway

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(os.path.dirname(HERE), "static")


@pytest.fixture()
def client():
    app = create_app(FakeLabCoreGateway())
    app.config["TESTING"] = True
    return app.test_client()


def _importmap(html):
    m = re.search(r'<script type="importmap">(.*?)</script>', html, re.S)
    assert m, "the floor must publish an import map"
    return json.loads(m.group(1))["imports"]


class TestTheImportMap:
    def test_three_is_vendored_not_a_cdn(self):
        """A lab bench has no internet. A CDN import is a blank floor."""
        path = os.path.join(STATIC, "vendor", "three.module.min.js")
        assert os.path.exists(path), "three.js must be vendored into static/"
        assert os.path.getsize(path) > 100_000

    def test_every_module_is_mapped_and_versioned(self, client):
        imports = _importmap(client.get("/floor").get_data(as_text=True))
        assert "three" in imports
        assert imports["three"].startswith("/static/vendor/three.module.min.js?v=")
        # Every file in static/world is reachable by a bare specifier, so a
        # module can `import {x} from "world/rail.js"` and still get a URL that
        # changes when the file does.
        for name in sorted(os.listdir(os.path.join(STATIC, "world"))):
            if not name.endswith(".js"):
                continue
            spec = "world/" + name
            assert spec in imports, f"{spec} is not in the import map"
            assert re.search(r"\?v=[0-9a-f]{6,}$", imports[spec]), \
                f"{spec} is not cache-busted"

    def test_a_changed_module_gets_a_new_url(self, client, tmp_path):
        first = _importmap(client.get("/floor").get_data(as_text=True))
        target = os.path.join(STATIC, "world", "engine.js")
        original = open(target, "rb").read()
        try:
            with open(target, "ab") as fh:
                fh.write(b"\n// touched by a test\n")
            second = _importmap(client.get("/floor").get_data(as_text=True))
        finally:
            with open(target, "wb") as fh:
                fh.write(original)
        assert first["world/engine.js"] != second["world/engine.js"]

    def test_a_missing_file_never_takes_the_page_down(self, client):
        """static_version's discipline: a packaging slip is not an outage."""
        html = client.get("/floor").get_data(as_text=True)
        assert html.count("<script type=\"importmap\">") == 1


class TestArrangingTheFloor:
    """Dragging an instrument always worked, but only for someone who already
    knew the map had a shared lock and that a building could be dragged at all.
    None of that was on the screen, so the feature existed and could not be
    found."""

    def test_there_is_a_way_in(self, client):
        html = client.get("/floor").get_data(as_text=True)
        assert 'id="btnArrange"' in html

    def test_the_whole_floor_can_be_laid_out_at_once(self, client):
        html = client.get("/floor").get_data(as_text=True)
        for preset in ('id="arrGrid"', 'id="arrCompact"', 'id="arrRow"'):
            assert preset in html

    def test_it_manages_the_shared_lock_itself(self, client):
        """Entering unlocks and leaving re-locks: an unlocked floor is one
        anyone can rearrange by accident, and the lock is shared with every
        other screen looking at the lab."""
        html = client.get("/floor").get_data(as_text=True)
        m = re.search(r"async function setArrange\(on\) \{(.*?)\n\}", html, re.S)
        assert m, "setArrange is missing"
        assert "requireAuth()" in m.group(1)
        assert '"/api/map"' in m.group(1) or "'/api/map'" in m.group(1)
        assert "locked: false" in m.group(1) and "locked: true" in m.group(1)

    def test_positions_are_saved_the_same_way_a_drag_saves_them(self, client):
        """One way a position is ever written, so the two paths cannot drift."""
        html = client.get("/floor").get_data(as_text=True)
        m = re.search(r"async function applyArrangement\(kind\) \{(.*?)\n\}",
                      html, re.S)
        assert m and "/position" in m.group(1)


class TestTheFloorIsThreeDimensionalOnly:
    def test_the_stage_is_a_canvas(self, client):
        html = client.get("/floor").get_data(as_text=True)
        assert 'id="world"' in html and "<canvas" in html

    def test_there_is_no_two_dimensional_mode(self, client):
        """Ryan: "No 2d mode, just 3d." The toggle is gone, not hidden."""
        html = client.get("/floor").get_data(as_text=True)
        assert 'id="btn2d"' not in html
        assert 'id="btn3d"' not in html
        assert "viewtoggle" not in html

    def test_nothing_still_reaches_for_the_removed_controls(self, client):
        """Checking the markup is not enough, and this is not hypothetical:
        removing the toggle left `$('#btn3d').addEventListener(...)` behind,
        which threw on page load and killed every listener registered after
        it — sign-in, lab hours, the whole tail of the file. The page looked
        fine and half its buttons were dead."""
        html = client.get("/floor").get_data(as_text=True)
        for gone in ("btn2d", "btn3d", "flyTo(", "FLAT_START"):
            assert gone not in html, f"{gone} outlived the 2D mode"

    def test_the_page_registers_its_listeners_without_throwing(self, client):
        """Everything after a top-level throw never runs. Anything the script
        addresses by id at load time must exist in the same document."""
        html = client.get("/floor").get_data(as_text=True)
        ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', html))
        for name in re.findall(r"\$\('#([A-Za-z0-9_-]+)'\)\.addEventListener",
                               html):
            assert name in ids, f"#{name} is addressed but never rendered"

    def test_the_svg_floor_is_gone(self, client):
        html = client.get("/floor").get_data(as_text=True)
        assert 'id="floor"' not in html

    def test_the_lab_data_ui_is_untouched(self, client):
        """The map is replaced. Everything around it is not."""
        html = client.get("/floor").get_data(as_text=True)
        for kept in ('id="railL"', 'id="railR"', 'id="tally"', 'id="btnQc"',
                     'id="btnHours"', 'id="btnExport"', 'id="btnMax"',
                     'id="btnLock"', 'id="btnDebug"', 'id="qcLib"',
                     'id="corrDlg"', 'id="maintDlg"', 'id="schedDlg"',
                     'id="authDlg"', 'id="qcSheet"', 'id="simSheet"'):
            assert kept in html, f"{kept} went missing from the floor page"
