#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A structural guard against the way a made-up fact got into three rounds of work.

`{"queued": false, "pending": 137}` was invented during the confirm-every-write
branch. It went into a docstring as "the queue's refusal, verbatim off the live
system", and from there three later rounds of tests and comments cited it as
measured — including one that asserted the string "137" survived into an error
message, which pinned a store's wording to a protocol nobody has ever seen.

Nothing about that was caught by the test suite, because a wrong fact in a
comment is not a failing test. So this is the test.

WHAT IS EVIDENCED (notes.md; lem_station_module.py:495): LabCore refuses new
work past ~100 pending by ANSWERING —

    {"error": "LabCore is busy…", "busy": true, "retry_after": n}

an error dict returned normally, not raised.

WHAT IS NOT: what LabCore answers to a write that SUCCEEDS — every
`{"ok": True, "rows_affected": N}` in this tree is our own sqlite fake — and
anything about a refusal that carries no "error" key.

THE RULES BELOW
  1. No CODE file may contain the invented shape at all — .py, .pyw, templates,
     scripts. There is no reason for one to: its only legitimate use is as a
     test fixture, and in `machine_map.py`'s docstring it read as a statement
     about a service.
  2. Where it does appear — in tests, and in the prose docs that have to tell
     this story — the text around it must say plainly that it is synthetic, and
     must not describe it as real, measured, or verbatim. A CLAUDE.md section
     headed "which half is evidence" has to be able to name what it is warning
     about; a store's docstring does not.

Both rules are about words, not behaviour, which is exactly why they need a
test: everything else in this suite would stay green with the lie in place.
"""
import os
import re

import pytest

# The invented SHAPE, in the forms it has been written in — the `pending: 137`
# count, or a `queued: false` sitting next to a `pending`.
#
# Deliberately not every `{"queued": False}`: that one is a verdict FLAG in
# `labcore_result._VERDICT_FLAGS`, and `test_labcore_result` is entitled to
# test the rule's own flags. What is banned is the invented answer BODY.
INVENTED = re.compile(
    r"""["']?pending["']?\s*:\s*137"""
    r"""|["']?queued["']?\s*:\s*(?:False|false)[^\n]*pending"""
    r"""|pending[^\n]*["']?queued["']?\s*:\s*(?:False|false)""")

# Words that turn a fixture into a claim about LabCore.
CLAIMS = (
    "verbatim",
    "off the live system",
    "actual refusal shape",
    "the real refusal",
    "real shape",
    "exact shape",
    "measured",
    "what labcore actually answers",
    "the literal body",
)

# What an honest mention says about itself.
HONEST = ("synthetic", "invented", "not a shape labcore", "not evidence")

WINDOW = 8          # lines either side that count as "around" a mention

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _files(directory, exts=(".py", ".pyw", ".md", ".html", ".js", ".mjs")):
    out = []
    for base, dirs, names in os.walk(directory):
        dirs[:] = [d for d in dirs
                   if d not in (".venv", ".git", "__pycache__", "node_modules")]
        for name in names:
            if name.endswith(exts):
                out.append(os.path.join(base, name))
    return sorted(out)


def _code_files():
    """Everything the app ships except its prose docs, tests excluded."""
    return [p for p in _files(ROOT)
            if not p.startswith(HERE + os.sep) and not p.endswith(".md")]


def _prose_files():
    """The docs — which must LABEL the shape rather than never mention it."""
    return [p for p in _files(ROOT, exts=(".md",))
            if not p.startswith(HERE + os.sep)]


def _mentions(path):
    """(line number, surrounding text) for each mention of the invented shape."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    for i, line in enumerate(lines):
        if INVENTED.search(line):
            lo, hi = max(0, i - WINDOW), min(len(lines), i + WINDOW + 1)
            yield i + 1, "\n".join(lines[lo:hi])


@pytest.mark.parametrize("path", _code_files(),
                         ids=[os.path.relpath(p, ROOT) for p in _code_files()])
def test_no_code_file_names_the_invented_shape(path):
    """Rule 1. A store's docstring is where an operator's successor looks to
    find out what LabCore does; a fixture shape written there reads as protocol
    documentation, and it was cited as such."""
    found = [n for n, _ctx in _mentions(path)]
    assert not found, (
        "{0} names the invented refusal shape at line(s) {1}. The evidenced "
        "refusal is the busy/error dict — see tests/refusal_shapes.py.".format(
            os.path.relpath(path, ROOT), found))


def _labelled_files():
    """Every file allowed to name it, except this one.

    The guard cannot be judged by its own vocabulary: it has to quote the
    invented shape and quote the claims made about it in order to ban them.
    """
    return [p for p in _files(HERE) + _prose_files()
            if os.path.abspath(p) != os.path.abspath(__file__)]


@pytest.mark.parametrize("path", _labelled_files(),
                         ids=[os.path.relpath(p, ROOT) for p in _labelled_files()])
def test_a_test_fixture_says_it_is_one(path):
    """Rule 2. It may live in tests — it exercises the half of the rule an
    `if res.get("error")` check cannot see — but only labelled."""
    for line_no, context in _mentions(path):
        low = context.lower()
        claimed = [c for c in CLAIMS if c in low]
        assert not claimed, (
            "{0}:{1} describes the invented shape as {2}. It is a test fixture "
            "chosen because it carries no \"error\" key; nobody has recorded "
            "LabCore sending it.".format(
                os.path.relpath(path, ROOT), line_no, claimed))
        assert any(h in low for h in HONEST), (
            "{0}:{1} uses the invented shape without saying it is synthetic. "
            "Say so, or the next reader takes it for protocol — which is "
            "exactly what happened.".format(
                os.path.relpath(path, ROOT), line_no))


def test_the_guard_would_actually_catch_it(tmp_path):
    """The guard on the guard. A structural test that cannot fail is decoration,
    and this one is asserting about prose, where that is easy to get wrong."""
    bad = tmp_path / "store.py"
    bad.write_text('# The queue answers {"queued": false, "pending": 137}\n',
                   encoding="utf-8")
    assert list(_mentions(str(bad))), "the pattern missed a plain mention"

    labelled = tmp_path / "fixture.py"
    labelled.write_text('# SYNTHETIC shape\n'
                        'REFUSAL = {"queued": False, "pending": 137}\n',
                        encoding="utf-8")
    _line, context = next(_mentions(str(labelled)))
    assert any(h in context.lower() for h in HONEST)

    claiming = tmp_path / "claim.py"
    claiming.write_text('# measured off the live system\n'
                        'REFUSAL = {"pending": 137}\n', encoding="utf-8")
    _line, context = next(_mentions(str(claiming)))
    assert [c for c in CLAIMS if c in context.lower()]
