"""The instrument panel must not repaint on every poll, and the hover card must
say when QC last ran.

Ryan, 2026-08-05: "the data keeps flickering when I look into a machine".

`load()` ended with `if (selected) select(selected)` — an unconditional rebuild
of the whole left rail on every refresh. At the old 30s timer that was a blink;
at 2s it is a flicker, and `select()` replaces `#railL.innerHTML` outright, so it
also snaps the open tab back to QC, loses the scroll position, and reflashes
"Loading…" in the trend.

The fix follows the pattern already in `lem.js`: a signature decides what
"changed" means, and only a real change repaints.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from labcore_gateway import FakeLabCoreGateway

SCRIPT = Path(__file__).parent / "js" / "panel.mjs"
FLOOR = Path(__file__).parent.parent / "templates" / "floor.html"


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


@pytest.fixture
def floor_html():
    from web_app import create_app
    app = create_app(FakeLabCoreGateway(), authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client().get("/floor").get_data(as_text=True)


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_the_panel_and_qc_helpers_behave():
    proc = subprocess.run(["node", str(SCRIPT)], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


class TestThePanelRedrawsOnlyOnChange:
    def test_the_refresh_is_guarded(self, floor_html):
        """An unconditional `select(selected)` at the end of load() is the
        flicker. It has to be behind a comparison."""
        tail = floor_html[floor_html.index("async function load()"):]
        tail = tail[:tail.index("\n}")]
        assert re.search(r"panelSignature", tail), (
            "load() does not consult panelSignature — the open panel will "
            "rebuild on every poll")

    def test_the_signature_is_remembered_between_polls(self, floor_html):
        assert re.search(r"(let|var)[^;\n]*\bPANEL_SIG\b", floor_html)


class TestTheHoverCardSaysWhenQcRan:
    def test_the_tip_shows_a_last_qc_row(self, floor_html):
        tip = floor_html[floor_html.index("function showTip("):]
        tip = tip[:tip.index("\n}")]
        assert "Last QC" in tip
        assert "lastQcAt" in tip

    def test_it_reads_the_specs_the_module_resolved(self, floor_html):
        """`effective_specs` is the only list carrying last_qc_at — qc_specs is
        a human's override and usually empty, and qc_targets is an assignment
        with no result on it."""
        helper = floor_html[floor_html.index("function lastQcAt("):]
        helper = helper[:helper.index("\n}")]
        assert "effective_specs" in helper


class TestTheActivityRailIsSteadyToo:
    """Same regression, other rail: renderFeed() replaced #railR wholesale on
    every refresh, which at 2s is a flickering list of the lab's activity."""

    def test_the_feed_redraws_only_when_it_changed(self, floor_html):
        feed = floor_html[floor_html.index("async function renderFeed("):]
        feed = feed[:feed.index("\n}")]
        assert "feedSignature" in feed

    def test_the_feed_signature_is_remembered(self, floor_html):
        """Declared at page scope — a signature that resets every call would
        compare a value against itself and never suppress anything."""
        assert re.search(r"(let|var)[^;\n]*\bFEED_SIG\b", floor_html)
