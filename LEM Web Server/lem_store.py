#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lem_store.py — LEM's own record, in a SQLite file on this server's disk.

Transfer spec v4, §5. Until this file existed every `lem_*` table lived inside
LabCore, which LEM reaches through an HTTP write queue that serialises the
whole lab at about 1.5 ops/sec and kills any read past eight seconds. That had
three costs, all measured in `baseline/`:

* every snapshot refresh was a LabCore op (1 per 12 s, constant), and one
  killed read cost +426 ops over the next six minutes (W1);
* "save a correction factor" was three queue statements that could not be a
  transaction, so a lost response left the factor saved and the audit missing
  (W2);
* and the ISO/IEC 17025 record — `lem_machine_log` — could be rewritten or
  deleted by any statement that reached that queue. `web_app`'s "purge
  history" did exactly that.

`LocalStoreGateway` is the store. It has the surface of the LabCore gateways
(`sql`, `read_sql`, `write`, `is_running`) and LabCore's exact answer shapes,
so every store module in this app (`DbConfigStore`, `MachineConfigStore`,
`LevelStore`, …) and `snapshot_service._ARMS` run on it unchanged. What it
adds:

* **Durability.** WAL with `synchronous=FULL`: a commit that returned is on
  disk; a reader never blocks the writer and never waits behind it.
* **One writer.** Every write goes through ONE connection under one lock, so
  concurrent saves are serialised in-process rather than racing to "database
  is locked". Readers use their own connections from a small pool.
* **Transactions.** `with store.transaction():` — what LabCore's one-statement
  queue could never give, and what W2 needs. Statements inside it still go
  through `sql()`, so a test double that refuses a write refuses it inside a
  transaction too, and the transaction rolls back.
* **Append-only, in the file.** Triggers refuse UPDATE and DELETE on
  `lem_machine_log` and `log_annotation`. Anybody opening `lem.db` with the
  sqlite3 shell gets the same answer as the app (S1).
* **Hiding is an annotation.** `lem_machine_log_effective` is the record minus
  rows whose newest annotation hides them, minus the history of a machine
  retired with "purge history" (which used to DELETE, and now cannot).
* **Read-only on a candidate boot.** The updater health-checks a release on a
  scratch port with `--no-publish`; that process opens the store `mode=ro` and
  can neither migrate nor write (§5.1).

A FAILED READ IS NEVER AN EMPTY RESULT, here as everywhere in LEM. Every
`sqlite3.Error` — a missing table, a locked file, a store that is not there —
comes back as `{"error": ...}`, the shape every reader in this app already
refuses to read as "no rows". Nothing in this class answers `{"rows": []}`
for a question it could not ask.
"""

from __future__ import annotations

import contextlib
import os
import queue
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

#: Where production keeps the store (§5.1). Outside the release folder, which a
#: deploy swaps wholesale, and outside `data/`, which RELEASING.md calls
#: regenerable cache. `LEM_STORE_PATH` overrides it.
DEFAULT_WINDOWS_STORE = r"C:\ASAPApps\lem\store\lem.db"

SCHEMA_VERSION = 1

#: The tables §5.2 adds. The current-state `lem_*` tables keep their names and
#: shapes and are declared by their owners (`snapshot_service.SCHEMA_DDL`) on
#: the store exactly as they were on LabCore.
STORE_TABLES = (
    "lem_machine_log", "log_annotation", "annotation_approval",
    "bench_cursor", "bench_token", "bench_source", "result_ledger",
    "result_conflict", "projection_outbox", "import_run", "log_digest",
    "store_meta", "unknown_records", "request_ledger", "lem_machine_config",
)

#: The seven columns LabCore's `lem_machine_log` has, in its order. Every
#: server INSERT names exactly these (W2b), and every one of them still works.
LOG_COLUMNS = ("machine_uid", "ts", "kind", "lab_id", "test_name", "value",
               "detail")

#: The columns the store adds to the record. None of them is written by the
#: server's INSERTs or the bench's; each has a default or stays NULL.
CUSTODY_COLUMNS = ("id", "origin", "bench_epoch", "bench_seq", "content_key",
                   "legacy_key", "legacy_rowid", "received_at")

#: Annotation labels that HIDE a row from the effective view. Every other label
#: (`replay_candidate`, `ambiguous_repeat`, `probable_duplicate`) is visible,
#: and `reinstated` cancels an earlier hide because only the NEWEST annotation
#: on a row counts.
HIDING_LABELS = ("replay_duplicate", "import_leftover")

_LOG_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_machine_log ("
    " id INTEGER PRIMARY KEY,"
    " machine_uid TEXT, ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT,"
    " value TEXT, detail TEXT,"
    " origin TEXT NOT NULL DEFAULT 'server',"
    " bench_epoch TEXT, bench_seq INTEGER,"
    " content_key TEXT,"
    " legacy_key TEXT,"
    " legacy_rowid INTEGER,"
    " received_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')))"
)

_DDL = (
    _LOG_DDL,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_log_bench ON lem_machine_log"
    "(machine_uid, bench_epoch, bench_seq) WHERE bench_seq IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_log_legacy ON lem_machine_log"
    "(legacy_key) WHERE legacy_key IS NOT NULL",
    # The three index names LabCore's copy carries, kept so `EXPLAIN` reads
    # the same on both and `snapshot_service`'s declarations are no-ops here.
    "CREATE INDEX IF NOT EXISTS idx_lem_log_uid_kind_ts ON lem_machine_log"
    "(machine_uid, kind, ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_lem_log_lab_ts ON lem_machine_log"
    "(lab_id, ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_lem_log_ts ON lem_machine_log(ts DESC)",
    "CREATE INDEX IF NOT EXISTS ix_log_key ON lem_machine_log"
    "(machine_uid, content_key)",
    "CREATE TRIGGER IF NOT EXISTS lem_log_no_update BEFORE UPDATE ON "
    "lem_machine_log BEGIN SELECT RAISE(ABORT, "
    "'lem_machine_log is append-only: annotate the row instead'); END",
    "CREATE TRIGGER IF NOT EXISTS lem_log_no_delete BEFORE DELETE ON "
    "lem_machine_log BEGIN SELECT RAISE(ABORT, "
    "'lem_machine_log is append-only: annotate the row instead'); END",
    "CREATE TABLE IF NOT EXISTS log_annotation ("
    " id INTEGER PRIMARY KEY,"
    " log_id INTEGER NOT NULL REFERENCES lem_machine_log(id),"
    " label TEXT NOT NULL,"
    " dup_of INTEGER, rule TEXT, run_id TEXT,"
    " by TEXT NOT NULL, at TEXT NOT NULL, approval_id INTEGER)",
    "CREATE INDEX IF NOT EXISTS ix_annotation_log ON log_annotation(log_id, id)",
    "CREATE TRIGGER IF NOT EXISTS ann_no_update BEFORE UPDATE ON log_annotation "
    "BEGIN SELECT RAISE(ABORT, 'log_annotation is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS ann_no_delete BEFORE DELETE ON log_annotation "
    "BEGIN SELECT RAISE(ABORT, 'log_annotation is append-only'); END",
    "CREATE TABLE IF NOT EXISTS annotation_approval ("
    " id INTEGER PRIMARY KEY, machine_uid TEXT, rule TEXT, run_id TEXT,"
    " candidates INTEGER, examples TEXT, qc_impact TEXT, approved_by TEXT,"
    " approved_at TEXT, decision TEXT)",
    # The machine's configuration row, declared HERE (with its owner's exact
    # columns from machine_configs.CONFIG_DDL) because the effective view
    # reads `retired_at` from it and a view naming a missing table fails every
    # reader of the log. `retired_at` is store-only: no bench reads it.
    "CREATE TABLE IF NOT EXISTS lem_machine_config ("
    " machine_uid TEXT PRIMARY KEY, title TEXT NOT NULL, config TEXT,"
    " updated_at TEXT, updated_by TEXT, retired_at TEXT)",
    "CREATE TABLE IF NOT EXISTS bench_cursor ("
    " machine_uid TEXT, bench_epoch TEXT, acked_seq INTEGER NOT NULL,"
    " durable_seq INTEGER NOT NULL DEFAULT 0, digest TEXT, records_total INTEGER,"
    " first_seen TEXT, last_seen TEXT, road TEXT, module_version TEXT,"
    " clock_skew_s REAL, labcore_failures_5min INTEGER, mode TEXT,"
    " PRIMARY KEY (machine_uid, bench_epoch))",
    "CREATE TABLE IF NOT EXISTS bench_token ("
    " machine_uid TEXT PRIMARY KEY, token_sha256 TEXT, issued_at TEXT,"
    " issued_by TEXT, revoked_at TEXT, pending_reenrol_at TEXT)",
    "CREATE TABLE IF NOT EXISTS bench_source ("
    " machine_uid TEXT, src TEXT, lineage TEXT, cursor TEXT, snapshot BLOB,"
    " updated_at TEXT, PRIMARY KEY (machine_uid, src))",
    "CREATE TABLE IF NOT EXISTS result_ledger ("
    " machine_uid TEXT, lab_id TEXT, test_name TEXT, value TEXT, filed_at TEXT,"
    " bench_seq_ref TEXT, PRIMARY KEY (lab_id, test_name, machine_uid))",
    "CREATE TABLE IF NOT EXISTS result_conflict ("
    " bench_seq_ref TEXT PRIMARY KEY, machine_uid, lab_id, test_name, ours,"
    " theirs, their_updated_at, their_operator, opened_at, resolved_at,"
    " resolved_by, choice, delivered_at)",
    "CREATE TABLE IF NOT EXISTS projection_outbox ("
    " id INTEGER PRIMARY KEY, sql TEXT, args TEXT, table_name TEXT,"
    " created_at TEXT, tries INTEGER DEFAULT 0, last_error TEXT, landed_at TEXT)",
    "CREATE TABLE IF NOT EXISTS import_run ("
    " table_name TEXT PRIMARY KEY, source_rows INTEGER, max_rowid INTEGER,"
    " copied INTEGER, verified TEXT, finished_at TEXT)",
    "CREATE TABLE IF NOT EXISTS log_digest ("
    " day TEXT PRIMARY KEY, rows INTEGER, sha256 TEXT, computed_at TEXT)",
    "CREATE TABLE IF NOT EXISTS store_meta (key TEXT PRIMARY KEY, value TEXT)",
    "CREATE TABLE IF NOT EXISTS unknown_records ("
    " id INTEGER PRIMARY KEY, machine_uid, bench_epoch, bench_seq, body,"
    " received_at)",
    # W2: a browser retry carrying the same X-Request-Id is answered from here
    # instead of being done twice. Written in the SAME transaction as the
    # change, so "recorded as done" and "done" cannot disagree.
    "CREATE TABLE IF NOT EXISTS request_ledger ("
    " request_id TEXT PRIMARY KEY, route TEXT, status INTEGER, body TEXT,"
    " at TEXT)",
    # The record minus what an append-only annotation hides, minus the history
    # of a machine retired with "purge history" (rows older than its
    # `retired_at`). Only the NEWEST annotation on a row decides, so
    # `reinstated` undoes a hide without touching anything that came before.
    # raw-log: the view IS the definition of the effective record.
    "CREATE VIEW IF NOT EXISTS lem_machine_log_effective AS "
    "SELECT l.* FROM lem_machine_log l "
    "WHERE NOT EXISTS (SELECT 1 FROM log_annotation a "
    "  WHERE a.id = (SELECT MAX(b.id) FROM log_annotation b "
    "                WHERE b.log_id = l.id) "
    "  AND a.label IN ('replay_duplicate', 'import_leftover')) "
    "AND NOT EXISTS (SELECT 1 FROM lem_machine_config c "
    "  WHERE c.machine_uid = l.machine_uid AND c.retired_at IS NOT NULL "
    "  AND l.ts < c.retired_at)",
)


def default_store_path() -> str:
    """`LEM_STORE_PATH`, else the production path on Windows, else a `store/`
    folder beside the data directory (never inside the release)."""
    configured = os.environ.get("LEM_STORE_PATH", "").strip()
    if configured:
        return configured
    if os.name == "nt":
        return DEFAULT_WINDOWS_STORE
    try:
        import tray
        base = os.path.dirname(os.path.abspath(tray.data_dir()))
    except Exception:                                  # noqa: BLE001
        base = os.path.expanduser("~/.lem")
    return os.path.join(base, "store", "lem.db")


def is_local_store(gateway) -> bool:
    """Is this gateway LEM's own store (as opposed to LabCore)?

    Asked by name rather than by `isinstance`, so a test double that wraps a
    store and forwards to it answers the same as the store it wraps.
    """
    probe = gateway
    for _ in range(4):
        if probe is None:
            return False
        if getattr(probe, "IS_LEM_STORE", False) is True:
            return True
        probe = getattr(probe, "inner", None) or getattr(probe, "_inner", None)
    return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _error(exc: BaseException) -> dict:
    """LabCore's error shape for a local failure.

    "database is locked" is the one local failure that is worth retrying, so
    it says so the same way LabCore's busy queue does — `check_write` then
    reports 503 + Retry-After rather than "never retry this".
    """
    text = "{0}: {1}".format(type(exc).__name__, exc)
    out = {"error": text}
    if "locked" in str(exc).lower() or "busy" in str(exc).lower():
        out["busy"] = True
        out["retry_after"] = 1
    return out


class StoreReadOnly(RuntimeError):
    """A write was attempted on a store opened read-only."""


class LocalStoreGateway:
    """LEM's store: a local SQLite file with the LabCore gateways' surface."""

    IS_LEM_STORE = True

    #: How long a connection waits for the file lock before answering "busy".
    BUSY_TIMEOUT_MS = 10000

    def __init__(self, path: str, read_only: bool = False) -> None:
        self.path = str(path)
        self.read_only = bool(read_only)
        self._wlock = threading.RLock()
        self._tx = threading.local()
        self._readers: "queue.LifoQueue[sqlite3.Connection]" = queue.LifoQueue()
        self._all: List[sqlite3.Connection] = []
        self._all_lock = threading.Lock()
        self._closed = False
        self._open_error = ""
        self._writer: Optional[sqlite3.Connection] = None
        if self.read_only:
            # Nothing is created, migrated or declared. A missing file stays
            # missing and every read says so.
            if not os.path.exists(self.path):
                self._open_error = ("the LEM store is not at {0} (opened "
                                    "read-only, so it was not created)"
                                    .format(self.path))
            return
        folder = os.path.dirname(os.path.abspath(self.path))
        if folder:
            os.makedirs(folder, exist_ok=True)
        self._writer = self._connect()
        self._migrate()

    # ── connections ───────────────────────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
        if self.read_only:
            uri = "file:{0}?mode=ro".format(
                os.path.abspath(self.path).replace("?", "%3f"))
            con = sqlite3.connect(uri, uri=True, check_same_thread=False,
                                  isolation_level=None,
                                  timeout=self.BUSY_TIMEOUT_MS / 1000.0)
        else:
            con = sqlite3.connect(self.path, check_same_thread=False,
                                  isolation_level=None,
                                  timeout=self.BUSY_TIMEOUT_MS / 1000.0)
        con.row_factory = sqlite3.Row
        if not self.read_only:
            con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout={0}".format(self.BUSY_TIMEOUT_MS))
        with self._all_lock:
            self._all.append(con)
        return con

    @contextlib.contextmanager
    def _reader(self):
        if self._closed:
            raise sqlite3.ProgrammingError("the LEM store is closed")
        if self._open_error:
            raise sqlite3.OperationalError(self._open_error)
        try:
            con = self._readers.get_nowait()
        except queue.Empty:
            con = self._connect()
        try:
            yield con
        finally:
            if self._closed:
                con.close()
            else:
                self._readers.put(con)

    def _in_tx(self) -> bool:
        return getattr(self._tx, "depth", 0) > 0

    # ── schema ────────────────────────────────────────────────────────
    def _migrate(self) -> None:
        """Bring the file to §5.2's shape. Idempotent; runs once per open.

        A log declared the OLD way (LabCore's seven columns, no key, no
        triggers) is rebuilt rather than ALTERed: `received_at` needs a
        non-constant default, which `ALTER TABLE ADD COLUMN` refuses, and a
        table that quietly lacked its triggers would be an append-only record
        in name only. Rows keep their order; `id` takes the old rowid.
        """
        con = self._writer
        with self._wlock:
            con.execute("BEGIN IMMEDIATE")
            try:
                cols = [r[1] for r in con.execute(
                    "PRAGMA table_info('lem_machine_log')").fetchall()]
                if cols and "id" not in cols:
                    con.execute("ALTER TABLE lem_machine_log RENAME TO "
                                "lem_machine_log_v0")
                    con.execute(_LOG_DDL)
                    con.execute(
                        "INSERT INTO lem_machine_log (id, {0}, origin) "
                        "SELECT rowid, {0}, 'legacy_labcore' "
                        "FROM lem_machine_log_v0 ORDER BY rowid".format(
                            ", ".join(LOG_COLUMNS)))
                    con.execute("DROP TABLE lem_machine_log_v0")
                cfg = [r[1] for r in con.execute(
                    "PRAGMA table_info('lem_machine_config')").fetchall()]
                if cfg and "retired_at" not in cfg:
                    con.execute("ALTER TABLE lem_machine_config "
                                "ADD COLUMN retired_at TEXT")
                for stmt in _DDL:
                    con.execute(stmt)
                con.execute(
                    "INSERT INTO store_meta (key, value) VALUES "
                    "('schema_version', ?) ON CONFLICT(key) DO UPDATE SET "
                    "value = excluded.value", [str(SCHEMA_VERSION)])
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise

    # ── the gateway surface ───────────────────────────────────────────
    def is_running(self) -> bool:
        """True when the store's file answers a trivial read.

        On the engine directly rather than through `read_sql`, as LabCore's
        `is_running` is a status GET and never a queue op: a liveness probe
        must not show up as a read in anybody's count.
        """
        try:
            with self._reader() as con:
                return con.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    def sql(self, sql: str, args: Optional[list] = None, **_kw) -> dict:
        if self.read_only:
            return {"error": "the LEM store is open read-only (a candidate "
                             "boot): nothing was written",
                    "read_only": True}
        if self._closed or self._writer is None:
            return {"error": "the LEM store is closed"}
        try:
            with self._wlock:
                cur = self._writer.execute(sql, args or [])
                count = cur.rowcount if cur.rowcount != -1 else 0
                return {"ok": True, "rows_affected": count}
        except sqlite3.Error as exc:
            return _error(exc)

    def read_sql(self, sql: str, args: Optional[list] = None, **_kw) -> dict:
        try:
            if self._in_tx():
                # Inside a transaction a read sees the transaction's own
                # writes — which only the writer connection can.
                with self._wlock:
                    return self._fetch(self._writer, sql, args)
            with self._reader() as con:
                return self._fetch(con, sql, args)
        except sqlite3.Error as exc:
            return _error(exc)

    @staticmethod
    def _fetch(con, sql, args) -> dict:
        cur = con.execute(sql, args or [])
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description] if cur.description else []
        return {"ok": True,
                "rows": [{k: r[i] for i, k in enumerate(columns)} for r in rows],
                "columns": columns}

    def write(self, operation: str, params: dict, **_kw) -> dict:
        handler = getattr(self, "_op_" + str(operation), None)
        if handler is None:
            return {"error": "Unknown operation for the LEM store: {0}. "
                             "Results are LabCore's, not LEM's.".format(operation)}
        return handler(params or {})

    def _op_raw_sql(self, p: dict) -> dict:
        return self.sql(p.get("sql", ""), p.get("args") or [])

    def _op_read_sql(self, p: dict) -> dict:
        return self.read_sql(p.get("sql", ""), p.get("args") or [])

    @contextlib.contextmanager
    def transaction(self):
        """One write transaction; nested calls join the outer one.

        Holds the writer lock for its whole length, so another thread's write
        waits rather than interleaving — that is what "single writer" means
        here. Reads from OTHER threads carry on against the last committed
        state (WAL). An exception anywhere inside rolls everything back.
        """
        if self.read_only:
            raise StoreReadOnly("the LEM store is open read-only")
        if self._closed or self._writer is None:
            raise sqlite3.ProgrammingError("the LEM store is closed")
        with self._wlock:
            depth = getattr(self._tx, "depth", 0)
            if depth:
                self._tx.depth = depth + 1
                try:
                    yield self
                finally:
                    self._tx.depth = depth
                return
            self._writer.execute("BEGIN IMMEDIATE")
            self._tx.depth = 1
            try:
                yield self
            except BaseException:
                self._tx.depth = 0
                self._writer.execute("ROLLBACK")
                raise
            self._tx.depth = 0
            self._writer.execute("COMMIT")

    # ── what it is ────────────────────────────────────────────────────
    def health(self) -> dict:
        size = 0
        for suffix in ("", "-wal"):
            try:
                size += os.path.getsize(self.path + suffix)
            except OSError:
                pass
        out = {"path": self.path, "read_only": self.read_only, "bytes": size,
               "schema_version": None}
        # On the engine, like `is_running`: `/healthz` must never count as a
        # read in anybody's tally.
        try:
            with self._reader() as con:
                row = con.execute("SELECT value FROM store_meta "
                                  "WHERE key = 'schema_version'").fetchone()
            out["schema_version"] = int(row[0]) if row else None
        except (sqlite3.Error, TypeError, ValueError) as exc:
            out["error"] = "{0}: {1}".format(type(exc).__name__, exc)
        return out

    def _pragmas(self, con) -> Dict[str, Any]:
        out = {}
        for name in ("journal_mode", "synchronous", "foreign_keys",
                     "busy_timeout"):
            out[name] = con.execute("PRAGMA " + name).fetchone()[0]
        return out

    def writer_pragmas(self) -> Dict[str, Any]:
        with self._wlock:
            return self._pragmas(self._writer)

    def reader_pragmas(self) -> Dict[str, Any]:
        with self._reader() as con:
            return self._pragmas(con)

    def close(self) -> None:
        self._closed = True
        with self._all_lock:
            conns, self._all = self._all, []
        for con in conns:
            try:
                con.close()
            except Exception:                          # noqa: BLE001
                pass

    def __del__(self):                                 # pragma: no cover
        try:
            self.close()
        except Exception:                              # noqa: BLE001
            pass

    def __repr__(self) -> str:
        return "<LocalStoreGateway {0}{1}>".format(
            self.path, " (read-only)" if self.read_only else "")
