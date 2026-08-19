"""A QC standard is a QC check, not a sample run.

Every parsed print was logged as a `run`, and QC prints got `qc` events on top —
so running the Cloud CRM showed up twice in the machine's history, once as a
sample nobody submitted. The history is the record an auditor reads; a standard
appearing as a production run is wrong.

So a print that IS a QC standard logs only its `qc` verdicts. The one thing that
must not happen: a print vanishing from the history because it looked like QC but
produced no readable verdict.
"""
import json
from datetime import datetime

import pytest

import lem_station_module as mod
from lem_station_module import LAB_ID_KEY, Machine, TestSpec

NOW = datetime(2026, 8, 3, 12, 0, 0)


def machine_with_cloud_qc(**over):
    base = dict(
        uid="m1", title="OptiMPP 1",
        tests=[TestSpec(name="ASTM D7689 - Cloud Point, mini method",
                        value_col="ASTM D7689 - Cloud Point, mini method",
                        expected=-7.4, std_dev=2.8, k=1.0, sample_id="CP")])
    base.update(over)
    return Machine(**base)


def kinds(events):
    """build_log_insert() args → the `kind` column of each queued event."""
    return [args[2] for _sql, args in events]


def detail_for(events, kind):
    for _sql, args in events:
        if args[2] == kind:
            return json.loads(args[6]) if args[6] else {}
    return None


class Recorder:
    """Minimal stand-in: collects what _queue_run_events queues."""

    def __init__(self, machine):
        self._machine = machine
        self._pending_events = []

    _log_event = mod.LEMStationModule._log_event
    _queue_run_events = mod.LEMStationModule._queue_run_events


def run_events(machine, rows):
    r = Recorder(machine)
    r._queue_run_events(machine, rows, NOW)
    return list(r._pending_events)


# ── a standard logs QC only ─────────────────────────────────────────────────

class TestQcPrintIsNotARun:
    def test_a_qc_standard_logs_only_a_qc_event(self):
        rows = [{LAB_ID_KEY: "CP",
                 "ASTM D7689 - Cloud Point, mini method": "-6.6"}]
        assert kinds(run_events(machine_with_cloud_qc(), rows)) == ["qc"]

    def test_the_verdict_is_still_recorded(self):
        rows = [{LAB_ID_KEY: "CP",
                 "ASTM D7689 - Cloud Point, mini method": "-6.6"}]
        detail = detail_for(run_events(machine_with_cloud_qc(), rows), "qc")
        assert detail["in_spec"] is True          # -7.4 ± 2.8 → [-10.2, -4.6]
        assert detail["expected"] == -7.4

    def test_an_out_of_spec_standard_is_still_qc_only(self):
        rows = [{LAB_ID_KEY: "CP",
                 "ASTM D7689 - Cloud Point, mini method": "-99"}]
        events = run_events(machine_with_cloud_qc(), rows)
        assert kinds(events) == ["qc"]
        assert detail_for(events, "qc")["in_spec"] is False

    def test_the_lab_id_match_is_case_insensitive(self):
        rows = [{LAB_ID_KEY: " cp ",
                 "ASTM D7689 - Cloud Point, mini method": "-6.6"}]
        assert kinds(run_events(machine_with_cloud_qc(), rows)) == ["qc"]

    def test_two_standards_on_one_print_give_two_qc_events_and_no_run(self):
        machine = machine_with_cloud_qc(tests=[
            TestSpec(name="Cloud", value_col="Cloud", expected=-7.4,
                     std_dev=2.8, k=1.0, sample_id="CP"),
            TestSpec(name="Pour", value_col="Pour", expected=-18.3,
                     std_dev=6.4, k=1.0, sample_id="CP"),
        ])
        rows = [{LAB_ID_KEY: "CP", "Cloud": "-6.6", "Pour": "-19.0"}]
        assert kinds(run_events(machine, rows)) == ["qc", "qc"]


# ── ordinary samples are unaffected ─────────────────────────────────────────

class TestSampleRunsStillLog:
    def test_a_production_sample_logs_a_run(self):
        rows = [{LAB_ID_KEY: "37043",
                 "ASTM D7689 - Cloud Point, mini method": "-11.7"}]
        assert kinds(run_events(machine_with_cloud_qc(), rows)) == ["run"]

    def test_the_run_carries_its_values(self):
        rows = [{LAB_ID_KEY: "37043",
                 "ASTM D7689 - Cloud Point, mini method": "-11.7"}]
        detail = detail_for(run_events(machine_with_cloud_qc(), rows), "run")
        assert detail["values"] == {
            "ASTM D7689 - Cloud Point, mini method": "-11.7"}

    def test_a_machine_with_no_qc_assigned_logs_runs(self):
        rows = [{LAB_ID_KEY: "37043", "Cloud": "-11.7"}]
        assert kinds(run_events(Machine(uid="m1", title="x"), rows)) == ["run"]

    def test_a_mixed_batch_logs_each_print_correctly(self):
        rows = [
            {LAB_ID_KEY: "37043",
             "ASTM D7689 - Cloud Point, mini method": "-11.7"},
            {LAB_ID_KEY: "CP",
             "ASTM D7689 - Cloud Point, mini method": "-6.6"},
        ]
        assert kinds(run_events(machine_with_cloud_qc(), rows)) == ["run", "qc"]


# ── nothing may disappear ───────────────────────────────────────────────────

class TestNothingVanishes:
    def test_a_standard_with_an_unreadable_value_still_logs_a_run(self):
        """It looked like QC but produced no verdict — it must not vanish from
        the machine's history."""
        rows = [{LAB_ID_KEY: "CP",
                 "ASTM D7689 - Cloud Point, mini method": "ERROR"}]
        assert kinds(run_events(machine_with_cloud_qc(), rows)) == ["run"]

    def test_a_standard_missing_the_watched_column_still_logs_a_run(self):
        rows = [{LAB_ID_KEY: "CP", "Something Else": "-6.6"}]
        assert kinds(run_events(machine_with_cloud_qc(), rows)) == ["run"]

    def test_a_partial_standard_logs_qc_for_what_it_did_measure(self):
        machine = machine_with_cloud_qc(tests=[
            TestSpec(name="Cloud", value_col="Cloud", expected=-7.4,
                     std_dev=2.8, k=1.0, sample_id="CP"),
            TestSpec(name="Pour", value_col="Pour", expected=-18.3,
                     std_dev=6.4, k=1.0, sample_id="CP"),
        ])
        rows = [{LAB_ID_KEY: "CP", "Cloud": "-6.6"}]   # Pour absent
        assert kinds(run_events(machine, rows)) == ["qc"]
