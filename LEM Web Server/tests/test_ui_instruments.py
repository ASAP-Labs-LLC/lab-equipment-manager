"""GET /api/ui/instruments and the Instruments home (ia-final §1, §3.2, §5).

What this answers, and why each part is a test rather than a hope:

* **Can it run? at 0 clicks (T1).** The home page exists so that "is Agilent
  GC 2 OK to run?" is answered by looking, not by opening a record. The answer
  is ONE rule, `ui_live.readiness`, computed server-side. The nav's "6 need
  you", the Needs-you card and the table all read it, so they cannot disagree
  (§0.2): a nav that said 6 above a card that showed 5 was a judged defect.
* **0 LabCore ops.** The page polls this whenever the live feed says an
  instrument changed. It is memory only: the snapshot, the live road and the
  overrides the snapshot already read. `CountingGateway` counts every road in,
  the reachability probe included, cold and warm.
* **`/api/machines` is untouched, byte for byte.** Benches, the floor and
  GC hub read it. The readiness verdict lives in its own route precisely so
  that it never grows a field there (judge J3 caught a proposal that did).
* **A failed read is never an empty result.** Before the first snapshot the
  answer is "not read yet", with `instruments: None`, never `[]`: an empty
  list would draw "Nothing needs you" over a lab nobody has looked at.
* **Say each problem once (§0, §12 "Instruments repeats problems three
  times").** Problems are enumerated in the Needs-you card only. Instruments
  with the same cause merge into one tile, the page-head gets ONE fleet pill,
  and the filter chips carry no counts.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import demo_floor
import ui_instruments
import ui_live
from labcore_gateway import FakeLabCoreGateway
from live_presence import LivePresence
from web_app import create_app

ROOT = Path(__file__).resolve().parent.parent
T = ROOT / "templates"
NOW = datetime(2026, 10, 1, 13, 30, 0)


class CountingGateway(FakeLabCoreGateway):
    """Every road into LabCore, counted: reads, writes and the probe."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def sql(self, *a, **k):
        self.calls.append(("sql", str(a[0])[:50] if a else ""))
        return super().sql(*a, **k)

    def read_sql(self, *a, **k):
        self.calls.append(("read_sql", str(a[0])[:50] if a else ""))
        return super().read_sql(*a, **k)

    def write(self, *a, **k):
        self.calls.append(("write", ""))
        return super().write(*a, **k)

    def is_running(self, *a, **k):
        self.calls.append(("is_running", ""))
        return super().is_running(*a, **k)


class StubAuth:
    def login(self, u, p):
        return ("Kaden Ortiz", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


def _app(gw, tmp_path, live=None):
    app = create_app(gw, authenticator=StubAuth(), secret="s",
                     documents_root=str(tmp_path), live=live or LivePresence(),
                     live_token="test-token")
    app.config["TESTING"] = True
    return app


def _seeded(tmp_path):
    gw = CountingGateway()
    app = _app(gw, tmp_path)
    app.config["SNAPSHOTS"].ensure_schema()
    demo_floor.seed(gw, documents_root=str(tmp_path))
    app.config["SNAPSHOTS"].refresh()
    return app, gw


# ── a machine, as /api/machines serves it (the shapes production uses) ──────

def machine(uid, title=None, *, specs=None, targets=None, maint=None, status="GREEN",
            running=True, module_state="running", live=False, level="L1", pos=(1.0, 1.0),
            watching="single_csv C:/data/results.csv", last_poll="2026-10-01T13:15:00"):
    return {"machine_uid": uid, "title": title or uid, "effective_specs": specs or [],
            "qc_targets": targets or [], "maintenance": maint or [], "status": status,
            "reason": "", "module_running": running, "module_state": module_state,
            "live": live, "level_uid": level, "pos": list(pos) if pos else None,
            "watching": watching, "last_poll": last_poll}


def spec(test, ok, at="2026-10-01T11:23:00", sample="AF26"):
    return {"test_name": test, "last_qc_in_spec": ok, "last_qc_at": at if ok is not None else "",
            "last_qc_superseded_by": "", "sample_id": sample, "low": 1.0, "high": 2.0,
            "expected": 1.5, "last_qc_value": 1.4}


def task(kind, status, name=None):
    return {"uid": "t-" + kind, "kind": kind, "status": status, "name": name or kind.upper(),
            "next_due": "2026-09-01", "reason": ""}


LEVELS = [{"uid": "L1", "name": "Ground Floor", "rank": 0},
          {"uid": "L2", "name": "Upper Lab", "rank": 1}]


def build(machines, overrides=None, levels=LEVELS):
    return ui_instruments.build(machines=machines, overrides=overrides or {}, levels=levels,
                                href=lambda uid, sec: "/instruments/%s%s" % (
                                    uid, "#" + sec if sec else ""))


def row(payload, uid):
    return next(r for r in payload["instruments"] if r["uid"] == uid)


# ── the readiness object ────────────────────────────────────────────────────

class TestEachInstrumentSaysWhetherItCanRun:
    def test_the_shape_the_record_page_will_read(self):
        """`{state, reason, next, tiles}` per uid (§5). The record (piece 5)
        draws its readiness card from exactly this, so the list row and the
        record's card are one computation and cannot say two things."""
        p = build([machine("gc1", "Agilent GC 1", specs=[spec("IBP", False)])])
        r = row(p, "gc1")["readiness"]
        assert {"state", "word", "glyph", "reason", "next", "tiles"} <= set(r)
        assert r["state"] == "not_ok" and r["word"] == "Not OK to run"
        assert r["glyph"] == "error"
        assert "IBP" in r["reason"]
        assert r["next"]["href"] == "/instruments/gc1#qc"

    def test_it_is_the_same_rule_as_the_live_feed(self):
        """One rule (§3.1), not a second copy that drifts: every state here is
        `ui_live.readiness` of the same machine and override."""
        ms = [machine("a", specs=[spec("X", False)]),
              machine("b", specs=[spec("X", None)]),
              machine("c", maint=[task("calibration", "RED")], specs=[spec("X", True)]),
              machine("d", running=False, module_state="stopped", specs=[spec("X", True)]),
              machine("e"),
              machine("f", specs=[spec("X", True)]),
              machine("g", specs=[spec("X", True)])]
        p = build(ms, overrides={"g": "SERVICE"})
        for m in ms:
            want = ui_live.readiness(m, {"g": "SERVICE"}.get(m["machine_uid"], ""))
            assert row(p, m["machine_uid"])["readiness"]["state"] == want["state"], m["machine_uid"]

    def test_words_and_glyphs_never_colour_alone(self):
        """§4.1: every state is a glyph SHAPE plus a word. Six states, six
        different glyph classes, so a colour-blind reader can still tell them."""
        ms = [machine("a", specs=[spec("X", False)]), machine("b", specs=[spec("X", None)]),
              machine("d", running=False, module_state="stopped", specs=[spec("X", True)]),
              machine("e"), machine("f", specs=[spec("X", True)]), machine("g")]
        p = build(ms, overrides={"g": "DEAD-LINE"})
        got = {row(p, u)["readiness"]["state"]: (row(p, u)["readiness"]["word"],
                                                  row(p, u)["readiness"]["glyph"])
               for u in "abdefg"}
        assert got == {"not_ok": ("Not OK to run", "error"),
                       "ok_but": ("OK to run, but…", "half"),
                       "cant_tell": ("Can't tell", "dashed"),
                       "no_qc": ("No QC assigned", "never"),
                       "ok": ("OK to run", "final"),
                       "off_line": ("Off line", "off")}

    def test_an_overdue_calibration_is_a_but_not_a_no(self):
        """Ryan, 2026-10-01: only QC or an override can make the answer No.
        The list must not tell an analyst to stop using a bench whose QC
        passed this morning because a calibration date lapsed."""
        p = build([machine("a", maint=[task("calibration", "RED")], specs=[spec("X", True)])])
        r = row(p, "a")
        assert r["readiness"]["word"] == "OK to run, but…"
        assert r["cause"]["words"] == "Calibration overdue"
        assert r["readiness"]["next"]["href"] == "/instruments/a#maintenance"
        assert p["fleet"]["pill"]["text"] == "It can run"

    def test_an_ok_instrument_has_no_next_step(self):
        p = build([machine("f", specs=[spec("X", True)])])
        assert row(p, "f")["readiness"]["next"] is None

    def test_tiles_are_qc_bench_on_line_and_maintenance_only_with_a_schedule(self):
        """§3.1: Maintenance is a tile only when the instrument has a scheduled
        task. Production has 0 rows in lem_maintenance today, so most records
        show three tiles, not a fourth saying "nothing scheduled"."""
        bare = row(build([machine("a", specs=[spec("X", True)])]), "a")["readiness"]["tiles"]
        assert [t["key"] for t in bare] == ["qc", "bench", "online"]
        with_pm = row(build([machine("a", specs=[spec("X", True)],
                                     maint=[task("pm", "GREEN")])]), "a")["readiness"]["tiles"]
        assert [t["key"] for t in with_pm] == ["qc", "bench", "online", "maintenance"]
        for t in with_pm:
            assert {"title", "word", "detail", "glyph", "current", "action"} <= set(t), t

    def test_the_bad_tile_is_the_current_one(self):
        tiles = row(build([machine("a", specs=[spec("X", False)])]), "a")["readiness"]["tiles"]
        assert [t["key"] for t in tiles if t["current"]] == ["qc"]


class TestNoVerdictYetIsNotNoQcAssigned:
    """§4.1 and the Eravap mislabel judge J3 caught: an instrument that HAS an
    assignment but no verdict (never run, or its bench stopped) must not read
    "No QC assigned". That sentence tells a supervisor to go and assign QC to
    something that already has it."""

    def test_assigned_but_never_run(self):
        p = build([machine("eravap", targets=[{"sample": "Pentane", "test": "RVP"}],
                           running=False, module_state="stopped")])
        assert row(p, "eravap")["last_qc"]["word"] == "No verdict yet"

    def test_nothing_assigned(self):
        p = build([machine("kf")])
        assert row(p, "kf")["last_qc"]["word"] == "No QC assigned"

    def test_a_run_check_names_its_newest_result(self):
        p = build([machine("a", specs=[spec("Flash Point", True, "2026-10-01T09:00:00"),
                                       spec("Density", True, "2026-10-01T11:00:00")])])
        lq = row(p, "a")["last_qc"]
        assert lq["at"] == "2026-10-01T11:00:00" and lq["test"] == "Density"
        assert lq["checks"] == 2


class TestTheTableOrder:
    def test_worst_first_then_by_name_then_uid(self):
        """Sorted worst first, ties broken by (title.lower(), uid) so two
        reloads never swap rows under somebody's finger."""
        ms = [machine("z", "beta", specs=[spec("X", True)]),
              machine("y", "Alpha", specs=[spec("X", True)]),
              machine("x", "alpha", specs=[spec("X", True)]),
              machine("w", "Zed", specs=[spec("X", False)])]
        assert [r["uid"] for r in build(ms)["instruments"]] == ["w", "x", "y", "z"]


# ── the Needs-you card ──────────────────────────────────────────────────────

class TestNeedsYou:
    def test_same_cause_merges_into_one_tile(self):
        """§3.2: "OptiMPP 1 and OptiMPP 2 · QC due" is one tile. Five
        calibrations that lapsed together are one sentence, not five tiles."""
        ms = [machine("o1", "OptiMPP 1", specs=[spec("Cloud", None)]),
              machine("o2", "OptiMPP 2", specs=[spec("Pour", None)])]
        tiles = build(ms)["needs_you"]["tiles"]
        assert len(tiles) == 1
        t = tiles[0]
        assert t["cause"] == "QC due"
        assert [m["uid"] for m in t["members"]] == ["o1", "o2"]
        assert t["names"] == "OptiMPP 1 and OptiMPP 2"

    def test_a_tile_links_to_the_section_that_explains_it(self):
        one = build([machine("gc1", specs=[spec("IBP", False)])])["needs_you"]["tiles"][0]
        assert one["href"] == "/instruments/gc1#qc"
        cal = build([machine("a", maint=[task("calibration", "RED")], specs=[spec("X", True)])])
        assert cal["needs_you"]["tiles"][0]["href"] == "/instruments/a#maintenance"
        two = build([machine("a", specs=[spec("X", None)]),
                     machine("b", specs=[spec("X", None)])])["needs_you"]["tiles"][0]
        # a merged tile goes to the list filtered to exactly its members
        assert two["href"] == "/?cause=" + two["key"]

    def test_at_most_six_tiles_and_the_rest_are_counted_not_dropped(self):
        """Seven causes do not fit. The sixth tile becomes "+2 more" linking
        to the filter, so nothing that needs you silently falls off."""
        ms = [machine("a", specs=[spec("X", False)]),
              machine("b", specs=[spec("Y", False)]),          # same cause: QC out of spec
              machine("c", maint=[task("calibration", "RED")], specs=[spec("X", True)]),
              machine("d", specs=[spec("X", None)]),
              machine("e", specs=[spec("X", True)], maint=[task("pm", "RED")]),
              machine("f", running=False, module_state="stopped", specs=[spec("X", True)]),
              machine("g", running=False, module_state="unknown", last_poll="",
                      specs=[spec("X", True)]),
              machine("h", running=False, module_state="closed", specs=[spec("X", True)])]
        ny = build(ms)["needs_you"]
        assert ny["count"] == 8
        assert len(ny["tiles"]) <= 6
        shown = sum(len(t["members"]) for t in ny["tiles"] if not t.get("more"))
        more = [t for t in ny["tiles"] if t.get("more")]
        assert len(more) == 1 and shown + more[0]["more"] == 8
        assert more[0]["href"] == "/?filter=needs"

    def test_worst_cause_first(self):
        ms = [machine("d", "D", specs=[spec("X", None)]),
              machine("a", "A", specs=[spec("X", False)])]
        assert [t["cause"] for t in build(ms)["needs_you"]["tiles"]] == ["QC out of spec", "QC due"]

    def test_off_line_and_no_qc_are_not_problems_that_happened(self):
        """Off line is a decision somebody made, with a comment; No QC
        assigned is a filter. Neither is on the card (ui_live.NEEDS_YOU)."""
        ny = build([machine("a"), machine("b", specs=[spec("X", True)])],
                   overrides={"b": "SERVICE"})["needs_you"]
        assert ny == {"count": 0, "tiles": []}

    def test_the_count_is_the_live_feeds_count(self, tmp_path):
        """§0.2 one field, one count: the card's count and the nav's "N need
        you" are the same number for the same snapshot."""
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        live = c.get("/api/ui/live").get_json()
        inst = c.get("/api/ui/instruments").get_json()
        assert inst["needs_you"]["count"] == live["needs_you"]
        assert live["needs_you"] > 0, "the seed should have something that needs you"


class TestTheFleetPill:
    """ONE pill in the page-head (§3.2). It says the fleet's verdict, not a
    per-problem tally: the tallies were the "three times" defect."""

    def test_something_not_ok(self):
        p = build([machine("a", specs=[spec("X", False)]), machine("b", specs=[spec("X", True)])])
        assert p["fleet"]["pill"] == {"glyph": "error", "level": "error", "text": "1 not OK to run"}

    def test_all_can_run(self):
        p = build([machine("a", specs=[spec("X", True)]), machine("b"),
                   machine("c", specs=[spec("X", None)])])
        assert p["fleet"]["pill"] == {"glyph": "final", "level": "final", "text": "All 3 can run"}

    def test_nothing_wrong_but_some_cannot_be_told(self):
        p = build([machine("a", specs=[spec("X", True)]),
                   machine("b", running=False, module_state="stopped", specs=[spec("X", True)])])
        assert p["fleet"]["pill"]["text"] == "1 of 2 can run"
        assert p["fleet"]["pill"]["level"] == "held"


class TestFiltersAreViewsNotCounts:
    def test_levels_only_when_more_than_one_holds_instruments(self):
        one = build([machine("a"), machine("b")])
        assert one["levels"] == []
        two = build([machine("a"), machine("b", level="L2")])
        assert [lv["uid"] for lv in two["levels"]] == ["L1", "L2"]
        assert all(set(lv) == {"uid", "name"} for lv in two["levels"]), \
            "a level chip names a place; it carries no count"

    def test_maintenance_only_when_a_task_exists(self):
        assert build([machine("a")])["has_maintenance"] is False
        assert build([machine("a", maint=[task("pm", "GREEN")])])["has_maintenance"] is True

    def test_where_says_not_on_the_map(self):
        p = build([machine("a", pos=None), machine("b")])
        assert row(p, "a")["where"] == {"level": "Ground Floor", "placed": False}
        assert row(p, "b")["where"] == {"level": "Ground Floor", "placed": True}


class TestTheSourceCaption:
    """The caption under each name says where its results come from, in the
    lab's words. The strings are production's own `watching` values."""

    @pytest.mark.parametrize("watching,want", [
        ("single_csv //asapserver/Labsharedrive/GC Results/LEVEL 1/distill_results.csv", "Results file"),
        ("multi_csv C:/Users/Data/auto-grabner", "Results folder"),
        ("manual entry (no parsing)", "Typed in at the bench"),
        ("serial COM4 @9600", "Serial port COM4"),
        ("serial COm10 @9600", "Serial port COM10"),
        ("idle (not watching) — manual entry (no parsing)", "Not watching · Typed in at the bench"),
        ("idle (not watching) — multi_csv //asapserver/x/Unparsed", "Not watching · Results folder"),
        ("C:\\LabData\\dma-4500", "Results folder"),
        ("", "Source not reported"),
    ])
    def test_caption(self, watching, want):
        assert ui_instruments.source_caption(watching) == want


# ── the route ───────────────────────────────────────────────────────────────

class TestTheRoute:
    def test_warm_it_costs_labcore_nothing(self, tmp_path):
        app, gw = _seeded(tmp_path)
        c = app.test_client()
        gw.calls.clear()
        for _ in range(10):
            assert c.get("/api/ui/instruments").status_code == 200
        assert gw.calls == [], gw.calls

    def test_the_page_costs_labcore_nothing(self, tmp_path):
        app, gw = _seeded(tmp_path)
        c = app.test_client()
        gw.calls.clear()
        for path in ("/", "/instruments", "/instruments?filter=needs"):
            assert c.get(path).status_code == 200, path
        assert gw.calls == [], gw.calls

    def test_cold_it_says_not_read_and_does_not_read(self, tmp_path):
        """No snapshot yet: not `[]`, and not the request that builds it."""
        gw = CountingGateway()
        app = _app(gw, tmp_path)
        gw.calls.clear()
        body = app.test_client().get("/api/ui/instruments").get_json()
        assert gw.calls == [], gw.calls
        assert body["state"] == "not_read"
        assert body["instruments"] is None and body["needs_you"] is None
        assert body["fleet"] is None

    def test_a_failed_first_read_is_named_not_emptied(self, tmp_path):
        gw = CountingGateway()
        app = _app(gw, tmp_path)
        snaps = app.config["SNAPSHOTS"]
        snaps._last_error = "LabCoreUnavailable('queue 104 deep')"
        body = app.test_client().get("/api/ui/instruments").get_json()
        assert body["state"] == "unreadable"
        assert "104 deep" in body["error"]
        assert body["instruments"] is None

    def test_the_answer_is_memoised_per_snapshot(self, tmp_path):
        """Built once per snapshot cycle (§5): two polls between cycles are
        the same object, not two computations over 17 instruments."""
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        a = c.get("/api/ui/instruments").get_json()
        b = c.get("/api/ui/instruments").get_json()
        assert a == b
        assert a["built_at"]

    def test_no_store(self, tmp_path):
        app, _ = _seeded(tmp_path)
        r = app.test_client().get("/api/ui/instruments")
        assert r.headers["Cache-Control"] == "no-store"

    def test_a_get_is_not_a_person(self, tmp_path):
        """A page refetching on the live feed must not hold /healthz
        idle_seconds at zero (A.7): the updater deploys only when LEM is idle."""
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        before = c.get("/healthz").get_json()["idle_seconds"]
        import time
        time.sleep(1.1)
        c.get("/api/ui/instruments", headers={"X-LEM-Background": "1"})
        after = c.get("/healthz").get_json()["idle_seconds"]
        assert after >= before + 1


class TestMachinesIsByteForByte:
    """Golden: /api/machines answers the same bytes before and after the
    readiness route and the home page are asked, with the clock held still
    (its `age_seconds` is the only thing that moves on its own)."""

    def test_unchanged(self, tmp_path, monkeypatch):
        import snapshot_service

        app, _ = _seeded(tmp_path)

        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2030, 1, 1, 12, 0, 0)
        monkeypatch.setattr(snapshot_service, "datetime", Frozen)
        c = app.test_client()
        before = c.get("/api/machines").get_data()
        for _ in range(3):
            c.get("/api/ui/instruments")
            c.get("/")
            c.get("/api/ui/live")
        after = c.get("/api/machines").get_data()
        assert before == after
        body = json.loads(before)
        assert set(body) == {"age_seconds", "default_level", "ground_level", "labcore_online",
                             "levels", "machines", "stale"}
        for m in body["machines"]:
            assert "readiness" not in m and "ok_to_run" not in m, \
                "readiness lives in /api/ui/instruments, never on /api/machines"


# ── the pages ───────────────────────────────────────────────────────────────

class TestThePages:
    def test_root_and_instruments_are_the_instruments_page(self, tmp_path):
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        for path in ("/", "/instruments"):
            body = c.get(path).get_data(as_text=True)
            assert 'data-testid="instruments-page"' in body, path
            assert 'aria-current="page"' in body and 'data-nav="instruments"' in body

    def test_maintenance_is_a_filter_now(self, tmp_path):
        app, _ = _seeded(tmp_path)
        r = app.test_client().get("/maintenance")
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/instruments?filter=maintenance")

    def test_the_template_extends_the_shell_and_runs_no_inline_code(self):
        src = (T / "instruments.html").read_text(encoding="utf-8")
        assert src.lstrip().startswith('{% extends "_layout.html" %}')
        assert "<style" not in src
        assert not re.search(r"\son[a-z]+=", src)
        # the one <script> without src is the first paint's data, never code
        for tag in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", src):
            assert 'type="application/json"' in tag, tag

    def test_the_first_paint_carries_the_answer(self, tmp_path):
        """T1 at 0 clicks: the verdicts are in the page as it arrives, not
        behind a second request a slow tablet might not finish."""
        app, _ = _seeded(tmp_path)
        body = app.test_client().get("/").get_data(as_text=True)
        m = re.search(r'<script type="application/json" id="instruments-data">(.*?)</script>',
                      body, re.S)
        assert m, "no first-paint data"
        data = json.loads(m.group(1))
        assert data["state"] == "ready" and data["instruments"]
        assert "</" not in m.group(1), "the data island must not be able to close its tag"

    def test_find_box_and_footer(self, tmp_path):
        app, _ = _seeded(tmp_path)
        body = app.test_client().get("/").get_data(as_text=True)
        assert 'placeholder="Find an instrument or a Lab ID"' in body
        assert "Ctrl K" in body
        assert "LabStation › LEM module › New machine…" in body

    def test_a_name_cannot_close_the_data_island(self, tmp_path):
        """An instrument's title is typed by a person in LabStation. One
        named "</script><script>…" must stay data: the island escapes "<"."""
        app, gw = _seeded(tmp_path)
        gw.sql("UPDATE lem_machine_status SET title = ? WHERE machine_uid = ?",
               ["GC </script><script>alert(1)</script>", "gc-1"])
        app.config["SNAPSHOTS"].refresh()
        body = app.test_client().get("/").get_data(as_text=True)
        assert "<script>alert(1)" not in body
        m = re.search(r'<script type="application/json" id="instruments-data">(.*?)</script>',
                      body, re.S)
        titles = [r["title"] for r in json.loads(m.group(1))["instruments"]]
        assert "GC </script><script>alert(1)</script>" in titles

    def test_the_map_view_is_the_floor_until_it_moves_in(self, tmp_path):
        app, _ = _seeded(tmp_path)
        r = app.test_client().get("/?view=map")
        assert r.status_code == 302 and r.headers["Location"].endswith("/floor")

    def test_one_h1(self, tmp_path):
        app, _ = _seeded(tmp_path)
        body = app.test_client().get("/").get_data(as_text=True)
        assert len(re.findall(r"<h1\b", body)) == 1
