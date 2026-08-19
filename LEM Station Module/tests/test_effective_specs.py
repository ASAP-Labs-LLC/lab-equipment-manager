"""The module has to publish what it is ACTUALLY checking.

Reported 2026-08-03: clicking a machine on the floor shows an empty "QC checks"
panel, and the module shows no min/max.

Root cause, confirmed against live LabCore: `lem_qc_specs` has **0 rows**, and
`lem_machine_targets` has 2 rows covering exactly one machine. Yet PAC Flash 1 and
2 are running Flash Point QC against expected 63.72 — because the module resolves
its specs at runtime from `lem_qc_samples` (the shared standards, matched by Lab
ID) and never writes the result anywhere. So the web server has literally nothing
to render, and says "No QC assigned" about an instrument that is being checked.

The two existing tables are inputs:
  * `lem_qc_specs`       — a human's per-machine numeric override
  * `lem_machine_targets` — what was assigned from the floor

This adds the missing output: `lem_machine_specs`, the **effective** specs the
module resolved and is applying, including the low/high band it judges against.
Deliberately a separate table — writing resolved specs back into `lem_qc_specs`
would turn the module's own output into an override it then reads back in.
"""
from datetime import datetime

import pytest

import lem_station_module as mod
from lem_station_module import Machine, TestSpec

NOW = datetime(2026, 8, 3, 18, 30)


def spec(**over):
    base = dict(name="ASTM D7236/D7094 - Flash Point Closed cup (small scale)",
                value_col="ASTM D7236/D7094 - Flash Point Closed cup (small scale)",
                expected=63.72, std_dev=1.05, k=2.0, sample_id="AO25", units="C")
    base.update(over)
    return TestSpec(**base)


def machine(**over):
    base = dict(uid="5fd04c0031f9", title="PAC Flash 1", tests=[spec()])
    base.update(over)
    return Machine(**base)


# ── the band the module judges against ──────────────────────────────────────

class TestSpecBand:
    def test_low_and_high_are_expected_plus_minus_k_sigma(self):
        low, high = mod.spec_band(spec())
        assert low == pytest.approx(61.62)
        assert high == pytest.approx(65.82)

    def test_it_matches_what_the_module_logged_live(self):
        """From lem_machine_log: {"expected": 63.72, "low": 61.62, "high": 65.82}."""
        low, high = mod.spec_band(spec())
        assert (round(low, 2), round(high, 2)) == (61.62, 65.82)

    def test_a_k_of_one_is_a_narrower_band(self):
        low, high = mod.spec_band(spec(expected=-7.4, std_dev=2.8, k=1.0))
        assert (round(low, 2), round(high, 2)) == (-10.2, -4.6)

    def test_zero_sigma_collapses_to_the_target(self):
        assert mod.spec_band(spec(std_dev=0.0)) == (63.72, 63.72)

    # The band is a published number as well as a judged one: `low` and `high`
    # go into lem_machine_specs and the floor draws min/target/max from them.
    # Same defect as the sulfur results reported 2026-08-13 — expected ± k·σ was
    # binary float arithmetic, so a low-sulfur spec published
    # low=0.0009000000000000001. Ryan asked for the same Decimal treatment here
    # after being told it moves the pass/fail boundary by about one ULP.

    @pytest.mark.parametrize("expected,std_dev,k,low,high", [
        (0.0015, 0.0003, 2.0, "0.0009", "0.0021"),   # the case that showed it
        (0.0453, 0.0021, 2.0, "0.0411", "0.0495"),
        (63.72, 1.05, 2.0, "61.62", "65.82"),
        (2.6431, 0.0224, 2.0, "2.5983", "2.6879"),
        (0.482, 0.017, 1.0, "0.465", "0.499"),
    ])
    def test_the_band_reads_like_a_lab_number(self, expected, std_dev, k,
                                              low, high):
        got_low, got_high = mod.spec_band(
            spec(expected=expected, std_dev=std_dev, k=k))
        assert str(got_low) == low
        assert str(got_high) == high

    def test_the_floor_and_the_verdict_still_share_one_band(self):
        """The whole reason spec_band exists. Both readers must get the same
        two numbers, or the floor draws a band the module did not judge with."""
        s = spec(expected=0.0015, std_dev=0.0003, k=2.0)
        low, high = mod.spec_band(s)
        assert mod.spec_band(s) == (low, high)
        assert low <= 0.0012 <= high          # a reading inside the band
        assert not (low <= 0.00089 <= high)   # and one just outside it

    def test_a_junk_spec_never_raises_on_the_verdict_path(self):
        """This decides a machine's status. A bad number must degrade, not
        explode — a raise here strands the poll."""
        for bad in (float("nan"), float("inf")):
            low, high = mod.spec_band(spec(std_dev=bad))
            assert isinstance(low, float) and isinstance(high, float)


# ── publishing ──────────────────────────────────────────────────────────────

class TestPublishEffectiveSpecs:
    def test_the_op_count_does_not_grow_with_the_number_of_tests(self):
        """The write queue serialises at ~1.5 ops/sec, so a twelve-test standard
        must not be twelve writes. It is a DELETE plus one multi-row INSERT —
        two either way, not two per test."""
        one = mod.build_effective_specs_publish(machine(), NOW)
        twelve = mod.build_effective_specs_publish(
            machine(tests=[spec(name=f"T{i}", value_col=f"T{i}")
                           for i in range(12)]), NOW)
        assert len(one) == len(twelve) == 2, f"{len(one)} vs {len(twelve)}"

    def test_all_twelve_rows_ride_in_that_one_insert(self):
        m = machine(tests=[spec(name=f"T{i}", value_col=f"T{i}") for i in range(12)])
        _delete, (sql, args) = mod.build_effective_specs_publish(m, NOW)
        # 14 columns per row since the correction factor joined them.
        assert sql.count("(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)") == 12
        assert len(args) == 12 * 14

    def test_it_carries_the_numbers_the_panel_needs(self):
        _delete, (sql, args) = mod.build_effective_specs_publish(machine(), NOW)
        assert "lem_machine_specs" in sql
        assert "5fd04c0031f9" in args
        assert 63.72 in args and pytest.approx(61.62) in args \
            and pytest.approx(65.82) in args
        assert "C" in args and "AO25" in args

    def test_it_replaces_rather_than_accumulates(self):
        """A test that stops being checked must disappear, or the panel shows QC
        the instrument is no longer running."""
        ops = mod.build_effective_specs_publish(machine(), NOW)
        assert any("DELETE" in sql.upper() for sql, _a in ops) or \
            any("REPLACE" in sql.upper() for sql, _a in ops), \
            "stale specs would linger"

    def test_no_specs_publishes_a_clearing_write(self):
        """Unassigning everything has to be visible too."""
        ops = mod.build_effective_specs_publish(machine(tests=[]), NOW)
        assert ops, "nothing published, so the panel would keep showing old QC"
        assert any("DELETE" in sql.upper() for sql, _a in ops)

    def test_the_remembered_verdict_rides_along(self):
        """So the floor can show the last reading against the band, not just the
        band — that is the difference between a chart and a label."""
        m = machine(tests=[spec(last_qc_at=NOW.isoformat(), last_qc_value=65.0,
                                last_qc_in_spec=True)])
        _delete, (_sql, args) = mod.build_effective_specs_publish(m, NOW)
        assert 65.0 in args

    def test_the_table_is_declared(self):
        assert "lem_machine_specs" in mod.EFFECTIVE_SPECS_DDL
        assert "CREATE TABLE IF NOT EXISTS" in mod.EFFECTIVE_SPECS_DDL


# ── it only writes when something changed ───────────────────────────────────

class TestQuietWhenUnchanged:
    def test_identical_specs_are_a_no_op(self):
        m = machine()
        first = mod.effective_specs_fingerprint(m)
        assert mod.effective_specs_fingerprint(machine()) == first

    def test_a_changed_limit_is_detected(self):
        assert mod.effective_specs_fingerprint(machine(tests=[spec(std_dev=9.9)])) \
            != mod.effective_specs_fingerprint(machine())

    def test_a_new_reading_is_detected(self):
        """The panel shows the last value, so a new one has to be published."""
        m = machine(tests=[spec(last_qc_value=65.0)])
        assert mod.effective_specs_fingerprint(m) \
            != mod.effective_specs_fingerprint(machine())

    def test_dropping_a_test_is_detected(self):
        assert mod.effective_specs_fingerprint(machine(tests=[])) \
            != mod.effective_specs_fingerprint(machine())
