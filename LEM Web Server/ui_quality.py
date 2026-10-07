"""ui_quality.py: what /quality and the standards pages say (ia-final §3.5, piece 8).

Three answers, each a pure function of what was read, with no gateway and no
clock it was not handed:

* ``latest``: **Latest checks**, QA's cross-instrument table. One row per
  (instrument, check) in force, worst first. The verdict on each row is
  ``ui_record.checks``'s, which is ``ui_live.check_verdict``'s: the rule the
  record's QC table and its readiness card judge by, so the cross-lab view and
  the record cannot disagree about one check. **There is one verdict.** A
  statistical-control finding (Westgard-style rules over the QC history) is a
  caption on the row, provisional, and only when a rule broke; it never has a
  column of its own (judge J3: proposal B's Verdict and Control columns let
  one check read "In spec" and "Out of control" side by side).
* ``standards``: the library as a table: Lab ID, checks, how many instruments
  are checked on it, and whether its certificate is on file.
* ``standard``: one standard's record. Its **Used on** card names every
  instrument that is checked on it, with that check's verdict, from the
  ASSIGNMENTS in LEM's store (``lem_machine_targets``) and the verdicts in the
  snapshot. The assignment is the store's because the store is where a Save
  puts it: T5 lands on this page a second after Save, before any snapshot has
  been rebuilt, and must say "Agilent GC 2 · Flash Point · No verdict yet".

A failed read is never an empty result: each function has an ``unreadable``
state distinct from its empty one, and a count whose source is missing is
said in words.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import qc_series
import ui_live
import ui_record
from standard_documents import (STATUS_EXPIRED, STATUS_EXPIRING,
                                certificate_status, covering_certificate)

# worst first: a stop, a lapsed pass, a check never run, nothing judged, a pass
RANK = {("out", "Out of spec"): 0, ("due", "QC due"): 1, ("due", "No verdict yet"): 2,
        ("none", "No verdict yet"): 3, ("in", "In spec"): 4}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def rank(verdict: dict) -> int:
    return RANK.get((verdict.get("key"), verdict.get("word")), 3)


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _norm(s: Any) -> str:
    return " ".join(str(s or "").split()).lower()


def standard_href(name: str) -> str:
    return "/quality/standards/" + quote(str(name or ""), safe="")


def _sample(s: Any) -> dict:
    """A library entry as a dict, whether it came as a QcSample or a dict."""
    if hasattr(s, "to_dict"):
        s = s.to_dict()
    return s if isinstance(s, dict) else {}


def _index(library: Optional[Iterable[Any]]) -> Tuple[Dict[str, dict], Dict[str, dict]]:
    """(by Lab ID, by name), both lower-cased: the bench publishes a check's
    standard by its Lab ID; an assignment names it."""
    by_id: Dict[str, dict] = {}
    by_name: Dict[str, dict] = {}
    for s in library or ():
        s = _sample(s)
        name = str(s.get("name") or "").strip()
        lab = str(s.get("sample_id_val") or s.get("lab_id") or "").strip()
        if not name:
            continue
        rec = {"name": name, "lab_id": lab,
               "tests": [t for t in s.get("tests") or [] if isinstance(t, dict)]}
        by_name.setdefault(name.lower(), rec)
        if lab:
            by_id.setdefault(lab.lower(), rec)
    return by_id, by_name


def certified_band(test: str, known: Optional[dict]) -> Optional[Tuple[float, float, float, str]]:
    """(low, expected, high, units) a standard certifies for `test`, by the
    rule the standard's own page uses (``_band``), or None when `known` (an
    ``_index`` entry) does not certify it. One rule for Latest checks, the
    record and the standard's Used on, so the three cannot print two bands."""
    if not known:
        return None
    t = next((t for t in known.get("tests") or []
              if _norm(test) in (_norm(t.get("name")), _norm(t.get("value_col")))), None)
    if not t:
        return None
    units = str(t.get("units") or "")
    return _band(t) + (ui_record.UNITS.get(units, units),)


def fill_certified_bands(rows: List[dict], library: Optional[Iterable[Any]]) -> List[dict]:
    """The record's QC rows (``ui_record.checks``), each told where its band
    came from in ``band_from``:

    * "bench": the band the bench published and judges by;
    * "library": the bench has not published one yet (a new assignment), so
      the row carries the standard's certified band, the one the standard's
      page shows under Used on, and says the bench has not picked it up;
    * "none": the library was read and does not certify this check;
    * "unread": the library could not be read (`library` None). A failed
      read is never "no certified values on file"."""
    by_id, by_name = _index(library) if library is not None else ({}, {})
    for c in rows:
        if c.get("low") is not None or c.get("high") is not None:
            c["band_from"] = "bench"
            continue
        if library is None:
            c["band_from"] = "unread"
            continue
        sid = str(c.get("sample_id") or "").lower()
        band = certified_band(c["test"], by_id.get(sid) or by_name.get(sid))
        if band is None:
            c["band_from"] = "none"
            continue
        c["low"], c["expected"], c["high"], c["units"] = band
        c["band_from"] = "library"
    return rows


# ── Latest checks ───────────────────────────────────────────────────────────

def _control(series) -> Optional[dict]:
    from ui_wall import _control as control_note   # one rule for the wall and this table
    return control_note(series)


def latest(machines: Optional[List[dict]], *, href: Callable[[str, str], str],
           library: Optional[Iterable[Any]] = None, rows: Optional[List[dict]] = None,
           missing: str = "unread", error: Optional[str] = None) -> dict:
    """The Latest checks table.

    `machines` are the judged, merged machines (``ui_live.judged``), None
    when nothing was read. `library` is the QC library (names a standard
    from the Lab ID a check carries; None: named by the Lab ID). `rows` are
    the QC history rows of LEM's log for the control captions (None: not
    there, and `missing` says why: "unread" or "filling")."""
    if machines is None:
        if error:
            return {"state": "unreadable", "error": str(error)[:240], "rows": None,
                    "pill": None, "count": None, "history": missing, "legend_note": ""}
        return {"state": "not_read", "error": None, "rows": None, "pill": None,
                "count": None, "history": missing, "legend_note": ""}
    by_id, by_name = _index(library)
    by_key = qc_series.series_from_rows(rows) if rows else {}
    out: List[dict] = []
    for m in machines:
        uid = str(m.get("machine_uid") or "")
        title = str(m.get("title") or uid)
        for c in ui_record.checks(m):
            sid = str(c.get("sample_id") or "")
            known = by_id.get(sid.lower()) or by_name.get(sid.lower())
            std = ({"name": known["name"], "lab_id": known["lab_id"] or sid,
                    "href": standard_href(known["name"])} if known else
                   {"name": sid, "lab_id": sid, "href": standard_href(sid)} if sid else None)
            control = None
            if by_key and c["verdict"]["key"] != "out":
                # Said once: "Out of spec" already says the last result
                # failed; a rule broken beside it would say it again. The
                # caption is for the row whose verdict looks fine.
                series = qc_series.current_series(by_key, uid, c["test"])
                if series is not None and series.points:
                    control = _control(series)
            band = (c["low"], c["expected"], c["high"], c["units"])
            if c["low"] is None and c["high"] is None:
                # an assignment the bench has not published a band for: the
                # library still knows what its first run will be judged by
                band = certified_band(c["test"], known) or band
            out.append({
                "uid": uid, "title": title, "href": href(uid, "qc"),
                # how long a pass counts on this instrument; the page says the
                # lab default once, in its legend, and a row only when it differs
                "window": ui_live.qc_window(m)["hours"],
                "test": c["test"], "check": c["title"], "method": c["method"],
                "standard": std,
                "low": band[0], "expected": band[1], "high": band[2], "units": band[3],
                "value": c["value"], "at": c["at"],
                "verdict": c["verdict"], "control": control,
            })
    out.sort(key=lambda r: (rank(r["verdict"]), r["title"].lower(), r["uid"],
                            ui_record._boiling_rank(r["check"])))
    n_out = sum(1 for r in out if r["verdict"]["key"] == "out")
    owed = [r for r in out if r["verdict"]["key"] == "due"]
    n_in = sum(1 for r in out if r["verdict"]["key"] == "in")
    if not out:
        pill = {"glyph": "never", "level": "", "text": "No QC assigned anywhere"}
    elif n_out:
        pill = {"glyph": "error", "level": "error",
                "text": "%d %s out of spec" % (n_out, _plural(n_out, "check", "checks"))}
    elif owed:
        word = ui_live.owed_word(owed)
        pill = {"glyph": "half" if word == ui_live.QC_DUE else "never", "level": "held",
                "text": "%d %s" % (len(owed), "QC due" if word == ui_live.QC_DUE else
                                   _plural(len(owed), "check with no verdict yet", "checks with no verdict yet"))}
    elif n_in == len(out):
        pill = {"glyph": "final", "level": "ok",
                "text": "The 1 check is in spec" if n_in == 1 else "All %d checks in spec" % n_in}
    else:
        nv = len(out) - n_in
        pill = {"glyph": "never", "level": "",
                "text": "%d %s" % (nv, _plural(nv, "check with no verdict yet", "checks with no verdict yet"))}
    n_inst = len({r["uid"] for r in out})
    count = ("%d %s on %d %s" % (len(out), _plural(len(out), "check", "checks"),
                                 n_inst, _plural(n_inst, "instrument", "instruments"))) if out else None
    history = "ok" if rows is not None else missing
    note = ""
    if out and rows is None:
        note = ("The QC history is still being copied, so no control notes are shown yet."
                if missing == "filling" else
                "The QC history could not be read just now, so no control notes are shown; "
                "the verdicts above are unaffected.")
    return {"state": "ready", "error": None, "rows": out, "pill": pill, "count": count,
            "history": history, "legend_note": note, "window": ui_live.QC_WINDOW_HOURS}


# ── certificates ────────────────────────────────────────────────────────────

def _day(d: str) -> str:
    try:
        y, mo, dd = (int(x) for x in str(d)[:10].split("-"))
        return "%d %s %d" % (dd, _MONTHS[mo - 1], y)
    except (ValueError, IndexError):
        return str(d or "")


def _short_day(d: str, today: date) -> str:
    try:
        y, mo, dd = (int(x) for x in str(d)[:10].split("-"))
    except (ValueError, IndexError):
        return str(d or "")
    return "%d %s" % (dd, _MONTHS[mo - 1]) + ("" if y == today.year else " %d" % y)


def certificate_word(certs: Optional[List[Any]], today: date) -> dict:
    """{key, word, glyph, detail}: what the certificate column says.

    `needed` (nothing on file), `expired` (nothing in date), `expiring`
    (the cover runs out within the warning window), `uploaded`. The cover is
    ``covering_certificate``'s, the rule the expiry report uses, so this word
    and the report cannot disagree about one standard."""
    certs = list(certs or [])
    if not certs:
        return {"key": "needed", "word": "Needed", "glyph": "half",
                "detail": "No certificate on file"}
    cover = covering_certificate(certs, today)
    if cover is None:
        newest = max(certs, key=lambda c: str(getattr(c, "expires_at", "") or ""))
        return {"key": "expired", "word": "Expired " + _short_day(newest.expires_at, today),
                "glyph": "half", "detail": "Nothing on file is in date"}
    st = certificate_status(cover.expires_at, today)
    if st == STATUS_EXPIRING:
        days = cover.days_until_expiry(today) if hasattr(cover, "days_until_expiry") else None
        if days is None:
            try:
                days = (date.fromisoformat(str(cover.expires_at)[:10]) - today).days
            except ValueError:
                days = 0
        return {"key": "expiring", "word": "Expires in %d d" % days if days else "Expires today",
                "glyph": "half", "detail": "Valid to " + _day(cover.expires_at)}
    if st == STATUS_EXPIRED:      # unreachable through covering_certificate; kept honest
        return {"key": "expired", "word": "Expired", "glyph": "half", "detail": ""}
    return {"key": "uploaded", "word": "Uploaded", "glyph": "final",
            "detail": ("Valid to " + _day(cover.expires_at)) if cover.expires_at else "No expiry stated"}


# ── the library ─────────────────────────────────────────────────────────────

def _pairs(targets: Optional[Dict[str, Iterable[Any]]]) -> List[Tuple[str, str, str]]:
    """[(uid, sample, test)] from {uid: [WatchedTarget | (sample, test) | dict]}."""
    out = []
    for uid, ts in (targets or {}).items():
        for t in ts or ():
            if isinstance(t, dict):
                s, te = t.get("sample") or t.get("sample_name"), t.get("test") or t.get("test_name")
            elif isinstance(t, (tuple, list)):
                s, te = t[0], t[1]
            else:
                s, te = getattr(t, "sample", ""), getattr(t, "test", "")
            out.append((str(uid), str(s or ""), str(te or "")))
    return out


def standards(library: Optional[Iterable[Any]], targets: Optional[Dict[str, Iterable[Any]]],
              certs: Optional[Dict[str, List[Any]]], *, today: date,
              error: Optional[str] = None) -> dict:
    """The Standards table. `library` None with `error`: it could not be read."""
    if library is None:
        return {"state": "unreadable" if error else "not_read", "error": (str(error)[:240] if error else None),
                "rows": None, "pill": None, "count": None}
    pairs = _pairs(targets)
    rows = []
    for s in library:
        s = _sample(s)
        name = str(s.get("name") or "")
        tests = [t for t in s.get("tests") or [] if isinstance(t, dict)]
        used = {uid for uid, sm, _t in pairs if sm == name}
        titles = [ui_record.short_test(t.get("name") or t.get("value_col") or "")[0] for t in tests]
        rows.append({"name": name, "href": standard_href(name),
                     "lab_id": str(s.get("sample_id_val") or ""),
                     "n_checks": len(tests), "checks": titles,
                     "used_on": len(used),
                     "certificate": (certificate_word((certs or {}).get(name), today)
                                     if certs is not None else
                                     {"key": "unread", "word": "Couldn't read", "glyph": "dashed",
                                      "detail": "The certificates could not be read"})})
    rows.sort(key=lambda r: r["name"].lower())
    need = sum(1 for r in rows if r["certificate"]["key"] in ("needed", "expired"))
    soon = sum(1 for r in rows if r["certificate"]["key"] == "expiring")
    if not rows:
        pill = None
    elif certs is None:
        pill = {"glyph": "dashed", "level": "", "text": "Certificates not read"}
    elif need:
        pill = {"glyph": "half", "level": "held",
                "text": "%d %s a certificate" % (need, _plural(need, "needs", "need"))}
    elif soon:
        pill = {"glyph": "half", "level": "held",
                "text": "%d %s soon" % (soon, _plural(soon, "certificate expires", "certificates expire"))}
    else:
        pill = {"glyph": "final", "level": "ok", "text": "Every certificate on file"}
    n_used = len({uid for uid, sm, _t in pairs if any(sm == r["name"] for r in rows)})
    count = ("%d %s" % (len(rows), _plural(len(rows), "standard", "standards"))
             + (" · used on %d %s" % (n_used, _plural(n_used, "instrument", "instruments")) if n_used else "")) \
        if rows else None
    return {"state": "ready", "error": None, "rows": rows, "pill": pill, "count": count}


def resolve(library: Iterable[Any], key: str) -> Tuple[Optional[Any], Optional[str]]:
    """(the standard named `key`, None) · (None, the name to redirect to,
    when `key` is a Lab ID or the name in another case) · (None, None)."""
    key = str(key or "").strip()
    lib = list(library or [])
    for s in lib:
        if str(_sample(s).get("name") or "") == key:
            return s, None
    for s in lib:
        d = _sample(s)
        if str(d.get("name") or "").lower() == key.lower() or \
                (key and str(d.get("sample_id_val") or "").strip().lower() == key.lower()):
            return None, str(d.get("name") or "")
    return None, None


# ── one standard ────────────────────────────────────────────────────────────

def _window(hours: Any) -> str:
    try:
        h = float(hours or 0)
    except (TypeError, ValueError):
        h = 0.0
    if not h > 0:
        return ui_live.hours_words(ui_live.QC_WINDOW_HOURS) + " (the lab default)"
    return ui_live.hours_words(h)


def _band(t: dict) -> Tuple[float, float, float]:
    e = float(t.get("expected") or 0.0)
    s = float(t.get("std_dev") or 0.0)
    k = float(t.get("k") or 2.0)
    # rounded to the decimals the certificate is typed in, plus two, so
    # 63.7 ± 2·1.05 reads 61.6 – 63.7 – 65.8, not 61.599999999999994
    places = max(_places(e), _places(s)) + 2
    return round(e - k * s, places), e, round(e + k * s, places)


def _places(x: float) -> int:
    s = repr(float(x))
    if "e" in s or "E" in s:
        s = "%.10f" % float(x)
    return len(s.split(".")[1].rstrip("0")) if "." in s else 0


def _verdict_for(m: Optional[dict], test: str, lab_id: str, name: str) -> Tuple[dict, Optional[dict]]:
    """This standard's verdict for one (instrument, test), and the record's
    row it came from. A check the bench last judged against ANOTHER standard
    is not this one's: until it runs on this lot it has no verdict."""
    if m is None:
        return ({"key": "none", "word": "Not in LEM", "glyph": "dashed",
                 "detail": "No instrument with this id is on the floor"}, None)
    want = {lab_id.lower(), name.lower()} - {""}
    for c in ui_record.checks(m):
        if _norm(c["test"]) == _norm(test) and str(c.get("sample_id") or "").lower() in want:
            return c["verdict"], c
    return ui_live.check_verdict(None, m), None


def standard(sample: Any, *, targets: Optional[Dict[str, Iterable[Any]]],
             certs: Optional[List[Any]], machines: Optional[List[dict]], today: date,
             href: Callable[[str, str], str]) -> dict:
    """One standard's record (ia-final §3.5): head, Used on, Certified
    values, Certificate, and whether it may be deleted.

    `targets` None: the assignments could not be read (Used on says so, it
    is not "used nowhere"). `certs` None: the certificates could not be
    read. `machines` None: the snapshot is not read, so instruments are
    named by id and no verdict is claimed."""
    s = _sample(sample)
    name = str(s.get("name") or "")
    lab_id = str(s.get("sample_id_val") or "")
    tests = [t for t in s.get("tests") or [] if isinstance(t, dict)]
    by_test = {_norm(t.get("name")): t for t in tests}
    by_test.update({_norm(t.get("value_col")): t for t in tests if t.get("value_col")})
    by_uid = {str(m.get("machine_uid")): m for m in machines or []}

    values = []
    for t in tests:
        low, exp, high = _band(t)
        title, method = ui_record.short_test(t.get("name") or "")
        units = str(t.get("units") or "")
        values.append({"test": str(t.get("name") or ""), "check": title, "method": method,
                       "low": low, "expected": exp, "high": high,
                       "units": ui_record.UNITS.get(units, units),
                       "std_dev": float(t.get("std_dev") or 0.0), "k": float(t.get("k") or 2.0),
                       "window": _window(t.get("qc_expire_hours")),
                       "window_hours": float(t.get("qc_expire_hours") or 0.0),
                       "used_on": 0})

    used_rows: Optional[List[dict]] = None
    if targets is not None:
        used_rows = []
        for uid, sm, test in _pairs(targets):
            if sm != name:
                continue
            m = by_uid.get(uid) if machines is not None else None
            if machines is None:
                verdict, c = ({"key": "none", "word": "Not read yet", "glyph": "dashed",
                               "detail": "the instruments have not been read"}, None)
            else:
                verdict, c = _verdict_for(m, test, lab_id, name)
            title = str((m or {}).get("title") or uid)
            check, method = ui_record.short_test(test)
            t = by_test.get(_norm(test)) or {}
            low, exp, high = _band(t) if t else (None, None, None)
            units = str(t.get("units") or "")
            used_rows.append({
                "uid": uid, "title": title, "href": href(uid, "qc"),
                "test": test, "check": check, "method": method,
                "verdict": verdict,
                "value": c["value"] if c else None, "at": c["at"] if c else None,
                "low": low, "expected": exp, "high": high,
                "units": ui_record.UNITS.get(units, units),
                "line": "%s · %s · %s" % (title, check, verdict["word"]),
            })
            for v in values:
                if _norm(v["test"]) == _norm(test):
                    v["used_on"] += 1
        used_rows.sort(key=lambda r: (rank(r["verdict"]), r["title"].lower(), r["uid"],
                                      ui_record._boiling_rank(r["check"])))
    n_inst = len({r["uid"] for r in used_rows or []})
    if used_rows is None:
        caption = "Couldn't read which instruments are checked on it"
    elif not used_rows:
        caption = "Not checked on any instrument yet"
    else:
        caption = "%d %s · %d %s" % (n_inst, _plural(n_inst, "instrument", "instruments"),
                                     len(used_rows), _plural(len(used_rows), "check", "checks"))
        if len(used_rows) > 1:
            caption += " · worst first"

    cert = certificate_word(certs, today) if certs is not None else None
    cert_rows = None
    if certs is not None:
        cover = covering_certificate(list(certs), today)
        cert_rows = []
        for c in certs:
            d = c.to_dict() if hasattr(c, "to_dict") else dict(c)
            st = certificate_status(d.get("expires_at"), today)
            d["status"] = st
            d["current"] = cover is not None and d.get("uid") == getattr(cover, "uid", None)
            d["href"] = "/api/qc-standards/certificates/%s/download" % quote(str(d.get("uid") or ""), safe="")
            cert_rows.append(d)

    if cert is not None and cert["key"] in ("needed", "expired"):
        pill = {"glyph": "half", "level": "held",
                "text": "Certificate needed" if cert["key"] == "needed" else "Certificate expired"}
    elif used_rows:
        pill = {"glyph": "final", "level": "ok", "text": "In use on %d" % n_inst}
    elif used_rows is None:
        pill = {"glyph": "dashed", "level": "", "text": "Use not read"}
    else:
        pill = {"glyph": "never", "level": "", "text": "Not in use"}

    blocked = None
    if certs is None or targets is None:
        blocked = "LEM could not read whether it holds a certificate or is in use, so it is not offered."
    elif certs:
        blocked = ("It holds a certificate. Remove the certificate first, or rename the standard "
                   "instead: a rename carries the certificate across.")
    elif used_rows:
        blocked = ("It is checked on %d %s. Take it off %s first (Check it on…), or replace it "
                   "with a new lot." % (n_inst, _plural(n_inst, "instrument", "instruments"),
                                        _plural(n_inst, "it", "them")))
    return {
        "state": "ready", "name": name, "href": standard_href(name),
        "head": {"lab_id": lab_id, "pill": pill, "n_checks": len(tests),
                 "used_on": None if used_rows is None else n_inst},
        "used_on": {"rows": used_rows, "caption": caption},
        "values": values,
        "certificate": cert,
        "certificates": {"rows": cert_rows},
        "delete": {"blocked": blocked},
    }


# ── who reports a test ──────────────────────────────────────────────────────

def reporting(pairs: Iterable[Tuple[str, str]], machines: Optional[List[dict]] = None) -> Dict[str, List[str]]:
    """normalised test -> [uid] that report it: the log's (uid, test) pairs
    plus every check an instrument is already QC'd on (it reports that test
    whether or not its runs reached the log)."""
    out: Dict[str, set] = {}
    for uid, test in pairs or ():
        if uid and test:
            out.setdefault(_norm(test), set()).add(str(uid))
    for m in machines or []:
        uid = str(m.get("machine_uid") or "")
        for sp in m.get("effective_specs") or []:
            if sp.get("test_name"):
                out.setdefault(_norm(sp["test_name"]), set()).add(uid)
        for t in m.get("qc_targets") or []:
            if t.get("test"):
                out.setdefault(_norm(t["test"]), set()).add(uid)
    return {k: sorted(v) for k, v in out.items()}
