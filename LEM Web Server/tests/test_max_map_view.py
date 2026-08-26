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
import re

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
        # The page has more than one Escape handler now (the search box
        # swallows its own), so this looks for the one that backs out of the
        # maximal view rather than for the first time the word appears.
        handlers = [s[m.start() - 400:m.start() + 400]
                    for m in re.finditer("Escape", s)]
        assert any("maxmap" in h for h in handlers) or "exitMax" in s

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


# ─────────────────────────────────────────────────────────────────────────────
# The bug the class above could not see.
#
# `TestTheExitButtonCannotLeak` asserts the markup ships `hidden`, that `.tool`
# supplies the look, and that `setMaxMap()` drives the attribute. All four were
# true, and the button still sat in the header of a page that was NOT in the
# maximal view — fixed at the top right, painted over the clock, leaving "202"
# of it showing. Reported again 2026-08-25.
#
# The cause is the cascade, which none of those tests evaluates. The UA
# stylesheet's `[hidden]{display:none}` and lem.css's `.tool{...display:
# inline-flex...}` have the SAME specificity (0,1,0), and an author rule beats
# the user agent at a tie — so `hidden` did nothing to any `.tool` anywhere in
# the app. `.who[hidden]{display:none}` already existed for exactly this
# reason, on exactly one element.
#
# So this RESOLVES the cascade rather than looking for a string: it parses both
# stylesheets, matches the rules that apply to the button, ranks them, and asks
# what `display` a browser would land on. The resolver is checked against the
# broken stylesheet first, so a resolver that has been gutted into "always
# answers none" cannot pass.
# ─────────────────────────────────────────────────────────────────────────────
import re


def _rules(sheet):
    """Every `selector { declarations }` in source order.

    At-rules are stepped over by brace depth rather than parsed: `@media`
    blocks in these sheets are all viewport queries, and a desktop browser
    running the page at 1600px applies none of them to this button. Anything
    inside one is therefore skipped, which is the same answer.
    """
    out, i, n = [], 0, len(sheet)
    sheet = re.sub(r"/\*.*?\*/", " ", sheet, flags=re.S)
    n = len(sheet)
    while i < n:
        brace = sheet.find("{", i)
        if brace < 0:
            break
        prelude = sheet[i:brace].strip()
        if prelude.startswith("@"):
            # Step over the whole at-rule, block and all.
            depth, j = 0, brace
            while j < n:
                if sheet[j] == "{":
                    depth += 1
                elif sheet[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            i = j + 1
            continue
        close = sheet.find("}", brace)
        if close < 0:
            break
        body = sheet[brace + 1:close]
        for sel in prelude.split(","):
            sel = sel.strip()
            if sel:
                out.append((sel, body))
        i = close + 1
    return out


#: A compound selector we understand: an optional element name followed by any
#: number of `.class`, `[attr]`, `#id` and `:pseudo-class` parts.
_COMPOUND = re.compile(
    r"^(?P<tag>[a-zA-Z][\w-]*|\*)?"
    r"(?P<rest>(?:[.#][\w-]+|\[[^\]]+\]|:[\w-]+)*)$")
_PART = re.compile(r"[.#][\w-]+|\[[^\]]+\]|:[\w-]+")


def _match_compound(compound, node):
    m = _COMPOUND.match(compound)
    if not m:
        return None                      # not a shape we can judge
    tag = m.group("tag")
    if tag and tag != "*" and tag.lower() != node["tag"]:
        return False
    ids = classes = elems = 0
    if tag and tag != "*":
        elems = 1
    for part in _PART.findall(m.group("rest") or ""):
        if part.startswith("#"):
            if part[1:] != node.get("id"):
                return False
            ids += 1
        elif part.startswith("."):
            if part[1:] not in node["classes"]:
                return False
            classes += 1
        elif part.startswith("["):
            if part[1:-1].split("=")[0].strip() not in node["attrs"]:
                return False
            classes += 1
        else:                                            # :pseudo-class
            if part[1:] not in node.get("pseudo", ()):
                return False
            classes += 1
    return (ids, classes, elems)


def _specificity(selector, chain):
    """The selector's specificity if it matches the LAST node of `chain`.

    Descendant combinators only. Anything using `>`, `+`, `~`, a pseudo-element
    or a functional pseudo-class is returned as unmatched — none of the rules
    that decide this button's `display` use one, and guessing at them would be
    worse than declining.
    """
    if re.search(r"[>+~]|::|\(", selector):
        return None
    parts = selector.split()
    if not parts:
        return None
    got = _match_compound(parts[-1], chain[-1])
    if got is None or got is False:
        return None if got is None else False
    total = list(got)
    # Every earlier compound must match some strict ancestor, innermost first.
    ancestors = list(chain[:-1])
    for compound in reversed(parts[:-1]):
        while ancestors:
            node = ancestors.pop()
            hit = _match_compound(compound, node)
            if hit is None:
                return None
            if hit:
                total = [a + b for a, b in zip(total, hit)]
                break
        else:
            return False
    return tuple(total)


def _resolve(prop, sheets, chain):
    """What `prop` settles on for the last node of `chain`.

    The UA stylesheet's `[hidden]{display:none}` is seeded first and at a lower
    origin, which is the whole point: an author rule of equal specificity beats
    it, and that is the bug.
    """
    best, winner = None, None
    candidates = [(0, "[hidden]", "display:none")]
    for origin, sheet in enumerate(sheets, start=1):
        for sel, body in _rules(sheet):
            candidates.append((origin, sel, body))
    for order, (origin, sel, body) in enumerate(candidates):
        value = None
        for decl in body.split(";"):
            name, _, val = decl.partition(":")
            if name.strip() == prop and val.strip():
                value = val.strip().split("!")[0].strip()
        if value is None:
            continue
        spec = _specificity(sel, chain)
        if not spec:
            continue
        rank = (origin,) + spec + (order,)
        if best is None or rank > best:
            best, winner = rank, value
    return winner


HTML = {"tag": "html", "classes": set(), "attrs": set(), "pseudo": {"root"}}
HTML_MAX = {"tag": "html", "classes": {"maxmap"}, "attrs": set(),
            "pseudo": {"root"}}
BODY = {"tag": "body", "classes": set(), "attrs": set()}
PANE = {"tag": "div", "classes": {"pane"}, "attrs": set()}


def _exit_button(hidden):
    node = {"tag": "button", "classes": {"tool", "maxexit"},
            "attrs": {"hidden"} if hidden else set(), "id": "btnMaxExit"}
    return node


class TestTheResolverItself:
    """The harness is checked before it is trusted.

    A cascade resolver that silently understands nothing answers `None` for
    everything, and `None != 'inline-flex'` would make every assertion below
    pass on a completely broken stylesheet. So it has to get the ORIGINAL bug
    right first.
    """

    def test_it_finds_the_rule_that_caused_the_bug(self):
        got = [b for s, b in _rules(css()) if s == ".tool"]
        assert got, "the resolver cannot even see the .tool rule"
        assert "inline-flex" in got[0]

    def test_it_reproduces_the_bug_on_the_broken_stylesheet(self):
        """Take the fix back out and the button must come back on screen. If
        this passes with the fix removed, the resolver is decoration."""
        broken = css().replace(".tool[hidden]{display:none}", "")
        assert ".tool[hidden]" not in broken
        settled = _resolve("display", [broken, ""],
                           [HTML, BODY, PANE, _exit_button(hidden=True)])
        assert settled == "inline-flex", (
            "the resolver cannot see the bug it exists to catch")

    def test_it_can_tell_a_shown_button_from_a_hidden_one(self):
        shown = _resolve("display", [css(), ""],
                         [HTML, BODY, PANE, _exit_button(hidden=False)])
        assert shown == "inline-flex"


class TestTheExitButtonIsActuallyHidden:
    """Ryan, 2026-08-25: "'✕ EXIT MAX MAP' is visible when not in max map, on
    both desktop and mobile. It should appear only in that mode."
    """

    def test_hidden_really_hides_it(self):
        settled = _resolve("display", [css(), ""],
                           [HTML, BODY, PANE, _exit_button(hidden=True)])
        assert settled == "none", (
            "the exit button renders on a page that is not in the maximal "
            "view — `.tool`'s display beats the UA's [hidden]")

    def test_it_is_hidden_in_the_maximal_view_too_until_shown(self):
        """`hidden` is the single source of truth in BOTH modes: `setMaxMap()`
        clears it on the way in and sets it on the way out, so a rule that
        showed it whenever `:root.maxmap` was present would make the attribute
        a lie half the time."""
        settled = _resolve("display", [css(), ""],
                           [HTML_MAX, BODY, PANE, _exit_button(hidden=True)])
        assert settled == "none"

    def test_the_fix_is_not_private_to_this_one_button(self):
        """Every `.tool` on every page had the same bug; `.who[hidden]` was
        already this fix applied to exactly one element. A rule written as
        `.maxexit[hidden]` would leave the floor's own hidden buttons — the
        View, Quality and Arrange controls the severed site hides — rendering
        on the tool row."""
        severed = {"tag": "button", "classes": {"tool"}, "attrs": {"hidden"},
                   "id": "btnView"}
        settled = _resolve("display", [css(), ""], [HTML, BODY, PANE, severed])
        assert settled == "none"


class TestTheClockIsNotPaintedOver:
    """Ryan, 2026-08-25: "the clock is truncated to '202' in the header."

    It was not truncated: the exit button above is `position:fixed` at the top
    right, so it was OUT OF FLOW and sitting on top of the last item in the
    bar. Hiding it fixes the normal view — but in the maximal view the button
    is legitimately there, and it would still land on the clock. So the bar
    reserves the lane.
    """

    def test_the_clock_does_not_shrink_out_of_its_own_box(self):
        """Every other item in this bar carries `flex:none`. A shrinkable item
        whose text cannot wrap does not get shorter — it overflows onto its
        neighbour."""
        rules = dict((s, b) for s, b in _rules(src()) if s == ".clock")
        assert ".clock" in rules, "the clock lost its rule entirely"
        assert "flex:none" in rules[".clock"].replace(" ", "")

    def test_the_maximal_bar_reserves_the_lane_the_exit_button_sits_in(self):
        lane = _resolve("padding-right", [css(), src()],
                        [HTML_MAX, BODY,
                         {"tag": "header", "classes": {"bar"}, "attrs": set()}])
        shorthand = _resolve("padding", [css(), src()],
                             [HTML_MAX, BODY,
                              {"tag": "header", "classes": {"bar"},
                               "attrs": set()}])
        reserved = lane or (shorthand or "").split()
        assert "var(--maxexit-lane)" in str(reserved), (
            "the fixed exit button would sit on top of the clock in the "
            "maximal view")

    def test_the_lane_is_wide_enough_for_the_button(self):
        """A lane narrower than the control it is reserving for is the same bug
        with an extra step. The button is ~129px of text plus its 12px offset."""
        sheet = css()
        m = re.search(r"--maxexit-lane:\s*(\d+)px", sheet)
        assert m, "the lane has no width to check"
        assert int(m.group(1)) >= 141

    def test_the_normal_bar_does_not_reserve_it(self):
        """Nothing is parked there when the button is hidden, and 156px of dead
        space on the right of every header would be a worse bug than the one
        being fixed."""
        lane = _resolve("padding", [css(), src()],
                        [HTML, BODY,
                         {"tag": "header", "classes": {"bar"}, "attrs": set()}])
        assert "maxexit-lane" not in str(lane)
