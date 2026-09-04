"""The statistics of a QC control series — `qc_series.py`.

What these tests are guarding against, in order of how badly it has bitten:

1. **n vs n-1.** Every number on a control chart hangs off `s`, and the
   population divisor makes an instrument look tighter than it is. The two
   fixtures below are hand-worked: `SPREAD` has a population sd of exactly
   2.0 and a sample sd of sqrt(32/7), and `TEXTBOOK` is the worked example
   whose sample sd is 5.2372 against a population 4.8990. Both assert the
   sample value AND assert it is not the population value, because an
   implementation that returns 2.0 passes any test written loosely enough.

2. **Zones are not the pass band.** `mean +/- 3s` is what the instrument has
   actually been doing; `low`/`high` is what the certificate says it must do.
   Conflating them makes a wide certificate hide a drifting process, and a
   tight one condemn a stable one.

3. **A detector that fires on everything.** Every rule here has a trigger
   series and a NEAR-MISS series that must stay silent — usually one point
   sitting exactly on the limit, or one short of the run length.

Numbers are hand-computed and shown in the comment beside them. Nothing here
asserts against whatever the implementation happens to return.
"""
import math

import pytest

import qc_series as qs


# ── hand-worked fixtures ─────────────────────────────────────────────────────
#
# SPREAD: n=8, sum=40, mean=5.0, sum of squared deviations = 32.
#   population sd = sqrt(32/8) = 2.0 exactly    <- the wrong answer
#   sample sd     = sqrt(32/7) = 2.1380899...   <- the right one
SPREAD = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]

# TEXTBOOK: n=8, sum=144, mean=18.0, sum of squared deviations = 192.
#   population sd = sqrt(192/8) = sqrt(24) = 4.8989794...
#   sample sd     = sqrt(192/7) = 5.2372293...
TEXTBOOK = [10.0, 12.0, 23.0, 23.0, 16.0, 23.0, 21.0, 16.0]

# EXACT: n=5, sum=500, mean=100.0, SS = 400, sample var = 400/4 = 100,
# so s = 10.0 with no floating-point residue at all. Every zone lands on a
# round number: 1s 90/110, 2s 80/120, 3s 70/130. Used for the rule tests so a
# limit can be sat on EXACTLY.
EXACT = [90.0, 90.0, 100.0, 110.0, 110.0]


def row(ts, value, test_name="Flash Point", machine_uid="m1",
        detail=None, lab_id="AO25"):
    """One `lem_machine_log` row as `_qc_events` hands it over.

    `value` is TEXT in that table and `detail` is a JSON string, so the
    fixture stores them the way LabCore really does rather than the way a
    Python caller would like them.
    """
    import json
    return {"machine_uid": machine_uid, "ts": ts, "lab_id": lab_id,
            "test_name": test_name, "value": str(value),
            "detail": json.dumps(detail if detail is not None else {})}


def rows_at_hourly(values, day="2026-08-20", **kw):
    """Values one hour apart on one day — order matters to every run rule."""
    return [row(f"{day}T{8 + i:02d}:00:00", v, **kw)
            for i, v in enumerate(values)]


# ── reading the log ──────────────────────────────────────────────────────────

class TestPointsFromRows:
    def test_reads_value_ts_and_the_spec_verdict(self):
        pts = qs.points_from_rows([
            row("2026-08-20T08:00:00", 63.7,
                detail={"in_spec": True, "low": 61.6, "high": 65.8,
                        "expected": 63.7})])
        assert len(pts) == 1
        assert pts[0].value == 63.7
        assert pts[0].ts == "2026-08-20T08:00:00"
        assert pts[0].in_spec is True
        assert pts[0].test_name == "Flash Point"
        assert pts[0].machine_uid == "m1"

    def test_a_non_numeric_value_is_not_a_point(self):
        # "ok" in the record is a reading nobody can ever judge against a band.
        pts = qs.points_from_rows([row("2026-08-20T08:00:00", "ok"),
                                   row("2026-08-20T09:00:00", 5.0)])
        assert [p.value for p in pts] == [5.0]

    def test_unparseable_detail_is_an_empty_detail_not_a_crash(self):
        r = row("2026-08-20T08:00:00", 5.0)
        r["detail"] = "{not json"
        pts = qs.points_from_rows([r])
        assert len(pts) == 1
        assert pts[0].in_spec is None

    def test_a_NaN_is_not_a_reading(self):
        # `float("nan")` succeeds, so a NaN survives every "is this a number"
        # check and then compares FALSE against every limit — a point that
        # sits on the chart and that no rule can ever fire on. It also poisons
        # the mean and the spread of every other result in the series. This
        # tree has hidden one on every instrument for a round before.
        pts = qs.points_from_rows([row("2026-08-20T08:00:00", "nan"),
                                   row("2026-08-20T09:00:00", "NaN"),
                                   row("2026-08-20T10:00:00", 5.0),
                                   row("2026-08-20T11:00:00", 7.0)])
        assert [p.value for p in pts] == [5.0, 7.0]
        assert qs.mean_and_s([p.value for p in pts]) == (6.0, pytest.approx(
            math.sqrt(2.0)))

    def test_an_infinity_is_not_a_reading_either(self):
        pts = qs.points_from_rows([row("2026-08-20T08:00:00", "inf"),
                                   row("2026-08-20T09:00:00", "-Infinity"),
                                   row("2026-08-20T10:00:00", 5.0)])
        assert [p.value for p in pts] == [5.0]

    def test_a_row_with_no_test_name_is_dropped(self):
        pts = qs.points_from_rows([row("2026-08-20T08:00:00", 5.0,
                                       test_name="  ")])
        assert pts == ()

    def test_non_qc_rows_are_dropped_when_the_kind_column_is_present(self):
        # `_qc_events` filters kind='qc' in SQL and does not select the column,
        # so absent must mean "already filtered" — but a caller passing whole
        # log rows must not get a PM completion counted as a QC result.
        pm = row("2026-08-20T08:00:00", 5.0)
        pm["kind"] = "pm"
        qc = row("2026-08-20T09:00:00", 6.0)
        qc["kind"] = "qc"
        assert [p.value for p in qs.points_from_rows([pm, qc])] == [6.0]

    def test_points_are_ordered_by_time_not_by_the_callers_query(self):
        # Every run rule below is order-dependent, so the module sorts rather
        # than trusting whatever ORDER BY the caller wrote.
        pts = qs.points_from_rows([row("2026-08-20T10:00:00", 3.0),
                                   row("2026-08-20T08:00:00", 1.0),
                                   row("2026-08-20T09:00:00", 2.0)])
        assert [p.value for p in pts] == [1.0, 2.0, 3.0]


class TestSeriesFromRows:
    def test_one_series_per_machine_test_and_standard(self):
        # The standard is the third part of the key — see
        # `TestAChangeoverStartsANewChart` for why it has to be.
        rows = (rows_at_hourly([1.0, 2.0], test_name="Flash Point")
                + rows_at_hourly([3.0], test_name="Cloud Point")
                + rows_at_hourly([4.0], test_name="Flash Point",
                                 machine_uid="m2"))
        got = qs.series_from_rows(rows)
        assert set(got) == {("m1", "Flash Point", "AO25"),
                            ("m1", "Cloud Point", "AO25"),
                            ("m2", "Flash Point", "AO25")}
        assert [p.value for p in
                got[("m1", "Flash Point", "AO25")].points] == [1.0, 2.0]

    def test_the_pass_band_is_the_latest_one_the_log_carries(self):
        # The SAME lot re-assayed: the band moves and the chart draws the
        # one in force now, the newest row's, not the first row's. A move
        # to a DIFFERENT standard is not this — that starts a new chart
        # rather than moving this one's band. Both rows are AO25.
        rows = [row("2026-08-20T08:00:00", 63.0,
                    detail={"low": 61.0, "high": 65.0, "expected": 63.0}),
                row("2026-08-20T09:00:00", 63.5,
                    detail={"low": 62.0, "high": 66.0, "expected": 64.0})]
        band = qs.series_from_rows(rows)[
            ("m1", "Flash Point", "AO25")].pass_band
        assert (band.low, band.high, band.expected) == (62.0, 66.0, 64.0)

    def test_a_series_with_no_band_in_the_log_has_no_pass_band(self):
        # Not a band of (0, 0) — a spec nobody recorded is unknown, and a zero
        # width band would report every result out of spec.
        s = qs.series_from_rows(
            rows_at_hourly([1.0, 2.0]))[("m1", "Flash Point", "AO25")]
        assert s.pass_band is None

    def test_series_for_picks_one_out(self):
        rows = (rows_at_hourly([1.0, 2.0], test_name="Flash Point")
                + rows_at_hourly([3.0], test_name="Cloud Point"))
        s = qs.series_for(rows, "m1", "Cloud Point")
        assert [p.value for p in s.points] == [3.0]

    def test_series_for_an_unknown_test_is_empty_not_an_error(self):
        s = qs.series_for(rows_at_hourly([1.0]), "m1", "Nothing")
        assert s.points == ()
        assert s.test_name == "Nothing"

    def test_a_non_qc_row_cannot_supply_the_band_or_the_sample_id(self):
        # `points_from_rows` drops a PM row; the band/sample scan must drop it
        # too. A maintenance record sharing the machine and the test name
        # otherwise overwrites the certificate's band with (0, 0.001) and the
        # sample id with its own — and every QC result then reads out of spec
        # against limits from a maintenance record.
        band = {"in_spec": True, "low": 61.6, "high": 65.8, "expected": 63.7}
        rows = []
        for i, value in enumerate((63.7, 63.8)):
            r = row(f"2026-08-20T0{8 + i}:00:00", value, detail=band,
                    lab_id="AO25")
            r["kind"] = "qc"
            rows.append(r)
        pm = row("2026-08-20T10:00:00", 0.0, lab_id="WRONG-SAMPLE",
                 detail={"low": 0.0, "high": 0.001})
        pm["kind"] = "pm"
        rows.append(pm)

        s = qs.series_from_rows(rows)[("m1", "Flash Point", "AO25")]
        assert list(s.values) == [63.7, 63.8]
        assert (s.pass_band.low, s.pass_band.high) == (61.6, 65.8)
        assert s.sample_id == "AO25"

    def test_a_series_of_only_non_qc_rows_does_not_exist_at_all(self):
        pm = row("2026-08-20T10:00:00", 5.0, detail={"low": 0.0, "high": 1.0})
        pm["kind"] = "pm"
        assert qs.series_from_rows([pm]) == {}


# ── the divisor ──────────────────────────────────────────────────────────────

class TestSampleStandardDeviation:
    def test_mean_and_s_of_the_hand_worked_spread(self):
        mean, s = qs.mean_and_s(SPREAD)
        assert mean == 5.0                       # 40 / 8
        assert s == pytest.approx(math.sqrt(32 / 7), abs=1e-12)
        assert s == pytest.approx(2.1380899353, abs=1e-9)

    def test_it_is_the_n_minus_1_divisor_not_n(self):
        # sqrt(32/8) is exactly 2.0. If this ever passes, the divisor is wrong.
        _, s = qs.mean_and_s(SPREAD)
        assert s != pytest.approx(2.0, abs=1e-6)

    def test_the_textbook_worked_example(self):
        mean, s = qs.mean_and_s(TEXTBOOK)
        assert mean == 18.0                      # 144 / 8
        assert s == pytest.approx(5.2372293657, abs=1e-9)   # sqrt(192/7)
        assert s != pytest.approx(math.sqrt(24.0), abs=1e-6)  # the n divisor

    def test_the_exact_fixture_has_s_of_exactly_ten(self):
        mean, s = qs.mean_and_s(EXACT)
        assert (mean, s) == (100.0, 10.0)        # SS 400 / (5-1) = 100

    def test_one_point_has_a_mean_and_no_spread(self):
        # NOT zero. A single result has no spread to report, and calling it 0.0
        # would put every later point beyond a zone of zero width.
        mean, s = qs.mean_and_s([7.5])
        assert mean == 7.5
        assert s is None

    def test_no_points_has_neither(self):
        assert qs.mean_and_s([]) == (None, None)

    def test_identical_values_have_a_spread_of_exactly_zero(self):
        mean, s = qs.mean_and_s([5.0] * 10)
        assert (mean, s) == (5.0, 0.0)


# ── zones, and what they are NOT ─────────────────────────────────────────────

class TestControlLimits:
    def test_the_three_zones_off_the_exact_fixture(self):
        # mean 100, s 10 -> 90/110, 80/120, 70/130. All hand-arithmetic.
        cl = qs.control_limits(EXACT)
        assert (cl.mean, cl.s, cl.n) == (100.0, 10.0, 5)
        assert cl.zone(1) == (90.0, 110.0)
        assert cl.zone(2) == (80.0, 120.0)
        assert cl.zone(3) == (70.0, 130.0)
        assert (cl.lower_3s, cl.upper_3s) == (70.0, 130.0)

    def test_degrees_of_freedom_is_n_minus_1(self):
        # The one number a later uncertainty module needs beside s.
        assert qs.control_limits(EXACT).df == 4

    def test_zones_use_the_sample_s_so_they_are_wider_than_the_population_ones(self):
        cl = qs.control_limits(SPREAD)        # mean 5.0, s = sqrt(32/7)
        assert cl.zone(3) == pytest.approx(
            (5.0 - 3 * math.sqrt(32 / 7), 5.0 + 3 * math.sqrt(32 / 7)))
        # The population divisor would give exactly 5 +/- 6 -> (-1.0, 11.0).
        assert cl.upper_3s != pytest.approx(11.0, abs=1e-6)

    def test_no_points_has_no_limits(self):
        assert qs.control_limits([]) is None

    def test_one_point_has_a_centre_line_and_no_zones(self):
        cl = qs.control_limits([7.5])
        assert (cl.n, cl.mean, cl.s, cl.df) == (1, 7.5, None, 0)
        assert cl.zone(3) is None
        assert cl.upper_3s is None

    def test_identical_values_collapse_every_zone_onto_the_mean(self):
        # s = 0 is a real answer (an instrument reporting one rounded figure),
        # not a division by zero and not a missing one.
        cl = qs.control_limits([5.0] * 10)
        assert (cl.n, cl.mean, cl.s) == (10, 5.0, 0.0)
        assert cl.zone(3) == (5.0, 5.0)


class TestZonesAreNotThePassBand:
    """The distinction the whole module hangs on, pinned in both directions."""

    def test_a_wide_certificate_over_a_tight_process(self):
        # The instrument sits at 100 +/- 10; the standard's control limit is
        # 50-150. Everything passes and the process limits are far tighter.
        s = qs.series_for(
            rows_at_hourly(EXACT, detail={"in_spec": True, "low": 50.0,
                                          "high": 150.0, "expected": 100.0}),
            "m1", "Flash Point")
        a = qs.analyse(s)
        assert (a.pass_band.low, a.pass_band.high) == (50.0, 150.0)
        assert (a.limits.lower_3s, a.limits.upper_3s) == (70.0, 130.0)
        assert a.failures == 0

    def test_a_series_can_be_fully_in_spec_and_out_of_control(self):
        # This is the entire reason the module exists. Nine consecutive
        # results above the mean, every one of them inside the band: the old
        # chart draws a clean green run and the process has moved.
        vals = [101.0, 102.0, 103.0, 101.0, 102.0,
                103.0, 101.0, 102.0, 103.0]
        s = qs.series_for(
            rows_at_hourly(vals, detail={"in_spec": True, "low": 50.0,
                                         "high": 150.0, "expected": 100.0}),
            "m1", "Flash Point")
        a = qs.analyse(s, limits=qs.ControlLimits(n=20, mean=100.0, s=10.0))
        assert a.failures == 0                       # nothing outside the band
        assert a.in_control is False                 # and yet
        assert [v.rule for v in a.violations] == [qs.RULE_SHIFT]
        # And the finding is FIRM, because the limits came from a
        # qualification period rather than from the points being judged.
        assert a.self_fitted is False
        assert a.violations[0].provisional is False

    def test_the_failure_count_is_the_logged_verdict_not_a_recomputed_one(self):
        # The bench judged with the correction factor that was in force at the
        # time; recomputing against today's band would restate a recorded
        # result, which 17025 7.11.3 does not allow.
        rows = [row("2026-08-20T08:00:00", 63.0,
                    detail={"in_spec": False, "low": 61.0, "high": 65.0}),
                row("2026-08-20T09:00:00", 63.5,
                    detail={"in_spec": True, "low": 61.0, "high": 65.0})]
        a = qs.analyse(qs.series_for(rows, "m1", "Flash Point"))
        assert a.failures == 1        # 63.0 IS inside 61-65, and was logged FAIL
        assert a.unjudged == 0

    def test_a_result_with_no_recorded_verdict_is_counted_apart(self):
        rows = rows_at_hourly([1.0, 2.0])            # no in_spec in the detail
        a = qs.analyse(qs.series_for(rows, "m1", "Flash Point"))
        assert (a.failures, a.unjudged) == (0, 2)


# ── out-of-control rules ─────────────────────────────────────────────────────
#
# Every rule test below judges against FIXED limits — mean 100.0, s 10.0 — so
# the zones are round numbers that can be sat on exactly:
#
#     1s   90 / 110      2s   80 / 120      3s   70 / 130
#
# Fixed rather than recomputed because adding the violating point would move
# the mean and the spread it is being judged against, which is both untestable
# and, as it happens, how a real lab runs a chart: limits come from a
# qualification period and later results are judged against them.

LIM = qs.ControlLimits(n=20, mean=100.0, s=10.0)


def pts(values, **kw):
    """Values one minute apart, in order, as points."""
    return qs.points_from_rows(
        [row(f"2026-08-20T08:{i:02d}:00", v, **kw) for i, v in enumerate(values)])


def fired(values, limits=LIM):
    return qs.violations(pts(values), limits=limits)


class TestOnePointBeyond3s:
    def test_a_point_above_the_upper_3s_limit(self):
        v = fired([100.0, 100.0, 131.0])            # 3s = 130.0
        assert [x.rule for x in v] == [qs.RULE_1_3S]
        assert v[0].indices == (2,)
        assert v[0].side == "above"

    def test_a_point_below_the_lower_3s_limit(self):
        v = fired([100.0, 100.0, 69.0])             # 3s = 70.0
        assert [(x.rule, x.indices, x.side) for x in v] == \
            [(qs.RULE_1_3S, (2,), "below")]

    def test_near_miss_a_point_sitting_exactly_on_3s_is_not_beyond_it(self):
        # The commonest way this detector goes wrong: >= instead of >, which
        # condemns an instrument that landed precisely on its limit.
        assert fired([100.0, 100.0, 130.0]) == ()
        assert fired([100.0, 100.0, 70.0]) == ()

    def test_near_miss_beyond_2s_is_not_beyond_3s(self):
        assert fired([100.0, 100.0, 129.9]) == ()


class TestTwoOfThreeBeyond2s:
    def test_two_adjacent_points_beyond_2s_on_the_same_side(self):
        v = fired([121.0, 122.0, 100.0])            # 2s = 120.0
        assert [(x.rule, x.indices, x.side) for x in v] == \
            [(qs.RULE_2OF3_2S, (0, 1), "above")]

    def test_the_two_need_not_be_adjacent_only_within_three(self):
        v = fired([121.0, 100.0, 122.0])
        assert [(x.rule, x.indices) for x in v] == [(qs.RULE_2OF3_2S, (0, 2))]

    def test_below_the_lower_2s_limit(self):
        v = fired([79.0, 78.0, 100.0])              # 2s = 80.0
        assert [(x.rule, x.side) for x in v] == [(qs.RULE_2OF3_2S, "below")]

    def test_near_miss_two_beyond_2s_on_OPPOSITE_sides_is_not_the_rule(self):
        # A detector that counts |x - mean| without regard to side fires here,
        # and this is normal random scatter, not a process fault.
        assert fired([121.0, 79.0, 100.0]) == ()

    def test_near_miss_two_beyond_2s_more_than_three_apart(self):
        assert fired([121.0, 100.0, 100.0, 122.0]) == ()

    def test_near_miss_exactly_on_2s_is_not_beyond_it(self):
        assert fired([120.0, 120.0, 100.0]) == ()

    def test_one_excursion_is_reported_once_not_once_per_window(self):
        # Four points beyond 2s span two overlapping windows. A chart that
        # raises the same alarm twice is a chart the bench stops reading.
        v = fired([121.0, 122.0, 123.0, 124.0])
        assert len(v) == 1
        assert v[0].indices == (0, 1, 2)

    def test_AABB_reports_BOTH_excursions_not_just_the_first(self):
        # Two above 2s then two below 2s: results swinging beyond 2s on both
        # sides, which is the Westgard R-4s random-error signature. Resuming
        # a whole WINDOW past a hit instead of resuming past the last
        # offending point skips runs 3 and 4 entirely and calls the second
        # half of this series clean. A missed alarm is worse than a false one.
        v = fired([121.0, 122.0, 79.0, 78.0, 100.0, 100.0])
        assert [(x.rule, x.indices, x.side) for x in v] == [
            (qs.RULE_2OF3_2S, (0, 1), "above"),
            (qs.RULE_2OF3_2S, (2, 3), "below")]

    def test_ABAB_reports_both_too(self):
        # The same excursion with one ordinary result between the halves. This
        # one has always been reported; it is here as the differential that
        # shows the AABB case above is a real defect and not the rule.
        v = fired([121.0, 122.0, 100.0, 79.0, 78.0, 100.0])
        assert [(x.rule, x.indices, x.side) for x in v] == [
            (qs.RULE_2OF3_2S, (0, 1), "above"),
            (qs.RULE_2OF3_2S, (3, 4), "below")]

    def test_the_scan_resumes_past_the_last_offending_point_not_the_window(self):
        # [121, 122, 123] fires on runs 1-3; the scan resumes at run 4, and
        # 124 on its own is not a second 2-of-3 excursion. Resuming one point
        # later must not turn one alarm into two.
        v = [x for x in fired([121.0, 122.0, 123.0, 124.0, 100.0, 100.0])
             if x.rule == qs.RULE_2OF3_2S]
        assert len(v) == 1
        assert v[0].indices == (0, 1, 2)


class TestFourOfFiveBeyond1s:
    def test_four_of_five_above_1s(self):
        v = fired([111.0, 112.0, 100.0, 113.0, 114.0])       # 1s = 110.0
        assert [(x.rule, x.indices, x.side) for x in v] == \
            [(qs.RULE_4OF5_1S, (0, 1, 3, 4), "above")]

    def test_four_of_five_below_1s(self):
        v = fired([89.0, 88.0, 100.0, 87.0, 86.0])           # 1s = 90.0
        assert [(x.rule, x.indices, x.side) for x in v] == \
            [(qs.RULE_4OF5_1S, (0, 1, 3, 4), "below")]

    def test_near_miss_only_three_of_five(self):
        assert fired([111.0, 112.0, 100.0, 100.0, 114.0]) == ()

    def test_near_miss_four_of_five_but_split_across_both_sides(self):
        assert fired([111.0, 112.0, 100.0, 89.0, 88.0]) == ()

    def test_near_miss_exactly_on_1s(self):
        assert fired([110.0, 110.0, 110.0, 110.0, 100.0]) == ()

    def test_a_second_excursion_beginning_one_point_later_is_not_skipped(self):
        # Runs 1-4 are above 1s and runs 5-8 are below it. The second
        # excursion is only visible in the window that starts at run 5; a scan
        # that jumps a whole five-point window lands at run 6, sees three
        # below instead of four, and reports the chart as half clean.
        v = fired([111.0, 112.0, 113.0, 114.0,
                   89.0, 88.0, 87.0, 86.0, 100.0, 100.0])
        assert [(x.rule, x.indices, x.side) for x in v] == [
            (qs.RULE_4OF5_1S, (0, 1, 2, 3), "above"),
            (qs.RULE_4OF5_1S, (4, 5, 6, 7), "below")]


class TestNineOnOneSideIsAShift:
    """Nine, not seven — ISO 7870-2 / Nelson rule 2. See `SHIFT_RUN`.

    A run of seven fires on 36.5% of in-control charts of sixty points and a
    detector that cries wolf on a third of clean charts is a detector the
    bench switches off. Nine is the run length the control-chart standard in
    the ISO/IEC 17025 family specifies, and Nelson rule 2 agrees at nine.
    """

    ABOVE = [101.0, 102.0, 103.0, 101.0, 102.0, 103.0, 101.0, 102.0, 103.0]
    BELOW = [99.0, 98.0, 97.0, 99.0, 98.0, 97.0, 99.0, 98.0, 97.0]

    def test_the_run_lengths_name_the_rules_they_implement(self):
        # Two constants, two different authorities, and they do not agree on a
        # number — so neither may be justified by the other's citation.
        assert qs.SHIFT_RUN == 9        # ISO 7870-2 / Nelson rule 2
        assert qs.TREND_RUN == 7        # Westgard 7T

    def test_nine_consecutive_above_the_mean(self):
        v = fired(self.ABOVE)
        assert [(x.rule, x.indices, x.side) for x in v] == \
            [(qs.RULE_SHIFT, tuple(range(9)), "above")]

    def test_nine_consecutive_below_the_mean(self):
        v = fired(self.BELOW)
        assert [(x.rule, x.side) for x in v] == [(qs.RULE_SHIFT, "below")]

    def test_the_boundary_both_ways_eight_is_silent_and_nine_fires(self):
        # The run length IS the rule. Pinned from both sides so an off-by-one
        # cannot survive in either direction.
        assert fired(self.ABOVE[:8]) == ()
        assert [x.rule for x in fired(self.ABOVE[:9])] == [qs.RULE_SHIFT]
        assert fired(self.BELOW[:8]) == ()
        assert [x.rule for x in fired(self.BELOW[:9])] == [qs.RULE_SHIFT]

    def test_near_miss_seven_in_a_row_is_no_longer_an_alarm(self):
        # Seven was this module's old run length and fired on a third of
        # clean charts.
        assert fired(self.ABOVE[:7]) == ()

    def test_near_miss_a_point_exactly_on_the_mean_breaks_the_run(self):
        # It is on neither side. Skipping it instead would splice two runs of
        # four into a shift of nine that never happened.
        vals = list(self.ABOVE)
        vals[4] = 100.0
        assert fired(vals) == ()

    def test_a_longer_run_is_one_violation_carrying_all_of_it(self):
        v = fired([101.0, 102.0, 103.0] * 4)                 # twelve above
        assert len(v) == 1
        assert v[0].indices == tuple(range(12))


class TestSevenRisingOrFallingIsATrend:
    def test_seven_consecutively_increasing(self):
        v = fired([97.0, 98.0, 99.0, 100.0, 101.0, 102.0, 103.0])
        assert [(x.rule, x.indices, x.side) for x in v] == \
            [(qs.RULE_TREND, (0, 1, 2, 3, 4, 5, 6), "up")]

    def test_seven_consecutively_decreasing(self):
        v = fired([103.0, 102.0, 101.0, 100.0, 99.0, 98.0, 97.0])
        assert [(x.rule, x.side) for x in v] == [(qs.RULE_TREND, "down")]

    def test_near_miss_six_rising_points(self):
        # SEVEN POINTS, which is six increases. Counting the steps instead of
        # the points is the off-by-one that makes this rule fire a run early.
        assert fired([98.0, 99.0, 100.0, 101.0, 102.0, 103.0]) == ()

    def test_near_miss_a_repeated_value_breaks_the_run(self):
        # Eight points, one flat step: the longest strictly rising run is six.
        assert fired([96.0, 97.0, 98.0, 99.0, 100.0, 101.0, 101.0, 102.0]) == ()

    def test_a_longer_trend_is_one_violation_carrying_all_of_it(self):
        v = fired([96.0, 97.0, 98.0, 99.0, 100.0, 101.0, 102.0, 103.0])
        assert len(v) == 1
        assert v[0].indices == tuple(range(8))


class TestRuleMachinery:
    def test_a_clean_series_trips_nothing(self):
        assert fired([100.0, 101.0, 99.0, 100.5, 98.0, 102.0, 99.5]) == ()

    def test_limits_default_to_the_ones_these_points_themselves_give(self):
        # [100, 100, 100, 131]: mean 431/4 = 107.75, SS = 3*7.75^2 + 23.25^2
        # = 180.1875 + 540.5625 = 720.75, s = sqrt(720.75/3) = sqrt(240.25)
        # = 15.5 exactly, so 3s = 46.5 and 131 is well inside its OWN limits.
        vals = [100.0, 100.0, 100.0, 131.0]
        assert qs.control_limits(vals).s == 15.5
        assert qs.violations(pts(vals)) == ()
        assert len(qs.violations(pts(vals), limits=LIM)) == 1

    def test_a_spread_of_zero_fires_no_zone_rule(self):
        # Limits handed in from a qualification period where the instrument
        # reported one rounded figure. Zones of zero width would make every
        # ordinary later reading a 3s excursion.
        flat = qs.ControlLimits(n=5, mean=100.0, s=0.0)
        assert qs.violations(pts([100.0, 105.0, 100.0, 95.0, 100.0]),
                             limits=flat) == ()

    def test_no_spread_yet_fires_no_zone_rule(self):
        one = qs.ControlLimits(n=1, mean=100.0, s=None)
        assert qs.violations(pts([100.0, 105.0, 131.0]), limits=one) == ()

    def test_an_empty_series_trips_nothing(self):
        assert qs.violations((), limits=LIM) == ()
        assert qs.violations(()) == ()

    def test_a_single_point_trips_nothing(self):
        assert qs.violations(pts([131.0]), limits=LIM) != ()   # judged, and out
        assert qs.violations(pts([131.0])) == ()               # judged on itself

    def test_identical_values_trip_nothing(self):
        assert qs.violations(pts([5.0] * 12)) == ()

    def test_violations_come_back_in_chart_order(self):
        v = fired([131.0, 100.0, 100.0, 100.0, 79.0, 78.0])
        assert [x.rule for x in v] == [qs.RULE_1_3S, qs.RULE_2OF3_2S]
        assert v[0].indices[0] < v[1].indices[0]

    def test_chart_order_is_the_FIRST_point_involved_not_when_it_was_knowable(self):
        # A shift beginning at run 1 is only knowable at its ninth point; the
        # 3s excursion at run 3 is knowable the moment run 3 arrives. So
        # detection order is 1_3s first, and chart order — the order the
        # docstring promises and a reader scanning left to right expects — is
        # the shift first. The two are different and the module says which.
        v = fired([101.0, 102.0, 131.0, 101.0, 102.0,
                   103.0, 101.0, 102.0, 103.0])
        assert [x.rule for x in v] == [qs.RULE_SHIFT, qs.RULE_1_3S]
        assert v[0].indices[0] == 0 and v[1].indices[0] == 2

    def test_every_violation_says_which_runs_and_what_to_do(self):
        # NOT vacuous: the second half of this table puts every violation at a
        # non-zero index, so a message that names the WINDOW start instead of
        # the first offending run fails here. `off_zero` proves that.
        off_zero = 0
        for values in ([100.0, 100.0, 131.0],
                       [121.0, 122.0, 100.0],
                       [111.0, 112.0, 100.0, 113.0, 114.0],
                       [101.0, 102.0, 103.0, 101.0, 102.0,
                        103.0, 101.0, 102.0, 103.0],
                       [97.0, 98.0, 99.0, 100.0, 101.0, 102.0, 103.0],
                       # 2of3 whose window starts at run 1 and whose first
                       # offending result is run 2.
                       [100.0, 121.0, 122.0],
                       # 4of5 whose window starts at run 1 and whose first
                       # offending result is run 2.
                       [100.0, 111.0, 112.0, 113.0, 114.0],
                       # a trend that starts at run 3
                       [100.0, 100.0, 90.0, 91.0, 92.0, 93.0,
                        94.0, 95.0, 96.0],
                       # a 3s excursion at run 4
                       [100.0, 100.0, 100.0, 131.0]):
            got = fired(values)
            assert got, values          # every row here must fire something
            for v in got:
                assert v.message.strip()
                assert v.message.rstrip().endswith(".")
                # 1-based, because "run 0" means nothing at a bench.
                assert f"run {v.indices[0] + 1}" in v.message.lower(), v.message
                if v.indices[0] != 0:
                    off_zero += 1
        assert off_zero >= 4

    def test_the_message_names_the_first_OFFENDING_run_not_the_window_start(self):
        # [100, 121, 122]: the window starts at run 1 and the two offending
        # results are runs 2 and 3. A message reading "starting at run 1"
        # sends a tech to the wrong result and contradicts `indices`.
        v = fired([100.0, 121.0, 122.0])
        assert [(x.rule, x.indices) for x in v] == [(qs.RULE_2OF3_2S, (1, 2))]
        assert "run 2" in v[0].message.lower()
        assert "run 1" not in v[0].message.lower()

    def test_all_three_beyond_is_not_understated_as_two_of_three(self):
        # Reporting "2 of the 3" when every one of the three is beyond 2s
        # tells the bench the excursion is smaller than it is.
        v = fired([121.0, 122.0, 123.0])
        assert v[0].indices == (0, 1, 2)
        assert "2 of" not in v[0].message.lower()
        assert "all 3" in v[0].message.lower()

    def test_four_of_five_still_says_four_of_five(self):
        v = fired([111.0, 112.0, 100.0, 113.0, 114.0])
        assert v[0].indices == (0, 1, 3, 4)
        assert "4 of" in v[0].message.lower()

    def test_the_3s_message_names_the_reading_and_the_limit(self):
        v = fired([100.0, 100.0, 131.0])[0]
        assert "131" in v.message
        assert "3s" in v.message


# ── what the spread is allowed to be CALLED ──────────────────────────────────
#
# The metrology, because it is what these tests are really about: results from
# one analyst in one sitting measure REPEATABILITY (s_r) — the best the method
# can ever look. Results spread across analysts and days measure
# within-laboratory reproducibility (u(Rw)), which is what a lab's uncertainty
# is actually built from and is always the larger number.
#
# Calling the first the second overstates the lab's control. So does inferring
# it: the `operator` field is being added to the log detail by other work, so
# a series read today is part attributed and part not, and a missing name must
# never be read as "somebody else".

def op_rows(entries):
    """(day, hour, value, operator-or-None[, calibration-or-None]) -> rows.

    The calibration epoch is optional in the fixture for the same reason it is
    optional in the log: rows written before `qc_log_detail` carried it have
    no epoch at all, and those rows must degrade to unknown rather than to a
    claim. A fixture that always supplies one could not express that case.
    """
    out = []
    for entry in entries:
        day, hour, value, who = entry[:4]
        cal = entry[4] if len(entry) > 4 else None
        detail = {"in_spec": True}
        if who is not None:
            detail["operator"] = who
        if cal is not None:
            detail["calibration_id"] = cal
        out.append(row(f"2026-08-{day:02d}T{hour:02d}:00:00", value,
                       detail=detail))
    return out


class TestCoverage:
    def test_counts_distinct_named_operators_and_distinct_days(self):
        cov = qs.coverage(qs.points_from_rows(op_rows([
            (20, 8, 1.0, "R Chen"), (20, 9, 2.0, "R Chen"),
            (21, 8, 3.0, "J Patel")])))
        assert cov.n == 3
        assert cov.n_operators == 2
        assert cov.operators == ("R Chen", "J Patel")
        assert cov.n_days == 2
        assert cov.n_unknown_operator == 0

    def test_the_same_analyst_typed_two_ways_is_one_analyst(self):
        # Counting "ryan" and "Ryan" as two overstates the coverage, which is
        # the one direction this module is not allowed to be wrong in.
        cov = qs.coverage(qs.points_from_rows(op_rows([
            (20, 8, 1.0, "Ryan"), (20, 9, 2.0, "ryan"),
            (20, 10, 3.0, " RYAN ")])))
        assert cov.n_operators == 1

    def test_a_missing_operator_is_unknown_and_is_NOT_another_operator(self):
        cov = qs.coverage(qs.points_from_rows(op_rows([
            (20, 8, 1.0, "R Chen"), (21, 9, 2.0, None), (22, 9, 3.0, None)])))
        assert cov.n_operators == 1            # not 2, and not 3
        assert cov.n_unknown_operator == 2
        assert cov.operator_varied is False

    def test_an_empty_operator_string_is_missing_not_a_name(self):
        cov = qs.coverage(qs.points_from_rows(op_rows([
            (20, 8, 1.0, "   "), (20, 9, 2.0, "R Chen")])))
        assert cov.n_operators == 1
        assert cov.n_unknown_operator == 1

    def test_the_audit_spelling_of_the_field_is_read_too(self):
        # Every audit row in this tree names the person `by`. Reading both
        # keys costs nothing and the failure direction is safe either way: a
        # key nobody writes leaves the series UNKNOWN, which understates.
        cov = qs.coverage(qs.points_from_rows(
            [row("2026-08-20T08:00:00", 1.0, detail={"by": "R Chen"})]))
        assert cov.operators == ("R Chen",)

    def test_a_point_with_an_unreadable_timestamp_is_not_a_day(self):
        pts_ = qs.points_from_rows([row("not a date", 1.0),
                                    row("2026-08-20T08:00:00", 2.0)])
        cov = qs.coverage(pts_)
        assert cov.n_days == 1
        assert cov.n_undated == 1


class TestWhatTheSpreadSupports:
    def one_analyst_one_day(self):
        return qs.coverage(qs.points_from_rows(op_rows([
            (20, 8, 1.0, "R Chen", "CAL-1"), (20, 9, 2.0, "R Chen", "CAL-1"),
            (20, 10, 3.0, "R Chen", "CAL-1")])))

    def two_analysts_two_days(self):
        """Fully attributed AND spanning two calibration epochs.

        Two analysts over two days on ONE calibration is a different case and
        is not u(Rw) — see `test_two_analysts_over_two_days_on_ONE_...`.
        """
        return qs.coverage(qs.points_from_rows(op_rows([
            (20, 8, 1.0, "R Chen", "CAL-1"), (20, 9, 2.0, "R Chen", "CAL-1"),
            (21, 8, 3.0, "J Patel", "CAL-2"),
            (21, 9, 4.0, "J Patel", "CAL-2")])))

    def test_one_analyst_one_day_is_repeatability_and_nothing_more(self):
        cov = self.one_analyst_one_day()
        assert cov.basis == qs.BASIS_REPEATABILITY
        assert cov.supports_repeatability() is True
        assert cov.supports_reproducibility() is False

    def test_several_analysts_over_several_days_is_within_lab_reproducibility(self):
        cov = self.two_analysts_two_days()
        assert cov.basis == qs.BASIS_INTERMEDIATE
        assert cov.supports_reproducibility() is True
        assert cov.supports_repeatability() is False

    def test_an_unrecorded_analyst_supports_NEITHER_claim(self):
        # The load-bearing case, and the one this module exists to get right:
        # five results over five days with nobody's name on them look exactly
        # like good coverage and are not evidence of any.
        cov = qs.coverage(qs.points_from_rows(op_rows(
            [(20 + i, 8, float(i), None) for i in range(5)])))
        assert cov.n_days == 5
        assert cov.basis == qs.BASIS_UNKNOWN
        assert cov.supports_reproducibility() is False
        assert cov.supports_repeatability() is False

    def test_a_partly_attributed_series_supports_neither_either(self):
        # Mid-rollout: some rows carry the new field and some never will.
        cov = qs.coverage(qs.points_from_rows(op_rows([
            (20, 8, 1.0, "R Chen"), (21, 8, 2.0, "J Patel"),
            (22, 8, 3.0, None)])))
        assert cov.n_operators == 2          # two really are named
        assert cov.basis == qs.BASIS_UNKNOWN  # and the third is not
        assert cov.supports_reproducibility() is False

    def test_one_analyst_over_many_days_is_neither_of_the_two_names(self):
        cov = qs.coverage(qs.points_from_rows(op_rows([
            (20, 8, 1.0, "R Chen", "CAL-1"), (21, 8, 2.0, "R Chen", "CAL-1"),
            (22, 8, 3.0, "R Chen", "CAL-1")])))
        assert cov.basis == qs.BASIS_PARTIAL
        assert cov.supports_repeatability() is False   # conditions did vary
        assert cov.supports_reproducibility() is False  # the analyst did not

    def test_two_analysts_in_one_sitting_is_also_partial(self):
        cov = qs.coverage(qs.points_from_rows(op_rows([
            (20, 8, 1.0, "R Chen", "CAL-1"),
            (20, 9, 2.0, "J Patel", "CAL-1")])))
        assert cov.basis == qs.BASIS_PARTIAL

    def test_undated_results_support_no_claim_even_when_fully_attributed(self):
        cov = qs.coverage(qs.points_from_rows(
            [row("nonsense", 1.0, detail={"operator": "R Chen"}),
             row("also nonsense", 2.0, detail={"operator": "J Patel"})]))
        assert cov.basis == qs.BASIS_UNKNOWN

    def test_fewer_than_two_results_is_no_spread_at_all(self):
        assert qs.coverage(()).basis == qs.BASIS_INSUFFICIENT
        one = qs.points_from_rows(op_rows([(20, 8, 1.0, "R Chen")]))
        assert qs.coverage(one).basis == qs.BASIS_INSUFFICIENT
        assert qs.coverage(one).supports_repeatability() is False

    def test_every_basis_carries_a_sentence_that_says_why(self):
        for cov in (qs.coverage(()), self.one_analyst_one_day(),
                    self.two_analysts_two_days()):
            assert cov.caveat().strip().endswith(".")

    def test_the_unknown_caveat_names_the_missing_field_not_the_conclusion(self):
        cov = qs.coverage(qs.points_from_rows(op_rows(
            [(20 + i, 8, float(i), None) for i in range(3)])))
        assert "who" in cov.caveat().lower()

    def test_the_analysis_carries_the_coverage(self):
        s = qs.series_for(op_rows([(20, 8, 1.0, "R Chen", "CAL-1"),
                                   (20, 9, 2.0, "R Chen", "CAL-1")]),
                          "m1", "Flash Point")
        assert qs.analyse(s).coverage.basis == qs.BASIS_REPEATABILITY


# ── the calibration epoch, which is the third factor ─────────────────────────
#
# The bench writes `calibration_id` on EVERY verdict (`qc_log_detail` in the
# station module) and its own docstring is explicit: a spread is u(Rw) only if
# the set spans "analysts, shifts and calibrations". A set from one analyst
# against one calibration is repeatability, a far narrower claim. Two analysts
# over two days on ONE calibration is somewhere in between and is not u(Rw).
#
# Absence is UNKNOWN and can never count as "spans calibrations" — exactly the
# rule already applied to `operator`. Rows written before the field existed
# carry neither, and must degrade to unknown rather than to a claim.

class TestCalibrationEpoch:
    def test_the_epoch_is_read_off_the_detail(self):
        cov = qs.coverage(qs.points_from_rows(op_rows([
            (20, 8, 1.0, "R Chen", "CAL-1"),
            (21, 8, 2.0, "J Patel", "CAL-2")])))
        assert cov.calibrations == ("CAL-1", "CAL-2")
        assert cov.n_calibrations == 2
        assert cov.n_unknown_calibration == 0
        assert cov.calibration_varied is True

    def test_one_epoch_across_the_whole_series_has_not_varied(self):
        cov = qs.coverage(qs.points_from_rows(op_rows([
            (20, 8, 1.0, "R Chen", "CAL-1"),
            (21, 8, 2.0, "J Patel", "CAL-1")])))
        assert cov.n_calibrations == 1
        assert cov.calibration_varied is False
        assert cov.calibration_known is True

    def test_a_missing_epoch_is_unknown_and_is_NOT_another_epoch(self):
        # The same failure direction the operator field already guards: an
        # absent id counted as a distinct epoch manufactures the very coverage
        # this module refuses to infer.
        cov = qs.coverage(qs.points_from_rows(op_rows([
            (20, 8, 1.0, "R Chen", "CAL-1"), (21, 8, 2.0, "J Patel", None)])))
        assert cov.n_calibrations == 1          # not 2
        assert cov.n_unknown_calibration == 1
        assert cov.calibration_varied is False
        assert cov.calibration_known is False

    def test_a_null_or_blank_epoch_is_absence_not_a_name(self):
        # `qc_log_detail` writes the key with a null when it looked and did not
        # know. Blank is neither, and would be tallied as one epoch.
        rows = [row("2026-08-20T08:00:00", 1.0,
                    detail={"operator": "R Chen", "calibration_id": None}),
                row("2026-08-21T08:00:00", 2.0,
                    detail={"operator": "J Patel", "calibration_id": "   "})]
        cov = qs.coverage(qs.points_from_rows(rows))
        assert cov.n_calibrations == 0
        assert cov.n_unknown_calibration == 2

    def test_the_same_epoch_typed_two_ways_is_one_epoch(self):
        cov = qs.coverage(qs.points_from_rows(op_rows([
            (20, 8, 1.0, "R Chen", "cal-1"),
            (21, 8, 2.0, "J Patel", " CAL-1 ")])))
        assert cov.n_calibrations == 1

    def test_the_point_carries_the_epoch(self):
        pt = qs.points_from_rows(op_rows([(20, 8, 1.0, "R Chen", "CAL-1")]))[0]
        assert pt.calibration_id == "CAL-1"
        assert pt.operator == "R Chen"


class TestOneCalibrationIsNotReproducibility:
    """BLOCKER: u(Rw) claimed on a spread that may span one calibration."""

    def test_two_analysts_over_two_days_on_ONE_calibration_is_not_uRw(self):
        # This is the case the module used to call INTERMEDIATE. Every result
        # was measured against a single calibration, so the spread carries no
        # between-calibration component at all and naming it u(Rw) overstates
        # the lab's control — which is the one direction this must not be
        # wrong in.
        cov = qs.coverage(qs.points_from_rows(op_rows([
            (20, 8, 1.0, "R Chen", "CAL-1"), (20, 9, 2.0, "R Chen", "CAL-1"),
            (21, 8, 3.0, "J Patel", "CAL-1"),
            (21, 9, 4.0, "J Patel", "CAL-1")])))
        assert cov.operator_varied is True
        assert cov.day_varied is True
        assert cov.calibration_varied is False
        assert cov.basis == qs.BASIS_PARTIAL
        assert cov.supports_reproducibility() is False
        assert cov.supports_repeatability() is False

    def test_all_three_factors_varied_is_within_lab_reproducibility(self):
        cov = qs.coverage(qs.points_from_rows(op_rows([
            (20, 8, 1.0, "R Chen", "CAL-1"), (20, 9, 2.0, "R Chen", "CAL-1"),
            (21, 8, 3.0, "J Patel", "CAL-2"),
            (21, 9, 4.0, "J Patel", "CAL-2")])))
        assert cov.basis == qs.BASIS_INTERMEDIATE
        assert cov.supports_reproducibility() is True

    def test_an_unrecorded_epoch_supports_NEITHER_claim(self):
        # Fully attributed to two analysts over two days, but nobody wrote the
        # calibration down. Unknown, not "intermediate as far as we can tell".
        cov = qs.coverage(qs.points_from_rows(op_rows([
            (20, 8, 1.0, "R Chen", "CAL-1"), (20, 9, 2.0, "R Chen", "CAL-1"),
            (21, 8, 3.0, "J Patel", "CAL-2"), (21, 9, 4.0, "J Patel", None)])))
        assert cov.n_calibrations == 2       # two really are named
        assert cov.basis == qs.BASIS_UNKNOWN
        assert cov.supports_reproducibility() is False

    def test_an_older_row_carrying_neither_field_degrades_to_unknown(self):
        # Rows written before `qc_log_detail` carried provenance have no
        # operator and no calibration. Five results over five days look like
        # good coverage and are evidence of none.
        cov = qs.coverage(qs.points_from_rows(op_rows(
            [(20 + i, 8, float(i), None) for i in range(5)])))
        assert cov.n_days == 5
        assert cov.n_unknown_operator == 5
        assert cov.n_unknown_calibration == 5
        assert cov.basis == qs.BASIS_UNKNOWN
        assert cov.supports_reproducibility() is False
        assert cov.supports_repeatability() is False

    def test_one_analyst_one_day_one_calibration_is_repeatability(self):
        cov = qs.coverage(qs.points_from_rows(op_rows([
            (20, 8, 1.0, "R Chen", "CAL-1"), (20, 9, 2.0, "R Chen", "CAL-1")])))
        assert cov.basis == qs.BASIS_REPEATABILITY
        assert cov.supports_repeatability() is True

    def test_the_uRw_caveat_names_the_calibrations_it_rests_on(self):
        cov = qs.coverage(qs.points_from_rows(op_rows([
            (20, 8, 1.0, "R Chen", "CAL-1"), (20, 9, 2.0, "R Chen", "CAL-1"),
            (21, 8, 3.0, "J Patel", "CAL-2"),
            (21, 9, 4.0, "J Patel", "CAL-2")])))
        text = cov.caveat().lower()
        assert "calibration" in text
        assert "u(rw)" in text

    def test_the_unknown_caveat_names_the_missing_calibration(self):
        cov = qs.coverage(qs.points_from_rows(op_rows([
            (20, 8, 1.0, "R Chen", "CAL-1"), (21, 8, 2.0, "J Patel", None)])))
        assert "calibration" in cov.caveat().lower()


class TestTheCaveatCountsCalendarDaysAndSaysSo:
    """`coverage()` counts calendar dates. The sentence must not say shifts."""

    def test_two_shifts_on_one_date_are_one_calendar_day(self):
        # 08:00 and 23:00 are two shifts and one date. The module counts
        # dates, which UNDERSTATES the conditions covered — the safe
        # direction — and the printed sentence must not claim otherwise.
        cov = qs.coverage(qs.points_from_rows(op_rows([
            (20, 8, 1.0, "R Chen", "CAL-1"),
            (20, 23, 2.0, "R Chen", "CAL-1")])))
        assert cov.n_days == 1
        assert cov.day_varied is False

    def test_no_caveat_claims_to_have_counted_shifts(self):
        for cov in (qs.coverage(()),
                    qs.coverage(qs.points_from_rows(op_rows([
                        (20, 8, 1.0, "R Chen", "CAL-1"),
                        (20, 9, 2.0, "R Chen", "CAL-1")]))),
                    qs.coverage(qs.points_from_rows(op_rows([
                        (20, 8, 1.0, "R Chen", "CAL-1"),
                        (21, 8, 2.0, "J Patel", "CAL-2")]))),
                    qs.coverage(qs.points_from_rows(op_rows([
                        (20, 8, 1.0, None), (21, 8, 2.0, None)]))),
                    qs.coverage(qs.points_from_rows(op_rows([
                        (20, 8, 1.0, "R Chen", "CAL-1"),
                        (21, 8, 2.0, "R Chen", "CAL-1")])))):
            assert "shift" not in cov.caveat().lower()

    def test_a_caveat_that_counts_days_calls_them_calendar_days(self):
        cov = qs.coverage(qs.points_from_rows(op_rows([
            (20, 8, 1.0, "R Chen", "CAL-1"),
            (20, 23, 2.0, "R Chen", "CAL-1")])))
        assert "calendar day" in cov.caveat().lower()


# ── the time window ──────────────────────────────────────────────────────────

from datetime import datetime as _dt      # noqa: E402  (read after the fixtures)


def dated(entries):
    """(day, hour, value) rows in August 2026."""
    return [row(f"2026-08-{d:02d}T{h:02d}:00:00", v) for d, h, v in entries]


DAY1 = [(19, 8, 1.0), (19, 9, 3.0)]
DAY2 = [(20, 8, 90.0), (20, 9, 90.0), (20, 10, 100.0),
        (20, 11, 110.0), (20, 12, 110.0)]           # = EXACT: mean 100, s 10


class TestWindow:
    def series(self):
        return qs.series_for(dated(DAY1 + DAY2), "m1", "Flash Point")

    def test_a_start_keeps_only_what_came_after_it(self):
        got = qs.window(self.series(), start=_dt(2026, 8, 20))
        assert list(got.values) == list(EXACT)

    def test_an_end_keeps_only_what_came_before_it(self):
        got = qs.window(self.series(), end=_dt(2026, 8, 20))
        assert list(got.values) == [1.0, 3.0]

    def test_the_window_is_half_open_start_in_end_out(self):
        # So consecutive windows tile the history without counting a result
        # into two of them.
        got = qs.window(self.series(), start=_dt(2026, 8, 20, 9),
                        end=_dt(2026, 8, 20, 11))
        assert list(got.values) == [90.0, 100.0]        # 09:00 in, 11:00 out

    def test_no_bounds_at_all_is_the_whole_series(self):
        assert len(qs.window(self.series()).points) == 7

    def test_the_window_keeps_the_series_identity_and_its_band(self):
        rows = dated(DAY1 + DAY2)
        rows[-1]["detail"] = '{"low": 61.0, "high": 65.0, "expected": 63.0}'
        s = qs.series_for(rows, "m1", "Flash Point")
        got = qs.window(s, start=_dt(2026, 8, 20))
        assert (got.machine_uid, got.test_name) == ("m1", "Flash Point")
        assert got.pass_band.low == 61.0        # the band is the standard's,
        assert got.sample_id == "AO25"          # not a property of the window

    def test_an_undated_result_cannot_be_placed_in_a_bounded_window(self):
        rows = dated(DAY2) + [row("not a date", 999.0)]
        s = qs.series_for(rows, "m1", "Flash Point")
        assert 999.0 in s.values                       # kept in the whole series
        assert 999.0 not in qs.window(s, start=_dt(2026, 8, 1)).values

    def test_the_statistics_are_of_the_WINDOW_not_of_the_history(self):
        # Whole series: 7 points including 1.0 and 3.0, so a mean nowhere near
        # 100. The window is EXACT, whose mean is 100 and s exactly 10.
        a = qs.analyse(qs.window(self.series(), start=_dt(2026, 8, 20)))
        assert (a.n, a.mean, a.s) == (5, 100.0, 10.0)
        assert a.limits.zone(3) == (70.0, 130.0)
        assert qs.analyse(self.series()).mean != 100.0

    def test_a_window_that_catches_nothing_is_an_empty_series(self):
        got = qs.window(self.series(), start=_dt(2027, 1, 1))
        assert got.points == ()
        a = qs.analyse(got)
        assert (a.n, a.mean, a.s, a.limits) == (0, None, None, None)
        assert a.violations == ()
        assert a.in_control is True


# ── the three shapes that must not crash ─────────────────────────────────────

class TestDegenerateSeries:
    def test_an_empty_series_end_to_end(self):
        a = qs.analyse(qs.series_for([], "m1", "Flash Point"))
        assert (a.n, a.mean, a.s, a.limits, a.pass_band) == \
            (0, None, None, None, None)
        assert a.violations == () and a.in_control is True
        assert a.failures == 0 and a.unjudged == 0
        assert a.coverage.basis == qs.BASIS_INSUFFICIENT

    def test_a_single_point_series_end_to_end(self):
        a = qs.analyse(qs.series_for(
            dated([(20, 8, 63.7)]), "m1", "Flash Point"))
        assert (a.n, a.mean, a.s) == (1, 63.7, None)
        assert a.limits.df == 0
        assert a.limits.zone(3) is None
        assert a.violations == ()

    def test_a_series_with_no_spread_at_all_end_to_end(self):
        a = qs.analyse(qs.series_for(
            dated([(20, 8 + i, 63.7) for i in range(9)]), "m1", "Flash Point"))
        assert (a.n, a.mean, a.s) == (9, 63.7, 0.0)
        assert a.limits.zone(3) == (63.7, 63.7)
        # Nine identical results are neither a shift nor a trend nor an
        # excursion, and must not be reported as all three.
        assert a.violations == ()
        assert a.in_control is True


# ── limits fitted from the very points they judge ────────────────────────────
#
# Nothing in this tree supplies fixed limits, so `analyse(series)` is the only
# call a consumer writes — and its limits come from the points being judged.
# That is not a control chart; it is a chart that grades itself. Measured on
# in-control data with correct fixed limits as the control, the default path
# flagged 21.1% of clean 30-point charts and 50.3% of clean 60-point ones.
#
# Worse than the rate is the DIRECTION. On [100 x 9, 10] the self-fitted mean
# lands at 91, so the nine good results are all "above the mean" and fire the
# shift rule with remedial advice about reagents, while the one real excursion
# is silent because it inflated s past its own 3s limit. The diagnosis is
# exactly backwards and it carries instructions.

class TestSelfFittedLimitsAreMarkedAndDoNotDiagnose:
    OUTLIER = [100.0] * 9 + [10.0]

    def series(self, values):
        return qs.series_for(rows_at_hourly(values), "m1", "Flash Point")

    def test_the_analysis_says_whether_its_limits_were_fitted_to_it(self):
        # The whole point: a consumer must be able to tell a self-fitted
        # analysis from a qualified one without inspecting the call site.
        assert qs.analyse(self.series(EXACT)).self_fitted is True
        assert qs.analyse(self.series(EXACT), limits=LIM).self_fitted is False

    def test_a_single_outlier_does_not_produce_a_reagent_diagnosis(self):
        # The commonest real event on a bench, and the case that used to send
        # a tech to change a reagent because of the nine results that were
        # fine. Nothing here may carry that instruction.
        a = qs.analyse(self.series(self.OUTLIER))
        assert a.self_fitted is True
        assert qs.RULE_SHIFT not in [v.rule for v in a.violations]
        for v in a.violations:
            assert "reagent" not in v.message.lower()

    def test_the_shift_rule_is_suppressed_when_the_mean_is_fitted_to_it(self):
        # Judged against the limits it deserves, [100 x 9, 10] has one real
        # finding and it is not a shift. Judged against its own mean of 91,
        # the nine good results ARE nine in a row above it — an artefact of
        # the outlier, reported as a process change.
        assert qs.violations(pts(self.OUTLIER)) == ()
        firm = qs.violations(pts(self.OUTLIER), limits=LIM)
        assert [v.rule for v in firm] == [qs.RULE_1_3S]
        assert firm[0].indices == (9,)

    def test_the_shift_rule_still_fires_against_supplied_limits(self):
        # Suppression is a property of self-fitting, not of the rule.
        v = qs.violations(pts([101.0, 102.0, 103.0] * 3), limits=LIM)
        assert [x.rule for x in v] == [qs.RULE_SHIFT]
        assert v[0].provisional is False

    def test_zone_findings_under_self_fitted_limits_are_marked_provisional(self):
        # mean 91.667, s 19.46 -> 2s at 52.74 / 130.6, so the two 50s are the
        # only results beyond a zone. The finding may well be real; what it
        # cannot be is confident, because the limits moved to accommodate it.
        vals = [100.0] * 10 + [50.0, 50.0]
        v = qs.violations(pts(vals))
        assert [(x.rule, x.indices) for x in v] == [(qs.RULE_2OF3_2S, (10, 11))]
        assert v[0].provisional is True
        # The FLAG is the fact; the paragraph that used to be appended to the
        # message was removed on Ryan's instruction (1 Sep 2026) because it
        # repeated on every finding of every chart. Anything reading this
        # programmatically — the payload, the panel's chip — is unaffected,
        # which is exactly why the flag is what this asserts.
        assert "provisional" not in v[0].message.lower(), (
            "the prose came back; the flag is what carries this now")

    def test_the_same_finding_against_supplied_limits_is_firm(self):
        v = qs.violations(pts([100.0] * 10 + [50.0, 50.0]), limits=LIM)
        assert all(x.provisional is False for x in v)
        assert all("provisional" not in x.message.lower() for x in v)

    def test_the_trend_rule_is_firm_even_when_self_fitted(self):
        # A trend reads no zone and no mean — seven results each higher than
        # the last is a trend whatever the limits are — so it is the one rule
        # self-fitting does not weaken.
        v = qs.violations(pts([96.0, 97.0, 98.0, 99.0,
                               100.0, 101.0, 102.0, 103.0]))
        assert [x.rule for x in v] == [qs.RULE_TREND]
        assert v[0].provisional is False

    def test_a_clean_thirty_point_chart_is_not_condemned_by_its_own_limits(self):
        # A sawtooth around 100: no drift, no step, no excursion. The old
        # default path called a third of charts like this out of control.
        vals = [100.0 + (1.0 if i % 2 else -1.0) for i in range(30)]
        a = qs.analyse(self.series(vals))
        assert a.violations == ()
        assert a.in_control is True

    def test_firm_violations_are_the_ones_a_bench_may_act_on(self):
        a = qs.analyse(self.series([100.0] * 10 + [50.0, 50.0]))
        assert len(a.violations) == 1
        assert a.firm_violations == ()
        b = qs.analyse(self.series([100.0] * 10 + [50.0, 50.0]), limits=LIM)
        assert len(b.firm_violations) == len(b.violations) > 0


# ── which set `s` came from ──────────────────────────────────────────────────

class TestTheSpreadAndItsDegreesOfFreedom:
    def test_n_counts_this_window_and_s_df_counts_the_set_s_CAME_from(self):
        # Qualification limits of n=20 judging a three-point window. `a.n` is
        # 3 and `a.limits.df` is 19, and a later uncertainty module handed the
        # wrong one of those reports 19 degrees of freedom for 3 results.
        a = qs.analyse(qs.series_for(rows_at_hourly([100.0, 101.0, 99.0]),
                                     "m1", "Flash Point"), limits=LIM)
        assert a.n == 3                 # results in this window
        assert a.s_n == 20              # results `s` was computed from
        assert a.s_df == 19             # and its degrees of freedom
        assert a.s == 10.0              # which is the qualification spread
        assert a.self_fitted is False

    def test_when_self_fitted_the_two_are_the_same_set(self):
        a = qs.analyse(qs.series_for(rows_at_hourly(EXACT), "m1", "Flash Point"))
        assert (a.n, a.s_n, a.s_df) == (5, 5, 4)
        assert a.self_fitted is True

    def test_the_basis_labels_s_only_when_s_is_of_these_points(self):
        # `coverage` describes the points in THIS window. When `s` came from a
        # qualification period, this window's analysts say nothing about that
        # period's, so the basis of `s` is unknown here — it has to come from
        # the coverage of the qualification set itself.
        rows = op_rows([(20, 8, 1.0, "R Chen", "CAL-1"),
                        (20, 9, 2.0, "R Chen", "CAL-1")])
        s = qs.series_for(rows, "m1", "Flash Point")
        assert qs.analyse(s).spread_basis == qs.BASIS_REPEATABILITY
        assert qs.analyse(s).coverage.basis == qs.BASIS_REPEATABILITY
        assert qs.analyse(s, limits=LIM).spread_basis == qs.BASIS_UNKNOWN
        # and the window's own coverage is still reported, unchanged
        assert qs.analyse(s, limits=LIM).coverage.basis == \
            qs.BASIS_REPEATABILITY

    def test_an_empty_series_has_no_spread_and_no_degrees_of_freedom(self):
        a = qs.analyse(qs.series_for([], "m1", "Flash Point"))
        assert (a.s, a.s_n, a.s_df) == (None, 0, 0)


# ── the band and the zones, compared ─────────────────────────────────────────

class TestZonesWithinBand:
    """`PassBand.contains` earning its place: the module's opening thesis.

    A wide certificate over a drifting instrument passes everything while the
    process is in control of nothing; a tight certificate over a stable one
    fails results the process never lost control of. Asking whether the
    OBSERVED 3s spread fits inside the SPECIFIED band says which chart this is
    — and it restates no recorded verdict, which is the one thing this module
    may not do.
    """

    def series(self, low, high):
        return qs.series_for(
            rows_at_hourly(EXACT, detail={"in_spec": True, "low": low,
                                          "high": high, "expected": 100.0}),
            "m1", "Flash Point")

    def test_a_wide_certificate_over_a_tight_process_fits(self):
        # 3s is 70-130 and the band is 50-150.
        assert qs.analyse(self.series(50.0, 150.0)).zones_within_band is True

    def test_a_tight_certificate_over_this_process_does_not(self):
        # 3s is 70-130 and the band is 95-105: ordinary scatter will fail the
        # band long before any control rule fires.
        assert qs.analyse(self.series(95.0, 105.0)).zones_within_band is False

    def test_a_band_touched_exactly_by_the_3s_edges_still_fits(self):
        # `contains` is inclusive — a value ON the band's edge is inside it,
        # the same way a result ON 3s is not beyond it.
        assert qs.analyse(self.series(70.0, 130.0)).zones_within_band is True

    def test_one_edge_outside_is_enough_to_answer_no(self):
        assert qs.analyse(self.series(70.0, 129.0)).zones_within_band is False
        assert qs.analyse(self.series(71.0, 130.0)).zones_within_band is False

    def test_it_is_unknown_rather_than_false_when_either_side_is_missing(self):
        # No band recorded, and a single point with no spread: neither is a
        # "no", and answering False would report a healthy chart as a
        # mismatch nobody can check.
        no_band = qs.series_for(rows_at_hourly(EXACT), "m1", "Flash Point")
        assert qs.analyse(no_band).zones_within_band is None
        one = qs.series_for(
            rows_at_hourly([63.7], detail={"low": 61.6, "high": 65.8}),
            "m1", "Flash Point")
        assert qs.analyse(one).zones_within_band is None
        assert qs.analyse(qs.series_for([], "m1", "x")).zones_within_band is None


class TestTheDefaultPathCannotFireFirmlyExceptOnATrend:
    """The invariant behind the false-alarm numbers, pinned as a property.

    Measured over 20,000 simulated in-control charts of sixty points: the
    default path reports something about 30.5% of CLEAN charts (was 50.3%),
    and every one of those findings is provisional. Only 1.9% carry a FIRM
    finding, and every firm finding is a trend — the one rule that reads
    neither the mean nor a zone and so is not weakened by self-fitting.

    A Monte Carlo in the suite would be slow and flaky, so what is pinned here
    is the property that produces the number: under self-fitted limits nothing
    but a trend may come back firm, and the shift rule may not come back at
    all. A deterministic sample of random charts exercises it.
    """

    def charts(self, n_points, trials=300, seed=20260826):
        import random
        rng = random.Random(seed)
        for _ in range(trials):
            yield [rng.gauss(100.0, 10.0) for _ in range(n_points)]

    def test_no_firm_finding_on_a_self_fitted_chart_is_anything_but_a_trend(self):
        seen = set()
        for values in self.charts(60):
            for v in qs.violations(pts(values)):
                seen.add((v.rule, v.provisional))
        firm = {rule for rule, provisional in seen if not provisional}
        assert firm <= {qs.RULE_TREND}
        # and the sample really did exercise the zone rules, or this proves
        # nothing at all
        assert {rule for rule, provisional in seen if provisional} == {
            qs.RULE_1_3S, qs.RULE_2OF3_2S, qs.RULE_4OF5_1S}

    def test_the_shift_rule_never_appears_under_self_fitted_limits(self):
        for values in self.charts(60):
            assert qs.RULE_SHIFT not in {v.rule for v in qs.violations(pts(values))}

    def test_the_same_charts_against_fixed_limits_do_fire_the_shift_rule(self):
        # The differential: suppression is a property of self-fitting, not of
        # these charts. Against correct fixed limits the shift rule is alive
        # and firing on the very same data.
        fixed = qs.ControlLimits(n=200, mean=100.0, s=10.0)
        fired_shift = any(
            qs.RULE_SHIFT in {v.rule for v in qs.violations(pts(values),
                                                            limits=fixed)}
            for values in self.charts(60))
        assert fired_shift is True


# ── a changeover starts a new chart ──────────────────────────────────────────
#
# Found live on 3 Sep 2026. The sulfur standard moved AO25 -> AF26 on 2 Sep.
# `series_from_rows` keyed on (machine_uid, test_name) alone, so 36 AO25
# readings around 4.99 were pooled with the single AF26 reading of 2.875 and
# fitted as ONE process. The band correctly followed the newest row to AF26's
# 2.08..3.44; the points did not. The consequences on the floor:
#
#   observed mean 4.934, s 0.463, 3s zones 3.545..6.323
#   -> the good 2.875 was flagged 1_3s BELOW the lower control limit, with
#      "Hold every result since the last good check and investigate before
#      this instrument reports again" printed in red under the chart.
#
# A control chart is a statement about ONE material measured repeatedly. Two
# materials on one chart is not a wide chart, it is a meaningless one, and the
# lab is judged against it. The standard's Lab ID is therefore part of the
# series' identity, not a label hung on it afterwards.
#
# Ryan, 3 Sep: "new QC sample means new QC chart."

class TestAChangeoverStartsANewChart:
    def test_a_new_standard_gets_its_own_series(self):
        rows = (rows_at_hourly([5.0, 5.1], lab_id="AO25", day="2026-08-20")
                + rows_at_hourly([2.9], lab_id="AF26", day="2026-09-02"))
        got = qs.series_from_rows(rows)
        assert set(got) == {("m1", "Flash Point", "AO25"),
                            ("m1", "Flash Point", "AF26")}

    def test_the_retired_standard_keeps_its_own_points(self):
        rows = (rows_at_hourly([5.0, 5.1], lab_id="AO25", day="2026-08-20")
                + rows_at_hourly([2.9], lab_id="AF26", day="2026-09-02"))
        got = qs.series_from_rows(rows)
        assert list(got[("m1", "Flash Point", "AO25")].values) == [5.0, 5.1]
        assert list(got[("m1", "Flash Point", "AF26")].values) == [2.9]

    def test_each_series_keeps_the_band_of_ITS_OWN_standard(self):
        # The old chart is record. Redrawing it against the new certificate's
        # limits would restate verdicts already reported (17025 7.11.3) and
        # would make every historical point read out of spec.
        old = {"in_spec": True, "low": 4.73, "high": 7.13, "expected": 5.93}
        new = {"in_spec": True, "low": 2.08, "high": 3.44, "expected": 2.76}
        rows = [row("2026-08-20T08:00:00", 5.0, lab_id="AO25", detail=old),
                row("2026-09-02T17:06:41", 2.875, lab_id="AF26", detail=new)]
        got = qs.series_from_rows(rows)
        ao = got[("m1", "Flash Point", "AO25")].pass_band
        af = got[("m1", "Flash Point", "AF26")].pass_band
        assert (ao.low, ao.high, ao.expected) == (4.73, 7.13, 5.93)
        assert (af.low, af.high, af.expected) == (2.08, 3.44, 2.76)

    def test_a_retired_series_is_not_relabelled_with_the_current_standard(self):
        # The old code took the LAST lab_id it saw for the whole series, so a
        # month of AO25 results was captioned "std AF26" on the panel.
        rows = (rows_at_hourly([5.0], lab_id="AO25", day="2026-08-20")
                + rows_at_hourly([2.9], lab_id="AF26", day="2026-09-02"))
        got = qs.series_from_rows(rows)
        assert got[("m1", "Flash Point", "AO25")].sample_id == "AO25"
        assert got[("m1", "Flash Point", "AF26")].sample_id == "AF26"

    def test_series_for_defaults_to_the_standard_in_use_now(self):
        # `uncertainty.read_series` calls this with (machine, test) and no
        # standard. Pooling two materials there is the same contamination as
        # on the chart, and u(Rw) is a signed record.
        rows = (rows_at_hourly([5.0, 5.1], lab_id="AO25", day="2026-08-20")
                + rows_at_hourly([2.9], lab_id="AF26", day="2026-09-02"))
        s = qs.series_for(rows, "m1", "Flash Point")
        assert s.sample_id == "AF26"
        assert list(s.values) == [2.9]

    def test_series_for_can_still_be_asked_for_a_retired_standard(self):
        rows = (rows_at_hourly([5.0, 5.1], lab_id="AO25", day="2026-08-20")
                + rows_at_hourly([2.9], lab_id="AF26", day="2026-09-02"))
        s = qs.series_for(rows, "m1", "Flash Point", sample_id="AO25")
        assert list(s.values) == [5.0, 5.1]

    def test_the_multitek_S_shape_that_started_this(self):
        # The real series, 3 Sep 2026: 36 AO25 readings then one AF26 at 2.875.
        # Pooled, the mean is 4.934 and 2.875 sits below the lower 3s limit of
        # 3.545. Split, the AF26 chart contains exactly the one reading it
        # should and cannot inherit the retired material's centre.
        ao25 = rows_at_hourly([4.981, 4.858, 5.071, 5.175, 4.926, 4.926,
                               5.175, 4.981, 4.858, 5.071, 4.959, 4.832],
                              lab_id="AO25", day="2026-08-20")
        af26 = [row("2026-09-02T17:06:41", 2.875, lab_id="AF26",
                    detail={"in_spec": True, "low": 2.08, "high": 3.44,
                            "expected": 2.76})]
        got = qs.series_from_rows(ao25 + af26)
        current = got[("m1", "Flash Point", "AF26")]
        assert list(current.values) == [2.875]
        assert qs.analyse(current).violations == ()


# ── the centre line is the certificate, never the observed mean ──────────────
#
# `ControlLimits` has always argued for this in its own docstring: "A lab
# normally FIXES its limits from a qualification period and then judges later
# results against them, rather than recomputing the mean every time a point
# arrives — which is a moving target that absorbs the very drift the chart
# exists to show." `analyse()` has always taken supplied limits. Nothing ever
# supplied any, so every chart in this lab was self-fitted.
#
# A self-fitted centre cannot detect the one thing an assessor asks a control
# chart about: has this instrument MOVED away from the reference value? An
# instrument reading 0.5 mg/kg high, consistently, is dead-centre on its own
# mean and perfectly in control of the wrong number.
#
# Ryan, 3 Sep: "make the QC not based on the mean anymore." The centre is the
# certificate's assigned value; the spread is the certificate's std_dev.
#
# `n=0` on these limits is deliberate and load-bearing. `n` means "how many
# results this s was computed from", and a certificate's sigma was computed
# from none of THIS lab's results. `df` therefore floors to 0, `self_fitted`
# is False, and `spread_basis` reports UNKNOWN rather than borrowing the
# coverage of the points being judged — the s_n/s_df confusion SeriesAnalysis
# already documents.

class TestLimitsComeFromTheCertificate:
    AF26 = qs.PassBand(low=2.08, high=3.44, expected=2.76)   # k=2, sd=0.34

    def test_the_centre_is_the_assigned_value_not_the_observed_mean(self):
        lim = qs.certificate_limits(self.AF26, std_dev=0.34)
        assert lim.mean == 2.76

    def test_sigma_is_the_certificates_not_the_benchs_scatter(self):
        lim = qs.certificate_limits(self.AF26, std_dev=0.34)
        assert lim.s == 0.34
        assert lim.zone(1) == pytest.approx((2.42, 3.10))
        assert lim.zone(2) == pytest.approx((2.08, 3.44))   # == the pass band
        assert lim.zone(3) == pytest.approx((1.74, 3.78))

    def test_sigma_can_be_recovered_from_the_band_when_only_k_is_known(self):
        # The QC log's detail records expected/low/high but NOT std_dev or k,
        # so a row on its own cannot say what sigma was. Half the band over k
        # recovers it exactly: 2.08..3.44 at k=2 is 0.68/2 = 0.34.
        lim = qs.certificate_limits(self.AF26, k=2.0)
        assert lim.s == pytest.approx(0.34)
        assert lim.mean == 2.76

    def test_an_explicit_std_dev_beats_one_derived_from_the_band(self):
        # The route reads std_dev straight off lem_machine_specs. A band that
        # disagrees with it is a rounding artefact in the stored low/high, not
        # a second opinion about the certificate.
        lim = qs.certificate_limits(self.AF26, std_dev=0.34, k=7.0)
        assert lim.s == 0.34

    def test_no_sigma_and_no_k_is_no_limits_rather_than_a_guess(self):
        # A retired standard whose spec row is gone. Drawing zones off an
        # invented sigma would be a fabricated control limit on a record an
        # assessor reads.
        assert qs.certificate_limits(self.AF26) is None

    def test_no_band_at_all_is_no_limits(self):
        assert qs.certificate_limits(None, std_dev=0.34) is None

    def test_a_band_with_no_assigned_value_has_no_centre_to_use(self):
        assert qs.certificate_limits(
            qs.PassBand(low=2.08, high=3.44), std_dev=0.34) is None

    def test_sigma_has_no_degrees_of_freedom_from_these_results(self):
        lim = qs.certificate_limits(self.AF26, std_dev=0.34)
        assert (lim.n, lim.df) == (0, 0)

    def test_findings_against_a_certificate_are_firm_not_provisional(self):
        # Self-fitted limits make every finding provisional, because the
        # points wrote the limits they are judged by. A certificate did not
        # come from these points, so a breach of it is a firm finding — and
        # the shift rule, which is suppressed when self-fitted, is live.
        pts = qs.points_from_rows(rows_at_hourly([2.7, 2.8, 9.9]))
        got = qs.analyse(qs.QcSeries(machine_uid="m1", test_name="Flash Point",
                                     points=pts, pass_band=self.AF26,
                                     sample_id="AF26"),
                         limits=qs.certificate_limits(self.AF26, std_dev=0.34))
        assert got.self_fitted is False
        assert [v.rule for v in got.violations] == [qs.RULE_1_3S]
        assert got.firm_violations == got.violations

    def test_a_bench_reading_consistently_high_is_no_longer_in_control(self):
        # THE CASE A SELF-FITTED CHART CANNOT SEE. Eight readings all ~0.6
        # above the assigned value, tightly clustered. Fitted to themselves
        # they are a model process: dead on their own mean, tiny s, nothing
        # fires. Against the certificate they are a bench that has moved.
        drifted = [3.35, 3.37, 3.36, 3.38, 3.35, 3.39, 3.36, 3.37]
        pts = qs.points_from_rows(rows_at_hourly(drifted))
        series = qs.QcSeries(machine_uid="m1", test_name="Flash Point",
                             points=pts, pass_band=self.AF26, sample_id="AF26")
        assert qs.analyse(series).violations == ()          # self-fitted: blind
        against_cert = qs.analyse(
            series, limits=qs.certificate_limits(self.AF26, std_dev=0.34))
        assert against_cert.violations != ()

    def test_the_multitek_S_reading_that_was_wrongly_condemned(self):
        # 2.875 against AF26 (2.76 +/- 0.34) is 0.34 sigma high — inside 1s.
        # The pooled self-fitted chart called it a 3s excursion and told the
        # lab to hold every result since the last good check.
        pts = qs.points_from_rows([row("2026-09-02T17:06:41", 2.875,
                                       lab_id="AF26")])
        got = qs.analyse(qs.QcSeries(machine_uid="m1", test_name="Flash Point",
                                     points=pts, pass_band=self.AF26,
                                     sample_id="AF26"),
                         limits=qs.certificate_limits(self.AF26, std_dev=0.34))
        assert got.violations == ()
        assert got.mean == 2.76
