"""The §9.4 tally: what was printed against what was kept, and nothing else.

Phase 1's tally keyed everything by lab_id and counted "a lab with no run row"
as lost. Two blind spots followed, both found by the judges: a lab printed
twice (a QC standard, a genuine re-run) could lose its second print and still
look whole, and a scenario author could zero a term by hand (`log_lost +
0 * missing_runs`) and nothing would notice. These tests pin the replacement:
multisets over (lab_id, raw value), sums with no switches, and the property
the T0 mutation relies on — remove one stored row and the tally MUST move.
"""
from gharness.ledger import Ledger, tally, results


def rows(*pairs, labels=None, hidden=()):
    out = []
    for i, (lab, val) in enumerate(pairs):
        out.append({"lab_id": lab, "value": val,
                    "labels": (labels or {}).get(i, set()), "hidden": i in hidden})
    return out


def test_exactly_kept_is_zero_lost_zero_dup():
    L = Ledger()
    for lab, v in (("a", "1"), ("b", "2"), ("c", "3")):
        L.register(lab, v)
    t = tally(L.truth(), rows(("a", "1"), ("b", "2"), ("c", "3")))
    assert (t["lost"], t["dup"]) == (0, 0)


def test_a_genuine_repeat_is_counted_as_two_prints():
    # X1's lab1 is printed three times with identical content. Keeping two of
    # them is ONE lost, which a set (or a lab-keyed "has a row") would call 0.
    L = Ledger()
    for _ in range(3):
        L.register("lab1", "0.8001")
    t = tally(L.truth(), rows(("lab1", "0.8001"), ("lab1", "0.8001")))
    assert (t["lost"], t["dup"]) == (1, 0)


def test_a_replayed_row_is_a_dup_even_when_every_lab_has_a_row():
    L = Ledger()
    L.register("a", "1")
    t = tally(L.truth(), rows(("a", "1"), ("a", "1")))
    assert (t["lost"], t["dup"]) == (0, 1)


def test_the_same_lab_with_a_different_value_is_not_the_same_print():
    # R6: the corrected line is a new run. Keeping only the old value loses
    # the correction AND the old row is not a duplicate of it.
    L = Ledger()
    L.register("a", "0.8008")
    L.register("a", "0.9008")
    t = tally(L.truth(), rows(("a", "0.8008")))
    assert (t["lost"], t["dup"]) == (1, 0)


def test_a_stray_row_from_a_half_line_is_a_dup():
    L = Ledger()
    L.register("100126-10004", "0.8004")
    t = tally(L.truth(), rows(("100126-10004", "0.8004"), ("100126-10", "")))
    assert (t["lost"], t["dup"]) == (0, 1)


def test_removing_one_stored_row_always_moves_the_tally():
    # The property mutation T0 checks against the whole gate, here in miniature:
    # whatever the shape, deleting a stored row changes lost or dup.
    L = Ledger()
    for lab, v in (("a", "1"), ("a", "1"), ("b", "2")):
        L.register(lab, v)
    for stored in ([("a", "1"), ("a", "1"), ("b", "2")],
                   [("a", "1"), ("a", "1"), ("a", "1"), ("b", "2")],
                   [("a", "1"), ("b", "2")]):
        full = tally(L.truth(), rows(*stored))
        cut = tally(L.truth(), rows(*stored[1:]))
        assert (full["lost"], full["dup"]) != (cut["lost"], cut["dup"])


def test_labelled_dups_are_dups_that_carry_the_label():
    # X3r: three rows labelled ambiguous_repeat that the truth does not hold
    # are three labelled dups — visible, never hidden.
    L = Ledger()
    for lab, v in (("x", "1"), ("y", "2"), ("z", "3")):
        L.register(lab, v)
    stored = rows(("x", "1"), ("y", "2"), ("z", "3"), ("x", "1"), ("y", "2"), ("z", "3"),
                  labels={3: {"ambiguous_repeat"}, 4: {"ambiguous_repeat"},
                          5: {"ambiguous_repeat"}})
    t = tally(L.truth(), stored)
    assert (t["dup"], t["labelled_dup"], t["labelled_rows"]) == (3, 3, 3)


def test_labelled_rows_the_truth_holds_are_not_dups():
    # X3: the same label on genuine prints is information, not a duplicate.
    L = Ledger()
    for lab, v in (("x", "1"), ("x", "1")):
        L.register(lab, v)
    t = tally(L.truth(), rows(("x", "1"), ("x", "1"),
                              labels={1: {"ambiguous_repeat"}}))
    assert (t["dup"], t["labelled_dup"], t["labelled_rows"]) == (0, 0, 1)


def test_hidden_rows_do_not_count_as_stored():
    # A replay the dedupe marked replay_duplicate is out of the effective view.
    L = Ledger()
    L.register("a", "1")
    t = tally(L.truth(), rows(("a", "1"), ("a", "1"), hidden=(1,)))
    assert (t["lost"], t["dup"]) == (0, 0)


def test_rewrites_register_nothing():
    # Only `register` adds truth; a Ledger has no other way in.
    L = Ledger()
    L.register("a", "1")
    assert len(L) == 1 and L.truth() == {("a", "1"): 1}


def test_results_use_the_latest_print_unless_a_person_typed_after_it():
    L = Ledger()
    L.register("a", "1")
    L.register("a", "2")                    # instrument re-run (A4)
    L.register("b", "5")
    L.analyst_set("b", "6")                 # a person corrected the cell (A1)
    assert results(L, {"a": "2", "b": "6"}) == {"res_lost": 0, "res_wrong": 0}
    assert results(L, {"a": "1", "b": "5"}) == {"res_lost": 0, "res_wrong": 2}
    assert results(L, {"a": "2"}) == {"res_lost": 1, "res_wrong": 0}


def test_a_print_after_the_analyst_makes_the_print_the_truth_again():
    L = Ledger()
    L.register("b", "5")
    L.analyst_set("b", "6")
    L.register("b", "7")
    assert L.expected_cell("b") == "7"
