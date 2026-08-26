"""The bench honours a window the STANDARD carries, and can say where it came from.

Ryan, 2026-08-26: *"make the QC staleness adjustable in the QC sample library."*

A control's usable life belongs to the MATERIAL, not to the instrument. Until
now the only levels that could state it were per-instrument — `MethodMapping`
(this bench, this mapping) and `Machine.qc_expire_hours` (this bench) — so an
ampoule that is good for eight hours had to be re-typed on every bench that runs
it, and a lot change could not carry the fact with it.

The chain, most specific first, and **zero means fall through at every level**:

    MethodMapping override -> the standard's own window -> the machine -> 24.0

`resolve_qc_window` is the one implementation of that order in this file;
`spec_qc_window` and `qc_window_for` are the two directions it is asked in.
Nothing resolves it inline any more.

**Absence is never zero.** The library rows already in LabCore carry no such
key, and `/api/bench/<uid>/config` on an older floor will not either. Read as a
window of zero hours, that would make every reading in the lab instantly stale
the moment this ships. Several tests below exist only to hold that line.

`qc_is_stale` itself is untouched — `LEM Web Server/tests/test_qc_window.py`
asserts the two copies of it never disagree. This changes where the NUMBER comes
from, not how it is applied.
"""
import json
from datetime import datetime, timedelta

import pytest

from lem_station_module import (
    LAB_ID_KEY,
    QC_WINDOW_DEFAULT_HOURS,
    STATUS_GREEN,
    STATUS_YELLOW,
    Machine,
    MethodMapping,
    Selector,
    TestResult,
    TestSpec,
    evaluate_machine,
    floor_config_results,
    parse_qc_sample_rows,
    qc_freshness,
    qc_window_for,
    resolve_qc_window,
    spec_qc_window,
    specs_from_qc_samples,
)


NOW = datetime(2026, 8, 26, 15, 0)


# ── 1. the chain itself ────────────────────────────────────────────────────

class TestThePrecedenceChain:
    def test_the_most_specific_level_wins(self):
        assert resolve_qc_window(
            (("mapping", 4.0), ("standard", 8.0), ("machine", 12.0))
        ) == (4.0, "mapping")

    def test_zero_falls_through_one_level(self):
        assert resolve_qc_window(
            (("mapping", 0.0), ("standard", 8.0), ("machine", 12.0))
        ) == (8.0, "standard")

    def test_zero_falls_through_two(self):
        assert resolve_qc_window(
            (("mapping", 0.0), ("standard", 0.0), ("machine", 12.0))
        ) == (12.0, "machine")

    def test_nothing_anywhere_is_the_shared_default_and_says_so(self):
        assert QC_WINDOW_DEFAULT_HOURS == 24.0
        assert resolve_qc_window(
            (("mapping", 0.0), ("standard", 0.0), ("machine", 0.0))
        ) == (24.0, "default")

    def test_junk_at_a_level_is_absence_not_a_zero_hour_window(self):
        for junk in (None, "", "banana", float("nan"), float("inf"), -1.0):
            assert resolve_qc_window(
                (("standard", junk), ("machine", 12.0))) == (12.0, "machine")

    def test_a_caller_may_stop_short_of_the_default(self):
        assert resolve_qc_window(
            (("mapping", 0.0), ("standard", 0.0)), default_hours=0.0
        ) == (0.0, "default")

    def test_a_number_that_arrived_as_text_still_counts(self):
        """JSON from the floor, or a hand-edited config."""
        assert resolve_qc_window((("standard", "8"),)) == (8.0, "standard")


class TestTheTwoDirectionsItIsAskedIn:
    def test_a_spec_takes_the_mapping_over_the_standard(self):
        assert spec_qc_window(4.0, 8.0) == (4.0, "mapping")

    def test_a_spec_takes_the_standard_when_the_mapping_is_silent(self):
        assert spec_qc_window(0.0, 8.0) == (8.0, "standard")

    def test_a_spec_with_neither_says_nothing_rather_than_24(self):
        """The machine has not been consulted yet at this point. Baking 24.0 in
        here would silently override every machine default in the lab."""
        assert spec_qc_window(0.0, 0.0) == (0.0, "")

    def test_the_applied_window_prefers_the_spec(self):
        spec = TestSpec(name="Flash Point", value_col="Flash Point",
                        expected=62.5, std_dev=0.8, qc_expire_hours=8.0,
                        qc_expire_source="standard")
        assert qc_window_for(spec, Machine(uid="m1", qc_expire_hours=12.0)) \
            == (8.0, "standard")

    def test_the_applied_window_falls_to_the_machine(self):
        spec = TestSpec(name="Flash Point", value_col="Flash Point",
                        expected=62.5, std_dev=0.8)
        assert qc_window_for(spec, Machine(uid="m1", qc_expire_hours=12.0)) \
            == (12.0, "machine")

    def test_the_applied_window_falls_all_the_way_to_the_default(self):
        spec = TestSpec(name="Flash Point", value_col="Flash Point",
                        expected=62.5, std_dev=0.8)
        assert qc_window_for(spec, Machine(uid="m1", qc_expire_hours=0.0)) \
            == (24.0, "default")

    def test_no_spec_at_all_still_answers(self):
        """`evaluate_machine` can hold a result whose spec has gone."""
        assert qc_window_for(None, Machine(uid="m1", qc_expire_hours=12.0)) \
            == (12.0, "machine")

    def test_a_spec_from_an_older_config_is_labelled_honestly(self):
        """A window with no recorded level — a persisted `Machine` from before
        this shipped. It is used; it is just not claimed to be a standard's."""
        spec = TestSpec(name="Flash Point", value_col="Flash Point",
                        expected=62.5, std_dev=0.8, qc_expire_hours=8.0)
        assert qc_window_for(spec, Machine(uid="m1")) == (8.0, "spec")


# ── 2. the standard's window reaches a spec ────────────────────────────────

def library(hours=8.0, name="Flash Point"):
    """A `lem_qc_samples` row set, in the shape `parse_qc_sample_rows` emits."""
    test = {"name": name, "value_col": name, "expected": 62.5,
            "std_dev": 0.8, "k": 2.0, "units": "C"}
    if hours is not None:
        test["qc_expire_hours"] = hours
    return [{"name": "Flash CRM", "sample_id_val": "L-9001", "tests": [test]}]


TARGETS = [{"sample": "Flash CRM", "test": "Flash Point"}]


def parsing_machine(mapping_hours=0.0, machine_hours=24.0):
    return Machine(
        uid="pac-flash-2", title="PAC Flash 2",
        qc_expire_hours=machine_hours,
        mappings=[MethodMapping(methods=["Flash Point"],
                                selector=Selector(mode="cell", index=1),
                                qc_expire_hours=mapping_hours)])


class TestSpecsFromQcSamplesCarryTheStandardsWindow:
    def test_the_standards_window_lands_on_the_spec(self):
        [spec] = specs_from_qc_samples(parsing_machine(), library(), TARGETS)
        assert spec.qc_expire_hours == 8.0
        assert spec.qc_expire_source == "standard"

    def test_a_mapping_override_still_wins(self):
        """An explicit human act on THIS instrument beats the library."""
        [spec] = specs_from_qc_samples(
            parsing_machine(mapping_hours=4.0), library(), TARGETS)
        assert spec.qc_expire_hours == 4.0
        assert spec.qc_expire_source == "mapping"

    def test_a_standard_that_says_nothing_leaves_the_spec_silent(self):
        [spec] = specs_from_qc_samples(parsing_machine(), library(hours=None),
                                       TARGETS)
        assert spec.qc_expire_hours == 0.0
        assert spec.qc_expire_source == ""
        assert qc_window_for(spec, parsing_machine()) == (24.0, "machine")

    def test_a_standard_window_of_zero_is_fall_through_not_instant_staleness(
            self):
        [spec] = specs_from_qc_samples(parsing_machine(), library(hours=0),
                                       TARGETS)
        assert qc_window_for(spec, parsing_machine()) == (24.0, "machine")

    def test_a_manual_bench_gets_it_too(self):
        """A manual bench has no mappings, so the assignment IS the
        declaration — and the standard is the only level that can speak."""
        machine = Machine(uid="man-1", title="Manual", source_type="manual",
                          qc_expire_hours=24.0)
        [spec] = specs_from_qc_samples(machine, library(), TARGETS)
        assert spec.qc_expire_hours == 8.0
        assert spec.qc_expire_source == "standard"

    def test_the_window_survives_the_spec_dict_round_trip(self):
        """`Machine.to_dict` is what LabStation persists and what
        `lem_machine_config` publishes."""
        [spec] = specs_from_qc_samples(parsing_machine(), library(), TARGETS)
        back = TestSpec.from_dict(spec.to_dict())
        assert back.qc_expire_hours == 8.0
        assert back.qc_expire_source == "standard"

    def test_a_spec_dict_from_before_this_shipped_still_loads(self):
        old = {"name": "Flash Point", "value_col": "Flash Point",
               "expected": 62.5, "std_dev": 0.8, "k": 2.0, "units": "C",
               "sample_id": "L-9001", "qc_expire_hours": 8.0}
        back = TestSpec.from_dict(old)
        assert back.qc_expire_hours == 8.0
        assert back.qc_expire_source == ""


class TestTheLibraryParserKeepsIt:
    def test_a_labcore_row_carries_the_window_into_the_library(self):
        rows = [{"name": "Flash CRM", "sample_id_val": "L-9001",
                 "tests": json.dumps([{"name": "Flash Point",
                                       "qc_expire_hours": 8.0}])}]
        assert parse_qc_sample_rows(rows)[0]["tests"][0][
            "qc_expire_hours"] == 8.0

    def test_a_row_without_one_parses_fine(self):
        rows = [{"name": "Flash CRM", "sample_id_val": "L-9001",
                 "tests": '[{"name": "Flash Point"}]'}]
        assert "qc_expire_hours" not in parse_qc_sample_rows(rows)[0]["tests"][0]


# ── 3. the verdict actually moves ──────────────────────────────────────────

def evaluated(machine, specs, ran_hours_ago):
    """One in-spec reading, made `ran_hours_ago` before NOW.

    The row is the shape `parse_print` emits: the Lab ID under `LAB_ID_KEY` and
    the time split across `parsed_date` / `parsed_time`, which is what
    `_row_time` reads.
    """
    machine.tests = specs
    ran = NOW - timedelta(hours=ran_hours_ago)
    row = {LAB_ID_KEY: "L-9001", "Flash Point": 62.5,
           "parsed_date": ran.strftime("%Y-%m-%d"),
           "parsed_time": ran.strftime("%H:%M:%S")}
    return evaluate_machine(machine, [row], NOW)


class TestTheStandardsWindowDecidesTheColour:
    """The anti-"it is only carried around" test. Same rows, same clock, same
    machine default — only the standard's window differs."""

    def make(self, hours):
        machine = parsing_machine(machine_hours=24.0)
        specs = specs_from_qc_samples(machine, library(hours=hours), TARGETS)
        return machine, specs

    def test_ten_hours_old_is_green_under_the_machine_default(self):
        machine, specs = self.make(None)
        outcome = evaluated(machine, specs, 10)
        assert outcome.status == STATUS_GREEN, outcome.reason

    def test_the_same_reading_is_yellow_under_an_eight_hour_standard(self):
        machine, specs = self.make(8.0)
        outcome = evaluated(machine, specs, 10)
        assert outcome.status == STATUS_YELLOW
        assert "QC stale" in outcome.reason

    def test_a_longer_standard_window_keeps_it_green_past_the_default(self):
        machine, specs = self.make(72.0)
        outcome = evaluated(machine, specs, 30)
        assert outcome.status == STATUS_GREEN, outcome.reason

    def test_a_mapping_override_beats_the_standard_in_the_verdict(self):
        machine = parsing_machine(mapping_hours=48.0, machine_hours=24.0)
        specs = specs_from_qc_samples(machine, library(hours=8.0), TARGETS)
        assert evaluated(machine, specs, 30).status == STATUS_GREEN

    def test_a_standard_with_no_window_never_makes_a_fresh_reading_stale(self):
        """The catastrophic reading of absence. One minute old, no window
        anywhere on the standard: it is GREEN, not instantly stale."""
        machine, specs = self.make(None)
        assert evaluated(machine, specs, 0).status == STATUS_GREEN


class TestTheBatteryAgreesWithTheVerdict:
    """`qc_freshness` fills the card's battery. It used to be handed no window
    at all, so a card could read half-full about a test the verdict had already
    called stale."""

    def test_the_fill_follows_the_specs_window(self):
        machine = parsing_machine(machine_hours=24.0)
        [spec] = specs_from_qc_samples(machine, library(hours=8.0), TARGETS)
        result = TestResult("Flash Point", 62.5, True,
                            NOW - timedelta(hours=10))
        assert qc_freshness(machine, result, NOW, spec.qc_expire_hours) == 0.0
        assert qc_freshness(machine, result, NOW) > 0.0     # machine default

    def test_a_machine_with_no_default_still_gets_the_shared_one(self):
        """`hours = expire_hours or machine.qc_expire_hours` used to leave 0,
        which made the window 1e-9 seconds and the battery permanently empty."""
        machine = Machine(uid="m1", qc_expire_hours=0.0)
        result = TestResult("Flash Point", 62.5, True,
                            NOW - timedelta(hours=1))
        assert qc_freshness(machine, result, NOW) > 0.9


# ── 4. the config road, and a bench that has not been upgraded ─────────────

def floor_body(tests_json, uid="pac-flash-2", age=1.0):
    """A `/api/bench/<uid>/config` answer in the shape the floor serves it.

    Key names and the JSON-TEXT `tests` column are what
    `snapshot_service.bench_config_from_tables` actually emits — see
    `LEM Web Server/tests/test_bench_config.py`, which pins them literally, and
    `LEM Web Server/tests/test_qc_standard_window.py`, which drives the real
    endpoint end to end into this module.
    """
    return {"machine_uid": uid, "snapshot_age_seconds": age, "override": "",
            "corrections": [], "qc_targets": [{"sample_name": "Flash CRM",
                                               "test_name": "Flash Point"}],
            "qc_specs": [], "maintenance": [],
            "qc_samples": [{"name": "Flash CRM", "sample_id_val": "L-9001",
                            "tests": tests_json}]}


class TestTheConfigRoadCarriesIt:
    def test_the_window_survives_the_floor_answer(self):
        body = floor_body(json.dumps([{"name": "Flash Point",
                                       "value_col": "Flash Point",
                                       "expected": 62.5, "std_dev": 0.8,
                                       "qc_expire_hours": 8.0}]))
        results = floor_config_results(body, "pac-flash-2")
        lib = parse_qc_sample_rows(results["qc_samples"]["rows"])
        [spec] = specs_from_qc_samples(
            parsing_machine(), lib,
            [{"sample": r["sample_name"], "test": r["test_name"]}
             for r in body["qc_targets"]])
        assert spec.qc_expire_hours == 8.0

    def test_an_older_floor_that_ships_no_window_falls_through(self):
        """A bench upgraded ahead of its server. The field simply is not there,
        and the machine default has to stand — not zero."""
        body = floor_body('[{"name": "Flash Point", "value_col": '
                          '"Flash Point", "expected": 62.5, "std_dev": 0.8}]')
        results = floor_config_results(body, "pac-flash-2")
        lib = parse_qc_sample_rows(results["qc_samples"]["rows"])
        machine = parsing_machine(machine_hours=24.0)
        [spec] = specs_from_qc_samples(
            machine, lib, [{"sample": "Flash CRM", "test": "Flash Point"}])
        assert qc_window_for(spec, machine) == (24.0, "machine")

    def test_the_answer_is_still_refused_whole_when_a_section_is_missing(self):
        body = floor_body('[]')
        body.pop("qc_specs")
        assert floor_config_results(body, "pac-flash-2") is None
