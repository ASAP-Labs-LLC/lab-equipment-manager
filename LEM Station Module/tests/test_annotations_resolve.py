"""Every annotation in the module must resolve against the module's own globals.

This is the guard for a bug that only appears on the lab PC. The module bans
`from __future__ import annotations` (LabStation loads it without registering it
in sys.modules), so on LabStation's Python every annotation is evaluated the
moment its class body runs — a name that was never imported raises NameError
during the import itself, the loader's exec fails, no class carrying
`module_type` is found, and the install dies with "no module_type attribute".

Developer machines running 3.14 hide this completely: PEP 649 defers annotation
evaluation, and dataclasses resolves them in FORWARDREF mode, so an undefined
name quietly becomes a ForwardRef and the whole suite passes. `Dict[str, float]`
on Machine.corrections shipped exactly that way (2026-08-04, correction factors).

`get_type_hints` forces the resolution here, on any Python version.
"""
import inspect
import typing

import lem_station_module as lsm


def _module_level_objects():
    """Classes and functions defined in the module itself (not imported ones)."""
    for name, obj in vars(lsm).items():
        if name.startswith("__"):
            continue
        if not (inspect.isclass(obj) or inspect.isfunction(obj)):
            continue
        if getattr(obj, "__module__", None) != lsm.__name__:
            continue
        yield name, obj


class TestAnnotationsResolve:
    def test_every_annotation_resolves(self):
        unresolved = []
        for name, obj in _module_level_objects():
            try:
                typing.get_type_hints(obj)
            except NameError as exc:
                unresolved.append(f"{name}: {exc}")
        assert not unresolved, (
            "annotations referencing names the module never imported — these "
            "raise NameError on LabStation's Python and break the install:\n  "
            + "\n  ".join(unresolved))

    def test_machine_corrections_is_a_resolved_mapping(self):
        """The specific annotation that broke the install."""
        hints = typing.get_type_hints(lsm.Machine)
        assert typing.get_origin(hints["corrections"]) is dict
