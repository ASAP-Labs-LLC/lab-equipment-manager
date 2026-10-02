#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dedupe_routes.py — the dedupe approval flow over HTTP (transfer spec §10.5).

    GET  /api/dedupe/dry-run    ?machine_uid=&since=   the report; reads only
    POST /api/dedupe/approve    {machine_uid, label, run_id, decision?}
    POST /api/dedupe/apply      {approval_id}
    POST /api/dedupe/reinstate  {log_ids: [...], reason}

Signed in, every one. The person recorded as approver, applier or reinstater
is the session's user — a name in the request body is ignored, because the
approval is the evidence that a person decided (D7).

Status codes say which sentence is true: 401 nobody is signed in; 400 the
request is malformed; 409 refused (a rejected approval, a report the record
has moved past, the store is not LEM's); 503 the record could not be read —
never a 200 with an empty report.
"""

from __future__ import annotations

from flask import jsonify, request, session

import dedupe


def register(app, gateway) -> None:
    def _who():
        return str(session.get("user") or "").strip()

    def _refusal(exc):
        if isinstance(exc, dedupe.DedupeReadError):
            return jsonify({"error": str(exc)}), 503
        return jsonify({"error": str(exc)}), 409

    @app.get("/api/dedupe/dry-run")
    def dedupe_dry_run():
        if not _who():
            return jsonify({"error": "Authentication required"}), 401
        uid = (request.args.get("machine_uid") or "").strip() or None
        since = (request.args.get("since") or "").strip() or None
        try:
            return jsonify(dedupe.dry_run(gateway, uid, since=since))
        except dedupe.DedupeError as exc:
            return _refusal(exc)

    def _body():
        body = request.get_json(silent=True)
        return body if isinstance(body, dict) else None

    @app.post("/api/dedupe/approve")
    def dedupe_approve():
        who = _who()
        if not who:
            return jsonify({"error": "Authentication required"}), 401
        body = _body()
        if body is None or not all(isinstance(body.get(k), str) and body[k]
                                   for k in ("machine_uid", "label",
                                             "run_id")):
            return jsonify({"error": "Expected machine_uid, label and "
                                     "run_id from a dry run."}), 400
        try:
            return jsonify(dedupe.approve(
                gateway, body["machine_uid"], body["label"], body["run_id"],
                approved_by=who,
                decision=str(body.get("decision") or "approved")))
        except dedupe.DedupeError as exc:
            return _refusal(exc)

    @app.post("/api/dedupe/apply")
    def dedupe_apply():
        who = _who()
        if not who:
            return jsonify({"error": "Authentication required"}), 401
        body = _body()
        aid = body.get("approval_id") if body else None
        if not isinstance(aid, int) or isinstance(aid, bool):
            return jsonify({"error": "Expected an approval_id."}), 400
        try:
            return jsonify(dedupe.apply(gateway, aid, by=who))
        except dedupe.DedupeError as exc:
            return _refusal(exc)

    @app.post("/api/dedupe/reinstate")
    def dedupe_reinstate():
        who = _who()
        if not who:
            return jsonify({"error": "Authentication required"}), 401
        body = _body()
        ids = body.get("log_ids") if body else None
        if (not isinstance(ids, list) or not ids
                or not all(isinstance(i, int) and not isinstance(i, bool)
                           for i in ids)):
            return jsonify({"error": "Expected log_ids, a list of row ids, "
                                     "and a reason."}), 400
        try:
            return jsonify(dedupe.reinstate(
                gateway, ids, by=who, reason=str(body.get("reason") or "")))
        except dedupe.DedupeError as exc:
            return _refusal(exc)
