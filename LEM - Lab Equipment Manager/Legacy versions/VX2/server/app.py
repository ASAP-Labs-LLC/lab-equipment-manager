from __future__ import annotations

from fastapi import FastAPI

from .api import create_router
from .runtime import ServerContext

context = ServerContext()

app = FastAPI(title="Lab Manager Server", version="1.0.0")
app.include_router(create_router(context))


@app.on_event("startup")
async def _startup() -> None:
    await context.startup()


@app.on_event("shutdown")
async def _shutdown() -> None:
    await context.shutdown()


@app.get("/")
async def root() -> dict:
    snap = await context.state.snapshot()
    return {"status": "ok", "generated_at": snap.generated_at.isoformat(timespec="seconds")}
