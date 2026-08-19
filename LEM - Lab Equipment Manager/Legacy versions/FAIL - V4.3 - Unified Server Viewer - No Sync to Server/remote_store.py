#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
remote_store.py - HTTP client helpers for talking to the EQM server.

Uses only stdlib (urllib). Environment variables:
    LABMAP_SERVER_URL   -> base URL (default http://127.0.0.1:8787)
    LABMAP_CLIENT_TOKEN -> optional bearer token for auth (falls back to LABMAP_SERVER_TOKEN)
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from client_settings import get_client_settings, load_client_settings, normalize_server_url


load_client_settings()


def _authorize(req: Request) -> None:
    token = _current_auth_token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")


def _url(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return get_base_url() + path


def _current_base_url() -> str:
    env_url = os.environ.get("LABMAP_SERVER_URL", "").strip()
    if env_url:
        return normalize_server_url(env_url)
    return normalize_server_url(get_client_settings().server_url)


def _current_auth_token() -> str:
    env_token = (
        os.environ.get("LABMAP_CLIENT_TOKEN")
        or os.environ.get("LABMAP_SERVER_TOKEN")
        or ""
    ).strip()
    if env_token:
        return env_token
    return get_client_settings().auth_token.strip()


def get_base_url() -> str:
    return _current_base_url()


def get_auth_token() -> str:
    return _current_auth_token()


def http_get_json(path: str) -> Dict[str, Any]:
    req = Request(_url(path))
    _authorize(req)
    try:
        with urlopen(req, timeout=10) as resp:
            data = resp.read()
            return json.loads(data.decode("utf-8"))
    except (URLError, HTTPError, TimeoutError):
        return {}
    except Exception:
        return {}


def http_post_json(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        body = json.dumps(payload).encode("utf-8")
        req = Request(_url(path), data=body, method="POST")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        _authorize(req)
        with urlopen(req, timeout=10) as resp:
            data = resp.read()
            return json.loads(data.decode("utf-8"))
    except (URLError, HTTPError, TimeoutError):
        return {"ok": False}
    except Exception:
        return {"ok": False}


def get_state() -> Dict[str, Any]:
    return http_get_json("/state")


def get_config() -> Dict[str, Any]:
    return http_get_json("/config")


def post_action(name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return http_post_json(f"/action/{name}", payload)


def get_report_preview() -> Dict[str, Any]:
    return http_get_json("/reports/preview")


def generate_report(payload: Dict[str, Any]) -> Dict[str, Any]:
    return http_post_json("/reports/generate", payload)


def get_maintenance_snapshot() -> Dict[str, Any]:
    return http_get_json("/maintenance")


def create_maintenance_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    return http_post_json("/maintenance/tasks", payload)


def start_maintenance_task(task_id: str) -> Dict[str, Any]:
    return http_post_json(f"/maintenance/tasks/{task_id}/start", {})


def complete_maintenance_task(task_id: str, user: str, comment: str) -> Dict[str, Any]:
    return http_post_json(
        f"/maintenance/tasks/{task_id}/complete",
        {"user": user, "comment": comment},
    )


def delete_maintenance_task(task_id: str, user: str, reason: str) -> Dict[str, Any]:
    return http_post_json(f"/maintenance/tasks/{task_id}", {"user": user, "reason": reason})


def add_maintenance_comment(box_uid: str, box_title: str, comment: str, user: str) -> Dict[str, Any]:
    return http_post_json(
        "/maintenance/comments",
        {"box_uid": box_uid, "box_title": box_title, "comment": comment, "user": user},
    )
