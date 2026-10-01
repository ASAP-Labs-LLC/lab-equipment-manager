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
import shutil
import sqlite3
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
                # Counted from the server's store when the target has one.
                "conflicts": self._store_count("result_conflict"),
                "rejected": None,
                "labcore_ops": gw.ops_total(),
                "v2_syncs": v2,
                "records_resent": srv.records_resent if (srv is not None and v2) else None,
                "roads_used": srv.roads_used() if srv is not None else [],
                "max_sends_per_cell": max(gw.cell_sends.values()) if gw.cell_sends else 0,
            }

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
