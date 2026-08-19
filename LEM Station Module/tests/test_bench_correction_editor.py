"""Saving one correction on the bench must not disturb the others.

`Machine.corrections` is the authority: it covers EVERY method the bench
reports, and QC is assignment-only, so most of those methods have no TestSpec
at all — they are the customer results. `TestSpec.correction` is a display copy
of the subset that happens to have QC assigned.

So the map the editor saves has to be rebuilt from the map, not from the specs.
Rebuilt from the specs, every correction on a method with no QC assigned is
dropped the moment the operator saves an unrelated one — and until a poll
manages to re-read LabCore, those measurements are reported raw.
"""
from datetime import datetime

import pytest

import lem_station_module as mod

from test_module_qt import make_module

NOW = datetime(2026, 8, 5, 10, 0, 0)


def pac_flash_2():
    """A real shape from the floor: Flash Point is corrected but has no QC
    assigned; Density is the one control with a spec."""
    machine = mod.Machine(
        uid="pac-flash-2", title="PAC Flash 2",
        mappings=[mod.MethodMapping(methods=["Flash Point", "Density"],
                                    selector=mod.Selector())],
        tests=[mod.TestSpec(name="Density", value_col="Density",
                            expected=0.84, std_dev=0.01, k=2.0,
                            sample_id="QC1", units="g/cm3")])
    mod.apply_corrections(machine, {"Flash Point": -3.0, "Density": 0.1})
    assert machine.corrections == {"Flash Point": -3.0, "Density": 0.1}
    return machine


class FakeDialog:
    """Stands in for _CorrectionsDialog: accepted, with what changed."""
    changes_to_return: dict = {}

    def __init__(self, machine, parent):
        self.machine = machine

    def exec(self):
        return True

    def changes(self):
        return dict(self.changes_to_return)


@pytest.fixture
def editor(monkeypatch, qapp):
    """A module whose corrections dialog is stubbed and whose LabCore writes
    are recorded rather than sent."""
    module = make_module()
    written = []
    monkeypatch.setitem(mod.__dict__, "labcore_sql",
                        lambda sql, args=None: written.append((sql, args)))
    monkeypatch.setattr(mod, "_CorrectionsDialog", FakeDialog)
    monkeypatch.setattr(mod, "_in_thread", lambda fn, cb: cb(fn()))
    return module, written


class TestSavingOneCorrectionKeepsTheRest:
    def test_a_method_with_no_qc_keeps_its_correction(self, editor):
        module, _ = editor
        machine = pac_flash_2()
        FakeDialog.changes_to_return = {"Density": 0.2}

        module._open_corrections(machine)

        assert machine.corrections["Flash Point"] == -3.0, (
            "the offset on a method with no QC spec was dropped by saving "
            "an unrelated one")
        assert machine.corrections["Density"] == 0.2

    def test_the_next_print_is_still_corrected(self, editor):
        """The consequence on the bench: a parse straight after the save."""
        module, _ = editor
        machine = pac_flash_2()
        FakeDialog.changes_to_return = {"Density": 0.2}

        module._open_corrections(machine)
        rows = mod.apply_row_corrections(
            [{mod.LAB_ID_KEY: "L-1", "Flash Point": 65.5, "Density": 0.84}],
            machine.corrections)

        assert rows[0]["Flash Point"] == 62.5
        assert rows[0][mod.RAW_KEY]["Flash Point"] == 65.5

    def test_clearing_a_correction_stops_it_correcting(self, editor):
        module, _ = editor
        machine = pac_flash_2()
        FakeDialog.changes_to_return = {"Flash Point": 0.0}

        module._open_corrections(machine)
        rows = mod.apply_row_corrections(
            [{mod.LAB_ID_KEY: "L-1", "Flash Point": 65.5}], machine.corrections)

        assert rows[0]["Flash Point"] == 65.5
        assert mod.RAW_KEY not in rows[0]

    def test_the_display_copy_follows_the_map(self, editor):
        module, _ = editor
        machine = pac_flash_2()
        FakeDialog.changes_to_return = {"Density": 0.2}

        module._open_corrections(machine)

        assert machine.tests[0].correction == 0.2

    def test_only_what_changed_is_written(self, editor):
        module, written = editor
        machine = pac_flash_2()
        FakeDialog.changes_to_return = {"Density": 0.2}

        module._open_corrections(machine)

        statements = [sql for sql, _ in written]
        assert sum("lem_correction_factors" in s and "INSERT" in s
                   for s in statements) == 1
        assert not any("Flash Point" in str(args) for _, args in written)


class TestABusyQueueNeverTurnsCorrectionsOff:
    """`refresh_corrections`: a stale correction is a lesser problem than a
    wrong result, so nothing short of a good read may change the map."""

    def test_a_raising_reader_keeps_what_it_had(self):
        machine = pac_flash_2()

        def boom(*a, **k):
            raise RuntimeError("LabCore queue full")

        assert mod.refresh_corrections(machine, boom) is False
        assert machine.corrections == {"Flash Point": -3.0, "Density": 0.1}

    def test_an_error_answer_keeps_what_it_had(self):
        machine = pac_flash_2()
        assert mod.refresh_corrections(
            machine, lambda *a, **k: {"error": "no such table"}) is False
        assert machine.corrections == {"Flash Point": -3.0, "Density": 0.1}

    def test_an_empty_answer_clears_nothing_silently(self):
        """No rows is a real answer — every correction was deleted — but an
        answer with no `rows` key at all is not, and must be ignored."""
        machine = pac_flash_2()
        assert mod.refresh_corrections(machine, lambda *a, **k: None) is False
        assert machine.corrections == {"Flash Point": -3.0, "Density": 0.1}
