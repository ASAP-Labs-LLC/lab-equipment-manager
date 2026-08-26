# LabCore: reads contend with writes — root cause and proposed fixes

**For the LabCore / LabLink dev team.** Written 2026-08-26 by the LEM team.

Findings come from reading `apps/LabCore/src/LabCore.py` in the LabLink monorepo,
not from probing endpoints. (An earlier note of ours claimed "no read endpoint
exists — probed, all 404". That was wrong; see [§7](#7-corrections-to-our-own-notes).)

---

## TL;DR

LabCore serialises **reads** through the **write** queue. That is correct given
where the database lives, but it means every client read consumes a write slot,
and a single slow query stalls writes for the whole lab.

**We are not asking you to move the database or migrate off SQLite.** We are
asking for a **read path that does not touch the file being written to** — a
local read replica inside LabCore ([§5, P1](#p1--serve-reads-from-a-local-replica--recommended)).

We are separately fixing the thing on *our* side that makes our reads slow in the
first place ([§4](#4-what-were-fixing-on-our-side)) — a missing index on a table
we create. You should not have to absorb that.

---

## 1. Root cause

```
SQLite database lives on an SMB share (shared across machines — cannot move)
        │
        ▼
WAL is unusable on a share (needs shared-memory locking; can corrupt the file)
        │
        ▼
Journal mode is DELETE
        │
        ▼
A concurrent reader BLOCKS the writer's commit; a writer in PENDING then blocks
every new reader → all of LabLink freezes
        │
        ▼
Therefore reads MUST serialise through the write queue
        │
        ▼
Every read costs a write slot · load scales with clients × read frequency ·
ONE slow read stalls every write in the lab
```

The leverage point is the third box. Everything else follows from it, and it is
the only link that can be broken without moving the database.

---

## 2. The evidence

`LabCore.py:13180-13204`, in `_handle_queue_write` — your own comment states the
constraint exactly:

```python
# read_sql skips the write queue ONLY when the DB is on a local
# drive: there WAL is enabled and a reader can't block (or be
# blocked by) the writer, so reads stay fast while the queue is
# deep and are never rejected by backpressure.
#
# On a network share WAL is disabled (DELETE journal), and a
# concurrent reader DOES block the writer's commit — worse, a
# writer stuck in PENDING then blocks every new reader, freezing
# all of LabLink at once. There, reads must serialize through the
# queue like any other operation so readers and the writer can
# never collide.
if operation == "read_sql":
    db = _get_lv_db()
    if db_on_local_drive(db):
        result = _wop_read_sql(db, params)      # direct — no queue, no backpressure
        return
    if self._reject_if_busy(): return
    result = _write_queue.submit_and_wait(...)  # serialised behind every write
```

`db_on_local_drive` (`LabCore.py:1057`) returns **False** for any UNC path (`\\…`
/ `//…`) and for any drive where `GetDriveTypeW == DRIVE_REMOTE`. Our deployment
takes the `else` branch permanently.

Measured on the live system by us, recorded in `LEM Web Server/snapshot_service.py`:

> *"while six modules were publishing: `pending: 28`, and the batched read timed
> out at exactly 8.00s four times in six — while the query itself, run when the
> queue was clear, took **0.12s** for 103 rows."*

Same query, 0.12s clear vs killed at 8s under load. The read was never slow; it
was queued.

---

## 3. Why it degrades to an outage rather than to slowness

`_wop_read_sql` (`LabCore.py:7984`) runs a watchdog that **interrupts** any read
over `read_watchdog_s` (default **8.0s**):

```python
# Watchdog: reads share the single write-queue worker, so a runaway
# SELECT (e.g. an unindexed scan over the SMB share) would block every
# queued write behind it. Interrupt any read that outruns this cap ...
```

The watchdog is the right call — without it one bad query freezes the lab. But
combined with the serialisation it produces a cliff: as data grows, a read
crosses 8s and starts being killed outright. Clients see failures, retry, and
deepen the queue. It presents to users as **"LabCore is offline"** when LabCore is
perfectly healthy.

---

## 4. What we're fixing on our side

Listing this so you know what is *not* being asked of you. All of it is in tables
LEM creates, so it is ours to fix.

**`lem_machine_log` has no primary key and no index.** Every other `lem_*` table
has a sensible key; this one has none, is never pruned, and is the container every
run, QC verdict, status change, comment and PM tick lands in. Three queries scan
it, two of them **every 12 seconds, forever**:

| Where | Query | Frequency |
|---|---|---|
| our snapshot, `event` arm | `… ORDER BY ts DESC LIMIT n` | every 12s |
| our snapshot, `activity` arm | `… GROUP BY machine_uid` | every 12s |
| the station module | `WHERE machine_uid = ? AND kind = 'qc' AND TRIM(test_name) != '' ORDER BY ts DESC LIMIT 400` | per bench |

That is the unindexed SMB scan your watchdog comment warns about, and it is our
doing. **Fixed 2026-08-26** — we added:

```sql
CREATE INDEX IF NOT EXISTS idx_lem_log_ts   ON lem_machine_log(ts DESC);
CREATE INDEX IF NOT EXISTS idx_lem_log_uid_kind_ts
       ON lem_machine_log(machine_uid, kind, ts DESC);
CREATE INDEX IF NOT EXISTS idx_lem_maint_machine ON lem_maintenance(machine_uid);
```

Measured on 100k rows, local SSD, plans identical with and without `ANALYZE`:

| Query | Before | After |
|---|---|---|
| snapshot `event` arm | `SCAN` + temp b-tree — **23.99 ms** | index walk — **0.04 ms** |
| snapshot `activity` arm | `SCAN` + temp b-tree — **16.51 ms** | covering index — **3.22 ms** |
| module `LAST_QC_QUERY` | `SCAN` + temp b-tree — **2.56 ms** | `SEARCH` — **0.18 ms** |

Over SMB, on a table that keeps growing, those "before" numbers are the ones that
were heading for your 8s watchdog. The `activity` arm still reports `SCAN` and
that is correct — `MAX(ts) GROUP BY machine_uid` must consider every entry; what
the index removes is fetching the 7-column row off the share for each one.

Still to do on our side: a retention/archive rule for the log (`kind='run'` and
`kind='qc'` are ISO/IEC 17025 §7.5.1 records and must be archived rather than
deleted; `status_change` is operational noise). We are measuring the table first.

> **Correction to an earlier draft of this document.** It claimed
> `TRIM(test_name) != ''` was "non-sargable — it forces a scan even with an
> index". That is wrong, and we only found out by measuring. `test_name` is not
> a column in either index, so the predicate is a row filter either way and the
> planner produces the same `SEARCH … (machine_uid=? AND kind=?)` plan with or
> without it — 2.56 ms vs 2.51 ms before the index, 0.18 vs 0.17 after. We did
> rewrite it (one fewer function call per candidate row, clearer intent), but
> **the index is what makes this fast, not the predicate.** Flagging it because
> "make your predicates sargable" is exactly the sort of advice that gets
> repeated without measurement — as we just demonstrated.

We have also **cut our own read volume by ~92%**: bench configuration is now
served from an in-memory snapshot in the LEM web server (co-located with LabCore),
so LabCore sees **one op per 12s regardless of how many benches exist**, instead
of a fixed per-bench cost. That is exactly the pattern proposed in P1/P2 below —
we built it in the wrong process because LabCore doesn't offer it, and it only
helps LEM. LabEntry and LabStation still pay full price.

---

## 5. Proposed fixes for LabCore

Ranked by value per unit of effort.

### P1 — Serve reads from a local replica *(recommended)*

**The idea.** Keep the authoritative database on the share exactly as it is. Copy
it periodically to a **local disk on the LabCore host**, and serve reads from that
copy. The reader then touches a different file, on a local drive, so it can be
opened WAL/read-only and **cannot block the writer** — link 3 of the causal chain
is gone, without moving anything the lab depends on.

**How to take the copy safely.** Do it *from the write-queue worker*, as a queued
operation. Then it takes its turn like any write and never contends:

- `VACUUM INTO '<local>/labcore-read.db'` gives a consistent point-in-time copy in
  one statement, and compacts as a side effect.
- Or `sqlite3.Connection.backup()` (the online backup API) if you want it
  incremental. Note it restarts the copy if the source is written mid-copy, which
  can livelock on a busy DB — running it from the queue worker avoids that,
  because nothing else writes while it holds its turn.

**Cost.** One queue slot per refresh interval. At 30s that is **2 ops/min,
constant**, replacing potentially hundreds of client read slots. It is the same
trade LEM's snapshot already makes and it measured well: our floor went from
~34 ops/min per screen to 0.

**Routing.** `read_sql` and every GET endpoint read the replica. Report the
replica's age in the response (`X-LabCore-Data-Age` or a JSON field) so callers
can judge staleness rather than guess.

**The one real caveat: read-your-writes.** A client that writes and immediately
reads back will not see its own write until the next refresh. That matters for a
minority of paths, so keep an explicit escape hatch — `read_sql` with
`{"fresh": true}` routes through the queue against the authoritative file exactly
as today. Default fast and slightly stale; opt in to authoritative.

**Why we think this is the right answer:** it removes the entire class of problem
for *every* LabLink app, needs no schema change, no migration, no change to where
the data lives, and no client changes for the default path.

### P2 — Cached read endpoints for `lem_*`

LabCore already proves this pattern for its own domain (`/api/results`,
`/api/limits`, `/api/samples` … all read directly, none through `_write_queue`).
There is no equivalent for any `lem_*` table, so LEM's configuration is reachable
only via raw `read_sql` — a queue op.

A `GET /api/lem/snapshot` (or per-table endpoints) served from memory would let us
delete the workaround described in §4 and would help any future LabLink client
that touches these tables.

Largely subsumed by P1; worth doing separately only if P1 is deferred.

### P3 — A `read_many` operation

Let one queue slot carry N queries and return N result sets. We already
hand-roll this with `UNION ALL`, which is fragile: every arm must have the same
column count and explicit aliases, or the whole statement fails and takes every
table with it — we have shipped that bug to production twice. A first-class
`read_many` would be cheap for you and would remove a footgun for every client.

### P4 — Log slow reads with their source *(cheapest useful thing here)*

Today the watchdog kills a slow read and the caller sees a generic failure.
Nobody learns *which* query, from *which* app, took how long. Since every request
already carries `source`, logging interrupted and near-threshold reads
(`source`, first ~120 chars of SQL, duration, queue depth at the time) into the
existing `_write_log` and exposing them on `/api/queue/log` would have surfaced
our `lem_machine_log` scan immediately instead of after a code audit.

This is a few lines and it converts invisible degradation into a metric.

### P5 — Fair queuing per source

One misbehaving client can currently fill the queue and starve everyone. Even
crude per-`source` round-robin or a per-source cap on pending work would contain
the blast radius, and would have made our own regression a LEM problem instead of
a lab problem.

### P6 — A real client/server database *(long term)*

Postgres or similar removes the single-writer constraint entirely and makes all
of the above unnecessary. The LabLink README already says "Postgres" while the
code is SQLite. Big migration; noted for completeness, not proposed for now.

### ⚠ The trap — do not simply let reads run concurrently

The obvious-looking fix is "give reads their own connection pool so they stop
queueing behind writes." **On a share this reintroduces exactly the deadlock your
comment describes**: with `DELETE` journalling, a reader's SHARED lock blocks the
writer's EXCLUSIVE lock, and a writer in `PENDING` blocks new readers.
`immutable=1` does not rescue it either — the file demonstrably does change, and
lying to SQLite about that risks returning corrupt pages.

Concurrent reads are safe **only** once they are pointed at a different file on a
local disk. That is P1, and it is why P1 is ranked first rather than a read pool.

---

## 6. Client bug in the vendored `labcore_client.py`

`get_test_names()` reads `data.get("test_names", [])`, but LabCore serves
`{"tests": [...]}`. It therefore **always returns `[]`**.

- `apps/LabStation/src/labcore_client.py:349` — wrong
- `apps/LabEntry/src/labcore_client.py:233` — wrong
- `apps/LabOut-Server/src/labcore_client.py:328` — **correct**

Reading the right key returns 282 names in 0.3s. Our copy is vendored from
LabStation, so it inherited the bug; we are not hand-editing the vendored file and
would rather it were fixed upstream and re-synced.

Related, same file: the fallback (`SELECT DISTINCT test_name FROM sample_tests`)
scans 342k+ rows and blows the client's read timeout, so it also returns `[]`. It
needs a generous explicit timeout, and the result is worth caching.

---

## 7. Corrections to our own notes

Recorded here because we propagated these internally and they may have reached you.

- **"No read endpoint exists — probed, all 404" is wrong.** `/api/read`,
  `/api/query` and `/api/sql` don't exist, but roughly twenty GET endpoints do
  (`LabCore.py:11475`), and none goes through `_write_queue`. They just don't
  cover `lem_*` tables. We had probed guessed paths instead of reading the source.
- **"Reads are capped at the write queue's ~1.5 ops/sec" is imprecise.** Reads run
  at ~102ms each when the queue is clear. They are not rate-limited; they are
  *blocked* behind write backlog. Same outcome under load, different mechanism.

---

## 8. Useful things we found that may not be widely known

- **`set_queue_tuning` is a live relief valve** (`LabCore.py:13161`) — handled
  before the busy check and off the queue, deliberately. It adjusts
  `busy_threshold` (default 100, clamp 10–10000), `read_watchdog_s` (8.0, 2–120),
  `write_wait_s` (30.0, 5–600) and `callback_wait_s` with no restart. Extremely
  useful mid-incident; we suspect most operators don't know it exists.
- **`/api/station/heartbeat` never touches the queue or the DB** (`LabCore.py:13120`)
  — ephemeral in-memory presence. Worth advertising to app authors who are
  currently writing their own heartbeat rows through the queue (we are one).
- **`GET /api/queue/status`** reports `pending`, `busy`, `current_op` and
  `current_op_seconds` with no session required. That is the number to alert on.

---

## Summary

| # | Action | Owner | Effort | Payoff |
|---|---|---|---|---|
| **P1** | **Local read replica; reads served from it, `fresh:true` to opt out** | **LabCore** | **medium** | **removes the root cause for every app** |
| P4 | Log slow/interrupted reads with `source` + SQL + duration | LabCore | tiny | makes this class of problem visible |
| P3 | `read_many` op | LabCore | small | removes a real client footgun |
| P5 | Fair queuing per source | LabCore | small | contains blast radius |
| P2 | Cached `lem_*` read endpoints | LabCore | medium | subsumed by P1 |
| P6 | Client/server database | LabCore | large | long-term |
| ✅ | Index `lem_machine_log` + `lem_maintenance` — **done 2026-08-26** | LEM (us) | small | 24ms → 0.04ms on the 12s query |
| — | Retention/archive rule for `lem_machine_log` (measuring first) | **LEM (us)** | small | keeps the above true as it grows |
| — | Fix `get_test_names` key + timeout, re-sync vendored client | LabCore | trivial | assay picker works |

Happy to pair on P1 — we've already built the same pattern once in the LEM web
server and can share what the refresh interval and staleness reporting need to
look like in practice.
