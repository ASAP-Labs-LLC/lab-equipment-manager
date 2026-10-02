"""The `jk` recipe, held to ONE answer on both sides (transfer v4 §7, §13).

A v4 bench on today's server projects its machine-log rows into LabCore with
`detail.jk = epoch:seq` (the station module's `projection_detail`). When the
server becomes v4, two things read those rows back: the bridge's legacy pull,
which links a row carrying `jk` to the bench record it projects, and the v2
ingest, which stores the same record's rows with `_row_detail(detail, jk)`.
If the two recipes ever wrote different bytes for one row, nothing would
break loudly — the link is by `json_extract(detail, '$.jk')` — but the store
would hold two spellings of one record's rows, and a key recipe is a MAJOR
contract (§13). So the module's function and the server's are asked the same
questions here, on the shapes the bench actually writes.

The end-to-end behaviour (a projected bench reaching a v4 server and sending
its epoch from seq 1 with nothing doubled) is the gate's M6r, M5 and DG2.
"""
import json
import os
import sys

import pytest

import bench_api

MODULE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                          "LEM Station Module"))


@pytest.fixture(scope="module")
def station():
    for p in (os.path.join(MODULE_DIR, "tests"), MODULE_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    import lem_station_module
    return lem_station_module


DETAILS = [
    "{}",
    "",
    json.dumps({"values": {"Density": "0.8123"}}),
    json.dumps({"values": {"RON": "91.2"}, "raw": {"RON": "91.0"},
                "corrections": {"RON": 0.2}}),
    json.dumps({"verdict": "PASS", "expected": -7.4, "low": -10.2,
                "high": -4.6, "operator": None, "calibration_id": None}),
    json.dumps({"from": "GREEN", "to": "RED", "reason": "QC out",
                "sub": {"qc": "RED", "pm": "GREEN", "calibration": "GREEN"}}),
    json.dumps({"note": "lamp replaced — Ölwechsel"}),
    json.dumps({"jk": "old:1", "note": "already keyed"}),
    "not json at all",
    "[1, 2]",
]


@pytest.mark.parametrize("detail", DETAILS)
def test_bench_and_server_write_the_same_bytes(station, detail):
    for jk in ("a1b2c3d4e5f60718:1", "ep:123456"):
        assert station.projection_detail(detail, jk) == \
            bench_api._row_detail(detail, jk)


def test_a_projected_rows_key_is_what_the_server_links_on(station):
    """The server's link reads `jk` back out of the detail and splits it at
    the last colon into (epoch, seq). Every row the bench projects must
    answer that question with its own record."""
    from legacy_import import jk_of
    for seq in (1, 42, 100000):
        detail = station.projection_detail(
            json.dumps({"values": {"Density": "0.8"}}), "a1b2c3d4e5f60718:%d" % seq)
        assert jk_of(detail) == ("a1b2c3d4e5f60718", seq)
