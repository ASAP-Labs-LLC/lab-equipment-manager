"""Shared setup for the harness's own tests.

The environment redirect runs at import, before anything LEM is imported —
the same order gate.py uses — so these tests cannot write into the real
profile either. The target loaded in-process is THIS worktree's code (v4);
the v3.9 reproduction is tested end to end through gate.py in a subprocess,
because one process cannot import two versions of the module.
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True

from gharness import env  # noqa: E402

ROOT = env.activate(os.environ.get("LEM_GATE_TMP"), prefix="lem-gate-tests-")

import pytest  # noqa: E402

PYTHON = sys.executable


@pytest.fixture(scope="session")
def loaded():
    """(lemharness, run_faults, run_web, module, GateGateway) for the worktree."""
    from gharness import netguard, target
    netguard.install()
    lh, rf, rw = target.load(target.WORKTREE)
    netguard.reassert()
    import lem_station_module as mod
    from gharness.hgateway import make_gateway_class
    return lh, rf, rw, mod, make_gateway_class(lh)
