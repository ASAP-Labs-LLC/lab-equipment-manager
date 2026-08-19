"""The branches the suite never reached.

Found by running the suite under branch coverage: these are the paths a bench
takes on a bad day — a non-numeric reading, a corrupt date, a Results module
that changes its mind, a spec list refreshed mid-shift — and none of them had a
test. Each assertion states what the docstring promises, not what the code
happens to do.
"""
from datetime import date, datetime, timedelta

import pytest

import lem_station_module as mod
from lem_station_module import LAB_ID_KEY, Machine, MethodMapping, Selector, TestSpec

from test_module_qt import make_module

NOW = datetime(2026, 8, 5, 12, 0, 0)
TODAY = date(2026, 8, 5)


class TestABadReadingDoesNotBreakTheCorrection:
    def test_a_non_numeric_reading_is_left_alone(self):
        """"N/A" cannot be offset. It passes through as it came, and nothing
        is recorded as having been corrected."""
        rows = mod.apply_row_corrections(
            [{LAB_ID_KEY: "L-1", "Flash Point": "N/A", "Density": 0.84}],
            {"Flash Point": -3.0, "Density": 0.1})
        assert rows[0]["Flash Point"] == "N/A"
        assert rows[0][mod.RAW_KEY] == {"Density": 0.84}
        assert mod.CORRECTION_KEY in rows[0]

    def test_a_row_of_nothing_but_text_carries_no_bookkeeping(self):
        rows = mod.apply_row_corrections(
            [{LAB_ID_KEY: "L-1", "Flash Point": "not run"}],
            {"Flash Point": -3.0})
        assert mod.RAW_KEY not in rows[0]
        assert mod.CORRECTION_KEY not in rows[0]

    def test_an_empty_reading_is_not_treated_as_zero(self):
        rows = mod.apply_row_corrections(
            [{LAB_ID_KEY: "L-1", "Flash Point": ""}], {"Flash Point": -3.0})
        assert rows[0]["Flash Point"] == ""


class TestTheQcWindow:
    def test_a_test_that_never_ran_is_not_stale(self):
        """No result is UNKNOWN, which is not the same as expired."""
        assert mod.qc_is_stale(None, NOW, 24.0) is False

    def test_a_result_inside_the_window_is_fresh(self):
        assert mod.qc_is_stale(NOW - timedelta(hours=23, minutes=59),
                               NOW, 24.0) is False

    def test_a_result_past_the_window_is_stale(self):
        assert mod.qc_is_stale(NOW - timedelta(hours=24, seconds=1),
                               NOW, 24.0) is True


class TestAMethodFoundWhateverTheCase:
    def test_a_spec_resolves_a_differently_cased_column(self):
        assert mod._ci_lookup({"flash point": 65.5}, "Flash Point") == 65.5

    def test_a_missing_column_reads_as_nothing(self):
        assert mod._ci_lookup({"Density": 0.84}, "Flash Point") is None


class TestCarryingAVerdictAcrossASpecRefresh:
    def test_the_last_verdict_survives_new_specs_from_labcore(self):
        """LabCore answering with a fresh spec list must not wipe what the
        bench already proved — that is what turns a restart into a false RED."""
        old = [TestSpec(name="Flash Point", value_col="Flash Point",
                        expected=62.5, std_dev=1.0, k=2.0, sample_id="QC1",
                        last_qc_at="2026-08-05T09:00:00", last_qc_value=62.4,
                        last_qc_in_spec=True)]
        new = [TestSpec(name="Flash Point", value_col="Flash Point",
                        expected=62.5, std_dev=1.0, k=2.0, sample_id="QC1")]

        carried = mod.carry_last_qc(new, old)

        assert carried[0].last_qc_at == "2026-08-05T09:00:00"
        assert carried[0].last_qc_value == pytest.approx(62.4)
        assert carried[0].last_qc_in_spec is True

    def test_a_test_that_is_new_carries_nothing(self):
        carried = mod.carry_last_qc(
            [TestSpec(name="Density", value_col="Density", expected=0.84,
                      std_dev=0.01, sample_id="QC1")],
            [TestSpec(name="Flash Point", value_col="Flash Point",
                      expected=62.5, std_dev=1.0, sample_id="QC1",
                      last_qc_value=62.4)])
        assert carried[0].last_qc_value is None


class TestMaintenanceDates:
    def test_a_task_never_done_is_yellow(self):
        task = mod.MaintTask(name="Annual calibration", kind="cal",
                             interval_days=365)
        status, reason = mod.maint_status(task, TODAY)
        assert status == mod.STATUS_YELLOW
        assert "Annual calibration" in reason

    def test_a_corrupt_date_reads_as_never_done_rather_than_crashing(self):
        """A bad date in the record must not take the poll down with it."""
        task = mod.MaintTask(name="Annual calibration", kind="cal",
                             interval_days=365, last_done="last tuesday")
        status, reason = mod.maint_status(task, TODAY)
        assert status == mod.STATUS_YELLOW
        assert "Annual calibration" in reason


class TestTheMathOps:
    """Scale factors an operator can type into a mapping."""

    @pytest.mark.parametrize("expr,value,expected", [
        ("x * 2", "3", "6"),
        ("x ** 2", "3", "9"),
        ("-x", "3", "-3"),
        ("+x", "3", "3"),
        ("round(x)", "2.6", "3"),
        ("round(x, 1)", "2.649", "2.6"),
    ])
    def test_an_expression_is_evaluated(self, expr, value, expected):
        assert float(mod._run_math_op(expr, value)) == pytest.approx(
            float(expected))

    def test_a_runaway_exponent_is_refused(self):
        """`x ** 999999` would hang the poll thread computing it."""
        assert mod._run_math_op("x ** 999999", "10") == "10"

    def test_anything_that_is_not_arithmetic_is_refused(self):
        assert mod._run_math_op("__import__('os').getcwd()", "3") == "3"


class TestBuildingADetectionPattern:
    def test_a_label_and_number_become_a_pattern(self):
        pattern = mod.build_detection_pattern("Cloud point : -15.0")
        assert mod.re.search(pattern, "Cloud point :  -15.0").group(1) == "-15.0"

    def test_a_bare_number_has_no_label_to_anchor_on(self):
        assert mod.build_detection_pattern("-15.0") is None

    def test_nothing_selected_is_no_pattern(self):
        assert mod.build_detection_pattern("") is None


class TestTheHeartbeatSaysWhatItIsWatching:
    @pytest.mark.parametrize("source_type,path,expected", [
        ("multi_csv", "/data/in", "multi_csv /data/in"),
        ("multi_csv", "", "multi_csv (no folder)"),
    ])
    def test_a_folder_watcher_reports_its_folder(self, source_type, path,
                                                 expected):
        machine = Machine(uid="m1", title="t", source_type=source_type,
                          csv_path=path)
        _, args = mod.build_heartbeat_upsert(machine, NOW, polling=True)
        assert expected in args


class FakeResults:
    module_type = "Results"

    def __init__(self, watched, explode=False):
        self._columns = [{"tests": list(watched)}]
        self.explode = explode

    def _all_grids(self):
        if self.explode:
            raise RuntimeError("Results module is mid-rebuild")
        return []


class TestRowsAreNeverLostAtTheHandOff:
    def test_a_broken_results_module_does_not_take_the_poll_down(self, qapp,
                                                                 monkeypatch):
        module = make_module()
        module.context.modules = {"r": FakeResults(["Flash Point"],
                                                   explode=True)}
        assert module._send_to_results(
            [{LAB_ID_KEY: "L-1", "Flash Point": 62.5}]) is False
        assert "Results hand-off error" in module._status_label.text()

    def test_rows_the_storage_step_never_saw_are_kept_not_written(
            self, qapp, monkeypatch):
        """The sync raised before the results road ran.

        This used to push the rows straight to LabCore from the main thread —
        which could not ask which sample they belonged to, and so filed them
        under the ID the instrument printed. That write is the bug. They are
        taken into custody instead and go out on the next poll, under an
        identity LabCore has confirmed.
        """
        module = make_module()
        written = []
        monkeypatch.setitem(mod.__dict__, "labcore_write",
                            lambda op, params=None, **kw: (
                                written.append((op, params)), {"ok": True})[1])
        row = {LAB_ID_KEY: "L-1", "Flash Point": 62.5}
        machine = Machine(uid="m1", title="t")

        module._show_outcome({
            "machine": machine, "raw_prints": [], "rows": [row],
            "now": NOW, "messages": [], "stored": False,
            "evaluation": mod.MachineEvaluation(status=mod.STATUS_GREEN,
                                                reason="")})

        assert not written, "nothing may be written without asking whose it is"
        assert module._parked_rows == [row], "the reading was dropped"

    def test_a_reading_kept_that_way_goes_out_on_the_next_poll(self, qapp,
                                                               monkeypatch):
        """And it must actually come back out — custody is not a bin."""
        module = make_module()
        written = []
        monkeypatch.setitem(mod.__dict__, "labcore_write",
                            lambda op, params=None, **kw: (
                                written.append((op, params)), {"ok": True})[1])
        monkeypatch.setitem(mod.__dict__, "labcore_sql",
                            lambda sql, args=None, **kw: {"ok": True})
        monkeypatch.setitem(
            mod.__dict__, "labcore_read_sql",
            lambda sql, args=None, **kw: (
                {"ok": True, "columns": ["lab_id"], "rows": [{"lab_id": "L-1"}]}
                if 'FROM "samples"' in sql else {"error": "no such table"}))
        module._park([{LAB_ID_KEY: "L-1", "Flash Point": 62.5}])

        module._labcore_sync(Machine(uid="m1", title="t"), [],
                             mod.MachineEvaluation(status=mod.STATUS_GREEN,
                                                   reason=""),
                             NOW, [], [])

        cells = [o["params"] for _op, params in written
                 for o in (params or {}).get("operations", [])]
        assert cells == [{"lab_id": "L-1", "test_name": "Flash Point",
                          "value": "62.5"}]
