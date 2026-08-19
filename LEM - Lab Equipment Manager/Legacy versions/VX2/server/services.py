from __future__ import annotations

import asyncio
import copy
import csv
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from models import AppConfig, BoxConfig, SampleSpec, STATUS_DEAD, STATUS_GREEN, STATUS_SERVICE, STATUS_YELLOW

from config_store import load_config, save_config
from data_source import evaluate_box
from maintenance import MaintenanceManager, MaintenanceLogEntry, MaintenanceTemplate

from .models import BoxStatusSnapshot
from .state import LabState


logger = logging.getLogger(__name__)


class ConfigService:
    def __init__(self, state: LabState) -> None:
        self._state = state

    async def load(self) -> AppConfig:
        loop = asyncio.get_running_loop()
        cfg = await loop.run_in_executor(None, load_config)
        await self._state.set_config(cfg)
        return cfg

    async def save(self, cfg: AppConfig) -> None:
        loop = asyncio.get_running_loop()
        ok, msg = await loop.run_in_executor(None, lambda: save_config(cfg))
        if not ok:
            raise RuntimeError(f"Failed to save config: {msg}")

    async def update_poll_minutes(self, minutes: int) -> AppConfig:
        cfg = await self._state.get_config_copy()
        cfg.poll_minutes = max(1, int(minutes or 1))
        await self._state.set_config(cfg)
        await self.save(cfg)
        return cfg

    async def update_map_lock(self, locked: bool) -> AppConfig:
        cfg = await self._state.get_config_copy()
        cfg.map_locked = bool(locked)
        await self._state.set_config(cfg)
        await self.save(cfg)
        return cfg

    async def update_view_state(self, center: Optional[List[float]], zoom: Optional[float]) -> AppConfig:
        cfg = await self._state.get_config_copy()
        if center and len(center) == 2:
            cfg.view_center = (float(center[0]), float(center[1]))
        if zoom is not None:
            cfg.view_zoom = float(zoom)
        await self._state.set_config(cfg)
        await self.save(cfg)
        return cfg

    async def update_box_layout(self, uid: str, pos: Optional[List[float]], size: Optional[List[float]], locked: Optional[bool]) -> BoxConfig:
        cfg = await self._state.get_config_copy()
        target = next((b for b in cfg.boxes if b.uid == uid), None)
        if target is None:
            raise KeyError(f"Box {uid} not found")
        if pos and len(pos) == 2:
            target.pos = (float(pos[0]), float(pos[1]))
        if size and len(size) == 2:
            target.size = (float(size[0]), float(size[1]))
        if locked is not None:
            target.locked = bool(locked)
        await self._state.set_config(cfg)
        await self.save(cfg)
        return target


class MaintenanceService:
    def __init__(self, base_dir: Path) -> None:
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._manager = MaintenanceManager(str(base_dir))
        self._lock = asyncio.Lock()

    @property
    def manager(self) -> MaintenanceManager:
        return self._manager

    async def sync_dirs(self, cfg: AppConfig) -> None:
        mapping: Dict[str, str] = {}
        for box in cfg.boxes:
            if box.csv_path:
                mapping[box.uid] = os.path.dirname(box.csv_path)
        loop = asyncio.get_running_loop()
        async with self._lock:
            await loop.run_in_executor(self._executor, lambda: self._manager.set_box_dirs(mapping))

    async def log_manual_override(self, box_uid: str, box_title: str, user: str, comment: str) -> None:
        loop = asyncio.get_running_loop()
        async with self._lock:
            await loop.run_in_executor(self._executor, lambda: self._manager.log_manual_override(box_uid, box_title, user, comment))

    async def list_templates(self) -> List[MaintenanceTemplate]:
        async with self._lock:
            return [copy.deepcopy(tpl) for tpl in self._manager.templates.values()]

    async def list_logs(self) -> List[MaintenanceLogEntry]:
        async with self._lock:
            return [copy.deepcopy(entry) for entry in self._manager.log]

    async def create_task(self, box_uid: str, box_title: str, name: str, kind: str, start_date: str, repeat_value: int, repeat_unit: str, notes: str = "") -> Optional[MaintenanceTemplate]:
        loop = asyncio.get_running_loop()
        def _create() -> Optional[MaintenanceTemplate]:
            try:
                start_dt = datetime.fromisoformat(start_date)
            except Exception:
                start_dt = datetime.now()
            tpl = self._manager.create_task(box_uid, box_title, name, kind, start_dt, repeat_value, repeat_unit)
            if tpl and notes:
                tpl.notes = notes
                self._manager._save_templates()
            return tpl
        async with self._lock:
            return await loop.run_in_executor(self._executor, _create)

    async def start_task(self, task_id: str) -> Optional[MaintenanceTemplate]:
        loop = asyncio.get_running_loop()
        async with self._lock:
            return await loop.run_in_executor(self._executor, lambda: self._manager.start_task(task_id))

    async def complete_task(self, task_id: str, user: str, comment: str) -> Optional[MaintenanceTemplate]:
        loop = asyncio.get_running_loop()
        async with self._lock:
            return await loop.run_in_executor(self._executor, lambda: self._manager.complete_task(task_id, user, comment))

    async def add_comment(self, box_uid: str, box_title: str, comment: str, user: str = "") -> None:
        loop = asyncio.get_running_loop()
        async with self._lock:
            await loop.run_in_executor(self._executor, lambda: self._manager.add_comment(box_uid, box_title, comment, user))

    async def delete_task(self, task_id: str, user: str, comment: str) -> bool:
        loop = asyncio.get_running_loop()
        async with self._lock:
            tpl = self._manager.templates.get(task_id)
            if not tpl:
                return False
            def _delete() -> bool:
                self._manager.log_delete(tpl.box_uid, tpl.box_title, tpl.id, tpl.name, user, comment)
                self._manager.remove_task(task_id)
                return True
            return await loop.run_in_executor(self._executor, _delete)

    async def shutdown(self) -> None:
        self._executor.shutdown(wait=False)


class ReportService:
    def __init__(self, config_service: ConfigService) -> None:
        self._config_service = config_service
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._startup_catchup = True

    async def maybe_run_daily_report(
        self,
        cfg: AppConfig,
        samples_by_name: Dict[str, SampleSpec],
        rows_by_path: Dict[str, List[dict]],
    ) -> bool:
        if not cfg.report_enabled or not cfg.report_dir:
            self._startup_catchup = False
            return False

        changed = False
        if self._startup_catchup:
            self._startup_catchup = False
            changed |= await self._export_if_needed(cfg, samples_by_name, rows_by_path, force_if_missed=True)

        changed |= await self._export_if_needed(cfg, samples_by_name, rows_by_path, force_if_missed=False)
        return changed

    async def _export_if_needed(
        self,
        cfg: AppConfig,
        samples_by_name: Dict[str, SampleSpec],
        rows_by_path: Dict[str, List[dict]],
        *,
        force_if_missed: bool,
    ) -> bool:
        try:
            hh, mm = [int(x) for x in cfg.report_time.split(":")[:2]]
        except Exception:
            hh, mm = 17, 0
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")

        if force_if_missed:
            if cfg.last_report_date != today_str:
                return await self._export(cfg, samples_by_name, rows_by_path, today_str)
            return False

        if cfg.last_report_date == today_str:
            return False
        if (now.hour, now.minute) < (hh, mm):
            return False
        return await self._export(cfg, samples_by_name, rows_by_path, today_str)

    async def _export(
        self,
        cfg: AppConfig,
        samples_by_name: Dict[str, SampleSpec],
        rows_by_path: Dict[str, List[dict]],
        today_str: str,
    ) -> bool:
        headers, rows = self._build_report(cfg, samples_by_name, rows_by_path)
        out_dir = Path(cfg.report_dir)
        out_path = out_dir / f"LabManagerReport_{today_str}.csv"
        loop = asyncio.get_running_loop()

        def _write() -> None:
            out_dir.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(headers)
                writer.writerows(rows)

        try:
            await loop.run_in_executor(self._executor, _write)
        except Exception:
            logger.exception("failed to export daily report")
            return False

        cfg.last_report_date = today_str
        logger.info("Daily report exported to %s", out_path)
        return True

    def _build_report(
        self,
        cfg: AppConfig,
        samples_by_name: Dict[str, SampleSpec],
        rows_by_path: Dict[str, List[dict]],
    ) -> Tuple[List[str], List[List[str]]]:
        headers = [
            "Box Title", "Box UID", "Box Status", "Override",
            "CSV Path", "QC Expiry (h)", "Last In-Spec QC / Fallback", "Latest Match Time",
            "Reason", "Used Parsed Time",
            "Sample", "Test Name", "Expected", "k*StdDev", "Low", "High", "Latest Value", "In Spec", "Units"
        ]
        out_rows: List[List[str]] = []
        for box in cfg.boxes:
            rows = rows_by_path.get(box.csv_path, [])
            eval_res = evaluate_box(box, samples_by_name, cfg.sample_id_column, rows)
            status = eval_res.status
            if box.manual_override == STATUS_DEAD:
                status = STATUS_DEAD
            elif box.manual_override == STATUS_SERVICE:
                status = STATUS_SERVICE

            effective = eval_res.last_good_qc
            if not eval_res.used_parsed:
                iso = cfg.first_inspec_map.get(box.uid)
                if iso:
                    try:
                        effective = datetime.fromisoformat(iso)
                    except Exception:
                        pass

            last_qc = effective.isoformat(sep=' ') if effective else ""
            last_mt = eval_res.latest_match_time.isoformat(sep=' ') if eval_res.latest_match_time else ""
            reason = eval_res.reason or ""
            used_parsed_str = "YES" if eval_res.used_parsed else "NO"

            if eval_res.results:
                for pr in eval_res.results:
                    sample_name = pr.sample
                    if pr.test:
                        units = pr.test.units or ""
                        tol = pr.test.k * pr.test.std_dev
                        low = f"{pr.low:.6g}" if pr.low is not None else ""
                        high = f"{pr.high:.6g}" if pr.high is not None else ""
                        expected = f"{pr.test.expected:.6g}"
                        latest = "" if pr.latest_value is None else f"{pr.latest_value:.6g}"
                        insp = "" if pr.in_spec is None else ("YES" if pr.in_spec else "NO")
                        out_rows.append([
                            box.title, box.uid, status, (box.manual_override or ""),
                            box.csv_path, f"{box.qc_expire_hours:.2f}", last_qc, last_mt,
                            reason, used_parsed_str,
                            sample_name, pr.test.name, expected, f"{tol:.6g}",
                            low, high, latest, insp, units,
                        ])
                    else:
                        out_rows.append([
                            box.title, box.uid, status, (box.manual_override or ""),
                            box.csv_path, f"{box.qc_expire_hours:.2f}", last_qc, last_mt,
                            reason, used_parsed_str,
                            sample_name, "", "", "", "", "", "", "", "",
                        ])
            else:
                out_rows.append([
                    box.title, box.uid, status, (box.manual_override or ""),
                    box.csv_path, f"{box.qc_expire_hours:.2f}", last_qc, last_mt,
                    reason, used_parsed_str,
                    "", "", "", "", "", "", "", "", "",
                ])
        return headers, out_rows

    async def shutdown(self) -> None:
        self._executor.shutdown(wait=False)


class StatusService:
    def __init__(
        self,
        state: LabState,
        config_service: ConfigService,
        maintenance: MaintenanceService,
        report_service: Optional[ReportService] = None,
    ) -> None:
        self._state = state
        self._config_service = config_service
        self._maintenance = maintenance
        self._report_service = report_service
        self._executor = ThreadPoolExecutor(max_workers=8)
        self._csv_cache: Dict[str, Tuple[float, List[dict]]] = {}

    async def refresh_all(self) -> None:
        cfg = await self._state.get_config_copy()
        await self._maintenance.sync_dirs(cfg)

        samples_by_name = {s.name: s for s in cfg.samples}
        paths = sorted({box.csv_path for box in cfg.boxes if box.csv_path})
        loop = asyncio.get_running_loop()
        rows_by_path = await loop.run_in_executor(self._executor, lambda: self._load_rows_sync(paths))

        previous_statuses = await self._state.get_statuses()
        config_dirty = self._ensure_first_inspec_epoch(cfg)
        status_map: Dict[str, BoxStatusSnapshot] = {}
        eval_timestamp = datetime.utcnow()

        for box in cfg.boxes:
            rows = rows_by_path.get(box.csv_path, [])
            eval_res = evaluate_box(box, samples_by_name, cfg.sample_id_column, rows)

            effective_last_good = eval_res.last_good_qc
            if not eval_res.used_parsed:
                now_local = datetime.now()
                if eval_res.status in (STATUS_GREEN, STATUS_YELLOW):
                    if self._set_first_inspec_if_missing(cfg, box.uid, now_local):
                        config_dirty = True
                    effective_last_good = self._get_first_inspec(cfg, box.uid) or eval_res.last_good_qc
                else:
                    effective_last_good = self._get_first_inspec(cfg, box.uid) or eval_res.last_good_qc

            status = eval_res.status
            reason = eval_res.reason
            used_manual_override = False
            if box.manual_override == STATUS_DEAD:
                status = STATUS_DEAD
                reason = "Manual override: DEAD-LINE"
                used_manual_override = True
            elif box.manual_override == STATUS_SERVICE:
                status = STATUS_SERVICE
                reason = "Manual override: SERVICE"
                used_manual_override = True

            if status == STATUS_GREEN and effective_last_good:
                if (datetime.utcnow() - effective_last_good) > timedelta(hours=box.qc_expire_hours):
                    status = STATUS_YELLOW
                    reason = "Last in-spec QC is stale."

            snapshot = BoxStatusSnapshot(
                uid=box.uid,
                status=status,
                reason=reason,
                manual_override=box.manual_override,
                evaluated_at=eval_timestamp,
                last_good_qc=effective_last_good,
                latest_match_time=eval_res.latest_match_time,
                used_manual_override=used_manual_override,
                used_parsed=eval_res.used_parsed,
                parameter_results=eval_res.results,
            )
            status_map[box.uid] = snapshot

            prev = previous_statuses.get(box.uid)
            if prev and prev.status != status:
                await self._log_status_change(cfg, box, prev.status, status, reason)

        if self._report_service:
            report_dirty = await self._report_service.maybe_run_daily_report(cfg, samples_by_name, rows_by_path)
            if report_dirty:
                config_dirty = True

        await self._state.set_statuses(cfg, status_map)
        if config_dirty:
            await self._config_service.save(cfg)
    def _load_rows_sync(self, paths: List[str]) -> Dict[str, List[dict]]:
        rows_by_path: Dict[str, List[dict]] = {}
        for path in paths:
            try:
                if not path:
                    rows_by_path[path] = []
                    continue
                mtime = os.path.getmtime(path)
                cached = self._csv_cache.get(path)
                if cached and cached[0] == mtime:
                    rows_by_path[path] = cached[1]
                    continue
                rows: List[dict] = []
                if os.path.exists(path):
                    with open(path, "r", newline="", encoding="utf-8-sig") as f:
                        reader = csv.DictReader(f)
                        rows = list(reader)
                self._csv_cache[path] = (mtime, rows)
                rows_by_path[path] = rows
            except FileNotFoundError:
                self._csv_cache.pop(path, None)
                rows_by_path[path] = []
            except Exception:
                rows_by_path[path] = []
        return rows_by_path

    def _ensure_first_inspec_epoch(self, cfg: AppConfig) -> bool:
        today = datetime.now().strftime("%Y-%m-%d")
        if cfg.first_inspec_date != today:
            cfg.first_inspec_date = today
            cfg.first_inspec_map = {}
            return True
        return False

    def _get_first_inspec(self, cfg: AppConfig, uid: str) -> Optional[datetime]:
        iso = cfg.first_inspec_map.get(uid)
        if not iso:
            return None
        try:
            return datetime.fromisoformat(iso)
        except Exception:
            return None

    def _set_first_inspec_if_missing(self, cfg: AppConfig, uid: str, when: datetime) -> bool:
        if uid not in cfg.first_inspec_map:
            cfg.first_inspec_map[uid] = when.replace(microsecond=0).isoformat(sep=' ')
            return True
        return False

    async def _log_status_change(self, cfg: AppConfig, box: BoxConfig, prev_status: str, new_status: str, reason: str) -> None:
        out_dir = (cfg.status_log_dir or '').strip()
        if not out_dir:
            return
        path = os.path.join(out_dir, "status_changes.csv")
        os.makedirs(out_dir, exist_ok=True)

        def _write() -> None:
            file_exists = os.path.exists(path)
            with open(path, "a", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                if not file_exists:
                    writer.writerow(["timestamp", "box_uid", "box_title", "prev_status", "new_status", "reason"])
                writer.writerow([
                    datetime.now().isoformat(timespec='seconds'),
                    box.uid,
                    box.title,
                    prev_status,
                    new_status,
                    reason or "",
                ])

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, _write)

    async def shutdown(self) -> None:
        self._executor.shutdown(wait=False)


class OverrideService:
    def __init__(
        self,
        state: LabState,
        config_service: ConfigService,
        maintenance: MaintenanceService,
        status_service: StatusService,
    ) -> None:
        self._state = state
        self._config_service = config_service
        self._maintenance = maintenance
        self._status_service = status_service

    async def set_override(self, uid: str, mode: str, user: str, note: str) -> BoxConfig:
        if mode not in (STATUS_DEAD, STATUS_SERVICE):
            raise ValueError("Unsupported override mode")
        cfg = await self._state.get_config_copy()
        target = next((b for b in cfg.boxes if b.uid == uid), None)
        if target is None:
            raise KeyError(f"Box {uid} not found")
        if target.manual_override == mode:
            await self._state.set_config(cfg)
            return target
        target.manual_override = mode
        await self._state.set_config(cfg)
        await self._config_service.save(cfg)
        await self._log_manual_override(cfg, target, f"{mode}: ON", user, note)
        await self._maintenance.log_manual_override(target.uid, target.title, user, note)
        await self._status_service.refresh_all()
        return target

    async def clear_override(self, uid: str, user: str, note: str) -> BoxConfig:
        cfg = await self._state.get_config_copy()
        target = next((b for b in cfg.boxes if b.uid == uid), None)
        if target is None:
            raise KeyError(f"Box {uid} not found")
        mode = target.manual_override
        if not mode:
            await self._state.set_config(cfg)
            return target
        target.manual_override = ""
        await self._state.set_config(cfg)
        await self._config_service.save(cfg)
        if mode in (STATUS_DEAD, STATUS_SERVICE):
            await self._log_manual_override(cfg, target, f"{mode}: OFF", user, note)
        await self._maintenance.log_manual_override(target.uid, target.title, user, note)
        await self._status_service.refresh_all()
        return target

    async def _log_manual_override(self, cfg: AppConfig, box: BoxConfig, action: str, user: str, note: str) -> None:
        out_dir = (cfg.status_log_dir or '').strip()
        if not out_dir:
            return
        path = os.path.join(out_dir, "manual_overrides.csv")
        os.makedirs(out_dir, exist_ok=True)

        def _write() -> None:
            file_exists = os.path.exists(path)
            with open(path, "a", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                if not file_exists:
                    writer.writerow(["timestamp", "box_uid", "box_title", "action", "user", "note"])
                writer.writerow([
                    datetime.now().isoformat(timespec='seconds'),
                    box.uid,
                    box.title,
                    action,
                    user,
                    note or "",
                ])

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._status_service._executor, _write)
