#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
equipment_documents.py — the PDFs that belong to a piece of equipment.

Ryan: "I want a documents tab as well per equipment for adding documents like
PDF's that have data on them."

Calibration certificates, manufacturer manuals, the signed sheet from the vendor
who last serviced the analyser. Storage only — save, list, fetch, delete. No
Flask routes live here; a later phase mounts them.

## Bytes on disk, metadata in LabCore

This is the one decision the whole module is shaped around, and it is not
symmetry for its own sake:

LabCore is an HTTP **write queue** in front of SQLite. It serialises at roughly
1.5 writes/sec and refuses outright past 100 pending, returning an error dict
rather than raising. A 5 MB PDF is ~6.7 MB of base64 inside a single SQL
statement — one certificate upload would sit in that queue ahead of every QC
verdict, every result and every heartbeat the floor is trying to write, for as
long as it takes to push. Nothing that big goes through it. So the file lands on
the server's own disk and LabCore holds one small row describing it: 1 write,
the same cost as ticking a checklist.

## The path is derived, never taken from the client

A document's on-disk location is computed from its **uid** and its **resolved
content type**, and from nothing a person typed:

    <root>/<slug(machine_uid)>-<hash(machine_uid)>/<uid>.pdf

The uploaded filename is kept as *metadata only*, for the download's
Content-Disposition. It is never a path component, so `../../web_app.py` is not
a thing this module has to defend against with a check that a later refactor can
drop — there is simply no route from the name to the path. The same holds for
`machine_uid`: it is the wire contract every bench keys its rows on, it arrives
over HTTP, and it is folded through the same slug+hash before it becomes a
folder. The hash suffix is there because slugging alone collides — `bay/1` and
`bay:1` both flatten to `bay_1`, and two instruments sharing one folder is a
delete that takes the wrong unit's certificate with it.

Nothing stores the path. It is recomputed from the row every time, so moving the
root, or restoring from a backup into a different folder, needs no rewrite of
LabCore — and a path column is a traversal vector that outlives the code that
wrote it.

## Which orphan is survivable

A save is two writes to two systems and there is no transaction across them, so
one of them can land alone. That is not a maybe; it is what a full write queue or
a full disk does. So the order is chosen deliberately:

**Save writes the bytes first, then the metadata row. Delete removes the
metadata row first, then the bytes.**

Both orders leave the same survivable orphan: **a file on disk that no row
mentions**. Nothing lists it, nothing links to it, it costs some disk and
`orphaned_files()` finds it when someone wants to sweep. The orphan we refuse to
create is the other one — **a row with no file**: the documents tab shows a
calibration certificate, someone clicks it during an audit, and it 404s. A
document that is listed and cannot be produced is worse than one that was never
listed, because the list is what people trust.

The failure paths still tidy up (a failed metadata write unlinks the bytes it
just wrote), so the orphan is what is left when even the tidying fails — not the
normal outcome.

## What LabCore's answer means is decided in ONE place

`labcore_result`, and not here. This module used to make both halves of that
judgement privately — a regex of its own for reads, and "the answer has no
'error' key, so the write worked" — and it got the write half wrong in the
direction that loses work silently. Three modules invented three versions of the
same rule in one week, so it now lives once, is tested once, and is imported.

**Reads** (`_rows` → `labcore_result.rows`) still distinguish a **missing table**,
which honestly means there is nothing, from **LabCore not answering**, which
means nothing at all. They were the same empty list once, and three callers
turned that into a confident wrong answer during a blip lasting one second:
`delete` reported "there was nothing to delete" about a document still in LabCore
and still on disk, `fetch` named a live uid as unknown, and `save` skipped its
dedupe and wrote a second row and a second copy of identical bytes that nothing
ever collapses. That is the rule `snapshot_service.SnapshotReadError` states for
the machine list — "Reporting 'no machines' when the truth is 'could not ask' is
how a whole lab reads as empty during a LabCore blip" — and the sharper version
of this module's own argument above: the list is what people trust.

The one exemption is `document_counts_by_machine`, the floor's tab badge, and it
is a named method (`_rows_or_empty`) rather than a swallowed exception, so it has
to be asked for. See its docstring for why a count on a page that already carries
an OFFLINE banner is allowed to degrade where the tab is not.

**Writes** (`_run` → `labcore_result.wrote_rows`) count only when the answer says
the write happened. The absence of an "error" key is not an acknowledgement:
LabCore's queue refuses past 100 pending by ANSWERING, in whatever shape it feels
like — `{"ok": False, "status": "rejected", "pending": 100}` carries no "error"
at all — and a gateway that has stopped answering returns `None`. Reading any of
those as success broke both promises made above: `save()` handed back a document
with bytes on disk and zero rows in LabCore, so the tab never listed it, and
`delete()` returned True and unlinked the file while the row survived — the
row-with-no-file orphan this module exists to refuse.

## Whitespace is not part of anything

`machine_uid` arrives over HTTP and arrives with whatever was around it, so every
entry point folds it through `clean_uid` — not just `save()`, which is how a
document filed as `"m1 "` became invisible to `documents("m1")` and, worse, to
the dedupe check. Filenames are stripped before their extension is read, because
`os.path.splitext("cert.pdf ")` is `".pdf "` and a real certificate was being
refused as "not a kind of document LEM stores".
"""

from __future__ import annotations

import hashlib
import os
import re
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# The one place that decides what a LabCore answer means. This module used to
# decide it twice on its own — a private missing-table regex for reads, and
# "there is no 'error' key, so it worked" for writes — and the write half was
# wrong in the direction that loses data silently. `is_missing_table` is
# re-exported rather than redefined so that the name callers already use keeps
# working while there is only ONE implementation of the rule in the codebase.
from labcore_result import (
    LabCoreError,
    is_missing_table,          # noqa: F401  re-exported: see above
    wrote_rows,
)
from labcore_result import rows as labcore_rows

# REGISTERED CENTRALLY, and saying which way round it is, is the point of this
# comment.
#
# `snapshot_service.SCHEMA_DDL` imports this constant — `equipment_documents.
# DOCUMENTS_DDL`, imported and never retyped — so the table is declared once at
# boot, after `existing_tables()` has been asked, and costs nothing on the
# restarts the tray does on every code edit.
#
# It said the opposite until 2026-08-25, and had said the opposite while
# claiming otherwise before that: the table was in no central tuple, so on a
# running server it did not exist, every read earned the missing-table empty
# list, and an unwired deployment looked on screen exactly like an instrument
# with no documents. `test_the_ddl_is_registered_or_the_module_says_it_is_not`
# holds the claim to the fact in BOTH directions, so this paragraph cannot go
# stale again in either.
#
# Two rules that came with the registration and outlive it:
#   * `_ARMS` is deliberately untouched. Documents are per-equipment and read on
#     demand; every arm of the batched read shares ONE statement, so an extra
#     arm buys a tab badge with the whole floor's read. The fleet-wide badge is
#     `document_counts_by_machine` — one `COUNT(*) … GROUP BY`, on a page
#     nobody polls.
#   * any column added here AFTER this shipped needs an entry in
#     `SCHEMA_MIGRATIONS` and an `ALTER`, because `CREATE TABLE IF NOT EXISTS`
#     is a no-op on a table that already exists. That is exactly how adding
#     `correction` dropped the whole floor to the fallback path once.
#
# This store still declares nothing on demand. A table created by whoever
# touches it first is how a column ends up missing from a shared statement.
#
# A NEW table, touching no existing one: the station module on every bench reads
# the `lem_*` tables, so a new or renamed column on a shared table is a MAJOR
# release that has to move every bench with it. This ships as a MINOR.
DOCUMENTS_DDL = (
    "CREATE TABLE IF NOT EXISTS lem_equipment_documents ("
    "uid TEXT PRIMARY KEY, machine_uid TEXT NOT NULL, filename TEXT NOT NULL, "
    "size_bytes INTEGER NOT NULL, content_type TEXT, content_hash TEXT, "
    "uploaded_at TEXT, uploaded_by TEXT)"
)

# Where the bytes live. `LEM_DOCUMENTS_DIR` first, then the server's existing
# `LEM_DATA_DIR` (tray.data_dir already reads it), then `data/documents` beside
# the code.
#
# The default is deliberately the one that works on a laptop and is deliberately
# WRONG for the live deployment, where the code directory is a junction onto an
# immutable release that a deploy swaps wholesale — see `risks`. `data/` is also
# excluded from the release archive and the CI verify step fails the build if it
# leaks in, which keeps uploads out of releases but does not move them out of
# harm's way. On ASAPSV1 `LEM_DOCUMENTS_DIR` must point somewhere outside
# `C:\ASAPApps\lem\current`, or the next deploy takes the certificates with it.
DOCUMENTS_DIR_ENV = "LEM_DOCUMENTS_DIR"
DATA_DIR_ENV = "LEM_DATA_DIR"

# 25 MB. The queue is not the constraint here — the bytes never touch it — so
# this is about the two places a large upload actually hurts: the whole file is
# held in memory by Flask and again by this module while it is hashed and
# written, and the lab's server is a Windows box that also runs the floor. A
# scanned 40-page certificate is comfortably under 10 MB; anything past 25 is an
# instrument dump or a wrong-file mistake, and refusing it costs a re-pick.
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024

# How much of an upload is read at a time by `read_upload`, which is the only
# place the ceiling can actually bound memory: `save()` takes bytes, so by the
# time it measures them the whole file is already in this process. Reading in
# chunks and stopping at the limit means an oversized POST costs one chunk over
# the ceiling instead of however large it was.
UPLOAD_CHUNK_BYTES = 256 * 1024

# An accept-list, never a deny-list: a deny-list is a promise to have thought of
# every extension, and that promise is always wrong. These are the three things a
# person can open in a browser tab to read a number off — the PDF Ryan asked for,
# plus a photo of a nameplate or a chart recorder trace, which is what people
# actually take when there is no PDF.
#
# The value is the canonical extension the stored file gets.
ACCEPTED_CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}

EXTENSION_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

# The extension is a claim and the browser's Content-Type is barely better — on
# Windows it comes out of the registry and a box with no PDF reader reports
# `application/octet-stream` for a perfectly good certificate. The leading bytes
# are the only part of an upload that cannot be renamed, so they are the gate:
# `virus.exe` renamed `cert.pdf` is refused here and nowhere else.
CONTENT_SIGNATURES = {
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
}

# The suffix `_write_bytes` renames from, and the age past which one of them is
# rubbish rather than a save in flight.
#
# A sweep runs while the lab is using the lab. A 20 MB scan being uploaded right
# now exists, briefly, under exactly the name a crashed write leaves behind, and
# `orphaned_files()` promises a list a person can act on — so naming an upload in
# progress hands them a live file to delete. Age is the only thing that separates
# the two: a write in flight is seconds old, a crashed one is however long ago the
# process died. Five minutes is far past any write this module can make (the
# ceiling is 25 MB) and far short of noticing a crash from last week.
PART_SUFFIX = ".part"
PART_FILE_GRACE_SECONDS = 300


_UNSAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9_-]+")
# A uid that is safe to hand to `Path.glob`. Uids this module mints are hex, but
# a row read back from LabCore is whatever is in the table, and `*` inside a glob
# pattern would match a NEIGHBOUR's file — which `stored_path` would then serve
# and `delete` would then unlink. The containment check in `path_for` does not
# catch it, because `<root>/<folder>/*.pdf` is genuinely under the root.
_SAFE_UID = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")
# Quotes and control characters go, because the display name is handed straight
# back out in a `Content-Disposition: attachment; filename="…"` header, and a
# name carrying a quote or a CRLF is a header injection wearing a filename.
_UNSAFE_NAME_CHARS = re.compile(r'[\x00-\x1f\x7f"\\/]+')
_SLUG_MAX = 40


class DocumentError(Exception):
    """Base for everything this module raises."""


class DocumentRejected(DocumentError):
    """The upload itself is wrong — too big, empty, or not an accepted type.

    Separate from DocumentStoreError on purpose: this one is a person's mistake
    and a route should answer 400 and say what to do about it. The other is ours.
    """


class DocumentStoreError(DocumentError):
    """LabCore or the disk would not cooperate. Not the uploader's fault; 500."""


def clean_uid(value) -> str:
    """A uid as it is keyed, wherever it entered from.

    `machine_uid` is the wire contract: it arrives in a form field, a query
    string or a JSON body, and it arrives with whatever whitespace was around it.
    `save()` stripped it and the reads did not, so a document filed by a form
    that sent `"m1 "` was stored under `m1` and then invisible to
    `documents("m1 ")` — and invisible to the dedupe check, which is how one
    certificate ends up in the tab twice with nothing that ever collapses them.
    One function, used by every entry point, so the two halves cannot drift
    apart again.
    """
    return str(value or "").strip()


def default_documents_root(app_dir: Optional[str] = None) -> Path:
    """Where documents live when nobody has said otherwise."""
    configured = os.environ.get(DOCUMENTS_DIR_ENV, "").strip()
    if configured:
        return Path(os.path.abspath(configured))
    data = os.environ.get(DATA_DIR_ENV, "").strip()
    if data:
        return Path(os.path.abspath(data)) / "documents"
    base = app_dir or os.path.dirname(os.path.abspath(__file__))
    return Path(base) / "data" / "documents"


@dataclass(frozen=True)
class EquipmentDocument:
    """One document's metadata — the whole LabCore row.

    `machine_uid` and not `equipment_uid`: it is the wire contract every bench
    writes its `lem_*` rows on and POSTs to `/api/live` with, LabCore has no
    foreign keys, and renaming it would not error — it would silently orphan
    every row forever. The machine→equipment rename is display-facing and is a
    later phase.
    """

    uid: str
    machine_uid: str
    filename: str
    size_bytes: int
    content_type: str
    content_hash: str
    uploaded_at: str
    uploaded_by: str = ""

    def to_dict(self) -> dict:
        return {
            "uid": self.uid,
            "machine_uid": self.machine_uid,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "content_type": self.content_type,
            "content_hash": self.content_hash,
            "uploaded_at": self.uploaded_at,
            "uploaded_by": self.uploaded_by,
        }

    @classmethod
    def from_row(cls, row: dict) -> "EquipmentDocument":
        try:
            size = int(row.get("size_bytes") or 0)
        except (TypeError, ValueError):
            size = 0
        return cls(
            uid=str(row.get("uid") or ""),
            machine_uid=str(row.get("machine_uid") or ""),
            filename=str(row.get("filename") or ""),
            size_bytes=size,
            content_type=str(row.get("content_type") or ""),
            content_hash=str(row.get("content_hash") or ""),
            uploaded_at=str(row.get("uploaded_at") or ""),
            uploaded_by=str(row.get("uploaded_by") or ""),
        )


def storage_slug(machine_uid: str) -> str:
    """The folder name for one instrument: readable, unique, inert.

    Readable so that someone standing in the folder can tell whose certificates
    these are. Unique because slugging alone collides (`bay/1` and `bay:1` both
    flatten to `bay_1`) and a shared folder makes one unit's delete take the
    other's document with it. Inert because the surviving characters are
    `[A-Za-z0-9_-]` only — dots included in that ban, so a `machine_uid` of
    `../..` becomes underscores rather than a way out of the root.
    """
    # Cleaned once, and the DIGEST is taken from the cleaned value too. The
    # readable half used to be stripped while the sha1 was taken from the raw
    # string, so `" m1 "` and `"m1"` — the same instrument, one of them straight
    # out of a form field — named two different folders, and the one a route
    # asked about was always the empty one.
    uid = clean_uid(machine_uid)
    cleaned = _UNSAFE_PATH_CHARS.sub("_", uid)[:_SLUG_MAX]
    cleaned = cleaned.strip("_-") or "equipment"
    digest = hashlib.sha1(uid.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned}-{digest}"


def display_name(filename: str, extension: str) -> str:
    """The name a browser will save it as. Metadata, never a path component.

    Both separators are flattened before the basename is taken, because this
    server runs on Windows and reads uploads from anywhere: `..\\..\\web_app.py`
    has no `/` in it and `os.path.basename` on POSIX would hand the whole string
    back unchanged.
    """
    raw = str(filename or "").replace("\\", "/")
    base = raw.rsplit("/", 1)[-1]
    base = _UNSAFE_NAME_CHARS.sub("", base).strip().strip(".").strip()
    if not base:
        base = "document"
    # An extension that names the SAME type is kept as the person spelled it.
    # `.jpeg` and `.jpg` are one content type and only the on-disk name has to be
    # canonical; comparing the two spellings as strings stored `plate.jpeg` as
    # `plate.jpeg.jpg`, so the download arrived looking like the server mangled
    # it. Only a name that says something else — or nothing — gains the canonical
    # extension.
    current = os.path.splitext(base)[1].lower()
    same_type = (current in EXTENSION_TYPES
                 and EXTENSION_TYPES[current] == EXTENSION_TYPES.get(extension))
    if current != extension and not same_type:
        base += extension
    return base


def content_disposition(filename: str, disposition: str = "attachment") -> str:
    """The download header, in the one form a WSGI server can actually send.

    WSGI headers are **latin-1**. `Prüfzertifikat.pdf` is a perfectly ordinary
    certificate here and encoding it into a plain `filename="…"` raises inside
    the server on the way out — a 500 on the download of a document that stored
    without a complaint. RFC 6266 is the fix and it is two fields: an ASCII
    `filename` old clients understand, and `filename*=UTF-8''…` percent-encoded,
    which every current browser prefers. Both are pure ASCII on the wire, so the
    header is latin-1 safe whatever the name is.

    The name is re-sanitised here rather than trusted. `display_name` already
    strips quotes and control characters, but this function is what a route
    hands to `make_response`, and a header builder that assumes its input was
    cleaned somewhere else is one refactor from being a header injection.
    """
    name = _UNSAFE_NAME_CHARS.sub("", str(filename or "")).strip() or "document"
    ascii_name = "".join(c if 32 <= ord(c) < 127 else "_" for c in name)
    quoted = urllib.parse.quote(name, safe="")
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}"


def _human_bytes(count: int) -> str:
    """Sizes in the units the person picking the file thinks in."""
    if count >= 1048576:
        mb = count / 1048576
        return f"{mb:.0f} MB" if abs(mb - round(mb)) < 0.05 else f"{mb:.1f} MB"
    if count >= 1024:
        return f"{count / 1024:.0f} KB"
    return f"{count} bytes"


def read_upload(stream, limit: Optional[int] = None) -> bytes:
    """Read an upload, refusing an oversized one before it is all in memory.

    `save()` takes bytes and checks the ceiling after the fact, which bounds what
    gets *stored* but not the memory the ceiling is justified by — the file is
    already in this process, and in Flask's buffer besides. This is the bounded
    door: it stops one chunk past the limit and never accumulates the rest, so a
    400 MB mis-drop costs 25 MB instead of 400 on a Windows box that is also
    drawing the floor.

    It does not make this module the only defence. A route must still set
    `MAX_CONTENT_LENGTH`, which is the only thing that stops the bytes before
    they reach Python at all; this bounds what happens after that.
    """
    cap = MAX_DOCUMENT_BYTES if limit is None else int(limit)
    read = getattr(stream, "read", None)
    if read is None:
        raise DocumentRejected("That upload could not be read; nothing was stored.")
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = read(UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > cap:
            raise DocumentRejected(
                f"That file is larger than the {_human_bytes(cap)} limit; "
                f"nothing was stored.")
        chunks.append(chunk)
    return b"".join(chunks)


def resolve_content_type(filename: str, data: bytes,
                         declared: str = "") -> str:
    """Decide what this file is, or refuse it.

    Order matters, and the two rules are deliberately not symmetric:

    * **A name that has an extension is judged on it.** `payload.py` is refused
      even when the upload declares `application/pdf`, because the declared type
      is the half a caller controls most cheaply and the half a browser gets
      wrong most often. Letting it override a named extension would leave the
      signature check as the only thing between the folder and `.py`.
    * **A name with no extension at all falls back to the declared type** — that
      is a scanner or a fax gateway handing over `scan`, which is real.

    Then the **signature has the last word**, so a name and a Content-Type that
    agree with each other and disagree with the bytes still get nowhere.
    """
    # Stripped first. `os.path.splitext("cert.pdf ")` is `".pdf "`, which is in
    # no table here, so a real certificate whose name picked up a trailing space —
    # a copied name, a drag out of Explorer, a form field — was refused with a
    # message saying it is not a kind of document LEM stores. There is nothing an
    # operator can do with that sentence, because it is not true.
    ext = os.path.splitext(
        str(filename or "").replace("\\", "/").strip())[1].lower()
    stated = str(declared or "").split(";", 1)[0].strip().lower()
    if ext:
        content_type = EXTENSION_TYPES.get(ext, "")
    else:
        content_type = stated if stated in ACCEPTED_CONTENT_TYPES else ""
    if not content_type:
        accepted = ", ".join(sorted(ACCEPTED_CONTENT_TYPES.values()))
        raise DocumentRejected(
            f"{filename or 'That file'} is not a kind of document LEM stores. "
            f"Accepted: {accepted}.")
    if not data.startswith(CONTENT_SIGNATURES[content_type]):
        raise DocumentRejected(
            f"{filename or 'That file'} is named like a "
            f"{ACCEPTED_CONTENT_TYPES[content_type]} but its contents are not "
            f"one. Nothing was stored.")
    return content_type


class EquipmentDocumentStore:
    """Save, list, fetch and delete the documents attached to one instrument.

    The gateway carries only metadata; `root` carries the bytes. Both are
    injected, so the tests run against `FakeLabCoreGateway` and a `tmp_path` and
    never go near LabCore or the real documents folder.
    """

    def __init__(self, gateway, root=None) -> None:
        self.gateway = gateway
        # Not created here. A store is constructed by the app factory, which must
        # stay free of side effects — spawning a directory at import time is the
        # same class of mistake as the snapshot poller that gave every test a
        # background thread. The first save makes it.
        self.root = Path(root) if root is not None else default_documents_root()

    # ── paths ───────────────────────────────────────────────────────────
    def folder_for(self, machine_uid: str) -> Path:
        return self.root / storage_slug(machine_uid)

    def path_for(self, doc: EquipmentDocument) -> Path:
        """Recomputed from the row every time — see the module docstring.

        The containment check is belt and braces, and it is here because of what
        a mutation run showed: with the uid put back into the path by mistake,
        every escape test still passed, because `display_name` was independently
        flattening the name. Two gates that each look sufficient are how a
        refactor removes one of them and nothing goes red. This one asserts the
        property the module actually promises — the result is under the root —
        rather than trusting the construction that gets it there.
        """
        ext = ACCEPTED_CONTENT_TYPES.get(doc.content_type, ".bin")
        candidate = self.folder_for(doc.machine_uid) / f"{doc.uid}{ext}"
        root = os.path.normpath(str(self.root))
        try:
            inside = os.path.commonpath([root, os.path.normpath(str(candidate))])
        except ValueError:
            inside = ""             # different drives on Windows: not inside
        if inside != root:
            raise DocumentStoreError(
                f"Refusing a document path outside {self.root}: {candidate}")
        return candidate

    def stored_path(self, doc: EquipmentDocument) -> Path:
        """Where this document's bytes actually are.

        Not always where a new one would be written. `path_for` derives the extension from `ACCEPTED_CONTENT_TYPES`, so
        editing one value in that table — `.jpg` to `.jpeg`, the most natural
        tidy-up in this file — silently repointed every already-stored file of
        that type: the tab still listed them, `fetch` raised "listed but its file
        is missing", `delete` removed the row and left the bytes, and
        `orphaned_files()` then reported the LIVE files as the ones to sweep.
        Renaming a constant is not supposed to be a data migration.

        So a lookup does not trust the constant. The canonical path is tried
        first — it is right for every file this version wrote — and only if
        nothing is there does it look for the same uid under any other
        extension. Writes still go to the canonical path, so nothing new is
        created under an old spelling.
        """
        canonical = self.path_for(doc)      # also the containment gate
        if canonical.exists():
            return canonical
        for sibling in self._stored_siblings(doc, canonical):
            return sibling
        return canonical

    def _stored_siblings(self, doc: EquipmentDocument,
                         canonical: Path) -> List[Path]:
        """Files that are this document under a different extension.

        `stem == uid` and not just the glob, so `<uid>.pdf.part` — a crashed
        write, not the document — is left out and stays findable as the orphan
        it is.
        """
        if not _SAFE_UID.match(str(doc.uid or "")):
            return []      # see _SAFE_UID: a `*` here would match the neighbour
        try:
            return sorted(p for p in canonical.parent.glob(f"{doc.uid}.*")
                          if p.is_file() and p.stem == doc.uid)
        except OSError:
            return []

    # ── save ────────────────────────────────────────────────────────────
    def save(self, machine_uid: str, filename: str, data: bytes,
             uploaded_by: str = "", content_type: str = "",
             now: Optional[datetime] = None) -> EquipmentDocument:
        """Store one document. Bytes first, then the row — see the docstring."""
        machine_uid = clean_uid(machine_uid)
        if not machine_uid:
            raise DocumentRejected(
                "A document has to belong to a piece of equipment; one filed "
                "against nothing can never be found again.")
        data = bytes(data or b"")
        if not data:
            # Zero bytes is a cancelled file picker or a failed read, never a
            # document. Listing an empty "certificate" is the row-with-no-file
            # failure by another route.
            raise DocumentRejected("That file is empty; nothing was stored.")
        if len(data) > MAX_DOCUMENT_BYTES:
            raise DocumentRejected(
                f"That file is {_human_bytes(len(data))}; the limit is "
                f"{_human_bytes(MAX_DOCUMENT_BYTES)}.")

        resolved = resolve_content_type(filename, data, content_type)
        extension = ACCEPTED_CONTENT_TYPES[resolved]
        name = display_name(filename, extension)
        digest = hashlib.sha256(data).hexdigest()

        existing = self._by_hash(machine_uid, digest)
        if existing is not None:
            # The same bytes on the same instrument twice is a double-click or a
            # retry after an upload nobody saw finish — not two documents. Two
            # byte-identical PDFs in the tab are indistinguishable to the person
            # reading it, so the second save is idempotent instead. Across two
            # instruments they stay separate rows: one certificate really can
            # cover two units, and deleting it from one must not remove it from
            # the other.
            path = self.stored_path(existing)
            if not path.exists():
                # Self-heal a swept-away file. `stored_path` first, so a re-upload
                # after ACCEPTED_CONTENT_TYPES changed heals the file that is
                # there instead of writing a second copy beside it.
                self._write_bytes(self.path_for(existing), data)
            return existing

        doc = EquipmentDocument(
            uid=uuid.uuid4().hex[:12],
            machine_uid=machine_uid,
            filename=name,
            size_bytes=len(data),
            content_type=resolved,
            content_hash=digest,
            uploaded_at=(now or datetime.now()).isoformat(),
            uploaded_by=str(uploaded_by or ""),
        )
        path = self.path_for(doc)
        self._write_bytes(path, data)
        try:
            self._insert(doc)
        except DocumentStoreError:
            # The bytes are already down and no row will ever mention them. Take
            # them back so the survivable orphan stays hypothetical; if even this
            # fails, an unreferenced file is what is left, which is the one we
            # chose to be able to live with.
            try:
                path.unlink()
            except OSError:
                pass
            raise
        return doc

    def _write_bytes(self, path: Path, data: bytes) -> None:
        """Write via a temp file and rename, so no reader ever sees half a PDF.

        `os.replace` is atomic on both platforms. Without it a crash mid-write
        leaves a file of the right name and the wrong length, which opens as a
        corrupt document rather than as a missing one — and a corrupt certificate
        is the failure people argue about instead of noticing.
        """
        temp = path.with_name(path.name + PART_SUFFIX)
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
            raise DocumentStoreError(
                f"Could not write the document to {path.parent}: {exc}") from exc

    # ── read ────────────────────────────────────────────────────────────
    def documents(self, machine_uid: str) -> List[EquipmentDocument]:
        """This instrument's documents, newest first.

        Raises when LabCore could not be asked. An empty list from here means the
        instrument has no documents, and a caller is entitled to render that
        sentence — which is only true if the outage takes the other exit.
        """
        rows = self._rows(
            "SELECT uid, machine_uid, filename, size_bytes, content_type, "
            "content_hash, uploaded_at, uploaded_by FROM lem_equipment_documents "
            "WHERE machine_uid = ? ORDER BY uploaded_at DESC, uid DESC",
            [clean_uid(machine_uid)], "list this equipment's documents")
        return [EquipmentDocument.from_row(row) for row in rows]

    def get(self, uid: str) -> Optional[EquipmentDocument]:
        """One document's metadata, or None if there is no such document.

        None means "asked, and there is none". An outage raises, because every
        caller of this reads None as a fact about the uid.
        """
        rows = self._rows(
            "SELECT uid, machine_uid, filename, size_bytes, content_type, "
            "content_hash, uploaded_at, uploaded_by FROM lem_equipment_documents "
            "WHERE uid = ?", [clean_uid(uid)], "look this document up")
        return EquipmentDocument.from_row(rows[0]) if rows else None

    def fetch(self, uid: str) -> Tuple[EquipmentDocument, bytes]:
        """Metadata and bytes together, which is what a download needs.

        A row whose file is gone raises rather than returning empty bytes: the
        orphan we designed against still has to be *reported* if it ever happens,
        because a zero-byte PDF handed to an auditor looks like our answer.
        """
        doc = self.get(uid)
        if doc is None:
            raise DocumentStoreError(f"No document {uid!r}.")
        path = self.stored_path(doc)
        try:
            return doc, path.read_bytes()
        except OSError as exc:
            raise DocumentStoreError(
                f"{doc.filename} is listed but its file is missing from "
                f"{path.parent}.") from exc

    def orphaned_files(self) -> List[str]:
        """Files under the root that no metadata row mentions.

        The sweep for the orphan the save/delete ordering deliberately allows.
        Read-only on purpose — it reports, and a person decides. Deleting
        unrecognised files automatically is how a half-restored backup erases
        itself.

        The read comes first even when the root does not exist, and it raises on
        an outage rather than reporting a clean sweep. Every path this cannot
        account for is a path the person reading the report will consider
        deleting, so a list built from rows nobody could read would name exactly
        the files to keep. One queue op on an operation someone runs by hand is
        the cheap half of that trade.

        A `.part` file younger than `PART_FILE_GRACE_SECONDS` is left out for the
        same reason: it is not an orphan, it is a save happening right now. The
        sweep runs while the lab is using the lab, and `_write_bytes` renames
        from exactly the name a crashed write leaves behind.
        """
        # `missing_ok=False` is the whole point, and it is the one place in this
        # module where the shared rule's honest degradation would be wrong. An
        # absent table truthfully means "no documents are recorded" — but for a
        # sweep that reads as "every file on disk is unaccounted for", and the
        # only use anyone has for this list is deleting what is on it. A true
        # statement is not a safe one when it is a delete list.
        rows = self._rows(
            "SELECT uid, machine_uid, filename, size_bytes, content_type, "
            "content_hash, uploaded_at, uploaded_by FROM lem_equipment_documents",
            None, "list the documents to sweep against", missing_ok=False)
        if not self.root.exists():
            return []
        known: set = set()
        for row in rows:
            doc = EquipmentDocument.from_row(row)
            try:
                canonical = self.path_for(doc)
            except DocumentStoreError:
                # A forged or corrupted uid. Skipping it keeps the sweep going:
                # letting one bad row abort the whole thing hides every real
                # orphan from the person doing the sweeping, which is the
                # opposite of what a report is for. The row is not lost — it is a
                # row whose file cannot be located, and `fetch` says exactly that
                # the moment anyone asks for it.
                continue
            known.add(str(canonical))
            # Every spelling of this uid counts as known, so a change to
            # ACCEPTED_CONTENT_TYPES cannot turn live files into orphans.
            known.update(str(p) for p in self._stored_siblings(doc, canonical))
        cutoff = time.time() - max(0, PART_FILE_GRACE_SECONDS)
        found = []
        for path in self.root.rglob("*"):
            if not path.is_file() or str(path) in known:
                continue
            if path.name.endswith(PART_SUFFIX) and not self._settled(path, cutoff):
                continue        # a save in flight, not a leftover
            found.append(str(path))
        return sorted(found)

    @staticmethod
    def _settled(path: Path, cutoff: float) -> bool:
        """Is this file old enough that nothing is still writing it?

        An unreadable stat answers False — it reports nothing rather than
        inviting the deletion of a file it could not even look at, which is the
        same direction every other judgement in this function leans.
        """
        try:
            return path.stat().st_mtime <= cutoff
        except OSError:
            return False

    # ── delete ──────────────────────────────────────────────────────────
    def delete(self, uid: str) -> bool:
        """Row first, then the file. False when there was nothing to delete.

        `False` is a fact about the uid, not a shrug: it is reached only through
        a read that succeeded. During an outage this raises instead, because
        "there was nothing to delete" said about a document that is still in
        LabCore and still on disk is a lie the caller cannot detect.
        """
        doc = self.get(uid)
        if doc is None:
            return False
        self._run("DELETE FROM lem_equipment_documents WHERE uid = ?",
                  [doc.uid], "delete the document record")
        try:
            self.stored_path(doc).unlink()
        except (OSError, DocumentError):
            # Nothing references it any more, so the tab is already correct. An
            # unreferenced file is the orphan this module is willing to have;
            # `orphaned_files()` finds it. `DocumentError` too, because a forged
            # uid makes `stored_path` refuse — and by here the row is already
            # gone, so raising would report a delete that did happen as failed
            # and invite the operator to do it again.
            pass
        return True

    def delete_for_machine(self, machine_uid: str) -> int:
        """Retire one instrument's whole document set. Returns how many.

        `delete()` in a loop is one queue write per document, and the queue
        serialises at ~1.5 ops/sec in front of every QC verdict and heartbeat the
        floor is writing — a unit with a dozen certificates would hold that up
        for the better part of ten seconds. One `DELETE ... WHERE machine_uid`
        costs the same as one.

        The row-first order is unchanged and matters more here, not less: if the
        unlinks fail halfway the leftovers are files nothing mentions, which is
        the orphan this module chose to be able to live with.
        """
        machine_uid = clean_uid(machine_uid)
        if not machine_uid:
            return 0
        docs = self.documents(machine_uid)
        if not docs:
            return 0
        self._run("DELETE FROM lem_equipment_documents WHERE machine_uid = ?",
                  [machine_uid], "delete the documents for this equipment")
        for doc in docs:
            try:
                self.stored_path(doc).unlink()
            except (OSError, DocumentError):
                pass
        try:
            # Only ever succeeds when the folder is empty, so a stray file left
            # by a half-restored backup keeps its folder rather than being
            # silently swept with the instrument.
            self.folder_for(machine_uid).rmdir()
        except OSError:
            pass
        return len(docs)

    # ── gateway plumbing ────────────────────────────────────────────────
    def _by_hash(self, machine_uid: str,
                 digest: str) -> Optional[EquipmentDocument]:
        rows = self._rows(
            "SELECT uid, machine_uid, filename, size_bytes, content_type, "
            "content_hash, uploaded_at, uploaded_by FROM lem_equipment_documents "
            "WHERE machine_uid = ? AND content_hash = ? LIMIT 1",
            [clean_uid(machine_uid), digest],
            "check whether this document is already stored",
            # The dedupe read is the first half of a write, so the table's
            # absence is the news and not an empty answer. Swallowing it here
            # meant an unwired server created the folder, wrote the bytes, failed
            # the INSERT on the same missing table and unlinked them again — work
            # done to learn what the read already knew, and a confusing error
            # naming the INSERT instead of the schema.
            missing_ok=False)
        return EquipmentDocument.from_row(rows[0]) if rows else None

    def _rows(self, sql: str, args: Optional[list] = None,
              what: str = "read the documents",
              missing_ok: bool = True) -> List[dict]:
        """Every read, and it must tell the two failures apart.

        The judgement itself is `labcore_result.rows`, not this method. It used
        to be made here, in a private copy of a rule three modules each invented
        separately and two got wrong; what is left here is the translation into
        this module's own exception, with the shared cause preserved so a route
        that wants to say "try again in a moment" can still tell a blip from a
        refusal.

        The rule: an absent table really does mean there is nothing, so it reads
        as `[]`. Everything else raises, because "could not ask" answered as
        "there is nothing" is what made `delete` report nothing to delete about a
        document still on disk, `fetch` name a live uid as unknown, and `save`
        skip dedupe and write a permanent second copy.

        `missing_ok=False` is for the read on the way to a write, where the
        table's absence is the news rather than an empty answer.
        """
        try:
            res = self.gateway.read_sql(sql, args or [])
        except Exception as exc:
            raise DocumentStoreError(
                f"LabCore could not {what}: {exc}") from exc
        try:
            found = labcore_rows(res, missing_ok=missing_ok)
        except LabCoreError as exc:
            raise DocumentStoreError(f"LabCore could not {what}: {exc}") from exc
        return [r for r in found if isinstance(r, dict)]

    def _rows_or_empty(self, sql: str, args: Optional[list] = None) -> List[dict]:
        """The read that is allowed to answer "nothing" when it does not know.

        Exactly one caller uses it — `document_counts_by_machine`, the floor's
        badge — and it is a separate, named method rather than a bare `except`
        inside `_rows` so that the exemption has to be asked for. The badge
        decorates a page that already carries its own staleness and OFFLINE
        banner, it is a count rather than a list, and nobody produces it during an
        audit. The tab is the opposite on all three counts, so the tab raises.
        """
        try:
            return self._rows(sql, args)
        except DocumentStoreError:
            return []

    def _insert(self, doc: EquipmentDocument) -> None:
        self._run(
            "INSERT INTO lem_equipment_documents (uid, machine_uid, filename, "
            "size_bytes, content_type, content_hash, uploaded_at, uploaded_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [doc.uid, doc.machine_uid, doc.filename, doc.size_bytes,
             doc.content_type, doc.content_hash, doc.uploaded_at,
             doc.uploaded_by],
            "record the document in LabCore")

    def _run(self, sql: str, args: list, what: str) -> int:
        """One write, and it counts only if LabCore says it happened.

        This method used to decide success from the ABSENCE of an "error" key,
        which is not the same question. The real client returns `resp.json()`
        verbatim from LabCore's queue, and that queue refuses past 100 pending by
        ANSWERING — in whatever shape it felt like, including
        `{"ok": False, "status": "rejected", "pending": 100}`, which carries no
        "error" key at all. A gateway that has stopped answering returns `None`,
        and a half-open connection returns `{}`. All three read as success, and
        both of this module's promises broke on them: `save()` handed back a
        document with bytes on disk and zero rows in LabCore, so the tab never
        listed it; `delete()` returned True and unlinked the file while the row
        survived, which is the row-with-no-file orphan the module docstring says
        it refuses to create.

        `labcore_result.wrote_rows` states it positively — a write happened only
        if the answer says so — and hands back the count, which is a different
        question from "did it happen": a DELETE that matched nothing DID run, and
        callers here deliberately do not treat that as a failure (an operator's
        second click must not raise). The count is returned rather than checked
        so that no caller has to re-derive it from a shape again.
        """
        try:
            res = self.gateway.sql(sql, args)
        except Exception as exc:
            raise DocumentStoreError(f"LabCore could not {what}: {exc}") from exc
        try:
            return wrote_rows(res)
        except LabCoreError as exc:
            raise DocumentStoreError(f"LabCore could not {what}: {exc}") from exc


def document_counts_by_machine(store: EquipmentDocumentStore,
                              machine_uids) -> Dict[str, int]:
    """How many documents each instrument has, for a tab badge, in ONE read.

    Per-instrument `documents()` in a loop is one queue op per instrument on a
    page that draws every instrument — the exact pattern the snapshot service
    exists to stop. Instruments with none are present as `0`, so a caller never
    has to distinguish "none" from "not asked".

    **This is the one read here allowed to answer "nothing" when it does not
    know** (`_rows_or_empty`), and it is a deliberate exemption rather than a
    swallowed exception: the badge decorates a floor that already carries its own
    staleness and OFFLINE banner, it is a count rather than a list, and nobody
    produces it during an audit. `documents()` is the opposite on all three
    counts and raises.

    It returns counts because that is what the exemption was granted for. The
    previous version called itself a count, took the licence, and then returned
    every instrument's full document list — which is not only a bigger claim
    resting on a degraded read, it shipped a floor of sixty instruments the
    metadata of all five hundred of their documents in order to draw sixty
    numbers. `COUNT(*) ... GROUP BY` sends sixty integers. Whoever needs the list
    calls `documents()`, which raises rather than degrading, because a list IS
    the thing people trust.
    """
    wanted: List[str] = []
    for uid in (machine_uids or []):
        clean = clean_uid(uid)
        # Deduped: two spellings of one instrument would otherwise cost a
        # placeholder each and collide in the result anyway.
        if clean and clean not in wanted:
            wanted.append(clean)
    out: Dict[str, int] = {uid: 0 for uid in wanted}
    if not wanted:
        return out
    marks = ", ".join(["?"] * len(wanted))
    counted = store._rows_or_empty(
        "SELECT machine_uid, COUNT(*) AS document_count "
        "FROM lem_equipment_documents "
        f"WHERE machine_uid IN ({marks}) GROUP BY machine_uid", wanted)
    for row in counted:
        uid = clean_uid(row.get("machine_uid"))
        if uid not in out:
            continue
        try:
            out[uid] = int(row.get("document_count") or 0)
        except (TypeError, ValueError):
            out[uid] = 0
    return out
