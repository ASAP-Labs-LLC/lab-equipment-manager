#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
models.py — Data models and constants for Lab Manager Map.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Tuple, Dict


# ---- Status constants
STATUS_GREEN   = "GREEN"
STATUS_RED     = "RED"
STATUS_YELLOW  = "YELLOW"
STATUS_DEAD    = "DEAD-LINE"
STATUS_SERVICE = "SERVICE"
STATUS_UNKNOWN = "UNKNOWN"


@dataclass
class TestSpec:
    name: str
    sample_id_col: str
    sample_id_val: str
    value_col: str
    expected: float
    std_dev: float
    k: float = 2.0
    units: str = ""

    def serialize(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "TestSpec":
        return TestSpec(
            name=str(d.get("name", "")),
            sample_id_col=str(d.get("sample_id_col", "Lab ID")),
            sample_id_val=str(d.get("sample_id_val", "")),
            value_col=str(d.get("value_col", "")),
            expected=float(d.get("expected", 0.0)),
            std_dev=float(d.get("std_dev", 0.0)),
            k=float(d.get("k", 2.0)),
            units=str(d.get("units", "")),
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
        }

    @staticmethod
    def from_dict(d: dict) -> "BoxConfig":
        return BoxConfig(
            uid=str(d.get("uid", "")),
            title=str(d.get("title", "Machine")),
            csv_path=str(d.get("csv_path", "")),
            timestamp_col=str(d.get("timestamp_col", "")),
            qc_expire_hours=float(d.get("qc_expire_hours", 24.0)),
            watched_tests=[str(x) for x in d.get("watched_tests", [])],
            pos=tuple(d.get("pos", [20.0, 20.0])),
            size=tuple(d.get("size", [240.0, 130.0])),
            locked=bool(d.get("locked", False)),
            manual_override=str(d.get("manual_override", "")),
        )


@dataclass
class AppConfig:
    version: int
    poll_minutes: int
    map_locked: bool
    tests: List[TestSpec] = field(default_factory=list)
    boxes: List[BoxConfig] = field(default_factory=list)

    # ---- Daily report settings
    report_enabled: bool = False
    report_time: str = "17:00"     # HH:MM 24h (local)
    report_dir: str = ""           # destination folder
    last_report_date: str = ""     # YYYY-MM-DD (local) — last exported day

    # ---- Fallback “first in-spec today” clock when CSV lacks parsed_date/time
    first_inspec_date: str = ""                # YYYY-MM-DD (local) for the map below
    first_inspec_map: Dict[str, str] = field(default_factory=dict)  # {box_uid: ISO8601 local time}

    def serialize(self) -> dict:
        return {
            "version": self.version,
            "poll_minutes": int(self.poll_minutes),
            "map_locked": bool(self.map_locked),
            "tests": [t.serialize() for t in self.tests],
            "boxes": [b.serialize() for b in self.boxes],
            "report_enabled": bool(self.report_enabled),
            "report_time": str(self.report_time),
            "report_dir": str(self.report_dir),
            "last_report_date": str(self.last_report_date),
            "first_inspec_date": str(self.first_inspec_date),
            "first_inspec_map": dict(self.first_inspec_map),
        }

    @staticmethod
    def from_dict(d: dict) -> "AppConfig":
        return AppConfig(
            version=int(d.get("version", 3)),
            poll_minutes=int(d.get("poll_minutes", 5)),
            map_locked=bool(d.get("map_locked", False)),
            tests=[TestSpec.from_dict(x) for x in d.get("tests", [])],
            boxes=[BoxConfig.from_dict(x) for x in d.get("boxes", [])],
            report_enabled=bool(d.get("report_enabled", False)),
            report_time=str(d.get("report_time", "17:00")),
            report_dir=str(d.get("report_dir", "")),
            last_report_date=str(d.get("last_report_date", "")),
            first_inspec_date=str(d.get("first_inspec_date", "")),
            first_inspec_map=dict(d.get("first_inspec_map", {})),
        )
