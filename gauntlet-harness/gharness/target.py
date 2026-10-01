"""Which LEM code the gate runs, and making sure it is THAT code that runs.

  --target v3.9   the tagged v3.9.0 tree, extracted with `git archive v3.9.0`
                  into the gate's temp root. Not the main checkout, not this
                  worktree: "today" must stay today when either moves.
  --target v4     this worktree (the parent of gauntlet-harness/).
  --code PATH     any tree with "LEM Station Module" and "LEM Web Server".

The baseline harness (`baseline/harness/lemharness.py`) is imported read-only,
and it hard-codes the MAIN checkout onto the front of sys.path. Left alone,
that would silently run the main checkout's module under a gate that says it
ran v3.9.0 or this worktree. So the target's modules are imported FIRST (they
are then in sys.modules and win), the main-checkout paths lemharness inserts
are removed again, and `verify_loaded()` fails the gate if any loaded module
came from anywhere but the target.
"""
import os
import subprocess
import sys
import tarfile
import io

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # gauntlet-harness/
WORKTREE = os.path.dirname(HERE)
MAIN_CHECKOUT = "/Users/rynatical/Projects/lab-equipment-manager"
BASELINE = os.environ.get(
    "LEM_GATE_BASELINE",
    "/private/tmp/claude-501/-Users-rynatical/d75f6110-132a-4335-bd09-5d98bf281f0c"
    "/scratchpad/gauntlet/baseline")
BASELINE_HARNESS = os.path.join(BASELINE, "harness")

TAGS = {"v3.9": "v3.9.0"}
CORE_MODULES = ("lem_station_module", "test_module_qt", "labcore_gateway",
                "snapshot_service", "web_app", "log_mirror")


class TargetError(RuntimeError):
    pass


def extract_tag(tag, dest):
    """`git archive <tag>` of the two code folders, unpacked under dest."""
    out = subprocess.run(
        ["git", "-C", WORKTREE, "archive", "--format=tar", tag,
         "LEM Station Module", "LEM Web Server"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if out.returncode != 0:
        raise TargetError("git archive %s failed: %s"
                          % (tag, out.stderr.decode(errors="replace")[:300]))
    with tarfile.open(fileobj=io.BytesIO(out.stdout)) as tf:
        tf.extractall(dest)
    return dest


def resolve(target, code=None, tmp_root=None):
    """(label, code_root). The code root holds the two code folders."""
    if code:
        root = os.path.realpath(code)
        label = target or "custom"
    elif target in TAGS:
        root = os.path.join(tmp_root, "target-" + target)
        os.makedirs(root, exist_ok=True)
        extract_tag(TAGS[target], root)
        label = target
    elif target == "v4":
        root = WORKTREE
        label = "v4"
    else:
        raise TargetError("unknown target %r (v3.9, v4, or --code PATH)" % (target,))
    for sub in ("LEM Station Module", "LEM Web Server"):
        if not os.path.isdir(os.path.join(root, sub)):
            raise TargetError("%s has no %r" % (root, sub))
    return label, root


def code_paths(root):
    return [os.path.join(root, "LEM Web Server"),
            os.path.join(root, "LEM Station Module", "tests"),
            os.path.join(root, "LEM Station Module")]


def load(root):
    """Import the target's modules, then the baseline harness, in the order
    that makes the target win. Returns the baseline modules
    (lemharness, run_faults, run_web)."""
    sys.dont_write_bytecode = True
    for p in reversed(code_paths(root)):
        if p not in sys.path:
            sys.path.insert(0, p)
    import importlib
    for name in CORE_MODULES:
        importlib.import_module(name)
    if BASELINE_HARNESS not in sys.path:
        sys.path.insert(0, BASELINE_HARNESS)
    import lemharness            # noqa: F401  (inserts MAIN_CHECKOUT paths)
    _strip_main_checkout()
    import run_faults            # noqa: F401
    import run_web               # noqa: F401  (sets LEM_DATA_DIR = mkdtemp)
    _strip_main_checkout()
    verify_loaded(root)
    return lemharness, run_faults, run_web


def _strip_main_checkout():
    keep = []
    for p in sys.path:
        rp = os.path.realpath(p) if p else p
        if rp and rp.startswith(os.path.realpath(MAIN_CHECKOUT) + os.sep) \
                and "site-packages" not in rp.split(os.sep) \
                and not rp.startswith(os.path.realpath(WORKTREE)):
            continue
        keep.append(p)
    sys.path[:] = keep


def verify_loaded(root):
    """Every LEM module in sys.modules came from `root`. Raises otherwise."""
    root_r = os.path.realpath(root)
    main_r = os.path.realpath(MAIN_CHECKOUT)
    bad = []
    for name, m in list(sys.modules.items()):
        f = getattr(m, "__file__", None)
        if not f:
            continue
        rf = os.path.realpath(f)
        if "site-packages" in rf.split(os.sep):
            continue                      # the interpreter's libraries, not LEM
        if rf.startswith(main_r + os.sep):
            bad.append("%s from %s" % (name, rf))
    for name in CORE_MODULES:
        f = os.path.realpath(sys.modules[name].__file__)
        if not f.startswith(root_r + os.sep):
            bad.append("%s from %s (expected under %s)" % (name, f, root_r))
    if bad:
        raise TargetError("loaded code outside the target: " + "; ".join(bad[:5]))
    return True
