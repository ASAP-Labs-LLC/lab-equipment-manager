"""The module must not spend the lab's write queue on paperwork.

LabCore serialises every operation — and `read_sql` goes through the same queue —
so a wasted write delays everybody's reads. Measured live while six modules were
running: **81 operations pending**, the queue clearing about 3.8 ops/sec, and the
web server's snapshot read waiting behind all of it. That is what made the floor
look stale and, before the timeout was raised, made it flash "LABCORE OFFLINE".

`CREATE TABLE IF NOT EXISTS` is the easiest waste to find: harmless, invisible, and
issued over and over. The main three were already declared once per process behind
`_labcore_table_ready`; the sub-status table was re-declared on **every status
change**, and the effective-specs and corrections tables on **every publish** —
which happens on every QC reading, since the published fingerprint includes it.

Ryan, later: *"the LEM heartbeats are bogging down the server"* — the queue backing
up, at ten benches and "a lot more to be added still". So the heartbeat road gets
its own class below. It was paying twice over: a `CREATE TABLE` before **every
beat**, and a pulse timer that fired on its own fixed clock without asking whether
the poll had already checked in seconds earlier. Both multiply by bench count,
which is the number that is growing.
"""
from datetime import datetime, timedelta

import pytest

import lem_station_module as mod
from lem_station_module import Machine, TestSpec

from test_module_qt import make_module

NOW = datetime(2026, 8, 3, 22, 0)

ALL_TABLES = ("STATUS_TABLE_DDL", "LOG_TABLE_DDL", "HEARTBEAT_TABLE_DDL",
              "HELD_TABLE_DDL", "SUBSTATUS_TABLE_DDL", "EFFECTIVE_SPECS_DDL",
              "CORRECTIONS_DDL")

# The indexes on lem_machine_log ride in the SAME one-time block. They are DDL
# with the same cost and the same failure mode as a CREATE TABLE — one queue op
# each, refusable by a congested LabCore — so everything this file pins about a
# refused table has to hold for a refused index as well. See
# `is_ddl` and `TestARefusedIndexBacksOffToo`.
ALL_INDEXES = ("LOG_INDEX_DDL",)


def is_ddl(sql):
    """A statement that costs a queue op to declare something.

    This used to read `"CREATE TABLE" in sql.upper()` in three places. That was
    the same thing while the block was seven CREATE TABLEs; once index
    declarations joined it, a fake that only knew about tables would ACCEPT
    every index a congested LabCore would really have refused — and the backoff
    tests would pass while the production path latched its ready flag over
    declarations that never landed.
    """
    text = str(sql).upper()
    return "CREATE TABLE" in text or "CREATE INDEX" in text


def ddl_count(sqls):
    return len([s for s in sqls if is_ddl(s)])


class Counter:
    """The injected labcore_* helpers, counting what reaches the queue."""

    def __init__(self):
        self.sqls = []

    def sql(self, sql, args=None, source=""):
        self.sqls.append(str(sql))
        return {"ok": True}

    def read_sql(self, sql, args=None, **kw):
        return {"error": "no such table"}

    def write(self, operation, params=None, source=""):
        return {"ok": True}

    def beats(self):
        return [s for s in self.sqls
                if "lem_machine_heartbeat" in s and "INSERT" in s.upper()]

    def heartbeat_ddl(self):
        return [s for s in self.sqls
                if "lem_machine_heartbeat" in s and "CREATE" in s.upper()]


@pytest.fixture
def bench(qapp, monkeypatch):
    """A module wired to a counting gateway, with the worker run inline."""

    def build():
        counter = Counter()
        monkeypatch.setitem(mod.__dict__, "labcore_write", counter.write)
        monkeypatch.setitem(mod.__dict__, "labcore_sql", counter.sql)
        monkeypatch.setitem(mod.__dict__, "labcore_read_sql", counter.read_sql)
        monkeypatch.setattr(mod, "_in_thread", lambda fn, cb: cb(fn()))
        module = make_module()
        module._machine = Machine(uid="m1", title="Eraspec")
        module._polling = True
        return module, counter

    return build


class TestDdlIsDeclaredOncePerProcess:
    def test_the_sync_declares_its_tables_once(self):
        """Every table this module writes to, declared behind one flag.

        The block moved out of `_labcore_sync` into `_declare_tables` so that
        the pulse and the event flush could share it instead of each declaring
        its own subset. The guarantee is unchanged: one flag, set last, every
        table inside it.
        """
        import inspect
        src = inspect.getsource(mod.LEMStationModule._declare_tables)
        guarded = src[:src.index("self._labcore_table_ready = True")]
        assert "if self._labcore_table_ready:" in guarded
        for name in ALL_TABLES + ALL_INDEXES:
            assert name in guarded, f"{name} is not declared in the one-time block"

    def test_the_log_indexes_are_declared_after_the_log_table(self):
        """Order is load-bearing, not cosmetic. `CREATE INDEX` on a table that
        does not exist yet is an ERROR, and an error in this block backs the
        whole thing off — so a fresh LabCore would leave the bench declaring
        forever. The table has to be declared first, in the same block."""
        import inspect
        src = inspect.getsource(mod.LEMStationModule._declare_tables)
        guarded = src[:src.index("self._labcore_table_ready = True")]
        # Comments in that block name both constants while explaining exactly
        # this rule, so read the CODE, not the prose about it.
        code = "\n".join(line for line in guarded.splitlines()
                         if not line.lstrip().startswith("#"))
        assert code.index("LOG_TABLE_DDL") < code.index("LOG_INDEX_DDL"), code

    def test_the_indexes_are_declared_exactly_once(self, bench):
        """Same guarantee the tables have. Creating an index over SMB on a log
        that has grown for a year is not free, and it must not be attempted on
        every poll of every bench."""
        module, counter = bench()
        for beat in range(5):
            module._send_pulse(NOW + timedelta(seconds=mod.HEARTBEAT_SECONDS
                                               * beat))
        made = [s for s in counter.sqls if "CREATE INDEX" in s.upper()]
        assert len(made) == len(mod.LOG_INDEX_DDL), made

    def test_the_sync_goes_through_the_shared_block(self):
        import inspect
        src = inspect.getsource(mod.LEMStationModule._labcore_sync)
        assert "self._declare_tables(run_sql" in src
        assert "_DDL" not in src, "the sync declares a table of its own"

    def test_no_ddl_sits_on_the_status_write_path(self):
        """A status change is frequent; it must cost exactly the rows it changes."""
        import inspect
        src = inspect.getsource(mod.LEMStationModule._labcore_sync)
        after = src[src.index("if snapshot != self._last_status_pushed"):]
        after = after[:after.index("self._last_status_pushed")]
        assert "_DDL" not in after, after

    def test_no_ddl_sits_on_the_spec_publish_path(self):
        """Publishing happens on every QC reading."""
        import inspect
        src = inspect.getsource(mod.LEMStationModule._labcore_sync)
        block = src[src.index("if fingerprint != self._published_specs"):]
        block = block[:block.index("self._published_specs = fingerprint")]
        assert "_DDL" not in block, block


class TestTheHeartbeatRoad:
    """Ryan's report, in his words: "the LEM heartbeats are bogging down the
    server". Two costs, both per bench per beat and both multiplied by a bench
    count that is growing."""

    def test_no_ddl_sits_on_the_pulse_path(self):
        """The same guard the status and spec-publish paths already have.

        `_send_pulse` ran `run_sql(HEARTBEAT_TABLE_DDL)` before every beat,
        which doubled the heartbeat's cost forever. It cannot simply be deleted:
        the pulse timer starts at construction and can fire before
        `_labcore_sync` has ever run, so on a fresh LabCore the table may not
        exist yet. It goes through the shared one-time block instead.
        """
        import inspect
        src = inspect.getsource(mod.LEMStationModule._send_pulse)
        assert "_DDL" not in src, src
        assert "self._declare_tables(run_sql" in src

    def test_the_pulse_declares_the_table_on_a_fresh_labcore(self, bench):
        """Declared once, but before the first beat — not never."""
        module, counter = bench()
        module._send_pulse(NOW)
        assert len(counter.heartbeat_ddl()) == 1
        assert len(counter.beats()) == 1

    def test_the_pulse_declares_nothing_on_later_beats(self, bench):
        module, counter = bench()
        for beat in range(5):
            module._send_pulse(NOW + timedelta(seconds=mod.HEARTBEAT_SECONDS
                                               * beat))
        assert len(counter.beats()) == 5
        assert len(counter.heartbeat_ddl()) == 1, counter.heartbeat_ddl()

    def test_the_pulse_does_not_beat_inside_the_window(self, bench):
        """The timer's own cadence is not the rule; `_last_heartbeat` is."""
        module, counter = bench()
        module._send_pulse(NOW)
        module._send_pulse(NOW + timedelta(seconds=mod.HEARTBEAT_SECONDS - 1))
        assert len(counter.beats()) == 1

    def test_a_beat_from_the_poll_suppresses_the_pulse(self, bench):
        """The bug Ryan reported, at its source: the sync writes a beat, and
        seconds later the pulse wrote another because it never asked."""
        module, counter = bench()
        module._labcore_sync(module._machine, [],
                             mod.MachineEvaluation(status="GREEN", reason=""),
                             NOW, [], [])
        assert len(counter.beats()) == 1
        module._send_pulse(NOW + timedelta(seconds=5))
        assert len(counter.beats()) == 1, "the pulse beat over a fresh beat"

    def test_a_beat_from_the_pulse_suppresses_the_poll(self, bench):
        """And the same gate the other way round, which already held."""
        module, counter = bench()
        module._send_pulse(NOW)
        module._labcore_sync(module._machine, [],
                             mod.MachineEvaluation(status="GREEN", reason=""),
                             NOW + timedelta(seconds=5), [], [])
        assert len(counter.beats()) == 1

    def test_one_beat_per_window_however_many_roads_want_one(self, bench):
        """An hour of a bench at a twelve-second poll and a five-minute pulse."""
        module, counter = bench()
        evaluation = mod.MachineEvaluation(status="GREEN", reason="")
        for second in range(0, 3600, 1):
            when = NOW + timedelta(seconds=second)
            if second % 12 == 0:
                module._labcore_sync(module._machine, [], evaluation, when,
                                     [], [])
            if second % mod.HEARTBEAT_SECONDS == 0:
                module._send_pulse(when)
        assert len(counter.beats()) == 3600 // mod.HEARTBEAT_SECONDS
        assert len(counter.heartbeat_ddl()) == 1

    def test_a_failed_beat_is_tried_again_next_tick(self, bench):
        """`_last_heartbeat` moves on a beat that landed, never on one that
        did not — the floor's dot must not go grey because a write bounced."""
        module, counter = bench()

        def refuse(sql, args=None, source=""):
            raise RuntimeError("queue full")

        mod.__dict__["labcore_sql"] = refuse
        module._send_pulse(NOW)
        mod.__dict__["labcore_sql"] = counter.sql
        module._send_pulse(NOW + timedelta(seconds=1))
        assert len(counter.beats()) == 1

    def test_the_flush_path_declares_every_table(self, bench):
        """`_flush_events_worker` declared TWO tables and then set the flag the
        sync reads, so a process whose first LabCore contact was an operator
        note left five tables undeclared and believed otherwise.

        The count now includes the log's indexes for the same reason it includes
        the tables: this road latches the flag every other road trusts, and
        anything it skips is skipped for the life of the process.
        """
        module, counter = bench()
        module._flush_events_worker()
        assert module._labcore_table_ready is True
        assert ddl_count(counter.sqls) == (len(ALL_TABLES)
                                           + len(mod.LOG_INDEX_DDL))


class TestRecoveryDoesNotChurn:
    """Re-applying remembered QC set `needs_reevaluation` on every sync for any
    instrument with a test that had never run — so a bench awaiting its first
    standard re-evaluated forever. The status write is guarded, so it cost no
    writes, but it is work done for nothing on every tick."""

    def spec(self, **over):
        base = dict(name="Cloud", value_col="Cloud", expected=-7.4, std_dev=2.8,
                    k=1.0, sample_id="CP")
        base.update(over)
        return TestSpec(**base)

    def test_applying_nothing_reports_no_change(self):
        m = Machine(uid="m1", title="T", tests=[self.spec()])
        assert mod.apply_last_qc(m, {}) is False

    def test_applying_something_reports_a_change(self):
        m = Machine(uid="m1", title="T", tests=[self.spec()])
        assert mod.apply_last_qc(m, {"Cloud": {
            "at": NOW.isoformat(), "value": -7.2, "in_spec": True}}) is True

    def test_re_applying_the_same_thing_reports_no_change(self):
        """The second sync must be a no-op, not another round of work."""
        latest = {"Cloud": {"at": NOW.isoformat(), "value": -7.2,
                            "in_spec": True}}
        m = Machine(uid="m1", title="T", tests=[self.spec()])
        assert mod.apply_last_qc(m, latest) is True
        assert mod.apply_last_qc(m, latest) is False

    def test_a_test_that_never_ran_does_not_trigger_forever(self):
        """Nothing remembered for it, so there is nothing to apply."""
        m = Machine(uid="m1", title="T", tests=[self.spec(name="Never",
                                                          value_col="Never")])
        latest = {"Cloud": {"at": NOW.isoformat(), "value": -7.2,
                            "in_spec": True}}
        assert mod.apply_last_qc(m, latest) is False


# ── A refused declaration must not become the congestion it is reporting ────
#
# `_declare_tables` fires seven `CREATE TABLE IF NOT EXISTS` statements and
# latches its flag only if ALL SEVEN were accepted, which is right: a refused
# declaration is not a declaration, and believing otherwise is the bug that
# block already exists to fix.
#
# What was missing is what happens next. LabCore refuses work when its queue is
# deep by returning an error DICT — `{"error": ..., "busy": true,
# "retry_after": n}` — so a congested LabCore left the flag down and EVERY
# subsequent poll, on EVERY bench, re-fired all seven statements straight at the
# queue that had just said it was full. A slow queue produced more work, which
# made it slower: the load rose exactly when it could least be afforded, and it
# rose by bench count.
#
# LabCore's own client documents `retry_after` and notes.md records the standing
# rule that a bulk write must "honour `retry_after` and back off". So this backs
# off, the same way `_retry_pending_bind` already does for the binding read —
# five seconds doubling to a minute — and it honours `retry_after` when LabCore
# has said how long it wants.
#
# The existing guarantees are untouched: a refusal still leaves the flag DOWN, a
# raise still leaves it down, and once all seven are accepted the flag latches
# and the road costs nothing for the life of the process.


class Refusing:
    """A LabCore that refuses DDL until it is told to stop.

    The signatures are LabStation's REAL ones — `labcore_sql` takes `source`
    AND `timeout`, `labcore_read_sql` takes NO `source` — because a fake looser
    than the thing it stands in for is how a call that raises TypeError in
    production sails through a test.
    """

    def __init__(self, refusal=None, refuse_after=0):
        self.sqls = []
        self.busy = True
        # Which statement of the seven is turned away: 0 refuses the first.
        self.refuse_after = refuse_after
        self.refusal = refusal or {"error": "LabCore is busy", "busy": True}

    def sql(self, sql, args=None, source="LabStation", timeout=None):
        self.sqls.append(str(sql))
        if self.busy and is_ddl(sql):
            if len(self.ddl()) > self.refuse_after:
                return dict(self.refusal)
        return {"ok": True}

    def read_sql(self, sql, args=None, timeout=None):
        return {"error": "no such table"}

    def write(self, operation, params=None, source=""):
        return {"ok": True}

    def ddl(self):
        return [s for s in self.sqls if is_ddl(s)]


@pytest.fixture
def congested(qapp, monkeypatch):
    def build():
        labcore = Refusing()
        monkeypatch.setitem(mod.__dict__, "labcore_write", labcore.write)
        monkeypatch.setitem(mod.__dict__, "labcore_sql", labcore.sql)
        monkeypatch.setitem(mod.__dict__, "labcore_read_sql", labcore.read_sql)
        monkeypatch.setattr(mod, "_in_thread", lambda fn, cb: cb(fn()))
        module = make_module()
        module._machine = Machine(uid="m1", title="Eraspec")
        module._polling = True
        return module, labcore
    return build


class TestTheBackoffSchedule:
    def test_the_constants_mirror_the_binding_retry(self):
        """The proven pattern in this file, reused rather than reinvented."""
        assert mod.DECLARE_RETRY_SECONDS == 5.0
        assert mod.DECLARE_RETRY_MAX_SECONDS == 60.0

    def test_labcore_s_own_wait_is_read_off_the_refusal(self):
        assert mod.retry_after_seconds(
            {"error": "busy", "busy": True, "retry_after": 30}) == 30.0

    def test_a_refusal_without_one_has_none_to_read(self):
        assert mod.retry_after_seconds({"error": "busy"}) is None

    def test_junk_is_not_a_wait(self):
        """A server that answers with something new must not be able to park
        this bench's declarations for a fortnight, or for a negative time."""
        for result in (None, {}, [], "30", {"retry_after": "soon"},
                       {"retry_after": None}, {"retry_after": -5},
                       {"retry_after": float("nan")}):
            assert mod.retry_after_seconds(result) is None, result


class TestARefusedDeclarationBacksOff:
    def test_a_refusal_still_leaves_the_flag_down(self, congested):
        """The existing guarantee, and it is not being traded away."""
        module, labcore = congested()
        module._declare_tables(labcore.sql, NOW)
        assert module._labcore_table_ready is False

    def test_the_refused_statement_is_the_last_one_fired(self, congested):
        """Statement three of seven refused must not be followed by four to
        seven. The queue has just said it is full; the remaining statements are
        work aimed at the congestion being reported."""
        module, labcore = congested()
        labcore.refuse_after = 2          # the third is turned away
        module._declare_tables(labcore.sql, NOW)
        assert len(labcore.ddl()) == 3, labcore.ddl()

    def test_the_roads_arriving_inside_the_wait_do_not_re_fire(self,
                                                              congested):
        """The defect. Every road through here re-fired the block at a queue
        that was refusing because it was already too deep — and THREE roads go
        through it (the sync, the pulse and the event flush), so a bench could
        spend the block several times inside one poll.

        The first wait is deliberately short — five seconds, the same as the
        binding retry — so a bench is back within seconds of LabCore clearing.
        What it buys is that the refusal is not free to repeat: the roads that
        arrive in that instant carry on instead of paying for it again. The
        wait then doubles past any bench's poll interval, which is the test
        below.
        """
        module, labcore = congested()
        module._declare_tables(labcore.sql, NOW)
        fired = len(labcore.ddl())
        for offset in (0, 1, 2, 4):
            module._declare_tables(labcore.sql,
                                   NOW + timedelta(seconds=offset))
        assert len(labcore.ddl()) == fired, (
            f"{len(labcore.ddl()) - fired} more statements went to a LabCore "
            "that had just refused the last one")

    def test_the_wait_outgrows_the_poll_interval(self, congested):
        """Where "not on every poll" is actually delivered. A bench at the 30s
        default polls twice a minute; once the wait has doubled past that, a
        refusing LabCore hears from this road at most once a minute however
        often the bench polls."""
        module, labcore = congested()
        at = NOW
        for _ in range(6):                 # walk the wait up to the cap
            module._declare_tables(labcore.sql, at)
            at = at + timedelta(seconds=mod.DECLARE_RETRY_MAX_SECONDS)
        fired = len(labcore.ddl())
        for poll in range(1, 21):          # ten minutes at the 30s default
            module._declare_tables(labcore.sql,
                                   at + timedelta(seconds=30 * poll))
        assert len(labcore.ddl()) - fired == 10, (
            f"{len(labcore.ddl()) - fired} statements in twenty polls against a "
            "refusing LabCore — at a one-minute cap it is ten")

    def test_it_is_retried_once_the_wait_has_elapsed(self, congested):
        """Backing off is not giving up. The tables genuinely may not exist."""
        module, labcore = congested()
        module._declare_tables(labcore.sql, NOW)
        fired = len(labcore.ddl())
        module._declare_tables(
            labcore.sql, NOW + timedelta(seconds=mod.DECLARE_RETRY_SECONDS))
        assert len(labcore.ddl()) > fired, (
            "the backoff elapsed and the bench never asked again")

    def test_the_wait_doubles_on_repeated_refusals(self, congested):
        """Five, then ten, then twenty. The reason the first attempt failed is
        usually that the queue is congested — and ten benches asking every five
        seconds for ever would be the congestion."""
        module, labcore = congested()
        at = NOW
        for wait in (5, 10, 20):
            module._declare_tables(labcore.sql, at)
            fired = len(labcore.ddl())
            module._declare_tables(labcore.sql,
                                   at + timedelta(seconds=wait - 1))
            assert len(labcore.ddl()) == fired, (
                f"asked again {wait - 1}s into a {wait}s wait")
            at = at + timedelta(seconds=wait)

    def test_the_wait_is_capped(self, congested):
        """Unbounded doubling is a bench that stops asking for a whole shift."""
        module, labcore = congested()
        at = NOW
        for _ in range(20):
            module._declare_tables(labcore.sql, at)
            at = at + timedelta(seconds=mod.DECLARE_RETRY_MAX_SECONDS)
        fired = len(labcore.ddl())
        module._declare_tables(
            labcore.sql, at + timedelta(seconds=mod.DECLARE_RETRY_MAX_SECONDS))
        assert len(labcore.ddl()) > fired, (
            "the wait grew past the cap and the bench went quiet")

    def test_labcore_s_own_retry_after_is_honoured(self, congested):
        """It said how long it wants. Asking sooner is the pattern notes.md's
        standing rule exists to stop; asking later than the doubling schedule
        would have is LabCore's call to make, not this module's."""
        module, labcore = congested()
        labcore.refusal = {"error": "busy", "busy": True, "retry_after": 30}
        module._declare_tables(labcore.sql, NOW)
        fired = len(labcore.ddl())
        module._declare_tables(labcore.sql, NOW + timedelta(seconds=29))
        assert len(labcore.ddl()) == fired, (
            "LabCore asked for 30 seconds and got asked again after 29")
        module._declare_tables(labcore.sql, NOW + timedelta(seconds=30))
        assert len(labcore.ddl()) > fired, (
            "LabCore's own wait elapsed and the bench never asked again")

    def test_a_clock_that_steps_backwards_does_not_park_the_declaration(
            self, congested):
        """Naive local `datetime.now()` on a bench PC: DST fall-back repeats an
        hour and NTP steps the clock back whenever it likes. Negative elapsed
        time is not "recently refused", it is arithmetic that has stopped
        meaning anything — the same rule as `_config_due` and
        `_corrections_due`."""
        module, labcore = congested()
        module._declare_tables(labcore.sql, NOW)
        fired = len(labcore.ddl())
        module._declare_tables(labcore.sql, NOW - timedelta(minutes=45))
        assert len(labcore.ddl()) > fired, (
            "the clock stepped back and the bench stopped declaring its tables")

    def test_a_success_latches_the_flag_and_costs_nothing_after(self,
                                                                congested):
        module, labcore = congested()
        module._declare_tables(labcore.sql, NOW)
        labcore.busy = False
        module._declare_tables(labcore.sql, NOW + timedelta(seconds=60))
        assert module._labcore_table_ready is True
        fired = len(labcore.ddl())
        # The one statement the congested LabCore turned away, plus the whole
        # block once it cleared: every table AND every index. Counting the
        # indexes here is the point — a block that latched its flag having
        # declared only the tables would pass a bare `len(ALL_TABLES) + 1`.
        assert fired == 1 + len(ALL_TABLES) + len(mod.LOG_INDEX_DDL), labcore.ddl()
        for beat in range(5):
            module._declare_tables(labcore.sql,
                                   NOW + timedelta(seconds=120 + 60 * beat))
        assert len(labcore.ddl()) == fired

    def test_a_success_resets_the_backoff(self, congested):
        """A LabCore that clears, congests again and clears again must not be
        served by a wait inherited from the last bad patch — the module would
        sit out a minute of a queue that is fine."""
        module, labcore = congested()
        for step in range(4):                       # walk the wait up to 40s
            module._declare_tables(labcore.sql,
                                   NOW + timedelta(seconds=100 * step))
        labcore.busy = False
        module._declare_tables(labcore.sql, NOW + timedelta(seconds=400))
        assert module._labcore_table_ready is True

        module._labcore_table_ready = False         # a fresh road, same module
        labcore.busy = True
        module._declare_tables(labcore.sql, NOW + timedelta(seconds=500))
        fired = len(labcore.ddl())
        module._declare_tables(labcore.sql, NOW + timedelta(seconds=505))
        assert len(labcore.ddl()) > fired, (
            "the backoff was still where the last congestion left it")

    def test_a_raise_still_leaves_the_flag_down(self, congested):
        """Unchanged: an exception is not a refusal and is not swallowed here.
        Every caller already runs this inside its own try."""
        module, labcore = congested()

        def explode(sql, args=None, source="LabStation", timeout=None):
            raise RuntimeError("queue full")

        with pytest.raises(RuntimeError):
            module._declare_tables(explode, NOW)
        assert module._labcore_table_ready is False

    def test_a_refused_index_backs_off_exactly_like_a_refused_table(
            self, congested):
        """The indexes are new arrivals in a block whose whole discipline is
        "a refused declaration is not a declaration, and a refusal is not free
        to repeat". An index is DDL like any other: one queue op, refusable by
        a congested LabCore, and — on a log that has grown over SMB for a year
        — the most expensive statement in the block. It must not be the one
        that slips through the backoff.

        `refuse_after` is set past the seven tables so the statement turned
        away is an INDEX, not a table.
        """
        module, labcore = congested()
        labcore.refuse_after = len(ALL_TABLES)      # the first index is refused
        module._declare_tables(labcore.sql, NOW)
        assert module._labcore_table_ready is False, (
            "the flag latched over an index LabCore had refused")
        fired = len(labcore.ddl())
        assert fired == len(ALL_TABLES) + 1, labcore.ddl()
        for offset in (0, 1, 2, 4):
            module._declare_tables(labcore.sql, NOW + timedelta(seconds=offset))
        assert len(labcore.ddl()) == fired, (
            "a refused INDEX was re-fired inside the wait a refused TABLE buys")

    def test_a_refused_index_is_the_last_statement_fired(self, congested):
        """The same rule the tables have: the queue has just said it is full,
        so the remaining statements are work aimed at the congestion being
        reported."""
        module, labcore = congested()
        labcore.refuse_after = len(ALL_TABLES)
        module._declare_tables(labcore.sql, NOW)
        assert len(labcore.ddl()) == len(ALL_TABLES) + 1, labcore.ddl()

    def test_a_refused_index_is_tried_again_once_the_wait_elapses(
            self, congested):
        """Backing off is not giving up — and an index that was never created
        is the unindexed scan this whole change exists to remove."""
        module, labcore = congested()
        labcore.refuse_after = len(ALL_TABLES)
        module._declare_tables(labcore.sql, NOW)
        fired = len(labcore.ddl())
        module._declare_tables(
            labcore.sql, NOW + timedelta(seconds=mod.DECLARE_RETRY_SECONDS))
        assert len(labcore.ddl()) > fired, (
            "the backoff elapsed and the bench never asked for its index again")

    def test_the_congested_queue_gets_a_fraction_of_the_load(self, congested):
        """The number that matters, at the bench's real cadence. Five minutes
        of a 12-second poll against a LabCore that is refusing throughout."""
        module, labcore = congested()
        for second in range(0, 300, 12):
            module._declare_tables(labcore.sql, NOW + timedelta(seconds=second))
        assert len(labcore.ddl()) < 25, (
            f"{len(labcore.ddl())} statements into a refusing queue in five "
            "minutes, from one bench")
