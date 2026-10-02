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

    def test_no_tile_says_its_title_twice(self):
        """Round 2's critic: the On line tile read "On line / On line", and the
        Bench tile's link read "Bench" under a tile titled Bench. A tile's word
        is an answer to its title, and its link says where it goes."""
        for ov in ("", "SERVICE"):
            m = dict(prod("Agilent GC 1"), maintenance=[
                {"uid": "p", "kind": "pm", "status": "GREEN", "name": "Monthly PM",
                 "next_due": "2026-10-20", "interval_days": 30, "last_done": "2026-09-20"}])
            for t in record(m, override=ov)["readiness"]["tiles"]:
                assert t["word"].lower() != t["title"].lower(), t
                if t["action"]:
                    assert t["action"]["label"].lower() != t["title"].lower(), t
                    assert t["action"]["label"].startswith("See the "), t
        on = next(t for t in record(prod("PAC Flash 2"))["readiness"]["tiles"] if t["key"] == "online")
        assert (on["word"], on["detail"]) == ("In service", "Nobody has taken it off line")



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


# ── one rule for the card, the tile and every row ───────────────────────────

def _variants():
    """Every production instrument, as captured and in the bench states the
    capture happens not to contain: stopped, never checked in, lab closed,
    and with each check flipped to "no verdict in the window". Round 2's
    critic found two of these by hand (Koehler's never-run check on a bench
    that checks in, Viscocity's old pass on a stopped bench); a rule checked
    only against the instruments that happen to exist today will break on
    the next one."""
    benches = {
        "running": {"module_running": True, "module_state": "running"},
        "stopped": {"module_running": False, "live": False, "module_state": "stopped"},
        "never": {"module_running": False, "live": False, "module_state": "unknown", "last_poll": None},
        "closed": {"module_running": False, "live": False, "module_state": "closed"},
    }
    for m in PROD["machines"]:
        for bname, b in benches.items():
            yield "%s/%s" % (m["title"], bname), dict(m, **b)
            for i, s in enumerate(m.get("effective_specs") or []):
                for at in (s.get("last_qc_at"), None):
                    specs = list(m["effective_specs"])
                    specs[i] = dict(s, last_qc_in_spec=None, last_qc_at=at,
                                    last_qc_value=None if at is None else s.get("last_qc_value"))
                    yield ("%s/%s/due%d%s" % (m["title"], bname, i, "" if at else "-never"),
                           dict(m, effective_specs=specs, **b))
        # the standard changed: the last result was against the old one
        if m.get("effective_specs"):
            specs = [dict(m["effective_specs"][0], last_qc_superseded_by="OLD-STD")] \
                + list(m["effective_specs"][1:])
            yield "%s/superseded" % m["title"], dict(m, effective_specs=specs)
        # assigned, the bench checking in, and no band published yet
        if not m.get("effective_specs") and not m.get("qc_targets"):
            yield ("%s/target-only" % m["title"],
                   dict(m, qc_targets=[{"sample": "STD-9", "test": "ASTM D1 - Thing"}],
                        module_running=True, module_state="running"))


def _qc_tile(rec):
    return next(t for t in rec["readiness"]["tiles"] if t["key"] == "qc")


class TestEveryCheckIsJudgedByTheCardsRule:
    """The card's verdict, the QC tile and each row of the QC table are one
    judgement (§0.2, §4.1). If the card says "QC due on X", a row says QC due
    on X; if the bench is stopped and the card says Can't tell, no row may
    still say In spec off a result the bench is no longer vouching for."""

    @pytest.mark.parametrize("name,m", list(_variants()), ids=lambda v: v if isinstance(v, str) else "")
    def test_the_card_the_tile_and_the_rows_agree(self, name, m):
        rec = record(m)
        state = rec["readiness"]["state"]
        rows = rec["qc"]["checks"]
        keys = [c["verdict"]["key"] for c in rows]
        cap = rec["readiness"]["caption"]
        tile = _qc_tile(rec)
        # no list in the sentence is ever empty ("QC due on .")
        for part in (cap["lead"], cap["too"]):
            assert not re.search(r"\bon\s*(\.|;|$| too)", part or ""), (name, cap)
        if state == "not_ok":
            assert "out" in keys, name
        else:
            assert "out" not in keys, name
        if state == "ok":
            assert keys and all(k == "in" for k in keys), (name, keys)
        if state == "cant_tell":
            assert all(k == "none" for k in keys), (name, keys)
        if state == "ok_but" and rec["readiness"]["caption"]["lead"].startswith("QC due"):
            due = [c["title"] for c in rows if c["verdict"]["key"] == "due"]
            assert due, name
            for t in due:
                assert t in cap["lead"], (name, cap["lead"])
        if "due" in keys and state != "off_line":
            assert "QC due on " + rows[keys.index("due")]["title"] in (cap["lead"] + " " + cap["too"]) \
                or rows[keys.index("due")]["title"] in cap["lead"] + cap["too"], (name, cap)
        # the tile says the worst row's word, never a different one
        if rows:
            worst = next(k for k in ("out", "due", "none", "in") if k in keys)
            word = next(c["verdict"]["word"] for c in rows if c["verdict"]["key"] == worst)
            assert tile["word"] == word, (name, tile, word)
        else:
            assert tile["word"] == "No QC assigned", name

    def test_koehler_never_run_on_a_bench_that_checks_in_is_qc_due(self):
        """Dev seed Koehler K23000 read "QC due on . Next: run the QC
        standard." over a row that said "No verdict yet · never run"."""
        m = prod("PAC Flash 2")
        s = dict(m["effective_specs"][0], last_qc_in_spec=None, last_qc_at=None, last_qc_value=None)
        rec = record(dict(m, effective_specs=[s]))
        assert rec["readiness"]["word"] == "OK to run, but…"
        (c,) = rec["qc"]["checks"]
        assert c["verdict"]["word"] == "QC due" and c["verdict"]["detail"] == "never run"
        assert rec["readiness"]["caption"]["lead"] == "QC due on Flash Point Closed cup (small scale)"
        assert rec["readiness"]["caption"]["next"] == "Run AF26"
        assert _qc_tile(rec)["word"] == "QC due"
        assert rec["qc"]["selected"] == c["test"]

    def test_viscocity_old_pass_on_a_stopped_bench_is_no_verdict_yet(self):
        """Production Viscocity: bench stopped, card Can't tell, and a pass
        from 3 Sep. The pass stays on the row as history (value and date),
        but the row's word is §4.1's: No verdict yet · bench stopped."""
        rec = record(prod("Viscocity"))
        assert rec["readiness"]["word"] == "Can't tell"
        (c,) = rec["qc"]["checks"]
        assert c["verdict"]["word"] == "No verdict yet"
        assert c["verdict"]["detail"] == "bench stopped"
        assert c["value"] == 2.345 and c["at"].startswith("2026-09-03")
        tile = _qc_tile(rec)
        assert tile["word"] == "No verdict yet" and tile["detail"] == "Bench stopped"

    def test_eravap_tile_gives_the_rows_reason(self):
        """The tile said "Assigned, never run" over a row that said "bench
        stopped": two reasons on one page for one fact."""
        rec = record(prod("Eravap"))
        assert _qc_tile(rec)["detail"] == "Bench stopped"
        assert rec["qc"]["checks"][0]["verdict"]["detail"] == "bench stopped"

    def test_a_stopped_bench_behind_a_warning_is_said_too(self):
        """Calibration overdue AND the bench stopped: the card's verdict is
        the warning, and the stopped bench (which is why the rows have no
        verdict) is in the same sentence."""
        m = dict(prod("Viscocity"), maintenance=[
            {"uid": "t1", "name": "Calibration", "kind": "calibration", "status": "RED",
             "next_due": "2026-09-01", "last_done": "2025-09-01", "interval_days": 365}])
        rec = record(m)
        assert rec["readiness"]["word"] == "OK to run, but…"
        assert "bench stopped checking in too" in rec["readiness"]["caption"]["too"]

    def test_the_home_row_names_the_same_due_checks(self):
        """ui_instruments says the home's row from the same rule, so the row
        and the record name the same checks."""
        m = dict(prod("Agilent GC 2"), qc_targets=[{"sample": "STD-9", "test": "ASTM D1 - Thing"}])
        row = ui_instruments.instrument(m, "", LEVELS, href)
        assert row["readiness"]["word"] == "OK to run, but…"
        assert "ASTM D1 - Thing" in json.dumps(row)
        rec = record(m)
        assert rec["qc"]["checks"][0]["verdict"]["word"] == "QC due"
        assert "Thing" in rec["readiness"]["caption"]["lead"]

    def test_the_dev_seed_agrees_too(self, tmp_path):
        """Koehler K23000 is in the dev seed: walk every seeded record."""
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        rows = c.get("/api/ui/instruments").get_json()["instruments"]
        assert rows
        for r in rows:
            rec = c.get("/api/ui/instruments/%s" % r["uid"]).get_json()
            cap = rec["readiness"]["caption"]
            assert not re.search(r"\bon\s*(\.|;|$)", cap["lead"]), (r["title"], cap)
            keys = [x["verdict"]["key"] for x in rec["qc"]["checks"]]
            if cap["lead"].startswith("QC due"):
                assert "due" in keys, (r["title"], keys)
            if rec["readiness"]["state"] == "cant_tell":
                assert "in" not in keys, r["title"]


class TestTheFloorPanelUsesFmtQC:
    """§4.2: fmtQC "replaces toFixed(2) at floor.html:2131, 3188, 3193–3194",
    and a guard fails on toFixed(2) in any spec path. The record's "Show on
    the floor map" still lands on the floor's panel, and round 2's critic
    found Anton Paar's density band there as "0.80 – 0.80": a band two
    decimals cannot tell apart is a band nobody can check a reading against.
    The one toFixed(2) left is the expanded uncertainty's ± (not a band)."""

    def test_no_spec_number_is_cut_to_two_decimals(self):
        floor = (T / "floor.html").read_text()
        bad = [ln.strip() for ln in floor.splitlines() if "toFixed(2)" in ln and "widest" not in ln]
        assert bad == [], bad

    def test_the_floor_loads_the_one_formatter(self):
        floor = (T / "floor.html").read_text()
        assert "/static/js/record_logic.js" in floor
        assert "window.LEMRecord" in floor and "R.fmtQC(" in floor

    def test_the_panel_says_the_records_verdict_not_only_the_benchs(self):
        """Round 2's critic followed "Show on the floor map" from Anton Paar's
        record (OK to run, but… calibration overdue) to a panel headed "GREEN
        / System nominal". GREEN is what the bench says of itself; the lab's
        verdict is readiness's. The panel now says the record's verdict first,
        read from the same /api/ui/instruments/<uid> the record draws, labels
        the bench's own word as the bench's, and says when it could not read
        the verdict instead of drawing nothing (a failed read is never empty)."""
        floor = (T / "floor.html").read_text()
        assert 'id="panelVerdict"' in floor
        assert "/api/ui/instruments/" in floor
        assert "Bench reports" in floor
        assert "Couldn't read the verdict" in floor


class TestSignedOutPrimaryKeepsItsContrast:
    def test_the_primary_is_not_greyed_signed_out(self):
        """Signed out, a gated control is drawn in the muted text token with a
        dashed edge (P03). On the ink-filled primary that put grey words on
        ink, 3.75:1 in light and 2.15:1 in dark (round 2's critic), under AA's
        4.5:1, and the page's one next step looked disabled though clicking it
        opens sign-in. The primary keeps its own ink-fg words and solid edge;
        it says it needs a sign-in with a lock, a shape, not with grey."""
        css = (ROOT / "static" / "css" / "lem.css").read_text()
        m = re.search(r"body\.anon \.btn-primary\[data-gated\][^{]*\{([^}]*)\}", css)
        assert m, "no signed-out rule for the primary"
        assert "color: var(--ink-fg)" in m.group(1) and "border-style: solid" in m.group(1)
        assert re.search(r"body\.anon \.btn-primary\[data-gated\]::before", css)


def test_favicon_ico_is_not_a_404(tmp_path):
    """Round 2's shooter: the one console error on a record was the browser's
    own /favicon.ico request answered 404. The icon is favicon.svg; the old
    path is sent there rather than logged as an error on every first load."""
    app, gw = _seeded(tmp_path)
    gw.calls.clear()
    r = app.test_client().get("/favicon.ico")
    assert r.status_code in (301, 302, 308)
    assert r.headers["Location"].split("?")[0].endswith("/static/favicon.svg")
    assert gw.calls == []
