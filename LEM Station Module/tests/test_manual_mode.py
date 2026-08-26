"""Manual entry: QC on a bench that cannot print.

Ryan, 2026-08-06: "a mode in the module that doesnt track parsing, but lets you
QC older machines that dont have parsing capability ... in addition to single,
multi, serial, theres another mode that lets you put it in manually. no parsing
just QC." And then, narrowing it: "remove the Lab ID / QC Sample part, and the
parse log beneath it, this is only to put in the QC result. Nothing else, if
there is no QC assigned then it can't put any data in. That QC must be assigned
(the machine can be created and the QC assigned in LEM later, but it wont be
able to put any data in until it detects the QC to compare against)."

So this is not a general data-entry bench. It is a **control panel**: the only
thing enterable is a reading for a QC test the master view has assigned, and the
standard's Lab ID comes from the assignment rather than from anybody's typing.
Nothing assigned means nothing enterable — which is the honest state, and the
one that makes an unassigned bench impossible to fill with results nobody can
check.

The assignment is also what declares the bench's methods. A parsing machine's
mappings say what it reports; a manual one has no mappings and nothing else
says it, so `specs_from_qc_samples` reads the floor's targets directly. Create
the machine, assign QC in LEM, and the boxes appear.

Everything after the row is the SAME path a parsed print takes: corrections at
the row boundary, the QC verdict, the LabCore write, the 'qc' log event, the
card, the live push.
"""
from datetime import datetime

import pytest

import lem_station_module as mod
from lem_station_module import Machine, MethodMapping, Selector, TestSpec

from test_module_qt import make_module

NOW = datetime(2026, 8, 6, 9, 30, 0)

FLASH = TestSpec(name="Flash Point", value_col="Flash Point", expected=63.72,
                 std_dev=1.05, k=2.0, units="C", sample_id="AO25")

LIBRARY = [{"name": "Diesel - AO25", "sample_id_val": "AO25", "tests": [
    {"name": "Flash Point", "value_col": "Flash Point", "expected": 63.72,
     "std_dev": 1.05, "k": 2.0, "units": "C"},
    {"name": "Sulfur", "value_col": "ASTM D5453 - Sulfur", "expected": 5.93,
     "std_dev": 0.6, "k": 2.0, "units": "mg/kg"}]}]


def manual_machine(**overrides):
    """A manual bench with one QC test assigned to it."""
    base = dict(uid="old-flash", title="Herzog Flash (manual)",
                source_type="manual", tests=[FLASH])
    base.update(overrides)
    return Machine(**base)


def unassigned_machine(**overrides):
    """Created, but the master view has not assigned it any QC yet."""
    return manual_machine(tests=[], **overrides)


# ── The source type itself ───────────────────────────────────────────────────

class TestManualIsAFourthSource:
    def test_it_joins_single_multi_and_serial(self):
        assert mod.SOURCE_TYPES == ("single_csv", "multi_csv", "serial",
                                    "manual")

    def test_a_manual_machine_round_trips_through_labcore(self):
        m = manual_machine()
        assert Machine.from_dict(m.to_dict()).source_type == "manual"

    def test_a_manual_machine_parses_nothing(self):
        assert mod.parse_print(manual_machine(), "63.7,AO25,junk").values == {}


# ── The assignment is the declaration ────────────────────────────────────────

class TestQcAssignmentIsWhatDeclaresTheBench:
    """A parsing bench's mappings say what it reports. A manual one has no
    mappings and nothing else says it, so the floor's assignment is read
    directly — otherwise a machine created for manual QC could never be given
    any, and "assign the QC in LEM later" would not work."""

    TARGET = [{"sample": "Diesel - AO25", "test": "Flash Point"}]

    def test_an_assignment_resolves_with_no_mappings_at_all(self):
        specs = mod.specs_from_qc_samples(unassigned_machine(), LIBRARY,
                                          targets=self.TARGET)
        assert [s.name for s in specs] == ["Flash Point"]

    def test_the_standards_lab_id_comes_with_it(self):
        specs = mod.specs_from_qc_samples(unassigned_machine(), LIBRARY,
                                          targets=self.TARGET)
        assert specs[0].sample_id == "AO25"
        assert (specs[0].expected, specs[0].std_dev) == (63.72, 1.05)

    def test_nothing_assigned_is_still_nothing_checked(self):
        """The rule that closed the Multitek NS case holds here too: a manual
        bench must not pick up a standard nobody assigned it."""
        assert mod.specs_from_qc_samples(unassigned_machine(), LIBRARY,
                                         targets=[]) == []
        assert mod.specs_from_qc_samples(unassigned_machine(), LIBRARY,
                                         targets=None) == []

    def test_only_the_assigned_test_of_a_multi_test_standard(self):
        specs = mod.specs_from_qc_samples(unassigned_machine(), LIBRARY,
                                          targets=self.TARGET)
        assert [s.name for s in specs] == ["Flash Point"]

    def test_a_test_is_named_by_its_measurement_column(self):
        """`Sulfur` is assigned but LabCore's method is the value_col — that is
        the name the result must be written under."""
        specs = mod.specs_from_qc_samples(
            unassigned_machine(), LIBRARY,
            targets=[{"sample": "Diesel - AO25", "test": "Sulfur"}])
        assert [s.name for s in specs] == ["ASTM D5453 - Sulfur"]

    def test_a_parsing_bench_is_untouched_by_this(self):
        """The mappings filter still governs everywhere else."""
        parsing = Machine(uid="p", source_type="single_csv",
                          mappings=[MethodMapping(methods=["Flash Point"])])
        assert [s.name for s in mod.specs_from_qc_samples(
            parsing, LIBRARY, targets=self.TARGET)] == ["Flash Point"]
        no_mapping = Machine(uid="p", source_type="single_csv")
        assert mod.specs_from_qc_samples(no_mapping, LIBRARY,
                                         targets=self.TARGET) == []


class TestAPerMachineOverride:
    """`lem_qc_specs` is the other way in. A manual bench has no mapping to
    carry the QC sample, so a row written FOR it is the assignment itself."""

    ROW = {"machine_uid": "old-flash", "test_name": "Flash Point",
           "expected": 63.72, "std_dev": 1.05, "k": 2.0, "units": "C",
           "sample_id": "AO25"}

    def test_a_row_written_for_this_machine_assigns_it(self):
        specs = mod.specs_for_machine(
            manual_machine(), mod.parse_qc_specs([self.ROW], "old-flash"))
        assert [(s.name, s.sample_id) for s in specs] == [("Flash Point",
                                                           "AO25")]

    def test_an_unscoped_row_assigns_nothing(self):
        """A global row is a value, not an assignment — auto-adopting it is
        exactly the detection that put Multitek NS on RED."""
        loose = dict(self.ROW, machine_uid="")
        assert mod.machine_scoped_qc_rows([loose], "old-flash") == []
        assert mod.machine_scoped_qc_rows([self.ROW], "old-flash") == [self.ROW]

    def test_a_row_naming_no_standard_assigns_nothing(self):
        """Without a Lab ID there is no standard to log the reading against."""
        anonymous = dict(self.ROW, sample_id="")
        assert mod.specs_for_machine(
            manual_machine(), mod.parse_qc_specs([anonymous],
                                                 "old-flash")) == []


# ── What the entry box offers ────────────────────────────────────────────────

class TestWhatCanBeEntered:
    def test_only_the_assigned_qc_tests(self):
        assert [s.name for s in mod.manual_entry_specs(manual_machine())] == \
            ["Flash Point"]

    def test_nothing_assigned_means_nothing_enterable(self):
        assert mod.manual_entry_specs(unassigned_machine()) == []

    def test_mappings_are_not_a_way_in(self):
        """This is a QC panel, not a data-entry form: a method the machine
        happens to have a mapping for is not something to type a result for."""
        m = unassigned_machine(
            mappings=[MethodMapping(methods=["Density"])])
        assert mod.manual_entry_specs(m) == []

    def test_a_spec_with_no_standard_cannot_be_entered(self):
        """The Lab ID is the standard's, taken from the assignment. With none
        there is nothing to log the reading against."""
        loose = TestSpec(name="Flash Point", value_col="Flash Point",
                         expected=63.72, std_dev=1.05, k=2.0)
        assert mod.manual_entry_specs(manual_machine(tests=[loose])) == []


# ── The row an operator types ────────────────────────────────────────────────

class TestTheTypedRow:
    def test_it_has_the_shape_a_parsed_print_has(self):
        row = mod.manual_qc_row(FLASH, "63.9", NOW)
        assert row == {mod.LAB_ID_KEY: "AO25", "Flash Point": "63.9",
                       "parsed_date": "2026-08-06", "parsed_time": "09:30:00"}

    def test_the_lab_id_is_the_standards_not_a_typed_one(self):
        assert mod.manual_qc_row(FLASH, "63.9", NOW)[mod.LAB_ID_KEY] == "AO25"

    def test_it_trims_what_was_typed(self):
        assert mod.manual_qc_row(FLASH, "  63.9 ", NOW)["Flash Point"] == "63.9"

    def test_an_empty_box_is_no_row(self):
        assert mod.manual_qc_row(FLASH, "   ", NOW) is None

    def test_a_reading_that_is_not_a_number_is_no_row(self):
        """A QC result is a measurement to compare against a band. "ok" is not
        one, and writing it would put an unjudgeable value in the record."""
        assert mod.manual_qc_row(FLASH, "ok", NOW) is None

    def test_no_spec_is_no_row(self):
        assert mod.manual_qc_row(None, "63.9", NOW) is None


# ── What the floor is told ───────────────────────────────────────────────────

class TestWhatTheFloorIsTold:
    def test_the_heartbeat_does_not_claim_a_file(self):
        _sql, args = mod.build_heartbeat_upsert(manual_machine(), NOW)
        assert args[2] == "manual entry (no parsing)"

    def test_and_says_so_when_the_watch_is_stopped(self):
        _sql, args = mod.build_heartbeat_upsert(manual_machine(), NOW,
                                                polling=False)
        assert args[2].startswith("idle (not watching)")


# ── The module: no ingest, and an entry that behaves like a print ────────────

class TestManualBenchNeverIngests:
    def test_no_file_error_from_a_machine_that_has_no_file(self, qapp):
        m = make_module()
        m.set_machine(manual_machine(csv_path="/nope/does/not/exist.csv"),
                      publish=False)
        machine, prints, error = m._ingest(m.machine())
        assert (prints, error) == ([], None)

    def test_a_poll_still_evaluates_qc(self, qapp):
        """Polling is what keeps QC freshness, PM/Cal and the heartbeat
        moving — a manual bench polls like any other, it just reads nothing."""
        m = make_module()
        m.set_machine(manual_machine(), publish=False)
        m.process_now(now=NOW)
        assert m.evaluation() is not None
        assert "error" not in (m.evaluation().reason or "").lower()


class TestLoggingAQcResult:
    def logged(self, qapp, machine=None, method="Flash Point", value="63.9",
               now=NOW):
        m = make_module()
        m.set_machine(machine or manual_machine(), publish=False)
        m.log_manual_entry(method, value, now=now)
        return m

    @staticmethod
    def sample_events(m):
        """The run/qc events queued for lem_machine_log (kind is args[2]),
        without the status_change every first evaluation also queues."""
        return [args[2] for _sql, args in m._pending_events
                if args[2] in ("run", "qc")]

    def test_the_reading_becomes_a_row_in_history(self, qapp):
        m = self.logged(qapp)
        assert [r["Flash Point"] for r in m._history] == ["63.9"]

    def test_it_is_judged_against_the_assigned_spec(self, qapp):
        """A typed reading inside the band reads GREEN.

        Logged at the REAL clock, not at the module-wide `NOW`. GREEN is the
        only assertion in this class that also depends on the QC being fresh,
        and `evaluate_machine` measures freshness against the wall clock — so
        pinned to a fixed date this passed on the day it was written and turned
        YELLOW ("QC stale: Flash Point") a few weeks later, which it did.
        Nothing here is about staleness; `test_qc_window.py` owns that rule and
        passes its own clock. So the reading is made recent and the test says
        only what it means to say.
        """
        m = self.logged(qapp, now=datetime.now())
        assert m.evaluation().status == mod.STATUS_GREEN
        result = m.evaluation().test_results[0]
        assert result.name == "Flash Point" and result.in_spec is True

    def test_an_out_of_band_reading_goes_red(self, qapp):
        m = self.logged(qapp, value="70.0")
        assert m.evaluation().status == mod.STATUS_RED

    def test_the_correction_factor_applies_to_a_typed_value(self, qapp):
        """ISO/IEC 17025 §7.8.2 — the reported result is the corrected one, and
        a value typed by hand is a measurement like any other."""
        machine = manual_machine()
        mod.apply_corrections(machine, {"Flash Point": -3.0})
        m = self.logged(qapp, machine=machine, value="66.7")
        row = list(m._history)[-1]
        assert row["Flash Point"] == 63.7
        assert row[mod.RAW_KEY]["Flash Point"] == 66.7

    def test_it_goes_out_on_the_result_bus_under_the_standards_lab_id(self,
                                                                     qapp):
        m = self.logged(qapp)
        assert ("AO25", "Flash Point", "63.9", "LEM Station") in \
            m.context.results

    def test_it_is_logged_as_a_qc_verdict_never_a_run(self, qapp):
        """It is a check against a standard, by construction — there is no
        other kind of entry this bench can make."""
        assert self.sample_events(self.logged(qapp)) == ["qc"]

    def test_the_verdict_reaches_the_card(self, qapp):
        m = self.logged(qapp)
        assert "63.9" in m.card().qc_rows()[0].value_text()

    def test_a_test_that_is_not_assigned_is_refused(self, qapp):
        m = self.logged(qapp, method="Density", value="0.84")
        assert list(m._history) == []

    def test_a_bench_with_no_qc_assigned_can_log_nothing(self, qapp):
        """"if there is no QC assigned then it can't put any data in"."""
        m = self.logged(qapp, machine=unassigned_machine())
        assert list(m._history) == []
        assert m.context.results == []

    def test_an_empty_box_logs_nothing(self, qapp):
        m = self.logged(qapp, value="")
        assert list(m._history) == []


# ── The window: an entry box where the data drop-down was ────────────────────

class TestTheEntryBox:
    def module(self, machine=None, source_type="manual", **kwargs):
        m = make_module()
        m.set_machine(machine or manual_machine(source_type=source_type,
                                                **kwargs), publish=False)
        return m

    def test_a_manual_bench_shows_the_entry_bar(self, qapp):
        m = self.module()
        assert m.manual_bar().isVisibleTo(m)

    def test_and_hides_the_data_drop_down(self, qapp):
        m = self.module()
        assert not m._data_toggle.isVisibleTo(m)

    def test_and_the_parse_log_beneath_it(self, qapp):
        """"remove ... the parse log beneath it" — there are no parsed prints
        on this bench, and the QC readings show on the card."""
        m = self.module()
        assert not m._data_table.isVisibleTo(m)

    def test_there_is_nowhere_to_type_a_lab_id(self, qapp):
        """The standard's Lab ID comes from the assignment. A box for it is a
        way to log a QC result against the wrong standard."""
        assert not hasattr(self.module(), "_manual_lab_id")

    def test_a_parsing_bench_shows_the_drop_down_and_no_entry_bar(self, qapp):
        m = self.module(source_type="single_csv")
        assert m._data_toggle.isVisibleTo(m)
        assert not m.manual_bar().isVisibleTo(m)

    def test_the_method_button_offers_the_assigned_qc_tests(self, qapp):
        m = self.module(machine=manual_machine(tests=[
            FLASH, TestSpec(name="Density", value_col="Density", expected=0.84,
                            std_dev=0.01, k=2.0, sample_id="AO25")]))
        labels = [a.text() for a in m._manual_method_btn.menu().actions()]
        assert labels == ["Flash Point", "Density"]

    def test_typing_and_clicking_log_records_the_reading(self, qapp):
        m = self.module()
        m._pick_manual_method("Flash Point")
        m._manual_value.setText("63.9")
        m._on_log_manual()
        assert list(m._history)[-1]["Flash Point"] == "63.9"

    def test_the_box_clears_after_a_successful_log(self, qapp):
        m = self.module()
        m._pick_manual_method("Flash Point")
        m._manual_value.setText("63.9")
        m._on_log_manual()
        assert m._manual_value.text() == ""

    def test_the_only_assigned_test_is_pre_picked(self, qapp):
        """One control, one box — nothing to choose between."""
        assert self.module()._manual_method == "Flash Point"


class TestNothingAssignedLocksTheBox:
    def unassigned(self):
        m = make_module()
        m.set_machine(unassigned_machine(), publish=False)
        return m

    def test_the_entry_is_disabled(self, qapp):
        m = self.unassigned()
        assert not m._manual_value.isEnabled()
        assert not m._manual_log_btn.isEnabled()
        assert not m._manual_method_btn.isEnabled()

    def test_it_says_why(self, qapp):
        assert "assign" in self.unassigned()._manual_note.text().lower()

    def test_and_typing_into_it_anyway_writes_nothing(self, qapp):
        m = self.unassigned()
        m._manual_value.setText("63.9")
        m._on_log_manual()
        assert list(m._history) == []

    def test_qc_assigned_on_a_later_poll_unlocks_it(self, qapp):
        """"the machine can be created and the QC assigned in LEM later" — the
        assignment lands on a poll, and the box has to open then."""
        m = self.unassigned()
        m.machine().tests = [FLASH]
        m.process_now(now=NOW)
        assert m._manual_value.isEnabled()
        assert [a.text() for a in m._manual_method_btn.menu().actions()] == \
            ["Flash Point"]

    def test_a_click_during_a_poll_is_not_silently_dropped(self, qapp):
        """A poll can sit on LabCore HTTP for seconds. The pipeline refuses a
        second run, so the operator has to be told — otherwise the click looks
        like it worked and the reading is gone."""
        m = make_module()
        m.set_machine(manual_machine(), publish=False)
        m._manual_value.setText("63.9")
        m._polling = True
        m._on_log_manual()
        assert list(m._history) == []
        assert "busy" in m._status_label.text().lower()
        assert m._manual_value.text() == "63.9"  # still there to retry

    def test_switching_the_machine_to_manual_swaps_the_section(self, qapp):
        m = make_module()
        m.set_machine(Machine(uid="p", source_type="single_csv"),
                      publish=False)
        m.set_machine(manual_machine(), publish=False)
        assert m.manual_bar().isVisibleTo(m)
        assert not m._data_toggle.isVisibleTo(m)


# ── The setup dialog ─────────────────────────────────────────────────────────

class TestTheSetupDialog:
    def test_manual_is_offered_as_a_source(self, qapp):
        d = mod._MachineDialog(Machine(uid="x"), None)
        labels = [a.text() for a in d._source_btn.menu().actions()]
        assert any("Manual" in label for label in labels)

    def test_picking_manual_hides_the_file_and_serial_rows(self, qapp):
        d = mod._MachineDialog(Machine(uid="x"), None)
        d._pick_source("manual", "Manual entry (no parsing)")
        assert not d._file_label.isVisibleTo(d)
        assert not d._serial_label.isVisibleTo(d)

    def test_manual_replaces_the_mapping_area(self, qapp):
        d = mod._MachineDialog(Machine(uid="x", template="a,b"), None)
        d._pick_source("manual", "Manual entry (no parsing)")
        assert not d._mapping_area.isVisibleTo(d)
        assert d._manual_area.isVisibleTo(d)

    def test_it_says_where_the_qc_comes_from(self, qapp):
        d = mod._MachineDialog(Machine(uid="x"), None)
        assert "assign" in d._manual_note.text().lower()

    def test_a_manual_bench_is_never_waiting_for_a_print(self, qapp):
        """The "waiting for the first print" hint gates a parsing setup. On a
        manual bench no print is ever coming, so it must not appear."""
        d = mod._MachineDialog(Machine(uid="x"), None)
        d._pick_source("manual", "Manual entry (no parsing)")
        assert not d._waiting_label.isVisibleTo(d)

    def test_there_is_nothing_to_declare(self, qapp):
        """Setup is the source and the name; the QC assignment does the rest."""
        d = mod._MachineDialog(Machine(uid="x"), None)
        d._pick_source("manual", "Manual entry (no parsing)")
        machine = Machine(uid="x")
        d._write_fields_into(machine)
        assert machine.source_type == "manual"
        assert machine.mappings == []

    def test_switching_to_manual_keeps_a_parsers_mappings(self, qapp):
        """Switched by mistake and switched back, the parse setup is still
        there — manual mode ignores mappings rather than erasing them."""
        parsing = Machine(uid="x", template="a,b",
                          mappings=[MethodMapping(
                              methods=["Flash Point"],
                              selector=Selector(mode="cell", index=1))])
        d = mod._MachineDialog(parsing, None)
        d._pick_source("manual", "Manual entry (no parsing)")
        d._on_accept()
        assert [m.methods for m in parsing.mappings] == [["Flash Point"]]

    def test_switching_away_from_manual_restores_the_mapping_area(self, qapp):
        d = mod._MachineDialog(Machine(uid="x", template="a,b"), None)
        d._pick_source("manual", "Manual entry (no parsing)")
        d._pick_source("single_csv", "Single CSV (tail a file)")
        assert d._mapping_area.isVisibleTo(d)
        assert not d._manual_area.isVisibleTo(d)
