# Measurement Uncertainty from QC data — Design

**Date:** 2026-08-25
**Goal:** Turn the QC runs LEM already logs into defensible ISO/IEC 17025 measurement
uncertainty estimates — within-laboratory reproducibility, bias, and expanded
uncertainty — frozen as approved records that satisfy an accreditation assessor's
annual review.

**Status:** Spec only. Not started. Two open questions at the bottom need Ryan's answer
before the u(Rw) route can be finalised.

---

## Why this exists

ASAP Labs is preparing for a PJLA assessment in **September 2026**. ISO/IEC 17025:2017
clause 7.6 requires the laboratory to identify the contributions to measurement
uncertainty and to evaluate it for every quantitative measurand on scope. PJLA policy
PL-3 requires those records to be available at assessment.

The laboratory's method for doing this is **NORDTEST TR 537 edition 4** — a top-down
model that builds uncertainty from routine QC data rather than from a component-by-
component propagation. That decision is documented in a new SOP (below).

The relevant fact for this repo: **LEM is already collecting the data the model needs
and throwing away everything except a pass/fail light.** Every QC run lands in
`lem_machine_log` with `kind='qc'`, a timestamp, a machine, a test name and a numeric
value. That is a time series of repeat measurements on a known material. Nobody does
arithmetic on it.

This spec is about doing the arithmetic, and — more importantly — recording it in a
form that survives an auditor.

---

## Controlling document

**`ASAP SOP QMU 1.001 — Estimation and Reporting of Measurement Uncertainty`**
`/Volumes/Labsharedrive/SOPs/ISO 17025 Uncertainty - DRAFT/ASAP SOP QMU 1.001 Measurement Uncertainty.docx`
(draft, awaiting approval — read-only web copy: https://claude.ai/code/artifact/55c7923c-0b81-43b4-b14e-8110b87b2376)

**This code implements that SOP. If the two disagree, the SOP wins and the code is a
bug.** The clauses that map directly to code:

| SOP clause | What the code must do |
|---|---|
| 2.1 | Decide whether a measurand needs an estimate at all; ordinal and qualitative results are excluded *with a recorded reason*, not skipped |
| 2.2 | Store the list of contributions considered — including ones judged negligible |
| 2.3 | `u_c = √(u(Rw)² + u(bias)²)`, `U = 2·u_c`, k = 2 always unless justified |
| 2.4 | Three routes to `u(Rw)`; record which was used and why |
| 2.5 | Three routes to `u(bias)`; the six-round rule for the PT route |
| 2.6 | Convert inputs to standard uncertainties before combining; absolute vs relative crossover |
| 2.7 | Compare `U` against the method's published reproducibility `R` |
| 2.8 | Record whether a known bias is corrected or carried |
| 2.9 | Exclusions require an investigated cause and a nonconforming-work reference |
| 2.10 | The twelve-field Register entry — this is the output format |
| 2.11 | Seven re-estimation triggers — LEM already emits most of these as events |
| 2.12 | `U` feeds the decision rule for statements of conformity |

---

## Reference documents and where they live

Everything below is on the lab share, reachable at `/Volumes/Labsharedrive/`.

**Metrology method**
- NORDTEST TR 537 ed. 4 (2017) — public: http://www.nordtest.info/wp/wp-content/uploads/2017/11/NT_TR_537_edition4_English_Handbook_for_calculation_of_measurement_uncertainty_in_environmental_laboratories.pdf
  Sections 5–8 are the model; **Section 8.2 is the worked example to imitate** — it shows
  a laboratory with only three PT rounds estimating bias two ways rather than one.
- `ASTM & UNE-EN Methods/ASTM In-House Methods/ASTM E2655-14(Reapproved 2020) Standard Guide for Reporting Uncertainty...pdf`
- `ASTM & UNE-EN Methods/ASTM In-House Methods/ISO IEC Guide 99-2007.pdf` — VIM, the vocabulary

**Certificates — the source of `cert_value` and `cert_uncertainty`**
- `Laboratory Calibration and Standards/` — the whole folder. Notable:
  - `VHG- Sulfur Standards/`
  - `SQS- Spectrum Quality Standards/`, `LGC/`, `Restek/`, `SCP Scientific/`
  - `COA Motor Gasoline Reference Material - RON Octane NO. ASTM D2699 1-9-26.pdf`
  - `COA Motor Gasoline Reference Material -MON Octane NO. ASTM D2700 1-9-26.pdf`
  - `ASTM Reference Mat-Catalog-2023-Oct.pdf`

**Proficiency testing — the alternative bias route, and the cross-check**
- `ASTM Proficiency Information/` — one folder per cycle, 2022→2026. Each cycle's full
  report contains, per method, a *Data Report* (consensus average, StdDev, ASTM R,
  results used) and a *Results Table and Z-Scores* listing every lab by its per-cycle
  confidential code.
- ASAP's PTP account is **2389765**. There is no permanent lab code — ASTM issues a new
  one each cycle. Diesel codes to date: 0576, 0370, 0316, 0662, 0237, 0009, 0346, 0497, 0054.
- A completed nine-cycle diesel bias analysis already exists and should be treated as the
  reference implementation of the maths: https://claude.ai/code/artifact/c9ec9121-0e0c-450a-99db-b5d8984c461e

**Method precision statements — for the 2.7 check**
- `ASTM & UNE-EN Methods/ASTM In-House Methods/` — the ASTM standard for each method
  carries its own repeatability `r` and reproducibility `R` in the Precision and Bias
  section.

**Wider accreditation context** (not needed to write the code, useful for judgement calls)
- https://claude.ai/code/artifact/aec8971a-f38c-4406-aaf5-1bd41d09e15f

---

## What LEM already has

Read these before writing anything.

| File | What it gives you |
|---|---|
| `LEM Web Server/qc_samples.py` | `QcSample` / `QcSampleTest` — a named standard with `expected`, `std_dev`, `k`, `units`, stored in LabCore `lem_qc_samples`. Defined once, used by every machine. **This is the right model and it stays.** |
| `LEM Web Server/qc_specs.py` | `QcSpec` / `QcSpecStore` — the per-machine table `lem_qc_specs`, plus the contract docstring describing the whole QC bus |
| `LEM Web Server/web_app.py` ~1032 | `lem_machine_log` DDL: `machine_uid, ts, kind, lab_id, test_name, value, detail` |
| `LEM Web Server/web_app.py` ~1067 | `LOG_KINDS = ("run","qc","status_change","override","comment","pm","calibration","config")` |
| `LEM Web Server/web_app.py` ~1047 | `_audit()` — the pattern for writing a config-change row, including `by` |
| `LEM Web Server/snapshot_service.py` | Schema ownership and snapshotting — the natural home for the frozen-estimate table |
| `LEM Station Module/lem_station_module.py` ~6017 | Where a QC row is written: `value=f"{value:g}", detail=qc_log_detail(spec, raw, value)` |
| `LEM Station Module/lem_station_module.py` ~748 | `build_last_qc_query()` — the existing read pattern over `lem_machine_log` |

**The data you need is `SELECT ts, value, detail FROM lem_machine_log WHERE kind='qc'
AND machine_uid=? AND test_name=?`.** That is the whole input.

---

## The gaps

### 1. `std_dev` is a control limit, not a certificate uncertainty

`QcSampleTest.std_dev` is used for one thing: the pass band, `expected ± k·std_dev`.
Whoever set it may have taken it from the method's precision statement, from experience,
or from the certificate — nothing records which.

`u(bias)` needs a different number: **the certificate's expanded uncertainty divided by
its own coverage factor.** Conflating the two is the single most likely way to get this
wrong, and it produces a plausible-looking answer, which makes it worse.

Add to `QcSampleTest`, all optional so existing definitions keep working:

```
cert_value:        float | None   # certified value; falls back to `expected`
cert_uncertainty:  float | None   # expanded U from the certificate
cert_k:            float = 2.0    # the certificate's own coverage factor
cert_number:       str = ""       # certificate / COA identifier
cert_lot:          str = ""
cert_expiry:       str = ""       # ISO date
```

`u(Cref) = cert_uncertainty / cert_k`. If `cert_uncertainty` is absent, a bias estimate
**must not be produced** — return the repeatability half only and say why. Do not guess.

### 2. Nothing computes

No module turns the log into numbers. That is the bulk of this work — see below.

### 3. Repeatability is not within-laboratory reproducibility

**This is the metrology trap. Read this twice.**

If every QC run for a test is the same analyst on the same shift against the same
calibration, the standard deviation of those results is **repeatability, `s_r`** — the
best case, not the real spread. Calling it `u(Rw)` overstates the laboratory's control
and an assessor who asks "who ran these?" will find it.

`u(Rw)` requires the spread to span analysts, days and calibration states. The log
currently records value and timestamp; it does not record **who** or **which calibration
epoch**. Two additions to the QC detail JSON written by the station module:

```
"operator":       "<LabStation user>"
"calibration_id": "<ts of the last kind='calibration' row for this machine>"
```

Then the calculation can report `n_operators` and `n_days` alongside `s`, and refuse to
label the result `u(Rw)` when the data does not support it. **The honest output when the
spread is single-operator is `s_r`, clearly labelled, plus a note that duplicate-analysis
data is needed to complete the estimate.**

### 4. Nothing is frozen

An annual review needs *"as of 2026-08-25, from these 84 results, u_c was X, approved by
Y."* A number recomputed on every page load is not a record — the inputs move under it.

Add `lem_uncertainty_estimates`, written once and never recomputed:

```sql
CREATE TABLE IF NOT EXISTS lem_uncertainty_estimates (
  estimate_id    TEXT PRIMARY KEY,
  machine_uid    TEXT NOT NULL,
  test_name      TEXT NOT NULL,
  sample_name    TEXT,
  window_start   TEXT, window_end TEXT,
  n              INTEGER, n_operators INTEGER, n_days INTEGER,
  mean           REAL, s REAL,
  rw_route       TEXT,        -- 'control_sample' | 'control_plus_duplicates' | 'target_limits'
  u_rw           REAL,
  bias_route     TEXT,        -- 'crm' | 'pt' | 'recovery' | 'none'
  cert_value     REAL, u_cref REAL,
  bias           REAL, u_bias REAL,
  u_c            REAL, k REAL, u_expanded REAL,
  astm_r         REAL, r_ratio REAL,
  bias_decision  TEXT,        -- 'corrected' | 'carried' | 'undecided'
  contributions  TEXT,        -- JSON list, SOP 2.2
  exclusions     TEXT,        -- JSON list of {ts, value, cause, ncr_ref}
  notes          TEXT,
  computed_at    TEXT, computed_by TEXT,
  approved_at    TEXT, approved_by TEXT,
  superseded_by  TEXT         -- estimate_id of the replacement, or NULL
)
```

Revision, never mutation: a new estimate sets `superseded_by` on the old one. An assessor
can then walk backwards through the history.

### 5. Exclusions need a cause

SOP 2.9, following TR 537: a point may be dropped **only when its cause has been
investigated and identified**. Statistical extremity alone is not grounds. Excluding a
point also has to be linkable to a nonconforming-work record, because if the excluded run
represents work reported to a customer, clause 7.10 is engaged.

So: no automatic outlier rejection. Flag candidates, make a human give a cause, store the
cause and the reference in `exclusions`.

### 6. Stale-estimate triggers are nearly free

SOP 2.11 lists seven re-estimation triggers. LEM already emits four of them as log rows:
`kind='calibration'`, `kind='pm'`, `kind='config'` (QC spec edited), and machine
replacement. Wire those to mark an estimate stale — an assessor asking "how do you know
this is still current?" is answered by a dashboard, not a memory.

---

## The maths

All of it. There is nothing else.

```
Given QC runs v₁..vₙ for one (machine, test) in a window:

  mean  = Σvᵢ / n
  s     = √( Σ(vᵢ − mean)² / (n−1) )          # sample SD, n−1

Within-laboratory reproducibility — SOP 2.4:
  Route 1  u(Rw) = s                           # control sample, whole process
  Route 2  u(Rw) = √(s² + s_r²)                # + duplicates, different matrix
  Route 3  u(Rw) = control_limit / 2           # interim, target limits

Bias against a CRM — SOP 2.5 Route B:
  bias    = mean − cert_value
  u(Cref) = cert_uncertainty / cert_k
  u(bias) = √( bias² + s²/n + u(Cref)² )

Combine — SOP 2.3:
  u_c = √( u(Rw)² + u(bias)² )
  U   = 2 · u_c

Sanity check — SOP 2.7:
  Since R = 2.77·s_R, a lab consistent with interlaboratory scatter lands near
  U ≈ R / 1.39.   r_ratio = U / (astm_r / 1.39)
  r_ratio ≫ 1  → bias or control problem, refer to 2.9 before approving
  r_ratio ≪ 1  → input data isn't capturing real variability, refer back
```

**Note on the multi-CRM case:** where several certified materials cover one measurand,
`RMS_bias = √(Σbiasᵢ²/n_CRM)` replaces the single `bias` term and the `s²/n` term is
dropped. Handle the single-CRM case first; this is the extension.

**The PT route (SOP 2.5 Route A)** is out of scope for LEM — the data lives in ASTM cycle
reports, not in LabCore. But note the rule for when someone asks: TR 537 states in the
body of the procedure that a laboratory *"should participate at least 6 times within a
reasonable time interval."* Under six rounds, the PT route is not used alone. The
existing diesel analysis (link above) already does this and can be imported later.

---

## New module: `LEM Web Server/uncertainty.py`

Follows the house pattern exactly — injected gateway, no raw DB, testable against
`FakeLabCoreGateway`. `from __future__ import annotations` **is** allowed here (this is
the web server, not the station module — the dataclass crash rule applies only to
`lem_station_module.py`).

```python
@dataclass
class QcSeries:
    """The raw material: QC runs for one (machine, test) in a window."""
    machine_uid: str
    test_name: str
    values: list[float]
    timestamps: list[str]
    operators: list[str]
    excluded: list[dict]          # {ts, value, cause, ncr_ref}

    @property
    def n(self) -> int: ...
    @property
    def n_operators(self) -> int: ...
    @property
    def n_days(self) -> int: ...
    def mean(self) -> float: ...
    def sd(self) -> float: ...     # n−1


@dataclass
class UncertaintyEstimate:
    """One frozen budget. Mirrors lem_uncertainty_estimates 1:1."""
    ...
    def is_reproducibility(self) -> bool:
        """False when the series is single-operator or single-day — in which
        case u_rw is repeatability and must be labelled s_r."""

    def to_register_row(self) -> dict:
        """The twelve fields of SOP 2.10, ready to render or export."""


class UncertaintyStore:
    """Owns lem_uncertainty_estimates. Never recomputes a stored estimate."""
    def __init__(self, gateway) -> None: ...
    def ensure_schema(self) -> None: ...
    def compute(self, machine_uid, test_name, window_start, window_end,
                rw_route="control_sample") -> UncertaintyEstimate: ...
    def save(self, est: UncertaintyEstimate, computed_by: str) -> str: ...
    def approve(self, estimate_id: str, approved_by: str) -> None: ...
    def supersede(self, old_id: str, new_id: str) -> None: ...
    def current_for(self, machine_uid, test_name) -> UncertaintyEstimate | None: ...
    def stale(self) -> list: ...   # estimates with a 2.11 trigger since computed_at
```

Web surface — one page and one export:

- `GET  /uncertainty` — table of current estimates by machine and test, with `r_ratio`
  status, staleness, and approval state
- `GET  /uncertainty/<machine_uid>/<test_name>` — the series, the working, the exclusion
  list, the history of superseded estimates
- `POST /uncertainty/compute` — compute and save a draft (never auto-approves)
- `POST /uncertainty/<id>/approve` — records `approved_by` and `approved_at`
- `POST /uncertainty/<id>/exclude` — add an exclusion with a cause; recompute as a *new*
  estimate superseding the old, never in place
- `GET  /uncertainty/<id>/register.pdf` — the SOP 2.10 Register entry, the thing that goes
  in the assessment file

---

## Tests to write first

TDD, per CLAUDE.md. These are the failing tests:

**`tests/test_uncertainty_math.py`**
- Known series → known `mean`, `s` (n−1, not n — assert against a hand-worked value)
- `u_bias` with a certificate → matches TR 537 Section 8 worked example
- `u_c` combines correctly; `U = 2·u_c`
- Missing `cert_uncertainty` → no bias term produced, and a stated reason
- `r_ratio` uses `R/1.39`, not `R/√2` — the wrong relation is close enough numerically to
  pass a sloppy test
- Single value in series → `s` undefined, estimate refused, not a crash or a zero

**`tests/test_uncertainty_reproducibility.py`**
- Single-operator series → `is_reproducibility()` is False, label is `s_r`
- Multi-operator multi-day series → True, label is `u(Rw)`
- Missing operator data → treated as unknown, not as multi-operator

**`tests/test_uncertainty_store.py`**
- A saved estimate is byte-identical on re-read — recomputation never mutates
- `approve()` sets both fields; an unapproved estimate never reports as current
- Adding an exclusion creates a new estimate and sets `superseded_by` on the old
- An exclusion without a cause is rejected
- `stale()` picks up a `kind='calibration'` row newer than `computed_at`

**`tests/test_uncertainty_web.py`**
- Auth required on every route (match `test_auth_and_delete.py`)
- Compute never auto-approves
- Register export contains all twelve SOP 2.10 fields

---

## Do not

- **Do not** use `std_dev` as `u(Cref)`. Different quantity. See gap 1.
- **Do not** auto-reject outliers. SOP 2.9 and TR 537 both forbid it.
- **Do not** recompute a saved estimate in place. Supersede it.
- **Do not** call a single-operator standard deviation `u(Rw)`.
- **Do not** invent a `cert_value` when the certificate is missing — produce a partial
  estimate and say what is missing.
- **Do not** add pip dependencies (CLAUDE.md). `statistics` from the stdlib is enough.
- **Do not** touch `lem_qc_specs` pass/fail behaviour. The QC light and the uncertainty
  budget read the same data for different purposes; changing the light's semantics would
  break the floor UI.

---

## Open questions for Ryan

Both block the u(Rw) half. Neither blocks the schema or the CRM plumbing, so work can
start.

1. **Do QC runs on a given instrument span different analysts and shifts, or is it
   effectively one person?** If it is one person, the honest number is repeatability and
   the budget needs duplicate-analysis data alongside it — a different collection problem,
   and worth knowing before building the wrong thing.

2. **Which QC standards have real certificates with stated uncertainties, and which are
   in-house or working standards with an `expected` someone derived?** Only the certified
   ones can carry a bias term. The rest give repeatability only until they are
   characterised against a CRM.

A third, lower stakes: **how far back does `lem_machine_log` actually go?** TR 537 wants
ideally more than 60 results over at least a year for the control-sample route. If the log
is younger than that, the first estimates use SOP 2.4 Route 3 (target control limits) and
carry a replacement date — which is legitimate and explicitly permitted, but should be a
decision rather than a discovery.
