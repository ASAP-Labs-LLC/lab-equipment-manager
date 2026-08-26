"""The certificate a QC standard's values rest on, reachable over HTTP.

`standard_documents.py` shipped fully tested and connected to nothing — the
exact "declared but inert" pattern CLAUDE.md records for `levels.py`,
`equipment_documents.py` and `equipment_history.py`, where working and unwired
look identical from the outside. This is the wiring, and this file is the gate
that says it stayed wired.

Two shapes worth explaining before the tests.

**The standard's name is never a path segment.** It is a human string —
`Diesel - AO25` — chosen by whoever created the standard, and nothing stops one
containing a slash. As a path segment that either 404s or silently addresses a
different route, and `<path:...>` would then collide with the by-uid routes at
the same depth. `equipment_documents` had to solve the same collision by
reasoning about which of two same-depth rules Werkzeug matches first; not
putting the name in the path at all is the version of that with no trap in it.

**A refused write must not read as a rejected file.** "The queue is deep, try
again in five seconds" and "that is not a PDF" are opposite instructions to the
person holding the certificate, and the store already tells them apart —
`CertificateRejected` for the file, `CertificateStoreError` for LabCore. The
routes must keep them apart too.
"""

import io
import json

import pytest

import snapshot_service
import standard_documents
from labcore_gateway import FakeLabCoreGateway
from qc_samples import QcSample, QcSampleStore, QcSampleTest
from web_app import create_app

STANDARD = "Diesel - AO25"
PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n"
       b"%%EOF\n")


@pytest.fixture
def app(tmp_path):
    gw = FakeLabCoreGateway()
    snapshot_service.SnapshotService(gw).ensure_schema()
    store = QcSampleStore(gw)
    store.ensure_schema()
    store.save(QcSample(name=STANDARD, sample_id_val="STD-1",
                        tests=[QcSampleTest(name="Flash Point",
                                            value_col="Flash Point",
                                            expected=63.7, std_dev=1.05)]))
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


def _upload(client, name=STANDARD, filename="coa.pdf", data=PDF, **extra):
    payload = {"standard": name, "file": (io.BytesIO(data), filename)}
    payload.update(extra)
    return client.post("/api/qc-standards/certificates", data=payload,
                       content_type="multipart/form-data")


class TestTheCertificateIsReachable:
    def test_a_certificate_can_be_uploaded_and_listed(self, client):
        assert _upload(client).status_code == 200
        body = client.get("/api/qc-standards/certificates?standard="
                          + STANDARD.replace(" ", "%20")).get_json()
        assert [c["filename"] for c in body["certificates"]] == ["coa.pdf"]

    def test_it_comes_back_as_the_bytes_that_went_in(self, client):
        uid = _upload(client).get_json()["certificate"]["uid"]
        got = client.get(f"/api/qc-standards/certificates/{uid}/download")
        assert got.status_code == 200
        assert got.data == PDF
        assert got.headers["Content-Type"] == "application/pdf"
        assert "coa.pdf" in got.headers["Content-Disposition"]

    def test_a_standard_with_no_certificate_says_so_and_is_not_an_error(
            self, client):
        body = client.get("/api/qc-standards/certificates?standard="
                          + STANDARD.replace(" ", "%20")).get_json()
        assert body["certificates"] == []

    def test_it_can_be_deleted(self, client):
        uid = _upload(client).get_json()["certificate"]["uid"]
        assert client.delete(
            f"/api/qc-standards/certificates/{uid}").status_code == 200
        body = client.get("/api/qc-standards/certificates?standard="
                          + STANDARD.replace(" ", "%20")).get_json()
        assert body["certificates"] == []


class TestTheNameIsNeverAPathSegment:
    """A standard is named by a person and may contain anything, including a
    slash. Routing on it is a trap; this is the test that keeps it out."""

    def test_a_standard_whose_name_contains_a_slash_still_works(self, app):
        gw = app.config["GW"]
        odd = "Gasoline / RON check"
        QcSampleStore(gw).save(QcSample(name=odd, sample_id_val="STD-9"))
        client = app.test_client()
        with client.session_transaction() as s:
            s["user"] = "ryan"
        assert _upload(client, name=odd).status_code == 200
        body = client.get("/api/qc-standards/certificates",
                          query_string={"standard": odd}).get_json()
        assert [c["filename"] for c in body["certificates"]] == ["coa.pdf"]

    def test_asking_without_naming_a_standard_is_a_400_not_an_empty_list(
            self, client):
        """An empty list would read as "this standard has no certificate",
        which is a sentence about a standard nobody named."""
        r = client.get("/api/qc-standards/certificates")
        assert r.status_code == 400


class TestARefusedWriteIsNotARejectedFile:
    def test_a_file_that_is_not_a_pdf_is_a_400_with_nothing_to_retry(
            self, client):
        r = _upload(client, filename="notes.txt", data=b"hello")
        assert r.status_code == 400
        assert r.get_json().get("retry") is False

    def test_no_file_at_all_is_a_400(self, client):
        r = client.post("/api/qc-standards/certificates",
                        data={"standard": STANDARD},
                        content_type="multipart/form-data")
        assert r.status_code == 400

    def test_a_busy_labcore_is_retryable_and_not_a_bad_file(self, app):
        """The distinction the person holding the certificate acts on."""
        gw = app.config["GW"]
        real = gw.sql

        def refusing(sql, args=None, **kw):
            if "lem_standard_documents" in sql and sql.strip().upper().startswith(
                    "INSERT"):
                return {"error": "LabCore is busy", "busy": True,
                        "retry_after": 5}
            return real(sql, args, **kw)

        gw.sql = refusing
        client = app.test_client()
        with client.session_transaction() as s:
            s["user"] = "ryan"
        r = _upload(client)
        assert r.status_code in (502, 503), r.status_code
        assert r.get_json().get("retry") is not False


class TestTheExpiryReportIsTheAssessmentAnswer:
    def test_an_expired_certificate_is_a_finding(self, client):
        _upload(client, filename="old.pdf", expires_at="2020-01-01")
        body = client.get("/api/qc-standards/certificate-expiry").get_json()
        assert STANDARD in [row["standard"] for row in body["expired"]]

    def test_a_current_certificate_is_not_a_finding(self, client):
        _upload(client, filename="new.pdf", expires_at="2099-01-01")
        body = client.get("/api/qc-standards/certificate-expiry").get_json()
        assert STANDARD not in [row["standard"] for row in body["expired"]]

    def test_the_report_says_what_day_it_judged_against(self, client):
        body = client.get("/api/qc-standards/certificate-expiry").get_json()
        assert body.get("as_of")

    def test_an_unreadable_labcore_is_not_a_clean_bill_of_health(self, app):
        """The one report whose whole purpose is being produced during an
        assessment. "Nothing is out of date" must be impossible to produce
        from an outage."""
        gw = app.config["GW"]

        def blind(sql, args=None, **kw):
            if "lem_standard_documents" in sql:
                return {"error": "LabCore is busy", "busy": True}
            return {"ok": True, "rows": []}

        gw.read_sql = blind
        client = app.test_client()
        with client.session_transaction() as s:
            s["user"] = "ryan"
        r = client.get("/api/qc-standards/certificate-expiry")
        assert r.status_code >= 400, r.get_json()


class TestAuthIsRequiredToChangeAnything:
    def test_an_anonymous_upload_is_refused(self, app):
        r = app.test_client().post(
            "/api/qc-standards/certificates",
            data={"standard": STANDARD, "file": (io.BytesIO(PDF), "coa.pdf")},
            content_type="multipart/form-data")
        assert r.status_code == 401

    def test_an_anonymous_delete_is_refused(self, app, client):
        uid = _upload(client).get_json()["certificate"]["uid"]
        assert app.test_client().delete(
            f"/api/qc-standards/certificates/{uid}").status_code == 401


class TestItStaysWired:
    """`levels.py` shipped a tripwire because declared-but-inert and working
    look identical from the outside. This is that tripwire for the certificate
    store: the routes exist, and they reach the real module."""

    def test_web_app_actually_imports_the_certificate_store(self):
        import web_app
        assert "standard_documents" in open(web_app.__file__).read()

    def test_the_routes_are_registered(self, app):
        rules = {str(r) for r in app.url_map.iter_rules()}
        for rule in ("/api/qc-standards/certificates",
                     "/api/qc-standards/certificates/<uid>/download",
                     "/api/qc-standards/certificate-expiry"):
            assert rule in rules, rule
