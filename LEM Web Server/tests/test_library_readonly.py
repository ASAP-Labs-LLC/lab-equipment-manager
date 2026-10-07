"""The band reads min – TARGET – max everywhere, and signed-out is obvious.

Ryan, 2026-09-21, with two screenshots:

  1. Hovering an instrument showed "ASTM D7236/D7094  52.3…58.0" in 11px at
     the bottom of the tip — the one number a person hovering wants (what
     should this instrument read, and how far either side is still fine) was
     the smallest thing on the card, the name was cut at 16 characters, and
     the target was not there at all.
  2. The QC samples library showed "52.28 … 57.96 C": a span with no centre.
     A standard IS its certified value; the span is derived from it.

So one helper renders every band the same way — low, then the target large
and prominent, then high, then the unit — and the tip, the library and the
assign sheet all use it. The tip shows every published band on the
instrument, with the whole test name.

  3. The QC library must be READABLE signed out. The read routes always were
     public; this pins that, and pins that opening the library never asks for
     a login.
  4. Signed out should look signed out. The header said nothing (the `who`
     span was hidden), Sign in looked like any other tool, and every button
     that needs an account looked exactly like one that does not. Now the body
     carries `anon`, the header says read-only, Sign in draws the eye, and the
     controls that change things are marked `gated` and dimmed — still
     clickable, because clicking is how you find out what to do (they open
     the sign-in prompt), and because opening a standard read-only is
     deliberately allowed (tests/test_floor_ui.py).

The old floor page this was checked against was deleted in piece 14.
Where each promise lives now:

  1 + 2. a band reads min – target – max with shared decimals: on the
     record `LEMRecord.bandText` (tests/js/record_logic.mjs), on /quality
     quality_logic.js (tests/js/quality_logic.mjs), on the walls
     `ui_wall.fmt_qc` (tests/test_ui_wall.py). Hover cards are cut
     (ia-final §10).
  3. readable signed out: the read routes, and the library's pages, below.
  4. signed out looks signed out: the shell's `anon` body, the user chip as
     Sign in, and dimmed-not-dead gated controls
     (tests/test_signin_in_place.py, test_ui_signin.py).
"""
import pytest

from labcore_gateway import FakeLabCoreGateway
from tests.test_floor_ui import StubAuth


@pytest.fixture
def client():
    from web_app import create_app
    app = create_app(FakeLabCoreGateway(), authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client()


# ── 3. readable signed out ─────────────────────────────────────────────────

class TestReadableSignedOut:
    def test_the_library_read_is_public(self, client):
        r = client.get("/api/qc-samples")
        assert r.status_code == 200
        assert "samples" in r.get_json()

    def test_the_test_names_the_library_needs_are_public(self, client):
        assert client.get("/api/test-names").status_code in (200, 503), \
            "503 only when LabCore cannot be asked; never 401"

    def test_opening_the_library_never_asks_for_a_login(self, client):
        """The library's pages (piece 8) answer signed out, with no detour to
        /signin: reading is never gated, only changing."""
        for path in ("/quality/standards", "/quality"):
            r = client.get(path)
            assert r.status_code == 200 and "Location" not in r.headers, path
