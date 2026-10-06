"""Sign-in by password or card, and dragging on the floor map.

Four faults reported from the bench on 2026-08-03, all of them things an
operator hits in the first minute:

  1. the header never says who is signed in
  2. signing in is two chained prompt() boxes, with no path for a card swipe
  3. a standard's assays can't be changed — only their numbers
  4. the assay picker's checkboxes are stretched over their own labels

They were faults of the old floor page, and most of this
file read that template. Piece 14 deleted it, and the tests that only read it
went with it. What each promise is now, and where it is held:

  1. the shell's user chip names the person (tests/test_signin_in_place.py,
     test_ui_signin.py); /api/me is checked here.
  2. one sign-in sheet on every page, no prompt() anywhere shipped
     (test_signin_in_place.py, test_ia_guards.py); the card road is checked
     here, on the API both use.
  3. a standard is made in /quality's New standard sheet and its checks are
     changed on its page (test_ui_quality.py).
  4. the old picker is gone.

Kept here, unchanged: the drag rules, which hold in the shell's floor map.
"""
import re
from pathlib import Path

import pytest

from labcore_gateway import FakeLabCoreGateway


class StubAuth:
    """LabCore accepts a card code in either field; mimic that."""

    def login(self, u, p):
        if p == "good" or u == "CARD123" or p == "CARD123":
            return ("kaden", "tok", "")
        return (None, "", "Invalid credentials")

    def logout(self, t):
        pass


@pytest.fixture
def gw():
    return FakeLabCoreGateway()


@pytest.fixture
def client(gw):
    from web_app import create_app
    app = create_app(gw, authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture()
def floor_map():
    """The floor map's controller (the shell's ?view=map, piece 12): the one
    place an instrument is dragged now that the 3D site is deleted."""
    return (Path(__file__).parent.parent / "static" / "js"
            / "floor_map.js").read_text(encoding="utf-8")


# ── 1. who is signed in ─────────────────────────────────────────────────────

class TestWhoIsSignedIn:
    def test_api_me_names_the_user_once_signed_in(self, client):
        client.post("/api/login", json={"username": "kaden",
                                       "password": "good"})
        body = client.get("/api/me").get_json()
        assert body["authenticated"] is True
        assert body["user"] == "kaden"

    def test_api_me_is_anonymous_before_signing_in(self, client):
        body = client.get("/api/me").get_json()
        assert body["authenticated"] is False and body["user"] == ""


# ── 2. a card swipe signs in ─────────────────────────────────────────────────

class TestSignInDialog:
    def test_a_card_code_authenticates(self, client):
        r = client.post("/api/login", json={"username": "CARD123",
                                           "password": "CARD123"})
        assert r.status_code == 200 and r.get_json()["user"] == "kaden"

    def test_a_bad_password_is_rejected_with_a_message(self, client):
        r = client.post("/api/login", json={"username": "kaden",
                                            "password": "nope"})
        assert r.status_code == 401
        assert r.get_json()["error"]


# ── 7. dragging must not rebuild the floor ──────────────────────────────────
# The old floor's failure was `drawFloor()` per snap step: it cleared the SVG
# and re-created every tile, pipe and beacon, which is what made dragging
# stutter. The rule outlived the 3D site that carried it last, and holds in
# the shell's floor map (static/js/floor_map.js): move it locally while the
# pointer is down, commit once on drop, and never redraw under a hand.

class TestDragIsLocalUntilPlaced:
    def test_the_move_is_only_committed_on_release(self, floor_map):
        """`move()` is what writes to the server. Called per pointermove it
        would be one HTTP POST per pixel."""
        mv = re.search(r"addEventListener\('pointermove', \(ev\) => \{(.*?)\n    \}\);",
                       floor_map, re.S)
        assert mv, "the map has no pointermove handler"
        assert "move(" not in mv.group(1).replace("pointermove", "")
        end = re.search(r"function endDrag\(ev, cancelled\) \{(.*?)\n    \}", floor_map, re.S)
        assert end and "move(d.uid" in end.group(1)

    def test_a_refresh_never_redraws_under_a_hand(self, floor_map):
        """A repaint mid-drag would snap the instrument back."""
        assert "if (drag) return;" in floor_map

    def test_the_drop_snaps_to_a_whole_bay(self, floor_map):
        """Instruments land on whole bays (round(v / 2.05) and back), so the
        floor can never drift into a crooked mess; plan.mjs holds the sums."""
        assert "P.toSaved(x, y, lay.origin)" in floor_map


# ── 8. a drag is not a click ─────────────────────────────────────────────────

class TestDeselect:
    def test_finishing_a_drag_does_not_deselect(self, floor_map):
        """A pointerup that ends a drag must not also be read as a click
        (on the map, a click picks a bay up): the click after a drag is
        swallowed."""
        end = re.search(r"function endDrag\(ev, cancelled\) \{(.*?)\n    \}", floor_map, re.S)
        assert end and "suppressClick = true" in end.group(1)
        assert "if (suppressClick) return;" in floor_map
