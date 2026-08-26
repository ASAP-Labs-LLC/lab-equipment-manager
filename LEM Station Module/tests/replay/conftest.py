"""The wiring behind the replay: one clock, one sqlite LabCore, the real
LabStation and the real LEM module, all sharing one canvas.

Every fixture here is deliberately FUNCTION-scoped except the two module
imports. A replay event is a lab day, and a lab day starts with an empty
database, an empty canvas and a bench that has never polled; sharing any of
that between events would let one event's held queue, written-cell memory or
painted grid row answer for the next one's.

The two exceptions are `labstation` and `labcore`: they are 17,000-line source
files, importing them costs seconds, and neither holds per-test state — the
state lives in the LabStationContext and the sqlite file, both of which are
rebuilt every test.
"""
import sys
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
for _path in (str(_HERE), str(_HERE.parents[1]), str(_HERE.parents[0])):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import lab_day                      # noqa: E402
import labcore_db                   # noqa: E402
import replay_paths                 # noqa: E402

# The day the replay is set on: TODAY, mid-morning, derived from the real
# calendar rather than written down.
#
# It has to be today. Only LEM's clock is frozen to it — the `lem` fixture
# injects a frozen datetime class — while the Results module builds its shipped
# "Yesterday" range from the real system clock, because that code is LabStation's
# and the harness does not patch it. Pin DAY to a literal and the two agree only
# on the day it was written: E16 then seeds `received_at` from the frozen clock,
# LabStation filters against the real one, the reading falls outside the range
# and the suite reports that the shipped grid cannot show it — an accusation
# against the identity road for a fault that is entirely the harness's.
#
# Verified by the round-4 critic, who shifted this constant three days and
# watched E16 go red on working code. A judge that expires is worse than no
# judge, because the failure names the wrong culprit.
DAY = datetime.now().replace(hour=9, minute=30, second=0, microsecond=0)


@pytest.fixture(scope="session")
def qapp():
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    lab_day.set_app(app)
    return app


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    """Keep LabStation's and LEM's own on-disk state out of the developer's
    real AppData tree — a replay must not be able to read, or leave behind, a
    latest-result file from the machine it happens to run on."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))


@pytest.fixture(autouse=True)
def _no_swallowed_worker_error():
    """A raise on the worker must fail a test, not degrade into a quiet None.

    `deferred_run_in_thread` records instead of printing, because LabStation's
    real `_run_in_thread` DROPS the callback on an exception — which strands
    LEM's `_polling` flag and stops the bench polling at all (CLAUDE.md). A
    replay that let that pass would be certifying a bench that has stopped.
    """
    lab_day.WORKER_ERRORS.clear()
    yield
    assert not lab_day.WORKER_ERRORS, (
        "a background body raised — LabStation drops the callback on this and "
        f"LEM's _polling stays True forever: {lab_day.WORKER_ERRORS!r}")


@pytest.fixture
def clock():
    return lab_day.Clock(DAY)


@pytest.fixture(scope="session")
def labstation(qapp):
    """The REAL LabStation.pyw, loaded from the clone.

    Its own src directory goes on sys.path first because the file imports its
    siblings (`labcore_client`) by name. `_run_in_thread` is replaced with the
    harness's deferred runner — the same one LEM gets — so both modules keep
    the worker/main-thread split the product has and neither collapses into one
    call stack.
    """
    path = replay_paths.labstation_pyw()
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    mod = lab_day.import_source("labstation_replay", path)
    mod._run_in_thread = lab_day.deferred_run_in_thread
    return mod


@pytest.fixture(scope="session")
def labcore():
    """The REAL LabCore.py, for its own batch handlers.

    Nothing in the harness reimplements a write: `LabCoreGateway` looks every
    inner operation up in `_BATCH_INNER_OPS` and hands it a live connection,
    which is what makes `_batch_update_cell`'s bare INSERT — the whole reason an
    orphan result row is accepted — part of the replay rather than a
    paraphrase of it.
    """
    path = replay_paths.labcore_py()
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    return lab_day.import_source("labcore_replay", path)


@pytest.fixture
def db(tmp_path):
    return labcore_db.create_database(tmp_path / "labcore.sqlite")


@pytest.fixture
def gateway(db, labcore, clock):
    return labcore_db.LabCoreGateway(db, labcore, clock)


@pytest.fixture
def oracle(db):
    return labcore_db.Oracle(db)


@pytest.fixture
def seed(db, oracle):
    """The LIMS logging a sample in — the only thing allowed to create one.

    Every identity that goes through here is recorded on the oracle, so
    `phantom_samples()` can name anything in `samples` that the software under
    test minted for itself.
    """
    def _seed(samples=(), tests=(), received=None):
        stamp = received or DAY.isoformat(sep=" ", timespec="seconds")
        con = sqlite3.connect(str(db))
        try:
            for lab_id in samples:
                con.execute(
                    f'INSERT OR IGNORE INTO "samples" (lab_id, "'
                    f'{labcore_db.RECEIVED_COL}") VALUES (?, ?)',
                    [lab_id, stamp])
                oracle.seeded_samples.add(lab_id)
            for lab_id, test_name in tests:
                con.execute(
                    'INSERT OR IGNORE INTO "sample_tests" '
                    '(lab_id, test_name, result) VALUES (?, ?, ?)',
                    [lab_id, test_name, ""])
            con.commit()
        finally:
            con.close()
    return _seed


@pytest.fixture
def context(labstation, gateway, qapp):
    """The canvas both modules live on, with this test's LabCore behind it.

    LabStation reaches LabCore two ways and the replay has to answer both: the
    Results module pushes through `context.labcore_client.batch`, and almost
    everything else — the grid fetch included — goes through the module-level
    `labcore_*` shims, which in the product hold a LabCoreClient. The shims are
    re-pointed at THIS test's gateway rather than the client being stood in for,
    because the client's read signature (`args=`) is not the gateway's and a
    stand-in that quietly swallowed the mismatch would leave the grid empty and
    every identity assertion passing for the wrong reason.
    """
    ctx = labstation.LabStationContext()
    ctx.labcore_client = gateway
    labstation.labcore_read_sql = (
        lambda sql, args=None, timeout=None: gateway.read_sql(sql, args or []))
    labstation.labcore_sql = (
        lambda sql, args=None, source="LabStation", timeout=None:
        gateway.sql(sql, args or [], source=source))
    labstation.labcore_write = (
        lambda operation, params, source="LabStation":
        gateway.write(operation, params, source=source))
    labstation.labcore_is_running = lambda: True
    return ctx


@pytest.fixture
def lem(labstation, gateway, clock):
    """lem_station_module.py, loaded the way LabStation's loader loads it.

    The labcore_* helpers and `datetime` are INJECTED, exactly as the loader
    injects them, so the module under replay is the shipped file with no test
    seam cut into it. `labcore_is_running` answers True because these events
    are about a LabCore that is up; the events that need it down say so.

    This list is ONLY names `_load_custom_module` really injects. It used to
    also supply `labcore_user` and `labcore_username`, which LabStation has
    never injected and which appear nowhere in its source — so the module's two
    audit bylines read them, got "" on every real bench, and passed under
    replay. A harness that supplies what production does not is a harness that
    hides the bug it exists to catch. The signed-in user is an attribute of the
    context (`current_user`), not a global; see `context_operator`.
    """
    mod, cls = lab_day.load_lem_like_labstation(
        replay_paths.LEM_FILE, labstation, {
            "datetime": lab_day.frozen_datetime_class(clock),
            "_run_in_thread": lab_day.deferred_run_in_thread,
            "labcore_write": gateway.write,
            "labcore_sql": gateway.sql,
            "labcore_read_sql": gateway.read_sql,
            "labcore_is_running": lambda: True,
        })
    mod._REPLAY_CLASS = cls
    return mod


@pytest.fixture
def bench(lem, context):
    """One LEM module on the canvas — the bench the instrument prints to."""
    return lem._REPLAY_CLASS(context)


@pytest.fixture
def make_results(labstation, context):
    def _make(columns, date_filter="All"):
        return lab_day.make_results_module(labstation, context, columns,
                                           date_filter)
    return _make


@pytest.fixture
def fresh_view(labstation, context):
    def _fresh(columns, date_filter="All"):
        return lab_day.fresh_results_view(labstation, context, columns,
                                          date_filter)
    return _fresh


@pytest.fixture
def printer(tmp_path):
    """The instrument's printer: a watched file, one print per line."""

    class _Printer:
        def __init__(self, path):
            self.path = str(path)
            open(self.path, "a", encoding="utf-8").close()

        def prints(self, text):
            lab_day.print_line(self.path, text)

    return _Printer(tmp_path / "prints.csv")
