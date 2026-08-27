# Floor UI pass — Ryan's list, 27 Aug 2026

Everything asked for in one sitting, plus the certificate work already in
flight. Ordered by what has to be true before the next thing can be built, not
by the order it was said.

Each item states **what a test asserts**, because "more visually separated" is
not a thing that can be checked and a test that cannot fail is worse than no
test. Where the ask is genuinely a matter of taste, the test guards the
*structure* the taste needs — a separator element exists, a block is not
rendered N times — and the look itself is judged by eye in a screenshot.

---

## Where it stands before this pass

- `v3.1.0` is live at `lem.asaplabs.net` and `main` is clean.
- **Two modules are finished, tested, and unreachable from any screen.**
  `uncertainty.py` (6 routes) and `standard_documents.py` (4 routes) have zero
  callers in `templates/` or `static/`. This is the "declared but inert"
  pattern CLAUDE.md names for `levels.py`, `equipment_documents.py` and
  `equipment_history.py` — working and unwired look identical from outside,
  because the tests pass either way.
- `tests/test_certificate_lifecycle.py` is **14/15 green**; the one red is the
  tripwire that fails until the floor can actually reach the routes (§9).

---

## 1. The QC actions move to the top of the QC tab

**Ask:** *"the 'assigned QC samples' and 'QC standards library' buttons should
be moved to the top."*

Today they sit in `.railact` at the BOTTOM of `#tab-qc`, under the control
chart and under the full QC checks list — so on an instrument with several
methods you scroll past everything to reach the two things you came to do.

**Test:** in the rendered panel, the `#actQc` / `#actQcLib` block appears
*before* `#trend` in document order.

---

## 2. The four tabs get real separation

**Ask:** *"the QC PM and Cal docs and history tabs should be more visually
separated."*

**Test:** the `.tabs` strip carries a bottom rule, and the selected `.tab` is
distinguished by more than colour alone (a border/underline), so it survives a
greyscale screenshot and a colour-blind reader. Structure is testable; the
exact weight is judged in the screenshot.

---

## 3. The uncertainty number goes above the module status

**Ask:** *"The uncertainty number should be above where it says the module
status and the check-in time."*

This is the first UI `uncertainty.py` has ever had. The railhead currently runs
`last data …` → `module …` → `watching …` → uid. The expanded uncertainty **U**
goes above that pair.

Rules this line must obey, taken from the module's own tests:

- It shows the **approved** estimate only. A computed-but-unapproved number has
  not been anybody's judgement yet (`TestComputeNeverAutoApproves`).
- It must say when there is **no estimate** rather than render blank — blank
  reads as "uncertainty is zero", which is the one thing it can never be.
- An interim Route 3 estimate is **labelled interim** and never as a measured
  `u(Rw)` (`test_the_interim_route_is_labelled_as_interim`).
- A failed read is not "no estimate" (`TestAReadThatFailedIsNotAnEmptyRegister`).

**Test:** the line renders above `module …` in document order; an unapproved
estimate does not appear; an absent estimate renders a stated absence, not an
empty node; a read failure renders the failure.

---

## 4. The railhead identity block gets separated

**Ask:** the run of `QC stale: …` / `last data 3h ago` / `module running ·
checked in 1m ago` / `watching serial COM4 @9600` / `b2ce21612b3c` *"needs
better visual separation."*

Five different KINDS of fact stacked in one grey column: a verdict, a data
recency, a module heartbeat, a source, and an identifier. They read as one
paragraph because they are drawn as one.

**Test:** the block is grouped into labelled runs rather than five sibling
`.meta` divs, and the machine uid — the only one nobody reads unless they are
debugging — is visually demoted below a separator.

---

## 5. The Shewhart statistics block stops rendering for every QC

**Ask:** the whole `days 6/8 → 27/8 … spread basis: unknown` block *"needs to
be not populating for every single QC, as it takes so much visual space."*

That block is ~10 lines per method. On an instrument with five methods it is 50
lines of statistics above everything else. Every sentence in it is *true* and
worth keeping — the PROVISIONAL warning and the "cannot be called repeatability
or within-laboratory reproducibility" note are the honest core of the QC work —
so this is **collapse, not delete**.

**Test:** with N methods on an instrument, the statistics block is rendered
collapsed by default for all of them; the *headline* verdict per method stays
visible; expanding one does not expand the others; and the PROVISIONAL text is
still present in the DOM (a warning that disappears when collapsed is a warning
that was deleted).

---

## 6. "QC checks on this equipment" splits off the control chart

**Ask:** *"should be visually separated from the QC control chart, not just
stacked below them, maybe a separate tab within the QC window?"*

A sub-tab inside `#tab-qc`: **Chart** | **Checks**.

**Test:** both sub-panes exist; exactly one is visible at a time; switching
between them does not refetch (the panel already holds the data); and the
default pane is Chart.

---

## 7. The idle diagnosis gets shorter

**Ask:** *"'Module is running but has parsed nothing for 13 h. Check its source
(single_csv //asapserver/Labsharedrive/ASAP Lab Results/GC Results/LEVEL 1/
distill_results.csv) — the equipment may simply be idle.' should be shorter."*

The path is 78 characters of the 190 and it is already on the `watching` line
directly below. Proposed: **"Running, nothing parsed for 13 h — may just be
idle."**

**Test:** the diagnosis for a silent-but-running module is under 80 characters
and does not repeat the source path, which `watching` already carries.

---

## 8. The map becomes a flat top-down 2D plan

**Ask:** *"Make the map not isometric but a simple top down 2d view for faster
loading."*

This reverses the 25 Aug camera work, deliberately. `PLAN_CAM` goes from
`{tilt: 30, yaw: 45}` to a true overhead: no yaw rotation, no vertical
extrusion, `PLAN_H_UNIT` → 0.

**The existing guard must be inverted, not deleted.**
`test_the_projection_stays_isometric` in `tests/js/floorboot.mjs` exists
because a rotated plan once shipped *as* an isometric and every coverage test
passed. The replacement asserts the new invariant with the same force: the
projection is axis-aligned (a bay is a rectangle, not a diamond) and flat (a
wall contributes zero height). Removing the guard rather than replacing it is
how the drawing drifts back.

**Also worth measuring, since "faster loading" is the stated reason:** a flat
plan drops the per-instrument wall polygons. Record the before/after node count
and draw time rather than asserting the speed-up.

**Test:** `planIso(1,0)` and `planIso(0,1)` are axis-aligned; height `h` does
not move a point; a bay's projected aspect is 1:1; the drawing still fits the
stage.

---

## 9. Certificates become reachable (in flight — server done)

The QC standards library gets a certificate section: upload, list, download,
delete, and the expiry report.

**Already landed, server side**, `tests/test_certificate_lifecycle.py`:

- **A rename carries the certificate.** The library renames by `POST` new +
  `DELETE` old, and a certificate is keyed by the standard's NAME — so before
  this, a rename orphaned every certificate on it. `DELETE` now takes
  `renamed_to` and repoints.
- **A rename onto a name the library does not hold is refused** — it would
  produce an orphan wearing a valid label, which `orphaned_certificates` cannot
  even see.
- **A rename onto itself is refused as the contradiction it is**, rather than
  falling through to the certificate-conflict branch and reporting the wrong
  problem.
- **Deleting a standard that still holds a certificate is refused (409).**
  Destroying a controlled document as a side effect of a library tidy-up is not
  undoable and is the worse of the two available mistakes.
- **A changeover does NOT inherit the certificate**, and says the new lot needs
  one. A changeover is a new LOT — new batch, new assay, new COA. Inheriting
  the specs is right; inheriting the document would attach a certificate
  describing a batch this is not, which is worse than having none because it
  looks complete.

**Discovered while testing:** the store is **content-addressed**. Uploading
identical bytes under a second filename returns the FIRST record, with the
first filename. The UI must display the filename the server returns, never the
one that was picked, or it will claim to have filed a document it did not.

**Still to do:** the UI itself, which is what turns the last red test green.

---

## 10. Agilent GC 1 shows no events — NEEDS ONE ANSWER

**Ask:** *"The Agilent GC doesn't show events for some reason, but other
equipment does."*

Measured against live LabCore this morning, and **the API is healthy**:

| | Agilent GC 1 | OptiMPP 1 |
|---|---|---|
| `lem_machine_log` rows | **26,106** (most in the lab) | 295 |
| log read, 3 runs | 0.84 / 0.10 / 0.20 s | 0.14 / 0.17 / 0.10 s |
| other four timeline sources | all 0.08–0.15 s, no error | same |
| `GET /api/equipment/<uid>/history` | **200, 200 entries** | 200, 200 entries |

So the data is there, readable, fast, and the endpoint returns it. Two facts
that are probably the real story:

1. **Agilent's newest event is `2026-08-27T00:08` — about 13 hours old.** Every
   other instrument has events from this morning. It is the instrument the
   "parsed nothing for 13 h" message in §7 is about.
2. **Its 200 newest entries span only 8.5 hours**, because it writes five rows
   per run. Any panel showing "the last 200 events" shows Agilent less than one
   day while showing OptiMPP twenty days.

**The question:** which panel is empty — the **History** tab, the **Activity**
feed on the overview, or the **QC checks** list? History returns 200 entries,
so if that is the empty one it is a front-end bug and I will go straight to it.

---

## Order of work

1. §7 idle text — one line, unblocks nothing, costs nothing
2. §1 button move + §2 tab separation — pure layout, same file, one screenshot
3. §4 railhead grouping — same region, same screenshot
4. §5 statistics collapse + §6 sub-tabs — the QC pane restructure, together
5. §9 certificate UI — turns the last red test green
6. §3 uncertainty line — the largest new surface, and it wants §4 landed first
7. §8 flat map — self-contained, and it rewrites a JS guard
8. §10 Agilent — as soon as the panel is named

TDD throughout: the failing assertion first, then the markup. Where the ask is
taste, the test guards the structure and the screenshot judges the look.
