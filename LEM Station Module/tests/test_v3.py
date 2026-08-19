"""v3 tests: stackable clean-ups (purge/math), per-mapping QC (sample +
expiry), PM/Calibration tasks, serial frame assembly, and the standardized
lem_machine_log "machine universe" container.
"""
import json
import os
from datetime import date, datetime

import pytest

import lem_station_module as mod
from lem_station_module import (
    LOG_TABLE_DDL,
    STATUS_DEAD,
    STATUS_GREEN,
    STATUS_RED,
    STATUS_SERVICE,
    STATUS_UNKNOWN,
    STATUS_YELLOW,
    FrameAssembler,
    Machine,
    MaintTask,
    MethodMapping,
    Selector,
    TestSpec,
    apply_clean,
    build_log_insert,
    evaluate_machine,
    maint_status,
    parse_qc_specs,
    qc_freshness,
    specs_for_machine,
)

from test_module_qt import FakeContext, make_module, sample_machine

NOW = datetime(2026, 7, 27, 12, 0, 0)
TODAY = date(2026, 7, 27)


# ── Clean-up ops: purge text / purge symbols / math operations ───────────────

class TestCleanOpsV3:
    def test_purge_text_drops_letters(self):
        assert apply_clean("Flash 210 C", ["purge_text"]) == "210"

    def test_purge_symbols_drops_punctuation(self):
        assert apply_clean("*Flash*: 210;", ["purge_symbols"]) == "Flash 210"

    def test_math_operation_on_value(self):
        assert apply_clean("0.7351", ["math:round(x * 1000, 1)"]) == "735.1"

    def test_ops_stack_in_order(self):
        assert apply_clean("RON = 91.2", ["keep_number", "math:x * 2"]) == "182.4"

    def test_math_on_non_numeric_leaves_value(self):
        assert apply_clean("abc", ["math:x * 2"]) == "abc"

    def test_math_rejects_disallowed_expressions(self):
        assert apply_clean("5", ["math:__import__('os').getcwd()"]) == "5"
        assert apply_clean("5", ["math:x ** 99999999"]) == "5"


# ── Per-mapping QC: sample + expiry ──────────────────────────────────────────

class TestMappingQC:
    def test_qc_fields_round_trip(self):
        m = MethodMapping(methods=["RON"], selector=Selector(index=1),
                          qc_sample_id="QC1", qc_expire_hours=48.0)
        m2 = MethodMapping.from_dict(m.to_dict())
        assert m2.qc_sample_id == "QC1"
        assert m2.qc_expire_hours == 48.0

    def test_qc_fields_default_off(self):
        m = MethodMapping.from_dict({"methods": ["RON"], "selector": {}})
        assert m.qc_sample_id == ""
        assert m.qc_expire_hours == 0.0


class TestSpecsForMachine:
    LIBRARY = [
        TestSpec(name="RON", value_col="RON", expected=91.0, std_dev=0.5,
                 k=2.0, units=""),
        TestSpec(name="Flash", value_col="Flash", expected=210.0, std_dev=2.0,
                 k=2.0, units="°C"),
    ]

    def make(self, **kw):
        base = dict(uid="m1", title="X", mappings=[MethodMapping(
            methods=["RON"], selector=Selector(index=1),
            qc_sample_id="QC1", qc_expire_hours=48.0)])
        base.update(kw)
        return Machine(**base)

    def test_mapping_qc_joins_labcore_spec_values(self):
        specs = specs_for_machine(self.make(), self.LIBRARY)
        assert len(specs) == 1
        spec = specs[0]
        assert spec.name == "RON"
        assert spec.expected == 91.0      # from LabCore library
        assert spec.std_dev == 0.5
        assert spec.sample_id == "QC1"    # from the mapping
        assert spec.qc_expire_hours == 48.0

    def test_mapping_without_qc_sample_is_not_qcd(self):
        machine = self.make(mappings=[MethodMapping(
            methods=["RON"], selector=Selector(index=1))])
        assert specs_for_machine(machine, self.LIBRARY) == []

    def test_method_missing_from_library_is_skipped(self):
        machine = self.make(mappings=[MethodMapping(
            methods=["Mystery"], selector=Selector(index=1),
            qc_sample_id="QC1")])
        assert specs_for_machine(machine, self.LIBRARY) == []


class TestSpecLevelExpiry:
    def spec(self, hours):
        return TestSpec(name="RON", value_col="RON", expected=91.0,
                        std_dev=0.5, k=2.0, sample_id="QC1",
                        qc_expire_hours=hours)

    def row(self, date_str):
        return {"Lab ID": "QC1", "RON": "91.2",
                "parsed_date": date_str, "parsed_time": "11:00:00"}

    def test_spec_expiry_overrides_machine_default(self):
        machine = Machine(uid="m1", qc_expire_hours=24.0,
                          tests=[self.spec(72.0)])
        ev = evaluate_machine(machine, [self.row("2026-07-25")], NOW)
        assert ev.status == STATUS_GREEN  # 72 h window: 2 days old still fresh

    def test_spec_expiry_zero_falls_back_to_machine_default(self):
        machine = Machine(uid="m1", qc_expire_hours=24.0,
                          tests=[self.spec(0.0)])
        ev = evaluate_machine(machine, [self.row("2026-07-25")], NOW)
        assert ev.status == STATUS_YELLOW

    def test_qc_freshness_accepts_expiry_override(self):
        from lem_station_module import TestResult
        machine = Machine(uid="m1", qc_expire_hours=24.0)
        result = TestResult("RON", 91.2, True, datetime(2026, 7, 26, 8, 0))
        assert qc_freshness(machine, result, NOW) == 0.0
        assert qc_freshness(machine, result, NOW, expire_hours=72.0) > 0.0


# ── PM / Calibration tasks ───────────────────────────────────────────────────

class TestMaintenance:
    def task(self, **kw):
        base = dict(uid="t1", name="Annual cal", kind="calibration",
                    interval_days=365, last_done="2026-01-15")
        base.update(kw)
        return MaintTask(**base)

    def test_completed_recently_is_green(self):
        status, reason = maint_status(self.task(), TODAY)
        assert status == STATUS_GREEN
        assert "2027-01-15" in reason

    def test_overdue_is_red(self):
        status, reason = maint_status(self.task(last_done="2025-01-01"), TODAY)
        assert status == STATUS_RED
        assert "Overdue" in reason

    def test_due_today_is_yellow(self):
        status, reason = maint_status(
            self.task(last_done="2025-07-27", interval_days=365), TODAY)
        assert status == STATUS_YELLOW

    def test_never_completed_is_yellow(self):
        status, reason = maint_status(self.task(last_done=""), TODAY)
        assert status == STATUS_YELLOW
        assert "Not completed" in reason

    def test_round_trip(self):
        t = self.task(note="pump replaced")
        assert MaintTask.from_dict(t.to_dict()) == t

    def test_overdue_task_makes_machine_red(self):
        machine = Machine(
            uid="m1",
            tests=[TestSpec(name="RON", value_col="RON", expected=91.0,
                            std_dev=0.5, k=2.0, sample_id="QC1")],
            maintenance=[self.task(last_done="2025-01-01")])
        row = {"Lab ID": "QC1", "RON": "91.2",
               "parsed_date": "2026-07-27", "parsed_time": "11:00:00"}
        ev = evaluate_machine(machine, [row], NOW)
        assert ev.status == STATUS_RED
        assert "Overdue" in ev.reason
        assert ev.maintenance[0]["status"] == STATUS_RED

    def test_due_maintenance_downgrades_green_to_yellow(self):
        machine = Machine(
            uid="m1",
            tests=[TestSpec(name="RON", value_col="RON", expected=91.0,
                            std_dev=0.5, k=2.0, sample_id="QC1")],
            maintenance=[self.task(last_done="")])
        row = {"Lab ID": "QC1", "RON": "91.2",
               "parsed_date": "2026-07-27", "parsed_time": "11:00:00"}
        ev = evaluate_machine(machine, [row], NOW)
        assert ev.status == STATUS_YELLOW


# ── Serial: frame assembly by idle gap ───────────────────────────────────────

class TestFrameAssembler:
    def test_frames_split_on_idle_gap(self):
        fa = FrameAssembler(idle_gap=0.3)
        assert fa.feed(b"REPORT 1 ", 0.0) == []
        assert fa.feed(b"END", 0.1) == []
        assert fa.feed(b"NEXT", 1.0) == ["REPORT 1 END"]
        assert fa.flush() == ["NEXT"]

    def test_flush_on_empty_buffer_gives_nothing(self):
        fa = FrameAssembler(idle_gap=0.3)
        assert fa.flush() == []

    def test_cp1252_bytes_decode(self):
        fa = FrameAssembler(idle_gap=0.3)
        fa.feed(b"Temp \xb0C", 0.0)
        assert fa.flush() == ["Temp °C"]


# ── Serial backends: Qt preferred, raw ctypes/termios fallback ───────────────

class TestSerialSettings:
    def test_windows_dcb_mapping(self):
        from lem_station_module import _win_serial_settings
        m = Machine(baud_rate=19200, parity="E", stop_bits=2.0, byte_size=7)
        assert _win_serial_settings(m) == (19200, 7, 2, 2)

    def test_defaults_and_clamping(self):
        from lem_station_module import _win_serial_settings
        m = Machine(baud_rate=9600, parity="?", stop_bits=1.5, byte_size=3)
        baud, size, parity, stop = _win_serial_settings(m)
        assert (baud, size, parity, stop) == (9600, 5, 0, 1)


class TestSerialFallback:
    def test_raw_reader_used_when_qt_serial_missing(self, qapp, tmp_path,
                                                    monkeypatch):
        created = []

        class FakeRawReader:
            def __init__(self, machine):
                created.append(machine)
                self.error = None
                self._frames = ["QC1,91.2"]

            def start(self):
                pass

            def take_frames(self):
                out, self._frames = self._frames, []
                return out

            def close(self):
                pass

        monkeypatch.setattr(mod, "_qt_serial_available", lambda: False)
        monkeypatch.setattr(mod, "_RawSerialReader", FakeRawReader)
        ctx = FakeContext()
        m = make_module(ctx)
        machine = sample_machine(tmp_path, source_type="serial",
                                 com_port="COM3")
        m.set_machine(machine)
        m.process_now(now=NOW)
        assert created and created[0] is machine
        assert ("QC1", "RON", "91.2", "LEM Station") in ctx.results
        m.shutdown()

    def test_reader_open_failure_reports_reason(self, qapp, tmp_path,
                                                monkeypatch):
        class ExplodingReader:
            def __init__(self, machine):
                raise OSError("Access denied")

        monkeypatch.setattr(mod, "_qt_serial_available", lambda: False)
        monkeypatch.setattr(mod, "_RawSerialReader", ExplodingReader)
        m = make_module()
        m.set_machine(sample_machine(tmp_path, source_type="serial",
                                     com_port="COM3"))
        m.process_now(now=NOW)
        assert m.evaluation().status == STATUS_UNKNOWN
        assert "COM3" in m.evaluation().reason
        m.shutdown()

    def test_serial_without_port_asks_for_config(self, qapp, tmp_path):
        m = make_module()
        m.set_machine(sample_machine(tmp_path, source_type="serial",
                                     com_port=""))
        m.process_now(now=NOW)
        assert m.evaluation().status == STATUS_UNKNOWN
        assert "COM port" in m.evaluation().reason
        m.shutdown()


# ── Text-detection pattern builder (no regex knowledge required) ─────────────

class TestBuildDetectionPattern:
    def test_label_and_number_become_anchored_pattern(self):
        from lem_station_module import build_detection_pattern, extract_value
        pattern = build_detection_pattern("Cloud point : -15.0°C")
        assert pattern
        sel = Selector(mode="detect", pattern=pattern)
        # matches the original...
        assert extract_value(sel, "junk\nCloud point : -15.0°C\nmore", ",") == "-15.0"
        # ...and the same label with a different value / spacing
        assert extract_value(sel, "Cloud point:  -21.5°C", ",") == "-21.5"

    def test_flexible_about_whitespace_and_regex_chars_in_label(self):
        from lem_station_module import build_detection_pattern, extract_value
        pattern = build_detection_pattern("--- Result (avg): 91.2 ---")
        sel = Selector(mode="detect", pattern=pattern)
        assert extract_value(sel, "--- Result (avg): 88.7 ---", ",") == "88.7"

    def test_label_only_sample_anchors_on_the_whole_label(self):
        # No number in the sample → the whole text becomes the label and the
        # number AFTER it is captured on future prints.
        from lem_station_module import build_detection_pattern, extract_value
        pattern = build_detection_pattern("Cloud point :")
        sel = Selector(mode="detect", pattern=pattern)
        assert extract_value(sel, "Cloud point : -18.5°C", ",") == "-18.5"

    def test_empty_sample_gives_none(self):
        from lem_station_module import build_detection_pattern
        assert build_detection_pattern("") is None


class TestDetectionPatternText:
    """Label-based detection must also capture TEXT (Lab IDs, grades) and
    work from a label alone — 'Cloud point :' → the string after it."""

    def test_text_capture_from_label_and_value(self):
        from lem_station_module import build_detection_pattern, extract_value
        pattern = build_detection_pattern("Sample ID : 36873", capture="text")
        sel = Selector(mode="detect", pattern=pattern)
        assert extract_value(sel, "Sample ID : 36873", ",") == "36873"
        assert extract_value(sel, "Sample ID :  26-00412", ",") == "26-00412"

    def test_label_only_sample_captures_what_follows(self):
        from lem_station_module import build_detection_pattern, extract_value
        # the user typed/selected just the label — no value in the sample
        pattern = build_detection_pattern("Cloud point :", capture="text")
        sel = Selector(mode="detect", pattern=pattern)
        assert extract_value(sel, "junk\nCloud point : -15.0°C", ",") == "-15.0°C"

    def test_number_capture_from_label_only(self):
        from lem_station_module import build_detection_pattern, extract_value
        pattern = build_detection_pattern("Cloud point :", capture="number")
        sel = Selector(mode="detect", pattern=pattern)
        assert extract_value(sel, "Cloud point : -21.5°C", ",") == "-21.5"

    def test_text_capture_without_label_gives_none(self):
        from lem_station_module import build_detection_pattern
        assert build_detection_pattern("", capture="text") is None


class TestLabIdCleanFlow:
    """Lab ID flows through the same pipeline: selector + clean tools."""

    def test_lab_id_clean_ops_strip_the_label(self, tmp_path):
        from lem_station_module import parse_print
        machine = Machine(
            uid="m1", delimiter=",",
            lab_id=Selector(mode="cell", index=0,
                            clean=["remove:Sample ID :", "strip"]),
            mappings=[MethodMapping(methods=["Cloud Point"],
                                    selector=Selector(mode="cell", index=1))])
        result = parse_print(machine, "Sample ID : 36873,-15.0")
        assert result.lab_id == "36873"

    def test_lab_id_detect_mode(self):
        from lem_station_module import parse_print
        machine = Machine(
            uid="m1",
            lab_id=Selector(mode="detect", pattern=r"Sample\s*ID\s*:\s*(\S+)"),
            mappings=[MethodMapping(
                methods=["Cloud Point"],
                selector=Selector(mode="detect",
                                  pattern=r"Cloud point :\s*(-?\d+\.?\d*)"))])
        result = parse_print(machine,
                             "Sample ID : 36873\nCloud point : -15.0°C")
        assert result.lab_id == "36873"
        assert result.values == {"Cloud Point": "-15.0"}


class TestMethodPicker:
    def test_scrollable_checkbox_picker(self, qapp):
        methods = [f"Method {i}" for i in range(300)]
        dlg = mod._MethodPickerDialog(methods, None)
        assert dlg._list.count() == 300
        dlg._list.item(3).setCheckState(QtCore_checked())
        dlg._list.item(7).setCheckState(QtCore_checked())
        assert dlg.selected_methods() == ["Method 3", "Method 7"]

    def test_filter_narrows_visible_items(self, qapp):
        dlg = mod._MethodPickerDialog(["Cloud Point", "Pour Point", "RON"],
                                      None)
        dlg._filter.setText("point")
        visible = [dlg._list.item(i).text() for i in range(dlg._list.count())
                   if not dlg._list.item(i).isHidden()]
        assert visible == ["Cloud Point", "Pour Point"]

    def test_checked_survive_filtering(self, qapp):
        dlg = mod._MethodPickerDialog(["Cloud Point", "Pour Point", "RON"],
                                      None)
        dlg._list.item(2).setCheckState(QtCore_checked())  # RON
        dlg._filter.setText("point")
        dlg._filter.setText("")
        assert dlg.selected_methods() == ["RON"]


def QtCore_checked():
    from PySide6 import QtCore
    return QtCore.Qt.CheckState.Checked


class TestSerialContinuousWatch:
    def test_serial_machine_uses_fast_drain_not_slow_poll(self, qapp,
                                                          tmp_path):
        m = make_module()
        m.set_machine(sample_machine(tmp_path, source_type="serial",
                                     com_port="COM3"))
        m.on_finish_loading()
        assert m._drain_timer.isActive()
        assert m._drain_timer.interval() <= 1000
        # the slow timer stays for the periodic LabCore sync tick
        assert m._timer.isActive()
        m.shutdown()
        assert not m._drain_timer.isActive()

    def test_csv_machine_does_not_run_the_drain_timer(self, qapp, tmp_path):
        m = make_module()
        m.set_machine(sample_machine(tmp_path))
        m.on_finish_loading()
        assert not m._drain_timer.isActive()
        m.shutdown()

    def test_drain_with_no_frames_does_no_heavy_work(self, qapp, tmp_path,
                                                     monkeypatch):
        class IdleReader:
            error = None

            def __init__(self, machine):
                pass

            def start(self):
                pass

            def take_frames(self):
                return []

            def close(self):
                pass

        monkeypatch.setattr(mod, "_qt_serial_available", lambda: False)
        monkeypatch.setattr(mod, "_RawSerialReader", IdleReader)
        m = make_module()
        m.set_machine(sample_machine(tmp_path, source_type="serial",
                                     com_port="COM3"))
        calls = []
        monkeypatch.setattr(m, "_dispatch_pipeline",
                            lambda *a, **k: calls.append(a))
        m._drain_serial()
        assert calls == []  # nothing arrived → no parse/sync/UI pass
        m.shutdown()


class TestRecentPrints:
    def test_module_keeps_raw_prints_for_testing_the_parser(self, qapp,
                                                            tmp_path):
        m = make_module()
        (tmp_path / "in.csv").write_text("QC1,91.2\n26-001,87.6\n")
        m.set_machine(sample_machine(tmp_path))
        m.process_now(now=NOW)
        assert m.recent_prints() == ["26-001,87.6", "QC1,91.2"]
        m.shutdown()


# ── The machine universe: lem_machine_log ────────────────────────────────────

class TestMachineLog:
    def test_ddl_creates_the_log_table(self):
        assert "lem_machine_log" in LOG_TABLE_DDL
        assert "CREATE TABLE IF NOT EXISTS" in LOG_TABLE_DDL

    def test_build_log_insert_shape(self):
        sql, args = build_log_insert(
            "m1", "override", NOW, lab_id="", test_name="", value="",
            detail={"status": STATUS_SERVICE, "comment": "pump noise"})
        assert "INSERT INTO lem_machine_log" in sql
        assert args[0] == "m1"
        assert args[1] == "2026-07-27T12:00:00"
        assert args[2] == "override"
        detail = json.loads(args[6])
        assert detail["comment"] == "pump noise"


# ── Settings dialog: source-aware visibility + first-run gating ──────────────

class TestDialogVisibility:
    def dialog(self, **machine_kw):
        base = dict(uid="m1", title="X")
        base.update(machine_kw)
        return mod._MachineDialog(Machine(**base), None)

    def test_csv_source_hides_serial_row(self, qapp):
        d = self.dialog(source_type="single_csv")
        assert not d._serial_wrap.isVisibleTo(d)
        assert d._file_wrap.isVisibleTo(d)

    def test_serial_source_hides_file_row(self, qapp):
        d = self.dialog(source_type="serial")
        assert d._serial_wrap.isVisibleTo(d)
        assert not d._file_wrap.isVisibleTo(d)

    def test_switching_source_updates_visibility(self, qapp):
        d = self.dialog(source_type="single_csv")
        d._pick_source("serial", "Serial (RS-232)")
        assert d._serial_wrap.isVisibleTo(d)
        assert not d._file_wrap.isVisibleTo(d)

    def test_no_template_disables_mapping_area_with_hint(self, qapp):
        d = self.dialog(template="")
        assert not d._mapping_area.isEnabled()
        assert d._waiting_label.isVisibleTo(d)

    def test_template_enables_mapping_area(self, qapp):
        d = self.dialog(template="QC1,91.2")
        assert d._mapping_area.isEnabled()
        assert not d._waiting_label.isVisibleTo(d)

    def test_loading_a_config_with_a_template_reenables_mapping_area(self, qapp):
        from lem_station_module import machine_from_config_payload
        d = self.dialog(template="")
        assert not d._mapping_area.isEnabled()
        source = Machine(uid="src", title="Src", template="QC1,91.2")
        loaded = machine_from_config_payload(source.to_dict(), "m1")
        d._apply_machine(loaded)
        assert d._mapping_area.isEnabled()


# ── Config held on the server (fleet setup, surviving reinstalls) ────────────
#
# Configs live in LabCore's lem_machine_config, not in files. What used to be
# checked through export/import is checked here through the SQL the module
# publishes and the Machine it rebuilds from a stored blob.

class TestServerConfigRoundTrip:
    def make(self):
        return Machine(
            uid="m1", title="OptiMPP 1", source_type="serial",
            com_port="COM4", baud_rate=19200, parity="E", idle_gap=0.5,
            template="Sample ID : 36873\nCloud point : -15.0",
            lab_id=Selector(mode="detect", pattern=r"Sample\s*ID\s*:\s*(\S+)"),
            mappings=[MethodMapping(
                methods=["Cloud Point"],
                selector=Selector(mode="detect", pattern=r"Cloud point :(.+)",
                                  clean=["keep_number"]),
                qc_sample_id="QC-CP-1", qc_expire_hours=48.0)],
            maintenance=[MaintTask(uid="t1", name="Annual cal",
                                   kind="calibration", interval_days=365,
                                   last_done="2026-01-15")],
            manual_override=STATUS_SERVICE, override_comment="down",
            last_position=9999, last_mtime=123.0)

    def stored_blob(self, machine=None):
        """The JSON this machine would publish to lem_machine_config."""
        from lem_station_module import build_config_upsert
        _sql, args = build_config_upsert(machine or self.make(),
                                         datetime(2026, 8, 3, 9, 0))
        return args[2]

    def test_round_trip_preserves_parser_config(self):
        from lem_station_module import machine_from_config_payload
        m2 = machine_from_config_payload(self.stored_blob(), "new-uid")
        assert m2.title == "OptiMPP 1"
        assert m2.source_type == "serial"
        assert m2.com_port == "COM4"
        assert m2.baud_rate == 19200
        assert m2.mappings[0].methods == ["Cloud Point"]
        assert m2.mappings[0].qc_sample_id == "QC-CP-1"
        assert m2.mappings[0].selector.clean == ["keep_number"]
        assert m2.lab_id.pattern == r"Sample\s*ID\s*:\s*(\S+)"
        assert m2.template.startswith("Sample ID")
        assert m2.maintenance[0].name == "Annual cal"

    def test_adopting_a_config_binds_it_to_the_row_it_came_from(self):
        from lem_station_module import machine_from_config_payload
        m2 = machine_from_config_payload(self.stored_blob(), "new-uid")
        assert m2.uid == "new-uid"

    def test_a_machine_keeps_its_own_runtime_state_when_it_saves_itself(self):
        """Only a COPY starts fresh — a machine reloading its own config must
        keep its ingest offset or it re-parses every print it already read."""
        from lem_station_module import machine_from_config_payload
        m2 = machine_from_config_payload(self.stored_blob(), "m1")
        assert m2.last_position == 9999
        assert m2.last_mtime == 123.0

    def test_duplicating_starts_the_copy_fresh(self):
        from lem_station_module import duplicated_machine
        copy = duplicated_machine(self.make(), "OptiMPP 2")
        assert copy.uid != "m1"             # its own identity
        assert copy.last_position == 0      # fresh ingest offsets
        assert copy.last_mtime == 0.0
        assert copy.manual_override == ""   # overrides never travel
        assert copy.override_comment == ""
        assert copy.mappings[0].qc_sample_id == "QC-CP-1"   # setup does

    def test_the_config_is_stored_as_json(self):
        import json as _json
        assert _json.loads(self.stored_blob())["title"] == "OptiMPP 1"

    def test_an_unreadable_stored_config_is_reported_not_swallowed(self):
        from lem_station_module import machine_from_config_payload
        with pytest.raises(ValueError):
            machine_from_config_payload("not json {", "x")
        with pytest.raises(ValueError):
            machine_from_config_payload('["a list"]', "x")


# ── Machine serialization: v3 fields ─────────────────────────────────────────

class TestMachineSerializationV3:
    def test_serial_and_maintenance_round_trip(self):
        m = Machine(uid="m1", title="GC", source_type="serial",
                    com_port="COM3", baud_rate=19200, parity="E",
                    stop_bits=2.0, byte_size=7, idle_gap=0.5,
                    override_comment="operator said so",
                    maintenance=[MaintTask(uid="t1", name="PM", kind="pm",
                                           interval_days=30,
                                           last_done="2026-07-01")])
        m2 = Machine.from_dict(m.to_dict())
        assert m2 == m

    def test_defaults(self):
        m = Machine.from_dict({})
        assert m.parity == "N"
        assert m.stop_bits == 1.0
        assert m.byte_size == 8
        assert m.idle_gap == 0.3
        assert m.maintenance == []
        assert m.override_comment == ""


# ── Module behavior: mandatory-comment override + event logging ──────────────

class FakeLabCore:
    def __init__(self):
        self.sqls = []

    def write(self, operation, params, source="LabStation"):
        return {"ok": True}

    def sql(self, sql, args=None, source="LabStation"):
        self.sqls.append((sql, args))
        return {"ok": True, "rows_affected": 1}

    def read_sql(self, sql, args=None):
        return {"error": "no such table"}

    def is_running(self):
        return True

    def log_kinds(self):
        """The `kind` column of every record written to lem_machine_log.

        The drain batches many records into ONE multi-row INSERT (notes.md rule
        (c) — see `build_log_batch`), so the args of a single statement are the
        seven columns of each record laid end to end. Reading args[2] alone saw
        only the first record of each batch.
        """
        kinds = []
        for sql, args in self.sqls:
            if "INSERT INTO lem_machine_log" in sql and args:
                for i in range(0, len(args), 7):
                    kinds.append(args[i + 2])
        return kinds


@pytest.fixture
def fake_labcore(monkeypatch):
    fake = FakeLabCore()
    monkeypatch.setattr(mod, "labcore_write", fake.write, raising=False)
    monkeypatch.setattr(mod, "labcore_sql", fake.sql, raising=False)
    monkeypatch.setattr(mod, "labcore_read_sql", fake.read_sql, raising=False)
    monkeypatch.setattr(mod, "labcore_is_running", fake.is_running,
                        raising=False)
    return fake


class TestModuleV3:
    def test_a_qc_standard_lands_in_the_machine_log_as_qc_only(
            self, qapp, tmp_path, fake_labcore):
        """QC1 is the standard's Lab ID, so this print is a check — not also a
        production run. See test_qc_logging.py."""
        m = make_module()
        (tmp_path / "in.csv").write_text("QC1,91.2\n")
        m.set_machine(sample_machine(tmp_path))
        m.process_now(now=NOW)
        kinds = fake_labcore.log_kinds()
        assert "qc" in kinds
        assert "run" not in kinds
        assert "status_change" in kinds
        m.shutdown()

    def test_an_ordinary_sample_lands_in_the_machine_log_as_a_run(
            self, qapp, tmp_path, fake_labcore):
        m = make_module()
        (tmp_path / "in.csv").write_text("37043,91.2\n")
        m.set_machine(sample_machine(tmp_path))
        m.process_now(now=NOW)
        kinds = fake_labcore.log_kinds()
        assert "run" in kinds
        assert "qc" not in kinds
        m.shutdown()

    def test_override_requires_comment(self, qapp, tmp_path, monkeypatch,
                                       fake_labcore):
        from PySide6 import QtWidgets
        m = make_module()
        machine = sample_machine(tmp_path)
        m.set_machine(machine)

        monkeypatch.setattr(
            QtWidgets.QInputDialog, "getMultiLineText",
            staticmethod(lambda *a, **k: ("", True)))
        m._set_override(STATUS_DEAD)
        assert machine.manual_override == ""  # empty comment → refused

        monkeypatch.setattr(
            QtWidgets.QInputDialog, "getMultiLineText",
            staticmethod(lambda *a, **k: ("pump seized", True)))
        m._set_override(STATUS_DEAD)
        assert machine.manual_override == STATUS_DEAD
        assert machine.override_comment == "pump seized"
        assert "override" in fake_labcore.log_kinds()
        m.shutdown()

    def test_operator_comment_is_logged(self, qapp, tmp_path, fake_labcore):
        m = make_module()
        m.set_machine(sample_machine(tmp_path))
        m.add_comment("strange noise during run")
        assert "comment" in fake_labcore.log_kinds()
        m.shutdown()

    def test_completing_maintenance_logs_and_updates(self, qapp, tmp_path,
                                                     fake_labcore):
        m = make_module()
        machine = sample_machine(tmp_path)
        machine.maintenance = [MaintTask(uid="t1", name="Monthly PM",
                                         kind="pm", interval_days=30)]
        m.set_machine(machine)
        m.complete_task("t1", note="filters swapped", when=TODAY)
        assert machine.maintenance[0].last_done == "2026-07-27"
        assert "pm" in fake_labcore.log_kinds()
        m.shutdown()


# ── Latest-result temp file in LabStation's directory ────────────────────────

class TestLatestResultFile:
    ROW = {"Lab ID": "26-00412", "RON": "91.2", "MON": "90.1",
           "parsed_date": "2026-07-28", "parsed_time": "10:30:00"}

    def test_prefers_existing_roaming_apps_layout(self, monkeypatch, tmp_path):
        # Real installs live at %APPDATA%\LabLink\apps\LabStation
        from lem_station_module import labstation_dir
        roaming = tmp_path / "Roaming" / "LabLink" / "apps" / "LabStation"
        roaming.mkdir(parents=True)
        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
        assert labstation_dir() == str(roaming)

    def test_falls_back_to_existing_local_layout(self, monkeypatch, tmp_path):
        from lem_station_module import labstation_dir
        local = tmp_path / "Local" / "LabLink" / "LabStation"
        local.mkdir(parents=True)
        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
        assert labstation_dir() == str(local)

    def test_neither_exists_defaults_to_roaming_apps_path(self, monkeypatch,
                                                          tmp_path):
        from lem_station_module import labstation_dir
        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
        assert labstation_dir() == str(
            tmp_path / "Roaming" / "LabLink" / "apps" / "LabStation")

    def test_no_env_defaults_to_home_roaming(self, monkeypatch):
        from lem_station_module import labstation_dir
        monkeypatch.delenv("APPDATA", raising=False)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        assert labstation_dir() == os.path.join(
            os.path.expanduser("~"), "AppData", "Roaming", "LabLink",
            "apps", "LabStation")

    def test_filename_carries_machine_name_sanitized(self):
        from lem_station_module import latest_result_filename
        assert latest_result_filename("OptiMPP 1") == "lem_latest_OptiMPP 1.csv"
        assert latest_result_filename('Visc/40:"C*?') == "lem_latest_Visc_40__C__.csv"
        assert latest_result_filename("") == "lem_latest_machine.csv"

    def test_writes_header_plus_one_row(self, tmp_path):
        from lem_station_module import write_latest_result
        path = write_latest_result(self.ROW, "Eraspec",
                                   directory=str(tmp_path))
        assert path.endswith("lem_latest_Eraspec.csv")
        lines = open(path).read().splitlines()
        assert lines == [
            "machine,Lab ID,RON,MON,parsed_date,parsed_time",
            "Eraspec,26-00412,91.2,90.1,2026-07-28,10:30:00",
        ]

    def test_rewrites_instead_of_appending(self, tmp_path):
        from lem_station_module import write_latest_result
        write_latest_result(self.ROW, "Eraspec", directory=str(tmp_path))
        newer = {"Lab ID": "QC1", "Flash": "210",
                 "parsed_date": "2026-07-28", "parsed_time": "11:00:00"}
        path = write_latest_result(newer, "Eraspec", directory=str(tmp_path))
        lines = open(path).read().splitlines()
        assert len(lines) == 2          # header + ONE row, never appended
        assert "QC1" in lines[1]
        assert "26-00412" not in "".join(lines)

    def test_module_writes_latest_row_after_processing(self, qapp, tmp_path,
                                                       monkeypatch):
        lsdir = tmp_path / "lsdir"
        monkeypatch.setattr(mod, "labstation_dir", lambda: str(lsdir))
        m = make_module()
        (tmp_path / "in.csv").write_text("QC1,91.2\n26-001,87.6\n")
        m.set_machine(sample_machine(tmp_path))
        m.process_now(now=NOW)
        content = (lsdir / "lem_latest_Eraspec.csv").read_text()
        assert "26-001" in content      # the LATEST parsed row wins
        assert "QC1" not in content
        m.shutdown()

    def test_rename_cleans_up_the_old_file(self, qapp, tmp_path, monkeypatch):
        lsdir = tmp_path / "lsdir"
        monkeypatch.setattr(mod, "labstation_dir", lambda: str(lsdir))
        m = make_module()
        (tmp_path / "in.csv").write_text("QC1,91.2\n")
        machine = sample_machine(tmp_path)
        m.set_machine(machine)
        m.process_now(now=NOW)
        assert (lsdir / "lem_latest_Eraspec.csv").exists()
        assert machine.last_result_file == "lem_latest_Eraspec.csv"

        machine.title = "Eraspec 2"     # renamed in settings
        with open(tmp_path / "in.csv", "a") as f:
            f.write("QC1,91.4\n")
        m.process_now(now=NOW)
        assert (lsdir / "lem_latest_Eraspec 2.csv").exists()
        assert not (lsdir / "lem_latest_Eraspec.csv").exists()
        m.shutdown()

    def test_duplicating_resets_last_result_file(self):
        """A copy pointing at the source's latest-result CSV would have two
        machines overwriting one file."""
        from lem_station_module import duplicated_machine
        machine = Machine(uid="m1", title="X",
                          last_result_file="lem_latest_X.csv")
        assert duplicated_machine(machine, "Y").last_result_file == ""

    def test_unwritable_dir_does_not_break_processing(self, qapp, tmp_path,
                                                      monkeypatch):
        monkeypatch.setattr(mod, "labstation_dir",
                            lambda: str(tmp_path / "in.csv" / "not-a-dir"))
        m = make_module()
        (tmp_path / "in.csv").write_text("QC1,91.2\n")
        m.set_machine(sample_machine(tmp_path))
        m.process_now(now=NOW)          # must not raise
        assert m.evaluation().status == STATUS_GREEN
        m.shutdown()


# ── CSV headers + alternate detections for the same method group ─────────────

class TestCsvHeaders:
    def test_csv_header_round_trips(self):
        m = MethodMapping(methods=["Cloud Point"], selector=Selector(index=1),
                          csv_header="Cloud Point")
        assert MethodMapping.from_dict(m.to_dict()).csv_header == "Cloud Point"
        assert MethodMapping.from_dict({"methods": []}).csv_header == ""

    def test_apply_csv_headers_groups_methods_under_one_column(self):
        from lem_station_module import apply_csv_headers
        machine = Machine(uid="m1", mappings=[MethodMapping(
            methods=["ASTM D7689 - Cloud Point, Automatic Tilt Method",
                     "Cloud Point, mini method"],
            selector=Selector(index=1), csv_header="Cloud Point")])
        row = {"Lab ID": "35653",
               "ASTM D7689 - Cloud Point, Automatic Tilt Method": "-9.1",
               "Cloud Point, mini method": "-9.1",
               "parsed_date": "2026-07-28", "parsed_time": "15:00:00"}
        out = apply_csv_headers(row, machine)
        assert out == {"Lab ID": "35653", "Cloud Point": "-9.1",
                       "parsed_date": "2026-07-28", "parsed_time": "15:00:00"}

    def test_unheadered_methods_keep_their_names(self):
        from lem_station_module import apply_csv_headers
        machine = Machine(uid="m1", mappings=[MethodMapping(
            methods=["Pour Point"], selector=Selector(index=2))])
        row = {"Lab ID": "x", "Pour Point": "-31.0"}
        assert apply_csv_headers(row, machine) == row

    def test_latest_result_csv_uses_headers(self, qapp, tmp_path, monkeypatch):
        lsdir = tmp_path / "lsdir"
        monkeypatch.setattr(mod, "labstation_dir", lambda: str(lsdir))
        m = make_module()
        (tmp_path / "in.csv").write_text("QC1,91.2\n")
        machine = sample_machine(tmp_path)
        machine.mappings[0].csv_header = "Octane (RON)"
        m.set_machine(machine)
        m.process_now(now=NOW)
        content = (lsdir / "lem_latest_Eraspec.csv").read_text()
        assert "Octane (RON)" in content.splitlines()[0]
        assert "91.2" in content.splitlines()[1]
        m.shutdown()


class TestAlternateDetections:
    METHODS = ["ASTM D7689 - Cloud Point", "Cloud Point"]

    def machine(self):
        return Machine(
            uid="m1", delimiter=",",
            lab_id=Selector(mode="detect", pattern=r"Sample:\s*(\S+)"),
            mappings=[
                MethodMapping(methods=list(self.METHODS),
                              selector=Selector(mode="detect",
                                                pattern=r"CP-A:\s*(-?\d+\.?\d*)")),
                MethodMapping(methods=list(self.METHODS),
                              selector=Selector(mode="detect",
                                                pattern=r"CP-B:\s*(-?\d+\.?\d*)")),
            ])

    def test_first_matching_alternate_wins(self):
        from lem_station_module import parse_print
        result = parse_print(self.machine(),
                             "Sample: 35653\nCP-A: -9.1\nCP-B: -12.0")
        assert result.values["Cloud Point"] == "-9.1"

    def test_missing_alternate_falls_through_to_the_other(self):
        from lem_station_module import parse_print
        result = parse_print(self.machine(), "Sample: 35653\nCP-B: -12.0")
        assert result.values["Cloud Point"] == "-12.0"

    def test_preview_marks_covered_alternate_instead_of_error(self, qapp):
        machine = self.machine()
        machine.template = "Sample: 35653\nCP-B: -12.0"
        d = mod._MachineDialog(machine, None)
        texts = [d._preview.item(i, 1).text()
                 for i in range(d._preview.rowCount())]
        # CP-A missed, but CP-B provides the methods → not an error state
        assert any("covered" in t for t in texts)
        assert not any("nothing extracted" in t for t in texts)

    def test_preview_still_flags_a_truly_missing_mapping(self, qapp):
        machine = self.machine()
        machine.template = "Sample: 35653"   # neither alternate matches
        d = mod._MachineDialog(machine, None)
        texts = [d._preview.item(i, 1).text()
                 for i in range(d._preview.rowCount())]
        assert any("nothing extracted" in t for t in texts)


class TestAbsentTestsProduceNoColumns:
    """A print that only carries SOME tests exports only those columns —
    no empty header, no blank cell for the missing ones."""

    def machine(self, tmp_path):
        return Machine(
            uid="m1", title="OptiMPP 1", source_type="single_csv",
            csv_path=str(tmp_path / "in.csv"), delimiter=",",
            lab_id=Selector(mode="detect", pattern=r"Sample:\s*(\S+)"),
            mappings=[
                MethodMapping(methods=["Cloud Point"], csv_header="Cloud Point",
                              selector=Selector(mode="detect",
                                                pattern=r"Cloud:\s*(-?\d+\.?\d*)")),
                MethodMapping(methods=["Pour Point"], csv_header="Pour Point",
                              selector=Selector(mode="detect",
                                                pattern=r"Pour:\s*(-?\d+\.?\d*)")),
            ])

    def test_pour_only_print_has_no_cloud_column(self, qapp, tmp_path,
                                                 monkeypatch):
        lsdir = tmp_path / "lsdir"
        monkeypatch.setattr(mod, "labstation_dir", lambda: str(lsdir))
        m = make_module()
        (tmp_path / "in.csv").write_text("Sample: 35653,Pour: -31.0\n")
        m.set_machine(self.machine(tmp_path))
        m.process_now(now=NOW)
        lines = (lsdir / "lem_latest_OptiMPP 1.csv").read_text().splitlines()
        assert lines[0] == "machine,Lab ID,Pour Point,parsed_date,parsed_time"
        assert "Cloud" not in lines[0]
        assert "-31.0" in lines[1]
        m.shutdown()

    def test_both_tests_present_gives_both_columns(self, qapp, tmp_path,
                                                   monkeypatch):
        lsdir = tmp_path / "lsdir"
        monkeypatch.setattr(mod, "labstation_dir", lambda: str(lsdir))
        m = make_module()
        (tmp_path / "in.csv").write_text(
            "Sample: 35653,Cloud: -9.1,Pour: -31.0\n")
        m.set_machine(self.machine(tmp_path))
        m.process_now(now=NOW)
        lines = (lsdir / "lem_latest_OptiMPP 1.csv").read_text().splitlines()
        assert lines[0] == ("machine,Lab ID,Cloud Point,Pour Point,"
                            "parsed_date,parsed_time")
        m.shutdown()

    def test_columns_shrink_again_on_the_next_partial_print(self, qapp,
                                                            tmp_path,
                                                            monkeypatch):
        lsdir = tmp_path / "lsdir"
        monkeypatch.setattr(mod, "labstation_dir", lambda: str(lsdir))
        m = make_module()
        (tmp_path / "in.csv").write_text(
            "Sample: 35653,Cloud: -9.1,Pour: -31.0\n")
        m.set_machine(self.machine(tmp_path))
        m.process_now(now=NOW)
        with open(tmp_path / "in.csv", "a") as f:
            f.write("Sample: 35654,Pour: -28.5\n")
        m.process_now(now=NOW)
        lines = (lsdir / "lem_latest_OptiMPP 1.csv").read_text().splitlines()
        assert lines[0] == "machine,Lab ID,Pour Point,parsed_date,parsed_time"
        assert len(lines) == 2  # still one row, old columns gone with it
        m.shutdown()

    def test_clean_op_that_empties_a_value_drops_the_column(self):
        from lem_station_module import parse_print
        machine = Machine(
            uid="m1", delimiter=",",
            lab_id=Selector(mode="cell", index=0),
            mappings=[MethodMapping(
                methods=["Cloud Point"],
                selector=Selector(mode="detect", pattern=r"Cloud:\s*(\S+)",
                                  clean=["keep_number"]))])
        # instrument printed a placeholder, keep_number leaves nothing
        result = parse_print(machine, "35653,Cloud: ---")
        assert "Cloud Point" not in result.values


# ── Multi CSV: processed files move into a "processed" subfolder ─────────────

class TestMultiCsvProcessedFolder:
    def machine(self, folder):
        return Machine(
            uid="m1", title="Multi", source_type="multi_csv",
            csv_path=str(folder), delimiter=",",
            lab_id=Selector(mode="cell", index=0),
            mappings=[MethodMapping(methods=["RON"],
                                    selector=Selector(mode="cell", index=1))])

    def test_pending_file_is_processed_then_moved(self, qapp, tmp_path):
        folder = tmp_path / "prints"
        folder.mkdir()
        (folder / "a.csv").write_text("QC1,91.2\n")
        m = make_module()
        m.set_machine(self.machine(folder))
        m.process_now(now=NOW)
        assert not (folder / "a.csv").exists()
        assert (folder / mod.PROCESSED_DIRNAME / "a.csv").read_text() \
            == "QC1,91.2\n"
        assert m.recent_prints() == ["QC1,91.2"]
        m.shutdown()

    def test_processed_folder_is_never_reprocessed(self, qapp, tmp_path):
        folder = tmp_path / "prints"
        folder.mkdir()
        (folder / "a.csv").write_text("QC1,91.2\n")
        m = make_module()
        m.set_machine(self.machine(folder))
        m.process_now(now=NOW)
        m.process_now(now=NOW)
        m.process_now(now=NOW)
        assert m.recent_prints() == ["QC1,91.2"]   # exactly once
        m.shutdown()

    def test_any_file_present_is_processed_regardless_of_name_or_time(
            self, qapp, tmp_path):
        # No name/mtime tracking: an OLD file dropped in still gets picked up.
        import os as _os
        folder = tmp_path / "prints"
        folder.mkdir()
        old = folder / "zz_old.csv"
        old.write_text("QC9,88.8\n")
        _os.utime(old, (1000000, 1000000))
        m = make_module()
        m.set_machine(self.machine(folder))
        m.process_now(now=NOW)
        assert m.recent_prints() == ["QC9,88.8"]
        assert (folder / mod.PROCESSED_DIRNAME / "zz_old.csv").exists()
        m.shutdown()

    def test_same_filename_twice_does_not_clobber_the_archive(self, qapp,
                                                              tmp_path):
        folder = tmp_path / "prints"
        folder.mkdir()
        (folder / "run.csv").write_text("QC1,91.2\n")
        m = make_module()
        m.set_machine(self.machine(folder))
        m.process_now(now=NOW)
        (folder / "run.csv").write_text("QC2,90.4\n")
        m.process_now(now=NOW)
        archived = sorted(p.name for p in
                          (folder / mod.PROCESSED_DIRNAME).iterdir())
        assert len(archived) == 2          # both kept, second renamed
        assert archived[0] == "run.csv"
        contents = {(folder / mod.PROCESSED_DIRNAME / n).read_text()
                    for n in archived}
        assert contents == {"QC1,91.2\n", "QC2,90.4\n"}
        m.shutdown()

    def test_unmovable_file_is_not_delivered_so_it_cannot_duplicate(
            self, qapp, tmp_path, monkeypatch):
        folder = tmp_path / "prints"
        folder.mkdir()
        (folder / "locked.csv").write_text("QC1,91.2\n")
        m = make_module()
        m.set_machine(self.machine(folder))
        monkeypatch.setattr(mod.shutil, "move",
                            lambda *a, **k: (_ for _ in ()).throw(
                                OSError("file in use")))
        m.process_now(now=NOW)
        assert m.recent_prints() == []     # nothing consumed
        assert (folder / "locked.csv").exists()
        m.shutdown()

    def test_missing_folder_reports_error(self, qapp, tmp_path):
        m = make_module()
        m.set_machine(self.machine(tmp_path / "nope"))
        m.process_now(now=NOW)
        assert m.evaluation().status == STATUS_UNKNOWN
        assert "not found" in m.evaluation().reason.lower()
        m.shutdown()


# ── QC sample self-detection (the old LEM's QC-sample model) ─────────────────

QC_LIBRARY = [
    {"name": "Cloud CRM", "sample_id_val": "CP", "tests": [
        {"name": "Cloud - D7689", "value_col": "Cloud Point",
         "expected": -7.4, "std_dev": 2.8, "k": 1.0, "units": "C"}]},
    {"name": "Pour CRM", "sample_id_val": "PP", "tests": [
        {"name": "Pour Point - D7346", "value_col": "Pour Point",
         "expected": -18.3, "std_dev": 6.4, "k": 1.0}]},
]


# Renamed from TestQcSampleDetection 2026-08-03. There is no detection any more:
# QC comes from an assignment or it does not exist. See test_manual_qc_only.py.
BOTH_ASSIGNED = [{"sample": "Cloud CRM", "test": "Cloud Point"},
                 {"sample": "Pour CRM", "test": "Pour Point"}]


class TestQcSpecsFromAssignedStandards:
    def machine(self, **kw):
        base = dict(uid="m1", title="OptiMPP 1", mappings=[
            MethodMapping(methods=["Cloud Point"],
                          selector=Selector(mode="cell", index=1)),
            MethodMapping(methods=["Pour Point"],
                          selector=Selector(mode="cell", index=2))])
        base.update(kw)
        return Machine(**base)

    def test_specs_derived_from_an_assigned_qc_sample(self):
        from lem_station_module import specs_from_qc_samples
        specs = specs_from_qc_samples(self.machine(), QC_LIBRARY,
                                      targets=BOTH_ASSIGNED)
        by_name = {s.name: s for s in specs}
        assert set(by_name) == {"Cloud Point", "Pour Point"}
        cloud = by_name["Cloud Point"]
        assert cloud.sample_id == "CP"          # detect QC by this Lab ID
        assert cloud.expected == -7.4
        assert cloud.std_dev == 2.8
        assert cloud.k == 1.0
        assert cloud.units == "C"

    def test_matches_on_the_qc_test_name_too(self):
        from lem_station_module import specs_from_qc_samples
        machine = self.machine(mappings=[MethodMapping(
            methods=["Cloud - D7689"], selector=Selector(index=1))])
        specs = specs_from_qc_samples(
            machine, QC_LIBRARY,
            targets=[{"sample": "Cloud CRM", "test": "Cloud - D7689"}])
        assert [s.name for s in specs] == ["Cloud - D7689"]
        assert specs[0].sample_id == "CP"

    def test_methods_with_no_qc_sample_get_no_spec(self):
        from lem_station_module import specs_from_qc_samples
        machine = self.machine(mappings=[MethodMapping(
            methods=["Sulfur"], selector=Selector(index=1))])
        assert specs_from_qc_samples(machine, QC_LIBRARY,
                                     targets=BOTH_ASSIGNED) == []

    def test_empty_library_gives_no_specs(self):
        from lem_station_module import specs_from_qc_samples
        assert specs_from_qc_samples(self.machine(), [],
                                     targets=BOTH_ASSIGNED) == []

    def test_explicit_mapping_qc_still_wins(self):
        """A hand-set QC sample on the mapping overrides the standard's Lab ID —
        the bench runs its own material under that ID. Still needs the assignment
        to say WHICH test is being checked."""
        from lem_station_module import specs_from_qc_samples
        machine = self.machine(mappings=[MethodMapping(
            methods=["Cloud Point"], selector=Selector(index=1),
            qc_sample_id="HOUSE-STD")])
        specs = specs_from_qc_samples(
            machine, QC_LIBRARY,
            targets=[{"sample": "Cloud CRM", "test": "Cloud Point"}])
        assert specs[0].sample_id == "HOUSE-STD"


class TestQcSampleParsing:
    def test_parse_qc_samples_payload(self):
        from lem_station_module import parse_qc_sample_rows
        rows = [{"name": "Cloud CRM", "sample_id_val": "CP",
                 "tests": '[{"name": "Cloud - D7689", "value_col": '
                          '"Cloud Point", "expected": -7.4, "std_dev": 2.8, '
                          '"k": 1.0, "units": "C"}]'}]
        library = parse_qc_sample_rows(rows)
        assert library[0]["sample_id_val"] == "CP"
        assert library[0]["tests"][0]["value_col"] == "Cloud Point"

    def test_bad_json_is_skipped(self):
        from lem_station_module import parse_qc_sample_rows
        rows = [{"name": "X", "sample_id_val": "X1", "tests": "{not json"}]
        assert parse_qc_sample_rows(rows) == [
            {"name": "X", "sample_id_val": "X1", "tests": []}]


class TestModulePullsQcSamples:
    def test_running_the_crm_turns_qc_green(self, qapp, tmp_path,
                                            monkeypatch):
        """End to end: the parser sees Lab ID 'CP', recognises the QC sample
        and checks Cloud Point against it — no per-machine QC setup."""
        import json as _json

        class LabCore:
            def __init__(self):
                self.sqls = []

            def write(self, op, params, source="x"):
                return {"ok": True}

            def sql(self, sql, args=None, source="x"):
                self.sqls.append((sql, args))
                return {"ok": True}

            def read_sql(self, sql, args=None):
                if "lem_qc_samples" in sql:
                    return {"ok": True, "rows": [{
                        "name": "Cloud CRM", "sample_id_val": "CP",
                        "tests": _json.dumps([
                            {"name": "Cloud - D7689", "value_col": "Cloud Point",
                             "expected": -7.4, "std_dev": 2.8, "k": 1.0}])}]}
                if "lem_machine_targets" in sql:
                    # Required since 2026-08-03: QC is assigned, not detected.
                    return {"ok": True, "rows": [{"machine_uid": "m1",
                                                  "sample_name": "Cloud CRM",
                                                  "test_name": "Cloud Point"}]}
                return {"error": "no such table"}

            def is_running(self):
                return True

        fake = LabCore()
        monkeypatch.setattr(mod, "labcore_write", fake.write, raising=False)
        monkeypatch.setattr(mod, "labcore_sql", fake.sql, raising=False)
        monkeypatch.setattr(mod, "labcore_read_sql", fake.read_sql, raising=False)
        monkeypatch.setattr(mod, "labcore_is_running", fake.is_running,
                            raising=False)

        m = make_module()
        (tmp_path / "in.csv").write_text("CP,-7.5\n")
        machine = Machine(
            uid="m1", title="OptiMPP 1", source_type="single_csv",
            csv_path=str(tmp_path / "in.csv"), delimiter=",",
            lab_id=Selector(mode="cell", index=0),
            mappings=[MethodMapping(methods=["Cloud Point"],
                                    selector=Selector(mode="cell", index=1))])
        m.set_machine(machine)
        m.process_now(now=NOW)

        assert [t.name for t in machine.tests] == ["Cloud Point"]
        assert machine.tests[0].sample_id == "CP"
        assert m.evaluation().status == STATUS_GREEN   # -7.5 within -10.2…-4.6
        m.shutdown()

    def test_out_of_spec_crm_turns_red(self, qapp, tmp_path, monkeypatch):
        import json as _json

        def read_sql(sql, args=None):
            if "lem_qc_samples" in sql:
                return {"ok": True, "rows": [{
                    "name": "Cloud CRM", "sample_id_val": "CP",
                    "tests": _json.dumps([
                        {"name": "Cloud - D7689", "value_col": "Cloud Point",
                         "expected": -7.4, "std_dev": 2.8, "k": 1.0}])}]}
            if "lem_machine_targets" in sql:
                # Required since 2026-08-03: QC is assigned, not detected.
                return {"ok": True, "rows": [{"machine_uid": "m1",
                                              "sample_name": "Cloud CRM",
                                              "test_name": "Cloud Point"}]}
            return {"error": "no such table"}

        monkeypatch.setattr(mod, "labcore_write", lambda *a, **k: {"ok": True},
                            raising=False)
        monkeypatch.setattr(mod, "labcore_sql", lambda *a, **k: {"ok": True},
                            raising=False)
        monkeypatch.setattr(mod, "labcore_read_sql", read_sql, raising=False)
        monkeypatch.setattr(mod, "labcore_is_running", lambda: True,
                            raising=False)
        m = make_module()
        (tmp_path / "in.csv").write_text("CP,-15.0\n")   # way below -10.2
        m.set_machine(Machine(
            uid="m1", title="OptiMPP 1", source_type="single_csv",
            csv_path=str(tmp_path / "in.csv"), delimiter=",",
            lab_id=Selector(mode="cell", index=0),
            mappings=[MethodMapping(methods=["Cloud Point"],
                                    selector=Selector(mode="cell", index=1))]))
        m.process_now(now=NOW)
        assert m.evaluation().status == STATUS_RED
        m.shutdown()


class TestAssignedQcTargets:
    """The master view pins exactly which QC sample + test an instrument is
    checked against (V4's watched targets). Since 2026-08-03 this is the ONLY way
    in — there is no detection to fall back on."""

    LIB = [
        {"name": "Cloud CRM", "sample_id_val": "CP", "tests": [
            {"name": "Cloud Point", "value_col": "Cloud Point",
             "expected": -7.4, "std_dev": 2.8, "k": 1.0, "units": "C"}]},
        {"name": "Pour CRM", "sample_id_val": "PP", "tests": [
            {"name": "Pour Point", "value_col": "Pour Point",
             "expected": -18.3, "std_dev": 6.4, "k": 1.0, "units": "C"}]},
    ]

    def machine(self):
        return Machine(uid="m1", mappings=[
            MethodMapping(methods=["Cloud Point"], selector=Selector(index=1)),
            MethodMapping(methods=["Pour Point"], selector=Selector(index=2))])

    def test_assignment_narrows_qc_to_the_named_targets(self):
        from lem_station_module import specs_from_qc_samples
        specs = specs_from_qc_samples(
            self.machine(), self.LIB,
            targets=[{"sample": "Cloud CRM", "test": "Cloud Point"}])
        assert [s.name for s in specs] == ["Cloud Point"]
        assert specs[0].sample_id == "CP"

    def test_no_assignment_means_no_qc_at_all(self):
        """Was `test_no_assignment_falls_back_to_detection`, and that fallback is
        exactly what put Multitek NS on RED for a check nobody assigned."""
        from lem_station_module import specs_from_qc_samples
        assert specs_from_qc_samples(self.machine(), self.LIB, targets=[]) == []

    def test_assignment_for_a_method_this_machine_cannot_parse_is_ignored(self):
        from lem_station_module import specs_from_qc_samples
        specs = specs_from_qc_samples(
            self.machine(), self.LIB,
            targets=[{"sample": "Cloud CRM", "test": "Sulfur"}])
        assert specs == []

    def test_module_pulls_assignments_from_labcore(self, qapp, tmp_path,
                                                   monkeypatch):
        import json as _json

        def read_sql(sql, args=None):
            if "lem_qc_samples" in sql:
                return {"ok": True, "rows": [{
                    "name": "Cloud CRM", "sample_id_val": "CP",
                    "tests": _json.dumps(self.LIB[0]["tests"])}, {
                    "name": "Pour CRM", "sample_id_val": "PP",
                    "tests": _json.dumps(self.LIB[1]["tests"])}]}
            if "lem_machine_targets" in sql:
                return {"ok": True, "rows": [
                    {"sample_name": "Cloud CRM", "test_name": "Cloud Point"}]}
            return {"error": "no such table"}

        monkeypatch.setattr(mod, "labcore_write", lambda *a, **k: {"ok": True},
                            raising=False)
        monkeypatch.setattr(mod, "labcore_sql", lambda *a, **k: {"ok": True},
                            raising=False)
        monkeypatch.setattr(mod, "labcore_read_sql", read_sql, raising=False)
        monkeypatch.setattr(mod, "labcore_is_running", lambda: True,
                            raising=False)

        m = make_module()
        (tmp_path / "in.csv").write_text("CP,-7.5,-20.0\n")
        machine = Machine(
            uid="m1", title="OptiMPP 1", source_type="single_csv",
            csv_path=str(tmp_path / "in.csv"), delimiter=",",
            lab_id=Selector(mode="cell", index=0),
            mappings=[MethodMapping(methods=["Cloud Point"],
                                    selector=Selector(mode="cell", index=1)),
                      MethodMapping(methods=["Pour Point"],
                                    selector=Selector(mode="cell", index=2))])
        m.set_machine(machine)
        m.process_now(now=NOW)
        # Only the assigned check is applied, even though both parse.
        assert [t.name for t in machine.tests] == ["Cloud Point"]
        assert m.evaluation().status == STATUS_GREEN
        m.shutdown()


# ── QC / PM / CAL sub-statuses (old LEM's three pills) ───────────────────────

class TestSubStatuses:
    def spec(self):
        return TestSpec(name="RON", value_col="RON", expected=91.0,
                        std_dev=0.5, k=2.0, sample_id="QC1")

    def row(self, value="91.2", date="2026-07-27"):
        return {"Lab ID": "QC1", "RON": value,
                "parsed_date": date, "parsed_time": "11:00:00"}

    def test_all_three_reported(self):
        ev = evaluate_machine(Machine(uid="m1", tests=[self.spec()]),
                              [self.row()], NOW)
        assert set(ev.sub_statuses) == {"qc", "pm", "calibration"}

    def test_qc_green_when_in_spec_and_fresh(self):
        ev = evaluate_machine(Machine(uid="m1", tests=[self.spec()]),
                              [self.row()], NOW)
        assert ev.sub_statuses["qc"] == STATUS_GREEN

    def test_qc_red_when_out_of_spec(self):
        ev = evaluate_machine(Machine(uid="m1", tests=[self.spec()]),
                              [self.row("95.0")], NOW)
        assert ev.sub_statuses["qc"] == STATUS_RED

    def test_qc_yellow_when_stale(self):
        ev = evaluate_machine(Machine(uid="m1", tests=[self.spec()]),
                              [self.row(date="2026-07-24")], NOW)
        assert ev.sub_statuses["qc"] == STATUS_YELLOW

    def test_qc_yellow_when_assigned_but_not_yet_run(self):
        """A spec with no data is a QC waiting to be run, not an unknown."""
        ev = evaluate_machine(Machine(uid="m1", tests=[self.spec()]), [], NOW)
        assert ev.sub_statuses["qc"] == STATUS_YELLOW

    def test_qc_unknown_only_when_nothing_is_assigned(self):
        ev = evaluate_machine(Machine(uid="m1", tests=[]), [], NOW)
        assert ev.sub_statuses["qc"] == STATUS_UNKNOWN

    def test_pm_and_cal_are_separate(self):
        machine = Machine(uid="m1", tests=[self.spec()], maintenance=[
            MaintTask(uid="p", name="Monthly PM", kind="pm",
                      interval_days=30, last_done="2026-07-20"),
            MaintTask(uid="c", name="Annual cal", kind="calibration",
                      interval_days=365, last_done="2020-01-01")])
        ev = evaluate_machine(machine, [self.row()], NOW)
        assert ev.sub_statuses["pm"] == STATUS_GREEN
        assert ev.sub_statuses["calibration"] == STATUS_RED

    def test_unconfigured_maintenance_is_unknown(self):
        ev = evaluate_machine(Machine(uid="m1", tests=[self.spec()]),
                              [self.row()], NOW)
        assert ev.sub_statuses["pm"] == STATUS_UNKNOWN
        assert ev.sub_statuses["calibration"] == STATUS_UNKNOWN

    def test_substatus_upsert_carries_the_three_pills(self):
        from lem_station_module import build_substatus_upsert
        ev = evaluate_machine(Machine(uid="m1", tests=[self.spec()]),
                              [self.row()], NOW)
        sql, args = build_substatus_upsert(Machine(uid="m1"), ev, NOW)
        assert "lem_machine_substatus" in sql
        assert args[0] == "m1"
        assert args[1] == STATUS_GREEN          # qc
        assert args[2] == STATUS_UNKNOWN        # pm
        assert args[3] == STATUS_UNKNOWN        # calibration

    def test_module_publishes_substatuses(self, qapp, tmp_path, monkeypatch):
        writes = []
        monkeypatch.setattr(mod, "labcore_write", lambda *a, **k: {"ok": True},
                            raising=False)
        monkeypatch.setattr(mod, "labcore_sql",
                            lambda sql, args=None, source="x": writes.append((sql, args))
                            or {"ok": True}, raising=False)
        monkeypatch.setattr(mod, "labcore_read_sql",
                            lambda *a, **k: {"error": "no table"}, raising=False)
        monkeypatch.setattr(mod, "labcore_is_running", lambda: True, raising=False)
        m = make_module()
        (tmp_path / "in.csv").write_text("QC1,91.2\n")
        m.set_machine(sample_machine(tmp_path))
        m.process_now(now=NOW)
        assert any("lem_machine_substatus" in s for s, _ in writes)
        m.shutdown()


# ── Heartbeat: "the module is alive" vs "the machine is idle" ────────────────

class TestHeartbeat:
    """Without this, a stopped module and a quiet machine are
    indistinguishable — both simply stop writing. The heartbeat separates
    'nobody is watching this instrument' from 'nothing has run on it'."""

    def test_upsert_shape(self):
        from lem_station_module import build_heartbeat_upsert
        sql, args = build_heartbeat_upsert(
            Machine(uid="m1", title="Multitek S", source_type="serial",
                    com_port="COM4"), NOW)
        assert "lem_machine_heartbeat" in sql
        assert "ON CONFLICT" in sql.upper()
        assert args[0] == "m1"
        assert args[1] == "2026-07-27T12:00:00"
        assert "serial" in args[2]          # what it is watching
        assert "COM4" in args[2]

    def test_source_note_describes_a_csv_watch(self):
        from lem_station_module import build_heartbeat_upsert
        _, args = build_heartbeat_upsert(
            Machine(uid="m1", source_type="single_csv",
                    csv_path="C:/out.csv"), NOW)
        assert "single_csv" in args[2] and "out.csv" in args[2]

    def test_module_beats_on_a_poll(self, qapp, tmp_path, monkeypatch):
        sqls = []
        monkeypatch.setattr(mod, "labcore_write", lambda *a, **k: {"ok": True},
                            raising=False)
        monkeypatch.setattr(mod, "labcore_sql",
                            lambda sql, args=None, source="x":
                            sqls.append(sql) or {"ok": True}, raising=False)
        monkeypatch.setattr(mod, "labcore_read_sql",
                            lambda *a, **k: {"error": "no table"}, raising=False)
        monkeypatch.setattr(mod, "labcore_is_running", lambda: True,
                            raising=False)
        m = make_module()
        m.set_machine(sample_machine(tmp_path))     # no file: nothing to parse
        m.process_now(now=NOW)
        assert any("lem_machine_heartbeat" in s for s in sqls), \
            "an idle instrument must still prove its module is running"
        m.shutdown()

    def test_beat_is_rate_limited(self, qapp, tmp_path, monkeypatch):
        from datetime import timedelta
        sqls = []
        monkeypatch.setattr(mod, "labcore_write", lambda *a, **k: {"ok": True},
                            raising=False)
        monkeypatch.setattr(mod, "labcore_sql",
                            lambda sql, args=None, source="x":
                            sqls.append(sql) or {"ok": True}, raising=False)
        monkeypatch.setattr(mod, "labcore_read_sql",
                            lambda *a, **k: {"error": "no table"}, raising=False)
        monkeypatch.setattr(mod, "labcore_is_running", lambda: True,
                            raising=False)
        m = make_module()
        m.set_machine(sample_machine(tmp_path))
        m.process_now(now=NOW)
        beats = lambda: len([s for s in sqls if "INSERT INTO lem_machine_heartbeat" in s])
        first = beats()
        m.process_now(now=NOW + timedelta(seconds=30))     # too soon
        assert beats() == first
        m.process_now(now=NOW + timedelta(minutes=6))      # past the window
        assert beats() == first + 1
        m.shutdown()


class TestCentralMaintenance:
    """PM/CAL schedules are set from the master view; the module pulls them
    so the pills reflect what the lab manager actually scheduled."""

    def test_parse_maintenance_rows(self):
        from lem_station_module import parse_maint_rows
        rows = [{"uid": "t1", "name": "Annual cal", "kind": "calibration",
                 "interval_days": 365, "last_done": "2026-01-15", "note": ""},
                {"uid": "t2", "name": "Monthly PM", "kind": "pm",
                 "interval_days": 30, "last_done": "", "note": "x"}]
        tasks = parse_maint_rows(rows)
        assert [t.name for t in tasks] == ["Annual cal", "Monthly PM"]
        assert tasks[0].kind == "calibration"
        assert tasks[0].interval_days == 365

    def test_bad_rows_are_skipped(self):
        from lem_station_module import parse_maint_rows
        assert parse_maint_rows([{"name": ""}, {"uid": "t", "name": "OK",
                                                "interval_days": "junk"}]) == \
            [MaintTask(uid="t", name="OK", kind="pm", interval_days=30,
                       last_done="", note="")]

    def test_module_pulls_schedules_and_pills_follow(self, qapp, tmp_path,
                                                     monkeypatch):
        def read_sql(sql, args=None):
            if "lem_maintenance" in sql:
                return {"ok": True, "rows": [
                    {"uid": "c1", "name": "Annual cal", "kind": "calibration",
                     "interval_days": 365, "last_done": "2020-01-01",
                     "note": ""}]}
            return {"error": "no such table"}

        monkeypatch.setattr(mod, "labcore_write", lambda *a, **k: {"ok": True},
                            raising=False)
        monkeypatch.setattr(mod, "labcore_sql", lambda *a, **k: {"ok": True},
                            raising=False)
        monkeypatch.setattr(mod, "labcore_read_sql", read_sql, raising=False)
        monkeypatch.setattr(mod, "labcore_is_running", lambda: True,
                            raising=False)
        m = make_module()
        (tmp_path / "in.csv").write_text("QC1,91.2\n")
        machine = sample_machine(tmp_path)
        m.set_machine(machine)
        m.process_now(now=NOW)
        assert [t.name for t in machine.maintenance] == ["Annual cal"]
        assert m.evaluation().sub_statuses["calibration"] == STATUS_RED
        assert m.evaluation().status == STATUS_RED       # overdue cal wins
        m.shutdown()
