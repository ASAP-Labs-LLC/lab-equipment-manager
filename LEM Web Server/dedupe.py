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
module: `lem_store` refuses a hiding annotation that does not name an
approved approval for the same bench and rule, and the effective view hides
a row only through such an approval.

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
  poll, in a poll that carried ≥ 20 rows, or in which ≥ 80 % of the rows
  have such twins (the majority rule; it needs at least 5 rows, because C's
  2-row/50 % rule was rejected as too eager and a 1-row poll is "100 %").
* `probable_duplicate` — a twin that is NOT in such a poll: a genuine
  identical re-test or a QC repeat looks exactly like this. Always visible;
  listed for review.
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
import re
import sqlite3
from dataclasses import dataclass, field
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

IMPORT_TAG = "labshare-2026-08-27"
#: The import read a numeric column as the Lab ID on these (ASK-CLAUDE.md).
MISREAD_LAB_IDS = frozenset(("3659736601", "271750", "3246632466"))

#: `detail` keys a replay restamps, plus the import's provenance.
DROPPED_DETAIL_KEYS = frozenset((
    "operator", "calibration_id", "calibration", "polled_at", "poll_ts",
    "imported", "source_file"))

CLASSIFIED_KINDS = ("run", "qc")

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


@dataclass
class Classification:
    candidates: Dict[int, Candidate] = field(default_factory=dict)
    rows: Dict[int, LogRow] = field(default_factory=dict)
    polls: Dict[str, List[PollStat]] = field(default_factory=dict)
    upto_id: int = 0

    def machines(self) -> List[str]:
        return sorted({r.machine_uid for r in self.rows.values()})

    def ids(self, machine_uid: str, label: str) -> List[int]:
        return sorted(c.log_id for c in self.candidates.values()
                      if c.machine_uid == machine_uid and c.label == label)

    def run_id(self, machine_uid: str, label: str) -> str:
        """Names exactly one candidate set: the record it was read from (all
        rows with `id <= upto`) and a digest of the set itself."""
        h = hashlib.sha256()
        for rid in self.ids(machine_uid, label):
            c = self.candidates[rid]
            h.update(("%d:%s:%s:%s\n" % (rid, c.dup_of, c.rule, label)
                      ).encode("utf-8"))
        return "upto=%d;sha=%s" % (self.upto_id, h.hexdigest())


_RUN_ID = re.compile(r"^upto=(\d+);sha=([0-9a-f]{64})$")


def parse_run_id(run_id: str) -> Tuple[int, str]:
    m = _RUN_ID.match(str(run_id or ""))
    if not m:
        raise DedupeRefused("not a dry-run id: {0!r}".format(run_id))
    return int(m.group(1)), m.group(2)


def _resends(poll: List[LogRow]) -> Dict[int, int]:
    """Positions in `poll` that repeat the block right before them, mapped to
    the position they repeat. Exact rows only (N3 re-sends the same bytes)."""
    exact = [_exact(r) for r in poll]
    out: Dict[int, int] = {}
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
                out[k] = k - hit
            i += hit
        else:
            i += 1
    return out


def classify(rows: Iterable[LogRow],
             upto_id: Optional[int] = None) -> Classification:
    """Label every `run` and `qc` row that looks like a copy. Pure.

    `upto_id` is the newest id of the record the rows were read from; by
    default the newest id among them."""
    result = Classification(upto_id=int(upto_id or 0))
    by_machine: Dict[str, List[LogRow]] = collections.defaultdict(list)
    for r in rows:
        result.upto_id = max(result.upto_id, r.id)
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

            resent = _resends(poll)
            rest = [k for k in range(n) if k not in resent]
            used: Dict[Tuple[str, ...], int] = collections.Counter()
            twin_of: Dict[int, int] = {}
            for k in rest:
                f = fingerprint(poll[k])
                if used[f] < len(kept[f]):
                    twin_of[k] = kept[f][used[f]]
                used[f] += 1
            m = len(twin_of)
            replay = m > 0 and (
                n >= BURST_ROWS or (n >= MAJORITY_MIN_ROWS
                                    and m >= MAJORITY_SHARE * len(rest)))
            rule = "burst" if n >= BURST_ROWS else "majority"

            def put(k, label, why, dup_of):
                r = poll[k]
                if label in ("resend", "replay_duplicate") and r.is_imported():
                    label, why = "import_leftover", "import_" + why
                result.candidates[r.id] = Candidate(
                    r.id, uid, r.ts, r.kind, label, why, dup_of, n)

            for k, j in resent.items():
                put(k, "resend", "tandem", poll[j].id)
            for k in rest:
                r = poll[k]
                if k in twin_of:
                    if replay:
                        put(k, "replay_duplicate", rule, twin_of[k])
                        continue
                    put(k, "probable_duplicate", "twin_not_in_burst",
                        twin_of[k])
                kept[fingerprint(r)].append(r.id)
            for k in range(n):
                r = poll[k]
                if (r.lab_id in MISREAD_LAB_IDS and r.is_imported()
                        and r.id not in result.candidates):
                    result.candidates[r.id] = Candidate(
                        r.id, uid, r.ts, r.kind, "import_leftover",
                        "misread_lab_id", None, n)
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
            polls_seen.append(PollStat(poll[0].ts, n, hidden, replay,
                                       dict(why)))
        result.polls[uid] = polls_seen
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
              labels: Sequence[str] = HIDE_CANDIDATE_LABELS
              ) -> List[Dict[str, Any]]:
    """Every QC series the hiding candidates change, and how.

    Built with `qc_series` — the same parser and the same `s` the QC wall and
    the uncertainty module use — over the record as it is ("before") and the
    record minus every HIDING candidate ("after"). Review labels stay in
    "after": they stay in the record.
    """
    hide = {i for i, c in result.candidates.items() if c.label in labels}
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
              only: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """Twenty, spread over every label the bench has (or the `only` ones)
    and over time, each beside the row it duplicates."""
    by_label: Dict[str, List[Candidate]] = collections.defaultdict(list)
    for c in result.candidates.values():
        if c.machine_uid == uid:
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
                    "poll_rows": c.poll_rows,
                    "original": (result.rows[c.dup_of].brief()
                                 if c.dup_of in result.rows else None)})
        out.append(row)
    return out


def _storms(result: Classification, uid: str) -> List[Dict[str, Any]]:
    """Days with ten or more bursts (G1's proxy, polls of ≥ 20 rows) or
    replay polls — O9's 08-07 and 08-18 — each with what its rows are, so a
    storm is reviewed as an event and never approved as part of a total."""
    per_day: Dict[str, Dict[str, Any]] = {}
    for p in result.polls.get(uid, ()):
        if not (p.replay or p.rows >= BURST_ROWS):
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
        if d["polls"] >= STORM_POLLS_PER_DAY:
            d["not_hidden"] = dict(d["not_hidden"])
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


def report(result: Classification, *, since: Optional[str] = None,
           now: Optional[str] = None, source: str = "") -> Dict[str, Any]:
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
            "run_ids": {l: result.run_id(uid, l)
                        for l in HIDE_CANDIDATE_LABELS if counts.get(l)},
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
    """The `run` and `qc` rows (of one bench, or all), and the id the read
    stops at. With `upto_id`, exactly the rows a report was taken over."""
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
    where = ["kind IN ('run', 'qc')", "id <= ?"]
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
    return report(classify(rows, upto), since=since, now=now, source="store")


# ── approval, apply, reinstate ───────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _one_bench(gateway, machine_uid: str, upto: int) -> Classification:
    rows, upto = read_store(gateway, machine_uid, upto_id=upto)
    return classify(rows, upto)


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
    if label not in HIDE_CANDIDATE_LABELS:
        raise DedupeRefused("{0!r} is not a label that hides anything"
                            .format(label))
    if decision not in ("approved", "rejected"):
        raise DedupeRefused("a decision is 'approved' or 'rejected'")
    if not str(approved_by or "").strip():
        raise DedupeRefused("an approval needs the name of the person "
                            "approving")
    upto, _sha = parse_run_id(run_id)
    result = _one_bench(gateway, machine_uid, upto)
    current = result.run_id(machine_uid, label)
    if current != run_id:
        raise DedupeRefused(
            "the candidates for {0} / {1} have changed since that report "
            "(it named {2}, the record now gives {3}): run a new dry run"
            .format(machine_uid, label, run_id, current))
    ids = result.ids(machine_uid, label)
    if not ids:
        raise DedupeRefused("there is nothing to approve for {0} / {1}"
                            .format(machine_uid, label))
    examples = _examples(result, machine_uid, (label,))
    with gateway.transaction():
        res = gateway.sql(
            "INSERT INTO annotation_approval (machine_uid, rule, run_id, "
            "candidates, examples, qc_impact, approved_by, approved_at, "
            "decision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [machine_uid, label, run_id, len(ids), json.dumps(examples),
             json.dumps(qc_impact(result, machine_uid, now, (label,))),
             approved_by.strip(),
             now or _now(), decision])
        if res.get("error"):
            raise DedupeRefused("the approval was not recorded: {0}"
                                .format(res["error"]))
        got = _read(gateway, "SELECT last_insert_rowid() AS i", [],
                    "the approval's id")
    return {"approval_id": got[0]["i"], "machine_uid": machine_uid,
            "label": label, "candidates": len(ids), "decision": decision}


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
    uid, label = appr["machine_uid"], appr["rule"]
    upto, _sha = parse_run_id(appr["run_id"])
    result = _one_bench(gateway, uid, upto)
    if result.run_id(uid, label) != appr["run_id"]:
        # Cannot happen on an append-only record; said loudly if it does.
        raise DedupeRefused("the record below id {0} no longer gives the "
                            "candidates approval {1} was given".format(
                                upto, approval_id))
    ids = result.ids(uid, label)
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
            "label": label, "annotated": len(todo),
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
                 "{0}:{1}".format(label, c.rule), appr["run_id"],
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
    a = ap.parse_args(argv)
    rows = read_mirror(a.mirror)
    rep = report(classify(rows), since=a.since, now=a.now,
                 source="mirror:" + a.mirror)
    names = json.load(open(a.names)) if a.names else {}
    print("\n".join(summary_lines(rep, names)))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=1)
    return 0


if __name__ == "__main__":                              # pragma: no cover
    raise SystemExit(main())
