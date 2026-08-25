#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
labcore_result.py — what did LabCore actually tell me?

Every store in this app asks the gateway a question and then has to decide what the
answer means. That decision was being made independently in every module, and it was
made wrong in three new ones in a single week, each in a different direction:

  - a read error became `[]`, so a routine timeout emptied the floor picker, drew no
    instruments on the level the operator was looking at, and reported the level as
    deleted;
  - the same in the document store, so during a blip `delete()` said "there was
    nothing to delete" about a document still sitting on disk, and `save()` skipped
    dedupe and wrote a permanent duplicate;
  - a WRITE was treated as done because the answer had no "error" key, so a gateway
    answering `None`, `{}`, or a queue refusal was read as success and the caller
    reported a row it never wrote.

Three modules, three wrong answers, one question. `snapshot_service.SnapshotReadError`
already states the rule for the machine list — *"Reporting 'no machines' when the
truth is 'could not ask' is how a whole lab reads as empty during a LabCore blip"* —
so this is that rule extracted, tested once, and importable, rather than re-derived
per store and re-derived wrong.

THE GATEWAY'S CONTRACT, read off labcore_gateway.py:

    read ok       {"ok": True, "rows": [...], "columns": [...]}
    write ok      {"ok": True, "rows_affected": N}
    either fails  {"error": "SomeError: text"}

and — the part that catches people — the REAL client's `write()` returns
`resp.json()` verbatim from LabCore's HTTP queue. That queue serialises at roughly
1.5 writes a second and refuses past 100 pending by ANSWERING, not by raising. So a
refusal can arrive in any shape LabCore felt like sending, including one with no
"error" key at all. Which is why the rule below is stated positively:

    a write succeeded only if the answer SAYS SO. Silence is never success.

THE ONE READ ERROR THAT IS ALLOWED TO MEAN EMPTY is "no such table". Every lem_*
table is created centrally at boot (see snapshot_service), so a module read before
that has run, or on a LabCore where the CREATE was refused, is genuinely looking at
nothing. That is a different fact from "could not ask" and it is the only one a read
may swallow. Everything else raises, because a blank floor with no explanation is the
failure this module exists to prevent.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class LabCoreError(RuntimeError):
    """Anything that stops an answer from being trustworthy.

    Callers that only need to turn the whole class into one HTTP status catch this;
    the two below exist because a route that wants to say "try again" has to be able
    to tell a blip apart from a refusal.
    """


class LabCoreUnavailable(LabCoreError):
    """LabCore could not be asked, so nothing is known.

    NOT the same as "the answer was empty". This is the one that must never be
    quietly turned into `[]`, `{}` or `None` — every instrument vanishing from the
    floor during an eight-second timeout looks exactly like a lab with no equipment.
    """


class LabCoreRefused(LabCoreError):
    """LabCore was asked, answered, and the work did not happen.

    A full write queue, a rejected operation, or an answer so unlike an
    acknowledgement that claiming success from it would be a guess.
    """


# Matched case-insensitively against the error text. sqlite phrases it
# "no such table: lem_levels" and the gateway prefixes the exception class, so the
# substring is the reliable part rather than any exact wrapper.
_MISSING_TABLE = "no such table"


def is_missing_table(error_text: Any) -> bool:
    """Is this the one error that honestly means "there is nothing there yet"?"""
    return _MISSING_TABLE in str(error_text or "").lower()


# The failure signals LabCore actually sends, evidenced rather than assumed.
#
# notes.md and lem_station_module.py:495 both record the same measured shape from
# a real incident — a bulk import that "reported imported 3094 while nothing
# landed":
#
#     {"error": "LabCore is busy…", "busy": true, "retry_after": n}
#
# an error DICT returned normally, not raised. The station module, which is the
# half that has actually run against production LabCore, judges every write by
# `result.get("error")`.
#
# WHAT IS NOT RECORDED ANYWHERE is what LabCore answers to a write that SUCCEEDS.
# Every `{"ok": True, "rows_affected": N}` in this tree is our own sqlite fake.
# This module's first version demanded a truthy `ok` on the principle that
# silence is not success — sound as a principle, unsafe as an implementation,
# because a real service answering `{"rows_affected": 1}` would have had every
# write in the lab raising while /healthz stayed green. RELEASING.md §5 is
# explicit that nothing in the deploy pipeline catches that.
#
# So the rule refuses on a POSITIVE failure signal and accepts otherwise. It
# cannot break a write that worked, and it still catches the refusal that caused
# the 3094.
_FAILURE_FLAGS = ("busy",)          # truthy means refused
_VERDICT_FLAGS = ("ok", "queued")   # present-and-falsy means refused


def refusal_of(res: Any) -> Optional[str]:
    """Why this answer says the work did not happen, or None if it does not.

    Positive signals only. An answer carrying no verdict at all is not a
    refusal — it is a protocol we have not recorded, and guessing "failed" there
    is the guess that takes the lab down.
    """
    if not isinstance(res, dict):
        # Not an answer at all: a gateway that returned nothing, or a transport
        # that handed back something unparseable. That IS a failure.
        return "LabCore returned no answer ({!r})".format(res)
    # Whatever the signal, the operational detail rides along. "LabCore said no"
    # sends someone to a log file; "LabCore said no, 137 pending, retry in 4s"
    # tells them it is a queue and it will clear.
    detail = ", ".join(
        "{}={}".format(k, res[k]) for k in ("pending", "retry_after", "busy")
        if k in res)
    suffix = " ({})".format(detail) if detail else ""

    err = res.get("error")
    if err:
        return "{}{}".format(err, suffix)
    for flag in _FAILURE_FLAGS:
        if res.get(flag):
            return "LabCore reported {}={!r}{}".format(
                flag, res.get(flag), suffix)
    for flag in _VERDICT_FLAGS:
        if flag in res and not res.get(flag):
            return "LabCore reported {}={!r}{}".format(
                flag, res.get(flag), suffix)
    return None


def retry_after(res: Any, default: Optional[float] = None) -> Optional[float]:
    """How long LabCore asked us to wait, if it said.

    notes.md requires bulk writes to honour this, and a caller cannot honour what
    it cannot read. Returns `default` when the answer carries no hint.
    """
    if not isinstance(res, dict):
        return default
    hint = res.get("retry_after")
    try:
        return float(hint)
    except (TypeError, ValueError):
        return default


def _error_of(res: Any) -> Optional[str]:
    """The error text in an answer, or None if it does not carry one.

    A non-dict is not an answer at all. The gateway returns `None` when it has
    stopped answering, and the real client can return whatever JSON came back.
    """
    if not isinstance(res, dict):
        return "LabCore returned no answer ({!r})".format(res)
    err = res.get("error")
    return str(err) if err else None


def rows(res: Any, *, missing_ok: bool = True) -> List[Dict[str, Any]]:
    """The rows from a read, or an exception saying why there are none.

    `missing_ok` swallows exactly one error — a table that does not exist yet — and
    nothing else. Pass `missing_ok=False` on a path where a missing table is itself
    the news: a write that is about to be attempted, or a health check whose job is
    to notice that the schema never got created.

    An answer with no "rows" key is empty rather than broken: LabCore has replied,
    it simply had nothing to list.
    """
    # `refusal_of`, not `_error_of`: reads travel the same endpoint as writes and
    # can be turned away the same way. Answering `[]` to a busy queue is how a
    # full WRITE queue empties the floor.
    err = refusal_of(res)
    if err is not None:
        if missing_ok and is_missing_table(err):
            return []
        raise LabCoreUnavailable(err)
    return list(res.get("rows") or [])


def confirm_write(res: Any) -> None:
    """Raise unless the answer positively acknowledges the write.

    Stated positively on purpose. The old test — "no error key, so it worked" —
    passes for `None`, for `{}`, and for the refusal LabCore's queue sends when it is
    past 100 pending, all of which mean the row was never written. A store that
    believes any of those tells the operator their work was saved.

    `rows_affected: 0` IS an acknowledgement. Deleting something already gone did
    happen; it just changed nothing, which is a fact about the data rather than a
    failure of the write.
    """
    err = refusal_of(res)
    if err is not None:
        raise LabCoreRefused(err)


def wrote_rows(res: Any) -> int:
    """How many rows a confirmed write touched.

    Separate from `confirm_write` because "did it happen" and "did it match
    anything" are different questions, and conflating them is how a delete of an
    already-deleted row gets reported as a failure.
    """
    confirm_write(res)
    try:
        return int(res.get("rows_affected") or 0)
    except (TypeError, ValueError):
        return 0
