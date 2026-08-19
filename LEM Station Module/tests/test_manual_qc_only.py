"""QC is assigned, never guessed.

Ryan, 2026-08-03: "it auto assigns a QC because its assigned the test, but I cant
apply a correction factor until I manually assign a QC. So please make it all
manual and skip the automatic detection. (example multitek NS)"

What it used to do: `specs_from_qc_samples` filtered by the master view's targets
**only if there were any**. With none it detected on its own — any method the
parser produced that some shared standard happened to certify became a live QC
spec. So Multitek NS was RED on a Sulfur check nobody had assigned to it, and
because nothing was assigned there was no assignment to hang a correction factor
on either.

Now the assignment is the only way in:
  * `lem_machine_targets` — assigned from the floor ("Assign QC samples")
  * `lem_qc_specs`        — a per-machine numeric override

No assignment means no QC, which reads as grey "No QC assigned" — the honest state
for a bench nobody has told the system how to check.
"""
import pytest

import lem_station_module as mod
from lem_station_module import Machine, MethodMapping, Selector


LIBRARY = [
    {"name": "Diesel - AO25", "sample_id_val": "AO25", "tests": [
        {"name": "ASTM D5453 - Sulfur", "value_col": "ASTM D5453 - Sulfur",
         "expected": 5.93, "std_dev": 0.6, "k": 2.0, "units": "mg/kg"},
        {"name": "Flash", "value_col": "Flash", "expected": 63.72,
         "std_dev": 1.05, "k": 2.0, "units": "C"}]},
    {"name": "Cloud CRM", "sample_id_val": "CP", "tests": [
        {"name": "Cloud", "value_col": "Cloud", "expected": -7.4,
         "std_dev": 2.8, "k": 1.0, "units": "C"}]},
]


def machine(methods=("ASTM D5453 - Sulfur",)):
    return Machine(uid="844337a2ba08", title="Multitek NS",
                   mappings=[MethodMapping(methods=list(methods))])


# ── nothing assigned means nothing checked ──────────────────────────────────

class TestNoAssignmentNoQc:
    def test_the_reported_case_multitek_ns(self):
        """It parses Sulfur, a standard certifies Sulfur, nobody assigned it."""
        specs = mod.specs_from_qc_samples(machine(), LIBRARY, targets=[])
        assert specs == [], [s.name for s in specs]

    def test_targets_of_none_is_the_same_as_empty(self):
        """`None` used to mean "detect freely", which is how this happened."""
        assert mod.specs_from_qc_samples(machine(), LIBRARY, targets=None) == []

    def test_a_machine_with_no_specs_reads_grey_not_red(self):
        from datetime import datetime
        m = machine()
        m.tests = mod.specs_from_qc_samples(m, LIBRARY, targets=[])
        ev = mod.evaluate_machine(m, [], datetime(2026, 8, 3, 18, 0))
        assert ev.status == mod.STATUS_UNKNOWN
        assert "No QC assigned" in ev.reason

    def test_it_does_not_invent_specs_for_every_method_it_can_parse(self):
        many = machine(methods=("ASTM D5453 - Sulfur", "Flash", "Cloud"))
        assert mod.specs_from_qc_samples(many, LIBRARY, targets=[]) == []


# ── an assignment turns it on ───────────────────────────────────────────────

class TestAssignmentIsTheWayIn:
    def test_one_assigned_pair_gives_one_spec(self):
        specs = mod.specs_from_qc_samples(
            machine(), LIBRARY,
            targets=[{"sample": "Diesel - AO25", "test": "ASTM D5453 - Sulfur"}])
        assert [s.name for s in specs] == ["ASTM D5453 - Sulfur"]
        assert specs[0].expected == pytest.approx(5.93)
        assert specs[0].sample_id == "AO25"

    def test_only_the_assigned_test_not_the_whole_standard(self):
        """Diesel - AO25 certifies Sulfur AND Flash; assigning one must not
        quietly enable the other."""
        many = machine(methods=("ASTM D5453 - Sulfur", "Flash"))
        specs = mod.specs_from_qc_samples(
            many, LIBRARY,
            targets=[{"sample": "Diesel - AO25", "test": "ASTM D5453 - Sulfur"}])
        assert [s.name for s in specs] == ["ASTM D5453 - Sulfur"]

    def test_two_assignments_give_two_specs(self):
        many = machine(methods=("ASTM D5453 - Sulfur", "Cloud"))
        specs = mod.specs_from_qc_samples(many, LIBRARY, targets=[
            {"sample": "Diesel - AO25", "test": "ASTM D5453 - Sulfur"},
            {"sample": "Cloud CRM", "test": "Cloud"}])
        assert sorted(s.name for s in specs) == ["ASTM D5453 - Sulfur", "Cloud"]

    def test_an_assignment_for_a_method_this_bench_cannot_parse_is_inert(self):
        """Assigning a test the parser never produces must not fabricate a spec
        that can only ever read "awaiting QC"."""
        specs = mod.specs_from_qc_samples(
            machine(methods=("Flash",)), LIBRARY,
            targets=[{"sample": "Diesel - AO25", "test": "ASTM D5453 - Sulfur"}])
        assert specs == []

    def test_the_assignment_matches_on_either_name_or_column(self):
        """Definitions carried over from the old LEM name the column, not the
        method — both still resolve."""
        lib = [{"name": "S", "sample_id_val": "S1", "tests": [
            {"name": "Sulfur by UV", "value_col": "SULF", "expected": 1.0,
             "std_dev": 0.1, "k": 2.0, "units": "ppm"}]}]
        m = machine(methods=("SULF",))
        by_col = mod.specs_from_qc_samples(
            m, lib, targets=[{"sample": "S", "test": "SULF"}])
        by_name = mod.specs_from_qc_samples(
            m, lib, targets=[{"sample": "S", "test": "Sulfur by UV"}])
        assert len(by_col) == 1 and len(by_name) == 1

    def test_an_assignment_naming_the_wrong_standard_does_nothing(self):
        specs = mod.specs_from_qc_samples(
            machine(), LIBRARY,
            targets=[{"sample": "Cloud CRM", "test": "ASTM D5453 - Sulfur"}])
        assert specs == []


# ── the override channel still works ────────────────────────────────────────

class TestPerMachineOverrideStillApplies:
    def test_an_explicit_spec_row_applies_via_the_marked_mapping(self):
        """`lem_qc_specs` names the machine and test outright, so it needs no
        floor assignment — but the mapping must still declare WHICH standard it
        runs (`qc_sample_id`). Both halves are deliberate manual acts, which is
        what "all manual" means."""
        rows = [{"machine_uid": "844337a2ba08",
                 "test_name": "ASTM D5453 - Sulfur", "sample_id": "AO25",
                 "expected": 6.0, "std_dev": 0.5, "k": 2.0, "units": "mg/kg"}]
        m = Machine(uid="844337a2ba08", title="Multitek NS",
                    mappings=[MethodMapping(methods=["ASTM D5453 - Sulfur"],
                                            qc_sample_id="AO25")])
        specs = mod.specs_for_machine(
            m, mod.parse_qc_specs(rows, "844337a2ba08"))
        assert [s.name for s in specs] == ["ASTM D5453 - Sulfur"]
        assert specs[0].expected == pytest.approx(6.0)

    def test_an_unmarked_mapping_yields_nothing_even_with_a_spec_row(self):
        rows = [{"machine_uid": "844337a2ba08",
                 "test_name": "ASTM D5453 - Sulfur", "sample_id": "AO25",
                 "expected": 6.0, "std_dev": 0.5, "k": 2.0, "units": "mg/kg"}]
        assert mod.specs_for_machine(
            machine(), mod.parse_qc_specs(rows, "844337a2ba08")) == []
