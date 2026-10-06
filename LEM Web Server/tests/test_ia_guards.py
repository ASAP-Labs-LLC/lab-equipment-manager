"""The guard list (ia-final §11) and the route table (§1), in one place.

Piece 14 deletes the pages the redesign replaced: `floor.html` (7,275 lines,
18 dialogs), `dashboard.html`, `stations.html`, `home.html`,
`maintenance.html` and `_nav.html`, ~13,100 lines in all (§10). Deleting is
the easy half. The hard half is that most of the defects the redesign exists
to fix were properties of THOSE files: a right-click menu nobody could find,
native `prompt()` boxes, bands printed through `toFixed(2)` ("0.00 – 0.00"
for sulfur), a floor that POSTed on a timer. The new pages were each built
without them, and each piece guarded its own page. Nothing yet guarded the
WHOLE tree, so a later change could put any of them back on a page whose
own tests never looked.

So every guard here scans everything that ships (every template, every
script under static/) or walks every route, rather than naming pages. A
guard that lists the pages it checks is a list somebody forgets to add to.

The few §11 items that are behaviour in a JS harness or a pure function
already have a test where the behaviour lives. They are not copied here: two
copies of a test drift. `TestTheGuardListIsWhole` instead names each one and
fails if its test is deleted or renamed, so §11 stays complete as a list.

Not here, and why:
* "no timer-driven POST on any page" needs a page running in a browser:
  `test_ui_guards_browser.py` idles every page with its clock sped up and
  counts what it sends.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import demo_floor
import snapshot_service
from labcore_counter import CountingLabCore
from labcore_gateway import FakeLabCoreGateway
from live_presence import LivePresence
from web_app import create_app

ROOT = Path(__file__).resolve().parent.parent
T = ROOT / "templates"
STATIC = ROOT / "static"
TESTS = ROOT / "tests"
NOW = datetime(2026, 10, 1, 13, 30, 0)

#: What piece 14 deletes (§10). Named once; every test below reads this.
DELETED_TEMPLATES = ("dashboard.html", "stations.html", "home.html",
                     "maintenance.html", "_nav.html", "floor.html")


class StubAuth:
    def login(self, u, p):
        return ("Kaden Ortiz", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


def _app(tmp_path, labcore=None):
    gw = FakeLabCoreGateway()
    app = create_app(gw, labcore_gateway=labcore, authenticator=StubAuth(), secret="s",
                     documents_root=str(tmp_path), live=LivePresence(),
                     live_token="test-token")
    app.config["TESTING"] = True
    return app, gw


def _seeded(tmp_path, labcore=None):
    app, gw = _app(tmp_path, labcore)
    app.config["SNAPSHOTS"].ensure_schema()
    demo_floor.seed(gw, documents_root=str(tmp_path), now=NOW)
    app.config["SNAPSHOTS"].refresh()
    return app, gw


@pytest.fixture
def seeded(tmp_path):
    return _seeded(tmp_path)[0]


def shipped_sources():
    """Everything a browser can be sent: every template and every script and
    stylesheet under static/. Read fresh, so a file added later is scanned."""
    files = sorted(T.rglob("*.html"))
    files += sorted(p for p in STATIC.rglob("*") if p.suffix in (".js", ".css", ".html", ".mjs"))
    assert len(files) > 40, "the scan sees almost nothing: is it looking in the right place?"
    return files


def code_lines(path: Path):
    """(line number, line) for every line, with `//` and `/* */` comments and
    Jinja `{# #}` comments blanked, so prose ABOUT a removed thing (this file
    is full of it) does not read as the thing."""
    text = path.read_text(encoding="utf-8")
    blank = lambda m: re.sub(r"[^\n]", " ", m.group(0))  # noqa: E731
    text = re.sub(r"\{#.*?#\}", blank, text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", blank, text, flags=re.S)
    text = re.sub(r"(?<![:\"'\\])//[^\n]*", blank, text)   # not https:// or '//'
    return list(enumerate(text.splitlines(), 1))


# ── §1: the route table, exactly ────────────────────────────────────────────

#: Every page URL §1 names, and what it answers. A redirect names where it
#: lands; `None` means 200 with no Location at all.
PAGE_TABLE = [
    ("/", 200, None),
    ("/instruments", 200, None),
    ("/?view=map", 200, None),
    ("/instruments/gc-1", 200, None),
    ("/instruments/no-such-uid", 404, None),
    ("/checklists/opening", 200, None),
    ("/checklists/closing", 200, None),
    ("/checklists/edit", 200, None),
    ("/checklists/edit/new?slot=opening", 200, None),
    ("/checklists/trends", 200, None),
    ("/quality", 200, None),
    ("/quality/standards", 200, None),
    ("/quality/standards/" + demo_floor.STANDARD, 200, None),
    ("/logs", 200, None),
    ("/settings", 200, None),
    ("/floor", 200, None),
    ("/qc", 200, None),
    ("/wall?show=floor,qc&every=60", 200, None),
    ("/healthz", 200, None),
    # the redirects §1 lists
    ("/maintenance", 302, "/instruments?filter=maintenance"),
    ("/stations", 302, "/floor"),
    ("/dashboard", 302, "/floor"),
    # an old /floor query that selected a machine lands on its record
    ("/floor?machine=gc-1", 302, "/instruments/gc-1"),
]

#: GET pages that exist and are not rows of §1's table, each with the
#: section that puts it there. Anything else is a page nobody specified.
EXTRA_PAGES = {
    "/help": "§1 nav: the user menu's Help",
    "/signin": "§3 piece 3: sign-in without script (signin.js makes it a sheet)",
    "/results/conflicts": "transfer-final: results a person decides (D1)",
    "/favicon.ico": "a 301 to the one icon, so browsers stop logging a 404",
    "/checklists": "§1: 302 to the slot open now (asserted below)",
    "/checklists/<slot>": "§1: /checklists/opening and /closing",
    "/checklists/edit/<uid>": "§1: one round in the editor (asserted below)",
    "/quality/standards/<path:name>": "§1: one standard",
    "/instruments/<machine_uid>": "§1: the record",
}


class TestTheRouteTable:
    @pytest.mark.parametrize("path,status,location", PAGE_TABLE,
                             ids=[p for p, _s, _l in PAGE_TABLE])
    def test_every_page_answers_as_section_1_says(self, seeded, path, status, location):
        r = seeded.test_client().get(path)
        assert r.status_code == status, (path, r.status_code, r.headers.get("Location"))
        if location is None:
            assert "Location" not in r.headers, (path, r.headers.get("Location"))
        else:
            got = urlsplit(r.headers["Location"])
            assert (got.path + ("?" + got.query if got.query else "")) == location

    def test_checklists_goes_to_a_slot(self, seeded):
        r = seeded.test_client().get("/checklists")
        assert r.status_code == 302
        assert urlsplit(r.headers["Location"]).path in ("/checklists/opening",
                                                        "/checklists/closing")

    def test_one_round_in_the_editor(self, seeded):
        c = seeded.test_client()
        with c.session_transaction() as s:
            s["user"] = "Ana"
        body = {"uid": "a1b2c3d4e5f6", "name": "Opening round", "slot": "opening",
                "due_time": "07:30", "items": [{"uid": "i1", "text": "Nitrogen",
                                                "entry_type": "none"}]}
        assert c.post("/api/checklists", json=body).status_code == 200
        assert c.get("/checklists/edit/a1b2c3d4e5f6").status_code == 200

    def test_the_old_machine_query_is_quoted_not_pasted(self, seeded):
        """The uid goes into a path. One with a slash or a `?` in it must
        land on that uid's record (or its honest 404), not on another URL."""
        r = seeded.test_client().get("/floor?machine=a/b?c")
        assert r.status_code == 302
        assert urlsplit(r.headers["Location"]).path == "/instruments/a%2Fb%3Fc"

    def test_the_wall_keeps_its_own_query(self, seeded):
        """Only `machine` is the old floor's. The wall's kiosk parameters
        are the wall's, and a TV bookmark with them must still get 200."""
        for path in ("/floor?theme=dark&level=x&rotate=0", "/floor?machine=", "/floor?level=L1"):
            r = seeded.test_client().get(path)
            assert r.status_code == 200 and "Location" not in r.headers, path

    def test_no_page_exists_that_section_1_does_not_name(self, seeded):
        """"Matches §1 exactly": a GET page that is neither a row above nor
        a named extra was added without the spec, and a page the deletion
        missed (`/floor/classic`, `/maintenance/classic`) shows up here."""
        named = {urlsplit(p).path for p, _s, _l in PAGE_TABLE} | set(EXTRA_PAGES)
        stray = sorted(r.rule for r in seeded.url_map.iter_rules()
                       if "GET" in r.methods and not r.rule.startswith(("/api/", "/static/"))
                       and r.rule not in named)
        assert stray == [], stray

    @pytest.mark.parametrize("path", ["/floor/classic", "/maintenance/classic",
                                      "/floor/classic?machine=gc-1"])
    def test_the_interim_doors_to_the_old_pages_are_gone(self, seeded, path):
        """Pieces 4 to 13 kept the old floor and PM page at /…/classic while
        some of their jobs had no other door. Every job has a door now (the
        record's sections, Settings › Imports), and neither URL was ever
        released (main has neither), so nobody's bookmark is lost."""
        assert seeded.test_client().get(path).status_code == 404


# ── §10: the deleted files stay deleted, and nothing names them ─────────────

class TestTheDeletedPagesAreGone:
    @pytest.mark.parametrize("name", DELETED_TEMPLATES)
    def test_the_template_is_gone(self, name):
        assert not (T / name).exists(), name

    def test_no_shipped_file_or_module_names_a_deleted_template(self):
        """A template name in an include, a render_template, a script or a
        test that reads it is a reference that either fails at runtime or
        keeps testing a file that is not shipped. Prose in a comment that
        says what a thing replaced is history, not a reference, so it is
        allowed only as `floor.html` inside a sentence; any quoted,
        included or path-like use is not."""
        pat = re.compile(r"""(templates/|["'(]|include\s+["'])(%s)\b"""
                         % "|".join(re.escape(n) for n in DELETED_TEMPLATES))
        hits = []
        for p in shipped_sources() + sorted(ROOT.glob("*.py")) + sorted(ROOT.glob("*.pyw")) \
                + sorted(TESTS.rglob("*.py")) + sorted(TESTS.rglob("*.mjs")):
            if p.name == Path(__file__).name:
                continue
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if pat.search(line):
                    hits.append("%s:%d %s" % (p.relative_to(ROOT), n, line.strip()[:90]))
        assert hits == [], hits

    def test_the_3d_site_and_its_vendored_three_js_are_gone(self, seeded):
        """decisions.md: "3D Site view: DELETE (static/world/,
        static/vendor/three*.js and the Site toggle)". Piece 12 removed the
        files; this keeps them out, served and named."""
        assert not (STATIC / "world").exists()
        assert not list(STATIC.rglob("three*.js"))
        c = seeded.test_client()
        for url in ("/static/world/index.js", "/static/vendor/three.module.min.js"):
            assert c.get(url).status_code == 404, url
        for p in shipped_sources():
            text = p.read_text(encoding="utf-8")
            assert "static/world" not in text and "three.module" not in text, p.name
            assert "importmap" not in text, p.name

    def test_the_old_pages_own_stylesheet_went_with_them(self):
        """`static/lem.css` and `css/signin_legacy.css` styled only the
        deleted pages. Left behind, they are 1,000+ lines of rules a later
        page could link and inherit the old look from."""
        assert not (STATIC / "lem.css").exists()
        assert not (STATIC / "css" / "signin_legacy.css").exists()
        for p in shipped_sources():
            text = p.read_text(encoding="utf-8")
            assert "/static/lem.css" not in text and "signin_legacy" not in text, p.name


# ── §11: the guards that are a scan of everything shipped ───────────────────

class TestNothingShippedBringsBackACutFeature:
    def test_no_contextmenu_listener(self):
        """The 20-item right-click menu was a door nobody could see (§10).
        Every action now has a visible place on the record."""
        hits = ["%s:%d" % (p.name, n) for p in shipped_sources()
                for n, line in code_lines(p) if "contextmenu" in line.lower()]
        assert hits == [], hits

    def test_no_native_prompt_alert_or_confirm(self):
        """A native box cannot be styled, cannot say who is signed in, and
        blocks the live feed while it is up (§10). The page's own sheets do
        this job everywhere."""
        call = re.compile(r"(?<![\w.$])(window\.)?(prompt|alert|confirm)\s*\(")
        hits = ["%s:%d %s" % (p.name, n, line.strip()[:80]) for p in shipped_sources()
                for n, line in code_lines(p) if call.search(line)]
        assert hits == [], hits

    def test_no_number_is_cut_to_two_places(self):
        """§4.2: a QC number keeps its band's decimals (fmtQC). toFixed(2)
        printed a sulfur band 0.0008 – 0.0014 as "0.00 – 0.00". Nothing
        shipped uses it, for any number: a money-style two places is never
        the right precision for a measurement."""
        hits = ["%s:%d %s" % (p.name, n, line.strip()[:80]) for p in shipped_sources()
                for n, line in code_lines(p) if "toFixed(2)" in line]
        assert hits == [], hits

    def test_the_scans_can_see_what_they_look_for(self, tmp_path):
        """A scan that strips too much passes on anything. Each pattern is
        found in a line of real code and not found in a comment about it."""
        f = tmp_path / "probe.js"
        f.write_text("// prompt( contextmenu toFixed(2)\n"
                     "el.addEventListener('contextmenu', f); const n = prompt('x');\n"
                     "/* alert( */ v.toFixed(2); fetch('https://x/y'); alert('a')\n")
        lines = dict(code_lines(f))
        assert "contextmenu" not in lines[1] and "prompt(" not in lines[1]
        assert "contextmenu" in lines[2] and "prompt(" in lines[2]
        assert "toFixed(2)" in lines[3] and "alert('a')" in lines[3]
        assert "alert( */" not in lines[3]


class TestTheWallsAre200AndUngated:
    @pytest.mark.parametrize("path", ["/floor", "/qc"])
    def test_the_tv_bookmarks_get_200_with_no_redirect(self, seeded, path):
        """O11: two TVs have had /floor and /qc bookmarked for a year."""
        r = seeded.test_client().get(path)
        assert r.status_code == 200 and "Location" not in r.headers
        assert 'http-equiv="refresh"' not in r.get_data(as_text=True).lower()

    @pytest.mark.parametrize("path", ["/floor", "/qc", "/wall?show=floor,qc&every=60"])
    def test_no_wall_carries_a_gated_control(self, seeded, path):
        """Nobody signs in at a TV. A gated control there is a sign-in sheet
        nobody can answer."""
        page = seeded.test_client().get(path).get_data(as_text=True)
        assert not re.search(r'class="[^"]*\bgated\b', page), path
        assert "data-gated" not in page, path

    def test_no_wall_script_makes_one_either(self):
        walls = sorted(STATIC.glob("js/wall*.js")) + sorted(T.glob("*wall*.html"))
        assert len(walls) >= 6, [p.name for p in walls]
        for p in walls:
            for n, line in code_lines(p):
                assert "gated" not in line, "%s:%d %s" % (p.name, n, line.strip())


# ── §11: the read routes cost LabCore nothing ───────────────────────────────

class TestTheUiReadsCostLabCoreNothing:
    def test_live_and_instruments_cold_and_warm(self, tmp_path):
        """These two are polled by every open page. One LabCore op each
        would put every browser in the lab on the serialised queue the
        benches write through (A.7). LabCore is counted apart from LEM's
        store, so a store read is not mistaken for one."""
        lab = CountingLabCore()
        app, _gw = _seeded(tmp_path, labcore=lab)
        c = app.test_client()
        lab.calls.clear()
        for _ in range(3):
            for url in ("/api/ui/live", "/api/ui/instruments", "/", "/floor", "/qc"):
                assert c.get(url).status_code == 200, url
        assert lab.ops == 0, lab.calls


# ── §11: /api/machines is the same bytes it was before the redesign ─────────

GOLDEN = TESTS / "fixtures" / "api_machines_golden.json"


def _machines_bytes(tmp_path, monkeypatch):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(snapshot_service, "datetime", Frozen)
    app, _gw = _seeded(tmp_path)
    return app, app.test_client().get("/api/machines").get_data()


def normalised(raw: bytes) -> dict:
    """Two things in /api/machines are not the code's: level uids are minted
    at random when the demo is seeded, and `level_moved_at` is the wall
    clock at seeding. Both are replaced by stable stand-ins; nothing else is
    touched."""
    body = json.loads(raw)
    names = {lv["uid"]: "LEVEL-%d" % i for i, lv in enumerate(body["levels"])}
    text = json.dumps(body, sort_keys=True)
    for uid, name in names.items():
        text = text.replace('"%s"' % uid, '"%s"' % name)
    text = re.sub(r'"level_moved_at": "[^"]*"', '"level_moved_at": "SEEDED"', text)
    return json.loads(text)


class TestApiMachinesIsGolden:
    """Benches, the old floor's users and GC hub read /api/machines; ia-final
    says it does not change (§0, §5, J3). The golden file is what `main`
    (ed04fa0, before any piece of the redesign) answered for this same
    seeded lab and frozen clock: produced by running main's web_app with
    this tree's demo_floor and checked equal to this tree's answer once
    the one sanctioned change is taken out. That change is transfer-final
    §848's additive `transfer` field, one per machine (13 here), which is
    the transfer spec's and not the UI's. Any other difference, a key, a
    value, a sort, is a change to a contract other programs read."""

    def test_the_answer_is_mains_plus_only_the_transfer_field(self, tmp_path, monkeypatch):
        _app, raw = _machines_bytes(tmp_path, monkeypatch)
        now = normalised(raw)
        for m in now["machines"]:
            assert "transfer" in m, m["machine_uid"]
            t = m.pop("transfer")
            assert t is None or set(t) == {"road", "unacked", "last_sync_age_s"}, t
        assert now == json.loads(GOLDEN.read_text(encoding="utf-8"))

    def test_asking_every_ui_route_does_not_move_a_byte(self, tmp_path, monkeypatch):
        app, before = _machines_bytes(tmp_path, monkeypatch)
        c = app.test_client()
        for url in ("/", "/?view=map", "/api/ui/instruments", "/api/ui/live",
                    "/instruments/gc-1", "/api/ui/instruments/gc-1", "/quality",
                    "/floor", "/qc", "/logs", "/settings"):
            c.get(url)
        assert c.get("/api/machines").get_data() == before

    def test_the_golden_is_a_real_answer(self):
        """A golden of `{}` would pass anything: it holds the fleet."""
        g = json.loads(GOLDEN.read_text(encoding="utf-8"))
        assert len(g["machines"]) == len(demo_floor.FLEET) == 13
        assert all(len(m) == 23 for m in g["machines"]), \
            "transfer-final §848: all 23 per-machine keys"
        assert [m["title"].lower() for m in g["machines"]] == \
            sorted(m["title"].lower() for m in g["machines"])


# ── §11: every list row opens its record ────────────────────────────────────

class TestEveryListRowHasARecordLink:
    def test_every_instruments_row_links_to_its_record(self, seeded):
        """The list is how a person reaches a record; a row with no link is
        a dead end (and the old floor's rows opened a side panel, not a URL
        anyone could share). The browser test checks the drawn rows too."""
        rows = seeded.test_client().get("/api/ui/instruments").get_json()["instruments"]
        assert len(rows) == 13
        for r in rows:
            assert r["href"] == "/instruments/" + r["uid"], r

    def test_every_quality_row_links_to_its_record(self, seeded):
        body = seeded.test_client().get("/api/ui/quality").get_json()
        rows = body.get("rows") or body.get("checks") or []
        assert rows, sorted(body)
        for r in rows:
            assert str(r.get("href", "")).startswith("/instruments/"), r


# ── §11 as a list: the guards that live with their behaviour ────────────────

#: (§11 item, test file, a string that is the test's own name or claim).
#: The behaviour is tested where it lives; this only fails if one goes.
GUARD_INDEX = [
    ("contrast, both themes", "test_ui_contrast.py", "def test_"),
    ("theme: System default, live switch, no flash", "test_ui_theme.py", "def test_"),
    ("every shell page: sidebar, way home, #app-version == /healthz",
     "test_ui_shell_pages.py", "app-version"),
    ("live JS", "js/live.mjs", "checks pass"),
    ("live poller JS", "js/live_poller.mjs", "checks passed"),
    ("ui_logic JS", "js/ui_logic.mjs", "check("),
    ("a tick paints before the POST resolves", "js/round.mjs",
     "the row is painted ticked synchronously, before any answer"),
    ("...and survives a live merge", "js/round.mjs", "a merge while saving leaves the row ticked"),
    ("a second tap on a ticked row sends nothing", "js/round.mjs",
     "...and sends nothing: Undo is the only way back"),
    ("the round editor posts once with items[]", "test_round_editor.py",
     "def test_three_items_in_one_post_make_one_checklist"),
    ("the sulfur 0.0011 band", "js/record_logic.mjs", "sulfur keeps its small values: 0.0011 stays 0.0011"),
    ("the 334.90 equal-decimals band", "js/record_logic.mjs",
     "the whole band shares its decimals: 334.90, never 334.9"),
    ("No verdict yet vs No QC assigned, assigned but stopped", "test_ui_wall.py",
     "def test_an_assignment_with_no_result_yet_is_a_card"),
    ("no timer-driven POST on any page", "test_ui_guards_browser.py",
     "def test_an_idle_page_sends_nothing_but_gets"),
    ("every drawn list row links to /instruments/", "test_ui_guards_browser.py",
     "def test_every_drawn_row_opens_a_record"),
]


class TestTheGuardListIsWhole:
    @pytest.mark.parametrize("item,path,needle", GUARD_INDEX, ids=[g[0] for g in GUARD_INDEX])
    def test_the_guard_is_still_there(self, item, path, needle):
        f = TESTS / path
        assert f.exists(), (item, path)
        assert needle in f.read_text(encoding="utf-8"), (item, path, needle)
