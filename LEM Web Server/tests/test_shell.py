"""One shell, shared by every page.

Five pages had five hand-rolled headers and five copies of the palette, and it
had already drifted: SERVICE went purple and DEAD-LINE got hazard stripes on the
floor, while the maintenance page's own colour map still rendered them grey.

It was also a broken graph — you couldn't reach /logs from home, or /checklists
from anywhere but home — and the floor crammed 19 controls into one 52px row with
`overflow:hidden`, so Sign in and the LabCore-offline warning were silently
clipped on a narrow window.

This file used to check that against the old shell (the old nav partial
and `static/lem.css`), on the old floor and PM pages. Piece 14 deleted all four.
The redesign's shell is GC hub's, ported (`_layout.html`, `_shell.html`,
`static/css/tokens.css`); `tests/test_ui_shell_pages.py` walks every page for
the nav, the way home, the version and the words strip. What stays here is
the rule this file was written for, restated for the new shell:

* **The palette lives in exactly one file.** Every colour is a token in
  tokens.css. lem.css carries the one deviation ia-final §9.2 allows (the
  dark --warn family) and the walls' type sizes, and no colour else. No
  template has a <style> of its own to redefine anything in.
* **Every page reaches every nav destination,** from the one sidebar.
"""
import re
from pathlib import Path

import pytest

from labcore_gateway import FakeLabCoreGateway

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "static" / "css"
T = ROOT / "templates"
PAGES = ["/", "/checklists/opening", "/quality", "/logs", "/settings", "/help"]
DESTS = ["/", "/checklists", "/quality", "/logs", "/settings"]

#: The custom properties lem.css may set, and why. Anything else is a second
#: palette starting.
LEM_CSS_TOKENS = {
    "--warn", "--warn-text", "--warn-soft", "--st-held", "--pill-held-fg",  # §9.2
    "--w-name", "--w-word", "--w-detail", "--w-pad",                        # wall type sizes
    # piece 15: on a tinted surface --text-muted is pointed at GC's own
    # --text-muted-sunken (4.33:1 -> 5.28:1 on --bg-sunken). An alias to a
    # palette token, never a new colour: test_the_muted_alias_is_only_an_alias
    "--text-muted",
}


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


@pytest.fixture
def client():
    from web_app import create_app
    app = create_app(FakeLabCoreGateway(), authenticator=StubAuth(),
                     secret="s")
    app.config["TESTING"] = True
    return app.test_client()


def declared(css: str) -> set:
    return set(re.findall(r"(?m)^\s*(--[a-z0-9-]+)\s*:", css)) | \
        set(re.findall(r";\s*(--[a-z0-9-]+)\s*:", css))


class TestOnePalette:
    def test_the_tokens_file_owns_the_palette(self):
        tokens = declared((CSS / "tokens.css").read_text(encoding="utf-8"))
        for t in ("--bg", "--text", "--accent", "--bad", "--warn", "--good"):
            assert t in tokens, t

    def test_lem_css_sets_only_the_allowed_tokens(self):
        got = declared((CSS / "lem.css").read_text(encoding="utf-8"))
        assert got == LEM_CSS_TOKENS, sorted(got ^ LEM_CSS_TOKENS)

    def test_the_muted_alias_is_only_an_alias(self):
        css = (CSS / "lem.css").read_text(encoding="utf-8")
        values = re.findall(r"(?<![-\w])--text-muted\s*:\s*([^;]+);", css)
        assert values and set(v.strip() for v in values) == {"var(--text-muted-sunken)"}, values

    def test_no_other_stylesheet_declares_a_token(self):
        for p in sorted(CSS.glob("*.css")):
            if p.name in ("tokens.css", "lem.css"):
                continue
            assert declared(p.read_text(encoding="utf-8")) == set(), p.name

    def test_no_template_carries_its_own_style(self):
        hits = [p.name for p in T.glob("*.html") if "<style" in p.read_text(encoding="utf-8")]
        assert hits == [], hits

    def test_the_old_shell_is_gone(self):
        assert not (ROOT / "static" / "lem.css").exists()


class TestNavigation:
    @pytest.mark.parametrize("path", PAGES)
    def test_every_page_carries_the_whole_nav(self, client, path):
        html = client.get(path).get_data(as_text=True)
        nav = html[html.index('id="sidebar"'):]
        nav = nav[:nav.index("</nav>")]
        for dest in DESTS:
            assert f'href="{dest}"' in nav, f"{path} cannot reach {dest}"
