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
from dedupe_sim import DUP, GENUINE, LISTED, SimLab


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
        assert probable <= lab.ids(GENUINE) | lab.ids(LISTED)

    def test_a_copy_no_rule_can_split_from_a_retest_is_listed(
            self, lab, result):
        """`LISTED` rows are real copies whose rows are, row for row, what a
        genuine re-test writes (a two-line re-read beside a pair re-test).
        They are not hide candidates — that would hide the re-test too —
        but every one of them is LISTED beside the row it copies, so it is
        marked, visible, and one approval away from a person's decision."""
        listed = lab.ids(LISTED)
        assert listed
        for i in listed:
            c = result.candidates.get(i)
            assert c is not None and c.label == "probable_duplicate", i
            assert c.dup_of is not None


class TestEachRule:
    """The rules one at a time, at their edges."""

    def _classify(self, lab):
        return dedupe.classify(dedupe.LogRow.from_dict(r) for r in lab.rows)

    def _seed(self, lab, uid, n):
        lines = [SimLab.run_line("L40%03d" % k, {"v": str(k)}) for k in range(n)]
        for line in lines:
            lab.poll(uid, [line], [GENUINE])
        return lines

    def test_a_burst_is_twenty_rows(self):
        """`≥ 20 rows`: a 20-row poll whose rows were all seen before is a
        replay even when the twins are fewer than 80 % of it — when they
        are a stretch of five or more samples (`MIN_COPY_SAMPLES`)."""
        lab = SimLab()
        lines = self._seed(lab, "b", 5)
        new = [SimLab.run_line("N41%03d" % k, {"v": "x%d" % k}) for k in range(15)]
        ids = lab.poll("b", lines + new, [DUP] * 5 + [GENUINE] * 15)
        got = self._classify(lab).candidates
        assert [got[i].label for i in ids[:5]] == ["replay_duplicate"] * 5
        assert got[ids[0]].rule == "burst"
        assert all(i not in got for i in ids[5:])

    def test_nineteen_rows_with_few_twins_is_not_a_burst(self):
        lab = SimLab()
        lines = self._seed(lab, "b", 4)
        new = [SimLab.run_line("N41%03d" % k, {"v": "x%d" % k}) for k in range(15)]
        ids = lab.poll("b", lines + new, [GENUINE] * 19)
        got = self._classify(lab).candidates
        assert {got[i].label for i in ids[:4]} == {"probable_duplicate"}

    def test_the_majority_rule_is_eighty_percent_and_five_samples(self):
        """80 % of the poll's rows are twins AND at least five of the twins
        are sample readings. The five is the evidence floor both paths
        share (`MIN_COPY_SAMPLES`): four samples re-tested in one go, every
        number the same, is something a lab does; it is listed, not hidden.
        """
        lab = SimLab()
        lines = self._seed(lab, "m", 5)
        # 6 rows, 5 sample twins = 83 %: a replay.
        a = lab.poll("m", lines + [SimLab.run_line("Z", {"v": "z"})],
                     [DUP] * 5 + [GENUINE])
        # 5 rows, 4 sample twins = 80 %, but only four samples: reviewed.
        b = lab.poll("m", lines[:4] + [SimLab.run_line("Y", {"v": "y"})],
                     [GENUINE] * 5)
        # 4 rows, all twins: under the floor of five rows, reviewed.
        c = lab.poll("m", lines[:4], [GENUINE] * 4)
        got = self._classify(lab).candidates
        assert {got[i].label for i in a[:5]} == {"replay_duplicate"}
        assert got[a[0]].rule == "majority"
        assert a[5] not in got
        assert [(got[i].label, got[i].rule) for i in b[:4]] == [
            ("probable_duplicate", "fewer_than_five_samples")] * 4
        assert b[4] not in got
        assert {got[i].label for i in c} == {"probable_duplicate"}

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
        """Five samples, back to back, twice, consecutive ids: the floor a
        block needs when nothing else (an id gap, a status change, a full
        batch of 100) proves a second INSERT."""
        lab = SimLab()
        batch = [SimLab.run_line("R42%03d" % k, {"v": str(k)}) for k in range(5)]
        ts = lab.tick()
        first = lab.poll("r", batch, [GENUINE] * 5, ts=ts)
        again = lab.poll("r", batch, [DUP] * 5, ts=ts)
        got = self._classify(lab).candidates
        assert [got[i].label for i in again] == ["resend"] * 5
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
        got = self._classify(lab).candidates
        assert ids[0] not in got
        assert (got[ids[1]].label, got[ids[1]].rule) == \
            ("probable_duplicate", "repeat_in_poll")      # listed, visible

    def test_operator_and_calibration_do_not_make_a_replay_new(self):
        """A replayed QC print comes back stamped with whoever is signed in
        and today's calibration; it is still the same reading. (The replay
        holds five samples too: QC rows alone are never proposed, see
        `TestAStandardIsNotASample` and `TestARetestOfAFewSamplesIsNotACopy`.)
        """
        lab = SimLab()
        q = [SimLab.qc_line("AF26", "S", 2.0 + k / 10, operator="ana",
                            calibration_id="c1") for k in range(5)]
        s = [SimLab.run_line("4170%d" % k, {"S": "1%d.2" % k})
             for k in range(5)]
        for line in q + s:
            lab.poll("q", [line], [GENUINE])
        restamped = [SimLab.qc_line("AF26", "S", 2.0 + k / 10, operator="zed",
                                    calibration_id="c9") for k in range(5)]
        ids = lab.poll("q", restamped + s, [DUP] * 10)
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
        assert [got.get(i) for i in ids[7:9]] == [None] * 2
        # the second print is listed for review, and stays visible
        assert {got[i].label for i in ids[9:11]} == {"probable_duplicate"}

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

    def test_an_unproven_repeat_in_one_poll_is_listed_for_review(self):
        """Visible is not unremarked. One sample's lines printed twice (or a
        one-row batch re-sent with nothing landing between: the two cannot
        be told apart) stay in the record, and are LISTED as probable
        duplicates beside the row they repeat, so a person can look."""
        lab = SimLab()
        two = [SimLab.run_line("S9", {"IBP": "150.2"}),
               SimLab.run_line("S9", {"FBP": "350.9"})]
        ids = lab.poll("pp", two + two, [GENUINE] * 4)
        got = self._classify(lab).candidates
        assert ids[0] not in got and ids[1] not in got
        assert [(got[i].label, got[i].rule, got[i].dup_of)
                for i in ids[2:]] == [
            ("probable_duplicate", "repeat_in_poll", ids[0]),
            ("probable_duplicate", "repeat_in_poll", ids[1])]


class TestACatchUpPollIsNotAReplay:
    """Round-2 critic: "≥ 20 rows" alone treated EVERY twin inside a big
    poll as a copy. A bench that was offline overnight catches up with one
    poll of 24 genuinely new rows; if that poll holds the morning QC check
    reading the same value as yesterday's, the rule proposed that QC repeat
    for hiding. On the mirror the same shape is Multitek NS's 08-31 13:26
    poll: 30 brand-new Lab IDs, and one Blank (row 216039) proposed as a
    replay of a Blank from 08-07.

    What a replay leaves behind is not "a big poll" but a STRETCH: a restart
    re-reads a run of the file, so its copies arrive as consecutive rows
    spanning two or more samples, in the order the file holds them. A lone
    twin among new rows is exactly what a genuine repeat looks like, and a
    QC standard is measured every day by design, so QC rows alone are never
    a stretch. Inside a big poll that is not mostly twins (the 80 % rule
    still covers whole-poll replays), a twin outside a stretch is LISTED as
    a probable duplicate — visible — and never proposed for hiding."""

    def _yesterday(self, lab, uid):
        qc = SimLab.qc_line("AF26", "Water", 2.5)
        lab.poll(uid, [qc], [GENUINE])
        s1 = SimLab.run_line("S1", {"Water": "12.1"})
        lab.poll(uid, [s1], [GENUINE])
        return qc, s1

    def test_the_critics_shape_a_qc_repeat_in_a_catch_up_poll(self):
        lab = SimLab()
        qc, _ = self._yesterday(lab, "aq1")
        new = [SimLab.run_line("S%d" % (100 + k), {"Water": str(10 + k)})
               for k in range(23)]
        ids = lab.poll("aq1", [qc] + new, [GENUINE] * 24)
        got = dedupe.classify(dedupe.LogRow.from_dict(r) for r in lab.rows)
        c = got.candidates[ids[0]]
        assert (c.label, c.rule) == ("probable_duplicate",
                                     "lone_twin_in_burst")
        assert all(i not in got.candidates for i in ids[1:])
        assert not got.polls["aq1"][-1].replay

    def test_two_standards_repeating_together_are_still_qc_not_a_stretch(
            self):
        """Two QC standards, adjacent, both reading what they read
        yesterday: consecutive twins, but no SAMPLE among them. A lab runs
        its standards every day; that is the point of them."""
        lab = SimLab()
        a = SimLab.qc_line("AF26", "Water", 2.5)
        b = SimLab.qc_line("AO25", "Water", 7.25)
        lab.poll("aq2", [a, b], [GENUINE, GENUINE])
        new = [SimLab.run_line("S%d" % (200 + k), {"Water": str(10 + k)})
               for k in range(22)]
        ids = lab.poll("aq2", new[:5] + [a, b] + new[5:], [GENUINE] * 24)
        got = dedupe.classify(dedupe.LogRow.from_dict(r) for r in lab.rows)
        assert [got.candidates[i].label for i in ids[5:7]] == [
            "probable_duplicate"] * 2
        assert sum(1 for c in got.candidates.values()
                   if c.label in dedupe.HIDE_CANDIDATE_LABELS) == 0

    def test_a_replay_of_five_samples_ahead_of_new_rows_is_a_replay(self):
        """The other side of the line: a restart that re-reads the last six
        samples from a stale offset, then the night's 22 new rows, all in
        one poll. Six samples in the file's order is a stretch — the copies
        are proposed, the new rows are not."""
        lab = SimLab()
        old = [SimLab.run_line("R42%03d" % k, {"v": "r%d" % k})
               for k in range(8)]
        for line in old:
            lab.poll("rs", [line], [GENUINE])
        new = [SimLab.run_line("N41%03d" % k, {"v": "n%d" % k})
               for k in range(22)]
        ids = lab.poll("rs", old[2:] + new, [DUP] * 6 + [GENUINE] * 22)
        got = dedupe.classify(dedupe.LogRow.from_dict(r) for r in lab.rows)
        assert [(got.candidates[i].label, got.candidates[i].rule)
                for i in ids[:6]] == [("replay_duplicate", "burst")] * 6
        assert all(i not in got.candidates for i in ids[6:])

    def test_a_poll_that_is_mostly_twins_keeps_its_lone_twins(self):
        """The 80 % rule is unchanged: in a poll that is overwhelmingly a
        re-read, a twin separated from the others by one changed row is
        still part of the re-read."""
        lab = SimLab()
        old = [SimLab.run_line("M43%03d" % k, {"v": "m%d" % k})
               for k in range(24)]
        for line in old:
            lab.poll("mj", [line], [GENUINE])
        changed = SimLab.run_line("M43012", {"v": "re-integrated"})
        poll = old[:12] + [changed] + old[12:]
        ids = lab.poll("mj", poll, [DUP] * 12 + [GENUINE] + [DUP] * 12)
        got = dedupe.classify(dedupe.LogRow.from_dict(r) for r in lab.rows)
        assert {got.candidates[i].label for i in ids[:12] + ids[13:]} == {
            "replay_duplicate"}
        assert ids[12] not in got.candidates


class TestARetestOfAFewSamplesIsNotACopy:
    """Round-4 critic: a 26-row catch-up poll of new samples in which two
    samples (40005, 40006) were re-tested back to back and read exactly
    what they read before ({S: "2.2", N: "5"}). Both genuine re-tests were
    proposed as `replay_duplicate`, rule `burst`: the round-3 rule called
    ANY run of two or more consecutive twins spanning two samples a
    re-read stretch. Integer results (its C1) failed the same way.

    A pair re-test and a two-line re-read are the same rows; nothing in the
    record tells them apart, and between the two errors only one costs the
    record a reading. So a stretch has to carry more than a re-test of a
    few samples can: FIVE sample readings the record already holds, in a
    row (standards and blanks between them ride along, but do not count —
    they repeat every day by design). The majority path asks the same five.

    A run must also COPY THE RECORD'S ORDER: each copied sample came, in
    the earlier record, right after the sample copied before it (or right
    before, all along, for a newest-first file); a standard joins only
    through the row right next to it. A re-read reproduces the file, so
    its copies do; a re-test printed behind a re-read does not, and starts
    a run of its own that must prove itself.

    What that costs, measured on the server's log-mirror copy of
    2026-10-02 (256,485 run/qc rows), old rule against new: 178 rows move
    from hide candidate to listed-for-review (86 of them on the 08-18
    storm day, 7 on b2ce21612b3c's 09-01 catch-up) and 17 become
    candidates (copies of rows that now stay visible): 63,869 -> 63,708
    hide candidates. Since 09-01 on the four benches O9 names: GC 1
    2,927 -> 2,933, GC 2 395 -> 390, Eraspec 3,484 and NIR 24,961
    unchanged. Of the 48 stretches the round-3 rule found on the mirror,
    35 hold five or more sample twins.

    The residual, stated: five or more samples re-tested in a row, in the
    order first printed, every value identical, inside one big poll, is
    proposed — and still only proposed: Ryan sees twenty examples per bench
    beside their originals before anything is hidden, and `reinstated`
    brings any row back."""

    def _cls(self, lab):
        return dedupe.classify(dedupe.LogRow.from_dict(r) for r in lab.rows)

    def _first(self, lab, uid, values):
        s = [SimLab.run_line("40%03d" % k, values(k)) for k in range(30)]
        lab.poll(uid, s, [GENUINE] * 30)
        for _ in range(5):
            lab.noise()
        return s

    def _new(self, n, base=41):
        return [SimLab.run_line("%d%03d" % (base, k),
                                {"S": "%d.%d" % (k % 5, k)})
                for k in range(n)]

    def _hidden(self, got, ids):
        return [i for i in ids if i in got
                and got[i].label in dedupe.HIDE_CANDIDATE_LABELS]

    def test_the_critics_c1_integer_pair_retest_in_a_catch_up_poll(self):
        lab = SimLab()
        s = self._first(lab, "c1", lambda k: {"S": "%d" % (k % 4 + 1)})
        new = [SimLab.run_line("41%03d" % k, {"S": "%d" % (k % 5)})
               for k in range(23)]
        ids = lab.poll("c1", new[:10] + [s[5], s[6]] + new[10:],
                       [GENUINE] * 25)
        got = self._cls(lab).candidates
        assert self._hidden(got, ids) == []
        assert [(got[i].label, got[i].rule) for i in ids[10:12]] == [
            ("probable_duplicate", "short_stretch")] * 2

    def test_the_critics_c1c_decimal_pair_retest_in_a_catch_up_poll(self):
        lab = SimLab()
        s = self._first(lab, "c1c", lambda k: {"S": "%d.%d" % (k % 4 + 1,
                                                                 k % 3),
                                                 "N": "%d" % (k % 7)})
        new = self._new(24)
        ids = lab.poll("c1c", new[:10] + [s[5], s[6]] + new[10:],
                       [GENUINE] * 26)
        got = self._cls(lab).candidates
        assert self._hidden(got, ids) == []
        assert [got[i].dup_of for i in ids[10:12]] == [
            r["id"] for r in lab.rows[5:7]]

    @pytest.mark.parametrize("where", ["head", "middle", "tail"])
    @pytest.mark.parametrize("how_many", [2, 3, 4])
    def test_up_to_four_samples_retested_anywhere_in_a_big_poll(
            self, where, how_many):
        lab = SimLab()
        s = self._first(lab, "rt", lambda k: {"S": "%d" % (k % 3)})
        retest = s[28 - how_many:28]            # in their original order
        new = self._new(22)
        poll = {"head": retest + new, "middle": new[:11] + retest + new[11:],
                "tail": new + retest}[where]
        ids = lab.poll("rt", poll, [GENUINE] * len(poll))
        got = self._cls(lab).candidates
        assert self._hidden(got, ids) == []
        listed = [i for i in ids if i in got]
        assert len(listed) == how_many
        assert {got[i].label for i in listed} == {"probable_duplicate"}

    def test_a_pair_retest_between_repeated_standards(self):
        """Standards read what they read yesterday on either side of a
        pair re-test: five consecutive twins, but two samples. The
        standards do not make the pair a re-read."""
        lab = SimLab()
        s = self._first(lab, "ps", lambda k: {"S": "%d" % (k % 3)})
        stds = [SimLab.qc_line(n, "S", v) for n, v in (("AF26", 2.0),
                                                      ("AO25", 2.5),
                                                      ("AB10", 1.5))]
        blank = SimLab.run_line("Blank", {"S": "0"})
        lab.poll("ps", stds + [blank], [GENUINE] * 4)
        new = self._new(20)
        ids = lab.poll("ps", new[:8] + stds[:2] + [blank, s[3], s[4],
                                                   stds[2]] + new[8:],
                       [GENUINE] * 26)
        got = self._cls(lab).candidates
        assert self._hidden(got, ids) == []

    def test_five_samples_in_a_row_are_the_evidence_floor(self):
        """The edge, both sides: four sample twins in a row are a re-test
        as far as anyone can tell; five are a re-read."""
        lab = SimLab()
        s = self._first(lab, "five", lambda k: {"S": "%d.%d" % (k, k)})
        ids4 = lab.poll("five", self._new(10) + s[10:14] + self._new(10, 42),
                        [GENUINE] * 24)
        ids5 = lab.poll("five", self._new(10, 43) + s[20:25]
                        + self._new(10, 44), [GENUINE] * 10 + [DUP] * 5
                        + [GENUINE] * 10)
        got = self._cls(lab).candidates
        assert self._hidden(got, ids4) == []
        assert [(got[i].label, got[i].rule) for i in ids5[10:15]] == [
            ("replay_duplicate", "burst")] * 5
        assert self._hidden(got, ids5[:10] + ids5[15:]) == []

    @pytest.mark.parametrize("how_many", [2, 4])
    def test_samples_retested_straight_after_in_the_same_poll(self,
                                                              how_many):
        """The same floor inside one poll. Two (or four) samples measured
        and at once measured again, reading the same: [40005, 40006,
        40005, 40006] mid catch-up. The round-3 `resend` rule called any
        block of two or more Lab IDs repeated back to back a re-sent
        stretch; a block of a few samples is a re-test as far as anyone can
        tell, and is listed (`repeat_in_poll`)."""
        lab = SimLab()
        pair = [SimLab.run_line("48%03d" % k, {"S": "%d" % (k % 2)})
                for k in range(how_many)]
        new = self._new(20)
        ids = lab.poll("bb", new[:9] + pair + pair + new[9:],
                       [GENUINE] * (20 + 2 * how_many))
        got = self._cls(lab).candidates
        assert self._hidden(got, ids) == []
        assert [(got[i].label, got[i].rule)
                for i in ids[9 + how_many:9 + 2 * how_many]] == [
            ("probable_duplicate", "repeat_in_poll")] * how_many

    def test_a_file_holding_sixty_samples_twice_over_is_a_copy(self):
        """Round-4 critic's C3, stated rather than left implicit: a first
        ingest whose 120 lines are 60 samples and then the same 60 again,
        same order, every number the same. That is not sixty re-tests; it
        is the file (or the transfer) holding one block twice, as Agilent
        GC 2's file holds its re-exports. Each reading stays visible once;
        the second block is proposed (`resend`, `stretch`) and, like every
        proposal, hides nothing until Ryan approves it."""
        lab = SimLab()
        block = [SimLab.run_line("42%03d" % k, {"F": "%d" % (60 + k % 3)})
                 for k in range(60)]
        ids = lab.poll("fp", block + block, [GENUINE] * 60 + [DUP] * 60)
        got = self._cls(lab).candidates
        assert all(i not in got for i in ids[:60])
        assert {(got[i].label, got[i].rule) for i in ids[60:]} == {
            ("resend", "stretch")}

    def test_a_retest_right_behind_a_re_read_is_not_part_of_it(self):
        """Found by `TestRandomLabsKeepTheirRetests` (seed 11): a restart
        re-reads five samples (40033-40037) and the first new lines are a
        re-test of four OLD samples (40001-40004), identical. Nine twins in
        a row, so the five-sample floor alone called all nine a re-read. A
        re-read copies the FILE: each copied sample came, in the earlier
        record, right after the one before it. 40001 never came right after
        40037, so the re-test starts a run of its own, and four samples do
        not prove a copy."""
        lab = SimLab()
        s = self._first(lab, "rr", lambda k: {"S": "%d" % (k % 3)})
        new = self._new(22)
        ids = lab.poll("rr", s[25:30] + s[1:5] + new,
                       [DUP] * 5 + [GENUINE] * 26)
        got = self._cls(lab).candidates
        assert [got[i].label for i in ids[:5]] == ["replay_duplicate"] * 5
        assert self._hidden(got, ids[5:]) == []
        assert [got[i].rule for i in ids[5:9]] == ["short_stretch"] * 4

    def test_a_standard_does_not_carry_a_re_read_into_a_retest(self):
        """A re-read that ends on the AF26 check, then a re-test of three
        samples that, in the record, came right after ANOTHER AF26 check.
        The AF26 sits next to half the record; it may end the re-read, but
        it cannot link the re-read to the samples after it. (Had the three
        come right after the re-read's own last sample, they would be what
        a longer re-read writes, and nothing could tell them apart.)"""
        lab = SimLab()
        af = SimLab.qc_line("AF26", "S", 2.0)
        s = [SimLab.run_line("40%03d" % k, {"S": "%d" % (k % 3)})
             for k in range(30)]
        lab.poll("sb", s[:10] + [af] + s[10:20] + [af] + s[20:],
                 [GENUINE] * 32)
        for _ in range(5):
            lab.noise()
        ids = lab.poll("sb", s[3:10] + [af] + s[20:23] + self._new(20),
                       [DUP] * 8 + [GENUINE] * 23)
        got = self._cls(lab).candidates
        assert {got[i].label for i in ids[:8]} == {"replay_duplicate"}
        assert self._hidden(got, ids[8:]) == []

    def test_a_file_holding_a_blank_twice_running_is_still_one_re_read(
            self):
        """The other side: a file that holds Blank, Blank between two
        samples is re-read whole; both Blanks are copies of what the record
        holds right there, and the samples on either side stay one run."""
        lab = SimLab()
        b = SimLab.run_line("Blank", {"S": "0"})
        s = [SimLab.run_line("40%03d" % k, {"S": "%d" % (k % 3)})
             for k in range(8)]
        lab.poll("bb2", s[:4] + [b, b] + s[4:], [GENUINE] * 10)
        for _ in range(5):
            lab.noise()
        ids = lab.poll("bb2", s[:4] + [b, b] + s[4:] + self._new(20),
                       [DUP] * 10 + [GENUINE] * 20)
        got = self._cls(lab).candidates
        assert {got[i].label for i in ids[:10]} == {"replay_duplicate"}
        assert self._hidden(got, ids[10:]) == []

    @pytest.mark.parametrize("backwards", [False, True])
    def test_a_run_keeps_one_direction(self, backwards):
        """A newest-first file is re-read backwards, an oldest-first one
        forwards; a run does not turn round. 40006 was re-tested once
        already, so the record holds it twice. The re-read below copies
        40005-40009 (one way or the other) and then the analyst re-tests
        40006 again: the only link from the re-read's last sample to it
        runs the OTHER way, so it is a run of one, and listed."""
        lab = SimLab()
        s = [SimLab.run_line("40%03d" % k, {"S": "%d" % (k % 3)})
             for k in range(12)]
        lab.poll("dw", s, [GENUINE] * 12)
        lab.poll("dw", [s[6]], [GENUINE])
        for _ in range(5):
            lab.noise()
        if backwards:
            reread, retest = s[9:4:-1], [s[6]]      # 9..5, then 5 -> 6
        else:
            reread, retest = s[3:8], [s[6]]         # 3..7, then 7 -> 6
        ids = lab.poll("dw", reread + retest + self._new(20),
                       [DUP] * 5 + [GENUINE] * 21)
        got = self._cls(lab).candidates
        assert {got[i].label for i in ids[:5]} == {"replay_duplicate"}
        assert self._hidden(got, ids[5:]) == []

    def test_a_retest_inside_a_whole_poll_re_read_is_not_part_of_it(self):
        """The majority path, same reasoning: a re-read of twenty samples
        and, after it, a re-test of two older ones (90 % twins). The
        re-read is proposed; the two re-tests, whose originals sit far
        from the re-read's, are listed."""
        lab = SimLab()
        s = self._first(lab, "mr", lambda k: {"S": "%d" % (k % 3)})
        ids = lab.poll("mr", s[10:30] + [s[2], s[3]] + [
            SimLab.run_line("49000", {"S": "9"})],
            [DUP] * 20 + [GENUINE] * 3)
        got = self._cls(lab).candidates
        assert {got[i].label for i in ids[:20]} == {"replay_duplicate"}
        assert self._hidden(got, ids[20:]) == []
        assert [got[i].label for i in ids[20:22]] == [
            "probable_duplicate"] * 2

    def test_a_two_line_re_read_and_a_pair_retest_are_the_same_record(self):
        """The case no rule can split, shown rather than hidden: bench A
        restarts and re-reads its last two lines from a stale offset; bench
        B's analyst re-tests the last two samples first thing and reads the
        same numbers. Both then print 22 new rows. The two records are
        identical row for row, so ANY rule labels them alike. This one keeps
        both visible and LISTS both beside their originals, because hiding
        bench B's re-test deletes a reading and leaving bench A's copy
        costs one extra point that a person can still mark."""
        lab = SimLab()
        out = {}
        for uid, truth in (("A", DUP), ("B", GENUINE)):
            s = [SimLab.run_line("45%03d" % k, {"S": "%d" % (k % 3)})
                 for k in range(10)]
            for line in s:
                lab.poll(uid, [line], [GENUINE])
            ids = lab.poll(uid, s[-2:] + self._new(22),
                           [truth] * 2 + [GENUINE] * 22)
            out[uid] = ids
        got = self._cls(lab).candidates
        shape = {uid: [(got[i].label, got[i].rule) if i in got else None
                       for i in ids] for uid, ids in out.items()}
        assert shape["A"] == shape["B"]
        assert shape["A"][:2] == [("probable_duplicate", "short_stretch")] * 2
        assert all(x is None for x in shape["A"][2:])


class TestAStandardIsNotASample:
    """Round-3 critic: the 80 % majority rule had none of the guards the
    burst path had, and a Blank and a Solvent counted as "two samples".

    * Five QC standards read again the next morning, every value the same
      (whole degrees), in a poll of their own: 100 % twins, so all five were
      proposed as a replay.
    * Four QC repeats and one new sample: 80 % twins, four hidden.
    * The daily Blank and Solvent run lines at the head of a 32-row overnight
      catch-up poll: two consecutive twins with two distinct Lab IDs, so a
      "stretch of two samples", both hidden.

    Every one of those is a genuine reading. What the three share is that
    nothing in the repeat is a SAMPLE. A lab reads its standards, blanks and
    solvents every day, by design, and they read the same when the
    instrument is in control; that is their whole purpose. A replay re-reads
    a run of the FILE, and a bench's file is mostly samples. So, on both
    paths, the twins have to include at least two samples — Lab IDs the way
    LabCore numbers a sample (four or more digits at the start: 39878,
    40528, 091823-7945, 28967 Top). A named ID (Blank, Solvent, AF26,
    Cal STD, RT 6.29, ASTM2887-12) is a standard or a blank, whether the
    bench logs it as `qc` or as `run`. On the server's log-mirror copy
    79,517 of the benches' own 82,272 `run` rows (96.7 %) carry a sample's
    Lab ID; the named ones are Blank, AF25, AF24, AF26, AJ24, ...

    A repeat with fewer than two samples in it is listed for review
    (`probable_duplicate`, rule `fewer_than_two_samples`), never proposed."""

    def _cls(self, lab):
        return dedupe.classify(dedupe.LogRow.from_dict(r) for r in lab.rows)

    def _standards(self):
        return [SimLab.qc_line(s, "Flash", v, low=v - 3, high=v + 3)
                for s, v in (("AF26", 62.0), ("AO25", 70.0), ("AB10", 45.0),
                             ("AC11", 88.0), ("AD12", 101.0))]

    def test_five_standards_read_again_in_their_own_poll(self):
        lab = SimLab()
        stds = self._standards()
        lab.poll("fl", stds, [GENUINE] * 5)
        for k in range(3):
            lab.poll("fl", [SimLab.run_line("4050%d" % k, {"F": "5%d" % k})],
                     [GENUINE])
        ids = lab.poll("fl", stds, [GENUINE] * 5)
        got = self._cls(lab).candidates
        assert [(got[i].label, got[i].rule) for i in ids] == [
            ("probable_duplicate", "fewer_than_two_samples")] * 5

    def test_four_standards_and_one_new_sample(self):
        lab = SimLab()
        stds = self._standards()[:4]
        lab.poll("fl", stds, [GENUINE] * 4)
        ids = lab.poll("fl", stds + [SimLab.run_line("40600", {"F": "60"})],
                       [GENUINE] * 5)
        got = self._cls(lab).candidates
        assert {got[i].label for i in ids[:4]} == {"probable_duplicate"}
        assert ids[4] not in got

    def test_a_blank_and_a_solvent_ahead_of_a_catch_up_poll(self):
        lab = SimLab()
        blank = SimLab.run_line("Blank", {"N": "0.00"})
        solvent = SimLab.run_line("Solvent", {"N": "0.00"})
        lab.poll("gcb", [blank], [GENUINE])
        lab.poll("gcb", [solvent], [GENUINE])
        night = [SimLab.run_line("40%03d" % (700 + k),
                                 {"N": "%d.%d" % (20 + k, k)})
                 for k in range(30)]
        ids = lab.poll("gcb", [blank, solvent] + night, [GENUINE] * 32)
        got = self._cls(lab).candidates
        assert [got[i].label for i in ids[:2]] == ["probable_duplicate"] * 2
        assert not any(c.label in dedupe.HIDE_CANDIDATE_LABELS
                       for c in got.values())

    def test_a_replayed_file_tail_of_standards_and_samples_is_still_a_copy(
            self):
        """The guard asks for samples, not for no standards: a restart that
        re-reads the morning's standards and five samples re-reads the file,
        and every row of it is a copy — the standards too."""
        lab = SimLab()
        stds = self._standards()[:3]
        s = [SimLab.run_line("4080%d" % k, {"F": "7%d" % k}) for k in range(5)]
        for line in stds + s:
            lab.poll("tl", [line], [GENUINE])
        ids = lab.poll("tl", stds + s, [DUP] * 8)
        got = self._cls(lab).candidates
        assert {got[i].label for i in ids} == {"replay_duplicate"}

    def test_a_named_id_is_a_standard_whether_logged_as_qc_or_run(self):
        assert not dedupe.is_sample_id("Blank")
        assert not dedupe.is_sample_id("AF26")
        assert not dedupe.is_sample_id("Cal STD")
        assert not dedupe.is_sample_id("RT 6.29")
        assert not dedupe.is_sample_id("")
        assert not dedupe.is_sample_id("ASTM2887-12")
        assert not dedupe.is_sample_id("RGO 011623")
        assert not dedupe.is_sample_id("D2887-STD")
        assert not dedupe.is_sample_id("D2887 Cal Std")
        assert dedupe.is_sample_id("39878")
        assert dedupe.is_sample_id("091823-7945")
        assert dedupe.is_sample_id("28018N1")
        assert dedupe.is_sample_id("28967 Top")

    def test_standards_inside_a_whole_file_re_read_are_still_copies(self):
        """The guard must not open a hole in a real re-read. Agilent GC 2's
        09-23 poll re-read its whole file, which holds some readings more
        often than the record does; those extra copies stay visible, and
        between them sat a run of AF26 copies around one sample (39888).
        Judged as a run of its own that is "one sample", but it is the middle
        of a re-read: every row around it is a reading the record already
        holds. A stretch is measured across every such row, so the
        standards' twins in it are copies like the samples'."""
        lab = SimLab()
        p = [SimLab.run_line("4000%d" % k, {"v": "p%d" % k}) for k in range(4)]
        q = [SimLab.run_line("AF26", {"v": "a1"}),
             SimLab.run_line("AF26", {"v": "a2"}),
             SimLab.run_line("40005", {"v": "q5"}),
             SimLab.run_line("Blank", {"v": "0"})]
        for line in p + q:
            lab.poll("g2", [line], [GENUINE])
        new = [SimLab.run_line("4100%d" % k, {"v": "n%d" % k})
               for k in range(10)]
        back = p[::-1]           # GC 2's file runs newest-first in places
        ids = lab.poll("g2", p + back + q + back + new,
                       [DUP] * 4 + [GENUINE] * 4 + [DUP] * 4 + [GENUINE] * 4
                       + [GENUINE] * 10)
        got = self._cls(lab).candidates
        assert [got[i].label for i in ids[8:12]] == ["replay_duplicate"] * 4
        assert {got[i].label for i in ids[4:8] + ids[12:16]} == {
            "probable_duplicate"}
        assert all(i not in got for i in ids[16:])


class TestRandomLabsKeepTheirRetests:
    """The named shapes above are what critics found; this is what they
    might find next. 300 random benches, each with a FILE the way a
    single_csv instrument keeps one: every line appended in the order it
    was printed — new samples, the daily standard and blank, and re-tests
    of one to four earlier samples (identical numbers at low resolution,
    consecutive or scattered, in their first order or not). The bench logs
    it live, or catches up in one poll of 20 or more rows, or restarts and
    re-reads from a stale offset: the last 5-15 SAMPLE lines it already
    logged (whatever standards and re-tests sit among them) and then the
    new lines. Every genuine row must stay visible; every re-read row must
    be proposed."""

    @pytest.mark.parametrize("seed", range(300))
    def test_a_random_bench(self, seed):
        import random
        rnd = random.Random(seed)
        lab = SimLab()
        uid = "r%d" % seed
        stds = [SimLab.qc_line("AF26", "S", 2.0),
                SimLab.run_line("Blank", {"S": "0"})]
        file, logged, nxt = [], 0, [40000]

        def sample():
            nxt[0] += 1
            return SimLab.run_line(str(nxt[0]), {"S": str(rnd.randint(1, 3))})

        for day in range(rnd.randint(2, 6)):
            today = []
            while sum(1 for l in today if l[1].isdigit()) < 20:
                r = rnd.random()
                if r < 0.1:
                    today.append(rnd.choice(stds))
                elif r < 0.2 and file:
                    old = [l for l in file if l[1].isdigit()]
                    k = rnd.randint(1, min(4, len(old)))
                    if rnd.random() < 0.5:
                        j = rnd.randint(0, len(old) - k)
                        today.extend(old[j:j + k])
                    else:
                        today.extend(rnd.sample(old, k))
                else:
                    today.append(sample())
            file.extend(today)
            mode = "first" if day == 0 else rnd.choice(
                ("live", "catchup", "restart"))
            if mode == "live":
                for line in today:
                    lab.poll(uid, [line], [GENUINE])
            elif mode in ("first", "catchup"):
                lab.poll(uid, today, [GENUINE] * len(today))
            else:
                want, stale = rnd.randint(5, 15), logged
                while stale > 0 and sum(1 for l in file[stale:logged]
                                        if l[1].isdigit()) < want:
                    stale -= 1
                lab.poll(uid, file[stale:], [DUP] * (logged - stale)
                         + [GENUINE] * (len(file) - logged))
            logged = len(file)
        got = dedupe.classify(dedupe.LogRow.from_dict(r) for r in lab.rows)
        hide = {c.log_id for c in got.candidates.values()
                if c.label in dedupe.HIDE_CANDIDATE_LABELS}
        assert lab.ids(GENUINE) & hide == set()
        assert lab.ids(DUP) - hide == set()
