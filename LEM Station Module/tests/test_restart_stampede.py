"""A restarted bench must not stampede LabCore on its first poll.

The config road removed the per-bench polling load: from the SECOND poll onward
an idle bench asks LabCore for nothing, because it reads its configuration from
the floor's web server instead. The first poll of a module's life was still
paying the full price — six config reads plus one `lem_meta` read, seven slots
in a queue that serialises reads AND writes at about 1.5 ops/sec.

Per bench that is a rounding error. **Floor-wide it is the failure mode.** The
one moment every module in the building starts at once is a LabStation restart
after a power event or a deploy, and that is also the moment LabCore's queue is
deepest — every bench replaying its held results at the same time. The single
poll this file is about is therefore the worst-timed load the lab generates.

Two things cost it, and both are fixed here:

  * **The bench could not find the floor without asking LabCore.** `live_url`
    and `live_token` live in `lem_meta`, so a bench with nothing cached had to
    spend a queue slot before it could even address the server that would have
    answered the other six for free. They are now saved in the module's own
    state alongside `machine_uid` and `poll_seconds`.

  * **Health could not be earned in time.** `_live_channel_healthy()` requires
    `_live_delivering`, which only a push that came back speaking the note
    protocol can raise — and the push happens at the END of `_process_outcome`,
    after `_labcore_sync` has already made its read decisions. So poll 1 fell
    back to LabCore even with a healthy floor one hop away. The module now
    knocks ONCE at boot, on the worker, before any read decision is made.

**What must not change is the safety property**, and section (C) is the test
that matters most in this file. Health is still EARNED by a real push that came
back carrying a `stale` list. A cached URL is an ADDRESS, never evidence that
anything is listening on it, and least of all evidence that whatever is
listening speaks the note protocol. An un-upgraded floor answers `/api/live`
with a bodyless 204 — a push that landed, carrying no note and never able to
carry one — and treating that as health is precisely the version-skew hole that
suppressed the manual override read for fifteen minutes against an old server.
The override is the lever that takes a bench OFF LINE. Re-opening that hole to
save one read would be a bad trade at any price.

Everything here also has to hold the existing lines: the worker never raises,
only `_show_outcome` touches widgets and timers, a floor that is not there costs
the bench nothing, and the note channel may never drive the bench in a loop.
"""
from datetime import datetime, timedelta

import pytest

import lem_station_module as mod
from lem_station_module import Machine

from test_module_qt import make_module
from test_floor_config import LabCore, FloorServer, FLOOR, UID

NOW = datetime(2026, 8, 26, 12, 0, 0)
MOVED = "http://10.0.0.9:5557"

# Everything an idle bench used to ask LabCore for on its first poll. Measured
# on the pristine module: six config reads plus the `lem_meta` read that told it
# where the floor was, all seven in the one serialised queue.
FULL_PRICE = ["corrections", "qc_samples", "targets", "qc_specs", "maint",
              "override"]


# `None` is a MEANINGFUL answer from `post_live` — it is what a floor that did
# not answer looks like — so it cannot double as "use the default".
_DEFAULT = object()


class Floor:
    """The web server on the other end of `POST /api/live`.

    `answer` is what it says; a callable is handed the push number so a floor
    can be dark at boot and awake by poll 2, or the other way round. The three
    answers that matter each have a name in the lab:

        {"stale": [...]}  an upgraded floor speaking the note protocol
        {}                an UN-UPGRADED floor: a bodyless 204, a push that
                          landed carrying no note and never able to carry one
        None              no floor at all — refused, timed out, not there
    """

    def __init__(self, answer=_DEFAULT):
        self.answer = {"stale": []} if answer is _DEFAULT else answer
        self.pushes = []

    def post(self, url, token, payload, timeout=None):
        self.pushes.append((url, token, payload, timeout))
        answer = self.answer
        return answer(len(self.pushes)) if callable(answer) else answer


@pytest.fixture
def bench(qapp, monkeypatch):
    """A module wired to a counting LabCore and a fake floor, nothing preset.

    Deliberately NOT pre-flagged healthy, unlike the fixture in
    `test_floor_config.py`. This file is about the boot itself, so every flag
    the module ends up with has to be one it earned during the test.
    """

    def build(labcore, floor=None, config=None):
        monkeypatch.setitem(mod.__dict__, "labcore_read_sql", labcore.read_sql)
        monkeypatch.setitem(mod.__dict__, "labcore_sql", labcore.sql)
        monkeypatch.setitem(mod.__dict__, "labcore_write", labcore.write)
        monkeypatch.setattr(mod, "_in_thread", lambda fn, cb: cb(fn()))
        if floor is not None:
            monkeypatch.setattr(mod, "post_live", floor.post)
        monkeypatch.setattr(mod, "fetch_floor_config",
                            (config or FloorServer()).fetch)
        return make_module()
    return build


def saved(url=FLOOR, token="tok", **extra):
    """What LabStation hands a module it is re-creating from the canvas."""
    state = {"machine_uid": UID, "poll_seconds": 30}
    if url is not None:
        state["live_url"] = url
        state["live_token"] = token
    state.update(extra)
    return state


def boot(module, state):
    """LabStation restoring the module, then LabCore handing its config back.

    `set_machine(publish=False)` is exactly what `_apply_pulled_config` does
    once the binding read answers — the real boot path, so the stamps this
    starts with are the ones a real restarted bench starts with.
    """
    module.restore_state(state)
    module.set_machine(Machine(uid=UID, title="Eraspec", source_type="manual"),
                       publish=False)
    return module


def poll(module, now):
    """One whole worker-half poll, with the clock injected."""
    return module._process_outcome(module._machine, [], None, [], now)


def dispatch(module):
    """A whole poll INCLUDING the main-thread half, which is where a follow-up
    poll would be arranged from."""
    machine = module._machine
    module._dispatch_pipeline(lambda: (machine, [], None))


# ── (A) The address survives the restart ────────────────────────────────────
#
# `serialize_state` deliberately keeps almost nothing: the configuration lives
# in LabCore so a LabStation reinstall cannot lose it. The floor's ADDRESS is
# not configuration in that sense — it is the thing the bench needs in order to
# ask anybody anything, and having to spend a LabCore read to learn it is the
# bootstrap problem this whole road exists to remove.


class TestTheFloorsAddressIsRememberedAcrossARestart:
    def test_serialize_carries_the_address_and_token(self, bench):
        labcore = LabCore()
        module = bench(labcore, Floor())
        module._live_url = FLOOR
        module._live_token = "tok"
        state = module.serialize_state()
        assert state["live_url"] == FLOOR
        assert state["live_token"] == "tok"

    def test_the_state_is_still_json(self, bench):
        """LabStation writes the canvas file as JSON. A value that cannot be
        serialised does not lose the live config — it loses the whole canvas."""
        import json
        labcore = LabCore()
        module = bench(labcore, Floor())
        module._live_url = FLOOR
        module._live_token = "tok"
        json.dumps(module.serialize_state())

    def test_restore_puts_them_back(self, bench):
        module = bench(LabCore(), Floor())
        boot(module, saved())
        assert module._live_url == FLOOR
        assert module._live_token == "tok"

    def test_a_round_trip_preserves_them(self, bench):
        module = bench(LabCore(), Floor())
        module._live_url = FLOOR
        module._live_token = "tok"
        second = bench(LabCore(), Floor())
        second.restore_state(module.serialize_state())
        second._stop_bind_retry()
        assert (second._live_url, second._live_token) == (FLOOR, "tok")

    def test_a_canvas_saved_before_this_change_still_loads(self, bench):
        """The whole installed base on the day this ships. An old canvas has no
        `live_url` key at all, and it must behave EXACTLY as it does today —
        no address, so the first push reads `lem_meta` as it always has."""
        module = bench(LabCore(), Floor())
        boot(module, saved(url=None))
        assert module._live_url == ""
        assert module._live_token == ""
        assert module.machine().uid == UID

    def test_an_empty_dict_still_loads(self, bench):
        module = bench(LabCore(), Floor())
        module.restore_state({})
        assert module._live_url == ""

    def test_a_restored_address_is_not_re_read_from_labcore(self, bench):
        """The bootstrap read is the point. A restored address that still had
        to be confirmed against `lem_meta` would save nothing — the queue slot
        is the cost, not the string."""
        labcore = LabCore()
        module = bench(labcore, Floor())
        boot(module, saved())
        assert module._live_config() == (FLOOR, "tok")
        assert labcore.count("live_config") == 0, (
            "the bench asked LabCore for an address it had already saved")

    def test_a_trailing_slash_is_normalised_like_a_fresh_read(self, bench):
        """`parse_live_config` strips it, so a restored value must be stripped
        too — otherwise the config GET builds `...//api/bench/...` and a
        restarted bench silently never gets an answer."""
        module = bench(LabCore(), Floor())
        boot(module, saved(url=FLOOR + "/"))
        assert module._live_url == FLOOR


# ── (B) The channel is proven BEFORE the first read decision ────────────────


class TestTheBootProbeEarnsTheChannelInTime:
    def test_a_restarted_bench_costs_zero_labcore_config_reads(self, bench):
        """THE defect. Poll 1 of a restarted bench against a healthy floor.

        Floor-wide this is the difference between a restart the queue absorbs
        and a restart that takes LabCore down at the moment every bench is also
        replaying its held results.
        """
        labcore, floor = LabCore(), Floor()
        module = boot(bench(labcore, floor), saved())
        poll(module, NOW)
        assert labcore.config_reads() == [], (
            "the first poll after a restart still went to LabCore for "
            "configuration a healthy floor was ready to answer for free")
        assert labcore.count("live_config") == 0, (
            "the bench spent a queue slot on `lem_meta` just to learn an "
            "address it had saved")

    def test_and_the_only_reads_left_are_the_results_road(self, bench):
        """What a restarted bench still costs LabCore, stated exactly.

        The config road is at zero; these two are not configuration and are
        out of this defect's scope. They are the held-results queue
        (`lem_held_results`) and the QC-freshness lookback over
        `lem_machine_log` — both of them about READINGS, both of them on every
        poll of every bench today, and neither one something the floor's
        snapshot is offered as a substitute for. Named here so that a future
        change which quietly adds a seventh read to the first poll of a
        floor-wide restart has to come and edit this list.
        """
        labcore, floor = LabCore(), Floor()
        module = boot(bench(labcore, floor), saved())
        bound = list(labcore.reads)             # the one-off binding read
        poll(module, NOW)
        assert labcore.reads[len(bound):] == ["other", "other"], (
            f"poll 1 asked LabCore for {labcore.reads[len(bound):]}")

    def test_the_probe_is_one_push_and_nothing_else(self, bench):
        """One HTTP call to a server on the same LAN. If proving the channel
        cost a LabCore op it would be the very load it exists to remove."""
        labcore, floor = LabCore(), Floor()
        module = boot(bench(labcore, floor), saved())
        before = list(labcore.reads)
        module._probe_live_channel(module._machine)
        assert len(floor.pushes) == 1
        assert labcore.reads == before, "the boot probe cost a LabCore op"

    def test_the_probe_goes_to_the_saved_address_with_the_saved_token(
            self, bench):
        labcore, floor = LabCore(), Floor()
        module = boot(bench(labcore, floor), saved())
        module._probe_live_channel(module._machine)
        url, token, payload, _ = floor.pushes[0]
        assert (url, token) == (FLOOR, "tok")
        assert payload["machine_uid"] == UID

    def test_the_probe_claims_no_status_it_has_not_measured(self):
        """The bench has not polled anything yet, so it has no status to
        report. The floor's `merge_machines` falls back to the LabCore record
        for an entry with an empty status — so a knock carrying none is
        harmless, while a knock carrying an invented one would repaint the
        floor with a fact no bench ever measured."""
        assert mod.build_live_probe(Machine(uid=UID)) == {"machine_uid": UID}

    def test_the_probe_earns_health_before_the_sync_reads(self, bench):
        labcore, floor = LabCore(), Floor()
        module = boot(bench(labcore, floor), saved())
        assert module._live_channel_healthy() is False
        module._probe_live_channel(module._machine)
        assert module._live_channel_healthy() is True

    def test_it_runs_before_any_read_decision_in_the_poll(self):
        """Order is the whole fix. `_refresh_corrections` runs at the TOP of
        `_process_outcome` and `_labcore_sync` after it; a probe placed after
        either one proves the channel for the poll that has already paid."""
        import inspect
        source = inspect.getsource(mod.LEMStationModule._process_outcome)
        assert "_probe_live_channel" in source, (
            "nothing proves the channel inside the poll's worker half")
        assert (source.index("_probe_live_channel")
                < source.index("_corrections_due")), (
            "the probe runs after the corrections read has already decided")
        assert (source.index("_probe_live_channel")
                < source.index("_labcore_sync")), (
            "the probe runs after the sync has already made its read decisions")

    def test_it_is_the_worker_that_does_the_network(self):
        """Threading model: widgets and timers belong to the main thread, and a
        QTimer touched from a worker is undefined behaviour rather than a race
        that shows up in a log."""
        import inspect
        probe = inspect.getsource(mod.LEMStationModule._probe_live_channel)
        assert "QTimer" not in probe and "singleShot" not in probe
        assert "setText" not in probe and "_status_label" not in probe

    def test_the_probe_happens_once_in_a_module_life(self, bench):
        """A knock per poll would be a second push per bench for ever — half
        the traffic of the road it is helping, for no information after the
        first answer."""
        labcore, floor = LabCore(), Floor()
        module = boot(bench(labcore, floor), saved())
        for i in range(5):
            poll(module, NOW + timedelta(seconds=30 * i))
        assert len(floor.pushes) == 6, (
            f"{len(floor.pushes) - 6} extra pushes — one knock at boot plus "
            "one push per poll is the whole budget")

    def test_the_probe_never_raises(self, bench, monkeypatch):
        """The worker's rule. A raise here travels up, LabStation's
        `_run_in_thread` drops the callback, `_polling` is stranded True and
        the bench stops polling ALTOGETHER."""
        labcore = LabCore()
        module = boot(bench(labcore, None), saved())

        def boom(*args, **kwargs):
            raise RuntimeError("the floor is on fire")

        monkeypatch.setattr(mod, "post_live", boom)
        module._probe_live_channel(module._machine)      # must not raise
        assert module._live_channel_healthy() is False

    def test_a_bench_with_no_machine_yet_knocks_at_nobody(self, bench):
        """A module whose binding LabCore has not handed back yet. There is no
        uid to knock with, and `/api/live` refuses a push without one."""
        labcore, floor = LabCore(), Floor()
        module = bench(labcore, floor)
        module.restore_state(saved())
        module._stop_bind_retry()
        module._probe_live_channel(None)
        assert floor.pushes == []


class TestAFreshInstallBehavesExactlyAsBefore:
    """No cached address is the state of every bench today and of every new
    one for ever. It must cost what it costs now — no more, and above all no
    hang looking for a floor it cannot name."""

    def test_it_still_pays_the_full_price_on_poll_one(self, bench):
        labcore, floor = LabCore(), Floor()
        module = boot(bench(labcore, floor), saved(url=None))
        poll(module, NOW)
        assert labcore.config_reads() == FULL_PRICE
        assert labcore.count("live_config") == 1

    def test_it_knocks_at_nothing(self, bench):
        """There is no address to knock at, and inventing one — or reading
        `lem_meta` to find one — would be a LabCore op inside the thing whose
        whole point is to avoid one."""
        labcore, floor = LabCore(), Floor()
        module = boot(bench(labcore, floor), saved(url=None))
        before = list(labcore.reads)
        module._probe_live_channel(module._machine)
        assert floor.pushes == []
        assert labcore.reads == before

    def test_and_poll_two_uses_the_floor_exactly_as_today(self, bench):
        """The behaviour the config road already has, unchanged: poll 1 earns
        the channel with its own push, poll 2 is free."""
        labcore, floor = LabCore(), Floor()
        module = boot(bench(labcore, floor), saved(url=None))
        poll(module, NOW)
        before = list(labcore.config_reads())
        poll(module, NOW + timedelta(seconds=mod.CONFIG_REFRESH_SECONDS + 1))
        assert labcore.config_reads() == before


class TestAFloorThatIsNotThereCostsTheBenchNothing:
    def test_poll_one_falls_back_to_labcore(self, bench):
        """A saved address for a server that has been decommissioned, or a
        floor still booting when the benches come back. Falling back is always
        correct; hanging is not."""
        labcore, floor = LabCore(), Floor(answer=None)
        module = boot(bench(labcore, floor), saved())
        poll(module, NOW)
        assert labcore.config_reads() == FULL_PRICE, (
            "the floor never answered and the bench did not fall back")

    def test_the_channel_is_not_healthy(self, bench):
        labcore, floor = LabCore(), Floor(answer=None)
        module = boot(bench(labcore, floor), saved())
        module._probe_live_channel(module._machine)
        assert module._live_channel_healthy() is False

    def test_it_is_one_attempt_and_not_a_retry_storm(self, bench):
        """A dark floor multiplied by every bench in the building is the load
        pattern this road exists to prevent. One knock, then the ordinary
        push schedule."""
        labcore, floor = LabCore(), Floor(answer=None)
        module = boot(bench(labcore, floor), saved())
        for i in range(4):
            poll(module, NOW + timedelta(seconds=30 * i))
        assert len(floor.pushes) == 5, "one knock, then one push per poll"

    def test_the_knock_is_bounded_in_time(self):
        """It sits on the poll's critical path in front of a floor that may be
        rebooting. `post_live`'s own timeout is the bound, and the probe must
        not widen it."""
        import inspect
        assert mod.LIVE_TIMEOUT <= 2.0
        probe = inspect.getsource(mod.LEMStationModule._probe_live_channel)
        assert "timeout" not in probe, (
            "the boot probe overrides post_live's timeout")

    def test_a_floor_that_wakes_up_is_picked_up_by_the_ordinary_push(
            self, bench):
        """No special recovery path: the push at the end of every poll already
        earns the channel the moment the floor answers."""
        labcore = LabCore()
        floor = Floor(answer=lambda n: None if n < 2 else {"stale": []})
        module = boot(bench(labcore, floor), saved())
        poll(module, NOW)                       # probe dark, push earns it
        before = list(labcore.config_reads())
        poll(module, NOW + timedelta(seconds=mod.CONFIG_REFRESH_SECONDS + 1))
        assert labcore.config_reads() == before, (
            "the floor came back and the bench kept reading LabCore")


# ── (C) THE SAFETY PROPERTY — health is still earned, never assumed ─────────
#
# The most important tests in this file. A cached URL is an address; it is not
# evidence that anything is listening, and least of all evidence that whatever
# is listening speaks the note protocol.
#
# The bench and the floor are separate PCs and cannot be upgraded in the same
# instant, so there is ALWAYS a window in which an older floor answers
# `/api/live` with 204 and no body. That reaches the module as `{}` — a push
# that landed, correctly counted a success, carrying no note and never able to
# carry one. Health taken from anything less than a `stale` list therefore puts
# the manual override — the lever that takes a bench OFF LINE — behind a
# fifteen-minute window on a floor that has never heard of notes. Reproduced
# before `speaks_live_notes` existed: `_override_due` False at 60s, 300s, 899s.


class TestAnUnUpgradedFloorAtBootIsNotAHealthyChannel:
    def test_a_bodyless_answer_does_not_earn_health(self, bench):
        labcore, floor = LabCore(), Floor(answer={})
        module = boot(bench(labcore, floor), saved())
        module._probe_live_channel(module._machine)
        assert module._live_delivering is False
        assert module._live_channel_healthy() is False, (
            "a 204 from an un-upgraded floor was taken as a note channel — "
            "the override read is now suppressed against a server that will "
            "never send a note")

    def test_the_override_is_still_read_on_every_poll(self, bench):
        """The reproduction, at the three moments it was reproduced at."""
        labcore, floor = LabCore(), Floor(answer={})
        module = boot(bench(labcore, floor), saved())
        for seconds in (0, 60, 300, 899):
            poll(module, NOW + timedelta(seconds=seconds))
        assert labcore.count("override") == 4, (
            f"the override was read {labcore.count('override')} times in 4 "
            "polls against a floor that cannot send notes — a bench somebody "
            "took off line keeps running")

    def test_the_config_road_is_not_used_either(self, bench):
        """Same gate, deliberately. A floor too old to answer `/api/live` with
        a note is a floor that has never heard of `/api/bench/<uid>/config`
        either, and asking it costs a timeout on every poll of every bench
        before falling back anyway."""
        labcore, config = LabCore(), FloorServer()
        module = boot(bench(labcore, Floor(answer={}), config=config),
                      saved())
        poll(module, NOW)
        assert config.calls == []
        assert labcore.config_reads() == FULL_PRICE

    def test_the_push_itself_is_still_counted_a_success(self, bench):
        """`{}` is a push that LANDED. Counting it a failure would walk
        `_live_failures` to LIVE_RETRY_AFTER and re-read `lem_meta` out of the
        congested queue on every fourth poll of every bench — the exact load
        pattern this road exists to remove."""
        labcore, floor = LabCore(), Floor(answer={})
        module = boot(bench(labcore, floor), saved())
        module._probe_live_channel(module._machine)
        assert module._live_failures == 0

    def test_a_proxy_serving_a_login_page_is_not_a_channel(self, bench):
        """`post_live` reduces an unreadable body to `{}`, so this arrives the
        same way an un-upgraded floor does — and must be refused the same way.
        """
        labcore, floor = LabCore(), Floor(answer={"error": "Sign in"})
        module = boot(bench(labcore, floor), saved())
        module._probe_live_channel(module._machine)
        assert module._live_channel_healthy() is False

    def test_a_cached_address_alone_is_never_health(self, bench):
        """No push at all has been made. The saved address must not, on its
        own, open a single window."""
        module = boot(bench(LabCore(), Floor()), saved())
        assert module._live_url == FLOOR
        assert module._live_channel_healthy() is False
        assert module._override_due(NOW) is True

    def test_a_floor_that_stops_speaking_the_protocol_closes_it_again(
            self, bench):
        """Health earned at boot is not health for ever. A rollback mid-shift
        must re-open the override read on the next push."""
        labcore = LabCore()
        floor = Floor(answer=lambda n: {"stale": []} if n < 2 else {})
        module = boot(bench(labcore, floor), saved())
        module._probe_live_channel(module._machine)
        assert module._live_channel_healthy() is True
        poll(module, NOW)                       # the push that rolls back
        assert module._live_channel_healthy() is False
        assert module._override_due(NOW + timedelta(seconds=30)) is True


# ── (D) A moved server or a rotated token still heals by itself ─────────────
#
# The saved address is a CACHE, and a cache nobody can correct is worse than no
# cache at all: a bench pinned to a decommissioned address would sit there
# reading LabCore for ever and nothing would ever say why. The existing
# LIVE_RETRY_AFTER path re-reads `lem_meta` after consecutive failures, and it
# has to keep working with nothing typed at the bench.


class TestAStaleSavedAddressStillHeals:
    def test_it_fails_re_reads_and_recovers(self, bench, monkeypatch):
        labcore = LabCore(live_url=MOVED)       # `lem_meta` has the new one
        seen = []

        def post(url, token, payload, timeout=None):
            seen.append(url)
            return None if url != MOVED else {"stale": []}

        monkeypatch.setattr(mod, "post_live", post)
        module = boot(bench(labcore, None), saved(url=FLOOR))

        for i in range(mod.LIVE_RETRY_AFTER + 2):
            poll(module, NOW + timedelta(seconds=30 * i))

        assert FLOOR in seen, "the saved address was never tried"
        assert MOVED in seen, (
            "the saved address kept failing and the bench never went back to "
            "`lem_meta` — a moved server now needs somebody at every bench")
        assert labcore.count("live_config") >= 1
        assert module._live_url == MOVED
        assert module._live_channel_healthy() is True

    def test_a_rotated_token_heals_the_same_way(self, bench, monkeypatch):
        labcore = LabCore()                     # same url, `lem_meta` token
        tokens = []

        def post(url, token, payload, timeout=None):
            tokens.append(token)
            return None if token != "tok" else {"stale": []}

        monkeypatch.setattr(mod, "post_live", post)
        module = boot(bench(labcore, None), saved(token="expired"))

        for i in range(mod.LIVE_RETRY_AFTER + 2):
            poll(module, NOW + timedelta(seconds=30 * i))

        assert tokens[0] == "expired"
        assert module._live_token == "tok"
        assert module._live_channel_healthy() is True

    def test_the_re_read_is_not_on_every_poll(self, bench, monkeypatch):
        """A dark floor must not turn into a `lem_meta` read every poll of
        every bench — that is the load pattern, in a different table."""
        labcore = LabCore()
        monkeypatch.setattr(mod, "post_live", lambda *a, **kw: None)
        module = boot(bench(labcore, None), saved())
        for i in range(12):
            poll(module, NOW + timedelta(seconds=30 * i))
        assert labcore.count("live_config") <= 4, (
            f"{labcore.count('live_config')} `lem_meta` reads in 12 polls")


# ── (E) The boot probe may not drive the bench in a loop ────────────────────
#
# `_ask_followup` turns a note into an IMMEDIATE re-poll, and a follow-up may
# never ask for one of its own — otherwise a floor that keeps answering with a
# note has the bench polling itself into the ground on the main thread. The
# probe receives notes too (the server hands them to any push), so it is a new
# way into that machinery and has to be checked against the same property.


class TestTheProbeCannotStartAPollLoop:
    def test_the_probe_never_asks_for_a_follow_up(self, bench):
        """It has nothing to be late for. The probe runs BEFORE any read this
        poll makes, so everything a note invalidates is about to be read
        anyway — while asking for a follow-up would double every bench's poll
        rate at the exact moment of a floor-wide restart."""
        labcore = LabCore()
        floor = Floor(answer={"stale": ["corrections", "override"]})
        module = boot(bench(labcore, floor), saved())
        module._probe_live_channel(module._machine)
        assert module._live_followup is False, (
            "the boot probe asked for a follow-up poll — every bench in the "
            "building now polls twice on restart")

    def test_a_note_on_every_push_still_costs_one_follow_up(self, bench,
                                                            qapp):
        """The no-loop property end to end, with the probe in the road and a
        floor that answers EVERY push with a note."""
        labcore = LabCore()
        floor = Floor(answer={"stale": ["override", "corrections"]})
        module = boot(bench(labcore, floor), saved())
        dispatch(module)
        for _ in range(20):
            qapp.processEvents()
        # One knock at boot, one push from the poll, one from the single
        # follow-up that poll is entitled to ask for. Nothing after it.
        assert len(floor.pushes) == 3, (
            f"{len(floor.pushes) - 3} pushes beyond the budget — the note "
            "channel is driving the bench in a loop")

    def test_the_probe_still_honours_a_note_it_was_handed(self, bench):
        """The floor hands its notes to whichever push finds them, and the
        probe is a push. Dropping one on the floor would mean the note is
        spent without the bench acting on it."""
        labcore = LabCore()
        floor = Floor(answer={"stale": ["corrections"]})
        module = boot(bench(labcore, floor), saved())
        module._corrections_read_at = NOW       # as if a window were open
        module._probe_live_channel(module._machine)
        assert module._corrections_read_at is None
