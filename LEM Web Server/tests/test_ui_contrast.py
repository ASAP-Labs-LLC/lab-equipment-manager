"""WCAG AA for the shell's colours, computed from the token files — no browser.

Ported from GC hub's ``tests/test_ui_contrast.py``. LEM ships GC's
``static/css/tokens.css`` verbatim so the two apps are one palette, plus ONE
override block in ``static/css/lem.css``: dark-mode ``--warn``. In GC's dark
theme ``--warn`` is yellow (#facc15) while light is orange (#b45309), and the
judges watched "Overdue" change hue between themes. LEM uses amber (#f59e0b)
in dark, the same hue family as light. That override is the one place this
file's numbers can drift from GC's, so it gets its own rows below and the
override is read from ``lem.css`` exactly as the browser would apply it
(after tokens.css).

Every text pair needs 4.5:1; glyphs, the focus ring and other non-text marks
need 3:1. Translucent fills (hover, active, the pills' soft tints) are
composited over the surface they sit on, as the browser does.

The LEM pairs are the places this shell puts text that GC's does not: the rail
badges, the words strip that replaces the sidebar foot in rail mode, the phone
top bar, nav-meta, and the verdict words later pages will set on cards and
tiles. A token change that breaks any of them fails here at once rather than
on a wall display three weeks later.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "static" / "css"
TOKENS = CSS / "tokens.css"
LEM = CSS / "lem.css"
SHELL = CSS / "shell.css"
BADGE = CSS / "badge.css"
DARK = ':root[data-theme="dark"]'


def _strip(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _blocks(css: str) -> dict:
    out: dict = {}
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", _strip(css)):
        decls = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body))
        for one in sel.split(","):
            out.setdefault(one.strip(), {}).update(decls)
    return out


def _themes() -> dict:
    gc = _blocks(TOKENS.read_text(encoding="utf-8"))
    lem = _blocks(LEM.read_text(encoding="utf-8"))
    # in cascade order: GC's tokens, then lem.css (loaded after tokens.css)
    light = {**gc[":root"], **lem.get(":root", {})}
    dark = {**light, **gc[DARK], **lem.get(DARK, {})}
    return {"light": light, "dark": dark}


def _resolve(tokens: dict, value: str, depth: int = 0) -> str:
    value = value.strip()
    m = re.fullmatch(r"var\((--[\w-]+)\)", value)
    if m and depth < 10:
        return _resolve(tokens, tokens[m.group(1)], depth + 1)
    return value


def _rgba(text: str) -> tuple:
    text = text.strip()
    if text.startswith("#"):
        h = text[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
    m = re.fullmatch(r"rgba?\(([^)]*)\)", text)
    assert m, text
    parts = [float(p) for p in re.split(r"[ ,/]+", m.group(1).strip()) if p]
    return (parts[0], parts[1], parts[2], parts[3] if len(parts) > 3 else 1.0)


def _over(top, bottom):
    a = top[3]
    return tuple(top[i] * a + bottom[i] * (1 - a) for i in range(3)) + (1.0,)


def _lum(c):
    def f(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])


def ratio(fg, bg) -> float:
    a, b = _lum(fg), _lum(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def colour(tokens, *layers):
    """The colour of stacked layers, bottom first (tokens or literals)."""
    base = (255, 255, 255, 1.0)
    for layer in layers:
        base = _over(_rgba(_resolve(tokens, tokens.get(layer, layer))), base)
    return base


TEXT = 4.5
UI = 3.0
# GC hub's pairs for the pieces LEM's shell ports (shell, settings, controls,
# notifications, status_shell, badge, gc_pages): (fg, [bg bottom first], need, what)
GC_PAIRS = [
    ("--text", ["--bg"], TEXT, "body text"),
    ("--text", ["--bg-card"], TEXT, "card text"),
    ("--text", ["--sidebar-bg"], TEXT, "sidebar text"),
    ("--text", ["--sidebar-bg", "--bg-active"], TEXT, "active nav item"),
    ("--text", ["--bg-sunken"], TEXT, "text on sunken tracks"),
    ("--text", ["--bg-elevated"], TEXT, "menus and dialogs"),
    ("--text-muted", ["--bg"], TEXT, "captions"),
    ("--text-muted", ["--bg-card"], TEXT, "captions on cards"),
    ("--text-muted", ["--sidebar-bg"], TEXT, "sidebar captions"),
    ("--nav-meta", ["--sidebar-bg", "--bg-hover"], TEXT, "nav meta on hover"),
    ("--nav-meta", ["--sidebar-bg", "--bg-active"], TEXT, "nav meta on the active item"),
    ("--text-muted", ["--bg-elevated"], TEXT, "menu captions"),
    ("--text-muted-sunken", ["--bg-sunken"], TEXT, "muted text on sunken tracks"),
    ("--text-muted-sunken", ["--bg-card", "--bg-sunken"], TEXT, "tiles, pills, notes"),
    ("--pill-final-fg", ["--bg-card", "--good-soft"], TEXT, "OK pill"),
    ("--pill-held-fg", ["--bg-card", "--warn-soft"], TEXT, "held pill"),
    ("--pill-error-fg", ["--bg-card", "--bad-soft"], TEXT, "error pill"),
    ("--warn-text", ["--bg"], TEXT, "warning lines"),
    ("--st-error", ["--bg"], TEXT, "error lines"),
    ("--bad", ["--bg"], TEXT, "danger buttons"),
    ("--ink-fg", ["--ink"], TEXT, "primary buttons and the toast"),
    ("--ink-fg", ["--ink-hover"], TEXT, "primary buttons on hover"),
    ("--text-inverse", ["--st-error"], TEXT, "the bell's count"),
    ("--text-inverse", ["--bad"], TEXT, "an error toast"),
    ("--badge-fg", ["--bg"], TEXT, "the version badge"),
    ("--chart-axis", ["--bg-card"], TEXT, "chart labels"),
    ("--text-muted-sunken", ["--bg", "--bg-sunken"], TEXT, "the seg's unchosen options"),
    ("--text", ["--bg-card"], TEXT, "the seg's chosen option"),
    ("--text", ["--bg-elevated", "--bg-hover"], TEXT, "a menu item on hover"),
    ("--st-error", ["--bg-elevated"], TEXT, "an error notification's level"),
    ("--warn-text", ["--bg-elevated"], TEXT, "a warning notification's level"),
    ("--text-muted", ["--bg-elevated"], TEXT, "a notification's time"),
    ("--pill-final-fg", ["--bg"], TEXT, "a saved setting's line"),
    ("--text", ["--bg", "--bg-active"], TEXT, "the settings sub-nav's current item"),
    ("--accent", ["--bg"], UI, "focus ring"),
    ("--accent", ["--sidebar-bg"], UI, "focus ring in the sidebar"),
    ("--accent", ["--bg-sunken"], UI, "focus ring on sunken fields"),
    ("--st-final", ["--bg"], UI, "OK glyph"),
    ("--st-held", ["--bg"], UI, "held glyph"),
    ("--st-error", ["--bg"], UI, "error glyph"),
    ("--text-muted", ["--bg"], UI, "hollow (never/unknown) glyph"),
]

# LEM's own pairs (ia-final §9.1): where LEM's shell and pages put text or
# marks that GC's do not.
LEM_PAIRS = [
    # the rail badge is the bell-count style in ink, never the red pill
    ("--ink-fg", ["--sidebar-bg", "--ink"], TEXT, "a rail badge's number"),
    ("--ink", ["--sidebar-bg"], UI, "a rail badge against the rail"),
    ("--ink", ["--sidebar-bg", "--bg-active"], UI, "a rail badge on the active item"),
    # the words strip at the bottom of the page in rail mode
    ("--text", ["--bg-card"], TEXT, "the rail status strip's words"),
    ("--text-muted", ["--bg-card"], TEXT, "the rail status strip's separators and age"),
    ("--st-final", ["--bg-card"], UI, "the strip's fresh glyph"),
    ("--st-error", ["--bg-card"], UI, "the strip's LabCore-down glyph"),
    ("--text-muted", ["--bg-card"], UI, "the strip's stale glyph"),
    # the phone's top bar
    ("--text", ["--sidebar-bg"], TEXT, "the phone bar's page title"),
    # verdict words on cards and on sunken tiles (pages to come use these)
    ("--pill-final-fg", ["--bg-card"], TEXT, "OK to run on a card"),
    ("--warn-text", ["--bg-card"], TEXT, "OK to run, but... / QC due on a card"),
    ("--st-error", ["--bg-card"], TEXT, "Not OK to run on a card"),
    ("--pill-final-fg", ["--bg-card", "--bg-sunken"], TEXT, "a verdict on a tile"),
    ("--warn-text", ["--bg-card", "--bg-sunken"], TEXT, "a due verdict on a tile"),
    ("--st-error", ["--bg-card", "--bg-sunken"], TEXT, "a failed verdict on a tile"),
    ("--text-muted-sunken", ["--bg-card", "--bg-sunken"], TEXT, "a tile's detail line"),
    ("--pill-held-fg", ["--bg", "--warn-soft"], TEXT, "a held pill on the page"),
    ("--pill-final-fg", ["--bg", "--good-soft"], TEXT, "an OK pill on the page"),
    ("--pill-error-fg", ["--bg", "--bad-soft"], TEXT, "an error pill on the page"),
    ("--st-held", ["--bg-card"], UI, "a held glyph on a card"),
    ("--st-held", ["--bg-card", "--bg-sunken"], UI, "a held glyph on a tile"),
    ("--chart-ink", ["--bg-card"], UI, "chart ink against the card"),
    # the wall kiosks (piece 13): read from across the room, so every word
    # on them is held to text contrast, and the QC cards' limit lines (the
    # band's edges, what a point is judged against) to the 3:1 of a glyph
    ("--text", ["--bg"], TEXT, "the wall's headline and counts"),
    ("--text-muted", ["--bg"], TEXT, "the wall's sub-line, count words and footer"),
    ("--st-error", ["--bg"], TEXT, "the wall's Not OK to run count"),
    ("--pill-held-fg", ["--bg-card"], TEXT, "OK to run, but... / QC due on a wall bay or card"),
    ("--st-error", ["--bg-card"], TEXT, "Not OK to run / Out of spec on a wall bay or card"),
    ("--text-muted", ["--bg-card"], UI, "the QC wall's limit lines"),
    ("--chart-ink", ["--bg-card"], UI, "the QC wall's points"),
    ("--st-error", ["--bg-card"], UI, "the QC wall's out-of-spec triangles"),
    # the degraded-data banner (#paused-banner). GC hub draws it in
    # --warn-text on --warn-soft, which is 4.38:1 in light: under AA. LEM's
    # lem.css sets it in --pill-held-fg, the token made for text on that tint.
    ("--pill-held-fg", ["--bg", "--warn-soft"], TEXT, "the degraded-data banner"),
]
PAIRS = GC_PAIRS + LEM_PAIRS


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("fg,bg,need,what", PAIRS, ids=[p[3] for p in PAIRS])
def test_token_pair_contrast(theme, fg, bg, need, what):
    t = _themes()[theme]
    back = colour(t, *bg)
    front = _over(_rgba(_resolve(t, t[fg])), back)
    r = ratio(front, back)
    assert r >= need, f"{theme}: {what} ({fg} on {' + '.join(bg)}) is {r:.2f}:1, needs {need}:1"


class TestTheOneTokenDeviation:
    """ia-final §9.2: dark --warn is amber, in lem.css, and nowhere else."""

    def test_dark_warn_is_amber_not_gcs_yellow(self):
        dark = _themes()["dark"]
        assert dark["--warn"].lower() == "#f59e0b"
        assert dark["--st-held"].lower() == "#f59e0b"
        assert dark["--pill-held-fg"].lower() == "#fbbf24"
        # the text and soft tokens move with it, or the banner and the pills
        # would still be yellow while the glyph went amber
        assert _resolve(dark, dark["--warn-text"]).lower() in ("#f59e0b", "#fbbf24")
        assert "250, 204, 21" not in dark["--warn-soft"]

    def test_light_is_untouched(self):
        """Light --warn was never the problem; the override is dark only."""
        gc = _blocks(TOKENS.read_text(encoding="utf-8"))[":root"]
        light = _themes()["light"]
        for name in ("--warn", "--warn-text", "--st-held", "--pill-held-fg", "--warn-soft"):
            assert light[name] == gc[name], name

    @pytest.mark.parametrize("bg", ["--bg", "--bg-card", "--bg-elevated", "--sidebar-bg"])
    def test_dark_amber_is_readable_text_everywhere_it_sits(self, bg):
        t = _themes()["dark"]
        back = colour(t, bg)
        r = ratio(_over(_rgba(_resolve(t, t["--warn-text"])), back), back)
        assert r >= TEXT, f"dark --warn-text on {bg} is {r:.2f}:1"

    def test_spec_figure_holds(self):
        """The spec quotes about 8.6:1 for #f59e0b on #141414 (the dark card).
        Show the number rather than trust the sentence."""
        r = ratio(_rgba("#f59e0b"), _rgba("#141414"))
        assert 8.4 <= r <= 8.8, f"{r:.2f}:1"

    def test_the_override_is_the_only_colour_lem_css_defines(self):
        """lem.css uses tokens only; the dark --warn block is the single
        exception, and it may only set the warn family."""
        css = _strip(LEM.read_text(encoding="utf-8"))
        blocks = re.findall(r"([^{}]+)\{([^{}]*)\}", css)
        for sel, body in blocks:
            literal = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", body)
            if not literal:
                continue
            assert sel.strip() == DARK, (sel.strip(), literal)
            names = set(re.findall(r"(--[\w-]+)\s*:", body))
            assert names <= {"--warn", "--warn-text", "--warn-soft", "--st-held",
                             "--pill-held-fg"}, names


def test_the_shell_uses_the_checked_tokens():
    css = SHELL.read_text(encoding="utf-8")
    assert re.search(r"\.pill\.final\s*\{[^}]*color:\s*var\(--pill-final-fg\)", css)
    assert re.search(r"\.pill\.held\s*\{[^}]*color:\s*var\(--pill-held-fg\)", css)
    assert re.search(r"\.pill\.error\s*\{[^}]*color:\s*var\(--pill-error-fg\)", css)
    assert re.search(r"\.nav-item \.nav-meta\s*\{[^}]*color:\s*var\(--nav-meta\)", css)


def test_the_banner_is_drawn_in_the_tint_text_token():
    """The pair above only protects the banner if the banner uses it. GC's
    status_shell.css says `color: var(--warn-text)`; lem.css (loaded later)
    must win with --pill-held-fg, or the light banner is 4.38:1."""
    css = _strip(LEM.read_text(encoding="utf-8"))
    assert re.search(r"\.paused-banner\s*\{[^}]*color:\s*var\(--pill-held-fg\)", css)
    t = _themes()["light"]
    back = colour(t, "--bg", "--warn-soft")
    gc_ratio = ratio(_over(_rgba(_resolve(t, t["--warn-text"])), back), back)
    assert gc_ratio < TEXT, "GC's banner colour now passes; the override can go"


def test_the_focus_ring_is_a_solid_offset_outline():
    css = SHELL.read_text(encoding="utf-8")
    rule = re.search(r"\.gc :focus-visible\s*\{([^}]*)\}", css).group(1)
    assert re.search(r"outline:\s*2px solid var\(--accent\)", rule)
    assert "outline-offset" in rule and "border-radius" not in rule


def test_the_version_badge_is_readable():
    css = BADGE.read_text(encoding="utf-8")
    rule = re.search(r"#app-version\s*\{([^}]*)\}", css).group(1)
    op = re.search(r"opacity:\s*([\d.]+)", rule)
    assert op is None or float(op.group(1)) == 1.0
    assert "var(--badge-fg" in rule


# The ported page stylesheets and lem.css use only checked tokens for colour
# (no literal colours outside the one override), and only foreground tokens
# that appear in a pair above.
PAGE_CSS = [CSS / n for n in ("settings.css", "controls.css", "notifications.css", "lem.css")]
CHECKED_FG = {p[0] for p in PAIRS}


@pytest.mark.parametrize("css", PAGE_CSS, ids=lambda p: p.name)
def test_the_pages_use_tokens_only(css):
    text = _strip(css.read_text(encoding="utf-8"))
    if css.name == "lem.css":
        # the §9.2 block is checked by TestTheOneTokenDeviation
        text = re.sub(re.escape(DARK) + r"\s*\{[^}]*\}", "", text)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", text), css.name
    for m in re.finditer(r"(?<![-\w])color\s*:\s*([^;}]+)", text):
        value = m.group(1).strip()
        token = re.fullmatch(r"var\((--[\w-]+)\)", value)
        assert token, (css.name, value)
        assert token.group(1) in CHECKED_FG, (css.name, token.group(1))


# ── every token pair LEM can draw (piece 15) ────────────────────────────────
# The rows above are the pairs someone thought of. These are the pairs the
# CSS can actually produce, found by reading every stylesheet the shell
# loads, so a new colour or a new tinted surface fails here until somebody
# says where it sits and the numbers hold:
#
# * every token used as a text colour is checked on every surface a word can
#   sit on (the page, a card, a menu, the sidebar, a sunken tile, a hovered
#   row, a selected row), unless it is listed in SITS_ON with the only
#   surfaces it is drawn on, or in MARK_ONLY as a glyph's currentColor;
# * every status or chart mark (fill, stroke, border, a glyph's colour) and
#   the focus ring is checked at 3:1 on the same surfaces;
# * every rule that paints a tinted surface (sunken, hover, active) is
#   classified: it re-points --text-muted at --text-muted-sunken (lem.css),
#   or it holds no muted words. GC's --text-muted is 4.33:1 on --bg-sunken
#   and 4.13:1 on a selected row in light; that is how a caption on a
#   selected QC row came in under AA unseen.
ALL_CSS = sorted(CSS.glob("*.css"))
SURFACES = {
    "page": ["--bg"], "card": ["--bg-card"], "menu": ["--bg-elevated"], "sidebar": ["--sidebar-bg"],
    "sunken tile": ["--bg-card", "--bg-sunken"], "sunken on the page": ["--bg", "--bg-sunken"],
    "hovered row": ["--bg-card", "--bg-hover"], "selected row": ["--bg-card", "--bg-active"],
    "hovered sidebar item": ["--sidebar-bg", "--bg-hover"], "current sidebar item": ["--sidebar-bg", "--bg-active"],
}
TINTED = ["sunken tile", "sunken on the page", "hovered row", "selected row", "hovered sidebar item",
          "current sidebar item"]
PLAIN = [k for k in SURFACES if k not in TINTED]
#: text tokens drawn only on some surfaces, and why
SITS_ON = {
    # re-pointed at --text-muted-sunken on every tinted surface (lem.css)
    "--text-muted": PLAIN,
    "--warn-text": PLAIN,
    # made for one ground each
    "--ink-fg": [["--ink"], ["--ink-hover"], ["--sidebar-bg", "--ink"]],
    "--text-inverse": [["--st-error"], ["--bad"]],
    "--pill-final-fg": PLAIN + TINTED + [["--bg", "--good-soft"], ["--bg-card", "--good-soft"]],
    "--pill-held-fg": PLAIN + TINTED + [["--bg", "--warn-soft"], ["--bg-card", "--warn-soft"]],
    "--pill-error-fg": PLAIN + TINTED + [["--bg", "--bad-soft"], ["--bg-card", "--bad-soft"]],
    # chart labels: charts are drawn on cards only
    "--chart-axis": ["card"],
}
#: tokens set as `color:` only so a glyph or icon (currentColor) takes them
MARK_ONLY = {"--st-final", "--st-held", "--ink"}
MARK_SELECTOR = re.compile(r"glyph|\.ico\b|-dot\b|\.tick\b")
#: marks that carry nothing a word beside them does not also say
DECORATIVE = {
    "--border", "--border-light", "--border-strong",   # separators and the dashed "stopped"/"stale" edges, always with words
    "--border-focus",            # a focused field also gets the 2px --accent ring
    "--chart-band", "--chart-grid", "--chart-tick",    # the band's edges are the limit lines, in --text-muted
    "--chart-ref",               # the target line is labelled "target 190.2" beside it
    "--bg-card", "--sidebar-bg", "--bg-active",        # a halo or knock-out in the surface's own colour
    "--ring", "--e1", "--e2", "--e3", "--good-soft", "--bad-soft", "--warn-soft", "--accent-soft",
    "--text-muted-sunken",       # a hollow ring on a sunken track, beside its word
}


def _rules():
    for f in ALL_CSS:
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", _strip(f.read_text(encoding="utf-8"))):
            yield f.name, " ".join(sel.split()), body


def _stack(spec):
    return SURFACES[spec] if isinstance(spec, str) else spec


def _pair(theme, fg, stack):
    t = _themes()[theme]
    back = colour(t, *stack)
    return ratio(_over(_rgba(_resolve(t, t[fg])), back), back)


TEXT_USED = sorted({tok for _f, sel, body in _rules()
                    for tok in re.findall(r"(?<![-\w])color\s*:\s*var\((--[\w-]+)\)", body)} |
                   {"--chart-axis"})


def test_every_text_colour_is_classified():
    unknown = [t for t in TEXT_USED if t not in SITS_ON and t not in MARK_ONLY
               and t not in ("--text", "--text-muted-sunken", "--nav-meta", "--st-error", "--bad")]
    assert unknown == [], f"say where these sit (SITS_ON) or that they only colour glyphs: {unknown}"


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("fg", [t for t in TEXT_USED if t not in MARK_ONLY])
def test_every_text_token_on_every_surface_it_can_sit_on(theme, fg):
    where = SITS_ON.get(fg, list(SURFACES))
    bad = [(w if isinstance(w, str) else "+".join(w), round(_pair(theme, fg, _stack(w)), 2))
           for w in where if _pair(theme, fg, _stack(w)) < TEXT]
    assert bad == [], f"{theme}: {fg} under 4.5:1 on {bad}"


def test_mark_only_colours_colour_only_marks():
    for name, sel, body in _rules():
        m = re.search(r"(?<![-\w])color\s*:\s*var\((--[\w-]+)\)", body)
        if m and m.group(1) in MARK_ONLY:
            assert MARK_SELECTOR.search(sel), (name, sel, m.group(1))


MARKS_USED = sorted({tok for _f, _sel, body in _rules()
                     for tok in re.findall(r"(?<![-\w])(?:fill|stroke|background(?:-color)?|border(?:-[a-z]+)*|outline|"
                                           r"text-decoration(?:-color)?|box-shadow)\s*:[^;]*?var\((--[\w-]+)\)", body)}
                    | MARK_ONLY)
NOT_MARKS = {"--bg", "--bg-sunken", "--bg-hover", "--bg-elevated", "--radius-card", "--radius-lg", "--radius-md",
             "--radius-pill", "--radius-sm", "--ink-hover", "--bad", "--text-muted-sunken"}


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("mark", [m for m in MARKS_USED if m not in DECORATIVE and m not in NOT_MARKS])
def test_every_glyph_and_the_focus_ring_reach_3_to_1(theme, mark):
    bad = [(w, round(_pair(theme, mark, SURFACES[w]), 2)) for w in SURFACES if _pair(theme, mark, SURFACES[w]) < UI]
    assert bad == [], f"{theme}: {mark} under 3:1 on {bad}"


def test_the_decorative_marks_are_backed_by_checked_ones():
    """The claims DECORATIVE makes are kept: the limit lines that edge the
    band are --text-muted (3:1 above), and the target line is labelled."""
    lem = _strip(LEM.read_text(encoding="utf-8"))
    assert re.search(r"\.qc-limit\s*\{[^}]*stroke:\s*var\(--text-muted\)", lem)
    rec = (ROOT / "static" / "js" / "record.js").read_text(encoding="utf-8")
    assert "'target'" in rec, "the record chart's target line lost its label"


TINTED_RULE = re.compile(r"background(?:-color)?\s*:[^;]*var\(--bg-(?:sunken|hover|active)\)")
#: tinted surfaces that hold no muted words, and what they hold instead
NO_MUTED_INSIDE = {
    ".add-card:hover", ".add-card .plus", ".feed li .ic", ".gstep .badge", ".gstep ol.how li::before",
    ".gstep code",                                   # GC's /setup page: not served by LEM
    ".round .pill.part", ".rec-pill", ".pill.q-pill", ".pill",     # pills set their own text token
    ".rrow .undo:hover", ".ed-n[draggable=\"true\"]:hover", ".wall-mark:hover", ".btn:hover",
    ".btn-ghost:hover", ".icon-btn:hover", ".user-chip:hover", ".menu a:hover, .menu button.menu-item:hover",
    ".nav-item:hover", ".nav-item.active, .nav-item[aria-current=\"page\"]",   # --nav-meta, checked on both
    ".skeleton i", ".chart-skel i", ".progress", ".plan .cell:hover, .plan .cell.over, .plan .cell:focus-visible",
    ".plan", ".bay.dim", ".seg", ".find-box kbd", ".help kbd", ".ed-tag",       # words in --text / -sunken
    ".sb-running:hover",
}


#: a tinted cell or paragraph, and the re-pointing selector that covers it
COVERED_BY = {
    ".tbl.inst .irow:hover td": ".tbl tbody tr:hover", ".tbl.qc .qrow:hover td": ".tbl tbody tr:hover",
    ".tbl.qc .qrow.is-sel td": ".tbl tbody tr.is-sel", ".log-row:hover td": ".tbl tbody tr:hover",
    ".log-row:focus-within td": ".tbl tbody tr:focus-within", ".tbl.qx .qxrow:hover td": ".tbl tbody tr:hover",
    ".tbl.qs .srow:hover td": ".tbl tbody tr:hover", ".tbl.bf tr.can:hover td": ".tbl tbody tr:hover",
    ".tbl.bf tr.is-sel td": ".tbl tbody tr.is-sel", ".tbl.bf tr.is-sel:hover td": ".tbl tbody tr.is-sel",
    ".rounds-tbl tr.can:hover td": ".tbl tbody tr:hover", ".rrow.next:hover": ".rrow:hover",
    "dialog.sheet.log-sheet p.log-sheet-say": ".log-sheet-say",
    '.find-list li[aria-selected="true"] a': '.find-list li[aria-selected="true"]',
    '.load-state[data-state="empty"]': ".load-state",
}


def test_every_tinted_surface_keeps_its_muted_words_readable():
    lem = _strip(LEM.read_text(encoding="utf-8"))
    rule = re.search(r"([^{}]+)\{\s*--text-muted:\s*var\(--text-muted-sunken\);", lem)
    assert rule, "lem.css no longer re-points --text-muted on tinted surfaces"
    covered = {" ".join(s.split()) for s in rule.group(1).split(",")}
    loose = []
    for name, sel, body in _rules():
        if not TINTED_RULE.search(body) or sel in NO_MUTED_INSIDE:
            continue
        for part in (p.strip() for p in sel.split(",")):
            # a row's cells inherit the row's tokens; the row itself is the
            # <tr> of a .tbl, which `.tbl tbody tr:hover` and friends cover
            via = COVERED_BY.get(part, part.split(" ")[-1] if part.split(" ")[-1] in covered else part)
            if via not in covered:
                loose.append(f"{name}: {part}")
    assert loose == [], "tinted surfaces with no answer for muted words inside: %r" % loose
