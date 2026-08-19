"""Replicates LabStation's _load_custom_module() VERBATIM (LabStation.pyw
line ~12699) against our module file, so install failures show up here
instead of only on the lab PC. Any deviation from the real loader defeats
the point — keep this in sync with the LabLink repo."""
import builtins as _builtins
import importlib.util
from pathlib import Path

import pytest
from PySide6 import QtWidgets

MODULE_FILE = Path(__file__).resolve().parent.parent / "lem_station_module.py"


class FakeContext:
    def __init__(self):
        self.connection_manager = None
        self.modules = {}


class BaseModule(QtWidgets.QWidget):
    """Minimal stand-in with the same construction contract."""

    def __init__(self, context):
        super().__init__()
        self.context = context
        self.module_id = "loader-test-id"
        self.custom_title = None

    def dialog_parent(self):
        return self


def load_like_labstation(file_path: Path):
    """Mirror of LabStation's _load_custom_module, including builtins swap,
    name injection, candidate scan over dir(), and dict(cls.__dict__)
    re-parenting."""
    spec = importlib.util.spec_from_file_location(file_path.stem, str(file_path))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    mod.BaseModule = BaseModule
    mod.LabStationContext = FakeContext
    mod.labcore_write = lambda *a, **k: {"ok": True}
    mod.labcore_sql = lambda *a, **k: {"ok": True}
    mod.labcore_read_sql = lambda *a, **k: {"error": "no such table"}
    mod.labcore_append_photo = lambda *a, **k: {"ok": True}
    mod.labcore_is_running = lambda: False
    mod.ResultEntry = object
    mod.format_timestamp = lambda: "2026-07-27T12:00:00"
    mod._run_in_thread = lambda fn, cb: cb(fn())

    _original_import = _builtins.__import__

    def _guarded_import(name, *args, **kwargs):
        return _original_import(name, *args, **kwargs)

    mod.__builtins__ = {**_builtins.__dict__, "__import__": _guarded_import}

    spec.loader.exec_module(mod)  # loader catches this; we want the traceback

    candidates = []
    for name in dir(mod):
        obj = getattr(mod, name)
        if (
            isinstance(obj, type)
            and obj is not BaseModule
            and hasattr(obj, "module_type")
            and getattr(obj, "module_type", "") not in ("", "Base")
        ):
            candidates.append(obj)
    if not candidates:
        return None

    cls = candidates[0]
    if not issubclass(cls, BaseModule):
        cls = type(cls.__name__, (cls, BaseModule), dict(cls.__dict__))
        cls.__module__ = mod.__name__
    return cls


class TestLabStationLoader:
    def test_loader_finds_exactly_our_class(self, qapp):
        cls = load_like_labstation(MODULE_FILE)
        assert cls is not None, "loader found no class with module_type"
        assert cls.module_type == "LEMStation"

    def test_loaded_class_instantiates_on_the_canvas(self, qapp):
        cls = load_like_labstation(MODULE_FILE)
        instance = cls(FakeContext())
        assert instance.machine() is None
        state = instance.serialize_state()
        instance.restore_state(state)
        instance.shutdown()
