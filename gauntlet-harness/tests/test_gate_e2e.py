"""gate.py end to end, judged by its exit status.

The bar for the gate itself: on v3.9.0 it reproduces phase 1 exactly (no
drift), fails with exit 1 on exactly the 19 baseline scenarios v4 must fix
plus the new ones, touches nothing outside its temp root, never reaches the
network, and finishes well inside 90 s. And the self-test: with mutation T0
(one stored row deleted) every scenario that tallies stored rows goes red — a
tally that stayed green would have a term that cannot move.
"""
import json
import os
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(HERE, "gate.py")
BASELINE_19 = set("K1 K2 K3 K4 K5 K6 K7 K8 K9 N3 N4 F3 F4 F5 R2 R3 R4 R5 R6".split())


def run_gate(tmp_path, *args):
    out = tmp_path / "out.json"
    t0 = time.time()
    p = subprocess.run([sys.executable, GATE, "--tmp", str(tmp_path), "--quiet",
                        "--json", str(out)] + list(args),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    took = time.time() - t0
    assert out.exists(), p.stdout.decode(errors="replace")[-2000:]
    return p.returncode, json.loads(out.read_text()), took


@pytest.fixture(scope="module")
def v39(tmp_path_factory):
    return run_gate(tmp_path_factory.mktemp("v39"), "--target", "v3.9")


def test_v39_exits_1_and_reproduces_phase_one(v39):
    rc, out, took = v39
    assert out["drifted"] == [] and out["harness_errors"] == []
    assert rc == 1
    assert took < 90


def test_v39_fails_exactly_the_19_baseline_scenarios_v4_fixes(v39):
    rc, out, _ = v39
    baseline_ids = [s for s in out["scenarios"]][:29]
    failed = set(out["v4_failed"])
    assert {s for s in baseline_ids if s in failed} == BASELINE_19


def test_v39_headline_numbers(v39):
    m = {s: r.get("measured") or {} for s, r in v39[1]["scenarios"].items()}
    assert m["K1"]["log_dup"] == 12 and m["K2"]["log_dup"] == 203
    assert m["K6"]["log_dup"] == 30
    assert (m["K7"]["log_lost"], m["K8"]["log_lost"]) == (3, 3)
    assert m["F4"]["res_lost"] == 500 and m["F5"]["cell_dup_sends"] == 10100
    assert (m["R2"]["log_lost"], m["R2"]["log_dup"]) == (12, 12)
    assert m["W1"]["after_one_watchdog"]["extra_ops_vs_healthy"] == 426.0


def test_v39_wrote_nothing_outside_tmp_and_made_no_network_call(v39):
    out = v39[1]
    assert out["writes_outside_tmp"] == {}
    assert out["violations"] == [] and out["network_attempts"] == 0


def test_mutation_t0_turns_every_tallied_scenario_red(tmp_path, v39):
    rc, out, _ = run_gate(tmp_path, "--target", "v3.9", "--mutation", "T0")
    tallied = {s for s, r in v39[1]["scenarios"].items()
               if "lost" in (r.get("measured") or {})}
    assert len(tallied) == 48           # 47 of §9, plus the harness's A1j
    assert set(out["drifted"]) == tallied
    assert rc == 2          # drift: the harness no longer reproduces today


def test_a_v4_only_scenario_never_passes_by_default(v39):
    for sid in ("T4", "U1", "M1", "K1b"):
        r = v39[1]["scenarios"][sid]
        assert r["status"] != "ran" and r["v4_fail"], sid
