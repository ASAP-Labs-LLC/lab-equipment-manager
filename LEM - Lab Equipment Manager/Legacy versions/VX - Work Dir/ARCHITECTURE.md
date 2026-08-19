# Server-Centric Lab Manager Architecture

## Overview

The application is now organized as a thin PyQt client that defers all stateful logic to the Python HTTP server. The server continuously produces a cached snapshot of plant status, exposes it through a small JSON API, and serializes all authoritative configuration and audit logs. The client polls the server, renders the snapshot, and posts user-initiated actions back to the server.

## Server Responsibilities

- Load and persist canonical configuration (`AppConfig`) and maintenance data.
- Monitor all configured CSV feeds on a background interval, evaluate equipment status once per cycle, and cache the results.
- Own an append-only audit trail for status changes, manual overrides, and maintenance edits.
- Publish a normalized `/state` payload containing:
  - `state_version` monotonic identifier for change detection.
  - Map layout (`boxes` with positions, locks, overrides, and computed status detail lines).
  - Global UI hints (map lock, poll interval, theme, UI scale) to keep clients aligned.
  - Maintenance snapshot (in-progress tasks and templates).
- Serve auxiliary configuration endpoints (`/config`, `/active_pms`) for dialogs that need full metadata.
- Accept all mutation requests (`/action/*`), update config/maintenance stores, mark the cached state dirty, and immediately persist changes to disk.
- Persist the latest state snapshot to `state.json` for diagnostics and offline inspection.

### Performance & Reliability Enhancements

- **State caching**: `State._compute_state_locked` builds the full snapshot once per poll or on-demand when flagged, eliminating per-request CSV evaluation.
- **Dirty tracking**: Mutating routes mark the cache dirty; the next `GET /state` lazily recomputes and bumps `state_version`.
- **Thread-safe serialization**: All data access runs under a re-entrant lock to provide consistent views while the background poller updates.
- **Maintenance refresh**: Background refresh syncs maintenance directories, rewrites the `active_pms.csv` report, and ships the status view in the same response as the map state.

## Client Responsibilities

- Bootstrap from the server-provided configuration when available and maintain a minimal fallback config for offline launch.
- Poll `/state` on a short cadence, skipping UI work when the `state_version` has not changed.
- Update the PyQt scene, list view, and configuration mirrors based on the authoritative snapshot.
- Enqueue local edits when offline and replay them when connectivity is restored.
- Post high-level actions (`add_box`, `edit_box`, `manual_override`, etc.) and rely on the server to persist, audit, and broadcast the result.
- Refresh supplemental configuration (samples, watched targets, CSV paths) after structural edits by calling `_refresh_server_config(force=True)`.

## Data Flow Summary

1. The server bootstraps, loads `AppConfig`, primes the cached state (status = `UNKNOWN`), and starts the monitor thread.
2. Every poll cycle the server reads CSV inputs, recomputes statuses, logs transitions, refreshes maintenance data, and updates the cached snapshot + `state_version`.
3. Clients poll `/state`; if `state_version` is new, they synchronise layout, status, and global settings, then flush any queued mutations.
4. User actions invoke `/action/*`; the server applies the change, persists configuration, and marks the cache dirty. The next poll (or request) rebuilds and publishes the updated snapshot.
5. When clients re-establish connectivity, they automatically pull the latest configuration metadata and replay queued updates.

This separation keeps the GUI deterministic and lightweight while guaranteeing that all critical logic, history, and persistence stay centralized on the server.
