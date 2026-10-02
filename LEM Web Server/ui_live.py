"""ui_live.py: what ``GET /api/ui/live`` says, from memory only (ia-final §5).

A port of GC hub's ``live.py`` for LEM. Every open page polls this every 3 s
(30 s when the tab is hidden), so it is held to the rule the snapshot exists
for: **0 LabCore ops per request.** Its sources are the in-memory snapshot,
the live road (``LivePresence``), the checklist page cache, the job registry
(``jobs.py``), the log copy's in-memory status and the audit spool. Nothing
here imports a gateway; the route hands in values it already holds.

It is a GET, so ``_is_background`` leaves ``/healthz`` ``idle_seconds`` alone
(A.7). It is **not** ``POST /api/live``, the bench contract, which is
untouched.

The rule this codebase is built around holds for every count in the
payload: **a failed read is never an empty result.** No snapshot yet means
``needs_you``, ``fleet``, ``qc_out`` are ``None`` and the page draws nothing,
never 0. A cold checklist cache means ``round`` is ``None``, not "0 of 0".

Pieces, all pure or memory-only and node/pytest tested:

* ``readiness(machine, override)`` - the one readiness rule (§3.1 table),
  so ``needs_you`` here and the Needs-you card (piece 4) cannot disagree.
* ``round_summary(day, now)`` - the round due next, from one day's cached
  ``/api/checklists`` answer. The nav-meta and the round page's pill both read
  this one field (§0.2).
* ``Feed`` - boot id + seq cursor; which machines changed since a cursor.
* ``Notices`` - the bell's items, derived from the same memory, with the
  "became Not OK / recovered" transitions remembered for 30 min.
* ``payload(...)`` - the answer.
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import secrets
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

RING_SIZE = 500
CURSOR_MAX = 128
QUIET_SECONDS = 10 * 60          # a bench quiet this long in lab hours is a bell item
RECOVERED_SECONDS = 30 * 60      # "back to OK" items stay this long
OFFLINE_STATUSES = ("SERVICE", "DEAD-LINE")

# readiness states (ia-final §3.1), worst first for sorting
NOT_OK = "not_ok"
OFF_LINE = "off_line"
OK_BUT = "ok_but"
CANT_TELL = "cant_tell"
NO_QC = "no_qc"
OK = "ok"
WORDS = {NOT_OK: "Not OK to run", OFF_LINE: "Off line", OK_BUT: "OK to run, but…",
         CANT_TELL: "Can't tell", NO_QC: "No QC assigned", OK: "OK to run"}
# The states that put an instrument on the Needs-you card. Off line is a
# decision somebody already made (with a comment); No QC assigned is a filter
# of its own (§3.2 chips), not a problem that happened.
NEEDS_YOU = (NOT_OK, OK_BUT, CANT_TELL)


# ── readiness ───────────────────────────────────────────────────────────────

_BENCH_WORDS = {"stopped": "bench stopped", "closed": "lab closed"}


def _bench_word(machine: dict) -> str:
    """Why a bench that is not checking in vouches for nothing, in the words
    a QC row says it ("bench stopped"), lower case to follow a verdict."""
    st = machine.get("module_state") or "unknown"
    if st in _BENCH_WORDS:
        return _BENCH_WORDS[st]
    return "bench stopped" if machine.get("last_poll") else "bench never checked in"


def check_verdict(spec: Optional[dict], machine: dict) -> dict:
    """ONE check's verdict (§4.1), by the rule ``readiness`` judges the whole
    instrument with, so a QC row, the QC tile and the card cannot disagree.

    `spec` is the check's effective spec, or None for an assignment the bench
    has not published a band for (Eravap's Pentane / RVP). In order, the
    same order ``readiness`` takes:

    * a failed result is **Out of spec**, whatever the bench is doing: a
      stop stands until somebody reruns the standard;
    * an assigned check with no verdict in the window is **QC due**: that is
      what makes the card say "OK to run, but… QC due on X". A check whose
      band is not published yet counts once its bench is checking in, since
      then a run could judge it and nobody has;
    * a pass is **In spec** only while its bench is checking in. A stopped
      bench vouches for nothing new, so its card says Can't tell and the
      check says **No verdict yet · bench stopped** (round 2's critic:
      Viscocity's 3 Sep pass read In spec under a Can't tell card). The
      pass itself stays on the row, as history.
    """
    running = _checking_in(machine)
    moved = spec is not None and spec.get("last_qc_superseded_by")
    # A superseded result was run against the OLD standard: it judges
    # nothing about this one, so the check is assigned and not yet run.
    ok = None if spec is None or moved else spec.get("last_qc_in_spec")
    if ok is False:
        return {"key": "out", "word": "Out of spec", "glyph": "error", "detail": ""}
    if spec is not None and ok is None or spec is None and running:
        ran = spec is not None and spec.get("last_qc_at") and not moved
        parts = ["no verdict in the window" if ran else
                 "not yet run against %s" % spec.get("sample_id") if moved and spec.get("sample_id")
                 else "never run"]
        if not running:
            parts.append(_bench_word(machine))
        return {"key": "due", "word": "QC due", "glyph": "half", "detail": " · ".join(parts)}
    if not running:
        return {"key": "none", "word": "No verdict yet", "glyph": "never",
                "detail": _bench_word(machine)}
    return {"key": "in", "word": "In spec", "glyph": "final", "detail": ""}


def qc_checks(machine: dict) -> List[dict]:
    """Every check in force: ``{test_name, sample_id, spec, verdict}``. The
    effective specs (a superseded one is the new standard's band, not yet
    run), then each assignment the bench has not published a band for
    (spec None)."""
    out, seen = [], set()
    for s in machine.get("effective_specs") or []:
        name = str(s.get("test_name") or "")
        seen.add(name)
        out.append({"test_name": name, "sample_id": str(s.get("sample_id") or ""),
                    "last_qc_at": None if s.get("last_qc_superseded_by") else s.get("last_qc_at"),
                    "spec": s,
                    "verdict": check_verdict(s, machine)})
    for t in machine.get("qc_targets") or []:
        name = str(t.get("test") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({"test_name": name, "sample_id": str(t.get("sample") or ""),
                    "last_qc_at": None, "spec": None, "verdict": check_verdict(None, machine)})
    return out


def _out_of_spec(machine: dict) -> list:
    return [c for c in qc_checks(machine) if c["verdict"]["key"] == "out"]


def qc_due(machine: dict) -> list:
    """Assigned checks with no verdict inside the window (``check_verdict``)."""
    return [c for c in qc_checks(machine) if c["verdict"]["key"] == "due"]


_qc_due = qc_due


def _overdue(machine: dict, kind: str) -> list:
    """Scheduled tasks of `kind` that are overdue (from lem_maintenance, the
    schedule, never from the bench's sub_statuses: P10)."""
    return [t for t in machine.get("maintenance") or []
            if str(t.get("kind") or "").lower() == kind and t.get("status") == "RED"]


def _checking_in(machine: dict) -> bool:
    return bool(machine.get("live") or machine.get("module_running"))


def readiness(machine: dict, override: Optional[str] = None) -> dict:
    """``{state, reason}`` for one merged machine (ia-final §3.1).

    `override` is the instrument's manual override from ``lem_machine_control``
    ("" = none, None = not read). A bench reporting SERVICE counts as off line
    either way: SERVICE only ever comes from an override the bench holds,
    possibly one set before the table was last read. DEAD-LINE does not: a
    bench also says DEAD-LINE when no data arrives, which is not a decision
    anybody made.
    """
    ov = (override or "").strip().upper()
    if ov:
        return {"state": OFF_LINE, "reason": "Taken off line (%s)" % ov}
    if machine.get("status") == "SERVICE":
        return {"state": OFF_LINE, "reason": machine.get("reason") or "Out for service"}
    bad = _out_of_spec(machine)
    if bad:
        names = ", ".join(sorted({str(s.get("test_name") or "") for s in bad}))
        return {"state": NOT_OK, "reason": "QC out of spec: " + names}
    # Ryan, 2026-10-01: only QC (and an override) can make the answer No.
    # An overdue calibration is a warning, like an overdue PM; the QC check
    # against the certificate band is what says whether it still reads true.
    due = _qc_due(machine)
    if due:
        names = ", ".join(sorted({str(s.get("test_name") or "") for s in due}))
        return {"state": OK_BUT, "reason": "QC due: " + names}
    if _overdue(machine, "calibration"):
        return {"state": OK_BUT, "reason": "Calibration overdue"}
    if _overdue(machine, "pm"):
        return {"state": OK_BUT, "reason": "PM overdue"}
    if not _checking_in(machine):
        st = machine.get("module_state") or "unknown"
        return {"state": CANT_TELL, "reason": "Bench stopped" if st == "stopped"
                else "Lab closed" if st == "closed" else "Bench never checked in"}
    if not (machine.get("effective_specs") or machine.get("qc_targets")):
        return {"state": NO_QC, "reason": "No QC assigned"}
    return {"state": OK, "reason": ""}


# Every problem an instrument has, worst first, by key. The key is what a
# Needs-you tile, its ?cause= filter and a bell line are about. ui_live owns
# it (not ui_instruments) because the bell needs it and ui_instruments
# imports this module.
PROBLEM_WORDS = {"not_ok-qc": "QC out of spec", "ok_but-qc": "QC due",
                 "ok_but-cal": "Calibration overdue", "ok_but-pm": "PM overdue",
                 "cant_tell-stopped": "Bench stopped", "cant_tell-never": "Never checked in",
                 "cant_tell-closed": "Lab closed"}


def problems(machine: dict, override: Optional[str] = None) -> List[str]:
    """Every problem one instrument has, as keys, worst first.

    The first is the one its verdict is about (``readiness``'s reason), so the
    two cannot name different things. The rest are facts behind it that are
    just as true: an overdue PM behind an overdue calibration (round 3's
    critic found OptiMPP 2's said nowhere), a calibration behind a QC stop.
    Grouping each instrument by its one worst cause made a tile, its filter
    and the bell count 5 overdue calibrations where the schedule had 7.

    Off line is a decision about running it, so the QC and bench facts that
    decide "can it run?" are moot; its overdue tasks are not.
    """
    r = readiness(machine, override)
    state = r["state"]
    cal, pm = bool(_overdue(machine, "calibration")), bool(_overdue(machine, "pm"))
    tasks = (["ok_but-cal"] if cal else []) + (["ok_but-pm"] if pm else [])
    if state == OFF_LINE:
        return tasks
    out = (["not_ok-qc"] if _out_of_spec(machine) else []) \
        + (["ok_but-qc"] if _qc_due(machine) else []) + tasks
    if not _checking_in(machine):
        st = machine.get("module_state") or "unknown"
        slug = {"stopped": "stopped", "closed": "closed"}.get(st, "never")
        # "Lab closed" is a problem only when it is all there is to say
        if slug != "closed" or not out:
            out.append("cant_tell-" + slug)
    return out


def overrides_from_tables(tables: Optional[dict]) -> Optional[Dict[str, str]]:
    """uid -> override out of the snapshot's ``control`` arm, or None when the
    snapshot has no tables yet (unknown, not "no overrides")."""
    if not tables or "control" not in tables:
        return None
    out = {}
    for r in tables.get("control") or []:
        uid = str(r.get("c1") if isinstance(r, dict) else "")
        if uid:
            out[uid] = str(r.get("c2") or "")
    return out


# ── the round due next ──────────────────────────────────────────────────────

SLOT_ORDER = ("opening", "closing")


def round_summary(day: Optional[dict], now: datetime) -> Optional[dict]:
    """The round due next, out of one day's cached ``/api/checklists`` answer.

    None when the day is not in memory (the read has not happened or failed):
    unknown, never "0 of 0". A day with no opening or closing round says so
    with ``slot: None``. Several checklists in one slot add up: the round is
    the slot, not one list.

    -> {slot, done, total, due, overdue, complete}
    """
    if not isinstance(day, dict) or not isinstance(day.get("checklists"), list):
        return None
    slots: Dict[str, dict] = {}
    for cl in day["checklists"]:
        slot = str(cl.get("slot") or "")
        if slot not in SLOT_ORDER:
            continue
        s = slots.setdefault(slot, {"done": 0, "total": 0, "due": ""})
        s["done"] += int(cl.get("checked") or 0)
        s["total"] += int(cl.get("total") or 0)
        due = str(cl.get("due_time") or "")
        if due and (not s["due"] or due < s["due"]):
            s["due"] = due
    present = [s for s in SLOT_ORDER if s in slots and slots[s]["total"] > 0]
    if not present:
        return {"slot": None, "done": 0, "total": 0, "due": None,
                "overdue": False, "complete": False}
    hm = now.strftime("%H:%M")
    pick = next((s for s in present if slots[s]["done"] < slots[s]["total"]), None)
    complete = pick is None
    pick = pick or present[-1]
    s = slots[pick]
    overdue = (not complete and bool(s["due"]) and hm > s["due"])
    return {"slot": pick, "done": s["done"], "total": s["total"],
            "due": s["due"] or None, "overdue": overdue, "complete": complete}


# ── the cursor ──────────────────────────────────────────────────────────────

def machine_signature(m: dict, ready: dict) -> str:
    """What about one instrument a page would redraw for. Ages are left out:
    they tick on their own and are not a change."""
    specs = [(s.get("test_name"), s.get("last_qc_in_spec"), s.get("last_qc_at"),
              s.get("last_qc_value")) for s in m.get("effective_specs") or []]
    maint = [(t.get("uid"), t.get("status")) for t in m.get("maintenance") or []]
    key = [m.get("title"), m.get("status"), m.get("reason"), m.get("module_state"),
           bool(m.get("live")), m.get("level_uid"), m.get("pos"), specs, maint,
           ready.get("state"), ready.get("reason")]
    return hashlib.sha1(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()


class Feed:
    """Boot id + seq, and which machines changed after a cursor.

    GC's bus has publishers; LEM's changes arrive in memory without anybody
    announcing them (a snapshot build, a bench push), so the feed OBSERVES:
    each poll hands in every machine's signature and the other parts' keys,
    and a difference is an event. The first observation is the baseline.
    """

    def __init__(self, size: int = RING_SIZE, boot_id: Optional[str] = None) -> None:
        self.boot_id = boot_id or secrets.token_hex(8)
        self._ring: collections.deque = collections.deque(maxlen=size)  # (seq, uids, kinds)
        self._seq = 0
        self._last: Optional[Dict[str, str]] = None
        self._parts: Dict[str, str] = {}
        self._lock = threading.Lock()

    def observe(self, machines: Dict[str, str], parts: Dict[str, str]) -> None:
        with self._lock:
            if self._last is None:
                self._last, self._parts = dict(machines), dict(parts)
                return
            uids = {u for u in set(machines) | set(self._last)
                    if machines.get(u) != self._last.get(u)}
            kinds = {k for k in set(parts) | set(self._parts)
                     if parts.get(k) != self._parts.get(k)}
            if uids:
                kinds.add("machines")
            self._last, self._parts = dict(machines), dict(parts)
            if uids or kinds:
                self._seq += 1
                self._ring.append((self._seq, tuple(sorted(uids)), tuple(sorted(kinds))))

    def cursor(self) -> str:
        with self._lock:
            return "%s:%d" % (self.boot_id, self._seq)

    def _parse(self, cursor: Any) -> Optional[int]:
        if not isinstance(cursor, str) or not cursor or len(cursor) > CURSOR_MAX:
            return None
        boot, sep, raw = cursor.partition(":")
        if not sep or boot != self.boot_id or not (raw.isascii() and raw.isdigit()):
            return None
        return int(raw)

    def since(self, cursor: Any) -> dict:
        """``{cursor, reset, machines, kinds}`` after ``cursor`` (GC's rules:
        missing, garbled, another boot, the future, or older than the ring is
        a reset)."""
        seq = self._parse(cursor)
        with self._lock:
            now = self._seq
            events = list(self._ring)
        out = {"cursor": "%s:%d" % (self.boot_id, now), "reset": False,
               "machines": [], "kinds": []}
        if seq is None or seq > now:
            out["reset"] = True
            return out
        if seq == now:
            return out
        oldest = events[0][0] if events else now + 1
        if seq < oldest - 1:
            out["reset"] = True
            return out
        uids, kinds = set(), set()
        for ev_seq, u, k in events:
            if ev_seq > seq:
                uids.update(u)
                kinds.update(k)
        out["machines"] = sorted(uids)
        out["kinds"] = sorted(kinds)
        return out


# ── the bell ────────────────────────────────────────────────────────────────

def _hm(iso: str) -> str:
    try:
        at = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return ""
    if at.tzinfo is not None:
        at = at.astimezone().replace(tzinfo=None)
    return at.strftime("%H:%M")


def _names(titles: List[str], limit: int = 3) -> str:
    """"A", "A and B", "A, B and C", "A, B and 2 more"."""
    t = [x for x in titles if x]
    if len(t) <= 1:
        return "".join(t)
    if len(t) <= limit:
        return ", ".join(t[:-1]) + " and " + t[-1]
    return ", ".join(t[:limit - 1]) + " and %d more" % (len(t) - limit + 1)


def _lower_first(s: str) -> str:
    """"Calibration overdue" -> "calibration overdue", but "QC out of spec"
    stays: an acronym keeps its capitals."""
    if len(s) > 1 and s[1].isupper():
        return s
    return s[:1].lower() + s[1:]


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def conditions(*, machines: Optional[List[dict]], ready: Dict[str, dict],
               overrides: Optional[Dict[str, str]], round_: Optional[dict],
               audit_spool: Optional[int], live_road: Optional[dict],
               certificates: Optional[List[dict]], href: Callable[[str, str], str],
               now: datetime) -> List[dict]:
    """The bell's current conditions: ``{key, level, message, href, link}``.

    `key` names the condition, not the moment; `Notices` stamps when each
    began. Same-cause instruments merge into one item (§3.2), so a lab with
    four benches due for QC gets one line, not four.
    """
    out: List[dict] = []
    ms = machines or []
    title = {m.get("machine_uid"): (m.get("title") or m.get("machine_uid")) for m in ms}

    # Not OK to run: one item per instrument, except that instruments with
    # the SAME reason merge into one line (§3.2), so a lab where five
    # calibrations lapsed together reads one sentence, not five. The merged
    # key names its members: a sixth joining it is a new item, which shows
    # again on a computer that dismissed the five.
    groups: Dict[str, List[str]] = {}
    for m in ms:
        uid = m.get("machine_uid")
        r = ready.get(uid) or {}
        if r.get("state") == NOT_OK:
            groups.setdefault(str(r.get("reason") or ""), []).append(uid)
    for reason, uids in groups.items():
        if len(uids) == 1:
            uid = uids[0]
            out.append({"key": "notok:" + uid, "level": "error", "about": "instruments",
                        "message": "%s is not OK to run: %s." % (title[uid], reason),
                        "href": href(uid, "qc"), "link": "Open " + title[uid]})
        else:
            digest = hashlib.sha1(("%s|%s" % (reason, ",".join(sorted(uids)))).encode()).hexdigest()[:10]
            out.append({"key": "notok:group:" + digest, "level": "error", "about": "instruments",
                        "message": "%s are not OK to run: %s." % (
                            _names([title[u] for u in uids]), _lower_first(reason)),
                        "href": "/?cause=not_ok-qc", "link": "Show them"})
    for m in ms:
        uid = m.get("machine_uid")
        if overrides is not None and overrides.get(uid):
            out.append({"key": "override:%s:%s" % (uid, overrides[uid].upper()), "level": "warning",
                        "about": "instruments",
                        "message": "%s is off line (%s)." % (title[uid], overrides[uid].upper()),
                        "href": href(uid, ""), "link": "Open " + title[uid]})

    # A line that links to the list is about every instrument with that
    # problem: the same set its tile and its ?cause= filter are about
    # (§0.2). Not only those for which it is the worst problem: that counted
    # 5 overdue calibrations where the schedule had 7, and no PM at all.
    probs = {m.get("machine_uid"): problems(m, None if overrides is None
                                             else overrides.get(m.get("machine_uid"), ""))
             for m in ms}

    def _with(key: str) -> list:
        # by name, as the tile lists them
        return sorted((m for m in ms if key in probs.get(m.get("machine_uid"), ())),
                      key=lambda m: (str(title[m["machine_uid"]]).lower(), m["machine_uid"]))

    for key, prefix, words, section in (
            ("ok_but-qc", "qcdue", "due for QC", "qc"),
            ("ok_but-cal", "caldue", "overdue for calibration", "maintenance"),
            ("ok_but-pm", "pmdue", "overdue for PM", "maintenance")):
        hit = _with(key)
        if not hit:
            continue
        n = len(hit)
        out.append({"key": prefix + ":" + ",".join(sorted(m["machine_uid"] for m in hit)),
                    "level": "warning", "about": "instruments",
                    "message": "%d %s %s: %s." % (
                        n, _plural(n, "instrument is", "instruments are"), words,
                        _names([title[m["machine_uid"]] for m in hit])),
                    "href": href(hit[0]["machine_uid"], section) if n == 1 else "/?cause=" + key,
                    "link": "Open " + title[hit[0]["machine_uid"]] if n == 1 else "Show them"})

    quiet = []
    for m in ms:
        if m.get("live") or m.get("module_state") != "stopped":
            continue
        last = m.get("last_poll") or m.get("last_activity")
        try:
            at = datetime.fromisoformat(str(last))
            if at.tzinfo is not None:
                at = at.astimezone().replace(tzinfo=None)
        except (TypeError, ValueError):
            continue
        if (now - at).total_seconds() > QUIET_SECONDS:
            quiet.append((title[m["machine_uid"]], _hm(str(last)), m["machine_uid"]))
    if quiet:
        n = len(quiet)
        out.append({"key": "quiet", "level": "warning", "about": "instruments",
                    "message": "%d %s quiet for more than 10 min in lab hours: %s." % (
                        n, _plural(n, "bench has been", "benches have been"),
                        _names(["%s (since %s)" % (t, hm) if hm else t for t, hm, _ in quiet])),
                    "href": href(quiet[0][2], "bench") if n == 1 else "/?filter=quiet",
                    "link": "Open " + quiet[0][0] if n == 1 else "Show them"})

    if round_ and round_.get("overdue"):
        out.append({"key": "round:%s:%s" % (round_["slot"], now.date().isoformat()),
                    "level": "warning",
                    "message": "The %s round is overdue: %d of %d done, was due %s." % (
                        round_["slot"], round_["done"], round_["total"], round_["due"]),
                    "href": "/checklists", "link": "Open the round"})

    if audit_spool:
        out.append({"key": "audit", "level": "warning",
                    "message": "%d correction-factor audit %s waiting for LabCore to accept %s." % (
                        audit_spool, _plural(audit_spool, "row is", "rows are"),
                        _plural(audit_spool, "it", "them")),
                    "href": "/settings#diagnostics", "link": "Open Diagnostics"})

    for c in certificates or []:
        out.append({"key": "cert:%s:%s" % (c.get("standard"), c.get("expires")), "level": "warning",
                    "message": "The certificate for %s expires on %s." % (
                        c.get("standard"), c.get("expires")),
                    "href": "/quality/standards", "link": "Open the standards"})

    if live_road and live_road.get("checking_in"):
        n, live = live_road["checking_in"], live_road.get("live", 0)
        if live == 0:
            out.append({"key": "liveroad", "level": "info",
                        "message": "Benches can't reach LEM directly. 0 of %d use the live road; "
                                   "they read their settings from LabCore instead." % n,
                        "href": "/settings#diagnostics", "link": "Open Diagnostics"})
    return out


class Notices:
    """The bell's item set: current conditions plus recent recoveries.

    Each item's ``id`` is its condition key plus when it began, so a
    condition that clears and comes back is a new item (a browser that
    dismissed the first one sees the second). Recoveries of "not OK" and
    "off line" are kept for ``RECOVERED_SECONDS``.
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._since: Dict[str, float] = {}
        self._watched: Dict[str, set] = {}
        self._href: Dict[str, str] = {}
        self._recovered: List[dict] = []
        self._lock = threading.Lock()

    def update(self, items: List[dict], titles: Dict[str, str],
               watched: Optional[Dict[str, set]] = None) -> List[dict]:
        """Stamp `items` with when each began; add recoveries.

        `watched` is {"notok": uids, "offline": uids} right now. An instrument
        that WAS in one and is not any more, and still exists, gets a
        "back" item ("GC-1 is OK to run again."), whatever item it was
        merged into while it was bad.
        """
        now = self._clock()
        with self._lock:
            # None: nothing was read this time, so nothing can have recovered
            for kind, words in (() if watched is None else (("notok", "%s is OK to run again."),
                                                            ("offline", "%s is back on line."))):
                was = self._watched.get(kind, set())
                for uid in sorted(was - set(watched.get(kind) or ())):
                    if uid in titles:
                        self._recovered.append({
                            "id": "%s:%s:recovered:%d" % (kind, uid, int(now)), "level": "success",
                            "message": words % titles[uid], "href": self._href.get(uid) or "/",
                            "link": "Open " + titles[uid], "ts": _iso(now),
                            "about": "instruments"})
            if watched is not None:
                self._watched = {k: set(v) for k, v in watched.items()}
            keys = {i["key"] for i in items}
            for gone in [k for k in self._since if k not in keys]:
                self._since.pop(gone, None)
            self._recovered = [r for r in self._recovered
                               if now - _epoch(r["ts"]) < RECOVERED_SECONDS][-50:]
            out = []
            for i in items:
                since = self._since.setdefault(i["key"], now)
                out.append({"id": "%s:%d" % (i["key"], int(since)), "level": i["level"],
                            "message": i["message"], "href": i.get("href"), "link": i.get("link"),
                            "ts": _iso(since), "about": i.get("about")})
            out.extend(self._recovered)
            out.sort(key=lambda n: n["ts"], reverse=True)
            return out

    def remember_links(self, hrefs: Dict[str, str]) -> None:
        with self._lock:
            self._href.update(hrefs)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


def _epoch(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return 0.0


# ── the lab's clock ─────────────────────────────────────────────────────────

def lab_tz() -> Optional[str]:
    """The lab's IANA zone name for ``Intl.DateTimeFormat``, or None.

    ``LEM_LAB_TZ`` wins; otherwise this server's own zone where the OS says
    it (``/etc/localtime`` on macOS and Linux). None means the client uses its
    own clock and says so; it never guesses a zone.
    """
    env = (os.environ.get("LEM_LAB_TZ") or "").strip()
    if env:
        return env
    try:
        target = os.path.realpath("/etc/localtime")
        marker = "zoneinfo/"
        if marker in target:
            return target.split(marker, 1)[1]
    except OSError:
        pass
    try:                                         # Windows, when tzlocal is installed
        import tzlocal                           # type: ignore
        name = str(tzlocal.get_localzone_name() or "")
        return name or None
    except Exception:                            # noqa: BLE001
        return None


# ── the answer ──────────────────────────────────────────────────────────────

def nav_meta(p: dict) -> dict:
    """The nav's words from one payload: the SAME function the first paint
    (ui_shell) and the browser (status.js ``navMeta``) apply, so the nav and a
    page's count read one field and cannot disagree (§0.2).

    -> {key: {text, badge}} with only the items that have something to say.
    """
    out = {}
    n = p.get("needs_you")
    if isinstance(n, int) and n > 0:
        out["instruments"] = {"text": "%d %s" % (n, "needs you" if n == 1 else "need you"),
                              "badge": str(n)}
    r = p.get("round")
    if isinstance(r, dict) and r.get("slot") and r.get("total"):
        if r.get("complete"):
            out["checklists"] = {"text": "Done", "badge": "✓"}
        else:
            frac = "%d/%d" % (r["done"], r["total"])
            out["checklists"] = {"text": "%s %s" % (r["slot"].capitalize(), frac), "badge": frac}
    q = p.get("qc_out")
    if isinstance(q, int) and q > 0:
        # its unit, said: "3 checks", beside Instruments' "2 not OK to run"
        out["qc"] = {"text": "%d %s out of spec" % (q, "check" if q == 1 else "checks"),
                     "badge": str(q)}
    return out


def payload(*, feed: Feed, cursor: Any, snap: dict, merged: Optional[List[dict]],
            overrides: Optional[Dict[str, str]], day: Optional[dict],
            notices: Notices, audit_spool: Optional[int],
            certificates: Optional[List[dict]], mirror: Optional[dict],
            jobs: List[dict], version: str, href: Callable[[str, str], str],
            now: Optional[datetime] = None, tz: Optional[str] = None,
            custody: Optional[List[dict]] = None) -> dict:
    """``GET /api/ui/live``'s answer. Pure over what it is handed."""
    now = now or datetime.now()
    ready_snap = bool(snap.get("ready"))
    machines = merged if (ready_snap and merged is not None) else None
    ready = {m["machine_uid"]: readiness(m, None if overrides is None
                                         else overrides.get(m["machine_uid"], ""))
             for m in machines or []}
    fleet = None
    if machines:
        fleet = {"checking_in": sum(1 for m in machines if _checking_in(m)),
                 "total": len(machines),
                 "live_road": sum(1 for m in machines if m.get("live"))}
    needs_you = (sum(1 for r in ready.values() if r["state"] in NEEDS_YOU)
                 if machines is not None else None)
    qc_out = (sum(len(_out_of_spec(m)) for m in machines) if machines is not None else None)
    rnd = round_summary(day, now)
    items = conditions(machines=machines, ready=ready, overrides=overrides, round_=rnd,
                       audit_spool=audit_spool,
                       live_road=({"checking_in": fleet["checking_in"], "live": fleet["live_road"]}
                                  if fleet else None),
                       certificates=certificates, href=href, now=now)
    # Backup and custody (transfer §11): already {key, level, message, href,
    # link}, most urgent first, from the custody service's memory.
    items = list(items) + [dict(i) for i in custody or []]
    titles = {m["machine_uid"]: m.get("title") or m["machine_uid"] for m in machines or []}
    notices.remember_links({u: href(u, "") for u in titles})
    watched = None
    if machines is not None:
        watched = {"notok": {u for u, r in ready.items() if r["state"] == NOT_OK},
                   "offline": {u for u, r in ready.items() if r["state"] == OFF_LINE}}
    notes = notices.update(items, titles, watched)
    age = snap.get("age_seconds")
    parts = {"round": json.dumps(rnd, sort_keys=True),
             "notifications": json.dumps([n["id"] for n in notes]),
             "jobs": json.dumps([(j.get("id"), j.get("state"), j.get("progress"))
                                 for j in jobs], sort_keys=True, default=str),
             "mirror": json.dumps({k: (mirror or {}).get(k) for k in ("state", "complete_to")},
                                  default=str),
             "snapshot": str(snap.get("built_at") or ""),
             "labcore": str(snap.get("labcore_online"))}
    feed.observe({m["machine_uid"]: machine_signature(m, ready[m["machine_uid"]])
                  for m in machines or []}, parts)
    s = feed.since(cursor)
    out = {
        "cursor": s["cursor"], "reset": s["reset"], "machines": s["machines"],
        "kinds": s["kinds"],
        "needs_you": needs_you, "fleet": fleet, "round": rnd, "qc_out": qc_out,
        "snapshot_age": round(age, 1) if isinstance(age, (int, float)) else None,
        "snapshot_at": (snap.get("built_at") or None) if ready_snap else None,
        "snapshot_stale": bool(snap.get("stale")) if ready_snap else None,
        "labcore_online": snap.get("labcore_online"),
        "mirror": mirror,
        "notifications_unread": len(notes),
        "jobs": jobs, "version": version,
        "server_now": now.astimezone().isoformat(timespec="seconds"),
        "lab_tz": tz,
    }
    # the items ride along only when they changed for this client (or on a reset)
    if s["reset"] or "notifications" in s["kinds"]:
        out["notifications"] = notes
    out["nav_meta"] = nav_meta(out)
    return out

