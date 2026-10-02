"""The rewrite resolver: which lines of a changed file are NEW readings.

A tailed file usually only grows, and then the byte offset answers the
question by itself. Instruments also rewrite the whole file, trim its head,
correct a line in place, keep it newest-first, or rotate it away and start a
new one. Then the bench has two ordered lists of line hashes — O, the file as
it last consumed it, and N, the file now — and has to say which of N it has
never seen (transfer spec §4.2).

v3.9 answered with "the offset is past the end, start again from 0", which
re-logged whole files (Phase 1: R3 5 duplicates, R2 12 lost AND 12 doubled).
The three v4 proposals answered by COUNTING identical lines, or by a ≥3-line
overlap, and the judges showed each one silently loses a genuine reading when
the instrument prints a line that repeats one already in the file (X1, X2).

These tests pin the ORDERED diff that replaced them, one file shape each, and
keep the count rule beside it (`resolve_by_count`) so the loss it causes stays
demonstrated rather than remembered. Hashes are plain strings here: the
resolver only compares them.
"""
import time

import lem_station_module as mod
from lem_station_module import resolve_rewrite, resolve_by_count


def L(*names):
    return list(names)


def new_lines(res, N):
    return [N[i] for i in res.new]


# ── continuation: the old newest line is still there ─────────────────────────

def test_x1_a_trim_plus_a_reprint_of_the_last_line_is_one_new_reading():
    """The judges' R7 shape. The file held [L0, L1, L2, L1] (L1 genuinely
    printed twice). The instrument trimmed it in place to [L2, L1] and then
    printed L1 a THIRD time. Counting identical lines sees two L1 before and
    two after — "nothing new" — and the third reading is gone without a trace.
    In order, the old [L2, L1] is the start of the new file and the trailing
    L1 is new."""
    O = L("L0", "L1", "L2", "L1")
    N = L("L2", "L1", "L1")
    res = resolve_rewrite(O, N)
    assert res.kind == "continuation"
    assert res.new == [2]
    assert not res.ambiguous and res.ambiguity is None


def test_the_count_rule_this_replaced_loses_x1s_reading():
    """Kept on purpose: the gate's self-test swaps it in for the ordered diff
    and must go red, and this says why in one line."""
    assert resolve_by_count(L("L0", "L1", "L2", "L1"), L("L2", "L1", "L1")).new == []


def test_a_head_trim_plus_one_append_is_one_new_reading():
    """R3: trimmed to its last five lines plus one new. v3.9 re-logged all six
    (the offset was past the end, so it started again from 0)."""
    O = ["L%d" % i for i in range(30)]
    N = O[-5:] + ["L30"]
    res = resolve_rewrite(O, N)
    assert res.kind == "continuation"
    assert new_lines(res, N) == ["L30"]


def test_a_whole_file_rewrite_with_history_plus_one_is_one_new_reading():
    O = ["L%d" % i for i in range(15)]
    N = O + ["L15"]
    assert new_lines(resolve_rewrite(O, N), N) == ["L15"]


def test_an_in_place_correction_of_the_last_line_is_a_new_reading():
    """R6: the last line rewritten with a corrected value. The corrected line
    is a new reading; the original stays in the record (§7.11.3: never
    restated)."""
    O = ["L%d" % i for i in range(9)]
    N = O[:-1] + ["L8*"]
    res = resolve_rewrite(O, N)
    assert new_lines(res, N) == ["L8*"]


def test_a_mid_file_edit_is_a_new_reading_and_nothing_else_is():
    """R6deep: a same-size edit far from both ends."""
    O = ["L%d" % i for i in range(200)]
    N = list(O)
    N[100] = "L100*"
    res = resolve_rewrite(O, N)
    assert res.kind == "continuation"
    assert new_lines(res, N) == ["L100*"]


def test_an_unchanged_file_has_nothing_new():
    O = ["L%d" % i for i in range(50)]
    assert resolve_rewrite(O, list(O)).new == []


def test_a_suffix_that_only_looks_shared_does_not_anchor_the_file():
    """Stripping the common suffix before the diff is what makes a 80,000-line
    file cheap — and stripping it blindly breaks X1: the new trailing L1
    "matches" the old trailing L1 and the true alignment is lost (the resolver
    then calls L2 new and loses the L1). A margin of lines is left unstripped,
    so the diff still sees the real overlap."""
    O = ["H%d" % i for i in range(500)] + L("L0", "L1", "L2", "L1")
    N = L("L2", "L1", "L1")                  # X1, with a long history before it
    res = resolve_rewrite(O, N)
    assert new_lines(res, N) == ["L1"]
    assert res.new == [2]


def test_a_long_shared_suffix_is_stripped_and_still_correct():
    """The stripping itself: a mid-file edit in a long file keeps both ends."""
    O = ["L%d" % i for i in range(5000)]
    N = O[:10] + ["X"] + O[11:]
    res = resolve_rewrite(O, N)
    assert res.new == [10]


# ── newest-first ─────────────────────────────────────────────────────────────

def test_a_newest_first_file_is_detected_and_read_oldest_first():
    """R2: an instrument that keeps its newest row FIRST. Every poll the old
    rows move down and new ones appear above them. v3.9 lost 12 of 15 and
    doubled 12 (it read the top of the file again as "appended"). The prepend
    is recognised, the file is marked newest-first, and the new rows come out
    in the order they were printed."""
    O = L("L2", "L1", "L0")
    N = L("L5", "L4", "L3", "L2", "L1", "L0")
    res = resolve_rewrite(O, N)
    assert res.newest_first is True
    assert new_lines(res, N) == ["L3", "L4", "L5"]


def test_once_newest_first_later_prepends_are_continuations():
    O = L("L5", "L4", "L3", "L2", "L1", "L0")
    N = L("L7", "L6") + O
    res = resolve_rewrite(O, N, newest_first=True)
    assert res.newest_first is True
    assert new_lines(res, N) == ["L6", "L7"]


def test_an_append_that_also_looks_like_a_prepend_stays_an_append():
    """[P, P] -> [P, P, P] is both "one appended" and "one prepended". An
    oldest-first file must not be flipped to newest-first by a coincidence of
    identical lines."""
    res = resolve_rewrite(L("P", "P"), L("P", "P", "P"))
    assert res.newest_first is False
    assert len(res.new) == 1


def test_a_trim_to_the_last_line_then_qc_prints_is_a_continuation():
    """Found by fuzzing the round-2 resolver: the file is trimmed in place to
    its newest line (QC3) and the instrument then prints two samples, QC3
    and QC2 again, and a sample. The new file starts with the old file's
    newest line — the head-trim shape — but difflib's longest-match alignment
    paired the new QC3 and QC2 with older copies far up the old file, ended
    in a `replace`, and the change was read as a correction: the two QC
    prints were lost. When the old file's head is gone (the diff does not
    open by matching it) and the new file starts with the old file's last
    lines, the trim is the reading: everything after the overlap is new."""
    O = L("QC2", "S1", "QC1", "QC3", "S2", "S3", "S4", "QC2", "QC1", "S5",
          "QC2", "S6", "QC2", "QC2", "QC3", "QC2", "QC2", "QC3", "S7", "S8",
          "QC1", "S9", "QC2", "S10", "S11", "S12", "QC2", "S13", "QC3")
    N = L("QC3", "S41", "S42", "QC3", "QC2", "S43")
    res = resolve_rewrite(O, N)
    assert res.kind == "continuation"
    assert new_lines(res, N) == ["S41", "S42", "QC3", "QC2", "S43"]


def test_an_insert_between_repeats_is_also_a_trim_and_both_are_recorded():
    """[QC3, QC3] trimmed in place to [QC3], then S, QC2, QC3 printed. The
    same bytes are two lines inserted between the two old QC3s, which is
    what the diff reads (and the trim's QC3 would be lost). §4.2: when the
    diff could shift, the larger candidate set is recorded and the lines only
    it holds are labelled — a visible possible duplicate, not a loss."""
    O = L("QC3", "QC3")
    N = L("QC3", "S1", "QC2", "QC3")
    res = resolve_rewrite(O, N)
    assert res.new == [1, 2, 3]
    assert res.ambiguous == {3}
    assert res.ambiguity["kind"] == "periodic" and res.ambiguity["lines"] == 1


def test_a_correction_in_a_short_file_that_opens_and_closes_alike_is_recorded_labelled():
    """The price of the rule above, stated: a short file that opens and closes
    with the same QC line also reads as "trimmed to that line, then the rest
    printed again". The corrected line is new either way; the other four are
    recorded labelled."""
    O = L("QC1", "S1", "S2", "S3", "S4", "QC1")
    N = L("QC1", "S1", "S2*", "S3", "S4", "QC1")
    res = resolve_rewrite(O, N)
    assert new_lines(res, N) == ["S1", "S2*", "S3", "S4", "QC1"]
    assert res.ambiguous == {1, 3, 4, 5}


def test_a_correction_in_a_long_file_that_opens_and_closes_alike_stays_one_line():
    """...and only a short one: a trim reading that would label more lines
    than any print burst (over 20) is not taken. One new line."""
    O = ["QC1"] + ["S%d" % i for i in range(40)] + ["QC1"]
    N = list(O)
    N[10] = "S9*"
    res = resolve_rewrite(O, N)
    assert new_lines(res, N) == ["S9*"]
    assert not res.ambiguous


def test_in_place_head_trims_lose_only_what_the_bytes_cannot_show():
    """A property over 6,000 seeded in-place head trims that keep at least
    one line (R3's shape) plus 1..5 appended lines, a third of them repeating
    QC lines. The resolver's answer either holds every appended line, or is
    itself an exact explanation of the same bytes (the new file is some other
    suffix of O plus fewer new lines — R7p's class, which no reader can tell
    apart). Before this round's rules: 233 trials lost a reading the bytes
    did show; now none."""
    import random
    from collections import Counter
    rng = random.Random(11)
    qc = ["QC1", "QC2", "QC3"]
    sid = [0]

    def draw():
        if rng.random() < 0.35:
            return rng.choice(qc)
        sid[0] += 1
        return "S%05d" % sid[0]
    decidable, exact = [], 0
    for _ in range(6000):
        O = [draw() for _ in range(rng.randint(2, 40))]
        t = rng.randint(1, len(O) - 1)
        add = [draw() for _ in range(rng.randint(1, 5))]
        N = O[t:] + add
        res = resolve_rewrite(O, N)
        got = sorted(res.new)
        if Counter(N[j] for j in got) >= Counter(add):
            continue
        k = len(got)
        if got == list(range(len(N) - k, len(N))) and \
                N[:len(N) - k] == O[len(O) - (len(N) - k):]:
            exact += 1
            continue
        decidable.append((O, N, add, res))
    assert not decidable, (len(decidable), decidable[:2])
    assert exact < 10, exact


# ── rotation: a new file (or truncated and started again) ────────────────────

def test_x2_a_rotated_file_that_repeats_earlier_lines_is_all_new():
    """X2: the instrument rotates its file and the new one starts with the
    same three QC standards, same values, that the old one started with. They
    were printed again for real. All three proposals lost all three."""
    O = L("Q0", "Q1", "Q2", "S0", "S1", "S2")
    N = L("Q0", "Q1", "Q2")
    res = resolve_rewrite(O, N, rotated=True)
    assert res.kind == "rotation"
    assert res.new == [0, 1, 2]
    # Recorded, and labelled: the same bytes are what a write-temp-then-rename
    # that cut the old file's tail would leave (see the rotation tests below).
    assert res.ambiguous == {0, 1, 2}


# A new file under the name (a different file identity) that repeats some of
# the old file's lines. Round 1 resolved these by the shape of the diff's END:
# when the new file's last line differed from the old one's, the diff ended in
# a `replace`, the change was read as an in-place correction, and only the
# replacing lines were recorded. Every repeated QC line was lost without a
# trace as soon as one sample followed it (the critic's "QC triplet + 1
# sample": 3 lost, 0 labelled, through the gate's own World). A rotation is a
# different file; what the diff's end looks like says nothing about it.

def test_x2_a_rotated_file_repeating_qc_then_a_sample_loses_nothing():
    O = L("Q0", "Q1", "Q2", "S0", "S1", "S2")
    N = L("Q0", "Q1", "Q2", "S3")
    res = resolve_rewrite(O, N, rotated=True)
    assert res.kind == "rotation"
    assert res.new == [0, 1, 2, 3]
    assert res.ambiguous == {0, 1, 2}
    assert res.ambiguity["kind"] == "rotation_overlap"
    assert res.ambiguity["lines"] == 3


def test_x2_a_rotated_file_repeating_qc_then_three_samples_loses_nothing():
    O = L("Q0", "Q1", "Q2", "S0", "S1", "S2")
    N = L("Q0", "Q1", "Q2", "S3", "S4", "S5")
    res = resolve_rewrite(O, N, rotated=True)
    assert res.new == [0, 1, 2, 3, 4, 5]
    assert res.ambiguous == {0, 1, 2}


def test_x2_qc_repeated_between_two_new_samples_loses_nothing():
    O = L("Q0", "Q1", "Q2", "S0", "S1", "S2")
    N = L("S3", "Q0", "Q1", "Q2", "S4")
    res = resolve_rewrite(O, N, rotated=True)
    assert res.new == [0, 1, 2, 3, 4]
    assert res.ambiguous == {1, 2, 3}


def test_a_short_file_corrected_by_write_temp_then_rename_is_recorded_labelled():
    """The bytes the rotation rule cannot tell from X2's: a three-line file
    whose last line was corrected and saved by writing a temp file and
    renaming it over the old one (the identity changes). Recording all of it,
    with the two unchanged lines labelled, is a visible possible duplicate;
    the alternative reading loses a whole day's opening QC. Record, never
    drop (§4.2)."""
    O = L("A", "B", "C")
    N = L("A", "B", "C*")
    res = resolve_rewrite(O, N, rotated=True)
    assert res.new == [0, 1, 2]
    assert res.ambiguous == {0, 1}


def test_a_long_file_corrected_by_write_temp_then_rename_is_still_a_correction():
    """More than twenty of the old lines, in order, in the new file is not a
    plausible rotation (the spec's own bound for X3), so a large export
    corrected through a temp file records the corrected line and nothing else
    — never forty labelled duplicates."""
    O = ["L%d" % i for i in range(40)]
    N = O[:-1] + L("L39*")
    res = resolve_rewrite(O, N, rotated=True)
    assert new_lines(res, N) == ["L39*"]
    assert not res.ambiguous


def test_a_new_file_starting_with_all_of_a_short_old_file_is_a_rotation_when_the_old_file_is_still_there():
    """Day 1 printed only its QC triplet; day 2's file starts with the same
    triplet and then a sample. By bytes alone that is also a write-temp-then-
    rename of the whole file plus one line, which is a continuation (the next
    test). What tells them apart is not in the bytes: on a rotation the old
    file is still in the folder under another name (the bench found and
    drained it), while a temp-file rewrite leaves no old file behind."""
    O = L("Q0", "Q1", "Q2")
    N = L("Q0", "Q1", "Q2", "S0")
    res = resolve_rewrite(O, N, rotated=True, predecessor=True)
    assert res.new == [0, 1, 2, 3]
    assert res.ambiguous == {0, 1, 2}
    assert res.ambiguity["kind"] == "rotation_overlap"


def test_a_rotated_file_with_its_old_file_still_there_is_never_taken_for_newest_first():
    """[S0, Q0, Q1, Q2] after [Q0, Q1, Q2] looks like a newest-first prepend.
    When the old file is still in the folder this is a new file in its own
    right, read in file order, and the direction is not flipped for good."""
    O = L("Q0", "Q1", "Q2")
    N = L("S0", "Q0", "Q1", "Q2")
    res = resolve_rewrite(O, N, rotated=True, predecessor=True)
    assert sorted(res.new) == [0, 1, 2, 3]
    assert res.ambiguous == {1, 2, 3}
    assert not res.newest_first


def test_no_rotated_file_of_a_few_lines_ever_loses_a_reading():
    """The critic's fuzz, as a property: a new file of 1..8 lines drawn from a
    pool where QC lines repeat, after an old file of 1..40 lines. With the old
    file found in the folder, every line of the new one is recorded (labelled
    where it matches the old file), 4,000 seeded trials out of 4,000.

    Without the old file there is exactly one shape that is NOT all new: the
    new file holds every line of the old one, at its top or (prepended) at its
    bottom. Those bytes are what a write-temp-then-rename of the same history
    leaves, and without the predecessor's evidence they are read that way;
    the test counts them so the exception stays visible (12 of 4,000 here)."""
    import random
    rng = random.Random(7)
    qc = ["QC1", "QC2", "QC3"]
    sid = [0]

    def draw():
        if rng.random() < 0.4:
            return rng.choice(qc)
        sid[0] += 1
        return "S%05d" % sid[0]
    lost, copies = [], 0
    for _ in range(4000):
        O = [draw() for _ in range(rng.randint(1, 40))]
        N = [draw() for _ in range(rng.randint(1, 8))]
        copy = N[:len(O)] == O or N[len(N) - len(O):] == O
        for pred in (False, True):
            res = resolve_rewrite(O, N, rotated=True, predecessor=pred)
            if sorted(res.new) == list(range(len(N))):
                continue
            if copy and not pred:
                copies += 1
                continue
            lost.append((O, N, pred, res))
    assert not lost, (len(lost), lost[:3])
    assert copies == 12


def test_x3_a_new_file_starting_with_the_old_files_last_lines_is_recorded_and_labelled():
    """X3 / X3r: a new file (new identity) whose first three lines equal the
    old file's last three, then two new ones. That is either a rotation whose
    first prints repeat yesterday's last ones (the same QC triplet at shutdown
    and at start-up), or a trim done as write-temp-then-rename. The bytes
    cannot tell them apart. Record, do not drop: all five are recorded, the
    three overlapping ones as `ambiguous`, and one `ambiguity` record says so.
    The worst case is a labelled duplicate somebody can see, never a silent
    loss."""
    O = ["L%d" % i for i in range(6)]
    N = O[-3:] + L("L6", "L7")
    res = resolve_rewrite(O, N, rotated=True)
    assert res.kind == "rotation_overlap"
    assert res.new == [0, 1, 2, 3, 4]
    assert res.ambiguous == {0, 1, 2}
    assert res.ambiguity == {"kind": "rotation_overlap", "lines": 3,
                             "chosen": "recorded"}


def test_an_overlap_longer_than_twenty_lines_is_a_continuation():
    """A rotation that repeats more than twenty lines in order is not
    plausible; that is a trim, and only the lines after the overlap are new."""
    O = ["L%d" % i for i in range(40)]
    N = O[-21:] + L("L40")
    res = resolve_rewrite(O, N, rotated=True)
    assert res.kind == "continuation"
    assert new_lines(res, N) == ["L40"]
    assert not res.ambiguous


def test_a_new_file_that_is_a_copy_of_the_old_one_plus_more_is_a_continuation():
    """Write-to-temp-then-rename of the WHOLE file plus a new line (a common
    export pattern) changes the file's identity on every write. N starting
    with all of O is a copy, not a rotation, even where O's last line happens
    to equal its first."""
    O = L("A", "B", "A")
    N = L("A", "B", "A", "C")
    res = resolve_rewrite(O, N, rotated=True)
    assert new_lines(res, N) == ["C"]
    assert not res.ambiguous


def test_the_same_file_cut_back_to_its_own_head_is_recorded_and_labelled():
    """Same file, now holding only the first lines it held before. Either it
    was truncated and the instrument re-printed the same opening lines (X2 in
    place), or lines were deleted from its end. Recorded, labelled."""
    O = L("Q0", "Q1", "S0", "S1")
    N = L("Q0", "Q1")
    res = resolve_rewrite(O, N)
    assert res.kind == "rotation"
    assert res.new == [0, 1]
    assert res.ambiguous == {0, 1}


def test_an_unrelated_new_file_is_all_new():
    res = resolve_rewrite(L("A", "B", "C"), L("X", "Y"), rotated=True)
    assert res.new == [0, 1]


def test_nothing_consumed_before_means_everything_is_new():
    assert resolve_rewrite([], L("A", "B")).new == [0, 1]


# ── periodic content and the time budget ─────────────────────────────────────

def test_periodic_content_falls_back_and_says_so():
    """2,000 identical lines make the ordered diff quadratic (0.23 s measured
    for the spec; far worse at 80,000). When distinct lines are under 5 % the
    resolver uses the tail-anchored comparison instead and journals an
    `ambiguity` of kind `periodic`: where the anchor could sit in more than one
    place, the larger set is recorded and labelled."""
    O = ["P"] * 2000
    N = ["Q"] + ["P"] * 1990 + ["R"]     # nothing to strip at either end
    t0 = time.perf_counter()
    res = resolve_rewrite(O, N)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, elapsed
    assert res.ambiguity is not None and res.ambiguity["kind"] == "periodic"
    assert res.ambiguity["why"] == "periodic"
    assert N[res.new[-1]] == "R"                   # the certain new line
    assert res.ambiguous and max(res.ambiguous) < len(N) - 1
    assert len(res.ambiguous) == res.ambiguity["lines"]


def test_periodic_lines_at_the_ends_are_stripped_and_need_no_fallback():
    """The same identical lines, but with the change at one end: the common
    prefix strips them all, the diff runs on what is left, and the answer
    needs no labels."""
    res = resolve_rewrite(["P"] * 2000, ["P"] * 2000 + ["Q"])
    assert res.new == [2000] and res.ambiguity is None


def test_a_diff_over_budget_falls_back_to_the_tail_anchor():
    """The 2 s budget, forced to zero: the fallback anchors O's newest lines
    in N. Distinct content anchors in one place, so the answer is exact and
    nothing is labelled; the ambiguity record still says the diff did not
    run."""
    O = ["L%d" % i for i in range(100)]
    N = O[20:] + ["L100", "L101"]
    res = resolve_rewrite(O, N, budget=0.0)
    assert new_lines(res, N) == ["L100", "L101"]
    assert not res.ambiguous
    assert res.ambiguity["kind"] == "periodic"
    assert res.ambiguity["why"] == "budget"


def test_eighty_thousand_lines_resolve_well_inside_the_budget():
    """The spec measured 0.01–0.03 s for trim, mid-file edit, rotation and
    newest-first prepend on 80,000 hashed lines. Held here at < 1 s each,
    with the measured times in the failure message."""
    O = [("h%06d" % i).encode() for i in range(80000)]
    shapes = {
        "trim": (O[30000:] + [b"new"], False),
        "mid_edit": (O[:40000] + [b"edit"] + O[40001:], False),
        "rotation": ([b"r1", b"r2"], True),
        "prepend": (list(reversed([b"n1", b"n2"])) + O, False),
    }
    times = {}
    for name, (N, rotated) in shapes.items():
        t0 = time.perf_counter()
        res = resolve_rewrite(O, N, rotated=rotated)
        times[name] = round(time.perf_counter() - t0, 4)
        assert res.new, name
    assert max(times.values()) < 1.0, times


def test_the_budget_default_is_two_seconds():
    assert mod.REWRITE_DIFF_BUDGET == 2.0
    assert mod.ROTATION_OVERLAP_MAX == 20
