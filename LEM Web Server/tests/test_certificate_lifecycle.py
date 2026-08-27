"""A certificate has to survive what happens to the standard it belongs to.

`standard_documents.py` shipped with three functions for exactly this —
`repoint_certificates`, `orphaned_certificates`, `delete_for_standard` — and a
grep says the only callers are their own tests. The store knows how to keep a
certificate attached across a rename; nothing has ever asked it to.

That did not matter while no UI could upload one. It matters the moment one can,
because of how the QC library renames:

    POST /api/qc-samples      (save under the new name)
    DELETE /api/qc-samples    (remove the old name)

A certificate is keyed by the standard's NAME. So a rename leaves the file on
disk, the row in LabCore, and nothing on any screen able to find it — and the
standard the lab is actually using now has no certificate behind its numbers.
That is the failure mode an assessor tests for: the values are on file, the
document they came from is not.

Three questions here, and the third is a judgement rather than a bug.

**A rename carries the certificate.** Same material, same document, new label.

**A deletion does not leave a row pointing at a standard nobody has.** The
retired-lot case is real — `delete_for_standard(..., retired=True)` exists — but
it must be asked for, never inferred from a rename that happened to look like
one.

**A changeover does NOT carry it.** This is the one worth arguing about. A
changeover is a NEW LOT of the material: different batch, different assay,
different certificate. Inheriting the specs is right — they are what the lab
expects of the material. Inheriting the *certificate* would attach last lot's
document to this lot's numbers, which is worse than having none, because it
looks complete. So the new lot starts with no certificate and the answer says
so, loudly enough to reach the person holding the new COA.
"""

import io

import pytest

import snapshot_service
from labcore_gateway import FakeLabCoreGateway
from qc_samples import QcSample, QcSampleStore, QcSampleTest
from web_app import create_app

OLD = "Diesel - AO25"
NEW = "Diesel - AO26"
PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n"
       b"%%EOF\n")


def _standard(store, name, lab_id="STD-1"):
    store.save(QcSample(name=name, sample_id_val=lab_id,
                        tests=[QcSampleTest(name="Flash Point",
                                            value_col="Flash Point",
                                            expected=63.7, std_dev=1.05)]))


@pytest.fixture
def app(tmp_path):
    gw = FakeLabCoreGateway()
    snapshot_service.SnapshotService(gw).ensure_schema()
    store = QcSampleStore(gw)
    store.ensure_schema()
    _standard(store, OLD)
    application = create_app(gw, secret="t",
                             documents_root=str(tmp_path / "docs"))
    application.config.update(TESTING=True)
    application.config["GW"] = gw
    return application


@pytest.fixture
def client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = "ryan"
    return c


def _upload(client, standard=OLD, filename="coa.pdf", body=None):
    """`body` defaults to the same bytes every time on purpose — the store is
    content-addressed, so a second upload of identical bytes comes back as the
    FIRST record whatever it is called. A test wanting two certificates has to
    hand in two documents."""
    r = client.post("/api/qc-standards/certificates",
                    data={"standard": standard,
                          "file": (io.BytesIO(body or PDF), filename)},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    return r.get_json()["certificate"]


def _certs(client, standard):
    r = client.get("/api/qc-standards/certificates",
                   query_string={"standard": standard})
    assert r.status_code == 200, r.get_json()
    return [c["filename"] for c in r.get_json()["certificates"]]


def _rename(client, old=OLD, new=NEW, lab_id="STD-1"):
    """What the QC library does on a rename: save the new, delete the old."""
    saved = client.post("/api/qc-samples", json={
        "name": new, "sample_id_val": lab_id,
        "tests": [{"name": "Flash Point", "value_col": "Flash Point",
                   "expected": 63.7, "std_dev": 1.05, "k": 2, "units": "C",
                   "qc_expire_hours": 0}]})
    assert saved.status_code == 200, saved.get_json()
    return client.delete("/api/qc-samples",
                         json={"name": old, "renamed_to": new})


class TestARenameKeepsTheCertificate:
    """Same material, same document, new label."""

    def test_the_certificate_follows_the_new_name(self, client):
        _upload(client)
        assert _rename(client).status_code == 200
        assert _certs(client, NEW) == ["coa.pdf"]

    def test_and_is_no_longer_filed_under_the_old_one(self, client):
        _upload(client)
        _rename(client)
        assert _certs(client, OLD) == []

    def test_it_is_still_downloadable_afterwards(self, client):
        """A row that moved but a file that did not is the orphan this whole
        module refuses to create. Prove the bytes still come back."""
        uid = _upload(client)["uid"]
        _rename(client)
        got = client.get(f"/api/qc-standards/certificates/{uid}/download")
        assert got.status_code == 200
        assert got.data == PDF

    def test_every_certificate_moves_not_just_the_newest(self, client):
        _upload(client, filename="coa.pdf")
        _upload(client, filename="addendum.pdf",
                body=PDF.replace(b"Catalog", b"Catalog "))
        _rename(client)
        assert sorted(_certs(client, NEW)) == ["addendum.pdf", "coa.pdf"]

    def test_a_rename_with_no_certificates_is_still_fine(self, client):
        assert _rename(client).status_code == 200
        assert _certs(client, NEW) == []

    def test_the_move_is_audited(self, client):
        """Somebody has to be able to ask why this certificate is filed under
        a name it was not uploaded against.

        Asserting only that both names appear in the log is not a test — the
        save and the delete each write one, so it passes against a build that
        never moved anything. It has to find the MOVE."""
        _upload(client)
        _rename(client)
        rows = client.get("/api/logs", query_string={"limit": 200}).get_json()
        entries = rows.get("events") or []
        moves = [e for e in entries
                 if "certificate" in str(e).lower() and "moved" in str(e).lower()]
        assert moves, entries[:5]
        assert OLD in str(moves) and NEW in str(moves)


class TestADeletionIsNotARename:
    """`delete_for_standard` is the loaded gun. It must be asked for."""

    def test_a_plain_delete_does_not_orphan_a_certificate_row(self, client):
        """Deleting a standard outright with a certificate still on it leaves
        a row nothing can reach. Either it is refused or it is cleaned up —
        what it may NOT do is silently leave the orphan."""
        _upload(client)
        r = client.delete("/api/qc-samples", json={"name": OLD})
        if r.status_code == 200:
            assert _certs(client, OLD) == []
        else:
            assert str(r.get_json().get("error", "")).strip()
            assert _certs(client, OLD) == ["coa.pdf"]

    def test_a_delete_naming_a_standard_that_does_not_exist_is_not_a_move(
            self, client):
        """`renamed_to` pointing at nothing would file the certificate under a
        name the library does not hold — an orphan wearing a valid label."""
        _upload(client)
        r = client.delete("/api/qc-samples",
                          json={"name": OLD, "renamed_to": "Never Created"})
        assert r.status_code >= 400, r.get_json()
        assert _certs(client, OLD) == ["coa.pdf"]

    def test_renaming_onto_itself_is_refused_as_the_contradiction_it_is(
            self, client):
        """"Delete this standard, and move its certificates onto itself" is not
        a request that can be honoured either way round. The client never sends
        it — the rename path only fires when the name actually changed — so it
        is a malformed request, and it must be refused for THAT reason rather
        than falling through to the certificate-conflict branch, which would
        report a conflict that is not what is wrong."""
        _upload(client)
        r = client.delete("/api/qc-samples",
                          json={"name": OLD, "renamed_to": OLD})
        assert r.status_code == 400, r.get_json()
        assert _certs(client, OLD) == ["coa.pdf"]


class TestAChangeoverDoesNotInheritTheCertificate:
    """A new lot is new material. Its predecessor's COA describes a batch this
    one is not, and attaching it looks complete rather than looking missing —
    which is the worse of the two failures."""

    def _changeover(self, client, retire_old=False):
        return client.post("/api/qc-samples/changeover",
                           json={"old_name": OLD, "new_name": NEW,
                                 "new_id_val": "STD-2",
                                 "retire_old": retire_old})

    def test_the_new_lot_starts_with_no_certificate(self, client):
        _upload(client)
        assert self._changeover(client).status_code == 200
        assert _certs(client, NEW) == []

    def test_the_old_lot_keeps_its_own(self, client):
        _upload(client)
        self._changeover(client)
        assert _certs(client, OLD) == ["coa.pdf"]

    def test_the_answer_says_the_new_lot_needs_one(self, client):
        """Silence here means somebody has to notice the absence on their own,
        during an assessment, which is when nobody does."""
        _upload(client)
        body = self._changeover(client).get_json()
        assert body.get("certificate_needed") is True

    def test_and_does_not_say_so_when_the_old_lot_had_none(self, client):
        body = self._changeover(client).get_json()
        assert not body.get("certificate_needed")


class TestTheRoutesStayWired:
    """The pattern CLAUDE.md names three times: declared and inert looks
    exactly like working. These are the greps that fail if the wiring is cut."""

    def test_web_app_repoints_rather_than_orphaning(self):
        import web_app
        assert "repoint_certificates" in open(web_app.__file__).read()

    def test_the_floor_can_reach_the_certificate_routes(self):
        """The routes existed for a whole release with no caller in any
        template. That is not shipped."""
        import os

        import web_app
        page = os.path.join(os.path.dirname(web_app.__file__),
                            "templates", "floor.html")
        markup = open(page, encoding="utf-8").read()
        assert "/api/qc-standards/certificates" in markup
