#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
last_seen_cache.py - Persistent storage for the last successfully seen sample
data per machine. Helps preserve instrument state across CSV rotations.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional, Sequence

from data_source import BoxEvaluation, ParameterResult
from models import (
    AppConfig,
    BoxConfig,
    SampleSpec,
    STATUS_GREEN,
    STATUS_RED,
    STATUS_UNKNOWN,
    STATUS_YELLOW,
)

NOW = datetime.now
DEFAULT_MAX_AGE_DAYS = 7


def _dt_to_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.replace(microsecond=0).isoformat()


def _dt_from_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _watch_signature(box: BoxConfig) -> Sequence[str]:
    sig = []
    for target in box.watched_targets:
        sig.append(f"{target.sample}::{target.test}")
    return tuple(sorted(sig))


class LastSeenCache:
    """
    Lightweight JSON-backed cache storing last-known evaluation metadata per box.
    """

    def __init__(self, base_dir: str, filename: str = "last_seen_cache.json",
                 max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> None:
        self.path = os.path.join(base_dir, filename)
        self.max_age = timedelta(days=max(1, max_age_days))
        self._data: Dict[str, Any] = {"version": 1, "boxes": {}}
        self._dirty = False
        self._load()
        self.prune()

    # ----- lifecycle -------------------------------------------------
    def _load(self) -> None:
        if not os.path.exists(self.path):
            self._data = {"version": 1, "boxes": {}}
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict) and "boxes" in raw:
                self._data = raw
            else:
                self._data = {"version": 1, "boxes": {}}
        except Exception:
            self._data = {"version": 1, "boxes": {}}

    def flush(self) -> None:
        if not self._dirty:
            return
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
            self._dirty = False
        except Exception:
            # Ignore write errors; cache persistence is best-effort.
            pass

    def prune(self, now: Optional[datetime] = None) -> None:
        now = now or NOW()
        boxes = self._data.get("boxes", {})
        removed = False
        for uid, entry in list(boxes.items()):
            cached_at = _dt_from_iso(entry.get("cached_at"))
            if cached_at and (now - cached_at) > self.max_age:
                boxes.pop(uid, None)
                removed = True
        if removed:
            self._dirty = True

    # ----- interactions ----------------------------------------------
    def sync_config(self, cfg: AppConfig) -> None:
        """
        Remove cache entries for boxes that no longer exist and prune tests
        that are no longer being watched.
        """
        boxes = self._data.setdefault("boxes", {})
        valid_uids = {b.uid for b in cfg.boxes}
        removed_box = False
        for uid in list(boxes.keys()):
            if uid not in valid_uids:
                boxes.pop(uid, None)
                removed_box = True
        if removed_box:
            self._dirty = True

        # Update watcher signatures
        for box in cfg.boxes:
            entry = boxes.get(box.uid)
            if not entry:
                continue
            sig = _watch_signature(box)
            prev_sig = tuple(entry.get("watch_signature") or ())
            if sig != prev_sig:
                keep = set(sig)
                results = entry.get("results") or []
                filtered = [
                    r for r in results
                    if f"{r.get('sample')}::{r.get('test')}" in keep
                ]
                if len(filtered) != len(results):
                    entry["results"] = filtered
                    self._dirty = True
                entry["watch_signature"] = list(sig)
                # Drop empty cache entries after pruning
                if not filtered:
                    boxes.pop(box.uid, None)
                    self._dirty = True

        self.prune()

    def update_box(self, box: BoxConfig, evaluation: Any) -> None:
        """
        Store the latest evaluation for a box when fresh data is available.
        Expects evaluation to expose attributes compatible with data_source.BoxEvaluation.
        """
        if evaluation is None:
            return
        latest_time = getattr(evaluation, "latest_match_time", None)
        if latest_time is None:
            return
        boxes = self._data.setdefault("boxes", {})
        entry = boxes.get(box.uid, {})
        boxes[box.uid] = entry

        first_seen = _dt_from_iso(entry.get("first_seen")) or latest_time
        now = NOW()
        entry.update({
            "box_uid": box.uid,
            "box_title": box.title,
            "csv_path": box.csv_path,
            "status": getattr(evaluation, "status", ""),
            "reason": getattr(evaluation, "reason", ""),
            "last_good_qc": _dt_to_iso(getattr(evaluation, "last_good_qc", None)),
            "latest_match_time": _dt_to_iso(latest_time),
            "used_parsed": bool(getattr(evaluation, "used_parsed", False)),
            "cached_at": _dt_to_iso(now),
            "first_seen": _dt_to_iso(first_seen),
            "watch_signature": list(_watch_signature(box)),
        })
        entry["results"] = []
        for pr in getattr(evaluation, "results", []):
            test_name = pr.test.name if getattr(pr, "test", None) else None
            entry["results"].append({
                "sample": getattr(pr, "sample", ""),
                "test": test_name,
                "latest_value": getattr(pr, "latest_value", None),
                "in_spec": getattr(pr, "in_spec", None),
                "note": getattr(pr, "note", ""),
                "latest_time": _dt_to_iso(getattr(pr, "latest_time", None)),
                "timestamp_source": getattr(pr, "timestamp_source", ""),
            })
        self._dirty = True

    def get_box_entry(self, box_uid: str) -> Optional[Dict[str, Any]]:
        boxes = self._data.get("boxes", {})
        entry = boxes.get(box_uid)
        if not entry:
            return None
        cached_at = _dt_from_iso(entry.get("cached_at"))
        if cached_at and (NOW() - cached_at) > self.max_age:
            boxes.pop(box_uid, None)
            self._dirty = True
            return None
        return dict(entry)

    def remove_box(self, box_uid: str) -> None:
        boxes = self._data.get("boxes", {})
        if box_uid in boxes:
            boxes.pop(box_uid, None)
            self._dirty = True

    def tracked_pairs(self, box_uid: str) -> Iterable[str]:
        entry = self._data.get("boxes", {}).get(box_uid) or {}
        for r in entry.get("results") or []:
            yield f"{r.get('sample')}::{r.get('test')}"


def build_evaluation_from_entry(entry: Dict[str, Any],
                                box: BoxConfig,
                                samples_by_name: Dict[str, SampleSpec]) -> Optional[BoxEvaluation]:
    """
    Convert a cached entry back into a BoxEvaluation instance.
    """
    results = []
    missing_pairs = False
    for item in entry.get("results") or []:
        sample_name = item.get("sample") or ""
        test_name = item.get("test") or ""
        sample = samples_by_name.get(sample_name)
        test = None
        if sample and test_name:
            test = sample.tests_by_name().get(test_name)
            if test is None:
                missing_pairs = True
        elif test_name:
            missing_pairs = True
        latest_time = _dt_from_iso(item.get("latest_time"))
        pr = ParameterResult(
            sample=sample_name,
            test=test,
            latest_value=item.get("latest_value"),
            in_spec=item.get("in_spec"),
            low=(test.expected - test.k * test.std_dev) if test else None,
            high=(test.expected + test.k * test.std_dev) if test else None,
            note=item.get("note", ""),
            latest_time=latest_time,
            timestamp_source=item.get("timestamp_source", ""),
            from_cache=True,
        )
        results.append(pr)

    if not results:
        return None

    last_good_qc = _dt_from_iso(entry.get("last_good_qc"))
    latest_match_time = _dt_from_iso(entry.get("latest_match_time"))
    cached_at = _dt_from_iso(entry.get("cached_at"))
    first_seen = _dt_from_iso(entry.get("first_seen"))

    status = entry.get("status") or STATUS_UNKNOWN
    reason = entry.get("reason") or "Cached data"

    # Expiry logic: reuse cached status but enforce QC expiry thresholds.
    if status in (STATUS_GREEN, STATUS_YELLOW):
        anchor = last_good_qc or latest_match_time
        if anchor:
            age = NOW() - anchor
            yellow_threshold = timedelta(hours=max(1.0, box.qc_expire_hours))
            red_threshold = yellow_threshold * 2
            if age >= red_threshold:
                status = STATUS_RED
                reason = "Cached QC expired."
            elif age >= yellow_threshold and status != STATUS_RED:
                status = STATUS_YELLOW
                if "(cached)" not in reason.lower():
                    reason = "Last in-spec QC is stale (cached)."

    ev = BoxEvaluation(
        status=status,
        results=results,
        last_good_qc=last_good_qc,
        latest_match_time=latest_match_time,
        reason=reason,
        used_parsed=bool(entry.get("used_parsed")),
        from_cache=True,
    )
    ev.cache_info = {
        "cached_at": cached_at,
        "first_seen": first_seen,
        "box_title": entry.get("box_title", ""),
    }
    if missing_pairs:
        ev.reason = f"{ev.reason} (some watched tests not cached)"
    return ev

