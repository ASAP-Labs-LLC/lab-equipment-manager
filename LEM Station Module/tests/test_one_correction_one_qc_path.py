"""Two structural guarantees, asserted rather than assumed.

1. **The correction is global and applied once.** Parsed rows are built in ONE
   place and corrected on the next line; every consumer reads the corrected
   number off the row, and nothing adds an offset a second time.

2. **QC has no infrastructure of its own outside detection.** QC does not parse,
   does not correct, and does not invent test names: it reads the same corrected
   rows the customer results come from, and a method becomes a QC test only by
   assignment against LabCore's standards.

Both are the kind of property that holds today and quietly stops holding when
someone adds a consumer — which is exactly how the bookkeeping keys leaked into
the CSV, the result bus and the Results hand-off.
"""
import inspect
from datetime import datetime

import pytest

import lem_station_module as mod
from lem_station_module import LAB_ID_KEY, Machine, MethodMapping, Selector, TestSpec

from test_module_qt import make_module

NOW = datetime(2026, 8, 5, 11, 0, 0)

SOURCE = inspect.getsource(mod)


def bench():
    """A bench reporting two methods, one of which has QC assigned."""
    machine = Machine(
        uid="pac-flash-2", title="PAC Flash 2", source_type="single_csv",
        delimiter=",", lab_id=Selector(mode="cell", index=0),
        mappings=[MethodMapping(methods=["Flash Point"],
                                selector=Selector(mode="cell", index=1),
                                qc_sample_id="QC1"),
                  MethodMapping(methods=["Density"],
                                selector=Selector(mode="cell", index=2))],
        tests=[TestSpec(name="Flash Point", value_col="Flash Point",
                        expected=62.5, std_dev=1.0, k=2.0, sample_id="QC1")])
    mod.apply_corrections(machine, {"Flash Point": -3.0, "Density": 0.10})
    return machine


class TestOneParseBoundary:
    def test_rows_are_built_in_exactly_one_place(self):
        assert SOURCE.count("result.to_row(") == 1, (
            "a second row producer would need its own correction step")

    def test_a_row_is_corrected_the_moment_it_is_built(self):
        src = inspect.getsource(mod.LEMStationModule._process_outcome)
        built = src.index("result.to_row(")
        corrected = src.index("apply_row_corrections")
        assert built < corrected
        between = src[built:corrected]
        assert "return" not in between, (
            "a return between the parse and the correction would let raw rows out")

    def test_nothing_adds_an_offset_to_a_value_a_second_time(self):
        for forbidden in ("+ spec.correction", "+ float(spec.correction",
                          "+ self._machine.correction", "+ machine.correction"):
            assert forbidden not in SOURCE, (
                f"{forbidden!r} would double-apply the correction")

    @pytest.mark.parametrize("name", [
        "build_result_cells", "run_log_detail", "write_latest_result",
        "apply_csv_headers",
    ])
    def test_a_row_consumer_filters_on_the_reserved_keys(self, name):
        """Whatever a consumer does with a row, the bookkeeping is not a
        measurement — filtering on the Lab ID and timestamps alone is what let
        __raw__ out as a test name."""
        src = inspect.getsource(getattr(mod, name))
        assert "RESERVED_ROW_KEYS" in src

    @pytest.mark.parametrize("name", [
        "_publish_rows", "_results_can_accept", "_deliver_rows_to_results",
    ])
    def test_a_module_side_row_consumer_filters_on_the_reserved_keys(self, name):
        src = inspect.getsource(getattr(mod.LEMStationModule, name))
        assert "RESERVED_ROW_KEYS" in src


class TestOnePollCorrectsEverything:
    """One poll, end to end: every artefact it produces carries 62.5."""

    @pytest.fixture
    def polled(self, monkeypatch, qapp, tmp_path):
        module = make_module()
        written = []
        monkeypatch.setitem(mod.__dict__, "labcore_write",
                            lambda op, params=None, **kw: (written.append(
                                (op, params)), {"ok": True})[1])
        monkeypatch.setitem(mod.__dict__, "labcore_sql",
                            lambda sql, args=None, **kw: {"ok": True})
        # The lab logged its standard in, so this one print is judged as QC
        # *and* reported as a result. Without a `samples` answer the reading
        # would be a check only: correct, but not what this test is about.
        monkeypatch.setitem(
            mod.__dict__, "labcore_read_sql",
            lambda sql, args=None, **kw: (
                {"ok": True, "columns": ["lab_id"], "rows": [{"lab_id": "QC1"}]}
                if 'FROM "samples"' in sql else {"error": "offline"}))
        machine = bench()
        # The control's own Lab ID, so this one print is judged as QC *and*
        # reported as a result — the same row doing both is the point.
        payload = module._process_outcome(
            machine, ["QC1,65.5,0.84"], None, [], NOW)
        return module, machine, payload, written

    def test_the_parsed_row_is_corrected(self, polled):
        _, _, payload, _ = polled
        row = payload["rows"][0]
        assert row["Flash Point"] == pytest.approx(62.5)
        assert row["Density"] == pytest.approx(0.94)

    def test_the_raw_reading_rides_along_for_the_record(self, polled):
        _, _, payload, _ = polled
        assert payload["rows"][0][mod.RAW_KEY] == {"Flash Point": 65.5,
                                                   "Density": 0.84}

    def test_the_qc_verdict_is_made_on_the_corrected_value(self, polled):
        _, _, payload, _ = polled
        result = payload["evaluation"].test_results[0]
        assert result.name == "Flash Point"
        assert result.value == pytest.approx(62.5)
        assert result.raw_value == pytest.approx(65.5)
        assert result.in_spec is True

    def test_the_value_written_to_labcore_is_the_corrected_one(self, polled):
        _, _, _, written = polled
        cells = [op["params"] for _, params in written
                 for op in (params or {}).get("operations", [])
                 if op.get("operation") == "update_cell"]
        by_test = {c["test_name"]: c["value"] for c in cells}
        assert by_test["Flash Point"] == "62.5"
        assert by_test["Density"] == "0.94"

    def test_no_bookkeeping_is_written_as_a_test(self, polled):
        _, _, _, written = polled
        names = [op["params"].get("test_name") for _, params in written
                 for op in (params or {}).get("operations", [])]
        assert mod.RAW_KEY not in names
        assert mod.CORRECTION_KEY not in names

    def test_the_latest_result_csv_carries_the_corrected_value(self, polled):
        _, machine, _, _ = polled
        path = (mod.labstation_dir() + "/"
                + mod.latest_result_filename(machine.title))
        header, values = [l.strip().split(",")
                          for l in open(path, encoding="utf-8").readlines()[:2]]
        assert values[header.index("Flash Point")] == "62.5"
        assert mod.RAW_KEY not in header


class TestQcOnlyDetects:
    """QC resolves which method is a control and judges it. Nothing else."""

    LIBRARY = [{"sample_id_val": "QC1", "name": "Diesel - AO25",
                "tests": [{"name": "Flash Point", "value_col": "Flash Point",
                           "expected": 62.5, "std_dev": 1.0, "k": 2.0}]}]

    def test_no_assignment_means_no_spec(self):
        assert mod.specs_from_qc_samples(bench(), self.LIBRARY, []) == []

    def test_an_assignment_resolves_the_spec(self):
        specs = mod.specs_from_qc_samples(
            bench(), self.LIBRARY,
            [{"sample": "Diesel - AO25", "test": "Flash Point"}])
        assert [s.name for s in specs] == ["Flash Point"]

    def test_a_qc_test_name_is_always_a_method_this_bench_reports(self):
        machine = bench()
        reported = {m for mapping in machine.mappings for m in mapping.methods}
        specs = mod.specs_from_qc_samples(
            machine, self.LIBRARY,
            [{"sample": "Diesel - AO25", "test": "Flash Point"}])
        assert all(s.name in reported for s in specs), (
            "LEM has no test names of its own — they come from the mapping, "
            "matched against LabCore's standards")

    def test_qc_does_not_parse_anything_itself(self):
        """Calls, not mentions — the comments name both on purpose."""
        import ast
        tree = ast.parse(inspect.getsource(mod.evaluate_machine))
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "parse_print" not in called
        assert "apply_row_corrections" not in called

    def test_qc_reads_the_same_row_the_customer_result_comes_from(self):
        """One print, one corrected row: the number the control is judged on
        is the number reported for the sample."""
        machine = bench()
        rows = mod.apply_row_corrections(
            [{LAB_ID_KEY: "QC1", "Flash Point": 65.5}], machine.corrections)
        evaluation = mod.evaluate_machine(machine, rows, NOW)
        # The lab keeps its standards in `samples`, so the check is reported as
        # a result as well as judged — see split_qc_standards.
        reported = mod.build_result_cells(rows, {"QC1": "QC1"})
        cell = [op["params"] for op in reported
                if op["operation"] == "update_cell"][0]
        assert evaluation.test_results[0].value == pytest.approx(62.5)
        assert cell["value"] == "62.5"

    def test_the_offsets_come_from_one_map(self):
        """`Machine.corrections` is the authority; a spec's copy is display."""
        machine = bench()
        assert machine.corrections["Flash Point"] == pytest.approx(-3.0)
        assert machine.tests[0].correction == pytest.approx(-3.0)
        machine.tests[0].correction = 99.0     # display copy goes stale
        rows = mod.apply_row_corrections(
            [{LAB_ID_KEY: "L-1", "Flash Point": 65.5}], machine.corrections)
        assert rows[0]["Flash Point"] == pytest.approx(62.5), (
            "the map corrects, not the spec")


class TestTheRememberedVerdictUsesTheSameBand:
    """A verdict read back from LabCore after a restart must be judged by the
    same band as a live reading — `spec_band` exists so the two cannot drift."""

    def test_a_remembered_reading_agrees_with_a_live_one(self):
        spec = TestSpec(name="Flash Point", value_col="Flash Point",
                        expected=62.5, std_dev=1.0, k=2.0, sample_id="QC1")
        low, high = mod.spec_band(spec)
        edge = high - 0.01

        live = Machine(uid="m", title="t", tests=[spec])
        live_eval = mod.evaluate_machine(
            live, [{LAB_ID_KEY: "QC1", "Flash Point": edge}], NOW)

        remembered_spec = TestSpec(
            name="Flash Point", value_col="Flash Point", expected=62.5,
            std_dev=1.0, k=2.0, sample_id="QC1",
            last_qc_at=NOW.isoformat(), last_qc_value=edge)
        remembered = Machine(uid="m", title="t", tests=[remembered_spec])
        remembered_eval = mod.evaluate_machine(remembered, [], NOW)

        assert live_eval.test_results[0].in_spec is True
        assert remembered_eval.test_results[0].in_spec is True

    def test_the_band_is_not_recomputed_by_hand(self):
        """Every judgement goes through spec_band, so a change to the band is
        a change everywhere."""
        src = inspect.getsource(mod.evaluate_machine)
        assert "spec.k * spec.std_dev" not in src, (
            "inline band arithmetic can drift from spec_band")
