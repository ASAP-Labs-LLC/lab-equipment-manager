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
    err = _error_of(res)
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
    err = _error_of(res)
    if err is not None:
        raise LabCoreRefused(err)
    if not res.get("ok"):
        raise LabCoreRefused(
            "LabCore did not acknowledge the write ({!r})".format(res))


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
