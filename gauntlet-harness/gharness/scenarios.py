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

    # ── the legacy road keeps its guards (v4 ships first on it, D8) ─────────
    # A v2 bench no longer writes its machine log into LabCore, so phase 1's
    # log-refusal scenarios (F1, N2) now measure the v2 sync — and the
    # LabCore log drain that a v4 bench still uses against an OLD server went
    # unexercised (two P0 mutations of it survived). F1L and N2L run the very
    # same phase-1 functions with the server answering 404 from the start:
    # the bench never speaks v2, and the old road must still lose nothing.
    def on_legacy_road(fn):
        def run():
            keep = W.default_road_modes
            W.default_road_modes = {"A": "404", "B": "404"}
            try:
                return fn()
            finally:
                W.default_road_modes = keep
        return run

    def new(sid, kind="new"):
        def deco(fn):
            reg.add(sid, fn, kind)
            return fn
        return deco

    reg.add("F1L", on_legacy_road(rf.f1_log_refused_once), "new")
    reg.add("N2L", on_legacy_road(rf.n2_log_insert_raise_before), "new")

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

    @new("A1j")
    def a1j():
        """A1 with the bench journal LOST before the restart: the guard read,
        alone, has to protect the analyst. Not a spec row — the spec's A1w
        also demands 0 re-sent records, which needs /checkpoint (P8) — but
        the half of it that is the results road's: with the journal (and so
        LEM's ledger of what it filed) gone, the cell holding a value LEM
        cannot vouch for must be a conflict, never overwritten. A1 alone can
        not show this: there the journal suppresses the re-read before the
        guard is ever asked, so `guard_off` leaves A1 green."""
        import shutil
        c = W()
        c.emit(5); c.poll()
        for k in range(5):
            c.analyst_edit(lab_id(k), "0.7%03d" % k)
        c.before_next_restart(
            lambda: shutil.rmtree(c.journal_dir(), ignore_errors=True))
        c.restart()
        c.settle()
        return c.tally("A1j single_csv",
                       "analyst corrects 5 filed cells; journal lost; restart")

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
        # "0 filed for that cell" (§9.2) as the bench's own record says it,
        # not only as the empty cell implies it.
        t["failing_cell_filed"] = c.filed_cells(bad)
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
                "v2_syncs": r["v2_syncs"],
                "polls": r["steady_polls"],
                "lem_requests_on_poll_thread": r["lem_requests_on_poll_thread"]}

    @new("E1")
    def e1():
        r = economy("single_csv", 1)
        idle = economy("single_csv", 0)
        polls = r["steady_polls"]
        # The results road's own share, by category: the identity/guard read
        # (`read:identity` — it reads "samples"), the key lookup on
        # sample_tests for a cached sample, and the batch. The rest of
        # "extra" is the machine-log row, which v2 mode sends to LEM (P8).
        road = sum(n for cat, n in r["steady_by_cat"].items()
                   if cat == "read:identity" or cat == "write:batch"
                   or (cat.startswith("read:") and "SAMPLE_TESTS" in cat.upper()))
        return {"labcore_ops_per_filing_poll": round(r["steady_ops"] / polls, 4),
                "extra_ops_per_filing_poll": round((r["steady_ops"] - idle["steady_ops"]) / polls, 4),
                "results_road_ops_per_filing_poll": round(road / polls, 4),
                "v2_syncs": r["v2_syncs"],
                "prints": r["prints"], "lem_run_rows": r["lem_run_rows"],
                "cells_filed": r["cells_filed"]}

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
        # An old server from before the bench exists: it never spoke v2.
        c = W(road_modes={"A": "404", "B": "404"})
        c.emit(3); c.poll()
        c.emit(3)
        c.gw.plan = rf.nth("sql:machine_log", 1, "raise_after")
        c.poll()
        c.settle()
        return c.tally("L1 single_csv", "legacy projection: N3's shape")

    @new("L2")
    def l2():
        # An old server from before the bench exists: it never spoke v2.
        c = W(road_modes={"A": "404", "B": "404"})
        c.emit(3); c.poll()
        lab = lab_id(10)
        c.emit_line(lab, "0.8010")
        c.emit_line(lab, "0.8010")                      # printed twice, one poll
        c.poll()
        c.settle()
        return c.tally("L2 single_csv", "legacy projection: two genuine prints of one lab_id and test in one poll")

    # ── roads (§6.2) ────────────────────────────────────────────────────────
    v2_target = hasattr(mod, "BenchUploader")

    def need_v2(sid, what):
        if not v2_target:
            _unsupported(sid, what)

    def cells_for(c, labs):
        res = c.gw.results("Density")
        return sum(1 for lab in labs if res.get(lab) not in (None, ""))

    def last_heard(c):
        """When the bench last heard from LEM before the roads went dark:
        the previous poll's sync, or its bind if it has not polled yet."""
        return c.now - rf.POLL if c.k else c.now

    def unconfirmed(c, heard):
        """True for a print made now if the 60 s rule forbids filing it: the
        bench has not heard LEM confirm its factors for more than 60 s."""
        return (c.now - heard).total_seconds() > 60

    @new("T1")
    def t1():
        """The LAN is dark from before the bench exists, and the public road
        is Cloudflare: a request that does not name itself gets 1010. Every
        reading still arrives — over the public road, because the bench says
        who it is."""
        c = W(road_modes={"A": "drop_before", "B": "1010-without-UA"})
        for _ in range(10):
            c.emit(3); c.poll()
        c.settle()
        t = c.tally("T1 single_csv", "LAN road dark throughout")
        t["default_ua_gets_1010"] = _default_ua_refused(c.server)
        return t

    @new("T2")
    def t2():
        """D2: both LEM roads dark for 2 h while the bench prints 240 times.
        Results are HELD in the journal (no replica, no unconfirmed factor),
        then filed — none lost — once a road returns."""
        c = W()
        heard = last_heard(c)
        c.server.set_roads("down")
        dark = []
        for _ in range(240):
            labs = [lab for _i, lab in _labs(c.emit(1))]
            if unconfirmed(c, heard):
                dark += labs
            c.poll()
        filed_dark = cells_for(c, dark)
        c.server.set_roads("up")
        c.settle(polls=10)
        # (The "what" text is phase 1's, kept so the v3.9 row reproduces;
        # under D2 v4 holds the results and files them after — see v4_spec.)
        t = c.tally("T2 single_csv", "LEM down 2 h, 240 prints; results keep filing")
        t["filed_while_dark"] = filed_dark
        return t

    @new("D1")
    def d1():
        c = W(n_samples=5000)                           # 4,803 prints, all fileable
        heard = last_heard(c)
        c.server.set_roads("down")
        dark = []
        for _ in range(480):
            labs = [lab for _i, lab in _labs(c.emit(10))]
            if unconfirmed(c, heard):
                dark += labs
            c.poll()
        filed_dark = cells_for(c, dark)
        c.server.set_roads("up")
        drained = None
        filed = None
        for k in range(1, 11):
            c.poll()
            if drained is None and _drained(c):
                drained = k
            if filed is None and _filed(c):
                filed = k
        t = c.tally("D1 single_csv", "both roads down 4 h, 4,800 prints")
        t["polls_to_drain"] = drained
        t["filed_while_dark"] = filed_dark
        # D2 holds results while LEM is dark, so they file after the record
        # has drained, at the results road's own pace (2 identity reads a
        # poll): keep polling — v4 only, so the v3.9 row stays phase 1's —
        # and say how long it took.
        k = 10
        while v2_target and filed is None and k < 60:
            k += 1
            c.poll()
            if _filed(c):
                filed = k
        t["polls_to_file"] = filed
        t["res_lost_final"] = _res_lost(c)
        return t

    # ── journal wiped: blind mode and /checkpoint (§6.5) ────────────────────
    # Polls after the roads are up in which the bench asks to re-enrol and a
    # person approves it: the uploader may still be in its 300 s backoff from
    # the dark spell, so up to ten polls before it asks, then one to collect.
    AFTER_WIPE_POLLS = 12

    def _wiped(sid, dark_polls):
        need_v2(sid, "journal wipe + /checkpoint blind mode (P8)")
        c = W()
        for _ in range(5):
            c.emit(3); c.poll()
        c.settle(polls=2)
        ops_before = _results_ops(c)
        all_before = dict(c.gw.counts)
        c.before_next_restart(c.wipe_journal)
        if dark_polls:
            c.server.set_roads("down")
        c.restart()
        blind_reads = []
        for _ in range(dark_polls):
            c.emit(3); c.poll()
            blind_reads.append(c.m._transfer_blind())
        c.server.set_roads("up")
        approved = None
        for _ in range(AFTER_WIPE_POLLS):
            c.poll()           # the bench asks to re-enrol; a person approves
            if approved is None and c.m._transfer.enrol:
                approved = c.approve_reenrolment()
        for _ in range(4):
            c.emit(3); c.poll()
        c.settle()
        t = c.tally("%s single_csv" % sid, "journal wiped; roads %s"
                    % ("down %d polls" % dark_polls if dark_polls else "up"))
        t["records_resent"] = c.server.records_resent
        t["approved"] = approved
        t["blind_while_dark"] = all(blind_reads) if blind_reads else None
        t["blind_after"] = c.m._transfer_blind()
        t["_all_ops"] = _ops_since(c, all_before)
        return t, c, ops_before

    def _twin_ops(dark_polls):
        """The same bench, the same prints, nothing wiped: the results road's
        LabCore ops are what the wiped bench may spend and no more."""
        c = W()
        for _ in range(5):
            c.emit(3); c.poll()
        c.settle(polls=2)
        before = _results_ops(c)
        all_before = dict(c.gw.counts)
        if dark_polls:
            c.server.set_roads("down")
        c.restart()
        for _ in range(dark_polls):
            c.emit(3); c.poll()
        c.server.set_roads("up")
        for _ in range(AFTER_WIPE_POLLS):
            c.poll()
        for _ in range(4):
            c.emit(3); c.poll()
        c.settle()
        return _results_ops(c) - before, _ops_since(c, all_before)

    def _extra_ops(t, twin_all):
        """EVERY LabCore op the wiped bench spent beyond its twin, by kind —
        not only the results road's (round-1 critic: a narrowed metric hid a
        wiped bench's legacy-road heartbeat, DDL, status and log writes).
        The one exception is §6.4's: the re-enrolment proves itself with the
        shared token, one read of lem_meta, reported on its own."""
        mine = t.pop("_all_ops")
        extra = {k: n - twin_all.get(k, 0) for k, n in mine.items()
                 if n - twin_all.get(k, 0) > 0}
        t["enrol_token_reads"] = extra.pop("read:lem_meta", 0)
        t["extra_labcore_ops"] = sum(extra.values())
        t["extra_labcore_kinds"] = sorted(extra)

    @new("T4")
    def t4():
        t, c, before = _wiped("T4", 0)
        twin, twin_all = _twin_ops(0)
        t["extra_guard_ops"] = (_results_ops(c) - before) - twin
        _extra_ops(t, twin_all)
        return t

    @new("T4b")
    def t4b():
        t, c, before = _wiped("T4b", 3)
        twin, twin_all = _twin_ops(3)
        t["extra_guard_ops"] = (_results_ops(c) - before) - twin
        _extra_ops(t, twin_all)
        return t

    # ── the 60 s factor rule, confirmed by the sync (§6.6; D2: no replica) ──
    @new("CF1")
    def cf1():
        """Both roads dark, LabCore up. The confirmation ages out (4 polls
        with nothing printed), then 12 prints: none may be filed with a factor
        nobody confirmed in 60 s. When a road returns they are filed, each
        with a factor confirmed ≤ 60 s before it was filed."""
        need_v2("CF1", "the 60 s factor rule via the sync's config_rev (P8)")
        c = W()
        c.emit(3); c.poll(); c.poll()
        heard = last_heard(c)
        c.server.set_roads("down")
        for _ in range(4):
            c.poll()
        dark = []
        for _ in range(6):
            labs = [lab for _i, lab in _labs(c.emit(2))]
            if not unconfirmed(c, heard):
                raise RuntimeError("CF1 printed inside the 60 s window")
            dark += labs
            c.poll()
        filed_dark = cells_for(c, dark)
        c.server.set_roads("up")
        c.settle(polls=12)
        t = c.tally("CF1 single_csv", "both roads dark, LabCore up: results held, then filed")
        ages = list(getattr(c.m, "_factor_ages", []) or [])
        t["filed_while_dark"] = filed_dark
        t["factor_confirmed_within_s"] = max(ages) if ages else None
        t["filings"] = len(ages)
        return t

    @new("CF2")
    def cf2():
        """The factor changes in LEM while both roads are dark. The readings
        made after the change wait, and are filed with the NEW factor."""
        need_v2("CF2", "the 60 s factor rule via the sync's config_rev (P8)")
        c = W()
        c.emit(3); c.poll(); c.poll()
        c.server.set_roads("down")
        for _ in range(4):
            c.poll()
        c.set_lem_factor("Density", 0.001)
        after = {}
        for _ in range(5):
            for _i, lab in _labs(c.emit(2)):
                after[lab] = c.printed[lab]
            c.poll()
        filed_dark = cells_for(c, list(after))
        c.server.set_roads("up")
        c.settle(polls=12)
        t = c.tally("CF2 single_csv", "factor changed while both roads dark")
        res = c.gw.results("Density")
        wrong = stale = 0
        for lab, raw in after.items():
            got = res.get(lab)
            if got in (None, ""):
                continue
            if abs(float(got) - (float(raw) + 0.001)) > 1e-9:
                wrong += 1
            if abs(float(got) - float(raw)) <= 1e-12:
                stale += 1
        # The ledger's res_wrong compares with the PRINTED value; for these
        # readings the right result is the corrected one, so it is measured
        # here instead, and over every reading made after the change.
        t["res_wrong"] = wrong
        t["filed_with_stale_factor"] = stale
        t["filed_after_change"] = sum(1 for lab in after
                                      if res.get(lab) not in (None, ""))
        t["filed_while_dark"] = filed_dark
        return t

    # ── round 2 (T-P8): the critic's two gaps, kept in the gate ─────────────
    @new("CF2r")
    def cf2r():
        """CF2 with one LabStation restart while both roads are still dark.
        The restart brings the held readings back from the journal carrying
        the correction they were PARSED with (the old one); every one of the
        12 made after the change must still be filed with the NEW factor.
        Round 1 filed the 6 journaled before the restart with the old one."""
        need_v2("CF2r", "the 60 s factor rule across a restart (P8)")
        c = W()
        c.emit(3); c.poll(); c.poll()
        c.server.set_roads("down")
        for _ in range(4):
            c.poll()
        c.set_lem_factor("Density", 0.001)
        after = {}
        for i in range(6):
            for _i, lab in _labs(c.emit(2)):
                after[lab] = c.printed[lab]
            if i == 3:
                c.restart()
            c.poll()
        filed_dark = cells_for(c, list(after))
        c.server.set_roads("up")
        c.settle(polls=12)
        t = c.tally("CF2r single_csv", "factor changed while dark; one "
                    "restart while still dark")
        res = c.gw.results("Density")
        wrong = stale = 0
        for lab, raw in after.items():
            got = res.get(lab)
            if got in (None, ""):
                continue
            if abs(float(got) - (float(raw) + 0.001)) > 1e-9:
                wrong += 1
            if abs(float(got) - float(raw)) <= 1e-12:
                stale += 1
        t["res_wrong"] = wrong
        t["filed_with_stale_factor"] = stale
        t["filed_after_change"] = sum(1 for lab in after
                                      if res.get(lab) not in (None, ""))
        t["filed_while_dark"] = filed_dark
        return t

    def _unknown_bench(sid, mode):
        """A bench that binds while LEM cannot answer v2 (`mode`), prints for
        10 polls, then LEM comes up. Only a 404 means "old server" (§6.1,
        §12.2): until LEM answers, the bench must spend NOTHING in LabCore —
        no heartbeat, status, DDL, log rows — and afterwards its records are
        in LEM once, never also in LabCore's machine log."""
        need_v2(sid, "only a 404 means an old server (P8)")
        c = W(road_modes={"A": mode, "B": mode})
        before = dict(c.gw.counts)
        for _ in range(10):
            c.emit(2); c.poll()
        dark = _ops_since(c, before)
        c.server.set_roads("up")
        c.settle(polls=15)
        t = c.tally("%s single_csv" % sid, "binds with LEM %s; up after 10 "
                    "polls" % mode)
        t["labcore_ops_while_unknown"] = sum(dark.values())
        t["labcore_kinds_while_unknown"] = sorted(dark)
        everything = _ops_since(c, before)
        t["legacy_road_ops"] = sum(
            n for k, n in everything.items()
            if k not in ("read:identity", "write:batch", "read:lem_meta"))
        t["went_legacy"] = c.m._transfer.mode == "legacy"
        return t

    @new("N404")
    def n404():
        return _unknown_bench("N404", "down")

    @new("N503")
    def n503():
        return _unknown_bench("N503", "503")

    # ── needs a v4 capability with no code yet ─────────────────────────────
    for sid, needs in V4_ONLY.items():
        reg.add(sid, (lambda s, n: lambda: _unsupported(s, n))(sid, needs), "new")

    # ── web server (phase 1's run_web.py) ──────────────────────────────────
    # On a server with a LEM store (P6) W1/W2/W3/W2b are asked of the split
    # server — `create_app(LocalStoreGateway, labcore=WGateway)` — and count
    # LabCore's ops, which is what they always measured (gharness/store_web).
    from . import store_web

    def web(name, fn, store_fn=None):
        def run():
            if _has_store():
                if store_fn is None:
                    raise Unsupported(
                        "%s on a server with a LEM store measures the bridge's "
                        "legacy pull, which lands with P9" % name)
                return store_fn(rw)
            return fn()
        return run
    reg.add("W1", web("W1", rw.w1, store_web.w1), "web")
    reg.add("W2", web("W2", rw.w2, store_web.w2), "web")
    reg.add("W3", web("W3", rw.w3, store_web.w3), "web")
    reg.add("W4", web("W4", rw.w4), "web")
    reg.add("W2b", lambda: (store_web.w2b(rw) if _has_store()
                            else _unsupported("W2b", V4_WEB["W2b"])), "web")
    return reg


V4_ONLY = {
    "A1w": "journal wipe + /checkpoint blind mode (P1, P7, P8)",
    "T3": "store restored from a backup: a 409 cursor answer from /api/v2 sync (P7, P11)",
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


def _res_lost(c):
    from .ledger import results
    return results(c.ledger, c.gw.results("Density"))["res_lost"]


def _filed(c):
    return _res_lost(c) == 0


def _ops_since(c, before):
    """Every LabCore op kind's count since `before` (a copy of gw.counts)."""
    return {k: n - before.get(k, 0) for k, n in c.gw.counts.items()
            if n - before.get(k, 0)}


def _results_ops(c):
    """LabCore ops of the results road: identity (guard) reads + batches."""
    return sum(n for k, n in c.gw.counts.items()
               if k in ("read:identity", "write:batch"))


def _labs(emitted):
    """(i, lab_id) of what `emit` printed."""
    return [(i, line.split(",")[0]) for i, line in emitted]


def _default_ua_refused(server):
    """The harness's own check that road B enforces the User-Agent: a
    request carrying urllib's default agent gets Cloudflare's 1010. Made with
    the poll-thread tally switched off — it is the harness asking, not the
    bench."""
    import urllib.error
    import urllib.request
    keep, server.poll_thread = server.poll_thread, None
    mode = server.modes["B"]
    try:
        server.set_road("B", "1010-without-UA")
        req = urllib.request.Request("https://lem.asaplabs.net/api/v2/ping")
        req.add_header("User-agent", "Python-urllib/3.12")
        try:
            server.urlopen(req)
        except urllib.error.HTTPError as e:
            return e.code == 403 and b"1010" in e.read()
        return False
    finally:
        server.set_road("B", mode)
        server.poll_thread = keep


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
    mode `roads` can set), the v2 world below, and two extra totals at the
    end.

    THE V2 WORLD (E0–E2 say "v2 mode"; E3 says "legacy projection", i.e. the
    same world with the server answering 404). What production holds before a
    v4 bench's first poll, and nothing more:
      * LabCore's `lem_meta.live_token` — the shared token the server's boot
        step publishes, which a first enrolment proves itself with (§6.4).
        `live_url` is NOT published here: a v3.9 bench with an address pushes
        to it and, on failure, re-reads `lem_meta` every few polls, which would
        move the phase-1 numbers (economy.json) this run must reproduce on
        v3.9. A bench with no address finds LEM on its compiled-in roads
        (§6.2), which is what a v4 bench does.
      * the server started with that token (`LEM_LIVE_TOKEN`, as the service
        is), and in its store the bench's configuration row — copied from
        LabCore's `lem_machine_config` exactly as the import tool copies it
        (P9), and only when the bench first calls, so a bench that never calls
        (v3.9) builds no server at all — then one snapshot build, which the
        server's 12-second poller does in production.
    Nothing here is counted: the counts are the bench's LabCore ops."""
    import tempfile
    from types import SimpleNamespace
    from datetime import datetime
    from .world import fresh_world_dirs
    from .hserver import HServer
    import threading
    from .world import _published
    fresh_world_dirs()
    d = tempfile.mkdtemp(); path = os.path.join(d, "f.csv"); open(path, "w").close()
    v2_target = hasattr(mod, "BenchUploader")
    gw = _published(GateGateway(), v2_target)
    gw.seed_samples([lh.lab_id(i) for i in range(5000)])
    holder = SimpleNamespace(gw=gw, uid="b1")
    server = HServer(lambda: server_factory(holder)).install()
    server.poll_thread = threading.get_ident()
    if roads:
        server.set_roads(roads)
    t0 = datetime(2026, 10, 1, 9, 0, 0)
    clock = {"k": 0}
    if v2_target:
        mod.bench_now = lambda: t0 + timedelta(seconds=clock["k"] * 30)
    m = lh.new_bench(gw, lh.density_machine("b1", path, source_type=source_type))

    def settle():
        # The uploader finishes what the bind / poll / pulse woke it for:
        # the time between two polls (v4 only; v3.9 has no uploader).
        wait = getattr(m, "_uploader_wait_idle", None)
        if callable(wait) and not wait(120.0):
            raise RuntimeError("the bench's uploader did not go idle")
    settle()
    POLL = 30
    bind = gw.snapshot()
    n = 0
    first = warm = None
    steps = int(minutes * 60 / POLL)
    for k in range(steps):
        clock["k"] = k
        now = t0 + timedelta(seconds=k * POLL)
        if source_type == "single_csv" and prints:
            with open(path, "a") as f:
                for _ in range(prints):
                    f.write(lh.print_line(n)); n += 1
        lh.poll(m, now)
        settle()
        if k % (mod.HEARTBEAT_SECONDS // POLL) == 0 and k:
            m._send_pulse(now)
            settle()
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
            "steady_by_cat": dict(steady),
            "steady_polls": steps - int(warm_minutes * 60 / POLL),
            "v2_syncs": server.v2_syncs(),
            "lem_requests_on_poll_thread": server.poll_thread_requests,
            # Where the readings went, so a low op count can never be bought
            # by not delivering them: run rows in LEM's store (v2) and cells
            # in LabCore's results.
            "lem_run_rows": _store_run_rows(server, "b1"),
            "cells_filed": len([v for v in gw.results("Density").values()
                                if v not in (None, "")])}


def _store_run_rows(server, uid):
    """Run rows LEM's store holds for `uid`; 0 when no v4 server was built."""
    app = server._app
    path = getattr(app, "config", {}).get("LEM_STORE") if app is not None else None
    if not path:
        return 0
    import sqlite3
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        return con.execute("SELECT COUNT(*) FROM lem_machine_log WHERE "
                           "machine_uid = ? AND kind = 'run'", [uid]).fetchone()[0]
    except sqlite3.Error as exc:
        raise RuntimeError("store run-row count failed: %s" % exc)
    finally:
        con.close()
