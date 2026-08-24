#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
levels.py — the floor, stacked.

Ryan: "I want vertical layers like levels if you will. These 'Levels' can be
renamed, in the UI you can cycle through them. There is in the settings also a
default level. You can move machines up and down a level, you can also create a
level, when you create a machine it will allow you to decide on which layer."

A lab is not flat. The map has always been one plane, so a second-floor
instrument had to be drawn somewhere on the ground plan and everybody looking
at the map had to know it was not really there. A level is that missing axis:
a named plane, ordered, that equipment stands on.

Three facts, three tables of its own, and **not one existing table altered** —
that is deliberate. RELEASING.md §2 makes a new or renamed `lem_*` column a
MAJOR release, because the station module on every bench reads these tables and
would have to move with it. Levels are a floor-plan concern that no bench cares
about, so keeping them in their own tables ships this as a MINOR and no bench
changes.

One existing table is written to, and only written to: `lem_machine_log`, which
gets the move's history line as an ordinary `kind='config'` row (see
`MOVE_LOG_SQL`). No column is added, nothing is renamed, and the `/api/live`
payload is untouched, so a bench that has never heard of levels reads that row
exactly as it reads every other config audit — which is to say, it ignores it.
An INSERT is not a schema change and this stays a MINOR.

    lem_levels           the planes themselves: uid, name, rank.
    lem_machine_level    machine_uid -> level_uid, plus who moved it there and
                         when. One instrument, one level, one row.
    lem_level_settings   floor-wide settings, key/value — the same shape
                         `MapSettingsStore` uses for `locked`, because the
                         default level is the same kind of fact: one switch,
                         shared by everyone looking at the map.

**This module declares its schema; it does not create it.** The DDL below is
written to be pasted into `snapshot_service.SCHEMA_DDL`, and `SNAPSHOT_ARMS`
into `_ARMS`. A store that runs its own `CREATE TABLE IF NOT EXISTS` is the old
pattern, and it is how the floor went down once already: every arm of the
batched machine read shares ONE statement, so a table LabCore has not got fails
the entire read and drops the whole floor to the fallback path.

Until that wiring lands the three tables do not exist, so every read here names
a missing table — the one error a read may honestly turn into "nothing" (see
`_rows`), and exactly the truth about the lab today. **Nothing else degrades
to empty.**

**"Declared but inert" and "working" look identical from the outside**, which
is why the wiring has a gate rather than a paragraph:
`tests/test_levels.py::TestTheWiringIsNotDoneYet` holds a tripwire that fails
the day somebody pastes the DDL in (delete it then — that is the point) and a
`strict` xfail that fails the day the feature is live and nobody removed the
marker. The silence is over either way round.

These are all NEW tables, so `SCHEMA_MIGRATIONS` has nothing to say about them:
`CREATE TABLE IF NOT EXISTS` is enough for a table nobody has yet. The moment a
column is added to one of them *after this ships*, it needs an ALTER in
`SCHEMA_MIGRATIONS` as well, because CREATE-IF-NOT-EXISTS is a no-op on a table
that already exists and the missing column then fails the whole batched read.
That is what `correction` did to `lem_machine_specs`.

Wiring it up is three edits and they go together: the DDL, the arms, **and the
`*_from_tables` parsers**. Arms without parsers are a table nobody reads from
the snapshot, so the floor keeps paying three LabCore reads a poll for data it
already has in memory.

Two states this module is built around, neither of them an edge case:

* **No levels at all.** That is the live floor right now and it may last for
  months. Everything must draw exactly as it does today.
* **A level deleted with equipment on it.** LabCore has no foreign keys, so
  nothing stops it and nothing tidies up after it. The instruments fall back to
  the ground floor; they never fall off the map.

## The default level is a VIEW setting. It is not a placement.

Ryan asked for "in the settings also a default level", so a settings page will
carry it as one drop-down. Two different facts wanted that one name, and an
earlier cut of this file let them share it:

    default_level_uid()    what the level picker OPENS on, and what the
                           create-equipment dialog PRESELECTS. A preference.
    unplaced_level_uid()   where an instrument nobody has placed is DRAWN.
                           The ground floor, always.

Sharing them meant the settings drop-down decided where every unassigned
instrument appeared. Flipping it from Ground to Second moved the entire fleet
up a floor for everyone looking at the map — with `SELECT COUNT(*) FROM
lem_machine_level` still zero. Nothing was moved, nothing was recorded, no
instrument could be put back on its own, and nobody could tell from the map
that anything had happened. Deleting the default level did it again, in the
other direction.

So `placements` does not read the setting and **cannot be handed it**: it takes
no default argument at all. That is stronger than passing the ground in
explicitly, and on purpose — the bug arrived as a helpful-looking fourth
argument, and the fix that leaves the argument in place leaves the door open
for the next caller. The fallback is `ground_level_uid`, which is derived from
the ladder itself and does not move when a preference does.

### …and it is one value for the whole lab, which IS a departure

Desk- and room-booking products keep "default floor" as a **per-user**
preference, and that is the right shape for them: everybody has their own desk,
on their own floor. Ryan asked for the other thing — "in the settings also a
default level" — and his instruction outranks the comparison, but the departure
is worth defending rather than drifting into.

A lab has one floor plan, and the screen that matters most is the wall display.
`/floor` takes no login at all — `authed()` gates writes, not the map — so
there is no user to hang a preference on. "Per user" would really mean "per
browser": a hidden value nobody can inspect, that the wall display and the
phone in your pocket disagree about, and that no settings page could ever set
for the lab. One shared row, changed in settings, visible to everyone, is the
honest shape here. It is also why it must stay a VIEW default: a shared
preference that moved equipment would move it for everybody at once.

## What did LabCore tell me? — one rule, imported

This module used to answer that question itself, and answered it wrong: `_rows`
turned every failure into `[]`. The rule now lives in `labcore_result` and is
imported, not restated — see `_rows`, `_write` and `_try_write`, which are the
only three places here that touch the gateway.

## A move leaves a record

Every other mutation in this app is traceable and a change of floor was not.
An instrument that moved and nobody can say when or who is the same gap the
corrective-action work is closing elsewhere, so a placement now carries
`moved_at` / `moved_by` **in the same row as the placement itself** — one
write, so provenance cannot drift from position — and a `lem_machine_log` line
puts the move in the machine's history beside its runs, QC verdicts and config
audit. The log line is best-effort; the row is not. See `_place`.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence

# One answer to "what did LabCore actually tell me?", for the whole app. This
# module is one of the three that each invented its own and got it wrong.
from labcore_result import (LabCoreError, LabCoreRefused, LabCoreUnavailable,
                            confirm_write)
from labcore_result import rows as labcore_rows

# `rank` counts UP from the ground: rank 0 is the lowest plane, and "up one
# level" means the next rank. The UI is free to draw the list the other way
# round; the storage order is the one people say out loud ("level 1, level 2").
LEVELS_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_levels ("
    "uid TEXT PRIMARY KEY, name TEXT NOT NULL, rank INTEGER NOT NULL DEFAULT 0)"
)

# `machine_uid` and nothing else names the instrument. It is the wire contract:
# benches key their lem_* rows on it and POST /api/live with it, and with no
# foreign keys a rename would not error — it would silently orphan every row
# here forever. The machine->equipment rename is display-facing and stops at the
# template.
#
# `moved_at` / `moved_by` ship in the FIRST version of this table on purpose.
# They are the provenance of the placement, and they are in the placement's own
# row so that one write records both where the instrument is and how it got
# there — two rows could disagree, and the one that lost would be the audit.
# Adding them later would also cost an ALTER in `SCHEMA_MIGRATIONS`, because
# CREATE-IF-NOT-EXISTS is a no-op on a table that already exists and the missing
# column then fails the whole batched read. That is what `correction` did to
# `lem_machine_specs`.
LEVEL_ASSIGNMENT_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_machine_level ("
    "machine_uid TEXT PRIMARY KEY, level_uid TEXT NOT NULL, "
    "moved_at TEXT, moved_by TEXT)"
)

LEVEL_SETTINGS_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_level_settings ("
    "key TEXT PRIMARY KEY, value TEXT)"
)

# Paste into snapshot_service.SCHEMA_DDL. The "IF NOT EXISTS <name> (" spelling
# is load-bearing: `ensure_schema` pulls the table name out by splitting on it,
# and a DDL written any other way is issued on every start instead of being
# skipped — ten wasted writes into a queue that serialises at ~1.5 ops/sec.
SCHEMA_DDL = (LEVELS_DDL, LEVEL_ASSIGNMENT_DDL, LEVEL_SETTINGS_DDL)

# Paste into snapshot_service._ARMS. The same width as every arm already there,
# every column aliased — both rules are from that file and both were learned the
# hard way: a differing column count fails the whole UNION, and an unaliased arm
# looks fine in the batch (names come from the first arm) while coming back
# nameless in the fallback path, where each arm runs on its own. The width is
# asserted against `snapshot_service._ARMS` rather than against a number typed
# in a test, so widening those arms fails here instead of in the lab.
#
# These arms are only half of it: `levels_from_tables` and its two siblings,
# below, are what turn the rows back into facts. Arms without a parser leave the
# store as the only working API — three LabCore reads per floor poll, every two
# seconds, which is the load snapshot_service exists to remove.
SNAPSHOT_ARMS = (
    ("level",
     "SELECT 'level' AS src, uid AS c1, name AS c2, CAST(rank AS TEXT) AS c3, "
     "'' AS c4, '' AS c5, '' AS c6, '' AS c7, '' AS c8, '' AS c9 "
     "FROM lem_levels"),
    # c3/c4 carry the provenance so "moved 3 days ago, by Ryan" costs the floor
    # nothing: the batched read has already fetched it. Asking per instrument on
    # a floor that redraws every 2s is the N+1 the snapshot exists to end.
    ("levelof",
     "SELECT 'levelof' AS src, machine_uid AS c1, level_uid AS c2, "
     "COALESCE(moved_at, '') AS c3, COALESCE(moved_by, '') AS c4, "
     "'' AS c5, '' AS c6, '' AS c7, '' AS c8, '' AS c9 "
     "FROM lem_machine_level"),
    ("levelset",
     "SELECT 'levelset' AS src, key AS c1, value AS c2, '' AS c3, '' AS c4, "
     "'' AS c5, '' AS c6, '' AS c7, '' AS c8, '' AS c9 "
     "FROM lem_level_settings"),
)

# The floor-wide default, held the way `locked` is: one key, one row.
DEFAULT_LEVEL_KEY = "default_level"

# ── the move, in the machine's own history ───────────────────────────────────
#
# `lem_machine_log` is an EXISTING table, already declared in
# snapshot_service.SCHEMA_DDL, and this module only ever INSERTs into it: no new
# column, no ALTER, nothing a bench has to learn — so levels stay a MINOR
# release (RELEASING.md §2). It is also not created here; a store that runs its
# own DDL is the pattern that dropped the floor.
#
# The shape is web_app's `_audit` verbatim — kind='config', the action in
# `test_name`, a JSON `detail` carrying `action` and `by` — so the logs page,
# its kind filter and the machine's history render this with no new case.
LEVEL_MOVE_ACTION = "level_move"

MOVE_LOG_SQL = (
    "INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, test_name, "
    "value, detail) VALUES (?, ?, 'config', '', ?, '', ?)"
)


@dataclass(frozen=True)
class Level:
    """One plane of the lab. `uid` is the identity; `name` and `rank` are both
    things a person changes on a Tuesday afternoon."""

    uid: str
    name: str
    rank: int = 0

    def to_dict(self) -> dict:
        return {"uid": self.uid, "name": self.name, "rank": self.rank}


@dataclass(frozen=True)
class Placement:
    """One instrument's placement AND how it got there.

    Kept together because they are written together: `moved_at`/`moved_by` are
    columns of the placement row, not a second record that could survive the
    move failing or contradict it afterwards.

    Both stamps may be empty — a row written before this shipped, or by hand.
    An unstamped placement is still a placement, so nothing here refuses to draw
    it; it simply has nothing to say about who put it there.
    """

    machine_uid: str
    level_uid: str
    moved_at: str = ""
    moved_by: str = ""

    def to_dict(self) -> dict:
        return {"machine_uid": self.machine_uid, "level_uid": self.level_uid,
                "moved_at": self.moved_at, "moved_by": self.moved_by}


def _sort_key(level: Level):
    """Rank, then name, then uid.

    Rank is a **sort hint, not an identity**: nothing in LabCore stops two rows
    sharing one, and two clients creating a level in the same second is all it
    takes. Sorting on rank alone would then leave the order up to whatever the
    rows came back in — and the floor relayouts every two seconds, which is
    "everytime this thing refreshes it changes layout" all over again. The
    tie-break costs nothing and makes every read agree.
    """
    return (level.rank, level.name.lower(), level.uid)


def ground_level_uid(levels: Sequence[Level]) -> str:
    """The bottom of the ladder — and **the only fallback a placement uses.**

    Derived from the ladder, so it is stable under a settings change: the one
    thing that moves it is somebody creating a level below the current ground,
    which is a real change to the building and shows up as one.

    Sorted, not `levels[0]`: LabCore returns rows in whatever order it likes,
    and a basement created after the ground floor comes back second.
    """
    ordered = sorted(levels, key=_sort_key)
    return ordered[0].uid if ordered else ""


def resolve_default(levels: Sequence[Level], stored_uid: str) -> str:
    """Resolve the floor-wide **view** default: what the picker opens on and
    what the create-equipment dialog preselects.

    The stored uid if it still names a level; otherwise the ground floor;
    otherwise nothing, because there are no levels. Resolving on *read* is what
    keeps a pointer to a deleted level — which no foreign key will ever clean
    up — from opening the picker on nothing.

    **Never pass this to `placements`.** It is a preference, and a preference
    that decides where equipment is drawn moves the whole fleet when one person
    changes it; see the module docstring. `placements` will not accept it.

    Idempotent, so a caller may pass back either the raw setting or an answer
    this already gave.
    """
    ordered = sorted(levels, key=_sort_key)
    if not ordered:
        return ""
    uid = str(stored_uid or "").strip()
    if any(level.uid == uid for level in ordered):
        return uid
    return ordered[0].uid


def placements(machine_uids: Iterable[str], assignments: Dict[str, str],
               levels: Sequence[Level]) -> Dict[str, str]:
    """Where every instrument in the fleet stands: machine_uid -> level_uid.

    Pure on purpose — the snapshot already holds the rows, and asking LabCore
    again per instrument is the N+1 the whole snapshot exists to prevent.

    The one rule that matters: **every instrument gets a place.** An assignment
    to a level that has been deleted, no assignment at all, and a lab with no
    levels whatsoever all resolve to something the floor can draw. The empty
    string is a real answer and means the floor is flat, not that the
    instrument is missing.

    **Three arguments, and no fourth.** There is deliberately no way to hand
    this the floor-wide default: an unplaced instrument stands on the ground,
    full stop. The signature is the guarantee — a settings drop-down cannot
    silently relocate a fleet through an argument that does not exist.
    """
    known = {level.uid for level in levels}
    ground = ground_level_uid(levels)
    placed: Dict[str, str] = {}
    for machine_uid in machine_uids:
        uid = str(assignments.get(machine_uid) or "").strip()
        placed[machine_uid] = uid if uid in known else ground
    return placed


def machines_on(level_uid: str, placed: Dict[str, str]) -> List[str]:
    """The instruments drawn on one level, in the order the fleet was given.

    A blank `level_uid` means every instrument, which is both the "all levels"
    view and the answer for a lab that has no levels — the case where filtering
    at all would empty the map.
    """
    uid = str(level_uid or "").strip()
    if not uid:
        return list(placed)
    return [machine for machine, on in placed.items() if on == uid]


def cycle(level_uid: str, levels: Sequence[Level], delta: int = 1) -> str:
    """The UI's "cycle through them" — and it WRAPS, where moving equipment
    clamps. Pressing past the top floor as a viewer should show the ground
    again; pressing past the top floor with an instrument selected must not
    drop it into the basement. Same ladder, two different gestures.

    A uid that is no longer there — someone deleted the level the viewer was
    looking at — lands on the ground rather than on nothing.

    **A stepper is an addition, never the whole control.** An earlier draft of
    this docstring defended it by citing a captured comparison of two named
    floor-plan products — a document that does not exist anywhere in this repo.
    Restated without the borrowed authority, because the reasoning stands on
    its own and an invented citation is worse than none:

    A next/previous stepper costs one press per level, so it scales with the
    number of levels and a building with twenty floors makes it useless. A
    labelled dropdown costs an open and a pick no matter how tall the building,
    which is why every floor picker built for towers is one. Ryan asked for
    cycling in those words — "in the UI you can cycle through them" — for a lab
    of about three levels, where one press beats open-and-pick.

    So the UI carries BOTH: the stepper for the two- or three-level lab this is
    being built for, and a picker labelled with the current level's name beside
    it, which is the control that still works when a lab has eight. Shipping
    the stepper alone would be trading a control that always works for one that
    only works while the building stays small.
    """
    ordered = sorted(levels, key=_sort_key)
    if not ordered:
        return ""
    uids = [level.uid for level in ordered]
    uid = str(level_uid or "").strip()
    if uid not in uids:
        return uids[0]
    return uids[(uids.index(uid) + int(delta)) % len(uids)]


# ── the snapshot arms, parsed back ───────────────────────────────────────────
#
# `SNAPSHOT_ARMS` without these is decoration. Arms alone leave the store as the
# only working API, and the store costs THREE LabCore reads — ladder,
# assignments, settings — on every floor poll. The floor polls every 2s, which is
# the exact N+1-by-another-name that snapshot_service was built to end, and the
# zero-op rule is one of the gates this feature has to pass. With a parser, the
# rows the batched read already fetched become the answer and the poll costs
# nothing extra.
#
# Named `*_from_tables` to match `schedule_from_tables`, `beats_from_tables` and
# the rest, and they take the same `split_batched` output, so a caller cannot
# tell these tables apart from the ones already shipping.

def _f(row: dict, key: str, default=""):
    """snapshot_service's own row reader, spelled the same way: SQL NULL is not
    the empty string until somebody makes it one.

    **Copied rather than imported, and the reason matters** in a module that
    argues everywhere else against two readings of one value. The wiring this
    file specifies puts levels' DDL and arms INTO `snapshot_service`; a
    top-level `from snapshot_service import _f` here would make that dependency
    run both ways the moment somebody wires it by import instead of by paste,
    and a circular import fails at start-up, which on a `.pyw` under pythonw is
    a server that dies with no console to say why.

    A copy is only safe with a gate, so there is one:
    `TestOneReadingOfOneValue` asserts these two agree on NULL, "" and 0. If
    snapshot_service ever changes how it reads a row, that test fails here
    rather than the floor and the settings page quietly disagreeing.
    """
    value = row.get(key)
    return default if value is None else value


def _now_stamp() -> str:
    """One clock for every record this module writes.

    Seconds precision, matching web_app's `_audit`, because these rows sit in
    `lem_machine_log` next to it and a history that mixes precisions reads as
    two different systems writing.
    """
    return datetime.now().isoformat(timespec="seconds")


def _as_rank(value) -> int:
    """One reading of `rank`, shared by the store and the snapshot parser.

    Both paths are live at once — the store writes and the snapshot draws — and
    a rank that means 2 down one path and 0 down the other is a level that sits
    in a different place depending on which page you opened. The arm CASTs to
    TEXT (a UNION column has one type across every arm), so this has to take
    "2" as readily as 2.

    Anything unreadable is the ground rather than an exception: rows get edited
    by hand, and a level with a strange rank belongs at the bottom of the
    ladder, not off the map.
    """
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def levels_from_tables(tables: Dict[str, List[dict]]) -> List[Level]:
    """Snapshot rows → the ladder, ground first — same order `LevelStore.levels`
    returns, because the floor and the settings page must agree."""
    out = [Level(str(_f(r, "c1")), str(_f(r, "c2")), _as_rank(_f(r, "c3", 0)))
           for r in tables.get("level") or [] if _f(r, "c1")]
    return sorted(out, key=_sort_key)


def assignments_from_tables(tables: Dict[str, List[dict]]) -> Dict[str, str]:
    """Snapshot rows → machine_uid -> level_uid, for the whole fleet at once.

    Deliberately thin: this is what `placements` takes, and drawing the floor
    has no business knowing who moved anything. The provenance is one call away
    in `moves_from_tables`, out of the same rows.
    """
    return {str(_f(r, "c1")).strip(): str(_f(r, "c2")).strip()
            for r in tables.get("levelof") or [] if str(_f(r, "c1")).strip()}


def moves_from_tables(tables: Dict[str, List[dict]]) -> Dict[str, Placement]:
    """Snapshot rows → machine_uid -> `Placement`, provenance included.

    The same `levelof` rows `assignments_from_tables` reads, so "when was this
    moved, and by whom" costs the floor no LabCore op at all. A per-instrument
    read for that on a floor that redraws every two seconds is the N+1 the
    snapshot service exists to end.
    """
    out: Dict[str, Placement] = {}
    for row in tables.get("levelof") or []:
        machine_uid = str(_f(row, "c1")).strip()
        if not machine_uid:
            continue
        out[machine_uid] = Placement(machine_uid,
                                     str(_f(row, "c2")).strip(),
                                     str(_f(row, "c3")).strip(),
                                     str(_f(row, "c4")).strip())
    return out


def default_level_from_tables(tables: Dict[str, List[dict]]) -> str:
    """Snapshot rows → the RAW stored default, exactly as `stored_default_uid`
    gives it: unresolved, possibly naming a level that is gone.

    Matched on the key rather than taken from the first row — `lem_level_settings`
    is a key/value table and will hold a second switch before long, at which
    point "the first row" becomes "whichever was inserted first".

    Callers wanting something they can open a picker on pass this through
    `resolve_default`. Callers wanting to draw an unplaced instrument do not
    call this at all; they call `ground_level_uid`.
    """
    for row in tables.get("levelset") or []:
        if str(_f(row, "c1")).strip() == DEFAULT_LEVEL_KEY:
            return str(_f(row, "c2")).strip()
    return ""


class LevelStore:
    """Owns `lem_levels`, `lem_machine_level` and `lem_level_settings`.

    No `ensure_schema`: the tables are declared once, centrally, by
    `snapshot_service` (see SCHEMA_DDL above). Until that wiring lands every
    table here is missing, and a missing table is the one failure a read may
    honestly report as "nothing" — which is the lab's state today.

    Every other failure raises. `labcore_result` is the rule and it is imported,
    not restated: three modules wrote their own version of it in one week and
    all three were wrong.
    """

    def __init__(self, gateway) -> None:
        self.gateway = gateway

    # ── LabCore plumbing: three methods, one rule ────────────────────
    def _rows(self, sql: str, args: Optional[list] = None, *,
              missing_ok: bool = True) -> List[dict]:
        """Read, and never turn "could not ask" into "there is nothing".

        This method used to swallow everything and answer `[]`, and every
        consequence was a confident lie told to somebody standing in the lab: a
        routine 8s read timeout — the one `snapshot_service.READ_TIMEOUT`
        documents as ORDINARY, because reads queue behind every write in the
        lab — emptied the level picker, drew ZERO instruments on the level the
        operator was looking at, and made `assign` announce "That level is no
        longer there" about a level sitting safely in LabCore.

        `missing_ok` is the one exemption, and it is decided per CALLER rather
        than blanket, because the two kinds of caller want opposite things:

          * a path that DRAWS passes it (the default). No such table means the
            central DDL has not run, which today is simply true, and the floor
            has always drawn fine with no levels at all.
          * a path that is about to WRITE passes `missing_ok=False`. It is
            about to validate a name or a uid against what it read and then
            write; reading `[]` from a table that was never created makes every
            name unique and every level absent, and the news it swallows —
            "the schema is missing" — is the only thing anyone could act on.

        A gateway that RAISES (the real client, on a socket error) is mapped
        into the same vocabulary rather than given a second one, so a caller has
        exactly one exception family to catch.
        """
        try:
            res = self.gateway.read_sql(sql, args or [])
        except Exception as exc:
            raise LabCoreUnavailable(
                "LabCore could not be read: {}".format(exc)) from exc
        return [r for r in labcore_rows(res, missing_ok=missing_ok)
                if isinstance(r, dict)]

    def _write(self, sql: str, args: Optional[list] = None) -> None:
        """One write, and it counts only if LabCore says it happened.

        `confirm_write` states that positively on purpose. The old test — no
        "error" key, so it worked — passes for `None`, for `{}`, and for the
        refusal LabCore's queue sends when it is past 100 pending, all of which
        mean the row was never written. The queue serialises at roughly 1.5
        ops/sec and refuses by ANSWERING, so this is the busy-Tuesday path, not
        the exotic one.

        A gateway that RAISES becomes `LabCoreRefused` rather than
        `LabCoreUnavailable`, and the choice is about consequence, not cause.
        Cause-wise it is a blip. But `LabCoreUnavailable` invites "try again",
        and `create` mints a fresh uid every time — a caller that retried an
        unacknowledged create would end up with two levels of the same name,
        the one thing `create` exists to refuse. Unacknowledged is
        unacknowledged; whichever way it failed, nobody may report the row as
        written.
        """
        try:
            res = self.gateway.sql(sql, args or [])
        except Exception as exc:
            raise LabCoreRefused(
                "LabCore did not take the write: {}".format(exc)) from exc
        confirm_write(res)

    def _try_write(self, sql: str, args: Optional[list] = None) -> bool:
        """A write whose failure is not worth the operator's attention.

        Exactly two callers, and both say why at the call site: the renumber
        pass (`_renumber`) and the history line (`_record_move`). Everything
        else uses `_write`, so silence has to be asked for by name rather than
        being what happens when nobody checks.
        """
        try:
            self._write(sql, args)
            return True
        except LabCoreError:
            return False

    # ── the ladder ───────────────────────────────────────────────────
    def _ladder(self, *, missing_ok: bool) -> List[Level]:
        out = [Level(str(row.get("uid") or ""), str(row.get("name") or ""),
                     _as_rank(row.get("rank")))
               for row in self._rows("SELECT uid, name, rank FROM lem_levels",
                                     missing_ok=missing_ok)]
        return sorted(out, key=_sort_key)

    def levels(self) -> List[Level]:
        """Every level, ground first. One read.

        The DRAWING read: a table that does not exist yet is no levels, which
        is what the floor already handles. A blip raises.
        """
        return self._ladder(missing_ok=True)

    def _ladder_for_write(self) -> List[Level]:
        """The same ladder, read by something that is about to write against it.

        A missing table raises here. Validating a new name, or a target uid,
        against rows that were never created is how a write path invents an
        answer — and the answer it invents ("that level is not there", "that
        name is free") is indistinguishable from a fact.
        """
        return self._ladder(missing_ok=False)

    def get(self, uid: str) -> Optional[Level]:
        for level in self.levels():
            if level.uid == uid:
                return level
        return None

    def create(self, name: str, rank: Optional[int] = None) -> Level:
        """Add a plane. With no `rank` it goes on top, which is how a building
        grows; with one it is inserted at that position and everything above it
        shuffles up."""
        name = str(name or "").strip()
        if not name:
            raise ValueError("A level needs a name.")
        ladder = self._ladder_for_write()
        if any(level.name.lower() == name.lower() for level in ladder):
            raise ValueError(f"There is already a level called {name}.")
        index = len(ladder) if rank is None else max(0, min(int(rank), len(ladder)))
        level = Level(uuid.uuid4().hex[:12], name, index)
        self._write("INSERT INTO lem_levels (uid, name, rank) VALUES (?, ?, ?)",
                    [level.uid, level.name, level.rank])
        self._renumber(ladder[:index] + [level] + ladder[index:], skip=level.uid)
        return level

    def rename(self, uid: str, name: str) -> Level:
        """Ryan: "These 'Levels' can be renamed". The uid never moves, so a
        rename cannot orphan the equipment standing on it — which, with no
        foreign keys, is what re-creating a level under a new name would do."""
        name = str(name or "").strip()
        if not name:
            raise ValueError("A level needs a name.")
        ladder = self._ladder_for_write()
        current = next((l for l in ladder if l.uid == uid), None)
        if current is None:
            raise ValueError("That level is no longer there.")
        if any(l.uid != uid and l.name.lower() == name.lower() for l in ladder):
            raise ValueError(f"There is already a level called {name}.")
        self._write("UPDATE lem_levels SET name = ? WHERE uid = ?", [name, uid])
        return Level(current.uid, name, current.rank)

    def delete(self, uid: str) -> None:
        """Drop a plane, and everything that pointed at it.

        The assignments go in ONE delete, not one per instrument, and they go
        rather than being repointed at the ground: repointing would record a
        move nobody made. With the rows gone, `placements` puts the equipment
        on the ground and it stays visible.

        **The level row goes FIRST, and the order is the whole point.** This is
        the only operation here whose two writes must BOTH land to be right —
        `_place`'s second write is a history line that can be lost without
        costing anybody a fact — and the queue refuses past 100 pending by
        returning an error dict, so half of it landing is an ordinary Tuesday.
        The two orders fail very differently:

          assignments first — the placements are gone permanently and the level
            is still on the map. Nothing is recoverable: the operator presses
            delete again, it appears to do nothing new, and every instrument
            that stood on that level has silently lost where it was.
          level first — the level is off the map, which is what was asked for.
            The assignment rows dangle, and a dangling assignment is already a
            case this module handles: `placements` draws those instruments on
            the ground. The leftover rows are cleared by pressing delete again,
            which is exactly what an operator does after an error.

        So the surviving state is the *correct* one either way, and the damage
        is a few rows that the same button cleans up.

        Deleting the same level twice is not an error worth showing anybody —
        two screens, two people, one button.
        """
        self._write("DELETE FROM lem_levels WHERE uid = ?", [uid])
        self._write("DELETE FROM lem_machine_level WHERE level_uid = ?", [uid])
        self._clear_default_if_it_named(uid)

    def _clear_default_if_it_named(self, uid: str) -> None:
        """Tidy the settings pointer, and **never fail the delete over it.**

        This is the only cosmetic write in the operation: a default naming a
        level that is gone is resolved on read anyway (`resolve_default`), so
        nobody can see the difference. The comment said exactly that and the
        code then raised, which told an operator their delete had failed when
        both real deletes had already landed — and the level really was gone,
        so pressing delete again did nothing they could see either.

        Both halves are deliberately quiet, and for the same reason: the READ
        can blip (in which case we do not know whether the default named this
        level, and a stale pointer costs nothing), and the WRITE can be refused
        by a full queue. Neither is worth undoing an operator's delete.
        """
        try:
            if self.stored_default_uid() != uid:
                return
        except LabCoreError:
            return
        self._try_write("DELETE FROM lem_level_settings WHERE key = ?",
                        [DEFAULT_LEVEL_KEY])

    def _renumber(self, order: Sequence[Level], skip: str = "") -> None:
        """Rewrite ranks to match a new order. **Best-effort, and bounded by the
        number of levels** — a lab has floors, not thousands of them.

        Deliberately not raised on: if the queue refuses halfway, two levels
        share a rank, `_sort_key`'s tie-break still gives one stable order on
        every read, and the next successful insert renumbers them apart. The
        alternative — failing the whole create because a cosmetic shuffle was
        queued behind someone else's work — costs the operator the level they
        actually asked for.
        """
        for index, level in enumerate(order):
            if level.uid == skip or level.rank == index:
                continue
            self._try_write("UPDATE lem_levels SET rank = ? WHERE uid = ?",
                            [index, level.uid])

    # ── the floor-wide default ───────────────────────────────────────
    def stored_default_uid(self) -> str:
        """What settings actually holds — which may name a level that is gone."""
        rows = self._rows(
            "SELECT value FROM lem_level_settings WHERE key = ?",
            [DEFAULT_LEVEL_KEY])
        return str(rows[0].get("value") or "").strip() if rows else ""

    def default_level_uid(self) -> str:
        """The **view** default: what the picker opens on and what the
        create-equipment dialog preselects. Resolved, so a setting left
        pointing at a deleted level still opens on something.

        This does NOT say where unplaced equipment is drawn — see
        `unplaced_level_uid`, and the module docstring for why they were split.
        """
        return resolve_default(self.levels(), self.stored_default_uid())

    def unplaced_level_uid(self) -> str:
        """Where an instrument nobody has placed is drawn: the ground floor.

        Separate from `default_level_uid` on purpose and permanently. This one
        is derived from the ladder, so changing the settings drop-down — or
        deleting the level it named — moves no equipment at all.
        """
        return ground_level_uid(self.levels())

    def set_default_level(self, uid: str) -> None:
        """Set the floor-wide VIEW default. Blank clears it and the picker
        opens on the ground floor instead.

        Anything else has to name a real level: a default pointing at nothing
        is a setting that looks configured while behaving as though it is not.

        Note what this deliberately does NOT do: move anything. Not one row in
        `lem_machine_level` changes, and no instrument on the map appears
        anywhere new — that separation is the point of `unplaced_level_uid`.
        """
        uid = str(uid or "").strip()
        if not uid:
            self._write("DELETE FROM lem_level_settings WHERE key = ?",
                        [DEFAULT_LEVEL_KEY])
            return
        # A write path, so the ladder is read strictly: "that level is no longer
        # there" must be a fact about the levels, never about the connection.
        if not any(level.uid == uid for level in self._ladder_for_write()):
            raise ValueError("That level is no longer there.")
        self._write(
            "INSERT INTO lem_level_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            [DEFAULT_LEVEL_KEY, uid])

    # ── which level each instrument stands on ────────────────────────
    def assignments(self) -> Dict[str, str]:
        """Every equipment->level assignment on the floor, in ONE read.

        The floor payload is rebuilt for the whole fleet every poll, and the
        floor polls every 2s. One read per instrument here would be paid for as
        long as this feature exists.
        """
        out: Dict[str, str] = {}
        for row in self._rows(
                "SELECT machine_uid, level_uid FROM lem_machine_level"):
            machine_uid = str(row.get("machine_uid") or "").strip()
            if machine_uid:
                out[machine_uid] = str(row.get("level_uid") or "").strip()
        return out

    def _level_uid_of(self, machine_uid: str, *, missing_ok: bool) -> str:
        rows = self._rows(
            "SELECT level_uid FROM lem_machine_level WHERE machine_uid = ?",
            [machine_uid], missing_ok=missing_ok)
        return str(rows[0].get("level_uid") or "").strip() if rows else ""

    def level_of(self, machine_uid: str) -> str:
        """Which level one instrument stands on — the DRAWING read."""
        return self._level_uid_of(machine_uid, missing_ok=True)

    def placement_of(self, machine_uid: str) -> Optional[Placement]:
        """The placement AND who put it there, for one instrument.

        The same fact `moves_from_tables` gets free out of the snapshot, but
        reachable before the wiring lands — otherwise the provenance would
        exist only down a path that is currently connected to nothing, and an
        equipment panel could record a move it could never show.

        One instrument, on demand: never a loop over the fleet. That is what
        the snapshot parser is for.
        """
        rows = self._rows(
            "SELECT machine_uid, level_uid, moved_at, moved_by "
            "FROM lem_machine_level WHERE machine_uid = ?", [machine_uid])
        if not rows:
            return None
        row = rows[0]
        return Placement(str(row.get("machine_uid") or ""),
                         str(row.get("level_uid") or "").strip(),
                         str(row.get("moved_at") or ""),
                         str(row.get("moved_by") or ""))

    def assign(self, machine_uid: str, level_uid: str, by: str = "") -> None:
        """Stand one instrument on one level. Blank puts it back to unassigned,
        which is not "nowhere" — `placements` draws it on the ground.

        `by` is whoever asked for it, and it is a plain default rather than a
        required argument: a caller with no session (a script, a page that
        never asked) still gets a dated record, and an empty author is honest
        where an invented one is not.

        Two reads: the ladder, to refuse a uid that names no level, and the
        instrument's current level, so the history line can say what it moved
        FROM. Both are paid on an explicit operator action — a person picked a
        level from a menu — never on the floor's poll, which places the whole
        fleet out of the snapshot without touching this class.
        """
        level_uid = str(level_uid or "").strip()
        if not level_uid:
            self.unassign(machine_uid, by=by)
            return
        ladder = self._ladder_for_write()
        if not any(level.uid == level_uid for level in ladder):
            raise ValueError("That level is no longer there.")
        previous = self._level_uid_of(machine_uid, missing_ok=False)
        if previous == level_uid:
            # Standing where it already stands is not a move, and writing it as
            # one costs the real record: `_place`'s upsert re-stamps moved_at and
            # moved_by, so whoever actually moved the instrument is replaced by
            # whoever last pressed Save, and the log gains a line reading
            # "from X to X". No LabCore failure is needed to reach this — an
            # ordinary equipment-edit form that re-posts every field does it.
            return
        self._place(machine_uid, level_uid, previous, ladder, by)

    def _place(self, machine_uid: str, level_uid: str, previous: str,
               ladder: Sequence[Level], by: str) -> None:
        """Write the placement, then record the move.

        **One statement carries the placement AND its provenance**, because two
        statements can half-land: the queue refuses past 100 pending by
        answering, and a row saying where an instrument is with a stamp saying
        it was moved last March by somebody else is worse than no stamp at all.

        The history line is the second write and is deliberately best-effort —
        `_try_write`, not `_write`. web_app's own audit states the rule ("an
        audit failure must not fail the change the operator actually asked
        for"), and here it costs nothing that is not already recorded: the
        provenance is on the row, in LabCore, whatever the log did.
        """
        at = _now_stamp()
        self._write(
            "INSERT INTO lem_machine_level (machine_uid, level_uid, moved_at, "
            "moved_by) VALUES (?, ?, ?, ?) ON CONFLICT(machine_uid) DO UPDATE "
            "SET level_uid=excluded.level_uid, moved_at=excluded.moved_at, "
            "moved_by=excluded.moved_by",
            [machine_uid, level_uid, at, str(by or "")])
        self._record_move(machine_uid, previous, level_uid, ladder, by, at)

    def _record_move(self, machine_uid: str, previous: str, level_uid: str,
                     ladder: Sequence[Level], by: str, at: str) -> None:
        """One line in the machine's own history, beside its runs and QC.

        The NAMES go in as well as the uids. Levels get renamed and deleted,
        and "moved to 4f2c91ab" a year later is not a record of anything — the
        log has to say what was true when it was written, because nothing will
        be able to resolve it afterwards.
        """
        names = {level.uid: level.name for level in ladder}
        detail = json.dumps({
            "action": LEVEL_MOVE_ACTION,
            "by": str(by or ""),
            "from": previous,
            "to": level_uid,
            "from_name": names.get(previous, ""),
            "to_name": names.get(level_uid, ""),
        })
        self._try_write(MOVE_LOG_SQL, [machine_uid, at, LEVEL_MOVE_ACTION,
                                       detail])

    def unassign(self, machine_uid: str, by: str = "") -> None:
        """Take an instrument off its level. It does not leave the map — with
        no assignment `placements` draws it on the ground.

        This is a placement change a person made, so it is recorded like one.
        The one extra read that costs (the level it was on, for the history
        line) is paid on an explicit operator action, never on a poll — the
        same split the station module makes for `_reevaluate_and_show`.

        That read is the tolerant one on purpose: nothing is validated against
        it. A missing table means there was no placement to name, and the
        DELETE immediately below reports the missing table itself.
        """
        previous = self._level_uid_of(machine_uid, missing_ok=True)
        self._write("DELETE FROM lem_machine_level WHERE machine_uid = ?",
                    [machine_uid])
        if previous:
            # Everything past this point is the RECEIPT, not the operator's
            # change, and the ladder read it needs happens after the delete has
            # already landed. A blip there must not be reported as the unassign
            # failing — the instrument is off its level either way, and telling
            # the operator otherwise invites them to do it again. Same rule as
            # `_record_move`'s best-effort log write, extended to cover the read
            # that feeds it; `delete()` above already works this way.
            try:
                self._record_move(machine_uid, previous, "", self.levels(), by,
                                  _now_stamp())
            except LabCoreError:
                pass

    def forget(self, machine_uid: str) -> None:
        """The name `MachineLayoutStore` and `QcTargetStore` already use for the
        cleanup a deleted machine triggers, so the delete path does not have to
        learn a fourth verb.

        **No history line, and that is the difference from `unassign`.** The
        machine itself is being deleted, and web_app deletes its `lem_machine_log`
        rows in the same breath — so the line would be written into a history
        that is about to be dropped, at the cost of two more ops in a queue that
        serialises at ~1.5/s during a delete that is already several writes long.
        """
        self._write("DELETE FROM lem_machine_level WHERE machine_uid = ?",
                    [machine_uid])

    # ── up and down ──────────────────────────────────────────────────
    def move(self, machine_uid: str, delta: int, by: str = "") -> str:
        """Move one instrument `delta` levels and return where it ended up.

        **Clamped at both ends.** Up from the top floor stays on the top floor;
        a wrap would look, to the person holding the mouse, exactly like the
        instrument falling into the basement.

        An unassigned instrument moves from the level it is already being drawn
        on — **the ground**, the same answer `placements` gives — because "one
        up from nowhere" has no answer. Starting from the settings default
        instead is the same bug the module docstring describes wearing a
        different hat: the operator sees the instrument on Ground, presses up
        once, and it lands two floors above where they were looking because
        somebody changed a preference last week.

        A move that changes nothing writes nothing: an operator leaning on the
        button does not fill the write queue, and an unassigned instrument that
        could not move is left unassigned rather than quietly pinned to today's
        ground, which is a change nobody asked for.

        **Two reads and one write, and it used to be three reads.** The third
        was `assign` re-reading the ladder to re-validate a uid this method had
        just taken OUT of that ladder — a round-trip whose only possible answer
        was the one already in hand, on the operation an operator repeats most.
        Writing through `_place` keeps the validation where the uid comes from
        the caller (`assign`) and skips it where it cannot be wrong (here).
        `TestMovingIsCheap` is the bound; the two cheap read paths already had
        one and this one did not.
        """
        ladder = self._ladder_for_write()
        if not ladder:
            return ""
        uids = [level.uid for level in ladder]
        stored = self._level_uid_of(machine_uid, missing_ok=False)
        current = stored if stored in uids else ground_level_uid(ladder)
        index = uids.index(current)
        target = max(0, min(index + int(delta), len(uids) - 1))
        if target != index:
            self._place(machine_uid, uids[target], stored, ladder, by)
        return uids[target]

    def move_up(self, machine_uid: str, by: str = "") -> str:
        return self.move(machine_uid, 1, by)

    def move_down(self, machine_uid: str, by: str = "") -> str:
        return self.move(machine_uid, -1, by)

