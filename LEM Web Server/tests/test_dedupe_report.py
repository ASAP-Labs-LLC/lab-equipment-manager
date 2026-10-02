"""What Ryan reads before he approves anything: the dry run and its QC impact.

Spec §10.5 steps 1 and 2. The approval (D7) is only as good as what it was
shown, so the report has to answer the two questions an approver actually
has:

* WHAT would go — per bench, per kind, per label, with twenty concrete rows
  each beside the earlier row it duplicates, so "replay" is something he can
  check against a printout rather than take on trust;
* WHAT IT DOES TO QC — every (machine, test, standard) series whose last
  verdict moves earlier once replays stop counting (a replayed QC print
  restamped "today" has been renewing freshness nobody earned, and hiding it
  can turn a bench YELLOW), and every u(Rw) spread that changes (copies of
  one reading shrink `s` and inflate `n`).

Nothing in this file writes; the report is computed from a read.
"""

import json

import pytest

import dedupe
import dedupe_sim
from dedupe_sim import DUP, GENUINE, SimLab


def _report(lab, **kw):
    return dedupe.report(dedupe.classify(
        dedupe.LogRow.from_dict(r) for r in lab.rows), **kw)


@pytest.fixture(scope="module")
def full():
    lab = dedupe_sim.build()
    return lab, _report(lab, now="2026-08-10T00:00:00")


class TestTheDryRun:
    def test_per_bench_per_kind_per_label(self, full):
        lab, rep = full
        era = next(b for b in rep["benches"] if b["machine_uid"] == "era")
        assert era["by_kind"]["run"]["replay_duplicate"] > 0
        assert era["by_kind"]["run"]["probable_duplicate"] >= 1
        gc = next(b for b in rep["benches"] if b["machine_uid"] == "gc")
        assert gc["by_kind"]["qc"]["replay_duplicate"] == 6
        # the counts add up to the candidate list, and to the truth
        total = sum(b["candidates"].get(l, 0) for b in rep["benches"]
                    for l in dedupe.HIDE_CANDIDATE_LABELS)
        assert total == rep["hide_candidates"] == len(lab.ids(DUP))

    def test_twenty_examples_per_bench_each_beside_its_original(self, full):
        lab, rep = full
        by_id = {r["id"]: r for r in lab.rows}
        for bench in rep["benches"]:
            n = sum(bench["candidates"].values())
            assert len(bench["examples"]) == min(20, n), bench["machine_uid"]
            for ex in bench["examples"]:
                row = by_id[ex["id"]]
                assert ex["ts"] == row["ts"] and ex["lab_id"] == row["lab_id"]
                if ex["label"] != "import_leftover" or ex["dup_of"]:
                    orig = by_id[ex["dup_of"]]
                    assert ex["original"]["ts"] == orig["ts"]
                    assert dedupe.fingerprint(dedupe.LogRow.from_dict(row)) \
                        == dedupe.fingerprint(dedupe.LogRow.from_dict(orig))

    def test_examples_cover_every_label_the_bench_has(self, full):
        _lab, rep = full
        for bench in rep["benches"]:
            labels = {l for l, n in bench["candidates"].items() if n}
            shown = {e["label"] for e in bench["examples"]}
            assert shown == labels, bench["machine_uid"]

    def test_the_window_count_beside_the_burst_proxy(self):
        """G1 measured replays by a proxy — rows in polls of 20 or more —
        and the dry run reports both side by side for a window, so the
        prediction can be checked rather than restated."""
        lab = SimLab()
        lines = [SimLab.run_line("L40%03d" % k, {"v": str(k)}) for k in range(25)]
        lab.poll("w", lines, [GENUINE] * 25, ts="2026-08-31T10:00:00")
        lab.poll("w", lines, [DUP] * 25, ts="2026-09-02T10:00:00")
        rep = _report(lab, since="2026-09-01")
        assert rep["benches"][0]["bursts"] == {
            "polls": 2, "rows": 50, "hide_candidates": 25,
            "not_hidden": {"new_lab_id": 25}}
        w = rep["benches"][0]["since"]
        assert w["from"] == "2026-09-01"
        assert w["run_rows"] == 25
        assert w["run_rows_in_polls_of_20_or_more"] == 25
        assert w["run_hide_candidates"] == 25

    def test_storm_days_are_called_out(self):
        """O9: the Agilent had 88 replay polls on 08-07 and 363 on 08-18.
        Days like that are listed on their own, so they are reviewed as
        events rather than approved as part of a total."""
        lab = SimLab()
        lines = [SimLab.run_line("L40%03d" % k, {"v": str(k)}) for k in range(6)]
        for line in lines:
            lab.poll("s", [line], [GENUINE], ts="2026-08-17T09:00:00")
        for k in range(12):
            lab.poll("s", lines, [DUP] * 6,
                     ts="2026-08-18T09:%02d:00" % k)
        rep = _report(lab)
        storms = rep["benches"][0]["storms"]
        assert storms == [{"day": "2026-08-18", "polls": 12,
                           "replay_polls": 12, "rows": 72,
                           "hide_candidates": 72, "not_hidden": {},
                           "unit_suffix": "@storm:2026-08-18"}]

    def test_a_storm_of_bursts_that_are_not_copies_is_listed_with_why(self):
        """08-07 on the Agilent: 88 bursts, almost none of them copies.
        Listed all the same, with what the rows are instead: results for
        Lab IDs already logged with other values, readings whose Lab ID was
        last seen in the import's per-test shape, and new Lab IDs."""
        lab = SimLab()
        lab.poll("s", [SimLab.imported_line("I0", "IBP", "150", "f.csv")],
                 [GENUINE], ts="2026-08-01T00:00:00")
        lab.poll("s", [SimLab.run_line("L40000", {"v": "1"})], [GENUINE],
                 ts="2026-08-02T00:00:00")
        for k in range(10):
            lines = ([SimLab.run_line("I0", {"IBP": "150.%d" % k}),
                      SimLab.run_line("L40000", {"v": "2.%d" % k})] +
                     [SimLab.run_line("N%d_%d" % (k, j), {"v": "x"})
                      for j in range(18)])
            lab.poll("s", lines, [GENUINE] * 20,
                     ts="2026-08-07T09:%02d:00" % k)
        [storm] = _report(lab)["benches"][0]["storms"]
        assert storm["polls"] == 10 and storm["replay_polls"] == 0
        assert storm["hide_candidates"] == 0
        assert storm["not_hidden"] == {"import_shape": 1, "other_values": 19,
                                       "new_lab_id": 180}

    def test_the_report_is_plain_json(self, full):
        _lab, rep = full
        assert json.loads(json.dumps(rep)) == rep


class TestQcImpact:
    def _qc(self, value, operator="ana"):
        return SimLab.qc_line("AF26", "Sulfur", value, operator=operator,
                              calibration_id="c1")

    def test_a_replayed_qc_print_that_was_renewing_freshness(self):
        """The bench's last real QC was on the 1st; a restart replayed it on
        the 9th with the 9th's timestamp. Today it reads as QC'd on the 9th.
        Hidden, the last verdict moves back to the 1st — Ryan sees that
        before he approves."""
        lab = SimLab()
        lines = [SimLab.run_line("L40%03d" % k, {"v": str(k)}) for k in range(25)]
        lab.poll("g", lines + [self._qc(2.1)], [GENUINE] * 26,
                 ts="2026-08-01T09:00:00")
        lab.poll("g", lines + [self._qc(2.1, operator="zed")], [DUP] * 26,
                 ts="2026-08-09T09:00:00")
        rep = _report(lab, now="2026-08-10T09:00:00")
        [s] = rep["qc_impact"]
        assert (s["machine_uid"], s["test_name"], s["standard"]) == \
            ("g", "Sulfur", "AF26")
        assert s["last_before"] == "2026-08-09T09:00:00"
        assert s["last_after"] == "2026-08-01T09:00:00"
        assert s["last_moves"] is True
        assert s["age_hours_after"] == pytest.approx(9 * 24)
        assert s["n_before"] == 2 and s["n_after"] == 1

    def test_a_spread_padded_with_copies_is_reported(self):
        lab = SimLab()
        vals = [2.0, 2.2, 1.9, 2.1]
        for v in vals:
            lab.poll("u", [self._qc(v)], [GENUINE])
        # replay: all four QC again plus 20 run lines, in one burst
        runs = [SimLab.run_line("L40%03d" % k, {"v": str(k)}) for k in range(20)]
        lab.poll("u", runs, [GENUINE] * 20, ts="2026-08-01T00:00:01")
        lab.poll("u", [self._qc(v) for v in vals] + runs, [DUP] * 24)
        [s] = _report(lab)["qc_impact"]
        assert s["n_before"] == 8 and s["n_after"] == 4
        assert s["s_before"] != pytest.approx(s["s_after"])
        assert s["spread_changes"] is True

    def test_series_no_replay_touches_are_not_listed(self):
        lab = SimLab()
        for v in (2.0, 2.1, 2.2):
            lab.poll("c", [self._qc(v)], [GENUINE])
        assert _report(lab)["qc_impact"] == []

    def test_a_probable_duplicate_counts_as_it_does_today(self):
        """A QC repeat stays in the record, so it stays in the impact
        calculation: the 'after' side is the effective record after the
        HIDING labels, and nothing else."""
        lab = SimLab()
        lab.poll("p", [self._qc(2.0)], [GENUINE])
        lab.poll("p", [self._qc(2.0)], [GENUINE])     # repeat, visible
        assert _report(lab)["qc_impact"] == []


class TestStormsGoToReviewOnTheirOwn:
    """§10.5: "the 08-07 and 08-18 Agilent storms ... go to review rather
    than being auto-trusted". A storm day's candidates are therefore their
    OWN approval unit (`replay_duplicate@storm:2026-08-18`): approving a
    bench's replays approves the ordinary restarts, and the storm stays
    visible until somebody has looked at that day by itself."""

    def _lab(self):
        lab = SimLab()
        lines = [SimLab.run_line("L40%03d" % k, {"v": str(k)}) for k in range(25)]
        lab.poll("s", lines, [GENUINE] * 25, ts="2026-08-17T09:00:00")
        storm = []
        for k in range(12):
            storm += lab.poll("s", lines, [DUP] * 25,
                              ts="2026-08-18T09:%02d:00" % k)
        calm = lab.poll("s", lines, [DUP] * 25, ts="2026-08-20T09:00:00")
        return lab, storm, calm

    def test_a_storm_days_rows_are_a_separate_unit(self):
        lab, storm, calm = self._lab()
        result = dedupe.classify(dedupe.LogRow.from_dict(r) for r in lab.rows)
        rep = dedupe.report(result)
        [bench] = rep["benches"]
        assert set(bench["run_ids"]) == {
            "replay_duplicate", "replay_duplicate@storm:2026-08-18"}
        assert result.ids("s", "replay_duplicate") == calm
        assert result.ids("s", "replay_duplicate@storm:2026-08-18") == storm
        units = {u["rule"]: u for u in bench["units"]}
        assert units["replay_duplicate@storm:2026-08-18"]["candidates"] == 300
        assert units["replay_duplicate@storm:2026-08-18"]["storm"] is True
        assert units["replay_duplicate"]["storm"] is False
        # the label totals are unchanged: a storm is a unit, not a new label
        assert bench["candidates"] == {"replay_duplicate": 325}

    def test_the_imports_history_days_are_not_storms(self):
        """The 08-27 import stamps one ts per parsed run, so a historical day
        holds many of its 'polls'. That is the import's shape, not a bench
        replaying, and it is not a storm."""
        lab = SimLab()
        for k in range(12):
            lab.poll("i", [SimLab.imported_line("I%d_%d" % (k, j), "T", "1",
                                                "f.csv") for j in range(20)],
                     [GENUINE] * 20, ts="2025-01-13T09:%02d:00" % k)
        rep = _report(lab)
        assert all(b["storms"] == [] for b in rep["benches"])


class TestThePredictionIsChecked:
    """O9's prediction (§10.5) was made from G1's PROXY — rows in polls of
    20 or more — before anybody had compared the rows' content. The dry run
    checks it against the one bound content can give: a row is a duplicate
    under §10.5 only if an identical earlier row exists on its bench, so the
    rows that HAVE one are the most any rule faithful to §10.5 could ever
    propose (the "ceiling"). Where the ceiling is below the predicted floor,
    the prediction is unreachable by construction — and the report says so
    and lists the burst rows that are the record's ONLY copy of their
    reading, which is what reaching it would cost."""

    def _lab(self):
        lab = SimLab()
        first = [SimLab.run_line("F44%03d" % k, {"v": "f%d" % k})
                 for k in range(25)]
        lab.poll("b", first, [GENUINE] * 25, ts="2026-09-02T09:00:00")
        lab.poll("b", first, [DUP] * 25, ts="2026-09-03T09:00:00")
        # a second archive arrives whole and is never replayed: these rows
        # are the record's only copy of their readings
        other = [SimLab.run_line("G45%03d" % k, {"v": "g%d" % k})
                 for k in range(25)]
        lab.poll("b", other, [GENUINE] * 25, ts="2026-09-04T09:00:00")
        return lab

    def test_a_bench_whose_bursts_are_half_first_ingest(self):
        lab = self._lab()
        result = dedupe.classify(dedupe.LogRow.from_dict(r) for r in lab.rows)
        chk = dedupe.prediction_check(result, {
            "since": "2026-09-01", "total": [40, 60], "band": 0.10,
            "benches": {"b": {"name": "Bench B", "burst_rows": 75}}})
        [b] = chk["benches"]
        assert b["burst_rows_measured"] == 75
        assert b["predicted"] == 75
        assert b["band"] == [pytest.approx(67.5), pytest.approx(82.5)]
        assert b["proposed"] == 25 and b["ceiling"] == 25
        # no rule faithful to §10.5 can propose the 50 with no earlier twin
        assert b["no_earlier_twin"] == 50
        assert b["within_band"] is False and b["reachable"] is False
        # and 25 of them exist nowhere else in the record at all
        assert b["only_copy_rows"] == 25
        assert len(b["only_copy_examples"]) == 20
        assert chk["total"]["ceiling"] == 25
        assert chk["total"]["reachable"] is False

    def test_the_ceiling_bounds_every_candidate(self):
        """Every hide candidate except the import's three misread IDs points
        at an identical earlier row, so the proposal can never exceed the
        ceiling — on the full synthetic lab, and by construction."""
        lab = dedupe_sim.build()
        result = dedupe.classify(dedupe.LogRow.from_dict(r) for r in lab.rows)
        chk = dedupe.prediction_check(result, {
            "since": "2026-01-01", "total": [1, 2], "band": 0.10,
            "benches": {}})
        hide = [c for c in result.candidates.values()
                if c.label in dedupe.HIDE_CANDIDATE_LABELS
                and c.rule != "misread_lab_id"]
        assert len(hide) <= chk["total"]["ceiling"]
        assert chk["total"]["proposed_hide"] == len(hide) + sum(
            1 for c in result.candidates.values()
            if c.rule == "misread_lab_id")


class TestO9IsClosedByArithmeticNotByExplanation:
    """Round-2 critic: the dry run proposes ~64k against a 110k–140k
    prediction and GC 2 sits 40 % under G1, so O9 was "not met". The open
    item itself (gaps.md O9) asks for "the exact count of re-emitted rows"
    because "G1 gives only a burst-based bound". Closing it means three
    things the report must COMPUTE, not argue:

    1. what the prediction measured — the burst proxy, row for row — and
       where every one of those rows goes: proposed copy, listed for
       review, or the first copy of its reading;
    2. what reaching the predicted floor would cost: hiding more rows than
       have an earlier identical copy removes at least (hidden − ceiling)
       readings from what anybody sees, because visible rows can then no
       longer cover every distinct reading;
    3. whether the claim the prediction was scaled from (ASK-CLAUDE.md,
       3 Sep: ~99,000 of 220,841) was ever reachable in the record as it
       stood THEN — measured on the rows up to that id, not assumed."""

    def _lab(self):
        return TestThePredictionIsChecked()._lab()

    def _check(self, lab, **pred):
        base = {"since": "2026-09-01", "total": [40, 60], "band": 0.10,
                "benches": {"b": {"name": "Bench B", "burst_rows": 75}}}
        base.update(pred)
        result = dedupe.classify(dedupe.LogRow.from_dict(r) for r in lab.rows)
        return dedupe.prediction_check(result, base)

    def test_every_burst_row_is_accounted_for(self):
        chk = self._check(self._lab())
        [b] = chk["benches"]
        acc = b["burst_rows_accounted"]
        assert acc == {"hide_candidates": 25, "listed_for_review": 0,
                       "first_copy_of_its_reading": 50}
        assert sum(acc.values()) == b["burst_rows_measured"] == 75
        tot = chk["total"]["burst_rows_accounted"]
        assert sum(tot.values()) == chk["total"]["rows_in_polls_of_20_or_more"]

    def test_reaching_the_floor_would_erase_readings(self):
        chk = self._check(self._lab())
        [b] = chk["benches"]
        # band floor 67.5 -> at least 68 hidden; only 25 rows have an
        # earlier copy, so 43 readings would vanish from every view.
        assert b["hide_needed_for_band"] == 68
        assert b["readings_erased_at_band_floor"] == 43
        t = chk["total"]
        assert t["readings_erased_at_floor"] == 40 - 25

    def test_a_reachable_band_erases_nothing(self):
        chk = self._check(self._lab(), total=[10, 30], benches={
            "b": {"name": "Bench B", "burst_rows": 25}})
        [b] = chk["benches"]
        assert b["readings_erased_at_band_floor"] == 0
        assert chk["total"]["readings_erased_at_floor"] == 0

    def test_the_prediction_is_tested_against_the_proxy_it_came_from(self):
        chk = self._check(self._lab(), total=[70, 80])
        assert chk["total"]["rows_in_polls_of_20_or_more"] == 75
        assert chk["total"]["proxy_within_predicted"] is True
        assert self._check(self._lab())["total"][
            "proxy_within_predicted"] is False

    def test_an_earlier_claim_is_measured_on_the_record_as_it_stood(self):
        lab = self._lab()
        first_poll_last_id = 25        # ids 1..25: the first archive only
        chk = self._check(lab, earlier_claim={
            "source": "a note", "upto_id": 50, "claimed": 40,
            "benches": {"b": 40}})
        e = chk["earlier_claim"]
        # up to id 50 the record held the first archive and one replay of
        # it: 25 rows with an earlier identical copy, never 40
        assert e["run_qc_rows_then"] == 50
        assert e["ceiling_then"] == 25
        assert e["benches"] == [{"machine_uid": "b", "name": "Bench B",
                                 "claimed": 40, "ceiling_then": 25,
                                 "reachable": False}]
        assert e["reachable"] is False
        chk = self._check(lab, earlier_claim={
            "upto_id": first_poll_last_id, "claimed": 0, "benches": {}})
        assert chk["earlier_claim"]["ceiling_then"] == 0

    def test_the_cost_is_counted_from_the_ceiling_not_the_proposal(self):
        """A twin listed for review is still a row a looser rule COULD hide
        without erasing its reading: the cost of the floor is measured from
        the ceiling (every row with an earlier copy), not from what the
        rules propose."""
        lab = self._lab()
        again = SimLab.run_line("G45003", {"v": "g3"})       # a lone re-test
        [rid] = lab.poll("b", [again], [GENUINE], ts="2026-09-05T09:00:00")
        chk = self._check(lab)
        [b] = chk["benches"]
        assert b["proposed"] == 25 and b["ceiling"] == 26
        assert b["readings_erased_at_band_floor"] == 68 - 26
        assert chk["total"]["readings_erased_at_floor"] == 40 - 26

    def test_a_twin_the_rules_left_unlabelled_is_never_called_an_original(
            self):
        """The accounting is a check on the rules, not a restatement of
        them: a row with an earlier identical copy that carries no label at
        all is counted on its own line, never folded into "first copy"."""
        result = dedupe.classify(
            dedupe.LogRow.from_dict(r) for r in self._lab().rows)
        dropped = min(i for i, c in result.candidates.items()
                      if c.label == "replay_duplicate")
        del result.candidates[dropped]
        chk = dedupe.prediction_check(result, {
            "since": "2026-09-01", "total": [40, 60], "band": 0.10,
            "benches": {"b": {"name": "Bench B", "burst_rows": 75}}})
        assert chk["benches"][0]["burst_rows_accounted"] == {
            "hide_candidates": 24, "listed_for_review": 0,
            "first_copy_of_its_reading": 50, "unlabelled_twin": 1}


class TestTheBandIsJudgedWithoutOurKey:
    """Round-3 critic: "the builder replaced the requirement with the burst
    proxy it was predicted from"; every bound so far used §10.5's own
    fingerprint, so a reader could still ask whether a looser idea of a
    duplicate reaches the band. This bound uses no fingerprint at all.

    A bench can only send AGAIN what it has sent before. So a row whose Lab
    ID its bench never logged in an earlier poll is a first appearance under
    ANY definition of a re-emission — exact copy, re-processed result,
    re-test, anything keyed on the sample. Counting every other row as
    re-emitted (re-tests and re-integrations included, which is far too
    generous) is the most any definition can reach. Where even that is under
    the band's floor, the band is not reachable by a looser rule, only by a
    revised prediction; and the report names the first appearances, which
    are what hiding the difference would erase."""

    def _lab(self):
        lab = SimLab()
        first = [SimLab.run_line("F44%03d" % k, {"v": "f%d" % k})
                 for k in range(25)]
        lab.poll("b", first, [GENUINE] * 25, ts="2026-09-02T09:00:00")
        # the same samples re-processed: every number moved, a re-emission
        # under a loose definition, not a copy under §10.5
        again = [SimLab.run_line("F44%03d" % k, {"v": "r%d" % k})
                 for k in range(25)]
        lab.poll("b", again, [GENUINE] * 25, ts="2026-09-03T09:00:00")
        other = [SimLab.run_line("G45%03d" % k, {"v": "g%d" % k})
                 for k in range(25)]
        lab.poll("b", other, [GENUINE] * 25, ts="2026-09-04T09:00:00")
        return lab

    def _check(self, lab, burst_rows):
        result = dedupe.classify(dedupe.LogRow.from_dict(r) for r in lab.rows)
        return dedupe.prediction_check(result, {
            "since": "2026-09-01", "total": [40, 60], "band": 0.10,
            "benches": {"b": {"name": "Bench B", "burst_rows": burst_rows}}})

    def test_first_appearances_bound_every_definition(self):
        chk = self._check(self._lab(), 75)
        [b] = chk["benches"]
        assert b["ceiling"] == 0                  # nothing is a copy
        assert b["any_definition_ceiling"] == 25  # the re-processed rows
        assert b["first_appearances"] == 50
        assert b["reachable_by_any_definition"] is False
        shown = b["first_appearance_examples"]          # spread, at most 20
        assert len(shown) == 20 and shown[0]["lab_id"] == "F44000"
        assert {e["ts"][:10] for e in shown} == {"2026-09-02", "2026-09-04"}
        t = chk["total"]
        assert t["any_definition_ceiling"] == 25
        assert t["reachable_by_any_definition"] is False

    def test_a_band_a_loose_definition_could_reach_says_so(self):
        """The bound does not pretend: where re-processed rows would fill
        the band, it says the band is reachable by SOME definition — and the
        §10.5 ceiling still says what a faithful rule can do."""
        chk = self._check(self._lab(), 25)
        [b] = chk["benches"]
        assert b["reachable_by_any_definition"] is True
        assert b["reachable"] is False
