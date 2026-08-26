"""Who ran the QC, and against which calibration.

PJLA ISO/IEC 17025 assessment, September 2026: the lab is building measurement-
uncertainty estimates out of its own QC history. The metrology turns on one
question the log could not answer. If every QC run for a test is the same
analyst, on the same shift, against the same calibration, the standard deviation
of those results is **repeatability (s_r)** — the narrowest possible claim.
Calling that spread **within-laboratory reproducibility (u(Rw))** overstates the
lab's control, and it is the first thing an assessor tests by asking who ran
them.

`lem_machine_log` held the value and the timestamp and nothing else, so the
distinction could not be made after the fact — not for future rows and not for
the ones already written. Every poll that goes by without recording it is a day
of history that can never support a reproducibility claim.

The one thing that must not happen here is a blank being counted as a person.
A consumer tallying distinct operators has to be able to separate "one analyst
ran all of these" from "we do not know who ran these", and `""` reads as the
former to anything that does a set-of-strings count. Absence is JSON null, and
it is written EXPLICITLY: a row carrying `operator: null` says this module
looked and did not know, which is a different fact from a pre-2026-08 row that
has no such key because nothing was looking.
"""
import json
from datetime import datetime, timedelta

import pytest

import lem_station_module as mod
from lem_station_module import LAB_ID_KEY, Machine, TestSpec

from test_module_qt import make_module

NOW = datetime(2026, 8, 26, 9, 0, 0)

CLOUD = TestSpec(name="Cloud", value_col="Cloud", expected=-7.4,
                 std_dev=2.8, k=1.0, sample_id="CP")


def qc_machine(**over):
    base = dict(uid="m1", title="OptiMPP 1", tests=[CLOUD])
    base.update(over)
    return Machine(**base)


class Recorder:
    """Minimal stand-in: collects what _queue_run_events queues.

    Deliberately the same shape as test_qc_logging.py's. `context` defaults to
    absent rather than to a signed-in one, because that is the harder case: the
    poll worker must come back with "unknown" instead of raising.
    """

    def __init__(self, machine, context=None):
        self._machine = machine
        self._pending_events = []
        if context is not None:
            self.context = context

    _log_event = mod.LEMStationModule._log_event
    _queue_run_events = mod.LEMStationModule._queue_run_events
    _current_operator = mod.LEMStationModule._current_operator


def qc_details(machine, rows, recorder=None):
    r = recorder or Recorder(machine)
    r._queue_run_events(machine, rows, NOW)
    return [json.loads(args[6]) for _sql, args in r._pending_events
            if args[2] == "qc"]


# ── the operator ────────────────────────────────────────────────────────────
#
# LabStation's `_load_custom_module` injects exactly ten names into a custom
# module's namespace — BaseModule, LabStationContext, labcore_write,
# labcore_sql, labcore_read_sql, labcore_append_photo, labcore_is_running,
# ResultEntry, format_timestamp, _run_in_thread — and NOT one of them is a user
# identity. The identity is an attribute of the context object every module is
# constructed with, which LabStationWindow assigns at login and LabStation
# itself reads as `getattr(self.context, "current_user", None)` then
# `.username`. LabStationContext's own docstring is explicit that this is where
# modules are to read it "instead of ... routing through module-level globals".


class FakeUser:
    """LabCore's user object, as far as this module ever cares: a username."""

    def __init__(self, username):
        self.username = username


class FakeSignedInContext:
    """A LabStationContext after login — the shape LabStationWindow leaves."""

    def __init__(self, username="rmoore"):
        self.current_user = FakeUser(username)


class TestOperatorComesFromTheContext:
    def test_the_signed_in_user_is_recorded(self):
        assert mod.context_operator(FakeSignedInContext("rmoore")) == "rmoore"

    def test_no_context_at_all_is_not_a_person(self):
        """`_queue_run_events` is reachable on objects with no context, and the
        poll worker must not raise there — a raise strands `_polling`."""
        assert mod.context_operator(None) is None

    def test_a_context_without_the_attribute_is_not_a_person(self):
        """LabStationContext gained `current_user` at some point; an older or
        stubbed one simply does not have it."""
        assert mod.context_operator(object()) is None

    def test_nobody_signed_in_is_not_a_person(self):
        """`LabStationContext.__init__` sets `current_user = None` and login
        fills it in. Before that it is genuinely nobody."""
        ctx = FakeSignedInContext()
        ctx.current_user = None
        assert mod.context_operator(ctx) is None

    def test_a_user_object_with_no_username_is_not_a_person(self):
        ctx = FakeSignedInContext()
        ctx.current_user = object()
        assert mod.context_operator(ctx) is None

    def test_a_blank_username_is_not_a_person(self):
        """The exact failure this change exists to prevent: a blank that a
        consumer counts as one analyst."""
        assert mod.context_operator(FakeSignedInContext("   ")) is None
        assert mod.context_operator(FakeSignedInContext("")) is None
        assert mod.context_operator(FakeSignedInContext(None)) is None

    def test_the_name_is_trimmed(self):
        assert mod.context_operator(FakeSignedInContext(" rmoore\n")) == "rmoore"

    def test_the_module_reads_it_off_its_own_context(self, qapp):
        module = make_module()
        module.context.current_user = FakeUser("jdiaz")
        assert module._current_operator() == "jdiaz"

    def test_a_module_whose_context_never_logged_in(self, qapp):
        """`FakeContext` here has no `current_user`, exactly like a
        LabStationContext that was constructed but never signed in."""
        module = make_module()
        assert module._current_operator() is None


class TestTheIdentityIsNotAGlobal:
    """`labcore_user` / `labcore_username` were never injected by anything.

    They appear nowhere in LabStation.pyw. Every read of them returned "" on
    every bench forever, and because absence is now recorded honestly as null
    it would have gone on doing so in silence. This is the regression that
    would come back the next time somebody needs a username in a hurry.
    """

    @staticmethod
    def _globals_reads(source):
        """Line numbers where `globals().get("<banned name>")` is evaluated.

        Walks the AST rather than grepping the text, so the guard cannot be
        tripped by a comment or a docstring that NAMES the two globals. That
        matters: the whole point of the accessor's docstring is to explain
        which names were tried and why they never existed, and a tripwire the
        file cannot document itself past is a tripwire somebody deletes the
        next time it goes off for the wrong reason. Prose is a Constant node
        that is not in argument position of a `globals().get(...)` call and is
        not a Name, so neither branch below can ever see it.
        """
        import ast

        banned = ("labcore_user", "labcore_username")
        hits = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Name) and node.id in banned:
                hits.append(node.lineno)
                continue
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == "get"):
                continue
            inner = fn.value
            if not (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "globals"):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value in banned:
                    hits.append(node.lineno)
        return hits

    def test_no_source_line_reads_the_identity_out_of_globals(self):
        import inspect
        hits = self._globals_reads(inspect.getsource(mod))
        assert hits == [], (
            "the user identity is read out of globals() at line(s) "
            f"{hits} — LabStation injects no such name; use "
            "self._current_operator()")

    def test_the_guard_would_catch_it_coming_back(self):
        """A tripwire nobody has seen fire is a tripwire nobody trusts."""
        assert self._globals_reads(
            'who = globals().get("labcore_user")') == [1]
        assert self._globals_reads("x = labcore_username") == [1]

    def test_the_guard_permits_the_file_explaining_itself(self):
        assert self._globals_reads('"""Not labcore_user, which never existed."""') == []
        assert self._globals_reads("# labcore_username was never injected\nx = 1") == []


# ── the calibration epoch ───────────────────────────────────────────────────

class TestCalibrationEpochQuery:
    def test_it_asks_the_machine_log_for_calibrations(self):
        sql, args = mod.build_last_calibration_query("m1")
        assert args == ["m1"]
        assert "lem_machine_log" in sql
        assert "kind = 'calibration'" in sql

    def test_it_asks_for_the_newest_one_only(self):
        """`build_last_qc_query` had to be fixed from ASC to DESC after it spent
        months recovering the OLDEST verdict on any machine past 400 rows. One
        row, newest first, so there is nothing to get backwards."""
        sql, _args = mod.build_last_calibration_query("m1")
        assert "ORDER BY ts DESC" in sql and "LIMIT 1" in sql

    def test_the_newest_timestamp_wins(self):
        rows = [{"ts": "2026-08-01T07:00:00"}, {"ts": "2026-06-02T07:00:00"}]
        assert mod.last_calibration_id(rows) == "2026-08-01T07:00:00"

    def test_no_calibration_is_not_an_epoch(self):
        assert mod.last_calibration_id([]) is None
        assert mod.last_calibration_id(None) is None

    def test_a_blank_timestamp_is_not_an_epoch(self):
        assert mod.last_calibration_id([{"ts": "  "}]) is None

    def test_unreadable_rows_do_not_raise(self):
        """This runs on the poll worker, where a raise strands `_polling`."""
        assert mod.last_calibration_id(["nonsense", {"ts": None}]) is None


# ── what the verdict records ────────────────────────────────────────────────

class TestQcLogDetail:
    def test_it_carries_the_operator_and_the_calibration(self):
        detail = mod.qc_log_detail(CLOUD, -6.6, -6.6, operator="rmoore",
                                   calibration_id="2026-08-01T07:00:00")
        assert detail["operator"] == "rmoore"
        assert detail["calibration_id"] == "2026-08-01T07:00:00"

    def test_absence_is_explicit_null_not_a_missing_key(self):
        """A written null says this module looked and did not know. A missing
        key says nothing was looking — which is what every row before this
        change means, and the two must not be confused."""
        detail = mod.qc_log_detail(CLOUD, -6.6, -6.6)
        assert detail["operator"] is None
        assert detail["calibration_id"] is None

    def test_a_blank_operator_is_recorded_as_unknown(self):
        detail = mod.qc_log_detail(CLOUD, -6.6, -6.6, operator="  ",
                                   calibration_id="")
        assert detail["operator"] is None
        assert detail["calibration_id"] is None

    def test_the_verdict_it_already_recorded_is_untouched(self):
        detail = mod.qc_log_detail(CLOUD, -6.6, -6.6, operator="rmoore")
        assert detail["in_spec"] is True
        assert detail["expected"] == -7.4
        assert (detail["low"], detail["high"]) == (-10.2, -4.6)

    def test_the_correction_record_is_untouched(self):
        spec = TestSpec(name="Cloud", value_col="Cloud", expected=-7.4,
                        std_dev=2.8, k=1.0, sample_id="CP", correction=-1.0)
        detail = mod.qc_log_detail(spec, -5.6, -6.6, operator="rmoore")
        assert detail["raw_value"] == -5.6
        assert detail["correction"] == -1.0

    def test_it_survives_json(self):
        """The detail column is a JSON string; None has to round-trip as null."""
        detail = json.loads(json.dumps(mod.qc_log_detail(CLOUD, -6.6, -6.6)))
        assert detail["operator"] is None


# ── the verdict on the poll path ────────────────────────────────────────────

class TestVerdictsCarryProvenance:
    def test_a_qc_verdict_names_who_ran_it(self):
        machine = qc_machine()
        r = Recorder(machine, FakeSignedInContext("rmoore"))
        rows = [{LAB_ID_KEY: "CP", "Cloud": "-6.6"}]
        assert qc_details(machine, rows, r)[0]["operator"] == "rmoore"

    def test_a_qc_verdict_names_the_calibration_in_force(self):
        machine = qc_machine()
        r = Recorder(machine, FakeSignedInContext("rmoore"))
        r._calibration_epoch = "2026-08-01T07:00:00"
        rows = [{LAB_ID_KEY: "CP", "Cloud": "-6.6"}]
        detail = qc_details(machine, rows, r)[0]
        assert detail["calibration_id"] == "2026-08-01T07:00:00"

    def test_an_unknown_operator_is_null_not_a_name(self):
        """A bench nobody is signed in at still records its QC — with the
        analyst honestly unknown rather than blank."""
        rows = [{LAB_ID_KEY: "CP", "Cloud": "-6.6"}]
        detail = qc_details(qc_machine(), rows)[0]
        assert detail["operator"] is None and detail["calibration_id"] is None

    def test_a_typed_reading_records_who_typed_it(self, qapp, monkeypatch):
        """The manual bench is where the operator is literally standing at the
        instrument, and it goes through the same `_queue_run_events`."""
        from test_manual_mode import manual_machine
        monkeypatch.setattr(mod, "_in_thread", lambda fn, cb: cb(fn()))
        monkeypatch.delitem(mod.__dict__, "labcore_read_sql", raising=False)
        module = make_module()
        module.context.current_user = FakeUser("rmoore")
        module.set_machine(manual_machine(), publish=False)
        # A REAL completed calibration on the bench, not a poked attribute. The
        # epoch is derived from `machine.maintenance` now, so assigning
        # `_calibration_epoch` directly is overwritten the moment the entry path
        # resolves it — which is the design working, and a test that hid it
        # would be asserting against a value the bench cannot actually have.
        module.add_task("Annual cal", "calibration", 365)
        module._machine.maintenance[0].last_done = "2026-08-01"
        module.log_manual_entry("Flash Point", "63.9", now=NOW)
        detail = next(json.loads(args[6]) for _sql, args in module._pending_events
                      if args[2] == "qc")
        assert detail["operator"] == "rmoore"
        assert detail["calibration_id"] == "2026-08-01"

    def test_a_production_run_is_not_given_a_qc_verdict_shape(self, monkeypatch):
        """A `run` is a sample, not a check; nothing here claims otherwise."""
        monkeypatch.setitem(mod.__dict__, "labcore_username", "rmoore")
        rows = [{LAB_ID_KEY: "37043", "Cloud": "-11.7"}]
        r = Recorder(qc_machine())
        r._queue_run_events(qc_machine(), rows, NOW)
        detail = json.loads(r._pending_events[0][1][6])
        assert r._pending_events[0][1][2] == "run"
        assert "in_spec" not in detail


# ── what it costs ───────────────────────────────────────────────────────────

class CalibrationReads:
    """The injected labcore_read_sql, counting only calibration lookups."""

    def __init__(self, ts="2026-08-01T07:00:00"):
        self.calls = 0
        self.ts = ts
        self.error = None

    def read_sql(self, sql, args=None, **kw):
        if "'calibration'" not in str(sql):
            return {"error": "not this test's read"}
        self.calls += 1
        if self.error:
            return {"error": self.error}
        return {"rows": ([{"ts": self.ts}] if self.ts else [])}


@pytest.fixture
def bench(qapp, monkeypatch):
    def build():
        reads = CalibrationReads()
        monkeypatch.setitem(mod.__dict__, "labcore_read_sql", reads.read_sql)
        monkeypatch.setattr(mod, "_in_thread", lambda fn, cb: cb(fn()))
        module = make_module()
        module._machine = qc_machine()
        return module, reads
    return build


class TestTheEpochCostsNoLabCoreRead:
    """It is derived from `machine.maintenance`, which the bench already holds.

    The first draft of this road spent one windowed `read_sql` per bench for
    the epoch. `test_restart_stampede` counts what a poll asks LabCore for and
    caught it as a third read where the read-economy work allows two — that
    work exists because a per-bench timer read multiplies by a bench count that
    is still growing, and it is what took an idle bench from 6.2 reads/min to
    0.9. Paying an op again for something already on the config road would give
    a slice of that straight back.
    """

    def test_a_poll_asks_labcore_for_nothing_at_all(self, bench):
        module, reads = bench()
        module._refresh_calibration_epoch(module._machine, NOW)
        assert reads.calls == 0

    def test_not_even_on_the_first_poll(self, bench):
        """The window cannot be the thing that makes it free — a cold module
        has no stamp, and that is exactly when a read would fire."""
        module, reads = bench()
        module._calibration_read_at = None
        module._calibration_epoch = None
        module._refresh_calibration_epoch(module._machine, NOW)
        assert reads.calls == 0

    def test_fifty_readings_in_one_poll_still_ask_nothing(self, bench):
        module, reads = bench()
        for n in range(50):
            module._refresh_calibration_epoch(module._machine,
                                              NOW + timedelta(seconds=n))
        assert reads.calls == 0

    def test_it_reads_the_calibration_task_this_bench_already_has(self, bench):
        module, _reads = bench()
        module.add_task("Annual cal", "calibration", 365)
        module._machine.maintenance[0].last_done = "2026-08-20"
        module._refresh_calibration_epoch(module._machine, NOW)
        assert module._calibration_epoch == "2026-08-20"

    def test_the_newest_calibration_wins(self, bench):
        """A bench can carry more than one calibration task. The epoch is the
        most recent completion, not whichever task happens to be first."""
        module, _reads = bench()
        module.add_task("Annual cal", "calibration", 365)
        module.add_task("Six-monthly cal", "calibration", 180)
        module._machine.maintenance[0].last_done = "2026-02-01"
        module._machine.maintenance[1].last_done = "2026-08-20"
        module._refresh_calibration_epoch(module._machine, NOW)
        assert module._calibration_epoch == "2026-08-20"

    def test_a_pm_task_is_not_a_calibration(self, bench):
        """PM and calibration are different columns on the floor and different
        events in the record. A PM completion must not move the epoch."""
        module, _reads = bench()
        module.add_task("Monthly PM", "pm", 30)
        module._machine.maintenance[0].last_done = "2026-08-25"
        module._refresh_calibration_epoch(module._machine, NOW)
        assert module._calibration_epoch is None

    def test_a_calibration_logged_here_is_seen_at_once(self, bench):
        """Waiting out a window would stamp readings taken right after a
        calibration with the epoch it replaced."""
        module, reads = bench()
        module._refresh_calibration_epoch(module._machine, NOW)
        module.add_task("Annual cal", "calibration", 365)
        uid = module._machine.maintenance[0].uid
        module.complete_task(uid)
        module._refresh_calibration_epoch(module._machine,
                                          NOW + timedelta(seconds=12))
        assert module._calibration_epoch == module._machine.maintenance[0].last_done
        assert module._calibration_epoch is not None
        assert reads.calls == 0

    def test_no_read_at_all_without_labcore(self, qapp, monkeypatch):
        monkeypatch.delitem(mod.__dict__, "labcore_read_sql", raising=False)
        module = make_module()
        module._machine = qc_machine()
        module._refresh_calibration_epoch(module._machine, NOW)
        assert module._calibration_epoch is None


class TestAbsenceIsUnknownNotAValue:
    def test_a_bench_that_has_never_been_calibrated_has_no_epoch(self, bench):
        """`None`, never "". A consumer counts DISTINCT epochs to decide whether
        a QC series spans calibrations, and a blank string counted as an epoch
        would manufacture the coverage this road exists to report honestly."""
        module, _reads = bench()
        module._refresh_calibration_epoch(module._machine, NOW)
        assert module._calibration_epoch is None

    def test_a_calibration_task_never_completed_is_not_an_epoch(self, bench):
        """A scheduled calibration is not a performed one."""
        module, _reads = bench()
        module.add_task("Annual cal", "calibration", 365)
        module._machine.maintenance[0].last_done = ""
        module._refresh_calibration_epoch(module._machine, NOW)
        assert module._calibration_epoch is None

    def test_a_whitespace_completion_is_not_an_epoch(self, bench):
        module, _reads = bench()
        module.add_task("Annual cal", "calibration", 365)
        module._machine.maintenance[0].last_done = "   "
        module._refresh_calibration_epoch(module._machine, NOW)
        assert module._calibration_epoch is None


class TestBindingAnotherInstrument:
    def test_the_previous_bench_s_calibration_does_not_carry_over(self, bench):
        """Stamping a verdict with the instrument-before-last's calibration is
        worse than stamping it unknown: it is a provenance claim about the wrong
        machine, and nothing downstream can tell."""
        module, _reads = bench()
        module.add_task("Annual cal", "calibration", 365)
        module._machine.maintenance[0].last_done = "2026-08-20"
        module._refresh_calibration_epoch(module._machine, NOW)
        assert module._calibration_epoch == "2026-08-20"

        fresh = qc_machine()
        fresh.uid = "another-bench"
        fresh.maintenance = []
        module.set_machine(fresh, publish=False)
        module._refresh_calibration_epoch(module._machine, NOW)
        assert module._calibration_epoch is None


class TestOlderRowsStillRead:
    def test_a_verdict_with_no_provenance_is_still_a_verdict(self):
        """Every lem_machine_log row written before this change has neither
        field, and `qc_series.py` on the web server reads `operator`
        defensively. Nothing here may start requiring it."""
        rows = [{"test_name": "Cloud", "value": "-6.6",
                 "ts": "2026-07-01T09:00:00",
                 "detail": json.dumps({"in_spec": True, "expected": -7.4})}]
        latest = mod.last_qc_by_test(rows)
        assert latest["Cloud"]["in_spec"] is True

    def test_a_null_operator_does_not_break_the_reader(self):
        rows = [{"test_name": "Cloud", "value": "-6.6",
                 "ts": "2026-08-26T09:00:00",
                 "detail": json.dumps({"in_spec": True, "expected": -7.4,
                                       "operator": None,
                                       "calibration_id": None})}]
        latest = mod.last_qc_by_test(rows)
        assert latest["Cloud"]["in_spec"] is True


# ── the two audit sites that were silently blank ────────────────────────────
#
# Both predate this change and both read the identity out of a global that was
# never injected, so both have stamped "" on every bench since they were
# written. They are byline columns a person reads, not fields a program tallies,
# and that is why an unknown user writes UNKNOWN_OPERATOR here while the JSON
# provenance writes null: `lab_search._OPERATOR_KEYS` harvests `by` / `user` /
# `username` out of a log row's detail and `qc_series.Coverage` counts distinct
# NAMED analysts, so a marker string in the JSON would invent a person. Nothing
# reads `updated_by` that way — every consumer in both trees renders it with
# `str(... or "")` — so there the blank is the lie, because it is
# indistinguishable from a field nobody filled in.


class FakeCorrectionsDialog:
    """Stands in for _CorrectionsDialog: accepted, with one change."""

    def __init__(self, machine, parent):
        self.machine = machine

    def exec(self):
        return True

    def changes(self):
        return {"Cloud": -1.5}


@pytest.fixture
def audit(qapp, monkeypatch):
    """A module whose dialogs are stubbed and whose writes are captured."""
    def build(username=None):
        written = []
        monkeypatch.setitem(mod.__dict__, "labcore_sql",
                            lambda sql, args=None, **kw: written.append((sql, args)))
        monkeypatch.setattr(mod, "_CorrectionsDialog", FakeCorrectionsDialog)
        monkeypatch.setattr(mod, "_in_thread", lambda fn, cb: cb(fn()))
        module = make_module()
        if username is not None:
            module.context.current_user = FakeUser(username)
        module._machine = qc_machine()
        return module, written
    return build


def arg_of(written, table, index):
    for sql, args in written:
        if table in str(sql) and str(sql).upper().startswith("INSERT"):
            return args[index]
    raise AssertionError(f"no INSERT into {table} in {[s for s, _ in written]}")


def logged_detail(module, written, kind):
    """The detail of one machine-log record, queued or already drained.

    `_open_corrections` calls `_flush_events_now`, and with `_in_thread` inline
    that empties `_pending_events` into LabCore before the test looks — so the
    record is in `written`, as a flattened seven-column batch INSERT.
    """
    for _sql, args in module._pending_events:
        if args[2] == kind:
            return json.loads(args[6])
    for sql, args in written:
        if "lem_machine_log" not in str(sql) or not args:
            continue          # the CREATE TABLE goes through with no args
        for i in range(0, len(args), 7):
            if args[i + 2] == kind:
                return json.loads(args[i + 6])
    raise AssertionError(f"no {kind!r} record was queued or written")


class TestTheCorrectionFactorAuditNamesSomebody:
    """ISO/IEC 17025 §7.8.2 — the offset is part of every result the bench
    reports, `lem_correction_factors` is an UPSERT that destroys the previous
    value, and `updated_by` is the byline on the row that replaced it."""

    def test_the_signed_in_user_is_stamped(self, audit):
        module, written = audit("rmoore")
        module._open_corrections(module._machine)
        assert arg_of(written, "lem_correction_factors", 5) == "rmoore"

    def test_an_unknown_user_is_named_unknown_not_left_blank(self, audit):
        module, written = audit()
        module._open_corrections(module._machine)
        assert arg_of(written, "lem_correction_factors", 5) == mod.UNKNOWN_OPERATOR
        assert mod.UNKNOWN_OPERATOR.strip()

    def test_the_log_event_records_the_unknown_as_null(self, audit):
        """`detail["by"]` is harvested as a person by the floor's search index,
        so the marker must not go here — only into the byline column."""
        module, written = audit()
        module._open_corrections(module._machine)
        assert logged_detail(module, written, "config")["by"] is None

    def test_the_log_event_records_a_known_user_by_name(self, audit):
        module, written = audit("rmoore")
        module._open_corrections(module._machine)
        assert logged_detail(module, written, "config")["by"] == "rmoore"


class TestTheConfigPublishNamesSomebody:
    def test_the_signed_in_user_is_stamped(self, audit):
        module, written = audit("rmoore")
        module._publish_config(module._machine)
        assert arg_of(written, "lem_machine_config", 4) == "rmoore"

    def test_an_unknown_user_is_named_unknown_not_left_blank(self, audit):
        module, written = audit()
        module._publish_config(module._machine)
        assert arg_of(written, "lem_machine_config", 4) == mod.UNKNOWN_OPERATOR
