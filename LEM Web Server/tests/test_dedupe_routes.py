"""The approval flow over HTTP: the dry run, Ryan's decision, apply, undo.

D7 is Ryan's decision, not any signed-in person's. Approving and applying
are refused to anybody who is not a configured approver
(`LEM_DEDUPE_APPROVERS`, his sign-in name), and approving asks for the
approver's password again — a tablet left signed in is not a decision.

The approver recorded on `annotation_approval` is the SIGNED-IN person, never
a name the request body supplies: the approval is the evidence that a person
decided, and a field anybody can type is not evidence of anything. Every
route that writes is refused signed out, and the dry run — a read of the
whole record — is too, so a crawler cannot make the server classify 258k rows
on a loop.
"""

import pytest

import dedupe_sim
from dedupe_sim import DUP
from labcore_gateway import FakeLabCoreGateway
from web_app import create_app


PW = "the-admin-password"


@pytest.fixture
def lab():
    return dedupe_sim.build()


@pytest.fixture
def app(lab):
    gw = FakeLabCoreGateway()
    for r in lab.rows:
        res = gw.sql("INSERT INTO lem_machine_log (id, machine_uid, ts, kind, "
                     "lab_id, test_name, value, detail) VALUES "
                     "(?, ?, ?, ?, ?, ?, ?, ?)",
                     [r["id"], r["machine_uid"], r["ts"], r["kind"],
                      r["lab_id"], r["test_name"], r["value"], r["detail"]])
        assert "error" not in res, res
    application = create_app(gw, secret="t", admin_password=PW)
    application.config.update(TESTING=True, DEDUPE_APPROVERS="ryan")
    application.config["GW"] = gw
    return application


@pytest.fixture
def client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = "ryan"
    return c


def _effective(app):
    return {r["id"] for r in app.config["GW"].read_sql(
        "SELECT id FROM lem_machine_log_effective")["rows"]}


class TestSignedOut:
    @pytest.mark.parametrize("method,path,body", [
        ("get", "/api/dedupe/dry-run", None),
        ("post", "/api/dedupe/approve",
         {"machine_uid": "era", "label": "replay_duplicate", "run_id": "x"}),
        ("post", "/api/dedupe/apply", {"approval_id": 1}),
        ("post", "/api/dedupe/reinstate", {"log_ids": [1], "reason": "x"}),
    ])
    def test_every_route_is_refused(self, app, method, path, body):
        c = app.test_client()
        res = getattr(c, method)(path, json=body)
        assert res.status_code == 401
        assert app.config["GW"].read_sql(
            "SELECT COUNT(*) n FROM annotation_approval")["rows"][0]["n"] == 0


class TestTheFlow:
    def test_dry_run_approve_apply_reinstate(self, app, client, lab):
        res = client.get("/api/dedupe/dry-run?since=2026-08-01")
        assert res.status_code == 200
        rep = res.get_json()
        assert rep["hide_candidates"] == len(lab.ids(DUP))
        assert all("since" in b for b in rep["benches"])

        approvals = []
        for bench in rep["benches"]:
            for label, run_id in bench["run_ids"].items():
                r = client.post("/api/dedupe/approve", json={
                    "machine_uid": bench["machine_uid"], "label": label,
                    "run_id": run_id, "approved_by": "somebody else",
                    "password": PW})
                assert r.status_code == 200, r.get_json()
                approvals.append(r.get_json()["approval_id"])
        who = {row["approved_by"] for row in app.config["GW"].read_sql(
            "SELECT approved_by FROM annotation_approval")["rows"]}
        assert who == {"ryan"}                 # the session, not the body

        for aid in approvals:
            r = client.post("/api/dedupe/apply", json={"approval_id": aid})
            assert r.status_code == 200, r.get_json()
        assert lab.ids(DUP) & _effective(app) == set()

        victim = sorted(lab.ids(DUP))[0]
        r = client.post("/api/dedupe/reinstate",
                        json={"log_ids": [victim], "reason": "a real re-run"})
        assert r.status_code == 200 and r.get_json()["reinstated"] == [victim]
        assert victim in _effective(app)

    def test_a_rejection_is_recorded(self, app, client):
        rep = client.get("/api/dedupe/dry-run?machine_uid=era").get_json()
        [bench] = rep["benches"]
        r = client.post("/api/dedupe/approve", json={
            "machine_uid": "era", "label": "replay_duplicate",
            "run_id": bench["run_ids"]["replay_duplicate"],
            "decision": "rejected", "password": PW})
        assert r.status_code == 200
        r = client.post("/api/dedupe/apply",
                        json={"approval_id": r.get_json()["approval_id"]})
        assert r.status_code == 409 and "rejected" in r.get_json()["error"]

    def test_a_stale_report_is_refused_with_the_reason(self, client):
        r = client.post("/api/dedupe/approve", json={
            "machine_uid": "era", "label": "replay_duplicate",
            "run_id": "upto=1;sha=" + "0" * 64, "password": PW})
        assert r.status_code == 409
        assert "changed" in r.get_json()["error"] or \
            "nothing to approve" in r.get_json()["error"]

    def test_a_malformed_request_is_a_400_not_a_500(self, client):
        for body in (None, {}, {"approval_id": "x"}):
            r = client.post("/api/dedupe/apply", json=body)
            assert r.status_code == 400
        r = client.post("/api/dedupe/reinstate", json={"log_ids": "1"})
        assert r.status_code == 400

    def test_a_failed_read_is_a_503_never_an_empty_report(self, app, client):
        gw = app.config["GW"]
        real = gw.read_sql

        def broken(sql, *a, **k):
            if "lem_machine_log WHERE" in sql:
                return {"error": "database is locked"}
            return real(sql, *a, **k)
        gw.read_sql = broken
        r = client.get("/api/dedupe/dry-run")
        assert r.status_code == 503
        assert "locked" in r.get_json()["error"]
        assert "benches" not in r.get_json()


def _count(app, table):
    return app.config["GW"].read_sql(
        "SELECT COUNT(*) n FROM {0}".format(table))["rows"][0]["n"]


class TestOnlyTheApproverDecides:
    """The round-1 critic signed in as an arbitrary "tech1" and the code
    would have accepted that person's approval. D7 is Ryan's."""

    def _as(self, app, user):
        c = app.test_client()
        with c.session_transaction() as s:
            s["user"] = user
        return c

    def _unit(self, client):
        rep = client.get("/api/dedupe/dry-run?machine_uid=era").get_json()
        [bench] = rep["benches"]
        return bench["run_ids"]["replay_duplicate"]

    def test_anyone_signed_in_may_read_the_dry_run(self, app):
        r = self._as(app, "tech1").get("/api/dedupe/dry-run?machine_uid=era")
        assert r.status_code == 200

    def test_somebody_else_cannot_approve_or_apply(self, app):
        tech = self._as(app, "tech1")
        run_id = self._unit(tech)
        r = tech.post("/api/dedupe/approve", json={
            "machine_uid": "era", "label": "replay_duplicate",
            "run_id": run_id, "password": PW})
        assert r.status_code == 403
        assert "tech1" in r.get_json()["error"]
        assert _count(app, "annotation_approval") == 0
        # Ryan approves; tech1 still cannot apply it.
        ryan = self._as(app, "Ryan")              # sign-in names ignore case
        aid = ryan.post("/api/dedupe/approve", json={
            "machine_uid": "era", "label": "replay_duplicate",
            "run_id": run_id, "password": PW}).get_json()["approval_id"]
        r = tech.post("/api/dedupe/apply", json={"approval_id": aid})
        assert r.status_code == 403
        assert _count(app, "log_annotation") == 0

    def test_approving_asks_for_the_password_again(self, app):
        ryan = self._as(app, "ryan")
        run_id = self._unit(ryan)
        for body_pw in (None, "", "wrong"):
            body = {"machine_uid": "era", "label": "replay_duplicate",
                    "run_id": run_id}
            if body_pw is not None:
                body["password"] = body_pw
            r = ryan.post("/api/dedupe/approve", json=body)
            assert r.status_code == 403, body_pw
            assert "password" in r.get_json()["error"]
        assert _count(app, "annotation_approval") == 0

    def test_with_no_approver_configured_nobody_approves(self, app):
        app.config["DEDUPE_APPROVERS"] = ""
        ryan = self._as(app, "ryan")
        r = ryan.post("/api/dedupe/approve", json={
            "machine_uid": "era", "label": "replay_duplicate",
            "run_id": self._unit(ryan), "password": PW})
        assert r.status_code == 403
        assert "LEM_DEDUPE_APPROVERS" in r.get_json()["error"]
        assert _count(app, "annotation_approval") == 0

    def test_anyone_signed_in_may_reinstate(self, app):
        """Reinstating only ever makes a reading count again — the safe
        direction — and it records who and why."""
        ryan = self._as(app, "ryan")
        aid = ryan.post("/api/dedupe/approve", json={
            "machine_uid": "era", "label": "replay_duplicate",
            "run_id": self._unit(ryan), "password": PW}).get_json()[
                "approval_id"]
        assert ryan.post("/api/dedupe/apply",
                         json={"approval_id": aid}).status_code == 200
        hidden = sorted({r["id"] for r in app.config["GW"].read_sql(
            "SELECT id FROM lem_machine_log")["rows"]} - _effective(app))
        r = self._as(app, "tech1").post("/api/dedupe/reinstate", json={
            "log_ids": hidden[:1], "reason": "the printout shows a re-run"})
        assert r.status_code == 200 and r.get_json()["reinstated"] == \
            hidden[:1]
