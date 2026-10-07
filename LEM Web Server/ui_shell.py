"""What the shell (templates/_layout.html, _shell.html) says, from memory only.

The sidebar foot, the rail badges and the ``.rail-status`` strip have to say
something true on the first paint, before the live feed (``/api/ui/live``,
ui_live.py, static/js/status.js) has answered. The nav counts on first paint
are ``ui_live.nav_meta`` of the same payload the feed then serves. Everything here is read from the in-memory
snapshot and the live road — **0 LabCore ops per page render**. A shell that
asked LabCore anything would turn every open tablet into load on the queue the
snapshot exists to protect (tests/test_ui_shell_pages.py counts the calls).

The rule this codebase is built around applies to the strip too: **a failed
read is never an empty result.** No snapshot yet is "Not read from LabCore
yet", never "0 of 0 benches checking in"; a count whose source is missing is
``None`` and the template draws nothing, never 0.
"""
from __future__ import annotations

from typing import Optional

# The five nav items (ia-final §1): key, label, href, icon. `qc` is resolved
# at render time — see `nav_items`.
NAV = (
    ("instruments", "Instruments", "/"),
    ("checklists", "Checklists", "/checklists"),
    ("qc", "QC", "/quality"),
    ("log", "Log", "/logs"),
    ("settings", "Settings", "/settings"),
)


def nav_items(has_quality: bool) -> list:
    """The nav, with QC pointed where QC lives TODAY.

    `/quality` is QC's home in the new IA but a later piece builds it. Until
    the route exists the item goes to `/qc` (the QC wall, which is what "QC"
    in the old nav meant) rather than to a 404. The day `/quality` is
    registered this flips on its own.
    """
    out = []
    for key, label, href in NAV:
        if key == "qc" and not has_quality:
            href = "/qc"
        out.append({"key": key, "label": label, "href": href})
    return out


def qc_out_of_spec(machines) -> int:
    """Checks whose latest verdict is out of spec and not superseded."""
    n = 0
    for m in machines or []:
        for spec in m.get("effective_specs") or []:
            if spec.get("last_qc_in_spec") is False and not spec.get("last_qc_superseded_by"):
                n += 1
    return n


def shell_status(snap: Optional[dict], merged_machines: Optional[list] = None) -> dict:
    """The shell's words for one page render.

    `snap` is `SnapshotService.get(build_if_missing=False)` — never a build:
    a page must not pay for (or trigger) a LabCore read. `merged_machines` is
    that snapshot's machines with the live road overlaid
    (`live_presence.merge_machines`), so a bench that pushed seconds ago
    counts as checking in even if the queue has not carried it yet.

    -> {record_at, labcore, fleet: {checking, total} | None,
        nav_meta: {key: {text, badge}}}
    """
    snap = snap or {}
    online = snap.get("labcore_online")
    labcore = "unknown" if online is None else ("reachable" if online else "unreachable")
    ready = bool(snap.get("ready"))
    out = {"record_at": (snap.get("built_at") or None) if ready else None,
           "labcore": labcore, "fleet": None, "nav_meta": {}}
    if not ready:
        return out
    machines = merged_machines if merged_machines is not None else (snap.get("machines") or [])
    if machines:
        checking = sum(1 for m in machines if m.get("live") or m.get("module_running"))
        out["fleet"] = {"checking": checking, "total": len(machines)}
    qc = qc_out_of_spec(machines)
    if qc:
        out["nav_meta"]["qc"] = {"text": "%d out of spec" % qc, "badge": str(qc)}
    return out


def fleet_text(fleet: Optional[dict]) -> Optional[str]:
    """Same words as `LEMUi.fleetText` in static/js/ui_logic.js."""
    if not fleet or not fleet.get("total"):
        return None
    total = fleet["total"]
    return "%d of %d %s checking in" % (fleet["checking"], total,
                                        "bench" if total == 1 else "benches")


def record_words(status: dict) -> tuple:
    """(state, text) for the record's age, as the page first paints it.

    The same four states as `LEMUi.recordStatus`; the server says the clock
    time ("Updated 13:02") because it cannot know when the page will be read,
    and shell.js turns that into "Updated 4 s ago" and keeps it ageing. It
    never says "Live": nothing has polled yet when the server renders. Once
    the live feed answers (a second later), status.js replaces these words
    with the feed's own ("Live · updated 4 s ago").
    """
    at = status.get("record_at")
    down = status.get("labcore") == "unreachable"
    if not at:
        return (("error", "LabCore not answering · nothing read yet") if down
                else ("never", "Not read from LabCore yet"))
    hm = str(at)[11:16]
    if down:
        return ("error", "LabCore not answering · record from " + hm)
    return ("fresh", "Updated " + hm)
