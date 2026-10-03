"""M1 and M4 of the order matrix (transfer §12.2; piece T-P13).

  M1  v3.9 server, v3.9 bench: today. Its gate is "baseline reproduced
      exactly" — phase 1's faults.json, web.json and economy.json, on the
      TAGGED v3.9.0 code. One process cannot import two versions of the
      module, so this is the gate's own v3.9 drift run (a subprocess): when
      gate.py already ran it for this invocation it hands the results over
      (`LEM_GATE_DRIFT_JSON`); otherwise M1 runs it. M1 reports what that run
      found, never a constant: how many of the 29 baseline scenarios and the
      four web rows reproduced, whether economy.json did, and the ledger
      lost/dup of the control (S0) — the only run in today's pairing with no
      fault in it, so the only one whose answer is "nothing lost, nothing
      doubled".

  M4  v4 server, v4 bench: one bench life across every hand-over the bench
      and server make between them — enrolment, the LAN going dark and the
      public road taking over, a sync answered but the answer lost, a 503,
      a kill between the journal and the cursor, the server process
      restarting, a clean LabStation restart, and an analyst's edit followed
      by a restart. Every print once in the store, every cell filed once,
      the analyst's value kept, the bench never falls back to LabCore, and
      neither the bench nor the server sends a single `lem_*` statement to
      LabCore ("v2 sync; no lem_* LabCore traffic").
"""
import json
import os
import subprocess
import sys

from .world import Unsupported

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: LabCore op categories (phase 1's `categorize`) that are LEM's own tables.
#: A v2 bench sends none of them: its record goes to LEM (§2, §12.2 M4).
LEM_CATEGORIES = ("DDL", "heartbeat", "machine_log", "status", "substatus",
                  "effective_specs", "held", "config", "override",
                  "qc_samples", "qc_targets", "qc_specs", "maintenance",
                  "corrections", "lem_meta", "lem_other",
                  "calibration_epoch", "last_qc")

BASELINE_29 = ("S0 K1 K2 K3 K4 K5 K6 K7 K8 K9 K9c K9r N1 N2 N3 N4 N5 N6 F1 "
               "F2 F3 F4 F5 R1 R2 R3 R4 R5 R6").split()
WEB_4 = ("W1", "W2", "W3", "W4")


def is_lem_category(cat):
    return cat.split(":", 1)[-1] in LEM_CATEGORIES


# ── M1 ──────────────────────────────────────────────────────────────────────

def drift_results(tmp_root):
    """The v3.9 drift run's results: the one gate.py made for this
    invocation, else a fresh one. A run that wrote no results, or whose exit
    status contradicts them, raises — "not reproduced" is a statement about
    v3.9, a broken run is not."""
    path = os.environ.get("LEM_GATE_DRIFT_JSON")
    rc = None
    if not (path and os.path.exists(path)):
        path = os.path.join(tmp_root, "m1-drift.json")
        p = subprocess.run([sys.executable, os.path.join(HERE, "gate.py"),
                            "--target", "v3.9", "--drift-only", "--quiet",
                            "--tmp", tmp_root, "--json", path],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        rc = p.returncode
        if not os.path.exists(path):
            raise RuntimeError("M1: the v3.9 drift run wrote no results (exit "
                               "%d): %s" % (rc, p.stdout.decode(
                                   errors="replace")[-800:]))
    with open(path, encoding="utf-8") as f:
        res = json.load(f)
    if rc is not None:
        implied = 2 if (res.get("harness_errors") or res.get("drifted")
                        or res.get("economy_drift")) else 0
        if rc != implied:
            raise RuntimeError("M1: the v3.9 drift run exited %d but its "
                               "results imply %d" % (rc, implied))
    return res


def m1(tmp_root):
    res = drift_results(tmp_root)
    if res.get("target") != "v3.9":
        raise RuntimeError("M1: the drift results are for %r, not v3.9"
                           % res.get("target"))
    if res.get("harness_errors"):
        raise RuntimeError("M1: the v3.9 drift run is not trustworthy: %s"
                           % "; ".join(res["harness_errors"])[:600])
    sc = res.get("scenarios") or {}

    def held(sid):
        r = sc.get(sid)
        return r is not None and r.get("status") == "ran" and not r.get("drift")
    s0 = (sc.get("S0") or {}).get("measured") or {}
    return {"lost": s0.get("lost"), "effective_dup": s0.get("dup"),
            "baseline_reproduced": sum(1 for s in BASELINE_29 if held(s)),
            "web_reproduced": sum(1 for s in WEB_4 if held(s)),
            "economy_reproduced": not res.get("economy_drift"),
            "not_reproduced": sorted(s for s in BASELINE_29 + list(WEB_4)
                                     if not held(s)),
            "drift_target_code": res.get("code_root")}


# ── M4 ──────────────────────────────────────────────────────────────────────

def m4(W, rf, mod, lab_id):
    if not hasattr(mod, "BenchUploader"):
        raise Unsupported("M4 needs a v4 bench (journal, uploader: P1, P8)")
    c = W()
    if c.server is None:
        raise Unsupported("M4 needs the harness server")
    c.server.app                      # the v4 server is up before the bench
    boots = {"n": 1}
    factory = c.server._factory

    def counted_factory():
        boots["n"] += 1
        return factory()
    modes = [_mode(c)]
    bench_mark = len(c.gw.trace)
    srv_lab = c.server_labcore
    srv_mark = srv_lab.lem_sql

    def poll(n=3):
        c.emit(n)
        c.poll()
        modes.append(_mode(c))

    poll(); poll()
    # 1. the LAN goes dark; the public road carries the bench
    c.server.set_road("A", "drop_before")
    poll(); poll()
    c.server.set_road("A", "up")
    poll()
    # 2. a sync executed, its answer lost (N3's v2 shape), on both roads
    c.server.set_roads("lose_response", times=1)
    poll()
    # 3. LEM answers 503 once
    c.server.set_roads("503", times=1)
    poll()
    # 4. LabStation killed between the journal and the cursor
    c.kill_at("after_journal_before_cursor")
    poll()
    c.assert_killed("after_journal_before_cursor")
    poll()
    # 5. the server process restarts (no restore: its store is as it was)
    c.server.reboot()
    c.server._factory = counted_factory
    srv_lab_before_reboot = srv_lab.lem_sql - srv_mark
    poll()
    # 6. a clean LabStation restart
    c.restart()
    poll()
    # 7. an analyst corrects a cell the bench filed, then LabStation restarts
    c.analyst_edit(lab_id(0), "0.7000")
    c.restart()
    poll()
    c.settle()
    modes.append(_mode(c))
    t = c.tally("M4 single_csv", "v4 server, v4 bench: roads, N3, 503, kill, "
                                 "server restart, restart, A1")
    bench_lem = sum(1 for cat in c.gw.trace[bench_mark:]
                    if is_lem_category(cat))
    server_lem = srv_lab_before_reboot + c.server_labcore.lem_sql
    return {"lost": t["lost"], "effective_dup": t["dup"],
            "res_lost": t["res_lost"], "res_wrong": t["res_wrong"],
            "cell_dup_lands": t["cell_dup_lands"],
            "analyst_overwritten": t["analyst_overwritten"],
            "bench_lem_ops_to_labcore": bench_lem,
            "server_lem_sql_to_labcore": server_lem,
            "modes_seen": sorted(set(m for m in modes if m)),
            "roads_used": t["roads_used"], "store": c.store_kind(),
            # Each hand-over is shown to have happened, not assumed: the
            # requests the dark LAN dropped, the answers lost after LEM
            # executed them, the 503s, the kill, the server's second boot.
            "handovers": {
                "lan_dropped": _outcome(c, "A", "drop_before"),
                "answers_lost": sum(_outcome(c, r, "lose_response")
                                    for r in ("A", "B")),
                "answered_503": sum(_outcome(c, r, "503") for r in ("A", "B")),
                "server_boots": boots["n"]},
            "kills": t["kills"], "restarts": t["restarts"],
            "printed": t["printed"], "truth_prints": t["truth_prints"],
            "lem_requests_on_poll_thread": t["lem_requests_on_poll_thread"],
            "uploader_errors": t["uploader_errors"]}


def _outcome(c, road, mode):
    return c.server.outcomes.get((road, mode), 0)


def _mode(c):
    st = getattr(c.m, "_transfer", None)
    return getattr(st, "mode", None)
