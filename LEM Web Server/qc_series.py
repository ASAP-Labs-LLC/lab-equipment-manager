#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qc_series.py — is this instrument IN CONTROL, and what does its spread mean?

The floor already draws a QC chart: the points, the pass band, and a count of
how many fell outside it. That answers "was each result acceptable". It does
not answer the question a PJLA 17025 assessor asks of a control chart, which
is whether the PROCESS is in control — because a run of eleven results that
are every one of them inside the band, and every one of them above the mean,
is an instrument that has moved, reported as perfect.

So this module takes the rows `lem_machine_log` already holds and returns
numbers. No Flask, no gateway, no I/O, stdlib only — `statistics` is enough.
Everything here is a pure function of its arguments, which is what lets the
control rules be tested against series that were worked out on paper.

TWO KINDS OF LIMIT LIVE ON ONE CHART AND THEY ARE NOT THE SAME THING
--------------------------------------------------------------------
`PassBand` — `low` / `high` / `expected`, read straight out of the log detail
the station module wrote. It comes from the STANDARD: the certificate's
assigned value and its control limit, `expected +/- k * std_dev`
(`qc_samples.SampleTestSpec`). It is a specification. It says nothing about
this instrument; the same band judges every bench running that standard.

`ControlLimits` — `mean +/- 1s / 2s / 3s`, computed from THESE results. It is
an observation. It describes what this one instrument has actually been doing
and it moves as the instrument moves.

They are kept in separate types, with separate names, deliberately. A wide
certificate over a drifting instrument gives narrow zones inside a wide band —
in control of nothing, passing everything — and a tight certificate over a
stable one gives the reverse. Collapsing the two into one "limits" field is
how a chart comes to say the opposite of what the process is doing, and the
count of out-of-SPEC results (`failures`) is likewise not the count of
out-of-CONTROL rule violations. Both go on the chart. Neither substitutes.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
No measurement uncertainty, no bias, no certificate handling, no coverage
factors. A later uncertainty module would sit ON TOP of this one and take
three things from `SeriesAnalysis` — `s`, `s_df`, and `spread_basis`, which
says whether that `s` is repeatability or within-laboratory reproducibility.
Those three names, and not `n` or `coverage.basis`, because all three of them
describe the SAME set of results by construction: when the limits came from a
qualification period, `n` counts this window while `s` came from that period,
and `coverage` describes this window's analysts rather than that period's.
Computing u(Rw) here without that boundary is exactly how a repeatability
number ends up reported as an uncertainty.

A NOTE ON THE DEFAULT PATH
--------------------------
`analyse(series)` with no limits fits the limits to the results it is judging.
That is not a control chart — it is a chart grading itself — and this module
does not pretend otherwise: `SeriesAnalysis.self_fitted` says so, the shift
rule is not evaluated, and every zone finding comes back `provisional`. The
real fix is a qualification period, and this is what honesty looks like until
there is one.

Measured on 20,000 simulated in-control charts (60 points, correct fixed
limits as the control), the fraction of CLEAN charts the default path reports
anything at all about is 30.5%, of which every finding is provisional; the
fraction carrying a FIRM finding is 1.9%, and those are trends, the one rule
self-fitting does not weaken. It was 50.3% before, all of it firm.

The residual 30.5% is not a self-fitting artefact — the same rules against
PERFECTLY CORRECT fixed limits flag 42.6% of the same charts. That is the
multirule itself over sixty points: five rules, each scanned across every
window, accumulate false alarms with n. Reducing it is a decision about WHICH
rules a 17025 chart should run, not a bug in any of them, and it is not made
here.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


# ── one result ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class QcPoint:
    """One QC verdict out of `lem_machine_log`, parsed and nothing more.

    `in_spec` is the verdict the bench recorded against the certificate's band
    — tri-state, because a row whose detail could not be read has no verdict
    and `False` there would invent a failure.

    `operator` and `calibration_id` are `None` when the row does not carry
    them. See `Coverage`: the bench writes both on every verdict now
    (`qc_log_detail`), but rows written before it did carry neither, so a
    series read today is a mixture of rows that have them and rows that never
    will. `None` means UNKNOWN and never "a different one".
    """

    machine_uid: str
    test_name: str
    ts: str
    value: float
    in_spec: Optional[bool] = None
    operator: Optional[str] = None
    calibration_id: Optional[str] = None
    lab_id: str = ""
    at: Optional[datetime] = None      # parsed `ts`, or None if unparseable

    @property
    def day(self) -> Optional[date]:
        return self.at.date() if self.at else None


# ── the series ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PassBand:
    """The SPECIFICATION: `expected +/- k*std_dev` off the standard's record.

    Not the process's own limits — see the module docstring. Kept whole rather
    than reduced to a half-width, because the log carries `low`/`high` and a
    band recomputed from a midpoint is a band that can disagree with the
    verdict already written beside it.
    """

    low: float
    high: float
    expected: Optional[float] = None

    def contains(self, value: float) -> bool:
        """Inclusive, the same way a result ON 3s is not beyond it.

        NOT for re-judging a result: the verdict on the chart is the one the
        bench recorded against the band in force at the time, and 17025 7.11.3
        does not restate a reported result. What this is for is comparing the
        band with the process's own limits — see
        `SeriesAnalysis.zones_within_band`, which is the module docstring's
        opening case answered.
        """
        return self.low <= value <= self.high


@dataclass(frozen=True)
class QcSeries:
    """One (machine, test) control series, in time order."""

    machine_uid: str
    test_name: str
    points: Tuple[QcPoint, ...] = ()
    pass_band: Optional[PassBand] = None
    sample_id: str = ""

    @property
    def values(self) -> Tuple[float, ...]:
        return tuple(p.value for p in self.points)


# ── reading rows ─────────────────────────────────────────────────────────────

def _detail(raw: Any) -> Dict[str, Any]:
    """`detail` is a JSON TEXT column, but a fake gateway can hand back a dict.

    A detail that will not parse is an empty detail, never an exception: the
    reading itself is still a QC result and dropping it would take a real
    excursion off the chart because of a formatting problem.
    """
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _float(raw: Any) -> Optional[float]:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    # NaN compares false against every limit, so it would sit on a chart as a
    # point that no rule can ever fire on. Not a reading.
    return None if math.isnan(value) or math.isinf(value) else value


def _when(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _operator(detail: Dict[str, Any]) -> Optional[str]:
    """Who ran it — or None, which is NOT the same as "somebody else".

    Read defensively because the field does not exist in every row yet. The
    honest answer for a row without it is that the analyst is unknown, and
    `Coverage` refuses to draw any conclusion from an unknown one.
    """
    name = detail.get("operator")
    if name is None:
        name = detail.get("by")          # what every audit row in this tree calls it
    name = str(name or "").strip()
    return name or None


def _calibration_id(detail: Dict[str, Any]) -> Optional[str]:
    """Which calibration epoch this result was measured against — or None.

    The bench writes the key on EVERY verdict, `None` included: a key present
    and null says the station module looked and did not know, and a key missing
    says the row predates anything looking. Both are UNKNOWN here, because
    neither is evidence that this result sat on a different calibration from
    the one before it — and reading absence as difference is exactly how a
    single-calibration spread comes to be reported as u(Rw).

    Blank is neither present nor absent and would be tallied as one epoch by
    anything counting a set of strings, so it is normalised to None the same
    way `known_text` does it on the bench.
    """
    text = str(detail.get("calibration_id") or "").strip()
    return text or None


def _is_qc_row(r: Dict[str, Any]) -> bool:
    """Is this log row a QC verdict?

    `_qc_events` filters `kind = 'qc'` in SQL and never selects the column, so
    absent means already filtered. Present and something else is a caller
    passing whole log rows, where a PM completion must not become a QC result
    — nor supply a band, nor a sample id. One predicate, used everywhere a row
    is read, because the two loops in `series_from_rows` disagreeing is what
    let a maintenance record overwrite a certificate's limits.
    """
    return "kind" not in r or str(r.get("kind") or "").strip() == "qc"


def points_from_rows(rows: Iterable[Dict[str, Any]]) -> Tuple[QcPoint, ...]:
    """`lem_machine_log` rows -> points, oldest first.

    Sorted here rather than trusted from the caller's ORDER BY: every run rule
    below reads consecutive points, so a shift and a trend are properties of
    the ORDER, and a series handed over newest-first reports a rising drift as
    a falling one.
    """
    points: List[QcPoint] = []
    for r in rows or ():
        if not _is_qc_row(r):
            continue
        name = str(r.get("test_name") or "").strip()
        if not name:
            continue
        value = _float(r.get("value"))
        if value is None:
            continue
        detail = _detail(r.get("detail"))
        in_spec = detail.get("in_spec")
        points.append(QcPoint(
            machine_uid=str(r.get("machine_uid") or ""),
            test_name=name,
            ts=str(r.get("ts") or ""),
            value=value,
            in_spec=None if in_spec is None else bool(in_spec),
            operator=_operator(detail),
            calibration_id=_calibration_id(detail),
            lab_id=str(r.get("lab_id") or ""),
            at=_when(r.get("ts"))))
    # A point whose timestamp will not parse cannot be placed against the
    # others, so it keeps its arrival order at the end rather than being
    # dropped — it is still a result, and still counts toward n and s.
    points.sort(key=lambda p: (p.at is None, p.at or datetime.min))
    return tuple(points)


def _band(detail: Dict[str, Any]) -> Optional[PassBand]:
    low, high = _float(detail.get("low")), _float(detail.get("high"))
    if low is None or high is None:
        return None
    return PassBand(low=low, high=high, expected=_float(detail.get("expected")))


def series_from_rows(
        rows: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, str], QcSeries]:
    """Every (machine_uid, test_name) series in one pass over the rows."""
    rows = list(rows or ())
    grouped: Dict[Tuple[str, str], List[QcPoint]] = {}
    for point in points_from_rows(rows):
        grouped.setdefault((point.machine_uid, point.test_name), []).append(point)

    # The band is taken from the NEWEST row that carries one: a standard gets
    # re-certified and the band moves, and the chart has to be drawn against
    # the limits in force now. Read off the rows in time order for the same
    # reason the points are.
    #
    # `_is_qc_row` again, and it is not belt-and-braces: `points_from_rows`
    # drops a PM completion but this loop used not to, so a maintenance record
    # sharing the machine and the test name overwrote the certificate's band
    # with its own (0 - 0.001) and the sample id with its own. Every result in
    # the series then read out of spec against limits from a PM record.
    bands: Dict[Tuple[str, str], PassBand] = {}
    samples: Dict[Tuple[str, str], str] = {}
    for r in sorted((r for r in rows if _is_qc_row(r)),
                    key=lambda r: str(r.get("ts") or "")):
        key = (str(r.get("machine_uid") or ""),
               str(r.get("test_name") or "").strip())
        if key not in grouped:
            continue
        band = _band(_detail(r.get("detail")))
        if band is not None:
            bands[key] = band
        if r.get("lab_id"):
            samples[key] = str(r.get("lab_id"))

    return {key: QcSeries(machine_uid=key[0], test_name=key[1],
                          points=tuple(pts), pass_band=bands.get(key),
                          sample_id=samples.get(key, ""))
            for key, pts in grouped.items()}


def series_for(rows: Iterable[Dict[str, Any]], machine_uid: str,
               test_name: str) -> QcSeries:
    """One series by name. An unknown test is an EMPTY series, not an error.

    "No QC recorded for that test" is an answer an operator acts on, and the
    same rule the rest of this tree follows: a missing row is emptiness, a
    failed read is an exception, and the two are never confused.
    """
    found = series_from_rows(rows).get((machine_uid, test_name))
    return found or QcSeries(machine_uid=machine_uid, test_name=test_name)


# ── the divisor ──────────────────────────────────────────────────────────────

def mean_and_s(values: Sequence[float]) -> Tuple[Optional[float], Optional[float]]:
    """Mean and SAMPLE standard deviation, the n-1 divisor.

    n-1 because these results are a sample of what the instrument does, not
    the whole of it — `statistics.pstdev` would be the population figure and
    reports the process as tighter than it is, which is the classic control
    chart error and the one an assessor checks for.

    `s` is None below two points, NOT 0.0: one result has no spread to report,
    and a zero passed on to the zones would put every subsequent reading
    beyond a limit of zero width.
    """
    values = [float(v) for v in values]
    if not values:
        return None, None
    if len(values) == 1:
        return values[0], None
    # `statistics.mean`, NOT `fmean`. fmean accumulates in float and put the
    # centre line of nine identical 63.7 readings at 63.70000000000001 — every
    # one of those nine results then counted as BELOW its own mean, and a
    # perfectly flat instrument was reported as a shift. `mean`
    # sums exactly. n is at most a few hundred here, so the cost is nothing.
    return statistics.mean(values), statistics.stdev(values)


@dataclass(frozen=True)
class ControlLimits:
    """The OBSERVED process limits: `mean +/- k*s` for k = 1, 2, 3.

    NOT the pass band. See the module docstring — this is what the instrument
    has been doing, the band is what the certificate says it must do, and the
    two answer different questions on the same chart.

    Constructible by hand (`ControlLimits(n=20, mean=63.7, s=0.42)`) on
    purpose. A lab normally FIXES its limits from a qualification period and
    then judges later results against them, rather than recomputing the mean
    every time a point arrives — which is a moving target that absorbs the
    very drift the chart exists to show. `violations()` therefore takes limits
    as an argument and only computes them from the points as a default.

    `n` is the number of results these limits were computed FROM, which is not
    in general the number of results they are used to judge. Keep it that way:
    `df` is derived from it and `df` is what a later uncertainty module reads
    beside `s`.
    """

    n: int
    mean: Optional[float]
    s: Optional[float]

    @property
    def df(self) -> int:
        """Degrees of freedom, n-1 — the companion `s` never travels without.

        Floored at 0 so an empty or single-point series does not hand a later
        uncertainty calculation a negative divisor.
        """
        return max(0, self.n - 1)

    def zone(self, k: float) -> Optional[Tuple[float, float]]:
        """`(mean - k*s, mean + k*s)`, or None when there is no spread yet."""
        if self.mean is None or self.s is None:
            return None
        return (self.mean - k * self.s, self.mean + k * self.s)

    def _edge(self, k: float, upper: bool) -> Optional[float]:
        pair = self.zone(k)
        return None if pair is None else pair[1 if upper else 0]

    @property
    def lower_1s(self) -> Optional[float]: return self._edge(1, False)

    @property
    def upper_1s(self) -> Optional[float]: return self._edge(1, True)

    @property
    def lower_2s(self) -> Optional[float]: return self._edge(2, False)

    @property
    def upper_2s(self) -> Optional[float]: return self._edge(2, True)

    @property
    def lower_3s(self) -> Optional[float]: return self._edge(3, False)

    @property
    def upper_3s(self) -> Optional[float]: return self._edge(3, True)


def control_limits(values: Sequence[float]) -> Optional[ControlLimits]:
    """Observed limits for these values, or None if there are none at all.

    None rather than a zeroed object: "this instrument has no control chart
    yet" and "this instrument sits dead on its mean" are different states and
    a caller has to be able to tell them apart.
    """
    values = list(values)
    if not values:
        return None
    mean, s = mean_and_s(values)
    return ControlLimits(n=len(values), mean=mean, s=s)


# ── out-of-control rules ─────────────────────────────────────────────────────
#
# A defensible subset of the Westgard / Nelson rules — the ones a 17025
# assessor expects to see on a control chart and a bench tech can act on.
# Deliberately NOT the full Westgard multirule: R-4s, 10x and the rest need a
# run structure (paired controls, defined batches) that this log does not
# record, and a rule evaluated on the wrong structure is a false alarm every
# few days until somebody switches the chart off.
#
# R-4s in particular is a range across a PAIR of controls in one run, and this
# log has no pairs. The pattern it looks for is still visible here, though, as
# two adjacent 2-of-3 findings on opposite sides — which is precisely why
# `_k_of_m` must resume past the last offending point rather than past the
# window, or the chart reports the first half of that pattern and calls the
# second half clean.
#
# Every rule is judged with a STRICT inequality against its zone. A result
# landing exactly on 3s is at the limit, not beyond it, and >= there condemns
# an instrument that did nothing wrong — the single commonest defect in a
# hand-rolled control chart.

RULE_1_3S = "1_3s"
RULE_2OF3_2S = "2of3_2s"
RULE_4OF5_1S = "4of5_1s"
RULE_SHIFT = "shift"
RULE_TREND = "trend"

# TWO RUN LENGTHS, TWO DIFFERENT AUTHORITIES, AND THEY DO NOT AGREE
# -----------------------------------------------------------------
# These constants are not the same rule with the same number, and neither may
# be justified by the other's citation.
#
# SHIFT_RUN = 9 — consecutive points on one side of the centre line.
#   ISO 7870-2 (Shewhart control charts), which is the control-chart standard
#   in the ISO/IEC 17025 family this lab is assessed against; Nelson rule 2
#   agrees at nine. Westgard's own version of this rule is 8x or 10x depending
#   on which of his papers you read, which is the giveaway that seven belongs
#   to nobody.
#
#   Seven was this module's number and matched no cited authority. Measured on
#   20,000 simulated in-control charts of sixty points against correct fixed
#   limits, the fraction of CLEAN charts flagged out of control by this rule
#   alone (n=30 in brackets):
#
#       run = 7  (what this module used to do)      36.5%   (18.7%)
#       run = 8  (Westgard 8x)                      19.3%    (9.4%)
#       run = 9  (ISO 7870-2 / Nelson 2)            10.1%    (4.7%)
#       run = 10 (Westgard 10x)                      5.0%    (2.3%)
#
#   A detector that condemns a third of clean charts is a detector the bench
#   switches off, and then the chart catches nothing at all.
#
# TREND_RUN = 7 — consecutive points each higher (or lower) than the last.
#   Westgard 7T. Kept at seven because that IS the rule as cited; Nelson rule 3
#   uses six for the same pattern and is stricter still, so seven is not the
#   loose choice here. A trend is a rarer accident than a run on one side —
#   the same seven costs far less in false alarms — which is why the two
#   numbers differ and why making them agree for tidiness would be wrong.
#
# Both counted in POINTS, not in steps between points: a trend of seven is six
# consecutive rises. Counting the steps fires the rule a whole run early.
SHIFT_RUN = 9
TREND_RUN = 7


@dataclass(frozen=True)
class RuleViolation:
    """One out-of-control finding.

    `indices` are 0-based into the series' points, in time order; the messages
    number them from 1, because "run 0" means nothing at a bench.

    `side` is "above"/"below" for the four rules measured against the mean, and
    "up"/"down" for the trend, which has a direction rather than a side.

    `provisional` is True when the finding was measured against limits fitted
    to the very points it judges — see `violations()`. It is not a weaker
    version of the same claim; it says the comparison had no independent
    reference, so the finding may be an artefact of the points themselves.
    A provisional finding is a reason to re-run the standard, never a reason
    to change a reagent.
    """

    rule: str
    indices: Tuple[int, ...]
    side: str
    message: str
    provisional: bool = False


def _num(value: float) -> str:
    """131.0 -> "131", 2.6431 -> "2.6431" — a number fit to read in a sentence."""
    return f"{float(value):.4f}".rstrip("0").rstrip(".") or "0"


def _and_list(parts: Sequence[str]) -> str:
    """["a"] -> "a"; ["a", "b"] -> "a and b"; ["a","b","c"] -> "a, b and c"."""
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def _beyond(value: float, limits: ControlLimits, k: float) -> str:
    """Which side of the +/-k*s zone this value is BEYOND, or "".

    `not limits.s` covers both of the degenerate spreads and is the guard the
    whole zone half of this module rests on. s is None below two points; s is
    0.0 when every result so far is the same number, which is a real state for
    an instrument reporting one rounded figure. Zones of zero width would make
    the very next ordinary reading a 3s excursion, so no zone rule may fire
    without a spread to measure against — and limits can be handed in from a
    qualification period, so this is not merely a property of these points.
    """
    if not limits.s:
        return ""
    pair = limits.zone(k)
    if pair is None:
        return ""
    low, high = pair
    if value > high:
        return "above"
    if value < low:
        return "below"
    return ""


def _side_of_mean(value: float, mean: float) -> str:
    """Above, below, or neither — a point exactly ON the mean is neither.

    Not a rounding quibble: treating it as invisible and carrying the run
    across it splices two runs of four into a shift of nine that the
    instrument never made.
    """
    if value > mean:
        return "above"
    if value < mean:
        return "below"
    return ""


# Appended to every message derived from limits that were fitted to the points
# being judged. One sentence, printed beside the chart, and it has to say the
# thing plainly: the comparison had no independent reference.
_PROVISIONAL = (" PROVISIONAL: these limits were computed from the same "
                "results they are judging, so this instrument has no "
                "qualification limits to be out of control against. Confirm "
                "against fixed limits before acting on it.")


def _one_beyond_3s(points: Sequence[QcPoint], limits: ControlLimits, *,
                   provisional: bool = False) -> List[RuleViolation]:
    out = []
    for i, point in enumerate(points):
        side = _beyond(point.value, limits, 3)
        if not side:
            continue
        edge = limits.upper_3s if side == "above" else limits.lower_3s
        out.append(RuleViolation(
            rule=RULE_1_3S, indices=(i,), side=side, provisional=provisional,
            message=(f"Run {i + 1} ({_num(point.value)}) is beyond the "
                     f"{'upper' if side == 'above' else 'lower'} 3s control "
                     f"limit ({_num(edge)}). Hold every result since the last "
                     f"good check and investigate before this instrument "
                     f"reports again."
                     f"{_PROVISIONAL if provisional else ''}")))
    return out


def _k_of_m(points: Sequence[QcPoint], limits: ControlLimits, *, need: int,
            span: int, zone: float, rule: str, advice: str,
            provisional: bool = False) -> List[RuleViolation]:
    """`need` of `span` consecutive results beyond `zone`, on the SAME side.

    Same side is the rule. Counting |x - mean| instead — two points beyond 2s
    on OPPOSITE sides — reports ordinary scatter as a fault, because a process
    that swings both ways is imprecise, not shifted, and that is a different
    finding with a different fix.

    One alarm per excursion, not one per window: when a window fires, the scan
    resumes at the point AFTER the last offending one. Four consecutive results
    beyond 2s span two overlapping windows, and a chart that raises the same
    alarm twice is a chart the bench stops reading.

    Resuming past the last offending point, and not past the whole WINDOW, is
    the difference between reporting an excursion and dropping one. `i += span`
    skips every window that begins inside the one that just fired, so
    [121, 122, 79, 78, ...] against mean 100 / s 10 reports the two above 2s
    and reports the two below 2s as nothing at all — results swinging beyond
    2s on both sides, which is the Westgard R-4s random-error signature, half
    reported and half called clean. A missed alarm is worse than a false one.
    Resuming at `found[-1] + 1` still advances at least one point per pass, so
    no point is ever reported inside two findings of the same rule.
    """
    out: List[RuleViolation] = []
    i = 0
    while i + span <= len(points):
        hit = None
        for side in ("above", "below"):
            found = tuple(j for j in range(i, i + span)
                          if _beyond(points[j].value, limits, zone) == side)
            if len(found) >= need:
                hit = (side, found)
                break
        if hit is None:
            i += 1
            continue
        side, found = hit
        # The message names the first OFFENDING run, not the window's first
        # run: the window may open on a perfectly good result, and "starting
        # at run 1" beside `indices` of (1, 2) sends a tech to the wrong point
        # and contradicts the very field the chart highlights.
        count = len(found)
        first, last = found[0] + 1, found[-1] + 1
        # `count`, never `need`: "2 of the 3" when all three are beyond
        # understates the excursion to the person deciding what to do about it.
        head = (f"All {span} results" if count == span
                else f"{count} of {span} consecutive results")
        out.append(RuleViolation(
            rule=rule, indices=found, side=side, provisional=provisional,
            message=(f"{head} — from run {first} to run {last} — sit beyond "
                     f"{_num(zone)}s {side} the mean. {advice}"
                     f"{_PROVISIONAL if provisional else ''}")))
        i = found[-1] + 1
    return out


def _runs(points: Sequence[QcPoint], label) -> List[Tuple[int, int, str]]:
    """Maximal runs of consecutive points sharing a non-empty `label(i)`.

    Maximal, so a run of twelve is reported once as twelve rather than four
    times as overlapping nines.
    """
    out = []
    i = 0
    while i < len(points):
        side = label(i)
        if not side:
            i += 1
            continue
        j = i
        while j + 1 < len(points) and label(j + 1) == side:
            j += 1
        out.append((i, j, side))
        i = j + 1
    return out


def _shift(points: Sequence[QcPoint],
           limits: ControlLimits) -> List[RuleViolation]:
    """`SHIFT_RUN` or more consecutive results on one side of the centre line.

    NEVER evaluated against a mean fitted to these same points — `violations()`
    does not call it in that case, and the reason is not conservatism, it is
    that the answer inverts. The mean of a set sits inside the set, so a single
    gross outlier drags the centre line past all the GOOD results and every one
    of them becomes "on one side of the mean":

        [100, 100, 100, 100, 100, 100, 100, 100, 100, 10]

    fits a mean of 91, reports a nine-point shift on the nine results that were
    perfect, and stays silent about the 10 — which inflated `s` so far that it
    is inside its own 3s limit. The rule then prints remedial advice about
    reagents against the wrong half of the chart. There is no threshold that
    fixes that; the comparison itself is the error.
    """
    if limits.mean is None:
        return []
    runs = _runs(points, lambda i: _side_of_mean(points[i].value, limits.mean))
    out = []
    for start, end, side in runs:
        if end - start + 1 < SHIFT_RUN:
            continue
        out.append(RuleViolation(
            rule=RULE_SHIFT, indices=tuple(range(start, end + 1)), side=side,
            message=(f"{end - start + 1} results in a row from run "
                     f"{start + 1} are {side} the mean. That is a shift, not "
                     f"scatter — check for a new bottle of standard, a "
                     f"reagent change or an altered instrument setting.")))
    return out


def _trend(points: Sequence[QcPoint], limits: ControlLimits
           ) -> List[RuleViolation]:
    """`TREND_RUN` or more consecutive results each higher (or lower) than the last.

    Needs no zones at all — a trend inside 1s is still a trend, and it is the
    one pattern that catches an instrument BEFORE it starts failing, which is
    the whole reason a chart is drawn rather than a pass rate counted.

    Because it reads neither the mean nor a zone, it is also the one rule that
    self-fitted limits do not weaken: a run of seven rises is a run of seven
    rises whatever the limits are. So it is never marked provisional.
    """
    values = [p.value for p in points]

    def step(i: int) -> str:
        if values[i] > values[i - 1]:
            return "up"
        if values[i] < values[i - 1]:
            return "down"
        return ""            # a repeat breaks the run; it is not a rise

    out = []
    i = 1
    while i < len(values):
        way = step(i)
        if not way:
            i += 1
            continue
        j = i
        while j + 1 < len(values) and step(j + 1) == way:
            j += 1
        # steps i..j are one direction, so points i-1..j are the run.
        if j - i + 2 >= TREND_RUN:
            out.append(RuleViolation(
                rule=RULE_TREND, indices=tuple(range(i - 1, j + 1)), side=way,
                message=(f"{j - i + 2} results in a row from run {i} read "
                         f"{'higher' if way == 'up' else 'lower'} than the "
                         f"last. Something is drifting — check the detector, "
                         f"the bath temperature and the age of the "
                         f"standard.")))
        i = j + 1
    return out


def violations(points: Sequence[QcPoint],
               limits: Optional[ControlLimits] = None, *,
               self_fitted: Optional[bool] = None
               ) -> Tuple[RuleViolation, ...]:
    """Every rule this series breaks, in CHART order.

    Chart order is by the first result each finding involves, so the list and
    the plotted points read left to right the same way. That is NOT the order
    the findings became knowable, and the two are genuinely different: a shift
    beginning at run 1 is only knowable at its ninth point, while a 3s
    excursion at run 3 is knowable the moment run 3 arrives. The chart is what
    a person is looking at, so the chart wins; a caller wanting detection order
    has `indices` and the run lengths and can derive it. `rule` breaks ties for
    a stable order rather than for any meaning.

    LIMITS FITTED TO THE POINTS THEY JUDGE
    --------------------------------------
    `limits` is an argument because a lab fixes its control limits from a
    qualification period and judges later results against them. Recomputing
    the mean from the same points it is judging is a moving target that
    absorbs the very drift the chart exists to expose — so that is the
    fallback, not the design.

    It is also, today, the only call anyone writes, so the fallback cannot be
    left to be merely documented as inferior. When the limits were fitted to
    these points:

      * `_shift` is NOT EVALUATED. Its answer inverts under self-fitting — see
        that function — and it is the one rule carrying remedial instructions,
        so a wrong answer there sends a tech to change a reagent because of the
        results that were fine.
      * the three zone rules still run, but every finding comes back
        `provisional=True` with a sentence saying so. They are still worth
        raising — "re-run the standard" is never bad advice — but they are not
        evidence that the process left a control state nobody established.
      * `_trend` is unaffected and firm: it reads no mean and no zone.

    `self_fitted` defaults to "whether `limits` was supplied". Pass it
    explicitly when the caller computed the limits from these same points and
    is handing them in — which is what `analyse` does.
    """
    points = tuple(points)
    if not points:
        return ()
    if self_fitted is None:
        self_fitted = limits is None
    if limits is None:
        limits = control_limits([p.value for p in points])
    found: List[RuleViolation] = []
    found += _one_beyond_3s(points, limits, provisional=self_fitted)
    found += _k_of_m(points, limits, need=2, span=3, zone=2,
                     rule=RULE_2OF3_2S, provisional=self_fitted,
                     advice=("Re-run the standard before releasing anything "
                             "else from this instrument."))
    found += _k_of_m(points, limits, need=4, span=5, zone=1,
                     rule=RULE_4OF5_1S, provisional=self_fitted,
                     advice=("The process has moved off centre — re-run the "
                             "standard, and recalibrate if it repeats."))
    if not self_fitted:
        found += _shift(points, limits)
    found += _trend(points, limits)
    found.sort(key=lambda v: (v.indices[0], v.rule))
    return tuple(found)


# ── what the spread is allowed to be CALLED ──────────────────────────────────
#
# Metrologically load-bearing, and the reason this is not just a headcount.
#
# Results from ONE analyst in ONE sitting against ONE calibration measure
# repeatability (s_r): the tightest the method can ever look. Results spread
# across analysts, days AND calibrations measure within-laboratory
# reproducibility, u(Rw) — intermediate precision — which is what a lab's
# stated uncertainty is actually built from, and which is always the larger
# number.
#
# THREE factors, not two. The bench's own `qc_log_detail` says it: the spread
# is u(Rw) only if the set spans "analysts, shifts and calibrations". A set
# that spans two analysts over two days but sits entirely inside ONE
# calibration epoch carries no between-calibration component at all, and
# calling it u(Rw) claims a component that was never sampled — which is a
# component an assessor can ask to see, and which is usually the largest one.
# So `calibration_id` is read off the detail the same way `operator` is, and
# `supports_reproducibility()` needs more than one epoch before it is True.
#
# Reporting the narrower as if it were the wider overstates the lab's control,
# and the way that happens in software is by INFERENCE: counting rows instead
# of counting analysts, or reading a missing name as a different person. The
# bench writes both fields on every verdict now, but rows written before it did
# carry neither, so a series read today is part attributed and part not. A row
# without a name says the analyst is UNKNOWN. It never says "somebody else",
# and a row without a calibration id never says "a different calibration".
#
# DAYS ARE CALENDAR DATES, AND THE CAVEAT SAYS SO
# ------------------------------------------------
# `coverage()` counts distinct DATES. Two results at 08:00 and 23:00 on one
# date are two shifts and are counted as one day. That understates the
# conditions the spread covers, which is the safe direction — it can only ever
# withhold a claim, never manufacture one — but it does mean nothing printed
# here may use the word "shifts", because the module has not counted any. The
# log carries a timestamp and no shift boundary; a shift table would have to be
# configuration, and inventing one from the hour is how 08:00 and 08:15 end up
# on opposite sides of an imaginary line.

BASIS_INSUFFICIENT = "insufficient"
BASIS_REPEATABILITY = "repeatability"
BASIS_PARTIAL = "partial"
BASIS_INTERMEDIATE = "intermediate"
BASIS_UNKNOWN = "unknown"


@dataclass(frozen=True)
class Coverage:
    """Who ran this series, over how many CALENDAR DAYS, against how many
    calibrations — and what that permits.

    `operators` holds the distinct NAMED analysts in the order they first
    appear, and `calibrations` the distinct NAMED calibration epochs the same
    way. `n_unknown_operator` and `n_unknown_calibration` count the results
    that named nobody and nothing, kept separate so neither can ever be
    mistaken for a headcount.

    `days` are dates, not shifts. See the note above the class.
    """

    n: int
    operators: Tuple[str, ...] = ()
    n_unknown_operator: int = 0
    days: Tuple[date, ...] = ()
    n_undated: int = 0
    calibrations: Tuple[str, ...] = ()
    n_unknown_calibration: int = 0

    @property
    def n_operators(self) -> int:
        return len(self.operators)

    @property
    def n_days(self) -> int:
        return len(self.days)

    @property
    def n_calibrations(self) -> int:
        return len(self.calibrations)

    @property
    def operator_varied(self) -> bool:
        """True only on two NAMED analysts. Unknowns can never raise this."""
        return self.n_operators >= 2

    @property
    def day_varied(self) -> bool:
        return self.n_days >= 2

    @property
    def calibration_varied(self) -> bool:
        """True only on two NAMED epochs. Unknowns can never raise this.

        The same rule as `operator_varied`, and for the same reason: absence
        counted as difference is how a spread that never left one calibration
        comes to be reported as u(Rw).
        """
        return self.n_calibrations >= 2

    @property
    def operator_known(self) -> bool:
        return self.n > 0 and self.n_unknown_operator == 0

    @property
    def day_known(self) -> bool:
        return self.n > 0 and self.n_undated == 0

    @property
    def calibration_known(self) -> bool:
        return self.n > 0 and self.n_unknown_calibration == 0

    @property
    def basis(self) -> str:
        """Which precision this spread is evidence of — or that it is neither.

        A partly attributed series is UNKNOWN, not "intermediate as far as we
        can tell": two named analysts and one anonymous result is still a
        series nobody can reconstruct, and 17025 7.5.1 is about being able to.
        A row carrying no calibration id counts the same way, so the older
        rows — which carry neither field — degrade to UNKNOWN rather than to
        the repeatability claim their single day would otherwise buy.
        """
        if self.n < 2:
            return BASIS_INSUFFICIENT
        if not (self.operator_known and self.day_known
                and self.calibration_known):
            return BASIS_UNKNOWN
        varied = (self.operator_varied, self.day_varied,
                  self.calibration_varied)
        if all(varied):
            return BASIS_INTERMEDIATE
        if not any(varied):
            return BASIS_REPEATABILITY
        # Some factors varied and some did not. It is more than repeatability
        # and less than u(Rw), and naming it as either would be the
        # overstatement this whole type exists to prevent. Two analysts over
        # two days on ONE calibration lands here, and that is the point.
        return BASIS_PARTIAL

    def supports_repeatability(self) -> bool:
        return self.basis == BASIS_REPEATABILITY

    def supports_reproducibility(self) -> bool:
        """Within-laboratory reproducibility, u(Rw). Deliberately strict.

        All three factors must be KNOWN and must have VARIED: analyst,
        calendar day and calibration epoch.
        """
        return self.basis == BASIS_INTERMEDIATE

    def caveat(self) -> str:
        """One sentence, fit to print beside the chart, saying what this is.

        Every claim in it has to be one this object actually counted. In
        particular it says "calendar days", because dates are what `coverage`
        counts — the log has no shift boundary in it and the module must not
        imply it read one.
        """
        results = f"{self.n} result{'' if self.n == 1 else 's'}"
        days = (f"{self.n_days} calendar "
                f"day{'' if self.n_days == 1 else 's'}")
        cals = (f"{self.n_calibrations} "
                f"calibration{'' if self.n_calibrations == 1 else 's'}")
        basis = self.basis
        if basis == BASIS_INSUFFICIENT:
            return ("Fewer than two results, so there is no spread to "
                    "describe yet.")
        if basis == BASIS_UNKNOWN:
            missing = []
            if not self.operator_known:
                missing.append(
                    f"who ran "
                    f"{'them' if self.n_unknown_operator > 1 else 'one of them'}")
            if not self.calibration_known:
                missing.append(
                    f"which calibration "
                    f"{'they were' if self.n_unknown_calibration > 1 else 'one of them was'}"
                    f" measured against")
            if not self.day_known:
                missing.append(f"a readable date for {self.n_undated} of them")
            return (f"{results}: the log does not record "
                    f"{_and_list(missing)} — so this spread cannot be called "
                    f"repeatability or within-laboratory reproducibility.")
        if basis == BASIS_REPEATABILITY:
            return (f"{results} from one analyst on one calendar day against "
                    f"one calibration: this spread is repeatability (s_r), "
                    f"the tightest this method can look, and not a "
                    f"within-laboratory reproducibility.")
        if basis == BASIS_INTERMEDIATE:
            return (f"{results} from {self.n_operators} analysts over {days} "
                    f"against {cals}: this spread supports within-laboratory "
                    f"reproducibility, u(Rw).")
        varied = [name for name, did in
                  (("the analyst", self.operator_varied),
                   ("the calendar day", self.day_varied),
                   ("the calibration", self.calibration_varied)) if did]
        held = [name for name, did in
                (("the analyst", self.operator_varied),
                 ("the calendar day", self.day_varied),
                 ("the calibration", self.calibration_varied)) if not did]
        return (f"{results} over {days} against {cals}: {_and_list(varied)} "
                f"varied and {_and_list(held)} did not — neither pure "
                f"repeatability nor a full within-laboratory reproducibility.")


def coverage(points: Sequence[QcPoint]) -> Coverage:
    """Distinct analysts, distinct CALENDAR DATES and distinct calibration
    epochs across a series.

    Names and epoch ids are matched case-insensitively but reported as first
    written: two rows reading "Ryan" and "ryan" are one analyst, and counting
    them as two would manufacture exactly the multi-operator coverage this
    module refuses to infer. Calibration ids are folded the same way for the
    same reason — the only risk in folding is under-counting, which withholds
    a claim, and the only risk in not folding is over-counting, which invents
    one.

    Days are DATES. 08:00 and 23:00 on one date are two shifts and one day
    here; see the note above `Coverage`. Nothing this function returns is
    called a shift, and nothing printed from it may be.
    """
    points = tuple(points)
    seen: Dict[str, str] = {}
    epochs: Dict[str, str] = {}
    days: Dict[date, None] = {}
    unknown = undated = uncalibrated = 0
    for point in points:
        if point.operator:
            seen.setdefault(point.operator.casefold(), point.operator)
        else:
            unknown += 1
        if point.calibration_id:
            epochs.setdefault(point.calibration_id.casefold(),
                              point.calibration_id)
        else:
            uncalibrated += 1
        day = point.day
        if day is None:
            undated += 1
        else:
            days.setdefault(day, None)
    return Coverage(n=len(points), operators=tuple(seen.values()),
                    n_unknown_operator=unknown, days=tuple(days),
                    n_undated=undated, calibrations=tuple(epochs.values()),
                    n_unknown_calibration=uncalibrated)


# ── a time-windowed view ─────────────────────────────────────────────────────

def window(series: QcSeries, start: Optional[datetime] = None,
           end: Optional[datetime] = None) -> QcSeries:
    """The same series, narrowed to `[start, end)`.

    Half-open so consecutive windows tile the history without a result falling
    into two of them — "this month" and "last month" must not both claim the
    reading taken at midnight.

    The band and the sample id come with it unchanged: they describe the
    STANDARD, not the window, and re-deriving them from the surviving points
    would let a narrow window silently change what the results were judged
    against.

    Bounds must be naive datetimes, because `lem_machine_log.ts` is written by
    `datetime.now().isoformat()` on the bench and carries no offset.
    """
    if start is None and end is None:
        return series
    kept = []
    for point in series.points:
        # A result whose timestamp will not parse cannot be placed on either
        # side of a bound. It stays in the full series — it is still a result
        # and still counts toward n — but no bounded window may claim it.
        if point.at is None:
            continue
        if start is not None and point.at < start:
            continue
        if end is not None and point.at >= end:
            continue
        kept.append(point)
    return replace(series, points=tuple(kept))


# ── the whole answer for one series ──────────────────────────────────────────

@dataclass(frozen=True)
class SeriesAnalysis:
    """Everything this module can say about one (machine, test) series.

    `failures` and `violations` are different findings and neither replaces
    the other: `failures` counts results the bench judged OUTSIDE the
    certificate's band, `violations` counts the ways the process is out of
    control. A series can be all of one and none of the other, in both
    directions.

    THREE COUNTS THAT ARE NOT THE SAME COUNT
    ----------------------------------------
    `n` is how many results are in THIS series (or this window of it). `s_n`
    is how many results `s` was computed from, and `s_df` its degrees of
    freedom. They are equal only when `self_fitted` is True. Qualification
    limits of n=20 judging a three-point window give `n = 3`, `s_n = 20`,
    `s_df = 19` — and a later uncertainty module handed the wrong one of those
    reports nineteen degrees of freedom for three results. The triple that
    module consumes is `(s, s_df, spread_basis)`, all three of which describe
    the SAME set by construction.
    """

    machine_uid: str
    test_name: str
    sample_id: str
    n: int
    mean: Optional[float]
    s: Optional[float]
    limits: Optional[ControlLimits]
    pass_band: Optional[PassBand]
    violations: Tuple[RuleViolation, ...]
    coverage: "Coverage"
    failures: int
    unjudged: int
    self_fitted: bool = True

    @property
    def in_control(self) -> bool:
        """No findings at all, provisional ones included.

        Provisional findings still count here on purpose: `in_control` is the
        conservative summary and a provisional 3s excursion is not a reason to
        paint a chart green. What `provisional` changes is how much weight the
        DIAGNOSIS carries, not whether anything was seen. `firm_violations` is
        the stricter reading for a caller that wants one.
        """
        return not self.violations

    @property
    def firm_violations(self) -> Tuple[RuleViolation, ...]:
        """The findings measured against limits this series did not supply."""
        return tuple(v for v in self.violations if not v.provisional)

    @property
    def s_n(self) -> int:
        """How many results `s` was computed from — NOT necessarily `n`."""
        return self.limits.n if self.limits else 0

    @property
    def s_df(self) -> int:
        """Degrees of freedom belonging with `s`: `s_n - 1`, floored at 0."""
        return self.limits.df if self.limits else 0

    @property
    def spread_basis(self) -> str:
        """What `s` is evidence of — repeatability, u(Rw), or neither.

        `coverage` describes the points in THIS series. When `s` came from a
        supplied qualification period, this series' analysts and calibrations
        say nothing about that period's, so the basis of `s` is UNKNOWN here
        and has to come from the coverage of the qualification set itself.
        Reading `coverage.basis` as the label for a supplied `s` silently
        mixes two sets, which is the same class of error as `s_df` and `n`
        disagreeing.
        """
        return self.coverage.basis if self.self_fitted else BASIS_UNKNOWN

    @property
    def zones_within_band(self) -> Optional[bool]:
        """Does the OBSERVED 3s spread fit inside the SPECIFIED band?

        The module docstring's opening case, answered. True is the ordinary
        healthy chart. False says ordinary scatter will start failing the
        certificate before any control rule fires — the instrument is in
        control of a process the specification cannot accept. None when either
        side is missing, because "no band recorded" and "no spread yet" are not
        a no.

        Restates no recorded verdict: it compares two limits, not a result.
        """
        band, lim = self.pass_band, self.limits
        if band is None or lim is None:
            return None
        low, high = lim.lower_3s, lim.upper_3s
        if low is None or high is None:
            return None
        return band.contains(low) and band.contains(high)


def analyse(series: QcSeries,
            limits: Optional[ControlLimits] = None) -> SeriesAnalysis:
    """The numbers for one series. Pure: no clock, no I/O, no gateway.

    `limits` supplied means a qualification period judging these results, and
    the findings come back firm. `limits` omitted means the limits are fitted
    to the very results they judge — `self_fitted` is True, the shift rule is
    not evaluated and every zone finding is provisional. See `violations()`
    for why, and `SeriesAnalysis` for what a consumer reads to tell them apart.
    """
    points = series.points
    self_fitted = limits is None
    computed = limits if limits is not None else control_limits(
        [p.value for p in points])
    mean, s = (computed.mean, computed.s) if computed else (None, None)
    # The verdict is the one the bench RECORDED, never one recomputed against
    # today's band: it was judged with the correction factor in force at the
    # time, and 17025 7.11.3 does not restate a result already reported.
    return SeriesAnalysis(
        machine_uid=series.machine_uid, test_name=series.test_name,
        sample_id=series.sample_id, n=len(points), mean=mean, s=s,
        limits=computed, pass_band=series.pass_band,
        violations=(violations(points, computed, self_fitted=self_fitted)
                    if computed else ()),
        coverage=coverage(points),
        failures=sum(1 for p in points if p.in_spec is False),
        unjudged=sum(1 for p in points if p.in_spec is None),
        self_fitted=self_fitted)
