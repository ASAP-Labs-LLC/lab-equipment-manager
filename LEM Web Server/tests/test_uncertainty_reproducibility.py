#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Spec gap 3: repeatability is not within-laboratory reproducibility.

The metrology trap, and the one an assessor tests by asking "who ran these?".
`qc_series.Coverage` already answers it — three factors, analyst, calendar day
and calibration epoch, all of which must be KNOWN and must have VARIED. These
suites assert that `uncertainty.py` takes that answer rather than forming a
second, weaker one of its own.

TWO QUESTIONS THAT LOOK LIKE ONE
--------------------------------
`spread_label` says what the SERIES' own `s` is. `u_rw_label` says what the
`u_rw` NUMBER on the budget is. They are the same on Routes 1 and 2, where
u(Rw) IS the spread, and different on Route 3, where the number came from a
control limit somebody set. Conflating them is how an interim target comes to
be reported as a measured reproducibility, so they are tested apart.
"""

import math

import pytest

import qc_series
import uncertainty
from test_uncertainty_fixtures import (one_analyst_one_day, series,
                                       spans_analysts_days_and_calibrations,
                                       unattributed)

JUSTIFIED = "Arithmetic fixture; sufficiency is tested in test_uncertainty_routes."
S = math.sqrt(10.0)


def _estimate(series_, **kw):
    """Compute by whatever route this series' own evidence permits.

    The gate below is a question about the SERIES, so each fixture is computed
    the honest way: the one that spans all three factors by Route 1, the two
    that cannot support Route 1 by Route 3 — which is the route this laboratory
    actually has. Forcing Route 1 onto a repeatability series is refused, and
    that refusal has its own suite at the bottom of this file.
    """
    if "rw_route" not in kw:
        if qc_series.coverage(series_.points).supports_reproducibility():
            kw["rw_route"] = uncertainty.RW_CONTROL_SAMPLE
            kw.setdefault("short_series_justification", JUSTIFIED)
        else:
            kw["rw_route"] = uncertainty.RW_TARGET_LIMITS
            kw.setdefault("control_limit", 2.8)
            kw.setdefault("control_limit_k", 2.0)
    return uncertainty.compute_from_series(series_, **kw)


class TestTheGate:

    def test_a_multi_analyst_multi_day_multi_calibration_series_is_u_Rw(self):
        est = _estimate(spans_analysts_days_and_calibrations())
        assert est.is_reproducibility() is True
        assert est.spread_label == uncertainty.LABEL_U_RW == "u(Rw)"
        assert est.u_rw_label == "u(Rw)"
        assert est.spread_basis == qc_series.BASIS_INTERMEDIATE
        assert est.u_rw == pytest.approx(S, rel=1e-12)

    def test_a_single_analyst_single_day_series_is_s_r_and_never_u_Rw(self):
        est = _estimate(one_analyst_one_day())
        assert est.is_reproducibility() is False
        assert est.spread_label == uncertainty.LABEL_S_R == "s_r"
        assert est.spread_basis == qc_series.BASIS_REPEATABILITY
        # The spread itself is still recorded — an assessor compares it with
        # the interim target — it just is not called u(Rw).
        assert est.s == pytest.approx(S, rel=1e-12)

    def test_missing_operator_data_is_unknown_and_never_multi_operator(self):
        """A row with no name says the analyst is UNKNOWN, never "somebody else"."""
        est = _estimate(unattributed())
        assert est.is_reproducibility() is False
        assert est.spread_basis == qc_series.BASIS_UNKNOWN
        assert est.n_operators == 0
        assert est.spread_label == uncertainty.LABEL_UNATTRIBUTED

    def test_two_analysts_over_two_days_on_ONE_calibration_is_not_u_Rw(self):
        """The third factor. Two of three varied is PARTIAL, and partial is not u(Rw).

        This is the case that passes a two-factor gate and should not: the
        between-calibration component was never sampled, and it is usually the
        largest one.
        """
        one_epoch = series("mach-1", "Cloud Point", [
            ("2026-08-03T08:00:00", 10.0, "Ryan", "cal-A"),
            ("2026-08-04T08:00:00", 12.0, "Dana", "cal-A"),
            ("2026-08-05T08:00:00", 14.0, "Ryan", "cal-A"),
            ("2026-08-06T08:00:00", 16.0, "Dana", "cal-A"),
            ("2026-08-07T08:00:00", 18.0, "Ryan", "cal-A")])
        est = _estimate(one_epoch)
        assert est.spread_basis == qc_series.BASIS_PARTIAL
        assert est.is_reproducibility() is False
        assert est.n_calibrations == 1
        assert est.n_operators == 2 and est.n_days == 5

    def test_case_only_differences_are_one_analyst_not_two(self):
        """"Ryan" and "ryan" must not manufacture the coverage this refuses to infer."""
        folded = series("mach-1", "Cloud Point", [
            ("2026-08-03T08:00:00", 10.0, "Ryan", "cal-A"),
            ("2026-08-04T08:00:00", 12.0, "ryan", "cal-B"),
            ("2026-08-05T08:00:00", 14.0, "RYAN", "cal-A"),
            ("2026-08-06T08:00:00", 16.0, "Ryan", "cal-B"),
            ("2026-08-07T08:00:00", 18.0, "ryan", "cal-A")])
        est = _estimate(folded)
        assert est.n_operators == 1
        assert est.is_reproducibility() is False


class TestThePredicateStandsOnItsOwn:
    """`is_reproducibility()` is read off ROWS, not only off fresh computations.

    FOUND BY MUTATION. Every other test in this file computes through
    `compute_from_series`, where the route gate has already refused a Route 1
    over a repeatability series — so `spread_basis == intermediate` was true of
    every measured-route estimate the suite ever built, and deleting the basis
    check from `is_reproducibility()` passed all 154 tests.

    That is the shape of bug this repo has been burned by: the feature broken,
    the suite green. The predicate is read on estimates coming BACK from
    `lem_uncertainty_estimates` — rows written by an older build, by a route
    added later, or by a hand correction on the database — and it has to answer
    from what the row says rather than from what the route implies.
    """

    BASES = (qc_series.BASIS_INTERMEDIATE, qc_series.BASIS_REPEATABILITY,
             qc_series.BASIS_PARTIAL, qc_series.BASIS_UNKNOWN,
             qc_series.BASIS_INSUFFICIENT)

    def _row(self, route, basis):
        return uncertainty.UncertaintyEstimate(
            machine_uid="mach-1", test_name="Cloud Point", estimate_id="x",
            rw_route=route, spread_basis=basis, n=5, s=S, u_rw=S)

    @pytest.mark.parametrize("basis", BASES)
    @pytest.mark.parametrize("route", uncertainty.RW_ROUTES)
    def test_only_a_measured_route_over_an_intermediate_spread_is_u_Rw(
            self, route, basis):
        expected = (route in uncertainty.MEASURED_RW_ROUTES
                    and basis == qc_series.BASIS_INTERMEDIATE)
        assert self._row(route, basis).is_reproducibility() is expected

    def test_a_stored_repeatability_row_on_route_one_is_still_not_u_Rw(self):
        """The row a looser build could have written. It must not read as u(Rw)."""
        stored = self._row(uncertainty.RW_CONTROL_SAMPLE,
                           qc_series.BASIS_REPEATABILITY)
        back = uncertainty.UncertaintyEstimate.from_row(stored.to_row())
        assert back.spread_basis == qc_series.BASIS_REPEATABILITY
        assert back.is_reproducibility() is False
        assert back.u_rw_label == uncertainty.LABEL_S_R

    def test_a_row_with_no_basis_at_all_reads_as_insufficient_not_as_u_Rw(self):
        """Absence is never a claim — the rule the whole module is built on."""
        back = uncertainty.UncertaintyEstimate.from_row(
            {"estimate_id": "y", "machine_uid": "m", "test_name": "t",
             "rw_route": uncertainty.RW_CONTROL_SAMPLE})
        assert back.spread_basis == qc_series.BASIS_INSUFFICIENT
        assert back.is_reproducibility() is False


class TestItIsTheSameOpinion:
    """Reuse, not a second opinion. qc_series decides; this module reports."""

    @pytest.mark.parametrize("build", [
        one_analyst_one_day, spans_analysts_days_and_calibrations, unattributed])
    def test_the_basis_is_exactly_what_qc_series_says(self, build):
        built = build()
        assert _estimate(built).spread_basis == qc_series.coverage(
            built.points).basis

    @pytest.mark.parametrize("build", [
        one_analyst_one_day, spans_analysts_days_and_calibrations, unattributed])
    def test_and_so_is_the_verdict(self, build):
        built = build()
        est = _estimate(built)
        assert est.is_reproducibility() == (
            qc_series.coverage(built.points).supports_reproducibility()
            and est.rw_route in uncertainty.MEASURED_RW_ROUTES)

    def test_the_counts_come_from_coverage_and_not_from_a_row_count(self):
        built = spans_analysts_days_and_calibrations()
        cov = qc_series.coverage(built.points)
        est = _estimate(built)
        assert (est.n_operators, est.n_days, est.n_calibrations) == (
            cov.n_operators, cov.n_days, cov.n_calibrations)
        assert est.n == cov.n

    def test_the_caveat_sentence_is_carried_into_the_record(self):
        built = one_analyst_one_day()
        assert qc_series.coverage(built.points).caveat() in _estimate(built).notes

    def test_uncertainty_does_not_reimplement_coverage(self):
        source = open(uncertainty.__file__, encoding="utf-8").read()
        # One import, one call site. A private `_coverage`/`_basis` here would
        # be the second opinion the brief forbids.
        assert "qc_series.coverage(" in source
        assert "\ndef coverage(" not in source
        assert "def _basis(" not in source
        assert "def _coverage(" not in source

    def test_and_it_does_not_reimplement_the_divisor_either(self):
        """`mean_and_s` is qc_series' — n-1, and `mean` rather than `fmean`."""
        source = open(uncertainty.__file__, encoding="utf-8").read()
        assert "qc_series.mean_and_s(" in source
        assert "statistics.stdev" not in source
        assert "pstdev" not in source


class TestWhatTheLabelIsAllowedToSay:

    def test_an_interim_target_is_labelled_as_a_target_not_as_measured(self):
        """Route 3's u(Rw) is not evidence of anything the instrument did.

        Even on a series whose coverage WOULD support u(Rw), the number
        returned by Route 3 came from a control limit somebody set. Calling it
        a measured within-laboratory reproducibility claims a measurement that
        was not made.
        """
        est = uncertainty.compute_from_series(
            spans_analysts_days_and_calibrations(),
            rw_route=uncertainty.RW_TARGET_LIMITS,
            control_limit=2.8, control_limit_k=2.0)
        assert est.spread_basis == qc_series.BASIS_INTERMEDIATE
        assert est.spread_label == uncertainty.LABEL_U_RW
        assert est.is_reproducibility() is False
        assert est.u_rw_label == uncertainty.LABEL_U_RW_TARGET
        assert "target" in est.u_rw_label.lower()

    def test_no_label_anywhere_says_shifts(self):
        """`coverage` counts calendar DATES. The log carries no shift boundary."""
        for build in (one_analyst_one_day, spans_analysts_days_and_calibrations,
                      unattributed):
            printed = " ".join(str(v) for v in
                               _estimate(build()).to_register_row().values())
            assert "shift" not in printed.lower()

    def test_the_register_row_carries_the_series_own_label_beside_the_number(self):
        """On the interim route the entry has to say BOTH things.

        The u(Rw) on the budget is a target; the spread the instrument actually
        showed is s_r. An entry that printed only the first would let a reader
        believe the second had been measured.
        """
        printed = str(_estimate(one_analyst_one_day()).to_register_row()["u_rw"])
        assert "s_r" in printed
        assert "target" in printed.lower()
        assert "target_limits" in printed

    def test_and_says_what_would_complete_it(self):
        """Spec gap 3's own prescription, word for word:

        *"The honest output when the spread is single-operator is s_r, clearly
        labelled, plus a note that duplicate-analysis data is needed to
        complete the estimate."*
        """
        printed = str(_estimate(one_analyst_one_day()).to_register_row()["u_rw"])
        assert "NOT a within-laboratory reproducibility" in printed
        assert "duplicate-analysis data" in printed

    def test_a_series_that_IS_u_Rw_carries_no_such_note(self):
        printed = str(_estimate(
            spans_analysts_days_and_calibrations()).to_register_row()["u_rw"])
        assert "duplicate-analysis data" not in printed


class TestRouteOneIsRefusedWhenTheSpreadIsNotUrw:
    """"Do not call a single-operator standard deviation u(Rw)."" — the spec.

    Labelling it correctly is not enough on its own: Route 1's identity IS
    `u(Rw) = s`, so on a series whose spread is s_r there is no Route 1 answer
    to give — and the refusal names the route that IS available.
    """

    def test_route_one_on_a_repeatability_series_is_refused(self):
        with pytest.raises(uncertainty.InsufficientEvidence) as caught:
            uncertainty.compute_from_series(
                one_analyst_one_day(),
                rw_route=uncertainty.RW_CONTROL_SAMPLE,
                short_series_justification=JUSTIFIED)
        said = str(caught.value).lower()
        assert "repeatability" in said or "s_r" in said
        assert "route 3" in said
        assert caught.value.route == uncertainty.RW_CONTROL_SAMPLE

    def test_and_on_an_unattributed_series_too(self):
        with pytest.raises(uncertainty.InsufficientEvidence):
            uncertainty.compute_from_series(
                unattributed(), rw_route=uncertainty.RW_CONTROL_SAMPLE,
                short_series_justification=JUSTIFIED)

    def test_and_on_a_partly_attributed_one(self):
        one_epoch = series("mach-1", "Cloud Point", [
            ("2026-08-0{}T08:00:00".format(3 + i), v,
             "Ryan" if i % 2 else "Dana", "cal-A")
            for i, v in enumerate((10.0, 12.0, 14.0, 16.0, 18.0))])
        with pytest.raises(uncertainty.InsufficientEvidence):
            uncertainty.compute_from_series(
                one_epoch, rw_route=uncertainty.RW_CONTROL_SAMPLE,
                short_series_justification=JUSTIFIED)

    def test_the_justification_does_not_buy_past_the_coverage_gate(self):
        """A sentence can excuse a SHORT series. It cannot excuse a wrong one.

        Data sufficiency is a judgement a technical manager may make. Whether a
        spread spans analysts is a fact about the log, and no amount of prose
        changes it.
        """
        with pytest.raises(uncertainty.InsufficientEvidence):
            uncertainty.compute_from_series(
                one_analyst_one_day(),
                rw_route=uncertainty.RW_CONTROL_SAMPLE,
                short_series_justification="The technical manager says so.")

    def test_route_evidence_agrees_with_the_refusal(self):
        verdict = uncertainty.route_evidence(one_analyst_one_day())[
            uncertainty.RW_CONTROL_SAMPLE]
        assert verdict.permitted is False
        assert "repeatability" in verdict.reason.lower()


class TestRouteTwo:
    """u(Rw) = sqrt(s^2 + s_r^2) — control sample plus duplicates.

        s   = sqrt(10) = 3.16227766      s^2   = 10
        s_r = 1.5                        s_r^2 = 2.25
        u(Rw) = sqrt(12.25)              = 3.5 exactly
    """

    def test_the_duplicates_term_is_added_in_quadrature(self):
        est = uncertainty.compute_from_series(
            spans_analysts_days_and_calibrations(),
            rw_route=uncertainty.RW_CONTROL_PLUS_DUPLICATES,
            s_r=1.5, s_r_n=12,
            short_series_justification=JUSTIFIED)
        assert est.u_rw == pytest.approx(3.5, rel=1e-12)
        assert est.u_rw > est.s          # never smaller than the control sample
        assert est.is_reproducibility() is True

    def test_s_r_is_recovered_exactly_from_what_is_stored(self):
        est = uncertainty.compute_from_series(
            spans_analysts_days_and_calibrations(),
            rw_route=uncertainty.RW_CONTROL_PLUS_DUPLICATES,
            s_r=1.5, s_r_n=12, short_series_justification=JUSTIFIED)
        assert est.s_r == pytest.approx(1.5, rel=1e-9)
        assert uncertainty.UncertaintyEstimate.from_row(
            est.to_row()).s_r == pytest.approx(1.5, rel=1e-9)

    def test_route_two_without_an_s_r_is_refused_not_silently_route_one(self):
        with pytest.raises(uncertainty.InsufficientEvidence) as caught:
            uncertainty.compute_from_series(
                spans_analysts_days_and_calibrations(),
                rw_route=uncertainty.RW_CONTROL_PLUS_DUPLICATES,
                short_series_justification=JUSTIFIED)
        assert "s_r" in str(caught.value)
