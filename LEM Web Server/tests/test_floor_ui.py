"""The floor's sign-in and QC-editing surface.

Four faults reported from the bench on 2026-08-03, all of them things an
operator hits in the first minute:

  1. the header never says who is signed in
  2. signing in is two chained prompt() boxes, with no path for a card swipe
  3. a standard's assays can't be changed — only their numbers
  4. the assay picker's checkboxes are stretched over their own labels

The floor is one template, so these are checked the way the rest of the floor
is checked: against the HTML actually served.
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


@pytest.fixture
def floor(client):
    # The floor moved to /floor when the root became the mode selector.
    return client.get("/floor").get_data(as_text=True)


@pytest.fixture()
def world_index():
    """The world's integration seam — picking, dragging, and the plan."""
    return (Path(__file__).parent.parent / "static" / "world"
            / "index.js").read_text(encoding="utf-8")


@pytest.fixture()
def world_labels():
    """Where the instrument's state is actually painted onto the site."""
    path = (Path(__file__).parent.parent / "static" / "world" / "labels.js")
    if not path.exists():
        pytest.fail("static/world/labels.js is missing — the floor would "
                    "render a site with no statuses on it")
    return path.read_text(encoding="utf-8")


def style_block(html):
    m = re.search(r"<style>(.*?)</style>", html, re.S)
    return m.group(1) if m else ""


@pytest.fixture
def all_css(client, floor):
    """Everything the page is actually styled by: its own <style> plus the
    shared shell. Palette-level rules live in lem.css now."""
    shared = client.get("/static/lem.css").get_data(as_text=True)
    return style_block(floor) + "\n" + shared


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

    def test_the_header_has_somewhere_to_show_the_user(self, floor):
        assert 'id="who"' in floor

    def test_the_floor_actually_reads_the_name_it_is_given(self, floor):
        """/api/me has always returned `user`; the floor threw it away."""
        assert re.search(r"\bme\.user\b", floor)


# ── 2. a real sign-in dialog ────────────────────────────────────────────────

class TestSignInDialog:
    def test_there_is_a_sign_in_dialog(self, floor):
        assert 'id="authDlg"' in floor

    def test_it_has_username_and_password_fields(self, floor):
        assert 'id="auUser"' in floor and 'id="auPass"' in floor

    def test_the_password_field_is_masked(self, floor):
        m = re.search(r'<input[^>]*id="auPass"[^>]*>', floor)
        assert m and 'type="password"' in m.group(0)

    def test_a_card_swipe_has_its_own_field(self, floor):
        """A swipe types a code and presses Enter; it must not need the
        operator to guess which of two boxes it belongs in."""
        assert 'id="auCard"' in floor

    def test_signing_in_no_longer_uses_prompt(self, floor):
        """prompt() was the whole of the old sign-in UI."""
        assert "prompt('LabCore username" not in floor
        assert "prompt('Password" not in floor

    def test_a_card_code_authenticates(self, client):
        r = client.post("/api/login", json={"username": "CARD123",
                                           "password": "CARD123"})
        assert r.status_code == 200 and r.get_json()["user"] == "kaden"

    def test_a_bad_password_is_rejected_with_a_message(self, client):
        r = client.post("/api/login", json={"username": "kaden",
                                            "password": "nope"})
        assert r.status_code == 401
        assert r.get_json()["error"]


# ── 3. editing a standard's assays ──────────────────────────────────────────

class TestEditableAssays:
    def test_an_assay_row_can_be_repointed(self, floor):
        """The assay was a read-only <div>: the only way to change which test
        a row measured was to delete the row and add another."""
        assert "data-pickassay" in floor

    def test_the_assay_cell_is_no_longer_inert(self, floor):
        assert not re.search(r'<div class="assay" data-assay=', floor)

    def test_opening_a_standard_does_not_silently_require_auth(self, floor):
        """openSample() began with requireAuth(), so Edit did nothing at all
        when signed out — indistinguishable from a broken button."""
        m = re.search(r"function openSample\([^)]*\)\s*\{(.{0,200})", floor,
                      re.S)
        assert m and "requireAuth" not in m.group(1)

    def test_saving_a_standard_still_requires_auth(self, floor):
        """Read the library signed out; changing it needs an account."""
        m = re.search(r"#sampleSave'\)\.addEventListener\('click',"
                      r"\s*async\s*\(\)\s*=>\s*\{(.{0,200})", floor, re.S)
        assert m and "requireAuth" in m.group(1)


# ── 4. the assay picker's checkboxes ────────────────────────────────────────

class TestAssayPickerIsLegible:
    def test_the_blanket_input_rule_spares_checkboxes(self, all_css):
        """`input,select{width:100%;padding;background;border}` also matched
        every checkbox in the picker, inflating each one over its own label."""
        css = all_css
        rule = re.search(r"(^|\})\s*(input\s*,\s*select|select\s*,\s*input)"
                         r"\s*\{", css)
        assert rule is None, ("a bare `input,select` rule still applies "
                              "width/padding to checkboxes")

    def test_checkboxes_are_sized_for_themselves(self, all_css):
        css = all_css
        assert re.search(r'input\[type=("|\')?checkbox', css), \
            "no checkbox-specific sizing anywhere in the stylesheet"

    def test_text_inputs_still_fill_their_cell(self, all_css):
        """The fix must not shrink the real fields."""
        css = all_css
        assert "width:100%" in css.replace(" ", "")


# ── 5. lab hours must be settable from the floor ────────────────────────────

class TestLabHoursUi:
    def test_there_is_a_way_in(self, floor):
        assert 'id="btnHours"' in floor

    def test_the_dialog_exists(self, floor):
        assert 'id="schedDlg"' in floor

    def test_working_days_are_pickable(self, floor):
        assert 'id="schedDays"' in floor

    def test_hours_have_fields(self, floor):
        assert 'id="schedOpens"' in floor and 'id="schedCloses"' in floor

    def test_holidays_can_be_added_and_removed(self, floor):
        assert 'id="holAdd"' in floor and "data-delhol" in floor

    def test_a_closed_module_is_not_painted_as_a_fault(self, floor):
        """`closed` must be handled everywhere `stopped` is, or the floor
        still shows an alarm for a shut lab."""
        assert floor.count("'closed'") >= 3


# ── 6. status colours: SERVICE and DEAD-LINE must not read as "no data" ──────

class TestStatusColours:
    def test_service_is_purple_not_grey(self, floor):
        """Grey belongs to UNKNOWN. SERVICE is a decision someone made."""
        m = re.search(r"SERVICE:'(#[0-9a-fA-F]{6})'", floor)
        assert m, "no SERVICE colour in the palette"
        assert m.group(1).lower() == "#a855f7"

    def test_unknown_keeps_grey_to_itself(self, floor):
        m = re.search(r"UNKNOWN:'(#[0-9a-fA-F]{6})'", floor)
        assert m and m.group(1).lower() == "#6b7280"

    def test_service_and_unknown_are_different_colours(self, floor):
        svc = re.search(r"SERVICE:'(#[0-9a-fA-F]{6})'", floor).group(1)
        unk = re.search(r"UNKNOWN:'(#[0-9a-fA-F]{6})'", floor).group(1)
        assert svc.lower() != unk.lower()

    def test_dead_line_still_reads_as_a_barrier(self, world_labels):
        """A dead-lined instrument is a barrier, not one more coloured lamp.
        The SVG floor said that with a hazard-stripe pattern; the world says it
        with hazard striping on the building's signage. The statement has to
        survive the change of medium."""
        assert "hazard" in world_labels.lower()
        assert "#e2483d" in world_labels.lower()

    def test_the_palette_survived_the_move_to_3d(self, world_labels):
        """The six status colours are not open for reinterpretation just
        because the floor is rendered now."""
        for colour in ("#21c071", "#f5c542", "#f85b5b", "#a855f7",
                       "#e2483d", "#6b7280"):
            assert colour in world_labels.lower(), f"{colour} missing"


# ── 7. dragging must not rebuild the world ──────────────────────────────────
# The old floor's failure was `drawFloor()` per snap step: it cleared the SVG
# and re-created every tile, pipe and beacon, which is what made dragging
# stutter. The 3D world has the same shape of danger and a bigger price —
# `_replan()` regenerates terrain pads, track and forest — so the rule is
# unchanged: move it locally while the pointer is down, commit once on drop.

class TestDragIsLocalUntilPlaced:
    def test_the_page_no_longer_drags_anything_itself(self, floor):
        """The world raycasts the pointer onto the ground; a second drag
        implementation in the page would fight it."""
        assert "setAttribute('transform'" not in floor
        assert "$('#stage').addEventListener('pointermove'" not in floor

    def test_the_move_is_only_committed_on_release(self, world_index):
        """`onMove` is what writes to the server. Called per pointermove it
        would be one HTTP POST per pixel."""
        move = re.search(r"pointermove'[,\s]*\s*e\s*=>\s*\{(.*?)\n    \}\);",
                         world_index, re.S)
        assert move, "the world has no pointermove handler"
        assert "onMove" not in move.group(1)
        up = re.search(r"'pointerup'[,\s]*\s*e\s*=>\s*\{(.*?)\n    \}\);",
                       world_index, re.S)
        assert up and "onMove" in up.group(1)

    def test_the_drag_reports_itself_while_it_is_moving(self, world_index):
        """So the page can hide the tooltip and hold off its 2s refresh —
        a reload mid-drag would snap the instrument back."""
        assert "emit('dragging'" in world_index

    def test_the_drop_snaps_to_a_whole_bay(self, world_index):
        """Instruments land on whole grid squares, so the floor can never
        drift into a crooked mess."""
        assert re.search(r"Math\.round\(p\.x / METRES_PER_BAY / BAY\)", world_index)

    def test_the_drop_is_what_saves_the_position(self, floor):
        m = re.search(r"async onMove\(uid, gx, gy\) \{(.*?)\n  \},", floor, re.S)
        assert m, "the bridge has no onMove"
        assert "/position" in m.group(1)
        assert m.group(1).count("drawFloor()") == 1

    def test_a_locked_floor_cannot_be_dragged(self, floor):
        m = re.search(r"canDrag:\s*\(\)\s*=>\s*(.*)", floor)
        assert m and "LOCKED" in m.group(1) and "AUTHED" in m.group(1)


# ── 8. getting out of an instrument, and the tabs in its record ──────────────

class TestDeselect:
    def test_the_record_has_a_close_button(self, floor):
        assert 'id="railClose"' in floor

    def test_clicking_bare_ground_deselects(self, floor):
        """Getting OUT of an instrument has to be as easy as getting in. The
        world reports a click that hit nothing as a pick of null."""
        m = re.search(r"onSelect\(uid\) \{(.*?)\n  \},", floor, re.S)
        assert m, "the bridge has no onSelect"
        assert "deselect()" in m.group(1)

    def test_a_click_on_an_instrument_does_not_deselect(self, floor):
        m = re.search(r"onSelect\(uid\) \{(.*?)\n  \},", floor, re.S)
        assert "if (!uid)" in m.group(1), "null must be the only deselect path"
        assert "select(m)" in m.group(1)

    def test_finishing_a_drag_does_not_deselect(self, world_index):
        """A pointerup that ends a drag must not also be read as a click on
        bare ground."""
        up = re.search(r"'pointerup'[,\s]*\s*e\s*=>\s*\{(.*?)\n    \}\);",
                       world_index, re.S)
        assert up and re.search(r"!moved", up.group(1))

    def test_deselect_restores_the_global_view(self, floor):
        m = re.search(r"function deselect\(\)\s*\{(.*?)\n\}", floor, re.S)
        assert m and "renderOverview()" in m.group(1)

    def test_deselect_only_calls_functions_that_exist(self, floor):
        """`renderRecent` was a typo for `renderFeed` and would have thrown."""
        m = re.search(r"function deselect\(\)\s*\{(.*?)\n\}", floor, re.S)
        called = set(re.findall(r"\b(\w+)\(\)", m.group(1)))
        for name in called - {"deselect"}:
            assert re.search(rf"function {name}\(", floor), f"{name}() undefined"


class TestRecordTabs:
    def test_there_are_three_tabs(self, floor):
        for name in ("qc", "maint", "sop"):
            assert f'data-tab="{name}"' in floor, name

    def test_each_tab_has_a_pane(self, floor):
        for name in ("qc", "maint", "sop"):
            assert f'id="tab-{name}"' in floor, name

    def test_the_sop_tab_is_an_honest_placeholder(self, floor):
        block = floor[floor.index('id="tab-sop"'):][:500]
        assert "placeholder" in block.lower()

    def test_pm_and_cal_show_scheduled_and_completed(self, floor):
        assert 'id="maintNow"' in floor and 'id="maintDone"' in floor

    def test_completed_history_scrolls_on_its_own(self, floor, all_css):
        assert 'class="scroller" id="maintDone"' in floor
        css = all_css
        assert re.search(r"\.scroller\{[^}]*overflow-y:auto", css)

    def test_the_right_click_actions_are_also_buttons(self, floor):
        """They were reachable only by right-clicking the instrument."""
        for ident in ("actQc", "actQcLib", "actAddPm", "actAddCal"):
            assert f'id="{ident}"' in floor, ident

    def test_completions_are_markable_from_the_record(self, floor):
        m = re.search(r"async function loadMaintPanel\(m\)\s*\{(.*?)\n\}",
                      floor, re.S)
        assert m and "data-done=" in m.group(1)

    def test_the_panel_reads_the_history_endpoint(self, floor):
        assert "maintenance-history" in floor


# ── 9. modal dialogs must be centred ────────────────────────────────────────

class TestDialogsAreCentred:
    def test_the_reset_that_broke_centring_is_compensated(self, all_css):
        """A modal <dialog> is centred by the UA's own `margin:auto`. The
        `*{margin:0}` reset wiped it, so every popup sat in a corner."""
        css = all_css
        assert re.search(r"\*\{[^}]*margin:0", css), "reset changed; re-check"
        rule = re.search(r"^\s*dialog\{([^}]*)\}", css, re.M)
        assert rule, "no dialog rule"
        assert "margin:auto" in rule.group(1).replace(" ", "")

    def test_a_tall_dialog_scrolls_instead_of_running_off_screen(self, all_css):
        css = all_css
        rule = re.search(r"^\s*dialog\{([^}]*)\}", css, re.M).group(1)
        assert "max-height" in rule and "overflow:auto" in rule.replace(" ", "")

    def test_the_wide_variant_does_not_lose_the_centring(self, all_css):
        """dialog.wide only overrides width, so it must not reset margin."""
        css = all_css
        wide = re.search(r"dialog\.wide\{([^}]*)\}", css)
        assert wide and "margin" not in wide.group(1)

    def test_every_dialog_uses_the_shared_rule(self, floor):
        """No dialog should carry its own inline positioning."""
        for tag in re.findall(r"<dialog[^>]*>", floor):
            assert "style=" not in tag, tag


# ── 10. the QC control chart must be a chart over TIME ──────────────────────

class TestQcChartAxes:
    def trend(self, floor):
        m = re.search(r"async function drawTrend\(m\)\s*\{(.*?)\n\}\n", floor,
                      re.S)
        assert m, "drawTrend not found"
        return m.group(1)

    def test_x_is_scaled_by_timestamp_not_index(self, floor):
        """Even index spacing put a month-old result as far from its neighbour
        as one an hour later, hiding the drift the chart exists to show."""
        body = self.trend(floor)
        assert "Date.parse" in body
        assert re.search(r"\(times\[i\]\s*-\s*t0\)\s*/\s*\(t1\s*-\s*t0\)", body)

    def test_it_still_draws_a_single_point(self, floor):
        body = self.trend(floor)
        assert "pts.length < 2" in body

    def test_identical_timestamps_fall_back_to_even_spacing(self, floor):
        """Several results in the same instant can't be separated by time."""
        body = self.trend(floor)
        assert "usable" in body and "t1 > t0" in body

    def test_the_limits_are_labelled_on_the_y_edge(self, floor):
        """Printed in the bottom corners they read as an X range — which is
        exactly how they were misread."""
        body = self.trend(floor)
        assert body.count('text-anchor="end"') >= 3

    def test_the_bottom_row_shows_a_date_range(self, floor):
        body = self.trend(floor)
        assert "dayLabel" in body
        assert "→" in body

    def test_the_bottom_row_no_longer_shows_the_limits(self, floor):
        body = self.trend(floor)
        lim = re.search(r'<div class="lim">(.*?)</div>', body, re.S)
        assert lim and "lo.toFixed" not in lim.group(1)
        assert "hi.toFixed" not in lim.group(1)

    def test_each_point_says_when_it_was(self, floor):
        assert "<title>" in self.trend(floor)

    def test_there_are_axis_lines(self, floor):
        body = self.trend(floor)
        assert body.count("<line") >= 3       # expected + both axes
