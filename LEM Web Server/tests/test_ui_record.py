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
  line…" when somebody took it off line, "Mark the calibration done…" when
  an overdue task is the warning, nothing when the step is not LEM's.
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
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import demo_floor
import ui_instruments
import ui_live
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


# The moment the capture was taken (its newest heartbeat is 13:17:49). QC is
# judged AT a moment: a pass counts for 24 h (§3.1), so a test that judged
# 1 Oct's capture by today's wall clock would find a different lab every day.
AT = datetime(2026, 10, 1, 13, 18)


def prod(title, at=AT, windows=None):
    m = next(m for m in PROD["machines"] if m["title"] == title)
    return ui_live.judged([m], at, windows)[0]


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
        # a warning, so no stop; but its next step is a button (round 6)
        assert r["primary"]["act"] == "done"

    def test_pm_overdue_is_a_warning_too(self):
        m = dict(prod("PAC Flash 2"), maintenance=[
            {"uid": "p", "kind": "pm", "status": "RED", "name": "Monthly PM",
             "next_due": "2026-09-20", "interval_days": 30, "last_done": "2026-08-21"}])
        r = record(m)["readiness"]
        assert r["state"] == "ok_but" and r["primary"]["label"] == "Mark the PM done…"

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


    def test_the_whole_page_has_exactly_one_primary(self, tmp_path):
        """§0.1, counted over the page as served, shell included. The closed
        sign-in sheet's submit was a second .btn-primary on every record
        (round 3's shooter: "2 in the DOM"). A dialog's own go button is a
        sheet's (`.sheet-go`, the same ink fill), so the page's one primary
        is the card's, shown when there is a next step and hidden when not."""
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        for r in c.get("/api/ui/instruments").get_json()["instruments"]:
            html = c.get("/instruments/" + r["uid"]).get_data(as_text=True)
            assert len(re.findall(r"\bbtn-primary\b", html)) == 1, r["uid"]
            assert re.search(r'class="btn btn-primary" id="ready-primary"', html)


class TestAWarningsNextStepIsTheButton:
    """Round 6's critic: on "OK to run, but…" (GC-2 and six others in the
    dev seed) the card said "Next: calibrate it, then mark the calibration
    done" and offered no button for it. The only way to act was the small
    "See the schedule" link on the Maintenance tile, which led to a table
    that sent you to another page. The sentence named a step LEM can take
    and the page would not take it.

    The physical work (calibrating) happens at the instrument, but marking
    it done is LEM's: one POST to /api/maintenance/<task>/complete, with a
    note. So an overdue calibration or PM gets the card's one primary,
    "Mark the calibration done…" / "Mark the PM done…", opening a sheet
    against the task the sentence is about (the most overdue of that kind).
    It is still a warning: the verdict, glyph and word do not change.

    QC due keeps no button. Its next step is "Run CP and PP" on the
    instrument; the bench reads the result in and nobody types QC here
    (the QC section's own sentence). A button that pretended otherwise
    would be the dead end the critic was complaining about."""

    CAL = {"uid": "cal-1", "kind": "calibration", "status": "RED", "name": "Annual calibration",
           "next_due": "2026-07-11", "interval_days": 365, "last_done": "2025-07-11"}
    PM = {"uid": "pm-1", "kind": "pm", "status": "RED", "name": "Monthly PM",
          "next_due": "2026-09-20", "interval_days": 30, "last_done": "2026-08-21"}

    def test_calibration_overdue_offers_mark_the_calibration_done(self):
        r = record(dict(prod("PAC Flash 2"), maintenance=[self.CAL]))["readiness"]
        assert r["state"] == "ok_but"
        assert r["primary"] == {
            "label": "Mark the calibration done…", "act": "done", "kind": "calibration",
            "task": "cal-1",
            "tasks": [{"uid": "cal-1", "name": "Annual calibration", "next_due": "2026-07-11"}]}
        # the sentence and the button say the same step
        assert r["caption"]["next"] == "Calibrate it, then mark the calibration done"

    def test_pm_overdue_offers_mark_the_pm_done(self):
        r = record(dict(prod("PAC Flash 2"), maintenance=[self.PM]))["readiness"]
        assert (r["primary"]["label"], r["primary"]["task"]) == ("Mark the PM done…", "pm-1")

    def test_the_button_is_about_the_lead_reason_not_the_other_task(self):
        """Calibration outranks PM in the sentence ("Calibration overdue
        since 11 Jul; PM overdue since 20 Sep too"), so the button is the
        calibration's. One button, the sentence's own step."""
        r = record(dict(prod("PAC Flash 2"), maintenance=[self.PM, self.CAL]))["readiness"]
        assert r["caption"]["lead"].startswith("Calibration overdue")
        assert r["primary"]["kind"] == "calibration"
        assert [t["uid"] for t in r["primary"]["tasks"]] == ["cal-1"]

    def test_the_most_overdue_task_is_the_one_marked(self):
        older = dict(self.CAL, uid="cal-0", name="Detector calibration", next_due="2026-03-02")
        r = record(dict(prod("PAC Flash 2"), maintenance=[self.CAL, older]))["readiness"]
        assert r["primary"]["task"] == "cal-0"
        assert [t["uid"] for t in r["primary"]["tasks"]] == ["cal-0", "cal-1"]

    def test_qc_due_has_no_button_because_the_bench_files_qc(self):
        r = record(prod("OptiMPP 1"))["readiness"]
        assert r["state"] == "ok_but" and r["primary"] is None

    def test_a_qc_stop_keeps_its_corrective_action(self):
        """Not OK outranks the warning: one button, and it is the stop's."""
        r = record(dict(prod("Agilent GC 1"), maintenance=[self.CAL]))["readiness"]
        assert r["primary"]["act"] == "action"

    def test_cant_tell_keeps_no_button_even_with_a_task_overdue(self):
        m = dict(prod("Viscocity"), maintenance=[self.CAL])
        assert record(m)["readiness"]["primary"] is None

    def test_off_line_keeps_put_back_on_line(self):
        rec = record(dict(prod("PAC Flash 2"), maintenance=[self.CAL]), override="SERVICE")
        assert rec["readiness"]["primary"]["act"] == "online"

    def test_every_ok_but_record_in_the_dev_seed_but_qc_due_has_its_button(self, tmp_path):
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        seen = 0
        for row in c.get("/api/ui/instruments").get_json()["instruments"]:
            if row["readiness"]["state"] != "ok_but":
                continue
            rec = c.get("/api/ui/instruments/" + row["uid"]).get_json()
            p = rec["readiness"]["primary"]
            if rec["readiness"]["caption"]["lead"].startswith(("QC due", "No verdict yet")):
                assert p is None, row["uid"]
                continue
            seen += 1
            assert p and p["act"] == "done" and p["task"], row["uid"]
            # the task it marks is one the record's own Maintenance section lists
            assert p["task"] in [t["uid"] for t in rec["maintenance"]], row["uid"]
        # GC-2, Anton Paar 1, Cetane calc, GC-1, OptiMPP 2, PAC Flash 1
        assert seen == 6

    def test_the_sheet_requires_a_note_and_posts_the_complete_route(self):
        html = (T / "instrument.html").read_text()
        sheet = re.search(r'<dialog[^>]*id="done-sheet".*?</dialog>', html, re.S).group(0)
        assert re.search(r'<textarea id="done-note"[^>]*required', sheet)
        assert 'id="done-when"' in sheet and 'type="date"' in sheet
        # the sheet's go button is a sheet's, never a second .btn-primary
        assert "btn-primary" not in sheet and "sheet-go" in sheet
        js = (JS / "record.js").read_text()
        assert "'/api/maintenance/' + encodeURIComponent(" in js
        # a completion that moved the schedule but missed the history is said
        assert "logged === false" in js


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

    def test_units_are_written_as_the_lab_writes_them(self):
        """LabCore stores Viscocity's units as ASCII "mm2/s"; a record that
        prints them so reads like a typo (round 3's critic)."""
        assert record(prod("Viscocity"))["qc"]["checks"][0]["units"] == "mm²/s"

    def test_a_refresh_failure_is_not_swallowed(self):
        """Round 3's critic: `.catch(() => {})` on the record's own refresh
        kept a stale verdict on screen through six 503s with no mark. The
        browser walk is tests/test_ui_record_browser.py; this guards the
        source so the swallow cannot come back unnoticed."""
        js = (JS / "record.js").read_text()
        assert ".catch(() => {})" not in js
        assert 'id="ready-stale"' in (T / "instrument.html").read_text()

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


class TestAPassCountsFor24Hours:
    """§3.1's QC sentence: "A passing check counts for 24 h", and §4.1's QC
    due for a pass that has aged out of it. Round 3's critic found OptiMPP 1
    and 2 reading "OK to run · All 2 checks in spec (4 Aug)" in production off
    passes two months old. A pass says the instrument read true THEN; the
    record's one answer is about NOW, so a pass past its window is QC due
    (OK to run, but…), never In spec.

    The window is the lab's own rule (qc_samples.resolve_qc_window): a
    standard that states its own life wins, else 24 h. The boundary is the
    bench's (data_source.qc_is_stale: age >= window is stale), so the record
    and the bench cannot disagree about the same pass at the same second."""

    def test_optimpp_august_passes_are_qc_due_not_in_spec(self):
        rec = record(prod("OptiMPP 1"))
        r = rec["readiness"]
        assert r["state"] == "ok_but" and r["word"] == "OK to run, but…"
        for c in rec["qc"]["checks"]:
            assert c["verdict"]["key"] == "due", c
            assert c["verdict"]["word"] == "QC due"
            assert c["verdict"]["glyph"] == "half"
            # the row says why: when it last passed, and how long a pass counts
            assert c["verdict"]["detail"] == "last passed 3 Aug · a pass counts for 24 h"
        assert r["caption"]["lead"] == "QC due on Cloud Point, mini method and Pour Point, mini method"
        assert r["caption"]["next"] == "Run CP and PP"
        assert r["primary"] is None          # a warning: Ryan, 2026-10-01
        qc_tile = next(t for t in r["tiles"] if t["key"] == "qc")
        assert qc_tile["word"] == "QC due" and qc_tile["current"] is True

    def test_production_on_1_oct_had_three_instruments_due(self):
        """The whole capture, judged at the moment it was taken: OptiMPP 1
        and 2 (3 Aug) and Aquamax 3 (30 Sep 11:23, 26 h before) are QC due.
        Multitek S passed at 14:00 the day before, 23 h 18 min earlier: it
        still counts."""
        due = sorted(m["title"] for m in PROD["machines"]
                     if ui_live.readiness(prod(m["title"]))["reason"].startswith("QC due"))
        assert due == ["Aquamax 3", "OptiMPP 1", "OptiMPP 2"]
        assert record(prod("Multitek S"))["readiness"]["word"] == "OK to run"
        assert record(prod("PAC Flash 2"))["readiness"]["word"] == "OK to run"

    def test_the_boundary_is_the_benches(self):
        """Multitek S passed at 2026-09-30T14:00:24.759917."""
        before = prod("Multitek S", at=datetime(2026, 10, 1, 14, 0, 24))
        after = prod("Multitek S", at=datetime(2026, 10, 1, 14, 0, 25))
        assert ui_live.readiness(before)["state"] == "ok"
        assert ui_live.readiness(after)["state"] == "ok_but"

    def test_a_standards_own_window_decides(self):
        """PAC Flash 2 passed at 08:38; a standard good for 4 h has aged out
        by 13:18, and the record says which window it used and where from."""
        uid = prod("PAC Flash 2")["machine_uid"]
        rec = record(prod("PAC Flash 2", windows={uid: (4.0, "AF26 · Flash Point")}))
        assert rec["readiness"]["state"] == "ok_but"
        assert rec["qc"]["checks"][0]["verdict"]["detail"] == "last passed 1 Oct · a pass counts for 4 h"
        assert rec["qc"]["window"] == {"hours": 4.0, "from": "AF26 · Flash Point"}
        assert record(prod("PAC Flash 2"))["qc"]["window"] == {"hours": 24.0, "from": ""}

    def test_a_stopped_bench_still_reads_no_verdict_yet(self):
        """§4.1: assigned, bench stopped -> No verdict yet. The 3 Sep pass is
        out of its window too, but the reason nothing new is judged is the
        bench, and the row says that, as it did."""
        c = record(prod("Viscocity"))["qc"]["checks"][0]
        assert c["verdict"]["word"] == "No verdict yet"
        assert c["verdict"]["detail"] == "bench stopped"

    def test_a_failed_check_stays_out_of_spec_however_old(self):
        """A stop stands until somebody reruns the standard: age never turns
        Out of spec into QC due."""
        later = prod("Agilent GC 1", at=datetime(2026, 12, 1))
        assert ui_live.readiness(later)["state"] == "not_ok"

    def test_a_machine_nobody_stamped_is_judged_now(self):
        """Fail safe: a caller that forgot to say WHEN gets the wall clock,
        so a forgotten stamp can only make a pass due, never keep an old one
        in spec. The raw August capture is due today."""
        raw = next(m for m in PROD["machines"] if m["title"] == "OptiMPP 1")
        assert ui_live.readiness(raw)["state"] == "ok_but"

    def test_a_pass_whose_time_cannot_be_read_is_not_in_spec(self):
        """A failed read is never an answer: a pass with no readable time
        cannot be said to be inside any window."""
        m = prod("PAC Flash 2")
        s = dict(m["effective_specs"][0], last_qc_at="not a time")
        c = record(dict(m, effective_specs=[s]))["qc"]["checks"][0]
        assert c["verdict"]["key"] == "due"
        assert c["verdict"]["detail"] == "when it passed is not on record · a pass counts for 24 h"

    def test_the_intro_sentence_says_the_window(self):
        """§3.1's QC intro has three sentences; round 3 dropped the middle one."""
        page = (T / "instrument.html").read_text()
        assert "A passing check counts for" in page
        js = (JS / "record.js").read_text()
        assert "R.windowSentence(" in js


class TestTheRouteJudgesQcNow:
    def _shift(self, monkeypatch, hours):
        import web_app
        real = web_app._now()
        monkeypatch.setattr(web_app, "_now", lambda: real + timedelta(hours=hours))

    def test_a_pass_ages_out_without_any_new_data(self, tmp_path, monkeypatch):
        """Nothing in LabCore changes when a pass turns 24 h old; the answer
        must change anyway. The home's memo was keyed on the data alone, so
        it would have served the morning's "OK to run" all night."""
        app, gw = _seeded(tmp_path)
        c = app.test_client()
        first = c.get("/api/ui/instruments").get_json()
        ok_now = {r["uid"] for r in first["instruments"] if r["readiness"]["state"] == "ok"}
        assert ok_now, "the dev seed has instruments that are OK to run"
        self._shift(monkeypatch, 48)
        later = c.get("/api/ui/instruments").get_json()
        states = {r["uid"]: r["readiness"]["state"] for r in later["instruments"]}
        assert all(states[u] == "ok_but" for u in ok_now), states
        rec = c.get("/api/ui/instruments/" + sorted(ok_now)[0]).get_json()
        assert rec["readiness"]["state"] == "ok_but"
        assert rec["qc"]["window"]["hours"] == 24.0
        assert gw.calls == [] or all(k != "write" for k, _ in gw.calls)

    def test_the_live_count_ages_with_it(self, tmp_path, monkeypatch):
        """The nav's Needs-you count and the bell are the same rule (§0.2)."""
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        before = c.get("/api/ui/live").get_json()["needs_you"]
        self._shift(monkeypatch, 48)
        after = c.get("/api/ui/live").get_json()["needs_you"]
        assert after > before


class TestShortNames:
    def test_a_method_qualifier_is_part_of_the_name(self):
        """"Pour Point, mini method" is not a distillation point: cutting at
        the comma named both OptiMPP checks "mini method", so a caption read
        "QC due on mini method and mini method"."""
        assert ui_record.short_test("ASTM D7346 - Pour Point, mini method") == \
            ("Pour Point, mini method", "ASTM D7346")
        assert ui_record.short_test("ASTM D2887/D86 - Distillation in Petroleum Products, 10% Recovery") == \
            ("10% Recovery", "ASTM D2887/D86")
        assert ui_record.short_test("ASTM D2887/D86 - Distillation in Petroleum Products, FBP") == \
            ("FBP", "ASTM D2887/D86")


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

    def test_last_result_is_never_older_than_a_qc_row_on_the_same_page(self):
        """Round 4's critic: the head said "Last result Thu 07:49" while the
        QC row under it showed a run at 07:30 today. `last_activity` is the
        bench's last parse; a QC run the bench reported in its status is a
        result too. The head says the newest of them, so the page never calls
        an older time the last one."""
        m = dict(prod("Agilent GC 1"))
        m["last_activity"] = "2026-09-30T07:49:00"
        m["effective_specs"] = [dict(s, last_qc_at="2026-10-01T07:30:00", last_qc_superseded_by=None)
                                for s in m["effective_specs"]]
        assert m["effective_specs"], "the capture's GC 1 has checks to stamp"
        assert record(m)["head"]["last_result_at"] == "2026-10-01T07:30:00"

    def test_last_result_keeps_the_newer_activity(self):
        m = dict(prod("Agilent GC 1"))
        m["last_activity"] = "2026-10-01T09:00:00"
        m["effective_specs"] = [dict(s, last_qc_at="2026-10-01T07:30:00", last_qc_superseded_by=None)
                                for s in m["effective_specs"]]
        assert record(m)["head"]["last_result_at"] == "2026-10-01T09:00:00"

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


class TestRound7Wording:
    """Round 6's critic, two sentences that made a reader stop."""

    @staticmethod
    def _in(name, at="2026-10-02T12:02:00"):
        return {"test": name, "title": name, "at": at, "verdict": {"key": "in"},
                "sample_id": "STD-1"}

    def test_two_checks_in_spec_read_both_not_all_2(self):
        """"All 2 checks in spec" is a count dressed as a quantifier; people
        say "both". Three or more stays "All 3 checks in spec"."""
        two = ui_record.caption({}, "ok", "", [self._in("Nitrogen"), self._in("Sulfur")], [])
        assert two["lead"] == "Both checks in spec"
        three = ui_record.caption({}, "ok", "", [self._in("A"), self._in("B"), self._in("C")], [])
        assert three["lead"] == "All 3 checks in spec"

    def test_a_result_with_no_bench_says_it_came_from_labcore(self):
        """Multitek S read "Bench never checked in · Last result today 11:18":
        two true facts that look like a contradiction, explained only by the
        chart caption further down. When the bench is not checking in and the
        newest time is a QC run (which LEM reads from LabCore), the head says
        where that result is: "Last result in LabCore". When the bench is
        checking in, or the newest time is the bench's own parse, it is the
        plain "Last result"."""
        rows = [self._in("Sulfur", "2026-10-02T11:18:00")]
        never = {"last_activity": None}
        assert ui_record.last_result_label(never, rows) == "Last result in LabCore"
        live = {"last_activity": None, "live": True, "last_poll": "2026-10-02T12:00:00"}
        assert ui_record.last_result_label(live, rows, checking_in=True) == "Last result"
        parsed = {"last_activity": "2026-10-02T11:40:00"}
        assert ui_record.last_result_label(parsed, rows) == "Last result"

    def test_the_head_carries_the_label(self, tmp_path):
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        heads = {}
        for uid in ("multitek-s", "gc-2"):
            j = c.get("/api/ui/instruments/%s" % uid).get_json()
            heads[uid] = (j["head"]["bench"]["word"], j["head"]["last_result_label"])
        assert heads["multitek-s"] == ("Never checked in", "Last result in LabCore"), heads
        assert heads["gc-2"] == ("Checking in", "Last result"), heads


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

    def test_the_record_carries_data_transfer_and_still_costs_labcore_nothing(self, tmp_path):
        """T-P12's Data transfer section belongs on the record (transfer §14),
        and the record's promise of 0 LabCore ops must survive it. Where LEM's
        store and LabCore are one gateway (this suite's shape), reading the
        transfer state at render time WOULD be a LabCore op, so the first
        paint is the section's frame saying it is reading, and the browser
        asks /api/ui/transfer for the rows. A blank section would read as
        "nothing to report", which is not what the page knows."""
        app, gw = _seeded(tmp_path)
        c = app.test_client()
        gw.calls.clear()
        html = c.get("/instruments/gc-2").get_data(as_text=True)
        assert gw.calls == [], gw.calls
        assert 'id="transfer"' in html and "Data transfer" in html
        assert "Reading how this bench&#39;s readings travel" in html or \
            "Reading how this bench's readings travel" in html
        assert "transfer_section.js" in html
        # it sits after Bench and results and adds no button to compete
        # with the card's one primary
        assert html.index('id="bench"') < html.index('id="transfer"')
        assert html.count("btn-primary") == 1

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
        # The map is now the Instruments page drawn as a plan (piece 12);
        # floor_map.js reads ?focus= and rings that bay. The link must land
        # on that page, not bounce to the whole floor.
        r = app.test_client().get("/?view=map&focus=optimpp-1")
        assert r.status_code == 200
        assert "floor_map.js" in r.get_data(as_text=True)

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

    def test_the_bell_badge_is_ink_not_a_red_pill(self):
        """The bar for this page is "no red fill anywhere", and round 5's
        shooter found exactly one on every record: the shell's bell count, a
        red pill (GC hub's own style, ported as it was). The count is a
        number to read, not a verdict, and the rail badge already says it in
        ink (test_ui_contrast's LEM_PAIRS: --ink-fg on --ink). The bell says
        it the same way, so red stays a word and a glyph."""
        css = (ROOT / "static" / "css" / "shell.css").read_text()
        rule = css[css.index(".bell-count {"):]
        rule = rule[:rule.index("}")]
        assert "--st-error" not in rule, rule
        assert "background: var(--ink)" in rule and "color: var(--ink-fg)" in rule, rule


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
        if state == "ok_but" and cap["lead"].startswith(("QC due", "No verdict yet")):
            due = [c for c in rows if c["verdict"]["key"] == "due"]
            assert due, name
            for c in due:
                assert c["title"] in cap["lead"], (name, cap["lead"])
                # each owed check is named after its own row's word (round 9)
                w = c["verdict"]["word"]
                assert re.search(r"(?i)%s on [^;]*%s" % (re.escape(w), re.escape(c["title"])), cap["lead"]), \
                    (name, w, cap["lead"])
        if "due" in keys and state != "off_line":
            assert "QC due on " + rows[keys.index("due")]["title"] in (cap["lead"] + " " + cap["too"]) \
                or rows[keys.index("due")]["title"] in cap["lead"] + cap["too"], (name, cap)
        # the tile says the worst row's word, never a different one
        if rows:
            worst = next(k for k in ("out", "due", "none", "in") if k in keys)
            words = [c["verdict"]["word"] for c in rows if c["verdict"]["key"] == worst]
            # of the worst rows, "QC due" (a run is owed and one ran
            # before) outranks "No verdict yet" (never run): both make the
            # card say "QC due on …", and the tile says the stronger
            want = "QC due" if "QC due" in words else words[0]
            assert tile["word"] == want, (name, tile, words)
            # a row's word and glyph are one §4.1 pair, never mixed
            for c in rows:
                v = c["verdict"]
                assert {"In spec": "final", "QC due": "half", "Out of spec": "error",
                        "No verdict yet": "never"}[v["word"]] == v["glyph"], (name, v)
        else:
            assert tile["word"] == "No QC assigned", name

    def test_koehler_never_run_on_a_bench_that_checks_in_is_no_verdict_yet(self):
        """Dev seed Koehler K23000: an assigned check that has never run, on a
        bench that checks in. Round 2 found the card reading "QC due on ."
        over the row; round 8's critic found the row reading "QC due · never
        run" while the Instruments list's Last QC column said "No verdict yet"
        for the same instrument. §4.1 defines "No verdict yet" as exactly
        this case (assigned but never run), so the row and the QC tile say
        it, in §4.1's hollow ring, with the reason "never run".

        The instrument's verdict is a different level of §4.1 and is
        unchanged: a check is owed, so the card says "OK to run, but… No
        verdict yet on <check>. Next: run AF26", and the QC tile is the current tile,
        because it is the one that explains the "but". The row keeps key
        "due" so everything that counts what makes the card say "QC due"
        (the caption, the home's row) still counts it."""
        m = prod("PAC Flash 2")
        s = dict(m["effective_specs"][0], last_qc_in_spec=None, last_qc_at=None, last_qc_value=None)
        rec = record(dict(m, effective_specs=[s]))
        assert rec["readiness"]["word"] == "OK to run, but…"
        (c,) = rec["qc"]["checks"]
        assert c["verdict"] == {"key": "due", "word": "No verdict yet", "glyph": "never",
                                "detail": "never run"}
        # and the card's sentence says the row's word (round 9's critic)
        assert rec["readiness"]["caption"]["lead"] == "No verdict yet on Flash Point Closed cup (small scale)"
        assert rec["readiness"]["caption"]["next"] == "Run AF26"
        tile = _qc_tile(rec)
        assert (tile["word"], tile["detail"], tile["glyph"]) == ("No verdict yet", "Never run", "never")
        assert tile["current"] is True
        assert rec["qc"]["selected"] == c["test"]

    def test_a_result_against_an_old_standard_is_no_verdict_yet_too(self):
        """The standard changed and nothing has run against the new one: by
        §4.1 that is assigned and never run (against this standard). The
        Instruments list's Last QC column drops a superseded result and says
        "No verdict yet", so the row says it too."""
        m = prod("PAC Flash 2")
        s = dict(m["effective_specs"][0], last_qc_superseded_by="OLD-STD")
        rec = record(dict(m, effective_specs=[s]))
        (c,) = rec["qc"]["checks"]
        assert (c["verdict"]["key"], c["verdict"]["word"], c["verdict"]["glyph"]) == ("due", "No verdict yet", "never")
        assert c["verdict"]["detail"].startswith("not yet run against ")

    def test_a_check_that_ran_but_has_no_verdict_in_the_window_is_still_qc_due(self):
        """Not every "due" row is a never-run one. A check that ran, and whose
        pass is older than its window, is QC due: the list shows its date in
        Last QC, not a word, so there is nothing for the two pages to
        disagree on, and "QC due · last passed …" says what to do."""
        rec = record(prod("PAC Flash 2", at=AT + timedelta(days=8)))
        words = {(c["verdict"]["key"], c["verdict"]["word"]) for c in rec["qc"]["checks"]}
        assert ("due", "QC due") in words, words

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

    def test_a_warning_behind_a_stopped_bench_is_said_too(self):
        """Calibration overdue AND the bench stopped. Until round 5 the card
        said "OK to run, but… Calibration overdue" with the stopped bench as
        a "too"; the critic found that on Multitek S (never checked in) and
        called it the record's weakest honesty point: the page told an
        analyst the instrument may run while nothing was vouching for it.
        Now the verdict is Can't tell, and the overdue calibration, which is
        still true and still needs doing, is in the same sentence."""
        m = dict(prod("Viscocity"), maintenance=[
            {"uid": "t1", "name": "Calibration", "kind": "calibration", "status": "RED",
             "next_due": "2026-09-01", "last_done": "2025-09-01", "interval_days": 365}])
        rec = record(m)
        assert rec["readiness"]["word"] == "Can't tell"
        cap = rec["readiness"]["caption"]
        assert cap["lead"] == "Its bench stopped checking in, so nothing new is judged"
        assert cap["too"] == "calibration overdue since 1 Sep too"
        assert rec["readiness"]["primary"] is None, "Can't tell has no primary button (§3.1)"
        assert [c["verdict"]["word"] for c in rec["qc"]["checks"]] == ["No verdict yet"]

    def test_a_check_never_run_on_a_silent_bench_is_no_verdict_yet(self):
        """A check with no result on a bench that is not checking in used to
        read "QC due · bench stopped" under a card that, from round 5, says
        Can't tell: "QC due" asks for a run that nothing would pick up. The
        row says what the card says."""
        m = prod("Viscocity")
        s = dict(m["effective_specs"][0], last_qc_in_spec=None, last_qc_at=None, last_qc_value=None)
        rec = record(dict(m, effective_specs=[s]))
        assert rec["readiness"]["word"] == "Can't tell"
        (c,) = rec["qc"]["checks"]
        assert (c["verdict"]["word"], c["verdict"]["detail"]) == ("No verdict yet", "bench stopped")

    def test_the_home_row_names_the_same_due_checks(self):
        """ui_instruments says the home's row from the same rule, so the row
        and the record name the same checks."""
        m = dict(prod("Agilent GC 2"), qc_targets=[{"sample": "STD-9", "test": "ASTM D1 - Thing"}])
        row = ui_instruments.instrument(m, "", LEVELS, href)
        assert row["readiness"]["word"] == "OK to run, but…"
        assert "ASTM D1 - Thing" in json.dumps(row)
        rec = record(m)
        # assigned, never run: owed (the card's "QC due on …"), and in §4.1's
        # words for the check itself, No verdict yet · never run
        v = rec["qc"]["checks"][0]["verdict"]
        assert (v["key"], v["word"], v["detail"]) == ("due", "No verdict yet", "never run")
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
            if cap["lead"].startswith(("QC due", "No verdict yet")):
                assert "due" in keys, (r["title"], keys)
            if rec["readiness"]["state"] == "cant_tell":
                assert "in" not in keys, r["title"]


class TestOneWordForNeverRunOnEveryPage:
    def test_the_tile_wears_the_rows_ring(self):
        """§4.1 gives No verdict yet a hollow ring and Can't tell a dashed
        one. The row drew the hollow ring and the QC tile above it the dashed
        one, so the same words wore two shapes a few centimetres apart. The
        tile now wears "never", which the page draws as a solid hollow ring."""
        for rec in (record(prod("Eravap")), record(prod("Viscocity"))):
            tile = _qc_tile(rec)
            assert (tile["word"], tile["glyph"]) == ("No verdict yet", "never"), tile
        js = (ROOT / "static" / "js" / "record.js").read_text()
        assert re.search(r"kind === 'never'[^\n]*\n[^\n]*class: 'ring' \}", js), \
            "circle('never') must draw a solid (not dashed) ring"

    def test_the_record_and_the_instruments_list_agree_on_the_dev_seed(self, tmp_path):
        """Round 8's critic: for the dev seed's Koehler K23000 the record's
        Verdict column and QC tile said "QC due · never run" while the
        Instruments list's Last QC column said "No verdict yet". One fact,
        two words, on two pages (§0.2 say it once; §4.1 one vocabulary).
        Walk every seeded instrument: wherever the list says "No verdict
        yet", every row on the record that has no result says it too, and
        so does the QC tile."""
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        rows = c.get("/api/ui/instruments").get_json()["instruments"]
        seen = 0
        for r in rows:
            if (r.get("last_qc") or {}).get("word") != "No verdict yet":
                continue
            seen += 1
            rec = c.get("/api/ui/instruments/%s" % r["uid"]).get_json()
            unrun = [x for x in rec["qc"]["checks"] if not x.get("at")]
            assert unrun, r["title"]
            for x in unrun:
                assert x["verdict"]["word"] == "No verdict yet", (r["title"], x["verdict"])
            assert _qc_tile(rec)["word"] == "No verdict yet", (r["title"], _qc_tile(rec))
        assert seen >= 1, "the seed should exercise this (Koehler K23000)"
        k = next(r for r in rows if r["title"] == "Koehler K23000")
        rec = c.get("/api/ui/instruments/%s" % k["uid"]).get_json()
        assert rec["readiness"]["word"] == "OK to run, but…"
        assert "QC due" not in json.dumps(rec["qc"]), "the rows say §4.1's word, not the card's"


class TestASilentBenchIsCantTell:
    def test_multitek_s_in_the_dev_seed(self, tmp_path):
        """Round 5's critic, on this seed: Multitek S's head and Bench tile
        said "Bench never checked in", and the card said "OK to run, but…"
        because an overdue calibration outranked Can't tell. The page told an
        analyst it may run off a bench nobody has heard from. Now the card
        says Can't tell, the calibration is still said in the same sentence,
        the QC row and tile say No verdict yet, and there is no primary
        button (§3.1: Can't tell -> nothing). The home's row agrees."""
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        rec = c.get("/api/ui/instruments/multitek-s").get_json()
        r = rec["readiness"]
        assert (r["state"], r["word"]) == ("cant_tell", "Can't tell"), r
        assert r["caption"]["lead"] == "Its bench has never checked in, so nothing is judged"
        assert r["caption"]["too"].startswith("calibration overdue since "), r["caption"]
        assert r["caption"]["next"] == "Start the LEM module in LabStation on its computer"
        assert r["primary"] is None
        assert [x["verdict"]["word"] for x in rec["qc"]["checks"]] == ["No verdict yet"]
        assert _qc_tile(rec)["word"] == "No verdict yet"
        row = next(x for x in c.get("/api/ui/instruments").get_json()["instruments"]
                   if x["uid"] == "multitek-s")
        assert row["readiness"]["word"] == "Can't tell"
        assert [p["key"] for p in row["problems"]] == ["cant_tell-never", "ok_but-cal"]


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

    def test_the_topbar_gated_button_says_sign_in_the_same_way(self):
        """Round 8's critic: signed out, the record's two gated buttons said
        "sign in first" two ways: the topbar "Take off line…" dashed and
        muted, the primary solid with a lock. On this page both keep their
        own words and solid edge and both carry the lock; the shell's
        dashed dim stays for every other page."""
        css = (ROOT / "static" / "css" / "lem.css").read_text()
        tpl = (ROOT / "templates" / "instrument.html").read_text()
        btn = re.search(r'<button[^>]*id="topbar-online"[^>]*>', tpl).group(0)
        assert "gate-lock" in btn and "data-gated=" in btn
        m = re.search(r"body\.anon \.btn\.gate-lock\[data-gated\][^{]*\{([^}]*)\}", css)
        assert m, "no signed-out rule for the topbar's gated button"
        assert "color: var(--text)" in m.group(1) and "border-style: solid" in m.group(1)
        lock = re.search(r"([^{}]*)\{[^}]*-webkit-mask: url", css).group(1)
        assert "body.anon .btn.gate-lock[data-gated]::before" in lock
        assert "body.anon .btn-primary[data-gated]::before" in lock


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
