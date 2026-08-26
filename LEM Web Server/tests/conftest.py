import importlib.abc
import importlib.machinery
import importlib.util
import os
import sys
from datetime import datetime, timedelta

import pytest

import refusal_shapes

# Make the V5 app package importable (modules live one dir up).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class _PywFinder(importlib.abc.MetaPathFinder):
    """Let `import web_server` find `web_server.pyw`.

    The entry point is a .pyw so Windows launches it without a console window,
    and Python's import machinery only ever looks for .py/.pyc — so the four
    tests in test_deployment.py that drive `build_parser()` and `publish_live()`
    could not import the module they test. They were red, and a red test that
    fails on the file EXTENSION reads like a broken suite rather than a broken
    app, which is how it survived.

    Deliberately narrow: it answers for exactly one module name, and only when
    that .pyw is really there. Anything else falls through to the normal
    finders, so this cannot mask a genuine ImportError elsewhere. It is also
    lazy — the module is executed on first `import web_server`, not at
    collection — and web_server.pyw guards its own entry with
    `if __name__ == "__main__"`, so importing it starts no server.
    """

    NAME = "web_server"

    def find_spec(self, fullname, path=None, target=None):
        if fullname != self.NAME:
            return None
        source = os.path.join(_ROOT, self.NAME + ".pyw")
        if not os.path.isfile(source):
            return None
        # The loader has to be explicit. `spec_from_file_location` picks one by
        # matching the suffix against importlib.machinery.SOURCE_SUFFIXES, which
        # is [".py"] — a .pyw comes back with `spec.loader = None`, and importing
        # that raises the same ModuleNotFoundError as having no finder at all.
        return importlib.util.spec_from_file_location(
            fullname, source,
            loader=importlib.machinery.SourceFileLoader(fullname, source))


if not any(isinstance(f, _PywFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _PywFinder())


@pytest.fixture(scope="session", autouse=True)
def _app_log_goes_somewhere_disposable(tmp_path_factory):
    """Keep the app's real log file out of the checkout.

    `create_app` now opens a rotating log in `tray.data_dir()` (see
    web_app.configure_logging) — which, under pytest, resolves off
    `sys.argv[0]` and lands inside site-packages. The behaviour is right and
    the suite should not be the thing that exercises it into someone's venv,
    so every test run gets its own throwaway data dir. Tests that care about
    the path set LEM_DATA_DIR themselves with monkeypatch, which wins over
    this.
    """
    os.environ.setdefault("LEM_DATA_DIR", str(tmp_path_factory.mktemp("lemdata")))
    yield


@pytest.fixture(params=refusal_shapes.BOTH, ids=refusal_shapes.IDS)
def both_refusal_shapes(request):
    """Run a whole suite once per refusal shape.

    A module opts in with `pytestmark = pytest.mark.usefixtures(
    "both_refusal_shapes")` and answers `refusal_shapes.current()` from its
    fake gateway. The point is that the suites which decide whether this app
    reports a write honestly were all driving ONE shape — and it was the
    invented one. See tests/refusal_shapes.py for which half is evidence.
    """
    refusal_shapes.use(request.param)
    yield request.param
    refusal_shapes.use(refusal_shapes.EVIDENCED)


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
