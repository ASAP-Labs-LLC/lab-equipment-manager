"""What the wall kiosks say (ia-final §3.8): ui_wall, pure.

A wall is read from across the room by somebody who did not walk up to it.
Nobody is standing there to ask "what does that number mean?", so every
sentence on it has to be one the record supports, and the worst thing it can
do is look fine. These tests hold the words to that:

* **The counts add up to the fleet.** Each instrument is counted once, in the
  state its record page shows (``ui_live.readiness``). A wall whose
  "8 OK · 4 QC due · 1 Not OK · 4 No QC" summed to 17 over a fleet of 17 only
  by luck (C's mock: OK and QC due overlapped) teaches the room that the
  numbers are decoration. Here a missing count is a test failure, for every
  combination of states.
* **One verdict vocabulary.** The words are the app's (§4.1), so "Not OK to
  run" on the wall is the same claim as "Not OK to run" on the record.
* **The headline is a sentence about the lab**, and "All 17 instruments can
  run" is said only when every one of them can (OK, OK-but, or No QC
  assigned: Ryan's rule, only QC or an override makes the answer No). An
  instrument whose bench stopped is "can't tell", which is not "can run".
* **A failed read is never an empty result.** Before the first read the wall
  says it is reading; when the read failed it says so, and why. Neither may
  ever produce "All 0 instruments can run".
* **Needs attention is the worst five, merged.** Two instruments with the
  same cause are one line ("PAC Flash 1 and GC-2 · Calibration overdue"),
  and what does not fit is counted, never silently dropped.
* **/qc speaks the same verdict words**, per check, from the snapshot the
  app reads. The chart history comes from the local record; when it cannot
  be read the card says the history is missing, it does not draw a flat
  line or drop the card.
"""
from __future__ import annotations

import itertools
from datetime import datetime

import pytest

import ui_instruments
import ui_wall

NOW = datetime(2026, 10, 1, 13, 30, 0)


def machine(uid, title=None, *, specs=None, targets=None, maint=None, status="GREEN",
            running=True, module_state="running", level="L1", pos=(0.0, 0.0),
            last_poll="2026-10-01T13:15:00"):
    return {"machine_uid": uid, "title": title or uid, "effective_specs": specs or [],
            "qc_targets": targets or [], "maintenance": maint or [], "status": status,
            "reason": "", "module_running": running, "module_state": module_state,
            "live": False, "level_uid": level, "pos": list(pos) if pos else None,
            "watching": "single_csv C:/r.csv", "last_poll": last_poll}


def spec(test, ok, at="2026-10-01T11:23:00", sample="AF26", value=1.4,
         low=1.0, high=2.0, expected=1.5, units="C"):
    return {"test_name": test, "last_qc_in_spec": ok, "last_qc_at": at,
            "last_qc_superseded_by": "", "sample_id": sample, "low": low, "high": high,
            "expected": expected, "last_qc_value": value, "units": units}


def task(kind, status="RED"):
    return {"uid": "t-" + kind, "kind": kind, "status": status, "name": kind.upper(),
            "next_due": "2026-09-01"}


LEVELS = [{"uid": "L1", "name": "Ground Floor", "rank": 0}]


def href(uid, section):
    return "/instruments/%s%s" % (uid, "#" + section if section else "")


def payload(machines, overrides=None):
    return ui_instruments.build(machines=machines, overrides=overrides or {},
                                levels=LEVELS, href=href)


# one machine per readiness state, so a fleet of any mix can be built
def of_state(state, uid):
    if state == "ok":
        return machine(uid, specs=[spec("Flash Point", True)])
    if state == "ok_but":
        return machine(uid, specs=[spec("Flash Point", True)], maint=[task("calibration")])
    if state == "not_ok":
        return machine(uid, specs=[spec("Flash Point", False)])
    if state == "off_line":
        return machine(uid, status="SERVICE", specs=[spec("Flash Point", True)])
    if state == "cant_tell":
        return machine(uid, running=False, module_state="stopped", specs=[spec("Flash Point", True)])
    if state == "no_qc":
        return machine(uid)
    raise AssertionError(state)


STATES = ("ok", "ok_but", "not_ok", "off_line", "cant_tell", "no_qc")


# ── the counts ──────────────────────────────────────────────────────────────

class TestTheCountsAddUpToTheFleet:
    @pytest.mark.parametrize("mix", [
        c for n in range(1, 4) for c in itertools.combinations_with_replacement(STATES, n)])
    def test_every_mix_of_states(self, mix):
        """Every instrument lands in exactly one count, whatever the mix,
        and a hidden zero cannot hide anybody (zeros are hidden, except OK)."""
        ms = [of_state(s, "m%d" % i) for i, s in enumerate(mix)]
        w = ui_wall.floor(payload(ms))
        assert sum(c["n"] for c in w["counts"]) == len(ms) == w["total"]
        for s in set(mix):
            assert next(c["n"] for c in w["counts"] if c["state"] == s) == mix.count(s)

    def test_zero_counts_are_hidden_but_ok_is_always_shown(self):
        """'0 Off line' is noise on a wall; '0 OK to run' is the finding."""
        w = ui_wall.floor(payload([of_state("not_ok", "a")]))
        states = [c["state"] for c in w["counts"]]
        assert states == ["ok", "not_ok"]
        assert w["counts"][0]["n"] == 0

    def test_the_words_and_glyphs_are_the_apps(self):
        """§4.1: the same words the record page says, a glyph shape each."""
        w = ui_wall.floor(payload([of_state(s, s) for s in STATES]))
        got = {c["state"]: (c["word"], c["glyph"]) for c in w["counts"]}
        for s in STATES:
            assert got[s] == (ui_instruments.WORDS[s], ui_instruments.GLYPH[s])

    def test_counts_run_best_first_like_the_mock_reads(self):
        """OK first, then the shades of trouble, the way C's wall reads."""
        w = ui_wall.floor(payload([of_state(s, s) for s in STATES]))
        assert [c["state"] for c in w["counts"]] == [
            "ok", "ok_but", "not_ok", "off_line", "cant_tell", "no_qc"]


# ── the headline ────────────────────────────────────────────────────────────

class TestTheHeadline:
    def test_not_ok_leads_and_names_what_and_who(self):
        ms = [machine("gc1", "Agilent GC 1", specs=[spec("Distillation 10%", False)]),
              of_state("ok_but", "b"), of_state("ok_but", "c"), of_state("ok", "d")]
        w = ui_wall.floor(payload(ms))
        assert w["headline"] == "1 instrument is not OK to run"
        assert w["sub"].startswith("Agilent GC 1 · Distillation 10% out of spec.")
        assert "2 more need attention." in w["sub"]
        assert w["tone"] == "stop"

    def test_long_method_names_are_said_by_what_differs(self):
        """Production's Agilent GC 1 fails two checks whose names are 70
        characters each and differ only at the end. A wall line that spells
        both out is cut off before it says which; the common part goes and
        the differences stay (C's mock: "10% and 50% recovery")."""
        dist = "ASTM D2887/D86 - Distillation in Petroleum Products, "
        ms = [machine("gc1", "Agilent GC 1", specs=[spec(dist + "10% Recovery", False),
                                                    spec(dist + "50% Recovery", False),
                                                    spec(dist + "FBP", True)])]
        w = ui_wall.floor(payload(ms), machines=ms)
        assert w["sub"] == "Agilent GC 1 · 2 checks out of spec: 10% Recovery and 50% Recovery."
        assert w["attention"][0]["detail"] == "2 checks out of spec: 10% Recovery and 50% Recovery"

    def test_short_names_are_kept_whole(self):
        assert ui_wall.compact_tests(["Cloud Point", "Pour Point"]) == "Cloud Point and Pour Point"
        assert ui_wall.compact_tests(["Flash Point"]) == "Flash Point"

    def test_plural_not_ok(self):
        ms = [machine("a", "A", specs=[spec("X", False)]), machine("b", "B", specs=[spec("Y", False)]),
              machine("c", "C", specs=[spec("Z", False)])]
        w = ui_wall.floor(payload(ms))
        assert w["headline"] == "3 instruments are not OK to run"
        assert w["sub"].startswith("A, B and 1 more · QC out of spec.")

    def test_all_can_run_only_when_all_can(self):
        """OK, OK-but and No QC assigned can run (Ryan, 1 Oct); nothing else."""
        ms = [of_state("ok", "a"), of_state("ok_but", "b"), of_state("no_qc", "c")]
        w = ui_wall.floor(payload(ms))
        assert w["headline"] == "All 3 instruments can run"
        assert w["sub"] == "1 needs attention: calibration overdue."
        assert w["tone"] == "ok"

    def test_all_clear(self):
        w = ui_wall.floor(payload([of_state("ok", "a"), of_state("ok", "b")]))
        assert (w["headline"], w["sub"]) == ("All 2 instruments can run", "Nothing needs attention.")

    def test_a_stopped_bench_is_not_can_run(self):
        """A frozen bench says nothing about its instrument. 'All can run'
        over it would be a verdict made from no information."""
        ms = [of_state("ok", "a"), machine("e", "Eravap", running=False, module_state="stopped",
                                           specs=[spec("RVP", True)])]
        w = ui_wall.floor(payload(ms))
        assert w["headline"] == "1 of 2 instruments can run"
        assert "Eravap · can't tell: its bench stopped checking in." in w["sub"]
        assert w["tone"] == "warn"

    def test_off_line_is_said(self):
        ms = [of_state("ok", "a"), machine("kf", "Karl Fischer", status="SERVICE")]
        w = ui_wall.floor(payload(ms))
        assert w["headline"] == "1 of 2 instruments can run"
        assert "Karl Fischer · off line." in w["sub"]

    def test_one_instrument_fleet_grammar(self):
        assert ui_wall.floor(payload([of_state("ok", "a")]))["headline"] == "The 1 instrument can run"

    def test_no_instruments_is_not_all_clear(self):
        w = ui_wall.floor(payload([]))
        assert w["headline"] == "No instruments in LEM yet"
        assert "can run" not in w["headline"]
        assert w["counts"] == []

    @pytest.mark.parametrize("err", [None, "LabCore did not answer the first read: timed out"])
    def test_a_failed_or_missing_read_is_never_a_verdict(self, err):
        w = ui_wall.floor(ui_instruments.unread(err))
        assert w["counts"] is None and w["attention"] is None and w["total"] is None
        assert "can run" not in w["headline"].lower()
        if err:
            assert w["headline"] == "Can't read the lab"
            assert "timed out" in w["sub"]
            assert "not because" in w["sub"]
        else:
            assert w["headline"] == "Reading the lab…"


# ── needs attention ─────────────────────────────────────────────────────────

class TestNeedsAttention:
    def test_worst_first_merged_by_cause(self):
        ms = [machine("p1", "PAC Flash 1", specs=[spec("FP", True)], maint=[task("calibration")]),
              machine("g2", "GC-2", specs=[spec("S", True)], maint=[task("calibration")]),
              machine("o1", "OptiMPP 1", specs=[spec("Cloud", False)]),
              machine("e", "Eravap", running=False, module_state="stopped", specs=[spec("RVP", True)]),
              of_state("ok", "fine")]
        a = ui_wall.floor(payload(ms))["attention"]
        assert [i["names"] for i in a] == [["OptiMPP 1"], ["GC-2", "PAC Flash 1"], ["Eravap"]]
        assert a[0]["word"] == "Not OK to run" and a[0]["glyph"] == "error"
        assert a[0]["detail"] == "Cloud out of spec"
        assert a[0]["href"] == "/instruments/o1#qc"
        assert a[1]["detail"] == "Calibration overdue"
        assert a[1]["href"] == "/?cause=ok_but-cal"

    def test_a_long_merged_line_names_two_and_counts_the_rest(self):
        """Five instruments overdue for calibration are one line; a card on
        a wall has room for two names, so the rest are a number, never a
        name cut in half."""
        ms = [machine("m%d" % i, "Instrument %d" % i, specs=[spec("X", True)],
                      maint=[task("calibration")]) for i in range(5)]
        a = ui_wall.floor(payload(ms))["attention"]
        assert a[0]["label"] == "Instrument 0, Instrument 1 and 3 more"
        assert len(a[0]["names"]) == 5

    def test_at_most_five_and_the_rest_counted(self):
        """A stop is never merged: each Not-OK instrument is named with its
        own failed checks, because "which one, and what" is what the room
        needs. Seven stops are five lines and "2 more", never a silent five."""
        ms = [machine("m%d" % i, "M%d" % i, specs=[spec("T%d" % i, False)]) for i in range(7)]
        w = ui_wall.floor(payload(ms))
        assert len(w["attention"]) == 5
        assert w["attention_more"] == 2

    def test_off_line_and_no_qc_are_not_attention(self):
        """Off line is a decision somebody made; No QC assigned is a fact
        about the setup. Neither is a problem that happened (NEEDS_YOU)."""
        w = ui_wall.floor(payload([machine("kf", status="SERVICE"), of_state("no_qc", "n")]))
        assert w["attention"] == [] and w["attention_more"] == 0


# ── a bay's one detail line ─────────────────────────────────────────────────

class TestBayDetails:
    """A bay on the wall is about 130px wide at 1440 and its detail line is
    14px: room for about 16 characters. "1 check in spec" under "OK to run"
    says the same thing twice and is cut anyway. The wall's line is the one
    fact the word leaves out: when QC last ran, since when a bench has been
    silent, what is overdue (C's mock: "QC 13:10", "Silent since 08:48")."""

    def details(self, ms, now=NOW):
        return ui_wall.bay_details(payload(ms), now=now)

    def test_ok_says_when_qc_last_ran(self):
        d = self.details([machine("a", specs=[spec("X", True, at="2026-10-01T13:10:00")]),
                          machine("b", specs=[spec("X", True, at="2026-09-30T08:00:00")])])
        assert d == {"a": "QC 13:10", "b": "QC 30 Sep"}

    def test_a_stopped_bench_says_since_when(self):
        d = self.details([machine("e", running=False, module_state="stopped", specs=[spec("R", True)],
                                  last_poll="2026-09-23T14:43:00"),
                          machine("v", running=False, module_state="stopped", specs=[spec("R", True)],
                                  last_poll="2026-10-01T08:48:00")])
        assert d == {"e": "Silent since 23 Sep", "v": "Silent since 08:48"}

    def test_the_rest(self):
        d = self.details([machine("n", specs=[spec("X", False)]),
                          machine("c", specs=[spec("X", True)], maint=[task("calibration")]),
                          machine("q", specs=[spec("X", None, at="2026-09-01T10:00:00")]),
                          machine("k", status="SERVICE"),
                          machine("z")])
        assert d == {"n": "QC out of spec", "c": "Calibration overdue", "q": "QC due",
                     "k": "Out for service", "z": "Checking in"}

    def test_the_short_lines_a_narrow_bay_falls_back_to(self):
        """At 1440 a 7-wide floor leaves ~110px for the line. The short
        form keeps the fact (the date) and drops the filler, rather than
        an ellipsis eating the date."""
        ms = [machine("v", running=False, module_state="stopped", specs=[spec("R", True)],
                      last_poll="2026-09-23T14:43:00"),
              machine("c", specs=[spec("X", True)], maint=[task("calibration")])]
        d = ui_wall.bay_details(payload(ms), now=NOW, short=True)
        assert d == {"v": "Silent 23 Sep", "c": "Cal. overdue"}

    def test_unread_is_empty(self):
        assert ui_wall.bay_details(ui_instruments.unread(None), now=NOW) == {}


# ── /qc ─────────────────────────────────────────────────────────────────────

def qc_rows(uid, test, values, sample="AF26", low=1.0, high=2.0, expected=1.5, fails=()):
    import json
    return [{"machine_uid": uid, "ts": "2026-09-%02dT09:00:00" % (i + 1), "kind": "qc",
             "lab_id": sample, "test_name": test, "value": str(v),
             "detail": json.dumps({"low": low, "high": high, "expected": expected,
                                   "in_spec": i not in fails})}
            for i, v in enumerate(values)]


class TestQcCards:
    def ms(self):
        return [machine("gc1", "Agilent GC 1", specs=[spec("D10", False, value=2.31), spec("D50", True)]),
                machine("kv", "Koehler", specs=[spec("Visc 40", None, at="2026-09-20T10:00:00")]),
                machine("ev", "Eravap", running=False, module_state="stopped",
                        specs=[spec("RVP", None, at="")]),
                machine("pf", "PAC Flash 1", specs=[spec("Flash Point", True, value=63.70,
                                                         low=61.6, high=65.8, expected=63.7)])]

    def test_verdict_words_are_the_apps_and_worst_first(self):
        q = ui_wall.qc(self.ms(), rows=[], href=href, now=NOW)
        assert [(c["title"], c["test"], c["verdict"]["word"]) for c in q["cards"]] == [
            ("Agilent GC 1", "D10", "Out of spec"),
            ("Koehler", "Visc 40", "QC due"),
            ("Eravap", "RVP", "No verdict yet"),
            ("Agilent GC 1", "D50", "In spec"),
            ("PAC Flash 1", "Flash Point", "In spec")]
        assert q["cards"][2]["verdict"]["note"] == "bench stopped"
        assert q["cards"][0]["href"] == "/instruments/gc1#qc"

    def test_the_headline_counts_checks_in_the_same_words(self):
        q = ui_wall.qc(self.ms(), rows=[], href=href, now=NOW)
        assert q["headline"] == "1 check out of spec · 1 due · 1 no verdict yet · 2 in spec"
        assert sum(q["counts"].values()) == len(q["cards"]) == 5
        assert q["tone"] == "stop"

    def test_all_in_spec(self):
        q = ui_wall.qc([machine("a", specs=[spec("X", True)]), machine("b", specs=[spec("Y", True)])],
                       rows=[], href=href, now=NOW)
        assert q["headline"] == "All 2 checks in spec" and q["tone"] == "ok"

    def test_nothing_assigned_is_not_all_in_spec(self):
        q = ui_wall.qc([machine("a")], rows=[], href=href, now=NOW)
        assert q["cards"] == []
        assert q["headline"] == "No QC is assigned to any instrument"

    def test_unread_is_not_empty(self):
        q = ui_wall.qc(None, rows=None, href=href, now=NOW, error="timed out")
        assert q["cards"] is None and q["headline"] == "Can't read QC"
        assert "timed out" in q["sub"]

    def test_values_keep_the_bands_decimals(self):
        """§4.2: 63.70 against 61.6 – 63.7 – 65.8 is written 63.7 (one
        decimal, the band's), and the band uses one decimals for all three."""
        q = ui_wall.qc(self.ms(), rows=[], href=href, now=NOW)
        pf = next(c for c in q["cards"] if c["title"] == "PAC Flash 1")
        assert pf["last"]["value"] == "63.7"
        assert pf["last"]["band"] == "61.6 – 63.7 – 65.8"

    def test_small_limits_keep_their_significant_figures(self):
        assert ui_wall.fmt_qc(0.0011, (0.0009, 0.0011, 0.0013)) == "0.0011"
        assert ui_wall.fmt_qc(334.9, (331.48, 334.9, 338.32)) == "334.90"

    def test_history_is_normalised_to_the_band(self):
        rows = qc_rows("pf", "Flash Point", [61.6, 63.7, 65.8], low=61.6, high=65.8, expected=63.7)
        q = ui_wall.qc(self.ms(), rows=rows, href=href, now=NOW)
        pf = next(c for c in q["cards"] if c["title"] == "PAC Flash 1")
        assert [round(p["z"], 3) for p in pf["points"]] == [-1.0, 0.0, 1.0]
        assert pf["history"] == "ok"

    def test_unreadable_history_is_said_not_drawn_flat(self):
        """The verdict still stands (it is the snapshot's); the chart says
        its history could not be read rather than drawing nothing."""
        q = ui_wall.qc(self.ms(), rows=None, href=href, now=NOW)
        assert all(c["history"] == "unread" and c["points"] == [] for c in q["cards"])
        assert q["cards"][0]["verdict"]["word"] == "Out of spec"

    def test_a_broken_control_rule_is_a_chip(self):
        """Eight rising points, all in spec: no limit was crossed, but the
        trend rule broke, and a wall that only says "In spec" hides the one
        thing the chart is for. The trend rule reads no fitted mean, so its
        chip is firm (qc_series.violations); the zone rules' chips say
        "provisional" because their limits were fitted to the points they
        judge."""
        vals = [1.10, 1.15, 1.20, 1.25, 1.30, 1.35, 1.40, 1.45]
        rows = qc_rows("a", "X", vals)
        q = ui_wall.qc([machine("a", "A", specs=[spec("X", True, value=1.45)])], rows=rows,
                       href=href, now=NOW)
        chip = q["cards"][0]["control"]
        assert chip == {"rule": "trend", "provisional": False, "words": "Trend: 7 in a row"}

    def test_a_zone_rule_chip_says_provisional(self):
        vals = [1.50, 1.49, 1.51, 1.50, 1.49, 1.51, 1.50, 1.49, 1.51, 1.50, 1.62, 1.63]
        rows = qc_rows("a", "X", vals)
        q = ui_wall.qc([machine("a", "A", specs=[spec("X", True, value=1.63)])], rows=rows,
                       href=href, now=NOW)
        chip = q["cards"][0]["control"]
        assert chip and chip["provisional"] is True and chip["words"].endswith("· provisional")

    def test_out_of_spec_says_it_once(self):
        """A failed last result is "Out of spec"; a "1 beyond 3s" chip beside
        it would be the same fact twice. The chip is for a card whose
        verdict looks fine."""
        vals = [1.50, 1.49, 1.51, 1.50, 1.49, 1.51, 1.50, 1.49, 1.51, 2.4]
        rows = qc_rows("a", "X", vals, fails=(9,))
        q = ui_wall.qc([machine("a", "A", specs=[spec("X", False, value=2.4)])], rows=rows,
                       href=href, now=NOW)
        assert q["cards"][0]["verdict"]["word"] == "Out of spec"
        assert q["cards"][0]["control"] is None

    def test_the_sub_line_names_failures_by_instrument(self):
        ms = [machine("o", "OptiMPP 1", specs=[spec("Cloud Point", False), spec("Pour Point", False)]),
              machine("p", "Pensky-Martens 1", specs=[spec("Flash Point", False)])]
        q = ui_wall.qc(ms, rows=[], href=href, now=NOW)
        assert q["sub"] == "OptiMPP 1 · Cloud Point and Pour Point. Pensky-Martens 1 · Flash Point."

    def test_no_chip_when_no_rule_broke(self):
        rows = qc_rows("a", "X", [1.5, 1.4, 1.6, 1.5, 1.45, 1.55])
        q = ui_wall.qc([machine("a", "A", specs=[spec("X", True)])], rows=rows, href=href, now=NOW)
        assert q["cards"][0]["control"] is None


# ── /qc on production's shape (round 2) ─────────────────────────────────────
#
# Round 1's /qc was checked on demo data and on a hand-made Eravap that had
# been given an `effective_specs` row. Production's Eravap has no such row:
# its RVP check exists only as an assignment (`qc_targets`), because its
# bench has never reported a QC result. A wall that reads only
# effective_specs therefore dropped the check, and its headline
# ("2 checks out of spec · 17 in spec") counted a fleet one check short. The
# room would read that as "every assigned check is accounted for", which it
# was not. The fixture below is production's /api/machines answer of 1 Oct.

import json as _json
import os as _os

PROD_FIXTURE = _os.path.join(_os.path.dirname(__file__), "fixtures", "machines_live_2026-10-01.json")


def prod_machines():
    with open(PROD_FIXTURE, encoding="utf-8") as fh:
        return _json.load(fh)["machines"]


class TestQcAssignmentsAndStoppedBenches:
    RVP = "ASTM D6378 - Reid Vapor Pressure (VPx)"

    def test_an_assignment_with_no_result_yet_is_a_card(self):
        """§4.1: Eravap has an assignment (Pentane / RVP) and its module is
        stopped, so it reads "No verdict yet · bench stopped", never nothing
        and never "No QC assigned"."""
        ev = machine("ev", "Eravap", running=False, module_state="stopped",
                     targets=[{"sample": "Pentane", "test": self.RVP}])
        q = ui_wall.qc([ev], rows=[], href=href, now=NOW)
        assert len(q["cards"]) == 1
        c = q["cards"][0]
        assert (c["title"], c["test"], c["verdict"]["word"], c["verdict"]["note"]) == (
            "Eravap", self.RVP, "No verdict yet", "bench stopped")
        assert c["sample_id"] == "Pentane"
        assert c["last"]["value"] is None and c["last"]["band"] == ""
        assert q["headline"] == "1 check no verdict yet"
        assert q["counts"]["never"] == 1

    def test_the_snapshots_own_target_shape_is_read_too(self):
        """snapshot_service writes {sample_name, test_name}; the merged
        /api/machines answer says {sample, test}. Either is an assignment."""
        ev = machine("ev", "Eravap", targets=[{"sample_name": "Pentane", "test_name": self.RVP}])
        q = ui_wall.qc([ev], rows=[], href=href, now=NOW)
        assert [(c["test"], c["verdict"]["word"], c["verdict"]["note"]) for c in q["cards"]] == [
            (self.RVP, "No verdict yet", "")]

    def test_an_assignment_that_has_a_spec_is_one_card_not_two(self):
        gc = machine("gc", "Agilent GC 1", specs=[spec("D10", True)],
                     targets=[{"sample": "AF26", "test": "D10"}, {"sample": "AF26", "test": " d10 "}])
        q = ui_wall.qc([gc], rows=[], href=href, now=NOW)
        assert [c["test"] for c in q["cards"]] == ["D10"]

    def test_a_stopped_benchs_old_pass_is_no_verdict_yet(self):
        """§4.1: "No verdict yet" is also a check whose bench is stopped. A
        pass from before the bench stopped says nothing about today, and the
        floor already calls that instrument "Can't tell"; /qc saying
        "In spec" beside it would be two answers to one question. The last
        result is still shown, with its date, so nothing is hidden."""
        vi = machine("vi", "Viscocity", running=False, module_state="stopped",
                     specs=[spec("Visc 40", True, value=2.34, low=2.2989, high=2.3817, expected=2.3403)])
        q = ui_wall.qc([vi], rows=[], href=href, now=NOW)
        c = q["cards"][0]
        assert (c["verdict"]["word"], c["verdict"]["note"]) == ("No verdict yet", "bench stopped")
        assert c["last"]["value"] == "2.3400" and c["last"]["at"]

    def test_a_stopped_benchs_failure_still_says_out_of_spec(self):
        """Out of spec is what makes the floor say Not OK to run; stopping
        the bench does not make a failed check go away (ui_live.readiness
        puts QC before the bench). Same for QC due."""
        ms = [machine("a", "A", running=False, module_state="stopped", specs=[spec("X", False)]),
              machine("b", "B", running=False, module_state="stopped",
                      specs=[spec("Y", None, at="2026-09-20T10:00:00")])]
        q = ui_wall.qc(ms, rows=[], href=href, now=NOW)
        assert [(c["verdict"]["word"], c["verdict"]["note"]) for c in q["cards"]] == [
            ("Out of spec", "bench stopped"), ("QC due", "bench stopped")]

    def test_production_every_assigned_check_is_on_the_wall(self):
        q = ui_wall.qc(prod_machines(), rows=[], href=href, now=NOW)
        assigned = sum(max(len(m.get("effective_specs") or []), len(m.get("qc_targets") or []))
                       for m in prod_machines())
        assert len(q["cards"]) == assigned == 20
        by = {(c["title"], c["verdict"]["word"], c["verdict"]["note"]) for c in q["cards"]}
        assert ("Eravap", "No verdict yet", "bench stopped") in by
        assert ("Viscocity", "No verdict yet", "bench stopped") in by
        assert q["headline"] == "2 checks out of spec · 2 no verdict yet · 16 in spec"
        assert sum(q["counts"].values()) == 20


class TestQcCardWords:
    """A card on a TV has room for about 40 characters a line. Production's
    Agilent GC 1 has five checks whose names share their first 51 characters
    ("ASTM D2887/D86 - Distillation in Petroleum Products, ") and differ only
    after it, so cut to fit, all five read the same: two Out of spec cards
    that could not be told from three In spec ones. The card leads with what
    tells the check apart and puts the shared method on a quieter line that
    may be cut."""

    def card(self, q, title, part):
        return next(c for c in q["cards"] if c["title"] == title and part in c["test"])

    def test_gc1s_five_checks_are_told_apart_by_what_differs(self):
        q = ui_wall.qc(prod_machines(), rows=[], href=href, now=NOW)
        gc = [c for c in q["cards"] if c["title"] == "Agilent GC 1"]
        assert sorted(c["check"] for c in gc) == ["10% Recovery", "50% Recovery", "90% Recovery", "FBP", "IBP"]
        assert {c["method"] for c in gc} == {"ASTM D2887/D86 · Distillation in Petroleum Products"}
        assert [c["check"] for c in gc if c["verdict"]["key"] == "out"] == ["10% Recovery", "50% Recovery"]

    def test_a_single_check_drops_its_standard_code_to_the_method_line(self):
        q = ui_wall.qc(prod_machines(), rows=[], href=href, now=NOW)
        aq = self.card(q, "Aquamax 1", "Water")
        assert (aq["check"], aq["method"]) == ("Water, by Karl Fischer", "ASTM D6304")
        vi = self.card(q, "Viscocity", "Viscosity")
        assert (vi["check"], vi["method"]) == ("Viscosity - Kinematic at 40°C (cSt)", "ASTM D445 40C")

    def test_a_name_with_no_code_is_kept_whole(self):
        q = ui_wall.qc([machine("a", "A", specs=[spec("Flash Point", True)])], rows=[], href=href, now=NOW)
        assert (q["cards"][0]["check"], q["cards"][0]["method"]) == ("Flash Point", "")

    def test_the_limits_are_said_so_a_minus_cannot_be_read_as_a_dash(self):
        """"-24.7 – -18.3 – -11.9" reads as a range of dashes. The card says
        "−24.7 to −11.9" with the target apart, using the minus sign."""
        q = ui_wall.qc(prod_machines(), rows=[], href=href, now=NOW)
        pp = self.card(q, "OptiMPP 1", "Pour Point")
        assert (pp["last"]["range"], pp["last"]["target"]) == ("−24.7 to −11.9", "−18.3")
        assert pp["last"]["band"] == "−24.7 – −18.3 – −11.9"
        d10 = self.card(q, "Agilent GC 1", "10% Recovery")
        assert (d10["last"]["range"], d10["last"]["target"]) == ("185.05 to 190.21", "187.63")

    def test_the_sub_line_says_what_differs_too(self):
        q = ui_wall.qc(prod_machines(), rows=[], href=href, now=NOW)
        assert q["sub"] == "Agilent GC 1 · 10% Recovery and 50% Recovery."


class TestAttentionRuns:
    """Needs attention says each state word once, as the heading of its
    group (round 4: "OK to run, but…" said on every card made the warning
    tier compete with the failures, both blind judges). The groups are runs
    of one state in the list's own worst-first order, never a re-sort: the
    server already ranked the lines, and the wall must not undo that."""

    def test_consecutive_states_become_one_group_each(self):
        items = [{"state": "not_ok", "n": 1}, {"state": "not_ok", "n": 2},
                 {"state": "ok_but", "n": 3}, {"state": "off_line", "n": 4}, {"state": "off_line", "n": 5}]
        assert [[i["n"] for i in g] for g in ui_wall.runs(items, "state")] == [[1, 2], [3], [4, 5]]

    def test_nothing_is_no_groups_and_a_bad_list_is_not_a_crash(self):
        assert ui_wall.runs([], "state") == []
        assert ui_wall.runs(None, "state") == []

    def test_order_is_kept_even_if_a_state_comes_back(self):
        items = [{"state": "a"}, {"state": "b"}, {"state": "a"}]
        assert [g[0]["state"] for g in ui_wall.runs(items, "state")] == ["a", "b", "a"]


# ── round 5: one check, one word, on both walls ─────────────────────────────
#
# The round-4 critic: /qc called Koehler K23000's Viscosity 40C "No verdict
# yet · Never run" (right: §4.1 says a check that is assigned but has never
# run has no verdict yet), while /floor called the same check "QC due" in its
# bay, in Needs attention ("QC due on Viscosity 40C") and in the headline's
# sub-line. /wall alternates the two, so the room saw one check called two
# things a minute apart. "QC due" means the check ran before and its pass
# has lapsed; a check that never ran has nothing to lapse. The floor now
# says, per check, the word /qc says: these tests hold the floor's QC words
# to /qc's card words for the same instruments, so the two cannot drift.

KOEHLER = dict(uid="kv", title="Koehler K23000")


def never_run(test="Viscosity 40C"):
    return spec(test, None, at="")


def _qc_words_on_floor(w, details, uid, title):
    """Every QC word the floor says about one instrument: its bay line, its
    Needs-attention line (or its merged group's), and the headline's sub."""
    said = [details.get(uid, "")]
    for a in w["attention"]:
        if title in a["names"]:
            said.append(a["detail"])
    said.append(w["sub"])
    return " | ".join(said)


class TestOneWordPerCheckOnBothWalls:
    def fleet(self):
        return [machine("kv", "Koehler K23000", specs=[never_run()]),
                machine("pf", "PAC Flash 1", specs=[spec("Flash Point", None, at="2026-09-20T10:00:00")]),
                of_state("ok", "fine")]

    def test_a_check_that_never_ran_is_no_verdict_yet_on_the_floor_too(self):
        ms = self.fleet()
        q = ui_wall.qc(ms, rows=[], href=href, now=NOW)
        card = {c["uid"]: c["verdict"]["word"] for c in q["cards"]}
        assert card == {"kv": "No verdict yet", "pf": "QC due", "fine": "In spec"}
        p = payload(ms)
        w = ui_wall.floor(p)
        d = ui_wall.bay_details(p, now=NOW)
        assert d["kv"] == "No verdict yet"
        assert d["pf"] == "QC due"
        kv = [a for a in w["attention"] if "Koehler K23000" in a["names"]]
        assert kv and kv[0]["detail"] == "No verdict yet on Viscosity 40C", kv
        pf = [a for a in w["attention"] if "PAC Flash 1" in a["names"]]
        assert pf and pf[0]["detail"] == "QC due on Flash Point", pf
        # the two are different facts, so they are two lines, not one merged
        # line under one word
        assert kv[0] is not pf[0]
        assert "no verdict yet" in w["sub"] and "QC due" in w["sub"], w["sub"]

    def test_the_instrument_word_is_unchanged(self):
        """The instrument's own word stays the §4.1 instrument word: an
        assigned check with no verdict is still "OK to run, but…" (it can
        run, somebody should run the standard). Only the reason changes."""
        p = payload([machine("kv", "Koehler K23000", specs=[never_run()])])
        r = p["instruments"][0]
        assert r["readiness"]["word"] == "OK to run, but…"
        assert r["readiness"]["detail"] == "No verdict yet on Viscosity 40C"
        assert r["cause"]["words"] == "No verdict yet"
        assert r["readiness"]["next"]["text"] == "Run AF26"
        qc_tile = [t for t in r["readiness"]["tiles"] if t["key"] == "qc"][0]
        assert qc_tile["word"] == "No verdict yet"

    def test_one_instrument_with_both_says_both_each_by_its_own_word(self):
        """Lapsed on Density, never run on Viscosity: the record's line names
        each check under the word /qc gives that check."""
        p = payload([machine("kv", "Koehler K23000",
                             specs=[never_run(), spec("Density", None, at="2026-09-20T10:00:00")],
                             maint=[task("pm")])])
        r = p["instruments"][0]
        assert r["readiness"]["detail"] == \
            "QC due on Density · no verdict yet on Viscosity 40C · PM overdue since 1 Sep too"
        assert r["cause"]["words"] == "QC due"

    def test_needs_you_names_the_tile_by_what_its_members_have(self):
        p = payload([machine("kv", "Koehler K23000", specs=[never_run()])])
        tiles = {t["key"]: t["cause"] for t in p["needs_you"]["tiles"]}
        assert tiles["ok_but-qc"] == "No verdict yet"
        p = payload([machine("kv", "Koehler K23000", specs=[never_run()]),
                     machine("pf", "PAC Flash 1", specs=[spec("Flash Point", None, at="2026-09-20T10:00:00")])])
        tiles = {t["key"]: t["cause"] for t in p["needs_you"]["tiles"]}
        assert tiles["ok_but-qc"] == "QC due or no verdict yet"

    @pytest.mark.parametrize("mix", [
        ("never",), ("lapsed",), ("never", "lapsed"), ("never", "in"), ("lapsed", "in"),
        ("never", "out"), ("lapsed", "out"), ("never", "lapsed", "in"),
    ])
    def test_every_qc_word_the_floor_says_is_a_word_qc_says(self, mix):
        """The invariant, over every mix of check states on one instrument:
        if the floor says "QC due" anywhere about it, /qc has a "QC due"
        card for it; if it says "no verdict yet", /qc has a "No verdict yet"
        card. Never one without the other."""
        make = {"never": lambda i: spec("T%d" % i, None, at=""),
                "lapsed": lambda i: spec("T%d" % i, None, at="2026-09-20T10:00:00"),
                "in": lambda i: spec("T%d" % i, True),
                "out": lambda i: spec("T%d" % i, False)}
        ms = [machine("kv", "Koehler K23000", specs=[make[k](i) for i, k in enumerate(mix)])]
        q = ui_wall.qc(ms, rows=[], href=href, now=NOW)
        qc_words = {c["verdict"]["word"].lower() for c in q["cards"]}
        p = payload(ms)
        said = _qc_words_on_floor(ui_wall.floor(p, ms), ui_wall.bay_details(p, now=NOW),
                                  "kv", "Koehler K23000").lower()
        said += " | " + p["instruments"][0]["readiness"]["detail"].lower()
        for word in ("qc due", "no verdict yet"):
            assert (word in said) == (word in qc_words), (word, said, qc_words)
