"""LEM Station — one machine's parsing + QC status, as a LabStation module.

v2 model (capture and map): the module waits for the device to print
(single CSV tail, multi CSV folder, or serial), holds the first print as a
template, and the operator maps portions of that real data — by cell
selection or text detection, with clean-text tools — onto LabCore test
methods. No CSV formatting exists here: parsed data goes into LabCore only.
QC specs are pulled from LabCore (written by the LEM master view); the
module never defines its own test names.

A fourth source, "manual", is for instruments too old to print at all: no
capture, no mapping, nothing ingested. It is a QC panel — the operator types a
reading for a test the master view has ASSIGNED, and nothing else is enterable.
Everything after the row is the parsed path unchanged (see `manual_qc_row`).

NOTE: no `from __future__ import annotations` here — LabStation loads custom
modules without registering them in sys.modules, and dataclasses cannot
resolve stringized annotations for a module missing from sys.modules.
"""
import ast
import csv
import difflib
import hashlib
import io
import json
import operator
import os
import re
import secrets
import shutil
import struct
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional

STATUS_GREEN = "GREEN"
STATUS_YELLOW = "YELLOW"
STATUS_RED = "RED"
STATUS_DEAD = "DEAD-LINE"
STATUS_SERVICE = "SERVICE"
STATUS_UNKNOWN = "UNKNOWN"

STATUS_COLORS = {
    STATUS_GREEN: "#21c071",
    STATUS_YELLOW: "#f5c542",
    STATUS_RED: "#f85b5b",
    STATUS_DEAD: "#0f172a",
    STATUS_SERVICE: "#8d99ae",
    STATUS_UNKNOWN: "#718096",
}

LAB_ID_KEY = "Lab ID"
TIMESTAMP_KEYS = ("parsed_date", "parsed_time")
# Bookkeeping carried on a parsed row alongside the measurements. ISO/IEC
# 17025:2017 §7.5.1 requires a technical record from which the measurement can be
# reconstructed, so a corrected row keeps the raw reading and the offset applied.
# Reserved like the keys above: consumers skip them rather than treat them as
# methods, or "__raw__" would be written to LabCore as a test name.
RAW_KEY = "__raw__"
CORRECTION_KEY = "__corrections__"
# The journal record a row came from ("epoch:seq"), so the results road can
# tell the journal which readings it has finished with. Bookkeeping, never a
# measurement: reserved, so no consumer writes it as a test name, a log value
# or a CSV column. See "The bench journal".
JOURNAL_KEY = "__journal__"
# Where a row's print came from when that is not simply "read live off the
# instrument": "ambiguous" marks a line the rewrite resolver recorded although
# it may repeat one already recorded (transfer v4 §4.2). It rides into the log
# detail as `origin` so the record shows it; never a measurement.
ORIGIN_KEY = "__origin__"
RESERVED_ROW_KEYS = (LAB_ID_KEY, RAW_KEY, CORRECTION_KEY, JOURNAL_KEY,
                     ORIGIN_KEY) + TIMESTAMP_KEYS
# "manual" is the bench with no parser: an older instrument that prints to paper
# or to nothing at all, whose readings the operator types in. It ingests nothing
# — everything after the row is the same path a parsed print takes.
SOURCE_TYPES = ("single_csv", "multi_csv", "serial", "manual")
SOURCE_LABELS = {
    "single_csv": "Single CSV (tail a file)",
    "multi_csv": "Multi CSV (new file per print)",
    "serial": "Serial (RS-232)",
    "manual": "Manual entry (no parsing)",
}


# ── Config model ─────────────────────────────────────────────────────────────

@dataclass
class Selector:
    """Marks a portion of a device print.

    mode "cell":   index into the print split by the machine delimiter
                   (lines flattened in order).
    mode "detect": regex searched over the whole print; first group wins,
                   else the whole match.
    clean:         clean-text ops applied to the extracted value, in order:
                   "strip", "collapse_ws", "keep_number", "remove:<text>".
    """
    mode: str = "cell"
    index: int = 0
    pattern: str = ""
    clean: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"mode": self.mode, "index": self.index,
                "pattern": self.pattern, "clean": list(self.clean)}

    @classmethod
    def from_dict(cls, data: dict) -> "Selector":
        return cls(
            mode=str(data.get("mode", "cell")),
            index=int(data.get("index", 0)),
            pattern=str(data.get("pattern", "")),
            clean=[str(op) for op in data.get("clean", [])],
        )

    def describe(self) -> str:
        if self.mode == "detect":
            return f"detect: {self.pattern}"
        return f"cell {self.index}"


@dataclass
class MethodMapping:
    """One marked portion assigned to a LabCore test method (or a group).

    qc_sample_id marks this result as QC-checked: whenever that sample runs,
    the module self-verifies against LabCore's spec for the method.
    qc_expire_hours overrides the machine's default QC window (0 = default).
    csv_header names this group's column in the latest-result CSV export —
    one clean column instead of every LabCore method name."""
    methods: List[str] = field(default_factory=list)
    selector: Selector = field(default_factory=Selector)
    qc_sample_id: str = ""
    qc_expire_hours: float = 0.0
    csv_header: str = ""

    def to_dict(self) -> dict:
        return {"methods": list(self.methods),
                "selector": self.selector.to_dict(),
                "qc_sample_id": self.qc_sample_id,
                "qc_expire_hours": self.qc_expire_hours,
                "csv_header": self.csv_header}

    @classmethod
    def from_dict(cls, data: dict) -> "MethodMapping":
        return cls(
            methods=[str(m) for m in data.get("methods", [])],
            selector=Selector.from_dict(data.get("selector", {})),
            qc_sample_id=str(data.get("qc_sample_id", "")),
            qc_expire_hours=float(data.get("qc_expire_hours", 0.0)),
            csv_header=str(data.get("csv_header", "")),
        )


@dataclass
class TestSpec:
    """A QC spec pulled from LabCore: pass when value is within
    expected ± k·std_dev. name/value_col is the LabCore test method."""
    __test__ = False  # tell pytest this is not a test class
    name: str
    value_col: str
    expected: float
    std_dev: float
    k: float = 2.0
    units: str = ""
    sample_id: str = ""  # Lab ID of the QC sample; "" matches every row
    qc_expire_hours: float = 0.0  # per-test QC window; 0 = machine default
    # WHICH level put that number here: "mapping" (the operator, on this bench)
    # or "standard" (the shared library's own window). "" means nobody did, or
    # that this spec was persisted before the standard could speak. It is
    # carried so a person can be told what to change; `qc_window_for` reads it.
    qc_expire_source: str = ""
    # Additive offset applied to the RAW reading before it is judged:
    # corrected = raw + correction. Default 0.0 = no correction. V4 stored a
    # number of this shape and never applied it to anything; this one decides
    # pass/fail, so the raw value is kept alongside it in the log.
    correction: float = 0.0
    # The last verdict LabCore has for this test. A LabStation restart loses the
    # rows this module parsed, so without these a machine whose QC passed three
    # hours ago looked like one whose QC had never run — and went YELLOW.
    last_qc_at: str = ""          # ISO timestamp of that verdict
    last_qc_value: Optional[float] = None
    last_qc_in_spec: Optional[bool] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value_col": self.value_col,
            "expected": self.expected,
            "std_dev": self.std_dev,
            "k": self.k,
            "units": self.units,
            "sample_id": self.sample_id,
            "qc_expire_hours": self.qc_expire_hours,
            "qc_expire_source": self.qc_expire_source,
            "last_qc_at": self.last_qc_at,
            "last_qc_value": self.last_qc_value,
            "last_qc_in_spec": self.last_qc_in_spec,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TestSpec":
        return cls(
            name=str(data.get("name", "")),
            value_col=str(data.get("value_col", "")),
            expected=float(data.get("expected", 0.0)),
            std_dev=float(data.get("std_dev", 0.0)),
            k=float(data.get("k", 2.0)),
            units=str(data.get("units", "")),
            sample_id=str(data.get("sample_id", "")),
            # `_window_hours`, not `float(...)`: an absent key, a blank, text,
            # NaN and a negative all have to land on the SAME 0.0 that means
            # "fall through". A saved config from an older build has none of
            # this, and it must load, not crash and not go instantly stale.
            qc_expire_hours=_window_hours(data.get("qc_expire_hours")),
            qc_expire_source=str(data.get("qc_expire_source", "") or ""),
            last_qc_at=str(data.get("last_qc_at", "") or ""),
            last_qc_value=(None if data.get("last_qc_value") is None
                           else float(data.get("last_qc_value"))),
            last_qc_in_spec=(None if data.get("last_qc_in_spec") is None
                             else bool(data.get("last_qc_in_spec"))),
        )


@dataclass
class MaintTask:
    """A repeating PM or calibration the operator completes on LabStation."""
    uid: str = ""
    name: str = ""
    kind: str = "pm"          # "pm" | "calibration"
    interval_days: int = 30
    last_done: str = ""       # ISO date; "" = never completed
    note: str = ""            # note from the most recent completion

    def to_dict(self) -> dict:
        return {"uid": self.uid, "name": self.name, "kind": self.kind,
                "interval_days": self.interval_days,
                "last_done": self.last_done, "note": self.note}

    @classmethod
    def from_dict(cls, data: dict) -> "MaintTask":
        return cls(
            uid=str(data.get("uid", "")),
            name=str(data.get("name", "")),
            kind=str(data.get("kind", "pm")),
            interval_days=int(data.get("interval_days", 30)),
            last_done=str(data.get("last_done", "")),
            note=str(data.get("note", "")),
        )


@dataclass
class Machine:
    """The one instrument this module handles: where its prints come from
    and how marked portions map onto LabCore test methods."""
    uid: str = ""
    title: str = ""
    source_type: str = "single_csv"  # single_csv | multi_csv | serial | manual
    csv_path: str = ""               # single: file to tail; multi: folder
    delimiter: str = ","
    com_port: str = ""               # serial source
    baud_rate: int = 9600
    parity: str = "N"                # N / E / O / M / S
    stop_bits: float = 1.0           # 1, 1.5, 2
    byte_size: int = 8               # 5-8
    idle_gap: float = 0.3            # seconds of silence ending a report
    lab_id: Selector = field(default_factory=Selector)
    mappings: List[MethodMapping] = field(default_factory=list)
    template: str = ""               # held print used to configure mappings
    qc_expire_hours: float = 24.0
    tests: List[TestSpec] = field(default_factory=list)  # cache of LabCore specs
    # {test_name: offset} for EVERY method this bench reports — not only the ones
    # with QC assigned. QC is assignment-only, so most methods have no spec at all,
    # and those are exactly the ones producing customer results.
    corrections: Dict[str, float] = field(default_factory=dict)
    lab_id_column: str = LAB_ID_KEY  # internal row key, not user-facing
    manual_override: str = ""        # "", SERVICE, or DEAD-LINE
    override_comment: str = ""       # mandatory for SERVICE / DEAD-LINE
    maintenance: List[MaintTask] = field(default_factory=list)
    last_position: int = 0           # single_csv byte offset
    last_mtime: float = 0.0          # multi_csv newest processed file mtime
    last_result_file: str = ""       # latest-result CSV we last wrote
    image_path: str = ""             # optional photo shown on the card

    def to_dict(self) -> dict:
        return {
            "uid": self.uid,
            "title": self.title,
            "source_type": self.source_type,
            "csv_path": self.csv_path,
            "delimiter": self.delimiter,
            "com_port": self.com_port,
            "baud_rate": self.baud_rate,
            "parity": self.parity,
            "stop_bits": self.stop_bits,
            "byte_size": self.byte_size,
            "idle_gap": self.idle_gap,
            "lab_id": self.lab_id.to_dict(),
            "mappings": [m.to_dict() for m in self.mappings],
            "template": self.template,
            "qc_expire_hours": self.qc_expire_hours,
            "tests": [t.to_dict() for t in self.tests],
            "manual_override": self.manual_override,
            "override_comment": self.override_comment,
            "maintenance": [t.to_dict() for t in self.maintenance],
            "last_position": self.last_position,
            "last_mtime": self.last_mtime,
            "last_result_file": self.last_result_file,
            "image_path": self.image_path,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Machine":
        return cls(
            uid=str(data.get("uid", "")),
            title=str(data.get("title", "")),
            source_type=str(data.get("source_type", "single_csv")),
            csv_path=str(data.get("csv_path", "")),
            delimiter=str(data.get("delimiter", ",")) or ",",
            com_port=str(data.get("com_port", "")),
            baud_rate=int(data.get("baud_rate", 9600)),
            parity=str(data.get("parity", "N")),
            stop_bits=float(data.get("stop_bits", 1.0)),
            byte_size=int(data.get("byte_size", 8)),
            idle_gap=float(data.get("idle_gap", 0.3)),
            lab_id=Selector.from_dict(data.get("lab_id", {})),
            mappings=[MethodMapping.from_dict(m)
                      for m in data.get("mappings", [])],
            template=str(data.get("template", "")),
            qc_expire_hours=float(data.get("qc_expire_hours", 24.0)),
            tests=[TestSpec.from_dict(t) for t in data.get("tests", [])],
            manual_override=str(data.get("manual_override", "")),
            override_comment=str(data.get("override_comment", "")),
            maintenance=[MaintTask.from_dict(t)
                         for t in data.get("maintenance", [])],
            last_position=int(data.get("last_position", 0)),
            last_mtime=float(data.get("last_mtime", 0.0)),
            last_result_file=str(data.get("last_result_file", "")),
            image_path=str(data.get("image_path", "")),
        )


@dataclass
class TestResult:
    __test__ = False  # tell pytest this is not a test class
    name: str
    value: Optional[float]          # corrected: what the verdict is based on
    in_spec: Optional[bool]  # None = no data / not numeric
    time: Optional[datetime]
    raw_value: Optional[float] = None   # as parsed, before the correction


@dataclass
class MachineEvaluation:
    status: str
    reason: str
    test_results: List[TestResult] = field(default_factory=list)
    last_seen: Optional[datetime] = None
    maintenance: List[dict] = field(default_factory=list)
    # The three things a lab reads at a glance, kept apart the way the old
    # LEM did: quality control, preventive maintenance, calibration.
    sub_statuses: dict = field(default_factory=dict)


@dataclass
class PrintResult:
    """One parsed device print: the Lab ID plus method → value."""
    lab_id: str = ""
    values: dict = field(default_factory=dict)

    def to_row(self, now: datetime) -> dict:
        row = {LAB_ID_KEY: self.lab_id}
        row.update(self.values)
        row["parsed_date"] = now.strftime("%Y-%m-%d")
        row["parsed_time"] = now.strftime("%H:%M:%S")
        return row


# ── PM / Calibration status (tasks are defined next to the config model) ─────

def maint_status(task: "MaintTask", today: date) -> tuple:
    """(status, reason) for one PM/Cal task — LEM's maintenance rules."""
    if not task.last_done:
        return STATUS_YELLOW, f"Not completed yet: {task.name}"
    try:
        last = date.fromisoformat(task.last_done)
    except ValueError:
        return STATUS_YELLOW, f"Not completed yet: {task.name}"
    next_due = last + timedelta(days=max(1, task.interval_days))
    if next_due < today:
        return STATUS_RED, f"Overdue: {task.name} (was due {next_due.isoformat()})"
    if next_due == today:
        return STATUS_YELLOW, f"Due today: {task.name}"
    return STATUS_GREEN, f"{task.name}: next due {next_due.isoformat()}"


# ── Serial framing: a report ends when the wire goes idle ────────────────────

def _decode_bytes(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


class FrameAssembler:
    """Assembles serial bytes into report frames split by idle gaps."""

    def __init__(self, idle_gap: float = 0.3) -> None:
        self.idle_gap = idle_gap
        self._buffer = b""
        self._last_feed: Optional[float] = None

    def feed(self, data: bytes, t: float) -> List[str]:
        """Add bytes arriving at time t (seconds). Returns any frames
        completed by the idle gap that preceded this data."""
        frames = []
        if (self._buffer and self._last_feed is not None
                and t - self._last_feed > self.idle_gap):
            frames.append(_decode_bytes(self._buffer))
            self._buffer = b""
        self._buffer += data
        self._last_feed = t
        return frames

    def flush(self) -> List[str]:
        """Force out whatever is buffered (e.g. after a poll's idle wait)."""
        if not self._buffer:
            return []
        frame = _decode_bytes(self._buffer)
        self._buffer = b""
        return [frame]

    def idle_since(self, t: float) -> bool:
        return (self._last_feed is not None
                and t - self._last_feed > self.idle_gap)


# ── The bench journal (transfer v4 §3) ───────────────────────────────────────
#
# THE BENCH'S OWN CUSTODY OF EVERY READING. Until v4 a reading lived in this
# module's memory from the moment it came off the instrument until LabCore
# accepted it, and a LabStation restart, a crash or a reboot at shift change in
# that window took it with no trace. Phase 1 measured it on the real v3.9.0
# module: kill a serial bench after the frames are read and before the log
# write, and three readings are in no store at all (K8); kill it while readings
# wait for their sample to be logged in, and three results never reach LabCore
# (K9). A serial frame has no other copy — the instrument does not print twice.
#
# So every reading is appended here, fsync'd, BEFORE anything downstream acts
# on it — before the log row, before the result, before the source is treated
# as consumed — and a restart re-delivers whatever the journal says was never
# delivered. The journal is plain files because LabStation intercepts
# `import sqlite3` in custom modules (transfer-map §5); stdlib only.
#
#   <labstation_dir()>/lem_journal/<uid>/
#     journal.meta      {"epoch","acked","durable","created","module",
#                        "last_v2_handshake", ...}           atomic replace
#     seg-000001.jsonl  records, one per line; a new segment at 4 MB
#     known.idx         replay-suppression keys of pruned segments
#     bench.key         the per-bench token (§6.4), written once
#     torn-*.bin        bytes cut off a torn tail, kept for a human
#
# A record is canonical JSON (sorted keys) with a CRC32 of exactly those bytes
# appended as its last field. Records are numbered per bench under an EPOCH,
# minted only when journal.meta is created: the server keeps one cursor per
# (uid, epoch), so a new epoch says "this bench numbers from 1 again" and must
# never happen for any reason other than the meta file being gone.

MODULE_VERSION = "4.0.0-dev"

JOURNAL_DIRNAME = "lem_journal"
JOURNAL_META_NAME = "journal.meta"
JOURNAL_KEY_NAME = "bench.key"
JOURNAL_KNOWN_NAME = "known.idx"
# A segment is the unit retention deletes. 4 MB is ten thousand ordinary
# readings: small enough that a pruned file is a few days of a busy bench,
# large enough that the folder never holds thousands of files.
JOURNAL_SEGMENT_BYTES = 4 * 1024 * 1024
# The bench is an independent second copy for this long after a server backup
# has the record (§3.4). Nothing younger is pruned except under disk pressure.
JOURNAL_RETENTION_DAYS = 30
JOURNAL_PRUNE_EVERY = timedelta(hours=1)
_MB = 1024 * 1024
# §3.4's disk policy. Warn at 200 MB not yet acknowledged by LEM, or at 80 % of
# a 500 MB budget. At 1 GB unacknowledged, or under 1 GB free on the disk,
# prune durable segments early; if that is not enough, PAUSE FILE INGEST — the
# instrument's file still holds those bytes. Serial and manual readings are
# never paused: they have no other copy. Nothing is ever dropped.
JOURNAL_LIMITS = {"warn_unacked": 200 * _MB, "budget": 500 * _MB,
                  "warn_fraction": 0.8, "pause_unacked": 1024 * _MB,
                  "min_free": 1024 * _MB}
# Windows refuses os.replace while another process (an antivirus scan, a
# backup agent, Explorer's preview) holds the target open — a sharing
# violation, surfaced as PermissionError, that clears in milliseconds.
REPLACE_RETRIES = 3
REPLACE_RETRY_SECONDS = 0.05

_SEGMENT_RE = re.compile(r"^seg-(\d{6,})\.jsonl$")
# `,"crc":"xxxxxxxx"}` then the newline. Anchored at the very end: only a
# complete line was ever written and fsync'd as a whole.
_CRC_TAIL = re.compile(rb',"crc":"([0-9a-f]{8})"\}\n\Z')
_DIGEST_ZERO = "0" * 64


class JournalError(Exception):
    """The journal could not do what was asked. Never swallowed into "empty":
    a journal that cannot be read is not a journal with nothing in it."""


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def journal_root() -> str:
    """Where every bench's journal lives. LEM_JOURNAL_DIR overrides it (the
    gate and tests point it at a temp folder); otherwise LabStation's own data
    directory, which survives LabStation updates."""
    override = os.environ.get("LEM_JOURNAL_DIR")
    if override:
        return override
    return os.path.join(labstation_dir(), JOURNAL_DIRNAME)


def journal_dir(machine_uid: str) -> str:
    return os.path.join(journal_root(), _sanitize_filename(machine_uid))


def canonical_body(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")


def journal_line(body: dict) -> bytes:
    """One record as it is written: the canonical body with its CRC32 appended
    as the last field. Another program can verify a line without this module:
    take the line, drop `,"crc":"…"` and the newline, close the brace, CRC it."""
    if "crc" in body:
        raise ValueError("a journal record may not carry its own 'crc' field")
    raw = canonical_body(body)
    if not raw.endswith(b"}") or raw == b"{}":
        raise ValueError("a journal record is a non-empty JSON object")
    return raw[:-1] + b',"crc":"%08x"}\n' % (zlib.crc32(raw) & 0xffffffff)


def _line_body(line: bytes) -> Optional[bytes]:
    """The canonical body bytes of a line whose CRC checks, else None."""
    m = _CRC_TAIL.search(line)
    if m is None:
        return None
    raw = line[:m.start()] + b"}"
    if (zlib.crc32(raw) & 0xffffffff) != int(m.group(1), 16):
        return None
    return raw


def parse_journal_line(line: bytes) -> Optional[dict]:
    """A record, or None if the line is torn, damaged or not a record. A
    flipped byte anywhere is None, never a different reading."""
    raw = _line_body(line)
    if raw is None:
        return None
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if (not isinstance(body, dict) or not isinstance(body.get("seq"), int)
            or not isinstance(body.get("epoch"), str) or not body.get("kind")):
        return None
    return body


def running_digest(previous_hex: str, body: bytes) -> str:
    """One step of the reconciliation digest (§3.1): sha256(previous || body).

    A chain rather than one long hash because both ends must carry it across
    a restart — the bench after old segments are pruned, the server between
    two syncs — and only a hex value can be saved. The server computes exactly
    this over the canonical bodies it stored, from d0 = 64 zeros."""
    return hashlib.sha256(bytes.fromhex(previous_hex) + body).hexdigest()


def _local_ts() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _poll_ts(now: datetime) -> str:
    """A poll's time as a record's `ts`: bench local time WITH its UTC offset
    (§3.2), so a record read on another machine is not an hour out."""
    try:
        return now.astimezone().isoformat(timespec="seconds")
    except (ValueError, OSError, OverflowError):
        return _local_ts()


# The module re-evaluates the disk policy at most this often: it lists the
# journal folder and asks the OS for free space.
JOURNAL_DISK_CHECK_SECONDS = 60.0

# ONE journal object per journal folder per process. Two module instances on
# one canvas bound to the same instrument would otherwise each number records
# from their own counter into the same files — two records with one seq, and a
# server that keeps the first of any (uid, epoch, seq) would silently drop the
# second. Shared, they number through one lock, and the second instance's
# re-read of the same file is suppressed by the first's keys instead of being
# logged twice. Released at module shutdown; a process that dies releases
# everything, which is the point of a journal.
_OPEN_JOURNALS: Dict[str, list] = {}
_OPEN_JOURNALS_LOCK = threading.Lock()


def acquire_journal(machine_uid: str) -> "BenchJournal":
    directory = journal_dir(machine_uid)
    key = os.path.normcase(os.path.realpath(directory))
    with _OPEN_JOURNALS_LOCK:
        entry = _OPEN_JOURNALS.get(key)
        if entry is not None:
            entry[1] += 1
            return entry[0]
        journal = BenchJournal(directory, machine_uid)
        _OPEN_JOURNALS[key] = [journal, 1]
        return journal


def release_journal(journal: "BenchJournal") -> None:
    with _OPEN_JOURNALS_LOCK:
        for key, entry in list(_OPEN_JOURNALS.items()):
            if entry[0] is journal:
                entry[1] -= 1
                if entry[1] <= 0:
                    del _OPEN_JOURNALS[key]
                    journal.close()
                return


def _fsync_dir(path: str, fsync=None) -> None:
    """Make a create/rename/delete in `path` durable. POSIX only: Windows has
    no way to open a directory for fsync, and NTFS journals the metadata."""
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        (fsync or os.fsync)(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write(path: str, data: bytes, fsync=None, mode: Optional[int] = None) -> None:
    """Write `.tmp`, fsync it, then os.replace — so a reader sees the old file
    or the new one, never half of either. A Windows sharing violation on the
    replace is retried REPLACE_RETRIES times, REPLACE_RETRY_SECONDS apart."""
    fsync = fsync or os.fsync
    tmp = path + ".tmp"
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
        fd = os.open(tmp, flags, mode if mode is not None else 0o666)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        for attempt in range(REPLACE_RETRIES + 1):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == REPLACE_RETRIES:
                    raise
                _sleep(REPLACE_RETRY_SECONDS)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(os.path.dirname(path) or ".", fsync)


class BenchJournal:
    """One bench's journal. Thread-safe: the serial reader thread appends
    frames while the poll worker appends readings.

    State kept in memory is only what a restart needs: the replay-suppression
    keys (`known`), the frames no poll has consumed yet, and the readings not
    yet both PROJECTED (their machine-log row landed) and SETTLED (the results
    road is done with them). Everything else is on disk."""

    def __init__(self, directory: str, uid: str,
                 segment_bytes: int = JOURNAL_SEGMENT_BYTES,
                 disk_usage=None, limits: Optional[dict] = None,
                 fsync=None) -> None:
        self.dir = directory
        self.uid = uid
        self.segment_bytes = int(segment_bytes)
        self.limits = dict(JOURNAL_LIMITS)
        self.limits.update(limits or {})
        self._disk_usage = disk_usage or shutil.disk_usage
        self._fsync = fsync or os.fsync
        self._lock = threading.RLock()
        self.notices: List[str] = []      # said once to the operator
        self.repairs: List[dict] = []     # torn tails cut on open
        self.corrupt_lines = 0            # damaged lines skipped (not torn)
        self.had_history = False          # records existed when opened
        self.recovery_done = False        # the module's re-delivery has run
        self.pause_files = False          # disk policy: file ingest paused
        self._known: set = set()
        self._frames: "OrderedDict[str, tuple]" = OrderedDict()
        self._runs: "OrderedDict[str, dict]" = OrderedDict()
        self._segments: List[dict] = []
        self._meta: dict = {}
        self.epoch = ""
        self._seq = 0
        self._frame_no = 0
        self.acked = 0
        self.durable = 0
        self._digest_at = (0, _DIGEST_ZERO)
        self._last_prune: Optional[datetime] = None
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            raise JournalError(f"cannot create the journal folder {directory}: "
                               f"{exc}") from exc
        with self._lock:
            self._open()

    # ── opening ──────────────────────────────────────────────────────────────

    def _meta_path(self) -> str:
        return os.path.join(self.dir, JOURNAL_META_NAME)

    def _segment_files(self) -> List[tuple]:
        out = []
        try:
            names = os.listdir(self.dir)
        except OSError as exc:
            raise JournalError(f"cannot list the journal folder: {exc}") from exc
        for name in names:
            m = _SEGMENT_RE.match(name)
            if m:
                out.append((int(m.group(1)), os.path.join(self.dir, name)))
        return sorted(out)

    def _read_meta(self) -> tuple:
        """("ok", dict) | ("missing", None) | ("bad", reason)."""
        path = self._meta_path()
        if not os.path.exists(path):
            return "missing", None
        try:
            with open(path, "rb") as f:
                meta = json.loads(f.read().decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return "bad", str(exc)
        if not isinstance(meta, dict) or not isinstance(meta.get("epoch"), str) \
                or not meta.get("epoch"):
            return "bad", "no epoch in it"
        return "ok", meta

    def _open(self) -> None:
        status, meta = self._read_meta()
        per_epoch_seq: Dict[str, int] = {}
        per_epoch_frame: Dict[str, int] = {}
        last_epoch = None
        segs = self._segment_files()
        for idx, (n, path) in enumerate(segs):
            seg = {"n": n, "path": path, "size": 0, "seqs": {}}
            self._segments.append(seg)
            for body in self._load_segment(seg, last=(idx == len(segs) - 1)):
                self.had_history = True
                ep, seq = body["epoch"], body["seq"]
                last_epoch = ep
                prev = per_epoch_seq.get(ep)
                if prev is not None and seq != prev + 1:
                    self.notices.append(
                        f"Journal records of epoch {ep} jump from seq {prev} "
                        f"to {seq}; the journal has been damaged.")
                per_epoch_seq[ep] = max(prev or 0, seq)
                if body.get("kind") == "frame":
                    per_epoch_frame[ep] = max(per_epoch_frame.get(ep, 0),
                                              int(body.get("frame_no") or 0))
                self._apply(body, seg)
        self._load_known()
        if status == "ok":
            self._meta = meta
        elif status == "missing":
            self._meta = {"epoch": secrets.token_hex(8), "acked": 0,
                          "durable": 0, "created": _local_ts(),
                          "module": MODULE_VERSION, "last_v2_handshake": None,
                          "uid": self.uid}
            self._write_meta()
        else:
            if last_epoch is None:
                raise JournalError(
                    f"journal.meta in {self.dir} cannot be read ({meta}) and "
                    "there are no records to rebuild it from; refusing to "
                    "guess this bench's epoch")
            # Rebuilt, not re-minted: the records say which epoch they are.
            # acked and durable restart at 0 — the conservative direction:
            # the next sync resends and the server dedupes; nothing is pruned
            # until a backup is confirmed again.
            self._meta = {"epoch": last_epoch, "acked": 0, "durable": 0,
                          "created": _local_ts(), "module": MODULE_VERSION,
                          "last_v2_handshake": None, "uid": self.uid,
                          "rebuilt": _local_ts()}
            self._write_meta()
            self.notices.append(
                f"This bench's journal.meta could not be read ({meta}); it was "
                f"rebuilt from the records (epoch {last_epoch}).")
        self.epoch = self._meta["epoch"]
        self.acked = int(self._meta.get("acked") or 0)
        self.durable = int(self._meta.get("durable") or 0)
        self._seq = max(per_epoch_seq.get(self.epoch, 0),
                        int(self._meta.get("pruned_seq") or 0))
        self._frame_no = max(per_epoch_frame.get(self.epoch, 0),
                             int(self._meta.get("pruned_frame") or 0))
        if self._meta.get("digest_seq"):
            self._digest_at = (int(self._meta["digest_seq"]),
                               str(self._meta.get("digest") or _DIGEST_ZERO))

    def _load_segment(self, seg: dict, last: bool):
        """Yield the segment's valid records. In the LAST segment the first bad
        line is a torn tail: everything from it on is cut and kept aside. In
        any other segment a bad line is damage: skipped, counted, said."""
        try:
            with open(seg["path"], "rb") as f:
                data = f.read()
        except OSError as exc:
            raise JournalError(f"cannot read {seg['path']}: {exc}") from exc
        pos = 0
        while pos < len(data):
            nl = data.find(b"\n", pos)
            line = data[pos:] if nl < 0 else data[pos:nl + 1]
            body = parse_journal_line(line)
            if body is None:
                if last:
                    self._cut_tail(seg, data, pos)
                    seg["size"] = pos
                    return
                self.corrupt_lines += 1
                self.notices.append(
                    f"A damaged line in {os.path.basename(seg['path'])} at byte "
                    f"{pos} was skipped; the records around it are intact.")
                pos += len(line)
                continue
            seg["seqs"][body["epoch"]] = max(seg["seqs"].get(body["epoch"], 0),
                                             body["seq"])
            yield body
            pos += len(line)
        seg["size"] = len(data)

    def _cut_tail(self, seg: dict, data: bytes, pos: int) -> None:
        cut = data[pos:]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        aside = os.path.join(self.dir, f"torn-{seg['n']:06d}-{stamp}-{pos}.bin")
        try:
            with open(aside, "wb") as f:
                f.write(cut)
                f.flush()
                self._fsync(f.fileno())
            with open(seg["path"], "r+b") as f:
                f.truncate(pos)
                f.flush()
                self._fsync(f.fileno())
        except OSError as exc:
            raise JournalError(f"cannot repair the torn tail of "
                               f"{seg['path']}: {exc}") from exc
        self.repairs.append({"segment": seg["n"], "at": pos,
                             "cut_bytes": len(cut), "kept_in": aside})
        self.notices.append(
            f"The journal's last write was cut short (power loss?): "
            f"{len(cut)} byte(s) were set aside in {os.path.basename(aside)} "
            "and the readings in them will be read again from the instrument.")

    def _load_known(self) -> None:
        path = os.path.join(self.dir, JOURNAL_KNOWN_NAME)
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    key = line.strip()
                    if key:
                        self._known.add(key)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise JournalError(f"cannot read {path}: {exc}") from exc

    def _apply(self, body: dict, seg: dict) -> None:
        kind = body.get("kind")
        ref = f"{body['epoch']}:{body['seq']}"
        if kind == "frame":
            self._frames[str(body.get("pk"))] = (str(body.get("text") or ""),
                                                 seg["n"])
        elif kind == "consumed":
            for pk in body.get("pks") or ():
                self._known.add(str(pk))
                self._frames.pop(str(pk), None)
        elif kind == "run":
            pk = body.get("pk")
            if pk:
                self._known.add(str(pk))
                self._frames.pop(str(pk), None)
            self._runs[ref] = {"ref": ref, "rec": body, "projected": False,
                               "settled": False, "segment": seg["n"]}
        elif kind in ("projected", "settled"):
            for r in body.get("of") or ():
                run = self._runs.get(str(r))
                if run is None:
                    continue
                run[kind] = True
                if run["projected"] and run["settled"]:
                    del self._runs[str(r)]

    def _write_meta(self) -> None:
        try:
            atomic_write(self._meta_path(), canonical_body(self._meta),
                         fsync=self._fsync)
        except OSError as exc:
            raise JournalError(f"cannot write journal.meta: {exc}") from exc

    # ── appending ────────────────────────────────────────────────────────────

    def _segment_for(self, nbytes: int) -> dict:
        last = self._segments[-1] if self._segments else None
        if last is not None and (last["size"] == 0
                                 or last["size"] + nbytes <= self.segment_bytes):
            return last
        n = (last["n"] + 1) if last else 1
        path = os.path.join(self.dir, "seg-%06d.jsonl" % n)
        try:
            with open(path, "ab"):
                pass
        except OSError as exc:
            raise JournalError(f"cannot create {path}: {exc}") from exc
        _fsync_dir(self.dir, self._fsync)
        seg = {"n": n, "path": path, "size": 0, "seqs": {}}
        self._segments.append(seg)
        return seg

    def append(self, records: List[dict], ts: Optional[str] = None) -> List[str]:
        """Append records and fsync ONCE. Returns their refs ("epoch:seq").

        Nothing is appended for an empty list — an idle poll costs the disk
        nothing. A failure leaves neither a partial line nor a hole in the
        numbering: the file is cut back and the error raised."""
        if not records:
            return []
        with self._lock:
            seq = self._seq
            stamp = ts or _local_ts()
            bodies, lines = [], []
            for rec in records:
                body = dict(rec)
                seq += 1
                body["seq"] = seq
                body["epoch"] = self.epoch
                body["uid"] = self.uid
                body.setdefault("ts", stamp)
                body.setdefault("module", MODULE_VERSION)
                lines.append(journal_line(body))
                bodies.append(body)
            data = b"".join(lines)
            seg = self._segment_for(len(data))
            start = seg["size"]
            try:
                with open(seg["path"], "ab") as f:
                    f.write(data)
                    f.flush()
                    # A process kill here leaves the bytes in the OS cache and
                    # they reach the disk; a power loss may tear them (T5).
                    globals()["fault_point"]("after_journal_before_fsync")
                    self._fsync(f.fileno())
            except Exception as exc:
                self._cut_back(seg, start)
                raise JournalError(f"journal append failed: {exc}") from exc
            seg["size"] = start + len(data)
            self._seq = seq
            seg["seqs"][self.epoch] = seq
            for body in bodies:
                if body.get("kind") == "frame":
                    self._frame_no = max(self._frame_no,
                                         int(body.get("frame_no") or 0))
                self._apply(body, seg)
            return [f"{self.epoch}:{b['seq']}" for b in bodies]

    def _cut_back(self, seg: dict, size: int) -> None:
        try:
            with open(seg["path"], "r+b") as f:
                f.truncate(size)
        except OSError:
            # The next open finds the partial line and cuts it as a torn tail.
            pass

    def append_frame(self, text: str, ts: Optional[str] = None) -> str:
        """Journal one completed serial frame. Returns its replay key
        (epoch, frame number) — numbers are never reused within an epoch."""
        with self._lock:
            n = self._frame_no + 1
            pk = f"f:{self.epoch}:{n}"
            self.append([{"kind": "frame", "pk": pk, "frame_no": n,
                          "text": text}], ts=ts)
            return pk

    def mark_projected(self, refs) -> List[str]:
        return self._mark("projected", refs)

    def mark_settled(self, refs) -> List[str]:
        return self._mark("settled", refs)

    def _mark(self, kind: str, refs) -> List[str]:
        with self._lock:
            todo = []
            for ref in refs or ():
                run = self._runs.get(str(ref))
                if run is not None and not run[kind] and str(ref) not in todo:
                    todo.append(str(ref))
            if not todo:
                return []
            self.append([{"kind": kind, "of": todo}])
            return todo

    # ── what a restart needs ─────────────────────────────────────────────────

    def known(self, pk) -> bool:
        with self._lock:
            return bool(pk) and str(pk) in self._known

    def pending_frames(self) -> List[tuple]:
        """Frames journaled and never consumed by a poll, oldest first."""
        with self._lock:
            return [(pk, text) for pk, (text, _n) in self._frames.items()]

    def open_runs(self) -> List[dict]:
        """Readings not yet both projected and settled, oldest first."""
        with self._lock:
            return [{"ref": r["ref"], "rec": r["rec"],
                     "projected": r["projected"], "settled": r["settled"]}
                    for r in self._runs.values()]

    def next_seq(self) -> int:
        with self._lock:
            return self._seq + 1

    def set_acked(self, acked: int, durable: Optional[int] = None) -> None:
        with self._lock:
            self.acked = int(acked)
            if durable is not None:
                self.durable = int(durable)
            self._meta["acked"] = self.acked
            self._meta["durable"] = self.durable
            self._write_meta()

    def note_v2_handshake(self, when: Optional[str] = None) -> None:
        with self._lock:
            self._meta["last_v2_handshake"] = when or _local_ts()
            self._write_meta()

    # ── reading back ─────────────────────────────────────────────────────────

    def _scan(self) -> List[dict]:
        """Every valid record on disk, in file order."""
        out = []
        with self._lock:
            for seg in self._segments:
                try:
                    with open(seg["path"], "rb") as f:
                        data = f.read(seg["size"])
                except OSError as exc:
                    raise JournalError(f"cannot read {seg['path']}: {exc}") from exc
                for line in data.splitlines(True):
                    body = parse_journal_line(line)
                    if body is not None:
                        out.append(body)
        return out

    def digest(self, through: Optional[int] = None) -> str:
        """The reconciliation digest of this epoch's records 1..`through`
        (default: acked). See `running_digest`."""
        with self._lock:
            through = self.acked if through is None else int(through)
            base_seq, base = self._digest_at
            pruned_seq = int(self._meta.get("digest_seq") or 0)
            if through < base_seq:
                if through < pruned_seq:
                    raise JournalError(
                        f"records up to seq {pruned_seq} have been pruned; the "
                        f"digest through {through} can no longer be computed")
                base_seq = pruned_seq
                base = str(self._meta.get("digest") or _DIGEST_ZERO)
            d, seq = base, base_seq
            for seg in self._segments:
                if seg["seqs"].get(self.epoch, 0) <= base_seq:
                    continue
                with open(seg["path"], "rb") as f:
                    data = f.read(seg["size"])
                for line in data.splitlines(True):
                    raw = _line_body(line)
                    if raw is None:
                        continue
                    body = json.loads(raw.decode("utf-8"))
                    if body.get("epoch") != self.epoch:
                        continue
                    s = body.get("seq")
                    if not isinstance(s, int) or s <= seq or s > through:
                        continue
                    d, seq = running_digest(d, raw), s
            if seq != through and through > 0:
                raise JournalError(f"the journal does not hold seq {seq + 1}"
                                   f"..{through} of epoch {self.epoch}")
            self._digest_at = (seq, d)
            return d

    # ── bench.key ────────────────────────────────────────────────────────────

    def read_bench_key(self) -> Optional[str]:
        path = os.path.join(self.dir, JOURNAL_KEY_NAME)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip() or None
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise JournalError(f"cannot read bench.key: {exc}") from exc

    def write_bench_key(self, token: str) -> None:
        """Written ONCE, at enrolment, readable only by this user. A second
        write is a re-enrolment — an admin decision on the server, not this
        file's to make."""
        path = os.path.join(self.dir, JOURNAL_KEY_NAME)
        with self._lock:
            if os.path.exists(path):
                raise JournalError("bench.key already exists; re-enrolment "
                                   "needs an admin on the LEM server")
            try:
                atomic_write(path, (str(token).strip() + "\n").encode("utf-8"),
                             fsync=self._fsync, mode=0o600)
            except OSError as exc:
                raise JournalError(f"cannot write bench.key: {exc}") from exc

    # ── retention and disk ───────────────────────────────────────────────────

    def _segment_busy(self, seg: dict) -> bool:
        n = seg["n"]
        return (any(r["segment"] == n for r in self._runs.values())
                or any(sn == n for _t, sn in self._frames.values()))

    def prune(self, now: datetime, early: bool = False) -> int:
        """Delete whole segments the bench no longer needs to hold: every
        record in them is in a completed server backup (<= durable), nothing
        in them is still owed to LabCore or unread by a poll, and they are
        older than JOURNAL_RETENTION_DAYS (`early` lifts the age test only, for
        the disk policy). Only a prefix is pruned, and the segment being
        written to never is. Their replay keys go to known.idx first."""
        removed = 0
        with self._lock:
            limit = now.timestamp() - JOURNAL_RETENTION_DAYS * 86400
            for seg in list(self._segments[:-1]):
                epochs = seg["seqs"]
                if set(epochs) - {self.epoch}:
                    break     # an older epoch's records: durability unknown
                if epochs.get(self.epoch, 0) > self.durable:
                    break
                if self._segment_busy(seg):
                    break
                try:
                    mtime = os.path.getmtime(seg["path"])
                except OSError:
                    break
                if not early and mtime > limit:
                    break
                keys, last_seq, last_frame = [], 0, 0
                with open(seg["path"], "rb") as f:
                    data = f.read(seg["size"])
                for line in data.splitlines(True):
                    body = parse_journal_line(line)
                    if body is None:
                        continue
                    last_seq = max(last_seq, body["seq"])
                    if body.get("kind") == "run" and body.get("pk"):
                        keys.append(str(body["pk"]))
                    elif body.get("kind") == "consumed":
                        keys.extend(str(k) for k in body.get("pks") or ())
                    elif body.get("kind") == "frame":
                        last_frame = max(last_frame, int(body.get("frame_no") or 0))
                d = self.digest(last_seq) if last_seq else None
                if keys:
                    with open(os.path.join(self.dir, JOURNAL_KNOWN_NAME), "ab") as f:
                        f.write("".join(k + "\n" for k in keys).encode("utf-8"))
                        f.flush()
                        self._fsync(f.fileno())
                if last_seq:
                    self._meta["pruned_seq"] = max(
                        int(self._meta.get("pruned_seq") or 0), last_seq)
                    self._meta["digest_seq"] = last_seq
                    self._meta["digest"] = d
                if last_frame:
                    self._meta["pruned_frame"] = max(
                        int(self._meta.get("pruned_frame") or 0), last_frame)
                self._write_meta()
                os.remove(seg["path"])
                self._segments.remove(seg)
                removed += 1
            if removed:
                _fsync_dir(self.dir, self._fsync)
        return removed

    def disk_state(self) -> dict:
        with self._lock:
            journal_bytes = sum(s["size"] for s in self._segments)
            unacked = sum(s["size"] for s in self._segments
                          if s["seqs"].get(self.epoch, 0) > self.acked
                          or set(s["seqs"]) - {self.epoch})
            try:
                free = int(self._disk_usage(self.dir).free)
            except (OSError, AttributeError, TypeError, ValueError):
                free = None
            lim = self.limits
            hard = (unacked >= lim["pause_unacked"]
                    or (free is not None and free < lim["min_free"]))
            warn = (unacked >= lim["warn_unacked"]
                    or journal_bytes >= lim["budget"] * lim["warn_fraction"])
            return {"journal_bytes": journal_bytes, "unacked_bytes": unacked,
                    "free_bytes": free, "hard": hard,
                    "level": "pause" if hard else ("warn" if warn else "ok")}

    def enforce_disk_policy(self, now: datetime) -> dict:
        """§3.4, applied: prune what retention allows (hourly); under disk
        pressure prune durable segments early; if that is not enough, pause
        FILE ingest. Returns the state with a sentence for the operator."""
        with self._lock:
            if (self._last_prune is None
                    or now - self._last_prune >= JOURNAL_PRUNE_EVERY):
                self._last_prune = now
                self.prune(now)
            st = self.disk_state()
            st["pruned_early"] = 0
            if st["hard"]:
                st["pruned_early"] = self.prune(now, early=True)
                st = dict(self.disk_state(), pruned_early=st["pruned_early"])
            self.pause_files = st["hard"]
            st["pause_files"] = st["hard"]
            mb = st["unacked_bytes"] / _MB
            parts = []
            if st["hard"]:
                st["level"] = "pause_files"
                why = (f"{mb:.0f} MB not yet confirmed by LEM"
                       if st["unacked_bytes"] >= self.limits["pause_unacked"]
                       else "the disk is nearly full")
                parts.append(
                    f"Bench journal: {why} — reading the instrument FILE is "
                    "paused (the file keeps the data); serial and typed "
                    "readings are still recorded. Nothing is lost.")
            elif st["level"] == "warn":
                parts.append(
                    f"Bench journal: {mb:.0f} MB waiting for LEM to confirm it "
                    "(nothing is lost; check this PC can reach LEM).")
            if st["free_bytes"] is None:
                parts.append("Free disk space on this PC is unknown (the check "
                             "failed); the journal keeps recording.")
            st["message"] = " ".join(parts)
            return st

    def close(self) -> None:
        """Nothing is held open between appends; kept for symmetry."""
        return None


# ── The machine universe: one standardized event log per machine ─────────────
#
# Everything the machine does lands in lem_machine_log so the LEM web app
# can open a machine's "room" and present its full history. Kinds:
# run | qc | status_change | override | comment | pm | calibration

LOG_TABLE_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_machine_log ("
    "machine_uid TEXT, ts TEXT, kind TEXT, "
    "lab_id TEXT, test_name TEXT, value TEXT, detail TEXT)"
)

# ── and the indexes it never had ────────────────────────────────────────────
#
# This is the only lem_* table with no primary key and no index, it is never
# pruned, and everything above lands in it. That would be an ordinary
# performance smell anywhere else. Here it is a lab-wide outage waiting to
# happen, because of where LabCore's database lives.
#
# LabCore's SQLite file is on an SMB share and cannot move. WAL is unusable on a
# share, so the journal mode is DELETE, so a concurrent reader blocks the
# writer's commit — which is why `read_sql` is SERIALISED THROUGH THE WRITE
# QUEUE (LabCore.py:13180). A slow read of ours therefore does not inconvenience
# us; it blocks every write in the building for as long as it runs. LabCore then
# interrupts any read that outruns `read_watchdog_s` (8.0s), and its own comment
# names "an unindexed scan over the SMB share" as the hazard. That scan is this
# table. As it grows, `LAST_QC_QUERY` crosses eight seconds, LabCore kills it,
# every bench retries, the queue deepens, and the lab reads it as "LabCore is
# offline" while LabCore is perfectly healthy.
#
#   * (ts DESC) serves the web server's `ORDER BY ts DESC LIMIT n` — sixty rows
#     read off the end of an index instead of the whole table sorted.
#   * (machine_uid, kind, ts DESC) is the exact shape of LAST_QC_QUERY: seek to
#     this bench's QC rows, walk them newest-first, stop at 400. It also covers
#     the web server's `GROUP BY machine_uid`.
#
# `CREATE INDEX IF NOT EXISTS` is idempotent, so the web server declaring the
# same two is harmless and correct — whichever process starts first creates
# them.
#
# CREATING THEM IS ITSELF A QUEUE OP, and on a log that has grown for a year
# over SMB the FIRST one may be slow: SQLite has to read the whole table across
# the share and sort it. It happens once, on the first process to start after
# this ships, and every read of the table afterwards is the thing it buys. It is
# declared inside `_declare_tables`, so a LabCore that refuses it because its
# queue is deep backs off exactly like a refused table rather than being retried
# on every poll of every bench.
#
# The index DDL must be declared AFTER LOG_TABLE_DDL in that block: CREATE INDEX
# on a table that does not exist yet is an error, and an error there backs the
# whole block off and leaves `_labcore_table_ready` down for the process.
LOG_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_lem_log_ts "
    "ON lem_machine_log(ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_lem_log_uid_kind_ts "
    "ON lem_machine_log(machine_uid, kind, ts DESC)",
)

# ── The machine log is the promise every other cap on this road makes ────────
#
# Read the drop notices on the results road and they all end the same way: the
# reading "stays in the machine log". That sentence is the whole justification
# for HELD_ROW_LIMIT, for IDENTITY_BACKLOG_LIMIT, for the parked cap and for the
# seven-day expiry — none of them is a loss, because the record went to
# lem_machine_log the moment the print was parsed (ISO/IEC 17025:2017 §7.5.1).
#
# It was not true. The queue those records wait in was `deque(maxlen=200)` and
# `_queue_run_events` appends ONE PER PARSED ROW, so a poll parsing more than
# two hundred prints — an ordinary first run of a multi-CSV bench over an
# archive folder — evicted the oldest records before anything wrote them, in
# silence, while the status line said they were filed. Worse, the results road's
# own `held_expired` events went into the SAME two hundred slots, so announcing
# that a reading had been given up on could destroy the record it pointed at.
# `_ingest_multi` has already moved the source file into processed/ by then, so
# nothing re-reads it. Measured on the real module: one 3,000-print poll with
# the LIMS behind left 200 readings in no store at all.
#
# Two changes make the sentence true, and they are deliberately belt AND braces
# because this is the only custody of last resort in the file.
#
#   • ORDER. The queue is drained BEFORE the results road runs (see
#     `_drain_events`), so a reading's record is in LabCore before any cap on
#     that road can decide to stop waiting for its sample. A `held_expired`
#     event can no longer land in front of the record it describes.
#   • ROOM, and an alarm when there is none. The bound below is far above what
#     one poll can produce — a poll's events drain in the same poll, so the
#     steady state is one poll's worth — and a record already accepted is never
#     thrown away to make room for a newer one. If the bound is ever reached the
#     refusal is COUNTED and said out loud through `_report_loss`. A record may
#     be refused; it may not vanish quietly.
#
# Twenty thousand events is a few megabytes of SQL strings at the very worst,
# on a bench that has just read a weekend of archived prints, and it is back to
# nothing one poll later.
LOG_EVENT_LIMIT = 20000

# Records per INSERT when the queue drains. Seven columns, so a hundred rows is
# seven hundred bound parameters — comfortably under the 999 that an older
# SQLite host allows in one statement, which is the only ceiling here that
# belongs to somebody else. Raising it buys less and less (a 3,000-print import
# is already thirty ops at this size) while walking towards a limit whose
# failure mode is the whole batch being rejected for a reason that has nothing
# to do with the lab.
LOG_BATCH_ROWS = 100


LOG_INSERT_SQL = ("INSERT INTO lem_machine_log "
                  "(machine_uid, ts, kind, lab_id, test_name, value, detail) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?)")


class _LogEntry(tuple):
    """A queued machine-log record — `(sql, args)` like every other entry —
    that also knows which journal record it belongs to, so the drain can tell
    the journal when that reading's record has landed."""
    ref = None


def _log_entry(args: list, ref: Optional[str]) -> "_LogEntry":
    entry = _LogEntry((LOG_INSERT_SQL, list(args)))
    entry.ref = ref
    return entry


def build_log_insert(machine_uid: str, kind: str, ts: datetime,
                     lab_id: str = "", test_name: str = "",
                     value: str = "", detail: Optional[dict] = None) -> tuple:
    sql = LOG_INSERT_SQL
    # A name that is ENTIRELY whitespace is stored as '', because that is what
    # it means: no method was named. This is the write half of the pair that
    # let LAST_QC_QUERY stop calling TRIM (see there) — the read predicate can
    # be answerable from an index OR it can call a function on every candidate
    # row, and on a table read over SMB through the lab's write queue it has to
    # be the former. Normalising the one value the two predicates disagreed
    # about, at the single choke point every log row passes through, makes them
    # equivalent for everything this module writes.
    #
    # Deliberately NOT a plain .strip(). `carry_last_qc` looks a verdict back up
    # by `spec.name`, and a spec name can carry padding — `specs_for_machine`
    # takes the method string from the mapping as the operator wrote it. Storing
    # "Sulphur" against a spec called " Sulphur " would mean the bench lost the
    # verdict it had just recorded, and went YELLOW for QC it had passed. Only
    # the case that can never be a lookup key is normalised.
    name = str(test_name or "")
    if not name.strip():
        name = ""
    args = [machine_uid, ts.isoformat(), kind, lab_id, name,
            str(value), json.dumps(detail or {})]
    return sql, args


def refusal_reason(result) -> str:
    """LabCore's refusal of an op, or "" if it went through.

    THE SINGLE MOST EXPENSIVE MISTAKE ON THIS ROAD IS TREATING A REFUSAL AS A
    WRITE. LabCore serialises its write queue at roughly 1.5 ops/sec and turns
    new work away past ~100 pending with `{"error": ..., "busy": true,
    "retry_after": n}` — an error DICT, returned normally, not an exception.
    The web server's checklist import learned this the hard way: its loop
    counted the rejections as successes and reported "imported 3094" while
    nothing landed (notes.md, "Writes are the opposite story").

    Every write path in this file had grown its own copy of the isinstance
    check, and the one path that had not was `_drain_events` — the queue every
    other cap's "they stay in the machine log" points at. One function now, so
    the next road that writes has something to call rather than a pattern to
    remember.
    """
    if isinstance(result, dict) and result.get("error"):
        return str(result["error"]) or "LabCore refused the write"
    return ""


def retry_after_seconds(result) -> Optional[float]:
    """How long LabCore asked to be left alone, or None if it did not say.

    The other half of a refusal, and the half every caller here used to throw
    away. LabCore refuses with `{"error": ..., "busy": true, "retry_after": n}`
    and `n` is seconds; notes.md's standing rule for a bulk write is to honour
    it and back off. A caller that ignores it re-fires into the queue that has
    just reported it is too deep, which is the load the refusal was asking to be
    spared.

    None rather than a default, so the caller can fall back to its OWN schedule
    — a wait this function invented would be indistinguishable from one LabCore
    asked for, and the two mean different things.

    Everything unusable reads as "it did not say". A server on another schedule
    may answer with a string, a null, a negative number or a NaN, and none of
    those may be allowed to park a bench's declarations for a shift — or, worse,
    for no time at all. Note that `nan > 0` is False, which is what excludes it.
    """
    if not isinstance(result, dict):
        return None
    value = result.get("retry_after")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if not (value > 0) or value == float("inf"):
        return None
    return value


def build_log_batch(records: List[list]) -> tuple:
    """One multi-row INSERT for many lem_machine_log records.

    A poll's records all have the same seven columns, so they go as one op
    instead of one op each. That is notes.md's standing rule (c) for any bulk
    write — "batch rows into multi-row INSERTs to keep the op count down" — and
    here it is what makes the durability claim affordable: an archive import of
    three thousand prints is thirty ops rather than three thousand, which fits
    inside a queue that refuses past a hundred pending instead of guaranteeing
    it hits the limit.

    Rows per statement are bounded by LOG_BATCH_ROWS, not by the poll, because
    the bound that matters is the host's parameter limit rather than anything
    about the bench; see LOG_BATCH_ROWS.
    """
    if not records:
        return "", []
    values = ", ".join(["(?, ?, ?, ?, ?, ?, ?)"] * len(records))
    sql = ("INSERT INTO lem_machine_log "
           "(machine_uid, ts, kind, lab_id, test_name, value, detail) "
           "VALUES " + values)
    args: List = []
    for record in records:
        args.extend(record)
    return sql, args


def machine_scoped_qc_rows(rows: List[dict], machine_uid: str) -> List[dict]:
    """The `lem_qc_specs` rows written FOR this machine, and only those.

    An unscoped row is a value somebody stored, not an assignment to a bench.
    A manual bench takes such rows as its assignment (it has no mapping to
    carry one), so adopting the unscoped ones would be the automatic detection
    that put Multitek NS on RED all over again.
    """
    return [r for r in rows
            if str(r.get("machine_uid") or "").strip() == machine_uid]


def specs_for_machine(machine: "Machine",
                      library: List["TestSpec"]) -> List["TestSpec"]:
    """Join the machine's QC-marked mappings with LabCore's spec library.

    The mapping says WHICH QC sample applies and how long QC lasts; the
    library (from LabCore) says what the method's expected / std-dev / k
    are. Mappings without a QC sample, or methods missing from the
    library, produce no spec.

    A manual bench has no mappings at all, so a row written for it IS the
    assignment and is taken whole — provided it names the standard it is
    checked against, since that Lab ID is the only thing identifying what was
    run. Rows are scoped to the machine by `machine_scoped_qc_rows` first.
    """
    if machine.source_type == "manual":
        return [s for s in library if s.sample_id.strip()]
    by_name = {}
    for spec in library:
        by_name.setdefault(spec.name, spec)
    specs: List[TestSpec] = []
    seen = set()
    for mapping in machine.mappings:
        if not mapping.qc_sample_id:
            continue
        for method in mapping.methods:
            lib = by_name.get(method)
            if lib is None or method in seen:
                continue
            seen.add(method)
            # `lem_qc_specs` rows carry no window of their own — they are a
            # human's per-machine BAND override, not a statement about the
            # material — so only the mapping can speak at this level. The
            # standard's window still applies to specs resolved the other way,
            # in `specs_from_qc_samples`.
            hours, source = spec_qc_window(mapping.qc_expire_hours, 0.0)
            specs.append(TestSpec(
                name=method, value_col=method,
                expected=lib.expected, std_dev=lib.std_dev, k=lib.k,
                units=lib.units, sample_id=mapping.qc_sample_id,
                qc_expire_hours=hours, qc_expire_source=source))
    return specs


# ── Latest-result temp file (in LabStation's own data directory) ─────────────

LATEST_RESULT_PREFIX = "lem_latest_"

# Multi-CSV: files land in the watched folder and are moved here once read,
# so "anything still in the folder" is exactly the unprocessed queue.
PROCESSED_DIRNAME = "processed"


def _unique_path(directory: str, name: str) -> str:
    """A non-colliding path in `directory` for `name` (run.csv → run_2.csv)."""
    candidate = os.path.join(directory, name)
    if not os.path.exists(candidate):
        return candidate
    stem, ext = os.path.splitext(name)
    counter = 2
    while True:
        candidate = os.path.join(directory, f"{stem}_{counter}{ext}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def labstation_dir() -> str:
    """LabStation's data directory. Deployed installs (LabLink launcher)
    live at %APPDATA%\\LabLink\\apps\\LabStation — the folder holding the
    versioned 0.0.x subfolders — so the file survives updates. The source
    layout (%LOCALAPPDATA%\\LabLink\\LabStation) is the fallback."""
    candidates = []
    roaming = os.environ.get("APPDATA")
    if roaming:
        candidates.append(os.path.join(roaming, "LabLink", "apps",
                                       "LabStation"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, "LabLink", "LabStation"))
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    if candidates:
        return candidates[0]
    return os.path.join(os.path.expanduser("~"), "AppData", "Roaming",
                        "LabLink", "apps", "LabStation")


def _sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()
    return name or "machine"


def latest_result_filename(machine_title: str) -> str:
    return f"{LATEST_RESULT_PREFIX}{_sanitize_filename(machine_title)}.csv"


def write_latest_result(row: dict, machine_title: str,
                        directory: Optional[str] = None) -> str:
    """Overwrite (never append) a one-row CSV named after the machine with
    the latest parsed result: machine, Lab ID, each method value, and the
    parse timestamp. Written atomically so a reader never sees a
    half-written file."""
    directory = directory or labstation_dir()
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, latest_result_filename(machine_title))
    methods = [k for k in row if k not in RESERVED_ROW_KEYS]
    header = ["machine", LAB_ID_KEY, *methods, "parsed_date", "parsed_time"]
    values = ([machine_title, str(row.get(LAB_ID_KEY, ""))]
              + [str(row.get(m, "")) for m in methods]
              + [str(row.get("parsed_date", "")),
                 str(row.get("parsed_time", ""))])
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerow(values)
    os.replace(tmp_path, path)
    return path


# ── Config export / import ───────────────────────────────────────────────────
#
# For setting up several identical machines, or surviving a reinstall,
# without redoing the mapping work. Identity and runtime state never
# travel: the importing instance keeps its own uid, fresh ingest offsets,
# and no inherited override.

# ── Equipment configuration, held on the server ──────────────────────────────
#
# A machine's setup used to live only in this module instance, so a LabStation
# reinstall lost it and an identical second instrument had to be built by hand.
# `lem_machine_config` is the store now — the same table the web server's
# machine_configs.py owns — which is why config files are gone: one source of
# truth, not two.

CONFIG_TABLE_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_machine_config ("
    "machine_uid TEXT PRIMARY KEY, title TEXT NOT NULL, config TEXT, "
    "updated_at TEXT, updated_by TEXT)"
)

# Where this instrument is up to, and what an operator has forced. Per-machine
# facts, not configuration: they stay when a machine saves ITSELF, and are
# dropped when a config is COPIED to another machine. Must match
# machine_configs.RUNTIME_KEYS on the server.
CONFIG_RUNTIME_KEYS = frozenset({
    "last_position",
    "last_mtime",
    "last_result_file",
    "manual_override",
    "override_comment",
})


def _fresh_uid() -> str:
    return uuid.uuid4().hex[:12]


def build_config_upsert(machine: Machine, now: datetime,
                        by: str = "") -> tuple:
    """SQL to publish this machine's own configuration."""
    if not (machine.uid or "").strip():
        raise ValueError("A configuration needs a machine uid.")
    if not (machine.title or "").strip():
        raise ValueError("A configuration needs a machine name.")
    sql = ("INSERT INTO lem_machine_config (machine_uid, title, config, "
           "updated_at, updated_by) VALUES (?, ?, ?, ?, ?) "
           "ON CONFLICT(machine_uid) DO UPDATE SET title=excluded.title, "
           "config=excluded.config, updated_at=excluded.updated_at, "
           "updated_by=excluded.updated_by")
    args = [machine.uid, machine.title.strip(),
            json.dumps(machine.to_dict()),
            now.isoformat(timespec="seconds"), by]
    return sql, args


def build_config_list_query() -> str:
    """Names only — the picker must not drag every mapping in the lab down."""
    return ("SELECT machine_uid, title, updated_at, updated_by "
            "FROM lem_machine_config ORDER BY title")


def build_config_fetch(machine_uid: str) -> tuple:
    return ("SELECT machine_uid, title, config FROM lem_machine_config "
            "WHERE machine_uid = ?", [machine_uid])


def build_config_delete(machine_uid: str) -> tuple:
    return ("DELETE FROM lem_machine_config WHERE machine_uid = ?",
            [machine_uid])


# A module counts as live if it beat within this window — a couple of missed
# beats' grace before another module is warned the config is in use. Matches
# MachineStateReader.HEARTBEAT_GRACE on the server.
HEARTBEAT_GRACE_SECONDS = 900


LAST_QC_QUERY = (
    # DESC, not ASC: with `ORDER BY ts ASC LIMIT 400` this asked for the OLDEST
    # 400 verdicts, so on any machine past 400 the "most recent" verdict it
    # recovered was ancient history.
    #
    # `test_name != ''`, not `TRIM(test_name) != ''`. An index can answer a
    # question about a stored value; it cannot answer one about the result of a
    # function applied to it, so every candidate row had to be fetched and TRIM
    # run on it before the row could be ruled in or out. On a table this size,
    # read over SMB through a queue that serialises every write in the lab, that
    # is work nobody can afford — see LOG_INDEX_DDL for what a slow read here
    # actually costs.
    #
    # `TRIM(x) != ''` excluded one thing this does not: a test_name that is
    # ENTIRELY whitespace. That exclusion has not been dropped, it has moved to
    # the writer — `build_log_insert` stores a whitespace-only name as '' — so
    # the two predicates agree on every row this module writes, and the read
    # gets to use the index. `IS NOT NULL` is redundant against SQLite's
    # three-valued logic (NULL != '' is NULL, which is not true) and is stated
    # anyway, because "we did not think about NULL" and "NULL is excluded on
    # purpose" should not look the same in a query an auditor may read.
    "SELECT test_name, value, ts, detail FROM lem_machine_log "
    "WHERE machine_uid = ? AND kind = 'qc' "
    "AND test_name IS NOT NULL AND test_name != '' "
    "ORDER BY ts DESC LIMIT 400"
)


def build_last_qc_query(machine_uid: str) -> tuple:
    """This machine's QC verdicts, oldest first so the newest simply wins."""
    return LAST_QC_QUERY, [machine_uid]


LAST_CALIBRATION_QUERY = (
    # DESC from the start. LAST_QC_QUERY shipped as ASC and therefore recovered
    # the OLDEST row of its window for months; the same mistake here would date
    # every QC verdict to the bench's first-ever calibration.
    "SELECT ts FROM lem_machine_log "
    "WHERE machine_uid = ? AND kind = 'calibration' "
    "ORDER BY ts DESC LIMIT 1"
)


def build_last_calibration_query(machine_uid: str) -> tuple:
    """When this machine was last calibrated — the epoch a QC reading belongs to.

    The machine log is the only place that answers this for the machine rather
    than for a scheduled task: `lem_maintenance.last_done` is a date somebody
    typed against a recurring job, and a bench can be calibrated without one.
    """
    return LAST_CALIBRATION_QUERY, [machine_uid]


def known_text(value) -> Optional[str]:
    """A string somebody actually supplied, or None for "we do not know".

    The distinction the whole provenance record turns on. `""` is counted as a
    value by anything doing a set-of-strings tally, so a blank operator becomes
    "one analyst ran all of these" — which is precisely the repeatability /
    reproducibility confusion these fields exist to make impossible.
    """
    text = str(value or "").strip()
    return text or None


def last_calibration_id(rows) -> Optional[str]:
    """Rows from build_last_calibration_query() → the epoch, or None.

    Compares timestamps rather than trusting the row order, for the reason
    `last_qc_by_test` does: the ORDER BY lives in another function and has been
    wrong before. Never raises — the caller is on the poll worker, where a
    raise strands `_polling`.
    """
    newest = None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        at = known_text(row.get("ts"))
        if at and (newest is None or at > newest):
            newest = at
    return newest


def last_qc_by_test(rows) -> dict:
    """Rows from build_last_qc_query() → {test_name: {at, value, in_spec}}.

    Compares timestamps rather than trusting the row order. It used to rely on
    "oldest first, so later rows overwrite earlier ones", which silently inverted
    the moment the query was fixed to fetch the NEWEST 400 instead of the oldest.
    A rule this important should not depend on an ORDER BY somewhere else.
    """
    out: dict = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("test_name") or "").strip()
        if not name:
            continue
        value = _safe_float(row.get("value"))
        if value is None:
            continue          # a verdict with no readable number tells us nothing
        try:
            detail = json.loads(row.get("detail") or "{}")
            if not isinstance(detail, dict):
                detail = {}
        except (TypeError, ValueError):
            detail = {}       # unreadable blob: keep the value, lose the verdict
        at = str(row.get("ts") or "")
        if name in out and at <= out[name]["at"]:
            continue                      # we already have a newer verdict
        out[name] = {"at": at, "value": value,
                     "in_spec": (None if detail.get("in_spec") is None
                                 else bool(detail.get("in_spec")))}
    return out


def carry_last_qc(new_specs: List[TestSpec],
                  old_specs: List[TestSpec]) -> List[TestSpec]:
    """Keep what we already remembered when the spec list is refreshed.

    Specs are rebuilt from LabCore on every sync with blank last_qc fields. Left
    alone that would (a) throw away the verdict a restart depends on and (b) make
    the "did the specs change?" comparison true every single poll.
    """
    remembered = {s.name: s for s in old_specs or []}
    for spec in new_specs or []:
        was = remembered.get(spec.name)
        if was is None:
            continue
        # The correction is carried even with no remembered verdict: it is
        # configuration, not history, and dropping it for one poll would let an
        # uncorrected reading decide pass/fail.
        if was.correction:
            spec.correction = was.correction
        if not was.last_qc_at:
            continue
        spec.last_qc_at = was.last_qc_at
        spec.last_qc_value = was.last_qc_value
        spec.last_qc_in_spec = was.last_qc_in_spec
    return new_specs


def apply_last_qc(machine: Machine, latest: dict) -> bool:
    """Stamp what LabCore remembers onto this machine's specs.

    Returns whether anything actually changed. The caller re-evaluates on that, and
    without it an instrument still awaiting its first standard re-evaluated on every
    single sync forever — the write was guarded, but the work was not.
    """
    changed = False
    for spec in machine.tests:
        found = (latest or {}).get(spec.name) or (latest or {}).get(spec.value_col)
        if not found:
            continue
        at = str(found.get("at") or "")
        value, in_spec = found.get("value"), found.get("in_spec")
        if (spec.last_qc_at, spec.last_qc_value, spec.last_qc_in_spec) == \
                (at, value, in_spec):
            continue
        spec.last_qc_at, spec.last_qc_value, spec.last_qc_in_spec = \
            at, value, in_spec
        changed = True
    return changed


def build_heartbeat_query() -> str:
    """Who is checking in — so the picker can say which configs are already
    being run by another module."""
    return "SELECT machine_uid, last_poll FROM lem_machine_heartbeat"


def live_uids(rows, now: datetime,
             grace: int = HEARTBEAT_GRACE_SECONDS) -> set:
    """Machines with a fresh heartbeat.

    A beat dated in the future is ignored: benches disagree about the clock,
    and skew must not mark the whole lab live.
    """
    live = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        uid = str(row.get("machine_uid") or "").strip()
        if not uid:
            continue
        try:
            seen = datetime.fromisoformat(str(row.get("last_poll") or ""))
        except (TypeError, ValueError):
            continue
        age = (now - seen).total_seconds()
        if 0 <= age <= grace:
            live.add(uid)
    return live


def config_choices(rows, live=()) -> List[dict]:
    """Rows from build_config_list_query() → entries for the startup picker.
    Junk is dropped rather than allowed to break the only way in."""
    live = set(live or ())
    out: List[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        uid = str(row.get("machine_uid") or "").strip()
        if not uid:
            continue
        title = str(row.get("title") or "").strip() or f"Untitled ({uid})"
        out.append({"machine_uid": uid, "title": title,
                    "updated_at": str(row.get("updated_at") or ""),
                    "updated_by": str(row.get("updated_by") or ""),
                    "in_use": uid in live})
    return out


def machine_from_config_payload(payload, machine_uid: str) -> Machine:
    """Turn a stored blob into a Machine bound to the row it came from."""
    if isinstance(payload, dict):
        data = payload
    else:
        try:
            data = json.loads(payload or "{}")
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(
                f"That machine's stored configuration is unreadable: {exc}"
            ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            "That machine's stored configuration is not a config object.")
    machine = Machine.from_dict(data)
    machine.uid = machine_uid
    return machine


def config_was_deleted(result) -> bool:
    """Did LabCore definitively say this machine's configuration is gone?

    LabCore owns the configuration — nothing is stored on this PC — so a config
    deleted from the floor means this module has none and must stop.

    The dangerous half is telling "the row is gone" apart from "I could not
    ask". Only an explicitly successful read that returned no rows counts:
    treating an outage as a deletion would wipe every module's setup in the lab
    at once, and a heartbeat gap is normal.
    """
    if not isinstance(result, dict):
        return False
    if result.get("error"):
        return False
    if result.get("ok") is not True:
        return False
    rows = result.get("rows")
    if not isinstance(rows, list):
        return False
    return len(rows) == 0


def new_machine_config(title: str) -> Machine:
    """A brand-new instrument: named, registered, nothing configured yet."""
    title = (title or "").strip()
    if not title:
        raise ValueError("A new machine needs a name.")
    return Machine(uid=_fresh_uid(), title=title)


def duplicated_machine(source: Machine, title: str) -> Machine:
    """Clone a setup onto a new instrument, leaving the original alone.

    Runtime state is dropped: a copy that inherited the source's byte offset
    would skip its own file, and one that inherited a SERVICE override would
    start dead.
    """
    title = (title or "").strip()
    if not title:
        raise ValueError("A duplicate needs a name.")
    data = {k: v for k, v in source.to_dict().items()
            if k not in CONFIG_RUNTIME_KEYS}
    machine = Machine.from_dict(data)
    machine.uid = _fresh_uid()
    machine.title = title
    return machine


# ── Clean-text tools (stackable) ─────────────────────────────────────────────

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

_MATH_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}
_MATH_MAX_POW = 16


def _eval_math_node(node, x: float) -> float:
    if isinstance(node, ast.Expression):
        return _eval_math_node(node.body, x)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name) and node.id == "x":
        return x
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Pow):
            exponent = _eval_math_node(node.right, x)
            if abs(exponent) > _MATH_MAX_POW:
                raise ValueError("exponent too large")
            return _eval_math_node(node.left, x) ** exponent
        if type(node.op) in _MATH_BINOPS:
            return _MATH_BINOPS[type(node.op)](
                _eval_math_node(node.left, x),
                _eval_math_node(node.right, x))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        val = _eval_math_node(node.operand, x)
        return -val if isinstance(node.op, ast.USub) else val
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "round" and not node.keywords
            and 1 <= len(node.args) <= 2):
        val = _eval_math_node(node.args[0], x)
        if len(node.args) == 2:
            return round(val, int(_eval_math_node(node.args[1], x)))
        return float(round(val))
    raise ValueError("disallowed math expression")


def _run_math_op(expr: str, value: str) -> str:
    """Evaluate a data-handler math op on the extracted value (as x).
    Non-numeric input or a disallowed expression leaves the value as-is."""
    try:
        x = float(value.strip())
    except (TypeError, ValueError):
        return value
    try:
        tree = ast.parse(expr.strip(), mode="eval")
        result = _eval_math_node(tree, x)
    except (ValueError, SyntaxError, TypeError, ZeroDivisionError,
            OverflowError):
        return value
    return f"{result:g}"


def apply_clean(value: str, ops: List[str]) -> str:
    """Apply clean-text ops in order (stackable). Unknown ops are ignored.

    Ops: strip · collapse_ws · keep_number · remove:<text> ·
    purge_text (drop letters) · purge_symbols (drop punctuation) ·
    math:<expr> (safe math on the value as x, e.g. math:round(x*1000, 1))."""
    for op in ops:
        if op == "strip":
            value = value.strip()
        elif op == "collapse_ws":
            value = re.sub(r"\s+", " ", value).strip()
        elif op == "keep_number":
            m = _NUMBER_RE.search(value)
            value = m.group(0) if m else ""
        elif op == "purge_text":
            value = re.sub(r"[A-Za-z]+", "", value)
            value = re.sub(r"\s+", " ", value).strip()
        elif op == "purge_symbols":
            value = re.sub(r"[^0-9A-Za-z.\-\s]", "", value)
            value = re.sub(r"\s+", " ", value).strip()
        elif op.startswith("remove:"):
            value = value.replace(op[len("remove:"):], "")
        elif op.startswith("math:"):
            value = _run_math_op(op[len("math:"):], value)
    return value


# ── Extraction (cell selection / text detection) ─────────────────────────────

def split_cells(text: str, delimiter: str) -> List[str]:
    """Flatten a print into cells: each line split by the delimiter."""
    cells: List[str] = []
    for line in text.splitlines():
        cells.extend(line.split(delimiter))
    return cells


def extract_value(selector: Selector, text: str, delimiter: str) -> str:
    if selector.mode == "detect":
        try:
            m = re.search(selector.pattern, text)
        except re.error:
            return ""
        if not m:
            return ""
        raw = m.group(1) if m.groups() else m.group(0)
    else:
        cells = split_cells(text, delimiter)
        raw = cells[selector.index] if 0 <= selector.index < len(cells) else ""
    return apply_clean(raw, selector.clean)


def build_detection_pattern(sample: str,
                            capture: str = "number") -> Optional[str]:
    """Turn a marked piece of real data into a text-detection pattern —
    no regex knowledge needed.

    capture="number": "Cloud point : -15.0°C" → the number after that label.
    capture="text":   "Sample ID : 36873" → whatever token follows the label
                      (works for alphanumeric IDs like 26-00412).
    A label-only sample ("Cloud point :") anchors on the whole sample and
    captures what follows it. Flexible about spacing either way."""
    sample = (sample or "").strip()
    if not sample:
        return None
    m = _NUMBER_RE.search(sample)
    label = sample[:m.start()].strip() if m else sample
    if not label:
        return None
    flexible_label = re.sub(r"(\\?\s)+", r"\\s*", re.escape(label))
    if capture == "text":
        return flexible_label + r"\s*(\S+)"
    return flexible_label + r"\s*(-?\d+(?:\.\d+)?)"


def parse_print(machine: Machine, text: str) -> PrintResult:
    """Parse one device print via the machine's mappings. Empty extractions
    are omitted; a group of methods all receive the same value.

    Several mappings may target the SAME methods as alternates (e.g. two
    cloud-point detections for report variants) — the first one that
    extracts a value wins; the others are simply not needed."""
    lab_id = extract_value(machine.lab_id, text, machine.delimiter).strip()
    values: dict = {}
    for mapping in machine.mappings:
        value = extract_value(mapping.selector, text, machine.delimiter).strip()
        if not value:
            continue
        for method in mapping.methods:
            values.setdefault(method, value)
    return PrintResult(lab_id=lab_id, values=values)


# ── Manual entry: QC on the bench with no parser ─────────────────────────────
#
# An older instrument that prints to paper (or to nothing) has no file, no
# folder and no wire, so there is nothing to capture and nothing to map. What it
# does have is an operator reading a number off a dial.
#
# This is a QC panel, not a data-entry form. The ONLY thing enterable is a
# reading for a test the master view has assigned, and the standard's Lab ID
# comes from that assignment rather than from anybody's typing — Ryan: "this is
# only to put in the QC result. Nothing else, if there is no QC assigned then it
# can't put any data in." An unassigned bench is therefore inert, which is the
# honest state and the one that makes it impossible to fill with results nobody
# can check.
#
# Everything past the row — corrections, the QC verdict, the LabCore write, the
# 'qc' log event, the card, the live push — is the parsed path, unchanged.

def manual_entry_specs(machine: Machine) -> List[TestSpec]:
    """The QC tests this bench can be given a reading for.

    The assigned specs, and only those. A spec naming no standard is skipped:
    its Lab ID is what the reading is logged against and what `evaluate_machine`
    matches on, so without one there is nothing to record the check as.
    """
    return [spec for spec in (machine.tests or [])
            if str(spec.name).strip() and spec.sample_id.strip()]


def manual_qc_row(spec: Optional[TestSpec], value: str,
                  now: datetime) -> Optional[dict]:
    """One operator-typed QC reading, in the shape `parse_print` produces.

    The Lab ID is the standard's, off the assignment — there is no box for it,
    because a box is a way to log a good reading against the wrong standard.

    A blank box is silence rather than an empty result. So is anything that is
    not a number: a QC result exists to be compared with a band, and "ok" put in
    the record is a reading nobody can ever judge.
    """
    if spec is None or not spec.sample_id.strip():
        return None
    text = str(value if value is not None else "").strip()
    if not text or _safe_float(text) is None:
        return None
    return PrintResult(lab_id=spec.sample_id.strip(),
                       values={spec.name: text}).to_row(now)


def apply_csv_headers(row: dict, machine: Machine) -> dict:
    """Rename/merge a parsed row's method columns for the CSV export:
    methods whose mapping declares a csv_header collapse into ONE column
    under that name (first non-empty value wins); everything else keeps
    its method name. Lab ID and timestamps pass through untouched."""
    header_for = {}
    for mapping in machine.mappings:
        if mapping.csv_header:
            for method in mapping.methods:
                header_for.setdefault(method, mapping.csv_header)
    out: dict = {}
    for key, value in row.items():
        if key in RESERVED_ROW_KEYS:
            out[key] = value
            continue
        name = header_for.get(key, key)
        if name in out:
            if not out[name] and value:
                out[name] = value
            continue
        out[name] = value
    return out


# ── QC specs — pulled from LabCore, never defined in the module ──────────────

def parse_qc_specs(rows: List[dict], machine_uid: str) -> List[TestSpec]:
    """Turn lem_qc_specs rows into TestSpecs for this machine. Rows scoped
    to another machine, or with a missing/bad shape, are skipped."""
    specs: List[TestSpec] = []
    for row in rows:
        name = str(row.get("test_name") or "").strip()
        scope = str(row.get("machine_uid") or "").strip()
        if not name or (scope and scope != machine_uid):
            continue
        try:
            specs.append(TestSpec(
                name=name,
                value_col=name,
                expected=float(row.get("expected")),
                std_dev=float(row.get("std_dev")),
                k=float(row.get("k", 2.0)),
                units=str(row.get("units") or ""),
                sample_id=str(row.get("sample_id") or ""),
            ))
        except (TypeError, ValueError):
            continue
    return specs


# ── QC samples: the shared standards library, stored in LabCore ──────────────
#
# The master view keeps named QC standards (CRMs) in `lem_qc_samples`: a Lab
# ID plus the tests it certifies. Every module pulls that library and detects
# QC on its own — when a print's Lab ID matches a standard, the methods this
# machine parses are checked against that standard's specs. No per-machine QC
# wiring, and one place to update a CRM's values for the whole lab.

QC_SAMPLES_QUERY = "SELECT name, sample_id_val, tests FROM lem_qc_samples"

# The master view can pin exactly which sample + test this instrument is
# checked against; with none assigned the parser detects on its own.
QC_TARGETS_QUERY = ("SELECT sample_name, test_name FROM lem_machine_targets "
                    "WHERE machine_uid = ?")

# PM and calibration schedules are set in the master view, not here — a task
# that only exists on one bench is invisible to whoever plans the work.
MAINTENANCE_QUERY = ("SELECT uid, name, kind, interval_days, last_done, note "
                     "FROM lem_maintenance WHERE machine_uid = ?")


def parse_maint_rows(rows: List[dict]) -> List["MaintTask"]:
    """Turn `lem_maintenance` rows into the tasks the evaluator understands."""
    tasks = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        try:
            interval = int(row.get("interval_days") or 30)
        except (TypeError, ValueError):
            interval = 30
        tasks.append(MaintTask(
            uid=str(row.get("uid") or ""), name=name,
            kind="calibration" if "cal" in str(row.get("kind") or "").lower()
                 else "pm",
            interval_days=max(1, interval),
            last_done=str(row.get("last_done") or ""),
            note=str(row.get("note") or "")))
    return tasks


def parse_qc_sample_rows(rows: List[dict]) -> List[dict]:
    """Turn `lem_qc_samples` rows (tests held as JSON) into the library."""
    library = []
    for row in rows:
        try:
            tests = json.loads(row.get("tests") or "[]")
            if not isinstance(tests, list):
                tests = []
        except (TypeError, ValueError):
            tests = []
        library.append({
            "name": str(row.get("name") or ""),
            "sample_id_val": str(row.get("sample_id_val") or ""),
            "tests": tests,
        })
    return library


def specs_from_qc_samples(machine: Machine, library: List[dict],
                          targets: Optional[List[dict]] = None
                          ) -> List[TestSpec]:
    """Derive this machine's QC specs from the shared standards.

    **Assignment only.** `targets` are the master view's assignments (V4's
    watched targets) and nothing outside them is checked. No targets means no
    specs, which reads as grey "No QC assigned".

    This used to detect on its own when no targets existed: any method the parser
    produced that some shared standard happened to certify became a live QC spec.
    That put Multitek NS on RED for a Sulfur check nobody had assigned to it — and
    since there was no assignment, there was nothing to hang a correction factor
    on either. Changed 2026-08-03 at Ryan's request: "make it all manual and skip
    the automatic detection".

    A QC test matches a method by its measurement column (`value_col`) or by its
    own name, so definitions carried over from the old LEM still resolve.
    """
    wanted = {(str(t.get("sample") or "").strip(),
               str(t.get("test") or "").strip().lower())
              for t in (targets or [])}
    specs: List[TestSpec] = []
    seen = set()

    # A manual bench has no mappings, and nothing else on it declares what it
    # reports — so the assignment IS the declaration. Without this a machine
    # created for manual QC could never be given any, and "create the machine,
    # assign the QC in LEM later" would not work. Still assignment-only: the
    # `wanted` filter below is the same one, just not gated behind a parser.
    if machine.source_type == "manual":
        for sample in library:
            lab_id = str(sample.get("sample_id_val") or "").strip()
            if not lab_id:
                continue
            sample_name = str(sample.get("name") or "").strip()
            for test in sample.get("tests") or []:
                value_col = str(test.get("value_col") or "").strip()
                test_name = str(test.get("name") or "").strip()
                names = {value_col.lower(), test_name.lower()}
                if not any((sample_name, n) in wanted for n in names):
                    continue
                # The measurement column is the LabCore method, and so the name
                # the entered result is written under.
                method = value_col or test_name
                if not method or method in seen:
                    continue
                seen.add(method)
                # A manual bench has no mappings, so the standard is the only
                # level below the machine that can say anything at all.
                hours, source = spec_qc_window(
                    0.0, test.get("qc_expire_hours"))
                specs.append(TestSpec(
                    name=method, value_col=method,
                    expected=float(test.get("expected") or 0.0),
                    std_dev=float(test.get("std_dev") or 0.0),
                    k=float(test.get("k") or 2.0),
                    units=str(test.get("units") or ""),
                    sample_id=lab_id,
                    qc_expire_hours=hours, qc_expire_source=source))
        return specs

    for mapping in machine.mappings:
        for method in mapping.methods:
            if method in seen:
                continue
            key = method.strip().lower()
            for sample in library:
                lab_id = str(sample.get("sample_id_val") or "").strip()
                if not lab_id:
                    continue
                sample_name = str(sample.get("name") or "").strip()
                for test in sample.get("tests") or []:
                    names = {str(test.get("value_col") or "").strip().lower(),
                             str(test.get("name") or "").strip().lower()}
                    # Always filtered, never "only if there are targets" — that
                    # `if wanted:` was the automatic detection.
                    if not any((sample_name, n) in wanted for n in names):
                        continue
                    if key not in names or not key:
                        continue
                    seen.add(method)
                    # The mapping's override is an explicit human act on THIS
                    # instrument, so it still wins; the standard's own window is
                    # the level under it. `spec_qc_window` owns that order —
                    # this used to be a bare `mapping.qc_expire_hours`, which
                    # left the library nothing to say.
                    hours, source = spec_qc_window(
                        mapping.qc_expire_hours, test.get("qc_expire_hours"))
                    specs.append(TestSpec(
                        name=method, value_col=method,
                        expected=float(test.get("expected") or 0.0),
                        std_dev=float(test.get("std_dev") or 0.0),
                        k=float(test.get("k") or 2.0),
                        units=str(test.get("units") or ""),
                        # An explicit QC sample on the mapping wins — the
                        # machine runs its own standard under that Lab ID.
                        sample_id=mapping.qc_sample_id.strip() or lab_id,
                        qc_expire_hours=hours, qc_expire_source=source))
                    break
                if method in seen:
                    break
    return specs


# ── How long a QC result stays good, and who gets to say ────────────────────
#
# `qc_is_stale` below is the rule; this is where the NUMBER comes from. Four
# levels can supply it, and this block is the ONLY place in this file that
# decides which one does:
#
#   1. MethodMapping.qc_expire_hours   — a human act on THIS instrument
#   2. the standard's own window       — from `lem_qc_samples` (2026-08-26)
#   3. Machine.qc_expire_hours         — this instrument's default
#   4. QC_WINDOW_DEFAULT_HOURS
#
# Level 2 is the addition. A control's usable life is a property of the
# MATERIAL — a working standard degrades, an ampoule opened this morning is not
# good for a week — so it belongs on the standard, once, rather than being
# re-typed on every bench that runs it and lost on the next lot change.
#
# **Zero means "fall through", never "expire immediately."** That is already how
# `MethodMapping.qc_expire_hours` and `TestSpec.qc_expire_hours` read, and it is
# what makes this safe to ship into a running lab: every row now in
# `lem_qc_samples` carries no window, and a floor on an older build will not send
# one either. Absence read as a zero-hour window would make every reading in the
# building stale the moment it was taken.

QC_WINDOW_DEFAULT_HOURS = 24.0


def _window_hours(raw) -> float:
    """A usable window in hours, or 0.0 meaning "this level said nothing".

    Everything that is not a finite positive number is silence: None, "", text,
    NaN, inf, negatives. NaN matters more than it looks — it compares False
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


def resolve_qc_window(levels, default_hours: float = QC_WINDOW_DEFAULT_HOURS):
    """The QC staleness window, and WHICH level supplied it.

    `levels` is an ordered sequence of `(source, hours)`, most specific first.
    The first level with something to say wins; everything else is silence.

    The source travels with the number on purpose. With four levels able to
    supply it, "24 hours" alone stopped being an answer anybody can act on —
    the operator needs to know whether to change the standard, the mapping or
    the machine. The floor reports the same pair as `qc_expire_source`.

    A caller that has not reached the bottom of the chain passes
    `default_hours=0.0`: `spec_qc_window` knows the mapping and the standard but
    not the instrument, and answering 24.0 there would silently override every
    machine default in the lab.

    Mirrors `qc_samples.resolve_qc_window` in the web server, which cannot be
    imported here — LabStation loads this file on its own.
    """
    for source, raw in levels or ():
        hours = _window_hours(raw)
        if hours:
            return hours, str(source)
    return float(default_hours), "default"


def spec_qc_window(mapping_hours, standard_hours):
    """What a SPEC asserts about its own life: the mapping, then the standard.

    `(0.0, "")` means neither said anything, which is the spec staying silent so
    the machine default can decide later — see `qc_window_for`.
    """
    hours, source = resolve_qc_window(
        (("mapping", mapping_hours), ("standard", standard_hours)),
        default_hours=0.0)
    return (hours, source) if hours else (0.0, "")


def qc_window_for(spec, machine):
    """The window actually applied to one test, and the level it came from.

    The bottom half of the chain: whatever the spec asserts (already resolved
    between the mapping and the standard), then the machine, then the shared
    default. Called wherever a window is needed — the verdict, the battery — so
    no call site re-implements the order.

    A spec carrying a window but no recorded level is reported as `"spec"`: that
    is a `Machine` persisted before the standard's window existed, and its
    number is honoured without claiming a provenance nobody recorded.
    """
    return resolve_qc_window((
        (getattr(spec, "qc_expire_source", "") or "spec",
         getattr(spec, "qc_expire_hours", 0.0) if spec is not None else 0.0),
        ("machine", getattr(machine, "qc_expire_hours", 0.0)),
    ))


def qc_is_stale(result_time: Optional[datetime], now: datetime,
                hours: float) -> bool:
    """Has this QC result aged out? A **rolling** window from when it was run.

    Changed from calendar-day 2026-08-03, at Ryan's call. V4 expired QC at the day
    boundary, which meant a standard run at 23:00 was stale at 00:01 — an hour
    later — while one run at 00:30 lasted almost 48. A window called "24 hours"
    has to be 24 hours.

    The only input is the timestamp on the result itself, which is why this
    survives both a restart and a move to another PC: nothing is measured from
    when the module started, and the timestamp comes from LabCore keyed on the
    machine, not from anything local.
    """
    if result_time is None:
        return False
    return (now - result_time).total_seconds() >= max(0.0, hours) * 3600.0


def qc_freshness(machine: Machine, test_result: Optional[TestResult],
                 now: datetime,
                 expire_hours: Optional[float] = None) -> float:
    """Remaining share (0.0–1.0) of one test's QC window — the battery fill.

    Mirrors the rolling staleness rule in evaluate_machine: an in-spec result
    decays over `expire_hours` from when it was run; no/out-of-spec data = 0.
    `expire_hours` is the window the SPEC asserts (the mapping override or the
    standard's own); it falls through to the machine and then to the shared
    default through `resolve_qc_window`, so the battery and the verdict cannot
    disagree about how long a result lasts.
    """
    if test_result is None or not test_result.in_spec or test_result.time is None:
        return 0.0
    # `expire_hours or machine.qc_expire_hours` left 0 when both were 0, which
    # made the window 1e-9 seconds and the battery permanently empty on a
    # machine saved without a default.
    hours, _source = resolve_qc_window(
        (("spec", expire_hours), ("machine", machine.qc_expire_hours)))
    window = max(1e-9, hours * 3600.0)
    elapsed = (now - test_result.time).total_seconds()
    return max(0.0, min(1.0, (window - elapsed) / window))


def format_relative_time(then: Optional[datetime], now: datetime) -> str:
    """Compact "ago" text for the machine card, e.g. "11 min., 53 secs. ago"."""
    if then is None:
        return "—"
    total = max(0, int((now - then).total_seconds()))
    if total < 10:
        return "just now"
    if total < 60:
        return f"{total} secs. ago"
    minutes, seconds = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} min., {seconds} secs. ago"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} hr., {minutes} min. ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"


# ── File tailing ─────────────────────────────────────────────────────────────

def tail_new_text(path: str, last_position: int) -> tuple:
    """Read text appended since last_position (byte offset).

    If the file shrank (rotated/truncated), restart from the beginning.
    Returns (new_text, new_position).
    """
    data, _start, new_position, _identity = _read_tail(path, last_position)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    return text, new_position


def _read_tail(path: str, last_position: int) -> tuple:
    """(bytes appended since last_position, the offset they start at, the new
    position, the file's identity). If the file shrank (rotated/truncated),
    from the beginning. The one place a tailed file is read."""
    size = os.path.getsize(path)
    if last_position > size:
        last_position = 0
    with open(path, "rb") as f:
        identity = file_identity(os.fstat(f.fileno()))
        f.seek(last_position)
        data = f.read()
    return data, last_position, last_position + len(data), identity


class _Print(str):
    """A device print that carries its journal replay key.

    A str in every other respect, so everything downstream of the ingest —
    the parser, the template capture, the recent-prints list — treats it
    exactly as before. `pk` is what the journal's store check compares; `src`
    and `lh` say what a file line's key was made of (transfer v4 §4.1);
    `origin` is "ambiguous" for a line the rewrite resolver recorded although
    it may repeat one (§4.2)."""
    pk = None
    src = None
    lh = None
    origin = "live"


def _keyed(text: str, pk: Optional[str], src: Optional[str] = None,
           lh: Optional[str] = None) -> "_Print":
    out = _Print(text)
    out.pk, out.src, out.lh = pk, src, lh
    return out


_BYTE_LINE = re.compile(rb"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")


def file_identity(st) -> str:
    """Which FILE this is, as opposed to which path (§4.1's file_id): device
    and inode, plus the creation time where the platform keeps one. A file
    rotated away and replaced by a new one under the same name is a different
    file, and its lines are new readings even where they repeat the old file's
    bytes at the same offsets (X2: the same QC standards printed again at the
    top of a new file). Windows has kept a real st_ino since Python 3.5; on a
    filesystem that reports 0 the creation time (st_ctime there) still tells
    two files apart."""
    birth = getattr(st, "st_birthtime", None)
    if birth is None and os.name == "nt":
        birth = st.st_ctime
    return "%s:%s:%s" % (st.st_dev, st.st_ino,
                         "" if birth is None else repr(float(birth)))


def file_line_key(src: str, offset: int, part: int, line_hash: str) -> str:
    """The replay key of one line of a tailed file: WHERE it is (the file —
    path and identity — and the byte offset the line starts at) and WHAT it is
    (its hash). A restarted bench re-reading its file from a stale offset
    meets the same line at the same offset — the same key, not a new reading.
    An instrument that prints the same sample and value again writes it at a
    NEW offset — a new key, a new reading, as it must be.

    The key only ever SUPPRESSES what the offset logic has already decided to
    read; it never causes a read. So when it cannot recognise a line (a file
    replaced by a copy of itself), the bench does what it did before v4."""
    return hashlib.sha256(f"{src}\0{offset}\0{part}\0{line_hash}".encode(
        "utf-8")).hexdigest()[:32]


def tail_new_lines(path: str, last_position: int) -> tuple:
    """`tail_new_text`, line by line and keyed: ([(offset, part, text,
    line_hash)], new_position, file identity) for every non-blank line
    appended since `last_position`. The texts are exactly `tail_new_text`'s
    `text.splitlines()` minus the blank ones, so the parser sees what it
    always saw; `offset` is the byte at which the line starts."""
    data, last_position, new_position, identity = _read_tail(path,
                                                             last_position)
    try:
        data.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        encoding = "cp1252"
    out = []
    for m in _BYTE_LINE.finditer(data):
        chunk = m.group()
        if not chunk:
            continue
        body = chunk.rstrip(b"\r\n")
        line_hash = hashlib.sha256(body).hexdigest()[:32]
        text = chunk.decode(encoding, errors="replace")
        for part, piece in enumerate(text.splitlines()):
            if piece.strip():
                out.append((last_position + m.start(), part, piece, line_hash))
    return out, new_position, identity


# ── Source readers: the cursor, quiescence and the rewrite resolver ─────────
#
# Transfer v4 §4. A tailed file usually only grows, and then a byte offset is
# all a bench needs. Instruments also rewrite the whole file, trim its head,
# correct a line in place, keep it newest-first, or rotate it away and start a
# new one. v3.9 met every one of those with "the offset is past the end, start
# again from 0" and re-logged whole files; its offset lived in LabCore and was
# saved only when somebody pressed OK in Settings, so every clean restart
# replayed the file from wherever that was (Phase 1: K6 30 duplicate rows for
# one restart, R2 12 lost and 12 doubled, R3 5 doubled, R5 a lost reading plus
# two stray Lab IDs). The pieces here:
#
#   cursor.json     where the bench is up to in each source: offset, a hash of
#                   the head and of the bytes just before the offset, the
#                   file's identity, and a lineage that names this run of
#                   offsets. Saved after the journal's fsync on every poll that
#                   consumed anything — never before (§3.3 (a) then (b)).
#   snapshot.bin    the ordered 16-byte hashes of the lines consumed, O.
#   quiescence      a change the cursor cannot explain is resolved only once
#                   the file has stopped changing (2 polls, 10 s), so a poll
#                   in the middle of a whole-file rewrite decides nothing (Q1).
#   the resolver    an ORDERED diff of O against the file now, N. Not a count
#                   of identical lines: counting is what lost X1 and X2.
#
# `lem_machine_config.last_position` is no longer read. It is still mirrored
# onto the machine (and so published) for a bench rolled back to v3.9.

CURSOR_NAME = "cursor.json"
SNAPSHOT_NAME = "snapshot.bin"
_SNAPSHOT_MAGIC = b"LEMSNAP1\n"
# The cursor's two windows (§4.1): the head catches a file replaced by another
# that happens to be longer, the tail catches an edit just before the offset.
CURSOR_HEAD_BYTES = 4096
CURSOR_TAIL_BYTES = 256
# Quiescence: unchanged size, mtime and identity across two polls at least
# this far apart on the bench's clock. Also the wait before an unterminated
# last line is taken as complete.
QUIET_SECONDS = 10.0
# A file that never goes quiet (an instrument that saves on every poll) would
# hold its change forever. After this long waiting, the bench reads the part
# of the file that has held still — byte-identical to what a poll at least
# QUIET_SECONDS earlier saw — and says on the status line that it is waiting
# for the rest. A whole-file rewrite takes seconds, not a minute, so inside
# this cap a poll mid-rewrite (Q1) is still never taken for a trim.
QUIET_MAX_WAIT = timedelta(seconds=60)
# How many earlier looks at a still-changing file are kept to compare against.
HELD_LOOKS = 4
# Every 15 minutes a bench on the append path hashes the whole file against
# the snapshot (files up to 32 MB): a same-size edit outside both windows is
# invisible to the cursor's hashes and would otherwise never be seen (R6deep).
RESCAN_EVERY = timedelta(minutes=15)
RESCAN_MAX_BYTES = 32 * 1024 * 1024
# The ordered diff's wall-clock budget. Measured for the spec at 0.01–0.03 s
# on 80,000 lines for every ordinary shape; it is quadratic only on periodic
# content, which is caught before it starts.
REWRITE_DIFF_BUDGET = 2.0
# When distinct lines are under this share of what is left after stripping
# (and there are at least PERIODIC_MIN_LINES), the content is periodic: the
# diff could align it many ways, and the tail anchor is used instead.
PERIODIC_DISTINCT_FRACTION = 0.05
PERIODIC_MIN_LINES = 100
# A rotation repeating more than this many of the old file's last lines, in
# order, at the top of the new one is not plausible: that is a trim.
ROTATION_OVERLAP_MAX = 20
# Lines left unstripped at the end of a common suffix, so the diff still sees
# where the old file's newest lines really are (see `_ordered_opcodes`).
SUFFIX_STRIP_MARGIN = 32
# How many of O's newest lines the fallback anchors on.
TAIL_ANCHOR_LINES = 64
# Timestamps per read when a restarted bench asks LabCore which of the rows it
# owes already landed — well under SQLite's 999-variable limit on old builds.
OWED_CHECK_STAMPS = 200
# multi_csv: a file is moved here before it is read (the move proves the
# instrument has let go of it) and out to processed/ only after the journal
# holds it. A kill in between leaves it here, where the next poll finds it.
INFLIGHT_DIRNAME = ".lem_inflight"


def line_digest(body: bytes) -> bytes:
    """The 16-byte hash of one line's bytes (terminator excluded) — the unit
    of snapshot.bin, and the same bytes as a run record's `lh` in hex."""
    return hashlib.sha256(body).digest()[:16]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Resolution:
    """What the resolver decided about a changed file.

    `new` are indices into N (the file now, in FILE order) of the lines that
    are new readings, listed in the order they were printed; `ambiguous` the
    subset recorded although they may repeat a recorded line; `ambiguity` the
    journal record that says so, or None."""

    __slots__ = ("kind", "new", "ambiguous", "ambiguity", "newest_first")

    def __init__(self, kind, new, ambiguous=(), ambiguity=None,
                 newest_first=False):
        self.kind = kind
        self.new = list(new)
        self.ambiguous = set(ambiguous)
        self.ambiguity = ambiguity
        self.newest_first = bool(newest_first)

    def __repr__(self):
        return ("Resolution(%r, new=%r, ambiguous=%r, ambiguity=%r, "
                "newest_first=%r)" % (self.kind, self.new, sorted(self.ambiguous),
                                      self.ambiguity, self.newest_first))


class _Fallback(Exception):
    """The ordered diff will not run here: the content is periodic, or the
    budget ran out. The argument says which."""


def _matching_blocks(a, b, deadline):
    """difflib's matching blocks, computed under a deadline. The recursion of
    `SequenceMatcher.get_matching_blocks`, unrolled so the clock can be
    checked between every `find_longest_match` — the stdlib call cannot be
    interrupted, and an instrument file must never hold a poll hostage."""
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    queue = [(0, len(a), 0, len(b))]
    blocks = []
    while queue:
        if time.monotonic() > deadline:
            raise _Fallback("budget")
        alo, ahi, blo, bhi = queue.pop()
        i, j, k = sm.find_longest_match(alo, ahi, blo, bhi)
        if k:
            blocks.append((i, j, k))
            if alo < i and blo < j:
                queue.append((alo, i, blo, j))
            if i + k < ahi and j + k < bhi:
                queue.append((i + k, ahi, j + k, bhi))
    blocks.sort()
    merged = []
    for i, j, k in blocks:
        if merged and merged[-1][0] + merged[-1][2] == i \
                and merged[-1][1] + merged[-1][2] == j:
            merged[-1] = (merged[-1][0], merged[-1][1], merged[-1][2] + k)
        else:
            merged.append((i, j, k))
    return merged


def _ordered_opcodes(a, b, budget):
    """`SequenceMatcher(None, a, b, autojunk=False).get_opcodes()`, after
    stripping the common prefix and suffix, under `budget` seconds.

    The suffix is stripped only beyond a margin of SUFFIX_STRIP_MARGIN lines.
    Stripped blindly, X1 breaks: [L0, L1, L2, L1] trimmed to [L2, L1] plus a
    third L1 shares the suffix "L1", and once that is cut away the diff
    anchors the remaining L1s to each other and calls L2 the new line — the
    reading that was really printed is lost and an old one doubled. Kept in
    view, the old [L2, L1] lines up with the start of the new file. A file
    whose appended lines repeat more than 32 of its last lines in order is
    the only thing the margin cannot see past, and that is periodic content,
    which is caught below."""
    na, nb = len(a), len(b)
    p = 0
    top = min(na, nb)
    while p < top and a[p] == b[p]:
        p += 1
    s = 0
    while s < top - p and a[na - 1 - s] == b[nb - 1 - s]:
        s += 1
    s = max(0, s - SUFFIX_STRIP_MARGIN)
    a_mid, b_mid = a[p:na - s], b[p:nb - s]
    total = len(a_mid) + len(b_mid)
    if total >= PERIODIC_MIN_LINES and \
            len(set(a_mid) | set(b_mid)) < PERIODIC_DISTINCT_FRACTION * total:
        raise _Fallback("periodic")
    if budget <= 0:
        raise _Fallback("budget")
    blocks = _matching_blocks(a_mid, b_mid, time.monotonic() + budget)
    ops = []
    if p:
        ops.append(("equal", 0, p, 0, p))
    i = j = 0
    for bi, bj, k in blocks + [(len(a_mid), len(b_mid), 0)]:
        tag = ""
        if i < bi and j < bj:
            tag = "replace"
        elif i < bi:
            tag = "delete"
        elif j < bj:
            tag = "insert"
        if tag:
            ops.append((tag, p + i, p + bi, p + j, p + bj))
        if k:
            ops.append(("equal", p + bi, p + bi + k, p + bj, p + bj + k))
        i, j = bi + k, bj + k
    if s:
        ops.append(("equal", na - s, na, nb - s, nb))
    # adjacent equals (prefix + first block) read as one
    out = []
    for op in ops:
        if out and out[-1][0] == op[0] == "equal" and out[-1][2] == op[1] \
                and out[-1][4] == op[3]:
            out[-1] = ("equal", out[-1][1], op[2], out[-1][3], op[4])
        else:
            out.append(op)
    return out


def _head_tail_overlap(O, N, deadline=None):
    """The largest k < len(O) with N[:k] == O[-k:] (0 if none): how many of
    the old file's last lines the new file starts with.

    One KMP pass (N's opening lines as the pattern, O's tail as the text), so
    it is linear however often N's first line recurs in O. The first version
    sliced O at every copy of N[0] and compared; with a QC line every third
    line of 80,000 that took 6.7 s. `deadline` is accepted and not needed."""
    L = min(len(N), len(O) - 1)
    if L <= 0:
        return 0
    P = N[:L]
    fail = [0] * L
    q = 0
    for i in range(1, L):
        while q and P[i] != P[q]:
            q = fail[q - 1]
        if P[i] == P[q]:
            q += 1
        fail[i] = q
    q = 0
    for x in O[len(O) - L:]:
        while q and x != P[q]:
            q = fail[q - 1]
        if x == P[q]:
            q += 1
            if q == L:
                break           # only at the very end: len(text) == L
    return q


def _tail_anchored(Oc, Nc, why):
    """The fallback: find where O's newest lines sit in N, and call what
    follows them new. Distinct content anchors in exactly one place and the
    answer is exact. Periodic content can anchor in several: the LARGER set of
    candidates is recorded and the lines only it contains are labelled
    ambiguous — record, never drop (§4.2)."""
    nN = len(Nc)
    m = min(len(Oc), TAIL_ANCHOR_LINES, nN)
    cands = []
    while m >= 1 and not cands:
        anchor = Oc[-m:]
        first = anchor[0]
        cands = [q for q in range(m, nN + 1)
                 if Nc[q - m] == first and Nc[q - m:q] == anchor]
        if not cands:
            m //= 2
    if not cands:
        # O's newest line is nowhere in N: a new file, all of it new. The lines
        # that also appear in O may be repeats.
        olds = set(Oc)
        amb = [j for j in range(nN) if Nc[j] in olds]
        return list(range(nN)), amb, {"kind": "periodic", "why": why,
                                       "lines": len(amb), "candidates": 0,
                                       "chosen": "recorded"}
    lo, hi = cands[0], cands[-1]
    return (list(range(lo, nN)), list(range(lo, hi)),
            {"kind": "periodic", "why": why, "lines": hi - lo,
             "candidates": len(cands), "chosen": "recorded"})


def resolve_rewrite(O, N, rotated: bool = False, newest_first: bool = False,
                    budget: float = REWRITE_DIFF_BUDGET,
                    predecessor: bool = False) -> Resolution:
    """Which lines of N (the file now) are new, given O (the lines consumed),
    both in file order. `rotated`: the file under the name is a different file
    from the one O was read from. `newest_first`: this source keeps its newest
    line at the top. Transfer v4 §4.2, shape by shape:

      continuation  O's newest line is still matched (whole-file rewrite, head
                    trim, an edit somewhere): the inserted and replacing lines
                    of the diff are new. Or N opens with O's last k lines
                    (a head trim the diff aligned elsewhere): N after them.
      correction    O's head kept and O's newest lines replaced by as many or
                    more (R6, or a correction plus a print): the replacing
                    lines are new; the originals stay in the record.
      rotation      anything else that lost O's newest line: a new file, or
                    the old one cut back and started again. All of N is new,
                    the lines that match O labelled.
      (shift)       where a head-trim reading and the diff's reading of the
                    same bytes disagree, both are recorded and the lines only
                    one calls new are labelled (§4.2 "periodic": the diff
                    could shift) — at most ROTATION_OVERLAP_MAX of them; a
                    plain append (N = all of O + more) is never widened.
      rotation_overlap  a different file that starts with up to 20 of O's
                    last lines in order (X3): a rotation repeating them, or a
                    trim done as write-temp-then-rename. All of N recorded,
                    the overlap labelled.
      newest_first  N is O with lines added at the TOP (R2): marked, and from
                    then on both are reversed before the diff.

    A ROTATED file (`rotated`: a different file identity under the name) is
    not read by the diff's end at all unless more than ROTATION_OVERLAP_MAX of
    O's lines reappear in it in order. A different file holding a handful of
    O's lines is a new file whose instrument printed them again — the same QC
    triplet every morning — or, by the very same bytes, a short file saved
    through a temp file. Either way all of N is recorded and the lines that
    match O are labelled. Read by the diff's end instead (round 1), a new
    day's "QC triplet, then a sample" ended in a `replace`, was taken for a
    correction, and the triplet was lost without a trace. More than twenty
    lines in order is a rewrite of the same content (temp-then-rename of a
    large export), and the shapes above apply.

    `predecessor`: the bench found the old file still in the folder under
    another name. A temp-file rewrite leaves no old file behind, so this
    settles the one shape the bytes cannot: a new file that starts with ALL
    of a short old file is then a rotation (labelled overlap), not a copy
    plus more; and it is never taken for a newest-first prepend.
    """
    nO, nN = len(O), len(N)
    deadline = time.monotonic() + max(budget, 0.0)
    if newest_first:
        Oc, Nc = list(reversed(O)), list(reversed(N))

        def fidx(i):
            return nN - 1 - i
    else:
        Oc, Nc = O, N

        def fidx(i):
            return i

    def out(kind, new, amb=(), ambiguity=None):
        return Resolution(kind, [fidx(i) for i in new], [fidx(i) for i in amb],
                          ambiguity, newest_first)

    if not nO:
        return out("fresh", range(nN))
    copy = Nc[:nO] == list(Oc)
    if rotated:
        if copy:
            # N starts with ALL of O: a temp-file copy plus more, unless the
            # old file is still in the folder (then a rotation that repeats it)
            k = nO if predecessor else 0
        else:
            k = _head_tail_overlap(Oc, Nc, deadline)
        if 1 <= k <= ROTATION_OVERLAP_MAX:
            return out("rotation_overlap", range(nN), range(k),
                       {"kind": "rotation_overlap", "lines": k,
                        "chosen": "recorded"})
    if not newest_first and not (rotated and predecessor) and nN > nO \
            and N[nN - nO:] == list(O) and N[:nO] != list(O):
        j = nN - nO
        return Resolution("newest_first", range(j - 1, -1, -1), (), None, True)
    if not copy:
        k = _head_tail_overlap(Oc, Nc)
        if k > ROTATION_OVERLAP_MAX:
            # N opens with more than twenty of O's last lines in order: a head
            # trim (R3), and only what follows is new. Said before the diff,
            # because difflib's longest match over a long file full of repeated
            # QC lines can take seconds in a single uninterruptible call.
            return out("continuation", range(k, nN))
    try:
        ops = _ordered_opcodes(list(Oc), list(Nc), budget)
    except _Fallback as why:
        new, amb, ambiguity = _tail_anchored(list(Oc), list(Nc), str(why))
        return out("fallback", new, amb, ambiguity)
    added = [j for tag, _i1, _i2, j1, j2 in ops if tag in ("insert", "replace")
             for j in range(j1, j2)]
    if rotated and not copy:
        matched = [j for tag, _i1, _i2, j1, j2 in ops if tag == "equal"
                   for j in range(j1, j2)]
        if len(matched) <= ROTATION_OVERLAP_MAX:
            # A different file with a few of O's lines in it: all of it new.
            return out("rotation", range(nN), matched,
                       {"kind": "rotation_overlap", "lines": len(matched),
                        "where": "within", "file": "new",
                        "chosen": "recorded"} if matched else None)
    trim = []

    def shifted(kind, new):
        """The diff's answer, widened by the head-trim reading when N opens
        with O's last lines and that reading has new lines the diff does not
        (§4.2's "the diff could shift" case): [QC3, QC3] trimmed to [QC3]
        plus S, QC2, QC3 is, by the same bytes, two lines inserted between
        the old QC3s. Both readings are recorded; the lines only the trim
        calls new are labelled — unless there are more of them than any
        plausible print burst (then the trim reading is not taken)."""
        if copy:
            # N is all of O plus more: a plain append by its bytes, the one
            # reading R7p's gate row fixes.
            return out(kind, new)
        if not trim:
            trim.append(_head_tail_overlap(Oc, Nc, deadline))
        k = trim[0]
        have = set(new)
        extra = [j for j in range(k, nN) if j not in have] if k else []
        if not extra or len(extra) > ROTATION_OVERLAP_MAX:
            return out(kind, new)
        return out(kind, sorted(have | set(range(k, nN))), extra,
                   {"kind": "periodic", "why": "shift", "lines": len(extra),
                    "candidates": 2, "chosen": "recorded"})

    end = next(op for op in ops if op[1] <= nO - 1 < op[2])
    if end[0] == "equal":
        return shifted("continuation", added)
    # O's newest line is not where it was. A CORRECTION (R6) keeps O's head
    # and replaces its last lines with as many or more (a corrected line, or
    # one plus a new print). Anything else that lost O's newest line removed
    # more of O than it put back, and difflib's longest-match alignment is no
    # guide to it: it pairs a repeated QC line with any older copy it likes,
    # and reading that as a correction drops the new copy.
    head_kept = ops[0][0] == "equal" and ops[0][1] == 0 and ops[0][3] == 0
    removed = sum(i2 - i1 for tag, i1, i2, _j1, _j2 in ops if tag != "equal")
    if head_kept and end[0] == "replace" and removed <= len(added):
        return shifted("correction", added)
    matched = [j for tag, _i1, _i2, j1, j2 in ops if tag == "equal"
               for j in range(j1, j2)]
    k = _head_tail_overlap(Oc, Nc, deadline)
    if k:
        # N opens with O's last k lines: a head trim (R3, X1) — everything
        # after the overlap is new. Lines after it that the diff found in O
        # are where the two readings disagree: recorded, and labelled.
        amb = [j for j in matched if j >= k]
        return out("continuation", range(k, nN), amb,
                   {"kind": "periodic", "why": "shift", "lines": len(amb),
                    "candidates": 2, "chosen": "recorded"} if amb else None)
    # Cut back and started again (or, rotated, a new file): every line of N
    # is recorded, and the ones that match O are labelled.
    return out("rotation", range(nN), matched,
               {"kind": "rotation_overlap", "lines": len(matched),
                "file": "new" if rotated else "same",
                "chosen": "recorded"} if matched else None)


def resolve_by_count(O, N, rotated: bool = False, newest_first: bool = False,
                     budget: float = REWRITE_DIFF_BUDGET,
                     predecessor: bool = False) -> Resolution:
    """The rule the ordered diff replaced, kept as a reference: a line of N is
    new when N holds more copies of it than O did. Right for distinct lines;
    wrong whenever the instrument prints a line that repeats one still in the
    file — X1 loses its third L1, X2 all three repeated QC lines. Not called
    by the module. The gate's mutation self-test swaps it in for
    `resolve_rewrite` and must go red; test_rewrite_resolver.py shows the
    loss in one line."""
    left = {}
    for h in O:
        left[h] = left.get(h, 0) + 1
    new = []
    for j, h in enumerate(N):
        if left.get(h, 0) > 0:
            left[h] -= 1
        else:
            new.append(j)
    return Resolution("count", new, (), None, newest_first)


def file_lineage(path: str, identity: str) -> str:
    """The lineage of a file first seen at `path` with `identity`: the name of
    the run of byte offsets its line keys are made of. Deterministic, so a
    bench that lost its cursor computes the same keys again and the journal
    recognises them."""
    return hashlib.sha256(("lineage\0%s\0%s" % (os.path.normcase(
        os.path.abspath(path)), identity)).encode("utf-8")).hexdigest()[:24]


def next_lineage(lineage: str, data: bytes) -> str:
    """The lineage after a rewrite was resolved: a function of the old lineage
    and the new bytes, so a bench killed after journaling the resolution and
    before saving its cursor resolves the same file to the same keys."""
    return hashlib.sha256(("%s\0" % lineage).encode("utf-8")
                          + hashlib.sha256(data).digest()).hexdigest()[:24]


def multi_file_key(uid: str, name: str, size: int, mtime_ns: int,
                   sha256_hex: str) -> str:
    """multi_csv's replay key (§4.3): the file's name, size, modification time
    and content. A re-read before the move is the same key; identical bytes
    exported again under the same name have a new mtime and are a new
    reading."""
    return hashlib.sha256(("multi\0%s\0%s\0%d\0%d\0%s" % (
        uid, name, int(size), int(mtime_ns), sha256_hex)).encode(
        "utf-8")).hexdigest()[:32]


def _split_chunks(data: bytes, final_complete: bool) -> tuple:
    """([(start, end, body)], partial_start): the complete lines of `data`,
    and where an unfinished last line starts (None if there is none). A line
    ends at \\n, \\r\\n or \\r — but a \\r at the very end may be the first
    half of \\r\\n, so it is not an end yet. `final_complete` takes the last
    line whole however it ends (the file is quiet)."""
    out = []
    partial = None
    n = len(data)
    for m in _BYTE_LINE.finditer(data):
        chunk = m.group()
        if not chunk:
            continue
        start, end = m.start(), m.end()
        terminated = chunk.endswith(b"\n") or (chunk.endswith(b"\r") and end < n)
        if not terminated and not final_complete:
            partial = start
            break
        out.append((start, end, chunk.rstrip(b"\r\n")))
    return out, partial


def _decode_block(data: bytes) -> str:
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1252"


def _prints_from(data: bytes, chunks, base: int, lineage: str, src_label: str,
                 origin_of=None) -> list:
    """Keyed prints for `chunks` of `data` (file offsets = base + start)."""
    encoding = _decode_block(data)
    out = []
    for idx, (start, end, body) in enumerate(chunks):
        lh = line_digest(body).hex()
        text = data[start:end].decode(encoding, errors="replace")
        origin = origin_of(idx) if origin_of else "live"
        for part, piece in enumerate(text.splitlines()):
            if piece.strip():
                p = _keyed(piece, file_line_key(lineage, base + start, part, lh),
                           src=src_label, lh=lh)
                p.origin = origin
                out.append(p)
    return out


class CursorStore:
    """cursor.json and snapshot.bin in one bench's journal folder.

    Written in the order snapshot.bin, then cursor.json, each by atomic
    replace. cursor.json names the snapshot it goes with (count and sha256),
    so a kill between the two leaves a cursor that knows its snapshot is not
    the one on disk — and the source rebuilds O from the file itself, which
    is what O was read from."""

    def __init__(self, directory: str) -> None:
        self.dir = directory

    def _path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def load(self) -> tuple:
        """({key: cursor}, {key: [hashes] | None}, [notice]). A missing
        cursor.json is "this bench has no cursor yet"; an unreadable one is
        set aside and SAID, never taken for an empty one."""
        notices: List[str] = []
        path = self._path(CURSOR_NAME)
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except FileNotFoundError:
            return {}, {}, notices
        except OSError as exc:
            raise JournalError(f"cannot read {path}: {exc}") from exc
        try:
            doc = json.loads(raw.decode("utf-8"))
            sources = doc["sources"]
            if not isinstance(sources, dict):
                raise ValueError("no sources in it")
        except (UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            aside = path + ".bad-" + stamp
            try:
                os.replace(path, aside)
            except OSError:
                aside = path
            notices.append(
                f"This bench's source cursor ({CURSOR_NAME}) could not be read "
                f"({exc}); it was kept as {os.path.basename(aside)} and the "
                "file is being read again from the top — lines the journal "
                "already holds are recognised and not recorded twice.")
            return {}, {}, notices
        return sources, self._load_snapshots(sources), notices

    def _load_snapshots(self, sources: dict) -> dict:
        snaps: Dict[str, Optional[list]] = {k: None for k in sources}
        try:
            with open(self._path(SNAPSHOT_NAME), "rb") as f:
                data = f.read()
        except OSError:
            return snaps
        if not data.startswith(_SNAPSHOT_MAGIC):
            return snaps
        pos = len(_SNAPSHOT_MAGIC)
        try:
            while pos < len(data):
                (klen,) = struct.unpack_from("<I", data, pos)
                pos += 4
                key = data[pos:pos + klen].decode("utf-8")
                pos += klen
                (n,) = struct.unpack_from("<I", data, pos)
                pos += 4
                body = data[pos:pos + 16 * n]
                pos += 16 * n
                if len(body) != 16 * n:
                    break
                want = (sources.get(key) or {}).get("snapshot") or {}
                if want.get("n") == n and want.get("sha") == _sha(body):
                    snaps[key] = [body[i:i + 16] for i in range(0, len(body), 16)]
        except (struct.error, UnicodeDecodeError):
            pass
        return snaps

    def save(self, cursors: dict, snapshots: dict, fsync=None) -> None:
        parts = [_SNAPSHOT_MAGIC]
        doc = {"version": 1, "sources": {}}
        for key, cur in cursors.items():
            body = b"".join(snapshots.get(key) or ())
            kb = key.encode("utf-8")
            parts += [struct.pack("<I", len(kb)), kb,
                      struct.pack("<I", len(body) // 16), body]
            entry = dict(cur)
            entry["snapshot"] = {"n": len(body) // 16, "sha": _sha(body)}
            doc["sources"][key] = entry
        os.makedirs(self.dir, exist_ok=True)
        atomic_write(self._path(SNAPSHOT_NAME), b"".join(parts), fsync=fsync)
        atomic_write(self._path(CURSOR_NAME), canonical_body(doc), fsync=fsync)


class _SourceRead:
    """One poll's read of a source, not yet consumed. `commit` makes it
    consumed — the cursor saved, or the files moved — and runs only after the
    journal holds what was read (§3.3 (b) after (a))."""

    def __init__(self, prints, records=(), commit=None, offset=None):
        self.prints = prints
        self.records = list(records)
        self._commit = commit
        self.offset = offset

    def commit(self) -> List[str]:
        return self._commit() if self._commit else []


class SingleCsvSource:
    """One tailed file of one bench: its cursor, its snapshot, and what the
    file looked like at the last poll (for the quiet rule). Not thread-safe
    by itself; the module calls it from one poll at a time."""

    def __init__(self, path: str, store: Optional[CursorStore]) -> None:
        self.path = path
        self.key = "single_csv:" + os.path.normcase(os.path.abspath(path))
        self.store = store
        self.cursor: Optional[dict] = None
        self.O: Optional[list] = []
        self.full_checked: Optional[datetime] = None
        self.obs: Optional[tuple] = None
        self.notices: List[str] = []
        self.loaded = False
        # A change waiting for the file to go quiet: since when, and earlier
        # looks at the file (time, size, sha256) to find the part that held
        # still. `waiting` is the standing status-line sentence.
        self.pending_since: Optional[datetime] = None
        self.looks: list = []
        self.waiting = ""

    def status_note(self) -> str:
        """A sentence that stays on the status line while this source holds
        a change it has not finished reading, or ""."""
        return self.waiting

    def _wait_note(self, text: str) -> None:
        """Set the standing sentence; the first time in a stretch of waiting
        it is also said once, as a notice."""
        if not self.waiting:
            self.notices.append(text)
        self.waiting = text

    def _settled(self) -> None:
        self.pending_since = None
        self.looks = []

    # ── state ────────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Read the saved cursor once. A load that RAISES leaves the source
        unloaded, so the next poll asks again: a cursor the OS would not hand
        over this time is not a bench with no cursor."""
        if self.loaded:
            return
        if self.store is None:
            self.loaded = True
            return
        sources, snaps, notices = self.store.load()
        self.loaded = True
        self.notices.extend(notices)
        cur = sources.get(self.key)
        if isinstance(cur, dict) and isinstance(cur.get("offset"), int):
            self.cursor = cur
            self.O = snaps.get(self.key)          # None: rebuild from the file
            self.full_checked = None             # validate against the file soon

    def _save(self, cursor: dict, O: list) -> List[str]:
        self.cursor, self.O = cursor, O
        if self.store is None:
            return []
        try:
            self.store.save({self.key: cursor}, {self.key: O})
        except OSError as exc:
            return [f"The source cursor could not be saved ({exc}); a restart "
                    "will read from the last saved point, and the journal will "
                    "recognise what it already holds."]
        return []

    # ── the quiet rule ───────────────────────────────────────────────────────

    def _observe(self, state: tuple, now: datetime) -> bool:
        """Record what the file looks like now; True when it has looked
        exactly like this for QUIET_SECONDS of the bench's clock (which takes
        at least two polls)."""
        if self.obs is not None and self.obs[0] == state:
            return (now - self.obs[1]).total_seconds() >= QUIET_SECONDS
        self.obs = (state, now)
        return False

    # ── reading ──────────────────────────────────────────────────────────────

    def read(self, now: datetime) -> _SourceRead:
        """What is new in the file. Raises OSError when it cannot be read —
        a file that cannot be read is an error, never "nothing new"."""
        self.load()
        with open(self.path, "rb") as f:
            st = os.fstat(f.fileno())
            fid = file_identity(st)
            state = (st.st_size, st.st_mtime_ns, fid)
            quiet = self._observe(state, now)
            cur = self.cursor
            if cur is None:
                return self._append(f, st.st_size, quiet, now,
                                    {"offset": 0, "file_id": fid,
                                     "lineage": file_lineage(self.path, fid),
                                     "newest_first": False}, fresh=True)
            offset = int(cur["offset"])
            if cur.get("file_id") == fid and st.st_size >= offset \
                    and not cur.get("unsettled") \
                    and self._windows_match(f, cur, offset):
                if self.O is None:
                    self.O = self._hashes(f, offset)
                if self._rescan_due(now) and st.st_size <= RESCAN_MAX_BYTES:
                    if self._hashes(f, offset) == self.O:
                        self.full_checked = now
                        self._settled()
                        self.waiting = ""
                        return self._append(f, st.st_size, quiet, now, cur)
                    return self._rewrite(f, st, fid, quiet, now, rescanned=True)
                self._settled()
                self.waiting = ""
                return self._append(f, st.st_size, quiet, now, cur)
            return self._rewrite(f, st, fid, quiet, now)

    def _rescan_due(self, now: datetime) -> bool:
        return self.full_checked is None or now - self.full_checked >= RESCAN_EVERY

    @staticmethod
    def _window_hashes(f, offset: int) -> tuple:
        f.seek(0)
        head = f.read(min(offset, CURSOR_HEAD_BYTES))
        start = max(0, offset - CURSOR_TAIL_BYTES)
        f.seek(start)
        tail = f.read(offset - start)
        return _sha(head), _sha(tail)

    def _windows_match(self, f, cur: dict, offset: int) -> bool:
        return self._window_hashes(f, offset) == (cur.get("head_hash"),
                                                  cur.get("tail_hash"))

    @staticmethod
    def _hashes(f, end: int) -> list:
        f.seek(0)
        data = f.read(end)
        chunks, _ = _split_chunks(data, final_complete=True)
        return [line_digest(body) for _s, _e, body in chunks]

    def _cursor_at(self, f, offset: int, fid: str, lineage: str,
                   newest_first: bool, unsettled: bool = False) -> dict:
        head, tail = self._window_hashes(f, offset)
        cur = {"offset": offset, "head_hash": head, "tail_hash": tail,
               "file_id": fid, "lineage": lineage,
               "newest_first": bool(newest_first)}
        if unsettled:
            # Only the bottom of a newest-first file was read: its top is
            # still unread, so the next poll diffs again instead of tailing.
            cur["unsettled"] = True
        return cur

    def _append(self, f, size: int, quiet: bool, now: datetime, cur: dict,
                fresh: bool = False) -> _SourceRead:
        """The append path: whole lines after the offset. A last line with no
        end waits until the file has been quiet, then is taken whole."""
        last_position = int(cur["offset"])
        f.seek(last_position)
        data = f.read(size - last_position)
        chunks, _partial = _split_chunks(data, final_complete=quiet)
        if not chunks:
            return _SourceRead([])
        # The end of the last whole line read: where the next poll starts.
        new_position = last_position + chunks[-1][1]
        prints = _prints_from(data, chunks, last_position, cur["lineage"],
                              "file:" + self.key)
        end = new_position
        cursor = self._cursor_at(f, end, cur["file_id"], cur["lineage"],
                                 cur.get("newest_first"))
        O = list(self.O or []) + [line_digest(b) for _s, _e, b in chunks]
        checked = now if fresh else self.full_checked

        def commit():
            self.full_checked = checked
            return self._save(cursor, O)
        return _SourceRead(prints, commit=commit, offset=end)

    def _rewrite(self, f, st, fid: str, quiet: bool, now: datetime,
                 rescanned: bool = False) -> _SourceRead:
        """The rewrite path (§4.1 2): once the file is quiet — or, when it has
        not gone quiet within QUIET_MAX_WAIT, on the part of it that has held
        still — the renamed predecessor is drained if the file was rotated,
        and the resolver decides what of the file is new."""
        cur = self.cursor
        f.seek(0)
        whole = f.read(st.st_size)
        base, region = 0, None
        if quiet:
            data = whole
        else:
            region = self._held_region(whole, now, bool(cur.get("newest_first")))
            if region is None:
                return _SourceRead([])
            base, size = region
            data = whole[base:base + size]
        # Not quiet: only lines the writer has ended are whole.
        chunks, _ = _split_chunks(data, final_complete=quiet)
        if region is not None and not chunks:
            return _SourceRead([])     # nothing whole has held still yet
        rotated = cur.get("file_id") != fid
        prints: list = []
        O = None if self.O is None else list(self.O)
        records = []
        found = False
        if rotated:
            drained, O, found = self._drain_sibling(cur, O)
            prints += drained
        N = [line_digest(body) for _s, _e, body in chunks]
        if O is None:
            # The snapshot is gone and the file is not the one the cursor
            # describes: there is nothing to diff against. Record everything,
            # labelled; the journal's keys still drop what it already holds.
            res = Resolution("no_snapshot", range(len(N)), range(len(N)),
                             {"kind": "no_snapshot", "lines": len(N),
                              "chosen": "recorded"}, cur.get("newest_first"))
        else:
            resolution = resolve_rewrite(O, N, rotated=rotated,
                                         newest_first=bool(cur.get("newest_first")),
                                         predecessor=found)
            res = resolution
        used = data[:chunks[-1][1]] if chunks else b""
        lineage = next_lineage(cur["lineage"], used)
        new = [chunks[i] for i in res.new]
        amb = {k for k, i in enumerate(res.new) if i in res.ambiguous}
        fresh = _prints_from(data, new, base, lineage, "file:" + self.key,
                             origin_of=lambda k: "ambiguous" if k in amb else "live")
        prints += fresh
        if res.ambiguity:
            rec = {"kind": "ambiguity", "src": "file:" + self.key,
                   "ambiguity": res.ambiguity["kind"],
                   "lines": res.ambiguity.get("lines", 0),
                   "chosen": res.ambiguity.get("chosen", "recorded"),
                   "detail": res.ambiguity, "resolution": res.kind,
                   "pks": [p.pk for p in fresh if p.origin == "ambiguous"]}
            records.append(rec)
        if region is not None and base:
            # The bottom of a newest-first file: everything is "read" as far
            # as the offset goes, but the top is not — next poll diffs again.
            end = len(whole)
            cursor = self._cursor_at(f, end, fid, lineage, res.newest_first,
                                     unsettled=True)
        else:
            end = len(used)
            cursor = self._cursor_at(f, end, fid, lineage, res.newest_first)
        if region is not None:
            waited = int((now - self.pending_since).total_seconds()) \
                if self.pending_since else 0
            self._wait_note(
                f"The instrument's file {os.path.basename(self.path)} has not "
                f"stopped changing for {waited} s; the bench read the "
                f"{len(chunks)} lines that have held still and will read the "
                "rest as it settles. Nothing is lost by waiting.")
        else:
            self.waiting = ""
        if rescanned:
            self.notices.append(
                "The instrument's file changed somewhere the bench had already "
                "read (found by the 15-minute check); the changed lines were "
                "recorded as new readings.")

        def commit():
            self.full_checked = now
            self._settled()
            return self._save(cursor, N)
        return _SourceRead(prints, records=records, commit=commit,
                           offset=end)

    def _held_region(self, data: bytes, now: datetime,
                     newest_first: bool) -> Optional[tuple]:
        """(start, size) of the part of a still-changing file that has held
        still, once the change has waited QUIET_MAX_WAIT; None to keep
        waiting. "Held still" is the quiet rule applied to a region: the
        bytes are identical to an earlier look at the whole file taken at
        least QUIET_SECONDS ago — at the top of the file, or for a newest-
        first file (which grows at the top) at the bottom."""
        if self.pending_since is None:
            self.pending_since = now
        n = len(data)
        valid = []
        if n <= RESCAN_MAX_BYTES:
            for t, size, sha in self.looks:
                if size > n:
                    continue
                if _sha(data[:size]) == sha:
                    valid.append((t, 0, size, sha))
                elif newest_first and _sha(data[n - size:]) == sha:
                    valid.append((t, n - size, size, sha))
            here = _sha(data)
            if not valid or valid[-1][3] != here or valid[-1][2] != n:
                valid.append((now, 0, n, here))
            self.looks = [(t, size, sha) for t, _b, size, sha in valid][-HELD_LOOKS:]
        waited = now - self.pending_since
        if waited < QUIET_MAX_WAIT:
            return None
        cands = [(size, start) for t, start, size, _sha_ in valid
                 if size and (now - t).total_seconds() >= QUIET_SECONDS]
        if not cands:
            self._wait_note(
                f"The instrument's file {os.path.basename(self.path)} has not "
                f"stopped changing for {int(waited.total_seconds())} s and "
                + ("no part of it has held still" if n <= RESCAN_MAX_BYTES else
                   "it is too large to compare while it changes")
                + "; the bench reads it as soon as it settles. Nothing is "
                "lost by waiting.")
            return None
        size, start = max(cands)
        return start, size

    def _drain_sibling(self, cur: dict, O: Optional[list]) -> tuple:
        """X4: lines appended to the old file after the last poll and before it
        was renamed away. Find it in the same folder by its identity and read
        it on from the cursor, under the old lineage — exactly the keys the
        append path would have given those lines."""
        folder = os.path.dirname(os.path.abspath(self.path)) or "."
        want = cur.get("file_id")
        me = os.path.normcase(os.path.abspath(self.path))
        try:
            entries = list(os.scandir(folder))
        except OSError as exc:
            self.notices.append(f"Could not look for the renamed old file in "
                                f"{folder} ({exc}).")
            return [], O, False
        for entry in entries:
            try:
                if os.path.normcase(os.path.abspath(entry.path)) == me \
                        or not entry.is_file():
                    continue
                with open(entry.path, "rb") as g:
                    if file_identity(os.fstat(g.fileno())) != want:
                        continue
                    offset = int(cur["offset"])
                    size = os.fstat(g.fileno()).st_size
                    if size < offset or not self._windows_match(g, cur, offset):
                        self.notices.append(
                            f"The instrument's old file ({entry.name}) was "
                            "changed as well as renamed; only the new file "
                            "was read.")
                        return [], O, True
                    if O is None:
                        O = self._hashes(g, offset)
                    g.seek(offset)
                    data = g.read(size - offset)
            except OSError:
                continue
            chunks, _ = _split_chunks(data, final_complete=True)
            prints = _prints_from(data, chunks, offset, cur["lineage"],
                                  "file:" + self.key)
            return (prints, (O or []) + [line_digest(b) for _s, _e, b in chunks],
                    True)
        return [], O, False


# ── Status evaluation (ported from LEM V5.0 data_source.evaluate_box) ────────

def _ci_lookup(row: dict, key: str):
    if key in row:
        return row[key]
    key_l = key.strip().lower()
    for k, v in row.items():
        if k.strip().lower() == key_l:
            return v
    return None


def _row_time(row: dict, fallback: datetime) -> datetime:
    try:
        return datetime.strptime(
            f"{row.get('parsed_date', '')} {row.get('parsed_time', '')}",
            "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return fallback


def _safe_float(value) -> Optional[float]:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def corrected_value(value, offset) -> Optional[float]:
    """`raw + offset`, carrying the precision of the reading — not of the
    hardware.

    Reported from the floor 2026-08-13 as sulfur results "infinitely
    extending" (Lab IDs 37712, 37709). The addition was a plain binary float
    one, so a four-decimal reading came back with seventeen:

        0.0015 + -0.0003  ->  0.0012000000000000001

    and `str()` of that is exactly what the write op carries to LabCore and what
    the floor renders. Neither number is representable in binary, so the sum
    lands a fraction off and the shortest round-tripping repr has to spell the
    whole thing out. It surfaced on sulfur because those readings sit around
    0.001-0.05, where the error falls inside the digits somebody reads; the same
    bug was always there on flash point, it just hid below the printed
    precision.

    Doing the arithmetic in Decimal — from the decimal strings, not from the
    floats — is exact, and the scale of the answer is naturally the larger of
    the two operands' scales, which is precisely the rule a lab already uses: a
    reading to four decimals offset by a factor to four decimals is a result to
    four decimals. The float that comes back is the nearest double to that
    decimal, so its repr is the short form and every existing consumer (the QC
    band comparison, `_safe_float`, both CSV exports) keeps taking a float.

    Returns None when either side is not a number, which leaves the reading
    untouched — a value that cannot be offset must not be invented.
    """
    try:
        number = Decimal(str(value).strip())
        shift = Decimal(str(offset).strip())
    except (TypeError, ValueError, ArithmeticError):
        return None
    if not (number.is_finite() and shift.is_finite()):
        return None
    return float(number + shift)


def _row_time_from_iso(text: str) -> Optional[datetime]:
    """Parse a remembered QC timestamp. Junk yields None rather than raising —
    an unreadable stamp must not decide a machine's status."""
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def evaluate_machine(machine: Machine, rows: List[dict],
                     now: datetime) -> MachineEvaluation:
    """LEM status logic: latest matching row per LabCore method vs
    expected ± k·std_dev, rolling-window QC staleness, RED > YELLOW >
    UNKNOWN > GREEN, manual overrides."""
    results: List[TestResult] = []
    last_seen: Optional[datetime] = None

    for spec in machine.tests:
        wanted = spec.sample_id.strip().lower()
        matching = []
        for row in rows:
            lab_id = _ci_lookup(row, machine.lab_id_column)
            if wanted and str(lab_id or "").strip().lower() != wanted:
                continue
            matching.append(row)
        if not matching:
            # Nothing parsed for this test in THIS session. A LabStation restart
            # is not a QC failure: if LabCore remembers a verdict, judge on that
            # so a machine whose QC passed three hours ago stays green.
            remembered = _row_time_from_iso(spec.last_qc_at)
            if remembered is not None and spec.last_qc_value is not None:
                # spec_band, not the arithmetic again: a verdict read back after
                # a restart has to be judged by the same band as a live one.
                low, high = spec_band(spec)
                in_spec = (spec.last_qc_in_spec
                           if spec.last_qc_in_spec is not None
                           else low <= spec.last_qc_value <= high)
                last_seen = (remembered if last_seen is None
                             else max(last_seen, remembered))
                results.append(TestResult(spec.name, spec.last_qc_value,
                                          in_spec, remembered))
                continue
            results.append(TestResult(spec.name, None, None, None))
            continue
        matching.sort(key=lambda r: _row_time(r, now), reverse=True)
        t = _row_time(matching[0], now)
        last_seen = t if last_seen is None else max(last_seen, t)
        # Prints can carry a SUBSET of tests (a partial re-run). Judge each
        # test by its newest row that actually carries a value — an un-run
        # test keeps its last real measurement instead of going UNKNOWN.
        value = None
        value_time = t
        raw_value = None
        for row in matching:
            value = _safe_float(_ci_lookup(row, spec.value_col))
            if value is not None:
                value_time = _row_time(row, now)
                # Whatever this row recorded as the reading before correction.
                raw_value = row_raw(row).get(spec.value_col,
                                             row_raw(row).get(spec.name, value))
                break
        if value is None:
            results.append(TestResult(spec.name, None, None, t))
            continue
        # NOT corrected here. The correction is applied once, at the parse
        # boundary (`apply_row_corrections`), so this value already carries it —
        # applying `spec.correction` again would double it. The raw reading comes
        # off the row's own record, which is what makes the verdict auditable.
        low, high = spec_band(spec)
        results.append(TestResult(spec.name, value, low <= value <= high,
                                  value_time,
                                  raw_value=raw_value))

    spec_by_name = {spec.name: spec for spec in machine.tests}
    failed = [r.name for r in results if r.in_spec is False]
    unknown = [r.name for r in results if r.in_spec is None]
    stale = []
    for r in results:
        if not (r.in_spec and r.time):
            continue
        spec = spec_by_name.get(r.name)
        # One resolver, not the chain retyped here. It used to end at
        # `machine.qc_expire_hours`, so a machine saved with 0 got a zero-hour
        # window and every reading on it was instantly stale.
        hours, _source = qc_window_for(spec, machine)
        if qc_is_stale(r.time, now, hours):
            stale.append(r.name)
    if failed:
        status, reason = STATUS_RED, f"QC out of spec: {', '.join(failed)}"
    elif not machine.tests:
        # Nothing is assigned, so there is genuinely nothing to say. This is
        # the ONLY case that stays grey.
        status, reason = STATUS_UNKNOWN, "No QC assigned."
    elif unknown:
        # QC IS assigned and hasn't produced a usable measurement. That's a job
        # someone needs to do, not an unknown state — grey made it look
        # identical to an unconfigured bench, so it read as "ignore me".
        never = [r.name for r in results if r.time is None]
        status = STATUS_YELLOW
        if len(never) == len(results):
            reason = f"QC assigned but not yet run: {', '.join(never)}"
        else:
            reason = f"Awaiting QC: {', '.join(unknown)}"
    elif stale:
        status, reason = STATUS_YELLOW, f"QC stale: {', '.join(stale)}"
    else:
        status, reason = STATUS_GREEN, "System nominal"
    qc_status = status

    # ── PM / Calibration rollup (operator-managed on LabStation) ──
    maintenance = []
    maint_red = []
    maint_yellow = []
    by_kind = {"pm": [], "calibration": []}
    for task in machine.maintenance:
        m_status, m_reason = maint_status(task, now.date())
        maintenance.append({"uid": task.uid, "name": task.name,
                            "kind": task.kind, "status": m_status,
                            "reason": m_reason})
        kind = "calibration" if "cal" in task.kind.lower() else "pm"
        by_kind[kind].append(m_status)
        if m_status == STATUS_RED:
            maint_red.append(m_reason)
        elif m_status == STATUS_YELLOW:
            maint_yellow.append(m_reason)

    def rollup(states):
        if not states:
            return STATUS_UNKNOWN            # nothing scheduled yet
        for worst in (STATUS_RED, STATUS_YELLOW):
            if worst in states:
                return worst
        return STATUS_GREEN

    sub_statuses = {"qc": qc_status,
                    "pm": rollup(by_kind["pm"]),
                    "calibration": rollup(by_kind["calibration"])}
    if maint_red:
        status = STATUS_RED
        reason = "; ".join([reason] + maint_red) if failed else "; ".join(maint_red)
    elif maint_yellow and status == STATUS_GREEN:
        status, reason = STATUS_YELLOW, "; ".join(maint_yellow)

    if machine.manual_override in (STATUS_SERVICE, STATUS_DEAD):
        return MachineEvaluation(
            status=machine.manual_override,
            reason=f"Overridden to {machine.manual_override}. Underlying: {reason}",
            test_results=results, last_seen=last_seen,
            maintenance=maintenance, sub_statuses=sub_statuses)
    return MachineEvaluation(status=status, reason=reason,
                             test_results=results, last_seen=last_seen,
                             maintenance=maintenance,
                             sub_statuses=sub_statuses)


# ── LabCore sync (module ⇄ master view, LabCore as the data bus) ─────────────
#
# The module writes parsed prints and its machine status to LabCore; the LEM
# web server (master view) reads them there, provides the QC specs
# (lem_qc_specs), and writes operator commands into lem_machine_control.

STATUS_TABLE_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_machine_status ("
    "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
    "reason TEXT, updated_at TEXT)"
)

QC_SPECS_QUERY = (
    "SELECT machine_uid, test_name, sample_id, expected, std_dev, k, units "
    "FROM lem_qc_specs"
)


def apply_row_corrections(rows: List[dict], corrections: dict) -> List[dict]:
    """Apply the machine's correction factors to EVERY measurement on every row.

    This is the single point at which a correction is applied. Everything
    downstream — the QC verdict, the result written to LabCore, the history, the
    card — reads the corrected value, so no consumer has to remember to apply it
    and none can apply it twice.

    It used to happen in `evaluate_machine`, which only ever sees the machine's QC
    specs. PAC Flash 2's -3.0 therefore adjusted its QC verdict while every
    customer sample was written to LabCore raw — the opposite of what a correction
    is for. ISO/IEC 17025:2017 §7.8.2: a reported result must be the measurement
    result, which means corrected.

    The raw reading and the offset are kept on the row (§7.5.1, records sufficient
    to reconstruct the measurement). Rows already corrected are left alone.
    """
    out = []
    for row in rows:
        row = dict(row)
        if not corrections or row.get(RAW_KEY):
            out.append(row)
            continue
        raw, applied = {}, {}
        for key, value in list(row.items()):
            if key in RESERVED_ROW_KEYS:
                continue
            offset = corrections.get(key)
            if not offset:                  # absent, or an explicit zero
                continue
            number = _safe_float(value)
            if number is None:              # a non-numeric reading cannot be offset
                continue
            result = corrected_value(value, offset)
            if result is None:              # unrepresentable: report it raw
                continue
            raw[key] = number
            applied[key] = float(offset)
            row[key] = result
        if raw:
            row[RAW_KEY] = raw
            row[CORRECTION_KEY] = applied
        out.append(row)
    return out


def row_raw(row: dict) -> dict:
    """The readings as parsed, before any correction."""
    return dict(row.get(RAW_KEY) or {})


def row_corrections(row: dict) -> dict:
    """The offsets actually applied to this row."""
    return dict(row.get(CORRECTION_KEY) or {})


def run_log_detail(row: dict) -> dict:
    """What a parsed run records.

    Carries the corrected values that were reported, and — only where a correction
    was applied — the raw readings and offsets behind them, so the result can be
    reconstructed from the record alone (ISO/IEC 17025:2017 §7.5.1).
    """
    values = {k: v for k, v in row.items() if k not in RESERVED_ROW_KEYS}
    detail = {"values": values}
    raw, applied = row_raw(row), row_corrections(row)
    if raw:
        detail["raw"] = raw
        detail["corrections"] = applied
    # A line the rewrite resolver recorded although it may repeat one already
    # recorded (transfer v4 §4.2) says so in the record itself: a labelled,
    # visible possible duplicate, never a silent one and never a silent loss.
    origin = row.get(ORIGIN_KEY)
    if origin and origin != "live":
        detail["origin"] = origin
    return detail


def run_log_events(machine: "Machine", rows: List[dict], operator,
                   calibration_id) -> List[tuple]:
    """The machine-log records a poll's rows make, in order:
    (row, kind, lab_id, test_name, value, detail).

    One place, because two callers need the identical answer: the journal
    records them with the reading (so a restart can re-deliver exactly the
    rows it would have written), and `_queue_run_events` queues them. See that
    method for the rules — a QC standard's print logs its 'qc' verdicts and not
    a 'run', and falls back to a 'run' when no verdict is readable."""
    out = []
    for row in rows:
        lab_id = str(row.get(LAB_ID_KEY) or "").strip()
        # RESERVED_ROW_KEYS, not just the Lab ID and timestamps: a corrected row
        # carries its raw readings and offsets, and those are not measurements.
        raw_by_test = row_raw(row)
        verdicts = []
        for spec in machine.tests:
            if not spec.sample_id:
                continue
            if lab_id.lower() != spec.sample_id.strip().lower():
                continue
            value = _safe_float(_ci_lookup(row, spec.value_col))
            if value is None:
                continue
            # Already corrected at the parse boundary — adding spec.correction
            # here would apply it a second time.
            raw = raw_by_test.get(spec.value_col,
                                  raw_by_test.get(spec.name, value))
            verdicts.append((spec, raw, value))
        if not verdicts:
            out.append((row, "run", lab_id, "", "", run_log_detail(row)))
            continue
        for spec, raw, value in verdicts:
            # `value` is the corrected number the verdict was made on; the
            # detail carries the raw reading and the offset when there is one.
            out.append((row, "qc", lab_id, spec.name, f"{value:g}",
                        qc_log_detail(spec, raw, value, operator=operator,
                                      calibration_id=calibration_id)))
    return out


# ── Whose sample is this? ────────────────────────────────────────────────────
#
# The instrument prints what is written on the cup — "34566" — and the sample the
# lab logged in is "081126-34566". Nothing on the LEM road ever reconciled the
# two. LabCore has no foreign key from `sample_tests` onto `samples`, so a cell
# written under the printed ID is accepted, returns ok, and lands beside a sample
# that does not exist; the Results grid reads through an INNER JOIN, so that row
# is invisible for good. The reading was stored and lost at the same time.
#
# LabStation already knows how to do this for an ID an operator types:
# `_check_test_assignments` (LabStation.pyw:12691) resolves it against `samples`
# — exact, leading-zero tolerant, or as the suffix of a dated "mmddyy-labid".
# But that runs off the operator's Enter key, and the LEM road never reaches it.
# These functions ask LabCore the same question from the worker, so the answer no
# longer depends on which widgets happen to be on the canvas.
#
# Two rules make the answer safe to act on:
#
#   • LEM NEVER INVENTS A SAMPLE. If LabCore does not hold one, none is minted.
#     A phantom "34566" sitting beside the LIMS's "081126-34566" leaves the
#     LIMS's own record blank forever and — stamped with `datetime.now()` by
#     insert_sample — is not even visible under the shipped date filter. A
#     reading that is late is recoverable; a forked sample table is not.
#   • A CUP NUMBER IS ISSUED ONCE. Ryan, asked what to do when a bare printed
#     number answers to several dated samples: "This can never happen because
#     its linear from 0 to indef. But if it does choose the closer date." The
#     numeric part is one monotonic sequence over the whole life of the lab, so
#     "081126-34566" and "081026-34566" cannot both exist — the date is a label
#     on a unique number, not a per-day cup number. A tie is therefore a
#     defect in the data, not a Tuesday, and the reading is placed on the
#     sample whose date is NEAREST the reading's own parse time
#     (`closest_by_date`, which says plainly what that is and is not). Only a
#     tie that date cannot break — two samples stamped the same day, or none
#     stamped at all — is held, because there is then genuinely nothing to
#     choose with. See `describe_held` for what the operator is told; it is
#     never "rename one", which orphans every result already filed against it.
#   • THE PHANTOMS ARE ALREADY THERE. Not minting one from today on is only half
#     the job: every bench in this lab has run the pristine code, which minted
#     one on every poll, so `samples` already holds a bare "34566" beside the
#     LIMS's "081126-34566" for every cup this software has processed. Left
#     alone, the exact tier hands the reading straight back to the phantom and
#     the LIMS's cell stays blank — the fix would be correct and inert. So where
#     an undated match and a dated one both answer to a printed ID,
#     `sample_matches` takes the dated one: the numbers are issued once, so it is
#     one sample, and the LIMS owns the record of it. Nothing is deleted or
#     renamed to make that true — the phantom is left exactly where it is for
#     whoever owns sample identity to deal with.
#
# A reading nobody can place is held and offered again next poll — see
# `_store_results`. Holding is bounded on both axes, because a bench must not
# spend the afternoon re-asking about work that is never coming, and the held
# queue is written to LabCore (`lem_held_results`) so a restart, a crash or a
# shift change cannot quietly take it with them.
#
# WHAT IS DURABLE AND WHAT IS NOT, precisely, because "held" now means two
# things. The HELD queue — readings LabCore has been asked about and could not
# place — is mirrored, because it can wait days. The IDENTITY BACKLOG —
# readings the per-poll ceiling has not got round to asking about — is memory
# only, because it is measured in polls, not days: it drains at
# IDENTITY_LOOKUP_CHUNK × IDENTITY_LOOKUP_MAX_CHUNKS readings a poll whatever
# anybody does, so mirroring it would write a few hundred kilobytes per poll
# into a queue that refuses past 100 pending in order to protect a window of a
# minute or two. A restart inside that window costs the automatic filing of what
# is left, never the record: every one of those readings is in lem_machine_log
# as it was parsed. `describe_held` says so on the status line rather than
# leaving the operator to assume otherwise.
#
# THERE IS NO ROAD OUT OF THIS FILE THAT INVENTS A SAMPLE. `insert_sample` does
# not appear anywhere in it, on any branch, for any failure. When LabCore cannot
# be asked the reading waits; the one exception is a gateway with no `samples`
# table AT ALL (see `identity_verdict`), where there is no identity to resolve
# against and no table for a phantom to appear in.

# How many unplaceable readings one bench keeps offering. A hundred of them is a
# paperwork problem, not a timing gap, and every one is in lem_machine_log as it
# was parsed (ISO/IEC 17025:2017 §7.5.1) and in lem_held_results as it waits —
# the record is not what is at stake, only the automatic filing.
HELD_ROW_LIMIT = 100
# And how long. A week covers a Friday-night run whose paperwork lands on Monday;
# past that the sample is not late, it is not coming.
HELD_ROW_MAX_AGE = timedelta(days=7)
# Cells already written, remembered so an unchanged reading offered again — a
# re-read source file, a restarted watch — does not re-stamp updated_at and cost
# a slot in a queue that refuses past 100 pending.
WRITTEN_CELL_MEMORY = 4000
# Ops LabCore's queue refused, kept for the next poll. Past this the OLDEST go:
# a newer reading of the same cell supersedes an older one, so the tail is the
# part worth keeping.
RETRY_OP_LIMIT = 200
# Printed Lab IDs per identity query. The lookup is one round trip per chunk
# rather than one for the whole poll, because the whole poll has no bound: a
# multi-CSV folder holding a weekend of archived prints is read in a single pass,
# and a query built from all of them exceeds SQLite's 999 bound variables (before
# 3.32). That comes back as an error, i.e. as "LabCore could not be asked", which
# would strand an entire backlog on the poll that most needed to land it.
#
# A hundred and fifty IDs is at most 300 keys and 900 parameters, inside every
# version. It used to be forty, because the query carried one LIKE term per key
# and an OR chain is a binary tree SQLite refuses deeper than 1000 — so the
# expression, not the parameters, set the chunk size. `build_sample_identity_query`
# no longer builds that chain (see its docstring), and the number of round trips
# a poll costs fell with it: the scan is the expensive part and it is now paid
# once per hundred and fifty IDs instead of once per forty.
IDENTITY_LOOKUP_CHUNK = 150
# And how many of those chunks one poll may ask. This is the ceiling the chunking
# alone does not give: the chunks are issued SEQUENTIALLY, on the worker, with
# `_polling` held, and every one of them is a full scan of `samples` — the
# predicate wraps `lab_id` in lower()/ltrim(), so no index can serve it, and on a
# lab with a few hundred thousand samples one scan is tens of milliseconds on the
# connection every other bench shares.
#
# A first run of a multi-CSV bench over an archive folder is one poll of a few
# thousand prints; unbounded, that was seventy-five consecutive scans before the
# bench answered anything. So a poll asks at most this many chunks and leaves the
# rest for the next one — two scans, three hundred readings, twelve seconds
# later. See `split_identity_backlog`, and `_identity_backlog` for where the
# remainder waits.
IDENTITY_LOOKUP_MAX_CHUNKS = 2
# And how many readings may be waiting their turn at that ceiling.
#
# This is NOT HELD_ROW_LIMIT and must not be: a hundred readings LabCore has
# been asked about and cannot place is a paperwork problem, and dropping the
# oldest is a defensible answer to it. A reading that has not been ASKED about
# yet is not unplaceable — it is work in progress, its sample is almost always
# sitting in `samples` already, and the queue drains at three hundred a poll
# whatever the operator does. Sending an archive import through a hundred-row
# cap would drop the other two thousand seven hundred readings and call it an
# overflow of a queue they were never in.
#
# It is bounded all the same, because "unbounded on the worker" is how this road
# got its other bug. Five thousand is more than a day of any real bench and a
# few hundred kilobytes of dicts; past it the OLDEST go, like everywhere else
# here, and they stay in lem_machine_log. It is memory only, deliberately — see
# "Whose sample is this?" for which queue is mirrored and why this one is not.
IDENTITY_BACKLOG_LIMIT = 5000
# How recently parsed a held reading has to be to be asked about on EVERY poll.
#
# The identity query is not free and cannot be made free: every arm wraps
# "lab_id" in lower()/ltrim(), and the third extracts the suffix of a dated ID,
# so no index can serve any of it and SQLite reads `samples` end to end —
# measured at 78ms on a 200,000-row table, whatever the chunk holds. One read
# per poll per bench is the price of the hold, and at a twelve-second cadence a
# reading held for its full week would ask fifty thousand times.
#
# It is asked at that cadence for the first hour, because a reading parsed
# minutes ago is exactly the one whose paperwork is being typed in right now and
# the operator is standing at the bench watching for it. Past an hour the
# paperwork is not landing in the next twelve seconds, and the sweep drops to
# HELD_RECHECK_SECONDS — a tenth of the reads, for at worst two minutes of extra
# latency on something already hours late. The tail is where all the volume is:
# it is the difference between fifty thousand reads and five thousand.
HELD_FRESH_WINDOW = timedelta(hours=1)
HELD_RECHECK_SECONDS = 120
# ── A resolved Lab ID is resolved for good ──────────────────────────────────
#
# Ryan's ruling on identity has a consequence nothing exploited: "its linear
# from 0 to indef", one monotonic sequence over the whole life of the lab, never
# reused. So once a printed ID has been shown to be exactly one sample, THAT
# MAPPING IS IMMUTABLE. Nothing in the lab can make "34566" stop being
# "081126-34566"; the sequence never comes round again to give the number to
# anybody else.
#
# The identity lookup sits on the critical path of every poll that produces
# rows, and `read_sql` POSTs to /api/queue/write, so each of those reads queues
# behind every write in the lab (MEMORY: labcore-write-queue-limits) and each is
# a full scan of `samples` that no index can serve. Measured: one read per poll
# for a steady bench, so at a twelve-second cadence five reads a minute per
# bench — fifty a minute across ten benches, and Ryan is adding more. Every one
# of them re-asked a question whose answer had already been proved unchangeable.
#
# So a certain resolution is remembered and a bench in steady state asks nothing
# at all. Three rules keep that from becoming a wrong answer:
#
#   • ONLY CERTAIN ONES. An ID placed by `closest_by_date` was placed by
#     measuring against one print's date and belongs to that reading, not to the
#     lab — see `resolve_lab_ids_certain`.
#   • NEVER A FAILURE. A sample the LIMS has not logged in yet is exactly what
#     the held queue exists for, and caching "no" would mean it never files. A
#     miss is a question, and questions get asked again.
#   • THE STANDARD FLAG IS PART OF THE KEY. A QC standard's Lab ID resolves
#     under a narrower rule than a customer sample's (`sample_matches`), so the
#     same printed ID has two possible answers depending on whether this bench's
#     QC assignment names it. Keyed on the pair, an ID that becomes a standard
#     later simply misses and is re-asked under the rule that now applies.
#   • NEVER AN ANSWER OUR OWN PHANTOM CAN STILL CHANGE, which is the rule the
#     first cut of this cache did not have, and it nearly made the bug this
#     whole road exists to end PERMANENT. "Certain" was read as "immutable", but
#     what a resolution names is a ROW OF `samples`, and `sample_matches`
#     deliberately lets the LIMS's dated record displace a bare one ("A BARE
#     MATCH BESIDE A DATED ONE IS OUR OWN FORGERY"). So one printed ID has two
#     certain answers at two different times: with only our phantom present,
#     `34566` resolves to `34566`; an hour later, when the LIMS logs in
#     `081126-34566`, the same call resolves to the dated record. Cache the
#     first and every later reading for that cup lands on the phantom and the
#     LIMS's cell stays blank forever — self-healing turned into permanent.
#     A bare answer is therefore treated as PROVISIONAL and never cached; only
#     the dated LIMS record is remembered, because displacement only ever runs
#     bare → dated and never back. A standard is exempt: `sample_matches` skips
#     displacement for standards entirely, so its exact match cannot move, and
#     standards are the IDs that print on every poll anyway.
#
# Bounded because a bench runs for months, and evicted least-recently-used:
# a miss after eviction costs one read and re-asks the real question, so the
# only thing a bound can cost is time. Ten thousand entries is a couple of
# megabytes of short strings, and more cups than any bench here sees in a year.
IDENTITY_CACHE_LIMIT = 10000

# And bounded in TIME as well as in count, because "the number is never reused"
# is a rule about the NUMBER and not about the row: an analyst who voids or
# deletes a sample mid-shift leaves a cached bench writing update_cell against a
# lab_id that no longer exists, outside the Results grid's INNER JOIN, invisible
# — stored and lost at once, with no read that could ever discover it. An entry
# is re-proved once an hour, which costs one read per cup per hour on a bench
# that keeps printing the same cup and nothing at all on one that does not.
IDENTITY_CACHE_SECONDS = 3600.0
# And how often the held queue's mirror in LabCore is rewritten. Writing it "only
# when it changes" is not a bound: on the one bench this exists for — a LIMS
# running behind — it changes on every poll, so the mirror became a fresh row of
# up to about eleven kilobytes every twelve seconds, into a queue that serialises
# roughly 1.5 writes a second and refuses past 100 pending (MEMORY:
# labcore-write-queue-limits).
#
# So an addition may wait up to a minute; a REMOVAL never waits. That asymmetry
# is the whole rule. A mirror missing a reading that is held costs, if the
# process dies inside that minute, custody of a reading still recorded in
# lem_machine_log. A mirror still naming a reading that has been FILED costs an
# analyst's correction: the next restart restores it and files it again, over
# whatever the cell holds by then. Only one of those is worth deferring.
HELD_PERSIST_SECONDS = 60


def _identity_keys(printed: str) -> List[str]:
    """The normalised forms one printed Lab ID can be looked up under."""
    low = str(printed or "").strip().lower()
    if not low:
        return []
    return sorted({low, low.lstrip("0")} - {""})


# The dated Lab ID's own shape, as SQL: everything after the FIRST dash, with
# leading zeros off, lowercased. `instr` answers 0 when there is no dash, and
# substr(x, 1) is then the whole string — which the first arm already covers, so
# an undated ID costs nothing and confuses nothing.
_ID_SUFFIX_SQL = ('ltrim(lower(substr(CAST("lab_id" AS TEXT), '
                  'instr(CAST("lab_id" AS TEXT), \'-\') + 1)), \'0\')')


def build_sample_identity_query(printed_ids: List[str]) -> tuple:
    """(sql, params) asking LabCore which samples one CHUNK of printed Lab IDs
    could be. `build_sample_identity_queries` does the chunking.

    The three arms are `_check_test_assignments`'s own (LabStation.pyw:12691) in
    substance — exact, leading-zero tolerant, and the cup number at the end of a
    dated ID — and they are a PREFILTER only: `sample_matches` re-checks every
    candidate. Returning too many candidates is harmless; returning too few
    would lose a reading, which is what decides every trade below.

    ALL THREE ARMS ARE ONE IN-LIST EACH. The dated arm used to be one
    `LIKE '%-<key>'` term per key, OR-ed together, and that shape cost twice
    over: SQLite refuses an OR chain deeper than 1000, which capped a chunk at
    forty IDs, and every row of `samples` was matched against up to eighty
    leading-wildcard patterns — measured at 0.78s for one forty-ID chunk on a
    200,000-row table, or 3.9s for a poll at the old ceiling, on the connection
    the whole lab shares. Extracting the suffix ONCE per row and looking it up in
    an IN list is the same question asked the other way round: the expression is
    now a fixed size whatever the chunk holds, so the chunk grew to a hundred and
    fifty and the poll costs two scans instead of five.

    It also narrows the dated arm to the suffix after the FIRST dash, which is
    the LIMS's own "mmddyy-labid" form and the only one `sample_id_date` can read
    a date out of. `sample_matches` matches the same way, so prefilter and
    matcher agree exactly — a candidate neither of them would accept is not a
    candidate this road ever had a use for.

    No index can serve any of this: a function on the column defeats the primary
    key, and the suffix is not a prefix. That is why the ceiling above exists.
    """
    keys: List[str] = []
    seen: set = set()
    for printed in printed_ids:
        for key in _identity_keys(printed):
            if key not in seen:
                seen.add(key)
                keys.append(key)
    if not keys:
        return "", []
    holes = ", ".join("?" for _ in keys)
    sql = ('SELECT DISTINCT "lab_id" AS lab_id FROM "samples" WHERE '
           'lower(CAST("lab_id" AS TEXT)) IN (%s)'
           ' OR ltrim(lower(CAST("lab_id" AS TEXT)), \'0\') IN (%s)'
           ' OR %s IN (%s)' % (holes, holes, _ID_SUFFIX_SQL, holes))
    return sql, list(keys) * 3


def build_sample_identity_queries(printed_ids: List[str]) -> List[tuple]:
    """One (sql, params) per chunk of printed Lab IDs, in order.

    Chunked so that no single poll can be too big to ask about. A chunk that
    comes back an error costs only the readings named in it — the rest of the
    poll still files.
    """
    wanted: List[str] = []
    seen: set = set()          # "seen before" is a hash question — see row_lab_ids
    for printed in printed_ids:
        key = str(printed or "").strip()
        if key and key not in seen:
            seen.add(key)
            wanted.append(key)
    out: List[tuple] = []
    for start in range(0, len(wanted), IDENTITY_LOOKUP_CHUNK):
        chunk = wanted[start:start + IDENTITY_LOOKUP_CHUNK]
        sql, params = build_sample_identity_query(chunk)
        if sql:
            out.append((sql, params, chunk))
    return out


def sample_matches(printed: str, candidates: List[str],
                   standard: bool = False) -> List[str]:
    """The samples this printed Lab ID could be, strongest tier only.

    Tiered, strongest first: an ID that IS a sample is that sample whatever else
    it resembles; only then a leading-zero difference; only then the cup number
    at the end of a dated ID. `_check_test_assignments` pools the three, which is
    fine for one ID an operator just typed and watched resolve, and not fine for
    an unattended bench — the tiers stop "34566" being called ambiguous merely
    because a sample is literally named 34566 and another ends in it.

    TWO EXCEPTIONS TO THE TIER ORDER, and both are about samples that are not
    what they look like.

    A BARE MATCH BESIDE A DATED ONE IS OUR OWN FORGERY. Every bench in this lab
    has run the pristine code, and it wrote `insert_sample` under whatever the
    instrument printed on every poll — so `samples` holds a bare "34566" next to
    the LIMS's "081126-34566" for every cup this software has ever processed.
    Taking the exact tier there files the reading back onto the phantom and
    leaves the LIMS's own cell blank, which is the whole bug. Under the lab's
    identity rule — the number is one never-reused sequence, so the pair is one
    sample — the dated form is the LIMS's record and the undated one is ours, so
    the dated form wins. It only ever fires where BOTH exist: a lab whose samples
    are genuinely bare has no dated twin and keeps the exact match.

    A STANDARD IS NEVER A DATED SAMPLE. `standard` says this printed ID is one of
    the bench's QC standards, and a standard's Lab ID is a name somebody gave a
    bottle, not a number from the sample sequence. Matching it to the tail of
    "081126-1234" is a coincidence, and acting on the coincidence writes a
    control check onto a customer's result — the worst thing on this road. So a
    standard resolves by exact or leading-zero match only, and otherwise resolves
    to nothing: its verdict is already recorded as a 'qc' event, and it is not
    held either (see "A standard is a check").

    More than one name in the winning tier is returned as-is. Deciding between
    them is `closest_by_date`'s job and telling the operator about it is
    `describe_held`'s, and both need to know it happened: "two samples answer to
    this" and "no sample answers to this" are different facts about the lab.
    """
    keys = _identity_keys(printed)
    if not keys:
        return []
    exact: List[str] = []
    zero_padded: List[str] = []
    dated: List[str] = []
    for candidate in candidates:
        name = str(candidate or "").strip()
        low = name.lower()
        if not low:
            continue
        _head, dash, tail = low.partition("-")
        if low in keys:
            exact.append(name)
        elif low.lstrip("0") in keys:
            zero_padded.append(name)
        elif dash and (tail in keys or tail.lstrip("0") in keys):
            # The suffix after the FIRST dash, which is what the prefilter asks
            # for and what `sample_id_date` reads a date out of. Matching any
            # trailing "-34566" instead would accept names neither of those two
            # can act on, and the prefilter would not have returned them anyway.
            dated.append(name)
    later = sorted(set(dated))
    # Only a candidate carrying a READABLE date can displace an exact match. The
    # phantom's twin is the LIMS's own "mmddyy-labid" and nothing else; a lab
    # whose sample happened to be called "BATCH-34566" would otherwise take the
    # reading off the sample literally named 34566, which is a wrong-sample
    # write invented to fix one.
    lims = [name for name in later if sample_id_date(name) is not None]
    for tier in (exact, zero_padded):
        found = sorted(set(tier))
        if not found:
            continue
        if lims and not standard and all("-" not in name for name in found):
            return lims
        return found
    return [] if standard else later


def sample_id_date(name: str) -> Optional[datetime]:
    """The date the LIMS stamped on a dated Lab ID — "081126-34566" is the
    eleventh of August 2026 — or None where there is no readable one.

    Only the dated form is read, and only when the prefix really is six digits
    that make a date. A bare "034566" is a number, not the third of April, and
    reading it as one would invent a distance between two candidates that have
    none — which is exactly the guess `closest_by_date` exists to avoid.
    """
    head, dash, _rest = str(name or "").strip().partition("-")
    if not dash or len(head) != 6 or not head.isdigit():
        return None
    try:
        return datetime(2000 + int(head[4:6]), int(head[0:2]), int(head[2:4]))
    except ValueError:
        return None


def closest_by_date(candidates: List[str],
                    when: Optional[datetime]) -> Optional[str]:
    """Of several samples answering to one printed Lab ID, the one whose stamped
    date is NEAREST `when` — or None when nothing separates them.

    This is the lab owner's rule for a case the lab owner says cannot arise:
    "This can never happen because its linear from 0 to indef. But if it does
    choose the closer date." The cup number is one sequence over the life of the
    lab, so two samples carrying it is a defect in the data rather than ordinary
    traffic — and a defect must not stop a bench filing its work. The nearest
    date is the answer the lab would give.

    WHAT `when` ACTUALLY IS, plainly, because the comments here used to call it
    "the print's own date" and it is not. It is `_row_time` — the reading's
    `parsed_date`/`parsed_time`, which is this module's clock at the moment the
    print was parsed. Nothing on this road reads a date off the print itself:
    `parse_print` extracts the mapped values and the Lab ID, no more, and the
    instruments here do not agree on a date format worth capturing. Making it
    read the print's date would mean a new mapping the operator has to make and
    a silent behaviour change on every bench that has not made it.

    The bench clock is a good enough proxy for exactly the reason this function
    is allowed to exist at all: it is only consulted when the data already holds
    a defect, and the two things it must tell apart are samples stamped DAYS
    apart. A print is parsed within seconds of being taken on a serial or
    single-CSV bench, and within a poll of being dropped into the folder on a
    multi-CSV one. It is NOT a good proxy on the one path where it drifts —
    a first run over an archive folder, where a week-old print is parsed today —
    and there it does not matter either: `_store_results_once` gives an ID no
    date at all when its prints span more than one day, so those readings are
    held rather than measured. The rule holds: a distance that cannot be
    trusted is not measured, and an unmeasured tie is held.

    Compared by DATE and not by timestamp, because a Lab ID carries a date and
    nothing finer. Measuring by the clock instead makes a print parsed after
    lunch nearer to TOMORROW's stamp than to today's, so a two-o'clock reading
    would be filed on the next day's sample — precisely the wrong-sample write
    this whole road exists to stop, arrived at by arithmetic.

    None is returned only when the dates genuinely cannot decide: no print date
    to measure from, no candidate carrying a readable date, or two candidates
    equally near. Then the reading is held, because there is nothing left to
    choose with and a coin toss files a real result onto the wrong sample.
    """
    if when is None:
        return None
    day = datetime(when.year, when.month, when.day)
    best: Optional[str] = None
    best_gap: Optional[float] = None
    tied = False
    for name in candidates:
        stamped = sample_id_date(name)
        if stamped is None:
            continue
        gap = abs((stamped - day).total_seconds())
        if best_gap is None or gap < best_gap:
            best, best_gap, tied = name, gap, False
        elif gap == best_gap:
            tied = True
    return None if tied else best


def resolve_lab_id(printed: str, candidates: List[str],
                   when: Optional[datetime] = None,
                   standard: bool = False) -> Optional[str]:
    """The one sample LabCore holds for this printed Lab ID, or None.

    `when` is when the reading was PARSED — this module's clock, not a date off
    the print; see `closest_by_date` — and it is what breaks a tie inside a
    tier — see `closest_by_date`. Without it, or with a tie it cannot break,
    the answer is None and the reading is held rather than filed on a guess.
    `standard` marks a QC standard's Lab ID; see `sample_matches`.
    """
    found = sample_matches(printed, candidates, standard=standard)
    if len(found) == 1:
        return found[0]
    if found:
        return closest_by_date(found, when)
    return None


def resolve_lab_ids(printed_ids: List[str], candidates: List[str],
                    dates: Optional[Dict[str, datetime]] = None,
                    standards=()) -> tuple:
    """({printed Lab ID: the sample it is}, {printed Lab ID: the samples it
    could be, with nothing to choose between them}).

    An ID in neither map matched nothing. `dates` maps a printed Lab ID to when
    its print was taken; an ID that reaches the second map had several
    candidates AND its date could not separate them, which takes a data defect
    on top of a data defect. Everything else is placed.

    `standards` is the set of Lab IDs this bench's QC standards print under,
    lowercased, and it changes which matches are allowed at all — see
    `sample_matches`. A standard that resolves to nothing is not ambiguous and
    is not held; its verdict is already in the machine log.
    """
    return resolve_lab_ids_certain(printed_ids, candidates, dates,
                                   standards=standards)[:2]


def resolve_lab_ids_certain(printed_ids: List[str], candidates: List[str],
                            dates: Optional[Dict[str, datetime]] = None,
                            standards=()) -> tuple:
    """As `resolve_lab_ids`, plus the set of printed Lab IDs whose answer did
    NOT depend on a tiebreak.

    The third value exists for the resolved-ID cache (see IDENTITY_CACHE_LIMIT),
    and the distinction it draws is the whole reason that cache is safe. An ID
    that matched exactly one sample matched one sample, full stop: under the
    lab's identity rule the number is issued once and never reused, so that
    answer is a fact about the lab and cannot change. An ID that matched several
    and was placed by `closest_by_date` was placed by measuring against the date
    of THIS print — a different print of the same number would measure
    differently — so the answer belongs to the reading, not to the lab, and
    remembering it would turn a data defect into a permanent wrong answer.

    Only the first kind is certain. Everything else — ambiguous, unmatched, or
    decided by a date — is left to be asked again.
    """
    dates = dates or {}
    standards = {str(s or "").strip().lower() for s in standards}
    out: Dict[str, str] = {}
    ambiguous: Dict[str, List[str]] = {}
    certain: set = set()
    for printed in printed_ids:
        key = str(printed or "").strip()
        if not key or key in out or key in ambiguous:
            continue
        found = sample_matches(key, candidates,
                               standard=key.lower() in standards)
        if len(found) == 1:
            out[key] = found[0]
            certain.add(key)
        elif found:
            chosen = closest_by_date(found, dates.get(key))
            if chosen:
                out[key] = chosen
            else:
                ambiguous[key] = found
    return out, ambiguous, certain


def identity_of_last_resort(printed_ids: List[str]) -> Dict[str, str]:
    """The printed Lab ID standing as its own identity.

    ONE caller, and it is not a fallback for a failed lookup: a gateway with no
    `samples` table at all has no sample identity to resolve against, so the
    printed ID is the only identity there is, and no phantom can be minted in a
    table that does not exist. LabCore itself always has that table
    (LabCore.py), so on the real thing this map is never built — it exists for a
    deployment whose gateway is something else.

    Every other failure — a busy queue, a timeout, a query the database
    refused — holds the reading instead. Those are questions we could not ask,
    not answers, and filing on them is how a reading ends up on a row nothing
    can read.
    """
    return {key: key for key in
            (str(p or "").strip() for p in printed_ids) if key}


def identity_verdict(result) -> str:
    """What LabCore's answer to the identity query actually was:

        "answered"   — rows came back (possibly none); act on them.
        "no samples" — this gateway has no `samples` table; there is no identity
                       to resolve against, so the printed ID is the identity.
        "unknown"    — anything else. NOT an answer. Hold and ask again.

    The string match is on sqlite3's own wording, and it is deliberately narrow:
    everything it fails to recognise falls into "unknown", which holds. A busy
    queue, a refused read, an expression the database would not compile all read
    as "we did not get to ask", because that is what they are.
    """
    if not isinstance(result, dict):
        return "unknown"
    error = str(result.get("error") or "")
    if not error:
        return "answered"
    if "no such table" in error.lower():
        return "no samples"
    return "unknown"


def row_lab_ids(rows: List[dict]) -> List[str]:
    """The printed Lab IDs on these rows, in order, once each. A row with no
    Lab ID names no sample and is not a result anybody can file.

    The "once each" is remembered in a SET rather than tested against the list
    being built. Order still comes from the list; the set only answers "seen
    before", which a list answers in linear time. This is called several times
    per poll on the whole waiting queue, on the worker with `_polling` held, and
    at the 5,000-row backlog the list form measured 0.194s a poll between this
    and `split_identity_backlog` — a fifth of a second of a twelve-second poll
    spent on a question a hash answers instantly.
    """
    out: List[str] = []
    seen: set = set()
    for row in rows:
        lab_id = str(row.get(LAB_ID_KEY) or "").strip()
        if lab_id and lab_id not in seen:
            seen.add(lab_id)
            out.append(lab_id)
    return out


def build_result_cells(rows: List[dict],
                       identities: Dict[str, str]) -> List[dict]:
    """update_cell ops for the readings whose sample LabCore confirmed it holds.

    The only builder of LabCore result ops in this module, and there is no
    insert_sample in it: the Lab ID written is the one LabCore answered with,
    never the one the instrument printed. A row whose ID was not placed produces
    nothing — the caller is holding it.

    The values are whatever is on the row, which is the CORRECTED reading (see
    `apply_row_corrections`). This is the reported result.
    """
    ops = []
    for row in rows:
        printed = str(row.get(LAB_ID_KEY) or "").strip()
        lab_id = identities.get(printed, "") if printed else ""
        if not lab_id:
            continue
        for key, value in row.items():
            if key in RESERVED_ROW_KEYS:
                continue
            if value in (None, ""):
                continue
            ops.append({"operation": "update_cell",
                        "params": {"lab_id": lab_id, "test_name": key,
                                   "value": str(value)}})
    return ops


def result_cell_key(op: dict) -> tuple:
    """What makes two writes the same write: sample, test, and value."""
    params = op.get("params") or {}
    return (str(params.get("lab_id") or ""), str(params.get("test_name") or ""),
            str(params.get("value") or ""))


def identity_lookup_ids(rows: List[dict], now: datetime,
                        last_sweep: Optional[datetime]) -> tuple:
    """(the printed Lab IDs to ask LabCore about now, was this a full sweep).

    Every reading parsed within HELD_FRESH_WINDOW is asked about on every poll;
    the rest of the queue only on the slower clock, because the question costs a
    full scan of `samples` (see HELD_RECHECK_SECONDS) and the answer for a
    reading that has been waiting since Friday does not change in twelve
    seconds. A sweep asks about everything and resets the clock.

    A row with no timestamp is treated as new, which is the safe direction: it
    is asked about more often than it needs to be, never less.
    """
    sweep = (last_sweep is None
             or (now - last_sweep).total_seconds() >= HELD_RECHECK_SECONDS)
    if sweep:
        return row_lab_ids(rows), True
    return row_lab_ids([row for row in rows
                        if now - _row_time(row, now) <= HELD_FRESH_WINDOW]), False


def split_identity_backlog(printed_ids: List[str]) -> tuple:
    """(the Lab IDs this poll asks LabCore about, the ones it leaves for the
    next).

    The chunking in `build_sample_identity_queries` bounds the SIZE of each
    question; this bounds how many are asked at all. Without it the number of
    round trips is whatever the poll happened to parse — a first run over an
    archive folder is thousands of prints, so seventy-five sequential full scans
    of `samples` on the lab's shared, serialised connection while `_polling` is
    held and every other bench waits behind them.

    Deferring is cheap here in a way it is nowhere else on this road, because
    nothing has been decided about a deferred reading: it has not been asked
    about, so it is not "unplaceable", and it is back at the front of the queue
    twelve seconds later. See `_identity_backlog`, which is deliberately NOT the
    held queue.

    That is only true while the NEXT poll asks about it, and for one round it
    was not: the caller rebuilt the backlog out of whatever the freshness filter
    had asked about, so a deferred reading whose print was stamped over an hour
    ago disappeared from the ask, came back classed as unplaceable, and was
    shredded by the hundred-row cap — on an archive import, which is prints from
    days ago and the exact case this ceiling exists for. `_store_results_once`
    now exempts a never-asked row from that filter, which is the invariant this
    docstring's "back at the front of the queue" depends on.
    """
    limit = IDENTITY_LOOKUP_CHUNK * IDENTITY_LOOKUP_MAX_CHUNKS
    wanted: List[str] = []
    seen: set = set()          # "seen before" is a hash question — see row_lab_ids
    for printed in printed_ids:
        key = str(printed or "").strip()
        if key and key not in seen:
            seen.add(key)
            wanted.append(key)
    return wanted[:limit], wanted[limit:]


# ── A print with no Lab ID names no sample ───────────────────────────────────
#
# `parse_print` keeps a print that produced measurements but no Lab ID, and it
# is right to: the reading happened, it belongs in lem_machine_log and on the
# card, and a purge or standby report that prints values under no sample name is
# ordinary on plenty of benches. What such a reading can never be is FILED —
# there is no sample to file it against, and no poll of any future week will
# give it one.
#
# The old batch builder skipped it silently, which was right about the write.
# Holding it, which is what the first cut of the held queue did, is worse than
# either: it waits seven days for an answer that cannot come, its notice reads
# "reading(s) held for  —" naming nothing anybody can act on, and on a bench
# whose Lab ID capture has broken — a firmware update that moved the line, a
# mapping made against an older print layout — it is EVERY print, so within
# twenty minutes the queue is full of rows that can never leave and has evicted
# the genuinely late reading it exists for.

def split_unidentified(rows: List[dict]) -> tuple:
    """(readings that name a sample, readings that name none)."""
    named, nameless = [], []
    for row in rows:
        (named if str(row.get(LAB_ID_KEY) or "").strip()
         else nameless).append(row)
    return named, nameless


# ── A standard is a check, not a submitted sample ────────────────────────────
#
# `_queue_run_events` already argues this: a print whose Lab ID is a QC standard
# logs 'qc' verdicts and NOT a 'run', because a standard is not work anybody
# ordered. The results road has to agree with it, and until this it did not.
#
# It matters because of what removing insert_sample changed. A standard's Lab ID
# is very often not a row in `samples` — on older benches it only ever became
# one because LEM's own insert_sample minted it years ago. Treated as a customer
# result it can never resolve, so it would be held for seven days, occupy the
# retry queue that late customer readings need, and then expire under a message
# saying the reading was never matched to a sample. On a `manual` bench, where
# every row IS a QC reading, that would be every reading the bench ever takes.
#
# So: a standard's reading is COMPLETE when its verdict is recorded. That record
# is the 'qc' event in lem_machine_log — the same row `build_last_qc_query`
# reads back to rebuild this module's own verdicts after a restart, and the row
# the master view draws the band from. If the lab does keep its standards in
# `samples`, the reading is filed there as well, exactly as before; if it does
# not, nothing is held and nothing is lost.

def qc_standard_ids(machine) -> set:
    """The Lab IDs this bench's assigned QC standards print under, lowercased.

    Read off the specs, which come from LabCore — LEM has no test names or
    standards of its own. A spec naming no standard contributes nothing: it
    cannot be recognised on a print either.
    """
    out = set()
    for spec in getattr(machine, "tests", None) or []:
        sample_id = str(getattr(spec, "sample_id", "") or "").strip().lower()
        if sample_id:
            out.add(sample_id)
    return out


def split_qc_standards(rows: List[dict], standard_ids: set) -> tuple:
    """(customer results, control checks) — the readings that name a sample the
    lab submitted, and the ones that name one of this bench's standards."""
    results, checks = [], []
    for row in rows:
        lab_id = str(row.get(LAB_ID_KEY) or "").strip().lower()
        (checks if lab_id and lab_id in standard_ids else results).append(row)
    return results, checks


def expire_held_rows(rows: List[dict], now: datetime) -> tuple:
    """(still worth offering, given up on) — held readings split by age.

    Giving up is not losing the reading: `_queue_run_events` wrote it to
    lem_machine_log the moment it was parsed, with its values and any correction
    applied, which is the record ISO/IEC 17025:2017 §7.5.1 asks for. What is
    given up is only the automatic filing, and the operator is told.
    """
    keep, expired = [], []
    for row in rows:
        if now - _row_time(row, now) > HELD_ROW_MAX_AGE:
            expired.append(row)
        else:
            keep.append(row)
    return keep, expired


def describe_held(rows: List[dict], ambiguous: Dict[str, List[str]],
                  unknown=(), backlog=()) -> str:
    """One line saying what this bench is holding and why, or "".

    Recomputed every poll and carried on the payload rather than appended to
    `messages`, because `messages` is a running commentary whose LAST entry wins
    the status line — so a hold notice could be, and was, overwritten by
    "Recovered 3 QC result(s) from LabCore." from later in the same sync. A
    reading that has not been filed outranks routine news for as long as it is
    unfiled, so it is state, not an event.

    Different reasons get different sentences, because they ask different things
    of the operator. "No LabCore sample matches 34566" is FALSE when two of them
    do, and it sends the operator to log a third sample named 34566 — which then
    wins the exact tier outright and takes the reading. It is equally false when
    LabCore could not be asked at all, where there is nothing to log in and
    nothing to do but wait, and when the bench simply has not got to the
    question yet.

    NOTHING HERE EVER ASKS ANYBODY TO RENAME OR CLOSE A SAMPLE. It used to, on
    the ambiguous branch, and it was the most destructive sentence in the file:
    `sample_tests` has no foreign key onto `samples` and no cascade, so renaming
    a sample orphans every result already filed against it — the exact failure
    this whole road exists to remove, printed as advice.
    """
    parts: List[str] = []
    if rows:
        ids = row_lab_ids(rows)
        # Only empty if a reading naming no sample got into the queue, which
        # `split_unidentified` exists to prevent and an older mirror could still
        # be holding: "held for  —" names nothing an operator can act on, so in
        # that case the sentence says less rather than saying nothing.
        named = ", ".join(ids[:3]) + ("…" if len(ids) > 3 else "")
        named = f" for {named}" if named else ""
        if any(lab_id in set(unknown) for lab_id in ids):
            parts.append(f"{len(rows)} reading(s) held{named} — LabCore cannot "
                         "say what samples it holds; nothing is filed on a "
                         "guess.")
        elif ambiguous:
            # A tie the reading's parse date could not break — `closest_by_date`
            # — which takes two samples stamped the same day carrying a number
            # the lab issues once. The candidates are named because that is the
            # data defect, and the remedy is deliberately left to the person who
            # owns sample identity: LEM saying "rename one" would orphan every
            # result already filed against it, and LEM choosing for them would
            # make this the second place in the lab where identity is decided.
            first = sorted(ambiguous)[0]
            parts.append(
                f"{len(rows)} reading(s) held: more than one LabCore sample "
                f"answers to {first} ({', '.join(ambiguous[first][:3])}) and "
                "their dates cannot separate them; the readings are in the "
                "machine log and nothing is filed on a guess.")
        else:
            parts.append(f"{len(rows)} reading(s) held{named} — no LabCore "
                         "sample matches yet; they go out as soon as one is "
                         "logged in.")
    if backlog:
        # Not "held": nobody has asked about these yet, and the answer is very
        # nearly always going to be yes. It is said anyway because a bench
        # working through an archive would otherwise read "Ready." for the ten
        # minutes it takes, and an operator who cannot see the queue moving
        # reasonably concludes it is stuck.
        #
        # And it says where they are, because unlike the held queue they are not
        # mirrored into LabCore (see "Whose sample is this?"). An operator who
        # is about to close the station during an import is the one person who
        # can act on that, and telling them costs six words.
        parts.append(f"{len(backlog)} more waiting their turn to be matched — "
                     "the bench works through them a poll at a time, at the "
                     "bench and in the machine log until it does.")
    return " · ".join(parts)


def describe_parked(rows: List[dict]) -> str:
    """One line for readings this bench is keeping because LabCore could not be
    ASKED at all, or "".

    Separate from `describe_held` because it is a different fact: a held reading
    has been offered to LabCore and refused a sample, a parked one has never
    left the bench. It exists because the two branches that park — no
    labcore_* helpers on the canvas, and `labcore_is_running()` False — used to
    say nothing at all while the count climbed toward `HELD_ROW_LIMIT`, so the
    status line read "Ready." right up to the poll that silently dropped the
    hundred-and-first reading.
    """
    if not rows:
        return ""
    ids = row_lab_ids(rows)
    named = ", ".join(ids[:3]) + ("…" if len(ids) > 3 else "")
    named = f" for {named}" if named else ""
    return (f"{len(rows)} reading(s) kept at the bench{named} — LabCore has "
            f"not been reachable to file them (limit {HELD_ROW_LIMIT}).")


# How many sentences the status line carries before it starts counting them.
# Three is about what fits on a module's width at the shipped font; past that
# the line stopped being read at all, which is the same as saying nothing.
STATUS_LINE_PARTS = 3


def _loss_line(parts: List[str]) -> str:
    """The status line, condensed so that the worst poll is still readable.

    Every notice on this road is joined with ' · ' into one label, and the poll
    that loses the most readings is the poll with the most to say — so the
    sentences that mattered most were the ones that ran off the end of the
    widget. The FIRST ones are kept, because the order they arrive in is already
    worst-first: a reading this poll gave up on, then every cap that discarded
    one, then what is still waiting, then routine news.

    The remainder is counted rather than dropped, so the operator knows to look;
    `_show_outcome` puts all of it on the tooltip, whole. Nothing here is the
    only record of anything — every reading these sentences name is in
    lem_machine_log.
    """
    parts = [part for part in parts if part]
    if len(parts) <= STATUS_LINE_PARTS:
        return " · ".join(parts)
    rest = len(parts) - STATUS_LINE_PARTS
    return " · ".join(parts[:STATUS_LINE_PARTS]
                      + [f"(+{rest} more — hover for all of it)"])


def cap_held_rows(rows: List[dict]) -> tuple:
    """(the rows kept, the rows the COUNT cap dropped) — oldest out first.

    One place, because the cap has to mean the same thing in all three: the
    queue the bench keeps, the queue it mirrors into LabCore, and the eviction
    the mirror must not mistake for a filing. It used to be applied in
    `_commit_held` only, so `_persist_held` was handed the uncapped list and one
    poll of a first-run multi-CSV bench serialised thousands of rows into a
    single LabCore row — measured at 288,000 bytes — of which all but a hundred
    were discarded microseconds later.

    Oldest first for the reason `_commit_held` gives: a reading that has had
    every poll of the week to resolve and has not is the weakest claim on the
    last slot.
    """
    dropped = max(0, len(rows) - HELD_ROW_LIMIT)
    return list(rows[dropped:]), list(rows[:dropped])


# The held queue, in LabCore. A reading that has been parsed, corrected and
# judged but not yet filed lives ONLY in this module's memory otherwise, and a
# restart at shift change would take it with no trace but the machine log. This
# is one row per bench holding the whole queue as JSON, rewritten only when the
# queue actually changes, so an idle bench costs nothing. It is also the floor's
# answer to "what has this bench got waiting" without a human joining logs.
HELD_TABLE_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_held_results ("
    "machine_uid TEXT PRIMARY KEY, updated_at TEXT, held TEXT)"
)

HELD_QUERY = "SELECT held FROM lem_held_results WHERE machine_uid = ?"


def build_held_upsert(machine_uid: str, rows: List[dict],
                      now: datetime) -> tuple:
    """(sql, args) storing this bench's held queue as it stands.

    `default=str` because a row carries whatever the parser put on it and a
    record that cannot be serialised must not take the sync down with it — a
    stringified value still names the reading for a human.
    """
    return ("INSERT INTO lem_held_results (machine_uid, updated_at, held) "
            "VALUES (?, ?, ?) ON CONFLICT(machine_uid) DO UPDATE SET "
            "updated_at = excluded.updated_at, held = excluded.held",
            [machine_uid, now.isoformat(timespec="seconds"),
             json.dumps(list(rows), default=str)])


def parse_held_payload(rows) -> tuple:
    """(the held queue read back from LabCore, was the stored row READABLE).

    Anything unreadable yields nothing rather than raising: this runs on the
    worker, and a corrupt row must not cost the bench its poll. The newest
    HELD_ROW_LIMIT are kept, and `expire_held_rows` still has the last word on
    age — a bench that was off for a fortnight must not wake up re-offering a
    fortnight of readings.

    The second half of the answer exists because the first half cannot tell the
    two failures apart, and they need opposite handling. "The bench was holding
    nothing" is the ordinary case and needs no words. "The bench's stored queue
    is corrupt" means readings that were parked against a restart may be gone,
    and the row will sit there being re-read and re-discarded on every restart
    until somebody is told — which, returning only a list, nobody ever was. See
    `_restore_held`, which says so and then overwrites the unreadable row.
    """
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            held = json.loads(str(row.get("held") or "[]"))
        except (TypeError, ValueError):
            return [], False
        if isinstance(held, list):
            return [r for r in held if isinstance(r, dict)][-HELD_ROW_LIMIT:], True
        # Valid JSON that is not a list: something wrote over this row with a
        # shape this module never produces. Unreadable, for the same reason.
        return [], False
    return [], True


def parse_held_rows(rows) -> List[dict]:
    """The held queue read back from LabCore — the rows alone. See
    `parse_held_payload` for whether the stored row could be read at all."""
    return parse_held_payload(rows)[0]


HEARTBEAT_TABLE_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_machine_heartbeat ("
    "machine_uid TEXT PRIMARY KEY, last_poll TEXT, watching TEXT)"
)

# How often a module proves it is alive. Data writes are event-driven, so
# without this a stopped module and an idle instrument look identical.
HEARTBEAT_SECONDS = 300

# How often a bench re-asks LabCore for its CONFIGURATION — the shared QC
# standards, the floor's QC assignment, per-machine spec overrides, and the
# PM/Cal schedule.
#
# These were re-read on every poll, which at the 30s default is five reads a
# poll on a bench that is doing nothing: ten LabCore operations a minute per
# bench, a hundred a minute across ten of them, into the endpoint reads and
# writes share. That is the standing load behind the lock storms — far more than
# the heartbeat, which is one write per bench per five minutes. None of these
# four answers changes on the timescale of a poll: a QC standard, an assignment
# made on the floor, a spec override and a PM interval are all things somebody
# edits occasionally and by hand.
#
# Two minutes is chosen against the one case where the delay is felt: QC
# assigned in LEM has to reach a bench that has none, because until it arrives a
# manual bench cannot log anything (see `_rebuild_manual_methods`). Waiting up to
# two minutes for that is an operator noticing on their next glance; waiting for
# a restart would not be. Everything urgent is deliberately outside this window —
# the floor's manual override is read on EVERY poll, because it is the lever
# somebody pulls to take a bench off line.
CONFIG_REFRESH_SECONDS = 120

# The same window, for the same reason, on the one read that never had one.
#
# The correction factors were re-read at the top of EVERY poll — two of the 6.2
# LabCore reads a minute an idle bench makes, per bench, for a table that holds
# an instrument's calibration offsets. Those are set when the instrument is
# calibrated and otherwise touched a handful of times a year; nothing that
# happens during a poll changes one.
#
# This gates the FREQUENCY and nothing else. The read still happens BEFORE the
# parse and `apply_row_corrections` still runs on every row of every poll,
# because the factor applied to a measurement has to be the one in force when it
# was made (ISO/IEC 17025 §7.8.2) — and that is a claim about ORDER, not about
# cadence. What a window costs is that an offset edited elsewhere can be up to
# two minutes old; a reading corrected with the offset that was in force two
# minutes ago is still corrected with an offset that was in force.
#
# The edit made HERE is deliberately outside the window: `_open_corrections`
# drops the stamp, so an operator standing at the bench sees their own change on
# the very next poll. Two minutes of the old factor after their own save is
# indistinguishable from the save not having worked.
#
# There are TWO windows because there are two ways an edit made elsewhere can
# reach this bench. The floor's web server now answers the live push with a note
# naming what changed (see "the note the floor sends back", below), and where
# that channel is delivering the read is only a BACKSTOP against a note that got
# lost — so it can be long. Where it is not delivering, the backstop is the only
# path there is, and it goes back to being the short window it was before the
# note existed. `_corrections_due` picks between them; nothing else may.
CORRECTIONS_REFRESH_SECONDS = 900
CORRECTIONS_REFRESH_UNSIGNALLED_SECONDS = 120

# The same pair for the floor's manual override, `lem_machine_control`.
#
# This read had no window AT ALL until the note channel existed, and the reason
# is worth restating rather than deleting: it is the lever somebody on the floor
# pulls to take a bench OFF LINE, and a bench that keeps running for the length
# of a refresh window after being overridden is the one delay nobody in this lab
# would accept. The note is what makes a window defensible — it carries the news
# within one poll, the same 30s the ungated read gave, without the two LabCore
# ops a minute that read cost on every bench in the building.
#
# So the window is CONDITIONAL, and `_override_due` is where that is enforced:
# no floor configured, or a floor that is not taking pushes, and this read goes
# straight back to every poll. There is no third behaviour. Anything that widens
# the definition of "the channel is healthy" is trading a bench that stays live
# after somebody switched it off against a fraction of one LabCore op a minute.
OVERRIDE_REFRESH_SECONDS = 900

# How long a module waits before asking again for a configuration LabCore could
# not hand over, and the ceiling that wait grows to.
#
# The read that binds a bench to its instrument goes through the same queue as
# everything else in the lab, and LabStation restores its canvas one module at a
# time through a QTimer chain at start-up — so a restart is exactly when this
# read is most likely to find LabCore not ready. It used to be asked once.
#
# Backing off matters as much as retrying: the reason the first attempt failed
# is usually that the queue is congested, and ten benches asking every second
# would be the congestion. Doubling from five seconds to a minute means a bench
# is back within a few seconds of LabCore answering, without adding load while
# it is not.
BIND_RETRY_SECONDS = 5.0
BIND_RETRY_MAX_SECONDS = 60.0

# The same wait, for the same reason, on the table declarations.
#
# `_declare_tables` fires nine `IF NOT EXISTS` declarations — seven tables and
# the log's two indexes — and latches its flag only if every one was ACCEPTED —
# a refused declaration is not a declaration. What was missing is what happens next. LabCore turns work away
# when its queue is deep by returning an error dict, so a congested LabCore left
# the flag down and every subsequent poll, on every bench, re-fired those
# statements straight at the queue that had just said it was full. A slow queue
# produced more work, which made it slower: a positive feedback loop whose gain
# is the number of benches in the building, arriving at the moment the lab can
# least afford it.
#
# So it waits, doubling exactly as the binding read above does — and it honours
# LabCore's own `retry_after` when the refusal carries one, which is notes.md's
# standing rule for any bulk write. Backing off is not giving up: the tables
# genuinely may not exist, and a bench is back within seconds of LabCore
# answering without adding load while it is not.
DECLARE_RETRY_SECONDS = 5.0
DECLARE_RETRY_MAX_SECONDS = 60.0


# ── the live road ───────────────────────────────────────────────────────────
#
# Everything the lab RECORDS still goes to LabCore. This second road carries
# only what this module alone can know — I am running, my status is now X, I
# just parsed L-1234 — straight to the floor's web server on the LAN, so a dot
# does not wait behind the write queue and a 12-second snapshot.
#
# It is best-effort by construction. Not configured, unreachable, or refused
# means the floor falls back to the record, which is exactly how it behaved
# before this existed. Nothing here may raise: this runs on the worker, and
# LabStation's `_run_in_thread` drops the callback on an exception, which
# strands `_polling` and stops the bench polling at all.
LIVE_URL_KEY = "live_url"
LIVE_TOKEN_KEY = "live_token"
LIVE_TIMEOUT = 1.5
LIVE_PATH = "/api/live"
# Consecutive failed pushes before the address and token are read again. Low
# enough that a moved server heals on its own; high enough that an unreachable
# floor does not turn into a LabCore read on every poll of every bench.
LIVE_RETRY_AFTER = 3


def build_live_config_query() -> tuple:
    """Where the floor listens and with what token — published by the server at
    boot, so a bench that moves to another PC needs nothing typed on it."""
    return ("SELECT key, value FROM lem_meta WHERE key IN (?, ?)",
            [LIVE_URL_KEY, LIVE_TOKEN_KEY])


def parse_live_config(rows) -> tuple:
    """`lem_meta` rows → (url, token). Missing or malformed reads as no channel,
    which simply means no push is attempted."""
    found = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip()
        if key:
            found[key] = str(row.get("value") or "").strip()
    return (found.get(LIVE_URL_KEY, "").rstrip("/"),
            found.get(LIVE_TOKEN_KEY, ""))


# ── the note the floor sends back ───────────────────────────────────────────
#
# The push was one-way and the answer was thrown away. It is now the cheapest
# channel this module has for the opposite direction.
#
# LabCore serialises reads AND writes through one queue at about 1.5 ops/sec and
# is falling over under the load. Two of the questions an idle bench asks it are
# pure polling — the correction factors and the floor's manual override — and
# the answer is "nothing changed" for weeks at a time. The bench already POSTs
# `/api/live` on every poll, and that handler on the floor's web server never
# touches LabCore. So the floor answers the push with a note saying what moved,
# and the bench reads LabCore when it is TOLD to, plus a long backstop.
#
# The wire contract, fixed — the server is built against exactly this:
#
#     POST /api/live      (request body unchanged)
#       200 + {"stale": ["corrections", "override"]}   # any subset, any order
#       200 + {"stale": []}                            # nothing pending
#       auth/validation failures unchanged (401 / 400)
#
# A missing, empty, non-JSON or unexpected-shape body is "no notes" and never an
# error. The server is another program on another schedule; this module is the
# half that must not fall over when it answers with something new, something
# old, or a proxy's login page.
LIVE_NOTE_CORRECTIONS = "corrections"
LIVE_NOTE_OVERRIDE = "override"
LIVE_NOTE_KINDS = frozenset((LIVE_NOTE_CORRECTIONS, LIVE_NOTE_OVERRIDE))
LIVE_NOTE_KEY = "stale"
# What a list of kinds may arrive as. json.loads only ever builds a list, but
# the fakes and the in-process callers hand tuples and sets, and refusing those
# would make the check stricter than the parser it has to agree with.
LIVE_NOTE_LIST_TYPES = (list, tuple, set, frozenset)


def speaks_live_notes(body) -> bool:
    """Does this answer come from a floor that speaks the note protocol?

    ONE shape question, asked in ONE place, because two callers need the same
    answer for different reasons and a disagreement between them is a silent
    safety failure rather than a bug anybody would see.

    `parse_live_notes` asks it to decide what is stale. `_live_channel_healthy`
    asks it to decide whether the refresh windows may apply at all — and that is
    the load-bearing use. A push that LANDED is not evidence of a note channel:
    the benches are separate PCs and the floor's web server cannot be upgraded
    in the same instant as every module in the building, so there is always a
    window where an OLDER floor answers `/api/live` with 204 and no body. That
    reaches `_push_live` as `{}` — a success, correctly, because the push did
    land — and it carries no note and never will. Health taken from the 2xx
    alone therefore put the manual override, the lever that takes a bench OFF
    LINE, behind a fifteen-minute window on a floor that has never heard of
    notes. Reproduced: `_override_due` False at 60s, 300s and 899s. The same
    goes for a rolled-back server and for any proxy that swallows the body.

    So health is EARNED by evidence of the protocol, never by acceptance:
    a dict carrying a `stale` list. `{"stale": []}` IS the protocol — the floor
    saying "nothing pending" — and counts; a bare `{}` does not.

    Total and never raises: it sits on the worker's road with everything else
    here (see `post_live`).
    """
    return (isinstance(body, dict)
            and isinstance(body.get(LIVE_NOTE_KEY), LIVE_NOTE_LIST_TYPES))


def parse_live_notes(body) -> set:
    """The floor's answer → the set of kinds it says are stale.

    Total, by design: anything that is not a recognised kind inside a list under
    "stale" is simply absent from the result. Two failure modes it exists to
    close. An unknown kind — the server learning a third one before this module
    does — must be ignored rather than raise or invalidate something at random.
    And the whole call sits on the worker's road, where a raise strands the poll
    (see `post_live`), so there is nothing here that can throw.

    The shape test is `speaks_live_notes` rather than a copy of it, because
    `_live_channel_healthy` asks the same question to decide whether the refresh
    windows may apply. Two copies that drifted apart would let a floor be
    "healthy" while every note it sends parses to nothing — which is the whole
    of the defect that seam exists to close.
    """
    if not speaks_live_notes(body):
        return set()
    stale = body[LIVE_NOTE_KEY]
    # Stripped on the way OUT, not just on the way in. Returning the raw string
    # would hand back " override ", which no `in` test downstream matches — the
    # note would be silently dropped by the very code that recognised it.
    return {kind.strip() for kind in stale
            if isinstance(kind, str) and kind.strip() in LIVE_NOTE_KINDS}


def _live_response_body(response) -> dict:
    """The note out of an http response, or {} for anything that is not one.

    {} and not None: this is only ever reached on a response the floor ACCEPTED,
    and a push that landed with an unreadable body is still a push that landed.
    Conflating the two is what would walk `_live_failures` up on a healthy
    floor — see `post_live`.
    """
    try:
        reader = getattr(response, "read", None)
        raw = reader() if callable(reader) else b""
        if not raw:
            return {}
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def post_live(url: str, token: str, payload: dict,
              timeout: float = LIVE_TIMEOUT) -> Optional[dict]:
    """POST one push and hand back what the floor said.

    None means the push did not land — no address, refused, timed out, 4xx,
    anything. A DICT, possibly empty, means it did; its content is the note (see
    `parse_live_notes`).

    That split is the whole signature, and the reason it is not a bool any more.
    A success with an empty body is `{}`, which is FALSY — so a caller testing
    truthiness would count a perfectly healthy push as a failure, walk
    `_live_failures` up to LIVE_RETRY_AFTER and re-read the live config out of
    the congested LabCore queue on every fourth poll of every bench, which is the
    exact load pattern this whole road exists to avoid. Callers test
    `is not None`.

    stdlib only — no pip dependency is available inside LabStation — and it
    swallows everything. A raise here would travel up the worker, and
    LabStation's `_run_in_thread` drops the callback on an exception, stranding
    `_polling` so the bench stops polling altogether. Losing a status update is
    a far smaller problem than losing the poll.
    """
    if not str(url or "").strip():
        return None
    try:
        request = urllib.request.Request(
            str(url).rstrip("/") + LIVE_PATH,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "X-LEM-Token": str(token or "")},
            method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if not 200 <= int(getattr(response, "status", 200) or 200) < 300:
                return None
            return _live_response_body(response)
    except Exception:
        return None


def build_live_payload(machine: Machine, evaluation: "MachineEvaluation",
                       now: datetime, interval_seconds: int,
                       rows: List[dict]) -> dict:
    """What this bench says about itself after a poll.

    `interval_seconds` is not decoration: the server sizes this machine's TTL
    from it, and without it a bench on the 5-minute interval would read live for
    90s and from-record for the rest, flapping every cycle.

    The parse fields are omitted rather than blanked when nothing was parsed —
    an absent key is "no run to blip", which is different from a run with no
    Lab ID.
    """
    payload = {"machine_uid": machine.uid,
               "status": evaluation.status,
               "reason": evaluation.reason or "",
               "at": now.isoformat(),
               "interval_seconds": int(interval_seconds or 0)}
    newest = None
    for row in rows or []:
        if not str(row.get(LAB_ID_KEY) or "").strip():
            continue
        when = _row_time(row, now)
        if newest is None or when >= newest[0]:
            newest = (when, row)
    if newest is not None:
        when, row = newest
        payload["last_parse_at"] = when.isoformat()
        payload["lab_id"] = str(row.get(LAB_ID_KEY)).strip()
    return payload


def build_live_probe(machine: Machine) -> dict:
    """The knock at boot: the least the floor needs in order to answer.

    A push whose only purpose is to find out whether there is a floor there
    that speaks the note protocol — asked BEFORE the first poll has read or
    parsed anything, which is the whole reason it exists (see
    `_probe_live_channel`). `/api/live` requires a `machine_uid` and nothing
    else, so that is what this carries.

    It deliberately carries NO `status`. This module has not evaluated anything
    yet, so any status here would be invented — and the floor's `merge_machines`
    treats a live entry with a status as authoritative over the LabCore record,
    colour and reason and all. An entry with an EMPTY status falls straight
    back to the record, which is precisely the right answer for a bench that
    has not looked at its instrument yet. A knock must not repaint the floor.

    No `at` either, for a smaller reason with the same shape: `LivePresence.record`
    refuses a push stamped earlier than the one it already holds, and a knock is
    not a report worth letting win — or lose — that comparison.
    """
    return {"machine_uid": machine.uid}


def build_heartbeat_upsert(machine: Machine, now: datetime,
                           polling: bool = True) -> tuple:
    """"I am running, and here is what I am watching." Cheap, bounded, and
    the only way the master view can tell a dead module from a quiet bench.

    `polling=False` means the module is loaded but not watching — still alive,
    which is why the pulse must keep going when the watch is stopped.
    """
    if machine.source_type == "serial":
        watching = f"serial {machine.com_port or '(no port)'} @{machine.baud_rate}"
    elif machine.source_type == "multi_csv":
        watching = f"multi_csv {machine.csv_path or '(no folder)'}"
    elif machine.source_type == "manual":
        # Not a source at all — without this it fell through to the single_csv
        # arm and told the floor "single_csv (no file)", which reads as broken.
        watching = "manual entry (no parsing)"
    else:
        watching = f"single_csv {machine.csv_path or '(no file)'}"
    if not polling:
        watching = f"idle (not watching) — {watching}"
    sql = ("INSERT INTO lem_machine_heartbeat (machine_uid, last_poll, watching) "
           "VALUES (?, ?, ?) ON CONFLICT(machine_uid) DO UPDATE SET "
           "last_poll=excluded.last_poll, watching=excluded.watching")
    return sql, [machine.uid, now.isoformat(), watching]


# ── the bench reads its configuration from the FLOOR ────────────────────────
#
# LabCore serialises reads AND writes through ONE queue at about 1.5 ops/sec.
# Its database sits on an SMB share and CANNOT be moved to local disk, so every
# `read_sql` will always consume a write-queue slot — a bench asking a question
# delays every other bench's results. That is settled and is not what this
# changes.
#
# What this changes is WHO is asked. Each bench read its own configuration out
# of LabCore, so LabCore's load grew with the number of benches — which is what
# is crashing it. The floor's web server sits beside LabCore and already holds
# every one of these tables in an in-memory snapshot it refreshes every 12
# seconds at a cost that does NOT grow with the bench count. So the bench asks
# the floor, and touches LabCore only when the floor cannot answer.
#
# The wire contract is fixed — the server is built against exactly this:
#
#     GET {live_url}/api/bench/{machine_uid}/config
#       Header: X-LEM-Token: {live_token}
#       200 {"machine_uid", "snapshot_age_seconds", "override",
#            "corrections", "qc_samples", "qc_targets", "qc_specs",
#            "maintenance"}
#       401 bad token
#       503 {"error": ..., "stale": true}   snapshot never populated
#
# The row shapes are EXACTLY what `read_sql` returns for those tables, which is
# the point: `floor_config_results` hands them back as LabCore-shaped result
# dicts and every existing parser runs unchanged. A second parser for the same
# rows would be a second definition of what a bench believes about itself, and
# the two would disagree somewhere nobody ever looks.
#
# This road changes the SOURCE of a read, never its SCHEDULE. `_config_due`,
# `_corrections_due` and `_override_due` still decide when to ask, and the note
# channel still says when something is stale.
FLOOR_CONFIG_PATH = "/api/bench/{uid}/config"
# In the spirit of LIVE_TIMEOUT. This is on the poll's critical path and the
# floor is one hop away on the LAN, so a long timeout would freeze the poll
# behind a server that is merely rebooting — and the fallback that follows is
# cheap and always correct.
FLOOR_CONFIG_TIMEOUT = 1.5

# How old the floor's snapshot may be before the bench refuses it and asks
# LabCore instead.
#
# THIS IS A COMPLIANCE GATE, not a tuning knob. The correction factors served
# here are added to EVERY measurement before it is written to LabCore,
# displayed or QC-judged (ISO/IEC 17025:2017 s7.8.2 — the reported result must
# be the measurement result). Serving them from the floor is defensible
# *because* the snapshot is strictly FRESHER than the window it replaces — 12s
# against a 900s backstop — and because LabCore remains the origin of every
# row. That argument holds only while the age is BOUNDED: an unbounded stale
# snapshot means a bench applying a calibration offset the lab has already
# superseded, reported as the measurement result, which is the finding this
# module exists to avoid. So the check lives inside `floor_config_results` —
# the single door every floor row comes through — rather than at any call
# site, where a future caller could simply not write it.
#
# 60 seconds. The floor refreshes every 12s, so this tolerates four missed
# cycles (a long LabCore refresh, a GC pause, a skipped tick) without
# stampeding every bench in the building back onto the queue this road exists
# to unload. And it stays at or under CONFIG_REFRESH_SECONDS, so the floor can
# never serve configuration older than the LabCore read it is replacing.
FLOOR_CONFIG_MAX_AGE_SECONDS = 60.0

# Every key the answer must carry, and what each one must be. A body missing
# any of them is refused WHOLE rather than used in part: a missing `qc_specs`
# read as an empty list is "every QC assignment was deleted", which leaves the
# bench looking configured and monitoring nothing.
_FLOOR_ROW_KEYS = ("corrections", "qc_samples", "qc_targets", "qc_specs",
                   "maintenance")


def build_floor_config_url(url: str, machine_uid: str) -> str:
    """Where this bench's configuration lives on the floor.

    The uid is quoted with `safe=""`. It is operator-typed and lands in a URL
    PATH, so a space or a slash in it would otherwise build a request for some
    other path entirely — and the worst version of that is a bench applying
    another instrument's correction factors to its own measurements.
    """
    return (str(url).rstrip("/")
            + FLOOR_CONFIG_PATH.format(
                uid=urllib.parse.quote(str(machine_uid or ""), safe="")))


def fetch_floor_config(url: str, token: str, machine_uid: str,
                       timeout: float = FLOOR_CONFIG_TIMEOUT
                       ) -> Optional[dict]:
    """GET one bench's configuration from the floor. None means no answer.

    None for everything that is not a 2xx carrying a JSON OBJECT: no address, a
    refused connection, a timeout, a 401, a 503 with a cold snapshot, a proxy's
    login page, a JSON array, an empty body. There is nothing to configure from
    in any of those, and the caller's answer to all of them is the same — ask
    LabCore.

    Note the deliberate difference from `post_live`, where an empty body is
    `{}` and means a push that LANDED. A push that landed is the whole point
    of a push; an answer with no configuration in it is not an answer.

    stdlib only — no pip dependency is available inside LabStation — and it
    swallows EVERYTHING. A raise here travels up the worker, and LabStation's
    `_run_in_thread` drops the callback on an exception, which strands
    `_polling` so the bench stops polling altogether. Falling back to a LabCore
    read costs one queue slot; losing the poll costs the bench.
    """
    if not str(url or "").strip():
        return None
    try:
        request = urllib.request.Request(
            build_floor_config_url(url, machine_uid),
            headers={"Accept": "application/json",
                     "X-LEM-Token": str(token or "")},
            method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if not 200 <= int(getattr(response, "status", 200) or 200) < 300:
                return None
            reader = getattr(response, "read", None)
            raw = reader() if callable(reader) else b""
            if not raw:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            parsed = json.loads(raw)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _floor_age(value) -> Optional[float]:
    """The snapshot age as a number, or None for anything that is not one.

    `bool` is excluded on purpose: `True` is an int in Python and would sail
    through as an age of 1.0 second, which is the most dangerous possible way
    for this gate to be wrong.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    age = float(value)
    if age != age:                      # NaN compares False against everything
        return None
    return age


def floor_config_results(body, machine_uid: str) -> Optional[dict]:
    """The floor's answer -> LabCore-shaped result dicts, or None to fall back.

    THE single door. Every row that reaches the bench from the floor comes
    through here, which is what makes the age bound impossible to skip (see
    FLOOR_CONFIG_MAX_AGE_SECONDS) and what keeps the two roads from drifting:
    the returned dicts are exactly what `read_sql` hands back for those tables,
    so `parse_correction_rows`, `parse_qc_sample_rows`, `parse_qc_specs`,
    `parse_maint_rows` and `extract_overrides` all run unchanged on them.

    Refused WHOLE, never in part, and refusal is always safe — it means the
    bench asks LabCore, which is exactly what it did before this road existed.

    Total and never raises: this runs on the worker with everything else here
    (see `fetch_floor_config`).
    """
    if not isinstance(body, dict):
        return None

    # WHOSE configuration is this? A caching proxy, or a floor that resolved
    # the uid differently, would otherwise hand this bench another instrument's
    # correction factors — and they are added to every measurement it reports.
    # Nothing else in the answer identifies the machine, so a missing uid is a
    # refusal too.
    if str(body.get("machine_uid") or "").strip() != str(machine_uid).strip():
        return None

    # The compliance gate. Missing, non-numeric, NaN and negative all refuse:
    # "no age" is not "fresh", it is a floor that cannot tell us, and a
    # snapshot from the future is two clocks disagreeing rather than a fresh
    # one — the
    # same argument `_corrections_due` makes about negative elapsed time.
    age = _floor_age(body.get("snapshot_age_seconds"))
    if age is None or age < 0 or age > FLOOR_CONFIG_MAX_AGE_SECONDS:
        return None

    for key in _FLOOR_ROW_KEYS:
        if not isinstance(body.get(key), list):
            return None
    # The override rides the wire as a bare string, not as rows — but it is
    # rebuilt into the `lem_machine_control` row shape below so that
    # `extract_overrides` still does the validating. An unrecognised value is
    # dropped there, on both roads, by one piece of code.
    override = body.get("override")
    if not isinstance(override, str):
        return None

    return {"corrections": {"rows": list(body["corrections"])},
            "qc_samples": {"rows": list(body["qc_samples"])},
            "targets": {"rows": list(body["qc_targets"])},
            "qc_specs": {"rows": list(body["qc_specs"])},
            "maint": {"rows": list(body["maintenance"])},
            "override": {"rows": [{"machine_uid": str(machine_uid),
                                   "manual_override": override}]}}



# ── what this module is ACTUALLY checking ──────────────────────────────────
# The two QC tables LabCore already had are both *inputs*: `lem_qc_specs` is a
# human's per-machine override, `lem_machine_targets` is what was assigned from
# the floor. Neither says what the module ended up applying — and most QC here is
# resolved at runtime from `lem_qc_samples`, matched by the standard's Lab ID, so
# neither input has a row for it at all.
#
# Live proof of the gap (2026-08-03): `lem_qc_specs` held 0 rows and
# `lem_machine_targets` 2, while PAC Flash 1 and 2 were both checking Flash Point
# against expected 63.72. The floor showed "No QC assigned" for an instrument it
# was actively judging, and had no limits to draw a band with.
#
# So this is the output: the effective spec, with the band, published whenever it
# changes.
EFFECTIVE_SPECS_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_machine_specs ("
    "machine_uid TEXT NOT NULL, test_name TEXT NOT NULL, sample_id TEXT, "
    "expected REAL, std_dev REAL, k REAL, units TEXT, low REAL, high REAL, "
    "last_qc_at TEXT, last_qc_value REAL, last_qc_in_spec INTEGER, "
    "correction REAL DEFAULT 0.0, updated_at TEXT, "
    "PRIMARY KEY (machine_uid, test_name))"
)

# `CREATE TABLE IF NOT EXISTS` is a no-op on a table that already exists, so a new
# column needs an ALTER. Harmless to re-run: it errors when the column is already
# there, and every caller ignores that.
EFFECTIVE_SPECS_MIGRATIONS = (
    "ALTER TABLE lem_machine_specs ADD COLUMN correction REAL DEFAULT 0.0",
)


def spec_band(spec: TestSpec) -> tuple:
    """The (low, high) this spec passes within — expected ± k·std_dev.

    The same arithmetic `evaluate_machine` judges with, pulled out so the number
    the floor draws and the number the module decides on cannot drift apart.
    Both readers still come through here, so making it exact moves both together
    and that guarantee is untouched.

    Decimal, for the reason `corrected_value` is: this band is PUBLISHED as well
    as judged with. `low` and `high` go into lem_machine_specs and the floor
    draws min/target/max from them, so a low-sulfur spec was advertising
    low=0.0009000000000000001 — the same binary-representation tail reported on
    the sulfur results (Lab IDs 37712, 37709) on 2026-08-13. `limits_text`
    formats to 2 or 4 places so the card always looked right; the floor's copy
    did not.

    Ryan asked for this after being told what it costs: doing it exactly moves
    the pass/fail boundary by about one unit in the last place. That is far
    below any instrument's resolution, and it moves it TOWARDS the number the
    band is supposed to be — 0.0015 − 2×0.0003 is 0.0009, and a reading of
    0.0009 should pass. It is a real change to a QC decision, on a reading
    sitting exactly on the limit, and it makes that reading behave the way the
    spec on paper says.

    Falls back to float arithmetic if the numbers cannot be represented as
    decimals (a NaN or infinite std_dev from a bad row). This is on the verdict
    path and a raise here strands the poll.
    """
    try:
        expected = Decimal(str(spec.expected).strip())
        margin = Decimal(str(spec.k).strip()) * Decimal(str(spec.std_dev).strip())
        if expected.is_finite() and margin.is_finite():
            return float(expected - margin), float(expected + margin)
    except (TypeError, ValueError, ArithmeticError):
        pass
    margin = float(spec.k) * float(spec.std_dev)
    return float(spec.expected) - margin, float(spec.expected) + margin


# ── correction factors ─────────────────────────────────────────────────────
# `corrected = raw + correction`, per machine per test. Default 0.0, so a machine
# with no row behaves exactly as before.
CORRECTIONS_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_correction_factors ("
    "machine_uid TEXT NOT NULL, test_name TEXT NOT NULL, "
    "correction REAL NOT NULL DEFAULT 0.0, units TEXT, "
    "updated_at TEXT, updated_by TEXT, "
    "PRIMARY KEY (machine_uid, test_name))"
)


def build_corrections_query(machine_uid: str) -> tuple:
    return ("SELECT test_name, correction FROM lem_correction_factors "
            "WHERE machine_uid = ?", [machine_uid])


def parse_correction_rows(rows) -> dict:
    """Rows → {test_name: offset}. Junk is skipped, not raised.

    This runs inside a poll: one malformed row must not strand the worker and
    take the instrument's status with it.
    """
    out: dict = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("test_name") or "").strip()
        if not name:
            continue
        value = _safe_float(row.get("correction"))
        if value is None:
            continue
        out[name] = value
    return out


def apply_corrections(machine: Machine, corrections: dict) -> None:
    """Record the offsets on the machine, and mirror them onto the QC specs.

    The MAP is authoritative: it covers every method the bench reports, including
    the many with no QC assigned, and `apply_row_corrections` reads it. The copy on
    each spec is for display — the card and the floor show a test's band with its
    offset — and is never what does the correcting.

    Absent means 0.0 rather than "leave it alone": deleting a correction has to
    actually stop correcting.
    """
    machine.corrections = {str(k): float(v)
                           for k, v in (corrections or {}).items()
                           if _safe_float(v) is not None}
    for spec in machine.tests or []:
        spec.correction = float(machine.corrections.get(spec.name, 0.0) or 0.0)


def correctable_methods(machine: Machine) -> List[str]:
    """Every method a correction could apply to, sorted.

    The mapped methods — i.e. everything this bench actually reports — plus its QC
    tests, plus anything that already carries a correction even if it is no longer
    mapped (otherwise a stale factor can never be found and removed).

    QC is assignment-only, so most reported methods have no spec at all. Offering
    only the QC tests would mean corrections could be applied to every measurement
    but only *set* on the control, which is the gap this closes.
    """
    names = set()
    for mapping in machine.mappings or []:
        for method in mapping.methods or []:
            if str(method).strip():
                names.add(str(method).strip())
    for spec in machine.tests or []:
        if str(spec.name).strip():
            names.add(str(spec.name).strip())
    for name in (machine.corrections or {}):
        if str(name).strip():
            names.add(str(name).strip())
    return sorted(names)


def fetch_corrections(machine_uid: str, read_sql) -> tuple:
    """Ask LabCore for one machine's correction factors. Returns
    (answered, factors); `factors` means nothing unless `answered`.

    Split out of `read_corrections` because the caller has to be able to THROW
    THE ANSWER AWAY. This read runs on the worker and blocks on a congested
    queue, and the bench can change underneath it — see `_corrections_epoch`.
    A function that reads and applies in one step gives the caller no moment in
    between to decide the answer is about something that is no longer there.

    Every "no" is reported as not answered, so the caller retries rather than
    caching a refusal for a whole refresh window: no reader injected yet (a
    LabStation canvas restores its modules before the helpers land), a raise, an
    error dict from a full queue — and a result with no `rows` key at all.

    That last one is the one that looks like an answer. `res.get("rows") or []`
    turned a bare `{"ok": True}` into the empty list, which means "every
    correction was deleted" — so an acknowledgement carrying no rows WIPED the
    map and the window then cached the wipe. An empty LIST is still a real
    answer and still clears them; deleting a correction has to actually stop it
    correcting.
    """
    if not callable(read_sql):
        return False, {}
    # The parse is inside the try with the read, not after it. This is called on
    # the WORKER, where a raise strands `_polling` and the bench stops polling
    # altogether — and the rows are whatever a gateway handed back, not a shape
    # this module chose. Nothing about a malformed answer is worth that.
    try:
        res = read_sql(*build_corrections_query(machine_uid))
        if not isinstance(res, dict) or res.get("error"):
            return False, {}
        rows = res.get("rows")
        if rows is None:
            return False, {}
        return True, parse_correction_rows(rows)
    except Exception:
        return False, {}


def read_corrections(machine: Machine, read_sql) -> tuple:
    """Re-read this machine's correction factors. Returns (answered, changed).

    Called at the TOP of a poll, before the print is parsed, because the factor
    applied to a measurement has to be the one in force when it was made. It used to
    be read in the LabCore sync, which runs after the parse — so the first print
    after a change was reported with the previous factor (ISO/IEC 17025 §7.8.2: the
    reported result must be the measurement result).

    An unreadable table keeps what it already had. A busy queue must never silently
    turn corrections off and report raw values — a stale correction is a lesser
    problem than a wrong result.

    `answered` is what the caller's refresh window is stamped from, and it is NOT
    the same question as `changed`: a table that answered with the factors already
    held IS an answer, and a refusal that changed nothing is not one. Reporting
    either for the other is how a bench caches "LabCore was busy" for a whole
    window — the failure `_config_due` describes, arriving on the one read that had
    no window at all until now. See `_corrections_due`.
    """
    answered, wanted = fetch_corrections(machine.uid, read_sql)
    if not answered:
        return False, False
    if wanted == dict(machine.corrections or {}):
        return True, False
    apply_corrections(machine, wanted)
    return True, True


def refresh_corrections(machine: Machine, read_sql) -> bool:
    """`read_corrections`, asked only whether the factors changed."""
    return read_corrections(machine, read_sql)[1]


def build_correction_upsert(machine_uid: str, test_name: str, correction: float,
                            units: str, now: datetime, by: str) -> tuple:
    """Write one correction, recording who set it and when.

    The same table the web server writes, so a bench tech and a supervisor are
    editing one number rather than two that disagree.
    """
    return ("INSERT INTO lem_correction_factors (machine_uid, test_name, "
            "correction, units, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(machine_uid, test_name) DO UPDATE SET "
            "correction=excluded.correction, units=excluded.units, "
            "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
            [machine_uid, test_name, float(correction), units or "",
             now.isoformat(), by or ""])


def build_correction_delete(machine_uid: str, test_name: str) -> tuple:
    return ("DELETE FROM lem_correction_factors "
            "WHERE machine_uid = ? AND test_name = ?", [machine_uid, test_name])


def parse_correction_input(text: str) -> float:
    """What an operator typed → an offset. Blank means none.

    Raises ValueError on anything else rather than falling back to 0.0: silently
    zeroing a mistyped correction would change every verdict on the bench and
    look like nothing happened.
    """
    raw = (text or "")
    # A Unicode minus (U+2212) and the dashes look identical to a hyphen but
    # `float()` refuses them, and a pasted "-3.0" is exactly how PAC Flash 2's real
    # correction would be entered. Normalised, then parsed strictly.
    for bad in ("\u2212", "\u2013", "\u2014"):
        raw = raw.replace(bad, "-")
    for space in ("\u00a0", "\u2007", "\u202f"):
        raw = raw.replace(space, " ")
    raw = raw.strip()
    if not raw:
        return 0.0
    return float(raw)


def qc_log_detail(spec: TestSpec, raw: float, corrected: float,
                  operator: Optional[str] = None,
                  calibration_id: Optional[str] = None) -> dict:
    """What a QC verdict records.

    Carries the raw reading and the offset whenever one was applied — a log that
    holds only the corrected number cannot be audited, and a correction that
    changes a pass into a fail has to be visible in the record that did it.

    `operator` and `calibration_id` are the provenance the uncertainty budget
    needs (PJLA ISO/IEC 17025 assessment, September 2026). The spread of a set
    of QC results is only **within-laboratory reproducibility (u(Rw))** if the
    set spans analysts, shifts and calibrations; one analyst against one
    calibration is **repeatability (s_r)**, a far narrower claim. Value and
    timestamp alone cannot tell those apart after the fact, and an assessor
    separates them by asking who ran them.

    Both are written on EVERY verdict, `None` included, and `None` is the only
    way absence is expressed — see `known_text`. A key present and null says
    this module looked and did not know; a key missing says the row predates
    anything looking. Blank is neither, and would be counted as a person.
    """
    low, high = spec_band(spec)
    detail = {"in_spec": low <= corrected <= high,
              "expected": spec.expected, "low": low, "high": high,
              "operator": known_text(operator),
              "calibration_id": known_text(calibration_id)}
    if spec.correction:
        detail["raw_value"] = raw
        detail["correction"] = float(spec.correction)
    return detail


def _trim_number(value: float) -> str:
    """0.5 -> "0.5", 0.0 -> "0" — no trailing noise in an editable box."""
    return f"{float(value):.4f}".rstrip("0").rstrip(".") or "0"


def limits_text(spec: TestSpec) -> str:
    """"min – max units" for the front view.

    Asked for 2026-08-03: the QC row showed the reading and the test name but
    never what it was judged against, so 65.0 told you nothing on its own.

    Enough decimals to be useful: viscosity standards run to four places
    (2.5983 – 2.6879), and a band printed as "2.60 – 2.69" is not a band anyone
    can check a result against.
    """
    low, high = spec_band(spec)
    places = 2 if abs(high - low) >= 0.2 else 4
    units = (spec.units or "").strip()
    offset = ""
    if spec.correction:
        # Shown wherever the band is shown: an operator reading 65.5 needs to
        # know whether the bench measured 65.5 or 65.0 plus a correction.
        offset = f"  ({float(spec.correction):+.2f})"
    if low == high:
        # Zero width is a target, not a range; "63.72 – 63.72" reads as a bug.
        # Trailing zeros trimmed so 63.72 stays 63.72 while 2.6431 keeps its
        # four places — the fixed width that suits a band suits neither here.
        body = f"{low:.4f}".rstrip("0").rstrip(".")
    else:
        body = f"{low:.{places}f} \u2013 {high:.{places}f}"
    return f"{body} {units}".rstrip() + offset


def effective_specs_fingerprint(machine: Machine) -> tuple:
    """What would be published — so an unchanged sync writes nothing.

    Includes the last reading, because the floor shows the value against the band
    and a new reading has to reach it. Excludes the timestamp of the publish
    itself, or every poll would look like a change.
    """
    return tuple(
        (s.name, s.sample_id, float(s.expected), float(s.std_dev), float(s.k),
         s.units, s.last_qc_at, s.last_qc_value, s.last_qc_in_spec,
         float(s.correction or 0.0))
        for s in sorted(machine.tests or [], key=lambda x: x.name))


def build_effective_specs_publish(machine: Machine, now: datetime) -> list:
    """[(sql, args)] publishing this machine's effective specs.

    Deleted-then-inserted, in as few ops as possible: the write queue serialises
    at roughly 1.5 ops/sec, so a twelve-test standard must not be twelve writes.
    The DELETE always goes out — dropping the last assignment has to be visible,
    and an upsert alone would leave a test the module no longer checks on screen.
    """
    ops = [("DELETE FROM lem_machine_specs WHERE machine_uid = ?", [machine.uid])]
    specs = list(machine.tests or [])
    if not specs:
        return ops
    args: list = []
    for spec in specs:
        low, high = spec_band(spec)
        args.extend([
            machine.uid, spec.name, spec.sample_id, float(spec.expected),
            float(spec.std_dev), float(spec.k), spec.units, low, high,
            spec.last_qc_at or "", spec.last_qc_value,
            (None if spec.last_qc_in_spec is None else int(spec.last_qc_in_spec)),
            float(spec.correction or 0.0), now.isoformat(),
        ])
    values = ", ".join(["(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"] * len(specs))
    ops.append((
        "INSERT INTO lem_machine_specs (machine_uid, test_name, sample_id, "
        "expected, std_dev, k, units, low, high, last_qc_at, last_qc_value, "
        f"last_qc_in_spec, correction, updated_at) VALUES {values}", args))
    return ops


SUBSTATUS_TABLE_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_machine_substatus ("
    "machine_uid TEXT PRIMARY KEY, qc TEXT, pm TEXT, calibration TEXT, "
    "updated_at TEXT)"
)


def build_substatus_upsert(machine: Machine, evaluation: MachineEvaluation,
                           now: datetime) -> tuple:
    """Publish QC / PM / CAL separately so the master view can show the
    three pills the old LEM showed, instead of one blended colour."""
    sub = evaluation.sub_statuses or {}
    sql = ("INSERT INTO lem_machine_substatus "
           "(machine_uid, qc, pm, calibration, updated_at) "
           "VALUES (?, ?, ?, ?, ?) "
           "ON CONFLICT(machine_uid) DO UPDATE SET qc=excluded.qc, "
           "pm=excluded.pm, calibration=excluded.calibration, "
           "updated_at=excluded.updated_at")
    args = [machine.uid, sub.get("qc", STATUS_UNKNOWN),
            sub.get("pm", STATUS_UNKNOWN),
            sub.get("calibration", STATUS_UNKNOWN), now.isoformat()]
    return sql, args


def build_status_upsert(machine: Machine, evaluation: MachineEvaluation,
                        now: datetime) -> tuple:
    sql = (
        "INSERT INTO lem_machine_status "
        "(machine_uid, title, status, reason, updated_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(machine_uid) DO UPDATE SET "
        "title=excluded.title, status=excluded.status, "
        "reason=excluded.reason, updated_at=excluded.updated_at"
    )
    args = [machine.uid, machine.title, evaluation.status,
            evaluation.reason, now.isoformat()]
    return sql, args


_VALID_OVERRIDES = ("", STATUS_SERVICE, STATUS_DEAD)


def extract_overrides(rows: List[dict]) -> dict:
    """Map machine_uid -> manual_override from lem_machine_control rows,
    dropping rows with no uid or an unrecognized override value."""
    overrides = {}
    for row in rows:
        uid = str(row.get("machine_uid") or "").strip()
        value = str(row.get("manual_override") or "").strip()
        if uid and value in _VALID_OVERRIDES:
            overrides[uid] = value
    return overrides


# ── Serial backends ──────────────────────────────────────────────────────────
#
# QtSerialPort is preferred but is an add-on module LabStation's bundled
# PySide6 may not ship. The fallback is dependency-free: Win32 comm API via
# ctypes on Windows, termios on POSIX — both feed the same FrameAssembler.

_WIN_PARITY = {"N": 0, "O": 1, "E": 2, "M": 3, "S": 4}
_WIN_STOPBITS = {1.0: 0, 1.5: 1, 2.0: 2}


def _qt_serial_available() -> bool:
    try:
        from PySide6 import QtSerialPort  # noqa: F401
        return True
    except ImportError:
        return False


def _win_serial_settings(machine) -> tuple:
    """(baud, byte_size, parity_code, stopbits_code) for a Win32 DCB."""
    parity_key = (machine.parity.strip().upper()[:1]
                  if machine.parity else "N")
    return (
        int(machine.baud_rate),
        min(8, max(5, int(machine.byte_size))),
        _WIN_PARITY.get(parity_key, 0),
        _WIN_STOPBITS.get(float(machine.stop_bits), 0),
    )


class _FrameSink:
    """Where a reader puts a COMPLETED frame: through `on_frame` first — the
    module's journal, which fsyncs it and hands back the frame tagged with its
    replay key — and only then onto the deque the poll drains.

    That order is the serial half of the custody argument (transfer v4 §3.3).
    A serial frame has no other copy: the instrument does not print twice, so
    a frame that sat in memory waiting for the next poll was lost to any kill
    in between (K8: 3 of 3). Journaled here, on the reader's own thread, the
    residual is the one frame whose fsync a kill interrupts (K8r: exactly 1).

    A journal that fails never costs the frame: it still goes on the deque,
    untagged, and the poll journals it with the rest of its readings."""

    _on_frame = None

    def _complete(self, frames: List[str]) -> None:
        for frame in frames:
            if self._on_frame is not None and frame.strip():
                try:
                    frame = self._on_frame(frame)
                except Exception:
                    pass
            self._frames.append(frame)


class _RawSerialReader(_FrameSink):
    """Dependency-free serial reader on a daemon thread.

    Windows: CreateFile + SetCommState/SetCommTimeouts + ReadFile (ctypes).
    POSIX:   os.open + termios raw mode + select.
    Completed frames are journaled (see `_FrameSink`) and accumulate in a
    thread-safe deque; the poll drains them via take_frames(). Errors land in
    self.error.

    `port` replaces the OS backend with any object that has `read() -> bytes`
    (returning b"" when nothing arrived) and `close()`: the gate and the tests
    drive the reader's real frame handling through it."""

    def __init__(self, machine, on_frame=None, port=None) -> None:
        import threading
        self._machine = machine
        self._assembler = FrameAssembler(idle_gap=machine.idle_gap)
        self._frames: deque = deque()
        self._on_frame = on_frame
        self._stop = None
        self.error: Optional[str] = None
        self._stop = threading.Event()
        self._port = port
        if port is not None:
            self._handle = None
            self._fd = None
        elif os.name == "nt":
            self._handle = self._open_windows(machine)
            self._fd = None
        else:
            self._fd = self._open_posix(machine)
            self._handle = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def take_frames(self) -> List[str]:
        frames = []
        while self._frames:
            frames.append(self._frames.popleft())
        return frames

    def close(self) -> None:
        self._stop.set()

    # ── Windows backend ───────────────────────────────────────────────────

    @staticmethod
    def _win_api():
        import ctypes
        from ctypes import wintypes

        class DCB(ctypes.Structure):
            _fields_ = [
                ("DCBlength", wintypes.DWORD),
                ("BaudRate", wintypes.DWORD),
                ("fFlags", wintypes.DWORD),
                ("wReserved", wintypes.WORD),
                ("XonLim", wintypes.WORD),
                ("XoffLim", wintypes.WORD),
                ("ByteSize", ctypes.c_ubyte),
                ("Parity", ctypes.c_ubyte),
                ("StopBits", ctypes.c_ubyte),
                ("XonChar", ctypes.c_char),
                ("XoffChar", ctypes.c_char),
                ("ErrorChar", ctypes.c_char),
                ("EofChar", ctypes.c_char),
                ("EvtChar", ctypes.c_char),
                ("wReserved1", wintypes.WORD),
            ]

        class COMMTIMEOUTS(ctypes.Structure):
            _fields_ = [
                ("ReadIntervalTimeout", wintypes.DWORD),
                ("ReadTotalTimeoutMultiplier", wintypes.DWORD),
                ("ReadTotalTimeoutConstant", wintypes.DWORD),
                ("WriteTotalTimeoutMultiplier", wintypes.DWORD),
                ("WriteTotalTimeoutConstant", wintypes.DWORD),
            ]

        return ctypes, wintypes, DCB, COMMTIMEOUTS

    def _open_windows(self, machine):
        ctypes, wintypes, DCB, COMMTIMEOUTS = self._win_api()
        kernel32 = ctypes.windll.kernel32
        GENERIC_READ = 0x80000000
        OPEN_EXISTING = 3
        handle = kernel32.CreateFileW(
            f"\\\\.\\{machine.com_port}", GENERIC_READ, 0, None,
            OPEN_EXISTING, 0, None)
        if handle == ctypes.c_void_p(-1).value or handle == -1:
            raise OSError(f"CreateFile failed "
                          f"(WinError {kernel32.GetLastError()})")
        try:
            dcb = DCB()
            dcb.DCBlength = ctypes.sizeof(DCB)
            if not kernel32.GetCommState(handle, ctypes.byref(dcb)):
                raise OSError("GetCommState failed — not a serial port?")
            baud, size, parity, stop = _win_serial_settings(machine)
            dcb.BaudRate = baud
            dcb.ByteSize = size
            dcb.Parity = parity
            dcb.StopBits = stop
            # fBinary on, DTR + RTS enabled (pyserial-style defaults).
            dcb.fFlags |= 0x1 | 0x10 | 0x1000
            if not kernel32.SetCommState(handle, ctypes.byref(dcb)):
                raise OSError(f"SetCommState failed "
                              f"(WinError {kernel32.GetLastError()})")
            timeouts = COMMTIMEOUTS(50, 0, 200, 0, 0)
            kernel32.SetCommTimeouts(handle, ctypes.byref(timeouts))
        except OSError:
            kernel32.CloseHandle(handle)
            raise
        return handle

    def _read_windows(self) -> bytes:
        ctypes, wintypes, _, _ = self._win_api()
        kernel32 = ctypes.windll.kernel32
        buffer = ctypes.create_string_buffer(4096)
        read = wintypes.DWORD(0)
        if not kernel32.ReadFile(self._handle, buffer, 4096,
                                 ctypes.byref(read), None):
            raise OSError(f"ReadFile failed "
                          f"(WinError {kernel32.GetLastError()})")
        return buffer.raw[:read.value]

    # ── POSIX backend ─────────────────────────────────────────────────────

    def _open_posix(self, machine):
        import termios
        path = (machine.com_port if machine.com_port.startswith("/")
                else f"/dev/{machine.com_port}")
        fd = os.open(path, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            attrs = termios.tcgetattr(fd)
            baud = getattr(termios, f"B{int(machine.baud_rate)}",
                           termios.B9600)
            attrs[0] = 0  # iflag
            attrs[1] = 0  # oflag
            attrs[3] = 0  # lflag — raw
            cflag = termios.CREAD | termios.CLOCAL
            size_map = {5: termios.CS5, 6: termios.CS6,
                        7: termios.CS7, 8: termios.CS8}
            cflag |= size_map.get(int(machine.byte_size), termios.CS8)
            parity = (machine.parity or "N").strip().upper()[:1]
            if parity in ("E", "O"):
                cflag |= termios.PARENB
                if parity == "O":
                    cflag |= termios.PARODD
            if float(machine.stop_bits) == 2.0:
                cflag |= termios.CSTOPB
            attrs[2] = cflag
            attrs[4] = baud
            attrs[5] = baud
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except Exception:
            os.close(fd)
            raise
        return fd

    def _read_posix(self) -> bytes:
        import select
        readable, _, _ = select.select([self._fd], [], [], 0.2)
        if not readable:
            return b""
        try:
            return os.read(self._fd, 4096)
        except BlockingIOError:
            return b""

    # ── Shared read loop ──────────────────────────────────────────────────

    def _on_bytes(self, data: bytes, now: float) -> None:
        """Bytes arrived at `now`: completes the frame an idle gap ended."""
        self._complete(self._assembler.feed(data, now))

    def _on_idle(self, now: float) -> None:
        """Nothing arrived: a frame silent for the idle gap is complete."""
        if self._assembler.idle_since(now):
            self._complete(self._assembler.flush())

    def _run(self) -> None:
        import time
        try:
            while not self._stop.is_set():
                if self._port is not None:
                    data = self._port.read()
                elif self._handle is not None:
                    data = self._read_windows()
                else:
                    data = self._read_posix()
                now = time.monotonic()
                if data:
                    self._on_bytes(data, now)
                else:
                    self._on_idle(now)
        except Exception as exc:
            self.error = f"Serial read error: {exc}"
        finally:
            try:
                if self._port is not None:
                    self._port.close()
                elif self._handle is not None:
                    ctypes, _, _, _ = self._win_api()
                    ctypes.windll.kernel32.CloseHandle(self._handle)
                elif self._fd is not None:
                    os.close(self._fd)
            except Exception:
                pass


class _QtSerialReader(_FrameSink):
    """QtSerialPort-backed reader (preferred when the add-on is present).
    Same take_frames()/close()/error interface as _RawSerialReader, and the
    same journaling of each completed frame (see `_FrameSink`)."""

    def __init__(self, machine, on_frame=None) -> None:
        from PySide6 import QtSerialPort
        self.error: Optional[str] = None
        self._assembler = FrameAssembler(idle_gap=machine.idle_gap)
        self._frames: deque = deque()
        self._on_frame = on_frame
        port = QtSerialPort.QSerialPort(machine.com_port)
        port.setBaudRate(int(machine.baud_rate))
        parity_map = {
            "N": QtSerialPort.QSerialPort.Parity.NoParity,
            "E": QtSerialPort.QSerialPort.Parity.EvenParity,
            "O": QtSerialPort.QSerialPort.Parity.OddParity,
            "M": QtSerialPort.QSerialPort.Parity.MarkParity,
            "S": QtSerialPort.QSerialPort.Parity.SpaceParity,
        }
        port.setParity(parity_map.get(
            (machine.parity or "N").strip().upper()[:1],
            QtSerialPort.QSerialPort.Parity.NoParity))
        stop_map = {
            1.0: QtSerialPort.QSerialPort.StopBits.OneStop,
            1.5: QtSerialPort.QSerialPort.StopBits.OneAndHalfStop,
            2.0: QtSerialPort.QSerialPort.StopBits.TwoStop,
        }
        port.setStopBits(stop_map.get(
            float(machine.stop_bits),
            QtSerialPort.QSerialPort.StopBits.OneStop))
        data_map = {
            5: QtSerialPort.QSerialPort.DataBits.Data5,
            6: QtSerialPort.QSerialPort.DataBits.Data6,
            7: QtSerialPort.QSerialPort.DataBits.Data7,
            8: QtSerialPort.QSerialPort.DataBits.Data8,
        }
        port.setDataBits(data_map.get(
            int(machine.byte_size),
            QtSerialPort.QSerialPort.DataBits.Data8))
        from PySide6 import QtCore as _QtCore
        if not port.open(_QtCore.QIODevice.OpenModeFlag.ReadOnly):
            raise OSError(port.errorString())
        port.readyRead.connect(self._on_data)
        self._port = port

    def _on_data(self) -> None:
        import time
        data = bytes(self._port.readAll().data())
        self._complete(self._assembler.feed(data, time.monotonic()))

    def take_frames(self) -> List[str]:
        import time
        if self._assembler.idle_since(time.monotonic()):
            self._complete(self._assembler.flush())
        frames = []
        while self._frames:
            frames.append(self._frames.popleft())
        return frames

    def close(self) -> None:
        try:
            self._port.close()
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
#  Qt module class — the ONE class LabStation auto-detects (module_type set).
#  BaseModule and the labcore_* / _run_in_thread helpers are injected by
#  LabStation at load time; _in_thread() falls back to synchronous execution
#  so the file also works under plain pytest.
# ═════════════════════════════════════════════════════════════════════════════

from collections import deque

from PySide6 import QtCore, QtGui, QtWidgets


def _in_thread(fn, callback):
    runner = globals().get("_run_in_thread")
    if runner is not None:
        runner(fn, callback)
    else:
        callback(fn())


# The byline written where a person reads WHO, and the identity could not be
# established. Not "" — an empty byline is indistinguishable from a field
# nobody ever filled in, which is exactly the state these columns were in for
# months (see `_current_operator`). Not a bare word either: this is stored in
# the same column as usernames, and the parentheses are what stop it being read
# as one. It is the string LabStation itself shows for this condition.
#
# ONLY for those byline columns. The JSON provenance writes None instead, and
# the difference is deliberate: `lab_search._OPERATOR_KEYS` harvests `by` /
# `user` / `username` out of a log row's detail, and `qc_series.Coverage`
# counts distinct NAMED analysts to decide repeatability from reproducibility.
# A marker string in there would invent a person and produce the exact
# overstatement this whole road exists to prevent.
UNKNOWN_OPERATOR = "(unknown user)"


def context_operator(context) -> Optional[str]:
    """Whoever is signed in, off the LabStation context. None if it cannot say.

    THE IDENTITY IS NOT A GLOBAL AND NEVER WAS. `_load_custom_module` injects
    exactly ten names into a custom module — BaseModule, LabStationContext, the
    five `labcore_*` helpers, ResultEntry, format_timestamp, _run_in_thread —
    and not one of them is a user. This file spent months reading two invented
    names out of `globals()` for the correction-factor and config bylines, and
    both were therefore "" on every bench in the lab, silently.

    It lives on the context object every module is constructed with:
    `LabStationContext.__init__` sets `current_user = None`, LabStationWindow
    assigns it at login, and LabStation reads it exactly as below. The
    context's own docstring is explicit — modules read it there "instead of
    ... routing through module-level globals".

    Total at every hop, and no exception escapes: a missing context, no login
    yet, a user object with no username and a blank username are all simply
    unknown. This runs on the poll worker, where a raise strands `_polling`.
    """
    user = getattr(context, "current_user", None)
    if user is None:
        return None
    return known_text(getattr(user, "username", None))


# ── Fault points (the transfer v4 gate's kill sites) ─────────────────────────
#
# The gate (gauntlet-harness/gate.py, transfer spec §15.3) proves "nothing lost,
# nothing doubled" by killing this module at NAMED places and restarting it.
# The module marks those places itself with `self._fault_point(name)`. In
# production the hook is a no-op; the harness replaces the module-level
# `fault_point` — one global, so calls from the serial reader thread (which is
# not the bench object) are seen too. See tests/test_fault_point.py.
FAULT_POINTS = (
    "before_journal", "after_journal_before_fsync",
    "after_journal_before_cursor", "after_cursor",
    "serial_frame_complete_before_fsync",
    "after_combined_read", "after_batch_landed", "before_filed_journaled",
    "between_upload_chunks",
)


def fault_point(name: str) -> None:
    """No-op. Exists to be replaced by the gate harness."""
    return None


class LEMStationModule:
    """LEM – Lab Equipment Manager: ONE machine per module instance.

    Captures that machine's device prints, maps them onto LabCore test
    methods, shows QC status (specs pulled from LabCore), and stores all
    parsed data in LabCore. The LEM web server elsewhere is the master
    view over all machines."""

    module_type = "LEMStation"
    module_title = "LEM – Lab Equipment Manager"

    def _fault_point(self, name: str) -> None:
        """A named kill site for the gate. A no-op unless the harness has
        replaced the module-level `fault_point`. An unknown name is refused
        only under LEM_FAULT_POINTS_STRICT=1 (tests): on the floor a typo must
        never cost a poll."""
        if name not in FAULT_POINTS:
            if os.environ.get("LEM_FAULT_POINTS_STRICT") == "1":
                raise ValueError("unknown fault point: %r" % (name,))
            return None
        # Looked up at call time on purpose: the harness swaps the global.
        return globals()["fault_point"](name)

    outputs = ("row_parsed", "status_changed")
    inputs = ()

    HISTORY_LIMIT = 500
    DATA_TAB_LIMIT = 100
    # The settings dialog keeps a few raw prints to test mappings against. Four
    # is enough to see the shape of a print; holding twenty was just memory on a
    # bench PC that also runs the instrument.
    RECENT_PRINTS = 4
    SERIAL_DRAIN_MS = 500

    def __init__(self, context) -> None:
        super().__init__(context)

        self._machine: Optional[Machine] = None
        self._history: deque = deque(maxlen=self.HISTORY_LIMIT)
        self._evaluation: Optional[MachineEvaluation] = None
        self._recent_rows: deque = deque(maxlen=self.DATA_TAB_LIMIT)
        self._poll_seconds = 30
        self._polling = False
        self._labcore_table_ready = False
        # When LabCore last REFUSED the declarations, and how long it is being
        # left alone for. A refusal used to cost nothing to repeat, so every
        # road through `_declare_tables` re-fired the whole block at a queue
        # that had just said it was full. See DECLARE_RETRY_SECONDS.
        #
        # Two waits and not one: `_declare_wait_seconds` is what is in force
        # right now — LabCore's own `retry_after` when it sent one — while
        # `_declare_retry_seconds` is where this module's own doubling schedule
        # has got to. Collapsing them would let one `retry_after` of 30 reset
        # the schedule, or a long schedule override a short `retry_after`.
        self._declare_refused_at: Optional[datetime] = None
        self._declare_retry_seconds = DECLARE_RETRY_SECONDS
        self._declare_wait_seconds = DECLARE_RETRY_SECONDS
        # The live road (see post_live). Read from LabCore once, then only
        # again after repeated failures, so a moved server or a rotated token
        # heals itself without a restart on every bench.
        self._live_url = ""
        self._live_token = ""
        self._live_checked = False
        self._live_failures = 0
        # Whether the LAST push came back SPEAKING THE NOTE PROTOCOL — which is
        # a different question from `_live_failures`, and the one the refresh
        # windows turn on.
        #
        # Not "did the push land". An older floor answers `/api/live` with 204
        # and no body: the push landed, so the counter below is right to stay at
        # zero, and there is no note channel at all. See `speaks_live_notes`.
        #
        # The counter cannot carry this. `_live_config` resets it to zero every
        # time it re-reads, so `_live_failures >= LIVE_RETRY_AFTER` is true for
        # one poll in every three or four while the floor is down, and a bench
        # would read its manual override every THIRD poll instead of every poll
        # — ninety seconds of a bench still running after somebody took it off
        # line, arrived at by accident. This is the durable half: False the
        # moment a push fails, True again the moment one lands.
        #
        # False at construction, and that matters: nothing has been delivered
        # yet, so the first poll asks LabCore for everything. Being wrong in
        # this direction costs one read. See `_live_channel_healthy`.
        self._live_delivering = False
        # Whether the one boot-time knock has been made. ONE per module life:
        # a knock on every poll would be a second push per bench for ever, half
        # again the traffic of the road it is helping, for no information after
        # the first answer. See `_probe_live_channel`.
        self._live_probed = False
        # Worker → main thread: "a note invalidated something; re-poll now,
        # do not wait for the tick." `_show_outcome` is the only consumer.
        self._live_followup = False
        # ... and whether the poll now running IS that follow-up, which is what
        # stops the channel driving this bench in a loop. See `_ask_followup`.
        self._in_live_followup = False
        # Machine-log records on their way to lem_machine_log. UNBOUNDED as a
        # deque and bounded in `_log_event` instead: the cap has to be able to
        # refuse a new record rather than silently evict an accepted one, and a
        # maxlen deque can only do the second. See LOG_EVENT_LIMIT.
        self._pending_events: deque = deque()
        # One file reader per (machine, path): its cursor, snapshot and the
        # quiet rule's last look at the file (see `SingleCsvSource`).
        self._sources: dict = {}
        self._source_pending = None
        self._poll_clock: Optional[datetime] = None
        self._events_dropped = 0         # records there was no room for
        # Whether the last drain got its records into LabCore. Optimistic at
        # construction: nothing has been refused, and the caps that consult it
        # only speak about readings this process has actually handled.
        self._log_road_open = True
        self._recent_prints_raw: deque = deque(maxlen=self.RECENT_PRINTS)
        self._serial_reader = None
        # ── the bench journal (see "The bench journal") ──
        # Opened lazily for the bound uid, from the poll worker or the serial
        # reader's thread, under this lock. `_journal_error` is why there is
        # none, when there is none: said on the status line, never read as
        # "nothing to keep".
        self._journal_lock = threading.RLock()
        self._journal = None
        self._journal_uid = ""
        self._journal_error = ""
        # {journal ref: log rows of that reading still to land}; at zero the
        # reading is marked PROJECTED.
        self._journal_unprojected: dict = {}
        # Refs a cap threw out of the results road this process: NOT settled.
        self._journal_dropped: set = set()
        # Frames a previous process journaled and no poll consumed.
        self._journal_carry: list = []
        self._journal_disk_state = None
        self._journal_disk_checked = None
        # Prints the store check dropped because the journal already held them.
        self._journal_suppressed = 0
        self._last_status_pushed = None  # (uid, status, reason) last written
        self._config_read_at = None      # when QC/PM config last ANSWERED
        self._corrections_read_at = None  # when the factors last ANSWERED
        self._override_read_at = None    # when lem_machine_control last ANSWERED
        # WHICH set of correction factors a read still in flight is about.
        #
        # `_refresh_corrections` runs on the WORKER and blocks inside `read_sql`
        # on the same congested LabCore queue the refresh window exists to
        # spare; `set_machine` and `_open_corrections` run on the GUI thread and
        # can both land in the middle of that read. Whoever changes what the
        # read is ABOUT bumps this, and an answer whose generation moved while
        # it was in flight is DISCARDED — not applied, and above all not
        # stamped.
        #
        # Clearing the stamp cannot do this on its own. `apply_corrections`
        # writes into the Machine object the worker captured before the swap,
        # and no assignment on the GUI thread can reach back into a call already
        # in progress. Without the counter, a bench bound while a read was out
        # reports RAW values for the whole CORRECTIONS_REFRESH_SECONDS — the
        # config LabCore hands back carries no corrections, so a fresh Machine
        # starts with none — and an operator's own save is reverted by the
        # pre-edit rows the read brings back and then cached for two minutes.
        # Both are wrong REPORTED results (ISO/IEC 17025 §7.8.2), written to
        # LabCore and QC-judged, not merely late ones.
        self._corrections_epoch = 0
        # Raised by the poll when the correction factors changed, cleared by the
        # LabCore sync once it has re-judged the specs against them. Declared
        # here so the flag has one owner and one lifetime: it is a message that
        # outlives the poll which raised it, and a poll that did not read the
        # factors has nothing to say about it. `set_machine` drops it with
        # everything else it drops — a re-evaluation pending for the previous
        # instrument is not a message about this one — so the lifetime is one
        # binding, not one process. See `_refresh_corrections`.
        self._corrections_changed = False
        # The calibration epoch every QC verdict this bench records is stamped
        # with (see `_refresh_calibration_epoch`). Cached because a poll can
        # carry fifty readings and the answer is the same for all of them.
        self._calibration_epoch = None   # ts of the last 'calibration' event
        self._calibration_read_at = None  # when that lookup last ANSWERED
        self._last_heartbeat = None      # when this module last checked in
        self._pending_uid = ""           # bound uid whose config we can't read yet
        self._qc_tried: set = set()       # spec names we have looked history up for
        self._qc_memory: dict = {}        # {test_name: last verdict} — survives
                                          # the spec list going empty and back
        self._published_specs = None      # last effective specs sent to LabCore
        # ── the results road (see "Whose sample is this?") ──
        # Readings LabCore could not place yet: the cup was run before the LIMS
        # logged the sample in. Offered again every poll, bounded by
        # HELD_ROW_LIMIT and HELD_ROW_MAX_AGE, and mirrored into
        # lem_held_results so a restart at shift change cannot take an unfiled
        # reading with it.
        self._held_rows: List[dict] = []
        # The queue as LabCore last agreed it was — None until we have READ the
        # stored row, and then whatever that read said. It used to start as the
        # empty queue, which is a claim rather than a fact and was wrong in the
        # one case that matters: a process that restores a held reading and
        # files it drains back to empty, "empty == empty" skipped the write, and
        # the mirror went on naming a reading that had already gone out. Every
        # restart inside the seven-day window then filed it again, over whatever
        # the cell held by then. See `_restore_held` and `_persist_held`.
        self._held_persisted = None
        self._held_persisted_keys: set = set()
        self._held_persisted_at = None   # when the mirror was last written
        # Rows the COUNT cap threw out since the last mirror write. The mirror
        # defers an addition and never defers a removal, and a cap eviction
        # looks exactly like a removal while being the opposite of one: a
        # filed reading must leave the mirror at once or a restart re-files it,
        # while an evicted reading was never filed and can never be revived by
        # anything. Left unmarked, a queue sitting at the cap evicted its oldest
        # row every poll, every eviction read as a removal, and the whole
        # HELD_PERSIST_SECONDS rate floor came off — measured at 50 mirror
        # writes in 50 polls, up to 9,898 bytes each, every twelve seconds.
        self._held_evicted_keys: dict = {}
        self._held_restored = False      # read back from LabCore yet?
        self._held_swept_at = None       # when the whole queue was last asked about
        self._idless_reported = False    # said once: prints with no Lab ID
        self._held_notice = ""           # one line for the operator, every poll
        # Every sentence saying a reading has been given up on, kept OUT of
        # `messages` as well as in it.
        #
        # `messages` is a running commentary and `_show_outcome` shows its LAST
        # entry, so a drop notice was routinely buried: `_labcore_sync` appends
        # "Recovered 2 QC result(s) from LabCore." further down the very same
        # sync, and that is what the operator read on the poll that discarded
        # three hundred readings. Terminal news outranks routine news for the one
        # poll it is news, which is why `given_up` was promoted to payload state
        # already; this is the same promotion for the losses the other three caps
        # cause. Bounded, drained by the status line, and never the only record —
        # every one of these readings is in lem_machine_log.
        #
        # Two hundred, not twenty. It is drained on every `_show_outcome`, so a
        # poll's own notices never come close; what filled it was a run of polls
        # whose worker raised before the payload reached the main thread, and at
        # twenty the poll that lost the most readings was the poll whose notices
        # were evicted. The status line stays readable by CONDENSING rather than
        # by dropping — see `_loss_line`.
        self._losses: deque = deque(maxlen=200)
        # Readings parsed but not yet ASKED about, because one poll asks at most
        # IDENTITY_LOOKUP_CHUNK × IDENTITY_LOOKUP_MAX_CHUNKS Lab IDs. Kept apart
        # from `_held_rows` on purpose: these are not unplaceable, they are
        # untried, so the hundred-row cap that is a fair answer to a paperwork
        # backlog would be a silent shredder for an archive import. See
        # IDENTITY_BACKLOG_LIMIT.
        self._identity_backlog: List[dict] = []
        # Rows handed to the results road while it was already busy on the other
        # thread, or that never reached it at all. They join the held queue at
        # the next commit; see `_park`.
        self._parked_rows: List[dict] = []
        self._written_cells: dict = {}    # cells already stored, FIFO-bounded
        # {(printed Lab ID lowered, is a QC standard): the sample it IS}. The
        # Lab ID sequence is never reused, so a certain resolution is permanent
        # and a bench in steady state asks LabCore nothing. Least-recently-used,
        # bounded, and it never remembers a failure — see IDENTITY_CACHE_LIMIT.
        self._identity_cache: dict = {}
        self._retry_ops: List[dict] = []  # ops LabCore's queue refused
        self._identity_lookup_ok = True   # can LabCore say what samples it holds
        # The held queue, the retry queue and the written-cell memory are the
        # only custody an unfiled reading has, and two threads reach them: the
        # poll worker, and the main thread on an explicit operator action. The
        # RLock guards every read-modify-write of that state; `_storing` is
        # taken WITHOUT waiting, so a second caller parks its rows and leaves
        # rather than blocking the GUI behind a network round trip.
        self._results_lock = threading.RLock()
        self._storing = threading.Lock()
        # What the worker's last storage step did, read back by _process_outcome
        # on its way out. `stored` False means the step never ran at all, which
        # is the one case the main thread has to cover.
        self._last_storage = {"identities": {}, "filed": [], "stored": False,
                              "notice": ""}

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.poll_now)
        # Serial is watched continuously: the reader collects bytes on its
        # own (event-driven / daemon thread) and this fast timer only drains
        # COMPLETED frames from an in-memory deque — near-zero cost when
        # idle. The slow timer stays as the periodic LabCore sync tick.
        self._drain_timer = QtCore.QTimer(self)
        self._drain_timer.setInterval(self.SERIAL_DRAIN_MS)
        self._drain_timer.timeout.connect(self._drain_serial)
        # A module loaded but not watching is a real state, and it used to be
        # indistinguishable from one that had crashed: the heartbeat only ran
        # inside the poll pipeline, so stopping the watch stopped the pulse.
        # This ticks regardless, so the floor can tell "here, idle" from "gone".
        self._pulse_timer = QtCore.QTimer(self)
        # Retries a binding LabCore could not hand over at start-up. Single
        # shot and re-armed by `_schedule_bind_retry`, so the interval can grow.
        self._bind_retry_seconds = BIND_RETRY_SECONDS
        self._bind_retry_timer = QtCore.QTimer(self)
        self._bind_retry_timer.setSingleShot(True)
        self._bind_retry_timer.timeout.connect(self._retry_pending_bind)
        self._pulse_timer.setInterval(HEARTBEAT_SECONDS * 1000)
        self._pulse_timer.timeout.connect(self._send_pulse)
        self._pulse_timer.start()

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(6)

        # ── Integrated controls (live in the card's header row) ──
        # All standard widgets stay UNSTYLED so the theme QSS LabStation sets
        # on the ModuleFrame cascades into them and the widget blends in.
        self._override_btn = QtWidgets.QToolButton()
        self._override_btn.setText("Override")
        self._override_btn.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QtWidgets.QMenu(self._override_btn)
        menu.addAction("Clear override", lambda: self._set_override(""))
        menu.addAction("Service", lambda: self._set_override(STATUS_SERVICE))
        menu.addAction("Dead-line", lambda: self._set_override(STATUS_DEAD))
        self._override_btn.setMenu(menu)

        self._poll_btn = QtWidgets.QToolButton()
        self._poll_btn.setText("↻")
        self._poll_btn.setToolTip("Poll now")
        self._poll_btn.clicked.connect(self.poll_now)

        self._interval_btn = QtWidgets.QToolButton()
        self._interval_btn.setText("30 s")
        self._interval_btn.setToolTip("Poll interval")
        self._interval_btn.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        imenu = QtWidgets.QMenu(self._interval_btn)
        for label, secs in (("15 s", 15), ("30 s", 30), ("60 s", 60), ("5 min", 300)):
            imenu.addAction(f"Every {label}",
                            lambda s=secs, l=label: self._set_interval(s, l))
        self._interval_btn.setMenu(imenu)

        self._note_btn = QtWidgets.QToolButton()
        self._note_btn.setText("✎")
        self._note_btn.setToolTip("Add an operator note")
        self._note_btn.clicked.connect(self._on_add_note)

        self._maint_btn = QtWidgets.QToolButton()
        self._maint_btn.setText("🔧")
        self._maint_btn.setToolTip("PM & Calibrations")
        self._maint_btn.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self._maint_menu = QtWidgets.QMenu(self._maint_btn)
        self._maint_menu.aboutToShow.connect(self._rebuild_maint_menu)
        self._maint_btn.setMenu(self._maint_menu)

        self._card = _MachineCard(
            None, on_settings=self._open_settings,
            on_corrections=self._open_corrections,
            controls=[self._poll_btn, self._interval_btn, self._note_btn,
                      self._maint_btn, self._override_btn])
        root.addWidget(self._card)

        # ── Data section — part of the same surface, folded by default ──
        data_bar = QtWidgets.QHBoxLayout()
        self._data_toggle = QtWidgets.QToolButton()
        self._data_toggle.setText("Data")
        self._data_toggle.setCheckable(True)
        self._data_toggle.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self._data_toggle.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._data_toggle.toggled.connect(self._toggle_data)
        data_bar.addWidget(self._data_toggle)
        self._status_label = QtWidgets.QLabel("Ready.")
        _shrink_font(self._status_label, 0.85)
        self._status_label.setStyleSheet("color: rgba(128, 131, 138, 220);")
        data_bar.addWidget(self._status_label)
        data_bar.addStretch()
        root.addLayout(data_bar)

        # ── Manual QC entry — where the Data drop-down is on a parsing bench ──
        # An older instrument prints to paper or to nothing, so there is no
        # drop-down of parsed prints to fold open. What replaces it is ONE box:
        # the reading for an assigned QC test. No Lab ID box — the standard's
        # comes from the assignment, and a box for it is a way to log a good
        # reading against the wrong standard. Built always, shown only when the
        # machine's source is "manual" (see _apply_source_mode).
        entry_bar = QtWidgets.QHBoxLayout()
        entry_bar.setContentsMargins(0, 0, 0, 0)
        # QToolButton + QMenu, never QComboBox — proxy-canvas rule from
        # module_template.py.
        self._manual_method_btn = QtWidgets.QToolButton()
        self._manual_method_btn.setText("QC test")
        self._manual_method_btn.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self._manual_method_btn.setMenu(QtWidgets.QMenu(
            self._manual_method_btn))
        self._manual_method = ""
        self._manual_value = QtWidgets.QLineEdit()
        self._manual_value.setPlaceholderText("QC result")
        self._manual_value.setMaximumWidth(110)
        self._manual_value.returnPressed.connect(self._on_log_manual)
        self._manual_log_btn = QtWidgets.QPushButton("Log QC result")
        self._manual_log_btn.clicked.connect(self._on_log_manual)
        self._manual_note = QtWidgets.QLabel("")
        _shrink_font(self._manual_note, 0.85)
        self._manual_note.setStyleSheet("color: rgba(128, 131, 138, 220);")
        for widget in (self._manual_method_btn, self._manual_value,
                       self._manual_log_btn, self._manual_note):
            entry_bar.addWidget(widget)
        entry_bar.addStretch()
        self._manual_bar = QtWidgets.QWidget()
        self._manual_bar.setLayout(entry_bar)
        self._manual_bar.setVisible(False)
        root.addWidget(self._manual_bar)

        self._data_table = QtWidgets.QTableWidget(0, 2)
        self._data_table.setHorizontalHeaderLabels(["Time", "Parsed print"])
        self._data_table.horizontalHeader().setStretchLastSection(True)
        self._data_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._data_table.setVisible(False)
        root.addWidget(self._data_table, 1)
        root.addStretch()

    # ── Public API (also used by tests) ───────────────────────────────────

    def machine(self) -> Optional[Machine]:
        return self._machine

    def set_machine(self, machine: Machine, publish: bool = True) -> None:
        self._machine = machine
        # Everything cached about QC, specs and PM belongs to the machine that
        # was bound a moment ago, so the next poll asks again rather than
        # judging this instrument against the last one's standards for up to
        # CONFIG_REFRESH_SECONDS. Reconfiguring the same machine lands here too,
        # which is what makes a source or mapping change take effect at once.
        self._config_read_at = None
        # The corrections belong to the bound machine just as much, and more
        # sharply: a stale QC spec judges a reading, a stale correction factor
        # CHANGES it. Carrying the last instrument's offset onto this one's
        # readings would be a wrong reported result, not merely a late one.
        self._corrections_read_at = None
        # And the generation with it, because dropping the stamp is only half of
        # it. A poll worker may be blocked in `read_sql` right now, holding the
        # PREVIOUS instrument's Machine object and about to apply an answer to
        # it and stamp the window — undoing the line above from a thread this
        # one cannot see. Bumping tells that read its subject is gone, so it is
        # discarded and this bench asks for its own factors on the next poll.
        # Until then it holds none: `Machine.to_dict` does not carry them, so a
        # machine bound from LabCore config starts with an empty map and
        # `read_corrections` is the only thing that fills it in.
        self._corrections_epoch += 1
        # The override map is keyed by machine_uid, and what this module cached
        # was the answer for the instrument it no longer is. Keeping the stamp
        # would leave a newly bound bench holding no override at all for the
        # whole window while the floor believes it is off line.
        self._override_read_at = None
        # A re-evaluation the sync has not consumed yet was raised about the
        # instrument that has just been unbound. It says nothing about this one,
        # whose factors have not been read at all — and the poll that reads them
        # raises it again if they differ from the empty map it starts with.
        self._corrections_changed = False
        # The epoch belongs to the machine that was bound a moment ago too, and
        # this one is a DIFFERENT instrument's calibration history. Cleared, not
        # merely re-read: stamping a verdict with the previous bench's
        # calibration is worse than stamping it unknown.
        self._calibration_read_at = None
        self._calibration_epoch = None
        # This module now has an instrument, however it got one. Any binding
        # still being retried is stale, and letting it land later would swap the
        # instrument underneath whoever just chose this one.
        self._stop_bind_retry()
        self._close_serial()  # source config may have changed
        if machine.source_type == "serial":
            self._drain_timer.start()
        else:
            self._drain_timer.stop()
        self._apply_source_mode(machine)
        self._card.set_machine(machine)
        self._card.update_view(self._evaluation, datetime.now())
        # LabCore owns the configuration. `publish=False` is for a config we
        # just READ from it — no point echoing it straight back.
        if publish:
            self._publish_config(machine)

    def _refresh_card(self) -> None:
        machine = self._machine
        if machine is not None:
            self._card.set_machine(machine)
        else:
            self._card.set_machine(None)
        self._card.update_view(self._evaluation, datetime.now())

    def card(self) -> "_MachineCard":
        return self._card

    def evaluation(self) -> Optional[MachineEvaluation]:
        return self._evaluation

    def recent_prints(self) -> List[str]:
        """Raw device prints, newest first — for testing the parser against
        real data in the settings dialog."""
        return list(self._recent_prints_raw)

    def is_polling(self) -> bool:
        return self._timer.isActive()

    def poll_now(self) -> None:
        if self._polling or self._machine is None:
            return
        machine = self._machine

        def work():
            machine2, prints, error = self._ingest(machine)
            return machine2, prints, error

        self._dispatch_pipeline(work)

    def _dispatch_pipeline(self, ingest_fn, manual_rows=None) -> None:
        """Run ingest → parse → evaluate → LabCore sync in the WORKER, and
        only the UI half on the main thread. The worker never raises (a
        raised exception would strand _polling=True forever, because
        LabStation's _run_in_thread drops the callback on error).

        `manual_rows` are rows the operator typed rather than the parser
        produced. Given them, the pipeline skips the parse and runs everything
        else — a typed reading is a measurement like any other."""
        self._polling = True
        history_snapshot = list(self._history)

        def work():
            try:
                machine, prints, error = ingest_fn()
                return self._process_outcome(machine, prints, error,
                                             history_snapshot, None,
                                             manual_rows=manual_rows)
            except Exception as exc:
                return {"machine": self._machine, "raw_prints": [],
                        "rows": [],
                        "evaluation": MachineEvaluation(
                            status=STATUS_UNKNOWN,
                            reason=f"Ingest error: {exc}"),
                        "messages": [f"Ingest error: {exc}"],
                        "now": datetime.now()}

        def done(payload):
            self._polling = False
            if payload:
                self._show_outcome(payload)

        _in_thread(work, done)

    def _drain_serial(self) -> None:
        """Fast serial tick: process frames the moment a report completes.
        Reads only the in-memory frame deque — when nothing arrived, no
        parsing, no LabCore traffic, no UI churn."""
        machine = self._machine
        if machine is None or machine.source_type != "serial" or self._polling:
            return
        reader = self._serial_reader
        if reader is None or reader.error:
            # No reader yet (or it died) — let the normal poll path open it
            # and surface the error on its own schedule.
            return
        frames = [f for f in reader.take_frames() if f.strip()]
        if not frames:
            return
        self._dispatch_pipeline(lambda: (machine, frames, None))

    def log_manual_entry(self, method: str, value: str,
                         now: Optional[datetime] = None) -> bool:
        """Record a QC reading the operator typed. Returns whether it landed.

        The whole of manual mode on this side: find the assigned spec, build the
        row, then hand it to the same pipeline a parsed print goes through —
        off-thread, because it writes to LabCore and the operator is standing at
        the bench waiting for the window to come back.

        A test that is not assigned is refused rather than written under a name
        nothing can check it against."""
        machine = self._machine
        if machine is None:
            return False
        if self._polling:
            # A poll can sit on LabCore HTTP for seconds and the pipeline
            # refuses a second run. Say so, or the click looks like it worked
            # and the reading is gone.
            self._status_label.setText("Busy polling — try again in a moment.")
            return False
        spec = next((s for s in manual_entry_specs(machine)
                     if s.name == method), None)
        if spec is None:
            self._status_label.setText(
                "No QC assigned for that test — assign it in LEM first.")
            return False
        row = manual_qc_row(spec, value, now or datetime.now())
        if row is None:
            self._status_label.setText(
                f"Enter a numeric {spec.name} result to log.")
            return False
        self._dispatch_pipeline(lambda: (machine, [], None), manual_rows=[row])
        return True

    def process_now(self, now: Optional[datetime] = None) -> None:
        """Synchronous ingest + evaluate (poll_now's worker does the same
        in a background thread)."""
        if self._machine is None:
            return
        # The source readers' quiet rule runs on the poll's clock, so a test
        # (and the gate) that polls every 30 s of simulated time is measured
        # on that time, not on how fast the machine running it is.
        self._poll_clock = now
        try:
            machine, prints, error = self._ingest(self._machine)
        finally:
            self._poll_clock = None
        payload = self._process_outcome(machine, prints, error,
                                        list(self._history), now)
        self._show_outcome(payload)

    # ── The bench journal: custody of every reading (transfer v4 §3) ─────────
    #
    # Five touch points, and the order between them is the whole argument:
    #
    #   reader thread   a completed serial frame is journaled before the poll
    #                   can take it                    (`_journal_serial_frame`)
    #   poll, first     whatever a previous process left owed is queued again
    #                                                  (`_journal_recover_once`)
    #   poll, intake    prints the journal already holds are dropped
    #                                                  (`_journal_intake`)
    #   poll, (a)       the readings are appended + fsync'd, THEN queued
    #                                                  (`_journal_poll`)
    #   afterwards      the drain marks a reading PROJECTED when its log rows
    #                   land; the results road marks it SETTLED when it is
    #                   done with it          (`_journal_landed`, `_settle`)
    #
    # A reading not both projected and settled is re-delivered by the next
    # process. Every helper here tolerates a module built without __init__
    # (the tests' stand-ins), because the roads that call them do.

    def _journal_for(self, machine) -> Optional["BenchJournal"]:
        """The journal of the bound machine, opened on first use. None when
        the machine has no uid (nothing to key a journal on) or the journal
        cannot be opened — `_journal_error` then says why."""
        uid = str(getattr(machine, "uid", "") or "") if machine is not None else ""
        if not uid:
            return None
        lock = getattr(self, "_journal_lock", None)
        if lock is None:
            lock = self._journal_lock = threading.RLock()
        with lock:
            journal = getattr(self, "_journal", None)
            if journal is not None and getattr(self, "_journal_uid", "") == uid:
                return journal
            # Kept for the module's life, per uid: rebinding A → B → A must
            # find A's journal as it left it, or A's owed readings would be
            # re-queued a second time on top of the ones still pending here.
            held = getattr(self, "_journals", None)
            if held is None:
                held = self._journals = {}
            journal = held.get(uid)
            if journal is None:
                try:
                    journal = acquire_journal(uid)
                except (JournalError, OSError, ValueError) as exc:
                    self._journal = None
                    self._journal_uid = ""
                    self._journal_error = str(exc) or exc.__class__.__name__
                    return None
                held[uid] = journal
            self._journal = journal
            self._journal_uid = uid
            self._journal_error = ""
            return journal

    def _release_journals(self) -> None:
        lock = getattr(self, "_journal_lock", None) or threading.RLock()
        with lock:
            for journal in (getattr(self, "_journals", None) or {}).values():
                release_journal(journal)
            self._journals = {}
            self._journal = None
            self._journal_uid = ""

    def _journal_holds_files(self, machine) -> bool:
        """Must a FILE source wait this poll? Only when the disk policy has
        paused file ingest.

        NOT when the journal cannot be opened at all (an unwritable LabStation
        folder, a permissions change). Waiting there would stop every result
        on the bench over a local fault the lab can live with for a day, and
        an unwritable data folder has never been allowed to stop processing
        (test_v3's latest-result test pins exactly that). The bench carries on
        the way it did before the journal existed — straight to LabCore, no
        copy kept, a restart may re-read the file — and says so on the status
        line every poll until the journal opens again."""
        if not str(getattr(machine, "uid", "") or ""):
            return False
        journal = self._journal_for(machine)
        if journal is None:
            return False
        return bool(self._journal_disk(journal).get("pause_files"))

    def _journal_disk(self, journal) -> dict:
        """The disk policy's state, re-evaluated at most once a minute — it
        lists the folder and asks the OS for free space."""
        state = getattr(self, "_journal_disk_state", None)
        checked = getattr(self, "_journal_disk_checked", None)
        now = time.monotonic()
        if state is None or checked is None \
                or now - checked >= JOURNAL_DISK_CHECK_SECONDS:
            try:
                state = journal.enforce_disk_policy(datetime.now())
            except (JournalError, OSError) as exc:
                state = {"pause_files": journal.pause_files,
                         "message": f"Bench journal: the disk check failed "
                                    f"({exc}); the last decision stands."}
            self._journal_disk_state = state
            self._journal_disk_checked = now
        return state

    def _source_notes(self, machine) -> str:
        """What the file reader has to say (an unreadable cursor set aside, a
        renamed file that was also changed, a change found by the 15-minute
        check), said once on the status line — and, for as long as it lasts,
        that a file which will not stop changing is being waited for."""
        parts = []
        for source in (getattr(self, "_sources", None) or {}).values():
            while source.notices:
                parts.append(source.notices.pop(0))
            note = getattr(source, "status_note", None)
            if callable(note) and note() and note() not in parts:
                parts.append(note())
        return " ".join(parts)

    def _journal_status(self, machine) -> str:
        """One sentence about the journal for the status line, or ""."""
        uid = str(getattr(machine, "uid", "") or "") if machine is not None else ""
        if not uid:
            return ""
        journal = getattr(self, "_journal", None)
        if journal is None or getattr(self, "_journal_uid", "") != uid:
            error = getattr(self, "_journal_error", "")
            if not error:
                return ""
            return (f"Bench journal unavailable ({error}) — readings go "
                    "straight to LabCore with NO copy kept at this bench"
                    + (", and a restart may read the file again"
                       if machine.source_type in ("single_csv", "multi_csv")
                       else "") + ".")
        parts = []
        while journal.notices:
            parts.append(journal.notices.pop(0))
        state = getattr(self, "_journal_disk_state", None) or {}
        if state.get("message"):
            parts.append(state["message"])
        return " ".join(parts)

    def _journal_recover_once(self, machine, journal, messages) -> None:
        """Re-deliver what a previous process left owed. Once per journal.

        A reading whose log rows never landed is queued again — the rows it
        was journaled with, original timestamps and all. A reading the results
        road never finished with goes back on it, at the front. A frame the
        reader journaled and no poll consumed is carried into the next poll
        that reads. And when the journal has history it is this bench's
        custody, so the LabCore held-results mirror is not read back on top of
        it: two custodians for one reading is how a reading gets filed twice."""
        if journal.recovery_done:
            return
        journal.recovery_done = True
        counts = getattr(self, "_journal_unprojected", None)
        if counts is None:
            counts = self._journal_unprojected = {}
        entries, backlog, no_logs, no_row = [], [], [], []
        for run in journal.open_runs():
            rec, ref = run["rec"], run["ref"]
            if not run["projected"]:
                logs = [a for a in rec.get("log") or () if isinstance(a, list)]
                if logs:
                    entries.extend(_log_entry(args, ref) for args in logs)
                    counts[ref] = len(logs)
                else:
                    no_logs.append(ref)
            if not run["settled"]:
                row = rec.get("row")
                if isinstance(row, dict) and row:
                    row = dict(row)
                    row[JOURNAL_KEY] = ref
                    backlog.append(row)
                else:
                    no_row.append(ref)
        try:
            journal.mark_projected(no_logs)
            journal.mark_settled(no_row)
        except JournalError:
            pass
        if entries:
            # Not queued yet: some of these rows may have LANDED before the
            # previous process died — written, then killed before the mark said
            # so (K2: 100 rows logged twice). `_journal_verify_owed` asks
            # LabCore which are already there before any is sent again.
            self._journal_owed = list(entries) + list(
                getattr(self, "_journal_owed", None) or [])
        if backlog:
            with self._results_lock:
                self._identity_backlog = backlog + list(self._identity_backlog)
        if journal.had_history:
            self._held_restored = True
        self._journal_carry = [_keyed(text, pk, src="serial")
                               for pk, text in journal.pending_frames()]
        owed = len({e.ref for e in entries} | {r[JOURNAL_KEY] for r in backlog})
        if owed or self._journal_carry:
            messages.append(
                f"Picked up {owed + len(self._journal_carry)} reading(s) this "
                "bench had journaled but not yet delivered; sending them now.")

    def _journal_intake(self, journal, prints) -> list:
        """The store check: carried frames first, then this poll's prints,
        minus every print whose key the journal already holds (or that
        appears twice — the reader and the carry can both hand one over)."""
        carry = list(getattr(self, "_journal_carry", None) or [])
        self._journal_carry = []
        out, seen = [], set()
        for text in carry + list(prints):
            pk = getattr(text, "pk", None)
            if pk:
                if pk in seen:
                    continue
                if journal.known(pk):
                    # Counted, because suppression makes a re-read harmless to
                    # the RECORD but not free: a bench re-reading its whole file
                    # every poll is a cost nobody would otherwise see, and this
                    # number is what "replays stopped" looks like on the floor.
                    self._journal_suppressed = \
                        getattr(self, "_journal_suppressed", 0) + 1
                    continue
                seen.add(pk)
            out.append(text)
        return out

    def _journal_poll(self, machine, journal, prints, rows, sources, now,
                      messages, extra_records=()) -> bool:
        """(a): append this poll's readings in ONE fsync'd write, then queue
        their log rows tagged with the record they came from. Returns whether
        they were journaled; False leaves the caller on today's road."""
        produced = {getattr(src, "pk", None) for src in sources
                    if src is not None}
        idle = [text.pk for text in prints
                if getattr(text, "pk", None) and text.pk not in produced]
        extra = [dict(r) for r in extra_records or ()]
        if not rows and not idle and not extra:
            return False
        self._fault_point("before_journal")
        operator = self._current_operator()
        calibration_id = getattr(self, "_calibration_epoch", None)
        logs: Dict[int, list] = {}
        for row, kind, lab_id, test_name, value, detail in run_log_events(
                machine, rows, operator, calibration_id):
            logs.setdefault(id(row), []).append(build_log_insert(
                machine.uid, kind, now, lab_id=lab_id, test_name=test_name,
                value=value, detail=detail)[1])
        # Prints that made no row are consumed FIRST in the write, so a torn
        # tail can only ever cut readings — never the note that a header line
        # was read, which would let it come back as one.
        records = [{"kind": "consumed", "pks": idle}] if idle else []
        records += extra
        lead = len(records)
        for row, src in zip(rows, sources):
            detail = run_log_detail(row)
            records.append({
                "kind": "run",
                "origin": (getattr(src, "origin", None) or "live")
                if src is not None else "manual",
                "src": getattr(src, "src", None) or machine.source_type,
                "pk": getattr(src, "pk", None),
                "lh": getattr(src, "lh", None),
                "line": str(src) if src is not None else None,
                "lab_id": str(row.get(LAB_ID_KEY) or "").strip(),
                "values": detail.get("values") or {},
                "raw": detail.get("raw") or {},
                "corrections": detail.get("corrections") or {},
                "row": {k: v for k, v in row.items() if k != JOURNAL_KEY},
                "log": logs.get(id(row), []),
            })
        try:
            refs = journal.append(records, ts=_poll_ts(now))
        except JournalError as exc:
            messages.append(f"Bench journal write failed ({exc}); this poll's "
                            "readings go to LabCore with no copy kept at the "
                            "bench.")
            return False
        self._fault_point("after_journal_before_cursor")
        counts = getattr(self, "_journal_unprojected", None)
        if counts is None:
            counts = self._journal_unprojected = {}
        lock = self._journal_lock_or_new()
        for row, ref in zip(rows, refs[lead:]):
            row[JOURNAL_KEY] = ref
            args_list = logs.get(id(row), [])
            if args_list:
                # Counted BEFORE queued: a drain on another worker that lands
                # an entry the instant it is queued must find its count.
                with lock:
                    counts[ref] = len(args_list)
            for args in args_list:
                if len(self._pending_events) >= LOG_EVENT_LIMIT:
                    # Refused, as `_log_event` refuses — but kept: the count
                    # never reaches zero, the reading stays unprojected in the
                    # journal, and the next process writes it.
                    self._events_dropped += 1
                    continue
                self._pending_events.append(_log_entry(args, ref))
        return True

    def _journal_verify_owed(self, machine, read_sql, messages) -> None:
        """Re-deliver what a previous process left unprojected — except what
        already landed.

        A reading is marked PROJECTED only after LabCore accepted its rows, so
        a process killed between the accept and the mark leaves rows the
        journal still owes that LabCore already has. Sent again they are
        duplicates (K2: a kill after the 2nd of 3 log batches cost 100). So
        before re-queueing, ONE read asks LabCore for this bench's rows at the
        owed rows' timestamps, and for each exact row (every column) the bench
        sends only how many more the journal says should exist than LabCore
        holds. Exact on every column, timestamp included, and counted rather
        than "exists", so two genuine identical prints in one poll are still
        two rows. The rows found already there are counted as landed, which
        marks their readings projected.

        A read that fails is not "none landed": the rows stay owed at the
        bench, nothing is sent, and the next poll asks again."""
        owed = list(getattr(self, "_journal_owed", None) or [])
        if not owed:
            return
        journal = self._journal_for(machine) if machine is not None else None
        stamps = sorted({str(e[1][1]) for e in owed})
        uid = str(owed[0][1][0])

        def key(args):
            return tuple("" if v is None else str(v) for v in args[:7])
        res = None
        if callable(read_sql) and journal is not None:
            rows: list = []
            res = {"rows": rows}
            step = max(1, int(OWED_CHECK_STAMPS))
            for at in range(0, len(stamps), step):
                part = stamps[at:at + step]
                marks = ",".join("?" for _ in part)
                try:
                    got = read_sql(
                        "SELECT machine_uid, ts, kind, lab_id, test_name, value, "
                        "detail FROM lem_machine_log WHERE machine_uid = ? AND ts "
                        f"IN ({marks})", [uid] + part)
                except Exception as exc:          # noqa: BLE001 — any failure
                    got = {"error": str(exc) or exc.__class__.__name__}
                if not isinstance(got, dict) or got.get("error"):
                    res = got
                    break
                rows.extend(got.get("rows") or [])
        if not isinstance(res, dict) or res.get("error") or journal is None:
            why = (res or {}).get("error") if isinstance(res, dict) else "no answer"
            messages.append(
                f"{len(owed)} machine-log record(s) a previous run left owed "
                f"are kept at the bench: LabCore could not be asked which of "
                f"them already landed ({why or 'no journal'}); asking again "
                "next poll.")
            return
        have: Dict[tuple, int] = {}
        for r in res.get("rows") or []:
            k = key([r.get("machine_uid"), r.get("ts"), r.get("kind"),
                     r.get("lab_id"), r.get("test_name"), r.get("value"),
                     r.get("detail")])
            have[k] = have.get(k, 0) + 1
        want: Dict[tuple, int] = {}
        stamp_set = set(stamps)
        try:
            records = journal._scan()
        except JournalError as exc:
            messages.append(f"{len(owed)} owed machine-log record(s) are kept "
                            f"at the bench: the journal could not be read "
                            f"({exc}); asking again next poll.")
            return
        for rec in records:
            if rec.get("kind") != "run":
                continue
            for args in rec.get("log") or ():
                if isinstance(args, list) and len(args) >= 7 \
                        and str(args[1]) in stamp_set:
                    k = key(args)
                    want[k] = want.get(k, 0) + 1
        room = {k: want.get(k, 0) - have.get(k, 0) for k in want}
        send, landed = [], []
        for entry in owed:
            k = key(entry[1])
            if room.get(k, 1) > 0:
                room[k] = room.get(k, 1) - 1
                send.append(entry)
            else:
                landed.append(entry)
        self._journal_owed = []
        if send:
            self._pending_events.extendleft(reversed(send))
        if landed:
            self._journal_landed(landed)
            messages.append(f"{len(landed)} machine-log record(s) a previous "
                            "run left owed were already in LabCore and were "
                            "not sent again.")

    def _journal_landed(self, batch) -> None:
        """The drain got `batch` into lem_machine_log: a reading all of whose
        rows have now landed is PROJECTED. A mark that fails to write costs a
        re-sent row after the next restart, never a reading."""
        counts = getattr(self, "_journal_unprojected", None)
        if not counts:
            return
        done = []
        # Under the journal lock: an operator's note drains on its own worker
        # while a poll may be draining too.
        with self._journal_lock_or_new():
            for entry in batch:
                ref = getattr(entry, "ref", None)
                if ref is None or ref not in counts:
                    continue
                counts[ref] -= 1
                if counts[ref] <= 0:
                    del counts[ref]
                    done.append(ref)
        if done:
            self._journal_mark("projected", done)

    def _journal_lock_or_new(self):
        lock = getattr(self, "_journal_lock", None)
        if lock is None:
            lock = self._journal_lock = threading.RLock()
        return lock

    def _journal_mark(self, kind: str, refs) -> None:
        """Mark refs in whichever of this module's journals holds them (a
        journal ignores refs it does not hold — a rebinding can leave a
        reading of the previous instrument in flight)."""
        journals = list((getattr(self, "_journals", None) or {}).values())
        current = getattr(self, "_journal", None)
        if current is not None and current not in journals:
            journals.append(current)
        for journal in journals:
            try:
                (journal.mark_projected if kind == "projected"
                 else journal.mark_settled)(refs)
            except JournalError:
                # Unmarked means re-offered after a restart: a re-sent row or
                # cell, never a lost reading.
                pass

    def _journal_note_dropped(self, rows) -> None:
        """A cap threw these rows off the results road. They are NOT settled:
        the journal keeps them owed, and the next process offers them again."""
        dropped = getattr(self, "_journal_dropped", None)
        if dropped is None:
            dropped = self._journal_dropped = set()
        for row in rows or ():
            if isinstance(row, dict) and row.get(JOURNAL_KEY):
                dropped.add(row[JOURNAL_KEY])

    def _journal_settle(self, refs) -> None:
        """The results road has decided on `refs`: those not back in its
        custody — filed, given up after seven days, carrying no Lab ID, or a
        QC standard's check — are SETTLED. Held, backlogged, parked or
        cap-dropped readings are not."""
        if not refs:
            return
        with self._results_lock:
            custody = {row.get(JOURNAL_KEY)
                       for row in (list(self._held_rows)
                                   + list(self._identity_backlog)
                                   + list(self._parked_rows))
                       if isinstance(row, dict)}
        dropped = getattr(self, "_journal_dropped", None) or set()
        settled = set(refs) - custody - dropped
        if dropped:
            dropped.difference_update(refs)
        if settled:
            self._journal_mark("settled", sorted(settled))

    # ── Ingestion (thread-safe half: no widget access) ────────────────────

    def _ingest(self, machine: Machine):
        """Collect new device prints. Returns (machine, prints, error).

        A file source's read is not CONSUMED here: the cursor is saved, or the
        files moved to processed/, only by `_commit_source` once the journal
        holds what was read (§3.3 (a) then (b)). A read nobody committed —
        the poll died, or raised — is simply read again next time."""
        self._source_pending = None
        # The disk policy can pause FILE ingest (§3.4): the file keeps the
        # bytes and the offset does not move, so nothing is lost by waiting,
        # and the status line says why. Serial and manual are never held back
        # here — they have no other copy.
        if (machine.source_type in ("single_csv", "multi_csv")
                and self._journal_holds_files(machine)):
            return machine, [], None
        if machine.source_type == "multi_csv":
            return self._ingest_multi(machine)
        if machine.source_type == "serial":
            return self._ingest_serial(machine)
        if machine.source_type == "manual":
            return self._ingest_manual(machine)
        return self._ingest_single(machine)

    def _ingest_manual(self, machine: Machine):
        """Nothing to read: the operator types this bench's readings.

        A manual bench still polls, and that is the point — the poll is what
        keeps QC freshness, PM/Cal and the heartbeat moving. It just comes back
        with no prints and, importantly, no error: there is no file to be
        missing, so a stale `csv_path` must not report one."""
        return machine, [], None

    def _ingest_single(self, machine: Machine):
        """A tailed file, through its `SingleCsvSource`: the append path when
        the cursor still describes the file, the rewrite resolver once a
        change it cannot explain has gone quiet. Where the bench is up to
        comes from cursor.json in its journal folder — never from the
        configuration's stored offset (§4.1)."""
        source = self._source_for(machine)
        now = getattr(self, "_poll_clock", None) or datetime.now()
        try:
            read = source.read(now)
        except OSError as exc:
            return machine, [], f"File error: {exc}"
        except JournalError as exc:
            return machine, [], f"Source cursor error: {exc}"
        prints = list(read.prints)
        self._source_pending = (prints, read, machine)
        return machine, prints, None

    def _source_for(self, machine: Machine) -> "SingleCsvSource":
        """This module's reader for the machine's file, created on first use.
        Its cursor lives in the machine's journal folder; with no journal the
        bench keeps it in memory only, as v3.9 did, and says so."""
        sources = getattr(self, "_sources", None)
        if sources is None:
            sources = self._sources = {}
        uid = str(getattr(machine, "uid", "") or "")
        key = (uid, os.path.normcase(os.path.abspath(machine.csv_path or "")))
        source = sources.get(key)
        if source is None:
            journal = self._journal_for(machine) if uid else None
            store = CursorStore(journal.dir) if journal is not None else None
            source = sources[key] = SingleCsvSource(machine.csv_path, store)
        return source

    def _commit_source(self, machine, prints, messages,
                       pending=None) -> None:
        """(b) of §3.3: the read this poll's prints came from is consumed —
        cursor.json and snapshot.bin saved, or the files moved out of the
        watched folder. Called after the journal append, and only for the very
        prints `_ingest` returned (a typed reading's pipeline carries none)."""
        if pending is None:
            pending = self._take_source_pending(prints)
        if pending is None:
            return
        _prints, read, src_machine = pending
        for note in read.commit():
            messages.append(note)
        if read.offset is not None and src_machine is not None:
            # Mirrored, never read: a bench rolled back to v3.9 takes its
            # offset from the published configuration.
            src_machine.last_position = read.offset
        self._fault_point("after_cursor")

    def _take_source_pending(self, prints):
        pending = getattr(self, "_source_pending", None)
        self._source_pending = None
        if pending is None or pending[0] is not prints:
            return None
        return pending

    def _ingest_multi(self, machine: Machine):
        """Any file sitting in the watched folder is unprocessed — read it,
        then move it into the `processed` subfolder. No name or timestamp
        bookkeeping: presence in the folder IS the queue.

        With a journal (§4.3) the move to processed/ happens only after the
        journal holds the reading — v3.9 moved first, and a kill between the
        move and the log write lost the reading (K7: 3 lost). The file is
        first moved aside into `.lem_inflight/`: that move is still the proof
        that the instrument has let go of it (a locked file is never read),
        and a kill before the journal leaves it there for the next poll to
        read again. Each reading is keyed on (name, size, mtime_ns, sha256),
        so a re-read of a file the journal already holds is dropped.

        Without a journal the bench does exactly what it did before: a file
        is only delivered once its move to processed/ succeeds."""
        folder = machine.csv_path
        if not os.path.isdir(folder):
            return machine, [], f"Folder not found: {folder}"
        try:
            names = sorted(os.listdir(folder))
        except OSError as exc:
            return machine, [], f"Folder error: {exc}"
        journal = self._journal_for(machine) \
            if str(getattr(machine, "uid", "") or "") else None
        if journal is None:
            return self._ingest_multi_unjournaled(machine, folder, names)

        prints, errors, moves = [], [], []
        inflight = os.path.join(folder, INFLIGHT_DIRNAME)
        candidates = []
        try:
            staged = sorted(os.listdir(inflight)) if os.path.isdir(inflight) else []
        except OSError as exc:
            return machine, [], f"Folder error: {exc}"
        candidates += [(os.path.join(inflight, n), True) for n in staged]
        candidates += [(os.path.join(folder, n), False) for n in names
                       if not n.startswith(".")]
        for path, is_staged in candidates:
            if not os.path.isfile(path):
                continue
            if not is_staged:
                try:
                    os.makedirs(inflight, exist_ok=True)
                    dest = _unique_path(inflight, os.path.basename(path))
                    shutil.move(path, dest)
                except OSError as exc:
                    # Still being written / locked — leave it for the next
                    # poll rather than read half a file.
                    errors.append(f"{os.path.basename(path)}: {exc}")
                    continue
                path = dest
            try:
                with open(path, "rb") as f:
                    data = f.read()
                    st = os.fstat(f.fileno())
            except OSError as exc:
                errors.append(f"{os.path.basename(path)}: {exc}")
                continue
            moves.append(path)
            text = data.decode(_decode_block(data), errors="replace").strip()
            if text:
                pk = multi_file_key(machine.uid, os.path.basename(path),
                                    st.st_size, st.st_mtime_ns, _sha(data))
                prints.append(_keyed(text, pk, src="multi_csv:" + folder))
        archive = os.path.join(folder, PROCESSED_DIRNAME)

        def commit():
            notes = []
            for path in moves:
                try:
                    os.makedirs(archive, exist_ok=True)
                    shutil.move(path, _unique_path(archive, os.path.basename(path)))
                except OSError as exc:
                    # It stays in .lem_inflight; the next poll reads it again,
                    # the journal recognises it, and the move is retried.
                    notes.append(f"{os.path.basename(path)} could not be moved "
                                 f"to {PROCESSED_DIRNAME} ({exc}); it is "
                                 "recorded and the move will be retried.")
            return notes
        error = ("Some files could not be archived: "
                 + "; ".join(errors[:3])) if errors and not prints else None
        out = list(prints)
        self._source_pending = (out, _SourceRead(out, commit=commit), None)
        return machine, out, error

    def _ingest_multi_unjournaled(self, machine: Machine, folder: str, names):
        """v3.9's multi_csv road, for a bench whose journal cannot be opened:
        a file is only delivered once its move succeeds, so a locked file can
        never be parsed twice."""
        prints = []
        errors = []
        archive = os.path.join(folder, PROCESSED_DIRNAME)
        # A file a journaled run moved aside and never got to processed/ is
        # still a reading: never stranded because the journal is gone now.
        inflight = os.path.join(folder, INFLIGHT_DIRNAME)
        try:
            staged = sorted(os.listdir(inflight)) if os.path.isdir(inflight) else []
        except OSError:
            staged = []
        paths = [(os.path.join(inflight, n), n) for n in staged]
        paths += [(os.path.join(folder, n), n) for n in names
                  if not n.startswith(".")]
        for path, name in paths:
            if not os.path.isfile(path):
                continue
            try:
                text, _ = tail_new_text(path, 0)
            except OSError as exc:
                errors.append(f"{name}: {exc}")
                continue
            try:
                os.makedirs(archive, exist_ok=True)
                shutil.move(path, _unique_path(archive, name))
            except OSError as exc:
                # Couldn't archive it (still being written / locked) —
                # leave it for the next poll rather than risk a duplicate.
                errors.append(f"{name}: {exc}")
                continue
            if text.strip():
                prints.append(text.strip())
        error = ("Some files could not be archived: "
                 + "; ".join(errors[:3])) if errors and not prints else None
        return machine, prints, error

    def _ingest_serial(self, machine: Machine):
        """RS-232 source: reports framed by idle gaps on the wire. The
        reader (QtSerialPort when available, raw ctypes/termios otherwise)
        collects bytes continuously; polls drain the completed frames."""
        if self._serial_reader is None and not machine.com_port:
            return machine, [], "No COM port configured — set one in ⚙ settings."
        if self._serial_reader is None:
            try:
                self._serial_reader = self._open_serial_reader(machine)
            except OSError as exc:
                self._serial_reader = None
                return machine, [], f"Could not open {machine.com_port}: {exc}"
        reader = self._serial_reader
        if reader.error:
            error = reader.error
            self._close_serial()  # re-open attempt on the next poll
            return machine, [], error
        frames = [f for f in reader.take_frames() if f.strip()]
        return machine, frames, None

    def _open_serial_reader(self, machine: Machine, port=None):
        """A reader for this machine whose every completed frame is journaled
        on the reader's own path before the poll can take it (see
        `_FrameSink`). Given `port`, the OS backend is replaced and no thread
        is started: the caller drives `_on_bytes` / `_on_idle` itself."""
        on_frame = self._journal_serial_frame
        if port is not None:
            return _RawSerialReader(machine, on_frame=on_frame, port=port)
        # Constructed with the machine alone — the readers' long-standing
        # contract — and handed the journal before any byte can arrive: the
        # raw reader's thread has not started, and the Qt reader's readyRead
        # cannot fire until this call returns to the event loop.
        if _qt_serial_available():
            reader = _QtSerialReader(machine)
            reader._on_frame = on_frame
            return reader
        reader = _RawSerialReader(machine)
        reader._on_frame = on_frame
        reader.start()
        return reader

    def _journal_serial_frame(self, frame: str) -> str:
        """Journal one completed serial frame. Runs on the READER's thread.

        Returns the frame tagged with its replay key, or untagged if there is
        no journal to put it in — then the poll journals it with the rest of
        its readings, which is today's custody and no worse. The fault point
        sits between completion and the fsync: a kill there is the one frame
        the stated residual allows (K8r)."""
        machine = self._machine
        if machine is None or not frame.strip():
            return frame
        journal = self._journal_for(machine)
        if journal is None:
            return frame
        self._fault_point("serial_frame_complete_before_fsync")
        try:
            pk = journal.append_frame(frame)
        except (JournalError, OSError, ValueError):
            return frame
        return _keyed(frame, pk, src="serial")

    def _close_serial(self) -> None:
        if self._serial_reader is not None:
            try:
                self._serial_reader.close()
            except Exception:
                pass
        self._serial_reader = None

    # ── Worker half: parse, evaluate, ALL LabCore traffic (no widgets) ────

    def _current_operator(self) -> Optional[str]:
        """Who is signed in at this bench — the ONE way this file asks.

        Three roads need it: the QC verdict's provenance, the correction-factor
        byline and the config-publish byline. They used to ask three different
        ways, two of them for a global LabStation has never injected, and the
        wrong answers were invisible because "" is what an unfilled field looks
        like. One accessor so that cannot drift apart again.

        `getattr` on `context` too: `_queue_run_events` is reachable on objects
        that have none, and returning "unknown" beats raising on the poll
        worker.
        """
        return context_operator(getattr(self, "context", None))

    def _refresh_corrections(self, machine: Machine, now: datetime) -> bool:
        """Re-read the correction factors for this machine (see
        `read_corrections`). Kept as a method so the poll reads like a sequence.

        Returns whether the factors CHANGED — never whether they were read. The
        stamp is set only when LabCore actually answered, so a refusal is asked
        again on the next poll instead of being cached as a configuration.

        The generation is captured BEFORE the read and checked after it. This
        runs on the worker and the read is the slow part — a query on the very
        queue that is congested — so the GUI thread has a wide open window in
        which to bind a different instrument or save a correction factor. An
        answer that comes back into a bumped generation is about a bench that is
        no longer this bench, or about the factor the operator has just
        replaced, and it is thrown away whole: not applied to the Machine object
        this call captured, and not stamped. Discarding costs one read on the
        next poll; believing it costs CORRECTIONS_REFRESH_SECONDS of results
        reported with an offset nobody chose. See `_corrections_epoch`.
        """
        epoch = self._corrections_epoch
        # The floor first, LabCore only when it cannot answer. Both are read
        # INSIDE the generation captured above, because the race the generation
        # exists to close is a property of this running off-thread, not of
        # LabCore being slow — the GUI thread can bind another instrument or
        # save a factor while a floor GET is in flight just as easily. And the
        # floor's snapshot is up to twelve seconds old, so an answer that
        # crosses the operator's own save is carrying the PRE-edit factor and
        # would silently revert it (ISO/IEC 17025 s7.8.2).
        floor = self._floor_config(machine)
        if floor is not None:
            answered, wanted = True, parse_correction_rows(
                floor["corrections"]["rows"])
        else:
            answered, wanted = fetch_corrections(
                machine.uid, globals().get("labcore_read_sql"))
        if not answered:
            return False
        if epoch != self._corrections_epoch:
            return False
        if wanted == dict(machine.corrections or {}):
            self._corrections_read_at = now
            return False
        apply_corrections(machine, wanted)
        self._corrections_read_at = now
        return True

    def _refresh_calibration_epoch(self, machine: Machine,
                                   now: datetime) -> None:
        """Which calibration this poll's QC verdicts belong to.

        At the top of the poll, beside `_refresh_corrections`, because
        `_queue_run_events` runs before `_labcore_sync` — an epoch resolved in
        the sync would stamp this poll's readings with the previous poll's
        answer, and on a module's first poll with nothing at all.

        **It costs no LabCore read, ever.** The epoch is `last_done` of this
        bench's calibration tasks, which it already holds: `machine.maintenance`
        arrives on the config road (`/api/bench/<uid>/config`), served from the
        web server's snapshot at zero LabCore ops. That road exists because a
        per-bench timer read multiplies by a bench count Ryan is still growing,
        and an earlier draft of this method spent an op here —
        `test_restart_stampede` counts the reads a poll makes and caught it as a
        third where the road allows two.

        `lem_maintenance.last_done` and the `kind='calibration'` log row are the
        SAME event: `complete_task` writes both, and it is the only thing that
        writes either. The log row is the audit record, the task is the state,
        and reading the state is free. A bench with no calibration task has
        therefore never logged a calibration, so UNKNOWN is the correct answer
        rather than a question worth an operation.

        **The epoch is a DATE, not a timestamp**, because `last_done` is one.
        Two calibrations on the same day read as one, which is coarser than the
        log row would be. It is enough for what the epoch is for — deciding
        whether a QC series SPANS calibrations — and it fails in the safe
        direction: a series that looks like one epoch when it was two withholds
        the u(Rw) claim rather than manufacturing it.

        A calibration completed on this bench is in force on the very next poll:
        `complete_task` sets `last_done` and clears the stamp.
        """
        if machine is None:
            return
        done = sorted(
            d for d in (known_text(getattr(task, "last_done", ""))
                        for task in (machine.maintenance or [])
                        if getattr(task, "kind", "") == "calibration")
            if d)
        # Absence is UNKNOWN and is written as such — never "" and never a
        # carried-over value from the instrument that was bound before this one.
        self._calibration_epoch = done[-1] if done else None
        self._calibration_read_at = now

    def _live_config(self) -> tuple:
        """Where the floor listens, and with what token.

        Read once. Re-read only after LIVE_RETRY_AFTER consecutive failures —
        an unreachable floor must not turn into a LabCore read on every poll of
        every bench, which is the load pattern this whole channel exists to
        avoid. An unreadable answer keeps whatever was already known.
        """
        if self._live_checked and self._live_failures < LIVE_RETRY_AFTER:
            return self._live_url, self._live_token
        self._live_checked = True
        self._live_failures = 0
        read_sql = globals().get("labcore_read_sql")
        if not callable(read_sql):
            return self._live_url, self._live_token
        try:
            result = read_sql(*build_live_config_query())
        except Exception:
            return self._live_url, self._live_token
        if isinstance(result, dict) and not result.get("error"):
            self._live_url, self._live_token = parse_live_config(
                result.get("rows") or [])
        return self._live_url, self._live_token

    def _live_channel_healthy(self) -> bool:
        """Is the note channel actually delivering right now?

        The one question the two refresh windows turn on, and the reason they
        are safe. `live_url` may never have been published, the floor may be
        unreachable, and `post_live` swallows every failure by design — so
        "there is a note channel" is a claim that has to be EARNED, not assumed
        from a row in `lem_meta`.

        Earned by an answer that SPEAKS THE NOTE PROTOCOL, and specifically not
        by a push that merely landed. An un-upgraded floor — one the rollout has
        not reached, a rolled-back one, or a proxy that swallows the body —
        answers with 204 and nothing, which is a perfectly successful push
        carrying no note and never able to carry one. See `speaks_live_notes`,
        which is where `_push_live` gets the flag below from and which
        `parse_live_notes` shares, so the two can never disagree about what
        "the floor is talking to us" means.

        Deliberately reads the CACHED live config and never calls
        `_live_config`, which would put a LabCore read inside the gate whose
        whole purpose is to remove LabCore reads.

        `_live_failures` is checked as well as `_live_delivering` because the
        retry contract is stated in terms of it, but `_live_delivering` is the
        load-bearing half: `_live_config` zeroes the counter every time it
        re-reads, so the counter alone drops back under the threshold and would
        re-open the window on a floor that is still dead. See `_live_delivering`.
        """
        return (bool(self._live_url)
                and self._live_delivering
                and self._live_failures < LIVE_RETRY_AFTER)

    def _floor_config(self, machine: Machine) -> Optional[dict]:
        """This bench's configuration from the floor, or None to ask LabCore.

        The PREFERRED source for every config read: the floor holds all of
        these tables in a 12-second in-memory snapshot at a cost that does not
        grow
        with the bench count, while every `read_sql` consumes a slot in the one
        serialised queue LabCore runs reads and writes through. See
        `floor_config_results`.

        `_live_channel_healthy()` is the gate, and it is already the right
        one — it is the question "is there a floor talking to this bench at all",
        earned by a push that came back speaking the note protocol rather than
        assumed from a row in `lem_meta`. A floor the note road has given up on
        would otherwise cost a FLOOR_CONFIG_TIMEOUT stall on every poll of
        every bench before falling back anyway. It also means the first poll of
        a module's life always reads LabCore, which is correct: nothing has
        proved the channel yet.

        Deliberately reads the CACHED `_live_url` / `_live_token` and never
        calls `_live_config`, which would put a LabCore read inside the road
        whose whole purpose is to remove LabCore reads. The health gate above
        already requires a url, so there is one.

        Returning None is always SAFE — it means the caller reads LabCore,
        exactly as it did before this road existed — which is why every failure
        this can see ends here: an unhealthy channel, no answer, a non-200, a
        body that is not a dict, a body missing keys, a body about another
        bench, and a snapshot older than FLOOR_CONFIG_MAX_AGE_SECONDS.

        Total, like everything else on the worker's road: a raise here strands
        `_polling` and the bench stops polling altogether (see `post_live`).
        `fetch_floor_config` swallows its own failures, but this must not
        DEPEND on that — the guarantee has to hold at the seam as well as
        inside it.
        """
        if machine is None or not self._live_channel_healthy():
            return None
        try:
            return floor_config_results(
                fetch_floor_config(self._live_url, self._live_token,
                                   machine.uid),
                machine.uid)
        except Exception:
            return None

    def _act_on_live_notes(self, notes) -> bool:
        """Drop what the floor says is stale. Returns whether anything was.

        WORKER thread — this only invalidates state; it starts nothing. The
        return value is what `_show_outcome` turns into an immediate follow-up
        poll, because this runs at the very END of `_process_outcome`, after
        `_labcore_sync` has already read past the values being dropped.
        """
        acted = False
        if LIVE_NOTE_CORRECTIONS in notes:
            self._corrections_read_at = None
            # And the generation with it. This is not belt-and-braces: a
            # corrections read may be blocked in `read_sql` on the congested
            # queue at this very moment, and it BEGAN BEFORE the note existed —
            # so the rows it is carrying are the pre-change ones the note is
            # about. Applied, they overwrite the new factors and then stamp the
            # window over the line above, and the bench reports results carrying
            # an offset the floor has already replaced (ISO/IEC 17025 §7.8.2).
            # Nulling the stamp cannot reach a call already in progress; the
            # counter can. See `_corrections_epoch`.
            self._corrections_epoch += 1
            acted = True
        if LIVE_NOTE_OVERRIDE in notes:
            self._override_read_at = None
            acted = True
        return acted

    def _push_live(self, payload: dict) -> None:
        """Tell the floor what this poll found, and act on what it says back.

        Best-effort and silent: no configuration, no floor, or a refused push
        all mean the floor falls back to the record — which is how it behaved
        before this road existed.
        """
        try:
            machine = payload.get("machine")
            evaluation = payload.get("evaluation")
            if machine is None or evaluation is None:
                return
            url, token = self._live_config()
            if not url:
                return
            body = build_live_payload(machine, evaluation,
                                      payload.get("now") or datetime.now(),
                                      self._poll_seconds,
                                      payload.get("rows") or [])
            # Dropped BEFORE the push, so EVERY way out of what follows leaves
            # the refresh windows open: a refusal, an exception from a
            # `post_live` that stops swallowing them, an exception from anything
            # after it. The read this gates is the one that takes a bench off
            # line, and the only tolerable direction to be wrong in is the one
            # that costs a LabCore read. Re-raised to True below on the single
            # path that proves the channel is delivering.
            self._live_delivering = False
            answer = post_live(url, token, body)
            # `is not None`, never truthiness. A success with an empty body is
            # `{}` and falsy, and counting that as a failure would walk the
            # counter to LIVE_RETRY_AFTER on a healthy floor and re-read the
            # live config out of LabCore on a loop. See `post_live`.
            if answer is None:
                self._live_failures += 1
                return
            self._live_failures = 0
            # The push LANDED — so the address and token are right and
            # `_live_config` has no reason to go back to LabCore. That is what
            # the counter above records, and it is a DIFFERENT question from
            # the one below.
            #
            # Delivery is not health. An older floor — and there is always one
            # mid-rollout, because the benches are separate PCs and cannot be
            # upgraded in the same instant as the server — answers `/api/live`
            # with 204 and no body, which arrives here as `{}`. Accepted, and
            # carrying no note, for ever. Taking health from `answer is not
            # None` therefore opened the fifteen-minute window on the manual
            # override against a floor that has never heard of notes: a bench
            # kept running for up to a quarter of an hour after somebody took it
            # off line, with nothing anywhere able to notice. Health is earned
            # by evidence of the PROTOCOL — the same shape `parse_live_notes`
            # reads — so a floor that stops speaking it (a rollback, a proxy
            # that eats the body) closes the window again on the next push.
            self._live_delivering = speaks_live_notes(answer)
            if self._act_on_live_notes(parse_live_notes(answer)):
                self._live_followup = True
        except Exception:
            # Worker thread: see post_live. A push is never worth the poll.
            return

    def _probe_live_channel(self, machine: Optional[Machine]) -> None:
        """One knock at boot, so poll 1 can already use the floor road.

        THE ORDERING PROBLEM this exists to fix. `_live_channel_healthy()` is
        the gate on both the config road and the two refresh windows, and it
        requires `_live_delivering` — which only a push that came back speaking
        the note protocol can raise. That push happens at the very END of
        `_process_outcome`, after `_labcore_sync` has already decided what to
        read. So the first poll of a module's life fell back to LabCore for all
        six config reads even with a healthy floor one hop away, purely because
        nothing had had the chance to prove it yet.

        Per bench that is one poll's worth of reads. Floor-wide it is the
        failure mode: every module in the building starts at the same moment
        after a LabStation restart, and that is also the moment LabCore's
        queue is deepest, with every bench replaying its held results. This
        turns that stampede into one HTTP call per bench to a server on the
        same LAN that never touches LabCore at all.

        ZERO LabCore ops, and that is a hard requirement rather than a
        preference. It reads the CACHED `_live_url` and never calls
        `_live_config`, which would put a `lem_meta` read inside the thing
        whose whole purpose is to remove a `lem_meta` read. No cached address
        means no knock: a fresh install behaves exactly as it does today, and
        its own first push earns the channel for poll 2 as it always has.

        **The safety property is untouched.** Health is still EARNED, by
        `speaks_live_notes` on a real answer — the same one call `_push_live`
        and `parse_live_notes` use, so the three can never drift apart. A
        cached URL is an ADDRESS; it is not evidence that anything is listening
        on it, and least of all evidence that whatever is listening speaks the
        note protocol. An un-upgraded floor answers `/api/live` with a bodyless
        204, which arrives as `{}` — a push that landed, carrying no note and
        never able to carry one — and taking that for health is exactly the
        version-skew hole that suppressed the manual override read, the lever
        that takes a bench OFF LINE, for fifteen minutes against an old server.

        Notes are acted on but a follow-up poll is never asked for. The floor
        hands its notes to whichever push finds them, so dropping one here
        would spend it without the bench acting on it. Asking for a follow-up,
        though, is the one thing this must not do: `_ask_followup` exists
        because `_push_live` runs AFTER the reads and a note it receives is
        already late — while this runs BEFORE them, so everything the note
        invalidates is about to be read anyway. A follow-up here would double
        every bench's poll rate at the exact moment of a floor-wide restart.

        WORKER thread, and total. It touches no widget and no timer, and it
        swallows everything: a raise here travels up the worker, LabStation's
        `_run_in_thread` drops the callback, `_polling` is stranded True and
        the bench stops polling ALTOGETHER. `post_live` already swallows its
        own failures with a LIVE_TIMEOUT bound, but this must not DEPEND on
        that — the guarantee has to hold at the seam as well as inside it.
        """
        if self._live_probed or self._live_delivering:
            # Already knocked, or the channel is already proven and there is
            # nothing left to prove. Marked below on every road out, so an
            # absent floor gets one attempt and not one per poll.
            return
        self._live_probed = True
        if machine is None or not str(machine.uid or "").strip():
            # No binding yet — LabCore has not handed this module's config back.
            # `/api/live` refuses a push with no machine_uid, so there is
            # nothing to knock with.
            return
        if not self._live_url:
            return
        try:
            answer = post_live(self._live_url, self._live_token,
                               build_live_probe(machine))
            if answer is None:
                # A saved address that nothing answers on. Counted, so the
                # LIVE_RETRY_AFTER path starts walking towards the `lem_meta`
                # re-read that heals a moved server — the cache must never be
                # a place a bench can get permanently stuck.
                self._live_failures += 1
                return
            self._live_failures = 0
            self._live_delivering = speaks_live_notes(answer)
            self._act_on_live_notes(parse_live_notes(answer))
        except Exception:
            return

    def _pushed(self, payload: dict) -> dict:
        """Announce this poll on the live road, then hand the payload on."""
        self._push_live(payload)
        return payload

    def _process_outcome(self, machine: Machine, prints: List[str],
                         error: Optional[str], history_snapshot: List[dict],
                         now: Optional[datetime],
                         manual_rows: Optional[List[dict]] = None) -> dict:
        now = now or datetime.now()
        messages: List[str] = []
        # The source read these prints came from, if any — taken before the
        # store check below replaces the list. Consumed by `_commit_source`
        # after the journal append.
        source_pending = self._take_source_pending(prints)
        payload = {"machine": machine, "raw_prints": list(prints),
                   "rows": [], "now": now, "messages": messages,
                   "template_captured": False, "stored": False,
                   "identities": {}, "filed": [], "given_up": "",
                   "notice": self._held_notice}
        # Whatever the last poll filed is not what this one filed. The notice
        # is not reset with it: it describes readings that are still waiting,
        # and they do not stop waiting because a new poll started. `given_up`
        # is: it is news about one poll, not a state, and repeating it would
        # keep announcing a week-old decision every twelve seconds.
        self._last_storage = {"identities": {}, "filed": [], "stored": False,
                              "given_up": "",
                              "notice": self._held_notice}

        # BEFORE any read decision this poll makes — which is the whole of the
        # fix, so it comes first. One knock at the floor, on the worker,
        # costing zero LabCore ops, so that `_live_channel_healthy()` can
        # already be true when the corrections refresh below and the sync after
        # it choose where to read from. Placed after either of them it would
        # prove the channel for a poll that had already paid LabCore for
        # everything, which is the defect rather than the fix.
        #
        # (Deliberately not naming the sync step here: a test locates it by the
        # FIRST occurrence of its name in this function's source to prove the
        # probe precedes it, and a mention in a comment above it defeats the
        # check — the same trap the corrections block below documents.)
        #
        # Safe in every direction it can fail: no cached address, no floor, or
        # a floor too old to speak the note protocol all leave the flag exactly
        # as it was, and the reads below fall back to LabCore precisely as they
        # do today. See `_probe_live_channel`.
        self._probe_live_channel(machine)

        # Before anything is parsed: the factor applied to a measurement must be the
        # one in force when it was made. Moved here from the LabCore sync (which runs
        # after the parse). The read now sits behind a window
        # (CORRECTIONS_REFRESH_SECONDS), which changes how OFTEN LabCore is asked and
        # never where in the poll the answer is applied — the correction step below
        # still runs on every row of every poll, read or no read.
        #
        # (Deliberately not naming that step here: two tests locate it by the FIRST
        # occurrence of its name in this function's source to prove the read precedes
        # the parse, and a mention in a comment above it defeats the check.)
        #
        # Never `self._corrections_changed = self._refresh_corrections(...)`. On a
        # poll that skipped the read that assignment answers a question this poll did
        # not ask, and answers it False — overwriting a True the sync has not
        # consumed yet and silently losing the re-evaluation that re-judges the specs
        # against the new offsets. The flag is only ever RAISED here; the sync clears
        # it once it has acted on it.
        if machine is not None and self._corrections_due(now):
            if self._refresh_corrections(machine, now):
                self._corrections_changed = True
        # The calibration a QC verdict is stamped with has to be the one in
        # force when the reading was taken, and `_queue_run_events` runs below
        # this. Its own cache and its own window — see
        # `_refresh_calibration_epoch` — so this is not a read per poll, and it
        # is deliberately NOT gated on `_corrections_due`: the two roads answer
        # different questions and a bench can recalibrate without any factor
        # changing.
        if machine is not None:
            self._refresh_calibration_epoch(machine, now)

        # The bench journal. Whatever a previous process left owed is queued
        # again first (once per journal); then, on a poll that read something,
        # every print the journal already holds is dropped before anything
        # parses it — a re-read is not a new reading, and a replayed QC print
        # never renews freshness (§3.3, "store check before QC evaluation").
        journal = self._journal_for(machine) if machine is not None else None
        if journal is not None:
            self._journal_recover_once(machine, journal, messages)
            if not error:
                prints = self._journal_intake(journal, prints)
                payload["raw_prints"] = list(prints)
        # What the resolver has to say about this read (an `ambiguity` record),
        # kept only while a line it speaks of is still in this poll: a re-read
        # the journal already holds must not journal the decision twice.
        extra_records = []
        if source_pending is not None:
            live_pks = {getattr(t, "pk", None) for t in prints}
            extra_records = [r for r in source_pending[1].records
                             if not r.get("pks") or live_pks & set(r["pks"])]
        payload["journal"] = " ".join(
            part for part in (self._journal_status(machine),
                              self._source_notes(machine)) if part)

        if error:
            evaluation = MachineEvaluation(status=STATUS_UNKNOWN, reason=error)
            payload["evaluation"] = self._labcore_sync(
                machine, [], evaluation, now, messages, history_snapshot)
            # Still worth the sync: a bench that cannot read its folder may well
            # be holding readings from before it broke, and the LIMS may have
            # caught up with them in the meantime.
            payload.update(self._last_storage)
            # A bench that cannot read its folder is still a running module,
            # and that is exactly when the floor most needs to hear from it.
            return self._pushed(payload)

        # Capture flow: no mappings yet — hold the first print as the
        # template and wait for the operator to configure the parser. A typed
        # entry is never a template; there is no parser to configure.
        if prints and not machine.mappings and manual_rows is None:
            machine.template = prints[0]
            # Consumed, as it was before the journal: a template is not a
            # reading, and must not come back as one after a restart.
            if journal is not None:
                self._journal_poll(machine, journal, prints, [], [], now,
                                   messages, extra_records)
            self._commit_source(machine, prints, messages, source_pending)
            payload["template_captured"] = True
            payload["evaluation"] = MachineEvaluation(
                status=STATUS_UNKNOWN,
                reason="Print captured — click ⚙ to configure the parser.")
            messages.append(
                "Print captured and held as the mapping template.")
            return self._pushed(payload)

        rows = list(manual_rows or [])
        sources = [None] * len(rows)          # typed rows have no print
        for text in prints:
            result = parse_print(machine, text)
            if not result.lab_id and not result.values:
                continue
            row = result.to_row(now)
            if getattr(text, "origin", "live") != "live":
                row[ORIGIN_KEY] = text.origin
            rows.append(row)
            sources.append(text)
        # THE point at which corrections are applied — every measurement on every
        # print, before anything else sees it. Downstream (QC verdict, the result
        # written to LabCore, the history, the card, the CSV) all read the corrected
        # value, and the raw reading rides along on the row for the record.
        # ISO/IEC 17025:2017 §7.8.2 (the reported result must be the measurement
        # result) and §7.5.1 (records sufficient to reconstruct it).
        rows = apply_row_corrections(rows, machine.corrections)
        # (a) of §3.3: the readings go into the journal, fsync'd, before ANY
        # of them goes anywhere else. Only if that fails do they take today's
        # road with no copy kept at the bench — said, never silent.
        journaled = journal is not None and self._journal_poll(
            machine, journal, prints, rows, sources, now, messages,
            extra_records)
        # (b): only now is the read consumed — the cursor saved, the files
        # moved. A kill before this re-reads, and the journal's keys drop it.
        self._commit_source(machine, prints, messages, source_pending)
        payload["rows"] = rows
        combined = history_snapshot + rows
        if rows:
            if not journaled:
                self._queue_run_events(machine, rows, now)
            try:
                new_name = latest_result_filename(machine.title)
                # Machine renamed → remove the file written under the old
                # name so stale copies never linger. Only our own
                # lem_latest_* files are ever touched.
                if (machine.last_result_file
                        and machine.last_result_file != new_name
                        and machine.last_result_file.startswith(
                            LATEST_RESULT_PREFIX)):
                    try:
                        os.remove(os.path.join(labstation_dir(),
                                               machine.last_result_file))
                    except OSError:
                        pass
                write_latest_result(apply_csv_headers(rows[-1], machine),
                                    machine.title)
                machine.last_result_file = new_name
            except OSError as exc:
                messages.append(f"Latest-result file error: {exc}")

        evaluation = evaluate_machine(machine, combined, now)
        previous = self._evaluation
        if previous is None or previous.status != evaluation.status:
            self._log_event("status_change", detail={
                "from": previous.status if previous else "",
                "to": evaluation.status,
                "reason": evaluation.reason}, now=now)
        # Storage never depends on a widget. It used to: the worker predicted
        # whether a Results column watched one of these methods and, if one did,
        # emptied `sync_rows` and left the write to the grid's own push. So a
        # bench with no Results module on the canvas stored its readings and one
        # with the "wrong" module on the canvas did not, the Results road wrote
        # only the methods it happened to watch, and a grey or already-filled
        # cell dropped the reading while still reporting it delivered. The
        # Results module is a VIEW of LabCore, not a place a reading can live —
        # so the write always goes out from here, and the hand-off in
        # `_show_outcome` paints what was written and nothing more.
        payload["evaluation"] = self._labcore_sync(
            machine, rows, evaluation, now, messages, combined)
        payload.update(self._last_storage)
        return self._pushed(payload)

    # ── Main-thread half: history, Results hand-off, UI, signals ──────────

    def _show_outcome(self, payload: dict) -> None:
        machine = payload["machine"]
        now = payload["now"]
        rows = payload["rows"]
        for text in payload["raw_prints"]:
            self._recent_prints_raw.appendleft(text)
        if rows:
            self._history.extend(rows)
            for row in rows:
                self._recent_rows.appendleft(row)
            self._publish_rows(machine, rows)
            if not payload.get("stored"):
                # The worker never reached its storage step — the sync raised
                # before it. Take custody of the readings here rather than lose
                # them; the next poll files them. There is no direct write from
                # this thread any more: it could not ask LabCore which sample
                # they belong to, and a write that cannot ask that question is
                # the bug this road exists to end. The worker has finished with
                # its message list by now, so anything `_park` has to say about
                # what it could not keep still reaches the status line below.
                #
                # And the payload is filled in from that, because it was not
                # filled in by anybody: `filed` was still the empty list this
                # method was handed, so a sync that raised anywhere before the
                # results road parked the readings correctly and then painted
                # NOTHING and said nothing — the one path where the operator saw
                # a print arrive at the bench and no sign of it anywhere else.
                # `_parked_storage` is the same answer the two deliberate parked
                # branches give: paint provisionally under the printed ID, and
                # name the parked count on the status line.
                kept = self._park(rows, payload["messages"])
                payload.update(self._parked_storage(kept))
            self._refresh_data_table()
        # Painted from what was FILED, not from what was parsed. Those differ in
        # both directions: a reading held on an earlier poll is filed on this
        # one and belongs on the grid now, and a reading parsed on this one may
        # not be filed at all. A held reading is deliberately not painted — a
        # row invented for a sample the LIMS has never heard of reads as a
        # delivered result, and the value is on this module's own table and card
        # in front of the operator either way.
        #
        # `identities` None is passed THROUGH, not squashed to {}: it is how the
        # branches with no LabCore at all say "nobody could be asked who this
        # is", and the hand-off reads it as "show the printed ID" — which is
        # what the pristine code did on those branches and what the operator
        # standing at the bench needs to see. An empty map means the opposite:
        # LabCore was asked and placed nothing, so nothing is painted.
        filed = payload.get("filed") or []
        if filed and self._results_can_accept(filed):
            self._send_to_results(filed, payload.get("identities"))
        # A reading this poll STOPPED waiting for first — expiry, and every cap
        # that discarded one — then the ones it is still waiting for, then
        # whatever else happened. Everything before `messages[-1]` outranks it
        # because it is a running commentary that would otherwise bury them: the
        # sync appends "Recovered 2 QC result(s) from LabCore." after the results
        # road has run, and that is what the status line read on the poll that
        # discarded three hundred readings. These are the last words anybody will
        # ever hear about those readings.
        parts = [part for part in ([payload.get("given_up")]
                                   + self._take_losses()
                                   + [payload.get("journal"),
                                      payload.get("notice"),
                                      (payload["messages"] or [""])[-1]])
                 if part]
        if parts:
            self._status_label.setText(_loss_line(parts))
            # Nothing is condensed AWAY. The label is one line on a canvas and
            # the poll that loses the most readings is the poll with the most to
            # say, so the sentences that do not fit are on the tooltip, whole,
            # in the order they were said.
            self._status_label.setToolTip("\n".join(parts))
        self._finish_evaluation(machine, payload["evaluation"], now)
        # Last, because it starts another poll: everything this one has to paint
        # is on the canvas before anything else is dispatched.
        self._ask_followup()

    def _ask_followup(self) -> None:
        """Honour a note the worker acted on, by polling again AT ONCE.

        The latency problem this solves. `_push_live` runs at the very END of
        `_process_outcome`, AFTER `_labcore_sync` — so a note that arrives
        during poll N invalidates a value poll N has already read past, and left
        alone it would not be re-read until poll N+1. That makes an override
        take up to TWO poll intervals, sixty seconds at the default, where the
        ungated read it replaces took ONE. A saving that costs the lab thirty
        seconds on the read that takes a bench off line is not a saving.

        Main thread only. The worker may raise the flag and nothing else:
        widgets and timers belong to this thread, and a QTimer touched from a
        worker is undefined behaviour, not a race that shows up in a log.

        A follow-up may never ask for a follow-up of its own. The follow-up
        PUSHES too, so a floor that answers it with another note — because it
        re-sends, because somebody is still editing, because of a bug on a
        server built by another hand — would have this bench polling itself in a
        tight loop on the GUI thread, for ever. The note it receives is still
        ACTED on, so nothing is lost: the value stays invalidated and the next
        scheduled poll reads it, which is the same thirty seconds the old ungated
        read gave.
        """
        asked, self._live_followup = self._live_followup, False
        was_followup, self._in_live_followup = self._in_live_followup, False
        if not asked or was_followup:
            return
        self._in_live_followup = True
        QtCore.QTimer.singleShot(0, self._followup_poll)

    def _followup_poll(self) -> None:
        """The immediate re-poll `_ask_followup` scheduled."""
        self.poll_now()
        if not self._polling:
            # `poll_now` declined — no machine bound any more, or a poll is
            # already in flight — so no `_show_outcome` is coming to clear the
            # flag. Left set it would swallow the NEXT genuine note's follow-up,
            # which is a silent thirty-second regression on the read that takes
            # a bench off line. Clear it here instead.
            self._in_live_followup = False

    def _results_can_accept(self, rows: List[dict]) -> bool:
        """Is any Results module's column watching one of these methods?

        This used to decide where the reading was STORED, which is what made
        storage depend on the canvas. It now decides only whether there is
        anything to paint: nothing watching means nothing to show, and no reason
        to walk another module's widgets at all.
        """
        methods = set()
        for row in rows:
            for key in row:
                if key not in RESERVED_ROW_KEYS:
                    methods.add(key)
        for module in list(self.context.modules.values()):
            if getattr(module, "module_type", "") != "Results":
                continue
            for col in getattr(module, "_columns", None) or []:
                for test in col.get("tests", []):
                    if str(test).strip() in methods:
                        return True
        return False

    # ── Hand-off to LabStation's Results module — DISPLAY ONLY ────────────
    #
    # Each of a Results module's columns WATCHES a set of LabCore test methods,
    # and that mapping is the one thing worth borrowing: it says which column an
    # operator is reading a given method in. So we paint the number into that
    # column, on the row for the sample the reading was filed against, and stop.
    #
    # We used to ask the grid to STORE it as well — mark it dirty, start its
    # debounced push, let its write queue carry the value to LabCore. That is
    # the coupling this fix removes, and removing it is a subtraction: LEM no
    # longer touches `_grid_dirty`, `_auto_push_timer`, `_write_queue` or
    # `_lab_id_suffix`, and no longer depends on `_check_test_assignments`
    # running to give the reading an identity. It asks the grid one question —
    # which column shows this method — and answers the identity question itself,
    # against LabCore, before the value is written.
    #
    # Everything here is therefore free to be a UI judgement, because nothing is
    # at stake but a cell: a blacked-out cell stays grey, an entered value is
    # never overwritten, and a reading with no sample is not painted at all. The
    # record is already in LabCore.

    def _send_to_results(self, rows: List[dict],
                         identities: Optional[Dict[str, str]] = None) -> bool:
        """Show parsed rows on every Results module on the canvas. True if at
        least one painted something.

        `identities` maps the printed Lab ID to the sample the reading was filed
        under; rows missing from it were held and are not shown. None means the
        caller does not know where they were filed (LabCore was never asked), so
        the printed ID stands — which is also what it displayed before.
        """
        delivered = False
        for mod in list(self.context.modules.values()):
            if getattr(mod, "module_type", "") != "Results":
                continue
            try:
                if self._deliver_rows_to_results(mod, rows, identities):
                    delivered = True
            except Exception as exc:
                self._status_label.setText(f"Results hand-off error: {exc}")
        return delivered

    def _deliver_rows_to_results(self, results, rows: List[dict],
                                 identities: Optional[Dict[str, str]] = None
                                 ) -> bool:
        columns = getattr(results, "_columns", None)
        grids = (results._all_grids()
                 if hasattr(results, "_all_grids") else [])
        if not columns or not grids:
            return False
        # Detection map: LabCore test method → grid column that watches it
        # (col 0 is Lab ID, result columns start at 1).
        method_to_gcol = {}
        for i, col in enumerate(columns):
            for test in col.get("tests", []):
                method_to_gcol.setdefault(str(test).strip(), 1 + i)
        if not method_to_gcol:
            return False
        # Signals stay blocked for the whole hand-off, and staying blocked takes
        # more than blocking once. Writing a cell arms the grid's own debounced
        # push (_on_grid_item_changed, LabStation.pyw:11755), and this is
        # display — the reading has already been stored, under an identity a
        # grid row cannot know. Blocking here, and re-asserting it after every
        # call into the Results module (see `_fill_results_grids`), is what
        # makes that true; the state each grid arrived in is restored at the
        # end, because it is not ours to change.
        blocked = [(grid, grid.signalsBlocked()) for grid in grids]
        for grid, _was in blocked:
            grid.blockSignals(True)
        delivered = False
        try:
            for row in rows:
                printed = str(row.get(LAB_ID_KEY) or "").strip()
                if not printed:
                    continue
                # Shown under the identity it was FILED under, so what the
                # operator reads at the bench is what the lab reads on the
                # report. A printed ID absent from the map was held: not filed,
                # so not painted.
                lab_id = (printed if identities is None
                          else identities.get(printed, ""))
                if not lab_id:
                    continue
                col_values = {}
                method_values = {}
                for key, value in row.items():
                    if key in RESERVED_ROW_KEYS:
                        continue
                    if value in (None, ""):
                        continue
                    method = str(key).strip()
                    gcol = method_to_gcol.get(method)
                    if gcol is not None:
                        col_values[gcol] = str(value)
                        method_values[method] = str(value)
                if not col_values:
                    continue
                if self._fill_results_grids(results, grids, lab_id, col_values,
                                            provisional=identities is None):
                    delivered = True
                    if identities is not None:
                        self._remember_in_results(results, lab_id,
                                                  method_values)
            if delivered and hasattr(results, "_update_status_footer"):
                # No _grid_dirty and no auto-push: the value is already in
                # LabCore, under the identity LabCore itself named. A second
                # road writing the same number would cost a slot in a queue that
                # refuses past 100 pending — and would file it under whatever
                # Lab ID the row happens to carry, which for a row this hand-off
                # appended is the one the instrument printed. Inside the guard
                # with everything else, because it is another call into another
                # module and the restore below is what makes that safe.
                results._update_status_footer()
        finally:
            for grid, was in blocked:
                grid.blockSignals(was)
        return delivered

    @staticmethod
    def _remember_in_results(results, lab_id: str, method_values: dict) -> None:
        """Tell the Results module what LabCore now holds, not just what to draw.

        Reported from the floor 2026-08-14: a cell populates, and when the next
        print parses the previous one disappears. LabCore had every reading; only
        the screen lost them, and a restart cleared it.

        Painting a cell is not telling the Results module anything. Its grid is
        rebuilt from `test_index` — `_cell_for` (LabStation.pyw:11240) reads the
        value from there and blacks the cell out unless `test_exists` says the
        sample has that test — and `_refresh_grid` repaints from cache without
        re-fetching whenever the selection has not changed. Ordinarily a typed
        value reaches that cache through `_on_grid_item_changed`, which our
        hand-off deliberately silences: it also arms the debounced auto-push,
        the second write road this module stopped using. So the paint survived
        exactly until the next repaint.

        Worse, the bench triggered its own repaints. `update_cell` is in
        `_LIVE_REFRESH_OPS`, so every reading filed made the Results module
        re-read the write log and reload — and the previous reading vanished as
        the next one landed, which is precisely what the operators described.
        The pristine code hid this by setting `_grid_dirty`, which
        `_poll_live_changes` treats as "an edit is in progress, skip".

        So the value is recorded where the repaint will find it, which is the
        same bookkeeping the Results module does for itself when its own push
        succeeds (`_consume_batch_response`). This is one more pair of attributes
        this module knows about, and that is the wrong direction — but the
        alternative is painting a number that disappears, and a value LabCore has
        already accepted is exactly what its cache is supposed to say it holds.

        Only for readings that were FILED. A provisional paint is not in LabCore,
        so recording it as cached would make a repaint show a value the record
        does not have — a lie that outlives the outage that caused it.
        """
        index = getattr(results, "test_index", None)
        exists = getattr(results, "test_exists", None)
        if not isinstance(index, dict):
            return
        for test_name, value in method_values.items():
            try:
                index.setdefault(test_name, {})[lab_id] = value
                if isinstance(exists, dict):
                    exists.setdefault(test_name, set()).add(lab_id)
            except Exception:
                return      # a Results-like object that is not this shape

    @staticmethod
    def _fill_results_grids(results, grids, lab_id: str, col_values: dict,
                            provisional: bool = False) -> bool:
        """Paint one sample's readings: into its existing grid row, or a new row
        under the Additional tab like a CSV pull does.

        `lab_id` is normally the identity the reading was FILED under, so the row
        match is exact. It used to fall back to `_lab_id_suffix`, which is how a
        bare "34566" found a painted "081126-34566" row — and also how it could
        have found yesterday's 081026-34566. Resolving identity before the write
        makes the laundering unnecessary and the collision impossible.

        `provisional` is the one case where identity has NOT been resolved: no
        LabCore on the canvas, or LabCore unreachable, so the only name the
        reading has is the one the instrument printed. Two things change, and
        both exist to stop the operator being shown the same reading twice.

        It matches by suffix as well as exactly, so the printed "34566" paints
        into the LIMS's "081126-34566" row the analyst already has open — the
        cell the canonical poll would fill later, so the later poll finds it
        filled and leaves it, and there is one row.

        And it NEVER APPENDS. With no row to fill there is nothing to paint,
        because the row it would append carries the printed ID, and when LabCore
        comes back the reading is painted again under the canonical one: two rows
        for one reading, one of them a Lab ID the LIMS has never heard of, and
        over a long outage a hundred of them. The reading is not lost by staying
        off the grid — it is on this module's own table and card in front of the
        operator, and it goes on the grid properly on the poll that files it.

        Every refusal below is a UI judgement, and costs the reading nothing:
        LabCore already has it, or is about to.
        """
        suffix = lab_id.rpartition("-")[2].strip() if provisional else ""
        for grid in grids:
            for r in range(grid.rowCount()):
                item = grid.item(r, 0)
                if item is None:
                    continue
                painted = item.text().strip()
                if painted != lab_id and not (
                        suffix and (painted.rpartition("-")[2].strip()
                                    == suffix)):
                    continue
                for gcol, value in col_values.items():
                    if gcol >= grid.columnCount():
                        continue
                    cell = grid.item(r, gcol)
                    if (cell is not None and cell.data(
                            QtCore.Qt.ItemDataRole.UserRole) == "blackout"):
                        continue  # the work order says not this test
                    if cell is not None and cell.text().strip():
                        continue  # never clobber an entered result
                    if cell is None:
                        cell = QtWidgets.QTableWidgetItem()
                        grid.setItem(r, gcol, cell)
                    cell.setText(value)
                return True
        if provisional:
            return False
        if hasattr(results, "_append_lab_id_row"):
            results._append_lab_id_row(lab_id, results=col_values,
                                       mark_as=lab_id)
            # And block them again, because that call unblocked them. The
            # Results module's own append blocks the grid, paints, and then
            # unblocks unconditionally (LabStation.pyw:13069) — it restores to
            # False, not to the state it was handed. So from the row after the
            # first appended one, every cell painted below emits itemChanged,
            # which sets `_grid_dirty` and starts the debounced auto-push: the
            # second write road this whole change exists to close, re-armed by
            # the hand-off that closed it. One poll filing two prints of the
            # same sample is enough to do it.
            #
            # The fix is to re-assert our own block and nothing else. That file
            # is not ours to correct, and reaching in afterwards to clear
            # `_grid_dirty` or stop the timer would be more of this module
            # knowing another module's insides, which is the direction we are
            # supposed to be travelling away from.
            for grid in grids:
                grid.blockSignals(True)
            return True
        return False

    # ── The results road: a reading, and the sample it belongs to ─────────

    def _resolve_identities(self, printed_ids: List[str], read_sql,
                            dates: Optional[Dict[str, datetime]] = None,
                            standards=()) -> tuple:
        """(identities, ambiguous, unknown) — ask LabCore which samples these
        printed Lab IDs are.

        `dates` maps a printed Lab ID to when its reading was PARSED (this
        module's clock — see `closest_by_date`), and is only
        ever consulted for the collision the lab says cannot happen: several
        samples answering to one number, decided by whichever is nearest that
        date. See `closest_by_date`. `standards` is this bench's QC standard Lab
        IDs, which resolve under a narrower rule — see `sample_matches`.

        `unknown` is the set of IDs the question could not be asked about: a
        chunk that errored, a read that raised. Those are NOT missing samples
        and must not be treated as any kind of answer — the caller holds their
        readings and asks again next poll. The one error that IS an answer is a
        gateway with no `samples` table, which resolves each ID to itself (see
        `identity_of_last_resort`); there is nothing else it could mean.

        Chunked, so the size of one poll cannot make the question unaskable, and
        the caller has already applied the per-poll ceiling on how many chunks
        there are (`split_identity_backlog`). Worker thread, and the answer only
        ever addresses a write, so anything that goes wrong here is "we do not
        know", never a raise.

        AN ID THAT HAS ALREADY BEEN SETTLED IS NOT ASKED ABOUT AGAIN — see
        IDENTITY_CACHE_LIMIT for what "settled" excludes, which is more than the
        first cut of this cache thought.

        BE HONEST ABOUT WHAT THAT SAVES. A cup number is printed once and never
        again, so a bench working through new samples MISSES ON EVERY ONE and
        pays exactly the pristine cost: measured, sixty polls with a new cup each
        time issue sixty reads, before and after. What the cache removes is the
        REPEAT question — a QC standard, which prints on every single poll and is
        the one ID a bench asks about hundreds of times a day; a source file
        re-read from the top; a restart; a held reading whose ID also appears on
        a later print. That is a real saving on a real bench and it is not the
        headline somebody hoping for zero reads would write. The heartbeat road
        is where the per-bench multiplier actually was.

        Nothing else about this method changes: a cached ID takes the same
        road out as one answered this second.
        """
        identities: Dict[str, str] = {}
        ambiguous: Dict[str, List[str]] = {}
        unknown: set = set()
        standard_keys = {str(s or "").strip().lower() for s in standards}
        # Split before a single query is built, so an all-cached poll builds
        # none. The keys carry the standard flag because it changes the answer.
        asking: List[str] = []
        for printed in printed_ids:
            key = str(printed or "").strip()
            if not key:
                continue
            known = self._cached_identity(key, key.lower() in standard_keys)
            if known:
                identities[key] = known
            else:
                asking.append(key)
        for sql, params, chunk in build_sample_identity_queries(asking):
            try:
                result = read_sql(sql, params)
            except Exception:
                result = None
            verdict = identity_verdict(result)
            if verdict == "unknown":
                unknown.update(chunk)
                continue
            if verdict == "no samples":
                # Deliberately NOT cached. This is not an answer about the lab's
                # samples, it is the absence of a samples table, and a gateway
                # that grows one later must be believed the moment it does.
                identities.update(identity_of_last_resort(chunk))
                continue
            candidates = [str(row.get("lab_id") or "")
                          for row in (result.get("rows") or [])]
            found, unsure, certain = resolve_lab_ids_certain(
                chunk, candidates, dates, standards=standards)
            self._remember_identities(found, certain, standard_keys)
            identities.update(found)
            ambiguous.update(unsure)
        return identities, ambiguous, unknown

    def _cached_identity(self, printed: str, standard: bool,
                         now: Optional[datetime] = None) -> str:
        """The sample this printed Lab ID was already proved to be, or "".

        Least-recently-used, so the IDs a bench keeps printing — its QC
        standards above all — stay resolved however long it runs. An entry older
        than IDENTITY_CACHE_SECONDS is dropped rather than returned: the number
        cannot be reused, but the ROW can be voided, and a bench filing onto a
        sample the LIMS has deleted writes cells nobody can ever see.
        """
        key = (printed.lower(), standard)
        stamp = now or datetime.now()
        with self._results_lock:
            entry = self._identity_cache.pop(key, None)
            if not entry:
                return ""
            lab_id, seen = entry
            if (stamp - seen).total_seconds() >= IDENTITY_CACHE_SECONDS:
                return ""     # popped and not put back: re-ask, re-prove
            self._identity_cache[key] = (lab_id, seen)
        return lab_id

    def _remember_identities(self, found: Dict[str, str], certain: set,
                             standard_keys: set,
                             now: Optional[datetime] = None) -> None:
        """Remember the resolutions that can never change, oldest use first out.

        A miss after eviction re-asks the real question, so the bound can cost a
        read and can never cost a wrong answer — which is why nothing here
        remembers a FAILURE. A sample the LIMS has not logged in yet is the case
        the held queue exists for, and a remembered "no" would hold that reading
        for its full seven days without ever asking again.

        Nor does it remember an answer that is still OURS. A bare match with no
        dated twin is our own `insert_sample` phantom waiting for the LIMS to
        log the real record in, and `sample_matches` will hand back the dated
        one the moment it appears — so remembering the bare answer would pin the
        phantom for the life of the process and permanently blank the LIMS's
        cell. Only a dated record, or a standard (which displacement never
        touches), is a settled fact. See IDENTITY_CACHE_LIMIT.
        """
        stamp = now or datetime.now()
        with self._results_lock:
            for printed, lab_id in found.items():
                if printed not in certain or not lab_id:
                    continue
                standard = printed.lower() in standard_keys
                if not standard and sample_id_date(lab_id) is None:
                    continue          # provisional: our phantom may yet move
                self._identity_cache.pop((printed.lower(), standard), None)
                self._identity_cache[(printed.lower(), standard)] = (
                    lab_id, stamp)
            while len(self._identity_cache) > IDENTITY_CACHE_LIMIT:
                self._identity_cache.pop(next(iter(self._identity_cache)))

    def _store_results(self, machine: Machine, rows: List[dict], read_sql,
                       run_sql, write, messages: List[str],
                       now: datetime) -> dict:
        """Write this poll's readings to LabCore, under the identity LabCore
        itself confirms it holds. Returns what `_show_outcome` needs to know:

            {"identities": {printed Lab ID: the sample it was filed under},
             "filed":      the rows that went out this poll, held ones included,
             "notice":     one line about what is still waiting, or "",
             "given_up":   one line about readings this poll stopped waiting
                           for, or "",
             "stored":     True}

        `stored` is True whatever the outcome — written, held, deduplicated
        away, refused and queued for retry — because it means "this step ran".
        Only when it did not does the main thread have to cover for it.

        Worker thread: no widgets, everything reported through `messages`, and
        the enclosing sync's guard catches anything that still escapes.
        """
        # Taken without waiting. The other holder is either a poll worker inside
        # a LabCore round trip or the main thread on an operator action, and
        # neither is worth blocking behind: the rows go into the parked list and
        # the next poll — twelve seconds away — carries them. Waiting on the
        # main thread would freeze the window for a network round trip; not
        # locking at all is how one caller's stale snapshot silently replaces
        # readings the other had just taken custody of.
        if not self._storing.acquire(False):
            self._park(rows, messages)
            return {"identities": {}, "filed": [], "stored": True,
                    "notice": self._held_notice, "given_up": ""}
        try:
            return self._store_results_once(machine, rows, read_sql, run_sql,
                                            write, messages, now)
        finally:
            self._storing.release()

    def _store_results_once(self, machine: Machine, rows: List[dict], read_sql,
                            run_sql, write, messages: List[str],
                            now: datetime) -> dict:
        self._restore_held(machine, read_sql, now, messages)
        with self._results_lock:
            # The parked list is READ here, not emptied. Emptying it put every
            # parked reading on a local variable for the length of two network
            # round trips, and this whole step runs under a guard that swallows
            # a raise — so anything that went wrong in between deleted them
            # silently, while the held queue (not cleared until the commit)
            # survived. They come off the list at the commit, by identity, once
            # they are somewhere else; `_park` only ever appends, so identity is
            # stable for as long as it takes to get there.
            parked = list(self._parked_rows)
            # Oldest first, and the backlog ahead of anything new: the readings
            # this bench has been carrying longest are the ones an operator is
            # waiting on, and putting them first is what makes the per-poll
            # identity ceiling a queue that drains rather than a sieve that
            # re-asks about the same three hundred prints forever.
            untried = self._identity_backlog + parked + list(rows)
            waiting = self._held_rows + untried
            # WHICH ROWS HAVE NEVER BEEN ASKED ABOUT, by object identity, kept
            # for as long as `waiting` holds a reference to them. It has to be
            # the rows and not their Lab IDs: the freshness filter below is a
            # statement about a row's age, the ceiling is a statement about a
            # printed ID, and deriving the one from the other is what made a
            # deferred reading indistinguishable from an unplaceable one.
            untried_rows = {id(row) for row in untried}
            retry = list(self._retry_ops)
            # The journaled readings this step is about to decide on. Whatever
            # is not back in custody when it is done has been settled.
            journal_refs = {row.get(JOURNAL_KEY) for row in waiting
                            if isinstance(row, dict)} - {None}

        # Before anything is held: a reading that names no sample cannot be
        # filed by waiting. See "A print with no Lab ID names no sample".
        waiting, nameless = split_unidentified(waiting)
        if nameless and not self._idless_reported:
            # Once per module life, and not as an error. On many benches a
            # print with no Lab ID is a purge or standby report and this is
            # information; on a bench whose Lab ID mapping has stopped matching
            # the print layout it is the only warning anybody gets, and it says
            # where the reading did go.
            self._idless_reported = True
            messages.append(
                f"{len(nameless)} print(s) carried no Lab ID — there is no "
                "sample to file them against, so the machine log is the only "
                "record: " + self._log_home())

        waiting, expired = expire_held_rows(waiting, now)
        given_up = ""
        if expired:
            # Named, not counted: "1 reading(s)" tells an operator nothing they
            # can act on, and this is the last time anybody hears about it.
            for row in expired:
                self._log_event("held_expired",
                                lab_id=str(row.get(LAB_ID_KEY) or "").strip(),
                                detail=run_log_detail(row), now=now)
            # Carried on the payload like the hold notice, and for the same
            # reason only more sharply: `messages[-1]` wins the status line, and
            # "Recovered 2 QC result(s) from LabCore." appended further down the
            # same sync would bury the single sentence that says a week of
            # waiting has ended. Terminal news outranks routine news.
            given_up = (
                f"{len(expired)} reading(s) for "
                f"{', '.join(row_lab_ids(expired)[:3])} were never matched to "
                f"a sample in {HELD_ROW_MAX_AGE.days} days; "
                + self._log_home())

        # A standard's reading is a check, and a check is complete when its
        # verdict is recorded — which _queue_run_events already did. It is still
        # offered to `samples` below in case the lab keeps its standards there,
        # but it is never HELD waiting for one, and it is never resolved onto a
        # dated customer sample that happens to end in the standard's number
        # (see `sample_matches`). See "A standard is a check".
        standard_ids = qc_standard_ids(machine)
        results, _checks = split_qc_standards(waiting, standard_ids)

        # There is deliberately no early return for an empty poll. There used to
        # be one, and it took the commit at the bottom with it, so a queue that
        # had just been emptied — by expiry, or by every reading in it finally
        # filing — stayed in memory exactly as it was and was offered again
        # forever. Everything below is a no-op on empty input anyway.

        # WHAT THIS POLL ASKS ABOUT, in two parts that follow different rules.
        #
        # A row that has been asked before and refused a sample is asked again on
        # the freshness clock — every poll for its first hour, then on the sweep
        # — because the question costs a full scan and the answer for a reading
        # that has been waiting since Friday does not change in twelve seconds.
        #
        # A row that has NEVER been asked is exempt from that clock entirely, and
        # this is the correction: it used to go through the same filter, so a
        # deferred reading whose print was stamped more than an hour ago vanished
        # from `asking` on the next non-sweep poll, came out of the split as
        # "asked and unplaceable", and was shredded by the hundred-row cap. The
        # queue built to stop an archive import being shredded routed it into the
        # shredder. Nothing about a reading nobody has asked about gets staler
        # with age; there is simply a question outstanding.
        never_asked = [row for row in waiting if id(row) in untried_rows]
        asked_before = [row for row in waiting if id(row) not in untried_rows]
        stale_asking, sweep = identity_lookup_ids(asked_before, now,
                                                  self._held_swept_at)
        asking = stale_asking + [printed for printed in row_lab_ids(never_asked)
                                 if printed not in set(stale_asking)]

        # The per-poll ceiling, with the held queue ahead of the untried rows —
        # and the held queue always fits, because HELD_ROW_LIMIT is below the
        # ceiling. A bench working through a thousand-print archive can
        # therefore never starve the one late reading an operator is standing
        # there waiting for.
        #
        # Beyond the ceiling nothing is asked and nothing is
        # decided — see `split_identity_backlog` for why the round trips have to
        # be counted, and `_identity_backlog` for why the remainder is not the
        # held queue. `sweep` still records that the slow clock ran: the rows it
        # did not get to are at the FRONT of the next poll's queue, which is
        # sooner than the sweep would have come round again anyway.
        asking, deferred = split_identity_backlog(asking)
        deferred_ids = set(deferred)
        if sweep:
            self._held_swept_at = now
        # When each of these readings was PARSED — `_row_time`, this module's
        # clock, and not a date read off the print, which nothing on this road
        # extracts. See `closest_by_date` for why that is a good enough measure
        # of a collision the lab says cannot happen, and for the one case where
        # it is not.
        #
        # A printed ID carried by prints from MORE THAN ONE DAY gets no date at
        # all. Keeping the latest was a quiet wrong-sample write waiting to
        # happen: with two samples answering to one number, both readings would
        # file onto whichever is nearest the NEWER print, so the older print
        # lands on a sample it was not taken for. There is nothing to measure
        # from when the prints disagree, so nothing is measured, the tie stands,
        # and both readings are held — which is what a double data defect
        # deserves. It costs nothing in the ordinary case: a date is only ever
        # consulted when more than one sample answers to the number.
        print_days: Dict[str, set] = {}
        print_dates: Dict[str, datetime] = {}
        for row in waiting:
            printed = str(row.get(LAB_ID_KEY) or "").strip()
            if printed:
                when = _row_time(row, now)
                days = print_days.setdefault(printed, set())
                days.add((when.year, when.month, when.day))
                if printed not in print_dates or when > print_dates[printed]:
                    print_dates[printed] = when
        print_dates = {printed: when for printed, when in print_dates.items()
                       if len(print_days.get(printed) or ()) == 1}
        identities, ambiguous, unknown = self._resolve_identities(
            asking, read_sql, print_dates, standards=standard_ids)
        # A reading is in the backlog because its own ID was deferred, and the
        # deferred set is disjoint from the asked set — so a row is either
        # answered or untried, never both.
        backlog, askable = [], []
        for row in results:
            (backlog if str(row.get(LAB_ID_KEY) or "").strip() in deferred_ids
             else askable).append(row)
        still_held = [row for row in askable
                      if str(row.get(LAB_ID_KEY) or "").strip()
                      not in identities]
        filed = [row for row in waiting
                 if str(row.get(LAB_ID_KEY) or "").strip() in identities]
        ops = retry + build_result_cells(waiting, identities)

        # Said once, on the change: unlike a held reading, this is not a state
        # the operator can do anything about, and repeating it every twelve
        # seconds would bury the notice that is. Only when something was
        # actually asked — a poll that asked nothing has learned nothing, and
        # "LabCore is answering identity lookups again" would be a guess.
        if asking and bool(unknown) == self._identity_lookup_ok:
            self._identity_lookup_ok = not unknown
            messages.append(
                "LabCore cannot say what samples it holds — readings are being "
                "held until it can." if unknown else
                "LabCore is answering identity lookups again.")

        ops = self._unwritten(ops)
        # The mirror is written DOWN before the batch goes out, never after.
        # The two orders fail in opposite directions and only one of them is
        # survivable: stop here and the mirror is missing a reading that is
        # still held, which costs custody of something lem_machine_log already
        # records; stop the other way round and the mirror still names a reading
        # that HAS been filed, and every restart inside the seven-day window
        # files it again — over an analyst's correction, silently, on a real
        # sample. A bench may lose a copy of its own work. It may not revive a
        # value somebody has since replaced.
        self._persist_held(machine, run_sql, now, rows=still_held)
        error = None
        if ops:
            try:
                result = write("batch", {"operations": ops},
                               source="LEM Station")
            except Exception as exc:
                # A gateway that raises rather than returning an error dict used
                # to cost the whole retry queue, because the queue was emptied
                # before the write. Nothing is given up until the write comes
                # back, so a dropped connection costs a poll, not the readings.
                error = str(exc) or exc.__class__.__name__
            else:
                if isinstance(result, dict) and result.get("error"):
                    # LabCore answers a full queue with an error DICT, not an
                    # exception. The prints have already been consumed off the
                    # source file, so if these ops are dropped here nothing will
                    # ever re-offer them.
                    error = str(result["error"])

        with self._results_lock:
            # The untried backlog is set on BOTH paths and outside the held
            # queue, because a refused write says nothing about whether these
            # readings have a sample — nobody asked. They keep their place at
            # the front of the next poll either way.
            self._identity_backlog = backlog[-IDENTITY_BACKLOG_LIMIT:]
            # Said if it ever overflows, like every other cap on this road. It
            # takes a bench producing readings faster than three hundred a
            # poll for four hours, which is not a thing an instrument does — so if
            # anybody ever reads this line, the interesting news is that it
            # happened at all.
            if len(backlog) > IDENTITY_BACKLOG_LIMIT:
                lost = backlog[:len(backlog) - IDENTITY_BACKLOG_LIMIT]
                self._journal_note_dropped(lost)
                self._report_loss(
                    f"{len(lost)} reading(s) for "
                    f"{', '.join(row_lab_ids(lost)[:3])} were dropped before "
                    f"their sample could be looked up (limit "
                    f"{IDENTITY_BACKLOG_LIMIT}); " + self._log_home(),
                    messages)
            if error:
                self._retry_ops = ops[-RETRY_OP_LIMIT:]
                messages.append(f"LabCore write error: {error}")
                # The rows are held as well as queued, and that is not belt and
                # braces: `_retry_ops` is memory only, so a restart between the
                # refusal and the next poll would take the readings with it,
                # while the held queue is mirrored into LabCore. The retry queue
                # is kept for its ORDER — a re-run must land after the value it
                # supersedes — and the duplicate collapses in `_unwritten`,
                # which deduplicates on (sample, test, value) across the whole
                # batch. Nothing is given up until a write comes back clean.
                filed = []
                self._commit_held(askable, messages, taken=parked)
            else:
                self._retry_ops = []
                self._remember_written(ops)
                self._commit_held(still_held, messages, taken=parked)
        # And again afterwards, which is a no-op in the ordinary case because
        # the queue is exactly what was written above. It is not a no-op when
        # the write was refused and every reading came back into the queue, or
        # when another thread parked one while this write was in flight.
        self._persist_held(machine, run_sql, now)
        # Kept on the module, not just returned: the notice describes a state
        # that outlives the poll that discovered it, and the operator-action
        # path (`_reevaluate_and_show`) has no payload to read it off.
        self._held_notice = describe_held(self._held_rows, ambiguous, unknown,
                                          self._identity_backlog)
        self._journal_settle(journal_refs)
        return {"identities": identities, "filed": filed, "stored": True,
                "notice": self._held_notice, "given_up": given_up}

    def _log_home(self) -> str:
        """The tail of every give-up notice: where the reading actually is.

        The sentence "they stay in the machine log" is the entire justification
        for every cap on this road, and it is a claim about a DIFFERENT queue —
        one that can itself be refused. When `_drain_events` has been turned
        away by a busy LabCore the records are still at the bench and the
        promise is not yet true, so the notice says that instead of asserting
        something the operator can check and find false. Nothing is lost either
        way; the difference is whether the operator is told where to look.

        The queue is consulted as well as the flag, and that is the whole point.
        `_log_road_open` only remembers how the LAST drain went, and it starts
        True — so on the two branches that give up BEFORE the sync's try block
        (no `labcore_*` helpers injected, or `labcore_is_running()` False) it
        still says "open" although no drain has run and, on the first of those,
        none ever can. Those are exactly the branches `_park` reports from, so
        the flag alone would put the confident sentence on the one notice that
        is guaranteed false when it prints. A record still sitting in
        `_pending_events` has by definition not reached LabCore, whatever the
        last drain did, so that is what decides it.
        """
        if self._log_road_open and not self._pending_events:
            return "they stay in the machine log."
        return ("their machine-log records are queued at this bench and have "
                "NOT reached LabCore yet.")

    def _report_loss(self, text: str,
                     messages: Optional[List[str]] = None) -> None:
        """Say that readings have been given up on, on both channels.

        `messages` keeps the sentence in the poll's commentary, where the rest
        of the sync's news is; `_losses` is what the status line actually shows,
        because the commentary's last entry wins it and this sentence is
        routinely not the last. Worker thread — a deque append is all this does,
        and there are no widgets anywhere near it.
        """
        if messages is not None:
            messages.append(text)
        self._losses.append(text)

    def _take_losses(self) -> List[str]:
        """The loss notices nobody has shown yet. Main thread; drained rather
        than read so one poll's news is not repeated on the next."""
        out: List[str] = []
        while True:
            try:
                out.append(self._losses.popleft())
            except IndexError:
                return out

    def _park(self, rows: List[dict],
              messages: Optional[List[str]] = None) -> List[dict]:
        """Take custody of readings the results road could not deal with now —
        LabCore down, the road busy on the other thread, or the worker's storage
        step never reached at all.

        They join the held queue at the next commit rather than being written
        into it here, so a commit computed from an older snapshot cannot delete
        them. Why they are parked is not repeated here: whatever prevented the
        write has already said so, and the held notice will name them on the
        next poll.

        What IS said is what this drops. The parked list is bounded like the
        held queue, and the message the operator was reading while it filled —
        "LabCore not reachable — data kept locally." — stops being true at the
        hundred-and-first reading. Forty minutes of a busy multi-CSV bench is
        several hundred, so silence here reads as a promise the code is not
        keeping. There is no log event to go with it on purpose: the event queue
        drains to LabCore, and this happens precisely when LabCore is the thing
        that is gone, so the entry would only evict two others.

        RETURNS THE ROWS IT ACTUALLY KEPT, and the caller has to use them. The
        parked branches hand what they parked straight to `_parked_storage`,
        which paints it on the Results grid — and it used to hand over the whole
        list, including readings this method had just discarded. Painting a
        reading that has been dropped is precisely the "reported delivered while
        dropped" failure the rest of this change exists to remove; doing it in
        the code that removes it would be the worst version of it.
        """
        if not rows:
            return []
        with self._results_lock:
            kept = self._parked_rows + list(rows)
            dropped = kept[:max(0, len(kept) - HELD_ROW_LIMIT)]
            self._parked_rows = kept[-HELD_ROW_LIMIT:]
        if not dropped:
            return list(rows)
        self._journal_note_dropped(dropped)
        # `_log_home()` rather than the flat sentence, and this is the notice
        # that needed it most: `_park` is only ever reached because LabCore was
        # unreachable, so the drain has not run and the records are still here.
        self._report_loss(
            f"{len(dropped)} reading(s) for "
            f"{', '.join(row_lab_ids(dropped)[:3])} could not be kept "
            f"waiting (limit {HELD_ROW_LIMIT}); " + self._log_home(), messages)
        # By object identity, not by value: two prints of the same sample with
        # the same readings are equal dicts and separate readings, and the one
        # that survived must still be painted.
        gone = {id(row) for row in dropped}
        return [row for row in rows if id(row) not in gone]

    def _parked_storage(self, rows: List[dict]) -> dict:
        """What the storage step reports when there was no LabCore to store to.

        `rows` is what `_park` KEPT, never what it was offered — see there. This
        method paints, and painting a reading the cap has just thrown away tells
        the operator it was delivered when it was dropped.

        Two things it has to say that it used to say neither of.

        `filed` carries the rows and `identities` is None, so `_show_outcome`
        paints them on the Results grid under the Lab ID the instrument printed.
        Before the identity road these two branches called `_send_to_results`
        directly and the number appeared; afterwards nothing was painted at all,
        because painting was moved behind "what was filed" and on these branches
        nothing is ever filed. None is not the empty map: it means "we could not
        ask LabCore who this is", which is exactly true here, and it is what the
        hand-off already understood as "use the printed ID".

        That paint is PROVISIONAL and `_fill_results_grids` treats it as such —
        it fills a row the analyst already has open and appends none of its own,
        so the poll that files the reading properly cannot end up showing it a
        second time under a second Lab ID. See there for the whole argument.

        `notice` names the growing parked count. The status line otherwise read
        "Ready." — or nothing at all, on a canvas with no LabCore helpers — while
        readings piled up toward a silent HELD_ROW_LIMIT drop.
        """
        return {"identities": None, "filed": list(rows), "stored": True,
                "given_up": "",
                "notice": " · ".join(
                    [part for part in (self._held_notice,
                                       describe_parked(self._parked_rows))
                     if part])}

    def _commit_held(self, rows: List[dict], messages: List[str],
                     taken=()) -> None:
        """Take custody of the readings that are still unplaceable.

        Called with `_results_lock` held. `taken` is the parked rows the caller
        picked up before its round trips: they are in `rows` already, and this
        is the first moment it is safe to drop them from the parked list —
        before it, a raise in the middle of the storage step took them with it.
        Anything parked WHILE the write was in flight is still on that list and
        is folded in below, which is what makes the read-modify-write safe: the
        loser of a race adds rows, it never replaces them.

        The count cap drops the OLDEST first. A reading that has had every poll
        of the last week to resolve and has not is the weakest claim on the last
        slot, and the age cap is the principled statement of the same thing; the
        readings the operator is standing next to are at the other end. Nothing
        that can never resolve reaches this queue to distort that — a print with
        no Lab ID and a QC standard's reading are both taken out upstream.
        """
        if taken:
            self._parked_rows = [row for row in self._parked_rows
                                 if not any(row is one for one in taken)]
        rows = list(rows) + self._parked_rows
        self._parked_rows = []
        rows, dropped = cap_held_rows(rows)
        if dropped:
            self._journal_note_dropped(dropped)
            # Named for what it is. It used to say "dropped from the retry
            # queue", which is a different queue (`_retry_ops`, ops LabCore
            # refused) and sends whoever reads it looking for a write that was
            # never attempted: nothing in this queue has been refused by
            # anybody, it is waiting for a sample to be logged in.
            self._report_loss(
                f"{len(dropped)} reading(s) for "
                f"{', '.join(row_lab_ids(dropped)[:3])} stopped waiting for a "
                f"sample to be logged in (limit {HELD_ROW_LIMIT} readings); "
                + self._log_home(), messages)
            self._note_evicted(dropped)
        self._held_rows = rows

    def _note_evicted(self, rows: List[dict]) -> None:
        """Remember rows the COUNT cap threw out, until the next mirror write.

        `_persist_held` defers an addition and never defers a removal, and the
        reason is asymmetric: a reading that has been FILED must leave the
        mirror at once or the next restart restores it and files it again over
        an analyst's correction. A cap eviction is not that. The reading was
        never filed, so nothing can revive it — writing the mirror this instant
        buys the lab exactly nothing, and treating every eviction as urgent took
        the whole rate floor off for the one bench it exists to protect: a queue
        sitting at the cap evicts its oldest row on EVERY poll.

        Bounded, and cleared the moment a mirror write lands. Losing an entry
        only costs one early mirror write, never a reading.
        """
        for row in rows:
            self._held_evicted_keys[
                json.dumps(row, sort_keys=True, default=str)] = True
        while len(self._held_evicted_keys) > HELD_ROW_LIMIT * 2:
            self._held_evicted_keys.pop(next(iter(self._held_evicted_keys)))

    def _restore_held(self, machine: Machine, read_sql, now: datetime,
                      messages: List[str]) -> None:
        """Read this bench's held queue back from LabCore, once per module life.

        A reading that has been parsed, corrected and judged but not yet filed
        is real work, and before this it lived only in this object: LabStation
        restarting at shift change took it silently. Read once, because after
        that this module is the authority on its own queue.

        A read that fails is not an empty queue — the flag stays down and the
        next poll tries again. Nothing is mirrored before this has succeeded
        either (see `_persist_held`): writing the queue we happen to hold over
        the one we have not read yet would delete exactly the readings this
        exists to keep.
        """
        if self._held_restored or machine is None:
            return
        try:
            result = read_sql(HELD_QUERY, [machine.uid])
        except Exception:
            return
        if not isinstance(result, dict) or result.get("error"):
            return
        self._held_restored = True
        restored, readable = parse_held_payload(result.get("rows") or [])
        if not readable:
            # An unreadable row is not an empty queue, and saying nothing about
            # it was the quiet failure: the row sat there being re-read and
            # re-discarded on every restart, readings that were parked against
            # exactly this event were gone, and the only person who could have
            # noticed was never told. So it is said once, and the baselines are
            # left as "nothing agreed" — which makes the very next mirror write
            # overwrite the unreadable row with a queue that can be read.
            messages.append(
                "This bench's stored queue of unfiled readings could not be "
                "read and has been replaced; for anything it held, "
                + self._log_home())
            self._held_persisted = None
            self._held_persisted_keys = set()
            self._held_persisted_at = None
            return
        # What LabCore holds, taken from the read rather than assumed. This is
        # the baseline every later mirror write is compared against, and getting
        # it here is what makes the queue draining back to empty a CHANGE worth
        # writing: a process that restores a reading and files it must clear the
        # row it restored it from, or the next restart files it again.
        self._held_persisted = json.dumps(restored, sort_keys=True,
                                          default=str)
        self._held_persisted_keys = {
            json.dumps(row, sort_keys=True, default=str) for row in restored}
        if not restored:
            return
        with self._results_lock:
            known = {json.dumps(r, sort_keys=True, default=str)
                     for r in self._held_rows}
            self._held_rows = (self._held_rows + [
                r for r in restored
                if json.dumps(r, sort_keys=True, default=str) not in known
            ])[-HELD_ROW_LIMIT:]
        messages.append(
            f"{len(restored)} reading(s) still waiting for a sample were "
            "recovered from LabCore.")

    def _persist_held(self, machine: Machine, run_sql, now: datetime,
                      rows: Optional[List[dict]] = None) -> None:
        """Mirror the held queue into LabCore.

        `rows` is the queue to store — the one this poll is about to be left
        with, when the caller is writing the mirror down ahead of the batch, and
        the queue as it now stands otherwise.

        Never before `_restore_held` has succeeded: until the stored row has
        been READ, writing this module's own queue over it would delete a
        restart's worth of readings on the first poll of a fresh process, which
        is the opposite of the job.

        It is CAPPED here as well as in `_commit_held`, and that is not
        belt-and-braces: this is the one caller handed a queue that has not been
        committed yet — the still-held list, written down ahead of the batch —
        and it was handed it uncapped. One poll of a first-run multi-CSV bench
        therefore serialised thousands of rows into a single LabCore row,
        measured at 288,000 bytes, of which all but a hundred were discarded
        microseconds later by the commit. The mirror can never usefully hold
        more than the queue can.

        Two gates, and the difference between them is the whole safety argument.
        A snapshot that has not changed is not written at all. A snapshot that
        only ADDS rows may wait for HELD_PERSIST_SECONDS, because on the one
        bench this feature exists for the queue grows on every poll and an
        eleven-kilobyte row every twelve seconds is a real share of a write
        queue that refuses past 100 pending. A snapshot that REMOVES one is
        written immediately, always: a mirror still naming a reading that has
        been filed is a reading that gets filed again after the next restart,
        over whatever the cell holds by then.

        A row the COUNT CAP threw out is not that kind of removal and is
        excluded from the test — see `_note_evicted`.

        Failure is silent and simply leaves the stored copy stale — it is a
        safety net, and the next poll that changes anything tries again.
        """
        if machine is None or not callable(run_sql) or not self._held_restored:
            return
        with self._results_lock:
            rows = list(self._held_rows if rows is None else rows)
            rows, evicted = cap_held_rows(rows)
            if evicted:
                self._note_evicted(evicted)
            # Copied under the lock rather than iterated outside it: this runs
            # on the worker, and iterating a dict another thread is writing
            # raises — which on this thread strands `_polling` for good.
            evicted_keys = set(self._held_evicted_keys)
        snapshot = json.dumps(rows, sort_keys=True, default=str)
        if snapshot == self._held_persisted:
            return
        keys = {json.dumps(row, sort_keys=True, default=str) for row in rows}
        filed_away = self._held_persisted_keys - keys - evicted_keys
        if (not filed_away
                and self._held_persisted_at is not None
                and (now - self._held_persisted_at).total_seconds()
                < HELD_PERSIST_SECONDS):
            return
        try:
            sql, args = build_held_upsert(machine.uid, rows, now)
            result = run_sql(sql, args, source="LEM Station")
        except Exception:
            return
        if isinstance(result, dict) and result.get("error"):
            return
        self._held_persisted = snapshot
        self._held_persisted_keys = keys
        self._held_persisted_at = now
        # The mirror now agrees with the queue, so nothing that left it before
        # this write can matter again.
        self._held_evicted_keys.clear()

    def _unwritten(self, ops: List[dict]) -> List[dict]:
        """The ops that are not a repeat of a cell already stored.

        An unchanged reading offered again — a source file re-read from the top,
        a watch restarted — would re-stamp updated_at and the operator on a cell
        nobody touched, and cost a slot in a queue that refuses past 100 pending.
        Only `update_cell` is deduplicated; anything else is idempotent already.
        """
        seen, out = set(), []
        for op in ops:
            if op.get("operation") != "update_cell":
                out.append(op)
                continue
            key = result_cell_key(op)
            if key in seen or key in self._written_cells:
                continue
            seen.add(key)
            out.append(op)
        return out

    def _remember_written(self, ops: List[dict]) -> None:
        """Remember stored cells, oldest forgotten first.

        Bounded because a bench runs for months. Forgetting the far past only
        risks re-writing a reading the instrument offers again long after it was
        first stored, which is a wasted op and not a wrong one.
        """
        for op in ops:
            if op.get("operation") == "update_cell":
                self._written_cells[result_cell_key(op)] = True
        while len(self._written_cells) > WRITTEN_CELL_MEMORY:
            self._written_cells.pop(next(iter(self._written_cells)))

    # ── One declaration, one beat ─────────────────────────────────────────
    #
    # `CREATE TABLE IF NOT EXISTS` is harmless and invisible and costs a slot in
    # a queue that serialises everything in the lab, reads included (MEMORY:
    # labcore-write-queue-limits). The sync's tables were pulled behind one flag
    # for exactly that reason; three roads were missed.
    #
    #   • The PULSE ran the heartbeat DDL before EVERY beat — a second write per
    #     beat, forever, on every bench. That is the road Ryan reported as "the
    #     LEM heartbeats are bogging down the server", and at ten benches going
    #     to "a lot more" it is the multiplier that matters.
    #   • `_flush_events_worker` set the shared flag having declared only TWO of
    #     the seven tables, so a process whose first LabCore contact was an
    #     operator note — a comment, an override, a PM — left the sync believing
    #     lem_machine_heartbeat, lem_held_results, lem_machine_substatus,
    #     lem_machine_specs and lem_correction_factors were already there. On a
    #     fresh LabCore they were not, and the writes to them failed.
    #   • Neither of those two roads could declare anything at all before the
    #     first sync, and the pulse timer starts at construction: on a fresh
    #     LabCore the first beat can genuinely precede the first sync.
    #
    # So there is one method, it declares EVERY table this module writes to, and
    # every road goes through it. It is idempotent by construction (IF NOT
    # EXISTS), so the worst a race between two workers can do is declare twice
    # once, in the first seconds of a process.

    def _declare_due(self, now: datetime) -> bool:
        """May the declarations be attempted again yet?

        The twin of `_config_due` and `_corrections_due`, on the write side.
        True whenever nothing has been refused, which is every road through here
        on a LabCore that is behaving.

        A stamp in the FUTURE counts as due, for the reason spelled out in
        `_corrections_due`: these are naive local `datetime.now()` values on a
        bench PC, DST fall-back repeats an hour and NTP steps the clock back
        whenever it likes, and negative elapsed time is not "refused a moment
        ago" — it is arithmetic that has stopped meaning anything. Being wrong
        in this direction costs nine idempotent statements; being wrong the
        other way parks a bench's tables for the whole repeated hour, and the
        writes that need them fail one at a time for as long as it lasts.
        """
        last = self._declare_refused_at
        if last is None:
            return True
        elapsed = (now - last).total_seconds()
        return elapsed < 0 or elapsed >= self._declare_wait_seconds

    def _hold_off_declaring(self, now: datetime, refusal) -> None:
        """Note a refusal and decide how long to leave LabCore alone.

        LabCore's own `retry_after` wins when it sent one: it is the only party
        that knows how deep its queue is, and asking again sooner than it asked
        is the pattern notes.md's standing rule exists to stop. Where it said
        nothing, this module's doubling schedule is the fallback — five seconds
        to a minute, the same shape as `_retry_pending_bind`, because the reason
        the attempt failed is usually congestion and every bench in the lab is
        running this same loop.

        The schedule advances only on the fallback path. A `retry_after` is
        LabCore answering one question about one moment; letting it move the
        schedule would mean a single generous `retry_after` reset a wait that
        repeated refusals had earned.
        """
        wait = retry_after_seconds(refusal)
        if wait is None:
            wait = self._declare_retry_seconds
            self._declare_retry_seconds = min(
                self._declare_retry_seconds * 2, DECLARE_RETRY_MAX_SECONDS)
        self._declare_wait_seconds = wait
        self._declare_refused_at = now

    def _declare_tables(self, run_sql, now: Optional[datetime] = None) -> None:
        """Declare every table this module writes to, once per process.

        A REFUSED DECLARATION IS NOT A DECLARATION. The flag was set whenever
        nothing raised, but LabCore turns work away with an error dict — so on a
        fresh LabCore whose queue happens to be busy at boot (and the pulse
        timer starts at construction, which is the case this method exists to
        cover) all seven tables stayed undeclared while three roads believed
        they existed, for the life of the process. The flag goes up only if
        every declaration came back accepted.

        AND A REFUSAL IS NOT FREE TO REPEAT. That is the part that was missing.
        LabCore refuses because its queue is deep, and the next road through
        here re-fired the block immediately — every poll, on every bench, aimed
        squarely at the congestion being reported. A slow queue produced more
        work, which made it slower, multiplied by the bench count. So a refusal
        now buys a wait (see `_declare_due` / `_hold_off_declaring`) and the
        roads in between simply carry on; the block is still attempted, just not
        as fast as the polls arrive.

        The loop stops at the first refusal rather than firing the remaining
        statements into a queue that has this instant said it is full — which is
        what it already did, and what the backoff would otherwise undo six
        statements at a time.

        `now` is injected rather than read here, so the wait is testable at a
        bench's cadence the way `_config_due` and `_corrections_due` are; the
        one caller with no clock of its own passes nothing.
        """
        if self._labcore_table_ready:
            return
        now = now or datetime.now()
        if not self._declare_due(now):
            return
        # The indexes ride in the same block and under the same rule. They are
        # DDL with the same cost and the same failure mode — one queue op each,
        # refusable by a congested LabCore — so a refused index has to back off
        # exactly like a refused table rather than latch the flag over a
        # declaration that never landed. LOG_INDEX_DDL comes AFTER
        # LOG_TABLE_DDL because CREATE INDEX on a table that does not exist yet
        # is an error, and an error here parks the whole block for the process.
        #
        # Only the lem_machine_log indexes. `lem_maintenance` is read by this
        # module but CREATED by the web server, and indexing a table we do not
        # declare would fail on a fresh LabCore the server has not started
        # against — which, by the rule above, would leave this bench declaring
        # its tables forever. That index is declared where the table is.
        for ddl in (STATUS_TABLE_DDL, LOG_TABLE_DDL, HEARTBEAT_TABLE_DDL,
                    HELD_TABLE_DDL, SUBSTATUS_TABLE_DDL, EFFECTIVE_SPECS_DDL,
                    CORRECTIONS_DDL) + LOG_INDEX_DDL:
            answer = run_sql(ddl, source="LEM Station")
            if refusal_reason(answer):
                self._hold_off_declaring(now, answer)
                return
        for ddl in EFFECTIVE_SPECS_MIGRATIONS:
            try:
                run_sql(ddl, source="LEM Station")
            except Exception:
                pass          # column already present
        # Set LAST, so a run_sql that raises leaves the flag down and the next
        # road through here tries again rather than assuming a table exists. A
        # raise is deliberately NOT backed off: it is not LabCore asking for
        # room, it is this road failing, and every caller already runs inside
        # its own try.
        self._labcore_table_ready = True
        # Cleared on success, so a LabCore that congests, clears and congests
        # again is met with a five-second wait rather than the minute the last
        # bad patch had earned.
        self._declare_refused_at = None
        self._declare_retry_seconds = DECLARE_RETRY_SECONDS
        self._declare_wait_seconds = DECLARE_RETRY_SECONDS

    def _heartbeat_due(self, now: datetime) -> bool:
        """Has this bench gone HEARTBEAT_SECONDS without checking in?

        The one gate, consulted by both roads that beat. The pulse used to fire
        on its own fixed timer without asking, so a beat the poll had written
        seconds earlier did not suppress it and a bench emitted two.
        """
        last = self._last_heartbeat
        return (last is None
                or (now - last).total_seconds() >= HEARTBEAT_SECONDS)

    def _config_due(self, now: datetime) -> bool:
        """Is the bench's cached configuration old enough to re-ask for?

        True whenever nothing has been read yet, which covers both the first
        poll of a module's life and a newly bound machine — `set_machine` clears
        the stamp, because everything known was about a different instrument.

        The stamp is set only when LabCore actually ANSWERS. A refusal is not a
        configuration, and caching one would leave a bench running for the whole
        window on QC it never received. See `CONFIG_REFRESH_SECONDS`.

        A stamp in the FUTURE counts as due, for the same reason as
        `_corrections_due` — see the note there. The cost of being wrong is
        smaller on this read (a late QC spec, not a wrong number), but the
        arithmetic is broken in exactly the same way and a bench that goes an
        hour without re-reading its config after the clock steps back is not a
        behaviour anything relies on.
        """
        last = self._config_read_at
        if last is None:
            return True
        elapsed = (now - last).total_seconds()
        return elapsed < 0 or elapsed >= CONFIG_REFRESH_SECONDS

    def _corrections_due(self, now: datetime) -> bool:
        """Are the cached correction factors old enough to re-ask for?

        The twin of `_config_due`, on the read that until now happened on every
        single poll. True whenever nothing has been read yet, which covers the
        first poll of a module's life, a newly bound machine, and a correction
        this module itself just saved — `set_machine` and `_open_corrections`
        both clear the stamp.

        The stamp is set only when LabCore actually ANSWERS. A refusal is not a
        set of correction factors, and caching one would leave a bench correcting
        for the whole window with whatever it happened to be holding when the
        queue filled. See `CORRECTIONS_REFRESH_SECONDS`.

        A stamp that is in the FUTURE means the clock moved, not that the read
        is fresh. These are naive local `datetime.now()` values on a bench PC:
        the DST fall-back repeats an hour, and an NTP correction steps the clock
        back whenever it likes. `(now - last).total_seconds()` then goes
        NEGATIVE, which compares less than the window and skips the read — for
        the whole repeated hour, on the one read whose staleness changes the
        numbers this lab reports. Negative elapsed time is not "recently read",
        it is arithmetic that has stopped meaning anything, so it counts as due
        and the next read re-stamps it back into sanity.
        """
        last = self._corrections_read_at
        if last is None:
            return True
        elapsed = (now - last).total_seconds()
        # Which window depends on whether anything can shortcut it. With the
        # floor answering the push, an edit made in the web server arrives as a
        # note within one poll and this read is only catching a note that got
        # lost, so it can be long. With no floor to be told by, this read IS the
        # only way that edit ever reaches the bench, and it goes back to being
        # the window it was before the note existed.
        window = (CORRECTIONS_REFRESH_SECONDS if self._live_channel_healthy()
                  else CORRECTIONS_REFRESH_UNSIGNALLED_SECONDS)
        return elapsed < 0 or elapsed >= window

    def _override_due(self, now: datetime) -> bool:
        """Is the floor's manual override old enough to re-ask for?

        The twin of `_corrections_due` on the read that had no window at all,
        and the one place the conditional is enforced.

        The FIRST clause is the whole safety argument, so it comes first and
        returns before any arithmetic. This is the lever somebody on the floor
        pulls to take a bench OFF LINE. The live road is best-effort BY
        CONSTRUCTION — `live_url` may never have been published, the floor may
        be unreachable, and `post_live` swallows every failure — so a window
        that applied while the note channel was dead would leave a bench running
        for OVERRIDE_REFRESH_SECONDS after somebody switched it off, with
        nothing anywhere able to notice. Where the channel is not delivering
        this read goes straight back to every poll, exactly as it always was.

        The stamp is set only when LabCore actually ANSWERS. A refusal is not an
        override, and caching one would leave a bench running for the whole
        window on a lever it never read.

        A stamp in the FUTURE counts as due, for the reason spelled out in
        `_corrections_due`: these are naive local `datetime.now()` values on a
        bench PC, DST fall-back repeats an hour and NTP steps the clock back
        whenever it likes, and negative elapsed time is not "recently read" —
        it is arithmetic that has stopped meaning anything. Freezing THIS read
        for a repeated hour is the worst version of that bug in the module.
        """
        if not self._live_channel_healthy():
            return True
        last = self._override_read_at
        if last is None:
            return True
        elapsed = (now - last).total_seconds()
        return elapsed < 0 or elapsed >= OVERRIDE_REFRESH_SECONDS

    def _labcore_sync(self, machine: Machine, rows: List[dict],
                      evaluation: MachineEvaluation, now: datetime,
                      messages: List[str], history: List[dict],
                      store: bool = True) -> MachineEvaluation:
        """Push parsed rows + status to LabCore, pull QC specs and
        master-view overrides. Runs in the WORKER thread — never touch
        widgets here; report through `messages`. No-op when the labcore_*
        helpers aren't injected.

        `store` False leaves the results road alone. `_reevaluate_and_show`
        runs this synchronously ON THE MAIN THREAD for explicit operator
        actions, and the results road there would be an identity read plus a
        batch write of the whole held queue with the window frozen behind it —
        work the operator did not ask for, on the one thread that must not wait
        for a network. The poll twelve seconds later does it off-thread."""
        write = globals().get("labcore_write")
        run_sql = globals().get("labcore_sql")
        read_sql = globals().get("labcore_read_sql")
        if not (callable(write) and callable(run_sql) and callable(read_sql)):
            # No LabCore on this canvas at all. The readings are still real, so
            # they are kept rather than dropped, and `stored` says the decision
            # was made here so the main thread does not make it a second time.
            # Only when this call IS the storage step, though: `_last_storage`
            # is read by a poll worker between this returning and the payload
            # being assembled, and an operator action on the main thread has no
            # business telling that worker its rows were dealt with.
            kept = self._park(rows, messages)
            if store:
                self._last_storage = self._parked_storage(kept)
            return evaluation
        is_running = globals().get("labcore_is_running")
        if callable(is_running) and not is_running():
            messages.append("LabCore not reachable — data kept locally.")
            # "Locally" used to mean the history list and nothing else, so a
            # LabCore that was down for one poll cost every print in it. The
            # readings are parked instead and join the held queue on the poll
            # after it comes back. `stored` is True because that decision has
            # been made — there is nothing for the main thread to cover. The
            # parked list is bounded, and `_park` says so when it fills: the
            # message above stops being true at the hundred-and-first reading,
            # and the mirror that would otherwise hold them needs the LabCore
            # this branch exists because we cannot reach.
            kept = self._park(rows, messages)
            if store:
                self._last_storage = self._parked_storage(kept)
            return evaluation
        try:
            self._declare_tables(run_sql, now)

            # Prove the module is alive even when the bench is quiet. One gate,
            # shared with the pulse timer through `_last_heartbeat`, so however
            # many roads want to check in the bench emits at most one beat per
            # HEARTBEAT_SECONDS — see `_send_pulse`.
            if self._heartbeat_due(now):
                sql, args = build_heartbeat_upsert(machine, now, polling=True)
                # Only a beat LabCore ACCEPTED closes the window. Marking the
                # gate on a refusal made one busy moment cost the whole
                # HEARTBEAT_SECONDS on both roads at once — the pulse would then
                # find the beat "recent" and skip too — so the floor's failover
                # record went twice as long without an update as the pristine
                # code's worst case, on a bench that is running fine.
                if not refusal_reason(run_sql(sql, args, source="LEM Station")):
                    self._last_heartbeat = now

            needs_reevaluation = False

            # QC comes from LabCore in two layers, both optional:
            #   lem_qc_samples — shared standards; the parser DETECTS these
            #                    by Lab ID and runs its own QC.
            #   lem_qc_specs   — per-machine overrides for anything special.
            specs: List[TestSpec] = []
            got_qc_config = False
            # Only when the window has passed. Between times the bench runs on
            # the `machine.tests` / `machine.maintenance` it already holds,
            # which is exactly what these reads would have rebuilt — the reads
            # existed to notice a CHANGE, and a change made on the floor is
            # worth one read every two minutes, not four on every poll.
            config_due = self._config_due(now)
            override_due = self._override_due(now)
            # Every source that answered this poll. The stamp goes up only if
            # ALL of them did — a partial answer means the next poll asks again
            # rather than running two minutes on half a configuration.
            answered = []

            # THE PREFERRED SOURCE. One request to the floor's 12-second
            # snapshot answers all five reads below, where LabCore would charge
            # five slots in the queue it runs reads AND writes through — the
            # load that grows with the bench count and is what is crashing it.
            # See `_floor_config`, which returns LabCore-shaped result dicts so
            # every parser below runs unchanged, and None for every failure
            # there is. None simply leaves the LabCore reads exactly as they
            # were.
            #
            # Asked only when something is actually due: this road changes
            # where a read comes from, never how often it happens. And asked once, so
            # the four config sources and the override can never be assembled
            # from two different snapshots of the floor.
            floor = (self._floor_config(machine)
                     if (config_due or override_due) else None)

            samples_result = ((floor["qc_samples"] if floor
                               else read_sql(QC_SAMPLES_QUERY))
                              if config_due else {})
            if config_due and not samples_result.get("error"):
                answered.append(True)
                got_qc_config = True
                targets_result = (floor["targets"] if floor else
                                  read_sql(QC_TARGETS_QUERY, [machine.uid]))
                answered.append(not targets_result.get("error"))
                targets = [] if targets_result.get("error") else [
                    {"sample": r.get("sample_name"), "test": r.get("test_name")}
                    for r in targets_result.get("rows") or []]
                specs = specs_from_qc_samples(
                    machine, parse_qc_sample_rows(
                        samples_result.get("rows") or []), targets=targets)
            elif config_due:
                answered.append(False)

            specs_result = ((floor["qc_specs"] if floor
                             else read_sql(QC_SPECS_QUERY))
                            if config_due else {})
            if config_due:
                answered.append(not specs_result.get("error"))
            if config_due and not specs_result.get("error"):
                got_qc_config = True
                spec_rows = specs_result.get("rows") or []
                if machine.source_type == "manual":
                    # No mapping carries the QC sample here, so a row is the
                    # assignment itself — and only a row written for THIS
                    # machine is one. See machine_scoped_qc_rows.
                    spec_rows = machine_scoped_qc_rows(spec_rows, machine.uid)
                overrides = specs_for_machine(
                    machine, parse_qc_specs(spec_rows, machine.uid))
                by_name = {s.name: s for s in specs}
                for spec in overrides:      # per-machine specs win
                    by_name[spec.name] = spec
                specs = [by_name[name] for name in sorted(by_name)]

            if got_qc_config:
                carry_last_qc(specs, machine.tests)
                if ([s.to_dict() for s in specs]
                        != [t.to_dict() for t in machine.tests]):
                    machine.tests = specs
                    needs_reevaluation = True

            # The machine log FIRST, and this ordering is the durability claim
            # itself. Every cap on the results road below tells the operator the
            # reading "stays in the machine log"; drained afterwards, as it was,
            # the record was still sitting in a queue when the cap discarded it,
            # and a 'held_expired' event announcing the give-up went into the
            # same queue and could evict the very record it pointed at. Now the
            # record is in LabCore before anything can decide to stop waiting
            # for its sample, and everything below is a decision about a reading
            # that is already written down. See `_drain_events`.
            #
            # ONLY ON THE WORKER. `store=False` is the operator's own action
            # (`_reevaluate_and_show`) running synchronously on the GUI thread,
            # and it already skips the results road for exactly this reason.
            # Draining here would put the whole queue on the canvas thread on
            # one click of an override — on a bench that imported an archive
            # while LabCore was down, minutes of frozen window, where the
            # pristine code's worst case was two hundred records. Nothing about
            # that click is waiting on these records and the next poll takes
            # them.
            if store:
                self._journal_verify_owed(machine, read_sql, messages)
                self._drain_events(run_sql, messages)

            # The results road. Runs even with no new prints: it is also where
            # readings held for a sample that had not been logged in yet, and
            # ops LabCore's queue refused, get another chance.
            #
            # AFTER the QC specs are read, and that ordering is load-bearing.
            # `split_qc_standards` asks `machine.tests` which Lab IDs are this
            # bench's standards, and on the FIRST poll of a module life
            # `machine.tests` is whatever the setup dialog left there — nothing.
            # Run before the read, every QC standard's reading was classed as a
            # customer result and held waiting for a sample the lab was never
            # going to log in, and the operator was told so. On a `manual` bench
            # every row IS a QC reading, so every restart began by holding the
            # whole poll and saying the readings were unmatched.
            if store:
                self._last_storage = self._store_results(
                    machine, rows, read_sql, run_sql, write, messages, now)

            # And again, for what the results road logged on its way through —
            # 'held_expired'. Nothing is waiting on this one: it is news about a
            # decision whose subject the drain above already recorded.
            #
            # Skipped when the first drain was refused. LabCore has just said it
            # is full; offering it the same queue again in the same pass is a
            # second rejected round trip per bench per poll aimed at the very
            # congestion being reported — 50 a minute across ten benches — and
            # it contradicts the backoff the refusal path exists to honour.
            # These records go out on the next poll with everything else.
            if store and self._log_road_open:
                self._drain_events(run_sql, messages)

            # Read this machine's own QC verdicts back, so a LabStation restart
            # doesn't look like QC having never run.
            #
            # Tracked per test name rather than by one `_qc_hydrated` flag. The
            # flag latched on the first successful read, so a spec list that went
            # empty and came back — which is exactly what LabCore returning
            # nothing for one poll looks like — lost its remembered verdict for
            # good and the bench read YELLOW "assigned but not yet run".
            # Correction factors were re-read at the top of the poll, before the
            # parse — see _refresh_corrections. If they changed, the specs need
            # re-judging against the newly corrected readings.
            if self._corrections_changed:
                apply_corrections(machine, machine.corrections)
                needs_reevaluation = True
                self._corrections_changed = False

            # Anything we already know goes into memory, live verdicts included.
            for spec in machine.tests:
                if spec.last_qc_at:
                    self._qc_memory[spec.name] = {
                        "at": spec.last_qc_at, "value": spec.last_qc_value,
                        "in_spec": spec.last_qc_in_spec}

            # Seed that memory from LabCore once per test name. `_qc_tried` only
            # guards the READ — it must never gate re-applying what we remember,
            # which was the bug: a spec list that emptied and came back arrived
            # blank, the name was already "tried", and the bench read YELLOW
            # "assigned but not yet run" for QC that had passed hours earlier.
            pending = [s.name for s in machine.tests
                       if not s.last_qc_at and s.name not in self._qc_tried]
            if pending:
                sql, args = build_last_qc_query(machine.uid)
                past = read_sql(sql, args)
                if not past.get("error"):
                    self._qc_tried.update(pending)
                    self._qc_memory.update(last_qc_by_test(past.get("rows") or []))
                    recovered = [n for n in pending if n in self._qc_memory]
                    if recovered:
                        messages.append(
                            f"Recovered {len(recovered)} QC result(s) from LabCore.")

            # Re-apply every sync, from memory, with no read at all — but only
            # count it as a change when it actually changed something.
            if self._qc_memory and any(not s.last_qc_at for s in machine.tests):
                if apply_last_qc(machine, self._qc_memory):
                    needs_reevaluation = True

            # Publish what we are actually checking, so the floor can draw the
            # band instead of saying "No QC assigned" about a live instrument.
            fingerprint = effective_specs_fingerprint(machine)
            if fingerprint != self._published_specs:
                ok = run_sql is not None
                if ok:
                    for sql, args in build_effective_specs_publish(machine, now):
                        res = run_sql(sql, args)
                        # LabCore answers a full queue with an error DICT, not an
                        # exception. Treating that as success would leave the
                        # floor showing specs that never landed.
                        if isinstance(res, dict) and res.get("error"):
                            ok = False    # busy queue: retry on the next sync
                            break
                if ok:
                    self._published_specs = fingerprint

            maint = ((floor["maint"] if floor
                      else read_sql(MAINTENANCE_QUERY, [machine.uid]))
                     if config_due else {})
            if config_due:
                answered.append(not maint.get("error"))
            if config_due and not maint.get("error"):
                scheduled = parse_maint_rows(maint.get("rows") or [])
                if ([t.to_dict() for t in scheduled]
                        != [t.to_dict() for t in machine.maintenance]):
                    machine.maintenance = scheduled
                    needs_reevaluation = True

            # Stamped only on a complete answer — see `_config_due`.
            if config_due and answered and all(answered):
                self._config_read_at = now

            # In a window of its OWN, and only while something can shortcut it.
            #
            # This is the floor's lever for taking a bench off line, and it was
            # read on every single poll for exactly that reason: a bench that
            # keeps running for a refresh window after somebody overrides it is
            # the one delay nobody would accept. What changed is not that the
            # delay became acceptable — it is that the bench now gets TOLD. The
            # floor answers the live push with a note naming this read, and
            # `_push_live` drops the stamp the moment it hears one, so an
            # override still lands within one poll. The window behind it is a
            # backstop against a note that got lost.
            #
            # And `_override_due` refuses the window outright unless the note
            # channel is actually delivering — see the safety argument there.
            # One small query is cheap; two LabCore ops a minute per bench, on a
            # queue that runs at about 1.5 ops/sec and is falling over, is what
            # it costs across the floor.
            if override_due:
                # The floor carries the override as a bare string; it is
                # rebuilt into the `lem_machine_control` row shape by
                # `floor_config_results`, so `extract_overrides` below still
                # does the validating on both roads. An unrecognised value is
                # dropped by one piece of code, not two.
                #
                # `override_due` was computed at the TOP of this method, beside
                # `config_due`, so the two questions are asked before the floor
                # is, and one answer serves both.
                control = (floor["override"] if floor else
                           read_sql("SELECT machine_uid, manual_override "
                                    "FROM lem_machine_control"))
                if not control.get("error"):
                    # Stamped only on an ANSWER. A busy LabCore replies with an
                    # error DICT rather than raising, and stamping that would
                    # cache "nobody told me" as "nobody has overridden this".
                    self._override_read_at = now
                    overrides = extract_overrides(control.get("rows") or [])
                    wanted = overrides.get(machine.uid)
                    if wanted is not None and wanted != machine.manual_override:
                        machine.manual_override = wanted
                        needs_reevaluation = True

            if needs_reevaluation:
                evaluation = evaluate_machine(machine, history, now)

            # Write the status row ONLY when it actually changed — idle
            # sync ticks must not hammer LabCore's write queue.
            snapshot = (machine.uid, evaluation.status, evaluation.reason,
                        tuple(sorted((evaluation.sub_statuses or {}).items())))
            if snapshot != self._last_status_pushed:
                sql, args = build_status_upsert(machine, evaluation, now)
                refused = refusal_reason(run_sql(sql, args,
                                                 source="LEM Station"))
                sql, args = build_substatus_upsert(machine, evaluation, now)
                refused = refused or refusal_reason(
                    run_sql(sql, args, source="LEM Station"))
                # Only remember it as pushed if LabCore took it. A refusal is an
                # error DICT, not an exception, so recording the snapshot
                # regardless meant a refused status LATCHED: the next poll's
                # snapshot compares equal and skips, and the floor keeps showing
                # the last status LabCore actually accepted. Measured by the
                # critic — a bench going RED while the queue was backed up read
                # GREEN on the floor for eleven minutes of healthy polls
                # afterwards, and would have done so indefinitely. That is the
                # failover the web server treats as authoritative whenever the
                # live push is absent or a module has restarted. The spec
                # publish below and `_persist_held` already work this way; this
                # was the one write on the road that did not.
                if not refused:
                    self._last_status_pushed = snapshot
                else:
                    messages.append(
                        f"LabCore refused the status write ({refused}); "
                        "the floor still shows the previous status and this "
                        "retries on the next poll.")
        except Exception as exc:  # sync must never break local operation
            messages.append(f"LabCore sync error: {exc}")
        return evaluation

    # ── Machine-universe events (lem_machine_log) ─────────────────────────

    def _log_event(self, kind: str, lab_id: str = "", test_name: str = "",
                   value: str = "", detail: Optional[dict] = None,
                   now: Optional[datetime] = None) -> None:
        """Queue one record for lem_machine_log.

        The queue REFUSES a record when it is full rather than evicting one it
        has already accepted, and counts the refusal so `_drain_events` can say
        so. Dropping the oldest is the right answer everywhere else on this road
        — a reading that has waited a week has the weakest claim on the last
        slot — and it is the wrong answer here, because this queue is not a list
        of readings waiting for something to happen. It is the record itself,
        the one every other cap's "they stay in the machine log" points at, and
        a record already accepted must not be traded for a newer one. See
        LOG_EVENT_LIMIT for why the bound is not reachable by an ordinary poll
        in the first place.
        """
        if self._machine is None:
            return
        if len(self._pending_events) >= LOG_EVENT_LIMIT:
            self._events_dropped += 1
            return
        self._pending_events.append(build_log_insert(
            self._machine.uid, kind, now or datetime.now(),
            lab_id=lab_id, test_name=test_name, value=value, detail=detail))

    def _drain_events(self, run_sql, messages: Optional[List[str]] = None
                      ) -> None:
        """Write every queued record to lem_machine_log.

        Called TWICE per sync, and the FIRST call is the load-bearing one: the
        'run' and 'qc' records of everything this poll parsed go out before the
        results road, so a reading's record is in LabCore before any cap on that
        road can decide to stop waiting for its sample. Every drop notice this
        module prints ends "they stay in the machine log", and that is only true
        if the record went first. The second call carries what the results road
        itself logged — 'held_expired' — which is news about a decision whose
        subject is already recorded.

        A record popped off and lost to a raise is the same silent loss the
        bound exists to stop, arrived at the other way round, so a failed write
        puts its records back at the FRONT and lets the enclosing sync report
        the failure. The next poll drains again.

        AND A REFUSAL IS NOT A WRITE. This was the one write path in the file
        that checked only for an exception, and LabCore does not refuse by
        raising — it returns `{"error": ..., "busy": true}` and the loop counted
        it as filed. Measured on the real module against a gateway refusing the
        way LabCore refuses, a 3,000-print poll stored a hundred records,
        discarded two thousand nine hundred, reported `_events_dropped` 0 and
        told the operator they were in the machine log: the exact failure this
        queue was rebuilt to end, arrived at through the door nobody closed, and
        firing precisely when the queue is backing up — the condition Ryan
        reported. See `refusal_reason`.

        A refused batch is NOT a loss and is not reported as one: the records go
        back on the front and the next poll offers them again. What it is, is a
        reason to stop pushing — LabCore is telling us it is full, and the rest
        of the queue behind this batch would be refused too. So the drain gives
        up its turn, says the road is closed through `messages`, and the caps
        downstream stop claiming the machine log has the reading.

        Batched, at LOG_BATCH_ROWS records an op. One INSERT per record turned a
        3,000-print import into 3,000 serialised queue operations in front of
        every other bench in the lab — fourteen times the pristine module's cost
        for that poll — which both saturated the queue and guaranteed the
        refusal above. See `build_log_batch` and notes.md rule (c).

        Worker thread: nothing here touches a widget, and every notice goes out
        through `messages` / `_report_loss` like the rest of this road.
        """
        while self._pending_events:
            batch = []
            while self._pending_events and len(batch) < LOG_BATCH_ROWS:
                try:
                    batch.append(self._pending_events.popleft())
                except IndexError:
                    break
            if not batch:
                break
            sql, args = build_log_batch([args for _sql, args in batch])
            try:
                result = run_sql(sql, args, source="LEM Station")
            except Exception:
                self._pending_events.extendleft(reversed(batch))
                raise
            refused = refusal_reason(result)
            if not refused:
                self._journal_landed(batch)
            if refused:
                self._pending_events.extendleft(reversed(batch))
                already_closed = not self._log_road_open
                self._log_road_open = False
                # Through `_report_loss`, not a bare `messages.append`. This is
                # the sentence that says the durability every cap on this road
                # promises is not true right now, and `messages[-1]` is the only
                # entry the status line reads — while `_labcore_sync` appends
                # "Recovered N QC result(s)" after this runs. Said the plain way
                # it was reliably the second-to-last message and reached no
                # widget at all, on precisely the backed-up-queue poll it exists
                # for. Said once per sync, because both drains run in one pass
                # and the operator does not need to be told twice.
                if not already_closed:
                    self._report_loss(
                        f"LabCore refused the machine-log write ({refused}); "
                        f"{len(self._pending_events)} record(s) are still "
                        "queued at the bench and go out on the next poll.",
                        messages)
                return
        self._log_road_open = True
        dropped, self._events_dropped = self._events_dropped, 0
        if dropped:
            # The one loss on this road that nothing else covers, so it is said
            # in the same breath as the others rather than left to be inferred
            # from a reading that never appears anywhere.
            self._report_loss(
                f"{dropped} machine-log record(s) could not be queued (limit "
                f"{LOG_EVENT_LIMIT}) and are NOT in the machine log; those "
                "readings are on this module's own table at the bench only.",
                messages)

    def _queue_run_events(self, machine: Machine, rows: List[dict],
                          now: datetime) -> None:
        """One event per parsed print.

        A print whose Lab ID is a QC standard logs its 'qc' verdicts and NOT a
        'run': a standard is a check, not a sample somebody submitted, and
        logging both put the Cloud CRM in the history twice — once as a
        production run nobody ordered.

        If such a print yields no readable verdict it falls back to a 'run', so
        that a print can never disappear from the machine's history just
        because it looked like QC.

        Every 'qc' record is stamped with who ran it and which calibration was
        in force — resolved ONCE here, not once per verdict. The epoch is a
        LabCore read the top of the poll already paid for
        (`_refresh_calibration_epoch`); an archive import of three thousand
        prints must not be three thousand lookups. Unknown is None all the way
        down, never "" — see `qc_log_detail` for why that matters.

        getattr on the epoch, because `_queue_run_events` is reachable with
        nothing but `_log_event` and `_pending_events` and the tests hold it to
        that: the provenance may not come from state only a fully built module
        has.
        """
        operator = self._current_operator()
        calibration_id = getattr(self, "_calibration_epoch", None)
        for _row, kind, lab_id, test_name, value, detail in run_log_events(
                machine, rows, operator, calibration_id):
            self._log_event(kind, lab_id=lab_id, test_name=test_name,
                            value=value, detail=detail, now=now)

    def _flush_events_now(self) -> None:
        """Drain queued events outside a poll (comments, overrides, PM/Cal).
        The HTTP work runs off-thread; only the error report touches UI."""

        def done(error):
            if error:
                self._status_label.setText(error)

        _in_thread(self._flush_events_worker, done)

    def _flush_events_worker(self) -> Optional[str]:
        run_sql = globals().get("labcore_sql")
        if not callable(run_sql):
            return None
        is_running = globals().get("labcore_is_running")
        if callable(is_running) and not is_running():
            return None
        try:
            # The shared declaration, not a partial one. This used to declare
            # two tables and then set the flag the SYNC reads, so a process
            # whose first LabCore contact was an operator note left five tables
            # undeclared and believed otherwise. See `_declare_tables`.
            self._declare_tables(run_sql)
            self._drain_events(run_sql)
        except Exception as exc:
            return f"LabCore log error: {exc}"
        return None

    # ── Operator actions: notes, PM & Calibrations ────────────────────────

    def add_comment(self, note: str) -> None:
        """Log an operator note into the machine's universe."""
        note = note.strip()
        if not note or self._machine is None:
            return
        self._log_event("comment", detail={"note": note})
        self._flush_events_now()
        self._status_label.setText("Note saved.")

    def _on_add_note(self) -> None:
        if self._machine is None:
            QtWidgets.QMessageBox.information(
                self.dialog_parent(), "Note",
                "Set up the machine first (⚙ on the card).")
            return
        note, ok = QtWidgets.QInputDialog.getMultiLineText(
            self.dialog_parent(), "Operator note",
            "Anything strange happen? Log it against this machine:")
        if ok:
            self.add_comment(note)

    def add_task(self, name: str, kind: str, interval_days: int) -> None:
        import uuid
        if self._machine is None:
            return
        self._machine.maintenance.append(MaintTask(
            uid=uuid.uuid4().hex[:12], name=name, kind=kind,
            interval_days=max(1, int(interval_days))))
        self._reevaluate_and_show()

    def complete_task(self, uid: str, note: str = "",
                      when: Optional[date] = None) -> None:
        if self._machine is None:
            return
        for task in self._machine.maintenance:
            if task.uid != uid:
                continue
            done = (when or date.today()).isoformat()
            task.last_done = done
            task.note = note.strip()
            next_due = (date.fromisoformat(done)
                        + timedelta(days=max(1, task.interval_days)))
            kind = task.kind if task.kind in ("pm", "calibration") else "pm"
            self._log_event(kind,
                            detail={"task": task.name, "note": task.note,
                                    "completed": done,
                                    "next_due": next_due.isoformat()})
            if kind == "calibration":
                # A new epoch starts here, and the readings taken right after it
                # are the ones most likely to be stamped with the calibration it
                # replaced. Clearing the stamp costs one read on the next poll
                # and closes the window `_refresh_calibration_epoch` otherwise
                # leaves open for CONFIG_REFRESH_SECONDS.
                self._calibration_read_at = None
            self._flush_events_now()
            self._reevaluate_and_show()
            return

    def _rebuild_maint_menu(self) -> None:
        menu = self._maint_menu
        menu.clear()
        machine = self._machine
        if machine is None:
            menu.addAction("Set up the machine first").setEnabled(False)
            return
        today = date.today()
        for task in machine.maintenance:
            status, reason = maint_status(task, today)
            action = menu.addAction(
                f"✓ Mark done — {task.name} [{status}]",
                lambda t=task: self._on_complete_task(t))
            action.setToolTip(reason)
        if machine.maintenance:
            menu.addSeparator()
        menu.addAction("Add PM…", lambda: self._on_add_task("pm"))
        menu.addAction("Add Calibration…",
                       lambda: self._on_add_task("calibration"))

    def _on_complete_task(self, task: MaintTask) -> None:
        note, ok = QtWidgets.QInputDialog.getMultiLineText(
            self.dialog_parent(), f"Complete {task.name}",
            "Note (optional):")
        if ok:
            self.complete_task(task.uid, note=note)

    def _on_add_task(self, kind: str) -> None:
        label = "PM" if kind == "pm" else "Calibration"
        name, ok = QtWidgets.QInputDialog.getText(
            self.dialog_parent(), f"Add {label}", f"{label} name:")
        if not ok or not name.strip():
            return
        days, ok = QtWidgets.QInputDialog.getInt(
            self.dialog_parent(), f"Add {label}", "Repeat every (days):",
            30, 1, 3650)
        if ok:
            self.add_task(name.strip(), kind, days)

    def _reevaluate_and_show(self) -> None:
        if self._machine is None:
            return
        now = datetime.now()
        evaluation = evaluate_machine(self._machine, list(self._history), now)
        # Explicit operator action (override / PM completion) — the small
        # synchronous sync here is acceptable; polls stay off-thread. The
        # results road is left out of it (`store=False`): it is the one part
        # that reads and writes over the network in bulk, and this call is on
        # the GUI thread with the operator waiting on a dialog.
        messages: List[str] = []
        evaluation = self._labcore_sync(self._machine, [], evaluation, now,
                                        messages, list(self._history),
                                        store=False)
        # Through the same condenser as every other status write, rather than a
        # bare join. `_held_notice` on a bench holding readings for several IDs
        # is already a long sentence, and this was the one path that could
        # overflow the label with nothing carrying the remainder.
        parts = [part for part in
                 (self._held_notice, (messages or [""])[-1]) if part]
        line = _loss_line(parts)
        if line:
            self._status_label.setText(line)
            self._status_label.setToolTip(" · ".join(parts))
        self._finish_evaluation(self._machine, evaluation, now)

    def _publish_rows(self, machine: Machine, rows: List[dict]) -> None:
        for row in rows:
            self.context.connection_manager.emit(
                self.module_id, "row_parsed",
                {"machine": machine.title, "row": dict(row)})
            lab_id = str(row.get(LAB_ID_KEY) or "").strip()
            if not lab_id:
                continue
            for key, value in row.items():
                # RESERVED_ROW_KEYS: __raw__ and __corrections__ are the row's
                # own bookkeeping, not measurements. Publishing them here would
                # put a test method named "__raw__" on the result bus — and so
                # into LabCore — carrying a dict as its value.
                if key in RESERVED_ROW_KEYS:
                    continue
                if value in (None, ""):
                    continue
                self.context.add_result(lab_id, key, str(value), "LEM Station")

    def _finish_evaluation(self, machine: Machine,
                           evaluation: MachineEvaluation,
                           now: Optional[datetime] = None) -> None:
        previous = self._evaluation
        self._evaluation = evaluation
        if previous is None or previous.status != evaluation.status:
            self.context.connection_manager.emit(
                self.module_id, "status_changed",
                {"machine": machine.title, "status": evaluation.status,
                 "reason": evaluation.reason})
        # QC specs may have just arrived from LabCore — keep card sections
        # in step with them.
        spec_names = [t.name for t in machine.tests]
        if [r.test_name() for r in self._card.qc_rows()] != spec_names:
            self._card.set_machine(machine)
            # ...and the entry box, for the same reason: a spec assigned from
            # the master view needs a box before that reading can be entered.
            if machine.source_type == "manual":
                self._rebuild_manual_methods(machine)
        self._card.update_view(evaluation, now or datetime.now())

    # ── UI plumbing ───────────────────────────────────────────────────────

    def _toggle_data(self, checked: bool) -> None:
        self._data_table.setVisible(checked)
        self._data_toggle.setArrowType(
            QtCore.Qt.ArrowType.DownArrow if checked
            else QtCore.Qt.ArrowType.RightArrow)

    # ── Manual entry (source_type "manual") ───────────────────────────────

    def manual_bar(self) -> QtWidgets.QWidget:
        """The QC entry box shown in place of the Data drop-down."""
        return self._manual_bar

    def _apply_source_mode(self, machine: Optional[Machine]) -> None:
        """Swap the Data drop-down — and the parsed-print log under it — for
        the QC entry box, or back.

        There are no parsed prints on a manual bench, so neither the toggle nor
        the table has anything to show; the reading appears on the card, where
        its band is."""
        manual = machine is not None and machine.source_type == "manual"
        self._manual_bar.setVisible(manual)
        self._data_toggle.setVisible(not manual)
        if manual:
            self._rebuild_manual_methods(machine)
            self._data_table.setVisible(False)
        else:
            self._data_table.setVisible(self._data_toggle.isChecked())

    def _rebuild_manual_methods(self, machine: Machine) -> None:
        """One menu entry per ASSIGNED QC test, and nothing when there are none.

        Rebuilt whenever the spec list changes, because a bench is created
        before the master view assigns its QC — "the machine can be created and
        the QC assigned in LEM later, but it wont be able to put any data in
        until it detects the QC to compare against"."""
        specs = manual_entry_specs(machine)
        names = [s.name for s in specs]
        menu = self._manual_method_btn.menu()
        menu.clear()
        for spec in specs:
            menu.addAction(spec.name,
                           lambda n=spec.name: self._pick_manual_method(n))
        # Nothing assigned: inert, and saying why. Enterable QC is the only
        # thing this bench can record, so with none there is nothing to record.
        for widget in (self._manual_method_btn, self._manual_value,
                       self._manual_log_btn):
            widget.setEnabled(bool(specs))
        self._manual_note.setText(
            "" if specs else
            "No QC assigned — assign it in LEM before entering results.")
        # Keep a still-valid choice; one control means nothing to choose.
        if self._manual_method not in names:
            self._pick_manual_method(names[0] if len(names) == 1 else "")

    def _pick_manual_method(self, name: str) -> None:
        self._manual_method = name
        self._manual_method_btn.setText(name or "QC test")
        machine = self._machine
        spec = next((s for s in manual_entry_specs(machine)
                     if s.name == name), None) if machine else None
        # What it is checked against, so the operator sees the target without a
        # box for it — the standard is the assignment's, not theirs to pick.
        self._manual_value.setToolTip(
            f"{spec.sample_id}: {limits_text(spec)}" if spec else "")

    def _on_log_manual(self) -> None:
        if not self._manual_method:
            self._status_label.setText("Pick a QC test first.")
            return
        if self.log_manual_entry(self._manual_method,
                                 self._manual_value.text()):
            # A value left in the box is how the same reading gets logged twice.
            self._manual_value.clear()
            self._manual_value.setFocus()

    def _refresh_data_table(self) -> None:
        table = self._data_table
        entries = list(self._recent_rows)
        table.setRowCount(len(entries))
        for i, row in enumerate(entries):
            when = f"{row.get('parsed_date', '')} {row.get('parsed_time', '')}"
            # Lab ID stays — it is what the operator looks for. The correction
            # bookkeeping goes: it is the record's, not the bench's, and reads
            # as a stray column of Python dicts.
            summary = ", ".join(
                f"{k}={v}" for k, v in row.items()
                if k not in TIMESTAMP_KEYS
                and k not in (RAW_KEY, CORRECTION_KEY, JOURNAL_KEY))
            for col, text in enumerate((when, summary)):
                table.setItem(i, col, QtWidgets.QTableWidgetItem(text))

    def _open_settings(self, machine: Optional[Machine] = None) -> None:
        target = machine or self._machine
        if target is None:
            # Nothing bound yet: ask which instrument this module IS before
            # asking how to parse it.
            target = self._pick_machine()
            if target is None:
                return
        if _MachineDialog(target, self.dialog_parent(),
                          recent_prints=self.recent_prints(),
                          on_corrections=self._open_corrections).exec():
            self.set_machine(target)

    def _open_corrections(self, machine: Optional[Machine] = None) -> None:
        """Per-test correction factors, from the module's own settings.

        Writes the same `lem_correction_factors` rows the web server writes, so a
        bench tech and a supervisor are editing one number. Applied on the next
        poll like any other config change.
        """
        target = machine or self._machine
        if target is None:
            return
        dlg = _CorrectionsDialog(target, self.dialog_parent())
        if not dlg.exec():
            return
        changes = dlg.changes()
        if not changes:
            return
        run_sql = globals().get("labcore_sql")
        if not callable(run_sql):
            self._status_label.setText("LabCore unavailable — nothing saved.")
            return
        now = datetime.now()
        # §7.8.2 makes this offset part of every result the bench reports, and
        # `lem_correction_factors` is an UPSERT — saving one DESTROYS the
        # previous value, so `updated_by` is the byline on the row that replaced
        # it. It has been "" on every bench since this was written, because the
        # global it read is not a thing LabStation injects.
        who = self._current_operator()
        failed = []
        # Whatever is cached about the corrections is about to be wrong, so the
        # next poll re-reads rather than waiting out CORRECTIONS_REFRESH_SECONDS.
        # Dropped BEFORE the write, not after it, so every way out of what
        # follows is covered: a partial failure that landed some rows and a
        # run_sql that raises mid-loop both return early below, and both leave
        # LabCore holding factors this module has not seen. Being wrong in this
        # direction costs one read; being wrong in the other is the operator
        # standing at the bench watching their own correction not take effect.
        #
        # The generation goes with it, and for a reason the stamp cannot cover.
        # A poll worker may be inside `read_sql` right now, and LabCore still
        # held the OLD factor when that read started — so the rows it is about
        # to bring back are the PRE-EDIT ones. Applied, they revert the save the
        # operator just made and then stamp the window over the line above, and
        # the bench reports the old offset for two minutes while the status line
        # says the change was saved. Bumping makes that in-flight answer be
        # discarded instead.
        self._corrections_read_at = None
        self._corrections_epoch += 1
        try:
            run_sql(CORRECTIONS_DDL)
            for name, value in changes.items():
                spec = next((t for t in target.tests if t.name == name), None)
                units = spec.units if spec else ""
                if value:
                    sql, args = build_correction_upsert(
                        target.uid, name, value, units, now,
                        who or UNKNOWN_OPERATOR)
                else:
                    # Zero means no correction, so the row goes rather than
                    # lingering as a correction of nothing.
                    sql, args = build_correction_delete(target.uid, name)
                res = run_sql(sql, args)
                if isinstance(res, dict) and res.get("error"):
                    failed.append(name)
        except Exception as exc:
            self._status_label.setText(f"Correction not saved: {exc}")
            return
        if failed:
            self._status_label.setText(
                "LabCore was busy — not saved: " + ", ".join(failed))
            return
        # Merged onto the MAP, not rebuilt from the specs: the map covers every
        # method the bench reports and the specs only the QC-assigned few, so
        # rebuilding from them drops the offset on every customer method the
        # operator did not happen to touch — and reports it raw until a poll
        # manages to re-read LabCore.
        apply_corrections(target, {**(target.corrections or {}), **changes})
        # `who`, not the byline marker: the floor's search index harvests `by`
        # out of a log detail as a person (`lab_search._OPERATOR_KEYS`), so
        # "(unknown user)" here would put an analyst who does not exist on the
        # lab-wide operator list. Null is absent to that reader; a string is not.
        self._log_event("config", detail={"action": "correction factors set",
                                         "by": who, "changes": changes},
                        now=now)
        self._status_label.setText(
            f"Correction factor(s) saved: {', '.join(sorted(changes))}.")
        # On the worker, like every other operator action that logs something.
        # `_reevaluate_and_show` no longer drains — that ran the whole queue on
        # the canvas thread — so the record of who changed a correction factor
        # goes out here rather than waiting for the next poll.
        self._flush_events_now()
        self._reevaluate_and_show()

    def fetch_config_choices(self) -> List[dict]:
        """Registered machines and which are live. Blocking — call it off the
        UI thread, or from a dialog that has already told the user it is
        loading."""
        read_sql = globals().get("labcore_read_sql")
        if not callable(read_sql):
            return []
        try:
            # NB: labcore_read_sql is (sql, args=None, timeout=None) — it takes
            # NO source, unlike labcore_sql. Passing one raises TypeError, which
            # the except below would swallow into an empty picker.
            configs = read_sql(build_config_list_query(), None)
        except Exception:
            return []
        rows = (configs or {}).get("rows") or []
        beats = []
        try:
            res = read_sql(build_heartbeat_query(), None)
            beats = (res or {}).get("rows") or []
        except Exception:
            pass
        return config_choices(rows, live=live_uids(beats, datetime.now()))

    def _pick_machine(self) -> Optional[Machine]:
        """Adopt / duplicate / create. Returns an unsaved Machine, or None."""
        dialog = _MachinePickerDialog(self.fetch_config_choices(),
                                      self.dialog_parent())
        if not dialog.exec() or not dialog.outcome:
            return None
        kind, uid, title = dialog.outcome
        if kind == "new":
            return new_machine_config(title)
        source = self._pull_config(uid)
        rows = (source or {}).get("rows") or []
        if not rows:
            QtWidgets.QMessageBox.warning(
                self.dialog_parent(), "Configuration unavailable",
                "That machine's configuration could not be read from LabCore. "
                "Check the connection and try again.")
            return None
        try:
            machine = machine_from_config_payload(rows[0].get("config"), uid)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(
                self.dialog_parent(), "Configuration unreadable", str(exc))
            return None
        if kind == "duplicate":
            # New identity, and no runtime state: a copy that inherited the
            # source's file offset would skip its own prints.
            return duplicated_machine(machine, title)
        machine.title = machine.title or title
        return machine

    def _set_override(self, status: str) -> None:
        if self._machine is None:
            QtWidgets.QMessageBox.information(
                self.dialog_parent(), "Override",
                "Set up the machine first (⚙ on the card).")
            return
        comment = ""
        if status in (STATUS_SERVICE, STATUS_DEAD):
            comment, ok = QtWidgets.QInputDialog.getMultiLineText(
                self.dialog_parent(), f"Override to {status}",
                "A comment is required to override this machine — why?")
            comment = comment.strip()
            if not ok or not comment:
                self._status_label.setText(
                    "Override cancelled — a comment is mandatory.")
                return
        self._machine.manual_override = status
        self._machine.override_comment = comment
        self._log_event("override",
                        detail={"status": status or "cleared",
                                "comment": comment})
        self._flush_events_now()
        self._reevaluate_and_show()

    def _set_interval(self, seconds: int, label: str) -> None:
        self._poll_seconds = seconds
        self._interval_btn.setText(f"Every {label}")
        if self._timer.isActive():
            self._timer.start(seconds * 1000)

    # ── LabStation lifecycle ──────────────────────────────────────────────

    def on_finish_loading(self) -> None:
        self._timer.start(self._poll_seconds * 1000)
        self.poll_now()

    def serialize_state(self) -> dict:
        """Only the binding is local — which instrument this module IS.

        The configuration itself lives in LabCore (`lem_machine_config`), so a
        LabStation reinstall can't lose it and the floor can re-purpose it. The
        poll interval stays: that's a per-bench preference, not lab config.

        The floor's ADDRESS is the exception, and it is not configuration in
        that sense — it is what the bench needs in order to ask anybody
        anything. Without it a restarted module must spend a LabCore read on
        `lem_meta` just to find the server that would have answered its other
        six reads for free, and every module in the building restarts at the
        same moment: a LabStation restart after a power cut or a deploy, which
        is also when LabCore's queue is deepest. Caching it here is what makes
        that restart cost nothing. It stays CORRECTABLE — see `_live_config`,
        which re-reads `lem_meta` after LIVE_RETRY_AFTER consecutive failures,
        so a moved server or a rotated token still heals with nothing typed at
        the bench.

        Note where the token now lives. It was already readable by anything
        with LabCore access — it sits in `lem_meta` precisely so that benches
        can fetch it — and nothing pushed on this road is authoritative, so
        writing it into the canvas file does not change what it is worth to an
        attacker. But the canvas file IS a new place it is written, on the
        bench PC rather than in the database, and that is worth saying out loud
        rather than leaving for somebody to discover: rotating the token now
        means the old value lingers in saved canvases until each bench's next
        successful re-read.
        """
        return {
            "machine_uid": self._machine.uid if self._machine else "",
            "poll_seconds": self._poll_seconds,
            "live_url": self._live_url,
            "live_token": self._live_token,
        }

    def restore_state(self, state: dict) -> None:
        self._poll_seconds = int(state.get("poll_seconds", 30))
        # The floor's address, if this canvas was saved by a module that had
        # one. A canvas saved before this existed simply has no key here, and
        # everything below leaves `_live_url` empty — which is exactly today's
        # behaviour, right down to the `lem_meta` read on the first push.
        #
        # `rstrip("/")` because `parse_live_config` strips it on the way out of
        # LabCore, and a restored value that did not agree would build
        # `http://host:5557//api/bench/...`. That is not an error anybody sees:
        # the request 404s, `fetch_floor_config` swallows it, and the bench
        # quietly reads LabCore for ever.
        url = str(state.get("live_url") or "").strip().rstrip("/")
        if url:
            self._live_url = url
            self._live_token = str(state.get("live_token") or "")
            # And it counts as CHECKED, or the first push reads `lem_meta` to
            # confirm what was just restored — which is the queue slot this
            # whole thing exists to save; the cost is the read, not the string.
            # `_live_failures` stays at zero, so the LIVE_RETRY_AFTER path is
            # untouched: three consecutive failed pushes still send this module
            # back to `lem_meta` and a moved server still heals itself.
            self._live_checked = True
        uid = str(state.get("machine_uid") or "").strip()
        if uid:
            self._adopt_config(uid)
            return
        legacy = state.get("machine")
        if legacy:
            # A canvas saved before configs moved to LabCore. Don't strand the
            # bench: adopt what's here, then publish it so LabCore owns it.
            machine = Machine.from_dict(legacy)
            if machine.uid:
                self.set_machine(machine)

    def _pull_config(self, uid: str):
        """Read one machine's stored configuration. Returns the raw result so
        the caller can tell "gone" from "couldn't ask"."""
        read_sql = globals().get("labcore_read_sql")
        if not callable(read_sql):
            return None
        sql, args = build_config_fetch(uid)
        try:
            return read_sql(sql, args)
        except Exception:
            return None

    def _adopt_config(self, uid: str) -> None:
        """Bind to a stored config and load it."""
        if not self._apply_pulled_config(uid, self._pull_config(uid)):
            self._schedule_bind_retry()

    def _apply_pulled_config(self, uid: str, result) -> bool:
        """Bind from one answer to `build_config_fetch`. True if it bound.

        False means "not yet", never "give up" — the caller schedules the next
        attempt. Splitting this out of `_adopt_config` is what lets the retry
        below reuse the same reasoning instead of a second copy of it.
        """
        rows = (result or {}).get("rows") or []
        if not rows:
            # Either it's gone or LabCore is unreachable, and this cannot tell
            # which — see `config_was_deleted`, which can, and which the pulse
            # uses to clear a module whose config really was deleted. So the uid
            # is kept and asked for again. It used to be kept and never asked
            # for again, which is the whole bug: the binding was not lost, it
            # was parked, and nothing ever came back for it.
            self._status_label.setText(
                "Waiting for this machine's configuration from LabCore…")
            self._pending_uid = uid
            return False
        row = rows[0]
        try:
            machine = machine_from_config_payload(row.get("config"), uid)
        except ValueError as exc:
            self._status_label.setText(f"Stored configuration unreadable: {exc}")
            return False
        machine.title = machine.title or str(row.get("title") or "")
        self._pending_uid = ""
        self._bind_retry_seconds = BIND_RETRY_SECONDS
        self.set_machine(machine, publish=False)
        return True

    def _schedule_bind_retry(self) -> None:
        """Arm the next attempt at a binding LabCore could not hand over."""
        if not self._pending_uid or self._machine is not None:
            return
        self._bind_retry_timer.start(int(self._bind_retry_seconds * 1000))

    def _stop_bind_retry(self) -> None:
        self._pending_uid = ""
        self._bind_retry_seconds = BIND_RETRY_SECONDS
        self._bind_retry_timer.stop()

    def _retry_pending_bind(self) -> None:
        """Ask again for a parked binding, off the GUI thread.

        The read is a network round trip through a queue that is congested often
        enough for this to be needed at all, so it does not run on the canvas
        thread — a bench retrying every few seconds must not stutter LabStation
        while it does. The worker returns the raw answer and never raises; the
        binding itself happens on the main thread, because `set_machine` builds
        widgets.
        """
        uid = self._pending_uid
        if not uid or self._machine is not None:
            return

        def work():
            try:
                return self._pull_config(uid)
            except Exception:
                return None       # LabCore still down: not worth a stack trace

        def done(result):
            # The operator may have picked an instrument while this was in
            # flight; binding now would swap it underneath them.
            if self._pending_uid != uid or self._machine is not None:
                return
            if not self._apply_pulled_config(uid, result):
                self._bind_retry_seconds = min(self._bind_retry_seconds * 2,
                                               BIND_RETRY_MAX_SECONDS)
                self._schedule_bind_retry()

        _in_thread(work, done)

    def _publish_config(self, machine: Machine) -> None:
        """Push this machine's configuration up. Worker-side, never raises."""
        if machine is None or not machine.uid or not (machine.title or "").strip():
            return
        snapshot = Machine.from_dict(machine.to_dict())
        now = datetime.now()
        # Off the context BEFORE the worker starts: `work` runs on another
        # thread and the identity belongs to the module, not to that thread.
        # Same byline rule as the correction audit — a config nobody can be
        # named for says so, rather than showing an empty author.
        user = self._current_operator() or UNKNOWN_OPERATOR

        def work():
            run_sql = globals().get("labcore_sql")
            if not callable(run_sql):
                return None
            try:
                run_sql(CONFIG_TABLE_DDL, source="LEM Station")
                sql, args = build_config_upsert(snapshot, now, by=user)
                run_sql(sql, args, source="LEM Station")
            except Exception:
                return None       # LabCore down: the bench still runs
            return True

        _in_thread(work, lambda _ok: None)

    def _check_config_still_exists(self) -> None:
        """LabCore owns the configuration, so a config deleted from the floor
        means this module has none: clear it and stop.

        Only a definitive empty answer counts. Treating an outage as a deletion
        would wipe every bench in the lab at once — see config_was_deleted().
        """
        machine = self._machine
        if machine is None or not machine.uid:
            return
        if not config_was_deleted(self._pull_config(machine.uid)):
            return
        title = machine.title or machine.uid
        self._timer.stop()
        self._drain_timer.stop()
        self._close_serial()
        self._polling = False
        self._machine = None
        self._evaluation = None
        self._status_label.setText(
            f"“{title}” was removed from LabCore — this module has no "
            f"configuration. Use ⚙ to pick or create a machine.")
        self._refresh_card()

    def _send_pulse(self, now: Optional[datetime] = None) -> None:
        """Check in with LabCore whether or not we are watching.

        Runs entirely in the worker: the pulse must never block the canvas,
        and — like every worker here — must never raise, or LabStation's
        _run_in_thread drops the callback.

        TWO THINGS IT NO LONGER SPENDS, both of them multiplied by every bench
        in the lab and by every bench Ryan is about to add.

        It does not re-declare lem_machine_heartbeat before each beat. That was
        a second write per beat forever, and the one-time block the sync uses
        exists precisely to stop this pattern; the pulse path was missed. It
        cannot simply be deleted, because this timer starts at construction and
        can fire before `_labcore_sync` has ever run — so the pulse declares
        through the same one-time `_declare_tables` the sync does, which means
        the table is there on the first beat of a fresh LabCore and declared
        once per process rather than once per beat.

        And it consults `_last_heartbeat` like the sync does. The sync writes a
        beat only when HEARTBEAT_SECONDS have elapsed; this fired on a fixed
        timer regardless, so a beat the poll had written seconds earlier did not
        suppress it and the bench emitted two. One gate now (`_heartbeat_due`),
        so however many roads want to check in, a bench beats at most once per
        HEARTBEAT_SECONDS.

        The gate is read here on the main thread and `_last_heartbeat` is set in
        `done` on success, exactly as the sync does it — so a beat that fails is
        retried on the next tick instead of leaving the floor to guess. Two
        roads passing the gate in the same instant would write the same upsert
        twice, which the floor cannot tell from one; the beat that matters is
        the one that is missing, never the one that is doubled.

        `now` is injectable so the rate limit can be tested at a bench's cadence
        rather than in real time; the timer calls it with nothing.
        """
        machine = self._machine
        if machine is None or not machine.uid:
            return
        polling = self._polling
        now = now or datetime.now()
        if not self._heartbeat_due(now):
            # Somebody has already checked in for this bench inside the window.
            # Still worth the tick for the config check below, which costs a
            # read of state this module already holds.
            self._check_config_still_exists()
            return

        def work():
            run_sql = globals().get("labcore_sql")
            if not callable(run_sql):
                return None
            is_running = globals().get("labcore_is_running")
            if callable(is_running) and not is_running():
                return None
            try:
                self._declare_tables(run_sql, now)
                sql, args = build_heartbeat_upsert(machine, now,
                                                   polling=polling)
                if refusal_reason(run_sql(sql, args, source="LEM Station")):
                    # Refused, not sent. Returning `now` here would close the
                    # gate for both roads and leave the floor's dot to go stale
                    # for a full window over one busy instant; returning None
                    # retries on the next tick, which is what the docstring
                    # above promises and what only the raise path delivered.
                    return None
                return now
            except Exception:
                return None      # a missed beat is not worth a stack trace

        def done(sent):
            if sent is not None:
                self._last_heartbeat = sent
            # Same tick, main thread: has the floor removed this config?
            self._check_config_still_exists()

        _in_thread(work, done)

    def shutdown(self) -> None:
        self._timer.stop()
        self._drain_timer.stop()
        self._pulse_timer.stop()
        self._bind_retry_timer.stop()
        self._close_serial()
        self._release_journals()


def _shrink_font(widget: QtWidgets.QWidget, factor: float,
                 bold: bool = False) -> None:
    """Scale a widget's inherited font — sizes derive from the theme's own
    base font instead of hard-coded pixel values."""
    font = widget.font()
    size = font.pointSizeF()
    if size <= 0:
        size = 9.0
    font.setPointSizeF(size * factor)
    font.setBold(bold)
    widget.setFont(font)


class _BatteryBar(QtWidgets.QWidget):
    """Battery-style QC freshness gauge (the widget reference's battery).
    Outline comes from the palette so it reads on any LabStation theme."""

    def __init__(self) -> None:
        super().__init__()
        self._fraction = 0.0
        self._color = STATUS_COLORS[STATUS_UNKNOWN]
        self.setFixedSize(46, 20)

    def set_fraction(self, fraction: float, color: str) -> None:
        self._fraction = max(0.0, min(1.0, fraction))
        self._color = color
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        from PySide6 import QtGui
        outline = self.palette().color(
            QtGui.QPalette.ColorRole.WindowText)
        outline.setAlpha(110)
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        body = QtCore.QRectF(1, 1, 40, 18)
        p.setPen(QtGui.QPen(outline, 1.5))
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(body, 5, 5)
        # terminal nub
        p.setBrush(outline)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.drawRoundedRect(QtCore.QRectF(42.5, 6.5, 3, 7), 1.5, 1.5)
        if self._fraction > 0:
            fill_w = max(3.0, 34.0 * self._fraction)
            p.setBrush(QtGui.QColor(self._color))
            p.drawRoundedRect(QtCore.QRectF(4, 4, fill_w, 12), 3, 3)
        p.end()


class _QCRow(QtWidgets.QWidget):
    """One QC section on the card: battery + ⚡ value + LabCore method.
    Only the semantic status colors are set — everything else inherits
    the LabStation theme."""

    def __init__(self, spec: TestSpec) -> None:
        super().__init__()
        self._spec = spec
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(9)
        self._battery = _BatteryBar()
        self._value = QtWidgets.QLabel("⚡ —")
        _shrink_font(self._value, 1.3, bold=True)
        self._value.setStyleSheet(f"color: {STATUS_COLORS[STATUS_UNKNOWN]};")
        self._name = QtWidgets.QLabel(spec.name)
        _shrink_font(self._name, 0.95)
        self._name.setStyleSheet("color: rgba(128, 131, 138, 230);")
        # The band this test is judged against, shown whether or not a result has
        # arrived — knowing the target before running the standard is the point.
        self._limits = QtWidgets.QLabel(limits_text(spec))
        _shrink_font(self._limits, 0.85)
        self._limits.setStyleSheet("color: rgba(128, 131, 138, 175);")
        lay.addWidget(self._battery)
        lay.addWidget(self._value)
        lay.addWidget(self._name)
        lay.addWidget(self._limits)
        lay.addStretch()

    def test_name(self) -> str:
        return self._spec.name

    def value_text(self) -> str:
        return self._value.text()

    def limits_text_shown(self) -> str:
        return self._limits.text()

    def update_result(self, machine: Machine,
                      result: Optional[TestResult], now: datetime) -> None:
        if result is not None and result.value is not None:
            color = (STATUS_COLORS[STATUS_GREEN] if result.in_spec
                     else STATUS_COLORS[STATUS_RED])
            self._value.setText(
                f"⚡ {result.value:g} {self._spec.units}".rstrip())
        else:
            color = STATUS_COLORS[STATUS_UNKNOWN]
            self._value.setText("⚡ —")
        self._value.setStyleSheet(f"color: {color};")
        # The spec's own window, not just the machine default. Without it a card
        # showed a half-full battery for a test the verdict had already called
        # stale — two answers to "how long does this last" on the same screen.
        self._battery.set_fraction(
            qc_freshness(machine, result, now, self._spec.qc_expire_hours),
            color)


class _MachineCard(QtWidgets.QWidget):
    """The machine's status surface — flat, no frame of its own, so it sits
    directly on the ModuleFrame and inherits the LabStation theme.

    Header row: bold name + integrated controls (poll / interval /
    override, passed in by the module) + ⚙ parser settings. Below: one QC
    section per LabCore spec (battery + ⚡ value), reason line, status dot
    left / "ago" stamp right. Optional machine photo on the right."""

    def __init__(self, machine: Optional[Machine], on_settings,
                 controls: Optional[List[QtWidgets.QWidget]] = None,
                 on_corrections=None) -> None:
        super().__init__()
        self._machine = machine
        self._qc_rows: List[_QCRow] = []

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        # ── Header: name + controls ──
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(4)
        self._title = QtWidgets.QLabel()
        _shrink_font(self._title, 1.6, bold=True)
        header.addWidget(self._title)
        header.addStretch()
        for control in (controls or []):
            header.addWidget(control)
        # A plain click, not a popup menu: three tests pin that ⚙ opens the
        # parser dialog directly, and an InstantPopup menu blocks on a modal
        # popup the moment anything clicks it. Correction factors live INSIDE
        # that dialog instead — which is what "in the settings" meant anyway.
        self.settings_button = QtWidgets.QToolButton()
        self.settings_button.setText("⚙")
        self.settings_button.setToolTip("Parser settings")
        self.settings_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.settings_button.clicked.connect(
            lambda: on_settings(self._machine))
        header.addWidget(self.settings_button)
        outer.addLayout(header)

        # ── Body: QC sections left, photo right ──
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(12)
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(5)
        self._qc_box = QtWidgets.QVBoxLayout()
        self._qc_box.setSpacing(5)
        left.addLayout(self._qc_box)
        self._reason = QtWidgets.QLabel("Not polled yet.")
        _shrink_font(self._reason, 0.95)
        self._reason.setStyleSheet("color: rgba(128, 131, 138, 230);")
        self._reason.setWordWrap(True)
        left.addWidget(self._reason)
        left.addStretch()
        body.addLayout(left, 1)
        self._image = QtWidgets.QLabel()
        body.addWidget(self._image)
        outer.addLayout(body)

        # ── Footer: status dot left, "ago" right ──
        footer = QtWidgets.QHBoxLayout()
        self._dot = QtWidgets.QLabel(f"● {STATUS_UNKNOWN}")
        _shrink_font(self._dot, 0.95, bold=True)
        footer.addWidget(self._dot)
        footer.addStretch()
        self._ago = QtWidgets.QLabel("—")
        _shrink_font(self._ago, 0.8)
        self._ago.setStyleSheet("color: rgba(128, 131, 138, 200);")
        footer.addWidget(self._ago)
        outer.addLayout(footer)

        self._set_dot(STATUS_UNKNOWN)
        self.set_machine(machine)

    # accessors used by the module and tests
    def title_text(self) -> str:
        return self._title.text()

    def status_text(self) -> str:
        return self._dot.text()

    def subtitle_text(self) -> str:
        return self._reason.text()

    def qc_rows(self) -> List[_QCRow]:
        return list(self._qc_rows)

    def set_machine(self, machine: Optional[Machine]) -> None:
        self._machine = machine
        self._title.setText(
            (machine.title or "Machine").upper() if machine
            else "NOT CONFIGURED")
        for row in self._qc_rows:
            self._qc_box.removeWidget(row)
            row.deleteLater()
        self._qc_rows = []
        if machine:
            for spec in machine.tests:
                row = _QCRow(spec)
                self._qc_rows.append(row)
                self._qc_box.addWidget(row)
        self._image.clear()
        self._image.setVisible(False)
        if machine and machine.image_path and os.path.exists(machine.image_path):
            from PySide6 import QtGui
            pix = QtGui.QPixmap(machine.image_path)
            if not pix.isNull():
                self._image.setPixmap(pix.scaledToHeight(
                    116, QtCore.Qt.TransformationMode.SmoothTransformation))
                self._image.setVisible(True)
        if not machine:
            self._reason.setText("Click ⚙ to set up this machine.")

    def update_view(self, evaluation: Optional[MachineEvaluation],
                    now: datetime) -> None:
        if self._machine is None:
            return
        results = ({r.name: r for r in evaluation.test_results}
                   if evaluation else {})
        for row in self._qc_rows:
            row.update_result(self._machine, results.get(row.test_name()), now)
        status = evaluation.status if evaluation else STATUS_UNKNOWN
        self._reason.setText(evaluation.reason if evaluation
                             else "Not polled yet.")
        self._ago.setText(format_relative_time(
            evaluation.last_seen if evaluation else None, now))
        self._set_dot(status)

    def _set_dot(self, status: str) -> None:
        color = STATUS_COLORS.get(status, STATUS_COLORS[STATUS_UNKNOWN])
        self._dot.setText(f"● {status}")
        self._dot.setStyleSheet(f"color: {color};")


class _MethodPickerDialog(QtWidgets.QDialog):
    """Scrollable, filterable checkbox list of LabCore test methods —
    replaces the screen-filling QMenu. Check any number, OK."""

    def __init__(self, methods: List[str], parent,
                 title: str = "Select test method(s)",
                 selected=()) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(420, 480)
        root = QtWidgets.QVBoxLayout(self)
        self._filter = QtWidgets.QLineEdit()
        self._filter.setPlaceholderText("Type to filter…")
        self._filter.textChanged.connect(self._apply_filter)
        root.addWidget(self._filter)
        self._list = QtWidgets.QListWidget()
        self._list.setVerticalScrollMode(
            QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        already = list(selected or [])
        # A method already on the mapping that LabCore no longer lists still
        # gets a (checked) row. LabCore's method names are uncurated, so a
        # rename orphans a mapping — and dropping it silently the moment the
        # operator opens the editor and clicks OK deletes their work.
        for method in list(methods) + [m for m in already
                                       if m not in methods]:
            item = QtWidgets.QListWidgetItem(method)
            item.setFlags(item.flags()
                          | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.CheckState.Checked
                               if method in already
                               else QtCore.Qt.CheckState.Unchecked)
            self._list.addItem(item)
        root.addWidget(self._list, 1)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._filter.setFocus()

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self._list.count()):
            item = self._list.item(i)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def selected_methods(self) -> List[str]:
        return [self._list.item(i).text() for i in range(self._list.count())
                if self._list.item(i).checkState()
                == QtCore.Qt.CheckState.Checked]


class _MachinePickerDialog(QtWidgets.QDialog):
    """First question a fresh module asks: which instrument am I?

    Configurations live in LabCore, so the honest options are to adopt one that
    already exists, copy one as a starting point, or start blank. Adopting a
    config another module is actively running is allowed — sometimes that IS
    the intent after moving a bench — but it is warned about, because two
    modules on one uid both write the same status row.

    The two modal prompts sit behind `confirm_in_use` and `ask_name` so the
    flow can be driven in tests without blocking on a dialog.
    """

    def __init__(self, choices: List[dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("LEM — set up this module")
        self._choices = list(choices or [])
        self.outcome = None

        root = QtWidgets.QVBoxLayout(self)
        blurb = QtWidgets.QLabel(
            "This module handles one instrument. Its setup is stored in "
            "LabCore, so it survives a LabStation reinstall and can be reused.")
        blurb.setWordWrap(True)
        root.addWidget(blurb)

        self._list = QtWidgets.QListWidget(self)
        for choice in self._choices:
            label = choice["title"]
            if choice.get("in_use"):
                label += "   • already running on another LabStation"
            elif choice.get("updated_at"):
                label += f"   · updated {choice['updated_at'][:10]}"
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, choice)
            self._list.addItem(item)
        root.addWidget(self._list)
        if self._choices:
            self._list.setCurrentRow(0)
        else:
            empty = QtWidgets.QLabel(
                "No machines are registered yet — create the first one.")
            empty.setWordWrap(True)
            root.addWidget(empty)

        row = QtWidgets.QHBoxLayout()
        self._adopt_btn = QtWidgets.QPushButton("Use this machine", self)
        self._adopt_btn.clicked.connect(self._on_adopt)
        self._dup_btn = QtWidgets.QPushButton("Duplicate…", self)
        self._dup_btn.clicked.connect(self._on_duplicate)
        self._new_btn = QtWidgets.QPushButton("New machine…", self)
        self._new_btn.clicked.connect(self._on_new)
        for btn in (self._adopt_btn, self._dup_btn, self._new_btn):
            row.addWidget(btn)
        root.addLayout(row)
        self._adopt_btn.setEnabled(bool(self._choices))
        self._dup_btn.setEnabled(bool(self._choices))

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ── overridable prompts ────────────────────────────────────────────
    def confirm_in_use(self, title: str) -> str:
        """Another module is live on this config. 'adopt' | 'duplicate' |
        'cancel' — duplicating is offered first because a copy is usually what
        someone wants from a machine that is already running."""
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setWindowTitle("Already in use")
        box.setText(f"“{title}” is being run by another LabStation right now.")
        box.setInformativeText(
            "Two modules on the same machine both write its status, which will "
            "look like it is flickering. Duplicating gives you the same setup "
            "on a new machine instead.")
        dup = box.addButton("Duplicate instead",
                            QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        adopt = box.addButton("Use it anyway",
                              QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(dup)
        box.exec()
        clicked = box.clickedButton()
        if clicked is dup:
            return "duplicate"
        if clicked is adopt:
            return "adopt"
        return "cancel"

    def ask_name(self, prompt: str, default: str = "") -> Optional[str]:
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Machine name", prompt, text=default)
        if not ok:
            return None
        return name.strip()

    # ── actions ────────────────────────────────────────────────────────
    def selected(self) -> Optional[dict]:
        item = self._list.currentItem()
        if item is None:
            return None
        return item.data(QtCore.Qt.ItemDataRole.UserRole)

    def _on_adopt(self) -> None:
        choice = self.selected()
        if choice is None:
            return
        if choice.get("in_use"):
            answer = self.confirm_in_use(choice["title"])
            if answer == "cancel":
                return
            if answer == "duplicate":
                self._on_duplicate()
                return
        self.outcome = ("adopt", choice["machine_uid"], choice["title"])
        self.accept()

    def _on_duplicate(self) -> None:
        choice = self.selected()
        if choice is None:
            return
        name = self.ask_name("Name for the copy:",
                             f"{choice['title']} (copy)")
        if not name:
            return
        self.outcome = ("duplicate", choice["machine_uid"], name)
        self.accept()

    def _on_new(self) -> None:
        name = self.ask_name("Name this instrument:")
        if not name:
            return
        self.outcome = ("new", "", name)
        self.accept()


class _CorrectionsDialog(QtWidgets.QDialog):
    """Per-test correction factors, editable on the bench.

    An offset added to the raw reading before it is judged, so the dialog says so
    outright and shows the band each test is checked against — a number that
    decides pass/fail should never be an unlabelled box.
    """

    def __init__(self, machine: Machine, parent) -> None:
        super().__init__(parent)
        self._machine = machine
        self._methods = correctable_methods(machine)
        self._original = {name: float((machine.corrections or {}).get(name, 0.0))
                          for name in self._methods}
        self._fields: dict = {}
        self.setWindowTitle("Correction Factors")
        self.setMinimumWidth(520)

        root = QtWidgets.QVBoxLayout(self)
        blurb = QtWidgets.QLabel(
            "Added to the raw reading of EVERY measurement — customer samples as "
            "well as QC:\n    corrected = raw + correction\n"
            "The corrected value is what is reported; the raw reading is kept in "
            "the record. Leave at 0 for no correction.")
        blurb.setWordWrap(True)
        root.addWidget(blurb)

        # One row per method this bench reports — NOT per QC spec. Most reported
        # methods have no QC assigned, and those are the customer results.
        by_spec = {sp.name: sp for sp in machine.tests or []}
        form = QtWidgets.QFormLayout()
        for name in self._methods:
            field = QtWidgets.QLineEdit(_trim_number(self._original[name]))
            field.setPlaceholderText("0")
            self._fields[name] = field
            row = QtWidgets.QHBoxLayout()
            row.addWidget(field)
            spec = by_spec.get(name)
            note = QtWidgets.QLabel(limits_text(spec) if spec is not None
                                    else "no QC assigned")
            note.setStyleSheet("color: rgba(128, 131, 138, 200);")
            row.addWidget(note)
            wrap = QtWidgets.QWidget()
            wrap.setLayout(row)
            form.addRow(name, wrap)
        if not self._methods:
            form.addRow(QtWidgets.QLabel(
                "This instrument reports no methods yet — configure the parser "
                "first, then its corrections can be set here."))
        root.addLayout(form)

        self._err = QtWidgets.QLabel("")
        self._err.setStyleSheet("color: #d64545;")
        self._err.setWordWrap(True)
        root.addWidget(self._err)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # -- test seams ---------------------------------------------------
    def rows_for_test(self, name: str) -> str:
        return self._fields[name].text()

    def set_row(self, name: str, text: str) -> None:
        self._fields[name].setText(text)

    def collect(self) -> dict:
        """Only what actually changed, so an untouched dialog writes nothing."""
        out = {}
        for name, field in self._fields.items():
            value = parse_correction_input(field.text())
            if value != self._original.get(name, 0.0):
                out[name] = value
        return out

    def _save(self) -> None:
        try:
            self._changes = self.collect()
        except ValueError as exc:
            self._err.setText(f"{exc} — use a plain number like 0.5 or -1.2.")
            return
        self.accept()

    def changes(self) -> dict:
        return getattr(self, "_changes", {})


class _MachineDialog(QtWidgets.QDialog):
    """Parser settings: source select, held template, and mapping of marked
    portions onto LabCore test methods. No custom test names exist — methods
    are fetched from LabCore."""

    CLEAN_OPS = ("strip", "collapse_ws", "keep_number",
                 "purge_text", "purge_symbols")

    def _edit_corrections(self) -> None:
        """Hand off to the module, which owns the LabCore write."""
        if callable(self._on_corrections):
            self._on_corrections(self._machine)

    def __init__(self, machine: Machine, parent,
                 recent_prints: Optional[List[str]] = None,
                 on_corrections=None) -> None:
        super().__init__(parent)
        self._machine = machine
        self._on_corrections = on_corrections
        self._mappings = [MethodMapping.from_dict(m.to_dict())
                          for m in machine.mappings]
        self._lab_id = Selector.from_dict(machine.lab_id.to_dict())
        self._methods: List[str] = []
        self._methods_loaded = False
        self._recent_prints = list(recent_prints or [])
        self._template_text = machine.template
        self._test_text = machine.template  # what the simulated parse runs on
        self.setWindowTitle("Machine Setup")
        self.setMinimumWidth(680)

        root = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        self._title = QtWidgets.QLineEdit(machine.title)
        self._source_btn = QtWidgets.QToolButton()
        self._source_btn.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        smenu = QtWidgets.QMenu(self._source_btn)
        for key in SOURCE_TYPES:
            label = SOURCE_LABELS[key]
            smenu.addAction(label, lambda k=key, l=label: self._pick_source(k, l))
        self._source_btn.setMenu(smenu)
        self._source_type = machine.source_type
        self._source_btn.setText(SOURCE_LABELS.get(
            machine.source_type, SOURCE_LABELS["single_csv"]))
        self._csv_path = QtWidgets.QLineEdit(machine.csv_path)
        self._delimiter = QtWidgets.QLineEdit(machine.delimiter)
        self._qc_hours = QtWidgets.QLineEdit(str(machine.qc_expire_hours))
        self._image_path = QtWidgets.QLineEdit(machine.image_path)

        form.addRow("Name", self._title)
        form.addRow("Source", self._source_btn)
        self._file_label = QtWidgets.QLabel("File to tail")
        self._file_wrap = self._with_browse(self._csv_path)
        form.addRow(self._file_label, self._file_wrap)
        form.addRow("Delimiter", self._delimiter)

        # Serial (RS-232) settings — used when the source is Serial.
        serial_row = QtWidgets.QHBoxLayout()
        self._com_port = QtWidgets.QLineEdit(machine.com_port)
        self._com_port.setPlaceholderText("COM3")
        self._baud = QtWidgets.QLineEdit(str(machine.baud_rate))
        self._parity = QtWidgets.QLineEdit(machine.parity)
        self._parity.setPlaceholderText("N/E/O/M/S")
        self._parity.setMaximumWidth(60)
        self._stop_bits = QtWidgets.QLineEdit(str(machine.stop_bits))
        self._stop_bits.setMaximumWidth(50)
        self._byte_size = QtWidgets.QLineEdit(str(machine.byte_size))
        self._byte_size.setMaximumWidth(40)
        self._idle_gap = QtWidgets.QLineEdit(str(machine.idle_gap))
        self._idle_gap.setMaximumWidth(60)
        for label, widget in (("Port", self._com_port), ("Baud", self._baud),
                              ("Parity", self._parity),
                              ("Stop", self._stop_bits),
                              ("Bits", self._byte_size),
                              ("Idle gap s", self._idle_gap)):
            serial_row.addWidget(QtWidgets.QLabel(label))
            serial_row.addWidget(widget)
        self._serial_wrap = QtWidgets.QWidget()
        self._serial_wrap.setLayout(serial_row)
        self._serial_label = QtWidgets.QLabel("Serial (RS-232)")
        form.addRow(self._serial_label, self._serial_wrap)

        form.addRow("QC expire default (hours)", self._qc_hours)
        form.addRow("Card image (optional)", self._with_browse(
            self._image_path, "Images (*.png *.jpg *.jpeg *.bmp);;All files (*)"))

        # Correction factors — the offset added to a raw reading before it is
        # judged. Here rather than behind the ⚙ itself so a plain click keeps
        # opening this dialog, and because it is genuinely a per-test setting.
        self.corrections_button = QtWidgets.QPushButton("Correction factors…")
        self.corrections_button.setToolTip(
            "Offsets added to raw readings before they are checked "
            "(corrected = raw + correction)")
        self.corrections_button.clicked.connect(self._edit_corrections)
        self.corrections_button.setEnabled(bool(self._on_corrections))
        form.addRow("QC corrections", self.corrections_button)
        root.addLayout(form)

        outer_root = root

        # ── First-run hint: parsing setup needs a captured print ──
        self._waiting_label = QtWidgets.QLabel(
            "⏳  Waiting for the first print from the machine.\n"
            "Save with OK, run a sample (or QC) on the instrument, then "
            "come back here — the received print becomes the mapping "
            "template below.")
        self._waiting_label.setWordWrap(True)
        self._waiting_label.setStyleSheet(
            "background: rgba(61, 132, 247, 26); color: #3d84f7; "
            "border: 1px solid rgba(61, 132, 247, 80); border-radius: 6px; "
            "padding: 10px; font-size: 12px;")
        root.addWidget(self._waiting_label)

        # ── Everything below is gated until a template exists ──
        self._mapping_area = QtWidgets.QWidget()
        area = QtWidgets.QVBoxLayout(self._mapping_area)
        area.setContentsMargins(0, 0, 0, 0)
        root = area  # subsequent sections land inside the gated area

        # ── Template: the held device print, split into selectable cells ──
        root.addWidget(self._section_label(
            "Received print (mapping template) — select a cell, then map it"))
        self._cells = QtWidgets.QTableWidget(0, 0)
        self._cells.setMinimumHeight(120)
        self._cells.setMaximumHeight(200)
        self._cells.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self._cells.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._cells.setWordWrap(False)
        self._rebuild_cells()
        self._delimiter.textChanged.connect(lambda _: self._rebuild_cells())
        root.addWidget(self._cells)

        assign_row = QtWidgets.QHBoxLayout()
        map_btn = QtWidgets.QPushButton("Map selected cell → method(s)…")
        map_btn.setToolTip(
            "The selected cell's POSITION becomes the value for the chosen "
            "method(s) on every future print. Best when the report layout "
            "never changes.")
        map_btn.clicked.connect(lambda: self._map_selected(detect=False))
        detect_btn = QtWidgets.QPushButton(
            "Detect selected cell by its label → method(s)…")
        detect_btn.setToolTip(
            "Find the value by the TEXT around it (e.g. 'Cloud point :') "
            "instead of its position — robust when the report layout moves "
            "around, as serial reports often do. The detection is built for "
            "you from the selected cell.")
        detect_btn.clicked.connect(lambda: self._map_selected(detect=True))
        lab_id_btn = QtWidgets.QToolButton()
        lab_id_btn.setText("Selected cell = Lab ID")
        lab_id_btn.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        lmenu = QtWidgets.QMenu(lab_id_btn)
        lmenu.addAction("By cell position",
                        lambda: self._set_lab_id(detect=False))
        lmenu.addAction("By label detection (robust for serial)",
                        lambda: self._set_lab_id(detect=True))
        lab_id_btn.setMenu(lmenu)
        assign_row.addWidget(map_btn)
        assign_row.addWidget(detect_btn)
        assign_row.addWidget(lab_id_btn)
        assign_row.addStretch()
        root.addLayout(assign_row)

        # ── Mappings ──
        root.addWidget(self._section_label(
            "Mappings — marked portions → LabCore test methods"))
        self._map_table = QtWidgets.QTableWidget(0, 5)
        self._map_table.setHorizontalHeaderLabels(
            ["Selection", "Clean tools", "Method(s)", "CSV header", "QC"])
        self._map_table.horizontalHeader().setStretchLastSection(True)
        self._map_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        root.addWidget(self._map_table, 1)

        tools_row = QtWidgets.QHBoxLayout()
        clean_btn = QtWidgets.QToolButton()
        clean_btn.setText("Clean tools for selected mapping…")
        clean_btn.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        # Built on every show: it lists the highlighted row's OWN tools, each
        # with an Edit and a Remove, so a math expression can be corrected
        # rather than cleared and retyped.
        self._clean_menu = QtWidgets.QMenu(clean_btn)
        self._clean_menu.aboutToShow.connect(self._rebuild_clean_menu)
        self._rebuild_clean_menu()
        clean_btn.setMenu(self._clean_menu)
        header_btn = QtWidgets.QPushButton("CSV header…")
        header_btn.setToolTip(
            "Name this mapping's column in the latest-result CSV export — "
            "one clean header (e.g. “Cloud Point”) instead of every LabCore "
            "method name. Alternates sharing a header share the column.")
        header_btn.clicked.connect(self._set_csv_header)
        methods_btn = QtWidgets.QPushButton("Methods…")
        methods_btn.setToolTip(
            "Change which LabCore method(s) the selected mapping feeds. The "
            "cell, its clean tools, the CSV header and the QC sample all stay "
            "as they are.")
        methods_btn.clicked.connect(self._edit_methods)
        qc_btn = QtWidgets.QPushButton("QC for selected mapping…")
        qc_btn.clicked.connect(self._set_mapping_qc)
        del_btn = QtWidgets.QPushButton("Remove mapping")
        del_btn.clicked.connect(self._remove_mapping)
        tools_row.addWidget(clean_btn)
        tools_row.addWidget(methods_btn)
        tools_row.addWidget(header_btn)
        tools_row.addWidget(qc_btn)
        tools_row.addWidget(del_btn)
        tools_row.addStretch()
        root.addLayout(tools_row)

        # ── Simulated output: run the mappings against a test print ──
        root.addWidget(self._section_label(
            "Simulated output — what these mappings extract"))
        test_row = QtWidgets.QHBoxLayout()
        recent_btn = QtWidgets.QToolButton()
        recent_btn.setText("Test with a received print…")
        recent_btn.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        rmenu = QtWidgets.QMenu(recent_btn)
        if not self._recent_prints:
            rmenu.addAction("(no prints received yet)").setEnabled(False)
        for text in self._recent_prints[:15]:
            preview = " ".join(text.split())[:60]
            rmenu.addAction(preview or "(blank)",
                            lambda t=text: self._set_test_print(t))
        recent_btn.setMenu(rmenu)
        paste_btn = QtWidgets.QPushButton("Paste test print…")
        paste_btn.clicked.connect(self._paste_test_print)
        swap_btn = QtWidgets.QPushButton("Make test print the template")
        swap_btn.setToolTip(
            "Swap the captured template for the current test print — the "
            "cell grid above rebuilds from it.")
        swap_btn.clicked.connect(self._make_test_template)
        self._test_source_label = QtWidgets.QLabel("Testing: captured template")
        for w in (recent_btn, paste_btn, swap_btn, self._test_source_label):
            test_row.addWidget(w)
        test_row.addStretch()
        root.addLayout(test_row)

        self._preview = QtWidgets.QTableWidget(0, 3)
        self._preview.setHorizontalHeaderLabels(
            ["Assign to", "Extracted value", "From"])
        self._preview.horizontalHeader().setStretchLastSection(True)
        self._preview.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._preview.setMinimumHeight(110)
        self._preview.setMaximumHeight(180)
        root.addWidget(self._preview)

        # Back to the dialog-level layout: the gated area goes in whole.
        root = outer_root
        root.addWidget(self._mapping_area, 1)

        # ── Manual entry: nothing to set up here ──
        # No print means nothing to map, and this bench records nothing but QC —
        # so what it checks is the master view's assignment, not a list kept
        # here. Setup really is just the name and the source.
        self._manual_area = QtWidgets.QWidget()
        manual_layout = QtWidgets.QVBoxLayout(self._manual_area)
        manual_layout.setContentsMargins(0, 0, 0, 0)
        manual_layout.addWidget(self._section_label(
            "Manual entry — QC results only"))
        self._manual_note = QtWidgets.QLabel(
            "Nothing to configure here. This machine does not parse: the "
            "operator types a QC result into the module window, and the only "
            "tests it can accept are the ones assigned to it in LEM "
            "(“Assign QC samples” in the master view). Until QC is assigned "
            "the entry box stays closed — there would be nothing to compare a "
            "reading against. Correction factors still apply.")
        self._manual_note.setWordWrap(True)
        self._manual_note.setStyleSheet(
            "background: rgba(61, 132, 247, 26); color: #3d84f7; "
            "border: 1px solid rgba(61, 132, 247, 80); border-radius: 6px; "
            "padding: 10px; font-size: 12px;")
        manual_layout.addWidget(self._manual_note)
        manual_layout.addStretch()
        root.addWidget(self._manual_area, 1)

        self._methods_note = QtWidgets.QLabel(
            "Loading test methods from LabCore…")
        self._methods_note.setWordWrap(True)
        self._methods_note.setStyleSheet("color: #b8860b; font-size: 11px;")
        root.addWidget(self._methods_note)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        # Config files are gone: this setup is stored in LabCore
        # (lem_machine_config), so it survives a LabStation reinstall and can
        # be duplicated onto an identical instrument from the startup picker.
        root.addWidget(buttons)

        self._refresh_map_table()
        self._update_source_visibility()
        self._update_setup_gating()
        # Method list loads in the background so the dialog opens instantly
        # even when LabCore is slow (or unreachable).
        _in_thread(self._fetch_methods, self._on_methods_loaded)

    def _on_methods_loaded(self, methods: List[str]) -> None:
        try:
            self._methods = list(methods or [])
            self._methods_loaded = True
            if self._methods:
                self._methods_note.setVisible(False)
            else:
                self._methods_note.setText(
                    "No test methods available from LabCore — connect "
                    "LabCore to assign methods. There are no custom test "
                    "names in LEM.")
        except RuntimeError:
            pass  # dialog was closed before the fetch finished

    # ── LabCore methods (the only allowed test names) ─────────────────────

    @staticmethod
    def _fetch_methods() -> List[str]:
        read_sql = globals().get("labcore_read_sql")
        if not callable(read_sql):
            return []
        methods = set()
        for query in (
            "SELECT DISTINCT test_name FROM sample_tests "
            "WHERE test_name IS NOT NULL AND TRIM(test_name) != ''",
            "SELECT DISTINCT test_name FROM lem_qc_specs "
            "WHERE test_name IS NOT NULL AND TRIM(test_name) != ''",
        ):
            try:
                result = read_sql(query)
            except Exception:
                continue
            if result.get("error"):
                continue
            for row in result.get("rows") or []:
                name = str(row.get("test_name") or "").strip()
                if name:
                    methods.add(name)
        return sorted(methods)

    def _pick_methods(self) -> List[str]:
        """Scrollable checkbox picker — replaces the screen-filling menu."""
        if not self._methods:
            QtWidgets.QMessageBox.information(
                self, "Test methods",
                "Still loading test methods from LabCore — try again in a "
                "moment." if not self._methods_loaded else
                "No test methods available from LabCore — connect LabCore "
                "to assign methods.")
            return []
        picker = _MethodPickerDialog(self._methods, self)
        if picker.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return []
        return picker.selected_methods()

    # ── Template cells ────────────────────────────────────────────────────

    def _rebuild_cells(self) -> None:
        """Grid of the template: one row per print line, one column per
        delimited cell. Each item remembers its FLAT cell index (the value
        cell-selection uses), so multi-line serial reports read naturally."""
        delim = self._delimiter.text() or ","
        lines = self._template_text.splitlines() or [""]
        rows = [line.split(delim) for line in lines]
        self._cells.setRowCount(len(rows))
        self._cells.setColumnCount(max((len(r) for r in rows), default=0))
        flat = 0
        for r, row_cells in enumerate(rows):
            for c, cell in enumerate(row_cells):
                item = QtWidgets.QTableWidgetItem(cell)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, flat)
                item.setToolTip(f"cell {flat}")
                self._cells.setItem(r, c, item)
                flat += 1
        self._cells.resizeColumnsToContents()
        self._cells.resizeRowsToContents()
        if hasattr(self, "_preview"):
            self._refresh_preview()

    def _selected_cell_index(self) -> Optional[int]:
        item = self._cells.currentItem()
        if item is None:
            return None
        flat = item.data(QtCore.Qt.ItemDataRole.UserRole)
        return int(flat) if flat is not None else None

    def _selected_cell_text(self) -> str:
        item = self._cells.currentItem()
        return item.text() if item else ""

    def _build_selector(self, detect: bool,
                        capture: str = "number") -> Optional[Selector]:
        """Selector for the currently selected template cell — by position,
        or by a label-detection pattern built from the cell's real text."""
        index = self._selected_cell_index()
        if index is None:
            QtWidgets.QMessageBox.information(
                self, "Mapping", "Select a cell in the template first.")
            return None
        if not detect:
            return Selector(mode="cell", index=index)
        sample = self._selected_cell_text()
        suggested = build_detection_pattern(sample, capture=capture) or ""
        what = "value" if capture == "text" else "number"
        if suggested:
            prompt = (f"Detection built from “{sample.strip()}”.\n"
                      f"It finds the {what} after that label on every print, "
                      "even if the report layout shifts.\n"
                      "OK to accept, or fine-tune:")
        else:
            prompt = ("Enter the label to detect (e.g. “Cloud point :”) — "
                      "the value after it is captured. Or a full pattern "
                      "(first group = the value):")
        pattern, ok = QtWidgets.QInputDialog.getText(
            self, "Text detection", prompt, text=suggested)
        if not ok or not pattern.strip():
            return None
        pattern = pattern.strip()
        # A plain label typed by hand ("Cloud point:") is turned into a
        # detection automatically — no regex knowledge needed.
        if "(" not in pattern:
            pattern = build_detection_pattern(pattern, capture=capture) or pattern
        return Selector(mode="detect", pattern=pattern)

    def _set_lab_id(self, detect: bool) -> None:
        selector = self._build_selector(detect, capture="text")
        if selector is None:
            return
        selector.clean = self._lab_id.clean  # keep existing clean tools
        self._lab_id = selector
        self._refresh_map_table()

    def _map_selected(self, detect: bool) -> None:
        selector = self._build_selector(detect, capture="number")
        if selector is None:
            return
        methods = self._pick_methods()
        if not methods:
            return
        # Always a NEW mapping, including on a cell already mapped. It used to
        # merge into the existing one, which meant a cell could only ever have
        # ONE set of clean tools — and one raw density reading feeding API
        # gravity AND kg/m³ is two different conversions of the same number.
        # Grouping methods onto one value is what checking several in the
        # picker does; changing them afterwards is "Methods for selected
        # mapping…".
        self._mappings.append(MethodMapping(methods=methods,
                                            selector=selector))
        self._refresh_map_table()

    # ── Mapping table + clean tools ───────────────────────────────────────

    def _refresh_map_table(self) -> None:
        """Row 0 is always the Lab ID — it flows through the same pipeline
        (selection → clean tools → assignment) as every method mapping."""
        table = self._map_table
        table.setRowCount(1 + len(self._mappings))
        lab_row = (self._lab_id.describe(),
                   ", ".join(self._lab_id.clean) or "—",
                   "Lab ID", LAB_ID_KEY, "—")
        for col, text in enumerate(lab_row):
            item = QtWidgets.QTableWidgetItem(text)
            font = item.font()
            font.setBold(True)
            item.setFont(font)
            table.setItem(0, col, item)
        for i, mapping in enumerate(self._mappings):
            if mapping.qc_sample_id:
                hours = _window_hours(mapping.qc_expire_hours)
                # "default" here means "not overridden on this bench" — the
                # standard's own window, or failing that the machine's. The
                # standard is not resolved at setup time (the library is not
                # loaded in this dialog), so this deliberately does not claim a
                # number it has not looked up.
                qc_text = (f"{mapping.qc_sample_id}"
                           + (f" · {hours:g} h" if hours
                              else " · standard/machine"))
            else:
                qc_text = "—"
            for col, text in enumerate((
                    mapping.selector.describe(),
                    ", ".join(mapping.selector.clean) or "—",
                    ", ".join(mapping.methods),
                    mapping.csv_header or "—",
                    qc_text)):
                table.setItem(1 + i, col, QtWidgets.QTableWidgetItem(text))
        table.resizeColumnsToContents()
        if hasattr(self, "_preview"):
            self._refresh_preview()

    # ── Simulated output ──────────────────────────────────────────────────

    def _current_config(self) -> Machine:
        """A throwaway Machine reflecting the dialog's CURRENT state, so the
        preview always shows what would happen after pressing OK."""
        return Machine(
            uid=self._machine.uid,
            delimiter=self._delimiter.text() or ",",
            lab_id=self._lab_id,
            mappings=self._mappings,
        )

    def _refresh_preview(self) -> None:
        machine = self._current_config()
        result = parse_print(machine, self._test_text)
        rows = [("Lab ID", result.lab_id or "(not found)",
                 self._lab_id.describe())]
        for mapping in self._mappings:
            value = extract_value(mapping.selector, self._test_text,
                                  machine.delimiter).strip()
            target = mapping.csv_header or ", ".join(mapping.methods)
            if value:
                # Alternates: an earlier mapping may already have claimed
                # these methods — this one extracted but isn't the winner.
                used = any(result.values.get(m) == value
                           for m in mapping.methods)
                shown = value if used else f"{value} (alternate, not used)"
            elif any(m in result.values for m in mapping.methods):
                shown = "— (covered by an alternate selection)"
            else:
                shown = "(nothing extracted)"
            rows.append((target, shown, mapping.selector.describe()))
        self._preview.setRowCount(len(rows))
        for i, (target, value, source) in enumerate(rows):
            for col, text in enumerate((target, value, source)):
                item = QtWidgets.QTableWidgetItem(text)
                if col == 1:
                    if text in ("(not found)", "(nothing extracted)"):
                        item.setForeground(
                            QtGui.QColor(STATUS_COLORS[STATUS_RED]))
                    elif "alternate" in text:
                        item.setForeground(
                            QtGui.QColor(STATUS_COLORS[STATUS_UNKNOWN]))
                self._preview.setItem(i, col, item)
        self._preview.resizeColumnsToContents()

    def _set_test_print(self, text: str) -> None:
        self._test_text = text
        self._test_source_label.setText("Testing: received print")
        self._refresh_preview()

    def _paste_test_print(self) -> None:
        text, ok = QtWidgets.QInputDialog.getMultiLineText(
            self, "Test print",
            "Paste a device print to run the mappings against:",
            self._test_text)
        if ok and text.strip():
            self._test_text = text
            self._test_source_label.setText("Testing: pasted print")
            self._refresh_preview()

    def _make_test_template(self) -> None:
        if not self._test_text.strip():
            return
        self._template_text = self._test_text
        self._test_source_label.setText("Testing: captured template")
        self._rebuild_cells()
        self._update_setup_gating()

    def _selected_mapping(self) -> Optional[MethodMapping]:
        row = self._map_table.currentRow()
        if 1 <= row <= len(self._mappings):
            return self._mappings[row - 1]
        return None

    def _selected_selector(self) -> Optional[Selector]:
        """The selector of the highlighted mapping row — row 0 is the Lab ID,
        so clean tools flow into it exactly like any method mapping."""
        row = self._map_table.currentRow()
        if row == 0:
            return self._lab_id
        mapping = self._selected_mapping()
        return mapping.selector if mapping else None

    # ── Editing a mapping after it is made ────────────────────────────────

    def set_mapping_methods(self, methods: List[str]) -> None:
        """Point the highlighted mapping at a different set of methods.

        Everything else on it — the selector, its clean tools, the CSV header,
        the QC sample — is untouched: this changes what the extracted value is
        called, not how it is extracted.

        An empty selection is refused. A mapping with no methods extracts a
        value for nothing, and unchecking everything is far more likely to be a
        misclick than a request to delete — that is the Remove button's job.
        """
        mapping = self._selected_mapping()
        if mapping is None or not methods:
            return
        mapping.methods = [str(m) for m in methods]
        self._refresh_map_table()

    def _edit_methods(self) -> None:
        mapping = self._selected_mapping()
        if mapping is None:
            QtWidgets.QMessageBox.information(
                self, "Mapping", "Select a mapping row first.")
            return
        if not self._methods:
            self._pick_methods()      # shares the "still loading" explanation
            return
        picker = _MethodPickerDialog(self._methods, self,
                                     title="Methods for this mapping",
                                     selected=mapping.methods)
        if picker.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.set_mapping_methods(picker.selected_methods())

    def set_clean_op(self, index: int, argument: str) -> None:
        """Rewrite the argument of one clean tool, in place.

        In place because `apply_clean` runs the tools in order: dropping the old
        one and appending the new would move a math step to the end and quietly
        change the result. Ryan: "allow me to edit the math, instead of having
        to clear it and re-write the equation."

        Only the tools that carry an argument (`math:`, `remove:`) are editable;
        the plain ones are toggled, not typed. An empty argument is a cancelled
        edit, not a request for a `math:` with no expression.
        """
        selector = self._selected_selector()
        if selector is None or not 0 <= index < len(selector.clean):
            return
        prefix = self._clean_op_prefix(selector.clean[index])
        if prefix is None or not str(argument).strip():
            return
        selector.clean[index] = f"{prefix}:{str(argument).strip()}"
        self._refresh_map_table()

    def drop_clean_op(self, index: int) -> None:
        """Remove one clean tool, leaving the rest in their order."""
        selector = self._selected_selector()
        if selector is None or not 0 <= index < len(selector.clean):
            return
        del selector.clean[index]
        self._refresh_map_table()

    @staticmethod
    def _clean_op_prefix(op: str) -> Optional[str]:
        """"math" / "remove" for a tool that carries an argument, else None."""
        for prefix in ("math", "remove"):
            if str(op).startswith(f"{prefix}:"):
                return prefix
        return None

    def _prompt_clean_op(self, index: int) -> None:
        selector = self._selected_selector()
        if selector is None or not 0 <= index < len(selector.clean):
            return
        op = selector.clean[index]
        prefix = self._clean_op_prefix(op)
        if prefix is None:
            return
        current = op.split(":", 1)[1]
        label = ("Expression on the value as x:" if prefix == "math"
                 else "Text to remove from the value:")
        text, ok = QtWidgets.QInputDialog.getText(
            self, "Edit clean tool", label, text=current)
        if ok:
            self.set_clean_op(index, text)

    def _rebuild_clean_menu(self) -> None:
        """The menu is the editor: which plain tools are on, and an Edit and a
        Remove for every tool that carries an argument. Rebuilt on each show
        because it describes the highlighted row, which changes."""
        menu = self._clean_menu
        menu.clear()
        selector = self._selected_selector()
        if selector is None:
            menu.addAction("Select a row first").setEnabled(False)
            return
        for op in self.CLEAN_OPS:
            action = menu.addAction(op, lambda o=op: self._toggle_clean(o))
            action.setCheckable(True)
            action.setChecked(op in selector.clean)
        editable = [(i, op) for i, op in enumerate(selector.clean)
                    if self._clean_op_prefix(op) is not None]
        if editable:
            menu.addSeparator()
            for i, op in editable:
                menu.addAction(f"Edit  {op}…",
                               lambda n=i: self._prompt_clean_op(n))
                menu.addAction(f"Remove  {op}",
                               lambda n=i: self.drop_clean_op(n))
        menu.addSeparator()
        menu.addAction("Add remove:<text>…", self._add_remove_op)
        menu.addAction("Add math:<expr>…", self._add_math_op)
        menu.addAction("Clear clean tools", self._clear_clean)

    def _toggle_clean(self, op: str) -> None:
        selector = self._selected_selector()
        if selector is None:
            return
        if op in selector.clean:
            selector.clean.remove(op)
        else:
            selector.clean.append(op)
        self._refresh_map_table()

    def _add_remove_op(self) -> None:
        selector = self._selected_selector()
        if selector is None:
            return
        text, ok = QtWidgets.QInputDialog.getText(
            self, "Clean tool", "Text to remove from the value:")
        if ok and text:
            selector.clean.append(f"remove:{text}")
            self._refresh_map_table()

    def _add_math_op(self) -> None:
        selector = self._selected_selector()
        if selector is None:
            return
        expr, ok = QtWidgets.QInputDialog.getText(
            self, "Math operation",
            "Expression on the value as x (e.g. round(x * 1000, 1)):")
        if ok and expr.strip():
            selector.clean.append(f"math:{expr.strip()}")
            self._refresh_map_table()

    def _set_csv_header(self) -> None:
        mapping = self._selected_mapping()
        if mapping is None:
            QtWidgets.QMessageBox.information(
                self, "CSV header",
                "Select a method mapping row first (the Lab ID column name "
                "is fixed).")
            return
        header, ok = QtWidgets.QInputDialog.getText(
            self, "CSV header",
            "Column name in the latest-result CSV (empty = use the "
            "method names):", text=mapping.csv_header)
        if ok:
            mapping.csv_header = header.strip()
            self._refresh_map_table()

    def _set_mapping_qc(self) -> None:
        """Mark the selected mapping as QC-checked: which QC sample runs it,
        and how long a passing QC lasts.

        0 is not "expires now" — it is this bench declining to override, so the
        standard's own window decides, and the machine default after that.
        """
        mapping = self._selected_mapping()
        if mapping is None:
            return
        sample, ok = QtWidgets.QInputDialog.getText(
            self, "QC sample",
            "QC sample Lab ID (empty = not QC-checked):",
            text=mapping.qc_sample_id)
        if not ok:
            return
        mapping.qc_sample_id = sample.strip()
        if mapping.qc_sample_id:
            hours, ok = QtWidgets.QInputDialog.getDouble(
                self, "QC expires",
                "QC window in hours "
                "(0 = use the standard's own, then the machine default):",
                mapping.qc_expire_hours, 0, 8760, 1)
            if ok:
                mapping.qc_expire_hours = hours
        else:
            mapping.qc_expire_hours = 0.0
        self._refresh_map_table()

    def _clear_clean(self) -> None:
        selector = self._selected_selector()
        if selector is not None:
            selector.clean = []
            self._refresh_map_table()

    def _remove_mapping(self) -> None:
        row = self._map_table.currentRow()
        if row == 0:
            QtWidgets.QMessageBox.information(
                self, "Lab ID",
                "The Lab ID row can't be removed — reassign it from the "
                "template instead.")
            return
        if 1 <= row <= len(self._mappings):
            del self._mappings[row - 1]
            self._refresh_map_table()

    # ── Config export / import ────────────────────────────────────────────

    def _dialog_machine_snapshot(self) -> Machine:
        """The dialog's CURRENT state as a Machine (what OK would save)."""
        snapshot = Machine.from_dict(self._machine.to_dict())
        self._write_fields_into(snapshot)
        return snapshot

    def _apply_machine(self, m: Machine) -> None:
        """Repopulate every dialog widget from a loaded Machine."""
        self._title.setText(m.title)
        self._pick_source(m.source_type, SOURCE_LABELS.get(
            m.source_type, SOURCE_LABELS["single_csv"]))
        self._csv_path.setText(m.csv_path)
        self._delimiter.setText(m.delimiter)
        self._com_port.setText(m.com_port)
        self._baud.setText(str(m.baud_rate))
        self._parity.setText(m.parity)
        self._stop_bits.setText(str(m.stop_bits))
        self._byte_size.setText(str(m.byte_size))
        self._idle_gap.setText(str(m.idle_gap))
        self._qc_hours.setText(str(m.qc_expire_hours))
        self._image_path.setText(m.image_path)
        self._lab_id = m.lab_id
        self._mappings = m.mappings
        self._template_text = m.template
        self._test_text = m.template
        self._machine.maintenance = m.maintenance
        self._machine.tests = m.tests
        self._rebuild_cells()
        self._refresh_map_table()
        self._update_source_visibility()
        self._update_setup_gating()

    # ── Misc ──────────────────────────────────────────────────────────────

    def _pick_source(self, key: str, label: str) -> None:
        self._source_type = key
        self._source_btn.setText(label)
        self._update_source_visibility()
        self._update_setup_gating()

    def _update_source_visibility(self) -> None:
        """Show only the fields the chosen source actually uses."""
        manual = self._source_type == "manual"
        serial = self._source_type == "serial"
        self._serial_label.setVisible(serial)
        self._serial_wrap.setVisible(serial)
        # A manual bench has neither a file nor a wire.
        self._file_label.setVisible(not serial and not manual)
        self._file_wrap.setVisible(not serial and not manual)
        multi = self._source_type == "multi_csv"
        self._file_label.setText("Folder to watch" if multi else "File to tail")
        self._file_wrap.setToolTip(
            f"Every file dropped here is parsed, then moved into a "
            f"“{PROCESSED_DIRNAME}” subfolder — whatever is left in the "
            f"folder is simply what hasn't been processed yet."
            if multi else
            "The file is tailed: only newly appended lines are parsed.")

    def _update_setup_gating(self) -> None:
        """First-time setup: until a print has been captured there is
        nothing to map — gray the whole mapping/simulation area out.

        A manual bench swaps that whole area for the declared-method list, and
        is never "waiting for the first print": no print is ever coming."""
        manual = self._source_type == "manual"
        has_template = bool(self._template_text.strip())
        self._manual_area.setVisible(manual)
        self._mapping_area.setVisible(not manual)
        self._mapping_area.setEnabled(has_template)
        self._waiting_label.setVisible(not manual and not has_template)

    def _section_label(self, text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text.upper())
        label.setStyleSheet(
            "color: #8e8e93; font-size: 10px; letter-spacing: 1px; "
            "font-weight: 700; margin-top: 6px;")
        return label

    def _with_browse(self, line_edit: QtWidgets.QLineEdit,
                     filters: str = "CSV files (*.csv);;All files (*)"
                     ) -> QtWidgets.QWidget:
        wrap = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        browse = QtWidgets.QPushButton("…")
        browse.setFixedWidth(28)

        def pick():
            if self._source_type == "multi_csv":
                path = QtWidgets.QFileDialog.getExistingDirectory(
                    self, "Choose folder", line_edit.text() or "")
            else:
                path, _ = QtWidgets.QFileDialog.getOpenFileName(
                    self, "Choose file", line_edit.text() or "", filters)
            if path:
                line_edit.setText(path)

        browse.clicked.connect(pick)
        lay.addWidget(line_edit, 1)
        lay.addWidget(browse)
        return wrap

    def _on_accept(self) -> None:
        self._write_fields_into(self._machine)
        self.accept()

    def _write_fields_into(self, m: Machine) -> None:
        m.title = self._title.text().strip() or "Machine"
        m.source_type = (self._source_type
                         if self._source_type in SOURCE_TYPES else "single_csv")
        m.csv_path = self._csv_path.text().strip()
        m.delimiter = self._delimiter.text() or ","
        m.com_port = self._com_port.text().strip()
        try:
            m.baud_rate = int(self._baud.text())
        except ValueError:
            m.baud_rate = 9600
        m.parity = (self._parity.text().strip().upper()[:1] or "N")
        try:
            m.stop_bits = float(self._stop_bits.text())
        except ValueError:
            m.stop_bits = 1.0
        try:
            m.byte_size = int(self._byte_size.text())
        except ValueError:
            m.byte_size = 8
        try:
            m.idle_gap = float(self._idle_gap.text())
        except ValueError:
            m.idle_gap = 0.3
        try:
            m.qc_expire_hours = float(self._qc_hours.text())
        except ValueError:
            m.qc_expire_hours = 24.0
        m.image_path = self._image_path.text().strip()
        m.lab_id = self._lab_id
        # Left alone in manual mode rather than cleared: manual QC ignores
        # mappings, so a machine switched over by mistake and switched back
        # still has its parse setup.
        m.mappings = self._mappings
        m.template = self._template_text
