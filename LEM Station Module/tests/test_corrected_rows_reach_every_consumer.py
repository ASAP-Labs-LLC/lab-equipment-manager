"""Every consumer of a parsed row gets the CORRECTED value, and none of them
treats the row's bookkeeping as a measurement.

`apply_row_corrections` corrects at one point and hangs `__raw__` /
`__corrections__` on the row (ISO/IEC 17025:2017 §7.5.1). The module's own
comment at RESERVED_ROW_KEYS states the contract for everything downstream:
"consumers skip them rather than treat them as methods, or '__raw__' would be
written to LabCore as a test name."

`build_labcore_batch` and `run_log_detail` honour that. These tests cover the
consumers the correction work did not revisit: the latest-result CSV, the
LabStation result bus, the Results-module hand-off, and the operator's data
table — each of which decides what counts as a method by filtering row keys.
"""
from datetime import datetime

import pytest
from PySide6 import QtWidgets

import lem_station_module as mod

from test_module_qt import FakeContext, make_module

NOW = datetime(2026, 8, 5, 9, 30, 0)


def corrected_row():
    """One parsed print off a bench with a -3.0 on Flash Point, as the poll
    produces it: corrected values, with the raw reading kept on the row."""
    rows = mod.apply_row_corrections(
        [{mod.LAB_ID_KEY: "L-1001", "Flash Point": 65.5, "Density": 0.84,
          "parsed_date": "2026-08-05", "parsed_time": "09:30:00"}],
        {"Flash Point": -3.0})
    assert rows[0]["Flash Point"] == 62.5, "precondition: the row is corrected"
    assert rows[0][mod.RAW_KEY] == {"Flash Point": 65.5}
    return rows[0]


class TestTheLatestResultCsv:
    """`write_latest_result` — the one-row CSV other apps read off disk."""

    def test_the_bookkeeping_is_not_a_csv_column(self, tmp_path):
        path = mod.write_latest_result(corrected_row(), "PAC Flash 2",
                                       str(tmp_path))
        header = open(path, encoding="utf-8").readlines()[0]
        assert mod.RAW_KEY not in header
        assert mod.CORRECTION_KEY not in header

    def test_the_reading_in_the_csv_is_the_corrected_one(self, tmp_path):
        path = mod.write_latest_result(corrected_row(), "PAC Flash 2",
                                       str(tmp_path))
        header, values = [l.strip().split(",")
                          for l in open(path, encoding="utf-8").readlines()[:2]]
        assert values[header.index("Flash Point")] == "62.5"

    def test_a_csv_header_mapping_never_renames_the_bookkeeping(self):
        """`apply_csv_headers` collapses methods onto their csv_header. The
        reserved keys are not methods and must pass through untouched, or a
        mapping could rename __raw__ into a real column."""
        machine = mod.Machine(
            uid="m1", title="PAC Flash 2",
            mappings=[mod.MethodMapping(methods=["Flash Point"],
                                        selector=mod.Selector(),
                                        csv_header="Flash")])
        out = mod.apply_csv_headers(corrected_row(), machine)
        assert out["Flash"] == 62.5
        assert out[mod.RAW_KEY] == {"Flash Point": 65.5}


class TestTheLabStationResultBus:
    """`_publish_rows` — what the module hands to LabStation via add_result."""

    def test_the_bookkeeping_is_never_published_as_a_test(self, qapp):
        module = make_module()
        module._publish_rows(mod.Machine(uid="m1", title="PAC Flash 2"),
                             [corrected_row()])
        published = [test for _, test, _, _ in module.context.results]
        assert mod.RAW_KEY not in published
        assert mod.CORRECTION_KEY not in published
        assert sorted(published) == ["Density", "Flash Point"]

    def test_the_value_published_is_the_corrected_one(self, qapp):
        module = make_module()
        module._publish_rows(mod.Machine(uid="m1", title="PAC Flash 2"),
                             [corrected_row()])
        by_test = {test: value for _, test, value, _ in module.context.results}
        assert by_test["Flash Point"] == "62.5"


class FakeResults:
    """Stand-in for LabStation's Results module: result columns that watch
    LabCore methods, and the append path a CSV pull uses."""
    module_type = "Results"

    def __init__(self, watched):
        self._columns = [{"tests": list(tests)} for tests in watched]
        self._grid = QtWidgets.QTableWidget(0, 1 + len(watched))
        self._grid_dirty = False
        self._auto_push_timer = None
        self.appended = []

    def _all_grids(self):
        return [self._grid]

    def _lab_id_suffix(self, value):
        return value.strip()

    def _append_lab_id_row(self, lab_id, results=None, mark_as=None):
        self.appended.append((lab_id, dict(results or {})))


class TestTheResultsHandOff:
    """`_deliver_rows_to_results` / `_results_can_accept` — the hand-off to a
    Results module on the same canvas."""

    def test_a_watching_column_receives_the_corrected_value(self, qapp):
        """The identity map is passed because a FILED reading always has one.

        `identities=None` means the opposite — LabCore was never asked who this
        is — and the hand-off treats that paint as provisional: it fills a row
        the analyst already has open and appends none of its own, so an outage
        cannot leave the same reading on the grid twice under two Lab IDs. This
        test is about the corrected VALUE reaching the watching column, so it
        speaks for the ordinary path where identity is known.
        """
        module = make_module()
        results = FakeResults([["Flash Point"]])
        assert module._deliver_rows_to_results(results, [corrected_row()],
                                               {"L-1001": "L-1001"})
        assert results.appended == [("L-1001", {1: "62.5"})]

    def test_the_bookkeeping_is_never_offered_to_a_column(self, qapp):
        """A column watching __raw__ must receive nothing: it is not a
        method, whatever a Results column has been configured to watch."""
        module = make_module()
        results = FakeResults([[mod.RAW_KEY], [mod.CORRECTION_KEY]])
        module._deliver_rows_to_results(results, [corrected_row()])
        delivered = [values for _, values in results.appended]
        assert delivered in ([], [{}]), delivered

    def test_the_prediction_does_not_count_bookkeeping_as_a_method(self, qapp):
        module = make_module()
        module.context.modules = {"r": FakeResults([[mod.RAW_KEY]])}
        assert module._results_can_accept([corrected_row()]) is False


class TestTheOperatorsDataTable:
    """`_refresh_data_table` — the recent-rows list under the card."""

    def test_the_summary_shows_measurements_not_bookkeeping(self, qapp):
        module = make_module()
        module._recent_rows.appendleft(corrected_row())
        module._refresh_data_table()
        summary = module._data_table.item(0, 1).text()
        assert "Flash Point=62.5" in summary
        assert mod.RAW_KEY not in summary
        assert mod.CORRECTION_KEY not in summary
