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


def ddl_count(sqls):
    return len([s for s in sqls if "CREATE TABLE" in str(s).upper()])


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
        for name in ALL_TABLES:
            assert name in guarded, f"{name} is not declared in the one-time block"

    def test_the_sync_goes_through_the_shared_block(self):
        import inspect
        src = inspect.getsource(mod.LEMStationModule._labcore_sync)
        assert "self._declare_tables(run_sql)" in src
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
        assert "self._declare_tables(run_sql)" in src

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
        note left five tables undeclared and believed otherwise."""
        module, counter = bench()
        module._flush_events_worker()
        assert module._labcore_table_ready is True
        assert ddl_count(counter.sqls) == len(ALL_TABLES)


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
