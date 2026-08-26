"""`/api/machines/<uid>/qc-trend` is a CONTROL CHART, not a pass rate.

`qc_series.py` shipped with 143 tests and was imported by nothing. The endpoint
kept doing its own arithmetic: points, the certificate's band, and a count of
how many fell outside it. That answers "was each result acceptable" and not the
question a PJLA 17025 assessor asks of a control chart, which is whether the
PROCESS is in control.

Three things this file exists to hold, because each of them is a way the
payload could look finished and say something false:

1.  **The zones and the pass band are different quantities.** `pass_band` is
    `expected +/- k*std_dev` off the STANDARD's certificate — a specification.
    `observed.zones` is `mean +/- k*s` from THESE results — an observation. A
    chart that draws one and labels it the other reports a drifting instrument
    as compliant, or a compliant one as drifting. The fixture below is built so
    the two DISAGREE (band 8..16, 3s zone 6..18): a payload that collapsed them
    could not pass both assertions.

2.  **`self_fitted`.** Nobody supplies qualification limits here, so the
    analysis judges the points against limits computed from those same points.
    Every zone finding is `provisional`. If the payload drops either flag the
    UI presents a chart grading itself as an alarm.

3.  **The coverage basis.** A spread that does not span analysts, calendar days
    AND calibrations is not u(Rw), and the payload must never imply it is.

FIXTURES COME FROM WHAT THE BENCH ACTUALLY WRITES
-------------------------------------------------
`qc_detail()` mirrors `lem_station_module.qc_log_detail` key for key, and
`TestTheFixtureIsNotInvented` loads the real function and proves it. A fixture
carrying a key the writer never emits — or missing one it always emits — is how
a payload passes a suite and breaks on the floor.
"""
import json
import statistics

import pytest

from labcore_gateway import FakeLabCoreGateway


# ── the shape the bench writes ──────────────────────────────────────────────

def qc_detail(expected, low, high, in_spec, operator=None,
              calibration_id=None, raw=None, correction=None):
    """EXACTLY the dict `qc_log_detail` puts in `lem_machine_log.detail`.

    `operator` and `calibration_id` are written on EVERY verdict, `None`
    included — present-and-null is how the bench says "looked, did not know",
    and a fixture that omits the keys would test a row shape that only exists
    for verdicts written before the fields were added. Both cases are real, so
    both appear in the seeds below; the DEFAULT here is the older one so a test
    that wants attribution has to ask for it.
    """
    detail = {"in_spec": bool(in_spec), "expected": expected,
              "low": low, "high": high,
              "operator": operator, "calibration_id": calibration_id}
    if correction:
        detail["raw_value"] = raw
        detail["correction"] = float(correction)
    return detail


def station_module():
    import importlib.util
    import pathlib
    path = (pathlib.Path(__file__).resolve().parent.parent.parent
            / "LEM Station Module" / "lem_station_module.py")
    if not path.exists():
        pytest.skip("station module not present next to the web server")
    spec = importlib.util.spec_from_file_location("_lem_mod_for_chart", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestTheFixtureIsNotInvented:
    """The seed shape is the writer's shape — checked against the writer.

    notes.md: a fixture once carried `low: -16` on a list the writer never
    populates, and that invented shape hid a NaN on every instrument for a
    round. The defence is not care, it is loading the real function.
    """

    def test_keys_match_qc_log_detail_exactly(self):
        mod = station_module()
        spec = mod.TestSpec(name="Flash Point", value_col="flash",
                            expected=12.0, std_dev=2.0, k=2.0, units="C")
        real = mod.qc_log_detail(spec, raw=12.5, corrected=12.5,
                                 operator="Ryan", calibration_id="CAL-1")
        mine = qc_detail(12.0, 8.0, 16.0, True, operator="Ryan",
                         calibration_id="CAL-1")
        assert set(real) == set(mine)
        assert real == mine

    def test_a_corrected_verdict_carries_the_raw_reading_too(self):
        mod = station_module()
        spec = mod.TestSpec(name="Flash Point", value_col="flash",
                            expected=12.0, std_dev=2.0, k=2.0, correction=-3.0)
        real = mod.qc_log_detail(spec, raw=15.5, corrected=12.5)
        mine = qc_detail(12.0, 8.0, 16.0, True, raw=15.5, correction=-3.0)
        assert set(real) == set(mine)
        assert real["raw_value"] == 15.5 and real["correction"] == -3.0

    def test_the_band_in_the_fixture_is_the_band_the_bench_computes(self):
        """8..16 below is not a number somebody liked the look of."""
        mod = station_module()
        spec = mod.TestSpec(name="Flash Point", value_col="flash",
                            expected=12.0, std_dev=2.0, k=2.0)
        assert mod.spec_band(spec) == (8.0, 16.0)


# ── the lab ─────────────────────────────────────────────────────────────────

@pytest.fixture
def gw():
    return FakeLabCoreGateway()


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


@pytest.fixture
def client(gw):
    from web_app import create_app
    app = create_app(gw, authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client()


LOG_DDL = ("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
           "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
           "detail TEXT)")


def log(gw, uid, ts, kind, lab_id, test_name, value, detail):
    gw.sql(LOG_DDL)
    gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
           [uid, ts, kind, lab_id, test_name, str(value),
            json.dumps(detail)])


# The band: expected 12.0, std_dev 2.0, k 2.0 -> 8.0 .. 16.0.
# The results: 10, 12, 14 -> mean 12.0, sample s (n-1) 2.0 -> 3s is 6.0 .. 18.0.
# The two DISAGREE, on purpose. See the module docstring.
BAND_LOW, BAND_HIGH, BAND_EXPECTED = 8.0, 16.0, 12.0
VALUES = [10.0, 12.0, 14.0]
MEAN = 12.0
S = 2.0


def seed_attributed(gw):
    """Three results a 17025 assessor can reconstruct: two analysts, three
    calendar days, two calibration epochs. That is u(Rw), and the payload has
    to be able to say so."""
    who = [("Ryan", "CAL-1"), ("kaden", "CAL-2"), ("Ryan", "CAL-1")]
    for i, (value, (operator, cal)) in enumerate(zip(VALUES, who)):
        log(gw, "m1", f"2026-07-{29 + i:02d}T09:00:00", "qc", "L-9001",
            "Flash Point", value,
            qc_detail(BAND_EXPECTED, BAND_LOW, BAND_HIGH, True,
                      operator=operator, calibration_id=cal))


def seed_unattributed(gw):
    """The same three numbers on rows written before the bench recorded who ran
    them. Same spread, and a spread nobody can attribute is not u(Rw)."""
    for i, value in enumerate(VALUES):
        log(gw, "m2", f"2026-07-{29 + i:02d}T09:00:00", "qc", "L-9001",
            "Flash Point", value,
            qc_detail(BAND_EXPECTED, BAND_LOW, BAND_HIGH, True))


def series_of(client, uid, name="Flash Point"):
    body = client.get(f"/api/machines/{uid}/qc-trend").get_json()
    found = [s for s in body["series"] if s["test_name"] == name]
    assert found, f"no series named {name!r} in {[s['test_name'] for s in body['series']]}"
    return found[0]


# ── 1. the band and the zones are two quantities ───────────────────────────

class TestTheBandAndTheZonesAreNotTheSameThing:
    def test_the_pass_band_is_the_standards_certificate(self, gw, client):
        seed_attributed(gw)
        band = series_of(client, "m1")["pass_band"]
        assert band["low"] == BAND_LOW
        assert band["high"] == BAND_HIGH
        assert band["expected"] == BAND_EXPECTED

    def test_the_zones_are_computed_from_the_results(self, gw, client):
        seed_attributed(gw)
        observed = series_of(client, "m1")["observed"]
        assert observed["mean"] == pytest.approx(MEAN)
        assert observed["s"] == pytest.approx(S)
        assert observed["s"] == pytest.approx(statistics.stdev(VALUES))
        zones = observed["zones"]
        assert zones["1s"]["low"] == pytest.approx(MEAN - S)
        assert zones["1s"]["high"] == pytest.approx(MEAN + S)
        assert zones["2s"]["low"] == pytest.approx(MEAN - 2 * S)
        assert zones["2s"]["high"] == pytest.approx(MEAN + 2 * S)
        assert zones["3s"]["low"] == pytest.approx(MEAN - 3 * S)
        assert zones["3s"]["high"] == pytest.approx(MEAN + 3 * S)

    def test_they_are_different_numbers_and_stay_under_different_names(
            self, gw, client):
        """The mutation this catches: `zones = pass_band`, or the reverse.

        Both survive a payload that only checks the keys are present, and both
        are the defect the qc_series module docstring opens with.
        """
        seed_attributed(gw)
        s = series_of(client, "m1")
        band, zones = s["pass_band"], s["observed"]["zones"]
        assert (zones["3s"]["low"], zones["3s"]["high"]) == (6.0, 18.0)
        assert (band["low"], band["high"]) == (8.0, 16.0)
        assert zones["3s"]["low"] != band["low"]
        assert zones["3s"]["high"] != band["high"]

    def test_the_observed_spread_is_reported_as_wider_than_the_band(
            self, gw, client):
        """3s runs 6..18 and the certificate accepts 8..16, so ordinary scatter
        will start failing the standard before any control rule fires. That is
        a real finding and it has its own field."""
        seed_attributed(gw)
        assert series_of(client, "m1")["zones_within_band"] is False

    def test_a_tighter_process_reports_the_zones_inside_the_band(
            self, gw, client):
        """The other direction, so the field is not a constant `False`."""
        for i, value in enumerate(VALUES):
            log(gw, "m3", f"2026-07-{29 + i:02d}T09:00:00", "qc", "L-9001",
                "Flash Point", value, qc_detail(12.0, 0.0, 24.0, True))
        assert series_of(client, "m3")["zones_within_band"] is True

    def test_the_failure_count_is_not_the_violation_count(self, gw, client):
        """Out of SPEC and out of CONTROL are different findings and neither
        substitutes for the other. Here: every result passed the certificate
        and the process is clean, so both are zero — the interesting case is
        that they are two fields."""
        seed_attributed(gw)
        s = series_of(client, "m1")
        assert s["failures"] == 0
        assert s["violations"] == []
        assert s["in_control"] is True


# ── 2. self-fitted limits, and saying so ───────────────────────────────────

class TestAChartGradingItselfSaysSo:
    def test_the_series_is_flagged_self_fitted(self, gw, client):
        seed_attributed(gw)
        s = series_of(client, "m1")
        assert s["self_fitted"] is True
        assert s["observed"]["self_fitted"] is True

    def test_every_zone_finding_comes_back_provisional(self, gw, client):
        """Eleven results, ten flat and one wild. Self-fitted limits make a 3s
        excursion possible only from n=11 up ((n-1)/sqrt(n) > 3), which is
        itself the reason the flag matters."""
        for i in range(10):
            log(gw, "m4", f"2026-07-01T{i:02d}:00:00", "qc", "L-9001",
                "Flash Point", 10.0, qc_detail(12.0, 8.0, 16.0, True))
        log(gw, "m4", "2026-07-01T10:00:00", "qc", "L-9001", "Flash Point",
            20.0, qc_detail(12.0, 8.0, 16.0, False))
        s = series_of(client, "m4")
        beyond = [v for v in s["violations"] if v["rule"] == "1_3s"]
        assert beyond, f"expected a 1_3s finding, got {s['violations']}"
        assert beyond[0]["provisional"] is True
        assert beyond[0]["indices"] == [10]
        assert s["firm_violations"] == 0
        assert s["in_control"] is False

    def test_a_trend_is_firm_even_self_fitted(self, gw, client):
        """`_trend` reads neither the mean nor a zone, so self-fitting does not
        weaken it. If the payload marked every finding provisional this would
        fail — and a chart that can never say anything firmly is a chart nobody
        acts on."""
        for i in range(7):
            log(gw, "m5", f"2026-07-01T{i:02d}:00:00", "qc", "L-9001",
                "Flash Point", float(i + 1), qc_detail(12.0, 0.0, 24.0, True))
        s = series_of(client, "m5")
        trends = [v for v in s["violations"] if v["rule"] == "trend"]
        assert len(trends) == 1
        assert trends[0]["provisional"] is False
        assert trends[0]["side"] == "up"
        assert trends[0]["indices"] == [0, 1, 2, 3, 4, 5, 6]
        assert s["firm_violations"] == 1

    def test_the_finding_carries_its_plain_english_sentence(self, gw, client):
        """The whole point of the messages: a bench tech reads them, so they
        travel into the payload rather than being re-worded by the UI."""
        for i in range(7):
            log(gw, "m5", f"2026-07-01T{i:02d}:00:00", "qc", "L-9001",
                "Flash Point", float(i + 1), qc_detail(12.0, 0.0, 24.0, True))
        message = series_of(client, "m5")["violations"][0]["message"]
        assert "7 results in a row" in message
        assert "run 1" in message
        assert "drifting" in message

    def test_the_rule_names_are_the_modules_own(self, gw, client):
        """`RULE_SHIFT`/`RULE_TREND` changed value after the review. A payload
        re-spelling them would drift from the module silently."""
        import qc_series
        for i in range(7):
            log(gw, "m5", f"2026-07-01T{i:02d}:00:00", "qc", "L-9001",
                "Flash Point", float(i + 1), qc_detail(12.0, 0.0, 24.0, True))
        assert series_of(client, "m5")["violations"][0]["rule"] == \
            qc_series.RULE_TREND

    def test_the_shift_rule_is_not_evaluated_on_self_fitted_limits(
            self, gw, client):
        """Nine results on one side of a mean fitted to those nine results is
        arithmetically impossible to read, and the rule carries remedial
        instructions. `violations()` skips it; the payload must not resurrect
        it."""
        import qc_series
        assert qc_series.SHIFT_RUN == 9
        values = [10.0] * 5 + [14.0] * 9
        for i, value in enumerate(values):
            log(gw, "m6", f"2026-07-01T{i:02d}:00:00", "qc", "L-9001",
                "Flash Point", value, qc_detail(12.0, 0.0, 24.0, True))
        rules = {v["rule"] for v in series_of(client, "m6")["violations"]}
        assert qc_series.RULE_SHIFT not in rules


# ── 3. what the spread may be CALLED ───────────────────────────────────────

class TestTheCoverageBasis:
    def test_two_analysts_over_three_days_and_two_calibrations_is_u_rw(
            self, gw, client):
        seed_attributed(gw)
        cov = series_of(client, "m1")["coverage"]
        assert cov["basis"] == "intermediate"
        assert cov["supports_reproducibility"] is True
        assert cov["supports_repeatability"] is False
        assert cov["n_operators"] == 2
        assert cov["n_days"] == 3
        assert cov["n_calibrations"] == 2
        assert cov["n_unknown_operator"] == 0
        assert cov["n_unknown_calibration"] == 0

    def test_the_caveat_sentence_travels_with_it(self, gw, client):
        seed_attributed(gw)
        cov = series_of(client, "m1")["coverage"]
        assert cov["caveat"] == (
            "3 results from 2 analysts over 3 calendar days against 2 "
            "calibrations: this spread supports within-laboratory "
            "reproducibility, u(Rw).")

    def test_rows_that_name_nobody_are_unknown_not_u_rw(self, gw, client):
        """The same three numbers, the same spread, no attribution. This is the
        overstatement the whole Coverage type exists to prevent."""
        seed_unattributed(gw)
        cov = series_of(client, "m2")["coverage"]
        assert cov["basis"] == "unknown"
        assert cov["supports_reproducibility"] is False
        assert cov["n_unknown_operator"] == 3
        assert cov["n_unknown_calibration"] == 3
        assert "cannot be called" in cov["caveat"]

    def test_one_analyst_one_day_one_calibration_is_repeatability(
            self, gw, client):
        for i, value in enumerate(VALUES):
            log(gw, "m7", f"2026-07-29T0{i}:00:00", "qc", "L-9001",
                "Flash Point", value,
                qc_detail(12.0, 8.0, 16.0, True, operator="Ryan",
                          calibration_id="CAL-1"))
        cov = series_of(client, "m7")["coverage"]
        assert cov["basis"] == "repeatability"
        assert cov["supports_repeatability"] is True
        assert cov["supports_reproducibility"] is False
        assert "repeatability (s_r)" in cov["caveat"]

    def test_varying_two_factors_of_three_is_partial_not_u_rw(self, gw, client):
        """Two analysts over three days inside ONE calibration epoch carries no
        between-calibration component at all."""
        who = ["Ryan", "kaden", "Ryan"]
        for i, (value, operator) in enumerate(zip(VALUES, who)):
            log(gw, "m8", f"2026-07-{29 + i:02d}T09:00:00", "qc", "L-9001",
                "Flash Point", value,
                qc_detail(12.0, 8.0, 16.0, True, operator=operator,
                          calibration_id="CAL-1"))
        cov = series_of(client, "m8")["coverage"]
        assert cov["basis"] == "partial"
        assert cov["supports_reproducibility"] is False
        assert cov["n_calibrations"] == 1

    def test_the_spread_basis_travels_beside_s_and_its_degrees_of_freedom(
            self, gw, client):
        """`(s, s_df, spread_basis)` is the triple a later uncertainty module
        reads, and all three have to describe the SAME set."""
        seed_attributed(gw)
        s = series_of(client, "m1")
        assert s["spread_basis"] == "intermediate"
        assert s["observed"]["s"] == pytest.approx(S)
        assert s["observed"]["n"] == 3
        assert s["observed"]["df"] == 2


# ── the module is actually the one doing the arithmetic ────────────────────

class TestTheEndpointUsesQcSeries:
    def test_the_sample_standard_deviation_uses_the_n_minus_one_divisor(
            self, gw, client):
        """The population divisor would give 1.632993, not 2.0. A hand-rolled
        `statistics.pstdev` in the endpoint passes every "is there a number"
        test and understates the spread by 18%."""
        seed_attributed(gw)
        s = series_of(client, "m1")["observed"]["s"]
        assert s == pytest.approx(statistics.stdev(VALUES))
        assert s != pytest.approx(statistics.pstdev(VALUES))

    def test_a_pm_completion_never_supplies_a_band_or_becomes_a_point(
            self, gw, client):
        """`_is_qc_row`, from the other side. A maintenance record sharing the
        machine and the test name once overwrote the certificate's band with
        its own and put every result out of spec."""
        seed_attributed(gw)
        log(gw, "m1", "2026-08-01T09:00:00", "pm", "L-9001", "Flash Point",
            0.0005, {"low": 0.0, "high": 0.001, "expected": 0.0005,
                     "task": "Annual service"})
        s = series_of(client, "m1")
        assert s["pass_band"]["low"] == BAND_LOW
        assert s["pass_band"]["high"] == BAND_HIGH
        assert len(s["points"]) == 3
        assert [p["value"] for p in s["points"]] == VALUES

    def test_the_newest_band_wins_when_a_standard_is_recertified(
            self, gw, client):
        seed_attributed(gw)
        log(gw, "m1", "2026-08-02T09:00:00", "qc", "L-9001", "Flash Point",
            12.0, qc_detail(12.5, 9.0, 16.0, True))
        assert series_of(client, "m1")["pass_band"]["expected"] == 12.5

    def test_a_verdict_the_log_does_not_carry_is_unjudged_not_a_failure(
            self, gw, client):
        """`in_spec` is tri-state. `False` for a row whose detail could not be
        read invents a failure that never happened."""
        seed_attributed(gw)
        gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
               ["m1", "2026-08-03T09:00:00", "qc", "L-9001", "Flash Point",
                "11.0", "{not json at all"])
        s = series_of(client, "m1")
        assert s["failures"] == 0
        assert s["unjudged"] == 1
        assert s["runs"] == 4

    def test_the_violation_indices_point_into_the_points_that_were_served(
            self, gw, client):
        """The endpoint truncates the chart to the newest results. If it
        analysed the WHOLE history and then truncated, every `indices` entry
        would be off by the number of points dropped and the UI would circle
        the wrong readings."""
        for i in range(70):
            log(gw, "m9", f"2026-07-01T{i // 60:02d}:{i % 60:02d}:00", "qc",
                "L-9001", "Flash Point", 10.0,
                qc_detail(12.0, 0.0, 24.0, True))
        for i in range(7):
            log(gw, "m9", f"2026-07-02T{i:02d}:00:00", "qc", "L-9001",
                "Flash Point", float(i + 1), qc_detail(12.0, 0.0, 24.0, True))
        s = series_of(client, "m9")
        assert len(s["points"]) == 60
        assert s["runs"] == 60
        trends = [v for v in s["violations"] if v["rule"] == "trend"]
        assert len(trends) == 1
        served = [s["points"][i]["value"] for i in trends[0]["indices"]]
        assert served == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]


# ── nothing the floor already draws was taken away ─────────────────────────

class TestTheExistingContractSurvives:
    def test_points_band_and_counts_are_still_where_they_were(self, gw, client):
        """floor.html is being rewritten against this payload right now. The
        new fields are additions; the old names still mean what they meant."""
        seed_attributed(gw)
        s = series_of(client, "m1")
        assert [p["value"] for p in s["points"]] == VALUES
        assert [p["in_spec"] for p in s["points"]] == [True, True, True]
        assert s["low"] == BAND_LOW and s["high"] == BAND_HIGH
        assert s["expected"] == BAND_EXPECTED
        assert s["runs"] == 3 and s["failures"] == 0
        assert s["sample_id"] == "L-9001"

    def test_a_read_that_failed_is_never_an_empty_chart(self, gw, client):
        """LabCore refuses by ANSWERING with an error dict, not by raising —
        so a chart of no points is exactly what a naive endpoint would draw."""
        class Broken(FakeLabCoreGateway):
            def read_sql(self, sql, args=None, **kw):
                if "lem_machine_log" in sql:
                    return {"error": "HTTPSConnectionPool: Read timed out"}
                return super().read_sql(sql, args, **kw)

        from web_app import create_app
        app = create_app(Broken(), authenticator=StubAuth(), secret="s")
        app.config["TESTING"] = True
        r = app.test_client().get("/api/machines/m1/qc-trend")
        assert r.status_code in (502, 503)
        assert "series" not in r.get_json()

    def test_no_history_is_an_empty_list_and_a_200(self, gw, client):
        r = client.get("/api/machines/nobody/qc-trend")
        assert r.status_code == 200
        assert r.get_json()["series"] == []
