#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
labcore_auth.py — LEM's login, shared with the rest of the LabLink suite.

Every LabLink app (LabStation, LabEntry, …) authenticates against LabCore's
`/api/login`: post `{username, password}` and get back `{token, username}`,
or 401. LabCore owns the accounts and the sessions, so a user has ONE set of
credentials across the suite — including NFC card login, where a reader types
the card's code into a single field and LabCore matches it in either.

LEM uses exactly that endpoint instead of an app-local admin password.
"""

from __future__ import annotations

from typing import Optional, Tuple

LOGIN_TIMEOUT = 10


class LabCoreAuth:
    """Authenticates LEM users against LabCore.

    Mirrors `UserManager.authenticate` in LabStation/LabEntry so behavior
    (including NFC cards and error wording) is identical across apps.
    """

    def __init__(self, base_url: str = "", gateway=None) -> None:
        self._base_url = (base_url or "").rstrip("/")
        self._gateway = gateway

    @property
    def base_url(self) -> str:
        if self._base_url:
            return self._base_url
        # Fall back to whatever LabCore the gateway is pointed at.
        return str(getattr(self._gateway, "base_url", "") or "").rstrip("/")

    def _post(self, url, json=None, timeout=None, headers=None):
        import requests

        return requests.post(url, json=json, timeout=timeout, headers=headers)

    def login(self, username: str, password: str
              ) -> Tuple[Optional[str], str, str]:
        """Returns (username, token, error). A blank username is allowed:
        an NFC reader may put the card code in either field."""
        base = self.base_url
        if not base:
            return None, "", "LabCore is not connected."
        try:
            resp = self._post(
                f"{base}/api/login",
                json={"username": (username or "").strip(),
                      "password": password or ""},
                timeout=LOGIN_TIMEOUT,
            )
        except Exception as exc:  # network/DNS/TLS — report, never raise
            return None, "", f"Connection error: {exc}"

        if resp.status_code == 200:
            data = resp.json() or {}
            return (data.get("username") or (username or "").strip(),
                    data.get("token", ""), "")
        if resp.status_code == 401:
            return None, "", "Invalid username or password."
        if resp.status_code == 400:
            return None, "", "Username and password are required."
        return None, "", f"LabCore returned status {resp.status_code}."

    def logout(self, token: str) -> None:
        """Destroy the LabCore session so signing out of LEM signs the
        token out suite-wide."""
        base = self.base_url
        if not base or not token:
            return
        try:
            self._post(f"{base}/api/logout", json={}, timeout=LOGIN_TIMEOUT,
                       headers={"Authorization": f"Bearer {token}"})
        except Exception:
            pass  # best-effort; the local session is cleared regardless
