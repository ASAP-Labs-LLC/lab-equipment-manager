#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
uncertainty.py — ISO/IEC 17025 measurement uncertainty out of the QC log.

Implements `ASAP SOP QMU 1.001 — Estimation and Reporting of Measurement
Uncertainty`, which adopts NORDTEST TR 537 ed. 4 as the laboratory's method.
**If this code and the SOP disagree, the SOP wins and this is a bug.**

Design doc: `docs/superpowers/specs/2026-08-25-measurement-uncertainty.md`.

WHAT THIS LABORATORY CAN ACTUALLY CLAIM TODAY (measured 2026-08-27)
-------------------------------------------------------------------
`lem_machine_log` begins **2026-08-03**. Twenty-four days. 773 QC rows. The
best (machine, test) series is 115 results over 12 distinct calendar days; most
are 12-29 results over 6-15 days.

TR 537 wants ideally **more than 60 results over at least a year** for the
control-sample route. **No series in this laboratory qualifies on time span.
Not one.**

So **SOP 2.4 Route 3 — `u(Rw) = control_limit / 2`, the interim target-limits
route — is the route this laboratory uses for its first estimates**, and it is
built here as a first-class path carrying a **replacement date**. Route 1
remains, remains preferred, and is *refused* on evidence that cannot support
it: `route_evidence()` says which routes the data permits and why, and
`compute_from_series()` will not hand back a Route 1 answer over a series whose
spread is not a within-laboratory reproducibility.

There is no fallback. When no route is permitted, the answer is "no route is
permitted, here is what each one is short of" — a sentence somebody acts on —
never a number produced by the nearest thing that would compute.

THERE IS NO BIAS TERM TODAY, AND THAT IS SAID OUT LOUD
------------------------------------------------------
`u(bias)` needs **the certificate's expanded uncertainty divided by its own
coverage factor**. The QC library holds four standards; three carry `expected`
+ `std_dev` + `k`, and **nothing records whether that `std_dev` came from a
certificate, from a method precision statement, or from experience.** Two of
them are literally named "CRM", which is exactly what makes the mistake easy.

`std_dev` is a **control limit**. It is not `u(Cref)`. Reading one as the other
produces a plausible-looking answer, which is what makes it the worst available
mistake — so this module **cannot make it**: it does not import `qc_samples`,
it never reads `std_dev`, and the only way a certificate uncertainty enters is
an explicit `Certificate` a person filled in. `standard_documents.py` has the
file half of the certificate binding; the numeric half is deliberately deferred
(Ryan: "we have them separated for now and will bind them later").

With no `cert_uncertainty` the spec is unambiguous: **produce the repeatability
half only, and say why.** `bias`, `u_cref` and `u_bias` come back `None`,
`bias_route` is `'none'`, `is_partial()` is True and `missing_terms['u(bias)']`
is the sentence that goes on the register entry. Absent, never omitted.

REPEATABILITY IS NOT WITHIN-LABORATORY REPRODUCIBILITY
------------------------------------------------------
`qc_series.Coverage` already decides this, over three factors — analyst,
calendar day, calibration epoch — all of which must be KNOWN and must have
VARIED. **This module takes that answer and does not form a second one.** There
is no `coverage()` here, no private `_basis()`, and `spread_basis` is
`qc_series.coverage(points).basis` verbatim, frozen onto the record so a re-read
estimate cannot be re-judged by a later, different opinion.

A spread that does not span all three is `s_r`. Calling it `u(Rw)` overstates
the laboratory's control, and an assessor who asks "who ran these?" finds it.

FROZEN, AND REVISED BY SUPERSESSION
------------------------------------
`lem_uncertainty_estimates` is written once. There is no upsert and no UPDATE of
any computed field; the only two UPDATEs in this file record a human act —
approval, and supersession. A revision is a NEW row whose id is written into the
old row's `superseded_by`, so an assessor walks backwards (`predecessors`).
`current_for` reports the approved, unsuperseded one and nothing else: a draft
is not a record.

EXCLUSIONS NEED AN INVESTIGATED CAUSE
--------------------------------------
SOP 2.9, following TR 537: a point may be dropped only when its cause has been
investigated and identified. **Statistical extremity alone is not grounds**, so
`exclusion_candidates()` FLAGS and never applies, and `Exclusion` is refused
without a cause and without a nonconforming-work reference — if the excluded run
represents work reported to a customer, clause 7.10 is engaged.

SCHEMA
------
`UNCERTAINTY_DDL` is imported into `snapshot_service.SCHEMA_DDL`, never retyped
there. It is a **new** table, so it needs no `SCHEMA_MIGRATIONS` entry — that
tuple is for a column added to a table already in the field — and it is
deliberately **not** a snapshot arm: every arm is bought with the whole floor's
2-second read, and this is a page nobody polls. No bench moves for this; it is a
MINOR.

FOUR COLUMNS THE DESIGN DOC'S SCHEMA DOES NOT HAVE, AND WHY EACH IS THERE
-------------------------------------------------------------------------
The doc's DDL is otherwise reproduced field for field and in order.

* `n_calibrations`, `spread_basis` — the doc predates `qc_series.Coverage`'s
  THIRD factor. Storing only `n_operators`/`n_days` would force a re-read
  estimate to re-decide the reproducibility question from two of the three
  facts, which is the second opinion this module exists not to have.
* `s_df` — `qc_series` warns in as many words that a consumer handed the wrong
  count "reports nineteen degrees of freedom for three results". The triple it
  hands over is `(s, s_df, spread_basis)` and all three are stored together.
* `control_limit`, `control_limit_k` — Route 3's input. A first-class route
  stores what it was computed from; without these the interim estimate is a
  number nobody can check.
* `replace_by` — SOP 2.4 Route 3 estimates "carry a replacement date". A date
  buried in `notes` cannot be queried, and `stale()` has to be able to fire on
  it.

Route 2's `s_r` is NOT a column: it is exactly `sqrt(u_rw^2 - s^2)` and is
exposed as a property, with its provenance (`s_r_n`) recorded as an SOP 2.2
contribution. Route 2 is not a route this laboratory can walk today.

WHAT IS NOT HERE
----------------
No Flask, no routes, no templates — the six endpoints in the design doc are
mounted elsewhere. The PT route (SOP 2.5 Route A) is out of scope: the data
lives in ASTM cycle reports, not in LabCore. Note the rule for when it arrives —
TR 537 says a laboratory "should participate at least 6 times within a
reasonable time interval", and under six rounds the PT route is not used alone.
"""

from __future__ import annotations

import json
import math
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import qc_series
from labcore_result import (LabCoreError, LabCoreRefused, LabCoreUnavailable,
                            confirm_write, rows)

# The raw material is `qc_series`' type, re-exported rather than redefined. The
# design doc sketches a `QcSeries` in this module; a second one would be a
# second parser of `lem_machine_log` and a second answer to the coverage
# question, which is the one thing this module must not have.
QcSeries = qc_series.QcSeries


# ── SOP 2.4: the three routes to u(Rw) ───────────────────────────────────────

RW_CONTROL_SAMPLE = "control_sample"            # Route 1: u(Rw) = s
RW_CONTROL_PLUS_DUPLICATES = "control_plus_duplicates"   # Route 2: sqrt(s²+s_r²)
RW_TARGET_LIMITS = "target_limits"              # Route 3: control_limit / 2

RW_ROUTES = (RW_CONTROL_SAMPLE, RW_CONTROL_PLUS_DUPLICATES, RW_TARGET_LIMITS)

# The routes whose u(Rw) IS the series' own spread. Route 3's number came from a
# limit somebody set, so it is evidence of an intention rather than of anything
# the instrument did — which is why `is_reproducibility()` is False there even
# on a series whose coverage would otherwise support the claim.
MEASURED_RW_ROUTES = (RW_CONTROL_SAMPLE, RW_CONTROL_PLUS_DUPLICATES)


# ── SOP 2.5: the routes to u(bias) ───────────────────────────────────────────

BIAS_CRM = "crm"
BIAS_PT = "pt"                 # out of scope here; see the module docstring
BIAS_RECOVERY = "recovery"
BIAS_NONE = "none"

BIAS_DECISION_CORRECTED = "corrected"
BIAS_DECISION_CARRIED = "carried"
BIAS_DECISION_UNDECIDED = "undecided"


# ── SOP 2.3 and 2.7: the constants ───────────────────────────────────────────

DEFAULT_K = 2.0

# R = 2.77 * s_R, so a laboratory whose expanded uncertainty is consistent with
# interlaboratory scatter lands near U = 2 * s_R = R / 1.385, which TR 537
# prints as **R / 1.39**.
#
# THE TRAP: R / sqrt(2) is 1.4142, close enough to 1.39 that the two ratios land
# within about 1.7% of each other on a real series — a test written with a loose
# tolerance passes on the wrong relation. It is asserted exactly in
# `tests/test_uncertainty_math.py`, both ways round.
R_TO_S_R = 2.77
R_TO_U = 1.39

R_RATIO_CONSISTENT = "consistent"
R_RATIO_HIGH = "high"
R_RATIO_LOW = "low"

# Where "much greater" and "much less" than 1 are drawn. HOUSE VALUES: TR 537
# and the SOP both say the words rather than the numbers, so these are a
# reporting threshold and not a metrological constant. Ryan / the SOP get to
# move them; nothing computes differently, only the verdict word changes.
R_RATIO_HIGH_AT = 1.5
R_RATIO_LOW_AT = 0.5


# ── TR 537's data sufficiency for the control-sample route ───────────────────
#
# "ideally more than 60 results over at least a year". Both are thresholds on
# the DATA, and neither is met anywhere in this laboratory today.
TR537_MIN_RESULTS = 60
TR537_MIN_SPAN_DAYS = 365

# How long an interim (Route 3) estimate stands before it must be replaced. A
# year, because a year of control data is exactly what Route 1 is waiting for:
# the replacement date and the thing that replaces it arrive together.
INTERIM_VALID_DAYS = 365


# ── what a spread is allowed to be CALLED ────────────────────────────────────

LABEL_U_RW = "u(Rw)"
LABEL_U_RW_TARGET = "u(Rw) target"
LABEL_S_R = "s_r"
LABEL_PARTIAL = "s (analyst, day or calibration held constant)"
LABEL_UNATTRIBUTED = "s (basis not recorded)"
LABEL_INSUFFICIENT = "s (undefined)"

_LABELS = {
    qc_series.BASIS_INTERMEDIATE: LABEL_U_RW,
    qc_series.BASIS_REPEATABILITY: LABEL_S_R,
    qc_series.BASIS_PARTIAL: LABEL_PARTIAL,
    qc_series.BASIS_UNKNOWN: LABEL_UNATTRIBUTED,
    qc_series.BASIS_INSUFFICIENT: LABEL_INSUFFICIENT,
}


# ── SOP 2.11: the re-estimation triggers LEM already emits ───────────────────
#
# Four of the seven land in `lem_machine_log` as ordinary rows. Machine
# replacement is not its own `kind`: retiring a machine is audited as
# `kind='config'` with `action='machine deleted'`, so the config kind covers it
# and `StaleTrigger.sentence` names the action rather than saying "config".
TRIGGER_KINDS = ("calibration", "pm", "config")
TRIGGER_REPLACE_BY = "replace_by"


# ── SOP 2.2: the contributions this module names for itself ──────────────────

CONTRIB_U_RW = "u(Rw)"
CONTRIB_U_BIAS = "u(bias)"
CONTRIB_SHORT_SERIES = "short series justification"
CONTRIB_DUPLICATES = "duplicate analyses (s_r)"


# ── the table ────────────────────────────────────────────────────────────────

COLUMNS = (
    "estimate_id", "machine_uid", "test_name", "sample_name",
    "window_start", "window_end",
    "n", "n_operators", "n_days", "n_calibrations", "spread_basis",
    "mean", "s", "s_df",
    "rw_route", "u_rw", "control_limit", "control_limit_k",
    "bias_route", "cert_value", "u_cref", "bias", "u_bias",
    "u_c", "k", "u_expanded",
    "astm_r", "r_ratio",
    "bias_decision", "contributions", "exclusions", "notes", "replace_by",
    "computed_at", "computed_by", "approved_at", "approved_by",
    "superseded_by",
)

UNCERTAINTY_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_uncertainty_estimates ("
    "estimate_id TEXT PRIMARY KEY, "
    "machine_uid TEXT NOT NULL, test_name TEXT NOT NULL, sample_name TEXT, "
    "window_start TEXT, window_end TEXT, "
    "n INTEGER, n_operators INTEGER, n_days INTEGER, n_calibrations INTEGER, "
    "spread_basis TEXT, "
    "mean REAL, s REAL, s_df INTEGER, "
    "rw_route TEXT, u_rw REAL, control_limit REAL, control_limit_k REAL, "
    "bias_route TEXT, cert_value REAL, u_cref REAL, bias REAL, u_bias REAL, "
    "u_c REAL, k REAL, u_expanded REAL, "
    "astm_r REAL, r_ratio REAL, "
    "bias_decision TEXT, contributions TEXT, exclusions TEXT, notes TEXT, "
    "replace_by TEXT, "
    "computed_at TEXT, computed_by TEXT, approved_at TEXT, approved_by TEXT, "
    "superseded_by TEXT)"
)


# ── SOP 2.10: the Register entry ─────────────────────────────────────────────
#
# ⚠ THE FIELD NAMES BELOW ARE DERIVED, NOT COPIED. The SOP is a draft on the lab
# share (`/Volumes/Labsharedrive/SOPs/...QMU 1.001...docx`) and is not readable
# from here; these twelve are built from the clause map in the design doc, one
# field per clause the map names, so every clause has somewhere to be answered.
# **Confirm them against the real 2.10 before the September assessment.**
REGISTER_FIELDS = {
    "measurand": "SOP 2.1 — the quantity, and that it needs an estimate",
    "instrument": "SOP 2.1 — the equipment this estimate belongs to",
    "control_material": "SOP 2.5 — the material the estimate was built on",
    "data_window": "SOP 2.4 — the results used, and what they span",
    "contributions":
        "SOP 2.2 — every contribution considered, negligible ones included",
    "u_rw": "SOP 2.4 — within-laboratory reproducibility, and the route to it",
    "u_bias": "SOP 2.5 — the bias term, or the reason there is none",
    "combined_and_expanded": "SOP 2.3 — u_c, k and U",
    "method_comparison": "SOP 2.7 — U against the method's published R",
    "bias_decision": "SOP 2.8 — a known bias corrected or carried",
    "exclusions": "SOP 2.9 — every point dropped, its cause and its NCR",
    "review": "SOP 2.11 — computed, approved, replaced, superseded",
}


# ── what this module raises ──────────────────────────────────────────────────

class UncertaintyStoreError(LabCoreError):
    """A store operation whose outcome LabCore did not confirm."""


class UncertaintyUnavailable(UncertaintyStoreError, LabCoreUnavailable):
    """LabCore could not be asked, so the register is UNKNOWN.

    Never `[]`. "No estimates on file" is a sentence an assessor acts on, and it
    must be impossible to produce from an outage — that is the difference
    between a laboratory that has not started and a laboratory whose evidence
    could not be read on the morning of the assessment.
    """


class UncertaintyRefused(UncertaintyStoreError, LabCoreRefused):
    """LabCore answered, and the estimate was not written."""


class EstimateRefused(ValueError):
    """The rules refuse this, and the refusal is the answer.

    Deliberately NOT a `LabCoreError`: nothing went wrong with the database. An
    exclusion with no cause, an approval of an estimate that is already
    approved, an estimate superseding itself — these are the module doing its
    job, and a route renders them as a sentence, not as a 502.
    """


class InsufficientEvidence(EstimateRefused):
    """The data does not support the estimate that was asked for.

    Carries `.route` so a caller can offer the route the evidence DOES permit
    instead of only reporting the one it does not.
    """

    def __init__(self, reason: str, route: str = "") -> None:
        self.route = route
        super().__init__(reason)


@contextmanager
def _doing(what: str):
    """Re-label `labcore_result`'s verdict with the operation that failed."""
    try:
        yield
    except UncertaintyStoreError:
        raise
    except LabCoreUnavailable as exc:
        raise UncertaintyUnavailable(
            "Could not {}: {}".format(what, exc)) from exc
    except LabCoreRefused as exc:
        # The ANSWER travels, not just the sentence: `busy` and `retry_after`
        # are the only things that tell a client whether to come back.
        raise UncertaintyRefused("Could not {}: {}".format(what, exc),
                                 getattr(exc, "result", None)) from exc


def _write(gateway, sql: str, args=None) -> dict:
    """Issue one write, turning a RAISED transport error into an ANSWER.

    The one place in this module that calls `gateway.sql`. `confirm_write`
    reads the answer but leaves the CALL bare, so a socket error — a write that
    equally did not happen — would escape as a raw OSError past every
    `except UncertaintyStoreError`.
    """
    try:
        return gateway.sql(sql, args or [])
    except Exception as exc:                       # transport, not logic
        return {"error": "{}: {}".format(type(exc).__name__, exc)}


def _read(gateway, sql: str, args=None) -> dict:
    try:
        return gateway.read_sql(sql, args or [])
    except Exception as exc:
        return {"error": "{}: {}".format(type(exc).__name__, exc)}


# ── the certificate, and the one number it is for ────────────────────────────

@dataclass(frozen=True)
class Certificate:
    """What a certified reference material's certificate STATES.

    `uncertainty` is the certificate's **expanded** U and `k` is the
    certificate's **own** coverage factor — usually 2, sometimes 1.96 or 3, and
    read off the certificate rather than assumed. `u(Cref) = uncertainty / k`.

    NOTHING BUILDS ONE OF THESE FROM A `QcSampleTest`. `std_dev` on a QC
    standard is a control limit: whoever set it may have taken it from the
    method's precision statement, from experience, or from the certificate, and
    nothing records which. Feeding it in here produces an answer that looks
    entirely reasonable and is wrong, which is worse than no answer at all.
    """

    # The CERTIFIED value. The design doc says it "falls back to `expected`";
    # it deliberately does not here, because `expected` is a field on a QC
    # standard and reaching into that record is the one move gap 1 forbids. No
    # certified value means no bias term, said out loud.
    value: Optional[float] = None
    uncertainty: Optional[float] = None  # expanded U from the certificate
    k: float = 2.0                       # the certificate's own coverage factor
    number: str = ""                     # certificate / COA identifier
    lot: str = ""
    expiry: str = ""                     # ISO date

    def u_cref(self) -> Optional[float]:
        """The standard uncertainty of the certified value, or None.

        None — never 0.0 — when the certificate states no uncertainty. A zero
        here would enter `u(bias)` as "the reference value is exact", which is
        the claim nobody is entitled to make.
        """
        if self.uncertainty is None or self.k in (None, 0):
            return None
        try:
            return abs(float(self.uncertainty)) / abs(float(self.k))
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    @classmethod
    def from_standard_uncertainty(cls, value: Optional[float],
                                  u_cref: Optional[float]) -> "Certificate":
        """Rebuild from what the record stores (`cert_value`, `u_cref`).

        The table keeps the STANDARD uncertainty, so replaying it needs k = 1 —
        the certificate's own k has already been divided out and re-applying it
        would divide twice. Used only by `UncertaintyStore.exclude`, which
        recomputes a frozen estimate's successor from the frozen row.
        """
        return cls(value=value, uncertainty=u_cref, k=1.0)


# ── exclusions (SOP 2.9) ─────────────────────────────────────────────────────

# Causes that are not causes. Whitespace is stripped out entirely before the
# comparison, so "> 3s" and ">3s" are one entry, and a trailing full stop goes
# too. This list is a backstop for the obvious spellings; the length floor below
# it is what catches the rest.
_NOT_A_CAUSE = frozenset((
    "outlier", "outliers", "anoutlier", "statisticaloutlier", "extreme",
    "extremevalue", "3s", ">3s", "<3s", "2s", ">2s", "beyond3s", "outofband",
    "outofrange", "toohigh", "toolow", "anomaly", "anomalous", "bad", "badrun",
    "fail", "failed", "failure", "wrong", "error", "n/a", "na", "none",
))

# A cause is a sentence about an investigation. Ten characters is not a quality
# bar — it is a floor under "outlier" and its relatives, and it is deliberately
# low enough that a real short cause ("vial empty") clears it.
_MIN_CAUSE = 10


@dataclass(frozen=True)
class Exclusion:
    """One point dropped from the estimate, with the reason it was dropped.

    TR 537 and SOP 2.9 agree and this module enforces it: **statistical
    extremity alone is not grounds.** A cause has to have been investigated and
    identified, and the exclusion has to be linkable to a nonconforming-work
    record — because if the excluded run represents work reported to a customer,
    clause 7.10 is engaged and somebody has a customer to talk to.
    """

    ts: str
    value: Optional[float] = None
    cause: str = ""
    ncr_ref: str = ""

    def to_dict(self) -> dict:
        return {"ts": self.ts, "value": self.value, "cause": self.cause,
                "ncr_ref": self.ncr_ref}

    @classmethod
    def from_dict(cls, data: dict) -> "Exclusion":
        return cls(ts=str(data.get("ts") or ""),
                   value=_float(data.get("value")),
                   cause=str(data.get("cause") or ""),
                   ncr_ref=str(data.get("ncr_ref") or ""))


def check_exclusion(exclusion: Exclusion) -> Exclusion:
    """Refuse an exclusion the SOP would not accept. Returns it if it stands."""
    if not str(exclusion.ts or "").strip():
        raise EstimateRefused(
            "An exclusion has to name the result it removes: give the "
            "timestamp of the QC row.")
    cause = str(exclusion.cause or "").strip().rstrip(".")
    folded = "".join(cause.split()).casefold()
    if not cause or folded in _NOT_A_CAUSE or len(cause) < _MIN_CAUSE:
        raise EstimateRefused(
            "SOP 2.9 and TR 537 both refuse this: a result may be excluded "
            "only when its cause has been INVESTIGATED and identified. "
            "Statistical extremity is not a cause. Record what was found "
            "({!r} is not it).".format(exclusion.cause))
    if not str(exclusion.ncr_ref or "").strip():
        raise EstimateRefused(
            "An exclusion needs a nonconforming-work reference. If the "
            "excluded run represents work reported to a customer, ISO/IEC "
            "17025 clause 7.10 is engaged — so the reference is how the two "
            "records stay linked.")
    return exclusion


@dataclass(frozen=True)
class ExclusionCandidate:
    """A point somebody might want to look at. NEVER a point this module drops."""

    ts: str
    value: float
    why: str


def exclusion_candidates(series: QcSeries,
                         k: float = 3.0) -> List[ExclusionCandidate]:
    """Results beyond `k`s of the series' own mean — FLAGGED, not removed.

    There is no automatic outlier rejection anywhere in this module. This
    function exists so that a person can be shown where to start looking, and
    every candidate it returns says in its own sentence that being extreme is
    not a reason.
    """
    limits = qc_series.control_limits([p.value for p in series.points])
    if limits is None:
        return []
    zone = limits.zone(k)
    if zone is None:
        return []
    low, high = zone
    found = []
    for point in series.points:
        if low <= point.value <= high:
            continue
        found.append(ExclusionCandidate(
            ts=point.ts, value=point.value,
            why="{} lies beyond {:g}s of this series' own mean. That is a "
                "place to look, NOT a reason: SOP 2.9 excludes a result only "
                "once its cause has been investigated and identified, with a "
                "nonconforming-work reference.".format(_num(point.value), k)))
    return found


# ── the arithmetic, on its own ───────────────────────────────────────────────
#
# Every function here is pure and takes numbers, so the formulas can be tested
# against values worked out on paper without a series, a route or a gateway in
# the way.

def _float(raw: Any) -> Optional[float]:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) or math.isinf(value) else value


def _num(value: Any) -> str:
    got = _float(value)
    return "—" if got is None else "{:.6g}".format(got)


def combine(*terms: Optional[float]) -> Optional[float]:
    """Root sum of squares over the terms that exist. SOP 2.3.

    A `None` term is a term that was not established and is skipped; it is not
    a zero, and the difference matters because `is_partial()` is what tells an
    assessor that the answer is one half of a budget rather than a whole one.
    """
    present = [abs(float(t)) for t in terms if t is not None]
    if not present:
        return None
    return math.sqrt(sum(t * t for t in present))


def expand(u_c: Optional[float], k: float = DEFAULT_K) -> Optional[float]:
    """U = k * u_c. SOP 2.3: k = 2 always unless justified."""
    return None if u_c is None else abs(float(k)) * u_c


def u_bias_single_crm(bias: Optional[float], s_squared: Optional[float],
                      n: int, u_cref: Optional[float]) -> Optional[float]:
    """u(bias) = sqrt(bias² + s²/n + u(Cref)²). SOP 2.5 Route B.

    `s²/n` is the uncertainty of the MEAN that the bias was measured from, and
    dropping it is a quiet 18% understatement on a five-point series. It is
    passed as `s_squared` rather than `s` so the caller cannot accidentally hand
    over `s` and have it squared twice.
    """
    if bias is None or u_cref is None:
        return None
    mean_term = None
    if s_squared is not None and n:
        mean_term = math.sqrt(abs(float(s_squared)) / float(n))
    return combine(bias, mean_term, u_cref)


def rms_bias(biases: Sequence[float]) -> Optional[float]:
    """RMS_bias = sqrt(Σbiasᵢ² / n_CRM). The multi-CRM case.

    None on an empty list, never 0.0 — no materials is no answer, and a zero
    bias is a claim.
    """
    values = [abs(float(b)) for b in biases or () if _float(b) is not None]
    if not values:
        return None
    return math.sqrt(sum(v * v for v in values) / len(values))


def u_bias_multi_crm(biases: Sequence[float],
                     u_cref: Optional[float]) -> Optional[float]:
    """u(bias) over several certified materials.

    `RMS_bias` replaces the single `bias` term **and the s²/n term is DROPPED** —
    the scatter between the materials already carries the mean's own
    uncertainty, and keeping both counts it twice.
    """
    rms = rms_bias(biases)
    if rms is None:
        return None
    return combine(rms, u_cref)


def r_ratio(u_expanded: Optional[float],
            astm_r: Optional[float]) -> Optional[float]:
    """r_ratio = U / (R / 1.39). SOP 2.7.

    **1.39, not sqrt(2).** R = 2.77·s_R, so a laboratory consistent with
    interlaboratory scatter lands near U = R/1.385, printed as R/1.39. The wrong
    relation is 1.7% away on a real series, which is inside a sloppy tolerance.
    """
    if u_expanded is None or astm_r in (None, 0):
        return None
    denominator = abs(float(astm_r)) / R_TO_U
    if not denominator:
        return None
    return abs(float(u_expanded)) / denominator


def control_limit_from_band(low: Optional[float],
                            high: Optional[float]) -> Optional[float]:
    """The control limit as a HALF-WIDTH, from the band the log carries.

    `lem_machine_log`'s QC detail records `low`/`high`; Route 3 wants the
    half-width. An inverted or incomplete band is no answer rather than a
    negative or a zero — `u(Rw) = 0` would report an instrument with no
    uncertainty at all.
    """
    low, high = _float(low), _float(high)
    if low is None or high is None or high <= low:
        return None
    return (high - low) / 2.0


def spread_label(basis: str) -> str:
    """What a spread on this `qc_series` basis is allowed to be called."""
    return _LABELS.get(basis, LABEL_UNATTRIBUTED)


# ── which routes the evidence permits (the honesty gate) ─────────────────────

@dataclass(frozen=True)
class RouteVerdict:
    """One route, whether the data permits it, and a sentence either way.

    The sentence is there when `permitted` is True as well: "you may use this,
    and here is what it rests on" is what somebody signs, and it is the same
    sentence the register entry carries.
    """

    route: str
    permitted: bool
    reason: str


def _span_days(series: QcSeries) -> int:
    dated = [p.at for p in series.points if p.at is not None]
    if len(dated) < 2:
        return 0
    return (max(dated).date() - min(dated).date()).days


def route_evidence(series: QcSeries, *, control_limit: Optional[float] = None,
                   s_r: Optional[float] = None,
                   now: Optional[datetime] = None
                   ) -> Dict[str, RouteVerdict]:
    """Which of SOP 2.4's three routes THIS series' evidence permits, and why.

    Ask before computing. Every route gets a verdict and a sentence, so a page
    can show a person what the laboratory is short of rather than only that the
    button is greyed out.
    """
    points = series.points
    cov = qc_series.coverage(points)
    n = len(points)
    span = _span_days(series)
    _, s = qc_series.mean_and_s([p.value for p in points])

    out: Dict[str, RouteVerdict] = {}

    # ── Route 1 ──────────────────────────────────────────────────────────
    if s is None:
        why = ("Route 1 is u(Rw) = s, and a series of {} result{} has no "
               "spread to be it.".format(n, "" if n == 1 else "s"))
        out[RW_CONTROL_SAMPLE] = RouteVerdict(RW_CONTROL_SAMPLE, False, why)
    elif not cov.supports_reproducibility():
        out[RW_CONTROL_SAMPLE] = RouteVerdict(
            RW_CONTROL_SAMPLE, False,
            "Route 1 is u(Rw) = s, and this spread is not a u(Rw): {} "
            "Collect duplicate-analysis data (Route 2), or use Route 3 in the "
            "meantime.".format(cov.caveat()))
    elif n <= TR537_MIN_RESULTS or span < TR537_MIN_SPAN_DAYS:
        out[RW_CONTROL_SAMPLE] = RouteVerdict(
            RW_CONTROL_SAMPLE, False,
            "TR 537 wants ideally more than {} results over at least {} days "
            "for the control-sample route. This series has {} results over {} "
            "calendar days, a span of {} days. Route 3 is the interim route, "
            "or record a written justification for accepting a short series."
            .format(TR537_MIN_RESULTS, TR537_MIN_SPAN_DAYS, n, cov.n_days,
                    span))
    else:
        out[RW_CONTROL_SAMPLE] = RouteVerdict(
            RW_CONTROL_SAMPLE, True,
            "{} results over {} calendar days, a span of {} days. {}".format(
                n, cov.n_days, span, cov.caveat()))

    # ── Route 2 ──────────────────────────────────────────────────────────
    if s is None:
        out[RW_CONTROL_PLUS_DUPLICATES] = RouteVerdict(
            RW_CONTROL_PLUS_DUPLICATES, False,
            "Route 2 is sqrt(s² + s_r²) and there is no s: a series of {} "
            "result{} has no spread.".format(n, "" if n == 1 else "s"))
    elif s_r is None:
        out[RW_CONTROL_PLUS_DUPLICATES] = RouteVerdict(
            RW_CONTROL_PLUS_DUPLICATES, False,
            "Route 2 needs an s_r from duplicate analyses on the real matrix. "
            "None has been supplied, and LEM does not collect duplicates.")
    elif not cov.supports_reproducibility():
        out[RW_CONTROL_PLUS_DUPLICATES] = RouteVerdict(
            RW_CONTROL_PLUS_DUPLICATES, False,
            "Route 2's control-sample half is still an s that has to be a "
            "u(Rw): {}".format(cov.caveat()))
    else:
        out[RW_CONTROL_PLUS_DUPLICATES] = RouteVerdict(
            RW_CONTROL_PLUS_DUPLICATES, True,
            "s from {} control results plus an s_r of {} from duplicate "
            "analyses.".format(n, _num(s_r)))

    # ── Route 3 ──────────────────────────────────────────────────────────
    limit = _float(control_limit)
    if limit is None or limit <= 0:
        out[RW_TARGET_LIMITS] = RouteVerdict(
            RW_TARGET_LIMITS, False,
            "Route 3 is u(Rw) = control_limit / 2 and no usable control limit "
            "was supplied. The limit is the half-width of the standard's pass "
            "band, in the units of the measurand.")
    else:
        horizon = ((now or datetime.now())
                   + timedelta(days=INTERIM_VALID_DAYS)).date().isoformat()
        out[RW_TARGET_LIMITS] = RouteVerdict(
            RW_TARGET_LIMITS, True,
            "Interim target-limits route: u(Rw) = {} / 2. Legitimate and "
            "explicitly permitted while the control-sample route has no data, "
            "and it carries a replacement date of {}.".format(
                _num(limit), horizon))

    return out


def preferred_route(series: QcSeries, *, control_limit: Optional[float] = None,
                    s_r: Optional[float] = None,
                    now: Optional[datetime] = None) -> Optional[str]:
    """The best route this series' evidence permits, or None.

    Preference is the SOP's own order — the control sample first, always, and
    the interim route last. **None is an answer**: no route being permitted is
    a fact somebody acts on, and returning the nearest thing that would compute
    is how a target limit comes to be reported as a measured reproducibility.
    """
    verdicts = route_evidence(series, control_limit=control_limit, s_r=s_r,
                              now=now)
    for route in RW_ROUTES:
        if verdicts[route].permitted:
            return route
    return None


# ── one frozen budget ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class UncertaintyEstimate:
    """One frozen budget. Mirrors `lem_uncertainty_estimates` 1:1.

    Frozen in both senses: the dataclass cannot be mutated, and the row is
    written once. A revision is a new estimate that supersedes this one.
    """

    machine_uid: str
    test_name: str
    estimate_id: str = ""
    sample_name: str = ""
    window_start: str = ""
    window_end: str = ""
    n: int = 0
    n_operators: int = 0
    n_days: int = 0
    n_calibrations: int = 0
    spread_basis: str = qc_series.BASIS_INSUFFICIENT
    mean: Optional[float] = None
    s: Optional[float] = None
    s_df: int = 0
    rw_route: str = RW_CONTROL_SAMPLE
    u_rw: Optional[float] = None
    control_limit: Optional[float] = None
    control_limit_k: Optional[float] = None
    bias_route: str = BIAS_NONE
    cert_value: Optional[float] = None
    u_cref: Optional[float] = None
    bias: Optional[float] = None
    u_bias: Optional[float] = None
    u_c: Optional[float] = None
    k: float = DEFAULT_K
    u_expanded: Optional[float] = None
    astm_r: Optional[float] = None
    r_ratio: Optional[float] = None
    bias_decision: str = BIAS_DECISION_UNDECIDED
    contributions: List[dict] = field(default_factory=list)
    exclusions: List[dict] = field(default_factory=list)
    notes: str = ""
    replace_by: str = ""
    computed_at: str = ""
    computed_by: str = ""
    approved_at: str = ""
    approved_by: str = ""
    superseded_by: str = ""

    # ── what the spread is, and what it may be called ─────────────────────

    def is_reproducibility(self) -> bool:
        """Is `u_rw` a within-laboratory reproducibility?

        False whenever the series is single-operator, single-day or
        single-calibration — the answer is `qc_series.Coverage`'s, frozen onto
        the record at compute time, not a second opinion formed on re-read.

        Also False on Route 3, whatever the coverage: that number came from a
        control limit somebody set, so it is a TARGET u(Rw) and not evidence of
        anything this instrument did.
        """
        return (self.rw_route in MEASURED_RW_ROUTES
                and self.spread_basis == qc_series.BASIS_INTERMEDIATE)

    @property
    def spread_label(self) -> str:
        """What the SERIES' own `s` is: `u(Rw)`, `s_r`, or neither."""
        return spread_label(self.spread_basis)

    @property
    def u_rw_label(self) -> str:
        """What the `u_rw` NUMBER is. Not always the same question."""
        if self.rw_route == RW_TARGET_LIMITS:
            return LABEL_U_RW_TARGET
        return self.spread_label

    @property
    def s_r(self) -> Optional[float]:
        """Route 2's duplicates term, recovered exactly from what is stored."""
        if self.rw_route != RW_CONTROL_PLUS_DUPLICATES:
            return None
        if self.u_rw is None or self.s is None:
            return None
        gap = self.u_rw * self.u_rw - self.s * self.s
        return math.sqrt(gap) if gap > 0 else None

    # ── what is NOT on this budget ────────────────────────────────────────

    @property
    def missing_terms(self) -> Dict[str, str]:
        """Every term this budget does not carry, and the sentence saying why.

        Derived from the stored fields rather than stored itself, so an estimate
        read back years later says the same thing it said the day it was
        approved. An empty dict is a complete budget.
        """
        out: Dict[str, str] = {}
        if self.u_bias is None:
            short_of = ("no certified value is bound to this standard"
                        if self.cert_value is None else
                        "the certified value {} is on file but its stated "
                        "uncertainty is not".format(_num(self.cert_value)))
            out[CONTRIB_U_BIAS] = (
                "u(bias) is not established. It needs the certificate's "
                "expanded uncertainty and the certificate's own coverage "
                "factor — u(Cref) = U_cert / k_cert — and {}. A QC standard's "
                "control limit is a different quantity and is deliberately "
                "not used for it. Until the certificate is bound, this is the "
                "repeatability half of the budget only.".format(short_of))
        if self.r_ratio is None:
            out["r_ratio"] = (
                "SOP 2.7 compares U against the method's published "
                "reproducibility R; none was supplied, so the comparison has "
                "not been made.")
        if self.rw_route == RW_TARGET_LIMITS:
            if self.control_limit_k is None:
                out["control_limit_k"] = (
                    "The coverage factor the control limit was set at is not "
                    "recorded. Route 3 reads the limit as a 2s bound; if it "
                    "was set at some other k, u(Rw) is wrong by that ratio.")
            elif abs(self.control_limit_k - 2.0) > 1e-9:
                out["control_limit_k"] = (
                    "The control limit was set at k = {:g}, not k = 2. Route 3 "
                    "reads the limit as a 2s bound, so u(Rw) = limit / 2 will "
                    "understate the real spread by a factor of {:g} until the "
                    "limit is restated at k = 2.".format(
                        self.control_limit_k, 2.0 / self.control_limit_k))
        return out

    def is_partial(self) -> bool:
        """Is this one half of a budget rather than a whole one?"""
        return bool(self.missing_terms)

    # ── persistence ───────────────────────────────────────────────────────

    def to_row(self) -> Dict[str, Any]:
        """The DB shape: one key per column, lists as JSON TEXT."""
        row = {name: getattr(self, name) for name in COLUMNS}
        row["contributions"] = json.dumps(self.contributions)
        row["exclusions"] = json.dumps(self.exclusions)
        return row

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "UncertaintyEstimate":
        def _text(name):
            return str(row.get(name) or "")

        def _int(name):
            got = _float(row.get(name))
            return 0 if got is None else int(got)

        return cls(
            estimate_id=_text("estimate_id"),
            machine_uid=_text("machine_uid"), test_name=_text("test_name"),
            sample_name=_text("sample_name"),
            window_start=_text("window_start"), window_end=_text("window_end"),
            n=_int("n"), n_operators=_int("n_operators"),
            n_days=_int("n_days"), n_calibrations=_int("n_calibrations"),
            spread_basis=_text("spread_basis") or qc_series.BASIS_INSUFFICIENT,
            mean=_float(row.get("mean")), s=_float(row.get("s")),
            s_df=_int("s_df"),
            rw_route=_text("rw_route"), u_rw=_float(row.get("u_rw")),
            control_limit=_float(row.get("control_limit")),
            control_limit_k=_float(row.get("control_limit_k")),
            bias_route=_text("bias_route") or BIAS_NONE,
            cert_value=_float(row.get("cert_value")),
            u_cref=_float(row.get("u_cref")), bias=_float(row.get("bias")),
            u_bias=_float(row.get("u_bias")), u_c=_float(row.get("u_c")),
            k=_float(row.get("k")) or DEFAULT_K,
            u_expanded=_float(row.get("u_expanded")),
            astm_r=_float(row.get("astm_r")),
            r_ratio=_float(row.get("r_ratio")),
            bias_decision=_text("bias_decision") or BIAS_DECISION_UNDECIDED,
            contributions=_json_list(row.get("contributions")),
            exclusions=_json_list(row.get("exclusions")),
            notes=_text("notes"), replace_by=_text("replace_by"),
            computed_at=_text("computed_at"), computed_by=_text("computed_by"),
            approved_at=_text("approved_at"), approved_by=_text("approved_by"),
            superseded_by=_text("superseded_by"))

    def to_dict(self) -> Dict[str, Any]:
        """The JSON shape a route renders: every field, lists as lists."""
        out = {name: getattr(self, name) for name in COLUMNS}
        out["u_rw_label"] = self.u_rw_label
        out["spread_label"] = self.spread_label
        out["is_reproducibility"] = self.is_reproducibility()
        out["missing_terms"] = self.missing_terms
        out["is_partial"] = self.is_partial()
        return out

    # ── SOP 2.10 ──────────────────────────────────────────────────────────

    def to_register_row(self) -> Dict[str, str]:
        """The twelve-field Register entry, ready to render or export.

        Every field is answered. A term that does not exist says so in words —
        an assessor reading a blank cell cannot tell "not applicable" from "we
        did not do this part", and the whole reason this module refuses to
        invent a bias term is so that the difference stays visible.
        """
        cov = ("{} result{} · {} analyst{} · {} calendar day{} · {} "
               "calibration{}".format(
                   self.n, "" if self.n == 1 else "s",
                   self.n_operators or "no named", "" if self.n_operators == 1 else "s",
                   self.n_days, "" if self.n_days == 1 else "s",
                   self.n_calibrations or "no named",
                   "" if self.n_calibrations == 1 else "s"))

        u_rw = "{} = {} (route: {})".format(
            self.u_rw_label, _num(self.u_rw), self.rw_route)
        if self.rw_route == RW_TARGET_LIMITS:
            u_rw += "; control limit {} at k = {}".format(
                _num(self.control_limit),
                _num(self.control_limit_k) if self.control_limit_k is not None
                else "unrecorded")
        if self.u_rw_label != self.spread_label:
            u_rw += ("; the series' own spread is {} = {} over {} results"
                     .format(self.spread_label, _num(self.s), self.n))
        justification = self._contribution(CONTRIB_SHORT_SERIES)
        if justification:
            u_rw += "; short-series justification: {}".format(justification)
        # Spec gap 3's own prescription, on whichever route produced the
        # number: when the series' spread is not a u(Rw), the entry says so and
        # says what would complete it. It is on the interim route that this
        # matters most — the target is a number somebody set, and the only
        # measured thing on the entry is a spread that is NOT a
        # within-laboratory reproducibility.
        if self.s is not None and self.spread_basis != qc_series.BASIS_INTERMEDIATE:
            u_rw += ("; the measured spread is NOT a within-laboratory "
                     "reproducibility — duplicate-analysis data across "
                     "analysts, days and calibrations is needed to complete it")

        if self.u_bias is None:
            u_bias = self.missing_terms.get(CONTRIB_U_BIAS, "Not established.")
        else:
            u_bias = ("u(bias) = {} (route: {}); bias = {}, u(Cref) = {}, "
                      "certified value {}".format(
                          _num(self.u_bias), self.bias_route, _num(self.bias),
                          _num(self.u_cref), _num(self.cert_value)))

        if self.r_ratio is None:
            method = self.missing_terms.get("r_ratio", "Not compared.")
        else:
            method = "R = {}; r_ratio = U / (R / 1.39) = {} — {}".format(
                _num(self.astm_r), _num(self.r_ratio), self.r_ratio_sentence)

        review = "Computed {} by {}".format(
            self.computed_at or "—", self.computed_by or "—")
        review += ("; approved {} by {}".format(self.approved_at,
                                                self.approved_by)
                   if self.approved_at else "; NOT YET APPROVED (draft)")
        if self.replace_by:
            review += "; interim — replacement date {}".format(self.replace_by)
        if self.superseded_by:
            review += "; superseded by {}".format(self.superseded_by)

        return {
            "measurand": self.test_name or "not recorded",
            "instrument": self.machine_uid or "not recorded",
            "control_material": self.sample_name or "not recorded",
            "data_window": "{} to {} · {}".format(
                self.window_start or "—", self.window_end or "—", cov),
            "contributions": "; ".join(
                "{}: {} ({})".format(
                    c.get("name"),
                    "included" if c.get("included") else "considered, not "
                                                         "included",
                    c.get("basis") or "no basis recorded")
                for c in self.contributions) or "none recorded",
            "u_rw": u_rw,
            "u_bias": u_bias,
            "combined_and_expanded": "u_c = {}; k = {:g}; U = {}{}".format(
                _num(self.u_c), self.k, _num(self.u_expanded),
                # Not "repeatability half": on Route 3 the u(Rw) term is a
                # target, not a measured repeatability, and the phrase would
                # claim a measurement on the one route that makes none.
                " — the u(Rw) half only, with no bias term; see u_bias"
                if self.u_bias is None else ""),
            "method_comparison": method,
            "bias_decision": self.bias_decision or BIAS_DECISION_UNDECIDED,
            "exclusions": "; ".join(
                "{} = {}: {} [{}]".format(
                    e.get("ts"), _num(e.get("value")), e.get("cause"),
                    e.get("ncr_ref"))
                for e in self.exclusions) or "none",
            "review": review,
        }

    def _contribution(self, name: str) -> str:
        for entry in self.contributions:
            if entry.get("name") == name:
                return str(entry.get("basis") or "")
        return ""

    # ── SOP 2.7's verdict ─────────────────────────────────────────────────

    @property
    def r_ratio_verdict(self) -> Optional[str]:
        if self.r_ratio is None:
            return None
        if self.r_ratio >= R_RATIO_HIGH_AT:
            return R_RATIO_HIGH
        if self.r_ratio <= R_RATIO_LOW_AT:
            return R_RATIO_LOW
        return R_RATIO_CONSISTENT

    @property
    def r_ratio_sentence(self) -> str:
        verdict = self.r_ratio_verdict
        if verdict is None:
            return self.missing_terms.get("r_ratio", "")
        if verdict == R_RATIO_HIGH:
            return ("U is well above what the method's own reproducibility "
                    "implies: this points at a bias or a control problem. "
                    "Refer to SOP 2.9 before approving.")
        if verdict == R_RATIO_LOW:
            return ("U is well below what the method's own reproducibility "
                    "implies: the input data is probably not capturing the "
                    "real variability. Refer it back before approving.")
        return ("U is consistent with the scatter the method's published "
                "reproducibility describes.")


def _json_list(raw: Any) -> List[dict]:
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict)]
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [dict(item) for item in parsed
            if isinstance(parsed, list) and isinstance(item, dict)]


# ── computing one ────────────────────────────────────────────────────────────

def _apply_exclusions(series: QcSeries,
                      exclusions: Sequence[Exclusion]
                      ) -> Tuple[QcSeries, List[str]]:
    """Drop the excluded points. Returns the survivors and what matched nothing.

    Matched on the timestamp, which is what the log keys a result by. An
    exclusion that matches nothing in THIS window is kept on the record anyway
    — it is history, and a narrower window is not a reason to forget that a
    result was investigated and removed — but it is reported so the sentence
    can say so.
    """
    drop = {str(e.ts) for e in exclusions}
    kept = tuple(p for p in series.points if p.ts not in drop)
    matched = {p.ts for p in series.points if p.ts in drop}
    unmatched = sorted(drop - matched)
    return replace(series, points=kept), unmatched


def compute_from_series(
        series: QcSeries, *,
        rw_route: str = RW_CONTROL_SAMPLE,
        certificate: Optional[Certificate] = None,
        astm_r: Optional[float] = None,
        control_limit: Optional[float] = None,
        control_limit_k: Optional[float] = None,
        s_r: Optional[float] = None,
        s_r_n: Optional[int] = None,
        exclusions: Iterable = (),
        bias_decision: str = BIAS_DECISION_UNDECIDED,
        short_series_justification: str = "",
        replace_by: Optional[str] = None,
        interim_days: int = INTERIM_VALID_DAYS,
        contributions: Iterable[dict] = (),
        notes: str = "",
        sample_name: str = "",
        k: float = DEFAULT_K,
        now: Optional[datetime] = None) -> UncertaintyEstimate:
    """One budget for one (machine, test), or a refusal saying what is short.

    Pure: no gateway, no clock beyond the `now` handed in. Every refusal is an
    `InsufficientEvidence` carrying the route it refused, so a caller can offer
    the route the data DOES permit.
    """
    now = now or datetime.now()

    # ── exclusions first: they change n, and n changes everything ─────────
    parsed = [check_exclusion(e if isinstance(e, Exclusion)
                              else Exclusion.from_dict(e))
              for e in exclusions or ()]
    seen: Dict[str, None] = {}
    for one in parsed:
        if one.ts in seen:
            raise EstimateRefused(
                "The result at {} is already excluded from this estimate. An "
                "exclusion is recorded once, with one cause and one "
                "nonconforming-work reference.".format(one.ts))
        seen[one.ts] = None
    kept, unmatched = _apply_exclusions(series, parsed)

    points = kept.points
    cov = qc_series.coverage(points)
    n = len(points)
    mean, s = qc_series.mean_and_s([p.value for p in points])

    # ── SOP 2.4: u(Rw) by the route asked for, or a refusal ───────────────
    if rw_route not in RW_ROUTES:
        raise InsufficientEvidence(
            "{!r} is not one of SOP 2.4's routes ({}).".format(
                rw_route, ", ".join(RW_ROUTES)), rw_route)

    verdicts = route_evidence(kept, control_limit=control_limit, s_r=s_r,
                              now=now)
    verdict = verdicts[rw_route]
    if not verdict.permitted:
        # ONE exception, and it is a judgement a technical manager is entitled
        # to make: a series that clears every coverage gate and is only SHORT
        # may be accepted with a written justification, which is then recorded
        # on the estimate. Nothing else can be justified past — whether a
        # spread spans analysts is a fact about the log, and no sentence
        # changes it.
        short_only = (
            rw_route == RW_CONTROL_SAMPLE and s is not None
            and cov.supports_reproducibility()
            and str(short_series_justification or "").strip())
        if not short_only:
            raise InsufficientEvidence(verdict.reason, rw_route)

    if rw_route == RW_CONTROL_SAMPLE:
        u_rw = s
        rw_basis = "Route 1, control sample: u(Rw) = s over {} results.".format(n)
    elif rw_route == RW_CONTROL_PLUS_DUPLICATES:
        u_rw = combine(s, s_r)
        rw_basis = ("Route 2, control sample plus duplicates: "
                    "u(Rw) = sqrt(s² + s_r²), s_r = {} from {} duplicate "
                    "analyses.".format(_num(s_r), s_r_n or "an unrecorded "
                                                           "number of"))
    else:
        u_rw = abs(float(control_limit)) / 2.0
        rw_basis = ("Route 3, interim target limits: u(Rw) = control limit / 2 "
                    "= {} / 2. Replaced once the control-sample route has "
                    "data.".format(_num(control_limit)))

    # ── SOP 2.5: the bias half, or the reason there is none ───────────────
    cert = certificate or Certificate()
    u_cref = cert.u_cref()
    cert_value = _float(cert.value)
    bias = u_bias = None
    bias_route = BIAS_NONE
    if u_cref is not None and cert_value is not None and mean is not None:
        bias = mean - cert_value
        u_bias = u_bias_single_crm(
            bias, None if s is None else s * s, n, u_cref)
        bias_route = BIAS_CRM
    # Otherwise the bias term is simply not established. Not an error and not a
    # silence: `bias_route` stays 'none', `missing_terms` carries the sentence,
    # `is_partial()` is True, and the register entry SAYS the budget is the
    # repeatability half only.

    # ── SOP 2.3 and 2.7 ───────────────────────────────────────────────────
    u_c = combine(u_rw, u_bias)
    u_expanded = expand(u_c, k)
    ratio = r_ratio(u_expanded, astm_r)

    # ── SOP 2.4 Route 3: the replacement date ─────────────────────────────
    stamp = ""
    if rw_route == RW_TARGET_LIMITS:
        stamp = (str(replace_by).strip() if replace_by
                 else (now + timedelta(days=int(interim_days))).date()
                 .isoformat())

    # ── SOP 2.2: the contributions considered, negligible ones included ───
    considered: List[dict] = [
        {"name": CONTRIB_U_RW, "included": True, "value": u_rw,
         "basis": rw_basis, "auto": True},
        {"name": CONTRIB_U_BIAS, "included": u_bias is not None,
         "value": u_bias,
         "basis": ("Certificate {} — u(Cref) = U/k = {}".format(
             cert.number or "(unnumbered)", _num(u_cref))
             if u_bias is not None else
             "No certificate uncertainty is bound to this standard, so the "
             "bias term is not established. A QC standard's control limit is a "
             "different quantity and is not used for it."),
         "auto": True},
    ]
    if rw_route == RW_CONTROL_PLUS_DUPLICATES:
        considered.append({"name": CONTRIB_DUPLICATES, "included": True,
                           "value": s_r, "n": s_r_n,
                           "basis": "Duplicate analyses on the real matrix.",
                           "auto": True})
    if str(short_series_justification or "").strip():
        considered.append({
            "name": CONTRIB_SHORT_SERIES, "included": True, "value": None,
            "basis": str(short_series_justification).strip(), "auto": True})
    considered.extend(dict(c) for c in contributions or ()
                      if isinstance(c, dict))

    # ── the notes an assessor reads ───────────────────────────────────────
    lines = [cov.caveat()]
    # The notes and `missing_terms` must never disagree, and the only way to
    # guarantee that is for the notes to be BUILT from the property rather than
    # written alongside it. A throwaway estimate carrying just the fields that
    # property reads is cheaper than a second copy of its wording.
    draft = UncertaintyEstimate(
        machine_uid=series.machine_uid, test_name=series.test_name,
        rw_route=rw_route, u_bias=u_bias, r_ratio=ratio,
        cert_value=cert_value, u_cref=u_cref,
        control_limit=_float(control_limit),
        control_limit_k=_float(control_limit_k))
    lines.extend(draft.missing_terms.values())
    if str(short_series_justification or "").strip():
        lines.append("Short series accepted for Route 1: {}".format(
            str(short_series_justification).strip()))
    if unmatched:
        lines.append(
            "Carried from an earlier estimate but outside this window: {}."
            .format(", ".join(unmatched)))
    if str(notes or "").strip():
        lines.append(str(notes).strip())

    # THE WINDOW IS THE RESULTS USED, NOT THE WINDOW ASKED FOR.
    #
    # Two reasons, and the second is the load-bearing one. An assessor's
    # question is "from WHICH results" — a requested window of "this quarter"
    # over a series that started in August answers it worse than the span of
    # the results themselves. And these two strings are the log's own `ts`
    # values verbatim, so `UncertaintyStore.exclude` can reproduce exactly this
    # set of points by an INCLUSIVE string filter when it recomputes a
    # successor. Storing a requested half-open bound instead is what dropped
    # the final result of every recomputation: `qc_series.window` is `[start,
    # end)` and the stored end IS the last result.
    stamps = sorted(p.ts for p in points if p.at is not None)
    return UncertaintyEstimate(
        estimate_id=uuid.uuid4().hex,
        machine_uid=series.machine_uid, test_name=series.test_name,
        sample_name=sample_name or series.sample_id,
        window_start=stamps[0] if stamps else "",
        window_end=stamps[-1] if stamps else "",
        n=n, n_operators=cov.n_operators, n_days=cov.n_days,
        n_calibrations=cov.n_calibrations, spread_basis=cov.basis,
        mean=mean, s=s, s_df=(n - 1) if s is not None else 0,
        rw_route=rw_route, u_rw=u_rw,
        control_limit=_float(control_limit),
        control_limit_k=_float(control_limit_k),
        bias_route=bias_route, cert_value=cert_value, u_cref=u_cref,
        bias=bias, u_bias=u_bias,
        u_c=u_c, k=float(k), u_expanded=u_expanded,
        astm_r=_float(astm_r), r_ratio=ratio,
        bias_decision=bias_decision or BIAS_DECISION_UNDECIDED,
        contributions=considered,
        exclusions=[e.to_dict() for e in parsed],
        notes=" ".join(line for line in lines if line),
        replace_by=stamp,
        computed_at=now.isoformat(timespec="seconds"))


# ── one stale estimate, and why ──────────────────────────────────────────────

@dataclass(frozen=True)
class StaleTrigger:
    """An SOP 2.11 trigger that has fired since an estimate was computed."""

    estimate_id: str
    machine_uid: str
    test_name: str
    trigger: str
    at: str
    sentence: str


# ── the store ────────────────────────────────────────────────────────────────

class UncertaintyStore:
    """Owns `lem_uncertainty_estimates`. Never recomputes a stored estimate.

    House pattern: injected gateway, no raw DB, every write judged by
    `confirm_write`, every read that FAILED raised rather than answered as
    empty. The one error a read may swallow is "no such table" — nobody has
    computed an estimate yet, which is a different fact from "the register could
    not be read" and the only one an assessor should ever see as emptiness.
    """

    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self._schema_ready = False

    # ── schema ────────────────────────────────────────────────────────────

    def ensure_schema(self) -> None:
        """Declare the table, or say why it might not be there.

        `_schema_ready` latches only after the CREATE is ACKNOWLEDGED: caching
        it on an unread answer would remember a refused CREATE as done and send
        every later save into a table that is not there.

        A READ declares nothing. `CREATE TABLE IF NOT EXISTS` goes through the
        same queue as everything else, so declaring from a read means a full
        WRITE queue takes down a read-only page for a table that has existed
        for months, while adding to the congestion.
        """
        if self._schema_ready:
            return
        with _doing("create lem_uncertainty_estimates"):
            confirm_write(_write(self.gateway, UNCERTAINTY_DDL))
        self._schema_ready = True

    # ── the input ─────────────────────────────────────────────────────────

    def read_series(self, machine_uid: str, test_name: str,
                    window_start: Optional[datetime] = None,
                    window_end: Optional[datetime] = None) -> QcSeries:
        """The QC runs for one (machine, test), as `qc_series` parses them.

        `ORDER BY ts, rowid`: `lem_machine_log` has a same-second tie-break
        problem since it was indexed (`_audit` stamps to whole seconds), and two
        reporting queries in this tree already say exactly this for exactly that
        reason.
        """
        res = _read(
            self.gateway,
            "SELECT machine_uid, ts, kind, lab_id, test_name, value, detail "
            "FROM lem_machine_log "
            "WHERE kind = 'qc' AND machine_uid = ? AND test_name = ? "
            "ORDER BY ts, rowid", [machine_uid, test_name])
        with _doing("read the QC log for {!r} on {!r}".format(
                test_name, machine_uid)):
            listed = rows(res)
        found = qc_series.series_for(listed, machine_uid, test_name)
        return qc_series.window(found, window_start, window_end)

    def compute(self, machine_uid: str, test_name: str,
                window_start: Optional[datetime] = None,
                window_end: Optional[datetime] = None,
                rw_route: str = RW_CONTROL_SAMPLE,
                **kw) -> UncertaintyEstimate:
        """Read the series and compute a DRAFT. Writes nothing, approves nothing."""
        series = self.read_series(machine_uid, test_name, window_start,
                                  window_end)
        return compute_from_series(series, rw_route=rw_route, **kw)

    # ── writing one, once ─────────────────────────────────────────────────

    def save(self, est: UncertaintyEstimate, computed_by: str) -> str:
        """INSERT the frozen row. Returns its id.

        INSERT and nothing else — no upsert, no fallback to an UPDATE. Saving an
        estimate whose id is already on file is refused by the primary key, and
        that refusal is the point: an estimate is written once and revised by
        supersession.
        """
        self.ensure_schema()
        row = replace(est, computed_by=str(computed_by or "")).to_row()
        placeholders = ", ".join("?" for _ in COLUMNS)
        with _doing("save the uncertainty estimate for {!r} on {!r}".format(
                est.test_name, est.machine_uid)):
            confirm_write(_write(
                self.gateway,
                "INSERT INTO lem_uncertainty_estimates ({}) VALUES ({})".format(
                    ", ".join(COLUMNS), placeholders),
                [row[name] for name in COLUMNS]))
        return est.estimate_id

    def approve(self, estimate_id: str, approved_by: str,
                when: Optional[datetime] = None) -> None:
        """Record who signed it and when. The only other thing a row may gain.

        Refused on an estimate that is already approved: a signature is a
        person's act, and overwriting one is the one kind of mutation this
        record must never allow.
        """
        existing = self.get(estimate_id)
        if existing is None:
            raise EstimateRefused(
                "There is no estimate {!r} to approve.".format(estimate_id))
        if existing.approved_at:
            raise EstimateRefused(
                "Estimate {!r} was already approved by {} on {}. An approval "
                "is not overwritten; supersede the estimate instead.".format(
                    estimate_id, existing.approved_by, existing.approved_at))
        stamp = (when or datetime.now()).isoformat(timespec="seconds")
        with _doing("approve estimate {!r}".format(estimate_id)):
            confirm_write(_write(
                self.gateway,
                "UPDATE lem_uncertainty_estimates SET approved_at = ?, "
                "approved_by = ? WHERE estimate_id = ?",
                [stamp, str(approved_by or ""), estimate_id]))

    def supersede(self, old_id: str, new_id: str) -> None:
        """Point the old estimate at the one that replaces it.

        The only revision mechanism there is. Both must exist — a
        `superseded_by` naming nothing takes the current estimate off the
        register and leaves nothing in its place, which reads as a laboratory
        that stopped estimating.
        """
        if old_id == new_id:
            raise EstimateRefused(
                "An estimate cannot supersede itself.")
        if self.get(old_id) is None:
            raise EstimateRefused(
                "There is no estimate {!r} to supersede.".format(old_id))
        if self.get(new_id) is None:
            raise EstimateRefused(
                "There is no estimate {!r} to supersede it WITH. Save the "
                "replacement first.".format(new_id))
        with _doing("supersede estimate {!r}".format(old_id)):
            confirm_write(_write(
                self.gateway,
                "UPDATE lem_uncertainty_estimates SET superseded_by = ? "
                "WHERE estimate_id = ?", [new_id, old_id]))

    # ── reading them back ─────────────────────────────────────────────────

    def _select(self, where: str, args: Sequence[Any],
                what: str) -> List[UncertaintyEstimate]:
        res = _read(
            self.gateway,
            "SELECT {} FROM lem_uncertainty_estimates {}".format(
                ", ".join(COLUMNS), where), list(args))
        with _doing(what):
            listed = rows(res)
        return [UncertaintyEstimate.from_row(r) for r in listed]

    def get(self, estimate_id: str) -> Optional[UncertaintyEstimate]:
        found = self._select("WHERE estimate_id = ?", [estimate_id],
                             "read estimate {!r}".format(estimate_id))
        return found[0] if found else None

    def history_for(self, machine_uid: str,
                    test_name: str) -> List[UncertaintyEstimate]:
        """Every estimate ever made for this (machine, test), newest first.

        `computed_at, rowid` — several revisions of one estimate can share a
        second (an exclusion recomputes immediately), and an assessor walking
        the chain backwards needs the order to be the order they were written.
        """
        return self._select(
            "WHERE machine_uid = ? AND test_name = ? "
            "ORDER BY computed_at DESC, rowid DESC",
            [machine_uid, test_name],
            "read the estimate history for {!r} on {!r}".format(
                test_name, machine_uid))

    def predecessors(self, estimate_id: str) -> List[UncertaintyEstimate]:
        """Walk backwards: what this estimate replaced, and what that replaced.

        SOP 2.11's whole point — "how do you know this is still current" is
        answered by a chain somebody can read, not by a memory.
        """
        found = self.get(estimate_id)
        if found is None:
            return []
        chain = self.history_for(found.machine_uid, found.test_name)
        by_successor = {e.superseded_by: e for e in chain if e.superseded_by}
        out, cursor, guard = [], estimate_id, len(chain) + 1
        while cursor in by_successor and guard > 0:
            previous = by_successor[cursor]
            out.append(previous)
            cursor, guard = previous.estimate_id, guard - 1
        return out

    def current_for(self, machine_uid: str,
                    test_name: str) -> Optional[UncertaintyEstimate]:
        """The approved, unsuperseded estimate in force. A draft is not one."""
        found = self._select(
            "WHERE machine_uid = ? AND test_name = ? AND approved_at <> '' "
            "AND (superseded_by IS NULL OR superseded_by = '') "
            "ORDER BY computed_at DESC, rowid DESC",
            [machine_uid, test_name],
            "read the current estimate for {!r} on {!r}".format(
                test_name, machine_uid))
        return found[0] if found else None

    def list_current(self) -> List[UncertaintyEstimate]:
        """Every estimate in force, by machine and test. The register page."""
        return self._select(
            "WHERE approved_at <> '' "
            "AND (superseded_by IS NULL OR superseded_by = '') "
            "ORDER BY machine_uid, test_name, computed_at DESC, rowid DESC",
            [], "read the uncertainty register")

    # ── revising one ──────────────────────────────────────────────────────

    def exclude(self, estimate_id: str, exclusion: Exclusion,
                computed_by: str, now: Optional[datetime] = None,
                **overrides) -> UncertaintyEstimate:
        """Add an exclusion, recompute as a NEW estimate, supersede the old.

        Never in place. The old row's numbers are untouched and its
        `superseded_by` is the only thing that changes, so the estimate that was
        approved last quarter still says exactly what it said when it was
        approved.

        The successor is a DRAFT: an approval belongs to the numbers it was
        given, and inheriting one across a recomputation would put a signature
        on a budget nobody signed.
        """
        old = self.get(estimate_id)
        if old is None:
            raise EstimateRefused(
                "There is no estimate {!r} to exclude a result from.".format(
                    estimate_id))
        check_exclusion(exclusion)

        kwargs = self._replay(old)
        kwargs.update(overrides)
        kwargs["exclusions"] = list(old.exclusions) + [exclusion.to_dict()]
        # INCLUSIVE, on the log's own `ts` strings, not `qc_series.window`.
        # The stored bounds ARE the first and last result the old estimate
        # used, and `window` is half-open — replaying through it silently drops
        # the final result, so every recomputation would come back one point
        # short and no test of the arithmetic would notice.
        series = _within(self.read_series(old.machine_uid, old.test_name),
                         old.window_start, old.window_end)
        fresh = compute_from_series(series, now=now, **kwargs)
        self.save(fresh, computed_by=computed_by)
        self.supersede(estimate_id, fresh.estimate_id)
        return fresh

    @staticmethod
    def _replay(old: UncertaintyEstimate) -> Dict[str, Any]:
        """Every input the frozen row was computed from, recovered from the row.

        A successor has to be the same budget with one result removed, so the
        route, the certificate, the method's R and the caller's own
        contributions all come back off the record rather than being asked for
        again. Anything the caller genuinely wants to change is an explicit
        `overrides` on `exclude`.

        The replacement date is deliberately NOT replayed: a new interim
        estimate starts its own year.
        """
        cert = None
        if old.u_cref is not None:
            cert = Certificate.from_standard_uncertainty(old.cert_value,
                                                         old.u_cref)
        justification = old._contribution(CONTRIB_SHORT_SERIES)
        s_r = s_r_n = None
        for entry in old.contributions:
            if entry.get("name") == CONTRIB_DUPLICATES:
                s_r, s_r_n = _float(entry.get("value")), entry.get("n")
        return {
            "rw_route": old.rw_route, "certificate": cert,
            "astm_r": old.astm_r, "control_limit": old.control_limit,
            "control_limit_k": old.control_limit_k, "s_r": s_r,
            "s_r_n": s_r_n, "k": old.k, "bias_decision": old.bias_decision,
            "short_series_justification": justification,
            "sample_name": old.sample_name,
            "contributions": [dict(c) for c in old.contributions
                              if not c.get("auto")],
        }

    # ── SOP 2.11 ──────────────────────────────────────────────────────────

    def stale(self, now: Optional[datetime] = None) -> List[StaleTrigger]:
        """Every estimate in force that a 2.11 trigger has overtaken.

        One read of the log, bounded by the oldest estimate on the register and
        by the three kinds that matter, so it uses `idx_lem_log_uid_kind_ts`
        rather than scanning. This is a page nobody polls; it is deliberately
        not an arm.

        A read that failed raises. "Nothing is stale" is the answer somebody
        acts on when an assessor asks how they know the register is current, and
        it must be impossible to produce from an outage.
        """
        now = now or datetime.now()
        current = self.list_current()
        if not current:
            return []

        oldest = min(e.computed_at for e in current)
        res = _read(
            self.gateway,
            "SELECT machine_uid, kind, ts, detail FROM lem_machine_log "
            "WHERE kind IN ({}) AND ts > ? ORDER BY ts, rowid".format(
                ", ".join("?" for _ in TRIGGER_KINDS)),
            list(TRIGGER_KINDS) + [oldest])
        with _doing("read the re-estimation triggers"):
            listed = rows(res)

        by_machine: Dict[str, List[dict]] = {}
        for row in listed:
            by_machine.setdefault(str(row.get("machine_uid") or ""),
                                  []).append(row)

        out: List[StaleTrigger] = []
        for est in current:
            fired = None
            for row in by_machine.get(est.machine_uid, ()):
                if str(row.get("ts") or "") > est.computed_at:
                    fired = row
                    break
            if fired is not None:
                kind = str(fired.get("kind") or "")
                action = _action_of(fired.get("detail"))
                out.append(StaleTrigger(
                    estimate_id=est.estimate_id, machine_uid=est.machine_uid,
                    test_name=est.test_name, trigger=kind,
                    at=str(fired.get("ts") or ""),
                    sentence="{} on {} at {}, after this estimate was computed "
                             "on {}. SOP 2.11 requires a re-estimate.".format(
                                 action or _TRIGGER_WORDS.get(kind, kind),
                                 est.machine_uid, fired.get("ts"),
                                 est.computed_at)))
                continue
            if est.replace_by and _past(est.replace_by, now):
                out.append(StaleTrigger(
                    estimate_id=est.estimate_id, machine_uid=est.machine_uid,
                    test_name=est.test_name, trigger=TRIGGER_REPLACE_BY,
                    at=est.replace_by,
                    sentence="This is an interim {} estimate and its "
                             "replacement date of {} has passed. Re-estimate "
                             "from control data, or restate the interim one."
                             .format(est.rw_route, est.replace_by)))
        return out


_TRIGGER_WORDS = {
    "calibration": "A calibration was recorded",
    "pm": "Preventive maintenance was recorded",
    # Machine replacement is not its own kind: retiring an instrument is
    # audited as a config row, so the sentence prefers the recorded ACTION and
    # falls back to this only when the detail carries none.
    "config": "A configuration change was recorded",
}


def _action_of(detail: Any) -> str:
    """The audited ACTION out of a log row's detail, or "".

    `_audit` writes `{"action": "machine deleted", "by": ...}`, and naming the
    action is the difference between "a configuration change was recorded" and
    "machine deleted was recorded" — the second of which is SOP 2.11's fourth
    trigger, machine replacement, arriving as a config row.
    """
    if isinstance(detail, dict):
        parsed = detail
    else:
        try:
            parsed = json.loads(detail or "{}")
        except (TypeError, ValueError):
            return ""
    if not isinstance(parsed, dict):
        return ""
    action = str(parsed.get("action") or "").strip()
    return "{} was recorded".format(action) if action else ""


def _within(series: QcSeries, start_ts: str, end_ts: str) -> QcSeries:
    """The same series, narrowed to `[start_ts, end_ts]` INCLUSIVE.

    Deliberately not `qc_series.window`, which is half-open so that consecutive
    windows tile a history without a result falling into two of them. That is
    the right rule for windows a person asks for and the wrong one here: these
    bounds are the first and last result of the estimate being replayed, and
    excluding the endpoint would recompute a successor over one fewer result
    than its predecessor used.
    """
    if not start_ts and not end_ts:
        return series
    kept = tuple(p for p in series.points
                 if (not start_ts or p.ts >= start_ts)
                 and (not end_ts or p.ts <= end_ts))
    return replace(series, points=kept)


def _past(day: str, now: datetime) -> bool:
    """Has this replacement date gone by?

    A date, not an instant: an estimate does not expire at 14:32. Parsed
    strictly, and an unparseable date is NOT treated as passed — silently
    marking every estimate stale because somebody typed a date oddly is a worse
    failure than not marking one.
    """
    text = str(day or "").strip()
    if len(text) != 10:
        return False
    try:
        return date.fromisoformat(text) < now.date()
    except ValueError:
        return False
