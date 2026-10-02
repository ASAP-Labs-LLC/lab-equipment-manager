"""The instrument record, /instruments/<uid> (ia-final §3.1, piece 5).

The record is the page every other page links to: a Needs-you tile, a table
row, a bell line, a search hit. It answers one question first, "can it run?",
and then shows the QC that answer rests on. Why each part is a test:

* **Readiness is one rule, said once.** The card reads the same object the
  Instruments home reads (`ui_instruments.instrument`, which reads
  `ui_live.readiness`). Ryan, 2026-10-01: only QC, or an override, can make
  the answer No; an overdue calibration or PM is a warning. A record that
  computed its own verdict could say "Not OK" under a row that says "OK to
  run, but…", and the lab would stop trusting both.
* **One emphasis.** At most one primary button on the page, and it is the
  next step: "Open a corrective action…" when QC stopped it, "Put back on
  line…" when somebody took it off line, nothing when there is nothing to do.
  A control is said once: when the primary already puts it back on line, the
  topbar and the On line tile do not offer the same thing again.
* **"No verdict yet" is not "No QC assigned"** (§4.1, judge J3's Eravap
  mislabel). Eravap has an assignment (Pentane / Reid Vapor Pressure) and a
  stopped bench. Calling that "No QC assigned" tells QA nothing is expected of
  it, which is false: something is expected and has not happened.
* **404 is not 503.** "There is no such instrument" is a statement about the
  lab; "LEM could not ask" is a statement about LEM. A record page that
  answered 404 while LabCore was down would tell a tech their instrument had
  been deleted.
* **0 LabCore ops to draw the record.** The page, and the JSON it refreshes
  from, read the in-memory snapshot. The chart's history is the one read a
  person pays for by opening it, as before.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import demo_floor
import ui_instruments
import ui_record
from labcore_gateway import FakeLabCoreGateway
from live_presence import LivePresence
from web_app import create_app

ROOT = Path(__file__).resolve().parent.parent
T = ROOT / "templates"
JS = ROOT / "static" / "js"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "machines_live_2026-10-01.json"


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


def _app(gw, tmp_path):
    app = create_app(gw, authenticator=StubAuth(), secret="s",
                     documents_root=str(tmp_path), live=LivePresence(),
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


# ── production's own shapes (the 2026-10-01 /api/machines capture) ─────────

PROD = json.loads(FIXTURE.read_text())
LEVELS = {lv["uid"]: lv["name"] for lv in PROD["levels"]}


def prod(title):
    return next(m for m in PROD["machines"] if m["title"] == title)


def href(uid, section):
    return "/instruments/%s%s" % (uid, ("#" + section) if section else "")


def record(m, override=""):
    row = ui_instruments.instrument(m, override, LEVELS, href)
    return ui_record.build(row, m, LEVELS, override=override)


def check(rec, title):
    return next(c for c in rec["qc"]["checks"] if c["title"] == title)


# ── the readiness card ──────────────────────────────────────────────────────

class TestTheCardSaysTheHomesVerdict:
    def test_it_is_the_rows_verdict_not_a_second_one(self):
        """The card and the home's Can it run? column are one computation."""
        for m in PROD["machines"]:
            row = ui_instruments.instrument(m, "", LEVELS, href)
            rec = ui_record.build(row, m, LEVELS, override="")
            assert rec["readiness"]["state"] == row["readiness"]["state"], m["title"]
            assert rec["readiness"]["word"] == row["readiness"]["word"], m["title"]

    def test_agilent_gc1_is_not_ok_because_of_qc(self):
        rec = record(prod("Agilent GC 1"))
        r = rec["readiness"]
        assert r["word"] == "Not OK to run"
        cap = r["caption"]
        # what failed, against which standard, and what to do about it
        assert cap["lead"] == "QC out of spec on 10% Recovery and 50% Recovery"
        assert cap["std"] == "AF26"
        assert cap["at"] == "2026-09-30T15:04:24.712184"
        assert cap["next"] == "Fix, then rerun AF26"

    def test_an_overdue_calibration_is_a_warning_not_a_stop(self):
        """Ryan, 2026-10-01: only QC or an override makes the answer No."""
        m = dict(prod("PAC Flash 2"), maintenance=[
            {"uid": "c", "kind": "calibration", "status": "RED", "name": "Annual calibration",
             "next_due": "2026-07-11", "interval_days": 365, "last_done": "2025-07-11"}])
        r = record(m)["readiness"]
        assert r["state"] == "ok_but" and r["word"] == "OK to run, but…"
        assert r["caption"]["lead"] == "Calibration overdue since 11 Jul"
        assert r["primary"] is None

    def test_pm_overdue_is_a_warning_too(self):
        m = dict(prod("PAC Flash 2"), maintenance=[
            {"uid": "p", "kind": "pm", "status": "RED", "name": "Monthly PM",
             "next_due": "2026-09-20", "interval_days": 30, "last_done": "2026-08-21"}])
        r = record(m)["readiness"]
        assert r["state"] == "ok_but" and r["primary"] is None

    def test_a_qc_stop_names_its_overdue_calibration_in_the_same_sentence(self):
        m = dict(prod("Agilent GC 1"), maintenance=[
            {"uid": "c", "kind": "calibration", "status": "RED", "name": "Annual calibration",
             "next_due": "2026-08-09", "interval_days": 365, "last_done": "2025-08-09"}])
        cap = record(m)["readiness"]["caption"]
        assert cap["too"] == "calibration overdue since 9 Aug too"


class TestOnePrimaryAndItIsTheNextStep:
    def test_qc_stop_opens_a_corrective_action(self):
        p = record(prod("Agilent GC 1"))["readiness"]["primary"]
        assert p["label"] == "Open a corrective action…"
        assert p["act"] == "action"
        # the action is filed against the check that failed first, by name
        assert p["test"] == "ASTM D2887/D86 - Distillation in Petroleum Products, 10% Recovery"

    def test_off_line_puts_it_back_and_says_it_once(self):
        rec = record(prod("PAC Flash 2"), override="SERVICE")
        assert rec["readiness"]["state"] == "off_line"
        assert rec["readiness"]["primary"] == {"label": "Put back on line…", "act": "online"}
        # in the lab's words, not the control table's code
        assert rec["readiness"]["caption"]["lead"] == "Taken off line for service"
        online = next(t for t in rec["readiness"]["tiles"] if t["key"] == "online")
        assert (online["word"], online["glyph"], online["detail"]) == \
            ("Off line", "off", "Taken off line for service")
        # the topbar's default action and the On line tile do not repeat it
        assert rec["topbar"]["online"] is None
        online = next(t for t in rec["readiness"]["tiles"] if t["key"] == "online")
        assert online["action"] is None

    @pytest.mark.parametrize("title", ["PAC Flash 2", "Agilent GC 2", "Eravap"])
    def test_nothing_to_do_here_means_no_primary(self, title):
        """OK, No QC assigned (assigning is the QC section's job) and Can't
        tell (the remedy is at the bench's own computer) have no button."""
        assert record(prod(title))["readiness"]["primary"] is None

    def test_the_topbar_takes_it_off_line_when_it_is_on(self):
        rec = record(prod("PAC Flash 2"))
        assert rec["topbar"]["online"] == {"label": "Take off line…", "act": "offline"}

    def test_the_template_has_exactly_one_primary(self):
        """The one button is drawn by the server and re-labelled by the page;
        no script makes another."""
        html = (T / "instrument.html").read_text()
        assert len(re.findall(r"\bbtn-primary\b", html)) == 1
        for name in ("record.js", "record_logic.js"):
            assert "btn-primary" not in (JS / name).read_text(), name


class TestTiles:
    def test_qc_bench_on_line_and_maintenance_only_when_scheduled(self):
        """Production has 0 scheduled tasks: three tiles, not an empty fourth."""
        keys = [t["key"] for t in record(prod("Agilent GC 1"))["readiness"]["tiles"]]
        assert keys == ["qc", "bench", "online"]
        m = dict(prod("Agilent GC 1"), maintenance=[
            {"uid": "p", "kind": "pm", "status": "GREEN", "name": "Monthly PM",
             "next_due": "2026-10-20", "interval_days": 30, "last_done": "2026-09-20"}])
        keys = [t["key"] for t in record(m)["readiness"]["tiles"]]
        assert keys == ["qc", "bench", "online", "maintenance"]

    def test_the_qc_tile_is_current_when_qc_stopped_it(self):
        tiles = record(prod("Agilent GC 1"))["readiness"]["tiles"]
        assert [t["key"] for t in tiles if t["current"]] == ["qc"]

    def test_an_overdue_task_wears_the_warning_glyph_not_the_stop(self):
        """Ryan, 2026-10-01: an overdue calibration is a warning. The stop
        triangle is QC's; a Maintenance tile or task row that wore it would
        say "stop" in the one language the card reserves for a stop."""
        m = dict(prod("Agilent GC 1"), maintenance=[
            {"uid": "c", "kind": "calibration", "status": "RED", "name": "Annual calibration",
             "next_due": "2026-08-09", "interval_days": 365, "last_done": "2025-08-09"}])
        rec = record(m)
        mt = next(t for t in rec["readiness"]["tiles"] if t["key"] == "maintenance")
        assert (mt["word"], mt["glyph"]) == ("Overdue", "due")
        assert [(t["word"], t["glyph"]) for t in rec["maintenance"]] == [("Overdue", "half")]

    def test_every_tile_action_lands_on_this_page(self):
        """No dead ends: a tile links to a section the page has, or opens a
        sheet the page has."""
        rec = record(prod("Agilent GC 1"))
        sections = {s for s in ui_record.SECTIONS}
        for t in rec["readiness"]["tiles"]:
            a = t["action"]
            if a is None:
                continue
            if "href" in a:
                assert a["href"].split("#")[1] in sections, a
            else:
                pytest.fail("a tile offers a control the page already has: %r" % a)

    def test_on_line_is_offered_once(self):
        """Topbar "Take off line…" while on line; the card's primary "Put back
        on line…" while off. Never also on the tile."""
        for ov in ("", "SERVICE"):
            rec = record(prod("PAC Flash 2"), override=ov)
            online = next(t for t in rec["readiness"]["tiles"] if t["key"] == "online")
            assert online["action"] is None
            offers = [x for x in (rec["topbar"]["online"], rec["readiness"]["primary"]) if x]
            assert len(offers) == 1, offers


# ── the QC section ──────────────────────────────────────────────────────────

class TestTheQcTable:
    def test_distillation_reads_in_boiling_order(self):
        titles = [c["title"] for c in record(prod("Agilent GC 1"))["qc"]["checks"]]
        assert titles == ["IBP", "10% Recovery", "50% Recovery", "90% Recovery", "FBP"]

    def test_a_check_is_named_short_with_its_method_beneath(self):
        c = check(record(prod("Agilent GC 1")), "90% Recovery")
        assert c["method"] == "ASTM D2887/D86"
        assert (c["low"], c["expected"], c["high"]) == (331.48, 334.9, 338.32)
        assert c["units"] == "°C"
        assert c["value"] == 331.51 and c["verdict"]["word"] == "In spec"

    def test_viscosity_keeps_its_test_words(self):
        c = record(prod("Viscocity"))["qc"]["checks"][0]
        assert c["method"] == "ASTM D445 40C"
        assert c["title"] == "Viscosity - Kinematic at 40°C (cSt)"

    def test_the_first_failing_check_is_selected(self):
        """T2: the chart under the table is about the check that matters."""
        rec = record(prod("Agilent GC 1"))
        assert rec["qc"]["selected"] == "ASTM D2887/D86 - Distillation in Petroleum Products, 10% Recovery"

    def test_verdict_words_and_glyphs(self):
        rec = record(prod("Agilent GC 1"))
        assert check(rec, "10% Recovery")["verdict"] == {"key": "out", "word": "Out of spec", "glyph": "error", "detail": ""}
        assert check(rec, "IBP")["verdict"]["glyph"] == "final"

    def test_qc_due_is_its_own_word(self):
        m = prod("PAC Flash 2")
        s = dict(m["effective_specs"][0], last_qc_in_spec=None)
        c = record(dict(m, effective_specs=[s]))["qc"]["checks"][0]
        assert c["verdict"]["word"] == "QC due" and c["verdict"]["glyph"] == "half"

    def test_standards_are_named_for_the_intro_sentence(self):
        assert record(prod("Agilent GC 1"))["qc"]["standards"] == ["AF26"]
        assert record(prod("OptiMPP 1"))["qc"]["standards"] == ["CP", "PP"]


class TestNoVerdictYetIsNotNoQcAssigned:
    def test_eravap(self):
        """Assigned (Pentane / RVP), bench stopped, never judged."""
        rec = record(prod("Eravap"))
        assert rec["qc"]["assigned"] is True
        (c,) = rec["qc"]["checks"]
        assert c["title"] == "Reid Vapor Pressure (VPx)"
        assert c["sample_id"] == "Pentane"
        assert c["verdict"]["word"] == "No verdict yet"
        assert c["verdict"]["detail"] == "bench stopped"
        assert c["low"] is None and c["value"] is None
        qc_tile = next(t for t in rec["readiness"]["tiles"] if t["key"] == "qc")
        assert qc_tile["word"] == "No verdict yet"
        assert "No QC assigned" not in json.dumps(rec)

    def test_nothing_assigned_is_said_as_such(self):
        rec = record(prod("Agilent GC 2"))
        assert rec["qc"]["assigned"] is False and rec["qc"]["checks"] == []
        assert rec["readiness"]["word"] == "No QC assigned"


class TestTheHead:
    def test_meta_line(self):
        rec = record(prod("Agilent GC 1"))
        h = rec["head"]
        assert h["bench"] == {"word": "Checking in", "at": "2026-10-01T13:15:56.465074"}
        assert h["last_result_at"] == "2026-10-01T08:46:26.348665"
        assert h["level"] == "Lab testing Machines"
        assert h["uid"] == "bf8e64b59f12"

    def test_maintenance_section_has_the_tasks(self):
        rec = record(prod("Agilent GC 1"))
        assert rec["maintenance"] == []

    def test_bench_section_never_invents_a_zero(self):
        """The transfer guard's counters are not reported by today's module:
        the record says so in words, and never 0 (§3.1 #7, §7)."""
        b = record(prod("Agilent GC 1"))["bench"]
        assert b["reads_from"].startswith("single_csv //asapserver")
        assert b["replays_not_resent"] is None
        assert b["live_road"] is False


# ── the chart's range (24 runs · 90 days · All) ─────────────────────────────

class TestTheRange:
    PTS = [{"ts": "2026-%02d-15T10:00:00" % mo, "value": float(mo), "in_spec": True}
           for mo in range(1, 11)]

    def test_24_runs_is_the_last_24(self):
        pts = [{"ts": "2026-09-%02dT10:00:00" % (d % 28 + 1), "value": d, "in_spec": True} for d in range(30)]
        assert ui_record.trim_points(pts, "24", "2026-10-02T12:00:00") == pts[-24:]

    def test_90_days_counts_back_from_now(self):
        got = ui_record.trim_points(self.PTS, "90d", "2026-10-02T12:00:00")
        assert [p["value"] for p in got] == [7.0, 8.0, 9.0, 10.0]

    def test_all_is_all(self):
        assert ui_record.trim_points(self.PTS, "all", "2026-10-02T12:00:00") == self.PTS

    def test_an_unknown_range_is_refused_not_guessed(self):
        with pytest.raises(ValueError):
            ui_record.trim_points(self.PTS, "lots", "2026-10-02T12:00:00")


# ── the routes ──────────────────────────────────────────────────────────────

class TestTheRoutes:
    def test_the_record_costs_labcore_nothing(self, tmp_path):
        app, gw = _seeded(tmp_path)
        c = app.test_client()
        gw.calls.clear()
        r = c.get("/instruments/optimpp-1")
        assert r.status_code == 200
        j = c.get("/api/ui/instruments/optimpp-1")
        assert j.status_code == 200 and j.headers["Cache-Control"] == "no-store"
        assert gw.calls == [], gw.calls
        assert j.get_json()["readiness"]["word"] == "Not OK to run"

    def test_the_page_carries_its_answer(self, tmp_path):
        app, _ = _seeded(tmp_path)
        html = app.test_client().get("/instruments/optimpp-1").get_data(as_text=True)
        assert "<title>OptiMPP 1 · LEM</title>" in html
        assert 'id="record-data"' in html
        assert 'href="/instruments">Instruments</a>' in html or 'href="/">Instruments</a>' in html
        assert 'id="sec-qc"' in html or 'id="qc"' in html

    def test_the_home_now_links_to_the_record(self, tmp_path):
        app, _ = _seeded(tmp_path)
        body = app.test_client().get("/api/ui/instruments").get_json()
        assert all(r["href"].startswith("/instruments/") for r in body["instruments"])

    def test_no_such_instrument_is_404(self, tmp_path):
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        r = c.get("/instruments/no-such-thing")
        assert r.status_code == 404
        html = r.get_data(as_text=True)
        assert "No instrument" in html and "no-such-thing" in html
        assert c.get("/api/ui/instruments/no-such-thing").status_code == 404

    def test_could_not_ask_is_503_and_says_so(self, tmp_path):
        gw = CountingGateway()
        app = _app(gw, tmp_path)
        app.config["SNAPSHOTS"]._last_error = "LabCoreUnavailable('queue 104 deep')"
        c = app.test_client()
        r = c.get("/instruments/optimpp-1")
        assert r.status_code == 503
        html = r.get_data(as_text=True)
        assert "No instrument" not in html
        assert "could not" in html.lower() or "couldn't" in html.lower()
        assert "104 deep" in html
        j = c.get("/api/ui/instruments/optimpp-1")
        assert j.status_code == 503 and j.get_json()["state"] == "unreadable"

    def test_not_read_yet_is_503_not_404(self, tmp_path):
        gw = CountingGateway()
        app = _app(gw, tmp_path)
        c = app.test_client()
        gw.calls.clear()
        r = c.get("/instruments/optimpp-1")
        assert r.status_code == 503
        assert gw.calls == []
        assert "not read" in r.get_data(as_text=True).lower()

    def test_trend_range_trims_before_analysing(self, tmp_path):
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        full = c.get("/api/machines/optimpp-1/qc-trend").get_json()["series"]
        cut = c.get("/api/machines/optimpp-1/qc-trend?range=24").get_json()["series"]
        n = {s["test_name"]: len(s["points"]) for s in full}
        assert all(v > 24 for v in n.values()), n
        assert all(len(s["points"]) == 24 for s in cut)
        assert all(s["runs"] == 24 for s in cut)
        every = c.get("/api/machines/optimpp-1/qc-trend?range=all").get_json()["series"]
        assert {s["test_name"]: len(s["points"]) for s in every} == n
        assert c.get("/api/machines/optimpp-1/qc-trend?range=lots").status_code == 400

    def test_show_on_the_floor_map_lands_on_the_instrument(self, tmp_path):
        app, _ = _seeded(tmp_path)
        r = app.test_client().get("/?view=map&focus=optimpp-1")
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/floor?machine=optimpp-1")

    def test_the_floor_opens_the_machine_it_was_sent(self):
        """Until the map moves into the shell, the floor's own record panel is
        where corrections, documents and actions live: the record's ghost
        button has to land on THIS instrument, not on the whole floor."""
        floor = (T / "floor.html").read_text()
        assert "get('machine')" in floor


class TestNoRedAnywhere:
    def test_record_css_spends_no_red_on_fills_or_borders(self):
        """§0.1: colour only in glyph + word. No red background, no red
        border on the record's own rules."""
        css = (ROOT / "static" / "css" / "lem.css").read_text()
        block = css[css.index("/* ── the record"):]
        block = block[:block.index("/* ── end of the record")]
        for line in block.splitlines():
            # the one exception is a glyph: GC draws its triangle as a red
            # background clipped to a triangle (shell.css .glyph.error), a
            # shape 11px wide, not a fill behind anything
            if "clip-path: polygon(50% 0, 100% 100%, 0 100%)" in line:
                continue
            if re.search(r"(background|border)[a-z-]*\s*:[^;]*(--st-error|--bad|--pill-error)", line):
                pytest.fail("red fill/border in the record's CSS: " + line.strip())


class TestEveryStateIsAShellPage:
    """The record, "no such instrument" and "could not ask" are all shell
    pages: a way home (the sidebar and the crumbs) and the version stamp that
    /healthz reports, bottom-right (§2). A 404 or 503 that dropped the shell
    would be a dead end."""

    @pytest.mark.parametrize("path,code", [("/instruments/optimpp-1", 200),
                                           ("/instruments/no-such-thing", 404)])
    def test_seeded(self, tmp_path, path, code):
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        r = c.get(path)
        assert r.status_code == code
        html = r.get_data(as_text=True)
        version = c.get("/healthz").get_json()["version"]
        assert re.search(r'id="app-version"[^>]*>\s*%s\s*<' % re.escape(version), html)
        assert 'class="sb' in html and 'href="/">Instruments</a>' in html

    def test_could_not_ask(self, tmp_path):
        app = _app(CountingGateway(), tmp_path)
        app.config["SNAPSHOTS"]._last_error = "LabCoreUnavailable('down')"
        html = app.test_client().get("/instruments/optimpp-1").get_data(as_text=True)
        assert 'id="app-version"' in html and 'href="/">Instruments</a>' in html
        # it comes back by itself: the page listens to the live feed
        assert "record_wait.js" in html
