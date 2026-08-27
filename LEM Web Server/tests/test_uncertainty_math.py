#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The arithmetic of SOP QMU 1.001 §2.3–2.7, against numbers worked on paper.

EVERY expected value in this file was derived by hand from the series and then
written down twice — once as the arithmetic that produced it, once as a decimal
literal. Nothing here is asserted against what `uncertainty.py` returns, which
is the only way a test of a formula can catch the formula being wrong.

THE HAND-WORKED SERIES, used by almost everything below:

    values          10, 12, 14, 16, 18          n = 5
    mean            70 / 5                      = 14
    deviations      -4, -2, 0, +2, +4
    sum of squares  16 + 4 + 0 + 4 + 16         = 40
    s^2  (n-1)      40 / 4                      = 10
    s               sqrt(10)                    = 3.16227766...

    the WRONG divisor, kept here so the mutation is nameable:
    s^2  (n)        40 / 5                      = 8
    s               sqrt(8)                     = 2.82842712...

    certificate     cert_value 12.0, U 0.6, k 2.0
    u(Cref)         0.6 / 2                     = 0.3
    bias            14 - 12                     = 2.0
    s^2/n           10 / 5                      = 2.0
    u(bias)         sqrt(4 + 2 + 0.09)          = sqrt(6.09) = 2.46779255...
    u_c             sqrt(10 + 6.09)             = sqrt(16.09) = 4.01123423...
    U               2 * u_c                     = 8.02246845...

    method R        5.56  (chosen so 5.56 / 1.39 is exactly 4.0)
    r_ratio         8.02246845 / 4.0            = 2.00561711...

    and the relation this is NOT:
    5.56 / sqrt(2)                              = 3.93151063...
    8.02246845 / 3.93151063                     = 2.04055608...

The two r_ratios differ by 1.7%. That is the whole reason the spec calls it out:
a test written with `approx(rel=0.05)` passes on both.
"""

import math
import tokenize

import pytest

import uncertainty
from test_uncertainty_fixtures import series, spans_analysts_days_and_calibrations

# Hand-worked, from the docstring above.
MEAN = 14.0
S = math.sqrt(10.0)
S_POPULATION = math.sqrt(8.0)
U_CREF = 0.3
BIAS = 2.0
U_BIAS = math.sqrt(4.0 + 2.0 + 0.09)
U_C = math.sqrt(10.0 + 6.09)
U_EXPANDED = 2.0 * U_C
ASTM_R = 5.56
R_RATIO = U_EXPANDED / 4.0


@pytest.fixture
def cert():
    return uncertainty.Certificate(value=12.0, uncertainty=0.6, k=2.0,
                                   number="COA-1234", lot="L-9")


@pytest.fixture
def five():
    """The hand-worked series, with coverage that permits Route 1."""
    return spans_analysts_days_and_calibrations()


def _route1(series_, **kw):
    """Route 1 on a 24-day series needs the short-series justification.

    Every call in this file passes one, because this file is about the
    ARITHMETIC. Whether the evidence permits Route 1 at all is
    test_uncertainty_routes.py's question, and mixing the two is how a maths
    test comes to depend on a policy threshold.
    """
    kw.setdefault("short_series_justification",
                  "Arithmetic fixture; sufficiency is tested elsewhere.")
    return uncertainty.compute_from_series(series_, **kw)


class TestMeanAndSpread:

    def test_mean_and_s_match_the_hand_worked_values(self, five):
        est = _route1(five)
        assert est.n == 5
        assert est.mean == pytest.approx(MEAN, rel=1e-12)
        assert est.s == pytest.approx(S, rel=1e-12)
        assert est.s == pytest.approx(3.1622776601683795, rel=1e-12)

    def test_s_uses_the_n_minus_one_divisor_and_not_n(self, five):
        est = _route1(five)
        # The whole point: these two are 12% apart on this series, and the
        # population figure reports the instrument as tighter than it is.
        assert est.s != pytest.approx(S_POPULATION, rel=1e-3)
        assert est.s_df == 4

    def test_a_single_result_has_no_spread_and_the_estimate_is_refused(self):
        one = series("mach-1", "Cloud Point",
                     [("2026-08-10T08:00:00", 7.0, "Ryan", "cal-1")])
        with pytest.raises(uncertainty.InsufficientEvidence) as caught:
            _route1(one)
        # Not a crash and not a zero — a sentence naming what is missing.
        said = str(caught.value).lower()
        assert "no spread" in said and "1 result" in said

    def test_an_empty_series_is_refused_the_same_way(self):
        empty = series("mach-1", "Cloud Point", [])
        with pytest.raises(uncertainty.InsufficientEvidence):
            _route1(empty)


class TestTheBiasHalf:

    def test_u_cref_is_the_certificate_uncertainty_over_its_own_k(self, cert):
        assert cert.u_cref() == pytest.approx(U_CREF, rel=1e-12)
        # k is the CERTIFICATE's coverage factor, not the lab's k=2 default.
        k3 = uncertainty.Certificate(value=1.0, uncertainty=0.6, k=3.0)
        assert k3.u_cref() == pytest.approx(0.2, rel=1e-12)

    def test_bias_is_mean_minus_certified_value(self, five, cert):
        est = _route1(five, certificate=cert)
        assert est.bias == pytest.approx(BIAS, rel=1e-12)
        assert est.cert_value == pytest.approx(12.0, rel=1e-12)

    def test_u_bias_combines_bias_the_mean_and_the_certificate(self, five, cert):
        est = _route1(five, certificate=cert)
        assert est.u_cref == pytest.approx(U_CREF, rel=1e-12)
        assert est.u_bias == pytest.approx(U_BIAS, rel=1e-12)
        assert est.u_bias == pytest.approx(2.4677925, rel=1e-7)
        assert est.bias_route == uncertainty.BIAS_CRM

    def test_the_s_squared_over_n_term_is_really_there(self, five, cert):
        """Dropping it gives sqrt(4 + 0.09) = 2.0224, which is 18% low."""
        est = _route1(five, certificate=cert)
        assert est.u_bias != pytest.approx(math.sqrt(4.0 + 0.09), rel=1e-3)

    def test_a_certificate_with_no_stated_uncertainty_produces_no_bias_term(
            self, five):
        """Gap 1. A certified VALUE is not a certified UNCERTAINTY."""
        est = _route1(five, certificate=uncertainty.Certificate(value=12.0))
        assert est.bias is None
        assert est.u_bias is None
        assert est.u_cref is None
        assert est.bias_route == uncertainty.BIAS_NONE

    def test_and_it_says_why_rather_than_omitting_it(self, five):
        est = _route1(five, certificate=uncertainty.Certificate(value=12.0))
        assert est.is_partial()
        assert "u(bias)" in est.missing_terms
        why = est.missing_terms["u(bias)"].lower()
        assert "certificate" in why and "uncertainty" in why
        # It has to be visible in the record an assessor reads, not only on the
        # object a route happened to hold.
        assert "u(bias)" in str(est.to_register_row()["u_bias"])

    def test_the_sentence_says_WHICH_half_of_the_certificate_is_missing(
            self, five):
        """"No certificate on file" and "a value with no stated uncertainty"
        send a person to two different places. The second is this laboratory's
        actual position on Cloud CRM, Pour CRM and Pentane."""
        no_cert = _route1(five).missing_terms["u(bias)"]
        assert "no certified value is bound" in no_cert

        value_only = _route1(
            five, certificate=uncertainty.Certificate(value=12.0)
        ).missing_terms["u(bias)"]
        assert "certified value 12 is on file" in value_only
        assert "uncertainty is not" in value_only

    def test_no_certificate_at_all_is_the_same_refusal(self, five):
        est = _route1(five)
        assert est.bias is None and est.u_bias is None
        assert est.bias_route == uncertainty.BIAS_NONE
        assert est.is_partial()

    def test_the_module_never_reads_a_control_limit_as_a_certificate(self):
        """The single most likely way to get this wrong (spec gap 1).

        `Certificate` cannot be built from a `QcSampleTest`, and this module
        does not import the one that defines it. Cloud CRM's std_dev 2.8 at
        k 1.0 is a PASS BAND. If it ever became u(Cref) = 2.8 the answer would
        look entirely plausible and be wrong by an order of magnitude.
        """
        source = open(uncertainty.__file__, encoding="utf-8").read()
        assert "import qc_samples" not in source
        assert "from qc_samples" not in source
        # `std_dev` may be WARNED about in prose and must not appear in code.
        # Comments and strings are stripped rather than searched for, because a
        # docstring that mentions the trap is exactly what should be there.
        code = []
        with open(uncertainty.__file__, "rb") as fh:
            for token in tokenize.tokenize(fh.readline):
                if token.type not in (tokenize.COMMENT, tokenize.STRING):
                    code.append(token.string)
        assert "std_dev" not in " ".join(code)


class TestCombining:

    def test_u_c_is_the_root_sum_of_squares(self, five, cert):
        est = _route1(five, certificate=cert)
        assert est.u_c == pytest.approx(U_C, rel=1e-12)
        assert est.u_c == pytest.approx(4.0112342, rel=1e-7)

    def test_U_is_two_u_c_and_k_is_recorded_as_two(self, five, cert):
        est = _route1(five, certificate=cert)
        assert est.k == 2.0
        assert est.u_expanded == pytest.approx(U_EXPANDED, rel=1e-12)
        assert est.u_expanded == pytest.approx(2.0 * est.u_c, rel=1e-12)

    def test_with_no_bias_term_u_c_is_the_repeatability_half_alone(self, five):
        est = _route1(five)
        assert est.u_c == pytest.approx(S, rel=1e-12)
        assert est.u_expanded == pytest.approx(2.0 * S, rel=1e-12)

    def test_a_named_k_other_than_two_is_carried_not_silently_doubled(
            self, five, cert):
        est = _route1(five, certificate=cert, k=2.58)
        assert est.k == 2.58
        assert est.u_expanded == pytest.approx(2.58 * U_C, rel=1e-12)


class TestTheRRatio:

    def test_r_ratio_divides_R_by_1_39(self, five, cert):
        est = _route1(five, certificate=cert, astm_r=ASTM_R)
        assert est.astm_r == ASTM_R
        assert est.r_ratio == pytest.approx(R_RATIO, rel=1e-12)
        assert est.r_ratio == pytest.approx(2.0056171, rel=1e-7)

    def test_and_NOT_by_root_two(self, five, cert):
        """The trap the spec names. 1.7% apart — a sloppy tolerance passes both."""
        est = _route1(five, certificate=cert, astm_r=ASTM_R)
        wrong = U_EXPANDED / (ASTM_R / math.sqrt(2.0))
        assert wrong == pytest.approx(2.0405561, rel=1e-6)
        assert est.r_ratio != pytest.approx(wrong, rel=1e-3)

    def test_the_divisor_is_a_named_constant_at_the_stated_value(self):
        assert uncertainty.R_TO_U == 1.39
        assert uncertainty.R_TO_S_R == 2.77

    def test_no_method_R_means_no_ratio_rather_than_a_zero(self, five, cert):
        est = _route1(five, certificate=cert)
        assert est.astm_r is None
        assert est.r_ratio is None
        assert "2.7" in est.missing_terms.get("r_ratio", "")

    def test_a_ratio_far_above_one_is_reported_as_needing_2_9(self, five, cert):
        est = _route1(five, certificate=cert, astm_r=1.0)
        # U = 8.02, R/1.39 = 0.719 -> ratio 11.2
        assert est.r_ratio == pytest.approx(U_EXPANDED / (1.0 / 1.39), rel=1e-12)
        assert est.r_ratio_verdict == uncertainty.R_RATIO_HIGH
        assert "2.9" in est.r_ratio_sentence

    def test_a_ratio_far_below_one_is_reported_as_input_data(self, five, cert):
        est = _route1(five, certificate=cert, astm_r=1000.0)
        assert est.r_ratio_verdict == uncertainty.R_RATIO_LOW
        assert "variability" in est.r_ratio_sentence.lower()

    def test_a_ratio_near_one_is_consistent(self, five, cert):
        # Choose R so that R/1.39 == U exactly: R = U * 1.39
        est = _route1(five, certificate=cert, astm_r=U_EXPANDED * 1.39)
        assert est.r_ratio == pytest.approx(1.0, rel=1e-12)
        assert est.r_ratio_verdict == uncertainty.R_RATIO_CONSISTENT


class TestTheMultiCrmExtension:
    """SOP 2.5 / TR 537: RMS_bias replaces bias and the s^2/n term is DROPPED.

        biases      +1.0, -2.0, +2.0        n_CRM = 3
        sum sq      1 + 4 + 4               = 9
        /n_CRM      9 / 3                   = 3
        RMS         sqrt(3)                 = 1.73205081...
        u(Cref)     0.3
        u(bias)     sqrt(3 + 0.09)          = sqrt(3.09) = 1.75783958...
    """

    def test_rms_bias(self):
        assert uncertainty.rms_bias([1.0, -2.0, 2.0]) == pytest.approx(
            math.sqrt(3.0), rel=1e-12)
        assert uncertainty.rms_bias([1.0, -2.0, 2.0]) == pytest.approx(
            1.7320508, rel=1e-7)

    def test_u_bias_over_several_crms_drops_the_s_squared_over_n_term(self):
        got = uncertainty.u_bias_multi_crm([1.0, -2.0, 2.0], 0.3)
        assert got == pytest.approx(math.sqrt(3.0 + 0.09), rel=1e-12)
        assert got == pytest.approx(1.7578396, rel=1e-7)

    def test_an_empty_list_of_crms_is_no_answer_rather_than_zero(self):
        assert uncertainty.rms_bias([]) is None
        assert uncertainty.u_bias_multi_crm([], 0.3) is None

    def test_a_single_crm_here_is_still_not_the_single_crm_route(self):
        """sqrt(bias^2/1) is |bias|, and it deliberately has no s^2/n in it.

        Kept as a test rather than a comment because someone reaching for this
        function on one material would otherwise get an answer that silently
        omits the mean's own uncertainty.
        """
        assert uncertainty.rms_bias([2.0]) == pytest.approx(2.0, rel=1e-12)
        assert uncertainty.u_bias_multi_crm([2.0], 0.3) == pytest.approx(
            math.sqrt(4.0 + 0.09), rel=1e-12)


class TestPureHelpers:
    """The formulas on their own, so a route change cannot hide a maths change."""

    def test_combine(self):
        assert uncertainty.combine(3.0, 4.0) == pytest.approx(5.0, rel=1e-12)
        assert uncertainty.combine(3.0, None) == pytest.approx(3.0, rel=1e-12)
        assert uncertainty.combine(None, None) is None

    def test_expand(self):
        assert uncertainty.expand(4.0, 2.0) == pytest.approx(8.0, rel=1e-12)
        assert uncertainty.expand(None, 2.0) is None

    def test_u_bias_single_crm(self):
        assert uncertainty.u_bias_single_crm(2.0, 10.0, 5, 0.3) == pytest.approx(
            math.sqrt(6.09), rel=1e-12)

    def test_r_ratio_helper(self):
        assert uncertainty.r_ratio(8.0, 5.56) == pytest.approx(
            8.0 / (5.56 / 1.39), rel=1e-12)
        assert uncertainty.r_ratio(8.0, 0.0) is None
        assert uncertainty.r_ratio(None, 5.56) is None
