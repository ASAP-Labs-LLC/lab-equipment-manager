#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The two refusal shapes this suite drives, and which of them is evidence.

This module exists because one of them was invented during development, written
into a docstring as "the queue's refusal, verbatim off the live system", and
then cited by three later rounds of work as if it had been measured. A wrong
fact in a comment is how that happened, so the fact lives in exactly one place
now.

EVIDENCED — notes.md, and lem_station_module.py:495. LabCore's write queue
refuses new work past ~100 pending by ANSWERING:

    {"error": "LabCore is busy…", "busy": true, "retry_after": n}

an error DICT returned normally, not raised. The station module is the half of
this system that has actually run against production LabCore, and it judges
every write by `result.get("error")`.

NOT EVIDENCED — what LabCore answers to a write that SUCCEEDS. Every
`{"ok": True, "rows_affected": N}` in this tree is our own sqlite fake, which
is why `labcore_result` refuses on a positive failure signal and ACCEPTS
anything else rather than demanding an acknowledgement.

SYNTHETIC — `NO_ERROR_KEY` below. Chosen for one property: it carries no
"error" key, so it is refused only by the `ok`/`queued` half of the rule. That
makes it worth driving — a store that passes only against `{"error": ...}`
proves nothing, because that is the one shape the old `if not
res.get("error")` code already coped with — but it is a test fixture and
nothing more. It must never again be described as something LabCore sends.

Usage: a suite that wants both shapes declares

    pytestmark = pytest.mark.usefixtures("both_refusal_shapes")

and its fake gateway answers `refusal_shapes.current()` instead of a constant.
Every test in the module then runs once per shape.

`_current` is process-global, which is fine because this suite runs serially
and the fixture resets it after every test. If pytest-xdist is ever introduced,
each worker is its own process, so that stays true — but a threaded runner
would not, and this would need to move onto the fixture instead.
"""

EVIDENCED = {"error": "LabCore is busy, try again later", "busy": True,
             "retry_after": 4}

# Synthetic. See the module docstring: chosen because it carries no "error"
# key, not because LabCore has ever sent it.
NO_ERROR_KEY = {"queued": False, "pending": 137}

BOTH = (EVIDENCED, NO_ERROR_KEY)
IDS = ("evidenced-busy-dict", "synthetic-no-error-key")

_current = [EVIDENCED]


def current() -> dict:
    """A copy of the shape the running test is driving."""
    return dict(_current[0])


def use(shape) -> None:
    _current[0] = shape
