#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
data_source.py â€” CSV loading and evaluation engine.

Adds:
- Primary timestamp from parsed_date + parsed_time (row-level), tolerant to a trailing space in header.
- BoxEvaluation.used_parsed flag to indicate whether row timestamps came from parsed_date/time.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Set

from PyQt5.QtCore import QObject, pyqtSignal

from models import SampleSpec, BoxConfig, STATUS_GREEN, STATUS_RED, STATUS_YELLOW, STATUS_UNKNOWN


UTC = datetime.utcnow


def ci_lookup(row: dict, key: str) -> Optional[str]:
    """Case-insensitive lookup of a CSV row by header name."""
    if not key:
        return None
    lk = key.strip().lower()
    for k, v in row.items():
        if k.strip().lower() == lk:
            return v
    return None


def _parse_timefmt(s: str, fmts: Tuple[str, ...]) -> Optional[datetime]:
    for fmt in fmts:
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


def parsed_dt_from_row(row: dict) -> Optional[datetime]:
    """
    Try to build a datetime from two separate columns:
    parsed_date, parsed_time (trailing space tolerated on the time column).

    Example values:
      parsed_date = "9/5/2025"
      parsed_time = "9:39:28"
    """
    d = ci_lookup(row, "parsed_date")
    t = ci_lookup(row, "parsed_time") or ci_lookup(row, "parsed_time ")
    if d and t:
        s = f"{d.strip()} {t.strip()}"
        # Try expected format first, then a few safe fallbacks
        dt = _parse_timefmt(s, ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M"))
        if dt:
            return dt
    return None


def parse_time(s: str) -> Optional[datetime]:
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    return _parse_timefmt(s, (
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%d-%b-%Y %H:%M:%S",
    ))


def best_row_time(row: dict, timestamp_col: str, file_path: str) -> Tuple[datetime, bool]:
    """
    Best-effort timestamp for a row, returning (datetime, used_parsed_flag).
    Priority:
      1) parsed_date + parsed_time
      2) explicit timestamp_col (if provided)
      3) file mtime
    """
    dt = parsed_dt_from_row(row)
    if dt:
        return dt, True

    if timestamp_col:
        sval = ci_lookup(row, timestamp_col)
        ts = parse_time(sval) if sval is not None else None
        if ts:
            return ts, False

    try:
        return datetime.utcfromtimestamp(os.path.getmtime(file_path)), False
    except Exception:
        return UTC(), False


class CsvReadWorker(QObject):
    finished = pyqtSignal(dict)         # {path: [rows]}
    error = pyqtSignal(str, str)        # (path, message)

    def __init__(self, paths: List[str]) -> None:
        super().__init__()
        self._paths = paths

    def run(self) -> None:
        out: Dict[str, List[dict]] = {}
        for path in self._paths:
            try:
                rows: List[dict] = []
                if os.path.exists(path):
                    with open(path, "r", newline="", encoding="utf-8-sig") as f:
                        reader = csv.DictReader(f)
                        rows.extend(reader)
                out[path] = rows
            except Exception as e:
                self.error.emit(path, f"{type(e).__name__}: {e}")
                out[path] = []
        self.finished.emit(out)


class ParameterResult:
    def __init__(self, test_name: Optional[str],
                 latest_value: Optional[float],
                 in_spec: Optional[bool],
                 low: Optional[float],
                 high: Optional[float],
                 note: str = "",
                 latest_time: Optional[datetime] = None,
                 sample_name: Optional[str] = None,
                 units: str = "") -> None:
        self.test_name = test_name
        self.latest_value = latest_value
        self.in_spec = in_spec
        self.low = low
        self.high = high
        self.note = note
        self.latest_time = latest_time
        self.sample_name = sample_name
        self.units = units


class BoxEvaluation:
    def __init__(self, status: str, results: List[ParameterResult],
                 last_good_qc: Optional[datetime],
                 latest_match_time: Optional[datetime],
                 reason: str,
                 used_parsed: bool) -> None:
        self.status = status
        self.results = results
        self.last_good_qc = last_good_qc
        self.latest_match_time = latest_match_time
        self.reason = reason
        self.used_parsed = used_parsed  # True if parsed_date/parsed_time was used for any matched row


def _safe_float(s) -> Optional[float]:
    try:
        return float(str(s).strip())
    except Exception:
        return None


def evaluate_box(box: BoxConfig,
                 samples_by_name: Dict[str, SampleSpec],
                 sample_id_col: str,
                 rows: List[dict]) -> BoxEvaluation:
    """
    Evaluate a single box given the CSV rows and global test catalog.
    Returns:
        BoxEvaluation with status RED/GREEN/YELLOW/UNKNOWN, per-test details,
        and whether parsed_date/time timestamps were used.
    """
    if not box.watched_tests:
        return BoxEvaluation(STATUS_UNKNOWN, [], None, None, "No tests selected.", False)
    if not getattr(box, 'sample_refs', None):
        return BoxEvaluation(STATUS_UNKNOWN, [], None, None, "No samples selected.", False)

    # Determine which tests affect status (per-test control). Backward compatible
    affecting: Set[str]
    try:
        affecting = set(getattr(box, 'affects_tests', None) or ([] if not getattr(box, 'affects_status', True) else list(box.watched_tests)))
    except Exception:
        affecting = set(box.watched_tests)

    if not affecting:
        # No tests affecting status: still compute results for display, but overall status unchanged/unknown
        reason_no_affect = "No affecting tests selected."

    param_results: List[ParameterResult] = []
    any_fail = False
    all_affect_have_values = True
    latest_any_time: Optional[datetime] = None
    in_spec_times: List[datetime] = []
    used_parsed_any = False

    # Build per-sample limits map
    limits: Dict[str, Tuple[float, float, float]] = {}
    # For each selected sample and watched test, compute results
    for sname in list(getattr(box, 'sample_refs', []) or []):
        sample = samples_by_name.get(sname)
        if not sample:
            # Skip unknown sample entries
            continue
        stv_by_name: Dict[str, Tuple[float, float, float, str, str]] = {
            stv.test_name: (stv.expected, stv.std_dev, stv.k, stv.value_col, stv.units) for stv in sample.tests
        }

        for tname in box.watched_tests:
            if tname not in stv_by_name:
                param_results.append(ParameterResult(test_name=tname, latest_value=None, in_spec=None,
                                                      low=None, high=None,
                                                      note="No limits for this test in sample.", sample_name=sample.name))
                if tname in affecting:
                    all_affect_have_values = False
                continue

            expected, std_dev, k, value_col, units = stv_by_name[tname]
            matches: List[Tuple[dict, datetime, bool]] = []
            for r in rows:
                sid = ci_lookup(r, sample_id_col)
                if sid is not None and str(sid).strip() == sample.sample_id_val:
                    ts, used_parsed = best_row_time(r, box.timestamp_col, box.csv_path)
                    matches.append((r, ts, used_parsed))

            if not matches:
                low = expected - k * std_dev
                high = expected + k * std_dev
                param_results.append(ParameterResult(test_name=tname, latest_value=None, in_spec=None,
                                                      low=low, high=high,
                                                      note="No matching rows for sample.", sample_name=sample.name, units=units))
                if tname in affecting:
                    all_affect_have_values = False
                continue

            matches.sort(key=lambda x: x[1])
            latest_row, latest_time, used_parsed = matches[-1]
            if used_parsed:
                used_parsed_any = True
            latest_any_time = max(latest_any_time, latest_time) if latest_any_time else latest_time

            raw = ci_lookup(latest_row, value_col)
            val = _safe_float(raw)
            low = expected - k * std_dev
            high = expected + k * std_dev

            if val is None:
                param_results.append(ParameterResult(test_name=tname, latest_value=None, in_spec=None,
                                                      low=low, high=high, latest_time=latest_time,
                                                      note=f"No numeric value in '{value_col}'.", sample_name=sample.name, units=units))
                if tname in affecting:
                    all_affect_have_values = False
                continue

            in_spec = (low <= val <= high)
            if tname in affecting:
                if in_spec:
                    in_spec_times.append(latest_time)
                else:
                    any_fail = True

            param_results.append(ParameterResult(test_name=tname, latest_value=val, in_spec=in_spec,
                                                 low=low, high=high, latest_time=latest_time,
                                                 sample_name=sample.name, units=units))

    if not affecting:
        status = STATUS_UNKNOWN
        reason = reason_no_affect
        last_good_qc = None
    elif any_fail:
        status = STATUS_RED
        reason = "At least one affecting test is out of spec."
        last_good_qc = None
    elif not all_affect_have_values:
        status = STATUS_UNKNOWN
        reason = "Insufficient data for one or more affecting tests."
        last_good_qc = None
    else:
        status = STATUS_GREEN
        reason = "All affecting tests are within expected ranges."
        last_good_qc = min(in_spec_times) if in_spec_times else None

    # Staleness (YELLOW) using the derived last_good_qc (caller may override with fallback clock)
    if status == STATUS_GREEN and last_good_qc:
        if (UTC() - last_good_qc) > timedelta(hours=box.qc_expire_hours):
            status = STATUS_YELLOW
            reason = "Last in-spec QC is stale."

    return BoxEvaluation(status=status, results=param_results,
                         last_good_qc=last_good_qc,
                         latest_match_time=latest_any_time,
                         reason=reason,
                         used_parsed=used_parsed_any)

