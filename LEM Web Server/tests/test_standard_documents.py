"""Certificates attached to a QC STANDARD — bytes on disk, metadata in LabCore.

Ryan: "leave that alone for now, we have them separated for now and will bind
them later, build out the infrastructure to upload the certificate into the
standard itself."

A QC standard's certificate — the COA or CRM certificate PDF — is the evidence
for the values that standard asserts, and PJLA are in the building in September
2026. So this is `equipment_documents.py`'s split applied to `QcSample`: the
file lands on the server's own disk and LabCore holds one small row describing
it, because a 5 MB PDF is ~6.7 MB of base64 in a single SQL statement and
LabCore's queue serialises at ~1.5 writes/sec and refuses past ~100 pending.

What is pinned down here is not "does it save a file" — `test_equipment_
documents.py` already holds that shape, and the primitives are IMPORTED rather
than re-derived, so the tests that matter here are the ones about what is
DIFFERENT:

* **`lem_qc_samples` is keyed by `name`, a human string.** A standard gets
  renamed — a relabelled lot, a typo fixed, a supplier's spelling adopted — and
  a certificate whose on-disk path was derived from that name is a file nothing
  can compute any more. The tab reads empty, `fetch` reports the file missing,
  and the sweep names every live certificate as deletable, in the one week of
  the year somebody is actually looking. So the path hangs off a `storage_key`
  written once and never updated, and a rename is one UPDATE that moves no
  bytes at all;
* **a certificate expires, and an expired one is a finding.** "Expired" and
  "expiring soon" have to be answerable for the whole library in ONE read, and
  that read may not degrade to empty: a report that says "nothing expires"
  during a blip is the failure the report exists to prevent;
* **the two document roots must never nest.** `equipment_documents.
  orphaned_files()` rglobs its entire root and hands the result to a person
  whose next step is deleting them. A certificates folder living underneath it
  would be reported, in full, as sweepable;
* **the numeric binding is NOT here.** No `cert_value`, no `cert_uncertainty`,
  no `cert_k`, and nothing that reads a number out of a PDF. That is a later,
  deliberate step and this suite is the tripwire that keeps a helpful agent from
  anticipating it.
"""
import ast
import io
import os
import sys
import time
from datetime import date, datetime

import pytest

from labcore_gateway import FakeLabCoreGateway

import equipment_documents
import labcore_result
import qc_samples
import snapshot_service
import standard_documents
from equipment_documents import DocumentError, DocumentRejected, DocumentStoreError
from labcore_result import LabCoreRefused, LabCoreUnavailable
from standard_documents import (
    EXPIRY_WARNING_DAYS,
    STANDARD_DOCUMENTS_DDL,
    STANDARD_DOCUMENTS_DIR_ENV,
    CertificateRejected,
    CertificateStoreError,
    StandardCertificate,
    StandardCertificateStore,
    certificate_status,
    default_certificates_root,
    expiry_report,
    normalise_expiry,
)

# The gateways and the queue-traffic guard are IMPORTED from the documents
# suite rather than copied. They are the same claims about the same queue, and
# the guard there is already proved to bite on a leak
# (`test_the_guard_bites_when_the_bytes_do_go_through`); a second copy is how
# one of them gets tightened and the other quietly stops meaning anything.
from test_equipment_documents import (  # noqa: E402
    BIG_PDF,
    JPEG,
    OTHER_PDF,
    PDF,
    PNG,
    BlippingGateway,
    RecordingGateway,
    RefusingGateway,
    UnacknowledgingGateway,
    assert_no_document_bytes_reached_the_queue,
)


def _module_code_without_its_docstring() -> str:
    """The module's CODE, with its own docstring removed.

    `test_no_invented_protocol.py` sets the precedent these two tripwires
    follow: a document that has to tell the story is allowed to NAME the thing
    it warns about, while a line of code is not. The module docstring says
    which numeric fields nobody may add and that no Flask routes live here, and
    both of those sentences are the entire point of it — banning the words from
    the file would delete the warning to enforce it.
    """
    source = open(standard_documents.__file__, encoding="utf-8").read()
    doc = ast.get_docstring(ast.parse(source), clean=False) or ""
    return source.replace(doc, "", 1)


# ── fixtures ────────────────────────────────────────────────────────────────

# The names are the shape a lab actually types: a lot with punctuation in it,
# and the near-miss respelling somebody produces when they retype it.
STANDARD = "Diesel - AO25"
RENAMED = "Diesel AO-25"


@pytest.fixture
def gw():
    """A LabCore fake with the certificates table already declared.

    The store deliberately does NOT declare its own schema — DDL belongs in
    `snapshot_service.SCHEMA_DDL`, applied once at boot. Applying the module's
    own constant here is exactly what that boot does, and nothing more.
    """
    g = FakeLabCoreGateway()
    g.sql(STANDARD_DOCUMENTS_DDL)
    return g


@pytest.fixture
def store(gw, tmp_path):
    return StandardCertificateStore(gw, root=tmp_path / "certificates")


# ── where the bytes live ────────────────────────────────────────────────────

class TestTheCertificatesRoot:
    def test_the_root_is_configurable(self, gw, tmp_path):
        here = tmp_path / "elsewhere"
        assert StandardCertificateStore(gw, root=here).root == here

    def test_the_default_root_comes_from_the_environment(self, monkeypatch):
        monkeypatch.setenv(STANDARD_DOCUMENTS_DIR_ENV, "/srv/lem-certs")
        assert str(default_certificates_root()) == os.path.abspath("/srv/lem-certs")

    def test_the_default_root_falls_back_to_the_data_dir(self, monkeypatch):
        monkeypatch.delenv(STANDARD_DOCUMENTS_DIR_ENV, raising=False)
        monkeypatch.setenv(equipment_documents.DATA_DIR_ENV, "/srv/lem-data")
        root = default_certificates_root()
        assert str(root).startswith(os.path.abspath("/srv/lem-data"))

    def test_the_two_document_roots_never_nest(self, monkeypatch):
        """The certificates root is a SIBLING of the equipment documents root.

        `equipment_documents.orphaned_files()` rglobs its whole root and hands
        the result to a person whose next step is deleting them. Nesting the
        certificates underneath it would put every COA in the lab on that list,
        accounted for by nothing, on the sweep somebody runs to tidy up.
        """
        monkeypatch.delenv(STANDARD_DOCUMENTS_DIR_ENV, raising=False)
        monkeypatch.delenv(equipment_documents.DOCUMENTS_DIR_ENV, raising=False)
        monkeypatch.setenv(equipment_documents.DATA_DIR_ENV, "/srv/lem-data")
        certs = default_certificates_root()
        docs = equipment_documents.default_documents_root()
        assert certs != docs
        assert docs not in certs.parents
        assert certs not in docs.parents

    def test_the_root_is_created_on_first_save_not_on_construction(
            self, gw, tmp_path):
        root = tmp_path / "certificates"
        store = StandardCertificateStore(gw, root=root)
        assert not root.exists()
        store.save(STANDARD, "coa.pdf", PDF)
        assert root.exists()

    def test_nothing_lands_outside_the_root(self, store, tmp_path):
        store.save(STANDARD, "coa.pdf", PDF)
        written = [p for p in tmp_path.rglob("*") if p.is_file()]
        assert written
        for path in written:
            assert store.root in path.parents


# ── the path is DERIVED, and it does not follow the name ────────────────────

class TestDerivedPaths:
    def test_the_path_is_built_from_the_uid_and_the_resolved_type(self, store):
        cert = store.save(STANDARD, "Certificate of Analysis 2026.pdf", PDF)
        path = store.path_for(cert)
        assert path.name == f"{cert.uid}.pdf"
        assert "Certificate" not in str(path)

    def test_a_traversal_in_the_filename_cannot_escape(self, store):
        cert = store.save(STANDARD, "../../web_app.pdf", PDF)
        assert store.root in store.path_for(cert).parents
        assert ".." not in str(store.path_for(cert))
        assert "/" not in cert.filename and "\\" not in cert.filename

    def test_the_traversal_really_does_not_reach_the_neighbour(self, gw, tmp_path):
        victim = tmp_path / "web_app.pdf"
        victim.write_text("# the real thing\n")
        store = StandardCertificateStore(gw, root=tmp_path / "certs" / "deep")
        store.save(STANDARD, "../../web_app.pdf", PDF)
        assert victim.read_text() == "# the real thing\n"

    def test_a_traversing_standard_name_cannot_escape(self, store):
        """The name is typed by a person into the QC library, so it is exactly
        as trustworthy as an uploaded filename."""
        cert = store.save("../../..", "coa.pdf", PDF)
        assert store.root in store.path_for(cert).parents
        assert ".." not in str(store.path_for(cert).relative_to(store.root))

    def test_standard_names_that_sanitise_alike_get_different_folders(self, store):
        """Slugging alone collides, and a shared folder makes one standard's
        retirement take the other's certificate with it."""
        a = store.save("Diesel/AO25", "coa.pdf", PDF)
        b = store.save("Diesel:AO25", "coa.pdf", OTHER_PDF)
        assert store.path_for(a).parent != store.path_for(b).parent

    def test_a_forged_row_cannot_point_outside_the_root(self, store):
        forged = StandardCertificate(
            uid="../../../evil", standard_name=STANDARD,
            storage_key=STANDARD, filename="x.pdf", size_bytes=1,
            content_type="application/pdf", content_hash="",
            uploaded_at="2026-08-26T09:00:00")
        with pytest.raises(DocumentStoreError):
            store.path_for(forged)

    def test_a_forged_storage_key_cannot_point_outside_the_root(self, store):
        """`storage_key` is read back out of LabCore, so it is a path component
        that arrives from a table rather than from this module's own hand."""
        forged = StandardCertificate(
            uid="abc123", standard_name=STANDARD, storage_key="../../..",
            filename="x.pdf", size_bytes=1, content_type="application/pdf",
            content_hash="", uploaded_at="2026-08-26T09:00:00")
        assert store.root in store.path_for(forged).parents


# ── the rename decision, which is the whole point of the module ─────────────

class TestARenameMovesNoBytes:
    """The re-filing PRIMITIVE, exercised through `rename_standard`.

    Read `TestTheRenameThisApplicationActuallyPerforms` first: there is no
    rename verb in this application and this method has no caller. What is
    pinned here is the property the primitive has to hold whenever it IS
    called — by `repoint_certificates`, which is the verb that repairs a
    rename, and by whatever real rename verb arrives later.

    `lem_qc_samples` is keyed by `name`, a human string. `QcSampleStore.save`
    upserts on it and `changeover` mints a whole new lot under a new one, so
    the name a certificate was filed under is not a thing this module may treat
    as stable. Deriving the folder from it would mean re-filing silently
    detaches every certificate the standard has: the tab reads "no certificate
    on file" about a PDF sitting on disk, `fetch` reports it missing, and
    `orphaned_files()` names it as deletable.

    So the on-disk path hangs off `storage_key` — written once at save time,
    never updated — and the current name is a column. Re-filing is one UPDATE.
    """

    def test_a_rename_keeps_the_certificate(self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF)
        before = store.path_for(cert)
        assert store.rename_standard(STANDARD, RENAMED) == 1
        listed = store.certificates(RENAMED)
        assert [c.uid for c in listed] == [cert.uid]
        assert store.certificates(STANDARD) == []
        assert store.fetch(cert.uid)[1] == PDF
        assert store.path_for(listed[0]) == before

    def test_a_rename_does_not_touch_the_disk_at_all(self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF)
        before = {str(p): p.read_bytes()
                  for p in store.root.rglob("*") if p.is_file()}
        store.rename_standard(STANDARD, RENAMED)
        after = {str(p): p.read_bytes()
                 for p in store.root.rglob("*") if p.is_file()}
        assert after == before

    def test_the_storage_key_is_not_the_current_name(self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF)
        store.rename_standard(STANDARD, "Something Else Entirely")
        got = store.get(cert.uid)
        assert got.standard_name == "Something Else Entirely"
        assert got.storage_key == STANDARD
        assert "something" not in str(store.path_for(got)).lower()

    def test_a_rename_is_ONE_statement_however_many_certificates(
            self, gw, tmp_path):
        """A per-row rename loop is N statements at ~1.5 ops/sec, and a refusal
        halfway leaves the standard's certificates split across two names —
        half of them invisible under each. One UPDATE cannot half-happen."""
        rec = RecordingGateway(gw)
        store = StandardCertificateStore(rec, root=tmp_path / "certificates")
        store.save(STANDARD, "a.pdf", PDF)
        store.save(STANDARD, "b.pdf", OTHER_PDF)
        store.save(STANDARD, "c.png", PNG)
        rec.writes.clear()
        assert store.rename_standard(STANDARD, RENAMED) == 3
        assert len(rec.writes) == 1

    def test_a_certificate_saved_after_the_rename_is_found_with_the_older_ones(
            self, store):
        old = store.save(STANDARD, "coa.pdf", PDF)
        store.rename_standard(STANDARD, RENAMED)
        new = store.save(RENAMED, "coa-2027.pdf", OTHER_PDF)
        assert {c.uid for c in store.certificates(RENAMED)} == {old.uid, new.uid}

    def test_renaming_a_standard_with_nothing_costs_nothing(self, gw, tmp_path):
        rec = RecordingGateway(gw)
        store = StandardCertificateStore(rec, root=tmp_path / "certificates")
        assert store.rename_standard(STANDARD, RENAMED) == 0
        assert rec.writes == []

    def test_a_rename_onto_an_existing_standard_is_refused(self, store):
        """Two standards' certificates merged under one name cannot be
        unmerged: `storage_key` says which folder each came from, but nothing
        says which standard each belonged to."""
        store.save(STANDARD, "a.pdf", PDF)
        store.save(RENAMED, "b.pdf", OTHER_PDF)
        with pytest.raises(CertificateRejected):
            store.rename_standard(STANDARD, RENAMED)
        assert len(store.certificates(STANDARD)) == 1
        assert len(store.certificates(RENAMED)) == 1

    def test_a_rename_during_an_outage_is_not_a_quiet_no_op(self, gw, tmp_path):
        """A `0` from here means "that standard has no certificates".

        Said during a blip it is a lie the caller cannot detect, and the caller
        is somebody repairing a rename who is about to report it repaired.
        """
        store = StandardCertificateStore(gw, root=tmp_path / "certificates")
        cert = store.save(STANDARD, "coa.pdf", PDF)
        store.gateway = BlippingGateway(gw)
        with pytest.raises(CertificateStoreError):
            store.rename_standard(STANDARD, RENAMED)
        store.gateway.blipping = False
        assert [c.uid for c in store.certificates(STANDARD)] == [cert.uid]


# ── the rename this application actually performs ───────────────────────────

class TestTheRenameThisApplicationActuallyPerforms:
    """There is no rename verb. `QcSampleStore` has `save` and `delete`.

    `templates/floor.html`, `templates/stations.html` and `web_app.py` all say
    the same thing in the same words: *a rename is save-new-then-delete-old*.
    So `rename_standard` has no caller and cannot have one, and a real rename
    leaves every certificate filed against a standard that no longer exists —
    invisible to `certificates(new_name)`, with the PDF still on disk.

    Two properties are held here, and they are the whole point of the module:
    a certificate is never silently lost, and a rename never silently destroys
    one.
    """

    def test_a_real_rename_leaves_the_certificates_behind(self, store):
        """Not a bug in this store — a fact about the application it serves.

        Pinned so nobody re-reads `rename_standard` as the thing that stops it
        happening. What stops it is `orphaned_certificates` plus a repoint.
        """
        cert = store.save(STANDARD, "coa.pdf", PDF)
        # save-new-then-delete-old, as web_app.py:4141 performs it. Nothing in
        # that sequence touches this table.
        assert store.certificates(RENAMED) == []
        assert [c.uid for c in store.certificates(STANDARD)] == [cert.uid]

    def test_an_orphaned_certificate_is_findable(self, store):
        """The whole library was previously unable to answer this question."""
        cert = store.save(STANDARD, "coa.pdf", PDF)
        orphans = store.orphaned_certificates([RENAMED, "Some Other Lot"])
        assert [c.uid for c in orphans] == [cert.uid]

    def test_a_certificate_of_a_live_standard_is_not_an_orphan(self, store):
        store.save(STANDARD, "coa.pdf", PDF)
        assert store.orphaned_certificates([STANDARD]) == []

    def test_the_live_names_are_matched_the_way_they_are_stored(self, store):
        """`clean_name` strips; a name out of a form field carries whitespace,
        and a padded one must not make every certificate look orphaned."""
        store.save(STANDARD, "coa.pdf", PDF)
        assert store.orphaned_certificates([f" {STANDARD} "]) == []

    def test_orphan_hunting_costs_one_read(self, gw, tmp_path):
        rec = RecordingGateway(gw)
        store = StandardCertificateStore(rec, root=tmp_path / "certificates")
        store.save(STANDARD, "a.pdf", PDF)
        store.save(RENAMED, "b.pdf", OTHER_PDF)
        rec.reads.clear()
        store.orphaned_certificates([STANDARD])
        assert len(rec.reads) == 1

    def test_it_does_not_reach_for_the_qc_sample_store_itself(self):
        """The dependency direction stays as it is: this module knows nothing
        about `qc_samples`, so the standards are an ARGUMENT."""
        tree = ast.parse(open(standard_documents.__file__,
                              encoding="utf-8").read())
        imported, named = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported.add((node.module or "").split(".")[0])
            elif isinstance(node, ast.Name):
                named.add(node.id)
            elif isinstance(node, ast.Attribute):
                named.add(node.attr)
        assert "qc_samples" not in imported
        # Prose may NAME it — the docstrings explain the store this module is
        # deliberately not coupled to. Code may not touch it.
        assert "QcSampleStore" not in named

    def test_no_live_names_at_all_is_refused_rather_than_answered(self, store):
        """`list_samples()` degrades to `[]` on a missing table.

        Handed that, a report that answered "every certificate in the lab is
        orphaned" would be a lie with a delete button next to it. `None` is the
        shape that arrives from a caller that did not really ask.
        """
        store.save(STANDARD, "coa.pdf", PDF)
        with pytest.raises(CertificateStoreError):
            store.orphaned_certificates(None)

    def test_an_outage_is_not_a_clean_bill_of_health(self, gw, tmp_path):
        store = StandardCertificateStore(gw, root=tmp_path / "certificates")
        store.save(STANDARD, "coa.pdf", PDF)
        store.gateway = BlippingGateway(gw)
        with pytest.raises(CertificateStoreError):
            store.orphaned_certificates([RENAMED])

    def test_a_missing_table_does_not_report_nothing_orphaned(self, tmp_path):
        bare = FakeLabCoreGateway()
        store = StandardCertificateStore(bare, root=tmp_path / "certificates")
        with pytest.raises(CertificateStoreError):
            store.orphaned_certificates([STANDARD])


class TestRepointingRepairsARename:
    """The repair verb, and it is the primitive `rename_standard` already was.

    Moving certificates from a name that has vanished to the one that replaced
    it is one UPDATE and no file operations — exactly what `rename_standard`
    does. So there is one implementation and two names, and the honest one is
    the one that says what the operation is FOR.
    """

    def test_repointing_puts_the_certificates_back_on_the_live_standard(
            self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF)
        assert store.repoint_certificates(STANDARD, RENAMED) == 1
        assert [c.uid for c in store.certificates(RENAMED)] == [cert.uid]
        assert store.orphaned_certificates([RENAMED]) == []
        assert store.fetch(cert.uid)[1] == PDF

    def test_repointing_moves_no_bytes(self, store):
        store.save(STANDARD, "coa.pdf", PDF)
        before = {str(p): p.read_bytes()
                  for p in store.root.rglob("*") if p.is_file()}
        store.repoint_certificates(STANDARD, RENAMED)
        after = {str(p): p.read_bytes()
                 for p in store.root.rglob("*") if p.is_file()}
        assert after == before

    def test_repointing_is_one_statement_however_many_certificates(
            self, gw, tmp_path):
        rec = RecordingGateway(gw)
        store = StandardCertificateStore(rec, root=tmp_path / "certificates")
        store.save(STANDARD, "a.pdf", PDF)
        store.save(STANDARD, "b.pdf", OTHER_PDF)
        store.save(STANDARD, "c.png", PNG)
        rec.writes.clear()
        assert store.repoint_certificates(STANDARD, RENAMED) == 3
        assert len(rec.writes) == 1

    def test_rename_standard_is_the_same_operation_under_the_other_name(
            self, store):
        """One implementation. Two names that must never drift apart."""
        cert = store.save(STANDARD, "coa.pdf", PDF)
        assert store.rename_standard(STANDARD, RENAMED) == 1
        assert [c.uid for c in store.certificates(RENAMED)] == [cert.uid]

    def test_repointing_onto_a_standard_that_has_its_own_is_refused(
            self, store):
        store.save(STANDARD, "a.pdf", PDF)
        store.save(RENAMED, "b.pdf", OTHER_PDF)
        with pytest.raises(CertificateRejected):
            store.repoint_certificates(STANDARD, RENAMED)
        assert len(store.certificates(STANDARD)) == 1
        assert len(store.certificates(RENAMED)) == 1

    def test_the_merge_is_available_when_a_person_says_it_is_one_lot(
            self, store):
        """After a rename somebody often uploads the COA again under the new
        name before anyone notices the old ones. Refusing outright would leave
        the orphans unrepairable, so the merge is opt-in and never a default.
        """
        old = store.save(STANDARD, "a.pdf", PDF)
        new = store.save(RENAMED, "b.pdf", OTHER_PDF)
        assert store.repoint_certificates(STANDARD, RENAMED, merge=True) == 1
        assert {c.uid for c in store.certificates(RENAMED)} == {old.uid, new.uid}


class TestRetiringAStandardsEvidenceIsHardToDoByAccident:
    """`delete_for_standard` is the loaded gun in this module.

    A rename ENDS in `DELETE /api/qc-samples`. CLAUDE.md's own precedent —
    "Retiring a machine now also forgets its level and its documents" — points
    whoever wires the routes straight at this method, and hooking it there
    would make renaming a standard destroy the lab's certificates.

    The store cannot tell a retirement from the back half of a rename; only the
    caller can. So the assertion is required at the call site, and the default
    is a refusal rather than a deletion.
    """

    def test_it_cannot_be_called_without_asserting_the_lot_is_gone(self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF)
        with pytest.raises(TypeError):
            store.delete_for_standard(STANDARD)
        assert [c.uid for c in store.certificates(STANDARD)] == [cert.uid]
        assert store.path_for(cert).exists()

    def test_a_caller_that_cannot_assert_it_gets_a_refusal_not_a_deletion(
            self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF)
        with pytest.raises(CertificateRejected):
            store.delete_for_standard(STANDARD, retired=False)
        assert [c.uid for c in store.certificates(STANDARD)] == [cert.uid]
        assert store.path_for(cert).exists()

    def test_the_refusal_names_the_repair_verb(self, store):
        store.save(STANDARD, "coa.pdf", PDF)
        with pytest.raises(CertificateRejected) as caught:
            store.delete_for_standard(STANDARD, retired=False)
        assert "repoint_certificates" in str(caught.value)

    def test_the_docstring_names_the_route_it_must_not_be_wired_into(self):
        doc = StandardCertificateStore.delete_for_standard.__doc__ or ""
        assert "DELETE /api/qc-samples" in doc
        assert "repoint_certificates" in doc

    def test_the_rename_docstring_does_not_claim_a_verb_that_does_not_exist(
            self):
        """A docstring describing a world the code does not live in is worse
        than no docstring; this one cost a reviewer an hour."""
        doc = (StandardCertificateStore.rename_standard.__doc__ or "").lower()
        assert "save-new-then-delete-old" in doc
        assert "no rename verb" in doc


# ── save, list, fetch, delete ───────────────────────────────────────────────

class TestSaveListFetch:
    def test_round_trip(self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF, uploaded_by="ryan",
                          now=datetime(2026, 8, 26, 9, 0, 0))
        assert isinstance(cert, StandardCertificate)
        assert cert.standard_name == STANDARD
        assert cert.filename == "coa.pdf"
        assert cert.size_bytes == len(PDF)
        assert cert.content_type == "application/pdf"
        assert cert.uploaded_by == "ryan"
        assert cert.uploaded_at.startswith("2026-08-26T09:00")
        assert cert.content_hash

    def test_the_bytes_are_on_disk(self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF)
        assert store.path_for(cert).read_bytes() == PDF

    def test_the_metadata_row_is_in_labcore(self, store, gw):
        store.save(STANDARD, "coa.pdf", PDF)
        res = gw.read_sql("SELECT * FROM lem_standard_documents")
        assert len(res["rows"]) == 1
        assert res["rows"][0]["standard_name"] == STANDARD

    def test_certificates_are_listed_for_their_own_standard_only(self, store):
        store.save(STANDARD, "a.pdf", PDF)
        store.save(RENAMED, "b.pdf", OTHER_PDF)
        assert [c.filename for c in store.certificates(STANDARD)] == ["a.pdf"]
        assert [c.filename for c in store.certificates(RENAMED)] == ["b.pdf"]

    def test_listing_is_newest_first(self, store):
        store.save(STANDARD, "old.pdf", PDF, now=datetime(2026, 1, 1))
        store.save(STANDARD, "new.pdf", OTHER_PDF, now=datetime(2026, 8, 1))
        assert [c.filename for c in store.certificates(STANDARD)] == \
            ["new.pdf", "old.pdf"]

    def test_fetch_returns_the_metadata_and_the_bytes(self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF)
        got, data = store.fetch(cert.uid)
        assert got.uid == cert.uid and data == PDF

    def test_get_on_an_unknown_uid_is_None(self, store):
        assert store.get("nope") is None

    def test_a_certificate_with_no_standard_is_refused(self, store):
        with pytest.raises(CertificateRejected):
            store.save("   ", "coa.pdf", PDF)

    def test_a_padded_standard_name_is_the_same_standard(self, store):
        """`QcSampleStore.save` strips the name before it becomes the primary
        key, so a certificate filed under a padded one would hang off a
        standard the rest of the app does not have."""
        cert = store.save(f"  {STANDARD}  ", "coa.pdf", PDF)
        assert cert.standard_name == STANDARD
        assert [c.uid for c in store.certificates(f" {STANDARD} ")] == [cert.uid]

    def test_the_name_is_matched_case_sensitively(self, store):
        """`lem_qc_samples` compares names exactly — `changeover` does
        `t.sample == old_name`. Folding case here would attach a certificate to
        a standard the rest of the app thinks is a different one."""
        store.save(STANDARD, "coa.pdf", PDF)
        assert store.certificates(STANDARD.lower()) == []

    def test_the_same_bytes_twice_on_one_standard_is_one_certificate(self, store):
        first = store.save(STANDARD, "coa.pdf", PDF)
        again = store.save(STANDARD, "coa.pdf", PDF)
        assert again.uid == first.uid
        assert len(store.certificates(STANDARD)) == 1

    def test_the_same_certificate_on_two_standards_is_two_rows(self, store):
        """One certificate really can cover two lots, and retiring one must not
        take the other's evidence with it."""
        a = store.save(STANDARD, "coa.pdf", PDF)
        b = store.save(RENAMED, "coa.pdf", PDF)
        assert a.uid != b.uid
        assert a.content_hash == b.content_hash
        assert store.path_for(a) != store.path_for(b)

    def test_to_dict_is_json_safe(self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF)
        payload = cert.to_dict()
        assert payload["uid"] == cert.uid
        assert set(payload) >= {"uid", "standard_name", "storage_key",
                                "filename", "size_bytes", "content_type",
                                "content_hash", "issued_at", "expires_at",
                                "uploaded_at", "uploaded_by"}


class TestDelete:
    def test_delete_removes_the_row_and_the_file(self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF)
        path = store.path_for(cert)
        assert store.delete(cert.uid) is True
        assert store.certificates(STANDARD) == []
        assert not path.exists()

    def test_delete_is_idempotent(self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF)
        store.delete(cert.uid)
        assert store.delete(cert.uid) is False

    def test_retiring_a_standard_is_one_write(self, gw, tmp_path):
        rec = RecordingGateway(gw)
        store = StandardCertificateStore(rec, root=tmp_path / "certificates")
        store.save(STANDARD, "a.pdf", PDF)
        store.save(STANDARD, "b.pdf", OTHER_PDF)
        keep = store.save(RENAMED, "keep.jpg", JPEG)
        rec.writes.clear()
        assert store.delete_for_standard(STANDARD, retired=True) == 2
        assert len(rec.writes) == 1
        assert store.certificates(STANDARD) == []
        assert [c.uid for c in store.certificates(RENAMED)] == [keep.uid]
        assert store.path_for(keep).exists()

    def test_retiring_a_standard_after_a_rename_takes_the_old_files_too(
            self, store):
        """The certificates live in the folder named for the name they were
        filed under, so a retirement that only looked in the CURRENT name's
        folder would leave the bytes behind for the sweep to find."""
        store.save(STANDARD, "a.pdf", PDF)
        store.rename_standard(STANDARD, RENAMED)
        store.save(RENAMED, "b.pdf", OTHER_PDF)
        assert store.delete_for_standard(RENAMED, retired=True) == 2
        assert [p for p in store.root.rglob("*") if p.is_file()] == []


# ── queue economy and schema discipline ─────────────────────────────────────

class TestSchemaDiscipline:
    def test_the_ddl_declares_a_new_table_and_alters_nothing(self):
        assert "lem_standard_documents" in STANDARD_DOCUMENTS_DDL
        assert "ALTER" not in STANDARD_DOCUMENTS_DDL.upper()
        source = open(standard_documents.__file__, encoding="utf-8").read()
        assert "ALTER TABLE" not in source.upper()

    def test_the_ddl_is_registered_in_snapshot_service(self):
        """IMPORTED, never retyped. A retyped copy drifts, and a copy that
        drifts here is a table the boot path declares with one set of columns
        while the store reads another."""
        registered = [d for d in snapshot_service.SCHEMA_DDL
                      if "lem_standard_documents" in d]
        assert registered == [STANDARD_DOCUMENTS_DDL]

    def test_the_table_is_not_an_arm_of_the_batched_read(self):
        """Every arm shares ONE statement, so an extra arm is bought with the
        whole floor's read. Certificates are read on a page nobody polls."""
        assert "lem_standard_documents" not in snapshot_service.batched_machine_sql()

    def test_it_did_not_go_into_schema_migrations(self):
        """A NEW table needs no ALTER. SCHEMA_MIGRATIONS is for a column added
        to a table that already exists in the field."""
        migrated = {table for table, _col, _ddl
                    in snapshot_service.SCHEMA_MIGRATIONS}
        assert "lem_standard_documents" not in migrated

    def test_this_module_touches_no_table_but_its_own(self):
        """RELEASING.md §2: a new column on a table the benches read is a MAJOR
        release. A new table is a MINOR and no bench moves.

        This used to assert that five other table names were absent from
        `STANDARD_DOCUMENTS_DDL` — a single `CREATE TABLE lem_standard_
        documents (...)` string, which cannot contain them and never could.
        True by construction, forever, about a constant nobody would edit that
        way. The real claim is about the SQL this module ISSUES, so that is
        what is read: every `lem_*` table named anywhere in its statements.
        """
        source = open(standard_documents.__file__, encoding="utf-8").read()
        statements = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "lem_" in node.value and (
                        "SELECT" in node.value or "INSERT" in node.value
                        or "UPDATE" in node.value or "DELETE" in node.value
                        or "CREATE" in node.value):
                    statements.append(node.value)
        assert statements, "no SQL found — the scan is looking in the wrong place"
        named = set()
        for sql in statements:
            for word in sql.replace("(", " ").replace(",", " ").split():
                if word.startswith("lem_"):
                    named.add(word)
        assert named == {"lem_standard_documents"}, named

    def test_boot_declares_it_and_the_store_then_works(self, tmp_path):
        """Behavioural, not a grep: against a LabCore whose only schema is what
        `ensure_schema` declared."""
        gateway = FakeLabCoreGateway()
        snapshot_service.SnapshotService(gateway).ensure_schema()
        store = StandardCertificateStore(gateway, root=tmp_path / "certificates")
        cert = store.save(STANDARD, "coa.pdf", PDF)
        assert [c.uid for c in store.certificates(STANDARD)] == [cert.uid]

    def test_the_store_never_declares_its_own_schema(self, gw, tmp_path):
        rec = RecordingGateway(gw)
        store = StandardCertificateStore(rec, root=tmp_path / "certificates")
        cert = store.save(STANDARD, "coa.pdf", PDF)
        store.certificates(STANDARD)
        store.fetch(cert.uid)
        store.delete(cert.uid)
        assert not any("CREATE TABLE" in s.upper() for s in rec.writes)

    def test_a_save_costs_one_write(self, gw, tmp_path):
        rec = RecordingGateway(gw)
        store = StandardCertificateStore(rec, root=tmp_path / "certificates")
        store.save(STANDARD, "coa.pdf", PDF)
        assert len(rec.writes) == 1

    def test_no_file_bytes_ever_reach_the_queue(self, gw, tmp_path):
        """The module's one load-bearing claim. A 300 KB certificate is the
        realistic size; a 200-byte one hides inside a statement unnoticed."""
        rec = RecordingGateway(gw)
        store = StandardCertificateStore(rec, root=tmp_path / "certificates")
        cert = store.save(STANDARD, "coa.pdf", BIG_PDF)
        assert_no_document_bytes_reached_the_queue(rec, BIG_PDF)
        assert store.path_for(cert).read_bytes() == BIG_PDF

    def test_no_file_bytes_reach_the_queue_on_any_path(self, gw, tmp_path):
        rec = RecordingGateway(gw)
        store = StandardCertificateStore(rec, root=tmp_path / "certificates")
        cert = store.save(STANDARD, "coa.pdf", BIG_PDF)
        store.certificates(STANDARD)
        store.fetch(cert.uid)
        store.rename_standard(STANDARD, RENAMED)
        store.delete(cert.uid)
        assert_no_document_bytes_reached_the_queue(rec, BIG_PDF)


# ── certificates expire, and an expired one is a finding ────────────────────

class TestTheStoredExpiryFormat:
    """`YYYY-MM-DD` and nothing else, because the query is a string comparison.

    That is what makes "everything expiring before X" one read instead of every
    row parsed in Python. A date stored in any other spelling does not merely
    display oddly — `8/25/2026` sorts before `2026-01-01`, so it drops out of
    every expiry report from the moment it is written, permanently and silently.
    """

    def test_an_iso_date_is_kept(self):
        assert normalise_expiry("2026-09-01") == "2026-09-01"

    def test_a_date_and_a_datetime_are_both_accepted(self):
        assert normalise_expiry(date(2026, 9, 1)) == "2026-09-01"
        assert normalise_expiry(datetime(2026, 9, 1, 14, 32)) == "2026-09-01"

    def test_an_iso_datetime_string_is_truncated_to_its_day(self):
        """A form's `datetime-local` and a JSON payload both send this, and a
        validity period is a day rather than an instant."""
        assert normalise_expiry("2026-09-01T14:32:00") == "2026-09-01"
        assert normalise_expiry("2026-09-01 14:32:00") == "2026-09-01"

    def test_no_expiry_is_an_empty_string(self):
        assert normalise_expiry(None) == ""
        assert normalise_expiry("") == ""
        assert normalise_expiry("   ") == ""

    @pytest.mark.parametrize("bad", ["8/25/2026", "25-08-2026", "next tuesday",
                                     "2026-13-45", "2026", "Sept 2026"])
    def test_a_date_it_cannot_track_is_refused_rather_than_stored(self, bad):
        with pytest.raises(CertificateRejected):
            normalise_expiry(bad)

    @pytest.mark.parametrize("other", ["20260901", "2026-W36-2", "2026-244"])
    def test_a_spelling_the_refusal_does_not_offer_is_not_quietly_accepted(
            self, other):
        """`date.fromisoformat` grew a much wider appetite in 3.11.

        The refusal says "Use YYYY-MM-DD" while the parser behind it accepted
        the basic form, the week date and the ordinal date — a message that is
        not true about the function that prints it. The stored format is the
        one the SQL comparison and the docstring both name, so it is the one
        that is accepted.
        """
        with pytest.raises(CertificateRejected):
            normalise_expiry(other)

    def test_the_refusal_says_what_to_type(self, store):
        with pytest.raises(CertificateRejected) as caught:
            store.save(STANDARD, "coa.pdf", PDF, expires_at="8/25/2026")
        assert "YYYY-MM-DD" in str(caught.value)

    def test_a_bad_issue_date_is_refused_in_its_own_words(self, store):
        """It used to be refused with the expiry field's sentence, which sends
        somebody to correct a box they typed correctly."""
        with pytest.raises(CertificateRejected) as caught:
            store.save(STANDARD, "coa.pdf", PDF, issued_at="8/25/2026",
                       expires_at="2026-09-30")
        message = str(caught.value).lower()
        assert "issue" in message
        assert "expiry report" not in message

    def test_a_bad_date_stores_nothing_at_all(self, store):
        """Validated before the bytes go down, so a mistyped date costs a
        re-pick rather than a file on disk and a row to unwind."""
        with pytest.raises(CertificateRejected):
            store.save(STANDARD, "coa.pdf", PDF, expires_at="8/25/2026")
        assert not store.root.exists() or \
            [p for p in store.root.rglob("*") if p.is_file()] == []
        assert store.certificates(STANDARD) == []


class TestTheExpiryBoundary:
    """Valid THROUGH the expiry date. Both directions are wrong visibly.

    A day early cries wolf about a certificate that is still good; a day late
    passes an expired one at an assessment. So the boundary is pinned on both
    sides rather than left to whoever reads the comparison next.
    """

    TODAY = date(2026, 9, 1)

    def test_no_expiry_is_not_a_status(self):
        assert certificate_status("", self.TODAY) == "none"
        assert certificate_status(None, self.TODAY) == "none"

    def test_a_certificate_is_valid_on_its_expiry_date(self):
        assert certificate_status("2026-09-01", self.TODAY) == "expiring"

    def test_it_is_expired_the_day_after(self):
        assert certificate_status("2026-08-31", self.TODAY) == "expired"

    def test_inside_the_window_is_expiring_and_outside_it_is_valid(self):
        """The window is inclusive of its last day, like the expiry itself.

        30 days from 2026-09-01 is 2026-10-01, and a certificate expiring on
        exactly the horizon is the one the warning is for — it is the last day
        an order placed today still arrives in time.
        """
        assert certificate_status("2026-09-30", self.TODAY) == "expiring"
        assert certificate_status("2026-10-01", self.TODAY) == "expiring"
        assert certificate_status("2026-10-02", self.TODAY) == "valid"

    def test_the_window_is_the_constant(self, monkeypatch):
        """Moving the constant must move the boundary, or it is documentation.

        The DEFAULT path, which is the only one that can be wrong: a default
        argument binds at `def` time, so `within_days=EXPIRY_WARNING_DAYS` in a
        signature keeps enforcing whatever the constant was at import forever.
        Passing the value in positionally — as this test used to — exercises
        the one path that cannot break and proves nothing at all.
        """
        assert certificate_status("2026-12-01", self.TODAY) == "valid"
        monkeypatch.setattr(standard_documents, "EXPIRY_WARNING_DAYS", 365)
        assert certificate_status("2026-12-01", self.TODAY) == "expiring"

    def test_every_default_reads_the_constant_at_call_time(
            self, store, monkeypatch):
        """All five of them. The module warns about this exact hazard 25 lines
        above the first one, about another module's constants."""
        cert = store.save(STANDARD, "coa.pdf", PDF, expires_at="2026-12-01")
        assert cert.status(self.TODAY) == "valid"
        assert store.expiring(now=self.TODAY) == []
        assert expiry_report(store, now=self.TODAY)["expiring"] == []

        monkeypatch.setattr(standard_documents, "EXPIRY_WARNING_DAYS", 365)
        assert cert.status(self.TODAY) == "expiring"
        assert [c.uid for c in store.expiring(now=self.TODAY)] == [cert.uid]
        report = expiry_report(store, now=self.TODAY)
        assert [c.uid for c in report["expiring"]] == [cert.uid]
        assert report["within_days"] == 365

    def test_a_window_that_is_not_a_number_is_refused_not_raised_raw(self):
        """A pure status function must not throw `ValueError` out of `int()`
        at whatever is drawing the badge."""
        with pytest.raises(CertificateRejected):
            certificate_status("2026-09-30", self.TODAY, within_days="soon")

    def test_a_window_typed_as_a_number_still_works(self):
        assert certificate_status("2026-09-30", self.TODAY,
                                  within_days="30") == "expiring"

    def test_a_date_nothing_can_read_is_not_reported_as_valid(self):
        """A row written by hand, or one that predates the normalisation.

        An unknown date is not a good date, and the only safe unknown on a
        compliance report is the one somebody looks at.
        """
        assert certificate_status("soon", self.TODAY) == "expired"

    def test_days_until_expiry_counts_both_ways(self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF, expires_at="2026-09-11")
        assert cert.days_until_expiry(self.TODAY) == 10
        past = store.save(STANDARD, "old.pdf", OTHER_PDF, expires_at="2026-08-30")
        assert past.days_until_expiry(self.TODAY) == -2

    def test_a_certificate_with_no_expiry_has_no_countdown(self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF)
        assert cert.expires_at == ""
        assert cert.days_until_expiry(self.TODAY) is None
        assert cert.status(self.TODAY) == "none"


class TestExpiryIsAnswerableInOneRead:
    TODAY = date(2026, 9, 1)

    @pytest.fixture
    def library(self, store):
        """A standards library in the four states that matter."""
        store.save("Expired Lot", "a.pdf", PDF, expires_at="2026-08-01")
        store.save("Expiring Lot", "b.pdf", OTHER_PDF, expires_at="2026-09-10")
        store.save("Good Lot", "c.png", PNG, expires_at="2027-01-01")
        store.save("Undated Lot", "d.jpg", JPEG)
        return store

    def test_the_whole_library_costs_one_read(self, gw, tmp_path):
        rec = RecordingGateway(gw)
        store = StandardCertificateStore(rec, root=tmp_path / "certificates")
        for i, expiry in enumerate(("2026-08-01", "2026-09-10", "2027-01-01")):
            store.save(f"Lot {i}", f"c{i}.pdf",
                       b"%PDF-1.7\n" + bytes(str(i), "ascii") * 40,
                       expires_at=expiry)
        rec.reads.clear()
        found = store.expiring(now=self.TODAY)
        assert len(rec.reads) == 1
        assert [c.standard_name for c in found] == ["Lot 0", "Lot 1"]

    def test_the_horizon_is_asked_of_the_database_not_of_python(
            self, gw, tmp_path):
        """The stored format exists so this is a `WHERE`, not a full read.

        Pulling every row back and filtering in Python would answer the same
        question and would scale with the size of the library rather than with
        the number of certificates that actually need attention.
        """
        rec = RecordingGateway(gw)
        store = StandardCertificateStore(rec, root=tmp_path / "certificates")
        store.save(STANDARD, "coa.pdf", PDF, expires_at="2030-01-01")
        rec.reads.clear()
        assert store.expiring(now=self.TODAY) == []
        asked = rec.reads[0]
        assert "expires_at" in asked and "<=" in asked

    def test_undated_and_far_future_certificates_are_left_out(self, library):
        names = [c.standard_name for c in library.expiring(now=self.TODAY)]
        assert "Undated Lot" not in names
        assert "Good Lot" not in names

    def test_the_soonest_to_expire_comes_first(self, library):
        found = library.expiring(now=self.TODAY)
        assert [c.standard_name for c in found] == ["Expired Lot", "Expiring Lot"]

    def test_the_report_splits_what_is_late_from_what_is_due(self, library):
        report = expiry_report(library, now=self.TODAY)
        assert [c.standard_name for c in report["expired"]] == ["Expired Lot"]
        assert [c.standard_name for c in report["expiring"]] == ["Expiring Lot"]
        assert report["as_of"] == "2026-09-01"
        assert report["within_days"] == EXPIRY_WARNING_DAYS

    def test_the_report_is_also_one_read(self, gw, tmp_path):
        rec = RecordingGateway(gw)
        store = StandardCertificateStore(rec, root=tmp_path / "certificates")
        store.save(STANDARD, "coa.pdf", PDF, expires_at="2026-08-01")
        rec.reads.clear()
        expiry_report(store, now=self.TODAY)
        assert len(rec.reads) == 1

    def test_a_wider_window_pulls_more_in(self, library):
        report = expiry_report(library, now=self.TODAY, within_days=365)
        assert [c.standard_name for c in report["expiring"]] == \
            ["Expiring Lot", "Good Lot"]

    def test_an_outage_is_never_a_clean_bill_of_health(self, gw, tmp_path):
        """"Nothing is expiring" and "could not ask" are the same sentence to
        the person reading the report, and the first one is the finding this
        report exists to be able to deny."""
        store = StandardCertificateStore(gw, root=tmp_path / "certificates")
        store.save(STANDARD, "coa.pdf", PDF, expires_at="2026-08-01")
        store.gateway = BlippingGateway(gw)
        with pytest.raises(CertificateStoreError):
            store.expiring(now=self.TODAY)
        with pytest.raises(CertificateStoreError):
            expiry_report(store, now=self.TODAY)

    def test_a_missing_table_does_not_report_everything_in_date_either(
            self, tmp_path):
        """The one degradation the sibling module allows is a COUNT on a polled
        page. This is a list, on a page nobody polls, produced during an audit —
        so an unwired server says so rather than passing the lab."""
        bare = FakeLabCoreGateway()
        store = StandardCertificateStore(bare, root=tmp_path / "certificates")
        with pytest.raises(CertificateStoreError):
            store.expiring()


class TestTheReportAndTheStatusCannotDisagree:
    """One certificate, two answers, from the same row. That was the bug.

    `certificate_status` calls a date it cannot read EXPIRED, on the stated
    grounds that the only safe unknown on a compliance report is the one that
    gets looked at. `expiring()` selected `WHERE expires_at <= ?` as a STRING,
    and `'8/25/2026'` sorts AFTER every ISO date — so the standard's own tab
    read EXPIRED while the library-wide report said all clear, out of the same
    stored value, in the month of the assessment.

    The SQL is now a superset filter and the verdict is `certificate_status`
    itself, so the two cannot disagree in either direction by construction.
    """

    TODAY = date(2026, 9, 1)

    def _hand_written(self, store, gw, uid, value):
        """A row as it can only arrive: written by hand, or from before
        `normalise_expiry` existed. The store itself refuses to write these."""
        gw.sql("UPDATE lem_standard_documents SET expires_at = ? WHERE uid = ?",
               [value, uid])

    @pytest.mark.parametrize("unreadable", ["8/25/2026", "2026-13-45",
                                            "next tuesday", "2026-9-1"])
    def test_a_date_the_status_calls_expired_is_in_the_report(
            self, store, gw, unreadable):
        cert = store.save(STANDARD, "coa.pdf", PDF, expires_at="2026-08-01")
        self._hand_written(store, gw, cert.uid, unreadable)
        assert store.get(cert.uid).status(self.TODAY) == "expired"
        assert [c.uid for c in store.expiring(now=self.TODAY)] == [cert.uid]
        report = expiry_report(store, now=self.TODAY)
        assert [c.uid for c in report["expired"]] == [cert.uid]

    def test_a_row_the_status_calls_nothing_is_not_in_the_report(
            self, store, gw):
        """Whitespace passes `expires_at <> ''` and sorts before any horizon,
        so the old query listed it as expiring while its status said `none`."""
        cert = store.save(STANDARD, "coa.pdf", PDF, expires_at="2026-08-01")
        self._hand_written(store, gw, cert.uid, "   ")
        assert store.get(cert.uid).status(self.TODAY) == "none"
        assert store.expiring(now=self.TODAY) == []

    def test_every_row_the_report_returns_agrees_with_its_own_status(
            self, store, gw):
        good = store.save(STANDARD, "good.pdf", PDF, expires_at="2027-01-01")
        due = store.save(STANDARD, "due.png", PNG, expires_at="2026-09-10")
        late = store.save(STANDARD, "late.jpg", JPEG, expires_at="2026-08-01")
        odd = store.save(RENAMED, "odd.pdf", OTHER_PDF, expires_at="2026-08-02")
        self._hand_written(store, gw, odd.uid, "8/25/2026")
        due_now = store.expiring(now=self.TODAY)
        for cert in due_now:
            assert cert.status(self.TODAY) in ("expired", "expiring")
        assert {c.uid for c in due_now} == {due.uid, late.uid, odd.uid}
        assert good.uid not in {c.uid for c in due_now}

    def test_it_is_still_one_read_with_the_wider_net(self, gw, tmp_path):
        rec = RecordingGateway(gw)
        store = StandardCertificateStore(rec, root=tmp_path / "certificates")
        store.save(STANDARD, "coa.pdf", PDF, expires_at="2026-08-01")
        rec.reads.clear()
        store.expiring(now=self.TODAY)
        assert len(rec.reads) == 1

    def test_the_horizon_is_still_asked_of_the_database(self, gw, tmp_path):
        """The whole library must not be dragged back to answer a question
        about a handful of rows."""
        rec = RecordingGateway(gw)
        store = StandardCertificateStore(rec, root=tmp_path / "certificates")
        for i in range(5):
            store.save(f"Lot {i}", f"c{i}.pdf",
                       b"%PDF-1.7\n" + bytes(str(i), "ascii") * 40,
                       expires_at="2030-01-01")
        rec.reads.clear()
        assert store.expiring(now=self.TODAY) == []
        assert "<=" in rec.reads[0] and "?" in rec.reads[0]


class TestAStandardWithACurrentCertificateIsCovered:
    """`expiry_report` used to be per-certificate and never asked `current()`.

    So every superseded COA the lab has ever replaced stayed on the expired
    list for good: a standard with this year's certificate on file read as a
    finding because last year's was still in the table. An expiry report that
    lists standards which are fully covered is a report nobody finishes
    reading, which is the same as no report.
    """

    TODAY = date(2026, 9, 1)

    def test_a_superseded_certificate_is_not_a_finding(self, store):
        old = store.save(STANDARD, "coa-2025.pdf", PDF, expires_at="2025-09-01")
        store.save(STANDARD, "coa-2027.pdf", OTHER_PDF, expires_at="2027-09-01")
        report = expiry_report(store, now=self.TODAY)
        assert report["expired"] == []
        assert report["expiring"] == []
        assert [c.uid for c in report["superseded"]] == [old.uid]
        assert report["covered"] == [STANDARD]

    def test_a_standard_with_nothing_in_date_is_still_a_finding(self, store):
        a = store.save(STANDARD, "a.pdf", PDF, expires_at="2025-09-01")
        b = store.save(STANDARD, "b.pdf", OTHER_PDF, expires_at="2026-08-01")
        report = expiry_report(store, now=self.TODAY)
        assert {c.uid for c in report["expired"]} == {a.uid, b.uid}
        assert report["superseded"] == []
        assert report["covered"] == []

    def test_a_cover_that_is_itself_running_out_is_still_reported(self, store):
        old = store.save(STANDARD, "old.pdf", PDF, expires_at="2025-09-01")
        soon = store.save(STANDARD, "soon.png", PNG, expires_at="2026-09-10")
        report = expiry_report(store, now=self.TODAY)
        assert [c.uid for c in report["expiring"]] == [soon.uid]
        assert [c.uid for c in report["superseded"]] == [old.uid]
        assert report["expired"] == []

    def test_an_undated_certificate_covers_the_standard_here_too(self, store):
        """Whatever `current()` treats as cover, this treats as cover. They
        read the same rule out of the same function or they will drift."""
        old = store.save(STANDARD, "old.pdf", PDF, expires_at="2025-09-01")
        store.save(STANDARD, "inhouse.jpg", JPEG)
        assert store.current(STANDARD, now=self.TODAY) is not None
        report = expiry_report(store, now=self.TODAY)
        assert report["expired"] == []
        assert [c.uid for c in report["superseded"]] == [old.uid]

    def test_the_report_never_calls_a_covered_standard_expired(self, store):
        """The property, stated once: nothing `current()` covers is a finding."""
        store.save(STANDARD, "old.pdf", PDF, expires_at="2025-09-01")
        store.save(STANDARD, "new.pdf", OTHER_PDF, expires_at="2027-09-01")
        store.save(RENAMED, "gone.png", PNG, expires_at="2025-01-01")
        report = expiry_report(store, now=self.TODAY)
        for cert in report["expired"] + report["expiring"]:
            assert store.current(cert.standard_name, now=self.TODAY) is None \
                or store.current(cert.standard_name,
                                 now=self.TODAY).uid == cert.uid

    def test_the_whole_report_is_still_one_read(self, gw, tmp_path):
        """Coverage is a question about every certificate of the standards
        that have something due — one statement, not one per standard."""
        rec = RecordingGateway(gw)
        store = StandardCertificateStore(rec, root=tmp_path / "certificates")
        store.save(STANDARD, "a.pdf", PDF, expires_at="2025-09-01")
        store.save(STANDARD, "b.pdf", OTHER_PDF, expires_at="2027-09-01")
        store.save(RENAMED, "c.png", PNG, expires_at="2026-08-01")
        rec.reads.clear()
        expiry_report(store, now=self.TODAY)
        assert len(rec.reads) == 1

    def test_an_outage_is_still_never_a_clean_bill_of_health(self, gw,
                                                             tmp_path):
        store = StandardCertificateStore(gw, root=tmp_path / "certificates")
        store.save(STANDARD, "a.pdf", PDF, expires_at="2025-09-01")
        store.gateway = BlippingGateway(gw)
        with pytest.raises(CertificateStoreError):
            expiry_report(store, now=self.TODAY)


class TestWhatDayItIsJudgedAgainst:
    """`_as_date` answered "today" to any string, under a docstring reading
    "Today, however the caller expressed it."

    So `certificate_status("2020-01-01", today="2019-01-01")` reported
    `expired` — self-consistently, and wrong, about a certificate that had a
    year left on the day asked about. A silent substitution is the one answer
    a caller cannot detect.
    """

    def test_a_day_given_as_a_string_is_the_day_it_names(self):
        assert certificate_status("2020-01-01", today="2019-01-01") == "valid"
        assert certificate_status("2020-01-01", today="2020-06-01") == "expired"

    def test_an_iso_datetime_string_names_its_day(self):
        assert certificate_status("2020-01-01",
                                  today="2019-01-01T09:00:00") == "valid"

    def test_a_string_that_is_not_a_day_is_refused_not_substituted(self):
        for bad in ("lunchtime", "8/25/2026", ""):
            with pytest.raises(CertificateRejected):
                certificate_status("2020-01-01", today=bad)

    def test_none_still_means_today(self):
        assert certificate_status(date.today().isoformat()) == "expiring"

    def test_the_countdown_uses_the_day_it_was_given(self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF, expires_at="2026-09-11")
        assert cert.days_until_expiry("2026-09-01") == 10

    def test_the_library_reads_can_be_asked_about_a_named_day(self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF, expires_at="2026-09-10")
        assert [c.uid for c in store.expiring(now="2026-09-01")] == [cert.uid]
        assert store.current(STANDARD, now="2026-09-01").uid == cert.uid
        assert expiry_report(store, now="2026-09-01")["as_of"] == "2026-09-01"


class TestFetchNeverHandsBackAnEmptyCertificate:
    """The promise in `fetch`'s own docstring, which nothing asserted.

    Replacing its `raise` with `return cert, b""` left the whole suite green —
    and a zero-byte PDF handed to an assessor looks like our answer, not like a
    missing file.
    """

    def test_a_row_whose_file_is_gone_raises(self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF)
        store.stored_path(cert).unlink()
        with pytest.raises(CertificateStoreError):
            store.fetch(cert.uid)

    def test_a_file_truncated_to_nothing_raises_too(self, store):
        """A half-restored backup or an interrupted copy. `save` refuses empty
        bytes, so zero on disk is always damage."""
        cert = store.save(STANDARD, "coa.pdf", PDF)
        store.stored_path(cert).write_bytes(b"")
        with pytest.raises(CertificateStoreError):
            store.fetch(cert.uid)

    def test_the_refusal_names_the_certificate_and_where_it_should_be(
            self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF)
        path = store.stored_path(cert)
        path.unlink()
        with pytest.raises(CertificateStoreError) as caught:
            store.fetch(cert.uid)
        assert "coa.pdf" in str(caught.value)
        assert str(path.parent) in str(caught.value)


class TestCorrectingAnExpiry:
    TODAY = date(2026, 9, 1)

    def test_the_expiry_is_stored_normalised(self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF,
                          expires_at="2026-09-30T00:00:00")
        assert cert.expires_at == "2026-09-30"
        assert store.get(cert.uid).expires_at == "2026-09-30"

    def test_an_issue_date_rides_along(self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF, issued_at="2025-09-30",
                          expires_at="2026-09-30")
        assert cert.issued_at == "2025-09-30"
        assert store.get(cert.uid).issued_at == "2025-09-30"

    def test_a_typo_is_fixed_without_re_uploading_the_file(self, gw, tmp_path):
        """A 5 MB PDF re-sent because somebody typed 2027 for 2026 is a
        re-pick, a re-upload and a second copy of identical bytes."""
        rec = RecordingGateway(gw)
        store = StandardCertificateStore(rec, root=tmp_path / "certificates")
        cert = store.save(STANDARD, "coa.pdf", PDF, expires_at="2027-09-30")
        rec.writes.clear()
        fixed = store.set_expiry(cert.uid, "2026-09-30")
        assert len(rec.writes) == 1
        assert fixed.expires_at == "2026-09-30"
        assert store.get(cert.uid).expires_at == "2026-09-30"
        assert store.fetch(cert.uid)[1] == PDF

    def test_re_uploading_the_same_file_with_a_corrected_date_updates_it(
            self, store):
        """The dedupe path must not silently keep the old date: re-uploading
        with the right one is exactly what somebody does after mistyping it."""
        first = store.save(STANDARD, "coa.pdf", PDF, expires_at="2027-09-30")
        again = store.save(STANDARD, "coa.pdf", PDF, expires_at="2026-09-30")
        assert again.uid == first.uid
        assert again.expires_at == "2026-09-30"
        assert len(store.certificates(STANDARD)) == 1

    def test_re_uploading_without_a_date_leaves_the_stored_one_alone(self, store):
        """Silence is not an instruction to forget the expiry."""
        first = store.save(STANDARD, "coa.pdf", PDF, expires_at="2026-09-30")
        again = store.save(STANDARD, "coa.pdf", PDF)
        assert again.uid == first.uid
        assert store.get(first.uid).expires_at == "2026-09-30"

    @pytest.mark.parametrize("blank", ["", "   ", None])
    def test_a_blank_expiry_is_silence_and_not_an_erasure(self, store, blank):
        """An HTML form and a JSON body both send `""`, never `None`.

        The old guard was `if expires_at is not None`, so `""` took the update
        branch and cleared the column — and a row with no expiry falls out of
        the expiry report permanently. Re-uploading a certificate without
        re-typing its date is the most ordinary thing an operator does.
        """
        first = store.save(STANDARD, "coa.pdf", PDF, expires_at="2026-09-30")
        again = store.save(STANDARD, "coa.pdf", PDF, expires_at=blank)
        assert again.uid == first.uid
        assert again.expires_at == "2026-09-30"
        assert store.get(first.uid).expires_at == "2026-09-30"

    def test_a_blank_expiry_writes_nothing_at_all(self, gw, tmp_path):
        rec = RecordingGateway(gw)
        store = StandardCertificateStore(rec, root=tmp_path / "certificates")
        store.save(STANDARD, "coa.pdf", PDF, expires_at="2026-09-30")
        rec.writes.clear()
        store.save(STANDARD, "coa.pdf", PDF, expires_at="")
        assert rec.writes == []

    def test_a_first_upload_with_a_blank_expiry_simply_has_none(self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF, expires_at="")
        assert cert.expires_at == ""

    def test_an_expiry_can_be_cleared(self, store):
        """Clearing takes the explicit verb, which is the whole reason `""` is
        allowed to mean silence on the way in."""
        cert = store.save(STANDARD, "coa.pdf", PDF, expires_at="2026-09-30")
        assert store.set_expiry(cert.uid, "").expires_at == ""
        assert store.get(cert.uid).status(self.TODAY) == "none"

    def test_a_corrected_issue_date_survives_a_re_upload(self, store):
        """The dedupe path took a corrected expiry and threw a corrected issue
        date away — two date fields, one of them honoured."""
        first = store.save(STANDARD, "coa.pdf", PDF, issued_at="2025-09-30",
                           expires_at="2026-09-30")
        again = store.save(STANDARD, "coa.pdf", PDF, issued_at="2025-10-01",
                           expires_at="2026-09-30")
        assert again.uid == first.uid
        assert again.issued_at == "2025-10-01"
        assert store.get(first.uid).issued_at == "2025-10-01"

    def test_a_blank_issue_date_is_silence_too(self, store):
        first = store.save(STANDARD, "coa.pdf", PDF, issued_at="2025-09-30")
        again = store.save(STANDARD, "coa.pdf", PDF, issued_at="")
        assert again.uid == first.uid
        assert store.get(again.uid).issued_at == "2025-09-30"

    def test_correcting_one_date_never_clears_the_other(self, store):
        """`set_dates` touches only what it is handed. Silence about a column
        is not an instruction to empty it — the same rule as `_supplied`, one
        level down."""
        cert = store.save(STANDARD, "coa.pdf", PDF, issued_at="2025-09-30",
                          expires_at="2026-09-30")
        fixed = store.set_expiry(cert.uid, "2027-09-30")
        assert fixed.issued_at == "2025-09-30"
        assert store.get(cert.uid).issued_at == "2025-09-30"

        again = store.set_dates(cert.uid, issued_at="2025-10-01")
        assert again.expires_at == "2027-09-30"
        assert store.get(cert.uid).expires_at == "2027-09-30"

    def test_set_dates_refuses_a_field_that_is_not_a_date(self, store):
        """It builds its own `SET` clause, so the column names it will accept
        are a closed list rather than whatever a caller passes."""
        cert = store.save(STANDARD, "coa.pdf", PDF)
        with pytest.raises(CertificateRejected):
            store.set_dates(cert.uid, standard_name="Somewhere Else")
        with pytest.raises(CertificateRejected):
            store.set_dates(cert.uid)

    def test_correcting_both_dates_at_once_is_one_write(self, gw, tmp_path):
        rec = RecordingGateway(gw)
        store = StandardCertificateStore(rec, root=tmp_path / "certificates")
        store.save(STANDARD, "coa.pdf", PDF, issued_at="2025-09-30",
                   expires_at="2027-09-30")
        rec.writes.clear()
        fixed = store.save(STANDARD, "coa.pdf", PDF, issued_at="2025-10-01",
                           expires_at="2026-09-30")
        assert len(rec.writes) == 1
        assert (fixed.issued_at, fixed.expires_at) == ("2025-10-01",
                                                       "2026-09-30")

    def test_a_bad_date_is_refused_and_nothing_is_written(self, gw, tmp_path):
        rec = RecordingGateway(gw)
        store = StandardCertificateStore(rec, root=tmp_path / "certificates")
        cert = store.save(STANDARD, "coa.pdf", PDF, expires_at="2026-09-30")
        rec.writes.clear()
        with pytest.raises(CertificateRejected):
            store.set_expiry(cert.uid, "8/25/2026")
        assert rec.writes == []
        assert store.get(cert.uid).expires_at == "2026-09-30"

    def test_setting_the_expiry_of_a_certificate_that_is_not_there_raises(
            self, store):
        with pytest.raises(CertificateStoreError):
            store.set_expiry("nope", "2026-09-30")

    def test_an_unacknowledged_expiry_write_is_not_a_recorded_expiry(
            self, gw, tmp_path):
        """The queue refuses by answering, and the answer carries no error key.

        Believing it would report a date to the operator that LabCore never
        took — on the one field the expiry report reads.
        """
        store = StandardCertificateStore(gw, root=tmp_path / "certificates")
        cert = store.save(STANDARD, "coa.pdf", PDF, expires_at="2027-09-30")
        store.gateway = UnacknowledgingGateway(
            gw, answer={"ok": False, "status": "rejected", "pending": 100},
            refuse_prefix="UPDATE")
        with pytest.raises(CertificateStoreError):
            store.set_expiry(cert.uid, "2026-09-30")
        store.gateway = gw
        assert store.get(cert.uid).expires_at == "2027-09-30"


# ── "is this standard covered right now?" ───────────────────────────────────

class TestTheCertificateAStandardIsCurrentlyRestingOn:
    """The finding, phrased as a question about one standard.

    An expired certificate is a finding at assessment, and the standard is
    asserting values the whole time. `expiring()` answers it for the library;
    this answers it for the standard in front of you, out of the same one read
    the tab already makes.
    """

    TODAY = date(2026, 9, 1)

    def test_the_covering_certificate_is_the_one_valid_longest(self, store):
        """Not the most recently uploaded. A lab that scans last year's COA
        after this year's — which is what happens when somebody tidies a
        drawer — is still covered by this year's."""
        store.save(STANDARD, "old.pdf", PDF, expires_at="2026-10-01",
                   now=datetime(2026, 8, 1))
        newest = store.save(STANDARD, "new.pdf", OTHER_PDF,
                            expires_at="2027-01-01",
                            now=datetime(2026, 8, 2))
        store.save(STANDARD, "rescan.png", PNG, expires_at="2026-09-15",
                   now=datetime(2026, 8, 3))
        assert store.current(STANDARD, now=self.TODAY).uid == newest.uid

    def test_a_standard_whose_only_certificate_has_expired_is_not_covered(
            self, store):
        store.save(STANDARD, "coa.pdf", PDF, expires_at="2026-08-01")
        assert store.current(STANDARD, now=self.TODAY) is None

    def test_a_standard_with_nothing_on_file_is_not_covered(self, store):
        assert store.current(STANDARD, now=self.TODAY) is None

    def test_an_undated_certificate_still_counts_as_cover(self, store):
        """Plenty of in-house standards carry no stated validity period, and
        "no expiry" is not the same fact as "expired". Reading it as expired
        would report every one of them as a finding."""
        cert = store.save(STANDARD, "coa.pdf", PDF)
        assert store.current(STANDARD, now=self.TODAY).uid == cert.uid

    def test_a_dated_certificate_is_preferred_over_an_undated_one(self, store):
        """An expiry is evidence about the cover; its absence is silence."""
        store.save(STANDARD, "undated.pdf", PDF, now=datetime(2026, 8, 1))
        dated = store.save(STANDARD, "dated.pdf", OTHER_PDF,
                           expires_at="2027-01-01", now=datetime(2026, 8, 2))
        assert store.current(STANDARD, now=self.TODAY).uid == dated.uid

    def test_asking_costs_one_read(self, gw, tmp_path):
        rec = RecordingGateway(gw)
        store = StandardCertificateStore(rec, root=tmp_path / "certificates")
        store.save(STANDARD, "coa.pdf", PDF, expires_at="2027-01-01")
        rec.reads.clear()
        assert store.current(STANDARD, now=self.TODAY) is not None
        assert len(rec.reads) == 1

    def test_an_outage_is_not_an_uncovered_standard(self, gw, tmp_path):
        """`None` here means "this standard has no certificate in date", which
        is a finding somebody acts on. Said during a blip about a standard that
        is perfectly covered, it sends them looking for a document that is
        already on file."""
        store = StandardCertificateStore(gw, root=tmp_path / "certificates")
        store.save(STANDARD, "coa.pdf", PDF, expires_at="2027-01-01")
        store.gateway = BlippingGateway(gw)
        with pytest.raises(CertificateStoreError):
            store.current(STANDARD, now=self.TODAY)


# ── "could not ask" is never "no certificate on file" ───────────────────────

class TestAReadOutageIsNotAnEmptyAnswer:
    """"No certificate on file" is a sentence somebody acts on.

    They go and look for one, or they raise it as a gap. Said about a
    certificate that is sitting on the server's disk during one bad second, it
    is a lie the caller cannot detect — and the house rule
    (`snapshot_service.SnapshotReadError`) states it directly: reporting "no
    machines" when the truth is "could not ask" is how a whole lab reads as
    empty during a LabCore blip.
    """

    @pytest.fixture
    def blip(self, gw, tmp_path):
        store = StandardCertificateStore(gw, root=tmp_path / "certificates")
        cert = store.save(STANDARD, "coa.pdf", PDF)
        store.gateway = BlippingGateway(gw)
        return store, cert

    def test_a_missing_table_is_still_an_empty_list(self, tmp_path):
        """The one failure that genuinely means "there is nothing". Display
        paths only — the sweep and every read on the way to a write take the
        other exit."""
        bare = FakeLabCoreGateway()
        store = StandardCertificateStore(bare, root=tmp_path / "certificates")
        assert store.certificates(STANDARD) == []
        assert store.get("anything") is None

    def test_a_read_outage_is_not_an_empty_list(self, blip):
        store, _cert = blip
        with pytest.raises(CertificateStoreError):
            store.certificates(STANDARD)

    def test_a_read_outage_is_not_an_unknown_uid(self, blip):
        store, cert = blip
        with pytest.raises(CertificateStoreError):
            store.get(cert.uid)

    def test_delete_during_an_outage_does_not_report_nothing_to_delete(
            self, blip):
        store, cert = blip
        with pytest.raises(CertificateStoreError):
            store.delete(cert.uid)
        store.gateway.blipping = False
        assert [c.uid for c in store.certificates(STANDARD)] == [cert.uid]
        assert store.path_for(cert).exists()

    def test_fetch_during_an_outage_does_not_name_the_uid_as_unknown(self, blip):
        store, cert = blip
        with pytest.raises(CertificateStoreError) as caught:
            store.fetch(cert.uid)
        assert "No certificate" not in str(caught.value)
        assert "LabCore" in str(caught.value)

    def test_an_outage_never_stores_a_second_copy_of_the_same_bytes(self, blip):
        """The permanent one: dedupe reads, and a read that failed says "new".

        The second upload writes a second row and a second copy of identical
        bytes, and nothing ever collapses them — the standard shows one
        certificate twice, forever, because of one bad second.
        """
        store, cert = blip
        with pytest.raises(CertificateStoreError):
            store.save(STANDARD, "coa.pdf", PDF)
        store.gateway.blipping = False
        assert [c.uid for c in store.certificates(STANDARD)] == [cert.uid]
        assert len([p for p in store.root.rglob("*") if p.is_file()]) == 1

    def test_an_outage_does_not_call_every_live_file_an_orphan(self, blip):
        store, _cert = blip
        with pytest.raises(CertificateStoreError):
            store.orphaned_files()

    def test_a_missing_table_refuses_the_sweep_too(self, store, gw):
        """A delete list is the one place the shared rule's honest degradation
        is wrong: "no certificates are recorded" reads as "every file on disk is
        unaccounted for", and the only use for that list is deleting what is on
        it."""
        store.save(STANDARD, "coa.pdf", PDF)
        assert store.orphaned_files() == []
        gw.sql("DROP TABLE lem_standard_documents")
        with pytest.raises(CertificateStoreError):
            store.orphaned_files()

    def test_an_orphan_is_findable(self, store):
        cert = store.save(STANDARD, "coa.pdf", PDF)
        stray = store.path_for(cert).parent / "abandoned.pdf"
        stray.write_bytes(OTHER_PDF)
        orphans = store.orphaned_files()
        assert str(stray) in orphans
        assert str(store.path_for(cert)) not in orphans

    def test_an_upload_still_in_flight_is_not_an_orphan(self, store):
        """A 5 MB certificate takes a moment to land, and `_write_bytes` writes
        it under `.part` before the atomic rename. A sweep run during that
        upload used to list the temp file for deletion — the grace exists for
        exactly this and nothing exercised it."""
        cert = store.save(STANDARD, "coa.pdf", PDF)
        inflight = store.path_for(cert).parent / (
            "later.pdf" + equipment_documents.PART_SUFFIX)
        inflight.write_bytes(b"%PDF-1.7\nhalf a certificate")
        assert str(inflight) not in store.orphaned_files()

    def test_a_part_file_nobody_is_writing_any_more_is_an_orphan(self, store):
        """Once it is older than the grace it is a leftover, and leaving it out
        forever would make the sweep unable to report the one kind of file it
        is most likely to find."""
        cert = store.save(STANDARD, "coa.pdf", PDF)
        stale = store.path_for(cert).parent / (
            "abandoned.pdf" + equipment_documents.PART_SUFFIX)
        stale.write_bytes(b"%PDF-1.7\nhalf a certificate")
        old = time.time() - equipment_documents.PART_FILE_GRACE_SECONDS - 60
        os.utime(stale, (old, old))
        assert str(stale) in store.orphaned_files()

    def test_a_part_file_that_cannot_be_stat_ed_is_left_alone(
            self, store, monkeypatch):
        """It reports nothing rather than inviting the deletion of a file it
        could not even look at."""
        cert = store.save(STANDARD, "coa.pdf", PDF)
        stale = store.path_for(cert).parent / (
            "abandoned.pdf" + equipment_documents.PART_SUFFIX)
        stale.write_bytes(b"x")
        old = time.time() - equipment_documents.PART_FILE_GRACE_SECONDS - 60
        os.utime(stale, (old, old))

        real_stat = standard_documents.Path.stat

        def blind(self, *a, **kw):
            if str(self) == str(stale):
                raise OSError("gone")
            return real_stat(self, *a, **kw)

        monkeypatch.setattr(standard_documents.Path, "stat", blind)
        assert str(stale) not in store.orphaned_files()

    def test_a_forged_uid_cannot_serve_the_file_next_to_it(self, store):
        """A uid read back out of LabCore is whatever is in the table.

        `*` inside the sibling glob would match the certificate NEXT to it,
        which `stored_path` would serve and `delete` would unlink — and
        `path_for`'s containment check does not catch it, because
        `<root>/<folder>/*.pdf` is genuinely under the root. The stem equality
        is what closes it. This asserts the behaviour rather than the identity
        of the regex that used to sit in front of it doing nothing.
        """
        real = store.save(STANDARD, "coa.pdf", PDF)
        for forged_uid in ("*", "?", "[a-z]*", ".."):
            forged = StandardCertificate(
                **{**real.to_dict(), "uid": forged_uid})
            served = store.stored_path(forged)
            assert served != store.stored_path(real)
            assert not served.exists()

    def test_a_read_that_answers_with_nothing_at_all_is_an_outage(
            self, gw, tmp_path):
        class Mute(BlippingGateway):
            def read_sql(self, sql, args=None, **kw):
                return None

        store = StandardCertificateStore(Mute(gw), root=tmp_path / "certificates")
        with pytest.raises(CertificateStoreError):
            store.certificates(STANDARD)

    def test_a_raising_read_is_an_outage_too(self, gw, tmp_path):
        class Raising(BlippingGateway):
            def read_sql(self, sql, args=None, **kw):
                raise RuntimeError("connection reset")

        store = StandardCertificateStore(Raising(gw),
                                         root=tmp_path / "certificates")
        with pytest.raises(CertificateStoreError):
            store.certificates(STANDARD)


# ── a write counts only when LabCore says it happened ───────────────────────

class TestAWriteIsNotDoneUntilItIsAcknowledged:
    """The absence of an "error" key is not an acknowledgement.

    LabCore's queue refuses past ~100 pending by ANSWERING, and a gateway that
    has stopped answering returns `None`. Believing either hands back a
    certificate with bytes on disk and zero rows in LabCore — so the standard
    never lists it, and nobody finds out until the assessment.
    """

    @pytest.fixture(params=[None,
                            {"error": "LabCore is busy, try again",
                             "busy": True, "retry_after": 4},
                            {"ok": False, "status": "rejected",
                             "pending": 100}],
                    ids=["no-answer", "busy-refusal", "queue-refusal"])
    def answer(self, request):
        """The one refusal shape this lab has measured, plus the two shapes a
        gateway produces when it has stopped talking. `{}` is deliberately NOT
        here: nothing records what real LabCore answers to a write that
        SUCCEEDS, so an answer carrying no failure signal has to be accepted —
        see tests/test_labcore_result.py."""
        return request.param

    def test_an_unacknowledged_insert_is_not_a_saved_certificate(
            self, answer, gw, tmp_path):
        store = StandardCertificateStore(gw, root=tmp_path / "certificates")
        store.gateway = UnacknowledgingGateway(gw, answer=answer)
        with pytest.raises(CertificateStoreError):
            store.save(STANDARD, "coa.pdf", PDF)
        store.gateway = gw
        assert store.certificates(STANDARD) == []

    def test_an_unacknowledged_insert_leaves_no_file_behind(
            self, answer, gw, tmp_path):
        store = StandardCertificateStore(gw, root=tmp_path / "certificates")
        store.gateway = UnacknowledgingGateway(gw, answer=answer)
        with pytest.raises(CertificateStoreError):
            store.save(STANDARD, "coa.pdf", PDF)
        assert [p for p in store.root.rglob("*") if p.is_file()] == []

    def test_an_unacknowledged_delete_keeps_the_file(self, answer, gw, tmp_path):
        """Row first, then bytes. If the row did not go, the bytes must stay —
        unlinking here produces the certificate that is listed and cannot be
        produced."""
        store = StandardCertificateStore(gw, root=tmp_path / "certificates")
        cert = store.save(STANDARD, "coa.pdf", PDF)
        store.gateway = UnacknowledgingGateway(gw, answer=answer,
                                               refuse_prefix="DELETE")
        with pytest.raises(CertificateStoreError):
            store.delete(cert.uid)
        assert store.path_for(cert).exists()
        assert [c.uid for c in store.certificates(STANDARD)] == [cert.uid]

    def test_an_unacknowledged_rename_is_not_a_rename(self, answer, gw, tmp_path):
        """The caller is a QC library rename that is about to report success
        about the standard AND its evidence."""
        store = StandardCertificateStore(gw, root=tmp_path / "certificates")
        cert = store.save(STANDARD, "coa.pdf", PDF)
        store.gateway = UnacknowledgingGateway(gw, answer=answer,
                                               refuse_prefix="UPDATE")
        with pytest.raises(CertificateStoreError):
            store.rename_standard(STANDARD, RENAMED)
        store.gateway = gw
        assert [c.uid for c in store.certificates(STANDARD)] == [cert.uid]
        assert store.certificates(RENAMED) == []

    def test_an_unacknowledged_retirement_keeps_every_file(
            self, answer, gw, tmp_path):
        store = StandardCertificateStore(gw, root=tmp_path / "certificates")
        a = store.save(STANDARD, "a.pdf", PDF)
        b = store.save(STANDARD, "b.pdf", OTHER_PDF)
        store.gateway = UnacknowledgingGateway(gw, answer=answer,
                                               refuse_prefix="DELETE")
        with pytest.raises(CertificateStoreError):
            store.delete_for_standard(STANDARD, retired=True)
        assert store.path_for(a).exists() and store.path_for(b).exists()
        assert len(store.certificates(STANDARD)) == 2

    def test_a_refused_insert_leaves_no_file_behind(self, gw, tmp_path):
        store = StandardCertificateStore(RefusingGateway(gw),
                                         root=tmp_path / "certificates")
        with pytest.raises(CertificateStoreError):
            store.save(STANDARD, "coa.pdf", PDF)
        assert [p for p in tmp_path.rglob("*") if p.is_file()] == []

    def test_a_failed_disk_write_writes_no_metadata_row(self, store, monkeypatch):
        def boom(*a, **kw):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(standard_documents.os, "replace", boom)
        with pytest.raises(CertificateStoreError):
            store.save(STANDARD, "coa.pdf", PDF)
        assert store.certificates(STANDARD) == []
        assert [p for p in store.root.rglob("*") if p.is_file()] == []

    def test_an_acknowledged_write_that_matched_nothing_is_still_a_write(
            self, gw, tmp_path):
        """`rows_affected: 0` is an acknowledgement, not a failure — an
        operator's second click must not raise."""
        store = StandardCertificateStore(gw, root=tmp_path / "certificates")
        cert = store.save(STANDARD, "coa.pdf", PDF)
        path = store.path_for(cert)
        store.gateway = UnacknowledgingGateway(
            gw, answer={"ok": True, "rows_affected": 0}, refuse_prefix="DELETE")
        assert store.delete(cert.uid) is True
        assert not path.exists()


class TestTheRuleComesFromOnePlace:
    """One question about a gateway answer, one answer, in `labcore_result`."""

    def test_the_module_does_not_read_answers_itself(self):
        """`res.get("error")` and `res.get("rows")` are how the private,
        forked-and-wrong versions of the rule were spelled in three modules in
        one week. If either appears here, it has been forked again."""
        source = open(standard_documents.__file__, encoding="utf-8").read()
        assert '.get("error")' not in source
        assert '.get("rows")' not in source
        assert "no such table" not in source

    def test_a_read_outage_carries_the_shared_reason(self, gw, tmp_path):
        """Translated, not swallowed: a route that wants to say "try again in a
        moment" has to be able to tell a blip from a refusal."""
        store = StandardCertificateStore(BlippingGateway(gw),
                                         root=tmp_path / "certificates")
        with pytest.raises(CertificateStoreError) as caught:
            store.certificates(STANDARD)
        assert isinstance(caught.value.__cause__, LabCoreUnavailable)

    def test_a_refused_write_carries_the_shared_reason(self, gw, tmp_path):
        store = StandardCertificateStore(RefusingGateway(gw),
                                         root=tmp_path / "certificates")
        with pytest.raises(CertificateStoreError) as caught:
            store.save(STANDARD, "coa.pdf", PDF)
        assert isinstance(caught.value.__cause__, LabCoreRefused)

    def test_a_missing_table_is_the_news_on_the_way_to_a_write(self, tmp_path):
        """The dedupe read is a write path, so it does not swallow the absence:
        otherwise an unwired server creates the folder, writes the bytes, fails
        the INSERT on the same missing table and unlinks them again."""
        bare = FakeLabCoreGateway()
        store = StandardCertificateStore(bare, root=tmp_path / "certificates")
        with pytest.raises(CertificateStoreError) as caught:
            store.save(STANDARD, "coa.pdf", PDF)
        assert "already stored" in str(caught.value)
        assert not store.root.exists()

    def test_the_upload_gate_is_the_shared_one_and_not_a_second_copy(self):
        """The accept-list, the signature check and the slug are imported.

        A second copy of a traversal defence is worse than no copy at all: it
        makes the codebase look defended in two places while only one of them
        is maintained. So the constants are read through the module object, and
        moving one must move BOTH stores.
        """
        assert standard_documents._docs is equipment_documents
        # The `_SAFE_UID` identity assertion that used to sit here held a glob
        # guard in `_stored_siblings` that could never fire — everything the
        # pattern rejected, the `p.stem == cert.uid` equality had already
        # rejected. The guard is gone and the behaviour it claimed is asserted
        # directly: see `test_a_forged_uid_cannot_serve_the_file_next_to_it`.
        touched = {node.attr for node in ast.walk(ast.parse(open(
            standard_documents.__file__, encoding="utf-8").read()))
            if isinstance(node, ast.Attribute)}
        assert "_SAFE_UID" not in touched

    def test_the_ceiling_is_the_shared_constant_and_not_a_number_in_a_branch(
            self, store, monkeypatch):
        monkeypatch.setattr(equipment_documents, "MAX_DOCUMENT_BYTES", 512)
        with pytest.raises(CertificateRejected):
            store.save(STANDARD, "big.pdf", b"%PDF-1.7\n" + b"0" * 600)
        assert store.save(STANDARD, "small.pdf", PDF).size_bytes == len(PDF)

    def test_the_accept_list_is_the_shared_one(self, store, monkeypatch):
        for name, data in (("a.pdf", PDF), ("b.png", PNG), ("c.jpg", JPEG)):
            cert = store.save(STANDARD, name, data)
            assert store.path_for(cert).suffix == \
                equipment_documents.ACCEPTED_CONTENT_TYPES[cert.content_type]
        with pytest.raises(CertificateRejected):
            store.save(STANDARD, "setup.exe", b"MZ\x90\x00" + b"x" * 100)

    def test_a_stored_file_survives_a_changed_extension(self, store, monkeypatch):
        """Renaming a value in the shared accept-list is a data migration, not
        a tidy-up. The lookup finds an already-stored file whatever spelling it
        was written under."""
        cert = store.save(STANDARD, "plate.jpg", JPEG)
        monkeypatch.setitem(equipment_documents.ACCEPTED_CONTENT_TYPES,
                            "image/jpeg", ".jpeg")
        got, data = store.fetch(cert.uid)
        assert got.uid == cert.uid and data == JPEG
        assert store.orphaned_files() == []

    def test_a_pdf_name_over_non_pdf_bytes_is_refused(self, store):
        """The extension is a claim; the leading bytes are the only part of an
        upload that cannot be renamed."""
        with pytest.raises(CertificateRejected):
            store.save(STANDARD, "coa.pdf", b"MZ\x90\x00" + b"x" * 100)
        assert store.certificates(STANDARD) == []


# ── the numeric binding is a LATER, deliberate step ─────────────────────────

class TestTheCertificateIsNotBoundToTheStandardsNumbers:
    """Ryan: "leave that alone for now ... build out the infrastructure to
    upload the certificate into the standard itself."

    Read precisely, that is the FILE half and only the file half. The obvious
    next fields — the certified value, its uncertainty, its coverage factor —
    are a decision nobody has taken yet, on a table the benches read, which
    makes guessing at it a MAJOR release bought in advance for an unspecified
    feature. "Helpfully added the obvious next field" is exactly the change
    that looks harmless in a diff, so it is a test rather than a comment.
    """

    def test_no_numeric_certificate_fields_exist_yet(self):
        """In the code. The docstring names them on purpose — that is the
        warning, and a test that banned the words would delete it."""
        code = _module_code_without_its_docstring()
        for banned in ("cert_value", "cert_uncertainty", "cert_k",
                       "certified_value"):
            assert banned not in code, banned
            assert banned not in STANDARD_DOCUMENTS_DDL, banned
        assert not any(f.startswith("cert_")
                       for f in StandardCertificate.__dataclass_fields__)

    def test_the_qc_sample_model_did_not_grow_them(self):
        """The binding would land on `QcSampleTest`, and this phase does not
        touch `qc_samples.py` at all."""
        fields = qc_samples.QcSampleTest.__dataclass_fields__
        assert set(fields) == {"name", "value_col", "expected", "std_dev",
                               "k", "units"}
        assert "lem_standard_documents" not in qc_samples.QC_SAMPLES_DDL

    def test_nothing_here_reads_a_number_out_of_a_pdf(self):
        """The file is stored and served whole. Nothing parses it, so nothing
        can quietly become the source of a value the lab reports."""
        source = open(standard_documents.__file__, encoding="utf-8").read()
        for banned in ("PyPDF", "pdfplumber", "pdfminer", "extract_text",
                       "fitz"):
            assert banned not in source

    def test_the_row_leaves_the_door_open_and_nothing_more(self):
        """A uid is the whole of the affordance: whatever binds values later
        has something stable to point at, and no shape has been chosen for
        it."""
        assert "uid TEXT PRIMARY KEY" in STANDARD_DOCUMENTS_DDL


class TestTheModuleStaysStorageOnly:
    def test_there_are_no_flask_routes_here(self):
        """`equipment_documents` says the same about itself: a later phase
        mounts them. A store that grows a route grows a request context, and
        the tests stop being able to drive it directly."""
        code = _module_code_without_its_docstring()
        assert "flask" not in code.lower()
        assert "@app.route" not in code and "add_url_rule" not in code

    def test_it_imports_nothing_that_needs_pip(self):
        """Stdlib only, forever. The station module's rule, and this server
        ships as an archive onto a Windows box with no build step."""
        source = open(standard_documents.__file__, encoding="utf-8").read()
        local = {p[:-3] for p in os.listdir(
            os.path.dirname(standard_documents.__file__)) if p.endswith(".py")}
        imported = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported.add((node.module or "").split(".")[0])
        outside = imported - local - set(sys.stdlib_module_names) - {""}
        assert outside == set(), outside
