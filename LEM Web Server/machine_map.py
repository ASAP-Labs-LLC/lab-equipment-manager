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
"""

from __future__ import annotations

from dataclasses import dataclass
from labcore_gateway import check_write
from typing import Dict, List, Tuple

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


class MapSettingsStore:
    """Floor-wide map settings. `locked` is V4's map_locked: one switch,
    shared by everyone looking at the map, so a layout can be frozen once
    the lab is happy with it."""

    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self._ready = False

    def ensure_schema(self) -> None:
        if not self._ready:
            self.gateway.sql(SETTINGS_DDL)
            self._ready = True

    def locked(self) -> bool:
        self.ensure_schema()
        res = self.gateway.read_sql(
            "SELECT value FROM lem_map_settings WHERE key = 'locked'")
        if res.get("error"):
            return False
        rows = res.get("rows") or []
        return bool(rows) and str(rows[0].get("value")) == "1"

    def set_locked(self, locked: bool) -> None:
        self.ensure_schema()
        self.gateway.sql(
            "INSERT INTO lem_map_settings (key, value) VALUES ('locked', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ["1" if locked else "0"])


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
        if not self._ready:
            self.gateway.sql(LAYOUT_DDL)
            self._ready = True

    def save_position(self, machine_uid: str, x: float, y: float) -> None:
        self.ensure_schema()
        self.gateway.sql(
            "INSERT INTO lem_machine_layout (machine_uid, pos_x, pos_y) "
            "VALUES (?, ?, ?) ON CONFLICT(machine_uid) DO UPDATE SET "
            "pos_x=excluded.pos_x, pos_y=excluded.pos_y",
            [machine_uid, float(x), float(y)])

    def forget(self, machine_uid: str) -> None:
        self.ensure_schema()
        self.gateway.sql("DELETE FROM lem_machine_layout WHERE machine_uid = ?",
                         [machine_uid])

    def positions(self) -> Dict[str, Tuple[float, float]]:
        self.ensure_schema()
        res = self.gateway.read_sql(
            "SELECT machine_uid, pos_x, pos_y FROM lem_machine_layout")
        if res.get("error"):
            return {}
        out = {}
        for row in res.get("rows") or []:
            try:
                out[str(row.get("machine_uid"))] = (float(row.get("pos_x")),
                                                    float(row.get("pos_y")))
            except (TypeError, ValueError):
                continue
        return out


class QcTargetStore:
    """Which QC sample + test each instrument is checked against."""

    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self._ready = False

    def ensure_schema(self) -> None:
        if not self._ready:
            self.gateway.sql(TARGETS_DDL)
            self._ready = True

    def assign(self, machine_uid: str, targets: List[WatchedTarget]) -> None:
        """Replace this machine's whole assignment set.

        Guarded, unlike the layout and lock stores in this same file, because
        this one decides WHAT GETS QC-JUDGED. An assignment silently dropped
        stops QC on that instrument entirely and looks exactly like an
        instrument that simply has not run a control lately — the failure
        `changeover` exists to prevent, arrived at the other way round.

        A replace is a DELETE then N INSERTs with no transaction across them,
        so a refusal partway leaves FEWER assignments than either the old set
        or the new one. That is worth saying out loud rather than smoothing
        over; the caller is told the set is now incomplete.
        """
        self.ensure_schema()
        check_write(
            self.gateway.sql(
                "DELETE FROM lem_machine_targets WHERE machine_uid = ?",
                [machine_uid]),
            what="the QC assignments were not changed")
        seen = set()
        for target in targets:
            if not target.sample.strip() or not target.test.strip():
                continue
            key = (target.sample.strip(), target.test.strip())
            if key in seen:
                continue
            seen.add(key)
            check_write(
                self.gateway.sql(
                    "INSERT INTO lem_machine_targets (machine_uid, "
                    "sample_name, test_name) VALUES (?, ?, ?)",
                    [machine_uid, key[0], key[1]]),
                what="the old QC assignments were cleared but not all of the "
                     "new ones were stored — this instrument is now assigned "
                     "fewer QC checks than intended",
                partial=True, landed=sorted(f"{s} · {t}" for s, t in seen
                                            if (s, t) != key),
                not_landed=[f"{key[0]} · {key[1]}"])

    def targets(self, machine_uid: str) -> List[WatchedTarget]:
        self.ensure_schema()
        res = self.gateway.read_sql(
            "SELECT sample_name, test_name FROM lem_machine_targets "
            "WHERE machine_uid = ? ORDER BY sample_name, test_name",
            [machine_uid])
        if res.get("error"):
            return []
        return [WatchedTarget(str(r.get("sample_name") or ""),
                              str(r.get("test_name") or ""))
                for r in res.get("rows") or []]

    def all(self) -> Dict[str, List[WatchedTarget]]:
        self.ensure_schema()
        res = self.gateway.read_sql(
            "SELECT machine_uid, sample_name, test_name FROM "
            "lem_machine_targets ORDER BY machine_uid, sample_name, test_name")
        if res.get("error"):
            return {}
        out: Dict[str, List[WatchedTarget]] = {}
        for row in res.get("rows") or []:
            out.setdefault(str(row.get("machine_uid")), []).append(
                WatchedTarget(str(row.get("sample_name") or ""),
                              str(row.get("test_name") or "")))
        return out

    def forget(self, machine_uid: str) -> None:
        self.ensure_schema()
        self.gateway.sql("DELETE FROM lem_machine_targets WHERE machine_uid = ?",
                         [machine_uid])
