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

Checked the way the rest of the floor is checked: against the HTML served.
"""
import re

import pytest

from labcore_gateway import FakeLabCoreGateway
from tests.test_floor_ui import StubAuth, style_block


@pytest.fixture
def client():
    from web_app import create_app
    app = create_app(FakeLabCoreGateway(), authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def floor(client):
    return client.get("/floor").get_data(as_text=True)


@pytest.fixture
def all_css(client, floor):
    return style_block(floor) + "\n" + client.get("/static/lem.css").get_data(as_text=True)


def fn(floor, name):
    """The source of one top-level function on the floor page."""
    m = re.search(r"(?:async )?function " + re.escape(name) + r"\([^)]*\)\s*\{", floor)
    assert m, f"no function {name} on the floor"
    nxt = re.compile(r"\n(?:async )?function |\n\$\('#").search(floor, m.end())
    return floor[m.start():nxt.start() if nxt else len(floor)]


def px(css, selector, prop):
    """The pixel value of `prop` in the first rule for `selector`."""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"no rule for {selector}"
    v = re.search(prop + r"\s*:\s*([\d.]+)px", m.group(1))
    assert v, f"{selector} has no {prop} in px"
    return float(v.group(1))


# ── 1 + 2. one band, rendered one way ──────────────────────────────────────

class TestTheBand:
    def test_one_helper_draws_low_target_high_in_that_order(self, floor):
        body = fn(floor, "bandHtml")
        assert "lim3" in body
        assert re.search(r'class="tg"', body), "the target has its own class so it can be the big one"
        lo, tg, hi = body.index("low"), body.index("expected"), body.index("high")
        assert lo < tg < hi, "min, then TARGET, then max"

    def test_a_missing_target_is_a_dash_not_nan(self, floor):
        body = fn(floor, "bandHtml")
        assert "—" in body and "isFinite" in body

    def test_the_tip_shows_every_published_band_with_the_whole_name(self, floor):
        body = fn(floor, "showTip")
        assert "bandHtml(" in body
        assert ".slice(0, 16)" not in body, "the test name is no longer truncated"
        assert re.search(r"effective_specs[^\n]*\)\s*\.(map|filter)\(", body), \
            "every published band, not only the first"

    def test_the_tip_band_is_the_largest_thing_on_the_card(self, all_css):
        row = px(all_css, ".tip .row", "font-size")
        band = px(all_css, ".tip .lim3", "font-size")
        target = px(all_css, ".tip .lim3 .tg", "font-size")
        assert band > row, "the span outranks the status rows"
        assert target >= 18 and target > band, "the target is the headline"

    def test_the_library_rows_use_the_same_band(self, floor):
        assert "bandHtml(t.low, t.expected, t.high, t.units)" in fn(floor, "renderQcLib")

    def test_the_assign_sheet_uses_the_same_band(self, floor):
        assert "bandHtml(t.low, t.expected, t.high, t.units)" in fn(floor, "openQcSheet")

    def test_the_library_target_is_emphasised(self, all_css):
        m = re.search(r"\.qc \.lim \.lim3 \.tg\s*\{([^}]*)\}", all_css)
        assert m and "font-weight" in m.group(1)


# ── 3. readable signed out ─────────────────────────────────────────────────

class TestReadableSignedOut:
    def test_the_library_read_is_public(self, client):
        r = client.get("/api/qc-samples")
        assert r.status_code == 200
        assert "samples" in r.get_json()

    def test_the_test_names_the_library_needs_are_public(self, client):
        assert client.get("/api/test-names").status_code in (200, 503), \
            "503 only when LabCore cannot be asked; never 401"

    def test_opening_the_library_never_asks_for_a_login(self, floor):
        m = re.search(r"\$\('#btnQc'\)\.addEventListener\('click',\s*async\s*\(\)\s*=>\s*\{(.{0,300}?)\}\);",
                      floor, re.S)
        assert m and "requireAuth" not in m.group(1) and "AUTHED" not in m.group(1)

    def test_the_record_panels_library_button_is_not_gated(self, floor):
        m = re.search(r'<button class="([^"]*)" id="actQcLib"', floor)
        assert m and "gated" not in m.group(1)


# ── 4. signed out looks signed out ─────────────────────────────────────────

class TestSignedOutIsObvious:
    def test_the_body_says_whether_someone_is_signed_in(self, floor):
        assert "classList.toggle('anon', !AUTHED)" in fn(floor, "paintTools")

    def test_the_header_says_read_only_instead_of_going_quiet(self, floor):
        body = fn(floor, "paintTools")
        assert "who.hidden = !AUTHED" not in body, "hiding the span is how nobody noticed"
        assert "read-only" in body
        assert "ign in to make changes" in body

    def test_sign_in_draws_the_eye_when_nobody_is(self, floor, all_css):
        assert "classList.toggle('attn', !AUTHED)" in fn(floor, "paintTools")
        assert re.search(r"\.tool\.attn\s*\{[^}]*amber", all_css)

    @pytest.mark.parametrize("el", ["qcNew", "btnLock", "btnHours", "btnLevels", "actQc"])
    def test_controls_that_change_things_are_marked(self, floor, el):
        m = re.search(r'<button[^>]*id="' + el + r'"[^>]*>', floor)
        assert m and "gated" in m.group(0), f"#{el} changes the lab and must say so when signed out"

    def test_edit_and_changeover_are_marked_in_the_library(self, floor):
        body = fn(floor, "renderQcLib")
        assert re.search(r'class="tool gated" data-editsample', body)
        assert re.search(r'class="tool gated" data-changeover', body)

    def test_the_right_click_actions_are_marked_too(self, floor):
        assert re.search(r'<button class="gated" data-act="qc"', floor)
        assert re.search(r'<button class="gated" data-act="corr"', floor)

    def test_gated_controls_are_dimmed_but_still_answer(self, all_css):
        m = re.search(r"body\.anon \.gated\s*\{([^}]*)\}", all_css)
        assert m and "opacity" in m.group(1)
        assert "pointer-events" not in m.group(1), \
            "a dead button is indistinguishable from a broken one; the click opens sign-in"

    def test_the_read_only_pill_is_amber(self, all_css):
        assert re.search(r"\.who \.ro\s*\{[^}]*amber", all_css)
