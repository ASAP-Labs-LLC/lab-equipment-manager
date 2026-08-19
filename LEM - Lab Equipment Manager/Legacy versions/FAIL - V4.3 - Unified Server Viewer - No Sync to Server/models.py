#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
models.py - Data models and constants for Lab Manager Map.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Tuple


# ---- Status constants
STATUS_GREEN   = "GREEN"
STATUS_RED     = "RED"
STATUS_YELLOW  = "YELLOW"
STATUS_DEAD    = "DEAD-LINE"
STATUS_SERVICE = "SERVICE"
STATUS_UNKNOWN = "UNKNOWN"


@dataclass
class SampleTestSpec:
    """Test definition for a specific sample."""
    name: str
    value_col: str
    expected: float
    std_dev: float
    k: float = 2.0
    units: str = ""

    def serialize(self) -> dict:
        return {
            "name": self.name,
            "value_col": self.value_col,
            "expected": float(self.expected),
            "std_dev": float(self.std_dev),
            "k": float(self.k),
            "units": self.units,
        }

    @staticmethod
    def from_dict(d: dict) -> "SampleTestSpec":
        return SampleTestSpec(
            name=str(d.get("name", "")),
            value_col=str(d.get("value_col", "")),
            expected=float(d.get("expected", 0.0)),
            std_dev=float(d.get("std_dev", 0.0)),
            k=float(d.get("k", 2.0)),
            units=str(d.get("units", "")),
        )


@dataclass
class SampleSpec:
    """Sample definition containing sample ID value and nested tests."""
    name: str
    sample_id_val: str
    tests: List[SampleTestSpec] = field(default_factory=list)

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
            tests=[SampleTestSpec.from_dict(x) for x in d.get("tests", [])],
        )

    def tests_by_name(self) -> Dict[str, SampleTestSpec]:
        return {t.name: t for t in self.tests}

@dataclass
class WatchedTarget:
    """Link a machine to a sample/test pair."""
    sample: str
    test: str

    def serialize(self) -> dict:
        return {"sample": self.sample, "test": self.test}

    @staticmethod
    def from_dict(d: dict) -> "WatchedTarget":
        return WatchedTarget(sample=str(d.get("sample", "")), test=str(d.get("test", "")))


@dataclass
class LegacyTestSpec:
    """Legacy flat test definition for compatibility when loading older configs."""
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
    def from_dict(d: dict) -> "LegacyTestSpec":
        return LegacyTestSpec(
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
    sample_preset_id: str = ""
    watched_targets: List[WatchedTarget] = field(default_factory=list)
    pos: Tuple[float, float] = (20.0, 20.0)
    size: Tuple[float, float] = (240.0, 130.0)
    locked: bool = False
    manual_override: str = ""
    version: int = 1

    def serialize(self) -> dict:
        return {
            "uid": self.uid,
            "title": self.title,
            "csv_path": self.csv_path,
            "timestamp_col": self.timestamp_col,
            "qc_expire_hours": self.qc_expire_hours,
             "sample_preset_id": self.sample_preset_id,
            "watched_targets": [wt.serialize() for wt in self.watched_targets],
            "pos": list(self.pos),
            "size": list(self.size),
            "locked": self.locked,
            "manual_override": self.manual_override,
            "version": int(self.version),
        }

    @staticmethod
    def from_dict(d: dict) -> "BoxConfig":
        return BoxConfig(
            uid=str(d.get("uid", "")),
            title=str(d.get("title", "Machine")),
            csv_path=str(d.get("csv_path", "")),
            timestamp_col=str(d.get("timestamp_col", "")),
            qc_expire_hours=float(d.get("qc_expire_hours", 24.0)),
            sample_preset_id=str(d.get("sample_preset_id", "")),
            watched_targets=[WatchedTarget.from_dict(x) for x in d.get("watched_targets", [])],
            pos=tuple(d.get("pos", [20.0, 20.0])),
            size=tuple(d.get("size", [240.0, 130.0])),
            locked=bool(d.get("locked", False)),
            manual_override=str(d.get("manual_override", "")),
            version=int(d.get("version", 1) or 1),
        )


@dataclass
class AppConfig:
    version: int
    poll_minutes: int
    map_locked: bool
    sample_id_column: str = "Lab ID"
    samples: List[SampleSpec] = field(default_factory=list)
    boxes: List[BoxConfig] = field(default_factory=list)

    # ---- Daily report settings
    report_enabled: bool = False
    report_time: str = "17:00"     # HH:MM 24h (local)
    report_dir: str = ""           # destination folder
    last_report_date: str = ""     # YYYY-MM-DD (local) - last exported day
    status_log_dir: str = ""       # directory for status change log

    # ---- Fallback "first in-spec today" clock when CSV lacks parsed_date/time
    first_inspec_date: str = ""                # YYYY-MM-DD (local) for the map below
    first_inspec_map: Dict[str, str] = field(default_factory=dict)  # {box_uid: ISO8601 local time}

    # ---- UI view state and status updates persistence
    view_center: Tuple[float, float] = (0.0, 0.0)
    view_zoom: float = 1.0
    status_updates: List[dict] = field(default_factory=list)
    theme_mode: str = "light"  # "light" or "dark"
    app_font_family: str = ""
    app_font_size: int = 10
    imported_fonts: List[str] = field(default_factory=list)
    ui_scale: float = 1.0
    custom_qss_path: str = ""
    # Main window docking/geometry state (hex-encoded QByteArray)
    win_state_hex: str = ""
    win_geom_hex: str = ""

    def serialize(self) -> dict:
        return {
            "version": self.version,
            "poll_minutes": int(self.poll_minutes),
            "map_locked": bool(self.map_locked),
            "sample_id_column": str(self.sample_id_column),
            "samples": [s.serialize() for s in self.samples],
            "boxes": [b.serialize() for b in self.boxes],
            "report_enabled": bool(self.report_enabled),
            "report_time": str(self.report_time),
            "report_dir": str(self.report_dir),
            "last_report_date": str(self.last_report_date),
            "first_inspec_date": str(self.first_inspec_date),
            "first_inspec_map": dict(self.first_inspec_map),
            "status_log_dir": str(self.status_log_dir),
            "view_center": list(self.view_center),
            "view_zoom": float(self.view_zoom),
            "status_updates": list(self.status_updates),
            "theme_mode": str(self.theme_mode),
            "app_font_family": str(self.app_font_family),
            "app_font_size": int(self.app_font_size),
            "imported_fonts": list(self.imported_fonts),
            "ui_scale": float(self.ui_scale),
            "custom_qss_path": str(self.custom_qss_path),
            "win_state_hex": str(self.win_state_hex or ""),
            "win_geom_hex": str(self.win_geom_hex or ""),
        }

    @staticmethod
    def from_dict(d: dict) -> "AppConfig":
        raw = dict(d)
        samples_data = raw.get("samples")
        boxes_data = raw.get("boxes", [])
        sample_id_column = raw.get("sample_id_column")

        if samples_data is None:
            legacy_tests = [LegacyTestSpec.from_dict(x) for x in raw.get("tests", [])]
            sample_map: Dict[Tuple[str, str], dict] = {}
            test_to_sample: Dict[str, str] = {}
            for t in legacy_tests:
                key = (t.sample_id_col, t.sample_id_val)
                sample_name = t.sample_id_val or t.name or f"Sample {len(sample_map) + 1}"
                if key not in sample_map:
                    sample_map[key] = {
                        "name": sample_name,
                        "sample_id_col": t.sample_id_col,
                        "sample_id_val": t.sample_id_val,
                        "tests": [],
                    }
                sample_entry = sample_map[key]
                if sample_entry["name"] != sample_name and t.sample_id_val:
                    sample_entry["name"] = sample_name
                sample_entry["tests"].append({
                    "name": t.name,
                    "value_col": t.value_col,
                    "expected": t.expected,
                    "std_dev": t.std_dev,
                    "k": t.k,
                    "units": t.units,
                })
                test_to_sample[t.name] = sample_entry["name"]
                if not sample_id_column and t.sample_id_col:
                    sample_id_column = t.sample_id_col
            samples_data = list(sample_map.values())

            converted_boxes: List[dict] = []
            for b in boxes_data:
                watched_targets = []
                for entry in b.get("watched_targets", []):
                    if isinstance(entry, dict) and "sample" in entry and "test" in entry:
                        watched_targets.append(entry)
                if not watched_targets:
                    for tname in b.get("watched_tests", []):
                        sample_name = test_to_sample.get(tname, "")
                        watched_targets.append({"sample": sample_name, "test": tname})
                new_box = dict(b)
                new_box["watched_targets"] = watched_targets
                new_box.pop("watched_tests", None)
                converted_boxes.append(new_box)
            boxes_data = converted_boxes
            raw["samples"] = samples_data

        cleaned_samples: List[dict] = []
        for entry in raw.get("samples", []):
            entry_dict = dict(entry)
            col = entry_dict.pop("sample_id_col", None)
            if not sample_id_column and col:
                sample_id_column = col
            cleaned_samples.append(entry_dict)
        samples = [SampleSpec.from_dict(x) for x in cleaned_samples]
        boxes = [BoxConfig.from_dict(x) for x in boxes_data]

        if not sample_id_column:
            sample_id_column = "Lab ID"

        return AppConfig(
            version=int(raw.get("version", 5)),
            poll_minutes=int(raw.get("poll_minutes", 5)),
            map_locked=bool(raw.get("map_locked", False)),
            sample_id_column=sample_id_column,
            samples=samples,
            boxes=boxes,
            report_enabled=bool(raw.get("report_enabled", False)),
            report_time=str(raw.get("report_time", "17:00")),
            report_dir=str(raw.get("report_dir", "")),
            last_report_date=str(raw.get("last_report_date", "")),
            first_inspec_date=str(raw.get("first_inspec_date", "")),
            first_inspec_map=dict(raw.get("first_inspec_map", {})),
            status_log_dir=str(raw.get("status_log_dir", "")),
            view_center=tuple(raw.get("view_center", [0.0, 0.0])),
            view_zoom=float(raw.get("view_zoom", 1.0)),
            status_updates=list(raw.get("status_updates", [])),
            theme_mode=str(raw.get("theme_mode", "light")),
            app_font_family=str(raw.get("app_font_family", "")),
            app_font_size=int(raw.get("app_font_size", 10)),
            imported_fonts=list(raw.get("imported_fonts", [])),
            ui_scale=float(raw.get("ui_scale", 1.0)),
            custom_qss_path=str(raw.get("custom_qss_path", "")),
            win_state_hex=str(raw.get("win_state_hex", "")),
            win_geom_hex=str(raw.get("win_geom_hex", "")),
        )
