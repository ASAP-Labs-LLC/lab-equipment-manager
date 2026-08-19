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
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# Note: PyQt5 import removed - this is the web version (Flask) and PyQt5
# was a leftover from the desktop build. The CsvReadWorker class that
# depended on it was also unused by web_server.pyw and has been removed.

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




def qc_is_stale(result_time, now, hours: float) -> bool:
    """Has an in-spec QC result aged out? A rolling window, in real hours.

    V4 expired QC at the day boundary, so a standard run at 23:00 was stale at
    00:01 — an hour later — while one run at 00:30 lasted nearly 48. It also
    rounded the stored hours to whole days, which silently disabled any window
    shorter than a day.

    Deliberately duplicated in `lem_station_module.qc_is_stale`: the module cannot
    import from this package (LabStation loads it as a lone file). The pair are
    kept identical on purpose — if you change one, change both.
    """
    if result_time is None:
        return False
    return (now - result_time).total_seconds() >= max(0.0, float(hours)) * 3600.0

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


def best_row_time(row: dict, timestamp_col: str, file_path: str, file_mtime: Optional[float] = None) -> Tuple[datetime, str]:
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
        mtime = file_mtime if file_mtime is not None else (os.path.getmtime(file_path) if file_path else None)
        if mtime:
            return datetime.fromtimestamp(mtime), "file_mtime"
    except Exception as exc:
        log_timestamp_error(file_path, f"mtime fallback failed: {exc}")

    return NOW(), "generated"


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


@dataclass
class BoxEvaluation:
    status: str
    results: List[ParameterResult]
    last_good_qc: Optional[datetime]
    latest_match_time: Optional[datetime]
    reason: str
    used_parsed: bool
    # New multi-dimensional fields
    sub_statuses: Dict[str, dict] = field(default_factory=dict)  # qc, calibration, pm, testing
    context_results: Dict[str, str] = field(default_factory=dict) # Context -> Status
    overall_explanation: str = ""
    from_cache: bool = False
    cache_info: Optional[Dict[str, object]] = None

    def __init__(self, status: str, results: List[ParameterResult],
                 last_good_qc: Optional[datetime],
                 latest_match_time: Optional[datetime],
                 reason: str,
                 used_parsed: bool,
                 sub_statuses: Optional[Dict[str, dict]] = None,
                 context_results: Optional[Dict[str, str]] = None,
                 overall_explanation: str = "",
                 from_cache: bool = False,
                 cache_info: Optional[Dict[str, object]] = None) -> None:
        self.status = status
        self.results = results
        self.last_good_qc = last_good_qc
        self.latest_match_time = latest_match_time
        self.reason = reason
        self.used_parsed = used_parsed
        self.sub_statuses = sub_statuses or {}
        self.context_results = context_results or {}
        self.overall_explanation = overall_explanation or reason
        self.from_cache = from_cache
        self.cache_info = dict(cache_info or {})


def _safe_float(s) -> Optional[float]:
    try:
        return float(str(s).strip())
    except Exception:
        return None


def _normalized_sample_id(value) -> Optional[str]:
    if value is None:
        return None
    key = str(value).strip()
    return key or None


def build_sample_index(rows: List[dict], sample_id_column: str) -> Dict[str, List[dict]]:
    """
    Build a simple in-memory index: sample_id (normalized) -> list of rows.
    This avoids repeatedly scanning all rows for each watched target.
    """
    index: Dict[str, List[dict]] = {}
    for row in rows:
        sid = _normalized_sample_id(ci_lookup(row, sample_id_column))
        if sid is None:
            continue
        index.setdefault(sid, []).append(row)
    return index


def _compute_testing_sub_status(results: List[ParameterResult]) -> Tuple[str, str, Dict[str, str]]:
    """
    Derive Testing sub-status and context breakdown from parameter results.
    Context is derived from Sample Name.
    Returns: (status, explanation, context_map)
    """
    if not results:
        return STATUS_UNKNOWN, "No tests configured.", {}

    context_map: Dict[str, List[str]] = {} # Context -> [Status list]
    context_reasons: Dict[str, List[str]] = {}

    # 1. Group by context (Sample Name)
    for res in results:
        ctx = res.sample or "(unknown sample)"
        if ctx not in context_map:
            context_map[ctx] = []
            context_reasons[ctx] = []
        
        if res.latest_value is None:
            # Missing data
            context_map[ctx].append(STATUS_UNKNOWN)
            context_reasons[ctx].append(f"{res.test.name if res.test else 'Test'} missing data")
        elif res.in_spec is False:
            context_map[ctx].append(STATUS_RED)
            context_reasons[ctx].append(f"{res.test.name if res.test else 'Test'} out of spec ({res.latest_value})")
        else:
            context_map[ctx].append(STATUS_GREEN)
            
    # 2. Evaluate per context
    final_context_status: Dict[str, str] = {}
    context_explanations: List[str] = []
    
    overall_severity = STATUS_GREEN # Default if all good
    
    for ctx, statuses in context_map.items():
        if STATUS_RED in statuses:
            c_stat = STATUS_RED
        elif STATUS_UNKNOWN in statuses:
            c_stat = STATUS_UNKNOWN
        else:
            c_stat = STATUS_GREEN
        
        final_context_status[ctx] = c_stat
        
        if c_stat == STATUS_RED:
            # Add specific reasons
            reasons = [r for r in context_reasons[ctx] if "out of spec" in r]
            context_explanations.append(f"{ctx}: {', '.join(reasons)}")
            overall_severity = STATUS_RED
        elif c_stat == STATUS_UNKNOWN and overall_severity != STATUS_RED:
            # Only downgrade to UNKNOWN if not already RED
            overall_severity = STATUS_UNKNOWN

    if overall_severity == STATUS_RED:
        msg = f"Testing failed in contexts: {'; '.join(context_explanations)}."
    elif overall_severity == STATUS_UNKNOWN:
        msg = "Insufficient data for some contexts."
    else:
        msg = "All tests passed."

    return overall_severity, msg, final_context_status


def evaluate_box(box: BoxConfig,
                 samples_by_name: Dict[str, SampleSpec],
                 sample_id_column: str,
                 rows: List[dict],
                 sample_index: Optional[Dict[str, List[dict]]] = None,
                 row_time_cache: Optional[Dict[int, Tuple[datetime, str]]] = None,
                 file_mtime: Optional[float] = None,
                 maintenance_tasks: Optional[List[object]] = None) -> BoxEvaluation:
    """
    Evaluate a single box given the CSV rows and global sample/test catalog.
    
    Args:
        maintenance_tasks: Optional list of MaintenanceTemplate objects to evaluate PM and Calibration status.
    Returns:
        BoxEvaluation with full multi-dimensional status.
    """
    # --- 1. Testing Status Evaluation (Existing Logic + Context Grouping) ---
    param_results: List[ParameterResult] = []
    latest_any_time: Optional[datetime] = None
    in_spec_times: List[datetime] = []
    used_parsed_any = False

    if not box.watched_targets:
        test_status = STATUS_UNKNOWN
        test_reason = "No sample/test pairs selected."
        context_results = {}
    else:
        for target in box.watched_targets:
            sample_name = target.sample
            test_name = target.test
            sample = samples_by_name.get(sample_name)
            
            if sample is None:
                param_results.append(ParameterResult(
                    sample=sample_name or "(missing)", test=None, latest_value=None, 
                    in_spec=None, low=None, high=None, note="Sample not found."
                ))
                continue

            tests_map = sample.tests_by_name()
            test = tests_map.get(test_name)
            if test is None:
                param_results.append(ParameterResult(
                    sample=sample.name, test=None, latest_value=None,
                    in_spec=None, low=None, high=None, note=f"Test '{test_name}' not found."
                ))
                continue

            normalized_sample_id = _normalized_sample_id(sample.sample_id_val)
            low = test.expected - test.k * test.std_dev
            high = test.expected + test.k * test.std_dev
            
            if not normalized_sample_id:
                param_results.append(ParameterResult(
                    sample=sample.name, test=test, latest_value=None,
                    in_spec=None, low=low, high=high, note="Sample ID is blank."
                ))
                continue

            # Row Lookup
            matches: List[Tuple[dict, datetime, str]] = []
            candidate_rows = (sample_index or {}).get(normalized_sample_id)
            if candidate_rows is None:
                candidate_rows = []
                for r in rows:
                    sid = _normalized_sample_id(ci_lookup(r, sample_id_column))
                    if sid is not None and sid == normalized_sample_id:
                        candidate_rows.append(r)

            for r in candidate_rows:
                raw_val = ci_lookup(r, test.value_col)
                if raw_val is None or str(raw_val).strip() == "":
                    continue
                
                if row_time_cache is not None and id(r) in row_time_cache:
                    ts, ts_source = row_time_cache[id(r)]
                else:
                    ts, ts_source = best_row_time(r, box.timestamp_col, box.csv_path, file_mtime=file_mtime)
                    if row_time_cache is not None:
                        row_time_cache[id(r)] = (ts, ts_source)
                matches.append((r, ts, ts_source))

            if not matches:
                param_results.append(ParameterResult(
                    sample=sample.name, test=test, latest_value=None,
                    in_spec=None, low=low, high=high, note="No matching rows for sample ID.")
                )
                continue

            matches.sort(key=lambda x: x[1])
            latest_row, latest_time, ts_source = matches[-1]
            if ts_source == 'parsed':
                used_parsed_any = True
            latest_any_time = max(latest_any_time, latest_time) if latest_any_time else latest_time

            val = _safe_float(ci_lookup(latest_row, test.value_col))
            if val is None:
                param_results.append(ParameterResult(
                    sample=sample.name, test=test, latest_value=None,
                    in_spec=None, low=low, high=high, latest_time=latest_time,
                    note=f"No numeric value in '{test.value_col}'.",
                    timestamp_source=ts_source
                ))
                continue

            in_spec = (low <= val <= high)
            if in_spec:
                in_spec_times.append(latest_time)

            param_results.append(ParameterResult(
                sample=sample.name, test=test, latest_value=val,
                in_spec=in_spec, low=low, high=high, latest_time=latest_time,
                timestamp_source=ts_source
            ))

        test_status, test_reason, context_results = _compute_testing_sub_status(param_results)


    # --- 2. QC Status Evaluation ---
    last_good_qc = min(in_spec_times) if in_spec_times else None
    
    qc_status = STATUS_UNKNOWN
    qc_reason = "No valid QC data found."
    
    if test_status == STATUS_GREEN and last_good_qc:
        # Rolling window from when the standard was run — changed from V4's
        # calendar-day boundary on 2026-08-03 at Ryan's call, and kept identical
        # to `qc_is_stale` in the station module. Two engines disagreeing about
        # the same instrument is worse than either rule being wrong.
        if qc_is_stale(last_good_qc, NOW(), box.qc_expire_hours):
            qc_status = STATUS_YELLOW
            qc_reason = f"QC stale (Last valid: {last_good_qc.strftime('%Y-%m-%d %H:%M')})"
        else:
            qc_status = STATUS_GREEN
            qc_reason = "QC Fresh"
    elif test_status == STATUS_RED:
        qc_status = STATUS_RED
        qc_reason = "QC Out of Spec"
    elif test_status != STATUS_UNKNOWN:
         pass


    # --- 3. Maintenance (PM & Calibration) Evaluation ---
    # Now considers task lifecycle state (IN_PROGRESS, COMPLETED, CANCELLED) alongside due dates
    cal_status = STATUS_UNKNOWN
    cal_reason = "No Calibration Tasks"
    pm_status = STATUS_UNKNOWN
    pm_reason = "No PM Tasks"

    if maintenance_tasks:
        today = NOW().date()
        for task in maintenance_tasks:
            t_kind = getattr(task, "kind", "").lower()
            t_due_str = getattr(task, "next_due", None)
            t_name = getattr(task, "name", "Task")
            t_status = getattr(task, "status", "PENDING").upper()
            
            # Identify if it's a Cal task
            is_cal = "cal" in t_kind
            is_pm = not is_cal # Assumption: if not Cal, it's PM (or generic maintenance)

            # Upgrade to GREEN initially if it's the relevant type and still UNKNOWN
            if is_cal and cal_status == STATUS_UNKNOWN:
                cal_status = STATUS_GREEN
                cal_reason = "Calibration Up to Date"
            
            if is_pm and pm_status == STATUS_UNKNOWN:
                pm_status = STATUS_GREEN
                pm_reason = "PM Up to Date"

            if not t_due_str: continue
            try:
                due_date = datetime.strptime(t_due_str, "%Y-%m-%d").date()
            except:
                continue

            m_status = STATUS_GREEN
            m_msg = ""
            
            # Evaluate based on lifecycle state AND due date
            if t_status == "IN_PROGRESS":
                m_status = STATUS_YELLOW
                m_msg = f"In Progress: {t_name}"
            elif t_status == "COMPLETED":
                m_status = STATUS_GREEN
                m_msg = f"Completed: {t_name}"
            elif t_status == "CANCELLED":
                continue
            else:
                if due_date < today:
                    m_status = STATUS_RED
                    m_msg = f"Overdue: {t_name} ({t_due_str})"
                elif due_date == today:
                    m_status = STATUS_YELLOW
                    m_msg = f"Due Today: {t_name}"

            if m_status != STATUS_GREEN:
                if is_cal:
                    if m_status == STATUS_RED: cal_status = STATUS_RED
                    elif m_status == STATUS_YELLOW and cal_status != STATUS_RED: cal_status = STATUS_YELLOW
                    cal_reason = m_msg
                else: 
                    if m_status == STATUS_RED: pm_status = STATUS_RED
                    elif m_status == STATUS_YELLOW and pm_status != STATUS_RED: pm_status = STATUS_YELLOW
                    pm_reason = m_msg

    # --- 4. Overall Derivation ---
    sub_statuses = {
        "qc": {"status": qc_status, "reason": qc_reason},
        "calibration": {"status": cal_status, "reason": cal_reason},
        "pm": {"status": pm_status, "reason": pm_reason},
        "testing": {"status": test_status, "reason": test_reason},
    }
    
    # All dimensions contribute to RED/YELLOW
    all_statuses = [qc_status, cal_status, pm_status, test_status]
    
    # Only core dimensions contribute to UNKNOWN (Missing Data)
    # User Request: PM/Cal missing data should not affect overall status
    mid_statuses_for_unknown = [qc_status, test_status]
    
    if STATUS_RED in all_statuses:
        final_status = STATUS_RED
        drivers = [k for k, v in sub_statuses.items() if v["status"] == STATUS_RED]
        final_reason = f"Critical issues in: {', '.join(drivers).upper()}"
    elif STATUS_YELLOW in all_statuses:
        final_status = STATUS_YELLOW
        drivers = [k for k, v in sub_statuses.items() if v["status"] == STATUS_YELLOW]
        final_reason = f"Warnings in: {', '.join(drivers).upper()}"
    elif STATUS_UNKNOWN in mid_statuses_for_unknown:
        final_status = STATUS_UNKNOWN
        drivers = [k for k, v in sub_statuses.items() if v["status"] == STATUS_UNKNOWN and k in ['qc', 'testing']]
        final_reason = f"Missing data for: {', '.join(drivers).upper()}"
    else:
        final_status = STATUS_GREEN
        final_reason = "System nominal"

    return BoxEvaluation(
        status=final_status,
        results=param_results,
        last_good_qc=last_good_qc,
        latest_match_time=latest_any_time,
        reason=final_reason,
        used_parsed=used_parsed_any,
        sub_statuses=sub_statuses,
        context_results=context_results,
        overall_explanation=final_reason
    )


