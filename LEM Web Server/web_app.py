#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
web_app.py — V5 Flask app factory (LabCore-backed).

Reuses the V4 evaluation engine (data_source.evaluate_box), models, and the
dashboard template, but sources QC data from LabCore (via LabCoreDataSource) and
stores configuration in the central DB (via DbConfigStore). The dashboard is
static HTML+JS that polls the JSON endpoints below.

The factory takes an injected gateway so tests run against FakeLabCoreGateway and
production runs against HttpLabCoreGateway — the app code is identical either way.
"""

from __future__ import annotations

import csv
import io
import json
import os
import threading
import re
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, session, url_for)

from data_source import build_sample_index, evaluate_box
from db_config_store import DbConfigStore
from labcore_gateway import LabCoreRefused, check_write, refusal_reason
from labcore_source import LabCoreDataSource
from models import (
    AppConfig,
    BoxConfig,
    SampleSpec,
    WatchedTarget,
    STATUS_DEAD,
    STATUS_GREEN,
    STATUS_RED,
    STATUS_SERVICE,
    STATUS_UNKNOWN,
    STATUS_YELLOW,
)

STATUS_COLORS = {
    STATUS_GREEN: "#21c071",
    STATUS_YELLOW: "#f5c542",
    STATUS_RED: "#f85b5b",
    STATUS_DEAD: "#0f172a",
    STATUS_SERVICE: "#8d99ae",
    STATUS_UNKNOWN: "#718096",
}


def format_timestamp(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    try:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def format_value(val, units: str = "") -> str:
    if val is None:
        return "—"
    try:
        text = f"{float(val):g}"
    except Exception:
        text = str(val)
    return f"{text} {units}".strip()


def apply_manual_override(box: BoxConfig, evaluation):
    status = getattr(evaluation, "status", STATUS_UNKNOWN)
    reason = getattr(evaluation, "reason", "")
    if box.manual_override == STATUS_DEAD:
        status, reason = STATUS_DEAD, "Manual override: DEAD-LINE"
        if hasattr(evaluation, "overall_explanation"):
            evaluation.overall_explanation = f"Overridden to DEAD-LINE. Underlying: {evaluation.overall_explanation}"
    elif box.manual_override == STATUS_SERVICE:
        status, reason = STATUS_SERVICE, "Manual override: SERVICE"
        if hasattr(evaluation, "overall_explanation"):
            evaluation.overall_explanation = f"Overridden to SERVICE. Underlying: {evaluation.overall_explanation}"
    return status, reason


def serialize_config(cfg: AppConfig) -> dict:
    return cfg.serialize()


class StatusProvider:
    """Computes the dashboard snapshot from live LabCore data on demand."""

    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self.store = DbConfigStore(gateway)
        self.source = LabCoreDataSource(gateway)

    def load_config(self) -> AppConfig:
        return self.store.load()

    def save_config(self, cfg: AppConfig):
        return self.store.save(cfg)

    def build_snapshot(self) -> dict:
        cfg = self.load_config()
        sample_id_column = cfg.sample_id_column or "Lab ID"
        samples_by_name: Dict[str, SampleSpec] = {s.name: s for s in cfg.samples}

        rows = self.source.load_rows(cfg.samples, sample_id_column)
        sample_index = build_sample_index(rows, sample_id_column)

        boxes_payload: List[dict] = []
        for box in cfg.boxes:
            evaluation = evaluate_box(
                box, samples_by_name, sample_id_column, rows,
                sample_index=sample_index,
            )
            status, reason = apply_manual_override(box, evaluation)
            payload = {
                "uid": box.uid,
                "title": box.title,
                "status": status,
                "status_color": STATUS_COLORS.get(status, "#607d8b"),
                "reason": reason,
                "manual_override": box.manual_override or "",
                "sub_statuses": getattr(evaluation, "sub_statuses", {}),
                "context_results": getattr(evaluation, "context_results", {}),
                "overall_explanation": getattr(evaluation, "overall_explanation", reason),
                "latest_match_time": format_timestamp(getattr(evaluation, "latest_match_time", None)),
                "last_good_qc": format_timestamp(getattr(evaluation, "last_good_qc", None)),
                "spec": [{"sample": wt.sample, "test": wt.test} for wt in box.watched_targets],
                "results": [],
                "pos": list(box.pos),
                "size": list(box.size),
                "locked": box.locked,
                "qc_expire_hours": box.qc_expire_hours,
                "source": "labcore",
            }
            for pr in getattr(evaluation, "results", []):
                label = pr.sample
                if pr.test and pr.test.name:
                    label = f"{pr.sample} / {pr.test.name}"
                payload["results"].append({
                    "label": label,
                    "value": pr.latest_value,
                    "value_display": format_value(pr.latest_value, pr.test.units if pr.test else ""),
                    "in_spec": pr.in_spec,
                    "expected": pr.test.expected if pr.test else None,
                    "low": pr.low,
                    "high": pr.high,
                    "note": pr.note,
                    "timestamp": format_timestamp(pr.latest_time),
                    "timestamp_source": getattr(pr, "timestamp_source", ""),
                })
            boxes_payload.append(payload)

        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "boxes": boxes_payload,
            "errors": [],
            "refresh_seconds": max(60, int(cfg.poll_minutes) * 60),
            "labcore_online": bool(self.gateway.is_running()),
        }


def _now() -> datetime:
    """One clock for liveness and the opening schedule, so a test can move
    the whole floor to a Saturday morning in one place."""
    return datetime.now()


# Measured against the live LabCore: one read_sql round-trip is 194ms at best,
# 1.35s on average and 3.5s at worst — its write queue is slow and variable, so a
# request's wall-clock time is set by HOW MANY round-trips it makes, not by local
# work. `/api/machines` needed ten.
#
# Two intermediate answers lived here and are gone: `_gather()`, which ran those
# ten reads in parallel, and a 4-second response cache. Both made one request
# cheaper while still scaling with the number of screens. See snapshot_service.py
# for what replaced them, and tests/test_performance.py for why.


def _beat_is_fresh(last_poll: Optional[str]) -> bool:
    """Has this module checked in recently enough to call it running?"""
    if not last_poll:
        return False
    from qc_specs import MachineStateReader
    try:
        seen = datetime.fromisoformat(str(last_poll))
    except (TypeError, ValueError):
        return False
    return 0 <= (_now() - seen).total_seconds() <= \
        MachineStateReader.HEARTBEAT_GRACE


# A correction is typed by a human and often pasted. `float()` refuses a Unicode
# minus (U+2212) and the dashes, which look identical to a hyphen on screen — and
# PAC Flash 2's real correction is -3.0, so negatives are routine here, not an edge
# case. Normalised, then parsed strictly: look-alike minus signs are accepted,
# actual junk is still refused rather than guessed at.
_MINUS_LOOKALIKES = {"\u2212": "-", "\u2013": "-", "\u2014": "-",
                     "\u00a0": " ", "\u2007": " ", "\u202f": " "}


def normalise_number_text(text) -> str:
    out = str(text if text is not None else "")
    for bad, good in _MINUS_LOOKALIKES.items():
        out = out.replace(bad, good)
    return out.strip()


def static_version(path: str) -> str:
    """A short fingerprint of a static file, for cache-busting its URL.

    The maximal-map exit button appeared "constantly visible" purely because a
    browser held a `lem.css` from before the rule existed — with no rule the button
    was an unstyled, permanently on-screen <button>. Any CSS or JS change can land
    looking broken on whichever screen still has the old file, so the link carries
    the file's own hash and a changed file gets a new URL.

    Never raises: a packaging slip must not take every page down with it.
    """
    import hashlib

    try:
        with open(path, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()[:10]
    except Exception:
        return "0"


def refusal_response(exc):
    """The client's answer to a write LabCore turned away.

    Three things have to survive the trip to the browser, and each of them is a
    separate bug when it doesn't:

    * **Non-2xx, and no `ok` key.** The floor's save handlers branch on
      `r.ok`; anything in the 200s and the dialog closes clean over a change
      that is not in LabCore.
    * **Transient told apart from permanent.** "The queue is deep" is worth
      retrying and "no such column" never will be, and a client cannot be asked
      to tell them apart by reading English. 503 + `retryable: true` for busy —
      503 is the status whose whole meaning is "come back" — and 502 for a
      refusal that will refuse again, because that one is genuinely an upstream
      answer we cannot make good.
    * **`retry_after`, honoured rather than discarded.** LabCore says how long
      it wants to be left alone. Both a `Retry-After` header (for anything
      speaking HTTP properly) and the same number in the body (for the floor's
      `fetch`, which cannot see headers it wasn't told to read).
    """
    body = {"error": exc.reason, "busy": exc.busy, "retryable": exc.busy}
    if exc.what:
        # The reason is LabCore's sentence about its queue; `what` is ours about
        # this lab. A supervisor needs the second one — "the queue is deep" does
        # not tell them their correction factor is not in force.
        body["error"] = f"{exc.reason.rstrip('. ')} — {exc.what}."
    delay = exc.retry_after
    headers = {}
    if delay is not None:
        body["retry_after"] = delay
        headers["Retry-After"] = str(int(delay))
    body.update(exc.extra)
    return jsonify(body), (503 if exc.busy else 502), headers


def create_app(gateway, admin_password: Optional[str] = None,
               secret: Optional[str] = None, authenticator=None,
               live=None, live_token: Optional[str] = None) -> Flask:
    app = Flask(__name__)

    # The live road: what benches say about themselves, in memory only. The
    # record stays LabCore's. Publishing the address/token to `lem_meta` is a
    # BOOT step (see web_server.pyw) — a factory with side effects would give
    # every test a LabCore write, which is the lesson the snapshot poller taught.
    from live_presence import (LivePresence, STALE_CORRECTIONS, STALE_OVERRIDE,
                               resolve_token)
    app.config["LIVE"] = live if live is not None else LivePresence()
    app.config["LIVE_TOKEN"] = resolve_token(
        live_token or os.environ.get("LEM_LIVE_TOKEN"))

    # Available to every template as `v('lem.css')`.
    @app.template_global("v")
    def _static_v(filename: str) -> str:
        return static_version(
            os.path.join(app.static_folder or "static", filename))

    # The 3D floor loads as ES modules, and a static `import` cannot carry a
    # version of its own — so the import map is the only place a fingerprint can
    # go. Without it a screen holding last week's terrain.js runs it against
    # this week's renderer, which is precisely the stale-static failure
    # `static_version` exists to prevent. Bare specifiers throughout:
    # `import {Rail} from "world/rail.js"`.
    @app.template_global("worldmap")
    def _world_importmap() -> str:
        import json as _json

        root = app.static_folder or "static"
        imports = {"three": "/static/vendor/three.module.min.js?v="
                            + static_version(os.path.join(
                                root, "vendor", "three.module.min.js"))}
        try:
            names = sorted(os.listdir(os.path.join(root, "world")))
        except OSError:
            names = []                      # never fatal: see static_version
        for name in names:
            if name.endswith(".js"):
                imports["world/" + name] = (
                    "/static/world/" + name + "?v="
                    + static_version(os.path.join(root, "world", name)))
        return _json.dumps({"imports": imports}, indent=1)

    app.secret_key = secret or os.environ.get("LABMGR_SECRET", "lem-v5-dev-secret")

    # Login is the suite-wide LabCore one (same accounts + NFC cards as
    # LabStation/LabEntry). `admin_password` remains only as an offline
    # escape hatch for --dev runs with no LabCore.
    from labcore_auth import LabCoreAuth

    auth_backend = authenticator or LabCoreAuth(gateway=gateway)
    admin_pw = admin_password or os.environ.get("LABMGR_ADMIN_PASSWORD")

    provider = StatusProvider(gateway)
    app.config["PROVIDER"] = provider

    def authed() -> bool:
        return bool(session.get("user"))

    # ── a refused write is never reported as a save ───────────────────
    # LabCore does not refuse by raising. It returns an error DICT and the
    # gateway hands it straight back, so `gateway.sql(...)` on a line of its own
    # is indistinguishable from a write that landed. Several endpoints here
    # threw that answer away and replied `200 {"ok": true}` — a supervisor could
    # set a correction factor, watch it succeed, and have it not exist, while
    # the lab went on reporting uncorrected results (ISO/IEC 17025 §7.8.2). This
    # is the same class as notes.md's bulk import that "reported 'imported 3094'
    # while nothing landed".
    #
    # The guard is an ERROR HANDLER rather than a check per endpoint, because a
    # check per endpoint is a pattern to remember and the one nobody remembered
    # is exactly where the correction factor was dropped. Stores raise
    # `LabCoreRefused` from `check_write`; anything that escapes an endpoint
    # lands here and cannot become a 200 by omission. An endpoint that needs to
    # say more than "it did not land" — which statements DID, for the
    # multi-statement saves — catches it itself and passes `landed`.
    @app.errorhandler(LabCoreRefused)
    def _labcore_refused(exc: LabCoreRefused):
        return refusal_response(exc)

    # ── UI ────────────────────────────────────────────────────────────
    # Login → mode selector → Map or Checklists. The floor used to be the root,
    # which is wrong on a phone: someone walking the lab wants their checklist
    # or the map, not a 3D floor plan to pinch past.
    @app.route("/")
    def home():
        """Two big targets, and nothing else to get wrong."""
        return render_template("home.html", active="/")

    @app.route("/floor")
    def floor():
        """The lab floor: every instrument on its bay, hover for a glance,
        click for the full record, right-click to act on it."""
        return render_template("floor.html", active="/floor")

    @app.route("/maintenance")
    def maintenance_page():
        """Every machine's PM and calibration in one place, worst first."""
        return render_template("maintenance.html", active="/maintenance")

    @app.route("/checklists")
    def checklists():
        """Opening and closing rounds. The checklist system itself isn't built
        yet — this page says so rather than pretending."""
        return render_template("checklists.html", active="/checklists")

    @app.route("/stations")
    @app.route("/dashboard")
    def retired_pages():
        return redirect(url_for("floor"))

    # ── read endpoints ────────────────────────────────────────────────
    @app.route("/api/status")
    def api_status():
        return jsonify(provider.build_snapshot())

    @app.route("/api/config")
    def api_config():
        return jsonify(serialize_config(provider.load_config()))

    @app.route("/api/me")
    def api_me():
        return jsonify({"authenticated": authed(), "user": session.get("user", "")})

    # ── auth ──────────────────────────────────────────────────────────
    @app.route("/api/login", methods=["POST"])
    def api_login():
        body = request.get_json(silent=True) or {}
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
        user, token, error = auth_backend.login(username, password)
        if user:
            session["user"] = user
            session["token"] = token
            return jsonify({"ok": True, "user": user})
        # Offline escape hatch: only when an admin password is explicitly
        # configured (LABMGR_ADMIN_PASSWORD) — e.g. a --dev run.
        if admin_pw and password == admin_pw:
            session["user"] = username or "admin"
            session["token"] = ""
            return jsonify({"ok": True, "user": session["user"]})
        return jsonify({"ok": False,
                        "error": error or "Invalid credentials"}), 401

    @app.route("/api/logout", methods=["POST"])
    def api_logout():
        token = session.pop("token", "")
        session.pop("user", None)
        if token:
            auth_backend.logout(token)
        return jsonify({"ok": True})

    # ── mutation: add box (writes config to central DB) ───────────────
    @app.route("/api/boxes", methods=["POST"])
    def api_add_box():
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        cfg = provider.load_config()
        box = BoxConfig(
            uid=body.get("uid") or f"box_{uuid.uuid4().hex[:12]}",
            title=str(body.get("title") or "New Machine"),
            csv_path="",
            watched_targets=[WatchedTarget.from_dict(w) for w in body.get("watched_targets", [])],
        )
        cfg.boxes.append(box)
        ok, msg = provider.save_config(cfg)
        if not ok:
            return jsonify({"error": msg}), 500
        return jsonify({"ok": True, "box": box.serialize()})

    @app.route("/api/refresh", methods=["POST"])
    def api_refresh():
        return jsonify(provider.build_snapshot())

    # ── LEM Station bridge: machines, QC specs, events, overrides ─────
    #
    # Station modules (one per machine, in LabStation) publish their state
    # to LabCore and read their QC specs back from it. This master view
    # owns the specs and the control channel.

    from machine_map import (MachineLayoutStore, MapSettingsStore,
                             QcTargetStore, WatchedTarget)
    from qc_specs import MachineStateReader, QcSpec, QcSpecStore

    spec_store = QcSpecStore(gateway)
    state_reader = MachineStateReader(gateway)
    layout_store = MachineLayoutStore(gateway)
    target_store = QcTargetStore(gateway)
    map_settings = MapSettingsStore(gateway)

    @app.route("/api/map")
    def api_map():
        return jsonify({"locked": map_settings.locked()})

    @app.route("/api/map", methods=["POST"])
    def api_set_map():
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        map_settings.set_locked(bool(body.get("locked")))
        return jsonify({"ok": True, "locked": map_settings.locked()})

    # ── the lab's opening hours ────────────────────────────────────────
    # Silence only means "stopped" while the lab is open; see lab_schedule.
    from lab_schedule import LabSchedule, LabScheduleStore

    schedule_store = LabScheduleStore(gateway)

    # ── the floor is served from memory, not from LabCore ──────────────
    # One background refresher for the whole server: LabCore load is the same
    # whether one screen is open or ten, and no request waits on a round-trip.
    from snapshot_service import SnapshotService, build_machines

    snapshots = SnapshotService(
        gateway,
        interval=float(os.environ.get("LEM_SNAPSHOT_SECONDS", "12")),
        builder=lambda tables: build_machines(tables, _now(), _beat_is_fresh,
                                             STATUS_COLORS))
    # Deliberately NOT started here. An app factory that spawns a thread gives
    # every caller a background refresher it did not ask for — and the old
    # `if not app.config["TESTING"]` guard could never work, because a test sets
    # TESTING on the object this function has already returned. The entry point
    # owns the lifecycle: web_server.pyw calls start(). Without a poller,
    # refresh_soon() refreshes inline, so behaviour stays correct either way.
    app.config["SNAPSHOTS"] = snapshots

    # ── the page cache ────────────────────────────────────────────────
    # For answers this process is the ONLY writer of: checklist definitions,
    # a day's ticks, the log's `kind` vocabulary. Nobody else in the lab writes
    # them, so every write that could change an answer drops it here and the
    # cache costs no staleness at all — an operator cannot fail to see their own
    # tick. (The floor is different: the modules write it, so it goes through
    # SnapshotService and reports its age.)
    #
    # Keyed by string, dropped by prefix, and capped — an unbounded cache keyed
    # by day would quietly grow for as long as the server ran.
    _pages: "dict[str, object]" = {}
    _pages_lock = threading.Lock()
    PAGE_CACHE_MAX = 48

    def _build_checklist_day(day: str = "", when=None) -> dict:
        """One day's rounds and ticks. Shared by the endpoint and the warm-up so
        the cached shape can only ever be built one way."""
        day = day or _today()
        when = when or datetime.fromisoformat(day).date()
        state = checklist_store.state(day)
        out = []
        for cl in checklist_store.all():
            items = active_items(cl.items, when)
            checked, total, pct = completion(items, state.get(cl.uid, {}))
            payload = cl.to_dict()
            payload["items"] = [i.to_dict() for i in items]
            payload.update({"checked": checked, "total": total, "pct": pct})
            out.append(payload)
        return {"checklists": out, "state": state, "day": day}

    _building: "dict[str, threading.Lock]" = {}

    def _page(key: str, produce):
        """Cached answer for `key`, produced at most once at a time.

        Single-flight matters here: ten viewers landing on a cold checklist page
        used to run ten identical LabCore reads in parallel, each paying its own
        TLS setup. One thread builds; the rest wait for its answer.

        The per-key lock is what keeps that from turning one slow page into every
        slow page — a single global lock would make a cached hit queue behind an
        unrelated build.
        """
        with _pages_lock:
            if key in _pages:
                return _pages[key]
            gate = _building.get(key)
            if gate is None:
                gate = _building[key] = threading.Lock()
        with gate:
            with _pages_lock:
                if key in _pages:            # someone else built it while we waited
                    return _pages[key]
            try:
                value = produce()
            finally:
                # Released even when produce() raises, or one failed read would
                # wedge this key for the life of the process.
                with _pages_lock:
                    _building.pop(key, None)
            with _pages_lock:
                if len(_pages) >= PAGE_CACHE_MAX:
                    for stale in list(_pages)[:len(_pages) - PAGE_CACHE_MAX + 1]:
                        _pages.pop(stale, None)
                _pages[key] = value
            return value

    def _page_drop(*prefixes: str) -> None:
        with _pages_lock:
            for key in [k for k in _pages
                        if any(k.startswith(p) for p in prefixes)]:
                _pages.pop(key, None)


    from machine_configs import MachineConfigStore

    config_store = MachineConfigStore(gateway)

    @app.route("/api/schedule")
    def api_get_schedule():
        # From the snapshot's rows, which already contain lem_lab_schedule and
        # lem_lab_holidays — the batched read fetches them anyway. `to_dict`
        # applies the clock here, so `open_now` is exact rather than up to one
        # refresh interval behind.
        from snapshot_service import schedule_from_tables
        tables = _snapshot_tables()
        if tables is None:
            return jsonify(schedule_store.load().to_dict(_now()))
        return jsonify(schedule_from_tables(tables).to_dict(_now()))

    @app.route("/api/schedule", methods=["POST"])
    def api_save_schedule():
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        current = schedule_store.load()
        try:
            saved = schedule_store.save(LabSchedule(
                working_days=body.get("working_days", current.working_days),
                opens=str(body.get("opens", current.opens) or ""),
                closes=str(body.get("closes", current.closes) or ""),
                holidays=body.get("holidays", current.holidays) or {}))
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        snapshots.refresh_soon()
        _audit("schedule changed", "",
               {"working_days": saved.working_days, "opens": saved.opens,
                "closes": saved.closes})
        return jsonify({"ok": True, "schedule": saved.to_dict(_now())})

    @app.route("/api/holidays", methods=["POST"])
    def api_add_holiday():
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        try:
            schedule_store.add_holiday(str(body.get("day") or ""),
                                       str(body.get("name") or ""))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        snapshots.refresh_soon()
        return jsonify({"ok": True})

    @app.route("/api/holidays/<day>", methods=["DELETE"])
    def api_remove_holiday(day):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        try:
            schedule_store.remove_holiday(day)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        snapshots.refresh_soon()
        return jsonify({"ok": True})

    # ── checklists: opening and closing rounds ─────────────────────────
    from checklists import (Checklist, ChecklistStore, active_items,
                           completion)

    checklist_store = ChecklistStore(gateway)

    def _today() -> str:
        return _now().date().isoformat()

    @app.route("/api/checklists")
    def api_get_checklists():
        """Definitions scoped to the day, plus that day's ticks."""
        day = (request.args.get("day") or "").strip() or _today()
        try:
            when = datetime.fromisoformat(day).date()
        except ValueError:
            day, when = _today(), _now().date()
        return jsonify(_page(f"checklists:{day}",
                             lambda: _build_checklist_day(day, when)))

    @app.route("/api/checklists", methods=["POST"])
    def api_save_checklist():
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        try:
            saved = checklist_store.save(Checklist.from_dict(body))
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        _page_drop("checklists:")      # the definition changed, so every day did
        _audit("checklist saved", "",
               {"checklist": saved.name, "items": len(saved.items)})
        return jsonify({"ok": True, "checklist": saved.to_dict()})

    @app.route("/api/checklists/<uid>", methods=["DELETE"])
    def api_delete_checklist(uid):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        existing = checklist_store.get(uid)
        checklist_store.delete(uid)
        _page_drop("checklists:")
        _audit("checklist deleted", "",
               {"checklist": (existing.name if existing else uid)})
        return jsonify({"ok": True})

    @app.route("/api/checklists/<uid>/toggle", methods=["POST"])
    def api_toggle_checklist(uid):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        checklist = checklist_store.get(uid)
        if checklist is None:
            return jsonify({"error": "No such checklist."}), 404
        item_uid = str(body.get("item_uid") or "").strip()
        if not item_uid:
            return jsonify({"error": "Which item?"}), 400
        day = (str(body.get("day") or "").strip() or _today())
        touched = checklist_store.toggle(
            checklist, item_uid, bool(body.get("checked")), day,
            session.get("user", ""))
        # Only this day, and the archive's per-day counts. Yesterday's cached
        # answer is still correct and there is no reason to make someone pay for
        # it again.
        _page_drop(f"checklists:{day}", "checklisthistory")
        return jsonify({"ok": True, "touched": touched})

    @app.route("/api/checklists/<uid>/value", methods=["POST"])
    def api_checklist_value(uid):
        """Record a reading on an item that asks for one."""
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        checklist = checklist_store.get(uid)
        if checklist is None:
            return jsonify({"error": "No such checklist."}), 404
        item_uid = str(body.get("item_uid") or "").strip()
        item = next((i for i in checklist.items if i.uid == item_uid), None)
        if item is None:
            return jsonify({"error": "No such item."}), 404
        if item.entry_type not in ("number", "text"):
            return jsonify({"error": f"“{item.text}” is a tick, not a field."}), 400
        value = str(body.get("value") or "").strip()
        if item.entry_type == "number" and value:
            try:
                float(value)
            except ValueError:
                # Refused rather than stored: one "about half" and the trend
                # this field exists for silently stops being a trend.
                return jsonify({"error": f"{value!r} is not a number. "
                                         f"Use a plain figure"
                                         + (f" in {item.units}." if item.units
                                            else ".")}), 400
        day = (str(body.get("day") or "").strip() or _today())
        checklist_store.set_value(uid, item_uid, value, day,
                                  session.get("user", ""))
        _page_drop(f"checklists:{day}", "checklisthistory")
        return jsonify({"ok": True})

    @app.route("/api/checklists/<uid>/values")
    def api_checklist_values(uid):
        """One numeric item's readings over time — the point of `number`."""
        item_uid = (request.args.get("item") or "").strip()
        checklist = checklist_store.get(uid)
        item = None
        if checklist is not None:
            item = next((i for i in checklist.items if i.uid == item_uid), None)
        return jsonify({"series": checklist_store.values(uid, item_uid),
                        "units": item.units if item else "",
                        "text": item.text if item else ""})

    @app.route("/api/checklists/import-v4", methods=["POST"])
    def api_import_v4_checklists():
        """Bring the old LEM's rounds across from lab_manager_config.json."""
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        text = body.get("json")
        if not isinstance(text, str) or not text.strip():
            return jsonify({"error": "No V4 config supplied."}), 400
        from checklists import import_v4_checklists, import_v4_state
        found = import_v4_checklists(text)
        if not found:
            return jsonify({"error": "No checklists with any items were found "
                                     "in that file."}), 400
        dry = (request.args.get("dry_run") or "").strip() in ("1", "true",
                                                             "yes")
        preview = [{"name": c.name, "slot": c.slot, "due_time": c.due_time,
                    "items": len(c.items)} for c in found]
        if dry:
            rows = []
            state_text = body.get("state")
            if isinstance(state_text, str) and state_text.strip():
                known = {c.uid for c in found}
                rows = [r for r in import_v4_state(state_text, found)
                        if r["checklist_uid"] in known]
            return jsonify({"count": len(found), "checklists": preview,
                            "history_rows": len(rows),
                            "history_days": len({r["day"] for r in rows}),
                            "dry_run": True})
        for checklist in found:
            checklist_store.save(checklist)

        # The archive: V4's checklist_state.json, if it came along.
        history_rows = 0
        history_days = 0
        state_text = body.get("state")
        if isinstance(state_text, str) and state_text.strip():
            rows = import_v4_state(state_text, found)
            known = {c.uid for c in found}
            rows = [r for r in rows if r["checklist_uid"] in known]
            history_days = len({r["day"] for r in rows})
            history_rows = checklist_store.import_state(rows)
        _page_drop("checklists:", "checklisthistory")
        _audit("checklist v4 import", "",
               {"imported": len(found), "names": [c.name for c in found],
                "history_rows": history_rows, "history_days": history_days})
        return jsonify({"count": len(found), "checklists": preview,
                        "history_rows": history_rows,
                        "history_days": history_days, "dry_run": False})

    @app.route("/api/checklists/history")
    def api_checklist_history():
        # A GROUP BY over every tick ever recorded (3094 rows and counting),
        # asked for again on every visit to the archive.
        return jsonify(_page("checklisthistory",
                             lambda: {"days": checklist_store.history()}))

    @app.route("/api/machines")
    def api_machines():
        """Served from the in-memory snapshot — no LabCore in the request path.

        `?fresh=1` forces a rebuild for the rare caller that must have the very
        latest (a test, or a page right after a write).
        """
        if (request.args.get("fresh") or "").strip() in ("1", "true", "yes"):
            snapshots.refresh()
        snap = snapshots.get()
        if not snap.get("ready"):
            # Only reachable when even the first build failed (LabCore down).
            snapshots.refresh_soon()
            return jsonify({"machines": [], "labcore_online": False,
                            "warming": True, "age_seconds": None})
        # The live road overlays the record where a bench has spoken for itself
        # more recently than the queue could carry it. Failover, not merge —
        # see live_presence.merge_machines.
        from live_presence import merge_machines
        return jsonify({"machines": merge_machines(snap.get("machines") or [],
                                                   app.config["LIVE"],
                                                   STATUS_COLORS),
                        "labcore_online": snap.get("labcore_online", True),
                        "age_seconds": snap.get("age_seconds"),
                        "stale": snap.get("stale", False)})

    @app.route("/api/live", methods=["POST"])
    def api_live():
        """A bench reporting itself: running, status, and what it just parsed.

        Not a session endpoint — modules do not log in. The shared token in
        `lem_meta` is what separates a bench from anything else that can reach
        the port.

        This handler must never touch LabCore, not even to look something up:
        one ping per bench per poll multiplied by every instrument in the lab is
        precisely the load pattern the snapshot exists to prevent.

        **The response carries the bench's stale notes** — `{"stale": [...]}`,
        any subset of `STALE_KINDS`, empty when nothing is pending. A bench used
        to ask LabCore twice a minute whether its correction factors or its
        manual override had changed; between them those two reads were ~64% of
        everything on a queue that serialises at ~1.5 ops/sec, and the answer
        was almost always no. It already pushes here twice a minute, so the
        answer rides back on a call that was happening anyway and the bench does
        ONE LabCore read only when there is something to read.

        The note names the KIND and never the value — this is a doorbell, not a
        delivery. Values on this road would make the floor a second writer of a
        fact LabCore owns, which is the failure the failover rule in
        `merge_machines` exists to avoid; it would also need the read this is
        removing. Notes are in memory only, so the invariant above is intact.

        This used to answer `"", 204`. A 200 with a body is backward compatible
        — a module built before this change ignores it — so the two sides can be
        deployed in either order.
        """
        import hmac
        supplied = request.headers.get("X-LEM-Token", "")
        if not hmac.compare_digest(str(supplied),
                                   str(app.config["LIVE_TOKEN"])):
            return jsonify({"error": "Not authorised."}), 401
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "Expected a JSON object."}), 400
        if not str(body.get("machine_uid") or "").strip():
            return jsonify({"error": "machine_uid is required."}), 400
        live = app.config["LIVE"]
        live.record(body["machine_uid"], body)
        # Collected even when `record` refused the push (a POST delayed in
        # flight arriving after a newer one). The bench is real and IS reading
        # this response, so handing the notes over is right; withholding them
        # would strand a change behind the backstop poll for no gain.
        return jsonify({"stale": live.take_stale(body["machine_uid"])})

    @app.route("/api/bench/<machine_uid>/config")
    def api_bench_config(machine_uid):
        """A bench reading its own configuration — from memory, never LabCore.

        The floor stopped being a bad neighbour when screens started reading a
        snapshot instead of the database; the benches never did. Every module
        still polls LabCore itself for its QC samples, targets, specs,
        maintenance, correction factors and manual override, so **LabCore load
        grows with the number of instruments in the lab** — and the database
        lives on an SMB share that cannot move, which means every one of those
        reads takes a slot in the same serialised write queue LabStation and
        LabEntry are using. That is the load that is crashing it.

        This server already reads all nine of those tables in ONE `UNION ALL`
        every 12s, and it is co-located with LabCore. So the same rule the
        screens got applies to the benches: *a request never talks to LabCore,
        and LabCore load does not depend on how many people are looking* — now
        with benches counted among the lookers. Ten instruments or fifty, the
        cost is the same one op per cycle. `test_bench_config.py` asserts the
        zero with a CountingGateway, the way the push path does.

        Two details that are load-bearing rather than cosmetic:

        * **`snapshot_age_seconds` is the snapshot's own age**, straight off
          `get()`. The module refuses configuration older than its own tolerance
          and falls back to reading LabCore directly — a safety net that only
          works if the age is true. A second clock started when the request
          arrived would read as 0.0 forever and quietly pin every bench to
          whatever this server last managed to fetch, however long ago that was.
        * **an unknown uid is a 200 with empty lists, never a 404.** A machine
          that is registered but not yet configured is an ordinary state, and a
          bench that gets a 404 concludes this road is not for it and goes back
          to LabCore for good — re-creating the exact per-bench load being
          removed, for a machine somebody simply had not set up yet.

        A never-populated snapshot is the one case that must NOT answer with
        empty lists: an empty configuration is a real instruction the bench acts
        on (it would clear its QC and drop its override), so "I have nothing
        yet" is a 503 and the bench keeps what it has.
        """
        import hmac
        # Identical to /api/live, deliberately: benches do not log in, and one
        # shared token in `lem_meta` is what separates a bench from anything else
        # that can reach the port.
        supplied = request.headers.get("X-LEM-Token", "")
        if not hmac.compare_digest(str(supplied),
                                   str(app.config["LIVE_TOKEN"])):
            return jsonify({"error": "Not authorised."}), 401
        # build_if_missing=False on purpose. Letting the caller build would make
        # this endpoint cost a LabCore read after all — and worst of all at the
        # worst moment, when a lab full of benches comes back at once after an
        # outage and stampedes the queue that is already down. It waits for the
        # poller instead.
        snap = snapshots.get(build_if_missing=False)
        if not snap.get("ready"):
            # Not even refresh_soon() here: with no poller running it refreshes
            # INLINE, which is the stampede this is avoiding, wearing a different
            # hat. The poller is the only thing that reads LabCore.
            return jsonify({"error": "The snapshot has not built yet.",
                            "stale": True}), 503
        from snapshot_service import bench_config_from_tables
        payload = bench_config_from_tables(snapshots.tables(), machine_uid)
        payload["snapshot_age_seconds"] = snap.get("age_seconds")
        return jsonify(payload)

    @app.route("/api/machines/<machine_uid>/position", methods=["POST"])
    def api_machine_position(machine_uid):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        if map_settings.locked():
            return jsonify({"error": "The map is locked. Unlock it to "
                                     "rearrange the floor."}), 409
        body = request.get_json(silent=True) or {}
        try:
            layout_store.save_position(machine_uid,
                                       float(body.get("x")), float(body.get("y")))
        except (TypeError, ValueError):
            return jsonify({"error": "Position needs numeric x and y."}), 400
        snapshots.refresh_soon()
        return jsonify({"ok": True})

    @app.route("/api/machines/<machine_uid>/qc-targets", methods=["POST"])
    def api_machine_targets(machine_uid):
        """Assign which QC sample + test this instrument is checked against."""
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        raw = body.get("targets")
        if not isinstance(raw, list):
            return jsonify({"error": "Expected a list of targets."}), 400
        targets = [WatchedTarget.from_dict(t) for t in raw
                   if isinstance(t, dict)]
        target_store.assign(machine_uid, targets)
        snapshots.refresh_soon()
        _audit("qc-targets assigned", machine_uid,
               {"targets": [t.to_dict() for t in targets]})
        return jsonify({"ok": True})

    @app.route("/api/machines/<machine_uid>", methods=["DELETE"])
    def api_delete_machine(machine_uid):
        """Retire a machine a station module registered — clears its live
        status, QC specs and control row. Its history in lem_machine_log is
        kept unless purge_history is requested."""
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        # This drops the config too, so it must not be a way around the guard.
        refusal = _refuse_if_live(machine_uid, body)
        if refusal is not None:
            return refusal

        # Declared before the first DELETE, and not for the schema's sake: LEM
        # owns all four of these tables and `DELETE FROM` one that does not
        # exist yet comes back as an error dict indistinguishable from a
        # refusal. On a lab where nobody has ever set an override that would
        # turn a perfectly good retirement into a 502. Memoised, so this is one
        # read at most once per process — and `_audit` at the bottom of this
        # very function already called it.
        snapshots.ensure_schema()

        # Retiring a machine is several statements and there is NO transaction
        # across queue operations. So the honest thing when statement two is
        # refused is to say which ones happened, not to pretend the set was
        # atomic in either direction: `{"ok": true}` leaves a control row for a
        # machine that no longer exists, and a bare 503 leaves somebody to
        # discover that the status row went anyway.
        #
        # It STOPS at the first refusal rather than pushing on. LabCore has just
        # said its queue is too deep; firing the remaining statements at it is
        # the load the refusal was asking to be spared, and the station module's
        # event drain gives up its turn for exactly the same reason.
        landed: list = []
        failed = None

        def _step(name, run):
            """Run one statement of the cascade unless an earlier one refused."""
            nonlocal failed
            if failed is not None:
                return False
            try:
                check_write(run())
            except LabCoreRefused as exc:
                failed = (name, exc)
                return False
            landed.append(name)
            return True

        _step("live status", lambda: gateway.sql(
            "DELETE FROM lem_machine_status WHERE machine_uid = ?",
            [machine_uid]))
        _step("QC specs", lambda: gateway.sql(
            "DELETE FROM lem_qc_specs WHERE machine_uid = ?", [machine_uid]))
        # `lem_machine_control` is the manual-override row and nothing else, so
        # dropping it IS an override change from the bench's point of view —
        # just to the empty state. A module still running this machine when it
        # is retired would otherwise hold SERVICE until its backstop poll.
        # The note is left only if that DELETE really landed: reached only after
        # the guard above let the delete through, so a request that came back
        # 401 or 409 has changed nothing and marks nothing, and one refused here
        # would otherwise send a bench to LabCore to re-read a row still sitting
        # exactly where it was.
        if _step("manual override", lambda: gateway.sql(
                "DELETE FROM lem_machine_control WHERE machine_uid = ?",
                [machine_uid])):
            app.config["LIVE"].mark_stale(machine_uid, STALE_OVERRIDE)
        # The cascades. `layout` is cosmetic and stays best-effort; the other
        # three are stores that refuse by raising, so they go through `_step`
        # like the rest. A stranded config would offer itself again in the
        # module's picker.
        if failed is None:
            layout_store.forget(machine_uid)
        _step("QC assignments", lambda: target_store.forget(machine_uid))
        _step("maintenance tasks", lambda: maint_store.forget(machine_uid))
        _step("configuration", lambda: config_store.delete(machine_uid))
        if body.get("purge_history"):
            # Erasing history is the most destructive half of this endpoint, so
            # it is the half least tolerable to be wrong about in either
            # direction — "deleted" while the log is untouched, or a silent
            # wipe.
            _step("history", lambda: gateway.sql(
                "DELETE FROM lem_machine_log WHERE machine_uid = ?",
                [machine_uid]))

        snapshots.refresh_soon()
        if failed is not None:
            name, exc = failed
            # Audited as what it was. "machine deleted" for a machine that is
            # half deleted is a falsehood in the one log an assessor reads, and
            # writing nothing at all about a destructive partial action is
            # worse. `_audit` never raises, so this cannot mask the refusal.
            _audit("machine deletion incomplete", machine_uid,
                   {"landed": list(landed), "refused_at": name})
            return refusal_response(LabCoreRefused(
                exc.result,
                what=f"“{machine_uid}” was only partly retired — its {name} "
                     f"could not be removed",
                partial=True, landed=list(landed),
                not_landed=[name]))
        # Audited AFTER the purge on purpose: wiping a machine's history is the
        # one action whose record must survive the wipe.
        _audit("machine deleted", machine_uid,
               {"purged_history": bool(body.get("purge_history"))})
        return jsonify({"ok": True})

    # ── equipment configuration, held centrally ────────────────────────
    # A module starting up picks from these; see machine_configs.py.


    def _snapshot_tables():
        """The snapshot's rows if it has ever built, else None — read live.

        The distinction that matters is "has the snapshot run", not "did it find
        anything": a lab with no heartbeats yet is a real answer, and treating it
        as missing would buy a round-trip to confirm there was nothing to fetch.
        """
        return snapshots.tables() if snapshots.get().get("ready") else None

    def _machine_list() -> list:
        """The machine list — from the snapshot, which reads it every cycle anyway.

        This was memoised on Flask's `g`, which stopped endpoints asking three
        times in ONE request but not every request asking once. Most callers only
        want uid → title, and the snapshot has had that in hand since its last
        refresh.

        It falls back to a live read only when the snapshot has never built (the
        very first request, or LabCore down at startup), so an empty lab and an
        unbuilt snapshot are not confused.
        """
        from flask import g
        cached = getattr(g, "_lem_machines", None)
        if cached is not None:
            return cached
        snap = snapshots.get()
        if snap.get("ready"):
            rows = [{"machine_uid": m["machine_uid"], "title": m["title"],
                     "status": m["status"], "reason": m.get("reason", ""),
                     "updated_at": m.get("updated_at", "")}
                    for m in snap.get("machines") or []]
        else:
            rows = state_reader.machines()
        g._lem_machines = rows
        return rows

    def _titles() -> dict:
        return {m["machine_uid"]: m["title"] for m in _machine_list()}

    def _live_beats() -> dict:
        """Which machines have a module checking in right now."""
        return {uid: beat.get("last_poll")
                for uid, beat in state_reader.heartbeats().items()
                if _beat_is_fresh(beat.get("last_poll"))}

    @app.route("/api/machine-configs")
    def api_list_machine_configs():
        """Configs, each flagged with whether a parser is live on it — the
        picker and the delete guard both need to know."""
        # Heartbeats come from the snapshot: it reads lem_machine_heartbeat every
        # cycle, and "is a parser live" is judged against a grace window far
        # wider than the refresh interval, so a cached beat cannot change the
        # verdict. The configs themselves are read live — a module registering
        # must show up in the picker straight away.
        from snapshot_service import beats_from_tables
        tables = _snapshot_tables()
        beats = (beats_from_tables(tables) if tables is not None
                 else state_reader.heartbeats())
        configs = config_store.list()
        for row in configs:
            beat = beats.get(row["machine_uid"]) or {}
            row["last_poll"] = beat.get("last_poll")
            row["in_use"] = _beat_is_fresh(beat.get("last_poll"))
        return jsonify({"configs": configs})

    @app.route("/api/machine-configs/<machine_uid>")
    def api_get_machine_config(machine_uid):
        record = config_store.get(machine_uid)
        if record is None:
            return jsonify({"error": "No configuration for that machine."}), 404
        return jsonify(record)

    @app.route("/api/machine-configs", methods=["POST"])
    def api_create_machine_config():
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        try:
            made = config_store.create(str(body.get("title") or ""),
                                       by=session.get("user", ""))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True, **made})

    @app.route("/api/machine-configs/<machine_uid>", methods=["POST"])
    def api_save_machine_config(machine_uid):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        config = body.get("config")
        if not isinstance(config, dict):
            return jsonify({"error": "Expected a config object."}), 400
        try:
            saved = config_store.save(machine_uid,
                                      str(body.get("title") or ""), config,
                                      by=session.get("user", ""))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        snapshots.refresh_soon()
        return jsonify({"ok": True, **saved})

    @app.route("/api/machine-configs/<machine_uid>/duplicate",
               methods=["POST"])
    def api_duplicate_machine_config(machine_uid):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        try:
            made = config_store.duplicate(machine_uid,
                                          str(body.get("title") or ""),
                                          by=session.get("user", ""))
        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True, **made})

    def _refuse_if_live(machine_uid, body):
        """A parser may be running this config right now. Deleting it is still
        allowed — the module clears itself and returns to its picker — but not
        by accident, so the first attempt comes back naming what it would
        break."""
        if body.get("confirm"):
            return None
        if machine_uid not in _live_beats():
            return None
        record = config_store.get(machine_uid)
        name = (record or {}).get("title") or machine_uid
        return jsonify({
            "error": f"A LabStation module is running “{name}” right now. "
                     f"Deleting this will clear that module's configuration "
                     f"and stop it parsing. Confirm to go ahead.",
            "in_use": True, "needs_confirmation": True}), 409

    @app.route("/api/machine-configs/<machine_uid>", methods=["DELETE"])
    def api_delete_machine_config(machine_uid):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        refusal = _refuse_if_live(machine_uid, body)
        if refusal is not None:
            return refusal
        config_store.delete(machine_uid)
        snapshots.refresh_soon()
        return jsonify({"ok": True})

    # ── PM & calibration, managed from the floor ──────────────────────
    from maintenance_store import MaintenanceStore, MaintTaskRecord

    maint_store = MaintenanceStore(gateway)

    @app.route("/api/machines/<machine_uid>/maintenance")
    def api_list_maintenance(machine_uid):
        return jsonify({"tasks": [t.to_dict() for t in
                                  maint_store.for_machine(machine_uid)]})

    @app.route("/api/machines/<machine_uid>/maintenance", methods=["POST"])
    def api_save_maintenance(machine_uid):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        try:
            task = maint_store.save(MaintTaskRecord(
                uid=str(body.get("uid") or ""),
                machine_uid=machine_uid,
                name=str(body.get("name") or ""),
                kind=str(body.get("kind") or "pm"),
                interval_days=int(body.get("interval_days") or 0),
                last_done=str(body.get("last_done") or ""),
                note=str(body.get("note") or "")))
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        snapshots.refresh_soon()
        _audit("maintenance saved", machine_uid,
               {"task": task.name, "kind": task.kind,
                "interval_days": task.interval_days})
        return jsonify({"ok": True, "task": task.to_dict()})

    @app.route("/api/maintenance/<uid>/complete", methods=["POST"])
    def api_complete_maintenance(uid):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        task = maint_store.get(uid)
        if task is None:
            return jsonify({"error": "No such task."}), 404
        when = str(body.get("when") or datetime.now().date().isoformat())
        note = str(body.get("note") or "")
        # Raises if refused; the schedule has NOT moved and the handler says
        # so, rather than the floor showing the task as done for a write that
        # never happened.
        maint_store.complete(uid, when, note)
        snapshots.refresh_soon()
        # The completion belongs in the machine's history too. Second statement,
        # no transaction — so if this one is refused the schedule HAS moved and
        # the history row is missing, and the operator is told exactly that
        # rather than being left to find the gap at audit time.
        gateway.sql(
            "CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
            "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
            "detail TEXT)")
        check_write(
            gateway.sql(
                "INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
                "test_name, value, detail) VALUES (?, ?, ?, '', '', '', ?)",
                [task.machine_uid, datetime.now().isoformat(), task.kind,
                 json.dumps({"task": task.name, "completed": when,
                             "note": note,
                             "by": session.get("user", "")})]),
            what=f"“{task.name}” was marked done and its schedule moved, but "
                 f"the completion did not reach the machine's history",
            partial=True, landed=["the schedule"],
            not_landed=["the history record"])
        return jsonify({"ok": True})

    # ── audit: who changed the configuration ──────────────────────────
    # Editing a QC spec, assigning targets, running a changeover or deleting a
    # machine used to leave no trace anywhere. These land in lem_machine_log as
    # kind='config' so the logs page can show them next to everything else.
    def _audit(action: str, machine_uid: str = "", detail=None) -> None:
        """Record a configuration change. Never raises: an audit failure must
        not fail the change the operator actually asked for."""
        # No CREATE TABLE here. It used to run on every audit — a second write
        # into a queue that serialises at about 1.5 ops/sec, for a table
        # SnapshotService.ensure_schema() has already declared once at startup.
        try:
            snapshots.ensure_schema()
            gateway.sql(
                "INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
                "test_name, value, detail) VALUES (?, ?, 'config', '', ?, '', ?)",
                [machine_uid, _now().isoformat(timespec="seconds"), action,
                 json.dumps({"action": action, "by": session.get("user", ""),
                             **(detail or {})})])
            # This is the only writer that can add a new `kind`, so this is the
            # only place the log's filter list can go stale.
            _page_drop("logkinds")
        except Exception:
            pass

    LOG_KINDS = ("run", "qc", "status_change", "override", "comment", "pm",
                 "calibration", "config")

    def _log_rows(args, failed=None) -> list:
        """Every filter the logs page offers, applied in SQL where possible.

        `failed` is an out-parameter: a read that times out is reported, not
        silently turned into an empty result.
        """
        where, params = [], []
        machine = (args.get("machine") or "").strip()
        if machine:
            where.append("machine_uid = ?")
            params.append(machine)
        kinds = [k.strip().lower() for k in (args.get("kind") or "").split(",")
                 if k.strip()]
        kinds = [k for k in kinds if k in LOG_KINDS]
        if kinds:
            where.append(f"kind IN ({','.join('?' for _ in kinds)})")
            params += kinds
        since = (args.get("since") or "").strip()
        if since:
            try:
                datetime.fromisoformat(since)
                where.append("ts >= ?")
                params.append(since)
            except ValueError:
                pass                      # a half-typed date shouldn't 500
        until = (args.get("until") or "").strip()
        if until:
            try:
                datetime.fromisoformat(until)
                # Inclusive: `until=2026-07-02` must include all of the 2nd.
                where.append("ts < ?")
                params.append(until if len(until) > 10
                              else until + "T99")
            except ValueError:
                pass
        limit = max(1, min(int(args.get("limit") or 500), 5000))
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        res = gateway.read_sql(
            "SELECT machine_uid, ts, kind, lab_id, test_name, value, detail "
            f"FROM lem_machine_log {clause} ORDER BY ts DESC LIMIT ?",
            params + [limit])
        if res.get("error"):
            # Reported, not swallowed: an unreadable log served as an empty one is
            # a confident wrong answer about a lab that has plenty of history.
            if failed is not None:
                failed["at"] = True
            return []
        return res.get("rows") or []

    def _log_entries(args, failed=None) -> list:
        titles = _titles()
        needle = (args.get("q") or "").strip().lower()
        out = []
        for row in _log_rows(args, failed=failed):
            raw_detail = row.get("detail") or ""
            try:
                detail = json.loads(raw_detail or "{}")
                if not isinstance(detail, dict):
                    detail = {}
            except (TypeError, ValueError):
                detail = {}
            uid = str(row.get("machine_uid") or "")
            entry = {
                "machine_uid": uid,
                "machine_title": titles.get(uid, uid),
                "ts": str(row.get("ts") or ""),
                "kind": str(row.get("kind") or ""),
                "lab_id": str(row.get("lab_id") or ""),
                "test_name": str(row.get("test_name") or ""),
                "value": str(row.get("value") or ""),
                "detail": detail,
                "action": str(detail.get("action") or row.get("test_name") or ""),
                "by": str(detail.get("by") or ""),
            }
            if needle:
                # Search the detail blob too — an override's comment and a
                # config change's specifics only live in there.
                hay = " ".join([entry["machine_title"], entry["lab_id"],
                                entry["test_name"], entry["value"],
                                entry["kind"], raw_detail]).lower()
                if needle not in hay:
                    continue
            out.append(entry)
        return out

    @app.route("/api/logs")
    def api_logs():
        # An unreadable log must not be served as an empty one. The queue bursts,
        # reads time out behind it, and "no events" is a confident wrong answer
        # about a lab that has plenty.
        failed = {"at": False}
        entries = _log_entries(request.args, failed=failed)

        def kinds_now():
            # A DISTINCT over the whole machine log, to fill a dropdown of about
            # six fixed words. On the live table that is the same shape of query
            # that once took eight seconds — and it was running per request.
            res = gateway.read_sql(
                "SELECT DISTINCT kind FROM lem_machine_log ORDER BY kind")
            if res.get("error"):
                return []
            return [str(r.get("kind")) for r in (res.get("rows") or [])
                    if r.get("kind")]

        out = {"events": entries, "kinds": _page("logkinds", kinds_now)}
        if failed["at"]:
            out["error"] = ("LabCore did not answer in time — its write queue is "
                            "busy. This list may be incomplete; try again shortly.")
        return jsonify(out)

    @app.route("/api/logs.csv")
    def api_logs_csv():
        rows = [[e["ts"], e["machine_title"], e["kind"], e["lab_id"],
                 e["test_name"], e["value"], e["by"],
                 json.dumps(e["detail"], separators=(",", ":"))]
                for e in _log_entries(request.args)]
        return _csv_response(
            rows, ["timestamp", "machine", "kind", "lab_id", "test", "value",
                   "by", "detail"], "lem_log.csv")

    @app.route("/logs")
    def logs_page():
        """Everything that has happened, searchable."""
        return render_template("logs.html", active="/logs")

    @app.route("/api/machines/<machine_uid>/maintenance-history")
    def api_maintenance_history(machine_uid):
        """Completed PM/CAL for one machine, newest first.

        The completions already live in lem_machine_log; showing only
        "last done" threw away who did it and what they found, which is the
        part an auditor actually asks about.
        """
        kind = (request.args.get("kind") or "").strip().lower()
        kinds = [kind] if kind in ("pm", "calibration") else ["pm",
                                                              "calibration"]
        placeholders = ",".join("?" for _ in kinds)
        res = gateway.read_sql(
            "SELECT ts, kind, detail FROM lem_machine_log "
            f"WHERE machine_uid = ? AND kind IN ({placeholders}) "
            "ORDER BY ts DESC LIMIT 500", [machine_uid] + kinds)
        history = []
        for row in ([] if res.get("error") else (res.get("rows") or [])):
            try:
                detail = json.loads(row.get("detail") or "{}")
                if not isinstance(detail, dict):
                    detail = {}
            except (TypeError, ValueError):
                detail = {}      # a hand-edited blob must not hide the entry
            history.append({
                "ts": str(row.get("ts") or ""),
                "kind": str(row.get("kind") or ""),
                "task": str(detail.get("task") or ""),
                "completed": str(detail.get("completed") or "")
                             or str(row.get("ts") or "")[:10],
                "note": str(detail.get("note") or ""),
                "by": str(detail.get("by") or ""),
            })
        return jsonify({"history": history})

    @app.route("/api/maintenance-history")
    def api_fleet_maintenance_history():
        """Everything completed anywhere, newest first — the other half of the
        lab-wide view: not just what's due, but what has actually been done."""
        kind = (request.args.get("kind") or "").strip().lower()
        kinds = [kind] if kind in ("pm", "calibration") else ["pm",
                                                              "calibration"]
        limit = max(1, min(int(request.args.get("limit") or 300), 2000))
        placeholders = ",".join("?" for _ in kinds)
        res = gateway.read_sql(
            "SELECT machine_uid, ts, kind, detail FROM lem_machine_log "
            f"WHERE kind IN ({placeholders}) ORDER BY ts DESC LIMIT ?",
            kinds + [limit])
        titles = _titles()
        history = []
        for row in ([] if res.get("error") else (res.get("rows") or [])):
            try:
                detail = json.loads(row.get("detail") or "{}")
                if not isinstance(detail, dict):
                    detail = {}
            except (TypeError, ValueError):
                detail = {}
            uid = str(row.get("machine_uid") or "")
            history.append({
                "machine_uid": uid,
                "machine_title": titles.get(uid, uid),
                "ts": str(row.get("ts") or ""),
                "kind": str(row.get("kind") or ""),
                "task": str(detail.get("task") or ""),
                "completed": str(detail.get("completed") or "")
                             or str(row.get("ts") or "")[:10],
                "note": str(detail.get("note") or ""),
                "by": str(detail.get("by") or ""),
            })
        history.sort(key=lambda e: e["completed"], reverse=True)
        return jsonify({"history": history})

    # ── importing historic PM/CAL from a spreadsheet ───────────────────
    from maintenance_import import (parse_import_csv, plan_import,
                                    template_csv_rows)

    @app.route("/api/maintenance-import/template.csv")
    def api_import_template():
        """A sheet pre-filled with every active machine — a typo'd equipment
        name is the one error this format can't recover from."""
        header, rows = template_csv_rows(state_reader.machines())
        return _csv_response(rows, header, "lem_maintenance_template.csv")

    def _existing_completions() -> set:
        res = gateway.read_sql(
            "SELECT machine_uid, detail FROM lem_machine_log "
            "WHERE kind IN ('pm','calibration')")
        out = set()
        for row in ([] if res.get("error") else (res.get("rows") or [])):
            try:
                detail = json.loads(row.get("detail") or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(detail, dict):
                continue
            out.add((str(row.get("machine_uid") or ""),
                     str(detail.get("task") or ""),
                     str(detail.get("completed") or "")))
        return out

    @app.route("/api/maintenance-import", methods=["POST"])
    def api_import_maintenance():
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        text = body.get("csv")
        if not isinstance(text, str) or not text.strip():
            return jsonify({"error": "No CSV supplied."}), 400
        rows, errors = parse_import_csv(text)
        plan = plan_import(rows, state_reader.machines(),
                           _existing_completions(), maint_store.all())
        dry = (request.args.get("dry_run") or "").strip() in ("1", "true",
                                                             "yes")
        payload = {
            "created": 0, "create_count": len(plan["create"]),
            "skipped": len(plan["skipped"]),
            "unmatched": plan["unmatched"], "errors": errors,
            "reschedule": plan["reschedule"], "dry_run": dry,
            "preview": plan["create"][:50],
        }
        if dry:
            return jsonify(payload)

        gateway.sql(
            "CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
            "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
            "detail TEXT)")
        # `made += 1` used to sit unconditionally under this write. That is the
        # notes.md failure verbatim — a bulk import that "reported 'imported
        # 3094' while nothing landed", because the loop counted LabCore's
        # refusals as successes — reproduced in a second importer after the
        # checklist one was fixed. A count is a promise about what is in
        # LabCore, and here it is the only thing the operator is told.
        made = 0
        refused = 0
        stopped = None
        for entry in plan["create"]:
            if stopped is not None:
                # Not attempted. Counted as refused rather than as created,
                # because the queue that turned the last one away will turn
                # these away too and hammering it is the load the refusal asked
                # to be spared. They are still in the CSV; re-running the import
                # is idempotent and will pick them up.
                refused += 1
                continue
            # Stamp the event at the completion date, not now, so the history
            # sorts and charts as the work actually happened.
            result = gateway.sql(
                "INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
                "test_name, value, detail) VALUES (?, ?, ?, '', '', '', ?)",
                [entry["machine_uid"], entry["completed"] + "T00:00:00",
                 entry["kind"],
                 json.dumps({"task": entry["task"],
                             "completed": entry["completed"],
                             "note": entry["note"],
                             "by": entry["by"] or session.get("user", ""),
                             "imported": True})])
            if refusal_reason(result):
                stopped = result
                refused += 1
                continue
            made += 1
        rescheduled = 0
        for move in plan["reschedule"]:
            if stopped is not None:
                break
            task = maint_store.get(move["uid"])
            if task is None:
                continue
            try:
                maint_store.complete(move["uid"], move["last_done"], task.note)
            except LabCoreRefused as exc:
                # Caught rather than allowed to reach the error handler: the
                # payload below carries how many rows DID land, and losing that
                # to a bare 503 would leave the operator with no idea whether to
                # re-run the file.
                stopped = exc.result
                break
            rescheduled += 1
        payload["created"] = made
        payload["refused"] = refused
        payload["rescheduled"] = rescheduled
        _audit("maintenance history imported", "",
               {"created": made, "refused": refused,
                "skipped": payload["skipped"],
                "unmatched": len(plan["unmatched"]), "errors": len(errors),
                "rescheduled": rescheduled})
        if stopped is not None:
            return refusal_response(LabCoreRefused(
                stopped,
                what=f"{made} of {len(plan['create'])} completion(s) were "
                     f"imported before LabCore stopped accepting writes — "
                     f"re-run the same file to bring the rest in",
                partial=made > 0, **payload))
        return jsonify(payload)

    @app.route("/api/maintenance")
    def api_all_maintenance():
        """Every machine's PM/CAL in one list — "what is overdue anywhere",
        which no per-machine dialog can answer."""
        from snapshot_service import maintenance_from_tables, titles_from_tables
        tables = _snapshot_tables()
        if tables is None:                 # first request, or LabCore down at boot
            titles, tables = _titles(), {}
        else:
            titles = titles_from_tables(tables)
        today = _now().date()
        tasks = []
        for uid, records in maintenance_from_tables(tables).items():
            for task in records:
                # today, not date.today(): the interval status is judged per
                # request, so a task falling due overnight is red in the morning.
                row = task.to_dict(today)
                row["machine_title"] = titles.get(uid, uid)
                tasks.append(row)
        tasks.sort(key=lambda t: (t["status"] != "RED", t["status"] != "YELLOW",
                                 t["machine_title"], t["name"]))
        due = sum(1 for t in tasks if t["status"] in ("RED", "YELLOW"))
        return jsonify({"tasks": tasks, "due_count": due})

    @app.route("/api/maintenance/<uid>", methods=["DELETE"])
    def api_delete_maintenance(uid):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        maint_store.delete(uid)
        snapshots.refresh_soon()
        return jsonify({"ok": True})

    # ── QC trends and exports ─────────────────────────────────────────
    # Both read the machine log the modules already write: a control chart
    # per test, and a file an auditor can keep.

    def _qc_events(machine_uid=None, limit=4000):
        where = "WHERE kind = 'qc'"
        args = []
        if machine_uid:
            where += " AND machine_uid = ?"
            args.append(machine_uid)
        res = gateway.read_sql(
            "SELECT machine_uid, ts, lab_id, test_name, value, detail "
            f"FROM lem_machine_log {where} ORDER BY ts ASC LIMIT ?",
            args + [int(limit)])
        return [] if res.get("error") else (res.get("rows") or [])

    @app.route("/api/machines/<machine_uid>/qc-trend")
    def api_qc_trend(machine_uid):
        """One series per test: the last results with the pass band, so the
        floor can draw a control chart rather than just a colour."""
        series = {}
        for row in _qc_events(machine_uid):
            name = str(row.get("test_name") or "").strip()
            if not name:
                continue
            try:
                detail = json.loads(row.get("detail") or "{}")
            except (TypeError, ValueError):
                detail = {}
            try:
                value = float(row.get("value"))
            except (TypeError, ValueError):
                continue
            s = series.setdefault(name, {
                "test_name": name, "points": [], "failures": 0,
                "low": detail.get("low"), "high": detail.get("high"),
                "expected": detail.get("expected"),
                "sample_id": row.get("lab_id") or ""})
            in_spec = bool(detail.get("in_spec"))
            s["points"].append({"ts": row.get("ts"), "value": value,
                                "in_spec": in_spec})
            if not in_spec:
                s["failures"] += 1
            for k in ("low", "high", "expected"):      # keep the latest band
                if detail.get(k) is not None:
                    s[k] = detail[k]
        out = []
        for s in series.values():
            s["points"] = s["points"][-60:]
            s["runs"] = len(s["points"])
            out.append(s)
        return jsonify({"series": sorted(out, key=lambda s: s["test_name"])})

    def _csv_response(rows, header, filename):
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(header)
        writer.writerows(rows)
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition":
                                 f'attachment; filename="{filename}"'})

    def _verdict(detail):
        if "in_spec" not in detail:
            return ""
        return "PASS" if detail.get("in_spec") else "FAIL"


    # ── correction factors ────────────────────────────────────────────
    # `corrected = raw + correction`, per machine per test, applied by the module
    # at the one point a parsed value becomes a verdict.
    #
    # Worth knowing what this replaces: V4 could store and log a correction and
    # then judged every reading without it. This one changes pass/fail, so every
    # change is audited with who/from/to, and the raw value travels with the
    # verdict into the log and the export.
    CORRECTIONS_DDL = (
        "CREATE TABLE IF NOT EXISTS lem_correction_factors ("
        "machine_uid TEXT NOT NULL, test_name TEXT NOT NULL, "
        "correction REAL NOT NULL DEFAULT 0.0, units TEXT, "
        "updated_at TEXT, updated_by TEXT, "
        "PRIMARY KEY (machine_uid, test_name))")

    def _corrections(machine_uid: str) -> dict:
        gateway.sql(CORRECTIONS_DDL)
        res = gateway.read_sql(
            "SELECT test_name, correction, units FROM lem_correction_factors "
            "WHERE machine_uid = ? ORDER BY test_name", [machine_uid])
        if res.get("error"):
            return {}
        out = {}
        for r in res.get("rows") or []:
            name = str(r.get("test_name") or "").strip()
            if not name:
                continue
            try:
                out[name] = {"test_name": name,
                             "correction": float(r.get("correction") or 0.0),
                             "units": str(r.get("units") or "")}
            except (TypeError, ValueError):
                continue
        return out

    def _reported_methods(machine_uid: str, saved) -> list:
        """Every method a correction could apply to on this bench.

        The mapped methods come out of `lem_machine_config`, which the module
        already publishes in full — no extra table and no extra write. Plus
        anything that already carries a correction, even if it is no longer mapped,
        or a stale factor could never be found and removed.

        This matters because QC is assignment-only: most reported methods have no
        spec, and those are exactly the customer results a correction has to reach
        (ISO/IEC 17025 §7.8.2).
        """
        names = set(saved or ())
        record = None
        try:
            record = config_store.get(machine_uid)
        except Exception:
            record = None
        raw = (record or {}).get("config")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError):
                raw = None
        if isinstance(raw, dict):
            for mapping in raw.get("mappings") or []:
                if not isinstance(mapping, dict):
                    continue
                for method in mapping.get("methods") or []:
                    if str(method).strip():
                        names.add(str(method).strip())
            for spec in raw.get("tests") or []:
                if isinstance(spec, dict) and str(spec.get("name") or "").strip():
                    names.add(str(spec["name"]).strip())
        return sorted(names)

    @app.route("/api/machines/<machine_uid>/corrections")
    def api_get_corrections(machine_uid):
        saved = _corrections(machine_uid)
        return jsonify({"corrections": list(saved.values()),
                        "methods": _reported_methods(machine_uid, saved)})

    @app.route("/api/machines/<machine_uid>/corrections", methods=["POST"])
    def api_save_correction(machine_uid):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        if machine_uid not in _titles():
            return jsonify({"error": "No such instrument."}), 404
        body = request.get_json(silent=True) or {}
        test_name = str(body.get("test_name") or "").strip()
        if not test_name:
            return jsonify({"error": "Which test?"}), 400
        raw = body.get("correction")
        try:
            correction = float(normalise_number_text(raw))
        except (TypeError, ValueError):
            # Refused rather than coerced: a correction is added to every reading
            # this bench produces, and "a bit" would silently become 0.0.
            return jsonify({"error": f"{raw!r} is not a number."}), 400
        existing = _corrections(machine_uid).get(test_name)
        previous = existing["correction"] if existing else 0.0
        # THE write this whole guard exists for. `corrected = raw + correction`
        # is applied to EVERY measurement this bench takes — before the QC
        # verdict, before the LabCore write, before anything is displayed — so a
        # save that silently did not land leaves the lab reporting uncorrected
        # results while the supervisor who set the offset believes it is in
        # force. ISO/IEC 17025 §7.8.2: a reported result must be the measurement
        # result. This used to capture the answer, use it only to decide whether
        # to leave a note, and reply `200 {"ok": true}` either way.
        check_write(
            gateway.sql(
                "INSERT INTO lem_correction_factors (machine_uid, test_name, "
                "correction, units, updated_at, updated_by) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(machine_uid, test_name) DO UPDATE SET "
                "correction=excluded.correction, units=excluded.units, "
                "updated_at=excluded.updated_at, "
                "updated_by=excluded.updated_by",
                [machine_uid, test_name, correction,
                 str(body.get("units") or ""),
                 _now().isoformat(timespec="seconds"),
                 session.get("user", "")]),
            what=f"the correction for “{test_name}” was NOT saved and this "
                 f"instrument is still applying the previous one")
        # Everything below is reached only by a write that landed, which is what
        # makes all three of these honest:
        #
        # The NOTE, because the bench re-reads `lem_correction_factors` when it
        # sees one — a note for a write that failed buys a LabCore read that
        # finds the OLD value, on the very queue that has just said it is too
        # deep. This machine and no other: a correction is per machine per test,
        # and a broad mark would put the whole lab through a read for one bench.
        #
        # The AUDIT, because it records who changed the factor from what to
        # what, and a change that did not happen written into the one log an
        # assessor reads is worse than no log at all.
        #
        # The SNAPSHOT refresh, because there is nothing new to pick up.
        app.config["LIVE"].mark_stale(machine_uid, STALE_CORRECTIONS)
        _audit("correction factor set", machine_uid,
               {"test": test_name, "previous": previous, "new": correction})
        snapshots.refresh_soon()
        return jsonify({"ok": True, "test_name": test_name,
                        "correction": correction})

    @app.route("/api/machines/<machine_uid>/corrections/<test_name>",
               methods=["DELETE"])
    def api_delete_correction(machine_uid, test_name):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        existing = _corrections(machine_uid).get(test_name)
        if existing is None:
            return jsonify({"error": f"No correction for “{test_name}”."}), 404
        # Removing an offset changes every future reading exactly as setting
        # one does. A removal reported as done that did not happen leaves the
        # bench quietly still applying it, and the editor showing that it does
        # not.
        check_write(
            gateway.sql("DELETE FROM lem_correction_factors "
                        "WHERE machine_uid = ? AND test_name = ?",
                        [machine_uid, test_name]),
            what=f"the correction for “{test_name}” was NOT removed and this "
                 f"instrument is still applying it")
        # The bench must re-read: from its point of view an offset going away is
        # the same event as one arriving. Gated the same way, and for the same
        # reason — see the save above.
        app.config["LIVE"].mark_stale(machine_uid, STALE_CORRECTIONS)
        _audit("correction factor removed", machine_uid,
               {"test": test_name, "previous": existing["correction"],
                "new": 0.0})
        snapshots.refresh_soon()
        return jsonify({"ok": True, "deleted": test_name})

    @app.route("/api/machines/<machine_uid>/export.csv")
    def api_export_machine(machine_uid):
        kind = request.args.get("kind")
        where = "WHERE machine_uid = ?"
        args = [machine_uid]
        if kind:
            where += " AND kind = ?"
            args.append(kind)
        res = gateway.read_sql(
            "SELECT ts, kind, lab_id, test_name, value, detail FROM "
            f"lem_machine_log {where} ORDER BY ts ASC LIMIT 20000", args)
        rows = [] if res.get("error") else (res.get("rows") or [])
        out = []
        for r in rows:
            try:
                detail = json.loads(r.get("detail") or "{}")
            except (TypeError, ValueError):
                detail = {}
            out.append([r.get("ts"), r.get("kind"), r.get("lab_id"),
                        r.get("test_name"), r.get("value"), _verdict(detail),
                        detail.get("raw_value", ""),
                        detail.get("correction", ""),
                        json.dumps(detail, separators=(",", ":"))])
        title = machine_uid
        info = gateway.read_sql(
            "SELECT title FROM lem_machine_status WHERE machine_uid = ?",
            [machine_uid])
        if not info.get("error") and info.get("rows"):
            title = str(info["rows"][0].get("title") or machine_uid)
        safe = re.sub(r'[^A-Za-z0-9 ._-]', "_", title).strip() or machine_uid
        # raw_value/correction blank when none was applied — a 0 would be a claim
        # that a correction of zero was deliberately in force.
        return _csv_response(out, ["timestamp", "kind", "lab_id", "test",
                                   "value", "in_spec", "raw_value",
                                   "correction", "detail"],
                             f"{safe} history.csv")

    @app.route("/api/export/qc.csv")
    def api_export_qc():
        """Every QC check across the lab — the file an assessor asks for."""
        titles = _titles()
        out = []
        for r in _qc_events(limit=20000):
            try:
                detail = json.loads(r.get("detail") or "{}")
            except (TypeError, ValueError):
                detail = {}
            uid = r.get("machine_uid")
            out.append([uid, titles.get(uid, ""), r.get("ts"), "qc",
                        r.get("lab_id"), r.get("test_name"), r.get("value"),
                        detail.get("expected"), detail.get("low"),
                        detail.get("high"), _verdict(detail),
                        detail.get("raw_value", ""),
                        detail.get("correction", "")])
        return _csv_response(out, ["machine_uid", "machine", "timestamp",
                                   "kind", "sample_id", "test", "value",
                                   "expected", "low", "high", "in_spec",
                                   "raw_value", "correction"],
                             "LEM QC history.csv")

    @app.route("/api/machines/<machine_uid>/events")
    def api_machine_events(machine_uid):
        limit = request.args.get("limit", default=100, type=int)
        return jsonify({"events": state_reader.events(machine_uid, limit)})

    @app.route("/api/events")
    def api_recent_events():
        """The floor's run blips — polled every six seconds by every open screen.

        Served from the snapshot, which carries the newest EVENT_LIMIT entries.
        Ask for more than that and it reads live: a deep request is the log
        viewer's, and silently handing back a truncated list would look like a
        quiet lab.
        """
        limit = request.args.get("limit", default=50, type=int)
        from live_presence import live_events, merge_events
        from snapshot_service import EVENT_LIMIT, events_from_tables
        tables = _snapshot_tables() if limit <= EVENT_LIMIT else None
        recorded = (state_reader.recent_events(limit) if tables is None
                    else events_from_tables(tables, limit))
        # A run blips when the bench says so, not when its log row has cleared
        # the write queue. Deduplicated on the key the floor itself dedupes on,
        # so the same run coming through both roads is one blip.
        return jsonify({"events": merge_events(live_events(app.config["LIVE"]),
                                               recorded, limit)})

    @app.route("/api/machines/<machine_uid>/override", methods=["POST"])
    def api_machine_override(machine_uid):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        comment = str(body.get("comment") or "").strip()
        if not comment:
            return jsonify({"error": "A comment is required to override or "
                                     "clear a machine."}), 400
        try:
            state_reader.set_override(machine_uid,
                                      str(body.get("override") or ""), comment)
        except ValueError as exc:
            # A state nobody defined, so `lem_machine_control` is untouched.
            # Marking here would order a read of a row that did not change.
            return jsonify({"error": str(exc)}), 400
        # Clearing an override marks exactly as setting one does: "back in
        # service" is as urgent as "out of service", and a bench left on
        # SERVICE because only one direction was marked is an instrument nobody
        # can use until its backstop poll comes round.
        app.config["LIVE"].mark_stale(machine_uid, STALE_OVERRIDE)
        snapshots.refresh_soon()
        return jsonify({"ok": True})

    @app.route("/api/qc-specs")
    def api_list_qc_specs():
        machine_uid = request.args.get("machine_uid")
        specs = []
        for spec in spec_store.list_specs(machine_uid):
            low, high = spec.limits()
            payload = spec.to_dict()
            payload["low"] = low
            payload["high"] = high
            specs.append(payload)
        return jsonify({"specs": specs})

    @app.route("/api/qc-specs", methods=["POST"])
    def api_save_qc_spec():
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        try:
            spec = QcSpec(
                machine_uid=str(body.get("machine_uid") or "").strip(),
                test_name=str(body.get("test_name") or "").strip(),
                sample_id=str(body.get("sample_id") or "").strip(),
                expected=float(body.get("expected")),
                std_dev=float(body.get("std_dev")),
                k=float(body.get("k") or 2.0),
                units=str(body.get("units") or ""),
            )
            spec_store.save(spec)
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        snapshots.refresh_soon()
        _audit("qc-spec saved", spec.machine_uid,
               {"test": spec.test_name, "expected": spec.expected,
                "sample_id": spec.sample_id})
        return jsonify({"ok": True, "spec": spec.to_dict()})

    @app.route("/api/qc-specs", methods=["DELETE"])
    def api_delete_qc_spec():
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        spec_store.delete(str(body.get("machine_uid") or ""),
                          str(body.get("test_name") or ""))
        snapshots.refresh_soon()
        _audit("qc-spec deleted", str(body.get("machine_uid") or ""),
               {"test": str(body.get("test_name") or "")})
        return jsonify({"ok": True})

    # ── QC samples: the V4 model, central and shared ──────────────────
    from qc_samples import QcSample, QcSampleStore

    sample_store = QcSampleStore(gateway)

    @app.route("/api/qc-samples")
    def api_list_qc_samples():
        payload = []
        for sample in sample_store.list_samples():
            data = sample.to_dict()
            for test, spec in zip(data["tests"], sample.tests):
                low, high = spec.limits()
                test["low"] = low
                test["high"] = high
            payload.append(data)
        return jsonify({"samples": payload})

    @app.route("/api/qc-samples", methods=["POST"])
    def api_save_qc_sample():
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        try:
            sample = QcSample.from_dict(body)
            sample_store.save(sample)
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        _audit("qc-sample saved", "",
               {"standard": sample.name, "lab_id": sample.sample_id_val,
                "tests": len(sample.tests or [])})
        return jsonify({"ok": True})

    @app.route("/api/qc-samples/changeover", methods=["POST"])
    def api_changeover():
        """New lot of a standard: inherit its specs and move every machine
        that was checked against the old lot."""
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        from qc_samples import changeover
        try:
            moved = changeover(gateway,
                               str(body.get("old_name") or ""),
                               str(body.get("new_name") or ""),
                               str(body.get("new_id_val") or ""),
                               retire_old=bool(body.get("retire_old")))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True, "moved": moved})

    @app.route("/api/qc-samples", methods=["DELETE"])
    def api_delete_qc_sample():
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        sample_store.delete(str(body.get("name") or ""))
        return jsonify({"ok": True})

    # The picker asks on every open and the catalogue barely changes, so hold
    # it. A BLANK answer is never cached — that would keep the picker empty
    # until restart, which is the exact failure this endpoint had.
    _test_name_cache: dict = {}

    @app.route("/api/test-names")
    def api_test_names():
        """LabCore's test methods — the only allowed QC test names."""
        cached = _test_name_cache.get("names")
        if cached:
            return jsonify({"tests": cached, "cached": True})
        names = gateway.get_test_names()
        if names is None:
            # Couldn't ask LabCore. The DISTINCT scan is the safety net, and it
            # needs a generous timeout: it reads every result row in the lab.
            res = gateway.read_sql(
                "SELECT DISTINCT test_name FROM sample_tests "
                "WHERE test_name IS NOT NULL AND TRIM(test_name) != '' "
                "ORDER BY test_name", timeout=60)
            names = [r.get("test_name") for r in (res.get("rows") or [])]
        if names:
            _test_name_cache["names"] = names
        return jsonify({"tests": names or []})

    def _warm() -> None:
        """Fill the caches before anybody asks, on a background thread.

        Measured on the live system, the first visitor to the checklist page
        waited 7.5 seconds — nothing wrong with the cache, it simply had nobody to
        warm it, so the cost landed on a person instead of a thread.

        Never raises. This runs where an exception would be invisible, and a
        server that dies warming a cache is worse than a slow first page.
        """
        for label, job in (("floor", lambda: snapshots.get()),
                           ("checklists", lambda: _page(
                               f"checklists:{_today()}", _build_checklist_day)),
                           ("archive", lambda: _page(
                               "checklisthistory",
                               lambda: {"days": checklist_store.history()}))):
            try:
                job()
            except Exception as exc:
                logger.warning("LEM warm-up skipped %s: %s", label, exc)

    app.config["WARM"] = _warm
    app.config["PAGE"] = _page          # exercised directly by the cache tests
    return app
