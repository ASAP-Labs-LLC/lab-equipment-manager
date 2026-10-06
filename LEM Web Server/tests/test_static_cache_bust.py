"""A changed stylesheet or script reaches every screen: links carry the file's
own fingerprint (`?v=`), so a browser holding an old copy fetches the new one.

This lived in the maximal-map test file, where the bug was first seen (the
map's exit button). MAX MAP is cut (ia-final §10) and that file went with
the old floor in piece 14; this rule is the one part of it that was never
about the floor.
"""
import pathlib
import re


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

    LINK = re.compile(r'(?:href|src)="(/static/[^"]+\.(?:css|js))(\?[^"]*)?"')

    def test_no_template_links_the_bare_path(self):
        """Every stylesheet and script, not only lem.css: the pages since the
        redesign load a dozen of each."""
        offenders = ["%s: %s" % (name, m.group(1)) for name, text in self.templates().items()
                     for m in self.LINK.finditer(text) if not (m.group(2) or "").startswith("?v=")]
        assert offenders == [], offenders

    def test_the_link_carries_a_version(self):
        used = [m for t in self.templates().values() for m in self.LINK.finditer(t)]
        assert len(used) > 20, "the scan sees almost no links"
        assert any(m.group(1) == "/static/css/lem.css" for m in used)
        assert all("{{ v(" in (m.group(2) or "") for m in used)

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
# `hidden` has to hide.
#
# The other bug from the same file: the old floor's exit button sat on screen
# with `hidden` set, because the UA's `[hidden]{display:none}` and an author
# `.tool{display:inline-flex}` tie on specificity and the author rule wins a
# tie. The floor fixed it one class at a time (`.tool[hidden]`, `.who[hidden]`)
# and checked it with a cascade resolver over its own stylesheet. That file is
# gone (piece 14). The redesign fixes the whole class once, the way GC hub
# does: `.gc [hidden] { display: none !important; }` in shell.css, and every
# page sits in a frame whose body is `.gc` and loads shell.css. So that is
# what is pinned.
# ─────────────────────────────────────────────────────────────────────────────

ROOT = pathlib.Path(__file__).resolve().parent.parent
T = ROOT / "templates"


class TestHiddenAlwaysHides:
    def test_shell_css_makes_hidden_win_every_tie(self):
        css = (ROOT / "static" / "css" / "shell.css").read_text(encoding="utf-8")
        assert re.search(r"\.gc \[hidden\]\s*\{\s*display:\s*none\s*!important;\s*\}", css)

    def test_both_frames_load_it_on_a_gc_body(self):
        for frame in ("_layout.html", "_wall.html"):
            src = (T / frame).read_text(encoding="utf-8")
            assert '/static/css/shell.css?v=' in src, frame
            assert re.search(r'<body class="gc\b', src), frame

    def test_every_page_sits_in_one_of_the_frames(self):
        """A page outside both frames would be outside the rule; partials
        (`_*.html`) are included by pages, not served."""
        pages = [p for p in T.glob("*.html") if not p.name.startswith("_")]
        assert len(pages) >= 15
        for p in pages:
            head = p.read_text(encoding="utf-8").lstrip()
            assert head.startswith(('{% extends "_layout.html" %}', '{% extends "_wall.html" %}')), p.name
