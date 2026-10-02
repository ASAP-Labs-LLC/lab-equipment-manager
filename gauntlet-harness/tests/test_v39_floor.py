"""The rolled-back floor has to be able to READ what a rollback wrote.

Critic, T-P5 round 3: every count on DG2 was right — one QC verdict, the
status changes, no row twice, no bookkeeping row — and the floor those rows
exist for still could not show them. The status_change rows were written with
the journal's UTC offset (`2026-10-01T09:00:30-07:00`), v3.9's status gutter
subtracts each row's time from a naive `now()`, and
/api/machines/<uid>/status-timeline answered 500 for every bench that had
synced v2. No gate scenario read the projected rows with v3.9's floor code.

`legacy_projection.v39_floor` now does, with the TAGGED v3.9.0 web server (in
a subprocess: one process cannot import two versions of `web_app`). These
pin that the check can fail — it reports the 500 on the critic's own rows —
and that it passes the same rows in v3.9's naive shape, so a green DG2 means
the floor rendered, not that the check was blind.
"""
import json

import pytest

from gharness import legacy_projection as LP

QC = {"machine_uid": "b1", "ts": "2026-10-01T09:00:30", "kind": "qc",
      "lab_id": "QC-D", "test_name": "Density", "value": "0.85",
      "detail": json.dumps({"in_spec": True, "expected": 0.85, "low": 0.75,
                            "high": 0.95, "operator": None,
                            "calibration_id": None, "jk": "e:11"})}


def _state(ts, jk):
    return {"machine_uid": "b1", "ts": ts, "kind": "status_change",
            "lab_id": "", "test_name": "", "value": "",
            "detail": json.dumps({"from": "UNKNOWN", "to": "GREEN",
                                  "reason": "System nominal",
                                  "sub": {"qc": "GREEN"}, "jk": jk})}


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    return LP.v39_web_tree(str(tmp_path_factory.mktemp("v39floor")))


def test_the_critics_rows_break_v39s_status_gutter(tree, tmp_path):
    """The rows the gate's own DG2 left in LabCore at acedd5a: a status
    change at -07:00 beside a naive QC row."""
    got = LP.render_v39_floor([_state("2026-10-01T09:00:30-07:00", "e:13"),
                               QC], "b1", tree, str(tmp_path))
    assert got["status"] == 500
    assert "offset-naive and offset-aware" in got["error"]


def test_the_same_rows_in_v39s_naive_shape_render(tree, tmp_path):
    got = LP.render_v39_floor([_state("2026-10-01T09:00:30", "e:13"), QC],
                              "b1", tree, str(tmp_path))
    assert got == {"status": 200, "events": 2, "error": ""}


def test_offset_rows_are_counted():
    assert LP.offset_ts_rows([_state("2026-10-01T09:00:30-07:00", "e:1"),
                              _state("2026-10-01T16:00:30Z", "e:2"), QC]) == 2


def test_an_unreadable_floor_run_is_an_error_not_a_result(tmp_path):
    """A failed read is never an empty result: a tree with no web_app is a
    harness error, not status 0 or a pass."""
    (tmp_path / "LEM Web Server").mkdir()
    with pytest.raises(RuntimeError):
        LP.render_v39_floor([QC], "b1", str(tmp_path), str(tmp_path))
