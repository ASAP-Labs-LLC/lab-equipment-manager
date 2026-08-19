#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
server.py - FastAPI-based EQM server (V4.3 unified server/viewer architecture).

This module exposes a REST + SSE API surface while delegating all stateful logic
to `server_state.State`. It keeps backward compatibility with the legacy
`/action/*` endpoints so existing PyQt viewers can connect during the migration
to the new server/viewer split.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import deque
from typing import Any, AsyncIterator, Deque, Dict, List, Optional

from asyncio import Queue, QueueEmpty, QueueFull

from fastapi import Body, Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from server_state import State


DEFAULT_POLL_SECONDS = int(os.environ.get("LABMAP_POLL_SECONDS", "60"))
API_TOKEN = os.environ.get("LABMAP_SERVER_TOKEN", "").strip() or None
ALLOW_CORS = os.environ.get("LABMAP_ALLOW_CORS", "*")


class EventBroker:
    """Simple SSE publisher with thread-safe emits."""

    def __init__(self) -> None:
        self._subscribers: set[Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._buffer: Deque[dict] = deque(maxlen=256)
        self._lock = asyncio.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        while self._buffer:
            event = self._buffer.popleft()
            asyncio.run_coroutine_threadsafe(self._publish(event), loop)

    def emit(self, event: dict) -> None:
        loop = self._loop
        if loop is None:
            self._buffer.append(event)
            return
        asyncio.run_coroutine_threadsafe(self._publish(event), loop)

    async def subscribe(self) -> Queue:
        queue: Queue = asyncio.Queue(maxsize=512)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: Queue) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def _publish(self, event: dict) -> None:
        async with self._lock:
            subscribers = list(self._subscribers)
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except QueueFull:
                # Drop the oldest event to make space, then enqueue.
                try:
                    queue.get_nowait()
                except QueueEmpty:
                    pass
                queue.put_nowait(event)


security = HTTPBearer(auto_error=False)
app = FastAPI(title="EQM Lab Manager Server", version="4.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOW_CORS] if ALLOW_CORS != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

EVENTS = EventBroker()
STATE: Optional[State] = None


def _event_sink(event: dict) -> None:
    EVENTS.emit(event)


def get_state() -> State:
    if STATE is None:
        raise HTTPException(status_code=503, detail="Server state not ready")
    return STATE


async def require_auth(credentials: HTTPAuthorizationCredentials = Depends(security)) -> None:
    if API_TOKEN is None:
        return
    token = credentials.credentials if credentials else ""
    if token != API_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API token")


@app.on_event("startup")
async def _startup() -> None:
    global STATE
    loop = asyncio.get_running_loop()
    EVENTS.bind_loop(loop)
    if STATE is None:
        STATE = State(poll_seconds=DEFAULT_POLL_SECONDS, event_sink=_event_sink)
    STATE.start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    if STATE:
        STATE.stop()


@app.get("/healthz", response_class=PlainTextResponse)
async def health_check() -> str:
    return "OK"


@app.get("/state")
async def read_state(_: None = Depends(require_auth)) -> Dict[str, Any]:
    state = get_state()
    return await asyncio.to_thread(state.serialize_state)


@app.get("/config")
async def read_config(_: None = Depends(require_auth)) -> Dict[str, Any]:
    state = get_state()
    with state.lock:
        return state.cfg.serialize()


@app.patch("/settings")
async def update_settings(payload: Dict[str, Any], _: None = Depends(require_auth)) -> Dict[str, Any]:
    state = get_state()
    await asyncio.to_thread(state.update_settings, payload)
    return {"ok": True}


@app.get("/reports/preview")
async def report_preview(_: None = Depends(require_auth)) -> Dict[str, Any]:
    state = get_state()
    return await asyncio.to_thread(state.build_report_preview)


@app.post("/reports/generate")
async def report_generate(payload: Dict[str, Any], _: None = Depends(require_auth)) -> Dict[str, Any]:
    state = get_state()
    force = bool(payload.get("force", True))
    formats_raw = payload.get("formats")
    fmt_list: Optional[List[str]] = None
    if isinstance(formats_raw, list):
        fmt_list = [str(x).lower() for x in formats_raw if str(x).strip()]
        if not fmt_list:
            fmt_list = None
    try:
        outputs = await asyncio.to_thread(state.generate_report, force, fmt_list)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "outputs": outputs}


@app.get("/maintenance")
async def maintenance_snapshot(_: None = Depends(require_auth)) -> Dict[str, Any]:
    state = get_state()
    return await asyncio.to_thread(state.get_maintenance_snapshot)


@app.post("/maintenance/tasks")
async def maintenance_create(payload: Dict[str, Any], _: None = Depends(require_auth)) -> Dict[str, Any]:
    state = get_state()
    task = await asyncio.to_thread(state.add_maintenance_task, payload)
    if not task:
        raise HTTPException(status_code=400, detail="Unable to create maintenance task")
    return {"ok": True, "task": task}


@app.post("/maintenance/tasks/{task_id}/start")
async def maintenance_start(task_id: str, _: None = Depends(require_auth)) -> Dict[str, Any]:
    state = get_state()
    task = await asyncio.to_thread(state.start_maintenance_task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True, "task": task}


@app.post("/maintenance/tasks/{task_id}/complete")
async def maintenance_complete(
    task_id: str,
    payload: Dict[str, Any],
    _: None = Depends(require_auth),
) -> Dict[str, Any]:
    user = str(payload.get("user", ""))
    comment = str(payload.get("comment", ""))
    state = get_state()
    task = await asyncio.to_thread(state.complete_maintenance_task, task_id, user, comment)
    if not task:
        raise HTTPException(status_code=400, detail="Unable to complete task (check status/comment)")
    return {"ok": True, "task": task}


@app.delete("/maintenance/tasks/{task_id}")
async def maintenance_delete(
    task_id: str,
    payload: Dict[str, Any] = Body(default={}),
    _: None = Depends(require_auth),
) -> Dict[str, Any]:
    state = get_state()
    ok = await asyncio.to_thread(
        state.delete_maintenance_task,
        task_id,
        str(payload.get("user", "")),
        str(payload.get("reason", "")),
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True}


@app.post("/maintenance/comments")
async def maintenance_comment(payload: Dict[str, Any], _: None = Depends(require_auth)) -> Dict[str, Any]:
    state = get_state()
    ok = await asyncio.to_thread(
        state.add_maintenance_comment,
        str(payload.get("box_uid", "")),
        str(payload.get("box_title", "")),
        str(payload.get("comment", "")),
        str(payload.get("user", "")),
    )
    if not ok:
        raise HTTPException(status_code=400, detail="Comment is required")
    return {"ok": True}


@app.get("/boxes")
async def list_boxes(_: None = Depends(require_auth)) -> Dict[str, Any]:
    state = get_state()
    data = await asyncio.to_thread(state.serialize_state)
    return {"boxes": data.get("boxes", [])}


@app.post("/boxes")
async def create_box(payload: Dict[str, Any], _: None = Depends(require_auth)) -> Dict[str, Any]:
    box_data = payload.get("box") if "box" in payload else payload
    state = get_state()
    ok = await asyncio.to_thread(state.add_or_edit_box, box_data or {})
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid box payload")
    return {"ok": True}


@app.patch("/boxes/{box_uid}")
async def update_box(box_uid: str, payload: Dict[str, Any], _: None = Depends(require_auth)) -> Dict[str, Any]:
    box_data = payload.get("box") if "box" in payload else payload
    box_data["uid"] = box_uid
    state = get_state()
    ok = await asyncio.to_thread(state.add_or_edit_box, box_data)
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid box payload")
    return {"ok": True}


@app.delete("/boxes/{box_uid}")
async def delete_box(box_uid: str, _: None = Depends(require_auth)) -> Dict[str, Any]:
    state = get_state()
    removed = await asyncio.to_thread(state.remove_box, box_uid)
    if not removed:
        raise HTTPException(status_code=404, detail="Box not found")
    return {"ok": True}


@app.post("/boxes/{box_uid}/override")
async def set_override(
    box_uid: str,
    payload: Dict[str, Any] = Body(...),
    _: None = Depends(require_auth),
) -> Dict[str, Any]:
    mode = str(payload.get("mode", "")).upper()
    user = str(payload.get("user", ""))
    note = str(payload.get("note", ""))
    state = get_state()
    ok = await asyncio.to_thread(state.manual_override, box_uid, mode, user, note)
    if not ok:
        raise HTTPException(status_code=400, detail="Failed to apply override")
    return {"ok": True}


@app.post("/action/update_box_pos_size")
async def legacy_update_box(payload: Dict[str, Any]) -> Dict[str, Any]:
    state = get_state()
    found, changed = await asyncio.to_thread(
        state.update_box_pos_size,
        str(payload.get("uid", "")),
        payload.get("pos"),
        payload.get("size"),
        payload.get("locked"),
    )
    return {"ok": bool(found), "changed": bool(changed)}


@app.post("/action/manual_override")
async def legacy_override(payload: Dict[str, Any]) -> Dict[str, Any]:
    ok = await set_override(
        str(payload.get("uid", "")),
        payload,
    )
    return {"ok": ok.get("ok", False)}


@app.post("/action/add_box")
@app.post("/action/edit_box")
async def legacy_add_box(payload: Dict[str, Any]) -> Dict[str, Any]:
    return await create_box(payload)


@app.post("/action/remove_box")
async def legacy_remove_box(payload: Dict[str, Any]) -> Dict[str, Any]:
    uid = str(payload.get("uid", ""))
    return await delete_box(uid)


@app.post("/action/settings")
async def legacy_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    return await update_settings(payload)


@app.post("/action/pm_delete")
async def legacy_pm_delete(payload: Dict[str, Any]) -> Dict[str, Any]:
    state = get_state()
    ok = await asyncio.to_thread(
        state.delete_maintenance_task,
        str(payload.get("task_id", "")),
        str(payload.get("user", "")),
        str(payload.get("reason", "")),
    )
    maintenance = await asyncio.to_thread(state.serialize_state)
    return {"ok": ok, "maintenance": maintenance.get("maintenance", {})}


@app.get("/events/sse")
async def event_stream(request: Request) -> StreamingResponse:
    queue = await EVENTS.subscribe()

    async def gen() -> AsyncIterator[bytes]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                event = await queue.get()
                payload = json.dumps(event).encode("utf-8")
                yield b"data: " + payload + b"\n\n"
        finally:
            await EVENTS.unsubscribe(queue)

    return StreamingResponse(gen(), media_type="text/event-stream")


def _cli() -> None:
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="EQM FastAPI server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run(
        "server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    _cli()
