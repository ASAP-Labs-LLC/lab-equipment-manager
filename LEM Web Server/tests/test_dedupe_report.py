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
        lines = [SimLab.run_line("L%d" % k, {"v": str(k)}) for k in range(25)]
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
        lines = [SimLab.run_line("L%d" % k, {"v": str(k)}) for k in range(6)]
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
        lab.poll("s", [SimLab.run_line("L0", {"v": "1"})], [GENUINE],
                 ts="2026-08-02T00:00:00")
        for k in range(10):
            lines = ([SimLab.run_line("I0", {"IBP": "150.%d" % k}),
                      SimLab.run_line("L0", {"v": "2.%d" % k})] +
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
        lines = [SimLab.run_line("L%d" % k, {"v": str(k)}) for k in range(25)]
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
        runs = [SimLab.run_line("L%d" % k, {"v": str(k)}) for k in range(20)]
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
        lines = [SimLab.run_line("L%d" % k, {"v": str(k)}) for k in range(25)]
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
        first = [SimLab.run_line("F%d" % k, {"v": "f%d" % k})
                 for k in range(25)]
        lab.poll("b", first, [GENUINE] * 25, ts="2026-09-02T09:00:00")
        lab.poll("b", first, [DUP] * 25, ts="2026-09-03T09:00:00")
        # a second archive arrives whole and is never replayed: these rows
        # are the record's only copy of their readings
        other = [SimLab.run_line("G%d" % k, {"v": "g%d" % k})
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
