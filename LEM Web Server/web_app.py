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

from flask import (Flask, Response, g, jsonify, redirect, render_template,
                   request, session, url_for)

from data_source import build_sample_index, evaluate_box, qc_is_stale
from db_config_store import DbConfigStore
from labcore_result import (LabCoreError, LabCoreRefused, LabCoreUnavailable,
                            confirm_write, is_missing_table,
                            rows as labcore_rows)
from labcore_gateway import check_write, refusal_reason
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

# How much of `lem_machine_log` the search box can see, and how often that is
# re-read. Module scope, not closure scope: these decide what "no such sample"
# means, so a test has to be able to move them — and a deployment with a much
# larger log has to be able to raise them without editing a function body.
SEARCH_CORPUS_SECONDS = 180
SEARCH_CORPUS_ROWS = 20000

# ── somewhere for those warnings to land ────────────────────────────────────
#
# EVERY report this branch added is a `logger.warning`: a refused audit line, a
# CSV whose machine names could not be read, a warm-up job that gave up, a live
# address that was not published. On the target platform none of them went
# anywhere. LEM runs on ASAPSV1 as a `.pyw` under pythonw.exe — no console —
# and no handler was ever configured, so `logging` fell back to writing to a
# `sys.stderr` that does not exist. Detecting a refused write and announcing it
# into a void is barely better than not detecting it.
#
# WHERE IT GOES. `tray.data_dir()` — `C:\ASAPApps\lem\data` on the server,
# via LEM_DATA_DIR — and NOT the code directory. A deploy re-points `current`
# at a whole new release folder (RELEASING.md §1) and the release archive
# excludes `data/`, so a log written inside the release disappears on the next
# deploy: precisely the one you want to read after a deploy went wrong.
# `restart.log` already lives there for the same reason.
#
# ROTATING, because a busy afternoon on a full LabCore queue is one warning per
# refused write, and this app is not supposed to be able to fill a disk.
LOG_FILENAME = "lem.log"
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUPS = 5


def configure_logging(directory=None, level=logging.INFO) -> str:
    """Open the app's log file. Returns its path, or "" if it could not.

    Attached to the ROOT logger: every module here logs to
    `logging.getLogger(__name__)`, so the stores, the snapshot service and
    `live_presence` all reach it without each one being wired up separately.

    Idempotent — `create_app` runs once in production and hundreds of times in
    the test suite, and a handler per call would write every line that many
    times over. Never raises: a server that refuses to start because it could
    not open its log is a worse outage than the one the log was for.
    """
    from logging.handlers import RotatingFileHandler

    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, "_lem", False):
            return getattr(handler, "baseFilename", "")
    import tray

    target = os.path.join(directory or tray.data_dir(), LOG_FILENAME)
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        handler = RotatingFileHandler(
            target, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS,
            encoding="utf-8", delay=False)
    except OSError as exc:                      # read-only, missing, in use
        # `print`, not `logger`: the logger is precisely what does not work
        # yet. On a console-less service this goes nowhere either, which is
        # why the path is also reported by /healthz.
        print("LEM could not open its log at {0}: {1}".format(target, exc))
        return ""
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    # WARNING is what this branch reports with, so the handler has to be at or
    # below it; INFO keeps the boot lines that say which LabCore and which
    # version, which is the context those warnings need.
    handler.setLevel(level)
    handler._lem = True
    root.addHandler(handler)
    # ONE EXCEPTION, and it is the difference between a useful log and a full
    # one. `floor.html` re-reads its whole world every two seconds from every
    # open browser and every bench POSTs /api/live on each poll; werkzeug logs
    # a line per request at INFO, which is thousands an hour and would rotate
    # the refusals — the only reason this file exists — out of the file within
    # a day. Filtered on OUR HANDLER rather than silenced at the logger, so a
    # console dev run still shows its request log.
    handler.addFilter(lambda record: not (
        record.name.startswith("werkzeug") and record.levelno < logging.WARNING))
    if root.level > level or root.level == logging.NOTSET:
        # The root logger defaults to WARNING, which would be enough for the
        # refusals and would silently drop the INFO context around them.
        root.setLevel(level)
    return target

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


# ── the status gutter: what state was this instrument in WHILE that ran? ────
#
# Ryan's whiteboard: an events list with a colour band down the left. GREEN over
# four sample runs, then a QC event, then YELLOW, then RED at a QC that read 500
# against a band of about 7.8. The QC events are the TRANSITIONS; the band says
# what the instrument's state was while each sample ran.
#
# That is ISO/IEC 17025's question — "was this equipment in control when this
# result was produced?" — answered in the record rather than by an assessor
# cross-referencing a run report against a QC report on timestamps.
#
# THE RULE IS THE ENGINE'S OWN RULE, READ BACKWARDS THROUGH TIME.
# `data_source.evaluate_box` decides a machine's QC status right now from the
# verdicts standing right now: any assigned test out of spec is RED, otherwise
# the oldest standing PASS is GREEN until `qc_is_stale` says the rolling window
# has closed on it, and then YELLOW. Everything below is that same rule
# evaluated at the timestamp of each EVENT instead of at `now`. Inventing a
# second rule here is how the gutter would come to disagree with the dot on the
# floor about the same instrument.
#
# Four consequences worth keeping:
#
#   * BEFORE THE FIRST QC IN THE WINDOW THE ANSWER IS UNKNOWN. Not GREEN. A
#     bench with no QC yet is exactly the grey state "QC is assigned, never
#     detected" already refuses to colour in, and assuming GREEN would report
#     every run made before the first standard as made under control.
#   * A FAIL DOES NOT DECAY TO YELLOW. YELLOW is a PASS that aged out; RED is a
#     fail and it stands until another QC says otherwise. Running staleness over
#     every verdict downgrades "out of spec" to "a bit old", which is the softer
#     sentence and the wrong one.
#   * `in_spec` IS TRI-STATE, here as everywhere else in this tree. A row whose
#     detail will not parse leaves the instrument UNKNOWN — never a pass.
#   * SERVICE AND DEAD-LINE ARE NEVER EMITTED. They come from
#     `lem_machine_control`, which holds only what is in force NOW; nothing in
#     the record says WHEN an override was applied or lifted, so a gutter
#     painting them onto past events would be guessing. The set stays inside the
#     station module's vocabulary; this derivation uses four of the six.
GUTTER_DEFAULT_QC_HOURS = 24.0


def _finite(raw) -> Optional[float]:
    """A number, or None. NaN and inf are not readings — same rule as
    `qc_series._float`, which is where a NaN on a chart was stopped."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return None if value != value or value in (float("inf"),
                                               float("-inf")) else value


def _detail_dict(raw) -> dict:
    """`detail` is a JSON TEXT column; a fake gateway can hand back a dict.

    A detail that will not parse is an EMPTY detail, never an exception — the
    event still happened, and dropping a row over a formatting problem takes a
    real excursion off the record.
    """
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _gutter_band(detail: dict) -> Optional[dict]:
    """The band THIS verdict was judged against, off THIS row.

    Deliberately per row rather than "the newest band this series has": a
    standard gets re-certified and the limits move, and a historic verdict
    redrawn against today's band is a record that disagrees with the verdict
    written beside it (17025 7.11.3 — a reported result is not restated).

    None when the row carries no band, so the UI can say "judged against
    nothing recorded" instead of drawing a line at zero.
    """
    low, high = _finite(detail.get("low")), _finite(detail.get("high"))
    if low is None or high is None:
        return None
    return {"low": low, "high": high,
            "expected": _finite(detail.get("expected"))}


def _gutter_state(standing: dict, at: Optional[datetime], hours: float):
    """`(status, reason, since)` for the verdicts standing at `at`.

    `standing` is test_name -> {"in_spec", "ts", "at"}: the most recent QC
    verdict for each test, which is what the engine judges. `since` is the ts of
    the verdict that DECIDED the answer, so a UI can send someone from a sample
    run straight to the QC that says whether it was any good.
    """
    if not standing:
        return STATUS_UNKNOWN, "No valid QC data found.", None
    failed = [v for v in standing.values() if v["in_spec"] is False]
    if failed:
        # The one that put it out of spec first, and is still standing.
        first = min(failed, key=lambda v: v["ts"])
        return STATUS_RED, "QC Out of Spec", first["ts"]
    unread = [v for v in standing.values() if v["in_spec"] is None]
    if unread:
        first = min(unread, key=lambda v: v["ts"])
        return (STATUS_UNKNOWN,
                "The QC verdict on this instrument could not be read.",
                first["ts"])
    # Every standing verdict passed. The OLDEST of them is the binding one: it
    # is the first that will age out, and the window has to close on all of them.
    oldest = min(standing.values(), key=lambda v: v["ts"])
    if at is None or oldest["at"] is None:
        # A timestamp that will not parse cannot be placed against a window, and
        # "fresh" is a claim about elapsed time. Say unknown rather than assume.
        return (STATUS_UNKNOWN,
                "This event could not be placed in time, so the QC in force "
                "when it happened is unknown.", oldest["ts"])
    if qc_is_stale(oldest["at"], at, hours):
        return (STATUS_YELLOW,
                "QC stale (Last valid: {0})".format(
                    oldest["at"].strftime("%Y-%m-%d %H:%M")),
                oldest["ts"])
    return STATUS_GREEN, "QC Fresh", oldest["ts"]


def gutter_events(rows, hours: float = GUTTER_DEFAULT_QC_HOURS) -> List[dict]:
    """`lem_machine_log` rows for ONE instrument -> the gutter, newest first.

    Pure: no clock, no gateway, no Flask. The status of an event is a function
    of the verdicts recorded BEFORE it (and, for a QC row, of its own verdict),
    never of what time it is now — so the same window answers the same way
    tomorrow, which is what makes it a record rather than a dashboard.

    Walked oldest-first because a status is established by the EARLIER verdict,
    and reversed at the end because the whiteboard reads newest at the top.
    """
    ordered = sorted(rows or (), key=lambda r: str(r.get("ts") or ""))
    standing: Dict[str, dict] = {}
    out: List[dict] = []
    for row in ordered:
        ts = str(row.get("ts") or "")
        try:
            at = datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            at = None
        kind = str(row.get("kind") or "").strip()
        detail = _detail_dict(row.get("detail"))
        event = {
            "machine_uid": str(row.get("machine_uid") or ""),
            "ts": ts,
            "kind": kind,
            "lab_id": str(row.get("lab_id") or ""),
            "test_name": str(row.get("test_name") or "").strip(),
            # The value as RECORDED. `lem_machine_log.value` is a TEXT column
            # and a run's is often blank (its readings are in `detail.values`),
            # so this is not coerced; the QC row's number is parsed on the
            # transition below, where it is compared against a band.
            "value": str(row.get("value") or ""),
            "qc": kind == "qc",
        }
        if kind == "qc":
            # ONLY kind='qc'. A PM completion sharing the machine and the test
            # name once overwrote a certificate's band with its own (0 - 0.001)
            # and put every result out of spec — the same lesson `_is_qc_row`
            # exists for, on the other side of the seam.
            before, _reason, _since = _gutter_state(standing, at, hours)
            in_spec = detail.get("in_spec")
            in_spec = None if in_spec is None else bool(in_spec)
            standing[event["test_name"]] = {"in_spec": in_spec, "ts": ts,
                                            "at": at}
            status, reason, since = _gutter_state(standing, at, hours)
            event["transition"] = {
                "from": before, "to": status, "in_spec": in_spec,
                "value": _finite(row.get("value")),
                "band": _gutter_band(detail),
            }
        else:
            status, reason, since = _gutter_state(standing, at, hours)
        event.update({"status": status, "reason": reason,
                      "status_since": since})
        out.append(event)
    out.reverse()
    return out


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


# ── one noun on screen, over a trail that must not be rewritten ──────────────
_DISPLAY_NOUN = re.compile(r"\bmachines\b|\bmachine\b", re.I)


def display_action(action: str) -> str:
    """An audit action, in the word the rest of the app uses.

    The stored value is NOT touched. "machine deleted" sits in `lem_machine_log`
    rows written months ago and is what `_audit()` still writes, for the same
    reason `machine_uid` is never renamed: changing what goes INTO the table
    forks the record in two — rows before this date saying one word, rows after
    saying another, and any filter spanning them broken. The rename is the
    words a person reads, so it happens on the way out.

    Doing it here rather than in the template also brings the rows already in
    the table into the one noun, instead of leaving a visible seam at whatever
    date this shipped.
    """
    return _DISPLAY_NOUN.sub("equipment", _ACTION_WORDS.get(
        str(action or "").strip(), str(action or "")))


# An audit action whose STORED form is a constant, not a sentence.
#
# `levels.LEVEL_MOVE_ACTION` is written into `test_name` (see MOVE_LOG_SQL), so
# it went past the substitution above untouched and the Logs page printed the
# literal `level_move` in a column whose row above read "level created". The
# stored value is not touched, for the reason `display_action` gives: rows
# written before any rename have to keep matching a filter that spans them.
_ACTION_WORDS = {
    "level_move": "level moved",
}


# ── a detail blob, as a sentence ────────────────────────────────────────────
#
# The Logs page printed `detail` as raw JSON. On a level move that is
#
#   {"from":"","from_name":"","to":"1fbb5f3672d4","to_name":"Ground Floor"}
#
# — a bare uid on screen with the readable name sitting in the same blob, two
# keys away. These render the ones this app writes; anything else falls back to
# `key: value` pairs, which is still a line a person can read where
# `JSON.stringify` was not.


def _level_words(detail: dict, key: str) -> str:
    """The level a move names, preferring the name it was written down with.

    A move records BOTH the uid and the name at the time. The name is what a
    person reads; the uid is what survives a rename. An empty name with a uid
    present means the level has since been deleted — which is a fact worth
    printing, not a reason to print the uid.
    """
    name = str(detail.get(key + "_name") or "").strip()
    if name:
        return name
    uid = str(detail.get(key) or "").strip()
    if uid:
        return "a level that no longer exists"
    return ""


def describe_detail(action: str, detail) -> str:
    """What a log entry's detail says, in one sentence."""
    if not isinstance(detail, dict):
        return ""
    rest = {k: v for k, v in detail.items() if k not in ("action", "by")}
    if not rest:
        return ""
    act = str(action or "").strip()

    if act == "level_move":
        frm, to = _level_words(detail, "from"), _level_words(detail, "to")
        if frm and to:
            return f"Moved from {frm} to {to}."
        if to:
            return f"Placed on {to}."
        if frm:
            return f"Taken off {frm} — it now stands on the ground."
        return "Placement changed."

    level = rest.get("level")
    if isinstance(level, dict) and level.get("name"):
        name = str(level.get("name"))
        if act == "level created":
            return f"Created the level {name}."
        if act == "level renamed":
            return f"The level is now called {name}."
        return f"Level {name}."
    if act == "level deleted" and rest.get("level_uid"):
        return ("The level was deleted; equipment on it stands on the ground "
                "until it is placed again.")
    if act == "default level set" and rest.get("level_uid"):
        return "This level is where a screen now opens."

    # Everything else: readable pairs, never a JSON dump.
    bits = []
    for key, value in rest.items():
        if isinstance(value, dict):
            value = ", ".join(f"{k} {v}" for k, v in value.items() if v != "")
        elif isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value)
        text = str(value).strip()
        if text in ("", "None"):
            continue
        bits.append(f"{key.replace('_', ' ')}: {text}")
    return " \u00b7 ".join(bits)


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
# LabCore's write queue serialises at ~1.5 writes a second and refuses past ~100
# pending BY ANSWERING rather than raising — the recorded shape is an error dict
# carrying `busy` and `retry_after` (notes.md; lem_station_module.py:495). Every
# store now puts that answer through `labcore_result` instead of reading it as
# success, which means every route below has to decide what the operator is
# told. Two rules, and they are the whole point:
#
#   1. A refusal is never a 200. A dialog that closes on "Saved" after the band
#      was dropped is the failure this branch exists to remove.
#   2. A refusal is never a bare 500 either. "Internal Server Error" tells the
#      person nothing about whether to press the button again, and pressing the
#      button again is exactly the right move — the queue drains in seconds.
#
# So the kinds are told apart on the wire as well as in the message. THE STATUS
# CODE ANSWERS ONE QUESTION — is it worth coming back — and it answers it the
# same way here as in `refusal_response`, because both reach the same page:
#
#   503   Come back. A deep queue (`busy`), or a LabCore that could not be
#         asked at all. Carries `Retry-After` when LabCore said how long.
#   502   Do not. LabCore answered and the answer will not change — a
#         malformed statement, a column that does not exist. A client that
#         retries this retries forever.
#
# What the STATUS cannot say is what the operator may conclude about the
# record, and that is a different question with a different answer:
#
#   labcore="refused"      LabCore answered and said no. The record is
#                          DEFINITELY unchanged; press Save again.
#   labcore="unavailable"  Nothing answered, so nothing is known — which is
#                          NOT the same as nothing happened, and the wording
#                          never claims it is.
#
# `retry` is true on both because both clear on their own; `retryable` is the
# narrower promise that firing this exact write again is worth doing. A single
# flag cannot say three things.

_REFUSED_HINT = ("LabCore's write queue refused it, so nothing changed. "
                 "It clears in a few seconds — try again.")
_UNAVAILABLE_HINT = ("LabCore could not be reached, so this did not go through "
                     "and its state is unknown. Try again in a moment.")


def _labcore_failed(exc, what: str, note: str = "", *,
                    landed=None, not_landed=None, **extra):
    """The answer for a write LabCore did not acknowledge. JSON + status.

    `what` names the thing in the operator's words — "the QC band", "the map
    lock" — because a queue refusal carries no hint of what was being written.
    `note` is for the routes whose write is not atomic: a QC assignment or a
    machine deletion can be refused half-way, and "some of this did not save"
    is a different instruction from "press Save again".

    `detail` always carries the store's own message verbatim. The stores were
    written to say which drag or which assignment was lost, and that text must
    reach the page rather than stop at the log.

    **BUSY IS 503, NOT 502.** This used to answer 502 for every refusal and
    503 only for "could not be asked" — a second, incompatible reading of the
    same two status codes to the one `refusal_response` uses, in the same app,
    on endpoints the same page calls. The distinction that has to survive to
    the client is the one a client cannot recover by reading English: *is it
    worth coming back*. A deep queue clears in seconds and 503 is the status
    whose whole meaning is "come back"; a malformed statement will refuse
    again forever and 502 says so. "Could not be asked" keeps 503 because it
    is equally worth retrying — what tells the two apart in the body is
    `labcore`, which says whether the record is known unchanged.

    `landed` / `not_landed` are the multi-statement routes' half-done report,
    named the way `LEM.failure()` in static/lem.js reads them — there is no
    transaction across queue ops, so the honest answer is which statements
    landed rather than a claim of atomicity. A route that does not pass them
    can let the exception carry them instead; `partial` follows from whether
    anything landed at all.
    """
    refused = isinstance(exc, LabCoreRefused)
    busy = bool(refused and getattr(exc, "busy", False))
    hint = _REFUSED_HINT if refused else _UNAVAILABLE_HINT
    message = "{0} was NOT saved. {1}".format(what[:1].upper() + what[1:], hint)
    if note:
        message += " " + note
    body = {
        "error": message,
        "detail": str(exc),
        "saved": False,
        "retry": True,
        # `retry` says "this will clear on its own"; `retryable` says "and it
        # is worth firing this exact write at it again". A permanently invalid
        # statement is the case where they differ, and a client that cannot
        # tell them apart retries it forever.
        "retryable": busy,
        "labcore": "refused" if refused else "unavailable",
    }
    if landed is None:
        landed = getattr(exc, "landed", None)
    if not_landed is None:
        not_landed = getattr(exc, "not_landed", None)
    if landed is not None or not_landed is not None:
        body["landed"] = list(landed or [])
        body["not_landed"] = list(not_landed or [])
        body["partial"] = bool(body["landed"])
    body.update(extra)
    headers = {}
    delay = getattr(exc, "retry_after", None) if refused else None
    if delay is not None:
        body["retry_after"] = delay
        headers["Retry-After"] = str(int(delay))
    return jsonify(body), (502 if (refused and not busy) else 503), headers


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


# ── saying a thing once ──────────────────────────────────────────────────────
# How long an identical warning is held after it has been said.
#
# The floor polls /api/map every 2 seconds from every open browser, so its
# failure branch wrote one line per poll per screen. Measured at ~167 bytes,
# four wall displays make over a megabyte an hour — and the log is a 2 MB
# rotating file with five backups, so a single afternoon of one degraded read
# rotates away every refused write, every degraded schema and every unpublished
# live address. The noise about a problem deleted the evidence of it, which is
# the exact opposite of what this branch added logging for.
#
# Unsynchronised on purpose: two workers each saying it once is fine, and a lock
# on a logging path that runs inside a request is a worse trade than an
# occasional duplicate line.
#
# The store belongs to the APP, not the module. During a deploy the updater runs
# a candidate release on a scratch port while the live one is serving, and a
# module-global would let one app's first warning silence the other's — the
# health check would suppress exactly the line the release is being judged on.
WARN_REPEAT_SECONDS = 300


def throttled_warning(logger, seen: Dict[str, list], key: str,
                      message: str, *args) -> None:
    """Say it, then hold it and count, then say it again with the count.

    Never silence: the FIRST occurrence always lands, and the summary that
    follows names how many were suppressed — a warning nobody sees is the same
    as no warning, and "1 of 900" is the difference between a blip and an
    afternoon.
    """
    now = time.time()
    held = seen.get(key)
    if held is None or now - held[0] >= WARN_REPEAT_SECONDS:
        if held is not None and held[1]:
            logger.warning("%s (and %d more like it in the last %ds)",
                           message % args if args else message,
                           held[1], int(now - held[0]))
        else:
            logger.warning(message, *args)
        seen[key] = [now, 0]
        return
    held[1] += 1


def _already_recorded(exc) -> bool:
    """Does this refusal mean the row is ALREADY in the table?

    The spool below mints each row's uid once and keeps it across retries, so a
    row that landed on an attempt whose ANSWER never came back is refused the
    second time on the primary key. Without this the spool would retry it
    forever and `/healthz` would report a backlog that can never drain — the
    opposite of the honesty the spool exists for.

    Only the two phrases SQLite uses. Anything else is a real refusal and the
    row stays queued.
    """
    text = str(exc or "").lower()
    return "unique constraint" in text or "primary key" in text


class CorrectionAuditSpool:
    """Correction-audit rows LabCore would not take yet.

    ISO/IEC 17025 §7.8.2 makes the correction factor part of every result the
    bench reports, and `lem_correction_factors` is an UPSERT — so the save
    DESTROYS the previous value and `lem_correction_audit` is the only place
    left that says what it was. LabCore's write queue refuses past ~100 pending
    by answering, on an ordinary busy afternoon, and by then the operator's
    change has already landed.

    Three bad options and one good one. Failing the change over its receipt is a
    lie in one direction (the factor IS in force). Reporting success and
    dropping the row is a lie in the other, and it is the exact gap this table
    was created to close. Blocking on a retry inside the request hands the
    operator a spinner tied to somebody else's queue depth. So the row is held,
    retried by the thread that is already awake and already talking to LabCore,
    and COUNTED on `/healthz` until it lands.

    Bounded, because an outage lasting hours must not be able to grow this
    without limit. The OLDEST go first: the newest are the ones somebody is
    still standing at a bench waiting to see recorded, and a lost row is
    reported either way — it is in `lem.log` by name the moment it is refused.
    """

    MAX = 200

    def __init__(self, store, ensure_schema=None) -> None:
        self._store = store
        self._ensure = ensure_schema
        self._rows: List[dict] = []
        self._lock = threading.Lock()

    def pending(self) -> List[dict]:
        with self._lock:
            return [dict(row) for row in self._rows]

    def __len__(self) -> int:
        with self._lock:
            return len(self._rows)

    def oldest(self) -> str:
        with self._lock:
            return str(self._rows[0].get("when") or "") if self._rows else ""

    def add(self, row: dict) -> None:
        with self._lock:
            self._rows.append(dict(row))
            excess = len(self._rows) - self.MAX
            if excess > 0:
                del self._rows[:excess]

    def _forget(self, uid: str) -> None:
        with self._lock:
            self._rows = [row for row in self._rows if row.get("uid") != uid]

    def drain(self) -> int:
        """Try the queued rows oldest-first. Returns how many landed.

        Stops at the first REAL refusal rather than working through the rest:
        the queue refuses because it is full, so the remaining attempts would
        add to the congestion and be refused too. Order is kept so the trail
        reads in the order the changes were made.

        Never raises. It runs on the snapshot poller, where an exception is
        caught and recorded as the snapshot's last error — a receipt must not be
        able to make the floor look broken.
        """
        queued = self.pending()
        if not queued:
            return 0
        if self._ensure is not None:
            try:
                self._ensure()
            except Exception:               # a schema blip is not this row's
                pass
        landed = 0
        for row in queued:
            try:
                self._store.record(**row)
            except ValueError as exc:
                # Not writable by any retry — a number this store refuses. It
                # would block every row behind it forever, so it is dropped
                # with its contents named in the log.
                logger.warning("correction audit row for %r/%r cannot be "
                               "recorded and was dropped: %s",
                               row.get("machine_uid"), row.get("test_name"),
                               exc)
            except Exception as exc:
                if not _already_recorded(exc):
                    return landed
            self._forget(str(row.get("uid") or ""))
            landed += 1
        return landed

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
               live=None, live_token: Optional[str] = None,
               documents_root=None) -> Flask:
    # Per-app, never module-global — see throttled_warning.
    warn_seen: Dict[str, list] = {}

    app = Flask(__name__)

    # First, before anything here can have something to say. See
    # `configure_logging`: without this every warning on this branch is
    # written to a stderr that does not exist on the target platform.
    app.config["LOG_PATH"] = configure_logging()

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

    # And as `app_version()`. The SAME string /healthz reports — two version
    # stamps that can disagree is worse than one, because the one on the wall
    # is the one people quote. Deploys here are unattended: the updater swaps a
    # junction under a running service once the lab goes quiet, so nobody
    # installs a release at a moment they would remember, and "which one is
    # live?" has meant reading /healthz or the updater log.
    @app.template_global("app_version")
    def _app_version() -> str:
        return APP_VERSION

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

    def _confirmed_write(sql: str, args: Optional[list] = None, *,
                         what: str = "") -> dict:
        """One write this route issues itself, with BOTH failures named alike.

        `confirm_write(gateway.sql(...))` reads the ANSWER and leaves the CALL
        bare. A socket error, a DNS failure or a proxy 502 never produces an
        answer, so it sailed straight past every `except LabCoreError` in here
        and out as "Internal Server Error" — which tells an operator nothing
        about whether their correction factor, their completion or their
        machine deletion went through. The row is equally not written either
        way; the only difference was which words they got.

        `LabCoreUnavailable`, not `LabCoreRefused`: nothing answered, so the
        state of the write is unknown, and `_labcore_failed` says exactly that
        (503, "did not go through and its state is unknown") rather than
        "LabCore said no". The four stores already do this in their own
        `_write` helpers; this is the same rule for the writes web_app does not
        hand to a store.

        `what` is the sentence the person who clicked Save needs — it rides on
        the refusal so `refusal_response` can append it to LabCore's own
        reason. `check_write`, not `confirm_write`, for exactly that: the
        DECISION is the same rule either way (`check_write` delegates it), but
        only the gateway's exception carries `what` out to the error handler,
        and "the queue is deep" on its own does not tell a supervisor that
        their correction factor is not in force.
        """
        try:
            res = gateway.sql(sql, args or [])
        except Exception as exc:                    # transport, not logic
            raise LabCoreUnavailable(
                "LabCore could not be written to ({0}: {1})".format(
                    type(exc).__name__, exc)) from exc
        return check_write(res, what=what)

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
    #
    # Both of these render the stored AppConfig, and `DbConfigStore.load()`
    # raises now instead of answering `{}` (2026-08-25). It has to: the same
    # object feeds `/api/boxes`, which saves it straight back, and the save
    # prunes to match. What that costs HERE is only a status code, and the
    # alternative is the V4 dashboard drawing a lab with every instrument
    # retired because a queue was busy for eight seconds.
    @app.route("/api/status")
    def api_status():
        try:
            return jsonify(provider.build_snapshot())
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "the lab's configuration")

    @app.route("/api/config")
    def api_config():
        try:
            return jsonify(serialize_config(provider.load_config()))
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "the lab's configuration")

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
        try:
            # THE READ THAT DECIDES A WRITE. `save()` rewrites each list table
            # to match what it is handed — upsert the wanted rows, delete the
            # rest — so a config degraded to `{}` by a failed read is an
            # instruction to delete every QC standard, user and checklist in
            # the lab. Adding one box must never be able to do that, which is
            # why `load()` raises and this does not paper over it.
            cfg = provider.load_config()
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "the lab's configuration")
        box = BoxConfig(
            uid=body.get("uid") or f"box_{uuid.uuid4().hex[:12]}",
            title=str(body.get("title") or "New Equipment"),
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
        try:
            return jsonify(provider.build_snapshot())
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "the lab's configuration")

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
            throttled_warning(logger, warn_seen, "map-lock-unreadable",
                              "map lock unreadable, defaulting to locked: %s",
                              exc)
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
                                              STATUS_COLORS,
                                              last_qc=_mirrored_last_qc()))
    # Deliberately NOT started here. An app factory that spawns a thread gives
    # every caller a background refresher it did not ask for — and the old
    # `if not app.config["TESTING"]` guard could never work, because a test sets
    # TESTING on the object this function has already returned. The entry point
    # owns the lifecycle: web_server.pyw calls start(). Without a poller,
    # refresh_soon() refreshes inline, so behaviour stays correct either way.
    app.config["SNAPSHOTS"] = snapshots

    def _mirrored_last_qc():
        """The newest QC verdict per (machine, test) from the local log copy.

        None on ANY doubt — no mirror yet, nothing pulled, or a read that
        failed. None means unknown to `build_machines`, which then leaves each
        bench's published spec exactly as it is. Returning {} instead would
        say "no QC has ever been run on anything" and blank the QC panel for
        the whole lab the first time this copy was cold, which is the
        failed-read-is-not-an-empty-result rule in its most expensive form.
        """
        mirror = app.config.get("LOG_MIRROR")
        if mirror is None:
            return None
        try:
            if not mirror.state().get("rows"):
                return None
            return mirror.latest_qc()
        except Exception:
            return None

    # ── the local copy of the log ─────────────────────────────────────────
    #
    # Ryan: "cant it pull it every 5 minutes? and just keep it local?" — asked
    # about the History and Logs pages showing the whole record instead of the
    # newest page of it, and it is the right shape.
    #
    # WHERE IT IS USED, and where it deliberately is not. A plain open of
    # either page still reads LabCore, so what you see when the panel appears
    # is current to the second. The mirror serves the DEEP requests — the
    # walk backwards (`before=`) and `limit=all` — which is exactly where the
    # cost was: one instrument's 26,106 rows measured 2.23s / 13.8 MB, and
    # every one of those seconds is a write slot the benches are queued behind.
    # Paying it once every five minutes instead of once per click is the whole
    # point; paying it for the first page nobody complained about would be
    # trading freshness for nothing.
    #
    # Same lifecycle rule as the snapshot: constructed here, started by the
    # entry point, and correct with no thread at all — an unfilled mirror falls
    # back to reading LabCore rather than reporting a lab with no history.
    from log_mirror import LogMirror, LogMirrorService
    log_mirror = LogMirror(
        gateway,
        path=os.path.join(documents_root or os.path.join(APP_DIR, "data"),
                          "log-mirror.sqlite3"))
    app.config["LOG_MIRROR"] = log_mirror
    app.config["LOG_MIRROR_SERVICE"] = LogMirrorService(
        log_mirror,
        seconds=float(os.environ.get("LEM_LOG_MIRROR_SECONDS", "300")))

    # ── the equipment record: levels, documents, corrective actions ────
    #
    # Three stores that shipped fully tested and connected to nothing. Their
    # tables are declared centrally by `snapshots.ensure_schema()` (see
    # snapshot_service.SCHEMA_DDL) — none of them creates a table on demand,
    # which is the pattern that dropped the floor once.
    #
    # Constructed here and nothing more: no directory is made, no read is
    # issued, no thread is started. The factory stays side-effect free, which
    # is the lesson the snapshot poller taught.
    from equipment_documents import (DocumentError, DocumentRejected,
                                     DocumentStoreError,
                                     EquipmentDocumentStore, MAX_DOCUMENT_BYTES,
                                     content_disposition,
                                     document_counts_by_machine, read_upload)
    from equipment_history import (ALL as HISTORY_ALL, ActionLifecycleError,
                                   CorrectionAuditStore, EquipmentHistory)
    from levels import LevelStore

    from standard_documents import (CertificateRejected, CertificateStoreError,
                                    StandardCertificateStore,
                                    content_disposition, expiry_report)
    import uncertainty
    from uncertainty import EstimateRefused, Exclusion, UncertaintyStore

    level_store = LevelStore(gateway)
    document_store = EquipmentDocumentStore(gateway, root=documents_root)
    # The certificate a QC standard's values rest on. Same root as the
    # equipment documents — one place a deploy has to preserve, not two — and
    # the store keeps them apart underneath by its own folder scheme.
    certificate_store = StandardCertificateStore(gateway, root=documents_root)
    # Frozen measurement-uncertainty records. Deliberately NOT a snapshot arm:
    # every arm is bought with the whole floor's two-second read, and this is
    # opened by a person preparing for an assessment, not polled.
    uncertainty_store = UncertaintyStore(gateway)
    equipment_history = EquipmentHistory(gateway)
    correction_audit = CorrectionAuditStore(gateway)

    # The bytes never go through LabCore's queue, so the ceiling is about this
    # process's memory and a Windows box that is also drawing the floor.
    # `MAX_CONTENT_LENGTH` is the only thing that stops an upload before it
    # reaches Python at all; `read_upload` bounds what happens after that. The
    # slack is for the multipart envelope around a file exactly at the limit.
    app.config["MAX_DOCUMENT_BYTES"] = MAX_DOCUMENT_BYTES
    app.config.setdefault("MAX_CONTENT_LENGTH", MAX_DOCUMENT_BYTES + (1 << 20))

    @app.errorhandler(413)
    def _too_large(_exc):
        """Werkzeug's own 413 is an HTML page, and every caller here reads JSON.

        A drag-and-drop that answers with a page the uploader cannot parse looks
        to the operator exactly like an upload that hung.
        """
        return jsonify({
            "error": "That file is larger than LEM stores ({0} MB); nothing "
                     "was uploaded.".format(MAX_DOCUMENT_BYTES // (1 << 20)),
            "saved": False, "retry": False}), 413

    audit_spool = CorrectionAuditSpool(correction_audit,
                                       ensure_schema=snapshots.ensure_schema)
    app.config["AUDIT_SPOOL"] = audit_spool
    # The retry rides the thread that is already awake and already talking to
    # LabCore, exactly as `LiveConfigPublisher` does. A spool that only drained
    # when somebody happened to change another correction factor would sit
    # there for weeks on a bench nobody touches — which is the same lost record
    # in slower motion. web_server.pyw CHAINS onto this rather than replacing
    # it; see the note there.
    def _on_cycle():
        """Everything that rides the poller's thread, in one place.

        CHAINED, never assigned over — `web_server.pyw` chains the live
        publisher onto this same hook, and replacing it rather than wrapping it
        would strip the audit retry from every production server while leaving
        it working in dev and in tests.

        Each rider is isolated: the search corpus failing must not stop the
        audit spool draining, and vice versa. This is the only thread that
        talks to LabCore on a timer, so a raise here would end all of it.
        """
        for name, rider in (("audit spool", audit_spool.drain),
                            ("search corpus", _refresh_search_corpus)):
            try:
                rider()
            except Exception:                       # noqa: BLE001
                logger.exception("%s failed on the snapshot cycle", name)

    snapshots.on_cycle = _on_cycle

    def _equipment_gate(machine_uid: str):
        """`None` if this instrument exists, else the answer to send instead.

        LabCore has no foreign keys: a document or a corrective action written
        against a uid nothing else knows is ACCEPTED, and then unreachable
        forever — there is no page that lists it and no join that would ever
        surface it again.

        And the two failures are kept apart on purpose. "There is no such
        instrument" sends somebody to look for a bench that is standing right
        in front of them; a read that could not be made says "try again in a
        moment". `_titles()` raises rather than degrading for exactly this, and
        it is served from the snapshot, so the gate costs no LabCore op.
        """
        try:
            known = _titles()
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "the equipment list")
        if str(machine_uid or "").strip() not in known:
            return jsonify({"error": "No such equipment."}), 404
        return None

    def _record_correction_change(machine_uid: str, test_name: str, previous,
                                  new_value, units: str = "",
                                  reason: str = "") -> bool:
        """The §7.8.2 receipt for a correction factor that has just changed.

        THE REASON `CorrectionAuditStore` EXISTS, and the gap does not close
        until the thing that destroys the old value calls it.
        `lem_correction_factors` is an UPSERT keyed on (machine_uid,
        test_name), so a save overwrites the previous offset and nothing else
        anywhere records what it was, when it changed, or who changed it —
        while that number is added to every result the bench reports.

        Not a replacement for `_audit`'s `lem_machine_log` line, and both are
        written. The log line is what the logs page and the machine's own
        history show, and it can be PURGED with the machine; this table is
        append-only, typed (previous/new as numbers rather than text in a JSON
        blob) and queryable per test. Only one of them is the compliance trail.

        Never raises: the operator's change has already landed and refusing it
        now would be a lie in the other direction. A refusal is spooled,
        retried, logged by name, and carried out to the caller by
        `_report_unrecorded_audit`.
        """
        row = {
            "machine_uid": machine_uid, "test_name": test_name,
            "previous": previous, "new_value": new_value,
            "units": str(units or ""), "by": session.get("user", ""),
            "reason": str(reason or ""),
            "when": _now().isoformat(timespec="seconds"),
            # Minted once and kept across retries, so a row whose answer never
            # came back cannot be written twice. See `_already_recorded`.
            "uid": uuid.uuid4().hex,
        }
        # The retry path. Cheap when the spool is empty, and it means a lab that
        # is using the app is also draining it.
        audit_spool.drain()
        try:
            snapshots.ensure_schema()
            correction_audit.record(**row)
            return True
        except ValueError as exc:
            # A number this store refuses. Spooling it would queue a row no
            # retry can ever write.
            logger.warning("correction audit for %r on %r was not recorded: %s",
                           test_name, machine_uid, exc)
        except Exception as exc:
            logger.warning("correction audit for %r on %r was refused and is "
                           "held for retry: %s", test_name, machine_uid, exc)
            audit_spool.add(row)
        try:
            g._lem_audit_failed = True
        except RuntimeError:                # outside a request context
            pass
        return False

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
        # Whether LabCore ever accepted our CREATEs and ALTERs. Deliberately
        # NOT part of `status`: a degraded schema still serves a usable floor,
        # and answering 500 would fail a release that works. But it must be
        # VISIBLE — a refused CREATE at boot drops the snapshot onto the
        # fifteen-read fallback path and can leave `correction` missing from
        # lem_machine_specs, and both of those are invisible from the outside.
        # RELEASING.md §5: nothing else in the pipeline catches a release that
        # starts perfectly and shows the wrong thing.
        schema_ok = getattr(snapshots, "schema_ready", True)
        # "unknown", not "degraded", before the first refresh. `schema_ready`
        # is False on a server that has simply not looked yet — which is
        # exactly when the updater probes a candidate on its scratch port — and
        # answering "degraded" with an EMPTY `schema_error` there blocks a
        # release that works. A health check that cries wolf gets ignored, and
        # it takes the real signal with it.
        checked = getattr(snapshots, "schema_checked", True)
        schema = "ok" if schema_ok else ("degraded" if checked else "unknown")
        return jsonify({
            "status": "ok",
            "version": APP_VERSION,
            "labcore": "unknown" if online is None else (
                "reachable" if online else "unreachable"),
            "schema": schema,
            "schema_error": getattr(snapshots, "schema_error", ""),
            # Correction-factor audit rows LabCore would not take yet
            # (ISO/IEC 17025 §7.8.2). Reported as a COUNT and a date rather
            # than folded into `status`: the app is working and the release is
            # good, but a compliance trail with a backlog is a fact somebody
            # has to be able to see without reading a log file. Zero is the
            # normal answer, and it is the answer on a lab that has never saved
            # a correction — which is why it does not gate the health check.
            "audit_spool": len(audit_spool),
            "audit_spool_oldest": audit_spool.oldest(),
            "pid": os.getpid(),
            # Seconds since a person last did something. Wall displays polling
            # and benches pushing do not count - see _is_background.
            "idle_seconds": round(time.time() - _last_activity, 1),
            "last_activity": _last_activity_path,
            # Where the refusals are written down. This server has no console
            # to print it to, and a log nobody can find is the void with an
            # extra step. "" means it could not be opened.
            "log": app.config.get("LOG_PATH", ""),
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
            #
            # But it has to SAY it is guessing. `/api/map` already ships
            # `known: false` for its own fallback, and this one had no flag at
            # all — so a lab that works Saturdays saw its own hours quietly
            # replaced by Mon–Fri, and every silent module on a Saturday was
            # reported `closed` rather than `stopped`. Degrading is a judgement
            # call; degrading invisibly is the bug this branch is about.
            try:
                loaded = schedule_store.load()
                known = True
            except LabCoreError as exc:
                logger.warning("lab schedule unreadable, showing the default "
                               "week: %s", exc)
                from lab_schedule import LabSchedule as _LabSchedule
                loaded, known = _LabSchedule(), False
            body = loaded.to_dict(_now())
            body["known"] = known
            return jsonify(body)
        # Same check as /api/maintenance: the snapshot tolerates one failed
        # arm, so `sched: []` here can mean "the lab has never set its hours"
        # OR "that read timed out", and only one of those honestly draws a
        # Mon-Fri week. The floor is allowed to keep drawing one either way —
        # it just has to admit which.
        unread = (snapshots.table_error("sched")
                  or snapshots.table_error("holiday"))
        body = schedule_from_tables(tables).to_dict(_now())
        body["known"] = not unread
        return jsonify(body)

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
                           Tracked, TrackedStore, active_items, completion,
                           normalise_tracked_name)

    checklist_store = ChecklistStore(gateway)
    tracked_store = TrackedStore(gateway)

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

    # ── the things a round measures ──────────────────────────────────
    #
    # Ryan: "Opening and closing need to intersect. So we have to make the
    # things that are trending into an object (with a minimum and a maximum
    # volume) and then you can put in 'track' and select the object you want
    # to track."
    #
    # A reading never moves. It stays in `lem_checklist_state` keyed by
    # (checklist_uid, item_uid); what changes is that the trends read groups on
    # what an item TRACKS. So the conversion is reversible and no history is
    # rewritten, which is the only safe way to do this to a compliance record.

    def _tracked_number(body, key):
        raw = body.get(key)
        if raw is None or str(raw).strip() == "":
            return None
        return float(str(raw).strip())

    @app.route("/api/tracked")
    def api_tracked_list():
        try:
            return jsonify({"tracked": [t.to_dict()
                                        for t in tracked_store.all()]})
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "the tracked items")

    @app.route("/api/tracked", methods=["POST"])
    @app.route("/api/tracked/<uid>", methods=["POST"])
    def api_tracked_save(uid=""):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        existing = None
        if uid:
            try:
                existing = tracked_store.get(uid)
            except LabCoreError as exc:
                return _labcore_unreadable(exc, "that tracked item")
            if existing is None:
                return jsonify({"error": "No such tracked item."}), 404
        try:
            # An edit leaves out what it is not changing; a create starts blank.
            lo = (_tracked_number(body, "min") if "min" in body
                  else (existing.min_value if existing else None))
            hi = (_tracked_number(body, "max") if "max" in body
                  else (existing.max_value if existing else None))
        except ValueError:
            return jsonify({"error": "A minimum and a maximum have to be "
                                     "numbers, or left blank."}), 400
        name = body.get("name", existing.name if existing else "")
        units = body.get("units", existing.units if existing else "")
        try:
            saved = tracked_store.save(
                Tracked(uid=uid, name=str(name or ""), units=str(units or ""),
                        min_value=lo, max_value=hi),
                who=session.get("user", ""))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except LabCoreError as exc:
            return _labcore_failed(exc, "that tracked item")
        _audit("tracked saved", "", {"tracked": saved.uid, "name": saved.name,
                                     "min": saved.min_value,
                                     "max": saved.max_value})
        return jsonify({"ok": True, "tracked": saved.to_dict()})

    @app.route("/api/tracked/<uid>", methods=["DELETE"])
    def api_tracked_delete(uid):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        try:
            gone = tracked_store.delete(uid)
        except LabCoreError as exc:
            return _labcore_failed(exc, "that tracked item")
        # Items still pointing at it fall back to their own series, which is
        # the pre-conversion behaviour rather than a hole.
        _audit("tracked deleted", "", {"tracked": uid})
        return jsonify({"ok": True, "deleted": gone})

    @app.route("/api/checklists/<uid>/track", methods=["POST"])
    def api_checklist_track(uid):
        """Point one checklist item at a tracked thing, or unpoint it."""
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        item_uid = str(body.get("item_uid") or "").strip()
        tracked_uid = str(body.get("tracked_uid") or "").strip()
        try:
            checklist = checklist_store.get(uid)
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "that checklist")
        if checklist is None:
            return jsonify({"error": "No such checklist."}), 404
        item = next((i for i in checklist.items if i.uid == item_uid), None)
        if item is None:
            return jsonify({"error": "No such item."}), 404
        if item.entry_type != "number":
            return jsonify({"error": f"“{item.text}” does not record a "
                                     f"number, so there is nothing to "
                                     f"track."}), 400
        if tracked_uid:
            try:
                if tracked_store.get(tracked_uid) is None:
                    return jsonify({"error": "No such tracked item."}), 404
            except LabCoreError as exc:
                return _labcore_unreadable(exc, "that tracked item")
        item.track_uid = tracked_uid
        try:
            checklist_store.save(checklist)
        except LabCoreError as exc:
            return _labcore_failed(exc, "that checklist")
        _audit("checklist item tracked", "",
               {"checklist": checklist.name, "item": item.text,
                "tracked": tracked_uid})
        return jsonify({"ok": True, "checklist": checklist.to_dict()})

    @app.route("/api/tracked/convert", methods=["POST"])
    def api_tracked_convert():
        """Turn the numeric items already on the rounds into tracked things.

        Ryan: "convert the exist checklists with matching names into those
        objects with history." Items are grouped by name with case and spacing
        normalised — somebody typed "Nitrogen  Pressure" into one round and
        "nitrogen pressure" into another, months apart, and they are the same
        cylinder.

        NO READING IS TOUCHED. The items are pointed at a new object; the merge
        happens on read. `dry_run` reports what it would do and writes nothing,
        because this runs against a live compliance record.
        """
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        dry = bool(body.get("dry_run"))
        try:
            lists = checklist_store.all()
            existing = {normalise_tracked_name(t.name): t
                        for t in tracked_store.all()}
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "the rounds")

        groups = {}
        for cl in lists:
            for item in cl.items:
                if item.entry_type != "number":
                    continue           # a note is not a thing to track
                key = normalise_tracked_name(item.text)
                if not key:
                    continue
                groups.setdefault(key, []).append((cl, item))

        would_create = [k for k in groups if k not in existing]
        if dry:
            return jsonify({
                "dry_run": True,
                "would_create": len(would_create),
                "would_link": sum(len(v) for v in groups.values()),
                "groups": [{"name": groups[k][0][1].text,
                            "items": [{"checklist": cl.name, "slot": cl.slot,
                                       "text": it.text, "units": it.units}
                                      for cl, it in groups[k]]}
                           for k in sorted(groups)],
            })

        created, linked, touched = 0, 0, {}
        for key, members in groups.items():
            tracked = existing.get(key)
            if tracked is None:
                first = members[0][1]
                try:
                    tracked = tracked_store.save(
                        Tracked(name=first.text, units=first.units),
                        who=session.get("user", ""))
                except (ValueError, LabCoreError) as exc:
                    return _labcore_failed(exc, "a tracked item") \
                        if isinstance(exc, LabCoreError) else \
                        (jsonify({"error": str(exc)}), 400)
                existing[key] = tracked
                created += 1
            for cl, item in members:
                if item.track_uid == tracked.uid:
                    continue           # already pointed there; re-runnable
                item.track_uid = tracked.uid
                touched[cl.uid] = cl
                linked += 1
        try:
            for cl in touched.values():
                checklist_store.save(cl)
        except LabCoreError as exc:
            return _labcore_failed(
                exc, "the rounds",
                "Some items may already point at their tracked thing. "
                "Re-running is safe — it skips the ones already linked.")
        _audit("tracked converted", "", {"created": created, "linked": linked})
        return jsonify({"ok": True, "created": created, "linked": linked})

    @app.route("/api/checklists/trends")
    def api_checklist_trends():
        """Every numeric checklist item and its readings, in one answer.

        Ryan: "put a page of the checklists page with all the checklist
        'trend' items being visible as like a dashboard too."

        ONE READ, not one per item. Twenty numeric items across four rounds is
        twenty LabCore ops if each trend fetches itself, behind the same queue
        the benches write through, on a page somebody leaves open.

        IT DOES NOT INVENT A VERDICT. No checklist item has a spec band —
        nobody has told LEM what a good nitrogen pressure is — so nothing here
        is coloured pass or fail. What it can say honestly is movement and
        recency, and an item nobody has written down is the finding this page
        exists to make.
        """
        try:
            lists = checklist_store.all()
            readings = checklist_store.all_values()
            tracked = {t.uid: t for t in tracked_store.all()}
        except LabCoreError as exc:
            # A flat, empty trend is a claim about a cylinder nobody has been
            # reading. The per-item route already refuses rather than draw one;
            # the dashboard must not undo that.
            return _labcore_unreadable(exc, "the checklist readings")

        # ONE SERIES PER THING, not per line of a round.
        #
        # An item pointing at a tracked thing contributes its readings to that
        # thing's series; an item pointing at nothing keeps its own, exactly as
        # before. Nothing was moved to make this true — the readings are still
        # filed under the item that recorded them, and this is the read that
        # puts them together.
        merged = {}
        for cl in lists:
            for item in cl.items:
                if item.entry_type != "number" or not item.track_uid:
                    continue
                thing = tracked.get(item.track_uid)
                if thing is None:
                    continue           # deleted: falls back to its own series
                slot = merged.setdefault(thing.uid, {
                    "thing": thing, "points": [], "rounds": []})
                # WHICH ROUND a reading came from, carried on the point.
                #
                # A reading records a `day` and no time, so two readings on
                # one day cannot be ordered by the data alone — and the whole
                # point of merging opening and closing is that a day now HAS
                # two. The round is the only thing that knows which came
                # first, so it rides along and orders them.
                for point in readings.get((cl.uid, item.uid), []):
                    slot["points"].append(
                        dict(point, round=cl.name, slot=cl.slot))
                if cl.name not in slot["rounds"]:
                    slot["rounds"].append(cl.name)

        #: When in the day a round happens. Only the order matters, and an
        #: unrecognised slot sits between the two named ones rather than
        #: claiming to be first or last.
        SLOT_ORDER = {"opening": 0, "morning": 1, "midday": 4, "other": 5,
                      "afternoon": 7, "evening": 8, "closing": 9}

        out = []
        for uid, slot in merged.items():
            thing = slot["thing"]
            points = sorted(
                slot["points"],
                key=lambda p: (p["day"],
                               SLOT_ORDER.get(str(p.get("slot") or ""), 5),
                               str(p.get("round") or "")))
            last = points[-1] if points else None
            out.append({
                "tracked_uid": uid,
                "checklist": " · ".join(slot["rounds"]),
                "rounds": slot["rounds"],
                "slot": "tracked",
                "item_uid": "",
                "text": thing.name,
                "units": thing.units,
                "min": thing.min_value,
                "max": thing.max_value,
                # Limits somebody entered are not LEM inventing one, so this
                # may now say what a reading IS. Without limits it still says
                # nothing, which is the rule that has not changed.
                "state": thing.judge(last["value"]) if last
                         else ("NO READING" if (thing.min_value is not None
                                                or thing.max_value is not None)
                               else "NO LIMITS SET"),
                "points": points,
                "n": len(points),
                "last_value": last["value"] if last else None,
                "last_at": last["day"] if last else "",
                "last_by": last["user"] if last else "",
                "first_at": points[0]["day"] if points else "",
            })

        for cl in lists:
            for item in cl.items:
                if item.entry_type != "number":
                    continue           # a note is not a series
                if item.track_uid and item.track_uid in tracked:
                    continue           # counted once, under the thing it feeds
                points = readings.get((cl.uid, item.uid), [])
                last = points[-1] if points else None
                first = points[0] if points else None
                out.append({
                    "checklist_uid": cl.uid,
                    "checklist": cl.name,
                    "slot": cl.slot,
                    "item_uid": item.uid,
                    "text": item.text,
                    "units": item.units or "",
                    "points": points,
                    "n": len(points),
                    "last_value": last["value"] if last else None,
                    "last_at": last["day"] if last else "",
                    "last_by": last["user"] if last else "",
                    "first_at": first["day"] if first else "",
                })

        # Never written comes first: it is the only thing on this page that is
        # a finding rather than a reading. After that, oldest reading first —
        # the one drifting out of anybody's attention.
        out.sort(key=lambda t: (1 if t["n"] else 0, t["last_at"], t["text"]))
        return jsonify({"trends": out, "day": _today(),
                        "counts": {"items": len(out),
                                   "never_written": sum(1 for t in out
                                                        if not t["n"])}})

    @app.route("/checklists/trends")
    def page_checklist_trends():
        return render_template("checklist_trends.html")

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
                body_json, status, headers = _labcore_failed(
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
                return jsonify(data), status, headers
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
                data, status, headers = _labcore_failed(
                    exc, "the imported history")
                payload = data.get_json()
                payload.update({"count": len(found), "checklists": preview,
                                "history_rows": 0,
                                "history_days": history_days,
                                "incomplete": True, "dry_run": False,
                                "error": "The rounds imported, but LabCore "
                                         "stopped accepting the historic "
                                         "ticks. Run the import again to "
                                         "finish it — nothing is duplicated."})
                return jsonify(payload), status, headers
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
                            "warming": True, "age_seconds": None,
                            # Same keys as the ready answer, so the page has
                            # one shape to read rather than two. Empty is the
                            # truth here: nothing has been read yet.
                            "levels": [], "default_level": "",
                            "ground_level": ""})
        # The live road overlays the record where a bench has spoken for itself
        # more recently than the queue could carry it. Failover, not merge —
        # see live_presence.merge_machines.
        from live_presence import merge_machines
        return jsonify({"machines": merge_machines(snap.get("machines") or [],
                                                   app.config["LIVE"],
                                                   STATUS_COLORS),
                        "labcore_online": snap.get("labcore_online", True),
                        "age_seconds": snap.get("age_seconds"),
                        "stale": snap.get("stale", False),
                        # The ladder travels with the fleet, out of the same
                        # snapshot, at the same zero LabCore ops — the floor
                        # draws a level picker without asking anything.
                        "levels": snap.get("levels") or [],
                        "default_level": snap.get("default_level") or "",
                        "ground_level": snap.get("ground_level") or ""})

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
        try:
            locked = map_settings.locked()
        except LabCoreError as exc:
            # The lock is a permission check, so an unreadable one cannot be
            # assumed open — and a drag saved past it would be refused by the
            # same LabCore anyway.
            return _labcore_failed(exc, "this equipment's position")
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
            return _labcore_failed(exc, "this equipment's position")
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
                exc, "this equipment's QC assignment",
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
                                            "equipment")
        if refusal is not None:
            return refusal

        # Retiring a machine is seven separate writes into a queue that takes
        # one statement at a time, so it CANNOT be atomic. What it can be is
        # honest: each step is confirmed, and the first refusal stops the
        # sequence and reports exactly how far it got. Every step is a DELETE,
        # so pressing Delete again finishes the job — which is why stopping is
        # better than ploughing on and reporting a clean retirement over a
        # machine that still has its configuration.
        # BEFORE the first DELETE: a DELETE on a table LabCore has never
        # created answers with an error `refusal_reason` cannot tell from a
        # refusal, so in a lab where nobody had ever set an override a good
        # retirement came back 502.
        snapshots.ensure_schema()

        def _drop(table):
            def go():
                try:
                    _confirmed_write(
                        "DELETE FROM {0} WHERE machine_uid = ?".format(table),
                        [machine_uid])
                except LabCoreRefused as exc:
                    # The one refusal that honestly means "already gone": a
                    # table nothing has ever written to holds no rows for this
                    # machine, so there is nothing left to retire. Every other
                    # refusal stops the sequence.
                    if not is_missing_table(exc):
                        raise
            return go

        def _tolerating_missing(run):
            """`_drop`'s exemption, for the steps that go through a store.

            A table nothing has ever written to holds no rows for this machine,
            so there is nothing left to retire — the same one refusal `_drop`
            already forgives, spelled once rather than twice. Every OTHER
            refusal still stops the sequence, because ploughing on would report
            a clean retirement over a machine that still has half its record.
            """
            def go():
                try:
                    return run()
                except LabCoreError as exc:
                    if not is_missing_table(exc):
                        raise
            return go

        def _forget_documents():
            """The document store speaks its own exception; this sequence
            catches `LabCoreError`.

            Translated rather than caught-and-ignored, because the two halves
            fail differently and only one of them is safe to shrug at: LabCore
            refusing the metadata delete leaves rows that will list documents
            for a machine nobody can open, which has to stop the retirement the
            way every other refused step does. A disk that could not be written
            leaves files nothing references — the orphan this store is designed
            to be able to live with — but the rows are already gone by then, so
            it reaches here only when the metadata itself failed.
            """
            try:
                return document_store.delete_for_machine(machine_uid)
            except DocumentStoreError as exc:
                cause = getattr(exc, "__cause__", None)
                if isinstance(cause, LabCoreError):
                    raise cause
                raise LabCoreUnavailable(str(exc)) from exc

        # ONE label per step, used for `landed`/`not_landed`, for `removed`
        # and for `stopped_at`. Two vocabularies for the same ten steps is how
        # a client ends up matching on one list and rendering the other, and
        # these exact words are what `test_refused_writes` pins.
        steps = [
            ("live status", _drop("lem_machine_status")),
            ("QC specs", _drop("lem_qc_specs")),
            ("manual override", _drop("lem_machine_control")),
            ("position on the floor",
             lambda: layout_store.forget(machine_uid)),
            ("QC assignments", lambda: target_store.forget(machine_uid)),
            # Orphaned PM rows re-attach if that uid is ever registered again,
            # so this failing has to fail the delete rather than be skipped.
            ("PM and calibration tasks",
             lambda: maint_store.forget(machine_uid)),
            # A stranded config would offer itself again in the module's picker.
            ("configuration", lambda: config_store.delete(machine_uid)),
            # With no foreign keys, a placement left behind re-attaches itself
            # if that uid is ever registered again — the instrument would come
            # back standing on a level nobody put it on. `forget`, not
            # `unassign`: no history line, because the history is either about
            # to be purged or is being kept for a machine that no longer exists.
            ("level",
             _tolerating_missing(lambda: level_store.forget(machine_uid))),
            # ONE delete for the whole set, not one per document: the queue
            # serialises at ~1.5 ops/sec in front of every QC verdict the floor
            # is writing, and a unit with a dozen certificates would hold that
            # up for the better part of ten seconds. The bytes go after the
            # rows, so the worst case is files nothing references —
            # `orphaned_files()` finds those, and a row with no file is the one
            # this store refuses to create.
            ("documents", _tolerating_missing(_forget_documents)),
        ]
        if body.get("purge_history"):
            steps.append(("history", _drop("lem_machine_log")))

        removed = []
        for index, (label, step) in enumerate(steps):
            try:
                step()
            except LabCoreError as exc:
                snapshots.refresh_soon()
                # Audited even though the retirement failed: a half-retired
                # machine is exactly the state someone will need explained.
                _audit("machine delete incomplete", machine_uid,
                       {"removed": removed, "stopped_at": label})
                # NOT_LANDED IS THE WHOLE REMAINDER, not just the step that
                # was refused. The sequence stops on the first no rather than
                # pushing the other statements into a queue that has just said
                # it is too deep, so everything from here down is equally
                # still there — and a report naming only the refused step
                # would read as "one thing left", which is what sends somebody
                # away from a half-retired machine.
                return _labcore_failed(
                    exc, "{0} of “{1}”".format(label, machine_uid),
                    "{0} removed before it stopped. Press Delete again to "
                    "finish — it picks up where it left off.".format(
                        (", ".join(removed) or "Nothing").capitalize()),
                    landed=list(removed),
                    not_landed=[name for name, _run in steps[index:]],
                    removed=list(removed), stopped_at=label)
            removed.append(label)
        # The bench still holds an override for a machine that is gone.
        app.config["LIVE"].mark_stale(machine_uid, STALE_OVERRIDE)
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
            return _labcore_unreadable(exc, "the equipment configurations")
        for row in configs:
            beat = beats.get(row["machine_uid"]) or {}
            row["last_poll"] = beat.get("last_poll")
            row["in_use"] = _beat_is_fresh(beat.get("last_poll"))
        return jsonify({"configs": configs})

    @app.route("/api/machine-configs/<machine_uid>")
    def api_get_machine_config(machine_uid):
        try:
            record = config_store.get(machine_uid)
        except MachineConfigError as exc:
            # THE 404 TRAP. "Could not ask" served as "does not exist" is how a
            # module that has been parsing all morning is told it was never
            # configured — and it would then offer to make a second one.
            #
            # `MachineConfigError`, not `ConfigReadUnavailable`: the narrower
            # catch left every other way this can fail — a refusal, a raised
            # transport error — falling through as a bare 500, and "Internal
            # Server Error" answers the module's question no better than a 404
            # does. `_labcore_unreadable` already tells a refusal (502) from an
            # outage (503), so one catch loses nothing.
            return _labcore_unreadable(exc, "that equipment's configuration")
        if record is None:
            return jsonify({"error": "No configuration for that equipment."}), 404
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
            return _labcore_failed(exc, "the new equipment configuration")
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
            return _labcore_failed(exc, "this equipment's configuration")
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
            return _labcore_unreadable(exc, "this equipment's PM and "
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
        # Raises if refused; the schedule has NOT moved and the handler says
        # so, rather than the floor showing the task as done for a write that
        # never happened.
        maint_store.complete(uid, when, note)
        snapshots.refresh_soon()
        # The completion belongs in the machine's history too. Second
        # statement, no transaction — so if this one is refused the schedule
        # HAS moved and the history row is missing.
        #
        # AND THAT IS A 200. The reschedule has already landed and cannot be
        # taken back, so answering "not saved" would be false about the half
        # that did happen — and it would send the operator to press Complete
        # again, which moves the due date a second time and logs it twice.
        # `logged: false` plus the sentence is the honest shape, and it is the
        # one both pages that can complete a task actually render
        # (`out.logged === false` in floor.html, `b.logged === false` in
        # maintenance.html). Silence is what is forbidden here, not the 200.
        #
        # `Exception`, not `LabCoreError`: a client that RAISES never produced
        # an answer, and the history row is equally missing either way. Letting
        # that escape turned a completion that DID happen into "Internal Server
        # Error".
        logged = True
        warning = ""
        # The DDL stays unguarded on purpose — see CLAUDE.md: a declaration is
        # retried on the next call and a refusal surfaces on the data write
        # immediately below.
        try:
            gateway.sql(
                "CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
                "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
                "detail TEXT)")
            _confirmed_write(
                "INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
                "test_name, value, detail) VALUES (?, ?, ?, '', '', '', ?)",
                [task.machine_uid, datetime.now().isoformat(), task.kind,
                 json.dumps({"task": task.name, "completed": when,
                             "note": note,
                             "by": session.get("user", "")})])
        except Exception as exc:
            logged = False
            warning = (
                f"“{task.name}” was marked done and its schedule moved, but "
                f"the completion did not reach the machine's history "
                f"({exc}). The PM record an auditor reads will not show this "
                f"one — add it by hand, or complete it again once LabCore has "
                f"caught up and delete the duplicate.")
            logger.warning("completion of %r on %r was not logged: %s",
                           task.name, task.machine_uid, exc)
            return jsonify({"ok": True, "logged": False, "warning": warning})
        return jsonify({"ok": True})

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
            return True
        except LabCoreError as exc:
            logger.warning("audit line for %r on %r was not recorded: %s",
                           action, machine_uid or "-", exc)
        except Exception as exc:               # a gateway that raises outright
            logger.warning("audit line for %r on %r failed: %s",
                           action, machine_uid or "-", exc)
        # AND SAY SO ON THE WAY OUT (2026-08-25). A warning was the whole
        # report, and on the target platform it went to a stderr that does not
        # exist — so the operator's change succeeded, the route answered a
        # clean `{"ok": true}`, and the record of WHO changed a correction
        # factor was simply gone. Still not an exception: the change already
        # happened and refusing it now would be a lie in the other direction.
        # `_report_unrecorded_audit` turns this flag into a line in the answer.
        try:
            g._lem_audit_failed = True
        except RuntimeError:                   # outside a request context
            pass
        return False

    AUDIT_LOST = ("This change was made, but LabCore refused the audit line "
                  "that records who made it — so it will not appear in the "
                  "log. LabCore's write queue is busy; nothing needs redoing.")

    @app.after_request
    def _report_unrecorded_audit(response):
        """Carry a failed audit write out to whoever made the change.

        One hook rather than a return value threaded through twenty routes:
        every one of them already answers JSON, and the fact is the same
        wherever it happens. Only successful JSON answers are touched — an
        error response already has the operator's attention, and a CSV or a
        template must not have a key spliced into it.
        """
        if not getattr(g, "_lem_audit_failed", False):
            return response
        if response.status_code >= 400 or response.direct_passthrough:
            return response
        if response.mimetype != "application/json":
            return response
        body = response.get_json(silent=True)
        if not isinstance(body, dict) or "warning" in body:
            return response
        body["audit"] = False
        body["warning"] = AUDIT_LOST
        response.set_data(json.dumps(body))
        return response

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
        # `all` reads the whole table. The 5000 ceiling meant a lab with a
        # longer log could not reach the rest of it from this page at all —
        # and the database was never the reason: 41,903 rows measured at 1.00s.
        _raw = str(args.get("limit") or "").strip()
        limit = None if _raw.lower() == "all" else max(1, int(_raw or 500))
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        try:
            sql = ("SELECT machine_uid, ts, kind, lab_id, test_name, value, "
                   f"detail FROM lem_machine_log {clause} ORDER BY ts DESC")
            if limit is not None:
                sql += " LIMIT ?"
            res = gateway.read_sql(
                sql, params + ([limit] if limit is not None else []))
            # `labcore_rows`, not `res.get("error")` (2026-08-25). The verdict
            # was still hand-rolled here, in the file that imports the shared
            # rule and uses it three lines further down — so a refusal carrying
            # no "error" key read as a successful read of zero rows: /api/logs
            # answered 200 with no events and no banner, and /api/logs.csv
            # served a header row with nothing under it.
            #
            # missing_ok: `lem_machine_log` is declared centrally at boot, so a
            # read that gets there first is honestly looking at nothing. That
            # is the ONE error a read may call empty; a busy queue is not.
            return labcore_rows(res)
        except LabCoreError:
            # Reported, not swallowed: an unreadable log served as an empty one is
            # a confident wrong answer about a lab that has plenty of history.
            if failed is not None:
                failed["at"] = True
            return []
        except Exception:                       # a client that raises outright
            if failed is not None:
                failed["at"] = True
            return []

    def _log_entries(args, failed=None, unnamed=None, searched=None) -> list:
        # TWO FLAGS, NOT ONE (2026-08-25). `_titles()` reaches LabCore when the
        # snapshot has not built, and it raises rather than shrugging — but a
        # missing NAME and a missing EVENT are different facts and folding them
        # into one flag made the CSV export refuse a whole download because the
        # machine column could not be labelled.
        #
        # /api/export/qc.csv, reading the same log table, already serves the
        # record with a blank name column and says so in its comment: "names
        # decorate; the QC rows are the record". Two rules for one question in
        # one file is how this branch's bugs started.
        #
        # `failed` still means "the list may be incomplete" — that is what the
        # JSON banner and the CSV refusal are both for. `unnamed` means "the
        # rows are all here, their labels are not".
        titles, ok = _titles_soft()
        if not ok:
            if unnamed is not None:
                unnamed["at"] = True
            elif failed is not None:
                failed["at"] = True
        needle = (args.get("q") or "").strip().lower()

        # A SEARCH SEARCHES THE LOG, not the page of it that was fetched.
        #
        # This used to ask LabCore for the newest `limit` rows and then grep
        # THOSE in Python, so every match older than the fetched page did not
        # exist as far as the page was concerned. Measured on the live lab:
        # "Flash" returned 2 events out of a 214,714-row log holding thousands
        # of flash rows — and the flash instruments looked absent, because
        # their rows are not in the newest 500 of a lab where one instrument
        # writes constantly.
        #
        # With a term, the whole record is searched in the local mirror, with
        # the machine/kind/date filters IN the query so narrowing by instrument
        # narrows the search. Without one this is a paged listing and stays
        # exactly as it was: the whole log is not an answer to "show me the
        # log". A cold mirror falls through to the old path rather than to an
        # empty page — the mirror is a cache, never the record.
        mirror = app.config.get("LOG_MIRROR")
        searched_all = False
        rows = _log_rows(args, failed=failed)

        if needle and mirror is not None and mirror.state()["rows"]:
            # BOTH SOURCES, because each is missing something the other has.
            #
            # The mirror holds the whole record but is refreshed every five
            # minutes, so it can be behind by a few rows — and a search for a
            # sample run two minutes ago must still find it. The LabCore page
            # is current to the second but is only the newest `limit` rows,
            # which is the entire bug being fixed here. Merging costs the same
            # read the page was already doing.
            raw = str(args.get("limit") or "").strip()
            lim = None if raw.lower() == "all" else max(1, int(raw or 500))
            deep = mirror.query(
                term=needle,
                machine_uid=(args.get("machine") or "").strip(),
                kind=(args.get("kind") or "").strip(),
                since=(args.get("since") or "").strip(),
                until=(args.get("until") or "").strip(),
                limit=lim if lim is not None else 100000)
            # The live page still has to be filtered — it was fetched without
            # the term. Dedupe on what identifies a row to a reader; `rowid` is
            # not in the LabCore page's columns.
            def _key(r):
                return (str(r.get("machine_uid") or ""), str(r.get("ts") or ""),
                        str(r.get("kind") or ""), str(r.get("lab_id") or ""),
                        str(r.get("test_name") or ""), str(r.get("value") or ""))
            seen = {_key(r) for r in deep}
            fresh = [r for r in rows
                     if needle in " ".join(
                         str(r.get(k) or "") for k in
                         ("lab_id", "test_name", "value", "kind", "detail")
                     ).lower() and _key(r) not in seen]
            rows = sorted(deep + fresh,
                          key=lambda r: str(r.get("ts") or ""), reverse=True)
            if lim is not None:
                rows = rows[:lim]
            searched_all = True

        out = []
        for row in rows:
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
                # The same value, in the word the Logs page prints. `action`
                # stays exactly as it was stored — anything filtering on it
                # must keep matching rows written before the rename.
                "action_label": display_action(
                    detail.get("action") or row.get("test_name") or ""),
                # The same blob, as a sentence. `detail` itself still rides
                # along untouched — a client filtering on a key must keep
                # working — but nothing has to render JSON to a person.
                "detail_text": describe_detail(
                    detail.get("action") or row.get("test_name") or "", detail),
                "by": str(detail.get("by") or ""),
            }
            if needle:
                # Search the detail blob too — an override's comment and a
                # config change's specifics only live in there.
                hay = " ".join([entry["machine_title"], entry["lab_id"],
                                entry["test_name"], entry["value"],
                                entry["kind"], raw_detail]).lower()
                # The mirror already applied the term in SQL; re-applying it
                # here would drop rows that matched on a column this haystack
                # does not carry.
                if not searched_all and needle not in hay:
                    continue
            out.append(entry)
        if searched is not None:
            searched["all_time"] = searched_all
        return out

    @app.route("/api/logs")
    def api_logs():
        # An unreadable log must not be served as an empty one. The queue bursts,
        # reads time out behind it, and "no events" is a confident wrong answer
        # about a lab that has plenty.
        failed = {"at": False}
        searched = {"all_time": False}
        entries = _log_entries(request.args, failed=failed, searched=searched)

        # "The read failed" and "this lab has no log yet" are two facts, and
        # this used to answer `[]` to both — judged with `res.get("error")`,
        # the hand-rolled test the rest of this branch removes, so a refusal
        # carrying no "error" key read as an empty vocabulary. The drop-down
        # then vanished exactly when the log is busiest, with nothing saying
        # why.
        kinds_failed = {"at": False}

        def kinds_now():
            # A DISTINCT over the whole machine log, to fill a dropdown of about
            # six fixed words. On the live table that is the same shape of query
            # that once took eight seconds — and it was running per request.
            res = gateway.read_sql(
                "SELECT DISTINCT kind FROM lem_machine_log ORDER BY kind")
            try:
                found = labcore_rows(res)
            except LabCoreError:
                # DELIBERATE DEGRADATION. This fills a filter DROP-DOWN, and the
                # very same request already reports the read failure through
                # `failed["at"]` — raising here would replace an honest partial
                # answer with a 503 the page cannot render. But the fallback is
                # the vocabulary this app WRITES, not a blank: every filter the
                # page can offer still works, and `kinds_known: false` tells it
                # the list was not read.
                kinds_failed["at"] = True
                return list(LOG_KINDS)
            return [str(r.get("kind")) for r in found if r.get("kind")]

        kinds = _page("logkinds", kinds_now)
        if kinds_failed["at"] or not kinds:
            # A GUESS IS NEVER KEPT, and neither is a blank. `_page` caches
            # whatever `produce()` returns, and the only thing that drops this
            # key is a config change (`_audit`) — so one timed-out DISTINCT
            # would leave the fallback list in place for days, reported as if
            # it had been read.
            _page_drop("logkinds")
        out = {"events": entries, "kinds": kinds,
               "kinds_known": not kinds_failed["at"],
               # Whether this was a search of the WHOLE record or a page
               # listing. "12 matches" over the newest 500 rows and over
               # 214,000 are different sentences, and the page says which.
               "searched_all_time": searched["all_time"]}
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
        unnamed = {"at": False}
        entries = _log_entries(request.args, failed=failed, unnamed=unnamed)
        if failed["at"]:
            # The EVENTS could not be read. A file cannot carry a warning, and
            # a download with a header row and nothing under it is the one
            # version of this bug that leaves the building and gets filed as
            # the lab's history.
            return jsonify({
                "error": "LabCore did not answer in time, so this export would "
                         "be incomplete and nothing has been downloaded. Try "
                         "again in a moment.",
                "retry": True, "labcore": "unavailable"}), 503
        # `unnamed` deliberately does NOT refuse. Every event is present and
        # correct; only the `machine` column falls back to the uid, exactly as
        # it already does for a retired machine, and exactly as
        # /api/export/qc.csv does. Withholding the record over its decoration
        # would be the larger failure.
        rows = [[e["ts"], e["machine_title"], e["kind"], e["lab_id"],
                 e["test_name"], e["value"], e["by"],
                 json.dumps(e["detail"], separators=(",", ":"))]
                for e in entries]
        return _csv_response(
            rows, ["timestamp", "machine", "kind", "lab_id", "test", "value",
                   "by", "detail"], "lem_log.csv",
            note=NAMES_UNREAD if unnamed["at"] else "")

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
            return _labcore_unreadable(exc, "this equipment's PM and "
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
        # 2000 was a CEILING: a lab with more than that in its log could not
        # see the rest from this page at all. It is a default now, and `all`
        # serves the whole table — 41,903 rows read in 1.00s when measured, so
        # the cap was never protecting the database.
        _raw = (request.args.get("limit") or "").strip()
        if _raw.lower() == "all":
            limit = None
        else:
            try:
                limit = max(1, int(_raw or 300))
            except ValueError:
                return jsonify({"error": "limit must be a number, or 'all'."}), 400
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

        # BOTH ways a write can fail are the same fact here — the row is not in
        # LabCore — so a client that RAISES is folded into the same `stopped`
        # answer rather than escaping as "Internal Server Error" over a
        # half-finished import. That is the whole point of the counts below:
        # they are a promise about what is in LabCore, and a 500 makes that
        # promise unreadable.
        def _write_row(sql, args=None):
            try:
                return gateway.sql(sql, args or [])
            except Exception as exc:                # transport, not logic
                return {"error": "LabCore could not be written to ({0}: "
                                 "{1})".format(type(exc).__name__, exc)}

        _write_row(
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
            result = _write_row(
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
            except LabCoreError as exc:
                # Caught rather than allowed to reach the error handler: the
                # payload below carries how many rows DID land, and losing that
                # to a bare 503 would leave the operator with no idea whether to
                # re-run the file.
                #
                # `LabCoreError`, not just `LabCoreRefused`: a store whose write
                # RAISED is a reschedule that equally did not happen, and it
                # carries no `result` — so the sentence stands in for the answer
                # LabCore never gave.
                stopped = getattr(exc, "result", None) or {"error": str(exc)}
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
            # `refusal_reason(stopped)` FIRST, `stopped` SECOND. This
            # constructor takes the sentence and then the answer; handing it
            # the answer alone made the reason a printed dict and left `busy`
            # False, so a deep queue was reported as permanent with no
            # Retry-After.
            return refusal_response(LabCoreRefused(
                refusal_reason(stopped),
                stopped,
                what=f"{made} of {len(plan['create'])} completion(s) were "
                     f"imported before LabCore stopped accepting writes — "
                     f"re-run the same file to bring the rest in",
                partial=made > 0, incomplete=True, **payload))
        return jsonify(payload)

    @app.route("/api/maintenance")
    def api_all_maintenance():
        """Every machine's PM/CAL in one list — "what is overdue anywhere",
        which no per-machine dialog can answer."""
        from snapshot_service import maintenance_from_tables, titles_from_tables
        tables = _snapshot_tables()
        named = True
        if tables is None:
            # First request, or LabCore down at boot. This used to substitute
            # `tables = {}` and serve the empty task list that falls out of it —
            # a 200 saying "nothing scheduled anywhere" built from a snapshot
            # that had never been built. Ask LabCore instead, and if it cannot
            # answer, say so: `for_machine` on the sibling route already does.
            try:
                grouped = maint_store.all()
            except LabCoreError as exc:
                return _labcore_unreadable(
                    exc, "the lab's PM and calibration schedule")
            titles, named = _titles_soft()
        else:
            # The snapshot tolerates one failed arm — right for the floor, where
            # a missing maintenance row costs one pill. Not right here, where the
            # arm IS the answer: "nothing is overdue anywhere" invented out of a
            # read that timed out is how a calibration gets missed, and it is the
            # same lie as a write reported as saved.
            unread = snapshots.table_error("maint")
            if unread:
                return _labcore_unreadable(
                    LabCoreUnavailable(unread),
                    "the lab's PM and calibration schedule")
            grouped = maintenance_from_tables(tables)
            titles = titles_from_tables(tables)
        today = _now().date()
        tasks = []
        for uid, records in grouped.items():
            for task in records:
                # today, not date.today(): the interval status is judged per
                # request, so a task falling due overnight is red in the morning.
                row = task.to_dict(today)
                row["machine_title"] = titles.get(uid, uid)
                tasks.append(row)
        tasks.sort(key=lambda t: (t["status"] != "RED", t["status"] != "YELLOW",
                                 t["machine_title"], t["name"]))
        due = sum(1 for t in tasks if t["status"] in ("RED", "YELLOW"))
        # A NAME decorates; the task is the record. Losing the titles leaves a
        # row labelled `m1` rather than "Multitek NS", which is ugly and
        # complete — withholding the schedule over its labels would be the
        # larger failure, and it is the rule /api/export/qc.csv already follows.
        # But an unlabelled row must not read as a machine that is genuinely
        # called m1, so the page is told.
        return jsonify({"tasks": tasks, "due_count": due,
                        "machines_named": named})

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

    def _opt_float(raw):
        """A number LabCore stored, or None. NEVER 0.0 for a missing one.

        `std_dev` and `k` come back as TEXT through the queue, and a zero sigma
        collapses every control zone onto the centre line — so "the column was
        NULL" has to stay distinguishable from "the certificate says zero",
        which `certificate_limits` then refuses on its own.
        """
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    # How many results a control chart shows. The analysis runs on exactly
    # these points, never on the whole history — see `_chart_series`.
    CHART_POINTS = 60

    def _zone(limits, k: float):
        pair = limits.zone(k) if limits is not None else None
        return None if pair is None else {"low": pair[0], "high": pair[1]}

    def _chart_series(analysis, points) -> dict:
        """One `SeriesAnalysis` as the floor's chart payload.

        TWO KINDS OF LIMIT LIVE ON ONE CHART AND THEY ARE NOT THE SAME THING,
        so they are two named blocks here and never one "limits" field:

          `pass_band` — the SPECIFICATION. `expected +/- k*std_dev` off the
            standard's certificate, read straight out of the detail the bench
            wrote. It says nothing about this instrument; the same band judges
            every bench running that standard.
          `observed`  — the OBSERVATION. `mean +/- k*s` from THESE results, the
            n-1 divisor, and the zones a Shewhart chart is drawn with. It moves
            as the instrument moves.

        A wide certificate over a drifting instrument gives narrow zones inside
        a wide band — in control of nothing, passing everything — and a tight
        certificate over a stable one gives the reverse. A chart that draws one
        and labels it the other says the opposite of what the process is doing.
        `zones_within_band` is that comparison already made.

        `failures` (results the bench judged outside the certificate) and
        `violations` (ways the process is out of control) are likewise two
        counts and neither substitutes for the other.

        `low`/`high`/`expected` stay at the top level because the floor already
        reads them there and they ARE the pass band. They are the same three
        numbers as `pass_band`, not a fourth quantity.
        """
        band, limits, cov = analysis.pass_band, analysis.limits, analysis.coverage
        return {
            "test_name": analysis.test_name,
            "sample_id": analysis.sample_id,
            "points": [{"ts": p.ts, "value": p.value, "in_spec": p.in_spec}
                       for p in points],
            "runs": analysis.n,
            "failures": analysis.failures,
            # A verdict the log does not carry is UNJUDGED. Counting it as a
            # failure invents an excursion that never happened; counting it as a
            # pass hides one that did.
            "unjudged": analysis.unjudged,

            # ── the specification ──
            "low": band.low if band else None,
            "high": band.high if band else None,
            "expected": band.expected if band else None,
            "pass_band": None if band is None else {
                "low": band.low, "high": band.high, "expected": band.expected},

            # ── the observation ──
            "observed": {
                "mean": analysis.mean,
                "s": analysis.s,
                # `n` here is what `s` was computed FROM and `df` its degrees of
                # freedom — the pair a later uncertainty module reads beside
                # `s`. Equal to `runs` only because nothing supplies
                # qualification limits yet, which is exactly what
                # `self_fitted` is saying.
                "n": analysis.s_n,
                "df": analysis.s_df,
                "self_fitted": analysis.self_fitted,
                "zones": {"1s": _zone(limits, 1), "2s": _zone(limits, 2),
                          "3s": _zone(limits, 3)},
            },
            "zones_within_band": analysis.zones_within_band,

            # ── the chart is grading itself, and has to say so ──
            # With no qualification period the limits are fitted to the very
            # points they judge. The UI must be able to print that rather than
            # present a self-fitted alarm as fact.
            "self_fitted": analysis.self_fitted,
            "in_control": analysis.in_control,
            "violations": [{"rule": v.rule, "indices": list(v.indices),
                            "side": v.side, "message": v.message,
                            "provisional": v.provisional}
                           for v in analysis.violations],
            "firm_violations": len(analysis.firm_violations),

            # ── what the spread may be CALLED ──
            # A spread that does not span analysts, calendar days AND
            # calibrations is not within-laboratory reproducibility, and the
            # caveat is the sentence that goes beside the chart.
            "spread_basis": analysis.spread_basis,
            "coverage": {
                "basis": cov.basis,
                "caveat": cov.caveat(),
                "n": cov.n,
                "operators": list(cov.operators),
                "n_operators": cov.n_operators,
                "n_unknown_operator": cov.n_unknown_operator,
                "n_days": cov.n_days,
                "n_undated": cov.n_undated,
                "calibrations": list(cov.calibrations),
                "n_calibrations": cov.n_calibrations,
                "n_unknown_calibration": cov.n_unknown_calibration,
                "supports_repeatability": cov.supports_repeatability(),
                "supports_reproducibility": cov.supports_reproducibility(),
            },
        }

    @app.route("/api/machines/<machine_uid>/qc-trend")
    def api_qc_trend(machine_uid):
        """The control chart: is this instrument IN CONTROL, and what does its
        spread mean?

        The arithmetic is `qc_series`'s, not this route's. What used to be here
        parsed `detail` inline and answered "how many results fell outside the
        band", which is a pass rate — and a run of results every one of them
        inside the band and every one above the mean is an instrument that has
        moved, reported as perfect.
        """
        import qc_series
        try:
            events = _qc_events(machine_uid)
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "this equipment's QC history")

        # THE CERTIFICATE'S SIGMA, which the log does not carry. `qc_log_detail`
        # on the bench records expected/low/high and not std_dev or k, so the
        # centre and spread of the control limits have to come from the spec
        # the module publishes. One small indexed read per panel open — this is
        # an operator opening an instrument, not a wall on a timer.
        #
        # A FAILED READ IS NOT AN ABSENT CERTIFICATE. "No certificate on file"
        # and "could not read the certificate" draw the same empty chart and
        # mean opposite things, so the answer says which, the same way the CSV
        # export says when it could not resolve names.
        certs, cert_read = {}, "ok"
        try:
            res = gateway.read_sql(
                "SELECT test_name, sample_id, std_dev, k FROM lem_machine_specs "
                "WHERE machine_uid = ?", [machine_uid])
            for r in labcore_rows(res, missing_ok=True):
                certs[(str(r.get("test_name") or ""),
                       str(r.get("sample_id") or ""))] = (r.get("std_dev"),
                                                          r.get("k"))
        except LabCoreError:
            cert_read = "unreadable"

        by_key = qc_series.series_from_rows(events)
        # Which standard each test is on NOW, so a retired lot's chart is
        # served as history rather than as this instrument's current control.
        current = {}
        for (uid, name, _sid) in by_key:
            if uid != machine_uid or name in current:
                continue
            found = qc_series.current_series(by_key, uid, name)
            if found is not None:
                current[name] = found.sample_id

        out = []
        for (uid, name, sample_id), series in by_key.items():
            if uid != machine_uid:
                continue                       # already filtered in SQL; cheap
            # TRUNCATE, THEN ANALYSE. A violation's `indices` are positions in
            # the series it was found in, so analysing the whole history and
            # then trimming the points would leave every index off by the
            # number dropped and the UI circling the wrong readings.
            shown = qc_series.QcSeries(
                machine_uid=series.machine_uid, test_name=series.test_name,
                points=series.points[-CHART_POINTS:],
                pass_band=series.pass_band, sample_id=series.sample_id)
            std_dev, k = certs.get((name, sample_id), (None, None))
            limits = qc_series.certificate_limits(
                shown.pass_band, std_dev=_opt_float(std_dev),
                k=_opt_float(k))
            row = _chart_series(qc_series.analyse(shown, limits), shown.points)
            row["superseded"] = current.get(name, sample_id) != sample_id
            row["limits_source"] = ("certificate" if limits is not None
                                    else "unreadable" if cert_read != "ok"
                                    else "none")
            out.append(row)
        return jsonify({"series": sorted(
            out, key=lambda s: (s["superseded"], s["test_name"],
                                s["sample_id"]))})

    # ── the QC wall ───────────────────────────────────────────────────
    #
    # Ryan: "another tab beneath logs called QC, where it just shows the QC
    # history graph but for all the machines, design it to run like a literal
    # monitor, for viewing far away, and just being locked on that screen."
    #
    # Two things about a monitor decide this route.
    #
    # IT POLLS FOREVER, so it may not cost LabCore anything. A page left open
    # on a wall, refreshing, is precisely the load pattern the snapshot design
    # exists to prevent — and unlike the floor, this one needs QC HISTORY,
    # which is deep. It reads the local log mirror, which already holds every
    # `lem_machine_log` row and refreshes every five minutes. Five minutes is
    # invisible on a chart whose points are hours apart.
    #
    # NOBODY IS STANDING THERE TO INTERPRET IT. So it may never show a state it
    # cannot justify: a failed read must not look like a calm lab, and a series
    # too short to judge says TOO FEW rather than drawing a confident line
    # through three dots.

    #: Below this, a control chart is a picture rather than evidence. Three is
    #: deliberately low — it is the point at which a TREND is visible at all,
    #: not the point at which the statistics mean anything, which is what the
    #: PROVISIONAL warning on the equipment panel is for.
    WALL_MIN_POINTS = 3

    #: How long a passing QC result keeps this chart current. Same default the
    #: rest of LEM falls through to; a wall showing yesterday's green is worse
    #: than a wall showing nothing, because it is confidently wrong.
    WALL_STALE_HOURS = 24.0

    @app.route("/api/qc-wall")
    def api_qc_wall():
        import qc_series
        mirror = app.config.get("LOG_MIRROR")
        rows, source, as_of = None, "labcore", None
        if mirror is not None and mirror.state()["rows"]:
            rows = [r for r in mirror.events() if r.get("kind") == "qc"]
            source = "mirror"
            as_of = mirror.state()["filled_at"]
        if rows is None:
            # Cold mirror. The wall is not allowed to be empty because a cache
            # has not filled yet — LabCore is still the record.
            try:
                rows = _qc_events()
            except LabCoreError as exc:
                return _labcore_unreadable(exc, "the lab's QC history")
            as_of = _now().isoformat(timespec="seconds")

        # NAMES, FROM THE SNAPSHOT — the wall's no-LabCore-ops rule applies to
        # this read too. `lem_machine_status` carries the title and the floor's
        # 12-second snapshot already holds it, so the wall costs nothing to
        # refresh however long it hangs there. A uid is a poor thing to read
        # from four metres, but failing to resolve one is never a reason to
        # blank the wall: it falls back to the uid.
        # `build_if_missing=False`. A wall display must never be the request
        # that pays to build the first snapshot: it is a screen nobody is
        # standing at, refreshing on a timer, and the whole rule here is that
        # it costs LabCore nothing. If the snapshot has not built yet the wall
        # simply shows uids until it has.
        titles = {}
        tables = (snapshots.tables()
                  if snapshots.get(build_if_missing=False).get("ready")
                  else None)
        if tables:
            for r in tables.get("status", []) or []:
                uid, title = r.get("c1"), r.get("c2")
                if uid and title:
                    titles[uid] = title
        if not titles:
            # One small read, and only when the snapshot cannot answer — a
            # name table, never the log. It is bounded by the instrument count
            # and is what stops a cold wall being a wall of hex.
            try:
                res = gateway.read_sql(
                    "SELECT machine_uid, title FROM lem_machine_status",
                    timeout=30)
                titles = {r["machine_uid"]: r["title"]
                          for r in labcore_rows(res, missing_ok=True)
                          if r.get("title")}
            except LabCoreError:
                titles = {}

        now = _now()
        out = []
        by_key = qc_series.series_from_rows(rows)
        # ONE CARD PER INSTRUMENT AND METHOD, showing the standard it is on
        # NOW. A changeover starts a new chart (see `series_from_rows`), so a
        # retired lot's series is still in here — but a wall is read from four
        # metres by somebody walking past, and a card for a material the lab
        # stopped using is not a thing that room can act on. The history stays
        # on the equipment panel, which is where somebody is actually looking
        # into it.
        wanted = set()
        for (uid, name, _sid) in by_key:
            found = qc_series.current_series(by_key, uid, name)
            if found is not None:
                wanted.add((uid, name, found.sample_id))

        for (uid, _name, _sid), series in by_key.items():
            if (uid, _name, _sid) not in wanted:
                continue
            pts = series.points[-CHART_POINTS:]
            if not pts:
                continue
            band = series.pass_band
            last = pts[-1]
            age_h = None
            if last.at is not None:
                age_h = (now - last.at).total_seconds() / 3600.0

            # THE STATE, in one word, in the order that decides what the room
            # is told first. Out of spec outranks everything: an instrument
            # whose last control result failed is the sentence on this wall.
            if last.in_spec is False:
                state = "OUT OF SPEC"
            elif len(pts) < WALL_MIN_POINTS:
                state = "TOO FEW"
            elif age_h is not None and age_h > WALL_STALE_HOURS:
                state = "STALE"
            else:
                state = "IN CONTROL"

            # NORMALISED TO ITS OWN BAND, which is what lets one horizontal
            # rule run across the whole wall: -1 is the lower limit, +1 the
            # upper, 0 the target. Sixteen charts in °C, %m/m, cSt and kPa are
            # not otherwise comparable at a glance, and at four metres nobody
            # is reading axis labels.
            half = None
            if band is not None:
                half = (float(band.high) - float(band.low)) / 2.0
            def z(v):
                if half in (None, 0) or band is None or v is None:
                    return None
                return (float(v) - float(band.expected)) / half

            out.append({
                "machine_uid": uid,
                "title": titles.get(uid) or uid,
                "test_name": series.test_name,
                "state": state,
                "n": len(pts),
                "last_at": last.at.isoformat(timespec="seconds") if last.at else None,
                "age_hours": None if age_h is None else round(age_h, 1),
                "last_value": last.value,
                "pass_band": None if band is None else {
                    "low": band.low, "high": band.high, "expected": band.expected},
                "points": [{"ts": p.at.isoformat(timespec="seconds") if p.at else None,
                            "value": p.value, "z": z(p.value),
                            "in_spec": p.in_spec} for p in pts],
            })

        # WORST FIRST, because nobody scrolls a wall. If it does not fit, what
        # fits has to be what matters.
        rank = {"OUT OF SPEC": 0, "STALE": 1, "TOO FEW": 2, "IN CONTROL": 3}
        out.sort(key=lambda s: (rank.get(s["state"], 9),
                                -(s["age_hours"] or 0), s["title"]))
        methods = {s["test_name"] for s in out}
        controlled = {s["test_name"] for s in out if s["state"] == "IN CONTROL"}
        return jsonify({
            "series": out,
            "as_of": as_of,
            "source": source,
            "methods_total": len(methods),
            "methods_in_control": len(controlled),
            # An empty grid reads as "everything is fine". That is not the same
            # sentence as "nothing is being checked", and on this screen the
            # second one is the finding.
            "nothing_checked": ("No QC has been recorded on any instrument. "
                                "This wall is empty because nothing is being "
                                "checked, not because everything passed."
                                if not out else ""),
        })

    @app.route("/qc")
    def page_qc_wall():
        return render_template("qc.html")

    # ── the status gutter ─────────────────────────────────────────────
    # The events list with a colour band down its left: for each event, what
    # state the instrument was in while it happened. The derivation is
    # `gutter_events` — pure, and documented at its definition.

    @app.route("/api/machines/<machine_uid>/status-timeline")
    def api_status_timeline(machine_uid):
        """This instrument's recent events, each with the status in force then.

        SERVED FROM THE SNAPSHOT, AT ZERO LabCore OPS. This is a panel the floor
        opens beside a chart that already polls every two seconds, and the rule
        here is that a request never talks to LabCore and LabCore load does not
        depend on how many people are looking. The snapshot reads
        `lem_machine_log` every cycle anyway.

        What that costs, stated in the payload rather than hidden: the snapshot
        holds the newest `EVENT_LIMIT` rows for the WHOLE lab, so a quiet
        instrument's gutter can be clipped by a busy neighbour. `complete` and
        `covers_from` say whether it was, because "nothing else happened" and
        "nothing else is in this answer" are different sentences and only one of
        them is a statement about the record.

        The live read is the cold path only — the snapshot has never built.
        """
        from snapshot_service import EVENT_LIMIT, events_from_tables

        from qc_samples import resolve_qc_window, window_from_standards

        tables = _snapshot_tables()

        # The QC window, and which level supplied it.
        #
        # This used to be the 24h default, always, reported as `"default"`
        # because the server genuinely held no per-machine window: that is the
        # module's `Machine.qc_expire_hours` and it is in no snapshot arm. It
        # still is not — but a STANDARD's own window now is, in the `qcsample`
        # arm the benches already read, so the guess can stop.
        #
        # The chain here is shorter than the bench's on purpose. A mapping
        # override lives on the instrument's parser and never reaches this
        # server, and the machine default is not in the snapshot either; adding
        # a read for them would cost one LabCore op on a panel the floor opens
        # beside a chart that polls every two seconds. Both missing levels are
        # LESS specific than the standard and more specific than 24, so the
        # answer is the right one wherever they are silent — and `qc_expire_from`
        # names the standard, so a person can see what it was resolved from
        # instead of taking the number on trust.
        standard_hours, standard_from = 0.0, ""
        # CAN THE SNAPSHOT ACTUALLY ANSWER FOR THIS MACHINE?
        #
        # It holds the newest EVENT_LIMIT rows for the WHOLE lab, so "the
        # snapshot exists" and "the snapshot has anything to say about this
        # instrument" are different questions, and this route only ever asked
        # the first. Measured on the live lab: sixty rows reach back about four
        # hours, five of sixteen instruments own all of them, and Agilent GC 1
        # — 26,106 log rows, more than anything else here — owned none and
        # rendered an empty panel.
        #
        # So a clipped-out instrument falls through to the per-machine read
        # below, which is this route's cold path and already written. It costs
        # ONE LabCore op, only on opening the record of an instrument that has
        # been quiet, on a click that already reads that equipment's history.
        # An instrument inside the window still pays nothing.
        covered = False
        if tables is not None and not snapshots.table_error("event"):
            from snapshot_service import events_from_tables as _efc
            _all = _efc(tables, EVENT_LIMIT)
            covered = (any(e["machine_uid"] == machine_uid for e in _all)
                       or len(_all) < EVENT_LIMIT)

        if tables is not None:
            from snapshot_service import bench_config_from_tables
            config = bench_config_from_tables(tables, machine_uid)
            standard_hours, standard_from = window_from_standards(
                config["qc_samples"], config["qc_targets"])

        asked = _finite(normalise_number_text(
            request.args.get("qc_expire_hours") or ""))
        hours, hours_source = resolve_qc_window(
            (("request", asked), ("standard", standard_hours)),
            default_hours=GUTTER_DEFAULT_QC_HOURS)
        if hours_source != "standard":
            standard_from = ""

        if tables is not None and covered:
            # The snapshot tolerates one failed arm — right for the floor, where
            # a missing row costs one pill. Not right here, where the arm IS the
            # answer: an empty gutter says this instrument has done nothing,
            # which on a 17025 panel is a statement about the record.
            unread = snapshots.table_error("event")
            if unread:
                return _labcore_unreadable(
                    LabCoreUnavailable(unread),
                    "this equipment's recent activity")
            everything = events_from_tables(tables, EVENT_LIMIT)
            rows = [e for e in everything
                    if e["machine_uid"] == machine_uid]
            complete = len(everything) < EVENT_LIMIT
            # How far back THE WINDOW reaches, which is not how far back this
            # instrument's slice of it reaches. See `horizon` below.
            window_from = everything[-1]["ts"] if everything else None
            source = "snapshot"
            age = snapshots.get().get("age_seconds")
        else:
            try:
                res = gateway.read_sql(
                    "SELECT machine_uid, ts, kind, lab_id, test_name, value, "
                    "detail FROM lem_machine_log WHERE machine_uid = ? "
                    "ORDER BY ts DESC LIMIT ?", [machine_uid, EVENT_LIMIT])
                rows = [dict(r) for r in labcore_rows(res)]
            except LabCoreError as exc:
                return _labcore_unreadable(
                    exc, "this equipment's recent activity")
            complete = len(rows) < EVENT_LIMIT
            # The cold path asks for THIS instrument only, so the window and
            # the slice are the same list and its oldest row is the horizon.
            window_from = rows[-1]["ts"] if rows else None
            source, age = "labcore", None

        events = gutter_events(rows, hours)
        return jsonify({
            "machine_uid": machine_uid,
            "events": events,
            "qc_expire_hours": hours,
            "qc_expire_source": hours_source,
            # Which standard said so, when one did. Empty for every other
            # source — naming a standard the number did NOT come from is worse
            # than naming none.
            "qc_expire_from": standard_from,
            "source": source,
            "snapshot_age_seconds": age,
            "complete": complete,
            # HOW FAR BACK THIS ANSWER REACHES — a property of the WINDOW, not
            # of this instrument's slice of it.
            #
            # It used to be `events[-1]["ts"] if events else None`, and that
            # broke the panel in the one case the field exists for. The
            # snapshot holds the newest EVENT_LIMIT rows for the WHOLE lab, so
            # an instrument that has been silent while a busy neighbour filled
            # the window owns NONE of it — and the horizon then collapsed to
            # None, leaving the panel nothing to say but "nothing is recorded
            # against this equipment", which is a sentence about the record.
            #
            # Measured on the live lab, 27 Aug: Agilent GC 1 holds 26,106 log
            # rows, more than any other instrument here, and showed an empty
            # gutter because its newest event was fourteen hours old and sixty
            # rows of the lab reach back about four.
            #
            # It was also wrong when the instrument DID own rows: two events an
            # hour apart inside a four-hour window reported a one-hour horizon,
            # and a reader takes that as the limit of the record.
            "covers_from": window_from,
        })

    NAMES_UNREAD = ("# NOTE: the equipment names could not be read from LabCore "
                    "when this file was made, so the machine column shows "
                    "internal ids. The rows themselves are complete.")

    def _csv_response(rows, header, filename, note=""):
        """The file, plus a line saying what is wrong with it if anything is.

        A CSV cannot carry a banner, and this branch's only other reporting
        channel is `logger.warning` — which is a file on the server that
        whoever opens the download will never see. So the caveat travels IN the
        download.

        AT THE END, not the top: a comment line above the header breaks every
        parser that reads the file by column name, and being readable is the
        whole reason the file is served rather than withheld.
        """
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(header)
        writer.writerows(rows)
        if note:
            writer.writerow([note] + [""] * (len(header) - 1))
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
        """Declare the table once, and only once it is ACKNOWLEDGED. WRITES ONLY.

        The flag used to not exist at all — the DDL ran on every read of every
        correction dialog, one more write into a queue that serialises at ~1.5
        ops/sec. Setting the flag on an unconfirmed answer would be the other
        half of the bug: a boot while the queue was full would leave this
        believing the table exists for the life of the process, and every
        correction written afterwards would go nowhere.

        And `_corrections()` no longer calls it (2026-08-25). Declaring from
        the read meant a full WRITE queue closed the corrections dialog — the
        one dialog whose whole job is to show an offset that IS in force —
        because a `CREATE TABLE IF NOT EXISTS` for a table that already existed
        came back refused. The declaration stays where a row is about to be
        INSERTed, which is the only place it buys anything.
        """
        if _corrections_ready["at"]:
            return
        _confirmed_write(CORRECTIONS_DDL)
        _corrections_ready["at"] = True

    def _corrections(machine_uid: str) -> dict:
        """This bench's correction factors. Raises rather than answering `{}`.

        `{}` means "no correction is in force", and under ISO/IEC 17025 §7.8.2
        that is a claim about every result this instrument reports. It also
        feeds the audit line on a save — a degraded read would record
        `previous: 0.0` about a bench that has been running at -3.0.

        Declares nothing; `labcore_rows` still swallows exactly one error, "no
        such table", which on a LabCore where no correction has ever been saved
        is the honest `{}` rather than an invented one.
        """
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
            return _labcore_unreadable(exc, "this equipment's correction "
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
            return _labcore_unreadable(exc, "the equipment list")
        if machine_uid not in known:
            return jsonify({"error": "No such equipment."}), 404
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
        #
        # BOTH ways it can fail are named. A REFUSAL travels to the error
        # handler carrying `what`, so the operator reads LabCore's own reason
        # and a Retry-After. A RAISED transport error never produced an answer
        # at all, so nothing is known about the write — `_labcore_failed` says
        # exactly that rather than "LabCore said no", and it is caught here
        # rather than left to escape as "Internal Server Error".
        try:
            # HERE, not in `_corrections()`: this is the path that INSERTs, so
            # this is the path that needs the table to exist.
            _corrections_schema()
            _confirmed_write(
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
                 session.get("user", "")],
                what=f"the correction for “{test_name}” was NOT saved and this "
                     f"instrument is still applying the previous one")
        except LabCoreUnavailable as exc:
            return _labcore_failed(
                exc, "the correction factor for “{0}”".format(test_name))
        # Everything below is reached only by a write that landed, which is what
        # makes all four of these honest:
        #
        # The NOTE, because the bench re-reads `lem_correction_factors` when it
        # sees one — a note for a write that failed buys a LabCore read that
        # finds the OLD value, on the very queue that has just said it is too
        # deep. This machine and no other: a correction is per machine per test,
        # and a broad mark would put the whole lab through a read for one bench.
        #
        # The §7.8.2 RECEIPT, because the UPSERT above has just DESTROYED
        # `previous` and `lem_correction_audit` is the only place left that
        # says what it was, when, and who changed it — while that number is
        # added to every result this bench reports. It never raises: the
        # change has already landed, so a refused audit row is spooled and
        # retried, and `_report_unrecorded_audit` tells the operator it is
        # outstanding rather than letting it disappear.
        #
        # The AUDIT, because it records who changed the factor from what to
        # what, and a change that did not happen written into the one log an
        # assessor reads is worse than no log at all.
        #
        # The SNAPSHOT refresh, because there is nothing new to pick up.
        app.config["LIVE"].mark_stale(machine_uid, STALE_CORRECTIONS)
        _record_correction_change(machine_uid, test_name, previous, correction,
                                  units=str(body.get("units") or ""),
                                  reason=str(body.get("reason") or ""))
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
            return _labcore_unreadable(exc, "this equipment's correction "
                                            "factors")
        if existing is None:
            return jsonify({"error": f"No correction for “{test_name}”."}), 404
        # Removing an offset changes every future reading exactly as setting
        # one does. A removal reported as done that did not happen leaves the
        # bench quietly still applying it, and the editor showing that it does
        # not.
        try:
            _confirmed_write(
                "DELETE FROM lem_correction_factors "
                "WHERE machine_uid = ? AND test_name = ?",
                [machine_uid, test_name],
                what=f"the correction for “{test_name}” was NOT removed and "
                     f"this instrument is still applying it")
        except LabCoreUnavailable as exc:
            return _labcore_failed(
                exc, "removing the correction for “{0}”".format(test_name))
        # The bench must re-read: from its point of view an offset going away is
        # the same event as one arriving. Gated the same way, and for the same
        # reason — see the save above.
        app.config["LIVE"].mark_stale(machine_uid, STALE_CORRECTIONS)
        # A removal is a change TO ZERO, not an absence. The readings after it
        # really are corrected by nothing, and a hole in the trail cannot say
        # when that started.
        _record_correction_change(
            machine_uid, test_name, existing["correction"], 0.0,
            units=str(existing.get("units") or ""),
            reason=str((request.get_json(silent=True) or {}).get("reason")
                       or ""))
        _audit("correction factor removed", machine_uid,
               {"test": test_name, "previous": existing["correction"],
                "new": 0.0})
        snapshots.refresh_soon()
        return jsonify({"ok": True, "deleted": test_name})

    # ── /api/equipment — the record beside the readings ────────────────
    #
    # Levels, documents and corrective actions. Three stores that shipped
    # tested and reachable from nothing; this is the boundary over them, and
    # the boundary is where all four of the rules below actually live.
    #
    # 1. NOTHING IS WRITTEN AGAINST EQUIPMENT THAT DOES NOT EXIST. LabCore has
    #    no foreign keys, so such a row is accepted and then unreachable
    #    forever — `_equipment_gate`, which also keeps "no such instrument"
    #    apart from "could not ask".
    # 2. A BLIP READS AS A BLIP. Never an empty tab and never a bare 500:
    #    "no documents", "nothing open" and "no history" are sentences an
    #    operator acts on. The stores raise rather than degrading; these routes
    #    turn that into `_labcore_unreadable`.
    # 3. EVERY MUTATION CARRIES `by`. The session user, on every call. An
    #    action nobody can be shown to have taken is the gap the corrective
    #    action record exists to close.
    # 4. THE FLEET-WIDE ANSWERS ARE ONE READ EACH. A badge asked per
    #    instrument on a page that draws sixty of them is the N+1 the snapshot
    #    design forbids. None of these is on the floor's 2s poll — the floor
    #    gets its levels free out of the snapshot (see `/api/machines`).

    def _level_failed(exc, what: str):
        """A store exception, told apart as a request error or an outage.

        `LevelStore` raises `ValueError` for the three things a person can get
        wrong — a blank name, a duplicate, a level that is gone — and
        `LabCoreError` for everything else. Collapsing them would either put an
        outage banner on a typo or, far worse, tell somebody their level is
        gone because a queue was busy.
        """
        if isinstance(exc, LabCoreError):
            return _labcore_failed(exc, what)
        return jsonify({"error": str(exc), "saved": False}), 400

    @app.route("/api/equipment/levels")
    def api_list_levels():
        """The ladder and the settings default, for the picker and settings.

        Read live rather than out of the snapshot: this answers a page a person
        just opened, and a level created a second ago has to be in it. The
        FLOOR does not call this — it gets the same ladder out of
        `/api/machines` at zero LabCore ops.
        """
        try:
            ladder = level_store.levels()
            stored = level_store.stored_default_uid()
        except LabCoreError as exc:
            # Never `{"levels": []}`. An empty ladder is "this lab is flat",
            # which is a real state a lot of labs are in — so degrading to it
            # would be indistinguishable from the truth, and the picker would
            # report every level as deleted.
            return _labcore_unreadable(exc, "the lab's levels")
        from levels import ground_level_uid, resolve_default
        return jsonify({"levels": [l.to_dict() for l in ladder],
                        "default_level": resolve_default(ladder, stored),
                        "ground_level": ground_level_uid(ladder)})

    @app.route("/api/equipment/levels", methods=["POST"])
    def api_create_level():
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        rank = body.get("rank")
        try:
            level = level_store.create(
                body.get("name"), None if rank is None else int(rank))
        except (LabCoreError, ValueError) as exc:
            return _level_failed(exc, "the new level")
        snapshots.refresh_soon()
        _audit("level created", "", {"level": level.to_dict()})
        return jsonify({"ok": True, "level": level.to_dict()})

    @app.route("/api/equipment/levels/<uid>/rename", methods=["POST"])
    def api_rename_level(uid):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        try:
            level = level_store.rename(uid, body.get("name"))
        except (LabCoreError, ValueError) as exc:
            return _level_failed(exc, "the level's name")
        snapshots.refresh_soon()
        _audit("level renamed", "", {"level": level.to_dict()})
        return jsonify({"ok": True, "level": level.to_dict()})

    @app.route("/api/equipment/levels/<uid>", methods=["DELETE"])
    def api_delete_level(uid):
        """Drop a plane. The equipment on it falls to the ground, never off the
        map — see `levels.placements`."""
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        try:
            level_store.delete(uid)
        except LabCoreError as exc:
            return _labcore_failed(
                exc, "the level",
                "The level row and its placements are removed separately, so "
                "press Delete again — it picks up where it left off.")
        snapshots.refresh_soon()
        _audit("level deleted", "", {"level_uid": uid})
        return jsonify({"ok": True, "deleted": uid})

    @app.route("/api/equipment/default-level", methods=["POST"])
    def api_set_default_level():
        """The floor-wide VIEW default: what the picker opens on.

        It moves nothing, and that separation is load-bearing. A default that
        decided where unplaced equipment is DRAWN would relocate the whole
        fleet for everybody the moment one person changed this drop-down, with
        `lem_machine_level` still empty and nothing on the map to say anything
        had happened. `levels.placements` cannot even be handed this value.
        """
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        uid = str(body.get("level_uid") or "").strip()
        try:
            level_store.set_default_level(uid)
        except (LabCoreError, ValueError) as exc:
            return _level_failed(exc, "the default level")
        snapshots.refresh_soon()
        _audit("default level set", "", {"level_uid": uid})
        return jsonify({"ok": True, "default_level": uid})

    @app.route("/api/equipment/<machine_uid>/level", methods=["POST"])
    def api_assign_level(machine_uid):
        """Stand one instrument on one level. Blank unassigns it, which draws
        it on the ground rather than nowhere."""
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        refusal = _equipment_gate(machine_uid)
        if refusal is not None:
            return refusal
        body = request.get_json(silent=True) or {}
        level_uid = str(body.get("level_uid") or "").strip()
        try:
            level_store.assign(machine_uid, level_uid,
                               by=session.get("user", ""))
        except (LabCoreError, ValueError) as exc:
            return _level_failed(exc, "this equipment's level")
        snapshots.refresh_soon()
        return jsonify({"ok": True, "machine_uid": machine_uid,
                        "level_uid": level_uid})

    def _step_level(machine_uid, delta):
        refusal = _equipment_gate(machine_uid)
        if refusal is not None:
            return refusal
        try:
            landed = level_store.move(machine_uid, delta,
                                      by=session.get("user", ""))
        except (LabCoreError, ValueError) as exc:
            return _level_failed(exc, "this equipment's level")
        snapshots.refresh_soon()
        return jsonify({"ok": True, "machine_uid": machine_uid,
                        "level_uid": landed})

    @app.route("/api/equipment/<machine_uid>/level/up", methods=["POST"])
    def api_level_up(machine_uid):
        """Clamped at the top, deliberately. A wrap would look, to the person
        holding the mouse, exactly like the instrument falling into the
        basement."""
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        return _step_level(machine_uid, 1)

    @app.route("/api/equipment/<machine_uid>/level/down", methods=["POST"])
    def api_level_down(machine_uid):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        return _step_level(machine_uid, -1)

    # ── documents ──────────────────────────────────────────────────────
    #
    # A note on the URL shapes. `/api/equipment/<machine_uid>/documents` and
    # `/api/equipment/documents/<uid>` are the same depth, and Werkzeug matches
    # the STATIC segment first — so an instrument whose uid were literally
    # "documents" would lose its tab. Module-generated uids are hex; the
    # collision is named here rather than guarded against, because a guard
    # would have to reserve a word in the wire contract.

    def _document_failed(exc, what: str):
        """A document store failure, told apart by what actually failed.

        `DocumentStoreError` covers two very different things and the operator
        needs them apart: LabCore would not take the metadata (a busy queue,
        clears in seconds) or the FOLDER could not be written (a full disk, a
        documents root that a deploy moved). Only the first is "try again".
        """
        cause = getattr(exc, "__cause__", None)
        if isinstance(cause, LabCoreError):
            return _labcore_failed(cause, what)
        return jsonify({
            "error": "{0} was NOT saved — the document store could not be "
                     "written.".format(what[:1].upper() + what[1:]),
            "detail": str(exc), "saved": False, "retry": False,
            "storage": "unwritable"}), 503

    @app.route("/api/equipment/<machine_uid>/documents")
    def api_list_documents(machine_uid):
        """This instrument's certificates and manuals, newest first.

        No auth: the floor is anonymous and this is a read. An empty list here
        MEANS the instrument has no documents, which is only true because the
        store raises rather than degrading.
        """
        try:
            docs = document_store.documents(machine_uid)
        except DocumentError as exc:
            return _labcore_unreadable(exc, "this equipment's documents")
        return jsonify({"machine_uid": machine_uid,
                        "documents": [d.to_dict() for d in docs]})

    @app.route("/api/equipment/<machine_uid>/documents", methods=["POST"])
    def api_upload_document(machine_uid):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        refusal = _equipment_gate(machine_uid)
        if refusal is not None:
            return refusal
        upload = request.files.get("file")
        if upload is None or not str(upload.filename or "").strip():
            return jsonify({"error": "No file was sent."}), 400
        try:
            # The bounded door: it stops one chunk past the ceiling instead of
            # accumulating a 400 MB mis-drop on a box that is also drawing the
            # floor.
            data = read_upload(upload.stream)
            doc = document_store.save(machine_uid, upload.filename, data,
                                      uploaded_by=session.get("user", ""),
                                      content_type=upload.mimetype or "")
        except DocumentRejected as exc:
            # A decision about the FILE, not an outage: the wrong kind of file,
            # an empty one, or bytes that are not what the name claims. Nothing
            # to retry — the answer is to pick a different file.
            return jsonify({"error": str(exc), "saved": False,
                            "retry": False}), 400
        except DocumentStoreError as exc:
            return _document_failed(exc, "this document")
        _audit("document uploaded", machine_uid,
               {"document": doc.uid, "filename": doc.filename,
                "bytes": doc.size_bytes})
        return jsonify({"ok": True, "document": doc.to_dict()})

    @app.route("/api/equipment/documents/<uid>/download")
    def api_download_document(uid):
        try:
            doc = document_store.get(uid)
        except DocumentError as exc:
            return _labcore_unreadable(exc, "this document")
        if doc is None:
            # Reached only through a read that SUCCEEDED, so this really is a
            # fact about the uid.
            return jsonify({"error": "No such document."}), 404
        try:
            _doc, data = document_store.fetch(uid)
        except DocumentStoreError as exc:
            cause = getattr(exc, "__cause__", None)
            if isinstance(cause, LabCoreError):
                return _labcore_unreadable(cause, "this document")
            # Listed, and its bytes are gone. Not a blip and not a 404: the row
            # says the certificate exists, so this is damage somebody has to be
            # told about rather than a missing page. A zero-byte PDF handed to
            # an auditor would look like our answer.
            logger.warning("document %r is listed and its file is missing: %s",
                           uid, exc)
            return jsonify({"error": str(exc), "retry": False,
                            "storage": "missing"}), 500
        response = app.response_class(data, mimetype=doc.content_type)
        # RFC 6266, both fields: WSGI headers are latin-1 and
        # `Prüfzertifikat.pdf` is an ordinary certificate here.
        response.headers["Content-Disposition"] = content_disposition(
            doc.filename)
        response.headers["Content-Length"] = str(len(data))
        return response

    @app.route("/api/equipment/documents/<uid>", methods=["DELETE"])
    def api_delete_document(uid):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        try:
            doc = document_store.get(uid)
        except DocumentError as exc:
            return _labcore_unreadable(exc, "this document")
        if doc is None:
            return jsonify({"error": "No such document."}), 404
        try:
            document_store.delete(uid)
        except DocumentStoreError as exc:
            return _document_failed(exc, "removing this document")
        _audit("document deleted", doc.machine_uid,
               {"document": doc.uid, "filename": doc.filename})
        return jsonify({"ok": True, "deleted": uid})

    @app.route("/api/equipment/document-counts")
    def api_document_counts():
        """How many documents each instrument has — ONE read for the fleet.

        The endpoint exists so a UI drawing sixty cards never asks sixty times.
        The fleet comes from the snapshot (zero ops) and the counts are one
        `COUNT(*) … GROUP BY`.

        This is the one read in the document store allowed to answer "nothing"
        when it does not know, and it is a granted exemption rather than a
        swallowed exception: it is a badge on a page that already carries its
        own staleness banner, it is a count and not a list, and nobody produces
        it during an audit. The TAB is the opposite on all three counts and
        raises.
        """
        try:
            fleet = [m["machine_uid"] for m in _machine_list()]
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "the equipment list")
        return jsonify({"counts": document_counts_by_machine(document_store,
                                                             fleet)})

    # ── corrective actions and the timeline (ISO/IEC 17025 §8.7) ───────

    def _action_failed(exc, what: str):
        """A lifecycle refusal is a 409, a bad field is a 400, LabCore is 502/3.

        `ActionLifecycleError` says a move the record does not allow was asked
        for — closing something nobody verified, re-closing something finished.
        That is a conflict with the record's state, not a malformed request, and
        the message already names what to do instead.
        """
        if isinstance(exc, LabCoreError):
            return _labcore_failed(exc, what)
        if isinstance(exc, ActionLifecycleError):
            return jsonify({"error": str(exc), "saved": False}), 409
        return jsonify({"error": str(exc), "saved": False}), 400

    def _action_or_404(uid):
        """`(action, None)` or `(None, answer)`. Never invents a 404 from a
        blip — `get()` raises when it could not ask."""
        try:
            action = equipment_history.actions.get(uid)
        except LabCoreError as exc:
            return None, _labcore_unreadable(exc, "this corrective action")
        if action is None:
            return None, (jsonify({"error": "No such corrective action."}), 404)
        return action, None

    @app.route("/api/equipment/<machine_uid>/history")
    def api_equipment_history(machine_uid):
        """One instrument's whole history, merged: runs, QC verdicts, config
        changes, correction factors, PM completions and corrective actions.

        Five reads, and deliberately NOT served from the snapshot: a person
        opens this and reads it, so it costs nothing when nobody is looking —
        which is the rule the snapshot design exists to keep. It is not on any
        polled page.
        """
        raw = (request.args.get("limit") or "").strip()
        # `all` is the whole record, and it has to be asked for by name.
        # Ryan: "make it actually show the entire database." Measured against
        # live LabCore, one instrument's 26,106 rows read in 2.23s / 13.8 MB —
        # affordable for a person who asked, never as a default on a panel.
        everything = raw.lower() == "all"
        try:
            limit = None if everything else (int(raw) if raw else None)
        except ValueError:
            return jsonify({"error": "limit must be a number, or 'all'."}), 400
        if limit is not None and limit < 1:
            return jsonify({"error": "limit must be at least 1."}), 400
        before = (request.args.get("before") or "").strip() or None

        # THE DEEP READS COME FROM THE LOCAL COPY.
        #
        # A plain open still reads LabCore, so the panel is current when it
        # appears. A walk backwards, or `limit=all`, is served from the mirror:
        # that is where the cost was (26,106 rows at 2.23s, each second a write
        # slot the benches queue behind) and where up-to-five-minutes-stale is
        # invisible on a record that goes back months.
        #
        # An unfilled mirror falls through to LabCore rather than answering
        # "this instrument has no history". The cache is not the record.
        mirror = app.config.get("LOG_MIRROR")
        deep = bool(everything or before)
        mirrored = None
        log_rows, log_cut = None, False
        if deep and mirror is not None and mirror.state()["rows"]:
            # ONE ROW MORE THAN WILL BE SHOWN, exactly as the LabCore path
            # does. Reading exactly `limit` rows cannot tell you whether there
            # were more — and the mirror path used to assume there were not, so
            # a full page of 200 reported itself as the start of the record.
            # Caught while walking Agilent GC 1's 26,106 rows against the live
            # lab: page two said "complete" with twenty-five thousand behind
            # it. `complete` is the one claim this whole feature exists to make
            # true, so it is the one that may not be guessed.
            want = None if everything else int(limit or 0)
            log_rows = mirror.events(machine_uid=machine_uid,
                                     limit=None if want is None else want + 1,
                                     before=before)
            if want is not None and len(log_rows) > want:
                log_rows, log_cut = log_rows[:want], True
            mirrored = mirror.state()["filled_at"]

        try:
            timeline = equipment_history.timeline(
                machine_uid, limit=limit, before=before,
                # The LOG read goes exactly as deep as the page asked for. It
                # used to be pinned at LOG_DEFAULT whatever `limit` said, so a
                # request for 400 entries got 200 log rows and called itself
                # truncated.
                depth=HISTORY_ALL if everything else limit,
                log_rows=log_rows, log_cut=log_cut)
        except LabCoreError as exc:
            # `truncated` and `note` are a CLAIM ABOUT COMPLETENESS. A page that
            # drops the corrective actions during a blip and still says it is
            # showing everything tells a supervisor this instrument has nothing
            # open against it.
            return _labcore_unreadable(exc, "this equipment's history")
        body = _history_in_words(timeline.to_dict())
        # REACHING THE START OF THE RECORD IS THE ANSWER, so it is stated
        # rather than left to be inferred from a page that came back shorter
        # than the one that was asked for — which is also what a failed read
        # looks like from the outside. `complete` is only ever present on an
        # answer that was actually read to the end.
        body["complete"] = not body.get("truncated")
        # Which copy answered, and how old it is. Silence here would let a page
        # present a five-minute-old walk as live without ever saying so.
        body["source"] = "mirror" if mirrored else "labcore"
        if mirrored:
            body["mirrored_at"] = mirrored
        return jsonify(body)

    def _history_in_words(payload: dict) -> dict:
        """The timeline, with its config entries said in English.

        `equipment_history` builds a config entry's summary out of the stored
        `action`, which for a level move is the constant `level_move`. That is
        right for the STORE — the trail must not be rewritten — and wrong on
        screen, where the row above it reads "level created". So the rename
        happens on the way out, through the same `display_action` the Logs page
        uses, and the detail blob becomes the sentence it already contained.

        Only `source == "log"` entries are touched: a corrective action's
        summary is a sentence somebody typed.
        """
        for entry in payload.get("entries") or ():
            if entry.get("source") != "log":
                continue
            detail = entry.get("detail") or {}
            action = str(detail.get("action") or "").strip()
            if not action:
                continue
            said = describe_detail(action, detail)
            entry["summary"] = display_action(action) + (
                " \u2014 " + said if said else "")
        return payload

    @app.route("/api/equipment/<machine_uid>/actions")
    def api_list_actions(machine_uid):
        try:
            actions = equipment_history.actions.for_machine(machine_uid)
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "this equipment's corrective "
                                            "actions")
        now = _now()
        return jsonify({"machine_uid": machine_uid,
                        "actions": [a.to_dict(now) for a in actions]})

    @app.route("/api/equipment/<machine_uid>/actions", methods=["POST"])
    def api_open_action(machine_uid):
        """File one. `trigger_ref` is the identity of the event it answers —
        pass the uid of the log entry the operator is looking at."""
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        refusal = _equipment_gate(machine_uid)
        if refusal is not None:
            return refusal
        body = request.get_json(silent=True) or {}
        try:
            action = equipment_history.actions.open_action(
                machine_uid,
                what_happened=body.get("what_happened"),
                trigger_kind=body.get("trigger_kind") or "other",
                trigger_ref=body.get("trigger_ref") or "",
                test_name=body.get("test_name") or "",
                assigned_to=body.get("assigned_to") or "",
                due_at=body.get("due_at") or "",
                priority=body.get("priority"),
                by=session.get("user", ""))
        except (LabCoreError, ValueError) as exc:
            return _action_failed(exc, "this corrective action")
        _audit("corrective action opened", machine_uid,
               {"action": action.uid, "trigger": action.trigger_kind})
        return jsonify({"ok": True, "action": action.to_dict(_now())})

    @app.route("/api/equipment/actions/<uid>")
    def api_get_action(uid):
        """One action, opened: the record and everything said about it."""
        action, refusal = _action_or_404(uid)
        if refusal is not None:
            return refusal
        try:
            events = equipment_history.actions.events(uid)
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "this action's history")
        return jsonify({"action": action.to_dict(_now()), "events": events})

    def _act(uid, what, run):
        """The five state changes, which differ only in the call they make."""
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        action, refusal = _action_or_404(uid)
        if refusal is not None:
            return refusal
        try:
            changed = run(session.get("user", ""))
        except (LabCoreError, ValueError) as exc:
            return _action_failed(exc, what)
        _audit("corrective action " + what, action.machine_uid,
               {"action": uid})
        return jsonify({"ok": True, "action": changed.to_dict(_now())})

    @app.route("/api/equipment/actions/<uid>/record", methods=["POST"])
    def api_record_action(uid):
        """What was actually done. Rewritable, never erasable — an amendment
        keeps what it replaced in `lem_action_events`."""
        body = request.get_json(silent=True) or {}
        return _act(uid, "actioned", lambda by:
                    equipment_history.actions.record_action(
                        uid, body.get("action_taken"), by=by))

    @app.route("/api/equipment/actions/<uid>/verify", methods=["POST"])
    def api_verify_action(uid):
        """Somebody went back and checked it worked (§8.7.1)."""
        body = request.get_json(silent=True) or {}
        return _act(uid, "verified", lambda by:
                    equipment_history.actions.verify(
                        uid, by=by, note=body.get("note") or ""))

    @app.route("/api/equipment/actions/<uid>/close", methods=["POST"])
    def api_close_action(uid):
        body = request.get_json(silent=True) or {}
        return _act(uid, "closed", lambda by:
                    equipment_history.actions.close(
                        uid, by=by, note=body.get("note") or ""))

    @app.route("/api/equipment/actions/<uid>/withdraw", methods=["POST"])
    def api_withdraw_action(uid):
        """Opened by mistake. Not a delete: the row stays and says who
        withdrew it and why, and it never fills in a verification."""
        body = request.get_json(silent=True) or {}
        return _act(uid, "withdrawn", lambda by:
                    equipment_history.actions.withdraw(
                        uid, by=by, reason=body.get("reason") or ""))

    @app.route("/api/equipment/actions/<uid>/assign", methods=["POST"])
    def api_assign_action(uid):
        """Who owns it, by when, and how urgent — and who changed that."""
        body = request.get_json(silent=True) or {}
        return _act(uid, "assigned", lambda by:
                    equipment_history.actions.assign(
                        uid, assigned_to=body.get("assigned_to"),
                        due_at=body.get("due_at"),
                        priority=body.get("priority"), by=by))

    @app.route("/api/equipment/actions/<uid>/note", methods=["POST"])
    def api_note_action(uid):
        """Append-only, and legal at any point in an action's life, including
        after it is finished — a pointer to a recurrence belongs there."""
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        action, refusal = _action_or_404(uid)
        if refusal is not None:
            return refusal
        body = request.get_json(silent=True) or {}
        try:
            event = equipment_history.actions.add_note(
                uid, body.get("note"), by=session.get("user", ""))
        except (LabCoreError, ValueError) as exc:
            return _action_failed(exc, "this note")
        return jsonify({"ok": True, "event": event,
                        "machine_uid": action.machine_uid})

    @app.route("/api/equipment/open-actions")
    def api_open_actions():
        """Every instrument's open corrective actions — ONE read for the fleet.

        The Monday question, and the badge on an equipment card. Asked per
        instrument on a page that draws the whole floor it would be sixty reads
        for sixty badges; `open_by_machine()` answers all of them at once,
        most-urgent-first.

        Machines with none are absent from `by_machine` and zero in `counts`,
        so a caller can badge on presence without distinguishing "none" from
        "not asked" — the read raises rather than degrading, so neither key can
        be produced by an outage.
        """
        try:
            grouped = equipment_history.actions.open_by_machine()
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "the open corrective actions")
        now = _now()
        return jsonify({
            "by_machine": {uid: [a.to_dict(now) for a in actions]
                           for uid, actions in grouped.items()},
            "counts": {uid: len(actions) for uid, actions in grouped.items()},
            "overdue": {uid: sum(1 for a in actions if a.is_overdue(now))
                        for uid, actions in grouped.items()},
            "total": sum(len(actions) for actions in grouped.values()),
        })

    # ── lab-wide search ────────────────────────────────────────────────
    #
    # The index is built ONCE PER SNAPSHOT and reused, which is the whole
    # reason `lab_search` splits `build_index` from `search`. This route is hit
    # per keystroke from every open box; rebuilding the index per request would
    # make typing cost O(rows) each time, and reading LabCore per request would
    # be one op per CHARACTER per viewer — strictly worse than the
    # 17-ops-per-refresh that `snapshot_service` was built to end.
    #
    # Keyed on `built_at`, which changes if and only if a build was committed.
    # `age_seconds` moves every call and `refreshes` counts attempts including
    # the failed ones that kept the previous rows; neither can key a cache
    # derived from those rows.
    # ── the corpus a Lab ID is actually found in ────────────────────────
    #
    # The floor's snapshot carries the newest EVENT_LIMIT (60) log rows —
    # enough for the activity feed it was built for, and nowhere near enough
    # for "find sample L-37006". Measured on the demo floor: 77 events, so
    # HALF the Lab IDs in the log answered `no_match`, which on screen reads as
    # "that sample does not exist". A search that confidently denies a record
    # the lab holds is worse than no search, and this is the one an assessor
    # types into.
    #
    # So the corpus is its own read, and it is deliberately NOT an arm of the
    # batched statement: every arm is bought with the whole floor's 2-second
    # poll, and 20 000 rows on that path would be paid for by every open screen
    # forever. It rides the snapshot's background thread instead, on its own
    # slower clock — ONE read every SEARCH_CORPUS_SECONDS for the whole
    # building, no matter how many people are typing. A request never triggers
    # it.
    _corpus = {"events": [], "at": None, "rows": 0, "truncated": False,
               "error": "", "ever": False}
    _search_index = {"key": None, "index": None}

    def _refresh_search_corpus(force=False):
        """One read of the log's searchable history, on the poller's thread.

        A failure leaves the previous corpus in place and records why. That is
        the same call `SnapshotService` makes about its own rows: stale history
        still finds yesterday's sample, while an emptied corpus would answer
        "no such sample" about every record in the lab.
        """
        now = _now()
        at = _corpus["at"]
        if not force and at is not None:
            if (now - at).total_seconds() < SEARCH_CORPUS_SECONDS:
                return
        try:
            res = gateway.read_sql(
                "SELECT machine_uid, ts, kind, lab_id, test_name, value, "
                "detail FROM lem_machine_log ORDER BY ts DESC LIMIT ?",
                [SEARCH_CORPUS_ROWS])
            got = labcore_rows(res)
        except LabCoreError as exc:
            _corpus["error"] = str(exc)
            _corpus["at"] = now      # do not retry every cycle on a bad day
            logger.warning("search corpus not refreshed: %s", exc)
            return
        _corpus.update({
            "events": got, "at": now, "rows": len(got), "error": "",
            "ever": True,
            # A cap that binds is REPORTED, never silent: at the ceiling the
            # oldest samples are absent, and "not found" then means "not in
            # the last 20 000 records", which is a different sentence.
            "truncated": len(got) >= SEARCH_CORPUS_ROWS,
        })

    def _search_index_for(snap):
        """The index, rebuilt only when the fleet or the corpus actually moved.

        Keyed on `built_at` (which changes if and only if a snapshot build was
        committed) and the corpus stamp. `age_seconds` moves every call and
        `refreshes` counts attempts including the failed ones that kept the old
        rows — neither can key a cache derived from those rows.
        """
        import lab_search
        from snapshot_service import EVENT_LIMIT, events_from_tables

        # The FIRST search builds the corpus, exactly as `snapshots.get()`
        # builds the first snapshot rather than serving an empty floor: one
        # request pays for it, once, and the poller owns every refresh after
        # that. Without this the corpus is empty until the first background
        # cycle — so the first person to type would be told their sample does
        # not exist, which is the whole bug this exists to fix.
        if not _corpus["ever"] and not _corpus["error"]:
            _refresh_search_corpus(force=True)

        key = (snap.get("built_at") or "", _corpus["at"])
        if _search_index["index"] is not None and _search_index["key"] == key:
            return _search_index["index"]
        # If that read failed, search what the floor already holds rather than
        # nothing at all — 60 rows is a poor corpus, an empty one is a wrong
        # answer, and `corpus.partial` in the response says which it was.
        events = (_corpus["events"] if _corpus["ever"]
                  else events_from_tables(snapshots.tables(), EVENT_LIMIT))
        index = lab_search.build_index(
            machines=snap.get("machines") or [],
            events=events,
            levels=snap.get("levels") or [])
        _search_index.update({"key": key, "index": index})
        return index

    @app.route("/api/search")
    def api_search():
        """Type a Lab ID, an instrument, a method, a standard, a level or a
        person, and find it. Served from the snapshot at ZERO LabCore ops.

        A warming snapshot answers `warming` rather than "no results": at boot
        nothing has been read yet, and "nothing matched" is a sentence somebody
        would act on.
        """
        import lab_search

        query = request.args.get("q", default="", type=str)
        limit = request.args.get("limit", default=None, type=int)
        snap = snapshots.get()
        if not snap.get("ready"):
            snapshots.refresh_soon()
            return jsonify({"state": lab_search.STATE_IDLE, "results": [],
                            "matched": 0, "warming": True,
                            "labcore_online": snap.get("labcore_online", False)})
        answer = lab_search.search(query, _search_index_for(snap), limit=limit)
        answer["warming"] = False
        answer["age_seconds"] = snap.get("age_seconds")

        # AN EXACT LAB ID IS NOT A FUZZY SEARCH TERM.
        #
        # The corpus is the newest SEARCH_CORPUS_ROWS rows of the log. That was
        # most of the table at 41,905 rows; after the history import it is
        # 214,714, so the same 20,000 reaches back about ten days — measured on
        # the live lab, to 2026-08-18. Every sample older than that answered
        # `no_match`, which reads as "this sample was never tested" and is the
        # reason Ryan could not find a sample he had run.
        #
        # A Lab ID is an exact key, so it is looked up rather than hoped for.
        # One indexed point read (`idx_lem_log_lab_ts`), and only when the
        # corpus came up empty AND the query is all digits — a word search stays
        # inside the corpus, or every keystroke becomes a query against a
        # 200,000-row table.
        #
        # A FAILED LOOKUP IS NOT AN ABSENT SAMPLE. If the read is refused the
        # answer says so instead of confirming the `no_match`, because that is
        # the sentence this whole route exists not to say wrongly.
        # What "not found" actually means here. The corpus is the newest
        # SEARCH_CORPUS_ROWS log records; at the ceiling, or before the first
        # corpus read lands, "no such sample" is really "not in what I can
        # see" — a different sentence, and the one an assessor must be given
        # rather than a flat denial. Same rule as every other cap in this app:
        # a bound that binds is reported, never silent.
        answer["corpus"] = {
            "rows": _corpus["rows"] if _corpus["ever"] else None,
            "truncated": bool(_corpus["truncated"]),
            "partial": not _corpus["ever"],
            "stale": bool(_corpus["error"]),
            "refreshed_at": (_corpus["at"].isoformat()
                             if _corpus["at"] else ""),
        }

        # SEARCH THE WHOLE RECORD, not the newest slice of it.
        #
        # Ryan: "have it search through all time". The corpus is the newest
        # SEARCH_CORPUS_ROWS log rows — most of the table before the history
        # import, about ten days after it — so a method, a value or a sample
        # older than that was never in the haystack. The mirror holds every
        # row locally, so this covers all time and costs less than the ten-day
        # window did over the network.
        #
        # It runs whenever the corpus came up short: no match at all, or a
        # match from a corpus that is admittedly clipped. A word search is
        # allowed now too — it is one indexed query against a local file, not
        # a LabCore op.
        corpus_clipped = bool((answer.get("corpus") or {}).get("truncated"))
        if (answer.get("state") == lab_search.STATE_NO_MATCH
                or corpus_clipped):
            # THE MIRROR FIRST, because it holds the WHOLE log locally and
            # answers in microseconds. Against LabCore the same lookup was
            # measured at 8-17 s on the live lab and was being cancelled
            # outright ("query too slow — likely an unindexed scan"), so the
            # local copy is not merely faster here, it is the difference
            # between an answer and a denial.
            rows, mirror = None, app.config.get("LOG_MIRROR")
            if mirror is not None and mirror.state()["rows"]:
                rows = (mirror.by_lab_id(query.strip())
                        if query.strip().isdigit()
                        else mirror.search(query.strip()))
            if rows is None:
                try:
                    res = gateway.read_sql(
                        "SELECT machine_uid, ts, kind, lab_id, test_name, value "
                        "FROM lem_machine_log WHERE lab_id = ? "
                        "ORDER BY ts DESC LIMIT 50", [query.strip()],
                        timeout=30)
                    rows = labcore_rows(res, missing_ok=True)
                except LabCoreError as exc:
                    rows = None
                    answer["corpus"] = dict(answer.get("corpus") or {},
                                            stale=True, error=str(exc))
            if rows:
                # Merge rather than replace: the corpus also holds instruments,
                # levels and standards, and those hits are not in the log.
                keep = [h for h in (answer.get("results") or [])
                        if h.get("kind") != "sample"]
                titles = _machine_list()
                by_uid = {m.get("machine_uid"): m.get("title")
                          for m in titles} if titles else {}
                answer["state"] = lab_search.STATE_OK
                answer["beyond_corpus"] = True
                hits = [{
                    "kind": "sample", "lab_id": r.get("lab_id"),
                    "machine_uid": r.get("machine_uid"),
                    "title": by_uid.get(r.get("machine_uid"))
                             or r.get("machine_uid"),
                    "test_name": r.get("test_name"), "value": r.get("value"),
                    "at": r.get("ts"), "why": "Lab ID",
                } for r in rows]
                # One row per (sample, instrument), newest first, which is
                # what `expandSampleHits` produces on the floor for a corpus
                # hit — so the two paths look the same on screen.
                seen, merged = set(), []
                for h in hits:
                    key = (h["lab_id"], h["machine_uid"])
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(h)
                answer["results"] = keep + merged
                answer["hits"] = answer["results"]
                answer["matched"] = len(answer["results"])
                answer["searched_all_time"] = True
        return jsonify(answer)


    # ── the certificate a QC standard's values rest on ──────────────────
    #
    # `standard_documents.py` shipped fully tested and reachable by nothing —
    # the same "declared but inert" state `levels.py`, `equipment_documents.py`
    # and `equipment_history.py` were found in, where wired and unwired look
    # identical from the outside. `tests/test_certificate_routes.py` is the
    # standing gate.
    #
    # THE STANDARD'S NAME IS NEVER A PATH SEGMENT. It is a human string chosen
    # by whoever created the standard — "Diesel - AO25" — and nothing stops one
    # containing a slash, which as a path segment either 404s or addresses a
    # different rule. `<path:...>` would then sit at the same depth as the
    # by-uid routes and Werkzeug would have to be reasoned about, which is the
    # trap `equipment_documents` had to write a paragraph about. Keeping the
    # name in the query string or the form is that problem not existing.

    def _named_standard():
        """The standard this request is about, or the answer to send instead.

        A missing name is a 400, never an empty list: an empty list reads as
        "this standard has no certificate on file", and that is a sentence
        about a standard nobody named.
        """
        name = (request.args.get("standard")
                or request.form.get("standard") or "").strip()
        if not name:
            return None, (jsonify({
                "error": "Name the QC standard whose certificates you want."}),
                400)
        return name, None

    @app.route("/api/qc-standards/certificates")
    def api_list_certificates():
        name, refusal = _named_standard()
        if refusal is not None:
            return refusal
        try:
            certs = certificate_store.certificates(name)
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "this standard's certificates")
        now = _now()
        return jsonify({
            "standard": name,
            "certificates": [c.to_dict() for c in certs],
            # Which one the standard is actually RESTING on, as opposed to
            # every one ever filed against it. The report and this answer come
            # through the same function so they cannot disagree.
            "current": (certificate_store.current(name, now=now) or None)
                       and certificate_store.current(name, now=now).to_dict(),
        })

    @app.route("/api/qc-standards/certificates", methods=["POST"])
    def api_upload_certificate():
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        name, refusal = _named_standard()
        if refusal is not None:
            return refusal
        upload = request.files.get("file")
        if upload is None or not str(upload.filename or "").strip():
            return jsonify({"error": "No file was sent."}), 400
        try:
            data = read_upload(upload.stream)
            cert = certificate_store.save(
                name, upload.filename, data,
                uploaded_by=session.get("user", ""),
                content_type=upload.mimetype or "",
                expires_at=request.form.get("expires_at"),
                issued_at=request.form.get("issued_at"))
        except CertificateRejected as exc:
            # A decision about the FILE — the wrong kind, an empty one, a date
            # that is not a date. Nothing to retry; pick a different file or
            # fix the date. Kept apart from a refusal on purpose: "the queue is
            # deep, try again" and "that is not a PDF" are opposite
            # instructions to the person holding the certificate.
            return jsonify({"error": str(exc), "saved": False,
                            "retry": False}), 400
        except CertificateStoreError as exc:
            return _document_failed(exc, "this certificate")
        _audit("certificate uploaded", "",
               {"standard": name, "certificate": cert.uid,
                "filename": cert.filename, "bytes": cert.size_bytes,
                "expires_at": cert.expires_at})
        return jsonify({"ok": True, "certificate": cert.to_dict()})

    @app.route("/api/qc-standards/certificates/<uid>/download")
    def api_download_certificate(uid):
        try:
            cert, data = certificate_store.fetch(uid)
        except CertificateRejected as exc:
            return jsonify({"error": str(exc)}), 404
        except CertificateStoreError as exc:
            return _document_failed(exc, "this certificate")
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "this certificate")
        return Response(data, mimetype=cert.content_type or "application/pdf",
                        headers={"Content-Disposition":
                                 content_disposition(cert.filename)})

    @app.route("/api/qc-standards/certificates/<uid>", methods=["DELETE"])
    def api_delete_certificate(uid):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        try:
            cert = certificate_store.get(uid)
            gone = certificate_store.delete(uid)
        except CertificateStoreError as exc:
            return _document_failed(exc, "this certificate")
        except LabCoreError as exc:
            return _labcore_failed(exc, "this certificate")
        if not gone:
            return jsonify({"error": "No such certificate."}), 404
        _audit("certificate deleted", "",
               {"standard": getattr(cert, "standard_name", ""),
                "certificate": uid,
                "filename": getattr(cert, "filename", "")})
        return jsonify({"ok": True})

    @app.route("/api/qc-standards/certificate-expiry")
    def api_certificate_expiry():
        """What is out of date and what is about to be, across the library.

        The one report here whose entire purpose is being produced during an
        assessment, which is why it RAISES rather than degrading: "nothing is
        out of date" must be impossible to produce from an outage.
        """
        within = request.args.get("within_days", type=int)
        try:
            report = expiry_report(certificate_store, now=_now(),
                                   within_days=within)
        except CertificateRejected as exc:
            # A bad `within_days` or `as_of` — a decision about the REQUEST.
            return jsonify({"error": str(exc)}), 400
        except CertificateStoreError as exc:
            # The store's own wrapper around an unreadable LabCore. Caught
            # BEFORE LabCoreError because it is not one — it wraps the cause —
            # and letting it fall through reached the browser as a bare 500,
            # which is the one shape this report may never take: a page that
            # errors is at least honest, but nothing here may ever answer
            # "nothing is out of date" because LabCore could not be read.
            return _document_failed(exc, "the certificate expiry report")
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "the certificate expiry report")

        def rows(certs):
            return [{"standard": c.standard_name, "uid": c.uid,
                     "filename": c.filename, "expires_at": c.expires_at,
                     "issued_at": c.issued_at} for c in certs]

        return jsonify({
            "as_of": report.get("as_of"),
            "within_days": report.get("within_days"),
            "expired": rows(report.get("expired") or []),
            "expiring": rows(report.get("expiring") or []),
            "superseded": rows(report.get("superseded") or []),
            "covered": list(report.get("covered") or []),
        })


    # ── measurement uncertainty (ISO/IEC 17025 §7.6, SOP QMU 1.001) ─────
    #
    # `uncertainty.py` shipped fully tested and reachable by nothing, and left a
    # tripwire saying so — the fourth store in this app to do that, after
    # levels, documents and history. This is the wiring; the tripwire is gone
    # and `tests/test_uncertainty_web.py` replaces it.
    #
    # NOTHING HERE COMPUTES ON READ. An estimate is a record: it is calculated
    # once, from a stated window, and frozen. A number recomputed on page load
    # is not a record, because the inputs move under it — which is the whole
    # argument of the design doc's gap 4.

    def _estimate_or_404(estimate_id):
        try:
            est = uncertainty_store.get(estimate_id)
        except LabCoreError as exc:
            return None, _labcore_unreadable(exc, "this uncertainty estimate")
        if est is None:
            return None, (jsonify({"error": "No such estimate."}), 404)
        return est, None

    @app.route("/api/uncertainty")
    def api_uncertainty_list():
        """Current approved estimates, and what has gone stale.

        A read that failed is NOT an empty register: "no uncertainty estimates
        on file" is itself a finding at an assessment, and it must be
        impossible to produce from an outage.
        """
        try:
            current = uncertainty_store.list_current()
            stale = uncertainty_store.stale(now=_now())
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "the uncertainty register")
        return jsonify({
            "estimates": [e.to_dict() for e in current],
            "stale": [t.to_dict() if hasattr(t, "to_dict") else str(t)
                      for t in stale],
        })

    @app.route("/api/uncertainty/<machine_uid>/<path:test_name>")
    def api_uncertainty_history(machine_uid, test_name):
        """Every estimate for one measurand, newest first.

        Supersession is the revision mechanism, so the history IS the audit
        trail — an assessor walks backwards through it.
        """
        try:
            history = uncertainty_store.history_for(machine_uid, test_name)
            current = uncertainty_store.current_for(machine_uid, test_name)
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "this measurand's estimates")
        return jsonify({
            "machine_uid": machine_uid, "test_name": test_name,
            "history": [e.to_dict() for e in history],
            "current": current.to_dict() if current else None,
        })

    @app.route("/api/uncertainty/compute", methods=["POST"])
    def api_uncertainty_compute():
        """Calculate and SAVE a draft. It is never approved here.

        SOP 2.10's Register entry is signed. A number that signed itself the
        moment it was calculated records nobody's judgement.
        """
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        machine_uid = str(body.get("machine_uid") or "").strip()
        test_name = str(body.get("test_name") or "").strip()
        if not machine_uid or not test_name:
            return jsonify({"error": "Name the equipment and the test."}), 400
        kw = {k: body[k] for k in
              ("control_limit", "control_limit_k", "s_r", "certificate",
               "astm_r", "bias_decision", "short_series_justification", "notes")
              if k in body}
        try:
            est = uncertainty_store.compute(
                machine_uid, test_name,
                window_start=body.get("window_start"),
                window_end=body.get("window_end"),
                rw_route=str(body.get("rw_route") or "control_sample"),
                **kw)
            uncertainty_store.save(est, computed_by=session.get("user", ""))
        except EstimateRefused as exc:
            # The evidence does not permit the route that was asked for. This
            # is the common answer in this laboratory today and it is not an
            # error condition — the sentence names the route that IS permitted.
            return jsonify({"error": str(exc),
                            "route": getattr(exc, "route", ""),
                            "evidence": _route_evidence(machine_uid, test_name,
                                                        body)}), 400
        except LabCoreError as exc:
            return _labcore_failed(exc, "this uncertainty estimate")
        _audit("uncertainty computed", machine_uid,
               {"estimate": est.estimate_id, "test": test_name,
                "route": est.rw_route})
        return jsonify({"ok": True, "estimate": est.to_dict()})

    def _route_evidence(machine_uid, test_name, body):
        """Which routes the evidence permits, and why not for the rest."""
        try:
            series = uncertainty_store.read_series(
                machine_uid, test_name,
                window_start=body.get("window_start"),
                window_end=body.get("window_end"))
            verdicts = uncertainty.route_evidence(
                series, control_limit=body.get("control_limit"),
                s_r=body.get("s_r"), now=_now())
        except Exception:                       # noqa: BLE001 — advisory only
            return {}
        return {name: {"permitted": v.permitted, "reason": v.reason}
                for name, v in verdicts.items()}

    @app.route("/api/uncertainty/<estimate_id>/approve", methods=["POST"])
    def api_uncertainty_approve(estimate_id):
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        est, refusal = _estimate_or_404(estimate_id)
        if refusal is not None:
            return refusal
        try:
            uncertainty_store.approve(estimate_id, session.get("user", ""),
                                      when=_now())
            est = uncertainty_store.get(estimate_id)
        except EstimateRefused as exc:
            # Already signed. A second signature would overwrite the name and
            # the date of the person who actually reviewed it.
            return jsonify({"error": str(exc)}), 409
        except LabCoreError as exc:
            return _labcore_failed(exc, "this approval")
        _audit("uncertainty approved", est.machine_uid,
               {"estimate": estimate_id, "test": est.test_name})
        return jsonify({"ok": True, "estimate": est.to_dict()})

    @app.route("/api/uncertainty/<estimate_id>/exclude", methods=["POST"])
    def api_uncertainty_exclude(estimate_id):
        """Drop a point, with an investigated cause and an NCR reference.

        TR 537 and SOP 2.9 both forbid automatic outlier rejection: statistical
        extremity alone is not grounds. And if the excluded run represents work
        already reported to a customer, clause 7.10 is engaged — which is why
        the nonconforming-work reference is required rather than encouraged.
        The result SUPERSEDES rather than mutating; nothing is recomputed in
        place.
        """
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        _est, refusal = _estimate_or_404(estimate_id)
        if refusal is not None:
            return refusal
        body = request.get_json(silent=True) or {}
        try:
            exclusion = Exclusion(ts=str(body.get("ts") or ""),
                                  value=body.get("value"),
                                  cause=str(body.get("cause") or ""),
                                  ncr_ref=str(body.get("ncr_ref") or ""))
            new = uncertainty_store.exclude(estimate_id, exclusion,
                                            computed_by=session.get("user", ""),
                                            now=_now())
        except (EstimateRefused, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        except LabCoreError as exc:
            return _labcore_failed(exc, "this exclusion")
        _audit("uncertainty point excluded", new.machine_uid,
               {"estimate": new.estimate_id, "supersedes": estimate_id,
                "cause": exclusion.cause, "ncr": exclusion.ncr_ref})
        return jsonify({"ok": True, "estimate": new.to_dict()})

    @app.route("/api/uncertainty/<estimate_id>/register")
    def api_uncertainty_register(estimate_id):
        """The SOP 2.10 Register entry — the thing that goes in the file."""
        est, refusal = _estimate_or_404(estimate_id)
        if refusal is not None:
            return refusal
        return jsonify({"estimate_id": estimate_id,
                        "register": est.to_register_row(),
                        "fields": uncertainty.REGISTER_FIELDS})

    @app.route("/api/equipment/register")
    def api_action_register():
        """Every corrective action in a window, open and resolved.

        `/api/equipment/open-actions` answers the supervisor's question — what
        is still outstanding. This answers the assessor's: what happened over
        this period and what was done about it. At an assessment the CLOSED
        actions are the interesting ones, so this deliberately does not filter
        them out.

        A read that failed is never an empty register. "No corrective actions
        were raised this year" is a sentence somebody would act on, and it must
        never be produced by an outage.
        """
        start = request.args.get("start", default="", type=str)
        end = request.args.get("end", default="", type=str)
        machine_uid = request.args.get("machine_uid", default="", type=str)
        try:
            actions = equipment_history.actions.register(
                start=start, end=end, machine_uid=machine_uid or None)
            repeats = equipment_history.actions.recurrences(start=start, end=end)
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "the corrective-action register")
        now = _now()
        titles, named = _titles_soft()
        return jsonify({
            "actions": [dict(a.to_dict(now), machine=titles.get(a.machine_uid, ""))
                        for a in actions],
            "total": len(actions),
            "window": {"start": start, "end": end},
            # Keyed "uid::test" rather than a tuple — JSON has no tuple key, and
            # a caller matching on it needs the two halves back apart.
            "recurrences": [
                {"machine_uid": uid, "machine": titles.get(uid, ""),
                 "test_name": test, "count": len(items),
                 "uids": [a.uid for a in items]}
                for (uid, test), items in sorted(
                    repeats.items(), key=lambda kv: -len(kv[1]))],
            "names_read": named,
        })

    @app.route("/api/export/corrective-actions.csv")
    def api_export_corrective_actions():
        """The corrective-action register as a file — the second thing an
        assessor asks for after the QC history.

        One column per lifecycle state rather than a `status` word, because the
        record's value is that it says WHO did each thing and WHEN. A register
        that says "closed" without naming who closed it is not evidence.
        """
        start = request.args.get("start", default="", type=str)
        end = request.args.get("end", default="", type=str)
        try:
            actions = equipment_history.actions.register(start=start, end=end)
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "the corrective-action register")
        titles, named = _titles_soft()
        now = _now()
        rows = [[a.machine_uid, titles.get(a.machine_uid, ""), a.uid,
                 a.trigger_kind, a.trigger_ref, a.test_name, a.priority,
                 a.what_happened,
                 a.opened_at, a.opened_by,
                 a.action_taken, a.action_at, a.action_by,
                 a.verified_at, a.verified_by, a.verification,
                 a.closed_at, a.closed_by, a.closed_note, a.outcome,
                 a.assigned_to, a.due_at,
                 "yes" if a.is_overdue(now) else "no",
                 a.state]
                for a in actions]
        return _csv_response(
            rows,
            ["machine_uid", "machine", "action_uid", "trigger_kind",
             "trigger_ref", "test_name", "priority", "what_happened",
             "opened_at", "opened_by",
             "action_taken", "action_at", "action_by",
             "verified_at", "verified_by", "verification",
             "closed_at", "closed_by", "closed_note", "outcome",
             "assigned_to", "due_at", "overdue", "state"],
            "LEM corrective actions.csv",
            note="" if named else NAMES_UNREAD)

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
            return _labcore_unreadable(exc, "this equipment's history")
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
        # a retired uid, and does not withhold the file — but the file now SAYS
        # so, because "reported it in the log" means reported it to a file on
        # the server that whoever opens this download will never see.
        titles, named = _titles_soft()
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
                             "LEM QC history.csv",
                             note="" if named else NAMES_UNREAD)

    @app.route("/api/machines/<machine_uid>/events")
    def api_machine_events(machine_uid):
        limit = request.args.get("limit", default=100, type=int)
        try:
            events = state_reader.events(machine_uid, limit)
        except LabCoreError as exc:
            # "This bench has never reported" is a reason people walk over and
            # start touching an instrument that is running fine.
            return _labcore_unreadable(exc, "this equipment's history")
        # A `config` row's `test_name` is a stored ACTION, and one of them is
        # the constant `level_move`. The rail printed it raw beside rows that
        # read as English. Translated on the way out, exactly like /api/logs —
        # `test_name` itself still rides along untouched.
        for event in events:
            if str(event.get("kind") or "") != "config":
                continue
            event["test_label"] = display_action(event.get("test_name") or "")
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
                                     "clear a piece of equipment."}), 400
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
    import qc_samples as qc_samples_mod
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
            body_json.update({"samples": [], "labcore_online": False,
                              "default_qc_expire_hours":
                                  qc_samples_mod.QC_WINDOW_DEFAULT_HOURS})
            return jsonify(body_json), status
        payload = []
        for sample in samples:
            data = sample.to_dict()
            for test, spec in zip(data["tests"], sample.tests):
                low, high = spec.limits()
                test["low"] = low
                test["high"] = high
            payload.append(data)
        # A test's `qc_expire_hours` of 0 means "fall through", so the editor
        # would otherwise draw an empty box with nothing to say what leaving it
        # empty gets you. The library states the bottom of the chain here rather
        # than the page hard-coding a 24 that could drift away from
        # `resolve_qc_window`.
        return jsonify({
            "samples": payload,
            "default_qc_expire_hours": qc_samples_mod.QC_WINDOW_DEFAULT_HOURS,
        })

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
            #
            # `moved` is reported on the failure too, and it is the count that
            # ACTUALLY moved — the whole reason `changeover` hangs its progress
            # on the exception. Omitting it would leave the operator unable to
            # tell a changeover that created the lot and moved nothing from one
            # that moved every instrument but the last.
            return _labcore_failed(
                exc, "the rest of this changeover",
                "Some equipment may already point at the new lot. Re-run the "
                "changeover — it picks up the ones still on the old one.",
                moved=getattr(exc, "moved", 0))

        # The certificate deliberately does NOT come across. A changeover is a
        # new LOT: different batch, different assay, different certificate.
        # Inheriting the specs is right — they are what the lab expects of the
        # material — but inheriting the old lot's document would attach a COA
        # describing a batch this is not, which is worse than having none
        # because it looks complete.
        #
        # So the answer says the new lot needs one, and says it only when the
        # old lot actually had one: an unconditional nag is a nag people learn
        # to close. A failed read here does not fail the changeover, which has
        # already happened — it just cannot promise the prompt.
        needed = False
        try:
            needed = bool(certificate_store.certificates(
                str(body.get("old_name") or "")))
        except LabCoreError:
            pass
        return jsonify({"ok": True, "moved": moved,
                        "certificate_needed": needed})

    @app.route("/api/qc-samples", methods=["DELETE"])
    def api_delete_qc_sample():
        """Remove a QC standard — and decide what happens to its certificate.

        A certificate is keyed by the standard's NAME, and the library renames
        by saving the new name then deleting the old. So this route is where a
        rename either carries the document across or loses it, and until the
        floor could upload one there was nothing to lose.

        `renamed_to` is what tells the two apart. It is sent by the rename path
        and by nothing else; a plain delete never guesses that a deletion was
        "really" a rename, because guessing wrong moves a controlled document
        onto a standard it does not describe.
        """
        if not authed():
            return jsonify({"error": "Authentication required"}), 401
        body = request.get_json(silent=True) or {}
        name = str(body.get("name") or "")
        renamed_to = str(body.get("renamed_to") or "").strip()

        # Read the certificates BEFORE anything is removed. Deciding what to do
        # about them after the standard is gone means deciding it from a table
        # that no longer says which standard they belonged to.
        try:
            held = certificate_store.certificates(name)
        except LabCoreError as exc:
            return _labcore_unreadable(exc, "this standard's certificates")

        if renamed_to and renamed_to == name.strip():
            # "Remove this standard, and move its certificates onto itself."
            # Neither half can be honoured. Refused here, explicitly, so it
            # cannot fall through to the certificate-conflict branch below and
            # be reported as a conflict — which is not what is wrong with it.
            return jsonify({
                "error": "A standard cannot be renamed to the name it already "
                         "has. Nothing was changed.",
                "retry": False}), 400

        if renamed_to:
            # The target has to EXIST. Repointing at a name the library does
            # not hold produces an orphan wearing a valid label — worse than a
            # plain orphan, because `orphaned_certificates` cannot see it.
            try:
                # missing_ok=False: an empty library because the table is
                # absent would reject every rename with "there is no standard
                # called that", which is a sentence about a read that failed.
                known = {s.name for s in
                         sample_store.list_samples(missing_ok=False)}
            except LabCoreError as exc:
                return _labcore_unreadable(exc, "the QC library")
            if renamed_to not in known:
                return jsonify({
                    "error": f"There is no QC standard called "
                             f"\u201c{renamed_to}\u201d to move this one's "
                             f"certificates onto. Nothing was changed.",
                    "retry": False}), 400
            try:
                moved = certificate_store.repoint_certificates(name, renamed_to)
            except (CertificateRejected, CertificateStoreError) as exc:
                return jsonify({"error": str(exc), "retry": False}), 400
            except LabCoreError as exc:
                return _labcore_failed(
                    exc, "moving this standard's certificates",
                    "The standard was NOT removed, so nothing is orphaned. "
                    "Try the rename again.")
            if moved:
                _audit("certificate moved", "",
                       {"from": name, "to": renamed_to, "certificates": moved})
        elif held:
            # Deleting a standard that still has a certificate on it. The
            # certificate is a controlled document and this is a library
            # tidy-up, so it is REFUSED rather than obeyed: destroying an
            # assessor-visible record as a side effect of removing a row is the
            # worse of the two mistakes, and it is not undoable.
            return jsonify({
                "error": "This standard still holds %d certificate%s (%s). "
                         "Remove them first, or rename the standard instead — "
                         "a rename carries them across." % (
                             len(held), "" if len(held) == 1 else "s",
                             ", ".join(c.filename for c in held[:3])),
                "retry": False}), 409

        try:
            sample_store.delete(name)
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
        try:
            names = gateway.get_test_names()
        except Exception:                       # a client that raises outright
            names = None
        if names is None:
            # Couldn't ask LabCore. The DISTINCT scan is the safety net, and it
            # needs a generous timeout: it reads every result row in the lab.
            try:
                res = gateway.read_sql(
                    "SELECT DISTINCT test_name FROM sample_tests "
                    "WHERE test_name IS NOT NULL AND TRIM(test_name) != '' "
                    "ORDER BY test_name", timeout=60)
                # `labcore_rows`, not `res.get("rows") or []` (2026-08-25).
                # This picker holds the ONLY legal test names — LEM has none of
                # its own (CLAUDE.md) — so an empty list is not a harmless
                # degrade: it is a page that offers nothing and looks exactly
                # like a lab whose methods were never set up. missing_ok=False
                # because `sample_tests` is LabCore's own table, not a `lem_*`
                # one this app creates at boot.
                found = labcore_rows(res, missing_ok=False)
            except LabCoreError as exc:
                return _labcore_unreadable(exc, "the list of test methods")
            except Exception as exc:
                return _labcore_unreadable(
                    LabCoreUnavailable(str(exc)), "the list of test methods")
            names = [r.get("test_name") for r in found]
        # Only a real answer is cached. An empty one is not cached either — a
        # lab that adds its first method should not wait out a cache built
        # before it existed.
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
