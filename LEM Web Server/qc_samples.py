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
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from labcore_result import (
    LabCoreError,
    LabCoreRefused,
    LabCoreUnavailable,
    confirm_write,
    rows,
)


# ── What this store raises ───────────────────────────────────────────────────
#
# LabCore's write queue refuses past 100 pending by ANSWERING — no exception, no
# "error" key — so every unread `gateway.sql()` here reported success for a write
# that never happened. `lem_qc_samples` is the shared library: one dropped save
# is not one machine mis-judged but every machine running that standard, and a
# dropped `changeover` silently stops QC across the lab, which is the exact
# failure changeover exists to prevent.


class QcSampleStoreError(LabCoreError):
    """A QC-standard operation whose outcome LabCore did not confirm.

    Named so a route can catch this store specifically; a subclass of
    `LabCoreError` so one `except` still covers every LabCore problem. The two
    below preserve `labcore_result`'s distinction — "could not ask" is worth
    retrying, "answered and did not happen" is not — and, by subclassing the
    labcore_result pair too, a caller may equally catch `LabCoreRefused`.
    """


class QcSampleUnavailable(QcSampleStoreError, LabCoreUnavailable):
    """LabCore could not be asked, so the standards library is unknown."""


class QcSampleRefused(QcSampleStoreError, LabCoreRefused):
    """LabCore answered, and the standard was not written."""


@contextmanager
def _doing(what: str):
    """Re-label `labcore_result`'s verdict with the operation that failed."""
    try:
        yield
    except QcSampleStoreError:
        raise                      # already labelled; do not wrap twice
    except LabCoreUnavailable as exc:
        raise QcSampleUnavailable("Could not {}: {}".format(what, exc)) from exc
    except LabCoreRefused as exc:
        # The ANSWER is carried across the re-label, not just the sentence.
        # Re-raising with the text alone lost `busy` and `retry_after`, so a
        # full queue reached the browser as 502 "this will never work" instead
        # of 503 with a Retry-After — the one distinction the client cannot
        # recover by reading English.
        raise QcSampleRefused(
            "Could not {}: {}".format(what, exc),
            getattr(exc, "result", None)) from exc



def _sql(gateway, sql: str, args=None) -> dict:
    """Issue one write, turning a RAISED transport error into an ANSWER.

    `confirm_write(gateway.sql(...))` reads the answer but leaves the CALL
    bare, so a socket error — a write that equally did not happen — escaped
    past every `except QcSampleStoreError` as a raw OSError and became a bare
    500. "Internal Server Error" does not tell an operator whether the standard was
    saved. Handing it back in the shape `labcore_result` already refuses keeps
    one rule instead of two.
    """
    try:
        return gateway.sql(sql, args or [])
    except Exception as exc:                       # transport, not logic
        return {"error": "LabCore could not be written to ({0}: {1})".format(
            type(exc).__name__, exc)}


QC_SAMPLES_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_qc_samples ("
    "name TEXT PRIMARY KEY, sample_id_val TEXT, tests TEXT)"
)


# ── How long a QC result stays good, and who gets to say ─────────────────────
#
# QC expiry is a ROLLING window (`data_source.qc_is_stale`, duplicated on purpose
# in the station module). Four levels can supply the number; the chain below is
# the ONLY place in this tree that decides which one does.
#
#   1. MethodMapping.qc_expire_hours   — a human act on THIS instrument
#   2. the standard's own window       — NEW; a property of the MATERIAL
#   3. BoxConfig/Machine.qc_expire_hours — this instrument's default
#   4. QC_WINDOW_DEFAULT_HOURS
#
# Level 2 is the addition. A control's usable life belongs to the material — a
# working standard degrades, an ampoule opened this morning is not good for a
# week — so it belongs on the standard, once, instead of being re-typed on every
# bench that runs it and lost on the next lot change.
#
# **Zero means "fall through", never "expire immediately."** That is how
# `MethodMapping.qc_expire_hours` and `TestSpec.qc_expire_hours` already read,
# and it is the rule that makes this safe to ship: the rows already in
# `lem_qc_samples` carry no window at all, and neither will a bench on an older
# build. If absence read as a zero-hour window, every reading in the lab would be
# stale the moment it was taken.

QC_WINDOW_DEFAULT_HOURS = 24.0


def _window_hours(raw) -> float:
    """A usable window in hours, or 0.0 meaning "this level said nothing".

    Everything that is not a finite positive number is silence: None, "", text,
    NaN, inf and negatives. NaN matters more than it looks — it compares False
    against every bound, so an unguarded NaN sails past `if hours > 0` and then
    makes `qc_is_stale` answer False forever, which is a window that never
    expires rather than one that was never set.
    """
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if hours != hours or hours in (float("inf"), float("-inf")):
        return 0.0
    return hours if hours > 0 else 0.0


def resolve_qc_window(levels, default_hours: float = QC_WINDOW_DEFAULT_HOURS
                      ) -> Tuple[float, str]:
    """The QC staleness window, and WHICH level supplied it.

    `levels` is an ordered sequence of `(source, hours)`, most specific first.
    The first level with something to say wins; every other level is silence.

    The source travels with the number on purpose. `/api/machines/<uid>/
    status-timeline` already reports `qc_expire_source` for exactly this reason —
    a window silently assumed is a colour nobody can check — and now that four
    levels can supply it, "24 hours" alone stopped being an answer a person can
    act on.

    A caller that has not reached the bottom of the chain passes
    `default_hours=0.0`: spec-building knows the mapping and the standard but not
    the instrument, and answering 24.0 there would silently override every
    machine default in the lab.
    """
    for source, raw in levels or ():
        hours = _window_hours(raw)
        if hours:
            return hours, str(source)
    return float(default_hours), "default"


def _tests_of(sample) -> List[dict]:
    """The tests on one library row, however that row reached us.

    `QcSampleStore.as_payload` hands back parsed lists; the bench config road
    (`snapshot_service.bench_config_from_tables`) deliberately hands the JSON
    TEXT exactly as LabCore stores it, because the module's
    `parse_qc_sample_rows` calls `json.loads` on it. One reader for both, so the
    floor and the bench cannot reach different conclusions about the same
    library. Unparseable is EMPTY, never an exception: a standard whose blob is
    corrupt must not take the whole resolution down with it.
    """
    raw = (sample or {}).get("tests")
    if isinstance(raw, list):
        listed = raw
    else:
        try:
            listed = json.loads(raw or "[]")
        except (TypeError, ValueError):
            return []
    return [t for t in listed if isinstance(t, dict)]


def window_from_standards(library, targets) -> Tuple[float, str]:
    """What the standards THIS machine is assigned say about their own life.

    Returns `(hours, what)`. `(0.0, "")` means no assigned standard stated a
    window — silence, which the caller falls through on.

    **Only assigned standards count.** QC here is assignment-only (2026-08-03),
    and without that filter one tight control anywhere in the shared library
    would shorten the window of every instrument in the lab.

    **The tightest assigned window decides.** A single window colours a whole
    instrument, and its QC is only as fresh as its shortest-lived control — the
    same "any stale test" rule `evaluate_machine` applies to go YELLOW.

    A target matches a test on its measurement column OR its own name, exactly as
    `specs_from_qc_samples` matches, so both roads read one assignment the same
    way.
    """
    wanted = set()
    for target in targets or ():
        if not isinstance(target, dict):
            continue
        sample = str(target.get("sample_name", target.get("sample", "")) or "")
        test = str(target.get("test_name", target.get("test", "")) or "")
        if sample.strip() and test.strip():
            wanted.add((sample.strip(), test.strip().lower()))
    if not wanted:
        return 0.0, ""

    best = 0.0
    what = ""
    for sample in library or ():
        if not isinstance(sample, dict):
            continue
        sample_name = str(sample.get("name") or "").strip()
        for test in _tests_of(sample):
            value_col = str(test.get("value_col") or "").strip().lower()
            test_name = str(test.get("name") or "").strip().lower()
            if not any((sample_name, n) in wanted
                       for n in (value_col, test_name) if n):
                continue
            hours = _window_hours(test.get("qc_expire_hours"))
            if hours and (not best or hours < best):
                best = hours
                what = "{0} · {1}".format(
                    sample_name, str(test.get("name")
                                     or test.get("value_col") or "").strip())
    return best, what


@dataclass
class QcSampleTest:
    """One test on a QC standard: pass when expected ± k·std_dev.

    `name` is what the lab calls the check (V4's test name, e.g.
    "Cloud - D7689"); `value_col` is the measurement it reads — a LabCore
    test method in the new world, a CSV column in V4. Modules match on
    either, so V4 definitions keep working.

    `qc_expire_hours` is how long a passing result on THIS control stays good —
    a property of the material, stated once here instead of on every bench that
    runs it. **0.0 means "no opinion"**, and the instrument's own default
    decides; see `resolve_qc_window` for the full chain. It is stored inside the
    `tests` JSON TEXT column, so nothing about `lem_qc_samples`' three columns
    changes and no bench needs the field to exist.
    """

    name: str
    value_col: str = ""
    expected: float = 0.0
    std_dev: float = 0.0
    k: float = 2.0
    units: str = ""
    qc_expire_hours: float = 0.0

    def to_dict(self) -> dict:
        return {"name": self.name, "value_col": self.value_col,
                "expected": self.expected, "std_dev": self.std_dev,
                "k": self.k, "units": self.units,
                "qc_expire_hours": self.qc_expire_hours}

    @classmethod
    def from_dict(cls, data: dict) -> "QcSampleTest":
        return cls(
            name=str(data.get("name", "")),
            value_col=str(data.get("value_col", "") or data.get("name", "")),
            expected=float(data.get("expected") or 0.0),
            std_dev=float(data.get("std_dev") or 0.0),
            k=float(data.get("k") or 2.0),
            units=str(data.get("units", "")),
            # `_window_hours`, not `float(... or 0.0)`: an absent key, a blank
            # box, text, NaN and a negative all have to land on the SAME 0.0
            # that means "fall through". Every row already in LabCore is missing
            # this key, and a crash — or worse, a zero-hour window — on the rows
            # the lab already has is not a launch this feature survives.
            qc_expire_hours=_window_hours(data.get("qc_expire_hours")),
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
        """Make sure `lem_qc_samples` exists, or say why it might not.

        `_schema_ready` is set only after the CREATE is ACKNOWLEDGED — caching it
        on an unread answer would remember a refused CREATE as done and send
        every later save into a table that is not there.
        """
        if self._schema_ready:
            return
        with _doing("create lem_qc_samples"):
            confirm_write(_sql(self.gateway, QC_SAMPLES_DDL))
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
            # Refused, not silently normalised. A negative window would be read
            # as 0.0 = "fall through" everywhere downstream, so storing it would
            # throw away what the operator typed and show them the machine
            # default back — the same reason `std_dev < 0` is refused here
            # rather than clamped.
            if test.qc_expire_hours < 0:
                raise ValueError(
                    "A QC window cannot be negative. Leave it blank (or 0) to "
                    "use the instrument's own default.")
        self.ensure_schema()
        with _doing("save the QC standard {!r}".format(sample.name.strip())):
            confirm_write(_sql(
                self.gateway,
                "INSERT INTO lem_qc_samples (name, sample_id_val, tests) "
                "VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                "sample_id_val=excluded.sample_id_val, tests=excluded.tests",
                [sample.name.strip(), sample.sample_id_val.strip(),
                 json.dumps([t.to_dict() for t in sample.tests])],
            ))

    def delete(self, name: str) -> None:
        """Retire a standard. Raises unless LabCore says the DELETE ran.

        `confirm_write`, not `wrote_rows`: matching nothing means it was already
        gone, which is the state the caller asked for. Not knowing whether it ran
        is the failure — a standard the library still lists after the floor said
        it was retired is one benches keep pulling.
        """
        self.ensure_schema()
        with _doing("delete the QC standard {!r}".format(name)):
            confirm_write(_sql(
                self.gateway,
                "DELETE FROM lem_qc_samples WHERE name = ?", [name]))

    def list_samples(self, *, missing_ok: bool = True) -> List[QcSample]:
        """The standards library, or an exception saying why it is unknown.

        MISSING TABLE MAY DEGRADE TO EMPTY (`missing_ok`, the default): nobody
        has defined a standard yet, so an empty library is the truth.

        EVERY OTHER ERROR RAISES. `if res.get("error"): return []` meant a blip
        emptied the picker and made `/api/qc-samples` — which the station modules
        pull — answer "this lab certifies nothing", which is how a bench stops
        recognising its own standard's Lab ID and quietly logs a QC run as an
        ordinary sample.

        `missing_ok=False` is for `changeover`, which DECIDES A WRITE from this
        list: there, "could not ask" served as "does not exist" would report the
        operator's real lot as not found, or let a duplicate lot be created over
        a library it could not see.

        A READ DECLARES NOTHING (2026-08-25). The first cut of this called a
        best-effort `_schema_for_read()` which swallowed a refusal but still
        ISSUED the CREATE — one more op per read into a queue that was already
        past 100 pending, forever, since the flag can never latch while it is
        being refused. The declaration belongs to `save()`/`delete()`, which
        genuinely need the table before they INSERT. A SELECT says "no such
        table" for itself, and that is the one error `rows()` may call empty.
        """
        res = self.gateway.read_sql(
            "SELECT name, sample_id_val, tests FROM lem_qc_samples ORDER BY name")
        with _doing("read the QC standards"):
            listed = rows(res, missing_ok=missing_ok)
        samples = []
        for row in listed:
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

    NOTHING HERE IS BEST-EFFORT. Every step raises rather than reporting a
    number it is not sure of: this is the one operation whose whole job is to
    stop QC going quiet, and a changeover reported as "3 machines moved" that
    moved one is worse than a changeover that failed loudly. If it raises
    part-way, re-running it is safe — the new lot upserts by name and machines
    already moved no longer reference the old lot, so they are simply not
    counted twice.
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
    # A READ THAT DECIDES A WRITE, so it may not degrade. `ensure_schema` is the
    # strict one (we are about to write; a refused CREATE means the new lot
    # cannot land and it is better heard now), and the read below refuses even
    # the missing-table excuse: served as "does not exist", a read that could not
    # be asked would tell the operator their real lot is not found, or hide the
    # library well enough to let a duplicate lot be created over it.
    store.ensure_schema()
    samples = {s.name: s for s in store.list_samples(missing_ok=False)}
    if old_name not in samples:
        raise ValueError(f"QC sample '{old_name}' not found.")
    if new_name in samples:
        raise ValueError(f"QC sample '{new_name}' already exists.")

    old = samples[old_name]

    # WHAT ALREADY LANDED TRAVELS WITH THE REFUSAL. There is no undo across
    # queue ops, so a changeover stopped half-way really has created the new
    # lot and really has moved some instruments — and the operator finding two
    # lots and no explanation is the state this reporting exists to prevent.
    # `landed` / `moved` ride on the exception rather than being returned,
    # because returning them would mean returning a number this function is
    # not sure of, which is exactly what the docstring above forbids.
    landed: List[str] = []
    moved = 0
    targets = QcTargetStore(gateway)
    try:
        store.save(QcSample(name=new_name, sample_id_val=new_id_val,
                            tests=[QcSampleTest.from_dict(t.to_dict())
                                   for t in old.tests]))
        landed.append("the new lot '{0}'".format(new_name))
        # `moved` is incremented only AFTER `assign` returns, so a refusal from
        # the target store propagates instead of being counted as a move. That
        # store owns its own confirmation; this loop's job is not to swallow it.
        for uid, assigned in targets.all().items():
            if not any(t.sample == old_name for t in assigned):
                continue
            targets.assign(uid, [WatchedTarget(new_name, t.test)
                                 if t.sample == old_name else t
                                 for t in assigned])
            moved += 1
            landed.append("{0} moved to '{1}'".format(uid, new_name))
        if retire_old:
            store.delete(old_name)
            landed.append("the old lot '{0}' retired".format(old_name))
    except LabCoreError as exc:
        exc.landed = landed
        exc.moved = moved
        raise
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
            # DELIBERATE SWALLOW, and only this one: a V4 file may carry a
            # half-defined standard (no test name, a negative std dev), and one
            # bad row should not stop a migration. `QcSampleStoreError` is NOT
            # caught here — it is not a bad row, it is a row LabCore did not
            # store, and a migration that reports "imported 40" having written
            # 12 leaves a lab believing its library came across.
            continue
        imported += 1
    return imported
