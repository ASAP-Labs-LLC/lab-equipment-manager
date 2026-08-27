#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared builders for the uncertainty suites. CONTAINS NO TESTS.

Named `test_uncertainty_*` so it sits with the suites it serves (and so it stays
inside the file ownership this work was given); pytest collects it, finds
nothing, and moves on.

Every series here is built out of `lem_machine_log` rows in the exact shape the
station module writes — `kind='qc'`, a stringified value, a JSON `detail` — and
then read back through `qc_series.series_for`. That is deliberate: the point of
these suites is what the uncertainty module does with the log the lab actually
has, and a hand-built `QcSeries` would skip the two fields (`operator`,
`calibration_id`) whose absence is the whole reproducibility question.
"""

import json

import qc_series


def qc_row(machine_uid, test_name, ts, value, *, operator=None,
           calibration_id=None, low=None, high=None, expected=None,
           lab_id="", in_spec=True, kind="qc"):
    """One `lem_machine_log` row, written the way the bench writes it."""
    detail = {"in_spec": in_spec}
    if operator is not None:
        detail["operator"] = operator
    if calibration_id is not None:
        detail["calibration_id"] = calibration_id
    if low is not None:
        detail["low"] = low
    if high is not None:
        detail["high"] = high
    if expected is not None:
        detail["expected"] = expected
    return {"machine_uid": machine_uid, "ts": ts, "kind": kind,
            "lab_id": lab_id, "test_name": test_name,
            "value": "{:g}".format(value), "detail": json.dumps(detail)}


def qc_rows(machine_uid, test_name, entries, **kw):
    """`entries` is a sequence of (ts, value, operator, calibration_id)."""
    return [qc_row(machine_uid, test_name, ts, value, operator=op,
                   calibration_id=cal, **kw)
            for ts, value, op, cal in entries]


def series(machine_uid, test_name, entries, **kw):
    return qc_series.series_for(
        qc_rows(machine_uid, test_name, entries, **kw), machine_uid, test_name)


# ── the three coverages, spelled out once ────────────────────────────────────
#
# The values are the SAME in all three (10, 12, 14, 16, 18 — mean 14, s = sqrt
# 10) so that any difference a suite sees is a difference in what the log
# RECORDS about who ran them and against what, never a difference in the
# arithmetic.

_VALUES = (10.0, 12.0, 14.0, 16.0, 18.0)


def one_analyst_one_day(uid="mach-1", test="Cloud Point"):
    """Ryan, one calendar day, one calibration epoch. This is s_r."""
    return series(uid, test, [
        ("2026-08-10T08:0{}:00".format(i), v, "Ryan", "2026-08-01T09:00:00")
        for i, v in enumerate(_VALUES)])


def spans_analysts_days_and_calibrations(uid="mach-1", test="Cloud Point"):
    """Two analysts, five calendar days, two calibration epochs. This is u(Rw)."""
    who = ("Ryan", "Dana", "Ryan", "Dana", "Ryan")
    cal = ("2026-08-01T09:00:00", "2026-08-01T09:00:00", "2026-08-12T09:00:00",
           "2026-08-12T09:00:00", "2026-08-12T09:00:00")
    return series(uid, test, [
        ("2026-08-0{}T08:00:00".format(3 + i), v, who[i], cal[i])
        for i, v in enumerate(_VALUES)])


def unattributed(uid="mach-1", test="Cloud Point"):
    """Five results, five days, and the log records neither analyst nor epoch.

    This is what most of `lem_machine_log` looks like today — the rows written
    before the bench started stamping `operator` and `calibration_id`.
    """
    return series(uid, test, [
        ("2026-08-0{}T08:00:00".format(3 + i), v, None, None)
        for i, v in enumerate(_VALUES)])
