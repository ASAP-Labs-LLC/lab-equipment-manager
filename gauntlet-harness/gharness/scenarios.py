"""Every scenario of transfer spec §9, as code.

Three kinds:

* **baseline** (29): phase 1's `run_faults.py` functions, called UNCHANGED with
  `run_faults.Ctx` pointed at World. Their phase-1 columns must reproduce
  `baseline/faults.json` exactly on v3.9.0.
* **new, runnable anywhere**: file shapes (X1–X4, R7p, R6deep, Q1), the
  analyst (A1, A3, A4, A5), per-index errors (B1), economy (E0–E3), legacy
  projection (L1, L2), kills at named points (K1b, K7b, K8r), and the road
  scenarios that need only HServer (T1, T2, D1). They run on v3.9 too and say
  what today does — which is the point of a gate: the same scenario, two
  answers.
* **new, needing a v4 capability that has no code yet** (journal surgery,
  checkpoint, adoption, bridge, replica, store transactions): the body raises
  `Unsupported` naming exactly what is missing. That is a FAIL, never a skip,
  and the message says which piece lands it.

A scenario returns a dict of measurements; gate.py compares it with
expectations.json.
"""
import os
from datetime import timedelta

from .world import Unsupported

WEB_VOLATILE = {("W4", "state", "filled_at")}


class Registry:
    def __init__(self):
        self.order = []
        self.fns = {}
        self.kind = {}

    def add(self, sid, fn, kind):
        if sid in self.fns:
            raise ValueError("duplicate scenario id " + sid)
        self.order.append(sid)
        self.fns[sid] = fn
        self.kind[sid] = kind


def build(rf, rw, lh, W, mod, GateGateway, server_factory):
    """Bind every scenario to the loaded target. Returns a Registry."""
    reg = Registry()
    lab_id, print_line = lh.lab_id, lh.print_line

    # ── baseline: phase-1 functions, unchanged ──────────────────────────────
    for fn in rf.R:
        sid = _baseline_id(fn.__name__)
        reg.add(sid, (lambda f: lambda: f())(fn), "baseline")

    def new(sid, kind="new"):
        def deco(fn):
            reg.add(sid, fn, kind)
            return fn
        return deco

    # ── kills at named points (§3.3) ────────────────────────────────────────
    @new("K1b")
    def k1b():
        c = W()
        for _ in range(4):
            c.emit(3); c.poll()
        c.emit(3)
        c.kill_at("after_journal_before_cursor")
        c.poll()
        c.assert_killed("after_journal_before_cursor")
        for _ in range(4):
            c.emit(3); c.poll()
        c.settle()
        return c.tally("K1b single_csv", "kill after journal fsync, before cursor save")

    @new("K7b")
    def k7b():
        c = W("multi_csv")
        for _ in range(4):
            c.emit(3); c.poll()
        c.emit(3)
        c.kill_at("after_journal_before_cursor")
        c.poll()
        c.assert_killed("after_journal_before_cursor")
        c.settle()
        return c.tally("K7b multi_csv", "kill after journal, before the move to processed/")

    @new("K8r")
    def k8r():
        c = W("serial")
        for _ in range(4):
            c.emit(3); c.poll()
        c.emit(3)
        c.kill_at("serial_frame_complete_before_fsync")
        c.poll()
        c.assert_killed("serial_frame_complete_before_fsync")
        c.settle()
        return c.tally("K8r serial", "kill between a frame completing and its fsync")

    # ── power loss mid-append (§3.1, §9.2 T5) ───────────────────────────────
    @new("T5")
    def t5():
        """The process dies after the journal write and before its fsync, and
        the power cut leaves only part of the last line on disk. The restart
        must cut the torn line off (and keep it aside), re-deliver what the
        surviving lines hold, and read the torn reading again from the file:
        0 lost, 0 doubled."""
        c = W()
        for _ in range(4):
            c.emit(3); c.poll()
        c.emit(3)
        c.kill_at("after_journal_before_fsync")
        c.before_next_restart(c.tear_journal_tail)
        c.poll()
        c.assert_killed("after_journal_before_fsync")
        if not c.torn:
            raise RuntimeError("T5 tore nothing — it measured nothing")
        for _ in range(2):
            c.emit(3); c.poll()
        c.settle()
        t = c.tally("T5 single_csv", "power loss mid-append: the last journal line torn")
        t.update(c.journal_check())
        return t

    # ── file shapes (§4) ────────────────────────────────────────────────────
    def line(lab, val):
        return "%s,%s\n" % (lab, val)

    @new("X1")
    def x1():
        c = W()
        L = [(lab_id(i), "0.%04d" % (8000 + i)) for i in range(3)]
        for lab, val in (L[0], L[1], L[2], L[1]):     # L1 genuinely printed twice
            c.emit_line(lab, val)
        c.poll()
        c.rewrite([line(*L[2]), line(*L[1])])          # in-place trim to [L2, L1]
        c.emit_line(*L[1])                             # and L1 a third time
        c.poll()
        c.settle()
        t = c.tally("X1 single_csv", "[L0,L1,L2,L1] trimmed in place to [L2,L1], plus genuine L1")
        t["rows_lab1"] = sum(1 for r in c.stored_rows() if r["lab_id"] == L[1][0])
        return t

    @new("X2")
    def x2():
        c = W()
        qc = [("QC-STD-%d" % k, "0.%04d" % (8100 + k)) for k in range(3)]
        # Logged in as samples so the results road files them like any print:
        # X2 is about the LOG, and a never-fileable id would add 3 res_lost
        # that say nothing about rotation.
        c.gw.seed_samples([q[0] for q in qc])
        for lab, val in qc:
            c.emit_line(lab, val)
        c.poll()
        c.emit(3); c.poll()
        # rotation: the new file repeats the 3 QC lines — printed again for real
        for lab, val in qc:
            c.emit_line(lab, val, write=False)
        c.rotate([line(*q) for q in qc])
        c.poll()
        c.settle()
        return c.tally("X2 single_csv", "rotation: the new file repeats 3 earlier lines")

    def _x3(via_temp):
        c = W()
        c.emit(6); c.poll()
        tail = c.lines()[-3:]
        new_lines = []
        if not via_temp:
            for l in tail:                              # genuinely printed again
                lab, val = l.strip().split(",")
                c.emit_line(lab, val, write=False)
        new_lines += tail
        for _ in range(2):
            i = c.n; c.n += 1
            lab, val = print_line(i).strip().split(",")
            c.emit_line(lab, val, write=False)
            new_lines.append(line(lab, val))
        c.rotate(new_lines, via_temp=via_temp)
        c.poll()
        c.settle()
        return c

    @new("X3")
    def x3():
        c = _x3(via_temp=False)
        return c.tally("X3 single_csv", "rotation whose first 3 lines equal the old file's last 3, plus 2 new")

    @new("X3r")
    def x3r():
        c = _x3(via_temp=True)
        return c.tally("X3r single_csv", "same bytes as X3; truth is a temp-file-plus-rename trim")

    @new("X4")
    def x4():
        c = W()
        c.emit(3); c.poll()
        c.emit(2)                                       # appended, not yet polled
        new_lines = []
        for _ in range(2):
            i = c.n; c.n += 1
            lab, val = print_line(i).strip().split(",")
            c.emit_line(lab, val, write=False)
            new_lines.append(line(lab, val))
        c.rotate(new_lines)
        c.poll()
        c.settle()
        return c.tally("X4 single_csv", "2 lines appended to the old file, then renamed; new file starts")

    @new("R7p")
    def r7p():
        c = W()
        P = ("QC-P", "0.8500")
        c.gw.seed_samples([P[0]])                       # fileable; see X2
        for _ in range(3):
            c.emit_line(*P)
        c.poll()
        c.rewrite([line(*P), line(*P)])                 # head trimmed in place
        c.emit_line(*P)                                 # identical re-print: same bytes
        c.poll()
        c.settle()
        return c.tally("R7p single_csv", "periodic identical lines: head trim plus identical re-print")

    @new("R6deep")
    def r6deep():
        c = W()
        for _ in range(10):
            c.emit(20); c.poll()
        lines = c.lines()
        k = 100
        lab, val = lines[k].strip().split(",")
        corrected = val.replace("0.8", "0.9", 1)
        assert len(corrected) == len(val)
        lines[k] = line(lab, corrected)
        c.printed[lab] = corrected                      # the correction is a genuine print
        c.rewrite(lines)
        for _ in range(30):                             # 15 minutes of polls
            c.poll()
        return c.tally("R6deep single_csv", "same-size edit outside the head and tail windows")

    @new("Q1")
    def q1():
        c = W()
        for _ in range(5):
            c.emit(3); c.poll()
        old = c.lines()
        i = c.n; c.n += 1
        new_line = print_line(i)
        c.printed[new_line.split(",")[0]] = new_line.strip().split(",")[1]
        c.rewrite(old[:7])                              # mid-rewrite: a prefix
        c.poll()
        c.rewrite(old + [new_line])                     # the rewrite completes
        c.poll()
        c.settle()
        return c.tally("Q1 single_csv", "a mid-rewrite poll sees a prefix of the history")

    # ── the analyst (§8) ────────────────────────────────────────────────────
    @new("A1")
    def a1():
        c = W()
        c.emit(5); c.poll()
        for k in range(5):
            c.analyst_edit(lab_id(k), "0.7%03d" % k)
        c.restart()
        c.settle()
        return c.tally("A1 single_csv", "analyst corrects 5 filed cells; clean restart")

    @new("A3")
    def a3():
        c = W("serial")
        held = [lab_id(3), lab_id(4), lab_id(5)]
        c.gw.fake.sql("DELETE FROM samples WHERE lab_id IN (?,?,?)", held)
        c.emit(3); c.poll()
        c.emit(3); c.poll()                             # 3..5 held at the bench
        c.gw.seed_samples(held)                         # the sample is logged in...
        for k, lab in enumerate(held):
            c.analyst_edit(lab, "0.6%03d" % k)          # ...and the analyst types the cell
        c.settle(polls=10)
        return c.tally("A3 serial", "analyst types the cell while the bench holds the reading")

    @new("A4")
    def a4():
        c = W()
        c.emit(3); c.poll()
        lab = lab_id(1)
        c.emit_line(lab, "0.8888")                      # the instrument re-ran the sample
        c.poll()
        c.settle()
        return c.tally("A4 single_csv", "instrument re-run of a LEM-filed cell")

    @new("A5")
    def a5():
        c = W(batch_mode="labcore")
        c.emit(3); c.poll()
        c.emit(3)
        c.inject_between_read_and_batch(lab_id(3), "0.5555")
        c.poll()
        if not c.gw.injected:
            raise RuntimeError("the A5 injection never fired: no read was followed "
                               "by a batch carrying the cell — measured nothing")
        c.settle()
        return c.tally("A5 single_csv", "analyst edit between the guard read and the batch")

    @new("B1")
    def b1():
        c = W(batch_mode="labcore")
        bad = lab_id(1)
        c.gw.fail_index(bad, "Density")
        c.emit(3)
        for _ in range(8):
            c.poll()
        t = c.tally("B1 single_csv", "a per-index error inside an ok batch")
        t["failing_cell_sends"] = sum(n for (l, tn, v), n in c.gw.cell_sends.items()
                                      if l == bad and tn == "Density")
        t["failing_cell_errors"] = c.gw.per_index_errors[(bad, "Density")]
        return t

    # ── economy (§9.2 E0–E3; phase 1's run_economy.py, re-run here) ─────────
    reg.economy_runs = {}

    def economy(source_type, prints, roads=None):
        key = (source_type, prints, roads)
        if key not in reg.economy_runs:
            reg.economy_runs[key] = _economy_run(GateGateway, server_factory, mod, lh,
                                                 source_type, prints, roads)
        return reg.economy_runs[key]

    @new("E0")
    def e0():
        r = economy("single_csv", 0)
        return {"idle_labcore_ops": r["steady_ops"],
                "idle_labcore_ops_per_min": round(r["steady_ops"] / r["steady_minutes"], 3),
                "v2_syncs": r["v2_syncs"]}

    @new("E1")
    def e1():
        r = economy("single_csv", 1)
        idle = economy("single_csv", 0)
        polls = r["steady_polls"]
        return {"labcore_ops_per_filing_poll": round(r["steady_ops"] / polls, 4),
                "extra_ops_per_filing_poll": round((r["steady_ops"] - idle["steady_ops"]) / polls, 4),
                "v2_syncs": r["v2_syncs"]}

    @new("E2")
    def e2():
        r = economy("single_csv", 0)
        return {"first_poll_reads": r["first_poll_split"]["read"],
                "first_poll_writes": r["first_poll_split"]["write"]}

    @new("E3")
    def e3():
        r = economy("single_csv", 0, roads="404")
        return {"idle_reads_per_min": r["steady_split_per_min"]["read"]}

    # ── legacy projection (v4 bench against a server that answers 404) ──────
    @new("L1")
    def l1():
        c = W()
        if c.server:
            c.server.set_roads("404")
        c.emit(3); c.poll()
        c.emit(3)
        c.gw.plan = rf.nth("sql:machine_log", 1, "raise_after")
        c.poll()
        c.settle()
        return c.tally("L1 single_csv", "legacy projection: N3's shape")

    @new("L2")
    def l2():
        c = W()
        if c.server:
            c.server.set_roads("404")
        c.emit(3); c.poll()
        lab = lab_id(10)
        c.emit_line(lab, "0.8010")
        c.emit_line(lab, "0.8010")                      # printed twice, one poll
        c.poll()
        c.settle()
        return c.tally("L2 single_csv", "legacy projection: two genuine prints of one lab_id and test in one poll")

    # ── roads (§6.2) ────────────────────────────────────────────────────────
    @new("T1")
    def t1():
        c = W()
        c.server.set_road("A", "drop_before")           # LAN dark: silently dropped
        for _ in range(10):
            c.emit(3); c.poll()
        c.settle()
        return c.tally("T1 single_csv", "LAN road dark throughout")

    @new("T2")
    def t2():
        c = W()
        c.server.set_roads("down")
        for _ in range(240):
            c.emit(1); c.poll()
        c.server.set_roads("up")
        c.settle(polls=10)
        return c.tally("T2 single_csv", "LEM down 2 h, 240 prints; results keep filing")

    @new("D1")
    def d1():
        c = W(n_samples=5000)                           # 4,803 prints, all fileable
        c.server.set_roads("down")
        for _ in range(480):
            c.emit(10); c.poll()
        c.server.set_roads("up")
        drained = None
        for k in range(1, 11):
            c.poll()
            if drained is None and _drained(c):
                drained = k
        t = c.tally("D1 single_csv", "both roads down 4 h, 4,800 prints")
        t["polls_to_drain"] = drained
        return t

    # ── needs a v4 capability with no code yet ─────────────────────────────
    for sid, needs in V4_ONLY.items():
        reg.add(sid, (lambda s, n: lambda: _unsupported(s, n))(sid, needs), "new")

    # ── web server (phase 1's run_web.py) ──────────────────────────────────
    def web(name, fn):
        def run():
            if _has_store():
                raise Unsupported(
                    "%s on a server with a LEM store is measured by the store-side "
                    "version of this scenario, which lands with P6/P7" % name)
            return fn()
        return run
    reg.add("W1", web("W1", rw.w1), "web")
    reg.add("W2", web("W2", rw.w2), "web")
    reg.add("W3", web("W3", rw.w3), "web")
    reg.add("W4", web("W4", rw.w4), "web")
    reg.add("W2b", lambda: _unsupported("W2b", V4_WEB["W2b"]), "web")
    return reg


V4_ONLY = {
    "A1w": "journal wipe + /checkpoint blind mode (P1, P7, P8)",
    "T3": "store restored from a backup: a 409 cursor answer from /api/v2 sync (P7, P11)",
    "T4": "journal deletion with the server reachable: /checkpoint (P1, P7, P8)",
    "T4b": "journal deleted with both roads down: blind mode (P1, P8)",
    "CF1": "the LabCore factor replica, `config_rev:<uid>` in lem_meta (P8, P9)",
    "CF2": "a factor change while both roads are dark: replica + held readings (P8, P9)",
    "U1": "adoption at the first v4 start (P4)",
    "U2": "adoption: `recovered` rows (P4)",
    "U3": "adoption: raw-value matching across a factor change (P4)",
    "U4": "adoption: the pre-history summary record (P4)",
    "U5": "adoption under a v3.9 server, through indexed LabCore reads (P4, P5)",
    "M1": "order matrix pairing 1 (§12.2; P13)",
    "M2": "order matrix pairing 2 (§12.2; P13)",
    "M3": "order matrix pairing 3 (§12.2; P13)",
    "M4": "order matrix pairing 4 (§12.2; P13)",
    "M5": "order matrix pairing 5 (§12.2; P13)",
    "M6": "order matrix pairing 6 (§12.2; P13)",
    "DG1": "module downgrade with the bridge on (P9)",
    "DG2": "server rollback after v4 benches synced: projection (P9)",
}
V4_WEB = {"W2b": "server INSERTs on the LEM store (P6)"}


def _unsupported(sid, needs):
    raise Unsupported("%s needs %s — not present on this target" % (sid, needs))


def _has_store():
    from .servers import find_store_gateway
    return find_store_gateway() is not None


def _drained(c):
    from .ledger import tally
    t = tally(c.ledger.truth(), c.stored_rows())
    return t["lost"] == 0


def _baseline_id(fn_name):
    names = {
        "s00_no_fault": "S0", "k1_kill_before_log_write": "K1",
        "k2_kill_between_log_batches": "K2", "k3_kill_after_log_before_results": "K3",
        "k4_kill_after_results_write_before_marker": "K4",
        "k5_restart_marker_published_midway": "K5", "k6_clean_restart_never_saved": "K6",
        "k7_multi_kill_after_move": "K7", "k8_serial_kill_before_log": "K8",
        "k9_kill_with_held_rows": "K9", "k9c_held_control": "K9c",
        "k9r_held_restart_after_mirror": "K9r", "n1_labcore_unreachable": "N1",
        "n2_log_insert_raise_before": "N2", "n3_log_insert_raise_after": "N3",
        "n4_results_batch_raise_after": "N4", "n5_identity_read_timeout": "N5",
        "n6_everything_raises_10_polls": "N6", "f1_log_refused_once": "F1",
        "f2_results_refused_once": "F2", "f3_all_refused_20_polls": "F3",
        "f4_all_refused_60_polls": "F4", "f5_writes_refused_reads_ok_60_polls": "F5",
        "r1_rewrite_same_prefix_plus_one": "R1", "r2_rewrite_newest_first": "R2",
        "r3_rotate_keep_last_5": "R3", "r4_restart_then_reread_rewritten": "R4",
        "r5_partial_line_at_poll": "R5", "r6_inplace_correction_same_size": "R6",
    }
    if fn_name not in names:
        raise KeyError("baseline scenario %r has no gate id — run_faults.py changed?" % fn_name)
    return names[fn_name]


def _economy_run(GateGateway, server_factory, mod, lh, source_type, prints, roads,
                 minutes=60, warm_minutes=5):
    """`run_economy.run`, line for line (that file runs itself at import and
    writes ../economy.json relative to the cwd, so it cannot be imported).
    The only differences: fresh temp dirs, an HServer on the roads (whose
    mode `roads` can set), and two extra totals at the end."""
    import tempfile
    from types import SimpleNamespace
    from datetime import datetime
    from .world import fresh_world_dirs
    from .hserver import HServer
    fresh_world_dirs()
    d = tempfile.mkdtemp(); path = os.path.join(d, "f.csv"); open(path, "w").close()
    gw = GateGateway(); gw.seed_samples([lh.lab_id(i) for i in range(5000)])
    holder = SimpleNamespace(gw=gw)
    server = HServer(lambda: server_factory(holder)).install()
    if roads:
        server.set_roads(roads)
    t0 = datetime(2026, 10, 1, 9, 0, 0)
    m = lh.new_bench(gw, lh.density_machine("b1", path, source_type=source_type))
    POLL = 30
    bind = gw.snapshot()
    n = 0
    first = warm = None
    steps = int(minutes * 60 / POLL)
    for k in range(steps):
        now = t0 + timedelta(seconds=k * POLL)
        if source_type == "single_csv" and prints:
            with open(path, "a") as f:
                for _ in range(prints):
                    f.write(lh.print_line(n)); n += 1
        lh.poll(m, now)
        if k % (mod.HEARTBEAT_SECONDS // POLL) == 0 and k:
            m._send_pulse(now)
        if k == 0:
            first = gw.snapshot() - bind
        if k == int(warm_minutes * 60 / POLL) - 1:
            warm = gw.snapshot()
    steady = gw.snapshot() - warm
    steady_min = minutes - warm_minutes

    def split(c):
        out = {"read": 0, "write": 0}
        for key, v in c.items():
            out["read" if key.startswith("read") else "write"] += v
        return out
    return {"source_type": source_type, "prints_per_poll": prints,
            "poll_s": POLL, "bind_ops": dict(bind), "first_poll_ops": dict(first),
            "first_poll_split": split(first),
            "steady_minutes": steady_min,
            "steady_per_min": {k: round(v / steady_min, 3) for k, v in sorted(steady.items())},
            "steady_split_per_min": {k: round(v / steady_min, 3) for k, v in split(steady).items()},
            "prints": n, "log_rows": sum(gw.log_runs().values()),
            "steady_ops": sum(steady.values()),
            "steady_polls": steps - int(warm_minutes * 60 / POLL),
            "v2_syncs": server.v2_syncs()}
