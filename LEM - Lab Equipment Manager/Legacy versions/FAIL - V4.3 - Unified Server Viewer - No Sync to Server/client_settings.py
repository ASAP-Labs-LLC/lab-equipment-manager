#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
client_settings.py - Persisted client-only settings (server URL, auth token).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Tuple


DEFAULT_SERVER_URL = "http://127.0.0.1:8787"
BASE_DIR = os.path.dirname(__file__)
SETTINGS_FILE = os.path.join(BASE_DIR, "client_settings.json")


@dataclass
class ClientSettings:
    server_url: str = DEFAULT_SERVER_URL
    auth_token: str = ""


_SETTINGS: ClientSettings | None = None


def normalize_server_url(url: str) -> str:
    """Ensure the URL has a scheme and consistent formatting."""
    value = (url or "").strip()
    if not value:
        return DEFAULT_SERVER_URL
    if not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    return value.rstrip("/")


def _ensure_loaded() -> None:
    global _SETTINGS
    if _SETTINGS is not None:
        return
    _SETTINGS = _load_from_disk()


def _load_from_disk() -> ClientSettings:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        server_url = normalize_server_url(raw.get("server_url", DEFAULT_SERVER_URL))
        auth_token = str(raw.get("auth_token", "")).strip()
        return ClientSettings(server_url=server_url, auth_token=auth_token)
    except Exception:
        return ClientSettings()


def get_client_settings() -> ClientSettings:
    _ensure_loaded()
    assert _SETTINGS is not None
    return _SETTINGS


def load_client_settings() -> ClientSettings:
    global _SETTINGS
    _SETTINGS = _load_from_disk()
    return _SETTINGS


def save_client_settings(settings: ClientSettings) -> Tuple[bool, str]:
    """Persist the provided settings to disk."""
    payload = {
        "server_url": normalize_server_url(settings.server_url),
        "auth_token": settings.auth_token.strip(),
    }
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    global _SETTINGS
    _SETTINGS = ClientSettings(**payload)
    return True, "OK"
