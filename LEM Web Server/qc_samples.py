#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qc_samples.py — the QC-sample system carried forward from the old LEM.

V4 modelled QC as *samples*, not per-machine rules: a named standard (CRM)
with a Lab ID and a list of test specs —

    SampleSpec("Cloud CRM", sample_id_val="CP",
               tests=[SampleTestSpec("Cloud - D7689", value_col="Cloud Point",
                                     expected=-7.4, std_dev=2.8, k=1.0)])

That model is the right one and it survives here, stored centrally in
LabCore's `lem_qc_samples`. Define a standard once; every machine that runs
it is checked against it.

The station modules pull this library and **detect QC themselves**: when a
parsed print's Lab ID matches a QC sample, the parser runs QC on whichever
of that sample's tests it extracted. No per-machine QC wiring.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from labcore_gateway import LabCoreRefused, check_write
from typing import Dict, List, Optional, Tuple

QC_SAMPLES_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_qc_samples ("
    "name TEXT PRIMARY KEY, sample_id_val TEXT, tests TEXT)"
)


@dataclass
class QcSampleTest:
    """One test on a QC standard: pass when expected ± k·std_dev.

    `name` is what the lab calls the check (V4's test name, e.g.
    "Cloud - D7689"); `value_col` is the measurement it reads — a LabCore
    test method in the new world, a CSV column in V4. Modules match on
    either, so V4 definitions keep working.
    """

    name: str
    value_col: str = ""
    expected: float = 0.0
    std_dev: float = 0.0
    k: float = 2.0
    units: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "value_col": self.value_col,
                "expected": self.expected, "std_dev": self.std_dev,
                "k": self.k, "units": self.units}

    @classmethod
    def from_dict(cls, data: dict) -> "QcSampleTest":
        return cls(
            name=str(data.get("name", "")),
            value_col=str(data.get("value_col", "") or data.get("name", "")),
            expected=float(data.get("expected") or 0.0),
            std_dev=float(data.get("std_dev") or 0.0),
            k=float(data.get("k") or 2.0),
            units=str(data.get("units", "")),
        )

    def limits(self) -> Tuple[float, float]:
        margin = self.k * self.std_dev
        return self.expected - margin, self.expected + margin


@dataclass
class QcSample:
    """A named QC standard and the tests it certifies."""

    name: str
    sample_id_val: str
    tests: List[QcSampleTest] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"name": self.name, "sample_id_val": self.sample_id_val,
                "tests": [t.to_dict() for t in self.tests]}

    @classmethod
    def from_dict(cls, data: dict) -> "QcSample":
        return cls(
            name=str(data.get("name", "")),
            sample_id_val=str(data.get("sample_id_val", "")),
            tests=[QcSampleTest.from_dict(t) for t in data.get("tests", [])],
        )

    def limits(self, test_name: str) -> Optional[Tuple[float, float]]:
        for test in self.tests:
            if test.name == test_name:
                return test.limits()
        return None


class QcSampleStore:
    """Owns `lem_qc_samples` — the library every station module reads."""

    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self._schema_ready = False

    def ensure_schema(self) -> None:
        if not self._schema_ready:
            self.gateway.sql(QC_SAMPLES_DDL)
            self._schema_ready = True

    def save(self, sample: QcSample) -> None:
        if not sample.name.strip():
            raise ValueError("A QC sample needs a name.")
        if not sample.sample_id_val.strip():
            raise ValueError("A QC sample needs the Lab ID it runs under.")
        for test in sample.tests:
            if not test.name.strip():
                raise ValueError("Every QC test needs a name.")
            if test.std_dev < 0:
                raise ValueError("Standard deviation cannot be negative.")
            if test.k <= 0:
                raise ValueError("k must be greater than zero.")
        self.ensure_schema()
        # The standards library every module reads to know what a control is
        # meant to read. A lot saved that did not land means the lab is checking
        # against the previous lot's values without anyone being told.
        check_write(
            self.gateway.sql(
                "INSERT INTO lem_qc_samples (name, sample_id_val, tests) "
                "VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                "sample_id_val=excluded.sample_id_val, tests=excluded.tests",
                [sample.name.strip(), sample.sample_id_val.strip(),
                 json.dumps([t.to_dict() for t in sample.tests])],
            ),
            what=f"the QC standard “{sample.name.strip()}” was not saved")

    def delete(self, name: str) -> None:
        self.ensure_schema()
        check_write(
            self.gateway.sql("DELETE FROM lem_qc_samples WHERE name = ?",
                             [name]),
            what=f"the QC standard “{name}” was not removed")

    def list_samples(self) -> List[QcSample]:
        self.ensure_schema()
        res = self.gateway.read_sql(
            "SELECT name, sample_id_val, tests FROM lem_qc_samples ORDER BY name")
        if res.get("error"):
            return []
        samples = []
        for row in res.get("rows") or []:
            try:
                tests = json.loads(row.get("tests") or "[]")
            except (TypeError, ValueError):
                tests = []
            samples.append(QcSample(
                name=str(row.get("name") or ""),
                sample_id_val=str(row.get("sample_id_val") or ""),
                tests=[QcSampleTest.from_dict(t) for t in tests],
            ))
        return samples

    def as_payload(self) -> List[dict]:
        """JSON shape the station modules pull."""
        return [s.to_dict() for s in self.list_samples()]

    def by_lab_id(self) -> Dict[str, QcSample]:
        return {s.sample_id_val.strip().lower(): s
                for s in self.list_samples() if s.sample_id_val.strip()}


def changeover(gateway, old_name: str, new_name: str, new_id_val: str,
               retire_old: bool = False) -> int:
    """Turn a QC standard over to a new lot — V4's Changeover QC.

    The new lot inherits every test spec from the old one, and every
    machine checked against the old lot is moved to the new one. Without
    that reassignment a lot change silently stops QC across the lab, which
    is exactly the failure this exists to prevent.

    Returns how many machines were moved. The old lot is kept by default so
    its history still reads; pass retire_old to remove it.
    """
    from machine_map import QcTargetStore, WatchedTarget

    old_name = (old_name or "").strip()
    new_name = (new_name or "").strip()
    new_id_val = (new_id_val or "").strip()
    if not new_name:
        raise ValueError("The new lot needs a name.")
    if not new_id_val:
        raise ValueError("The new lot needs the Lab ID it runs under.")

    store = QcSampleStore(gateway)
    samples = {s.name: s for s in store.list_samples()}
    if old_name not in samples:
        raise ValueError(f"QC sample '{old_name}' not found.")
    if new_name in samples:
        raise ValueError(f"QC sample '{new_name}' already exists.")

    old = samples[old_name]
    store.save(QcSample(name=new_name, sample_id_val=new_id_val,
                        tests=[QcSampleTest.from_dict(t.to_dict())
                               for t in old.tests]))

    targets = QcTargetStore(gateway)
    moved = 0
    for uid, assigned in targets.all().items():
        if not any(t.sample == old_name for t in assigned):
            continue
        try:
            targets.assign(uid, [WatchedTarget(new_name, t.test)
                                 if t.sample == old_name else t
                                 for t in assigned])
        except LabCoreRefused as exc:
            # The new lot is already in the library and `moved` instruments have
            # already been reassigned — there is no transaction across queue
            # operations to undo either. A changeover that stops here and says
            # nothing is the precise failure this function exists to prevent,
            # arrived at from the inside: the instruments still pointing at the
            # old lot quietly stop being QC-judged, and look exactly like ones
            # that simply have not run a control lately.
            #
            # Re-raised rather than swallowed, carrying the count, so the
            # supervisor is told how far it got and which instrument to look at.
            # Re-running the changeover is safe: the already-moved ones no
            # longer match `old_name` and are skipped.
            raise LabCoreRefused(
                exc.result,
                what=f"the new lot “{new_name}” was created and {moved} "
                     f"instrument(s) were moved to it, but “{uid}” was not — "
                     f"the rest are still assigned to “{old_name}” and are no "
                     f"longer being QC-checked against a current lot. Re-run "
                     f"the changeover to finish it",
                partial=True, moved=moved,
                landed=[f"the new lot “{new_name}”"],
                not_landed=[f"{uid} and any instrument after it"]) from exc
        moved += 1

    if retire_old:
        # Last, and deliberately so: the old lot is what every un-moved
        # instrument is still pointing at, so retiring it before the moves are
        # known to have landed would strand them against a standard that no
        # longer exists.
        store.delete(old_name)
    return moved


def import_v4_samples(v4_config: dict, store: QcSampleStore) -> int:
    """Optional migration helper: pull `samples` out of a V4
    lab_manager_config.json into the central library. Returns how many were
    imported. Re-running it is safe (save upserts by name)."""
    if not isinstance(v4_config, dict):
        return 0
    samples = v4_config.get("samples")
    if not isinstance(samples, list):
        return 0
    imported = 0
    for raw in samples:
        if not isinstance(raw, dict):
            continue
        sample = QcSample.from_dict(raw)
        if not sample.name.strip() or not sample.sample_id_val.strip():
            continue
        try:
            store.save(sample)
        except ValueError:
            continue
        imported += 1
    return imported
