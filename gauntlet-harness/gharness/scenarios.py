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

    @new("A1j")
    def a1j():
        """A1 with the bench journal LOST before the restart: the guard read,
        alone, has to protect the analyst. Not a spec row — the spec's A1w
        also demands 0 re-sent records, which needs /checkpoint (P8) — but
        the half of it that is the results road's: with the journal (and so
        LEM's ledger of what it filed) gone, the cell holding a value LEM
        cannot vouch for must be a conflict, never overwritten. A1 alone can
        not show this: there the journal suppresses the re-read before the
        guard is ever asked, so `guard_off` leaves A1 green.

        Since P4 the wiped bench's new journal ADOPTS before it reads (§10.2):
        all 5 lines are in LabCore's record, so they join the seen-set and
        the re-read never reaches the results road at all — 0 overwritten
        and 0 duplicated rows (5 before adoption), and no cell to conflict
        over. The guard's own coverage is A3, K4 and N4, where `guard_off`
        still goes red."""
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
                "v2_syncs": r["v2_syncs"]}

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

    # ── adoption at the first v4 start (§10.2; P4) ──────────────────────────
    #
    # The world before each U scenario is what the floor holds on the day the
    # v4 module is installed: an instrument file v3.9 has been reading, the
    # `run` rows v3.9 logged from it (written with the target's own
    # `run_log_events` + `build_log_insert`, which is v3.9's shape) and the
    # cells it filed — and a bench journal that has never existed. "v4 server"
    # (U1–U4) is the v2 world of E0–E2 plus LEM's store holding those rows as
    # the §10.1 import copies them; U5 is the same world with no v2 (today's
    # v3.9 server), where the bench must ask LabCore itself.

    def adoption_world(logged, unlogged=0, pre=0, factor_then=None,
                       factor_now=None, v2=True, qc_print=False):
        if not hasattr(mod, "plan_adoption"):
            raise Unsupported("U1–U5 need adoption at the first v4 start "
                              "(P4) — not present on this target")
        c = W()
        machine = c.m.machine()
        at0 = c.now - timedelta(days=2)
        for i in range(pre):                 # older than LEM on this bench
            with open(c.path, "a") as f:
                f.write(print_line(900 + i, "0.7%03d" % i))
        # A QC standard's print, in the middle of the logged lines, judged
        # then against a spec with no correction of its own while the factor
        # was machine-level: v3.9 logged its verdict as a `qc` row holding
        # the corrected value and no raw. Not a sample: not in the ledger,
        # no cell. In the MIDDLE, so a bench that matched only the QC print
        # would call everything after it recovered (not quietly "history").
        qc_text = "QC-D,0.8500\n"
        qc_at = logged // 2 if qc_print else None
        texts, logged_texts = [], []
        for i in range(logged + unlogged):
            if i == qc_at:
                c._write_lines([qc_text])
                logged_texts.append(qc_text)
            lab, val = print_line(i).strip().split(",")
            texts.append(c.emit_line(lab, val))
            if i < logged:
                logged_texts.append(texts[-1])
        saved = machine.corrections, machine.tests
        if qc_print:
            machine.tests = [mod.TestSpec(name="Density", value_col="Density",
                                          expected=0.85, std_dev=0.05, k=2.0,
                                          sample_id="QC-D")]
        machine.corrections = dict(factor_then or {})
        try:
            for k, text in enumerate(logged_texts):
                at = at0 + timedelta(minutes=k)
                rows = mod.apply_row_corrections(
                    [mod.parse_print(machine, text.strip()).to_row(at)],
                    machine.corrections)
                for row, kind, lab, test, value, detail in mod.run_log_events(
                        machine, rows, "analyst", None):
                    sql, args = mod.build_log_insert(c.uid, kind, at, lab_id=lab,
                                                     test_name=test, value=value,
                                                     detail=detail)
                    res = c.gw.fake.sql(sql, args)
                    if res.get("error"):
                        raise RuntimeError("U world: v3.9 row: " + res["error"])
                    if kind != "run":
                        continue
                    res = c.gw.fake.write("update_cell", {
                        "lab_id": lab, "test_name": "Density",
                        "value": str(row["Density"])})
                    if res.get("error"):
                        raise RuntimeError("U world: v3.9 cell: " + res["error"])
        finally:
            machine.corrections, machine.tests = saved
        if factor_now:
            for test, corr in factor_now.items():
                res = c.gw.fake.sql(
                    "INSERT OR REPLACE INTO lem_correction_factors (machine_uid, "
                    "test_name, correction) VALUES (?, ?, ?)", [c.uid, test, corr])
                if res.get("error"):
                    raise RuntimeError("U world: factor: " + res["error"])
        c.seeded_rows = len(c.stored_rows())     # LabCore's, before any v2 copy
        c.saved_token = os.environ.get("LEM_LIVE_TOKEN")
        if v2:
            _v2_adoption_world(c, server_factory)
        reads = c.adoption_reads = []
        inner = c.gw.read_sql

        def read_sql(sql, args=None, **kw):
            if "MIN(ts)" in sql or "lab_id IN" in sql:
                reads.append(sql)
            return inner(sql, args, **kw)
        c.gw.read_sql = read_sql
        mod.__dict__["labcore_read_sql"] = read_sql
        c.restart()                    # the v4 module starts (and reads lem_meta)
        mod.__dict__["labcore_read_sql"] = read_sql
        c.reads_before = _labcore_reads(c)
        for _ in range(4):
            c.poll()
        c.reads_after = _labcore_reads(c)
        return c

    def adoption_measure(c):
        try:
            return _measure(c)
        finally:
            # The v2 world's server token must not outlive this world.
            if c.saved_token is None:
                os.environ.pop("LEM_LIVE_TOKEN", None)
            else:
                os.environ["LEM_LIVE_TOKEN"] = c.saved_token

    def _measure(c):
        rows = c.stored_rows()
        recs = c.journal_records() or []
        adoptions = [r for r in recs if r.get("kind") == "adoption"]
        recovered = _recovered_rows(c)
        led = _ledger_tally(c.ledger.truth(), rows)
        recovered_labs = {lab for lab, _v in recovered}
        status = c.m.evaluation().status if c.m.evaluation() else None
        return {"new_rows": len(rows) - c.seeded_rows,
                "cell_sends": sum(c.gw.cell_sends.values()),
                "recovered_rows": len(recovered),
                "lost": led["lost"], "dup": led["dup"],
                "auto_filed": sum(n for (lab, _t, _v), n in c.gw.cell_sends.items()
                                  if lab in recovered_labs),
                "adoption_summaries": len(adoptions),
                "alarms": len(recovered) + (c.counters()["conflicts"] or 0)
                + (1 if status == mod.STATUS_RED else 0),
                "labcore_reads": len(c.adoption_reads),
                # Every LabCore read of the four polls, adoption's and the
                # bench's own (config, override, QC library — v3.9's road).
                "labcore_reads_all_polls": c.reads_after - c.reads_before,
                "adoption": {k: adoptions[-1].get(k) for k in (
                    "path_kind", "matched", "presumed", "recovered",
                    "pre_history_lines", "unchecked", "unreadable", "road",
                    "labcore_reads")}
                if adoptions else None,
                "store": c.store_kind()}

    @new("U1")
    def u1():
        """30 lines v3.9 fully logged; A's prototype logged 30 and sent 30."""
        return adoption_measure(adoption_world(30))

    @new("U2")
    def u2():
        """30 logged and 1 printed while LabStation was down for the upgrade."""
        return adoption_measure(adoption_world(30, unlogged=1))

    @new("U3")
    def u3():
        """Logged with +0.0100; the factor is +0.0200 at the v4 start. Every
        line was recorded, so every recovered row would be a false one. The
        30 readings AND a QC standard's print, whose v3.9 verdict row kept no
        raw (machine-level factor) — through LEM's digest and again through
        LabCore's indexed reads (today's server)."""
        worlds = {road: adoption_measure(adoption_world(
            30, factor_then={"Density": 0.01}, factor_now={"Density": 0.02},
            v2=(road == "v2"), qc_print=True)) for road in ("v2", "legacy")}
        m = dict(worlds["v2"])
        m["false_recovered"] = sum(w["recovered_rows"] for w in worlds.values())
        m["legacy"] = worlds["legacy"]
        return m

    @new("U4")
    def u4():
        """12 lines from before the bench was on LEM, then 15 LEM recorded."""
        return adoption_measure(adoption_world(15, pre=12))

    @new("U5")
    def u5():
        """U1 and U2 again on today's v3.9 server: no v2, the bench asks
        LabCore through indexed reads. Gated: U1's 0/0, U2's 1/0/0/0, and the
        reads adoption made in the worse of the two."""
        one = adoption_measure(adoption_world(30, v2=False))
        two = adoption_measure(adoption_world(30, unlogged=1, v2=False))
        return {"new_rows": one["new_rows"],
                "cell_sends": one["cell_sends"] + two["cell_sends"],
                "recovered_rows": two["recovered_rows"], "lost": two["lost"],
                "dup": two["dup"], "auto_filed": two["auto_filed"],
                "labcore_reads": max(one["labcore_reads"], two["labcore_reads"]),
                "labcore_reads_all_polls": max(one["labcore_reads_all_polls"],
                                               two["labcore_reads_all_polls"]),
                "u1": one, "u2": two}

    # ── the mixed fleet (P9): a v3.9 bench under a v4 server, a projected
    # bench meeting v2, a module rolled back. Asked of the target's bridge;
    # `Unsupported` (a FAIL) on a target without one.
    from . import mixed_fleet as _mf
    for sid, fn in (("M2", _mf.m2), ("M6", _mf.m6), ("DG1", _mf.dg1)):
        reg.add(sid, (lambda f: lambda: (f(rw) if _has_store() else
                                         _unsupported(sid_of(f), "a LEM store "
                                                      "and the bridge (P6, P9)")))(fn),
                "new")

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
    from . import mixed_fleet
    reg.add("W1", web("W1", rw.w1, store_web.w1_with_bridge), "web")
    reg.add("W2", web("W2", rw.w2, store_web.w2), "web")
    reg.add("W3", web("W3", rw.w3, store_web.w3), "web")
    reg.add("W4", web("W4", rw.w4, mixed_fleet.w4), "web")
    reg.add("W2b", lambda: (store_web.w2b(rw) if _has_store()
                            else _unsupported("W2b", V4_WEB["W2b"])), "web")
    return reg


V4_ONLY = {
    "A1w": "journal wipe + /checkpoint blind mode (P1, P7, P8)",
    "T3": "store restored from a backup: a 409 cursor answer from /api/v2 sync (P7, P11)",
    "T4": "journal deletion with the server reachable: /checkpoint (P1, P7, P8)",
    "T4b": "journal deleted with both roads down: blind mode (P1, P8)",
    "CF1": "the LabCore factor replica, `config_rev:<uid>` in lem_meta (P8, P9)",
    "CF2": "a factor change while both roads are dark: replica + held readings (P8, P9)",
    "M1": "order matrix pairing 1 (§12.2; P13)",
    "M3": "order matrix pairing 3 (§12.2; P13)",
    "M4": "order matrix pairing 4 (§12.2; P13)",
    "M5": "order matrix pairing 5 (§12.2; P13)",
    "DG2": "server rollback after v4 benches synced: projection (P9)",
}
V4_WEB = {"W2b": "server INSERTs on the LEM store (P6)"}


def _unsupported(sid, needs):
    raise Unsupported("%s needs %s — not present on this target" % (sid, needs))


def sid_of(fn):
    return {"m2": "M2", "m6": "M6", "dg1": "DG1"}.get(fn.__name__, fn.__name__)


def _has_store():
    from .servers import find_store_gateway
    return find_store_gateway() is not None


def _labcore_reads(c):
    return sum(n for k, n in c.gw.counts.items() if k.startswith("read:"))


def _recovered_rows(c):
    """(lab_id, value) of the run rows the record holds as `recovered`: the
    LEM store's `origin` column, or `detail.origin` in LabCore's log."""
    import json as _json
    if c.store_kind() == "lem":
        import sqlite3
        con = sqlite3.connect("file:%s?mode=ro" % os.environ["LEM_STORE_PATH"],
                              uri=True)
        try:
            return [(r[0], r[1]) for r in con.execute(
                "SELECT lab_id, detail FROM lem_machine_log WHERE machine_uid = ? "
                "AND kind = 'run' AND origin = 'recovered'", [c.uid])]
        finally:
            con.close()
    res = c.gw.fake.read_sql("SELECT lab_id, detail FROM lem_machine_log WHERE "
                             "machine_uid = ? AND kind = 'run'", [c.uid])
    if res.get("error"):
        raise RuntimeError("recovered-row read failed: " + res["error"])
    out = []
    for r in res["rows"]:
        try:
            d = _json.loads(r["detail"] or "{}")
        except ValueError:
            continue
        if d.get("origin") == "recovered":
            out.append((r["lab_id"], r["detail"]))
    return out


def _ledger_tally(truth, rows):
    from .ledger import tally
    return tally(truth, rows)


def _v2_adoption_world(c, server_factory):
    """The v2 world of `_economy_run` for a World: LabCore's lem_meta holds
    the shared token, the server runs with it, and when it is first called
    its store gets the bench's config row (as the import tool copies it) and
    the bench's legacy machine-log rows, copied from LabCore as §10.1's import
    copies them (`origin='legacy_labcore'`, `legacy_key`), plus LabCore's
    correction factors."""
    token = "gate-shared-live-token"
    for stmt, args in (("CREATE TABLE IF NOT EXISTS lem_meta (key TEXT PRIMARY "
                        "KEY, value TEXT)", None),
                       ("INSERT OR REPLACE INTO lem_meta (key, value) VALUES "
                        "('live_token', ?)", [token])):
        res = c.gw.fake.sql(stmt, args)
        if res.get("error"):
            raise RuntimeError("v2 world: lem_meta: " + res["error"])
    os.environ["LEM_LIVE_TOKEN"] = token

    def factory():
        app = server_factory(c)
        _seed_store_from_labcore(app, c.gw)
        _seed_store_log_from_labcore(app, c.gw, c.uid)
        return app
    c.server._factory = factory


def _seed_store_log_from_labcore(app, gw, uid):
    from .servers import find_store_gateway
    store_path = app.config.get("LEM_STORE")
    Store = find_store_gateway()
    if not store_path or Store is None:
        return
    rows = gw.fake.read_sql("SELECT rowid AS r, machine_uid, ts, kind, lab_id, "
                            "test_name, value, detail FROM lem_machine_log WHERE "
                            "machine_uid = ? ORDER BY rowid", [uid])
    if rows.get("error"):
        raise RuntimeError("v2 world: LabCore log read: " + rows["error"])
    factors = gw.fake.read_sql("SELECT machine_uid, test_name, correction FROM "
                               "lem_correction_factors")
    if factors.get("error"):
        raise RuntimeError("v2 world: LabCore factor read: " + factors["error"])
    store = Store(store_path)
    try:
        for r in rows["rows"]:
            res = store.sql(
                "INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
                "test_name, value, detail, origin, legacy_key, legacy_rowid) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'legacy_labcore', ?, ?)",
                [r["machine_uid"], r["ts"], r["kind"], r["lab_id"],
                 r["test_name"], r["value"], r["detail"],
                 "gate:%s" % r["r"], r["r"]])
            if res.get("error"):
                raise RuntimeError("v2 world: store log: " + res["error"])
        for f in factors["rows"]:
            res = store.sql("INSERT OR REPLACE INTO lem_correction_factors "
                            "(machine_uid, test_name, correction) VALUES (?, ?, ?)",
                            [f["machine_uid"], f["test_name"], f["correction"]])
            if res.get("error"):
                raise RuntimeError("v2 world: store factor: " + res["error"])
    finally:
        close = getattr(store, "close", None)
        if callable(close):
            close()
    snaps = app.config.get("SNAPSHOTS")
    if snaps is not None:
        snaps.refresh()


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
    fresh_world_dirs()
    d = tempfile.mkdtemp(); path = os.path.join(d, "f.csv"); open(path, "w").close()
    gw = GateGateway(); gw.seed_samples([lh.lab_id(i) for i in range(5000)])
    holder = SimpleNamespace(gw=gw)
    token = "gate-shared-live-token"
    for stmt, args in (("CREATE TABLE IF NOT EXISTS lem_meta (key TEXT PRIMARY "
                        "KEY, value TEXT)", None),
                       ("INSERT OR REPLACE INTO lem_meta (key, value) VALUES "
                        "('live_token', ?)", [token])):
        res = gw.fake.sql(stmt, args)
        if res.get("error"):
            raise RuntimeError("v2 world: lem_meta: " + res["error"])
    saved_token = os.environ.get("LEM_LIVE_TOKEN")
    os.environ["LEM_LIVE_TOKEN"] = token

    def v2_server():
        app = server_factory(holder)
        _seed_store_from_labcore(app, gw)
        return app
    server = HServer(v2_server).install()
    try:
        return _economy_body(gw, server, mod, lh, source_type, prints, roads,
                             minutes, warm_minutes, path)
    finally:
        if saved_token is None:
            os.environ.pop("LEM_LIVE_TOKEN", None)
        else:
            os.environ["LEM_LIVE_TOKEN"] = saved_token


def _seed_store_from_labcore(app, gw):
    """The import tool's copy of `lem_machine_config` (and the status row the
    floor lists machines from) into a v4 server's store. A v3.9 server has no
    store: nothing to do."""
    from .servers import find_store_gateway
    store_path = app.config.get("LEM_STORE")
    Store = find_store_gateway()
    if not store_path or Store is None:
        return
    rows = gw.fake.read_sql("SELECT * FROM lem_machine_config")
    if rows.get("error"):
        raise RuntimeError("v2 world: LabCore config read: " + rows["error"])
    store = Store(store_path)
    try:
        import snapshot_service
        for ddl in snapshot_service.SCHEMA_DDL:
            store.sql(ddl)
        for r in rows["rows"]:
            cols = [c for c in ("machine_uid", "title", "config", "updated_at",
                                "updated_by") if c in r]
            res = store.sql("INSERT OR REPLACE INTO lem_machine_config (%s) "
                            "VALUES (%s)" % (", ".join(cols),
                                             ", ".join("?" for _ in cols)),
                            [r[c] for c in cols])
            if res.get("error"):
                raise RuntimeError("v2 world: store config: " + res["error"])
            res = store.sql("INSERT OR IGNORE INTO lem_machine_status "
                            "(machine_uid, title, status, reason, updated_at) "
                            "VALUES (?, ?, 'UNKNOWN', '', ?)",
                            [r["machine_uid"], r.get("title") or "",
                             r.get("updated_at") or ""])
            if res.get("error"):
                raise RuntimeError("v2 world: store status: " + res["error"])
    finally:
        close = getattr(store, "close", None)
        if callable(close):
            close()
    snaps = app.config.get("SNAPSHOTS")
    if snaps is not None:
        snaps.refresh()


def _economy_body(gw, server, mod, lh, source_type, prints, roads, minutes,
                  warm_minutes, path):
    from datetime import datetime
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
            "steady_by_cat": dict(steady),
            "steady_polls": steps - int(warm_minutes * 60 / POLL),
            "v2_syncs": server.v2_syncs(),
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
