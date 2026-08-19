"""The results road: whose sample is this, and what happens when nobody knows.

Every test here exists because the road it guards has already been got wrong
once. The module used to file a reading under the Lab ID the INSTRUMENT printed
and mint a sample to match — a phantom "34566" beside the LIMS's
"081126-34566", read by nothing, stamped with the wrong date, leaving the LIMS's
own cell blank forever.

The fix resolves identity against LabCore before writing and holds anything it
cannot place. The failures that fix can have are all of one shape: something
goes wrong, and the code decides it may as well write SOMETHING. So these are
mostly tests that nothing was written.
"""
import json
import sqlite3
import threading
from datetime import datetime, timedelta

import pytest
from PySide6 import QtWidgets

import lem_station_module as mod
from lem_station_module import LAB_ID_KEY, Machine, TestSpec

from test_module_qt import make_module

NOW = datetime(2026, 8, 11, 12, 0, 0)
BARE = "34566"
CANONICAL = "081126-34566"


def row(lab_id=BARE, when=NOW, **values):
    r = {LAB_ID_KEY: lab_id,
         "parsed_date": when.strftime("%Y-%m-%d"),
         "parsed_time": when.strftime("%H:%M:%S")}
    r.update(values)
    return r


def _sql_suffix(name):
    """`_ID_SUFFIX_SQL` in Python, so the fake answers what LabCore answers.

    The real third arm is
    `ltrim(lower(substr(lab_id, instr(lab_id, '-') + 1)), '0')`, and this fake
    used to approximate it with `endswith("-" + key)` — which is stricter. A
    sample logged in as "081126-034566" answers to a printed "34566" on the real
    thing and did not here, so the one shape that produces a SAME-DAY tie was
    invisible to every test in this file. `sample_matches` accepts it, the tie
    guard is the only thing that then stops a wrong-sample write, and no test
    could reach it.
    """
    low = str(name or "").strip().lower()
    _head, dash, tail = low.partition("-")
    return (tail if dash else low).lstrip("0")


class Gateway:
    """The injected labcore_* helpers, with the two failure modes LabCore
    actually has: an error DICT from a full queue, and a raise from a dropped
    connection."""

    def __init__(self, samples=(), samples_answer=None, write_error=None,
                 write_raises=None, qc_specs=()):
        self.samples = list(samples)
        self.qc_specs = list(qc_specs)   # lem_qc_specs, as LabCore holds them
        self.samples_answer = samples_answer   # override: an error dict
        self.write_error = write_error
        self.write_raises = write_raises
        self.ops = []
        self.sqls = []
        self.identity_queries = []
        # lem_held_results, as LabCore holds it: one row per bench, surviving
        # any number of modules. The first version of this fake answered the
        # held query with an error, which meant every test ran against a module
        # that could neither restore nor mirror — and the two bugs that road had
        # were invisible for exactly that reason.
        self.held_table = {}

    def write(self, operation, params=None, source=""):
        if self.write_raises:
            raise self.write_raises
        self.ops.extend((params or {}).get("operations", []))
        if self.write_error:
            return {"error": self.write_error}
        return {"ok": True}

    def sql(self, sql, args=None, source=""):
        self.sqls.append((sql, args))
        if "lem_held_results" in sql and sql.lstrip().upper().startswith(
                "INSERT"):
            self.held_table[args[0]] = args[2]
        return {"ok": True}

    def held_writes(self):
        """The mirror writes, newest last, as (machine_uid, held JSON)."""
        return [(args[0], args[2]) for sql, args in self.sqls
                if "lem_held_results" in sql
                and sql.lstrip().upper().startswith("INSERT")]

    def read_sql(self, sql, args=None, **kw):
        if 'FROM "samples"' in sql:
            self.identity_queries.append((sql, list(args or [])))
            if self.samples_answer is not None:
                return dict(self.samples_answer)
            keys = {str(a).lower() for a in (args or [])}
            return {"ok": True, "columns": ["lab_id"],
                    "rows": [{"lab_id": s} for s in self.samples
                             if s.lower() in keys
                             or s.lower().lstrip("0") in keys
                             or _sql_suffix(s) in keys]}
        if "lem_held_results" in sql:
            held = self.held_table.get((args or [""])[0])
            return {"ok": True, "columns": ["held"],
                    "rows": [{"held": held}] if held is not None else []}
        if "lem_qc_specs" in sql:
            return {"ok": True, "rows": list(self.qc_specs)}
        return {"error": "no such table"}

    def cells(self):
        return [(o["params"]["lab_id"], o["params"]["test_name"],
                 o["params"]["value"]) for o in self.ops
                if o["operation"] == "update_cell"]


@pytest.fixture
def bench(qapp, monkeypatch):
    """A module wired to a gateway, ready to run its results road."""

    def build(gateway, machine=None):
        module = make_module()
        monkeypatch.setitem(mod.__dict__, "labcore_write", gateway.write)
        monkeypatch.setitem(mod.__dict__, "labcore_sql", gateway.sql)
        monkeypatch.setitem(mod.__dict__, "labcore_read_sql", gateway.read_sql)
        module._machine = machine or Machine(uid="m1", title="Eraspec")
        return module

    return build


def store(module, gateway, rows, machine=None, now=NOW):
    messages = []
    result = module._store_results(machine or module._machine, rows,
                                   gateway.read_sql, gateway.sql,
                                   gateway.write, messages, now)
    return result, messages


# ── The invariant, stated once and checked structurally ─────────────────────

class TestLemNeverInventsASample:
    def test_the_word_does_not_appear_in_any_operation_this_module_emits(self):
        """A grep, deliberately.

        The bug was not that insert_sample was emitted on the wrong branch; it
        was that it was emitted at all. There is no condition under which this
        module should mint a sample identity, so the strongest test is that the
        string is not in any op it can build. (It is still in the prose that
        explains why — hence `"insert_sample"` with the quotes.)
        """
        source = open(mod.__file__, encoding="utf-8").read()
        assert '"insert_sample"' not in source
        assert "'insert_sample'" not in source

    def test_a_reading_for_an_unknown_sample_produces_no_ops_at_all(self, bench):
        gateway = Gateway(samples=[])
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])
        assert gateway.ops == []

    def test_it_is_filed_under_the_lims_identity_not_the_printed_one(self,
                                                                     bench):
        gateway = Gateway(samples=[CANONICAL])
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])
        assert gateway.cells() == [(CANONICAL, "Density", "0.8654")]


# ── "LabCore could not be asked" is not permission to guess ─────────────────
#
# The first version of this fix fell back to the old road — printed ID, minted
# sample — whenever the identity read returned an error. On a LabCore whose
# database is on a network share, reads serialise behind the write queue and are
# refused by backpressure, so that branch was reachable on any busy afternoon,
# and it flushed the ENTIRE held queue down it at once.

class TestABusyLabCoreHoldsRatherThanGuesses:
    BUSY = {"error": "LabCore is busy (queue full)."}

    def test_a_refused_read_writes_nothing(self, bench):
        gateway = Gateway(samples=[CANONICAL], samples_answer=self.BUSY)
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])
        assert gateway.ops == [], (
            "a question we could not ask is not an answer; filing on it is the "
            "orphan row the whole road exists to prevent")

    def test_a_refused_read_keeps_the_reading(self, bench):
        gateway = Gateway(samples_answer=self.BUSY)
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])
        assert module._held_rows == [row(Density="0.8654")]

    def test_a_read_that_raises_is_the_same_as_one_that_errors(self, bench):
        def boom(sql, args=None, **kw):
            raise RuntimeError("connection reset")

        gateway = Gateway()
        gateway.read_sql = boom
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])
        assert gateway.ops == []
        assert len(module._held_rows) == 1

    def test_the_backlog_it_was_holding_is_not_flushed_with_it(self, bench):
        """The worst case: a big held queue and a busy minute at once."""
        gateway = Gateway(samples=[CANONICAL], samples_answer=self.BUSY)
        module = bench(gateway)
        module._held_rows = [row(lab_id=str(34560 + i), Density="0.86")
                             for i in range(20)]
        store(module, gateway, [row(Density="0.8654")])
        assert gateway.ops == []
        assert len(module._held_rows) == 21

    def test_it_files_them_all_the_moment_the_read_answers_again(self, bench):
        gateway = Gateway(samples=[CANONICAL], samples_answer=self.BUSY)
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])
        gateway.samples_answer = None
        store(module, gateway, [])
        assert gateway.cells() == [(CANONICAL, "Density", "0.8654")]
        assert module._held_rows == []

    def test_the_operator_is_told_which_state_this_is(self, bench):
        """Not "no sample matches": there is nothing to log in and nothing to
        do but wait, which is a different instruction."""
        gateway = Gateway(samples_answer=self.BUSY)
        module = bench(gateway)
        result, messages = store(module, gateway, [row(Density="0.8654")])
        assert any("cannot say what samples it holds" in m for m in messages)
        assert "cannot say what samples it holds" in result["notice"]
        assert "no LabCore sample matches" not in result["notice"]


class TestTheOneErrorThatIsAnAnswer:
    """A gateway with no `samples` table has no sample identity to resolve
    against, and no table for a phantom to appear in. There the printed ID is
    the identity — and even there nothing is minted."""

    def test_a_missing_samples_table_files_under_the_printed_id(self, bench):
        gateway = Gateway(samples_answer={"error": "no such table: samples"})
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])
        assert gateway.cells() == [(BARE, "Density", "0.8654")]
        assert gateway.ops == [o for o in gateway.ops
                               if o["operation"] == "update_cell"]

    @pytest.mark.parametrize("error", [
        "LabCore is busy (queue full).",
        "Expression tree is too large (maximum depth 1000)",
        "too many SQL variables",
        "database is locked",
        "timed out",
    ])
    def test_every_other_error_holds(self, error, bench):
        gateway = Gateway(samples_answer={"error": error})
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])
        assert gateway.ops == [], f"{error!r} was treated as an answer"


# ── One poll has no bound, so the question must not either ──────────────────

class TestTheIdentityQuestionScalesWithThePoll:
    """`_ingest_multi` reads every file in the watched folder in a single pass,
    so a module that was off overnight can present a poll of hundreds of prints.
    A query built from all of them at once breaks SQLite's 999 bound variables
    (before 3.32), which comes back as an error, i.e. as "hold everything".

    The counts here are written against the constants rather than the numbers
    they currently hold: the numbers are a cost trade — how many full scans of
    `samples` one poll may spend on the connection the whole lab shares — and
    turning that dial must not be a day of arithmetic in a test file."""

    def test_a_real_sqlite_compiles_every_chunk_of_a_huge_poll(self):
        printed = [str(30000 + i) for i in range(400)] + \
                  [f"0{40000 + i}" for i in range(400)]
        db = sqlite3.connect(":memory:")
        db.execute('CREATE TABLE "samples" (lab_id TEXT)')
        for sql, params, chunk in mod.build_sample_identity_queries(printed):
            assert len(params) <= 999, (
                f"{len(params)} bound variables — SQLITE_MAX_VARIABLE_NUMBER "
                "is 999 before SQLite 3.32")
            db.execute(sql, params).fetchall()   # raises if it will not compile

    def test_every_printed_id_is_asked_about_exactly_once(self):
        printed = [str(30000 + i) for i in range(250)]
        asked = [key for _sql, _params, chunk in
                 mod.build_sample_identity_queries(printed) for key in chunk]
        assert asked == printed

    def test_a_big_poll_still_files_everything(self, bench):
        printed = [str(30000 + i) for i in range(mod.IDENTITY_LOOKUP_CHUNK * 2)]
        gateway = Gateway(samples=[f"081126-{p}" for p in printed])
        module = bench(gateway)
        store(module, gateway, [row(lab_id=p, Density="0.86") for p in printed])
        assert len(gateway.cells()) == len(printed)
        assert module._held_rows == []
        assert len(gateway.identity_queries) == 2

    def test_one_poll_cannot_issue_an_unbounded_number_of_reads(self, bench):
        """Chunking bounds the size of each question, not how many are asked.

        Every chunk is a full scan of `samples` — the arms wrap lab_id in
        lower()/ltrim() and the dated one extracts a suffix, so the primary key
        cannot be used — and they go out sequentially, on the worker, with
        `_polling` held, through the connection every other bench in the lab
        shares. A first run over an archive folder is one poll of thousands of
        prints: unbounded, that was seventy-five consecutive full scans before
        this bench answered anything.
        """
        printed = [str(30000 + i) for i in range(1200)]
        gateway = Gateway(samples=[f"081126-{p}" for p in printed])
        module = bench(gateway)
        store(module, gateway, [row(lab_id=p, Density="0.86") for p in printed])
        assert len(gateway.identity_queries) == mod.IDENTITY_LOOKUP_MAX_CHUNKS

    def test_and_the_readings_it_did_not_reach_are_not_lost(self, bench):
        """The remainder waits — but NOT in the hundred-row held queue.

        That queue's cap is a fair answer to a hundred readings LabCore has been
        asked about and cannot place. These have not been asked about at all, so
        putting them there would drop nine hundred of the twelve hundred and call
        it a queue overflow.
        """
        per_poll = mod.IDENTITY_LOOKUP_CHUNK * mod.IDENTITY_LOOKUP_MAX_CHUNKS
        printed = [str(30000 + i) for i in range(1200)]
        gateway = Gateway(samples=[f"081126-{p}" for p in printed])
        module = bench(gateway)
        store(module, gateway, [row(lab_id=p, Density="0.86") for p in printed])
        assert len(gateway.cells()) == per_poll
        assert len(module._identity_backlog) == 1200 - per_poll
        assert module._held_rows == []

    def test_and_the_backlog_drains_a_poll_at_a_time(self, bench):
        printed = [str(30000 + i) for i in range(1200)]
        gateway = Gateway(samples=[f"081126-{p}" for p in printed])
        module = bench(gateway)
        rows = [row(lab_id=p, Density="0.86") for p in printed]
        store(module, gateway, rows)
        for i in range(1, 4):                       # three more polls
            store(module, gateway, [], now=NOW + timedelta(seconds=12 * i))
        assert module._identity_backlog == []
        assert len(gateway.cells()) == 1200, (
            "every reading in the archive must reach LabCore, late or not")
        assert module._held_rows == []

    def test_an_ARCHIVE_of_old_prints_drains_the_same_way(self, bench):
        """The case the ceiling was built for, with the timestamps it really has.

        A first run over an archive folder is prints from days ago, and the
        backlog used to be rebuilt from whatever `identity_lookup_ids` asked
        about — which on a non-sweep poll is only rows parsed within
        HELD_FRESH_WINDOW. So on poll two the whole untried remainder was absent
        from the ask, came out classed as "asked and unplaceable", and all but a
        hundred of it was shredded by the held-queue cap. The queue built to stop
        an archive import being shredded fed it to the shredder.
        """
        old = NOW - timedelta(days=3)
        printed = [str(30000 + i) for i in range(1200)]
        gateway = Gateway(samples=[f"081126-{p}" for p in printed])
        module = bench(gateway)
        rows = [row(lab_id=p, when=old, Density="0.86") for p in printed]
        store(module, gateway, rows)
        for i in range(1, 4):                       # polls 12s apart: no sweep
            store(module, gateway, [], now=NOW + timedelta(seconds=12 * i))
        assert len(gateway.cells()) == 1200, (
            "an archive of old prints must file exactly like a fresh one — "
            "nothing about a reading nobody has asked about goes stale")
        assert module._identity_backlog == []
        assert module._held_rows == []

    def test_a_backlog_says_so_instead_of_reading_ready(self, bench):
        per_poll = mod.IDENTITY_LOOKUP_CHUNK * mod.IDENTITY_LOOKUP_MAX_CHUNKS
        printed = [str(30000 + i) for i in range(1200)]
        gateway = Gateway(samples=[f"081126-{p}" for p in printed])
        module = bench(gateway)
        result, _ = store(module, gateway,
                          [row(lab_id=p, Density="0.86") for p in printed])
        assert str(1200 - per_poll) in result["notice"], result["notice"]
        assert "machine log" in result["notice"], (
            "the backlog is not mirrored into LabCore, so where it lives is "
            "the operator's business: " + result["notice"])

    def test_one_bad_chunk_costs_only_the_readings_in_it(self, bench):
        """Chunking is also containment: a query the database refuses must not
        strand the rest of the poll."""
        chunk = mod.IDENTITY_LOOKUP_CHUNK
        printed = [str(30000 + i) for i in range(chunk + 40)]
        gateway = Gateway(samples=[f"081126-{p}" for p in printed])
        real = gateway.read_sql

        def flaky(sql, args=None, **kw):
            if 'FROM "samples"' in sql and len(gateway.identity_queries) == 1:
                gateway.identity_queries.append((sql, list(args or [])))
                return {"error": "LabCore is busy (queue full)."}
            return real(sql, args, **kw)

        gateway.read_sql = flaky
        module = bench(gateway)
        store(module, gateway, [row(lab_id=p, Density="0.86") for p in printed])
        assert len(gateway.cells()) == chunk
        assert len(module._held_rows) == 40


# ── Nothing is given up before the write comes back ─────────────────────────

class TestTheQueuesSurviveAFailedWrite:
    """Both queues used to be emptied into locals BEFORE the write. A gateway
    that raises rather than returning an error dict — a dropped LAN connection,
    a wrapper that does not catch — therefore destroyed every accumulated late
    reading and every refused op, silently."""

    def test_a_raising_write_keeps_the_ops_for_the_next_poll(self, bench):
        gateway = Gateway(samples=[CANONICAL],
                          write_raises=RuntimeError("connection reset"))
        module = bench(gateway)
        _, messages = store(module, gateway, [row(Density="0.8654")])
        assert [mod.result_cell_key(o) for o in module._retry_ops] == [
            (CANONICAL, "Density", "0.8654")]
        assert any("write error" in m for m in messages)

    def test_a_raising_write_does_not_empty_the_retry_queue(self, bench):
        gateway = Gateway(samples=[CANONICAL],
                          write_raises=RuntimeError("connection reset"))
        module = bench(gateway)
        module._retry_ops = [{"operation": "update_cell",
                              "params": {"lab_id": "A", "test_name": "T",
                                         "value": "1"}}]
        store(module, gateway, [])
        assert module._retry_ops

    def test_a_raising_write_does_not_empty_the_held_queue(self, bench):
        gateway = Gateway(samples=[], write_raises=RuntimeError("reset"))
        module = bench(gateway)
        module._held_rows = [row(lab_id="99999", Density="0.86")]
        store(module, gateway, [row(Density="0.8654")])
        assert len(module._held_rows) == 2

    def test_the_reading_goes_out_on_the_poll_after_the_gateway_returns(self,
                                                                        bench):
        gateway = Gateway(samples=[CANONICAL],
                          write_raises=RuntimeError("connection reset"))
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])
        gateway.write_raises = None
        store(module, gateway, [])
        assert gateway.cells() == [(CANONICAL, "Density", "0.8654")]
        assert module._retry_ops == []

    def test_a_refused_reading_is_held_as_well_as_queued(self, bench):
        """`_retry_ops` is memory only. A restart between the refusal and the
        next poll would take the reading with it, so the ROW is held too — and
        the duplicate collapses in `_unwritten`, which deduplicates on
        (sample, test, value) across the whole batch."""
        gateway = Gateway(samples=[CANONICAL], write_error="queue full")
        module = bench(gateway)
        result, _ = store(module, gateway, [row(Density="0.8654")])
        assert mod.row_lab_ids(module._held_rows) == [BARE]
        assert result["filed"] == [], (
            "nothing may be painted as delivered when the write was refused")
        assert [args for sql, args in gateway.sqls
                if "lem_held_results" in sql], "and it must be durable"

    def test_holding_it_as_well_does_not_write_it_twice(self, bench):
        gateway = Gateway(samples=[CANONICAL], write_error="queue full")
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])
        gateway.write_error = None
        gateway.ops = []
        store(module, gateway, [])
        store(module, gateway, [])
        assert gateway.cells() == [(CANONICAL, "Density", "0.8654")]
        assert module._held_rows == []

    def test_a_refused_write_is_retried_and_not_duplicated(self, bench):
        gateway = Gateway(samples=[CANONICAL], write_error="queue full")
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])
        gateway.write_error = None
        store(module, gateway, [])
        store(module, gateway, [])
        assert gateway.cells() == [(CANONICAL, "Density", "0.8654"),
                                   (CANONICAL, "Density", "0.8654")], (
            "once refused, once accepted — and never a third time")


# ── Two threads, one queue ──────────────────────────────────────────────────

class TestOnlyOneThreadHasCustodyAtATime:
    def test_a_second_caller_parks_its_rows_instead_of_replacing_the_queue(
            self, bench):
        """The lost update, reproduced.

        A poll worker is inside `_store_results`, blocked on the identity read.
        The operator saves the setup dialog on the main thread. Before the lock,
        the second caller computed its own `waiting` snapshot and assigned it
        over the first caller's — deleting whatever the first had just taken
        custody of.
        """
        gateway = Gateway(samples=[])
        module = bench(gateway)
        entered = threading.Event()
        release = threading.Event()
        real = gateway.read_sql

        def slow(sql, args=None, **kw):
            if 'FROM "samples"' in sql:
                entered.set()
                release.wait(5)
            return real(sql, args, **kw)

        gateway.read_sql = slow
        worker = threading.Thread(
            target=lambda: store(module, gateway, [row(lab_id="A",
                                                       Density="0.86")]))
        worker.start()
        assert entered.wait(5)
        # The main thread, while the worker is still inside.
        store(module, gateway, [row(lab_id="B", Density="0.87")])
        release.set()
        worker.join(5)
        assert sorted(mod.row_lab_ids(module._held_rows + module._parked_rows)) \
            == ["A", "B"], "one caller's snapshot deleted the other's reading"

    def test_the_second_caller_does_not_wait_for_the_first(self, bench):
        """It is the GUI thread. A blocking lock here is a frozen window for the
        length of a network round trip."""
        gateway = Gateway(samples=[])
        module = bench(gateway)
        module._storing.acquire()
        try:
            started = datetime.now()
            store(module, gateway, [row(Density="0.8654")])
            assert (datetime.now() - started) < timedelta(seconds=1)
        finally:
            module._storing.release()

    def test_an_operator_action_does_not_run_the_results_road(self, bench):
        """`_reevaluate_and_show` calls the sync inline on the main thread. The
        results road there is an identity read plus a batch write of the whole
        held queue, with the window frozen behind it and the operator waiting on
        a dialog."""
        gateway = Gateway(samples=[CANONICAL])
        module = bench(gateway)
        module._held_rows = [row(Density="0.8654")]
        module._reevaluate_and_show()
        assert gateway.identity_queries == []
        assert gateway.ops == []


# ── The display half may not arm a writer ───────────────────────────────────

class GridResults:
    """A Results module whose `_append_lab_id_row` has LabStation's own shape:
    block the grid, paint, unblock — restoring to False, not to the state the
    caller was in (LabStation.pyw:13051-13069)."""
    module_type = "Results"

    def __init__(self, watched):
        self._columns = [{"tests": list(tests)} for tests in watched]
        self.grid = QtWidgets.QTableWidget(0, 1 + len(watched))
        self.fired = []
        self.grid.itemChanged.connect(lambda item: self.fired.append(item))
        self.footer = 0

    def _all_grids(self):
        return [self.grid]

    def _append_lab_id_row(self, lab_id, results=None, mark_as=None):
        self.grid.blockSignals(True)
        r = self.grid.rowCount()
        self.grid.insertRow(r)
        self.grid.setItem(r, 0, QtWidgets.QTableWidgetItem(lab_id))
        for gcol, value in (results or {}).items():
            self.grid.setItem(r, gcol, QtWidgets.QTableWidgetItem(value))
        self.grid.blockSignals(False)
        return r

    def _update_status_footer(self):
        self.footer += 1


class TestTheHandOffArmsNothing:
    """Painting a cell on a live grid emits itemChanged, which sets
    `_grid_dirty` and starts the grid's debounced auto-push — a second writer
    that files whatever it finds under `col["tests"][0]` when the sample has no
    assigned test, which is precisely the freshly-logged-in sample this road is
    built around. Blocking once at the top of the hand-off is not enough,
    because the Results module's own append unblocks unconditionally."""

    def test_a_second_row_painted_after_an_appended_one_fires_nothing(self,
                                                                      qapp):
        module = make_module()
        results = GridResults([["Density"], ["Viscosity"]])
        # One poll, two prints of the same sample: the first appends a row, the
        # second paints into it. Routine on a bench that prints per test.
        rows = [row(Density="0.8654"), row(Viscosity="3.21")]
        module._deliver_rows_to_results(results, rows,
                                        {BARE: CANONICAL})
        assert results.fired == [], (
            "the hand-off re-armed the grid's own writer")
        assert results.grid.item(0, 2).text() == "3.21", (
            "and it must still have painted the value")

    def test_the_grid_is_left_in_the_state_it_was_handed(self, qapp):
        module = make_module()
        results = GridResults([["Density"]])
        assert results.grid.signalsBlocked() is False
        module._deliver_rows_to_results(results, [row(Density="0.8654")],
                                        {BARE: CANONICAL})
        assert results.grid.signalsBlocked() is False, (
            "the grid's own editing must work after LEM has finished with it")


# ── Rows the road must never take custody of ────────────────────────────────

class TestAPrintWithNoLabIdNamesNoSample:
    """`parse_print` keeps a print that produced measurements but no Lab ID —
    a purge or standby report, or a Lab ID capture that has stopped matching the
    print layout. The reading is real and belongs in the machine log. What it
    can never be is filed, and waiting seven days will not change that."""

    def nameless(self, when=NOW):
        return row(lab_id="", when=when, Density="0.8654")

    def test_it_is_not_held(self, bench):
        gateway = Gateway(samples=[])
        module = bench(gateway)
        result, _ = store(module, gateway, [self.nameless()])
        assert module._held_rows == []
        assert result["notice"] == ""
        assert gateway.ops == []

    def test_the_operator_is_told_once_and_told_where_it_went(self, bench):
        gateway = Gateway(samples=[])
        module = bench(gateway)
        _, first = store(module, gateway, [self.nameless()])
        assert any("no Lab ID" in m and "machine log" in m for m in first)
        _, second = store(module, gateway,
                          [self.nameless(NOW + timedelta(minutes=1))],
                          now=NOW + timedelta(minutes=1))
        assert not any("no Lab ID" in m for m in second), (
            "on a bench that prints one every poll this would be the status "
            "line all day, and it would bury the notice that can be acted on")

    def test_it_cannot_evict_a_genuinely_late_reading(self, bench):
        """The eviction argument depends on nothing permanently unplaceable
        being in the queue. A broken Lab ID mapping is one print per poll, so
        within twenty minutes the cap is reached and the oldest — the real
        reading, waiting for paperwork — is the one that goes."""
        gateway = Gateway(samples=[])
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])
        for i in range(120):
            when = NOW + timedelta(minutes=i + 1)
            store(module, gateway, [self.nameless(when)], now=when)
        assert mod.row_lab_ids(module._held_rows) == [BARE]

    def test_the_notice_never_names_nothing(self):
        """"5 reading(s) held for  — no LabCore sample matches yet" tells an
        operator nothing they can act on and promises something that cannot
        happen."""
        held = [{LAB_ID_KEY: "", "Density": "0.86"}]
        assert "for  " not in mod.describe_held(held, {}, ())
        assert "held for" not in mod.describe_held(held, {}, ())


# ── Custody while LabCore is away ───────────────────────────────────────────

class TestParkedReadings:
    def test_they_survive_a_raise_in_the_storage_step(self, bench,
                                                      monkeypatch):
        """`_labcore_sync` swallows a raise, so anything the storage step was
        holding on a local variable at the time is deleted silently. The held
        queue was safe (it is not cleared until the commit); the parked list was
        emptied at the snapshot, two network round trips earlier.

        The raise is injected between those two points rather than at the
        gateway, because every gateway call on this road is already guarded —
        which is exactly why the surviving failure modes are the ones nobody
        thought of, and why custody must not depend on getting to the end.
        """
        gateway = Gateway(samples=[])
        module = bench(gateway)
        module._park([row(lab_id="A", Density="0.86")])
        monkeypatch.setattr(mod, "split_qc_standards", lambda *a: 1 / 0)
        with pytest.raises(ZeroDivisionError):
            store(module, gateway, [row(lab_id="B", Density="0.87")])
        assert mod.row_lab_ids(module._parked_rows) == ["A"]

    def test_what_cannot_be_kept_is_named(self, bench):
        """`_park` is the path that runs during an outage, and the message the
        operator is reading while it fills — "LabCore not reachable — data kept
        locally." — stops being true at the hundred-and-first reading."""
        module = bench(Gateway())
        messages = []
        module._park([row(lab_id=f"{i}", Density="0.86") for i in range(150)],
                     messages)
        assert len(module._parked_rows) == mod.HELD_ROW_LIMIT
        assert any("could not be kept waiting" in m for m in messages)

    def test_an_operator_action_never_reports_a_poll_as_stored(self, bench,
                                                               monkeypatch):
        """`_last_storage` is read by a poll worker between `_labcore_sync`
        returning and the payload being assembled. An operator action on the
        GUI thread taking the LabCore-is-down branch used to write "stored":
        True into it, and the worker then skipped the parking that branch
        exists for."""
        gateway = Gateway(samples=[])
        module = bench(gateway)
        monkeypatch.setitem(mod.__dict__, "labcore_is_running", lambda: False)
        module._last_storage = {"identities": {}, "filed": [], "stored": False,
                                "given_up": "", "notice": ""}
        module._labcore_sync(module._machine, [],
                             mod.MachineEvaluation(status=mod.STATUS_GREEN,
                                                   reason=""),
                             NOW, [], [], store=False)
        assert module._last_storage["stored"] is False


# ── What the question costs ─────────────────────────────────────────────────

class TestTheIdentityQuestionIsNotFree:
    """Every arm of the identity query wraps "lab_id" in lower()/ltrim() and the
    third is a leading-wildcard LIKE, so SQLite cannot use the primary key and
    reads `samples` end to end. At a twelve-second poll, a reading held for its
    full week would ask fifty thousand times."""

    def test_a_reading_parsed_this_hour_is_asked_about_every_poll(self, bench):
        """The responsive path is not negotiable: the paperwork for a cup run
        five minutes ago is being typed in right now, and the operator is
        standing at the bench watching for it."""
        gateway = Gateway(samples=[])
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])
        gateway.identity_queries.clear()
        gateway.samples = [CANONICAL]
        store(module, gateway, [], now=NOW + timedelta(seconds=12))
        assert gateway.cells() == [(CANONICAL, "Density", "0.8654")]

    def test_a_reading_waiting_since_friday_is_not_re_asked_every_poll(self,
                                                                       bench):
        """Past an hour the answer does not change in twelve seconds, and the
        tail is where all the volume is: fifty thousand reads or five thousand.
        """
        gateway = Gateway(samples=[])
        module = bench(gateway)
        module._held_rows = [row(when=NOW - timedelta(days=2), Density="0.86")]
        module._held_swept_at = NOW
        gateway.identity_queries.clear()
        for i in range(10):                       # two minutes of polling
            store(module, gateway, [], now=NOW + timedelta(seconds=12 * (i + 1)))
        assert len(gateway.identity_queries) <= 2, (
            f"{len(gateway.identity_queries)} full scans of samples in two "
            "minutes for one reading nobody is waiting on")

    def test_it_is_still_swept_often_enough_to_land(self, bench):
        gateway = Gateway(samples=[])
        module = bench(gateway)
        module._held_rows = [row(when=NOW - timedelta(days=2), Density="0.86")]
        module._held_swept_at = NOW
        gateway.samples = [CANONICAL]
        store(module, gateway, [], now=NOW + timedelta(
            seconds=mod.HELD_RECHECK_SECONDS))
        assert gateway.cells() == [(CANONICAL, "Density", "0.86")]

    def test_a_poll_that_asked_nothing_claims_to_have_learned_nothing(self,
                                                                      bench):
        """Skipping the question is not the same as getting an answer. Saying
        "LabCore is answering identity lookups again" because we did not ask is
        a guess, and this road does not make those."""
        gateway = Gateway(samples=[])
        module = bench(gateway)
        module._identity_lookup_ok = False
        module._held_swept_at = NOW
        _, messages = store(module, gateway, [], now=NOW + timedelta(seconds=12))
        assert messages == []


# ── A standard is a check, not a submitted sample ───────────────────────────

class TestAQcStandardIsNotHeldWaitingForASample:
    def machine(self):
        return Machine(uid="m1", title="Eraspec",
                       tests=[TestSpec(name="Density", value_col="Density",
                                       expected=0.86, std_dev=0.01, k=2.0,
                                       sample_id="QC1")])

    def test_a_standard_the_lab_does_not_hold_as_a_sample_is_not_held(self,
                                                                      bench):
        """Otherwise every QC print on a fresh install waits seven days for a
        sample nobody is ever going to log in, filling the queue that late
        customer readings need — and then expires under a message saying the
        reading was never matched."""
        gateway = Gateway(samples=[])
        machine = self.machine()
        module = bench(gateway, machine)
        store(module, gateway, [row(lab_id="QC1", Density="0.861")], machine)
        assert module._held_rows == []
        assert module._held_notice == ""

    def test_a_standard_the_lab_does_hold_is_still_reported_as_a_result(self,
                                                                        bench):
        gateway = Gateway(samples=["QC1"])
        machine = self.machine()
        module = bench(gateway, machine)
        store(module, gateway, [row(lab_id="QC1", Density="0.861")], machine)
        assert gateway.cells() == [("QC1", "Density", "0.861")]

    def test_a_customer_reading_on_the_same_bench_is_still_held(self, bench):
        gateway = Gateway(samples=[])
        machine = self.machine()
        module = bench(gateway, machine)
        store(module, gateway, [row(lab_id="QC1", Density="0.861"),
                                row(Density="0.8654")], machine)
        assert mod.row_lab_ids(module._held_rows) == [BARE]

    def test_a_manual_bench_never_accumulates_a_queue(self, bench):
        """Manual mode produces QC rows and nothing else. Its record is the 'qc'
        event in lem_machine_log, which is where this module reads its own
        verdicts back from (build_last_qc_query)."""
        gateway = Gateway(samples=[])
        machine = self.machine()
        machine.source_type = "manual"
        module = bench(gateway, machine)
        for i in range(30):
            store(module, gateway, [row(lab_id="QC1", Density="0.86")],
                  machine, now=NOW + timedelta(minutes=i))
        assert module._held_rows == []


# ── What the operator is told ───────────────────────────────────────────────

class TestTheOperatorLearnsWhatIsWaiting:
    def test_an_ambiguous_id_is_not_reported_as_a_missing_sample(self, bench):
        """A tie nothing can break — two zero-padded spellings of one number,
        neither carrying a date. Telling the operator no sample matches sends
        them to log a THIRD sample named "34566" to force it through, which then
        wins the exact tier outright and takes the reading."""
        gateway = Gateway(samples=["034566", "0034566"])
        module = bench(gateway)
        result, _ = store(module, gateway, [row(Density="0.8654")])
        assert gateway.ops == []
        assert "more than one" in result["notice"]
        assert "034566" in result["notice"]

    def test_the_notice_never_tells_anyone_to_rename_a_sample(self, bench):
        """The most destructive sentence this file ever printed.

        `sample_tests` has no foreign key onto `samples` and no cascade, so
        renaming a sample orphans every result already filed against it — the
        exact failure the identity road exists to remove, offered as advice to
        the one person with the rights to do it.
        """
        gateway = Gateway(samples=["034566", "0034566"])
        module = bench(gateway)
        result, _ = store(module, gateway, [row(Density="0.8654")])
        assert "more than one" in result["notice"]
        for word in ("rename", "close or", "delete", "remove one"):
            assert word not in result["notice"].lower(), result["notice"]

    def test_the_notice_is_repeated_for_as_long_as_anything_is_waiting(self,
                                                                       bench):
        """It is state, not an event. The first version said it once per change
        and it was routinely never seen, because `messages[-1]` wins the status
        line and a later "Recovered 3 QC result(s)" overwrote it."""
        gateway = Gateway(samples=[])
        module = bench(gateway)
        for _ in range(3):
            result, _ = store(module, gateway, [row(Density="0.8654")])
            assert "held" in result["notice"]

    def test_the_notice_survives_a_poll_that_parses_nothing(self, bench):
        gateway = Gateway(samples=[])
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])
        result, _ = store(module, gateway, [])
        assert "held" in result["notice"]

    def test_it_goes_quiet_the_moment_the_sample_is_logged_in(self, bench):
        gateway = Gateway(samples=[])
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])
        gateway.samples = [CANONICAL]
        result, _ = store(module, gateway, [])
        assert result["notice"] == ""
        assert gateway.cells() == [(CANONICAL, "Density", "0.8654")]

    def test_the_status_line_shows_the_notice_ahead_of_routine_news(self,
                                                                    bench):
        module = bench(Gateway())
        module._show_outcome({
            "machine": module._machine, "raw_prints": [], "rows": [], "now": NOW,
            "messages": ["Recovered 3 QC result(s) from LabCore."],
            "notice": "2 reading(s) held for 34566 — no LabCore sample matches",
            "evaluation": mod.MachineEvaluation(status=mod.STATUS_GREEN,
                                                reason=""),
            "stored": True})
        assert module._status_label.text().startswith("2 reading(s) held")

    def test_a_reading_given_up_on_is_named_not_counted(self, bench):
        gateway = Gateway(samples=[])
        module = bench(gateway)
        module._held_rows = [row(when=NOW - timedelta(days=8), Density="0.86")]
        result, _ = store(module, gateway, [])
        assert BARE in result["given_up"]
        assert "never matched" in result["given_up"]
        assert module._held_rows == []

    def test_the_last_word_on_a_reading_cannot_be_buried(self, bench):
        """Expiry is carried on the payload, not appended to `messages`.

        It was a message, and `messages[-1]` wins the status line: anything
        appended further down the same sync — "Recovered 2 QC result(s) from
        LabCore." is the routine one — overwrote the single sentence saying a
        week of waiting had ended and the reading is now the machine log's
        problem. Terminal news outranks routine news.
        """
        module = bench(Gateway())
        module._show_outcome({
            "machine": module._machine, "raw_prints": [], "rows": [], "now": NOW,
            "messages": ["Recovered 2 QC result(s) from LabCore."],
            "given_up": "1 reading(s) for 34566 were never matched to a sample",
            "notice": "",
            "evaluation": mod.MachineEvaluation(status=mod.STATUS_GREEN,
                                                reason=""),
            "stored": True})
        assert module._status_label.text().startswith("1 reading(s) for 34566")


# ── Nothing is discarded quietly ────────────────────────────────────────────
#
# Three caps can end a reading's automatic filing: the held queue's hundred
# rows, the parked list's hundred, and the identity backlog's five thousand.
# All three said so through `messages`, and `messages[-1]` wins the status line
# — so the sentence was routinely buried by "Recovered 2 QC result(s) from
# LabCore." appended further down the SAME sync. That is the exact channel
# failure `given_up` was promoted out of; these three needed the same
# promotion.

class TestALostReadingIsAlwaysSaidOutLoud:
    def payload(self, module, messages):
        return {"machine": module._machine, "raw_prints": [], "rows": [],
                "now": NOW, "messages": list(messages), "notice": "",
                "given_up": "", "stored": True,
                "evaluation": mod.MachineEvaluation(status=mod.STATUS_GREEN,
                                                    reason="")}

    def test_the_held_queue_cap_reaches_the_status_line(self, bench):
        gateway = Gateway(samples=[])
        module = bench(gateway)
        rows = [row(lab_id=str(30000 + i), Density="0.86") for i in range(120)]
        _, messages = store(module, gateway, rows)
        # What the sync does next, every poll, after the results road has run.
        messages.append("Recovered 2 QC result(s) from LabCore.")
        module._show_outcome(self.payload(module, messages))
        line = module._status_label.text()
        assert "20 reading(s)" in line, line
        assert line.index("20 reading(s)") < line.index("Recovered"), line

    def test_it_names_the_queue_it_actually_came_from(self, bench):
        """Not "the retry queue" — that is `_retry_ops`, ops LabCore refused.
        Nothing in this queue has been refused by anybody; it is waiting for a
        sample to be logged in, and saying otherwise sends whoever debugs it
        looking for a write that was never attempted."""
        gateway = Gateway(samples=[])
        module = bench(gateway)
        rows = [row(lab_id=str(30000 + i), Density="0.86") for i in range(120)]
        _, messages = store(module, gateway, rows)
        said = " ".join(messages)
        assert "retry queue" not in said, said
        assert "waiting for a sample to be logged in" in said, said

    def test_the_parked_cap_reaches_the_status_line(self, bench):
        module = bench(Gateway())
        messages = []
        module._park([row(lab_id=str(30000 + i), Density="0.86")
                      for i in range(140)], messages)
        messages.append("Recovered 2 QC result(s) from LabCore.")
        module._show_outcome(self.payload(module, messages))
        line = module._status_label.text()
        assert "40 reading(s)" in line, line
        assert line.index("40 reading(s)") < line.index("Recovered"), line

    def test_the_news_is_said_once_and_not_every_poll_after(self, bench):
        module = bench(Gateway())
        module._park([row(lab_id=str(30000 + i)) for i in range(140)], [])
        module._show_outcome(self.payload(module, []))
        assert "40 reading(s)" in module._status_label.text()
        module._show_outcome(self.payload(module, ["Ready."]))
        assert module._status_label.text() == "Ready."


# ── The queue outlives the process ──────────────────────────────────────────

class TestTheHeldQueueIsDurable:
    """A reading that has been parsed, corrected and judged but not yet filed is
    real work. Before this it lived only in this object, and LabStation
    restarting at shift change took it with no trace but the machine log."""

    def test_it_is_written_to_labcore_when_it_changes(self, bench):
        gateway = Gateway(samples=[])
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])
        held = gateway.held_writes()
        assert held, "the queue was never persisted"
        assert json.loads(held[-1][1])[0][LAB_ID_KEY] == BARE

    def test_an_idle_bench_does_not_rewrite_it_every_poll(self, bench):
        """It would cost a slot every twelve seconds in a queue that refuses
        past 100 pending."""
        gateway = Gateway(samples=[])
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])
        before = len([s for s, _a in gateway.sqls if "lem_held_results" in s])
        store(module, gateway, [])
        store(module, gateway, [])
        after = len([s for s, _a in gateway.sqls if "lem_held_results" in s])
        assert after == before

    def test_a_restart_reads_it_back(self, bench):
        """One LabCore, two module lives — a shift change, as it happens."""
        gateway = Gateway(samples=[])
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])

        gateway.samples = [CANONICAL]            # the LIMS catches up overnight
        restarted = bench(gateway)
        _, messages = store(restarted, gateway, [])
        assert gateway.cells() == [(CANONICAL, "Density", "0.8654")]
        assert any("recovered from LabCore" in m for m in messages)

    def test_a_reading_it_filed_is_not_filed_again_by_the_next_restart(self,
                                                                       bench):
        """The success path has to clear the row it succeeded from.

        Monday the bench holds a reading and mirrors it. LabStation restarts.
        Tuesday the sample is logged in and the restored reading files — and if
        the mirror still names it, every restart for the rest of the week files
        it again, over whatever the cell holds by then. An analyst correcting
        that cell to 0.9000 would watch a week-old instrument reading come back
        and overwrite it, silently, on a real sample: the orphan bug's shape
        with the identity resolved correctly.

        The queue draining back to empty is a CHANGE, not the state it started
        in, which is why `_held_persisted` begins as None and is seeded from
        what the restore actually read.
        """
        gateway = Gateway(samples=[])
        store(bench(gateway), gateway, [row(Density="0.8654")])
        assert json.loads(gateway.held_table["m1"]), "precondition: mirrored"

        gateway.samples = [CANONICAL]
        store(bench(gateway), gateway, [], now=NOW + timedelta(hours=1))
        assert gateway.cells() == [(CANONICAL, "Density", "0.8654")]
        assert json.loads(gateway.held_table["m1"]) == [], (
            "the mirror still names a reading that has been filed")

        # A third life, still inside HELD_ROW_MAX_AGE. Nothing may come back.
        _, messages = store(bench(gateway), gateway, [],
                            now=NOW + timedelta(hours=2))
        assert gateway.cells() == [(CANONICAL, "Density", "0.8654")], (
            "the reading was resurrected and filed a second time")
        assert not any("recovered" in m for m in messages)

    def test_the_mirror_is_written_before_the_batch_that_empties_it(self,
                                                                    bench):
        """Order matters more than either write does.

        Mirror first: a crash in between loses custody of a reading that is
        still in lem_machine_log. Batch first: the mirror is left naming a
        reading that HAS been filed, and the next restart files it again over
        an analyst's correction. A bench may lose a copy of its own work; it
        may not revive a value somebody has since replaced.
        """
        gateway = Gateway(samples=[])
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])
        order = []
        real_sql, real_write = gateway.sql, gateway.write

        def sql(statement, args=None, source=""):
            if "lem_held_results" in statement and statement.lstrip().upper(
                    ).startswith("INSERT"):
                order.append("mirror")
            return real_sql(statement, args, source=source)

        def write(operation, params=None, source=""):
            order.append("batch")
            return real_write(operation, params, source=source)

        gateway.sql, gateway.write = sql, write
        gateway.samples = [CANONICAL]
        store(module, gateway, [], now=NOW + timedelta(minutes=5))
        assert order[:2] == ["mirror", "batch"], order

    def test_nothing_is_mirrored_before_the_stored_queue_has_been_read(self,
                                                                       bench):
        """A fresh process holds one reading and cannot read the stored row.

        Writing its own queue over LabCore's would delete a restart's worth of
        readings on the first poll — the exact opposite of the job — so the
        mirror stays shut until the read has answered once.
        """
        gateway = Gateway(samples=[])
        gateway.held_table["m1"] = json.dumps([row(Density="0.9999")])
        module = bench(gateway)
        real = gateway.read_sql

        def refuse(sql, args=None, **kw):
            if "lem_held_results" in sql:
                return {"error": "LabCore is busy (queue full)."}
            return real(sql, args, **kw)

        gateway.read_sql = refuse
        store(module, gateway, [row(Density="0.8654")])
        assert gateway.held_writes() == []
        assert json.loads(gateway.held_table["m1"])[0]["Density"] == "0.9999"

    def test_a_growing_queue_does_not_rewrite_the_mirror_every_poll(self,
                                                                    bench):
        """The bench this feature exists for — a LIMS running behind — changes
        its queue on EVERY poll, so "only when it changes" was no bound at all:
        an eleven-kilobyte row every twelve seconds, into a queue that
        serialises about 1.5 writes a second and refuses past 100 pending."""
        gateway = Gateway(samples=[])
        module = bench(gateway)
        for i in range(20):                       # four minutes of polling
            store(module, gateway, [row(lab_id=f"3456{i}", Density="0.86")],
                  now=NOW + timedelta(seconds=12 * i))
        assert len(gateway.held_writes()) <= 5, (
            f"{len(gateway.held_writes())} mirror writes in four minutes")
        assert len(json.loads(gateway.held_table["m1"])) >= 15, (
            "and it must still be close to the truth")

    def test_a_reading_leaving_the_queue_is_mirrored_at_once(self, bench):
        """The asymmetry that makes the deferral safe: an addition may wait a
        minute, a removal never waits."""
        gateway = Gateway(samples=[])
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])
        before = len(gateway.held_writes())
        gateway.samples = [CANONICAL]
        store(module, gateway, [], now=NOW + timedelta(seconds=12))
        assert len(gateway.held_writes()) == before + 1
        assert json.loads(gateway.held_table["m1"]) == []

    def test_a_read_that_fails_is_not_an_empty_queue(self, bench):
        gateway = Gateway(samples=[])
        module = bench(gateway)
        real = gateway.read_sql

        def refuse(sql, args=None, **kw):
            if "lem_held_results" in sql:
                return {"error": "LabCore is busy (queue full)."}
            return real(sql, args, **kw)

        gateway.read_sql = refuse
        store(module, gateway, [])
        assert module._held_restored is False, (
            "a refused read must not latch the restore off for good")

    def test_a_corrupt_stored_queue_costs_the_poll_nothing(self):
        assert mod.parse_held_rows([{"held": "{not json"}]) == []
        assert mod.parse_held_rows([{"held": None}]) == []
        assert mod.parse_held_rows([]) == []

    def test_unreadable_and_empty_are_told_apart(self):
        """They were not, and they need opposite handling: one is the ordinary
        case and needs no words, the other means readings parked against a
        restart may be gone."""
        assert mod.parse_held_payload([]) == ([], True)
        assert mod.parse_held_payload([{"held": "[]"}]) == ([], True)
        assert mod.parse_held_payload([{"held": "{not json"}]) == ([], False)
        assert mod.parse_held_payload([{"held": '{"a": 1}'}]) == ([], False)

    def _corrupt_bench(self, bench):
        gateway = Gateway(samples=[])
        real = gateway.read_sql

        def with_rubbish(sql, args=None, **kw):
            if "lem_held_results" in sql and sql.lstrip().upper().startswith(
                    "SELECT"):
                return {"ok": True, "columns": ["held"],
                        "rows": [{"held": "{not json"}]}
            return real(sql, args, **kw)

        gateway.read_sql = with_rubbish
        return gateway, bench(gateway)

    def test_a_corrupt_stored_queue_is_reported(self, bench):
        """It used to be silent, which is how it stayed corrupt: `parse_held_rows`
        answers an unreadable row and an empty one with the same empty list, so
        the bench read it, discarded it, and read it again on every restart of
        the next seven days without anybody being told."""
        gateway, module = self._corrupt_bench(bench)
        _, messages = store(module, gateway, [])
        assert any("could not be read" in m for m in messages), messages

    def test_and_it_is_replaced_rather_than_left_there(self, bench):
        gateway, module = self._corrupt_bench(bench)
        store(module, gateway, [])
        assert gateway.held_table.get("m1") == "[]", (
            "the unreadable row must not survive the poll that found it")

    def test_the_mirror_never_stores_more_than_the_queue_can_hold(self, bench):
        """`_persist_held` is handed the still-held list BEFORE it is committed,
        and that list was uncapped while every other consumer was capped. One
        poll of a first-run multi-CSV bench serialised thousands of rows into a
        single LabCore row — measured at 288,000 bytes — of which all but a
        hundred were discarded microseconds later."""
        gateway = Gateway(samples=[])
        module = bench(gateway)
        store(module, gateway, [row(lab_id=str(30000 + i), Density="0.86")
                                for i in range(150)])
        for _uid, held in gateway.held_writes():
            assert len(json.loads(held)) <= mod.HELD_ROW_LIMIT, (
                f"{len(json.loads(held))} rows, {len(held)} bytes")

    def test_a_queue_sitting_at_the_cap_does_not_rewrite_the_mirror_every_poll(
            self, bench):
        """The rate floor came off entirely at the cap.

        A full queue evicts its oldest row on every poll; the mirror classed
        that as a REMOVAL, and a removal never waits. Measured: fifty mirror
        writes in fifty polls, up to 9,898 bytes each, every twelve seconds, into
        a writer that serialises about 1.5 ops a second and refuses past 100
        pending. The immediacy bought nothing either — an evicted row was never
        filed, so nothing can revive it.
        """
        gateway = Gateway(samples=[])
        module = bench(gateway)
        store(module, gateway, [row(lab_id=str(30000 + i), Density="0.86")
                                for i in range(mod.HELD_ROW_LIMIT)])
        before = len(gateway.held_writes())
        for i in range(1, 51):                    # ten minutes at the cap
            store(module, gateway,
                  [row(lab_id=f"new{i}", Density="0.86")],
                  now=NOW + timedelta(seconds=12 * i))
        writes = len(gateway.held_writes()) - before
        assert writes <= 12, f"{writes} mirror writes in ten minutes at the cap"

    def test_but_a_filed_reading_still_leaves_the_mirror_at_once(self, bench):
        """The exemption is only for the cap. A mirror still naming a reading
        that HAS been filed re-files it after the next restart, over whatever
        the cell holds by then."""
        gateway = Gateway(samples=[])
        module = bench(gateway)
        store(module, gateway, [row(lab_id=str(30000 + i), Density="0.86")
                                for i in range(mod.HELD_ROW_LIMIT)])
        store(module, gateway, [row(lab_id="new", Density="0.86")],
              now=NOW + timedelta(seconds=12))
        before = len(gateway.held_writes())
        gateway.samples = ["081126-30050"]
        store(module, gateway, [], now=NOW + timedelta(seconds=24))
        assert len(gateway.held_writes()) == before + 1
        assert "30050" not in gateway.held_table["m1"]

    def test_a_bench_that_was_off_a_fortnight_does_not_re_offer_a_fortnight(
            self, bench):
        old = json.dumps([row(when=NOW - timedelta(days=9), Density="0.86")])
        gateway = Gateway(samples=[CANONICAL])
        real = gateway.read_sql

        def with_held(sql, args=None, **kw):
            if "lem_held_results" in sql:
                return {"ok": True, "columns": ["held"], "rows": [{"held": old}]}
            return real(sql, args, **kw)

        gateway.read_sql = with_held
        module = bench(gateway)
        store(module, gateway, [])
        assert gateway.cells() == []
        assert module._held_rows == []


# ── A standard is known before the readings are judged ─────────────────────

class TestTheSpecsAreReadBeforeTheResultsRoadRuns:
    """`split_qc_standards` asks `machine.tests` which Lab IDs are this bench's
    standards, and `_labcore_sync` used to run the results road BEFORE the read
    that fills it. So on the first poll of every module life `machine.tests` was
    empty, every QC reading was classed as a customer result, and the bench held
    work the design says can never be held — telling the operator the readings
    were not matched to a sample, which for a standard is not a thing that ever
    happens."""

    SPEC = {"machine_uid": "m1", "test_name": "Density", "sample_id": "QC1",
            "expected": 0.86, "std_dev": 0.01, "k": 2.0, "units": ""}

    def sync(self, module, gateway, machine, rows):
        messages = []
        module._labcore_sync(
            machine, rows,
            mod.MachineEvaluation(status=mod.STATUS_GREEN, reason=""),
            NOW, messages, [])
        return messages

    def test_a_manual_bench_does_not_hold_its_own_first_poll(self, bench):
        """Every row on a manual bench IS a QC reading, so this was the whole
        poll — after every restart, for a bench that cannot print."""
        gateway = Gateway(samples=[], qc_specs=[self.SPEC])
        machine = Machine(uid="m1", title="Eraspec", source_type="manual")
        module = bench(gateway, machine)
        assert machine.tests == [], "the spec must not be known in advance"
        self.sync(module, gateway, machine,
                  [row(lab_id="QC1", Density="0.861")])
        assert module._held_rows == []
        assert module._held_notice == ""

    def test_and_neither_does_a_parsing_bench(self, bench):
        gateway = Gateway(samples=[], qc_specs=[self.SPEC])
        machine = Machine(uid="m1", title="Eraspec", csv_path="/tmp/in.csv",
                          mappings=[mod.MethodMapping(methods=["Density"],
                                                      qc_sample_id="QC1")])
        module = bench(gateway, machine)
        self.sync(module, gateway, machine,
                  [row(lab_id="QC1", Density="0.861"), row(Density="0.8654")])
        assert mod.row_lab_ids(module._held_rows) == [BARE], (
            "the customer reading is held; the standard's is complete")


# ── A bench with no LabCore still shows the operator the number ─────────────

class TestNoLabCoreOnTheCanvas:
    """Two branches leave before the results road: no labcore_* helpers at all,
    and `labcore_is_running()` False. The pristine code called
    `_send_to_results(rows)` on both and the value appeared on the grid. When
    painting moved behind "what was filed" they stopped painting anything, and
    said nothing about the parked readings piling up toward a silent
    hundred-row drop either — the status line read "Ready."."""

    def payload(self, module, machine, rows, messages=None):
        return {"machine": machine, "raw_prints": [], "rows": rows, "now": NOW,
                "messages": list(messages or []),
                "evaluation": mod.MachineEvaluation(status=mod.STATUS_GREEN,
                                                    reason=""),
                **module._last_storage}

    def no_labcore_poll(self, module, monkeypatch, rows):
        module._machine = Machine(uid="m1", title="Eraspec")
        for name in ("labcore_write", "labcore_sql", "labcore_read_sql"):
            monkeypatch.setitem(mod.__dict__, name, None)
        messages = []
        module._labcore_sync(module._machine, rows,
                             mod.MachineEvaluation(status=mod.STATUS_GREEN,
                                                   reason=""),
                             NOW, messages, [])
        module._show_outcome(self.payload(module, module._machine, rows,
                                          messages))

    def test_the_reading_fills_the_row_the_analyst_already_has_open(
            self, qapp, monkeypatch):
        """The printed "34566" paints into the LIMS's "081126-34566" row.

        This is the paint the pristine code did and the identity road took away.
        It is a display judgement and nothing else: LabCore has not been asked
        anything, the reading is parked, and the cell the operator reads is the
        one the canonical poll will fill for real.
        """
        module = make_module()
        results = GridResults([["Density"]])
        results._append_lab_id_row(CANONICAL)
        module.context.modules["results-1"] = results
        self.no_labcore_poll(module, monkeypatch, [row(Density="0.8654")])
        assert results.grid.item(0, 0).text() == CANONICAL
        assert results.grid.item(0, 1).text() == "0.8654", (
            "the operator at the bench cannot see the number that just printed")

    def test_it_appends_no_row_of_its_own_that_would_double_up_later(
            self, qapp, monkeypatch):
        """With no row to fill, nothing is painted — because the row it would
        append carries the PRINTED id, and the poll that files the reading paints
        it again under the canonical one. Two rows for one reading, one of them a
        Lab ID the LIMS has never heard of, and a hundred of them over a long
        outage. The reading is on this module's own table and card meanwhile."""
        module = make_module()
        results = GridResults([["Density"]])
        module.context.modules["results-1"] = results
        rows = [row(Density="0.8654")]
        self.no_labcore_poll(module, monkeypatch, rows)
        assert results.grid.rowCount() == 0

        # LabCore comes back and the reading is filed. ONE row, canonical.
        module._deliver_rows_to_results(results, rows, {BARE: CANONICAL})
        assert results.grid.rowCount() == 1
        assert results.grid.item(0, 0).text() == CANONICAL
        assert results.grid.item(0, 1).text() == "0.8654"

    def test_the_parked_readings_are_named_not_silent(self, qapp, monkeypatch):
        module = make_module()
        module._machine = Machine(uid="m1", title="Eraspec")
        for name in ("labcore_write", "labcore_sql", "labcore_read_sql"):
            monkeypatch.setitem(mod.__dict__, name, None)
        rows = [row(Density="0.8654")]
        messages = []
        module._labcore_sync(module._machine, rows,
                             mod.MachineEvaluation(status=mod.STATUS_GREEN,
                                                   reason=""),
                             NOW, messages, [])
        notice = module._last_storage["notice"]
        assert BARE in notice and "kept at the bench" in notice, notice
        module._show_outcome(self.payload(module, module._machine, rows,
                                          messages))
        assert "kept at the bench" in module._status_label.text(), (
            module._status_label.text())

    def test_a_reading_labcore_could_not_place_is_still_not_painted(self,
                                                                    qapp):
        """The distinction the None carries. An empty map means LabCore WAS
        asked and placed nothing, and a row invented for a sample the LIMS has
        never heard of reads as a delivered result."""
        module = make_module()
        results = GridResults([["Density"]])
        module._deliver_rows_to_results(results, [row(Density="0.8654")], {})
        assert results.grid.rowCount() == 0


# ── Identity matching, on its own ───────────────────────────────────────────

class TestWhoseSampleThisIs:
    def test_a_bare_cup_number_finds_the_dated_sample(self):
        assert mod.resolve_lab_id(BARE, [CANONICAL]) == CANONICAL

    def test_a_bare_sample_standing_alone_is_that_sample(self):
        """Tiered, not pooled: a lab whose samples are genuinely bare keeps its
        exact match, and it is not made ambiguous by resembling anything."""
        assert mod.resolve_lab_id(BARE, [BARE]) == BARE
        assert mod.resolve_lab_id(BARE, [BARE, "081126-9999"]) == BARE

    def test_leading_zeros_are_tolerated(self):
        assert mod.resolve_lab_id("0034566", ["34566"]) == "34566"

    def test_a_like_wildcard_in_a_lab_id_cannot_over_match(self):
        """The SQL arms are a prefilter; `%` and `_` make LIKE over-match, so
        every candidate is re-checked exactly in Python."""
        assert mod.resolve_lab_id("1_5", ["081126-125"]) is None


# ── The phantoms this software already minted ───────────────────────────────
#
# Not minting one from today on is only half the job. Every bench in this lab
# has run the pristine code, which wrote `insert_sample` under whatever the
# instrument printed on every poll — so `samples` holds a bare "34566" beside
# the LIMS's "081126-34566" for every cup this software has ever processed.
# Tier order alone hands the reading straight back to the phantom, the LIMS's
# own cell stays blank, and the fix is correct and inert on the installed base.

class TestTheSamplesThisSoftwareAlreadyMinted:
    def test_the_dated_sample_wins_over_our_own_phantom(self):
        assert mod.resolve_lab_id(BARE, [BARE, CANONICAL]) == CANONICAL

    def test_leading_zeros_do_not_hide_a_phantom(self):
        assert mod.resolve_lab_id("034566", ["034566", CANONICAL]) == CANONICAL

    def test_only_a_readable_date_may_displace_an_exact_match(self):
        """The phantom's twin is the LIMS's own "mmddyy-labid" and nothing
        else. A sample called "BATCH-34566" is not evidence that the sample
        literally named 34566 is our forgery, and taking the reading off it
        would be a wrong-sample write invented to fix one."""
        assert mod.resolve_lab_id(BARE, [BARE, "BATCH-34566"]) == BARE
        assert mod.resolve_lab_id(BARE, ["BATCH-34566"]) == "BATCH-34566"

    def test_nothing_is_deleted_or_renamed_to_achieve_it(self, bench):
        """The phantom is left exactly where it is. Renaming it would orphan
        every result already filed against it — `sample_tests` has no foreign
        key onto `samples` and no cascade — and deleting it is not this
        module's business either. Only the new reading moves."""
        gateway = Gateway(samples=[BARE, CANONICAL])
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])
        assert gateway.cells() == [(CANONICAL, "Density", "0.8654")]
        assert not [op for op in gateway.ops
                    if op.get("operation") != "update_cell"]

    def test_a_dated_match_never_takes_a_qc_standard(self, bench):
        """A standard's Lab ID is a name somebody gave a bottle, not a number
        from the sample sequence. "1234" ending "081126-1234" is a coincidence,
        and acting on it writes a control check onto a customer's result."""
        assert mod.resolve_lab_id("1234", ["081126-1234"],
                                  standard=True) is None
        assert mod.resolve_lab_id("1234", ["1234", "081126-1234"],
                                  standard=True) == "1234"
        found, unsure = mod.resolve_lab_ids(
            ["1234"], ["081126-1234"], standards={"1234"})
        assert (found, unsure) == ({}, {})

    def test_the_standard_is_not_held_for_the_sample_it_did_not_match(self,
                                                                      bench):
        machine = Machine(uid="m1", title="Eraspec",
                          tests=[TestSpec(name="Density", value_col="Density",
                                          expected=0.86, std_dev=0.01, k=2.0,
                                          sample_id="1234")])
        gateway = Gateway(samples=["081126-1234"])
        module = bench(gateway, machine)
        store(module, gateway, [row(lab_id="1234", Density="0.8654")])
        assert gateway.cells() == []
        assert module._held_rows == []


# ── The collision that cannot happen, and what happens when it does ─────────
#
# Ryan, asked how to tell two dated samples carrying one printed cup number
# apart: "This can never happen because its linear from 0 to indef. But if it
# does choose the closer date."
#
# So the numeric Lab ID is one monotonic, never-reused sequence over the life of
# the lab — 34566 is issued exactly once, and "081126-34566" and "081026-34566"
# cannot both exist. These tests guard the DEFENSIVE path: not lab traffic, but
# what the bench does when the data is wrong anyway. It files, on the nearest
# date. It holds only when the dates genuinely cannot decide.

class TestACupNumberIsIssuedOnce:
    def test_the_nearer_date_takes_the_reading(self):
        assert mod.resolve_lab_id(BARE, [CANONICAL, "081026-34566"],
                                  when=NOW) == CANONICAL

    def test_and_it_is_the_print_s_own_date_that_decides(self):
        """Not "today", not the newest sample — the date on the print. A
        backlog imported a week late must land where it was run."""
        yesterday = NOW - timedelta(days=1)
        assert mod.resolve_lab_id(BARE, [CANONICAL, "081026-34566"],
                                  when=yesterday) == "081026-34566"

    def test_an_afternoon_print_is_not_nearer_to_tomorrow(self):
        """Compared by date, not by clock. Measured from the timestamp, a print
        taken at two in the afternoon is ten hours from tomorrow's midnight and
        fourteen from today's — so the afternoon's readings would file onto the
        NEXT day's sample, by arithmetic, silently."""
        afternoon = datetime(2026, 8, 11, 14, 30)
        assert mod.resolve_lab_id(BARE, [CANONICAL, "081226-34566"],
                                  when=afternoon) == CANONICAL

    def test_a_reading_is_never_held_for_a_collision_that_a_date_settles(
            self, bench):
        gateway = Gateway(samples=[CANONICAL, "081026-34566"])
        module = bench(gateway)
        store(module, gateway, [row(Density="0.8654")])
        assert gateway.cells() == [(CANONICAL, "Density", "0.8654")]
        assert module._held_rows == []

    def test_a_tie_the_dates_cannot_break_is_still_held(self):
        """Two spellings of one number, neither dated. There is nothing left to
        choose with, and a coin toss files a real result on the wrong sample."""
        assert mod.resolve_lab_id(BARE, ["034566", "0034566"],
                                  when=NOW) is None

    def test_so_is_a_collision_with_no_print_date_to_measure_from(self):
        assert mod.resolve_lab_id(BARE, [CANONICAL, "081026-34566"]) is None

    def test_the_undecidable_candidates_are_reported_not_swallowed(self):
        found, unsure = mod.resolve_lab_ids([BARE], ["034566", "0034566"],
                                            {BARE: NOW})
        assert found == {}
        assert unsure[BARE] == ["0034566", "034566"]

    def test_a_dated_prefix_is_read_only_when_it_really_is_a_date(self):
        assert mod.sample_id_date(CANONICAL) == datetime(2026, 8, 11)
        assert mod.sample_id_date("34566") is None      # a number, not a date
        assert mod.sample_id_date("034566") is None     # nor is this
        assert mod.sample_id_date("991399-34566") is None   # no such month
        assert mod.sample_id_date("") is None

    def test_a_candidate_with_no_readable_date_cannot_win_by_default(self):
        """It has no distance, so it cannot be the nearest — but it must not
        block the one that does have a date either."""
        assert mod.closest_by_date([CANONICAL, "x-34566"], NOW) == CANONICAL
        assert mod.closest_by_date(["x-34566", "y-34566"], NOW) is None

    # ── The tie guard proper ─────────────────────────────────────────────
    #
    # `closest_by_date` ends `return None if tied else best`, and that one
    # clause is the whole of Ryan's ruling on the case his ruling does not
    # cover: two samples the nearest-date rule cannot separate. Every test
    # above that CLAIMED to cover a tie used candidates with no readable date,
    # so they left through `best is None` and never set `tied` at all —
    # replacing the line with a bare `return best` left the entire suite green.
    #
    # A real tie takes two samples stamped the SAME DAY carrying one cup
    # number, which is what a login typed twice — once with a leading zero —
    # produces. The lab says it cannot happen. This is what the bench does when
    # it does.

    def test_two_samples_stamped_the_same_day_are_a_tie(self):
        """Both dates readable, both zero days from the print. Nothing is left
        to choose with, so nothing is chosen."""
        same_day = [CANONICAL, "081126-034566"]
        assert mod.sample_id_date(same_day[0]) == datetime(2026, 8, 11)
        assert mod.sample_id_date(same_day[1]) == datetime(2026, 8, 11)
        assert mod.closest_by_date(same_day, NOW) is None

    def test_a_same_day_tie_is_not_resolved_to_either_sample(self):
        """Through the resolver, where the wrong-sample write would happen."""
        assert mod.resolve_lab_id(BARE, [CANONICAL, "081126-034566"],
                                  when=NOW) is None
        found, unsure = mod.resolve_lab_ids([BARE],
                                            [CANONICAL, "081126-034566"],
                                            {BARE: NOW})
        assert found == {}
        assert unsure[BARE] == ["081126-034566", CANONICAL]

    def test_a_same_day_tie_is_held_and_named(self, bench):
        """End to end: nothing written, both readings kept, and the operator
        told which two samples answer to the number."""
        gateway = Gateway(samples=[CANONICAL, "081126-034566"])
        module = bench(gateway)
        result, _ = store(module, gateway, [row(Density="0.8654")])
        assert gateway.cells() == []
        assert len(module._held_rows) == 1
        assert "more than one" in result["notice"], result["notice"]
        assert "081126-034566" in result["notice"]

    def test_the_day_after_still_breaks_it(self):
        """The guard must fire on a TIE and on nothing else: one day between
        the two stamps is a distance, and a distance decides."""
        assert mod.closest_by_date([CANONICAL, "081026-034566"],
                                   NOW) == CANONICAL

    def test_two_prints_of_one_number_on_two_days_settle_nothing(self, bench):
        """The double defect: one number on two samples AND on two days' prints.

        The date map used to keep the LATEST print per printed ID, so both
        readings resolved against the newer print and the older one was filed on
        a sample it was not taken for — a silent wrong-sample write on the one
        road that exists to prevent them. When the prints disagree there is
        nothing to measure from, so nothing is measured and both are held.
        """
        gateway = Gateway(samples=[CANONICAL, "081026-34566"])
        module = bench(gateway)
        result, _ = store(module, gateway, [
            row(when=NOW - timedelta(days=1), Density="0.8600"),
            row(when=NOW, Density="0.8654")])
        assert gateway.cells() == []
        assert len(module._held_rows) == 2
        assert "more than one" in result["notice"], result["notice"]

    def test_but_two_prints_on_one_day_still_file(self, bench):
        """The guard costs the ordinary case nothing — and a date is only ever
        consulted when more than one sample answers to the number at all."""
        gateway = Gateway(samples=[CANONICAL, "081026-34566"])
        module = bench(gateway)
        store(module, gateway, [row(when=NOW, Density="0.8654"),
                                row(when=NOW - timedelta(hours=2),
                                    Viscosity="3.2")])
        assert sorted(gateway.cells()) == [
            (CANONICAL, "Density", "0.8654"), (CANONICAL, "Viscosity", "3.2")]
        assert module._held_rows == []
