#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
models.py — Data models and constants for Lab Manager Map.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Tuple, Dict, Optional


# ---- Status constants
STATUS_GREEN   = "GREEN"
STATUS_RED     = "RED"
STATUS_YELLOW  = "YELLOW"
STATUS_DEAD    = "DEAD-LINE"
STATUS_SERVICE = "SERVICE"
STATUS_UNKNOWN = "UNKNOWN"


@dataclass
class TestDef:
    """Global test definition (value source and units)."""
    name: str
    value_col: str
    units: str = ""

    def serialize(self) -> dict:
        return {"name": self.name, "value_col": self.value_col, "units": self.units}

    @staticmethod
    def from_dict(d: dict) -> "TestDef":
        return TestDef(
            name=str(d.get("name", "")),
            value_col=str(d.get("value_col", "")),
            units=str(d.get("units", "")),
        )


@dataclass
class SampleTestValue:
    test_name: str
    value_col: str
    expected: float
    std_dev: float
    k: float = 2.0
    units: str = ""

    def serialize(self) -> dict:
        return {
            "test_name": self.test_name,
            "value_col": self.value_col,
            "expected": float(self.expected),
            "std_dev": float(self.std_dev),
            "k": float(self.k),
            "units": self.units,
        }

    @staticmethod
    def from_dict(d: dict) -> "SampleTestValue":
        return SampleTestValue(
            test_name=str(d.get("test_name", "")),
            value_col=str(d.get("value_col", "Value")),
            expected=float(d.get("expected", 0.0)),
            std_dev=float(d.get("std_dev", 0.0)),
            k=float(d.get("k", 2.0)),
            units=str(d.get("units", "")),
        )


@dataclass
class SampleSpec:
    name: str
    sample_id_val: str
    tests: List[SampleTestValue] = field(default_factory=list)

    def serialize(self) -> dict:
        return {
            "name": self.name,
            "sample_id_val": self.sample_id_val,
            "tests": [t.serialize() for t in self.tests],
        }

    @staticmethod
    def from_dict(d: dict) -> "SampleSpec":
        return SampleSpec(
            name=str(d.get("name", "")),
            sample_id_val=str(d.get("sample_id_val", "")),
            tests=[SampleTestValue.from_dict(x) for x in d.get("tests", [])],
        )


@dataclass
class BoxConfig:
    uid: str
    title: str
    csv_path: str
    timestamp_col: str = ""
    qc_expire_hours: float = 24.0
    watched_tests: List[str] = field(default_factory=list)
    pos: Tuple[float, float] = (20.0, 20.0)
    size: Tuple[float, float] = (240.0, 130.0)
    locked: bool = False
    manual_override: str = ""
    sample_refs: List[str] = field(default_factory=list)
    affects_status: bool = True
    # Per-test control: which watched tests affect status
    affects_tests: List[str] = field(default_factory=list)

    def serialize(self) -> dict:
        return {
            "uid": self.uid,
            "title": self.title,
            "csv_path": self.csv_path,
            "timestamp_col": self.timestamp_col,
            "qc_expire_hours": self.qc_expire_hours,
            "watched_tests": list(self.watched_tests),
            "pos": list(self.pos),
            "size": list(self.size),
            "locked": self.locked,
            "manual_override": self.manual_override,
            "sample_refs": list(self.sample_refs),
            "affects_status": bool(self.affects_status),
            "affects_tests": list(self.affects_tests),
        }

    @staticmethod
    def from_dict(d: dict) -> "BoxConfig":
        # Backward compatibility: if no per-test list provided, derive from affects_status
        raw_watched = [str(x) for x in d.get("watched_tests", [])]
        has_affects_tests = "affects_tests" in d
        affects_status_flag = bool(d.get("affects_status", True))
        affects_tests_list = [str(x) for x in d.get("affects_tests", [])] if has_affects_tests else (raw_watched if affects_status_flag else [])
        return BoxConfig(
            uid=str(d.get("uid", "")),
            title=str(d.get("title", "Machine")),
            csv_path=str(d.get("csv_path", "")),
            timestamp_col=str(d.get("timestamp_col", "")),
            qc_expire_hours=float(d.get("qc_expire_hours", 24.0)),
            watched_tests=raw_watched,
            pos=tuple(d.get("pos", [20.0, 20.0])),
            size=tuple(d.get("size", [240.0, 130.0])),
            locked=bool(d.get("locked", False)),
            manual_override=str(d.get("manual_override", "")),
            sample_refs=[str(x) for x in (d.get("sample_refs") or ([] if not d.get("sample_ref") else [d.get("sample_ref")]))],
            affects_status=affects_status_flag,
            affects_tests=affects_tests_list,
        )


@dataclass
class AppConfig:
    version: int
    poll_minutes: int
    map_locked: bool
    tests: List[TestDef] = field(default_factory=list)
    samples: List[SampleSpec] = field(default_factory=list)
    sample_id_col: str = "Lab ID"
    boxes: List[BoxConfig] = field(default_factory=list)

    # ---- Daily report settings (already present in your build)
    report_enabled: bool = False
    report_time: str = "17:00"
    report_dir: str = ""
    last_report_date: str = ""

    # ---- Fallback “first in-spec today” clock (already present)
    first_inspec_date: str = ""
    first_inspec_map: Dict[str, str] = field(default_factory=dict)

    # ---- NEW: View state (zoom + scene center), so we restore camera on startup
    view_zoom: float = 1.0
    view_center: Tuple[float, float] = (0.0, 0.0)

    def serialize(self) -> dict:
        return {
            "version": self.version,
            "poll_minutes": int(self.poll_minutes),
            "map_locked": bool(self.map_locked),
            "tests": [t.serialize() for t in self.tests],
            "samples": [s.serialize() for s in self.samples],
            "sample_id_col": str(self.sample_id_col),
            "boxes": [b.serialize() for b in self.boxes],
            "report_enabled": bool(self.report_enabled),
            "report_time": str(self.report_time),
            "report_dir": str(self.report_dir),
            "last_report_date": str(self.last_report_date),
            "first_inspec_date": str(self.first_inspec_date),
            "first_inspec_map": dict(self.first_inspec_map),
            "view_zoom": float(self.view_zoom),
            "view_center": list(self.view_center),
        }

    @staticmethod
    def from_dict(d: dict) -> "AppConfig":
        # Migrate legacy per-test sample/limits to new structure if needed
        raw_tests = d.get("tests", [])
        legacy = any(isinstance(x, dict) and ("sample_id_val" in x or "expected" in x) for x in raw_tests)

        tests: List[TestDef]
        samples: List[SampleSpec]
        sample_id_col = str(d.get("sample_id_col", "Lab ID"))

        if legacy:
            # Deduplicate tests by name and group sample limits by sample_id_val
            seen_tests: Dict[str, TestDef] = {}
            grouped: Dict[str, SampleSpec] = {}
            for x in raw_tests:
                name = str(x.get("name", ""))
                value_col = str(x.get("value_col", ""))
                units = str(x.get("units", ""))
                if name and name not in seen_tests:
                    seen_tests[name] = TestDef(name=name, value_col=value_col, units=units)
                sid = str(x.get("sample_id_val", "")).strip()
                if sid:
                    ss = grouped.get(sid)
                    if not ss:
                        ss = SampleSpec(name=sid, sample_id_val=sid, tests=[])
                        grouped[sid] = ss
                    ss.tests.append(SampleTestValue(
                        test_name=name,
                        expected=float(x.get("expected", 0.0)),
                        std_dev=float(x.get("std_dev", 0.0)),
                        k=float(x.get("k", 2.0)),
                    ))
            tests = list(seen_tests.values())
            samples = list(grouped.values())
        else:
            tests = [TestDef.from_dict(x) for x in raw_tests]
            samples = [SampleSpec.from_dict(x) for x in d.get("samples", [])]

        return AppConfig(
            version=int(d.get("version", 5)),
            poll_minutes=int(d.get("poll_minutes", 5)),
            map_locked=bool(d.get("map_locked", False)),
            tests=tests,
            samples=samples,
            sample_id_col=sample_id_col,
            boxes=[BoxConfig.from_dict(x) for x in d.get("boxes", [])],
            report_enabled=bool(d.get("report_enabled", False)),
            report_time=str(d.get("report_time", "17:00")),
            report_dir=str(d.get("report_dir", "")),
            last_report_date=str(d.get("last_report_date", "")),
            first_inspec_date=str(d.get("first_inspec_date", "")),
            first_inspec_map=dict(d.get("first_inspec_map", {})),
            view_zoom=float(d.get("view_zoom", 1.0)),
            view_center=tuple(d.get("view_center", [0.0, 0.0])),
        )
