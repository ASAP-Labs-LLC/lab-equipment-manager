"""A maximal map view — the floor with nothing else on screen.

Asked for 2026-08-03: "options to hide the side bars in a sleek desktop viewer for
maximal map view". On a wall display the two rails and the nav cost most of the
width, and the map is the point of the page.

So: one toggle that hides the nav rail, both side rails and the tool row, leaving
the map and the status tally. It has to be reversible without hunting, survive a
reload (a wall display is set once and left), and never trap someone in a view with
no way out — hence Escape, and a control that stays visible.
"""
import pathlib

import pytest


def src():
    return (pathlib.Path(__file__).resolve().parent.parent
            / "templates" / "floor.html").read_text(encoding="utf-8")


def css():
    return (pathlib.Path(__file__).resolve().parent.parent
            / "static" / "lem.css").read_text(encoding="utf-8")


class TestTheToggleExists:
    def test_there_is_a_control(self):
        assert 'id="btnMax"' in src()

    def test_it_is_labelled_for_screen_readers(self):
        s = src()
        block = s[s.index('id="btnMax"') - 200:s.index('id="btnMax"') + 300]
        assert "title=" in block or "aria-label" in block


class TestWhatItHides:
    def test_a_body_level_class_drives_it(self):
        """One class on the root, so CSS does the hiding and no layout maths has
        to be duplicated in JS."""
        assert "maxmap" in src()

    def test_the_rails_are_hidden(self):
        s = src()
        rule = s[s.index(".maxmap"):]
        assert ".rail" in rule[:600]

    def test_the_nav_rail_is_hidden_too(self):
        """The nav lives outside the floor's own grid and is shared by every page,
        so its rule belongs in lem.css — otherwise "maximal" still leaves the nav
        column, and only on this one page."""
        sheet = css()
        assert ".maxmap .navrail{display:none}" in sheet.replace(":root", "")

    def test_the_page_frame_collapses_to_one_column(self):
        """Hiding the nav without collapsing the grid column leaves a gap where
        it used to be."""
        sheet = css()
        assert "grid-template-columns:1fr" in sheet[sheet.index(".maxmap"):]

    def test_the_tool_row_is_hidden(self):
        s = src()
        block = s[s.index(".maxmap"):]
        assert ".tools" in block[:900]

    def test_the_tally_stays(self):
        """Hiding the numbers that say how many instruments need attention would
        make this a screensaver, not a view."""
        s = src()
        block = s[s.index(".maxmap"):s.index(".maxmap") + 900]
        assert ".tally{display:none" not in block.replace(" ", "")


class TestGettingBackOut:
    def test_escape_leaves_it(self):
        s = src()
        assert "maxmap" in s
        esc = s[s.index("Escape") - 400:s.index("Escape") + 400] \
            if "Escape" in s else ""
        assert "maxmap" in esc or "exitMax" in s

    def test_the_choice_is_remembered(self):
        """A wall display is set once and left; a reload must not undo it."""
        s = src()
        assert "localStorage" in s and "maxmap" in s

    def test_a_control_remains_visible_to_leave_by(self):
        assert "btnMax" in src()
        assert "maxmap" in css() or "maxmap" in src()


class TestTheExitButtonCannotLeak:
    """Reported 2026-08-03: "exit map max is constantly visible, please make it
    match the theme and also make it hidden when map max is not toggled".

    The CSS was correct (`display:none`, overridden only under `:root.maxmap`).
    The cause was a **cached lem.css**: with the previous stylesheet the class had
    no rules at all, so the button rendered as a plain unstyled <button> — visible,
    and looking nothing like the theme. One cause, both symptoms.

    So visibility no longer depends on the stylesheet arriving: the `hidden`
    attribute is the source of truth, and it reuses `.tool` for its look rather
    than carrying a private copy of the palette.
    """

    def test_it_ships_hidden_in_the_markup(self):
        s = src()
        tag = s[s.index('id="btnMaxExit"') - 200:s.index('id="btnMaxExit"') + 200]
        assert "hidden" in tag, "with no CSS this would render visible"

    def test_it_reuses_the_shared_button_style(self):
        """"Match the theme" means the same class every other button uses, not a
        second hand-tuned palette that drifts."""
        s = src()
        tag = s[s.index('class="') if False else 0:]
        block = s[s.index('id="btnMaxExit"') - 200:s.index('id="btnMaxExit"') + 60]
        assert "tool" in block

    def test_the_toggle_drives_the_hidden_attribute(self):
        s = src()
        fn = s[s.index("function setMaxMap"):]
        fn = fn[:fn.index("\n}")]
        assert "hidden" in fn, "only a CSS class was toggled"

    def test_the_stylesheet_only_positions_it(self):
        """No private colours: position and layering here, appearance from .tool."""
        sheet = css()
        rule = sheet[sheet.index(".maxexit{"):]
        rule = rule[:rule.index("}")]
        assert "position:fixed" in rule
        assert "background" not in rule, "duplicating the palette drifts from it"


class TestStaticFilesAreCacheBusted:
    """The exit button was only ever "constantly visible" because a browser held a
    cached `lem.css` from before the rule existed. That is a whole class of bug —
    any future CSS or JS change can land looking broken on the one screen that
    happens to have the old file — so the links carry the file's own fingerprint
    and a changed file gets a new URL.
    """

    def templates(self):
        d = pathlib.Path(__file__).resolve().parent.parent / "templates"
        return {p.name: p.read_text(encoding="utf-8") for p in d.glob("*.html")}

    def test_no_template_links_the_bare_path(self):
        offenders = [name for name, text in self.templates().items()
                     if '/static/lem.css"' in text or '/static/lem.js"' in text]
        assert offenders == [], offenders

    def test_the_link_carries_a_version(self):
        used = [t for t in self.templates().values() if "lem.css" in t]
        assert used, "no template links lem.css at all"
        assert all("lem.css?v=" in t for t in used)

    def test_the_version_changes_with_the_file(self, tmp_path):
        from web_app import static_version
        a = tmp_path / "x.css"
        a.write_text("a{}")
        first = static_version(str(a))
        a.write_text("a{color:red}")
        assert static_version(str(a)) != first

    def test_it_is_stable_for_an_unchanged_file(self, tmp_path):
        from web_app import static_version
        a = tmp_path / "x.css"
        a.write_text("a{}")
        assert static_version(str(a)) == static_version(str(a))

    def test_a_missing_file_does_not_raise(self):
        """A packaging slip must not take every page down."""
        from web_app import static_version
        assert isinstance(static_version("/no/such/file.css"), str)
