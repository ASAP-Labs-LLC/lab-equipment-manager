"""The gate's self-test: break something on purpose; the gate must go red.

Spec §15.7. A mutation that leaves the gate green means the gate cannot see
that failure, and the gate is not trusted there. Two kinds:

* **harness** — applied inside the gate process. T0 deletes one stored row
  before the tally (a real DELETE on a LabCore store; on the append-only LEM
  store the row is dropped at read). It catches any tally term that was
  multiplied by 0 or zeroed by hand.

* **source** — a regex edit of a COPY of the target tree (the worktree is
  never touched), then the gate is run on the copy with `--code`. The regex
  must match at least once; one that matches nothing is UNAVAILABLE ("the code
  this mutation breaks does not exist on this target yet"), reported as such
  and never counted as killed. The piece that builds the code owns the pattern
  (`owner`) and fills it in when the code exists.

Verdicts: KILLED (some scenario green in the clean run is red under the
mutation), SURVIVED (none — the gate is blind there), UNAVAILABLE.
"""
import os
import re
import shutil

MUTATIONS = {
    "T0": {"kind": "harness", "what": "tally row deletion: one stored run row removed",
           "owner": "P0"},
    "unique_seq_off": {
        "kind": "source", "owner": "P6",
        "what": "unique (uid, epoch, seq) removed from the store",
        "pattern": r"CREATE UNIQUE INDEX (IF NOT EXISTS )?ux_log_bench",
        "replace": r"CREATE INDEX \1ux_log_bench"},
    "journal_order_reversed": {
        "kind": "source", "owner": "P1", "pattern": None, "replace": None,
        "what": "journal-before-consume order reversed (cursor saved before the journal fsync)"},
    "cursor_and_keys_off": {
        "kind": "source", "owner": "P2", "pattern": None, "replace": None,
        "what": "persisted cursor and content keys ignored"},
    "count_matching": {
        "kind": "source", "owner": "P2", "pattern": None, "replace": None,
        "what": "ordered diff replaced by count matching (X1/X2 must fail)"},
    "guard_off": {
        "kind": "source", "owner": "P3", "pattern": None, "replace": None,
        "what": "results guard read skipped"},
    "per_index_off": {
        "kind": "source", "owner": "P3", "pattern": None, "replace": None,
        "what": "per-index batch results ignored (B1 must fail)"},
    "blind_mode_off": {
        "kind": "source", "owner": "P8", "pattern": None, "replace": None,
        "what": "blind mode off: a wiped journal re-sends (T4 floods)"},
}

CODE_DIRS = ("LEM Station Module", "LEM Web Server")
SKIP_DIRS = {".venv", "venv", "__pycache__", "tests", "static", "templates", "data",
             "node_modules", ".git"}


def copy_tree(src_root, dst_root):
    for sub in CODE_DIRS:
        shutil.copytree(os.path.join(src_root, sub), os.path.join(dst_root, sub),
                        ignore=shutil.ignore_patterns(".venv", "venv", "__pycache__",
                                                      "*.pyc", "data", "*.log"))
    return dst_root


def apply_source(name, code_root, dst_root):
    """Copy, edit, and return (dst_root, matches). matches == 0 means
    UNAVAILABLE and nothing was edited."""
    spec = MUTATIONS[name]
    if spec["kind"] != "source":
        raise ValueError(name + " is not a source mutation")
    if not spec.get("pattern"):
        return None, 0
    rx = re.compile(spec["pattern"])
    hits = []
    for sub in CODE_DIRS:
        for dirpath, dirnames, files in os.walk(os.path.join(code_root, sub)):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for f in files:
                if f.endswith(".py"):
                    p = os.path.join(dirpath, f)
                    with open(p, encoding="utf-8") as fh:
                        if rx.search(fh.read()):
                            hits.append(os.path.relpath(p, code_root))
    if not hits:
        return None, 0
    copy_tree(code_root, dst_root)
    n = 0
    for rel in hits:
        p = os.path.join(dst_root, rel)
        with open(p, encoding="utf-8") as fh:
            src = fh.read()
        new, k = rx.subn(spec["replace"], src)
        n += k
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(new)
    return dst_root, n


def verdict(clean, mutated):
    """`clean` / `mutated`: {scenario: bool green}. KILLED if any scenario
    green in the clean run is red under the mutation."""
    killed = sorted(s for s, ok in clean.items() if ok and not mutated.get(s, False))
    return ("KILLED" if killed else "SURVIVED"), killed
