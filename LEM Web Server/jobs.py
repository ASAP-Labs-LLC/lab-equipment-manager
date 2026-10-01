"""jobs.py: background work, in memory, for the shell's "Running now" (§5).

A port of the shape of GC hub's ``tasks.REGISTRY``: something that runs for
longer than a click (the log copy's first fill, an import, a CSV export, the
uncertainty compute) registers here, reports progress, and ends with ONE
outcome line. ``GET /api/ui/live`` serves ``REGISTRY.list()`` from memory, so
every open page shows it without asking anything.

Ended jobs stay ``KEEP_SECONDS`` (30 min) and then drop, as in GC. Job ids
restart with the process; the browser keys its dismissals by boot id too.

Never raises into its caller: a progress report is never worth failing the
work it describes.
"""
from __future__ import annotations

import itertools
import threading
import time
from datetime import datetime
from typing import Callable, List, Optional

KEEP_SECONDS = 30 * 60
STATES = ("running", "done", "failed", "stopped", "interrupted")


def _iso(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


class Job:
    def __init__(self, registry: "Registry", jid: str, kind: str, title: str,
                 by: Optional[str], open_url: Optional[str]) -> None:
        self._r = registry
        self.id = jid
        self.kind = kind
        self.title = title
        self.by = by
        self.open_url = open_url
        self.download_url: Optional[str] = None
        self.state = "running"
        self.done: Optional[int] = None
        self.total: Optional[int] = None
        self.text: Optional[str] = None
        self.outcome: Optional[str] = None
        self.started = registry._clock()
        self.ended: Optional[float] = None

    def progress(self, done: Optional[int] = None, total: Optional[int] = None,
                 text: Optional[str] = None) -> None:
        try:
            with self._r._lock:
                if self.state != "running":
                    return
                self.done, self.total = done, total
                if text is not None:
                    self.text = str(text)
        except Exception:                                   # noqa: BLE001
            pass

    def _end(self, state: str, outcome: Optional[str]) -> None:
        try:
            with self._r._lock:
                if self.state != "running":
                    return
                self.state = state
                self.outcome = str(outcome) if outcome else None
                self.ended = self._r._clock()
        except Exception:                                   # noqa: BLE001
            pass

    def finish(self, outcome: Optional[str] = None, download_url: Optional[str] = None) -> None:
        if download_url:
            self.download_url = download_url
        self._end("done", outcome)

    def fail(self, outcome: Optional[str] = None) -> None:
        self._end("failed", outcome)

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "state": self.state,
                "progress": {"done": self.done, "total": self.total, "text": self.text},
                "by": self.by, "started_at": _iso(self.started), "ended_at": _iso(self.ended),
                "open_url": self.open_url, "download_url": self.download_url,
                "outcome": self.outcome}


class Registry:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._jobs: List[Job] = []
        self._ids = itertools.count(1)

    def start(self, kind: str, title: str, by: Optional[str] = None,
              open_url: Optional[str] = None) -> Job:
        with self._lock:
            job = Job(self, "%s:%d" % (kind, next(self._ids)), kind, title, by, open_url)
            self._jobs.append(job)
            return job

    def list(self) -> List[dict]:
        """Running jobs and the ones that ended inside KEEP_SECONDS, most recent first."""
        now = self._clock()
        with self._lock:
            self._jobs = [j for j in self._jobs
                          if j.state == "running" or (j.ended is not None and now - j.ended < KEEP_SECONDS)]
            jobs = list(self._jobs)
            out = [j.to_dict() for j in jobs]
        order = sorted(range(len(jobs)), key=lambda i: -(jobs[i].ended or jobs[i].started))
        return [out[i] for i in order]
