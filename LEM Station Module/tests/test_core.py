"""Unit tests for the pure-logic core of lem_station_module (v2 model).

Covers: file tailing, clean-text tools, selector extraction (cell /
text-detection), print parsing onto LabCore test methods, QC-spec parsing
from LabCore rows, the LEM status evaluation, and Machine serialization.
No CSV formatting exists — parsed data goes to LabCore only.
"""
from datetime import datetime

import pytest

from lem_station_module import (
    STATUS_DEAD,
    STATUS_GREEN,
    STATUS_RED,
    STATUS_SERVICE,
    STATUS_UNKNOWN,
    STATUS_YELLOW,
    Machine,
    MethodMapping,
    Selector,
    TestSpec,
    apply_clean,
    evaluate_machine,
    extract_value,
    format_relative_time,
    parse_print,
    parse_qc_specs,
    qc_freshness,
    tail_new_text,
)

NOW = datetime(2026, 7, 27, 12, 0, 0)


def make_machine(**overrides):
    base = dict(
        uid="m1",
        title="Eraspec",
        source_type="single_csv",
        csv_path="/tmp/in.csv",
        delimiter=",",
        lab_id=Selector(mode="cell", index=0),
        mappings=[MethodMapping(methods=["RON"],
                                selector=Selector(mode="cell", index=1))],
        tests=[TestSpec(name="RON", value_col="RON", expected=91.0, std_dev=0.5,
                        k=2.0, sample_id="QC1")],
    )
    base.update(overrides)
    return Machine(**base)


def qc_row(value, date="2026-07-27", time="11:00:00", lab_id="QC1"):
    return {"Lab ID": lab_id, "RON": value,
            "parsed_date": date, "parsed_time": time}


# ── tail_new_text ────────────────────────────────────────────────────────────

class TestTailNewText:
    def test_reads_whole_new_file_and_returns_offset(self, tmp_path):
        p = tmp_path / "in.csv"
        p.write_text("a,b\nc,d\n")
        text, pos = tail_new_text(str(p), 0)
        assert text == "a,b\nc,d\n"
        assert pos == len("a,b\nc,d\n")

    def test_returns_empty_when_nothing_new(self, tmp_path):
        p = tmp_path / "in.csv"
        p.write_text("a,b\n")
        _, pos = tail_new_text(str(p), 0)
        text, pos2 = tail_new_text(str(p), pos)
        assert text == ""
        assert pos2 == pos

    def test_returns_only_appended_text(self, tmp_path):
        p = tmp_path / "in.csv"
        p.write_text("a,b\n")
        _, pos = tail_new_text(str(p), 0)
        with open(p, "a") as f:
            f.write("e,f\n")
        text, _ = tail_new_text(str(p), pos)
        assert text == "e,f\n"

    def test_restarts_from_zero_when_file_shrank(self, tmp_path):
        p = tmp_path / "in.csv"
        p.write_text("a long line of text\n")
        _, pos = tail_new_text(str(p), 0)
        p.write_text("x,y\n")  # rotated/truncated
        text, new_pos = tail_new_text(str(p), pos)
        assert text == "x,y\n"
        assert new_pos == len("x,y\n")

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            tail_new_text(str(tmp_path / "nope.csv"), 0)

    def test_decodes_windows_cp1252_output(self, tmp_path):
        p = tmp_path / "in.csv"
        p.write_bytes(b"Temp \xb0C,25.1\n")  # 0xb0 = degree sign in cp1252
        text, _ = tail_new_text(str(p), 0)
        assert text == "Temp °C,25.1\n"


# ── apply_clean (the clean-text tools) ───────────────────────────────────────

class TestApplyClean:
    def test_strip(self):
        assert apply_clean("  91.2  ", ["strip"]) == "91.2"

    def test_collapse_whitespace(self):
        assert apply_clean("RON :   91.2", ["collapse_ws"]) == "RON : 91.2"

    def test_keep_number_extracts_first_numeric(self):
        assert apply_clean("RON = 91.2 (est)", ["keep_number"]) == "91.2"
        assert apply_clean("T -12.5C", ["keep_number"]) == "-12.5"

    def test_keep_number_with_no_number_gives_empty(self):
        assert apply_clean("no digits here", ["keep_number"]) == ""

    def test_remove_text(self):
        assert apply_clean("RON=91.2", ["remove:RON="]) == "91.2"

    def test_ops_apply_in_order(self):
        assert apply_clean("  val: 91.2  ", ["remove:val:", "strip"]) == "91.2"

    def test_unknown_op_is_ignored(self):
        assert apply_clean("x", ["bogus_op"]) == "x"


# ── extract_value (cell selection / text detection) ──────────────────────────

class TestExtractValue:
    def test_cell_mode_indexes_into_delimited_cells(self):
        sel = Selector(mode="cell", index=2)
        assert extract_value(sel, "QC1,91.2,90.1", ",") == "90.1"

    def test_cell_mode_flattens_multiline_prints(self):
        sel = Selector(mode="cell", index=3)
        assert extract_value(sel, "QC1,91.2\n90.1,58.3", ",") == "58.3"

    def test_cell_mode_out_of_range_gives_empty(self):
        sel = Selector(mode="cell", index=9)
        assert extract_value(sel, "QC1,91.2", ",") == ""

    def test_detect_mode_uses_pattern_group(self):
        sel = Selector(mode="detect", pattern=r"RON\s*=\s*(\S+)")
        assert extract_value(sel, "MON = 90.1\nRON = 91.2", ",") == "91.2"

    def test_detect_mode_without_group_uses_whole_match(self):
        sel = Selector(mode="detect", pattern=r"\d+\.\d+")
        assert extract_value(sel, "value 91.2 end", ",") == "91.2"

    def test_detect_mode_no_match_gives_empty(self):
        sel = Selector(mode="detect", pattern=r"XYZ(\d+)")
        assert extract_value(sel, "nothing here", ",") == ""

    def test_invalid_pattern_gives_empty(self):
        sel = Selector(mode="detect", pattern=r"([bad")
        assert extract_value(sel, "anything", ",") == ""

    def test_clean_ops_run_after_extraction(self):
        sel = Selector(mode="cell", index=1, clean=["keep_number"])
        assert extract_value(sel, "QC1, RON: 91.2 ", ",") == "91.2"


# ── parse_print (raw device print → Lab ID + method values) ──────────────────

class TestParsePrint:
    def test_extracts_lab_id_and_mapped_methods(self):
        m = make_machine()
        result = parse_print(m, "QC1,91.2")
        assert result.lab_id == "QC1"
        assert result.values == {"RON": "91.2"}

    def test_group_of_methods_share_one_value(self):
        m = make_machine(mappings=[MethodMapping(
            methods=["Visc @40C", "Visc @40C dup"],
            selector=Selector(mode="cell", index=1))])
        result = parse_print(m, "26-001,14.20")
        assert result.values == {"Visc @40C": "14.20",
                                 "Visc @40C dup": "14.20"}

    def test_empty_extractions_are_omitted(self):
        m = make_machine(mappings=[
            MethodMapping(methods=["RON"], selector=Selector(mode="cell", index=1)),
            MethodMapping(methods=["MON"], selector=Selector(mode="cell", index=9)),
        ])
        result = parse_print(m, "QC1,91.2")
        assert result.values == {"RON": "91.2"}

    def test_to_row_carries_lab_id_methods_and_timestamp(self):
        m = make_machine()
        row = parse_print(m, "QC1,91.2").to_row(NOW)
        assert row["Lab ID"] == "QC1"
        assert row["RON"] == "91.2"
        assert row["parsed_date"] == "2026-07-27"
        assert row["parsed_time"] == "12:00:00"


# ── parse_qc_specs (specs pulled from LabCore — no module-level tests) ───────

class TestParseQcSpecs:
    ROWS = [
        {"machine_uid": "m1", "test_name": "RON", "sample_id": "QC1",
         "expected": 91.0, "std_dev": 0.5, "k": 2.0, "units": ""},
        {"machine_uid": "", "test_name": "Flash", "sample_id": "QC-D2",
         "expected": 210.0, "std_dev": 2.0, "k": 2.0, "units": "°C"},
        {"machine_uid": "other", "test_name": "MON", "sample_id": "QC1",
         "expected": 90.0, "std_dev": 0.6, "k": 2.0, "units": ""},
    ]

    def test_keeps_own_and_unscoped_specs_only(self):
        specs = parse_qc_specs(self.ROWS, "m1")
        assert [s.name for s in specs] == ["RON", "Flash"]

    def test_spec_fields_map_onto_testspec(self):
        spec = parse_qc_specs(self.ROWS, "m1")[0]
        assert spec.value_col == "RON"
        assert spec.sample_id == "QC1"
        assert spec.expected == 91.0
        assert spec.std_dev == 0.5
        assert spec.k == 2.0

    def test_bad_rows_are_skipped(self):
        rows = [{"test_name": "", "expected": 1.0},
                {"test_name": "OK", "sample_id": "Q", "expected": "not-a-number",
                 "std_dev": 1.0, "k": 2.0}]
        assert parse_qc_specs(rows, "m1") == []


# ── evaluate_machine (unchanged LEM logic, methods as test names) ────────────

class TestEvaluateMachine:
    def test_in_spec_fresh_is_green(self):
        ev = evaluate_machine(make_machine(), [qc_row("91.2")], NOW)
        assert ev.status == STATUS_GREEN

    def test_out_of_spec_is_red(self):
        # spec 91.0 +/- 2*0.5 -> [90.0, 92.0]
        ev = evaluate_machine(make_machine(), [qc_row("93.0")], NOW)
        assert ev.status == STATUS_RED

    def test_boundary_value_is_in_spec(self):
        ev = evaluate_machine(make_machine(), [qc_row("92.0")], NOW)
        assert ev.status == STATUS_GREEN

    def test_no_rows_is_yellow_because_qc_is_assigned(self):
        """Grey is only for "no QC assigned" — see TestQcAssignedButNotRun."""
        ev = evaluate_machine(make_machine(), [], NOW)
        assert ev.status == STATUS_YELLOW

    def test_non_numeric_value_is_yellow(self):
        ev = evaluate_machine(make_machine(), [qc_row("ERROR")], NOW)
        assert ev.status == STATUS_YELLOW

    def test_latest_matching_row_wins(self):
        rows = [qc_row("93.0", time="09:00:00"), qc_row("91.2", time="10:00:00")]
        ev = evaluate_machine(make_machine(), rows, NOW)
        assert ev.status == STATUS_GREEN

    def test_partial_print_keeps_other_tests_last_measurement(self):
        # QC ran both tests this morning; only RON was re-run later. MON must
        # keep its last real measurement instead of going UNKNOWN because the
        # newest print doesn't carry it.
        machine = make_machine(tests=[
            TestSpec(name="RON", value_col="RON", expected=91.0, std_dev=0.5,
                     k=2.0, sample_id="QC1"),
            TestSpec(name="MON", value_col="MON", expected=90.0, std_dev=0.6,
                     k=2.0, sample_id="QC1"),
        ])
        rows = [
            {"Lab ID": "QC1", "RON": "91.2", "MON": "90.1",
             "parsed_date": "2026-07-27", "parsed_time": "09:00:00"},
            {"Lab ID": "QC1", "RON": "91.5",  # RON-only re-run
             "parsed_date": "2026-07-27", "parsed_time": "11:00:00"},
        ]
        ev = evaluate_machine(machine, rows, NOW)
        by_name = {r.name: r for r in ev.test_results}
        assert by_name["RON"].value == 91.5   # newest RON
        assert by_name["MON"].value == 90.1   # last real MON, not UNKNOWN
        assert ev.status == STATUS_GREEN

    def test_partial_print_out_of_spec_history_still_red(self):
        machine = make_machine(tests=[
            TestSpec(name="RON", value_col="RON", expected=91.0, std_dev=0.5,
                     k=2.0, sample_id="QC1"),
            TestSpec(name="MON", value_col="MON", expected=90.0, std_dev=0.6,
                     k=2.0, sample_id="QC1"),
        ])
        rows = [
            {"Lab ID": "QC1", "MON": "95.0",  # out of spec, only measurement
             "parsed_date": "2026-07-27", "parsed_time": "09:00:00"},
            {"Lab ID": "QC1", "RON": "91.5",
             "parsed_date": "2026-07-27", "parsed_time": "11:00:00"},
        ]
        ev = evaluate_machine(machine, rows, NOW)
        assert ev.status == STATUS_RED
        assert "MON" in ev.reason

    def test_lab_id_match_is_case_insensitive_and_stripped(self):
        ev = evaluate_machine(make_machine(), [qc_row("91.2", lab_id=" qc1 ")], NOW)
        assert ev.status == STATUS_GREEN

    def test_rows_for_other_samples_are_ignored(self):
        """Another sample's row is not this machine's QC, so it stays awaiting."""
        ev = evaluate_machine(make_machine(), [qc_row("91.2", lab_id="OTHER")], NOW)
        assert ev.status == STATUS_YELLOW

    def test_stale_qc_is_yellow_by_calendar_day(self):
        ev = evaluate_machine(make_machine(), [qc_row("91.2", date="2026-07-25")], NOW)
        assert ev.status == STATUS_YELLOW

    def test_same_day_qc_is_fresh(self):
        ev = evaluate_machine(make_machine(), [qc_row("91.2", date="2026-07-27")], NOW)
        assert ev.status == STATUS_GREEN

    def test_manual_override_service(self):
        m = make_machine(manual_override=STATUS_SERVICE)
        ev = evaluate_machine(m, [qc_row("91.2")], NOW)
        assert ev.status == STATUS_SERVICE
        assert "Overridden" in ev.reason

    def test_manual_override_dead_line(self):
        m = make_machine(manual_override=STATUS_DEAD)
        ev = evaluate_machine(m, [], NOW)
        assert ev.status == STATUS_DEAD

    def test_no_specs_from_labcore_is_unknown(self):
        ev = evaluate_machine(make_machine(tests=[]), [qc_row("91.2")], NOW)
        assert ev.status == STATUS_UNKNOWN


# ── format_relative_time ─────────────────────────────────────────────────────

class TestFormatRelativeTime:
    def test_just_now_under_ten_seconds(self):
        from datetime import timedelta
        assert format_relative_time(NOW - timedelta(seconds=5), NOW) == "just now"

    def test_seconds_only(self):
        from datetime import timedelta
        assert format_relative_time(NOW - timedelta(seconds=42), NOW) == "42 secs. ago"

    def test_minutes_and_seconds(self):
        from datetime import timedelta
        then = NOW - timedelta(minutes=11, seconds=53)
        assert format_relative_time(then, NOW) == "11 min., 53 secs. ago"

    def test_hours_and_minutes(self):
        from datetime import timedelta
        then = NOW - timedelta(hours=2, minutes=19)
        assert format_relative_time(then, NOW) == "2 hr., 19 min. ago"

    def test_days(self):
        from datetime import timedelta
        assert format_relative_time(NOW - timedelta(days=3), NOW) == "3 days ago"

    def test_none_gives_dash(self):
        assert format_relative_time(None, NOW) == "—"


# ── qc_freshness ─────────────────────────────────────────────────────────────

class TestQcFreshness:
    def make_result(self, in_spec=True, when=datetime(2026, 7, 27, 8, 0)):
        from lem_station_module import TestResult
        return TestResult("RON", 91.2 if in_spec else 93.0, in_spec, when)

    def test_fresh_in_spec_result_gives_high_fraction(self):
        fraction = qc_freshness(make_machine(), self.make_result(), NOW)
        assert 0.4 < fraction <= 1.0

    def test_stale_result_gives_zero(self):
        old = self.make_result(when=datetime(2026, 7, 24, 8, 0))
        assert qc_freshness(make_machine(), old, NOW) == 0.0

    def test_missing_result_gives_zero(self):
        from lem_station_module import TestResult
        assert qc_freshness(make_machine(), None, NOW) == 0.0
        no_data = TestResult("RON", None, None, None)
        assert qc_freshness(make_machine(), no_data, NOW) == 0.0

    def test_out_of_spec_result_gives_zero(self):
        bad = self.make_result(in_spec=False)
        assert qc_freshness(make_machine(), bad, NOW) == 0.0

    def test_fresher_result_gives_larger_fraction(self):
        earlier = self.make_result(when=datetime(2026, 7, 26, 23, 0))
        later = self.make_result(when=datetime(2026, 7, 27, 11, 0))
        m = make_machine(qc_expire_hours=48.0)
        assert qc_freshness(m, later, NOW) > qc_freshness(m, earlier, NOW)


# ── Machine serialization (v2 fields; no CSV formatting fields) ──────────────

class TestMachineSerialization:
    def test_round_trip(self):
        m = make_machine(source_type="multi_csv", delimiter=";",
                         template="QC1;91.2", qc_expire_hours=48.0,
                         manual_override=STATUS_SERVICE, last_position=123,
                         image_path="/tmp/eraspec.png")
        m2 = Machine.from_dict(m.to_dict())
        assert m2 == m

    def test_to_dict_is_json_serializable(self):
        import json
        json.dumps(make_machine().to_dict())

    def test_from_dict_tolerates_missing_keys(self):
        m = Machine.from_dict({"title": "Bare"})
        assert m.title == "Bare"
        assert m.source_type == "single_csv"
        assert m.delimiter == ","
        assert m.qc_expire_hours == 24.0
        assert m.mappings == []
        assert m.tests == []
        assert m.template == ""
        assert m.last_position == 0

    def test_from_dict_restores_mappings_and_selectors(self):
        m = make_machine(mappings=[MethodMapping(
            methods=["RON", "MON"],
            selector=Selector(mode="detect", pattern=r"(\d+)",
                              clean=["strip", "keep_number"]))])
        m2 = Machine.from_dict(m.to_dict())
        assert m2.mappings[0].methods == ["RON", "MON"]
        assert m2.mappings[0].selector.mode == "detect"
        assert m2.mappings[0].selector.clean == ["strip", "keep_number"]

    def test_no_csv_formatting_fields_exist(self):
        m = make_machine()
        for gone in ("header", "reorder", "math_operations", "master_csv"):
            assert not hasattr(m, gone)
            assert gone not in m.to_dict()


# ── QC assigned but not yet run is a JOB, not an unknown ─────────────────────
#
# Grey said "I have nothing to tell you", which is what an unconfigured bench
# looks like too — so a machine with QC assigned and no standard run yet was
# indistinguishable from one nobody had set up. Orange asks for the QC; grey is
# now reserved for "no QC assigned at all".

class TestQcAssignedButNotRun:
    def test_assigned_with_no_rows_at_all_is_yellow(self):
        ev = evaluate_machine(make_machine(), [], NOW)
        assert ev.status == STATUS_YELLOW
        assert "not yet run" in ev.reason.lower()

    def test_the_reason_names_the_test_to_run(self):
        ev = evaluate_machine(make_machine(), [], NOW)
        assert "RON" in ev.reason

    def test_nothing_assigned_is_still_grey(self):
        """The one case grey is for."""
        ev = evaluate_machine(make_machine(tests=[]), [qc_row("91.2")], NOW)
        assert ev.status == STATUS_UNKNOWN

    def test_a_failure_still_outranks_awaiting(self):
        """RED must win: one bad result matters more than a missing one."""
        m = make_machine(tests=[
            TestSpec(name="RON", value_col="RON", expected=91.0,
                         std_dev=0.5, k=2.0, sample_id="QC1"),
            TestSpec(name="MON", value_col="MON", expected=80.0,
                         std_dev=0.5, k=2.0, sample_id="QC1"),
        ])
        ev = evaluate_machine(m, [qc_row("93.0")], NOW)   # RON out, MON absent
        assert ev.status == STATUS_RED

    def test_a_good_result_alongside_a_missing_one_is_yellow(self):
        m = make_machine(tests=[
            TestSpec(name="RON", value_col="RON", expected=91.0,
                         std_dev=0.5, k=2.0, sample_id="QC1"),
            TestSpec(name="MON", value_col="MON", expected=80.0,
                         std_dev=0.5, k=2.0, sample_id="QC1"),
        ])
        ev = evaluate_machine(m, [qc_row("91.2")], NOW)   # RON good, MON absent
        assert ev.status == STATUS_YELLOW
        assert "MON" in ev.reason

    def test_the_qc_sub_status_agrees(self):
        ev = evaluate_machine(make_machine(), [], NOW)
        assert ev.sub_statuses["qc"] == STATUS_YELLOW

    def test_an_override_still_wins(self):
        m = make_machine(manual_override=STATUS_SERVICE)
        ev = evaluate_machine(m, [], NOW)
        assert ev.status == STATUS_SERVICE
