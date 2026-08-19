"""QC expires on a rolling window, and both engines must agree it does.

Ryan, 2026-08-03: "As long as it tracks real 24 hours and survives restarts and
reconfigs (aka moving modules from one PC to another) then it should be fine."

V4 expired QC at the **calendar-day boundary**. A standard run at 23:00 was stale
at 00:01 — an hour later — while one run at 00:30 lasted nearly 48 hours. It also
rounded the stored hours to whole days (`max(1, round(hours/24))`), which silently
disabled any window shorter than a day: an 8-hour window behaved as 24.

The rule now lives in two places on purpose — `data_source.qc_is_stale` here and
`lem_station_module.qc_is_stale` in the module, which cannot import from this
package because LabStation loads it as a lone file. **This file is what stops them
drifting apart.**
"""
from datetime import datetime, timedelta

import pytest

from data_source import qc_is_stale

NOW = datetime(2026, 8, 3, 15, 0)


def module_rule():
    """The station module's copy of the rule, loaded as LabStation loads it."""
    import importlib.util
    import pathlib
    path = (pathlib.Path(__file__).resolve().parent.parent.parent
            / "LEM Station Module" / "lem_station_module.py")
    if not path.exists():
        pytest.skip("station module not present next to the web server")
    spec = importlib.util.spec_from_file_location("_lem_mod_for_qc_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.qc_is_stale


# ── the window is real hours ────────────────────────────────────────────────

class TestRollingWindow:
    def test_just_inside_is_fresh(self):
        assert qc_is_stale(NOW - timedelta(hours=23, minutes=59), NOW, 24) is False

    def test_just_outside_is_stale(self):
        assert qc_is_stale(NOW - timedelta(hours=24, minutes=1), NOW, 24) is True

    def test_exactly_the_window_has_expired(self):
        assert qc_is_stale(NOW - timedelta(hours=24), NOW, 24) is True

    def test_last_night_at_23_00_is_still_fresh_this_morning(self):
        """The reported case, and the whole reason this changed."""
        morning = datetime(2026, 8, 3, 8, 0)
        last_night = datetime(2026, 8, 2, 23, 0)
        assert (morning - last_night) < timedelta(hours=24)
        assert qc_is_stale(last_night, morning, 24) is False

    def test_a_run_at_00_30_does_not_last_two_days(self):
        """The other half of the calendar-day bug: it used to be good until the
        end of the following day, nearly 48 hours."""
        early = datetime(2026, 8, 2, 0, 30)
        next_evening = datetime(2026, 8, 3, 20, 0)
        assert qc_is_stale(early, next_evening, 24) is True

    def test_a_window_shorter_than_a_day_actually_works(self):
        """`max(1, round(8/24))` was 1 day, so an 8-hour window did nothing."""
        assert qc_is_stale(NOW - timedelta(hours=9), NOW, 8) is True
        assert qc_is_stale(NOW - timedelta(hours=7), NOW, 8) is False

    def test_a_longer_window_works_too(self):
        assert qc_is_stale(NOW - timedelta(hours=30), NOW, 72) is False
        assert qc_is_stale(NOW - timedelta(hours=73), NOW, 72) is True

    def test_no_result_is_not_stale(self):
        """"Never run" is a different state from "ran and aged out" — the callers
        report them differently (yellow "not yet run" vs yellow "stale")."""
        assert qc_is_stale(None, NOW, 24) is False

    def test_a_future_timestamp_is_not_stale(self):
        """Clock skew between a bench PC and the server must not read as expired."""
        assert qc_is_stale(NOW + timedelta(hours=1), NOW, 24) is False


# ── the two copies agree ────────────────────────────────────────────────────

class TestBothEnginesAgree:
    CASES = [
        ("just inside", timedelta(hours=23, minutes=59), 24),
        ("just outside", timedelta(hours=24, minutes=1), 24),
        ("exactly on", timedelta(hours=24), 24),
        ("last night 23:00", timedelta(hours=16), 24),
        ("short window, over", timedelta(hours=9), 8),
        ("short window, under", timedelta(hours=7), 8),
        ("long window, under", timedelta(hours=30), 72),
        ("long window, over", timedelta(hours=73), 72),
        ("ancient", timedelta(days=14), 24),
        ("moments ago", timedelta(minutes=1), 24),
    ]

    def test_the_module_and_the_server_never_disagree(self):
        theirs = module_rule()
        for label, age, hours in self.CASES:
            when = NOW - age
            mine = qc_is_stale(when, NOW, hours)
            assert mine is theirs(when, NOW, hours), \
                f"{label}: server says {mine}, module disagrees"

    def test_they_agree_about_never_run(self):
        assert qc_is_stale(None, NOW, 24) is module_rule()(None, NOW, 24)

    def test_neither_rounds_hours_to_days(self):
        theirs = module_rule()
        when = NOW - timedelta(hours=9)
        assert qc_is_stale(when, NOW, 8) is True
        assert theirs(when, NOW, 8) is True
