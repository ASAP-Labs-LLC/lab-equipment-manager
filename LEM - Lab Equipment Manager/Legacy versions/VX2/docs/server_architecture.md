# Server-Centric Architecture

## Objectives
- Centralize all business rules (status evaluation, manual overrides, maintenance, reporting) on a long-running server process.
- Deliver a stateless HTTP API that front-end clients can call for state reads and mutations.
- Support efficient, scalable polling of CSV data and scheduled report exports without burdening the UI.
- Maintain backwards compatibility with existing configuration and CSV assets.

## System Overview
`
+-----------------------------+
|  Front-End (PyQt UI)        |
|  - renders layout           |
|  - sends user intents       |
|  - polls for updates        |
+-----------------------------+
               |
               v
+-----------------------------+
|  HTTP API (FastAPI)         |
|  - Auth TBD (out of scope)  |
|  - JSON requests/responses  |
+-----------------------------+
               |
               v
+-----------------------------+
|  Application Services       |
|  - StatusService            |
|  - OverrideService          |
|  - MaintenanceService       |
|  - ReportService            |
|  - ConfigService            |
+-----------------------------+
               |
               v
+-----------------------------+
|  State Store & Persistence  |
|  - In-memory LabState       |
|  - File-backed config CSV   |
|  - Async refresh scheduler  |
+-----------------------------+
`

## Core Components
- **ConfigService** loads and saves AppConfig using existing JSON structure and guards version migrations. Updates are funneled through an syncio.Lock to avoid write races.
- **StatusService** periodically fetches CSV data, runs evaluate_box, applies manual overrides, and caches BoxStatus snapshots. Uses a thread pool executor for IO-bound CSV reads so the main loop stays responsive under load.
- **OverrideService** updates overrides in config, persists the change, logs to the manual overrides CSV, and triggers an immediate status recompute.
- **MaintenanceService** wraps the existing MaintenanceManager but exposes async-safe entrypoints and serializes results for the API.
- **ReportService** builds the same CSV report as the legacy UI, running on the server via background job aligned with configured time.
- **LabState** (in server/state.py) tracks the current config, box statuses, maintenance tasks, and status updates. It exposes snapshot methods that copy data for API responses without leaking references.

## API Surface (JSON over HTTP)
- GET /api/v1/bootstrap ? layout metadata, status snapshot, maintenance summary.
- POST /api/v1/refresh ? trigger immediate CSV refresh.
- GET /api/v1/boxes ? list boxes with runtime status data.
- PATCH /api/v1/boxes/{uid}/layout ? update position/size/lock flags.
- POST /api/v1/boxes/{uid}/override ? set manual override (mode, user, 
ote).
- DELETE /api/v1/boxes/{uid}/override ? clear manual override.
- GET /api/v1/maintenance/tasks ? maintenance templates.
- POST /api/v1/maintenance/tasks ? create task.
- POST /api/v1/maintenance/{task_id}/start|complete|comment|delete ? task workflows.
- GET /api/v1/status/history ? read status log entries (paged, optional time filter).

## Scheduling & Concurrency
- A single background task (RefreshScheduler) wakes every poll_minutes (configurable at runtime) and calls StatusService.refresh_all. It uses syncio.Event to allow manual triggers and fast reconfiguration when poll interval changes.
- Report exports attach to the same scheduler; after every refresh the ReportService checks whether the configured time window is hit or missed and performs catch-up exports.
- Thread-safe operations use syncio.Lock at the state-service boundary. CSV IO, report generation, and maintenance disk writes are executed in a ThreadPoolExecutor to avoid blocking the event loop.

## Front-End Integration
- The PyQt UI is refactored to rely on client/server_proxy.py, which wraps httpx.AsyncClient (or equests for sync) to call the REST API.
- UI polling uses a timer that hits GET /api/v1/boxes and GET /api/v1/maintenance/tasks on an interval; data binding updates widgets without recomputing status locally.
- Actions like overrides or layout moves call the corresponding POST/PATCH endpoints and optimistically update the view after the server acknowledges success.

## Scalability Considerations
- CSV evaluation caches parsed rows to avoid redundant IO within a polling cycle and uses per-file mtime checks to skip reprocessing unchanged files.
- Manual overrides and status changes append to CSV logs using buffered writes to minimize disk churn.
- API responses avoid large payloads by trimming numeric history unless explicitly requested (e.g., 24h logs parameterized).
- The architecture is stateless across processes except for the config/CSV files, enabling future horizontal scaling by introducing a shared database or distributed cache.
