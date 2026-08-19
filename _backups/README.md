# Backups — 2026-08-03

Taken before and after the floor-stability work (see `LEM Web Server/CLAUDE.md`,
"Stability: why the floor kept rearranging itself").

| file | what it is |
|---|---|
| `LAB-lem_code_2026-08-03_2138.tar.gz` | the whole working folder, **before** the stability fixes. Includes the V4 / Data Handler reference trees, hence 592M. |
| `LIVE_code_2026-08-03_2138.tar.gz` | just the two live projects + notes, **before** the fixes. |
| `LIVE_code_2026-08-03_2221_stable.tar.gz` | the same, **after** — 965 web + 457 module tests passing. |
| `labcore_lem_tables_2026-08-03_2138.json` | every `lem_*` table out of live LabCore: 3,349 rows across 23 tables. |

All four verified readable (`tar -tzf` / `json.load`) at the time of writing.

## What's in the data snapshot

Notably: `lem_checklist_defs` 2 rounds, `lem_checklist_state` **3,096 ticks across
133 days** (2025-12-31 → 2026-08-03), `lem_machine_status` 6, `lem_machine_targets`
8, `lem_correction_factors` 2, `lem_machine_log` 205.

## Restoring

Code: unpack over `LAB-lem/`. The `.venv` is excluded — recreate with
`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.

Data: the JSON is a snapshot for *reference and recovery*, not a migration script.
Writing it back means going through LabCore's queue, which serialises at a few ops a
second and rejects past ~100 pending — see `ChecklistStore.import_state` for the
batching and back-off pattern any bulk restore should copy.
