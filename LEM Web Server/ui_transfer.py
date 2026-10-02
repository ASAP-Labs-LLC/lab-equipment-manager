"""ui_transfer.py: what people see of the data transfer (transfer spec §14).

Four surfaces read this module, and each says one thing:

* **The sidebar foot** (every page, through ``GET /api/ui/live``): one line,
  "Data · 15 of 17 benches reporting · 0 waiting", and the items behind it,
  most urgent first, each linking to the page that resolves it.
* **The instrument's "Data transfer" section** (``/instruments/<uid>#transfer``):
  how this bench's readings reach LEM and LabCore, as hairline rows.
* **``/results/conflicts``**: the results a bench held back because a person
  had changed the LabCore cell (D1), with Keep and Send. The server records
  the decision; the BENCH files it. LEM never writes a result.
* **Settings › Transfer**: the benches, the bridge and why it may or may not
  be turned off, enrolment, retiring, the journal import and dedupe approvals.

Where the facts come from, and what that costs:

* The bench registry (``bench_api.BenchRegistry``): memory. What each v2 bench
  last said about itself: road, unacked, module version, when.
* ``TransferWatch``: a few local reads of LEM's STORE (open conflicts, the
  benches asking to enrol, the stats each bench last sent), cached for
  ``WATCH_TTL_S`` and dropped the moment a decision is written. Never LabCore:
  ``/api/ui/live`` costs LabCore 0 ops, and tests/test_ui_transfer.py counts.

The rule this codebase is built around holds everywhere here: **a failed read
is never an empty result.** A bench that never said how many results it holds
is "Not reported by this bench's module version", never 0; a store that could
not be read is a sentence that says so, never "no conflicts".
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

#: A v2 bench that synced within this long is reporting (bench_api's number).
REPORTING_S = 120.0
#: A bench past this long without reaching LEM is said in the foot, by name.
QUIET_ITEM_S = 5 * 60
#: Past this, the same item is an error, not a warning.
QUIET_ERROR_S = 30 * 60
#: How long TransferWatch trusts what it read from the store.
WATCH_TTL_S = 10.0
#: A clock this far from the server's is "ahead" or "behind", not "in step".
CLOCK_SLACK_S = 60.0
#: §12.1 step 5: every bench must have reported over v2 this long.
BRIDGE_OFF_DAYS = 7

NOT_REPORTED = "Not reported by this bench's module version"
ROAD_WORDS = {"lan": "LAN", "public": "Internet", "folder": "Folder import",
              "other": "Another road"}
CONFLICTS_HREF = "/results/conflicts"
TRANSFER_HREF = "/settings#transfer"


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def ago(seconds: Optional[float]) -> Optional[str]:
    """"4 s", "12 min", "3 h", "2 d"; None when unknown."""
    if seconds is None:
        return None
    s = max(0, int(round(seconds)))
    if s < 60:
        return "%d s" % s
    if s < 3600:
        return "%d min" % (s // 60)
    if s < 86400:
        return "%d h" % (s // 3600)
    return "%d d" % (s // 86400)


def _count(value) -> Optional[int]:
    """A count a bench reported, or None when it did not say one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value < 0:          # NaN, negative: not a count
        return None
    return int(value)


def _parse(value) -> Optional[datetime]:
    try:
        text = str(value or "").strip().replace("Z", "+00:00")
        if not text:
            return None
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()                 # a naive stamp is this server's clock
    return dt


def _hm(value, now: datetime) -> Optional[str]:
    """"09:41" today, "30 Sep 09:41" another day, in this server's zone."""
    at = _parse(value)
    if at is None:
        return None
    local, today = at.astimezone(), now.astimezone()
    if local.date() == today.date():
        return local.strftime("%H:%M")
    return "%d %s %s" % (local.day, local.strftime("%b"), local.strftime("%H:%M"))


def _names(titles: List[str], limit: int = 3) -> str:
    t = [x for x in titles if x]
    if len(t) <= 1:
        return "".join(t)
    if len(t) <= limit:
        return ", ".join(t[:-1]) + " and " + t[-1]
    return ", ".join(t[:limit - 1]) + " and %d more" % (len(t) - limit + 1)


# ── the store's facts, cached ───────────────────────────────────────────────

class TransferWatch:
    """What the store says about the transfer, re-read at most every
    ``ttl`` seconds and dropped by ``invalidate()`` when a decision is
    written, so a person who just pressed Keep sees the count fall at once.

    ``facts()`` -> {error, at, conflicts: {uid: n} | None,
    pending: [uid] | None, stats: {uid: {...last stats, seen, mode, version,
    skew, first_seen}} | None}. On a failed read every part is None and
    ``error`` says why; nothing is ever an empty dict standing in for unknown.
    """

    def __init__(self, store, clock: Callable[[], float] = time.time,
                 ttl: float = WATCH_TTL_S, enabled: bool = True) -> None:
        self.store = store
        #: False when the store IS LabCore (the single-gateway shape the
        #: test suite uses): then a read here would be a LabCore op on every
        #: poll of every open page, so nothing is read and every count is
        #: unknown (None), never 0.
        self.enabled = bool(enabled)
        self.clock = clock
        self.ttl = float(ttl)
        self._lock = threading.Lock()
        self._facts: Optional[dict] = None
        self._at = 0.0

    def invalidate(self) -> None:
        with self._lock:
            self._facts = None

    def facts(self) -> dict:
        if not self.enabled:
            return {"error": None, "at": None, "conflicts": None, "pending": None,
                    "stats": None, "unasked": True}
        now = self.clock()
        with self._lock:
            if self._facts is not None and now - self._at < self.ttl:
                return self._facts
        got = read_facts(self.store)
        with self._lock:
            self._facts, self._at = got, now
        return got


def _rows(store, sql: str, args=None) -> List[dict]:
    res = store.read_sql(sql, args or [])
    if not isinstance(res, dict) or res.get("error") or "rows" not in res:
        raise LookupError((res or {}).get("error") if isinstance(res, dict)
                          else "no answer")
    return res["rows"]


def read_facts(store) -> dict:
    """One pass over the store's transfer tables. Local reads only."""
    try:
        conflicts: Dict[str, int] = {}
        for r in _rows(store, "SELECT machine_uid, COUNT(*) AS n FROM "
                              "result_conflict WHERE resolved_at IS NULL "
                              "GROUP BY machine_uid"):
            conflicts[str(r["machine_uid"])] = int(r["n"] or 0)
        pending = [str(r["machine_uid"]) for r in _rows(
            store, "SELECT machine_uid FROM bench_token WHERE "
                   "pending_reenrol_at IS NOT NULL ORDER BY machine_uid")]
        stats: Dict[str, dict] = {}
        for r in _rows(store,
                       "SELECT c.machine_uid, c.stats, c.last_seen, "
                       "c.first_seen, c.mode, c.module_version, "
                       "c.clock_skew_s, c.road FROM bench_cursor c WHERE "
                       "c.last_seen = (SELECT MAX(last_seen) FROM bench_cursor "
                       "d WHERE d.machine_uid = c.machine_uid)"):
            try:
                st = json.loads(r.get("stats") or "{}")
            except (TypeError, ValueError):
                st = {}
            first = _rows(store, "SELECT MIN(first_seen) AS f FROM bench_cursor "
                                 "WHERE machine_uid = ? AND mode = 'v2'",
                          [r["machine_uid"]])
            stats[str(r["machine_uid"])] = {
                "stats": st if isinstance(st, dict) else {},
                "last_seen": r.get("last_seen"),
                "first_v2": (first[0].get("f") if first else None),
                "mode": r.get("mode"), "module_version": r.get("module_version"),
                "clock_skew_s": r.get("clock_skew_s"), "road": r.get("road")}
    except LookupError as exc:
        return {"error": str(exc) or "the store did not answer", "at": time.time(),
                "conflicts": None, "pending": None, "stats": None}
    return {"error": None, "at": time.time(), "conflicts": conflicts,
            "pending": pending, "stats": stats}


# ── the sidebar foot ────────────────────────────────────────────────────────

def _bench_age(entry: Optional[dict], now: float) -> Optional[float]:
    seen = (entry or {}).get("seen")
    if seen is None:
        return None
    try:
        return max(0.0, now - float(seen))
    except (TypeError, ValueError):
        return None


def _waiting(entry: Optional[dict]) -> Optional[int]:
    """What is still at the bench after its last sync was acked; the count
    it reported before that ack when that is all there is."""
    if not entry:
        return None
    w = _count(entry.get("waiting"))
    return w if w is not None else _count(entry.get("unacked"))


def foot(*, machines: Optional[List[dict]], registry: Dict[str, dict],
         hydrated: bool, facts: dict, import_status: Optional[dict],
         legacy_ok: bool, href: Callable[[str, str], str],
         now: Optional[float] = None) -> Optional[dict]:
    """``{line, items}`` for the sidebar foot and the bell, or None when there
    is nothing to say anything about (no instrument record yet).

    `machines` are the snapshot's merged machines (titles, and whether each
    is checking in); `registry` is uid -> what the bench registry holds for a
    v2 bench; `legacy_ok` is whether a v3.9 bench's readings reach LEM (its
    road is the bridge's pull, or the store itself when there is no bridge).

    line = {text, glyph, href, link}: "Data · 15 of 17 benches reporting ·
    0 waiting". A bench is reporting when its readings are reaching LEM: a v2
    bench synced in the last 2 min; a v3.9 bench is checking in and its road
    works. "waiting" is what v2 benches hold that LEM has not acknowledged;
    a v3.9 bench holds no journal, so it adds nothing to it.
    """
    if machines is None:
        return None
    now = time.time() if now is None else now
    title = {m["machine_uid"]: (m.get("title") or m["machine_uid"]) for m in machines}
    items: List[dict] = []
    if not hydrated:
        line = {"text": "Data · not known yet", "glyph": "never",
                "href": TRANSFER_HREF, "link": "Open Transfer",
                "why": "LEM has not read which benches report over v2 yet"}
        return {"line": line, "items": items}

    total = len(machines)
    reporting = 0
    waiting = 0
    quiet = []
    for m in machines:
        uid = m["machine_uid"]
        entry = registry.get(uid)
        if entry is None:
            if (m.get("live") or m.get("module_running")) and legacy_ok:
                reporting += 1
            continue
        age = _bench_age(entry, now)
        unacked = _waiting(entry) or 0
        waiting += unacked
        if age is not None and age <= REPORTING_S:
            reporting += 1
        elif age is not None and age > QUIET_ITEM_S:
            quiet.append((age, uid, unacked))

    # 1. a bench holding readings LEM has not heard from: by name, worst first
    for age, uid, unacked in sorted(quiet, reverse=True):
        held = ("%d %s waiting at the bench" % (unacked, _plural(unacked, "reading", "readings"))
                if unacked else "nothing waiting at the bench")
        items.append({
            "key": "transfer:quiet:%s" % uid,
            "level": "error" if age > QUIET_ERROR_S else "warning",
            "about": "data",
            "message": "%s: %s; it has not reached LEM for %s." % (title[uid], held, ago(age)),
            "href": href(uid, "transfer"), "link": "Open " + title[uid]})

    # 2. results that need a person (D1); 3. results LabCore refused
    conflicts = facts.get("conflicts")
    stats = facts.get("stats")
    if facts.get("error"):
        items.append({"key": "transfer:unread", "level": "warning", "about": "data",
                      "message": "LEM could not read its transfer records (%s), so "
                                 "results waiting for a decision cannot be counted."
                                 % str(facts["error"])[:120],
                      "href": CONFLICTS_HREF, "link": "Open Results"})
    else:
        n = sum((conflicts or {}).values())
        if n:
            ids = sorted((conflicts or {}).keys())
            items.append({"key": "transfer:conflicts:%d:%s" % (n, ",".join(ids)),
                          "level": "warning", "about": "data",
                          "message": "%d %s a decision: a bench's value differs from "
                                     "one a person entered in LabCore." % (
                                         n, _plural(n, "result needs", "results need")),
                          "href": CONFLICTS_HREF, "link": "Decide"})
        rejected = {uid: _count((s.get("stats") or {}).get("rejected"))
                    for uid, s in (stats or {}).items()}
        hit = sorted(u for u, r in rejected.items() if r)
        if hit:
            r = sum(rejected[u] for u in hit)
            items.append({"key": "transfer:rejected:%d:%s" % (r, ",".join(hit)),
                          "level": "error", "about": "data",
                          "message": "%d %s by LabCore (%s)." % (
                              r, _plural(r, "result rejected", "results rejected"),
                              _names([title.get(u, u) for u in hit])),
                          "href": CONFLICTS_HREF + "#rejected", "link": "See why"})
        # 6. a bench asking to (re-)enrol
        for uid in facts.get("pending") or []:
            items.append({"key": "transfer:enrol:%s" % uid, "level": "info", "about": "data",
                          "message": "%s asks to enrol with LEM." % title.get(uid, uid),
                          "href": TRANSFER_HREF, "link": "Approve it"})

    # 5. the record moving from LabCore (the import, §10.1)
    imp = import_status or {}
    if imp.get("state") == "running":
        done, of = imp.get("tables_verified"), imp.get("tables_total")
        pct = (" · %d %%" % int(100 * done / of)) if (isinstance(done, int)
                                                     and isinstance(of, int) and of) else ""
        items.append({"key": "transfer:import", "level": "info", "about": "data",
                      "message": "Moving the record from LabCore%s." % pct,
                      "href": TRANSFER_HREF, "link": "Open Transfer"})
    elif imp.get("state") == "incomplete":
        items.append({"key": "transfer:import:incomplete", "level": "warning", "about": "data",
                      "message": "Moving the record from LabCore stopped before it was "
                                 "proven complete; the bridge stays off until it is.",
                      "href": TRANSFER_HREF, "link": "Open Transfer"})

    glyph = "final"
    if any(i["level"] == "error" for i in items):
        glyph = "error"
    elif items or waiting or reporting < total:
        glyph = "held"
    # One line at 260 px, so it says the one fact that matters most: what is
    # waiting, else who is not reporting, else that all are. The whole
    # sentence is its accessible name (and title), never only a tooltip:
    # the words on screen are already true on their own.
    full = "Data: %d of %d %s reporting, %d %s waiting at the benches." % (
        reporting, total, _plural(total, "bench", "benches"), waiting,
        _plural(waiting, "reading", "readings"))
    holding = sum(1 for m in machines if _waiting(registry.get(m["machine_uid"])))
    if waiting:
        text = "Data · %d waiting at %d %s" % (waiting, holding, _plural(holding, "bench", "benches"))
    elif reporting < total:
        text = "Data · %d of %d reporting" % (reporting, total)
    else:
        text = "Data · all %d reporting · 0 waiting" % total if total < 100 else \
            "Data · all reporting · 0 waiting"
    line = {"text": text, "full": full,
            "glyph": glyph, "href": TRANSFER_HREF, "link": "Open Transfer",
            "reporting": reporting, "total": total, "waiting": waiting}
    return {"line": line, "items": items}


# ── the instrument's Data transfer section ──────────────────────────────────

def section(*, uid: str, title: str, entry: Optional[dict], facts: dict,
            last_filed: Any, now: Optional[float] = None,
            checking_in: Optional[bool] = None,
            ambiguous: Optional[int] = None,
            recovered: Optional[int] = None) -> dict:
    """The section's rows: [{key, label, value, note, glyph, href, action}].

    `entry` is the bench registry's entry (None: the bench has never synced
    over v2). `last_filed` is the newest `result_ledger.filed_at` for this
    uid ("" when the ledger holds none, None when it could not be read).
    `ambiguous`/`recovered` are counts from the bench's records (None:
    unknown). Every row a v3.9 bench cannot answer says so, never 0.
    """
    now = time.time() if now is None else now
    nowdt = datetime.fromtimestamp(now, tz=timezone.utc)
    err = facts.get("error")
    st = (facts.get("stats") or {}).get(uid) or {}
    stats = st.get("stats") or {}
    rows: List[dict] = []

    def row(key, label, value, note="", glyph="", href=None, action=None):
        rows.append({"key": key, "label": label, "value": value, "note": note,
                     "glyph": glyph, "href": href, "action": action})

    v2 = entry is not None or st.get("mode") == "v2"
    if not v2:
        row("road", "Road", "LabCore, through the older module",
            "This bench runs module 3.9: it writes its readings into LabCore, and "
            "the bridge copies them into LEM. Module 4 sends them to LEM directly.",
            "never")
        for key, label in (("delivered", "Last delivered"), ("waiting", "Waiting at the bench"),
                           ("results", "Results"), ("adoption", "Never recorded"),
                           ("ambiguous", "Ambiguous repeats"), ("module", "Module"),
                           ("clock", "Clock")):
            row(key, label, NOT_REPORTED, "", "never")
        n = (facts.get("conflicts") or {}).get(uid) if not err else None
        if n:
            row("decide", "Needs a decision", "%d %s" % (n, _plural(n, "result", "results")),
                "", "error",
                CONFLICTS_HREF + "?machine=" + uid, "Decide")
        return {"uid": uid, "title": title, "mode": "legacy", "rows": rows,
                "error": err}

    road = (entry or {}).get("road") or st.get("road")
    row("road", "Road", ROAD_WORDS.get(str(road or ""), NOT_REPORTED),
        {"lan": "Straight across the lab's network.",
         "public": "Over the internet, through lem.asaplabs.net.",
         "folder": "Copied by hand from the bench's journal folder."}.get(str(road or ""), ""),
        "final" if road else "never")

    age = _bench_age(entry, now)
    if age is None and st.get("last_seen"):
        seen = _parse(st["last_seen"])
        age = (now - seen.timestamp()) if seen else None
    if age is None:
        row("delivered", "Last delivered", "Never", "", "never")
    else:
        row("delivered", "Last delivered", ago(age) + " ago",
            "" if age <= REPORTING_S else "Nothing is lost: the bench keeps every "
            "reading in its journal until LEM has it.",
            "final" if age <= REPORTING_S else ("error" if age > QUIET_ERROR_S else "held"),
            None, None)
        rows[-1]["age_s"] = round(age, 1)

    unacked = _waiting(entry)
    if unacked is None:
        unacked = _count(stats.get("unacked"))
    if unacked is None:
        row("waiting", "Waiting at the bench", NOT_REPORTED, "", "never")
    else:
        row("waiting", "Waiting at the bench",
            "0" if not unacked else "%d %s" % (unacked, _plural(unacked, "reading", "readings")),
            "" if not unacked else "Saved on the bench PC; sent as soon as LEM answers.",
            "final" if not unacked else "held")

    held, rejected = _count(stats.get("held")), _count(stats.get("rejected"))
    parts = []
    if last_filed is None:
        parts.append("last filed: could not be read")
    elif last_filed:
        parts.append("last filed " + (_hm(last_filed, nowdt) or str(last_filed)))
    else:
        parts.append("none filed yet")
    parts.append("%d waiting for %s sample" % (held, "its" if held == 1 else "their")
                 if held is not None else "waiting for a sample: not reported")
    parts.append("%d rejected" % rejected if rejected is not None
                 else "rejected: not reported")
    row("results", "Results", " · ".join(parts),
        "Filed into LabCore by LabStation, once each. A value a person changed is "
        "never overwritten.",
        "error" if rejected else ("never" if last_filed is None else "final"),
        (CONFLICTS_HREF + "#rejected") if rejected else None,
        "See why" if rejected else None)

    if err:
        row("decide", "Needs a decision", "Could not be read", str(err)[:120], "error")
    else:
        n = (facts.get("conflicts") or {}).get(uid, 0)
        row("decide", "Needs a decision",
            ("%d %s" % (n, _plural(n, "result", "results"))) if n else "0",
            "" if not n else "LabCore holds a value a person entered; the bench "
            "held its own back.", "error" if n else "final",
            (CONFLICTS_HREF + "?machine=" + uid) if n else None,
            "Decide" if n else None)

    row("adoption", "Never recorded",
        NOT_REPORTED if recovered is None else str(recovered),
        "Readings found in the instrument's file that never reached the record "
        "before module 4." if recovered else "",
        "never" if recovered is None else ("held" if recovered else "final"))
    row("ambiguous", "Ambiguous repeats",
        NOT_REPORTED if ambiguous is None else str(ambiguous),
        "Lines the bench could not prove were new or a re-read; kept and labelled, "
        "never dropped." if ambiguous else "",
        "never" if ambiguous is None else ("held" if ambiguous else "final"))

    ver = (entry or {}).get("module_version") or st.get("module_version")
    row("module", "Module", ("v" + str(ver).lstrip("v")) if ver else NOT_REPORTED,
        "", "" if ver else "never")

    skew = st.get("clock_skew_s")
    if not isinstance(skew, (int, float)) or isinstance(skew, bool):
        row("clock", "Clock", NOT_REPORTED, "", "never")
    elif abs(skew) <= CLOCK_SLACK_S:
        row("clock", "Clock", "in step", "", "final")
    else:
        row("clock", "Clock", "%s %s" % (ago(abs(skew)), "ahead" if skew > 0 else "behind"),
            "Times on this bench's readings are its own clock's; set it right in "
            "Windows.", "held")
    return {"uid": uid, "title": title, "mode": "v2", "rows": rows, "error": err}


# ── /results/conflicts ──────────────────────────────────────────────────────

def conflict_view(rows: List[dict], titles: Dict[str, str], prints: Dict[str, str],
                  now: Optional[datetime] = None) -> dict:
    """Open and recently decided conflicts, ready to draw.

    `rows` are `result_conflict` rows; `prints` maps a conflict's
    `bench_seq_ref` to the time of the print its value came from (when known).
    """
    now = now or datetime.now(timezone.utc)
    open_, decided = [], []
    for r in rows:
        uid = str(r.get("machine_uid") or "")
        item = {"ref": r.get("bench_seq_ref"), "machine_uid": uid,
                "title": titles.get(uid) or uid,
                "lab_id": r.get("lab_id") or "", "test_name": r.get("test_name") or "",
                "ours": r.get("ours") or "", "theirs": r.get("theirs") or "",
                "their_operator": r.get("their_operator") or "",
                "their_at": _hm(r.get("their_updated_at"), now) or "",
                "opened_at": _hm(r.get("opened_at"), now) or "",
                "print_at": prints.get(str(r.get("bench_seq_ref"))) or ""}
        if r.get("resolved_at"):
            item.update(choice=r.get("choice"), by=r.get("resolved_by") or "",
                        decided_at=_hm(r.get("resolved_at"), now) or "",
                        delivered=bool(r.get("delivered_at")),
                        delivered_at=_hm(r.get("delivered_at"), now) or "")
            decided.append(item)
        else:
            open_.append(item)
    key = lambda i: (str(i["title"]).lower(), i["lab_id"], i["test_name"])
    open_.sort(key=key)
    decided.sort(key=lambda i: str(i.get("decided_at") or ""), reverse=True)
    return {"open": open_, "decided": decided}


# ── Settings › Transfer: why the bridge may not be turned off ───────────────

def fleet_refusals(*, machines: List[dict], first_v2: Dict[str, Optional[str]],
                   newest_legacy_row: Optional[str], now: datetime,
                   days: int = BRIDGE_OFF_DAYS) -> List[str]:
    """§12.1 step 5, the bench half of the bridge-off rule (custody adds the
    off-host half). `machines` are the registered, not retired machines
    ({machine_uid, title}); `first_v2` uid -> the first v2 sync (None: never);
    `newest_legacy_row` the newest row the bridge pulled in from LabCore
    (None: none ever)."""
    out = []
    old = sorted((m for m in machines if not first_v2.get(m["machine_uid"])),
                 key=lambda m: str(m.get("title") or m["machine_uid"]).lower())
    if old:
        out.append("%d %s not reported over v2 yet: %s. Each must, for %d days; "
                   "retire one that is stopped for good." % (
                       len(old), _plural(len(old), "bench has", "benches have"),
                       _names([m.get("title") or m["machine_uid"] for m in old], 4), days))
    young = []
    for m in machines:
        at = _parse(first_v2.get(m["machine_uid"]))
        if at is not None and (now - at).total_seconds() < days * 86400:
            young.append((at, m.get("title") or m["machine_uid"]))
    if young:
        young.sort()
        ready = (young[-1][0] + timedelta(days=days)).astimezone()
        out.append("%s %s reported over v2 for under %d days; the last of %s "
                   "reaches %d days on %d %s." % (
                       _names([t for _a, t in young], 4),
                       _plural(len(young), "has", "have"), days,
                       _plural(len(young), "it", "them"), days,
                       ready.day, ready.strftime("%b")))
    at = _parse(newest_legacy_row)
    if at is not None and (now - at).total_seconds() < days * 86400:
        out.append("The bridge brought in a reading from LabCore on %s; it can be "
                   "turned off once %d days pass with none." % (
                       _hm(newest_legacy_row, now) or newest_legacy_row, days))
    return out
