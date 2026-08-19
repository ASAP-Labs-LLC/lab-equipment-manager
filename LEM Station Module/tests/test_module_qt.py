"""Smoke tests for the LabStation module class (offscreen Qt), v2 model.

One module = one machine. Parsing is capture-and-map: the module waits for
a device print, holds the first one as the template, then parses subsequent
prints via selector mappings onto LabCore test methods. QC specs come from
LabCore — machine.tests is only the fetched/cached spec list.
"""
import inspect
from datetime import datetime

import pytest
from PySide6 import QtWidgets

import lem_station_module as mod

NOW = datetime(2026, 7, 27, 12, 0, 0)


class FakeConnectionManager:
    def __init__(self):
        self.emitted = []

    def emit(self, module_id, output, payload):
        self.emitted.append((module_id, output, payload))
        return 0


class FakeContext:
    def __init__(self):
        self.connection_manager = FakeConnectionManager()
        self.results = []
        self.modules = {}
        self.default_database_path = None

    def add_result(self, lab_id, test, result, source):
        self.results.append((lab_id, test, result, source))


class FakeBaseModule(QtWidgets.QWidget):
    """Stand-in for LabStation's injected BaseModule."""

    def __init__(self, context):
        super().__init__()
        self.context = context
        self.module_id = "test-module-id"
        self.custom_title = None

    def dialog_parent(self):
        return self


def find_module_class():
    classes = [c for _, c in inspect.getmembers(mod, inspect.isclass)
               if getattr(c, "module_type", "")]
    assert len(classes) == 1, "file must contain exactly ONE class with module_type"
    return classes[0]


def make_module(context=None):
    cls = find_module_class()
    patched = type("PatchedLEMStation", (cls, FakeBaseModule), {})
    return patched(context or FakeContext())


def sample_machine(tmp_path, **overrides):
    base = dict(
        uid="m1", title="Eraspec", source_type="single_csv",
        csv_path=str(tmp_path / "in.csv"), delimiter=",",
        lab_id=mod.Selector(mode="cell", index=0),
        mappings=[mod.MethodMapping(
            methods=["RON"], selector=mod.Selector(mode="cell", index=1),
            qc_sample_id="QC1")],
        tests=[mod.TestSpec(name="RON", value_col="RON", expected=91.0,
                            std_dev=0.5, k=2.0, sample_id="QC1")],
    )
    base.update(overrides)
    return mod.Machine(**base)


class TestModuleClass:
    def test_registry_identity(self):
        cls = find_module_class()
        assert cls.module_type == "LEMStation"
        assert cls.module_title
        assert "row_parsed" in cls.outputs
        assert "status_changed" in cls.outputs

    def test_instantiates_unconfigured(self, qapp):
        m = make_module()
        assert m.layout() is not None
        assert m.machine() is None
        assert "NOT CONFIGURED" in m.card().title_text().upper()
        m.shutdown()

    def test_set_machine_updates_card_title(self, qapp, tmp_path):
        m = make_module()
        m.set_machine(sample_machine(tmp_path))
        assert m.card().title_text() == "ERASPEC"
        m.shutdown()

    def test_serialize_restore_round_trip(self, qapp, tmp_path):
        """Saved state is the BINDING only — the config comes from LabCore.

        With no labcore_* helpers injected there is nothing to pull from, so the
        module remembers which machine it is and waits rather than inventing a
        configuration. Pulling a real config back is covered in
        test_config_binding.py.
        """
        import json
        m = make_module()
        m.set_machine(sample_machine(tmp_path))
        state = m.serialize_state()
        json.dumps(state)  # must be JSON-serializable
        assert state["machine_uid"] == "m1"
        assert "machine" not in state          # no config in the canvas file
        m.shutdown()

        m2 = make_module()
        m2.restore_state(state)
        assert m2.machine() is None            # nothing to pull from
        assert m2._pending_uid == "m1"         # but the binding is remembered
        m2.shutdown()

    def test_restore_state_tolerates_empty_dict(self, qapp):
        m = make_module()
        m.restore_state({})
        assert m.machine() is None
        m.shutdown()

    def test_process_now_parses_print_and_evaluates(self, qapp, tmp_path):
        ctx = FakeContext()
        m = make_module(ctx)
        (tmp_path / "in.csv").write_text("QC1,91.2\n")
        m.set_machine(sample_machine(tmp_path))

        m.process_now(now=NOW)

        assert m.evaluation().status == mod.STATUS_GREEN
        assert m.machine().last_position > 0
        assert ("QC1", "RON", "91.2", "LEM Station") in ctx.results
        outputs = [name for _, name, _ in ctx.connection_manager.emitted]
        assert "row_parsed" in outputs
        assert "status_changed" in outputs
        m.shutdown()

    def test_each_line_is_one_print(self, qapp, tmp_path):
        ctx = FakeContext()
        m = make_module(ctx)
        (tmp_path / "in.csv").write_text("QC1,91.2\n26-001,87.6\n")
        m.set_machine(sample_machine(tmp_path))
        m.process_now(now=NOW)
        assert ("QC1", "RON", "91.2", "LEM Station") in ctx.results
        assert ("26-001", "RON", "87.6", "LEM Station") in ctx.results
        m.shutdown()

    def test_first_print_is_held_as_template_when_unmapped(self, qapp, tmp_path):
        ctx = FakeContext()
        m = make_module(ctx)
        (tmp_path / "in.csv").write_text("QC1,91.2,90.1\n")
        machine = sample_machine(tmp_path, mappings=[], tests=[])
        m.set_machine(machine)

        m.process_now(now=NOW)

        assert machine.template == "QC1,91.2,90.1"
        assert ctx.results == []  # nothing parsed, nothing stored
        assert "configure" in m.card().subtitle_text().lower()
        m.shutdown()

    def test_process_now_missing_file_is_unknown(self, qapp, tmp_path):
        m = make_module()
        m.set_machine(sample_machine(tmp_path))  # in.csv never created
        m.process_now(now=NOW)
        assert m.evaluation().status == mod.STATUS_UNKNOWN
        m.shutdown()

    def test_process_now_without_machine_is_a_noop(self, qapp):
        m = make_module()
        m.process_now(now=NOW)  # must not raise
        assert m.evaluation() is None
        m.shutdown()

    def test_multi_csv_treats_each_new_file_as_a_print(self, qapp, tmp_path):
        ctx = FakeContext()
        m = make_module(ctx)
        folder = tmp_path / "prints"
        folder.mkdir()
        machine = sample_machine(tmp_path, source_type="multi_csv",
                                 csv_path=str(folder))
        m.set_machine(machine)
        (folder / "a.csv").write_text("QC1,91.2\n")
        m.process_now(now=NOW)
        assert ("QC1", "RON", "91.2", "LEM Station") in ctx.results
        # already-seen files are not re-processed
        count = len(ctx.results)
        m.process_now(now=NOW)
        assert len(ctx.results) == count
        m.shutdown()

    def test_shutdown_stops_polling(self, qapp):
        m = make_module()
        m.on_finish_loading()
        m.shutdown()
        assert not m.is_polling()


class TestMachineCard:
    def test_one_qc_section_per_labcore_spec(self, qapp, tmp_path):
        machine = sample_machine(
            tmp_path,
            mappings=[
                mod.MethodMapping(methods=["RON"],
                                  selector=mod.Selector(mode="cell", index=1)),
                mod.MethodMapping(methods=["MON"],
                                  selector=mod.Selector(mode="cell", index=2)),
            ],
            tests=[
                mod.TestSpec(name="RON", value_col="RON", expected=91.0,
                             std_dev=0.5, k=2.0, sample_id="QC1"),
                mod.TestSpec(name="MON", value_col="MON", expected=90.0,
                             std_dev=0.6, k=2.0, sample_id="QC1"),
            ])
        m = make_module()
        (tmp_path / "in.csv").write_text("QC1,91.2,90.1\n")
        m.set_machine(machine)
        m.process_now(now=NOW)
        rows = m.card().qc_rows()
        assert [r.test_name() for r in rows] == ["RON", "MON"]
        assert "91.2" in rows[0].value_text()
        assert "90.1" in rows[1].value_text()
        m.shutdown()

    def test_card_shows_overall_status_after_processing(self, qapp, tmp_path):
        m = make_module()
        (tmp_path / "in.csv").write_text("QC1,91.2\n")
        m.set_machine(sample_machine(tmp_path))
        m.process_now(now=NOW)
        assert mod.STATUS_GREEN in m.card().status_text()
        m.shutdown()

    def test_settings_gear_opens_parser_settings(self, qapp, tmp_path,
                                                 monkeypatch):
        opened = []

        class FakeDialog:
            def __init__(self, machine, parent, **kwargs):
                opened.append(machine)

            def exec(self):
                return False

        monkeypatch.setattr(mod, "_MachineDialog", FakeDialog)
        m = make_module()
        machine = sample_machine(tmp_path)
        m.set_machine(machine)
        m.card().settings_button.click()
        assert opened == [machine]
        m.shutdown()

    def test_settings_gear_on_unconfigured_module_asks_which_machine_first(
            self, qapp, monkeypatch):
        """Configs live in LabCore, so an unbound module asks *which
        instrument am I* before it asks how to parse one."""
        opened = []

        class FakeDialog:
            def __init__(self, machine, parent, **kwargs):
                opened.append(machine)

            def exec(self):
                return False

        class FakePicker:
            def __init__(self, choices, parent=None):
                self.outcome = ("new", "", "Fresh Bench")

            def exec(self):
                return True

        monkeypatch.setattr(mod, "_MachineDialog", FakeDialog)
        monkeypatch.setattr(mod, "_MachinePickerDialog", FakePicker)
        m = make_module()
        m.card().settings_button.click()
        assert len(opened) == 1
        assert isinstance(opened[0], mod.Machine)
        assert opened[0].title == "Fresh Bench"
        assert opened[0].uid                      # registered, not blank
        m.shutdown()

    def test_cancelling_the_picker_opens_nothing(self, qapp, monkeypatch):
        opened = []

        class FakeDialog:
            def __init__(self, machine, parent, **kwargs):
                opened.append(machine)

            def exec(self):
                return False

        class CancelledPicker:
            def __init__(self, choices, parent=None):
                self.outcome = None

            def exec(self):
                return False

        monkeypatch.setattr(mod, "_MachineDialog", FakeDialog)
        monkeypatch.setattr(mod, "_MachinePickerDialog", CancelledPicker)
        m = make_module()
        m.card().settings_button.click()
        assert opened == []
        assert m.machine() is None
        m.shutdown()
