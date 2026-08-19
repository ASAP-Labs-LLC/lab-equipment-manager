"""The module front view has to show the band, not just the reading.

Reported 2026-08-03: "the module doesnt show min and max values". Each QC row
showed a battery, the last value and the test name — but never what the value was
being judged against, so an operator could see 65.0 and had no way to know whether
the limit was 65.82 or 64.0.

The numbers were always there (`expected ± k·std_dev`, the same `spec_band` the
floor and the evaluator use); nothing displayed them.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import lem_station_module as mod
from lem_station_module import TestSpec


def spec(**over):
    base = dict(name="ASTM D7236/D7094 - Flash Point", value_col="Flash",
                expected=63.72, std_dev=1.05, k=2.0, units="C")
    base.update(over)
    return TestSpec(**base)


# ── the label ───────────────────────────────────────────────────────────────

class TestLimitText:
    def test_it_reads_min_to_max_with_units(self):
        assert mod.limits_text(spec()) == "61.62 – 65.82 C"

    def test_a_narrow_band_is_not_rounded_away(self):
        assert mod.limits_text(spec(expected=2.6431, std_dev=0.0224, k=2.0,
                                    units="mm2/s")) == "2.5983 – 2.6879 mm2/s"

    def test_negative_limits_read_correctly(self):
        assert mod.limits_text(spec(expected=-7.4, std_dev=2.8, k=1.0,
                                    units="C")) == "-10.20 – -4.60 C"

    def test_no_units_leaves_no_trailing_space(self):
        assert mod.limits_text(spec(units="")) == "61.62 – 65.82"

    def test_a_zero_sigma_spec_says_the_single_target(self):
        """A band of zero width is a target, and printing "63.72 – 63.72" reads
        like a mistake."""
        assert mod.limits_text(spec(std_dev=0.0)) == "63.72 C"

    def test_it_agrees_with_the_band_the_evaluator_uses(self):
        low, high = mod.spec_band(spec())
        text = mod.limits_text(spec())
        assert f"{low:.2f}" in text and f"{high:.2f}" in text


# ── it reaches the widget ───────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return app


class TestTheRowShowsIt:
    def test_the_row_displays_the_limits(self, qapp):
        row = mod._QCRow(spec())
        assert "61.62" in row.limits_text_shown()
        assert "65.82" in row.limits_text_shown()

    def test_it_is_visible_before_any_result_arrives(self, qapp):
        """The point is to know the target BEFORE running the standard."""
        row = mod._QCRow(spec())
        assert row.limits_text_shown().strip() not in ("", "—")

    def test_it_survives_a_result_update(self, qapp):
        from datetime import datetime
        row = mod._QCRow(spec())
        row.update_result(mod.Machine(uid="m1", title="T", tests=[spec()]),
                          mod.TestResult("Flash", 65.0, True,
                                         datetime(2026, 8, 3, 16, 24)),
                          datetime(2026, 8, 3, 18, 0))
        assert "65.82" in row.limits_text_shown()
        assert "65" in row.value_text()
