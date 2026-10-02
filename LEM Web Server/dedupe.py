#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dedupe.py — find the rows the transfer wrote twice, and hide them only when
Ryan says so. Transfer spec v4 §10.5; decision D7.

WHY THIS EXISTS
---------------
A `single_csv` LabStation that restarts re-reads its file from a stale byte
offset and logs every line again under the restart's timestamp; a log INSERT
whose response was lost is sent again; the 08-27 labshare import re-inserted
some of its batches. On the server's log-mirror copy of 2026-10-02 that is
tens of thousands of rows (the numbers are in the commit that added this
file). They are not harmless: a replayed QC print stamped "today" renews QC
freshness that nobody earned, and copies of one reading pad `n` and shrink the
spread an uncertainty estimate is built from.

WHAT IT DOES, AND WHAT IT NEVER DOES
------------------------------------
1. `classify` — a pure function over rows. It PROPOSES labels; it writes
   nothing, and it never runs on LabCore (§10.5: "It never runs on
   LabCore"); it reads LEM's store, or a COPY of the log mirror.
2. `report` — the dry run: per bench, per kind, per label, twenty examples
   each beside the row it duplicates, storm days called out, and the
   QC-impact report (series whose last verdict moves earlier, u(Rw) spreads
   that change). Shown BEFORE anything is applied.
3. `approve` — Ryan's decision, per bench and per rule, as an append-only
   `annotation_approval` row carrying who, when, how many, the examples and
   the QC impact he was shown. The candidate set is named by a digest, so
   an approval of a report the record has since moved past is refused.
4. `apply` — one append-only `log_annotation` row per candidate, each naming
   its approval. Reversible per row by `reinstate`, which leaves both rows in
   the history.

There is no automatic hiding anywhere. The store enforces that, not this
module: an approval reaches the store only through `record_approval`, which
verifies its HMAC and writes the exact rows it covers beside it
(`annotation_approval_member`); `lem_store` refuses a hiding annotation
unless its approval is approved, signed, for the same bench and rule, and
NAMES that row; and the effective view hides a row only through such an
approval. What SQLite cannot check is the HMAC itself, so `apply` re-verifies
it and every dry run names an approval that does not verify.

THE RULES (§10.5), PER BENCH, IN `(ts, id)` ORDER
-------------------------------------------------
A *poll* is one `(machine_uid, ts)`: every row one poll wrote carries the
poll's `ts`. The *fingerprint* is `(kind, lab_id, test_name, value, detail)`
with the keys a replay restamps (operator, calibration, poll time) and the
import's provenance (`imported`, `source_file`) taken out of `detail`.

* `resend` — a block of rows repeated whole, back to back, inside one poll:
  a batch INSERT that landed twice (N3). Exact match, nothing dropped. Two
  identical rows with ADJACENT ids are NOT a resend: one INSERT carries a
  poll's lines with consecutive ids, so they came out of the file together
  — a re-test the instrument printed twice — and they go through the twin
  rule like any other row.
* `replay_duplicate` — a row with an earlier identical twin in a DIFFERENT
  poll, in a poll in which ≥ 80 % of the rows have such twins (the majority
  rule; it needs at least 5 rows, because C's 2-row/50 % rule was rejected
  as too eager and a 1-row poll is "100 %"), or in a poll that carried ≥ 20
  rows where the twin lies in a replayed STRETCH: consecutive rows the
  record already holds, spanning two or more samples
  (`_replayed_stretches`). A restart re-reads a run of its file; a lone
  twin among new rows is what a genuine repeat in a catch-up poll looks
  like. On BOTH paths the twins must include two or more SAMPLES
  (`is_sample_id`: a Lab ID numbered the way LabCore numbers a sample). A
  lab reads its standards, blanks and solvents every day by design, and a
  repeat of only those is a QC repeat until a person says otherwise.
* `probable_duplicate` — a twin that is NOT in such a poll or stretch: a
  genuine identical re-test or a QC repeat looks exactly like this. Always
  visible; listed for review.
* `import_leftover` — an imported (`labshare-2026-08-27`) row that one of
  the two rules above would hide, and the import's three misread Lab IDs.

Twins are matched by COUNT, not by "seen before": a file holding a reading
twice replays both copies, and both are duplicates only if the record
already holds it twice. A re-test printed while the bench was down arrives
inside the replay burst as the extra copy, and is new. Rows that stay
visible (genuine, probable) raise the count; hidden candidates do not.

A FAILED READ IS NEVER AN EMPTY RESULT. "No duplicates" is a statement about
the record; a read that failed is `DedupeReadError`, never an empty report.
"""

from __future__ import annotations

import collections
import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import qc_series

# ── the rules' constants ─────────────────────────────────────────────────────

BURST_ROWS = 20
MAJORITY_SHARE = 0.8
MAJORITY_MIN_ROWS = 5
#: How far back inside one poll a re-sent block is looked for. The module
#: sends at most 100 rows per INSERT; the import's re-inserted batches were
#: 100 rows. Generous, and bounded so a 12,000-row first ingest stays linear.
RESEND_LOOKBACK = 1000
#: The module's log drain sends at most this many rows per INSERT
#: (`LOG_BATCH_ROWS` in lem_station_module.py, since the 08-19 import of the
#: module into git). A re-sent batch starts on a multiple of it.
LOG_BATCH_ROWS = 100

IMPORT_TAG = "labshare-2026-08-27"
#: The import read a numeric column as the Lab ID on these (ASK-CLAUDE.md).
MISREAD_LAB_IDS = frozenset(("3659736601", "271750", "3246632466"))

#: `detail` keys a replay restamps, plus the import's provenance.
DROPPED_DETAIL_KEYS = frozenset((
    "operator", "calibration_id", "calibration", "polled_at", "poll_ts",
    "imported", "source_file"))

CLASSIFIED_KINDS = ("run", "qc")

#: A sample's Lab ID as LabCore numbers it: four or more digits at the
#: start, after at most one letter, with anything after (39878, 40528,
#: 091823-7945, 28018N1, 28967 Top, 19138T). Standards, blanks, solvents and
#: cal checks are NAMED (Blank, AF26, Cal STD, RT 6.29, ASTM2887-12,
#: RGO 011623), whether the bench logs them as `qc` or as `run`; a named
#: reference that starts like a number says so (D2887-STD, D2887 Cal Std).
#: On the server's log-mirror copy 79,517 of the benches' own 82,272 `run`
#: rows carry a sample's Lab ID.
_SAMPLE_ID = re.compile(r"[A-Za-z]?\d{4,}")
_NAMED_REFERENCE = re.compile(r"(?i)std|standard|blank|solvent|check|\bcal\b")

#: Labels the classifier proposes for hiding. Each needs its own approval.
HIDE_CANDIDATE_LABELS = ("replay_duplicate", "resend", "import_leftover")
#: Listed for review; never hidden.
REVIEW_LABELS = ("probable_duplicate",)

#: The annotation label each candidate label is written as. `resend` hides as
#: a `replay_duplicate` (the store's two hiding labels are the spec's), and
#: keeps its own name in `rule`.
ANNOTATION_LABEL = {"replay_duplicate": "replay_duplicate",
                    "resend": "replay_duplicate",
                    "import_leftover": "import_leftover"}

EXAMPLES_PER_BENCH = 20
APPLY_CHUNK = 2000
STORM_POLLS_PER_DAY = 10


class DedupeError(RuntimeError):
    pass


class DedupeReadError(DedupeError):
    """The record could not be read. Never reported as 'no duplicates'."""


class DedupeRefused(DedupeError):
    """A request this module will not carry out, and why."""


# ── rows ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LogRow:
    id: int
    machine_uid: str
    ts: str
    kind: str
    lab_id: str
    test_name: str
    value: str
    detail: str

    @classmethod
    def from_dict(cls, r: Dict[str, Any]) -> "LogRow":
        rid = r.get("id", r.get("rowid_src"))
        return cls(int(rid), str(r.get("machine_uid") or ""),
                   str(r.get("ts") or ""), str(r.get("kind") or ""),
                   str(r.get("lab_id") or ""), str(r.get("test_name") or ""),
                   "" if r.get("value") is None else str(r.get("value")),
                   "" if r.get("detail") is None else str(r.get("detail")))

    def detail_dict(self) -> Optional[dict]:
        try:
            d = json.loads(self.detail) if self.detail else {}
        except (TypeError, ValueError):
            return None
        return d if isinstance(d, dict) else None

    def is_imported(self) -> bool:
        d = self.detail_dict()
        return bool(d) and d.get("imported") == IMPORT_TAG

    def brief(self) -> Dict[str, Any]:
        return {"id": self.id, "ts": self.ts, "kind": self.kind,
                "lab_id": self.lab_id, "test_name": self.test_name,
                "value": self.value, "detail": self.detail[:400]}


def fingerprint(row: LogRow) -> Tuple[str, ...]:
    """What makes two rows the same reading (§10.5).

    A detail that is not a JSON object is compared as its raw text: dropping
    it would make two different unreadable details "identical".
    """
    d = row.detail_dict()
    if d is None:
        det = "raw:" + row.detail
    else:
        det = json.dumps({k: v for k, v in d.items()
                          if k not in DROPPED_DETAIL_KEYS},
                         sort_keys=True, ensure_ascii=False)
    return (row.kind, row.lab_id, row.test_name, row.value, det)


def is_sample_id(lab_id: str) -> bool:
    """True for a Lab ID numbered the way LabCore numbers a sample."""
    text = str(lab_id or "").strip()
    return bool(_SAMPLE_ID.match(text)) and not _NAMED_REFERENCE.search(text)


def _samples(rows: Iterable[LogRow]) -> set:
    """The distinct SAMPLES among rows: `run` rows with a numbered Lab ID.
    A repeat that holds fewer than two is a repeat of the lab's standards
    and blanks, which it reads every day by design."""
    return {r.lab_id for r in rows
            if r.kind == "run" and is_sample_id(r.lab_id)}


def _exact(row: LogRow) -> Tuple[str, ...]:
    return (row.kind, row.lab_id, row.test_name, row.value, row.detail)


# ── the classifier ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Candidate:
    log_id: int
    machine_uid: str
    ts: str
    kind: str
    label: str
    rule: str
    dup_of: Optional[int]
    poll_rows: int
    #: "" or "storm:YYYY-MM-DD": the approval unit the candidate belongs to.
    scope: str = ""

    @property
    def unit(self) -> str:
        return unit_name(self.label, self.scope)


def unit_name(label: str, scope: str = "") -> str:
    """The name of one approval unit: a label, and a storm day if any."""
    return label + ("@" + scope if scope else "")


_UNIT = re.compile(r"^([a-z_]+)(?:@(storm:\d{4}-\d{2}-\d{2}))?$")


def parse_unit(unit: str) -> Tuple[str, str]:
    """(label, scope) of an approval unit, refused if it is not one."""
    m = _UNIT.match(str(unit or ""))
    if not m or m.group(1) not in HIDE_CANDIDATE_LABELS:
        raise DedupeRefused("{0!r} is not a set of candidates that hides "
                            "anything".format(unit))
    return m.group(1), m.group(2) or ""


@dataclass
class PollStat:
    """One poll as the dry run saw it. `kept` sorts the rows that are NOT
    hide candidates by why not — what a reviewer of a storm day needs:

    * `probable_duplicate` — a twin, but not in a replay poll;
    * `new_lab_id` — the bench's first row for that Lab ID;
    * `other_values` — the Lab ID was logged before with different content
      (a re-processed or re-integrated result: not a copy of anything);
    * `import_shape` — the Lab ID's previous row is the 08-27 import's,
      which stores one row per TEST under the parsed file's column names,
      where the bench stores one row per SAMPLE. The same reading may be in
      the record twice in two shapes; no content rule can say so, so it is
      counted for review and never proposed for hiding.
    """
    ts: str
    rows: int
    hidden: int
    replay: bool
    kept: Dict[str, int]
    #: every row of the poll is the 08-27 import's (its history days hold
    #: many "polls" by construction; they are never a storm)
    imported: bool = False


@dataclass
class Classification:
    candidates: Dict[int, Candidate] = field(default_factory=dict)
    rows: Dict[int, LogRow] = field(default_factory=dict)
    polls: Dict[str, List[PollStat]] = field(default_factory=dict)
    upto_id: int = 0
    #: per bench, the days its live polls stormed (see `_storm_days`)
    storm_days: Dict[str, List[str]] = field(default_factory=dict)

    def machines(self) -> List[str]:
        return sorted({r.machine_uid for r in self.rows.values()})

    def ids(self, machine_uid: str, unit: str) -> List[int]:
        """The candidates of one approval unit: a label alone means the
        bench's ordinary candidates; `label@storm:DAY` one storm day's."""
        label, _, scope = str(unit).partition("@")
        return sorted(c.log_id for c in self.candidates.values()
                      if c.machine_uid == machine_uid and c.label == label
                      and c.scope == scope)

    def units(self, machine_uid: str) -> List[str]:
        """Every approval unit the bench has, ordinary before storms."""
        got = {c.unit for c in self.candidates.values()
               if c.machine_uid == machine_uid
               and c.label in HIDE_CANDIDATE_LABELS}
        return sorted(got, key=lambda u: ("@" in u, u))

    def run_id(self, machine_uid: str, unit: str) -> str:
        """Names exactly one candidate set: the record it was read from (all
        rows with `id <= upto`) and a digest of the set itself."""
        h = hashlib.sha256()
        for rid in self.ids(machine_uid, unit):
            c = self.candidates[rid]
            h.update(("%d:%s:%s:%s\n" % (rid, c.dup_of, c.rule, unit)
                      ).encode("utf-8"))
        return "upto=%d;sha=%s" % (self.upto_id, h.hexdigest())


_RUN_ID = re.compile(r"^upto=(\d+);sha=([0-9a-f]{64})$")


def parse_run_id(run_id: str) -> Tuple[int, str]:
    m = _RUN_ID.match(str(run_id or ""))
    if not m:
        raise DedupeRefused("not a dry-run id: {0!r}".format(run_id))
    return int(m.group(1)), m.group(2)


def _import_resends(poll: List[LogRow]) -> Dict[int, Tuple[int, str]]:
    """The 08-27 import's re-inserted batches, inside one of its polls.

    The import is not the module: it wrote 100-row batches across its own
    stream, so its copies are found as before — a block of exact rows
    repeated whole, back to back, with the one-row exception for adjacent
    ids (two identical lines of one parsed run, one INSERT)."""
    exact = [_exact(r) for r in poll]
    out: Dict[int, Tuple[int, str]] = {}
    n, i = len(poll), 1
    while i < n:
        hit = 0
        for j in range(i - 1, max(-1, i - 1 - RESEND_LOOKBACK), -1):
            if exact[j] != exact[i]:
                continue
            length = i - j
            if length == 1 and poll[i].id - poll[j].id == 1:
                break           # adjacent ids: two lines of one INSERT
            if i + length <= n and exact[j:i] == exact[i:i + length]:
                hit = length
                break
        if hit:
            for k in range(i, i + hit):
                out[poll[k].id] = (poll[k - hit].id, "tandem")
            i += hit
        else:
            i += 1
    return out


def _live_resends(poll: List[LogRow]) -> Dict[int, Tuple[int, str]]:
    """Copies inside one poll of a bench, from its FULL id order (every
    kind: a status row is part of what the drain sent). Maps a copy's id to
    (the id it copies, why).

    The module drains a poll's rows in INSERTs of up to LOG_BATCH_ROWS, in
    order; a lost response puts the whole batch back and sends it again. A
    block [p, p+L) repeated back to back at [p+L, p+2L) — and again, for a
    batch sent more than twice — is a copy when there is evidence for it:

    * ``batch`` (N3): the block starts on a batch boundary (p is a multiple
      of LOG_BATCH_ROWS), is at most one batch long, and the copy is a
      SEPARATE INSERT: another bench's row landed between the two (an id
      gap), or the block carries a status change (a bench cannot make the
      same transition twice running), or it is a full batch of 100.
    * ``stretch``: the block spans two or more samples in identical order.
      A person re-tests a sample; a stretch of samples, every number the
      same, in the same order, is the file or the transfer repeating itself.

    Anything else — one sample's lines printed twice (adjacent ids, one
    INSERT), a QC repeat — is the file's content and is left to the twin
    rule, wherever in the poll it sits.
    """
    exact = [_exact(r) for r in poll]
    out: Dict[int, Tuple[int, str]] = {}
    n, p = len(poll), 0
    while p < n - 1:
        advanced = False
        for length in range(1, min(LOG_BATCH_ROWS, (n - p) // 2) + 1):
            q = p + length
            if exact[p] != exact[q] or exact[p:q] != exact[q:q + length]:
                continue
            block = poll[p:q]
            samples = len({r.lab_id for r in block if r.lab_id}) >= 2
            status = any(r.kind not in CLASSIFIED_KINDS for r in block)
            on_boundary = p % LOG_BATCH_ROWS == 0
            copies = 0
            while q + length <= n and exact[q:q + length] == exact[p:p + length]:
                gap = poll[q].id - poll[q - 1].id != 1
                batch = on_boundary and (gap or status
                                         or length == LOG_BATCH_ROWS)
                if not (batch or samples):
                    break
                why = "batch" if batch else "stretch"
                for k in range(length):
                    if poll[q + k].kind in CLASSIFIED_KINDS:
                        out[poll[q + k].id] = (poll[p + k].id, why)
                q += length
                copies += 1
            if copies:
                p, advanced = q, True
                break
        if not advanced:
            p += 1
    return out


def _replayed_stretches(poll: List[LogRow], rest: List[int],
                        twin_of: Dict[int, int], seen: set) -> set:
    """The twins of a big poll that a re-read put there (positions in
    `poll`), for a poll that is NOT mostly twins.

    A restart re-reads a RUN of its file, so its copies arrive as a stretch:
    consecutive rows (in the poll's order, re-sent rows set aside), every
    one a reading the record already holds (`seen`), spanning two or more
    SAMPLES (`_samples`: a standard or a blank is not one). The twins in such
    a stretch are copies. A twin outside one — a row among new ones, or a
    run of standards and blanks, which a lab measures every day by design —
    is what a genuine repeat looks like in a catch-up poll, and is left to
    review (round-2 critic: a QC repeat in a 24-row catch-up; Multitek NS
    row 216039 on 08-31; round-3 critic: a Blank and a Solvent).

    A row the record holds but whose copies are all accounted for (the file
    holds that reading more often than the record does) still belongs to
    the re-read: it does not break the stretch around it, and it is not
    itself a twin, so it stays visible. Agilent GC 2's 09-23 re-read of its
    whole file has such rows between its copies of AF26 and 39888."""
    out: set = set()
    run: List[int] = []
    for k in rest + [-1]:                  # -1 closes the last run
        if k >= 0 and k in seen:
            run.append(k)
            continue
        if len(run) >= 2 and len(_samples(poll[j] for j in run)) >= 2:
            out.update(j for j in run if j in twin_of)
        run = []
    return out


def _storm_days(polls: Sequence[PollStat]) -> List[str]:
    """Days on which a bench's LIVE polls burst or replayed at least
    STORM_POLLS_PER_DAY times — O9's 08-07 and 08-18 on the Agilent. A day
    like that is an event with a cause (a file rewritten in place, a module
    looping), and §10.5 sends it to review on its own rather than letting it
    ride inside a bench-wide approval."""
    per_day: Dict[str, int] = collections.Counter()
    for p in polls:
        if not p.imported and (p.replay or p.rows >= BURST_ROWS):
            per_day[p.ts[:10]] += 1
    return sorted(d for d, n in per_day.items() if n >= STORM_POLLS_PER_DAY)


def classify(rows: Iterable[LogRow],
             upto_id: Optional[int] = None) -> Classification:
    """Label every `run` and `qc` row that looks like a copy. Pure.

    `upto_id` is the newest id of the record the rows were read from; by
    default the newest id among them."""
    result = Classification(upto_id=int(upto_id or 0))
    by_machine: Dict[str, List[LogRow]] = collections.defaultdict(list)
    # Every row of every kind, per poll: a resend is a repeat of what the
    # drain SENT, and it sent status changes between the readings.
    sent: Dict[Tuple[str, str], List[LogRow]] = collections.defaultdict(list)
    for r in rows:
        result.upto_id = max(result.upto_id, r.id)
        sent[(r.machine_uid, r.ts)].append(r)
        if r.kind not in CLASSIFIED_KINDS:
            continue
        result.rows[r.id] = r
        by_machine[r.machine_uid].append(r)

    for uid in sorted(by_machine):
        seq = sorted(by_machine[uid], key=lambda r: (r.ts, r.id))
        # visible copies so far, per fingerprint (ids, oldest first)
        kept: Dict[Tuple[str, ...], List[int]] = collections.defaultdict(list)
        polls_seen: List[PollStat] = []
        # the newest row so far per (kind, lab_id): imported or not
        last_lab: Dict[Tuple[str, str], bool] = {}
        start = 0
        while start < len(seq):
            end = start
            while end < len(seq) and seq[end].ts == seq[start].ts:
                end += 1
            poll = seq[start:end]
            start = end
            n = len(poll)

            in_order = sorted(sent[(uid, poll[0].ts)], key=lambda r: r.id)
            copies = _import_resends([r for r in poll if r.is_imported()])
            copies.update(_live_resends(
                [r for r in in_order if not r.is_imported()]))
            pos = {r.id: k for k, r in enumerate(poll)}
            resent = {pos[i]: v for i, v in copies.items()}
            rest = [k for k in range(n) if k not in resent]
            used: Dict[Tuple[str, ...], int] = collections.Counter()
            twin_of: Dict[int, int] = {}
            # readings the record already held before this poll
            seen = {k for k in rest if kept.get(fingerprint(poll[k]))}
            for k in rest:
                f = fingerprint(poll[k])
                if used[f] < len(kept[f]):
                    twin_of[k] = kept[f][used[f]]
                used[f] += 1
            m = len(twin_of)
            majority = (m > 0 and n >= MAJORITY_MIN_ROWS
                        and m >= MAJORITY_SHARE * len(rest))
            # Either way, a repeat with fewer than two SAMPLES in it is the
            # lab's standards and blanks read again (round-3 critic: five QC
            # standards re-read the next morning; a Blank and a Solvent at
            # the head of a catch-up poll) and is left to review.
            standards_only = len(_samples(poll[k] for k in twin_of)) < 2
            if majority and not standards_only:
                copied = set(twin_of)
            elif majority:
                copied = set()
            elif n >= BURST_ROWS:
                copied = _replayed_stretches(poll, rest, twin_of, seen)
            else:
                copied = set()
            rule = "burst" if n >= BURST_ROWS else "majority"

            def put(k, label, why, dup_of):
                r = poll[k]
                if label in ("resend", "replay_duplicate") and r.is_imported():
                    label, why = "import_leftover", "import_" + why
                result.candidates[r.id] = Candidate(
                    r.id, uid, r.ts, r.kind, label, why, dup_of, n)

            for k, (orig, why) in resent.items():
                put(k, "resend", why, orig)
            in_poll: Dict[Tuple[str, ...], int] = {}
            for k in rest:
                r = poll[k]
                f = fingerprint(r)
                if k in twin_of:
                    if k in copied:
                        put(k, "replay_duplicate", rule, twin_of[k])
                        in_poll.setdefault(f, r.id)
                        continue
                    put(k, "probable_duplicate",
                        "fewer_than_two_samples" if majority
                        else "lone_twin_in_burst" if n >= BURST_ROWS
                        else "twin_not_in_burst", twin_of[k])
                elif f in in_poll:
                    # The same reading again in this poll with nothing to
                    # prove a second INSERT: content as far as anybody can
                    # tell, so it counts -- and it is LISTED, beside the
                    # visible copy (or the poll's first), for a person.
                    put(k, "probable_duplicate", "repeat_in_poll",
                        kept[f][0] if kept[f] else in_poll[f])
                in_poll.setdefault(f, r.id)
                kept[f].append(r.id)
            for k in range(n):
                r = poll[k]
                if (r.lab_id in MISREAD_LAB_IDS and r.is_imported()
                        and r.id not in result.candidates):
                    result.candidates[r.id] = Candidate(
                        r.id, uid, r.ts, r.kind, "import_leftover",
                        "misread_lab_id", None, n)
            imported = all(r.is_imported() for r in poll)
            hidden, why = 0, collections.Counter()
            for r in poll:
                c = result.candidates.get(r.id)
                if c is not None and c.label in HIDE_CANDIDATE_LABELS:
                    hidden += 1
                elif c is not None:
                    why[c.label] += 1
                elif (r.kind, r.lab_id) not in last_lab:
                    why["new_lab_id"] += 1
                elif last_lab[(r.kind, r.lab_id)] and not r.is_imported():
                    why["import_shape"] += 1
                else:
                    why["other_values"] += 1
            for r in poll:
                last_lab[(r.kind, r.lab_id)] = r.is_imported()
            polls_seen.append(PollStat(poll[0].ts, n, hidden, bool(copied),
                                       dict(why), imported))
        result.polls[uid] = polls_seen
        storms = _storm_days(polls_seen)
        result.storm_days[uid] = storms
        if storms:
            stormy = set(storms)
            for rid, c in list(result.candidates.items()):
                if (c.machine_uid == uid and c.label in HIDE_CANDIDATE_LABELS
                        and c.ts[:10] in stormy
                        and not result.rows[rid].is_imported()):
                    result.candidates[rid] = replace(
                        c, scope="storm:" + c.ts[:10])
    return result


# ── the QC-impact report ─────────────────────────────────────────────────────

def _qc_dicts(rows: Iterable[LogRow]) -> List[dict]:
    return [{"machine_uid": r.machine_uid, "ts": r.ts, "kind": r.kind,
             "lab_id": r.lab_id, "test_name": r.test_name, "value": r.value,
             "detail": r.detail} for r in rows if r.kind == "qc"]


def _hours(later: str, earlier: str) -> Optional[float]:
    try:
        a = datetime.fromisoformat(later[:26])
        b = datetime.fromisoformat(earlier[:26])
    except (TypeError, ValueError):
        return None
    return round((a - b).total_seconds() / 3600.0, 2)


def qc_impact(result: Classification, machine_uid: Optional[str] = None,
              now: Optional[str] = None,
              labels: Sequence[str] = HIDE_CANDIDATE_LABELS,
              only_ids: Optional[Iterable[int]] = None
              ) -> List[Dict[str, Any]]:
    """Every QC series the hiding candidates change, and how.

    Built with `qc_series` — the same parser and the same `s` the QC wall and
    the uncertainty module use — over the record as it is ("before") and the
    record minus every HIDING candidate ("after"). Review labels stay in
    "after": they stay in the record.
    """
    hide = {i for i, c in result.candidates.items() if c.label in labels}
    if only_ids is not None:
        hide &= set(only_ids)
    rows = [r for r in result.rows.values()
            if machine_uid is None or r.machine_uid == machine_uid]
    rows.sort(key=lambda r: (r.ts, r.id))
    before = qc_series.series_from_rows(_qc_dicts(rows))
    after = qc_series.series_from_rows(
        _qc_dicts(r for r in rows if r.id not in hide))
    now = now or datetime.now().isoformat(timespec="seconds")
    out = []
    for key in sorted(before):
        b = before[key]
        a = after.get(key)
        b_pts = b.points
        a_pts = a.points if a else ()
        if len(a_pts) == len(b_pts):
            continue
        last_b = max(p.ts for p in b_pts) if b_pts else None
        last_a = max(p.ts for p in a_pts) if a_pts else None
        lim_b = qc_series.control_limits([p.value for p in b_pts])
        lim_a = qc_series.control_limits([p.value for p in a_pts])
        s_b = lim_b.s if lim_b else None
        s_a = lim_a.s if lim_a else None
        verdict_b = next((p.in_spec for p in reversed(b_pts)
                          if p.ts == last_b), None)
        verdict_a = next((p.in_spec for p in reversed(a_pts)
                          if p.ts == last_a), None)
        out.append({
            "machine_uid": key[0], "test_name": key[1], "standard": key[2],
            "n_before": len(b_pts), "n_after": len(a_pts),
            "last_before": last_b, "last_after": last_a,
            "last_moves": last_a != last_b,
            "last_verdict_before": verdict_b, "last_verdict_after": verdict_a,
            "age_hours_after": _hours(now, last_a) if last_a else None,
            "s_before": s_b, "s_after": s_a,
            "spread_changes": (s_b is None) != (s_a is None) or (
                s_b is not None and abs(s_b - s_a) > 1e-12),
            "spread_basis_before": qc_series.coverage(b_pts).basis,
            "spread_basis_after": (qc_series.coverage(a_pts).basis
                                   if a_pts else None),
        })
    return out


# ── the dry-run report ───────────────────────────────────────────────────────

def _examples(result: Classification, uid: str,
              only: Optional[Sequence[str]] = None,
              unit: Optional[str] = None) -> List[Dict[str, Any]]:
    """Twenty, spread over every label the bench has (or the `only` ones,
    or one approval `unit`) and over time, each beside the row it
    duplicates."""
    by_label: Dict[str, List[Candidate]] = collections.defaultdict(list)
    for c in result.candidates.values():
        if c.machine_uid == uid and (unit is None or c.unit == unit):
            by_label[c.label].append(c)
    for v in by_label.values():
        v.sort(key=lambda c: (c.ts, c.log_id))
    labels = [l for l in HIDE_CANDIDATE_LABELS + REVIEW_LABELS
              if by_label.get(l) and (only is None or l in only)]
    picked: List[Candidate] = []
    # round-robin over labels, taking evenly spaced rows within each
    quota = {l: 0 for l in labels}
    remaining = min(EXAMPLES_PER_BENCH, sum(len(by_label[l]) for l in labels))
    while remaining:
        for l in labels:
            if remaining and quota[l] < len(by_label[l]):
                quota[l] += 1
                remaining -= 1
    for l in labels:
        pool, q = by_label[l], quota[l]
        step = len(pool) / float(q)
        picked.extend(pool[int(i * step)] for i in range(q))
    out = []
    for c in picked:
        row = result.rows[c.log_id].brief()
        row.update({"label": c.label, "rule": c.rule, "dup_of": c.dup_of,
                    "poll_rows": c.poll_rows, "unit": c.unit,
                    "original": (result.rows[c.dup_of].brief()
                                 if c.dup_of in result.rows else None)})
        out.append(row)
    return out


def _storms(result: Classification, uid: str) -> List[Dict[str, Any]]:
    """Days with ten or more bursts (G1's proxy, polls of ≥ 20 rows) or
    replay polls — O9's 08-07 and 08-18 — each with what its rows are, so a
    storm is reviewed as an event and never approved as part of a total."""
    per_day: Dict[str, Dict[str, Any]] = {}
    stormy = set(result.storm_days.get(uid, ()))
    for p in result.polls.get(uid, ()):
        if p.imported or p.ts[:10] not in stormy or not (
                p.replay or p.rows >= BURST_ROWS):
            continue
        d = per_day.setdefault(p.ts[:10], {
            "day": p.ts[:10], "polls": 0, "replay_polls": 0, "rows": 0,
            "hide_candidates": 0, "not_hidden": collections.Counter()})
        d["polls"] += 1
        d["replay_polls"] += int(p.replay)
        d["rows"] += p.rows
        d["hide_candidates"] += p.hidden
        d["not_hidden"].update(p.kept)
    out = []
    for day in sorted(per_day):
        d = per_day[day]
        d["not_hidden"] = dict(d["not_hidden"])
        d["unit_suffix"] = "@storm:" + day
        out.append(d)
    return out


def _bursts(result: Classification, uid: str) -> Dict[str, Any]:
    """G1's proxy — every poll of ≥ 20 rows — beside what the rules make of
    its rows. O9 asked how many burst rows are really copies; this is the
    answer per bench, with the rest sorted by why they stay."""
    out = {"polls": 0, "rows": 0, "hide_candidates": 0,
           "not_hidden": collections.Counter()}
    for p in result.polls.get(uid, ()):
        if p.rows >= BURST_ROWS:
            out["polls"] += 1
            out["rows"] += p.rows
            out["hide_candidates"] += p.hidden
            out["not_hidden"].update(p.kept)
    out["not_hidden"] = dict(out["not_hidden"])
    return out


def _since(result: Classification, uid: str, since: str) -> Dict[str, Any]:
    run_rows = [r for r in result.rows.values()
                if r.machine_uid == uid and r.kind == "run" and r.ts >= since]
    per_poll = collections.Counter(r.ts for r in run_rows)
    hide = sum(1 for r in run_rows
               if r.id in result.candidates
               and result.candidates[r.id].label in HIDE_CANDIDATE_LABELS)
    return {"from": since, "run_rows": len(run_rows),
            "run_rows_in_polls_of_20_or_more": sum(
                n for n in per_poll.values() if n >= BURST_ROWS),
            "run_hide_candidates": hide}


#: The prediction §10.5 makes for this lab's record (transfer-final.md
#: §10.5 "Predicted"), with G1's burst rows per bench since 09-01
#: (baseline gaps.md G1). The uids are the benches G1 measured.
SPEC_PREDICTION: Dict[str, Any] = {
    "since": "2026-09-01",
    "total": [110000, 140000],
    "band": 0.10,
    "benches": {
        "bf8e64b59f12": {"name": "Agilent GC 1", "burst_rows": 3235},
        "3afa991a66e9": {"name": "Agilent GC 2", "burst_rows": 654},
        "ae05c9c117d7": {"name": "Eraspec", "burst_rows": 3482},
        "5345176988c2": {"name": "Eraspec NIR", "burst_rows": 24888},
    },
    # What the 110k-140k was scaled from: ASK-CLAUDE.md (3 Sep) "about
    # 99,000 of 220,841 rows ... exist because a sample was written again on
    # a later day". Ids are contiguous from 1 with nothing deleted, so the
    # record of 220,841 rows is exactly the rows with id <= 220,841.
    "earlier_claim": {
        "source": "ASK-CLAUDE.md, 3 Sep 2026",
        "upto_id": 220841,
        "claimed": 99000,
        "benches": {"bf8e64b59f12": 49800, "5345176988c2": 26000,
                    "ae05c9c117d7": 22700},
    },
}


def prediction_check(result: Classification,
                     prediction: Dict[str, Any]) -> Dict[str, Any]:
    """The O9 prediction against what the record's CONTENT allows.

    The prediction was made from G1's proxy (rows in polls of >= 20) before
    anybody compared rows. §10.5 calls a row a duplicate only if an
    identical EARLIER row exists on its bench, so the rows that have one are
    the most any rule faithful to §10.5 could propose: the ceiling. Where
    the ceiling is below the predicted floor, no rule reaches the prediction
    without hiding readings that have no earlier copy — and `only_copy_rows`
    counts the burst rows that are the ONLY copy of their reading anywhere
    in the record (hiding those deletes the reading from what anybody sees).

    Every number here is computed from the rows; nothing is estimated.
    """
    since = prediction["since"]
    band = float(prediction["band"])
    hide = {i for i, c in result.candidates.items()
            if c.label in HIDE_CANDIDATE_LABELS}
    review = {i for i, c in result.candidates.items()
              if c.label in REVIEW_LABELS}
    everywhere: Dict[Tuple[str, ...], int] = collections.Counter(
        fingerprint(r) for r in result.rows.values())
    has_earlier = _has_earlier(result.rows.values())
    firsts = _first_appearances(result.rows.values())
    by_machine: Dict[str, List[LogRow]] = collections.defaultdict(list)
    for r in result.rows.values():
        by_machine[r.machine_uid].append(r)

    def accounted(rows: Iterable[LogRow]) -> Dict[str, int]:
        """Where each row goes: a proposed copy, a row listed for review,
        or the first copy of its reading on its bench. A twin the rules
        neither proposed nor listed would be a fourth bucket, and a bug:
        it is counted, never folded into the others."""
        out = {"hide_candidates": 0, "listed_for_review": 0,
               "first_copy_of_its_reading": 0}
        for r in rows:
            if r.id in hide:
                out["hide_candidates"] += 1
            elif r.id in review:
                out["listed_for_review"] += 1
            elif r.id in has_earlier:
                out["unlabelled_twin"] = out.get("unlabelled_twin", 0) + 1
            else:
                out["first_copy_of_its_reading"] += 1
        return out

    benches = []
    for uid, want in sorted(prediction.get("benches", {}).items()):
        runs = [r for r in by_machine.get(uid, ())
                if r.kind == "run" and r.ts >= since]
        per_poll = collections.Counter(r.ts for r in runs)
        burst = [r for r in runs if per_poll[r.ts] >= BURST_ROWS]
        predicted = int(want["burst_rows"])
        lo, hi = predicted * (1 - band), predicted * (1 + band)
        proposed = sum(1 for r in runs if r.id in hide)
        ceiling = sum(1 for r in runs if r.id in has_earlier)
        no_twin = [r for r in burst if r.id not in has_earlier]
        only = sorted((r for r in burst if everywhere[fingerprint(r)] == 1),
                      key=lambda r: (r.ts, r.id))
        step = max(1, len(only) // EXAMPLES_PER_BENCH)
        need = int(math.ceil(lo - 1e-9))
        new = sorted((r for r in runs if r.id in firsts),
                     key=lambda r: (r.ts, r.id))
        loose = len(runs) - len(new)
        nstep = max(1, len(new) // EXAMPLES_PER_BENCH)
        benches.append({
            "machine_uid": uid, "name": want.get("name", uid),
            "since": since, "predicted": predicted, "band": [lo, hi],
            "burst_rows_measured": len(burst),
            "burst_rows_accounted": accounted(burst),
            "hide_needed_for_band": need,
            # visible rows must cover every distinct reading of the window
            # (rows - ceiling of them first appear there); hiding `need`
            # leaves rows - need, so at least need - ceiling vanish.
            "readings_erased_at_band_floor": max(0, need - ceiling),
            "proposed": proposed,
            "proposed_vs_predicted_pct": round(
                100.0 * (proposed - predicted) / predicted, 1),
            "ceiling": ceiling,
            "ceiling_vs_predicted_pct": round(
                100.0 * (ceiling - predicted) / predicted, 1),
            "no_earlier_twin": len(no_twin),
            "only_copy_rows": len(only),
            "only_copy_examples": [r.brief() for r in
                                   only[::step][:EXAMPLES_PER_BENCH]],
            "within_band": lo <= proposed <= hi,
            "reachable": ceiling >= lo,
            # Without §10.5's key: every row of the window except the first
            # appearance of its Lab ID on the bench. Re-tests and
            # re-processed results count here, so this is generous.
            "any_definition_ceiling": loose,
            "first_appearances": len(new),
            "first_appearance_examples": [
                r.brief() for r in new[::nstep][:EXAMPLES_PER_BENCH]],
            "reachable_by_any_definition": loose >= lo,
        })
    t_lo, t_hi = prediction["total"]
    replay = sum(1 for c in result.candidates.values()
                 if c.label == "replay_duplicate")
    ceiling = len(has_earlier)
    poll_n = collections.Counter((r.machine_uid, r.ts)
                                 for r in result.rows.values())
    in_bursts = [r for r in result.rows.values()
                 if poll_n[(r.machine_uid, r.ts)] >= BURST_ROWS]
    burst_rows = len(in_bursts)
    out = {
        "source": "transfer-final.md §10.5 Predicted; G1 (baseline gaps.md)",
        "total": {
            "predicted": [t_lo, t_hi],
            "proposed_replay_duplicate": replay,
            "proposed_hide": len(hide),
            "ceiling": ceiling,
            "rows_in_polls_of_20_or_more": burst_rows,
            "proxy_within_predicted": t_lo <= burst_rows <= t_hi,
            "burst_rows_accounted": accounted(in_bursts),
            "within_band": t_lo <= replay <= t_hi,
            "reachable": ceiling >= t_lo,
            "readings_erased_at_floor": max(0, int(t_lo) - ceiling),
            "any_definition_ceiling": len(result.rows) - len(firsts),
            "reachable_by_any_definition":
                len(result.rows) - len(firsts) >= t_lo,
        },
        "benches": benches,
    }
    claim = prediction.get("earlier_claim")
    if claim:
        upto = int(claim["upto_id"])
        then = [r for r in result.rows.values() if r.id <= upto]
        earlier_then = _has_earlier(then)
        per = collections.Counter(result.rows[i].machine_uid
                                  for i in earlier_then)
        names = {u: w.get("name", u)
                 for u, w in prediction.get("benches", {}).items()}
        out["earlier_claim"] = {
            "source": claim.get("source", ""),
            "upto_id": upto,
            "run_qc_rows_then": len(then),
            "claimed": int(claim["claimed"]),
            "ceiling_then": len(earlier_then),
            "reachable": len(earlier_then) >= int(claim["claimed"]),
            "benches": [{"machine_uid": u, "name": names.get(u, u),
                         "claimed": int(n), "ceiling_then": per[u],
                         "reachable": per[u] >= int(n)}
                        for u, n in sorted(claim.get("benches", {}).items())],
        }
    return out


def _first_appearances(rows: Iterable[LogRow]) -> set:
    """Ids of the first row of each (bench, Lab ID), in (ts, id) order.

    A bench can only send AGAIN what it has sent before, so these rows are
    first appearances under ANY definition of a re-emission — exact copy,
    re-processed result, re-test — and every other row is, at most, one.
    No fingerprint is involved: this bound does not lean on §10.5's key."""
    first: Dict[Tuple[str, str], Tuple[str, int]] = {}
    for r in rows:
        k = (r.machine_uid, r.lab_id)
        if k not in first or (r.ts, r.id) < first[k]:
            first[k] = (r.ts, r.id)
    return {i for _ts, i in first.values()}


def _has_earlier(rows: Iterable[LogRow]) -> set:
    """Ids of the rows with an identical EARLIER row (by `(ts, id)`) on the
    same bench: the most any rule faithful to §10.5 can call a copy. Their
    number is rows − distinct readings, bench by bench."""
    by_machine: Dict[str, List[LogRow]] = collections.defaultdict(list)
    for r in rows:
        by_machine[r.machine_uid].append(r)
    out: set = set()
    for rs in by_machine.values():
        seen: set = set()
        for r in sorted(rs, key=lambda r: (r.ts, r.id)):
            f = fingerprint(r)
            if f in seen:
                out.add(r.id)
            seen.add(f)
    return out


def report(result: Classification, *, since: Optional[str] = None,
           now: Optional[str] = None, source: str = "",
           prediction: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The dry run (§10.5 step 1) with its QC impact (step 2). Plain JSON."""
    benches = []
    totals: Dict[str, int] = collections.Counter()
    for uid in result.machines():
        cands = [c for c in result.candidates.values() if c.machine_uid == uid]
        storms = _storms(result, uid)
        if not cands and not storms:
            continue
        by_kind: Dict[str, Dict[str, int]] = {}
        counts: Dict[str, int] = collections.Counter()
        for c in cands:
            by_kind.setdefault(c.kind, collections.Counter())[c.label] += 1
            counts[c.label] += 1
            totals[c.label] += 1
        bench = {
            "machine_uid": uid,
            "rows": sum(1 for r in result.rows.values()
                        if r.machine_uid == uid),
            "candidates": dict(counts),
            "by_kind": {k: dict(v) for k, v in by_kind.items()},
            "run_ids": {u: result.run_id(uid, u)
                        for u in result.units(uid)},
            "units": [{"rule": u, "label": parse_unit(u)[0],
                       "storm": "@" in u,
                       "candidates": len(result.ids(uid, u)),
                       "run_id": result.run_id(uid, u)}
                      for u in result.units(uid)],
            "examples": _examples(result, uid),
            "storms": storms,
            "bursts": _bursts(result, uid),
            "qc_impact": qc_impact(result, uid, now),
        }
        if since:
            bench["since"] = _since(result, uid, since)
        benches.append(bench)
    return {
        "source": source,
        "upto_id": result.upto_id,
        "rows_read": len(result.rows),
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "totals": dict(totals),
        "hide_candidates": sum(totals.get(l, 0)
                               for l in HIDE_CANDIDATE_LABELS),
        "benches": benches,
        "qc_impact": [s for b in benches for s in b["qc_impact"]],
        "prediction": (prediction_check(result, prediction)
                       if prediction else None),
    }


# ── reading ──────────────────────────────────────────────────────────────────

def _require_store(gateway) -> None:
    from lem_store import is_local_store
    if not is_local_store(gateway):
        raise DedupeRefused(
            "dedupe runs on LEM's own store (or a copy of the log mirror), "
            "never on LabCore: nothing was read or written")


def _read(gateway, sql: str, args: Sequence[Any], what: str) -> List[dict]:
    res = gateway.read_sql(sql, list(args))
    if not isinstance(res, dict) or res.get("error") or "rows" not in res:
        why = res.get("error") if isinstance(res, dict) else repr(res)
        raise DedupeReadError("could not read {0}: {1}".format(what, why))
    return res["rows"]


def read_store(gateway, machine_uid: Optional[str] = None,
               upto_id: Optional[int] = None) -> Tuple[List[LogRow], int]:
    """Every row (of one bench, or all), and the id the read stops at. With
    `upto_id`, exactly the rows a report was taken over."""
    _require_store(gateway)
    if upto_id is None:
        # Read the newest id FIRST and classify only up to it: a row that
        # lands during the read is then outside the report rather than half
        # in it. The run id names the whole record read — rows of kinds the
        # classifier ignores included — so this is the record's true newest.
        top = _read(gateway,
                    # raw-log: the newest id in the record, hidden or not.
                    "SELECT MAX(id) AS i FROM lem_machine_log", [],
                    "the record's newest id")
        upto_id = top[0]["i"] if top and top[0]["i"] is not None else 0
    # EVERY kind: the classifier labels only `run` and `qc`, but a resend is
    # recognised in the order the drain SENT rows, status changes included
    # (a QC repeat with a status change between is not a repeated block).
    where = ["id <= ?"]
    args: List[Any] = [int(upto_id)]
    if machine_uid is not None:
        where.append("machine_uid = ?")
        args.append(machine_uid)
    # Dedupe classifies the WHOLE record: a row already hidden is still the
    # earlier twin of the next replay, and a row's own candidacy must not
    # change because somebody approved another.
    sql = ("SELECT id, machine_uid, ts, kind, lab_id, test_name, value, "
           # raw-log: hidden rows included, on purpose (above).
           "detail FROM lem_machine_log WHERE ")
    rows = _read(gateway, sql + " AND ".join(where), args, "the machine log")
    return [LogRow.from_dict(r) for r in rows], int(upto_id)


def read_mirror(path: str) -> List[LogRow]:
    """Every row of a log-mirror COPY, refused if the copy is part-filled.

    Opened read-only (`mode=ro`): the dry run never writes, not even to a
    copy. A copy whose last fill failed holds a prefix of the record, and a
    prefix would report "no twin" for every row whose original it is
    missing.
    """
    try:
        con = sqlite3.connect("file:{0}?mode=ro".format(path), uri=True)
    except sqlite3.Error as exc:
        raise DedupeReadError("could not open the mirror copy {0}: {1}"
                              .format(path, exc))
    try:
        try:
            meta = dict(con.execute("SELECT k, v FROM meta").fetchall())
            if meta.get("stale_reason"):
                raise DedupeReadError(
                    "the mirror copy's last fill failed ({0}); it holds part "
                    "of the record".format(meta["stale_reason"]))
            if not meta.get("filled_at"):
                raise DedupeReadError("the mirror copy was never filled")
            rows = con.execute(
                "SELECT rowid_src, machine_uid, ts, kind, lab_id, test_name, "
                "value, detail FROM log").fetchall()
        except sqlite3.Error as exc:
            raise DedupeReadError("could not read the mirror copy {0}: {1}"
                                  .format(path, exc))
    finally:
        con.close()
    return [LogRow(int(r[0]), str(r[1] or ""), str(r[2] or ""),
                   str(r[3] or ""), str(r[4] or ""), str(r[5] or ""),
                   "" if r[6] is None else str(r[6]),
                   "" if r[7] is None else str(r[7])) for r in rows]


def dry_run(gateway, machine_uid: Optional[str] = None, *,
            since: Optional[str] = None,
            now: Optional[str] = None) -> Dict[str, Any]:
    """Classify the store's record and report. Reads; writes nothing."""
    rows, upto = read_store(gateway, machine_uid)
    rep = report(classify(rows, upto), since=since, now=now, source="store")
    rep["unsigned_approvals"] = unsigned_approvals(gateway)
    return rep


# ── approval, apply, reinstate ───────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _one_bench(gateway, machine_uid: str, upto: int) -> Classification:
    rows, upto = read_store(gateway, machine_uid, upto_id=upto)
    return classify(rows, upto)


# ── the approval's signature ────────────────────────────────────────────────

def approval_key(gateway) -> bytes:
    """The key beside the store (`LocalStoreGateway.approval_key`)."""
    try:
        return gateway.approval_key()
    except Exception as exc:                            # noqa: BLE001
        raise DedupeRefused(str(exc))


def _sign(gateway, row: Dict[str, Any]) -> str:
    return gateway.sign_approval(row)


def signature_ok(gateway, row: Dict[str, Any]) -> bool:
    return bool(gateway.approval_signature_ok(row))


def unsigned_approvals(gateway) -> List[Dict[str, Any]]:
    """Approvals that do not verify AND hide at least one row now (their
    hide is the newest deciding annotation on it). Named in every dry run:
    the store cannot stop an INSERT that reaches the file, but it is not
    silent about one."""
    out = []
    for appr in _read(gateway, "SELECT * FROM annotation_approval "
                      "WHERE decision = 'approved' ORDER BY id", [],
                      "the approvals"):
        if signature_ok(gateway, appr):
            continue
        hides = _read(
            gateway,
            "SELECT COUNT(*) AS n FROM log_annotation a WHERE "
            "a.approval_id = ? AND a.label IN ('replay_duplicate', "
            "'import_leftover') AND a.id = (SELECT MAX(b.id) FROM "
            "log_annotation b WHERE b.log_id = a.log_id AND b.label IN "
            "('replay_duplicate', 'import_leftover', 'reinstated'))",
            [appr["id"]], "the rows an approval hides")[0]["n"]
        if hides:
            out.append({"approval_id": appr["id"],
                        "machine_uid": appr["machine_uid"],
                        "rule": appr["rule"],
                        "approved_by": appr["approved_by"],
                        "approved_at": appr["approved_at"], "hides": hides})
    return out


def approve(gateway, machine_uid: str, label: str, run_id: str, *,
            approved_by: str, decision: str = "approved",
            now: Optional[str] = None) -> Dict[str, Any]:
    """Record Ryan's decision on ONE bench's candidates for ONE rule (D7).

    The candidate set is recomputed from the record the report was read from
    (`upto` in the run id) and must match the digest he was shown; if it does
    not, the record changed under the report and the approval is refused.
    The approval row carries the count, the examples and the QC impact, so
    the record of the decision says what the decision was about.
    """
    _require_store(gateway)
    unit = label
    label, _scope = parse_unit(unit)
    if decision not in ("approved", "rejected"):
        raise DedupeRefused("a decision is 'approved' or 'rejected'")
    if not str(approved_by or "").strip():
        raise DedupeRefused("an approval needs the name of the person "
                            "approving")
    upto, _sha = parse_run_id(run_id)
    result = _one_bench(gateway, machine_uid, upto)
    current = result.run_id(machine_uid, unit)
    if current != run_id:
        raise DedupeRefused(
            "the candidates for {0} / {1} have changed since that report "
            "(it named {2}, the record now gives {3}): run a new dry run"
            .format(machine_uid, unit, run_id, current))
    ids = result.ids(machine_uid, unit)
    if not ids:
        raise DedupeRefused("there is nothing to approve for {0} / {1}"
                            .format(machine_uid, unit))
    examples = _examples(result, machine_uid, unit=unit)
    signed = {"machine_uid": machine_uid, "rule": unit, "run_id": run_id,
              "candidates": len(ids), "approved_by": approved_by.strip(),
              "approved_at": now or _now(), "decision": decision}
    signature = _sign(gateway, signed)
    try:
        aid = gateway.record_approval(
            signature=signature,
            members=ids if decision == "approved" else (),
            examples=json.dumps(examples),
            qc_impact=json.dumps(qc_impact(result, machine_uid, now,
                                           (label,), ids)),
            **signed)
    except Exception as exc:                            # noqa: BLE001
        raise DedupeRefused("the approval was not recorded: {0}".format(exc))
    return {"approval_id": aid, "machine_uid": machine_uid,
            "label": label, "unit": unit, "candidates": len(ids),
            "decision": decision}


def _annotation_history(gateway, ids: Sequence[int]) -> Dict[int, List[dict]]:
    out: Dict[int, List[dict]] = collections.defaultdict(list)
    ids = list(ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in _read(gateway,
                       "SELECT log_id, label, approval_id FROM log_annotation"
                       " WHERE log_id IN ({0}) ORDER BY id".format(
                           ",".join("?" * len(chunk))), chunk,
                       "the rows' annotations"):
            out[r["log_id"]].append(r)
    return out


_DECIDING = ("replay_duplicate", "import_leftover", "reinstated")


def apply(gateway, approval_id: int, *, by: str) -> Dict[str, Any]:
    """Write one annotation per approved candidate, naming the approval.

    Skips a row already hidden (applying twice annotates once) and a row a
    person ever reinstated: a reinstatement is a decision about that one
    reading, and a later bench-wide approval must not quietly undo it.
    """
    _require_store(gateway)
    if not str(by or "").strip():
        raise DedupeRefused("apply needs the name of the person applying")
    rows = _read(gateway, "SELECT * FROM annotation_approval WHERE id = ?",
                 [int(approval_id)], "the approval")
    if not rows:
        raise DedupeRefused("there is no approval {0}".format(approval_id))
    appr = rows[0]
    if appr["decision"] != "approved":
        raise DedupeRefused("approval {0} was {1}: nothing to apply"
                            .format(approval_id, appr["decision"]))
    if not signature_ok(gateway, appr):
        raise DedupeRefused(
            "approval {0} carries no valid signature: it was not recorded by "
            "the approval flow that showed the report (D7), so nothing is "
            "applied under it".format(approval_id))
    uid, unit = appr["machine_uid"], appr["rule"]
    label, _scope = parse_unit(unit)
    upto, _sha = parse_run_id(appr["run_id"])
    result = _one_bench(gateway, uid, upto)
    if result.run_id(uid, unit) != appr["run_id"]:
        # Cannot happen on an append-only record; said loudly if it does.
        raise DedupeRefused("the record below id {0} no longer gives the "
                            "candidates approval {1} was given".format(
                                upto, approval_id))
    ids = result.ids(uid, unit)
    history = _annotation_history(gateway, ids)
    todo, skipped_hidden, skipped_reinstated = [], 0, 0
    for rid in ids:
        h = [a for a in history.get(rid, ()) if a["label"] in _DECIDING]
        if any(a["label"] == "reinstated" for a in h):
            skipped_reinstated += 1
        elif h and h[-1]["label"] != "reinstated":
            skipped_hidden += 1
        else:
            todo.append(rid)
    at = _now()
    # In transactions of APPLY_CHUNK rows: one transaction over Eraspec NIR's
    # 40,000 would hold the store's single writer for ~15 s, and every bench
    # sync behind it would wait. A crash between chunks leaves a prefix
    # applied, and running apply again finishes it (hidden rows are skipped).
    for start in range(0, len(todo), APPLY_CHUNK):
        _apply_chunk(gateway, result, todo[start:start + APPLY_CHUNK],
                     label, appr, approval_id, by, at)
    return {"approval_id": int(approval_id), "machine_uid": uid,
            "label": label, "unit": unit, "annotated": len(todo),
            "already_hidden": skipped_hidden,
            "kept_reinstated": skipped_reinstated}


def _apply_chunk(gateway, result, ids, label, appr, approval_id, by, at):
    with gateway.transaction():
        for rid in ids:
            c = result.candidates[rid]
            res = gateway.sql(
                "INSERT INTO log_annotation (log_id, label, dup_of, rule, "
                "run_id, by, at, approval_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [rid, ANNOTATION_LABEL[label], c.dup_of,
                 "{0}:{1}".format(c.unit, c.rule), appr["run_id"],
                 by.strip(), at, int(approval_id)])
            if res.get("error"):
                raise DedupeRefused("annotating row {0} was refused: {1}"
                                    .format(rid, res["error"]))


def reinstate(gateway, log_ids: Sequence[int], *, by: str,
              reason: str) -> Dict[str, Any]:
    """Put hidden rows back, one `reinstated` annotation each, with why."""
    _require_store(gateway)
    if not str(by or "").strip() or not str(reason or "").strip():
        raise DedupeRefused("a reinstatement needs a person and a reason")
    ids = sorted({int(i) for i in log_ids})
    history = _annotation_history(gateway, ids)
    back, not_hidden = [], []
    at = _now()
    with gateway.transaction():
        for rid in ids:
            h = [a for a in history.get(rid, ()) if a["label"] in _DECIDING]
            if not h or h[-1]["label"] == "reinstated":
                not_hidden.append(rid)
                continue
            res = gateway.sql(
                "INSERT INTO log_annotation (log_id, label, rule, by, at) "
                "VALUES (?, 'reinstated', ?, ?, ?)",
                [rid, reason.strip(), by.strip(), at])
            if res.get("error"):
                raise DedupeRefused("reinstating row {0} was refused: {1}"
                                    .format(rid, res["error"]))
            back.append(rid)
    return {"reinstated": back, "not_hidden": not_hidden}


# ── the dry run from a shell ─────────────────────────────────────────────────

def summary_lines(rep: Dict[str, Any], names: Optional[Dict[str, str]] = None
                  ) -> List[str]:
    """The report as a person reads it at a terminal: totals, then a line
    per bench, then the series whose QC would change."""
    names = names or {}
    out = ["read {0:,} run/qc rows (record up to id {1}); hide candidates "
           "{2:,}: {3}".format(rep["rows_read"], rep["upto_id"],
                               rep["hide_candidates"],
                               ", ".join("{0} {1:,}".format(k, v)
                                         for k, v in sorted(
                                             rep["totals"].items())))]
    for b in sorted(rep["benches"], key=lambda b: -sum(
            b["candidates"].get(l, 0) for l in HIDE_CANDIDATE_LABELS)):
        line = "  {0:<22} rows {1:>7,}  ".format(
            names.get(b["machine_uid"], b["machine_uid"]), b["rows"])
        line += "  ".join("{0} {1:,}".format(l, b["candidates"][l])
                          for l in HIDE_CANDIDATE_LABELS + REVIEW_LABELS
                          if b["candidates"].get(l))
        if b.get("since"):
            s = b["since"]
            line += "  | since {0}: hide {1:,} of {2:,} run rows; " \
                    "in polls>=20 {3:,}".format(
                        s["from"], s["run_hide_candidates"], s["run_rows"],
                        s["run_rows_in_polls_of_20_or_more"])
        if b["storms"]:
            line += "  | storm days " + "; ".join(
                "{0}: {1} polls, {2:,} rows, {3:,} hide, kept {4}".format(
                    x["day"], x["polls"], x["rows"], x["hide_candidates"],
                    x["not_hidden"]) for x in b["storms"])
        out.append(line)
    agg: Dict[str, int] = collections.Counter()
    for b in rep["benches"]:
        agg["polls"] += b["bursts"]["polls"]
        agg["rows"] += b["bursts"]["rows"]
        agg["hide"] += b["bursts"]["hide_candidates"]
        for k, v in b["bursts"]["not_hidden"].items():
            agg["kept: " + k] += v
    out.append("rows in polls of >= 20 (G1's proxy): {0:,} in {1:,} polls; "
               "{2:,} are hide candidates; the rest: {3}".format(
                   agg["rows"], agg["polls"], agg["hide"], ", ".join(
                       "{0} {1:,}".format(k[6:], v)
                       for k, v in sorted(agg.items())
                       if k.startswith("kept: "))))
    pred = rep.get("prediction")
    if pred:
        out.extend(o9_lines(pred))
    moved = [s for s in rep["qc_impact"] if s["last_moves"]]
    spread = [s for s in rep["qc_impact"] if s["spread_changes"]]
    out.append("QC impact: {0} series change; last verdict moves earlier on "
               "{1}; spread changes on {2}".format(
                   len(rep["qc_impact"]), len(moved), len(spread)))
    for s in rep["qc_impact"]:
        out.append("  {0:<14} {1:<55.55} {2:<6} n {3}->{4}  last {5} -> {6}"
                   "  s {7} -> {8}".format(
                       names.get(s["machine_uid"], s["machine_uid"]),
                       s["test_name"], s["standard"], s["n_before"],
                       s["n_after"], (s["last_before"] or "-")[:16],
                       (s["last_after"] or "-")[:16],
                       "-" if s["s_before"] is None else
                       "%.4g" % s["s_before"],
                       "-" if s["s_after"] is None else
                       "%.4g" % s["s_after"]))
    return out


def _acc(a: Dict[str, int]) -> str:
    return ", ".join("{0} {1:,}".format(k.replace("_", " "), v)
                     for k, v in a.items())


def o9_lines(pred: Dict[str, Any]) -> List[str]:
    """O9, closed as arithmetic: what the prediction measured, where every
    one of those rows goes, and what reaching it would erase."""
    t = pred["total"]
    lo, hi = t["predicted"]
    out = [
        "O9 — predicted replay_duplicate {0:,}-{1:,}.".format(lo, hi),
        "  WHAT IT MEASURED: rows in polls of >= 20 (G1's proxy) = {0:,} "
        "-> {1} the predicted range. The prediction is the proxy.".format(
            t["rows_in_polls_of_20_or_more"],
            "INSIDE" if t["proxy_within_predicted"] else "outside"),
        "  WHERE THOSE ROWS GO: " + _acc(t["burst_rows_accounted"]),
        "  EXACT COUNT: replay_duplicate {0:,}; every hide candidate {1:,}; "
        "CEILING {2:,} (rows with an identical earlier row on their bench = "
        "rows - distinct readings; no rule that keeps one copy of every "
        "reading can propose more).".format(
            t["proposed_replay_duplicate"], t["proposed_hide"], t["ceiling"]),
        "  COST OF THE FLOOR: hiding {0:,} would erase at least {1:,} "
        "readings from every view -> {2}.".format(
            lo, t["readings_erased_at_floor"],
            "in band" if t["within_band"] else
            "the band needs Ryan's revision (D7), not a looser rule"),
    ]
    e = pred.get("earlier_claim")
    if e:
        out.append(
            "  THE CLAIM IT WAS SCALED FROM ({0}): {1:,} of the record up to "
            "id {2:,}; the record THEN allowed at most {3:,} ({4:,} run/qc "
            "rows) -> {5}".format(
                e["source"], e["claimed"], e["upto_id"], e["ceiling_then"],
                e["run_qc_rows_then"],
                "reachable" if e["reachable"] else "never reachable"))
        for b in e["benches"]:
            out.append("    {0:<13} claimed {1:>6,}  ceiling then {2:>6,}"
                       .format(b["name"], b["claimed"], b["ceiling_then"]))
    out.append(
        "  WITHOUT OUR KEY: rows whose Lab ID their bench had sent in an "
        "earlier poll (any content: re-tests and re-processed results "
        "counted as re-emissions too) = {0:,} -> {1}.".format(
            t["any_definition_ceiling"],
            "the floor is reached only by also hiding re-tests and "
            "re-processed results, which are readings" if
            t["reachable_by_any_definition"] else
            "no definition reaches the floor"))
    for b in pred["benches"]:
        out.append(
            "  since {0} {1:<13} any definition at most {2:>6,} "
            "(first appearances {3:,}) -> {4}".format(
                b["since"], b["name"], b["any_definition_ceiling"],
                b["first_appearances"],
                "NO definition reaches the band"
                if not b["reachable_by_any_definition"] else
                "reached only by also hiding re-tests and re-processed "
                "results" if not b["reachable"] else "reachable"))
    for b in pred["benches"]:
        out.append(
            "  since {0} {1:<13} G1 {2:>6,} band {3:,.0f}-{4:,.0f}  "
            "proposed {5:>6,} ({6:+.1f} %) {7}  ceiling {8:>6,} ({9:+.1f} %)"
            "  burst rows: {10}; to reach the band erases {11:,} readings"
            .format(b["since"], b["name"], b["predicted"], b["band"][0],
                    b["band"][1], b["proposed"],
                    b["proposed_vs_predicted_pct"],
                    "in band" if b["within_band"] else "OUT",
                    b["ceiling"], b["ceiling_vs_predicted_pct"],
                    _acc(b["burst_rows_accounted"]),
                    b["readings_erased_at_band_floor"]))
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Dedupe dry run (transfer spec §10.5). Reads only.")
    ap.add_argument("--mirror", required=True,
                    help="a COPY of the server's data/log-mirror.sqlite3")
    ap.add_argument("--since", help="also count from this ts (G1 window)")
    ap.add_argument("--now", help="the time QC ages are measured to")
    ap.add_argument("--json", help="write the full report here")
    ap.add_argument("--names", help="JSON {machine_uid: name}")
    ap.add_argument("--prediction", action="store_true",
                    help="check §10.5's O9 prediction against the record")
    a = ap.parse_args(argv)
    rows = read_mirror(a.mirror)
    rep = report(classify(rows), since=a.since, now=a.now,
                 source="mirror:" + a.mirror,
                 prediction=SPEC_PREDICTION if a.prediction else None)
    names = json.load(open(a.names)) if a.names else {}
    print("\n".join(summary_lines(rep, names)))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=1)
    return 0


if __name__ == "__main__":                              # pragma: no cover
    raise SystemExit(main())
