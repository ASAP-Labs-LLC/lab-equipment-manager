#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
labcore_source.py — read QC results from LabCore and adapt them for the engine.

LEM's evaluation engine (data_source.evaluate_box) consumes a list of row dicts,
matching rows by a sample-ID column and reading a measurement from a value
column. This module produces exactly those rows from LabCore's QC tables, so the
V4 engine is reused without modification.

Mapping:
    LEM sample.sample_id_val  ->  LabCore lab_id
    LEM test.value_col        ->  LabCore test_name
    measurement               ->  sample_test_results.result_value
                                  (falling back to sample_tests.result)
    timestamp                 ->  updated_at, split into parsed_date + parsed_time
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from models import SampleSpec


def _split_timestamp(updated_at: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Split a LabCore ``updated_at`` into (date, time) strings for the engine.

    Handles ISO ('2023-01-01T09:00:00'), space-separated, and date-only values.
    Returns (None, None) when nothing usable is present so the engine falls back
    to its other timestamp sources.
    """
    if not updated_at:
        return None, None
    raw = str(updated_at).strip()
    if not raw:
        return None, None
    sep = "T" if "T" in raw else " "
    parts = raw.split(sep, 1)
    date_part = parts[0].strip() or None
    time_part = parts[1].strip() if len(parts) > 1 else None
    if time_part:
        # drop trailing timezone / fractional noise the engine doesn't need
        time_part = time_part.rstrip("Z").strip() or None
    return date_part, time_part


class LabCoreDataSource:
    """Fetch QC observations from LabCore and emit engine-compatible rows."""

    def __init__(self, gateway) -> None:
        self._gw = gateway

    def _latest_result(self, lab_id: str, test_name: str) -> Optional[Tuple[str, Optional[str]]]:
        """Return (value, updated_at) for the most recent observation, or None.

        Prefers sample_test_results (streamed by LabStation); falls back to
        sample_tests. Within the union, the newest updated_at wins.
        """
        res = self._gw.read_sql(
            """
            SELECT result_value AS value, updated_at, 2 AS pref
              FROM sample_test_results
             WHERE lab_id = ? AND test_name = ? AND result_value IS NOT NULL
                   AND TRIM(result_value) != ''
            UNION ALL
            SELECT result AS value, updated_at, 1 AS pref
              FROM sample_tests
             WHERE lab_id = ? AND test_name = ? AND result IS NOT NULL
                   AND TRIM(result) != ''
             ORDER BY updated_at DESC, pref DESC
             LIMIT 1
            """,
            [lab_id, test_name, lab_id, test_name],
        )
        if not res.get("ok") or not res.get("rows"):
            return None
        row = res["rows"][0]
        return str(row["value"]), row.get("updated_at")

    def _latest_results(self, pairs: List[Tuple[str, str]]) -> Dict[Tuple[str, str],
                                                                    Tuple[str, Optional[str]]]:
        """The newest observation for many (lab_id, test_name) pairs, in ONE read.

        `_latest_result` above answers for a single pair, which meant `load_rows`
        made one HTTPS round trip per (sample × test), sequentially — and each trip
        paid a full TLS setup on the way (see the 2026-08-03 CPU report). The
        window function picks the winner per pair server-side, so the same
        precedence rules apply and only the shape of the traffic changes:
        `sample_test_results` beats `sample_tests` at equal timestamps, newest
        `updated_at` first.
        """
        if not pairs:
            return {}
        placeholders = ", ".join("(?, ?)" for _ in pairs)
        flat: List[str] = []
        for lab_id, test_name in pairs:
            flat.extend((lab_id, test_name))
        res = self._gw.read_sql(
            f"""
            SELECT lab_id, test_name, value, updated_at FROM (
              SELECT lab_id, test_name, value, updated_at,
                     ROW_NUMBER() OVER (PARTITION BY lab_id, test_name
                                        ORDER BY updated_at DESC, pref DESC) AS rn
                FROM (
                  SELECT lab_id, test_name, result_value AS value, updated_at,
                         2 AS pref
                    FROM sample_test_results
                   WHERE (lab_id, test_name) IN (VALUES {placeholders})
                         AND result_value IS NOT NULL AND TRIM(result_value) != ''
                  UNION ALL
                  SELECT lab_id, test_name, result AS value, updated_at, 1 AS pref
                    FROM sample_tests
                   WHERE (lab_id, test_name) IN (VALUES {placeholders})
                         AND result IS NOT NULL AND TRIM(result) != ''
                )
            ) WHERE rn = 1
            """,
            flat + flat,
        )
        if res.get("error"):
            return {}
        out: Dict[Tuple[str, str], Tuple[str, Optional[str]]] = {}
        for row in res.get("rows") or []:
            key = (str(row.get("lab_id")), str(row.get("test_name")))
            out[key] = (str(row.get("value")), row.get("updated_at"))
        return out

    def load_rows(self, samples: List[SampleSpec], sample_id_column: str) -> List[dict]:
        """Build engine rows for every (sample_id_val, test.value_col) LEM watches.

        One row per observation, carrying the sample-ID column, the single test's
        value column, and the split timestamp.
        """
        wanted: List[Tuple[str, str, object]] = []
        for sample in samples:
            lab_id = (sample.sample_id_val or "").strip()
            if not lab_id:
                continue
            for test in sample.tests:
                test_name = (test.value_col or "").strip()
                if not test_name:
                    continue
                wanted.append((lab_id, test_name, test))

        latest_by_pair = self._latest_results(
            list(dict.fromkeys((lab_id, name) for lab_id, name, _t in wanted)))

        rows: List[dict] = []
        for lab_id, test_name, test in wanted:
            latest = latest_by_pair.get((lab_id, test_name))
            if latest is not None:
                value, updated_at = latest
                date_part, time_part = _split_timestamp(updated_at)
                row: Dict[str, str] = {sample_id_column: lab_id,
                                       test.value_col: value}
                if date_part:
                    row["parsed_date"] = date_part
                if time_part:
                    row["parsed_time"] = time_part
                rows.append(row)
        return rows
