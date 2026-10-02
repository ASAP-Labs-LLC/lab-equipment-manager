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
  `lem_machine_log` and `log_annotation`, and refuse any INSERT that would
  collide with a row already there — so `INSERT OR REPLACE` / `REPLACE INTO`,
  whose conflict deletion skips DELETE triggers, cannot rewrite a row either.
  Anybody opening `lem.db` with the sqlite3 shell gets the same answer as the
  app (S1). Dedupe an ingest with `WHERE NOT EXISTS`, never `OR IGNORE`.
* **No way round it through the store.** Every connection runs with
  `recursive_triggers` on (a REPLACE then fires the DELETE guard as well) and
  an authorizer (`_guard`) that refuses DROP/ALTER against the record, its
  view and its guards, refuses every CREATE TRIGGER and every TEMP table,
  view or trigger (a temp object of the same name stands in front of the
  real one; a trigger can RAISE(IGNORE) a write that then answers ok), and
  refuses switching off the PRAGMAs the guarantee rests on. Readers are `query_only`. The bare file can still drop
  a trigger — SQLite has no rule against its owner — or save a look-alike
  under a guard's name, so every open compares each guard trigger and the
  effective view with its declaration BY ITS SQL, rebuilds any that differ,
  drops any trigger LEM did not write, and `health()` names what differs
  (`guards_missing`, `foreign_triggers`) and what the open repaired
  (`guards_repaired`).
* **One schema.** Every CREATE outside `main` (`temp.x`, however spelled)
  and every ATTACH is refused: an unqualified name resolves in `temp` first,
  so an object there stands in front of the record.
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
import hashlib
import hmac
import json
import os
import secrets
import queue
import re
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

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
    "annotation_approval_member", "bench_cursor", "bench_token", "bench_source", "result_ledger",
    "result_conflict", "projection_outbox", "import_run", "log_digest",
    "store_meta", "unknown_records", "request_ledger", "lem_machine_config",
    "bench_record",
)

#: The seven columns LabCore's `lem_machine_log` has, in its order. Every
#: server INSERT names exactly these (W2b), and every one of them still works.
LOG_COLUMNS = ("machine_uid", "ts", "kind", "lab_id", "test_name", "value",
               "detail")

#: The columns the store adds to the record. None of them is written by the
#: server's INSERTs or the bench's; each has a default or stays NULL.
CUSTODY_COLUMNS = ("id", "origin", "bench_epoch", "bench_seq", "content_key",
                   "legacy_key", "legacy_rowid", "received_at")

#: Annotation labels that HIDE a row from the effective view — and only under
#: an approved `annotation_approval` for the row's bench (D7). Every other
#: label (`replay_candidate`, `ambiguous_repeat`, `probable_duplicate`) is
#: visible, and `reinstated` cancels an earlier hide because only the NEWEST
#: hide-or-reinstate annotation on a row counts.
HIDING_LABELS = ("replay_duplicate", "import_leftover")

#: The triggers that make the record append-only. `health()` names any that
#: are missing (the bare file can drop one; the store cannot) and every open
#: re-declares them.
GUARD_TRIGGERS = ("lem_log_no_update", "lem_log_no_delete",
                  "lem_log_no_overwrite", "ann_no_update", "ann_no_delete",
                  "ann_no_overwrite", "ann_hide_needs_approval",
                  "apr_no_update", "apr_no_delete", "apr_no_overwrite",
                  "apr_signed", "apm_no_update", "apm_no_delete",
                  "apm_no_overwrite", "apm_names_a_candidate", "cfg_no_future_retire_insert",
                  "cfg_no_future_retire_update", "bench_record_no_update",
                  "bench_record_no_delete", "bench_record_no_overwrite")

#: Every schema object the guarantee rests on and that a statement could
#: replace with a look-alike: the guard triggers and the effective view. Each
#: is checked by its SQL, never by its name alone — a no-op trigger saved
#: under `lem_log_no_update` has the right name and guards nothing (round 4's
#: critic did exactly that with the bare file).
GUARD_OBJECTS = GUARD_TRIGGERS + ("lem_machine_log_effective",)

#: What the store will not let a statement drop, alter or hang a trigger on:
#: the record, the annotations that decide how it counts, the approvals behind
#: them, the configuration row whose `retired_at` the effective view reads,
#: and the view itself. Only `_migrate`, which runs before the guard is
#: installed, reshapes these.
PROTECTED = frozenset(("lem_machine_log", "log_annotation",
                       "annotation_approval", "annotation_approval_member",
                       "lem_machine_config",
                       "lem_machine_log_effective", "bench_record"))

#: What may not be DROPPED or ALTERed: the above, plus every table §5.2 adds.
#: `request_ledger` is W2's memory of what was already done — dropped, the
#: next retry of a save that DID commit would be performed a second time —
#: and the others are the bench's cursors, tokens and outbox, whose loss is a
#: silent re-ingest. They are the store's to shape, in `_migrate`, only.
UNDROPPABLE = PROTECTED | frozenset(STORE_TABLES)

#: Every authorizer action that creates a schema object. Each carries the
#: name of the database it creates in, and only `main` is allowed (`_guard`).
_CREATE_ACTIONS = frozenset((
    sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_CREATE_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_INDEX, sqlite3.SQLITE_CREATE_TEMP_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_TRIGGER, sqlite3.SQLITE_CREATE_TEMP_VIEW,
    sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_CREATE_VIEW,
    sqlite3.SQLITE_CREATE_VTABLE))

#: PRAGMAs a statement may not SET on a store connection: each one switches
#: off part of the guarantee (schema writes, the REPLACE-fires-DELETE rule,
#: the annotation's foreign key, a reader's query-only mode) or its
#: durability. Reading any of them is fine.
_LOCKED_PRAGMAS = frozenset(("writable_schema", "recursive_triggers",
                             "foreign_keys", "query_only", "journal_mode",
                             "synchronous", "trusted_schema",
                             "ignore_check_constraints", "legacy_alter_table"))


#: The two tables only `LocalStoreGateway.record_approval` writes.
APPROVAL_TABLES = frozenset(("annotation_approval",
                             "annotation_approval_member"))

#: The fields an approval's HMAC covers: every one the decision rests on.
APPROVAL_SIGNED_FIELDS = ("machine_uid", "rule", "run_id", "candidates",
                          "approved_by", "approved_at", "decision")

#: Set (per thread) only inside `record_approval`.
_APPROVAL_WRITER = threading.local()


class ApprovalRefused(RuntimeError):
    """An approval `record_approval` will not write, and why."""


def _guard(action, arg1, arg2, _db, _src):
    """sqlite3 authorizer for every store connection after migration.

    Refuses, with SQLite's own "not authorized" error (which `_error` hands
    back in LabCore's error shape), DDL that would unguard the record. Plain
    reads and writes are not its business: the triggers own those, and they
    hold for the bare file too.
    """
    a = sqlite3
    if (action == a.SQLITE_INSERT and arg1 in APPROVAL_TABLES
            and not getattr(_APPROVAL_WRITER, "on", False)):
        # D7. An approval is written by `record_approval` only, which checks
        # its signature and names the rows it covers in the same
        # transaction. Round-3 critic: a bare `INSERT INTO
        # annotation_approval` with no signature, by 'anyone', covering a
        # one-row unit, let a later write hide an unrelated genuine row.
        return a.SQLITE_DENY
    if action in (a.SQLITE_ATTACH, a.SQLITE_DETACH):
        # A second schema is a second place for an unqualified name to
        # resolve. LEM has one file and never attaches another.
        return a.SQLITE_DENY
    if action in _CREATE_ACTIONS and (_db or "").lower() != "main":
        # Round 4: refusing the TEMP *keyword* was not enough. `CREATE TABLE
        # temp.lem_machine_log (...)` reaches this function as a plain
        # SQLITE_CREATE_TABLE whose database is "temp", and stood in front of
        # the record exactly like the TEMP-keyword form did. The rule is
        # where the object goes, however the statement spells it: `main`,
        # or nowhere.
        return a.SQLITE_DENY
    if action in (a.SQLITE_CREATE_TEMP_TABLE, a.SQLITE_CREATE_TEMP_VIEW,
                  a.SQLITE_CREATE_TEMP_TRIGGER, a.SQLITE_CREATE_TEMP_INDEX,
                  a.SQLITE_CREATE_TRIGGER):
        # Nothing may stand in front of a table. SQLite resolves an
        # unqualified name in `temp` before `main`, so a TEMP table named
        # `lem_machine_log` on the writer took every later INSERT ("1 row")
        # into a scratch table that dies with the connection, while the
        # record stayed empty (round 3's critic). A trigger on ANY table can
        # do the same with RAISE(IGNORE): the write answers ok and no row
        # exists. LEM creates neither after `_migrate` (which declares the
        # guard triggers before this authorizer is installed), so both are
        # refused outright rather than by a list of names someone has to
        # remember to extend.
        return a.SQLITE_DENY
    if action in (a.SQLITE_DROP_TABLE, a.SQLITE_DROP_TEMP_TABLE,
                  a.SQLITE_DROP_VIEW, a.SQLITE_DROP_TEMP_VIEW):
        return a.SQLITE_DENY if arg1 in UNDROPPABLE else a.SQLITE_OK
    if action in (a.SQLITE_DROP_TRIGGER, a.SQLITE_DROP_TEMP_TRIGGER,
                  a.SQLITE_DROP_INDEX, a.SQLITE_DROP_TEMP_INDEX):
        if arg1 in GUARD_TRIGGERS or arg2 in PROTECTED:
            return a.SQLITE_DENY
        return a.SQLITE_OK
    if action == a.SQLITE_ALTER_TABLE:
        return a.SQLITE_DENY if arg2 in UNDROPPABLE else a.SQLITE_OK
    if action == a.SQLITE_PRAGMA:
        if arg2 is not None and str(arg1).lower() in _LOCKED_PRAGMAS:
            return a.SQLITE_DENY
        return a.SQLITE_OK
    return a.SQLITE_OK

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
    # REPLACE (and `INSERT OR REPLACE`) resolves a collision by DELETING the
    # row already there, and SQLite skips DELETE triggers for that unless
    # `recursive_triggers` is on, which no sqlite3 shell turns on. Without
    # this guard `INSERT OR REPLACE ... (id, ...)` rewrote a reading in
    # place. It refuses ANY insert that would collide with a row already in
    # the record, on each key that can collide (the id and both unique
    # custody keys, with the same NULL rules as their indexes), whatever
    # conflict clause the statement carries, OR IGNORE included: a duplicate
    # is an error its caller sees, never a silent no-op or a silent rewrite.
    # raw-log: the guard must see every row, hidden ones included.
    "CREATE TRIGGER IF NOT EXISTS lem_log_no_overwrite BEFORE INSERT ON "
    "lem_machine_log WHEN "
    " EXISTS (SELECT 1 FROM lem_machine_log WHERE id = NEW.id)"
    " OR (NEW.bench_seq IS NOT NULL AND EXISTS (SELECT 1 FROM lem_machine_log"
    "     WHERE machine_uid = NEW.machine_uid AND bench_epoch = NEW.bench_epoch"
    "     AND bench_seq = NEW.bench_seq))"
    " OR (NEW.legacy_key IS NOT NULL AND EXISTS (SELECT 1 FROM lem_machine_log"
    "     WHERE legacy_key = NEW.legacy_key)) "
    "BEGIN SELECT RAISE(ABORT, 'lem_machine_log is append-only: that row is "
    "already in the record and cannot be replaced'); END",
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
    "CREATE TRIGGER IF NOT EXISTS ann_no_overwrite BEFORE INSERT ON "
    "log_annotation WHEN EXISTS (SELECT 1 FROM log_annotation "
    "WHERE id = NEW.id) "
    "BEGIN SELECT RAISE(ABORT, 'log_annotation is append-only: that "
    "annotation is already in the record and cannot be replaced'); END",
    "CREATE TABLE IF NOT EXISTS annotation_approval ("
    " id INTEGER PRIMARY KEY, machine_uid TEXT, rule TEXT, run_id TEXT,"
    " candidates INTEGER, examples TEXT, qc_impact TEXT, approved_by TEXT,"
    " approved_at TEXT, decision TEXT, signature TEXT)",
    # D7: an approval is the record of Ryan's decision, and the evidence for
    # every row it hid. Edited or deleted afterwards, a hidden row would have
    # no say-so behind it (or a different one), so it is append-only like
    # the annotations it authorises — and it is refused unsigned, or with a
    # decision that is neither of the two.
    "CREATE TRIGGER IF NOT EXISTS apr_no_update BEFORE UPDATE ON "
    "annotation_approval "
    "BEGIN SELECT RAISE(ABORT, 'annotation_approval is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS apr_no_delete BEFORE DELETE ON "
    "annotation_approval "
    "BEGIN SELECT RAISE(ABORT, 'annotation_approval is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS apr_no_overwrite BEFORE INSERT ON "
    "annotation_approval WHEN EXISTS (SELECT 1 FROM annotation_approval "
    "WHERE id = NEW.id) "
    "BEGIN SELECT RAISE(ABORT, 'annotation_approval is append-only: that "
    "approval is already in the record and cannot be replaced'); END",
    "CREATE TRIGGER IF NOT EXISTS apr_signed BEFORE INSERT ON "
    "annotation_approval WHEN NEW.decision IS NULL "
    " OR NEW.decision NOT IN ('approved', 'rejected')"
    " OR NEW.approved_by IS NULL OR trim(NEW.approved_by) = ''"
    " OR NEW.approved_at IS NULL OR trim(NEW.approved_at) = ''"
    # An approval that hides anything carries the HMAC the approval flow
    # signed it with (64 hex digits; `record_approval` verifies it, the dry
    # run re-verifies every one) and says how many rows it covers.
    " OR (NEW.decision = 'approved' AND (NEW.signature IS NULL"
    "     OR length(NEW.signature) <> 64"
    "     OR NEW.signature GLOB '*[^0-9a-f]*'"
    "     OR typeof(NEW.candidates) <> 'integer' OR NEW.candidates < 1)) "
    "BEGIN SELECT RAISE(ABORT, 'an annotation_approval needs a decision "
    "(approved or rejected), the person who made it and when, and an "
    "approval needs its signature and the number of rows it covers'); END",
    # The rows an approval covers, named one by one: the candidate set Ryan
    # was shown, written in the same transaction as the approval. `seq`
    # numbers them 0 .. candidates-1, so an approval can never cover more
    # rows than it says it does, and every row is on the approval's bench.
    "CREATE TABLE IF NOT EXISTS annotation_approval_member ("
    " approval_id INTEGER NOT NULL REFERENCES annotation_approval(id),"
    " seq INTEGER NOT NULL,"
    " log_id INTEGER NOT NULL REFERENCES lem_machine_log(id),"
    " PRIMARY KEY (approval_id, seq), UNIQUE (approval_id, log_id))",
    "CREATE TRIGGER IF NOT EXISTS apm_no_update BEFORE UPDATE ON "
    "annotation_approval_member BEGIN SELECT RAISE(ABORT, "
    "'annotation_approval_member is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS apm_no_delete BEFORE DELETE ON "
    "annotation_approval_member BEGIN SELECT RAISE(ABORT, "
    "'annotation_approval_member is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS apm_no_overwrite BEFORE INSERT ON "
    "annotation_approval_member WHEN EXISTS (SELECT 1 FROM "
    "annotation_approval_member WHERE approval_id = NEW.approval_id AND "
    "(seq = NEW.seq OR log_id = NEW.log_id)) BEGIN SELECT RAISE(ABORT, "
    "'annotation_approval_member is append-only: that row is already "
    "named'); END",
    # raw-log: the member's bench, hidden or not.
    "CREATE TRIGGER IF NOT EXISTS apm_names_a_candidate BEFORE INSERT ON "
    "annotation_approval_member WHEN NOT EXISTS (SELECT 1 FROM "
    "annotation_approval p WHERE p.id = NEW.approval_id"
    " AND p.decision = 'approved' AND p.signature IS NOT NULL"
    " AND typeof(NEW.seq) = 'integer' AND NEW.seq >= 0"
    " AND NEW.seq < p.candidates"
    " AND p.machine_uid = (SELECT m.machine_uid FROM lem_machine_log m"
    "                      WHERE m.id = NEW.log_id)) "
    "BEGIN SELECT RAISE(ABORT, 'an approval names at most as many rows as "
    "it covers, each on its own bench'); END",
    # §10.5 "no automatic hiding anywhere", kept by the FILE: a hiding
    # annotation must name an APPROVED approval for the row's own bench and
    # for that rule (`resend` hides as `replay_duplicate`). Without this any
    # statement reaching the store could take a reading out of every QC
    # chart with no approval behind it.
    # raw-log: the guard reads the row's bench, hidden or not.
    "CREATE TRIGGER IF NOT EXISTS ann_hide_needs_approval BEFORE INSERT ON "
    "log_annotation WHEN NEW.label IN ('replay_duplicate', 'import_leftover') "
    "AND NOT EXISTS (SELECT 1 FROM annotation_approval p "
    " WHERE p.id = NEW.approval_id AND p.decision = 'approved'"
    " AND p.signature IS NOT NULL"
    # ...and the approval NAMES this row: approving one bench's replays is
    # not a licence to hide any row of that bench.
    " AND EXISTS (SELECT 1 FROM annotation_approval_member mm"
    "             WHERE mm.approval_id = p.id AND mm.log_id = NEW.log_id)"
    " AND p.machine_uid = (SELECT m.machine_uid FROM lem_machine_log m"
    "                      WHERE m.id = NEW.log_id)"
    # An approval's rule is a label, or a label and a storm day
    # (`replay_duplicate@storm:2026-08-18`): the storm is approved on its own.
    " AND (substr(p.rule || '@', 1, instr(p.rule || '@', '@') - 1)"
    "      = NEW.label"
    "      OR (substr(p.rule || '@', 1, instr(p.rule || '@', '@') - 1)"
    "          = 'resend' AND NEW.label = 'replay_duplicate'))) "
    "BEGIN SELECT RAISE(ABORT, 'hiding a reading needs an approved "
    "annotation_approval for its bench and rule that names the row (D7): "
    "nothing was hidden'); END",
    # The machine's configuration row, declared HERE (with its owner's exact
    # columns from machine_configs.CONFIG_DDL) because the effective view
    # reads `retired_at` from it and a view naming a missing table fails every
    # reader of the log. `retired_at` is store-only: no bench reads it.
    "CREATE TABLE IF NOT EXISTS lem_machine_config ("
    " machine_uid TEXT PRIMARY KEY, title TEXT NOT NULL, config TEXT,"
    " updated_at TEXT, updated_by TEXT, retired_at TEXT)",
    # "Purge history" hides a machine's rows with `ts < retired_at`. A
    # retirement stamped in the future (round 4's critic used '9999') hides
    # readings that have not happened yet — everything a re-registered bench
    # files from then on — with no annotation and no approval. The app stamps
    # `retired_at` from its own local clock, so the store refuses anything
    # later than its local clock plus a day (slack for a server whose time
    # zone and SQLite's 'localtime' disagree), and anything that is not text
    # (an integer compares below every text `ts` and would read as "never").
    "CREATE TRIGGER IF NOT EXISTS cfg_no_future_retire_insert BEFORE INSERT "
    "ON lem_machine_config WHEN NEW.retired_at IS NOT NULL AND ("
    " typeof(NEW.retired_at) <> 'text' OR NEW.retired_at > "
    " strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime', '+1 day')) "
    "BEGIN SELECT RAISE(ABORT, 'lem_machine_config.retired_at must be a "
    "time no later than now: a retirement in the future would hide readings "
    "not yet taken'); END",
    "CREATE TRIGGER IF NOT EXISTS cfg_no_future_retire_update BEFORE UPDATE "
    "OF retired_at ON lem_machine_config WHEN NEW.retired_at IS NOT NULL AND ("
    " typeof(NEW.retired_at) <> 'text' OR NEW.retired_at > "
    " strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime', '+1 day')) "
    "BEGIN SELECT RAISE(ABORT, 'lem_machine_config.retired_at must be a "
    "time no later than now: a retirement in the future would hide readings "
    "not yet taken'); END",
    "CREATE TABLE IF NOT EXISTS bench_cursor ("
    " machine_uid TEXT, bench_epoch TEXT, acked_seq INTEGER NOT NULL,"
    " durable_seq INTEGER NOT NULL DEFAULT 0, digest TEXT, records_total INTEGER,"
    " first_seen TEXT, last_seen TEXT, road TEXT, module_version TEXT,"
    " clock_skew_s REAL, labcore_failures_5min INTEGER, mode TEXT,"
    " stats TEXT, digest_mismatch TEXT,"
    " PRIMARY KEY (machine_uid, bench_epoch))",
    "CREATE TABLE IF NOT EXISTS bench_token ("
    " machine_uid TEXT PRIMARY KEY, token_sha256 TEXT, issued_at TEXT,"
    " issued_by TEXT, revoked_at TEXT, pending_reenrol_at TEXT,"
    " enroll_key_sha256 TEXT)",
    "CREATE TABLE IF NOT EXISTS bench_source ("
    " machine_uid TEXT, src TEXT, lineage TEXT, cursor TEXT, snapshot BLOB,"
    " updated_at TEXT, snapshot_sha TEXT, announced_sha TEXT,"
    " PRIMARY KEY (machine_uid, src))",
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
    # A record of a kind this server does not know is parked once, however
    # often it is resent (bench_api).
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_unknown_bench ON unknown_records"
    "(machine_uid, bench_epoch, bench_seq)",
    # Custody of every record a v2 bench sends, whatever its kind, exactly as
    # it was sent: the canonical body the bench's CRC and running digest were
    # computed over (`bench_api`). The machine log holds what a person reads;
    # this holds what the bench said, so the per-epoch digest can be
    # recomputed from the store alone (T-P11 reconciliation) and a restore
    # can be compared record for record. Append-only like the record: a held
    # (uid, epoch, seq) is never rewritten, and a resend is answered from it.
    "CREATE TABLE IF NOT EXISTS bench_record ("
    " machine_uid TEXT NOT NULL, bench_epoch TEXT NOT NULL,"
    " bench_seq INTEGER NOT NULL, kind TEXT, ts TEXT, body TEXT NOT NULL,"
    " received_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),"
    " PRIMARY KEY (machine_uid, bench_epoch, bench_seq))",
    "CREATE TRIGGER IF NOT EXISTS bench_record_no_update BEFORE UPDATE ON "
    "bench_record BEGIN SELECT RAISE(ABORT, 'bench_record is append-only: "
    "what a bench sent is never rewritten'); END",
    "CREATE TRIGGER IF NOT EXISTS bench_record_no_delete BEFORE DELETE ON "
    "bench_record BEGIN SELECT RAISE(ABORT, 'bench_record is append-only: "
    "what a bench sent is never rewritten'); END",
    # As `lem_log_no_overwrite`: REPLACE's conflict deletion skips DELETE
    # triggers in a shell without recursive_triggers.
    "CREATE TRIGGER IF NOT EXISTS bench_record_no_overwrite BEFORE INSERT ON "
    "bench_record WHEN EXISTS (SELECT 1 FROM bench_record WHERE "
    "machine_uid = NEW.machine_uid AND bench_epoch = NEW.bench_epoch AND "
    "bench_seq = NEW.bench_seq) BEGIN SELECT RAISE(ABORT, 'bench_record is "
    "append-only: that record is already held and cannot be replaced'); END",
    # W2: a browser retry carrying the same X-Request-Id is answered from here
    # instead of being done twice. Written in the SAME transaction as the
    # change, so "recorded as done" and "done" cannot disagree.
    # `route`, `who` and `fingerprint` scope the id to ONE request: reused
    # for anything else it is refused, never replayed.
    "CREATE TABLE IF NOT EXISTS request_ledger ("
    " request_id TEXT PRIMARY KEY, route TEXT, status INTEGER, body TEXT,"
    " at TEXT, who TEXT, fingerprint TEXT)",
    # The record minus what an append-only annotation hides, minus the history
    # of a machine retired with "purge history" (rows older than its
    # `retired_at`). Only the NEWEST deciding annotation on a row counts — a
    # hide or a `reinstated`; a later review note (`replay_candidate`, ...)
    # neither hides nor unhides — so `reinstated` undoes a hide without
    # touching anything that came before. A hide counts only through an
    # APPROVED approval for the row's own bench: the trigger above refuses
    # any other, and the view does not trust that a trigger was in place
    # when the annotation was written (the bare file can drop one).
    # raw-log: the view IS the definition of the effective record.
    "CREATE VIEW IF NOT EXISTS lem_machine_log_effective AS "
    "SELECT l.* FROM lem_machine_log l "
    "WHERE NOT EXISTS (SELECT 1 FROM log_annotation a "
    "  WHERE a.id = (SELECT MAX(b.id) FROM log_annotation b "
    "                WHERE b.log_id = l.id AND b.label IN "
    "                ('replay_duplicate', 'import_leftover', 'reinstated')) "
    "  AND a.label IN ('replay_duplicate', 'import_leftover') "
    "  AND EXISTS (SELECT 1 FROM annotation_approval p "
    "    WHERE p.id = a.approval_id AND p.decision = 'approved' "
    "    AND p.signature IS NOT NULL "
    "    AND p.machine_uid = l.machine_uid "
    "    AND EXISTS (SELECT 1 FROM annotation_approval_member mm "
    "      WHERE mm.approval_id = p.id AND mm.log_id = l.id))) "
    "AND NOT EXISTS (SELECT 1 FROM lem_machine_config c "
    "  WHERE c.machine_uid = l.machine_uid AND c.retired_at IS NOT NULL "
    "  AND l.ts < c.retired_at)",
)


def _guard_ddl() -> Dict[str, str]:
    """Each guard object's name -> the statement in `_DDL` that declares it."""
    out = {}
    for stmt in _DDL:
        m = re.match(r"CREATE (?:TRIGGER|VIEW) IF NOT EXISTS (\w+)", stmt)
        if m:
            out[m.group(1)] = stmt
    missing = set(GUARD_OBJECTS) - set(out)
    if missing:                                       # pragma: no cover
        raise RuntimeError("no DDL for guard objects: {0}".format(missing))
    return out


_CANON: Dict[str, str] = {}
_CANON_LOCK = threading.Lock()


def _canonical_sql() -> Dict[str, str]:
    """What `sqlite_master.sql` holds for each guard object when this module
    declared it. Taken from SQLite itself (an in-memory database running the
    same `_DDL`) rather than retyped, so the comparison can never drift from
    the declaration by a space."""
    with _CANON_LOCK:
        if not _CANON:
            mem = sqlite3.connect(":memory:")
            try:
                for stmt in _DDL:
                    mem.execute(stmt)
                for name, sql in mem.execute(
                        "SELECT name, sql FROM sqlite_master "
                        "WHERE type IN ('trigger', 'view')"):
                    if name in GUARD_OBJECTS:
                        _CANON[name] = sql
            finally:
                mem.close()
        return dict(_CANON)


def _schema_audit(con) -> Dict[str, List[str]]:
    """Which guard objects are missing OR differ from their declaration, and
    which triggers in the file LEM did not write. The store refuses CREATE
    TRIGGER after `_migrate`, so any trigger outside `GUARD_TRIGGERS` came
    from outside the store — and a trigger can make a write vanish
    (`RAISE(IGNORE)`) or rewrite one, so none is trusted."""
    canon = _canonical_sql()
    have = {name: (kind, sql) for kind, name, sql in con.execute(
        "SELECT type, name, sql FROM main.sqlite_master "
        "WHERE type IN ('trigger', 'view')")}
    wrong = [n for n in GUARD_OBJECTS
             if n not in have or have[n][1] != canon.get(n)]
    foreign = sorted(n for n, (kind, _sql) in have.items()
                     if kind == "trigger" and n not in GUARD_TRIGGERS)
    return {"guards_missing": wrong, "foreign_triggers": foreign}


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


def _stored_schema_version(path: str) -> Optional[int]:
    """The schema version a store file says it has, read without changing
    it; None for a new file or one that never recorded a version (LabCore's
    old shape, which `_migrate` rebuilds and has always rebuilt)."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    # A file that cannot be read is NOT a file with no version: that error
    # propagates and the open fails, rather than migrating unbacked-up.
    con = sqlite3.connect("file:{0}?mode=ro".format(
        os.path.abspath(path).replace("?", "%3f")), uri=True)
    try:
        try:
            row = con.execute("SELECT value FROM store_meta WHERE key = "
                              "'schema_version'").fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return None
            raise
        try:
            return int(row[0]) if row else None
        except (TypeError, ValueError):
            return None
    finally:
        con.close()


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
    if str(exc) == "not authorized":
        # `_guard` said no. Say what was refused and why, keeping SQLite's
        # own words so the cause is searchable.
        text += (" (the LEM store refuses statements that would drop, alter "
                 "or unguard the append-only record, or switch off its "
                 "durability, and approvals written outside the approval "
                 "flow (D7); nothing was changed)")
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
        #: Guard objects the last open had to rebuild or remove (`health()`).
        self._repaired: List[str] = []
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
        # §5.1: a migration runs only after an automatic pre-migration
        # backup. A file stamped with an OLDER schema than this code is about
        # to be reshaped, and if the reshape is wrong the copy is the only way
        # back — so a backup that cannot be taken stops the open (raises)
        # rather than migrating with nothing behind it. Never pruned.
        found = _stored_schema_version(self.path)
        if found is not None and found < SCHEMA_VERSION:
            import custody
            custody.pre_migration_backup(self.path, found)
        # Unguarded only while `_migrate` reshapes the record; guarded for
        # every statement after it.
        self._writer = self._connect(writer=True, guarded=False)
        self._migrate()
        self._writer.set_authorizer(_guard)

    # ── connections ───────────────────────────────────────────────────
    def _connect(self, writer: bool = False,
                 guarded: bool = True) -> sqlite3.Connection:
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
        # A REPLACE's conflict deletion fires BEFORE DELETE triggers only
        # with this on: the second wall behind `*_no_overwrite`.
        con.execute("PRAGMA recursive_triggers=ON")
        con.execute("PRAGMA busy_timeout={0}".format(self.BUSY_TIMEOUT_MS))
        if not writer:
            # A reader reads. `read_sql("DROP TABLE ...")` is refused here,
            # not performed on the road everybody assumes cannot write.
            con.execute("PRAGMA query_only=ON")
        if guarded:
            con.set_authorizer(_guard)
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
                led = [r[1] for r in con.execute(
                    "PRAGMA table_info('request_ledger')").fetchall()]
                for col in ("who", "fingerprint"):
                    if led and col not in led:
                        con.execute("ALTER TABLE request_ledger ADD COLUMN "
                                    "{0} TEXT".format(col))
                # `signature`: the dedupe flow's HMAC over the decision it
                # recorded (dedupe.approve); a store from before it gains the
                # column, and its older approvals read as unsigned.
                apr = [r[1] for r in con.execute(
                    "PRAGMA table_info('annotation_approval')").fetchall()]
                if apr and "signature" not in apr:
                    con.execute("ALTER TABLE annotation_approval "
                                "ADD COLUMN signature TEXT")
                for table, added in (
                        ("bench_cursor", ("stats", "digest_mismatch")),
                        ("bench_token", ("enroll_key_sha256",)),
                        ("bench_source", ("snapshot_sha", "announced_sha"))):
                    have = [r[1] for r in con.execute(
                        "PRAGMA table_info('{0}')".format(table)).fetchall()]
                    for col in added:
                        if have and col not in have:
                            con.execute("ALTER TABLE {0} ADD COLUMN {1} "
                                        "TEXT".format(table, col))
                cfg = [r[1] for r in con.execute(
                    "PRAGMA table_info('lem_machine_config')").fetchall()]
                if cfg and "retired_at" not in cfg:
                    con.execute("ALTER TABLE lem_machine_config "
                                "ADD COLUMN retired_at TEXT")
                # Before the declarations, because they are all IF NOT EXISTS:
                # a look-alike under a guard's name would otherwise be kept.
                # Anything that differs from what this module declares is
                # dropped and declared again; a trigger LEM never writes is
                # dropped. Both are named in `health()["guards_repaired"]`.
                audit = _schema_audit(con)
                _guard_ddl()       # every guard object has a declaration
                kinds = {n: k for k, n in con.execute(
                    "SELECT type, name FROM main.sqlite_master "
                    "WHERE type IN ('trigger', 'view')")}
                repaired = []
                for name in audit["guards_missing"]:
                    if name in kinds:
                        con.execute('DROP {0} "{1}"'.format(
                            kinds[name].upper(), name.replace('"', '""')))
                        repaired.append(name)
                for name in audit["foreign_triggers"]:
                    con.execute('DROP TRIGGER "{0}"'.format(
                        name.replace('"', '""')))
                    repaired.append(name)
                for stmt in _DDL:
                    con.execute(stmt)
                left = _schema_audit(con)
                if left["guards_missing"] or left["foreign_triggers"]:
                    raise RuntimeError(
                        "the LEM store could not restore its guards: "
                        "{0}".format(left))
                self._repaired = repaired
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
            try:
                self._writer.execute("COMMIT")
            except BaseException:
                # A COMMIT that fails (disk full, I/O error) can leave the
                # transaction OPEN, and the next BEGIN on this writer would
                # then fail "within a transaction" for every save until a
                # restart. Rolled back here, so the failure is this save's.
                try:
                    self._writer.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    # ── D7: approvals ─────────────────────────────────────────────────
    def approval_key(self) -> bytes:
        """The key approvals are signed with: a file BESIDE the store, never
        in it (`<store>.approval-key`, created 0600 on first use), so a
        statement that can write the store cannot sign. A store with no file
        on disk keeps one per process."""
        path = self.path
        if path and os.path.isfile(path):
            kpath = path + ".approval-key"
            try:
                with open(kpath, "rb") as fh:
                    key = fh.read().strip()
                if key:
                    return key
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise ApprovalRefused("the approval key {0} cannot be read: "
                                      "{1}".format(kpath, exc))
            try:
                fd = os.open(kpath, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                             0o600)
            except FileExistsError:
                return self.approval_key()     # another writer made it first
            except OSError as exc:
                raise ApprovalRefused("the approval key {0} cannot be "
                                      "created: {1}".format(kpath, exc))
            key = secrets.token_hex(32).encode("ascii")
            with os.fdopen(fd, "wb") as fh:
                fh.write(key)
            return key
        key = getattr(self, "_approval_key_mem", None)
        if not key:
            key = secrets.token_hex(32).encode("ascii")
            self._approval_key_mem = key
        return key

    def sign_approval(self, row: Dict[str, Any]) -> str:
        """HMAC-SHA256 over every field the decision rests on."""
        msg = json.dumps([None if row.get(f) is None else str(row.get(f))
                          for f in APPROVAL_SIGNED_FIELDS],
                         separators=(",", ":"))
        return hmac.new(self.approval_key(), msg.encode("utf-8"),
                        hashlib.sha256).hexdigest()

    def approval_signature_ok(self, row: Dict[str, Any]) -> bool:
        sig = str(row.get("signature") or "")
        return bool(sig) and hmac.compare_digest(sig, self.sign_approval(row))

    def record_approval(self, *, machine_uid: str, rule: str, run_id: str,
                        candidates: int, approved_by: str, approved_at: str,
                        decision: str, signature: str,
                        members: Sequence[int] = (),
                        examples: Optional[str] = None,
                        qc_impact: Optional[str] = None) -> int:
        """Write one approval and the rows it covers, in one transaction.

        The ONLY way an approval reaches the store: the authorizer refuses
        a plain INSERT into either table. Refused unless the signature
        verifies with the key beside the store, and, for an approval, unless
        it names exactly `candidates` distinct rows. The triggers then hold
        the bare file to the same shape (signed, bounded, on its bench)."""
        row = {"machine_uid": machine_uid, "rule": rule, "run_id": run_id,
               "candidates": candidates, "approved_by": approved_by,
               "approved_at": approved_at, "decision": decision,
               "signature": signature}
        if not self.approval_signature_ok(row):
            raise ApprovalRefused(
                "that approval's signature does not verify: it was not "
                "signed by the approval flow that showed the report (D7)")
        ids = [int(i) for i in members]
        if decision == "approved" and (len(ids) != int(candidates)
                                       or len(set(ids)) != len(ids)):
            raise ApprovalRefused(
                "an approval names exactly the {0} rows it covers, once each "
                "(it named {1}, {2} distinct)".format(
                    candidates, len(ids), len(set(ids))))
        if decision != "approved" and ids:
            raise ApprovalRefused("a rejection covers no rows")
        with self.transaction():
            _APPROVAL_WRITER.on = True
            try:
                con = self._writer
                cur = con.execute(
                    "INSERT INTO annotation_approval (machine_uid, rule, "
                    "run_id, candidates, examples, qc_impact, approved_by, "
                    "approved_at, decision, signature) VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [machine_uid, rule, run_id, int(candidates), examples,
                     qc_impact, approved_by, approved_at, decision,
                     signature])
                aid = int(cur.lastrowid)
                con.executemany(
                    "INSERT INTO annotation_approval_member (approval_id, "
                    "seq, log_id) VALUES (?, ?, ?)",
                    [(aid, k, rid) for k, rid in enumerate(ids)])
            finally:
                _APPROVAL_WRITER.on = False
        return aid

    # ── what it is ────────────────────────────────────────────────────
    def health(self) -> dict:
        size = 0
        for suffix in ("", "-wal"):
            try:
                size += os.path.getsize(self.path + suffix)
            except OSError:
                pass
        out = {"path": self.path, "read_only": self.read_only, "bytes": size,
               "schema_version": None, "guards_missing": None,
               "foreign_triggers": None,
               "guards_repaired": list(self._repaired)}
        # On the engine, like `is_running`: `/healthz` must never count as a
        # read in anybody's tally.
        try:
            with self._reader() as con:
                row = con.execute("SELECT value FROM store_meta "
                                  "WHERE key = 'schema_version'").fetchone()
                # `None` for either list means it could not be read, which
                # is not "all there".
                audit = _schema_audit(con)
            out["schema_version"] = int(row[0]) if row else None
            out.update(audit)
        except (sqlite3.Error, TypeError, ValueError) as exc:
            out["error"] = "{0}: {1}".format(type(exc).__name__, exc)
        return out

    def _pragmas(self, con) -> Dict[str, Any]:
        out = {}
        for name in ("journal_mode", "synchronous", "foreign_keys",
                     "busy_timeout", "recursive_triggers", "query_only"):
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
