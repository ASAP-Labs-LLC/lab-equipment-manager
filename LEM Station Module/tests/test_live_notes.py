"""The floor answering the push: "this changed — go and read it."

LabCore serialises reads AND writes through one queue at about 1.5 ops/sec, and
it is falling over under the load. An idle bench at the 30s default makes 4.6
LabCore reads a minute, and two of the questions behind them are pure polling —
the answer is "no change" almost every single time:

  * the correction factors, already behind a refresh window;
  * the floor's manual override, `lem_machine_control`, deliberately ungated
    because it is the lever somebody pulls to take a bench OFF LINE and it
    cannot wait out a window.

The bench already POSTs `/api/live` to the floor's web server on every poll, and
that handler never touches LabCore. So the floor answers the push with a note
saying what changed, and the bench reads LabCore when it is TOLD to, plus a long
backstop. The wire contract, fixed:

    POST /api/live
      200 + {"stale": ["corrections", "override"]}   # any subset
      200 + {"stale": []}                            # nothing pending

Two things make this safe rather than clever, and both are tested below.

  * The live road is best-effort BY CONSTRUCTION — `live_url` may never have
    been published, the floor may be unreachable, and `post_live` swallows
    everything. A window that applied while the note channel was dead would
    leave a bench running for fifteen minutes after somebody overrode it. So the
    window applies ONLY while the channel is actually delivering; otherwise the
    bench falls straight back to reading every poll, exactly as it always has.

  * `_push_live` runs at the END of `_process_outcome`, after `_labcore_sync`.
    A note that arrived during poll N would naturally be acted on at poll N+1 —
    which makes the override take up to two poll intervals instead of one, and
    that is worse than what it replaces. The poll that RECEIVES a note asks for
    an immediate follow-up poll instead, and that follow-up may never ask for
    another.
"""
import json
from datetime import datetime, timedelta

import pytest

import lem_station_module as mod
from lem_station_module import Machine

from test_module_qt import make_module

NOW = datetime(2026, 8, 26, 12, 0, 0)
FLOOR = "http://10.0.0.5:5557"


# ── (A) `post_live` hands the floor's answer back ───────────────────────────
#
# It used to return a bool. The note is the reason it cannot: the caller needs
# what the floor SAID, not merely that it listened.
#
# The shape matters as much as the content. A successful push with an empty body
# is `{}`, which is FALSY — so the old truthiness test would count a perfectly
# healthy push as a failure, walk `_live_failures` up to LIVE_RETRY_AFTER and
# re-read the live config out of LabCore on a loop. None means failure; a dict,
# possibly empty, means success.


class Answer:
    """Stands in for urlopen: one response, with whatever body is asked for.

    `body=None` is a response object with no `read` at all — which is what the
    fakes in `test_live_push.py` are, and what a real 204 amounts to.
    """

    def __init__(self, body=None, status=200, boom=None):
        self.body = body
        self.status = status
        self.boom = boom
        self.requests = []
        if body is not None:
            self.read = lambda: (body.encode("utf-8")
                                 if isinstance(body, str) else body)

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.boom is not None:
            raise self.boom
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def floor_says(monkeypatch):
    def install(answer):
        monkeypatch.setattr(mod.urllib.request, "urlopen", answer)
        return answer
    return install


class TestThePushComesBackWithAnAnswer:
    def test_a_note_reaches_the_caller(self, floor_says):
        floor_says(Answer(json.dumps({"stale": ["override"]})))
        assert mod.post_live(FLOOR, "tok", {}) == {"stale": ["override"]}

    def test_nothing_pending_is_an_empty_list_not_a_failure(self, floor_says):
        floor_says(Answer(json.dumps({"stale": []})))
        assert mod.post_live(FLOOR, "tok", {}) == {"stale": []}

    def test_a_success_with_no_body_at_all_is_still_a_success(self, floor_says):
        """A 204, or a floor that has not been taught to answer yet. Success —
        and NOT None, because None is how the caller counts failures. The empty
        dict is FALSY, which is exactly the trap: a truthiness test here counts
        a healthy push as a failure and walks `_live_failures` up to
        LIVE_RETRY_AFTER, re-reading the live config out of LabCore on a loop.
        """
        floor_says(Answer())
        answer = mod.post_live(FLOOR, "tok", {})
        assert answer is not None
        assert answer == {}

    def test_a_refused_push_is_none_not_an_empty_dict(self, floor_says):
        """The distinction the failure counter rests on."""
        import urllib.error
        floor_says(Answer(boom=urllib.error.URLError("connection refused")))
        assert mod.post_live(FLOOR, "tok", {}) is None

    def test_no_address_is_none(self, floor_says):
        rec = floor_says(Answer())
        assert mod.post_live("", "tok", {}) is None
        assert rec.requests == []

    def test_a_non_json_body_is_no_notes_not_a_crash(self, floor_says):
        """An intercepting proxy, a login page, a half-written response. The
        push must not raise: it runs on the worker, and LabStation's
        `_run_in_thread` drops the callback on an exception, which strands
        `_polling` and stops the bench polling at all."""
        floor_says(Answer("<html>not the floor</html>"))
        assert mod.post_live(FLOOR, "tok", {}) == {}

    def test_a_json_body_of_the_wrong_shape_is_no_notes(self, floor_says):
        floor_says(Answer(json.dumps(["override"])))
        assert mod.post_live(FLOOR, "tok", {}) == {}

    def test_a_body_that_explodes_on_read_is_no_notes(self, floor_says):
        answer = Answer()
        answer.read = lambda: (_ for _ in ()).throw(OSError("socket died"))
        floor_says(answer)
        assert mod.post_live(FLOOR, "tok", {}) == {}


class TestWhatTheNoteMeans:
    """`parse_live_notes` is the whole of the wire contract's read side."""

    def test_both_kinds_are_understood(self):
        assert mod.parse_live_notes(
            {"stale": ["corrections", "override"]}) == {"corrections",
                                                        "override"}

    def test_nothing_pending_is_no_notes(self):
        assert mod.parse_live_notes({"stale": []}) == set()

    def test_a_missing_key_is_no_notes(self):
        assert mod.parse_live_notes({}) == set()

    def test_a_kind_nobody_here_knows_is_ignored(self):
        """The server is built by another hand and may learn new kinds before
        this module does. An unknown kind is not a reason to raise, and not a
        reason to invalidate something at random."""
        assert mod.parse_live_notes(
            {"stale": ["override", "sprockets"]}) == {"override"}

    def test_a_kind_is_matched_after_trimming_and_returned_trimmed(self):
        """Trimmed on the way OUT as well as in. Handing back " override "
        recognises the note and then drops it: no `in` test downstream
        matches, so the read it names is never invalidated."""
        assert mod.parse_live_notes(
            {"stale": [" override "]}) == {mod.LIVE_NOTE_OVERRIDE}

    def test_junk_never_raises(self):
        for body in (None, [], "stale", {"stale": "override"},
                     {"stale": [None, 3]}, True):
            assert mod.parse_live_notes(body) == set()


# ── The bench, its LabCore, and the floor on the other end ──────────────────


class Counter:
    """A LabCore that counts what it is asked for.

    The signatures are LabStation's REAL ones — `read_sql` takes NO `source` —
    because a fake looser than the thing it stands in for is how a call that
    raises TypeError in production sails through a test.
    """

    def __init__(self, live_url=FLOOR):
        self.reads = []
        self.live_url = live_url
        self.override = ""
        self.factors = {}

    def read_sql(self, sql, args=None, timeout=None):
        flat = " ".join(sql.split()).lower()
        if "lem_meta" in flat:
            self.reads.append("live_config")
            if not self.live_url:
                return {"rows": []}
            return {"rows": [{"key": "live_url", "value": self.live_url},
                             {"key": "live_token", "value": "tok"}]}
        if "lem_machine_control" in flat:
            self.reads.append("override")
            return {"rows": [{"machine_uid": "m1",
                              "manual_override": self.override}]}
        if "lem_correction_factors" in flat:
            self.reads.append("corrections")
            return {"rows": [{"test_name": name, "correction": value}
                             for name, value in self.factors.items()]}
        self.reads.append("other")
        return {"rows": []}

    def sql(self, sql, args=None, source="LabStation", timeout=None):
        return {"ok": True}

    def write(self, operation, params=None, source=""):
        return {"ok": True}

    def count(self, tag):
        return self.reads.count(tag)


class Floor:
    """The web server on the other end of `/api/live`.

    `note` is what it answers the NEXT push with, and it is CONSUMED — a note is
    delivered once, which is the whole point of the channel. `dead` is a floor
    that is not there, which `post_live` reports as None.
    """

    def __init__(self):
        self.pushes = []
        self.note = None
        self.dead = False
        self.always = None       # a note on every push — for the no-loop proof

    def post(self, url, token, payload, timeout=None):
        self.pushes.append(payload)
        if self.dead:
            return None
        if self.always is not None:
            return {"stale": list(self.always)}
        note, self.note = self.note, None
        return {"stale": list(note or [])}


@pytest.fixture
def bench(qapp, monkeypatch):
    def build(labcore, floor=None, machine=None):
        monkeypatch.setitem(mod.__dict__, "labcore_read_sql", labcore.read_sql)
        monkeypatch.setitem(mod.__dict__, "labcore_sql", labcore.sql)
        monkeypatch.setitem(mod.__dict__, "labcore_write", labcore.write)
        monkeypatch.setattr(mod, "_in_thread", lambda fn, cb: cb(fn()))
        if floor is not None:
            monkeypatch.setattr(mod, "post_live", floor.post)
        module = make_module()
        module._machine = machine or Machine(uid="m1", title="Eraspec",
                                             source_type="manual")
        return module
    return build


def poll(module, now):
    """One whole worker-half poll, with the clock injected."""
    return module._process_outcome(module._machine, [], None, [], now)


# ── (B) The note invalidates what it names ──────────────────────────────────


class TestTheBenchActsOnWhatTheFloorSaid:
    def test_a_corrections_note_drops_the_stamp(self, bench):
        labcore, floor = Counter(), Floor()
        module = bench(labcore, floor)
        poll(module, NOW)
        assert module._corrections_read_at == NOW
        floor.note = ["corrections"]
        poll(module, NOW + timedelta(seconds=30))
        assert module._corrections_read_at is None, (
            "the floor said the factors changed and the bench kept its stamp")

    def test_a_corrections_note_also_bumps_the_generation(self, bench):
        """Deliberate, and not belt-and-braces. A corrections read may be IN
        FLIGHT on the worker right now — it began before the note existed, so
        the rows it is carrying are the PRE-change ones. Applied, they revert
        the very edit the note is about and then stamp the window over the line
        above. The generation counter is the only thing that can reach a call
        already in progress; the stamp cannot. See `_corrections_epoch`."""
        labcore, floor = Counter(), Floor()
        module = bench(labcore, floor)
        poll(module, NOW)
        epoch = module._corrections_epoch
        floor.note = ["corrections"]
        poll(module, NOW + timedelta(seconds=30))
        assert module._corrections_epoch == epoch + 1, (
            "a read already in flight when the note arrived will be believed, "
            "and it is carrying the values the note says are stale")

    def test_an_override_note_drops_the_override_stamp(self, bench):
        labcore, floor = Counter(), Floor()
        module = bench(labcore, floor)
        poll(module, NOW)
        assert module._override_read_at == NOW
        floor.note = ["override"]
        poll(module, NOW + timedelta(seconds=30))
        assert module._override_read_at is None

    def test_a_note_about_one_thing_leaves_the_other_alone(self, bench):
        """The kinds are independent. Invalidating both on either note would
        throw away the saving on whichever the floor did not mention."""
        labcore, floor = Counter(), Floor()
        module = bench(labcore, floor)
        poll(module, NOW)
        floor.note = ["override"]
        poll(module, NOW + timedelta(seconds=30))
        assert module._corrections_read_at is not None, (
            "an override note invalidated the correction factors as well")

    def test_no_note_invalidates_nothing(self, bench):
        """The overwhelmingly common answer, and the whole saving."""
        labcore, floor = Counter(), Floor()
        module = bench(labcore, floor)
        poll(module, NOW)
        poll(module, NOW + timedelta(seconds=30))
        assert module._corrections_read_at is not None
        assert module._override_read_at is not None

    def test_the_note_reaches_the_bench_through_a_real_push(self, bench,
                                                            floor_says):
        """End to end, over the wire shape the server is being built against —
        not just the helper. `post_live` parses it, `_push_live` acts on it."""
        labcore = Counter()
        module = bench(labcore)
        floor_says(Answer(json.dumps({"stale": ["override", "corrections"]})))
        poll(module, NOW)
        assert module._override_read_at is None
        assert module._corrections_read_at is None


# ── (C) The windows, and the fallback that makes them safe ──────────────────


class TestTheOverrideIsBehindAWindowWhileTheFloorIsTalking:
    def test_the_window_exists(self):
        assert mod.OVERRIDE_REFRESH_SECONDS == 900

    def test_the_corrections_backstop_was_lengthened_too(self):
        """The note handles the normal case now; this is only the backstop."""
        assert mod.CORRECTIONS_REFRESH_SECONDS == 900

    def test_the_first_poll_still_reads_it(self, bench):
        """A bench that has cached nothing has to ask — and on the very first
        poll it does not yet know there is a floor at all."""
        labcore, floor = Counter(), Floor()
        module = bench(labcore, floor)
        poll(module, NOW)
        assert labcore.count("override") == 1

    def test_twenty_idle_polls_no_longer_cost_twenty_override_reads(self, bench):
        """Ten minutes of an idle bench at the 30s default. This is the read
        this whole channel exists to remove: two LabCore ops a minute, per
        bench, asking a question whose answer is "no" for weeks at a time."""
        labcore, floor = Counter(), Floor()
        module = bench(labcore, floor)
        for i in range(20):
            poll(module, NOW + timedelta(seconds=30 * i))
        assert labcore.count("override") == 1, (
            f"the override was read {labcore.count('override')} times in 20 "
            "polls while the floor was answering every push")

    def test_the_backstop_still_fires(self, bench):
        """Cached, not frozen. Even with the note channel silent about it, the
        override must be re-read eventually — the note is an accelerator, never
        the only path."""
        labcore, floor = Counter(), Floor()
        module = bench(labcore, floor)
        poll(module, NOW)
        poll(module, NOW + timedelta(
            seconds=mod.OVERRIDE_REFRESH_SECONDS + 1))
        assert labcore.count("override") == 2

    def test_a_clock_that_steps_backwards_does_not_freeze_the_override(
            self, bench):
        """These are naive local `datetime.now()` values on a bench PC. DST
        fall-back repeats an hour and NTP steps the clock back whenever it
        likes, so `(now - last).total_seconds()` goes NEGATIVE — which compares
        less than the window and skips the read for the whole repeated hour, on
        the one read that takes a bench off line. Negative elapsed time is not
        "recently read", it is arithmetic that has stopped meaning anything."""
        labcore, floor = Counter(), Floor()
        module = bench(labcore, floor)
        poll(module, NOW)
        poll(module, NOW - timedelta(minutes=45))
        assert labcore.count("override") == 2, (
            "the clock stepped back and the bench stopped reading the override")

    def test_a_refused_read_is_not_cached_as_an_answer(self, bench,
                                                       monkeypatch):
        """A busy LabCore is not an override. Stamping a refusal would leave a
        bench running for the whole window on a lever it never read."""
        labcore, floor = Counter(), Floor()
        module = bench(labcore, floor)

        real = labcore.read_sql

        def refuse_override(sql, args=None, timeout=None):
            if "lem_machine_control" in sql:
                labcore.reads.append("override")
                return {"error": "LabCore is busy"}
            return real(sql, args, timeout)

        monkeypatch.setitem(mod.__dict__, "labcore_read_sql", refuse_override)
        poll(module, NOW)
        monkeypatch.setitem(mod.__dict__, "labcore_read_sql", real)
        poll(module, NOW + timedelta(seconds=30))
        assert labcore.count("override") == 2, (
            "a refused override read was cached as though it had answered")

    def test_a_newly_bound_instrument_asks_again(self, bench):
        """Everything cached was about a different instrument."""
        labcore, floor = Counter(), Floor()
        module = bench(labcore, floor)
        poll(module, NOW)
        module.set_machine(Machine(uid="m2", title="Other",
                                   source_type="manual"), publish=False)
        poll(module, NOW + timedelta(seconds=30))
        assert labcore.count("override") == 2


class TestADeadChannelMeansNoWindowAtAll:
    """The single most important thing here.

    The live road is best-effort BY CONSTRUCTION: `live_url` may never have been
    published, the floor may be unreachable, and `post_live` swallows every
    failure by design. The manual override is what somebody pulls to take a
    bench OFF LINE. If the note channel is dead and the override sat behind a
    fifteen-minute window, that bench would keep running for fifteen minutes
    after being overridden — which is exactly the delay the code comment on that
    read has always said nobody would accept.

    So the window applies ONLY while the channel is delivering. Otherwise the
    backstop is the only path there is, and it must not be the slow one.
    """

    def test_no_floor_configured_means_the_override_is_read_every_poll(
            self, bench):
        """Nothing published in `lem_meta` — a lab that never set the live road
        up, which is every lab until somebody does. Behaviour must be exactly
        what it has always been."""
        labcore, floor = Counter(live_url=""), Floor()
        module = bench(labcore, floor)
        for i in range(20):
            poll(module, NOW + timedelta(seconds=30 * i))
        assert labcore.count("override") == 20, (
            "a bench with no note channel cached the one read that takes it "
            "off line")

    def test_an_unreachable_floor_means_the_override_is_read_every_poll(
            self, bench):
        """The floor is configured but not answering — a rebooting server, a
        cable out, a laptop that went to sleep. `post_live` swallows it, so
        nothing else in the module will ever notice."""
        labcore, floor = Counter(), Floor()
        floor.dead = True
        module = bench(labcore, floor)
        for i in range(20):
            poll(module, NOW + timedelta(seconds=30 * i))
        assert labcore.count("override") == 20, (
            "the floor stopped answering and the bench kept trusting a note "
            "channel that is not delivering")

    def test_a_floor_that_dies_mid_shift_reopens_the_read(self, bench):
        """It starts healthy — the window is on — and then goes.

        One poll of grace is structural and cannot be removed: `_labcore_sync`
        runs BEFORE `_push_live`, so the poll during which the floor dies has
        already decided about its reads by the time the push discovers it. From
        the poll after that it must be reading EVERY time.

        "Every time" is the assertion that matters. A bench that fell back to
        reading every THIRD poll — which is what keying this off `_live_failures`
        alone would give, because `_live_config` zeroes the counter each time it
        re-reads — would be ninety seconds behind an override with nobody aware
        of it.
        """
        labcore, floor = Counter(), Floor()
        module = bench(labcore, floor)
        for i in range(4):
            poll(module, NOW + timedelta(seconds=30 * i))
        assert labcore.count("override") == 1, "the window was not on"

        floor.dead = True
        poll(module, NOW + timedelta(seconds=120))       # discovers the death
        found = labcore.count("override")
        for i in range(5, 12):
            poll(module, NOW + timedelta(seconds=30 * i))
        assert labcore.count("override") == found + 7, (
            f"{labcore.count('override') - found} reads in the seven polls "
            "after the floor stopped answering — a bench overridden now waits "
            "for a window that nothing is going to shortcut")

    def test_a_floor_that_comes_back_closes_the_window_again(self, bench):
        """The counterweight. A guard that never re-armed would leave every
        bench that ever saw one failed push reading LabCore for ever."""
        labcore, floor = Counter(), Floor()
        floor.dead = True
        module = bench(labcore, floor)
        poll(module, NOW)
        floor.dead = False
        poll(module, NOW + timedelta(seconds=30))
        before = labcore.count("override")
        for i in range(2, 10):
            poll(module, NOW + timedelta(seconds=30 * i))
        assert labcore.count("override") == before, (
            "the floor came back and the bench never stopped polling LabCore")

    def test_a_push_that_raises_reopens_the_read_too(self, bench, monkeypatch):
        """`post_live` swallows everything TODAY. This does not depend on it.

        The channel is proven healthy first, so the window is on; then the push
        starts raising. If only the `return None` path re-opened the window, a
        `post_live` that grew one uncaught exception — or a caller that wrapped
        it — would leave every bench in the lab trusting a channel that has not
        delivered anything for hours, on the read that takes a bench off line.
        """
        labcore, floor = Counter(), Floor()
        module = bench(labcore, floor)
        for i in range(3):
            poll(module, NOW + timedelta(seconds=30 * i))
        assert labcore.count("override") == 1, "the window was not on"

        def explode(url, token, payload, timeout=None):
            raise RuntimeError("the floor is on fire")

        monkeypatch.setattr(mod, "post_live", explode)
        poll(module, NOW + timedelta(seconds=90))       # discovers it
        found = labcore.count("override")
        for i in range(4, 9):
            poll(module, NOW + timedelta(seconds=30 * i))
        assert labcore.count("override") == found + 5, (
            "a push that raised left the bench believing the note channel was "
            "still delivering")

    def test_no_floor_configured_keeps_the_short_corrections_backstop(
            self, bench):
        """Same reasoning, one step softer. With no note channel the backstop is
        the ONLY way an edit made in the web server reaches this bench, so it
        cannot be the fifteen-minute one — it stays the window the corrections
        read had before the note existed."""
        labcore, floor = Counter(live_url=""), Floor()
        module = bench(labcore, floor)
        for i in range(20):
            poll(module, NOW + timedelta(seconds=30 * i))
        assert labcore.count("corrections") == 5, (
            f"corrections read {labcore.count('corrections')} times in 20 "
            "polls with no note channel — at the unsignalled window of "
            f"{mod.CORRECTIONS_REFRESH_UNSIGNALLED_SECONDS}s it is 5")

    def test_a_talking_floor_stretches_the_corrections_backstop(self, bench):
        """And with the channel healthy the long backstop is the right one: the
        note carries the edit, so the read only has to catch what the note
        lost."""
        labcore, floor = Counter(), Floor()
        module = bench(labcore, floor)
        for i in range(20):
            poll(module, NOW + timedelta(seconds=30 * i))
        assert labcore.count("corrections") == 1


# ── (D) The note must not cost a poll interval to act on ────────────────────
#
# `_push_live` runs at the END of `_process_outcome`, AFTER `_labcore_sync`. So
# a note arriving during poll N invalidates a value that poll N has already read
# past, and it would be re-read at poll N+1 — up to TWO poll intervals, 60s at
# the default, where the ungated read this replaces took ONE. That is a
# regression dressed up as an optimisation.
#
# The poll that RECEIVES a note asks the main thread for an immediate follow-up
# poll. `_show_outcome` is the main-thread half and the only place allowed to
# touch a timer, so that is where the ask is honoured.


def dispatch(module):
    """A whole poll through the real pipeline — worker half AND `_show_outcome`
    — because the follow-up is arranged by the main-thread half."""
    machine = module._machine
    module._dispatch_pipeline(lambda: (machine, [], None))


class TestANoteLandsWithoutWaitingForTheNextTick:
    def test_an_override_note_is_re_read_in_the_same_poll_interval(
            self, bench, qapp):
        labcore, floor = Counter(), Floor()
        module = bench(labcore, floor)
        dispatch(module)                       # learns the floor, reads once
        assert labcore.count("override") == 1

        labcore.override = mod.STATUS_DEAD
        floor.note = ["override"]
        dispatch(module)                       # the poll that RECEIVES it
        qapp.processEvents()                   # the follow-up it asked for

        assert labcore.count("override") == 2, (
            "the note was acted on but nothing re-read the override until the "
            "next scheduled poll — a 30s saving turned into a 30s cost")
        assert module._machine.manual_override == mod.STATUS_DEAD, (
            "the bench was taken off line on the floor and is still running")

    def test_a_corrections_note_lands_the_same_way(self, bench, qapp):
        labcore, floor = Counter(), Floor()
        labcore.factors = {"Flash": -3.0}
        module = bench(labcore, floor)
        dispatch(module)
        assert labcore.count("corrections") == 1

        labcore.factors = {"Flash": -4.0}
        floor.note = ["corrections"]
        dispatch(module)
        qapp.processEvents()

        assert module._machine.corrections["Flash"] == pytest.approx(-4.0)

    def test_a_poll_with_no_note_asks_for_nothing(self, bench, qapp):
        """The ordinary poll, which is almost every poll. An unconditional
        follow-up would double this module's poll rate for ever."""
        labcore, floor = Counter(), Floor()
        module = bench(labcore, floor)
        dispatch(module)
        pushes = len(floor.pushes)
        dispatch(module)
        qapp.processEvents()
        assert len(floor.pushes) == pushes + 1, (
            "a poll that received no note still triggered a follow-up poll")

    def test_the_follow_up_cannot_ask_for_another(self, bench, qapp):
        """The no-loop property, and the reason it is not merely theoretical:
        the follow-up PUSHES too, so a floor that answers with a note again —
        because it re-sends, because somebody is editing, because of a bug on a
        server built by another hand — would have the bench polling itself into
        the ground in a tight loop, on the main thread, for ever.

        A follow-up poll may act on its note (the value is still invalidated and
        the next scheduled poll reads it) but may never ask for one of its own.
        """
        labcore, floor = Counter(), Floor()
        floor.always = ["override", "corrections"]     # a note on EVERY push
        module = bench(labcore, floor)

        dispatch(module)
        pushes = len(floor.pushes)
        for _ in range(20):
            qapp.processEvents()

        assert len(floor.pushes) == pushes + 1, (
            f"{len(floor.pushes) - pushes} follow-up polls off one note — the "
            "note channel is driving the bench in a loop")

    def test_the_follow_up_is_arranged_by_the_main_thread_half(self):
        """Threading model: widgets and timers belong to the main thread, and a
        QTimer touched from a worker is undefined behaviour rather than a race
        that shows up in a log. `_show_outcome` is the main-thread half. The
        worker may RAISE a flag and nothing else."""
        import inspect
        worker = inspect.getsource(mod.LEMStationModule._push_live)
        assert "QTimer" not in worker and "singleShot" not in worker, (
            "the worker thread is arranging a timer")
        assert "_live_followup" in worker, (
            "the worker never tells anybody it acted on a note")

        main = inspect.getsource(mod.LEMStationModule._show_outcome)
        assert "_ask_followup" in main, (
            "nothing on the main thread honours the follow-up the worker asked "
            "for")
        hook = inspect.getsource(mod.LEMStationModule._ask_followup)
        assert "_live_followup" in hook and "singleShot" in hook

    def test_the_bench_keeps_polling_when_the_floor_talks_nonsense(
            self, bench, monkeypatch, qapp):
        """The whole road is inside one `try` for one reason: a raise here
        travels up the worker, LabStation's `_run_in_thread` drops the callback,
        and `_polling` is stranded True — the bench stops polling ALTOGETHER.
        That is far worse than losing a push."""
        labcore = Counter()
        module = bench(labcore)

        def hostile(url, token, payload, timeout=None):
            raise RuntimeError("the floor is on fire")

        monkeypatch.setattr(mod, "post_live", hostile)
        dispatch(module)
        assert module._polling is False
        dispatch(module)
        assert labcore.count("override") >= 2, (
            "a floor that raises took the bench's LabCore reads with it")


# ── (E) Health is EARNED by the protocol, never by a 2xx ────────────────────
#
# The defect this section pins, and it is a rollout scenario rather than a
# theoretical one. The benches are separate PCs running LabStation; the floor's
# web server and every module in the building cannot be upgraded in the same
# instant. So there is always a window — and a rollback, and any proxy that
# swallows a body — where an OLDER floor is answering these pushes.
#
# An older floor answers `/api/live` with `204` and no body at all. `post_live`
# reports that as `{}` — a success, correctly, because the push DID land. But
# `{}` carries no note and never will: `parse_live_notes({})` is `set()` for
# ever. A health check that asks only "did the push land" therefore says the
# note channel is delivering on a floor that has never heard of notes, and the
# manual override — the lever that takes a bench OFF LINE — sits behind a
# fifteen-minute window with nothing anywhere able to shortcut it.
#
# Measured against the unfixed code: with an old floor answering, `_override_due`
# returned False at 60s, 300s and 899s. A bench would keep running for up to a
# quarter of an hour after a supervisor took it off line, silently.
#
# So a landed push is NOT evidence of a note channel. The evidence is an answer
# that speaks the protocol: a dict carrying a `stale` LIST. `{"stale": []}` is
# the protocol saying "nothing pending" and counts; a bare `{}`, a proxy's login
# page, a JSON array — none of them do, and each falls straight back to reading
# every poll, exactly as a bench with no floor at all does.


class ShapedFloor:
    """A floor that answers with EXACTLY the body it is handed.

    `Floor` above always answers in the note protocol, which is the one thing
    this section has to vary: the entire defect is a floor that ACCEPTS the push
    and answers with something that is not a note. A callable body is handed the
    push number, so a floor can be upgraded — or downgraded — mid-shift.
    """

    def __init__(self, body):
        self.body = body
        self.pushes = []

    def post(self, url, token, payload, timeout=None):
        self.pushes.append(payload)
        body = self.body
        return body(len(self.pushes)) if callable(body) else body


class TestOnlyTheProtocolCountsAsANoteChannel:
    """One shape question, asked in one place, so the parser and the health
    check cannot drift apart. A future edit to one and not the other is exactly
    how this defect comes back."""

    def test_the_protocol_is_a_dict_with_a_stale_list(self):
        assert mod.speaks_live_notes({"stale": []}) is True
        assert mod.speaks_live_notes({"stale": ["override"]}) is True

    def test_an_empty_body_does_not_speak_it(self):
        """The 204 an un-upgraded floor sends. A landed push, and no channel."""
        assert mod.speaks_live_notes({}) is False

    def test_junk_does_not_speak_it_and_never_raises(self):
        for body in (None, [], "stale", {"stale": "override"}, True, 3,
                     {"notes": []}):
            assert mod.speaks_live_notes(body) is False

    def test_the_parser_agrees_with_it_on_every_shape(self):
        """The seam's whole purpose. Anything the health check calls silent
        must parse to no notes, and anything that parses to a note must be
        recognised as the protocol."""
        for body in ({}, None, [], "x", {"stale": "override"}, {"stale": []},
                     {"stale": ["override"]}, {"stale": ("corrections",)},
                     {"stale": ["sprockets"]}):
            if mod.parse_live_notes(body):
                assert mod.speaks_live_notes(body), body
            if not mod.speaks_live_notes(body):
                assert mod.parse_live_notes(body) == set(), body


class TestAnUnupgradedFloorIsNotANoteChannel:
    def test_a_bodyless_answer_leaves_the_override_read_every_poll(self, bench):
        """The defect, at the read that matters. Ten minutes of an idle bench
        against a floor that has not been upgraded yet: the push lands every
        time, and not one of those pushes is evidence of a note."""
        labcore, floor = Counter(), ShapedFloor({})
        module = bench(labcore, floor)
        for i in range(20):
            poll(module, NOW + timedelta(seconds=30 * i))
        assert labcore.count("override") == 20, (
            f"the override was read {labcore.count('override')} times in 20 "
            "polls against a floor that cannot send a note — a bench taken off "
            "line waits out the whole 900s window with nothing to shortcut it")

    def test_a_bodyless_answer_keeps_the_short_corrections_backstop(self, bench):
        """Same argument one step softer: with no note channel the backstop is
        the ONLY way an edit in the web server reaches this bench, so it is the
        unsignalled window, not the fifteen-minute one."""
        labcore, floor = Counter(), ShapedFloor({})
        module = bench(labcore, floor)
        for i in range(20):
            poll(module, NOW + timedelta(seconds=30 * i))
        assert labcore.count("corrections") == 5, (
            f"corrections read {labcore.count('corrections')} times in 20 "
            "polls against an un-upgraded floor — at the unsignalled window of "
            f"{mod.CORRECTIONS_REFRESH_UNSIGNALLED_SECONDS}s it is 5")

    def test_the_channel_is_not_reported_healthy(self, bench):
        labcore, floor = Counter(), ShapedFloor({})
        module = bench(labcore, floor)
        poll(module, NOW)
        assert module._live_channel_healthy() is False

    def test_the_override_is_due_at_every_interval_inside_the_window(self,
                                                                    bench):
        """The empirical reproduction, spelled out. Against the unfixed code
        `_override_due` was False at 60s, 300s and 899s."""
        labcore, floor = Counter(), ShapedFloor({})
        module = bench(labcore, floor)
        poll(module, NOW)
        for seconds in (60, 300, 899):
            assert module._override_due(NOW + timedelta(seconds=seconds)), (
                f"the override was not due {seconds}s after the last read, "
                "against a floor that cannot tell this bench anything")

    def test_a_proxy_serving_html_is_not_a_note_channel(self, bench):
        """An intercepting proxy or a captive portal answers 200 with a login
        page. `post_live` hands that back as `{}`; a proxy that mangles rather
        than replaces can hand back a JSON array. Neither is a floor."""
        for body in ({}, ["override"], "<html>not the floor</html>", None):
            labcore, floor = Counter(), ShapedFloor(body)
            module = bench(labcore, floor)
            for i in range(6):
                poll(module, NOW + timedelta(seconds=30 * i))
            assert labcore.count("override") == 6, (
                f"{body!r} was accepted as a note channel")

    def test_the_push_itself_is_still_counted_a_success(self, bench):
        """Health and delivery are different questions. The address and token
        are RIGHT — the push landed — so `_live_config` must not go back to
        LabCore for them every fourth poll on top of everything else."""
        labcore, floor = Counter(), ShapedFloor({})
        module = bench(labcore, floor)
        for i in range(20):
            poll(module, NOW + timedelta(seconds=30 * i))
        assert module._live_failures == 0
        assert labcore.count("live_config") == 1, (
            "an un-upgraded floor turned into a LabCore read on a loop")


class TestAnUpgradedFloorEarnsTheWindow:
    def test_nothing_pending_is_the_protocol_and_counts(self, bench):
        """`{"stale": []}` is the floor SAYING nothing changed, which is a
        note channel doing its job. It must not be confused with silence."""
        labcore, floor = Counter(), ShapedFloor({"stale": []})
        module = bench(labcore, floor)
        for i in range(20):
            poll(module, NOW + timedelta(seconds=30 * i))
        assert labcore.count("override") == 1
        assert labcore.count("corrections") == 1

    def test_a_note_is_healthy_and_acted_on(self, bench):
        labcore, floor = Counter(), ShapedFloor({"stale": ["override"]})
        module = bench(labcore, floor)
        poll(module, NOW)
        assert module._override_read_at is None, "the note was not acted on"
        assert module._live_channel_healthy() is True

    def test_a_floor_upgraded_mid_shift_opens_the_window_from_that_point(
            self, bench):
        """The other half of the rollout. The floor is upgraded while the bench
        keeps running: bodyless answers, then the protocol. Every poll before
        the upgrade reads the override; the window opens only once the floor
        has actually proved it can send a note."""
        labcore = Counter()
        floor = ShapedFloor(lambda n: {} if n <= 5 else {"stale": []})
        module = bench(labcore, floor)
        for i in range(5):
            poll(module, NOW + timedelta(seconds=30 * i))
        assert labcore.count("override") == 5, "the window opened too early"

        for i in range(5, 20):
            poll(module, NOW + timedelta(seconds=30 * i))
        assert labcore.count("override") == 6, (
            f"{labcore.count('override')} reads — the upgraded floor's window "
            "did not open, or it opened retrospectively")

    def test_a_floor_downgraded_mid_shift_closes_the_window_again(self, bench):
        """A rolled-back server, or a proxy that starts eating the body. One
        poll of grace is structural — `_labcore_sync` decides its reads before
        `_push_live` discovers anything — and from the poll after that the
        bench must be reading EVERY time, not every third."""
        labcore = Counter()
        floor = ShapedFloor(lambda n: {"stale": []} if n <= 4 else {})
        module = bench(labcore, floor)
        for i in range(4):
            poll(module, NOW + timedelta(seconds=30 * i))
        assert labcore.count("override") == 1, "the window was not on"

        poll(module, NOW + timedelta(seconds=120))     # discovers the downgrade
        found = labcore.count("override")
        for i in range(5, 12):
            poll(module, NOW + timedelta(seconds=30 * i))
        assert labcore.count("override") == found + 7, (
            f"{labcore.count('override') - found} reads in the seven polls "
            "after the floor stopped speaking the protocol")
