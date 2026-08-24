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

    def test_silence_is_not_success(self):
        for answer in (None, {}, [], "done", 1):
            with pytest.raises(LabCoreRefused):
                confirm_write(answer)

    def test_the_refusal_says_what_happened(self):
        with pytest.raises(LabCoreRefused) as caught:
            confirm_write({"ok": False, "status": "rejected", "pending": 100})
        assert "rejected" in str(caught.value) or "pending" in str(caught.value)

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
