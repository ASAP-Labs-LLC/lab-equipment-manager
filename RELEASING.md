# Releasing

How a version number becomes a running LEM Web Server, and what an agent needs
to know before cutting one.

**Read §5 before your first release.** Deploys here are unattended: tagging
ships to the lab without anyone clicking anything.

> **This repo releases the web server only.** A tag builds an archive rooted at
> `LEM Web Server/`. The Qt station module, the V4 rollback reference, `docs/`
> and `scratchpad/` are deliberately excluded, and the workflow **fails the
> build** if the station module ends up in the archive — that is what rooting
> at the repo by mistake looks like. The station module is installed through
> LabStation's module palette and is not versioned by this pipeline.

---

## 1. The pipeline, end to end

```
you: git tag -a v1.2.3 && git push origin v1.2.3
        │
        ▼
.github/workflows/release.yml   (triggers on tags matching v* )
        │  archives "LEM Web Server/" only, writes the tag into VERSION
        │  refuses to publish if state or the station module leaked in
        ▼
GitHub Release  v1.2.3   ← this is now "latest"
        │
        ▼
C:\ASAPApps\updater\updater.py   (polls every 5 min, on ASAPSV1)
        │  compares latest tag with C:\ASAPApps\lem\current\VERSION
        │  downloads, VERIFIES THE CHECKSUM, unpacks to releases\v1.2.3\
        │  builds that release's venv from requirements.txt
        │  starts it on scratch port 15557 with --no-tray --no-publish
        │  polls /healthz; if unhealthy it is recorded and never deployed
        ▼
        │  if healthy: waits until nobody has WRITTEN for 5 minutes
        ▼
   repoints `current`, restarts on 5557, re-checks /healthz
        │  if unhealthy after the switch: ROLLS ITSELF BACK
        ▼
   live
```

`--no-publish` on the health check is not optional: a booting LEM writes its own
address into LabCore's `lem_meta`, and every bench reads from there. A release
under test on a scratch port would point the whole floor at a port that closes
seconds later.

**And `--no-publish` opens the LEM store read-only** (transfer spec §5.1). The
store — `C:\ASAPApps\lem\store\lem.db`, or `LEM_STORE_PATH` — is LEM's own
record: every `lem_*` table and the append-only machine log. It lives outside
the release folder (a deploy swaps that wholesale) and outside `data/` (which
stays regenerable cache: the log file, documents). The candidate on 15557 opens
the live store `mode=ro`, so it can be health-checked against real data and
cannot migrate, declare or write a single row into it; if the store is not
there it says so on every read and creates nothing. `/healthz` reports
`store.path` and `store.read_only`. **Never delete or replace `lem.db` as part
of a release** — unlike `data/`, it is not regenerable.

## 2. Choosing the number

`MAJOR.MINOR.PATCH`, tag prefixed with `v`.

| Bump | When | Examples here |
|---|---|---|
| **PATCH** | A fix that changes nothing about how LEM is used | A wrong "ago" stamp, a floor layout collision, a slow query |
| **MINOR** | New behaviour, existing behaviour unchanged | A new panel, a new `/api/` route, an extra column in an export |
| **MAJOR** | Something a person or **another program** must know about | A new/renamed `lem_*` column, a change to the `/api/live` payload, a QC verdict rule change, a correction-factor semantics change |

**MAJOR matters more in this repo than in most**, because LEM is not the only
thing reading these tables. The station module on every bench shares the QC
staleness rule (`qc_is_stale`, duplicated on purpose in `data_source.py` and
`lem_station_module.py`, with `tests/test_qc_window.py` asserting they never
disagree) and the `/api/live` payload shape. If a change touches either, it is
MAJOR and the bench side has to move with it.

Also: **schema changes go in `SCHEMA_MIGRATIONS`, never a bare
`CREATE TABLE`.** Every arm of the batched read shares one statement, so a
column LabCore does not have fails the entire read and drops the whole floor to
the fallback path. That has already happened in production once.

## 3. Cutting a release

From a clean checkout on `main`:

```bash
cd "LEM Web Server"
python -m pytest tests/ -q          # see CLAUDE.md: 7 known environment failures
cd ..
git tag -a v1.2.3 -m "One line saying what changed and why"
git push origin v1.2.3
```

**Transfer v4 (server or module 4.x): the gate first.** CI runs no tests, so
nothing else stands between a transfer regression and the floor. Before a
4.x tag (transfer spec §15), every one of these must exit 0. Judge by the exit
status, never a piped `| tail`:

```bash
set -o pipefail
PY="$PWD/LEM Web Server/.venv/bin/python"     # the gate needs Flask
cd gauntlet-harness
"$PY" gate.py --target v4;          echo "gate exit=$?"    # every §9 row exact
"$PY" gate.py --target v4 --mutations --strict; echo "self-test exit=$?"
"$PY" soak.py;                      echo "soak exit=$?"    # 17 benches, 24 h
```

Then also run all three existing suites green: web pytest, module pytest
(`QT_QPA_PLATFORM=offscreen`, the module's own venv), and every
`tests/js/*.mjs`. Exit 2 from the gate or the soak means the harness itself
is not trustworthy (drift, a write outside its temp folder, a fault that did
not fire). It is not a pass and not a verdict. Tagging is still Ryan's call.

Do **not** create a `VERSION` file by hand — CI writes it and it is gitignored.

Confirm CI built it:

```bash
gh run list --workflow=release.yml --limit 1     # expect: completed  success
gh release view v1.2.3                           # expect: .zip and .zip.sha256
```

## 4. Confirming it reached the floor

```
python C:\ASAPApps\updater\updater.py status --config C:\ASAPApps\updater\config.json
```

```
lem: SERVING on 5557  current=v1.2.3  junction->v1.2.3  staged=v1.2.3 healthy=True
```

`staged` ahead of `current` means built and waiting for quiet.
`C:\ASAPApps\updater\updater.log` gives the reason in plain words.

**"Quiet" here means no writes for 5 minutes — reads do not count.** A wall
display left on does not hold a deploy back; someone ticking a checklist or
editing a correction factor does. `/healthz` reports `last_activity` so you can
see exactly what is holding it.

## 5. ⚠ What deploying automatically does and does not protect you from

Blocked from ever going live: fails to start, bad checksum, venv won't build,
`/healthz` doesn't answer. Rolled back automatically: goes live and then stops
answering.

**Not caught by anything:** a release that starts perfectly and shows the wrong
thing — a machine GREEN that should be RED, a QC band computed wrongly, a
correction applied twice. `/healthz` proves LEM is alive, never that the floor
is telling the truth. Those changes need testing before the tag.

**Rolling back:**

```
python C:\ASAPApps\updater\updater.py rollback --app lem
```

**Publishing without deploying** — `/releases/latest` skips prereleases:

```bash
gh release edit v1.2.3 --prerelease          # updater ignores it
gh release edit v1.2.3 --prerelease=false    # hand it over when ready
```

Two caveats, both found the hard way:

- **It takes ~20 seconds to take effect.** Check `/releases/latest` too soon
  and you will wrongly conclude the flag does nothing.
- **Only use it on a release that is not yet deployed.** Marking the
  *currently running* release as a prerelease moves "latest" **backwards**, so
  the updater stages the older release and tries to deploy it — a silent
  downgrade. The updater now refuses, but to pull a bad release use
  `rollback`, not this flag.

**Pausing deploys and restarts:**

```
python C:\ASAPApps\updater\updater.py pause  --app lem
python C:\ASAPApps\updater\updater.py resume --app lem
```

## 6. Gotchas that will bite an agent

- **The updater tracks whatever GitHub calls *latest*, not the highest
  version.** The comparison is equality, and GitHub's "latest" is the most
  recently created non-prerelease tag. Publishing `v1.0.9` after `v1.2.0`
  **deploys `v1.0.9`**. Deliberate — it is how you re-release a known-good build
  — but a mistyped tag ships. Check `gh release list` first.
- **The tag becomes a directory name** (`releases\v1.2.3\`). Letters, digits,
  dots, hyphens only.
- **Only the tag sets the version.** `VERSION` comes from `github.ref_name`; the
  branch, commit and release title are ignored.
- **A release restarts LEM**, which drops the in-memory snapshot. The floor
  repolls within ~2s and the benches' pushes fail silently and resume, so the
  visible cost is a few seconds of stale dots — but it is not a no-op.
- **`/healthz` must keep reporting `idle_seconds`.** A release that stops
  reporting it will not be deployed unattended, and the updater says so rather
  than guessing nobody is there.
- **Retention keeps 5 releases plus `current` and its rollback target**, so 6–7
  directories is correct.

## 7. If something goes wrong

| Symptom | Where to look |
|---|---|
| Tag pushed, no release | `gh run list` — the verify step fails the build if state or the station module leaked into the archive |
| Release exists, never staged | `updater.log` — checksum mismatch or unreachable repo |
| Staged but never deployed | `updater.py status`, then `updater.log` — it prints the exact reason |
| Deployed and the floor looks wrong | `updater.py rollback --app lem` first, investigate after |
| "LabCore offline" after a deploy | Usually a schema change breaking the batched read — see CLAUDE.md; add the column to `SCHEMA_MIGRATIONS` |
| LEM keeps restarting | `updater.log` — after 3 starts in 15 min the supervisor gives up and logs CRITICAL |
| "It says saved and nothing changed" / a QC assignment, correction factor or audit line that vanished | `C:\ASAPApps\lem\data\lem.log` — every refused LabCore write is written there by name |
| The floor is slow to update, dots lag behind the benches | `data\lem.log` — look for "the live push address was NOT published"; benches read that address out of `lem_meta` |
| A CSV export came out with uid-looking machine names | Nothing is wrong with the data — the names could not be read; `data\lem.log` says so, and the file carries a note of its own |
| Where is that log? | `GET /healthz` reports its full path as `log`. It lives in the app's **data** directory, never inside a release: deploys swap the release folder wholesale, and this file has to survive the deploy that went wrong |
