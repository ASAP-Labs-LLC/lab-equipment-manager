#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
data_source.py — CSV loading and evaluation engine.

Adds:
- Primary timestamp from parsed_date + parsed_time (row-level), tolerant to a trailing space in header.
- BoxEvaluation.used_parsed flag to indicate whether row timestamps came from parsed_date/time.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import QObject, pyqtSignal

from models import BoxConfig, SampleSpec, SampleTestSpec, STATUS_GREEN, STATUS_RED, STATUS_YELLOW, STATUS_UNKNOWN


NOW = datetime.now

TIMESTAMP_ERROR_LOG = os.path.join(os.path.dirname(__file__), "timestamp_error.log")

DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%m/%d/%Y %H:%M:%S.%f",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%y %H:%M:%S",
    "%m/%d/%y %H:%M",
    "%d-%b-%Y %H:%M:%S",
    "%d-%b-%Y %H:%M",
)

DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%d-%b-%Y",
    "%Y%m%d",
)

TIME_FORMATS = (
    "%H:%M:%S",
    "%H:%M",
    "%I:%M:%S %p",
    "%I:%M %p",
)

ALT_TIMESTAMP_SUBSTRINGS = (
    "timestamp",
    "sampletime",
    "sample_timestamp",
    "runtime",
    "run_time",
    "run_datetime",
    "testtime",
    "analysis_time",
    "collection_time",
)



def normalize_header(name: str) -> str:
    return ''.join(ch for ch in name.strip().lower() if ch.isalnum() or ch == '_')


def log_timestamp_error(file_path: str, message: str) -> None:
    try:
        with open(TIMESTAMP_ERROR_LOG, 'a', encoding='utf-8') as fh:
            fh.write(f"{NOW().isoformat()}\t{file_path}\t{message}\n")
    except Exception:
        pass





def lookup_candidates(row: dict, candidates) -> tuple[Optional[str], Optional[str]]:
    for candidate in candidates:
        target = normalize_header(candidate)
        for key, value in row.items():
            if normalize_header(key) == target:
                sval = str(value).strip()
                if sval:
                    return sval, key
    return None, None


def search_row_for_substrings(row: dict, substrings) -> tuple[Optional[str], Optional[str]]:
    for key, value in row.items():
        norm = normalize_header(key)
        if any(sub in norm for sub in substrings):
            sval = str(value).strip()
            if sval:
                return sval, key
    return None, None


def parse_date_value(value: str) -> Optional[datetime]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except Exception:
            continue
    return None


def parse_time_value(value: str) -> Optional[datetime]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except Exception:
            continue
    return None


def combine_date_time(date_val: Optional[str], time_val: Optional[str], file_path: str) -> Optional[datetime]:
    date_dt = parse_date_value(date_val) if date_val else None
    time_dt = parse_time_value(time_val) if time_val else None
    if date_dt and time_dt:
        return datetime.combine(date_dt.date(), time_dt.time())
    if date_dt:
        return datetime.combine(date_dt.date(), datetime.min.time())
    if time_dt:
        try:
            file_date = datetime.fromtimestamp(os.path.getmtime(file_path)).date()
        except Exception:
            file_date = NOW().date()
        return datetime.combine(file_date, time_dt.time())
    return None



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


def parsed_dt_from_row(row: dict, file_path: str) -> tuple[Optional[datetime], Optional[str]]:
    """
    Attempt to build a datetime from separate date/time columns.
    Returns (datetime, source_tag) when successful.
    """
    date_candidates = (
        "parsed_date",
        "parse_date",
        "date",
        "sample_date",
        "run_date",
    )
    time_candidates = (
        "parsed_time",
        "parse_time",
        "time",
        "sample_time",
        "run_time",
    )
    date_val, _ = lookup_candidates(row, date_candidates)
    time_val, _ = lookup_candidates(row, time_candidates)
    if date_val or time_val:
        dt = combine_date_time(date_val, time_val, file_path)
        if dt:
            return dt, "parsed"
        log_timestamp_error(file_path, f"Failed to combine date/time values: date='{date_val}' time='{time_val}'")
    return None, None





def parse_time(s: str) -> Optional[datetime]:
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    if s.endswith('Z'):
        s = s[:-1]
    return _parse_timefmt(s, DATETIME_FORMATS)


def best_row_time(row: dict, timestamp_col: str, file_path: str) -> Tuple[datetime, str]:
    """
    Best-effort timestamp for a row, returning (datetime, source_tag).
    Priority:
      1) parsed_date + parsed_time
      2) explicit timestamp_col (if provided)
      3) inferred timestamp columns (contains run/sample timestamp words)
      4) file mtime
    """
    parsed_dt, source = parsed_dt_from_row(row, file_path)
    if parsed_dt:
        return parsed_dt, source or "parsed"

    col_value, col_key = (lookup_candidates(row, (timestamp_col,)) if timestamp_col else (None, None))
    if col_value:
        ts = parse_time(col_value)
        if ts:
            return ts, "derived"
        fallback_dt = combine_date_time(None, col_value, file_path)
        if fallback_dt:
            return fallback_dt, "derived"
        log_timestamp_error(file_path, f"Failed to parse configured timestamp column '{col_key}' value '{col_value}'")

    alt_value, alt_key = search_row_for_substrings(row, ALT_TIMESTAMP_SUBSTRINGS)
    if alt_value:
        ts = parse_time(alt_value)
        if ts:
            return ts, "derived"
        fallback_dt = combine_date_time(None, alt_value, file_path)
        if fallback_dt:
            return fallback_dt, "derived"
        log_timestamp_error(file_path, f"Failed to parse inferred timestamp column '{alt_key}' value '{alt_value}'")

    try:
        return datetime.fromtimestamp(os.path.getmtime(file_path)), "file_mtime"
    except Exception as exc:
        log_timestamp_error(file_path, f"mtime fallback failed: {exc}")
        return NOW(), "generated"


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
    def __init__(self, sample: str,
                 test: Optional[SampleTestSpec],
                 latest_value: Optional[float],
                 in_spec: Optional[bool],
                 low: Optional[float],
                 high: Optional[float],
                 note: str = "",
                 latest_time: Optional[datetime] = None,
                 timestamp_source: str = "",
                 from_cache: bool = False) -> None:
        self.sample = sample
        self.test = test
        self.latest_value = latest_value
        self.in_spec = in_spec
        self.low = low
        self.high = high
        self.note = note
        self.latest_time = latest_time
        self.timestamp_source = timestamp_source
        self.from_cache = from_cache


class BoxEvaluation:
    def __init__(self, status: str, results: List[ParameterResult],
                 last_good_qc: Optional[datetime],
                 latest_match_time: Optional[datetime],
                 reason: str,
                 used_parsed: bool,
                 from_cache: bool = False,
                 cache_info: Optional[Dict[str, object]] = None) -> None:
        self.status = status
        self.results = results
        self.last_good_qc = last_good_qc
        self.latest_match_time = latest_match_time
        self.reason = reason
        self.used_parsed = used_parsed  # True if parsed_date/parsed_time was used for any matched row
        self.from_cache = from_cache
        self.cache_info = dict(cache_info or {})


def _safe_float(s) -> Optional[float]:
    try:
        return float(str(s).strip())
    except Exception:
        return None


def evaluate_box(box: BoxConfig,
                 samples_by_name: Dict[str, SampleSpec],
                 sample_id_column: str,
                 rows: List[dict]) -> BoxEvaluation:
    """
    Evaluate a single box given the CSV rows and global sample/test catalog.
    Returns:
        BoxEvaluation with status RED/GREEN/YELLOW/UNKNOWN, per-test details,
        and whether parsed_date/time timestamps were used.
    """
    if not box.watched_targets:
        return BoxEvaluation(STATUS_UNKNOWN, [], None, None, "No sample/test pairs selected.", False)

    param_results: List[ParameterResult] = []
    any_fail = False
    all_have_values = True
    latest_any_time: Optional[datetime] = None
    in_spec_times: List[datetime] = []
    used_parsed_any = False

    for target in box.watched_targets:
        sample_name = target.sample
        test_name = target.test
        sample = samples_by_name.get(sample_name)
        if sample is None:
            display_name = sample_name or "(missing sample)"
            param_results.append(ParameterResult(
                sample=display_name,
                test=None,
                latest_value=None,
                in_spec=None,
                low=None,
                high=None,
                note=f"Sample '{display_name}' not found."
            ))
            any_fail = True
            continue

        tests_map = sample.tests_by_name()
        test = tests_map.get(test_name)
        if test is None:
            display_name = test_name or "(missing test)"
            param_results.append(ParameterResult(
                sample=sample.name,
                test=None,
                latest_value=None,
                in_spec=None,
                low=None,
                high=None,
                note=f"Test '{display_name}' not found in sample '{sample.name}'."
            ))
            any_fail = True
            continue

        matches: List[Tuple[dict, datetime, str]] = []
        for r in rows:
            sid = ci_lookup(r, sample_id_column)
            if sid is not None and str(sid).strip() == sample.sample_id_val:
                ts, ts_source = best_row_time(r, box.timestamp_col, box.csv_path)
                matches.append((r, ts, ts_source))

        low = test.expected - test.k * test.std_dev
        high = test.expected + test.k * test.std_dev

        if not matches:
            param_results.append(ParameterResult(
                sample=sample.name,
                test=test,
                latest_value=None,
                in_spec=None,
                low=low,
                high=high,
                note="No matching rows for sample ID.")
            )
            all_have_values = False
            continue

        matches.sort(key=lambda x: x[1])
        latest_row, latest_time, ts_source = matches[-1]
        if ts_source == 'parsed':
            used_parsed_any = True
        latest_any_time = max(latest_any_time, latest_time) if latest_any_time else latest_time

        raw = ci_lookup(latest_row, test.value_col)
        val = _safe_float(raw)

        if val is None:
            param_results.append(ParameterResult(
                sample=sample.name,
                test=test,
                latest_value=None,
                in_spec=None,
                low=low,
                high=high,
                latest_time=latest_time,
                note=f"No numeric value in '{test.value_col}'.",
                timestamp_source=ts_source
            ))
            all_have_values = False
            continue

        in_spec = (low <= val <= high)
        if in_spec:
            in_spec_times.append(latest_time)
        else:
            any_fail = True

        param_results.append(ParameterResult(
            sample=sample.name,
            test=test,
            latest_value=val,
            in_spec=in_spec,
            low=low,
            high=high,
            latest_time=latest_time,
            timestamp_source=ts_source
        ))

    if any_fail:
        status = STATUS_RED
        reason = "At least one watched test is out of spec or missing."
        last_good_qc = None
    elif not all_have_values:
        status = STATUS_UNKNOWN
        reason = "Insufficient data for one or more tests."
        last_good_qc = None
    else:
        status = STATUS_GREEN
        reason = "All watched tests are within expected ranges."
        last_good_qc = min(in_spec_times) if in_spec_times else None

    # Staleness (YELLOW) using the derived last_good_qc (caller may override with fallback clock)
    if status == STATUS_GREEN and last_good_qc:
        if (NOW() - last_good_qc) > timedelta(hours=box.qc_expire_hours):
            status = STATUS_YELLOW
            reason = "Last in-spec QC is stale."

    return BoxEvaluation(status=status, results=param_results,
                         last_good_qc=last_good_qc,
                         latest_match_time=latest_any_time,
                         reason=reason,
                         used_parsed=used_parsed_any)


