"""M3, M5 and DG2 on the real v4 module and the real v4 server (transfer
§10.3, §10.4, §12.2; piece T-P5), plus K6 and A1 on the legacy road.

  M3   v3.9 server, v4 bench: legacy projection. One bench life on a server
       that answers 404 from before the bench binds — a lost answer to a log
       write (N3), two genuine identical prints in one poll (L2), an analyst
       correcting a filed cell, a clean restart (K6), and a kill right after
       LabCore took a log write (K2's shape). Counted in LabCore, which is
       the record on that floor; and every CREATE/ALTER the bench sent is
       checked against v3.9.0's own declarations (no LabCore schema change).
  M5   v4 server, rolled back to v3.9, then v4 again. Readings synced v2,
       readings projected to LabCore during the rollback (one answer lost),
       then the re-upgrade: the bench finds v2 on its 15-minute re-probe and
       sends its epoch from where LEM's cursor stands, and the server's
       bridge pulls what the rollback wrote to LabCore. Counted in the LEM
       store: every print once.
  DG2  the same rollback with a QC standard printed before it: the fall-back
       copies the last 24 h of QC verdicts and status changes into LabCore
       (the v3.9 floor's only source), and on the re-upgrade the bridge
       recognises every one of them by `detail.jk` — 0 doubled.

  DG2C DG2 with LabStation restarted WHILE the server answers 404 — the
       restart's walk and the fall-back both offer the same records in one
       process (critic, round 2: 3 doubled rows in LabCore).
  M5R  M5 with the same restart mid-rollback.

Every rollback scenario also counts LabCore itself — the v3.9 floor's only
record — by exact duplicate row and by bookkeeping kind (`filed`, `settled`,
... are the journal's notes to itself, never machine events): the store
tally alone cannot see a doubled or a bogus row on the old floor.

Each needs the target's bridge and import tool (P9) for the v4 server's
side; `Unsupported` otherwise.
"""
import json
from collections import Counter

from .world import Unsupported

#: v3.9.0's DDL, whitespace-normalised: the 11 CREATE/ALTER constants
#: `git show v3.9.0:"LEM Station Module/lem_station_module.py"` declares.
V39_DDL = frozenset({
    "ALTER TABLE lem_machine_specs ADD COLUMN correction REAL DEFAULT 0.0",
    "CREATE INDEX IF NOT EXISTS idx_lem_log_ts ON lem_machine_log(ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_lem_log_uid_kind_ts ON "
    "lem_machine_log(machine_uid, kind, ts DESC)",
    "CREATE TABLE IF NOT EXISTS lem_correction_factors (machine_uid TEXT NOT "
    "NULL, test_name TEXT NOT NULL, correction REAL NOT NULL DEFAULT 0.0, "
    "units TEXT, updated_at TEXT, updated_by TEXT, PRIMARY KEY (machine_uid, "
    "test_name))",
    "CREATE TABLE IF NOT EXISTS lem_held_results (machine_uid TEXT PRIMARY "
    "KEY, updated_at TEXT, held TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_machine_config (machine_uid TEXT PRIMARY "
    "KEY, title TEXT NOT NULL, config TEXT, updated_at TEXT, updated_by TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_machine_heartbeat (machine_uid TEXT "
    "PRIMARY KEY, last_poll TEXT, watching TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, ts TEXT, "
    "kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, detail TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_machine_specs (machine_uid TEXT NOT NULL, "
    "test_name TEXT NOT NULL, sample_id TEXT, expected REAL, std_dev REAL, "
    "k REAL, units TEXT, low REAL, high REAL, last_qc_at TEXT, last_qc_value "
    "REAL, last_qc_in_spec INTEGER, correction REAL DEFAULT 0.0, updated_at "
    "TEXT, PRIMARY KEY (machine_uid, test_name))",
    "CREATE TABLE IF NOT EXISTS lem_machine_status (machine_uid TEXT PRIMARY "
    "KEY, title TEXT, status TEXT, reason TEXT, updated_at TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_machine_substatus (machine_uid TEXT "
    "PRIMARY KEY, qc TEXT, pm TEXT, calibration TEXT, updated_at TEXT)",
})

OLD = {"A": "404", "B": "404"}
#: Polls (30 s each) past the bench's 15-minute re-probe of v2.
REPROBE_POLLS = 32


def _need_bridge():
    try:
        import bridge                                   # noqa: F401
        import legacy_import                            # noqa: F401
    except ImportError:
        raise Unsupported("needs the mixed-fleet bridge and the import tool "
                          "(bridge.py, legacy_import.py: P9)")


class _DDLRecorder:
    """Every CREATE/ALTER/DROP the bench sends to LabCore, from the moment it
    is installed, across restarts (each restart re-installs `gw.sql` into
    the new module, and this sits on the gateway instance)."""

    def __init__(self, world, mod):
        self.sent = []
        gw = world.gw
        inner = gw.sql

        def sql(sql, args=None, source="", **kw):
            if str(sql).lstrip().upper().startswith(("CREATE", "ALTER",
                                                     "DROP")):
                self.sent.append(" ".join(str(sql).split()))
            return inner(sql, args, source=source, **kw)
        gw.sql = sql
        mod.__dict__["labcore_sql"] = sql

    def beyond_v39(self):
        return sorted(set(self.sent) - V39_DDL)


def _labcore_rows(c, kind):
    res = c.gw.fake.read_sql("SELECT * FROM lem_machine_log WHERE machine_uid "
                             "= ? AND kind = ? ORDER BY rowid", [c.uid, kind])
    if res.get("error"):
        if "no such table" in res["error"]:
            return []
        raise RuntimeError("ground-truth read failed: " + res["error"])
    return res["rows"]


#: Journal kinds that are the bench's bookkeeping, not machine events: none
#: may ever be a lem_machine_log row (v3.9 wrote none of them).
BOOKKEEPING_KINDS = ("filed", "settled", "conflict", "rejected", "projected",
                     "adoption", "frame", "consumed", "known", "specs",
                     "periodic", "rotation_overlap", "no_snapshot", "ambiguity")


def _labcore_all(c):
    res = c.gw.fake.read_sql("SELECT * FROM lem_machine_log WHERE machine_uid "
                             "= ? ORDER BY rowid", [c.uid])
    if res.get("error"):
        if "no such table" in res["error"]:
            return []
        raise RuntimeError("ground-truth read failed: " + res["error"])
    return res["rows"]


def labcore_audit(c):
    """LabCore's log as the v3.9 floor reads it: rows held more than once
    (exactly the same row — every projected row names its record, so two
    copies of one are never two genuine events), and rows of the journal's
    own bookkeeping kinds."""
    rows = _labcore_all(c)
    seen = Counter((r["ts"], r["kind"], r["lab_id"], r["test_name"],
                    r["value"], r["detail"]) for r in rows)
    kinds = Counter(r["kind"] for r in rows)
    return {"labcore_exact_dup": sum(n - 1 for n in seen.values() if n > 1),
            "labcore_bookkeeping_rows": sum(kinds[k] for k in
                                            BOOKKEEPING_KINDS),
            "labcore_kinds": dict(sorted(kinds.items()))}


def _jk(row):
    try:
        return json.loads(row.get("detail") or "{}").get("jk")
    except ValueError:
        return None


def _verified_store(c):
    """The v4 server's store, imported from LabCore and verified — what the
    server's rollout does before its bridge goes on (§10.1)."""
    import legacy_import
    store = c.lem_store()
    out = legacy_import.Importer(store, c.server_labcore,
                                 sleep=lambda s: None, chunk=1000).run()
    if out.get("state") != "verified":
        raise AssertionError("import did not verify: %s" % out)
    return store


def _pull_all(store, c):
    import bridge
    br = bridge.Bridge(store, c.server_labcore, chunk=1000, clock=lambda: 0.0)
    for _ in range(20):
        if not (br.pull() or {}).get("rows"):
            break
    return br


def m3(W, rf, mod, lab_id):
    c = W(road_modes=OLD)
    ddl = _DDLRecorder(c, mod)
    c.emit(3); c.poll()
    c.emit(3)
    c.gw.plan = rf.nth("sql:machine_log", 1, "raise_after")     # N3
    c.poll()
    lab = lab_id(c.n + 50)
    c.emit_line(lab, "0.8010")
    c.emit_line(lab, "0.8010")                                   # L2
    c.poll()
    c.analyst_edit(lab_id(0), "0.7000")                          # A1
    c.restart()                                                  # K6
    c.emit(2)
    c.gw.plan = rf.nth("sql:machine_log", 1, "kill_after")       # K2
    c.poll()
    c.emit(1); c.poll()
    c.settle()
    t = c.tally("M3 single_csv", "v3.9 server, v4 bench: N3, L2, A1, K6, K2")
    return {"lost": t["lost"], "effective_dup": t["dup"],
            "analyst_overwritten": t["analyst_overwritten"],
            "ddl_beyond_v39": len(ddl.beyond_v39()),
            "ddl_sent": len(set(ddl.sent)),
            "ddl_beyond_v39_statements": ddl.beyond_v39(),
            "went_legacy": getattr(getattr(c.m, "_transfer", None), "mode",
                                   None) == "legacy",
            "store": c.store_kind(), "printed": t["printed"],
            "kills": t["kills"], "restarts": t["restarts"]}


def m6r(W, rf, mod):
    """M6 with the REAL module (the M6 row drives a model bench): a v4 bench
    that has only ever known a v3.9 server projects to LabCore — one answer
    lost — then the server goes v4 (import, verify, bridge on). The bench
    finds v2 on its 15-minute re-probe and sends its epoch from seq 1; the
    bridge pulls the projected rows. Every print once in the store."""
    _need_bridge()
    c = W(road_modes=OLD)
    for _ in range(2):
        c.emit(3); c.poll()
    c.emit(2)
    c.gw.plan = rf.nth("sql:machine_log", 1, "raise_after")     # N3
    c.poll()
    c.poll()
    went_legacy = c.m._transfer.mode == "legacy"
    projected = len([r for r in _labcore_rows(c, "run") if _jk(r)])
    store = _verified_store(c)                                   # server v4
    _pull_all(store, c)                  # the bridge first: rows, then sync
    c.server.set_roads("up")
    for _ in range(REPROBE_POLLS):
        c.poll()
    c.emit(2); c.poll()
    _pull_all(store, c)
    c.poll()
    t = c.tally("M6r single_csv", "v3.9 server to v4, real bench in projection")
    return {"lost": t["lost"], "effective_dup": t["dup"],
            "went_legacy": went_legacy,
            "back_on_v2": c.m._transfer.mode == "v2",
            "labcore_rows_projected": projected, "store": c.store_kind(),
            "printed": t["printed"]}


def m5(W, rf, mod, restart=False):
    _need_bridge()
    c = W()
    store = _verified_store(c)
    for _ in range(2):
        c.emit(3); c.poll()
    synced = c.server.v2_syncs()
    c.server.set_roads("404")                                    # rollback
    c.emit(3)
    if restart:
        # LabStation closed and reopened mid-rollback, before any poll took
        # the old road: the new process hears the 404, falls back AND runs
        # its restart walk over the same journal.
        c.restart()
    c.poll()
    c.emit(2)
    c.gw.plan = rf.nth("sql:machine_log", 1, "raise_after")     # N3 on it
    c.poll()
    c.poll()
    went_legacy = c.m._transfer.mode == "legacy"
    projected = len([r for r in _labcore_rows(c, "run") if _jk(r)])
    audit = labcore_audit(c)
    c.server.set_roads("up")                                     # re-upgrade
    for _ in range(REPROBE_POLLS):
        c.poll()
    c.emit(2); c.poll()
    _pull_all(store, c)
    c.poll()
    t = c.tally("M5R single_csv" if restart else "M5 single_csv",
                "v4 server rolled back to v3.9, then v4"
                + (" (LabStation restarted mid-rollback)" if restart else ""))
    return {"lost": t["lost"], "effective_dup": t["dup"], **audit,
            "went_legacy": went_legacy, "back_on_v2":
                c.m._transfer.mode == "v2",
            "labcore_rows_projected_in_rollback": projected,
            "v2_syncs_before_rollback": synced, "store": c.store_kind(),
            "printed": t["printed"]}


def dg2(W, rf, mod, restart=False):
    _need_bridge()
    c = W()
    store = _verified_store(c)
    c.emit(2); c.poll()
    # The QC standard in force on this bench (what the floor's configuration
    # gives it): its prints are verdicts — `qc` rows, not runs.
    c.m.machine().tests = [mod.TestSpec(
        name="Density", value_col="Density", expected=0.85, std_dev=0.05,
        k=2.0, sample_id="QC-D")]
    c._write_lines(["QC-D,0.8500\n"])
    c.poll()
    c.emit(1); c.poll()
    store_qc_before = _store_count(store, c.uid, "qc")
    c.server.set_roads("404")                                    # rollback
    if restart:
        # Restarted while the server answers 404, before the back-fill
        # landed: restore_state -> 404 -> fall-back, AND the restart walk.
        c.restart()
    c.poll()
    c.poll()
    audit = labcore_audit(c)
    qc_back = [r for r in _labcore_rows(c, "qc") if _jk(r)]
    state_back = [r for r in _labcore_rows(c, "status_change") if _jk(r)]
    runs_back = [r for r in _labcore_rows(c, "run") if _jk(r)]
    # A second fall-back over the same journal (the server flapped): keyed,
    # so nothing more lands.
    c.m._journal.update_meta(dg2_due=c.m._journal.acked)
    c.m._v2_fell_back(c.m.machine(), c.m._journal, [])
    c.poll()
    qc_again = len([r for r in _labcore_rows(c, "qc") if _jk(r)])
    c.server.set_roads("up")                                     # re-upgrade
    for _ in range(REPROBE_POLLS):
        c.poll()
    _pull_all(store, c)
    c.poll()
    qc = _store_rows(store, c.uid, "qc")
    states = _store_rows(store, c.uid, "status_change")
    dup = _dups(qc) + _dups(states)
    return {"dup_on_reupgrade": dup, **audit,
            "qc_copied_back": len(qc_back), "state_copied_back": len(state_back),
            "runs_copied_back": len(runs_back),
            "qc_rows_after_second_fallback": qc_again,
            "store_qc_rows_before_rollback": store_qc_before,
            "store_qc_rows_after": len(qc),
            "back_on_v2": c.m._transfer.mode == "v2"}


def _store_rows(store, uid, kind):
    res = store.read_sql("SELECT ts, kind, lab_id, test_name, value, detail "
                         "FROM lem_machine_log_effective WHERE machine_uid = ? "
                         "AND kind = ?", [uid, kind])
    if res.get("error"):
        raise RuntimeError("store read failed: " + res["error"])
    return res["rows"]


def _store_count(store, uid, kind):
    return len(_store_rows(store, uid, kind))


def _dups(rows):
    """Rows of one record (one `jk`, or one identical row) held more than
    once."""
    seen = Counter()
    for r in rows:
        key = _jk(r) or (r["ts"], r["kind"], r["lab_id"], r["test_name"],
                         r["value"], r["detail"])
        seen[key] += 1
    return sum(n - 1 for n in seen.values() if n > 1)
