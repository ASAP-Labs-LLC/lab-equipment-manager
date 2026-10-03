"""ui_log.py: the machine log as the record and /logs show it (ia-final §3.1
#6 and #7, §3.6, §7; piece 7). Pure: no gateway, no clock it was not handed.

Three things live here because three surfaces must agree on them:

* **The kind groups.** The record's Log chips (All · Results · QC · Status ·
  Setup), the /logs kind filter, "Open in the full log" and "Export history
  (CSV)" all ask /api/logs with the same group name, so a chip and the page
  it opens can never disagree about which rows "Results" means.
  `result_conflict` is shown as **Not re-sent** (the transfer guard held a
  reading back because an analyst had changed the LabCore cell, D1) and a
  `reread` row as **Re-read**; both are Results.
* **The keyset cursor** "Load older" pages by: (ts, id) of the oldest row on
  screen. A timestamp alone skips or repeats rows that share one (a QC batch
  is one instant). A cursor that does not parse is refused, never ignored:
  ignoring it would serve page one again under "older".
* **The Bench and results counters.** Filed to LabCore today · Held, waiting
  for the sample · Replays not re-sent. Each comes from what the bench itself
  reported (protocol v2 stats, and the result ledger its `filed` records
  fill). A bench that reported nothing reads **"Not reported by this
  bench's module version"**, never 0 (§3.1 #7, §7): 0 would say the guard ran
  and found nothing. A store that did not answer is a third sentence.
"""
from __future__ import annotations

import base64
import math
from typing import Dict, Iterable, List, Optional, Tuple

NOT_REPORTED = "Not reported by this bench's module version"

#: chip key -> (label, kinds). Order is the chips' order. Every kind the
#: record holds is in exactly one group (tests/test_ui_log.py).
GROUPS: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    ("results", "Results", ("run", "held_expired", "result_conflict", "reread")),
    ("qc", "QC", ("qc",)),
    ("status", "Status", ("status_change", "override", "comment")),
    ("setup", "Setup", ("config", "pm", "calibration")),
)

#: Every kind a person can filter on: the v3.9 eight plus what the bench
#: sync writes (bench_api.log_rows_for) and the Re-read the module will.
KINDS: Tuple[str, ...] = tuple(k for _key, _label, ks in GROUPS for k in ks)


def expand_kinds(arg: str) -> List[str]:
    """"results,override" -> the kinds, in a stable order, unknown words
    dropped (a half-typed URL must not 500, and an unknown word is not a kind
    anybody can have written). "qc" is both a group and a kind: same rows."""
    out: List[str] = []
    for word in (arg or "").split(","):
        w = word.strip().lower()
        if not w or w == "all":
            continue
        group = next((ks for key, _l, ks in GROUPS if key == w), None)
        for k in (group or ((w,) if w in KINDS else ())):
            if k not in out:
                out.append(k)
    return out


# ── the cursor ──────────────────────────────────────────────────────────────

class BadCursor(ValueError):
    """`before` was not a cursor this server handed out."""


def cursor_for(ts: str, row_id: int) -> str:
    raw = ("%s|%d" % (ts, int(row_id))).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def parse_cursor(text: str) -> Tuple[str, int]:
    try:
        pad = "=" * (-len(text) % 4)
        raw = base64.urlsafe_b64decode((text + pad).encode("ascii")).decode("utf-8")
        ts, row_id = raw.rsplit("|", 1)
        if not ts or len(ts) > 64:
            raise ValueError(ts)
        return ts, int(row_id)
    except (ValueError, UnicodeError, TypeError) as exc:
        raise BadCursor("`before` is not a cursor this page was given: %r" % text[:40]) from exc


# ── the counters ────────────────────────────────────────────────────────────

def _count(value) -> Optional[int]:
    """A count the bench reported, or None. A bool, a string, NaN or a
    negative is not a count: each would otherwise print as a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if value < 0:
        return None
    return int(value)


def _say(n: Optional[int], one: str, many: str) -> str:
    if n is None:
        return NOT_REPORTED
    if n == 0:
        return "0"
    return "%d %s" % (n, one if n == 1 else many)


def bench_counts(*, entry: Optional[dict], cursor: Optional[dict],
                 filed_today: Optional[int], error: Optional[str] = None) -> Dict[str, dict]:
    """{filed_today, held, replays_not_resent}: each {n, text}.

    `entry` is the bench registry's (memory; None = never synced over v2),
    `cursor` the bench's newest `bench_cursor` row ({mode, stats}), and
    `filed_today` the result-ledger count for today (None = not asked: a
    v3.9 bench files through LabStation and tells LEM nothing). `error`:
    the store did not answer, so every counter says that instead."""
    if error:
        words = "Could not be read: %s" % str(error)[:160]
        return {k: {"n": None, "text": words, "unread": True}
                for k in ("filed_today", "held", "replays_not_resent")}
    stats = (cursor or {}).get("stats")
    stats = stats if isinstance(stats, dict) else {}
    v2 = entry is not None or str((cursor or {}).get("mode") or "") == "v2"
    filed = _count(filed_today) if v2 else None
    held = _count(stats.get("held"))
    # Not in today's v2 stats: rides the transfer rework (§0 deferral (b)).
    replays = _count(stats.get("replays_not_resent"))
    return {
        "filed_today": {"n": filed, "text": _say(filed, "result", "results")},
        "held": {"n": held, "text": _say(held, "reading", "readings")},
        "replays_not_resent": {"n": replays, "text": _say(replays, "row", "rows")},
    }


def group_of(kind: str) -> Optional[str]:
    return next((key for key, _l, ks in GROUPS if kind in ks), None)


def chips() -> List[dict]:
    """The record's chips, All first."""
    return [{"key": "all", "label": "All"}] + [{"key": k, "label": l} for k, l, _ks in GROUPS]


def known_kinds(found: Iterable[str]) -> List[str]:
    """The kind filter's words: what the log holds, in group order, then
    anything else it holds that no group names (shown, never hidden)."""
    have = [str(k) for k in found if k]
    return [k for k in KINDS if k in have] + sorted(k for k in set(have) if k not in KINDS)
