# Live Push Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the floor show a bench's status, liveness and parse activity within
about two seconds, instead of waiting on LabCore's write queue plus a 12s snapshot
plus a 30s browser poll.

**Architecture:** Two roads carrying different facts. LabCore keeps the record
(results, QC, history). A new direct road carries facts only the module knows —
I'm running, my status is now X, I just parsed L-1234 — into an in-memory store on
the web server, read by `/api/machines` and `/api/events`, with the LabCore record
as failover. Nothing pushed is persisted and the push path never touches LabCore.

**Tech Stack:** Flask (web server, `.venv`), PySide6 + stdlib only (station
module, `venv`), pytest both sides. Spec:
`docs/superpowers/specs/2026-08-05-live-push-channel-design.md`.

**Not a git repo** — the "commit" step of each task is replaced by "run the full
suite for that side and confirm green".

---

## File structure

| file | responsibility |
|---|---|
| `LEM Web Server/live_presence.py` | **new.** The store: TTL per machine, out-of-order rejection, cap, thread-safety. Plus token resolution and the `lem_meta` publish. No Flask, no LabCore. |
| `LEM Web Server/web_app.py` | `POST /api/live`, and the failover read in `/api/machines` + `/api/events`. |
| `LEM Web Server/web_server.pyw` | Boot-time publish of url + token to `lem_meta` (never in `create_app` — that would give every test a LabCore write). |
| `LEM Web Server/templates/floor.html` | Poll timers; `live` shown in the hover card. |
| `LEM Station Module/lem_station_module.py` | Pure helpers (`parse_live_config`, `build_live_payload`, `post_live`) + the worker-side push in `_process_outcome`. |

Tests: `LEM Web Server/tests/test_live_presence.py`, `tests/test_live_endpoint.py`,
`tests/test_floor_poll_interval.py`, `LEM Station Module/tests/test_live_push.py`.

Commands:
- web: `cd "LEM Web Server" && .venv/bin/python -m pytest tests/ -q`
- module: `cd "LEM Station Module" && ../venv/bin/python -m pytest tests/ -q`

---

## Task 1: The presence store

**Files:**
- Create: `LEM Web Server/live_presence.py`
- Test: `LEM Web Server/tests/test_live_presence.py`

- [ ] **Step 1: Write the failing tests**

```python
from live_presence import LivePresence, ttl_for

class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t
    def advance(self, s): self.t += s

def test_a_pushed_status_is_readable():
    p = LivePresence()
    p.record("m1", {"status": "GREEN", "at": "2026-08-05T14:00:00"})
    assert p.get("m1")["status"] == "GREEN"

def test_an_entry_expires_at_its_ttl():
    clock = Clock()
    p = LivePresence(clock=clock)
    p.record("m1", {"status": "GREEN", "at": "2026-08-05T14:00:00"})
    clock.advance(89)
    assert p.get("m1") is not None
    clock.advance(2)
    assert p.get("m1") is None

def test_a_slow_bench_stays_live_between_its_own_polls():
    assert ttl_for(300) == 750.0      # 5-minute bench: 12.5 minutes
    assert ttl_for(15) == 90.0        # fast bench: the floor
    assert ttl_for(100000) == 1200.0  # capped
    assert ttl_for(None) == 90.0

def test_an_out_of_order_push_is_discarded():
    p = LivePresence()
    p.record("m1", {"status": "RED", "at": "2026-08-05T14:05:00"})
    p.record("m1", {"status": "GREEN", "at": "2026-08-05T14:00:00"})
    assert p.get("m1")["status"] == "RED"

def test_the_store_is_capped():
    p = LivePresence()
    for i in range(300):
        p.record(f"m{i}", {"status": "GREEN", "at": "2026-08-05T14:00:00"})
    assert len(p.all()) <= 256
```

- [ ] **Step 2: Run and watch them fail** — `ModuleNotFoundError: live_presence`

- [ ] **Step 3: Implement `live_presence.py`** — `ttl_for`, `LivePresence`
  (`record`/`get`/`all`), `threading.Lock`, `time.monotonic` default clock,
  ISO `at` comparison with a string fallback, FIFO eviction at the cap.

- [ ] **Step 4: Run — all pass.**

- [ ] **Step 5: Full web suite green.**

---

## Task 2: Token resolution and `lem_meta` publish

**Files:**
- Modify: `LEM Web Server/live_presence.py`
- Test: `LEM Web Server/tests/test_live_presence.py`

- [ ] **Step 1: Failing tests**

```python
def test_an_explicit_token_is_used_as_given():
    assert resolve_token("from-env") == "from-env"

def test_a_missing_token_is_generated_not_blank():
    a, b = resolve_token(None), resolve_token("")
    assert len(a) >= 32 and a != b

def test_publishing_writes_both_keys(gw):     # FakeLabCoreGateway
    publish_live_config(gw, "http://10.0.0.5:5557", "tok")
    rows = gw.read_sql("SELECT key, value FROM lem_meta")["rows"]
    assert {r["key"]: r["value"] for r in rows} == {
        "live_url": "http://10.0.0.5:5557", "live_token": "tok"}

def test_publishing_never_raises_when_labcore_refuses():
    class Dead:
        def sql(self, *a, **k): raise RuntimeError("queue full")
    publish_live_config(Dead(), "http://x", "t")   # must not raise
```

- [ ] **Step 2: Run — fail.**
- [ ] **Step 3: Implement** `LIVE_URL_KEY`, `LIVE_TOKEN_KEY`, `resolve_token`,
  `publish_live_config` (DDL + upsert, all wrapped so a boot never dies on it).
- [ ] **Step 4: Run — pass.**
- [ ] **Step 5: Full web suite green.**

---

## Task 3: `POST /api/live`

**Files:**
- Modify: `LEM Web Server/web_app.py`
- Test: `LEM Web Server/tests/test_live_endpoint.py`

- [ ] **Step 1: Failing tests** — valid push accepted (`204`); missing token
  `401`; wrong token `401`; malformed body `400`; missing `machine_uid` `400`;
  **a push makes zero gateway calls** (count calls on the fake); unknown
  `machine_uid` accepted.

- [ ] **Step 2: Run — 404, the route does not exist.**

- [ ] **Step 3: Implement.** `create_app(..., live=None, live_token=None)`,
  storing `app.config["LIVE"]`/`["LIVE_TOKEN"]`; the route reads
  `X-LEM-Token`, compares with `hmac.compare_digest`, validates, records.
  No `refresh_soon()`, no gateway call anywhere on this path.

- [ ] **Step 4: Run — pass.**
- [ ] **Step 5: Full web suite green.**

---

## Task 4: Failover in `/api/machines`

**Files:**
- Modify: `LEM Web Server/live_presence.py` (add `merge_machines`), `web_app.py`
- Test: `LEM Web Server/tests/test_live_endpoint.py`

- [ ] **Step 1: Failing tests** — a fresh live entry replaces the record's
  status and colour and reports `live: true`; no live entry → record unchanged,
  `live: false`; an expired entry → record; a machine the live road has never
  mentioned is untouched; the record's own fields (title, position, QC) survive
  the overlay.

- [ ] **Step 2: Run — fail (no `live` key).**

- [ ] **Step 3: Implement** `merge_machines(machines, live, colors)` in
  `live_presence.py` — pure, no Flask — and call it from `/api/machines`.
  Recompute `color` from the status map so the dot matches the pushed status.

- [ ] **Step 4: Run — pass.**
- [ ] **Step 5: Full web suite green.**

---

## Task 5: Live parse blips in `/api/events`

**Files:**
- Modify: `LEM Web Server/live_presence.py` (add `live_events`), `web_app.py`
- Test: `LEM Web Server/tests/test_live_endpoint.py`

- [ ] **Step 1: Failing tests** — a push carrying `last_parse_at` + `lab_id`
  appears at the head of `/api/events` with the same key shape the floor dedupes
  on (`machine_uid`, `ts`, `lab_id`); a push without a parse adds nothing; the
  same run arriving later through the snapshot does not duplicate.

- [ ] **Step 2: Run — fail.**
- [ ] **Step 3: Implement** `live_events(live)` and merge ahead of the snapshot
  events, de-duplicating on `(machine_uid, ts, lab_id)`.
- [ ] **Step 4: Run — pass.**
- [ ] **Step 5: Full web suite green.**

---

## Task 6: Boot wiring

**Files:**
- Modify: `LEM Web Server/web_server.pyw`
- Test: `LEM Web Server/tests/test_live_endpoint.py`

- [ ] **Step 1: Failing test** — `create_app` alone performs **no** `lem_meta`
  write (the factory must stay side-effect free, per the snapshot lesson), and
  the boot helper does perform it.
- [ ] **Step 2: Run — fail.**
- [ ] **Step 3: Implement** a `start_live_channel(app, gateway, url)` helper
  called from `web_server.pyw` only.
- [ ] **Step 4: Run — pass.**
- [ ] **Step 5: Full web suite green.**

---

## Task 7: Floor timers and the hover card

**Files:**
- Modify: `LEM Web Server/templates/floor.html`
- Test: `LEM Web Server/tests/test_floor_poll_interval.py`

- [ ] **Step 1: Failing tests** — the status refresh interval is ≤ 5000ms and the
  run-blip interval is ≤ 5000ms, read out of the served HTML (the way
  `test_floor_ui.py` checks the floor).
- [ ] **Step 2: Run — fail (30000, 6000).**
- [ ] **Step 3: Change both to 2000**; show `live` vs from-record age in the hover
  card only — no change to how the dot is drawn.
- [ ] **Step 4: Run — pass.**
- [ ] **Step 5: Full web suite green.**

---

## Task 8: Module — the pure helpers

**Files:**
- Modify: `LEM Station Module/lem_station_module.py`
- Test: `LEM Station Module/tests/test_live_push.py`

- [ ] **Step 1: Failing tests** — `parse_live_config` turns `lem_meta` rows into
  `(url, token)` and tolerates missing/blank rows; `build_live_payload` carries
  `machine_uid`, `status`, `reason`, `at`, `interval_seconds`, and
  `last_parse_at`/`lab_id` from the newest row that has one, and omits the parse
  fields when nothing was parsed.
- [ ] **Step 2: Run — fail.**
- [ ] **Step 3: Implement** both, plus `LIVE_CONFIG_QUERY`.
- [ ] **Step 4: Run — pass.**
- [ ] **Step 5: Full module suite green.**

---

## Task 9: Module — posting, and never raising

**Files:**
- Modify: `LEM Station Module/lem_station_module.py`
- Test: `LEM Station Module/tests/test_live_push.py`

- [ ] **Step 1: Failing tests** — `post_live` POSTs JSON with the `X-LEM-Token`
  header to `<url>/api/live` and returns True; returns False (never raises) on
  connection error, timeout, and HTTP error; does nothing with a blank url.
- [ ] **Step 2: Run — fail.**
- [ ] **Step 3: Implement** with `urllib.request` (stdlib only, 1.5s timeout,
  `except Exception: return False`).
- [ ] **Step 4: Run — pass.**
- [ ] **Step 5: Full module suite green.**

---

## Task 10: Module — push from the poll

**Files:**
- Modify: `LEM Station Module/lem_station_module.py`
- Test: `LEM Station Module/tests/test_live_push.py`

- [ ] **Step 1: Failing tests** — a completed `_process_outcome` posts once,
  carrying the status it just evaluated and the Lab ID it just parsed; no config
  in `lem_meta` → no post attempted; a dead server leaves the poll's payload
  intact and raises nothing; the config is read once and cached, and re-read
  after repeated failures; structurally, the push is issued from
  `_process_outcome` (worker), not `_show_outcome` (main thread).
- [ ] **Step 2: Run — fail.**
- [ ] **Step 3: Implement** `_live_config()` (cached, re-read after 3 consecutive
  failures) and `_push_live(...)`, called at the end of `_process_outcome`,
  wrapped so nothing escapes the worker.
- [ ] **Step 4: Run — pass.**
- [ ] **Step 5: Both suites green.**

---

## Task 11: End-to-end and documentation

**Files:**
- Test: `LEM Web Server/tests/test_live_endpoint.py`
- Modify: `LEM Web Server/CLAUDE.md`, `LEM Station Module/../CLAUDE.md`

- [ ] **Step 1: Failing test** — the exact payload the module builds, posted to
  the real endpoint with the real token, changes what `/api/machines` reports for
  that machine. One test that crosses both programs' contracts.
- [ ] **Step 2: Run — fail if either side drifted.**
- [ ] **Step 3: Fix drift; document** the channel in both CLAUDE.md files
  (what it is, why the record stays authoritative, the `--host 0.0.0.0` note).
- [ ] **Step 4: Run — pass.**
- [ ] **Step 5: Both suites green; re-run the module's loader + annotation guards.**
