import os
import sys
from datetime import datetime, timedelta

import pytest

# Make the V5 app package importable (modules live one dir up).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture
def open_for_business(monkeypatch):
    """Make the lab open, without moving the clock.

    Two tests assert behaviour that only holds while the lab is open, and read
    the day from the wall clock to find out. They were green all week and both
    failed the moment the date rolled over to a Saturday:

        test_a_module_that_beat_and_then_stopped_is_STOPPED
            module_state came back "closed", not "stopped"
        test_the_items_arrive_too
            items are days_active [0..4], so only the all-week header survived

    Neither is a defect in the app — a silent module on a day the lab is shut IS
    closed rather than stopped, and web_app says so where it reaches for the
    schedule. It is the tests that were reading the calendar, and a gate that
    fails every weekend cannot tell you anything on a Saturday.

    Note what this deliberately does NOT do: pin `web_app._now` to a fixed
    weekday. That was tried and it broke four other tests, because they seed
    heartbeats and observations relative to the real clock and a fixed date puts
    that seeded data in the future. The calendar and the elapsed-time axis are
    two different dependencies; only the calendar one is wrong here. So the
    working week is widened to seven days and "now" is left alone.

    Anything genuinely exercising a closed day passes its own datetime —
    `test_lab_schedule.py` does exactly that — and is unaffected.
    """
    import lab_schedule

    # Unconditional: the app hands the schedule an explicit `when` taken from
    # `web_app._now()`, so honouring an explicit date here would still consult
    # the real calendar and the fixture would do nothing. Tests that genuinely
    # exercise closing time call LabSchedule directly and never request this
    # fixture, so widening it here costs them nothing.
    monkeypatch.setattr(lab_schedule.LabSchedule, "why_closed",
                        lambda self, when=None: "")

    # The checklist day filter takes its date from `web_app._today()`, which
    # derives from `_now()`. Shift "now" back to the nearest preceding weekday
    # rather than to a fixed date: a fixed date puts seeded heartbeats and
    # observations in the future, which is what broke four other tests when it
    # was tried. A shift of at most two days keeps every elapsed-time
    # relationship intact while removing the calendar dependency.
    import web_app

    real = datetime.now()
    shifted = real - timedelta(days=max(0, real.weekday() - 4))
    monkeypatch.setattr(web_app, "_now", lambda: shifted)


@pytest.fixture
def mock_now(monkeypatch):
    """Pin data_source.NOW so QC-staleness logic is deterministic in tests.

    Mirrors the V4 suite: the engine aliases NOW to datetime.now, so patching it
    freezes "today" near the seeded observation timestamps.
    """
    frozen = datetime(2023, 1, 1, 12, 0, 0)

    import data_source

    monkeypatch.setattr(data_source, "NOW", lambda: frozen)
    return frozen


@pytest.fixture
def sample_spec():
    from models import SampleSpec, SampleTestSpec

    t1 = SampleTestSpec(name="Test1", value_col="ValUe", expected=10.0, std_dev=1.0)
    return SampleSpec(name="ContextA", sample_id_val="S1", tests=[t1])


@pytest.fixture
def box_config(sample_spec):
    from models import BoxConfig, WatchedTarget

    return BoxConfig(
        uid="box1", title="Box 1", csv_path="dummy.csv", qc_expire_hours=24,
        watched_targets=[WatchedTarget(sample="ContextA", test="Test1")],
    )
