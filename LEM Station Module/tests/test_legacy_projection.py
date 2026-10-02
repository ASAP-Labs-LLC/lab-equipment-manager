"""Legacy projection (transfer v4 §10.3, piece T-P5): module v4 on TODAY's
server.

Ryan's rollout ships the bench before the server (D8): a v4 module on a
LabStation while LEM is still v3.9 — which answers every /api/v2 path with a
404. On that floor LabCore's `lem_*` tables are still the only place anyone
reads a bench's world from, so the bench must keep writing them, with
today's SQL shapes and with NO LabCore schema change. What changes is HOW the
machine log is written:

  * THE RECORD FIRST. Every reading and every operator event (note,
    override, PM, calibration, a given-up held reading, a status change) is
    appended to the bench journal and fsync'd before LabCore hears of it.
  * AN EXACT KEY. Each machine-log row carries `detail.jk = epoch:seq`, the
    journal record it projects, and goes in through
        INSERT … SELECT … FROM (VALUES …) WHERE NOT EXISTS
            (machine_uid, kind, ts, detail [, lab_id, test_name, value])
    which LabCore answers from `idx_lem_log_uid_kind_ts`, an index v3.9
    already declares. Because `detail` holds `jk`, the key is the record:
      - a write whose ANSWER was lost and is sent again lands nothing twice
        (L1, today N3: 3 duplicate rows);
      - two genuine prints of one sample with one value in one poll are two
        records, two keys, two rows (L2 — the key of proposal A left `value`
        and the record out, and dropped the second print).
    A restart re-sends what the journal says never landed, through the same
    key, so it needs no "what already landed?" read first.
  * `projected_seq`. The journal's meta says how far LabCore holds the
    bench's records — everything at or below it is in LabCore's log (or is
    bookkeeping that has no row). A restart re-projects from there.
  * DG2. A bench that synced v2 with a v4 server, which is then rolled back
    to v3.9, copies its last 24 h of QC verdicts and status changes back into
    LabCore, so the rolled-back floor shows current QC. Keyed the same way,
    so a second rollback, or the v4 server's bridge pulling them back in on
    the re-upgrade, doubles nothing.

What these tests do NOT claim: the server side of `jk` linking (the bridge and
the v2 ingest) is pinned by the web server's own tests, and the road
switching (404 only, 15-minute re-probe) by test_bench_uploader.py and
test_v2_road.py. The module here is on the legacy road from the start: see
`old_server.py` (no fake LEM installed means "LEM already answered 404").
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta

import pytest

import lem_station_module as mod
from test_journal_custody import (Bench, Kill, Lab, T0, UID, lab_id, line,
                                  runs_in_journal)


# Every DDL statement v3.9.0 can send to LabCore, whitespace-normalised,
# copied from `git show v3.9.0:"LEM Station Module/lem_station_module.py"`
# (the 11 CREATE/ALTER constants it declares). "No LabCore schema change"
# means a legacy bench sends nothing outside this list.
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


class FaultLab(Lab):
    """`Lab` with the other faults a LabCore answer can carry, on the
    machine-log INSERT only:

      lose     the INSERT executes, then the answer never arrives (a timeout
               raised to the caller) — N3, the write LANDED
      kill     the INSERT executes, then the process dies — K2's shape
      refuse   LabCore's busy refusal, nothing executed

    Each fires on the n-th machine-log INSERT counted from when it is set."""

    def __init__(self, samples=()):
        super().__init__(samples)
        self.log_fault = None          # (mode, n)
        self.statements = []           # every SQL statement, in order

    def sql(self, sql, args=None, source="LabStation", timeout=None):
        self.statements.append((sql, list(args or [])))
        fault = self.log_fault
        if fault and "INSERT INTO lem_machine_log" in sql:
            mode, n = fault
            if n > 1:
                self.log_fault = (mode, n - 1)
            else:
                self.log_fault = None
                if mode == "refuse":
                    self.ops += 1
                    return {"error": "LabCore is busy", "busy": True,
                            "retry_after": 1}
                res = super().sql(sql, args, source, timeout)
                assert not res.get("error"), res
                if mode == "lose":
                    raise TimeoutError("the answer was lost (the write landed)")
                raise Kill("after the log write")
        return super().sql(sql, args, source, timeout)

    def read_sql(self, sql, args=None, timeout=None):
        self.statements.append((sql, list(args or [])))
        return super().read_sql(sql, args, timeout)

    def rows(self, kind=None):
        res = Lab.read_sql(self, "SELECT * FROM lem_machine_log WHERE "
                           "machine_uid = ?" + (" AND kind = ?" if kind else "")
                           + " ORDER BY rowid", [UID] + ([kind] if kind else []))
        assert not res.get("error"), res
        return res["rows"]


def legacy_bench(tmp_path, monkeypatch, **kw):
    b = Bench.__new__(Bench)
    real = Lab

    # Bench builds its own `Lab`; build it as a FaultLab instead.
    import test_journal_custody as tjc
    monkeypatch.setattr(tjc, "Lab", FaultLab)
    try:
        Bench.__init__(b, tmp_path, monkeypatch, **kw)
    finally:
        monkeypatch.setattr(tjc, "Lab", real)
    assert b.m._transfer.mode == "legacy"
    return b


def jks(rows):
    return [json.loads(r["detail"]).get("jk") for r in rows]


# ── L1 / L2: the exact key ───────────────────────────────────────────────────

def test_L1_a_log_write_whose_answer_was_lost_lands_once(qapp, tmp_path,
                                                         monkeypatch):
    """N3 on the legacy road. LabCore stored the poll's rows, the answer was
    lost, the bench put the rows back and sent them again next poll. v3.9 and
    the integration build both stored them twice (gate L1: 3 duplicates). The
    resend now matches its own rows on the exact key and inserts nothing."""
    b = legacy_bench(tmp_path, monkeypatch)
    b.emit(3)
    b.poll()
    b.emit(3)
    b.lab.log_fault = ("lose", 1)
    b.poll()
    for _ in range(3):
        b.poll()
    assert b.lab.log_runs() == [lab_id(i) for i in range(6)]
    keys = jks(b.lab.rows("run"))
    assert len(set(keys)) == 6 and None not in keys


def test_L2_two_genuine_prints_of_one_sample_in_one_poll_both_land(
        qapp, tmp_path, monkeypatch):
    """The instrument printed the same sample with the same value twice
    between two polls. That is two readings. A key of (uid, kind, ts, lab_id,
    test) — or one without the record — calls the second a resend and drops
    it; `jk` makes them two records. And when the answer to that very write is
    lost, the resend still adds nothing: two rows, not one and not four."""
    b = legacy_bench(tmp_path, monkeypatch)
    b.emit(3)
    b.poll()
    with open(b.path, "a") as f:
        f.write(line(10) + "\n" + line(10) + "\n")
    b.lab.log_fault = ("lose", 1)
    b.poll()
    for _ in range(3):
        b.poll()
    runs = b.lab.rows("run")
    assert [r["lab_id"] for r in runs] == [lab_id(i) for i in range(3)] + \
        [lab_id(10), lab_id(10)]
    twins = runs[3:]
    assert twins[0]["ts"] == twins[1]["ts"]
    assert twins[0]["value"] == twins[1]["value"]
    assert jks(twins)[0] != jks(twins)[1]


def test_each_row_names_the_journal_record_it_projects(qapp, tmp_path,
                                                       monkeypatch):
    """`detail.jk` is the record's ref, `epoch:seq` — the same key the v4
    server stores a v2 sync under, so when this bench later reaches a v4
    server and sends its epoch from seq 1, every row already in LabCore is
    recognised as that record (M6) instead of being stored a second time.
    The detail is today's detail with ONE key added, nothing else changed."""
    b = legacy_bench(tmp_path, monkeypatch)
    b.emit(3)
    b.poll()
    journal = b.journal()
    by_lab = {r["lab_id"]: r for r in runs_in_journal(journal)}
    for row in b.lab.rows("run"):
        rec = by_lab[row["lab_id"]]
        (args,) = rec["log"]
        assert row["detail"] == mod.projection_detail(args[6], "%s:%d" % (
            rec["epoch"], rec["seq"]))
        today = json.loads(args[6])
        assert json.loads(row["detail"]) == dict(today, jk="%s:%d" % (
            rec["epoch"], rec["seq"]))
        assert [row[c] for c in ("machine_uid", "ts", "kind", "lab_id",
                                 "test_name", "value")] == args[:6]


def test_the_key_recipe_is_the_servers():
    """`jk` is a key recipe (§13: changing one is MAJOR). The bench and the
    server must write the same bytes: bench_api._row_detail parses the
    detail, sets `jk` last and re-dumps with json.dumps defaults. Pinned here
    on literal bytes; LEM Web Server/tests/test_legacy_projection_link.py
    holds the server's function to the same answers."""
    assert mod.projection_detail('{"values": {"Density": "0.8"}}', "e1:7") \
        == '{"values": {"Density": "0.8"}, "jk": "e1:7"}'
    assert mod.projection_detail("{}", "e1:8") == '{"jk": "e1:8"}'
    assert mod.projection_detail("", "e1:9") == '{"jk": "e1:9"}'
    # A detail that is not JSON is today's text, kept as it is.
    assert mod.projection_detail("not json", "e1:10") == "not json"


# ── the statement ────────────────────────────────────────────────────────────

def test_the_statement_is_one_keyed_insert_per_hundred_rows(qapp, tmp_path,
                                                            monkeypatch):
    """250 prints in one poll (and the status change they cause): 3
    statements of at most 100 rows, each an INSERT … SELECT … WHERE NOT
    EXISTS with 7 bound values a row — 700 at most, under the 999 an old
    SQLite allows. Repeating the key's values in the subquery would be 11 a
    row and 1,100 a statement: refused whole."""
    b = legacy_bench(tmp_path, monkeypatch, samples=300)
    b.emit(250)
    b.poll()
    inserts = [(s, a) for s, a in b.lab.statements
               if "INSERT INTO lem_machine_log" in s]
    sizes = [len(a) // 7 for _s, a in inserts]
    assert sizes[:2] == [100, 100] and len(sizes) == 3 and sizes[2] >= 50
    assert sum(sizes) == len(b.lab.rows())
    for s, a in inserts:
        flat = " ".join(s.split())
        assert flat.startswith("INSERT INTO lem_machine_log (machine_uid, ts, "
                               "kind, lab_id, test_name, value, detail) SELECT")
        assert "WHERE NOT EXISTS" in flat
        assert len(a) <= 700
    assert len(b.lab.log_runs()) == 250


def test_labcore_answers_the_key_from_the_index_v39_declared():
    """The NOT EXISTS probe must be a seek, not a scan: LabCore's log is a
    258k-row table on an SMB share behind an 8 s read watchdog. Asked of a
    real SQLite holding v3.9's exact table and indexes."""
    con = sqlite3.connect(":memory:")
    con.execute(mod.LOG_TABLE_DDL)
    for ddl in mod.LOG_INDEX_DDL:
        con.execute(ddl)
    for i in range(500):
        con.execute(mod.LOG_INSERT_SQL, ["u%d" % (i % 9), "2026-10-01T09:%02d"
                                         % (i % 60), "run", "L", "", "", "{}"])
    con.execute("ANALYZE")
    rows = [["b1", "2026-10-01T09:00:00", "run", "L1", "", "",
             '{"jk": "e:%d"}' % i] for i in range(3)]
    sql, args = mod.build_projection_batch(rows)
    plan = " | ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + sql,
                                                 args))
    assert "SEARCH l USING INDEX idx_lem_log_uid_kind_ts" in plan, plan
    assert "SCAN l" not in plan, plan
    assert con.execute(sql, args).rowcount == 3
    assert con.execute(sql, args).rowcount == 0      # the resend: nothing


def test_identical_rows_of_two_records_in_one_statement_both_land():
    """SQLite computes an INSERT … SELECT that reads its own table before it
    inserts, so the NOT EXISTS of the second of two rows does not see the
    first. Pinned, because the L2 guarantee rests on it when two prints are
    byte-identical apart from `jk` … and on `jk` when they are not."""
    con = sqlite3.connect(":memory:")
    con.execute(mod.LOG_TABLE_DDL)
    row = ["b1", "t", "run", "L1", "", "", '{"jk": "e:1"}']
    sql, args = mod.build_projection_batch([row, list(row)])
    assert con.execute(sql, args).rowcount == 2


def test_no_ddl_beyond_v39s_own_declarations(qapp, tmp_path, monkeypatch):
    """No LabCore schema change: over a legacy bench's life — bind, prints, a
    lost answer, an operator's note, a restart — every CREATE/ALTER it sends
    is one v3.9.0 already sent. (Proposal C's ALTER TABLE + partial unique
    index on a 258k-row production table over SMB is what this replaces.)"""
    b = legacy_bench(tmp_path, monkeypatch)
    b.emit(3)
    b.poll()
    b.lab.log_fault = ("lose", 1)
    b.emit(2)
    b.poll()
    b.m._log_event("comment", detail={"note": "lamp replaced"})
    b.m._flush_events_now()
    b.restart()
    b.emit(1)
    b.poll()
    b.poll()
    sent = {" ".join(s.split()) for s, _a in b.lab.statements
            if s.lstrip().upper().startswith(("CREATE", "ALTER", "DROP"))}
    assert sent, "the bench declared nothing at all: the test saw no session"
    assert sent <= V39_DDL, sorted(sent - V39_DDL)


# ── restarts: re-send, never ask first ───────────────────────────────────────

def test_a_kill_between_log_batches_resends_the_rest_once_without_a_read(
        qapp, tmp_path, monkeypatch):
    """K2's shape on the legacy road: 250 prints, three log statements, the
    process dies right after LabCore took the SECOND. The journal still owes
    all 250 (none was marked projected: the mark comes after the answer). The
    restart sends them again through the exact key — the 200 that landed add
    nothing — so it no longer needs the read that asked LabCore which rows
    were already there."""
    b = legacy_bench(tmp_path, monkeypatch, samples=300)
    b.emit(250)
    b.lab.log_fault = ("kill", 2)
    b.poll()                                   # dies; Bench restarts it
    mark = len(b.lab.statements)
    b.poll()
    b.poll()
    assert sorted(b.lab.log_runs()) == sorted(lab_id(i) for i in range(250))
    asked = [s for s, _a in b.lab.statements[mark:]
             if s.lstrip().upper().startswith("SELECT")
             and "lem_machine_log" in s and " ts IN" in s]
    assert asked == []


def test_K6_a_clean_restart_on_the_legacy_road_doubles_nothing(
        qapp, tmp_path, monkeypatch):
    """K6 (v3.9: 30 duplicate rows, 30 duplicate cell sends) with the bench
    on today's server: a clean restart re-reads nothing and sends nothing
    twice."""
    b = legacy_bench(tmp_path, monkeypatch)
    for _ in range(10):
        b.emit(3)
        b.poll()
    b.restart()
    for _ in range(3):
        b.poll()
    assert sorted(b.lab.log_runs()) == [lab_id(i) for i in range(30)]
    assert len(b.lab.cell_sends) == len(set(b.lab.cell_sends)) == 30


def test_A1_an_analysts_correction_survives_a_restart_on_the_legacy_road(
        qapp, tmp_path, monkeypatch):
    """A1 (v3.9: 5 of 5 corrected cells overwritten by the restart's replay)
    with the bench on today's server."""
    b = legacy_bench(tmp_path, monkeypatch)
    b.emit(5)
    b.poll()
    with b.lab.lock:
        for i in range(5):
            b.lab.con.execute("UPDATE sample_tests SET result = ? WHERE "
                              "lab_id = ?", ["0.7%03d" % i, lab_id(i)])
        b.lab.con.commit()
    b.restart()
    for _ in range(3):
        b.poll()
    assert b.lab.results() == {lab_id(i): "0.7%03d" % i for i in range(5)}


# ── operator events: journaled, then projected ───────────────────────────────

def _events(b, kind):
    return [r for r in b.journal()._scan() if r.get("kind") == kind]


def test_an_operators_note_is_journaled_and_lands_once_when_the_answer_is_lost(
        qapp, tmp_path, monkeypatch):
    """A note is a record like a reading. On the legacy road v3.9 queued it
    in memory only and wrote it as a plain INSERT — a lost answer doubled it,
    a kill before the write lost it. It is journaled first and keyed."""
    b = legacy_bench(tmp_path, monkeypatch)
    b.poll()
    b.lab.log_fault = ("lose", 1)
    b.m._log_event("comment", detail={"note": "lamp replaced"})
    try:
        b.m._flush_events_now()
    except Exception:                       # noqa: BLE001 — the lost answer
        pass
    b.poll()
    b.poll()
    rows = b.lab.rows("comment")
    assert len(rows) == 1
    (rec,) = _events(b, "comment")
    assert json.loads(rows[0]["detail"]) == {
        "note": "lamp replaced", "jk": "%s:%d" % (rec["epoch"], rec["seq"])}


def test_a_note_that_never_reached_labcore_lands_after_a_restart(
        qapp, tmp_path, monkeypatch):
    """The process dies with the note journaled and LabCore refusing: the
    restart projects it from `projected_seq`, once."""
    b = legacy_bench(tmp_path, monkeypatch)
    b.poll()
    b.lab.log_fault = ("refuse", 1)
    b.m._log_event("override", detail={"status": "SERVICE",
                                       "comment": "lamp out"})
    b.m._flush_events_now()
    assert b.lab.rows("override") == []
    j = b.journal()
    (rec,) = _events(b, "override")
    assert int(j.meta("projected_seq") or 0) < rec["seq"]
    b.restart()
    b.poll()
    b.poll()
    rows = b.lab.rows("override")
    assert len(rows) == 1
    assert json.loads(rows[0]["detail"])["jk"] == "%s:%d" % (rec["epoch"],
                                                             rec["seq"])


def test_a_status_change_is_a_state_record_and_its_row_is_todays(
        qapp, tmp_path, monkeypatch):
    """On the v2 side a status change is a `state` record (the v4 server
    turns it into the status_change row and the floor's status). On the
    legacy road it is journaled as the same record, so the v4 server reads
    it the same way after M6, and projected as today's status_change row."""
    b = legacy_bench(tmp_path, monkeypatch)
    b.emit(1)
    b.poll()
    states = _events(b, "state")
    rows = b.lab.rows("status_change")
    assert len(states) == len(rows) >= 1
    for rec, row in zip(states, rows):
        detail = json.loads(row["detail"])
        assert detail["jk"] == "%s:%d" % (rec["epoch"], rec["seq"])
        assert detail["to"] == rec["status"]
        assert {"from", "to", "reason"} <= set(detail)


# ── projected_seq ────────────────────────────────────────────────────────────

def test_projected_seq_follows_what_labcore_holds(qapp, tmp_path, monkeypatch):
    """`projected_seq` is the journal's statement of how far LabCore holds
    the bench's record: it reaches the end once a poll's rows land, and it
    stops short of the first record LabCore refused — never past it."""
    b = legacy_bench(tmp_path, monkeypatch)
    b.emit(3)
    b.poll()
    j = b.m._journal
    assert int(j.meta("projected_seq")) == j.last_seq()
    first_owed = j.last_seq() + 1
    b.lab.log_fault = ("refuse", 1)
    b.emit(2)
    b.poll()
    assert int(j.meta("projected_seq")) < first_owed
    b.poll()
    assert int(j.meta("projected_seq")) == j.last_seq()
    assert len(b.lab.log_runs()) == 5


# ── DG2 and M5: a v4 server rolled back to v3.9 ──────────────────────────────

NOW = datetime(2026, 10, 2, 12, 0, 0)


def _qc_run(ts, i):
    stamp = (NOW - ts).isoformat()
    return {"kind": "run", "origin": "live", "src": "single_csv",
            "pk": "pk-qc-%d" % i, "lab_id": "QC1", "values": {"RON": "91.0"},
            "raw": {}, "corrections": {}, "row": {}, "ts": _aware(NOW - ts),
            "log": [[UID, stamp, "qc", "QC1", "RON", "91",
                     json.dumps({"verdict": "PASS", "n": i})]]}


def _sample_run(ts, i):
    stamp = (NOW - ts).isoformat()
    return {"kind": "run", "origin": "live", "src": "single_csv",
            "pk": "pk-s-%d" % i, "lab_id": lab_id(i), "values": {},
            "raw": {}, "corrections": {}, "row": {}, "ts": _aware(NOW - ts),
            "log": [[UID, stamp, "run", lab_id(i), "", "",
                     json.dumps({"values": {"Density": "0.8"}})]]}


def _state(ts, status):
    return {"kind": "state", "status": status, "reason": "r", "from": "",
            "sub": {"qc": status, "pm": "GREEN", "calibration": "GREEN"},
            "ts": _aware(NOW - ts)}


def _aware(dt):
    return dt.astimezone().isoformat(timespec="seconds")


def _rolled_back_bench(tmp_path, monkeypatch, unacked=()):
    """A bench that has synced with a v4 server (journal acked, handshake
    recorded), whose records include QC verdicts and status changes from 30 h
    and 2 h ago, and whose server has just answered 404."""
    b = legacy_bench(tmp_path, monkeypatch)
    b.poll()                       # the legacy road declares its tables
    j = b.m._journal
    recs = [_qc_run(timedelta(hours=30), 0), _state(timedelta(hours=30), "RED"),
            _sample_run(timedelta(hours=3), 1),
            _qc_run(timedelta(hours=2), 2), _state(timedelta(hours=2), "GREEN")]
    for r in recs:
        j.append([r], ts=r.pop("ts"))
    j.set_acked(j.last_seq(), extra={"last_v2_handshake": _aware(NOW),
                                     "mode": "v2"})
    for r in unacked:
        j.append([r], ts=r.pop("ts"))
    return b, j


def test_DG2_a_rollback_copies_the_last_24h_of_qc_and_state_back(
        qapp, tmp_path, monkeypatch):
    """The v3.9 floor reads QC from LabCore's log. After a v4 server is
    rolled back, a bench that sent its QC verdicts to the v4 store only would
    show the floor stale QC for as long as the rollback lasts. The fall-back
    copies the verdicts and status changes of the last 24 h — not the
    readings, not older — keyed, so a second rollback adds nothing."""
    b, j = _rolled_back_bench(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "bench_now", lambda: NOW)
    before = {r["detail"] for r in b.lab.rows()}
    msgs = []
    b.m._v2_fell_back(b.m.machine(), j, msgs)
    b.poll()
    new = [r for r in b.lab.rows() if r["detail"] not in before]
    ours = [r for r in new
            if int(json.loads(r["detail"])["jk"].split(":")[1]) <= j.acked]
    assert sorted((r["kind"], json.loads(r["detail"]).get("n",
                   json.loads(r["detail"]).get("to"))) for r in ours) == [
        ("qc", 2), ("status_change", "GREEN")]
    assert all(r["kind"] != "run" for r in ours), \
        "a reading the v4 store holds was copied"
    recs = {r["seq"]: r for r in j._scan()}
    for row in ours:
        epoch, seq = json.loads(row["detail"])["jk"].split(":")
        assert epoch == j.epoch and recs[int(seq)]["kind"] in ("run", "state")
    assert j.meta("dg2_due") is None, "the rows landed, DG2 is done"
    # a second rollback (v4 came back, then went again): nothing doubled
    count = len(b.lab.rows())
    j.update_meta(dg2_due=j.acked)
    b.m._v2_fell_back(b.m.machine(), j, msgs)
    b.poll()
    again = [r for r in b.lab.rows()[count:]
             if int(json.loads(r["detail"])["jk"].split(":")[1]) <= j.acked]
    assert again == []


def test_DG2_survives_a_kill_before_its_rows_land(qapp, tmp_path,
                                                  monkeypatch):
    """The fall-back queued the back-fill and the process died before the
    rows reached LabCore: the next start does it again (dg2_due), once."""
    b, j = _rolled_back_bench(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "bench_now", lambda: NOW)
    b.m._v2_fell_back(b.m.machine(), j, [])
    assert j.meta("dg2_due") == j.acked
    b.restart()                                     # nothing drained
    b.poll()
    b.poll()
    qc = [r for r in b.lab.rows("qc")]
    assert [json.loads(r["detail"])["n"] for r in qc] == [2]
    assert b.journal().meta("dg2_due") is None


def test_M5_what_the_rolled_back_server_never_acked_lands_once(
        qapp, tmp_path, monkeypatch):
    """M5: records the v4 server never acknowledged are projected from where
    the projection stands, once, with their keys; an answer lost on the way
    adds nothing."""
    later = [_sample_run(timedelta(minutes=5), 7),
             _qc_run(timedelta(minutes=4), 8)]
    b, j = _rolled_back_bench(tmp_path, monkeypatch, unacked=later)
    monkeypatch.setattr(mod, "bench_now", lambda: NOW)
    b.m._v2_fell_back(b.m.machine(), j, [])
    b.lab.log_fault = ("lose", 1)
    b.poll()
    b.poll()
    b.poll()
    assert [r["lab_id"] for r in b.lab.rows("run")
            if r["lab_id"] == lab_id(7)] == [lab_id(7)]
    assert sorted(json.loads(r["detail"])["n"] for r in b.lab.rows("qc")) \
        == [2, 8]
    assert int(j.meta("projected_seq")) == j.last_seq()


def test_a_v2_side_note_reaches_labcore_when_the_404_came_just_before_a_restart(
        qapp, tmp_path, monkeypatch):
    """The bench was v2; LEM went dark and the operator wrote a note (a v2
    journal record, no row of its own); the uploader then heard LEM's 404
    and wrote `mode: legacy` to the journal — and LabStation was closed
    before any poll ran the fall-back. The next process starts on the legacy
    road with nothing telling it a fall-back was owed. Its restart walk
    projects every event record after `projected_seq` and LEM's acked, v2
    shaped or not, so the note reaches the old floor — once."""
    b = legacy_bench(tmp_path, monkeypatch)
    b.poll()
    j = b.m._journal
    j.append([{"kind": "comment", "lab_id": "", "test_name": "", "value": "",
               "detail": {"note": "column swapped"}}])
    (rec,) = [r for r in j._scan() if r.get("kind") == "comment"]
    b.restart()
    b.poll()
    b.restart()
    b.poll()
    rows = b.lab.rows("comment")
    assert len(rows) == 1
    assert json.loads(rows[0]["detail"]) == {
        "note": "column swapped", "jk": "%s:%d" % (rec["epoch"], rec["seq"])}


# ── a restart DURING the rollback: each record queued once ───────────────────
#
# Round 2 of the critic. Rows inside ONE keyed INSERT ... WHERE NOT EXISTS do
# not see each other — that is exactly what lets two genuine prints of one
# sample both land (L2). So the key protects against a row sent AGAIN, in a
# later statement, and not against the same row sent TWICE in one. The queue
# must therefore hold each record's rows at most once. It did not: a bench
# restarted while LEM answers 404 runs both the restart walk
# (`_journal_recover_once`) and the fall-back (`_v2_fell_back`) in one
# process, each offered the same records, each appended them, and the first
# drain sent both copies in one statement: the DG2 verdict and status changes
# landed twice, so did a v2-side note and a reading LEM never acked. The
# scripted gate never restarts mid-rollback and never counts LabCore rows by
# exact duplicate; these do both.

def _exact_dups(rows):
    seen, dups = set(), 0
    for r in rows:
        key = tuple(r[k] for k in ("machine_uid", "ts", "kind", "lab_id",
                                   "test_name", "value", "detail"))
        dups += key in seen
        seen.add(key)
    return dups


@pytest.mark.parametrize("explicit", [True, False],
                         ids=["fall-back-then-poll", "restart-poll-only"])
def test_DG2_a_restart_while_the_server_answers_404_doubles_nothing(
        qapp, tmp_path, monkeypatch, explicit):
    """The DG2C variant: rolled back, then LabStation restarted before the
    back-fill landed. The restart's walk and its fall-back both offer the
    QC verdict and the status changes; LabCore must hold each once."""
    note = {"kind": "comment", "lab_id": "", "test_name": "", "value": "",
            "detail": {"note": "column swapped"}, "ts": _aware(NOW)}
    later = [_sample_run(timedelta(minutes=5), 7), note]
    b, j = _rolled_back_bench(tmp_path, monkeypatch, unacked=later)
    monkeypatch.setattr(mod, "bench_now", lambda: NOW)
    if explicit:
        # The critic's module-level repro: the fall-back, then the restart
        # walk of the first poll, in one process.
        b.restart()
        b.m._v2_fell_back(b.m.machine(), b.m._journal_for(b.m.machine()), [])
    else:
        # The real road: the new process starts unsure (the journal says v2),
        # its uploader hears LEM's 404, and the poll falls back AND runs the
        # restart walk.
        from fake_lem_v2 import FakeLem
        lem = FakeLem(uid=UID).install(monkeypatch)
        lem.modes = {"lan": "404", "public": "404"}
        b.restart()
        assert b.m._v2_was_active
        b.m._uploader_wake()
        assert b.m._uploader_wait_idle(10)
        assert b.m._transfer.mode == "legacy"
    b.poll()
    b.poll()
    qc = [json.loads(r["detail"])["n"] for r in b.lab.rows("qc")]
    if explicit:
        assert qc == [2]
    else:
        # The poll's clock is the harness's (a day before NOW), so its 24 h
        # window also takes the 30 h-old verdict; once each is the claim.
        assert sorted(qc) == [0, 2]
    keyed = [json.loads(r["detail"]).get("jk") for r in b.lab.rows()]
    keyed = [k for k in keyed if k]
    assert len(keyed) == len(set(keyed)), "a record landed twice"
    assert len(b.lab.rows("comment")) == 1
    assert [r["lab_id"] for r in b.lab.rows("run")].count(lab_id(7)) == 1
    assert _exact_dups(b.lab.rows()) == 0
    assert b.journal().meta("dg2_due") is None


def test_offering_a_record_twice_queues_its_rows_once(qapp, tmp_path,
                                                      monkeypatch):
    """The invariant itself, at the queue: a ref whose rows are already
    waiting (or in flight) is not queued again, whoever offers it — and once
    they have landed it may be offered again harmlessly (the key drops it)."""
    b = legacy_bench(tmp_path, monkeypatch)
    b.poll()
    row = mod.build_log_insert(UID, "comment", datetime.now(),
                               detail={"note": "x"})[1]
    before = len(b.m._pending_events)
    with b.m._journal_lock_or_new():
        b.m._legacy_owe_event("e:99", [row])
        b.m._legacy_owe_event("e:99", [row])
        b.m._legacy_owe_event("e:99", [row], front=True)
    assert len(b.m._pending_events) == before + 1
    assert b.m._journal_unprojected["e:99"] == 1


def test_the_fall_back_projects_events_not_the_journals_bookkeeping(
        qapp, tmp_path, monkeypatch):
    """`filed`, `settled`, `conflict`, `rejected`, `projected` and `adoption`
    are the journal's notes to itself about what became of a reading — v3.9
    never wrote a machine-log row for any of them, and the floor's history
    would show them as machine events. The fall-back walk projected every
    kind it did not know to skip, so the critic's M5 left LabCore holding
    3 `filed` and 1 `settled` row. Only operator events and status changes
    go across."""
    book = [{"kind": "filed", "of": ["x:1"], "cells": [["L", "RON", "91"]]},
            {"kind": "settled", "of": ["x:1"]},
            {"kind": "conflict", "of": ["x:2"], "uid": UID, "cells": []},
            {"kind": "rejected", "of": ["x:3"], "cell": ["L", "RON", "9"],
             "error": "e", "tries": 5},
            {"kind": "projected", "of": ["x:4"]},
            {"kind": "adoption", "src": "file:x", "lines": 3},
            {"kind": "comment", "lab_id": "", "test_name": "", "value": "",
             "detail": {"note": "kept"}}]
    for r in book:
        r["ts"] = _aware(NOW - timedelta(minutes=1))
    b, j = _rolled_back_bench(tmp_path, monkeypatch, unacked=book)
    monkeypatch.setattr(mod, "bench_now", lambda: NOW)
    b.m._v2_fell_back(b.m.machine(), j, [])
    b.poll()
    kinds = {r["kind"] for r in b.lab.rows()}
    assert not kinds & {"filed", "settled", "conflict", "rejected",
                        "projected", "adoption"}, kinds
    assert [json.loads(r["detail"])["note"] for r in b.lab.rows("comment")] \
        == ["kept"]
    assert _exact_dups(b.lab.rows()) == 0
