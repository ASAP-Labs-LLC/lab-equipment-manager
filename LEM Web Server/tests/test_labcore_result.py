#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One answer to "what did LabCore actually tell me?".

Three modules were written against the gateway in one week and all three got this
wrong, each in a different direction:

  - `levels.LevelStore._rows` turned any error into `[]`, so a read timeout — which
    this repo documents as routine — emptied the picker, drew zero instruments on
    the level the operator was looking at, and told them the level no longer existed.
  - `equipment_documents._rows` did the same, so during a blip `delete()` reported
    "there was nothing to delete" about a document that was still there, and `save()`
    skipped dedupe and wrote a permanent duplicate.
  - `equipment_documents._run` decided a WRITE had succeeded from the ABSENCE of an
    "error" key, so a gateway answering `None`, `{}` or
    `{"ok": False, "status": "rejected", "pending": 100}` was read as "done".

That is not three mistakes, it is one API that invites them, so the rule lives here
once instead of being re-derived per store. `snapshot_service.SnapshotReadError`
already states the doctrine for the machine list — "Reporting 'no machines' when the
truth is 'could not ask' is how a whole lab reads as empty during a LabCore blip" —
and this is that rule, extracted so everything else can hold it too.

The gateway's contract, read off labcore_gateway.py:
    read ok      -> {"ok": True, "rows": [...], "columns": [...]}
    write ok     -> {"ok": True, "rows_affected": N}
    either fails -> {"error": "SomeError: text"}
    the REAL client's write() returns `resp.json()` verbatim, so it can be any shape
    LabCore's queue felt like sending, including a refusal with no "error" key.
"""
import pytest

from labcore_result import (
    LabCoreRefused,
    LabCoreUnavailable,
    confirm_write,
    is_missing_table,
    rows,
)


class TestReadsTellTheTruth:
    def test_rows_come_back(self):
        assert rows({"ok": True, "rows": [{"a": 1}]}) == [{"a": 1}]

    def test_a_genuinely_empty_table_is_empty(self):
        assert rows({"ok": True, "rows": []}) == []

    def test_a_missing_rows_key_is_empty_not_an_error(self):
        """LabCore has answered; it just had nothing to say."""
        assert rows({"ok": True}) == []

    def test_a_read_that_could_not_be_asked_raises(self):
        """The whole point. Empty must mean empty, never "I could not ask"."""
        with pytest.raises(LabCoreUnavailable):
            rows({"error": "Read timed out"})

    def test_the_error_text_survives(self):
        """An operator staring at a blank floor needs the reason, not a shrug."""
        with pytest.raises(LabCoreUnavailable) as caught:
            rows({"error": "HTTPSConnectionPool: Read timed out"})
        assert "Read timed out" in str(caught.value)

    def test_a_non_dict_answer_raises(self):
        """`None` is what a gateway returns when it has stopped answering at all."""
        for answer in (None, [], "", 0):
            with pytest.raises(LabCoreUnavailable):
                rows(answer)


class TestTheOneCaseWhereEmptyIsHonest:
    """A table nobody has created yet genuinely holds nothing.

    Every lem_* table is created centrally at boot, so a module read before that
    happens — or on a LabCore where the CREATE was refused — must degrade to empty
    rather than taking the page down. That is a DIFFERENT fact from "could not ask",
    and it is the only error a read is allowed to swallow.
    """

    def test_a_missing_table_reads_as_empty(self):
        assert rows({"error": "OperationalError: no such table: lem_levels"}) == []

    def test_it_is_recognised_whatever_the_wrapper_says(self):
        assert is_missing_table("OperationalError: no such table: lem_x")
        assert is_missing_table("no such table: lem_x")
        assert is_missing_table("SQL error: No Such Table: LEM_X")

    def test_a_timeout_is_not_a_missing_table(self):
        assert not is_missing_table("Read timed out")
        assert not is_missing_table("LabCore is not running. Start LabCore first.")

    def test_a_caller_can_refuse_to_swallow_even_that(self):
        """A write path wants to hear about a missing table, not draw a blank."""
        with pytest.raises(LabCoreUnavailable):
            rows({"error": "no such table: lem_levels"}, missing_ok=False)


class TestWritesMustBeAcknowledged:
    """Absence of an error is NOT success.

    The real client returns `resp.json()` verbatim from LabCore's queue, and the
    queue refuses past 100 pending by ANSWERING rather than raising. A store that
    tests `if not res.get("error")` reports work that was never done.
    """

    def test_an_acknowledged_write_passes(self):
        confirm_write({"ok": True, "rows_affected": 1})

    def test_an_explicit_error_raises(self):
        with pytest.raises(LabCoreRefused):
            confirm_write({"error": "queue full"})

    def test_a_refusal_carrying_no_error_key_still_raises(self):
        """The exact shape the queue sends when it is past 100 pending."""
        with pytest.raises(LabCoreRefused):
            confirm_write({"ok": False, "status": "rejected", "pending": 100})

    def test_a_non_answer_is_not_success(self):
        """Not an answer at all — a dead gateway, or a transport handing back
        something that is not a response document.

        `{}` used to be in this list. It was removed deliberately, not loosened:
        an empty dict carries no failure signal, and this module's first rule
        (demand a truthy `ok`) was never checked against what real LabCore
        answers to a write that WORKS. See TestAgainstWhatLabCoreActuallySends —
        refusing an unrecorded success shape would have failed every write in the
        lab with /healthz still green."""
        for answer in (None, [], "done", 1):
            with pytest.raises(LabCoreRefused):
                confirm_write(answer)

    def test_the_refusal_says_what_happened(self):
        """The message has to name the signal it refused on, because the operator
        reading it needs to know whether to press Save again or go and look."""
        with pytest.raises(LabCoreRefused) as caught:
            confirm_write({"error": "LabCore is busy, try again",
                           "busy": True, "retry_after": 4})
        assert "busy" in str(caught.value).lower()

        with pytest.raises(LabCoreRefused) as caught:
            confirm_write({"ok": False, "status": "rejected"})
        assert "ok" in str(caught.value)

    def test_rows_affected_zero_is_still_an_acknowledgement(self):
        """DELETE of something already gone did happen. It changed nothing, which
        is a fact about the data, not a failure of the write."""
        confirm_write({"ok": True, "rows_affected": 0})


class TestTheTwoFailuresAreDistinguishable:
    """A caller has to be able to tell "ask again later" from "LabCore said no",
    because one is retryable and the other is a bug or a full queue."""

    def test_they_are_not_the_same_class(self):
        assert not issubclass(LabCoreUnavailable, LabCoreRefused)
        assert not issubclass(LabCoreRefused, LabCoreUnavailable)

    def test_both_are_catchable_as_one(self):
        """A route that only wants to answer 503 should not need two excepts."""
        from labcore_result import LabCoreError
        assert issubclass(LabCoreUnavailable, LabCoreError)
        assert issubclass(LabCoreRefused, LabCoreError)


class TestAgainstWhatLabCoreActuallySends:
    """The evidenced protocol, not an invented one.

    `confirm_write` was first written to demand a truthy `ok`, on the principle
    that silence is not success. The principle is sound and the application of it
    was not: NOTHING in this repo, this lab's notes, or the station module records
    what real LabCore answers to a write that SUCCEEDS. Every `{"ok": True}` in
    the tree is this repo's own sqlite fake. If the real service answers
    `{"rows_affected": 1}`, or `{}`, that rule fails every write in the lab — and
    /healthz would stay green while nothing could be saved, which RELEASING.md §5
    says nothing in the deploy pipeline catches.

    What IS evidenced, in notes.md and again in lem_station_module.py:495:

        {"error": "LabCore is busy…", "busy": true, "retry_after": n}

    an error DICT, returned normally rather than raised. The station module —
    the half that has actually run against production LabCore — judges a write by
    `result.get("error")`, and it is right to.

    So the rule is inverted: refuse on a POSITIVE failure signal, accept
    otherwise. That cannot break a write that worked, and it still catches the
    refusal that caused "imported 3094" while nothing landed.
    """

    def test_the_measured_refusal_is_refused(self):
        with pytest.raises(LabCoreRefused):
            confirm_write({"error": "LabCore is busy, try again",
                           "busy": True, "retry_after": 4})

    def test_a_busy_flag_alone_is_refused(self):
        """Belt and braces: `busy` is the field that means it."""
        with pytest.raises(LabCoreRefused):
            confirm_write({"busy": True})

    def test_an_explicit_negative_is_refused(self):
        for answer in ({"ok": False}, {"queued": False}):
            with pytest.raises(LabCoreRefused):
                confirm_write(answer)

    def test_an_answer_with_no_verdict_is_ACCEPTED(self):
        """The one that matters. An unknown-but-not-failing shape must pass, or a
        protocol we have never recorded takes the whole lab down."""
        for answer in ({"rows_affected": 1}, {}, {"status": "done"}):
            confirm_write(answer)

    def test_the_sqlite_fake_still_passes(self):
        confirm_write({"ok": True, "rows_affected": 1})

    def test_silence_is_still_not_success(self):
        """`None` is not an answer at all — that is a dead gateway, not a
        protocol we have not seen."""
        with pytest.raises(LabCoreRefused):
            confirm_write(None)

    def test_a_busy_read_is_unavailable_not_empty(self):
        """Reads come back through the same endpoint and can be refused the same
        way. Answering `[]` to a busy queue is how a full write queue empties the
        floor."""
        with pytest.raises(LabCoreUnavailable):
            rows({"error": "LabCore is busy…", "busy": True, "retry_after": 4})
        with pytest.raises(LabCoreUnavailable):
            rows({"busy": True})

    def test_retry_after_is_reported(self):
        """notes.md requires honouring it; a caller cannot honour what it cannot
        read."""
        from labcore_result import retry_after
        assert retry_after({"busy": True, "retry_after": 7}) == 7.0
        assert retry_after({"error": "busy"}) is None
