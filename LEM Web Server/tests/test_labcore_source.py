"""TDD for LabCoreDataSource — the seam that feeds the reused evaluate_box engine.

The data source reads QC results from LabCore (via the gateway) and emits row
dicts in the exact shape data_source.evaluate_box already expects, so the V4
evaluation engine is reused unchanged.
"""

import pytest

import refusal_shapes
from data_source import evaluate_box
from labcore_result import LabCoreUnavailable
from labcore_gateway import FakeLabCoreGateway
from labcore_source import LabCoreDataSource
from models import (
    BoxConfig,
    SampleSpec,
    SampleTestSpec,
    WatchedTarget,
    STATUS_GREEN,
    STATUS_RED,
    STATUS_UNKNOWN,
)


def _seed(gw, lab_id, test_name, value, updated_at="2023-01-01 09:00:00"):
    gw.write("insert_sample", {"lab_id": lab_id, "customer": "QC"})
    gw.write("add_test", {"lab_id": lab_id, "test_name": test_name})
    gw.write("update_cell", {
        "lab_id": lab_id, "test_name": test_name,
        "value": str(value), "updated_at": updated_at,
    })


def _sample(sample_id_val="STD-1", value_col="Test1", expected=10.0, std=1.0):
    return SampleSpec(
        name="ContextA", sample_id_val=sample_id_val,
        tests=[SampleTestSpec(name="Test1", value_col=value_col, expected=expected, std_dev=std)],
    )


def _box():
    return BoxConfig(uid="box1", title="Box 1", csv_path="",
                     watched_targets=[WatchedTarget(sample="ContextA", test="Test1")])


def test_load_rows_emits_engine_compatible_row():
    gw = FakeLabCoreGateway()
    _seed(gw, "STD-1", "Test1", 10.0)
    src = LabCoreDataSource(gw)

    rows = src.load_rows([_sample()], sample_id_column="Lab ID")

    assert len(rows) == 1
    row = rows[0]
    assert row["Lab ID"] == "STD-1"
    assert row["Test1"] == "10.0"
    # timestamp split so best_row_time consumes it as a "parsed" source
    assert row["parsed_date"] == "2023-01-01"
    assert row["parsed_time"] == "09:00:00"


def test_in_spec_value_evaluates_green(mock_now):
    gw = FakeLabCoreGateway()
    _seed(gw, "STD-1", "Test1", 10.0, updated_at="2023-01-01 09:00:00")
    src = LabCoreDataSource(gw)
    sample = _sample()
    rows = src.load_rows([sample], "Lab ID")

    ev = evaluate_box(_box(), {"ContextA": sample}, "Lab ID", rows)
    assert ev.status == STATUS_GREEN
    assert ev.results[0].latest_value == 10.0
    assert ev.results[0].in_spec is True


def test_out_of_spec_value_evaluates_red():
    gw = FakeLabCoreGateway()
    _seed(gw, "STD-1", "Test1", 99.0)
    src = LabCoreDataSource(gw)
    sample = _sample()  # expected 10 ± 2

    rows = src.load_rows([sample], "Lab ID")
    ev = evaluate_box(_box(), {"ContextA": sample}, "Lab ID", rows)
    assert ev.status == STATUS_RED


def test_no_result_for_watched_sample_is_unknown():
    gw = FakeLabCoreGateway()  # nothing seeded
    src = LabCoreDataSource(gw)
    sample = _sample()

    rows = src.load_rows([sample], "Lab ID")
    assert rows == []
    ev = evaluate_box(_box(), {"ContextA": sample}, "Lab ID", rows)
    assert ev.status == STATUS_UNKNOWN


def test_prefers_sample_test_results_over_sample_tests():
    """LabStation streams into sample_test_results; that is the authoritative value."""
    gw = FakeLabCoreGateway()
    gw.write("insert_sample", {"lab_id": "STD-1", "customer": "QC"})
    gw.write("update_cell", {"lab_id": "STD-1", "test_name": "Test1", "value": "5.0",
                             "updated_at": "2023-01-01 08:00:00"})
    gw.sql(
        "INSERT INTO sample_test_results (lab_id, test_name, result_value, updated_at, source_workspace) "
        "VALUES (?, ?, ?, ?, ?)",
        ["STD-1", "Test1", "10.0", "2023-01-01 10:00:00", "bench1"],
    )
    src = LabCoreDataSource(gw)
    rows = src.load_rows([_sample()], "Lab ID")

    assert len(rows) == 1
    assert rows[0]["Test1"] == "10.0"
    assert rows[0]["parsed_time"] == "10:00:00"


# ── one round trip, not one per pair ────────────────────────────────────────
#
# Flagged in the 2026-08-03 CPU report: `load_rows` made one HTTPS round trip per
# (sample × test), sequentially, and each trip paid the full TLS setup cost. The
# pooled session (test_http_session.py) makes each trip cheap; this makes there be
# one of them.
#
# Note for whoever reads the report: this is NOT the path that spiked. `load_rows`
# is reached only from the legacy `/api/status` and `/api/refresh`, and the V4
# dashboard that polled them is no longer served by any route. Fixed because it is
# a real defect that would bite the moment anything used it again.

class Counting(FakeLabCoreGateway):
    def __init__(self):
        super().__init__()
        self.reads = []

    def read_sql(self, sql, args=None, **kw):
        self.reads.append(sql)
        return super().read_sql(sql, args, **kw)


def _multi_sample():
    return SampleSpec(
        name="ContextA", sample_id_val="STD-1",
        tests=[SampleTestSpec(name="T1", value_col="Test1", expected=10.0, std_dev=1.0),
               SampleTestSpec(name="T2", value_col="Test2", expected=20.0, std_dev=1.0),
               SampleTestSpec(name="T3", value_col="Test3", expected=30.0, std_dev=1.0)],
    )


def test_many_pairs_take_one_read():
    gw = Counting()
    for name, value in (("Test1", 10.0), ("Test2", 20.0), ("Test3", 30.0)):
        _seed(gw, "STD-1", name, value)
    for name, value in (("Test1", 11.0), ("Test2", 21.0)):
        _seed(gw, "STD-2", name, value)
    second = SampleSpec(
        name="ContextB", sample_id_val="STD-2",
        tests=[SampleTestSpec(name="T1", value_col="Test1", expected=10.0, std_dev=1.0),
               SampleTestSpec(name="T2", value_col="Test2", expected=20.0, std_dev=1.0)],
    )
    src = LabCoreDataSource(gw)
    gw.reads.clear()

    rows = src.load_rows([_multi_sample(), second], sample_id_column="Lab ID")

    assert len(gw.reads) == 1, f"{len(gw.reads)} round trips for 5 pairs"
    assert len(rows) == 5


def test_the_batched_answer_matches_the_per_pair_answer():
    """The optimisation is only safe if it returns the same thing."""
    gw = FakeLabCoreGateway()
    for name, value in (("Test1", 10.0), ("Test2", 20.0), ("Test3", 30.0)):
        _seed(gw, "STD-1", name, value)
    src = LabCoreDataSource(gw)
    sample = _multi_sample()

    batched = src.load_rows([sample], "Lab ID")
    one_at_a_time = []
    for test in sample.tests:
        latest = src._latest_result("STD-1", test.value_col)
        assert latest is not None
        one_at_a_time.append({"Lab ID": "STD-1", test.value_col: latest[0]})

    assert [r["Lab ID"] for r in batched] == ["STD-1"] * 3
    for row, expected in zip(batched, one_at_a_time):
        col = [k for k in expected if k != "Lab ID"][0]
        assert row[col] == expected[col]


def test_the_newest_observation_still_wins():
    gw = FakeLabCoreGateway()
    _seed(gw, "STD-1", "Test1", 10.0, updated_at="2023-01-01 09:00:00")
    _seed(gw, "STD-1", "Test1", 99.0, updated_at="2024-06-01 12:00:00")
    src = LabCoreDataSource(gw)

    rows = src.load_rows([_sample()], "Lab ID")

    assert rows[0]["Test1"] == "99.0"


def test_a_pair_with_no_result_is_simply_absent():
    gw = FakeLabCoreGateway()
    _seed(gw, "STD-1", "Test1", 10.0)
    src = LabCoreDataSource(gw)
    sample = SampleSpec(
        name="ContextA", sample_id_val="STD-1",
        tests=[SampleTestSpec(name="T1", value_col="Test1", expected=10.0, std_dev=1.0),
               SampleTestSpec(name="T2", value_col="Nothing", expected=1.0, std_dev=1.0)],
    )

    rows = src.load_rows([sample], "Lab ID")

    assert len(rows) == 1 and "Test1" in rows[0]


def test_no_samples_means_no_read_at_all():
    gw = Counting()
    src = LabCoreDataSource(gw)
    gw.reads.clear()
    assert src.load_rows([], "Lab ID") == []
    assert gw.reads == []


class Refusing(FakeLabCoreGateway):
    """LabCore answers, and the answer says the read did not happen."""

    def __init__(self, shape=None):
        super().__init__()
        self.shape = shape

    def read_sql(self, sql, args=None, **kw):
        return dict(self.shape if self.shape is not None
                    else refusal_shapes.current())


@pytest.mark.usefixtures("both_refusal_shapes")
def test_a_read_that_was_refused_is_not_a_lab_with_no_qc():
    """THIS TEST USED TO ASSERT THE DEGRADE.

    It was `test_a_failed_read_yields_no_rows_rather_than_raising`, and it
    pinned `load_rows(...) == []` for a busy queue — the one judgement
    `labcore_result` exists to abolish, sitting in the read that feeds
    `/api/status` and `/api/refresh`. No rows is not a neutral answer here:
    `evaluate_box` turns it into UNKNOWN for every instrument in the lab, so a
    routine LabCore blip painted the whole floor "no QC data" and the page
    said 200 OK.

    Both refusal shapes, because the old code judged by `res.get("error")` and
    would still be fooled by the other one.
    """
    src = LabCoreDataSource(Refusing())
    with pytest.raises(LabCoreUnavailable):
        src.load_rows([_sample()], "Lab ID")


def test_a_transport_failure_is_the_same_fact():
    """A client that raises read nothing either, and `build_snapshot` must not
    have to catch two different families to say so."""
    class Blown(FakeLabCoreGateway):
        def read_sql(self, sql, args=None, **kw):
            raise OSError("connection reset by peer")

    with pytest.raises(LabCoreUnavailable):
        LabCoreDataSource(Blown()).load_rows([_sample()], "Lab ID")


@pytest.mark.usefixtures("both_refusal_shapes")
def test_the_single_pair_read_no_longer_demands_a_positive_ok():
    """`_latest_result` judged its answer with `if not res.get("ok")`, which is
    the rule that fails EVERY read against a real service that simply returns
    its rows. Same rule as everywhere else now: refuse on a positive failure
    signal, accept anything else."""
    gw = FakeLabCoreGateway()
    _seed(gw, "STD-1", "Test1", 10.0)
    real = gw.read_sql

    class NoVerdict(FakeLabCoreGateway):
        def read_sql(self, sql, args=None, **kw):
            answer = dict(real(sql, args, **kw))
            answer.pop("ok", None)          # an answer with rows and no verdict
            return answer

    # Rows, no verdict — the shape a real service is free to answer with, and
    # the one the old `if not res.get("ok")` threw on the floor.
    assert LabCoreDataSource(NoVerdict())._latest_result(
        "STD-1", "Test1")[0] == "10.0"
    assert LabCoreDataSource(gw)._latest_result("STD-1", "Test1")[0] == "10.0"
    with pytest.raises(LabCoreUnavailable):
        LabCoreDataSource(Refusing())._latest_result("STD-1", "Test1")


def test_a_lab_with_no_results_yet_is_still_empty_not_an_error():
    """The degrade that WAS honest stays: LabCore answered, it had nothing."""
    gw = FakeLabCoreGateway()
    assert LabCoreDataSource(gw).load_rows([_sample()], "Lab ID") == []


def test_the_timestamp_still_splits_into_date_and_time():
    gw = FakeLabCoreGateway()
    _seed(gw, "STD-1", "Test1", 10.0, updated_at="2024-06-01 12:34:56")
    src = LabCoreDataSource(gw)

    row = src.load_rows([_sample()], "Lab ID")[0]

    assert row["parsed_date"] == "2024-06-01"
    assert row["parsed_time"].startswith("12:34")
