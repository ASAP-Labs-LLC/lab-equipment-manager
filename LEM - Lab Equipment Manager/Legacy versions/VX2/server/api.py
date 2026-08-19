from __future__ import annotations

from dataclasses import asdict
from typing import List

from fastapi import APIRouter, HTTPException

from .runtime import ServerContext
from .schemas import (
    BoxLayoutUpdateRequest,
    BoxStatusSchema,
    ClearOverrideRequest,
    ConfigUpdateRequest,
    LabSnapshotSchema,
    MaintenanceLogEntrySchema,
    MaintenanceTaskCommentRequest,
    MaintenanceTaskCompleteRequest,
    MaintenanceTaskCreateRequest,
    MaintenanceTaskSchema,
    MaintenanceTaskDeleteRequest,
    ManualOverrideRequest,
    ManualOverrideResponse,
    RefreshResponse,
    SimpleResponse,
)


def create_router(context: ServerContext) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["lab-manager"])

    @router.get("/snapshot", response_model=LabSnapshotSchema)
    async def get_snapshot() -> LabSnapshotSchema:
        snap = await context.state.snapshot()
        return LabSnapshotSchema.model_validate(snap.as_dict())

    @router.get("/boxes", response_model=List[BoxStatusSchema])
    async def list_boxes() -> List[BoxStatusSchema]:
        snap = await context.state.snapshot()
        data = snap.as_dict()["boxes"]
        return [BoxStatusSchema.model_validate(item) for item in data.values()]

    @router.post("/refresh", response_model=RefreshResponse)
    async def trigger_refresh() -> RefreshResponse:
        await context.status_service.refresh_all()
        return RefreshResponse(triggered=True)

    @router.patch("/config", response_model=LabSnapshotSchema)
    async def update_config(payload: ConfigUpdateRequest) -> LabSnapshotSchema:
        if payload.poll_minutes is not None:
            await context.config_service.update_poll_minutes(payload.poll_minutes)
        if payload.map_locked is not None:
            await context.config_service.update_map_lock(payload.map_locked)
        if payload.view_center is not None or payload.view_zoom is not None:
            await context.config_service.update_view_state(payload.view_center, payload.view_zoom)
        snap = await context.state.snapshot()
        return LabSnapshotSchema.model_validate(snap.as_dict())

    @router.patch("/boxes/{uid}/layout")
    async def update_box_layout(uid: str, payload: BoxLayoutUpdateRequest) -> dict:
        try:
            box = await context.config_service.update_box_layout(uid, payload.pos, payload.size, payload.locked)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "uid": box.uid,
            "pos": list(box.pos),
            "size": list(box.size),
            "locked": box.locked,
        }

    @router.post("/boxes/{uid}/override", response_model=ManualOverrideResponse)
    async def set_override(uid: str, payload: ManualOverrideRequest) -> ManualOverrideResponse:
        mode = (payload.mode or "").upper()
        if mode not in ("DEAD-LINE", "SERVICE"):
            raise HTTPException(status_code=400, detail="mode must be DEAD-LINE or SERVICE")
        try:
            box = await context.override_service.set_override(uid, mode, payload.user, payload.note or "")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return ManualOverrideResponse(uid=box.uid, manual_override=box.manual_override)

    @router.delete("/boxes/{uid}/override", response_model=ManualOverrideResponse)
    async def clear_override(uid: str, payload: ClearOverrideRequest | None = None) -> ManualOverrideResponse:
        req = payload or ClearOverrideRequest()
        try:
            box = await context.override_service.clear_override(uid, req.user, req.note or "")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return ManualOverrideResponse(uid=box.uid, manual_override=box.manual_override)

    @router.get("/maintenance/tasks", response_model=List[MaintenanceTaskSchema])
    async def maintenance_tasks() -> List[MaintenanceTaskSchema]:
        templates = await context.maintenance_service.list_templates()
        return [MaintenanceTaskSchema.model_validate(asdict(tpl)) for tpl in templates]

    @router.get("/maintenance/log", response_model=List[MaintenanceLogEntrySchema])
    async def maintenance_log() -> List[MaintenanceLogEntrySchema]:
        entries = await context.maintenance_service.list_logs()
        return [MaintenanceLogEntrySchema.model_validate(asdict(entry)) for entry in entries]

    @router.post("/maintenance/tasks", response_model=MaintenanceTaskSchema)
    async def create_task(payload: MaintenanceTaskCreateRequest) -> MaintenanceTaskSchema:
        tpl = await context.maintenance_service.create_task(
            payload.box_uid,
            payload.box_title,
            payload.name,
            payload.kind,
            payload.start_date,
            payload.repeat_value,
            payload.repeat_unit,
            payload.notes or "",
        )
        if tpl is None:
            raise HTTPException(status_code=400, detail="Unable to create task (possible duplicate calibration)")
        return MaintenanceTaskSchema.model_validate(asdict(tpl))

    @router.post("/maintenance/tasks/{task_id}/start", response_model=MaintenanceTaskSchema)
    async def start_task(task_id: str) -> MaintenanceTaskSchema:
        tpl = await context.maintenance_service.start_task(task_id)
        if tpl is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return MaintenanceTaskSchema.model_validate(asdict(tpl))

    @router.post("/maintenance/tasks/{task_id}/complete", response_model=MaintenanceTaskSchema)
    async def complete_task(task_id: str, payload: MaintenanceTaskCompleteRequest) -> MaintenanceTaskSchema:
        tpl = await context.maintenance_service.complete_task(task_id, payload.user, payload.comment)
        if tpl is None:
            raise HTTPException(status_code=400, detail="Cannot complete task")
        return MaintenanceTaskSchema.model_validate(asdict(tpl))

    @router.post("/maintenance/tasks/{task_id}/comment", response_model=SimpleResponse)
    async def comment_task(task_id: str, payload: MaintenanceTaskCommentRequest) -> SimpleResponse:
        await context.maintenance_service.add_comment(payload.box_uid, payload.box_title, payload.comment, payload.user)
        return SimpleResponse(ok=True)

    @router.delete("/maintenance/tasks/{task_id}", response_model=SimpleResponse)
    async def delete_task(task_id: str, payload: MaintenanceTaskDeleteRequest | None = None) -> SimpleResponse:
        req = payload or MaintenanceTaskDeleteRequest()
        removed = await context.maintenance_service.delete_task(task_id, req.user, req.comment)
        if not removed:
            raise HTTPException(status_code=404, detail="Task not found")
        return SimpleResponse(ok=True)

    return router
