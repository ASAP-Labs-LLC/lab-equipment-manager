"""The dedupe classifier, judged against a lab whose truth is known.

Transfer spec §10.5. About half of `lem_machine_log` is rows the transfer wrote
twice — a LabStation restart re-reads its whole file, a lost response re-sends
a batch, the 08-27 import re-inserted batches. Those rows inflate every QC
count, renew QC freshness with readings nobody took that day, and pad u(Rw)
with copies of the same number. They have to stop counting.

But a classifier that hides a GENUINE reading is worse than the duplicates: it
removes evidence from an ISO/IEC 17025 record, and nobody would notice. So the
bar here is asymmetric and absolute — 100 % of the simulated duplicates are
candidates, and 0 genuine rows are, including the cases built to fool a
content rule (see `dedupe_sim`). A duplicate that slips through costs a
slightly fat chart; a genuine row hidden costs the record.

The classifier only ever PROPOSES. Hiding needs an approval row per bench
(`test_dedupe_approval.py`); nothing here writes anything.
"""

import json

import pytest

import dedupe
import dedupe_sim
from dedupe_sim import DUP, GENUINE, SimLab


@pytest.fixture(scope="module")
def lab():
    return dedupe_sim.build()


@pytest.fixture(scope="module")
def result(lab):
    return dedupe.classify(dedupe.LogRow.from_dict(r) for r in lab.rows)


def _hide_candidates(result):
    return {c.log_id for c in result.candidates.values()
            if c.label in dedupe.HIDE_CANDIDATE_LABELS}


class TestTheSyntheticGroundTruth:
    def test_the_simulation_has_both_kinds_in_volume(self, lab):
        # A self-test over a handful of rows proves nothing.
        assert len(lab.ids(DUP)) >= 250
        assert len(lab.ids(GENUINE)) >= 200

    def test_every_replay_is_a_candidate(self, lab, result):
        missed = lab.ids(DUP) - _hide_candidates(result)
        assert missed == set(), [r for r in lab.rows if r["id"] in missed]

    def test_no_genuine_row_is_a_candidate(self, lab, result):
        wrong = lab.ids(GENUINE) & _hide_candidates(result)
        assert wrong == set(), [
            (r, result.candidates[r["id"]]) for r in lab.rows
            if r["id"] in wrong]

    def test_the_genuine_identical_retests_are_listed_for_review(
            self, lab, result):
        """Visible is not the same as unremarked: an identical row in a later
        small poll is LISTED as a probable duplicate, so a person can look,
        but nothing hides it."""
        probable = {c.log_id for c in result.candidates.values()
                    if c.label == "probable_duplicate"}
        assert probable, "the re-tests should be listed"
        assert probable <= lab.ids(GENUINE)


class TestEachRule:
    """The rules one at a time, at their edges."""

    def _classify(self, lab):
        return dedupe.classify(dedupe.LogRow.from_dict(r) for r in lab.rows)

    def _seed(self, lab, uid, n):
        lines = [SimLab.run_line("L%d" % k, {"v": str(k)}) for k in range(n)]
        for line in lines:
            lab.poll(uid, [line], [GENUINE])
        return lines

    def test_a_burst_is_twenty_rows(self):
        """`≥ 20 rows`: a 20-row poll whose rows were all seen before is a
        replay even when the twins are fewer than 80 % of it."""
        lab = SimLab()
        lines = self._seed(lab, "b", 4)
        new = [SimLab.run_line("N%d" % k, {"v": "x%d" % k}) for k in range(16)]
        ids = lab.poll("b", lines + new, [DUP] * 4 + [GENUINE] * 16)
        got = self._classify(lab).candidates
        assert [got[i].label for i in ids[:4]] == ["replay_duplicate"] * 4
        assert got[ids[0]].rule == "burst"
        assert all(i not in got for i in ids[4:])

    def test_nineteen_rows_with_few_twins_is_not_a_burst(self):
        lab = SimLab()
        lines = self._seed(lab, "b", 4)
        new = [SimLab.run_line("N%d" % k, {"v": "x%d" % k}) for k in range(15)]
        ids = lab.poll("b", lines + new, [GENUINE] * 19)
        got = self._classify(lab).candidates
        assert {got[i].label for i in ids[:4]} == {"probable_duplicate"}

    def test_the_majority_rule_is_eighty_percent_of_at_least_five(self):
        lab = SimLab()
        lines = self._seed(lab, "m", 4)
        # 5 rows, 4 twins = 80 %: a replay.
        a = lab.poll("m", lines + [SimLab.run_line("Z", {"v": "z"})],
                     [DUP] * 4 + [GENUINE])
        # 4 rows, all twins: under the floor of five, reviewed not hidden.
        b = lab.poll("m", lines, [GENUINE] * 4)
        got = self._classify(lab).candidates
        assert got[a[0]].label == "replay_duplicate"
        assert got[a[0]].rule == "majority"
        assert a[4] not in got
        assert {got[i].label for i in b} == {"probable_duplicate"}

    def test_a_twin_must_be_in_a_different_poll(self):
        """An earlier identical row in the SAME poll is not a replay twin: it
        is either a re-sent batch (a whole block repeated, `resend`) or
        identical lines in the file (content). Twenty-five identical lines
        with consecutive ids are one INSERT of the file's lines."""
        lab = SimLab()
        line = SimLab.run_line("A", {"v": "1"})
        ids = lab.poll("s", [line] * 25, [GENUINE] * 25)
        got = self._classify(lab).candidates
        assert not any(got.get(i) and got[i].label == "replay_duplicate"
                       for i in ids)

    def test_a_resent_batch_is_a_resend_and_points_at_its_original(self):
        lab = SimLab()
        batch = [SimLab.run_line("R%d" % k, {"v": str(k)}) for k in range(4)]
        ts = lab.tick()
        first = lab.poll("r", batch, [GENUINE] * 4, ts=ts)
        again = lab.poll("r", batch, [DUP] * 4, ts=ts)
        got = self._classify(lab).candidates
        assert [got[i].label for i in again] == ["resend"] * 4
        assert [got[i].dup_of for i in again] == first
        assert all(i not in got for i in first)

    def test_two_adjacent_identical_lines_are_content_not_a_resend(self):
        """One INSERT carries one poll's lines with consecutive ids, so two
        identical rows with ADJACENT ids came out of the file together — a
        re-test the instrument printed twice. A re-sent one-row batch can
        never be told from that, so it is left to review, not hidden."""
        lab = SimLab()
        line = SimLab.run_line("A", {"v": "1"})
        ids = lab.poll("t", [line, line], [GENUINE, GENUINE])
        assert set(ids) & set(self._classify(lab).candidates) == set()

    def test_operator_and_calibration_do_not_make_a_replay_new(self):
        lab = SimLab()
        q = [SimLab.qc_line("AF26", "S", 2.0 + k / 10, operator="ana",
                            calibration_id="c1") for k in range(5)]
        for line in q:
            lab.poll("q", [line], [GENUINE])
        restamped = [SimLab.qc_line("AF26", "S", 2.0 + k / 10, operator="zed",
                                    calibration_id="c9") for k in range(5)]
        ids = lab.poll("q", restamped, [DUP] * 5)
        got = self._classify(lab).candidates
        assert {got[i].label for i in ids} == {"replay_duplicate"}

    def test_a_different_value_is_never_a_twin(self):
        lab = SimLab()
        lines = self._seed(lab, "v", 25)
        moved = [(l[0], l[1], l[2], l[3], l[4].replace('"}', '.1"}'))
                 for l in lines]
        ids = lab.poll("v", moved, [GENUINE] * 25)
        assert set(ids) & set(self._classify(lab).candidates) == set()

    def test_import_leftovers_and_misread_ids(self):
        lab = SimLab()
        lines = [SimLab.imported_line("I%d" % k, "Flash", "60", "f.csv")
                 for k in range(3)]
        ts = "2025-01-01T00:00:00"
        lab.poll("i", lines, [GENUINE] * 3, ts=ts)
        lab.noise()
        again = lab.poll("i", lines, [DUP] * 3, ts=ts)
        bad = lab.poll("i", [SimLab.imported_line("271750", "Flash", "66.0",
                                                  "g.csv")], [DUP],
                       ts="2025-01-02T00:00:00")
        got = self._classify(lab).candidates
        assert [got[i].label for i in again] == ["import_leftover"] * 3
        assert got[bad[0]].label == "import_leftover"
        assert got[bad[0]].rule == "misread_lab_id"

    def test_a_live_reading_with_a_misread_looking_id_is_left_alone(self):
        """The three IDs are the IMPORT's misreads. A live bench printing one
        of those numbers is a different question, and not this rule's."""
        lab = SimLab()
        ids = lab.poll("x", [SimLab.run_line("271750", {"v": "1"})],
                       [GENUINE])
        assert ids[0] not in self._classify(lab).candidates

    def test_status_and_config_rows_are_never_classified(self):
        lab = SimLab()
        for _ in range(30):
            lab.poll("s", [("status_change", "", "", "", "{}")], [GENUINE],
                     ts="2026-08-01T00:00:00")
        assert self._classify(lab).candidates == {}


class TestDeterminism:
    def test_the_same_record_gives_the_same_run_id(self, lab):
        rows = [dedupe.LogRow.from_dict(r) for r in lab.rows]
        a = dedupe.classify(rows)
        b = dedupe.classify(list(reversed(rows)))   # read order is irrelevant
        for uid in a.machines():
            for label in dedupe.HIDE_CANDIDATE_LABELS:
                assert a.run_id(uid, label) == b.run_id(uid, label)

    def test_the_run_id_names_the_record_it_was_taken_over(self, lab, result):
        rid = result.run_id("era", "replay_duplicate")
        upto, digest = dedupe.parse_run_id(rid)
        assert upto == max(r["id"] for r in lab.rows)
        assert len(digest) == 64

    def test_a_row_the_classifier_cannot_parse_is_kept_whole(self):
        """A detail that is not JSON is compared as its raw text — never
        dropped, never treated as empty (two different unreadable details
        would otherwise be 'identical')."""
        lab = SimLab()
        lab.poll("p", [("run", "A", "", "", "{not json")] * 1, [GENUINE])
        lab.poll("p", [("run", "A", "", "", "{also not json")], [GENUINE])
        assert dedupe.classify(
            dedupe.LogRow.from_dict(r) for r in lab.rows).candidates == {}


def test_fingerprint_drops_only_poll_time_operator_calibration_and_provenance():
    a = dedupe.fingerprint(dedupe.LogRow(1, "m", "t1", "qc", "AF26", "S", "2",
                                         json.dumps({"in_spec": True,
                                                     "operator": "a",
                                                     "calibration_id": "c"})))
    b = dedupe.fingerprint(dedupe.LogRow(2, "m", "t2", "qc", "AF26", "S", "2",
                                         json.dumps({"in_spec": True})))
    c = dedupe.fingerprint(dedupe.LogRow(3, "m", "t2", "qc", "AF26", "S", "2",
                                         json.dumps({"in_spec": False})))
    assert a == b != c


class TestAResendIsAnInsertThatLandedTwice:
    """`resend` (§10.5, N3) is one thing: a log INSERT that landed, lost its
    response, and was sent again. The module's drain sends a poll's rows —
    ALL kinds, status changes included — in INSERTs of up to 100, in order,
    and a lost response puts the whole batch back on the front of the queue.
    So a resend has a shape, and the shape is the evidence:

    * the copy repeats a whole batch back to back in the poll's FULL id order
      (a status row between two readings is part of that order: two QC
      readings with a status change between them are not a repeated block);
    * the batch starts on a batch boundary (position 0, 100, 200 ... of the
      poll) and the copy is a separate INSERT — proven by another bench's row
      landing between the two (an id gap), by a repeated status change (a
      bench cannot go YELLOW -> GREEN twice without going back), or by a full
      100-row batch;
    * or the repeated block spans two or more samples in identical order —
      nobody re-tests a stretch of samples and reads every number the same.

    An identical repeat that has none of that evidence — one sample's lines
    printed twice by the instrument, a QC repeat — is the file's content,
    whatever its position, and goes through the twin rule like any reading.

    The first two tests are the critic's: both were hidden as resends before.
    """

    def _classify(self, lab):
        return dedupe.classify(dedupe.LogRow.from_dict(r) for r in lab.rows)

    def test_a_first_ingest_holding_one_samples_two_line_result_twice(self):
        """An archive first ingest: a sample's result is two lines, and the
        instrument printed it twice back to back. One INSERT, consecutive
        ids, mid-poll. All four rows are genuine."""
        lab = SimLab()
        before = [SimLab.run_line("A%02d" % k, {"v": str(k)})
                  for k in range(7)]
        two = [SimLab.run_line("S1", {"IBP": "150.2"}),
               SimLab.run_line("S1", {"FBP": "350.9"})]
        after = [SimLab.run_line("B%02d" % k, {"v": str(k)})
                 for k in range(30)]
        lines = before + two + two + after
        ids = lab.poll("arch", lines, [GENUINE] * len(lines))
        got = self._classify(lab).candidates
        assert [got.get(i) for i in ids[7:11]] == [None] * 4

    def test_a_qc_repeat_with_a_status_change_between(self):
        """qc, status_change, qc — the standard read the same twice and the
        bench went GREEN after the first. The repeat is genuine; the status
        row between them means this is not a block repeated back to back."""
        lab = SimLab()
        q = SimLab.qc_line("AF26", "Sulfur", 3.042)
        st = ("status_change", "", "", "",
              json.dumps({"from": "YELLOW", "to": "GREEN"}))
        ts = lab.tick()
        ids = lab.poll("ms", [q, st, q], [GENUINE] * 3, ts=ts)
        got = self._classify(lab).candidates
        assert ids[2] not in got or got[ids[2]].label not in \
            dedupe.HIDE_CANDIDATE_LABELS

    def test_a_small_poll_of_one_result_printed_twice(self):
        """[a, b, a, b] as a whole 4-row poll, one INSERT (consecutive ids):
        the same sample printed twice. Nothing proves a second INSERT."""
        lab = SimLab()
        two = [SimLab.run_line("S2", {"IBP": "151.0"}),
               SimLab.run_line("S2", {"FBP": "351.0"})]
        ids = lab.poll("sp", two + two, [GENUINE] * 4)
        got = self._classify(lab).candidates
        assert not any(i in got and got[i].label in
                       dedupe.HIDE_CANDIDATE_LABELS for i in ids)

    def test_a_repeated_status_change_proves_the_batch_was_sent_twice(self):
        """The mirror's Multitek S on 09-17: [qc, YELLOW->GREEN] four times
        in one poll with consecutive ids. A bench cannot turn GREEN from
        YELLOW four times in a row; the batch was sent four times."""
        lab = SimLab()
        q = SimLab.qc_line("AF26", "Sulfur", 3.042)
        st = ("status_change", "", "", "",
              json.dumps({"from": "YELLOW", "to": "GREEN"}))
        ts = lab.tick()
        ids = lab.poll("ms", [q, st] * 4,
                       [GENUINE, GENUINE] + [DUP, GENUINE] * 3, ts=ts)
        got = self._classify(lab).candidates
        assert ids[0] not in got
        assert [got[i].label for i in (ids[2], ids[4], ids[6])] == \
            ["resend"] * 3
        assert {got[i].dup_of for i in (ids[2], ids[4], ids[6])} == {ids[0]}

    def test_a_one_row_batch_resent_after_another_benchs_insert(self):
        lab = SimLab()
        one = SimLab.run_line("R1", {"v": "5.5"})
        ts = lab.tick()
        a = lab.poll("r1", [one], [GENUINE], ts=ts)
        lab.noise()
        b = lab.poll("r1", [one], [DUP], ts=ts)
        got = self._classify(lab).candidates
        assert a[0] not in got and got[b[0]].label == "resend"

    def test_a_short_batch_resent_mid_poll_is_not_on_a_boundary(self):
        """An id gap alone is not enough: a resent batch starts where a batch
        starts. Two identical one-sample lines at position 7 of a poll, with
        another bench's row between them, are not the shape of a batch the
        drain sent (its batches start at 0, 100, 200 ...)."""
        lab = SimLab()
        ts = lab.tick()
        head = [SimLab.run_line("H%d" % k, {"v": str(k)}) for k in range(7)]
        line = SimLab.run_line("S3", {"v": "7.7"})
        ids = lab.poll("mid", head + [line], [GENUINE] * 8, ts=ts)
        lab.noise()
        more = lab.poll("mid", [line], [GENUINE], ts=ts)
        got = self._classify(lab).candidates
        assert more[0] not in got or got[more[0]].label not in \
            dedupe.HIDE_CANDIDATE_LABELS

    def test_a_stretch_of_samples_repeated_in_order_is_a_copy_anywhere(self):
        """The mirror's Agilent GC 1 on 09-11: 32 rows — 25 samples and the
        AF26 QC set — then the same 32 again, consecutive ids, mid-file.
        Nobody re-tests 25 samples and reads every number the same."""
        lab = SimLab()
        lead = [SimLab.run_line("P%d" % k, {"v": "p%d" % k}) for k in range(3)]
        block = [SimLab.run_line("39%03d" % k, {"IBP": "15%d.%d" % (k, k)})
                 for k in range(25)]
        ids = lab.poll("gc1", lead + block + block,
                       [GENUINE] * 28 + [DUP] * 25)
        got = self._classify(lab).candidates
        assert all(i not in got for i in ids[:28])
        assert [got[i].label for i in ids[28:]] == ["resend"] * 25
        assert [got[i].dup_of for i in ids[28:]] == ids[3:28]

    def test_a_full_batch_of_one_hundred_sent_twice(self):
        lab = SimLab()
        rows = [SimLab.run_line("F%03d" % k, {"v": str(k % 3)})
                for k in range(100)]
        ids = lab.poll("full", rows + rows, [GENUINE] * 100 + [DUP] * 100)
        got = self._classify(lab).candidates
        assert [got[i].label for i in ids[100:]] == ["resend"] * 100
