"""The replayable lab day: a controllable clock, the real modules, and the
handful of pokes that stand in for a timer firing.

Nothing in here waits on wall time. `Clock` owns "now" for both LEM and
LabStation, and the two things the real lab waits on — the 5s auto-push
debounce and the 1 AM daily refresh — are driven by calling `_auto_push()` and
`_on_daily_refresh()` directly, so a whole day replays in milliseconds.

── WHAT THE HARNESS IS NOT ALLOWED TO DO FOR THE PRODUCT (hardened) ────────────

`push_now` used to set `results._grid_dirty = True` before calling
`_auto_push()`. That forged the operator's edit AND the module's own wake-up
(lem_station_module.py:3120-3124), so a fix that never armed the push — meaning
nothing is ever stored in the real lab unless someone happens to type in the
grid — still went green. It now fires only what the product itself armed.

Likewise `type_into_grid` no longer sets `_grid_dirty` by hand: it puts text in
the cell and lets LabStation's real `_on_grid_item_changed`
(LabStation.pyw:11755-11775) arm the debounce, and asserts that it did.

Worker exceptions are recorded in `WORKER_ERRORS` rather than printed. A raise
on the worker strands `_polling` in the real product (CLAUDE.md), so it must
fail a test, not degrade into a quiet None.
"""
import importlib.machinery
import importlib.util
import sys
from datetime import datetime, timedelta

_APP = None

# Every exception a "background" body raised during the test. An autouse
# fixture empties and asserts on this — see conftest._no_swallowed_worker_error.
WORKER_ERRORS = []


def set_app(app):
    """The QApplication the harness pumps to deliver queued callbacks."""
    global _APP
    _APP = app


def settle(rounds: int = 30):
    """Deliver every queued callback, then return.

    LabStation hands worker results back through a QUEUED signal, so the
    callback runs after the current call stack unwinds — `_refresh_grid` fires
    its fetch and finishes painting before the fetch's own repaint happens.
    Running the callback inline instead would paint twice and the harness would
    be measuring its own artefact. So: the "worker" body runs immediately (that
    is what makes the replay deterministic) and only its callback is queued.
    """
    if _APP is None:
        return
    for _ in range(rounds):
        _APP.processEvents()


def deferred_run_in_thread(fn, callback):
    """Drop-in for `_run_in_thread`: body now, callback queued.

    Used for BOTH LabStation and LEM. LEM used to get a fully inline runner
    (`cb(fn())`), which collapsed the exact split the bug lives in —
    `_process_outcome` deciding `sync_rows` on a worker, `_show_outcome` doing
    the Results hand-off on the main thread — into one call stack. A fix whose
    correctness depends on that ordering (e.g. "write directly only if the
    hand-off actually failed") would then be judged under an ordering
    production cannot produce.
    """
    from PySide6 import QtCore
    try:
        result = fn()
    except Exception as exc:
        # NOT printed: LabStation's real _run_in_thread drops the callback on
        # an exception, which strands LEM's `_polling` flag. That is a
        # regression the suite must fail on, so it is recorded and asserted.
        WORKER_ERRORS.append(exc)
        return
    QtCore.QTimer.singleShot(0, lambda: callback(result))


# ── the clock ────────────────────────────────────────────────────────────────

class Clock:
    """The only source of "now" in the harness."""

    def __init__(self, start: datetime):
        self._now = start

    def now(self, tz=None):
        return self._now

    def set(self, moment: datetime):
        self._now = moment
        return self._now

    def advance(self, **kwargs):
        self._now = self._now + timedelta(**kwargs)
        return self._now

    def next_1am(self):
        """Roll to the next 1:00 AM — the moment the daily refresh fires."""
        target = self._now.replace(hour=1, minute=0, second=0, microsecond=0)
        if target <= self._now:
            target += timedelta(days=1)
        return self.set(target)


def frozen_datetime_class(clock: Clock):
    """A `datetime` drop-in whose .now() is the clock's.

    A subclass, not a proxy, so any `isinstance(x, datetime)` in either module
    keeps working.
    """

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.now()

        @classmethod
        def today(cls):
            return clock.now()

        @classmethod
        def utcnow(cls):
            return clock.now()

    return _FrozenDateTime


# ── loading the real modules ─────────────────────────────────────────────────

def import_source(name: str, path, register=True):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    if register:
        sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


def load_lem_like_labstation(lem_file, labstation, injections):
    """Load lem_station_module.py the way LabStation's module loader does.

    Deliberately NOT registered in sys.modules and re-parented onto the real
    BaseModule with `dict(cls.__dict__)` — that is the loader's actual
    behaviour (LabStation.pyw ~12699), and it is why the module may never use
    `from __future__ import annotations`. Running the replay through the same
    path means an install-breaking change fails here too.
    """
    spec = importlib.util.spec_from_file_location(
        "lem_station_module_replay", str(lem_file))
    mod = importlib.util.module_from_spec(spec)
    mod.BaseModule = labstation.BaseModule
    mod.LabStationContext = labstation.LabStationContext
    mod.ResultEntry = getattr(labstation, "ResultEntry", object)
    for key, value in injections.items():
        setattr(mod, key, value)
    spec.loader.exec_module(mod)
    # Re-apply AFTER exec: the module's own `from datetime import datetime`
    # runs during exec and would otherwise put the real clock back.
    for key, value in injections.items():
        setattr(mod, key, value)

    candidates = [
        getattr(mod, n) for n in dir(mod)
        if isinstance(getattr(mod, n), type)
        and getattr(mod, n) is not labstation.BaseModule
        and getattr(getattr(mod, n), "module_type", "") not in ("", "Base")
    ]
    cls = next(c for c in candidates if c.module_type == "LEMStation")
    if not issubclass(cls, labstation.BaseModule):
        cls = type(cls.__name__, (cls, labstation.BaseModule),
                   dict(cls.__dict__))
        cls.__module__ = mod.__name__
    return mod, cls


# ── building a bench ─────────────────────────────────────────────────────────

def make_machine(lem, *, uid="mach-1", title="Densitometer",
                 csv_path="", lab_id_index=0, mappings=(), corrections=None):
    """A single_csv bench: one comma-delimited print per line, Lab ID in a
    cell, each mapping pulling another cell.

    `corrections` is the machine's correction factors ({method: offset}).
    It used to be left empty in every replay, so the correction boundary
    (`apply_row_corrections`, lem:1544) never ran and a fix that stored the RAW
    reading instead of the reported one passed the whole suite — an ISO/IEC
    17025 §7.8.2 defect the module's own docstring calls out.
    """
    m = lem.Machine(
        uid=uid, title=title, source_type="single_csv", csv_path=str(csv_path),
        delimiter=",",
        lab_id=lem.Selector(mode="cell", index=lab_id_index, clean=["strip"]),
    )
    for index, methods in mappings:
        m.mappings.append(lem.MethodMapping(
            methods=list(methods),
            selector=lem.Selector(mode="cell", index=index,
                                  clean=["strip", "keep_number"]),
        ))
    if corrections:
        m.corrections = dict(corrections)
    return m


def make_results_module(labstation, context, columns, date_filter="All"):
    """A real ResultsModule with `columns` = [(header, [test, ...]), ...].

    `date_filter` is the module's own picker text. Most events pin it to "All"
    so a missing row can only ever be the identity bug — but the SHIPPED
    default is "Yesterday" (LabStation.pyw:9305), and one event runs there so
    the harness is not permanently exercising a setting the lab does not use.
    """
    results = labstation.ResultsModule(context)
    results._columns = [{"name": name, "tests": list(tests)}
                        for name, tests in columns]
    results._sync_columns_derived()
    # What LabStation's schema discovery sets in the real system. Left at its
    # placeholder default ("received_at") the date clause names a column the
    # samples table does not have, so every filtered query errors out and the
    # grid silently shows nothing — which would make E16 pass for the wrong
    # reason. LabCore maps its logical `received_at` onto this same physical
    # column (LV_DB_SCHEMA, LabCore.py:5875).
    results.db_date_column = "first_seen_at"
    results._date_combo.setCurrentText(date_filter)
    results._apply_date_filter(date_filter)
    results._bootstrap_done = True
    return results


def date_clause(results):
    """The module's REAL date filter, handed to the oracle so the oracle asks
    the same question the grid asks."""
    return results._date_filter_clause()


def load_grid(results):
    """Pull the grid from LabCore and repaint — what the module does on open."""
    results._grid_fetch_token = None
    results._refresh_grid()
    settle()


def grid_snapshot(results):
    """[(tab, lab_id, [(header, text, blacked_out), ...]), ...]"""
    out = []
    names = ["filtered", "additional"]
    for tab, grid in zip(names, results._all_grids()):
        for r in range(grid.rowCount()):
            id_item = grid.item(r, 0)
            if id_item is None:
                continue
            cells = []
            for c in range(1, grid.columnCount()):
                item = grid.item(r, c)
                header = grid.horizontalHeaderItem(c)
                cells.append((header.text() if header else str(c),
                              item.text() if item else "",
                              _is_blackout(item)))
            out.append((tab, id_item.text(), cells))
    return out


def on_screen(results, lab_id, header):
    """What the operator can READ in the cell for this sample, right now.

    Identity is exact: a value painted on a row labelled "34566" is not on
    screen for the LIMS's "081126-34566". The first harness never asserted the
    number reached the grid at all, which is why deleting the whole Results
    hand-off cost it only one test.
    """
    for _tab, row_id, cells in grid_snapshot(results):
        if row_id.strip() != lab_id:
            continue
        for name, text, _black in cells:
            if name == header and text.strip():
                return text.strip()
    return None


def cell_identity(results, lab_id, header):
    """The identity of the widget holding this cell.

    Used to prove a repaint REALLY rebuilt the row, so a test that claims "the
    value survived being rebuilt from LabCore" cannot pass on a grid the event
    never touched. The critique's vacuity check — disable the event, see if the
    test still fails identically — is exactly what this defeats.
    """
    for grid in results._all_grids():
        for r in range(grid.rowCount()):
            id_item = grid.item(r, 0)
            if id_item is None or id_item.text().strip() != lab_id:
                continue
            for c in range(1, grid.columnCount()):
                head = grid.horizontalHeaderItem(c)
                if head is not None and head.text() == header:
                    item = grid.item(r, c)
                    return None if item is None else id(item)
    return None


def _is_blackout(item):
    if item is None:
        return False
    from PySide6 import QtCore
    return item.data(QtCore.Qt.ItemDataRole.UserRole) == "blackout"


def fresh_results_view(labstation, context, columns, date_filter="All"):
    """A brand-new ResultsModule loading the grid from LabCore alone.

    This is the durability oracle: it holds no appended row, no
    `_manual_lab_ids`, no widget memory of the bench. Whatever it paints is
    what survives a restart — and it is a strictly harder question than a raw
    sqlite query, because it runs the real load, the real date filter and the
    real blackout logic.
    """
    fresh = make_results_module(labstation, context, columns, date_filter)
    assert fresh._manual_lab_ids == set()
    load_grid(fresh)
    return fresh


def type_into_grid(results, lab_id, column_index, text):
    """An operator typing a result into the grid.

    Goes through the real `itemChanged` path — the harness does NOT set
    `_grid_dirty` itself, or a product that stopped noticing operator edits
    would still look fine. Returns False if the product failed to notice.
    """
    from PySide6 import QtWidgets
    for grid in results._all_grids():
        for r in range(grid.rowCount()):
            item = grid.item(r, 0)
            if item is not None and item.text().strip() == lab_id:
                cell = grid.item(r, 1 + column_index)
                if cell is None:
                    cell = QtWidgets.QTableWidgetItem()
                    grid.setItem(r, 1 + column_index, cell)
                cell.setText(text)
                settle()
                return bool(results._grid_dirty)
    return False


def push_now(results):
    """Fire the 5s auto-push debounce WITHOUT waiting five seconds.

    Fires only what the product armed. It does not mark the grid dirty and it
    does not start the timer: arming the push is LEM's job (lem:3120-3124) and
    the operator's, and a harness that does it for them cannot see a fix that
    stopped doing it.
    """
    fired = False
    timer = getattr(results, "_auto_push_timer", None)
    if timer is not None and timer.isActive():
        timer.stop()
        results._auto_push()
        settle()
        fired = True
    if results._write_queue:
        results._flush_write_queue()
        settle()
        fired = True
    return fired


def force_push(results):
    """The operator hitting the grid's own push, ignoring the debounce.

    Used only where a test needs to prove that even an EXPLICIT push cannot
    recover a reading — never as a substitute for the module arming itself.
    """
    results._grid_dirty = True
    results._auto_push()
    settle()
    if results._write_queue:
        results._flush_write_queue()
        settle()


def daily_refresh(results, clock):
    """1:00 AM, station left on overnight."""
    clock.next_1am()
    results._on_daily_refresh()
    settle()


def poll(bench):
    """One LEM poll, run to completion."""
    bench.poll_now()
    settle()


def print_line(path, text):
    """Append one device print to the watched CSV."""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text.rstrip("\n") + "\n")


def rewind(machine):
    """Replay the same source input: re-read the file from the top."""
    machine.last_position = 0
