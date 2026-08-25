#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
snapshot_service.py — decouple LabCore load from the number of people looking.

The web server was a bad neighbour. Measured on the live system, one refresh of
the pages a lab leaves open cost **17 LabCore ops**, so a single wall display
polling every 30s meant ~34 ops/min — and three screens was enough to push the
shared write queue (the one LabStation and LabEntry also use) deep enough that it
started rejecting work.

Two things fix that, and both matter:

1. **Requests never talk to LabCore.** One background thread refreshes an
   in-memory snapshot on a fixed interval; every read is served from it. LabCore
   load becomes *constant* — identical whether one screen is open or ten — and no
   request ever waits on a 1.35s round-trip.

2. **The machine tables arrive in one op.** LabCore accepts raw SQL, so nine
   small SELECTs become a single `UNION ALL` (verified: 5 tables, 15 rows,
   0.91s). If that is ever rejected — an older LabCore, or a table that doesn't
   exist yet — it falls back to reading each table separately. Slower, but never
   blank.

The snapshot always reports its own age and whether it is stale, because showing
data without saying how old it is, is how a stopped module passes for a live one.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List, Optional

# One rule for "what did LabCore actually tell me?", not a
# ninth re-derivation of it. `refusal_of` returns None for an
# answer carrying no failure signal, which is deliberate — see
# labcore_result for why demanding an acknowledgement would have
# failed every write in the lab.
from labcore_result import (LabCoreUnavailable, is_missing_table,
                            refusal_of, retry_after)
from labcore_result import rows as read_rows

# How often the poller refreshes. The floor's own polling is decoupled from this,
# so raising it costs freshness but not responsiveness.
DEFAULT_INTERVAL = 12.0
MAX_WORKERS = 8

# The vendored client allows 8s for a read, because `read_sql` POSTs to
# /api/queue/write and therefore waits its turn behind every write in the lab.
# Measured on the live system while six modules were publishing: `pending: 28`, and
# the batched read timed out at exactly 8.00s four times in six — while the query
# itself, run when the queue was clear, took **0.12s** for 103 rows.
#
# So the read was never slow; it was queued. This is a background thread, so waiting
# costs nothing and turns "offline banner plus stale data" into "fresh data, a moment
# later".
READ_TIMEOUT = 45.0
# How many of the newest log entries the snapshot carries. The floor polls for
# these every six seconds to animate a run blip; anything deeper (the log viewer)
# still reads live rather than being quietly truncated.
EVENT_LIMIT = 60

# Two rules, both learned the hard way:
#  * every arm must select the SAME number of columns, or the whole statement
#    fails and takes every table with it. Eight is what the widest arm
#    (qc_specs) needs; the rest pad with ''.
#  * EVERY arm needs explicit `AS src, AS c1…` aliases. In a UNION the names come
#    from the first arm, so unaliased arms look fine — but each arm is also run
#    ON ITS OWN in the fallback path, where its columns would come back named
#    `'layout'`, `machine_uid`, `CAST(pos_x AS TEXT)`… and the parser would find
#    no `src` at all. That silently emptied the floor's layout.
_ARMS = (
    ("status",
     "SELECT 'status' AS src, machine_uid AS c1, title AS c2, status AS c3, "
     "reason AS c4, updated_at AS c5, '' AS c6, '' AS c7, '' AS c8, '' AS c9 "
     "FROM lem_machine_status"),
    ("beat",
     "SELECT 'beat' AS src, machine_uid AS c1, last_poll AS c2, "
     "watching AS c3, '' AS c4, '' AS c5, '' AS c6, '' AS c7, '' AS c8, '' AS c9 "
     "FROM lem_machine_heartbeat"),
    ("sub",
     "SELECT 'sub' AS src, machine_uid AS c1, qc AS c2, pm AS c3, "
     "calibration AS c4, '' AS c5, '' AS c6, '' AS c7, '' AS c8, '' AS c9 "
     "FROM lem_machine_substatus"),
    ("layout",
     "SELECT 'layout' AS src, machine_uid AS c1, CAST(pos_x AS TEXT) AS c2, "
     "CAST(pos_y AS TEXT) AS c3, '' AS c4, '' AS c5, '' AS c6, '' AS c7, '' AS c8, '' AS c9 "
     "FROM lem_machine_layout"),
    ("target",
     "SELECT 'target' AS src, machine_uid AS c1, sample_name AS c2, "
     "test_name AS c3, '' AS c4, '' AS c5, '' AS c6, '' AS c7, '' AS c8, '' AS c9 "
     "FROM lem_machine_targets"),
    ("spec",
     "SELECT 'spec' AS src, machine_uid AS c1, test_name AS c2, "
     "sample_id AS c3, CAST(expected AS TEXT) AS c4, "
     "CAST(std_dev AS TEXT) AS c5, CAST(k AS TEXT) AS c6, units AS c7, '' AS c8, '' AS c9 "
     "FROM lem_qc_specs"),
    ("maint",
     "SELECT 'maint' AS src, machine_uid AS c1, uid AS c2, name AS c3, "
     "kind AS c4, CAST(interval_days AS TEXT) AS c5, last_done AS c6, "
     "note AS c7, '' AS c8, '' AS c9 FROM lem_maintenance"),
    ("sched",
     "SELECT 'sched' AS src, working_days AS c1, opens AS c2, closes AS c3, "
     "'' AS c4, '' AS c5, '' AS c6, '' AS c7, '' AS c8, '' AS c9 "
     "FROM lem_lab_schedule WHERE id = 1"),
    ("holiday",
     "SELECT 'holiday' AS src, day AS c1, name AS c2, '' AS c3, '' AS c4, "
     "'' AS c5, '' AS c6, '' AS c7, '' AS c8, '' AS c9 FROM lem_lab_holidays"),
    # ORDER BY / LIMIT has to be wrapped in a subquery to sit inside a UNION ALL,
    # and the wrapper keeps the aliases the fallback path needs.
    ("event",
     "SELECT * FROM (SELECT 'event' AS src, machine_uid AS c1, ts AS c2, "
     "kind AS c3, lab_id AS c4, test_name AS c5, value AS c6, detail AS c7, '' AS c8, '' AS c9 "
     f"FROM lem_machine_log ORDER BY ts DESC LIMIT {EVENT_LIMIT})"),
    # What the module is ACTUALLY checking, with the band it judges against.
    # `lem_qc_specs` and `lem_machine_targets` are both *inputs*; most QC here is
    # resolved at runtime from the shared standards, so neither had a row for it
    # and the floor said "No QC assigned" about a live instrument.
    ("espec",
     "SELECT 'espec' AS src, machine_uid AS c1, test_name AS c2, "
     "CAST(low AS TEXT) AS c3, CAST(high AS TEXT) AS c4, "
     "CAST(expected AS TEXT) AS c5, units AS c6, sample_id AS c7, "
     "CAST(last_qc_value AS TEXT) || '~' || COALESCE(CAST(correction AS TEXT),'0') "
     "AS c8, "
     "last_qc_at || '|' || COALESCE(CAST(last_qc_in_spec AS TEXT),'') AS c9 "
     "FROM lem_machine_specs"),
    ("activity",
     "SELECT 'activity' AS src, machine_uid AS c1, MAX(ts) AS c2, '' AS c3, "
     "'' AS c4, '' AS c5, '' AS c6, '' AS c7, '' AS c8, '' AS c9 "
     "FROM lem_machine_log GROUP BY machine_uid"),
)


# The tables the snapshot reads. Every writer creates its own on demand, so on a
# fresh LabCore some of these don't exist yet — and a UNION ALL naming a missing
# table fails *entirely*, taking the other nine with it. Declaring them once at
# startup is what makes the one-op read work on day one instead of only after
# every feature has been used at least once.
SCHEMA_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_machine_status (machine_uid TEXT PRIMARY KEY, "
    "title TEXT, status TEXT, reason TEXT, updated_at TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_machine_heartbeat (machine_uid TEXT PRIMARY KEY, "
    "last_poll TEXT, watching TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_machine_substatus (machine_uid TEXT PRIMARY KEY, "
    "qc TEXT, pm TEXT, calibration TEXT, updated_at TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_machine_layout (machine_uid TEXT PRIMARY KEY, "
    "pos_x REAL, pos_y REAL)",
    "CREATE TABLE IF NOT EXISTS lem_machine_targets (machine_uid TEXT NOT NULL, "
    "sample_name TEXT NOT NULL, test_name TEXT NOT NULL, "
    "PRIMARY KEY (machine_uid, sample_name, test_name))",
    "CREATE TABLE IF NOT EXISTS lem_qc_specs (machine_uid TEXT NOT NULL, "
    "test_name TEXT NOT NULL, sample_id TEXT, expected REAL, std_dev REAL, "
    "k REAL, units TEXT, PRIMARY KEY (machine_uid, test_name))",
    "CREATE TABLE IF NOT EXISTS lem_maintenance (uid TEXT PRIMARY KEY, "
    "machine_uid TEXT NOT NULL, name TEXT NOT NULL, kind TEXT, "
    "interval_days INTEGER, last_done TEXT, note TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_lab_schedule (id INTEGER PRIMARY KEY, "
    "working_days TEXT, opens TEXT, closes TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_lab_holidays (day TEXT PRIMARY KEY, name TEXT)",
    "CREATE TABLE IF NOT EXISTS lem_machine_specs ("
    "machine_uid TEXT NOT NULL, test_name TEXT NOT NULL, sample_id TEXT, "
    "expected REAL, std_dev REAL, k REAL, units TEXT, low REAL, high REAL, "
    "last_qc_at TEXT, last_qc_value REAL, last_qc_in_spec INTEGER, "
    "correction REAL DEFAULT 0.0, updated_at TEXT, "
    "PRIMARY KEY (machine_uid, test_name))",
    "CREATE TABLE IF NOT EXISTS lem_correction_factors ("
    "machine_uid TEXT NOT NULL, test_name TEXT NOT NULL, "
    "correction REAL NOT NULL DEFAULT 0.0, units TEXT, "
    "updated_at TEXT, updated_by TEXT, "
    "PRIMARY KEY (machine_uid, test_name))",
    "CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, ts TEXT, "
    "kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, detail TEXT)",
)

# Columns added to tables that already existed in the field. `CREATE TABLE IF NOT
# EXISTS` is a no-op on an existing table, so a new column needs an ALTER — and
# because every arm shares ONE statement, a column LabCore does not have fails the
# whole batched read, not just its own arm. That is exactly what happened when
# `correction` was added: the entire floor dropped to the fallback path.
SCHEMA_MIGRATIONS = (
    ("lem_machine_specs", "correction",
     "ALTER TABLE lem_machine_specs ADD COLUMN correction REAL DEFAULT 0.0"),
)


# How many refreshes to wait before trying the one-op read again after it was
# rejected. A permanent give-up would mean ten ops a cycle forever because of one
# bad minute or one table that has since been created.
BATCHED_RETRY_AFTER = 25



class SnapshotReadError(RuntimeError):
    """The machine list itself could not be read.

    Tolerating a failed read per-table is right for the trimmings — a missing
    maintenance row costs one pill — but not for `lem_machine_status`, which IS
    the floor. Reporting "no machines" when the truth is "could not ask" is how a
    whole lab reads as empty during a LabCore blip, so that case raises and the
    previous snapshot is kept instead.
    """


def batched_machine_sql() -> str:
    """Every machine-related table in one statement."""
    return "\n UNION ALL ".join(sql for _name, sql in _ARMS)


def split_batched(rows) -> Dict[str, List[dict]]:
    """Split a batched result back out by its `src` marker."""
    out: Dict[str, List[dict]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        src = str(row.get("src") or "").strip()
        if not src:
            continue
        out.setdefault(src, []).append(row)
    return out


class SnapshotService:
    """Holds the floor's state in memory and refreshes it in the background.

    `get()` never blocks and never raises: before the first refresh completes it
    returns an empty snapshot with `ready: False`, so a page can say "connecting"
    rather than hang.
    """

    def __init__(self, gateway, interval: float = DEFAULT_INTERVAL,
                 builder=None, clock=None) -> None:
        self.gateway = gateway
        self.interval = interval
        # Injectable so the schema back-off can be tested as the decision about
        # elapsed time that it is, rather than with real sleeps.
        self._clock = clock or time.monotonic
        self._builder = builder            # injected by web_app; see _build
        self._lock = threading.Lock()
        self._snapshot: Optional[dict] = None
        self._at: Optional[datetime] = None
        self._last_error = ""
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        # Serialises the very first build so a burst of arrivals at boot costs
        # ONE refresh between them, not one each.
        self._build_lock = threading.Lock()
        self.refreshes = 0
        self.batched_ok = True             # flips off if UNION ALL is rejected
        self._batched_cooldown = 0         # refreshes left before trying again
        self._schema_ready = False
        # Has a declaration ever been ATTEMPTED? Distinct from "did it work":
        # a server that has not refreshed yet knows nothing about its schema,
        # and /healthz reporting `degraded` for that is a false alarm that
        # fails a good release. See `schema_checked`.
        self._schema_checked = False
        # Why the schema is not known-good. Reported on /healthz: a service
        # quietly running on the 15-read fallback path with a missing column is
        # exactly the release RELEASING.md §5 says nothing else catches.
        self._schema_error = ""
        # When the next declaration attempt is allowed, and how long the last
        # refusal bought. See ensure_schema.
        self._schema_retry_at = 0.0
        self._schema_backoff = 0.0
        self._schema_hint = None
        self._online = True
        # The rows, not only the payload built from them. Other pages (schedule,
        # fleet maintenance) render from these same tables, so keeping them turns
        # two round-trips per request into none.
        self._tables: Dict[str, List[dict]] = {}
        # Which arms of the last read came back with an error rather than rows.
        # The per-table fallback path used to discard these: only the `status`
        # arm was allowed to fail loudly, so a maintenance read that timed out
        # became `maint: []` and `/api/maintenance` served it as "nothing
        # scheduled anywhere". "Read it, it was empty" and "could not read it"
        # have to stay two different facts all the way to the route.
        self._table_errors: Dict[str, str] = {}

    # ── lifecycle ──────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="lem-snapshot")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    MIN_GAP = 1.0        # never hot-loop, however slow the queue is

    def next_wait(self, elapsed: float) -> float:
        """How long to sleep so the CYCLE is `interval`, not interval + refresh.

        A read can wait tens of seconds behind LabCore's write queue. Sleeping a
        flat interval on top of that compounded the snapshot's age — soak-measured
        at 92s with a 12s interval. Floored so a refresh slower than the interval
        does not turn into a tight loop against a queue that is already struggling.
        """
        # The floor is capped by the interval itself, so a deliberately tiny
        # interval (tests, a dev loop) is honoured rather than stretched to a
        # second — while a production interval of 12s still gets its protection.
        floor = min(self.MIN_GAP, self.interval)
        return max(floor, self.interval - max(0.0, elapsed))

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.time()
            try:
                self.refresh()
            except Exception as exc:
                # A refresh must never take the poller down with it, or the
                # floor freezes on whatever it happened to be showing.
                self._last_error = repr(exc)
            # Woken early by refresh_soon() after a write.
            self._wake.wait(self.next_wait(time.time() - started))
            self._wake.clear()

    def refresh_soon(self) -> None:
        """Ask for a refresh after a write, so the operator sees their own change.

        Asynchronous when the poller is running. With no poller — tests, or a
        deployment that never called start() — a signal would go nowhere and the
        write would silently never appear, so it refreshes inline instead.
        """
        if self._thread is not None and self._thread.is_alive():
            self._wake.set()
            return
        try:
            self.refresh()
        except Exception:
            pass                # a write must not fail because a refresh did

    # ── reading ────────────────────────────────────────────────────────
    def get(self, build_if_missing: bool = True) -> dict:
        """The current snapshot.

        Nothing has been loaded yet only at boot. Rather than serve an empty
        floor, the first caller builds it — exactly one request pays that cost,
        and a burst of arrivals shares the single build.
        """
        if build_if_missing and self._snapshot is None:
            with self._build_lock:
                if self._snapshot is None:
                    self.refresh()
        with self._lock:
            snap = self._snapshot
            at = self._at
            err = self._last_error
        if snap is None:
            return {"ready": False, "machines": [], "age_seconds": None,
                    "stale": True, "error": err,
                    "labcore_online": self._online}
        age = (datetime.now() - at).total_seconds() if at else None
        out = dict(snap)
        out.update({"ready": True, "age_seconds": age,
                    # NOT `and not err`: a read that timed out behind a busy
                    # queue is stale data, not an unreachable server. `_online`
                    # is the reachability probe.
                    "labcore_online": self._online,
                    # Two missed refreshes is a real problem worth surfacing.
                    "stale": bool(err) or (age is not None
                                           and age > self.interval * 3),
                    "error": err})
        return out

    # ── refreshing ─────────────────────────────────────────────────────
    def tables(self) -> Dict[str, List[dict]]:
        """The raw rows behind the current snapshot, built lazily if needed."""
        if not self._tables:
            self.get()          # same lazy first build the floor uses
        return dict(self._tables)

    def _probe_reachable(self) -> None:
        """Ask whether LabCore is reachable at all — only after a read failed.

        Cheap (0.12s against production, and reliable even when the write queue is
        28 deep), but it is a separate call and must not run on the happy path.
        """
        try:
            self._online = bool(self.gateway.is_running())
        except Exception:
            self._online = False

    # ── schema, and what to do when LabCore will not declare it ────────
    #
    # THE BUG THIS SHAPE EXISTS TO PREVENT (2026-08-25). `ensure_schema` used to
    # set `_schema_ready = True` on its first line and wrap every CREATE in
    # `except Exception: pass`; `_migrate` did the same to its ALTERs and added
    # the column to its own `checked` set inside the try. LabCore's write queue
    # refuses past ~100 pending BY ANSWERING, never by raising, so those
    # `except` clauses caught nothing that actually happens and the flag latched
    # on work that was refused.
    #
    # The cost was silent and lasted for the life of the process. The batched
    # one-op read unions every lem_* table, so ONE missing table fails the whole
    # statement, `batched_ok` flips off, and the floor drops to fifteen reads
    # per refresh — every 12 seconds, against the very queue that was too busy
    # to take a CREATE. And a refused ALTER meant `correction` was missing from
    # lem_machine_specs, so the corrections feature read nothing, with nothing
    # anywhere to say why.
    #
    # So: confirm every statement, mark ready only when the last one landed,
    # retry what did not on the next call, and report the degraded state on
    # /healthz. Non-raising is kept deliberately — a floor drawn from the tables
    # that DO exist beats a blank one, and this runs inside `read_tables`.

    # How long a refused declaration waits before it is attempted again. The
    # floor is the snapshot interval doubled: below that the retry is issued
    # more often than the read it exists to serve, which is the storm. The
    # ceiling stops a lab that fixes LabCore waiting all afternoon for its
    # tables. LabCore's own `retry_after` overrides both when it is LONGER —
    # honouring it means never coming back sooner than it asked.
    SCHEMA_RETRY_MIN = 30.0
    SCHEMA_RETRY_MAX = 300.0

    @property
    def schema_ready(self) -> bool:
        """Has every table and column been ACKNOWLEDGED? Reported, not assumed."""
        return self._schema_ready

    @property
    def schema_checked(self) -> bool:
        """Has a declaration been attempted at all yet?

        `schema_ready is False` alone cannot tell "LabCore refused our CREATEs"
        from "this process has not looked yet", and those need different words
        on /healthz — one is a release that will show the wrong thing, the
        other is a candidate that has simply not refreshed.
        """
        return self._schema_checked

    @property
    def schema_error(self) -> str:
        """Why the schema is not known-good, or "" when it is."""
        return self._schema_error

    def ensure_schema(self) -> None:
        """Declare every table the snapshot reads, and remember what landed.

        Asks what already exists first. `CREATE TABLE IF NOT EXISTS` is harmless
        but not free — it goes through the same serialised write queue as the rest
        of the lab, roughly 1.5 ops/sec — so ten of them on every start is seven
        seconds of queue for tables that are almost always already there, and the
        tray restarts this server on every code edit. One read instead.

        Once everything is acknowledged this returns on the first line forever
        after; while anything is outstanding it re-attempts ONLY what is still
        missing, which is at most a handful of statements per refresh cycle.

        AND IT WAITS BETWEEN ATTEMPTS (2026-08-25). Removing the "mark ready
        first" latch also removed the only thing that stopped this re-issuing
        the whole DDL set on every call — and everything calls it: `read_tables`
        every 12 seconds, every audit line, every PM completion, every import.
        On a lab whose CREATEs are being refused that is dozens of writes a
        minute into a queue that is refusing BECAUSE it is full, with LabCore's
        own `retry_after` ignored. The latch was one bug and the unthrottled
        retry is its mirror image; the queue pays for both.

        So a refused round buys a cooldown: at least `SCHEMA_RETRY_MIN`,
        doubling up to `SCHEMA_RETRY_MAX`, and never shorter than the
        `retry_after` LabCore sent. Nothing about the property the latch fix
        won changes — a refused statement is still retried, and is still never
        recorded as done.
        """
        if self._schema_ready:
            # The steady state on every healthy lab, and it must stay free:
            # no clock, no read, no write.
            return
        now = self._clock()
        if self._schema_checked and now < self._schema_retry_at:
            return
        self._schema_checked = True
        self._schema_hint = None       # the longest wait this round was asked for
        existing = self._existing_tables()
        trouble = []
        for ddl in SCHEMA_DDL:
            table = ddl.split("IF NOT EXISTS", 1)[1].split("(", 1)[0].strip()
            if existing is not None and table in existing:
                continue
            refused = self._declare(ddl)
            if refused:
                trouble.append("{0}: {1}".format(table, refused))
        trouble += self._migrate(existing)
        self._schema_error = "; ".join(trouble)
        # ONLY here, and only when nothing was left outstanding.
        self._schema_ready = not trouble
        if trouble:
            self._schema_backoff = min(
                max(self._schema_backoff * 2.0, self.SCHEMA_RETRY_MIN),
                self.SCHEMA_RETRY_MAX)
            # `max`, not `or`: a hint of 4s must not shorten the back-off, and
            # a hint of ten minutes must not be shortened BY it.
            self._schema_retry_at = now + max(self._schema_backoff,
                                              self._schema_hint or 0.0)
        else:
            self._schema_backoff = 0.0
            self._schema_retry_at = 0.0

    def _declare(self, ddl: str) -> str:
        """Issue one DDL statement. Returns "" if it landed, else why not.

        Never raises: this is called from `read_tables`, and a schema problem
        must degrade the floor rather than blank it.
        """
        try:
            res = self.gateway.sql(ddl)
        except Exception as exc:                    # transport, not logic
            return "{0}: {1}".format(type(exc).__name__, exc)
        refused = refusal_of(res)
        if refused is None:
            return ""
        # Remember the longest wait LabCore asked for this round. Read through
        # `labcore_result.retry_after` rather than reached for by key, so there
        # is one place that knows what an answer carries.
        hint = retry_after(res)
        if hint is not None:
            self._schema_hint = max(self._schema_hint or 0.0, hint)
        # An ALTER re-run on a table that already has the column is the normal
        # case on every boot after the first. SQLite says "duplicate column
        # name", which means the work is DONE — treating it as outstanding
        # would keep the service permanently degraded and re-issue the ALTER
        # every refresh forever.
        if "duplicate column" in refused.lower():
            return ""
        return refused

    def _migrate(self, existing) -> List[str]:
        """Add columns to tables that predate them. Returns what did not land.

        Only when genuinely missing: a failed ALTER on every start would be a
        wasted write into a queue that serialises at roughly 1.5 ops/sec. A
        column is recorded as present only once its ALTER is ACKNOWLEDGED —
        recording it inside the try was the other half of the latch.
        """
        trouble: List[str] = []
        checked: Dict[str, set] = {}
        for table, column, ddl in SCHEMA_MIGRATIONS:
            if existing is not None and table not in existing:
                continue            # just created, so it already has the column
            if table not in checked:
                try:
                    res = self.gateway.read_sql(
                        f"SELECT name FROM pragma_table_info('{table}')")
                except Exception as exc:
                    trouble.append("{0}.{1}: could not be inspected "
                                   "({2})".format(table, column, exc))
                    continue
                if not res or res.get("error"):
                    trouble.append("{0}.{1}: could not be inspected ({2})".format(
                        table, column, (res or {}).get("error") or "no answer"))
                    continue
                checked[table] = {str(r.get("name"))
                                  for r in (res.get("rows") or [])}
            if column in checked[table]:
                continue
            refused = self._declare(ddl)
            if refused:
                trouble.append("{0}.{1}: {2}".format(table, column, refused))
                continue
            checked[table].add(column)
        return trouble

    def _existing_tables(self):
        """Shared with the config store — see labcore_gateway.existing_tables.

        None means declare everything: guessing "it probably exists" would let the
        one-op batched read fail on a missing table for the whole first cooldown.
        """
        from labcore_gateway import existing_tables
        return existing_tables(self.gateway)

    def read_tables(self) -> Dict[str, List[dict]]:
        """Every machine table, in one op if LabCore allows it.

        Returns {source: rows}. Falls back to per-table reads so an older
        LabCore costs more ops rather than showing nothing.

        The per-arm errors are published only when the rows are, which is why
        the work is done in `_read_tables`. See `table_error`.
        """
        out, errors = self._read_tables()
        self._table_errors = errors
        return out

    def _read_tables(self):
        """(tables, per-arm errors). Raises SnapshotReadError if the spine failed.

        Every verdict here comes from `labcore_result.refusal_of` (2026-08-25).
        It used to be `if not (res or {}).get("error")`, hand-rolled in the one
        file that imports the shared rule and used it only for DDL — so a
        refusal carrying no "error" key was read as a SUCCESSFUL read of zero
        rows: every arm `[]`, every error cleared, and the floor drawn as a lab
        with no equipment. That is the bug this branch exists to remove, in the
        service that draws the floor.
        """
        self.ensure_schema()
        if self.batched_ok:
            try:
                res = self.gateway.read_sql(batched_machine_sql(),
                                            timeout=READ_TIMEOUT)
            except Exception as exc:
                # A raised client error is no different from a returned one: try
                # the slow path rather than show an empty floor.
                res = {"error": "{0}: {1}".format(type(exc).__name__, exc)}
            refused = refusal_of(res)
            if refused is None:
                # Every arm gets a key, even with no rows: "read it, it was
                # empty" and "never read it" must not look identical downstream.
                out = {name: [] for name, _sql in _ARMS}
                out.update(split_batched((res or {}).get("rows") or []))
                # One statement answered, so every arm in it answered.
                return out, {}
            # Back off, but only for a while. Busy doesn't count at all: giving
            # up on the cheap path because of transient load would be backwards.
            if not (isinstance(res, dict) and res.get("busy")) \
                    and "busy" not in refused.lower():
                self.batched_ok = False
                self._batched_cooldown = BATCHED_RETRY_AFTER

        def one(name_sql):
            name, sql = name_sql
            try:
                res = self.gateway.read_sql(sql, timeout=READ_TIMEOUT)
            except Exception as exc:
                return name, [], str(exc) or "read failed"
            try:
                # missing_ok: a table nobody has created holds nothing, and that
                # is the one error a read may honestly call empty. Everything
                # else — including a refusal with no "error" key — is an arm
                # that was never read, and must not pass for one that was.
                answered = read_rows(res, missing_ok=True)
            except LabCoreUnavailable as exc:
                return name, [], str(exc)
            rows = split_batched(answered).get(name, [])
            return name, rows, ""

        out: Dict[str, List[dict]] = {name: [] for name, _sql in _ARMS}
        errors: Dict[str, str] = {}
        spine_error = ""
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(_ARMS)),
                                thread_name_prefix="lem-snap") as pool:
            for name, rows, err in pool.map(one, _ARMS):
                out[name] = rows
                if err:
                    errors[name] = err
                if name == "status" and err:
                    spine_error = err
        # Recorded even when the spine read fine. Tolerating a failed arm is
        # right for the trimmings — a missing maintenance row costs one pill —
        # but a route that answers a QUESTION out of one ("is anything overdue
        # anywhere?") has to be able to tell that it failed. See table_error().
        if spine_error:
            raise SnapshotReadError(spine_error)
        return out, errors

    def table_error(self, name: str) -> str:
        """Why one arm of the rows being served has none, or "" if it had none.

        The floor tolerates a failed arm; a route that renders an arm as an
        ANSWER must not. `/api/maintenance` answering `{"tasks": [],
        "due_count": 0}` off a maintenance read that timed out is a lab being
        told it has no PM due because a queue was busy.

        ABOUT THE ROWS BEING SERVED, which is the fix of 2026-08-25. This was
        assigned inside the read, so a read whose data was THROWN AWAY still
        left its errors behind: the spine fails, `refresh()` keeps the previous
        snapshot, the floor draws from it perfectly — and `/api/maintenance`
        and `/api/schedule` went dark over the very rows it was drawing.
        Errors and rows are published together now, so an arm is reported
        unreadable only while the caller is actually holding nothing for it.
        """
        return self._table_errors.get(name, "")

    def refresh(self) -> None:
        """Rebuild the snapshot. Keeps the previous one on failure."""
        # Asked separately: a reachable LabCore with genuinely no machines and an
        # unreachable one both produce an empty snapshot, and the floor must be
        # able to tell them apart.
        try:
            self._online = bool(self.gateway.is_running())
        except Exception:
            self._online = False
        # Reachability is asked ONLY when a read has failed. A successful read is
        # already proof LabCore is up, and a failed one is usually a busy queue
        # rather than an outage — which is why the floor kept flashing "LABCORE
        # OFFLINE" while it was plainly still updating.
        if not self.batched_ok and self._batched_cooldown > 0:
            self._batched_cooldown -= 1
            if self._batched_cooldown == 0:
                self.batched_ok = True      # the table may exist by now
        try:
            tables, errors = self._read_tables()
            built = self._builder(tables) if self._builder else {"machines": []}
        except Exception as exc:
            with self._lock:
                self._last_error = repr(exc)
            # `_table_errors` is deliberately NOT touched here. The rows the
            # readers still hold came from the last read that was kept, and its
            # errors are the ones that describe them.
            self._probe_reachable()
            self.refreshes += 1
            return
        with self._lock:
            self._snapshot = built
            self._tables = tables
            self._table_errors = errors
            self._at = datetime.now()
            self._last_error = ""
            self._online = True        # we just read from it; it is up
        self.refreshes += 1


# ── assembling the floor from raw rows ──────────────────────────────────────
# Only the FETCHING changes here. The rules — QC limits, PM/CAL status, the
# opening schedule, heartbeat freshness — are still the same objects the
# per-table path used, so a batched read can't quietly disagree with them.

def _f(row: dict, key: str, default=""):
    value = row.get(key)
    return default if value is None else value


def schedule_from_tables(tables: Dict[str, List[dict]]):
    """Snapshot rows → LabSchedule.

    Shared by the floor and by `/api/schedule`, deliberately: two readings of the
    same rows is how a lab ends up open on one page and shut on another. Note
    what this does NOT do — decide whether the lab is open *now*. That depends on
    the clock, so callers apply it at request time and a 07:00 opening is not
    announced at 07:00:12.
    """
    from lab_schedule import LabSchedule
    import json as _json

    holidays = {str(_f(r, "c1")): str(_f(r, "c2"))
                for r in tables.get("holiday") or [] if _f(r, "c1")}
    schedule = LabSchedule(holidays=holidays)
    rows = tables.get("sched") or []
    if rows:
        try:
            days = _json.loads(str(_f(rows[0], "c1")) or "null")
        except (TypeError, ValueError):
            days = None
        if isinstance(days, list) and days:
            schedule.working_days = [int(d) for d in days]
        schedule.opens = str(_f(rows[0], "c2"))
        schedule.closes = str(_f(rows[0], "c3"))
    return schedule


def maintenance_from_tables(tables: Dict[str, List[dict]]) -> Dict[str, list]:
    """Snapshot rows → {machine_uid: [MaintTaskRecord]}.

    Records, not rendered dicts: RED/YELLOW depends on today's date, and a task
    that falls due overnight has to be red in the morning without waiting for a
    refresh — let alone a restart.
    """
    from maintenance_store import MaintTaskRecord

    out: Dict[str, list] = {}
    for r in tables.get("maint") or []:
        try:
            task = MaintTaskRecord(uid=str(_f(r, "c2")),
                                   machine_uid=str(_f(r, "c1")),
                                   name=str(_f(r, "c3")),
                                   kind=str(_f(r, "c4")) or "pm",
                                   interval_days=int(float(_f(r, "c5", 30) or 30)),
                                   last_done=str(_f(r, "c6")),
                                   note=str(_f(r, "c7")))
        except (TypeError, ValueError):
            continue
        out.setdefault(task.machine_uid, []).append(task)
    return out


def events_from_tables(tables: Dict[str, List[dict]], limit: int) -> List[dict]:
    """Snapshot rows → the /api/events payload, newest first."""
    out = []
    for r in tables.get("event") or []:
        out.append({"machine_uid": str(_f(r, "c1")), "ts": str(_f(r, "c2")),
                    "kind": str(_f(r, "c3")), "lab_id": str(_f(r, "c4")),
                    "test_name": str(_f(r, "c5")), "value": str(_f(r, "c6")),
                    "detail": str(_f(r, "c7"))})
    out.sort(key=lambda e: e["ts"], reverse=True)
    return out[:limit]


def beats_from_tables(tables: Dict[str, List[dict]]) -> Dict[str, dict]:
    """machine_uid → {last_poll, watching}, for "is a parser live on this?"."""
    return {str(_f(r, "c1")): {"last_poll": str(_f(r, "c2")) or None,
                               "watching": str(_f(r, "c3"))}
            for r in tables.get("beat") or [] if _f(r, "c1")}


def titles_from_tables(tables: Dict[str, List[dict]]) -> Dict[str, str]:
    """machine_uid → title, for pages that only need the names."""
    return {str(_f(r, "c1")): (str(_f(r, "c2")) or str(_f(r, "c1")))
            for r in tables.get("status") or [] if _f(r, "c1")}


def build_machines(tables: Dict[str, List[dict]], now: datetime,
                   beat_is_fresh, status_colors: dict) -> dict:
    """Raw batched rows → the /api/machines payload."""
    from qc_specs import QcSpec

    schedule = schedule_from_tables(tables)
    closed_reason = schedule.why_closed(now)

    specs: Dict[str, list] = {}
    for r in tables.get("spec") or []:
        try:
            spec = QcSpec(machine_uid=str(_f(r, "c1")),
                          test_name=str(_f(r, "c2")),
                          sample_id=str(_f(r, "c3")),
                          expected=float(_f(r, "c4", 0) or 0),
                          std_dev=float(_f(r, "c5", 0) or 0),
                          k=float(_f(r, "c6", 2) or 2),
                          units=str(_f(r, "c7")))
        except (TypeError, ValueError):
            continue
        specs.setdefault(spec.machine_uid, []).append(spec)

    positions = {}
    for r in tables.get("layout") or []:
        try:
            positions[str(_f(r, "c1"))] = (float(_f(r, "c2", 0) or 0),
                                           float(_f(r, "c3", 0) or 0))
        except (TypeError, ValueError):
            continue

    targets: Dict[str, list] = {}
    for r in tables.get("target") or []:
        targets.setdefault(str(_f(r, "c1")), []).append(
            {"sample": str(_f(r, "c2")), "test": str(_f(r, "c3"))})

    def _num(raw):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    effective: Dict[str, list] = {}
    for r in tables.get("espec") or []:
        at, _sep, flag = str(_f(r, "c9")).partition("|")
        effective.setdefault(str(_f(r, "c1")), []).append({
            "test_name": str(_f(r, "c2")),
            "low": _num(_f(r, "c3")), "high": _num(_f(r, "c4")),
            "expected": _num(_f(r, "c5")), "units": str(_f(r, "c6")),
            "sample_id": str(_f(r, "c7")),
            "last_qc_value": _num(str(_f(r, "c8")).split("~")[0]),
            "correction": _num(str(_f(r, "c8")).partition("~")[2]) or 0.0,
            "last_qc_at": at,
            # Three states, not two: in spec, out of spec, or never measured.
            "last_qc_in_spec": (None if flag.strip() == ""
                                else bool(int(float(flag)))),
        })

    subs = {str(_f(r, "c1")): {"qc": str(_f(r, "c2")) or "UNKNOWN",
                               "pm": str(_f(r, "c3")) or "UNKNOWN",
                               "calibration": str(_f(r, "c4")) or "UNKNOWN"}
            for r in tables.get("sub") or []}
    beats = {str(_f(r, "c1")): {"last_poll": str(_f(r, "c2")) or None,
                                "watching": str(_f(r, "c3"))}
             for r in tables.get("beat") or []}
    activity = {str(_f(r, "c1")): str(_f(r, "c2"))
                for r in tables.get("activity") or [] if _f(r, "c2")}

    maint = maintenance_from_tables(tables)

    blank = {"qc": "UNKNOWN", "pm": "UNKNOWN", "calibration": "UNKNOWN"}
    machines = []
    for r in tables.get("status") or []:
        uid = str(_f(r, "c1"))
        status = str(_f(r, "c3")) or "UNKNOWN"
        beat = beats.get(uid) or {}
        entry = {
            "machine_uid": uid,
            "title": str(_f(r, "c2")) or uid,
            "status": status,
            "status_color": status_colors.get(status, status_colors["UNKNOWN"]),
            "reason": str(_f(r, "c4")),
            "updated_at": str(_f(r, "c5")),
            "qc_specs": [s.to_dict() for s in specs.get(uid, [])],
            "sub_statuses": subs.get(uid, dict(blank)),
            "last_activity": activity.get(uid) or str(_f(r, "c5")),
            "last_poll": beat.get("last_poll"),
            "watching": beat.get("watching", ""),
            "qc_targets": targets.get(uid, []),
            "effective_specs": effective.get(uid, []),
        }
        # The same three-way honesty as before: never-beat is `unknown`, a shut
        # lab is `closed`, and only a module that WAS beating can be `stopped`.
        if (beat_is_fresh(beat.get("last_poll"))
                or beat_is_fresh(entry["last_activity"])):
            state = "running"
        elif not beat.get("last_poll"):
            state = "unknown"
        elif closed_reason:
            state = "closed"
        else:
            state = "stopped"
        entry["module_state"] = state
        entry["module_running"] = state == "running"
        entry["closed_reason"] = closed_reason if state == "closed" else ""
        tasks = [t.to_dict(now.date()) for t in maint.get(uid, [])]
        entry["maintenance"] = tasks
        entry["maintenance_due"] = sum(
            1 for t in tasks if t["status"] in ("RED", "YELLOW"))
        pos = positions.get(uid)
        entry["pos"] = [pos[0], pos[1]] if pos else None
        machines.append(entry)

    # Ordered by the instrument, NOT by when it last reported. Instruments report
    # every ~40s, so an `updated_at` sort churned the payload constantly — and
    # anything downstream that keys off array order churned with it: the rail list
    # reordered, and the map's painter sort (which ties on gx+gy) flipped
    # overlapping machines on top of each other every single refresh.
    # uid is the tiebreak because titles are not unique — a duplicated config can
    # share one, and two machines swapping places is exactly the flicker being
    # fixed here. Recency is still on every machine for the feed and "ago" stamps.
    machines.sort(key=lambda m: (m["title"].lower(), m["machine_uid"]))
    return {"machines": machines, "closed_reason": closed_reason}
