"""A correction factor applies to EVERY measurement, not only the QC standard.

Ryan, 2026-08-04: "You need to have the parsers apply the correction factor to every
measurement, not just the QC sample ... We are complying with ISO 17025 section 7".

This was wrong, and wrong in the direction that matters. The correction was applied
in `evaluate_machine`, which only ever sees the machine's QC specs — so PAC Flash 2's
-3.0 °C adjusted its QC verdict while **every customer sample went to LabCore raw**.
The correction exists to make the *reported result* right; correcting only the control
and not the samples is precisely backwards.

ISO/IEC 17025:2017 §7 is explicit about what that requires:

  * §7.8.2 — reported results must be accurate and include what is needed to
    interpret them. A result reported without its correction applied is not the
    measurement result.
  * §7.5.1 — technical records must let the measurement be reconstructed. So the
    **raw reading, the correction applied, and the corrected value** are all kept:
    the corrected value is what is reported, the pair behind it is what makes it
    auditable.
  * §7.11.3 — data must be protected against loss or alteration; a correction that
    silently rewrote history would violate that, so already-recorded results are
    never restated (see TestFromThisPointOn in test_correction_factors.py).

So the correction is applied ONCE, at the parse boundary, and everything downstream —
the QC verdict, the LabCore result write, the history, the display — sees the same
corrected number. No consumer can forget it, because no consumer applies it.
"""
from datetime import datetime

import pytest

import lem_station_module as mod
from lem_station_module import LAB_ID_KEY, Machine, TestSpec

NOW = datetime(2026, 8, 4, 9, 0)


def machine(**over):
    base = dict(uid="7e8304c31983", title="PAC Flash 2",
                corrections={"Flash": -3.0})
    base.update(over)
    return Machine(**base)


def row(**values):
    r = {LAB_ID_KEY: values.pop("lab_id", "SAMPLE-1"),
         "parsed_date": "2026-08-04", "parsed_time": "08:55:00"}
    r.update(values)
    return r


# ── the reported result carries the correction ──────────────────────────────

class TestEveryMeasurementIsCorrected:
    def test_a_customer_sample_is_corrected(self):
        """The whole point: not a QC standard, no spec assigned, still corrected."""
        rows = mod.apply_row_corrections([row(Flash=66.5)], {"Flash": -3.0})
        assert rows[0]["Flash"] == pytest.approx(63.5)

    def test_the_value_written_to_labcore_is_the_corrected_one(self):
        rows = mod.apply_row_corrections([row(Flash=66.5)], {"Flash": -3.0})
        ops = mod.build_result_cells(rows, {"SAMPLE-1": "SAMPLE-1"})
        cell = [o for o in ops if o["operation"] == "update_cell"
                and o["params"]["test_name"] == "Flash"][0]
        assert cell["params"]["value"] == "63.5"

    def test_an_uncorrected_method_on_the_same_print_is_untouched(self):
        rows = mod.apply_row_corrections(
            [row(Flash=66.5, Viscosity=2.64)], {"Flash": -3.0})
        assert rows[0]["Viscosity"] == pytest.approx(2.64)

    def test_several_corrections_apply_independently(self):
        """Agilent GC has five distillation offsets on one print."""
        corr = {"IBP": -12.08, "10%": -5.25, "50%": -4.06}
        rows = mod.apply_row_corrections(
            [row(IBP=180.0, **{"10%": 214.0, "50%": 270.0})], corr)
        assert rows[0]["IBP"] == pytest.approx(167.92)
        assert rows[0]["10%"] == pytest.approx(208.75)
        assert rows[0]["50%"] == pytest.approx(265.94)

    def test_the_multitek_ns_sulfur_case(self):
        """+1.45 mg/kg, the real value from V4."""
        rows = mod.apply_row_corrections([row(Sulfur=7.384)], {"Sulfur": 1.45})
        assert rows[0]["Sulfur"] == pytest.approx(8.834)

    def test_every_row_in_a_burst_is_corrected(self):
        rows = mod.apply_row_corrections(
            [row(lab_id="A", Flash=66.5), row(lab_id="B", Flash=60.0)],
            {"Flash": -3.0})
        assert [r["Flash"] for r in rows] == [pytest.approx(63.5),
                                              pytest.approx(57.0)]

    def test_a_correction_of_zero_changes_nothing(self):
        rows = mod.apply_row_corrections([row(Flash=66.5)], {"Flash": 0.0})
        assert rows[0]["Flash"] == pytest.approx(66.5)

    def test_no_corrections_leaves_the_row_alone(self):
        original = row(Flash=66.5)
        assert mod.apply_row_corrections([dict(original)], {}) == [original]


# ── §7.5.1 the record must let it be reconstructed ──────────────────────────

class TestTheRawReadingIsKept:
    def test_the_raw_value_travels_with_the_row(self):
        rows = mod.apply_row_corrections([row(Flash=66.5)], {"Flash": -3.0})
        assert mod.row_raw(rows[0])["Flash"] == pytest.approx(66.5)

    def test_the_applied_correction_travels_with_the_row(self):
        rows = mod.apply_row_corrections([row(Flash=66.5)], {"Flash": -3.0})
        assert mod.row_corrections(rows[0])["Flash"] == pytest.approx(-3.0)

    def test_nothing_is_recorded_where_nothing_was_applied(self):
        """An empty correction record would imply a correction of zero was in
        force, which is a claim rather than a fact."""
        rows = mod.apply_row_corrections([row(Flash=66.5, Viscosity=2.64)],
                                         {"Flash": -3.0})
        assert "Viscosity" not in mod.row_raw(rows[0])

    def test_the_bookkeeping_is_not_written_to_labcore_as_a_result(self):
        """It must not turn up as a test method called __raw__."""
        rows = mod.apply_row_corrections([row(Flash=66.5)], {"Flash": -3.0})
        names = {o["params"].get("test_name")
                 for o in mod.build_result_cells(rows,
                                                 {"SAMPLE-1": "SAMPLE-1"})
                 if o["operation"] == "update_cell"}
        assert names == {"Flash"}

    def test_the_run_log_records_raw_and_correction(self):
        detail = mod.run_log_detail(
            mod.apply_row_corrections([row(Flash=66.5)], {"Flash": -3.0})[0])
        assert detail["values"]["Flash"] == pytest.approx(63.5)
        assert detail["raw"]["Flash"] == pytest.approx(66.5)
        assert detail["corrections"]["Flash"] == pytest.approx(-3.0)

    def test_an_uncorrected_run_logs_no_correction_keys(self):
        detail = mod.run_log_detail(row(Flash=66.5))
        assert "raw" not in detail and "corrections" not in detail


# ── applied exactly once ────────────────────────────────────────────────────

class TestAppliedOnceOnly:
    def test_the_qc_verdict_uses_the_already_corrected_value(self):
        """66.5 raw, -3.0 correction, band 61.62–65.82: corrected 63.5 passes.
        Applying it twice would give 60.5 and fail."""
        spec = TestSpec(name="Flash", value_col="Flash", expected=63.72,
                        std_dev=1.05, k=2.0, sample_id="AO25")
        m = machine(tests=[spec])
        rows = mod.apply_row_corrections(
            [row(lab_id="AO25", Flash=66.5)], m.corrections)
        ev = mod.evaluate_machine(m, rows, NOW)
        assert ev.test_results[0].value == pytest.approx(63.5)
        assert ev.test_results[0].in_spec is True

    def test_evaluate_machine_does_not_correct_again(self):
        """The spec still carries the number for display, but must not re-apply."""
        spec = TestSpec(name="Flash", value_col="Flash", expected=63.72,
                        std_dev=1.05, k=2.0, sample_id="AO25", correction=-3.0)
        ev = mod.evaluate_machine(machine(tests=[spec]),
                                  [row(lab_id="AO25", Flash=63.5)], NOW)
        assert ev.test_results[0].value == pytest.approx(63.5)

    def test_re_correcting_an_already_corrected_row_is_a_no_op(self):
        """Belt and braces: a row that has been through it once is marked."""
        once = mod.apply_row_corrections([row(Flash=66.5)], {"Flash": -3.0})
        twice = mod.apply_row_corrections(once, {"Flash": -3.0})
        assert twice[0]["Flash"] == pytest.approx(63.5)
        assert mod.row_raw(twice[0])["Flash"] == pytest.approx(66.5)


# ── the map covers methods with no QC assigned ──────────────────────────────

class TestCorrectionsAreNotTiedToQc:
    def test_a_method_with_no_spec_can_still_carry_a_correction(self):
        """QC is assignment-only now, so most methods have no spec at all — and
        they are exactly the ones producing customer results."""
        m = machine(tests=[])
        mod.apply_corrections(m, {"Flash": -3.0})
        assert m.corrections["Flash"] == pytest.approx(-3.0)

    def test_the_map_is_kept_on_the_machine_not_only_on_specs(self):
        m = machine(tests=[TestSpec(name="Other", value_col="Other",
                                    expected=1.0, std_dev=1.0)])
        mod.apply_corrections(m, {"Flash": -3.0, "Other": 0.5})
        assert m.corrections == {"Flash": pytest.approx(-3.0),
                                 "Other": pytest.approx(0.5)}
        assert m.tests[0].correction == pytest.approx(0.5)

    def test_removing_a_correction_clears_it_from_the_map(self):
        m = machine()
        mod.apply_corrections(m, {})
        assert m.corrections == {}


class TestTheFactorInForceIsTheOneApplied:
    """The correction applied must be the one in force when the sample was measured.

    The corrections were read inside `_labcore_sync`, which runs AFTER the parse — so
    a print arriving in the poll right after someone set a factor was reported with
    the PREVIOUS one. For ISO/IEC 17025 §7.8.2 that is a wrong reported result, not a
    minor lag.

    The read moved to the top of the poll instead of being duplicated, so the op count
    is unchanged.
    """

    def test_corrections_are_read_before_the_parse(self):
        import inspect
        src = inspect.getsource(mod.LEMStationModule._process_outcome)
        assert "_refresh_corrections" in src
        assert src.index("_refresh_corrections") < src.index("apply_row_corrections")

    def test_the_sync_no_longer_reads_them_again(self):
        """Moved, not added — the poll must not pay for two reads of the same thing."""
        import inspect
        src = inspect.getsource(mod.LEMStationModule._labcore_sync)
        assert "build_corrections_query" not in src

    def test_the_refresh_uses_one_read(self):
        reads = []

        class Mod:
            corrections_read = staticmethod(mod.build_corrections_query)

        def read_sql(sql, args=None, **kw):
            reads.append(sql)
            return {"rows": [{"test_name": "Flash", "correction": "-3.0"}]}

        m = machine(corrections={})
        applied = mod.refresh_corrections(m, read_sql)
        assert len(reads) == 1
        assert applied is True
        assert m.corrections["Flash"] == pytest.approx(-3.0)

    def test_an_unreadable_correction_table_keeps_what_it_had(self):
        """A busy queue must not silently drop a correction and report raw values —
        that would be a wrong result, which is worse than a stale one."""
        m = machine(corrections={"Flash": -3.0})
        applied = mod.refresh_corrections(
            m, lambda *a, **k: {"error": "read timed out"})
        assert applied is False
        assert m.corrections["Flash"] == pytest.approx(-3.0)

    def test_no_reader_at_all_keeps_what_it_had(self):
        m = machine(corrections={"Flash": -3.0})
        assert mod.refresh_corrections(m, None) is False
        assert m.corrections["Flash"] == pytest.approx(-3.0)

    def test_a_removed_correction_is_cleared(self):
        m = machine(corrections={"Flash": -3.0})
        assert mod.refresh_corrections(m, lambda *a, **k: {"rows": []}) is True
        assert m.corrections == {}


class TestEveryMethodCanBeGivenAFactor:
    """If corrections only apply to every measurement but can only be SET on
    QC-assigned tests, the compliance fix is unusable. QC is assignment-only, so most
    reported methods have no spec — and those are the customer results."""

    def qt(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QtWidgets = pytest.importorskip("PySide6.QtWidgets")
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def bench(self):
        """A bench reporting five methods, only one of which has QC assigned."""
        return Machine(
            uid="m1", title="Agilent GC",
            mappings=[mod.MethodMapping(methods=["IBP", "10%", "50%", "90%",
                                                 "FBP"])],
            tests=[TestSpec(name="IBP", value_col="IBP", expected=166.0,
                            std_dev=3.4, k=2.0, sample_id="AO25")])

    def test_the_method_list_covers_every_mapped_method(self):
        assert mod.correctable_methods(self.bench()) == [
            "10%", "50%", "90%", "FBP", "IBP"]

    def test_a_method_with_a_saved_correction_is_included(self):
        """Even if it is no longer mapped — otherwise it cannot be found to remove."""
        m = self.bench()
        m.corrections = {"Retired Method": 1.0}
        assert "Retired Method" in mod.correctable_methods(m)

    def test_it_does_not_duplicate(self):
        m = self.bench()
        m.corrections = {"IBP": -12.08}
        assert mod.correctable_methods(m).count("IBP") == 1

    def test_a_bench_with_no_mappings_yet_offers_its_qc_tests(self):
        m = Machine(uid="m1", title="T",
                    tests=[TestSpec(name="Flash", value_col="Flash",
                                    expected=1.0, std_dev=1.0)])
        assert mod.correctable_methods(m) == ["Flash"]

    def test_the_dialog_offers_a_row_for_every_method(self):
        self.qt()
        dlg = mod._CorrectionsDialog(self.bench(), None)
        for name in ("IBP", "10%", "50%", "90%", "FBP"):
            assert dlg.rows_for_test(name) == "0", name

    def test_a_correction_can_be_set_on_a_method_with_no_qc(self):
        self.qt()
        m = self.bench()
        dlg = mod._CorrectionsDialog(m, None)
        dlg.set_row("FBP", "-5.57")
        assert dlg.collect() == {"FBP": pytest.approx(-5.57)}

    def test_the_five_agilent_offsets_all_collect(self):
        """The real V4 values for Agilent GC."""
        self.qt()
        dlg = mod._CorrectionsDialog(self.bench(), None)
        want = {"IBP": -12.08, "10%": -5.25, "50%": -4.06, "90%": -3.46,
                "FBP": -5.57}
        for k, v in want.items():
            dlg.set_row(k, str(v))
        got = dlg.collect()
        assert {k: pytest.approx(v) for k, v in want.items()} == got
