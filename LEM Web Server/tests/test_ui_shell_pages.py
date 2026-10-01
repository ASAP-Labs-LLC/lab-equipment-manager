"""The shell every new LEM page sits in, by source and by rendered page.

Ported in spirit from GC hub's ``tests/test_ui_shell_pages.py`` (which checks
GC's pages by source) and extended with what ia-final §1, §2 and §2.1 promise
for LEM:

* **A way home and a version on every page.** The mark links to ``/``, and
  ``#app-version`` reads exactly what ``/healthz`` reports — on the new shell
  pages AND on the pages still drawn by the old ``_nav.html``, because the
  stamp is how Ryan tells which release the updater put on the floor, and a
  page without it is the page somebody quotes a wrong version from.
* **Five nav items, five different icons.** One of the judges' complaints was a
  duplicated icon. The test compares the drawn paths, not the names.
* **Rail mode keeps its words.** Below 1400 px the labels go; each item keeps
  an ``aria-label`` with its meta, a numeric badge stands in for the meta, and
  the ``.rail-status`` strip says the record's age and the benches in words.
* **Nothing on the shell costs LabCore anything.** The status words come from
  the in-memory snapshot. A page render that issued a LabCore read would make
  every open tablet a load on the queue the snapshot exists to protect.
* **A failed read is never an empty result.** No snapshot yet says "Not read
  from LabCore yet"; it never says "0 of 0 benches".
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import web_app
from labcore_gateway import FakeLabCoreGateway
from web_app import create_app

ROOT = Path(__file__).resolve().parent.parent
T = ROOT / "templates"
JS = ROOT / "static" / "js"
CSS = ROOT / "static" / "css"

SHELL_PAGES = ["/settings", "/help"]
# pages still drawn by the old _nav.html (later pieces replace them)
OLD_PAGES = ["/", "/floor", "/maintenance", "/checklists", "/logs", "/checklists/trends"]
WALL_PAGES = ["/qc"]
NAV = [("instruments", "Instruments", "/"), ("checklists", "Checklists", "/checklists"),
       ("qc", "QC", None), ("log", "Log", "/logs"), ("settings", "Settings", "/settings")]


class StubAuth:
    def login(self, u, p):
        return ("Kaden Ortiz", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


def _app(gw=None, tmp_path=None):
    app = create_app(gw or FakeLabCoreGateway(), authenticator=StubAuth(), secret="s",
                     documents_root=str(tmp_path) if tmp_path else None)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(tmp_path):
    return _app(tmp_path=tmp_path).test_client()


@pytest.fixture
def signed_in(client):
    with client.session_transaction() as s:
        s["user"] = "Kaden Ortiz"
    return client


def html(client, path):
    r = client.get(path)
    assert r.status_code == 200, (path, r.status_code)
    return r.get_data(as_text=True)


VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "source", "track", "wbr"}


def by_id(page, ident):
    """The inner HTML of the element with this id, nested tags and all."""
    m = re.search(r'<(\w+)[^>]*\bid="%s"[^>]*>' % re.escape(ident), page)
    if not m:
        return None
    depth, i = 1, m.end()
    for t in re.finditer(r"<(/?)(\w+)[^>]*?(/?)>", page[m.end():]):
        name = t.group(2).lower()
        if name in VOID or t.group(3):
            continue
        depth += -1 if t.group(1) else 1
        if depth == 0:
            return page[m.end():m.end() + t.start()]
    return None


def text(fragment):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment or "")).strip()


# ── by source ───────────────────────────────────────────────────────────────

class TestTheLayoutIsGCsFrame:
    def test_shell_pages_extend_the_layout(self):
        for name in ("settings.html", "help.html"):
            assert (T / name).read_text(encoding="utf-8").lstrip().startswith(
                '{% extends "_layout.html" %}'), name

    def test_no_inline_style_script_or_handler_in_the_shell(self):
        """Page code lives in static/js; every server string goes in through
        textContent (§2). Inline handlers are where that rule leaks."""
        for name in ("_layout.html", "_shell.html", "settings.html", "help.html"):
            src = (T / name).read_text(encoding="utf-8")
            assert "<style" not in src, name
            assert not re.search(r"<script(?![^>]*\bsrc=)", src), name
            assert not re.search(r"\son[a-z]+=", src), name

    def test_the_theme_script_runs_in_head_before_body(self):
        """No light flash: <html data-theme> must be set before <body> exists,
        so shell.js is a plain (not deferred, not async) script in <head>."""
        src = (T / "_layout.html").read_text(encoding="utf-8")
        head = src.split("</head>")[0]
        tag = re.search(r"<script[^>]*js/shell\.js[^>]*>", head)
        assert tag, "shell.js is not in <head>"
        assert "defer" not in tag.group(0) and "async" not in tag.group(0)
        assert head.index("js/ui_logic.js") < head.index("js/shell.js")

    def test_storage_keys_are_lems_own(self):
        """GC hub and LEM can be open in one browser on one origin during
        testing; sharing `gc.theme` would let one app's choice flip the other."""
        js = (JS / "shell.js").read_text(encoding="utf-8")
        for key in ("lem.theme", "lem.sidebar", "lem.recent"):
            assert "'%s'" % key in js, key
        assert "'gc." not in js and '"gc.' not in js

    def test_the_version_stamp_is_the_last_element(self):
        """GC's rule: last in <body>, so no page can end before it."""
        src = re.sub(r"\{#.*?#\}", "", (T / "_layout.html").read_text(encoding="utf-8"), flags=re.S)
        body = src.split("<body")[1].split("</body>")[0]
        last = re.findall(r"<(\w+)[^>]*>", body)[-1]
        assert last == "div" and 'id="app-version"' in body[body.rindex("<div"):]


# ── rendered ────────────────────────────────────────────────────────────────

class TestEveryPageHasAWayHomeAndTheVersion:
    @pytest.mark.parametrize("path", SHELL_PAGES + OLD_PAGES + WALL_PAGES)
    def test_app_version_equals_healthz(self, signed_in, path):
        health = signed_in.get("/healthz").get_json()["version"]
        page = html(signed_in, path)
        stamps = re.findall(r'<(\w+)[^>]*\bid="app-version"[^>]*>(.*?)</\1>', page, re.S)
        assert len(stamps) == 1, (path, len(stamps))
        assert text(stamps[0][1]) == health, (path, text(stamps[0][1]), health)
        assert health == web_app.APP_VERSION

    def test_a_checkout_says_dev(self, signed_in):
        """CI writes VERSION into the release; a checkout has none and says
        `dev`. Not blank: a blank corner reads as "no version"."""
        if web_app.APP_VERSION != "dev":
            pytest.skip("this checkout has a VERSION file")
        assert text(by_id(html(signed_in, "/settings"), "app-version")) == "dev"

    @pytest.mark.parametrize("path", SHELL_PAGES)
    def test_the_mark_goes_home_and_there_is_one_h1(self, signed_in, path):
        page = html(signed_in, path)
        assert re.search(r'<a class="sb-mark" href="/"', page)
        assert len(re.findall(r"<h1\b", page)) == 1, path
        assert re.search(r'<a class="skip-link" href="#main"', page)
        assert re.search(r'\bid="main"', page)

    def test_the_menu_names_what_the_stamp_is_a_version_of(self, signed_in):
        """'dev' alone in a corner is a number nobody can act on (the old
        stamp's own test). The corner stays GC's bare string; the user menu's
        foot says "LEM dev"."""
        menu = by_id(html(signed_in, "/settings"), "user-menu")
        assert "LEM " + web_app.APP_VERSION in text(menu)


class TestTheNav:
    def _items(self, page):
        nav = re.search(r'<nav class="sb-nav"[^>]*>(.*?)</nav>', page, re.S).group(1)
        return re.findall(r'<a class="nav-item[^"]*" href="([^"]+)" data-nav="([^"]+)"[^>]*>(.*?)</a>',
                          nav, re.S)

    def test_five_items_in_order(self, signed_in):
        items = self._items(html(signed_in, "/settings"))
        assert [(k, text(re.sub(r'<span class="(nav-meta|rail-badge)".*?</span>', "", body)))
                for _href, k, body in items] == [(k, label) for k, label, _ in NAV]

    def test_each_icon_is_drawn_differently(self, signed_in):
        items = self._items(html(signed_in, "/settings"))
        drawings = [" ".join(re.findall(r'\sd="([^"]+)"|<(?:rect|circle)[^>]*>', body).__str__().split())
                    for _h, _k, body in items]
        assert len(set(drawings)) == 5, drawings

    def test_hrefs(self, signed_in):
        items = {k: href for href, k, _ in self._items(html(signed_in, "/settings"))}
        for key, _label, href in NAV:
            if href:
                assert items[key] == href, key

    def test_qc_goes_where_qc_lives(self):
        """QC's home is /quality (§1), which a later piece builds. Until it
        exists the nav must not lead to a 404: it goes to the QC wall, which
        is what "QC" in the nav has meant until now."""
        app = _app()
        has_quality = any(r.rule == "/quality" for r in app.url_map.iter_rules())
        c = app.test_client()
        with c.session_transaction() as s:
            s["user"] = "x"
        href = {k: h for h, k, _ in self._items(html(c, "/settings"))}["qc"]
        assert href == ("/quality" if has_quality else "/qc")
        assert c.get(href).status_code == 200

    def test_the_current_page_is_marked(self, signed_in):
        page = html(signed_in, "/settings")
        cur = re.findall(r'<a class="nav-item[^"]*"[^>]*data-nav="(\w+)"[^>]*aria-current="page"', page)
        assert cur == ["settings"]

    def test_every_item_keeps_its_words_for_the_rail(self, signed_in):
        """In rail mode the label is hidden; the accessible name must still
        say the page and its meta."""
        page = html(signed_in, "/settings")
        for _href, key, body in self._items(page):
            assert re.search(r'<span class="rail-badge"', body), key
            assert re.search(r'<span class="nav-meta"', body), key
        for key, label, _ in NAV:
            m = re.search(r'data-nav="%s"[^>]*aria-label="([^"]+)"' % key, page)
            assert m and m.group(1).startswith(label), key


class TestTheUserMenu:
    def test_signed_in(self, signed_in):
        menu = by_id(html(signed_in, "/settings"), "user-menu")
        words = text(menu)
        for want in ("Kaden Ortiz", "Theme", "System", "Light", "Dark", "Switch person",
                     "Settings", "Help", "Sign out"):
            assert want in words, want
        assert re.search(r'<div class="menu-foot">LEM %s</div>' % re.escape(web_app.APP_VERSION), menu)
        order = [w for w in ("Theme", "Switch person", "Settings", "Help", "Sign out", "LEM " + web_app.APP_VERSION)
                 if w in words]
        assert sorted(order, key=words.index) == order
        assert words.index("Sign out") < words.rindex("LEM ")

    def test_signed_out_offers_sign_in_not_sign_out(self, client):
        page = html(client, "/settings")
        chip = text(by_id(page, "user-chip"))
        assert "Sign in" in chip
        menu = text(by_id(page, "user-menu"))
        assert "Sign in" in menu and "Sign out" not in menu and "Switch person" not in menu

    def test_the_theme_control_is_three_radios(self, signed_in):
        menu = by_id(html(signed_in, "/settings"), "user-menu")
        assert re.findall(r'data-theme-choice="(\w+)"', menu) == ["system", "light", "dark"]

    def test_signing_in_and_out_round_trip(self, client):
        """The sheet posts to /api/login; the shell then shows the name."""
        r = client.post("/api/login", json={"username": "kaden", "password": "good"})
        assert r.status_code == 200
        assert "Kaden Ortiz" in text(by_id(html(client, "/settings"), "user-chip"))
        client.post("/api/logout")
        assert "Sign in" in text(by_id(html(client, "/settings"), "user-chip"))


class TestRailAndPhone:
    def test_the_words_strip_is_on_every_shell_page(self, signed_in):
        for path in SHELL_PAGES:
            strip = re.search(r'<div class="rail-status"[^>]*role="status"[^>]*>(.*?)</div>\s*</div>',
                              html(signed_in, path), re.S)
            assert strip, path

    def test_the_phone_bar_has_a_menu_and_the_mark(self, signed_in):
        page = html(signed_in, "/settings")
        bar = re.search(r'<header class="topbar"[^>]*>(.*?)</header>', page, re.S).group(1)
        assert re.search(r'id="drawer-open"[^>]*aria-controls="sidebar"', bar)
        assert re.search(r'class="phone-mark phone-only" href="/"', bar)

    def test_no_snapshot_yet_is_said_not_zeroed(self, signed_in):
        """No snapshot in memory is a failed-or-pending read, not an empty
        lab: no "0 of 0 benches", no QC badge."""
        page = html(signed_in, "/settings")
        strip = text(by_id(page, "rs-record"))
        assert strip == "Not read from LabCore yet", strip
        assert "0 of 0" not in page
        assert not re.search(r'<span class="rail-badge"[^>]*>\s*0\s*</span>', page)


class TestTheStatusWordsComeFromMemory:
    def _seeded(self, tmp_path):
        import demo_floor
        gw = FakeLabCoreGateway()
        app = _app(gw, tmp_path)
        app.config["SNAPSHOTS"].ensure_schema()
        demo_floor.seed(gw, documents_root=str(tmp_path))
        app.config["SNAPSHOTS"].refresh()
        return app, gw

    def test_the_strip_counts_benches_and_qc_from_the_snapshot(self, tmp_path):
        app, _gw = self._seeded(tmp_path)
        snap = app.config["SNAPSHOTS"].get(build_if_missing=False)
        machines = snap["machines"]
        total = len(machines)
        running = sum(1 for m in machines if m.get("module_running"))
        out = sum(1 for m in machines for s in (m.get("effective_specs") or [])
                  if s.get("last_qc_in_spec") is False and not s.get("last_qc_superseded_by"))
        assert total > 0 and out > 0, "the demo floor should have a QC failure to show"
        c = app.test_client()
        with c.session_transaction() as s:
            s["user"] = "x"
        page = html(c, "/settings")
        assert text(by_id(page, "rs-fleet")) == "%d of %d benches checking in" % (running, total)
        qc = re.search(r'data-nav="qc"[^>]*aria-label="([^"]+)"[^>]*>(.*?)</a>', page, re.S)
        assert qc.group(1) == "QC, %d out of spec" % out
        assert text(re.search(r'<span class="rail-badge"[^>]*>(.*?)</span>', qc.group(2)).group(1)) == str(out)
        assert text(re.search(r'<span class="nav-meta"[^>]*>(.*?)</span>', qc.group(2)).group(1)) == "%d out of spec" % out
        rec = re.search(r'id="rs-record"[^>]*data-at="([^"]+)"', page)
        assert rec and rec.group(1) == snap["built_at"]

    def test_rendering_the_shell_costs_labcore_nothing(self, tmp_path):
        app, gw = self._seeded(tmp_path)
        calls = []
        for name in ("sql", "read_sql"):
            orig = getattr(gw, name)

            def counted(*a, _orig=orig, _name=name, **kw):
                calls.append((_name, str(a[0])[:60] if a else ""))
                return _orig(*a, **kw)
            setattr(gw, name, counted)
        c = app.test_client()
        with c.session_transaction() as s:
            s["user"] = "x"
        for path in SHELL_PAGES:
            html(c, path)
        assert calls == [], calls


class TestSettingsThisBrowser:
    def test_the_section_and_its_controls(self, signed_in):
        page = html(signed_in, "/settings")
        sec = re.search(r'<section class="sec" id="browser"(.*?)</section>', page, re.S)
        assert sec, "no This browser section"
        assert "This browser" in text(sec.group(1))
        assert re.findall(r'data-choice="(\w+)"', by_id(page, "theme-choice")) == ["system", "light", "dark"]
        assert re.findall(r'data-choice="(\w+)"', by_id(page, "sidebar-choice")) == ["auto", "full", "rail"]

    def test_the_sub_nav_lists_only_sections_that_exist(self, signed_in):
        """No dead ends: every sub-nav link is a section on this page."""
        page = html(signed_in, "/settings")
        nav = re.search(r'<nav class="set-nav"[^>]*>(.*?)</nav>', page, re.S).group(1)
        targets = re.findall(r'href="#([\w-]+)"', nav)
        assert targets and targets[0] == "browser"
        for t in targets:
            assert re.search(r'<section class="sec" id="%s"' % t, page), t

    def test_diagnostics_says_what_healthz_says(self, signed_in):
        page = html(signed_in, "/settings")
        health = signed_in.get("/healthz").get_json()
        diag = text(re.search(r'<section class="sec" id="diagnostics"(.*?)</section>', page, re.S).group(1))
        assert health["version"] in diag
        assert "Not asked yet" in diag or "Reachable" in diag or "Not answering" in diag
