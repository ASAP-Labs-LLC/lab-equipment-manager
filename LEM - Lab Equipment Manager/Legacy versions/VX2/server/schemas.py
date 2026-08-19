from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ParameterResultSchema(BaseModel):
    sample: str
    test: Optional[str] = None
    latest_value: Optional[float] = None
    in_spec: Optional[bool] = None
    low: Optional[float] = None
    high: Optional[float] = None
    note: str = ""
    units: Optional[str] = None
    latest_time: Optional[str] = None


class BoxStatusSchema(BaseModel):
    uid: str
    status: str
    reason: str
    manual_override: str
    evaluated_at: str
    last_good_qc: Optional[str] = None
    latest_match_time: Optional[str] = None
    used_manual_override: bool
    used_parsed: bool
    parameter_results: List[ParameterResultSchema] = Field(default_factory=list)


class LabSnapshotSchema(BaseModel):
    generated_at: str
    config: dict
    boxes: Dict[str, BoxStatusSchema]


class ManualOverrideRequest(BaseModel):
    mode: str
    user: str = ""
    note: Optional[str] = None


class ManualOverrideResponse(BaseModel):
    uid: str
    manual_override: str


class ClearOverrideRequest(BaseModel):
    user: str = ""
    note: Optional[str] = None


class RefreshResponse(BaseModel):
    triggered: bool


class ConfigUpdateRequest(BaseModel):
    poll_minutes: Optional[int] = None
    map_locked: Optional[bool] = None
    view_center: Optional[List[float]] = None
    view_zoom: Optional[float] = None


class BoxLayoutUpdateRequest(BaseModel):
    pos: Optional[List[float]] = None
    size: Optional[List[float]] = None
    locked: Optional[bool] = None


class MaintenanceTaskSchema(BaseModel):
    id: str
    box_uid: str
    box_title: str
    name: str
    kind: str
    start_date: str
    repeat_value: int
    repeat_unit: str
    next_due: str
    status: str
    notes: str = ""


class MaintenanceLogEntrySchema(BaseModel):
    timestamp: str
    box_uid: str
    box_title: str
    task_id: str
    task_name: str
    action: str
    user: str
    comment: str


class MaintenanceTaskCreateRequest(BaseModel):
    box_uid: str
    box_title: str
    name: str
    kind: str
    start_date: str
    repeat_value: int
    repeat_unit: str
    notes: Optional[str] = ""


class MaintenanceTaskStartRequest(BaseModel):
    pass


class MaintenanceTaskCompleteRequest(BaseModel):
    user: str = ""
    comment: str


class MaintenanceTaskCommentRequest(BaseModel):
    box_uid: str
    box_title: str
    comment: str
    user: str = ""


class MaintenanceTaskDeleteRequest(BaseModel):
    user: str = ""
    comment: str = ""


class SimpleResponse(BaseModel):
    ok: bool = True
