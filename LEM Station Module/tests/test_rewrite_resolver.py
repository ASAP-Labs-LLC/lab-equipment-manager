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
    assert not res.ambiguous


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
