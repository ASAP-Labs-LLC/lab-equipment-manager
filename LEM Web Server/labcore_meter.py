#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
labcore_meter.py — what this server asks LabCore, counted over five minutes.

Baseline item 2 (`baseline/baseline-numbers.md`) was "LEM reads exceeding
LabCore's 8 s watchdog: **not measurable from here — no count obtained**".
LabCore counts its own watchdog kills, but only on its local admin port, and
LEM's `/healthz` had no counter at all. So nobody could say whether LEM's reads
were being killed — and one killed read was worth +426 reads under the old
snapshot fallback (W1). Transfer spec §12.3 closes it: `/healthz` reports this
server's LabCore traffic over the last five minutes, by outcome.

`LabCoreMeter` wraps the LabCore gateway `create_app` was given and forwards
every call unchanged. It only WATCHES the answers:

* **read** — `read_sql`, `get_samples`, `get_test_names`, and `write("read_sql")`;
* **write** — `sql` and every other `write` operation;
* **failed** — any call whose answer is LabCore's `{"error": ...}` shape, or that
  raised. A failed read is never counted as an empty one: `None` from a GET
  helper is "could not ask", and is counted as failed too;
* **timeout** — a failure whose text or exception says it timed out (the HTTP
  client's read timeout, or a socket timeout);
* **watchdog** — LabCore's own answer when its 8 s cap interrupted the read:
  "Read cancelled after 8s to protect the write queue …"
  (`LabCore_main.py` `_wop_read_sql`, the text `baseline/harness/run_web.py`
  injects). Counted by its words, because that answer is the only place the
  kill is visible from this side.

`is_running` is a status GET, not a queue op, and is not counted — the same
rule `CountingLabCore` and `LocalStoreGateway.is_running` follow.

Memory only, a bounded deque of timestamps; reading the counts makes no call
anywhere, so `/healthz` stays a health check that costs LabCore nothing.
"""

from __future__ import annotations

import collections
import re
import socket
import threading
import time
from typing import Callable, Deque, Dict, Optional, Tuple

#: The window `/healthz` reports over, matching LabCore's own `top_5min`.
WINDOW_SECONDS = 300.0

#: At most this many events are remembered. At LEM's normal rate (one read
#: per 12 s refresh, fewer with the bridge off) five minutes is ~25 events;
#: the cap only matters in a storm, where the newest are the ones that count.
MAX_EVENTS = 50000

#: LabCore's watchdog answer, by its own words (see the module docstring).
_WATCHDOG = re.compile(r"read cancelled after [\d.]+\s*s.*protect the write queue",
                       re.I | re.S)
_TIMEOUT = re.compile(r"timed out|timeout", re.I)

_READ_OPS = frozenset(("read_sql",))


def classify_answer(answer) -> Tuple[bool, bool, bool]:
    """(failed, timeout, watchdog) for one gateway answer."""
    if answer is None:
        return True, False, False
    if isinstance(answer, dict) and answer.get("error"):
        text = str(answer.get("error"))
        watchdog = bool(_WATCHDOG.search(text))
        timeout = bool(_TIMEOUT.search(text)) and not watchdog
        return True, timeout, watchdog
    return False, False, False


class LabCoreMeter:
    """A LabCore gateway that counts what passes through it."""

    def __init__(self, inner, clock: Callable[[], float] = time.monotonic,
                 window: float = WINDOW_SECONDS) -> None:
        self.inner = inner
        self._clock = clock
        self._window = float(window)
        self._lock = threading.Lock()
        # (t, is_read, failed, timeout, watchdog)
        self._events: Deque[tuple] = collections.deque(maxlen=MAX_EVENTS)
        self._totals = {"reads": 0, "writes": 0, "failed": 0, "timeouts": 0,
                        "watchdog": 0}

    # ── counting ─────────────────────────────────────────────────────
    def _note(self, is_read: bool, failed: bool, timeout: bool,
              watchdog: bool) -> None:
        with self._lock:
            self._events.append((self._clock(), is_read, failed, timeout,
                                 watchdog))
            self._totals["reads" if is_read else "writes"] += 1
            self._totals["failed"] += int(failed)
            self._totals["timeouts"] += int(timeout)
            self._totals["watchdog"] += int(watchdog)

    def _call(self, is_read: bool, fn, *a, **kw):
        try:
            answer = fn(*a, **kw)
        except BaseException as exc:
            timeout = isinstance(exc, (socket.timeout, TimeoutError)) or \
                bool(_TIMEOUT.search(str(exc)))
            self._note(is_read, True, timeout, False)
            raise
        failed, timeout, watchdog = classify_answer(answer)
        self._note(is_read, failed, timeout, watchdog)
        return answer

    def counts(self, window: Optional[float] = None) -> Dict[str, int]:
        """The last `window` seconds (default five minutes), by outcome."""
        span = self._window if window is None else float(window)
        with self._lock:
            cutoff = self._clock() - span
            out = {"reads_5min": 0, "writes_5min": 0, "failed_5min": 0,
                   "timeouts_5min": 0, "watchdog_5min": 0}
            for t, is_read, failed, timeout, watchdog in self._events:
                if t < cutoff:
                    continue
                out["reads_5min" if is_read else "writes_5min"] += 1
                out["failed_5min"] += int(failed)
                out["timeouts_5min"] += int(timeout)
                out["watchdog_5min"] += int(watchdog)
            out["since_boot"] = dict(self._totals)
        return out

    # ── LabCore's surface, forwarded ─────────────────────────────────
    def is_running(self):
        return self.inner.is_running()

    def read_sql(self, *a, **kw):
        return self._call(True, self.inner.read_sql, *a, **kw)

    def sql(self, *a, **kw):
        return self._call(False, self.inner.sql, *a, **kw)

    def write(self, operation, *a, **kw):
        return self._call(str(operation) in _READ_OPS, self.inner.write,
                          operation, *a, **kw)

    def get_samples(self, *a, **kw):
        return self._call(True, self.inner.get_samples, *a, **kw)

    def get_test_names(self, *a, **kw):
        return self._call(True, self.inner.get_test_names, *a, **kw)

    def __getattr__(self, name):
        # Anything else (base_url, a fake's helper, `transaction` on the
        # single-gateway test shape) is the wrapped gateway's, uncounted.
        return getattr(self.inner, name)

    def __repr__(self) -> str:
        return "<LabCoreMeter around {0!r}>".format(self.inner)
