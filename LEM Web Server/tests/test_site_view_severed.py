#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The 3D site is severed. The SVG floor plan is the floor.

Ryan, 2026-08-24: "just dont have it render trains in 3d okay? We are going to
focus on the SVG rendering."

This is a switch, not a removal. `static/world/` stays exactly where it is,
still in the import map, still tested by `test_world_assets.py` — the world is
disconnected, not deleted, and `SITE_VIEW = true` brings it back with nothing
else touched.

What has to be true while it is off is only this: the browser must not FETCH
it. A static `import` at the top of a module runs whether or not anything below
it is reached, so guarding the `new LEMWorld(...)` call and leaving the import
alone would still pull three.js and every subsystem — half a dozen megabytes
onto a bench PC to build a renderer that is then never started. The import has
to be dynamic, inside the guard.

`tests/js/floorboot.mjs` covers the other half: what the page actually shows
once it has settled. See `test_reorder_logic.py::test_the_floor_script_actually_boots`.
"""
import re
from pathlib import Path

import pytest

from web_app import create_app
from labcore_gateway import FakeLabCoreGateway

FLOOR = Path(__file__).parent.parent / "templates" / "floor.html"


@pytest.fixture()
def client():
    app = create_app(FakeLabCoreGateway())
    app.config["TESTING"] = True
    return app.test_client()


def _page(client):
    return client.get("/floor").get_data(as_text=True)


class TestTheWorldIsNotLoaded:
    def test_nothing_statically_imports_the_world(self, client):
        """A static import downloads three.js whether or not it is used."""
        html = _page(client)
        static_imports = re.findall(
            r"^\s*import\s.*?from\s+['\"]world/index\.js['\"]", html, re.M)
        assert not static_imports, (
            "floor.html still imports the world at module top level: "
            f"{static_imports} — three.js downloads even though nothing "
            "starts the renderer")

    def test_the_world_is_reached_only_behind_the_switch(self, client):
        """It must still be reachable — this is a sever, not a deletion."""
        html = _page(client)
        assert "import('world/index.js')" in html or \
               'import("world/index.js")' in html, \
            "flipping SITE_VIEW back on must have something to load"

    def test_three_is_still_vendored_and_mapped(self, client):
        """Severed, not deleted. `test_world_assets.py` keeps proving the map
        is correct; this just states out loud that turning the floor's 3D off
        is not licence to start pruning the world's files."""
        html = _page(client)
        assert "/static/vendor/three.module.min.js?v=" in html
        assert "world/index.js?v=" in html


class TestTheSwitch:
    def test_it_is_present_and_off(self):
        """One line, one word, and the site view is back. If this assertion is
        what fails when someone restores the 3D floor, they are done."""
        src = FLOOR.read_text(encoding="utf-8")
        assert re.search(r"const SITE_VIEW = false;", src), \
            "the severing switch must stay a single named constant"

    def test_the_plan_is_not_left_waiting_for_a_world(self):
        """The remembered view is applied inside `__floorBridge.attach(world)`,
        which never runs now. Something else has to show the plan, or the floor
        is a blank stage."""
        src = FLOOR.read_text(encoding="utf-8")
        outside_attach = src.split("attach(world)", 1)[-1]
        assert "setView('plan'" in outside_attach, \
            "nothing shows the plan when the world never attaches"
