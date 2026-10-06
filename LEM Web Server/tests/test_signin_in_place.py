"""Sign in where you are (ia-final §8 T0, piece 3), by source and by response.

Before this, Sign in on Home, Checklists, PM & CAL and Logs ran
``location.href = '/floor'`` and nothing else: you landed on the map with no
dialog open, signed in there, and walked back (T0: 4 clicks, 2 typed, 2 extra
screens). A gated button signed out only printed "Sign in to …" under the
toolbar. Both are fixed by ONE sheet, the same markup on every page that has a
Sign in, opened in place and titled for what you were doing.

What is checked here without a browser (tests/test_ui_signin.py walks it):

* every page with a Sign in carries exactly one ``dialog#signin-sheet`` and no
  page sends you to /floor to sign in;
* the page knows it is signed out before any script runs (``body.anon``), so a
  gated control is dimmed on the first paint, not a second later;
* the no-JS road, ``/signin?next=``, signs you in and puts you back, and never
  follows a ``next`` off this server;
* Switch person ends the last person's LabCore session when the next one signs
  in, so a tablet passed between analysts does not leave tokens behind;
* dimmed is never dead: no gated rule carries ``pointer-events``;
* Mark done asks for its note in a sheet the page owns (so the sign-in can hand
  over to it), not in ``prompt()``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from labcore_gateway import FakeLabCoreGateway
from web_app import create_app

ROOT = Path(__file__).resolve().parent.parent
T = ROOT / "templates"
STATIC = ROOT / "static"

SHELL_PAGES = ["/settings", "/help", "/", "/logs", "/checklists/edit", "/checklists/edit/new",
               "/checklists/trends"]
# Every page is a shell page now. The pages drawn by the old nav partial,
# each with a Sign in of its own (the home chooser, the PM page, the floor),
# were deleted in piece 14; /checklists is a 302 to the round, and the round,
# the record and Instruments have their own sign-in checks
# (tests/test_round_page.py, test_ui_record*.py, test_ui_shell_pages.py).
SHELL_PAGES += ["/quality", "/quality/standards", "/instruments"]


class StubAuth:
    def __init__(self):
        self.logged_out = []
        self.n = 0

    def login(self, u, p):
        if p == "good":
            self.n += 1          # LabCore issues a new token per login
            return (u or "Kaden Ortiz", "tok-%s-%d" % (u or "k", self.n), "")
        if p == "down":
            return (None, "", "Connection error: timed out")
        return (None, "", "Invalid username or password.")

    def logout(self, t):
        self.logged_out.append(t)


@pytest.fixture
def auth():
    return StubAuth()


@pytest.fixture
def app(auth, tmp_path):
    a = create_app(FakeLabCoreGateway(), authenticator=auth, secret="s",
                   documents_root=str(tmp_path))
    a.config["TESTING"] = True
    return a


@pytest.fixture
def client(app):
    return app.test_client()


def page(client, path):
    r = client.get(path)
    assert r.status_code == 200, (path, r.status_code)
    return r.get_data(as_text=True)


def body_tag(html):
    m = re.search(r"<body\b[^>]*>", html)
    assert m, "no <body>"
    return m.group(0)


# ── one sheet, everywhere there is a Sign in ────────────────────────────────

class TestOneSheet:
    @pytest.mark.parametrize("path", SHELL_PAGES)
    def test_exactly_one_sign_in_sheet(self, client, path):
        html = page(client, path)
        sheets = re.findall(r'<dialog\b[^>]*\bid="signin-sheet"[^>]*>', html)
        assert len(sheets) == 1, f"{path}: {len(sheets)} sign-in sheets"
        assert re.search(r'class="[^"]*\bsheet\b', sheets[0])

    @pytest.mark.parametrize("path", SHELL_PAGES)
    def test_the_controller_is_loaded(self, client, path):
        assert re.search(r'<script src="/static/js/signin\.js\?v=', page(client, path)), path

    def test_the_sheet_is_one_partial_not_copies(self):
        """Four pages with four copies is how the old pages drifted apart."""
        uses = [p.name for p in T.glob("*.html") if 'id="signin-sheet"' in p.read_text()]
        assert uses == ["_signin.html"], uses

    def test_no_page_sends_you_to_the_floor_to_sign_in(self):
        """Every template and script, not a list of names: the pages this
        once named are gone, and the rule is for all of them."""
        files = sorted(T.glob("*.html")) + sorted((STATIC / "js").glob("*.js"))
        assert len(files) > 40
        for p in files:
            src = p.read_text()
            assert "the sign-in dialog lives there" not in src, p.name
            assert not re.search(r"location\.href\s*=\s*['\"]/floor['\"]", src), p.name

    def test_the_shell_chip_is_the_sign_in_when_signed_out(self, client):
        """T0 is two clicks: the chip, then Sign in. A chip that opens a menu
        with Sign in inside it is three."""
        html = page(client, "/settings")
        m = re.search(r'<a\b[^>]*\bid="user-chip"[^>]*>', html)
        assert m and 'href="/signin?next=%2Fsettings"' in m.group(0), "signed out, the chip is the way in"
        assert 'data-signin' in m.group(0)


# ── signed out is known before any script runs ──────────────────────────────

class TestAnonOnFirstPaint:
    @pytest.mark.parametrize("path", SHELL_PAGES)
    def test_signed_out_body_is_anon(self, client, path):
        tag = body_tag(page(client, path))
        assert re.search(r'class="[^"]*\banon\b', tag), tag
        assert 'data-user=""' in tag

    @pytest.mark.parametrize("path", SHELL_PAGES)
    def test_signed_in_body_is_not(self, client, path):
        with client.session_transaction() as s:
            s["user"] = "Cody"
        tag = body_tag(page(client, path))
        assert not re.search(r'class="[^"]*\banon\b', tag), tag
        assert 'data-user="Cody"' in tag


# ── the no-JS road ──────────────────────────────────────────────────────────

class TestSigninPage:
    def test_get_shows_a_form_that_returns_you(self, client):
        html = page(client, "/signin?next=/checklists")
        assert re.search(r'<form\b[^>]*method="post"[^>]*action="/signin"', html)
        assert re.search(r'name="next"[^>]*value="/checklists"', html)
        # the page IS the form: no second copy of it in a dialog
        assert 'id="signin-sheet"' not in html

    def test_good_credentials_go_back_to_next(self, client):
        r = client.post("/signin", data={"username": "cody", "password": "good", "next": "/checklists"})
        assert r.status_code == 303
        assert r.headers["Location"].endswith("/checklists")
        assert client.get("/api/me").get_json() == {"authenticated": True, "user": "cody"}

    def test_a_wrong_password_stays_with_the_reason(self, client):
        r = client.post("/signin", data={"username": "cody", "password": "nope", "next": "/logs"})
        assert r.status_code == 401
        html = r.get_data(as_text=True)
        assert "not accepted" in html
        assert re.search(r'name="username"[^>]*value="cody"', html), "the name typed is kept"
        assert re.search(r'name="next"[^>]*value="/logs"', html), "and so is where you were going"
        assert client.get("/api/me").get_json()["authenticated"] is False

    def test_labcore_down_is_not_a_wrong_password(self, client):
        r = client.post("/signin", data={"username": "cody", "password": "down", "next": "/"})
        assert r.status_code == 401
        html = r.get_data(as_text=True)
        assert "LabCore did not answer" in html and "not accepted" not in html

    @pytest.mark.parametrize("evil", ["//evil.example/x", "https://evil.example/", "/\\evil.example",
                                      "javascript:alert(1)", "/signin?next=/x"])
    def test_next_never_leaves_this_server(self, client, evil):
        r = client.post("/signin", data={"username": "cody", "password": "good", "next": evil})
        assert r.status_code == 303
        loc = r.headers["Location"]
        assert loc in ("/", "http://localhost/"), loc

    def test_already_signed_in_goes_straight_back(self, client):
        with client.session_transaction() as s:
            s["user"] = "Cody"
        r = client.get("/signin?next=/maintenance")
        assert r.status_code == 303 and r.headers["Location"].endswith("/maintenance")


# ── Switch person ───────────────────────────────────────────────────────────

class TestSwitchPerson:
    def test_the_last_persons_session_is_ended(self, client, auth):
        assert client.post("/api/login", json={"username": "cody", "password": "good"}).status_code == 200
        assert client.post("/api/login", json={"username": "ryan", "password": "good"}).status_code == 200
        assert auth.logged_out == ["tok-cody-1"]
        assert client.get("/api/me").get_json()["user"] == "ryan"

    def test_a_failed_switch_leaves_the_last_person_signed_in(self, client, auth):
        """Cancel, or a mistyped password, must not strand the bench signed
        out: the sheet says the last person stays until the next one is in."""
        client.post("/api/login", json={"username": "cody", "password": "good"})
        assert client.post("/api/login", json={"username": "ryan", "password": "bad"}).status_code == 401
        assert auth.logged_out == []
        assert client.get("/api/me").get_json()["user"] == "cody"

    def test_the_same_person_again_keeps_nothing_dangling(self, client, auth):
        client.post("/api/login", json={"username": "cody", "password": "good"})
        client.post("/api/login", json={"username": "cody", "password": "good"})
        # the token was re-issued; the old one is ended, the new one is kept
        assert auth.logged_out == ["tok-cody-1"]

    def test_the_menu_offers_it_signed_in(self, client):
        with client.session_transaction() as s:
            s["user"] = "Cody"
        assert 'id="menu-switch"' in page(client, "/settings")


# ── dimmed, never dead ──────────────────────────────────────────────────────

class TestGatedIsDimNotDead:
    # signin_legacy.css dimmed the old nav partial's pages; it went with them
    CSS = [STATIC / "css" / "lem.css"]

    @pytest.mark.parametrize("css", CSS, ids=lambda p: p.name)
    def test_a_gated_rule_exists_and_never_kills_the_click(self, css):
        src = css.read_text()
        rules = re.findall(r"body\.anon [^{]*(?:\.gated|\[data-gated\])[^{]*\{([^}]*)\}", src)
        assert rules, f"{css.name} has no signed-out rule for gated controls"
        for r in rules:
            assert "pointer-events" not in r, "a dead button is indistinguishable from a broken one"

    def test_the_shell_dims_with_a_token_not_opacity(self):
        """Opacity fails AA on text (the contrast walk in test_ui_theme would
        flag it); the muted text token is the dim that still reads."""
        src = (STATIC / "css" / "lem.css").read_text()
        m = re.search(r"body\.anon [^{]*\[data-gated\][^{]*\{([^}]*)\}", src)
        assert m and "var(--text-muted)" in m.group(1) and "opacity" not in m.group(1)


# ── the acts that wait for you ──────────────────────────────────────────────

class TestTheActsThatWait:
    # Mark done lives on the record's Maintenance and calibration section
    # (the fleet-wide PM page that had it was deleted in piece 14).

    def test_mark_done_is_gated_and_uses_a_sheet(self):
        js = (STATIC / "js" / "record_actions.js").read_text()
        assert "gated('mt-done', 'mark ' + lower(t.name) + ' done', 'Mark done…'" in js
        assert "prompt(" not in js, "a prompt() cannot be handed over to after sign-in"
        assert re.search(r'<dialog\b[^>]*id="done-sheet"', (T / "instrument.html").read_text())

    def test_the_note_is_required(self):
        src = (T / "instrument.html").read_text()
        assert re.search(r'<textarea\b[^>]*id="done-note"[^>]*\brequired\b', src)

    def test_saving_a_round_waits_for_sign_in(self):
        """The round's own tick is tests/test_round_page.py's; the editor's
        Save is gated, and a 401 mid-session opens the same sheet."""
        src = (T / "round_edit.html").read_text()
        assert re.search(r'id="ed-save"[^>]*data-gated="save the round"', src)
        js = (STATIC / "js" / "round_edit.js").read_text()
        assert "LEMSignIn.need('save the round', save)" in js
