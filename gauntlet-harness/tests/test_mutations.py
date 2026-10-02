"""The self-test has to be able to say more than "T0".

A gate is only as sharp as the breakages it has been shown to see. Until v4
code exists, every §15.7 source mutation is UNAVAILABLE (its pattern names
code that has not been written), which left T0 — a harness-side row deletion —
as the only mutation that ever ran, and the source route (copy the tree, edit
the copy, run the gate on the copy with --code) as code nobody had executed.

So four P0-owned source mutations break things v3.9 ALREADY does right, and
match both v3.9.0 and this worktree. Each must be KILLED: some scenario that
is green in the clean run goes red. One that SURVIVED would mean the gate is
blind to that failure today, and the self-test exits 1.

The runs here use --only with a handful of scenarios so the suite stays fast;
`gate.py --mutations` with no --only runs all 80 (about a minute).
"""
import json
import os
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(HERE, "gate.py")
WORKTREE = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from gharness import mutations as MU  # noqa: E402

P0_SOURCE = [n for n, s in MU.MUTATIONS.items() if s["kind"] == "source" and s["owner"] == "P0"]
ONLY = "K1,F1,F3,L1,A4"


def _v390_source(rel):
    p = subprocess.run(["git", "-C", WORKTREE, "show", "v3.9.0:" + rel],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert p.returncode == 0, p.stderr
    return p.stdout.decode()


def test_there_are_runnable_source_mutations():
    assert len(P0_SOURCE) == 4


@pytest.mark.parametrize("name", P0_SOURCE)
def test_each_p0_pattern_matches_exactly_once_today_and_in_this_worktree(name):
    # Exactly once: a pattern that drifted to zero matches would silently
    # become UNAVAILABLE; one matching twice would break more than it says.
    rx = re.compile(MU.MUTATIONS[name]["pattern"])
    rel = "LEM Station Module/lem_station_module.py"
    assert len(rx.findall(_v390_source(rel))) == 1
    with open(os.path.join(WORKTREE, rel), encoding="utf-8") as f:
        assert len(rx.findall(f.read())) == 1


def test_apply_source_edits_a_copy_and_never_the_tree(tmp_path):
    rel = os.path.join(WORKTREE, "LEM Station Module", "lem_station_module.py")
    before = open(rel, encoding="utf-8").read()
    dst, n = MU.apply_source("offset_not_advanced", WORKTREE, str(tmp_path / "copy"))
    assert n == 1
    assert open(rel, encoding="utf-8").read() == before
    mutated = open(os.path.join(dst, "LEM Station Module", "lem_station_module.py"),
                   encoding="utf-8").read()
    assert "new_position = last_position" in mutated
    assert "new_position = f.tell()" not in mutated


def test_a_pattern_with_no_match_is_unavailable_and_copies_nothing(
        tmp_path, monkeypatch):
    # A pattern that names no code. This used to borrow `unique_seq_off`,
    # which named code nobody had written — until P6 wrote it (below).
    monkeypatch.setitem(MU.MUTATIONS, "names_nothing", {
        "kind": "source", "owner": "P99", "what": "matches no LEM source",
        "pattern": r"THIS_TEXT_IS_IN_NO_LEM_SOURCE_FILE", "replace": "x"})
    dst, n = MU.apply_source("names_nothing", WORKTREE, str(tmp_path / "copy"))
    assert (dst, n) == (None, 0)
    assert not (tmp_path / "copy").exists()


def test_unique_seq_off_is_available_once_p6_has_landed(tmp_path):
    """P6 owns it: the store's `ux_log_bench` unique index exists in this
    worktree now, exactly once, and the mutation turns it into a plain
    index in the copy — and only the copy."""
    store_src = os.path.join(WORKTREE, "LEM Web Server", "lem_store.py")
    before = open(store_src, encoding="utf-8").read()
    dst, n = MU.apply_source("unique_seq_off", WORKTREE, str(tmp_path / "copy"))
    assert n == 1
    mutated = open(os.path.join(dst, "LEM Web Server", "lem_store.py"),
                   encoding="utf-8").read()
    assert "CREATE INDEX IF NOT EXISTS ux_log_bench" in mutated
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ux_log_bench" not in mutated
    assert open(store_src, encoding="utf-8").read() == before


def test_verdict():
    assert MU.verdict({"a": True, "b": False}, {"a": False, "b": False}) == ("KILLED", ["a"])
    # Red that was already red is not a kill.
    assert MU.verdict({"a": True, "b": False}, {"a": True, "b": False})[0] == "SURVIVED"


def _self_test(tmp_path, *extra):
    p = subprocess.run([sys.executable, GATE, "--target", "v3.9", "--mutations",
                        "--only", ONLY, "--tmp", str(tmp_path)] + list(extra),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
    out = p.stdout.decode(errors="replace")
    verdicts = dict(re.findall(r"^\s+(\S+)\s+(KILLED|SURVIVED|UNAVAILABLE)\b", out, re.M))
    return p.returncode, verdicts, out


@pytest.fixture(scope="module")
def self_test(tmp_path_factory):
    return _self_test(tmp_path_factory.mktemp("mut"))


def test_every_runnable_mutation_is_killed_on_v39(self_test):
    rc, verdicts, out = self_test
    assert set(verdicts) == set(MU.MUTATIONS), out
    for name in ["T0"] + P0_SOURCE:
        assert verdicts[name] == "KILLED", out
    assert rc == 0, out


def test_mutations_without_code_are_unavailable_not_killed(self_test):
    rc, verdicts, out = self_test
    for name, spec in MU.MUTATIONS.items():
        if spec["kind"] == "source" and spec["owner"] != "P0":
            assert verdicts[name] == "UNAVAILABLE", name


def test_strict_counts_unavailable_as_a_failure(tmp_path):
    rc, verdicts, out = _self_test(tmp_path, "--strict")
    assert rc == 1, out


def test_a_mutated_run_with_harness_errors_is_exit_2(monkeypatch, tmp_path, capsys):
    # If a mutated run wrote outside tmp, or called production, its red is not
    # evidence of anything — the self-test must stop at 2, not count a kill.
    import gate
    real_run = subprocess.run

    def fake_run(cmd, **kw):
        if "--json" not in cmd:                  # git archive of v3.9.0
            return real_run(cmd, **kw)
        path = cmd[cmd.index("--json") + 1]
        mutated = "--mutation" in cmd or "--code" in cmd
        res = {"scenarios": {"K1": {"drift": ["x"] if mutated else [], "v4_fail": ["y"]}},
               "drifted": ["K1"] if mutated else [], "v4_failed": ["K1"],
               "economy_drift": [],
               "harness_errors": ["production was called"] if mutated else []}
        with open(path, "w") as f:
            json.dump(res, f)
        return subprocess.CompletedProcess(cmd, 2 if mutated else 1, b"", None)
    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    rc = gate.main(["--target", "v3.9", "--mutations", "--only", "K1",
                    "--tmp", str(tmp_path)])
    assert rc == 2
    assert "production was called" in capsys.readouterr().out


def test_a_subprocess_whose_status_contradicts_its_results_is_exit_2(monkeypatch, tmp_path,
                                                                     capsys):
    import gate
    real_run = subprocess.run

    def fake_run(cmd, **kw):
        if "--json" not in cmd:                  # git archive of v3.9.0
            return real_run(cmd, **kw)
        path = cmd[cmd.index("--json") + 1]
        res = {"scenarios": {"K1": {"drift": [], "v4_fail": ["y"]}}, "drifted": [],
               "v4_failed": ["K1"], "economy_drift": [], "harness_errors": []}
        with open(path, "w") as f:
            json.dump(res, f)
        return subprocess.CompletedProcess(cmd, 0, b"", None)   # results say 1
    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    rc = gate.main(["--target", "v3.9", "--mutations", "--only", "K1",
                    "--tmp", str(tmp_path)])
    assert rc == 2
    assert "exited 0 but its results imply 1" in capsys.readouterr().out


P4_SOURCE = ["adoption_off", "adoption_on_corrected", "recovered_filed",
             "adoption_qc_on_value"]


@pytest.mark.parametrize("name", P4_SOURCE)
def test_each_p4_pattern_matches_exactly_once_here_and_never_in_v390(name):
    """P4's mutations name adoption code: once in this worktree (so the
    self-test can break it), nowhere in v3.9.0 (so they are UNAVAILABLE
    there, not silently killed by something else)."""
    rx = re.compile(MU.MUTATIONS[name]["pattern"])
    rel = "LEM Station Module/lem_station_module.py"
    with open(os.path.join(WORKTREE, rel), encoding="utf-8") as f:
        assert len(rx.findall(f.read())) == 1
    assert rx.findall(_v390_source(rel)) == []
