"""Expectation rows are exact. A better number is a failure too.

Spec §9: "The gate fails on any mismatch, including a better number. A better
number means the harness changed and must be re-baselined deliberately." If
`{"dup": 0}` quietly meant "at most 0", nothing changes; but `{"dup": 12}`
meaning "at most 12" would let a broken harness that counts 0 dups pass the
v3.9 reproduction — which is the one check that says the harness still sees.
"""
from gharness.expect import compare, check_field


def test_equal_passes():
    assert compare({"dup": 12, "lost": 0}, {"dup": 12, "lost": 0, "other": 5}) == []


def test_a_better_number_fails():
    assert compare({"dup": 12}, {"dup": 0})


def test_bounds_only_where_written():
    assert compare({"sends": {"le": 60}}, {"sends": 60}) == []
    assert compare({"sends": {"le": 60}}, {"sends": 61})
    assert compare({"n": {"ge": 1}}, {"n": 0})


def test_a_missing_field_is_a_mismatch_not_a_pass():
    assert compare({"conflicts": 3}, {})


def test_none_is_not_zero():
    # "No conflicts" and "this target cannot say" are different sentences.
    assert compare({"conflicts": 0}, {"conflicts": None})
    assert compare({"conflicts": None}, {"conflicts": 0})
    assert compare({"conflicts": None}, {"conflicts": None}) == []


def test_a_bound_on_a_non_number_fails():
    ok, _ = check_field({"le": 2}, None)
    assert not ok


def test_nested_rows_and_volatile_paths():
    row = {"state": {"rows": 250, "filled_at": "2026-10-01T20:26:20+00:00"}}
    got = {"state": {"rows": 250, "filled_at": "2026-10-01T21:00:00+00:00"}}
    assert compare(row, got) != []
    assert compare(row, got, volatile={"state.filled_at"}) == []
    assert compare(row, {"state": {"rows": 249, "filled_at": "x"}},
                   volatile={"state.filled_at"})
