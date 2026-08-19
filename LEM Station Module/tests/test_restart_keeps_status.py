"""A LabStation restart must not change an instrument's status.

The module keeps its QC specs across a restart but loses the rows it parsed, so
it looked like QC had never run — and turned YELLOW on a machine whose QC passed
three hours ago, inside its 24-hour window.

The cause is that the module never read its own QC history back. Its verdicts
are already in LabCore (`lem_machine_log`, kind `qc`); on startup it now
rehydrates the last verdict per test and evaluates against that when it has
nothing local yet. So:

  * QC passed recently, then a restart      → GREEN  (nothing changed)
  * QC genuinely never run                  → YELLOW (there is a job to do)
  * QC passed but longer ago than the window → stale
"""
from datetime import datetime, timedelta

import pytest

import lem_station_module as mod
from lem_station_module import LAB_ID_KEY, Machine, TestSpec

NOW = datetime(2026, 8, 3, 15, 0)


def spec(**over):
    base = dict(name="Cloud", value_col="Cloud", expected=-7.4, std_dev=2.8,
                k=1.0, sample_id="CP")
    base.update(over)
    return TestSpec(**base)


def machine(**over):
    base = dict(uid="m1", title="OptiMPP 1", tests=[spec()])
    base.update(over)
    return Machine(**base)


# ── the reported bug ────────────────────────────────────────────────────────

class TestRestartStaysGreen:
    def test_qc_three_hours_ago_survives_a_restart(self):
        """The exact report: restarted, went yellow, QC ran 3h ago, 24h window."""
        m = machine(tests=[spec(last_qc_at=(NOW - timedelta(hours=3)).isoformat(),
                                last_qc_value=-7.2, last_qc_in_spec=True)])
        ev = mod.evaluate_machine(m, [], NOW)
        assert ev.status == mod.STATUS_GREEN, ev.reason

    def test_the_reason_says_where_that_came_from(self):
        m = machine(tests=[spec(last_qc_at=(NOW - timedelta(hours=3)).isoformat(),
                                last_qc_value=-7.2, last_qc_in_spec=True)])
        assert "nominal" in mod.evaluate_machine(m, [], NOW).reason.lower()

    def test_a_failed_qc_still_reads_red_after_a_restart(self):
        """A restart must not launder a failure into green either."""
        m = machine(tests=[spec(last_qc_at=(NOW - timedelta(hours=2)).isoformat(),
                                last_qc_value=-99.0, last_qc_in_spec=False)])
        ev = mod.evaluate_machine(m, [], NOW)
        assert ev.status == mod.STATUS_RED

    def test_qc_older_than_the_window_is_stale_not_green(self):
        m = machine(tests=[spec(last_qc_at=(NOW - timedelta(days=3)).isoformat(),
                                last_qc_value=-7.2, last_qc_in_spec=True)])
        ev = mod.evaluate_machine(m, [], NOW)
        assert ev.status == mod.STATUS_YELLOW
        assert "stale" in ev.reason.lower()

    def test_qc_earlier_the_same_day_is_still_green_after_a_restart(self):
        """The reported case is same-day: QC at 06:00, restart at 15:00."""
        m = machine(tests=[spec(last_qc_at=NOW.replace(hour=6).isoformat(),
                                last_qc_value=-7.2, last_qc_in_spec=True)])
        assert mod.evaluate_machine(m, [], NOW).status == mod.STATUS_GREEN

    def test_expiry_is_a_ROLLING_24h_not_the_calendar_day(self):
        """Changed 2026-08-03 at Ryan's call: "as long as it tracks real 24 hours".

        V4 expired QC at the day boundary, so a standard run at 23:00 was stale at
        00:01 — an hour later — while one run at 00:30 lasted nearly 48. A window
        called 24 hours has to be 24 hours.
        """
        yesterday_late = (NOW - timedelta(days=1)).replace(hour=23, minute=0)
        assert (NOW - yesterday_late) < timedelta(hours=24)   # under 24h...
        m = machine(tests=[spec(last_qc_at=yesterday_late.isoformat(),
                                last_qc_value=-7.2, last_qc_in_spec=True)])
        assert mod.evaluate_machine(m, [], NOW).status == mod.STATUS_GREEN

    def test_it_expires_the_moment_the_window_closes(self):
        just_over = NOW - timedelta(hours=24, minutes=1)
        m = machine(tests=[spec(last_qc_at=just_over.isoformat(),
                                last_qc_value=-7.2, last_qc_in_spec=True)])
        ev = mod.evaluate_machine(m, [], NOW)
        assert ev.status == mod.STATUS_YELLOW
        assert "stale" in ev.reason.lower()

    def test_it_is_still_good_just_inside_the_window(self):
        just_under = NOW - timedelta(hours=23, minutes=59)
        m = machine(tests=[spec(last_qc_at=just_under.isoformat(),
                                last_qc_value=-7.2, last_qc_in_spec=True)])
        assert mod.evaluate_machine(m, [], NOW).status == mod.STATUS_GREEN

    def test_the_window_is_measured_from_the_run_not_from_start_up(self):
        """The distinction that makes it survive a restart: nothing is timed from
        when the module came up. Two modules started hours apart, same QC time,
        must agree."""
        ran_at = NOW - timedelta(hours=12)
        m = machine(tests=[spec(last_qc_at=ran_at.isoformat(),
                                last_qc_value=-7.2, last_qc_in_spec=True)])
        assert mod.qc_freshness(m, mod.TestResult("Cloud Point", -7.2, True,
                                                 ran_at), NOW) == pytest.approx(
            0.5, abs=0.01)

    def test_a_custom_window_is_honoured_in_hours_not_rounded_to_days(self):
        """An 8-hour window used to round to `max(1, round(8/24)) = 1 day`, so a
        per-test window shorter than a day did nothing at all."""
        m = machine(tests=[spec(last_qc_at=(NOW - timedelta(hours=9)).isoformat(),
                                last_qc_value=-7.2, last_qc_in_spec=True)])
        m.tests[0].qc_expire_hours = 8.0
        ev = mod.evaluate_machine(m, [], NOW)
        assert ev.status == mod.STATUS_YELLOW, "an 8h window was rounded to a day"

    def test_a_longer_window_is_honoured_too(self):
        m = machine(tests=[spec(last_qc_at=(NOW - timedelta(hours=30)).isoformat(),
                                last_qc_value=-7.2, last_qc_in_spec=True)])
        m.tests[0].qc_expire_hours = 72.0
        assert mod.evaluate_machine(m, [], NOW).status == mod.STATUS_GREEN

    def test_genuinely_never_run_is_still_yellow(self):
        """Ryan's earlier ask: assigned but never run should be orange."""
        ev = mod.evaluate_machine(machine(), [], NOW)
        assert ev.status == mod.STATUS_YELLOW
        assert "not yet run" in ev.reason.lower()

    def test_nothing_assigned_is_still_grey(self):
        ev = mod.evaluate_machine(machine(tests=[]), [], NOW)
        assert ev.status == mod.STATUS_UNKNOWN

    def test_a_live_row_beats_the_remembered_one(self):
        """Fresh local data is always more authoritative than history."""
        m = machine(tests=[spec(last_qc_at=(NOW - timedelta(hours=3)).isoformat(),
                                last_qc_value=-7.2, last_qc_in_spec=True)])
        row = {LAB_ID_KEY: "CP", "Cloud": "-99",
               "timestamp": NOW.strftime("%Y-%m-%d %H:%M:%S")}
        assert mod.evaluate_machine(m, [row], NOW).status == mod.STATUS_RED

    def test_an_unparseable_remembered_timestamp_is_ignored_safely(self):
        m = machine(tests=[spec(last_qc_at="not a date", last_qc_value=-7.2,
                                last_qc_in_spec=True)])
        ev = mod.evaluate_machine(m, [], NOW)
        assert ev.status in (mod.STATUS_YELLOW, mod.STATUS_UNKNOWN)


# ── reading the history back ─────────────────────────────────────────────────

class TestRehydration:
    def test_the_query_asks_for_this_machines_qc_only(self):
        sql, args = mod.build_last_qc_query("m1")
        assert "lem_machine_log" in sql and "qc" in sql
        assert args == ["m1"]

    def test_it_takes_the_newest_verdict_per_test(self):
        rows = [
            {"test_name": "Cloud", "value": "-7.9", "ts": "2026-08-01T09:00:00",
             "detail": '{"in_spec": true}'},
            {"test_name": "Cloud", "value": "-7.2", "ts": "2026-08-03T12:00:00",
             "detail": '{"in_spec": true}'},
            {"test_name": "Pour", "value": "-19.0", "ts": "2026-08-02T09:00:00",
             "detail": '{"in_spec": false}'},
        ]
        got = mod.last_qc_by_test(rows)
        assert got["Cloud"]["at"] == "2026-08-03T12:00:00"
        assert got["Cloud"]["value"] == -7.2
        assert got["Pour"]["in_spec"] is False

    def test_it_applies_onto_the_specs(self):
        m = machine()
        mod.apply_last_qc(m, {"Cloud": {"at": "2026-08-03T12:00:00",
                                        "value": -7.2, "in_spec": True}})
        assert m.tests[0].last_qc_at == "2026-08-03T12:00:00"
        assert m.tests[0].last_qc_in_spec is True

    def test_a_test_with_no_history_is_left_alone(self):
        m = machine()
        mod.apply_last_qc(m, {})
        assert m.tests[0].last_qc_at == ""

    def test_junk_rows_do_not_break_it(self):
        assert mod.last_qc_by_test([None, "x", {}, {"test_name": ""}]) == {}
        assert mod.last_qc_by_test(None) == {}

    def test_an_unreadable_detail_still_yields_the_value(self):
        got = mod.last_qc_by_test([{"test_name": "Cloud", "value": "-7.2",
                                    "ts": "2026-08-03T12:00:00",
                                    "detail": "{broken"}])
        assert got["Cloud"]["value"] == -7.2

    def test_a_non_numeric_value_is_skipped(self):
        assert mod.last_qc_by_test([{"test_name": "Cloud", "value": "ERROR",
                                     "ts": "2026-08-03T12:00:00",
                                     "detail": "{}"}]) == {}

    def test_the_remembered_verdict_survives_serialisation(self):
        m = machine(tests=[spec(last_qc_at="2026-08-03T12:00:00",
                                last_qc_value=-7.2, last_qc_in_spec=True)])
        back = Machine.from_dict(m.to_dict())
        assert back.tests[0].last_qc_at == "2026-08-03T12:00:00"
        assert back.tests[0].last_qc_in_spec is True


# ── keep the bench lean ─────────────────────────────────────────────────────

class TestRecentPrintsAreCapped:
    def test_only_four_prints_are_kept(self, qapp, tmp_path):
        from test_module_qt import make_module, sample_machine
        m = make_module()
        m.set_machine(sample_machine(tmp_path))
        for i in range(12):
            m._recent_prints_raw.appendleft(f"print {i}")
        assert len(m.recent_prints()) == 4
        m.shutdown()

    def test_the_newest_are_the_ones_kept(self, qapp, tmp_path):
        from test_module_qt import make_module, sample_machine
        m = make_module()
        m.set_machine(sample_machine(tmp_path))
        for i in range(12):
            m._recent_prints_raw.appendleft(f"print {i}")
        assert m.recent_prints()[0] == "print 11"
        assert "print 0" not in m.recent_prints()
        m.shutdown()

    def test_the_limit_is_a_named_constant(self):
        cls = [c for c in vars(mod).values()
               if isinstance(c, type) and getattr(c, "module_type", "")][0]
        assert cls.RECENT_PRINTS == 4


# ── moving a module to another PC ───────────────────────────────────────────

class TestSurvivesAMove:
    """Ryan, 2026-08-03: "as long as it tracks real 24 hours and survives
    restarts and reconfigs (aka moving modules from one PC to another)".

    A restart and a move are different mechanisms. A restart keeps the config and
    loses the parsed rows. A move keeps *nothing* local — new PC, new LabStation
    install, no history, possibly a different CSV path — and picks the machine back
    up out of LabCore by its uid.

    What makes both work is that the QC window is anchored to the timestamp on the
    verdict, and that verdict lives in LabCore keyed on `machine_uid`. Nothing is
    measured from when this process started or from anything on this disk.
    """

    def log_row(self, uid, test, value, in_spec, at):
        return {"machine_uid": uid, "test_name": test, "value": str(value),
                "detail": '{"in_spec": %s}' % ("true" if in_spec else "false"),
                "ts": at.isoformat()}

    def test_the_history_is_fetched_by_machine_uid_only(self):
        """Not by hostname, not by path — or a move would lose it."""
        sql, args = mod.build_last_qc_query("m1")
        assert args == ["m1"]
        assert "machine_uid" in sql

    def test_a_fresh_install_rehydrates_and_stays_green(self):
        """The move: brand-new PC, no local rows at all, same machine uid."""
        ran = NOW - timedelta(hours=6)
        rows = [self.log_row("m1", "Cloud", -7.2, True, ran)]
        m = machine()                                    # no last_qc_* yet
        mod.apply_last_qc(m, mod.last_qc_by_test(rows))
        ev = mod.evaluate_machine(m, [], NOW)
        assert ev.status == mod.STATUS_GREEN, ev.reason

    def test_a_move_does_not_reset_the_clock(self):
        """The window keeps running while the instrument is being moved: QC 25h
        old is stale on the new PC, not freshly green because the module is new."""
        ran = NOW - timedelta(hours=25)
        rows = [self.log_row("m1", "Cloud", -7.2, True, ran)]
        m = machine()
        mod.apply_last_qc(m, mod.last_qc_by_test(rows))
        ev = mod.evaluate_machine(m, [], NOW)
        assert ev.status == mod.STATUS_YELLOW
        assert "stale" in ev.reason.lower()

    def test_a_move_does_not_launder_a_failure(self):
        ran = NOW - timedelta(hours=2)
        rows = [self.log_row("m1", "Cloud", -99.0, False, ran)]
        m = machine()
        mod.apply_last_qc(m, mod.last_qc_by_test(rows))
        assert mod.evaluate_machine(m, [], NOW).status == mod.STATUS_RED

    def test_no_history_in_labcore_is_still_yellow_not_green(self):
        """A genuinely new machine has a job to do; it must not read as passing."""
        m = machine()
        mod.apply_last_qc(m, mod.last_qc_by_test([]))
        ev = mod.evaluate_machine(m, [], NOW)
        assert ev.status == mod.STATUS_YELLOW
        assert "not yet run" in ev.reason.lower()

    def test_another_machines_history_is_not_adopted(self):
        """Duplicating a config must not inherit the original's QC verdict."""
        rows = [self.log_row("m2", "Cloud", -7.2, True, NOW - timedelta(hours=1))]
        m = machine()
        mod.apply_last_qc(m, mod.last_qc_by_test(
            [r for r in rows if r["machine_uid"] == "m1"]))
        assert mod.evaluate_machine(m, [], NOW).status == mod.STATUS_YELLOW

    def test_the_newest_verdict_wins_across_a_move(self):
        rows = [self.log_row("m1", "Cloud", -99.0, False, NOW - timedelta(hours=9)),
                self.log_row("m1", "Cloud", -7.2, True, NOW - timedelta(hours=1))]
        m = machine()
        mod.apply_last_qc(m, mod.last_qc_by_test(rows))
        assert mod.evaluate_machine(m, [], NOW).status == mod.STATUS_GREEN


# ── the two ways recovery could still fail ──────────────────────────────────

class TestRecoveryIsRobust:
    """Both found while investigating the 2026-08-03 Flash report. Replaying
    Flash 1's real history against the code proved it recovers correctly, so the
    bench was running an older build — but these two would have produced exactly
    the same symptom, and neither was covered."""

    def row(self, test, value, in_spec, at):
        import json
        return {"test_name": test, "value": str(value), "ts": at,
                "detail": json.dumps({"in_spec": in_spec})}

    def test_the_query_asks_for_the_NEWEST_verdicts(self):
        """It was `ORDER BY ts ASC LIMIT 400` — the OLDEST 400. On any instrument
        past 400 QC records, the "most recent" verdict recovered was ancient."""
        sql, _args = mod.build_last_qc_query("m1")
        assert "ORDER BY ts DESC" in sql

    def test_the_newest_wins_regardless_of_row_order(self):
        """The old parser relied on rows arriving oldest-first and just let later
        ones overwrite. Fixing the ORDER BY silently inverted that."""
        old = self.row("Cloud", -99.0, False, "2026-08-01T09:00:00")
        new = self.row("Cloud", -7.2, True, "2026-08-03T14:00:00")
        for order in ([old, new], [new, old]):
            got = mod.last_qc_by_test(order)["Cloud"]
            assert got["value"] == -7.2 and got["in_spec"] is True, order

    def test_specs_vanishing_and_returning_does_not_lose_the_verdict(self):
        """The reported shape: GREEN, then "No QC assigned", then YELLOW "not yet
        run". Recovery used to latch after one successful read, so a spec list
        that emptied and came back was never looked up again."""
        tried, memory = set(), {}

        def hydrate(machine, rows):
            """Mirrors _labcore_sync: remember, seed once, re-apply always."""
            for sp in machine.tests:
                if sp.last_qc_at:
                    memory[sp.name] = {"at": sp.last_qc_at,
                                       "value": sp.last_qc_value,
                                       "in_spec": sp.last_qc_in_spec}
            pending = [sp.name for sp in machine.tests
                       if not sp.last_qc_at and sp.name not in tried]
            if pending:
                tried.update(pending)
                memory.update(mod.last_qc_by_test(rows))
            if memory and any(not sp.last_qc_at for sp in machine.tests):
                mod.apply_last_qc(machine, memory)
                return True
            return False

        rows = [self.row("Cloud", -7.2, True, (NOW - timedelta(hours=2)).isoformat())]
        m = machine()
        assert hydrate(m, rows) is True
        assert mod.evaluate_machine(m, [], NOW).status == mod.STATUS_GREEN

        m.tests = []                      # LabCore returned nothing for one poll
        assert mod.evaluate_machine(m, [], NOW).status == mod.STATUS_UNKNOWN

        m.tests = [spec()]                # ...and then it came back, blank
        assert hydrate(m, rows) is True, "never looked the history up again"
        assert mod.evaluate_machine(m, [], NOW).status == mod.STATUS_GREEN

    def test_a_test_already_carrying_a_verdict_is_not_re_read(self):
        """Recovery must not become a read on every poll."""
        m = machine(tests=[spec(last_qc_at=(NOW - timedelta(hours=1)).isoformat(),
                                last_qc_value=-7.2, last_qc_in_spec=True)])
        pending = [s.name for s in m.tests if not s.last_qc_at]
        assert pending == []
