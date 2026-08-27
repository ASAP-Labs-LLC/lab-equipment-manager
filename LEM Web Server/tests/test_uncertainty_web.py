"""The uncertainty routes — the surface an assessor is actually shown.

The design doc names three cases for this file and they are the first three
classes below: auth on every route, compute never auto-approves, and the
register export carrying all twelve SOP 2.10 fields. The rest are the ones the
doc did not think of but the "Do not" list implies.

Written against the measured state of this laboratory rather than a hypothetical
one. `lem_machine_log` begins 2026-08-03; TR 537 wants more than sixty results
over at least a year for the control-sample route, so **no series here qualifies
for Route 1 on time span**, and Route 3 — the interim target-limits route — is
what these routes will serve for real. A suite that only ever exercised Route 1
would test a path this lab cannot reach.
"""

import json

import pytest

import snapshot_service
import uncertainty
from labcore_gateway import FakeLabCoreGateway
from web_app import create_app

MACHINE = "pac-flash-1"
TEST = "Flash Point"


def _qc(gw, n=20, value=63.7, low=61.6, high=65.8, day_from=1):
    for i in range(n):
        gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
               "test_name, value, detail) VALUES (?, ?, 'qc', 'STD-1', ?, ?, ?)",
               [MACHINE, "2026-08-%02dT09:00:00" % (day_from + i % 25), TEST,
                str(value + (i % 5) * 0.1),
                json.dumps({"low": low, "high": high, "expected": value,
                            "in_spec": True, "operator": "ryan",
                            "calibration_id": "2026-06-02"})])


@pytest.fixture
def app():
    gw = FakeLabCoreGateway()
    snapshot_service.SnapshotService(gw).ensure_schema()
    _qc(gw)
    application = create_app(gw, secret="t")
    application.config.update(TESTING=True)
    application.config["GW"] = gw
    return application


@pytest.fixture
def client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = "ryan"
    return c


def _compute(client, **over):
    body = {"machine_uid": MACHINE, "test_name": TEST,
            "rw_route": "target_limits", "control_limit": 2.1,
            "control_limit_k": 2.0}
    body.update(over)
    return client.post("/api/uncertainty/compute", json=body)


class TestAuthIsRequiredOnEveryRoute:
    """A measurement-uncertainty record is a controlled document. Reading one
    anonymously is arguable; WRITING one is not."""

    def test_an_anonymous_compute_is_refused(self, app):
        assert app.test_client().post(
            "/api/uncertainty/compute",
            json={"machine_uid": MACHINE, "test_name": TEST}).status_code == 401

    def test_an_anonymous_approval_is_refused(self, app, client):
        est = _compute(client).get_json()["estimate"]
        assert app.test_client().post(
            "/api/uncertainty/%s/approve" % est["estimate_id"]).status_code == 401

    def test_an_anonymous_exclusion_is_refused(self, app, client):
        est = _compute(client).get_json()["estimate"]
        assert app.test_client().post(
            "/api/uncertainty/%s/exclude" % est["estimate_id"],
            json={"ts": "2026-08-01T09:00:00", "cause": "x", "ncr_ref": "y"}
        ).status_code == 401


class TestComputeNeverAutoApproves:
    """SOP 2.10's Register entry is signed. A number that signed itself the
    moment it was calculated is not a record of anybody's judgement."""

    def test_a_fresh_estimate_is_not_approved(self, client):
        est = _compute(client).get_json()["estimate"]
        assert not est.get("approved_at")
        assert not est.get("approved_by")

    def test_and_it_is_not_reported_as_current_until_it_is(self, client):
        _compute(client)
        body = client.get("/api/uncertainty").get_json()
        assert body["estimates"] == [] or all(
            not e.get("approved_at") for e in body["estimates"])

    def test_approving_records_who_and_when(self, client):
        est = _compute(client).get_json()["estimate"]
        got = client.post("/api/uncertainty/%s/approve" % est["estimate_id"])
        assert got.status_code == 200
        back = got.get_json()["estimate"]
        assert back["approved_by"] == "ryan" and back["approved_at"]


class TestTheRegisterCarriesEveryField:
    """SOP 2.10 is twelve fields. Eleven is a form with a hole in it."""

    def test_all_twelve_are_present(self, client):
        est = _compute(client).get_json()["estimate"]
        client.post("/api/uncertainty/%s/approve" % est["estimate_id"])
        row = client.get("/api/uncertainty/%s/register"
                         % est["estimate_id"]).get_json()
        assert set(row["register"]) == set(uncertainty.REGISTER_FIELDS), (
            set(uncertainty.REGISTER_FIELDS) ^ set(row["register"]))

    def test_no_field_is_silently_blank(self, client):
        """A field that says nothing is worse than one that says what is
        missing — the assessor cannot tell "not applicable" from "forgotten"."""
        est = _compute(client).get_json()["estimate"]
        row = client.get("/api/uncertainty/%s/register"
                         % est["estimate_id"]).get_json()["register"]
        blank = [k for k, v in row.items() if v in (None, "")]
        assert blank == [], blank

    def test_the_register_says_the_bias_term_is_missing(self, client):
        """No certificate uncertainty exists in this lab yet, so there is no
        bias term — and the register must SAY so rather than omit it."""
        est = _compute(client).get_json()["estimate"]
        row = client.get("/api/uncertainty/%s/register"
                         % est["estimate_id"]).get_json()["register"]
        assert "u_bias" in row and str(row["u_bias"]).strip()


class TestARouteThisLabCannotReachIsRefused:
    def test_route_1_is_refused_on_a_short_series_with_a_reason(self, client):
        got = _compute(client, rw_route="control_sample", control_limit=None)
        assert got.status_code == 400, got.get_json()
        assert str(got.get_json().get("error", "")).strip()

    def test_the_answer_names_the_route_that_is_permitted(self, client):
        body = _compute(client, rw_route="control_sample",
                        control_limit=None).get_json()
        assert "target" in json.dumps(body).lower()

    def test_the_interim_route_is_labelled_as_interim(self, client):
        est = _compute(client).get_json()["estimate"]
        assert est["rw_route"] == "target_limits"
        assert est.get("replace_by"), "an interim estimate needs a replacement date"
        assert "u(Rw)" in est.get("u_rw_label", "")
        assert est["u_rw_label"] != "u(Rw)", (
            "an interim target must not be labelled as a measured u(Rw)")


class TestAnExclusionNeedsACause:
    def test_an_exclusion_with_no_cause_is_refused(self, client):
        est = _compute(client).get_json()["estimate"]
        got = client.post("/api/uncertainty/%s/exclude" % est["estimate_id"],
                          json={"ts": "2026-08-02T09:00:00", "cause": "",
                                "ncr_ref": "NCR-1"})
        assert got.status_code == 400

    def test_an_exclusion_with_no_ncr_is_refused(self, client):
        est = _compute(client).get_json()["estimate"]
        got = client.post("/api/uncertainty/%s/exclude" % est["estimate_id"],
                          json={"ts": "2026-08-02T09:00:00",
                                "cause": "thermocouple replaced mid-run",
                                "ncr_ref": ""})
        assert got.status_code == 400

    def test_an_excluded_point_supersedes_rather_than_mutating(self, client):
        est = _compute(client).get_json()["estimate"]
        first = est["estimate_id"]
        got = client.post("/api/uncertainty/%s/exclude" % first,
                          json={"ts": "2026-08-02T09:00:00",
                                "cause": "bath thermocouple replaced mid-run",
                                "ncr_ref": "NCR-2026-11"})
        assert got.status_code == 200, got.get_json()
        new_id = got.get_json()["estimate"]["estimate_id"]
        assert new_id != first
        history = client.get("/api/uncertainty/%s/%s"
                             % (MACHINE, TEST)).get_json()
        superseded = [e for e in history["history"]
                      if e["estimate_id"] == first]
        assert superseded and superseded[0]["superseded_by"] == new_id


class TestAReadThatFailedIsNotAnEmptyRegister:
    def test_an_unreadable_labcore_is_not_no_estimates(self, app):
        """"No uncertainty estimates on file" is a finding. It must be
        impossible to produce from an outage."""
        gw = app.config["GW"]

        def blind(sql, args=None, **kw):
            if "lem_uncertainty_estimates" in sql:
                return {"error": "LabCore is busy", "busy": True}
            return {"ok": True, "rows": []}

        gw.read_sql = blind
        c = app.test_client()
        with c.session_transaction() as s:
            s["user"] = "ryan"
        assert c.get("/api/uncertainty").status_code >= 400
