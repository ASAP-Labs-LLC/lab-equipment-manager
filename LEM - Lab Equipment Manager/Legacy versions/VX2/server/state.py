from __future__ import annotations

import asyncio
import copy
from datetime import datetime
from typing import Dict, Optional

from models import AppConfig

from .models import BoxStatusSnapshot, LabSnapshot


class LabState:
    """Concurrency-safe holder for configuration and runtime status snapshots."""

    def __init__(self, config: AppConfig) -> None:
        self._lock = asyncio.Lock()
        self._config = config
        self._box_statuses: Dict[str, BoxStatusSnapshot] = {}
        self._last_refresh: Optional[datetime] = None

    async def get_config_copy(self) -> AppConfig:
        async with self._lock:
            return copy.deepcopy(self._config)

    async def get_config(self) -> AppConfig:
        async with self._lock:
            return self._config

    async def set_config(self, config: AppConfig) -> None:
        async with self._lock:
            self._config = config

    async def set_statuses(self, config: AppConfig, statuses: Dict[str, BoxStatusSnapshot]) -> Dict[str, BoxStatusSnapshot]:
        async with self._lock:
            previous = self._box_statuses
            self._box_statuses = statuses
            self._config = config
            self._last_refresh = datetime.utcnow()
            return previous

    async def snapshot(self) -> LabSnapshot:
        async with self._lock:
            config_copy = copy.deepcopy(self._config)
            statuses_copy = {uid: copy.deepcopy(snap) for uid, snap in self._box_statuses.items()}
            generated_at = self._last_refresh or datetime.utcnow()
        return LabSnapshot(config=config_copy, boxes=statuses_copy, generated_at=generated_at)

    async def get_statuses(self) -> Dict[str, BoxStatusSnapshot]:
        async with self._lock:
            return copy.deepcopy(self._box_statuses)

    async def last_refresh(self) -> Optional[datetime]:
        async with self._lock:
            return self._last_refresh
