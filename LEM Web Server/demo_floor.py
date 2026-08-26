#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
demo_floor.py — the offline lab that `--dev --seed` puts on the screen.

Ryan: "in dev mode just populate random crap on multilevels please."

## Why this module exists

`--seed` used to write the *config* side only — `lem_samples`, `lem_boxes`,
`lem_qc_specs`, through `DbConfigStore`. That is what `/api/status` reads, so
the old V4 dashboard came up with one instrument on it and looked fine. The
floor reads the *bench* side: `lem_machine_status`, `lem_machine_specs`,
`lem_machine_layout` and the three level arms, every one of them written out on
the instruments by the station module. Nothing seeded those. So `/api/machines`
answered `{"machines": [], "levels": []}` and levels, the documents tab and the
corrective-action timeline — the whole equipment record — demoed a blank room.

## Two rules this file is built around

**Every seeded state is one the lab could actually produce.** Status is
DERIVED here, never assigned: a bench is RED because a reading it took landed
outside its band, exactly as `evaluate_machine` would have decided. That
direction matters. A demo floor seeded RED with no QC assigned is a state real
LEM cannot reach, and a critic reading that screen correctly reports a
contradiction that says nothing about the app — which is precisely how a review
round was spent. `tests/test_demo_floor.py` holds every one of these invariants
against the floor's own payload.

**The shapes are the bench's, not ones we made up.** The column lists below
match the station module's DDL, and the test asserts that against
`lem_station_module.py` itself rather than trusting this comment. A fixture
whose shape agrees with the UI instead of with the instrument is how `low: -16`
on `qc_specs` — a list that has never carried a band — hid a tooltip rendering
`NaN…NaN` on every instrument in the lab.

## Random, but the same random every boot

The content is generated; the seed is fixed. A demo that reshuffles on every
restart makes findings against it unreproducible — you cannot ask someone to
look at the thing you just looked at, and "it was RED a minute ago" stops being
checkable. So `random.Random(SEED)` and nothing from the wall clock decides
anything a test compares.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional

from labcore_result import LabCoreError, confirm_write

# Fixed on purpose. See the module docstring — change it and every screenshot,
# bug report and "the one on the mezzanine" in a conversation stops matching.
SEED = 20260826

# The lab, stacked. Rank is the order people say out loud, ground first.
LEVELS = (
    ("Ground Floor", 0),
    ("Mezzanine", 1),
    ("Upper Lab", 2),
)


class _Bench:
    """One instrument's story, before any of it is written down.

    `story` is the only dial. Everything a screen shows — the badge, the pills,
    the band, the reason line — is derived from it, so there is no way to seed a
    colour that contradicts the reading underneath it.
    """

    def __init__(self, uid, title, level, watching, story, tests=()):
        self.uid = uid
        self.title = title
        self.level = level
        self.watching = watching
        self.story = story
        self.tests = list(tests)


# (test name, expected, std_dev, k, units) — real methods off this lab's scope.
_FLASH = ("Flash Point", 63.7, 1.05, 2.0, "C")
_CLOUD = ("Cloud Point", -14.0, 1.0, 2.0, "C")
_SULFUR = ("Sulfur", 0.0015, 0.0002, 2.0, "%m/m")
_DENSITY = ("Density 15C", 0.8412, 0.0009, 2.0, "g/cm3")
_VISC = ("Viscosity 40C", 2.98, 0.11, 2.0, "mm2/s")
_CETANE = ("Cetane Index", 47.2, 1.4, 2.0, "")
_POUR = ("Pour Point", -21.0, 2.0, 2.0, "C")

FLEET = (
    # Ground floor — the busy bench row.
    _Bench("multitek-ns", "Multitek NS", 0, r"C:\LabData\multitek-ns",
           "passing", [_SULFUR, _DENSITY]),
    _Bench("optimpp-1", "OptiMPP 1", 0, r"C:\LabData\optimpp-1",
           "failing", [_CLOUD, _POUR]),
    _Bench("pac-flash-1", "PAC Flash 1", 0, r"C:\LabData\pac-flash-1",
           "passing", [_FLASH]),
    _Bench("pac-flash-2", "PAC Flash 2", 0, r"C:\LabData\pac-flash-2",
           "corrected", [_FLASH]),
    _Bench("gc-1", "GC-1", 0, r"C:\LabData\gc-1", "unassigned"),
    # Mezzanine — physical properties.
    _Bench("anton-paar-1", "Anton Paar DMA 4500", 1, r"C:\LabData\dma-4500",
           "passing", [_DENSITY]),
    _Bench("koehler-visc", "Koehler K23000", 1, r"C:\LabData\koehler-k23",
           "stale", [_VISC]),
    _Bench("optimpp-2", "OptiMPP 2", 1, r"C:\LabData\optimpp-2",
           "unassigned"),
    _Bench("cetane-calc", "Cetane Bench", 1, r"C:\LabData\cetane",
           "passing", [_CETANE]),
    # Upper lab — the quieter end.
    _Bench("multitek-s", "Multitek S", 2, r"C:\LabData\multitek-s",
           "offline", [_SULFUR]),
    _Bench("karl-fischer", "Karl Fischer V20", 2, r"C:\LabData\kf-v20",
           "service"),
    _Bench("pensky-1", "Pensky-Martens 1", 2, r"C:\LabData\pensky-1",
           "failing", [_FLASH]),
    _Bench("gc-2", "GC-2", 2, r"C:\LabData\gc-2", "passing", [_SULFUR]),
)

# What the badge says for each story. Only "failing" is RED, and it is RED
# because a reading below puts it there — see `_reading`.
_STATUS = {
    "passing": "GREEN", "corrected": "GREEN", "failing": "RED",
    "stale": "YELLOW", "unassigned": "UNKNOWN", "offline": "DEAD-LINE",
    "service": "SERVICE",
}

STANDARD = "Diesel - AO25"
STANDARD_LAB_ID = "STD-1"


def spec_band(expected: float, std_dev: float, k: float) -> tuple:
    """`expected ± k·std_dev`, in Decimal.

    A deliberate second copy of `lem_station_module.spec_band`, for the same
    reason `qc_is_stale` is duplicated: this package cannot import that file,
    LabStation loads it as a lone module. `test_demo_floor` loads the module and
    asserts the two never disagree — that test is what stops the drift, not this
    comment.

    Decimal rather than float because the band is PUBLISHED, not only judged:
    a low-sulfur spec computed in binary advertises 0.0009000000000000001, and
    the floor draws that number.
    """
    try:
        exp = Decimal(str(expected))
        margin = Decimal(str(k)) * Decimal(str(std_dev))
        if exp.is_finite() and margin.is_finite():
            return float(exp - margin), float(exp + margin)
    except (TypeError, ValueError, ArithmeticError):
        pass
    margin = float(k) * float(std_dev)
    return float(expected) - margin, float(expected) + margin


def _reading(rng, expected, std_dev, k, fails: bool) -> float:
    """A believable last QC reading, inside its band or outside it on purpose.

    Rounded to the resolution an instrument actually prints, so the number on
    the card looks like something a bench produced rather than a float.
    """
    low, high = spec_band(expected, std_dev, k)
    if fails:
        # Outside, but only just — a wild number reads as a broken parser
        # rather than an instrument drifting, which is what RED usually means.
        over = std_dev * rng.uniform(2.2, 3.4)
        value = expected + over if rng.random() < 0.5 else expected - over
    else:
        value = rng.uniform(low + std_dev * 0.15, high - std_dev * 0.15)
    # Resolution comes from how the METHOD is quoted, not from the std_dev.
    # A flash point is reported to 0.1 C and a sulfur to 0.0001 %m/m, so
    # `expected` already carries the number of places the instrument prints;
    # deriving it from the deviation gave "Flash Point 61.376 C", which no
    # bench in this lab has ever put on a ticket.
    places = len(str(expected).partition(".")[2])
    return round(value, max(1, min(6, places)))


class _Writer:
    """Every write is read, like every other write in this app.

    A half-seeded demo is the confusing empty floor this module exists to
    prevent, so a refusal fails loudly here rather than leaving a room with
    three instruments in it and no explanation.
    """

    def __init__(self, gateway):
        self.gateway = gateway

    def __call__(self, sql: str, args: Optional[list] = None) -> None:
        try:
            confirm_write(self.gateway.sql(sql, args or []))
        except LabCoreError as exc:
            raise RuntimeError(
                "--seed could not write the demo floor ({0}). The floor would "
                "come up half-populated and look broken.".format(exc)) from exc


def seed(gateway, documents_root: Optional[str] = None,
         now: Optional[datetime] = None) -> dict:
    """Populate a fake gateway with a stacked, believable lab.

    Returns a small summary so the caller can say what it put up.
    """
    rng = random.Random(SEED)
    now = now or datetime.now()
    write = _Writer(gateway)

    _seed_schedule(write)
    ladder = _seed_levels(gateway)
    placed = _seed_fleet(write, rng, now, ladder)
    _seed_standards(gateway)
    _seed_certificates(gateway, documents_root)
    _seed_documents(gateway, documents_root)
    actions = _seed_corrective_actions(gateway)

    return {"levels": len(ladder), "equipment": len(FLEET),
            "placed": placed, "open_actions": actions,
            "standards": len(STANDARDS), "certificates": len(CERTIFICATES)}


def _seed_schedule(write) -> None:
    """A lab that is open, so the demo does not read "closed" all weekend."""
    write("DELETE FROM lem_lab_schedule WHERE id = 1")
    write("INSERT INTO lem_lab_schedule (id, working_days, opens, closes) "
          "VALUES (1, ?, ?, ?)", ["0,1,2,3,4,5,6", "06:00", "22:00"])


def _seed_levels(gateway) -> List:
    """The ladder, through `LevelStore` rather than behind it.

    Going through the store means the demo exercises the same create/assign
    path the UI does — including the provenance columns on every placement —
    instead of a set of rows that only look like its output.
    """
    from levels import LevelStore

    store = LevelStore(gateway)
    ladder = [store.create(name, rank) for name, rank in LEVELS]
    store.set_default_level(ladder[0].uid)
    return ladder


def _seed_fleet(write, rng, now, ladder) -> int:
    """Every instrument: status, pills, band, bay, heartbeat and history."""
    from levels import LevelStore

    bays = {i: _bay_grid(rng) for i in range(len(ladder))}
    placed = 0

    for bench in FLEET:
        status = _STATUS[bench.story]
        assigned = bool(bench.tests)
        fails = bench.story == "failing"

        # ── the band and the last reading, per test ──────────────────────
        specs = []
        for (name, expected, std_dev, k, units) in bench.tests:
            low, high = spec_band(expected, std_dev, k)
            # A "stale" bench has a band and an assignment but no verdict
            # inside the window — nothing has run, so there is no reading.
            if bench.story == "stale":
                value, in_spec, at = None, None, ""
            else:
                value = _reading(rng, expected, std_dev, k, fails)
                in_spec = low <= value <= high
                at = (now - timedelta(minutes=rng.randint(12, 240))).isoformat()
            correction = -3.0 if bench.story == "corrected" else 0.0
            specs.append({"name": name, "expected": expected,
                          "std_dev": std_dev, "k": k, "units": units,
                          "low": low, "high": high, "value": value,
                          "in_spec": in_spec, "at": at,
                          "correction": correction})

        # RED is a consequence, never a label. If nothing failed, the bench is
        # not RED — whatever the story said it wanted to be.
        if status == "RED" and not any(s["in_spec"] is False for s in specs):
            status = "GREEN"

        reason = _reason(status, specs, bench)

        write("INSERT INTO lem_machine_status (machine_uid, title, status, "
              "reason, updated_at) VALUES (?, ?, ?, ?, ?)",
              [bench.uid, bench.title, status, reason,
               (now - timedelta(seconds=rng.randint(5, 90))).isoformat()])

        qc_pill = ("UNKNOWN" if not assigned else
                   "RED" if status == "RED" else
                   "YELLOW" if bench.story == "stale" else "GREEN")
        write("INSERT INTO lem_machine_substatus (machine_uid, qc, pm, "
              "calibration, updated_at) VALUES (?, ?, ?, ?, ?)",
              [bench.uid, qc_pill,
               rng.choice(["GREEN", "GREEN", "GREEN", "YELLOW"]),
               rng.choice(["GREEN", "GREEN", "YELLOW"]), now.isoformat()])

        for spec in specs:
            write(
                "INSERT INTO lem_machine_specs (machine_uid, test_name, "
                "sample_id, expected, std_dev, k, units, low, high, "
                "last_qc_at, last_qc_value, last_qc_in_spec, correction, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [bench.uid, spec["name"], STANDARD_LAB_ID, spec["expected"],
                 spec["std_dev"], spec["k"], spec["units"], spec["low"],
                 spec["high"], spec["at"], spec["value"],
                 (None if spec["in_spec"] is None else int(spec["in_spec"])),
                 spec["correction"], now.isoformat()])
            write("INSERT INTO lem_machine_targets (machine_uid, sample_name, "
                  "test_name) VALUES (?, ?, ?)",
                  [bench.uid, STANDARD, spec["name"]])
            if spec["correction"]:
                write("INSERT INTO lem_correction_factors (machine_uid, "
                      "test_name, correction, units, updated_at, updated_by) "
                      "VALUES (?, ?, ?, ?, ?, ?)",
                      [bench.uid, spec["name"], spec["correction"],
                       spec["units"], now.isoformat(), "ryan"])

        # ── where it stands ──────────────────────────────────────────────
        x, y = bays[bench.level].pop()
        write("INSERT INTO lem_machine_layout (machine_uid, pos_x, pos_y) "
              "VALUES (?, ?, ?)", [bench.uid, x, y])
        LevelStore(write.gateway).assign(bench.uid, ladder[bench.level].uid,
                                         by="demo")
        placed += 1

        # ── a heartbeat, unless the bench is meant to look silent ────────
        if bench.story != "offline":
            write("INSERT INTO lem_machine_heartbeat (machine_uid, last_poll, "
                  "watching) VALUES (?, ?, ?)",
                  [bench.uid,
                   (now - timedelta(seconds=rng.randint(3, 50))).isoformat(),
                   bench.watching])

        _seed_history(write, rng, now, bench, specs, status)

        if bench.story == "corrected":
            # One per-machine override, so the qc_specs list is not always
            # empty on the demo floor. It carries expected/std_dev/k and no
            # band — the band is the module's to publish.
            name, expected, std_dev, k, units = bench.tests[0]
            write("INSERT INTO lem_qc_specs (machine_uid, test_name, "
                  "sample_id, expected, std_dev, k, units) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?)",
                  [bench.uid, name, STANDARD_LAB_ID, expected, std_dev, k,
                   units])

        _seed_maintenance(write, rng, now, bench)

    return placed


def _bay_grid(rng) -> List[tuple]:
    """Distinct bays, shuffled.

    Popped from, never sampled: two instruments saved on the SAME bay is a real
    production bug (OptiMPP 2 and PAC Flash 2, both 4.1,0) whose spill fix lives
    in the severed world module, so a collision here would demo the bug rather
    than the floor.
    """
    grid = [(float(col), float(row))
            for row in range(4) for col in range(6)]
    rng.shuffle(grid)
    return grid


def _reason(status, specs, bench) -> str:
    """The sentence under the badge, in the shape `evaluate_machine` writes."""
    if status == "UNKNOWN":
        return "No QC assigned"
    if status == "SERVICE":
        return "Out for service"
    if status == "DEAD-LINE":
        return "No data received"
    if status == "YELLOW":
        return "QC has not run inside the window"
    failed = [s for s in specs if s["in_spec"] is False]
    if failed:
        s = failed[0]
        return "{0} {1} {2} — outside {3} ± {4}.".format(
            s["name"], s["value"], s["units"],
            s["expected"], round(s["k"] * s["std_dev"], 4)).strip()
    return "System nominal"


def _seed_history(write, rng, now, bench, specs, status) -> None:
    """Runs and verdicts in `lem_machine_log`, so the timeline has something.

    Only the documented kinds — run | qc | status_change — because the logs page
    filters on them and an invented kind is a row nobody can find again.
    """
    if bench.story in ("offline", "service"):
        write(*_log(bench.uid, "status_change",
                    now - timedelta(hours=rng.randint(6, 40)),
                    value=status, detail={"from": "GREEN", "to": status}))
        return

    for hours_ago in sorted(rng.sample(range(1, 72), 6), reverse=True):
        ts = now - timedelta(hours=hours_ago, minutes=rng.randint(0, 59))
        write(*_log(bench.uid, "run", ts,
                    lab_id="L-{0}".format(37000 + rng.randint(1, 999)),
                    test_name=(specs[0]["name"] if specs else "Density 15C"),
                    value="{0}".format(round(rng.uniform(0.82, 0.86), 4))))

    for spec in specs:
        if spec["value"] is None:
            continue
        write(*_log(bench.uid, "qc", now - timedelta(minutes=rng.randint(12, 240)),
                    lab_id=STANDARD_LAB_ID, test_name=spec["name"],
                    value=str(spec["value"]),
                    detail={"low": spec["low"], "high": spec["high"],
                            "in_spec": bool(spec["in_spec"]),
                            "raw_value": spec["value"] - spec["correction"],
                            "correction": spec["correction"]}))

    if status == "RED":
        write(*_log(bench.uid, "status_change",
                    now - timedelta(minutes=rng.randint(5, 200)),
                    value="RED", detail={"from": "GREEN", "to": "RED"}))


def _log(machine_uid, kind, ts, lab_id="", test_name="", value="",
         detail=None) -> tuple:
    """The same column order `lem_station_module.build_log_insert` writes."""
    return ("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
            "test_name, value, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [machine_uid, ts.isoformat(), kind, lab_id, test_name, str(value),
             json.dumps(detail or {})])


def _seed_maintenance(write, rng, now, bench) -> None:
    """A PM and a calibration per bench, some of them overdue."""
    for kind, name, interval in (("pm", "Monthly PM", 30),
                                 ("calibration", "Annual calibration", 365)):
        last = now - timedelta(days=rng.randint(1, int(interval * 1.4)))
        write("INSERT INTO lem_maintenance (uid, machine_uid, name, kind, "
              "interval_days, last_done, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
              ["{0}-{1}".format(bench.uid, kind), bench.uid, name, kind,
               interval, last.strftime("%Y-%m-%d"), ""])


def _pdf(title: str) -> bytes:
    """A real one-page PDF with its own title printed on it.

    Two reasons it is generated per document rather than one shared constant.

    **The store deduplicates on `(machine_uid, content_hash)`** — re-uploading a
    file already filed against a piece of equipment returns the existing record
    instead of a second copy, which is right, and which silently collapsed two
    byte-identical demo documents into one. A demo where the second upload
    vanishes teaches the wrong thing about the tab.

    **A downloaded file should open.** The tab is for calibration certificates,
    and one that opens to a blank page is indistinguishable from a broken
    download. So this carries a real xref table and a content stream, and the
    offsets are computed rather than typed.
    """
    text = title.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    stream = ("BT /F1 13 Tf 62 720 Td ({0}) Tj ET\n"
              "BT /F1 9 Tf 62 700 Td (ASAP Labs - demo document, "
              "generated by --dev --seed.) Tj ET\n").format(text).encode("latin-1")

    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>",
        b"<</Length " + str(len(stream)).encode() + b">>stream\n"
        + stream + b"endstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj" + body + b"endobj\n"

    start = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += "{0:010d} 00000 n \n".format(offset).encode()
    out += (b"trailer<</Size " + str(len(objects) + 1).encode()
            + b"/Root 1 0 R>>\nstartxref\n" + str(start).encode()
            + b"\n%%EOF\n")
    return bytes(out)

DOCUMENTS = (
    ("pac-flash-1", "PAC Flash 1 - calibration certificate 2026.pdf"),
    ("pac-flash-1", "PAC Flash 1 - service report.pdf"),
    ("anton-paar-1", "DMA 4500 - manufacturer manual.pdf"),
    ("multitek-ns", "Multitek NS - annual verification.pdf"),
)


# The shared standards library: what a control is MEANT to read. Defined once,
# detected by every bench against the whole library — which is why the bench
# config road ships it unscoped while everything beside it is per-machine.
#
# Seeded because three features demo nothing without it. The bench road answered
# `qc_samples: 0`; the certificate store attaches a COA to a STANDARD and so had
# nothing to attach to; and `lem_qc_specs` rows are a per-machine OVERRIDE of a
# library entry, which reads oddly when there is no library to override.
#
# `expected`/`std_dev` here are the CONTROL LIMIT — the pass band. They are not
# the certificate's uncertainty, which is a different quantity from a different
# source, and the two are deliberately kept apart (see the certificates below).
STANDARDS = (
    (STANDARD, STANDARD_LAB_ID,
     (_FLASH, _CLOUD, _SULFUR, _DENSITY, _VISC, _CETANE, _POUR)),
    ("Gasoline - RON check", "STD-2", (_DENSITY,)),
)


def _seed_standards(gateway) -> None:
    """Through `QcSampleStore`, so the demo exercises the real save path."""
    from models import SampleSpec  # noqa: F401  (kept for shape parity)
    from qc_samples import QcSample, QcSampleStore, QcSampleTest

    store = QcSampleStore(gateway)
    store.ensure_schema()
    for name, lab_id, tests in STANDARDS:
        store.save(QcSample(
            name=name, sample_id_val=lab_id,
            tests=[QcSampleTest(name=t[0], value_col=t[0], expected=t[1],
                                std_dev=t[2], k=t[3], units=t[4])
                   for t in tests]))


# Certificates hang off the STANDARD, not the instrument — the certificate is
# the evidence for the values the standard asserts. One expired on purpose: an
# expired certificate is a finding at assessment, and a demo where everything is
# in date shows nothing about how that reads.
CERTIFICATES = (
    (STANDARD, "Diesel AO25 - certificate of analysis 2026.pdf",
     "2027-06-30", "2026-01-15"),
    ("Gasoline - RON check", "Gasoline RON - COA 2025.pdf",
     "2026-03-31", "2025-03-20"),
)


def _seed_certificates(gateway, documents_root: Optional[str]) -> None:
    from standard_documents import StandardCertificateStore

    store = StandardCertificateStore(gateway, root=documents_root)
    for standard, filename, expires, issued in CERTIFICATES:
        store.save(standard, filename, _pdf(filename), uploaded_by="ryan",
                   expires_at=expires, issued_at=issued)


def _seed_documents(gateway, documents_root: Optional[str]) -> None:
    """Bytes on disk, metadata in LabCore — through the store, as a user would.

    A row pointing at a file that is not there is the one shape that makes the
    tab look broken, so this goes through `save()` rather than inserting
    metadata rows and hoping.
    """
    from equipment_documents import EquipmentDocumentStore

    store = EquipmentDocumentStore(gateway, root=documents_root)
    for machine_uid, filename in DOCUMENTS:
        store.save(machine_uid, filename, _pdf(filename), uploaded_by="ryan")


ACTIONS = (
    ("optimpp-1", "Cloud Point drifting low across three consecutive standards.",
     "high"),
    ("pensky-1", "Flash Point failed the morning standard; suspect a worn "
     "thermocouple.", "high"),
    ("koehler-visc", "Bath temperature unstable overnight — no QC ran.",
     "normal"),
)


def _seed_corrective_actions(gateway) -> int:
    """A few open actions, so the timeline and the roll-up are not empty."""
    from equipment_history import CorrectiveActionStore

    store = CorrectiveActionStore(gateway)
    opened = 0
    for machine_uid, what_happened, priority in ACTIONS:
        store.open_action(machine_uid, what_happened, by="ryan",
                          priority=priority)
        opened += 1
    return opened
