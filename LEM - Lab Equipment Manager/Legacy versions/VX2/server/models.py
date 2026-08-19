from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from models import AppConfig
from data_source import ParameterResult


def _serialize_parameter_result(pr: ParameterResult) -> dict:
    test_name = getattr(getattr(pr, "test", None), "name", None)
    units = getattr(getattr(pr, "test", None), "units", None)
    return {
        "sample": pr.sample,
        "test": test_name,
        "latest_value": pr.latest_value,
        "in_spec": pr.in_spec,
        "low": pr.low,
        "high": pr.high,
        "note": pr.note,
        "units": units,
        "latest_time": pr.latest_time.isoformat(timespec="seconds") if pr.latest_time else None,
    }


@dataclass
class BoxStatusSnapshot:
    uid: str
    status: str
    reason: str
    manual_override: str
    evaluated_at: datetime
    last_good_qc: Optional[datetime]
    latest_match_time: Optional[datetime]
    used_manual_override: bool
    used_parsed: bool
    parameter_results: List[ParameterResult] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "uid": self.uid,
            "status": self.status,
            "reason": self.reason,
            "manual_override": self.manual_override,
            "evaluated_at": self.evaluated_at.isoformat(timespec="seconds"),
            "last_good_qc": self.last_good_qc.isoformat(timespec="seconds") if self.last_good_qc else None,
            "latest_match_time": self.latest_match_time.isoformat(timespec="seconds") if self.latest_match_time else None,
            "used_manual_override": self.used_manual_override,
            "used_parsed": self.used_parsed,
            "parameter_results": [_serialize_parameter_result(pr) for pr in self.parameter_results],
        }


@dataclass
class LabSnapshot:
    config: AppConfig
    boxes: Dict[str, BoxStatusSnapshot]
    generated_at: datetime

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at.isoformat(timespec="seconds"),
            "config": self.config.serialize(),
            "boxes": {uid: snap.as_dict() for uid, snap in self.boxes.items()},
        }
