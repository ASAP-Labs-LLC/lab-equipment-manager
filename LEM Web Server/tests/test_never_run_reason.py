"""A check that has never run is "No verdict yet" in every sentence, not "QC due".

Round 9's functional critic, on the dev seed's Koehler K23000: the QC tile
and the check's row said "No verdict yet · Never run", but the card's
sentence said "QC due on Viscosity 40C (STD-1). Next: run STD-1." and the
Instruments list's row said "QC due on Viscosity 40C" beside a Last QC
column reading "No verdict yet". Two words for one fact on one page, and a
third on the page before it.

§4.1 defines the two words differently, and the difference matters to the
analyst at the bench: **QC due** is a pass that aged out of its window (the
instrument read true once and nobody has shown it still does), **No verdict
yet** is an assigned check that has never run (nobody has ever shown it reads
true). Both leave the instrument "OK to run, but…", and both are remedied by
running the standard, so the verdict, its problem key ("ok_but-qc") and its
next step do not change. Only the words do, and they change everywhere a
reason is put into words: the readiness reason, the list row's line, the
record card's sentence, the "…too" tail behind another verdict, the
problem's words, the home's Needs-you tile and the bell.

A check that ran before and one that never ran, on the same instrument, are
said apart in one sentence: "QC due on Density · no verdict yet on Vapour".

The second nit of that round: GC-2's card acts in place ("Mark the
calibration done…"), but the Maintenance section under it still said "use
the PM and calibration page until it moves here". Guidance about where a
step will be one day is stale the moment the page takes the step.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import ui_live
from test_ui_instruments import build, machine, row, spec
from test_ui_record import _seeded

ROOT = Path(__file__).resolve().parent.parent

AGED = "2026-09-20T11:23:00"     # a pass well outside the 24 h window


def _row(machines, uid):
    return row(build(machines), uid)


class TestTheReasonSaysWhichKindOfOwed:
    def test_never_run_alone_is_no_verdict_yet(self):
        m = machine("k", "Koehler K23000", specs=[spec("Vapour", None, sample="STD-1")])
        r = ui_live.readiness(m)
        assert r == {"state": "ok_but", "reason": "No verdict yet: Vapour"}
        assert ui_live.is_qc_owed(r["reason"])

    def test_an_aged_pass_alone_is_still_qc_due(self):
        m = machine("d", specs=[spec("Density", True, at=AGED)])
        assert ui_live.readiness(m)["reason"] == "QC due: Density"

    def test_both_on_one_instrument_say_qc_due_first(self):
        """The stronger word leads (a run is owed and one ran before); the
        never-run check is still named for what it is in the sentence."""
        m = machine("d", specs=[spec("Density", True, at=AGED), spec("Vapour", None)])
        assert ui_live.readiness(m)["reason"] == "QC due: Density, Vapour"
        r = _row([m], "d")
        assert r["readiness"]["detail"] == "QC due on Density · no verdict yet on Vapour"


class TestTheListRowSaysIt:
    def test_never_run_row(self):
        r = _row([machine("k", "Koehler K23000", specs=[spec("Vapour", None, sample="STD-1")])], "k")
        assert r["readiness"]["word"] == "OK to run, but…"
        assert r["readiness"]["detail"] == "No verdict yet on Vapour"
        assert r["readiness"]["next"]["text"] == "Run STD-1"
        assert r["cause"]["key"] == "ok_but-qc" and r["cause"]["words"] == "No verdict yet"
        assert r["problems"] == [{"key": "ok_but-qc", "words": "No verdict yet"}]
        assert "QC due" not in json.dumps(r)

    def test_behind_another_verdict_it_says_no_verdict_yet_too(self):
        m = machine("o", specs=[spec("Cloud Point", False), spec("Vapour", None)])
        r = _row([m], "o")
        assert r["readiness"]["detail"] == "Cloud Point out of spec · no verdict yet on Vapour too"


class TestTheHomeTileAndBellSayIt:
    def test_a_tile_of_only_never_run_instruments(self):
        ms = [machine("k", specs=[spec("Vapour", None)]), machine("j", specs=[spec("RVP", None)])]
        (t,) = build(ms)["needs_you"]["tiles"]
        assert (t["key"], t["cause"]) == ("ok_but-qc", "No verdict yet")
        assert t["next"]["text"] == "Run the QC standard"

    def test_a_tile_holding_both_kinds_says_both(self):
        """One key, one filter, two kinds of member: the tile names both
        words, the lapsed one first, as /floor's Needs attention does. A tile
        that said only "QC due" would call Vapour's never-run check a lapse."""
        ms = [machine("k", specs=[spec("Vapour", None)]),
              machine("d", specs=[spec("Density", True, at=AGED)])]
        (t,) = build(ms)["needs_you"]["tiles"]
        assert t["cause"] == "QC due or no verdict yet"


class TestTheDevSeedSaysOneWord:
    def test_koehler_k23000_on_the_list_the_record_home_and_bell(self, tmp_path):
        """The critic's own walk: list row, record card, QC tile, check row,
        home tile and bell, for the dev seed's Koehler K23000. None of them
        may say "QC due" for a check that has never run."""
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        page = c.get("/api/ui/instruments").get_json()
        k = next(r for r in page["instruments"] if r["title"] == "Koehler K23000")
        assert k["last_qc"]["word"] == "No verdict yet"
        assert k["readiness"]["detail"] == "No verdict yet on Viscosity 40C"
        assert "QC due" not in json.dumps(k), k

        rec = c.get("/api/ui/instruments/%s" % k["uid"]).get_json()
        cap = rec["readiness"]["caption"]
        assert cap["lead"] == "No verdict yet on Viscosity 40C"
        assert cap["next"] == "Run STD-1"
        assert "QC due" not in json.dumps(rec), "one fact, one word, on the whole record"

        tile = next(t for t in page["needs_you"]["tiles"]
                    if any(m["uid"] == k["uid"] for m in t["members"]) and t["key"] == "ok_but-qc")
        assert tile["cause"] == "No verdict yet"

        bell = c.get("/api/ui/live").get_json()["notifications"]
        line = next(n for n in bell if n["id"].startswith("qcdue:"))
        assert line["message"] == "1 instrument has no QC verdict yet: Koehler K23000.", line
        assert "due for QC" not in json.dumps(bell)


class TestTheMaintenanceSectionHasNoStaleGuidance:
    def test_no_until_it_moves_here(self):
        """Nothing on the record promises a future home for a step: where the
        card marks the overdue task done, the section says nothing more; where
        it does not, it links the PM and calibration page as where the task is
        scheduled, without "until it moves here"."""
        js = (ROOT / "static" / "js" / "record.js").read_text()
        assert "until it moves here" not in js
        assert "The card above marks the overdue one done" not in js
        fn = js[js.index("function renderMaintenance"):js.index("function renderBench")]
        assert re.search(r"act === 'done'\s*\?\s*\[\]", fn), \
            "when the card acts in place, the section adds no guidance"
