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


WEB_PY = os.environ.get("LEM_WEB_PYTHON") or os.path.join(
    "/Users/rynatical/Projects/lab-equipment-manager", "LEM Web Server",
    ".venv", "bin", "python")

# What a second writer does to the store: insert a row for a (uid, epoch,
# seq) the store already holds — once through plain INSERT, once through
# INSERT OR IGNORE. Prints how many of the two the store ACCEPTED.
_REPLAY = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
import lem_store
s = lem_store.LocalStoreGateway(sys.argv[2])
row = ("INSERT {0}INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
       "test_name, value, detail, origin, bench_epoch, bench_seq) VALUES "
       "('b1', '2026-10-01 09:00:00', 'run', 'L-1', 'Density', '0.8', '{{}}', "
       "'bench', 'e1', 1)")
first = s.sql(row.format(""))
assert not first.get("error"), first
accepted = 0
for verb in ("", "OR IGNORE "):
    res = s.sql(row.format(verb))
    if not res.get("error") and res.get("rows_affected", 1):
        accepted += 1
n = s.read_sql("SELECT COUNT(*) AS n FROM lem_machine_log WHERE "
               "machine_uid='b1' AND bench_epoch='e1' AND bench_seq=1")
print(accepted, n["rows"][0]["n"])
"""


def _replay_into_store(code_root, tmp_path):
    """(second-writer inserts accepted, rows held for that one seq) on a
    fresh store built by `code_root`'s lem_store.py."""
    if not os.path.exists(WEB_PY):
        pytest.skip("the web server's venv is not at %s" % WEB_PY)
    p = subprocess.run([WEB_PY, "-c", _REPLAY,
                        os.path.join(code_root, "LEM Web Server"),
                        str(tmp_path / "store.sqlite3")],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=120)
    assert p.returncode == 0, p.stderr.decode()
    a, n = p.stdout.decode().split()
    return int(a), int(n)


def test_the_store_refuses_a_second_row_for_one_bench_seq(tmp_path):
    """The property `unique_seq_off` must break, measured on this tree."""
    assert _replay_into_store(WORKTREE, tmp_path) == (0, 1)


def test_removing_only_the_index_was_an_equivalent_mutant(tmp_path):
    """Why `unique_seq_off` is two edits, not one. Its first form turned
    `ux_log_bench` into a plain index and nothing else, and SURVIVED every
    gate run (critic, T-P8 round 2). It could not do otherwise: the store
    enforces the same uniqueness a second time, in `lem_log_no_overwrite`'s
    (uid, epoch, seq) arm, which refuses the insert before any index is
    consulted. With the index alone removed, a second writer is still
    refused — no test of any kind could tell the mutant from the original,
    which is the definition of an equivalent mutant. Pinned here, so that
    the mutation is never "simplified" back into one the gate cannot see."""
    dst, n = MU.apply_source("unique_seq_off", WORKTREE,
                             str(tmp_path / "copy"),
                             edits=MU.MUTATIONS["unique_seq_off"]["edits"][:1])
    assert n == 1
    assert _replay_into_store(dst, tmp_path) == (0, 1)


def test_unique_seq_off_removes_the_uniqueness_itself(tmp_path):
    """P6 owns the code: `ux_log_bench` and the overwrite trigger's
    (uid, epoch, seq) arm each exist once in this worktree. The mutation
    edits both — in the copy only — and the copy's store then accepts a
    second row for a seq it holds: the constraint is really gone."""
    store_src = os.path.join(WORKTREE, "LEM Web Server", "lem_store.py")
    before = open(store_src, encoding="utf-8").read()
    dst, n = MU.apply_source("unique_seq_off", WORKTREE, str(tmp_path / "copy"))
    assert n == 2
    mutated = open(os.path.join(dst, "LEM Web Server", "lem_store.py"),
                   encoding="utf-8").read()
    assert "CREATE INDEX IF NOT EXISTS ux_log_bench" in mutated
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ux_log_bench" not in mutated
    assert open(store_src, encoding="utf-8").read() == before
    accepted, held = _replay_into_store(dst, tmp_path / "m")
    assert accepted >= 1 and held >= 2


def test_a_multi_edit_mutation_with_one_edit_missing_is_unavailable(
        tmp_path, monkeypatch):
    """Every edit of a mutation must find its code: half a mutation breaks
    less than it says, and a KILLED verdict on it would overclaim."""
    monkeypatch.setitem(MU.MUTATIONS, "half_there", {
        "kind": "source", "owner": "P99", "what": "one edit names no code",
        "edits": [{"pattern": r"CREATE UNIQUE INDEX (IF NOT EXISTS )?ux_log_bench",
                   "replace": r"CREATE INDEX \1ux_log_bench"},
                  {"pattern": r"THIS_TEXT_IS_IN_NO_LEM_SOURCE_FILE",
                   "replace": "x"}]})
    dst, n = MU.apply_source("half_there", WORKTREE, str(tmp_path / "copy"))
    assert (dst, n) == (None, 0)
    assert not (tmp_path / "copy").exists()


def test_resend_skip_off_is_available_and_edits_only_the_copy(tmp_path):
    """The server's resend handling (§6.1 step 3: a record at or below
    `acked` is ignored; one already held is not written again). Both edits
    find their code once; the worktree is untouched."""
    src = os.path.join(WORKTREE, "LEM Web Server", "bench_api.py")
    before = open(src, encoding="utf-8").read()
    dst, n = MU.apply_source("resend_skip_off", WORKTREE, str(tmp_path / "copy"))
    assert n == 2
    assert open(src, encoding="utf-8").read() == before


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
