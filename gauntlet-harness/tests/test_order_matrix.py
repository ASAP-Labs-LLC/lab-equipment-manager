"""M1 and M4 (transfer §12.2): what makes their zeros mean something.

M4's row says a v4 bench on a v4 server sends LabCore no `lem_*` statement.
A counter that could not see such a statement would read 0 forever, so the
first test puts the same bench on an OLD server (404: legacy projection,
where its machine log goes to LabCore's lem_machine_log by design) and
requires the counter to see it.

M1's row says today's pairing reproduces phase 1 exactly. It reads the
gate's own v3.9 drift run; a drift run that is not trustworthy, or that
belongs to another target, must stop M1 rather than be summarised as
"reproduced 0 of 29" or, worse, as whatever its stale file said.
"""
import json

import pytest

from gharness import order_matrix as OM


@pytest.fixture
def W(loaded):
    lh, rf, rw, mod, GG = loaded
    from gharness.world import make_world
    from gharness.servers import server_factory
    World = make_world(lh, rf, mod, GG, server_factory)
    rf.Ctx = World
    return World


def test_lem_categories_are_lem_tables_and_nothing_else():
    assert OM.is_lem_category("sql:machine_log")
    assert OM.is_lem_category("read:config")
    assert OM.is_lem_category("sql:DDL")
    # The results road is LabCore's own business, not LEM's record.
    assert not OM.is_lem_category("write:batch")
    assert not OM.is_lem_category("read:SELECT lab_id, test_name, result FRO")
    assert not OM.is_lem_category("read:identity")


def test_the_lem_counter_sees_a_bench_writing_lem_tables_on_an_old_server(W):
    c = W(road_modes={"A": "404", "B": "404"})
    mark = len(c.gw.trace)
    for _ in range(3):
        c.emit(3)
        c.poll()
    seen = sum(1 for cat in c.gw.trace[mark:] if OM.is_lem_category(cat))
    assert c.m._transfer.mode == "legacy"
    assert seen > 0


def _drift_file(tmp_path, **over):
    res = {"target": "v3.9", "code_root": "/x/target-v3.9", "harness_errors": [],
           "drifted": [], "economy_drift": [],
           "scenarios": {s: {"status": "ran", "drift": [],
                             "measured": {"lost": 0, "dup": 0}}
                         for s in OM.BASELINE_29 + list(OM.WEB_4)}}
    res.update(over)
    p = tmp_path / "drift.json"
    p.write_text(json.dumps(res))
    return str(p)


def test_m1_reports_what_the_drift_run_found(tmp_path, monkeypatch):
    path = _drift_file(tmp_path)
    with open(path) as f:
        res = json.load(f)
    res["scenarios"]["K3"]["drift"] = ["cell_dup_sends: 14 != expected 15"]
    with open(path, "w") as f:
        json.dump(res, f)
    monkeypatch.setenv("LEM_GATE_DRIFT_JSON", path)
    got = OM.m1(str(tmp_path))
    assert got["baseline_reproduced"] == 28
    assert got["not_reproduced"] == ["K3"]
    assert (got["lost"], got["effective_dup"]) == (0, 0)


def test_m1_refuses_an_untrustworthy_or_foreign_drift_run(tmp_path, monkeypatch):
    monkeypatch.setenv("LEM_GATE_DRIFT_JSON", _drift_file(
        tmp_path, harness_errors=["production was called"]))
    with pytest.raises(RuntimeError, match="not trustworthy"):
        OM.m1(str(tmp_path))
    monkeypatch.setenv("LEM_GATE_DRIFT_JSON", _drift_file(tmp_path, target="v4"))
    with pytest.raises(RuntimeError, match="not v3.9"):
        OM.m1(str(tmp_path))
