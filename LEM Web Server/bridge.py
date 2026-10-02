#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bridge.py — the mixed fleet: server v4 while any v3.9 bench is still on the
floor (transfer spec §10.4, piece T-P9).

A v3.9 bench knows nothing of the LEM store. It writes its readings, status
and heartbeat into LabCore's `lem_*` tables and reads its configuration,
corrections, QC library, targets and maintenance from them. While any such
bench exists, server v4 has to carry both directions across:

**In — the legacy pull** (`pull`, every 60 s): `lem_machine_log WHERE rowid >=
cursor ORDER BY rowid LIMIT 20000`, ONE read, applied by
`legacy_import.LegacyWriter` — content keys, so nothing lands twice, and a
VACUUM that renumbered LabCore's rowids is recognised at the cursor row and
answered with a re-walk that adds nothing. Rows carrying `detail.jk` link to
the bench record they project (a v4 bench in legacy projection mode), so its
later v2 sync does not double them (M6). A v3.9 replay burst — a poll of ≥ 20
rows that each have an earlier identical twin — arrives with a VISIBLE
`replay_candidate` annotation: said, never hidden (§10.5 needs a person).

**In — the state arms** (`state_pull`, every 12 s): status, heartbeat,
substatus and effective specs, ONE read, and ONLY for uids still in legacy
mode — a bench that has synced v2 reports its own state and costs this
nothing. When every registered bench is v2 the read is not made at all.

**No per-arm fallback, anywhere.** A refused or killed read is retried at the
next cycle and nothing else happens. The v3.9 snapshot answered one watchdog
with 18 single-arm reads per refresh for 25 refreshes: +426 reads (baseline
W1). Here one kill costs +0.

**Out — the projection** (`project` + `drain`): the tables a v3.9 module reads
(`lem_machine_control`, `lem_qc_samples`, `lem_qc_specs`,
`lem_machine_targets`, `lem_maintenance`, `lem_correction_factors`,
`lem_meta.live_*`) and `lem_machine_config`. Every change is found by
comparing the store with `projection_state`, written to `projection_outbox`
FIRST (store first: the change cannot be forgotten once made), then sent in
order, upserts before deletes ("upsert first, prune last"), retried with
backoff, never marked landed unless LabCore took it. Projecting a machine's
configuration never overwrites the cursor keys a v3.9 bench keeps in it
(`last_position`, `last_mtime`, `last_result_file`): a web edit cannot rewind
a bench into a replay.

**Out — the downgrade bound** (`mirror_cursors`, every 15 min): for each v2
bench, its mirrored source offset is written into LabCore with a SINGLE-KEY
`json_set(config, '$.last_position', ?)` — only when it changed, through the
outbox. A bench rolled back to the v3.9 module starts from there and replays
at most 15 minutes of prints (DG1), where today it replays everything since
its offset was last saved by hand (09-23/09-24).

There are deliberately NO replica tables and no LabCore DDL: Ryan declined
D2's factor/override replica (decisions.md). Every write here lands in a
table v3.9 already has and reads.

Everything runs only while the bridge is on (`store_meta.bridge`, the switch
custody guards) AND the import is verified. Off, it makes no LabCore call at
all (S4). Every write in this module is a production LabCore write when it
runs live — the server v4 deploy that turns it on is Ryan's (§10.4 `[RYAN]`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

import legacy_import as li
from labcore_result import refusal_of

logger = logging.getLogger(__name__)

PULL_EVERY_S = 60.0
STATE_EVERY_S = 12.0
MIRROR_EVERY_S = 900.0
PULL_CHUNK = li.CHUNK
#: Outbox retry: 5 s doubling to 5 min.
BACKOFF_MIN_S = 5.0
BACKOFF_MAX_S = 300.0
DRAIN_BATCH = 100

#: v3.9's own columns for each table a v3.9 module reads, and its key.
PROJECTED: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "lem_machine_config": (("machine_uid",),
                           ("machine_uid", "title", "config", "updated_at",
                            "updated_by")),
    "lem_machine_control": (("machine_uid",),
                            ("machine_uid", "manual_override", "comment",
                             "updated_at")),
    "lem_qc_samples": (("name",), ("name", "sample_id_val", "tests")),
    "lem_qc_specs": (("machine_uid", "test_name"),
                     ("machine_uid", "test_name", "sample_id", "expected",
                      "std_dev", "k", "units")),
    "lem_machine_targets": (("machine_uid", "sample_name", "test_name"),
                            ("machine_uid", "sample_name", "test_name")),
    "lem_maintenance": (("uid",), ("uid", "machine_uid", "name", "kind",
                                   "interval_days", "last_done", "note")),
    "lem_correction_factors": (("machine_uid", "test_name"),
                               ("machine_uid", "test_name", "correction",
                                "units", "updated_at", "updated_by")),
    "lem_meta": (("key",), ("key", "value")),
}
#: Upserts in this order; prunes after all of them, in reverse.
PROJECT_ORDER = ("lem_machine_config", "lem_machine_control", "lem_qc_samples",
                 "lem_qc_specs", "lem_machine_targets", "lem_maintenance",
                 "lem_correction_factors", "lem_meta")
#: Runtime keys a v3.9 bench owns inside its configuration (machine_configs.
#: RUNTIME_KEYS minus the operator's override, which the web sets).
CURSOR_KEYS = ("last_position", "last_mtime", "last_result_file")

_CONFIG_UPSERT = (
    "INSERT INTO lem_machine_config (machine_uid, title, config, updated_at, "
    "updated_by) VALUES (?, ?, ?, ?, ?) ON CONFLICT(machine_uid) DO UPDATE SET "
    "title = excluded.title, updated_at = excluded.updated_at, updated_by = "
    "excluded.updated_by, config = CASE WHEN json_valid(lem_machine_config."
    "config) AND json_valid(excluded.config) THEN json_patch(excluded.config, "
    "(SELECT json_group_object(key, value) FROM json_each(lem_machine_config."
    "config) WHERE key IN ('last_position', 'last_mtime', "
    "'last_result_file'))) ELSE excluded.config END")
CURSOR_MIRROR_SQL = (
    "UPDATE lem_machine_config SET config = json_set(config, "
    "'$.last_position', ?) WHERE machine_uid = ? AND json_valid(config)")

#: The four state arms, one statement, padded to one width.
_STATE_ARMS = (
    ("status", "lem_machine_status",
     ("title", "status", "reason", "updated_at")),
    ("beat", "lem_machine_heartbeat", ("last_poll", "watching")),
    ("sub", "lem_machine_substatus", ("qc", "pm", "calibration", "updated_at")),
    ("spec", "lem_machine_specs",
     ("test_name", "sample_id", "expected", "std_dev", "k", "units", "low",
      "high", "last_qc_at", "last_qc_value", "last_qc_in_spec", "correction",
      "updated_at")),
)
_WIDTH = max(len(c) for _s, _t, c in _STATE_ARMS)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(value) -> str:
    return hashlib.sha256(li._canon(value)).hexdigest()


# ── projection rows ─────────────────────────────────────────────────────────

def _store_columns(store, table: str) -> Optional[List[str]]:
    res = store.read_sql('PRAGMA table_info("%s")' % table)
    if not isinstance(res, dict) or res.get("error"):
        return None
    return [r["name"] for r in res.get("rows") or []]


def _strip_cursor(config):
    if not isinstance(config, str):
        return config
    try:
        parsed = json.loads(config)
    except ValueError:
        return config
    if not isinstance(parsed, dict):
        return config
    return json.dumps({k: v for k, v in parsed.items() if k not in CURSOR_KEYS},
                      sort_keys=True)


def projection_rows(store, table: str) -> Optional[Dict[str, Tuple[list, list]]]:
    """{pk: (key values, projected values)} as LabCore should hold them, or
    None when the store could not be read (never "no rows")."""
    keys, cols = PROJECTED[table]
    have = _store_columns(store, table)
    if not have:
        # Unreadable, or no such table in the store: decide nothing. Read as
        # "no rows" it would prune every row of LabCore's copy.
        return None
    cols = [c for c in cols if c in have]
    where = " WHERE key LIKE 'live\\_%' ESCAPE '\\'" if table == "lem_meta" else ""
    res = store.read_sql('SELECT %s FROM "%s"%s' % (
        ", ".join('"%s"' % c for c in cols), table, where))
    if not isinstance(res, dict) or res.get("error"):
        return None
    out = {}
    for r in res.get("rows") or []:
        values = [r.get(c) for c in cols]
        if table == "lem_machine_config":
            values[cols.index("config")] = _strip_cursor(r.get("config"))
        kv = [r.get(k) for k in keys]
        out[json.dumps(kv, default=str)] = (kv, list(zip(cols, values)))
    return out


def seed_projection_state(store, table: str, only=None) -> None:
    """After an import has made the store equal to LabCore for `table`,
    record that, so the bridge does not re-send ~70 rows LabCore already has.
    `only`: the key values of the rows that ARE LabCore's (rows the store
    kept as newer, or that LabCore lacks, are left out — so they are sent).
    Inside the importer's transaction."""
    if table not in PROJECTED:
        return
    rows = projection_rows(store, table)
    if rows is None:
        raise li.ImportFailed("could not read %s back to seed its projection "
                              "state" % table)
    if only is not None:
        keep = {json.dumps(kv, default=str) for kv in only}
        rows = {pk: v for pk, v in rows.items() if pk in keep}
    li._x(store, "DELETE FROM projection_state WHERE table_name = ?", [table])
    for pk, (_kv, pairs) in rows.items():
        li._x(store, "INSERT INTO projection_state (table_name, pk, sha) "
                     "VALUES (?, ?, ?)", [table, pk, _sha(pairs)])


def _upsert_sql(table: str, pairs: List[Tuple[str, object]]) -> Tuple[str, list]:
    if table == "lem_machine_config":
        d = dict(pairs)
        return _CONFIG_UPSERT, [d.get("machine_uid"), d.get("title"),
                                d.get("config"), d.get("updated_at"),
                                d.get("updated_by")]
    keys, _cols = PROJECTED[table]
    cols = [c for c, _v in pairs]
    rest = [c for c in cols if c not in keys]
    sql = "INSERT INTO %s (%s) VALUES (%s) ON CONFLICT(%s) DO " % (
        table, ", ".join(cols), ", ".join("?" * len(cols)), ", ".join(keys))
    sql += ("UPDATE SET " + ", ".join("%s = excluded.%s" % (c, c) for c in rest)
            if rest else "NOTHING")
    return sql, [v for _c, v in pairs]


def _delete_sql(table: str, kv: list) -> Tuple[str, list]:
    keys, _cols = PROJECTED[table]
    return ("DELETE FROM %s WHERE %s" % (
        table, " AND ".join("%s = ?" % k for k in keys)), list(kv))


# ── the bridge ──────────────────────────────────────────────────────────────

class Bridge:
    """The mixed-fleet bridge. `cycle()` does whatever is due; `start()` runs
    it on a thread. Every method is safe to call directly (the tests do)."""

    def __init__(self, store, labcore, *, clock: Callable[[], float] = time.time,
                 pull_every: float = PULL_EVERY_S,
                 state_every: float = STATE_EVERY_S,
                 mirror_every: float = MIRROR_EVERY_S,
                 chunk: int = PULL_CHUNK) -> None:
        self.store = store
        self.labcore = labcore
        self.clock = clock
        self.pull_every = float(pull_every)
        self.state_every = float(state_every)
        self.mirror_every = float(mirror_every)
        self.chunk = int(chunk)
        self._next = {"pull": 0.0, "state": 0.0, "mirror": 0.0, "drain": 0.0}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._declared = False
        self.info: Dict[str, dict] = {
            "pull": {"last_ok_at": None, "last_error": None, "added": 0,
                     "linked": 0, "renumbered": 0, "cursor": None, "gen": None},
            "state": {"last_ok_at": None, "last_error": None, "updated": 0},
            "outbox": {"pending": None, "oldest": None, "last_error": None,
                       "landed": 0},
            "mirror": {"last_at": None, "queued": 0, "ambiguous": []},
            "enabled": {"on": None, "why": "not checked yet"},
        }
        self.legacy_benches: Optional[int] = None
        self.replay_candidates = 0

    # ── plumbing ──
    def _note(self, part: str, **kw) -> None:
        with self._lock:
            self.info[part].update(kw)

    def _ensure(self) -> None:
        if self._declared:
            return
        li.ensure_tables(self.store)
        import bench_api
        bench_api.ensure_current_state_tables(self.store)
        self._declared = True

    def enabled(self) -> bool:
        """On only while the switch is on AND the import is verified. A store
        that cannot be read is OFF (nothing is sent on a guess)."""
        res = self.store.read_sql("SELECT key, value FROM store_meta WHERE key "
                                  "IN ('bridge', 'import_state')")
        if not isinstance(res, dict) or res.get("error"):
            self._note("enabled", on=False,
                       why="the store could not be read: %s" % (
                           (res or {}).get("error") if isinstance(res, dict)
                           else res))
            return False
        meta = {r["key"]: r["value"] for r in res.get("rows") or []}
        if (meta.get("import_state") or "not started") != \
                li.cached_status(self.store).get("state"):
            # The import moved on (here, or from the command line in another
            # process): bring /healthz's copy up to date.
            try:
                li.refresh_status(self.store)
            except Exception:                           # noqa: BLE001
                pass
        if (meta.get("bridge") or "on") == "off":
            self._note("enabled", on=False, why="turned off")
            return False
        if meta.get("import_state") != "verified":
            self._note("enabled", on=False,
                       why="the import from LabCore is not verified")
            return False
        self._note("enabled", on=True, why="")
        return True

    def cycle(self, now: Optional[float] = None) -> dict:
        """Everything that is due. Returns what ran."""
        now = self.clock() if now is None else now
        ran = {}
        if not self.enabled():
            return ran
        try:
            self._ensure()
        except li.ImportFailed as exc:
            self._note("state", last_error=str(exc))
            return ran
        if now >= self._next["state"]:
            self._next["state"] = now + self.state_every
            ran["state"] = self.state_pull()
            # The projection is a local comparison: on the state cadence, so
            # a change reaches the outbox within 12 s and costs LabCore
            # nothing until there is something to send.
            ran["project"] = self.project()
        if now >= self._next["pull"]:
            self._next["pull"] = now + self.pull_every
            ran["pull"] = self.pull()
        if now >= self._next["mirror"]:
            self._next["mirror"] = now + self.mirror_every
            ran["mirror"] = self.mirror_cursors()
        queued = (ran.get("project") or {}).get("queued") or \
            (ran.get("mirror") or {}).get("queued")
        pending = self.info["outbox"].get("pending")
        if now >= self._next["drain"] and (queued or pending is None
                                           or pending > 0):
            ran["drain"] = self.drain(now)
        return ran

    # ── in: the log ──
    def pull(self) -> dict:
        """One read of LabCore's log from the cursor. A refusal is retried
        next cycle; nothing else happens (no fallback)."""
        store = self.store
        try:
            self._ensure()
            gen = int(li.get_meta(store, "legacy_gen") or 0)
            cursor = int(li.get_meta(store, "legacy_cursor") or 0)
        except (li.ImportFailed, ValueError) as exc:
            self._note("pull", last_error="the store: %s" % exc)
            return {"ok": False, "error": str(exc)}
        # Both reads are of LabCore's own table, which has no effective
        # view: the bridge copies every row v3.9 benches wrote.
        if cursor > 0:
            # raw-log: LabCore's own table (see above)
            sql = ("SELECT %s FROM lem_machine_log WHERE rowid >= ? ORDER BY "
                   "rowid LIMIT ?" % li._LOG_SELECT)
            args = [cursor, self.chunk + 1]
        else:
            # raw-log: LabCore's own table (see above)
            sql = ("SELECT %s FROM lem_machine_log WHERE rowid > ? ORDER BY "
                   "rowid LIMIT ?" % li._LOG_SELECT)
            args = [0, self.chunk]
        try:
            res = self.labcore.read_sql(sql, args)
        except Exception as exc:                        # noqa: BLE001
            res = {"error": "%s: %s" % (type(exc).__name__, exc)}
        why = refusal_of(res)
        if why is not None:
            self._note("pull", last_error=why)
            return {"ok": False, "error": why}
        rows = list(res.get("rows") or [])
        try:
            if cursor > 0:
                anchor = li._q(store, "SELECT h FROM legacy_index WHERE gen = ? "
                                      "AND src_rowid = ?", [gen, cursor])
                first = rows[0] if rows else None
                if not anchor or first is None or int(first["rid"]) != cursor \
                        or li.row_hash(first) != anchor[0]["h"]:
                    return self._renumbered(gen, cursor)
                rows = rows[1:]
            if not rows:
                self._note("pull", last_ok_at=_now_iso(), last_error=None,
                           cursor=cursor, gen=gen)
                return {"ok": True, "added": 0}
            writer = li.LegacyWriter(store, gen, annotate=True,
                                     by="lem-bridge")
            with store.transaction():
                writer.apply(rows)
                cursor = int(rows[-1]["rid"])
                li.set_meta(store, "legacy_cursor", cursor)
                li.set_meta(store, "legacy_cursor_gen", gen)
        except Exception as exc:                        # noqa: BLE001
            # The store's transaction rolled back: the cursor did not move,
            # so the same rows are read again next cycle.
            self._note("pull", last_error="the store: %s" % exc)
            return {"ok": False, "error": str(exc)}
        with self._lock:
            p = self.info["pull"]
            p.update(last_ok_at=_now_iso(), last_error=None, cursor=cursor,
                     gen=gen)
            p["added"] += writer.added
            p["linked"] += writer.linked
            self.replay_candidates += writer.candidates
        return {"ok": True, "added": writer.added, "linked": writer.linked,
                "candidates": writer.candidates, "rows": len(rows)}

    def _renumbered(self, gen: int, cursor: int) -> dict:
        """The row at the cursor is not the row the store holds there:
        LabCore's rowids moved (a VACUUM after deletes). Walk again from the
        start in a new generation; rows already held are found by content."""
        store = self.store
        new = gen + 1
        with store.transaction():
            li.set_meta(store, "legacy_gen", new)
            li.set_meta(store, "legacy_cursor", 0)
            li.set_meta(store, "legacy_cursor_gen", new)
            # The old numbering describes rowids LabCore no longer has.
            li._x(store, "DELETE FROM legacy_index WHERE gen < ?", [new])
        logger.warning("bridge: LabCore's log rowids moved at %d (generation "
                       "%d → %d); walking it again — rows already held are "
                       "recognised by content", cursor, gen, new)
        with self._lock:
            self.info["pull"]["renumbered"] += 1
            self.info["pull"].update(cursor=0, gen=new, last_error=None,
                                     last_ok_at=_now_iso())
        return {"ok": True, "renumbered": True, "gen": new}

    # ── in: state ──
    def legacy_uids(self) -> List[str]:
        """Registered, not retired, and never synced v2."""
        rows = li._q(self.store,
                     "SELECT machine_uid FROM lem_machine_config WHERE "
                     "retired_at IS NULL AND machine_uid NOT IN (SELECT "
                     "machine_uid FROM bench_cursor WHERE mode = 'v2') "
                     "ORDER BY machine_uid")
        return [r["machine_uid"] for r in rows if r.get("machine_uid")]

    def state_pull(self) -> dict:
        store = self.store
        try:
            self._ensure()
            uids = self.legacy_uids()
        except li.ImportFailed as exc:
            self._note("state", last_error=str(exc))
            return {"ok": False, "error": str(exc)}
        self.legacy_benches = len(uids)
        if not uids:
            self._note("state", last_ok_at=_now_iso(), last_error=None)
            return {"ok": True, "skipped": "no legacy benches", "reads": 0}
        marks = ", ".join("?" * len(uids))
        arms = []
        for src, table, cols in _STATE_ARMS:
            padded = list(cols) + ["NULL"] * (_WIDTH - len(cols))
            arms.append("SELECT '%s' AS src, machine_uid AS uid, %s FROM %s "
                        "WHERE machine_uid IN (%s)" % (
                            src, ", ".join("%s AS c%d" % (c, i) for i, c in
                                           enumerate(padded)), table, marks))
        sql = " UNION ALL ".join(arms)
        try:
            res = self.labcore.read_sql(sql, uids * len(_STATE_ARMS))
        except Exception as exc:                        # noqa: BLE001
            res = {"error": "%s: %s" % (type(exc).__name__, exc)}
        why = refusal_of(res)
        if why is not None:
            self._note("state", last_error=why)
            return {"ok": False, "error": why}
        got: Dict[str, Dict[str, list]] = {}
        for r in res.get("rows") or []:
            got.setdefault(r.get("src"), {}).setdefault(r.get("uid"), []).append(r)
        try:
            with store.transaction():
                changed = self._apply_state(got, uids)
        except Exception as exc:                        # noqa: BLE001
            self._note("state", last_error="the store: %s" % exc)
            return {"ok": False, "error": str(exc)}
        with self._lock:
            self.info["state"].update(last_ok_at=_now_iso(), last_error=None)
            self.info["state"]["updated"] += changed
        return {"ok": True, "updated": changed}

    def _apply_state(self, got, uids) -> int:
        store = self.store
        changed = 0
        for src, table, cols in _STATE_ARMS:
            arm = got.get(src) or {}
            if src == "spec":
                for uid, rows in arm.items():
                    want = sorted(([r.get("c%d" % i) for i in range(len(cols))]
                                   for r in rows), key=lambda v: str(v[0]))
                    have = sorted(([r.get(c) for c in cols] for r in li._q(
                        store, "SELECT %s FROM lem_machine_specs WHERE "
                               "machine_uid = ?" % ", ".join(cols), [uid])),
                        key=lambda v: str(v[0]))
                    if want == have:
                        continue
                    li._x(store, "DELETE FROM lem_machine_specs WHERE "
                                 "machine_uid = ?", [uid])
                    for v in want:
                        li._x(store, "INSERT INTO lem_machine_specs (machine_uid, "
                                     "%s) VALUES (?, %s)" % (
                                         ", ".join(cols),
                                         ", ".join("?" * len(cols))), [uid] + v)
                    changed += 1
                continue
            for uid, rows in arm.items():
                want = [rows[0].get("c%d" % i) for i in range(len(cols))]
                have = li._q(store, "SELECT %s FROM %s WHERE machine_uid = ?"
                             % (", ".join(cols), table), [uid])
                if have and [have[0].get(c) for c in cols] == want:
                    continue
                li._x(store, "INSERT INTO %s (machine_uid, %s) VALUES (?, %s) "
                             "ON CONFLICT(machine_uid) DO UPDATE SET %s" % (
                                 table, ", ".join(cols),
                                 ", ".join("?" * len(cols)),
                                 ", ".join("%s = excluded.%s" % (c, c)
                                           for c in cols)), [uid] + want)
                changed += 1
        return changed

    # ── out: projection ──
    def project(self) -> dict:
        """Find what changed in the store since it was last projected, and
        write it to the outbox (store first). Local only: no LabCore call."""
        store = self.store
        upserts, deletes, states = [], [], []
        for table in PROJECT_ORDER:
            want = projection_rows(store, table)
            if want is None:
                continue                  # unreadable: decide nothing
            res = store.read_sql("SELECT pk, sha FROM projection_state WHERE "
                                 "table_name = ?", [table])
            if not isinstance(res, dict) or res.get("error"):
                continue
            have = {r["pk"]: r["sha"] for r in res.get("rows") or []}
            for pk, (kv, pairs) in want.items():
                sha = _sha(pairs)
                if have.get(pk) != sha:
                    upserts.append((table, _upsert_sql(table, pairs)))
                    states.append(("set", table, pk, sha))
            if table == "lem_meta":
                continue                  # never prune: the boot publishes these
            for pk in have:
                if pk not in want:
                    deletes.append((table, _delete_sql(table, json.loads(pk))))
                    states.append(("del", table, pk, None))
        if not upserts and not deletes:
            return {"queued": 0}
        now = _now_iso()
        try:
            with store.transaction():
                for table, (sql, args) in upserts + list(reversed(deletes)):
                    li._x(store, "INSERT INTO projection_outbox (sql, args, "
                                 "table_name, created_at) VALUES (?, ?, ?, ?)",
                          [sql, json.dumps(args, default=str), table, now])
                for op, table, pk, sha in states:
                    if op == "set":
                        li._x(store, "INSERT INTO projection_state (table_name, "
                                     "pk, sha) VALUES (?, ?, ?) ON CONFLICT"
                                     "(table_name, pk) DO UPDATE SET sha = "
                                     "excluded.sha", [table, pk, sha])
                    else:
                        li._x(store, "DELETE FROM projection_state WHERE "
                                     "table_name = ? AND pk = ?", [table, pk])
        except Exception as exc:                        # noqa: BLE001
            self._note("outbox", last_error="the store: %s" % exc)
            return {"queued": 0, "error": str(exc)}
        return {"queued": len(upserts) + len(deletes)}

    def drain(self, now: Optional[float] = None) -> dict:
        """Send the outbox to LabCore in order. The first refusal stops the
        drain (order is kept) and backs off; nothing is marked landed that
        LabCore did not take."""
        now = self.clock() if now is None else now
        store = self.store
        res = store.read_sql("SELECT id, sql, args, tries, created_at FROM "
                             "projection_outbox WHERE landed_at IS NULL "
                             "ORDER BY id LIMIT ?", [DRAIN_BATCH])
        if not isinstance(res, dict) or res.get("error"):
            self._note("outbox", last_error="the store: %s" % (res or {}).get(
                "error") if isinstance(res, dict) else res)
            return {"sent": 0}
        sent = 0
        for item in res.get("rows") or []:
            try:
                args = json.loads(item["args"] or "[]")
                answer = self.labcore.sql(item["sql"], args)
            except Exception as exc:                    # noqa: BLE001
                answer = {"error": "%s: %s" % (type(exc).__name__, exc)}
            why = refusal_of(answer)
            if why is not None:
                tries = int(item["tries"] or 0) + 1
                store.sql("UPDATE projection_outbox SET tries = ?, last_error "
                          "= ? WHERE id = ?", [tries, why[:500], item["id"]])
                wait = min(BACKOFF_MAX_S, BACKOFF_MIN_S * 2 ** (tries - 1))
                self._next["drain"] = now + wait
                self._note("outbox", last_error=why)
                break
            store.sql("UPDATE projection_outbox SET landed_at = ?, last_error "
                      "= NULL WHERE id = ?", [_now_iso(), item["id"]])
            sent += 1
        with self._lock:
            self.info["outbox"]["landed"] += sent
        self._refresh_outbox_info()
        return {"sent": sent}

    def _refresh_outbox_info(self) -> None:
        res = self.store.read_sql("SELECT COUNT(*) AS n, MIN(created_at) AS "
                                  "oldest FROM projection_outbox WHERE "
                                  "landed_at IS NULL")
        if isinstance(res, dict) and not res.get("error") and res.get("rows"):
            self._note("outbox", pending=int(res["rows"][0]["n"] or 0),
                       oldest=res["rows"][0]["oldest"])
            if not res["rows"][0]["n"]:
                self._note("outbox", last_error=None)

    # ── out: the downgrade bound ──
    def mirror_cursors(self) -> dict:
        """Each v2 bench's source offset into LabCore's config, one key, only
        when it moved. Through the outbox (store first)."""
        store = self.store
        try:
            v2 = [r["machine_uid"] for r in li._q(
                store, "SELECT DISTINCT machine_uid FROM bench_cursor WHERE "
                       "mode = 'v2' ORDER BY machine_uid")]
            queued, ambiguous = 0, []
            with store.transaction():
                for uid in v2:
                    offsets = []
                    for s in li._q(store, "SELECT src, cursor FROM bench_source "
                                          "WHERE machine_uid = ? ORDER BY src",
                                   [uid]):
                        try:
                            cur = json.loads(s.get("cursor") or "{}")
                        except ValueError:
                            continue
                        off = cur.get("offset") if isinstance(cur, dict) else None
                        if isinstance(off, int) and not isinstance(off, bool) \
                                and off >= 0:
                            offsets.append((s["src"], off))
                    if len(offsets) != 1:
                        if len(offsets) > 1:
                            ambiguous.append(uid)
                        continue
                    src, off = offsets[0]
                    prev = li._q(store, "SELECT last_position FROM cursor_mirror "
                                        "WHERE machine_uid = ?", [uid])
                    if prev and prev[0]["last_position"] == off:
                        continue
                    li._x(store, "INSERT INTO projection_outbox (sql, args, "
                                 "table_name, created_at) VALUES (?, ?, "
                                 "'lem_machine_config', ?)",
                          [CURSOR_MIRROR_SQL, json.dumps([off, uid]),
                           _now_iso()])
                    li._x(store, "INSERT INTO cursor_mirror (machine_uid, src, "
                                 "last_position, queued_at) VALUES (?, ?, ?, ?) "
                                 "ON CONFLICT(machine_uid) DO UPDATE SET src = "
                                 "excluded.src, last_position = "
                                 "excluded.last_position, queued_at = "
                                 "excluded.queued_at", [uid, src, off, _now_iso()])
                    queued += 1
        except Exception as exc:                        # noqa: BLE001
            self._note("mirror", last_error=str(exc))
            return {"queued": 0, "error": str(exc)}
        with self._lock:
            self.info["mirror"].update(last_at=_now_iso(), ambiguous=ambiguous)
            self.info["mirror"]["queued"] += queued
        return {"queued": queued, "ambiguous": ambiguous}

    # ── what it says ──
    def status(self) -> dict:
        with self._lock:
            info = json.loads(json.dumps(self.info, default=str))
        return {"on": info["enabled"]["on"], "why_off": info["enabled"]["why"],
                "legacy_benches": self.legacy_benches,
                "outbox": info["outbox"], "pull": info["pull"],
                "state": info["state"], "mirror": info["mirror"],
                "replay_candidates": self.replay_candidates,
                "running": bool(self._thread and self._thread.is_alive())}

    def status_items(self) -> List[dict]:
        """Global-status lines (the custody shape): only what needs a person."""
        out = []
        with self._lock:
            ob = dict(self.info["outbox"])
            pull = dict(self.info["pull"])
            on = self.info["enabled"]["on"]
        # Waiting changes are said whether or not the bridge is on now:
        # switched off, they are waiting for it to come back.
        if ob.get("pending"):
            out.append({"key": "bridge_outbox",
                        "level": "amber" if ob.get("last_error") else "info",
                        "message": "%d change%s waiting to reach older "
                                   "benches%s" % (
                                       ob["pending"],
                                       "" if ob["pending"] == 1 else "s",
                                       " (LabCore: %s)" % ob["last_error"][:80]
                                       if ob.get("last_error") else ""),
                        "href": "/settings#transfer", "link": "Open Transfer"})
        if on and pull.get("last_error"):
            out.append({"key": "bridge_pull", "level": "amber",
                        "message": "Readings from older benches are not "
                                   "arriving: LabCore %s" % pull["last_error"][:80],
                        "href": "/settings#transfer", "link": "Open Transfer"})
        return out

    # ── the thread ──
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="lem-bridge",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.cycle()
            except Exception:                           # noqa: BLE001
                logger.exception("bridge: cycle failed; trying again")
            self._stop.wait(1.0)
