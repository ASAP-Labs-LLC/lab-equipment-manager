"""GateGateway: LabCore's real batch answer, per-index faults, and the analyst.

The baseline HGateway answered a failed sub-operation as the WHOLE batch's
error. Real LabCore (`_wop_batch` in LabCore_main.py) never does that: it runs
the batch as one transaction and answers `{"ok": true, "results": [...]}` with
the failure only inside that index's entry. A bench that reads `ok` and stops
there files a cell that was never written — B1 — and a harness that cannot
produce that shape cannot see it. Same for the analyst: A1/A3/A5 are about a
PERSON's value being replaced, which the gateway has to recognise at the
moment the bench's write lands.
"""
import pytest


@pytest.fixture
def gw(loaded):
    GG = loaded[4]
    g = GG(batch_mode="labcore")
    g.seed_samples(["L1", "L2", "L3"])
    return g


def cell(lab, v, **extra):
    return {"operation": "update_cell",
            "params": dict({"lab_id": lab, "test_name": "Density", "value": v}, **extra)}


def test_the_op_tables_come_from_labcore_main():
    from gharness.hgateway import labcore_tables
    db_ops, inner = labcore_tables()
    assert "batch" in db_ops and "raw_sql" in db_ops
    assert set(inner) == {"update_cell", "insert_sample", "add_test",
                          "update_sample_field", "remove_test", "add_result",
                          "update_result"}


def test_an_ok_batch_carries_per_index_results(gw):
    r = gw.write("batch", {"operations": [cell("L1", "0.8"), cell("L2", "0.9")]})
    assert r == {"ok": True, "results": [{"index": 0, "ok": True},
                                         {"index": 1, "ok": True}]}
    assert gw.cell("L1", "Density") == "0.8"


def test_labcores_per_index_error_strings(gw):
    r = gw.write("batch", {"operations": [
        {"operation": "nope", "params": {}},
        {"operation": "raw_sql", "params": {"sql": "SELECT 1"}},
        cell("", "1"),
        cell("L1", "0.7")]})
    assert r["ok"] is True
    assert r["results"] == [
        {"index": 0, "error": "Unknown operation: nope"},
        {"index": 1, "error": "Operation raw_sql not batchable."},
        {"index": 2, "error": "lab_id and test_name required."},
        {"index": 3, "ok": True}]
    assert gw.write("batch", {"operations": []}) == \
        {"error": "operations must be a non-empty list."}


def test_a_per_index_fault_leaves_the_rest_of_the_batch_committed(gw):
    gw.fail_index("L2", "Density")
    r = gw.write("batch", {"operations": [cell("L1", "0.8"), cell("L2", "0.9")]})
    assert r["ok"] is True and "error" in r["results"][1]
    assert gw.cell("L1", "Density") == "0.8"
    assert gw.cell("L2", "Density") is None
    assert gw.per_index_errors[("L2", "Density")] == 1


def test_a_landed_per_index_fault_writes_the_cell_and_still_says_error(gw):
    # An exception after the UPDATE (in _mirror_result_to_history) — no
    # savepoint, so the UPDATE commits with the rest.
    gw.fail_index("L2", "Density", landed=True)
    r = gw.write("batch", {"operations": [cell("L2", "0.9")]})
    assert "error" in r["results"][0]
    assert gw.cell("L2", "Density") == "0.9"


def test_a_per_index_fault_for_n_tries_then_clears(gw):
    gw.fail_index("L1", "Density", times=2)
    for _ in range(2):
        assert "error" in gw.write("batch", {"operations": [cell("L1", "1")]})["results"][0]
    assert gw.write("batch", {"operations": [cell("L1", "1")]})["results"][0] == \
        {"index": 0, "ok": True}


def test_the_analyst_is_not_a_bench_op(gw):
    n = gw.ops_total()
    gw.analyst_edit("L1", "Density", "0.5")
    assert gw.ops_total() == n and gw.cell("L1", "Density") == "0.5"


def test_overwriting_a_persons_value_is_counted(gw):
    gw.analyst_edit("L1", "Density", "0.5")
    gw.write("batch", {"operations": [cell("L1", "0.5")]})       # same value: not an overwrite
    assert sum(gw.analyst_overwritten.values()) == 0
    gw.write("batch", {"operations": [cell("L1", "0.8")]})
    assert gw.analyst_overwritten[("L1", "Density")] == 1


def test_expect_audit_hits_count_cells_that_did_not_hold_what_the_bench_read(gw):
    gw.write("batch", {"operations": [cell("L1", "0.8")]})
    gw.write("batch", {"operations": [cell("L1", "0.9", expect="0.8")]})   # held 0.8: fine
    assert gw.expect_ops == 1 and not gw.expect_audit_hits
    gw.analyst_edit("L1", "Density", "0.1")
    gw.write("batch", {"operations": [cell("L1", "0.9", expect="0.9")]})   # held 0.1: hit
    assert gw.expect_audit_hits[("L1", "Density")] == 1


def test_the_a5_injection_needs_a_read_first_and_fires_once(gw):
    gw.inject_between_read_and_batch("L3", "Density", "0.4")
    gw.write("batch", {"operations": [cell("L3", "0.8")]})        # no read yet
    assert gw.injected == 0
    gw.read_sql("SELECT lab_id FROM samples")                     # the guard read
    gw.write("batch", {"operations": [cell("L1", "0.8")]})        # not its cell
    assert gw.injected == 0
    gw.write("batch", {"operations": [cell("L3", "0.9")]})
    assert gw.injected == 1
    assert gw.analyst_overwritten[("L3", "Density")] == 1
    gw.read_sql("SELECT 1")
    gw.write("batch", {"operations": [cell("L3", "0.7")]})
    assert gw.injected == 1


def test_baseline_mode_keeps_phase_ones_accounting(loaded):
    # The 29 baseline numbers were measured with a batch that answers the
    # first sub-error as the batch's error and counts every executed sub-op
    # as a land. Baseline mode must be that, exactly.
    GG = loaded[4]
    g = GG(batch_mode="baseline")
    g.seed_samples(["L1"])
    r = g.write("batch", {"operations": [
        {"operation": "add_test", "params": {"lab_id": "L1", "test_name": "Density"}},
        cell("L1", "0.8")]})
    assert r == {"ok": True}
    assert g.cell_lands[("L1", "Density", None)] == 1
    assert g.cell_lands[("L1", "Density", "0.8")] == 1
