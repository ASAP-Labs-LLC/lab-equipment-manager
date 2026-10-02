"""ui_record.py: what the instrument record says (ia-final §3.1, piece 5).

``/instruments/<uid>`` answers "can it run?" first and then shows the QC that
answer rests on. This module turns one merged machine (the snapshot with the
live road overlaid) and its row of ``/api/ui/instruments`` into the record's
payload. It is pure: no gateway, no clock it was not handed, **0 LabCore ops**.

The verdict is NOT computed here. It is the row's, which is
``ui_live.readiness``'s, so the record, the Instruments home, the nav count and
the bell cannot disagree (§0.2). Ryan, 2026-10-01: only QC, or an override /
out of service, makes the answer No. An overdue PM or calibration is a
warning; the card says it in the same sentence and offers no stop.

What this module adds is what only the record shows:

* the card's one sentence, in parts the page joins with local times
  (``caption``: what, against which standard, when, what else, next);
* the ONE primary button (§0.1): "Open a corrective action…" when QC stopped
  it, "Put back on line…" when somebody took it off line, and nothing when
  there is nothing to do here. A control is offered once: when the primary
  puts it back on line, the topbar and the On line tile do not;
* the QC table, one row per check, short names in boiling order for a
  distillation, each with its own verdict word (§4.1). An assigned check that
  has never been judged reads **No verdict yet**, and only an instrument with
  nothing assigned reads **No QC assigned** (judge J3's Eravap mislabel);
* the read-only Maintenance and Bench sections the tiles link to, so no tile
  is a dead end before pieces 6 and 7 add their actions.

Numbers stay numbers: formatting is ``fmtQC`` in static/js/record_logic.js,
one function for the whole page, so a band never mixes decimals.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import ui_instruments
import ui_live
from ui_live import CANT_TELL, NO_QC, NOT_OK, OFF_LINE, OK, OK_BUT

# The record's sections that exist today, in page order. A tile's link must
# land on one of these (tests/test_ui_record.py).
SECTIONS = ("qc", "maintenance", "bench")

UNITS = {"C": "°C", "F": "°F", "degC": "°C", "degF": "°F"}

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _and(items: List[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _day(iso: str) -> str:
    try:
        _y, mo, d = (int(x) for x in str(iso)[:10].split("-"))
        return "%d %s" % (d, _MONTHS[mo - 1])
    except (ValueError, IndexError):
        return ""


# ── names ───────────────────────────────────────────────────────────────────

def short_test(name: str) -> Tuple[str, str]:
    """(title, method) for a LabCore test name, the way the bench says it.

    "ASTM D2887/D86 - Distillation in Petroleum Products, 10% Recovery"
      -> ("10% Recovery", "ASTM D2887/D86")
    "ASTM D445   40C - Viscosity - Kinematic at 40°C (cSt)"
      -> ("Viscosity - Kinematic at 40°C (cSt)", "ASTM D445 40C")
    "Flash Point" -> ("Flash Point", "")

    The method code is what tells two "Flash Point"s apart, so it stays, under
    the name, rather than being dropped.
    """
    raw = str(name or "").strip()
    if " - " not in raw:
        return raw, ""
    method, rest = raw.split(" - ", 1)
    method = re.sub(r"\s+", " ", method).strip()
    rest = rest.strip()
    if ", " in rest:
        rest = rest.rsplit(", ", 1)[1].strip()
    return rest or raw, method


def _boiling_rank(title: str) -> Tuple[int, str]:
    """IBP, 10%, 50%, 90%, FBP: a distillation reads in the order it boils.
    Anything else keeps its name order, after."""
    t = title.strip().upper()
    if t == "IBP" or t.startswith("IBP "):
        return (-1, title)
    if t == "FBP" or t.startswith("FBP "):
        return (101, title)
    m = re.match(r"^(\d{1,3})\s*%", t)
    if m:
        return (int(m.group(1)), title)
    return (1000, title.lower())


# ── one check ───────────────────────────────────────────────────────────────

def _verdict(spec: Optional[dict], checking_in: bool, never: bool) -> dict:
    """§4.1, one word per check. `spec` None = assigned, nothing on file."""
    ok = None if spec is None else spec.get("last_qc_in_spec")
    if ok is False:
        return {"key": "out", "word": "Out of spec", "glyph": "error", "detail": ""}
    if ok is True:
        return {"key": "in", "word": "In spec", "glyph": "final", "detail": ""}
    if not checking_in:
        return {"key": "none", "word": "No verdict yet", "glyph": "never",
                "detail": "bench never checked in" if never else "bench stopped"}
    if spec is not None and spec.get("last_qc_at"):
        return {"key": "due", "word": "QC due", "glyph": "half", "detail": ""}
    return {"key": "none", "word": "No verdict yet", "glyph": "never", "detail": "never run"}


def _num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def checks(m: dict) -> List[dict]:
    """The QC table's rows: every check in force, then every assignment the
    bench has not published a band for (assigned, never judged)."""
    running = bool(m.get("live") or m.get("module_running"))
    never = not running and not m.get("last_poll")
    out = []
    seen = set()
    for s in m.get("effective_specs") or []:
        if s.get("last_qc_superseded_by"):
            continue
        name = str(s.get("test_name") or "")
        seen.add(name)
        title, method = short_test(name)
        out.append({
            "test": name, "title": title, "method": method,
            "sample_id": str(s.get("sample_id") or ""),
            "low": _num(s.get("low")), "expected": _num(s.get("expected")),
            "high": _num(s.get("high")),
            "units": UNITS.get(str(s.get("units") or ""), str(s.get("units") or "")),
            "value": _num(s.get("last_qc_value")), "at": s.get("last_qc_at") or None,
            "correction": _num(s.get("correction")) or None,
            "verdict": _verdict(s, running, never),
        })
    for t in m.get("qc_targets") or []:
        name = str(t.get("test") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        title, method = short_test(name)
        out.append({
            "test": name, "title": title, "method": method,
            "sample_id": str(t.get("sample") or ""),
            "low": None, "expected": None, "high": None, "units": "",
            "value": None, "at": None, "correction": None,
            "verdict": _verdict(None, running, never),
        })
    out.sort(key=lambda c: _boiling_rank(c["title"]))
    return out


def _selected(rows: List[dict]) -> Optional[str]:
    for key in ("out", "due"):
        for c in rows:
            if c["verdict"]["key"] == key:
                return c["test"]
    return rows[0]["test"] if rows else None


# ── the card ────────────────────────────────────────────────────────────────

def _since(m: dict, kind: str) -> str:
    due = min((str(t.get("next_due") or "") for t in m.get("maintenance") or []
               if str(t.get("kind") or "").lower() == kind and t.get("status") == "RED"
               and t.get("next_due")), default="")
    return " since %s" % _day(due) if _day(due) else ""


def _stds(rows: List[dict]) -> str:
    return _and(sorted({c["sample_id"] for c in rows if c["sample_id"]}))


def _newest(rows: List[dict]) -> Optional[str]:
    ats = [str(c["at"]) for c in rows if c.get("at")]
    return max(ats) if ats else None


def caption(m: dict, state: str, reason: str, rows: List[dict], keys: List[str]) -> dict:
    """The bar's one sentence, in parts: {lead, std, at, too, next}.

    record_logic.captionText joins them with the local time:
    "QC out of spec on 10% Recovery and 50% Recovery (AF26, Wed 15:04).
     Next: fix, then rerun AF26."
    """
    bad = [c for c in rows if c["verdict"]["key"] == "out"]
    due = [c for c in rows if c["verdict"]["key"] == "due"]
    std, at, nxt = "", None, None
    if state == NOT_OK:
        lead = "QC out of spec on " + _and([c["title"] for c in bad])
        std, at = _stds(bad), _newest(bad)
        nxt = "Fix, then rerun " + (std or "the standard")
    elif state == OK_BUT and reason.startswith("QC due"):
        lead = "QC due on " + _and([c["title"] for c in due])
        std, at = _stds(due), _newest(due)
        nxt = "Run " + (std or "the QC standard")
    elif state == OK_BUT and reason.startswith("Calibration"):
        lead = "Calibration overdue" + _since(m, "calibration")
        nxt = "Calibrate it, then mark the calibration done"
    elif state == OK_BUT:
        lead = "PM overdue" + _since(m, "pm")
        nxt = "Do the PM, then mark it done"
    elif state == OFF_LINE:
        lead = _OVERRIDE_WORDS.get(reason, reason or "Out for service")
        nxt = "Put it back on line when the work is done"
    elif state == CANT_TELL:
        if reason == "Lab closed":
            lead = "The lab is closed and its bench is resting"
        elif reason == "Bench stopped":
            # when it stopped is the Bench tile's detail; said once
            lead = "Its bench stopped checking in, so nothing new is judged"
            nxt = "Start the LEM module in LabStation on its computer"
        else:
            lead = "Its bench has never checked in, so nothing is judged"
            nxt = "Start the LEM module in LabStation on its computer"
    elif state == NO_QC:
        lead = "Nothing is assigned to judge it by"
        nxt = "Assign a QC standard"
    else:
        n = len([c for c in rows if c["verdict"]["key"] == "in"])
        lead = ("All %d checks in spec" % n) if n > 1 else "Its check is in spec"
        std, at = _stds(rows), _newest(rows)
    # every other problem, said once, in the same sentence (round 3's critic:
    # an overdue PM behind an overdue calibration was said nowhere)
    behind = keys if state == OFF_LINE else keys[1:]
    too = []
    if "ok_but-qc" in behind:
        too.append("QC due on %s too" % _and([c["title"] for c in due]))
    if "ok_but-cal" in behind:
        too.append("calibration overdue%s too" % _since(m, "calibration"))
    if "ok_but-pm" in behind:
        too.append("PM overdue%s too" % _since(m, "pm"))
    return {"lead": lead, "std": std, "at": at, "too": "; ".join(too), "next": nxt}


def _primary(state: str, rows: List[dict]) -> Optional[dict]:
    """The page's one .btn-primary (§0.1). Can't tell, No QC assigned, OK
    and a warning have none: their next step is not a button on this page."""
    if state == NOT_OK:
        first = next((c for c in rows if c["verdict"]["key"] == "out"), None)
        return {"label": "Open a corrective action…", "act": "action",
                "test": first["test"] if first else ""}
    if state == OFF_LINE:
        return {"label": "Put back on line…", "act": "online"}
    return None


_OVERRIDE_WORDS = {"Taken off line (SERVICE)": "Taken off line for service",
                   "Taken off line (DEAD-LINE)": "Taken off line as a dead line"}


def _tiles(row: dict, primary: Optional[dict]) -> List[dict]:
    """The home's tiles, with their links made local to this page. An action
    the topbar or the primary already offers is not offered again."""
    out = []
    for t in row["readiness"]["tiles"]:
        t = dict(t)
        a = t.get("action") or None
        if t["key"] == "online":
            act = "online" if t["word"] == "Off line" else "offline"
            if act == "online":
                t["glyph"] = "off"      # §4.1: off line is the rotated square
                t["detail"] = _OVERRIDE_WORDS.get(t.get("detail") or "", t.get("detail") or "")
            # The control is the topbar's "Take off line…" while it is on
            # line, and the card's primary "Put back on line…" while it is
            # off: a third copy on the tile would be the "said twice" defect.
            a = None
        elif t["key"] == "maintenance" and t.get("glyph") == "bad":
            t["glyph"] = "due"          # a warning, never the stop triangle
        if t["key"] != "online" and a and a.get("href"):
            section = a["href"].split("#", 1)[1] if "#" in a["href"] else ""
            a = {"label": a["label"], "href": "#" + section} if section in SECTIONS else None
        t["action"] = a
        out.append(t)
    return out


# ── the read-only sections ──────────────────────────────────────────────────

# An overdue task is a WARNING (Ryan, 2026-10-01): it wears the half glyph,
# never QC's stop triangle.
_TASK = {"RED": ("Overdue", "half"), "YELLOW": ("Due soon", "half"), "GREEN": ("Scheduled", "final")}


def maintenance(m: dict) -> List[dict]:
    out = []
    for t in m.get("maintenance") or []:
        word, glyph = _TASK.get(str(t.get("status") or ""), ("Scheduled", "final"))
        out.append({"uid": t.get("uid"), "name": str(t.get("name") or t.get("kind") or "Task"),
                    "kind": str(t.get("kind") or ""), "every": t.get("interval_days"),
                    "last_done": t.get("last_done") or None, "next_due": t.get("next_due") or None,
                    "word": word, "glyph": glyph})
    out.sort(key=lambda t: (str(t["next_due"] or "9999"), t["name"]))
    return out


def bench(m: dict) -> dict:
    b = ui_instruments.bench(m)
    return {"reads_from": str(m.get("watching") or ""),
            "source": ui_instruments.source_caption(m.get("watching")),
            "word": b["word"], "glyph": b["glyph"], "at": b["at"],
            "live_road": bool(m.get("live")),
            # the transfer guard's counter: not reported by today's module.
            # None is drawn as words, never as 0 (§3.1 #7, §7).
            "replays_not_resent": None}


# ── the record ──────────────────────────────────────────────────────────────

def build(row: dict, m: dict, levels: Dict[str, str], override: Optional[str] = "") -> dict:
    """The record's payload for one instrument that was read."""
    state = row["readiness"]["state"]
    reason = str(row["readiness"].get("reason") or "")
    rows = checks(m)
    keys = ui_live.problems(m, override)
    primary = _primary(state, rows)
    off = state == OFF_LINE
    b = ui_instruments.bench(m)
    return {
        "state": "ready",
        "uid": row["uid"],
        "title": row["title"],
        "head": {
            "bench": {"word": b["word"], "at": b["at"]},
            "last_result_at": m.get("last_activity") or None,
            "level": levels.get(m.get("level_uid") or "") or None,
            "placed": m.get("pos") is not None,
            "uid": row["uid"],
        },
        "topbar": {"online": None if off else {"label": "Take off line…", "act": "offline"},
                   "map": "/?view=map&focus=" + row["uid"]},
        "readiness": {
            "state": state, "word": row["readiness"]["word"], "glyph": row["readiness"]["glyph"],
            "caption": caption(m, state, reason, rows, keys),
            "primary": primary,
            "tiles": _tiles(row, primary),
        },
        "qc": {"assigned": bool(rows), "checks": rows, "selected": _selected(rows),
               "standards": sorted({c["sample_id"] for c in rows if c["sample_id"]})},
        "maintenance": maintenance(m),
        "bench": bench(m),
    }


# ── the chart's range ───────────────────────────────────────────────────────

RANGES = ("24", "90d", "all")


def trim_points(points: List[dict], rng: str, now_iso: str) -> List[dict]:
    """The points a chart range shows. TRUNCATE, THEN ANALYSE: the caller runs
    the control rules on exactly these, so a violation's indices are
    positions in what is drawn. An unknown range is refused, never guessed."""
    if rng == "24":
        return list(points[-24:])
    if rng == "all":
        return list(points)
    if rng == "90d":
        cut = (datetime.fromisoformat(str(now_iso)[:19]) - timedelta(days=90)).isoformat()
        return [p for p in points if str(p.get("ts") or "")[:19] >= cut]
    raise ValueError("A chart range is one of %s, not %r." % (", ".join(RANGES), rng))
