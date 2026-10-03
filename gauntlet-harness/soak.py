"""The 24 h accelerated soak. Its exit status is its verdict.

    soak.py                       17 benches, 24 simulated hours, seed 1
    soak.py --hours 2 --seed 7    a shorter day (the same faults, scaled)
    soak.py --code PATH           on another tree (a mutated copy, say)

Exit status:
    0  the bar holds: 0 lost, 0 unlabelled dup, 0 analyst values overwritten
       (the soak injects no A5 race), every result filed or left to a
       person, every bench drained
    1  the bar does not hold (what failed is printed)
    2  the soak is not trustworthy: a fault it scheduled never fired (no
       kill, no restore, no 409 ...), production was called, it wrote
       outside its temp root, or it crashed. A 2 says nothing about the code.

See gharness/soak.py for what one simulated day contains.
Never `soak.py | tail && ...` — use the exit status (set -o pipefail).
"""
import argparse
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True


def parse(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--benches", type=int, default=17)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--kill-p", type=float, default=0.012,
                    help="chance, per bench poll that prints, of arming a kill")
    ap.add_argument("--code", help="run on this tree instead of the worktree")
    ap.add_argument("--tmp", help="base directory for the soak's temp root")
    ap.add_argument("--json", help="also write the full results here")
    return ap.parse_args(argv)


def run(args):
    from gharness import env
    env.activate(args.tmp, prefix="lem-soak-")
    from gharness import netguard, target
    netguard.install()
    label, code_root = target.resolve("v4", args.code, env.root())
    import gate
    roots = gate.audit_roots(code_root)
    before = gate.snapshot(roots)
    lh, rf, rw = target.load(code_root)
    netguard.reassert()
    import lem_station_module as mod
    if not hasattr(mod, "BenchUploader"):
        raise RuntimeError("the soak needs a v4 bench (transfer v2); %s has "
                           "none" % code_root)
    from gharness.hgateway import make_gateway_class
    from gharness.servers import server_factory
    from gharness import soak
    t0 = time.time()
    fleet = soak.Fleet(lh, rf, mod, make_gateway_class(lh), server_factory,
                       n=args.benches, seed=args.seed, hours=args.hours)
    out = fleet.run(kill_p=args.kill_p)
    out["seconds"] = round(time.time() - t0, 1)
    out["code_root"] = code_root
    out["tmp_root"] = env.root()
    harness = []
    harness += ["scheduled fault did not happen: " + m
                for m in soak.proof_of_faults(out, args.hours)]
    target.verify_loaded(code_root)
    viol = netguard.violations()
    if viol:
        harness.append("production was called: %s" % (viol[:5],))
    after = gate.snapshot(roots)
    for root in roots:
        d = env.diff_snapshots(before[root], after[root])
        if any(d.values()):
            harness.append("written outside the temp root: %s %s"
                           % (root, json.dumps(d)[:400]))
    out["harness_errors"] = harness
    out["network_attempts"] = len(netguard.attempts())
    out["failures"] = soak.verdict(out)
    return out


def report(out):
    t = out["totals"]
    print("soak: %d benches, %.1f simulated h, seed %d, %d polls/bench, %.0fs"
          % (out["benches"], out["simulated_hours"], out["seed"],
             out["polls_per_bench"], out["seconds"]))
    print("  prints %d  lost %d  dup %d (labelled %d, unlabelled %d)  "
          "res_lost %d  res_wrong %d" % (t["prints"], t["lost"], t["dup"],
                                         t["labelled_dup"],
                                         t["unlabelled_dup"], t["res_lost"],
                                         t["res_wrong"]))
    print("  analyst overwritten %d (A5 injected %d)  conflicts %d  "
          "drained %d/%d" % (out["analyst_overwritten"], out["injected_a5"],
                             out["conflicts"], out["benches_drained"],
                             out["benches"]))
    print("  faults %s" % json.dumps(out["faults"], sort_keys=True))
    print("  restore %s  sync answers %s  resent %d  restarts %d"
          % (json.dumps(out["restore"]), json.dumps(out["sync_answers"]),
             out["records_resent"], t["restarts"]))
    print("  roads %s" % json.dumps(out["road_outcomes"], sort_keys=True))
    for f in out["failures"]:
        print("FAIL: " + f)
    for e in out["harness_errors"]:
        print("HARNESS: " + e)
    print("network attempts %d" % out["network_attempts"])


def main(argv=None):
    args = parse(argv if argv is not None else sys.argv[1:])
    try:
        out = run(args)
        if args.json:
            with open(args.json, "w") as f:
                json.dump(out, f, indent=1, default=str)
        report(out)
    except BaseException as e:              # noqa: B902 — a crash is never 1
        sys.stdout.write(traceback.format_exc()[-4000:])
        print("HARNESS: the soak crashed (%s: %s) — exit 2" % (type(e).__name__, e))
        return 2
    if out["harness_errors"]:
        return 2
    return 1 if out["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
