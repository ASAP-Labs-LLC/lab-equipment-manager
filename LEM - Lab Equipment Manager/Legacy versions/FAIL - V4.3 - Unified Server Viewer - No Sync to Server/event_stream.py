#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
event_stream.py - SSE client utilities for the Lab Manager viewer.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PyQt5.QtCore import QObject, pyqtSignal

from remote_store import get_auth_token, get_base_url


class ServerEventStream(QObject):
    """Background SSE listener that emits events into the Qt thread."""

    eventReceived = pyqtSignal(dict)
    statusChanged = pyqtSignal(str)

    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None) -> None:
        super().__init__()
        resolved_url = base_url or get_base_url()
        self._base_url = resolved_url.rstrip("/")
        self._token = (token or get_auth_token()).strip()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._resp = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="srv-events", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        resp = self._resp
        try:
            if resp:
                resp.close()
        except Exception:
            pass
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=2.0)
        self._thread = None
        self._resp = None

    # ----- internals -----------------------------------------------------
    def _run(self) -> None:
        url = f"{self._base_url}/events/sse"
        headers = {
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
            "User-Agent": "LabManagerClient/4.3",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        backoff = 1.0
        while not self._stop.is_set():
            req = Request(url, headers=headers)
            try:
                with urlopen(req, timeout=60) as resp:
                    self._resp = resp
                    self.statusChanged.emit("connected")
                    backoff = 1.0
                    buffer = ""
                    for raw in resp:
                        if self._stop.is_set():
                            break
                        try:
                            line = raw.decode("utf-8").rstrip("\r\n")
                        except Exception:
                            continue
                        if not line:
                            if buffer:
                                self._dispatch(buffer)
                                buffer = ""
                            continue
                        if line.startswith(":"):
                            continue  # comment / heartbeat
                        if line.startswith("data:"):
                            chunk = line[5:].strip()
                            buffer += chunk
                    self.statusChanged.emit("disconnected")
                    self._resp = None
            except (URLError, HTTPError) as exc:
                self.statusChanged.emit(f"error: {exc}")
                self._resp = None
            except TimeoutError:
                self.statusChanged.emit("timeout")
                self._resp = None
            except Exception as exc:
                self.statusChanged.emit(f"error: {exc}")
                self._resp = None
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, 10.0)

    def _dispatch(self, payload: str) -> None:
        try:
            data = json.loads(payload)
        except Exception:
            return
        self.eventReceived.emit(data)
