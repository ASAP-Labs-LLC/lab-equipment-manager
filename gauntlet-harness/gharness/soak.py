"""The 24 h accelerated soak (transfer §16, piece P13): 17 benches, one LEM.

The gate's scenarios each put ONE bench through ONE fault, which is how a
number is pinned to a cause. The soak asks the other question: does anything
go wrong when everything happens to a fleet at once, for a day? Seventeen
real v4 station modules (the target's own `lem_station_module`) — 12 single
CSV files, 3 drop folders, 2 serial ports driven through the module's own
reader — share ONE LabCore (the gate's GateGateway, `_wop_batch` exact) and
ONE real v4 server (`create_app(LocalStoreGateway, labcore=...)` through
HServer's two roads), on a simulated clock: 24 h at the real 30 s poll, 2,880
polls per bench.

The schedule is drawn from a seed (so a failure reproduces) and every fault
in it is COUNTED as it fires — a soak whose kills never landed or whose
restore never happened measured a quiet day and would report it as a hard
one:

* random kills: each bench poll has a small chance of arming one of the
  module's fault points (on the poll thread or the uploader thread — the hook
  knows which bench a thread belongs to) or a LabCore kill around a results
  batch (K3/K4's shapes). A killed bench restarts from its saved canvas state,
  as LabStation does. An armed kill that its poll never reached is disarmed
  and counted as such.
* road flaps: the LAN dark for an hour (the public road takes over), short
  random flaps on either road (down, drop_before, lose_response, 503), and
  both roads dark for 90 minutes (results keep filing for 60 s, then hold).
* a refusal storm: LabCore refuses every operation for 30 minutes (F4).
* server restarts, and a store restore: the server's own hourly backups run
  on the simulated clock; at hour 18 the server is stopped and the backup of
  hour 16 is restored with custody's `restore` — every bench is answered 409
  and resends.
* analysts: a person corrects a cell the bench filed (A1), and types a cell
  while the bench holds the reading in the dark (A3).

The verdict (`verdict()`): 0 lost and 0 unlabelled duplicates on the §9.4
ledger per bench (counted in the LEM store the server wrote), 0 analyst
values overwritten (the soak injects no A5 race, so every overwrite is
outside A5), every result filed or deliberately left to a person (res_lost 0,
res_wrong 0 on the ledger, which knows what the analyst typed), and every
bench DRAINED: the store holds every record each bench's journal holds — read
from the journal on disk with the harness's own CRC reader, not the module's
word — and the bench has nothing unacked and no reading still open.
"""
import json
import os
import random
import shutil
import sqlite3
import threading
import time
from collections import Counter
from datetime import timedelta, timezone

from . import env
from .hserver import HServer
from .ledger import Ledger, tally as ledger_tally, results as ledger_results
from .world import HIDING, _row, _crc_ok, _CRC_TAIL, _SEG, _HarnessPort, _published

POLL_S = 30
HOUR = 3600 // POLL_S                     # polls per simulated hour

#: Fault points the module calls; any may be armed for one bench poll.
KILL_POINTS = ("before_journal", "after_journal_before_cursor", "after_cursor",
               "after_combined_read", "after_batch_landed",
               "before_filed_journaled")
LABCORE_KILLS = ("kill_before", "kill_after")


def _quiet_kills(Kill):
    """A kill on an uploader thread ends that thread by design (the process
    died there); its traceback is not news. Anything else still reaches the
    hook that was installed."""
    prior = threading.excepthook
    if getattr(prior, "_soak_quiet", False):
        return

    def hook(args):
        if args.exc_type is not None and issubclass(args.exc_type, Kill):
            return
        prior(args)
    hook._soak_quiet = True
    threading.excepthook = hook


def lab_id(bench_no, i):
    return "1001%02d-%05d" % (bench_no, i)


class SoakBench:
    def __init__(self, fleet, no, source):
        self.fleet = fleet
        self.no = no
        self.uid = "soak-%02d" % no
        self.source = source
        self.ledger = Ledger("Density")
        self.n = 0
        self.kills = Counter()
        self.restarts = 0
        self.pending_frames = []
        d = os.path.join(fleet.root, "instruments", self.uid)
        os.makedirs(d, exist_ok=True)
        if source == "multi_csv":
            self.path = os.path.join(d, "drop")
            os.makedirs(self.path)
        else:
            self.path = os.path.join(d, "inst.csv")
            open(self.path, "w").close()
        lh = fleet.lh
        self.m = lh.new_bench(fleet.gw, lh.density_machine(
            self.uid, self.path, source_type=source))
        self._attach_reader()

    # ── the instrument ──
    def emit(self, count):
        lines = []
        for _ in range(count):
            lab = lab_id(self.no, self.n)
            value = "0.%04d" % (8000 + (self.n * 7919 + self.no) % 2000)
            self.n += 1
            # The sample exists in the LIMS before its reading is printed,
            # as every phase-1 scenario arranges (a reading for no sample is
            # B1's question, not the soak's).
            self.fleet.gw.seed_samples([lab])
            self.ledger.register(lab, value)
            lines.append("%s,%s\n" % (lab, value))
        if not lines:
            return
        if self.source == "single_csv":
            with open(self.path, "a") as f:
                f.writelines(lines)
        elif self.source == "multi_csv":
            for line in lines:
                self.fleet.mc_serial += 1
                with open(os.path.join(self.path, "x%07d.csv"
                                       % self.fleet.mc_serial), "w") as f:
                    f.write(line)
        else:
            self.pending_frames.extend(line.strip() for line in lines)

    # ── serial, through the module's own reader (as World does) ──
    def _attach_reader(self):
        if self.source != "serial":
            return
        self._port = _HarnessPort()
        self.m._serial_reader = self.m._open_serial_reader(
            self.m.machine(), port=self._port)
        self._wire_t = 0.0

    def _deliver_frames(self):
        reader = self.m._serial_reader
        while self.pending_frames:
            frame = self.pending_frames.pop(0)
            self._wire_t += 10.0
            reader._on_bytes(frame.encode("utf-8"), self._wire_t)
            self._wire_t += 10.0
            reader._on_idle(self._wire_t)

    # ── the bench ──
    def poll(self, now):
        lh = self.fleet.lh
        try:
            if self.source == "serial":
                self._deliver_frames()
            lh.poll(self.m, now)
        except lh.Kill:
            self.kills["poll_thread"] += 1
            self.fleet.gw.plan = self.fleet.base_plan
            self.restart()

    def uploader_thread(self):
        return getattr(getattr(self.m, "_uploader", None), "thread", None)

    def settle(self):
        """The uploader finishes what it was woken for, or died of a kill."""
        thread = self.uploader_thread()
        deadline = time.monotonic() + 120.0
        while not self.m._uploader_wait_idle(0.2):
            if thread is not None and not thread.is_alive():
                break
            if time.monotonic() > deadline:
                raise RuntimeError("%s: the uploader did not go idle in 120 s"
                                   % self.uid)
        if thread is not None and not thread.is_alive():
            # The process died on its uploader thread: restart the bench.
            thread.join(30.0)
            self.kills["uploader_thread"] += 1
            self.fleet.gw.plan = self.fleet.base_plan
            self.restart()
            self.settle()

    def restart(self):
        lh = self.fleet.lh
        state = self.m.serialize_state()
        try:
            self.m.shutdown()
        except Exception:                                  # noqa: BLE001
            pass
        self.restarts += 1
        self.m = lh.new_bench(self.fleet.gw)
        self.m.restore_state(state)
        if self.m.machine() is None:
            raise RuntimeError("%s: restart did not bind" % self.uid)
        self._attach_reader()

    # ── what the bench holds, read independently ──
    def journal_dir(self):
        return os.path.join(os.environ["LEM_JOURNAL_DIR"], self.uid)

    def journal_records(self):
        d = self.journal_dir()
        if not os.path.isdir(d):
            raise RuntimeError("%s keeps no journal at %s" % (self.uid, d))
        out = []
        for n in sorted(os.listdir(d)):
            if not _SEG.match(n):
                continue
            with open(os.path.join(d, n), "rb") as f:
                for line in f.read().splitlines(True):
                    mt = _CRC_TAIL.search(line)
                    if not mt or not _crc_ok(line):
                        continue
                    out.append(json.loads(line[:mt.start()] + b"}"))
        return out


class Fleet:
    def __init__(self, lh, rf, mod, GateGateway, server_factory, n=17,
                 seed=1, hours=24.0):
        self.lh, self.rf, self.mod = lh, rf, mod
        self.rng = random.Random(seed)
        self.seed = seed
        self.hours = hours
        self.ticks = int(hours * HOUR)
        self.root = env.root()
        if not self.root:
            raise RuntimeError("env.activate() was not called")
        for k in ("APPDATA", "LOCALAPPDATA", "LEM_JOURNAL_DIR"):
            os.makedirs(os.environ[k], exist_ok=True)
        self.k = 0
        self.mc_serial = 0
        mod.bench_now = lambda: rf.T0 + self.k * rf.POLL
        self.base_plan = None
        self.gw = _published(GateGateway(batch_mode="labcore"), True)
        self.uids = ["soak-%02d" % i for i in range(n)]
        self.server = HServer(lambda: server_factory(self))
        self.server.install()
        self.server.poll_thread = threading.get_ident()
        self._lock = threading.Lock()
        inner = self.server.urlopen

        def urlopen(*a, **kw):
            # One LEM process answers one request at a time here: the
            # Flask test client and the harness's counters are not built
            # for 17 uploader threads at once.
            with self._lock:
                return inner(*a, **kw)
        self.server.urlopen = urlopen
        import urllib.request
        urllib.request.urlopen = urlopen
        self.server.app                 # the server is up before the benches
        sources = ["single_csv"] * 12 + ["multi_csv"] * 3 + ["serial"] * 2
        self.benches = []
        for i in range(n):
            b = SoakBench(self, i, sources[i % len(sources)])
            self.benches.append(b)
            b.settle()
        self.events = Counter()
        _quiet_kills(lh.Kill)
        self.armed = {}                # uid -> fault point armed this poll
        self.forced = None             # "point" | "labcore": arm on next poll
        self.current = None
        self._install_kill_hook()
        self.backups = {}              # hour -> backup path
        self.analyst_cells = set()
        self.log = []

    # ── kills ──
    def _bench_of_thread(self):
        t = threading.current_thread()
        if t is threading.main_thread():
            return self.current
        for b in self.benches:
            if b.uploader_thread() is t:
                return b
        return None

    def _install_kill_hook(self):
        fleet = self
        Kill = self.lh.Kill

        def hook(point):
            b = fleet._bench_of_thread()
            if b is None or fleet.armed.get(b.uid) != point:
                return None
            del fleet.armed[b.uid]
            fleet.events["kill:" + point] += 1
            raise Kill("soak: fault point " + point)
        self.mod.fault_point = hook

    def labcore_kill_plan(self, action):
        """Kill the process that sends LabCore its next results batch. There
        is one LabCore, so that may be ANOTHER bench's uploader (filing on its
        own schedule): that LabStation dies, and `restart_dead` brings it
        back. The plan says whether it fired; the bench polled is not the
        judge of that."""
        fleet = self

        def plan(kind, cat, op):
            if kind == "write" and cat == "write:batch" and not plan.fired:
                plan.fired = True
                fleet.events["kill:labcore_" + action] += 1
                return action
            return fleet.base_plan(kind, cat, op) if fleet.base_plan else None
        plan.fired = False
        return plan

    def restart_dead(self):
        """Every bench whose uploader thread died of a kill is a LabStation
        that died: restart it now, before anything else polls it."""
        for b in self.benches:
            t = b.uploader_thread()
            if t is not None and not t.is_alive():
                t.join(30.0)
                b.kills["uploader_thread"] += 1
                self.gw.plan = self.base_plan
                b.restart()
                b.settle()

    # ── the clock ──
    @property
    def now(self):
        return self.rf.T0 + self.k * self.rf.POLL

    def utc_now(self):
        return self.now.replace(tzinfo=timezone(timedelta(hours=-7))) \
            .astimezone(timezone.utc)

    # ── the schedule ──
    def schedule(self):
        """{tick: [(what, arg), ...]} drawn from the seed."""
        S = {}
        rng = self.rng

        def at(tick, what, arg=None):
            if 0 <= tick < self.ticks:
                S.setdefault(int(tick), []).append((what, arg))
        H = HOUR
        f = self.ticks / float(24 * H)           # a shorter soak scales down
        at(2.0 * H * f, "roads", ("A", "drop_before"))      # LAN dark, 1 h
        at(3.0 * H * f, "roads", ("A", "up"))
        at(6.0 * H * f, "both_dark", True)                  # 90 min dark
        at(7.5 * H * f, "both_dark", False)
        at(6.5 * H * f, "analyst_held", 3)                  # A3 in the dark
        at(10.0 * H * f, "storm", True)                     # refusal storm
        at(10.5 * H * f, "storm", False)
        at(12.0 * H * f, "server_reboot", None)
        at(16.0 * H * f, "mark_backup", 16)
        at(18.0 * H * f, "restore", 16)
        at(20.0 * H * f, "server_reboot", None)
        # One kill of each kind at a fixed point, so a day of any length has
        # both (the random ones come on top).
        at(1.0 * H * f, "force_kill", "point")
        at(4.0 * H * f, "force_kill", "labcore")
        at(14.0 * H * f, "force_kill", "labcore")
        for _ in range(max(4, int(30 * f))):                # short flaps
            t = rng.randrange(0, max(1, self.ticks - H))
            road = rng.choice(("A", "B"))
            mode = rng.choice(("down", "drop_before", "lose_response", "503"))
            at(t, "flap", (road, mode, rng.randint(1, 6)))
        for _ in range(max(3, int(20 * f))):                # analyst A1
            at(rng.randrange(H, max(H + 1, self.ticks - H)), "analyst_filed", None)
        return S

    # ── one simulated day ──
    def run(self, kill_p=0.012, print_p=0.1):
        sched = self.schedule()
        rng = self.rng
        dark = False
        for self.k in range(self.ticks):
            if self.k and self.k % HOUR == 0:
                self.hourly_backup()
            for what, arg in sched.get(self.k, ()):
                dark = self.apply(what, arg, dark)
            for b in self.benches:
                self.current = b
                count = 0
                if rng.random() < print_p:
                    count = rng.choice((1, 1, 1, 2, 3, 5, 8))
                if self.forced and not count:
                    count = 3                # a forced kill needs a print
                b.emit(count)
                # Kills are armed on polls that print: a quiet poll reaches
                # no journal, cursor or results point, and an armed kill
                # that never fires is a quiet day reported as a hard one.
                forced, self.forced = self.forced, None
                if count and (forced or rng.random() < kill_p):
                    if forced == "labcore" or (forced is None
                                               and rng.random() < 0.25):
                        self.gw.plan = self.labcore_kill_plan(
                            rng.choice(LABCORE_KILLS))
                        self.events["armed_labcore"] += 1
                    else:
                        self.armed[b.uid] = rng.choice(KILL_POINTS)
                        self.events["armed_point"] += 1
                b.poll(self.now)
                b.settle()
                self.restart_dead()
                if self.armed.pop(b.uid, None) is not None:
                    self.events["armed_not_reached"] += 1
                if getattr(self.gw.plan, "fired", None) is False:
                    self.events["labcore_kill_not_reached"] += 1
                if self.gw.plan is not self.base_plan:
                    self.gw.plan = self.base_plan
            self.current = None
        return self.settle_fleet()

    def apply(self, what, arg, dark):
        srv = self.server
        self.events[what] += 1
        if what == "roads":
            srv.set_road(*arg)
        elif what == "both_dark":
            srv.set_roads("down" if arg else "up")
            dark = bool(arg)
        elif what == "flap":
            road, mode, times = arg
            if not dark:
                srv.set_road(road, mode, times=times)
        elif what == "storm":
            events = self.events

            def refuse(kind, cat, op):
                events["labcore_refused"] += 1
                return "refuse"
            self.base_plan = refuse if arg else None
            self.gw.plan = self.base_plan
        elif what == "server_reboot":
            srv.reboot()
        elif what == "mark_backup":
            self.backups["mark"] = self.hourly_backup(force=True)
        elif what == "restore":
            self.restore(self.backups["mark"])
        elif what == "force_kill":
            self.forced = arg
        elif what == "analyst_filed":
            self.analyst_filed()
        elif what == "analyst_held":
            self.analyst_held(arg)
        return dark

    # ── custody on the simulated clock ──
    def custody(self):
        cust = self.server.app.config.get("CUSTODY")
        if cust is None:
            raise RuntimeError("the server has no custody service")
        cust.clock = self.utc_now
        return cust

    def hourly_backup(self, force=False):
        res = self.custody().backup_now()
        if not res.get("ok"):
            raise RuntimeError("soak: the hourly backup failed: %s"
                               % res.get("error"))
        self.events["backups"] += 1
        # Keep the copy the restore will use out of retention's reach (the
        # simulated day makes 24 "hourly" backups in a few real minutes).
        if force:
            keep = os.path.join(self.root, "soak-restore-source")
            os.makedirs(keep, exist_ok=True)
            dst = os.path.join(keep, os.path.basename(res["path"]))
            import custody
            shutil.copy2(res["path"], dst)
            shutil.copy2(custody.manifest_path(res["path"]),
                         custody.manifest_path(dst))
            self.marked_acked = self.store_acked()
            return {"path": dst, "orig": res["path"]}
        return res

    def restore(self, mark):
        import custody
        store_path = self.server.app.config["LEM_STORE"]
        before = self.store_acked()
        self.server.reboot()                        # the server is stopped
        out = custody.restore(mark["path"], store_path, offsite_dir=None)
        self.events["restore_unwitnessed"] += int(bool(out["unwitnessed"]))
        after = self.store_acked()
        self.restore_effect = {
            "acked_rolled_back": sum(1 for u in before
                                     if after.get(u, 0) < before[u]),
            "records_rolled_back": sum(before[u] - after.get(u, 0)
                                       for u in before)}

    # ── analysts ──
    def _filed_labs(self, b):
        cells = self.gw.results("Density")
        return [lab for lab in sorted(b.ledger.labs())
                if cells.get(lab) not in (None, "")
                and lab not in self.analyst_cells]

    def analyst_filed(self):
        b = self.rng.choice(self.benches)
        labs = self._filed_labs(b)
        if not labs:
            self.events["analyst_filed_skipped"] += 1
            return
        lab = self.rng.choice(labs)
        value = "0.7%03d" % self.rng.randrange(1000)
        self.gw.analyst_edit(lab, "Density", value)
        b.ledger.analyst_set(lab, value)
        self.analyst_cells.add(lab)
        self.events["analyst_edits"] += 1

    def analyst_held(self, n):
        """A person types cells the bench printed in the dark and has not
        filed (A3): the bench must leave them as a conflict."""
        done = 0
        for b in self.benches:
            if b.source != "single_csv":
                continue
            b.emit(1)
            b.poll(self.now)
            b.settle()
            lab = lab_id(b.no, b.n - 1)
            if self.gw.cell(lab, "Density") in (None, ""):
                value = "0.6%03d" % self.rng.randrange(1000)
                self.gw.analyst_edit(lab, "Density", value)
                b.ledger.analyst_set(lab, value)
                self.analyst_cells.add(lab)
                self.events["analyst_held_edits"] += 1
                done += 1
            if done >= n:
                return

    # ── the end of the day ──
    def settle_fleet(self, hours=2.0):
        """Two quiet hours (no prints, no faults, roads up, LabCore answering)
        for everything held to drain."""
        self.server.set_roads("up")
        self.base_plan = self.gw.plan = None
        for _ in range(int(hours * HOUR)):
            self.k += 1
            for b in self.benches:
                self.current = b
                b.poll(self.now)
                b.settle()
                self.restart_dead()
        self.current = None
        return self.measure()

    def store_acked(self):
        path = self.server.app.config["LEM_STORE"]
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        try:
            rows = con.execute("SELECT machine_uid, bench_epoch, acked_seq FROM "
                               "bench_cursor").fetchall()
        finally:
            con.close()
        return {"%s|%s" % (u, e): int(a) for u, e, a in rows}

    def store_rows(self, uid):
        path = self.server.app.config["LEM_STORE"]
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute("SELECT id, lab_id, detail FROM lem_machine_log "
                               "WHERE kind='run' AND machine_uid=? ORDER BY id",
                               [uid]).fetchall()
            ann = {}
            for a in con.execute("SELECT log_id, label FROM log_annotation "
                                 "ORDER BY id"):
                ann.setdefault(a["log_id"], []).append(a["label"])
        finally:
            con.close()
        out = []
        for r in rows:
            labels = ann.get(r["id"], [])
            hidden = bool(labels) and labels[-1] in HIDING
            out.append(_row(r["lab_id"], r["detail"], "Density", set(labels),
                            hidden))
        return out

    def measure(self):
        cells = self.gw.results("Density")
        acked = self.store_acked()
        per = {}
        for b in self.benches:
            led = ledger_tally(b.ledger.truth(), self.store_rows(b.uid))
            res = ledger_results(b.ledger, cells)
            recs = b.journal_records()
            epoch = b.m._journal.epoch
            disk_max = max([int(r.get("seq") or 0) for r in recs
                            if r.get("epoch") == epoch] or [0])
            store_has = acked.get("%s|%s" % (b.uid, epoch), 0)
            open_runs = len(list(b.m._journal.open_runs()))
            per[b.uid] = {
                "source": b.source, "prints": len(b.ledger),
                "lost": led["lost"], "dup": led["dup"],
                "labelled_dup": led["labelled_dup"],
                "unlabelled_dup": led["dup"] - led["labelled_dup"],
                "res_lost": res["res_lost"], "res_wrong": res["res_wrong"],
                "journal_max_seq_on_disk": disk_max,
                "store_acked": store_has,
                "bench_acked": b.m._journal.acked,
                "open_runs": open_runs,
                "drained": (disk_max > 0 and store_has == disk_max
                            and b.m._journal.acked == disk_max
                            and open_runs == 0),
                "kills": dict(b.kills), "restarts": b.restarts}
        srv = self.server
        totals = {k: sum(p[k] for p in per.values())
                  for k in ("prints", "lost", "dup", "labelled_dup",
                            "unlabelled_dup", "res_lost", "res_wrong",
                            "restarts")}
        out = {
            "benches": len(self.benches), "seed": self.seed,
            "simulated_hours": self.hours, "polls_per_bench": self.k + 1,
            "totals": totals,
            "analyst_overwritten": sum(1 for v in
                                       self.gw.analyst_overwritten.values()
                                       if v),
            "expect_audit_hits": sum(1 for v in
                                     self.gw.expect_audit_hits.values() if v),
            "injected_a5": self.gw.injected,
            "benches_drained": sum(1 for p in per.values() if p["drained"]),
            "not_drained": sorted(u for u, p in per.items()
                                  if not p["drained"]),
            "faults": dict(self.events),
            "kills_fired": sum(v for k, v in self.events.items()
                               if k.startswith("kill:")),
            "kills_restarted": sum(sum(b.kills.values())
                                   for b in self.benches),
            "restore": getattr(self, "restore_effect", None),
            "sync_answers": {str(k): v for k, v in
                             sorted(srv.sync_answers.items())},
            "road_outcomes": {"%s:%s" % k: v for k, v in
                              sorted(srv.outcomes.items())},
            "roads_used": srv.roads_used(),
            "records_resent": srv.records_resent,
            "lem_requests_on_poll_thread": srv.poll_thread_requests,
            "conflicts": self._store_count("result_conflict"),
            "per_bench": per}
        return out

    def _store_count(self, table):
        path = self.server.app.config["LEM_STORE"]
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        try:
            return con.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
        finally:
            con.close()


def verdict(out):
    """[] when the soak meets its bar, else what failed, in words."""
    bad = []
    t = out["totals"]
    for k in ("lost", "unlabelled_dup", "res_lost", "res_wrong"):
        if t[k]:
            bad.append("%s %d" % (k, t[k]))
    if out["analyst_overwritten"]:
        bad.append("%d analyst value(s) overwritten (no A5 race was injected)"
                   % out["analyst_overwritten"])
    if out["benches_drained"] != out["benches"]:
        bad.append("not drained: %s" % ", ".join(out["not_drained"]))
    if out["lem_requests_on_poll_thread"]:
        bad.append("%d LEM requests on the poll thread"
                   % out["lem_requests_on_poll_thread"])
    return bad


def proof_of_faults(out, hours):
    """The soak must have been a hard day, not a quiet one: every scheduled
    kind of fault fired. Returns what did not (a harness failure, never a
    pass). A short smoke soak (< 24 h) is held to the same list."""
    f = out["faults"]
    missing = []
    if not any(f.get("kill:" + p) for p in KILL_POINTS):
        missing.append("no fault-point kill fired")
    if not any(f.get("kill:labcore_" + a) for a in LABCORE_KILLS):
        missing.append("no LabCore kill fired")
    if out["kills_fired"] != out["kills_restarted"] or \
            out["kills_fired"] != out["totals"]["restarts"]:
        missing.append("kills fired %d, benches restarted for a kill %d, "
                       "restarts %d — each kill is one restart"
                       % (out["kills_fired"], out["kills_restarted"],
                          out["totals"]["restarts"]))
    for need in ("both_dark", "storm", "labcore_refused", "server_reboot",
                 "restore", "flap", "analyst_edits", "analyst_held_edits",
                 "backups"):
        if not f.get(need):
            missing.append("no " + need)
    if not (out.get("restore") or {}).get("acked_rolled_back"):
        missing.append("the restore rolled no bench's cursor back")
    if not int(out["sync_answers"].get("409", 0)):
        missing.append("no 409 cursor answer after the restore")
    if f.get("restore_unwitnessed"):
        missing.append("the restore was unwitnessed")
    if out["roads_used"] != ["A", "B"]:
        missing.append("roads used %s, not both" % out["roads_used"])
    return missing
