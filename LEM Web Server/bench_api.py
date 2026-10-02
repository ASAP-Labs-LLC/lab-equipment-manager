#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bench_api.py — the v2 bench protocol, the server's half (transfer spec §6).

A v4 bench journals every reading on its own disk before anything else
happens, numbered `(epoch, seq)` with a CRC. This module is how those records
become LEM's record, and it rests on ONE rule the bench follows: **send from
acked+1, and adopt whatever `acked` the server answers** — even one ahead of
the bench's own (the N3 rule). Everything below exists to make that rule safe.

Sync (`POST /api/v2/bench/<uid>/sync`), per request, in ONE `BEGIN IMMEDIATE`:

1. Read the cursor `(uid, epoch)`: `acked` is the highest CONTIGUOUS seq held.
2. `from_seq > acked + 1` → **409 `cursor`** with the real `acked`, and nothing
   is written. A correct bench only sends that when the server has gone BACK
   (restored from a backup, T3); it resends from `acked + 1` and loses nothing,
   because it never let go of what it had not seen acked as durable.
3. Every record with `seq > acked` is stored once: its canonical body in
   `bench_record` (custody, whatever its kind), and — for a reading or an event
   a person reads — its rows in `lem_machine_log`, the first carrying the
   unique `(uid, epoch, seq)`. Records at or below `acked` are a resend and are
   ignored. The spec says `INSERT OR IGNORE`; the store refuses a colliding
   insert under ANY conflict clause (`lem_log_no_overwrite`, so `REPLACE`
   cannot rewrite the record), so the same effect is spelled
   `INSERT … SELECT … WHERE NOT EXISTS`, which the store's docstring asks for.
4. The running digest advances over exactly those bodies, and the bench's
   `stats.digest` is compared with the server's at the same seq. A mismatch is
   written down and said in `notes` — and **never blocks ingest**: refusing a
   bench's readings because their history looks odd turns a question into a
   loss.
5. Current state (status, substatus, effective specs, heartbeat, conflicts,
   the result ledger) is updated in the same transaction, and the cursor row
   with what the bench says about itself (road, version, clock skew, stats).

Any store failure rolls the whole request back and answers 503 + Retry-After:
nothing is acked that the store does not hold. 404 is never an answer here
for a uid the server does not know: 404 is the bench's ONLY signal that this
server has no v2 at all, on which it falls back to legacy projection (§6.1).

Tokens (§6.4) are per bench, issued by `/enroll`, kept here only as SHA-256.

None of this touches LabCore. A bench reading or writing its own world through
this server costs the queue the whole lab writes through nothing at all — that
is the point of moving it here.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import json
import logging
import math
import re
import secrets
import threading
import time
import zlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from live_presence import ELLIPSIS, FLOOR_FIELD_BYTES, clip_text

logger = logging.getLogger(__name__)

PROTO = 2
#: Proto versions this server accepts (§13: N and N−1; there is no v1 sync).
ACCEPTED_PROTOS = (2,)
MAX_RECORDS = 100
#: §6.1 says ≤ 256 KB per body. Over the wire it may be gzipped; inflated it
#: may be larger, so the inflated cap is generous but finite.
MAX_SYNC_BYTES = 256 * 1024
MAX_INFLATED_BYTES = 4 * 1024 * 1024
#: snapshot.bin: 16 bytes per line; the Agilent's 84k-line file is ~1.3 MB.
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
DIGEST_ZERO = "0" * 64
#: A bench that has synced within this long is "reporting" (§14).
REPORTING_SECONDS = 120.0
#: Answers longer than this are gzipped when the bench accepts it.
GZIP_ABOVE = 1024
#: How long a bench is told to wait when the store cannot take its records.
RETRY_BUSY_S = 5
RETRY_HOLD_S = 60
RETRY_READ_ONLY_S = 300

#: Record kinds that become rows of the machine log a person reads.
LOG_KINDS = frozenset(("run", "qc", "state", "comment", "override", "pm",
                       "calibration", "config", "given_up", "conflict"))
#: The journal's own bookkeeping, and the results road's: records (the digest
#: runs over them, so the server holds them in custody), not readings.
BOOKKEEPING_KINDS = frozenset(("frame", "consumed", "projected", "settled",
                               "known", "filed", "rejected", "resolution",
                               "adoption", "ambiguity", "specs"))
#: A record's envelope: everything but its content.
ENVELOPE = frozenset(("seq", "epoch", "uid", "kind", "ts", "module", "crc"))

#: The lem_machine_specs columns a `specs` record may set.
SPEC_COLUMNS = ("test_name", "sample_id", "expected", "std_dev", "k", "units",
                "low", "high", "last_qc_at", "last_qc_value", "last_qc_in_spec",
                "correction")
#: A spec's numeric columns: a finite number or "not said" (NULL).
SPEC_NUMBERS = ("expected", "std_dev", "k", "low", "high", "last_qc_value",
                "correction")
#: The most specs one `specs` record may set. Production's busiest bench
#: publishes 18 (snapshot_service's note on the espec arm); a SimDist cut list
#: is ~21. A bench claiming more than this is not describing an instrument,
#: and 48 specs at their bounds still leave the real fleet inside GC hub's
#: 1 MB cap (test_bench_state_bounds pins the arithmetic).
MAX_SPECS = 48
#: The largest machine configuration a bench's `config` record may set.
MAX_MACHINE_CONFIG_BYTES = 128 * 1024
#: What a bench may set its override to ("" clears it); the floor's values.
BENCH_OVERRIDES = ("", "SERVICE", "DEAD-LINE")
#: Encoded-size bounds for a spec's text, shared with the floor builder.
SPEC_TEXT_BYTES = {k: FLOOR_FIELD_BYTES[k]
                   for k in ("test_name", "sample_id", "units")}

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")
_CURRENT_STATE_TABLES = ("lem_machine_status", "lem_machine_heartbeat",
                         "lem_machine_substatus", "lem_machine_specs")


# ── the recipes the bench uses too ────────────────────────────────────────────
# These three must stay byte-identical to `lem_station_module.canonical_body`,
# `journal_line` and `running_digest`. `tests/test_bench_v2_sync.py` runs the
# real module's journal against this server to hold them together.

def canonical(body) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")


def record_crc(raw: bytes) -> str:
    return "%08x" % (zlib.crc32(raw) & 0xffffffff)


def chain(previous_hex: str, raw: bytes) -> str:
    return hashlib.sha256(bytes.fromhex(previous_hex) + raw).hexdigest()


def adoption_value(value) -> str:
    """One measurement as the adoption key spells it — byte-identical to
    `lem_station_module.adoption_value`: a number in one canonical form
    (format(float, '.12g')), anything else stripped text. The file says
    "0.8000" and a corrected row's `detail.raw` holds the float 0.8; they are
    one reading."""
    if isinstance(value, bool):
        return str(value)
    text = str(value).strip() if value is not None else ""
    try:
        number = float(text)
    except ValueError:
        return text
    if number != number or number in (float("inf"), float("-inf")):
        return text
    return format(number, ".12g")


def adoption_hash(lab_id: str, raw_values: dict) -> str:
    """§10.2's H(lab_id, raw): what a bench hashes for each line after its
    adoption boundary, and what the server hashes for each recorded row.
    Identical to `lem_station_module.adoption_key`."""
    canon = {str(k): adoption_value(v) for k, v in (raw_values or {}).items()}
    body = json.dumps([str(lab_id or "").strip(), canon], sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


#: What a qc verdict row that kept no raw reading is keyed on in place of a
#: value — the module's `ADOPTION_NO_RAW`, byte for byte.
ADOPTION_NO_RAW = "(no raw)"


def _adoption_detail(detail):
    """A row's detail as a dict, {} when it has none, None when it cannot be
    read — the module's `_detail_dict`, so both sides call the same rows
    unreadable."""
    if isinstance(detail, dict):
        return detail
    if detail in (None, ""):
        return {}
    try:
        out = json.loads(detail)
    except (TypeError, ValueError):
        return None
    return out if isinstance(out, dict) else None


def adoption_raw_values(kind: str, test_name: str, value, detail: dict):
    """The raw reading a recorded row was made from (as the module's
    `legacy_row_raw_values`), or None when the row cannot say. run: `values`
    with `raw` laid over them (raw holds only the corrected tests); qc: the
    one test at `raw_value` (spec-corrected) or `raw`, else ADOPTION_NO_RAW."""
    if kind == "run":
        values = detail.get("values")
        if not isinstance(values, dict):
            return None
        out = dict(values)
        raw = detail.get("raw")
        if isinstance(raw, dict):
            out.update(raw)
        return out
    if kind == "qc":
        if not test_name:
            return None
        for name in ("raw_value", "raw"):
            raw = detail.get(name)
            if isinstance(raw, dict):
                raw = raw.get(test_name)
            if raw not in (None, ""):
                return {test_name: raw}
        # No raw kept (v3.9 under a machine-level factor): the value is a
        # corrected number, no key to the reading. Matched by count.
        return {test_name: ADOPTION_NO_RAW}
    return None


def _qc_number_text(value) -> str:
    """A verdict's `value` as v3.9 spelled it (f"{value:g}") — the module's
    `qc_verdict_value`."""
    text = str(value).strip() if value is not None else ""
    try:
        number = float(text)
    except ValueError:
        return text
    if number != number or number in (float("inf"), float("-inf")):
        return text
    return format(number, "g")


def adoption_qc_verdicts(rows) -> dict:
    """The recorded qc verdicts, as the module's `qc_verdict_record` builds
    them from LabCore's rows: {Lab ID: {test: {"raw": {raw: n}, "value":
    {value: n}}}}. "raw" holds the verdicts that kept their raw reading (a spec
    correction). "value" holds the ones that did not, by the value they
    judged: the raw reading, or the reading plus the factor of the day. The
    bench matches its file against those values. A count alone cannot tell a
    replayed verdict from a new print."""
    out: Dict[str, dict] = {}
    for r in rows:
        if str(r.get("kind") or "") != "qc":
            continue
        detail = _adoption_detail(r.get("detail"))
        test = str(r.get("test_name") or "")
        if not isinstance(detail, dict) or not test:
            continue
        slot = out.setdefault(str(r.get("lab_id") or "").strip(), {}) \
            .setdefault(test, {"raw": {}, "value": {}})
        raw = None
        for name in ("raw_value", "raw"):
            raw = detail.get(name)
            if isinstance(raw, dict):
                raw = raw.get(test)
            if raw not in (None, ""):
                break
            raw = None
        if raw is not None:
            k, side = adoption_value(raw), slot["raw"]
        else:
            k, side = _qc_number_text(r.get("value")), slot["value"]
        side[k] = side.get(k, 0) + 1
    return out


def adoption_digest(rows, now: datetime) -> dict:
    """§10.2's answer over a bench's recorded run/qc rows (pure, so the
    floor rehearsal runs the very code the endpoint does):

      counts           the multiset of H(lab_id, raw);
      unreadable_labs  Lab IDs with a row whose detail cannot be read — not
                       "no row": the bench never recovers their lines;
      qc_tests         Lab ID -> the tests it holds qc verdicts of;
      qc_verdicts      what each of those verdicts judged
                       (`adoption_qc_verdicts`), so a QC print matches its
                       own verdicts whatever today's QC assignment is;
      first_ts         when LEM first recorded a reading from the bench;
      recent           run rows of the last ADOPTION_LEDGER_DAYS with the
                       values v3.9 FILED, for the results guard's ledger."""
    counts: Dict[str, int] = {}
    unreadable = set()
    qc_tests: Dict[str, set] = {}
    recent: List[dict] = []
    first: Optional[str] = None
    since = (now - timedelta(days=ADOPTION_LEDGER_DAYS)).strftime(
        "%Y-%m-%d %H:%M:%S")
    for r in rows:
        ts = str(r.get("ts") or "")
        # v3.9 wrote "YYYY-MM-DD HH:MM:SS", v4 ISO: compared as one.
        if ts and (first is None or ts.replace("T", " ")[:19]
                   < first.replace("T", " ")[:19]):
            first = ts
        detail = _adoption_detail(r.get("detail"))
        kind = str(r.get("kind") or "")
        lab = str(r.get("lab_id") or "").strip()
        raw = adoption_raw_values(kind, str(r.get("test_name") or ""),
                                  r.get("value"), detail) \
            if isinstance(detail, dict) else None
        if raw is None:
            unreadable.add(lab)
            continue
        h = adoption_hash(r.get("lab_id"), raw)
        counts[h] = counts.get(h, 0) + 1
        if kind == "qc":
            qc_tests.setdefault(lab, set()).add(str(r.get("test_name") or ""))
        if kind == "run" and ts.replace("T", " ")[:19] >= since \
                and isinstance(detail.get("values"), dict):
            recent.append({"h": h, "lab_id": r.get("lab_id"),
                           "values": detail["values"], "ts": ts})
    return {"rows": len(rows), "counts": counts,
            "unreadable_labs": sorted(unreadable),
            "qc_tests": {k: sorted(v) for k, v in qc_tests.items()},
            "qc_verdicts": adoption_qc_verdicts(rows),
            "first_ts": first, "recent": recent}


ADOPTION_RECIPE = ("sha256(canonical([lab_id, {test: v}]))[:32]; v a number "
                   "-> format(float, '.12g'), else stripped text; run row: "
                   "detail.values with detail.raw laid over them; qc row: "
                   "{test_name: detail.raw_value or detail.raw or '(no raw)'}; "
                   "canonical = JSON, sorted keys, ',' ':' separators, UTF-8")
#: §10.2 step 5: the bench seeds its results ledger from matched rows this recent.
ADOPTION_LEDGER_DAYS = 30


def token_hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime] = None) -> str:
    return (dt or _utcnow()).isoformat(timespec="seconds")


def wall_ts(value, fallback: str = "") -> str:
    """A record's `ts` as the record and current-state tables hold it: the
    bench's LOCAL wall time, without its offset — exactly what v3.9 wrote
    (`now.isoformat()` on the bench), and what every reader of those tables
    parses and compares with naive local times. The offset is not lost: the
    record's own body, offset included, is kept in `bench_record`.

    A `ts` that is not a date is not a time, and is never echoed: it is the
    `fallback` (the server's own clock, where the callers pass one). Before,
    the raw text came back, so a 4 MB "ts" became `updated_at` and from there
    every reader's `last_activity` — and `/api/machines`, which GC hub reads
    under a 1 MB cap."""
    if not isinstance(value, str):
        return fallback
    text = value.strip()
    if not text or len(text) > 64:
        return fallback
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    return dt.replace(tzinfo=None).isoformat(
        timespec="microseconds" if dt.microsecond else "seconds")


def _now_wall() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_moment(value) -> Optional[datetime]:
    try:
        text = str(value or "").strip().replace("Z", "+00:00")
        if not text:
            return None
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return None          # a clock without an offset cannot be compared
    return dt


# ── what /api/machines and /healthz know, in memory ──────────────────────────

#: The roads the spec names (§6.2). Anything else a bench calls its road is
#: "other": still a road, never the bench's own text — `/api/machines` echoes
#: this, and GC hub reads that payload under a 1 MB cap.
ROADS = ("lan", "public", "folder")
#: The largest count a bench can report and LEM will echo. Nine digits: a
#: bench a billion records behind is a bench to look at, not a number to read.
MAX_COUNT = 999_999_999
#: The counters a bench reports about its LabCore traffic (§6.1 stats).
LABCORE_COUNTERS = ("reads", "writes", "failed", "timeouts", "watchdog")


def bench_road(value) -> Optional[str]:
    """What a bench said its road was, as LEM holds and echoes it: a named
    road, "other", or None when it said nothing."""
    if value is None or value == "":
        return None
    if isinstance(value, str) and value.strip().lower() in ROADS:
        return value.strip().lower()
    return "other"


def bench_count(value) -> Optional[int]:
    """A count a bench reported, as LEM holds and echoes it: an int in
    0..MAX_COUNT, or None when it is not a count (negative, bool, NaN, text).
    Too big — `Infinity` included, which Python's json reads — is MAX_COUNT."""
    if isinstance(value, float):
        if value != value:                          # NaN
            return None
        if value in (float("inf"), float("-inf")):
            return MAX_COUNT if value > 0 else None
    n = _int_or(value, None)
    if n is None or n < 0:
        return None
    return min(n, MAX_COUNT)


def labcore_counts(value) -> dict:
    """The bench's LabCore counters: the named ones, each a bounded count."""
    if not isinstance(value, dict):
        return {}
    out = {}
    for key in LABCORE_COUNTERS:
        n = bench_count(value.get(key))
        if n is not None:
            out[key] = n
    return out


def _bounded(fields: dict) -> dict:
    """The registry's one door: whatever a caller passes, what is kept is
    what `/api/machines` and `/healthz` may echo."""
    out = dict(fields)
    if "road" in out:
        out["road"] = bench_road(out["road"])
    if "unacked" in out:
        out["unacked"] = bench_count(out["unacked"])
    if "waiting" in out:
        out["waiting"] = bench_count(out["waiting"])
    if "labcore" in out:
        out["labcore"] = labcore_counts(out["labcore"])
    if "module_version" in out and out["module_version"] is not None:
        out["module_version"] = str(out["module_version"])[:40]
    return out

class BenchRegistry:
    """The last thing each v2 bench said about itself. Memory only, so the
    floor's `transfer` field and `/healthz` cost nothing per request; filled
    from `bench_cursor` once (`hydrate`) so a restart does not forget which
    benches are v2."""

    def __init__(self, clock=time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: Dict[str, dict] = {}
        self.hydrated = False
        self._retry_at = 0.0

    def note(self, uid: str, **fields) -> None:
        with self._lock:
            entry = dict(self._entries.get(uid) or {})
            entry.update(_bounded(fields))
            entry.setdefault("seen", self._clock())
            self._entries[uid] = entry

    def get(self, uid: str) -> Optional[dict]:
        with self._lock:
            entry = self._entries.get(str(uid or ""))
            return dict(entry) if entry else None

    def hydrate(self, store) -> None:
        """Once. A failed read leaves it un-hydrated, to be tried again: a
        bench we could not look up is not a bench that has never synced."""
        if self.hydrated or self._clock() < self._retry_at:
            return
        try:
            res = store.read_sql(
                "SELECT c.machine_uid, c.bench_epoch, c.acked_seq, "
                "c.last_seen, c.road, c.module_version, c.stats, "
                "c.digest_mismatch FROM bench_cursor c WHERE c.last_seen = "
                "(SELECT MAX(last_seen) FROM bench_cursor d "
                "WHERE d.machine_uid = c.machine_uid)")
        except Exception as exc:                        # noqa: BLE001
            res = {"error": "%s: %s" % (type(exc).__name__, exc)}
        if not isinstance(res, dict) or res.get("error"):
            # Tried again in a minute, not on every floor poll.
            self._retry_at = self._clock() + 60.0
            logger.warning("bench registry: could not read bench_cursor: %s",
                           (res or {}).get("error") if isinstance(res, dict)
                           else res)
            return
        with self._lock:
            for row in res.get("rows") or []:
                uid = str(row.get("machine_uid") or "")
                if not uid or uid in self._entries:
                    continue
                seen = _parse_moment(row.get("last_seen"))
                stats = _json_or(row.get("stats"), {})
                # Through the same door as `note`: a row an older build (or
                # a person) left in the store must not reopen the hole.
                # What still waits once this sync was acked: the bench's
                # newest seq less what LEM holds (None when it never said).
                total = bench_count(stats.get("records_total"))
                acked_n = bench_count(row.get("acked_seq"))
                self._entries[uid] = _bounded({
                    "epoch": row.get("bench_epoch"),
                    "acked": row.get("acked_seq"),
                    "road": row.get("road"),
                    "module_version": row.get("module_version"),
                    "unacked": stats.get("unacked"),
                    "waiting": (max(0, total - acked_n)
                                if total is not None and acked_n is not None
                                else None),
                    "labcore": stats.get("labcore"),
                    "digest_mismatch": bool(row.get("digest_mismatch")),
                    "seen": seen.timestamp() if seen else None,
                })
            self.hydrated = True

    def summary(self) -> dict:
        now = self._clock()
        with self._lock:
            entries = list(self._entries.values())
        reporting = [e for e in entries if e.get("seen") is not None
                     and now - float(e["seen"]) <= REPORTING_SECONDS]
        lc = [e.get("labcore") or {} for e in reporting]
        if not self.hydrated:
            # `bench_cursor` has not been read since this process started
            # (or the read failed). What is in memory is the benches that
            # synced since, not the fleet: every count is unknown (null),
            # never a 0 that reads as "no benches, nothing waiting".
            return {"v2": None, "reporting": None, "lagging": None,
                    "unacked_total": None, "digest_mismatch": None,
                    "labcore_failed_5min": None,
                    "labcore_watchdog_5min": None, "hydrated": False}
        return {
            "v2": len(entries),
            "reporting": len(reporting),
            "lagging": len(entries) - len(reporting),
            "unacked_total": min(MAX_COUNT, sum(
                bench_count(e.get("unacked")) or 0 for e in entries)),
            "digest_mismatch": sum(1 for e in entries if e.get("digest_mismatch")),
            "labcore_failed_5min": min(MAX_COUNT, sum(
                bench_count(x.get("failed")) or 0 for x in lc)),
            "labcore_watchdog_5min": min(MAX_COUNT, sum(
                bench_count(x.get("watchdog")) or 0 for x in lc)),
            "hydrated": self.hydrated,
        }


def transfer_field(entry: Optional[dict], now: Optional[float] = None
                   ) -> Optional[dict]:
    """`/api/machines`' one additive field (§12.3). None for a bench that has
    never synced over v2: nobody has asked it what it holds, which is not the
    same as "0 waiting"."""
    if not entry:
        return None
    now = time.time() if now is None else now
    seen = entry.get("seen")
    return {"road": bench_road(entry.get("road")),
            "unacked": bench_count(entry.get("unacked")),
            "last_sync_age_s": (round(max(0.0, now - float(seen)), 1)
                                if seen is not None else None)}


def _int_or(value, default):
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        # OverflowError: int(float('inf')). Python's json reads `Infinity`,
        # and an exception here inside a sync's transaction would roll back
        # every sync from that bench.
        return default


def _json_or(text, default):
    if isinstance(text, (dict, list)):
        return text
    try:
        out = json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
    return out if isinstance(out, type(default)) else default


# ── failures, named ──────────────────────────────────────────────────────────

class BenchRefusal(Exception):
    """An answer other than 200, with its status and body."""

    def __init__(self, status: int, error: str, retry_after: Optional[int] = None,
                 **extra) -> None:
        super().__init__(error)
        self.status = status
        self.error = error
        self.retry_after = retry_after
        self.extra = extra


class StoreFailed(Exception):
    """A store statement answered `{"error": ...}`; the transaction rolls
    back. Carries the answer so 503's `Retry-After` can honour `busy`."""

    def __init__(self, result) -> None:
        super().__init__(str((result or {}).get("error") if isinstance(
            result, dict) else result))
        self.result = result


def _bad(error: str) -> BenchRefusal:
    return BenchRefusal(400, error)


# ── reading a request ────────────────────────────────────────────────────────

def _read_body(request, cap: int, inflated_cap: int) -> bytes:
    length = request.content_length
    if length is not None and length > cap:
        raise _bad("the body is %d bytes; at most %d are read" % (length, cap))
    data = request.stream.read(cap + 1) if length is None else request.get_data()
    if len(data) > cap:
        raise _bad("the body is larger than %d bytes" % cap)
    if (request.headers.get("Content-Encoding") or "").strip().lower() == "gzip":
        try:
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
            data = d.decompress(data, inflated_cap + 1)
            if len(data) > inflated_cap or d.unconsumed_tail:
                raise _bad("the body inflates past %d bytes" % inflated_cap)
        except zlib.error as exc:
            raise _bad("the body says gzip and is not: %s" % exc)
    return data


def parse_sync(uid: str, data: bytes) -> dict:
    """The sync body, checked. Raises BenchRefusal(400) naming what is wrong
    — a bench holds on 400 and logs it, so the words are its diagnosis."""
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _bad("the body is not JSON: %s" % exc)
    if not isinstance(doc, dict):
        raise _bad("the body is not a JSON object")
    if str(doc.get("machine_uid") or "") != uid:
        raise _bad("machine_uid %r does not match the URL's %r"
                   % (doc.get("machine_uid"), uid))
    if doc.get("proto") not in ACCEPTED_PROTOS:
        raise _bad("proto %r is not one this server speaks (%s)"
                   % (doc.get("proto"), list(ACCEPTED_PROTOS)))
    epoch = doc.get("epoch")
    if not isinstance(epoch, str) or not epoch.strip() or len(epoch) > 128:
        raise _bad("epoch must be a non-empty string")
    from_seq = doc.get("from_seq")
    if isinstance(from_seq, bool) or not isinstance(from_seq, int) or from_seq < 1:
        raise _bad("from_seq must be a whole number of at least 1, not %r"
                   % (from_seq,))
    records = doc.get("records", [])
    if not isinstance(records, list):
        raise _bad("records must be a list")
    if len(records) > MAX_RECORDS:
        raise _bad("%d records in one sync; at most %d" % (len(records),
                                                           MAX_RECORDS))
    checked = []
    for i, rec in enumerate(records):
        where = "record %d" % i
        if not isinstance(rec, dict):
            raise _bad("%s is not an object" % where)
        seq = rec.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) \
                or seq != from_seq + i:
            raise _bad("%s has seq %r; records must be contiguous from "
                       "from_seq %d" % (where, seq, from_seq))
        if rec.get("epoch") != epoch:
            raise _bad("%s (seq %d) is of epoch %r, not this sync's epoch %r"
                       % (where, seq, rec.get("epoch"), epoch))
        if rec.get("uid") != uid:
            raise _bad("%s (seq %d) belongs to uid %r, not %r"
                       % (where, seq, rec.get("uid"), uid))
        kind = rec.get("kind")
        if not isinstance(kind, str) or not kind:
            raise _bad("%s (seq %d) has no kind" % (where, seq))
        crc = rec.get("crc")
        body = {k: v for k, v in rec.items() if k != "crc"}
        raw = canonical(body)
        if not isinstance(crc, str) or crc != record_crc(raw):
            raise _bad("%s (seq %d) fails its crc: it is not what the bench "
                       "journaled" % (where, seq))
        checked.append((seq, body, raw))
    stats = doc.get("stats") if isinstance(doc.get("stats"), dict) else {}
    live = doc.get("live") if isinstance(doc.get("live"), dict) else None
    sources = [s for s in (doc.get("sources") or [])
               if isinstance(s, dict) and isinstance(s.get("src"), str)
               and s.get("src")] if isinstance(doc.get("sources"), list) else []
    return {"epoch": epoch, "from_seq": from_seq, "records": checked,
            "stats": stats, "live": live, "sources": sources,
            "bench_clock": doc.get("bench_clock"),
            "module_version": str(doc.get("module_version") or "")[:40]}


# ── one record into the store ───────────────────────────────────────────────

def _row_detail(detail, jk: str) -> str:
    """A log row's detail with the record's key `jk` added — the link from
    every row back to the record it came from (§7's `detail.jk`)."""
    parsed = detail
    if isinstance(detail, str):
        try:
            parsed = json.loads(detail) if detail else {}
        except ValueError:
            return detail
    if not isinstance(parsed, dict):
        return detail if isinstance(detail, str) else json.dumps(detail)
    parsed = dict(parsed)
    parsed["jk"] = jk
    return json.dumps(parsed)


def _content(body: dict) -> dict:
    return {k: v for k, v in body.items() if k not in ENVELOPE}


def log_rows_for(body: dict) -> Optional[List[tuple]]:
    """(ts, kind, lab_id, test_name, value, detail) for each machine-log row
    a record makes, in order. None when the record carries rows that cannot
    be read — it is then parked, never guessed."""
    kind = body["kind"]
    ts = wall_ts(body.get("ts"), _now_wall())
    lab_id = str(body.get("lab_id") or "")
    log = body.get("log")
    if kind in ("run", "qc") and isinstance(log, list) and log:
        rows = []
        for entry in log:
            if not isinstance(entry, list) or len(entry) != 7 \
                    or str(entry[0]) != str(body.get("uid")):
                return None
            _uid, r_ts, r_kind, r_lab, r_test, r_value, r_detail = entry
            # The row's own ts exactly as the bench wrote it (v3.9's
            # "YYYY-MM-DD HH:MM:SS" included: matches on it are textual) —
            # when it IS a date. Otherwise the record's: `last_activity` is
            # MAX(ts) of the log, and a 200 KB string of nines sorts last.
            r_ts = r_ts.strip() if isinstance(r_ts, str) else ""
            if wall_ts(r_ts, None) is None:
                r_ts = ts
            rows.append((r_ts, str(r_kind or kind), str(r_lab or ""),
                         str(r_test or ""), "" if r_value is None else str(r_value),
                         r_detail if r_detail is not None else "{}"))
        return rows
    if kind == "run":
        detail = {"values": body.get("values") or {}}
        if body.get("raw"):
            detail["raw"] = body.get("raw")
            detail["corrections"] = body.get("corrections") or {}
        if body.get("origin") and body.get("origin") != "live":
            detail["origin"] = body.get("origin")
        return [(ts, "run", lab_id, "", "", detail)]
    if kind == "qc":
        test = str(body.get("test_name") or "")
        values = body.get("values") if isinstance(body.get("values"), dict) else {}
        value = body.get("value", values.get(test, ""))
        detail = {k: body[k] for k in ("verdict", "spec", "window_hours",
                                       "window_source", "operator",
                                       "calibration_id", "raw", "values",
                                       "corrections", "origin")
                  if k in body}
        return [(ts, "qc", lab_id, test, "" if value is None else str(value),
                 detail)]
    if kind == "state":
        return [(ts, "status_change", "", "", "",
                 {"from": str(body.get("from") or ""),
                  "to": str(body.get("status") or ""),
                  "reason": str(body.get("reason") or ""),
                  "sub": body.get("sub") if isinstance(body.get("sub"), dict)
                  else {}})]
    if kind == "given_up":
        return [(ts, "held_expired", lab_id, str(body.get("test_name") or ""),
                 "", _content(body))]
    if kind == "conflict":
        rows = []
        for cell in body.get("cells") or []:
            c = _cell(cell, ("lab_id", "test_name", "ours", "theirs",
                             "their_updated_at", "their_operator"))
            rows.append((ts, "result_conflict", c["lab_id"], c["test_name"],
                         c["ours"], dict(c, of=body.get("of"))))
        return rows or None
    # comment, override, pm, calibration, config: today's detail
    detail = body.get("detail") if isinstance(body.get("detail"), dict) \
        else {k: v for k, v in _content(body).items()
              if k not in ("lab_id", "test_name", "value")}
    value = body.get("value")
    return [(ts, kind, lab_id, str(body.get("test_name") or ""),
             "" if value is None else str(value), detail)]


def _text(value) -> str:
    """A record's text field as text; nothing said is ""."""
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, default=str)


def _finite(value) -> Optional[float]:
    """A spec number: finite, or None ("not said"). A bool is not a number;
    neither is Infinity, NaN, or a 4,000-digit string SQLite would store as
    Infinity — which is not JSON, and `/api/machines` must stay JSON."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _moment_text(value) -> Optional[str]:
    """A date as the bench wrote it when it is a naive local time (the floor
    matches it TEXTUALLY against log rows), as `wall_ts` makes it when it
    carries an offset, and None when it is not a date at all."""
    held = wall_ts(value, None)
    if held is None:
        return None
    text = value.strip()
    try:
        naive = datetime.fromisoformat(text).tzinfo is None
    except ValueError:                       # "Z": Python < 3.11 spelling
        naive = False
    return text if naive else held


def spec_set(specs: list) -> Tuple[List[dict], Optional[str]]:
    """A `specs` record's set, checked: (the rows to store, None), or
    ([], why it cannot be applied). Keys are refused, never clipped; units
    (a display label) are clipped; numbers are finite or None."""
    usable = [s for s in specs if isinstance(s, dict) and s.get("test_name")]
    if len(usable) > MAX_SPECS:
        return [], "%d specs, more than the %d one bench may set" % (
            len(usable), MAX_SPECS)
    out = []
    for spec in usable:
        for key in ("test_name", "sample_id"):
            value = spec.get(key)
            if value is None and key == "sample_id":
                continue
            if not isinstance(value, str):
                return [], "a spec's %s is not text" % key
            if clip_text(value, SPEC_TEXT_BYTES[key]) != value:
                return [], "a spec's %s is longer than %d bytes" % (
                    key, SPEC_TEXT_BYTES[key])
        row = {}
        for c in SPEC_COLUMNS:
            if c not in spec:
                continue
            v = spec[c]
            if c in SPEC_NUMBERS:
                row[c] = _finite(v)
            elif c == "last_qc_in_spec":
                row[c] = (int(v) if isinstance(v, bool) or v in (0, 1)
                          else None)
            elif c == "last_qc_at":
                row[c] = _moment_text(v)
            elif c == "units":
                row[c] = clip_text(_text(v), SPEC_TEXT_BYTES["units"])
            else:
                row[c] = v
        out.append(row)
    return out, None


def _cell(cell, names) -> dict:
    if isinstance(cell, dict):
        out = {n: cell.get(n, cell.get("test") if n == "test_name" else None)
               for n in names}
    elif isinstance(cell, (list, tuple)):
        out = {n: (cell[i] if i < len(cell) else None)
               for i, n in enumerate(names)}
    else:
        out = {n: None for n in names}
    return {k: ("" if v is None else str(v)) for k, v in out.items()}


class Ingest:
    """One sync's writes. Lives inside the store's transaction; every
    statement's answer is checked, and the first error rolls it all back."""

    def __init__(self, store, uid: str, epoch: str, now: str) -> None:
        self.store = store
        self.uid = uid
        self.epoch = epoch
        self.now = now
        self.notes: List[dict] = []
        #: A record changed the bench's configuration (config, factors, the
        #: override): the snapshot is refreshed after the commit, so the next
        #: config_rev says so.
        self.config_changed = False

    def x(self, sql: str, args: list) -> dict:
        res = self.store.sql(sql, args)
        if not isinstance(res, dict) or res.get("error"):
            raise StoreFailed(res)
        return res

    def q(self, sql: str, args: list) -> List[dict]:
        res = self.store.read_sql(sql, args)
        if not isinstance(res, dict) or res.get("error") or "rows" not in res:
            raise StoreFailed(res)
        return res["rows"]

    def record(self, seq: int, body: dict, raw: bytes) -> bool:
        """Store one record. False if it was already held (nothing written)."""
        uid, epoch = self.uid, self.epoch
        self._raw = raw
        kind = body["kind"]
        res = self.x(
            "INSERT INTO bench_record (machine_uid, bench_epoch, bench_seq, "
            "kind, ts, body) SELECT ?, ?, ?, ?, ?, ? WHERE NOT EXISTS "
            "(SELECT 1 FROM bench_record WHERE machine_uid = ? AND "
            "bench_epoch = ? AND bench_seq = ?)",
            [uid, epoch, seq, kind, str(body.get("ts") or ""),
             raw.decode("utf-8"), uid, epoch, seq])
        if not res.get("rows_affected"):
            return False
        if kind in LOG_KINDS:
            rows = log_rows_for(body)
            if rows is None:
                self.park(seq, raw, "its log rows could not be read")
            elif rows:
                self.log(seq, body, rows)
        elif kind not in BOOKKEEPING_KINDS:
            self.park(seq, raw, None)
        handler = getattr(self, "_state_" + kind, None)
        if handler is not None:
            handler(seq, body)
        return True

    def park(self, seq: int, raw: bytes, why: Optional[str]) -> None:
        self.x("INSERT INTO unknown_records (machine_uid, bench_epoch, "
               "bench_seq, body, received_at) SELECT ?, ?, ?, ?, ? WHERE NOT "
               "EXISTS (SELECT 1 FROM unknown_records WHERE machine_uid = ? "
               "AND bench_epoch = ? AND bench_seq = ?)",
               [self.uid, self.epoch, seq, raw.decode("utf-8"), self.now,
                self.uid, self.epoch, seq])
        if why:
            self.notes.append({"kind": "parked", "seq": seq, "why": why})

    def log(self, seq: int, body: dict, rows: List[tuple]) -> None:
        uid, epoch = self.uid, self.epoch
        jk = "%s:%d" % (epoch, seq)
        origin = {"recovered": "recovered", "ambiguous": "ambiguous"}.get(
            str(body.get("origin") or ""), "bench")
        key = body.get("lh") if isinstance(body.get("lh"), str) else None
        ts, kind, lab_id, test, value, detail = rows[0]
        first = self.x(
            "INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
            "test_name, value, detail, origin, bench_epoch, bench_seq, "
            "content_key) SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ? WHERE NOT "
            # raw-log: ingest — the custody key is unique over EVERY row,
            # hidden or not, so the existence check must see every row
            "EXISTS (SELECT 1 FROM lem_machine_log WHERE machine_uid = ? AND "
            "bench_epoch = ? AND bench_seq = ?)",
            [uid, ts, kind, lab_id, test, value, _row_detail(detail, jk),
             origin, epoch, seq, key, uid, epoch, seq])
        custody = self.q(
            # raw-log: ingest reads back the row it just wrote
            "SELECT id FROM lem_machine_log WHERE machine_uid = ? AND "
            "bench_epoch = ? AND bench_seq = ?", [uid, epoch, seq])
        ids = [custody[0]["id"]] if custody else []
        rest = rows[1:]
        if not first.get("rows_affected"):
            # The record's custody row was already here: the bridge pulled it
            # from LabCore, where this bench projected it in legacy mode with
            # `detail.jk` (§10.3, M6). Its first rows came that way, in the
            # projection's own order; only the ones not yet pulled are added
            # here, and from now on the pull skips this record (it is held as
            # a bench record), so no row lands twice and none is missing.
            held = self.q(
                # raw-log: ingest counts every row of the record, hidden or not
                "SELECT COUNT(*) AS n FROM lem_machine_log WHERE machine_uid "
                "= ? AND bench_epoch = ? AND CASE WHEN json_valid(detail) "
                "THEN json_extract(detail, '$.jk') END = ?", [uid, epoch, jk])
            n_held = _int_or(held[0].get("n"), 1) if held else 1
            rest = rows[max(1, n_held):]
            ids = []
        for ts, kind, lab_id, test, value, detail in rest:
            self.x("INSERT INTO lem_machine_log (machine_uid, ts, kind, "
                   "lab_id, test_name, value, detail, origin, bench_epoch, "
                   "content_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   [uid, ts, kind, lab_id, test, value,
                    _row_detail(detail, jk), origin, epoch, key])
            last = self.q("SELECT last_insert_rowid() AS id", [])
            ids.append(last[0]["id"])
        if origin == "ambiguous":
            for log_id in ids:
                self.annotate(log_id, "ambiguous_repeat", "origin=ambiguous")
        if key and ids and body["kind"] in ("run", "qc"):
            self._replay_check(ids[0], key)

    def annotate(self, log_id: int, label: str, rule: str,
                 dup_of: Optional[int] = None) -> None:
        self.x("INSERT INTO log_annotation (log_id, label, dup_of, rule, by, "
               "at) VALUES (?, ?, ?, ?, 'lem-server', ?)",
               [log_id, label, dup_of, rule, self.now])

    def _replay_check(self, log_id: int, key: str) -> None:
        """§4.4: the k-th occurrence of a line hash in THIS epoch, when an
        earlier epoch already holds k or more of it, is a possible replay.
        Inserted (it already is) and labelled visibly; hiding needs a person
        (§10.5)."""
        rows = self.q(
            "SELECT SUM(CASE WHEN bench_epoch = ? THEN 1 ELSE 0 END) AS mine, "
            "SUM(CASE WHEN bench_epoch <> ? THEN 1 ELSE 0 END) AS earlier "
            # raw-log: ingest — an occurrence already recorded counts whether
            # or not an annotation has since hidden it
            "FROM lem_machine_log WHERE machine_uid = ? AND content_key = ? "
            "AND bench_seq IS NOT NULL", [self.epoch, self.epoch, self.uid, key])
        mine = _int_or(rows[0].get("mine"), 0) if rows else 0
        earlier = _int_or(rows[0].get("earlier"), 0) if rows else 0
        if earlier >= mine >= 1:
            self.annotate(log_id, "replay_candidate",
                          "content_key seen under an earlier epoch")

    # ── current state, same transaction ──
    # What a record puts in current state is echoed by `/api/machines`, which
    # GC hub reads under a 1 MB cap that fails its WHOLE floor when crossed.
    # So each value has the bound the live block gives the same fact
    # (live_presence.FLOOR_FIELD_BYTES), applied here so the store holds the
    # bounded value and a restart reopens nothing. The record is not cut:
    # `bench_record` holds the body as sent, and the log row keeps the whole
    # reason for a person to read.
    def _state_state(self, seq: int, body: dict) -> None:
        status = _text(body.get("status"))
        if not status:
            return
        ts = wall_ts(body.get("ts"), _now_wall())
        self.x("INSERT INTO lem_machine_status (machine_uid, title, status, "
               "reason, updated_at) VALUES (?, (SELECT title FROM "
               "lem_machine_config WHERE machine_uid = ?), ?, ?, ?) "
               "ON CONFLICT(machine_uid) DO UPDATE SET status = excluded.status, "
               "reason = excluded.reason, updated_at = excluded.updated_at",
               [self.uid, self.uid,
                clip_text(status, FLOOR_FIELD_BYTES["status"]),
                clip_text(_text(body.get("reason")),
                          FLOOR_FIELD_BYTES["reason"], ELLIPSIS), ts])
        sub = body.get("sub")
        if isinstance(sub, dict):
            def word(key):
                return clip_text(_text(sub.get(key)), FLOOR_FIELD_BYTES["sub"])
            self.x("INSERT INTO lem_machine_substatus (machine_uid, qc, pm, "
                   "calibration, updated_at) VALUES (?, ?, ?, ?, ?) "
                   "ON CONFLICT(machine_uid) DO UPDATE SET qc = excluded.qc, "
                   "pm = excluded.pm, calibration = excluded.calibration, "
                   "updated_at = excluded.updated_at",
                   [self.uid, word("qc"), word("pm"), word("calibration"), ts])

    def _state_specs(self, seq: int, body: dict) -> None:
        specs = body.get("specs")
        if not isinstance(specs, list):
            return
        checked, why = spec_set(specs)
        if why:
            # Test name and sample are the keys QC matches on: a clipped key
            # is a band for a test nobody runs, and a partial set is a band
            # missing. So the set is neither cut nor applied — the bench's
            # previous set stays in force — and that is written down where a
            # person looks (unknown_records) and said in the answer's notes.
            self.park(seq, self._raw, "its spec set was not applied: %s; "
                                      "the previous set stays in force" % why)
            return
        # Whole-set replace in one transaction: no "no band" window (§7).
        self.x("DELETE FROM lem_machine_specs WHERE machine_uid = ?", [self.uid])
        updated = wall_ts(body.get("ts"), _now_wall())
        for spec in checked:
            cols = list(spec)
            self.x("INSERT OR REPLACE INTO lem_machine_specs (machine_uid, "
                   "updated_at, {0}) VALUES (?, ?, {1})".format(
                       ", ".join(cols), ", ".join("?" for _ in cols)),
                   [self.uid, updated] + [spec[c] for c in cols])

    def _state_conflict(self, seq: int, body: dict) -> None:
        for i, cell in enumerate(body.get("cells") or []):
            c = _cell(cell, ("lab_id", "test_name", "ours", "theirs",
                             "their_updated_at", "their_operator"))
            ref = "%s:%d" % (self.epoch, seq) + ("#%d" % i if i else "")
            self.x("INSERT INTO result_conflict (bench_seq_ref, machine_uid, "
                   "lab_id, test_name, ours, theirs, their_updated_at, "
                   "their_operator, opened_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                   "ON CONFLICT(bench_seq_ref) DO NOTHING",
                   [ref, self.uid, c["lab_id"], c["test_name"], c["ours"],
                    c["theirs"], c["their_updated_at"], c["their_operator"],
                    wall_ts(body.get("ts"), _now_wall())])

    def _state_filed(self, seq: int, body: dict) -> None:
        for cell in body.get("cells") or []:
            c = _cell(cell, ("lab_id", "test_name", "value", "expect"))
            if not c["lab_id"] or not c["test_name"]:
                continue
            self.x("INSERT INTO result_ledger (machine_uid, lab_id, test_name, "
                   "value, filed_at, bench_seq_ref) VALUES (?, ?, ?, ?, ?, ?) "
                   "ON CONFLICT(lab_id, test_name, machine_uid) DO UPDATE SET "
                   "value = excluded.value, filed_at = excluded.filed_at, "
                   "bench_seq_ref = excluded.bench_seq_ref",
                   [self.uid, c["lab_id"], c["test_name"], c["value"],
                    wall_ts(body.get("ts"), _now_wall()), "%s:%d" % (self.epoch, seq)])

    # ── edits made AT THE BENCH (transfer §2: "module setup dialog (`config`
    # record)") — a v2 bench writes none of these into LabCore any more, so
    # this is where they become LEM's current state. Applied only when the
    # bench's change is not older than what the store holds: a record held
    # back by an outage must not undo a newer edit made on the floor.
    def _newer_than(self, table: str, where: str, args: list, ts: str) -> bool:
        rows = self.q("SELECT updated_at FROM %s WHERE %s" % (table, where),
                      args)
        held = str((rows[0] if rows else {}).get("updated_at") or "")
        return not held or ts >= wall_ts(held, held)

    def _state_config(self, seq: int, body: dict) -> None:
        ts = wall_ts(body.get("ts"), _now_wall())
        detail = body.get("detail") if isinstance(body.get("detail"), dict) else {}
        by = clip_text(_text(detail.get("by") or "bench"), 64)
        machine = body.get("machine")
        if isinstance(machine, dict) and str(machine.get("uid") or "") == self.uid:
            text = json.dumps(machine, sort_keys=True, default=str)
            title = clip_text(_text(machine.get("title")).strip(), 200) or self.uid
            if len(text) <= MAX_MACHINE_CONFIG_BYTES and self._newer_than(
                    "lem_machine_config", "machine_uid = ?", [self.uid], ts):
                self.x("INSERT INTO lem_machine_config (machine_uid, title, "
                       "config, updated_at, updated_by) VALUES (?, ?, ?, ?, ?) "
                       "ON CONFLICT(machine_uid) DO UPDATE SET title = "
                       "excluded.title, config = excluded.config, updated_at = "
                       "excluded.updated_at, updated_by = excluded.updated_by",
                       [self.uid, title, text, ts, "bench: " + by])
                self.config_changed = True
        changes = body.get("corrections")
        if isinstance(changes, dict):
            for test, value in list(changes.items())[:MAX_SPECS]:
                test = _text(test).strip()
                if not test or clip_text(test, SPEC_TEXT_BYTES["test_name"]) != test:
                    continue
                if not self._newer_than("lem_correction_factors",
                                        "machine_uid = ? AND test_name = ?",
                                        [self.uid, test], ts):
                    continue
                if value in (None, 0, 0.0, "", "0"):
                    self.x("DELETE FROM lem_correction_factors WHERE "
                           "machine_uid = ? AND test_name = ?", [self.uid, test])
                    self.config_changed = True
                    continue
                number = _finite(value)
                if number is None:
                    continue       # logged as sent; never stored as a factor
                self.x("INSERT INTO lem_correction_factors (machine_uid, "
                       "test_name, correction, updated_at, updated_by) VALUES "
                       "(?, ?, ?, ?, ?) ON CONFLICT(machine_uid, test_name) DO "
                       "UPDATE SET correction = excluded.correction, updated_at "
                       "= excluded.updated_at, updated_by = excluded.updated_by",
                       [self.uid, test, number, ts, by])
                self.config_changed = True

    def _state_override(self, seq: int, body: dict) -> None:
        detail = body.get("detail") if isinstance(body.get("detail"), dict) else {}
        status = _text(detail.get("status")).strip()
        status = "" if status in ("", "cleared") else status
        if status not in BENCH_OVERRIDES:
            return                 # in the log as sent; not a state
        ts = wall_ts(body.get("ts"), _now_wall())
        if not self._newer_than("lem_machine_control", "machine_uid = ?",
                                [self.uid], ts):
            return
        self.x("INSERT INTO lem_machine_control (machine_uid, manual_override, "
               "comment, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT("
               "machine_uid) DO UPDATE SET manual_override = "
               "excluded.manual_override, comment = excluded.comment, "
               "updated_at = excluded.updated_at",
               [self.uid, status, clip_text(_text(detail.get("comment")), 2000),
                ts])
        self.config_changed = True

    def _state_resolution(self, seq: int, body: dict) -> None:
        # The module journals `conflict_ref` (lem_station_module Journal
        # `_apply`); `conflict_seq` is the name the sync answer hands out.
        # Either one names the decision this record delivers.
        ref = str(body.get("conflict_ref") or body.get("conflict_seq") or "")
        if ref:
            # The bench journaled the person's decision: delivered.
            self.x("UPDATE result_conflict SET delivered_at = ? WHERE "
                   "bench_seq_ref = ? AND delivered_at IS NULL", [self.now, ref])


# ── the routes ───────────────────────────────────────────────────────────────

#: Shorter than this, an `enroll_key` is guessable and is treated as absent.
MIN_ENROLL_KEY = 16
MAX_ENROLL_KEY = 256


def enroll_key_of(body) -> Optional[str]:
    """The hash of the bench's `enroll_key`, or None when it sent none (or one
    too short to be a secret). A bench makes the key once per enrolment and
    sends it with every retry of that enrolment, so a lost 200 is answered
    again instead of becoming "already enrolled; needs a person" (§6.4)."""
    key = (body or {}).get("enroll_key") if isinstance(body, dict) else None
    if not isinstance(key, str) or not \
            MIN_ENROLL_KEY <= len(key) <= MAX_ENROLL_KEY:
        return None
    return token_hash(key)


def ensure_current_state_tables(store) -> bool:
    """The current-state tables a sync updates, declared with their owner's
    exact DDL (snapshot_service) so a sync on a fresh store does not fail on
    a table the snapshot has not declared yet. Called before the first sync,
    never at app creation: a factory that writes costs every boot (and every
    test) statements nobody asked for. True when all are declared."""
    if getattr(store, "read_only", False):
        return False
    from snapshot_service import SCHEMA_DDL
    ok = True
    for stmt in SCHEMA_DDL:
        for table in _CURRENT_STATE_TABLES:
            if stmt.startswith("CREATE TABLE IF NOT EXISTS %s " % table) or \
                    stmt.startswith("CREATE TABLE IF NOT EXISTS %s(" % table):
                try:
                    res = store.sql(stmt)
                except Exception as exc:                # noqa: BLE001
                    res = {"error": str(exc)}
                if not isinstance(res, dict) or res.get("error"):
                    ok = False
                    logger.warning("bench api: could not declare %s: %s",
                                   table, (res or {}).get("error"))
    return ok


def register(app, store, *, snapshots, live, registry: BenchRegistry,
             authed, current_user, version: str) -> None:
    """Add the v2 bench routes to `app`. `store` is LEM's store; LabCore is
    not a parameter on purpose."""
    from flask import Response, jsonify, request

    declared = {"done": False}

    def _answer(status: int, body: dict, retry_after=None):
        resp = jsonify(body)
        resp.status_code = status
        if retry_after is not None:
            resp.headers["Retry-After"] = str(int(retry_after))
        return resp

    def _refused(exc: BenchRefusal):
        body = {"error": exc.error}
        body.update(exc.extra)
        return _answer(exc.status, body, exc.retry_after)

    def _store_failed(what: str, result):
        busy = isinstance(result, dict) and result.get("busy")
        logger.warning("bench api: %s: the store refused: %s", what,
                       result.get("error") if isinstance(result, dict) else result)
        return _answer(503, {"error": "LEM's store could not %s just now; "
                                      "nothing was recorded. Hold and retry."
                                      % what},
                       RETRY_BUSY_S if busy else 30)

    def _gz(resp):
        if len(resp.get_data()) > GZIP_ABOVE and "gzip" in (
                request.headers.get("Accept-Encoding") or "").lower():
            resp.set_data(gzip.compress(resp.get_data()))
            resp.headers["Content-Encoding"] = "gzip"
            resp.headers["Vary"] = "Accept-Encoding"
        return resp

    def _read_one(sql: str, args: list) -> Optional[dict]:
        res = store.read_sql(sql, args)
        if not isinstance(res, dict) or res.get("error") or "rows" not in res:
            raise StoreFailed(res)
        return res["rows"][0] if res["rows"] else None

    def _bench_auth(uid: str) -> None:
        """The bench's own token, compared by hash. 401 for anything else —
        an unknown uid included: that is an enrolment question, never 404."""
        supplied = request.headers.get("X-LEM-Bench-Token", "")
        if not supplied:
            raise BenchRefusal(401, "no bench token: enrol this bench "
                                    "(POST /api/v2/bench/<uid>/enroll)")
        row = _read_one("SELECT token_sha256, revoked_at FROM bench_token "
                        "WHERE machine_uid = ?", [uid])
        good = (row is not None and row.get("token_sha256")
                and not row.get("revoked_at")
                and hmac.compare_digest(str(row["token_sha256"]),
                                        token_hash(supplied)))
        if not good:
            raise BenchRefusal(401, "LEM does not recognise this bench's token "
                                    "for %s: re-enrol" % uid)

    def _hold() -> Optional[str]:
        """Why syncs are held right now, or None. A hold that cannot be read
        is a hold: a store that cannot answer this cannot take records."""
        if getattr(store, "read_only", False):
            raise BenchRefusal(503, "this LEM server has its store open "
                                    "read-only (a candidate boot); hold and "
                                    "retry", RETRY_READ_ONLY_S)
        row = _read_one("SELECT value FROM store_meta WHERE key = 'sync_hold'",
                        [])
        if row and str(row.get("value") or "").strip():
            raise BenchRefusal(503, "LEM is not taking bench records yet: %s"
                               % row["value"], RETRY_HOLD_S)
        return None

    # ── config ──
    def _machine_config(uid: str) -> Tuple[Optional[dict], Optional[str]]:
        row = _read_one("SELECT config, retired_at FROM lem_machine_config "
                        "WHERE machine_uid = ?", [uid])
        if row is None:
            return None, None
        return _json_or(row.get("config"), {}), row.get("retired_at")

    def _config_v1(uid: str) -> Optional[dict]:
        snap = snapshots.get(build_if_missing=False)
        if not snap.get("ready"):
            return None
        from snapshot_service import bench_config_from_tables
        return bench_config_from_tables(snapshots.tables(), uid)

    def _config_rev(v1: Optional[dict], machine_config) -> Optional[str]:
        if v1 is None:
            return None
        return hashlib.sha256(canonical({"v1": v1, "machine_config":
                                         machine_config})).hexdigest()[:32]

    def _last_qc(uid: str) -> List[dict]:
        res = store.read_sql(
            "SELECT test_name, lab_id, ts, value, detail FROM "
            "lem_machine_log_effective WHERE machine_uid = ? AND kind = 'qc' "
            "AND test_name != '' ORDER BY ts, id", [uid])
        if not isinstance(res, dict) or res.get("error") or "rows" not in res:
            raise StoreFailed(res)
        newest: Dict[tuple, dict] = {}
        for r in res["rows"]:
            detail = _json_or(r.get("detail"), {})
            in_spec = detail.get("in_spec")
            newest[(r.get("test_name"), r.get("lab_id"))] = {
                "test_name": r.get("test_name"), "lab_id": r.get("lab_id"),
                "ts": r.get("ts"), "value": r.get("value"),
                "verdict": detail.get("verdict"),
                # What the station module itself writes (qc_log_detail): a
                # v2 bench rebuilds its QC memory from this after a restart.
                "in_spec": in_spec if isinstance(in_spec, bool) else None}
        return [newest[k] for k in sorted(newest, key=lambda k: (str(k[0]),
                                                                str(k[1])))]

    @app.route("/api/v2/ping")
    def bench_v2_ping():
        return jsonify({"proto": list(ACCEPTED_PROTOS), "version": version,
                        "server_time": _iso()})

    @app.route("/api/v2/bench/<uid>/config")
    def bench_v2_config(uid):
        try:
            _bench_auth(uid)
            v1 = _config_v1(uid)
            if v1 is None:
                return _answer(503, {"error": "The snapshot has not built yet.",
                                     "stale": True}, RETRY_BUSY_S)
            machine_config, retired = _machine_config(uid)
            body = dict(v1)
            body["snapshot_age_seconds"] = snapshots.get(
                build_if_missing=False).get("age_seconds")
            body["config_rev"] = _config_rev(v1, machine_config)
            body["machine_config"] = machine_config
            body["machine"] = "retired" if retired else "active"
            body["last_qc"] = _last_qc(uid)
            return _gz(jsonify(body))
        except BenchRefusal as exc:
            return _refused(exc)
        except StoreFailed as exc:
            return _store_failed("read this bench's configuration", exc.result)

    @app.route("/api/v2/bench/<uid>/checkpoint")
    def bench_v2_checkpoint(uid):
        """§6.5: what the server holds of this bench, for a bench whose
        journal is gone. Every part is read or the whole answer is 503: a
        checkpoint that says "nothing" because a read failed would make the
        bench re-send its whole file."""
        try:
            _hold()             # a record still being imported is not one
            _bench_auth(uid)

            def rows(sql, args):
                res = store.read_sql(sql, args)
                if not isinstance(res, dict) or res.get("error") \
                        or "rows" not in res:
                    raise StoreFailed(res)
                return res["rows"]
            sources = []
            for r in rows("SELECT src, lineage, cursor, snapshot, snapshot_sha, "
                          "announced_sha, updated_at FROM bench_source WHERE "
                          "machine_uid = ? ORDER BY src", [uid]):
                blob = r.get("snapshot")
                sources.append({
                    "src": r.get("src"), "lineage": r.get("lineage"),
                    "cursor": _json_or(r.get("cursor"), {}),
                    "snapshot": base64.b64encode(bytes(blob)).decode("ascii")
                    if blob is not None else None,
                    "snapshot_sha": r.get("snapshot_sha"),
                    "announced_sha": r.get("announced_sha"),
                    "updated_at": r.get("updated_at")})
            epochs = [{"epoch": r["bench_epoch"], "acked": r["acked_seq"],
                       "durable": r["durable_seq"], "last_seen": r["last_seen"]}
                      for r in rows("SELECT bench_epoch, acked_seq, durable_seq, "
                                    "last_seen FROM bench_cursor WHERE "
                                    "machine_uid = ? ORDER BY last_seen DESC",
                                    [uid])]
            ledger = [{"lab_id": r["lab_id"], "test_name": r["test_name"],
                       "value": r["value"], "filed_at": r["filed_at"],
                       "ref": r["bench_seq_ref"]}
                      for r in rows("SELECT lab_id, test_name, value, filed_at, "
                                    "bench_seq_ref FROM result_ledger WHERE "
                                    "machine_uid = ? AND filed_at >= strftime("
                                    "'%Y-%m-%dT%H:%M:%S', 'now', '-30 days') "
                                    "ORDER BY lab_id, test_name", [uid])]
            return _gz(jsonify({"machine_uid": uid, "epochs": epochs,
                                "sources": sources, "result_ledger": ledger,
                                "last_qc": _last_qc(uid),
                                "server_time": _iso()}))
        except BenchRefusal as exc:
            return _refused(exc)
        except StoreFailed as exc:
            return _store_failed("read this bench's checkpoint", exc.result)

    @app.route("/api/v2/bench/<uid>/adoption")
    def bench_v2_adoption(uid):
        """§10.2: the multiset of H(lab_id, raw) over this uid's recorded
        run and qc rows, for the bench's first v4 start to match its file
        against. From the effective record: a row hidden as a replay is a
        copy of a row that is still there."""
        try:
            # Half an imported record would read as "never recorded": hold.
            _hold()
            _bench_auth(uid)
            res = store.read_sql(
                "SELECT ts, kind, lab_id, test_name, value, detail FROM "
                "lem_machine_log_effective WHERE machine_uid = ? AND kind IN "
                "('run', 'qc')", [uid])
            if not isinstance(res, dict) or res.get("error") or "rows" not in res:
                raise StoreFailed(res)
            digest = adoption_digest(res["rows"], datetime.now())
            return _gz(jsonify(dict(digest, machine_uid=uid,
                                    src=request.args.get("src", ""),
                                    boundary=request.args.get("boundary", ""),
                                    recipe=ADOPTION_RECIPE)))
        except BenchRefusal as exc:
            return _refused(exc)
        except StoreFailed as exc:
            return _store_failed("read this bench's recorded rows", exc.result)

    @app.route("/api/v2/bench/<uid>/source-snapshot", methods=["PUT", "POST"])
    def bench_v2_source_snapshot(uid):
        """`snapshot.bin` for one source, uploaded only when a sync's
        `need_snapshot` asked for it (§6.1), kept for `/checkpoint`."""
        try:
            _hold()
            _bench_auth(uid)
            src = str(request.args.get("src") or "")
            sha = str(request.args.get("sha") or "").lower()
            if not src or not _HEX64.match(sha):
                raise _bad("src and a 64-hex sha are required")
            data = _read_body(request, MAX_SNAPSHOT_BYTES, MAX_SNAPSHOT_BYTES)
            if hashlib.sha256(data).hexdigest() != sha:
                raise _bad("the snapshot's sha256 is not %s: it was damaged "
                           "on the way, or is not the one announced" % sha)
            with store.transaction():
                ing = Ingest(store, uid, "", _iso())
                ing.x("INSERT INTO bench_source (machine_uid, src, snapshot, "
                      "snapshot_sha, updated_at) VALUES (?, ?, ?, ?, ?) "
                      "ON CONFLICT(machine_uid, src) DO UPDATE SET "
                      "snapshot = excluded.snapshot, snapshot_sha = "
                      "excluded.snapshot_sha, updated_at = excluded.updated_at",
                      [uid, src, data, sha, _iso()])
            return jsonify({"ok": True, "bytes": len(data), "sha": sha})
        except BenchRefusal as exc:
            return _refused(exc)
        except StoreFailed as exc:
            return _store_failed("keep this source's snapshot", exc.result)

    @app.route("/api/v2/bench/<uid>/enroll", methods=["POST"])
    def bench_v2_enroll(uid):
        """§6.4. The shared live token proves an EXISTING uid's first
        enrolment; anything else waits for a person (202 pending) — except
        the same enrolment retried with its `enroll_key` after a lost 200,
        before the token has carried a sync."""
        supplied = request.headers.get("X-LEM-Token", "")
        if not hmac.compare_digest(str(supplied),
                                   str(app.config["LIVE_TOKEN"])):
            return _answer(401, {"error": "Not authorised."})
        if getattr(store, "read_only", False):
            return _answer(503, {"error": "read-only store; retry"},
                           RETRY_READ_ONLY_S)
        body = request.get_json(silent=True)
        if body is not None and not isinstance(body, dict):
            return _answer(400, {"error": "Expected a JSON object."})
        if body and body.get("machine_uid") not in (None, uid):
            return _answer(400, {"error": "machine_uid does not match the URL"})
        key_sha = enroll_key_of(body)
        now = _iso()
        if not declared["done"]:
            declared["done"] = ensure_current_state_tables(store)
        try:
            with store.transaction():
                ing = Ingest(store, uid, "", now)
                row = (ing.q("SELECT * FROM bench_token WHERE machine_uid = ?",
                             [uid]) or [None])[0]
                # "Existing" = LEM already holds this bench: its machine
                # configuration, or a status it has reported — and it is
                # not retired. Seventeen v3.9 benches are all one or both.
                known = bool(ing.q(
                    "SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM "
                    "lem_machine_config WHERE machine_uid = ? AND retired_at "
                    "IS NOT NULL) AND (EXISTS (SELECT 1 FROM lem_machine_config "
                    "WHERE machine_uid = ?) OR EXISTS (SELECT 1 FROM "
                    "lem_machine_status WHERE machine_uid = ?))",
                    [uid, uid, uid]))
                approved = (row is not None and not row.get("token_sha256")
                            and str(row.get("issued_by") or "")
                            .startswith("approved:"))
                # The same enrolment retried after its answer was lost: the
                # key this bench made for it, before the token has carried a
                # sync (a sync spends the key). See `enroll_key_of`.
                retried = (row is not None and key_sha is not None
                           and row.get("token_sha256")
                           and not row.get("revoked_at")
                           and row.get("enroll_key_sha256")
                           and hmac.compare_digest(
                               str(row["enroll_key_sha256"]), key_sha))
                if (row is None and known) or approved or retried:
                    token = secrets.token_urlsafe(32)
                    who = (str(row.get("issued_by") or "") if retried
                           else ("shared token, " + str(row["issued_by"])
                                 .replace("approved:", "approved by "))
                           if approved else "shared token (first enrolment)")
                    ing.x("INSERT INTO bench_token (machine_uid, token_sha256, "
                          "issued_at, issued_by, revoked_at, pending_reenrol_at, "
                          "enroll_key_sha256) "
                          "VALUES (?, ?, ?, ?, NULL, NULL, ?) ON CONFLICT("
                          "machine_uid) DO UPDATE SET token_sha256 = "
                          "excluded.token_sha256, issued_at = excluded.issued_at, "
                          "issued_by = excluded.issued_by, revoked_at = NULL, "
                          "pending_reenrol_at = NULL, enroll_key_sha256 = "
                          "excluded.enroll_key_sha256",
                          [uid, token_hash(token), now, who, key_sha])
                    result = (200, {"token": token, "machine_uid": uid,
                                    "issued_at": now})
                else:
                    ing.x("INSERT INTO bench_token (machine_uid, "
                          "pending_reenrol_at) VALUES (?, ?) ON CONFLICT("
                          "machine_uid) DO UPDATE SET pending_reenrol_at = "
                          "COALESCE(bench_token.pending_reenrol_at, "
                          "excluded.pending_reenrol_at)", [uid, now])
                    why = ("this bench is already enrolled; a new token needs "
                           "a person to approve it in LEM (Settings › "
                           "Transfer)" if row is not None and row.get(
                               "token_sha256") else
                           "LEM does not know this uid; a person must approve "
                           "it in LEM (Settings › Transfer)" if not known else
                           "waiting for a person to approve it in LEM")
                    result = (202, {"state": "pending", "machine_uid": uid,
                                    "why": why})
        except StoreFailed as exc:
            return _store_failed("enrol this bench", exc.result)
        return _answer(*result)

    @app.route("/api/transfer/benches")
    def transfer_benches():
        """Every bench that holds or asks for a token, for Settings ›
        Transfer. Tokens themselves are never here — only that one exists."""
        if not authed():
            return _answer(401, {"error": "Authentication required"})
        res = store.read_sql(
            "SELECT t.machine_uid, t.token_sha256 IS NOT NULL AS enrolled, "
            "t.issued_at, t.issued_by, t.revoked_at, t.pending_reenrol_at, "
            "c.title, c.machine_uid IS NOT NULL AS known FROM bench_token t "
            "LEFT JOIN lem_machine_config c ON c.machine_uid = t.machine_uid "
            "ORDER BY t.machine_uid")
        if not isinstance(res, dict) or res.get("error") or "rows" not in res:
            return _store_failed("read the enrolled benches", res)
        out = []
        for r in res["rows"]:
            entry = registry.get(r["machine_uid"]) or {}
            out.append({"machine_uid": r["machine_uid"], "title": r.get("title"),
                        "known": bool(r.get("known")),
                        "enrolled": bool(r.get("enrolled")),
                        "pending": bool(r.get("pending_reenrol_at")),
                        "pending_since": r.get("pending_reenrol_at"),
                        "issued_at": r.get("issued_at"),
                        "issued_by": r.get("issued_by"),
                        "transfer": transfer_field(entry or None)})
        return jsonify({"benches": out})

    @app.route("/api/transfer/benches/<uid>/approve", methods=["POST"])
    def transfer_bench_approve(uid):
        """A person lets this bench (re-)enrol. The old token stops working
        now; the bench's next enrolment gets a new one."""
        if not authed():
            return _answer(401, {"error": "Authentication required"})
        who = current_user() or "someone"
        now = _iso()
        try:
            with store.transaction():
                ing = Ingest(store, uid, "", now)
                row = (ing.q("SELECT pending_reenrol_at FROM bench_token "
                             "WHERE machine_uid = ?", [uid]) or [None])[0]
                if row is None or not row.get("pending_reenrol_at"):
                    return _answer(409, {"error": "%s has not asked to enrol"
                                                  % uid})
                ing.x("UPDATE bench_token SET token_sha256 = NULL, "
                      "revoked_at = ?, issued_by = ? WHERE machine_uid = ?",
                      [now, "approved:" + who, uid])
                # The 17025 trail: who let which bench speak for the record.
                ing.x("INSERT INTO lem_machine_log (machine_uid, ts, kind, "
                      "lab_id, test_name, value, detail) VALUES (?, ?, "
                      "'config', '', '', '', ?)",
                      [uid, datetime.now().isoformat(timespec="seconds"),
                       json.dumps({"action": "bench enrolment approved",
                                   "by": who})])
        except StoreFailed as exc:
            return _store_failed("approve this bench", exc.result)
        return jsonify({"ok": True, "machine_uid": uid, "approved_by": who})

    @app.route("/api/v2/bench/<uid>/sync", methods=["POST"])
    def bench_v2_sync(uid):
        try:
            _hold()
            _bench_auth(uid)
            data = _read_body(request, MAX_SYNC_BYTES, MAX_INFLATED_BYTES)
            doc = parse_sync(uid, data)
        except BenchRefusal as exc:
            return _refused(exc)
        except StoreFailed as exc:
            return _store_failed("check this bench", exc.result)

        epoch, from_seq = doc["epoch"], doc["from_seq"]
        stats = doc["stats"]
        now_dt = _utcnow()
        now = _iso(now_dt)
        skew = None
        clock = _parse_moment(doc.get("bench_clock"))
        if clock is not None:
            skew = round((clock - now_dt).total_seconds(), 1)
        v1 = _config_v1(uid)
        if not declared["done"]:
            declared["done"] = ensure_current_state_tables(store)
        try:
            with store.transaction():
                ing = Ingest(store, uid, epoch, now)
                # This token has carried a sync, so the bench holds it: its
                # enrolment key is spent. Only for THIS token — if a retried
                # enrolment rotated it since `_bench_auth`, the key stays so
                # the bench can still collect the token it never saw.
                ing.x("UPDATE bench_token SET enroll_key_sha256 = NULL WHERE "
                      "machine_uid = ? AND token_sha256 = ? AND "
                      "enroll_key_sha256 IS NOT NULL",
                      [uid, token_hash(request.headers.get(
                          "X-LEM-Bench-Token", ""))])
                cur = (ing.q("SELECT acked_seq, durable_seq, digest, "
                             "digest_mismatch FROM bench_cursor WHERE "
                             "machine_uid = ? AND bench_epoch = ?",
                             [uid, epoch]) or [None])[0]
                acked = int(cur["acked_seq"]) if cur else 0
                durable = int(cur["durable_seq"] or 0) if cur else 0
                if from_seq > acked + 1:
                    conflict = acked
                else:
                    conflict = None
                    digest = (cur.get("digest") if cur else None) or DIGEST_ZERO
                    mismatch = None
                    bench_digest = stats.get("digest")
                    through = _int_or(stats.get("digest_seq"), from_seq - 1)
                    if isinstance(bench_digest, str) and through == acked \
                            and bench_digest.lower() != digest:
                        mismatch = {"through": acked, "bench": bench_digest,
                                    "server": digest, "at": now}
                        ing.notes.append({
                            "kind": "digest_mismatch", "through": acked,
                            "message": "this bench's journal and LEM disagree "
                                       "at or before seq %d; records are "
                                       "still accepted" % acked})
                    for seq, body, raw in doc["records"]:
                        if seq <= acked:
                            continue                 # a resend: already held
                        ing.record(seq, body, raw)
                        digest = chain(digest, raw)
                        acked = seq
                    lc = stats.get("labcore") if isinstance(
                        stats.get("labcore"), dict) else {}
                    ing.x(
                        "INSERT INTO bench_cursor (machine_uid, bench_epoch, "
                        "acked_seq, digest, records_total, first_seen, "
                        "last_seen, road, module_version, clock_skew_s, "
                        "labcore_failures_5min, mode, stats, digest_mismatch) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'v2', ?, ?) "
                        "ON CONFLICT(machine_uid, bench_epoch) DO UPDATE SET "
                        "acked_seq = excluded.acked_seq, digest = "
                        "excluded.digest, records_total = COALESCE("
                        "excluded.records_total, bench_cursor.records_total), "
                        "last_seen = excluded.last_seen, road = COALESCE("
                        "excluded.road, bench_cursor.road), module_version = "
                        "excluded.module_version, clock_skew_s = COALESCE("
                        "excluded.clock_skew_s, bench_cursor.clock_skew_s), "
                        "labcore_failures_5min = COALESCE("
                        "excluded.labcore_failures_5min, "
                        "bench_cursor.labcore_failures_5min), mode = 'v2', "
                        "stats = excluded.stats, digest_mismatch = COALESCE("
                        "bench_cursor.digest_mismatch, excluded.digest_mismatch)",
                        [uid, epoch, acked, digest,
                         bench_count(stats.get("records_total")), now, now,
                         bench_road(stats.get("road")),
                         doc["module_version"], skew,
                         bench_count(lc.get("failed")),
                         json.dumps(stats, default=str)[:20000] if stats else None,
                         json.dumps(mismatch) if mismatch else None])
                    live_block = doc.get("live") or {}
                    ing.x("INSERT INTO lem_machine_heartbeat (machine_uid, "
                          "last_poll) VALUES (?, ?) ON CONFLICT(machine_uid) "
                          "DO UPDATE SET last_poll = excluded.last_poll",
                          [uid, wall_ts(live_block.get("at"), _now_wall())])
                    need = []
                    for s in doc["sources"]:
                        announced = str(s.get("snapshot_sha") or "").lower()
                        held = (ing.q("SELECT snapshot_sha FROM bench_source "
                                      "WHERE machine_uid = ? AND src = ?",
                                      [uid, s["src"]]) or [{}])[0]
                        cursor = s.get("cursor") if isinstance(
                            s.get("cursor"), dict) else {}
                        ing.x("INSERT INTO bench_source (machine_uid, src, "
                              "lineage, cursor, announced_sha, updated_at) "
                              "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT("
                              "machine_uid, src) DO UPDATE SET lineage = "
                              "excluded.lineage, cursor = excluded.cursor, "
                              "announced_sha = excluded.announced_sha, "
                              "updated_at = excluded.updated_at",
                              [uid, s["src"], str(cursor.get("lineage") or "")
                               or None, json.dumps(cursor), announced or None,
                               now])
                        if announced and announced != (held.get(
                                "snapshot_sha") or ""):
                            need.append(s["src"])
                    resolutions = [
                        {"conflict_seq": r["bench_seq_ref"],
                         "choice": r["choice"], "by": r["resolved_by"]}
                        for r in ing.q(
                            "SELECT bench_seq_ref, choice, resolved_by FROM "
                            "result_conflict WHERE machine_uid = ? AND "
                            "resolved_at IS NOT NULL AND delivered_at IS NULL "
                            "ORDER BY bench_seq_ref", [uid])]
                    machine_config, retired = None, None
                    cfg = ing.q("SELECT config, retired_at FROM "
                                "lem_machine_config WHERE machine_uid = ?", [uid])
                    if cfg:
                        machine_config = _json_or(cfg[0].get("config"), {})
                        retired = cfg[0].get("retired_at")
        except StoreFailed as exc:
            return _store_failed("record this bench's readings", exc.result)
        except Exception as exc:                       # noqa: BLE001
            # sqlite3 errors from BEGIN/COMMIT, a read-only store: the
            # transaction rolled back, so nothing is acked. Never a 500 the
            # bench would read as "unreachable" with no reason.
            logger.warning("bench api: sync for %s rolled back: %s", uid, exc)
            return _answer(503, {"error": "LEM's store could not record this "
                                          "sync (%s); nothing was recorded. "
                                          "Hold and retry." % type(exc).__name__},
                           RETRY_BUSY_S)

        if conflict is None and ing.config_changed:
            # The bench changed its own configuration: the next config_rev
            # must say so (inline without a poller, a wake with one).
            try:
                snapshots.refresh_soon()
            except Exception:                          # noqa: BLE001
                pass
        if doc.get("live") and doc["live"].get("status"):
            live.record(uid, doc["live"])
        # The other benches, from the store, before this one is noted: once
        # per process (a failed read retries in a minute). Without it,
        # /healthz after a restart knew only the benches that had synced
        # since — a partial count that read as the whole.
        registry.hydrate(store)
        if conflict is not None:
            registry.note(uid, seen=time.time())
            return _answer(409, {"error": "cursor", "acked": conflict,
                                 "epoch": epoch,
                                 "message": "LEM holds this epoch only through "
                                            "seq %d; send from %d"
                                            % (conflict, conflict + 1)})
        # `unacked` is what the bench held when it sent this sync, before
        # LEM acked any of it (the floor's `transfer` echoes it as said).
        # `waiting` is what is still at the bench AFTER this answer: its
        # newest seq (from_seq - 1 + unacked) less what LEM now holds. The
        # foot and the instrument page say this one (transfer §14).
        said = bench_count(stats.get("unacked"))
        registry.note(uid, epoch=epoch, acked=acked, road=stats.get("road"),
                      unacked=stats.get("unacked"),
                      waiting=(max(0, from_seq - 1 + said - acked)
                               if said is not None else None),
                      module_version=doc["module_version"],
                      labcore=stats.get("labcore"),
                      digest_mismatch=bool(mismatch) or bool(
                          cur and cur.get("digest_mismatch")),
                      seen=time.time())
        notes = list(ing.notes)
        for what in live.take_stale(uid):
            notes.append({"kind": "stale", "what": what})
        return _answer(200, {"epoch": epoch, "acked": acked, "durable": durable,
                             "notes": notes,
                             "config_rev": _config_rev(v1, machine_config),
                             "resolutions": resolutions,
                             "machine": "retired" if retired else "active",
                             "need_snapshot": need})
