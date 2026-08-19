from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .services import StatusService
from .state import LabState


class RefreshScheduler:
    def __init__(self, state: LabState, status_service: StatusService) -> None:
        self._state = state
        self._status_service = status_service
        self._wake_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._runner(), name="refresh-scheduler")

    async def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._task is not None:
            await self._task
            self._task = None
            self._stop_event.clear()

    async def trigger_now(self) -> None:
        self._wake_event.set()

    async def _runner(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._status_service.refresh_all()
            except Exception:
                logging.exception("status refresh failed")
            cfg = await self._state.get_config_copy()
            poll_minutes = cfg.poll_minutes or 5
            wait_seconds = max(30, int(poll_minutes) * 60)
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=wait_seconds)
            except asyncio.TimeoutError:
                pass
            self._wake_event.clear()
        try:
            await self._status_service.refresh_all()
        except Exception:
            logging.exception("final status refresh failed")
