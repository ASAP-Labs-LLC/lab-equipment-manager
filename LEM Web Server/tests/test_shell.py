"""One shell, shared by every page.

Five pages had five hand-rolled headers and five copies of the palette, and it
had already drifted: SERVICE went purple and DEAD-LINE got hazard stripes on the
floor, while the maintenance page's own colour map still rendered them grey.

It was also a broken graph — you couldn't reach /logs from home, or /checklists
from anywhere but home — and the floor crammed 19 controls into one 52px row with
`overflow:hidden`, so Sign in and the LabCore-offline warning were silently
clipped on a narrow window.

So: navigation lives in a left rail present on every page, page tools get their
own row, and the palette lives in exactly one file.
"""
import re

import pytest

from labcore_gateway import FakeLabCoreGateway

PAGES = ["/", "/floor", "/checklists", "/maintenance", "/logs"]
DESTS = ["/floor", "/checklists", "/maintenance", "/logs"]


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


def body(client, path):
    return client.get(path).get_data(as_text=True)


def style_block(html):
    return "\n".join(re.findall(r"<style>(.*?)</style>", html, re.S))


# ── one stylesheet ──────────────────────────────────────────────────────────

class TestSharedStylesheet:
    def test_it_is_served(self, client):
        r = client.get("/static/lem.css")
        assert r.status_code == 200
        assert "text/css" in r.headers["Content-Type"]

    @pytest.mark.parametrize("path", PAGES)
    def test_every_page_links_it(self, client, path):
        assert "/static/lem.css" in body(client, path), path

    def test_it_owns_the_palette(self, client):
        css = client.get("/static/lem.css").get_data(as_text=True)
        for token in ("--void", "--amber", "--green", "--red", "--edge"):
            assert token in css, token

    def test_it_owns_the_status_colours(self, client):
        """The drift that painted SERVICE grey on one page and purple on
        another."""
        css = client.get("/static/lem.css").get_data(as_text=True)
        assert "--service:#a855f7" in css.replace(" ", "")
        assert "--dead" in css

    @pytest.mark.parametrize("path", PAGES)
    def test_no_page_redefines_the_palette(self, client, path):
        css = style_block(body(client, path))
        assert "--void:" not in css, f"{path} still declares its own palette"

    @pytest.mark.parametrize("path", PAGES)
    def test_no_page_redefines_the_nav_or_buttons(self, client, path):
        css = style_block(body(client, path))
        # A *bare* rule is a redefinition; a scoped one (`.railact .tool`) is a
        # legitimate local override.
        for rule in ("tool", "navrail", "who"):
            assert not re.search(r"^\s*\." + rule + r"\{", css, re.M), \
                f"{path} redefines .{rule}"


# ── navigation ──────────────────────────────────────────────────────────────

class TestNavigation:
    @pytest.mark.parametrize("path", PAGES)
    def test_every_page_carries_the_whole_nav(self, client, path):
        html = body(client, path)
        for dest in DESTS:
            assert f'href="{dest}"' in html, f"{path} cannot reach {dest}"

    @pytest.mark.parametrize("path", PAGES)
    def test_every_page_can_get_home(self, client, path):
        assert 'href="/"' in body(client, path), path

    @pytest.mark.parametrize("path,expected", [
        ("/floor", "/floor"), ("/checklists", "/checklists"),
        ("/maintenance", "/maintenance"), ("/logs", "/logs")])
    def test_the_current_page_is_marked(self, client, path, expected):
        html = body(client, path)
        m = re.search(r'<a[^>]*class="[^"]*navitem[^"]*on[^"]*"[^>]*'
                      r'href="([^"]+)"', html) or \
            re.search(r'<a[^>]*href="([^"]+)"[^>]*class="[^"]*navitem[^"]*on',
                      html)
        assert m and m.group(1) == expected, f"{path} does not mark itself"

    def test_the_selector_marks_nothing_as_current(self, client):
        """The root is the way in, not one of the destinations."""
        html = body(client, "/")
        assert not re.search(r'class="[^"]*navitem on', html)

    @pytest.mark.parametrize("path", PAGES)
    def test_nav_items_are_labelled_not_just_icons(self, client, path):
        """An icon rail nobody can read is a puzzle, not navigation."""
        html = body(client, path)
        for label in ("Map", "Checklists", "Logs"):
            assert label in html, f"{path} missing label {label}"


# ── who you are, everywhere ─────────────────────────────────────────────────

class TestIdentityOnEveryPage:
    @pytest.mark.parametrize("path", PAGES)
    def test_every_page_says_who_is_signed_in(self, client, path):
        """/logs had neither, so you couldn't tell who you were on it."""
        assert 'id="who"' in body(client, path), path

    @pytest.mark.parametrize("path", PAGES)
    def test_every_page_can_sign_in_or_out(self, client, path):
        assert 'id="btnAuth"' in body(client, path), path


# ── nothing may be clipped away ─────────────────────────────────────────────

class TestNothingIsClipped:
    @pytest.mark.parametrize("path", PAGES)
    def test_no_toolbar_hides_its_overflow(self, client, path):
        """`overflow:hidden` on the floor's bar silently ate Sign in and the
        LabCore-offline warning on a narrow window."""
        css = style_block(body(client, path))
        for rule in re.findall(r"\.(?:bar|tools)\{([^}]*)\}", css):
            assert "overflow:hidden" not in rule.replace(" ", ""), path

    def test_the_floor_separates_page_tools_from_global_chrome(self, client):
        """19 controls in one row was the actual problem."""
        html = body(client, "/floor")
        assert 'class="tools"' in html

    def test_the_floor_tools_row_can_scroll_or_wrap(self, client):
        # `.tools` is the shared shell's rule, not the page's.
        css = client.get("/static/lem.css").get_data(as_text=True)
        m = re.search(r"\.tools\{([^}]*)\}", css)
        assert m
        rule = m.group(1).replace(" ", "")
        assert "overflow-x:auto" in rule or "flex-wrap:wrap" in rule


# ── the floor still gets its width ──────────────────────────────────────────

class TestFloorWidth:
    def test_the_rails_are_narrower_than_before(self, client):
        """The rail cost 612px of a 1366px laptop; the map is the page."""
        css = style_block(body(client, "/floor"))
        m = re.search(r"\.shell\{[^}]*grid-template-columns:\s*([^;}]+)", css)
        assert m, "floor shell grid not found"
        widths = [int(x) for x in re.findall(r"(\d+)px", m.group(1))]
        assert sum(widths) <= 560, f"rails still cost {sum(widths)}px"

    def test_the_nav_rail_is_slim(self, client):
        css = client.get("/static/lem.css").get_data(as_text=True)
        m = re.search(r"--navw:\s*(\d+)px", css)
        assert m and int(m.group(1)) <= 96
