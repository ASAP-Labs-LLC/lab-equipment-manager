#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
machine_map.py — the per-instrument record behind the floor map.

Two things V4 kept on every box and this version keeps too, only now in
LabCore so the map, the station modules, and every viewer agree:

    position        where the instrument stands on the lab floor.
    watched targets which QC sample + test it is checked against — V4's
                    `WatchedTarget(sample, test)`.

Assigning targets is the explicit form of QC: "this OptiMPP is checked by
Cloud CRM's Cloud Point." A machine with no targets falls back to the
station module's own detection against the shared sample library.

EVERY ANSWER FROM THE GATEWAY IS READ (2026-08-25)
--------------------------------------------------
This store used to ignore what LabCore said back. Nine `gateway.sql(...)`
calls threw their answer away, and every read decided for itself that an
error meant "empty".

That is not a stylistic gap, it is a silent data-loss bug, because LabCore's
write queue serialises at roughly 1.5 writes a second and **refuses past ~100
pending by ANSWERING** rather than raising. The refusal this lab has actually
recorded (notes.md; lem_station_module.py:495) is

    {"error": "LabCore is busy…", "busy": true, "retry_after": n}

an error DICT returned normally. `labcore_result` is what decides — it refuses
on any positive failure signal and never re-derives the shape here. Dropped on
the floor, a refusal means:

  * a dragged instrument that snaps back to where it was on the next refresh,
    with "saved" already on screen — the map that "keeps rearranging itself";
  * a QC target assignment that never lands, so the lab believes an instrument
    is being checked against a standard and it is not. QC here is
    assignment-only (see CLAUDE.md, 2026-08-03): no assignment means no
    checking at all, silently;
  * a map lock that never takes, so a layout the lab froze goes on moving.

So: every write goes through `confirm_write`, every read through `rows`, and
each read path states in a comment whether a missing table may honestly mean
empty. The rule is in labcore_result.py, tested once, rather than re-derived
here and re-derived wrong.

AND CONFIRMING IS NOT ENOUGH ON ITS OWN (2026-08-25)
-----------------------------------------------------
`QcTargetStore.assign` was "DELETE the whole set, then INSERT the new one" with
every statement confirmed — and a refusal in between still left an instrument
assigned to NOTHING, because the queue takes one statement at a time and there
is no transaction across them. That loss is the quiet kind: no QC assigned is a
legitimate state, drawn in grey, and nobody investigates grey.

`assign` now upserts what is wanted and prunes what is not, last and in one
statement, exactly as `db_config_store._rewrite_rows` does. A refusal leaves a
superset — an extra target that is checked, shows on the floor, and is cleared
by saving again — never an empty set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from labcore_result import (
    LabCoreError,
    LabCoreRefused,
    LabCoreUnavailable,
    confirm_write,
    rows,
)

LAYOUT_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_machine_layout ("
    "machine_uid TEXT PRIMARY KEY, pos_x REAL, pos_y REAL)"
)

TARGETS_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_machine_targets ("
    "machine_uid TEXT NOT NULL, sample_name TEXT NOT NULL, "
    "test_name TEXT NOT NULL, "
    "PRIMARY KEY (machine_uid, sample_name, test_name))"
)


SETTINGS_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_map_settings ("
    "key TEXT PRIMARY KEY, value TEXT)"
)


class MachineMapError(LabCoreError):
    """The floor map's record could not be trusted — one name for the routes.

    A route that just wants an HTTP status for "the map store could not do
    that" catches this. It subclasses `LabCoreError`, so a caller handling
    every store the same way still catches it, and the two children below keep
    the distinction `labcore_result` exists to preserve: a refusal is "LabCore
    said no", unavailability is "LabCore could not be asked", and only a route
    that can tell them apart can honestly offer "try again".
    """


class MapWriteRefused(MachineMapError, LabCoreRefused):
    """A position, a lock, or a QC assignment never reached LabCore.

    Raised in place of the old silence. The caller must not report "saved".
    """


class MapReadUnavailable(MachineMapError, LabCoreUnavailable):
    """The map's record could not be read, so nothing about it is known.

    Explicitly NOT "the map is empty". Reporting an unarranged floor, or "no
    QC assigned", when the truth is a LabCore blip is the failure this whole
    conversion exists to stop.
    """


def _confirm(res, what: str) -> None:
    """Confirm one write, or raise saying in plain words which one was lost.

    The message matters: a refusal from the queue carries no hint of what was
    being written, and "LabCore did not acknowledge the write" on its own tells
    an operator nothing about which drag or which assignment to redo.
    """
    try:
        confirm_write(res)
    except LabCoreRefused as exc:
        raise MapWriteRefused("{0} — not saved: {1}".format(what, exc)) from exc


def _write(gateway, sql: str, args=None, *, what: str) -> None:
    """Issue one write and confirm it, converting BOTH ways it can fail.

    The nine call sites here used to read `_confirm(gateway.sql(...), what)`,
    which converts the ANSWER but leaves the CALL bare: a socket error or a
    client that raises is a write that equally did not happen, and it escaped
    as a raw OSError past every `except MapWriteRefused` in web_app and landed
    as a bare 500. "Internal Server Error" does not tell an operator whether
    their drag was saved. checklists/lab_schedule/maintenance_store already had
    `_write` helpers doing exactly this; this brings the map stores in line.
    """
    try:
        res = gateway.sql(sql, args or [])
    except Exception as exc:                       # transport, not logic
        raise MapWriteRefused(
            "{0} — not saved: LabCore could not be written to ({1}: "
            "{2})".format(what, type(exc).__name__, exc)) from exc
    _confirm(res, what)


def _read(res, what: str, *, missing_ok: bool):
    """Rows from a read, or a raise saying which read could not be answered."""
    try:
        return rows(res, missing_ok=missing_ok)
    except LabCoreUnavailable as exc:
        raise MapReadUnavailable("{0}: {1}".format(what, exc)) from exc


class MapSettingsStore:
    """Floor-wide map settings. `locked` is V4's map_locked: one switch,
    shared by everyone looking at the map, so a layout can be frozen once
    the lab is happy with it."""

    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self._ready = False

    def ensure_schema(self) -> None:
        # `_ready` is set only once the CREATE is ACKNOWLEDGED. It used to be
        # set unconditionally, so a boot while the write queue was full left
        # this store believing its table existed for the whole life of the
        # process, and every lock written afterwards went nowhere against a
        # table that was never made. Raising here also means a caller retries
        # on its next request instead of failing forever.
        if not self._ready:
            _write(self.gateway, SETTINGS_DDL, what="creating lem_map_settings")
            self._ready = True

    def locked(self) -> bool:
        # NO `ensure_schema()` (2026-08-25). Every open floor screen polls this
        # every two seconds; declaring a schema from it meant a full WRITE
        # queue pushed one refused CREATE per screen per two seconds into the
        # queue that was already full, and the answer degraded to "locked" —
        # the map froze itself because the queue was busy.
        # missing_ok: a table nobody has ever written to is a map nobody has
        # ever locked, and unlocked is the honest default (V4's too).
        # Every OTHER error raises, because THIS READ DECIDES A WRITE:
        # api_machine_position asks it before saving a drag. Degrading to
        # False during a blip would silently unlock a map the lab deliberately
        # froze and let the floor be rearranged underneath them.
        found = _read(
            self.gateway.read_sql(
                "SELECT value FROM lem_map_settings WHERE key = 'locked'"),
            "reading the map lock", missing_ok=True)
        return bool(found) and str(found[0].get("value")) == "1"

    def set_locked(self, locked: bool) -> None:
        self.ensure_schema()
        _write(self.gateway,
               "INSERT INTO lem_map_settings (key, value) VALUES ('locked', ?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               ["1" if locked else "0"],
               what="{0} the map".format("locking" if locked else "unlocking"))


@dataclass(frozen=True)
class WatchedTarget:
    """One QC check assigned to an instrument: a sample and one of its tests."""

    sample: str
    test: str

    def to_dict(self) -> dict:
        return {"sample": self.sample, "test": self.test}

    @classmethod
    def from_dict(cls, data: dict) -> "WatchedTarget":
        return cls(str(data.get("sample", "")).strip(),
                   str(data.get("test", "")).strip())


class MachineLayoutStore:
    """Where each instrument stands on the floor."""

    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self._ready = False

    def ensure_schema(self) -> None:
        # Confirmed before `_ready`, for the reason in MapSettingsStore.
        # WRITES ONLY — `positions()` no longer calls it.
        if not self._ready:
            _write(self.gateway, LAYOUT_DDL,
                   what="creating lem_machine_layout")
            self._ready = True

    def save_position(self, machine_uid: str, x: float, y: float) -> None:
        self.ensure_schema()
        # A dropped position write is the floor silently refusing to remember a
        # drag: the instrument snaps back on the next 2s refresh while the page
        # has already said "saved". The operator drags it again, and again.
        _write(self.gateway,
               "INSERT INTO lem_machine_layout (machine_uid, pos_x, pos_y) "
               "VALUES (?, ?, ?) ON CONFLICT(machine_uid) DO UPDATE SET "
               "pos_x=excluded.pos_x, pos_y=excluded.pos_y",
               [machine_uid, float(x), float(y)],
               what="moving {0} on the floor".format(machine_uid))

    def forget(self, machine_uid: str) -> None:
        # rows_affected 0 is fine and confirm_write allows it: forgetting a
        # machine that had no saved position DID happen, it just matched
        # nothing. What must not pass is a refusal, which would leave a retired
        # instrument's bay claimed on a floor that says it was cleared.
        self.ensure_schema()
        _write(self.gateway,
               "DELETE FROM lem_machine_layout WHERE machine_uid = ?",
               [machine_uid],
               what="clearing the floor position of {0}".format(machine_uid))

    def positions(self) -> Dict[str, Tuple[float, float]]:
        # No `ensure_schema()` — see MapSettingsStore.locked().
        # missing_ok: no layout table means nobody has arranged the floor yet,
        # and the painter's own bay algorithm covers that honestly. Any other
        # error raises: silently reporting "no saved positions" during a blip
        # is how every instrument jumps to a computed bay at once, which reads
        # to the lab as the map rearranging itself.
        found = _read(
            self.gateway.read_sql(
                "SELECT machine_uid, pos_x, pos_y FROM lem_machine_layout"),
            "reading the floor layout", missing_ok=True)
        out = {}
        for row in found:
            try:
                out[str(row.get("machine_uid"))] = (float(row.get("pos_x")),
                                                    float(row.get("pos_y")))
            except (TypeError, ValueError):
                # Kept deliberately: one unparseable row is a bad row, not a
                # bad answer. Dropping the whole floor because a single pos_x
                # is NULL would turn a one-instrument defect into a blank map.
                continue
        return out


class QcTargetStore:
    """Which QC sample + test each instrument is checked against."""

    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self._ready = False

    def ensure_schema(self) -> None:
        # Confirmed before `_ready`, for the reason in MapSettingsStore.
        # WRITES ONLY — `targets()` and `all()` no longer call it.
        if not self._ready:
            _write(self.gateway, TARGETS_DDL,
                   what="creating lem_machine_targets")
            self._ready = True

    def assign(self, machine_uid: str, targets: List[WatchedTarget]) -> None:
        """Make this machine's assignment set exactly `targets`.

        THE MOST DANGEROUS WRITE IN THE FILE, AND THE ORDER IS THE FIX
        (2026-08-25). It used to be "DELETE this machine's whole set, then
        INSERT the new one", with no transaction across the statements because
        the queue takes one at a time. Every statement was confirmed — but
        confirmation only makes the loss LOUD, and the loss here is the quiet
        kind: QC is assignment-only (CLAUDE.md, 2026-08-03), so an instrument
        with no rows is not visibly broken. The floor says "No QC assigned" in
        grey, which is a legitimate state nobody investigates, and the bench
        keeps running with nothing checking it. A test asserted that empty set
        as correct; see `test_a_refused_insert_leaves_the_old_assignment_
        standing`.

        So, exactly as `db_config_store._rewrite_rows` does:

          1. upsert every wanted target, each confirmed. A refusal here leaves
             the old assignments plus whatever landed — a superset, visible on
             the floor, and corrected by repeating the save.
          2. only then prune what is no longer wanted, in ONE statement. A
             refusal here leaves a stale extra target, which is checked, shows
             up, and is likewise fixed by saving again.

        Neither failure can leave an instrument checked against nothing. The
        prune is still a real prune — `test_an_accepted_assign_still_removes_
        what_was_dropped` holds that half, so this cannot decay into "never
        remove anything".
        """
        self.ensure_schema()
        keep = []
        seen = set()
        for target in targets:
            if not target.sample.strip() or not target.test.strip():
                continue
            key = (target.sample.strip(), target.test.strip())
            if key in seen:
                continue
            seen.add(key)
            keep.append(key)
            # ON CONFLICT DO NOTHING, not a plain INSERT: with the DELETE moved
            # to the end, re-saving a set that already holds this pair is the
            # NORMAL case, and a primary-key collision answered as an error
            # would fail an assignment that is already correct.
            _write(self.gateway,
                   "INSERT INTO lem_machine_targets (machine_uid, sample_name, "
                   "test_name) VALUES (?, ?, ?) ON CONFLICT(machine_uid, "
                   "sample_name, test_name) DO NOTHING",
                   [machine_uid, key[0], key[1]],
                   what="assigning {0} / {1} to {2} (its previous assignment "
                        "is still in force and the set must be re-applied)"
                        .format(key[0], key[1], machine_uid))
        if not keep:
            # Clearing is a deliberate act — the accident above was clearing on
            # the way to refilling.
            _write(self.gateway,
                   "DELETE FROM lem_machine_targets WHERE machine_uid = ?",
                   [machine_uid],
                   what="clearing the QC assignment of {0}".format(machine_uid))
            return
        holes = " AND ".join(
            "NOT (sample_name = ? AND test_name = ?)" for _ in keep)
        args = [machine_uid]
        for sample, test in keep:
            args.extend((sample, test))
        _write(self.gateway,
               "DELETE FROM lem_machine_targets WHERE machine_uid = ? AND "
               + holes, args,
               what="removing the QC targets {0} is no longer checked against "
                    "(the new ones are assigned; a stale extra target may "
                    "remain until this is saved again)".format(machine_uid))

    def targets(self, machine_uid: str) -> List[WatchedTarget]:
        # No `ensure_schema()` — see MapSettingsStore.locked().
        # missing_ok: no table means nothing has ever been assigned anywhere,
        # which is a real state on a fresh LabCore and reads as "No QC
        # assigned" — the honest grey. Every other error raises: "no QC
        # assigned" is a VERDICT shown on the floor, and showing it because a
        # read timed out tells the lab an instrument is unchecked when it may
        # be checked and failing.
        found = _read(
            self.gateway.read_sql(
                "SELECT sample_name, test_name FROM lem_machine_targets "
                "WHERE machine_uid = ? ORDER BY sample_name, test_name",
                [machine_uid]),
            "reading the QC assignment of {0}".format(machine_uid),
            missing_ok=True)
        return [WatchedTarget(str(r.get("sample_name") or ""),
                              str(r.get("test_name") or ""))
                for r in found]

    def all(self) -> Dict[str, List[WatchedTarget]]:
        # No `ensure_schema()` — see MapSettingsStore.locked(). The one caller
        # that DECIDES A WRITE from this (`qc_samples.changeover`) declares the
        # schema itself before it starts, so the missing-table case cannot
        # reach it silently.
        # missing_ok=False, and it is the only read here that refuses to
        # degrade: THIS READ DECIDES WRITES. qc_samples.changeover() walks it
        # to move every machine off a retired QC lot onto the new one, and an
        # empty answer means "no machine was checked against the old lot" — it
        # would report "0 moved", leave every instrument pointed at a lot that
        # no longer exists, and stop QC across the lab, which is the exact
        # failure changeover() was written to prevent. A missing table stays
        # loud here too: on a LabCore with no lem_machine_targets at all, a
        # changeover reporting "0 moved" would be technically true and read as
        # "everything is fine", which is the answer this store must never give.
        found = _read(
            self.gateway.read_sql(
                "SELECT machine_uid, sample_name, test_name FROM "
                "lem_machine_targets ORDER BY machine_uid, sample_name, "
                "test_name"),
            "reading every QC assignment", missing_ok=False)
        out: Dict[str, List[WatchedTarget]] = {}
        for row in found:
            out.setdefault(str(row.get("machine_uid")), []).append(
                WatchedTarget(str(row.get("sample_name") or ""),
                              str(row.get("test_name") or "")))
        return out

    def forget(self, machine_uid: str) -> None:
        # As with the layout: 0 rows is an acknowledgement, a refusal is not.
        # A surviving assignment row for a retired machine would be picked up
        # again by changeover() and by the floor's own reads.
        self.ensure_schema()
        _write(self.gateway,
               "DELETE FROM lem_machine_targets WHERE machine_uid = ?",
               [machine_uid],
               what="clearing the QC assignment of {0}".format(machine_uid))
