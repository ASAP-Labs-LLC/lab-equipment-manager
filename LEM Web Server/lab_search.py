"""Lab-wide search: matching and ranking, over rows somebody else already has.

Ryan asked for "sample searchability" ahead of the ISO/IEC 17025 PJLA
assessment in September 2026. The thing being asked for is not a `LIKE` query —
it is "an assessor or a bench tech types a word and finds the thing", and the
words they type are a Lab ID, an instrument, a method, a QC standard, a level,
or a person.

WHY THIS MODULE TOUCHES NOTHING
-------------------------------
A search box is the worst possible shape for the load pattern CLAUDE.md's
performance section exists to kill. `/api/machines` used to cost 17 LabCore ops
per refresh and that was bad enough at one refresh per screen; a search that
reads on keystroke costs one op per *character* per viewer, and LabCore's queue
serialises at ~1.5 ops/sec. So there is no gateway here, no Flask, no import of
`snapshot_service`, and no I/O of any kind. The caller hands in rows the
snapshot is already holding in memory — the machine payload, the log events,
the level ladder — and gets an answer back. A tripwire in the tests asserts the
module cannot even reach LabCore.

FOLD ONCE PER SNAPSHOT, MATCH PER KEYSTROKE
-------------------------------------------
`build_index()` does every allocation that depends on the ROWS: normalising,
lowering, stripping punctuation, finding word boundaries, aggregating a method
across the instruments that run it. That is O(rows) and belongs on the
snapshot's 12-second cycle (or behind `_page()`, keyed by the snapshot's age —
the cache in `web_app.py` whose invalidation is explicit). `search()` over a
built index folds nothing and normalises nothing: it is `str` comparison —
`==`, `startswith` and `in` — against strings that were folded once, and it
allocates only for the records that actually matched. That is what makes a
per-keystroke search affordable at all.

THE QUERY IS INPUT, NEVER A PATTERN
------------------------------------
It arrives from a text box on a page anybody in the lab can open. `%`, `_`,
`.*`, `[a-z]` and `(((((` are characters a person might type, and every one of
them is looked FOR, not obeyed. There is no `re` in this module, no `fnmatch`,
no `LIKE`; matching is `str` methods on folded text, so there is no pattern to
compile and nothing to backtrack. This is also why the query is length-capped:
it bounds the needle in every `find`, and it stops a megabyte of paste being
echoed back in the answer.

CAPS ARE REPORTED, NEVER SILENT
--------------------------------
"Showing 25" drawn over 313 matches, without saying so, reads to an assessor as
"the lab has 25 of those". Every cap in here is in the answer: `limit`,
`per_kind_limit`, `matched` vs `shown` per kind, `truncated`, `machine_count`
beside a truncated `machines` list, `query_truncated`, and `max_query_tokens`
beside `query_tokens_capped`.

The last of those was the one that got away for a while. `MAX_QUERY_TOKENS` is
not a display cap — it changes the ANSWER, because a query with more distinct
words than it will check is refused the tokens tier rather than approved on a
sample of itself. So one more correct word could turn a hit into "no results"
with every reported flag saying nothing had been bounded.
"""
from __future__ import annotations

import json
import unicodedata
from typing import Dict, List, Optional, Sequence, Tuple

# ── the four blank-ish answers ────────────────────────────────────────────
#
# Four, not two. "Nothing found" and "you have not typed anything yet" are
# different facts and a box that cannot tell them apart draws "No results" at
# rest, which reads as an empty lab.
STATE_IDLE = "idle"          # nothing typed
STATE_SHORT = "short"        # typed, but not enough to search on
STATE_NO_MATCH = "no_match"  # searched, found nothing
STATE_OK = "ok"              # searched, found something

# ── result kinds, in the order they break a tie ───────────────────────────
KIND_SAMPLE = "sample"
KIND_EQUIPMENT = "equipment"
KIND_STANDARD = "standard"
KIND_METHOD = "method"
KIND_LEVEL = "level"
KIND_OPERATOR = "operator"
KINDS: Tuple[str, ...] = (KIND_SAMPLE, KIND_EQUIPMENT, KIND_STANDARD,
                          KIND_METHOD, KIND_LEVEL, KIND_OPERATOR)

# ── how a field matched ───────────────────────────────────────────────────
MATCH_EXACT = "exact"          # the whole field, once folded, IS the query
MATCH_PREFIX = "prefix"        # the field starts with the query
MATCH_WORD = "word"            # some word inside the field starts with it
MATCH_TOKENS = "tokens"        # every word of the query starts some word here
MATCH_SUBSTRING = "substring"  # it is in there somewhere

# THE RANKING RULE, and why it is three bands that cannot overlap.
#
# score = TIER + KIND + COVERAGE, and the bands are sized so that each one
# strictly dominates the one below it:
#
#   TIER      10000 / 8000 / 6000 / 4000 / 2000   gap 2000
#   KIND        500 / 400 / 300 / 200 / 100 / 50  gap 50, max 500
#   COVERAGE   0…49                               max 49
#
#   max(KIND) + max(COVERAGE) = 549  <  2000, so HOW WELL a query matched
#     always beats WHAT it matched. This is the requirement "an exact id match
#     must outrank a fuzzy title match", stated as an arithmetic invariant
#     rather than as a hope — a substring hit can never climb into the exact
#     band no matter how favoured its kind. It is also the honest ordering:
#     a person who typed a whole Lab ID typed the answer, and the thing that
#     equals what they typed is the thing they meant.
#
#   max(COVERAGE) = 49 < 50 = min gap between kinds, so within a tier the kind
#     decides, and coverage only ever separates things of the SAME tier and
#     kind. Kind order puts `sample` first because that is the search Ryan
#     asked for and the one an assessor runs; equipment next because it is the
#     other thing with a hard identity; then standard, method, level; operator
#     last, because a person's name is the loosest thing in the lab (it is
#     also, today, the thing least likely to be there at all).
#
#   COVERAGE is what fraction of the field the query accounted for. It is the
#     tiebreak that makes "flash" prefer "Flash Point" over "Flash Point
#     Autosampler Recalibration Rig": at equal tier and kind, the field the
#     query nearly fills is the field the query was about.
TIER_SCORE: Dict[str, int] = {
    MATCH_EXACT: 10000,
    MATCH_PREFIX: 8000,
    MATCH_WORD: 6000,
    MATCH_TOKENS: 4000,
    MATCH_SUBSTRING: 2000,
}
KIND_WEIGHT: Dict[str, int] = {
    KIND_SAMPLE: 500, KIND_EQUIPMENT: 400, KIND_STANDARD: 300,
    KIND_METHOD: 200, KIND_LEVEL: 100, KIND_OPERATOR: 50,
}
COVERAGE_MAX = 49

# ── the bounds ────────────────────────────────────────────────────────────
MIN_QUERY_CHARS = 2      # one character substring-matches most of the lab; the
                         # honest answer to it is "keep typing", not a capped
                         # dump that looks like a considered result set.
MAX_QUERY_CHARS = 128    # bounds the needle in every find(), and stops a paste
                         # being echoed back in the payload.
MAX_QUERY_TOKENS = 8     # bounds the tokens tier's nested loop (query words ×
                         # field words) to 8 × field words. It is not the only
                         # nested loop in the module — `_match` walks a
                         # record's fields and `build_index` walks each
                         # machine's specs — but those two are bounded by the
                         # rows, and this one is bounded by what a stranger
                         # typed. Reported, because it changes the answer:
                         # see `search()`.
MIN_TOKEN_PREFIX_CHARS = 2   # below this a query word must BE a field word
                             # rather than merely begin one. See `_tier`.
DEFAULT_LIMIT = 25
MAX_LIMIT = 200
PER_KIND_LIMIT = 10      # so 300 matching Lab IDs cannot push the one matching
                         # instrument off the page. See `_assemble`.
MAX_HIT_MACHINES = 8     # a method can run on the whole fleet; the answer
                         # carries the first few and the true count beside them.

# Kinds whose same-score tie is broken by recency rather than by name. A tech
# chasing a Lab ID wants the run that just happened; a tech looking for
# "Flash Point" wants it spelled the way it is spelled.
_RECENCY_KINDS = frozenset({KIND_SAMPLE, KIND_OPERATOR})

# Where an operator's name might live. Another agent is adding this column as
# this ships, so every spelling anybody might have chosen is tried and NONE of
# them being there is a normal, silent outcome — not an error, and not a kind
# that appears empty. `detail` is checked last because levels.py already writes
# `{"by": …}` JSON in there for move logs, so the name is sometimes already
# present without a column at all.
_OPERATOR_KEYS = ("operator", "operator_name", "operator_id", "who", "by",
                  "user", "username", "performed_by", "recorded_by")


def squash(text) -> str:
    """Fold to the one comparable form: NFKD, lowercase, alphanumerics only.

    `PAC Flash 2`, `pac-flash-2` and `PACFLASH2` are one instrument, and the
    dash is the difference between how the floor labels it and how the config
    names it. Dropping every separator rather than normalising it is what makes
    `flash2` reach `PAC Flash 2` — there is no separator left to disagree over.

    The NFKD step is there because this lab pastes IDs out of other systems.
    A LIMS or an Excel export hands over PAC Flash 2 in full-width forms
    (`ＰＡＣ　Ｆｌａｓｈ　２`), `flash` with an `ﬂ` ligature, `²` as a
    superscript and `café` as either one code point or two — every one of
    which `isalnum()` happily keeps and `lower()` leaves unequal to the ASCII
    the config was typed in. Compatibility decomposition turns them all back
    into the letters and digits somebody meant.
    """
    return _fold(text).squashed


class _Folded:
    """One field, pre-folded: the comparable string and where its words start.

    `bounds` is what separates "the query starts a word here" from "the query
    happens to appear here". Letter↔digit transitions count as word starts, so
    `L-37712` and `L37712` both give the bare `37712` a boundary to land on —
    which is the difference between the headline search working and it ranking
    below every accidental substring in the lab.

    `tokens` is DISTINCT, in the order first seen. On a field that is just a
    smaller pool of words to match against; on a query it is load-bearing, and
    it is what makes `l l` one letter typed twice rather than the two words
    the tokens tier is about. There is deliberately no `raw` — one is built
    per field of every record, nothing ever read it back, and `__slots__` is
    on this class precisely because it is the per-record cost.
    """
    __slots__ = ("squashed", "bounds", "tokens")

    def __init__(self, squashed: str, bounds: Tuple[int, ...],
                 tokens: Tuple[str, ...]):
        self.squashed = squashed
        self.bounds = bounds
        self.tokens = tokens


_EMPTY = _Folded("", (), ())


def _fold(text) -> _Folded:
    if text is None:
        return _EMPTY
    raw = text if isinstance(text, str) else str(text)
    if not raw:
        return _EMPTY
    # Compatibility decomposition FIRST, so everything below is deciding about
    # ASCII-ish letters and digits. See `squash` for why a fuel lab needs it.
    raw = unicodedata.normalize("NFKD", raw)
    chars: List[str] = []
    bounds: List[int] = []
    tokens: List[str] = []
    current: List[str] = []
    previous = ""      # "a" alphabetic, "d" numeric, "" nothing yet
    broken = True
    for ch in raw:
        # A combining mark left over from the decomposition is neither a
        # character nor a separator: dropping it is what folds `Müller` to
        # `muller` instead of breaking it into `m` and `ller`.
        if unicodedata.combining(ch):
            continue
        if ch.isalnum():
            group = "d" if ch.isdigit() else "a"
            if broken or group != previous:
                bounds.append(len(chars))
                if current:
                    tokens.append("".join(current))
                    current = []
            # `extend`, not `append`: a handful of code points lower-case to
            # more than one character, and `bounds` indexes the joined string.
            low = ch.lower()
            chars.extend(low)
            current.extend(low)
            previous = group
            broken = False
        else:
            broken = True
            previous = ""
    if current:
        tokens.append("".join(current))
    # Distinct, first-seen order. `dict` is the ordered set here.
    return _Folded("".join(chars), tuple(bounds), tuple(dict.fromkeys(tokens)))


def _tier(field: _Folded, query: _Folded) -> Optional[str]:
    """How `query` matched `field`, best first, or None.

    Every step is an equality, a `str.startswith` or an `in` over text that
    was folded once, at build time. There is no pattern here to compile and
    none to backtrack, which is the whole reason a query typed by anybody with
    the URL cannot wedge the process.
    """
    haystack = field.squashed
    needle = query.squashed
    if not haystack or not needle:
        return None
    if haystack == needle:
        return MATCH_EXACT
    if haystack.startswith(needle):
        return MATCH_PREFIX
    for offset in field.bounds:
        if haystack.startswith(needle, offset):
            return MATCH_WORD
    # Out-of-order and gapped queries — "flash pac", "pac 2" — but only when
    # the query really is several DISTINCT words. For a single word this tier
    # would be a strictly worse duplicate of the substring test below and
    # would drag every field sharing one prefix into the answer; and `l l` is
    # one word typed twice, which is why `query.tokens` is deduplicated at
    # fold time rather than counted raw here.
    if len(query.tokens) > 1 and _every_token_is_a_word_here(field, query):
        return MATCH_TOKENS
    if needle in haystack:
        return MATCH_SUBSTRING
    return None


def _every_token_is_a_word_here(field: _Folded, query: _Folded) -> bool:
    """EVERY query word, or the answer is no — and a word has to earn a prefix.

    Two rules, and the module reached production with neither:

    1. **A word of one character must BE a word of the field, not merely begin
       one.** `word.startswith(token)` is very nearly `True` for a
       single-character token against anything, so a one-character word is a
       wildcard wearing a word's clothes. Measured on a 60-instrument /
       8000-row lab, `l l` came back OK at THIS tier with 99.1% of the index —
       scoring above every honest substring match on the page, because the
       tier band is what dominates the score. `(a+)+$(a+)+$` is twelve
       characters, none of them a word, and it answered with *Diesel - AO25*.
       In a box an assessor types into, a nonsense query answering with a real
       record is worse than answering with nothing.

       The rule is a floor on the PREFIX, not on the query: `PAC 2` still
       reaches *PAC Flash 2* and `GC 2` still reaches *GC Bench 2*, because
       `2` is exactly a word of both. `l 37712` still reaches *L-37712*. What
       stops is `a` standing in for `ao25`.

    2. **EVERY word, checked.** This used to loop over
       `query.tokens[:MAX_QUERY_TOKENS]` and return True at the end, which is
       a claim about every word made after looking at eight of them. A query
       with more distinct words than can be checked is refused at this tier
       rather than approved on a sample of itself — and `search()` reports
       that it was, because refusing changes the answer.

    A query refused here still reaches the substring test below, which judges
    the whole needle and cannot be fooled either way.
    """
    if len(query.tokens) > MAX_QUERY_TOKENS:
        return False
    words = field.tokens
    for token in query.tokens:
        if len(token) < MIN_TOKEN_PREFIX_CHARS:
            if token not in words:
                return False
        elif not any(word.startswith(token) for word in words):
            return False
    return True


class _Record:
    """One searchable thing, with everything a caller needs to navigate to it.

    `fields` is ordered id-first: when two fields of the same record match at
    the same tier and cover the same fraction, the identity is the one
    reported, because "matched the uid" is a more useful thing to show a person
    than "matched the title".
    """
    __slots__ = ("kind", "ident", "label", "fields", "machines", "ts", "meta",
                 "level_uid", "machine_uid", "machine_title", "machine_total")

    def __init__(self, kind, ident, label, fields, machines, ts, meta,
                 level_uid, machine_uid, machine_title, machine_total):
        self.kind = kind
        self.ident = ident
        self.label = label
        self.fields = fields
        self.machines = machines
        self.ts = ts
        self.meta = meta
        self.level_uid = level_uid
        self.machine_uid = machine_uid
        self.machine_title = machine_title
        self.machine_total = machine_total


class SearchIndex:
    """Folded rows, ready to be matched against. Immutable by convention.

    Build it once per snapshot; search it on every keystroke. `counts` is how
    many records of each kind were folded in, and `search()` reports it as
    `indexed` — which is the only way the claim "a caller can prove the index
    it is serving from was built from the rows it thinks it was" is true. It
    was built and read by nothing, so no caller could prove anything with it.
    """
    __slots__ = ("records", "counts")

    def __init__(self, records: Sequence[_Record], counts: Dict[str, int]):
        self.records = tuple(records)
        self.counts = dict(counts)

    def __len__(self) -> int:
        return len(self.records)


EMPTY_INDEX = SearchIndex((), {kind: 0 for kind in KINDS})


def _text(row: dict, *keys) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = value if isinstance(value, str) else str(value)
        text = text.strip()
        if text:
            return text
    return ""


def _rows(source) -> List[dict]:
    # A partially-migrated table, a payload built before a column existed, and
    # a caller that passed the wrong thing all look the same from here: skip it
    # and carry on. A search box is not the place to discover a bad row.
    if not source:
        return []
    return [row for row in source if isinstance(row, dict)]


def _operator_of(row: dict) -> str:
    """The person on a log row, if anybody has written one there yet.

    Deliberately total: absent is absent. This column is being added by another
    agent right now, so the module has to be correct both before and after it
    lands, and the "before" answer is silence — not an empty `operator` kind
    and not an exception on a page an assessor is looking at.
    """
    for key in _OPERATOR_KEYS:
        value = row.get(key)
        if isinstance(value, (dict, list, tuple, set)) or value is None:
            continue
        text = (value if isinstance(value, str) else str(value)).strip()
        if text:
            return text
    detail = row.get("detail")
    if isinstance(detail, str):
        stripped = detail.strip()
        # Only spend the parse on something already shaped like an object.
        # `detail` is free text on most log kinds and json.loads on every log
        # row of every keystroke-built index is a cost with no answer in it.
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                detail = json.loads(stripped)
            except (ValueError, TypeError):
                return ""
        else:
            return ""
    if isinstance(detail, dict):
        for key in _OPERATOR_KEYS:
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _entry_key(entry: dict) -> Tuple[str, str, str]:
    """THIS module's total order over instruments. It borrows nobody else's.

    `_Group.machines` is insertion-ordered, i.e. the machine payload's order,
    and falling back to it looked perfectly stable — because `build_machines`
    happens to sort that payload by `(title, uid)` today. It is masked, not
    correct: reversing the payload changed which bench a standard hit
    navigates to, and which eight of twelve a method hit lists. CLAUDE.md
    records this exact burn on the floor map — two instruments, a sort key
    that ties, a stable sort, and the payload left holding the decision, so
    one of them was simply invisible.

    So the key is total on its own: the title, case-insensitively, then the
    title as written (two titles differing only in case are two titles), then
    the uid, which is unique. Nothing in it can tie.
    """
    title = entry.get("title") or ""
    return (title.casefold(), title, entry.get("machine_uid") or "")


class _Group:
    """A thing that spans instruments — a method, a standard, a Lab ID, a name.

    `machines` is uid → (entry, latest_ts) so the answer can say which benches,
    in the order that kind wants them, without a second pass over the rows.

    `stamp` is which ROW is currently supplying `label` and `meta`. It exists
    because "the newest row wins" is not an order when two rows carry the same
    timestamp: `ts >= group.ts` after `note()` has already raised `group.ts`
    to `ts` is true for every row that ties the maximum, so the last one in
    payload order won and a meta panel flipped its reading between refreshes.
    """
    __slots__ = ("label", "machines", "ts", "meta", "stamp")

    def __init__(self, label: str):
        self.label = label
        self.machines: Dict[str, Tuple[dict, str]] = {}
        self.ts = ""
        self.meta: Dict[str, str] = {}
        self.stamp: Tuple[str, ...] = ()

    def note(self, entry: Optional[dict], ts: str = "") -> None:
        if entry is not None:
            uid = entry["machine_uid"]
            seen = self.machines.get(uid)
            if seen is None or ts > seen[1]:
                self.machines[uid] = (entry, ts)
        if ts > self.ts:
            self.ts = ts

    def name_seen(self, name: str) -> None:
        """Several spellings fold to one key; one of them is shown.

        `Flash Point` and `flash point` are one method and the answer prints
        one label. Which one was whichever row came first — payload order
        again. The smallest wins now, so it is the rows that decide and not
        their order. A group whose label is claimed by a row (below) is left
        alone: for those the newest row's spelling is the right one.
        """
        if not self.stamp and name < self.label:
            self.label = name

    def claim(self, stamp: Tuple[str, ...], label: str,
              meta: Dict[str, str]) -> None:
        """The row with the greatest stamp supplies the label and the meta.

        `ts` leads the stamp, so recency still decides outright and this only
        ever settles a tie. The rest of the stamp is the row's own content —
        never its position — so every ordering of the same rows gives the same
        answer.
        """
        if stamp > self.stamp:
            self.stamp = stamp
            self.label = label
            self.meta = meta

    def ordered(self, by_recency: bool) -> List[dict]:
        items = sorted(self.machines.items(),
                       key=lambda item: _entry_key(item[1][0]))
        if by_recency:
            # ISO-8601 sorts lexicographically, which is the same assumption
            # `events_from_tables` already makes when it orders the feed.
            # `reverse=True` keeps this sort stable, so `_entry_key` above
            # survives inside an identical timestamp and the pair is total.
            items.sort(key=lambda item: item[1][1], reverse=True)
        return [entry for _uid, (entry, _ts) in items]


def build_index(machines=None, events=None, levels=None) -> SearchIndex:
    """Fold every row once. O(rows), no I/O, and no row is held onto.

    What is retained is the folded text, the word boundaries, and a small
    `{machine_uid, title, level_uid}` entry per instrument — never the caller's
    dicts, so a snapshot that moves on cannot be read back out of an index.

    `machines` is the `/api/machines` payload's list, `events` is
    `events_from_tables`' output, `levels` is `Level.to_dict()`. All three are
    already in the snapshot; none of them is read from here.
    """
    machine_rows = _rows(machines)
    event_rows = _rows(events)
    level_rows = _rows(levels)

    records: List[_Record] = []
    directory: Dict[str, dict] = {}

    # ── instruments ────────────────────────────────────────────────────────
    for row in machine_rows:
        uid = _text(row, "machine_uid", "uid")
        title = _text(row, "title") or uid
        if not uid and not title:
            continue
        entry = {"machine_uid": uid, "title": title,
                 "level_uid": _text(row, "level_uid")}
        if uid:
            directory[uid] = entry
        records.append(_Record(
            kind=KIND_EQUIPMENT, ident=uid or squash(title), label=title,
            fields=(("machine_uid", _fold(uid)), ("title", _fold(title))),
            machines=[entry], ts=_text(row, "last_activity", "updated_at"),
            meta={"status": _text(row, "status")},
            level_uid=entry["level_uid"], machine_uid=uid,
            machine_title=title, machine_total=1))

    # ── methods and standards, aggregated across the fleet ────────────────
    #
    # One hit per method, not one per (method, instrument): "Flash Point" is
    # one thing an assessor asks about, and answering with four rows that
    # differ only by bench pushes everything else off the page.
    methods: Dict[str, _Group] = {}
    standards: Dict[str, _Group] = {}

    def _collect(bucket: Dict[str, _Group], name: str, entry, ts=""):
        key = squash(name)
        if not key:
            return None
        group = bucket.get(key)
        if group is None:
            group = bucket[key] = _Group(name)
        else:
            group.name_seen(name)
        group.note(entry, ts)
        return group

    for row in machine_rows:
        entry = directory.get(_text(row, "machine_uid", "uid"))
        for spec in _rows(row.get("effective_specs")):
            _collect(methods, _text(spec, "test_name"), entry)
            _collect(standards, _text(spec, "sample_id"), entry)
        for spec in _rows(row.get("qc_specs")):
            _collect(methods, _text(spec, "test_name"), entry)
            _collect(standards, _text(spec, "sample_id"), entry)
        for target in _rows(row.get("qc_targets")):
            _collect(methods, _text(target, "test", "test_name"), entry)
            _collect(standards, _text(target, "sample", "sample_name"), entry)

    # ── Lab IDs, from the log — the headline search ───────────────────────
    samples: Dict[str, _Group] = {}
    operators: Dict[str, _Group] = {}
    for row in event_rows:
        uid = _text(row, "machine_uid")
        entry = directory.get(uid)
        if entry is None and uid:
            # A log line for an instrument that has since been retired still
            # names a real thing that happened, and hiding it would quietly
            # shorten the record an assessor is reading.
            entry = directory[uid] = {"machine_uid": uid, "title": uid,
                                      "level_uid": ""}
        ts = _text(row, "ts")
        lab_id = _text(row, "lab_id")
        if lab_id:
            group = _collect(samples, lab_id, entry, ts)
            if group is not None:
                # `ts` first, so the newest run still wins outright. The rest
                # is the row's own content, so two rows sharing a timestamp
                # are separated by what they SAY and never by where they sat
                # in the payload — the difference between a meta panel that
                # holds still and one that flips between refreshes.
                meta = {"test_name": _text(row, "test_name"),
                        "value": _text(row, "value"),
                        "log_kind": _text(row, "kind")}
                group.claim((ts, lab_id, meta["test_name"], meta["value"],
                             meta["log_kind"]), lab_id, meta)
        _collect(methods, _text(row, "test_name"), entry, ts)
        _collect(operators, _operator_of(row), entry, ts)

    def _spread(bucket: Dict[str, _Group], kind: str):
        by_recency = kind in _RECENCY_KINDS
        for key, group in bucket.items():
            listed = group.ordered(by_recency)
            first = listed[0] if listed else None
            records.append(_Record(
                kind=kind, ident=key, label=group.label,
                fields=(("name", _fold(group.label)),),
                machines=listed, ts=group.ts, meta=dict(group.meta),
                level_uid=first["level_uid"] if first else "",
                machine_uid=first["machine_uid"] if first else "",
                machine_title=first["title"] if first else "",
                machine_total=len(listed)))

    _spread(samples, KIND_SAMPLE)
    _spread(standards, KIND_STANDARD)
    _spread(methods, KIND_METHOD)
    _spread(operators, KIND_OPERATOR)

    # ── levels ────────────────────────────────────────────────────────────
    for row in level_rows:
        uid = _text(row, "uid", "level_uid")
        name = _text(row, "name")
        if not uid and not name:
            continue
        # `directory` is insertion-ordered, i.e. the machine payload's order,
        # and `MAX_HIT_MACHINES` below takes the first eight of it. Same key
        # as every other machine list in here, for the same reason.
        standing = sorted((entry for entry in directory.values()
                           if entry["level_uid"] == uid and uid),
                          key=_entry_key)
        records.append(_Record(
            kind=KIND_LEVEL, ident=uid or squash(name), label=name or uid,
            fields=(("uid", _fold(uid)), ("name", _fold(name))),
            machines=standing, ts="", meta={"rank": row.get("rank")},
            level_uid=uid, machine_uid="", machine_title="",
            machine_total=len(standing)))

    counts = {kind: 0 for kind in KINDS}
    for record in records:
        counts[record.kind] += 1
    return SearchIndex(records, counts)


def _clamp_limit(limit) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    try:
        wanted = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, wanted))


def _hit(record: _Record, tier: str, field: str, score: int) -> dict:
    if record.kind == KIND_EQUIPMENT:
        ident = record.machine_uid or record.ident
    elif record.kind == KIND_LEVEL:
        ident = record.level_uid or record.ident
    else:
        ident = record.label
    return {
        "kind": record.kind,
        # `id` is what a person recognises and it is NOT unique: `STD-1` is a
        # Lab ID that was run and a QC standard that certifies it, so one
        # search answers with two hits carrying that same `id`. A UI keying a
        # list on it draws one row where there are two. `key` is the pair, and
        # it is what a list should be keyed on.
        "key": record.kind + ":" + record.ident,
        "id": ident,
        "label": record.label,
        "score": score,
        "match": tier,
        "field": field,
        "machine_uid": record.machine_uid,
        "machine_title": record.machine_title,
        "level_uid": record.level_uid,
        "ts": record.ts,
        # Capped, and the true total is right beside it — a list of eight with
        # no count reads as "eight instruments run this method".
        "machines": [dict(entry) for entry in
                     record.machines[:MAX_HIT_MACHINES]],
        "machine_count": record.machine_total,
        "machines_truncated": record.machine_total > MAX_HIT_MACHINES,
        "meta": dict(record.meta),
    }


def _match(record: _Record, query: _Folded) -> Optional[Tuple[str, str, int]]:
    best: Optional[Tuple[int, int, str, str]] = None
    for name, folded in record.fields:
        tier = _tier(folded, query)
        if tier is None:
            continue
        coverage = min(COVERAGE_MAX,
                       (COVERAGE_MAX * len(query.squashed))
                       // max(1, len(folded.squashed)))
        candidate = (TIER_SCORE[tier], coverage, tier, name)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    if best is None:
        return None
    tier_score, coverage, tier, name = best
    return tier, name, tier_score + KIND_WEIGHT[record.kind] + coverage


def _assemble(by_kind: Dict[str, List[dict]], limit: int) -> List[dict]:
    """Pick which hits survive the cap, then put them back in score order.

    Two rules, and the first one exists because the second is not enough:

    1. **The best-SCORING hit of every kind that matched is kept.** Three
       hundred matching Lab IDs would otherwise fill the page and the one
       matching instrument would be missing — and "PAC Flash 2 is not in the
       lab" is a far more expensive wrong answer than "there were more
       samples".
    2. Everything else fills by score, capped per kind, so no one kind can take
       the whole page even when it deserves most of it.

    The cap only decides MEMBERSHIP. What comes back is always sorted by score,
    so a reserved low-scoring hit sits where its score puts it and the list is
    never in an order the ranking rule does not explain.

    Rule 1 was `by_kind[kind][0]` — and by the time this runs, `search()` has
    re-sorted each kind's list by name or by recency, so `[0]` is the
    ALPHABETICALLY FIRST hit of that kind and it was taking a guaranteed slot
    ahead of the score-ordered fill. At `limit=2` a lab holding an exact Lab
    ID (`FLASH`, 10549) and a prefix hit (`Flash Rig`, 8430) answered with the
    other two, because `Aflash Decanter` (2417) sorts first. That is the
    documented guarantee "an exact id match outranks a fuzzy title match"
    failing on MEMBERSHIP rather than on score, where no arithmetic test can
    see it. Reading the reservation off `ordered` — which is already sorted by
    score, and stably, so each kind's own tie order survives — is the fix, and
    it also means there is exactly one place in here that decides what "best"
    means.
    """
    ordered: List[dict] = []
    for kind in KINDS:
        ordered.extend(by_kind.get(kind, ()))
    ordered.sort(key=lambda hit: (-hit["score"], KINDS.index(hit["kind"])))

    reserved = set()
    spoken_for = set()
    for hit in ordered:
        if hit["kind"] not in spoken_for:
            spoken_for.add(hit["kind"])
            reserved.add(id(hit))
    chosen: List[dict] = []
    taken: Dict[str, int] = {}
    for hit in ordered:
        if len(chosen) >= limit:
            break
        if id(hit) in reserved:
            chosen.append(hit)
            taken[hit["kind"]] = taken.get(hit["kind"], 0) + 1
    for hit in ordered:
        if len(chosen) >= limit:
            break
        if id(hit) in reserved:
            continue
        if taken.get(hit["kind"], 0) >= PER_KIND_LIMIT:
            continue
        chosen.append(hit)
        taken[hit["kind"]] = taken.get(hit["kind"], 0) + 1
    chosen.sort(key=lambda hit: (-hit["score"], KINDS.index(hit["kind"])))
    return chosen


def search(query, index: Optional[SearchIndex] = None, *, limit=None) -> dict:
    """Match `query` against a built index. O(records), allocates per hit only.

    The answer always has the same keys whatever the state, so a UI reads one
    shape rather than four — the same rule `/api/machines` follows for its
    warming answer.
    """
    raw = "" if query is None else (query if isinstance(query, str)
                                    else str(query))
    query_truncated = len(raw) > MAX_QUERY_CHARS
    raw = raw[:MAX_QUERY_CHARS]
    limit = _clamp_limit(limit)
    answer = {
        "query": raw,
        "normalised": "",
        "query_truncated": query_truncated,
        "state": STATE_IDLE,
        "results": [],
        "shown": 0,
        "matched": 0,
        "truncated": False,
        "limit": limit,
        "per_kind_limit": PER_KIND_LIMIT,
        "min_query_chars": MIN_QUERY_CHARS,
        # A cap that changes the ANSWER and reports nothing is the "Showing 25
        # of 313" problem with the count left off. Past this many distinct
        # words the tokens tier is refused rather than approved on a sample of
        # the query, so one more correct word can turn a hit into "no results".
        "max_query_tokens": MAX_QUERY_TOKENS,
        "query_tokens_capped": False,
        "counts": {},
        "kinds": [],
        "searched": 0,
        # What the index was built from, per kind. See `SearchIndex`.
        "indexed": {},
    }
    if not raw.strip():
        return answer

    folded = _fold(raw)
    answer["normalised"] = folded.squashed
    answer["query_tokens_capped"] = len(folded.tokens) > MAX_QUERY_TOKENS
    if len(folded.squashed) < MIN_QUERY_CHARS:
        answer["state"] = STATE_SHORT
        return answer

    index = index or EMPTY_INDEX
    answer["searched"] = len(index.records)
    answer["indexed"] = dict(index.counts)
    by_kind: Dict[str, List[dict]] = {}
    for record in index.records:
        scored = _match(record, folded)
        if scored is None:
            continue
        tier, field, score = scored
        by_kind.setdefault(record.kind, []).append(
            _hit(record, tier, field, score))

    if not by_kind:
        answer["state"] = STATE_NO_MATCH
        return answer

    for kind, hits in by_kind.items():
        # Two stable passes: name first, then recency for the kinds that want
        # it. `reverse=True` keeps stability, so the name order survives inside
        # an identical timestamp.
        hits.sort(key=lambda hit: (hit["label"].lower(), hit["id"]))
        if kind in _RECENCY_KINDS:
            hits.sort(key=lambda hit: hit["ts"], reverse=True)

    chosen = _assemble(by_kind, limit)
    shown_by_kind: Dict[str, int] = {}
    for hit in chosen:
        shown_by_kind[hit["kind"]] = shown_by_kind.get(hit["kind"], 0) + 1

    matched = sum(len(hits) for hits in by_kind.values())
    answer["state"] = STATE_OK
    answer["results"] = chosen
    answer["shown"] = len(chosen)
    answer["matched"] = matched
    answer["truncated"] = matched > len(chosen)
    answer["counts"] = {
        kind: {"matched": len(by_kind[kind]),
               "shown": shown_by_kind.get(kind, 0)}
        for kind in KINDS if by_kind.get(kind)}
    answer["kinds"] = [kind for kind in KINDS if by_kind.get(kind)]
    return answer


def search_rows(query, machines=None, events=None, levels=None, *,
                limit=None) -> dict:
    """Build and search in one call.

    For a caller that has no index to hand — a test, or a route serving a page
    nobody polls. On a path that is hit per keystroke, build the index once per
    snapshot and call `search` instead; that is the whole point of the split.
    """
    return search(query,
                  build_index(machines=machines, events=events, levels=levels),
                  limit=limit)
