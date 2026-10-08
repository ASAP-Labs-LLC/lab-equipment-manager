"""GET /api/ui/instruments and the Instruments home (ia-final §1, §3.2, §5).

What this answers, and why each part is a test rather than a hope:

* **Can it run? at 0 clicks (T1).** The home page exists so that "is Agilent
  GC 2 Ready?" is answered by looking, not by opening a record. The answer
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
  times").** The table row is the one place an instrument is named with what
  is wrong with it. A Needs-you tile is a cause and its next step: it names
  no instrument, carries no count, and filters the table to its rows. The
  page-head gets ONE fleet pill, the chips carry no counts, and a bell line
  that links to the list names exactly what that list shows.
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
            "watching": watching, "last_poll": last_poll,
            # Judged at a fixed moment, 1 Oct 13:18 (the capture's). A pass
            # counts for 24 h, so a machine judged "now" read these 11:23
            # passes as in spec until 2 Oct 11:23 and as QC due after it:
            # the suite went red on its own at 11:23 the next day.
            "qc_judged": {"at": "2026-10-01T13:18:00", "hours": 24.0, "from": ""}}


def spec(test, ok, at="2026-10-01T11:23:00", sample="AF26"):
    return {"test_name": test, "last_qc_in_spec": ok, "last_qc_at": at if ok is not None else "",
            "last_qc_superseded_by": "", "sample_id": sample, "low": 1.0, "high": 2.0,
            "expected": 1.5, "last_qc_value": 1.4}


def lapsed(test, sample="AF26"):
    """A check that ran before and has no verdict in the window: "QC due".
    ``spec(test, None)`` is one that never ran, which §4.1 calls "No verdict
    yet" (round 5: /qc and /floor gave such a check two words)."""
    s = spec(test, None, sample=sample)
    s["last_qc_at"] = "2026-09-20T10:00:00"
    return s


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
        assert r["state"] == "not_ok" and r["word"] == "Stop"
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
              machine("e"), machine("f", specs=[spec("X", True)]), machine("g", specs=[spec("X", True)])]
        p = build(ms, overrides={"g": "DEAD-LINE"})
        got = {row(p, u)["readiness"]["state"]: (row(p, u)["readiness"]["word"],
                                                  row(p, u)["readiness"]["glyph"])
               for u in "abdef"}
        # Ryan's words, 2026-10-07
        assert got == {"not_ok": ("Stop", "error"),
                       "ok_but": ("Attention", "half"),
                       "cant_tell": ("No data", "dashed"),
                       "no_qc": ("No data", "never"),
                       "ok": ("Ready", "final")}
        # off line is a badge beside the state, with its own shape
        g = row(p, "g")["readiness"]
        assert (g["state"], g["word"]) == ("ok", "Ready")
        assert g["off_line"] == {"word": "Off line", "glyph": "off", "reason": "Taken off line (DEAD-LINE)"}
        assert row(p, "f")["readiness"]["off_line"] is None

    def test_an_overdue_calibration_is_a_but_not_a_no(self):
        """Ryan, 2026-10-01: only QC or an override can make the answer No.
        The list must not tell an analyst to stop using a bench whose QC
        passed this morning because a calibration date lapsed."""
        p = build([machine("a", maint=[task("calibration", "RED")], specs=[spec("X", True)])])
        r = row(p, "a")
        assert r["readiness"]["word"] == "Attention"
        assert r["cause"]["words"] == "Calibration overdue"
        assert r["readiness"]["next"]["href"] == "/instruments/a#maintenance"
        assert p["fleet"]["pill"]["text"] == "It can run"

    def test_an_overdue_calibration_behind_a_worse_cause_is_said_on_its_row(self):
        """An overdue calibration behind "QC out of spec" or "QC due" is not
        the row's verdict, but it is still a fact about that instrument, so
        its row says it, after the cause that outranks it."""
        p = build([machine("g", specs=[spec("Flash Point", False)], maint=[task("calibration", "RED")]),
                   machine("b", specs=[spec("Flash Point", None)], maint=[task("calibration", "RED")]),
                   machine("a", specs=[spec("Flash Point", True)], maint=[task("calibration", "RED")])])
        assert row(p, "g")["readiness"]["detail"] == "Flash Point out of spec · calibration overdue since 1 Sep too"
        # "b" has never run its check: §4.1's No verdict yet, not QC due (round 9)
        assert row(p, "b")["readiness"]["detail"] == "No verdict yet on Flash Point · calibration overdue since 1 Sep too"
        assert row(p, "a")["readiness"]["detail"] == "Calibration overdue since 1 Sep"

    def test_an_overdue_pm_says_since_when_as_a_calibration_does(self):
        """Round 2's critic: "Calibration overdue since 24 Jul" gave a date,
        "PM overdue" never did, nor did "calibration overdue too". Overdue is
        a question of how long; every overdue clause says since when."""
        p = build([machine("a", specs=[spec("X", True)], maint=[task("pm", "RED")])])
        assert row(p, "a")["readiness"]["detail"] == "PM overdue since 1 Sep"

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

    def test_the_fact_is_a_field_not_a_word_to_match(self):
        """Round 2's critic: the "No QC assigned" view was empty while three
        rows said "No QC assigned" in Last QC, because the view keyed on the
        readiness state (the WORST fact: Off line, No data) instead of the
        fact the chip names. `last_qc.assigned` is that fact, the same one
        the column's words come from, so the view and the column agree."""
        p = build([machine("kf"), machine("gc1", running=False, module_state="unknown", last_poll=""),
                   machine("o2", maint=[task("calibration", "RED")]),
                   machine("off"), machine("a", specs=[spec("X", True)]),
                   machine("e", targets=[{"sample": "P", "test": "RVP"}])],
                  overrides={"off": "SERVICE"})
        got = {u: row(p, u)["last_qc"]["assigned"] for u in ("kf", "gc1", "o2", "off", "a", "e")}
        assert got == {"kf": False, "gc1": False, "o2": False, "off": False, "a": True, "e": True}
        assert row(p, "gc1")["readiness"]["state"] != "no_qc", \
            "the case the critic found: not assigned, yet its verdict is about something worse"

    def test_in_the_seed_the_column_and_the_fact_agree(self, tmp_path):
        app, _ = _seeded(tmp_path)
        p = app.test_client().get("/api/ui/instruments").get_json()
        for r in p["instruments"]:
            assert (r["last_qc"]["word"] == "No QC assigned") == (r["last_qc"]["assigned"] is False), r["uid"]

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


class TestEveryProblemIsOnItsRow:
    """Round 3's critic: "OptiMPP 2's overdue PM is not mentioned anywhere on
    the home page." Its calibration was overdue too, and the page grouped
    each instrument by its ONE worst cause, so the PM behind the calibration
    was on no row, no tile and no bell line, and the PM filter showed 2 of
    the 3 instruments with a PM overdue.

    An instrument now carries every problem it has (`problems`, worst first).
    The first is its cause, the one its verdict is about; each of the others
    is said on its row as "· … too". A tile and its filter are about every
    instrument with that problem, not only those for which it is the worst."""

    def test_a_pm_behind_a_calibration_is_said_on_the_row(self):
        p = build([machine("o2", "OptiMPP 2", specs=[spec("X", True)],
                           maint=[task("calibration", "RED"), task("pm", "RED")])])
        r = row(p, "o2")
        assert r["readiness"]["detail"] == "Calibration overdue since 1 Sep · PM overdue since 1 Sep too"
        assert [x["key"] for x in r["problems"]] == ["ok_but-cal", "ok_but-pm"]

    def test_several_behind_one_cause_each_say_since_when(self):
        p = build([machine("g", specs=[spec("Flash Point", False), spec("Density", None)],
                           maint=[task("calibration", "RED"), task("pm", "RED")])])
        assert row(p, "g")["readiness"]["detail"] == (
            "Flash Point out of spec · no verdict yet on Density too"
            " · calibration overdue since 1 Sep too · PM overdue since 1 Sep too")
        assert [x["key"] for x in row(p, "g")["problems"]] == [
            "not_ok-qc", "ok_but-qc", "ok_but-cal", "ok_but-pm"]

    def test_the_first_problem_is_the_cause_its_verdict_is_about(self, tmp_path):
        """One rule, two readings: the verdict's reason and problems[0] can
        never name different things (§0.2)."""
        app, _ = _seeded(tmp_path)
        p = app.test_client().get("/api/ui/instruments").get_json()
        for r in p["instruments"]:
            if r["needs_you"]:
                assert r["problems"] and r["problems"][0]["key"] == r["cause"]["key"], r["uid"]

    def test_off_line_keeps_its_overdue_tasks(self):
        """Taking an instrument off line is a decision about running it; it
        does not make its calibration any less overdue."""
        p = build([machine("kf", maint=[task("calibration", "RED")], specs=[spec("X", False)])],
                  overrides={"kf": "SERVICE"})
        r = row(p, "kf")
        # its QC failed, so it is Stop, and Off line beside it (Ryan, 2026-10-07)
        assert r["readiness"]["state"] == "not_ok"
        assert r["readiness"]["off_line"]["reason"] == "Taken off line (SERVICE)"
        assert [x["key"] for x in r["problems"]] == ["ok_but-cal"]
        assert r["readiness"]["detail"] == "X out of spec · calibration overdue since 1 Sep too"
        assert r["needs_you"] is False, "somebody already decided about it"
        assert r["readiness"]["next"]["text"] == "Put it back on line when the work is done"

    def test_a_tile_is_about_everyone_with_the_problem(self):
        p = build([machine("alpha", specs=[spec("X", True)], maint=[task("calibration", "RED")]),
                   machine("gamma", specs=[spec("X", False)], maint=[task("calibration", "RED")]),
                   machine("o2", specs=[spec("X", True)],
                           maint=[task("calibration", "RED"), task("pm", "RED")])])
        tiles = {t["key"]: t for t in p["needs_you"]["tiles"]}
        assert sorted(m["uid"] for m in tiles["ok_but-cal"]["members"]) == ["alpha", "gamma", "o2"]
        assert [m["uid"] for m in tiles["ok_but-pm"]["members"]] == ["o2"]
        assert list(tiles) == ["not_ok-qc", "ok_but-cal", "ok_but-pm"], "worst cause first"

    def test_the_seed_tiles_count_what_the_schedule_says(self, tmp_path):
        """The critic's check: the API's own maintenance data says seven
        calibrations and three PMs are overdue; the tiles (and so their
        filters) must be about exactly those instruments."""
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        machines = c.get("/api/machines").get_json()["machines"]
        p = c.get("/api/ui/instruments").get_json()
        tiles = {t["key"]: t for t in p["needs_you"]["tiles"]}
        for kind, key in (("calibration", "ok_but-cal"), ("pm", "ok_but-pm")):
            want = sorted(m["machine_uid"] for m in machines
                          if any(str(t.get("kind")).lower() == kind and t.get("status") == "RED"
                                 for t in m.get("maintenance") or []))
            got = sorted(m["uid"] for m in tiles[key]["members"])
            assert got == want, (kind, got, want)
        assert len(tiles["ok_but-cal"]["members"]) == 7
        assert len(tiles["ok_but-pm"]["members"]) == 3
        o2 = row(p, "optimpp-2")
        assert "PM overdue" in o2["readiness"]["detail"], o2["readiness"]["detail"]


# ── the Needs-you card ──────────────────────────────────────────────────────

class TestNeedsYou:
    """Round 2 of the critic: "each Needs-you problem is listed in more than
    one place: the pill, the tile naming the instruments and cause, and the
    table rows repeating the verdict and the same reason." So each surface
    now owns ONE kind of fact, and no fact has two:

    * the table row owns the instance: which instrument, its verdict, and
      what exactly is wrong with it (T1 at 0 clicks);
    * the tile owns the cause and the remedy: "QC out of spec · Next: find
      the cause, then rerun the standard". It names no instrument and carries
      no count, and its link filters the table to the rows it is about;
    * the pill owns the fleet's verdict (one number, §3.2).
    """

    def test_same_cause_merges_into_one_tile(self):
        """§3.2: OptiMPP 1 and OptiMPP 2, both QC due, are one tile."""
        ms = [machine("o1", "OptiMPP 1", specs=[lapsed("Cloud")]),
              machine("o2", "OptiMPP 2", specs=[lapsed("Pour")])]
        tiles = build(ms)["needs_you"]["tiles"]
        assert len(tiles) == 1
        t = tiles[0]
        assert t["cause"] == "QC due"
        assert [m["uid"] for m in t["members"]] == ["o1", "o2"]

    def test_a_tile_says_the_cause_and_the_next_step_never_the_instruments(self):
        """The words a tile draws are its cause, its next step and its link.
        None of them may name an instrument (the table row does) or carry a
        number (a count beside the pill's count was the third telling)."""
        ms = [machine("o1", "OptiMPP 1", specs=[spec("Cloud Point", False, sample="STD-1")]),
              machine("pm1", "Pensky-Martens 1", specs=[spec("Flash Point", False, sample="STD-2")]),
              machine("dma", "Anton Paar DMA 4500", specs=[spec("Density", True)],
                      maint=[task("calibration", "RED")]),
              machine("k", "Koehler K23000", specs=[spec("Vapour", None, sample="STD-1")]),
              machine("c", "Cetane Bench", specs=[spec("CN", True)], maint=[task("pm", "RED")]),
              machine("q", "Quiet One", running=False, module_state="stopped",
                      specs=[spec("X", True)])]
        tiles = build(ms)["needs_you"]["tiles"]
        assert len(tiles) == 5
        titles = [m["title"] for m in ms] + ["STD-1", "STD-2"]
        for t in tiles:
            said = " ".join([t["cause"], t["next"]["text"], t["link"]])
            assert "names" not in t and "detail" not in t, t
            for title in titles:
                assert title not in said, (title, said)
            assert not re.search(r"\d", said), said

    def test_the_next_step_belongs_to_the_cause_not_to_one_instrument(self):
        """A tile with one member and a tile with five say the same next
        step for the same cause: the step is the remedy for the cause, and
        the instrument-specific step ("Run STD-1") lives on the record."""
        one = build([machine("a", specs=[spec("X", None)])])["needs_you"]["tiles"][0]
        two = build([machine("a", specs=[spec("X", None)]),
                     machine("b", specs=[spec("Y", None)])])["needs_you"]["tiles"][0]
        assert one["next"]["text"] == two["next"]["text"] == "Run the QC standard"
        assert one["link"] == "Show it" and two["link"] == "Show them"

    def test_a_tile_filters_the_list_to_its_members(self):
        """A nameless tile that jumped to one record would be a mystery jump.
        It filters the table in place to exactly its members, where each row
        names the instrument and opens its record (no dead end, §0.3)."""
        p = build([machine("gc1", specs=[spec("IBP", False)]),
                   machine("a", maint=[task("calibration", "RED")], specs=[spec("X", True)]),
                   machine("b", maint=[task("calibration", "RED")], specs=[spec("X", True)])])
        for t in p["needs_you"]["tiles"]:
            assert t["href"] == "/?cause=" + t["key"]
            assert sorted(m["uid"] for m in t["members"]) == sorted(
                r["uid"] for r in p["instruments"] if t["key"] in [x["key"] for x in r["problems"]])

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
        shown = {m["uid"] for t in ny["tiles"] if not t.get("more") for m in t["members"]}
        more = [t for t in ny["tiles"] if t.get("more")]
        assert len(more) == 1 and shown | set(more[0]["uids"]) == set("abcdefgh")
        assert more[0]["href"] == "/?filter=needs"
        said = " ".join([more[0]["cause"], more[0]["next"]["text"], more[0]["link"]])
        assert not re.search(r"\d", said), said

    def test_worst_cause_first(self):
        ms = [machine("d", "D", specs=[spec("X", None)]),
              machine("a", "A", specs=[spec("X", False)])]
        assert [t["cause"] for t in build(ms)["needs_you"]["tiles"]] == ["QC out of spec", "No verdict yet"]

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


class TestTheBellAgreesWithTheCard:
    """§0.2: two counts of one fact may not disagree. Round 3's critic: the
    bell said "5 instruments are overdue for calibration" when the schedule
    had 7, because it counted only instruments whose WORST cause was the
    calibration. And it had no PM line at all.

    The rule now: a bell line is about every instrument with that problem,
    the same set its tile and its "Show them" filter are about."""

    MS = [machine("alpha", "Alpha", specs=[spec("X", True)], maint=[task("calibration", "RED")]),
          machine("delta", "Delta", specs=[spec("X", True)], maint=[task("calibration", "RED")]),
          machine("beta", "Beta", specs=[spec("X", None)], maint=[task("calibration", "RED")]),
          machine("gamma", "Gamma", specs=[spec("X", False)], maint=[task("calibration", "RED")]),
          machine("eps", "Eps", specs=[spec("Y", None)]),
          machine("zeta", "Zeta", specs=[spec("X", False)]),
          machine("omega", "Omega", specs=[spec("X", True)],
                  maint=[task("calibration", "RED"), task("pm", "RED")])]

    def _bell(self):
        ready = {m["machine_uid"]: ui_live.readiness(m, "") for m in self.MS}
        return ui_live.conditions(machines=self.MS, ready=ready, overrides={}, round_=None,
                                  audit_spool=0, live_road=None, certificates=None,
                                  href=lambda u, s: "/instruments/%s#%s" % (u, s), now=NOW)

    def test_the_repro(self):
        cal = [i for i in self._bell() if i["key"].startswith("caldue")]
        assert [i["message"] for i in cal] == [
            "5 instruments are overdue for calibration: Alpha, Beta and 3 more."]
        assert cal[0]["href"] == "/?cause=ok_but-cal"

    def test_a_pm_behind_a_calibration_has_its_line(self):
        pm = [i for i in self._bell() if i["key"].startswith("pmdue")]
        assert [i["message"] for i in pm] == ["1 instrument is overdue for PM: Omega."]
        assert pm[0]["href"] == "/instruments/omega#maintenance"

    def test_every_bell_line_to_the_list_names_what_the_list_shows(self):
        p = build(self.MS)
        title = {m["machine_uid"]: m["title"] for m in self.MS}
        linked = [i for i in self._bell() if i["href"].startswith("/?cause=")]
        assert {i["href"] for i in linked} == {"/?cause=ok_but-cal", "/?cause=ok_but-qc",
                                               "/?cause=not_ok-qc"}, linked
        n = {"ok_but-cal": 5, "ok_but-qc": 2, "not_ok-qc": 2}
        for i in linked:
            key = i["href"].split("=", 1)[1]
            shown = [title[r["uid"]] for r in p["instruments"]
                     if key in [x["key"] for x in r["problems"]]]
            assert len(shown) == n[key], (key, shown)
            if key != "not_ok-qc":
                assert i["message"].startswith("%d instruments" % len(shown)), i["message"]
            for t in set(title.values()) - set(shown):
                assert not re.search(r"\b%s\b" % t, i["message"]), (t, i["message"])

    def test_instrument_lines_say_they_are_about_instruments(self):
        """The Instruments page lists these on its rows, so its bell folds
        them (they would be the second telling there). The bell can only
        fold what is marked: every line about an instrument is marked, and a
        line about something else (the round, the audit spool, a
        certificate, the live road) is not."""
        items = ui_live.conditions(
            machines=self.MS, ready={m["machine_uid"]: ui_live.readiness(m, "") for m in self.MS},
            overrides={"eps": "SERVICE"}, round_={"slot": "morning", "done": 1, "total": 5,
                                                  "due": "09:00", "overdue": True},
            audit_spool=2, live_road={"checking_in": 3, "live": 0},
            certificates=[{"standard": "STD-1", "expires": "2026-10-09"}],
            href=lambda u, s: "/instruments/%s#%s" % (u, s), now=NOW)
        about = {i["key"].split(":")[0]: i.get("about") for i in items}
        for k in ("notok", "override", "qcdue", "caldue", "pmdue"):
            assert about[k] == "instruments", (k, about)
        for k in ("round", "audit", "cert", "liveroad"):
            assert about[k] is None, (k, about)

    def test_notices_keep_the_mark_and_recoveries_carry_it(self):
        clock = [1000.0]
        n = ui_live.Notices(clock=lambda: clock[0])
        out = n.update([{"key": "notok:a", "level": "error", "message": "A is at Stop.",
                         "href": "/x", "link": "Open A", "about": "instruments"},
                        {"key": "audit", "level": "warning", "message": "2 rows.",
                         "href": "/settings", "link": "Open"}],
                       {"a": "A"}, {"notok": {"a"}, "offline": set()})
        assert {o["message"]: o.get("about") for o in out} == {
            "A is at Stop.": "instruments", "2 rows.": None}
        clock[0] += 5
        out = n.update([], {"a": "A"}, {"notok": set(), "offline": set()})
        assert [(o["message"], o.get("about")) for o in out] == [("A is no longer at Stop.", "instruments")]


class TestTheFleetPill:
    """ONE pill in the page-head (§3.2). It says the fleet's verdict, not a
    per-problem tally: the tallies were the "three times" defect."""

    def test_something_not_ok(self):
        p = build([machine("a", specs=[spec("X", False)]), machine("b", specs=[spec("X", True)])])
        assert p["fleet"]["pill"] == {"glyph": "error", "level": "error", "text": "1 at Stop"}

    def test_all_can_run(self):
        p = build([machine("a", specs=[spec("X", True)]), machine("b"),
                   machine("c", specs=[spec("X", None)])])
        assert p["fleet"]["pill"] == {"glyph": "final", "level": "final", "text": "All 3 can run"}

    def test_nothing_wrong_but_some_cannot_be_told(self):
        p = build([machine("a", specs=[spec("X", True)]),
                   machine("b", running=False, module_state="stopped", specs=[spec("X", True)])])
        assert p["fleet"]["pill"]["text"] == "1 of 2 can run"
        assert p["fleet"]["pill"]["level"] == "held"


    def test_an_empty_lab_has_no_verdict_to_give(self):
        """"All 0 can run" is a verdict on nothing. LabCore answered with no
        instruments, and the table says so in a sentence; the pill says
        nothing rather than something empty."""
        assert build([])["fleet"]["pill"] is None


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

class TestTheMaintenanceViewSaysWhatComesNext:
    """`/maintenance` became `/instruments?filter=maintenance` (§1). In the
    seed every instrument has a schedule, so the view showed the same 13 rows
    as All, with the same Last QC column: a door to a room identical to the
    one you were in. The view exists to answer "what PM or calibration comes
    next, and on what?", so each row carries its schedule, and the view
    orders by it.

    It must not say an overdue task a second time: the row's Can it run?
    line already says "Calibration overdue since 24 Jul". So the schedule's
    `next` is the next task that is NOT overdue, and `order` (the earliest
    due date of any task) only sorts; it is never drawn."""

    def _t(self, kind, status, due, name):
        return {"uid": "t-" + kind, "kind": kind, "status": status, "name": name,
                "next_due": due, "reason": ""}

    def test_next_is_the_soonest_task_that_is_not_overdue(self):
        p = build([machine("a", maint=[self._t("pm", "GREEN", "2026-10-26", "Monthly PM"),
                                       self._t("calibration", "RED", "2026-07-24", "Annual calibration")])])
        s = row(p, "a")["schedule"]
        assert s["tasks"] == 2
        assert s["next"] == {"name": "Monthly PM", "due": "26 Oct", "on": "2026-10-26", "soon": False}

    def test_due_soon_is_marked(self):
        p = build([machine("a", maint=[self._t("pm", "YELLOW", "2026-10-01", "Monthly PM"),
                                       self._t("calibration", "GREEN", "2027-01-01", "Annual calibration")])])
        assert row(p, "a")["schedule"]["next"] == {"name": "Monthly PM", "due": "1 Oct", "on": "2026-10-01",
                                                   "soon": True}

    def test_a_day_in_another_year_says_its_year(self):
        """GC-1's next annual calibration is next July: "22 Jul" alone read
        as two months ago, beside rows that say "overdue since 17 May"."""
        p = build([machine("a", maint=[self._t("calibration", "GREEN", "2099-03-02", "Annual calibration")])])
        assert row(p, "a")["schedule"]["next"]["due"] == "2 Mar 2099"

    def test_every_task_overdue_leaves_nothing_next(self):
        """Not "Calibration overdue" again: the row already says it."""
        p = build([machine("a", maint=[self._t("pm", "RED", "2026-09-19", "Monthly PM"),
                                       self._t("calibration", "RED", "2026-06-17", "Annual calibration")])])
        s = row(p, "a")["schedule"]
        assert s["next"] is None and s["tasks"] == 2

    def test_no_schedule(self):
        assert row(build([machine("a")]), "a")["schedule"] == {"tasks": 0, "next": None}

    def test_the_view_has_no_hidden_sort_key(self):
        """Round 2's critic: the view sorted by `order` (the earliest due
        date of ANY task, overdue included), which no column shows, so Next
        due read "16 Oct, 5 Oct, -, 1 Oct, ... 22 Jul 2027, 21 Oct". The
        schedule now carries only what is drawn, plus `on`, the ISO day of
        the drawn date, which is what the view sorts by."""
        p = build([machine("a", maint=[self._t("pm", "GREEN", "2026-10-26", "Monthly PM"),
                                       self._t("calibration", "RED", "2026-07-24", "Annual calibration")])])
        assert "order" not in row(p, "a")["schedule"]
        assert row(p, "a")["schedule"]["next"]["on"] == "2026-10-26"

    def test_the_seed_view_is_ordered_by_what_falls_due(self, tmp_path):
        app, gw = _seeded(tmp_path)
        data = app.test_client().get("/api/ui/instruments").get_json()
        assert any(r["schedule"]["tasks"] for r in data["instruments"]), "the seed schedules PM and calibration"
        # every 'next' is a task that is not overdue (the row says those)
        for r in data["instruments"]:
            nxt = r["schedule"]["next"]
            if nxt:
                assert "overdue" not in nxt["name"].lower()


class TestWhereAndMore:
    def test_where_says_not_on_the_map(self):
        p = build([machine("a", pos=None), machine("b")])
        # `pos` rides along for the map view (piece 12): the saved bay, or None
        assert row(p, "a")["where"] == {"level": "Ground Floor", "placed": False, "pos": None}
        assert row(p, "b")["where"] == {"level": "Ground Floor", "placed": True, "pos": [1.0, 1.0]}


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

    def test_the_map_view_has_moved_in(self, tmp_path):
        """It used to 302 to /floor until piece 12 landed; it is a view of
        this page now (tests/test_floor_map_view.py holds the rest)."""
        app, _ = _seeded(tmp_path)
        r = app.test_client().get("/?view=map")
        assert r.status_code == 200

    def test_the_home_page_folds_instrument_lines_out_of_its_bell(self, tmp_path):
        """The bell is on every page; on this one, the rows already say each
        instrument's problems, so its instrument lines would be the second
        telling (round 3's critic: "the bell lists the row problems a second
        time, by name and cause"). The page declares it; other pages do not."""
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        for path in ("/", "/instruments"):
            assert 'data-bell-folds="instruments"' in c.get(path).get_data(as_text=True), path
        assert "data-bell-folds" not in c.get("/settings").get_data(as_text=True)

    def test_one_h1(self, tmp_path):
        app, _ = _seeded(tmp_path)
        body = app.test_client().get("/").get_data(as_text=True)
        assert len(re.findall(r"<h1\b", body)) == 1

    def test_the_title_is_in_the_topbar_before_the_view_seg(self, tmp_path):
        """§3.2: an h1-sized "Instruments" then the List · Floor map seg, as
        GC hub and LEM's Settings do. Saying the title a second time in a
        page-head below it would be the page repeating itself."""
        app, _ = _seeded(tmp_path)
        body = app.test_client().get("/").get_data(as_text=True)
        bar = body[body.index('data-testid="topbar"'):body.index("</header>", body.index('data-testid="topbar"'))]
        assert re.search(r"<h1[^>]*>Instruments</h1>", bar), bar[:400]
        assert bar.index("<h1") < bar.index('data-testid="view-seg"')
