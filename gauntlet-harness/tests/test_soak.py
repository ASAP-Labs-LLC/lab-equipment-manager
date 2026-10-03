"""The soak: a day of a 17-bench fleet, and the proof it was a hard day.

A soak that reports "0 lost" proves something only if (a) the faults it
claims actually happened — a kill that never fired, a restore that never
rolled a cursor back, is a quiet day reported as a hard one — and (b) it can
report anything else. So: the short day here must meet the bar AND show
every kind of fault firing, with each kill one restart; the verdict must
name what fails; a day whose faults did not happen must be a harness
failure (exit 2), never a pass; and the same day on a copy of the module
with its results guard off, its poll journaling off, or its cursor and keys
ignored must go red (exit 1) for the reason that mutation causes.

Each run here is a 2-simulated-hour day (the 24 h schedule, scaled): about
6 s. The full day is `soak.py` with no arguments.
"""
import json
import os
import subprocess
import sys

import pytest

from gharness import soak as S
from gharness import mutations as MU
from gharness.target import WORKTREE

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_soak(tmp_path, *extra):
    out = tmp_path / "soak.json"
    p = subprocess.run([sys.executable, os.path.join(HERE, "soak.py"),
                        "--hours", "2", "--tmp", str(tmp_path),
                        "--json", str(out)] + list(extra),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    text = p.stdout.decode(errors="replace")
    res = json.loads(out.read_text()) if out.exists() else None
    return p.returncode, res, text


@pytest.fixture(scope="module")
def short_day(tmp_path_factory):
    return run_soak(tmp_path_factory.mktemp("soak"))


def test_a_short_day_meets_the_bar(short_day):
    rc, res, text = short_day
    assert rc == 0, text[-2000:]
    t = res["totals"]
    assert (t["lost"], t["unlabelled_dup"], t["res_lost"], t["res_wrong"]) \
        == (0, 0, 0, 0)
    assert res["analyst_overwritten"] == 0
    assert res["benches"] == 17 and res["benches_drained"] == 17
    assert res["network_attempts"] == 0 and res["harness_errors"] == []


def test_every_fault_fired_and_each_kill_was_one_restart(short_day):
    rc, res, text = short_day
    f = res["faults"]
    assert res["kills_fired"] >= 2
    assert res["kills_fired"] == res["kills_restarted"] == res["totals"]["restarts"]
    assert any(f.get("kill:" + p) for p in S.KILL_POINTS)
    assert any(f.get("kill:labcore_" + a) for a in S.LABCORE_KILLS)
    assert f["labcore_refused"] > 0 and f["server_reboot"] == 2
    assert res["restore"]["acked_rolled_back"] > 0
    assert int(res["sync_answers"]["409"]) > 0
    assert f["analyst_held_edits"] > 0 and res["conflicts"] >= f["analyst_held_edits"]
    sources = sorted({b["source"] for b in res["per_bench"].values()})
    assert sources == ["multi_csv", "serial", "single_csv"]


def _base_out(**over):
    out = {"totals": {"lost": 0, "unlabelled_dup": 0, "res_lost": 0,
                      "res_wrong": 0, "restarts": 2},
           "analyst_overwritten": 0, "benches": 17, "benches_drained": 17,
           "not_drained": [], "lem_requests_on_poll_thread": 0,
           "faults": {"kill:after_cursor": 1, "kill:labcore_kill_after": 1,
                      "both_dark": 2, "storm": 2, "labcore_refused": 9,
                      "server_reboot": 2, "restore": 1, "flap": 4,
                      "analyst_edits": 3, "analyst_held_edits": 3,
                      "backups": 2},
           "kills_fired": 2, "kills_restarted": 2,
           "restore": {"acked_rolled_back": 3}, "sync_answers": {"409": 3},
           "roads_used": ["A", "B"]}
    out.update(over)
    return out


def test_the_verdict_names_each_failure():
    assert S.verdict(_base_out()) == []
    bad = S.verdict(_base_out(
        totals={"lost": 1, "unlabelled_dup": 2, "res_lost": 0, "res_wrong": 0,
                "restarts": 2},
        analyst_overwritten=1, benches_drained=16, not_drained=["soak-04"]))
    assert bad == ["lost 1", "unlabelled_dup 2",
                   "1 analyst value(s) overwritten (no A5 race was injected)",
                   "not drained: soak-04"]


def test_a_quiet_day_is_a_harness_failure_not_a_pass():
    assert S.proof_of_faults(_base_out(), 2) == []
    quiet = _base_out(faults={"backups": 2}, kills_fired=0, kills_restarted=0,
                      restore={"acked_rolled_back": 0}, sync_answers={},
                      totals={"lost": 0, "unlabelled_dup": 0, "res_lost": 0,
                              "res_wrong": 0, "restarts": 0})
    missing = S.proof_of_faults(quiet, 2)
    assert "no fault-point kill fired" in missing
    assert "no restore" in missing and "no storm" in missing
    assert "no 409 cursor answer after the restore" in missing
    lying = _base_out(kills_restarted=1)
    assert any("each kill is one restart" in m
               for m in S.proof_of_faults(lying, 2))


@pytest.mark.parametrize("mutation, why", [
    ("guard_off", "analyst value(s) overwritten"),
    ("journal_poll_off", "lost"),
    ("cursor_and_keys_off", "unlabelled_dup"),
])
def test_the_soak_goes_red_on_a_broken_module(tmp_path, mutation, why):
    dst, n = MU.apply_source(mutation, WORKTREE, str(tmp_path / "code"))
    assert n, "the mutation %s matched no code" % mutation
    rc, res, text = run_soak(tmp_path, "--code", dst)
    assert rc == 1, text[-2000:]
    assert any(why in f for f in res["failures"]), res["failures"]
