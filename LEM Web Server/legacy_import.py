#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
legacy_import.py — moving LEM's record out of LabCore (transfer spec §10.1).

Server v4 keeps LEM's 33 `lem_*` tables in its own store. On its first boot
they are still in LabCore, which is reached through a queue that serialises
the whole lab at ~1.5 ops/s and kills any read past 8 s. This module carries
them across, and every design choice is about not paying that queue for it
and not believing a copy that has not been proven:

1. **The log is staged from a COPY of the log mirror.** The v3.9 server
   already keeps the whole `lem_machine_log` in `data/log-mirror.sqlite3`
   (`log_mirror.LogMirror`, rowid-cursored). The file is copied first and the
   copy read: the live file may be held by a running v3.9 pull, and a reader
   left on it leaves a lock and a -wal beside it. 258k rows cost LabCore 0.
   Without a mirror the log comes over in chunks of `CHUNK` rows by rowid.
   Either way the rows go into `legacy_index` — the import's STAGED copy —
   and not into the record: `lem_machine_log` is append-only, so a row put in
   from a copy that later proves wrong (a stale mirror, a chunk read before a
   VACUUM renumbered the rest) could never be taken out again.

2. **The staged copy is proven in two reads.** ONE read over LabCore's
   covering index `idx_lem_log_uid_kind_ts` gives, per (rowid range, machine,
   kind) for `rowid <= cut`, the row count AND two sums over the indexed
   `ts` — the spec's "GROUP BY machine_uid, kind" refined by range in the
   same single index-only read, so a disagreement also says WHERE, and a
   range holding OTHER rows (renumbered) disagrees even when it holds as many
   of each kind. ONE read of 1,000 sampled rows compares them field by
   field. A range that disagrees is re-read by rowid at one read per 2 s,
   honouring `busy`/`retry_after` — adjacent ranges share a read up to the
   walk's own `CHUNK` rows, so a mirror a VACUUM moved costs what walking the
   log would — and the ranges are counted again.

3. **Then the record, then the proof of the record.** The proven copy is
   settled into `lem_machine_log` in one transaction, and "verified" is said
   only after every `legacy_labcore` row the store holds has been re-hashed
   from its stored columns: its content must match its key, and the content
   multiset must equal the proven copy's — nothing lost, nothing extra. The
   only extra rows allowed are ones the record already held, verified,
   before this numbering began (LabCore deleted them since; kept, and said).

4. **The other tables**: one `SELECT *` each (the largest in production is
   `lem_checklist_state`, 4,774 rows), copied in one store transaction that
   also reads the copy back: row count and an ordered SHA-256 must match
   LabCore's or the transaction rolls back and the table stays undone.

5. **A failed read is never an empty result.** A chunk or table read that is
   refused is never marked done (`import_run.verified` stays NULL); the run
   says why and keeps going with what does not depend on it; the next run
   resumes from `import_run` and the persisted cursors. Until every table is
   verified, `store_meta.sync_hold` makes `/api/v2/bench/*/sync` answer 503 +
   Retry-After (`bench_api._hold`), so no bench adds to a record still moving.

   **Identity is content.** `legacy_key = 'lc:' + H(uid, ts, kind, lab_id,
   test_name, value, detail) + ':' + k` — the k-th copy of that exact row, k
   counted over the WHOLE staged generation in rowid order (never a partial
   count). LabCore's rowid is only a cursor (`legacy_rowid`): a VACUUM after
   deletes renumbers it. A re-walk of a renumbered log is a new generation;
   it finds every row the record holds by key and adds 0, and v3.9's N3
   double write (two byte-identical rows) stays two rows however often it is
   imported.

6. **`jk` links projected rows to bench records.** A v4 bench in legacy
   projection mode (§10.3) writes its rows to LabCore with `detail.jk =
   epoch:seq`. Such a row becomes the bench record's custody row
   (`bench_epoch`, `bench_seq`), so when the bench later syncs v2 from seq 1
   the server's `(uid, epoch, seq)` key finds it and nothing is doubled (M6) —
   and if the v2 sync got there first, the pulled copy is recognised and not
   added.

Cost, counted into `store_meta.import_reads` and shown as
`/healthz.store.import.reads`: 2 log reads + 1 schema read + 1 per table in
the happy path, plus 1 per repaired run of ranges (at most cut / CHUNK) and
1 per re-count. Production-sized (258k rows, 32 tables): 37 with one bad
range, 49 when a VACUUM moved the whole mirror.

Running this against production LabCore is a deploy step that needs Ryan
(transfer §10.1 `[RYAN]`). The command line refuses without
`--yes-this-is-production`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from labcore_result import is_missing_table, refusal_of, retry_after

logger = logging.getLogger(__name__)

#: Rows per LabCore read when the log is walked by rowid (no mirror; the
#: bridge's pull uses the same number, §10.4).
CHUNK = 20000
#: A repair range, rows per read (§10.1).
BUCKET = 5000
#: Rows compared field by field in the sample read (§10.1).
SAMPLE = 1000
#: One repair read per this many seconds (§10.1: "at one read per 2 s").
REPAIR_PACE_S = 2.0
#: How often a busy answer is waited out before the read counts as failed.
BUSY_RETRIES = 5
#: Count → repair → count rounds before the log is called unverifiable.
VERIFY_ROUNDS = 3
#: Mirror rows applied per store transaction (a resume point each).
SEED_BATCH = 5000
#: Placeholders per IN (...) on the store.
_IN = 400

LOG_COLS = ("machine_uid", "ts", "kind", "lab_id", "test_name", "value",
            "detail")
_LOG_SELECT = ("rowid AS rid, machine_uid, ts, kind, lab_id, test_name, "
               "value, detail")

#: The marker on `store_meta.sync_hold` this module sets — and the only hold
#: it ever clears.
HOLD_PREFIX = "importing LEM's record from LabCore"
HOLD_TEXT = (HOLD_PREFIX + ": not verified yet (transfer §10.1). Nothing is "
             "lost: hold and retry.")

#: Side tables. Not the record. `legacy_index` is the import's staged copy
#: of LabCore's log for one numbering of LabCore's rowids ("generation"):
#: each LabCore rowid's content, its hash, and — once settled — the content
#: key it is and the record row that holds it. It can always be rebuilt by
#: walking LabCore again. `legacy_fp` is the replay fingerprint of each
#: legacy run/qc row (the bridge's live `replay_candidate`).
LEGACY_INDEX_DDL = (
    "CREATE TABLE IF NOT EXISTS legacy_index (gen INTEGER NOT NULL,"
    " src_rowid INTEGER NOT NULL, h TEXT NOT NULL, legacy_key TEXT,"
    " log_id INTEGER,"
    # The seven columns with v3.9's own affinities, so a staged value is
    # stored — and later copied into the record — exactly as LabCore has it.
    " machine_uid TEXT, ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT,"
    " value TEXT, detail TEXT,"
    " PRIMARY KEY (gen, src_rowid))")
SIDE_DDL = (
    LEGACY_INDEX_DDL,
    "CREATE INDEX IF NOT EXISTS ix_legacy_index_h ON legacy_index(gen, h)",
    "CREATE INDEX IF NOT EXISTS ix_legacy_index_log ON legacy_index(gen, log_id)",
    "CREATE TABLE IF NOT EXISTS legacy_fp (log_id INTEGER PRIMARY KEY,"
    " machine_uid TEXT, ts TEXT, fp TEXT, twin_of INTEGER)",
    "CREATE INDEX IF NOT EXISTS ix_legacy_fp ON legacy_fp(machine_uid, fp)",
    "CREATE INDEX IF NOT EXISTS ix_legacy_fp_poll ON legacy_fp(machine_uid, ts)",
    "CREATE TABLE IF NOT EXISTS projection_state (table_name TEXT NOT NULL,"
    " pk TEXT NOT NULL, sha TEXT NOT NULL, PRIMARY KEY (table_name, pk))",
    "CREATE TABLE IF NOT EXISTS cursor_mirror (machine_uid TEXT PRIMARY KEY,"
    " src TEXT, last_position INTEGER, queued_at TEXT, outbox_id INTEGER)",
)


class ImportFailed(RuntimeError):
    """A step could not be completed; nothing it did not finish is marked done."""


# ── the content key (a recipe: §13 says a change needs a new prefix) ───────

def _canon(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")


def content_hash(machine_uid, ts, kind, lab_id, test_name, value,
                 detail) -> str:
    """H(uid, ts, kind, lab_id, test_name, value, detail): sha256 of the
    canonical JSON array of the seven values as LabCore holds them (NULL is
    null, never ""), first 32 hex digits."""
    return hashlib.sha256(_canon([machine_uid, ts, kind, lab_id, test_name,
                                  value, detail])).hexdigest()[:32]


def legacy_key(h: str, occurrence: int) -> str:
    return "lc:%s:%d" % (h, int(occurrence))


def row_hash(row: dict) -> str:
    return content_hash(*(row.get(c) for c in LOG_COLS))


#: Detail keys a v3.9 replay restamps (§10.5: poll time, operator,
#: calibration) or that only name the record (jk), dropped from the replay
#: fingerprint.
_FP_DROP = frozenset(("jk", "ts", "at", "polled_at", "poll_ts", "operator",
                      "calibration", "calibration_id"))


def replay_fingerprint(row: dict) -> str:
    detail = row.get("detail")
    try:
        parsed = json.loads(detail) if isinstance(detail, str) and detail \
            else detail
    except ValueError:
        parsed = detail
    if isinstance(parsed, dict):
        parsed = {k: v for k, v in parsed.items() if k not in _FP_DROP}
    return hashlib.sha256(_canon([row.get("machine_uid"), row.get("kind"),
                                  row.get("lab_id"), row.get("test_name"),
                                  row.get("value"), parsed])).hexdigest()[:32]


def jk_of(detail) -> Optional[Tuple[str, int]]:
    """(epoch, seq) from a row's `detail.jk`, or None."""
    if not isinstance(detail, str) or '"jk"' not in detail:
        return None
    try:
        jk = json.loads(detail).get("jk")
    except (ValueError, AttributeError):
        return None
    if not isinstance(jk, str) or ":" not in jk:
        return None
    epoch, _, seq = jk.rpartition(":")
    try:
        return epoch, int(seq)
    except ValueError:
        return None


# ── the store side ──────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ok(res, what: str) -> dict:
    if not isinstance(res, dict) or res.get("error"):
        raise ImportFailed("the LEM store could not %s: %s" % (
            what, (res or {}).get("error") if isinstance(res, dict) else res))
    return res


def _q(store, sql: str, args=None, what: str = "read") -> List[dict]:
    res = _ok(store.read_sql(sql, args or []), what)
    return list(res.get("rows") or [])


def _x(store, sql: str, args=None, what: str = "write") -> dict:
    return _ok(store.sql(sql, args or []), what)


def ensure_tables(store) -> None:
    have = {r["name"] for r in _q(store, 'PRAGMA table_info("legacy_index")')}
    if have and "detail" not in have:
        # Round 1's shape (no staged content). It is a rebuildable map, never
        # the record: dropped, the next pull re-walks LabCore in a new
        # numbering and finds every row it holds by content.
        _x(store, "DROP TABLE legacy_index")
    for ddl in SIDE_DDL:
        _x(store, ddl, what="declare the import's side tables")


def get_meta(store, key: str) -> Optional[str]:
    rows = _q(store, "SELECT value FROM store_meta WHERE key = ?", [key])
    return rows[0]["value"] if rows else None


def set_meta(store, key: str, value) -> None:
    if value is None:
        _x(store, "DELETE FROM store_meta WHERE key = ?", [key])
        return
    _x(store, "INSERT INTO store_meta (key, value) VALUES (?, ?) ON "
              "CONFLICT(key) DO UPDATE SET value = excluded.value",
       [key, str(value)])


def _chunks(seq: List, n: int = _IN) -> Iterable[List]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def hold_until_verified(store) -> bool:
    """Hold v2 syncs (503 + Retry-After) unless the import is verified.
    True when a hold is (now) in place. A store that cannot be read is held:
    a store that cannot answer this cannot take records either."""
    try:
        if get_meta(store, "import_state") == "verified":
            return False
        if not (get_meta(store, "sync_hold") or "").strip():
            set_meta(store, "sync_hold", HOLD_TEXT)
        return True
    except ImportFailed as exc:
        logger.warning("import: could not read or set the sync hold: %s", exc)
        return True


def _release_hold(store) -> None:
    held = get_meta(store, "sync_hold") or ""
    if held.startswith(HOLD_PREFIX):
        set_meta(store, "sync_hold", None)


def status(store) -> dict:
    """What `/healthz.store.import` says. Local reads only, never LabCore."""
    try:
        meta = {r["key"]: r["value"] for r in _q(
            store, "SELECT key, value FROM store_meta WHERE key LIKE "
                   "'import\\_%' ESCAPE '\\'")}
        verified = _q(store, "SELECT COUNT(*) AS n FROM import_run WHERE "
                             "verified IS NOT NULL")[0]["n"]
    except ImportFailed as exc:
        return {"state": "unknown", "error": str(exc), "reads": None,
                "tables_verified": None}
    try:
        problems = json.loads(meta.get("import_problems") or "[]")
    except ValueError:
        problems = [meta.get("import_problems")]
    return {"state": meta.get("import_state") or "not started",
            "reads": int(meta.get("import_reads") or 0),
            "tables_verified": int(verified or 0),
            "tables_total": (int(meta["import_tables_total"])
                             if meta.get("import_tables_total") else None),
            "cut": (int(meta["import_cut"]) if meta.get("import_cut")
                    not in (None, "") else None),
            "started_at": meta.get("import_started_at"),
            "finished_at": meta.get("import_finished_at"),
            "problems": problems}


def refresh_status(store) -> dict:
    """Read `status` and keep it on the store object, where `/healthz` reads
    it from memory (a health check makes no call, to the store or LabCore)."""
    st = status(store)
    try:
        store._lem_import_status = st
    except AttributeError:                              # pragma: no cover
        pass
    return st


def cached_status(store) -> dict:
    """The last status read, from memory. Never invented: before anything
    has read it, it says so."""
    st = getattr(store, "_lem_import_status", None)
    if st is None:
        return {"state": "unknown", "reads": None, "tables_verified": None,
                "error": "not read yet"}
    return dict(st)


# ── applying LabCore rows to the store ──────────────────────────────────────

#: Rows per multi-row INSERT into the staging index (8 values each).
_STAGE_ROWS = 100


class LegacyWriter:
    """Stages rows read from LabCore's `lem_machine_log` into `legacy_index`
    for one numbering generation, and settles staged contents into the
    record. Used by the import (seed, chunk, repair: `stage`, then one
    `settle` once the whole copy is proven) and by the bridge's pull
    (`apply`: a pull's rows are LabCore's own answer, read in one go and
    anchored at the cursor, so they stage and settle together). Call inside
    `store.transaction()`.

    The rule, per distinct content h: the generation's staged entries for h,
    in rowid order, are keyed `h:0 … h:n-1`, and the record must hold a row
    under each of those keys. Missing ones are added, and only those. That is
    computed from the WHOLE generation's entries for h at settle time —
    never from a partial count plus entries outside a range — so nothing is
    keyed off a stale numbering, and a row only ever enters the (append-only)
    record from a staged copy that has been proven or read just now."""

    def __init__(self, store, gen: int, annotate: bool = False,
                 by: str = "lem-import") -> None:
        self.store = store
        self.gen = int(gen)
        self.annotate = annotate
        self.by = by
        self.added = 0
        self.linked = 0
        self.candidates = 0

    # ── staging ──
    def stage(self, rows: List[dict],
              replace: Optional[Tuple[int, int]] = None) -> None:
        """Write `rows` (dicts with `rid` and the seven columns) into this
        generation's index — content and hash, no key yet. `replace=(lo,
        hi)`: every entry in [lo, hi] is replaced by `rows` (a repaired
        range; rows may be empty). The record is not touched."""
        store, gen = self.store, self.gen
        if replace is not None:
            _x(store, "DELETE FROM legacy_index WHERE gen = ? AND src_rowid "
                      ">= ? AND src_rowid <= ?", [gen, replace[0], replace[1]])
        if not rows:
            return
        rids = [int(r["rid"]) for r in rows]
        # A resumed read can start on a row it already staged.
        _x(store, "DELETE FROM legacy_index WHERE gen = ? AND src_rowid >= ? "
                  "AND src_rowid <= ?", [gen, min(rids), max(rids)])
        cols = ("gen", "src_rowid", "h") + LOG_COLS
        one = "(%s)" % ", ".join("?" * len(cols))
        for part in _chunks(rows, _STAGE_ROWS):
            args: List[Any] = []
            for r in part:
                args += [gen, int(r["rid"]), row_hash(r)] + \
                    [r.get(c) for c in LOG_COLS]
            _x(store, "INSERT OR REPLACE INTO legacy_index (%s) VALUES %s" % (
                ", ".join(cols), ", ".join([one] * len(part))), args,
               what="stage LabCore rows")

    def apply(self, rows: List[dict]) -> Dict[str, int]:
        """The bridge's pull: stage `rows` (ascending `rid`, all after what
        this generation already holds) and settle the contents they carry."""
        if not rows:
            return {"added": 0, "linked": 0}
        rids = [int(r["rid"]) for r in rows]
        self.stage(rows)
        return self.settle((min(rids), max(rids)))

    # ── settling into the record ──
    def settle(self, span: Optional[Tuple[int, int]] = None) -> Dict[str, int]:
        """Key every staged entry of the contents in scope (all of this
        generation, or the contents staged in rowid `span`), add the rows
        the record lacks, and point each entry at its record row."""
        store, gen = self.store, self.gen
        added0, linked0 = self.added, self.linked
        if span is None:
            scope, sargs = "", []
        else:
            scope = (" AND h IN (SELECT h FROM legacy_index WHERE gen = ? AND "
                     "src_rowid >= ? AND src_rowid <= ?)")
            sargs = [gen, int(span[0]), int(span[1])]
        base = _q(store, "SELECT COALESCE(MAX(id), 0) AS m FROM "
                         "lem_machine_log")[0]["m"]  # raw-log: ids, every row
        # 1. The k-th entry of h, in this numbering's rowid order, is h:k.
        _x(store, "UPDATE legacy_index AS x SET legacy_key = r.key, log_id = "
                  "NULL FROM (SELECT src_rowid, 'lc:' || h || ':' || "
                  "(ROW_NUMBER() OVER (PARTITION BY h ORDER BY src_rowid) - 1) "
                  "AS key FROM legacy_index WHERE gen = ?%s) AS r WHERE "
                  "x.gen = ? AND x.src_rowid = r.src_rowid AND x.legacy_key IS "
                  "NOT r.key" % scope, [gen] + sargs + [gen],
           what="key the staged LabCore rows")
        # 2. Rows the record lacks, in LabCore's order. A row carrying `jk`
        #    may be a bench record the store already holds: step 3.
        res = _x(store,
                 "INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
                 "test_name, value, detail, origin, legacy_key, legacy_rowid) "
                 "SELECT x.machine_uid, x.ts, x.kind, x.lab_id, x.test_name, "
                 "x.value, x.detail, 'legacy_labcore', x.legacy_key, "
                 "x.src_rowid FROM legacy_index x WHERE x.gen = ?%s AND "
                 "x.log_id IS NULL AND (x.detail IS NULL OR instr(x.detail, "
                 "'\"jk\"') = 0) AND NOT EXISTS (SELECT 1 FROM "
                 # raw-log: the legacy key is unique over every row
                 "lem_machine_log l WHERE l.legacy_key = x.legacy_key) "
                 "ORDER BY x.src_rowid" % scope.replace(" h IN", " x.h IN"),
                 [gen] + sargs, what="add LabCore rows to the record")
        self.added += int(res.get("rows_affected") or 0)
        # 3. `jk` rows one at a time: linked to the bench record if held.
        for e in _q(store,
                    "SELECT src_rowid AS rid, legacy_key, machine_uid, ts, kind, "
                    "lab_id, test_name, value, detail FROM legacy_index x "
                    "WHERE x.gen = ?%s AND x.log_id IS NULL AND instr(x.detail, "
                    "'\"jk\"') > 0 AND NOT EXISTS (SELECT 1 FROM "
                    # raw-log: the legacy key is unique over every row
                    "lem_machine_log l WHERE l.legacy_key = x.legacy_key) "
                    "ORDER BY src_rowid" % scope.replace(" h IN", " x.h IN"),
                    [gen] + sargs):
            log_id, _inserted = self._insert(e["legacy_key"], e)
            _x(store, "UPDATE legacy_index SET log_id = ? WHERE gen = ? AND "
                      "src_rowid = ?", [log_id, gen, e["rid"]])
        # 4. Every other entry points at the row its key names.
        _x(store, "UPDATE legacy_index SET log_id = (SELECT id FROM "
                  # raw-log: the legacy key is unique over every row
                  "lem_machine_log l WHERE l.legacy_key = legacy_index."
                  "legacy_key) WHERE gen = ?%s AND log_id IS NULL"
                  % scope, [gen] + sargs)
        # 5. The new rows' replay fingerprints (and, live, their labels).
        new_ids = [(r["id"], r) for r in _q(
            store, "SELECT id, machine_uid, ts, kind, lab_id, test_name, value, "
                   # raw-log: the rows this call just added
                   "detail FROM lem_machine_log WHERE id > ? AND origin = "
                   "'legacy_labcore' AND kind IN ('run', 'qc') ORDER BY id",
            [base])]
        self._fingerprints(new_ids)
        return {"added": self.added - added0, "linked": self.linked - linked0}

    def _insert(self, key: str, r: dict) -> Tuple[Optional[int], bool]:
        """Add one row: (its id, True). When the row is a bench record the
        store already holds through v2 (a `jk` link) it is that record, not a
        new one: (the record's id, False)."""
        store = self.store
        uid = r.get("machine_uid")
        epoch, seq = None, None
        link = jk_of(r.get("detail"))
        if link is not None:
            # Held as a bench record already (a v2 sync got here first): the
            # record is the bench's, and every row of it came with it.
            if _q(store, "SELECT 1 AS x FROM bench_record WHERE machine_uid = "
                         "? AND bench_epoch = ? AND bench_seq = ?",
                  [uid, link[0], link[1]]):
                got = _q(store,
                         # raw-log: custody is unique over every row
                         "SELECT id FROM lem_machine_log WHERE machine_uid = ? "
                         "AND bench_epoch = ? AND bench_seq = ?",
                         [uid, link[0], link[1]])
                self.linked += 1
                return (got[0]["id"] if got else None), False
            custody = _q(store,
                         # raw-log: custody is unique over every row
                         "SELECT id, origin FROM lem_machine_log WHERE "
                         "machine_uid = ? AND bench_epoch = ? AND bench_seq = ?",
                         [uid, link[0], link[1]])
            if custody and custody[0]["origin"] != "legacy_labcore":
                self.linked += 1
                return custody[0]["id"], False
            epoch = link[0]
            seq = None if custody else link[1]
        res = _x(store, "INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
                  "test_name, value, detail, origin, bench_epoch, bench_seq, "
                  "legacy_key, legacy_rowid) SELECT ?, ?, ?, ?, ?, ?, ?, "
                  "'legacy_labcore', ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM "
                  # raw-log: the legacy key is unique over every row
                  "lem_machine_log WHERE legacy_key = ?)",
           [r.get(c) for c in LOG_COLS] + [epoch, seq, key, int(r["rid"]), key],
           what="add a LabCore row to the record")
        got = _q(store, "SELECT id FROM lem_machine_log WHERE "  # raw-log: own row
                        "legacy_key = ?", [key])
        if res.get("rows_affected"):
            self.added += 1
        return (got[0]["id"] if got else None), bool(res.get("rows_affected"))

    def _fingerprints(self, new_ids: List[Tuple[int, dict]]) -> None:
        """Record each new run/qc row's replay fingerprint; with `annotate`,
        label a v3.9 replay burst visibly (§10.4): a poll (uid, ts) in which
        at least `BURST` rows have an earlier identical twin from another
        poll. Visible only — hiding needs a person (§10.5)."""
        if not new_ids:
            return
        store = self.store
        if not self.annotate:
            # The import: no labels, so no twin lookups — one statement per
            # 200 rows rather than one per row.
            for part in _chunks(new_ids, 200):
                args: List[Any] = []
                for log_id, r in part:
                    args += [log_id, r.get("machine_uid"), r.get("ts"),
                             replay_fingerprint(r)]
                _x(store, "INSERT OR REPLACE INTO legacy_fp (log_id, "
                          "machine_uid, ts, fp, twin_of) VALUES %s" % ", ".join(
                              ["(?, ?, ?, ?, NULL)"] * len(part)), args)
            return
        polls = set()
        for log_id, r in new_ids:
            fp = replay_fingerprint(r)
            got = _q(store, "SELECT log_id FROM legacy_fp WHERE "
                            "machine_uid = ? AND fp = ? AND ts <> ? AND "
                            "log_id < ? ORDER BY log_id LIMIT 1",
                     [r.get("machine_uid"), fp, r.get("ts"), log_id])
            twin = got[0]["log_id"] if got else None
            if twin is not None:
                polls.add((r.get("machine_uid"), r.get("ts")))
            _x(store, "INSERT OR REPLACE INTO legacy_fp (log_id, machine_uid, "
                      "ts, fp, twin_of) VALUES (?, ?, ?, ?, ?)",
               [log_id, r.get("machine_uid"), r.get("ts"), fp, twin])
        for uid, ts in polls:
            twinned = _q(store, "SELECT log_id, twin_of FROM legacy_fp WHERE "
                                "machine_uid = ? AND ts = ? AND twin_of IS NOT "
                                "NULL ORDER BY log_id", [uid, ts])
            if len(twinned) < BURST:
                continue
            done = set()
            for part in _chunks([t["log_id"] for t in twinned]):
                done.update(r["log_id"] for r in _q(
                    store, "SELECT log_id FROM log_annotation WHERE label = "
                           "'replay_candidate' AND log_id IN (%s)"
                           % ",".join("?" * len(part)), part))
            for t in twinned:
                if t["log_id"] in done:
                    continue
                _x(store, "INSERT INTO log_annotation (log_id, label, dup_of, "
                          "rule, by, at) VALUES (?, 'replay_candidate', ?, ?, "
                          "?, ?)",
                   [t["log_id"], t["twin_of"],
                    "bridge: poll of >= %d rows with an earlier identical twin"
                    % BURST, self.by, _now()])
                self.candidates += 1


#: §10.4: "burst ≥ 20 rows with an earlier identical twin".
BURST = 20


# ── the import ──────────────────────────────────────────────────────────────

class Importer:
    """One import of LabCore's `lem_*` tables into the store. `run()` is
    resumable: call it again after any failure and it carries on."""

    def __init__(self, store, labcore, *, mirror_path: Optional[str] = None,
                 sleep: Callable[[float], None] = time.sleep,
                 chunk: int = CHUNK, bucket: int = BUCKET,
                 sample: int = SAMPLE, pace: float = REPAIR_PACE_S,
                 rng: Optional[random.Random] = None) -> None:
        self.store = store
        self.labcore = labcore
        self.mirror_path = mirror_path
        self.sleep = sleep
        self.chunk = int(chunk)
        self.bucket = int(bucket)
        self.sample = int(sample)
        self.pace = float(pace)
        self.rng = rng or random.Random()
        self._last_read = 0.0

    # ── LabCore, counted ──
    def _ask(self, sql: str, args=None, paced: bool = False) -> dict:
        """One LabCore read: counted into `import_reads` BEFORE it is sent (a
        read that dies still cost the queue), busy waited out as asked. A
        refusal raises; it is never rows."""
        for attempt in range(BUSY_RETRIES + 1):
            if paced and self.pace > 0 and self._last_read:
                wait = self.pace - (time.monotonic() - self._last_read)
                if wait > 0:
                    self.sleep(wait)
            reads = int(get_meta(self.store, "import_reads") or 0) + 1
            set_meta(self.store, "import_reads", reads)
            self._last_read = time.monotonic()
            try:
                res = self.labcore.read_sql(sql, args or [])
            except Exception as exc:                    # noqa: BLE001
                res = {"error": "%s: %s" % (type(exc).__name__, exc)}
            why = refusal_of(res)
            if why is None:
                return res
            busy = isinstance(res, dict) and res.get("busy")
            if busy and attempt < BUSY_RETRIES:
                self.sleep(retry_after(res, 5.0) or 5.0)
                continue
            raise ImportFailed("LabCore refused: %s" % why)
        raise ImportFailed("LabCore stayed busy")         # pragma: no cover

    # ── the run ──
    def run(self, reimport: bool = False) -> dict:
        store = self.store
        ensure_tables(store)
        if reimport:
            self.mirror_path = None       # a re-walk asks LabCore, not a copy
            self._new_generation()
        elif get_meta(store, "import_state") != "verified":
            hold_until_verified(store)
        if not get_meta(store, "import_started_at"):
            set_meta(store, "import_started_at", _now())
        set_meta(store, "import_state", "running")
        problems: List[str] = []
        try:
            self._log()
        except ImportFailed as exc:
            problems.append("lem_machine_log: %s" % exc)
        if not reimport:
            problems += self._tables()
        done = not problems and self._all_verified()
        if done:
            set_meta(store, "import_state", "verified")
            set_meta(store, "import_finished_at", _now())
            set_meta(store, "import_problems", None)
            _release_hold(store)
        else:
            set_meta(store, "import_state", "incomplete")
            set_meta(store, "import_problems", json.dumps(problems[:50]))
        out = refresh_status(store)
        out = dict(out, problems=problems)
        logger.warning("import: %s after %s LabCore reads%s", out["state"],
                       out["reads"], ("; " + "; ".join(problems))[:500]
                       if problems else "")
        return out

    def _all_verified(self) -> bool:
        names = json.loads(get_meta(self.store, "import_tables") or "{}")
        rows = _q(self.store, "SELECT table_name FROM import_run WHERE "
                              "verified IS NOT NULL")
        have = {r["table_name"] for r in rows}
        return "lem_machine_log" in have and set(names) <= have

    def _new_generation(self) -> None:
        store = self.store
        gen = int(get_meta(store, "legacy_gen") or 0) + 1
        set_meta(store, "legacy_gen", gen)
        for key in ("import_cut", "import_log_cursor", "import_seed_cursor",
                    "import_seeded", "import_index_proven",
                    "import_source_rows", "legacy_cursor"):
            set_meta(store, key, None)
        # Rows the record held before this numbering began: the only ones
        # the proof may find LabCore no longer holds (it deleted them after
        # they were verified; the record is append-only and keeps them).
        set_meta(store, "legacy_gen_base_id", _q(
            store, "SELECT COALESCE(MAX(id), 0) AS m FROM "
                   "lem_machine_log")[0]["m"])     # raw-log: ids, every row
        _x(store, "DELETE FROM import_run WHERE table_name = 'lem_machine_log'")
        # The old numbering describes rowids LabCore no longer has.
        _x(store, "DELETE FROM legacy_index WHERE gen < ?", [gen])

    # ── the log ──
    def _log(self) -> None:
        """Stage → prove the staged copy against LabCore → settle it into the
        record → prove the record. Each step resumes where it stopped; the
        record is touched only by the third, and only with a proven copy."""
        store = self.store
        row = _q(store, "SELECT verified FROM import_run WHERE table_name = "
                        "'lem_machine_log'")
        if row and row[0]["verified"]:
            return
        gen = int(get_meta(store, "legacy_gen") or 0)
        if get_meta(store, "import_seeded") != "1":
            if self.mirror_path and get_meta(store, "import_log_cursor") is None:
                self._seed_from_mirror(gen)
            else:
                self._walk_labcore(gen)
            set_meta(store, "import_seeded", "1")
        if get_meta(store, "import_index_proven") != str(gen):
            self._verify(gen)
        writer = LegacyWriter(store, gen)
        with store.transaction():
            writer.settle()
        self._prove(gen)

    def _seed_from_mirror(self, gen: int) -> None:
        store = self.store
        if not os.path.isfile(self.mirror_path):
            raise ImportFailed("the log mirror %s is not there"
                               % self.mirror_path)
        scratch = tempfile.mkdtemp(prefix="lem-import-")
        copy = os.path.join(scratch, "log-mirror-copy.sqlite3")
        try:
            shutil.copyfile(self.mirror_path, copy)
            for suffix in ("-wal",):
                if os.path.exists(self.mirror_path + suffix):
                    shutil.copyfile(self.mirror_path + suffix, copy + suffix)
            con = sqlite3.connect("file:%s?mode=ro" % copy, uri=True)
            con.row_factory = sqlite3.Row
            try:
                cut = get_meta(store, "import_cut")
                if cut is None:
                    got = con.execute("SELECT MAX(rowid_src) FROM log").fetchone()
                    cut = int(got[0] or 0)
                    set_meta(store, "import_cut", cut)
                cut = int(cut)
                since = int(get_meta(store, "import_seed_cursor") or 0)
                writer = LegacyWriter(store, gen)
                while True:
                    batch = [dict(r) for r in con.execute(
                        "SELECT rowid_src AS rid, machine_uid, ts, kind, lab_id, "
                        "test_name, value, detail FROM log WHERE rowid_src > ? "
                        "AND rowid_src <= ? ORDER BY rowid_src LIMIT ?",
                        (since, cut, SEED_BATCH))]
                    if not batch:
                        break
                    with store.transaction():
                        writer.stage(batch)
                        since = int(batch[-1]["rid"])
                        set_meta(store, "import_seed_cursor", since)
            finally:
                con.close()
        except sqlite3.Error as exc:
            raise ImportFailed("the log mirror copy could not be read (%s): "
                               "a mirror that cannot be read is not an empty "
                               "one" % exc)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def _walk_labcore(self, gen: int) -> None:
        store = self.store
        cut = get_meta(store, "import_cut")
        if cut is None:
            try:
                # raw-log: LabCore's own table (no effective view there)
                res = self._ask("SELECT MAX(rowid) AS m FROM lem_machine_log")
                cut = int(((res.get("rows") or [{}])[0].get("m")) or 0)
            except ImportFailed as exc:
                if not is_missing_table(str(exc)):
                    raise
                cut = 0                     # LabCore never had a log
            set_meta(store, "import_cut", cut)
        cut = int(cut)
        since = int(get_meta(store, "import_log_cursor") or 0)
        writer = LegacyWriter(store, gen)
        while since < cut:
            # raw-log: LabCore's own table, every row is the import's to copy
            res = self._ask("SELECT %s FROM lem_machine_log WHERE rowid > ? AND "
                            "rowid <= ? ORDER BY rowid LIMIT ?" % _LOG_SELECT,
                            [since, cut, self.chunk], paced=False)
            rows = list(res.get("rows") or [])
            if not rows:
                break
            with store.transaction():
                writer.stage(rows)
                since = int(rows[-1]["rid"])
                set_meta(store, "import_log_cursor", since)

    #: Per (rowid range, machine, kind): how many rows, the sum of their
    #: timestamps in whole seconds, and the sum of the timestamps' lengths.
    #: All three come off LabCore's covering index (machine_uid, kind, ts),
    #: so the read stays the index-only scan §10.1 prices. The two sums make
    #: the proof see WHICH rows sit in a range, not only how many: after a
    #: VACUUM renumbers, a range holds other rows — usually as many of each
    #: machine and kind, never with the same timestamps.
    _PROOF_COLS = ("COUNT(*) AS n, SUM(CAST(strftime('%s', ts) AS INTEGER)) "
                   "AS t, SUM(LENGTH(ts)) AS l")

    def _verify(self, gen: int) -> None:
        """Prove the staged copy against LabCore: ONE GROUP BY read and ONE
        sample read; repair what disagrees; count again. Marks the copy
        proven (`import_index_proven`) — the record is not touched here."""
        store = self.store
        cut = int(get_meta(store, "import_cut") or 0)
        B = self.bucket
        sampled = False
        bad: set = set()
        for _round in range(VERIFY_ROUNDS):
            res = self._ask(
                # raw-log: LabCore's own table, counted to prove the copy
                "SELECT rowid / ? AS b, machine_uid, kind, %s FROM "
                "lem_machine_log INDEXED BY idx_lem_log_uid_kind_ts WHERE "
                "rowid <= ? GROUP BY b, machine_uid, kind" % self._PROOF_COLS,
                [B, cut])
            theirs = {(r["b"], r["machine_uid"], r["kind"]):
                      (r["n"], r["t"], r["l"]) for r in res.get("rows") or []}
            ours = {(r["b"], r["machine_uid"], r["kind"]):
                    (r["n"], r["t"], r["l"]) for r in _q(
                store, "SELECT src_rowid / ? AS b, machine_uid, kind, %s FROM "
                       "legacy_index WHERE gen = ? AND src_rowid <= ? GROUP BY "
                       "b, machine_uid, kind" % self._PROOF_COLS, [B, gen, cut])}
            bad = {k[0] for k in set(theirs) | set(ours)
                   if theirs.get(k) != ours.get(k)}
            if not sampled:
                bad |= self._sample(gen, cut)
                sampled = True
            n = sum(v[0] for v in theirs.values())
            if not bad:
                with store.transaction():
                    _x(store, "INSERT INTO import_run (table_name, source_rows, "
                              "max_rowid, copied) VALUES ('lem_machine_log', ?, "
                              "?, NULL) ON CONFLICT(table_name) DO UPDATE SET "
                              "source_rows = excluded.source_rows, max_rowid = "
                              "excluded.max_rowid, verified = NULL",
                       [n, cut])
                    set_meta(store, "import_source_rows", n)
                    set_meta(store, "import_index_proven", gen)
                return
            self._record_partial(cut, n, sum(v[0] for v in ours.values()))
            writer = LegacyWriter(store, gen)
            for lo, hi in self._ranges(sorted(bad), cut):
                # raw-log: LabCore's own table, a range re-read to repair it
                got = self._ask("SELECT %s FROM lem_machine_log WHERE rowid >= ? "
                                "AND rowid <= ? ORDER BY rowid" % _LOG_SELECT,
                                [lo, hi], paced=True)
                with store.transaction():
                    writer.stage(list(got.get("rows") or []), replace=(lo, hi))
        raise ImportFailed("rowid ranges %s still disagree with LabCore after "
                           "%d rounds" % (sorted(bad)[:20], VERIFY_ROUNDS))

    def _ranges(self, buckets: List[int], cut: int) -> List[Tuple[int, int]]:
        """Bad buckets as rowid ranges to re-read. Adjacent buckets share a
        read up to `chunk` rowids — the walk's and the pull's own read size,
        so no read is heavier than one the queue already takes. A mirror
        whose numbering a VACUUM moved is bad from the first deleted row to
        the end; re-read in `bucket`-sized pieces that would cost 52 reads
        on production's 258k rows, in `chunk`-sized ones 13."""
        B = self.bucket
        per = max(1, self.chunk // B)
        out: List[Tuple[int, int]] = []
        run: List[int] = []
        for b in buckets + [None]:
            if run and (b is None or b != run[-1] + 1 or len(run) >= per):
                out.append((run[0] * B, min((run[-1] + 1) * B - 1, cut)))
                run = []
            if b is not None:
                run.append(b)
        return out

    def _record_partial(self, cut, theirs_n, ours_n) -> None:
        _x(self.store, "INSERT INTO import_run (table_name, source_rows, "
                       "max_rowid, copied) VALUES ('lem_machine_log', ?, ?, ?) "
                       "ON CONFLICT(table_name) DO UPDATE SET source_rows = "
                       "excluded.source_rows, max_rowid = excluded.max_rowid, "
                       "copied = excluded.copied, verified = NULL",
           [theirs_n, cut, ours_n])

    def _sample(self, gen: int, cut: int) -> set:
        """1,000 staged rows compared field by field with LabCore's at the
        same rowid. The ranges of any that differ (or that LabCore no longer
        has) come back for repair."""
        store = self.store
        held = [r["src_rowid"] for r in _q(
            store, "SELECT src_rowid FROM legacy_index WHERE gen = ? AND "
                   "src_rowid <= ?", [gen, cut])]
        if not held:
            return set()
        pick = sorted(self.rng.sample(held, min(self.sample, len(held))))
        # raw-log: LabCore's own table, sampled to prove the copy
        res = self._ask("SELECT %s FROM lem_machine_log WHERE rowid IN (%s)"
                        % (_LOG_SELECT, ",".join(str(int(p)) for p in pick)))
        theirs = {int(r["rid"]): r for r in res.get("rows") or []}
        ours = {}
        for part in _chunks(pick):
            for r in _q(store,
                        "SELECT src_rowid AS rid, machine_uid, ts, kind, lab_id, "
                        "test_name, value, detail FROM legacy_index WHERE gen = "
                        "? AND src_rowid IN (%s)" % ",".join("?" * len(part)),
                        [gen] + part):
                ours[int(r["rid"])] = r
        bad = set()
        for rid in pick:
            a, b = theirs.get(rid), ours.get(rid)
            if a is None or b is None or \
                    any(a.get(c) != b.get(c) for c in LOG_COLS):
                bad.add(rid // self.bucket)
        return bad

    #: Record rows read per page while the proof re-hashes them.
    _PROOF_PAGE = 20000

    def _prove(self, gen: int) -> None:
        """"Verified" is said of the RECORD: every `legacy_labcore` row the
        store holds is re-hashed from its stored columns and the content
        multiset compared with the proven copy of LabCore's log. Local reads
        only. Fails — never quietly — on:

        * a stored row whose content does not match its key;
        * a LabCore row the record lacks (lost);
        * a record row LabCore does not hold (an extra copy or a fabricated
          reading), unless the record already held it, verified, before this
          numbering began and LabCore has deleted it since (kept and said:
          the record is append-only).
        """
        store = self.store
        cut = int(get_meta(store, "import_cut") or 0)
        entries = _q(store, "SELECT x.h, x.log_id, l.origin, x.detail FROM "
                            # raw-log: the import proves every row it copied
                            "legacy_index x LEFT JOIN lem_machine_log l ON l.id "
                            "= x.log_id WHERE x.gen = ?", [gen])
        expected: Counter = Counter()
        unheld = 0
        legacy_ids: Counter = Counter()
        linked = 0
        for e in entries:
            if e["log_id"] is None or e["origin"] is None:
                unheld += 1
            elif e["origin"] == "legacy_labcore":
                expected[e["h"]] += 1
                legacy_ids[e["log_id"]] += 1
            elif jk_of(e["detail"]) is not None:
                linked += 1                 # the bench's own record (jk)
            else:
                unheld += 1
        problems = []
        if unheld:
            problems.append("%d of LabCore's rows have no row in the record"
                            % unheld)
        shared = sum(n - 1 for n in legacy_ids.values() if n > 1)
        if shared:
            problems.append("%d of LabCore's rows share a record row with "
                            "another" % shared)
        stored: Counter = Counter()
        mismatched: List[int] = []
        orphans: List[int] = []
        since = 0
        while True:
            page = _q(store,
                      "SELECT id, legacy_key, machine_uid, ts, kind, lab_id, "
                      "test_name, value, detail FROM lem_machine_log WHERE "
                      # raw-log: the proof reads every row the import put in
                      "origin = 'legacy_labcore' AND id > ? ORDER BY id LIMIT ?",
                      [since, self._PROOF_PAGE])
            if not page:
                break
            for r in page:
                h = row_hash(r)
                key = r.get("legacy_key") or ""
                if not key.startswith("lc:%s:" % h):
                    mismatched.append(r["id"])
                stored[h] += 1
                if r["id"] not in legacy_ids:
                    orphans.append(r["id"])
            since = page[-1]["id"]
        if mismatched:
            problems.append("%d record rows' content does not match its key "
                            "(first id %s)" % (len(mismatched), mismatched[0]))
        lost = sum(max(0, n - stored[h]) for h, n in expected.items())
        extra = sum(max(0, n - expected[h]) for h, n in stored.items())
        if lost:
            problems.append("%d of LabCore's rows are missing from the record"
                            % lost)
        kept = 0
        if extra or orphans:
            base = get_meta(store, "legacy_gen_base_id")
            earlier = get_meta(store, "legacy_verified_gen") is not None
            allowed = [i for i in orphans if earlier and base is not None
                       and i <= int(base)]
            kept = len(allowed)
            if extra > kept or len(orphans) > kept:
                problems.append(
                    "the record holds %d legacy_labcore rows LabCore does not "
                    "hold (first id %s): a copy the import cannot account for "
                    "— it is not verified, and a person must look"
                    % (max(extra, len(orphans)) - kept,
                       next((i for i in orphans if i not in allowed), "?")))
        if problems:
            raise ImportFailed("the record is not LabCore's log: "
                               + "; ".join(problems))
        n = int(get_meta(store, "import_source_rows") or 0)
        cursor = _q(store, "SELECT COALESCE(MAX(src_rowid), 0) AS m FROM "
                           "legacy_index WHERE gen = ?", [gen])[0]["m"]
        note = ("the record's %d legacy rows match LabCore's %d rows at rowid "
                "<= %d by content, row for row (%d linked to bench records); "
                "counts, timestamp sums by range/machine/kind and %d sampled "
                "rows matched first (generation %d)" % (
                    sum(stored.values()) - kept, n, cut, linked,
                    min(self.sample, n), gen))
        if kept:
            note += ("; %d rows kept that LabCore deleted after they were "
                     "verified" % kept)
        with store.transaction():
            _x(store, "INSERT INTO import_run (table_name, source_rows, "
                      "max_rowid, copied, verified, finished_at) VALUES "
                      "('lem_machine_log', ?, ?, ?, ?, ?) ON CONFLICT"
                      "(table_name) DO UPDATE SET source_rows = "
                      "excluded.source_rows, max_rowid = excluded.max_rowid, "
                      "copied = excluded.copied, verified = excluded.verified, "
                      "finished_at = excluded.finished_at",
               [n, cut, sum(expected.values()) + linked, note, _now()])
            # The bridge pulls from the last row LabCore had in this
            # numbering (its anchor), not from a cut LabCore may not reach.
            set_meta(store, "legacy_cursor", cursor)
            set_meta(store, "legacy_cursor_gen", gen)
            set_meta(store, "legacy_verified_gen", gen)

    # ── the other tables ──
    def _tables(self) -> List[str]:
        store = self.store
        raw = get_meta(store, "import_tables")
        if raw is None:
            try:
                res = self._ask(
                    "SELECT name, sql FROM sqlite_master WHERE type = 'table' "
                    "AND name LIKE 'lem\\_%' ESCAPE '\\' ORDER BY name")
            except ImportFailed as exc:
                return ["the list of LabCore's lem_* tables: %s" % exc]
            names = {r["name"]: r["sql"] for r in res.get("rows") or []
                     if r.get("name") != "lem_machine_log"}
            set_meta(store, "import_tables", json.dumps(names, sort_keys=True))
            set_meta(store, "import_tables_total", len(names) + 1)
        else:
            names = json.loads(raw)
        done = {r["table_name"] for r in _q(
            store, "SELECT table_name FROM import_run WHERE verified IS NOT NULL")}
        problems = []
        for name in sorted(names):
            if name in done:
                continue
            try:
                self._table(name, names[name] or "")
            except ImportFailed as exc:
                problems.append("%s: %s" % (name, exc))
        return problems

    def _table(self, name: str, ddl: str) -> None:
        """One table: one read, copied and read back in one transaction.

        On the normal first boot the store's table is empty and this is a
        straight copy. If the store already holds rows — the server itself
        wrote them during the minutes before the import finished (a person
        saved a correction on v4's web) — they are NOT overwritten by the
        older LabCore copy: a store row whose key LabCore also has and whose
        content differs is kept (the store is the newer writer) and the
        projection then sends it to LabCore; a store row LabCore lacks is
        kept too. Both are counted in `import_run.verified`. Every other
        LabCore row must read back from the store byte-for-byte."""
        if not re.match(r"\Alem_[A-Za-z0-9_]+\Z", name):
            raise ImportFailed("not a LEM table name")
        store = self.store
        res = self._ask("SELECT * FROM %s ORDER BY rowid" % name)
        rows = list(res.get("rows") or [])
        cols = list(res.get("columns") or []) or _ddl_columns(ddl)
        if not cols:
            raise ImportFailed("its columns could not be read")
        source_sha = _rows_sha(rows, cols)
        self._declare(name, ddl, cols)
        collist = ", ".join('"%s"' % c for c in cols)
        pk = [r["name"] for r in sorted(
            (r for r in _q(store, 'PRAGMA table_info("%s")' % name)
             if r.get("pk")), key=lambda r: r["pk"])]
        pk = pk if pk and all(c in cols for c in pk) else []
        with store.transaction():
            before = _q(store, 'SELECT %s FROM "%s"' % (collist, name))
            mine = {}
            if before and pk:
                mine = {json.dumps([r.get(c) for c in pk], default=str): r
                        for r in before}
            elif before:
                _x(store, 'DELETE FROM "%s"' % name)   # no key: LabCore's copy
            kept_newer, copied = [], []
            for r in rows:
                key = json.dumps([r.get(c) for c in pk], default=str) if pk \
                    else None
                held = mine.pop(key, None) if key is not None else None
                if held is not None:
                    if [held.get(c) for c in cols] != [r.get(c) for c in cols]:
                        kept_newer.append(key)
                    else:
                        copied.append(r)
                    continue
                _x(store, 'INSERT INTO "%s" (%s) VALUES (%s)' % (
                    name, collist, ", ".join("?" * len(cols))),
                   [r.get(c) for c in cols], what="copy %s" % name)
                copied.append(r)
            kept_local = len(mine)
            # Read back: every row the store now holds as LabCore's must be
            # exactly LabCore's, as a multiset over the whole row; the rows
            # kept from the store are the only ones left out.
            skip = set(kept_newer) | set(mine)
            back = _q(store, 'SELECT %s FROM "%s"' % (collist, name))
            got = sorted(_canon([r.get(c) for c in cols]) for r in back
                         if not pk or json.dumps([r.get(c) for c in pk],
                                                 default=str) not in skip)
            want = sorted(_canon([r.get(c) for c in cols]) for r in copied)
            if got != want:
                raise ImportFailed("the copy read back differs from LabCore's "
                                   "(%d rows vs %d)" % (len(got), len(want)))
            note = source_sha
            if kept_newer or kept_local:
                note = "%s; kept from the store: %d newer, %d not in LabCore" % (
                    source_sha, len(kept_newer), kept_local)
            _x(store, "INSERT INTO import_run (table_name, source_rows, copied, "
                      "verified, finished_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT"
                      "(table_name) DO UPDATE SET source_rows = "
                      "excluded.source_rows, copied = excluded.copied, "
                      "verified = excluded.verified, finished_at = "
                      "excluded.finished_at",
               [name, len(rows), len(copied), note, _now()])
            import bridge
            bridge.seed_projection_state(
                store, name, only=[[r.get(c) for c in bridge.PROJECTED[name][0]]
                                   for r in copied]
                if name in bridge.PROJECTED else None)

    def _declare(self, name: str, ddl: str, cols: List[str]) -> None:
        store = self.store
        have = [r["name"] for r in _q(store, 'PRAGMA table_info("%s")' % name)]
        if not have:
            text = re.sub(r"\A\s*CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?",
                          "CREATE TABLE IF NOT EXISTS ", ddl or "", flags=re.I)
            if not text.startswith("CREATE TABLE IF NOT EXISTS") or \
                    not re.match(r'CREATE TABLE IF NOT EXISTS\s+"?%s"?[\s(]'
                                 % re.escape(name), text):
                text = 'CREATE TABLE IF NOT EXISTS "%s" (%s)' % (
                    name, ", ".join('"%s"' % c for c in cols))
            _x(store, text, what="declare %s" % name)
            have = [r["name"] for r in _q(store, 'PRAGMA table_info("%s")'
                                          % name)]
        types = dict(_ddl_columns(ddl, with_types=True))
        for c in cols:
            if c not in have:
                _x(store, 'ALTER TABLE "%s" ADD COLUMN "%s" %s' % (
                    name, c, types.get(c) or ""),
                   what="add %s.%s (LabCore has it)" % (name, c))


def _ddl_columns(ddl: str, with_types: bool = False) -> List:
    """The columns a CREATE TABLE declares, asked of SQLite itself."""
    if not ddl:
        return []
    con = sqlite3.connect(":memory:")
    try:
        con.execute(ddl)
        name = con.execute("SELECT name FROM sqlite_master WHERE type = "
                           "'table'").fetchone()[0]
        info = con.execute('PRAGMA table_info("%s")' % name).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    return [(r[1], r[2]) for r in info] if with_types else [r[1] for r in info]


def _rows_sha(rows: List[dict], cols: List[str]) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(_canon([r.get(c) for c in cols]))
        h.update(b"\n")
    return h.hexdigest()


class ImportService:
    """The import on a thread, run again after each failure until verified
    (a resume each time; nothing done twice). Started by the server's boot
    only when asked (`--import-from-mirror`): it reads production LabCore."""

    def __init__(self, importer: Importer, retry_s: float = 60.0) -> None:
        self.importer = importer
        self.retry_s = float(retry_s)
        self.last: Optional[dict] = None
        self._stop = None
        self._thread = None

    def start(self) -> None:
        import threading
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="lem-import",
                                        daemon=True)
        self._thread.start()

    def stop(self, wait_s: float = 10.0) -> None:
        """Ask the thread to stop, and wait (bounded) for a run in flight to
        finish, so whoever closes the store next does not pull it out from
        under a write."""
        if self._stop is not None:
            self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(wait_s)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.last = self.importer.run()
            except Exception as exc:                    # noqa: BLE001
                logger.exception("import: run failed; trying again")
                self.last = {"state": "failed", "problems": [str(exc)]}
            if self.last.get("state") == "verified":
                return
            self._stop.wait(self.retry_s)


# ── command line ────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Import LEM's lem_* tables from LabCore into the LEM "
                    "store (transfer spec §10.1). Production needs Ryan.")
    p.add_argument("--store", default=None, help="LEM store path "
                   "(default LEM_STORE_PATH or C:\\ASAPApps\\lem\\store\\lem.db)")
    p.add_argument("--mirror", default=None,
                   help="the v3.9 server's data/log-mirror.sqlite3 (copied, "
                        "never opened in place)")
    p.add_argument("--labcore-url", default=None)
    p.add_argument("--reimport", action="store_true",
                   help="walk LabCore's log again in a new numbering "
                        "generation (after a VACUUM); adds only what is missing")
    p.add_argument("--yes-this-is-production", action="store_true",
                   help="required: this reads production LabCore (40-70 reads)")
    args = p.parse_args(argv)
    if not args.yes_this_is_production:
        print("Refusing: this reads production LabCore. Ryan decides when "
              "(transfer §10.1). Re-run with --yes-this-is-production.")
        return 2
    from labcore_gateway import HttpLabCoreGateway
    from lem_store import LocalStoreGateway, default_store_path
    store = LocalStoreGateway(args.store or default_store_path())
    lab = HttpLabCoreGateway(args.labcore_url) if args.labcore_url \
        else HttpLabCoreGateway()
    out = Importer(store, lab, mirror_path=args.mirror).run(
        reimport=args.reimport)
    print(json.dumps(out, indent=1, default=str))
    return 0 if out["state"] == "verified" else 1


if __name__ == "__main__":
    sys.exit(main())
