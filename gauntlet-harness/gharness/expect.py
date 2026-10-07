"""Comparing a measurement with an expectation row. Exact by default.

Spec §9: "The gate fails on any mismatch, including a better number. A better
number means the harness changed and must be re-baselined deliberately." So a
plain value in a row means EQUAL — not "at most". The only relaxations are the
ones §9 itself writes as bounds (F2 "≤ 3", F3 "≤ 60", E1 "≤ 2", ...), spelled
explicitly in the row as {"le": n} or {"ge": n}.

A field the row names and the measurement lacks is a mismatch, never a pass:
a counter the target could not produce is None, and None equals only None.
"""
import json

MISSING = object()


def _norm(v):
    return json.loads(json.dumps(v, default=str))


def check_field(expected, got):
    """(ok, text)."""
    if got is MISSING:
        return False, "missing (expected %s)" % (json.dumps(expected),)
    if isinstance(expected, dict) and set(expected) <= {"le", "ge"} and expected:
        if not isinstance(got, (int, float)) or isinstance(got, bool):
            return False, "%r is not a number (expected %s)" % (got, json.dumps(expected))
        if "le" in expected and not got <= expected["le"]:
            return False, "%s > %s" % (got, expected["le"])
        if "ge" in expected and not got >= expected["ge"]:
            return False, "%s < %s" % (got, expected["ge"])
        return True, ""
    if _norm(expected) != _norm(got):
        return False, "%s != expected %s" % (json.dumps(_norm(got))[:200],
                                             json.dumps(_norm(expected))[:200])
    return True, ""


def compare(row, measured, volatile=()):
    """List of mismatch strings; [] means the row holds exactly. `volatile` is
    a set of dotted paths skipped (wall-clock timestamps only)."""
    out = []
    _walk(row, measured, "", out, set(volatile))
    return out


def _walk(row, measured, prefix, out, volatile):
    for k, exp in row.items():
        path = prefix + k
        if path in volatile:
            continue
        got = measured.get(k, MISSING) if isinstance(measured, dict) else MISSING
        if isinstance(exp, dict) and not (set(exp) <= {"le", "ge"} and exp) \
                and isinstance(got, dict):
            _walk(exp, got, path + ".", out, volatile)
            continue
        ok, why = check_field(exp, got)
        if not ok:
            out.append("%s: %s" % (path, why))
