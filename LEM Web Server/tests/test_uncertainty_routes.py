#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SOP 2.4: three routes to u(Rw), and which one THIS lab's evidence permits.

MEASURED, 2026-08-27, against the live LabCore:

    lem_machine_log begins 2026-08-03.  Twenty-four days.
    773 QC rows in total.
    Best (machine, test) series: 115 results over 12 distinct calendar days.
    Most: 12-29 results over 6-15 days.

TR 537 wants ideally more than 60 results over at least a year for the
control-sample route. **No series in this laboratory qualifies on time span.**

So Route 3 — the interim target-limits route — is the route this laboratory
uses for its first estimates, and it is built as a first-class path carrying a
replacement date. Route 1 stays, stays preferred, and is refused on evidence
that cannot support it. These suites are the honesty gate on that decision.
"""

from datetime import datetime, timedelta

import pytest

import uncertainty
from test_uncertainty_fixtures import (one_analyst_one_day, series,
                                       spans_analysts_days_and_calibrations,
                                       unattributed)

NOW = datetime(2026, 8, 27, 9, 0, 0)


def _lab_shaped_series(uid="mach-1", test="Cloud Point", n=29, start_day=3):
    """A series the size and shape the live log actually holds.

    29 results over 15 calendar days, two analysts, two calibration epochs —
    the healthy end of what this lab has. It clears every coverage gate and
    still cannot clear a year.
    """
    entries = []
    for i in range(n):
        day = start_day + (i % 15)
        entries.append((
            "2026-08-{:02d}T{:02d}:00:00".format(day, 8 + (i % 8)),
            -7.4 + ((i % 7) - 3) * 0.4,
            "Ryan" if i % 2 else "Dana",
            "2026-08-01T09:00:00" if i < n // 2 else "2026-08-14T09:00:00"))
    return series(uid, test, entries)


class TestWhatTheEvidencePermitsToday:

    def test_a_twenty_four_day_series_does_not_permit_route_one(self):
        verdicts = uncertainty.route_evidence(_lab_shaped_series(), now=NOW)
        route1 = verdicts[uncertainty.RW_CONTROL_SAMPLE]
        assert route1.permitted is False
        # The sentence has to name BOTH thresholds and BOTH actuals, or nobody
        # can tell whether it is the count or the span that is short.
        assert "60" in route1.reason
        assert "365" in route1.reason or "year" in route1.reason
        assert "29" in route1.reason
        assert "15" in route1.reason

    def test_the_route_the_evidence_permits_today_is_target_limits(self):
        chosen = uncertainty.preferred_route(
            _lab_shaped_series(), control_limit=2.8, now=NOW)
        assert chosen == uncertainty.RW_TARGET_LIMITS

    def test_with_no_control_limit_there_is_no_permitted_route_at_all(self):
        """"No route is permitted" is an answer. A silent fallback is not."""
        verdicts = uncertainty.route_evidence(_lab_shaped_series(), now=NOW)
        assert all(not v.permitted for v in verdicts.values())
        assert uncertainty.preferred_route(_lab_shaped_series(), now=NOW) is None

    def test_route_one_becomes_permitted_once_the_data_supports_it(self):
        """It must still exist, and still be preferred, when the log grows up."""
        entries = []
        base = datetime(2025, 6, 1, 8, 0, 0)
        for i in range(70):
            when = base + timedelta(days=i * 6)
            entries.append((when.isoformat(), -7.4 + (i % 5) * 0.3,
                            "Ryan" if i % 2 else "Dana",
                            "cal-{}".format(i // 20)))
        grown = series("mach-1", "Cloud Point", entries)
        verdicts = uncertainty.route_evidence(grown, control_limit=2.8,
                                              now=datetime(2026, 8, 27))
        assert verdicts[uncertainty.RW_CONTROL_SAMPLE].permitted is True
        assert uncertainty.preferred_route(
            grown, control_limit=2.8,
            now=datetime(2026, 8, 27)) == uncertainty.RW_CONTROL_SAMPLE

    def test_every_route_gets_a_sentence_whether_permitted_or_not(self):
        verdicts = uncertainty.route_evidence(
            _lab_shaped_series(), control_limit=2.8, now=NOW)
        assert set(verdicts) == set(uncertainty.RW_ROUTES)
        for route, verdict in verdicts.items():
            assert verdict.route == route
            assert verdict.reason.strip(), route

    def test_the_thresholds_are_named_constants_at_TR_537s_numbers(self):
        assert uncertainty.TR537_MIN_RESULTS == 60
        assert uncertainty.TR537_MIN_SPAN_DAYS == 365


class TestRouteThreeIsFirstClass:

    def test_u_rw_is_half_the_control_limit(self):
        """Cloud CRM as the library actually holds it: expected -7.4, std_dev
        2.8, k 1.0 — so the control limit is a half-width of 2.8 and u(Rw) is
        1.4."""
        est = uncertainty.compute_from_series(
            _lab_shaped_series(), rw_route=uncertainty.RW_TARGET_LIMITS,
            control_limit=2.8, control_limit_k=1.0, now=NOW)
        assert est.u_rw == pytest.approx(1.4, rel=1e-12)
        assert est.rw_route == uncertainty.RW_TARGET_LIMITS
        assert est.control_limit == pytest.approx(2.8, rel=1e-12)

    def test_it_does_not_use_the_series_spread_at_all(self):
        """Two series with wildly different spreads, one control limit."""
        wide = uncertainty.compute_from_series(
            spans_analysts_days_and_calibrations(),
            rw_route=uncertainty.RW_TARGET_LIMITS, control_limit=2.8, now=NOW)
        narrow = uncertainty.compute_from_series(
            _lab_shaped_series(), rw_route=uncertainty.RW_TARGET_LIMITS,
            control_limit=2.8, now=NOW)
        assert wide.u_rw == narrow.u_rw == pytest.approx(1.4, rel=1e-12)
        # …and s is still recorded, because an assessor compares the two.
        assert wide.s != pytest.approx(narrow.s, rel=1e-3)

    def test_it_carries_a_replacement_date(self):
        est = uncertainty.compute_from_series(
            _lab_shaped_series(), rw_route=uncertainty.RW_TARGET_LIMITS,
            control_limit=2.8, now=NOW)
        assert est.replace_by
        assert est.replace_by == (
            NOW + timedelta(days=uncertainty.INTERIM_VALID_DAYS)
        ).date().isoformat()
        assert est.replace_by in str(est.to_register_row()["review"])

    def test_a_named_replacement_date_wins_over_the_default(self):
        est = uncertainty.compute_from_series(
            _lab_shaped_series(), rw_route=uncertainty.RW_TARGET_LIMITS,
            control_limit=2.8, replace_by="2026-12-31", now=NOW)
        assert est.replace_by == "2026-12-31"

    def test_a_measured_route_carries_no_replacement_date(self):
        est = uncertainty.compute_from_series(
            spans_analysts_days_and_calibrations(),
            short_series_justification="fixture", now=NOW)
        assert est.replace_by == ""

    def test_route_three_without_a_control_limit_is_refused(self):
        with pytest.raises(uncertainty.InsufficientEvidence) as caught:
            uncertainty.compute_from_series(
                _lab_shaped_series(), rw_route=uncertainty.RW_TARGET_LIMITS,
                now=NOW)
        assert "control limit" in str(caught.value).lower()

    def test_a_zero_or_negative_control_limit_is_refused_not_a_zero_u_Rw(self):
        for bad in (0.0, -2.8):
            with pytest.raises(uncertainty.InsufficientEvidence):
                uncertainty.compute_from_series(
                    _lab_shaped_series(),
                    rw_route=uncertainty.RW_TARGET_LIMITS,
                    control_limit=bad, now=NOW)

    def test_route_three_needs_no_coverage_and_no_justification(self):
        """It is the route for a lab that has nothing else. It cannot be gated
        on the evidence it exists in the absence of."""
        est = uncertainty.compute_from_series(
            one_analyst_one_day(), rw_route=uncertainty.RW_TARGET_LIMITS,
            control_limit=2.8, now=NOW)
        assert est.u_rw == pytest.approx(1.4, rel=1e-12)

    def test_it_works_on_a_series_too_short_to_have_an_s_at_all(self):
        one = series("mach-1", "Cloud Point",
                     [("2026-08-10T08:00:00", -7.0, "Ryan", "cal-1")])
        est = uncertainty.compute_from_series(
            one, rw_route=uncertainty.RW_TARGET_LIMITS, control_limit=2.8,
            now=NOW)
        assert est.u_rw == pytest.approx(1.4, rel=1e-12)
        assert est.s is None
        assert est.n == 1


class TestTheControlLimitsOwnCoverageFactor:
    """The trap inside the trap.

    `u(Rw) = control_limit / 2` reads the control limit as a 2s bound. Three of
    this library's four standards state k = 1.0 — Cloud CRM is expected -7.4,
    std_dev 2.8, k 1.0 — so their pass band is a ONE-sigma band, and halving it
    gives std_dev/2, which understates u(Rw) by a factor of two.
    """

    def test_a_k_of_two_is_the_assumption_and_is_recorded_as_met(self):
        est = uncertainty.compute_from_series(
            _lab_shaped_series(), rw_route=uncertainty.RW_TARGET_LIMITS,
            control_limit=5.6, control_limit_k=2.0, now=NOW)
        assert est.control_limit_k == 2.0
        assert "control_limit_k" not in est.missing_terms

    def test_a_k_of_one_is_flagged_loudly_and_not_silently_halved(self):
        est = uncertainty.compute_from_series(
            _lab_shaped_series(), rw_route=uncertainty.RW_TARGET_LIMITS,
            control_limit=2.8, control_limit_k=1.0, now=NOW)
        assert est.u_rw == pytest.approx(1.4, rel=1e-12)
        flag = est.missing_terms.get("control_limit_k", "")
        assert "2" in flag and "1" in flag
        assert "understate" in flag.lower()
        assert flag in est.notes

    def test_an_unstated_k_is_unknown_and_says_so(self):
        est = uncertainty.compute_from_series(
            _lab_shaped_series(), rw_route=uncertainty.RW_TARGET_LIMITS,
            control_limit=2.8, now=NOW)
        assert est.control_limit_k is None
        assert "control_limit_k" in est.missing_terms

    def test_the_half_width_helper_reads_a_band_the_way_the_log_writes_one(self):
        assert uncertainty.control_limit_from_band(-10.2, -4.6) == pytest.approx(
            2.8, rel=1e-12)
        assert uncertainty.control_limit_from_band(None, -4.6) is None
        assert uncertainty.control_limit_from_band(-4.6, -10.2) is None


class TestTheShortSeriesJustification:

    def test_route_one_on_a_short_series_needs_a_written_justification(self):
        with pytest.raises(uncertainty.InsufficientEvidence):
            uncertainty.compute_from_series(_lab_shaped_series(), now=NOW)

    def test_and_the_justification_is_recorded_where_an_assessor_reads_it(self):
        est = uncertainty.compute_from_series(
            _lab_shaped_series(),
            short_series_justification="Technical manager R.C. accepted 29 "
                                       "results over 15 days as interim.",
            now=NOW)
        assert "Technical manager R.C." in est.notes
        assert "Technical manager R.C." in str(est.to_register_row()["u_rw"])
        assert any("short series" in str(c.get("name", "")).lower()
                   for c in est.contributions)

    def test_a_blank_justification_is_no_justification(self):
        for blank in ("", "   ", "\t\n"):
            with pytest.raises(uncertainty.InsufficientEvidence):
                uncertainty.compute_from_series(
                    _lab_shaped_series(), short_series_justification=blank,
                    now=NOW)


class TestContributionsAreRecordedIncludingNegligibleOnes:
    """SOP 2.2 — the list of contributions CONSIDERED, not the list used."""

    def test_the_two_terms_are_always_listed_even_when_absent(self):
        est = uncertainty.compute_from_series(
            _lab_shaped_series(), rw_route=uncertainty.RW_TARGET_LIMITS,
            control_limit=2.8, now=NOW)
        names = [c["name"] for c in est.contributions]
        assert "u(Rw)" in names and "u(bias)" in names
        absent = [c for c in est.contributions if c["name"] == "u(bias)"][0]
        assert absent["included"] is False
        assert absent["basis"]

    def test_a_callers_own_contributions_are_kept(self):
        est = uncertainty.compute_from_series(
            _lab_shaped_series(), rw_route=uncertainty.RW_TARGET_LIMITS,
            control_limit=2.8, now=NOW,
            contributions=[{"name": "sample homogeneity", "included": False,
                            "basis": "single-phase liquid; judged negligible"}])
        names = [c["name"] for c in est.contributions]
        assert "sample homogeneity" in names

    def test_they_survive_the_json_round_trip(self):
        est = uncertainty.compute_from_series(
            _lab_shaped_series(), rw_route=uncertainty.RW_TARGET_LIMITS,
            control_limit=2.8, now=NOW)
        back = uncertainty.UncertaintyEstimate.from_row(est.to_row())
        assert back.contributions == est.contributions
