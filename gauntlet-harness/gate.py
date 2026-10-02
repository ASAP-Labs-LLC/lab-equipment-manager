"""The LEM transfer gate. Its exit status is its verdict.

    gate.py --target v3.9     run every §9 scenario on the tagged v3.9.0 code
    gate.py --target v4       ... on this worktree (after a v3.9 drift check)
    gate.py --code PATH       ... on any tree
    gate.py --mutations       the self-test (§15.7): each mutation must turn
                              the gate red somewhere
    gate.py --mutation T0     one run with one mutation applied

Exit status:
    0  every scenario holds its v4 row exactly
    1  some scenario misses its v4 row (on v3.9 this is the expected answer:
       19 baseline scenarios and the new ones fail)
    2  the harness itself is not trustworthy: v3.9 drifted from faults.json /
       web.json / economy.json, a call to production was attempted, something
       was written outside the temp root, or code loaded from outside the
       target, or the gate crashed — anywhere: its own imports (wrong
       interpreter, missing package), loading the target, a scenario failing
       in harness code, the self-test, the report. A crash never exits 1,
       because 1 is a verdict. A run that exits 2 says nothing about the target.

With --mutations: 0 if every available mutation was KILLED, 1 if one
SURVIVED (or, with --strict, one was UNAVAILABLE), 2 on a harness failure —
including a mutated run that itself had harness errors, or a subprocess whose
exit status contradicts its own results. On v3.9 five mutations run today (T0
and four P0 source mutations of v3.9 mechanisms); the seven §15.7 mutations of
v4 code are UNAVAILABLE until the pieces that own them land that code.

Never `gate.py | tail && push` — use the exit status (set -o pipefail).

Two rows per scenario live in expectations.json: `v3.9` (today, exact) and
`v4` (spec §9). On --target v3.9 BOTH are checked: v3.9 for drift, v4 for the
verdict. On --target v4, the v3.9 drift check runs first, in a subprocess,
because one process cannot import two versions of the module.
"""
import argparse
import functools
import json
import os
import subprocess
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True

# This import is guarded like everything else: a gate whose own package will
# not import must still exit 2, not Python's 1 (see main()).
try:
    from gharness import env  # noqa: E402  (light: no LEM import)
    _IMPORT_FAILURE = None
except BaseException:                       # noqa: B902
    env = None
    _IMPORT_FAILURE = traceback.format_exc()

HARNESS_FAILURE = 2


def parse(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--target", default="v4", choices=("v3.9", "v4"))
    ap.add_argument("--code", help="run on this tree instead of the target's")
    ap.add_argument("--tmp", help="base directory for the gate's temp root")
    ap.add_argument("--only", help="comma-separated scenario ids")
    ap.add_argument("--mutation", help="apply one harness mutation (T0)")
    ap.add_argument("--mutations", action="store_true", help="run the self-test")
    ap.add_argument("--strict", action="store_true",
                    help="with --mutations: UNAVAILABLE counts as a failure")
    ap.add_argument("--drift-only", action="store_true",
                    help="v3.9 reproduction only; exit 0 iff it holds")
    ap.add_argument("--skip-drift", action="store_true",
                    help="(internal) do not run the v3.9 drift subprocess")
    ap.add_argument("--json", help="also write the full results here")
    ap.add_argument("--quiet", action="store_true")
    return ap.parse_args(argv)


def load_expectations():
    with open(os.path.join(HERE, "expectations.json"), encoding="utf-8") as f:
        return json.load(f)


# ── write audit ──────────────────────────────────────────────────────────────

def audit_roots(code_root):
    """Places a gate run must leave exactly as it found them."""
    from gharness import target
    home = env.real_home()
    roots = [os.path.join(home, "AppData"),
             os.path.join(home, "Library", "Application Support", "LEM"),
             os.path.join(home, "Library", "Application Support", "LabLink"),
             target.BASELINE,
             os.path.join(target.WORKTREE, "LEM Station Module"),
             os.path.join(target.WORKTREE, "LEM Web Server"),
             HERE]
    if code_root and os.path.realpath(code_root) != os.path.realpath(target.WORKTREE):
        roots.append(code_root)
    return roots


def snapshot(roots):
    skip = set()
    for r in roots:
        skip.add(os.path.join(r, ".venv"))
        skip.add(os.path.join(r, "venv"))
    return {r: env.snapshot_tree(r, skip=skip) for r in roots}


# ── the run ──────────────────────────────────────────────────────────────────

def run(args):
    t_start = time.time()
    tmp_root = env.activate(args.tmp)
    from gharness import netguard, target
    netguard.install()
    label, code_root = target.resolve(args.target, args.code, tmp_root)
    roots = audit_roots(code_root if label != "v3.9" else None)
    before = snapshot(roots)

    lh, rf, rw = target.load(code_root)
    netguard.reassert()
    # create_app keeps its log mirror under documents_root, else APP_DIR/data —
    # the CODE directory, whatever LEM_DATA_DIR says. Phase 1's run_web.make()
    # passes no documents_root, so W1–W4 wrote log-mirror.sqlite3 into the
    # tree under test. Same call, rooted in tmp.
    import web_app
    _create_app = web_app.create_app
    import functools

    # functools.wraps keeps create_app's signature visible: servers.py asks
    # `inspect.signature` whether the server takes `labcore=` (a LEM store,
    # P6) — through a bare (*a, **k) wrapper the answer was always "no", so
    # every World ran the LabCore-backed server whatever the target had.
    @functools.wraps(_create_app)
    def create_app(*a, **k):
        k.setdefault("documents_root", os.path.join(tmp_root, "data", "documents"))
        return _create_app(*a, **k)
    rw.create_app = create_app
    web_app.create_app = create_app
    import lem_station_module as mod
    from gharness.hgateway import make_gateway_class
    from gharness.world import make_world, Unsupported, KillNeverReached
    from gharness.servers import server_factory
    from gharness import scenarios as S
    from gharness.expect import compare

    GateGateway = make_gateway_class(lh)
    World = make_world(lh, rf, mod, GateGateway, server_factory)
    World.mutation = args.mutation
    rf.Ctx = World                    # the 29 baseline functions now build Worlds
    reg = S.build(rf, rw, lh, World, mod, GateGateway, server_factory)

    exp = load_expectations()["scenarios"]
    missing = [s for s in reg.order if s not in exp]
    extra = [s for s in exp if s not in reg.fns]
    harness_errors = []
    if missing or extra:
        harness_errors.append("expectations.json and the scenario registry disagree: "
                              "no row for %s; no scenario for %s" % (missing, extra))

    only = set(args.only.split(",")) if args.only else None
    if args.drift_only:
        only = {s for s in reg.order if exp.get(s, {}).get("v3.9") is not None} \
            if only is None else only
    results = {}
    for sid in reg.order:
        if only and sid not in only:
            continue
        t0 = time.time()
        try:
            m = reg.fns[sid]()
            results[sid] = {"status": "ran", "measured": m}
        except Unsupported as e:
            results[sid] = {"status": "unsupported", "why": str(e)}
        except KillNeverReached as e:
            results[sid] = {"status": "kill_not_reached", "why": str(e)}
        except Exception as e:
            origin = crash_origin(sys.exc_info()[2], code_root)
            results[sid] = {"status": "error", "origin": origin,
                            "why": "%s: %s" % (type(e).__name__, e),
                            "trace": traceback.format_exc()[-1500:]}
            if origin == "harness":
                # Not a statement about the target: the gate broke here.
                harness_errors.append("scenario %s crashed in harness code: %s\n%s"
                                      % (sid, results[sid]["why"],
                                         results[sid]["trace"]))
        results[sid]["seconds"] = round(time.time() - t0, 2)

    # economy.json reproduction rides on the E runs (v3.9 only)
    econ_drift = []
    if label == "v3.9" and not args.only:
        econ_drift = economy_drift(reg, S, GateGateway, server_factory, mod, lh)

    # verdicts
    for sid, r in results.items():
        row = exp.get(sid, {})
        vol = set(row.get("volatile", []))
        r["v4_fail"] = _diff(row.get("v4"), r, compare, vol)
        if label == "v3.9":
            r["drift"] = _diff(row.get("v3.9"), r, compare, vol)

    after = snapshot(roots)
    writes = {}
    for root in roots:
        d = env.diff_snapshots(before[root], after[root])
        if any(d.values()):
            writes[root] = d
    try:
        target.verify_loaded(code_root)
    except Exception as e:
        harness_errors.append(str(e))
    viol = netguard.violations()
    if viol:
        harness_errors.append("production was called: %s" % (viol[:5],))
    if writes:
        harness_errors.append("written outside the temp root: %s"
                              % json.dumps(writes)[:800])

    drifted = sorted(s for s, r in results.items() if r.get("drift"))
    v4_failed = sorted(s for s, r in results.items() if r["v4_fail"])
    out = {"target": label, "code_root": code_root, "tmp_root": tmp_root,
           "mutation": args.mutation, "seconds": round(time.time() - t_start, 1),
           "scenarios": results, "drifted": drifted, "v4_failed": v4_failed,
           # economy.json is phase-1 ground truth like faults.json: a miss is
           # drift (exit 2), kept apart from harness_errors so the self-test
           # can count it as a mutation going red rather than as a crash.
           "economy_drift": econ_drift,
           "harness_errors": harness_errors, "writes_outside_tmp": writes,
           "network_attempts": len(netguard.attempts()), "violations": viol}
    return out


def crash_origin(tb, code_root):
    """'target' if the exception was raised from the target's own code, else
    'harness'. Walk from the innermost frame outward to the first frame that is
    the gate's (gauntlet-harness/), the baseline harness's, or the target's;
    library frames (stdlib, site-packages) say nothing either way. The harness
    paths are checked first because on --target v4 the target is the worktree,
    which contains gauntlet-harness/. Nothing recognisable: the harness's —
    an unexplained crash is never evidence about the target."""
    from gharness import target
    ours = [os.path.realpath(HERE) + os.sep,
            os.path.realpath(target.BASELINE_HARNESS) + os.sep]
    theirs = os.path.realpath(code_root) + os.sep if code_root else None
    for fr in reversed(traceback.extract_tb(tb)):
        f = os.path.realpath(fr.filename)
        if "site-packages" in f.split(os.sep):
            continue
        if any(f.startswith(o) for o in ours):
            return "harness"
        if theirs and f.startswith(theirs):
            return "target"
    return "harness"


def _diff(row, r, compare, vol):
    if row is None:
        # No row: the scenario must not have produced numbers (it is a v4-only
        # scenario on a target without the capability). Numbers here mean the
        # harness changed without the expectations changing.
        return ["produced measurements but the row is null"] if r["status"] == "ran" else []
    if r["status"] != "ran":
        return ["%s: %s" % (r["status"], r["why"])]
    if r["status"] == "ran" and isinstance(row, dict):
        return compare(row, r["measured"], vol)
    return ["row is not an object"]


def economy_drift(reg, S, GateGateway, server_factory, mod, lh):
    from gharness.target import BASELINE
    with open(os.path.join(BASELINE, "economy.json")) as f:
        base = json.load(f)
    drift = []
    for want in base:
        key = (want["source_type"], want["prints_per_poll"], None)
        got = reg.economy_runs.get(key) or S._economy_run(
            GateGateway, server_factory, mod, lh, key[0], key[1], None)
        for k, v in want.items():
            if json.loads(json.dumps(got.get(k))) != v:
                drift.append("%s/%s %s: %s != %s" % (key[0], key[1], k,
                                                     json.dumps(got.get(k))[:120],
                                                     json.dumps(v)[:120]))
    return drift


# ── reporting ────────────────────────────────────────────────────────────────

def report(out, quiet=False):
    sc = out["scenarios"]
    if not quiet:
        print("target %s  (%s)" % (out["target"], out["code_root"]))
        if out["mutation"]:
            print("MUTATION %s applied" % out["mutation"])
        for sid, r in sc.items():
            mark = "PASS" if not r["v4_fail"] else "FAIL"
            drift = ""
            if "drift" in r:
                drift = "  v3.9:" + ("ok" if not r["drift"] else "DRIFT")
            m = r.get("measured") or {}
            nums = ""
            if r["status"] == "ran" and "lost" in m:
                nums = "lost/dup %s/%s  res %s/%s  sends/lands %s/%s" % (
                    m.get("lost"), m.get("dup"), m.get("res_lost"), m.get("res_wrong"),
                    m.get("cell_dup_sends"), m.get("cell_dup_lands"))
            elif r["status"] != "ran":
                nums = r["status"] + ": " + r["why"][:110]
            print("  %-6s %s%s  %s" % (sid, mark, drift, nums))
            if r["v4_fail"] and r["status"] == "ran":
                for d in r["v4_fail"][:4]:
                    print("           v4  " + d)
            for d in (r.get("drift") or [])[:6]:
                print("           v3.9 DRIFT " + d)
    print("scenarios %d  v4 failed %d  drifted %d  harness errors %d  network attempts %d  %.1fs"
          % (len(sc), len(out["v4_failed"]), len(out["drifted"]),
             len(out["harness_errors"]), out["network_attempts"], out["seconds"]))
    if out["v4_failed"]:
        print("v4 failed: " + " ".join(out["v4_failed"]))
    if out["drifted"]:
        print("v3.9 DRIFTED: " + " ".join(out["drifted"]))
    for e in out.get("economy_drift") or []:
        print("v3.9 DRIFT economy.json " + e)
    for e in out["harness_errors"]:
        print("HARNESS: " + e)


def exit_code(out, drift_only=False):
    if out["harness_errors"] or out["drifted"] or out.get("economy_drift"):
        return 2
    if drift_only:
        return 0
    return 1 if out["v4_failed"] else 0


# ── the self-test ────────────────────────────────────────────────────────────

def run_mutations(args):
    from gharness import mutations as MU
    from gharness import target
    tmp_root = env.activate(args.tmp, prefix="lem-gate-mut-")
    label, code_root = target.resolve(args.target, args.code, tmp_root)
    row_key = "drift" if label == "v3.9" else "v4_fail"

    def sub(extra, name):
        path = os.path.join(tmp_root, name + ".json")
        cmd = [sys.executable, os.path.join(HERE, "gate.py"), "--target", args.target,
               "--tmp", tmp_root, "--skip-drift", "--quiet", "--json", path] + extra
        if args.code and "--code" not in extra:
            cmd += ["--code", args.code]
        if args.only:
            cmd += ["--only", args.only]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if not os.path.exists(path):
            raise RuntimeError("gate subprocess wrote no results (exit %d): %s"
                               % (p.returncode, p.stdout.decode(errors="replace")[-1500:]))
        with open(path) as f:
            res = json.load(f)
        # The subprocess's exit status must be the one its own results imply;
        # if not, one of the two is lying and nothing here can be judged.
        if p.returncode != exit_code(res):
            raise RuntimeError("gate subprocess %s exited %d but its results imply %d"
                               % (name, p.returncode, exit_code(res)))
        if res["harness_errors"]:
            raise RuntimeError("gate subprocess %s is not trustworthy: %s"
                               % (name, "; ".join(res["harness_errors"])[:1500]))
        return res, p.returncode

    def greens(res):
        g = {s: not r[row_key] for s, r in res["scenarios"].items()}
        if label == "v3.9":
            g["economy.json"] = not res.get("economy_drift")
        return g

    clean, rc = sub([], "clean")     # raises (-> exit 2) on harness errors
    green = greens(clean)
    print("clean run: %d scenarios, %d green against their %s row"
          % (len(green), sum(green.values()), "v3.9" if label == "v3.9" else "v4"))
    survived = unavailable = 0
    for name, spec in MU.MUTATIONS.items():
        if spec["kind"] == "harness":
            res, _ = sub(["--mutation", name], "mut-" + name)
        else:
            dst, n = MU.apply_source(name, code_root, os.path.join(tmp_root, "mut-" + name))
            if not n:
                unavailable += 1
                print("  %-24s UNAVAILABLE  (%s; pattern owned by %s; target has no "
                      "matching code)" % (name, spec["what"], spec["owner"]))
                continue
            res, _ = sub(["--code", dst], "mut-" + name)
        mutated = greens(res)
        v, killed = MU.verdict(green, mutated)
        if v == "SURVIVED":
            survived += 1
        print("  %-24s %-9s %s  (red: %d of %d green: %s)"
              % (name, v, spec["what"], len(killed), sum(green.values()),
                 " ".join(killed[:12]) + (" ..." if len(killed) > 12 else "")))
    if survived:
        return 1
    if args.strict and unavailable:
        return 1
    return 0


def main(argv=None):
    """Parse, then run guarded: whatever escapes is exit 2.

    An uncaught exception would exit 1 — the same status as the correct v3.9
    verdict and as "v4 fails". So nothing escapes: any exception, including a
    SystemExit raised by the code under test (even SystemExit(0)) and an
    interrupt, is a harness failure. argparse's own errors already exit 2."""
    args = parse(argv if argv is not None else sys.argv[1:])
    try:
        if _IMPORT_FAILURE:
            raise ImportError("the gate's own package did not import:\n"
                              + _IMPORT_FAILURE)
        return _main(args)
    except BaseException as e:              # noqa: B902 — that is the point
        return _harness_crash(e)


def _harness_crash(e):
    try:
        tb = traceback.format_exc()
        sys.stdout.write(tb[-4000:])
        sys.stdout.write("HARNESS: the gate crashed (%s: %s) — exit 2; this run "
                         "says nothing about the target\n" % (type(e).__name__, e))
        sys.stdout.flush()
    except BaseException:                   # noqa: B902 — even printing failed
        pass
    return HARNESS_FAILURE


def _write_results(out, args):
    if args.json:
        with open(args.json, "w") as f:
            json.dump(out, f, indent=1, default=str)
    with open(os.path.join(out["tmp_root"], "results.json"), "w") as f:
        json.dump(out, f, indent=1, default=str)


def _main(args):
    if args.mutations:
        return run_mutations(args)
    drift_rc = None
    if args.target == "v4" and not args.code and not args.skip_drift and not args.drift_only:
        p = subprocess.run([sys.executable, os.path.join(HERE, "gate.py"), "--target",
                            "v3.9", "--drift-only", "--quiet"]
                           + (["--tmp", args.tmp] if args.tmp else []),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        drift_rc = p.returncode
        print(p.stdout.decode(errors="replace").rstrip())
        if drift_rc != 0:
            print("HARNESS: the v3.9 reproduction failed (exit %d) — the gate is not "
                  "trusted; v4 not judged" % drift_rc)
            return 2
    out = run(args)
    _write_results(out, args)
    report(out, quiet=args.quiet)
    return exit_code(out, args.drift_only)


if __name__ == "__main__":
    sys.exit(main())
