# V4 → V5 feature gap

**Date:** 2026-08-03
**Compared:** `V4.0.3.1 - Beta Stable/` (the shipped desktop-era app) against
`V5.0 - LabCore Backend/` (floor UI + station modules).

V5 rebuilt the *data path* — LabCore as the bus, one module per machine, QC
self-detection, the floor map, control charts. What it has not yet rebuilt is
most of V4's **lab-operations** layer: the daily human workflow around the
instruments. That's what this list is.

Legend: **A** = blocks daily use, **B** = needed before V5 replaces V4,
**C** = nice to have / decide against.

---

## 1. Checklists — completely absent · **A**

The single biggest hole. V4's whole daily-rounds workflow has no V5 equivalent:
no model, no table, no endpoint, no UI.

What V4 had (`models.py`, `web_server.pyw:993-1163`):

| Piece | Detail |
|---|---|
| `ChecklistSpec` | `uid`, `name`, `due_time` ("HH:MM", 24h) |
| `ChecklistItem` | `text`, `days_active` (list of weekday ints, default Mon–Fri), `item_type` = `item` \| `header` \| `subtask`, `uid`, `parent_uid` |
| Nesting | Checking a parent auto-checks every child |
| Daily state | Keyed by date → `{(checklist_uid, item_uid): {user, time, checked}}`, persisted to `checklist_state.json` |
| Attribution | Every toggle records **who** and **at what time** |
| Live sync | `BUS.publish({"type": "checklist"})` — SSE, so every screen in the lab updates at once |
| Audit | `log_checklist_event(...)` → CHECKED / UNCHECKED with user |
| Export | `Checklists_<date>.csv` in the daily report |
| History | Per-day completion % (`checklist_summary`) |

Endpoints: `GET /api/checklists`, `POST /api/checklists/toggle`,
`POST /api/checklists/config`.

**V5 work:** new `lem_checklists` (definition) + `lem_checklist_state`
(per-date ticks) in LabCore, a `checklists.py` store, three endpoints, and a
floor panel. The due_time matters — a checklist overdue at 09:00 should be
visible on the floor the way an overdue PM is.

---

## 2. PM & calibration — present but materially weaker · **A/B**

V5 *does* have central PM/CAL (`maintenance_store.py`, `lem_maintenance`,
scheduled from the floor, pulled by every module — genuinely better placed than
V4). But the task model lost real capability:

| Gap | V4 | V5 | Why it matters |
|---|---|---|---|
| **Repeat units** · A | `repeat_value` + `repeat_unit` = days/weeks/**months**/**years**, with true calendar arithmetic (`_add_interval` handles month-end and Feb 29) | `interval_days` integer only | "Annual calibration" has to be entered as 365 days and then drifts a day every leap year. Monthly PMs land on a different date each month. |
| **Edit a task** · A | `update_task` — name, next-due date, repeat, notes | no update path; delete and re-add | Can't fix a typo, can't change an interval, can't set the next-due date directly (e.g. after an off-book service call) |
| **In-progress state** · B | `start_task` → `IN_PROGRESS`, `cancel_task` reverts | none | The lab can't see "someone is doing the annual cal right now" — two techs can start the same job |
| **Mandatory completion note** · B | `complete_task` **refuses** without a comment, and refuses unless the task is IN_PROGRESS | `prompt(...) ?? ''` — optional, empty accepted | The completion record is the audit artifact; blank ones are worthless |
| **SOON warning** · B | statuses UPCOMING / **SOON (≤14 days)** / DUE / OVERDUE / IN_PROGRESS | GREEN until the due date, then YELLOW/RED | Nothing warns ahead. A cal goes from "fine" to "due today" with no lead time to schedule it |
| **One CAL per machine** · C | enforced in `create_task` | not enforced | Duplicate calibration entries drift apart |
| **Machine comments** · B | `add_comment` / `get_comments` — a per-machine notebook independent of tasks | `comment` exists as a `lem_machine_log` kind (module ✎ only); no server endpoint | The floor can't write a note against a machine |
| **Maintenance audit trail** · B | `maintenance_log.csv` per machine: start / complete / cancel / comment / delete / manual_override, each with user | only completions land in `lem_machine_log` | Adding and deleting a scheduled task is silent — no record of who removed a PM |

---

## 3. Correction factors — absent · **B**

Per machine, per test: a correction value plus `value_column` and file
destination, stored as JSON, with a **change log recording previous → new value
and the user**, and a daily `CorrectionFactors_<date>.csv` export.
Endpoints `GET/POST/DELETE /api/boxes/<uid>/corrections`
(`web_server.pyw:1392-1520`).

Explicitly deferred as v1-out-of-scope for the station module, so this is a
conscious debt rather than an oversight — but it is real instrument data the lab
was keeping, and the change log is the auditable part.

**Open question:** does the correction get applied in the module (at parse time,
so LabCore stores corrected values) or on the server (display only)? That
decision has to come before the schema.

---

## 4. Scheduled daily report — absent · **B**

V5 exports only on a click. V4 ran itself:

- `report_enabled`, `report_time`, `report_dir`, `last_report_date` —
  `_maybe_run_daily_report` writes once per day after the configured time
- `LabManagerReport_<date>.csv` — a **fleet status snapshot**, 17
  human-readable columns: Equipment Name, Overall Status, Manual Override,
  Last Good QC, Latest Data Time, QC Expiry (days), Status Reason, Sample,
  Test, Expected, Tolerance (±), Low, High, Latest Value, Units, In Spec,
  Data Source
- alongside it: `Checklists_<date>.csv`, `ConfigChanges_<date>.csv`,
  `CorrectionFactors_<date>.csv`
- `GET /api/report` — live preview of the same table, `?download=1` to save

V5's `export.csv` / `export/qc.csv` are per-machine **event** dumps. The
fleet-status-snapshot shape — one row per machine × test with the band and the
verdict — doesn't exist, and nothing runs on a schedule.

---

## 5. Logs / History view — thin API, no page · **B**

V4 kept **six** distinct audit streams: `status_changes.csv`,
`manual_overrides.csv`, `config_log.csv`, `maintenance_log.csv`, inventory
events (`log_inventory_event` — machine added/removed), and checklist events.
Surfaced via `GET /api/logs` and `GET /api/history` (which also returned
per-day checklist completion %).

V5 has one table, `lem_machine_log` (kinds `run|qc|status_change|override|
comment|pm|calibration`), and `/api/events`. Missing:

- **Config-change audit** — who edited a QC spec, changed a machine's targets,
  ran a changeover, added or deleted a machine. Currently *nothing* records
  this; `/api/boxes` POST and `/api/machines/<uid>` DELETE write no trail.
- **Inventory events** — machine added / removed.
- **Checklist events** (follows from §1).
- **A history page.** The floor shows "Recent activity" only. There is no
  searchable, filterable, date-ranged log view anywhere in V5.

---

## 6. Settings — absent · **B**

V4's `POST /api/settings` covered `poll_minutes`, `map_locked`,
`sample_id_column`, all four `report_*` fields, `status_log_dir`,
`correction_factor_dir`, plus theme/font/ui_scale.

V5 has no settings endpoint at all. `map_locked` hides in `/api/map`; poll
interval belongs to the module now. But **§4 can't ship without somewhere to
configure the report schedule**, so this is a prerequisite, not a standalone
feature. A theme toggle would also be welcome (V4 had light/dark).

---

## 7. User management — absent, probably deliberate · **C (decide)**

V4 had `UserSpec` in config and `GET/POST/DELETE /api/users`. V5 authenticates
against suite-wide LabCore (`labcore_auth.py`) — which is the right call and
shouldn't be undone.

What's genuinely missing is **authorization**: V5 has one permission level, so
any logged-in user can delete a machine, purge its history, or retire a QC lot.
Worth a deliberate decision rather than drift.

---

## 8. Smaller items · **C**

- **First-in-spec-of-day tracking** — V4 carried `first_inspec_date` /
  `first_inspec_map` (`{box_uid: ISO time}`): the moment each instrument first
  passed QC that day, i.e. when the bench actually came online. Vestigial in
  V4's web server (model only), absent in V5. A good turnaround metric and
  cheap to derive from `lem_machine_log` now.
- **`/api/fs/list`** (filesystem browser for picking CSV paths) — obsolete;
  modules own their own paths. Don't port.
- **`/api/boxes/<uid>/qc-data`** (raw rows for charting) — superseded by
  `/api/machines/<uid>/qc-trend`. Done.

---

## Already at parity or better in V5

QC sample/standard library · changeover (lot turnover propagating to every
machine) · per-machine QC overrides · manual status override (**comment
enforced server-side** — better than V4) · machine delete + history purge ·
floor map with drag-to-arrange, 2D/3D, lock · QC control charts · module
heartbeat and honest running/stopped/unknown states · LabCore outage
resilience · live SSE-ish refresh · mobile layout.

---

## Suggested order

1. **Checklists** (§1) — biggest missing workflow, self-contained, no
   dependencies.
2. **PM/CAL hardening** (§2) — repeat units, edit, SOON, mandatory note,
   in-progress. Touches `maintenance_store.py` + the module's PM/CAL evaluation
   in step, so do it as one piece.
3. **Settings** (§6) then **scheduled daily report** (§4) — in that order,
   since the report needs somewhere to live.
4. **Audit + history page** (§5) — config-change logging is the missing half;
   the page is what makes any of the logging useful.
5. **Correction factors** (§3) — after the module/server placement question is
   answered.
6. **Permissions** (§7) — decide, then implement or write down that one level
   is intended.
