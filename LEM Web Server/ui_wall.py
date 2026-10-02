"""ui_wall.py: what the wall kiosks say (ia-final §3.8). Pure.

Two screens hang in the lab and nobody stands at them: ``/floor`` ("can the
lab run?") and ``/qc`` ("how are the checks?"). Everything they say is
computed here, from what the app already holds in memory, so the wall and the
record page cannot disagree and a test can hold every sentence to the record:

* ``floor(payload)`` reads the ``/api/ui/instruments`` answer
  (``ui_instruments.build``): the readiness of every instrument, by the one
  rule ``ui_live.readiness``. It adds the headline sentence, the counts (one
  per readiness state, adding up to the fleet) and Needs attention (the worst
  five, same-cause instruments merged).
* ``qc(machines, rows, ...)`` makes one card per (instrument, check). The
  VERDICT is the snapshot's (``effective_specs``, the same field the record's
  QC section and the Instruments table read); the local record (the log
  mirror) only supplies the chart history.

A failed read is never an empty result: an unread payload gives counts of
``None`` and a headline that says it is reading or that it could not read,
never "All 0 instruments can run". The wall's own staleness (no answer from
``/api/ui/live`` for 90 s) is the browser's to say, in static/js/wall_logic.js:
only it knows when it last heard from the server.

Nothing here imports a gateway. **0 LabCore ops**; the routes that serve this
are counted in tests/test_wall_pages.py.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence

import qc_series
import ui_live
from ui_instruments import GLYPH, RANK, WORDS, _and
from ui_live import CANT_TELL, NEEDS_YOU, NO_QC, NOT_OK, OFF_LINE, OK, OK_BUT

# The counts read best first, the way the room scans a row of numbers: how
# many are fine, then the shades of trouble, then the setup facts.
COUNT_ORDER = (OK, OK_BUT, NOT_OK, OFF_LINE, CANT_TELL, NO_QC)
CAN_RUN = (OK, OK_BUT, NO_QC)        # Ryan, 1 Oct: only QC or an override says No
ATTENTION_MAX = 5
HISTORY_POINTS = 30                  # a wall card is a glance, not the record's chart
CONTROL_RECENT = 5                   # a rule broken this many runs ago still shows


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _lower_first(s: str) -> str:
    return ui_live._lower_first(s)


def _first(detail: str) -> str:
    """The verdict's own fact, without the "· … too" facts behind it."""
    return str(detail or "").split(" · ")[0]


_DATE = re.compile(r"\b(since )?(\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b")


def _glue(text: str) -> str:
    """"since 21 Jun" with no-break spaces: a wall line may wrap, but never
    inside a date (round 4: "since 21 / Jun too")."""
    return _DATE.sub(lambda m: ("since\u00a0" if m.group(1) else "") + m.group(2) + "\u00a0" + m.group(3),
                     str(text or ""))


def _names(titles: List[str], limit: int = 2) -> str:
    """"A", "A and B", "A, B and 1 more" (a wall line stays one line)."""
    t = [x for x in titles if x]
    if len(t) <= limit:
        return _and(t)
    return ", ".join(t[:limit]) + " and %d more" % (len(t) - limit)


# ── /floor ──────────────────────────────────────────────────────────────────

def compact_tests(names: List[str]) -> str:
    """Several check names, said by what differs when they share a long
    common start: "ASTM D2887/D86 - Distillation in Petroleum Products,
    10% Recovery" and "…, 50% Recovery" are "10% Recovery and 50% Recovery".
    The cut is at a ", " or " - " so no word is split."""
    names = sorted({str(n) for n in names if n})
    if len(names) < 2:
        return "".join(names)
    pre = names[0]
    for n in names[1:]:
        while not n.startswith(pre):
            pre = pre[:-1]
    cut = max(pre.rfind(", ") + 2 if ", " in pre else 0, pre.rfind(" - ") + 3 if " - " in pre else 0)
    if cut >= 12 and all(len(n) > cut for n in names):
        return _and([n[cut:] for n in names])
    return _and(names)


def _failed(machines: Optional[List[dict]]) -> Dict[str, List[str]]:
    """uid -> the checks whose last result failed (the snapshot's)."""
    out: Dict[str, List[str]] = {}
    for m in machines or []:
        bad = [str(s.get("test_name") or "") for s in m.get("effective_specs") or []
               if s.get("last_qc_in_spec") is False and not s.get("last_qc_superseded_by")]
        if bad:
            out[str(m.get("machine_uid"))] = bad
    return out


def _stop_words(uid: str, failed: Dict[str, List[str]], fallback: str) -> str:
    """What a stop is about: "2 checks out of spec: 10% Recovery and 50%
    Recovery", or "Flash Point out of spec"."""
    bad = failed.get(uid)
    if not bad:
        return fallback
    if len(bad) == 1:
        return "%s out of spec" % bad[0]
    return "%d checks out of spec: %s" % (len(bad), compact_tests(bad))

def _unread(payload: dict) -> dict:
    err = payload.get("error")
    if err:
        return {"state": "unreadable", "headline": "Can't read the lab", "tone": "unknown",
                "sub": "%s. This wall is blank because the record could not be read, "
                       "not because every instrument can run." % str(err).rstrip("."),
                "counts": None, "total": None, "attention": None, "attention_more": None,
                "benches": None}
    return {"state": "not_read", "headline": "Reading the lab…", "tone": "unknown",
            "sub": "The first read of the record has not finished. Nothing here is a verdict yet.",
            "counts": None, "total": None, "attention": None, "attention_more": None,
            "benches": None}


def counts(rows: List[dict]) -> List[dict]:
    """One count per readiness state, best first. Every row is in exactly one;
    a zero is left out (except OK, whose zero is the finding)."""
    n = {s: 0 for s in COUNT_ORDER}
    for r in rows:
        n[r["readiness"]["state"]] += 1
    return [{"state": s, "n": n[s], "word": WORDS[s], "glyph": GLYPH[s]}
            for s in COUNT_ORDER if n[s] or s == OK]


def attention(rows: List[dict], failed: Optional[Dict[str, List[str]]] = None) -> tuple:
    """Needs attention: the instruments that need somebody (NEEDS_YOU), one
    line per (state, cause), worst first, at most five. -> (items, more),
    `more` being how many instruments are on lines that did not fit."""
    groups: Dict[tuple, List[dict]] = {}
    for r in rows:
        if r["readiness"]["state"] not in NEEDS_YOU or not r.get("cause"):
            continue
        # a stop is never merged: each is named with its own failed checks
        # nor are "QC due" and "No verdict yet": one cause key (one filter),
        # two facts, each said in /qc's word for it
        key = (r["readiness"]["state"], r["cause"]["key"],
               r["uid"] if r["readiness"]["state"] == NOT_OK else "", r["cause"]["words"])
        groups.setdefault(key, []).append(r)
    order = list(ui_live.PROBLEM_WORDS)
    items = []
    for (state, cause, _one, _words), members in groups.items():
        members.sort(key=lambda r: (r["title"].lower(), r["uid"]))
        one = len(members) == 1
        items.append({
            "key": cause, "state": state, "word": WORDS[state], "glyph": GLYPH[state],
            "names": [m["title"] for m in members], "uids": [m["uid"] for m in members],
            "label": _names([m["title"] for m in members]),
            "detail": (_stop_words(members[0]["uid"], failed or {}, members[0]["readiness"]["detail"])
                       + _too_of(members[0]["readiness"]["detail"]) if state == NOT_OK
                       else members[0]["readiness"]["detail"] if one else members[0]["cause"]["words"]),
            "href": members[0]["cause"]["href"] if one else "/?cause=" + cause,
            "stopped": all(m["bench"]["state"] in ("stopped", "never") for m in members),
        })
    items.sort(key=lambda i: (RANK[i["state"]], order.index(i["key"]) if i["key"] in order else 99,
                              i["detail"] != ui_live.PROBLEM_WORDS.get(i["key"]) and not i["detail"].startswith("QC due"),
                              i["names"][0].lower()))
    for i in items:
        i["detail"] = _glue(i["detail"])
    shown, rest = items[:ATTENTION_MAX], items[ATTENTION_MAX:]
    return shown, sum(len(i["names"]) for i in rest)


def runs(items, key: str) -> List[List[dict]]:
    """`items` cut into runs of one `key`, in their own order: Needs
    attention's groups, one state word heading each (the floor wall)."""
    out: List[List[dict]] = []
    for it in items or []:
        if out and out[-1][0].get(key) == it.get(key):
            out[-1].append(it)
        else:
            out.append([it])
    return out


def _too_of(detail: str) -> str:
    """The " · … too" facts behind a verdict, kept as they are."""
    parts = str(detail or "").split(" · ")
    return "".join(" · " + p for p in parts[1:])


def _short_when(iso, now: datetime) -> str:
    """"13:10" today, "30 Sep" before: the lab's local time (the server is
    in the lab and stamps its own local time)."""
    try:
        at = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return ""
    if at.tzinfo is not None:
        at = at.astimezone().replace(tzinfo=None)
    if at.date() == now.date():
        return at.strftime("%H:%M")
    return "%d %s" % (at.day, at.strftime("%b"))


SHORT = {"Calibration overdue": "Cal. overdue", "Never checked in": "Never seen"}


def bay_details(payload: dict, now: Optional[datetime] = None, short: bool = False) -> Dict[str, str]:
    """uid -> the bay's one detail line on the wall: the fact its verdict
    word leaves out, short enough for a 130px bay. `short` gives the line a
    narrower bay falls back to before plan.js cuts it (it keeps the date)."""
    if not isinstance(payload, dict) or payload.get("state") != "ready":
        return {}
    now = now or datetime.now()
    out = {}
    for r in payload.get("instruments") or []:
        state = r["readiness"]["state"]
        bench = r.get("bench") or {}
        if state == NOT_OK:
            line = "QC out of spec"
        elif state == OK_BUT:
            cause = r.get("cause") or {}
            line = cause.get("words") or ui_live.PROBLEM_WORDS.get(cause.get("key", ""), r["readiness"].get("reason") or "")
        elif state == OFF_LINE:
            line = r["readiness"].get("reason") or "Off line"
        elif state == CANT_TELL:
            if bench.get("state") == "closed":
                line = "Lab closed"
            elif bench.get("at"):
                line = ("Silent " if short else "Silent since ") + _short_when(bench["at"], now)
            else:
                line = "Never checked in"
        elif state == NO_QC:
            line = bench.get("word") or ""
        else:
            at = (r.get("last_qc") or {}).get("at")
            line = ("QC " + _short_when(at, now)) if at else ""
        out[r["uid"]] = _glue(SHORT.get(line, line) if short else line)
    return out


def floor(payload: dict, machines: Optional[List[dict]] = None) -> dict:
    """The wall's words over one ``/api/ui/instruments`` answer. `machines`
    (the merged snapshot rows the answer was built from) only sharpen how a
    stop's failed checks are named."""
    failed = _failed(machines)
    if not isinstance(payload, dict) or payload.get("state") != "ready" \
            or payload.get("instruments") is None:
        return _unread(payload or {})
    rows = payload["instruments"]
    total = len(rows)
    items, more = attention(rows, failed)
    base = {"state": "ready", "total": total, "counts": counts(rows), "attention": items,
            "attention_more": more,
            # the footer's "12 of 13 benches checking in": the live feed's
            # fleet.checking_in, from the same snapshot (§0.2)
            "benches": {"checking_in": sum(1 for r in rows if r["bench"]["state"] == "in"),
                        "total": total}}
    if not total:
        return dict(base, counts=[], tone="unknown", headline="No instruments in LEM yet",
                    sub="They are added in LabStation › LEM module › New machine…")
    by = {s: [r for r in rows if r["readiness"]["state"] == s] for s in COUNT_ORDER}
    attn = len(by[OK_BUT]) + len(by[CANT_TELL])
    if by[NOT_OK]:
        bad = by[NOT_OK]
        n = len(bad)
        head = "%d %s not OK to run" % (n, _plural(n, "instrument is", "instruments are"))
        what = (_stop_words(bad[0]["uid"], failed, _first(bad[0]["readiness"]["detail"]))
                if n == 1 else "QC out of spec")
        sub = "%s · %s." % (_names([r["title"] for r in bad]), what)
        if attn:
            sub += " %d more %s attention." % (attn, _plural(attn, "needs", "need"))
        if by[OFF_LINE]:
            sub += " %d off line." % len(by[OFF_LINE])
        return dict(base, tone="stop", headline=head, sub=sub)
    can = sum(len(by[s]) for s in CAN_RUN)
    if can == total:
        head = ("The 1 instrument can run" if total == 1
                else "All %d instruments can run" % total)
        k = len(by[OK_BUT])
        if k:
            order = list(ui_live.PROBLEM_WORDS)
            # each cause in the word its instruments' rows say ("QC due",
            # "No verdict yet"), so the sentence and /qc agree
            causes = sorted({(r["cause"]["key"], r["cause"]["words"]) for r in by[OK_BUT] if r.get("cause")},
                            key=lambda c: (order.index(c[0]) if c[0] in order else 99,
                                           c[1] != ui_live.PROBLEM_WORDS.get(c[0]), c[1]))
            words = _and([_lower_first(w) for _k, w in causes])
            sub = "%d %s attention: %s." % (k, _plural(k, "needs", "need"), words)
        else:
            sub = "Nothing needs attention."
        return dict(base, tone="ok", headline=head, sub=sub)
    head = "%d of %d instruments can run" % (can, total)
    parts = []
    for r in by[CANT_TELL] + by[OFF_LINE]:
        if r["readiness"]["state"] == OFF_LINE:
            parts.append("%s · off line." % r["title"])
        else:
            parts.append("%s · can't tell: %s." % (
                r["title"], _lower_first(_first(r["readiness"]["detail"]))))
    sub = " ".join(parts[:2])
    if len(parts) > 2:
        sub += " %d more can't tell or are off line." % (len(parts) - 2)
    if by[OK_BUT]:
        k = len(by[OK_BUT])
        sub += " %d more %s attention." % (k, _plural(k, "needs", "need"))
    return dict(base, tone="warn", headline=head, sub=sub.strip())


# ── /qc ─────────────────────────────────────────────────────────────────────

VERDICTS = {
    "out": ("Out of spec", "error", 0),
    "due": ("QC due", "half", 1),
    "never": ("No verdict yet", "never", 2),
    "in": ("In spec", "final", 3),
}


def _decimals(x: float) -> int:
    s = repr(float(x))
    if "e" in s or "E" in s:
        s = "%.10f" % float(x)
    frac = s.split(".")[1].rstrip("0") if "." in s else ""
    return min(len(frac), 6)


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def fmt_qc(v, band: Sequence = ()) -> str:
    """§4.2: a value at the decimals its band is written in (the most any of
    low / expected / high uses, at least 1), so 334.90 sits in
    331.48 – 334.90 – 338.32 and 0.0011 keeps its places. Without a band, a
    value under 0.01 keeps two significant figures."""
    f = _num(v)
    if f is None:
        return "—"
    lims = [x for x in (_num(b) for b in band or ()) if x is not None]
    if lims:
        return "%.*f" % (max(1, max(_decimals(x) for x in lims)), f)
    if f and abs(f) < 0.01:
        return "%.2g" % f
    return ("%.4f" % f).rstrip("0").rstrip(".") if f != int(f) else "%.1f" % f


MINUS = "\u2212"


def _signed(text: str) -> str:
    """A written number with the minus sign, not a hyphen: beside the band's
    en dashes "-24.7 – -18.3" reads as a row of dashes."""
    return MINUS + text[1:] if text.startswith("-") else text


def _band_text(sp: dict) -> str:
    low, exp, high = _num(sp.get("low")), _num(sp.get("expected")), _num(sp.get("high"))
    if low is None or high is None:
        return ""
    lims = (low, exp, high) if exp is not None else (low, high)
    return " – ".join(_signed(fmt_qc(x, lims)) for x in lims)


def _band_words(sp: dict) -> tuple:
    """("185.05 to 190.21", "187.63"): the card's limits, said as words so a
    negative band ("−24.7 to −11.9") cannot be misread, and the target
    apart. ("", "") without both limits."""
    low, exp, high = _num(sp.get("low")), _num(sp.get("expected")), _num(sp.get("high"))
    if low is None or high is None:
        return "", ""
    lims = tuple(x for x in (low, exp, high) if x is not None)
    return ("%s to %s" % (_signed(fmt_qc(low, lims)), _signed(fmt_qc(high, lims))),
            _signed(fmt_qc(exp, lims)) if exp is not None else "")


def _split_code(name: str) -> tuple:
    """"ASTM D6304 - Water, by Karl Fischer" -> ("ASTM D6304", "Water, by
    Karl Fischer"). A name with no standard code in front stays whole."""
    name = " ".join(str(name or "").split())
    head, sep, rest = name.partition(" - ")
    if sep and rest and re.match(r"^(ASTM|IP|ISO|EN|UOP|GPA|DIN|ANSI|API)\b", head):
        return head, rest
    return "", name


def check_words(names: List[str]) -> Dict[str, tuple]:
    """name -> (check, method) for one instrument's checks: what tells each
    check apart, to lead the card, and what they share, for a quieter line.
    Agilent GC 1's five are "10% Recovery" … "IBP" under "ASTM D2887/D86 ·
    Distillation in Petroleum Products"; a lone check is its words without
    its standard code ("Water, by Karl Fischer" under "ASTM D6304")."""
    out: Dict[str, tuple] = {}
    uniq = sorted({str(n) for n in names if n})
    split = {n: _split_code(n) for n in uniq}
    # checks of one method share a long start; the part after it is the check
    groups: Dict[str, List[str]] = {}
    for n in uniq:
        groups.setdefault(split[n][0], []).append(n)
    for code, members in groups.items():
        rests = [split[n][1] for n in members]
        cut, family = 0, ""
        if len(rests) > 1:
            pre = rests[0]
            for r in rests[1:]:
                while not r.startswith(pre):
                    pre = pre[:-1]
            c = pre.rfind(", ") + 2 if ", " in pre else 0
            if c >= 8 and all(len(r) > c for r in rests):
                cut, family = c, pre[:c - 2]
        for n, r in zip(members, rests):
            method = " · ".join(x for x in (code, family) if x)
            out[n] = (r[cut:], method)
    return out


RULE_WORDS = {
    qc_series.RULE_1_3S: "1 beyond 3s",
    qc_series.RULE_2OF3_2S: "2 of 3 beyond 2s",
    qc_series.RULE_4OF5_1S: "4 of 5 beyond 1s",
    qc_series.RULE_SHIFT: "Shift: 9 on one side",
    qc_series.RULE_TREND: "Trend: 7 in a row",
}


def _control(series) -> Optional[dict]:
    """The newest control rule broken among the last few runs, or None."""
    pts = series.points
    if len(pts) < 3:
        return None
    try:
        found = qc_series.analyse(series).violations
    except Exception:                                  # noqa: BLE001 a chip is never worth a 500
        return None
    recent = [v for v in found if max(v.indices) >= len(pts) - CONTROL_RECENT]
    if not recent:
        return None
    v = max(recent, key=lambda v: (max(v.indices), v.rule))
    words = RULE_WORDS.get(v.rule, v.rule)
    return {"rule": v.rule, "provisional": bool(v.provisional),
            "words": words + (" · provisional" if v.provisional else "")}


def _key(test) -> str:
    return " ".join(str(test or "").split()).lower()


def qc(machines: Optional[List[dict]], *, rows: Optional[List[dict]],
       href: Callable[[str, str], str], now: Optional[datetime] = None,
       error: Optional[str] = None, missing: str = "unread") -> dict:
    """``/qc``'s cards and headline.

    `machines` are the snapshot's merged machines (None: not read), `rows`
    the QC rows of the local record (None: not there; the cards keep their
    verdicts and say why the history is missing: `missing` is "unread" when
    the read failed, "filling" when the log copy has not filled yet, which
    is not a failure)."""
    if machines is None:
        if error:
            return {"state": "unreadable", "headline": "Can't read QC", "tone": "unknown",
                    "sub": "%s. This wall is blank because the record could not be read, "
                           "not because every check passed." % str(error).rstrip("."),
                    "cards": None, "counts": None}
        return {"state": "not_read", "headline": "Reading QC…", "tone": "unknown",
                "sub": "The first read of the record has not finished. Nothing here is a verdict yet.",
                "cards": None, "counts": None}
    by_key = qc_series.series_from_rows(rows) if rows else {}
    cards = []
    for m in machines:
        uid = m.get("machine_uid")
        title = m.get("title") or uid
        checking_in = bool(m.get("live") or m.get("module_running"))
        specs = [sp for sp in m.get("effective_specs") or [] if not sp.get("last_qc_superseded_by")]
        # §4.1: an assignment with no result yet is a check too ("No verdict
        # yet"), not an absence. Production's Eravap has only this.
        have = {_key(sp.get("test_name")) for sp in specs}
        for t in m.get("qc_targets") or []:
            test = t.get("test") or t.get("test_name") or ""
            if _key(test) and _key(test) not in have:
                have.add(_key(test))
                specs.append({"test_name": " ".join(str(test).split()),
                              "sample_id": t.get("sample") or t.get("sample_name") or "",
                              "_assigned_only": True})
        words = check_words([sp.get("test_name") for sp in specs])
        for sp in specs:
            ok = sp.get("last_qc_in_spec")
            key = "out" if ok is False else "in" if ok is True else (
                "due" if sp.get("last_qc_at") else "never")
            if key == "in" and not checking_in:
                # §4.1: a stopped bench's check has no verdict yet. Its last
                # pass is history (shown, dated), not today's answer; the
                # floor calls the same instrument "Can't tell".
                key = "never"
            word, glyph, rank = VERDICTS[key]
            test = str(sp.get("test_name") or "")
            lims = (sp.get("low"), sp.get("expected"), sp.get("high"))
            series = qc_series.current_series(by_key, uid, test) if by_key else None
            points, history, control = [], (missing if rows is None else "none"), None
            if series is not None and series.points:
                pts = series.points[-HISTORY_POINTS:]
                band = series.pass_band
                low, high = (band.low, band.high) if band else (_num(sp.get("low")), _num(sp.get("high")))
                exp = (band.expected if band and band.expected is not None
                       else _num(sp.get("expected")))
                if exp is None and low is not None and high is not None:
                    exp = (low + high) / 2.0
                half = (high - low) / 2.0 if low is not None and high is not None else None
                points = [{"z": ((p.value - exp) / half) if half else None, "in_spec": p.in_spec,
                           "at": p.at.isoformat(timespec="seconds") if p.at else None}
                          for p in pts]
                history = "ok"
                # Say it once: "Out of spec" already says the last result
                # failed, and a "1 beyond 3s" chip beside it says it again.
                # The chip is for the card whose verdict looks fine.
                control = None if key == "out" else _control(series)
            rng, target = _band_words(sp)
            check, method = words.get(test, (test, ""))
            cards.append({
                "uid": uid, "title": title, "test": test, "check": check, "method": method,
                "sample_id": sp.get("sample_id") or "",
                "href": href(uid, "qc"), "rank": rank,
                "verdict": {"key": key, "word": word, "glyph": glyph,
                            "note": "" if checking_in else "bench stopped"},
                "last": {"value": fmt_qc(sp.get("last_qc_value"), lims)
                         if sp.get("last_qc_at") else None,
                         "at": sp.get("last_qc_at") or None, "band": _band_text(sp),
                         "range": rng, "target": target, "units": sp.get("units") or ""},
                "points": points, "history": history, "control": control,
            })
    cards.sort(key=lambda c: (c["rank"], c["title"].lower(), c["test"].lower()))
    n = {k: sum(1 for c in cards if c["verdict"]["key"] == k) for k in VERDICTS}
    if not cards:
        return {"state": "ready", "headline": "No QC is assigned to any instrument", "tone": "unknown",
                "sub": "Nothing is being checked, so nothing here is a pass.",
                "cards": [], "counts": n}
    if n["in"] == len(cards):
        head = ("The 1 check is in spec" if len(cards) == 1
                else "All %d checks in spec" % len(cards))
        return {"state": "ready", "headline": head, "tone": "ok", "sub": "",
                "cards": cards, "counts": n}
    parts = []
    if n["out"]:
        parts.append("%d %s out of spec" % (n["out"], _plural(n["out"], "check", "checks")))
    if n["due"]:
        parts.append("%d due" % n["due"])
    if n["never"]:
        parts.append("%d no verdict yet" % n["never"])
    if n["in"]:
        parts.append("%d in spec" % n["in"])
    if not n["out"]:
        # the first number names its unit, as "1 check out of spec" does
        first = parts[0].split(" ", 1)
        parts[0] = "%s %s %s" % (first[0], _plural(int(first[0]), "check", "checks"), first[1])
    sub = ""
    if n["out"]:
        # by instrument: "OptiMPP 1 · Cloud Point and Pour Point.
        # Pensky-Martens 1 · Flash Point."
        by: Dict[str, List[str]] = {}
        for c in cards:
            if c["verdict"]["key"] == "out":
                by.setdefault(c["title"], []).append(c["test"])
        lines = ["%s · %s." % (t, compact_tests(tests)) for t, tests in list(by.items())[:2]]
        if len(by) > 2:
            lines.append("%d more %s." % (len(by) - 2, _plural(len(by) - 2, "instrument", "instruments")))
        sub = " ".join(lines)
    return {"state": "ready", "headline": " · ".join(parts),
            "tone": "stop" if n["out"] else "warn", "sub": sub, "cards": cards, "counts": n}
