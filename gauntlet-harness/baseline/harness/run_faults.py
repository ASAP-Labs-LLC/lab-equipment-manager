"""(4) Fault behaviour of the CURRENT station-module write path, offline.

Every scenario: an instrument prints readings with unique Lab IDs (every Lab
ID is a sample the fake LIMS holds, so the results road can file it). Faults
are injected at the labcore_* helpers. Afterwards the fake LabCore is audited:

  log_lost     printed readings with NO `run` row in lem_machine_log
  log_dup      extra `run` rows beyond one per printed reading
  res_lost     printed readings with no value in sample_tests (Density)
  res_wrong    readings whose sample_tests value is not the printed value
  cell_dup_sends  update_cell ops sent more than once (same lab_id/test/value)
  cell_dup_lands  ...of which reached the DB more than once (idempotent upsert,
                  so these are wasted queue work / clobber risk, not extra rows)

A run row is the 17025 record; sample_tests is the LIMS result.
"""
import json
import os
import shutil
import tempfile

from lemharness import *

T0 = datetime(2026, 10, 1, 9, 0, 0)
POLL = timedelta(seconds=30)
N_SAMPLES = 3000


class Ctx:
    def __init__(self, source="single_csv", publish=True):
        self.dir = tempfile.mkdtemp(prefix="lemh-")
        self.source = source
        if source == "multi_csv":
            self.path = os.path.join(self.dir, "drop")
            os.makedirs(self.path)
        else:
            self.path = os.path.join(self.dir, "inst.csv")
            open(self.path, "w").close()
        self.gw = HGateway()
        self.gw.seed_samples([lab_id(i) for i in range(N_SAMPLES)])
        self.uid = "b1"
        self.m = new_bench(self.gw, density_machine(self.uid, self.path,
                                                    source_type=source))
        self.k = 0
        self.n = 0
        self.printed = {}            # lab_id -> value the instrument printed
        self.kills = 0
        self.restarts = 0
        self.notes = []

    @property
    def now(self):
        return T0 + self.k * POLL

    # instrument side
    def emit(self, count=1):
        ids = []
        for _ in range(count):
            i = self.n
            self.n += 1
            line = print_line(i)
            lid, val = line.strip().split(",")
            self.printed[lid] = val
            ids.append((i, line))
        if self.source == "single_csv":
            with open(self.path, "a") as f:
                for _i, line in ids:
                    f.write(line)
        elif self.source == "multi_csv":
            for i, line in ids:
                with open(os.path.join(self.path, "r%05d.csv" % i), "w") as f:
                    f.write(line)
        else:                                           # serial: frames
            self.pending_frames = getattr(self, "pending_frames", []) + \
                [line.strip() for _i, line in ids]
        return ids

    # bench side
    def poll(self):
        m = self.m
        if self.source == "serial":
            frames, self.pending_frames = getattr(self, "pending_frames", []), []
            m._ingest = lambda machine: (machine, frames, None)
        try:
            poll(m, self.now)
        except Kill:
            self.kills += 1
            self.gw.plan = None
            self.restart()
        self.k += 1

    def restart(self):
        """LabStation dies and comes back: everything in memory is gone; the
        canvas file holds the uid; the config (and its byte marker) comes back
        from LabCore."""
        try:
            self.m.shutdown()
        except Exception:
            pass
        self.restarts += 1
        self.m = new_bench(self.gw, restore_uid=self.uid)
        if self.m.machine() is None:
            raise RuntimeError("restart did not bind")

    def settle(self, polls=6):
        self.gw.plan = None
        self.gw.running = True
        for _ in range(polls):
            self.poll()

    def tally(self, name, what):
        log = self.gw.log_runs(self.uid)
        res = self.gw.results("Density")
        printed = self.printed
        log_lost = [i for i in printed if log.get(i, 0) == 0]
        log_dup = sum(max(0, log.get(i, 0) - 1) for i in printed)
        stray = sum(v for k, v in log.items() if k not in printed)
        res_lost = [i for i in printed if res.get(i) in (None, "")]
        res_wrong = [i for i in printed if res.get(i) not in (None, "", printed[i])]
        sends = sum(max(0, c - 1) for c in self.gw.cell_sends.values())
        lands = sum(max(0, c - 1) for c in self.gw.cell_lands.values())
        held = len(getattr(self.m, "_held_rows", []) or [])
        pend = len(getattr(self.m, "_pending_events", []) or [])
        return {"scenario": name, "what": what, "printed": len(printed),
                "log_rows": sum(log.values()), "log_lost": len(log_lost),
                "log_dup": log_dup, "log_stray": stray,
                "res_lost": len(res_lost), "res_wrong": len(res_wrong),
                "cell_dup_sends": sends, "cell_dup_lands": lands,
                "still_held_at_bench": held, "still_queued_log": pend,
                "kills": self.kills, "restarts": self.restarts,
                "ops": self.gw.ops_total(), "notes": self.notes}


def nth(cat, n, action, kinds=("sql", "read", "write")):
    """A plan that applies `action` to the n-th op of category `cat`."""
    seen = {"c": 0}

    def plan(kind, c, payload):
        if kind in kinds and c == cat:
            seen["c"] += 1
            if seen["c"] == n:
                return action
        return None
    return plan


def during(pred, action):
    def plan(kind, c, payload):
        return action if pred(kind, c) else None
    return plan


R = []


def scenario(fn):
    R.append(fn)
    return fn


# ── control ─────────────────────────────────────────────────────────────────
@scenario
def s00_no_fault():
    c = Ctx()
    for _ in range(10):
        c.emit(3); c.poll()
    c.settle()
    return c.tally("S0 control", "10 polls x 3 prints, no faults")


# ── kill mid-transfer ───────────────────────────────────────────────────────
@scenario
def k1_kill_before_log_write():
    c = Ctx()
    for _ in range(4):
        c.emit(3); c.poll()
    c.emit(3)
    c.gw.plan = nth("sql:machine_log", 1, "kill_before")
    c.poll()                                  # dies after the tail read
    for _ in range(4):
        c.emit(3); c.poll()
    c.settle()
    return c.tally("K1 single_csv", "kill after ingest, before the log write; restart")


@scenario
def k2_kill_between_log_batches():
    c = Ctx()
    c.emit(3); c.poll()
    c.emit(250)                               # 3 log batches of <=100 rows
    c.gw.plan = nth("sql:machine_log", 2, "kill_after")   # 2 batches landed
    c.poll()
    c.settle()
    return c.tally("K2 single_csv", "250-print poll, kill after 2nd of 3 log batches landed; restart")


@scenario
def k3_kill_after_log_before_results():
    c = Ctx()
    for _ in range(4):
        c.emit(3); c.poll()
    c.emit(3)
    c.gw.plan = nth("write:batch", 1, "kill_before")
    c.poll()
    c.settle()
    return c.tally("K3 single_csv", "kill after log write, before results batch; restart")


@scenario
def k4_kill_after_results_write_before_marker():
    c = Ctx()
    for _ in range(10):
        c.emit(3); c.poll()
    c.emit(3)
    c.gw.plan = nth("write:batch", 1, "kill_after")      # results landed, then death
    c.poll()
    c.settle()
    return c.tally("K4 single_csv", "33 prints over 11 polls; kill right after the results write landed; restart")


@scenario
def k5_restart_marker_published_midway():
    c = Ctx()
    for _ in range(10):
        c.emit(3); c.poll()
    # operator opens Settings and presses OK -> set_machine(publish=True):
    # the ONLY thing that writes last_position to LabCore
    c.m.set_machine(c.m.machine(), publish=True)
    c.notes.append("marker published after 30 prints")
    for _ in range(5):
        c.emit(3); c.poll()
    c.restart()                               # clean restart, no fault
    c.settle()
    return c.tally("K5 single_csv", "45 prints; config saved after print 30; clean LabStation restart")


@scenario
def k6_clean_restart_never_saved():
    c = Ctx()
    for _ in range(10):
        c.emit(3); c.poll()
    c.restart()
    c.settle()
    return c.tally("K6 single_csv", "30 prints; clean LabStation restart (config never re-saved since setup)")


@scenario
def k7_multi_kill_after_move():
    c = Ctx("multi_csv")
    for _ in range(4):
        c.emit(3); c.poll()
    c.emit(3)
    c.gw.plan = nth("sql:machine_log", 1, "kill_before")
    c.poll()
    c.settle()
    return c.tally("K7 multi_csv", "kill after files moved to processed/, before log write; restart")


@scenario
def k8_serial_kill_before_log():
    c = Ctx("serial")
    for _ in range(4):
        c.emit(3); c.poll()
    c.emit(3)
    c.gw.plan = nth("sql:machine_log", 1, "kill_before")
    c.poll()
    c.settle()
    return c.tally("K8 serial", "kill after frames read off the port, before log write; restart")


@scenario
def k9_kill_with_held_rows():
    """Readings for samples the LIMS has not logged yet are HELD at the bench
    (mirrored to lem_held_results). Kill before the mirror write lands."""
    c = Ctx("serial")
    c.gw.fake.sql("DELETE FROM samples WHERE lab_id IN (?,?,?)",
                  [lab_id(3), lab_id(4), lab_id(5)])
    c.emit(3); c.poll()
    c.emit(3)                                  # 3..5 -> not logged in yet: held
    c.gw.plan = nth("sql:held", 1, "kill_before")
    c.poll()
    c.gw.seed_samples([lab_id(3), lab_id(4), lab_id(5)])   # LIMS catches up
    c.settle(polls=10)
    return c.tally("K9 serial", "held readings (sample not yet logged in); kill before held-mirror write; restart; LIMS catches up")


# ── network drop ────────────────────────────────────────────────────────────
@scenario
def k9c_held_control():
    c = Ctx("serial")
    c.gw.fake.sql("DELETE FROM samples WHERE lab_id IN (?,?,?)",
                  [lab_id(3), lab_id(4), lab_id(5)])
    c.emit(3); c.poll()
    c.emit(3); c.poll()
    c.gw.seed_samples([lab_id(3), lab_id(4), lab_id(5)])
    c.settle(polls=10)
    return c.tally("K9c serial", "CONTROL for K9: same held readings, no kill")


@scenario
def k9r_held_restart_after_mirror():
    c = Ctx("serial")
    c.gw.fake.sql("DELETE FROM samples WHERE lab_id IN (?,?,?)",
                  [lab_id(3), lab_id(4), lab_id(5)])
    c.emit(3); c.poll()
    c.emit(3); c.poll()                    # held + mirrored to lem_held_results
    c.restart()                            # clean restart AFTER the mirror landed
    c.gw.seed_samples([lab_id(3), lab_id(4), lab_id(5)])
    c.settle(polls=10)
    return c.tally("K9r serial", "held readings mirrored, then clean restart; LIMS catches up")


@scenario
def n1_labcore_unreachable():
    c = Ctx()
    c.emit(3); c.poll()
    c.gw.running = False
    for _ in range(3):
        c.emit(3); c.poll()
    c.settle()
    return c.tally("N1 single_csv", "is_running False for 3 polls with prints; recovers")


@scenario
def n2_log_insert_raise_before():
    c = Ctx()
    c.emit(3); c.poll()
    c.emit(3)
    c.gw.plan = nth("sql:machine_log", 1, "raise_before")
    c.poll()
    c.settle()
    return c.tally("N2 single_csv", "log INSERT: connection dropped before LabCore got it")


@scenario
def n3_log_insert_raise_after():
    c = Ctx()
    c.emit(3); c.poll()
    c.emit(3)
    c.gw.plan = nth("sql:machine_log", 1, "raise_after")
    c.poll()
    c.settle()
    return c.tally("N3 single_csv", "log INSERT landed, response lost (client timeout)")


@scenario
def n4_results_batch_raise_after():
    c = Ctx()
    c.emit(3); c.poll()
    c.emit(3)
    c.gw.plan = nth("write:batch", 1, "raise_after")
    c.poll()
    c.settle()
    return c.tally("N4 single_csv", "results batch landed, response lost")


@scenario
def n5_identity_read_timeout():
    c = Ctx()
    c.emit(3); c.poll()
    c.emit(3)
    c.gw.plan = nth("read:identity", 1, "raise_before", kinds=("read",))
    c.poll()
    c.settle()
    return c.tally("N5 single_csv", "identity read times out (8 s watchdog shape)")


@scenario
def n6_everything_raises_10_polls():
    c = Ctx()
    c.emit(3); c.poll()
    c.gw.plan = during(lambda k, cat: True, "raise_before")
    for _ in range(10):
        c.emit(3); c.poll()
    c.settle(polls=10)
    return c.tally("N6 single_csv", "every helper raises for 10 polls (30 prints), then recovers")


# ── LabCore refusal {error, busy, retry_after} ──────────────────────────────
@scenario
def f1_log_refused_once():
    c = Ctx()
    c.emit(3); c.poll()
    c.emit(3)
    c.gw.plan = nth("sql:machine_log", 1, "refuse")
    c.poll()
    c.settle()
    return c.tally("F1 single_csv", "log INSERT refused busy once")


@scenario
def f2_results_refused_once():
    c = Ctx()
    c.emit(3); c.poll()
    c.emit(3)
    c.gw.plan = nth("write:batch", 1, "refuse", kinds=("write",))
    c.poll()
    c.settle()
    return c.tally("F2 single_csv", "results batch refused busy once")


@scenario
def f3_all_refused_20_polls():
    c = Ctx()
    c.emit(3); c.poll()
    c.gw.plan = during(lambda k, cat: True, "refuse")
    for _ in range(20):
        c.emit(10); c.poll()
    c.settle(polls=15)
    return c.tally("F3 single_csv", "every op refused for 20 polls (10 min) at 10 prints/poll, then recovers")


@scenario
def f4_all_refused_60_polls():
    c = Ctx()
    c.emit(3); c.poll()
    c.gw.plan = during(lambda k, cat: True, "refuse")
    for _ in range(60):
        c.emit(10); c.poll()
    c.settle(polls=30)
    return c.tally("F4 single_csv", "every op refused for 60 polls (30 min) at 10 prints/poll (600), then recovers")


@scenario
def f5_writes_refused_reads_ok_60_polls():
    c = Ctx("serial")
    c.emit(3); c.poll()
    c.gw.plan = during(lambda k, cat: k in ("sql", "write"), "refuse")
    for _ in range(60):
        c.emit(10); c.poll()
    c.settle(polls=30)
    return c.tally("F5 serial", "writes refused, reads answered, 60 polls at 10 prints/poll (600), then recovers")


# ── single_csv rewritten under the marker ──────────────────────────────────
def rewrite(c, lines):
    with open(c.path, "w") as f:
        f.writelines(lines)


@scenario
def r1_rewrite_same_prefix_plus_one():
    c = Ctx()
    for _ in range(5):
        c.emit(3); c.poll()
    old = open(c.path).read().splitlines(True)
    i = c.n; c.n += 1; line = print_line(i)
    c.printed[line.split(",")[0]] = line.strip().split(",")[1]
    rewrite(c, old + [line])                  # export rewrites whole file
    c.poll()
    c.settle()
    return c.tally("R1 single_csv", "instrument rewrites whole file: same history + 1 new row appended")


@scenario
def r2_rewrite_newest_first():
    c = Ctx()
    history = []
    for _ in range(5):
        before = len(c.printed)
        c.emit(3)                                 # appended by emit() ...
        history += open(c.path).read().splitlines(True)[-3:] if before == 0 else []
        # ... but this instrument keeps its export NEWEST-FIRST: rebuild it
        lines = [print_line(i) for i in range(c.n)]
        rewrite(c, list(reversed(lines)))
        c.poll()
    c.settle()
    return c.tally("R2 single_csv", "instrument keeps newest row FIRST (whole-file rewrite, prepend), 5 polls x 3")


@scenario
def r3_rotate_keep_last_5():
    c = Ctx()
    for _ in range(10):
        c.emit(3); c.poll()
    tail = open(c.path).read().splitlines(True)[-5:]
    i = c.n; c.n += 1; line = print_line(i)
    c.printed[line.split(",")[0]] = line.strip().split(",")[1]
    rewrite(c, tail + [line])                 # file shrinks -> marker > size
    c.poll()
    c.settle()
    return c.tally("R3 single_csv", "file trimmed to its last 5 rows + 1 new (shrinks below marker)")


@scenario
def r4_restart_then_reread_rewritten():
    c = Ctx()
    for _ in range(10):
        c.emit(3); c.poll()
    old = open(c.path).read().splitlines(True)
    c.restart()
    i = c.n; c.n += 1; line = print_line(i)
    c.printed[line.split(",")[0]] = line.strip().split(",")[1]
    rewrite(c, old + [line])
    c.settle()
    return c.tally("R4 single_csv", "30 prints; bench restarts; file rewritten (same history + 1); re-read")


@scenario
def r5_partial_line_at_poll():
    c = Ctx()
    c.emit(3); c.poll()
    i = c.n; c.n += 1; line = print_line(i)
    c.printed[line.split(",")[0]] = line.strip().split(",")[1]
    with open(c.path, "a") as f:
        f.write(line[:9])                     # writer mid-flush: "100126-10"
    c.poll()
    with open(c.path, "a") as f:
        f.write(line[9:])
    c.poll()
    c.settle()
    t = c.tally("R5 single_csv", "poll lands while the instrument has flushed half a line")
    log = c.gw.fake.read_sql("SELECT lab_id, value, detail FROM lem_machine_log WHERE kind='run'")
    t["stray_lab_ids"] = sorted({r["lab_id"] for r in log["rows"]} - set(c.printed))
    return t


@scenario
def r6_inplace_correction_same_size():
    c = Ctx()
    for _ in range(3):
        c.emit(3); c.poll()
    lines = open(c.path).read().splitlines(True)
    lid = lines[-1].split(",")[0]
    lines[-1] = lines[-1].replace("0.80", "0.90")     # same byte length
    c.printed[lid] = lines[-1].strip().split(",")[1]
    rewrite(c, lines)
    c.poll()
    c.settle()
    return c.tally("R6 single_csv", "instrument corrects the last row in place (same size)")


if __name__ == "__main__":
    out = []
    for fn in R:
        try:
            r = fn()
        except Exception as exc:                    # a harness failure is a failure
            r = {"scenario": fn.__name__, "HARNESS_ERROR": "%s: %s" % (type(exc).__name__, exc)}
        out.append(r)
        keys = ("printed", "log_rows", "log_lost", "log_dup", "res_lost", "res_wrong",
                "cell_dup_sends", "cell_dup_lands", "still_held_at_bench", "still_queued_log", "restarts")
        print(r.get("scenario"), "|", r.get("what", r.get("HARNESS_ERROR")))
        print("    " + "  ".join("%s=%s" % (k, r.get(k)) for k in keys))
        if r.get("stray_lab_ids"):
            print("    stray:", r["stray_lab_ids"])
    json.dump(out, open(os.path.join(os.path.dirname(__file__), "..", "faults.json"), "w"), indent=1)
