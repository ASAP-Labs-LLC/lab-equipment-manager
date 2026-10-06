"""The release gate must run from a clean clone, with nothing set.

RELEASING.md tells whoever cuts a 4.x tag to run `gate.py --target v4`,
`gate.py --target v4 --mutations --strict` and `soak.py`. Until this test
existed, all three exited 2 (ModuleNotFoundError: lemharness) on any machine
but the one that built the gate: the default baseline path pointed into a
session scratchpad under /private/tmp that had already been deleted, and the
phase-1 harness the gate imports was never committed. A release gate that only
runs on one laptop for one week is not a release gate.

So: the phase-1 baseline the gate reproduces (harness + the three result files
+ the LabCore table excerpt) lives inside gauntlet-harness/, the default
resolves to it, and LEM_GATE_BASELINE stays as an override. These tests run in
a subprocess with LEM_GATE_BASELINE REMOVED from the environment, because the
pytest process itself may have inherited it from whoever ran the suite.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # gauntlet-harness/

PROBE = r"""
import json, os, sys
sys.path.insert(0, %(here)r)
sys.dont_write_bytecode = True
from gharness import target
out = {"baseline": os.path.realpath(target.BASELINE),
       "harness": os.path.realpath(target.BASELINE_HARNESS)}
from gharness import hgateway
db_ops, inner = hgateway.labcore_tables()
out["db_ops"] = len(db_ops); out["inner"] = sorted(inner)
sys.path.insert(0, target.BASELINE_HARNESS)
import importlib.util
out["importable"] = {n: importlib.util.find_spec(n) is not None
                     for n in ("lemharness", "run_faults", "run_web")}
print(json.dumps(out))
"""


def _probe(extra_env=None):
    e = dict(os.environ)
    e.pop("LEM_GATE_BASELINE", None)
    e.update(extra_env or {})
    r = subprocess.run([sys.executable, "-c", PROBE % {"here": HERE}],
                       env=e, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_the_default_baseline_is_inside_the_repo_and_complete():
    """With nothing set, the baseline is gauntlet-harness/baseline — committed,
    so a clone has it — and every file the gate reads from it is there."""
    out = _probe()
    want = os.path.realpath(os.path.join(HERE, "baseline"))
    assert out["baseline"] == want
    for rel in ("harness/lemharness.py", "harness/run_faults.py",
                "harness/run_web.py", "faults.json", "web.json", "economy.json",
                "prod/LabCore_tables.excerpt"):
        assert os.path.isfile(os.path.join(want, rel)), rel
    assert all(out["importable"].values()), out["importable"]


def test_the_labcore_tables_still_parse_from_the_committed_excerpt():
    """The gateway models LabCore's batch rules from LabCore's own tables. The
    excerpt must still yield them: update_cell batchable, the seven inner ops
    exactly as LabCore_main.py had them on 2026-10-01."""
    out = _probe()
    assert out["inner"] == sorted(["update_cell", "insert_sample", "add_test",
                                   "update_sample_field", "remove_test",
                                   "add_result", "update_result"])
    assert out["db_ops"] > 50


def test_the_override_still_wins(tmp_path):
    """LEM_GATE_BASELINE still points the gate at another baseline (e.g. one
    re-measured on a new tag) when set."""
    e = dict(os.environ)
    e["LEM_GATE_BASELINE"] = str(tmp_path)
    r = subprocess.run([sys.executable, "-c",
                        "import sys; sys.path.insert(0, %r); from gharness import target;"
                        "print(target.BASELINE)" % HERE],
                       env=e, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == str(tmp_path)


def test_no_vendored_file_names_a_machine_path_or_production():
    """A vendored file that hard-codes /Users/... or /private/tmp/... works on
    one machine only, which is the bug this replaces; one that names the
    production LabCore host could be pointed at it. Neither may be committed."""
    base = os.path.join(HERE, "baseline")
    bad = []
    for dirpath, _dirs, files in os.walk(base):
        for name in files:
            p = os.path.join(dirpath, name)
            with open(p, encoding="utf-8") as f:
                text = f.read()
            for needle in ("/Users/", "/private/tmp", "labvision.asaplabs.net"):
                if needle in text:
                    bad.append("%s: %s" % (os.path.relpath(p, base), needle))
    assert bad == []
