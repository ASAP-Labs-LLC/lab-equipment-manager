"""The LEM store: LEM's own record, on this server's disk, not inside LabCore.

Transfer spec §5. Until now every `lem_*` table lived in LabCore, reached
through a write queue that serialises the whole lab at about 1.5 ops/sec and
kills any read past eight seconds. LEM was a client of a database whose
custody it did not hold, and the machine log — the ISO/IEC 17025 record of
every reading, verdict and correction — could be rewritten or deleted by any
statement that reached that queue (`web_app`'s "purge history" did exactly
that).

`LocalStoreGateway` is a SQLite file on the LEM server's local disk with the
SAME surface as the LabCore gateways (`sql`, `read_sql`, `write`,
`is_running`) and LabCore's exact answer shapes, so every store module in this
app runs on it unchanged. These tests pin what the store adds and what it must
never lose:

* **Durability** — WAL with `synchronous=FULL`: a commit that returned is on
  disk, and a reader never blocks the writer (the SMB-share LabCore could do
  neither, which is why its reads went through the write queue).
* **Append-only** — `lem_machine_log` and `log_annotation` refuse UPDATE and
  DELETE at the database level (S1). A rule in the application is a rule the
  next route forgets; a trigger is not.
* **Same table, same seven columns** — the four server INSERTs that write
  7-column rows (W2b) keep working; the custody columns default sensibly.
* **Hiding is an annotation, never a delete** — `lem_machine_log_effective`
  is the record minus rows an append-only annotation hides.
* **A failed read is never an empty result** — a local `sqlite3.Error` comes
  back as `{"error": ...}`, the shape every reader already refuses to read as
  "no rows".
"""

import os
import sqlite3
import threading
import time

import pytest

from lem_store import LocalStoreGateway, STORE_TABLES

SEVEN = ("machine_uid", "ts", "kind", "lab_id", "test_name", "value", "detail")


@pytest.fixture
def store(tmp_path):
    s = LocalStoreGateway(str(tmp_path / "store" / "lem.db"))
    yield s
    s.close()


def _log(store, uid="m1", ts="2026-10-01T09:00:00", kind="run", lab="L1",
         value="1.0"):
    res = store.sql(
        "INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
        "test_name, value, detail) VALUES (?, ?, ?, ?, 'Flash', ?, '{}')",
        [uid, ts, kind, lab, value])
    assert "error" not in res, res
    got = store.read_sql("SELECT MAX(id) AS id FROM lem_machine_log")
    return got["rows"][0]["id"]


# ── placement and pragmas ──────────────────────────────────────────────────

class TestTheFileIsDurable:
    def test_the_folder_is_made_and_the_file_is_where_it_was_asked_for(
            self, tmp_path):
        path = tmp_path / "deep" / "er" / "lem.db"
        s = LocalStoreGateway(str(path))
        try:
            assert path.exists()
        finally:
            s.close()

    def test_wal_and_full_sync_on_every_connection(self, store):
        """WAL so a reader never blocks the writer; FULL so a commit that
        returned survives a power cut. Checked on the reader too, because a
        pragma set on one connection says nothing about the next."""
        for conn in (store.writer_pragmas(), store.reader_pragmas()):
            assert conn["journal_mode"] == "wal"
            assert conn["synchronous"] == 2          # FULL
            assert conn["foreign_keys"] == 1
            assert conn["busy_timeout"] == 10000

    def test_is_running_means_the_file_answered(self, store):
        assert store.is_running() is True


# ── the schema of §5.2 ─────────────────────────────────────────────────────

class TestTheSchema:
    def test_the_log_keeps_its_seven_original_columns_first(self, store):
        res = store.read_sql("SELECT name FROM pragma_table_info('lem_machine_log') "
                             "ORDER BY cid")
        names = [r["name"] for r in res["rows"]]
        # `id` is the INTEGER PRIMARY KEY (an alias of rowid, so every
        # existing `rowid` reader still works); the seven follow in order.
        assert names[0] == "id"
        assert tuple(names[1:8]) == SEVEN
        for custody in ("origin", "bench_epoch", "bench_seq", "content_key",
                        "legacy_key", "legacy_rowid", "received_at"):
            assert custody in names

    def test_every_table_of_the_spec_exists(self, store):
        res = store.read_sql("SELECT name FROM sqlite_master "
                             "WHERE type IN ('table','view')")
        have = {r["name"] for r in res["rows"]}
        for name in STORE_TABLES:
            assert name in have, name
        for name in ("annotation_approval", "log_annotation", "bench_cursor",
                     "bench_token", "bench_source", "result_ledger",
                     "result_conflict", "projection_outbox", "import_run",
                     "log_digest", "store_meta", "unknown_records",
                     "lem_machine_log_effective", "request_ledger"):
            assert name in have, name

    def test_the_unique_custody_keys_hold(self, store):
        ok = store.sql(
            "INSERT INTO lem_machine_log (machine_uid, ts, kind, bench_epoch, "
            "bench_seq) VALUES ('m1', 't', 'run', 'E1', 1)")
        assert "error" not in ok
        again = store.sql(
            "INSERT INTO lem_machine_log (machine_uid, ts, kind, bench_epoch, "
            "bench_seq) VALUES ('m1', 't', 'run', 'E1', 1)")
        assert "already in the record" in again["error"]
        # Not even "OR IGNORE": the BEFORE INSERT guard (S1, below) refuses
        # any insert that would collide with a row already in the record,
        # whatever conflict clause it carries, so a duplicate bench line is
        # an error a caller sees, never a silent no-op or a silent rewrite.
        ignored = store.sql(
            "INSERT OR IGNORE INTO lem_machine_log (machine_uid, ts, kind, "
            "bench_epoch, bench_seq) VALUES ('m1', 't', 'run', 'E1', 1)")
        assert "already in the record" in ignored["error"]
        assert store.read_sql("SELECT COUNT(*) n FROM lem_machine_log"
                              )["rows"][0]["n"] == 1

    def test_a_seven_column_insert_lands_as_a_server_row(self, store):
        """W2b. `web_app._audit`, the PM completion, the maintenance import
        and `levels` all INSERT seven columns. Spec B's view-only design broke
        all four; a table with defaults breaks none."""
        rid = _log(store)
        row = store.read_sql("SELECT origin, received_at FROM lem_machine_log "
                             "WHERE id = ?", [rid])["rows"][0]
        assert row["origin"] == "server"
        assert row["received_at"]           # stamped by the store, not NULL

    def test_reopening_an_existing_store_changes_nothing(self, tmp_path):
        path = str(tmp_path / "lem.db")
        a = LocalStoreGateway(path)
        _log(a)
        a.close()
        b = LocalStoreGateway(path)
        try:
            n = b.read_sql("SELECT COUNT(*) n FROM lem_machine_log")["rows"][0]["n"]
            assert n == 1
        finally:
            b.close()

    def test_a_log_declared_the_old_way_gains_the_custody_columns(self, tmp_path):
        """A file whose log was declared with LabCore's seven-column DDL (an
        early dev run, a copy of LabCore) is brought up to shape, not
        refused and not silently left without its triggers."""
        path = str(tmp_path / "lem.db")
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE lem_machine_log (machine_uid TEXT, ts TEXT, "
                    "kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
                    "detail TEXT)")
        con.execute("INSERT INTO lem_machine_log VALUES "
                    "('m1','t','run','L','T','1','{}')")
        con.commit()
        con.close()
        s = LocalStoreGateway(path)
        try:
            cols = {r["name"] for r in s.read_sql(
                "SELECT name FROM pragma_table_info('lem_machine_log')")["rows"]}
            assert {"origin", "bench_seq", "received_at"} <= cols
            assert "append-only" in s.sql("DELETE FROM lem_machine_log")["error"]
        finally:
            s.close()


# ── S1: append-only ───────────────────────────────────────────────────────

class TestTheRecordIsAppendOnly:
    """S1. Neither the 17025 record nor the annotations that decide how it
    counts can be rewritten. Both refusals come back as LabCore's error shape,
    so `check_write` turns them into the refusal every route already
    reports."""

    def test_update_on_the_log_is_refused(self, store):
        rid = _log(store)
        res = store.sql("UPDATE lem_machine_log SET value = '9' WHERE id = ?",
                        [rid])
        assert "append-only" in res["error"]
        assert store.read_sql("SELECT value FROM lem_machine_log WHERE id = ?",
                              [rid])["rows"][0]["value"] == "1.0"

    def test_delete_on_the_log_is_refused(self, store):
        _log(store)
        res = store.sql("DELETE FROM lem_machine_log")
        assert "append-only" in res["error"]
        assert store.read_sql("SELECT COUNT(*) n FROM lem_machine_log"
                              )["rows"][0]["n"] == 1

    def test_the_refusal_raises_through_raw_sqlite_too(self, store):
        """Not a rule of this class — of the FILE. Anybody opening lem.db with
        the sqlite3 shell gets the same answer."""
        _log(store)
        con = sqlite3.connect(store.path)
        try:
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                con.execute("DELETE FROM lem_machine_log")
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                con.execute("UPDATE lem_machine_log SET kind = 'x'")
        finally:
            con.close()

    def test_annotations_are_append_only(self, store):
        rid = _log(store)
        assert "error" not in store.sql(
            "INSERT INTO log_annotation (log_id, label, by, at) "
            "VALUES (?, 'replay_candidate', 'lem', 't')", [rid])
        assert "append-only" in store.sql(
            "UPDATE log_annotation SET label = 'replay_duplicate'")["error"]
        assert "append-only" in store.sql(
            "DELETE FROM log_annotation")["error"]

    def test_an_annotation_must_point_at_a_real_row(self, store):
        res = store.sql("INSERT INTO log_annotation (log_id, label, by, at) "
                        "VALUES (999, 'replay_duplicate', 'lem', 't')")
        assert "FOREIGN KEY" in res["error"]


class TestNothingRewritesTheRecord:
    """S1, the way round the UPDATE and DELETE triggers.

    SQLite's REPLACE conflict resolution DELETES the colliding row and inserts
    the new one, and it does not fire DELETE triggers while
    `recursive_triggers` is off (the default, and the default of every
    `sqlite3` shell). So `INSERT OR REPLACE INTO lem_machine_log (id, ...)`
    rewrote a reading in place: the critic turned 1.0 into 999, the detail
    into 'forged' and the origin back to 'server', and forged a hiding
    annotation the same way, through `sql()` and through raw sqlite3 alike.
    And the store accepted `DROP TRIGGER` and `DROP TABLE`, which take the
    guarantee away wholesale.

    A rule that holds for the obvious statements and not for the less obvious
    ones is a rule an assessor cannot rely on. These pin every road we know:
    each conflict clause, the upsert form, a collision on each unique custody
    key, and DDL against the record, through the store and through the bare
    file.
    """

    def _row(self, store, rid):
        return store.read_sql("SELECT value, detail, origin FROM "
                              "lem_machine_log WHERE id = ?", [rid])["rows"][0]

    @pytest.mark.parametrize("verb", [
        "INSERT OR REPLACE", "REPLACE", "INSERT OR IGNORE", "INSERT OR FAIL",
        "INSERT OR ABORT", "INSERT OR ROLLBACK", "INSERT"])
    def test_no_insert_form_touches_an_existing_log_row(self, store, verb):
        rid = _log(store)
        before = self._row(store, rid)
        res = store.sql(
            verb + " INTO lem_machine_log (id, machine_uid, ts, kind, lab_id, "
            "test_name, value, detail) VALUES (?, 'm1', 't', 'run', 'L1', "
            "'Flash', '999', 'forged')", [rid])
        assert "already in the record" in res.get("error", ""), res
        assert self._row(store, rid) == before
        assert store.read_sql("SELECT COUNT(*) n FROM lem_machine_log"
                              )["rows"][0]["n"] == 1

    def test_the_upsert_form_is_refused_too(self, store):
        rid = _log(store)
        before = self._row(store, rid)
        res = store.sql(
            "INSERT INTO lem_machine_log (id, machine_uid, ts, kind, value) "
            "VALUES (?, 'm1', 't', 'run', '999') ON CONFLICT(id) DO UPDATE "
            "SET value = excluded.value", [rid])
        assert "error" in res
        assert self._row(store, rid) == before

    def test_a_collision_on_a_custody_key_cannot_replace_either(self, store):
        """No `id` named at all: the REPLACE collides on the bench key or the
        legacy key instead, and would delete the original under a new id."""
        assert "error" not in store.sql(
            "INSERT INTO lem_machine_log (machine_uid, ts, kind, value, "
            "bench_epoch, bench_seq, legacy_key) "
            "VALUES ('m1', 't', 'run', '1.0', 'E1', 7, 'LK')")
        for clash in ("bench_epoch, bench_seq) VALUES ('m1','t','run','999',"
                      "'E1', 7)",
                      "legacy_key) VALUES ('m1','t','run','999','LK')"):
            res = store.sql("INSERT OR REPLACE INTO lem_machine_log "
                            "(machine_uid, ts, kind, value, " + clash)
            assert "already in the record" in res.get("error", ""), res
        rows = store.read_sql("SELECT id, value FROM lem_machine_log")["rows"]
        assert rows == [{"id": 1, "value": "1.0"}]

    @pytest.mark.parametrize("verb", ["INSERT OR REPLACE", "REPLACE",
                                      "INSERT OR IGNORE", "INSERT"])
    def test_no_insert_form_touches_an_existing_annotation(self, store, verb):
        rid = _log(store)
        assert "error" not in store.sql(
            "INSERT INTO log_annotation (id, log_id, label, by, at) "
            "VALUES (1, ?, 'replay_candidate', 'lem', 't')", [rid])
        res = store.sql(
            verb + " INTO log_annotation (id, log_id, label, by, at) "
            "VALUES (1, ?, 'replay_duplicate', 'forger', 't')", [rid])
        assert "already in the record" in res.get("error", ""), res
        assert store.read_sql("SELECT label, by FROM log_annotation"
                              )["rows"] == [{"label": "replay_candidate",
                                             "by": "lem"}]
        # and the reading is still in the effective record
        assert store.read_sql("SELECT COUNT(*) n FROM "
                              "lem_machine_log_effective")["rows"][0]["n"] == 1

    def test_replace_is_refused_through_the_bare_file_too(self, store):
        """The guard is a trigger, so it belongs to the FILE, not to this
        class: a sqlite3 shell with every default gets the same refusal."""
        rid = _log(store)
        assert "error" not in store.sql(
            "INSERT INTO log_annotation (id, log_id, label, by, at) "
            "VALUES (1, ?, 'replay_candidate', 'lem', 't')", [rid])
        con = sqlite3.connect(store.path)
        try:
            with pytest.raises(sqlite3.DatabaseError,
                               match="already in the record"):
                con.execute("INSERT OR REPLACE INTO lem_machine_log (id, "
                            "machine_uid, ts, kind, value, detail) VALUES "
                            "(?, 'm1', 't', 'run', '999', 'forged')", [rid])
            with pytest.raises(sqlite3.DatabaseError,
                               match="already in the record"):
                con.execute("REPLACE INTO log_annotation (id, log_id, label, "
                            "by, at) VALUES (1, ?, 'replay_duplicate', 'x', "
                            "'t')", [rid])
        finally:
            con.close()
        assert self._row(store, rid) == {"value": "1.0", "detail": "{}",
                                         "origin": "server"}

    def test_replace_fires_the_delete_guard_on_the_stores_connections(
            self, store):
        """Defence in depth for a unique key nobody has thought of yet:
        with `recursive_triggers` on, a REPLACE's conflict deletion runs the
        BEFORE DELETE trigger, so even a collision the INSERT guard does not
        name is refused rather than performed."""
        assert store.writer_pragmas()["recursive_triggers"] == 1
        assert store.reader_pragmas()["recursive_triggers"] == 1

    @pytest.mark.parametrize("ddl", [
        "DROP TRIGGER lem_log_no_update",
        "DROP TRIGGER lem_log_no_delete",
        "DROP TRIGGER lem_log_no_overwrite",
        "DROP TRIGGER ann_no_update",
        "DROP TRIGGER ann_no_delete",
        "DROP TRIGGER ann_no_overwrite",
        "DROP TABLE log_annotation",
        "DROP TABLE lem_machine_log",
        "DROP TABLE annotation_approval",
        "DROP TABLE lem_machine_config",
        "DROP VIEW lem_machine_log_effective",
        "DROP INDEX ux_log_bench",
        "DROP INDEX ux_log_legacy",
        "ALTER TABLE lem_machine_log RENAME TO old_log",
        "ALTER TABLE log_annotation DROP COLUMN approval_id",
        "ALTER TABLE lem_machine_log ADD COLUMN sneaky TEXT",
        "CREATE TRIGGER sneak BEFORE DELETE ON lem_machine_log "
        "BEGIN SELECT RAISE(IGNORE); END",
        "CREATE TEMP TRIGGER sneak BEFORE INSERT ON log_annotation "
        "BEGIN SELECT 1; END",
        "PRAGMA writable_schema = 1",
        "PRAGMA recursive_triggers = 0",
        "PRAGMA foreign_keys = 0",
        "PRAGMA query_only = 0",
    ])
    def test_the_store_refuses_ddl_that_would_unguard_the_record(
            self, store, ddl):
        rid = _log(store)
        res = store.sql(ddl)
        assert "error" in res, (ddl, res)
        assert "not authorized" in res["error"], res
        # and the guard is still there, doing its job
        assert "append-only" in store.sql("DELETE FROM lem_machine_log"
                                          )["error"]
        assert "already in the record" in store.sql(
            "REPLACE INTO lem_machine_log (id, value) VALUES (?, '9')",
            [rid])["error"]
        assert store.health()["guards_missing"] == []

    def test_a_read_cannot_write(self, store):
        """`read_sql` runs on reader connections, which are query-only: a
        DROP or DELETE sent down the read road is refused, not performed."""
        _log(store)
        for stmt in ("DROP TABLE log_annotation",
                     "DELETE FROM request_ledger",
                     "INSERT INTO store_meta (key, value) VALUES ('x', 'y')"):
            assert "error" in store.read_sql(stmt), stmt
        assert store.read_sql("SELECT COUNT(*) n FROM store_meta WHERE key "
                              "= 'x'")["rows"][0]["n"] == 0

    def test_ordinary_schema_work_still_runs(self, store):
        """What the app's owners declare on the store (`snapshot_service`'s
        indexes, its own tables) is untouched by the refusal."""
        for ok in ("CREATE INDEX IF NOT EXISTS ix_extra ON lem_machine_log"
                   "(kind)",
                   "CREATE TABLE IF NOT EXISTS lem_scratch (a TEXT)",
                   "DROP TABLE lem_scratch",
                   "PRAGMA table_info('lem_machine_log')"):
            assert "error" not in store.sql(ok), ok

    def test_a_trigger_dropped_behind_the_stores_back_comes_back(
            self, tmp_path):
        """The bare file CAN drop a trigger; SQLite has no rule against its
        own owner. What the store can do is notice and restore: every open
        re-declares the guards, and `health()` names any that are missing
        while it runs, so `/healthz` says so instead of the record quietly
        becoming rewritable."""
        path = str(tmp_path / "lem.db")
        s = LocalStoreGateway(path)
        con = sqlite3.connect(path)
        con.execute("DROP TRIGGER lem_log_no_overwrite")
        con.commit()
        con.close()
        assert s.health()["guards_missing"] == ["lem_log_no_overwrite"]
        s.close()
        s = LocalStoreGateway(path)
        try:
            assert s.health()["guards_missing"] == []
        finally:
            s.close()


# ── the effective view ─────────────────────────────────────────────────────

class TestTheEffectiveView:
    def _annotate(self, store, rid, label):
        assert "error" not in store.sql(
            "INSERT INTO log_annotation (log_id, label, by, at) "
            "VALUES (?, ?, 'ryan', 't')", [rid, label])

    def _effective_ids(self, store):
        return [r["id"] for r in store.read_sql(
            "SELECT id FROM lem_machine_log_effective ORDER BY id")["rows"]]

    def test_hiding_labels_hide_and_visible_labels_do_not(self, store):
        a, b, c, d = (_log(store, lab="L%d" % i) for i in range(4))
        self._annotate(store, a, "replay_duplicate")
        self._annotate(store, b, "import_leftover")
        self._annotate(store, c, "replay_candidate")      # visible
        self._annotate(store, d, "ambiguous_repeat")      # visible
        assert self._effective_ids(store) == [c, d]
        # And the record itself still holds all four.
        assert store.read_sql("SELECT COUNT(*) n FROM lem_machine_log"
                              )["rows"][0]["n"] == 4

    def test_the_newest_annotation_decides_so_hiding_is_reversible(self, store):
        a = _log(store)
        self._annotate(store, a, "replay_duplicate")
        assert self._effective_ids(store) == []
        self._annotate(store, a, "reinstated")
        assert self._effective_ids(store) == [a]

    def test_the_view_carries_every_column_of_the_table(self, store):
        _log(store)
        row = store.read_sql("SELECT * FROM lem_machine_log_effective")["rows"][0]
        for col in SEVEN + ("id", "origin"):
            assert col in row

    def test_a_retired_machines_history_is_hidden_not_deleted(self, store):
        """Purge becomes hide (§5.2, D4). The triggers would refuse the old
        DELETE anyway; what replaces it is a `retired_at` on the machine's
        config row, and rows older than it drop out of every default view."""
        old = _log(store, uid="gone", ts="2026-09-01T00:00:00")
        keep = _log(store, uid="stays", ts="2026-09-01T00:00:00")
        assert "error" not in store.sql(
            "INSERT INTO lem_machine_config (machine_uid, title, retired_at) "
            "VALUES ('gone', 'Gone', '2026-09-30T00:00:00')")
        after = _log(store, uid="gone", ts="2026-09-30T00:00:01")
        assert self._effective_ids(store) == [keep, after]
        assert store.read_sql("SELECT COUNT(*) n FROM lem_machine_log WHERE id=?",
                              [old])["rows"][0]["n"] == 1


# ── a failed read is never an empty result ─────────────────────────────────

class TestFailuresAreAnswers:
    def test_a_bad_read_is_an_error_not_rows(self, store):
        res = store.read_sql("SELECT * FROM no_such_table")
        assert "error" in res and "rows" not in res

    def test_a_bad_write_is_an_error(self, store):
        res = store.sql("INSERT INTO no_such_table VALUES (1)")
        assert "error" in res and not res.get("ok")

    def test_a_raising_connection_is_an_error_not_an_exception(self, store):
        store.close()
        res = store.read_sql("SELECT 1 AS one")
        assert "error" in res

    def test_write_speaks_the_queue_operations_lem_uses(self, store):
        assert store.write("raw_sql", {"sql": "CREATE TABLE t (a)"})["ok"]
        assert store.write("raw_sql", {"sql": "INSERT INTO t VALUES (?)",
                                       "args": [1]})["rows_affected"] == 1
        assert store.write("read_sql", {"sql": "SELECT a FROM t"}
                           )["rows"] == [{"a": 1}]
        assert "error" in store.write("update_cell", {})


# ── one writer, and transactions ───────────────────────────────────────────

class TestTransactions:
    """W2 needs "factor, audit and config log row" to commit together or not
    at all. LabCore's queue takes one statement at a time and could not offer
    that; the store can."""

    def test_everything_in_a_transaction_commits_together(self, store):
        with store.transaction():
            store.sql("CREATE TABLE IF NOT EXISTS t (a)")
            store.sql("INSERT INTO t VALUES (1)")
            # A read inside the transaction sees its own writes.
            assert store.read_sql("SELECT COUNT(*) n FROM t")["rows"][0]["n"] == 1
            _log(store)
        assert store.read_sql("SELECT COUNT(*) n FROM t")["rows"][0]["n"] == 1

    def test_an_exception_rolls_all_of_it_back(self, store):
        store.sql("CREATE TABLE t (a)")
        with pytest.raises(RuntimeError):
            with store.transaction():
                store.sql("INSERT INTO t VALUES (1)")
                _log(store)
                raise RuntimeError("the third write was refused")
        assert store.read_sql("SELECT COUNT(*) n FROM t")["rows"][0]["n"] == 0
        assert store.read_sql("SELECT COUNT(*) n FROM lem_machine_log"
                              )["rows"][0]["n"] == 0

    def test_nested_transactions_are_one_transaction(self, store):
        store.sql("CREATE TABLE t (a)")
        with pytest.raises(RuntimeError):
            with store.transaction():
                with store.transaction():
                    store.sql("INSERT INTO t VALUES (1)")
                raise RuntimeError("outer fails")
        assert store.read_sql("SELECT COUNT(*) n FROM t")["rows"][0]["n"] == 0

    def test_other_threads_do_not_see_an_uncommitted_write(self, store):
        store.sql("CREATE TABLE t (a)")
        seen = {}
        inside = threading.Event()
        release = threading.Event()

        def writer():
            with store.transaction():
                store.sql("INSERT INTO t VALUES (1)")
                inside.set()
                release.wait(5)

        th = threading.Thread(target=writer)
        th.start()
        assert inside.wait(5)
        t0 = time.time()
        seen["n"] = store.read_sql("SELECT COUNT(*) n FROM t")["rows"][0]["n"]
        seen["read_s"] = time.time() - t0
        release.set()
        th.join(5)
        # WAL: the reader is NOT blocked by the open write transaction, and it
        # sees the last committed state.
        assert seen["n"] == 0
        assert seen["read_s"] < 1.0
        assert store.read_sql("SELECT COUNT(*) n FROM t")["rows"][0]["n"] == 1

    def test_writes_from_many_threads_all_land(self, store):
        """One writer: concurrent saves are serialised, never lost to
        'database is locked'."""
        def burst(k):
            for i in range(25):
                _log(store, lab="T%d-%d" % (k, i))
        threads = [threading.Thread(target=burst, args=(k,)) for k in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(30)
        assert store.read_sql("SELECT COUNT(*) n FROM lem_machine_log"
                              )["rows"][0]["n"] == 200


# ── the candidate boot: read-only ──────────────────────────────────────────

class TestReadOnly:
    """The updater health-checks a release on a scratch port with
    `--no-publish`. That process must not migrate, ingest or write anything
    into the store the live server is using (§5.1)."""

    def test_reads_work_and_writes_are_refused(self, tmp_path):
        path = str(tmp_path / "lem.db")
        live = LocalStoreGateway(path)
        _log(live)
        cand = LocalStoreGateway(path, read_only=True)
        try:
            assert cand.read_only is True
            assert cand.read_sql("SELECT COUNT(*) n FROM lem_machine_log"
                                 )["rows"][0]["n"] == 1
            res = cand.sql("INSERT INTO lem_machine_log (machine_uid) VALUES ('x')")
            assert "read-only" in res["error"]
            assert cand.sql("CREATE TABLE IF NOT EXISTS z (a)")["error"]
            with pytest.raises(Exception):
                with cand.transaction():
                    pass
        finally:
            cand.close()
            live.close()
        assert live.path == path

    def test_a_missing_store_is_an_error_not_an_empty_lab(self, tmp_path):
        path = str(tmp_path / "never-made" / "lem.db")
        cand = LocalStoreGateway(path, read_only=True)
        try:
            res = cand.read_sql("SELECT COUNT(*) n FROM lem_machine_log")
            assert "error" in res and "rows" not in res
            assert cand.is_running() is False
            # And it did not create the file behind our back.
            assert not os.path.exists(path)
        finally:
            cand.close()

    def test_health_says_what_it_is(self, tmp_path):
        path = str(tmp_path / "lem.db")
        s = LocalStoreGateway(path)
        try:
            h = s.health()
            assert h["path"] == path
            assert h["read_only"] is False
            assert h["schema_version"] >= 1
            assert h["bytes"] > 0
        finally:
            s.close()
