# Live push channel — the floor stops waiting on the queue

**Date:** 2026-08-05
**Status:** design approved, not yet implemented
**Touches:** `LEM Web Server/` (new `live_presence.py`, `web_app.py`,
`templates/floor.html`), `LEM Station Module/lem_station_module.py`

Ryan, 2026-08-05: *"the polling rate is really slow … Can't the Parsers
communicate directly with the webapp for certain things? Like being online, or
equipment status, when it parses something."*

Yes. And the measurement below says half the problem is somewhere else entirely.

---

## 1. Where the time actually goes

A status change today travels four stages before a screen shows it:

| stage | cost | notes |
|---|---|---|
| module's own poll of the bench | its interval | the floor of what is knowable |
| LabCore write queue | 0.1–11.8 ops/sec, bursty | measured 81 pending during the "offline" incident |
| snapshot refresh | up to 12s | `LEM_SNAPSHOT_SECONDS`, `web_app.py:397` |
| **floor's browser poll** | **up to 30s** | `templates/floor.html:2484` |

The browser poll is the largest fixed chunk, and it is **free to shorten**:
`/api/machines` and `/api/events` are served from the in-memory snapshot in under
2ms at zero LabCore ops — that was the point of the 2026-08-03 performance work.
The timer was simply never lowered afterwards. `pollRuns` (the run blips) is
already 6s (`floor.html:2475`).

Liveness is worse than it looks. "Is a module running" is inferred from the age
of `lem_machine_heartbeat`, which the module writes every `HEARTBEAT_SECONDS =
300` (`lem_station_module.py:1527`) — through the same queue. The floor infers
presence from a five-minute write.

## 2. Decision

Two separable changes, in this order.

**Step 1 — shorten the browser poll.** No new architecture, no LabCore cost.
Removes up to 28 seconds of the current lag on its own.

**Step 2 — a direct module → server channel.** Removes the queue and the
snapshot from the path for the small, live facts. This is the part step 1 cannot
do: freshness that does not depend on how backed up LabCore's queue is.

### Decisions recorded during design

| question | answer |
|---|---|
| Can benches reach the web server? | Yes — same LAN. Server IP is fixed; benches move between PCs |
| How does the update reach the browser? | Faster polling, not SSE. Revisit only if ~2s proves not enough |
| Auth on the push endpoint | Shared token, distributed automatically. No operator input |
| Floor behaviour when the direct road is silent | Fall back to the record, flagged as such |

V4 did live sync with SSE (`web_server.pyw`, checklists) so the lab has seen that
pattern before; V5 chooses polling here because the server already answers from
memory and an open connection per screen is a bigger operational commitment than
the problem currently justifies.

---

## 3. Step 1 — the browser poll

`templates/floor.html`:

- `load()` — 30s → **2s** (keeping the existing `if (!dragging)` guard)
- `pollRuns()` — 6s → **2s**

Cost is Flask requests against memory: five open screens ≈ 5 req/s, each under
2ms, zero LabCore ops. Periodic refreshes already use `LEM.fresh()` rather than
the session cache, so this does not reintroduce the "permanently a cycle behind"
bug.

**Guard:** a test asserts the floor's status interval stays ≤ 5s, so it cannot
quietly drift back to 30.

---

## 4. Step 2 — two roads, separate facts

The rejected design had both roads carrying **the same fact** at different
speeds, reconciled by a newest-wins rule. Precedence rules like that rot: a year
later someone changes one side and the floor shows a status LabCore never held,
with nothing to say which source lied.

Instead the roads carry **different facts**:

```
bench module ──► LabCore .................. the RECORD
             │    results, QC verdicts, history, specs, corrections,
             │    maintenance, checklists — facts about the lab
             │
             └─► POST /api/live ........... LIVENESS
                  I am running · my status is now X · I just parsed L-1234
                  — facts about the module, which only the module knows
```

The floor's dots stop being derived from `lem_machine_heartbeat` age. Asking the
module directly is better information, not merely faster: today's "online" is
inferred from a write that had to survive the queue.

This mirrors an existing pattern rather than inventing one — `lem_machine_control`
(`qc_specs.py:48`) is already an out-of-band **server → module** channel, written
by the floor and polled by modules. This is the same idea in the other direction.

### The failover rule

One source supplies a value at any moment, chosen by one deterministic rule:

> **Live entry if one is fresh; otherwise the record, flagged `live: false` with
> its age.**

Precisely: "the record" is the machine's `lem_machine_status` row as the snapshot
already holds it, and "its age" is that row's `updated_at`. The floor's dot takes
its liveness from the live road only — never from `lem_machine_heartbeat` age —
while `/api/machine-configs` keeps judging "is a parser on this config" from
heartbeats as it does today, because the picker and the delete guard need an
answer that survives a server restart.

**TTL is per machine**, not fixed: `max(90s, 2.5 × interval_seconds)`, capped at
20 minutes. A bench polling every 15s expires in 90s; one on 5 minutes gets 12½
minutes, so it stays live between its own polls instead of flapping. The cap
stops a bogus `interval_seconds` pinning a dead bench as live forever. A missing
`interval_seconds` falls back to 90s.

That is a failover, not a merge — no field is ever assembled from both. It exists
because pure separation has a specific failure mode: a server restart, a switch
reboot, or one bench losing its path to the server would blank that machine on
the floor while LabCore holds a perfectly good status the module wrote seconds
ago. Today that case degrades to "slower but correct", and it should keep doing
so.

Consequences, all deliberate:

- Nothing pushed is ever persisted, and the server never writes a pushed value
  back to LabCore — the module already wrote it.
- Entries expire at their TTL, so a bench that dies goes stale on its own rather
  than being remembered as online forever.
- A server restart starts with an empty overlay; the floor behaves exactly as it
  does today until benches check in.
- The push is a pure accelerator. Nothing in either program depends on it
  succeeding.

---

## 5. Protocol

`POST /api/live` → `204 No Content`.

```json
{"machine_uid": "pac-flash-2",
 "status": "GREEN",
 "reason": "",
 "at": "2026-08-05T14:02:11",
 "interval_seconds": 30,
 "last_parse_at": "2026-08-05T14:02:10",
 "lab_id": "L-1234"}
```

- Token in an `X-LEM-Token` header, compared with `hmac.compare_digest`, never
  logged. Missing or wrong → `401`.
- Malformed body or missing `machine_uid` → `400`.
- Unknown `machine_uid` → accepted and stored; the floor only draws machines it
  knows, so an unknown one is inert rather than an error the bench must handle.
- `at` is the module's own timestamp, used to discard an out-of-order POST rather
  than letting a delayed one overwrite a newer state.
- `interval_seconds` is the bench's poll interval, which sizes that machine's TTL
  (§4). Without it a fixed TTL makes the dot **flap**: the module offers 15s, 30s,
  60s and **5 min** (`lem_station_module.py:2283`), so a bench on 5 minutes would
  read live for 90s and from-record for the remaining 3½ — every cycle, visibly.
- `last_parse_at` + `lab_id` let the floor spawn a run blip immediately. The key
  `machine_uid|ts|lab_id` is the one `floor.html` already dedupes on, so the same
  run arriving later through LabCore does not blip twice.

### Config discovery — zero operator input

1. Server boot: `LEM_LIVE_TOKEN` from the environment, or generate one with
   `secrets.token_urlsafe(32)` on first boot and keep it.
2. Server writes `live_url` and `live_token` into `lem_meta`
   (`db_config_store.py:34`) — two writes, once per boot.
3. Module reads those two keys from LabCore, which it already talks to, and
   caches them. Nothing typed, no settings dialog, no per-PC file.
4. A bench that moves to another PC reads them again on startup. Re-read is also
   triggered after repeated push failures, so a changed server IP or a rotated
   token self-heals.

**What the token is worth:** it lives in `lem_meta`, so anything that can read
LabCore can read it. It stops a random host on the lab LAN — or a test script
with a typo'd `machine_uid` — from repainting the floor. It does **not** stop
someone who already has LabCore access. Resisting that would need a secret shared
only between the server and the LabStation PCs, deployed per machine, which
breaks the zero-touch property that made this acceptable. Accepted deliberately:
the pushed data is non-authoritative and expires on its own within minutes.

---

## 6. Server side

New `live_presence.py` — deliberately not a database of anything; it dies with
the process.

```
LivePresence
  record(uid, payload)   # ignores an `at` older than the stored one
  get(uid) -> entry|None # None once past that machine's TTL
  all() -> dict
  TTL per machine = max(90s, 2.5 × interval_seconds), capped at 20 min
  cap = 256 machines, thread-safe (the Flask dev server is threaded, same
  requirement as FakeLabCoreGateway)
```

Endpoints touched:

- `POST /api/live` — new.
- `/api/machines` — live entry if fresh, else the snapshot's record; each machine
  carries `live: true|false` and an age so the floor can dim a from-record dot.
- `/api/events` — a live parse becomes a blip without waiting for
  `lem_machine_log`.

**The push path never touches LabCore** — no read, no write, and specifically no
`snapshot_service.refresh_soon()`. Waking the snapshot on every ping would
rebuild "LabCore load scales with how many benches are running", which is the
same bug that was fixed for screens on 2026-08-03. A test asserts the gateway
sees zero calls during a push.

## 7. Module side

One POST at the end of `_process_outcome`, on the **worker** thread — where all
LabCore HTTP already happens, per the threading model in `CLAUDE.md`.

- stdlib `urllib.request`, 1.5s timeout. No pip dependency (LabStation bundles
  no `requests` for us to rely on).
- Wrapped so it can never raise: a worker exception strands `_polling`, because
  LabStation's `_run_in_thread` drops the callback on error. Same swallow-and-
  continue shape as `_push_batch_in_background`.
- No URL configured, unreachable, or refused → skip silently.
- One LAN POST per poll per bench. Negligible next to the LabCore traffic already
  in that function.

## 8. Failure modes

| what fails | what happens |
|---|---|
| Web server down | Module's push times out in 1.5s and is dropped. Poll unaffected. Floor unaffected — it is down too |
| Bench loses its path to the server | Live entry expires at its TTL; that machine falls back to the record, dimmed |
| LabCore down | Unchanged from today: the module keeps data locally. Live road still shows current status — strictly better than today |
| Token rotated | Pushes 401 until the module's re-read; floor falls back to the record meanwhile |
| Out-of-order POST | Discarded by the `at` comparison |
| Clock skew on a bench | Its `at` may be rejected as stale. Mitigation: compare only against that machine's own previous `at`, never against server time |
| Push storm / bad script | Token check rejects it; storage is capped at 256 machines |

## 9. Testing

Written first, as always.

**Server**

- token required; wrong token → 401; malformed body → 400
- a valid push is stored and served on `/api/machines`
- entry expires at TTL and the machine falls back to the record
- a bench on the 5-minute interval stays live between its own polls (no flap);
  TTL is capped so a bogus `interval_seconds` cannot pin a dead bench as live
- `/api/machines` prefers a fresh live entry over the record
- an out-of-order `at` is discarded
- **a push makes zero gateway calls** (no read, no write, no `refresh_soon`)
- concurrent pushes from several threads are safe
- restart (fresh `LivePresence`) serves the record, not blanks

**Module**

- payload built from the evaluation it just produced
- one post per completed poll
- no config in `lem_meta` → no post attempted
- server down / slow → poll completes, no exception escapes the worker
- config cached; re-read after repeated failures
- structural: the push is issued from the worker half, not `_show_outcome`

**Floor**

- guard: the status refresh interval stays ≤ 5s

## 10. Deployment notes

- The server must bind the LAN interface (`web_server.pyw --host 0.0.0.0`), not
  `127.0.0.1`, and the benches need that port open to it.
- `LEM_LIVE_TOKEN` is optional; unset means the server generates and stores one.

## 11. Explicitly out of scope

- SSE or WebSockets to the browser (revisit only if ~2s is not enough)
- Per-machine tokens
- Persisting live state anywhere
- Sending results or any record data over the direct road — the record goes to
  LabCore, unchanged
- Lowering `LEM_SNAPSHOT_SECONDS`: unlike the browser poll it is a real LabCore
  read per cycle, and reads queue behind every write in the lab. Push instead of
  polling LabCore harder.
