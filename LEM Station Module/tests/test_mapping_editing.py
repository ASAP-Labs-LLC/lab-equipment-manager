"""Mappings are editable after they are made.

Ryan, 2026-08-06: "please allow me to create multiple mappings on the same cell,
edit the clean tools and the mappings of the cell after they are created ...
like raw density to API is different math than raw density to kg/cm so i need
the same cell (raw density) to be mapped multiple times for different use cases.
Also allow me to edit the math, instead of having to clear it and re-write the
equation."

Three things were wrong:

1. **The same cell could not be mapped twice.** `_map_selected` merged a second
   mapping on a cell into the first, so one cell meant one set of clean tools.
   One raw density reading feeding API gravity AND kg/m³ is two different
   conversions of the same number — the merge made that impossible to express.
   Mapping now always makes a NEW mapping; grouping methods on one value is what
   checking several methods in the picker is for.

2. **A mapping's methods were fixed once set.** Nothing could change them, and
   the old merge could only ever add. Now the picker reopens with them checked.

3. **A math or remove tool could only be cleared, never edited.** A typo in
   `round(x * 141.5 / 131.5, 2)` meant retyping the whole expression. Each tool
   on a selector is now editable and removable on its own, in place, so the
   order the ops run in survives the edit.
"""
import pytest
from PySide6 import QtWidgets

import lem_station_module as mod
from lem_station_module import Machine, MethodMapping, Selector

# One cell holding a raw density reading — Ryan's case.
TEMPLATE = "26-00412,0.8654,20.0"
DENSITY_CELL = 1


def dialog(**machine_kw):
    base = dict(uid="m1", title="OptiMPP 1", template=TEMPLATE)
    base.update(machine_kw)
    return mod._MachineDialog(Machine(**base), None)


def mapped(d, methods, cell=DENSITY_CELL, clean=None):
    """Add a mapping the way _map_selected would, without the pickers."""
    d._mappings.append(MethodMapping(
        methods=list(methods),
        selector=Selector(mode="cell", index=cell, clean=list(clean or []))))
    d._refresh_map_table()
    return d._mappings[-1]


def select_mapping(d, index):
    """Highlight a mapping row — row 0 of the table is the Lab ID."""
    d._map_table.setCurrentCell(1 + index, 0)


# ── 1. The same cell, mapped more than once ──────────────────────────────────

class TestTheSameCellMappedTwice:
    def pick(self, d, monkeypatch, methods, cell=DENSITY_CELL):
        d._cells.setCurrentCell(0, cell)
        monkeypatch.setattr(d, "_pick_methods", lambda: list(methods))
        d._map_selected(detect=False)

    def test_a_second_mapping_on_one_cell_is_its_own_mapping(self, qapp,
                                                             monkeypatch):
        """It used to fold into the first, so one cell meant one set of clean
        tools — and raw density cannot become API and kg/m³ at once."""
        d = dialog()
        self.pick(d, monkeypatch, ["API Gravity"])
        self.pick(d, monkeypatch, ["Density kg/m3"])
        assert [m.methods for m in d._mappings] == [["API Gravity"],
                                                    ["Density kg/m3"]]

    def test_each_carries_its_own_math(self, qapp, monkeypatch):
        d = dialog()
        self.pick(d, monkeypatch, ["API Gravity"])
        self.pick(d, monkeypatch, ["Density kg/m3"])
        d._mappings[0].selector.clean = ["math:round(141.5 / x - 131.5, 2)"]
        d._mappings[1].selector.clean = ["math:round(x * 1000, 1)"]
        machine = d._current_config()
        values = mod.parse_print(machine, TEMPLATE).values
        assert values["API Gravity"] == "32.01"
        assert values["Density kg/m3"] == "865.4"

    def test_both_reach_labcore_as_separate_results(self, qapp, monkeypatch):
        d = dialog()
        self.pick(d, monkeypatch, ["API Gravity"])
        self.pick(d, monkeypatch, ["Density kg/m3"])
        d._mappings[0].selector.clean = ["math:round(141.5 / x - 131.5, 2)"]
        d._mappings[1].selector.clean = ["math:round(x * 1000, 1)"]
        row = mod.parse_print(d._current_config(), TEMPLATE).to_row(
            __import__("datetime").datetime(2026, 8, 6, 9, 0))
        assert row["API Gravity"] == "32.01"
        assert row["Density kg/m3"] == "865.4"

    def test_several_methods_at_once_still_share_one_mapping(self, qapp,
                                                             monkeypatch):
        """Grouping did not go away — it is what checking several methods in
        the picker means, rather than a side effect of mapping twice."""
        d = dialog()
        self.pick(d, monkeypatch, ["API Gravity", "Density kg/m3"])
        assert [m.methods for m in d._mappings] == [["API Gravity",
                                                     "Density kg/m3"]]

    def test_the_table_shows_a_row_for_each(self, qapp, monkeypatch):
        d = dialog()
        self.pick(d, monkeypatch, ["API Gravity"])
        self.pick(d, monkeypatch, ["Density kg/m3"])
        assert d._map_table.rowCount() == 3        # Lab ID + two mappings

    def test_a_detection_mapping_is_still_its_own(self, qapp, monkeypatch):
        d = dialog()
        self.pick(d, monkeypatch, ["API Gravity"])
        d._cells.setCurrentCell(0, DENSITY_CELL)
        monkeypatch.setattr(d, "_pick_methods", lambda: ["Density kg/m3"])
        monkeypatch.setattr(QtWidgets.QInputDialog, "getText",
                            staticmethod(lambda *a, **k: (r"Density\s*(\S+)",
                                                          True)))
        d._map_selected(detect=True)
        assert len(d._mappings) == 2


# ── 2. Editing which methods a mapping targets ───────────────────────────────

class TestEditingTheMethods:
    def test_the_methods_can_be_replaced(self, qapp):
        d = dialog()
        mapped(d, ["API Gravity"])
        select_mapping(d, 0)
        d.set_mapping_methods(["Density kg/m3", "Relative Density"])
        assert d._mappings[0].methods == ["Density kg/m3", "Relative Density"]

    def test_one_can_be_dropped_from_a_group(self, qapp):
        """The old merge could only ever add — there was no way back."""
        d = dialog()
        mapped(d, ["API Gravity", "Density kg/m3"])
        select_mapping(d, 0)
        d.set_mapping_methods(["API Gravity"])
        assert d._mappings[0].methods == ["API Gravity"]

    def test_the_clean_tools_and_qc_survive_the_edit(self, qapp):
        d = dialog()
        m = mapped(d, ["API Gravity"], clean=["math:x * 2"])
        m.qc_sample_id, m.csv_header = "AO25", "Density"
        select_mapping(d, 0)
        d.set_mapping_methods(["Density kg/m3"])
        assert d._mappings[0].selector.clean == ["math:x * 2"]
        assert (d._mappings[0].qc_sample_id,
                d._mappings[0].csv_header) == ("AO25", "Density")

    def test_the_table_shows_the_new_methods(self, qapp):
        d = dialog()
        mapped(d, ["API Gravity"])
        select_mapping(d, 0)
        d.set_mapping_methods(["Density kg/m3"])
        assert d._map_table.item(1, 2).text() == "Density kg/m3"

    def test_emptying_the_selection_is_refused(self, qapp):
        """A mapping with no methods extracts a value for nothing. Removing it
        is the Remove button's job, not a silent consequence of unchecking."""
        d = dialog()
        mapped(d, ["API Gravity"])
        select_mapping(d, 0)
        d.set_mapping_methods([])
        assert d._mappings[0].methods == ["API Gravity"]

    def test_with_the_lab_id_row_selected_it_does_nothing(self, qapp):
        d = dialog()
        mapped(d, ["API Gravity"])
        d._map_table.setCurrentCell(0, 0)      # the Lab ID row
        d.set_mapping_methods(["Density kg/m3"])
        assert d._mappings[0].methods == ["API Gravity"]

    def test_the_editor_opens_with_the_current_methods_checked(self, qapp,
                                                               monkeypatch):
        d = dialog()
        mapped(d, ["API Gravity"])
        select_mapping(d, 0)
        d._methods = ["API Gravity", "Density kg/m3"]
        seen = {}

        class Picker:
            def __init__(self, methods, parent, title="", selected=()):
                seen["selected"] = list(selected)

            def exec(self):
                return QtWidgets.QDialog.DialogCode.Rejected

        monkeypatch.setattr(mod, "_MethodPickerDialog", Picker)
        d._edit_methods()
        assert seen["selected"] == ["API Gravity"]


class TestThePickerPreselects:
    def test_checked_methods_come_back_without_a_click(self, qapp):
        p = mod._MethodPickerDialog(["A", "B", "C"], None, selected=["B"])
        assert p.selected_methods() == ["B"]

    def test_a_method_labcore_no_longer_lists_is_kept(self, qapp):
        """LabCore's method names are uncurated, so a rename orphans a mapping.
        Dropping it silently on OK would delete the operator's work."""
        p = mod._MethodPickerDialog(["A", "B"], None, selected=["Old Name"])
        assert p.selected_methods() == ["Old Name"]

    def test_nothing_is_checked_by_default(self, qapp):
        assert mod._MethodPickerDialog(["A", "B"], None).selected_methods() == []


# ── 3. Editing a clean tool in place ─────────────────────────────────────────

class TestEditingACleanTool:
    def test_the_math_can_be_edited_instead_of_retyped(self, qapp):
        d = dialog()
        mapped(d, ["API Gravity"], clean=["math:round(141.5 / x - 131.5, 2)"])
        select_mapping(d, 0)
        d.set_clean_op(0, "round(141.5 / x - 131.5, 1)")
        assert d._mappings[0].selector.clean == \
            ["math:round(141.5 / x - 131.5, 1)"]

    def test_editing_keeps_the_op_where_it_was(self, qapp):
        """`apply_clean` runs them in order, so an edit that moves one changes
        the result."""
        d = dialog()
        mapped(d, ["API Gravity"],
               clean=["strip", "math:x * 2", "keep_number"])
        select_mapping(d, 0)
        d.set_clean_op(1, "x * 3")
        assert d._mappings[0].selector.clean == \
            ["strip", "math:x * 3", "keep_number"]

    def test_a_remove_tool_is_editable_too(self, qapp):
        d = dialog()
        mapped(d, ["API Gravity"], clean=["remove:g/cm3"])
        select_mapping(d, 0)
        d.set_clean_op(0, "kg/m3")
        assert d._mappings[0].selector.clean == ["remove:kg/m3"]

    def test_one_tool_can_be_dropped_without_clearing_the_rest(self, qapp):
        d = dialog()
        mapped(d, ["API Gravity"], clean=["strip", "math:x * 2", "keep_number"])
        select_mapping(d, 0)
        d.drop_clean_op(1)
        assert d._mappings[0].selector.clean == ["strip", "keep_number"]

    def test_an_empty_expression_is_refused(self, qapp):
        """Emptying the box is a cancelled edit, not a request for `math:`."""
        d = dialog()
        mapped(d, ["API Gravity"], clean=["math:x * 2"])
        select_mapping(d, 0)
        d.set_clean_op(0, "   ")
        assert d._mappings[0].selector.clean == ["math:x * 2"]

    def test_a_plain_tool_is_not_editable_as_an_expression(self, qapp):
        """"strip" carries no argument — it is toggled, not typed."""
        d = dialog()
        mapped(d, ["API Gravity"], clean=["strip"])
        select_mapping(d, 0)
        d.set_clean_op(0, "anything")
        assert d._mappings[0].selector.clean == ["strip"]

    def test_out_of_range_indexes_do_nothing(self, qapp):
        d = dialog()
        mapped(d, ["API Gravity"], clean=["math:x * 2"])
        select_mapping(d, 0)
        d.set_clean_op(7, "x * 3")
        d.drop_clean_op(-1)
        assert d._mappings[0].selector.clean == ["math:x * 2"]

    def test_the_lab_ids_own_tools_are_editable_the_same_way(self, qapp):
        """Row 0 flows through the same pipeline as any mapping."""
        d = dialog()
        d._lab_id = Selector(mode="cell", index=0, clean=["remove:L-"])
        d._refresh_map_table()
        d._map_table.setCurrentCell(0, 0)
        d.set_clean_op(0, "LAB-")
        assert d._lab_id.clean == ["remove:LAB-"]

    def test_the_edit_shows_in_the_table(self, qapp):
        d = dialog()
        mapped(d, ["API Gravity"], clean=["math:x * 2"])
        select_mapping(d, 0)
        d.set_clean_op(0, "x * 3")
        assert "x * 3" in d._map_table.item(1, 1).text()


class TestTheCleanToolsMenu:
    def menu_labels(self, d):
        d._rebuild_clean_menu()
        return [a.text() for a in d._clean_menu.actions() if a.text()]

    def test_it_offers_an_edit_for_each_tool_that_has_an_argument(self, qapp):
        d = dialog()
        mapped(d, ["API Gravity"], clean=["math:x * 2", "remove:kg"])
        select_mapping(d, 0)
        labels = self.menu_labels(d)
        assert any("math:x * 2" in l and "Edit" in l for l in labels)
        assert any("remove:kg" in l and "Edit" in l for l in labels)

    def test_and_a_remove_for_each(self, qapp):
        d = dialog()
        mapped(d, ["API Gravity"], clean=["math:x * 2"])
        select_mapping(d, 0)
        assert any("math:x * 2" in l and "Remove" in l
                   for l in self.menu_labels(d))

    def test_the_plain_tools_show_which_are_on(self, qapp):
        d = dialog()
        mapped(d, ["API Gravity"], clean=["strip"])
        select_mapping(d, 0)
        d._rebuild_clean_menu()
        checked = {a.text(): a.isChecked() for a in d._clean_menu.actions()
                   if a.isCheckable()}
        assert checked["strip"] is True
        assert checked["keep_number"] is False

    def test_toggling_from_the_menu_still_works(self, qapp):
        d = dialog()
        mapped(d, ["API Gravity"])
        select_mapping(d, 0)
        d._toggle_clean("strip")
        assert d._mappings[0].selector.clean == ["strip"]
        d._toggle_clean("strip")
        assert d._mappings[0].selector.clean == []

    def test_with_nothing_selected_it_says_so(self, qapp):
        d = dialog()
        mapped(d, ["API Gravity"])
        d._map_table.setCurrentCell(-1, -1)
        assert any("select" in l.lower() for l in self.menu_labels(d))
