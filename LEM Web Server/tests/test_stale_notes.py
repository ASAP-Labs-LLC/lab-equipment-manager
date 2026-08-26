"""Stale notes: the floor tells a bench *that* something changed, never what.

The problem this exists to solve is LabCore load, not latency. LabCore
serialises every read and write through one queue at ~1.5 ops/sec. Each bench
asks it twice a minute "did my correction factors change?" and "did my manual
override change?", and the answer is almost always no — between them those two
polls are roughly 64% of all traffic on that queue, spent confirming that
nothing happened.

The benches already POST `/api/live` twice a minute, and that handler is
documented to never touch LabCore. So the floor leaves a note on the way past —
"your corrections changed" — and hands it back on that existing call. The bench
then does ONE LabCore read, and only when there is genuinely something to read.

**The note never carries the values.** It is a doorbell, not a delivery.
LabCore stays the single source of truth for what a correction factor actually
is; a value travelling on this road would be a second writer of the same fact,
which is the precedence rule that rots into "the bench used a correction the
record never held". Same reasoning as the failover rule in `merge_machines`.

Two properties are load-bearing and each has its own class below:

* Notes live OUTSIDE the TTL'd `_entries` dict. A bench switched off over the
  weekend must still be told when it comes back on Monday. `_entries` ages out
  by design; a note that aged out would be a change silently dropped.
* A note is held for TWO pushes, not one. See `TestHowLongANoteIsHeld` — the
  reasoning is subtle enough to be worth a class to itself.
"""
import sys
import threading
from datetime import datetime

import pytest

from labcore_gateway import FakeLabCoreGateway
from live_presence import LivePresence


TOKEN = "test-token"


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


class CountingGateway(FakeLabCoreGateway):
    """A gateway that reports how often anything reached LabCore.

    The same guard `test_live_endpoint.py` uses. It is repeated here because the
    notes are exactly the kind of feature that gets implemented with an
    innocent-looking lookup — "just check the machine exists before marking" —
    and one lookup on the push path is one LabCore op per bench per poll, which
    is the load pattern this whole change exists to remove.
    """

    def __init__(self):
        super().__init__()
        self.calls = 0

    def sql(self, *a, **k):
        self.calls += 1
        return super().sql(*a, **k)

    def read_sql(self, *a, **k):
        self.calls += 1
        return super().read_sql(*a, **k)

    def write(self, *a, **k):
        self.calls += 1
        return super().write(*a, **k)


@pytest.fixture
def gw():
    g = CountingGateway()
    g.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
          "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
          "reason TEXT, updated_at TEXT)")
    for uid, title in (("pac-flash-2", "PAC Flash 2"),
                       ("multitek-ns", "Multitek NS")):
        g.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
              [uid, title, "GREEN", "", "2026-08-05T13:00:00"])
    return g


@pytest.fixture
def app(gw):
    from web_app import create_app
    application = create_app(gw, authenticator=StubAuth(), secret="s",
                             live=LivePresence(), live_token=TOKEN)
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def live(app):
    return app.config["LIVE"]


def signed_in(client):
    client.post("/api/login", json={"username": "k", "password": "good"})
    return client


def push(client, uid="pac-flash-2", **over):
    body = {"machine_uid": uid, "status": "GREEN", "reason": "",
            "at": "", "interval_seconds": 30}
    body.update(over)
    return client.post("/api/live", json=body, headers={"X-LEM-Token": TOKEN})


def stale_of(response):
    """The note list off a `/api/live` response, as a set."""
    body = response.get_json()
    assert isinstance(body, dict), f"expected a JSON object, got {body!r}"
    assert "stale" in body, f"no `stale` key in {body!r}"
    assert isinstance(body["stale"], list), f"`stale` must be a list: {body!r}"
    return set(body["stale"])


# ── the wire contract ───────────────────────────────────────────────────────

class TestTheWireContract:
    """`POST /api/live` → 200 with `{"stale": [...]}`; auth and validation
    unchanged. It used to answer `"", 204`. Returning a body is backward
    compatible — a module built before this change ignores it — which is what
    lets the two sides be deployed in either order."""

    def test_a_push_with_nothing_pending_answers_an_empty_list(self, client):
        response = push(client)
        assert response.status_code == 200
        assert stale_of(response) == set()

    def test_the_kind_strings_are_exactly_as_the_bench_expects(self, client,
                                                               live):
        """Pinned as literals, deliberately. The client is a separate program
        that cannot import these, so a rename here is a silent no-op there:
        the bench would simply stop reading a table that had changed."""
        live.mark_stale("pac-flash-2", "corrections")
        live.mark_stale("pac-flash-2", "override")

        assert stale_of(push(client)) == {"corrections", "override"}

    def test_a_bad_token_is_still_refused(self, client, live):
        """A note pending must not become a way past the token check."""
        live.mark_stale("pac-flash-2", "corrections")
        response = client.post("/api/live", json={"machine_uid": "pac-flash-2"},
                               headers={"X-LEM-Token": "guess"})
        assert response.status_code == 401

    def test_a_refused_push_is_told_nothing(self, client, live):
        live.mark_stale("pac-flash-2", "corrections")
        response = client.post("/api/live", json={"machine_uid": "pac-flash-2"},
                               headers={"X-LEM-Token": "guess"})
        assert "corrections" not in response.get_data(as_text=True)

    def test_a_refused_push_does_not_consume_the_note(self, client, live):
        """Otherwise anything that can reach the port could burn a bench's
        notes without ever delivering them."""
        live.mark_stale("pac-flash-2", "corrections")
        client.post("/api/live", json={"machine_uid": "pac-flash-2"},
                    headers={"X-LEM-Token": "guess"})

        assert stale_of(push(client)) == {"corrections"}

    def test_a_body_that_is_not_an_object_is_still_refused(self, client):
        response = client.post("/api/live", json=["nope"],
                               headers={"X-LEM-Token": TOKEN})
        assert response.status_code == 400

    def test_a_push_with_no_machine_is_still_refused(self, client):
        response = client.post("/api/live", json={"status": "GREEN"},
                               headers={"X-LEM-Token": TOKEN})
        assert response.status_code == 400

    def test_a_note_goes_only_to_the_bench_it_was_left_for(self, client, live):
        live.mark_stale("pac-flash-2", "corrections")

        assert stale_of(push(client, uid="multitek-ns")) == set()
        assert stale_of(push(client, uid="pac-flash-2")) == {"corrections"}


# ── the two properties that are the whole point ─────────────────────────────

class TestTheNoteOutlivesTheLiveEntry:
    """`_entries` is a cache of liveness and ages out on purpose. A note is a
    pending instruction and must not.

    The failure this prevents: a bench is switched off on Friday, someone
    changes its correction factor on Saturday, and on Monday the note has
    expired along with the live entry — so the bench runs samples all week with
    the old factor and nothing anywhere reports it. That is the case a TTL'd
    note gets wrong, and it is also the case the 15-minute backstop poll was
    least able to cover, because the bench was not polling at all."""

    def test_a_note_survives_its_machine_aging_out_of_the_live_map(
            self, client, live):
        push(client)
        live.mark_stale("pac-flash-2", "corrections")

        entry = live._entries["pac-flash-2"]
        entry["seen"] -= (entry["ttl"] + 1)
        assert live.get("pac-flash-2") is None, "precondition: entry expired"

        assert stale_of(push(client)) == {"corrections"}

    def test_the_note_is_not_kept_in_the_ttld_map_at_all(self, live):
        """Structural, not behavioural — asserted directly because a note
        smuggled into `_entries` would pass every timing test above until the
        cap evicted it or the TTL caught it in production."""
        live.mark_stale("never-pushed", "override")
        assert "never-pushed" not in live._entries


class TestABenchTheFloorHasNeverHeardFrom:
    def test_a_note_for_a_machine_that_never_pushed_is_kept(self, client, live):
        """Corrections are settable before an instrument has ever parsed
        anything, so the change genuinely can precede the first push."""
        live.mark_stale("brand-new", "corrections")

        assert stale_of(push(client, uid="brand-new")) == {"corrections"}

    def test_marking_an_unknown_machine_does_not_reach_labcore(self, live, gw):
        gw.calls = 0
        live.mark_stale("brand-new", "corrections")
        assert gw.calls == 0


class TestHowLongANoteIsHeld:
    """**A note is delivered on the push that finds it AND on the next one,
    then dropped. Two deliveries, never three.**

    Stated as a sequence, with no note re-marked in between:

        push 1  →  ["corrections"]   the note is found and handed over
        push 2  →  ["corrections"]   handed over a second time, then retired
        push 3  →  []                gone

    Why not clear it the moment it is handed over, which is the obvious design?
    Because the hand-over is an HTTP response, and an HTTP response can be lost
    in flight. `post_live` on the bench is best-effort with a 1.5s timeout and
    swallows everything — so a response that dies on the way back is silent at
    both ends. Clear-on-delivery means that note is gone and the bench never
    learns; the 15-minute backstop poll becomes the only thing that catches it,
    and the change sits unapplied for up to fifteen minutes.

    Holding it one extra round makes a lost response self-heal on the next push,
    which is ~30 seconds away. The price, when the first response was NOT lost,
    is that the bench reads LabCore a second time for a change it already has:
    **at most one redundant read per change.** Against removing ~64% of the
    queue's traffic that is a rounding error, and it buys the failure mode back.

    A note re-marked while it is being held restarts the two-push window rather
    than riding out the old one — the second change is a real change and must
    reach the bench in its own right."""

    def test_push_one_is_told(self, client, live):
        live.mark_stale("pac-flash-2", "corrections")
        assert stale_of(push(client)) == {"corrections"}

    def test_push_two_is_told_again(self, client, live):
        live.mark_stale("pac-flash-2", "corrections")
        push(client)
        assert stale_of(push(client)) == {"corrections"}, (
            "the note was cleared on delivery — a response lost in flight now "
            "means the bench never hears about the change at all")

    def test_push_three_is_told_nothing(self, client, live):
        live.mark_stale("pac-flash-2", "corrections")
        push(client)
        push(client)
        assert stale_of(push(client)) == set(), (
            "the note is never retired — every push would order a LabCore read "
            "forever, which is worse than the polling it replaced")

    def test_a_note_marked_again_while_held_restarts_the_window(self, client,
                                                                live):
        live.mark_stale("pac-flash-2", "corrections")
        assert stale_of(push(client)) == {"corrections"}
        live.mark_stale("pac-flash-2", "corrections")   # a second real change
        assert stale_of(push(client)) == {"corrections"}
        assert stale_of(push(client)) == {"corrections"}
        assert stale_of(push(client)) == set()

    def test_the_two_kinds_are_held_independently(self, client, live):
        live.mark_stale("pac-flash-2", "corrections")
        push(client)
        live.mark_stale("pac-flash-2", "override")

        assert stale_of(push(client)) == {"corrections", "override"}
        assert stale_of(push(client)) == {"override"}
        assert stale_of(push(client)) == set()

    def test_one_benchs_pushes_do_not_retire_anothers_note(self, client, live):
        live.mark_stale("pac-flash-2", "corrections")
        for _ in range(5):
            push(client, uid="multitek-ns")

        assert stale_of(push(client, uid="pac-flash-2")) == {"corrections"}


# ── the three write sites ───────────────────────────────────────────────────

class TestSavingACorrectionLeavesTheNote:
    def test_it_marks_the_machine_that_changed(self, client, live):
        signed_in(client)
        response = client.post("/api/machines/pac-flash-2/corrections",
                               json={"test_name": "Flash", "correction": 0.5})
        assert response.status_code == 200

        assert stale_of(push(client, uid="pac-flash-2")) == {"corrections"}

    def test_it_marks_that_machine_and_no_other(self, client, live):
        """A correction is per machine per test. Marking the lab would put
        every bench through a LabCore read for one bench's change — which is
        the traffic this is here to remove, reintroduced by a broad mark."""
        signed_in(client)
        client.post("/api/machines/pac-flash-2/corrections",
                    json={"test_name": "Flash", "correction": 0.5})

        assert stale_of(push(client, uid="multitek-ns")) == set()

    def test_a_correction_that_is_not_a_number_marks_nothing(self, client):
        """The save is refused with a 400, so nothing changed. A note here
        orders a LabCore read to fetch a value that was never written."""
        signed_in(client)
        response = client.post("/api/machines/pac-flash-2/corrections",
                               json={"test_name": "Flash",
                                     "correction": "a bit"})
        assert response.status_code == 400

        assert stale_of(push(client, uid="pac-flash-2")) == set()

    def test_a_save_with_no_test_name_marks_nothing(self, client):
        signed_in(client)
        assert client.post("/api/machines/pac-flash-2/corrections",
                           json={"correction": 0.5}).status_code == 400

        assert stale_of(push(client, uid="pac-flash-2")) == set()

    def test_a_save_by_nobody_signed_in_marks_nothing(self, client):
        assert client.post("/api/machines/pac-flash-2/corrections",
                           json={"test_name": "Flash",
                                 "correction": 0.5}).status_code == 401

        assert stale_of(push(client, uid="pac-flash-2")) == set()

    def test_a_save_for_an_instrument_that_does_not_exist_marks_nothing(
            self, client):
        signed_in(client)
        assert client.post("/api/machines/ghost/corrections",
                           json={"test_name": "Flash",
                                 "correction": 0.5}).status_code == 404

        assert stale_of(push(client, uid="ghost")) == set()


class TestSettingAnOverrideLeavesTheNote:
    def test_it_marks_the_machine_that_changed(self, client):
        signed_in(client)
        response = client.post("/api/machines/pac-flash-2/override",
                               json={"override": "SERVICE",
                                     "comment": "pump swap"})
        assert response.status_code == 200

        assert stale_of(push(client, uid="pac-flash-2")) == {"override"}

    def test_clearing_an_override_is_also_a_change(self, client):
        """"Back in service" is exactly as urgent as "out of service"; a bench
        left on SERVICE because only the setting direction was marked is an
        instrument nobody can use."""
        signed_in(client)
        client.post("/api/machines/pac-flash-2/override",
                    json={"override": "SERVICE", "comment": "pump swap"})
        push(client)
        push(client)
        client.post("/api/machines/pac-flash-2/override",
                    json={"override": "", "comment": "back in service"})

        assert stale_of(push(client, uid="pac-flash-2")) == {"override"}

    def test_it_marks_that_machine_and_no_other(self, client):
        signed_in(client)
        client.post("/api/machines/pac-flash-2/override",
                    json={"override": "SERVICE", "comment": "pump swap"})

        assert stale_of(push(client, uid="multitek-ns")) == set()

    def test_an_override_with_no_comment_marks_nothing(self, client):
        signed_in(client)
        assert client.post("/api/machines/pac-flash-2/override",
                           json={"override": "SERVICE"}).status_code == 400

        assert stale_of(push(client, uid="pac-flash-2")) == set()

    def test_an_override_that_is_not_a_valid_state_marks_nothing(self, client):
        signed_in(client)
        assert client.post("/api/machines/pac-flash-2/override",
                           json={"override": "BANANA",
                                 "comment": "why"}).status_code == 400

        assert stale_of(push(client, uid="pac-flash-2")) == set()

    def test_an_override_by_nobody_signed_in_marks_nothing(self, client):
        assert client.post("/api/machines/pac-flash-2/override",
                           json={"override": "SERVICE",
                                 "comment": "x"}).status_code == 401

        assert stale_of(push(client, uid="pac-flash-2")) == set()


class TestRetiringAMachineClearsItsOverride:
    """`DELETE /api/machines/<uid>` drops that machine's `lem_machine_control`
    row, and `manual_override` is the column that row exists for — so from the
    bench's point of view this is an override change like any other, just to
    the empty state. A module still running one when it is retired would
    otherwise hold SERVICE until its next backstop poll."""

    def test_retiring_a_machine_marks_its_override(self, client):
        signed_in(client)
        client.post("/api/machines/pac-flash-2/override",
                    json={"override": "SERVICE", "comment": "pump swap"})
        push(client)
        push(client)

        response = client.delete("/api/machines/pac-flash-2",
                                 json={"confirm": True})
        assert response.status_code == 200

        assert stale_of(push(client, uid="pac-flash-2")) == {"override"}

    def test_it_marks_that_machine_and_no_other(self, client):
        signed_in(client)
        client.delete("/api/machines/pac-flash-2", json={"confirm": True})

        assert stale_of(push(client, uid="multitek-ns")) == set()

    def test_a_delete_by_nobody_signed_in_marks_nothing(self, client):
        assert client.delete("/api/machines/pac-flash-2").status_code == 401

        assert stale_of(push(client, uid="pac-flash-2")) == set()

    def test_a_delete_refused_for_confirmation_marks_nothing(self, client, gw):
        """The first attempt on a machine a module is running comes back 409
        naming what it would break. Nothing was deleted, so nothing changed —
        and a note here would send that very module off to re-read an override
        row still sitting exactly where it was."""
        signed_in(client)
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_heartbeat ("
               "machine_uid TEXT PRIMARY KEY, last_poll TEXT, watching TEXT)")
        gw.sql("INSERT INTO lem_machine_heartbeat VALUES (?,?,?)",
               ["pac-flash-2", datetime.now().isoformat(), "single_csv"])

        response = client.delete("/api/machines/pac-flash-2", json={})
        assert response.status_code == 409, response.get_json()

        assert stale_of(push(client, uid="pac-flash-2")) == set()


# ── the invariant the whole design rests on ─────────────────────────────────

class TestThePushStillNeverTouchesLabCore:
    """Unchanged and absolute. Notes are in memory; carrying a value on this
    road would need a read, and one read per bench per poll is the load pattern
    the snapshot exists to prevent."""

    def test_a_push_carrying_notes_costs_zero_labcore_operations(self, client,
                                                                 gw, live):
        for _ in range(20):
            live.mark_stale("pac-flash-2", "corrections")
            live.mark_stale("pac-flash-2", "override")
        gw.calls = 0

        for _ in range(20):
            push(client)

        assert gw.calls == 0, (
            "the push path reached LabCore while delivering notes — the notes "
            "exist to REMOVE LabCore ops, so paying one per push to hand them "
            "over is a net loss")

    def test_a_push_with_nothing_pending_costs_nothing_either(self, client, gw):
        gw.calls = 0
        for _ in range(20):
            push(client)
        assert gw.calls == 0

    def test_no_note_ever_carries_a_value(self, client, live):
        """The note is a doorbell. If a correction's number could travel this
        road there would be two writers of one fact and no way to say which is
        authoritative — the exact rot `merge_machines` was shaped to avoid."""
        live.mark_stale("pac-flash-2", "corrections")
        body = push(client).get_json()

        assert body["stale"] == ["corrections"] or set(body["stale"]) == {
            "corrections"}
        for item in body["stale"]:
            assert isinstance(item, str), (
                f"a note carried a payload: {item!r} — LabCore is the only "
                f"source of truth for what changed to what")


# ── storage limits and thread safety ────────────────────────────────────────

class TestNotesAreBounded:
    """`_entries` is capped at MAX_MACHINES for a reason and the notes need the
    same. Nothing validates a uid before marking (validating would cost a
    LabCore lookup on a path that must not have one), so a script with a typo
    in a loop is an unbounded set of uids that never push and never collect."""

    def test_the_notes_cannot_grow_without_limit(self, live):
        from live_presence import MAX_MACHINES
        for n in range(MAX_MACHINES * 3):
            live.mark_stale(f"ghost-{n}", "corrections")

        assert len(live._stale) <= MAX_MACHINES

    def test_the_most_recently_marked_note_survives_the_cap(self, live, client):
        from live_presence import MAX_MACHINES
        live.mark_stale("pac-flash-2", "corrections")
        for n in range(MAX_MACHINES * 2):
            live.mark_stale(f"ghost-{n}", "corrections")
        live.mark_stale("pac-flash-2", "override")

        assert stale_of(push(client, uid="pac-flash-2")) == {"override"}

    def test_a_kind_nobody_agreed_on_is_not_stored(self, live, client):
        """The two kind strings are a contract with a program that cannot
        import them. A third kind reaching the wire is a note no bench knows
        how to act on, so it would order a read and achieve nothing."""
        live.mark_stale("pac-flash-2", "colour")

        assert stale_of(push(client)) == set()

    def test_a_note_with_no_machine_is_not_stored(self, live):
        live.mark_stale("", "corrections")
        live.mark_stale(None, "corrections")
        assert live._stale == {}


class TestNotesSurviveConcurrentMarking:
    """The Flask dev server is threaded, so a supervisor saving a correction
    and a bench pushing are genuinely simultaneous. The failure mode is a torn
    read-modify-write in the collect step: it reads the pending set, a mark
    lands, and the collect writes back a set that never contained it. The note
    is then gone — not delayed, gone — and only the 15-minute backstop catches
    it, which is precisely the case the backstop is being reduced to."""

    def test_two_marks_on_one_bench_at_the_same_moment_both_survive(self):
        """A supervisor sets a correction factor while a colleague clears that
        bench's override. Two requests, two threads, one bench.

        This is the test that actually holds the lock in place, so it is worth
        saying how it is built. Each round marks ONE machine from two threads
        released together, then every machine is drained at the end. The
        interpreter's switch interval is dropped to its floor so a thread is
        preempted mid-update rather than running the whole method to
        completion, which is what makes the window reachable in a test at all.

        The window it aims at: `mark_stale` takes the note out of the dict,
        adds its kind, and puts it back. Two of those overlapping and the
        second thread finds nothing, builds a fresh note, and the first thread
        then writes its own copy back over the top — one of the two changes has
        vanished. Not delayed: gone, with only the 15-minute backstop left to
        catch it, which is the poll this whole feature is reducing.

        Measured while writing it: with the lock, 0 of 40 runs lost a note;
        with the lock neutralised, 38 of 40 did. Three rounds per run puts the
        chance of missing a regression far below the chance of a flake.

        Rounds stay under MAX_MACHINES on purpose — notes are only drained at
        the end, so a longer run would start evicting them and report the cap
        doing its job as a lost note."""
        from live_presence import MAX_MACHINES

        rounds = MAX_MACHINES - 6
        previous = sys.getswitchinterval()
        sys.setswitchinterval(1e-9)
        try:
            for attempt in range(3):
                live = LivePresence()
                uids = [f"bench-{n}" for n in range(rounds)]
                gate = threading.Barrier(2)
                failures = []

                def marker():
                    try:
                        for uid in uids:
                            gate.wait()
                            live.mark_stale(uid, "override")
                    except Exception as exc:      # pragma: no cover
                        failures.append(exc)

                thread = threading.Thread(target=marker)
                thread.start()
                try:
                    for uid in uids:
                        gate.wait()
                        live.mark_stale(uid, "corrections")
                finally:
                    thread.join()
                assert not failures, failures

                delivered = {}
                for uid in uids:
                    got = set(live.take_stale(uid))
                    got |= set(live.take_stale(uid))
                    delivered[uid] = got

                lost = {uid: sorted(delivered[uid]) for uid in uids
                        if delivered[uid] != {"corrections", "override"}}
                assert not lost, (
                    f"attempt {attempt}: {len(lost)} of {rounds} benches lost a "
                    f"note to a concurrent mark, e.g. {list(lost.items())[:3]} "
                    f"— that change reaches the bench only via the backstop")
        finally:
            sys.setswitchinterval(previous)

    def test_a_mark_racing_a_push_being_served_is_never_lost(self):
        """The other side of the same race: a note left while that bench's
        push is being collected. Whichever way it falls the note must reach the
        bench — on this push if the mark won, on a later one if it did not.
        What must never happen is neither."""
        rounds = 200
        previous = sys.getswitchinterval()
        sys.setswitchinterval(1e-9)
        try:
            live = LivePresence()
            uids = [f"bench-{n}" for n in range(rounds)]
            gate = threading.Barrier(2)
            delivered = {uid: set() for uid in uids}
            failures = []

            def marker():
                try:
                    for uid in uids:
                        gate.wait()
                        live.mark_stale(uid, "corrections")
                except Exception as exc:          # pragma: no cover
                    failures.append(exc)

            thread = threading.Thread(target=marker)
            thread.start()
            try:
                for uid in uids:
                    gate.wait()
                    delivered[uid] |= set(live.take_stale(uid))
            finally:
                thread.join()
            assert not failures, failures

            # Drain: two more pushes each, the longest a note is ever held.
            for uid in uids:
                delivered[uid] |= set(live.take_stale(uid))
                delivered[uid] |= set(live.take_stale(uid))

            lost = [uid for uid in uids
                    if "corrections" not in delivered[uid]]
            assert not lost, (
                f"{len(lost)} of {rounds} notes were marked and never "
                f"delivered — those changes are invisible until the backstop")
        finally:
            sys.setswitchinterval(previous)

    def test_pushes_from_many_benches_at_once_do_not_corrupt_the_notes(self):
        """The collect step mutates a shared dict. Without the lock this raises
        `RuntimeError: dictionary changed size during iteration` or drops
        entries, intermittently, under exactly the load the lab has."""
        live = LivePresence()
        uids = [f"bench-{n}" for n in range(24)]
        for uid in uids:
            live.mark_stale(uid, "override")
        errors = []

        def hammer(uid):
            try:
                for _ in range(200):
                    live.mark_stale(uid, "corrections")
                    live.take_stale(uid)
                    live.record(uid, {"status": "GREEN",
                                      "interval_seconds": 30})
            except Exception as exc:          # pragma: no cover - diagnostic
                errors.append(exc)

        threads = [threading.Thread(target=hammer, args=(uid,))
                   for uid in uids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors
