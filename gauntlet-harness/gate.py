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
       target. A run that exits 2 says nothing about the target.

With --mutations: 0 if every available mutation was KILLED, 1 if one
SURVIVED (or, with --strict, one was UNAVAILABLE), 2 on a harness failure.

Never `gate.py | tail && push` — use the exit status (set -o pipefail).

Two rows per scenario live in expectations.json: `v3.9` (today, exact) and
`v4` (spec §9). On --target v3.9 BOTH are checked: v3.9 for drift, v4 for the
verdict. On --target v4, the v3.9 drift check runs first, in a subprocess,
because one process cannot import two versions of the module.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True

from gharness import env  # noqa: E402  (light: no LEM import)


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
            results[sid] = {"status": "error",
                            "why": "%s: %s" % (type(e).__name__, e),
                            "trace": traceback.format_exc()[-1500:]}
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
    if econ_drift:
        harness_errors.append("economy.json not reproduced: " + "; ".join(econ_drift[:5]))
    v4_failed = sorted(s for s, r in results.items() if r["v4_fail"])
    out = {"target": label, "code_root": code_root, "tmp_root": tmp_root,
           "mutation": args.mutation, "seconds": round(time.time() - t_start, 1),
           "scenarios": results, "drifted": drifted, "v4_failed": v4_failed,
           "harness_errors": harness_errors, "writes_outside_tmp": writes,
           "network_attempts": len(netguard.attempts()), "violations": viol}
    return out


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
    for e in out["harness_errors"]:
        print("HARNESS: " + e)


def exit_code(out, drift_only=False):
    if out["harness_errors"] or out["drifted"]:
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
            return json.load(f), p.returncode

    clean, rc = sub([], "clean")
    if clean["harness_errors"]:
        print("clean run has harness errors; the self-test cannot be judged:")
        for e in clean["harness_errors"]:
            print("  " + e)
        return 2
    green = {s: not r[row_key] for s, r in clean["scenarios"].items()}
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
        mutated = {s: not r[row_key] for s, r in res["scenarios"].items()}
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
    args = parse(argv if argv is not None else sys.argv[1:])
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
    if args.json:
        with open(args.json, "w") as f:
            json.dump(out, f, indent=1, default=str)
    with open(os.path.join(out["tmp_root"], "results.json"), "w") as f:
        json.dump(out, f, indent=1, default=str)
    report(out, quiet=args.quiet)
    return exit_code(out, args.drift_only)


if __name__ == "__main__":
    sys.exit(main())
