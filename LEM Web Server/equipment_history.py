#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
equipment_history.py — one timeline per piece of equipment.

Ryan asked for historical corrective actions to be visible, and read them as one
thing rather than two: *what has happened to this instrument*. That question
interleaves two records this server has never kept.

**What a person did about a failure.** A machine went RED, or a QC check failed,
and somebody did something about it. Nothing recorded what they did, who they
were, or whether anyone went back and confirmed it worked. The floor could show
that an instrument was red on Tuesday and green on Wednesday and say nothing at
all about why.

**What a correction factor used to be.** `lem_correction_factors` is
overwrite-only — `_corrections()` in web_app.py upserts on
(machine_uid, test_name) — so changing PAC Flash 2's −3.0 to −2.5 destroys the
−3.0, and `updated_by` only ever names the last person to touch it. A correction
is added to **every** reading the bench reports (see CLAUDE.md, "It applies to
EVERY measurement"), so an unrecorded change is an unexplained step in every
result that follows it: ISO/IEC 17025 §7.8.2 and §8.4.

They belong together because the second is very often the first: the corrective
action taken about a biased instrument *is* the new factor. Shown in two panels,
that link is invisible.

## Three new tables, and nothing else touched

`lem_corrective_actions`, `lem_correction_audit` and `lem_action_events` are
new, and no existing `lem_*` table gains a column. That is deliberate and it is
what decides the release: RELEASING.md §2 makes a new or renamed `lem_*` column
MAJOR, because the station module on every bench reads these tables and would
have to move with it. New tables only means this ships MINOR and no bench
changes. `machine_uid` stays the key everywhere — LabCore has no foreign keys,
so renaming it would not error, it would silently orphan every row ever written.

Neither store declares its own schema. `HISTORY_DDL` is applied in exactly one
place — `snapshot_service.SCHEMA_DDL` imports the constant, so boot declares all
three tables once, after `existing_tables()` has been asked. Nothing goes into
`SCHEMA_MIGRATIONS`: that tuple is for columns added to a table that already
exists in the field, and all three of these are new. A column added to one of
them AFTER this shipped needs an ALTER there, because `CREATE TABLE IF NOT
EXISTS` is a no-op on a table that already exists.

Until 2026-08-25 nothing applied it at all, and the read rule below is written
for that state as much as for this one: a missing table means nothing has been
recorded yet, and it is still the only failure a read here may call empty.

A per-store `ensure_schema()` would put a bare CREATE TABLE on the write path,
and that pattern is how a column LabCore did not have took the whole batched
read down in production.

## What did LabCore actually tell me

One rule, `labcore_result`, shared with every other store in this app — this
module used to carry its own, and got it wrong in the same direction three other
modules did. `res.get("error")` is the SAME shape for a ConnectionError, an
eight-second read timeout and a queue refusal as it is for a table that does not
exist, so a store that answers it with `[]` reports a clean instrument during a
blip. Here that mattered more than usual: `Timeline.truncated` is a CLAIM that
the reader is looking at everything, and a degraded read presented as complete
is that claim made falsely.

So: a missing table degrades to empty (every `lem_*` table is created centrally
at boot, so a read before that has run is genuinely looking at nothing) and
**every other read raises `LabCoreUnavailable`**, including out of `timeline()`.
The one place even a missing table raises is a write path — `_require`, the
read a lifecycle write depends on — because the operator is about to be told
their corrective action was filed. Each call site says which it chose and why.
Writes are confirmed positively (`confirm_write`): absence of an "error" key is
not success.

## The merged record

Every source becomes a `HistoryEntry`: a small typed record, not a union of two
tables' columns. Adapters (`action_entries`, `action_event_entries`,
`correction_entries`, `log_entries`, `maintenance_entries`) map a source's rows
into it, so a source added later — a checklist tick, a changeover, a document
upload — is one more adapter and no change to the merge.

## Which failure an action answers

`lem_machine_log` has **no unique key, and no combination of its columns is
one**: no id, `ts` stamped once per poll and shared by every record in it,
`lab_id` naming the QC standard rather than the run of it, and `test_name` /
`value` repeating whenever a bench reads the same number twice. An id column
would settle it and is not available — that table is in the field on every
bench, so a new column there is MAJOR (RELEASING.md §2).

So the link is made **at open time**, from the event in front of the operator:
`trigger_ref` holds `log_event_ref(row)` — the same content fingerprint the
merge already uses as that entry's uid — and `action_entries` draws a link only
on an exact match, never a search. Anything else in `trigger_ref` (a Lab ID
somebody typed, a work order) is kept and shown as the evidence it is, and
draws no link.

What that replaces: `trigger_ref` used to be resolved by searching the window
for a QC standard's Lab ID, which repeats on every run of that standard. The
action was pinned to whichever run happened to be earliest in view, displayed
days out of place, and moved again every time the bench printed. A compliance
record that silently re-dates itself is worse than one carrying no link at all.
Two byte-identical log rows are still one identity — the same machine, second,
kind, test, standard, value and detail — and they are indistinguishable to
anyone reading that table, this module included.

## The lifecycle is the record

`LIFECYCLE` states the legal moves and `_require_transition` enforces every one
of them. Only `close` used to be guarded, so a finished action could be
reopened, re-closed with a different date, re-verified by somebody who was not
there, or have its recorded outcome rewritten — and the row would then say
whatever was written last rather than what happened. That is the one property a
compliance record has, so an illegal move is a refusal that names the state it
refused from (`ActionLifecycleError`), never a silent overwrite. `closed` and
`withdrawn` accept nothing at all: a problem that came back is a **new** action,
because a recurrence that overwrites its predecessor is a recurrence nobody can
count.

That check reads the row, decides in Python, and then writes — which two
operators walk straight through together. So the same rule is also **on the
UPDATE** (`_STATE_GUARDS`, `_change`): the statement only matches a row still
in a state the move is legal from, and the row count says whether it did. The
loser of a race is told, rather than silently overwriting a completed record
with their own name and date. `_require_transition` is kept because it produces
the message a person can act on; the WHERE clause is what makes it true.

And `lem_action_events` holds what is said ABOUT an action without changing it:
a reassignment (who took it off whom, and when the deadline moved) and a note —
the thing there was nowhere to put between "verified" and "closed". Append-only,
so it can never restate a date, a name or an outcome.

## Assignment, due dates and priority

`assigned_to`, `due_at` and `priority` are columns on `lem_corrective_actions`
from the start. They go in now because these two tables have not reached a field
LabCore yet, so adding them is free; once they ship, the same three columns are
a new `lem_*` column, a `SCHEMA_MIGRATIONS` ALTER and a MAJOR release
(RELEASING.md §2) — exactly what the new-tables-only shape was arranged to
avoid. The bar supports the shape: Limble's task record carries an assignee, a
priority and a due date on the task itself.

`priority` is a closed set (`PRIORITIES`) and anything else is refused; `due_at`
must parse; `assigned_to` is a plain LabCore username with nothing to validate
it against, and nothing here ever filters a row out by it. Every reassignment
writes a row in `lem_action_events` naming who made it and what it was before —
`assign` used to overwrite all three columns with no record and drop its own
`by` argument, which is the same defect as `lem_correction_factors` above, in
the module written to fix it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# The one query this module does not own. `MachineStateReader.events` already
# reads `lem_machine_log` for one machine, newest first, with a limit — and it
# is imported rather than copied because `qc_specs` is a plain gateway reader
# with no Flask on it, so nothing is dragged in by using it. That is the
# opposite trade from `_number` below, which IS duplicated: importing it would
# put Flask on the path of a compliance store. Two copies of a query drift, and
# this one decides what a compliance timeline contains.
from qc_specs import MachineStateReader

# The one rule for reading a gateway answer, shared with every other store here.
# Imported rather than re-derived: this module had its own `_written` / `_read`
# pair, which is how three modules in one week each invented a different wrong
# answer to the same question. See labcore_result.py.
from labcore_result import (LabCoreRefused, LabCoreUnavailable, confirm_write,
                            is_missing_table, rows, wrote_rows)

# ── schema ─────────────────────────────────────────────────────────────────
# NOT applied here — applied by `snapshot_service.SCHEMA_DDL`, which imports
# this constant rather than retyping it and asks `existing_tables()` first, so
# it costs nothing on a restart. Nothing goes into SCHEMA_MIGRATIONS: that
# tuple is for columns added to a table that already exists in the field, where
# `CREATE TABLE IF NOT EXISTS` is a no-op and only an ALTER helps. A column
# added to *these* tables later will need one.
#
# None of the three is an arm of the batched read, deliberately. Every arm
# shares ONE statement, and a timeline is opened by a person rather than polled
# by the floor; the fleet-wide badge is `open_actions()`, one read.
HISTORY_DDL = (
    # One row per corrective action, updated in place as it moves through its
    # life. The states are columns rather than a status word so the record says
    # *when* each thing happened and *who* did it — a single `status` column
    # would answer "verified" and lose the date and the name that make it a
    # record at all.
    "CREATE TABLE IF NOT EXISTS lem_corrective_actions ("
    "uid TEXT PRIMARY KEY, machine_uid TEXT NOT NULL, "
    "trigger_kind TEXT, trigger_ref TEXT, test_name TEXT, "
    "what_happened TEXT, "
    "opened_at TEXT, opened_by TEXT, "
    "action_taken TEXT, action_at TEXT, action_by TEXT, "
    "verified_at TEXT, verified_by TEXT, verification TEXT, "
    "closed_at TEXT, closed_by TEXT, closed_note TEXT, outcome TEXT, "
    "assigned_to TEXT, due_at TEXT, priority TEXT, "
    "updated_at TEXT)",
    # Append-only. Nothing in this module updates or deletes a row here: the
    # value of an audit trail is precisely that it cannot be tidied up.
    "CREATE TABLE IF NOT EXISTS lem_correction_audit ("
    "uid TEXT PRIMARY KEY, machine_uid TEXT NOT NULL, test_name TEXT NOT NULL, "
    "previous REAL, new_value REAL, units TEXT, "
    "changed_at TEXT, changed_by TEXT, reason TEXT)",
    # Everything said ABOUT an action that is not a state change: a
    # reassignment, and a note. Append-only, like the audit above and for the
    # same reason — a row here can never restate a date, a name or an outcome,
    # which is what makes it safe to allow at any point in an action's life,
    # including after it is finished, where a pointer to a recurrence belongs.
    #
    # `machine_uid` is carried as well as `action_uid` on purpose. LabCore has
    # no foreign keys, and a timeline reading this per action would be one read
    # per open action on a page that badges the whole floor — the N-reads
    # pattern the snapshot design exists to forbid. One read per instrument.
    # `by_user`, not `by`: BY is a SQL keyword.
    "CREATE TABLE IF NOT EXISTS lem_action_events ("
    "uid TEXT PRIMARY KEY, action_uid TEXT NOT NULL, machine_uid TEXT NOT NULL, "
    "kind TEXT, at TEXT, by_user TEXT, note TEXT, detail TEXT)",
)

# What a row in `lem_action_events` can be. Not a free-text kind: the timeline
# reads it to decide what the entry says, and "assigned"/"Assigned"/"reassign"
# for one idea is how a filter stops working.
ACTION_EVENT_KINDS = ("assigned", "note", "amended")

# `trigger` is a SQLite keyword (CREATE TRIGGER), hence `trigger_kind`. The
# values name where the action came from, because "opened by hand" and "opened
# because the bench failed a QC check" are different evidence.
TRIGGER_KINDS = ("qc_fail", "red_status", "maintenance", "audit", "other")


# Priority is a **closed set**, not free text. Free text gives you "urgent",
# "Urgent", "URGENT" and "high!!" for one idea, and then the open-actions list
# cannot be grouped, cannot be sorted by urgency, and cannot answer "show me the
# critical ones" — which is the only reason to record a priority at all. Four
# levels because a person picking from four does not deliberate; the rank is what
# orders the list, since a stored *word* cannot be ordered by SQL and putting the
# order in a query would leave two places to change when a level is added.
PRIORITIES = ("low", "normal", "high", "critical")
PRIORITY_RANK = {name: rank for rank, name in enumerate(PRIORITIES)}
DEFAULT_PRIORITY = "normal"

# The legal moves, in one place, so the lifecycle can be read rather than
# reconstructed from four scattered `if`s.
#
#   open ──▶ actioned ──▶ verified ──▶ closed
#     └──────────┴────────────┴──────▶ withdrawn
#
# * `actioned → actioned` is deliberate: the note is typed mid-job and finished
#   after, and a second row would read as a second action taken. It stops at
#   verification, because the verification attests to the action *as recorded*.
# * `verified → withdrawn` is allowed. Withdrawal is the only way to say "this
#   record should not exist", and a duplicate is very often spotted by the second
#   person going to verify it.
# * `closed` and `withdrawn` are terminal, full stop. See the module docstring.
LIFECYCLE = {
    "open":      frozenset({"actioned", "withdrawn"}),
    "actioned":  frozenset({"actioned", "verified", "withdrawn"}),
    "verified":  frozenset({"closed", "withdrawn"}),
    "closed":    frozenset(),
    "withdrawn": frozenset(),
}

# What the refusal calls the move it refused, so the message reads as a sentence.
_MOVE_NAMES = {"actioned": "given a new record of what was done",
               "verified": "verified", "closed": "closed",
               "withdrawn": "withdrawn"}

# Where a generic refusal would leave someone stuck, say what to do instead.
_TRANSITION_HINTS = {
    ("open", "closed"):
        "Record what was done and verify that it worked, or withdraw it.",
    ("actioned", "closed"):
        "Verify that it worked before closing it, or withdraw it.",
    ("open", "verified"):
        "Record what was done first — a verification with nothing recorded "
        "attests to nothing.",
}


class ActionLifecycleError(ValueError):
    """A move the corrective-action lifecycle does not allow.

    A `ValueError` on purpose: `close` already refused an unverified action with
    one and the routes above catch that, so narrowing the type must not widen a
    hole. It is a distinct class so a caller can tell "you cannot do that to a
    closed record" from "that is not a number".
    """


# A write LabCore did not accept. Kept as a NAME — the routes above and this
# module's tests read as sentences with it — but it is `LabCoreRefused` itself,
# not a second implementation of the same rule: two rules in one codebase is the
# bug labcore_result was extracted to end. LabCore's queue serialises at roughly
# 1.5 ops/sec and refuses past 100 pending **by returning**, in whatever shape
# it felt like, so "no error key" is not an acknowledgement and a corrective
# action reported as filed on the strength of one is a compliance record that
# does not exist.
HistoryWriteError = LabCoreRefused


# ── the merged record every source becomes ─────────────────────────────────

@dataclass(frozen=True)
class HistoryEntry:
    """One thing that happened to one instrument.

    `at` is the timestamp **exactly as it was recorded**. Ordering uses a parsed
    copy; the string itself is never rewritten, because a record that has been
    quietly adjusted no longer reconstructs the measurement (§7.5.1).

    `caused_by` is the uid of the entry this one answers — a verification
    answers its opening. It is what the merge uses when a clock disagrees.
    """

    at: str
    uid: str
    source: str          # corrective_action | correction_factor | log | maintenance
    kind: str            # opened | actioned | verified | closed | changed | qc | pm …
    machine_uid: str
    summary: str
    who: str = ""
    caused_by: str = ""
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"at": self.at, "uid": self.uid, "source": self.source,
                "kind": self.kind, "machine_uid": self.machine_uid,
                "summary": self.summary, "who": self.who,
                "caused_by": self.caused_by, "detail": dict(self.detail)}


# ── ordering ───────────────────────────────────────────────────────────────
#
# This is the hard part, and it is worth being explicit about what can and
# cannot be known.
#
# The entries come from different machines. `lem_machine_log.ts` is written by a
# station module from the bench PC's clock; a corrective action and a correction
# audit row are written by this server from the server's clock; a maintenance
# completion date is typed by a person. Nothing forces those clocks to agree, and
# nothing here can repair one that does not — the timestamp *is* the record.
#
# So the merge does three things, and refuses to do a fourth:
#
# 1. **It makes the order total.** The sort key is
#    `(parsed?, when, source rank, uid)`, and every uid is derived from the row
#    it describes — including the log's, which is why `log_entries` fingerprints
#    the value and detail rather than keying on (machine, ts, kind, test, lab_id)
#    alone: two prints in the same second used to collide on one uid, in a merge
#    whose whole claim is that nothing does. Two entries therefore compare equal
#    only when they are byte-identical, and those are one event described twice.
#    So the result does not depend on which source happened to be read first, on
#    dict iteration, or on the sort being stable. Two people looking at the same
#    instrument see the same history, and a refresh does not reshuffle it —
#    which matters more here than being right to the second, because a list that
#    rearranges itself is one nobody trusts.
#
# 2. **It breaks a tie as cause before response.** A bench logs a QC failure and
#    a supervisor opens an action about it within the same second — and the log
#    row's stamp has seconds' resolution, so this is common, not theoretical.
#    Read oldest-first the record of what the instrument did comes first, and the
#    human's response to it after: `_SOURCE_RANK`. An action cannot precede the
#    thing it answers. (Read newest-first the list is reversed, which puts the
#    response above its cause — correct for a newest-at-top reading.)
#
# 3. **It honours a link over a clock.** Where one entry *states* that it follows
#    another (`caused_by`), that is proof of order and the timestamps are not.
#    A bench an hour behind can stamp a verification before the opening it
#    verifies; `_causal_order` still places it after. Only linked entries are
#    moved — an unrelated neighbour keeps the place its own stamp earned, because
#    the timestamps are the best evidence available for everything else.
#
# What it will not do is guess an offset and shift a whole machine's entries to
# "correct" its clock. That would rewrite the record to make the display tidier,
# and it would be wrong in exactly the case it is meant for: a clock that is
# behind by an unknown amount. An entry whose stamp cannot be parsed at all is
# not dropped either — it sorts to the oldest end, where an unreadable date is
# visible rather than deleted.

_SOURCE_RANK = {
    "log": 0,               # what the instrument itself did
    "maintenance": 1,       # work carried out on it
    "correction_factor": 2, # a human changing how it is read
    "corrective_action": 3, # a human's response to all of the above
}
_UNRANKED = 9

_MIN = datetime.min

# `float()` refuses a Unicode minus and the dashes, which are indistinguishable
# from a hyphen on screen, and negative corrections are routine here (PAC Flash
# 2 runs at −3.0). Deliberately duplicated from `web_app.normalise_number_text`
# rather than imported: web_app will import this module, and a compliance store
# that cannot be used without Flask on the path is a worse trade than six lines.
# `tests/test_equipment_history.py` asserts the two never disagree.
_MINUS_LOOKALIKES = {"−": "-", "–": "-", "—": "-",
                     " ": " ", " ": " ", " ": " "}


def _number(value, what: str) -> float:
    text = str(value if value is not None else "")
    for bad, good in _MINUS_LOOKALIKES.items():
        text = text.replace(bad, good)
    try:
        return float(text.strip())
    except (TypeError, ValueError):
        raise ValueError(f"{value!r} is not a number ({what}).") from None


def parse_stamp(text) -> Optional[datetime]:
    """A timestamp from anywhere in the lab, or None if it cannot be read.

    Three shapes actually occur: the bench's `datetime.now().isoformat()` (with
    microseconds), the server's `isoformat(timespec="seconds")`, and a date
    alone from a typed maintenance completion. Compared as strings those sort
    wrongly — `09:00:00.500000` lands after `09:00:01` — so they are parsed.

    An offset-bearing stamp is converted to this server's local time rather than
    dropped: everything in the lab is written naive local, so a stamp carrying
    `Z` came from something else, and putting it on the same axis as the rest is
    the least wrong reading available.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    if len(raw) > 10 and raw[10] == " ":
        raw = raw[:10] + "T" + raw[11:]
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if when.tzinfo is not None:
        when = when.astimezone().replace(tzinfo=None)
    return when


def _sort_key(entry: HistoryEntry):
    """Oldest first, and total up to a byte-identical duplicate.

    Unreadable stamps sort to the oldest end (visible, not deleted); then the
    parsed instant; then cause before response within one second
    (`_SOURCE_RANK`); then the uid, which is derived from the row. Two entries
    can only tie when their uids tie, and a uid ties only when the rows are
    identical — see `log_entries`.
    """
    when = parse_stamp(entry.at)
    return (1 if when else 0, when or _MIN,
            _SOURCE_RANK.get(entry.source, _UNRANKED), entry.uid)


def _causal_order(ascending: List[HistoryEntry]) -> List[HistoryEntry]:
    """Hold stated chains together whatever the clocks say.

    An entry whose `caused_by` names an entry that is present but not yet
    emitted waits for it, and is released the moment its cause lands. A cause
    outside this timeline — cut off by a machine filter or a limit — is ignored
    rather than made to vanish, and a cycle (which no writer here can produce,
    but a future adapter could) flushes in timestamp order instead of hanging.
    """
    known = {e.uid for e in ascending}
    waiting: Dict[str, List[HistoryEntry]] = {}
    deferred: List[HistoryEntry] = []
    emitted: set = set()
    out: List[HistoryEntry] = []

    def emit(first: HistoryEntry) -> None:
        stack = [first]
        while stack:
            item = stack.pop()
            out.append(item)
            emitted.add(item.uid)
            for follower in reversed(waiting.pop(item.uid, [])):
                stack.append(follower)

    for entry in ascending:
        cause = entry.caused_by
        if cause and cause in known and cause not in emitted and cause != entry.uid:
            waiting.setdefault(cause, []).append(entry)
            deferred.append(entry)
            continue
        emit(entry)
    for entry in deferred:          # only a cycle can leave anything here
        if entry.uid not in emitted:
            out.append(entry)
            emitted.add(entry.uid)
    return out


def merge_timeline(*groups: Iterable[HistoryEntry], newest_first: bool = True,
                   limit: Optional[int] = None) -> List[HistoryEntry]:
    """Every source, in one order. See the block above for why that order.

    `limit` always keeps the **newest** entries, whichever direction they are
    read in: a truncated history that silently drops what happened this morning
    would be worse than no history.
    """
    entries: List[HistoryEntry] = []
    for group in groups:
        entries.extend(group or ())
    ordered = _causal_order(sorted(entries, key=_sort_key))
    if newest_first:
        ordered.reverse()
        return ordered[:limit] if limit else ordered
    if limit:
        return ordered[-limit:]
    return ordered


# ── corrective actions ─────────────────────────────────────────────────────

@dataclass
class CorrectiveAction:
    """What happened, what was done about it, and whether it worked."""

    uid: str
    machine_uid: str
    what_happened: str = ""
    trigger_kind: str = "other"
    trigger_ref: str = ""
    test_name: str = ""
    opened_at: str = ""
    opened_by: str = ""
    action_taken: str = ""
    action_at: str = ""
    action_by: str = ""
    verified_at: str = ""
    verified_by: str = ""
    verification: str = ""
    closed_at: str = ""
    closed_by: str = ""
    closed_note: str = ""
    outcome: str = ""
    # A LabCore username, the same shape as `updated_by` / `by_user` elsewhere.
    # LabCore has no foreign keys and there is no user directory table here, so
    # this cannot be validated against anything and is **a label, not a
    # reference**. Which is why nothing in this module ever filters a row out by
    # it: a rename or a leaver must never make an action disappear from a list —
    # an action nobody can see is one nobody does.
    assigned_to: str = ""
    due_at: str = ""
    priority: str = DEFAULT_PRIORITY
    updated_at: str = ""

    @property
    def priority_rank(self) -> int:
        """Unrecognised words sort as normal rather than to the bottom: a row
        written by something else must not fall off the end of an urgency
        list."""
        return PRIORITY_RANK.get(self.priority,
                                 PRIORITY_RANK[DEFAULT_PRIORITY])

    def due_datetime(self) -> Optional[datetime]:
        """The instant this is due, or None if it has no readable due date.

        **A date alone means the end of that day.** Somebody typing
        "2026-08-15" means "by the end of the 15th"; read as midnight it would
        be overdue for the whole day it is due, which turns the badge into
        noise on exactly the day someone should act on it.
        """
        when = parse_stamp(self.due_at)
        if when is None:
            return None
        if len(str(self.due_at).strip()) <= 10:
            return when.replace(hour=23, minute=59, second=59,
                                microsecond=999999)
        return when

    def is_overdue(self, now: Optional[datetime] = None) -> bool:
        """Past due and still owed.

        **Which clock.** `now` comes from the caller and in this server that is
        the server's clock; `due_at` is whatever was written into the row.
        Today that is always a person typing into this web UI — but nothing
        enforces it and nothing can: LabCore has no foreign keys, no writer
        column and no permission on a table, so anything holding the gateway
        can write this row. The earlier claim here ("a bench clock cannot make
        one overdue") was a guarantee dressed up as a fact.

        What is actually true is what the comparison does, and it is the least
        wrong reading available: a stamp carrying an offset is converted to
        this server's local time (`parse_stamp`) and a naive one is taken at
        face value, so both are compared on the one axis this process has. A
        due date written by a clock that disagrees is therefore read as the
        server would read it, not repaired and not refused.

        `now` is a parameter rather than a `datetime.now()` inside, because
        everything derived from `now` here is computed at request time
        (CLAUDE.md): a stored "overdue" is wrong a minute later.

        A stored date that will not parse is **not** overdue. Guessing would
        invent a red badge and send someone to an instrument that is fine.
        """
        if self.resolved:
            return False        # nothing is owed on a finished action
        due = self.due_datetime()
        if due is None:
            return False
        return (now or datetime.now()) > due

    @property
    def state(self) -> str:
        """Derived, never stored — a stored copy is a second answer that can
        disagree with the dates, and then nobody knows which one is true."""
        if self.outcome == "withdrawn":
            return "withdrawn"
        if self.closed_at:
            return "closed"
        if self.verified_at:
            return "verified"
        if self.action_taken:
            return "actioned"
        return "open"

    @property
    def resolved(self) -> bool:
        return bool(self.closed_at)

    def to_dict(self, now: Optional[datetime] = None) -> dict:
        out = {f: getattr(self, f) for f in _ACTION_FIELDS}
        out["state"] = self.state
        out["priority_rank"] = self.priority_rank
        # Derived, never stored — same rule as `state`, and the reason `now` is
        # an argument: a stored copy of "overdue" is stale the next minute.
        out["overdue"] = self.is_overdue(now)
        return out

    @classmethod
    def from_row(cls, row: dict) -> "CorrectiveAction":
        def text(key: str) -> str:
            return str(row.get(key) or "")
        return cls(uid=text("uid"), machine_uid=text("machine_uid"),
                   what_happened=text("what_happened"),
                   trigger_kind=text("trigger_kind") or "other",
                   trigger_ref=text("trigger_ref"), test_name=text("test_name"),
                   opened_at=text("opened_at"), opened_by=text("opened_by"),
                   action_taken=text("action_taken"), action_at=text("action_at"),
                   action_by=text("action_by"), verified_at=text("verified_at"),
                   verified_by=text("verified_by"),
                   verification=text("verification"),
                   closed_at=text("closed_at"), closed_by=text("closed_by"),
                   closed_note=text("closed_note"), outcome=text("outcome"),
                   assigned_to=text("assigned_to"), due_at=text("due_at"),
                   # Read-side default only: a row written before this column
                   # existed is a normal-priority action, because "no priority"
                   # is not a thing a list sorted by priority can show.
                   priority=text("priority") or DEFAULT_PRIORITY,
                   updated_at=text("updated_at"))


# Derived from the dataclass, not typed out beside it. The hand-written list
# came with a literal `19` placeholders next to it, so adding a column meant
# three edits in agreement or a silently shifted INSERT — every value one column
# to the left, which SQLite would accept without a word.
_ACTION_FIELDS = tuple(f.name for f in fields(CorrectiveAction))
_ACTION_COLUMNS = ", ".join(_ACTION_FIELDS)


def _priority(value) -> str:
    """One of `PRIORITIES`, or a refusal. Blank means the default."""
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return DEFAULT_PRIORITY
    if text not in PRIORITIES:
        raise ValueError(
            f"Priority must be one of {PRIORITIES!r}, got {value!r}.")
    return text


def _due(value) -> str:
    """A due date exactly as it was typed, or a refusal. Blank is allowed —
    plenty of corrective actions have no deadline, and a made-up one would
    put a red badge on the floor that nobody agreed to."""
    text = str(value or "").strip()
    if not text:
        return ""
    if parse_stamp(text) is None:
        raise ValueError(f"{value!r} is not a date that can be read (due).")
    return text


def _stamp(when=None) -> str:
    """A timestamp in the shape the rest of the server writes.

    A given string is **checked**, then handed back exactly as it came: checked
    because anything non-empty used to be accepted, so "soon" or a half-typed
    date entered the record and then sorted to the unreadable end of every
    timeline forever; handed back unchanged because the recorded stamp is the
    record (§7.5.1) and normalising it here would quietly restate it.
    """
    if isinstance(when, datetime):
        return when.isoformat(timespec="seconds")
    text = str(when or "").strip()
    if not text:
        return datetime.now().isoformat(timespec="seconds")
    if parse_stamp(text) is None:
        raise ValueError(f"{when!r} is not a date that can be read.")
    return text


def _answered(result, what: str, *, missing_ok: bool = True) -> List[dict]:
    """The rows of a read, or a refusal that names the read that failed.

    The judgement is `labcore_result.rows` and nothing else — this adds the
    sentence an operator sees, because "Read timed out" on its own does not say
    which half of a timeline is missing. `missing_ok` is a decision per call
    site, and every call site argues it.
    """
    try:
        return rows(result, missing_ok=missing_ok)
    except LabCoreUnavailable as exc:
        if is_missing_table(exc):
            # Only reachable with missing_ok=False, i.e. a write path. Say the
            # table is not there rather than letting a caller conclude the
            # record is not there: on the day this ships, before the DDL is
            # pasted into snapshot_service, that is the whole difference.
            raise LabCoreUnavailable(
                f"{what}: {exc} — the tables in HISTORY_DDL have not been "
                f"created (see the module docstring)") from None
        raise LabCoreUnavailable(f"{what}: {exc}") from None


def _confirmed(result, what: str) -> int:
    """Rows touched by a write LabCore positively acknowledged.

    `wrote_rows` is the rule; the wrapper only names the operation. Zero is a
    real answer (see `_change`), not a failure.
    """
    try:
        return wrote_rows(result)
    except LabCoreRefused as exc:
        # The ANSWER travels with the re-label, not just the sentence.
        # Re-raising the text alone drops `busy` and `retry_after`, so a
        # full queue reaches the browser as 502 "this will never work"
        # instead of 503 with a Retry-After — the one distinction a client
        # cannot recover by reading English.
        raise LabCoreRefused(f"{what}: {exc}",
                             getattr(exc, "result", None)) from None


def _window_end(text) -> Optional[datetime]:
    """The last instant a register window includes.

    **A date alone means the end of that day**, the same rule `due_datetime`
    applies to a due date and for the same reason: someone asking for a window
    ending "2026-08-26" means the whole of the 26th. Read as midnight it would
    exclude everything opened that day — so the register would quietly lose its
    most recent day, which is the day someone is most likely to be asking about.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    when = parse_stamp(raw)
    if when is None:
        return None
    if len(raw) <= 10:
        return when.replace(hour=23, minute=59, second=59, microsecond=999999)
    return when


def _register_order(action: CorrectiveAction):
    """Newest-opened first, with unreadable stamps sorted to the bottom.

    Sorted `reverse=True`, so `datetime.min` for an unreadable stamp puts it
    last rather than first — a row nobody can date must not displace the recent
    ones at the top of the page.
    """
    return parse_stamp(action.opened_at) or datetime.min


def _open_order(action: CorrectiveAction):
    """Most urgent first: priority, then the soonest deadline, then the oldest.

    Sorted in Python, not by SQL, because the priority stored is a *word* and
    `ORDER BY priority` would give alphabetical — critical, high, low, normal —
    which is not an order anyone means. The rank lives with the constant that
    names the set, so adding a level is one edit.

    An action with no due date sorts after dated ones of the same priority: it
    is not less important, but nothing is going to become late about it, so it
    should not push a dated one down the page.
    """
    due = action.due_datetime()
    return (-action.priority_rank, 0 if due else 1, due or datetime.max,
            action.opened_at, action.uid)


# ── the lifecycle, expressed where it can actually hold ────────────────────
#
# `LIFECYCLE` says which moves are legal; these say the same thing in SQL, so
# the UPDATE only matches a row that is STILL in a state the move is legal
# from. A read, a check in Python and an unconditional UPDATE is not a guard —
# two operators pass the check and the second one wins, silently, over a
# finished compliance record.
#
# They are written against the state columns rather than a stored status word
# for the same reason `state` is derived: a status column is a second answer
# that can disagree with the dates. `closed_at` covers both terminal states,
# because `withdraw` sets it too.
_NOT_FINISHED = "COALESCE(closed_at, '') = ''"
_NOT_VERIFIED = "COALESCE(verified_at, '') = ''"
_WAS_ACTIONED = "COALESCE(action_taken, '') <> ''"
_WAS_VERIFIED = "COALESCE(verified_at, '') <> ''"

_STATE_GUARDS = {
    # open | actioned  →  actioned
    "actioned":  f"{_NOT_FINISHED} AND {_NOT_VERIFIED}",
    # actioned  →  verified   (there has to be work to have checked)
    "verified":  f"{_NOT_FINISHED} AND {_NOT_VERIFIED} AND {_WAS_ACTIONED}",
    # verified  →  closed     (§8.7.1: was it effective?)
    "closed":    f"{_NOT_FINISHED} AND {_WAS_VERIFIED}",
    # anything not already finished  →  withdrawn
    "withdrawn": _NOT_FINISHED,
}

_ASSIGNMENT_LABELS = (("assigned_to", "Assigned to"), ("due_at", "Due"),
                      ("priority", "Priority"))


def _assignment_note(before: "CorrectiveAction", changes: dict) -> Tuple[str, dict]:
    """What a reassignment changed — as a sentence, and as from→to pairs.

    Both, because they are read by different things: the sentence is what the
    timeline shows a supervisor, and the pairs are what a machine can compare.
    "—" stands in for empty so "Assigned to — (was kaden)" reads as the
    unassignment it is rather than as a blank.
    """
    said, detail = [], {}
    for name, label in _ASSIGNMENT_LABELS:
        if name not in changes:
            continue
        was, now = getattr(before, name), changes[name]
        detail[name] = [was, now]
        said.append(f"{label} {now or '—'} (was {was or '—'})")
    return "; ".join(said), detail


class CorrectiveActionStore:
    """Owns `lem_corrective_actions` and `lem_action_events`.

    One write per operator action — two when the action needs auditing (see
    `assign`) — and never a loop: everything here is something a person just
    did and is waiting on, so there is nothing to batch and nothing that could
    flood a queue which refuses past 100 pending.

    **Reads.** A missing table means nothing has been recorded, because every
    `lem_*` table is created centrally at boot; every other failure raises
    `LabCoreUnavailable` rather than reading as an empty list. The one read that
    refuses to swallow even a missing table is `_require`, which is a write
    path. Each method says which it takes and why.
    """

    def __init__(self, gateway) -> None:
        self.gateway = gateway

    # ── writing ────────────────────────────────────────────────────
    def open_action(self, machine_uid: str, what_happened: str,
                    trigger_kind: str = "other", trigger_ref: str = "",
                    test_name: str = "", by: str = "", when=None,
                    uid: Optional[str] = None, assigned_to: str = "",
                    due_at: str = "", priority=None) -> CorrectiveAction:
        """File one.

        `trigger_ref` is **the identity of the event this answers**, captured
        here rather than searched for later: pass `log_event_ref(row)` (or,
        equivalently, the uid of the log entry the operator is looking at) for
        anything that came off a bench. Free text is still accepted and still
        recorded — it is evidence — but only an event identity is ever drawn as
        a link. See `log_event_ref` for why a Lab ID cannot be one.
        """
        machine_uid = str(machine_uid or "").strip()
        if not machine_uid:
            raise ValueError("A corrective action belongs to a piece of equipment.")
        what_happened = str(what_happened or "").strip()
        if not what_happened:
            # A row recording only that somebody clicked is not a record of
            # anything, and it is worse than nothing: it makes the history look
            # answered.
            raise ValueError("Say what happened.")
        trigger_kind = (str(trigger_kind or "other").strip().lower()
                        or "other")
        if trigger_kind not in TRIGGER_KINDS:
            raise ValueError(
                f"Trigger must be one of {TRIGGER_KINDS!r}, got "
                f"{trigger_kind!r}.")
        opened_at = _stamp(when)
        action = CorrectiveAction(
            uid=str(uid or uuid.uuid4().hex), machine_uid=machine_uid,
            what_happened=what_happened, trigger_kind=trigger_kind,
            trigger_ref=str(trigger_ref or "").strip(),
            test_name=str(test_name or "").strip(),
            opened_at=opened_at, opened_by=str(by or ""),
            assigned_to=str(assigned_to or "").strip(),
            due_at=_due(due_at), priority=_priority(priority),
            updated_at=opened_at)
        placeholders = ", ".join(["?"] * len(_ACTION_FIELDS))
        # The count is not checked: an INSERT of one row either happened or was
        # refused, and a LabCore that acknowledges without counting (the real
        # client hands back whatever the queue sent) must not read as a failure.
        _confirmed(self.gateway.sql(
            f"INSERT INTO lem_corrective_actions ({_ACTION_COLUMNS}) "
            f"VALUES ({placeholders})",
            [getattr(action, name) for name in _ACTION_FIELDS]),
            "opening a corrective action")
        return action

    def record_action(self, uid: str, action_taken: str, by: str = "",
                      when=None) -> CorrectiveAction:
        """What was actually done. Rewritable — the first note is often typed
        mid-job and finished later, and a second row would read as a second
        action taken.

        **Rewritable is not erasable.** This overwrote `action_taken`,
        `action_at` AND `action_by` with nothing kept, so kaden recording
        "Reconditioned the cell" at 10:00 and ryan recording "Replaced the cell"
        at 15:00 left a record saying only the second — kaden's account of what
        was done gone, and no row anywhere saying it had existed. That is the
        exact defect this module cites about `lem_correction_factors` as its
        reason to exist, committed inside it, in the one field an auditor
        actually reads.

        So an amendment keeps what it replaced, in `lem_action_events`, with the
        superseded text in the note so it can be READ rather than merely counted.
        The first recording overwrites nothing and records nothing, and retyping
        the same text is not an amendment — a re-posted form is not a second
        account of the work.
        """
        action = self._require(uid)
        self._require_transition(action, "actioned")
        text = str(action_taken or "").strip()
        if not text:
            raise ValueError("Say what was done.")
        at = _stamp(when)
        # Captured before the change, because after it there is nothing to keep.
        superseded = {
            "action_taken": {"from": action.action_taken, "to": text},
            "action_by": {"from": action.action_by, "to": str(by or "")},
            "action_at": {"from": action.action_at, "to": at},
        }
        amending = bool(action.action_taken) and any(
            str(pair["from"]) != str(pair["to"])
            for pair in superseded.values())
        self._change(uid, {"action_taken": text, "action_at": at,
                           "action_by": str(by or ""), "updated_at": at},
                     "recording a corrective action", to="actioned")
        action.action_taken, action.action_at = text, at
        action.action_by = str(by or "")
        if amending:
            # Change first, then the row that says what it replaced — an audit
            # written first would describe an overwrite that could still be
            # refused. Same order, and same reason, as `assign`.
            self._record_event(
                action, "amended",
                note=("Amended what was done. Previously: "
                      f"{superseded['action_taken']['from']!r}"
                      + (f" ({superseded['action_by']['from']}"
                         f", {superseded['action_at']['from']})"
                         if superseded["action_by"]["from"]
                         or superseded["action_at"]["from"] else "")),
                by=by, at=at, detail=superseded)
        return action

    def verify(self, uid: str, by: str = "", note: str = "",
               when=None) -> CorrectiveAction:
        """Somebody went back and checked it worked (§8.7.1).

        Legal only from `actioned`, and only once. With nothing recorded there
        is no work whose effectiveness could have been checked, and a second
        verification would overwrite the name and date of the person who
        actually did the checking.
        """
        action = self._require(uid)
        self._require_transition(action, "verified")
        at = _stamp(when)
        self._change(uid, {"verified_at": at, "verified_by": str(by or ""),
                           "verification": str(note or ""), "updated_at": at},
                     "verifying a corrective action", to="verified")
        action.verified_at, action.verified_by = at, str(by or "")
        action.verification = str(note or "")
        return action

    def close(self, uid: str, by: str = "", note: str = "",
              when=None) -> CorrectiveAction:
        """Close a verified action.

        An unverified one is refused. "Was it effective?" is the question the
        record exists to answer, and a close that skips it is the box-tick that
        makes the whole trail worthless. An action opened by mistake is not a
        failure of verification — it has `withdraw`. An action already finished
        is refused too: a second close would restate the date, the name and the
        outcome of the first.
        """
        action = self._require(uid)
        self._require_transition(action, "closed")
        at = _stamp(when)
        self._change(uid, {"closed_at": at, "closed_by": str(by or ""),
                           "closed_note": str(note or ""), "outcome": "closed",
                           "updated_at": at}, "closing a corrective action",
                     to="closed")
        action.closed_at, action.closed_by = at, str(by or "")
        action.closed_note, action.outcome = str(note or ""), "closed"
        return action

    def withdraw(self, uid: str, by: str = "", reason: str = "",
                 when=None) -> CorrectiveAction:
        """Opened by mistake — on the wrong instrument, or twice.

        Not a delete: the row stays and says it was withdrawn, by whom and why.
        And it never fills in `verified_at`, so nothing can later read as though
        someone checked a fix that was never made.

        Legal from every state that is not already finished — including
        `verified`, because a duplicate is very often spotted by the second
        person going to verify it.
        """
        action = self._require(uid)
        self._require_transition(action, "withdrawn")
        at = _stamp(when)
        self._change(uid, {"closed_at": at, "closed_by": str(by or ""),
                           "closed_note": str(reason or ""),
                           "outcome": "withdrawn", "updated_at": at},
                     "withdrawing a corrective action", to="withdrawn")
        action.closed_at, action.closed_by = at, str(by or "")
        action.closed_note, action.outcome = str(reason or ""), "withdrawn"
        return action

    def assign(self, uid: str, assigned_to=None, due_at=None, priority=None,
               by: str = "", when=None) -> CorrectiveAction:
        """Who owns it, by when, and how urgent — set or changed, **and
        recorded**.

        This used to be an overwrite with no audit and no timeline entry, which
        discarded its own `by`: exactly the defect the module docstring cites
        about `lem_correction_factors` as its reason to exist, committed inside
        it. Who took an action off whom, and who moved a deadline, is not
        decoration — it is the answer to "why did nobody do this".

        So it is two ops: the change, then the row that says who made it. In
        that order, because an audit row written first would describe a change
        that could still be refused, and a record of something that did not
        happen is worse than a late one. If the audit is refused the caller is
        TOLD (`HistoryWriteError`) rather than quietly left with an unaudited
        reassignment.

        Not a state change, so it is not in `LIFECYCLE`; but a finished action
        is finished, and re-dating a closed record is the same overwrite by
        another door. Only what is passed is written, and only what actually
        differs is written: `assign(uid, due_at=…)` must not blank the assignee,
        and re-picking the name that is already there is not a reassignment.

        This is also the answer to a departed user. `assigned_to` cannot be
        validated (no user directory, no foreign keys), so the repair for a
        leaver is reassignment — never a filter that hides their open actions.
        """
        action = self._require(uid)
        self._refuse_reassignment(action)
        changes: dict = {}
        # Validated BEFORE anything is written: a priority the urgency list
        # cannot sort by, or a date nothing can read, must not reach the row.
        if assigned_to is not None:
            changes["assigned_to"] = str(assigned_to or "").strip()
        if due_at is not None:
            changes["due_at"] = _due(due_at)
        if priority is not None:
            changes["priority"] = _priority(priority)
        changes = {name: value for name, value in changes.items()
                   if str(getattr(action, name)) != str(value)}
        if not changes:
            return action
        at = _stamp(when)
        note, detail = _assignment_note(action, changes)
        self._change(uid, dict(changes, updated_at=at),
                     "assigning a corrective action", guard=_NOT_FINISHED)
        for name, value in changes.items():
            setattr(action, name, value)
        action.updated_at = at
        try:
            self._record_event(action, "assigned", note=note, by=by, at=at,
                               detail=detail)
        except HistoryWriteError as refused:
            # TWO TABLES, NO TRANSACTION, AND THE CHANGE HAS ALREADY LANDED.
            #
            # Raising is still right — the caller must not be told a reassignment
            # was fully recorded when its audit row was refused. But the bare
            # error reads as "nothing happened", and it is not: a retry
            # finds the values already current, computes no changes, returns at
            # the early exit above and reports success while writing nothing. The
            # reassignment is then permanently unaudited and nothing anywhere
            # says so.
            #
            # So the message names the split state. It is NOT self-healed here: a
            # retry cannot tell "audit refused a moment ago" from "assigned last
            # week and nobody has touched it since" without a pending-write
            # protocol, which is three queue ops at ~1.5/s for a case a person can
            # resolve by looking. `events()` is the check, and it is one read.
            raise HistoryWriteError(
                f"The reassignment of {action.uid!r} was saved, but the row "
                f"recording who made it was refused ({refused}). The change is "
                f"live and UNAUDITED; retrying will report no change because the "
                f"values are already set. Add a note saying who reassigned it, "
                f"or check events() once LabCore is answering."
            ) from refused
        return action

    def add_note(self, uid: str, note: str, by: str = "", when=None) -> dict:
        """Something said about an action that is not a change to it.

        There was nowhere to put this: an action verified on Tuesday and closed
        on Friday had three days with no room for "waiting on the second QC
        run" or "supplier is sending a new cell". A follow-up typed into
        `action_taken` would have restated what was done, after somebody
        verified it.

        Allowed in **every** state, finished included. The row is append-only
        and separate, so nothing it holds can rewrite a date, a name or an
        outcome — and a closed action carrying "this recurred, see CA-9" is a
        cross-reference an auditor wants, not the reopening the lifecycle
        refuses. Recurrence is still a NEW action.
        """
        action = self._require(uid)
        text = str(note or "").strip()
        if not text:
            # An empty note is a misclick. A blank row in an audit trail reads
            # as "somebody had nothing to say", which is not a thing anyone
            # ever means to record.
            raise ValueError("Say something, or do not add a note.")
        return self._record_event(action, "note", note=text, by=by,
                                  at=_stamp(when))

    def _record_event(self, action: CorrectiveAction, kind: str, note: str,
                      by: str, at: str, detail: Optional[dict] = None) -> dict:
        if kind not in ACTION_EVENT_KINDS:
            raise ValueError(
                f"An action event is one of {ACTION_EVENT_KINDS!r}, got "
                f"{kind!r}.")
        row = {"uid": uuid.uuid4().hex, "action_uid": action.uid,
               "machine_uid": action.machine_uid, "kind": kind, "at": at,
               "by_user": str(by or ""), "note": note,
               "detail": json.dumps(detail or {}, sort_keys=True)}
        _confirmed(self.gateway.sql(
            "INSERT INTO lem_action_events (uid, action_uid, machine_uid, "
            "kind, at, by_user, note, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            list(row.values())),
            f"recording a {kind} event on a corrective action")
        return dict(row, detail=dict(detail or {}))

    def _refuse_reassignment(self, action: CorrectiveAction) -> None:
        if LIFECYCLE.get(action.state):
            return
        raise ActionLifecycleError(
            f"Corrective action {action.uid!r} is {action.state} and says what "
            f"actually happened; it cannot be reassigned or re-dated. "
            f"Open a new action if there is more to do.")

    def _require_transition(self, action: CorrectiveAction, to: str) -> None:
        """Refuse an illegal move, and say what was refused and why.

        The bug this closes: every call except `close` overwrote whatever was
        there. A closed action could be reopened, re-closed with a different
        date and outcome, or re-verified by somebody who was not there — so the
        row said whatever was written last, which is the one thing a compliance
        record must never do.

        This is the check that produces the good message. It is **not** the
        check that holds: see `_change`, where the same rule rides on the
        UPDATE itself.
        """
        allowed = LIFECYCLE.get(action.state, frozenset())
        if to in allowed:
            return
        move = _MOVE_NAMES.get(to, to)
        if not allowed:
            raise ActionLifecycleError(
                f"Corrective action {action.uid!r} is {action.state} and says "
                f"what actually happened; it cannot be {move}. Open a new "
                f"action if the problem came back.")
        hint = _TRANSITION_HINTS.get((action.state, to), "")
        raise ActionLifecycleError(
            f"Corrective action {action.uid!r} is {action.state}; it cannot be "
            f"{move} from there." + (f" {hint}" if hint else ""))

    def _require(self, uid: str) -> CorrectiveAction:
        """The action a write is about to change.

        `missing_ok=False`: this is a write path, and on a LabCore where these
        tables do not exist yet the operator must be told THAT, not "no such
        action" — which is a sentence they will act on by filing it again.
        """
        action = self._fetch(uid, missing_ok=False)
        if action is None:
            # Not a silent no-op: the caller believes it just recorded
            # something, and an operator who is told "saved" about a uid that
            # does not exist will never look again.
            raise KeyError(f"No corrective action {uid!r}.")
        return action

    def _change(self, uid: str, changes: dict, what: str,
                to: Optional[str] = None, guard: Optional[str] = None) -> None:
        """Apply a change **only if the record is still where it was read**.

        The guard used to be a read, a check in Python, and then an
        unconditional `UPDATE … WHERE uid = ?`. Two operators finishing the same
        action both passed the check, and the second overwrote a completed
        compliance record with a different date, a different name and a
        different outcome — the exact overwrite `LIFECYCLE` exists to refuse,
        reachable by two people clicking at once. So the precondition rides on
        the UPDATE, where the database decides it, and the row count says
        whether it matched.

        A count of 0 is ambiguous in one direction only: LabCore's real client
        hands back `resp.json()` verbatim, and a host that does not report
        `rows_affected` would make every update look lost. So a miss is
        CONFIRMED against the record before it is called one. (If the other
        writer wrote byte-identical values, this reads as success — and the row
        does say exactly what was meant, by somebody with the same name and
        stamp.)

        `to` is a lifecycle move and brings its own guard; `guard` is passed
        directly for the one change that is not a move — an assignment, which
        has no target state and is refused only on a finished record.
        """
        guard = guard or _STATE_GUARDS[to]
        sets = ", ".join(f"{name} = ?" for name in changes)
        matched = _confirmed(self.gateway.sql(
            f"UPDATE lem_corrective_actions SET {sets} "
            f"WHERE uid = ? AND {guard}",
            list(changes.values()) + [uid]), what)
        if matched == 1:
            return
        if self._holds(uid, changes):
            return
        self._lost(uid, to)

    def _holds(self, uid: str, changes: dict) -> bool:
        """Does the record now say what this write meant to say?"""
        current = self._fetch(uid, missing_ok=False)
        if current is None:
            return False
        return all(str(getattr(current, name, "")) == str(value)
                   for name, value in changes.items())

    def _lost(self, uid: str, to: Optional[str]) -> None:
        """Somebody else moved it between the read and the write.

        Refuse with what the record says NOW. Reporting success here is the
        failure: the operator would be told their close, their outcome and
        their name were recorded, about a row carrying somebody else's.
        """
        current = self._fetch(uid, missing_ok=False)
        if current is None:
            raise KeyError(
                f"No corrective action {uid!r} — it was removed while this was "
                f"being recorded.")
        if to is not None:
            self._require_transition(current, to)
        else:
            self._refuse_reassignment(current)
        raise ActionLifecycleError(
            f"Corrective action {uid!r} changed while you were working on it; "
            f"it is {current.state} now and nothing was recorded. Read it "
            f"again and repeat what you meant to do.")

    # ── reading ────────────────────────────────────────────────────
    def get(self, uid: str) -> Optional[CorrectiveAction]:
        """One action, or None because there is no such action.

        `missing_ok=True`: a table that was never created holds no actions, so
        None is true. Every other failure raises — this used to answer a read
        timeout with None, and `_require` then told the operator "No corrective
        action 'CA-1'" about a record sitting in LabCore.
        """
        return self._fetch(uid, missing_ok=True)

    def _fetch(self, uid: str, *, missing_ok: bool) -> Optional[CorrectiveAction]:
        found = _answered(self.gateway.read_sql(
            f"SELECT {_ACTION_COLUMNS} FROM lem_corrective_actions "
            "WHERE uid = ?", [uid]),
            f"reading corrective action {uid!r} (lem_corrective_actions)",
            missing_ok=missing_ok)
        return CorrectiveAction.from_row(found[0]) if found else None

    def for_machine(self, machine_uid: str) -> List[CorrectiveAction]:
        return self._list(
            f"SELECT {_ACTION_COLUMNS} FROM lem_corrective_actions "
            "WHERE machine_uid = ? ORDER BY opened_at, uid", [machine_uid])

    def events(self, action_uid: str) -> List[dict]:
        """Everything said about one action — reassignments and notes."""
        return self._events(
            "SELECT uid, action_uid, machine_uid, kind, at, by_user, note, "
            "detail FROM lem_action_events WHERE action_uid = ? "
            "ORDER BY at, uid", [action_uid])

    def events_for_machine(self, machine_uid: str) -> List[dict]:
        """The same, for every action on one instrument, in ONE read."""
        return self._events(
            "SELECT uid, action_uid, machine_uid, kind, at, by_user, note, "
            "detail FROM lem_action_events WHERE machine_uid = ? "
            "ORDER BY at, uid", [machine_uid])

    def _events(self, sql: str, args: list) -> List[dict]:
        # `missing_ok=True`: same rule as every other read here — the table not
        # existing yet means nothing has been said, and anything else raises.
        out = []
        for raw in _answered(self.gateway.read_sql(sql, args),
                             "reading lem_action_events"):
            row = dict(raw)
            row["detail"] = _detail_dict(row.get("detail"))
            out.append(row)
        return out

    # ── what a UI asks for ─────────────────────────────────────────
    #
    # One read each, always. The equipment card badges every instrument on one
    # page, so "open actions for this machine" run per card would be N reads
    # for N instruments — precisely the pattern the snapshot design exists to
    # forbid. `open_by_machine()` answers the whole fleet in a single read.
    #
    # None of these filters on `assigned_to`. That is the rule, not an
    # omission: it cannot be validated against anything, and a filter on it
    # means a rename or a leaver silently empties a list, which is how an open
    # corrective action stops being anybody's job. Filter by person in the UI,
    # over a list that still contains everyone.

    def open_actions(self, machine_uid: Optional[str] = None
                     ) -> List[CorrectiveAction]:
        """Everything still hanging open — the Monday question, per instrument
        or across the floor. Withdrawn actions carry `closed_at` too, so they
        drop out without needing to be filtered by outcome.

        Ordered most-urgent-first (`_open_order`), because the only reason to
        read this list is to know what to do next.
        """
        sql = (f"SELECT {_ACTION_COLUMNS} FROM lem_corrective_actions "
               "WHERE (closed_at IS NULL OR closed_at = '')")
        args: list = []
        if machine_uid:
            sql += " AND machine_uid = ?"
            args.append(machine_uid)
        return sorted(self._list(sql, args), key=_open_order)

    def unresolved(self) -> List[CorrectiveAction]:
        """The whole floor's open actions. Kept as a name because that is what
        a supervisor calls them."""
        return self.open_actions()

    def register(self, start: str = "", end: str = "",
                 machine_uid: Optional[str] = None) -> List[CorrectiveAction]:
        """Every corrective action in a window — **open and resolved**.

        Every other fleet-wide answer this store gives is about what is still
        open: `open_actions`, `open_by_machine`, `overdue`. That is the Monday
        supervisor question — what do I do next. An assessment asks the opposite
        one: *show me everything that happened in the last twelve months and
        what you did about it.* At an assessment the CLOSED actions are the
        interesting ones, because closing them is the evidence that the system
        works, and until now there was no way to list one across the fleet.

        Filtered on `opened_at`, which is when the fault was found. Filtering on
        `closed_at` instead would drop everything still open out of a window
        that plainly contains it.

        **An action whose `opened_at` cannot be read is always included**,
        whatever window is asked for. That is deliberate and it is the safer of
        the two wrong answers: a register is a compliance record, and one that
        silently loses rows under-reports the lab to an assessor, which is worse
        than showing a row with a blank date that a person can see and chase.
        """
        sql = f"SELECT {_ACTION_COLUMNS} FROM lem_corrective_actions"
        args: list = []
        if machine_uid:
            sql += " WHERE machine_uid = ?"
            args.append(machine_uid)

        lo = parse_stamp(start) if str(start or "").strip() else None
        hi = _window_end(end)

        kept = []
        for action in self._list(sql, args):
            opened = parse_stamp(action.opened_at)
            if opened is None:
                kept.append(action)
                continue
            if lo is not None and opened < lo:
                continue
            if hi is not None and opened > hi:
                continue
            kept.append(action)

        # Newest first — a register is read from the top. An unreadable stamp
        # sorts to the bottom rather than to the top, where it would displace
        # the recent rows the reader came for.
        return sorted(kept, key=_register_order, reverse=True)

    def recurrences(self, start: str = "", end: str = ""
                    ) -> Dict[Tuple[str, str], List[CorrectiveAction]]:
        """Faults that came back, keyed by `(machine_uid, test_name)`.

        "Has this happened before on this instrument, for this test?" is the
        question that separates a lab which closes tickets from one with a
        working corrective-action system, and across a year of rows nobody can
        answer it by eye.

        Two grouping rules, both of which exist to avoid inventing a recurrence
        the record does not show:

        * **The instrument is part of the key.** Two benches failing the same
          method is a method problem, not one instrument repeating itself.
        * **A blank `test_name` never groups.** It is missing information, not a
          shared key — otherwise every general fault on one bench ("odd noise",
          "loose door") collapses into a fictitious repeat.

        Only groups of two or more are returned; a single occurrence is not a
        recurrence and a caller should not have to filter that out itself.
        """
        grouped: Dict[Tuple[str, str], List[CorrectiveAction]] = {}
        for action in self.register(start=start, end=end):
            test_name = str(action.test_name or "").strip()
            if not test_name:
                continue
            grouped.setdefault((action.machine_uid, test_name), []).append(action)
        return {key: items for key, items in grouped.items() if len(items) > 1}

    def open_by_machine(self) -> Dict[str, List[CorrectiveAction]]:
        """Every instrument's open actions, in ONE read. Machines with none are
        absent rather than empty, so a caller can badge on presence."""
        grouped: Dict[str, List[CorrectiveAction]] = {}
        for action in self.open_actions():
            grouped.setdefault(action.machine_uid, []).append(action)
        return grouped

    def overdue(self, now: Optional[datetime] = None,
                machine_uid: Optional[str] = None) -> List[CorrectiveAction]:
        """Open actions past their due date at `now` — one read.

        Filtered here rather than in SQL: the comparison is "end of that day"
        for a date alone (see `due_datetime`), which a string comparison in
        SQLite cannot express, and a wrong answer here is a red badge on an
        instrument that is fine.
        """
        return [a for a in self.open_actions(machine_uid) if a.is_overdue(now)]

    def _list(self, sql: str, args: Optional[list] = None
              ) -> List[CorrectiveAction]:
        # `missing_ok=True`, and ONLY that: on the day this ships the table has
        # not been created and an empty list is the truth. A timeout is not, and
        # a supervisor reading "nothing is open" off one is the failure this
        # whole rule exists to prevent — nobody re-checks a list that says the
        # floor is clean.
        return [CorrectiveAction.from_row(row) for row in
                _answered(self.gateway.read_sql(sql, args or []),
                          "reading lem_corrective_actions")]


# ── the audit trail lem_correction_factors never kept ──────────────────────

class CorrectionAuditStore:
    """Owns `lem_correction_audit`. Append-only, by design and by API: there is
    no update and no delete on this class."""

    def __init__(self, gateway) -> None:
        self.gateway = gateway

    def record(self, machine_uid: str, test_name: str, previous, new_value,
               by: str = "", reason: str = "", units: str = "",
               when=None, uid: Optional[str] = None) -> dict:
        """One factor change: from, to, who, when, why.

        `previous` and `new_value` are refused rather than coerced, for the same
        reason `api_save_correction` refuses them: this number is added to every
        reading the bench produces, and "a bit" quietly becoming 0.0 would put a
        confident claim in the record that no correction was in force.

        Removing a factor is a change to 0.0, not an absence — the readings after
        it really are corrected by nothing, and a hole in the trail cannot say
        when that started.
        """
        machine_uid = str(machine_uid or "").strip()
        if not machine_uid:
            raise ValueError("A correction belongs to a piece of equipment.")
        test_name = str(test_name or "").strip()
        if not test_name:
            raise ValueError("Which test?")
        row = {
            "uid": str(uid or uuid.uuid4().hex),
            "machine_uid": machine_uid,
            "test_name": test_name,
            "previous": _number(previous, "previous correction"),
            "new_value": _number(new_value, "new correction"),
            "units": str(units or ""),
            "changed_at": _stamp(when),
            "changed_by": str(by or ""),
            "reason": str(reason or ""),
        }
        _confirmed(self.gateway.sql(
            "INSERT INTO lem_correction_audit (uid, machine_uid, test_name, "
            "previous, new_value, units, changed_at, changed_by, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", list(row.values())),
            "recording a correction change")
        # One shape for one row: `history()` flags what it could not read, and a
        # caller that reads both must not meet a dict missing the key.
        return dict(row, unreadable=[])

    def history(self, machine_uid: str,
                test_name: Optional[str] = None) -> List[dict]:
        sql = ("SELECT uid, machine_uid, test_name, previous, new_value, units, "
               "changed_at, changed_by, reason FROM lem_correction_audit "
               "WHERE machine_uid = ?")
        args: list = [machine_uid]
        if test_name:
            sql += " AND test_name = ?"
            args.append(test_name)
        # `missing_ok=True`: before the DDL is applied there is no trail, and
        # that is the truth. Anything else raises — a factor-change history
        # that comes back empty during a blip says a correction was never
        # changed, which is the §7.8.2 gap this table exists to close.
        out = []
        for raw in _answered(
                self.gateway.read_sql(sql + " ORDER BY changed_at, uid", args),
                "reading lem_correction_audit"):
            row = dict(raw)
            unreadable = []
            for key in ("previous", "new_value"):
                try:
                    row[key] = _number(row.get(key), key)
                except ValueError:
                    # `record()` refuses "a bit" because coercing it writes a
                    # confident claim that no correction was in force — and
                    # this did that exact coercion on the way back out, so a
                    # value nothing here could have written came back as a
                    # believable 0.0. Nothing else writes this table, so a row
                    # like that is damage: it is handed back exactly as stored
                    # and flagged, where a reader can see it.
                    unreadable.append(key)
            row["unreadable"] = unreadable
            out.append(row)
        return out


# ── adapters: source rows in, HistoryEntry out ─────────────────────────────

def _detail_dict(raw) -> dict:
    """`lem_machine_log.detail` is a JSON blob written by the modules. Anything
    that is not an object is kept as text rather than discarded — an unreadable
    detail is still evidence."""
    if isinstance(raw, dict):
        return dict(raw)
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return {"text": text}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _number_text(value) -> str:
    text = str(value if value is not None else "")
    return text.rstrip("0").rstrip(".") if "." in text else text


def log_event_ref(row: dict) -> str:
    """The identity of ONE event in `lem_machine_log`.

    **There is no unique key in that table, and there is no column combination
    that is one.** Read what the station module writes (`build_log_insert`):
    seven columns, no id, `ts` stamped once per POLL and shared by every record
    it produces, `lab_id` naming the QC STANDARD — which is run daily, so it
    repeats forever — `test_name` and `value` repeating whenever a bench reads
    the same number twice, and `detail` carrying the band, not an identity.
    Adding an id column would fix that and it is not available: `lem_machine_log`
    is in the field on every bench, so a new column there is a MAJOR release
    (RELEASING.md §2) and a change every station module has to move with.

    This is therefore a **content fingerprint**, and it is what the merge
    already keys log entries on. It separates everything the table can
    distinguish; two rows it cannot separate are byte-identical — the same
    machine, second, kind, test, standard, value and detail — and those are
    indistinguishable to an auditor reading the table too. They are treated as
    one event, which is the only honest reading available.

    What it replaces is the real defect: `trigger_ref` was resolved by
    SEARCHING the window for a QC standard's Lab ID. That pinned an action to
    whichever run of that standard happened to be earliest in view, put it days
    out of place, and moved it again every time the bench printed. A compliance
    record that silently re-dates itself is worse than one carrying no link at
    all — so the link is made at open time, from the event in front of the
    operator, and never resolved by search afterwards.
    """
    detail = _detail_dict(row.get("detail"))
    kind = str(row.get("kind") or "").strip() or "event"
    value = str(row.get("value") or "")
    seal = hashlib.sha1(
        json.dumps([value, detail], sort_keys=True,
                   default=str).encode("utf-8")).hexdigest()[:8]
    return ("log:{machine}:{ts}:{kind}:{test}:{lab}:{seal}".format(
        machine=str(row.get("machine_uid") or ""), ts=str(row.get("ts") or ""),
        kind=kind, test=str(row.get("test_name") or ""),
        lab=str(row.get("lab_id") or ""), seal=seal))


def action_entries(actions: Sequence[CorrectiveAction],
                   log_uids: Optional[Iterable[str]] = None
                   ) -> List[HistoryEntry]:
    """One entry per thing that actually happened, not one per action.

    An action opened on Monday and verified on Friday is two events four days
    apart; collapsing it to one dated row would put Monday's opening next to
    Friday's neighbours, or Friday's verification next to Monday's. The later
    entries carry `caused_by` so the merge can hold them in order when a clock
    disagrees.
    """
    known = set(log_uids or ())
    out: List[HistoryEntry] = []
    for action in actions or ():
        if action is None:
            continue
        base = {"machine_uid": action.machine_uid, "source": "corrective_action"}
        label = action.test_name or action.trigger_ref
        # An EXACT match against an event identity, never a search. A ref that
        # is anything else — a Lab ID somebody typed, a work order, a sentence
        # — is still carried in the detail as the evidence it is, and draws no
        # link, because a link that is right most of the time is exactly what
        # an auditor reads as a fact. A ref naming an event that has aged out
        # of this window is left alone too: `_causal_order` ignores a cause it
        # cannot see rather than making the entry vanish.
        cause = (action.trigger_ref
                 if action.trigger_ref and action.trigger_ref in known
                 else "")
        out.append(HistoryEntry(
            at=action.opened_at, uid=action.uid, kind="opened",
            caused_by=cause,
            summary=("Corrective action opened"
                     + (f" — {label}" if label else "")),
            who=action.opened_by,
            detail={"what_happened": action.what_happened,
                    "trigger_kind": action.trigger_kind,
                    "trigger_ref": action.trigger_ref,
                    "test_name": action.test_name,
                    "assigned_to": action.assigned_to,
                    "due_at": action.due_at,
                    "priority": action.priority,
                    "state": action.state}, **base))
        if action.action_taken:
            out.append(HistoryEntry(
                at=action.action_at or action.opened_at,
                uid=f"{action.uid}:actioned", kind="actioned",
                summary=f"Action taken — {action.action_taken}",
                who=action.action_by, caused_by=action.uid,
                detail={"action_taken": action.action_taken}, **base))
        if action.verified_at:
            out.append(HistoryEntry(
                at=action.verified_at, uid=f"{action.uid}:verified",
                kind="verified",
                summary=("Verified effective"
                         + (f" — {action.verification}"
                            if action.verification else "")),
                who=action.verified_by, caused_by=action.uid,
                detail={"verification": action.verification}, **base))
        if action.closed_at:
            withdrawn = action.outcome == "withdrawn"
            out.append(HistoryEntry(
                at=action.closed_at, uid=f"{action.uid}:closed",
                kind="withdrawn" if withdrawn else "closed",
                summary=("Withdrawn" if withdrawn else "Closed")
                        + (f" — {action.closed_note}"
                           if action.closed_note else ""),
                who=action.closed_by, caused_by=action.uid,
                detail={"outcome": action.outcome,
                        "note": action.closed_note}, **base))
    return out


def correction_entries(audit_rows: Sequence[dict]) -> List[HistoryEntry]:
    """Rows from `CorrectionAuditStore.history`."""
    out: List[HistoryEntry] = []
    for row in audit_rows or ():
        test_name = str(row.get("test_name") or "")
        units = str(row.get("units") or "")
        tail = f" {units}" if units else ""
        out.append(HistoryEntry(
            at=str(row.get("changed_at") or ""),
            uid=str(row.get("uid") or f"corr:{test_name}:{row.get('changed_at')}"),
            source="correction_factor", kind="changed",
            machine_uid=str(row.get("machine_uid") or ""),
            summary=(f"{test_name} correction "
                     f"{_number_text(row.get('previous'))}{tail} → "
                     f"{_number_text(row.get('new_value'))}{tail}"),
            who=str(row.get("changed_by") or ""),
            detail={"test_name": test_name,
                    "previous": row.get("previous"),
                    "new_value": row.get("new_value"),
                    "units": units,
                    "reason": str(row.get("reason") or "")}))
    return out


def log_entries(log_rows: Sequence[dict]) -> List[HistoryEntry]:
    """Rows from `lem_machine_log` — runs, QC verdicts, status changes,
    overrides, comments, PM and calibration completions, config audits.

    The log has no id column, so the uid is built from the row — **including a
    fingerprint of the value and the detail**. Keyed on (machine, ts, kind,
    test, lab_id) alone, two prints in the same second with different values
    collided on one uid: two distinct events sharing an identity, in a merge
    whose ordering rests on uids being unique, and in `_causal_order`, which
    holds chains together by uid. Two byte-identical rows still share a uid,
    and those really are one event described twice. The identity itself lives
    in `log_event_ref`, because a `trigger_ref` has to be able to name one —
    two copies of it would drift, and this one decides whether a corrective
    action points at the right failure.
    """
    out: List[HistoryEntry] = []
    for row in log_rows or ():
        detail = _detail_dict(row.get("detail"))
        kind = str(row.get("kind") or "").strip() or "event"
        ts = str(row.get("ts") or "")
        machine_uid = str(row.get("machine_uid") or "")
        lab_id = str(row.get("lab_id") or "")
        test_name = str(row.get("test_name") or "")
        value = str(row.get("value") or "")
        bits = [b for b in (kind.upper(), test_name, value, lab_id) if b]
        out.append(HistoryEntry(
            at=ts, uid=log_event_ref(row),
            source="log", kind=kind, machine_uid=machine_uid,
            summary=str(detail.get("task") or detail.get("action")
                        or " ".join(bits)),
            who=str(detail.get("by") or ""),
            detail={"lab_id": lab_id, "test_name": test_name, "value": value,
                    **detail}))
    return out


def action_event_entries(event_rows: Sequence[dict]) -> List[HistoryEntry]:
    """Rows from `lem_action_events` — a reassignment, or a note.

    They point at their action with `caused_by`, so a bench clock cannot float
    a note above the action it is about, and they carry the same
    `corrective_action` source: to a reader they are the same story, and the
    merge already ranks a human response after the thing it responds to.
    """
    out: List[HistoryEntry] = []
    for row in event_rows or ():
        kind = str(row.get("kind") or "note")
        note = str(row.get("note") or "")
        detail = row.get("detail")
        detail = detail if isinstance(detail, dict) else _detail_dict(detail)
        out.append(HistoryEntry(
            # Namespaced like `log:` and `maint:`: every uid in the merge says
            # which record it came from, and `_causal_order` holds chains
            # together by uid — two ID spaces sharing one is a collision
            # waiting for the day somebody stops using uuid4.
            at=str(row.get("at") or ""),
            uid="event:{}".format(row.get("uid") or ""),
            source="corrective_action", kind=kind,
            machine_uid=str(row.get("machine_uid") or ""),
            summary=("Reassigned" if kind == "assigned" else "Note")
                    + (f" — {note}" if note else ""),
            who=str(row.get("by_user") or ""),
            caused_by=str(row.get("action_uid") or ""),
            detail=dict(detail, note=note)))
    return out


def maintenance_entries(task_rows: Sequence[dict]) -> List[HistoryEntry]:
    """Rows from `lem_maintenance` — a PM or calibration task and when it was
    last done. A task never done yields nothing: `last_done` empty means the
    work has not happened, and an entry dated "" would claim it did."""
    out: List[HistoryEntry] = []
    for row in task_rows or ():
        last_done = str(row.get("last_done") or "").strip()
        if not last_done:
            continue
        name = str(row.get("name") or "")
        kind = str(row.get("kind") or "pm").strip() or "pm"
        out.append(HistoryEntry(
            at=last_done, uid=f"maint:{row.get('uid')}:{last_done}",
            source="maintenance", kind=kind,
            machine_uid=str(row.get("machine_uid") or ""),
            summary=f"{kind.upper()} completed — {name}",
            detail={"task": name, "note": str(row.get("note") or ""),
                    "interval_days": row.get("interval_days")}))
    return out


# ── the read side ──────────────────────────────────────────────────────────

class Timeline(list):
    """The entries — and whether they are all of them.

    A bare list has nowhere to put "there is more", so a history that stopped at
    `LOG_LIMIT` rows simply stopped and the reader could not tell a quiet
    instrument from a truncated page. A history that quietly stops is worse than
    one that says it is showing the most recent 200: the first is read as "that
    is everything that happened", which about a compliance record is a lie.

    It **is** a list, so every caller that only renders entries is unchanged;
    `truncated` and `note` are there for the one that draws the footer.
    """

    def __init__(self, entries=(), truncated: bool = False, note: str = "",
                 limit: Optional[int] = None,
                 next_before: Optional[str] = None) -> None:
        super().__init__(entries)
        self.truncated = bool(truncated)
        self.note = note
        self.limit = limit
        #: Where the NEXT page starts, as `ts|rowid`. A bare timestamp cannot
        #: address a row in this table — whole-second stamps are ordinary, so
        #: `ts < cursor` steps over every row sharing the last second of a
        #: page. Measured on the live lab before this existed: a walk of one
        #: instrument found 21,854 of 26,107 rows and reported reaching the
        #: start of the record.
        self.next_before = next_before

    def to_dict(self) -> dict:
        return {"entries": [e.to_dict() for e in self], "count": len(self),
                "next_before": self.next_before,
                "truncated": self.truncated, "note": self.note,
                "limit": self.limit}


class _JudgedRead:
    """The gateway, with the answer to the last read kept.

    `MachineStateReader.events` owns the machine-log query and answers a failed
    read with `[]` — its own rule, in a module this one does not own and must
    not edit. Copying the query here instead would put two versions of the
    statement that decides what a compliance timeline contains in one codebase,
    and they drift. So the query is still borrowed and the VERDICT is taken
    back: the raw answer is kept as it passes and re-judged by `_answered`.
    """

    def __init__(self, gateway) -> None:
        self._gateway = gateway
        self.answer = None

    def read_sql(self, sql, args=None, **kw):
        self.answer = self._gateway.read_sql(sql, args, **kw)
        return self.answer

    def __getattr__(self, name):
        return getattr(self._gateway, name)


#: "Every row, however many there are." A distinct value rather than `None`,
#: which already means "the caller did not say" — one sentinel for two
#: different questions is how `limit=all` came back with the default 200.
ALL = "all"

class EquipmentHistory:
    """One instrument's whole history, merged.

    Five reads per call — corrective actions, what was said about them, the
    correction audit, the machine log and maintenance — and deliberately **not**
    served from the snapshot: the floor does not poll this — a person opens one
    instrument's history and reads it — so it costs nothing when nobody is
    looking, which is the rule the whole snapshot design exists to keep. If it
    ever does end up on a polled page, the tables go into `_ARMS` instead,
    padded to the same column count as every other arm.

    **A source that could not be read raises** (`LabCoreUnavailable`) rather
    than being left out. A timeline is not a dashboard tile: `truncated` and
    `note` are a claim about completeness, and a page that drops the corrective
    actions during a blip and still says it is showing everything tells a
    supervisor the instrument has no open action against it. A missing table is
    the one exception, because it means the record genuinely does not exist yet.
    """

    #: How deep the log read goes when the caller does not say. A DEFAULT, not
    #: a ceiling — it used to be the latter, so `timeline(limit=5000)` still
    #: came back with 200 log rows and reported itself truncated, and the
    #: History tab could not show more of the record however hard it asked.
    #: Ryan: "I dont like that the history is cut off … make it actually show
    #: the entire database."
    LOG_DEFAULT = 200

    #: Kept as the old name so nothing that imported it breaks; it is the
    #: default now and nothing reads it as a cap.
    LOG_LIMIT = LOG_DEFAULT

    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self.actions = CorrectiveActionStore(gateway)
        self.corrections = CorrectionAuditStore(gateway)

    def timeline(self, machine_uid: str, limit: Optional[int] = None,
                 newest_first: bool = True, before: Optional[str] = None,
                 depth: Optional[int] = None,
                 log_rows: Optional[List[dict]] = None,
                 log_cut: bool = False) -> Timeline:
        """Everything that happened to one instrument, and whether that is all
        of it.

        Raises `LabCoreUnavailable` if any source could not be read — see the
        class docstring for why that is better than a shorter list.
        """
        # `log_rows` handed in means the caller already has them — the local
        # mirror, for a deep walk. The other four sources are still read live:
        # corrective actions and PM completions are small, current, and not
        # what made this expensive.
        if log_rows is None:
            log_rows, log_cut = self._log(machine_uid, depth=depth,
                                          before=before)
        log = log_entries(log_rows)
        entries = merge_timeline(
            action_entries(self.actions.for_machine(machine_uid),
                           log_uids={e.uid for e in log}),
            action_event_entries(
                self.actions.events_for_machine(machine_uid)),
            correction_entries(self.corrections.history(machine_uid)),
            log,
            maintenance_entries(self._maintenance(machine_uid)),
            newest_first=newest_first)
        limit_cut = bool(limit) and len(entries) > int(limit)
        if limit_cut:
            # `merge_timeline`'s rule, applied here so the count before the cut
            # is known: the newest are kept whichever way it is read.
            entries = (entries[:int(limit)] if newest_first
                       else entries[-int(limit):])
        notes = []
        if limit_cut:
            notes.append(f"Showing the {int(limit)} most recent entries.")
        if log_cut:
            notes.append(f"Showing the most recent {self.LOG_LIMIT} log "
                         f"entries for this equipment; older runs, QC "
                         f"verdicts and status changes are not listed.")
        # The cursor comes off the LOG, because the log is what is paged; the
        # other four sources are small and read whole every time.
        tail = log_rows[-1] if log_rows else None
        cursor = None
        if tail is not None and tail.get("rowid_src") is not None:
            cursor = "%s|%s" % (tail.get("ts"), tail.get("rowid_src"))
        elif tail is not None:
            cursor = str(tail.get("ts") or "") or None
        return Timeline(entries, truncated=bool(limit_cut or log_cut),
                        note=" ".join(notes), limit=limit, next_before=cursor)

    def _log(self, machine_uid: str, depth: Optional[int] = None,
             before: Optional[str] = None) -> Tuple[List[dict], bool]:
        """The machine log, newest first, and whether it was cut short.

        Asks for one row more than it will show. Reading exactly `LOG_LIMIT`
        rows out of a `LIMIT LOG_LIMIT` query cannot tell you whether there were
        more, so the boundary case — an instrument with exactly 200 events —
        would have had to be announced as truncated or guessed at. One extra row
        makes it exact.

        The query itself is `MachineStateReader.events`, not a copy of it —
        and the judgement of what it answered is this module's, not that one's:
        see `_JudgedRead`. Announcing `truncated=False` over a log that timed
        out would be this page certifying an instrument's quiet week.
        """
        seam = _JudgedRead(self.gateway)
        # `None` meant BOTH "caller said nothing" and "caller wants
        # everything", so `limit=all` quietly came back with the default 200 —
        # a wrong answer that looks exactly like a right one. The string is the
        # sentinel now, and the two cases cannot be confused.
        want = self.LOG_DEFAULT if depth is None else (
            None if depth == ALL else depth)
        reader = MachineStateReader(seam)
        if want is None:
            # Everything. Asked for explicitly (`limit=all`), never by default.
            log_rows = reader.events(machine_uid, None, before=before)
            _answered(seam.answer, "reading lem_machine_log")
            return log_rows, False
        log_rows = reader.events(machine_uid, int(want) + 1, before=before)
        # The rows come from the reader; only the verdict comes from here. The
        # return value is deliberately discarded — `_answered` is called for
        # the exception it raises when the answer was not usable.
        _answered(seam.answer, "reading lem_machine_log")
        if len(log_rows) > int(want):
            return log_rows[:int(want)], True
        return log_rows, False

    def _maintenance(self, machine_uid: str) -> List[dict]:
        """PM and calibration completions for this instrument.

        `maintenance_entries` existed and was never called, so "one
        instrument's whole history" silently excluded every PM and every
        calibration — the two things a supervisor is most likely to be looking
        for the date of.

        Read here rather than through `MaintenanceStore`, which is the opposite
        call from `_log` above and for a specific reason: every one of its
        readers goes through `ensure_schema()`, which issues a bare CREATE TABLE
        on the write path. Reading a history must not write, and this module
        does not declare its own schema (see `HISTORY_DDL`).

        `missing_ok=True`: `lem_maintenance` is created centrally at boot, so
        its absence means no PM or calibration has ever been configured. A
        failed read raises — "no calibration on record" is a sentence with
        consequences, and it must not be produced by a timeout.
        """
        return [dict(row) for row in _answered(self.gateway.read_sql(
            "SELECT uid, machine_uid, name, kind, interval_days, last_done, "
            "note FROM lem_maintenance WHERE machine_uid = ? "
            "ORDER BY kind, name", [machine_uid]),
            "reading lem_maintenance")]
