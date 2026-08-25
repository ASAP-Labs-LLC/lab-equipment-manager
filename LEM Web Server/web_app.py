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
import logging
import os
import threading
import re
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, session, url_for)

from data_source import build_sample_index, evaluate_box
from db_config_store import DbConfigStore
from labcore_result import (LabCoreError, LabCoreRefused, LabCoreUnavailable,
                            confirm_write, is_missing_table,
                            rows as labcore_rows)
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

# One logger for the whole app. It existed only as a NAME until now: `_warm`
# already called `logger.warning(...)` in its "never raises" handler, so the
# first time a store actually raised in a warm-up job the background thread died
# with NameError instead of logging a skipped cache. The stores were swallowing
# every LabCore failure, which is why that branch had never once run.
logger = logging.getLogger(__name__)

STATUS_COLORS = {
    STATUS_GREEN: "#21c071",
    STATUS_YELLOW: "#f5c542",
    STATUS_RED: "#f85b5b",
    STATUS_DEAD: "#0f172a",
    STATUS_SERVICE: "#8d99ae",
    STATUS_UNKNOWN: "#718096",
}


APP_DIR = os.path.dirname(os.path.abspath(__file__))


def read_version(directory=None) -> str:
    """The release tag from a VERSION file, or ``"dev"`` for a checkout.

    CI writes VERSION into the release, so the stamp travels with the code
    rather than with the data. Never raises: /healthz reporting "dev" is a
    nuisance, /healthz returning 500 makes a working release look broken and
    triggers a rollback that was never needed.
    """
    base = APP_DIR if directory is None else str(directory)
    try:
        with open(os.path.join(base, "VERSION"), encoding="utf-8") as fh:
            stamp = fh.read().strip()
    except (OSError, UnicodeDecodeError):
        return "dev"
    return stamp.splitlines()[0].strip() if stamp else "dev"


APP_VERSION = read_version()

# ── activity, for unattended deploys ────────────────────────────────────────
# "Is anyone using LEM?" is not "has LEM had a request?". LEM is a wall
# display: the floor polls the machine list and the blip endpoints every 2
# seconds from every open browser, and every bench POSTs /api/live on each
# module poll. Counting those would make LEM permanently busy and an
# idle-gated deploy could never fire.
#
# So activity means a person: any write, and any read that is not a background
# poll, a bench push, a health check or a static asset.
_last_activity = time.time()
# What last counted as a person. Reported by /healthz purely so "why does this
# app never look idle?" is answerable without adding request logging to a .pyw
# that has no console. Getting the exclusion list wrong is otherwise silent -
# unattended deploys simply never fire and nothing says why.
_last_activity_path = "(none since boot)"

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _is_background(path: str, method: str) -> bool:
    """Whether a request is machinery rather than a person.

    **Reads are background; writes are people.**

    This started as a list of the endpoints the floor polls, and that list was
    wrong twice in a row — first missing ``/api/me`` and ``/api/map``, then
    ``/api/qc-samples``, each time pinning idle time under a second so an
    unattended deploy could never fire. The failure is silent, and any new
    poller added to floor.html would reintroduce it.

    So the rule is inverted. ``floor.html`` re-reads its whole world every two
    seconds from every open browser, which makes *any* GET indistinguishable
    from a wall display; enumerating them is a losing game. What actually
    deserves protection from a restart is someone **writing** — an edit, a
    checklist tick, a correction factor. LEM holds no per-request state, so a
    reader loses at most the ~10s the floor takes to repoll.

    ``/api/live`` is excluded even though it is a POST: that is a bench module
    pushing liveness, not a person.
    """
    if method in _WRITE_METHODS:
        return path == "/api/live"
    return True


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


# ── how this app answers "LabCore did not do that" ──────────────────────────
#
# LabCore's write queue serialises at ~1.5 writes a second and refuses past 100
# pending BY ANSWERING — no exception, no "error" key. Every store now raises
# instead of reading that answer as success, which means every route below has
# to decide what the operator is told. Two rules, and they are the whole point:
#
#   1. A refusal is never a 200. A dialog that closes on "Saved" after the band
#      was dropped is the failure this branch exists to remove.
#   2. A refusal is never a bare 500 either. "Internal Server Error" tells the
#      person nothing about whether to press the button again, and pressing the
#      button again is exactly the right move — the queue drains in seconds.
#
# So the two kinds are told apart on the wire as well as in the message:
#
#   502 + labcore="refused"      LabCore answered and said no. The record is
#                                DEFINITELY unchanged; press Save again.
#   503 + labcore="unavailable"  LabCore could not be asked at all. Nothing is
#                                known — which is NOT the same as nothing
#                                happened, and the wording never claims it is.
#
# `retry` is true on both because both clear on their own; what differs is what
# the operator can conclude about the record, and that is carried by `labcore`,
# by the status code and by the message. A single flag cannot say two things.

_REFUSED_HINT = ("LabCore's write queue refused it, so nothing changed. "
                 "It clears in a few seconds — try again.")
_UNAVAILABLE_HINT = ("LabCore could not be reached, so this did not go through "
                     "and its state is unknown. Try again in a moment.")


def _labcore_failed(exc, what: str, note: str = ""):
    """The answer for a write LabCore did not acknowledge. JSON + status.

    `what` names the thing in the operator's words — "the QC band", "the map
    lock" — because a queue refusal carries no hint of what was being written.
    `note` is for the routes whose write is not atomic: a QC assignment or a
    machine deletion can be refused half-way, and "some of this did not save"
    is a different instruction from "press Save again".

    `detail` always carries the store's own message verbatim. The stores were
    written to say which drag or which assignment was lost, and that text must
    reach the page rather than stop at the log.
    """
    refused = isinstance(exc, LabCoreRefused)
    hint = _REFUSED_HINT if refused else _UNAVAILABLE_HINT
    message = "{0} was NOT saved. {1}".format(what[:1].upper() + what[1:], hint)
    if note:
        message += " " + note
    return jsonify({
        "error": message,
        "detail": str(exc),
        "saved": False,
        "retry": True,
        "labcore": "refused" if refused else "unavailable",
    }), (502 if refused else 503)


def _labcore_unreadable(exc, what: str):
    """The answer for a read LabCore could not give. JSON + status.

    Deliberately never 200-with-an-empty-list. "No QC assigned", "no history",
    "no configurations" are all answers an operator acts on, and inventing them
    out of a timed-out read is the sibling of the write bug above.
    """
    refused = isinstance(exc, LabCoreRefused)
    return jsonify({
        "error": "{0} could not be read — LabCore did not answer. This is not "
                 "an empty result; try again in a moment.".format(
                     what[:1].upper() + what[1:]),
        "detail": str(exc),
        "retry": True,
        "labcore": "refused" if refused else "unavailable",
    }), (502 if refused else 503)


def create_app(gateway, admin_password: Optional[str] = None,
               secret: Optional[str] = None, authenticator=None,
               live=None, live_token: Optional[str] = None) -> Flask:
    app = Flask(__name__)

    # The live road: what benches say about themselves, in memory only. The
    # record stays LabCore's. Publishing the address/token to `lem_meta` is a
    # BOOT step (see web_server.pyw) — a factory with side effects would give
    # every test a LabCore write, which is the lesson the snapshot poller taught.
    from live_presence import LivePresence, resolve_token
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

    from machine_map import (MachineLayoutStore, MachineMapError,
                             MapSettingsStore, QcTargetStore, WatchedTarget)
    from qc_specs import MachineStateReader, QcSpec, QcSpecStore

    spec_store = QcSpecStore(gateway)
    state_reader = MachineStateReader(gateway)
    layout_store = MachineLayoutStore(gateway)
    target_store = QcTargetStore(gateway)
    map_settings = MapSettingsStore(gateway)

    @app.route("/api/map")
    def api_map():
        """Is the floor frozen? Answered fail-safe when it cannot be read.

        The ONE route on this branch that deliberately still answers 200 on a
        LabCore failure, and the reason is what it is for: every open floor
        screen polls this every two seconds, and the answer decides whether the
        page offers drag handles. A 503 here would put an error banner on every
        wall display in the lab for a blip that changes nothing they can see.

        So it degrades — but towards LOCKED, never towards unlocked. Unlocked is
        an invitation to rearrange the floor, and every one of those drags would
        be refused by the very LabCore that just failed to answer, so the
        operator would drag ten instruments and keep none. `known: false` says
        the lock state is a fallback rather than a reading, and the page shows
        it; the POST below never degrades, because that one is a write.
        """
        try:
            return jsonify({"locked": map_settings.locked(), "known": True})
        except LabCoreError as exc:
            logger.warning("map lock unreadable, defaulting to locked: %s", exc)
            return jsonify({
                "locked": True, "known": False,
                "labcore": ("refused" if isinstance(exc, LabCoreRefused)
                            else "unavailable"),
                "error": "LabCore did not answer, so the floor is held locked "
                         "until it does — a drag saved now would be dropped."})

    @app.route("/api/map", methods=["POST"])
    def api_set_map():
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        wanted = bool(body.get("locked"))
        try:
            map_settings.set_locked(wanted)
            # Read back rather than echoing `wanted`: the answer the floor
            # paints its lock button from has to be what LabCore holds.
            return jsonify({"ok": True, "locked": map_settings.locked()})
        except LabCoreError as exc:
            # Never {"ok": true} here. A lock that never took leaves the floor
            # draggable for everyone while the button says it is frozen.
            return _labcore_failed(exc, "the map lock")

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

    @app.before_request
    def _track_activity():
        global _last_activity, _last_activity_path
        if not _is_background(request.path, request.method):
            _last_activity = time.time()
            _last_activity_path = f"{request.method} {request.path}"

    @app.route("/healthz")
    def healthz():
        """Deployment health check — no auth, no LabCore call.

        The updater starts a candidate release on a scratch port and asks this
        before the release is live, so it must answer with no session and
        without the admin password.

        ``labcore`` is read straight off SnapshotService, which already tracks
        reachability as a side effect of its own background reads. Probing here
        would add an op per health check and, worse, would let a momentary
        LabCore blip fail a release that was never broken — this whole server
        exists to keep LabCore load independent of how many things are looking.
        """
        online = getattr(snapshots, "_online", None)
        return jsonify({
            "status": "ok",
            "version": APP_VERSION,
            "labcore": "unknown" if online is None else (
                "reachable" if online else "unreachable"),
            "pid": os.getpid(),
            # Seconds since a person last did something. Wall displays polling
            # and benches pushing do not count - see _is_background.
            "idle_seconds": round(time.time() - _last_activity, 1),
            "last_activity": _last_activity_path,
            # LEM has no per-user sessions the way COA does; the floor is
            # anonymous. Reported for a uniform shape across both apps.
            "active_sessions": 0,
        })

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


    from machine_configs import (ConfigReadUnavailable, MachineConfigError,
                                 MachineConfigStore)

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
            # `degrade_to_default=True` exists for exactly this: a path that
            # only DRAWS the answer. Nothing is decided from it, the floor
            # shows Mon–Fri until LabCore answers, and the alternative is a 503
            # on the page a lab leaves open all day.
            return jsonify(
                schedule_store.load(degrade_to_default=True).to_dict(_now()))
        return jsonify(schedule_from_tables(tables).to_dict(_now()))

    @app.route("/api/schedule", methods=["POST"])
    def api_save_schedule():
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        try:
            # NOT degrade_to_default: this read fills in every field the
            # operator did not type. Degraded, it would silently reset the
            # working days to Mon–Fri and post the form back with every holiday
            # deleted — a write built out of a failed read.
            current = schedule_store.load()
            saved = schedule_store.save(LabSchedule(
                working_days=body.get("working_days", current.working_days),
                opens=str(body.get("opens", current.opens) or ""),
                closes=str(body.get("closes", current.closes) or ""),
                holidays=body.get("holidays", current.holidays) or {}))
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        except LabCoreError as exc:
            return _labcore_failed(exc, "the lab's opening hours")
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
        except LabCoreError as exc:
            # A dropped holiday means the lab reads as OPEN on a day it is shut,
            # and silence on a closed day is what "stopped" is judged against.
            return _labcore_failed(exc, "that holiday")
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
        except LabCoreError as exc:
            return _labcore_failed(exc, "removing that holiday")
        snapshots.refresh_soon()
        return jsonify({"ok": True})

    # ── checklists: opening and closing rounds ─────────────────────────
    from checklists import (Checklist, ChecklistStore, ChecklistWriteError,
                           active_items, completion)

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
        try:
            return jsonify(_page(f"checklists:{day}",
                                 lambda: _build_checklist_day(day, when)))
        except LabCoreError as exc:
            # An empty round tells the lab there is nothing to do this morning.
            # `_page` caches what `produce()` RETURNS, so a raise stores
            # nothing — the next request retries rather than serving a
            # remembered blank day for the life of the process.
            return _labcore_unreadable(exc, "today's round")

    @app.route("/api/checklists", methods=["POST"])
    def api_save_checklist():
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        try:
            saved = checklist_store.save(Checklist.from_dict(body))
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        except LabCoreError as exc:
            # NOT folded into the 400 above: a refused queue is not an invalid
            # checklist, and telling the operator to fix a round that is
            # perfectly valid sends them looking in the wrong place.
            return _labcore_failed(exc, "that checklist")
        _page_drop("checklists:")      # the definition changed, so every day did
        _audit("checklist saved", "",
               {"checklist": saved.name, "items": len(saved.items)})
        return jsonify({"ok": True, "checklist": saved.to_dict()})

    @app.route("/api/checklists/<uid>", methods=["DELETE"])
    def api_delete_checklist(uid):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        try:
            existing = checklist_store.get(uid)
            checklist_store.delete(uid)
        except LabCoreUnavailable as exc:
            return _labcore_unreadable(exc, "that checklist")
        except LabCoreError as exc:
            return _labcore_failed(exc, "deleting that checklist")
        _page_drop("checklists:")
        _audit("checklist deleted", "",
               {"checklist": (existing.name if existing else uid)})
        return jsonify({"ok": True})

    @app.route("/api/checklists/<uid>/toggle", methods=["POST"])
    def api_toggle_checklist(uid):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        try:
            checklist = checklist_store.get(uid)
        except LabCoreError as exc:
            # Explicitly not the 404 below. "Could not ask" served as "no such
            # checklist" is how a round that exists becomes a missing one.
            return _labcore_unreadable(exc, "that checklist")
        if checklist is None:
            return jsonify({"error": "No such checklist."}), 404
        item_uid = str(body.get("item_uid") or "").strip()
        if not item_uid:
            return jsonify({"error": "Which item?"}), 400
        day = (str(body.get("day") or "").strip() or _today())
        try:
            touched = checklist_store.toggle(
                checklist, item_uid, bool(body.get("checked")), day,
                session.get("user", ""))
        except LabCoreError as exc:
            # `toggle` writes the item and can cascade to its parent, so a
            # refusal part-way leaves the round genuinely inconsistent. The
            # operator must see it and press again rather than walk away from a
            # tick that was never recorded.
            return _labcore_failed(exc, "that tick")
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
        try:
            checklist = checklist_store.get(uid)
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "that checklist")
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
        try:
            checklist_store.set_value(uid, item_uid, value, day,
                                      session.get("user", ""))
        except LabCoreError as exc:
            # A reading exists to be a trend. Reported as recorded and dropped,
            # it leaves a gap nobody knows to go back and fill.
            return _labcore_failed(exc, "that reading")
        _page_drop(f"checklists:{day}", "checklisthistory")
        return jsonify({"ok": True})

    @app.route("/api/checklists/<uid>/values")
    def api_checklist_values(uid):
        """One numeric item's readings over time — the point of `number`."""
        item_uid = (request.args.get("item") or "").strip()
        try:
            checklist = checklist_store.get(uid)
            item = None
            if checklist is not None:
                item = next((i for i in checklist.items
                             if i.uid == item_uid), None)
            series = checklist_store.values(uid, item_uid)
        except LabCoreError as exc:
            # A flat, empty trend is a claim about a cylinder nobody has been
            # reading — the opposite of what an unreadable series means.
            return _labcore_unreadable(exc, "that item's readings")
        return jsonify({"series": series,
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

        # An import is many writes into a queue that refuses past 100 pending,
        # so a refusal part-way is the ORDINARY outcome here rather than an edge
        # case. Every count below is of work LabCore acknowledged, and a refusal
        # reports how far it got instead of claiming the file landed: re-running
        # is safe (checklists upsert by uid, ticks upsert on
        # day+checklist+item), so "run it again" is real advice.
        saved_names = []
        for checklist in found:
            try:
                checklist_store.save(checklist)
            except LabCoreError as exc:
                _page_drop("checklists:", "checklisthistory")
                _audit("checklist v4 import incomplete", "",
                       {"imported": len(saved_names), "of": len(found)})
                body_json, status = _labcore_failed(
                    exc, "the rest of the V4 import")
                data = body_json.get_json()
                data.update({"count": len(saved_names),
                             "checklists": preview,
                             "history_rows": 0, "history_days": 0,
                             "incomplete": True, "dry_run": False,
                             "error": "{0} of {1} checklists landed before "
                                      "LabCore stopped accepting writes. "
                                      "Nothing is duplicated by running the "
                                      "import again.".format(len(saved_names),
                                                             len(found))})
                return jsonify(data), status
            saved_names.append(checklist.name)

        # The archive: V4's checklist_state.json, if it came along.
        history_rows = 0
        history_days = 0
        state_text = body.get("state")
        if isinstance(state_text, str) and state_text.strip():
            rows = import_v4_state(state_text, found)
            known = {c.uid for c in found}
            rows = [r for r in rows if r["checklist_uid"] in known]
            history_days = len({r["day"] for r in rows})
            try:
                history_rows = checklist_store.import_state(rows)
            except ChecklistWriteError as exc:
                # `import_state` raises rather than returning a short count,
                # because a short count is indistinguishable from "the file only
                # had that many rows". Its message carries how many landed.
                _page_drop("checklists:", "checklisthistory")
                _audit("checklist v4 import incomplete", "",
                       {"imported": len(found), "history": str(exc)})
                data, status = _labcore_failed(exc, "the imported history")
                payload = data.get_json()
                payload.update({"count": len(found), "checklists": preview,
                                "history_rows": 0,
                                "history_days": history_days,
                                "incomplete": True, "dry_run": False,
                                "error": "The rounds imported, but LabCore "
                                         "stopped accepting the historic "
                                         "ticks. Run the import again to "
                                         "finish it — nothing is duplicated."})
                return jsonify(payload), status
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
        try:
            return jsonify(_page("checklisthistory",
                                 lambda: {"days": checklist_store.history()}))
        except LabCoreError as exc:
            # This is the archive an auditor asks for. "No rounds recorded yet"
            # about three years of ticks is the worst answer this page can give.
            return _labcore_unreadable(exc, "the checklist archive")

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
        app.config["LIVE"].record(body["machine_uid"], body)
        return "", 204

    @app.route("/api/machines/<machine_uid>/position", methods=["POST"])
    def api_machine_position(machine_uid):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        try:
            locked = map_settings.locked()
        except LabCoreError as exc:
            # The lock is a permission check, so an unreadable one cannot be
            # assumed open — and a drag saved past it would be refused by the
            # same LabCore anyway.
            return _labcore_failed(exc, "this instrument's position")
        if locked:
            return jsonify({"error": "The map is locked. Unlock it to "
                                     "rearrange the floor."}), 409
        body = request.get_json(silent=True) or {}
        try:
            layout_store.save_position(machine_uid,
                                       float(body.get("x")), float(body.get("y")))
        except (TypeError, ValueError):
            return jsonify({"error": "Position needs numeric x and y."}), 400
        except MachineMapError as exc:
            # The headline case. This route used to answer {"ok": true} for a
            # drag that never reached LabCore, so the instrument sat where the
            # operator dropped it until the next poll snapped it back — and the
            # arrangement tools wrote a whole floor that way.
            return _labcore_failed(exc, "this instrument's position")
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
        try:
            target_store.assign(machine_uid, targets)
        except MachineMapError as exc:
            # `assign` is a DELETE then an INSERT per target, so a refusal
            # part-way leaves the instrument checked against SOME of what was
            # asked for. The store says so in its own words and `_labcore_failed`
            # carries that text through verbatim, because "re-apply the whole
            # set" is the only safe instruction and only the store knows it
            # applies.
            return _labcore_failed(
                exc, "this instrument's QC assignment",
                "Part of the set may have been cleared, so re-apply the whole "
                "assignment rather than assuming it held.")
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
        try:
            refusal = _refuse_if_live(machine_uid, body)
        except LabCoreError as exc:
            # The guard reads the heartbeats and the config. Unreadable, it
            # cannot say whether a module is running this machine right now —
            # and "delete it anyway" is not a decision to make on a blip.
            return _labcore_unreadable(exc, "whether a module is running this "
                                            "instrument")
        if refusal is not None:
            return refusal

        # Retiring a machine is seven separate writes into a queue that takes
        # one statement at a time, so it CANNOT be atomic. What it can be is
        # honest: each step is confirmed, and the first refusal stops the
        # sequence and reports exactly how far it got. Every step is a DELETE,
        # so pressing Delete again finishes the job — which is why stopping is
        # better than ploughing on and reporting a clean retirement over a
        # machine that still has its configuration.
        def _drop(table):
            def go():
                res = gateway.sql(
                    "DELETE FROM {0} WHERE machine_uid = ?".format(table),
                    [machine_uid])
                try:
                    confirm_write(res)
                except LabCoreRefused as exc:
                    # The one refusal that honestly means "already gone": a
                    # table nothing has ever written to holds no rows for this
                    # machine, so there is nothing left to retire. Every other
                    # refusal stops the sequence.
                    if not is_missing_table(exc):
                        raise
            return go

        steps = [
            ("its live status", _drop("lem_machine_status")),
            ("its QC bands", _drop("lem_qc_specs")),
            ("its control row", _drop("lem_machine_control")),
            ("its position on the floor",
             lambda: layout_store.forget(machine_uid)),
            ("its QC assignments", lambda: target_store.forget(machine_uid)),
            # Orphaned PM rows re-attach if that uid is ever registered again,
            # so this failing has to fail the delete rather than be skipped.
            ("its PM and calibration tasks",
             lambda: maint_store.forget(machine_uid)),
            # A stranded config would offer itself again in the module's picker.
            ("its configuration", lambda: config_store.delete(machine_uid)),
        ]
        if body.get("purge_history"):
            steps.append(("its history", _drop("lem_machine_log")))

        removed = []
        for label, step in steps:
            try:
                step()
            except LabCoreError as exc:
                snapshots.refresh_soon()
                # Audited even though the retirement failed: a half-retired
                # machine is exactly the state someone will need explained.
                _audit("machine delete incomplete", machine_uid,
                       {"removed": removed, "stopped_at": label})
                data, status = _labcore_failed(
                    exc, "{0} of “{1}”".format(label, machine_uid),
                    "{0} removed before it stopped. Press Delete again to "
                    "finish — it picks up where it left off.".format(
                        (", ".join(removed) or "Nothing").capitalize()))
                payload = data.get_json()
                payload.update({"removed": removed, "stopped_at": label,
                                "complete": False})
                return jsonify(payload), status
            removed.append(label)

        # Audited AFTER the purge on purpose: wiping a machine's history is the
        # one action whose record must survive the wipe.
        snapshots.refresh_soon()
        _audit("machine deleted", machine_uid,
               {"purged_history": bool(body.get("purge_history"))})
        return jsonify({"ok": True, "complete": True})

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
        """uid → title. Raises when the snapshot has not built and LabCore
        cannot be read — see `_machine_list`."""
        return {m["machine_uid"]: m["title"] for m in _machine_list()}

    def _titles_soft() -> tuple:
        """`(titles, ok)` for the paths that only LABEL rows with a name.

        Deliberately different from `_titles()`, which raises. A CSV export or a
        history list whose machine column falls back to the uid is ugly and
        complete; refusing to serve it because the *labels* could not be read
        would withhold the data over its decoration. The routes that DECIDE
        something from this map — the corrections guard, the PM import — keep
        calling `_titles()` and fail loudly, because there a missing name means
        a missing machine.
        """
        try:
            return _titles(), True
        except LabCoreError as exc:
            logger.warning("machine titles unavailable: %s", exc)
            return {}, False

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
        try:
            beats = (beats_from_tables(tables) if tables is not None
                     else state_reader.heartbeats())
            configs = config_store.list()
        except LabCoreError as exc:
            # Both reads guard a write. An empty picker invites a SECOND config
            # for a bench that already has one, and an empty beat map says no
            # parser is live anywhere — which is what clears the way to delete
            # the configuration of a machine that is running right now.
            return _labcore_unreadable(exc, "the machine configurations")
        for row in configs:
            beat = beats.get(row["machine_uid"]) or {}
            row["last_poll"] = beat.get("last_poll")
            row["in_use"] = _beat_is_fresh(beat.get("last_poll"))
        return jsonify({"configs": configs})

    @app.route("/api/machine-configs/<machine_uid>")
    def api_get_machine_config(machine_uid):
        try:
            record = config_store.get(machine_uid)
        except ConfigReadUnavailable as exc:
            # THE 404 TRAP. "Could not ask" served as "does not exist" is how a
            # module that has been parsing all morning is told it was never
            # configured — and it would then offer to make a second one.
            return _labcore_unreadable(exc, "that machine's configuration")
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
        except MachineConfigError as exc:
            return _labcore_failed(exc, "the new machine configuration")
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
        except MachineConfigError as exc:
            # A whole bench's mappings, QC wiring and PM tasks. This used to
            # answer {"ok": true, **saved} built out of an answer nobody read.
            return _labcore_failed(exc, "this machine's configuration")
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
        except ConfigReadUnavailable as exc:
            # Listed before the write case on purpose: the source could not be
            # READ, which is not the LookupError above. A 404 here would say the
            # config being copied does not exist.
            return _labcore_unreadable(exc, "the configuration being copied")
        except MachineConfigError as exc:
            return _labcore_failed(exc, "the duplicated configuration")
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
        try:
            refusal = _refuse_if_live(machine_uid, body)
        except LabCoreError as exc:
            # The guard itself reads. If it cannot tell whether a module is
            # running this config, it must not silently wave the delete through.
            return _labcore_unreadable(exc, "whether a module is using this "
                                            "configuration")
        if refusal is not None:
            return refusal
        try:
            config_store.delete(machine_uid)
        except MachineConfigError as exc:
            return _labcore_failed(exc, "deleting this configuration")
        snapshots.refresh_soon()
        return jsonify({"ok": True})

    # ── PM & calibration, managed from the floor ──────────────────────
    from maintenance_store import MaintenanceStore, MaintTaskRecord

    maint_store = MaintenanceStore(gateway)

    @app.route("/api/machines/<machine_uid>/maintenance")
    def api_list_maintenance(machine_uid):
        try:
            tasks = maint_store.for_machine(machine_uid)
        except LabCoreError as exc:
            # "Nothing scheduled" about an instrument with a calibration due is
            # the answer that gets the calibration missed.
            return _labcore_unreadable(exc, "this instrument's PM and "
                                            "calibration tasks")
        return jsonify({"tasks": [t.to_dict() for t in tasks]})

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
        except LabCoreError as exc:
            return _labcore_failed(exc, "that PM or calibration task")
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
        try:
            task = maint_store.get(uid)
        except LabCoreError as exc:
            # Not the 404 below: a task that exists must never be reported as
            # missing because the read timed out.
            return _labcore_unreadable(exc, "that task")
        if task is None:
            return jsonify({"error": "No such task."}), 404
        when = str(body.get("when") or datetime.now().date().isoformat())
        note = str(body.get("note") or "")
        try:
            maint_store.complete(uid, when, note)
        except LabCoreError as exc:
            return _labcore_failed(exc, "this completion")
        snapshots.refresh_soon()

        # The completion belongs in the machine's history too — and this is a
        # SECOND write, which can be refused on its own. The reschedule above
        # has already happened and cannot be taken back, so failing the whole
        # request here would be the opposite lie: "not completed" about work
        # that is now recorded as done and no longer due.
        #
        # So it answers 200 and says which half is missing. `logged: false` is
        # not a detail — the PM/CAL history pages read lem_machine_log, so a
        # dropped line is a completion an auditor cannot find, and the operator
        # is the only person able to add the note back.
        logged = True
        try:
            snapshots.ensure_schema()
            confirm_write(gateway.sql(
                "INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
                "test_name, value, detail) VALUES (?, ?, ?, '', '', '', ?)",
                [task.machine_uid, datetime.now().isoformat(), task.kind,
                 json.dumps({"task": task.name, "completed": when,
                             "note": note,
                             "by": session.get("user", "")})]))
        except LabCoreError as exc:
            logged = False
            logger.warning("completion of %s not written to the log: %s",
                           uid, exc)
        if not logged:
            return jsonify({
                "ok": True, "logged": False, "retry": True,
                "warning": "“{0}” is marked done and rescheduled, but LabCore "
                           "refused the history entry — this completion will "
                           "not appear in the PM/CAL record. Mark it done again "
                           "in a moment to add the entry.".format(task.name)})
        return jsonify({"ok": True, "logged": True})

    # ── audit: who changed the configuration ──────────────────────────
    # Editing a QC spec, assigning targets, running a changeover or deleting a
    # machine used to leave no trace anywhere. These land in lem_machine_log as
    # kind='config' so the logs page can show them next to everything else.
    def _audit(action: str, machine_uid: str = "", detail=None) -> None:
        """Record a configuration change. Never raises: an audit failure must
        not fail the change the operator actually asked for.

        Still never raises — but it no longer fails INVISIBLY. The write is
        confirmed like every other, and a refusal is logged. Swallowing the
        exception is the right call (the operator's change already happened and
        an audit line cannot undo it), swallowing the KNOWLEDGE is not: this is
        the trail that answers "who changed that band", and it was capable of
        being empty for a week with nothing anywhere saying so.
        """
        # No CREATE TABLE here. It used to run on every audit — a second write
        # into a queue that serialises at about 1.5 ops/sec, for a table
        # SnapshotService.ensure_schema() has already declared once at startup.
        try:
            snapshots.ensure_schema()
            confirm_write(gateway.sql(
                "INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
                "test_name, value, detail) VALUES (?, ?, 'config', '', ?, '', ?)",
                [machine_uid, _now().isoformat(timespec="seconds"), action,
                 json.dumps({"action": action, "by": session.get("user", ""),
                             **(detail or {})})]))
            # This is the only writer that can add a new `kind`, so this is the
            # only place the log's filter list can go stale.
            _page_drop("logkinds")
        except LabCoreError as exc:
            logger.warning("audit line for %r on %r was not recorded: %s",
                           action, machine_uid or "-", exc)
        except Exception as exc:               # a gateway that raises outright
            logger.warning("audit line for %r on %r failed: %s",
                           action, machine_uid or "-", exc)

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
        # `_titles()` reaches LabCore when the snapshot has not built, and it
        # now raises rather than shrugging. That must land in the SAME `failed`
        # flag the log read uses — this route already has the honest answer
        # shape ("this list may be incomplete"), so a raise belongs there rather
        # than in a 500 that loses the events we did manage to read.
        titles, ok = _titles_soft()
        if not ok and failed is not None:
            failed["at"] = True
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
                # DELIBERATE DEGRADATION. This fills a filter DROP-DOWN, and the
                # very same request already reports the read failure through
                # `failed["at"]` — raising here would replace an honest partial
                # answer with a 503 the page cannot render. An empty filter list
                # hides no events; it only offers no shortcuts.
                #
                return []
            return [str(r.get("kind")) for r in (res.get("rows") or [])
                    if r.get("kind")]

        kinds = _page("logkinds", kinds_now)
        if not kinds:
            # A BLANK is never kept. `_page` caches whatever `produce()`
            # returns, and the only thing that drops this key is a config
            # change (`_audit`) — so one timed-out DISTINCT would empty the
            # filter for days, which is the same shape of bug as the rest of
            # this branch, just in a drop-down.
            _page_drop("logkinds")
        out = {"events": entries, "kinds": kinds}
        if failed["at"]:
            out["error"] = ("LabCore did not answer in time — its write queue is "
                            "busy. This list may be incomplete; try again shortly.")
        return jsonify(out)

    @app.route("/api/logs.csv")
    def api_logs_csv():
        # The CSV takes the `failed` flag where the JSON takes a banner: a file
        # cannot carry a warning, and a download with a header row and nothing
        # under it is the one version of this bug that leaves the building and
        # gets filed as the lab's history.
        failed = {"at": False}
        entries = _log_entries(request.args, failed=failed)
        if failed["at"]:
            return jsonify({
                "error": "LabCore did not answer in time, so this export would "
                         "be incomplete and nothing has been downloaded. Try "
                         "again in a moment.",
                "retry": True, "labcore": "unavailable"}), 503
        rows = [[e["ts"], e["machine_title"], e["kind"], e["lab_id"],
                 e["test_name"], e["value"], e["by"],
                 json.dumps(e["detail"], separators=(",", ":"))]
                for e in entries]
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
        try:
            # "Nothing completed yet on this instrument" is the sentence an
            # auditor reads as a compliance gap. It has to mean it.
            listed = labcore_rows(res)
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "this instrument's PM and "
                                            "calibration history")
        history = []
        for row in listed:
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
        try:
            listed = labcore_rows(res)
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "the lab's PM and calibration "
                                            "history")
        # Names only decorate the rows here, so a blip on the machine list
        # falls back to uids rather than withholding the history itself.
        titles, _named = _titles_soft()
        history = []
        for row in listed:
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
        try:
            machines = state_reader.machines()
        except LabCoreError as exc:
            # The whole point of this sheet is that it is pre-filled with the
            # real equipment names — a typo'd name is the one error the import
            # format cannot recover from. A template listing no machines would
            # be filled in by hand and match nothing.
            return _labcore_unreadable(exc, "the equipment list")
        header, rows = template_csv_rows(machines)
        return _csv_response(rows, header, "lem_maintenance_template.csv")

    def _existing_completions() -> set:
        """Completions already in the log, so an import cannot double them.

        Raises rather than degrading to an empty set: this read is what makes
        the import idempotent, and an empty answer during a blip would report
        every completion in the file as new and write the lot again.
        """
        res = gateway.read_sql(
            "SELECT machine_uid, detail FROM lem_machine_log "
            "WHERE kind IN ('pm','calibration')")
        out = set()
        for row in labcore_rows(res):
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
        try:
            # Three reads that decide writes. `plan_import` matches the
            # spreadsheet against the equipment list and then the completions
            # it matched are WRITTEN — so a blip degraded to `[]` would report
            # every historic PM as "unmatched equipment" and import nothing,
            # and an empty task list would plan a duplicate of every task in
            # the lab. Report the blip and import nothing, explicitly.
            plan = plan_import(rows, state_reader.machines(),
                               _existing_completions(), maint_store.all())
        except LabCoreError as exc:
            return _labcore_unreadable(
                exc, "the equipment and PM records this import matches against")
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

        snapshots.ensure_schema()
        # `made` counts rows LabCore ACKNOWLEDGED. It used to count loop
        # iterations, so an import that filled the queue reported "imported
        # 340" over an empty log. Re-running is safe — `_existing_completions`
        # above skips what already landed — so stopping at the first refusal
        # and saying how far it got is a workable instruction.
        made = 0
        rescheduled = 0
        failure = None
        for entry in plan["create"]:
            # Stamp the event at the completion date, not now, so the history
            # sorts and charts as the work actually happened.
            try:
                confirm_write(gateway.sql(
                    "INSERT INTO lem_machine_log (machine_uid, ts, kind, "
                    "lab_id, test_name, value, detail) "
                    "VALUES (?, ?, ?, '', '', '', ?)",
                    [entry["machine_uid"], entry["completed"] + "T00:00:00",
                     entry["kind"],
                     json.dumps({"task": entry["task"],
                                 "completed": entry["completed"],
                                 "note": entry["note"],
                                 "by": entry["by"] or session.get("user", ""),
                                 "imported": True})]))
            except LabCoreError as exc:
                failure = exc
                break
            made += 1
        if failure is None:
            for move in plan["reschedule"]:
                try:
                    task = maint_store.get(move["uid"])
                    if task is not None:
                        maint_store.complete(move["uid"], move["last_done"],
                                             task.note)
                        rescheduled += 1
                except LabCoreError as exc:
                    failure = exc
                    break
        payload["created"] = made
        payload["rescheduled"] = rescheduled
        if failure is not None:
            _audit("maintenance history import incomplete", "",
                   {"created": made, "rescheduled": rescheduled,
                    "of": len(plan["create"])})
            data, status = _labcore_failed(failure, "the rest of the import")
            body_json = data.get_json()
            body_json.update(payload)
            body_json["incomplete"] = True
            body_json["error"] = (
                "{0} of {1} completions were written before LabCore stopped "
                "accepting them, and {2} schedules were moved. Run the import "
                "again to finish it — what already landed is skipped.".format(
                    made, len(plan["create"]), rescheduled))
            return jsonify(body_json), status
        _audit("maintenance history imported", "",
               {"created": made, "skipped": payload["skipped"],
                "unmatched": len(plan["unmatched"]), "errors": len(errors),
                "rescheduled": len(plan["reschedule"])})
        return jsonify(payload)

    @app.route("/api/maintenance")
    def api_all_maintenance():
        """Every machine's PM/CAL in one list — "what is overdue anywhere",
        which no per-machine dialog can answer."""
        from snapshot_service import maintenance_from_tables, titles_from_tables
        tables = _snapshot_tables()
        if tables is None:                 # first request, or LabCore down at boot
            # Names only; `tables` is empty either way on this branch, so the
            # task list is the same and a blip costs the titles, not the page.
            titles, tables = _titles_soft()[0], {}
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
        try:
            maint_store.delete(uid)
        except LabCoreError as exc:
            # A task reported as removed and still in LabCore comes back on the
            # next poll, so the operator deletes it again and again.
            return _labcore_failed(exc, "removing that task")
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
        # Raises rather than returning []. This feeds the control chart AND the
        # QC export an assessor asks for; a file that silently contains no QC at
        # all is the one document where "empty" cannot be allowed to mean
        # "could not read". Missing table still means empty — no module has ever
        # logged anything.
        return labcore_rows(res)

    @app.route("/api/machines/<machine_uid>/qc-trend")
    def api_qc_trend(machine_uid):
        """One series per test: the last results with the pass band, so the
        floor can draw a control chart rather than just a colour."""
        series = {}
        try:
            events = _qc_events(machine_uid)
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "this instrument's QC history")
        for row in events:
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

    _corrections_ready = {"at": False}

    def _corrections_schema() -> None:
        """Declare the table once, and only once it is ACKNOWLEDGED.

        The flag used to not exist at all — the DDL ran on every read of every
        correction dialog, one more write into a queue that serialises at ~1.5
        ops/sec. Setting the flag on an unconfirmed answer would be the other
        half of the bug: a boot while the queue was full would leave this
        believing the table exists for the life of the process, and every
        correction written afterwards would go nowhere.
        """
        if _corrections_ready["at"]:
            return
        confirm_write(gateway.sql(CORRECTIONS_DDL))
        _corrections_ready["at"] = True

    def _corrections(machine_uid: str) -> dict:
        """This bench's correction factors. Raises rather than answering `{}`.

        `{}` means "no correction is in force", and under ISO/IEC 17025 §7.8.2
        that is a claim about every result this instrument reports. It also
        feeds the audit line on a save — a degraded read would record
        `previous: 0.0` about a bench that has been running at -3.0.
        """
        _corrections_schema()
        res = gateway.read_sql(
            "SELECT test_name, correction, units FROM lem_correction_factors "
            "WHERE machine_uid = ? ORDER BY test_name", [machine_uid])
        out = {}
        for r in labcore_rows(res):
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
        # This used to be `except Exception: record = None`, which now also
        # swallows ConfigReadUnavailable — and the cost of that is an editor
        # offering NO methods on a bench that reports twelve, with nothing on
        # screen to say why. A correction is a compliance control; an empty list
        # of things it can apply to is not a safe shrug. It raises, and
        # api_get_corrections turns it into a 503 the dialog shows.
        record = config_store.get(machine_uid)
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
        try:
            saved = _corrections(machine_uid)
            methods = _reported_methods(machine_uid, saved)
        except LabCoreError as exc:
            # A dialog showing 0.0 for a bench running at -3.0 is worse than a
            # dialog that will not open: the operator would type the offset in
            # again over one that is already there.
            return _labcore_unreadable(exc, "this instrument's correction "
                                            "factors")
        return jsonify({"corrections": list(saved.values()),
                        "methods": methods})

    @app.route("/api/machines/<machine_uid>/corrections", methods=["POST"])
    def api_save_correction(machine_uid):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        try:
            known = _titles()
        except LabCoreError as exc:
            # `_titles()`, not `_titles_soft()`: this map DECIDES, and a soft {}
            # would turn a blip into "No such instrument." about a bench the
            # operator is standing at.
            return _labcore_unreadable(exc, "the instrument list")
        if machine_uid not in known:
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
        try:
            existing = _corrections(machine_uid).get(test_name)
            previous = existing["correction"] if existing else 0.0
            confirm_write(gateway.sql(
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
                 session.get("user", "")]))
        except LabCoreError as exc:
            # This number is added to EVERY measurement the bench reports, not
            # only its QC. Reported as saved and dropped, the lab believes it is
            # correcting results it is still reporting raw.
            return _labcore_failed(
                exc, "the correction factor for “{0}”".format(test_name))
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
        try:
            existing = _corrections(machine_uid).get(test_name)
        except LabCoreError as exc:
            # Not the 404 below: "could not ask" is not "there is no correction
            # for that test", and the second is what makes a live offset
            # invisible.
            return _labcore_unreadable(exc, "this instrument's correction "
                                            "factors")
        if existing is None:
            return jsonify({"error": f"No correction for “{test_name}”."}), 404
        try:
            confirm_write(gateway.sql(
                "DELETE FROM lem_correction_factors WHERE machine_uid = ? "
                "AND test_name = ?", [machine_uid, test_name]))
        except LabCoreError as exc:
            # A correction reported as removed and still in force keeps being
            # added to every reading, and the dialog now shows it as gone.
            return _labcore_failed(
                exc, "removing the correction for “{0}”".format(test_name))
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
        try:
            # A downloaded file with a header row and nothing under it is the
            # least recoverable version of this bug: it leaves the building.
            rows = labcore_rows(res)
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "this instrument's history")
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
        # DELIBERATE DEGRADATION, and the only one left in this route. This read
        # names the FILE, not its contents — the rows above are already
        # confirmed. Losing the title costs a download called
        # "m1 history.csv" instead of "Multitek NS history.csv"; refusing to
        # serve the record because its label could not be read would withhold
        # the data over its decoration.
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
        try:
            events = _qc_events(limit=20000)
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "the lab's QC history")
        # Names decorate; the QC rows are the record. A blip on the machine
        # list leaves the `machine` column blank exactly as it already did for
        # a retired uid, and does not withhold the file.
        titles, _named = _titles_soft()
        out = []
        for r in events:
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
        try:
            events = state_reader.events(machine_uid, limit)
        except LabCoreError as exc:
            # "This bench has never reported" is a reason people walk over and
            # start touching an instrument that is running fine.
            return _labcore_unreadable(exc, "this instrument's history")
        return jsonify({"events": events})

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
        try:
            recorded = (state_reader.recent_events(limit) if tables is None
                        else events_from_tables(tables, limit))
        except LabCoreError as exc:
            # Only the live-read arm can raise: at the floor's own limit this is
            # served from the snapshot at zero ops. So this is either the log
            # viewer's deep request — where an empty answer reads as a quiet
            # lab — or a floor polling before the first snapshot has built, and
            # its rail already handles a failed fetch.
            return _labcore_unreadable(exc, "the lab's recent activity")
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
            return jsonify({"error": str(exc)}), 400
        except LabCoreError as exc:
            # The highest-consequence write on the floor. This is a COMMAND to a
            # bench — the module polls lem_machine_control and takes itself to
            # SERVICE or DEAD-LINE. Dropped and reported as applied, the floor
            # shows an instrument out of service while the bench keeps running
            # customer samples on it; an unheard "clear" locks out a healthy one.
            return _labcore_failed(exc, "this override")
        snapshots.refresh_soon()
        return jsonify({"ok": True})

    @app.route("/api/qc-specs")
    def api_list_qc_specs():
        machine_uid = request.args.get("machine_uid")
        try:
            listed = spec_store.list_specs(machine_uid)
        except LabCoreError as exc:
            # `specs: []` is kept in the body so nothing downstream has to guard
            # for a missing key, but the STATUS is a failure and the message
            # says so. An empty band list read as data is a bench told it has no
            # QC to check against.
            data, status = _labcore_unreadable(exc, "the QC bands")
            payload = data.get_json()
            payload.update({"specs": [], "labcore_online": False})
            return jsonify(payload), status
        specs = []
        for spec in listed:
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
        except LabCoreError as exc:
            # NOT folded into the 400. A refused queue is not an invalid band,
            # and telling an operator their numbers are wrong sends them
            # re-typing a spec that was perfectly good. `lem_qc_specs` is what
            # every station module reads to judge its own instrument, so a drop
            # here means a bench is checked against a band the lab set and
            # LabCore never took.
            return _labcore_failed(exc, "this QC band")
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
        try:
            spec_store.delete(str(body.get("machine_uid") or ""),
                              str(body.get("test_name") or ""))
        except LabCoreError as exc:
            # A band reported as removed and still in LabCore keeps failing a
            # bench against limits nobody believes are there any more.
            return _labcore_failed(exc, "removing this QC band")
        snapshots.refresh_soon()
        _audit("qc-spec deleted", str(body.get("machine_uid") or ""),
               {"test": str(body.get("test_name") or "")})
        return jsonify({"ok": True})

    # ── QC samples: the V4 model, central and shared ──────────────────
    from qc_samples import QcSample, QcSampleStore

    sample_store = QcSampleStore(gateway)

    @app.route("/api/qc-samples")
    def api_list_qc_samples():
        try:
            samples = sample_store.list_samples()
        except LabCoreError as exc:
            # Same shape as /api/qc-specs: the empty list stays in the body so
            # nothing has to guard for a missing key, and the status is what
            # says it is not an answer. An empty QC library also hides the
            # duplicate check, so a second lot can be created over a real one.
            data, status = _labcore_unreadable(exc, "the QC sample library")
            body_json = data.get_json()
            body_json.update({"samples": [], "labcore_online": False})
            return jsonify(body_json), status
        payload = []
        for sample in samples:
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
        except LabCoreError as exc:
            # A standard nobody notices is missing: the dialog closes, the
            # library repaints from a read that never sees it, and the operator
            # assigns instruments to a lot that is not there.
            return _labcore_failed(exc, "this QC standard")
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
        except LabCoreUnavailable as exc:
            # This one used to surface as a 400 "QC sample not found" — a 404
            # about a lot that exists, from a read that never happened. The
            # library read decides the writes, so it may not degrade: an empty
            # answer would report "0 machines moved" while every instrument in
            # the lab stayed pointed at a retired lot and QC quietly stopped.
            return _labcore_unreadable(exc, "the QC library this changeover "
                                            "reads")
        except LabCoreError as exc:
            # Partial by nature: the new lot may exist and some machines may
            # already have moved. Re-running is safe (the lot upserts by name
            # and moved machines no longer reference the old one), which is why
            # "run it again" is the instruction rather than a repair.
            return _labcore_failed(
                exc, "the rest of this changeover",
                "Some instruments may already point at the new lot. Run the "
                "changeover again — it picks up the ones still on the old one.")
        return jsonify({"ok": True, "moved": moved})

    @app.route("/api/qc-samples", methods=["DELETE"])
    def api_delete_qc_sample():
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        try:
            sample_store.delete(str(body.get("name") or ""))
        except LabCoreError as exc:
            # The rename path deletes the old name after saving the new one, so
            # a silent drop here leaves two lots in the library under different
            # names with the same Lab ID.
            return _labcore_failed(exc, "removing this QC standard")
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
