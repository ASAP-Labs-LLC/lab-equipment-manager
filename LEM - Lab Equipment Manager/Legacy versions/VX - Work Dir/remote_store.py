#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
remote_store.py - HTTP client helpers for talking to server.py

Uses only stdlib (urllib). Set LABMAP_SERVER_URL to override base URL.
Default: http://127.0.0.1:8787
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen


BASE_URL = os.environ.get("LABMAP_SERVER_URL", "http://127.0.0.1:8787").rstrip("/")


def _url(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return BASE_URL + path


def http_get_json(path: str) -> Dict[str, Any]:
    try:
        with urlopen(_url(path), timeout=5) as resp:
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
        with urlopen(req, timeout=5) as resp:
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

