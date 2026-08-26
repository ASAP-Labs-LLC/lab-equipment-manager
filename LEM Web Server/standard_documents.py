#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
standard_documents.py — the certificate that belongs to a QC standard.

Ryan: "leave that alone for now, we have them separated for now and will bind
them later, build out the infrastructure to upload the certificate into the
standard itself."

A QC standard is a `qc_samples.QcSample`: a named lot with a Lab ID and the
tests it certifies. Its certificate — the COA, the CRM certificate from the
supplier — is the *evidence* for the numbers that standard asserts, and PJLA
assess this lab against ISO/IEC 17025 in September 2026. Until now there was
nowhere to put it.

Storage only — save, list, fetch, delete, repoint, expire. No Flask routes
live here; a later phase mounts them, exactly as `equipment_documents.py` says
about itself.

## What this deliberately does NOT do

**It does not bind the certificate's numbers to the standard.** No
`cert_value`, no `cert_uncertainty`, no `cert_k` on `QcSampleTest`, and nothing
here reads a number out of a PDF. That binding is a later, deliberate step and
this module is the file half of it: the certificate is on file, findable, and
in date. Anticipating the numeric half would mean inventing a column shape
before anyone has decided it, on a table the benches will eventually read —
which is a MAJOR release bought in advance for a feature nobody has specified.
`tests/test_standard_documents.py` holds this in place as a tripwire, because
"helpfully added the obvious next field" is exactly the change that looks
harmless in a diff.

What is left open, and only this: the row has a `uid`, so whatever binds values
later has something stable to point at.

## Bytes on disk, metadata in LabCore

The same decision `equipment_documents.py` is shaped around, and the reasoning
is unchanged: LabCore is an HTTP **write queue** in front of SQLite, it
serialises at roughly 1.5 writes/sec and refuses past ~100 pending by answering
rather than raising. A 5 MB certificate is ~6.7 MB of base64 inside a single SQL
statement, sitting in that queue ahead of every QC verdict, every result and
every heartbeat the floor is trying to write. So the file lands on the server's
own disk and LabCore holds one small row describing it: 1 write, the same cost
as ticking a checklist.

**The certificates root is a SIBLING of the equipment documents root, never a
child.** `equipment_documents.orphaned_files()` rglobs its entire root and hands
what it cannot account for to a person whose next step is deleting them. A
certificates folder underneath it would put every COA in the lab on that list.

## There is no rename verb, and that is the fact to design around

**Read this before touching anything named `rename`.** `QcSampleStore` has
`save` (upsert-by-name), `delete`, `list_samples`, `as_payload` and `by_lab_id`
— and nothing that renames. A standard is renamed by **save-new-then-delete-
old**, and three places in this tree say so in those words: `templates/
floor.html`, `templates/stations.html` and `web_app.py`'s `/api/qc-samples`
handler.

So a rename never reaches this table. The certificates keep naming the old
standard, which no longer exists: `certificates(new_name)` answers `[]` while
the PDF sits on disk under a row nobody can select any more. Two verbs exist
for exactly that, and they are the module's real answer to renaming:

* `orphaned_certificates(standard_names)` — the certificates whose standard is
  no longer in the QC library. It takes the live names as an ARGUMENT; this
  module does not know `qc_samples` exists and must not learn, because the
  dependency runs one way;
* `repoint_certificates(old, new)` — one UPDATE that puts them back on the live
  name, moving no bytes.

`rename_standard` is the same single UPDATE under the name a real rename verb
would call, kept for the day one arrives. **It has no caller and cannot have
one today.** Do not read it as the thing that stops a rename losing
certificates; nothing calls it on a rename.

And the loaded gun, stated here as well as on the method: **`delete_for_standard`
must not be wired into `DELETE /api/qc-samples`**, because a rename ENDS in that
route. It takes a required `retired=True` for that reason.

## A repoint must not move a byte

This is the one place this module's shape differs from the one it mirrors, and
it is forced by a fact about the table it hangs off:

**`lem_qc_samples` is keyed by `name` — a human string.** `QcSampleStore.save`
upserts on it, `changeover` mints a whole new lot under a new one, and a
supervisor retypes it when a lot is relabelled or a supplier's spelling is
adopted. `machine_uid`, which `equipment_documents` folds into its folder name,
is the opposite: a wire contract every bench keys its rows on, which
`EquipmentDocument` documents as un-renameable precisely because renaming it
would orphan every row silently.

So a certificate's folder is derived from `storage_key` — the name the
certificate was FILED under, written once at save time and never updated — and
the standard's current name is an ordinary column. A repoint is one UPDATE that
touches no file at all.

Deriving the folder from the current name instead would mean re-filing silently
detaches every certificate the standard has: the tab reads "no certificate on
file" about a PDF sitting on disk, `fetch` reports it missing, and the sweep
names every live certificate as deletable — in the one week of the year when
somebody is actually looking at them. The alternative fix, moving the files, is
a multi-file operation across two systems with no transaction between them; it
can half-complete, and a half-completed one leaves certificates in two folders
with the rows pointing at neither.

**The cosmetic cost, accepted:** after a repoint, older certificates sit in a
folder named for the old name and newer ones in a folder named for the new. A
person standing in the folder still reads a real name, every row still says
which folder its file is in, and nothing has to be migrated. That is the price
of never touching bytes, and it is the cheap half of the trade.

**The repoint is ONE statement.** A per-row loop is N writes at ~1.5 ops/sec,
and a refusal halfway leaves one standard's certificates split across two names,
half invisible under each — the same failure mode the house rule about upserting
first and pruning last exists to prevent. A single UPDATE cannot half-happen.

## Certificates expire, and an expired one is a finding

`expires_at` is optional — plenty of in-house standards have no stated validity
period — but when it is there it is stored as a plain `YYYY-MM-DD` date and
NOTHING else. Two reasons, and the second is the load-bearing one:

* a certificate's validity is a day, not an instant. PJLA ask whether the
  certificate was in date, not whether it was in date at 14:32;
* **that format sorts lexicographically in the same order it sorts
  chronologically**, so "everything expiring before X" is one `WHERE expires_at
  <= ?` — one read for the whole library, on a page nobody polls, with no arm
  added to the batched statement (see below). A free-text date would need every
  row parsed in Python, which means reading every row.

So `normalise_expiry` refuses anything it cannot turn into that shape rather
than storing it. `8/25/2026` stored verbatim does not merely display oddly — it
sorts AFTER every ISO date this century, so a horizon comparison alone never
reaches it, and the certificate the tab is calling EXPIRED is absent from the
library-wide report.

**Neither the SQL nor the sort is ever the verdict.** A hand-written row can be
in the table whatever `normalise_expiry` refuses, so `expiring()` selects a
deliberate SUPERSET (`_DUE_PREDICATE`) and every row is judged by
`certificate_status`. The report and one standard's tab therefore give the same
answer about the same stored value, in both directions, by construction rather
than by two implementations agreeing.

**A certificate is valid THROUGH its expiry date**, so `expired` is
`expires_at < today`. The boundary is stated because both directions are wrong
in a way somebody notices: a day early cries wolf about a valid certificate, a
day late passes an expired one at an assessment.

**The expiry report does not degrade to empty.** `equipment_documents` grants
exactly one read the licence to answer "nothing" when it does not know — the
floor's tab badge, on a page that already carries its own OFFLINE banner. An
expiry report is the opposite on every count: it is a list, it is produced
during an audit, and answering "nothing is expiring" when the truth is "could
not ask" is precisely the finding it exists to prevent. It raises.

## Not an arm of the batched read

Deliberately, for the reason CLAUDE.md states: every arm shares ONE statement,
so an extra arm is bought with the whole floor's read. Certificates are opened
by a person, and the fleet-wide question — what is expiring — is ONE read on a
page nobody polls, not sixty reads on a page that does.

## One rule, imported, never re-derived

Two of them, in fact:

* **what a LabCore answer means** is `labcore_result`, and nothing here reads a
  gateway answer for itself. Reads tell a missing table (which honestly means
  there is nothing recorded) apart from LabCore not answering (which means
  nothing at all); writes count only when the answer does not say they failed;
* **the upload primitives** — the accept-list, the signature check, the slug,
  the display name, the traversal defences — are imported from
  `equipment_documents` rather than copied. They are the same rules about the
  same kinds of file, and a second copy is how one of them gets fixed and the
  other quietly stops meaning anything. The three arguments in that module's
  docstring for slug+hash apply here word for word: readable, so a person in the
  folder knows whose certificates these are; unique, because slugging alone
  collides (`Diesel/AO25` and `Diesel:AO25` both flatten) and a shared folder
  makes one standard's retirement take the other's evidence; inert, because the
  surviving characters are `[A-Za-z0-9_-]` and dots are in the ban, so `../..`
  becomes underscores rather than a way out of the root.
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# The whole upload gate, imported. See the docstring: a second copy of a
# traversal defence is worth less than no copy at all, because it makes the
# codebase look defended in two places while only one of them is maintained.
import equipment_documents as _docs
from equipment_documents import (
    DocumentError,             # noqa: F401  re-exported: one `except` for both
    DocumentRejected,
    DocumentStoreError,
    clean_uid,
    content_disposition,       # noqa: F401  re-exported for the route phase
    display_name,
    read_upload,               # noqa: F401  re-exported for the route phase
    resolve_content_type,
    storage_slug,
)

# The one place that decides what a LabCore answer means.
from labcore_result import LabCoreError, wrote_rows
from labcore_result import rows as labcore_rows

# `_docs` and not a `from` import for anything a test or an operator can MOVE.
# `MAX_DOCUMENT_BYTES` and `ACCEPTED_CONTENT_TYPES` are read through the module
# object at call time so there is exactly one live ceiling and one live
# accept-list in the process: binding them here would make this module keep
# enforcing the values that were in place at import, which is a limit that
# silently stops following the constant it is named after.

# Where the bytes live. Its OWN environment variable, and its own folder
# under LEM_DATA_DIR — never inside the equipment documents root, because that
# module's sweep rglobs its whole root and reports what it cannot account for
# as deletable. Same deployment warning as over there: on ASAPSV1 this must
# point somewhere outside `C:\ASAPApps\lem\current`, or the next deploy takes
# the lab's certificates with it.
STANDARD_DOCUMENTS_DIR_ENV = "LEM_STANDARD_DOCUMENTS_DIR"
CERTIFICATES_DIR_NAME = "standard-certificates"

# How far ahead "expiring soon" looks. Thirty days is a re-order and a delivery
# for a CRM, which is what the warning is FOR — a certificate that expires
# tomorrow with nothing on order is a finding either way.
#
# **Never a default argument.** Every window in this module defaults to `None`
# and resolves through `_window_days()`, which reads this name at CALL time. A
# default argument binds once, at `def` time, so `within_days=EXPIRY_WARNING_DAYS`
# in a signature keeps enforcing whatever the constant said at import forever —
# moving the constant would move nothing, in five places, silently. This is the
# same hazard the comment 25 lines above states about another module's
# constants, and this module had it in all five of its own signatures.
EXPIRY_WARNING_DAYS = 30

# REGISTERED CENTRALLY: `snapshot_service.SCHEMA_DDL` imports this constant, so
# the table is declared once at boot, after `existing_tables()` has been asked,
# and costs nothing on the restarts the tray does on every code edit. Never
# retyped there — a retyped copy drifts, and a drifted copy is a boot path
# declaring one set of columns while the store reads another.
#
# Two rules that come with the registration:
#   * this table is NOT an arm of the batched read, deliberately. See the
#     module docstring.
#   * any column added here AFTER this shipped needs an entry in
#     `SCHEMA_MIGRATIONS` and an `ALTER`, because `CREATE TABLE IF NOT EXISTS`
#     is a no-op on a table that already exists. That is exactly how adding
#     `correction` dropped the whole floor to the fallback path once.
#
# A NEW table, touching no existing one — `lem_qc_samples` gains nothing, so the
# station module on every bench is unaffected and this ships as a MINOR.
#
# `storage_key` is a stored column and the on-disk path is NOT. The path is
# recomputed from key + uid + resolved type every time, so moving the root or
# restoring a backup into a different folder needs no rewrite of LabCore, and
# there is no path column to outlive the code that wrote it. What the key buys
# is the one thing a recomputation cannot: a folder name that does not move when
# the standard is renamed.
STANDARD_DOCUMENTS_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_standard_documents ("
    "uid TEXT PRIMARY KEY, standard_name TEXT NOT NULL, storage_key TEXT NOT NULL, "
    "filename TEXT NOT NULL, size_bytes INTEGER NOT NULL, content_type TEXT, "
    "content_hash TEXT, issued_at TEXT, expires_at TEXT, "
    "uploaded_at TEXT, uploaded_by TEXT)"
)

_COLUMNS = ("uid, standard_name, storage_key, filename, size_bytes, "
            "content_type, content_hash, issued_at, expires_at, "
            "uploaded_at, uploaded_by")

# Which rows COULD need attention, as one SQL predicate. A deliberate SUPERSET
# of "expired or expiring": `certificate_status` gives the verdict, in Python,
# on whatever this returns. Written once because `expiring()` and
# `expiring_by_standard()` must select the same rows or the report and the
# per-standard answer drift apart again.
#
# Three arms, and the third is the one that was missing:
#   * `<= ?` — the ordinary case, decided by the lexicographic order that
#     matches the chronological one. This is what the stored format buys;
#   * `date(expires_at) IS NULL` — SQLite cannot read it, so `2026-13-45` is
#     caught even though it sorts after any sane horizon;
#   * `NOT GLOB` the canonical shape — `8/25/2026` sorts after EVERY ISO date
#     this century, and SQLite's `date()` is more forgiving than this module's
#     parser, so shape is checked as well as parseability.
#
# A row excluded by all three is canonical, a real date, and sorts after the
# horizon — exactly the row `certificate_status` calls VALID. So nothing that
# needs attention can hide behind this WHERE, and the read still scales with
# the rows that matter rather than with the size of the library.
_DUE_PREDICATE = (
    "expires_at IS NOT NULL AND expires_at <> '' AND ("
    "expires_at <= ? OR date(expires_at) IS NULL "
    "OR expires_at NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')")

# The statuses `certificate_status` answers. Named so a route and a template
# cannot disagree about the spelling of "expiring".
STATUS_NONE = "none"
STATUS_VALID = "valid"
STATUS_EXPIRING = "expiring"
STATUS_EXPIRED = "expired"


class CertificateRejected(DocumentRejected):
    """The upload or the date is wrong — a person's mistake, so a route says 400.

    A subclass of `equipment_documents.DocumentRejected` rather than a new
    hierarchy: the accept-list and the signature check are that module's and
    they raise its exception, so one `except DocumentRejected` has to keep
    covering both while a route that wants to answer differently about
    certificates can still catch this one.
    """


class CertificateStoreError(DocumentStoreError):
    """LabCore or the disk would not cooperate. Not the uploader's fault; 500."""


@contextmanager
def _as_certificate_error():
    """Re-label the imported primitives' verdicts as this store's own.

    `resolve_content_type` and the size ceiling raise `DocumentRejected`, which
    is correct and is also the exception a route mounting BOTH stores would have
    to disambiguate by reading the message. The cause is preserved, so nothing
    about why is lost.
    """
    try:
        yield
    except (CertificateRejected, CertificateStoreError):
        raise                          # already ours; do not wrap twice
    except DocumentRejected as exc:
        raise CertificateRejected(str(exc)) from exc
    except DocumentStoreError as exc:
        raise CertificateStoreError(str(exc)) from exc


def clean_name(value) -> str:
    """A standard's name as `lem_qc_samples` keys it.

    `QcSampleStore.save` strips the name before it becomes the primary key, so a
    certificate filed under `"Diesel - AO25 "` would hang off a standard the
    rest of the app does not have — invisible in the tab, invisible to the
    dedupe check, and counted as a second standard by every fleet-wide read.

    Stripped and NOT case-folded. `changeover` compares names exactly
    (`t.sample == old_name`) and the table's primary key is case-sensitive, so
    folding here would attach a certificate to a standard the rest of the app
    thinks is a different one.
    """
    return clean_uid(value)


def default_certificates_root(app_dir: Optional[str] = None) -> Path:
    """Where certificates live when nobody has said otherwise.

    Deliberately NOT `default_documents_root() / something`: see the module
    docstring on why the equipment sweep must never see this folder. The default
    is the one that works on a laptop and is wrong for the live deployment,
    where the code directory is a junction a deploy swaps wholesale.
    """
    configured = os.environ.get(STANDARD_DOCUMENTS_DIR_ENV, "").strip()
    if configured:
        return Path(os.path.abspath(configured))
    data = os.environ.get(_docs.DATA_DIR_ENV, "").strip()
    if data:
        return Path(os.path.abspath(data)) / CERTIFICATES_DIR_NAME
    base = app_dir or os.path.dirname(os.path.abspath(__file__))
    return Path(base) / "data" / CERTIFICATES_DIR_NAME


def _stored_day(text: str) -> Optional[date]:
    """The one spelling this module stores, parsed — or None.

    `date.fromisoformat` is NOT this: since 3.11 it also accepts the basic form
    (`20260901`), week dates (`2026-W36-2`) and ordinal dates (`2026-244`), none
    of which sort lexicographically the way the expiry query needs and none of
    which the refusal message offers. The stored format is `YYYY-MM-DD` and the
    parser has to agree with the sentence printed beside it.
    """
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        return None
    if not (text[:4].isdigit() and text[5:7].isdigit()
            and text[8:].isdigit()):
        return None
    try:
        return date(int(text[:4]), int(text[5:7]), int(text[8:]))
    except ValueError:
        return None


def _as_date(value) -> date:
    """The day to judge against: today, or the day the caller NAMED.

    A string used to fall through to `datetime.now()`, so
    `certificate_status("2020-01-01", today="2019-01-01")` answered `expired`
    about a certificate with a year left — self-consistently, and wrong, with
    no way for the caller to tell. A day this cannot read is refused instead:
    a silent substitution of today is the one answer nobody can detect.
    """
    if value is None:
        return datetime.now().date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    head = str(value).strip().replace(" ", "T").split("T", 1)[0]
    parsed = _stored_day(head)
    if parsed is None:
        raise CertificateRejected(
            f"{value!r} is not a day this can judge a certificate against. "
            f"Use YYYY-MM-DD, a date, or nothing at all for today.")
    return parsed


def _window_days(value) -> int:
    """How far ahead "expiring soon" looks, resolved at CALL time.

    `None` means the module constant — read through the module's own global so
    that moving `EXPIRY_WARNING_DAYS` moves every window in the process. See
    the comment on the constant for why no signature may default to it.

    A window that is not a number is REFUSED rather than allowed to throw
    `ValueError` out of `int()` at whatever is drawing the badge.
    """
    if value is None:
        value = EXPIRY_WARNING_DAYS
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        raise CertificateRejected(
            f"{value!r} is not a number of days. The expiry window is a count "
            f"of days ahead, and the default is EXPIRY_WARNING_DAYS.") from None


def _supplied(value) -> bool:
    """Did the caller actually give a date, or say nothing?

    **`""` means "not supplied", exactly like `None`.** An HTML form and a JSON
    body both send `""` for a box nobody typed in; neither can send `None`. The
    old guard was `if expires_at is not None`, so re-uploading a certificate
    without re-typing its date took the update branch and CLEARED the stored
    one — and a row with no expiry falls out of `expiring()` permanently, which
    is a certificate that silently stops being tracked.

    Clearing a date is a real thing to want, and it has its own verb:
    `set_expiry(uid, "")`. Deciding it deliberately here is the point — silence
    and erasure are different instructions and only one of them can be the
    default for a blank form field.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True


def _normalise_day(value, what: str) -> str:
    """One date field as it is stored: `YYYY-MM-DD`, or `""` for none.

    `what` names the field in the refusal. A bad issue date used to be refused
    with the expiry field's sentence, which sends somebody to correct a box
    they typed correctly.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return ""
    # An ISO datetime is what a form's `datetime-local` and a JSON payload both
    # send, and it names the right day; only the time is surplus.
    head = text.replace(" ", "T").split("T", 1)[0]
    parsed = _stored_day(head)
    if parsed is None:
        raise CertificateRejected(
            f"{text!r} is not a date this can track as {what}. Use "
            f"YYYY-MM-DD — a date stored in any other spelling is not the "
            f"format this library sorts and compares on.")
    return parsed.isoformat()


def normalise_expiry(value) -> str:
    """An expiry as it is stored: `YYYY-MM-DD`, or `""` for none.

    Refusing a date this cannot parse is the whole job. A free-text
    `8/25/2026` does not merely display oddly — the expiry query is a string
    comparison (that is what makes "everything expiring before X" one read
    rather than every row parsed in Python), and `8/25/2026` sorts AFTER every
    ISO date this century, so the horizon comparison alone would never reach
    it. `expiring()` casts a deliberately wider net for exactly that reason;
    this refusal is what keeps the net from having to be the whole table.

    A datetime is accepted and truncated: a validity period is a day.
    """
    return _normalise_day(value, "an expiry")


def certificate_status(expires_at, today=None, within_days=None) -> str:
    """`none` · `valid` · `expiring` · `expired`, from a stored expiry.

    A pure function, so the floor and a report and a test all answer the same
    question the same way without a read between them. **It is also the only
    verdict**: `expiring()` filters its rows through this rather than deciding
    for itself in SQL, so the library-wide report and one standard's tab cannot
    give different answers about the same stored value.

    `within_days=None` means `EXPIRY_WARNING_DAYS`, read at call time — never a
    default argument. See the comment on the constant.

    **Valid THROUGH the expiry date.** A certificate dated 2026-09-01 is good on
    2026-09-01 and a finding on the 2nd. Both directions of that boundary are
    wrong in a way somebody notices, so it is stated once, here, and pinned in
    the tests rather than re-derived per caller.
    """
    window = _window_days(within_days)
    now = _as_date(today)
    stored = str(expires_at or "").strip()
    if not stored:
        return STATUS_NONE
    when = _stored_day(stored)
    if when is None:
        # A row that predates the normalisation, or one written by hand. It is
        # NOT reported as valid: an unreadable date is an unknown date, and the
        # only safe unknown on a compliance report is the one that gets looked
        # at.
        return STATUS_EXPIRED
    if when < now:
        return STATUS_EXPIRED
    if when <= now + timedelta(days=window):
        return STATUS_EXPIRING
    return STATUS_VALID


def _horizon(today: date, window: int) -> str:
    """The last day the window reaches, as the stored format spells it."""
    return (today + timedelta(days=window)).isoformat()


def _sorted_due(certificates, today: date, window: int
                ) -> List["StandardCertificate"]:
    """The ones needing attention, soonest first, unreadable dates leading.

    THE filter as well as the sort: `certificate_status` decides, so nothing
    reaches a report that the certificate's own status would call valid, and
    nothing it calls expired is left out.

    A date nothing can read has no position on a timeline to be sorted into and
    is the one a person has to go and look at, so it leads rather than landing
    wherever its text happened to sort.
    """
    due = [c for c in certificates
           if certificate_status(c.expires_at, today, window)
           in (STATUS_EXPIRED, STATUS_EXPIRING)]
    return sorted(due, key=lambda c: (
        _stored_day(str(c.expires_at or "").strip()) is not None,
        str(c.expires_at or ""), c.standard_name, c.uid))


def covering_certificate(certificates, today=None
                         ) -> Optional[StandardCertificate]:
    """Which of these certificates a standard is resting on today, or None.

    ONE rule, in one place, because `current()` answers it about the standard
    in front of you and `expiry_report` answers it about the whole library — and
    a report that called a covered standard EXPIRED while the standard's own tab
    said it was covered is exactly the disagreement that made the report
    unreadable.

    Two parts to the judgement:

    * **an undated certificate still counts as cover.** Plenty of in-house
      standards carry no stated validity period, and "no expiry" is not the same
      fact as "expired" — reading it as expired would report every one of them
      as a finding;
    * **among DATED certificates the one valid longest wins, and a dated
      certificate beats an undated one.** A lab that scans last year's COA after
      this year's — which is what happens when somebody tidies a drawer — is
      still covered by this year's, so the upload order does not decide. A
      dated one wins over an undated one because an expiry is evidence about
      the cover and its absence is only silence; that does mean a certificate
      expiring tomorrow is preferred to an undated one, and the report says so
      by calling that standard EXPIRING rather than covered.
    """
    day = _as_date(today)
    covering = [c for c in certificates
                if certificate_status(c.expires_at, day) != STATUS_EXPIRED]
    if not covering:
        return None
    return max(covering,
               key=lambda c: (bool(c.expires_at), c.expires_at,
                              c.uploaded_at, c.uid))


@dataclass(frozen=True)
class StandardCertificate:
    """One certificate's metadata — the whole LabCore row.

    `standard_name` is the standard this certificate is FOR, and it moves when
    the standard is renamed. `storage_key` is the name it was FILED under, and
    it never moves — see the module docstring. Keeping both is what makes a
    rename one UPDATE and no file operations.
    """

    uid: str
    standard_name: str
    storage_key: str
    filename: str
    size_bytes: int
    content_type: str
    content_hash: str
    uploaded_at: str
    expires_at: str = ""
    issued_at: str = ""
    uploaded_by: str = ""

    def to_dict(self) -> dict:
        return {
            "uid": self.uid,
            "standard_name": self.standard_name,
            "storage_key": self.storage_key,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "content_type": self.content_type,
            "content_hash": self.content_hash,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "uploaded_at": self.uploaded_at,
            "uploaded_by": self.uploaded_by,
        }

    @classmethod
    def from_row(cls, row: dict) -> "StandardCertificate":
        try:
            size = int(row.get("size_bytes") or 0)
        except (TypeError, ValueError):
            size = 0
        name = str(row.get("standard_name") or "")
        return cls(
            uid=str(row.get("uid") or ""),
            standard_name=name,
            # A row written before `storage_key` existed, or one written by
            # hand, falls back to the name — which is where such a file would
            # have been put anyway. Defaulting to "" instead would point every
            # legacy certificate at one shared folder called `equipment`.
            storage_key=str(row.get("storage_key") or name),
            filename=str(row.get("filename") or ""),
            size_bytes=size,
            content_type=str(row.get("content_type") or ""),
            content_hash=str(row.get("content_hash") or ""),
            issued_at=str(row.get("issued_at") or ""),
            expires_at=str(row.get("expires_at") or ""),
            uploaded_at=str(row.get("uploaded_at") or ""),
            uploaded_by=str(row.get("uploaded_by") or ""),
        )

    def status(self, today=None, within_days=None) -> str:
        """`within_days=None` is the module constant, read at call time."""
        return certificate_status(self.expires_at, today, within_days)

    def days_until_expiry(self, today=None) -> Optional[int]:
        """Days left, negative once it is past. None when it has no expiry.

        Also None when the stored date is one this module cannot read — there
        is no honest number to give. `status()` still calls that EXPIRED, which
        is the answer that gets it looked at.
        """
        stored = str(self.expires_at or "").strip()
        if not stored:
            return None
        when = _stored_day(stored)
        if when is None:
            return None
        return (when - _as_date(today)).days


def _is_file(path) -> bool:
    """`path.is_file()`, but never raising on a path we cannot look at.

    `Path.is_file()` stats, and what it does when the stat FAILS depends on the
    interpreter: 3.14 swallows the OSError and answers False, 3.12 raises. Both
    sweeps below already treated an unreadable file as "leave it alone" — the
    guard was on the `_settled` call and not on the `is_file()` immediately
    beside it, so on 3.12 the sweep raised instead of reporting.

    It passed everywhere it was run because the newer interpreter hides it. The
    lab does not run the newer interpreter: the station module pins PySide6,
    which has no 3.14 wheels, so the version that breaks is the deployed one.
    """
    try:
        return path.is_file()
    except OSError:
        return False


class StandardCertificateStore:
    """Save, list, fetch, rename and delete the certificates of QC standards.

    The gateway carries only metadata; `root` carries the bytes. Both are
    injected, so the tests run against `FakeLabCoreGateway` and a `tmp_path` and
    never go near LabCore or the real certificates folder.
    """

    def __init__(self, gateway, root=None) -> None:
        self.gateway = gateway
        # Not created here. A store is constructed by the app factory, which
        # must stay free of side effects. The first save makes it.
        self.root = Path(root) if root is not None else default_certificates_root()

    # ── paths ───────────────────────────────────────────────────────────
    def folder_for(self, storage_key: str) -> Path:
        """The folder for one standard's certificates, by the key they were
        filed under — never by the standard's current name."""
        return self.root / storage_slug(storage_key)

    def path_for(self, cert: StandardCertificate) -> Path:
        """Recomputed from the row every time — see the module docstring.

        The containment check is the property this module actually promises —
        the result is under the root — asserted rather than trusted to the
        construction that gets it there. `storage_key` comes back out of
        LabCore, so it is a path component that arrives from a table; the slug
        makes it inert and this makes sure.
        """
        ext = _docs.ACCEPTED_CONTENT_TYPES.get(cert.content_type, ".bin")
        candidate = self.folder_for(cert.storage_key) / f"{cert.uid}{ext}"
        root = os.path.normpath(str(self.root))
        try:
            inside = os.path.commonpath(
                [root, os.path.normpath(str(candidate))])
        except ValueError:
            inside = ""             # different drives on Windows: not inside
        if inside != root:
            raise CertificateStoreError(
                f"Refusing a certificate path outside {self.root}: "
                f"{candidate}")
        return candidate

    def stored_path(self, cert: StandardCertificate) -> Path:
        """Where this certificate's bytes actually are.

        Not always where a new one would be written: `path_for` derives the
        extension from the shared `ACCEPTED_CONTENT_TYPES`, so editing one value
        there would otherwise repoint every already-stored file of that type.
        Canonical first, then the same uid under any other extension.
        """
        canonical = self.path_for(cert)         # also the containment gate
        if canonical.exists():
            return canonical
        siblings = self._stored_siblings(cert, canonical)
        return siblings[0] if siblings else canonical

    def _stored_siblings(self, cert: StandardCertificate,
                         canonical: Path) -> List[Path]:
        """Files that are this certificate under a different extension.

        **`p.stem == cert.uid` is the guard**, and it is an equality rather
        than a pattern. A uid read back out of LabCore is whatever is in the
        table, and a `*` in it would make the glob match the file NEXT to it —
        which `stored_path` would then serve and `delete` would then unlink. The
        containment check in `path_for` does not catch that one, because
        `<root>/<folder>/*.pdf` is genuinely under the root. No stem can equal
        `*`, or `..`, or anything else with a path separator in it, so the
        equality closes the hole for every such uid rather than for the ones a
        pattern happened to anticipate.

        This used to ALSO check `equipment_documents._SAFE_UID` first. That was
        dead code: everything the pattern rejects, the stem equality had already
        rejected, so it protected nothing while making the file look defended
        twice. The behaviour it claimed to hold is asserted directly instead —
        see `test_a_forged_uid_cannot_serve_the_file_next_to_it`.
        """
        try:
            return sorted(p for p in canonical.parent.glob(f"{cert.uid}.*")
                          if _is_file(p) and p.stem == cert.uid)
        except (OSError, ValueError):
            return []

    # ── save ────────────────────────────────────────────────────────────
    def save(self, standard_name: str, filename: str, data: bytes,
             uploaded_by: str = "", content_type: str = "",
             expires_at=None, issued_at=None,
             now: Optional[datetime] = None) -> StandardCertificate:
        """Store one certificate. Bytes first, then the row.

        Both orders leave the same survivable orphan — a file on disk that no
        row mentions, which nothing lists and `orphaned_files()` finds. The
        orphan this refuses to create is the other one: a row with no file, i.e.
        a certificate the tab lists and cannot produce when somebody clicks it
        during an assessment.
        """
        name = clean_name(standard_name)
        if not name:
            raise CertificateRejected(
                "A certificate has to belong to a QC standard; one filed "
                "against nothing can never be found again.")
        data = bytes(data or b"")
        if not data:
            # Zero bytes is a cancelled file picker or a failed read, never a
            # certificate. Listing an empty "COA" is the row-with-no-file
            # failure by another route.
            raise CertificateRejected("That file is empty; nothing was stored.")
        if len(data) > _docs.MAX_DOCUMENT_BYTES:
            raise CertificateRejected(
                f"That file is {len(data)} bytes; the limit is "
                f"{_docs.MAX_DOCUMENT_BYTES}.")

        with _as_certificate_error():
            resolved = resolve_content_type(filename, data, content_type)
        extension = _docs.ACCEPTED_CONTENT_TYPES[resolved]
        display = display_name(filename, extension)
        digest = hashlib.sha256(data).hexdigest()
        # The dates are validated BEFORE anything is written, so a mistyped
        # expiry costs a re-pick rather than a file on disk and a row to unwind.
        expiry = _normalise_day(expires_at, "an expiry")
        issued = _normalise_day(issued_at, "an issue date")

        existing = self._by_hash(name, digest)
        if existing is not None:
            # The same bytes on the same standard twice is a double-click or a
            # retry after an upload nobody saw finish — not two certificates.
            # Across two standards they stay separate rows: one certificate
            # really can cover two lots, and retiring one must not remove the
            # other's evidence.
            path = self.stored_path(existing)
            if not path.exists():
                self._write_bytes(self.path_for(existing), data)
            # Re-uploading the same PDF with a corrected date is exactly what
            # somebody does after typing one wrong. Answering "already stored"
            # and keeping the old value is a silent wrong answer about the two
            # fields the expiry report reads. BOTH dates are honoured — the
            # expiry used to be and the issue date used to be discarded.
            fixes = {}
            if _supplied(expires_at) and expiry != existing.expires_at:
                fixes["expires_at"] = expiry
            if _supplied(issued_at) and issued != existing.issued_at:
                fixes["issued_at"] = issued
            if fixes:
                return self.set_dates(existing.uid, **fixes)
            return existing

        cert = StandardCertificate(
            uid=uuid.uuid4().hex[:12],
            standard_name=name,
            # Written once, here, and never updated. This is what makes a rename
            # one UPDATE and no file operations.
            storage_key=name,
            filename=display,
            size_bytes=len(data),
            content_type=resolved,
            content_hash=digest,
            issued_at=issued,
            expires_at=expiry,
            uploaded_at=(now or datetime.now()).isoformat(),
            uploaded_by=str(uploaded_by or ""),
        )
        path = self.path_for(cert)
        self._write_bytes(path, data)
        try:
            self._insert(cert)
        except DocumentStoreError:
            # The bytes are down and no row will ever mention them. Take them
            # back so the survivable orphan stays hypothetical; if even this
            # fails, an unreferenced file is what is left, which is the one we
            # chose to be able to live with.
            try:
                path.unlink()
            except OSError:
                pass
            raise
        return cert

    def _write_bytes(self, path: Path, data: bytes) -> None:
        """Write via a temp file and rename, so no reader ever sees half a PDF.

        `os.replace` is atomic on both platforms. Without it a crash mid-write
        leaves a file of the right name and the wrong length, which opens as a
        corrupt certificate rather than a missing one — and a corrupt
        certificate is the one people argue about instead of noticing.

        Spelled out here rather than imported because the version in
        `equipment_documents` is a method on that store and this phase does not
        edit that file. The `.part` suffix IS shared, so both writers leave the
        same name behind and both sweeps recognise it.
        """
        temp = path.with_name(path.name + _docs.PART_SUFFIX)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(temp, "wb") as fh:
                fh.write(data)
            os.replace(temp, path)
        except OSError as exc:
            try:
                if temp.exists():
                    temp.unlink()
            except OSError:
                pass
            raise CertificateStoreError(
                f"Could not write the certificate to {path.parent}: {exc}"
            ) from exc

    # ── read ────────────────────────────────────────────────────────────
    def certificates(self, standard_name: str, *,
                     missing_ok: bool = True) -> List[StandardCertificate]:
        """This standard's certificates, newest first.

        Raises when LabCore could not be asked. An empty list from here means
        the standard has no certificate on file — a sentence somebody acts on by
        going to find one — and that is only true if the outage takes the other
        exit.
        """
        rows = self._rows(
            f"SELECT {_COLUMNS} FROM lem_standard_documents "
            "WHERE standard_name = ? ORDER BY uploaded_at DESC, uid DESC",
            [clean_name(standard_name)], "list this standard's certificates",
            missing_ok=missing_ok)
        return [StandardCertificate.from_row(row) for row in rows]

    def get(self, uid: str) -> Optional[StandardCertificate]:
        """One certificate's metadata, or None if there is no such certificate.

        None means "asked, and there is none". An outage raises, because every
        caller of this reads None as a fact about the uid.
        """
        rows = self._rows(
            f"SELECT {_COLUMNS} FROM lem_standard_documents WHERE uid = ?",
            [clean_uid(uid)], "look this certificate up")
        return StandardCertificate.from_row(rows[0]) if rows else None

    def fetch(self, uid: str) -> Tuple[StandardCertificate, bytes]:
        """Metadata and bytes together, which is what a download needs.

        A row whose file is gone raises rather than returning empty bytes: a
        zero-byte PDF handed to an assessor looks like our answer, not like a
        file that went missing.

        A file that IS there and is empty raises for the same reason. `save`
        refuses zero bytes outright, so zero on disk is always damage — a
        half-restored backup, an interrupted copy — and it must not be handed
        out as the lab's evidence.
        """
        cert = self.get(uid)
        if cert is None:
            raise CertificateStoreError(f"No certificate {uid!r}.")
        path = self.stored_path(cert)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise CertificateStoreError(
                f"{cert.filename} is listed but its file is missing from "
                f"{path.parent}.") from exc
        if not data:
            raise CertificateStoreError(
                f"{cert.filename} is listed but the file in {path.parent} is "
                f"empty. Nothing empty was ever stored, so this one is "
                f"damaged; it is not being handed out as a certificate.")
        return cert, data

    def current(self, standard_name: str,
                now=None) -> Optional[StandardCertificate]:
        """The certificate this standard is resting on today, or None.

        `expiring()` asks the finding of the whole library; this asks it of the
        standard in front of you, out of the same ONE read the tab already
        makes. None means "nothing in date on file" — a gap somebody acts on —
        so it is reached only through a read that succeeded; an outage raises.

        Which one, when there are several, is `covering_certificate`, and it is
        that function and not a rule spelled out again here. `expiry_report`
        reads the same function about the whole library, so the report cannot
        call a standard EXPIRED that this method says is covered.
        """
        return covering_certificate(self.certificates(standard_name), now)

    def expiring(self, now=None, within_days=None) -> List[StandardCertificate]:
        """Every certificate already expired or expiring within the window.

        **The verdict is `certificate_status`, never the SQL.** The SQL used to
        BE the verdict — `WHERE expires_at <= ?`, a string comparison — and a
        stored `8/25/2026` sorts AFTER every ISO date this century, so a
        certificate the standard's own tab called EXPIRED never appeared in the
        library-wide report. The same data, two answers, in the month of an
        assessment. Now the SQL selects a deliberate SUPERSET and every row is
        put through `certificate_status`, so the two cannot disagree in either
        direction.

        Still ONE read, and still not the whole table — which is the reason the
        stored format is `YYYY-MM-DD` and nothing else. The superset is:

        * anything at or before the horizon, the ordinary case, decided by the
          lexicographic order that matches the chronological one;
        * plus anything that is NOT a clean stored date — SQLite cannot parse
          it (`date(...) IS NULL`) or it is not the canonical shape. Those are
          the rows whose sort position says nothing, so they are pulled in
          wherever they sort and judged in Python.

        A row excluded by both is canonical, a real Gregorian date, and sorts
        after the horizon — which is precisely the row `certificate_status`
        calls VALID. Nothing that needs attention can hide behind the WHERE.

        `within_days=None` is `EXPIRY_WARNING_DAYS`, read at call time.

        **It does not degrade to empty.** A missing table raises here like
        everything else: on an unwired server "nothing is expiring" is the same
        sentence as "every certificate is in date", and it is the sentence this
        report exists to be able to deny.
        """
        window = _window_days(within_days)
        today = _as_date(now)
        rows = self._rows(
            f"SELECT {_COLUMNS} FROM lem_standard_documents WHERE "
            + _DUE_PREDICATE,
            [_horizon(today, window)],
            "check which certificates are expiring", missing_ok=False)
        return _sorted_due(
            (StandardCertificate.from_row(row) for row in rows),
            today, window)

    def expiring_by_standard(self, now=None, within_days=None
                             ) -> Dict[str, List[StandardCertificate]]:
        """Per standard that has something due: ALL of that standard's certs.

        Coverage is not answerable from the due rows alone — a standard with
        this year's COA on file and last year's still in the table has one due
        row and is perfectly covered — so the report needs the standard's whole
        set. This is that question as ONE statement: the outer select is
        filtered by an inner select of the standards with anything due, so the
        read scales with the standards that need attention rather than with the
        size of the library.

        `missing_ok=False`, same as `expiring()` and for the same reason.
        """
        window = _window_days(within_days)
        today = _as_date(now)
        rows = self._rows(
            f"SELECT {_COLUMNS} FROM lem_standard_documents "
            "WHERE standard_name IN ("
            "SELECT standard_name FROM lem_standard_documents WHERE "
            + _DUE_PREDICATE + ") "
            "ORDER BY standard_name ASC, uploaded_at DESC, uid DESC",
            [_horizon(today, window)],
            "check which standards have a certificate expiring",
            missing_ok=False)
        grouped: Dict[str, List[StandardCertificate]] = {}
        for row in rows:
            cert = StandardCertificate.from_row(row)
            grouped.setdefault(cert.standard_name, []).append(cert)
        return grouped

    def orphaned_files(self) -> List[str]:
        """Files under the root that no metadata row mentions.

        The sweep for the orphan the save/delete ordering deliberately allows.
        Read-only on purpose — it reports, and a person decides.

        `missing_ok=False`, and this is the one place the shared rule's honest
        degradation would be wrong: an absent table truthfully means "no
        certificates are recorded", but for a sweep that reads as "every file on
        disk is unaccounted for", and the only use anyone has for this list is
        deleting what is on it. A true statement is not a safe one when it is a
        delete list.
        """
        rows = self._rows(
            f"SELECT {_COLUMNS} FROM lem_standard_documents", None,
            "list the certificates to sweep against", missing_ok=False)
        if not self.root.exists():
            return []
        known: set = set()
        for row in rows:
            cert = StandardCertificate.from_row(row)
            try:
                canonical = self.path_for(cert)
            except DocumentStoreError:
                # A forged or corrupted uid. Skipping it keeps the sweep going:
                # letting one bad row abort the whole thing hides every real
                # orphan from the person doing the sweeping.
                continue
            known.add(str(canonical))
            known.update(str(p) for p in self._stored_siblings(cert, canonical))
        cutoff = time.time() - max(0, _docs.PART_FILE_GRACE_SECONDS)
        found = []
        for path in self.root.rglob("*"):
            if not _is_file(path) or str(path) in known:
                continue
            if path.name.endswith(_docs.PART_SUFFIX) and \
                    not self._settled(path, cutoff):
                continue        # a save in flight, not a leftover
            found.append(str(path))
        return sorted(found)

    @staticmethod
    def _settled(path: Path, cutoff: float) -> bool:
        """Is this file old enough that nothing is still writing it?

        An unreadable stat answers False — it reports nothing rather than
        inviting the deletion of a file it could not even look at.
        """
        try:
            return path.stat().st_mtime <= cutoff
        except OSError:
            return False

    def orphaned_certificates(
            self, standard_names) -> List[StandardCertificate]:
        """Certificates whose standard is no longer in the QC library.

        This is where a rename's casualties turn up. The application has no
        rename verb — see `rename_standard` — so a renamed standard is a NEW
        row under the new name and a DELETE of the old one, and every
        certificate is left naming a standard that no longer exists.
        `certificates(new_name)` answers `[]`, `get(uid)` still works if you
        happen to know the uid, and nothing else in this module would ever list
        them again. This lists them, and `repoint_certificates` repairs them.

        `standard_names` is the QC library's current names, passed IN. This
        module does not know `qc_samples` exists and must not learn: the
        dependency runs one way, so a caller that already holds the standards
        hands them over rather than making this store read a table it has no
        business reading.

        **Pass a list that came back from a read that SUCCEEDED.**
        `QcSampleStore.list_samples()` degrades to `[]` when its table is
        missing, and this cannot tell that apart from a lab with no standards —
        handed it, every certificate on file reads as orphaned. `None` is
        refused outright because it is the shape a caller that never really
        asked hands over; an empty collection is taken at its word.

        ONE read, `missing_ok=False`. "Nothing is orphaned" during a blip is
        the sentence this exists to be able to deny.
        """
        if standard_names is None:
            raise CertificateStoreError(
                "Which QC standards exist is not a question this store can "
                "answer, and None is not an answer to it. Hand in the names "
                "from a read that succeeded.")
        live = {clean_name(n) for n in standard_names}
        rows = self._rows(
            f"SELECT {_COLUMNS} FROM lem_standard_documents "
            "ORDER BY standard_name ASC, uploaded_at DESC, uid DESC", None,
            "list the certificates whose standard has gone",
            missing_ok=False)
        return [cert for cert in (StandardCertificate.from_row(r) for r in rows)
                if clean_name(cert.standard_name) not in live]

    # ── repointing and expiry ───────────────────────────────────────────
    def repoint_certificates(self, from_name: str, to_name: str, *,
                             merge: bool = False) -> int:
        """Move one standard's certificates onto another name. Returns how many.

        The repair for a rename, and the only verb in this module that can
        perform one. ONE statement, and no byte moves: the on-disk path hangs
        off `storage_key`, written once at save time and never updated, so
        re-pointing is a single `UPDATE ... SET standard_name`. A per-row loop
        would be N writes at ~1.5 ops/sec and a refusal halfway would leave the
        set split across two names, half invisible under each; one UPDATE
        cannot half-happen.

        The cosmetic cost, accepted: the files stay in the folder named for the
        name they were filed under. A person standing in that folder still
        reads a real name, every row still says which folder its file is in,
        and nothing has to be migrated. Moving the bytes instead is a
        multi-file operation across two systems with no transaction between
        them — it can half-complete, and a half-completed one leaves
        certificates in two folders with the rows pointing at neither.

        `merge=False` refuses when the destination already has certificates of
        its own: two standards' evidence merged under one name cannot be
        unmerged, because `storage_key` says which folder each file is in and
        nothing says which standard each certificate was the evidence FOR.
        `merge=True` is the opt-in for the case a person can see and this store
        cannot — the two names are the same lot, and somebody uploaded the COA
        again under the new one before anyone noticed the old ones.

        The reads on the way in are `missing_ok=False`: they decide a write, so
        a missing table is the news rather than "that standard has no
        certificates", and a blip must not be answered as a quiet zero to a
        caller that is about to report the repair as done.
        """
        old = clean_name(from_name)
        new = clean_name(to_name)
        if not new:
            raise CertificateRejected(
                "A standard needs a name; certificates filed against an empty "
                "one can never be found again.")
        if not old or old == new:
            # Nothing was re-filed, which is the honest count. Returning early
            # also keeps a form that submits an unchanged name from tripping the
            # collision check below and reporting a conflict with itself.
            return 0
        moving = self.certificates(old, missing_ok=False)
        if not moving:
            return 0
        if not merge and self.certificates(new, missing_ok=False):
            raise CertificateRejected(
                f"{new!r} already has certificates of its own. Merging two "
                f"standards' evidence into one name cannot be undone. Pass "
                f"merge=True only if you know these are the same lot.")
        self._run(
            "UPDATE lem_standard_documents SET standard_name = ? "
            "WHERE standard_name = ?", [new, old],
            "re-file this standard's certificates")
        return len(moving)

    def rename_standard(self, old_name: str, new_name: str) -> int:
        """The re-filing primitive, under the name a rename verb would use.

        **There is no rename verb in this application, and this method has no
        caller.** `QcSampleStore` has `save` (upsert-by-name), `delete`,
        `list_samples`, `as_payload` and `by_lab_id` — and nothing that renames.
        A standard is renamed by save-new-then-delete-old, and three places say
        so in those words: `templates/floor.html`, `templates/stations.html`
        and `web_app.py`'s `/api/qc-samples` handler.

        What that means for certificates, plainly: **a rename does not reach
        this table at all.** The certificates keep naming the old standard,
        which no longer exists. `certificates(new_name)` answers `[]` while the
        PDF sits on disk under a row naming a standard nobody can select any
        more. `orphaned_certificates()` is what finds them and
        `repoint_certificates()` is what repairs them — this method is the same
        single UPDATE under the other name, kept because a real rename verb may
        arrive later and this is the correct primitive for it to call.

        Do not read this method as the module's answer to renaming. It is not
        wired to anything, and nothing calls it on a rename.
        """
        return self.repoint_certificates(old_name, new_name)

    def set_expiry(self, uid: str, expires_at) -> StandardCertificate:
        """Correct one certificate's expiry without re-uploading it.

        A 5 MB PDF re-sent over HTTP because somebody typed 2027 for 2026 is a
        re-pick, a re-upload and a second copy of identical bytes to dedupe; the
        date is one column.

        **This is also the verb that CLEARS an expiry.** `set_expiry(uid, "")`
        means "this certificate has no stated validity period", said on
        purpose. A blank date arriving through `save` means silence instead —
        see `_supplied` for why the two cannot be the same instruction.
        """
        return self.set_dates(uid, expires_at=expires_at)

    def set_dates(self, uid: str, **fields) -> StandardCertificate:
        """Correct `expires_at` and/or `issued_at` in ONE write.

        Both are named because both are corrected in the same breath by the
        person who mistyped one of them, and two UPDATEs is two slots in a queue
        that serialises at ~1.5 ops/sec in front of every QC verdict the floor
        is writing.

        Only the fields NAMED are touched. A field left out is not cleared —
        this method never guesses that silence about a column means the column
        should be emptied.
        """
        allowed = {"expires_at": "an expiry", "issued_at": "an issue date"}
        unknown = set(fields) - set(allowed)
        if unknown:
            raise CertificateRejected(
                f"{sorted(unknown)} are not date fields of a certificate.")
        if not fields:
            raise CertificateRejected("Nothing was named to correct.")
        cert = self.get(uid)
        if cert is None:
            raise CertificateStoreError(f"No certificate {uid!r}.")
        # Validated before the write, so a mistyped correction costs a re-type
        # rather than a half-applied row.
        values = {col: _normalise_day(fields[col], allowed[col])
                  for col in fields}
        columns = sorted(values)
        self._run(
            "UPDATE lem_standard_documents SET "
            + ", ".join(f"{col} = ?" for col in columns)
            + " WHERE uid = ?",
            [values[col] for col in columns] + [cert.uid],
            "record the certificate's dates")
        return StandardCertificate(**{**cert.to_dict(), **values})

    # ── delete ──────────────────────────────────────────────────────────
    def delete(self, uid: str) -> bool:
        """Row first, then the file. False when there was nothing to delete.

        `False` is a fact about the uid, not a shrug: it is reached only through
        a read that succeeded. During an outage this raises instead, because
        "there was nothing to delete" said about a certificate that is still in
        LabCore and still on disk is a lie the caller cannot detect.
        """
        cert = self.get(uid)
        if cert is None:
            return False
        self._run("DELETE FROM lem_standard_documents WHERE uid = ?",
                  [cert.uid], "delete the certificate record")
        try:
            self.stored_path(cert).unlink()
        except (OSError, DocumentError):
            # Nothing references it any more, so the list is already correct. An
            # unreferenced file is the orphan this module is willing to have.
            pass
        return True

    def delete_for_standard(self, standard_name: str, *,
                            retired: bool) -> int:
        """Destroy one standard's whole certificate set. Returns how many.

        **Do NOT wire this into `DELETE /api/qc-samples`.** This application
        renames a standard by save-new-then-delete-old — `templates/floor.html`,
        `templates/stations.html` and `web_app.py` all say so — so a rename
        ENDS in that route. Hooked there, renaming a standard would destroy
        the lab's certificates: the rows and the PDFs, in one click, in the year
        of a PJLA assessment. CLAUDE.md's own precedent points straight at the
        mistake ("Retiring a machine now also forgets its level and its
        documents"), and a machine uid is un-renameable while a standard's name
        is a human string somebody retypes.

        What to do in that route instead: **nothing.** Leave the certificates
        where they are. They become orphans, `orphaned_certificates()` lists
        them, and `repoint_certificates()` puts them back on the live name in
        one statement without moving a byte. An orphan is recoverable; an
        unlinked PDF is not.

        `retired` is required and keyword-only, and it is the whole guard: this
        store cannot tell a retirement from the back half of a rename and only
        the caller can, so the caller has to say. Pass `retired=True` ONLY when
        the lot is gone for good and is not reappearing under another name.
        Anything else is refused rather than deleted — a route wired here by
        copy-paste fails loudly at the first click instead of quietly taking the
        evidence with it.

        One `DELETE ... WHERE standard_name` rather than `delete()` in a loop:
        the queue serialises at ~1.5 ops/sec in front of every QC verdict the
        floor is writing, and a lot with a certificate per year would hold that
        up for seconds.

        The unlinks walk `storage_key`, not the current name, so a standard that
        was renamed at some point still takes ALL of its files with it — the
        older ones live in the folder named for the name they were filed under.
        Deleting only the current name's folder would leave those bytes for the
        sweep to report.
        """
        if retired is not True:
            raise CertificateRejected(
                "Refusing to destroy this standard's certificates: nothing "
                "has asserted the lot is gone for good. A rename in this "
                "application is save-new-then-delete-old, so a delete is also "
                "the back half of a rename — use repoint_certificates() to "
                "move the evidence onto the new name, or pass retired=True if "
                "the lot really is retired.")
        name = clean_name(standard_name)
        if not name:
            return 0
        certs = self.certificates(name)
        if not certs:
            return 0
        self._run("DELETE FROM lem_standard_documents WHERE standard_name = ?",
                  [name], "delete the certificates for this standard")
        folders = []
        for cert in certs:
            try:
                self.stored_path(cert).unlink()
            except (OSError, DocumentError):
                pass
            # `folder_for` is a slug of a string; it raises nothing. The
            # containment check that CAN raise is in `path_for`, and the unlink
            # above catches it.
            folder = self.folder_for(cert.storage_key)
            if folder not in folders:
                folders.append(folder)
        for folder in folders:
            try:
                # Only ever succeeds when the folder is empty, so a stray file
                # left by a half-restored backup keeps its folder rather than
                # being silently swept with the standard.
                folder.rmdir()
            except OSError:
                pass
        return len(certs)

    # ── gateway plumbing ────────────────────────────────────────────────
    def _by_hash(self, standard_name: str,
                 digest: str) -> Optional[StandardCertificate]:
        rows = self._rows(
            f"SELECT {_COLUMNS} FROM lem_standard_documents "
            "WHERE standard_name = ? AND content_hash = ? LIMIT 1",
            [clean_name(standard_name), digest],
            "check whether this certificate is already stored",
            # The dedupe read is the first half of a write, so the table's
            # absence is the news and not an empty answer. Swallowing it would
            # mean creating the folder, writing the bytes, failing the INSERT on
            # the same missing table and unlinking them again — work done to
            # learn what the read already knew.
            missing_ok=False)
        return StandardCertificate.from_row(rows[0]) if rows else None

    def _rows(self, sql: str, args: Optional[list] = None,
              what: str = "read the certificates",
              missing_ok: bool = True) -> List[dict]:
        """Every read, and it must tell the two failures apart.

        The judgement is `labcore_result.rows`, not this method; what is left
        here is the translation into this module's exception, with the shared
        cause preserved so a route that wants to say "try again in a moment" can
        still tell a blip from a refusal.

        An absent table really does mean there is nothing, so it reads as `[]`.
        Everything else raises: "could not ask" answered as "no certificate on
        file" is a sentence somebody acts on, and the action is going to look
        for a document that is already there.
        """
        try:
            res = self.gateway.read_sql(sql, args or [])
        except Exception as exc:
            raise CertificateStoreError(
                f"LabCore could not {what}: {exc}") from exc
        try:
            found = labcore_rows(res, missing_ok=missing_ok)
        except LabCoreError as exc:
            raise CertificateStoreError(
                f"LabCore could not {what}: {exc}") from exc
        return [r for r in found if isinstance(r, dict)]

    def _insert(self, cert: StandardCertificate) -> None:
        self._run(
            f"INSERT INTO lem_standard_documents ({_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [cert.uid, cert.standard_name, cert.storage_key, cert.filename,
             cert.size_bytes, cert.content_type, cert.content_hash,
             cert.issued_at, cert.expires_at, cert.uploaded_at,
             cert.uploaded_by],
            "record the certificate in LabCore")

    def _run(self, sql: str, args: list, what: str) -> int:
        """One write, and it counts only if LabCore says it happened.

        The absence of an "error" key is not an acknowledgement: the queue
        refuses past ~100 pending by ANSWERING, and a gateway that has stopped
        answering returns `None`. Reading either as success is how a store hands
        back a certificate with bytes on disk and zero rows in LabCore.

        The count is returned rather than checked, because "did it happen" and
        "did it match anything" are different questions: a DELETE that matched
        nothing DID run, and an operator's second click must not raise.
        """
        try:
            res = self.gateway.sql(sql, args)
        except Exception as exc:
            raise CertificateStoreError(
                f"LabCore could not {what}: {exc}") from exc
        try:
            return wrote_rows(res)
        except LabCoreError as exc:
            raise CertificateStoreError(
                f"LabCore could not {what}: {exc}") from exc


def expiry_report(store: StandardCertificateStore, now=None,
                  within_days=None) -> Dict[str, object]:
    """What is out of date and what is about to be, for the whole library.

    **The unit of a finding is a STANDARD, not a certificate.** This used to be
    per-certificate and never asked what the standard was actually resting on,
    so every superseded COA the lab had ever replaced stayed on the expired
    list for good: a standard with this year's certificate on file read as a
    finding because last year's was still in the table. An expiry report that
    lists standards which are fully covered is a report nobody finishes
    reading, which is the same as no report.

    So the cover is `covering_certificate` — the same function `current()`
    answers with, so the report and one standard's tab cannot disagree — and
    the four answers are:

    * `expired` — nothing on file covers this standard. The finding;
    * `expiring` — the certificate the standard IS resting on runs out inside
      the window. One row per standard: the cover, not every certificate;
    * `superseded` — a certificate that is out of date or running out on a
      standard something else already covers. Kept, because "which one lapsed"
      is a real question at an assessment, and reported apart because it is not
      a finding;
    * `covered` — the names of the standards that had something due and are
      covered anyway.

    ONE LabCore read, split in Python — see `expiring_by_standard`. Coverage
    needs the whole set of the standards that have something due, which is one
    statement with an inner select, not one read per standard.

    It RAISES rather than reporting a clean bill of health it cannot vouch for.
    `equipment_documents.document_counts_by_machine` is the one read in this
    pair of modules allowed to degrade, and the reasons given there are that it
    is a count, on a polled page that already carries an OFFLINE banner, that
    nobody produces during an audit. This is a list, on a page nobody polls,
    whose entire purpose is being produced during an audit.
    """
    window = _window_days(within_days)
    today = _as_date(now)
    by_standard = store.expiring_by_standard(now=today, within_days=window)

    expired: List[StandardCertificate] = []
    expiring: List[StandardCertificate] = []
    superseded: List[StandardCertificate] = []
    covered: List[str] = []
    for name, certificates in by_standard.items():
        cover = covering_certificate(certificates, today)
        due = _sorted_due(certificates, today, window)
        if cover is None:
            # Nothing on file covers this standard. Every one of its lapsed
            # certificates is part of the same single finding.
            expired.extend(due)
            continue
        covered.append(name)
        if certificate_status(cover.expires_at, today, window) == STATUS_EXPIRING:
            expiring.append(cover)
        superseded.extend(c for c in due if c.uid != cover.uid)

    return {
        "as_of": today.isoformat(),
        "within_days": window,
        "expired": _sorted_due(expired, today, window),
        "expiring": _sorted_due(expiring, today, window),
        "superseded": _sorted_due(superseded, today, window),
        "covered": sorted(covered),
    }
