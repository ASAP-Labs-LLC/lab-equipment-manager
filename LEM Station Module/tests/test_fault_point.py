"""The fault-point plumbing the v4 gate kills the bench through.

The transfer v4 gate (gauntlet-harness/gate.py, spec §15.3) proves its
crash-safety claims by killing the module at named places — after the journal
append and before the cursor save, between a frame completing and its fsync,
after a batch landed and before `filed` is journaled — and then checking that
nothing was lost or doubled on restart. A kill at a place the module does not
mark is a kill at a place the gate cannot name, so the module marks them
itself, by calling `self._fault_point(name)`.

Three properties make that safe to ship, and each is pinned here:

  * **In production it is a no-op.** It returns None, raises nothing, and
    touches nothing. A crash test hook that could change behaviour on the lab
    floor would be a fault of its own.

  * **There is ONE hook for the harness to replace: the module-level
    `fault_point`.** The method looks it up at call time, so a harness that
    swaps `lem_station_module.fault_point` sees every call — from the bench
    object, and from the serial reader thread, which is not the bench object
    and has no `self._fault_point` of its own to patch.

  * **The names are a closed list, `FAULT_POINTS`.** A typo'd name at a call
    site would be a kill point the gate arms and never reaches; the gate
    reports that as a failure ("kill point never reached"), but the cheaper
    place to catch it is here, before anything runs. The method refuses an
    unknown name under test (`LEM_FAULT_POINTS_STRICT=1`) and ignores it in
    production, where refusing would turn a typo into a lost poll.
"""
import os

import pytest

import lem_station_module as mod


SPEC_POINTS = (
    "before_journal", "after_journal_before_fsync",
    "after_journal_before_cursor", "after_cursor",
    "serial_frame_complete_before_fsync",
    "after_combined_read", "after_batch_landed", "before_filed_journaled",
    "between_upload_chunks",
)


def test_the_named_points_are_exactly_the_spec_list():
    # §15.3 of the transfer spec, in its order. Adding a point is fine — it
    # is a spec change, and it must be made here too, on purpose.
    assert tuple(mod.FAULT_POINTS) == SPEC_POINTS


def test_the_default_hook_is_a_no_op():
    for name in SPEC_POINTS:
        assert mod.fault_point(name) is None


def test_the_method_routes_through_the_module_hook(monkeypatch):
    seen = []
    monkeypatch.setattr(mod, "fault_point", lambda name: seen.append(name))
    bench = mod.LEMStationModule.__new__(mod.LEMStationModule)
    bench._fault_point("after_cursor")
    bench._fault_point("before_journal")
    assert seen == ["after_cursor", "before_journal"]


def test_a_kill_raised_by_the_hook_is_not_swallowed(monkeypatch):
    # The gate kills with a BaseException so no `except Exception` in the
    # module can eat it. The method must not wrap the hook in anything that
    # would.
    class Kill(BaseException):
        pass

    def die(name):
        raise Kill(name)
    monkeypatch.setattr(mod, "fault_point", die)
    bench = mod.LEMStationModule.__new__(mod.LEMStationModule)
    with pytest.raises(Kill):
        bench._fault_point("after_cursor")


def test_an_unknown_name_is_refused_under_strict_mode(monkeypatch):
    monkeypatch.setenv("LEM_FAULT_POINTS_STRICT", "1")
    bench = mod.LEMStationModule.__new__(mod.LEMStationModule)
    with pytest.raises(ValueError):
        bench._fault_point("after_cursr")


def test_an_unknown_name_is_ignored_in_production(monkeypatch):
    monkeypatch.delenv("LEM_FAULT_POINTS_STRICT", raising=False)
    bench = mod.LEMStationModule.__new__(mod.LEMStationModule)
    assert bench._fault_point("after_cursr") is None
