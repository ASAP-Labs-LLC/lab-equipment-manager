"""What a bench costs the lab's queue, and what it promises about the record.

Two subjects that turn out to be one. LabCore serialises every operation and
`read_sql` goes through the same queue, so everything a bench asks for delays
everybody else — Ryan: *"the LEM heartbeats are bogging down the server"*, at
ten benches with "a lot more to be added still". And the answer to almost every
cap on the results road is "the reading stays in the machine log", which is a
promise about a queue of its own.

The promise was false. `_pending_events` was `deque(maxlen=200)`,
`_queue_run_events` appends one record per parsed print, and the drain to
lem_machine_log ran AFTER the results road — so a poll parsing more than two
hundred prints silently evicted the oldest records before anything wrote them,
and a `held_expired` event announcing a give-up could destroy the very record it
pointed at. Measured on the real module: one 3,000-print poll with the LIMS
behind left 200 readings in no store at all, while the status line said they
were in the machine log.

The heartbeat's own economy lives in `test_write_economy.py`, next door.
"""
from datetime import datetime, timedelta

import pytest

import lem_station_module as mod
from lem_station_module import LAB_ID_KEY, Machine, TestSpec

from test_module_qt import make_module
from test_result_identity_road import Gateway, _sql_suffix, row, store

NOW = datetime(2026, 8, 11, 12, 0, 0)
BARE = "34566"
CANONICAL = "081126-34566"


class Recorder:
    """A gateway that keeps ONE ordered list of everything it was asked to do,
    so a test can ask which happened first."""

    def __init__(self, samples=(), fail_on=None):
        self.samples = list(samples)
        self.order = []          # ("log", lab_id) | ("sql", sql) | ("batch", n)
        self.ops = 0             # what the LAB's serialised queue actually pays
        self.fail_on = fail_on   # a substring whose write raises, once
        self.failed = False

    def sql(self, sql, args=None, source=""):
        if (self.fail_on and not self.failed
                and self.fail_on in str(args or "")):
            self.failed = True
            raise RuntimeError("queue full")
        if "lem_machine_log" in sql and sql.lstrip().upper().startswith(
                "INSERT"):
            # One statement carries many records now — seven columns each, laid
            # end to end by `build_log_batch`. The fake unpacks them so a test
            # still counts RECORDS, which is what the durability claim is about,
            # while `ops()` counts what the queue actually pays for.
            self.ops += 1
            for i in range(0, len(args), 7):
                self.order.append(("log", args[i + 2], args[i + 3]))
        else:
            self.ops += 1
            self.order.append(("sql", sql))
        return {"ok": True}

    def read_sql(self, sql, args=None, **kw):
        if 'FROM "samples"' in sql:
            keys = {str(a).lower() for a in (args or [])}
            return {"ok": True, "columns": ["lab_id"],
                    "rows": [{"lab_id": s} for s in self.samples
                             if s.lower() in keys
                             or s.lower().lstrip("0") in keys
                             or _sql_suffix(s) in keys]}
        return {"error": "no such table"}

    def write(self, operation, params=None, source=""):
        self.order.append(("batch", len((params or {}).get("operations", []))))
        return {"ok": True}

    def logs(self):
        return [entry for entry in self.order if entry[0] == "log"]


@pytest.fixture
def bench(qapp, monkeypatch):
    def build(gateway, machine=None):
        monkeypatch.setitem(mod.__dict__, "labcore_write", gateway.write)
        monkeypatch.setitem(mod.__dict__, "labcore_sql", gateway.sql)
        monkeypatch.setitem(mod.__dict__, "labcore_read_sql", gateway.read_sql)
        monkeypatch.setattr(mod, "_in_thread", lambda fn, cb: cb(fn()))
        module = make_module()
        module._machine = machine or Machine(uid="m1", title="Eraspec")
        return module
    return build


# ── (A) The machine log is the promise every other cap makes ────────────────

class TestNoRecordIsEvictedInSilence:
    def test_a_poll_larger_than_the_old_queue_loses_no_record(self, bench):
        """Three thousand prints in one poll — a first run of a multi-CSV bench
        over an archive folder, which is the ordinary way this bench starts.

        The old queue held two hundred. `_ingest_multi` has already moved the
        source file into processed/ by the time this runs, so nothing re-reads
        it: a record evicted here was gone for good.
        """
        gateway = Recorder()
        module = bench(gateway)
        rows = [row(lab_id=str(50000 + i), Density="0.86") for i in range(3000)]
        module._queue_run_events(module._machine, rows, NOW)
        module._drain_events(gateway.sql, [])
        assert len(gateway.logs()) == 3000
        assert module._events_dropped == 0

    def test_the_record_goes_out_before_the_road_that_can_give_up_on_it(
            self, bench):
        """The ordering IS the durability claim. Every drop notice ends "they
        stay in the machine log"; drained afterwards, the record was still
        sitting in a queue when the cap discarded it."""
        gateway = Recorder(samples=[CANONICAL])
        module = bench(gateway)
        rows = [row(Density="0.8654")]
        module._queue_run_events(module._machine, rows, NOW)
        module._labcore_sync(module._machine, rows,
                             mod.MachineEvaluation(status="GREEN", reason=""),
                             NOW, [], [])
        kinds = [entry[0] for entry in gateway.order]
        assert "log" in kinds and "batch" in kinds
        assert kinds.index("log") < kinds.index("batch"), gateway.order

    def test_the_queue_refuses_a_new_record_rather_than_evicting_an_old_one(
            self, bench):
        """Dropping the oldest is right everywhere else on this road and wrong
        here: this queue is not readings waiting for something, it is the
        record every other cap points at."""
        gateway = Recorder()
        module = bench(gateway)
        module._log_event("run", lab_id="FIRST", now=NOW)
        for i in range(mod.LOG_EVENT_LIMIT + 5):
            module._log_event("run", lab_id=str(i), now=NOW)
        assert len(module._pending_events) == mod.LOG_EVENT_LIMIT
        assert module._events_dropped == 6
        module._drain_events(gateway.sql, [])
        assert gateway.logs()[0][2] == "FIRST", "an accepted record was traded"

    def test_a_refusal_is_reported_and_never_claims_the_machine_log(
            self, bench):
        gateway = Recorder()
        module = bench(gateway)
        module._events_dropped = 7
        messages = []
        module._drain_events(gateway.sql, messages)
        said = " ".join(messages)
        assert "7 machine-log record(s)" in said
        assert "NOT in the machine log" in said
        assert module._take_losses(), "the status line never heard about it"
        assert module._events_dropped == 0, "reported twice"

    def test_a_record_lost_to_a_dropped_connection_is_kept_for_next_poll(
            self, bench):
        """A record popped off and lost to a raise is the same silent loss the
        bound exists to stop, arrived at the other way round."""
        gateway = Recorder(fail_on="SECOND")
        module = bench(gateway)
        module._log_event("run", lab_id="FIRST", now=NOW)
        module._log_event("run", lab_id="SECOND", now=NOW)
        module._log_event("run", lab_id="THIRD", now=NOW)
        with pytest.raises(RuntimeError):
            module._drain_events(gateway.sql, [])
        # ALL THREE go back, not two. The three share one batched INSERT, and a
        # statement that raised either ran or did not — so the whole batch is
        # offered again rather than the module guessing which rows of it landed.
        # A duplicate record would be the other failure and is not possible here.
        assert len(module._pending_events) == 3
        module._drain_events(gateway.sql, [])
        assert [entry[2] for entry in gateway.logs()] == ["FIRST", "SECOND",
                                                          "THIRD"]

    def test_the_sync_swallows_that_raise_and_says_so(self, bench):
        """The worker must never raise — a raise strands `_polling` forever."""
        gateway = Recorder(fail_on="FIRST")
        module = bench(gateway)
        module._log_event("run", lab_id="FIRST", now=NOW)
        messages = []
        module._labcore_sync(module._machine, [],
                             mod.MachineEvaluation(status="GREEN", reason=""),
                             NOW, messages, [])
        assert any("LabCore sync error" in m for m in messages), messages
        assert len(module._pending_events) == 1


# ── (D) A resolved Lab ID is resolved for good ──────────────────────────────
#
# Ryan: the numeric Lab ID is "linear from 0 to indef" and never reused. So once
# a printed ID has been shown to be exactly one sample, that mapping cannot
# change for the life of the lab — and the identity read, which sits on the
# critical path of every poll that produces rows and POSTs to /api/queue/write
# like every write does, was re-asking it every twelve seconds on every bench.

class TestAResolvedIdIsNeverAskedAboutAgain:
    def steady(self, module, gateway, printed=BARE, when=NOW):
        return store(module, gateway, [row(lab_id=printed, Density="0.86")],
                     now=when)

    def test_a_steady_bench_issues_no_identity_read_at_all(self, bench):
        gateway = Gateway(samples=[CANONICAL])
        module = bench(gateway)
        self.steady(module, gateway)
        assert len(gateway.identity_queries) == 1
        for poll in range(1, 20):
            self.steady(module, gateway,
                        when=NOW + timedelta(seconds=12 * poll))
        assert len(gateway.identity_queries) == 1, gateway.identity_queries

    def test_and_files_the_same_reading_to_the_same_sample(self, bench):
        gateway = Gateway(samples=[CANONICAL])
        module = bench(gateway)
        self.steady(module, gateway)
        store(module, gateway, [row(Density="0.8700")],
              now=NOW + timedelta(seconds=12))
        assert gateway.cells() == [(CANONICAL, "Density", "0.86"),
                                   (CANONICAL, "Density", "0.8700")]

    def test_an_id_nobody_has_placed_yet_still_costs_a_read(self, bench):
        gateway = Gateway(samples=[CANONICAL])
        module = bench(gateway)
        self.steady(module, gateway)
        store(module, gateway, [row(lab_id="99999", Density="0.86")],
              now=NOW + timedelta(seconds=12))
        assert len(gateway.identity_queries) == 2

    def test_a_failure_is_never_remembered(self, bench):
        """A sample the LIMS has not logged in yet is the whole point of the
        held queue. Cached, the reading would wait its seven days without the
        bench ever asking again."""
        gateway = Gateway(samples=[])
        module = bench(gateway)
        self.steady(module, gateway)
        self.steady(module, gateway, when=NOW + timedelta(seconds=12))
        assert len(gateway.identity_queries) == 2
        gateway.samples.append(CANONICAL)
        self.steady(module, gateway, when=NOW + timedelta(seconds=24))
        assert (CANONICAL, "Density", "0.86") in gateway.cells()

    def test_a_read_that_could_not_be_asked_is_never_remembered(self, bench):
        gateway = Gateway(samples=[CANONICAL],
                          samples_answer={"error": "LabCore is busy."})
        module = bench(gateway)
        self.steady(module, gateway)
        self.steady(module, gateway, when=NOW + timedelta(seconds=12))
        assert len(gateway.identity_queries) == 2

    def test_an_answer_a_date_decided_is_never_remembered(self, bench):
        """It was reached by measuring against ONE reading's parse time, so it
        belongs to that reading and not to the lab. Remembering it would turn a
        data defect into a permanent wrong answer."""
        found, unsure, certain = mod.resolve_lab_ids_certain(
            [BARE], [CANONICAL, "081026-34566"], {BARE: NOW})
        assert found == {BARE: CANONICAL}
        assert certain == set(), "a tiebreak was called certain"
        gateway = Gateway(samples=[CANONICAL, "081026-34566"])
        module = bench(gateway)
        self.steady(module, gateway)
        self.steady(module, gateway, when=NOW + timedelta(seconds=12))
        assert len(gateway.identity_queries) == 2

    def test_the_standard_flag_is_part_of_the_key(self, bench):
        """A QC standard's Lab ID resolves under a narrower rule than a
        customer sample's, so the same printed ID has two possible answers. An
        ID that becomes a standard when the floor assigns QC must be re-asked
        under the rule that now applies."""
        gateway = Gateway(samples=["081126-1234"])
        module = bench(gateway)
        store(module, gateway, [row(lab_id="1234", Density="0.86")], now=NOW)
        assert gateway.cells() == [("081126-1234", "Density", "0.86")]
        module._machine.tests = [TestSpec(name="Density", value_col="Density",
                                          expected=0.86, std_dev=0.01, k=2.0,
                                          sample_id="1234")]
        store(module, gateway, [row(lab_id="1234", Density="0.87")],
              now=NOW + timedelta(seconds=12))
        assert len(gateway.identity_queries) == 2
        # And under the narrower rule it resolves to nothing, so nothing more
        # is written: the verdict is already a 'qc' event in the machine log.
        assert gateway.cells() == [("081126-1234", "Density", "0.86")]

    def test_the_cache_is_bounded_and_a_miss_only_costs_a_read(self, bench):
        gateway = Gateway(samples=[])
        module = bench(gateway)
        for i in range(mod.IDENTITY_CACHE_LIMIT + 50):
            module._remember_identities({str(i): f"081126-{i}"},
                                        {str(i)}, set())
        assert len(module._identity_cache) == mod.IDENTITY_CACHE_LIMIT
        assert module._cached_identity("0", False) == "", "oldest not evicted"
        assert module._cached_identity("60", False) == "081126-60"

    def test_the_bound_evicts_the_least_recently_USED(self, bench):
        """A bench's QC standards are printed on every poll and must stay
        resolved however long it runs.

        The standard is cached AS a standard, which is the only way its bare
        name is remembered at all: an undated answer for an ordinary sample is
        our own phantom and may still be displaced by the LIMS's dated record,
        so it is deliberately never kept. See IDENTITY_CACHE_LIMIT.
        """
        gateway = Gateway(samples=[])
        module = bench(gateway)
        module._remember_identities({"CP": "CP-STD"}, {"CP"}, {"cp"})
        for i in range(mod.IDENTITY_CACHE_LIMIT):
            module._cached_identity("CP", True)      # used on every poll
            module._remember_identities({str(i): f"081126-{i}"},
                                        {str(i)}, set())
        assert module._cached_identity("CP", True) == "CP-STD"


# ── (E) The small ones, all of which were losing or hiding a reading ────────

class TestTheDedupesAreNotQuadratic:
    """Both ran `if key not in out` against a LIST, on the worker with
    `_polling` held. Measured 0.194s per poll at the 5,000-row backlog — a
    fifth of a twelve-second poll spent on a question a hash answers."""

    def rows(self, n):
        return [row(lab_id=str(100000 + i)) for i in range(n)]

    def test_twenty_thousand_rows_do_not_cost_a_second(self):
        import time
        rows = self.rows(20000)
        start = time.perf_counter()
        ids = mod.row_lab_ids(rows)
        mod.split_identity_backlog(ids)
        mod.build_sample_identity_queries(ids[:1000])
        spent = time.perf_counter() - start
        assert len(ids) == 20000
        # The list form is ~20s at this size; linear is a few milliseconds.
        assert spent < 1.0, f"{spent:.3f}s — this is quadratic again"

    def test_order_and_uniqueness_are_unchanged(self):
        rows = [row(lab_id="b"), row(lab_id="a"), row(lab_id="b"),
                row(lab_id=""), row(lab_id="c")]
        assert mod.row_lab_ids(rows) == ["b", "a", "c"]
        assert mod.split_identity_backlog(["b", "a", "b", "", "c"]) == (
            ["b", "a", "c"], [])


class TestADroppedReadingIsNeverPaintedAsDelivered:
    def test_the_parked_cap_does_not_report_what_it_threw_away(self, qapp,
                                                              monkeypatch):
        """`_parked_storage` was handed the UNCAPPED list, so readings `_park`
        had just discarded were painted onto the Results grid — the exact
        "reported delivered while dropped" failure the rest of this change
        exists to remove."""
        for name in ("labcore_write", "labcore_sql", "labcore_read_sql"):
            monkeypatch.delitem(mod.__dict__, name, raising=False)
        module = make_module()
        module._machine = Machine(uid="m1", title="Eraspec")
        rows = [row(lab_id=str(i), Density="0.86")
                for i in range(mod.HELD_ROW_LIMIT + 50)]
        module._labcore_sync(module._machine, rows,
                             mod.MachineEvaluation(status="GREEN", reason=""),
                             NOW, [], [])
        filed = module._last_storage["filed"]
        assert len(filed) == mod.HELD_ROW_LIMIT
        assert filed == rows[50:]
        assert module._take_losses(), "the drop was not reported either"

    def test_park_returns_what_it_kept(self, qapp):
        module = make_module()
        module._machine = Machine(uid="m1", title="Eraspec")
        rows = [row(lab_id=str(i)) for i in range(mod.HELD_ROW_LIMIT + 3)]
        kept = module._park(rows)
        assert kept == rows[3:]
        assert module._park([]) == []


class TestASyncThatRaisedStillShowsTheReading:
    def test_the_main_thread_paints_and_says_where_they_went(self, qapp):
        """`payload["filed"]` was still the initial empty list, so a sync that
        raised anywhere before the results road parked the readings correctly
        and then painted nothing and said nothing — the one path where a print
        arrived at the bench and left no sign anywhere."""
        module = make_module()
        machine = Machine(uid="m1", title="Eraspec")
        module._machine = machine
        rows = [row(Density="0.8654")]
        payload = {"machine": machine, "now": NOW, "rows": rows,
                   "raw_prints": [], "messages": [], "stored": False,
                   "identities": {}, "filed": [], "given_up": "", "notice": "",
                   "evaluation": mod.MachineEvaluation(status="GREEN",
                                                       reason="")}
        module._show_outcome(payload)
        assert payload["filed"] == rows
        assert payload["identities"] is None, "painted under the printed ID"
        assert module._parked_rows == rows
        assert "kept at the bench" in module._status_label.text()


class TestTheStatusLineStaysReadable:
    """`_losses` concatenated every notice into one label with ' · ', so the
    poll that lost the most readings was the poll whose notices ran off the end
    of the widget and became unreadable."""

    def test_a_few_notices_are_shown_whole(self):
        assert mod._loss_line(["a", "b"]) == "a · b"

    def test_many_are_condensed_worst_first_and_counted(self):
        line = mod._loss_line([f"n{i}" for i in range(9)])
        assert line.startswith("n0 · n1 · n2 · (+6 more")
        assert "n8" not in line

    def test_nothing_is_condensed_away(self, qapp):
        module = make_module()
        machine = Machine(uid="m1", title="Eraspec")
        module._machine = machine
        for i in range(9):
            module._losses.append(f"notice {i}")
        payload = {"machine": machine, "now": NOW, "rows": [],
                   "raw_prints": [], "messages": [], "stored": True,
                   "identities": {}, "filed": [], "given_up": "", "notice": "",
                   "evaluation": mod.MachineEvaluation(status="GREEN",
                                                       reason="")}
        module._show_outcome(payload)
        assert "+6 more" in module._status_label.text()
        assert "notice 8" in module._status_label.toolTip()

    def test_the_deque_survives_a_run_of_polls_that_never_reached_the_ui(
            self, qapp):
        """It is drained on every `_show_outcome`, so a poll's own notices
        never fill it — what filled it was a run of polls whose worker raised
        before the payload reached the main thread. At twenty, the poll that
        lost the most readings was the poll whose notices were evicted."""
        module = make_module()
        module._machine = Machine(uid="m1", title="Eraspec")
        for i in range(120):
            module._report_loss(f"notice {i}")
        drained = module._take_losses()
        assert drained[0] == "notice 0", "the earliest notice was evicted"
        assert len(drained) == 120


# ── A REFUSAL IS NOT A WRITE ────────────────────────────────────────────────
#
# The blocker the first cut of this file missed entirely, because its own
# gateway only ever RAISED. LabCore does not refuse by raising: it serialises
# its queue at ~1.5 ops/sec and turns new work away past ~100 pending with
# `{"error": ..., "busy": true, "retry_after": n}`, returned normally
# (notes.md, "Writes are the opposite story" — the bug that once reported
# "imported 3094" while nothing landed). Measured on the real module before the
# fix: a 3,000-print poll stored 100 records, discarded 2,900, reported
# `_events_dropped == 0` and told the operator they were in the machine log.
#
# Every test below models the gateway LabCore actually is.


class BusyLabCore:
    """Accepts `capacity` operations and refuses the rest the way LabCore
    refuses — an error dict, returned, never raised."""

    def __init__(self, capacity=100, samples=()):
        self.capacity = capacity
        self.samples = list(samples)
        self.accepted = 0
        self.refused = 0
        self.records = 0          # lem_machine_log ROWS that really landed

    def sql(self, sql, args=None, source=""):
        if self.accepted >= self.capacity:
            self.refused += 1
            return {"error": "LabCore is busy", "busy": True, "retry_after": 5}
        self.accepted += 1
        if "lem_machine_log" in sql and sql.lstrip().upper().startswith(
                "INSERT"):
            self.records += len(args or []) // 7
        return {"ok": True}

    def read_sql(self, sql, args=None, **kw):
        return {"error": "no such table"}

    def write(self, operation, params=None, source=""):
        return {"ok": True}


class TestARefusalIsNeverCountedAsAWrite:
    def test_a_refused_batch_is_kept_and_said_not_silently_dropped(
            self, bench):
        """The blocker. Refused records stay in the queue for the next poll,
        nothing is counted as lost, and the operator is told the road is shut
        rather than told the readings are filed."""
        gateway = BusyLabCore(capacity=10)     # 1,000 records, then full
        module = bench(gateway)
        rows = [row(lab_id=str(50000 + i), Density="0.86") for i in range(3000)]
        module._queue_run_events(module._machine, rows, NOW)
        messages = []
        module._drain_events(gateway.sql, messages)
        assert gateway.records == 1000
        assert len(module._pending_events) == 2000, "refused records vanished"
        assert module._events_dropped == 0, "a refusal is not a loss"
        assert any("refused" in m for m in messages), messages

    def test_the_give_up_notice_stops_promising_the_machine_log(self, bench):
        """Every cap on this road ends "they stay in the machine log". While
        the drain is being refused that sentence is false, so it is not said."""
        gateway = BusyLabCore(capacity=0)
        module = bench(gateway)
        module._log_event("run", lab_id="X", now=NOW)
        module._drain_events(gateway.sql, [])
        assert not module._log_road_open
        assert "NOT reached LabCore" in module._log_home()

    def test_the_records_go_out_once_labcore_recovers(self, bench):
        """Refused is not lost: the whole 3,000 land on a later poll."""
        gateway = BusyLabCore(capacity=10)
        module = bench(gateway)
        rows = [row(lab_id=str(50000 + i), Density="0.86") for i in range(3000)]
        module._queue_run_events(module._machine, rows, NOW)
        module._drain_events(gateway.sql, [])
        gateway.capacity = 1000                # the queue drains at the server
        module._drain_events(gateway.sql, [])
        assert gateway.records == 3000
        assert not module._pending_events
        assert module._log_road_open, "the promise is true again"

    def test_a_refused_declaration_does_not_mark_the_tables_as_declared(
            self, bench):
        """Three roads share `_labcore_table_ready`. A DDL refused on a fresh
        LabCore whose queue is busy at boot used to poison all of them for the
        life of the process, and every later write went to a table that was
        never created."""
        gateway = BusyLabCore(capacity=0)
        module = bench(gateway)
        module._declare_tables(gateway.sql)
        assert not module._labcore_table_ready
        gateway.capacity = 100
        module._declare_tables(gateway.sql)
        assert module._labcore_table_ready

    def test_a_refused_beat_does_not_close_the_heartbeat_window(self, bench):
        """One gate governs the poll and the pulse now, so counting a refused
        beat as sent costs the floor a whole window on a bench that is fine —
        twice the pristine code's worst case."""
        gateway = BusyLabCore(capacity=0)
        module = bench(gateway)
        module._labcore_sync(module._machine, [],
                             mod.MachineEvaluation(status="GREEN", reason=""),
                             NOW, [], [])
        assert module._last_heartbeat is None, "a refusal counted as a beat"
        assert module._heartbeat_due(NOW), "the pulse would skip too"


class TestTheQueueCostOfOnePoll:
    """Ryan: "the LEM heartbeats are bogging down the server", at ten benches
    and "a lot more to be added still". Every record written one INSERT at a
    time is one slot in a queue the whole lab shares."""

    def test_an_archive_import_costs_ops_in_the_dozens_not_the_thousands(
            self, bench):
        """3,000 prints. One op per record was 3,000 serialised operations in
        front of every other bench — and, past 100 pending, guaranteed the
        refusal above. notes.md rule (c): batch bulk rows into multi-row
        INSERTs."""
        gateway = Recorder()
        module = bench(gateway)
        rows = [row(lab_id=str(50000 + i), Density="0.86") for i in range(3000)]
        module._queue_run_events(module._machine, rows, NOW)
        module._drain_events(gateway.sql, [])
        assert len(gateway.logs()) == 3000, "a record was lost to batching"
        assert gateway.ops == 30, gateway.ops

    def test_the_operator_s_own_click_never_drains_on_the_gui_thread(
            self, bench):
        """`_reevaluate_and_show` runs `_labcore_sync(store=False)` inline on
        the canvas thread and skips the results road for that reason. The drain
        is unbounded by comparison, so it belongs to the worker too."""
        gateway = Recorder()
        module = bench(gateway)
        for i in range(500):
            module._log_event("run", lab_id=str(i), now=NOW)
        module._labcore_sync(module._machine, [],
                             mod.MachineEvaluation(status="GREEN", reason=""),
                             NOW, [], [], store=False)
        assert not gateway.logs(), "the GUI thread drained the queue"
        assert len(module._pending_events) == 500


class TestTheCacheNeverPinsOurOwnPhantom:
    """The premise of the cache was that a single-candidate match is immutable.
    What it stores is not the ID, it is WHICH ROW of `samples` represents it,
    and `sample_matches` deliberately changes that answer when the LIMS logs
    the real record in ("A BARE MATCH BESIDE A DATED ONE IS OUR OWN FORGERY").
    Cached, the phantom would win for the life of the process — the bug this
    whole road exists to end, made permanent instead of self-healing."""

    def test_a_bare_answer_is_never_remembered(self, bench):
        module = bench(Gateway(samples=[]))
        module._remember_identities({BARE: BARE}, {BARE}, set())
        assert module._cached_identity(BARE, False) == ""

    def test_the_lims_record_displaces_the_phantom_on_the_very_next_poll(
            self, bench):
        """Poll 1 sees only our phantom; the LIMS logs `081126-34566` in an
        hour later. Poll 2 must file on the LIMS's record, not on the answer
        poll 1 proved 'certain'."""
        gateway = Gateway(samples=[BARE])
        module = bench(gateway)
        first, _amb, _unk = module._resolve_identities([BARE], gateway.read_sql)
        assert first == {BARE: BARE}
        gateway.samples.append(CANONICAL)
        second, _amb, _unk = module._resolve_identities([BARE],
                                                        gateway.read_sql)
        assert second == {BARE: CANONICAL}, "the phantom was pinned forever"

    def test_a_dated_record_is_remembered_and_costs_one_read(self, bench):
        gateway = Gateway(samples=[CANONICAL])
        module = bench(gateway)
        for _ in range(20):
            module._resolve_identities([BARE], gateway.read_sql)
        assert len(gateway.identity_queries) == 1, \
            gateway.identity_queries

    def test_an_entry_is_re_proved_after_an_hour(self, bench):
        """The number is never reused, but the ROW can be voided. A bench
        filing onto a deleted sample writes cells outside the Results grid's
        INNER JOIN, where nobody can ever see them."""
        module = bench(Gateway(samples=[]))
        module._remember_identities({BARE: CANONICAL}, {BARE}, set(), now=NOW)
        assert module._cached_identity(BARE, False, NOW) == CANONICAL
        later = NOW + timedelta(seconds=mod.IDENTITY_CACHE_SECONDS + 1)
        assert module._cached_identity(BARE, False, later) == ""

    def test_a_bench_running_new_cups_pays_the_same_as_before(self, bench):
        """The honest number. A cup is printed once and never again, so a bench
        working through new samples MISSES on every one — the cache's win is
        the repeat question (a QC standard, a re-read, a restart), not this."""
        gateway = Gateway(samples=[])
        module = bench(gateway)
        for i in range(60):
            module._resolve_identities([f"7{i:04d}"], gateway.read_sql)
        assert len(gateway.identity_queries) == 60


# ── The promise is checked against the queue, not against a flag ────────────
#
# Every cap on this road ends by telling the operator where the reading went.
# Three rounds of critics found the same shape of bug each time: the mechanism
# that keeps that sentence honest existed, and was wired to some of the paths
# that needed it. These pin the ones that were missed.

class TestTheMachineLogPromiseIsNeverMadeFalsely:
    def test_a_pending_record_means_the_promise_is_not_yet_true(self, bench):
        """`_log_road_open` only remembers the LAST drain, and it starts True —
        so on a bench that has never reached LabCore it said "open" while every
        record sat in RAM. A record still queued has not reached LabCore."""
        module = bench(BusyLabCore())
        assert module._log_road_open is True
        module._queue_run_events(
            module._machine, [row(lab_id="60000", Density="0.86")], NOW)
        assert "stay in the machine log" not in module._log_home()
        assert "NOT reached LabCore" in module._log_home()

    def test_the_parked_cap_does_not_promise_a_log_it_never_wrote(self, bench):
        """`_park` runs ONLY when LabCore is unreachable, so the drain provably
        has not run — this is the one notice guaranteed false when it prints,
        and the one that hardcoded the confident sentence."""
        module = bench(BusyLabCore())
        rows = [row(lab_id=str(60000 + i), Density="0.86") for i in range(150)]
        module._queue_run_events(module._machine, rows, NOW)
        messages = []
        module._park(rows, messages)
        dropped = [m for m in messages if "could not be kept waiting" in m]
        assert dropped, messages
        assert "stay in the machine log" not in dropped[0], dropped[0]
        assert "NOT reached LabCore" in dropped[0], dropped[0]


class TestARefusedStatusIsRetriedNotLatched:
    def test_the_floor_is_not_left_showing_a_status_labcore_refused(
            self, bench):
        """A refusal is an error dict, not a raise. Recording the snapshot
        anyway latched: the next poll compared equal, skipped, and the floor
        kept the last status LabCore accepted — indefinitely, on the one write
        the web server treats as authoritative when the live push is absent."""
        gateway = BusyLabCore(capacity=0)      # refuses everything
        module = bench(gateway)
        machine = module._machine
        ev = mod.MachineEvaluation(status=mod.STATUS_RED,
                                   reason="QC out of band")
        messages = []
        module._labcore_sync(machine, [], ev, NOW, messages, [])
        assert module._last_status_pushed is None, \
            "a refused status was recorded as pushed and will never retry"
        assert any("refused the status write" in m for m in messages), messages

    def test_an_accepted_status_is_still_only_written_once(self, bench):
        """The economy this guard must not cost: an accepted status still
        latches, so idle ticks do not hammer the queue."""
        gateway = BusyLabCore(capacity=1000)
        module = bench(gateway)
        ev = mod.MachineEvaluation(status=mod.STATUS_GREEN, reason="")
        for _ in range(5):
            module._labcore_sync(module._machine, [], ev, NOW, [], [])
        status_writes = gateway.accepted
        module._labcore_sync(module._machine, [], ev, NOW, [], [])
        assert gateway.accepted == status_writes, \
            "an unchanged status was written again"


# ── Read economy: the standing load, not the sharp edge ─────────────────────
#
# The per-poll DDL was the visible defect. This is the bigger one: a bench that
# is doing NOTHING re-reads its whole configuration every poll — QC standards,
# the floor's QC assignment, per-machine spec overrides, and the PM/Cal
# schedule. All four change rarely; the poll interval is 30s. Measured before
# this guard: 5 reads per poll, ~10 LabCore ops a minute per bench, 100 a minute
# across ten benches, into the endpoint reads and writes share.
#
# The floor's manual override is deliberately NOT in that set — it is the lever
# somebody on the floor pulls to take a bench off line, and it has to land on the
# next poll, not two minutes later.

class ReadCounter:
    """Counts reads per source so a cadence can be asserted, not eyeballed."""

    def __init__(self):
        self.reads = []
        self.fail = set()

    def _tag(self, sql):
        flat = " ".join(sql.split()).lower()
        for name, key in (("lem_qc_samples", "qc_samples"),
                          ("lem_machine_targets", "targets"),
                          ("lem_qc_specs", "qc_specs"),
                          ("lem_maintenance", "maint"),
                          ("interval_days", "maint"),
                          ("lem_machine_control", "override")):
            if name in flat:
                return key
        return "other"

    def read_sql(self, sql, args=None, **kw):
        tag = self._tag(sql)
        self.reads.append(tag)
        if tag in self.fail:
            return {"error": "LabCore is busy"}
        return {"rows": []}

    def sql(self, sql, args=None, source=""):
        return {"ok": True}

    def write(self, operation, params=None, source=""):
        return {"ok": True}

    def count(self, tag):
        return self.reads.count(tag)


CONFIG_SOURCES = ("qc_samples", "targets", "qc_specs", "maint")


@pytest.fixture
def reader(qapp, monkeypatch):
    def build(gateway, machine=None):
        monkeypatch.setitem(mod.__dict__, "labcore_write", gateway.write)
        monkeypatch.setitem(mod.__dict__, "labcore_sql", gateway.sql)
        monkeypatch.setitem(mod.__dict__, "labcore_read_sql", gateway.read_sql)
        monkeypatch.setattr(mod, "_in_thread", lambda fn, cb: cb(fn()))
        module = make_module()
        module._machine = machine or Machine(uid="m1", title="Eraspec")
        return module
    return build


def _poll(module, gateway, now):
    module._labcore_sync(module._machine, [],
                         mod.MachineEvaluation(status=mod.STATUS_GREEN,
                                               reason=""),
                         now, [], [])


class TestTheBenchDoesNotReReadItsConfigEveryPoll:
    def test_the_first_poll_reads_everything(self, reader):
        """A bench that has just started knows nothing — it must ask."""
        gw = ReadCounter()
        module = reader(gw)
        _poll(module, gw, NOW)
        for source in CONFIG_SOURCES:
            assert gw.count(source) == 1, f"{source} was not read at start-up"

    def test_twenty_idle_polls_do_not_cost_twenty_config_reads(self, reader):
        """Ten minutes of an idle bench at the 30s default. Before this guard
        each source was read 20 times; the whole point is that it is not."""
        gw = ReadCounter()
        module = reader(gw)
        for i in range(20):
            _poll(module, gw, NOW + timedelta(seconds=30 * i))
        for source in CONFIG_SOURCES:
            assert gw.count(source) <= 6, (
                f"{source} read {gw.count(source)} times in 20 polls")
        assert len(gw.reads) < 60, f"{len(gw.reads)} reads for 20 idle polls"

    def test_the_floor_override_still_lands_on_the_very_next_poll(self, reader):
        """The one source that must NOT be cached: somebody on the floor taking
        a bench off line cannot wait for a refresh window."""
        gw = ReadCounter()
        module = reader(gw)
        for i in range(20):
            _poll(module, gw, NOW + timedelta(seconds=30 * i))
        assert gw.count("override") == 20, (
            "the floor's manual override is being cached — it must be read "
            "every poll")

    def test_the_config_is_re_read_once_the_window_passes(self, reader):
        """Cached, not frozen. QC assigned in LEM has to reach the bench."""
        gw = ReadCounter()
        module = reader(gw)
        _poll(module, gw, NOW)
        first = gw.count("targets")
        _poll(module, gw, NOW + timedelta(seconds=mod.CONFIG_REFRESH_SECONDS + 1))
        assert gw.count("targets") == first + 1, (
            "the config was never re-read — QC assigned on the floor would "
            "never reach this bench")

    def test_a_refused_read_is_retried_on_the_next_poll(self, reader):
        """A busy LabCore is not an answer. Caching the failure would leave a
        bench running on no QC for the whole window."""
        gw = ReadCounter()
        gw.fail = {"qc_samples", "targets", "qc_specs", "maint"}
        module = reader(gw)
        _poll(module, gw, NOW)
        gw.fail = set()
        _poll(module, gw, NOW + timedelta(seconds=30))
        # qc_samples, not targets: targets is only read when the samples read
        # succeeded, so it cannot witness a retry that the samples read gates.
        assert gw.count("qc_samples") == 2, (
            "a refused config read was cached as though it had answered")

    def test_a_new_machine_asks_again_immediately(self, reader):
        """Binding a different instrument invalidates everything known."""
        gw = ReadCounter()
        module = reader(gw)
        _poll(module, gw, NOW)
        before = gw.count("qc_samples")
        module.set_machine(Machine(uid="m2", title="Other"), publish=False)
        _poll(module, gw, NOW + timedelta(seconds=30))
        assert gw.count("qc_samples") == before + 1, (
            "a newly bound machine reused the previous machine's config reads")
