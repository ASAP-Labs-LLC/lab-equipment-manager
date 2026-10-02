"""The scenario world: the baseline `run_faults.Ctx`, plus truth, kills and roads.

`World` subclasses the baseline Ctx (imported read-only) so a baseline scenario
function runs UNCHANGED against it: gate.py points `run_faults.Ctx` at World
and calls the 29 functions exactly as phase 1 did. What World adds:

* **Truth.** `self.printed` becomes a dict that registers every assignment in
  the Ledger. Phase 1's scenarios record a genuine print by `emit()` OR by
  assigning `c.printed[lab] = value` directly (R1, R3–R6 do), so hooking the
  dict — not `emit` — is the only way to see every genuine print without
  editing them. `emit_line(lab, value)` registers a print whose content
  repeats an earlier one (X1, R7p, L2), which a dict keyed by lab cannot.

* **Kill points.** `kill_at(name, n)` replaces the module-level
  `fault_point` hook with one that raises the baseline's `Kill` at the n-th hit.
  `poll()` already turns Kill into a restart. A kill that was armed and never
  reached FAILS the scenario: a kill scenario that did not kill measured
  nothing.

* **Serial through the reader.** Phase 1's serial scenarios hand frames to
  the poll by patching `m._ingest`, which is the only thing v3.9 allows: its
  reader opens a real port. A target whose module has `_open_serial_reader`
  (v4) gets a reader over a harness port instead, and every frame goes through
  the reader's OWN frame handling — bytes, then an idle gap, which is what
  completes a frame on the wire — before the poll drains it. That is where v4
  journals a frame, and where K8r's kill point is: a harness that bypassed the
  reader could neither reach the point nor see the custody it protects. The
  frames arrive one at a time, in time order, so a kill as frame k completes
  leaves frames k+1.. still to arrive after the restart — they have not been
  sent yet — which is why K8r loses exactly the one frame being journaled.

* **Journal surgery.** `before_next_restart(fn)` runs `fn` between a kill and
  the restart (T5 tears the journal's last line there: the power cut), and
  `journal_check()` verifies the journal on disk with an independent CRC
  reader, so "tail repaired" is measured, not taken from the module's word.

* **Roads.** An HServer (lazy; see hserver.py) is installed for every World,
  so a bench that calls out reaches a real app, and one that does not pays
  nothing.

* **The tally.** `gate_tally()` = the phase-1 tally (unchanged, so faults.json
  reproduces) + the §9.4 ledger tally + the counters of §15.6. Where a counter
  cannot be measured on the target, it is None — never 0. "No conflicts" and
  "this target has no conflict record to read" are different sentences.
"""
import json
import os
import re
import shutil
import sqlite3
import zlib
from collections import Counter

from . import env
from .hserver import HServer
from .ledger import Ledger, tally as ledger_tally, results as ledger_results

HIDING = ("replay_duplicate", "import_leftover")


class Unsupported(Exception):
    """The target lacks what this scenario measures. A FAIL, never a skip."""


class KillNeverReached(Exception):
    pass


class LedgerDict(dict):
    def __init__(self, ledger):
        super().__init__()
        self._ledger = ledger

    def __setitem__(self, k, v):
        self._ledger.register(k, v)
        super().__setitem__(k, v)


def make_world(lh, rf, mod, GateGateway, server_factory=None):
    """Bind World to the loaded target. `server_factory(world)` builds the
    Flask app HServer forwards to."""
    # The ORIGINAL phase-1 Ctx, even if run_faults.Ctx already points at a
    # World from an earlier make_world (World over World recurses in tally).
    Ctx = rf.__dict__.setdefault("_gate_original_ctx", rf.Ctx)
    Kill = lh.Kill
    plumbing = hasattr(mod, "fault_point") and \
        hasattr(getattr(mod, "LEMStationModule", object), "_fault_point")
    original_hook = getattr(mod, "fault_point", None)
    reader_seam = hasattr(getattr(mod, "LEMStationModule", object),
                          "_open_serial_reader")

    class World(Ctx):
        mutation = None          # set by the mutation runner
        batch_mode = "baseline"

        def __init__(self, source="single_csv", publish=True, batch_mode=None,
                     n_samples=None):
            self.ledger = Ledger("Density")
            self.home = fresh_world_dirs()
            if original_hook is not None:
                mod.fault_point = original_hook
            self._kill = None
            self._kills_fired = []
            mode = batch_mode or type(self).batch_mode
            # The baseline Ctx builds `self.gw = HGateway()` from run_faults'
            # globals; GateGateway is swapped in there by gate.py.
            rf.HGateway = lambda: GateGateway(batch_mode=mode)
            if n_samples is not None:
                old = rf.N_SAMPLES
                rf.N_SAMPLES = n_samples
            try:
                super().__init__(source, publish)
            finally:
                if n_samples is not None:
                    rf.N_SAMPLES = old
            self.printed = LedgerDict(self.ledger)
            self.server = HServer(lambda: server_factory(self)) \
                if server_factory else None
            if self.server:
                self.server.install()
            self._before_restart = []
            self.torn = None
            self._attach_reader()

        # ── serial, through the module's own reader (v4) ──
        def _attach_reader(self):
            if self.source != "serial" or not reader_seam:
                return
            self._port = _HarnessPort()
            self.m._serial_reader = self.m._open_serial_reader(
                self.m.machine(), port=self._port)
            self._wire_t = 0.0

        def _deliver_frames(self):
            reader = self.m._serial_reader
            while getattr(self, "pending_frames", None):
                frame = self.pending_frames.pop(0)
                self._wire_t += 10.0
                reader._on_bytes(frame.encode("utf-8"), self._wire_t)
                self._wire_t += 10.0                 # the idle gap
                reader._on_idle(self._wire_t)

        def poll(self):
            if self.source != "serial" or not reader_seam:
                return Ctx.poll(self)
            try:
                self._deliver_frames()
                lh.poll(self.m, self.now)
            except Kill:
                self.kills += 1
                self.gw.plan = None
                self.restart()
            self.k += 1

        def restart(self):
            hooks, self._before_restart = self._before_restart, []
            for fn in hooks:
                fn()
            Ctx.restart(self)
            self._attach_reader()

        def before_next_restart(self, fn):
            self._before_restart.append(fn)

        # ── the journal on disk ──
        def journal_dir(self):
            return os.path.join(os.environ["LEM_JOURNAL_DIR"], self.uid)

        def tear_journal_tail(self):
            """The power cut: the last append was written and never fsync'd,
            and only part of its last line reached the disk."""
            d = self.journal_dir()
            segs = sorted(n for n in os.listdir(d) if _SEG.match(n)) \
                if os.path.isdir(d) else []
            if not segs:
                raise RuntimeError("no journal segment to tear in %s — the "
                                   "target wrote no journal" % d)
            p = os.path.join(d, segs[-1])
            with open(p, "rb") as f:
                data = f.read()
            last = data.splitlines(True)[-1]
            cut = len(last) - len(last) // 2
            with open(p, "r+b") as f:
                f.truncate(len(data) - cut)
            # What the power cut LEFT of the line is what the module must find
            # torn, cut off and set aside.
            self.torn = {"segment": segs[-1], "lost_bytes": cut,
                         "left_bytes": len(last) - cut}

        def journal_check(self):
            """Every journal line verified with this harness's own CRC reader;
            `tail_repaired` is True only if the partial line the power cut
            left was cut off AND set aside (one torn-*.bin of exactly its size)
            AND every line left in the journal is clean."""
            d = self.journal_dir()
            bad = lines = 0
            for n in sorted(os.listdir(d)):
                if not _SEG.match(n):
                    continue
                with open(os.path.join(d, n), "rb") as f:
                    for line in f.read().splitlines(True):
                        lines += 1
                        if not _crc_ok(line):
                            bad += 1
            aside = [n for n in os.listdir(d) if n.startswith("torn-")]
            sizes = [os.path.getsize(os.path.join(d, n)) for n in aside]
            left = (self.torn or {}).get("left_bytes")
            return {"journal_lines": lines, "journal_bad_lines": bad,
                    "torn_bytes_left": left, "torn_set_aside": sizes,
                    "tail_repaired": bool(left) and bad == 0 and sizes == [left]}

        # ── instrument ──
        def emit_line(self, lab, value, write=True):
            """A genuine print whose content may repeat an earlier one."""
            line = "%s,%s\n" % (lab, value)
            self.ledger.register(lab, value)
            dict.__setitem__(self.printed, lab, value)
            if write:
                self._write_lines([line])
            return line

        def _write_lines(self, lines):
            if self.source == "single_csv":
                with open(self.path, "a") as f:
                    f.writelines(lines)
            elif self.source == "multi_csv":
                for line in lines:
                    self._mc = getattr(self, "_mc", 100000) + 1
                    with open(os.path.join(self.path, "x%06d.csv" % self._mc), "w") as f:
                        f.write(line)
            else:
                self.pending_frames = getattr(self, "pending_frames", []) + \
                    [l.strip() for l in lines]

        def rewrite(self, lines):
            """The file changes; the instrument printed nothing."""
            with open(self.path, "w") as f:
                f.writelines(lines)

        def lines(self):
            with open(self.path) as f:
                return f.read().splitlines(True)

        def rotate(self, new_lines, keep_as=".1", via_temp=False):
            """The old file is renamed away and a new one (new file identity)
            takes its name. `via_temp` is a trim done as write-temp-then-rename:
            same bytes, but the old content is NOT kept anywhere."""
            if via_temp:
                tmp = self.path + ".tmp"
                with open(tmp, "w") as f:
                    f.writelines(new_lines)
                os.replace(tmp, self.path)
                return
            shutil.move(self.path, self.path + keep_as)
            with open(self.path, "w") as f:
                f.writelines(new_lines)

        def analyst_edit(self, lab, value, test="Density"):
            self.gw.analyst_edit(lab, test, value)
            self.ledger.analyst_set(lab, value)

        def inject_between_read_and_batch(self, lab, value, test="Density"):
            self.ledger.analyst_set(lab, value)
            self.gw.inject_between_read_and_batch(lab, test, value)

        # ── kills ──
        def kill_at(self, name, n=1):
            hits = {"n": 0}
            world = self

            def hook(point):
                if point != name or world._kill is None:
                    return None
                hits["n"] += 1
                if hits["n"] == n:
                    world._kill = None
                    world._kills_fired.append(name)
                    raise Kill("fault point " + name)
                return None
            self._kill = name
            mod.fault_point = hook
            self._kill_hits = hits

        def assert_killed(self, name):
            if name not in self._kills_fired:
                has = plumbing
                why = ("the module has fault-point plumbing but never called %r"
                       % name) if has else "the target has no fault-point plumbing"
                raise KillNeverReached("kill point %r armed and never reached: %s"
                                       % (name, why))

        # ── stored rows ──
        def store_kind(self):
            if self.server is not None and self.server._app is not None \
                    and getattr(self.server._app, "config", {}).get("LEM_STORE"):
                return "lem"
            return "labcore"

        def stored_rows(self, test="Density"):
            if self.store_kind() == "lem":
                rows = self._lem_store_rows(test)
            else:
                rows = self._labcore_rows(test)
            if World.mutation == "T0" and self.store_kind() == "lem":
                # The LEM store is append-only (triggers refuse DELETE), so
                # T0 drops at read — the first row the tally COUNTS. A hidden
                # replay would change no term, and a T0 that cannot move the
                # tally proves nothing.
                for i, r in enumerate(rows):
                    if not r["hidden"]:
                        rows = rows[:i] + rows[i + 1:]
                        break
            return rows

        def _labcore_rows(self, test):
            res = self.gw.fake.read_sql(
                "SELECT rowid AS id, lab_id, detail FROM lem_machine_log "
                "WHERE kind='run' AND machine_uid=? ORDER BY rowid", [self.uid])
            if res.get("error"):
                if "no such table" in res["error"]:
                    return []
                raise RuntimeError("ground-truth read failed: " + res["error"])
            return [_row(r["lab_id"], r["detail"], test, (), False) for r in res["rows"]]

        def _lem_store_rows(self, test):
            path = os.environ.get("LEM_STORE_PATH")
            if not path or not os.path.exists(path):
                raise RuntimeError("the LEM store %r does not exist — cannot "
                                   "tally (a missing store is not an empty one)" % path)
            con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
            con.row_factory = sqlite3.Row
            try:
                rows = con.execute(
                    "SELECT id, lab_id, detail FROM lem_machine_log "
                    "WHERE kind='run' AND machine_uid=? ORDER BY id", [self.uid]).fetchall()
                ann = {}
                for a in con.execute("SELECT log_id, label FROM log_annotation ORDER BY id"):
                    ann.setdefault(a["log_id"], []).append(a["label"])
            finally:
                con.close()
            out = []
            for r in rows:
                labels = ann.get(r["id"], [])
                hidden = bool(labels) and labels[-1] in HIDING
                out.append(_row(r["lab_id"], r["detail"], test, set(labels), hidden))
            return out

        # ── mutation T0 on a LabCore store: a real DELETE ──
        def _apply_t0(self):
            if World.mutation != "T0" or self.store_kind() != "labcore":
                return
            r = self.gw.fake.sql(
                "DELETE FROM lem_machine_log WHERE rowid = (SELECT MIN(rowid) "
                "FROM lem_machine_log WHERE kind='run' AND machine_uid=?)", [self.uid])
            if r.get("error") and "no such table" not in r["error"]:
                raise RuntimeError("T0 delete failed: " + r["error"])
            self._t0_applied = True

        # ── the tally ──
        def tally(self, name, what):
            # The baseline scenario functions end in `c.tally(...)`.
            return self.gate_tally(name, what)

        def gate_tally(self, name, what, test="Density"):
            self._apply_t0()
            rows = self.stored_rows(test)
            base = Ctx.tally(self, name, what)        # phase-1 columns, unchanged
            led = ledger_tally(self.ledger.truth(), rows)
            cells = self.gw.results(test)
            res = ledger_results(self.ledger, cells)
            out = dict(base)
            out.update({"lost": led["lost"], "dup": led["dup"],
                        "labelled_dup": led["labelled_dup"],
                        "labelled_rows": led["labelled_rows"],
                        "truth_prints": led["truth"], "stored_effective": led["stored"]})
            # Ledger results agree with phase-1 res_* whenever no person typed
            # a cell; the ledger version (which knows the analyst) is the one kept.
            out["res_lost"], out["res_wrong"] = res["res_lost"], res["res_wrong"]
            out.update(self.counters())
            return out

        def counters(self):
            gw = self.gw
            srv = self.server
            v2 = srv.v2_syncs() if srv is not None else 0
            return {
                "analyst_overwritten": sum(1 for v in gw.analyst_overwritten.values() if v),
                "expect_audit_hits": (sum(1 for v in gw.expect_audit_hits.values() if v)
                                      if gw.expect_ops else None),
                "injected": gw.injected,
                # The server's store is the authority once a bench delivers to
                # it (P7/P8). Until then the bench journal holds the record of
                # what the results road decided — read here with the harness's
                # OWN CRC reader, never the module's word. None where neither
                # exists (v3.9): "no conflicts" and "no record of conflicts"
                # are different sentences.
                "conflicts": self._decision_count("conflict"),
                "rejected": self._decision_count("rejected"),
                "labcore_ops": gw.ops_total(),
                "v2_syncs": v2,
                "records_resent": srv.records_resent if (srv is not None and v2) else None,
                "roads_used": srv.roads_used() if srv is not None else [],
                "max_sends_per_cell": max(gw.cell_sends.values()) if gw.cell_sends else 0,
                # Re-read prints the journal's store check dropped (v4). None
                # on a target with no journal: "nothing suppressed" and "cannot
                # suppress" are different sentences.
                "suppressed_rereads": getattr(self.m, "_journal_suppressed", None),
            }

        def journal_records(self):
            """Every CRC-valid record in the bench journal, in file order, or
            None when the target keeps no journal (v3.9)."""
            d = self.journal_dir()
            if not os.path.isdir(d):
                return None
            out = []
            for n in sorted(os.listdir(d)):
                if not _SEG.match(n):
                    continue
                with open(os.path.join(d, n), "rb") as f:
                    for line in f.read().splitlines(True):
                        m = _CRC_TAIL.search(line)
                        if not m or not _crc_ok(line):
                            continue
                        out.append(json.loads(line[:m.start()] + b"}"))
            return out

        def _decision_count(self, kind):
            """Conflict CELLS (`conflict`) or parked cells (`rejected`). From
            the LEM store's result_conflict when the bench's records reach it;
            otherwise from the bench journal."""
            stored = None
            if kind == "conflict" and self.store_kind() == "lem":
                stored = self._store_count("result_conflict")
                if stored:
                    return stored
            recs = self.journal_records()
            if recs is None:
                return stored          # the store's 0, or None: unmeasurable
            if kind == "conflict":
                return len({(tuple(r.get("of") or ()), c[0], c[1])
                            for r in recs if r.get("kind") == "conflict"
                            for c in r.get("cells") or ()})
            return sum(1 for r in recs if r.get("kind") == kind)

        def filed_cells(self, lab, test="Density"):
            """How many `filed` records name this cell (None: no journal)."""
            recs = self.journal_records()
            if recs is None:
                return None
            return sum(1 for r in recs if r.get("kind") == "filed"
                       for c in r.get("cells") or ()
                       if c[0] == lab and c[1] == test)

        def _store_count(self, table):
            if self.store_kind() != "lem":
                return None
            con = sqlite3.connect("file:%s?mode=ro" % os.environ["LEM_STORE_PATH"], uri=True)
            try:
                return con.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
            finally:
                con.close()

    return World


_WORLDS = {"n": 0}
_SEG = re.compile(r"^seg-\d{6,}\.jsonl$")
_CRC_TAIL = re.compile(rb',"crc":"([0-9a-f]{8})"\}\n\Z')


def _crc_ok(line):
    """The journal's line format (transfer v4 §3.1), checked without the
    module: canonical JSON whose last field is the CRC32 of the body."""
    m = _CRC_TAIL.search(line)
    if not m:
        return False
    body = line[:m.start()] + b"}"
    return (zlib.crc32(body) & 0xffffffff) == int(m.group(1), 16)


class _HarnessPort:
    """The serial port the reader reads, when the harness drives it: nothing
    ever arrives on its own — `_deliver_frames` hands the reader each frame."""

    def read(self):
        return b""

    def close(self):
        pass


def fresh_world_dirs():
    """Each scenario gets its own AppData, journal and store under the gate's
    temp root. The bench uid is "b1" in every scenario, and a v4 journal lives
    at labstation_dir()/lem_journal/<uid>: shared folders would let one
    scenario's journal answer for the next one's bench. A restart INSIDE a
    scenario keeps them — that is the point of a restart."""
    root = env.root()
    if not root:
        raise RuntimeError("env.activate() was not called — refusing to run a "
                           "world against the real profile")
    _WORLDS["n"] += 1
    home = os.path.join(root, "worlds", "w%04d" % _WORLDS["n"])
    p = {"APPDATA": os.path.join(home, "AppData", "Roaming"),
         "LOCALAPPDATA": os.path.join(home, "AppData", "Local"),
         "LEM_JOURNAL_DIR": os.path.join(home, "journal")}
    for d in p.values():
        os.makedirs(d, exist_ok=True)
    os.makedirs(os.path.join(home, "store"), exist_ok=True)
    p["LEM_STORE_PATH"] = os.path.join(home, "store", "lem_store.sqlite3")
    os.environ.update(p)
    return home


def _row(lab_id, detail, test, labels, hidden):
    try:
        d = json.loads(detail or "{}")
    except ValueError:
        d = {}
    raw = d.get("raw") if isinstance(d.get("raw"), dict) else {}
    vals = d.get("values") if isinstance(d.get("values"), dict) else {}
    value = raw.get(test, vals.get(test))
    if value is None and vals:
        value = json.dumps(vals, sort_keys=True)
    if d.get("origin") == "ambiguous" or d.get("ambiguous_repeat"):
        labels = set(labels) | {"ambiguous_repeat"}
    return {"lab_id": lab_id, "value": value, "labels": labels, "hidden": hidden}
