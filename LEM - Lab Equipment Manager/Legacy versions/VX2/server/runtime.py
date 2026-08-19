from __future__ import annotations

from pathlib import Path
from typing import Optional

from config_store import load_config

from .services import ConfigService, MaintenanceService, OverrideService, ReportService, StatusService
from .state import LabState
from .scheduler import RefreshScheduler


class ServerContext:
    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self.base_dir = base_dir or Path(__file__).resolve().parent.parent
        initial_config = load_config()
        self.state = LabState(initial_config)
        self.config_service = ConfigService(self.state)
        maintenance_dir = self.base_dir / "Maintenance"
        self.maintenance_service = MaintenanceService(maintenance_dir)
        self.report_service = ReportService(self.config_service)
        self.status_service = StatusService(self.state, self.config_service, self.maintenance_service, self.report_service)
        self.override_service = OverrideService(self.state, self.config_service, self.maintenance_service, self.status_service)
        self.scheduler = RefreshScheduler(self.state, self.status_service)

    async def startup(self) -> None:
        await self.config_service.load()
        await self.status_service.refresh_all()
        await self.scheduler.start()

    async def shutdown(self) -> None:
        await self.scheduler.stop()
        await self.status_service.shutdown()
        await self.report_service.shutdown()
        await self.maintenance_service.shutdown()
