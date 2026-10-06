"""A crash is not a verdict.

gate.py's exit status IS its answer, and on --target v3.9 the right answer is
1. Python's own answer to an uncaught exception is also 1. So a gate that let
a traceback escape would print "1" for a bad interpreter, a missing package, a
scenario loader that blew up, or a typo in the harness — and anyone reading the
status (CI, a push hook, the next agent) would take it as "v3.9 fails v4, as
expected". It happened: run under the station-module venv, which has no flask,
`gate.py --target v3.9` died in target.load with ModuleNotFoundError and
exited 1, the same status as the correct verdict.

The documented contract is 2 for "the harness is not trustworthy; this run
says nothing about the target". These tests break the gate in each place it
can break — before its own package imports, while loading the target, inside
a scenario run, inside the mutation self-test, and through a SystemExit raised
by the code under test — and require 2 every time, never 0 and never 1.
"""
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(HERE, "gate.py")
MODULE_VENV_PY = "/Users/rynatical/Projects/lab-equipment-manager/venv/bin/python"


def _copy_gate(tmp_path, with_package=True):
    """A private copy of the gate, so a test can break it without touching
    the real one."""
    dst = tmp_path / "gate-copy"
    dst.mkdir()
    shutil.copy(GATE, dst / "gate.py")
    shutil.copy(os.path.join(HERE, "expectations.json"), dst / "expectations.json")
    if with_package:
        shutil.copytree(os.path.join(HERE, "gharness"), dst / "gharness",
                        ignore=shutil.ignore_patterns("__pycache__"))
        # the phase-1 baseline is part of the harness now (committed beside
        # gharness/, the default when LEM_GATE_BASELINE is unset)
        shutil.copytree(os.path.join(HERE, "baseline"), dst / "baseline",
                        ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def _run(gate_dir, tmp_path, *args, python=sys.executable):
    p = subprocess.run([python, str(gate_dir / "gate.py"), "--tmp", str(tmp_path),
                        "--quiet"] + list(args),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    return p.returncode, p.stdout.decode(errors="replace")


def test_a_broken_import_inside_the_harness_exits_2(tmp_path):
    # The scenario loader / target loader cannot even be imported.
    g = _copy_gate(tmp_path)
    (g / "gharness" / "target.py").write_text(
        "raise ImportError('deliberately broken for the exit-status test')\n")
    rc, out = _run(g, tmp_path, "--target", "v3.9")
    assert rc == 2, out
    assert "deliberately broken" in out          # and it says why


def test_a_missing_harness_package_exits_2(tmp_path):
    # The import at the very top of gate.py fails: still 2, not Python's 1.
    g = _copy_gate(tmp_path, with_package=False)
    rc, out = _run(g, tmp_path, "--target", "v3.9")
    assert rc == 2, out
    assert "gharness" in out


def test_a_crash_while_loading_the_target_exits_2(tmp_path):
    # A target tree whose web_app cannot import — the shape of "wrong
    # interpreter, no flask" without depending on which venvs this machine has.
    code = tmp_path / "code"
    for sub in ("LEM Station Module", "LEM Web Server"):
        (code / sub).mkdir(parents=True)
    (code / "LEM Station Module" / "lem_station_module.py").write_text("X = 1\n")
    for name in ("test_module_qt", "labcore_gateway", "snapshot_service", "log_mirror"):
        (code / "LEM Web Server" / (name + ".py")).write_text("X = 1\n")
    (code / "LEM Web Server" / "web_app.py").write_text("import flask_that_is_not_installed\n")
    rc, out = _run(_copy_gate(tmp_path), tmp_path, "--target", "v3.9", "--code", str(code))
    assert rc == 2, out
    assert "flask_that_is_not_installed" in out


@pytest.mark.skipif(not os.path.exists(MODULE_VENV_PY),
                    reason="the station-module venv is not on this machine")
def test_the_station_module_venv_without_flask_exits_2(tmp_path):
    # The exact run the critic made: the wrong interpreter. The real gate,
    # not a copy.
    probe = subprocess.run([MODULE_VENV_PY, "-c", "import flask"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if probe.returncode == 0:
        pytest.skip("that venv has flask now; the scenario does not arise")
    p = subprocess.run([MODULE_VENV_PY, GATE, "--target", "v3.9", "--tmp", str(tmp_path),
                        "--quiet"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=120)
    out = p.stdout.decode(errors="replace")
    assert p.returncode == 2, out
    assert "flask" in out


def _import_gate():
    sys.path.insert(0, HERE)
    import gate
    return gate


@pytest.mark.parametrize("exc", [RuntimeError("boom"), KeyError("x"),
                                 SystemExit(1), SystemExit(0), KeyboardInterrupt()])
def test_any_exception_out_of_run_is_2(monkeypatch, capsys, exc):
    # SystemExit(0) included: a target that calls sys.exit() mid-run must not
    # turn into a green gate. KeyboardInterrupt: an interrupted run judged
    # nothing.
    gate = _import_gate()

    def run(args):
        raise exc
    monkeypatch.setattr(gate, "run", run)
    assert gate.main(["--target", "v3.9", "--skip-drift"]) == 2
    assert "HARNESS" in capsys.readouterr().out


def test_any_exception_out_of_the_self_test_is_2(monkeypatch, capsys):
    gate = _import_gate()

    def run_mutations(args):
        raise ValueError("self-test crashed")
    monkeypatch.setattr(gate, "run_mutations", run_mutations)
    assert gate.main(["--mutations"]) == 2
    assert "self-test crashed" in capsys.readouterr().out


def test_a_crash_while_reporting_is_2(monkeypatch, capsys):
    # The verdict was computed, but printing it failed: the caller did not
    # get it, so it is not a verdict.
    gate = _import_gate()
    monkeypatch.setattr(gate, "run", lambda args: {
        "target": "v3.9", "code_root": "x", "tmp_root": str(os.getcwd()),
        "mutation": None, "seconds": 0, "scenarios": {}, "drifted": [],
        "v4_failed": ["K1"], "harness_errors": [], "writes_outside_tmp": {},
        "network_attempts": 0, "violations": [], "economy_drift": []})

    def report(out, quiet=False):
        raise OSError("stdout went away")
    monkeypatch.setattr(gate, "report", report)
    monkeypatch.setattr(gate, "_write_results", lambda out, args: None)
    assert gate.main(["--target", "v3.9", "--skip-drift"]) == 2


# ── a scenario that crashes ──────────────────────────────────────────────────
#
# run() catches each scenario's exception so one bad scenario does not hide the
# other 78. That catch must not turn a HARNESS bug into a target verdict: a
# typo in scenarios.py on --target v4 would otherwise read "v4 fails" (exit 1),
# and on a v4-only scenario with no v3.9 row it would read the same on v3.9.
# Where the exception came from decides: raised from the target's own code it
# is the target failing (a FAIL, exit 1); raised from the gate's or the
# baseline harness's code it is the harness failing (exit 2).

def test_a_scenario_that_crashes_in_harness_code_exits_2(tmp_path):
    g = _copy_gate(tmp_path)
    p = g / "gharness" / "scenarios.py"
    src = p.read_text()
    marker = "    def k1b():\n        c = W()\n"
    assert marker in src
    p.write_text(src.replace(marker, "    def k1b():\n        undefined_name_in_harness()\n"))
    # --code: the copy is not inside a git checkout, so it cannot `git
    # archive` v3.9.0 itself; any real tree will do for a harness crash.
    rc, out = _run(g, tmp_path, "--target", "v3.9", "--only", "K1b",
                   "--code", os.path.dirname(HERE))
    assert rc == 2, out
    assert "K1b" in out and "undefined_name_in_harness" in out


def _tb_from(path, code):
    """A real traceback whose innermost frame is in `path`."""
    ns = {}
    exec(compile(code, path, "exec"), ns)
    try:
        ns["f"]()
    except Exception:
        return sys.exc_info()[2]


def test_crash_origin_is_the_innermost_frame_that_is_ours_or_the_targets(tmp_path):
    gate = _import_gate()
    code_root = str(tmp_path / "code")
    in_target = os.path.join(code_root, "LEM Station Module", "lem_station_module.py")
    in_harness = os.path.join(HERE, "gharness", "scenarios.py")
    elsewhere = "/usr/lib/python3/json/decoder.py"
    body = "def f():\n    raise ValueError('x')\n"
    assert gate.crash_origin(_tb_from(in_target, body), code_root) == "target"
    assert gate.crash_origin(_tb_from(in_harness, body), code_root) == "harness"
    # Library frames are skipped; with nothing of ours or theirs, it is ours.
    assert gate.crash_origin(_tb_from(elsewhere, body), code_root) == "harness"
    # v4: the target is the worktree, which CONTAINS gauntlet-harness/. A
    # harness frame is still the harness's.
    worktree = os.path.dirname(HERE)
    assert gate.crash_origin(_tb_from(in_harness, body), worktree) == "harness"
