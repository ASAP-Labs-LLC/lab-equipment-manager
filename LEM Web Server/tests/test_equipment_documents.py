"""Per-equipment documents — PDFs on disk, metadata in LabCore.

Ryan: "I want a documents tab as well per equipment for adding documents like
PDF's that have data on them."

The split is the whole design: **bytes on the server's disk, one metadata row in
LabCore**. A 5 MB PDF is ~6.7 MB of base64 inside a single SQL statement, and
LabCore's queue serialises at roughly 1.5 writes/sec and refuses past 100
pending — so one certificate upload would sit in front of every QC verdict and
every result the floor is trying to write. Nothing that big goes through the
queue.

What is pinned down here is not "does it save a file". It is the places this kind
of code actually goes wrong:

* **no file content ever reaches the queue** — the claim above, and for a long
  time the only load-bearing one nothing tested. The suite passed against an
  implementation that put the whole PDF through LabCore as a bound parameter,
  because the recording gateway kept the statements and threw the arguments
  away. `assert_no_document_bytes_reached_the_queue` is the test that would have
  failed, and `test_the_guard_bites_when_the_bytes_do_go_through` proves it can;
* **the path is derived, never taken from the client** — a filename arriving as
  `../../web_app.py` must be unable to reach `web_app.py`;
* **duplicates and name collisions** — the same PDF twice, and two different
  PDFs both called `cert.pdf`;
* **half-completed saves** — the row landed and the disk write did not, and the
  reverse. One of those orphans is survivable and the other is a lie during an
  audit; the store must always leave the survivable one;
* **the limits are named and enforced** — a size ceiling and an accept-list,
  both constants with a stated reason rather than a magic number in a branch;
* **"could not ask" is never answered as "there is nothing"** — a LabCore blip
  and an empty table are different facts, and a store that returns `[]` for both
  says "no such document" about one that is still on disk, and stores a second
  copy of a PDF it failed to look up;
* **a write is not done until LabCore says it is** — `None`, `{}` and the
  queue's own refusal shape all carry no "error" key, and all three mean the row
  was never written. Believing them let `save()` report a document LabCore never
  heard of and `delete()` unlink a file whose row survived.

Both of the last two are one question — "what did LabCore actually tell me?" —
and the answer now lives in `labcore_result`, tested there, imported here.
"""
import base64
import io
import os
import time
import urllib.parse
from datetime import datetime

import pytest

from labcore_gateway import FakeLabCoreGateway

import equipment_documents
import labcore_result
import snapshot_service
from equipment_documents import (
    ACCEPTED_CONTENT_TYPES,
    DOCUMENTS_DDL,
    DOCUMENTS_DIR_ENV,
    DATA_DIR_ENV,
    MAX_DOCUMENT_BYTES,
    PART_FILE_GRACE_SECONDS,
    UPLOAD_CHUNK_BYTES,
    DocumentRejected,
    DocumentStoreError,
    EquipmentDocument,
    EquipmentDocumentStore,
    content_disposition,
    default_documents_root,
    document_counts_by_machine,
    read_upload,
)
from labcore_result import LabCoreRefused, LabCoreUnavailable


# ── fixtures ────────────────────────────────────────────────────────────────

PDF = b"%PDF-1.7\n" + b"x" * 200 + b"\n%%EOF\n"
OTHER_PDF = b"%PDF-1.4\n" + b"y" * 300 + b"\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"z" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"w" * 64

# A certificate the size real ones actually are. The small PDF above cannot
# prove the module's central claim: 200 bytes fits in a SQL statement without
# anyone noticing, and the whole design exists because 300 KB does not.
BIG_PDF = b"%PDF-1.7\n" + b"Q" * 300_000 + b"\n%%EOF\n"


@pytest.fixture
def gw():
    """A LabCore fake with the documents table already declared.

    The store deliberately does NOT declare its own schema — DDL belongs in
    snapshot_service's central tuple, applied once at boot, because a table
    created lazily by whoever touches it first is how a column ends up missing
    from the batched read. Applying the module's own DDL constant here is
    exactly what that boot does, and nothing more.
    """
    g = FakeLabCoreGateway()
    g.sql(DOCUMENTS_DDL)
    return g


@pytest.fixture
def store(gw, tmp_path):
    """Every test writes under tmp_path. Nothing here touches the real folder."""
    return EquipmentDocumentStore(gw, root=tmp_path / "documents")


class RecordingGateway:
    """Wraps the fake and remembers every statement AND its bound arguments.

    The arguments are the half that matters and the half the first version of
    this class threw away. Counting ops only needs the SQL; proving no file
    content reaches the queue needs everything that crosses the wire, and the
    natural wrong implementation — `INSERT ... VALUES (?, ?)` with the base64 in
    the parameter list — has an entirely innocent-looking statement.
    """

    def __init__(self, inner):
        self.inner = inner
        self.statements = []            # (sql, args) — everything sent
        self.writes = []                # sql only, for counting ops
        self.reads = []

    def sql(self, sql, args=None, **kw):
        self.writes.append(sql)
        self.statements.append((sql, list(args or [])))
        return self.inner.sql(sql, args, **kw)

    def read_sql(self, sql, args=None, **kw):
        self.reads.append(sql)
        self.statements.append((sql, list(args or [])))
        return self.inner.read_sql(sql, args, **kw)

    def is_running(self):
        return True


# Everything a save is allowed to put through the queue, in characters. A row of
# metadata is a uid, a machine_uid, a filename, a size, a type, a 64-character
# hash and two timestamps — a few hundred characters including the statement.
# Anything approaching a document is orders of magnitude past this, whatever
# encoding it arrives in.
QUEUE_BUDGET_CHARS = 2000


def queue_traffic(rec) -> str:
    """Every statement and every bound argument, as one searchable blob.

    Arguments are flattened with latin-1 rather than repr so that raw `bytes` in
    a parameter list are searchable as the file's own content; anything else is
    stringified.
    """
    parts = []
    for sql, args in rec.statements:
        parts.append(str(sql))
        for arg in args:
            if isinstance(arg, (bytes, bytearray)):
                parts.append(bytes(arg).decode("latin-1"))
            else:
                parts.append(str(arg))
    return "\n".join(parts)


def assert_no_document_bytes_reached_the_queue(rec, data: bytes) -> None:
    """The module's reason to exist, as an assertion.

    LabCore is an HTTP write queue that serialises at roughly 1.5 writes/sec and
    refuses past 100 pending. A 5 MB PDF is ~6.7 MB of base64 in one statement,
    and it would sit there ahead of every QC verdict, result and heartbeat the
    floor is trying to write. So this checks the three shapes a leak actually
    takes — the bytes inline in the SQL, the bytes as a bound parameter, the
    bytes base64'd into either — and then, because an encoding nobody thought of
    is always possible, checks the total size as well. The size check is the one
    that cannot be out-thought.

    `test_the_guard_bites_...` proves this function fails when it should, which
    is the difference between a guard and a comment.
    """
    blob = queue_traffic(rec)
    raw = data.decode("latin-1")
    assert raw not in blob, "the document's own bytes went through the queue"
    assert base64.b64encode(data).decode("ascii") not in blob, \
        "the document went through the queue base64-encoded"
    assert data.hex() not in blob, "the document went through the queue as hex"
    assert len(blob) < QUEUE_BUDGET_CHARS, (
        f"a save put {len(blob)} characters through the queue for a "
        f"{len(data)}-byte document; the budget is {QUEUE_BUDGET_CHARS}")


class RefusingGateway:
    """LabCore's queue past 100 pending: it returns an error dict, never raises.

    Reads still work, so a test can prove the store did not leave a row behind.
    """

    def __init__(self, inner, refuse_prefix="INSERT"):
        self.inner = inner
        self.refuse_prefix = refuse_prefix

    def sql(self, sql, args=None, **kw):
        if sql.lstrip().upper().startswith(self.refuse_prefix):
            return {"error": "queue is full (100 pending)"}
        return self.inner.sql(sql, args, **kw)

    def read_sql(self, sql, args=None, **kw):
        return self.inner.read_sql(sql, args, **kw)

    def is_running(self):
        return True


class UnacknowledgingGateway:
    """A gateway whose writes answer without ever saying the write happened.

    Three real shapes, none of which carries an "error" key, and all of which
    mean the row was never written:

      `None`  — what a gateway hands back when it has stopped answering at all;
      `{}`    — an empty body from a proxy or a half-open connection;
      `{"ok": False, "status": "rejected", "pending": 100}` — LabCore's own
              queue refusing past 100 pending. The real client returns
              `resp.json()` verbatim, so this arrives exactly as written.

    A store that decides success from the ABSENCE of "error" believes all three.
    Reads keep working, so a test can prove no row was left behind.
    """

    def __init__(self, inner, answer=None, refuse_prefix=""):
        self.inner = inner
        self.answer = answer
        self.refuse_prefix = refuse_prefix

    def sql(self, sql, args=None, **kw):
        if not self.refuse_prefix or \
                sql.lstrip().upper().startswith(self.refuse_prefix):
            return self.answer
        return self.inner.sql(sql, args, **kw)

    def read_sql(self, sql, args=None, **kw):
        return self.inner.read_sql(sql, args, **kw)

    def is_running(self):
        return True


class BlippingGateway:
    """Reads failing while writes still land — LabCore's actual bad minute.

    `read_sql` POSTs to `/api/queue/write` like everything else, so a congested
    queue times reads out (measured: exactly 8.00s, four times in six) while the
    connection itself is fine. The failure arrives as `{"error": ...}`, never as
    an exception, and it says nothing about the table existing.

    This is the gateway that separates "there is no such document" from "nobody
    could be asked", and every test that uses it is about a store that must not
    confuse the two.
    """

    def __init__(self, inner, error="HTTP 503"):
        self.inner = inner
        self.error = error
        self.blipping = True

    def sql(self, sql, args=None, **kw):
        return self.inner.sql(sql, args, **kw)

    def read_sql(self, sql, args=None, **kw):
        if self.blipping:
            return {"error": self.error}
        return self.inner.read_sql(sql, args, **kw)

    def is_running(self):
        return True


# ── the storage root ────────────────────────────────────────────────────────

class TestDocumentsRoot:
    def test_the_root_is_configurable(self, gw, tmp_path):
        here = tmp_path / "elsewhere"
        assert EquipmentDocumentStore(gw, root=here).root == here

    def test_the_default_root_comes_from_the_environment(self, monkeypatch):
        monkeypatch.setenv(DOCUMENTS_DIR_ENV, "/srv/lem-docs")
        assert str(default_documents_root()) == os.path.abspath("/srv/lem-docs")

    def test_the_default_root_falls_back_to_the_data_dir(self, monkeypatch):
        monkeypatch.delenv(DOCUMENTS_DIR_ENV, raising=False)
        monkeypatch.setenv(DATA_DIR_ENV, "/srv/lem-data")
        root = default_documents_root()
        assert str(root).startswith(os.path.abspath("/srv/lem-data"))
        assert root.name == "documents"

    def test_the_default_root_sits_under_the_app_folder(self, monkeypatch):
        monkeypatch.delenv(DOCUMENTS_DIR_ENV, raising=False)
        monkeypatch.delenv(DATA_DIR_ENV, raising=False)
        root = default_documents_root()
        app_dir = os.path.dirname(os.path.abspath(equipment_documents.__file__))
        assert str(root).startswith(app_dir)
        assert root.parts[-2:] == ("data", "documents")

    def test_the_root_is_created_on_first_save_not_on_construction(
            self, gw, tmp_path):
        root = tmp_path / "documents"
        store = EquipmentDocumentStore(gw, root=root)
        assert not root.exists()
        store.save("m1", "cert.pdf", PDF)
        assert root.exists()

    def test_nothing_lands_outside_the_root(self, store, tmp_path):
        store.save("m1", "cert.pdf", PDF)
        written = [p for p in tmp_path.rglob("*") if p.is_file()]
        assert written
        for path in written:
            assert store.root in path.parents


# ── the on-disk path is DERIVED, never taken from the client ────────────────

class TestDerivedPaths:
    def test_a_posix_traversal_cannot_escape(self, store):
        """The realistic attack: a genuine PDF wearing a path for a name."""
        doc = store.save("m1", "../../web_app.pdf", PDF)
        assert store.root in store.path_for(doc).parents
        assert ".." not in str(store.path_for(doc))
        assert "/" not in doc.filename and "\\" not in doc.filename

    def test_a_windows_traversal_cannot_escape(self, store):
        doc = store.save("m1", r"..\..\web_app.pdf", PDF)
        assert store.root in store.path_for(doc).parents
        assert doc.filename == "web_app.pdf"

    def test_an_absolute_path_cannot_escape(self, store):
        doc = store.save("m1", "/etc/passwd.pdf", PDF)
        assert store.root in store.path_for(doc).parents
        assert doc.filename == "passwd.pdf"

    def test_the_traversal_really_does_not_reach_the_neighbour(
            self, gw, tmp_path):
        """The proof, not the promise: a real file two levels up stays untouched."""
        victim = tmp_path / "web_app.pdf"
        victim.write_text("# the real thing\n")
        store = EquipmentDocumentStore(gw, root=tmp_path / "docs" / "deep")
        store.save("m1", "../../web_app.pdf", PDF)
        assert victim.read_text() == "# the real thing\n"

    def test_a_source_file_name_never_gets_that_far(self, store):
        """`../../web_app.py` is refused by the accept-list before the path
        derivation is even reached — two independent gates, not one."""
        with pytest.raises(DocumentRejected):
            store.save("m1", "../../web_app.py", PDF,
                       content_type="application/pdf")

    def test_the_path_is_built_from_the_uid_and_the_resolved_type(self, store):
        doc = store.save("m1", "Calibration Certificate 2026.pdf", PDF)
        path = store.path_for(doc)
        assert path.name == f"{doc.uid}.pdf"
        assert "Calibration" not in str(path)

    def test_a_traversing_equipment_uid_cannot_escape(self, store):
        doc = store.save("../../..", "cert.pdf", PDF)
        assert store.root in store.path_for(doc).parents
        assert ".." not in str(store.path_for(doc).relative_to(store.root))

    def test_equipment_uids_that_sanitise_alike_get_different_folders(self, store):
        a = store.save("bay/1", "cert.pdf", PDF)
        b = store.save("bay:1", "cert.pdf", PDF)
        assert store.path_for(a).parent != store.path_for(b).parent

    def test_a_forged_row_cannot_point_outside_the_root(self, store):
        """The last line of defence, and it earns its place.

        A mutation that put the uploaded name back into the path left every
        escape test green, because the display name was separately flattened.
        Two gates that each look sufficient are how a refactor quietly removes
        one. `path_for` now asserts the property itself.
        """
        forged = EquipmentDocument(
            uid="../../../evil", machine_uid="m1", filename="x.pdf",
            size_bytes=1, content_type="application/pdf",
            content_hash="", uploaded_at="2026-08-24T09:00:00")
        with pytest.raises(DocumentStoreError):
            store.path_for(forged)

    def test_quotes_and_newlines_are_stripped_from_the_display_name(self, store):
        doc = store.save("m1", 'ce"rt\r\nX-Evil: yes.pdf', PDF)
        assert '"' not in doc.filename
        assert "\r" not in doc.filename and "\n" not in doc.filename

    def test_a_nameless_upload_still_gets_a_usable_name(self, store):
        doc = store.save("m1", "   ", PDF, content_type="application/pdf")
        assert doc.filename.endswith(".pdf")
        assert doc.filename.strip(". ")


# ── save, list, fetch ───────────────────────────────────────────────────────

class TestSaveListFetch:
    def test_round_trip(self, store):
        doc = store.save("m1", "cert.pdf", PDF, uploaded_by="ryan",
                         now=datetime(2026, 8, 24, 9, 0, 0))
        assert isinstance(doc, EquipmentDocument)
        assert doc.machine_uid == "m1"
        assert doc.filename == "cert.pdf"
        assert doc.size_bytes == len(PDF)
        assert doc.content_type == "application/pdf"
        assert doc.uploaded_by == "ryan"
        assert doc.uploaded_at.startswith("2026-08-24T09:00")
        assert doc.content_hash

    def test_the_bytes_are_on_disk(self, store):
        doc = store.save("m1", "cert.pdf", PDF)
        assert store.path_for(doc).read_bytes() == PDF

    def test_the_metadata_row_is_in_labcore(self, store, gw):
        store.save("m1", "cert.pdf", PDF)
        res = gw.read_sql("SELECT * FROM lem_equipment_documents")
        assert len(res["rows"]) == 1
        assert res["rows"][0]["machine_uid"] == "m1"

    def test_documents_are_listed_for_their_own_equipment_only(self, store):
        store.save("m1", "a.pdf", PDF)
        store.save("m2", "b.pdf", OTHER_PDF)
        assert [d.filename for d in store.documents("m1")] == ["a.pdf"]
        assert [d.filename for d in store.documents("m2")] == ["b.pdf"]

    def test_listing_is_newest_first(self, store):
        store.save("m1", "old.pdf", PDF, now=datetime(2026, 1, 1))
        store.save("m1", "new.pdf", OTHER_PDF, now=datetime(2026, 8, 1))
        assert [d.filename for d in store.documents("m1")] == ["new.pdf", "old.pdf"]

    def test_fetch_returns_the_metadata_and_the_bytes(self, store):
        doc = store.save("m1", "cert.pdf", PDF)
        got, data = store.fetch(doc.uid)
        assert got.uid == doc.uid
        assert got.filename == "cert.pdf"
        assert data == PDF

    def test_get_on_an_unknown_uid_is_None(self, store):
        assert store.get("nope") is None

    def test_fetch_on_an_unknown_uid_raises(self, store):
        with pytest.raises(DocumentStoreError):
            store.fetch("nope")

    def test_a_missing_table_lists_empty_rather_than_raising(self, tmp_path):
        """Before the boot DDL runs, the tab is empty — not a stack trace."""
        bare = FakeLabCoreGateway()
        store = EquipmentDocumentStore(bare, root=tmp_path)
        assert store.documents("m1") == []
        assert store.get("anything") is None

    def test_to_dict_is_json_safe(self, store):
        doc = store.save("m1", "cert.pdf", PDF)
        payload = doc.to_dict()
        assert payload["uid"] == doc.uid
        assert payload["size_bytes"] == len(PDF)
        assert set(payload) >= {"uid", "machine_uid", "filename", "size_bytes",
                                "content_type", "content_hash", "uploaded_at",
                                "uploaded_by"}


# ── duplicates and name collisions ──────────────────────────────────────────

class TestDuplicatesAndCollisions:
    def test_the_same_bytes_twice_on_one_machine_is_one_document(self, store):
        first = store.save("m1", "cert.pdf", PDF)
        again = store.save("m1", "cert.pdf", PDF)
        assert again.uid == first.uid
        assert len(store.documents("m1")) == 1

    def test_a_re_upload_under_a_different_name_still_dedupes(self, store):
        first = store.save("m1", "cert.pdf", PDF)
        again = store.save("m1", "certificate-final.pdf", PDF)
        assert again.uid == first.uid
        assert len(store.documents("m1")) == 1

    def test_the_same_file_on_two_machines_is_two_documents(self, store):
        a = store.save("m1", "cert.pdf", PDF)
        b = store.save("m2", "cert.pdf", PDF)
        assert a.uid != b.uid
        assert a.content_hash == b.content_hash
        assert store.path_for(a) != store.path_for(b)

    def test_two_different_files_sharing_a_name_both_survive(self, store):
        a = store.save("m1", "cert.pdf", PDF)
        b = store.save("m1", "cert.pdf", OTHER_PDF)
        assert a.uid != b.uid
        assert {d.uid for d in store.documents("m1")} == {a.uid, b.uid}
        assert store.fetch(a.uid)[1] == PDF
        assert store.fetch(b.uid)[1] == OTHER_PDF

    def test_a_collision_never_overwrites_on_disk(self, store):
        a = store.save("m1", "cert.pdf", PDF)
        b = store.save("m1", "cert.pdf", OTHER_PDF)
        assert store.path_for(a).exists() and store.path_for(b).exists()
        assert store.path_for(a) != store.path_for(b)


# ── the limits, both named constants ────────────────────────────────────────

class TestLimits:
    def test_the_ceiling_is_the_constant_and_not_a_number_in_a_branch(
            self, store, monkeypatch):
        """The old version of this test asserted `isinstance(int) and > 0`.

        That passes with the constant renamed, ignored, or replaced by a magic
        number in the branch — it tested that a name existed, not that anything
        read it. Moving the constant must move the limit.
        """
        monkeypatch.setattr(equipment_documents, "MAX_DOCUMENT_BYTES", 512)
        with pytest.raises(DocumentRejected):
            store.save("m1", "big.pdf", b"%PDF-1.7\n" + b"0" * 600)
        assert store.save("m1", "small.pdf", PDF).size_bytes == len(PDF)

    def test_the_accept_list_drives_the_stored_extension_for_every_type(
            self, store):
        """Likewise: asserting one dict entry passes with the dict unread.

        Every accepted type must actually land on disk under the extension the
        accept-list names for it, or the constant is documentation.
        """
        assert ACCEPTED_CONTENT_TYPES["application/pdf"] == ".pdf"
        for name, data in (("a.pdf", PDF), ("b.png", PNG), ("c.jpg", JPEG)):
            doc = store.save("m1", name, data)
            assert store.path_for(doc).suffix == \
                ACCEPTED_CONTENT_TYPES[doc.content_type]

    def test_a_file_over_the_ceiling_is_refused(self, store):
        huge = b"%PDF-1.7\n" + b"0" * MAX_DOCUMENT_BYTES
        with pytest.raises(DocumentRejected):
            store.save("m1", "huge.pdf", huge)
        assert store.documents("m1") == []
        assert not store.root.exists() or not [
            p for p in store.root.rglob("*") if p.is_file()]

    def test_a_file_exactly_at_the_ceiling_is_accepted(self, store):
        head = b"%PDF-1.7\n"
        exact = head + b"0" * (MAX_DOCUMENT_BYTES - len(head))
        doc = store.save("m1", "big.pdf", exact)
        assert doc.size_bytes == MAX_DOCUMENT_BYTES

    def test_an_empty_upload_is_refused(self, store):
        with pytest.raises(DocumentRejected):
            store.save("m1", "cert.pdf", b"")
        assert store.documents("m1") == []

    def test_an_unaccepted_extension_is_refused(self, store):
        with pytest.raises(DocumentRejected):
            store.save("m1", "setup.exe", b"MZ\x90\x00" + b"x" * 100)

    def test_a_pdf_name_over_non_pdf_bytes_is_refused(self, store):
        """The extension is a claim; the signature is the only thing that cannot lie."""
        with pytest.raises(DocumentRejected):
            store.save("m1", "cert.pdf", b"MZ\x90\x00" + b"x" * 100)
        assert store.documents("m1") == []

    def test_images_are_accepted(self, store):
        png = store.save("m1", "plate.png", PNG)
        jpg = store.save("m1", "plate.jpg", JPEG)
        assert png.content_type == "image/png"
        assert jpg.content_type == "image/jpeg"

    def test_a_declared_type_cannot_override_a_named_extension(self, store):
        """The Content-Type is the half a caller controls most cheaply."""
        with pytest.raises(DocumentRejected):
            store.save("m1", "payload.py", PDF, content_type="application/pdf")

    def test_a_name_without_an_extension_falls_back_to_the_declared_type(self, store):
        doc = store.save("m1", "scan", PDF, content_type="application/pdf")
        assert doc.content_type == "application/pdf"
        assert store.path_for(doc).suffix == ".pdf"

    def test_a_declared_type_with_charset_parameters_still_resolves(self, store):
        doc = store.save("m1", "scan", PDF, content_type="application/pdf; qs=0.9")
        assert doc.content_type == "application/pdf"

    def test_a_document_with_no_equipment_is_refused(self, store):
        with pytest.raises(DocumentRejected):
            store.save("   ", "cert.pdf", PDF)


# ── half-completed saves: pick the survivable orphan ────────────────────────

class TestPartialFailures:
    def test_a_refused_metadata_write_leaves_no_file_behind(self, gw, tmp_path):
        """The queue refuses past 100 pending — an error dict, not an exception."""
        store = EquipmentDocumentStore(RefusingGateway(gw),
                                       root=tmp_path / "documents")
        with pytest.raises(DocumentStoreError):
            store.save("m1", "cert.pdf", PDF)
        assert [p for p in tmp_path.rglob("*") if p.is_file()] == []

    def test_a_raised_metadata_write_also_leaves_no_file_behind(self, gw, tmp_path):
        class Raising(RefusingGateway):
            def sql(self, sql, args=None, **kw):
                if sql.lstrip().upper().startswith("INSERT"):
                    raise RuntimeError("connection reset")
                return self.inner.sql(sql, args, **kw)

        store = EquipmentDocumentStore(Raising(gw), root=tmp_path / "documents")
        with pytest.raises(DocumentStoreError):
            store.save("m1", "cert.pdf", PDF)
        assert [p for p in tmp_path.rglob("*") if p.is_file()] == []

    def test_a_failed_disk_write_writes_no_metadata_row(self, store, monkeypatch):
        def boom(*a, **kw):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(equipment_documents.os, "replace", boom)
        with pytest.raises(DocumentStoreError):
            store.save("m1", "cert.pdf", PDF)
        assert store.documents("m1") == []

    def test_a_failed_disk_write_leaves_no_half_file(self, store, monkeypatch):
        def boom(*a, **kw):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(equipment_documents.os, "replace", boom)
        with pytest.raises(DocumentStoreError):
            store.save("m1", "cert.pdf", PDF)
        assert [p for p in store.root.rglob("*") if p.is_file()] == []

    def test_a_row_whose_file_vanished_says_so(self, store):
        doc = store.save("m1", "cert.pdf", PDF)
        store.path_for(doc).unlink()
        with pytest.raises(DocumentStoreError):
            store.fetch(doc.uid)

    def test_orphaned_files_are_findable(self, store):
        doc = store.save("m1", "cert.pdf", PDF)
        stray = store.path_for(doc).parent / "abandoned.pdf"
        stray.write_bytes(PDF)
        orphans = store.orphaned_files()
        assert str(stray) in orphans
        assert str(store.path_for(doc)) not in orphans


# ── delete ──────────────────────────────────────────────────────────────────

class TestDelete:
    def test_delete_removes_the_row_and_the_file(self, store):
        doc = store.save("m1", "cert.pdf", PDF)
        path = store.path_for(doc)
        assert store.delete(doc.uid) is True
        assert store.documents("m1") == []
        assert not path.exists()

    def test_delete_is_idempotent(self, store):
        doc = store.save("m1", "cert.pdf", PDF)
        store.delete(doc.uid)
        assert store.delete(doc.uid) is False

    def test_delete_leaves_the_others_alone(self, store):
        keep = store.save("m1", "keep.pdf", PDF)
        drop = store.save("m1", "drop.pdf", OTHER_PDF)
        store.delete(drop.uid)
        assert [d.uid for d in store.documents("m1")] == [keep.uid]
        assert store.path_for(keep).exists()

    def test_a_refused_metadata_delete_keeps_the_file(self, gw, tmp_path):
        """Row first, then bytes: a refused delete must leave the pair intact."""
        store = EquipmentDocumentStore(gw, root=tmp_path / "documents")
        doc = store.save("m1", "cert.pdf", PDF)
        store.gateway = RefusingGateway(gw, refuse_prefix="DELETE")
        with pytest.raises(DocumentStoreError):
            store.delete(doc.uid)
        assert store.path_for(doc).exists()
        assert [d.uid for d in store.documents("m1")] == [doc.uid]

    def test_deleting_the_last_document_can_be_re_uploaded(self, store):
        doc = store.save("m1", "cert.pdf", PDF)
        store.delete(doc.uid)
        again = store.save("m1", "cert.pdf", PDF)
        assert store.fetch(again.uid)[1] == PDF


# ── schema discipline and queue economy ─────────────────────────────────────

class TestSchemaDiscipline:
    def test_the_ddl_declares_a_new_table_and_alters_nothing(self):
        assert "lem_equipment_documents" in DOCUMENTS_DDL
        assert "ALTER" not in DOCUMENTS_DDL.upper()
        source = open(equipment_documents.__file__, encoding="utf-8").read()
        assert "ALTER TABLE" not in source.upper()

    def test_the_store_never_declares_its_own_schema(self, gw, tmp_path):
        """DDL belongs in snapshot_service's central tuple, applied once at boot.

        A store that creates its own table on demand is how the batched read ends
        up naming a column LabCore does not have.
        """
        rec = RecordingGateway(gw)
        store = EquipmentDocumentStore(rec, root=tmp_path / "documents")
        doc = store.save("m1", "cert.pdf", PDF)
        store.documents("m1")
        store.fetch(doc.uid)
        store.delete(doc.uid)
        assert not any("CREATE TABLE" in s.upper() for s in rec.writes)

    def test_a_save_costs_one_write(self, gw, tmp_path):
        rec = RecordingGateway(gw)
        store = EquipmentDocumentStore(rec, root=tmp_path / "documents")
        store.save("m1", "cert.pdf", PDF)
        assert len(rec.writes) == 1

    def test_a_whole_floor_costs_one_read(self, gw, tmp_path):
        """A tab badge on every instrument must not be one op per instrument."""
        rec = RecordingGateway(gw)
        store = EquipmentDocumentStore(rec, root=tmp_path / "documents")
        store.save("m1", "a.pdf", PDF)
        store.save("m2", "b.pdf", OTHER_PDF)
        rec.reads.clear()
        found = document_counts_by_machine(store, ["m1", "m2", "m3"])
        assert len(rec.reads) == 1
        assert found == {"m1": 1, "m2": 1, "m3": 0}

    def test_the_badge_read_asks_for_counts_and_not_for_documents(
            self, gw, tmp_path):
        """The exemption is granted to a COUNT, so it has to be one.

        This read is the only one here allowed to answer "nothing" when it does
        not know, and the reason given is that it is a count on a page that
        already carries an OFFLINE banner. Returning every row and counting them
        in Python took the licence without doing the thing it was granted for —
        and shipped a floor of 60 instruments the metadata of all 500 of their
        documents to draw 60 numbers.
        """
        rec = RecordingGateway(gw)
        store = EquipmentDocumentStore(rec, root=tmp_path / "documents")
        store.save("m1", "a.pdf", PDF)
        store.save("m1", "b.pdf", OTHER_PDF)
        store.save("m1", "c.png", PNG)
        store.save("m2", "d.jpg", JPEG)
        rec.reads.clear()
        counts = document_counts_by_machine(store, ["m1", "m2", "m3"])
        assert counts == {"m1": 3, "m2": 1, "m3": 0}
        asked = rec.reads[0]
        assert "COUNT(" in asked.upper()
        assert "filename" not in asked.lower()
        assert "content_hash" not in asked.lower()

    def test_a_whole_floor_of_nobody_costs_nothing(self, gw, tmp_path):
        rec = RecordingGateway(gw)
        store = EquipmentDocumentStore(rec, root=tmp_path / "documents")
        assert document_counts_by_machine(store, []) == {}
        assert rec.reads == []

    def test_no_file_bytes_ever_reach_the_queue(self, gw, tmp_path):
        """The module's one load-bearing claim, tested at last.

        The previous version of this test searched the SQL strings for `%PDF`
        and asserted each was under 1000 characters. It passes unchanged against
        an implementation that puts the whole document in the queue as a bound
        parameter — the arguments were never recorded — which is precisely the
        design this module exists to avoid. The 300 KB certificate below is the
        realistic size; the small one is short enough to hide inside a statement
        without anyone noticing.
        """
        rec = RecordingGateway(gw)
        store = EquipmentDocumentStore(rec, root=tmp_path / "documents")
        doc = store.save("m1", "cert.pdf", BIG_PDF)
        assert_no_document_bytes_reached_the_queue(rec, BIG_PDF)
        # And the bytes really did land — the cheap way to pass the assertion
        # above is to store nothing at all.
        assert store.path_for(doc).read_bytes() == BIG_PDF

    def test_no_file_bytes_reach_the_queue_on_any_path(self, gw, tmp_path):
        """Save is not the only writer. Fetch, list and delete all pass too."""
        rec = RecordingGateway(gw)
        store = EquipmentDocumentStore(rec, root=tmp_path / "documents")
        doc = store.save("m1", "cert.pdf", BIG_PDF)
        store.documents("m1")
        store.fetch(doc.uid)
        store.delete(doc.uid)
        assert_no_document_bytes_reached_the_queue(rec, BIG_PDF)

    def test_the_guard_bites_when_the_bytes_do_go_through(self, gw, tmp_path):
        """The guard above is only worth its line count if it fails on a leak.

        A test that can never fail is a comment with a runtime cost, and this
        module already has one such near-miss on record (two independent gates
        flattening a path, so removing either kept the suite green). So: the
        three shapes a leak takes, fed to the assertion directly, each of which
        must be caught.
        """
        rec = RecordingGateway(gw)
        leaks = [
            # the bound parameter — an innocent-looking statement
            ("INSERT INTO lem_equipment_documents (uid, blob) VALUES (?, ?)",
             ["abc", base64.b64encode(BIG_PDF).decode("ascii")]),
            # the raw bytes handed straight to the driver
            ("INSERT INTO lem_equipment_documents (uid, blob) VALUES (?, ?)",
             ["abc", BIG_PDF]),
            # inlined into the statement itself
            ("INSERT INTO lem_equipment_documents (blob) VALUES ('"
             + BIG_PDF.hex() + "')", []),
        ]
        for leak in leaks:
            rec.statements = [leak]
            with pytest.raises(AssertionError):
                assert_no_document_bytes_reached_the_queue(rec, BIG_PDF)


# ── "could not ask" is not "there is nothing" ───────────────────────────────

class TestAReadOutageIsNotAnEmptyAnswer:
    """The one bug in this module that writes a permanent wrong answer.

    Every read went through one bare `except: return []`, so a LabCore blip and
    an empty table were the same value. This module's own docstring is the
    argument against that — "a document that is listed and cannot be produced is
    worse than one that was never listed, because the list is what people trust"
    — and `snapshot_service.SnapshotReadError` states the house rule directly:
    "Reporting 'no machines' when the truth is 'could not ask' is how a whole lab
    reads as empty during a LabCore blip."

    A missing table really is an empty answer and still returns []. Everything
    else is an outage and says so.
    """

    @pytest.fixture
    def blip(self, gw, tmp_path):
        """A store holding one real document, whose reads have just gone dark."""
        store = EquipmentDocumentStore(gw, root=tmp_path / "documents")
        doc = store.save("m1", "cert.pdf", PDF)
        store.gateway = BlippingGateway(gw)
        return store, doc

    def test_a_missing_table_is_still_an_empty_tab(self, tmp_path):
        """The one failure that genuinely means "there is nothing".

        Display paths only. This test used to assert the sweep answered `[]`
        here too, which is how the sweep came to report every live certificate
        as deletable — see TestTheSweepNeverNamesTheFilesToKeep. An assertion
        that encodes the bug cannot catch it.
        """
        bare = FakeLabCoreGateway()
        store = EquipmentDocumentStore(bare, root=tmp_path)
        assert store.documents("m1") == []
        assert store.get("anything") is None

    def test_the_sweep_is_the_exception_to_that(self, tmp_path):
        """Everything else on this class degrades to empty; a delete list does
        not get to."""
        bare = FakeLabCoreGateway()
        store = EquipmentDocumentStore(bare, root=tmp_path)
        with pytest.raises(DocumentStoreError):
            store.orphaned_files()

    def test_the_matcher_recognises_labcores_own_wording_and_little_else(self):
        """The single hinge of the whole fix, so it is pinned directly.

        Too narrow costs a visible error about a table that really is absent.
        Too loose costs a silent empty tab during an outage — the bug itself —
        so a sentence that merely contains the words must not match.
        """
        assert equipment_documents.is_missing_table(
            "OperationalError: no such table: lem_equipment_documents")
        assert not equipment_documents.is_missing_table("HTTP 503")
        assert not equipment_documents.is_missing_table(
            "timeout after 8.00s; the table lock does not exist yet")
        assert not equipment_documents.is_missing_table("")

    def test_a_read_outage_is_not_an_empty_tab(self, blip):
        store, _doc = blip
        with pytest.raises(DocumentStoreError):
            store.documents("m1")

    def test_a_read_outage_is_not_an_unknown_uid(self, blip):
        store, doc = blip
        with pytest.raises(DocumentStoreError):
            store.get(doc.uid)

    def test_delete_during_an_outage_does_not_report_nothing_to_delete(
            self, blip, gw):
        """`delete() -> False` means "there was nothing to delete".

        Said about a document that is still in LabCore and still on disk, that is
        a lie the caller cannot detect: a route answers 404, the operator clicks
        again, and the certificate stays listed.
        """
        store, doc = blip
        with pytest.raises(DocumentStoreError):
            store.delete(doc.uid)
        store.gateway.blipping = False
        assert [d.uid for d in store.documents("m1")] == [doc.uid]
        assert store.path_for(doc).exists()

    def test_fetch_during_an_outage_does_not_name_the_uid_as_unknown(self, blip):
        store, doc = blip
        with pytest.raises(DocumentStoreError) as caught:
            store.fetch(doc.uid)
        assert "No document" not in str(caught.value)
        assert "LabCore" in str(caught.value)

    def test_an_outage_never_stores_a_second_copy_of_the_same_bytes(
            self, blip, gw):
        """The permanent one: dedupe reads, and a read that failed said "new".

        The second upload wrote a second row and a second copy of identical
        bytes, and nothing later ever collapses them — the tab shows one
        certificate twice, forever, because of one bad second.
        """
        store, doc = blip
        with pytest.raises(DocumentStoreError):
            store.save("m1", "cert.pdf", PDF)
        store.gateway.blipping = False
        assert [d.uid for d in store.documents("m1")] == [doc.uid]
        assert len([p for p in store.root.rglob("*") if p.is_file()]) == 1

    def test_an_outage_does_not_call_every_live_file_an_orphan(self, blip):
        """Worse than useless: the sweep would name exactly the files to keep.

        With no rows read, every known path is unknown, so `orphaned_files()`
        returns the whole store — handed to a person whose next step is deleting
        them.
        """
        store, _doc = blip
        with pytest.raises(DocumentStoreError):
            store.orphaned_files()

    def test_the_floor_badge_degrades_where_the_tab_does_not(self, blip, gw):
        """A deliberate, separate split — not one bare except covering both.

        `document_counts_by_machine` decorates a floor that already carries its own
        staleness and OFFLINE banner, and a count is not a list anyone reads
        during an audit. It degrades to empty so one blip does not take the whole
        floor down. `documents()` IS the list, so it raises.
        """
        store, _doc = blip
        assert document_counts_by_machine(store, ["m1", "m2"]) == {"m1": 0, "m2": 0}
        with pytest.raises(DocumentStoreError):
            store.documents("m1")

    def test_a_read_that_answers_with_nothing_at_all_is_an_outage(
            self, gw, tmp_path):
        """No dict, no rows key, no error key: not proof of an empty table."""

        class Mute(BlippingGateway):
            def read_sql(self, sql, args=None, **kw):
                return None

        store = EquipmentDocumentStore(Mute(gw), root=tmp_path / "documents")
        with pytest.raises(DocumentStoreError):
            store.documents("m1")

    def test_a_raising_read_is_an_outage_too(self, gw, tmp_path):
        class Raising(BlippingGateway):
            def read_sql(self, sql, args=None, **kw):
                raise RuntimeError("connection reset")

        store = EquipmentDocumentStore(Raising(gw), root=tmp_path / "documents")
        with pytest.raises(DocumentStoreError):
            store.documents("m1")


# ── the accept-list is a contract with the files already on disk ────────────

class TestChangingTheAcceptListDoesNotDetachStoredFiles:
    """`path_for` recomputes the extension from ACCEPTED_CONTENT_TYPES.

    So editing one value — `.jpg` to `.jpeg`, the most natural tidy-up in this
    file — silently repoints every stored file of that type. The tab still lists
    them, `fetch` 500s, `delete` removes the row and leaves the bytes, and
    `orphaned_files()` then reports the LIVE files as the orphans to sweep.
    """

    def test_stored_files_survive_a_changed_extension(self, store, monkeypatch):
        doc = store.save("m1", "plate.jpg", JPEG)
        monkeypatch.setitem(
            equipment_documents.ACCEPTED_CONTENT_TYPES, "image/jpeg", ".jpeg")
        got, data = store.fetch(doc.uid)
        assert got.uid == doc.uid and data == JPEG

    def test_a_changed_extension_does_not_turn_live_files_into_orphans(
            self, store, monkeypatch):
        store.save("m1", "plate.jpg", JPEG)
        monkeypatch.setitem(
            equipment_documents.ACCEPTED_CONTENT_TYPES, "image/jpeg", ".jpeg")
        assert store.orphaned_files() == []

    def test_a_changed_extension_does_not_leave_delete_half_done(
            self, store, monkeypatch):
        doc = store.save("m1", "plate.jpg", JPEG)
        monkeypatch.setitem(
            equipment_documents.ACCEPTED_CONTENT_TYPES, "image/jpeg", ".jpeg")
        assert store.delete(doc.uid) is True
        assert [p for p in store.root.rglob("*") if p.is_file()] == []

    def test_the_stored_extensions_are_pinned(self):
        """Changing a value here is a data migration, not an edit.

        The store now finds an already-stored file whatever it was written as, so
        this is no longer a corruption — but new and old files of one type end up
        under two spellings, and that is a decision, not a tidy-up.
        """
        assert ACCEPTED_CONTENT_TYPES == {
            "application/pdf": ".pdf",
            "image/png": ".png",
            "image/jpeg": ".jpg",
        }


# ── the DDL comment must describe reality ───────────────────────────────────

class TestTheDdlSaysWhatIsTrue:
    def test_the_ddl_is_registered_or_the_module_says_it_is_not(self):
        """The comment claimed a registration that does not exist.

        `lem_equipment_documents` is in no central tuple, so on a live server the
        table is absent, every read earns the missing-table empty list, and an
        unwired deployment looks exactly like an instrument with no documents.
        This test holds the claim to the fact in both directions: once the wiring
        phase adds the constant to `snapshot_service.SCHEMA_DDL`, it must be THIS
        constant and not a retyped copy that has since drifted.
        """
        source = open(equipment_documents.__file__, encoding="utf-8").read()
        registered = [d for d in snapshot_service.SCHEMA_DDL
                      if "lem_equipment_documents" in d]
        if registered:
            assert registered[0] == DOCUMENTS_DDL
        else:
            assert "not registered" in source.lower()
            assert "Registered in snapshot_service" not in source

    def test_the_table_is_not_an_arm_of_the_batched_read(self):
        """Deliberate: documents are per-equipment and read on demand.

        Every arm shares ONE statement, so an eleventh arm buys a badge with the
        whole floor's read.
        """
        assert "lem_equipment_documents" not in snapshot_service.batched_machine_sql()


# ── the sweep, the name, the ceiling, and retiring an instrument ────────────

class TestSweepSurvivesABadRow:
    def test_one_unmappable_row_does_not_abort_the_whole_sweep(self, store, gw):
        """A row LabCore has no foreign keys to protect.

        A forged or corrupted uid makes `path_for` refuse — correctly. Letting
        that refusal escape means one bad row hides every real orphan from the
        person sweeping, which is the opposite of what a report is for.
        """
        doc = store.save("m1", "cert.pdf", PDF)
        stray = store.path_for(doc).parent / "abandoned.pdf"
        stray.write_bytes(OTHER_PDF)
        gw.sql(
            "INSERT INTO lem_equipment_documents (uid, machine_uid, filename, "
            "size_bytes, content_type, content_hash, uploaded_at, uploaded_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ["../../../evil", "m1", "evil.pdf", 1, "application/pdf", "",
             "2026-08-24T09:00:00", ""])
        orphans = store.orphaned_files()
        assert str(stray) in orphans
        assert str(store.path_for(doc)) not in orphans

    def test_a_wildcard_uid_cannot_serve_a_neighbours_file(self, store, gw):
        """`path_for`'s containment check does not catch this one.

        `<root>/<folder>/*.pdf` is genuinely under the root, so a uid of `*` gets
        past it and would then glob onto the file next to it — served by `fetch`
        and unlinked by `delete`. Uids this module mints are hex; a row read back
        from LabCore is whatever is in the table.
        """
        real = store.save("m1", "cert.pdf", PDF)
        forged = EquipmentDocument(
            uid="*", machine_uid="m1", filename="x.pdf", size_bytes=1,
            content_type="application/pdf", content_hash="",
            uploaded_at="2026-08-24T09:00:00")
        assert store.stored_path(forged) != store.path_for(real)
        assert not store.stored_path(forged).exists()

    def test_a_stranded_part_file_is_reported(self, store):
        """`<uid>.pdf.part` left behind by a crashed write is not the document."""
        doc = store.save("m1", "cert.pdf", PDF)
        part = store.path_for(doc).with_name(store.path_for(doc).name + ".part")
        part.write_bytes(b"half")
        stale = time.time() - PART_FILE_GRACE_SECONDS - 60
        os.utime(part, (stale, stale))
        assert str(part) in store.orphaned_files()

    def test_a_part_file_from_a_save_in_flight_is_not_an_orphan(self, store):
        """A sweep runs while the lab is using the lab.

        `_write_bytes` writes `<uid>.pdf.part` and renames it, so a 20 MB scan
        being uploaded right now exists, briefly, under exactly the name a
        crashed write leaves. Naming it in the report hands the person sweeping
        a live upload to delete — and this function's whole posture is that it
        reports and a person decides, which only holds if what it reports is
        actually deletable. Age is what tells the two apart: a write in flight is
        seconds old, a crashed one is however long ago the process died.
        """
        doc = store.save("m1", "cert.pdf", PDF)
        part = store.path_for(doc).with_name(store.path_for(doc).name + ".part")
        part.write_bytes(b"the first 4 MB of a 20 MB scan")
        assert str(part) not in store.orphaned_files()

    def test_the_part_file_grace_is_the_constant(self, store, monkeypatch):
        """Moving the constant must move the boundary, or it is documentation."""
        doc = store.save("m1", "cert.pdf", PDF)
        part = store.path_for(doc).with_name(store.path_for(doc).name + ".part")
        part.write_bytes(b"half")
        assert str(part) not in store.orphaned_files()
        monkeypatch.setattr(equipment_documents, "PART_FILE_GRACE_SECONDS", 0)
        assert str(part) in store.orphaned_files()

    def test_only_part_files_get_the_grace(self, store):
        """A stray `cert.pdf` is an orphan the moment it appears.

        The grace exists because `.part` is a name this module itself creates
        mid-write. Nothing else here is ever half-written under its final name —
        `os.replace` is atomic — so extending the grace to everything would just
        delay every real report by five minutes.
        """
        doc = store.save("m1", "cert.pdf", PDF)
        stray = store.path_for(doc).parent / "abandoned.pdf"
        stray.write_bytes(OTHER_PDF)
        assert str(stray) in store.orphaned_files()


class TestTheDisplayName:
    def test_a_jpeg_keeps_its_own_spelling(self, store):
        """`plate.jpeg` was stored as `plate.jpeg.jpg`.

        `.jpeg` and `.jpg` are the same type; only the canonical ON-DISK name is
        `.jpg`. The display name is what a browser saves it as, and doubling it
        makes the download look like something the server mangled.
        """
        doc = store.save("m1", "plate.jpeg", JPEG)
        assert doc.filename == "plate.jpeg"
        assert store.path_for(doc).suffix == ".jpg"

    def test_a_name_with_no_extension_still_gains_one(self, store):
        doc = store.save("m1", "scan", PDF, content_type="application/pdf")
        assert doc.filename == "scan.pdf"

    def test_the_download_header_survives_a_non_latin_1_name(self, store):
        """WSGI headers are latin-1. A German certificate is not.

        `filename="Prüfzertifikat.pdf"` raises inside the server on the way out —
        a 500 on the download of a document that stored perfectly.
        """
        doc = store.save("m1", "Prüfzertifikat-2026.pdf", PDF)
        header = content_disposition(doc.filename)
        header.encode("latin-1")            # must not raise
        assert "attachment" in header
        encoded = header.split("UTF-8''", 1)[1]
        assert urllib.parse.unquote(encoded) == doc.filename

    def test_the_header_cannot_be_injected_into(self, store):
        header = content_disposition('ce"rt\r\nX-Evil: yes.pdf')
        assert "\r" not in header and "\n" not in header
        assert header.count('"') == 2


class TestBoundedReads:
    def test_an_oversized_stream_is_refused_without_being_read_whole(self):
        """The ceiling is justified by memory and was applied after the read.

        `save()` takes bytes, so by the time the limit is checked the whole file
        is already in this process twice. `read_upload` is the bounded door the
        route phase uses: it stops at the limit and never holds the rest.
        """

        class Counting(io.RawIOBase):
            def __init__(self, size):
                self.left = size
                self.read_bytes = 0

            def read(self, n=-1):
                take = min(n if n and n > 0 else self.left, self.left)
                self.left -= take
                self.read_bytes += take
                return b"0" * take

        stream = Counting(MAX_DOCUMENT_BYTES * 4)
        with pytest.raises(DocumentRejected):
            read_upload(stream)
        assert stream.read_bytes <= MAX_DOCUMENT_BYTES + UPLOAD_CHUNK_BYTES

    def test_a_normal_upload_reads_through_intact(self):
        assert read_upload(io.BytesIO(PDF)) == PDF

    def test_the_bound_is_the_constant(self, monkeypatch):
        monkeypatch.setattr(equipment_documents, "MAX_DOCUMENT_BYTES", 32)
        with pytest.raises(DocumentRejected):
            read_upload(io.BytesIO(b"0" * 64))


class TestRetiringAnInstrument:
    def test_retiring_an_instrument_is_one_write(self, gw, tmp_path):
        """Three documents was three DELETEs at ~1.5 ops/sec, ahead of the floor."""
        rec = RecordingGateway(gw)
        store = EquipmentDocumentStore(rec, root=tmp_path / "documents")
        store.save("m1", "a.pdf", PDF)
        store.save("m1", "b.pdf", OTHER_PDF)
        store.save("m1", "c.png", PNG)
        keep = store.save("m2", "keep.pdf", PDF)
        rec.writes.clear()
        assert store.delete_for_machine("m1") == 3
        assert len(rec.writes) == 1
        assert store.documents("m1") == []
        assert [p for p in store.folder_for("m1").rglob("*") if p.is_file()] == []
        assert [d.uid for d in store.documents("m2")] == [keep.uid]
        assert store.path_for(keep).exists()

    def test_retiring_an_instrument_with_nothing_costs_nothing(self, gw, tmp_path):
        rec = RecordingGateway(gw)
        store = EquipmentDocumentStore(rec, root=tmp_path / "documents")
        assert store.delete_for_machine("m1") == 0
        assert rec.writes == []

    def test_retiring_during_an_outage_does_not_report_a_clean_sweep(
            self, gw, tmp_path):
        store = EquipmentDocumentStore(gw, root=tmp_path / "documents")
        doc = store.save("m1", "cert.pdf", PDF)
        store.gateway = BlippingGateway(gw)
        with pytest.raises(DocumentStoreError):
            store.delete_for_machine("m1")
        store.gateway.blipping = False
        assert [d.uid for d in store.documents("m1")] == [doc.uid]
        assert store.path_for(doc).exists()


# ── a write counts only when LabCore says it happened ───────────────────────

class TestAWriteIsNotDoneUntilItIsAcknowledged:
    """Absence of an "error" key is not success, and this store believed it was.

    `_run` tested `if isinstance(res, dict) and res.get("error")`. Everything
    else — `None`, `{}`, and the shape LabCore's queue actually sends when it is
    past 100 pending — fell through as "done". The consequences are the two
    outcomes the module docstring says it refuses:

      * `save()` returned a document, the route answered 201, the bytes sat on
        disk, and LabCore held zero rows. The tab never listed it. Nobody found
        out until the audit;
      * `delete()` returned True and unlinked the file while the row survived —
        a row with no file, listed in the tab, 404 when clicked. That is the
        orphan this module explicitly chose NOT to be able to create.

    The rule now comes from `labcore_result.confirm_write`, which is stated
    positively: a write happened only if the answer says so.
    """

    @pytest.fixture(params=[None,
                            {"error": "LabCore is busy, try again",
                             "busy": True, "retry_after": 4},
                            {"ok": False, "status": "rejected",
                             "pending": 100}],
                    ids=["no-answer", "busy-refusal", "queue-refusal"])
    def unacknowledged(self, request, gw, tmp_path):
        """The answers that mean nothing was written.

        `{}` used to be in this list. It was removed when the shared rule was
        corrected, not loosened: nothing records what real LabCore answers to a
        write that SUCCEEDS, so an answer carrying no failure signal has to be
        accepted — demanding an acknowledgement we have never seen would fail
        every write in the lab. See
        tests/test_labcore_result.py::TestAgainstWhatLabCoreActuallySends.

        The evidenced busy dict takes its place, so this fixture now drives the
        one refusal shape the lab has actually measured."""
        store = EquipmentDocumentStore(gw, root=tmp_path / "documents")
        return store, UnacknowledgingGateway(gw, answer=request.param)

    def test_an_unacknowledged_insert_is_not_a_saved_document(
            self, unacknowledged, gw):
        store, mute = unacknowledged
        store.gateway = mute
        with pytest.raises(DocumentStoreError):
            store.save("m1", "cert.pdf", PDF)
        store.gateway = gw
        assert store.documents("m1") == []

    def test_an_unacknowledged_insert_leaves_no_file_behind(
            self, unacknowledged):
        """A file no row mentions is the survivable orphan — but only when the
        tidy-up itself fails. On this path it must not be reached at all."""
        store, mute = unacknowledged
        store.gateway = mute
        with pytest.raises(DocumentStoreError):
            store.save("m1", "cert.pdf", PDF)
        assert [p for p in store.root.rglob("*") if p.is_file()] == []

    def test_an_unacknowledged_delete_keeps_the_file(self, unacknowledged, gw):
        """Row first, then bytes. If the row did not go, the bytes must stay.

        Unlinking here produces exactly the failure the module says it will not
        create: the certificate is still listed and cannot be produced.
        """
        store, mute = unacknowledged
        doc = store.save("m1", "cert.pdf", PDF)
        store.gateway = UnacknowledgingGateway(gw, answer=mute.answer,
                                               refuse_prefix="DELETE")
        with pytest.raises(DocumentStoreError):
            store.delete(doc.uid)
        assert store.path_for(doc).exists()
        assert [d.uid for d in store.documents("m1")] == [doc.uid]

    def test_an_unacknowledged_retirement_keeps_every_file(
            self, unacknowledged, gw):
        store, mute = unacknowledged
        a = store.save("m1", "a.pdf", PDF)
        b = store.save("m1", "b.pdf", OTHER_PDF)
        store.gateway = UnacknowledgingGateway(gw, answer=mute.answer,
                                               refuse_prefix="DELETE")
        with pytest.raises(DocumentStoreError):
            store.delete_for_machine("m1")
        assert store.path_for(a).exists() and store.path_for(b).exists()
        assert len(store.documents("m1")) == 2

    def test_an_acknowledged_write_that_matched_nothing_is_still_a_write(
            self, gw, tmp_path):
        """`rows_affected: 0` is an acknowledgement, not a failure.

        Deleting a row a concurrent request already deleted DID happen; it just
        matched nothing. Treating that as a refusal would raise on an operator's
        second click and invite a third.
        """
        store = EquipmentDocumentStore(gw, root=tmp_path / "documents")
        doc = store.save("m1", "cert.pdf", PDF)
        path = store.path_for(doc)
        # The row was read, then a concurrent request deleted it before ours ran:
        # LabCore acknowledges a DELETE that matched nothing.
        store.gateway = UnacknowledgingGateway(
            gw, answer={"ok": True, "rows_affected": 0}, refuse_prefix="DELETE")
        assert store.delete(doc.uid) is True
        assert not path.exists()


class TestTheRuleComesFromOnePlace:
    """One question, one answer, in `labcore_result`.

    This module invented its own version of both halves — a private
    missing-table regex and a private "no error key means it worked" — and got
    the write half wrong. Two rules in a codebase means the next fix lands in
    one of them, so these tests hold the module to the shared one rather than to
    a copy that happens to agree today.
    """

    def test_the_missing_table_rule_is_the_shared_one(self):
        assert equipment_documents.is_missing_table is \
            labcore_result.is_missing_table

    def test_the_module_does_not_read_answers_itself(self):
        """No second interpretation of a gateway answer anywhere in the file.

        `res.get("error")` and `res.get("rows")` are how the private rules were
        spelled. If either comes back, the shared rule has been forked again.
        """
        source = open(equipment_documents.__file__, encoding="utf-8").read()
        assert '.get("error")' not in source
        assert '.get("rows")' not in source
        assert "re.compile" not in source or "no such table" not in source

    def test_a_read_outage_carries_the_shared_reason(self, gw, tmp_path):
        """Translated, not swallowed: the shared class stays reachable.

        A route that wants to say "try again in a moment" has to be able to tell
        a blip from a refusal, and `DocumentStoreError` alone cannot. So the
        cause is preserved rather than dropped.
        """
        store = EquipmentDocumentStore(BlippingGateway(gw),
                                       root=tmp_path / "documents")
        with pytest.raises(DocumentStoreError) as caught:
            store.documents("m1")
        assert isinstance(caught.value.__cause__, LabCoreUnavailable)

    def test_a_refused_write_carries_the_shared_reason(self, gw, tmp_path):
        store = EquipmentDocumentStore(RefusingGateway(gw),
                                       root=tmp_path / "documents")
        with pytest.raises(DocumentStoreError) as caught:
            store.save("m1", "cert.pdf", PDF)
        assert isinstance(caught.value.__cause__, LabCoreRefused)

    def test_a_missing_table_is_the_news_on_the_way_to_a_write(
            self, tmp_path):
        """The dedupe read is a write path, so it does not swallow the absence.

        On an unwired server the dedupe read used to come back empty, the bytes
        were written, the INSERT then failed on the same missing table, and the
        bytes were unlinked again — a folder created and a file written and
        removed to learn something the read already knew. `missing_ok=False` is
        exactly this case: a missing table IS the news here.
        """
        bare = FakeLabCoreGateway()
        store = EquipmentDocumentStore(bare, root=tmp_path / "documents")
        with pytest.raises(DocumentStoreError) as caught:
            store.save("m1", "cert.pdf", PDF)
        assert "already stored" in str(caught.value)
        assert not store.root.exists()


# ── whitespace, on both sides of the wire contract ──────────────────────────

class TestWhitespaceIsNotAKindOfFile:
    def test_a_pdf_whose_name_has_a_trailing_space_is_a_pdf(self, store):
        """A copied name, a drag from Explorer, a form field with a space.

        `os.path.splitext("cert.pdf ")` is `".pdf "`, which is in no table here,
        so a genuine certificate was refused with a message telling the operator
        it is not a kind of document LEM stores. There is nothing they can do
        with that sentence, because it is not true.
        """
        doc = store.save("m1", "cert.pdf ", PDF)
        assert doc.content_type == "application/pdf"
        assert doc.filename == "cert.pdf"

    def test_leading_and_trailing_whitespace_both_go(self, store):
        for name in ("  cert.pdf", "cert.pdf\n", "\tcert.pdf\t"):
            doc = store.save("m1", name, OTHER_PDF)
            assert doc.filename == "cert.pdf"
            assert doc.content_type == "application/pdf"

    def test_whitespace_does_not_smuggle_a_source_file_past_the_accept_list(
            self, store):
        """The stripping must not become a way in."""
        with pytest.raises(DocumentRejected):
            store.save("m1", "payload.py ", PDF, content_type="application/pdf")


class TestThePaddedWireContract:
    """`machine_uid` arrives over HTTP, so it arrives with whatever is around it.

    `save()` stripped it and the reads did not, so a document filed by a form
    that sent `"m1 "` was stored under `m1` and then invisible to
    `documents("m1 ")` — and, worse, invisible to the dedupe check, which is how
    the same certificate ends up in the tab twice with nothing to collapse it.
    """

    def test_a_padded_uid_lists_the_same_documents(self, store):
        doc = store.save("m1", "cert.pdf", PDF)
        assert [d.uid for d in store.documents(" m1 ")] == [doc.uid]

    def test_a_padded_uid_at_save_time_is_found_by_the_clean_one(self, store):
        doc = store.save(" m1 ", "cert.pdf", PDF)
        assert doc.machine_uid == "m1"
        assert [d.uid for d in store.documents("m1")] == [doc.uid]

    def test_a_padded_uid_still_dedupes(self, store):
        first = store.save("m1", "cert.pdf", PDF)
        again = store.save(" m1 ", "cert.pdf", PDF)
        assert again.uid == first.uid
        assert len(store.documents("m1")) == 1

    def test_a_padded_document_uid_is_the_same_document(self, store):
        doc = store.save("m1", "cert.pdf", PDF)
        got = store.get(f" {doc.uid} ")
        assert got is not None and got.uid == doc.uid
        assert store.fetch(f" {doc.uid} ")[1] == PDF

    def test_the_badge_counts_a_padded_uid_once(self, store):
        store.save("m1", "cert.pdf", PDF)
        assert document_counts_by_machine(store, [" m1 ", "m1"]) == {"m1": 1}

    def test_a_padded_uid_retires_the_same_instrument(self, store):
        store.save("m1", "cert.pdf", PDF)
        assert store.delete_for_machine(" m1 ") == 1
        assert store.documents("m1") == []

    def test_a_padded_uid_names_the_same_folder(self, store):
        """`storage_slug` hashes the uid, and it hashed the padding with it.

        The readable half of the folder name was stripped and the sha1 suffix was
        not, so `folder_for(" m1 ")` pointed at a folder that does not exist while
        `folder_for("m1")` held the certificates. Every caller inside the store
        cleans first now, so nothing is stored under a padded name — but this is a
        public method, and a route that hands it a raw form field would otherwise
        get an empty folder back and report an instrument with no documents.
        """
        doc = store.save("m1", "cert.pdf", PDF)
        assert store.folder_for(" m1 ") == store.folder_for("m1")
        assert store.path_for(doc).parent == store.folder_for(" m1 ")


class TestTheSweepNeverNamesTheFilesToKeep:
    """`orphaned_files()` builds a list of paths a person is about to delete.

    Its own docstring already states the rule — "a list built from rows nobody
    could read would name exactly the files to keep" — and the outage case was
    handled. The missing-table case was not: it went through the shared read
    with `missing_ok=True`, got `[]`, found nothing accounted for, and returned
    every certificate in the lab as an orphan.

    That is the one place the shared rule's honest degradation is wrong. Absent
    table means "no documents are recorded", which for a sweep means "everything
    on disk is unaccounted for" — a true statement whose only use is destructive.
    """

    def test_a_missing_table_refuses_the_sweep(self, store, gw):
        doc = store.save("optimpp-1", "cert.pdf", PDF, uploaded_by="ryan")
        assert store.orphaned_files() == []
        gw.sql("DROP TABLE lem_equipment_documents")
        with pytest.raises(DocumentStoreError):
            store.orphaned_files()

    def test_the_live_file_is_never_listed_as_an_orphan(self, store, gw):
        doc = store.save("optimpp-1", "cert.pdf", PDF, uploaded_by="ryan")
        live = str(store.path_for(doc))
        gw.sql("DROP TABLE lem_equipment_documents")
        try:
            found = store.orphaned_files()
        except DocumentStoreError:
            return                      # refusing is the correct behaviour
        assert live not in found, "the sweep named a file that is still recorded"
