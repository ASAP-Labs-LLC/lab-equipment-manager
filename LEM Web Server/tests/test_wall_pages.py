"""The wall kiosks, served: /floor, /qc and /wall (ia-final §3.8, piece 13).

Two TVs in the lab have had /floor and /qc bookmarked for a year. Nobody
stands at them, nobody signs in on them, and they poll forever. Each of
those facts is a test here:

* **The bookmarks keep working.** ``/floor`` and ``/qc`` answer 200 with no
  redirect (O11). A 302 to a page with a sidebar would put a sign-in button
  and a bell on a screen nobody can touch. The old floor stays reachable at
  ``/floor/classic`` until piece 14 deletes it, because some of its dialogs
  have no other door yet.
* **Chromeless, with a way home.** No sidebar, top bar, sign-in sheet, bell,
  toast or gated control: none of them can be used on a wall. The one thing
  that can be is the mark, which reads "LEM ›" and goes to ``/``, and the
  version stamp says what ``/healthz`` says (C's wall had no way home: J1).
* **0 LabCore ops per page load and per poll.** A wall polls every 3 s for
  as long as the TV is on. One read per poll would put the lab's screens on
  the serialised queue the benches write through. LabCore is counted
  (``CountingLabCore``) apart from the store; /floor and its poll do not even
  read the store: they are memory only. /qc reads chart history from the
  local store, never from LabCore, and at most once a minute.
* **A cold server is not made to read,** and does not say anything is fine:
  before the first snapshot the wall says it is reading.
* **The first paint is the answer.** The headline and the counts are in the
  HTML the server sends, so a TV that comes up before its scripts run still
  says something true, and the counts add up to the fleet.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import demo_floor
import web_app
from labcore_counter import CountingLabCore
from labcore_gateway import FakeLabCoreGateway
from web_app import create_app

ROOT = Path(__file__).resolve().parent.parent
T = ROOT / "templates"
WALLS = ["/floor", "/qc", "/wall"]
KIOSK = ["/floor?theme=dark&level=x&rotate=0", "/qc?theme=light&rotate=0",
         "/wall?show=floor,qc&every=60", "/wall?show=qc"]


class StubAuth:
    def login(self, u, p):
        return ("Kaden Ortiz", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


class CountingStore(FakeLabCoreGateway):
    """LEM's store, with its reads counted, so "memory only" is checked and
    not assumed."""

    def __init__(self):
        super().__init__()
        self.reads = []

    def read_sql(self, sql, args=None, **kw):
        self.reads.append(str(sql)[:60])
        return super().read_sql(sql, args, **kw)

    def sql(self, sql, args=None, **kw):
        if str(sql).lstrip().upper().startswith("SELECT"):
            self.reads.append(str(sql)[:60])
        return super().sql(sql, args, **kw)


def _split(tmp_path, seed=True):
    store, lab = CountingStore(), CountingLabCore()
    app = create_app(store, labcore=lab, authenticator=StubAuth(), secret="s",
                     documents_root=str(tmp_path), live_token="tok")
    app.config["TESTING"] = True
    if seed:
        app.config["SNAPSHOTS"].ensure_schema()
        demo_floor.seed(store, documents_root=str(tmp_path))
        app.config["SNAPSHOTS"].refresh()
    return app, store, lab


@pytest.fixture
def seeded(tmp_path):
    return _split(tmp_path)


def island(page, ident):
    m = re.search(r'<script type="application/json" id="%s">(.*?)</script>' % ident, page, re.S)
    assert m, ident
    return json.loads(m.group(1))


def text(fragment):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment or "")).strip()


# ── the bookmarks ───────────────────────────────────────────────────────────

class TestTheBookmarksKeepWorking:
    @pytest.mark.parametrize("path", WALLS + KIOSK)
    def test_200_with_no_redirect(self, seeded, path):
        app, _s, _l = seeded
        r = app.test_client().get(path, follow_redirects=False)
        assert r.status_code == 200, (path, r.status_code, r.headers.get("Location"))
        assert "Location" not in r.headers

    def test_signed_out_is_the_same_wall(self, seeded):
        """A TV is never signed in. The wall must not ask it to be."""
        app, _s, _l = seeded
        body = app.test_client().get("/floor").get_data(as_text=True)
        assert "signin" not in body.lower()

    def test_the_old_floor_is_kept_at_classic(self, seeded):
        app, _s, _l = seeded
        r = app.test_client().get("/floor/classic")
        assert r.status_code == 200
        assert "<title>LEM — Lab Floor</title>" in r.get_data(as_text=True)

    def test_retired_pages_still_land_on_the_wall(self, seeded):
        app, _s, _l = seeded
        for path in ("/stations", "/dashboard"):
            r = app.test_client().get(path)
            assert r.status_code == 302 and r.headers["Location"].endswith("/floor")


# ── chromeless ──────────────────────────────────────────────────────────────

class TestChromelessWithAWayHome:
    @pytest.mark.parametrize("path", WALLS)
    def test_no_shell_parts_that_cannot_be_used_on_a_wall(self, seeded, path):
        app, _s, _l = seeded
        page = app.test_client().get(path).get_data(as_text=True)
        for gone in ('id="sidebar"', 'data-testid="topbar"', 'id="signin-sheet"',
                     'id="bell', 'id="toast"', 'class="user-chip', "_nav.html", 'class="nav'):
            assert gone not in page, (path, gone)
        assert not re.search(r'class="[^"]*\bgated\b', page), path

    @pytest.mark.parametrize("path", WALLS)
    def test_the_mark_reads_lem_and_goes_home(self, seeded, path):
        app, _s, _l = seeded
        page = app.test_client().get(path).get_data(as_text=True)
        m = re.search(r'<a[^>]*class="[^"]*wall-mark[^"]*"[^>]*>(.*?)</a>', page, re.S)
        assert m, path
        assert 'href="/"' in m.group(0)
        assert text(m.group(1)) == "LEM ›"

    @pytest.mark.parametrize("path", WALLS)
    def test_the_version_is_healthzs(self, seeded, path):
        app, _s, _l = seeded
        c = app.test_client()
        health = c.get("/healthz").get_json()["version"]
        page = c.get(path).get_data(as_text=True)
        stamps = re.findall(r'<div[^>]*\bid="app-version"[^>]*>(.*?)</div>', page, re.S)
        assert [text(s) for s in stamps] == [health]
        assert health == web_app.APP_VERSION

    def test_no_inline_script_no_contextmenu(self):
        """Page code lives in static/js (§2), and nothing on a wall is a
        right-click away (§0.5)."""
        for name in ("_wall.html", "wall_floor.html", "wall_qc.html", "wall.html"):
            src = (T / name).read_text(encoding="utf-8")
            for tag in re.findall(r"<script\b[^>]*>", src):
                assert "src=" in tag or 'type="application/json"' in tag, (name, tag)
            assert "onclick=" not in src and "contextmenu" not in src, name
        for name in ("wall_floor.js", "wall_qc.js", "wall.js", "wall_logic.js"):
            js = (ROOT / "static" / "js" / name).read_text(encoding="utf-8")
            assert "contextmenu" not in js and "innerHTML" not in js, name
            assert not re.search(r"""['"]POST['"]""", js), name


# ── cost ────────────────────────────────────────────────────────────────────

# the live feed's transfer facts (ui_transfer.read_facts), as the fake store
# records them (the first 60 characters of each SELECT)
TRANSFER_FACTS = r"\b(result_confli|bench_token|bench_cursor)|^SELECT c\.machine_uid, c\.stats, c\.last_seen"


class TestZeroLabCoreOps:
    def test_floor_page_and_its_polls_read_nothing_at_all(self, seeded):
        """Memory only: not LabCore, and the wall's own routes not the store
        either. The one local read in the window is the live feed's
        transfer facts (T-P12's foot line, every page's: unresolved
        conflicts, re-enrolments, bench cursors), three SELECTs on the local
        store remembered for ui_transfer's TTL, so ten polls read them once.
        That read is the app shell's, not the wall's, and it never reaches
        LabCore; it is pinned here so a second one would show."""
        app, store, lab = seeded
        c = app.test_client()
        store.reads.clear()
        lab.calls.clear()
        c.get("/floor")
        assert store.reads == [], store.reads
        cursor = None
        for _ in range(10):
            live = c.get("/api/ui/live" + ("?since=" + cursor if cursor else "")).get_json()
            cursor = live["cursor"]
            n = len(store.reads)
            assert c.get("/api/ui/wall/floor").status_code == 200
            assert len(store.reads) == n, store.reads[n:]
        assert lab.calls == [], lab.calls
        assert len(store.reads) <= 3, store.reads
        for q in store.reads:
            assert re.search(TRANSFER_FACTS, q), q

    def test_qc_page_and_its_polls_cost_labcore_nothing(self, seeded):
        app, store, lab = seeded
        c = app.test_client()
        lab.calls.clear()
        store.reads.clear()
        c.get("/qc")
        for _ in range(10):
            c.get("/api/ui/live")
            assert c.get("/api/ui/wall/qc").status_code == 200
        c.get("/wall")
        assert lab.calls == [], lab.calls
        # the chart history is a local read, and it is remembered for a
        # minute: eleven loads and polls inside that minute read it once.
        # The live feed's transfer facts (three local SELECTs, TTL-cached,
        # the shell's not the wall's: see the floor test) are the rest.
        hist = [q for q in store.reads
                if not re.search(TRANSFER_FACTS, q)]
        assert len(hist) <= 1, store.reads
        assert len(store.reads) - len(hist) <= 3, store.reads

    def test_a_cold_server_is_not_made_to_read(self, tmp_path):
        """No snapshot yet: a TV reconnecting after a restart must not be the
        request that pays for the first build, and must not say "fine"."""
        app, store, lab = _split(tmp_path, seed=False)
        c = app.test_client()
        store.reads.clear()
        lab.calls.clear()
        page = c.get("/floor").get_data(as_text=True)
        body = c.get("/api/ui/wall/floor").get_json()
        qc = c.get("/api/ui/wall/qc").get_json()
        assert lab.calls == [] and store.reads == [], (lab.calls, store.reads)
        assert app.config["SNAPSHOTS"].get(build_if_missing=False)["ready"] is False
        assert body["wall"]["counts"] is None and body["wall"]["headline"] == "Reading the lab…"
        assert qc["cards"] is None
        assert "Reading the lab…" in page and "can run" not in text(page)


# ── the first paint ─────────────────────────────────────────────────────────

class TestTheFirstPaintIsTheAnswer:
    def test_headline_and_counts_are_in_the_html(self, seeded):
        app, _s, _l = seeded
        c = app.test_client()
        page = c.get("/floor").get_data(as_text=True)
        data = island(page, "wall-data")
        w = data["wall"]
        assert w["headline"] and w["headline"] in page
        assert w["sub"] in page.replace("&#39;", "'")
        shown = [int(n) for n in re.findall(r'<b class="wc-n">(\d+)</b>', page)]
        assert sum(shown) == w["total"] == len(data["instruments"])

    def test_counts_add_up_to_the_fleet_the_live_feed_counts(self, seeded):
        """Two surfaces, one fleet: the wall's counts and the live feed's
        fleet total are the same number (§0.2)."""
        app, _s, _l = seeded
        c = app.test_client()
        w = c.get("/api/ui/wall/floor").get_json()["wall"]
        live = c.get("/api/ui/live").get_json()
        assert sum(x["n"] for x in w["counts"]) == w["total"] == live["fleet"]["total"]
        assert w["benches"] == {"checking_in": live["fleet"]["checking_in"],
                                "total": live["fleet"]["total"]}

    def test_the_wall_carries_the_labs_clock(self, seeded):
        """The footer's clock is the lab's (lab_tz), not the TV's."""
        app, _s, _l = seeded
        data = island(app.test_client().get("/floor").get_data(as_text=True), "wall-data")
        assert "lab_tz" in data and "server_now" in data

    def test_qc_first_paint(self, seeded):
        app, _s, _l = seeded
        page = app.test_client().get("/qc").get_data(as_text=True)
        q = island(page, "wall-qc-data")
        assert q["headline"] in page
        assert q["cards"], "the demo floor has QC"
        words = {c["verdict"]["word"] for c in q["cards"]}
        assert words <= {"Out of spec", "QC due", "No verdict yet", "In spec"}

    def test_qc_verdicts_are_the_snapshots(self, seeded):
        """The wall's verdict for a check is the record's: the snapshot's
        last_qc_in_spec, the field the record page reads. Two §4.1 rules
        sit on top, and both are held here: every ASSIGNED check is a card
        (an assignment with no result yet is "No verdict yet", round 2's
        Eravap), and a stopped bench's old pass is "No verdict yet", never
        "In spec" (the floor calls it Can't tell). A failure is never
        softened by a stopped bench."""
        app, _s, _l = seeded
        q = app.test_client().get("/api/ui/wall/qc").get_json()
        snap = app.config["SNAPSHOTS"].get(build_if_missing=False)
        truth = {}
        for m in snap["machines"]:
            for sp in m.get("effective_specs") or []:
                if not sp.get("last_qc_superseded_by"):
                    truth[(m["machine_uid"], sp["test_name"])] = sp.get("last_qc_in_spec")
            for t in m.get("qc_targets") or []:
                name = " ".join(str(t.get("test") or t.get("test_name") or "").split())
                if name and not any(k[0] == m["machine_uid"] and k[1].lower() == name.lower() for k in truth):
                    truth[(m["machine_uid"], name)] = None
        assert len(q["cards"]) == len(truth)
        for c in q["cards"]:
            want = truth[(c["uid"], c["test"])]
            stopped = c["verdict"]["note"] == "bench stopped"
            if want is True:
                assert c["verdict"]["word"] == ("No verdict yet" if stopped else "In spec")
            elif want is False:
                assert c["verdict"]["word"] == "Out of spec"
            else:
                assert c["verdict"]["word"] in ("QC due", "No verdict yet")
