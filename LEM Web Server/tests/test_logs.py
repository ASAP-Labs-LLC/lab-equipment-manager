"""One searchable log, and an audit trail for the things nobody recorded.

`lem_machine_log` already holds runs, QC verdicts, status changes, overrides,
comments and PM/CAL completions — but there was no page to read it and no way to
filter it. Worse, **configuration changes were recorded nowhere**: editing a QC
spec, assigning targets, running a changeover or deleting a machine left no
trace at all, which is the one class of change you most want to look up later.
"""
import json

import pytest

from labcore_gateway import FakeLabCoreGateway


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


@pytest.fixture
def gw():
    return FakeLabCoreGateway()


@pytest.fixture
def client(gw):
    from web_app import create_app
    app = create_app(gw, authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def signed_in(client):
    client.post("/api/login", json={"username": "k", "password": "good"})
    return client


def seed_log(gw):
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
           "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
           "detail TEXT)")
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
           "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
           "reason TEXT, updated_at TEXT)")
    gw.sql("INSERT INTO lem_machine_status VALUES "
           "('m1','OptiMPP 1','GREEN','ok','2026-08-03T09:00:00')")
    rows = [
        ("m1", "2026-07-01T08:00:00", "run", "37043", "", "", {}),
        ("m1", "2026-07-02T09:00:00", "qc", "CP", "Cloud Point", "-7.2",
         {"in_spec": True}),
        ("m1", "2026-07-03T10:00:00", "status_change", "", "", "",
         {"from": "GREEN", "to": "RED"}),
        ("m1", "2026-07-04T11:00:00", "override", "", "", "",
         {"status": "SERVICE", "comment": "sensor"}),
        ("m2", "2026-07-05T12:00:00", "run", "37050", "", "", {}),
    ]
    for uid, ts, kind, lab, test, val, detail in rows:
        gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
               [uid, ts, kind, lab, test, val, json.dumps(detail)])


# ── the filterable endpoint ─────────────────────────────────────────────────

class TestLogQuery:
    def test_everything_by_default_newest_first(self, gw, client):
        seed_log(gw)
        body = client.get("/api/logs").get_json()
        ts = [e["ts"] for e in body["events"]]
        assert ts == sorted(ts, reverse=True)
        assert len(ts) == 5

    def test_events_carry_the_machine_name(self, gw, client):
        seed_log(gw)
        entry = [e for e in client.get("/api/logs").get_json()["events"]
                 if e["machine_uid"] == "m1"][0]
        assert entry["machine_title"] == "OptiMPP 1"

    def test_it_filters_by_machine(self, gw, client):
        seed_log(gw)
        body = client.get("/api/logs?machine=m2").get_json()
        assert {e["machine_uid"] for e in body["events"]} == {"m2"}

    def test_it_filters_by_kind(self, gw, client):
        seed_log(gw)
        body = client.get("/api/logs?kind=qc").get_json()
        assert {e["kind"] for e in body["events"]} == {"qc"}

    def test_it_accepts_several_kinds(self, gw, client):
        seed_log(gw)
        body = client.get("/api/logs?kind=qc,override").get_json()
        assert {e["kind"] for e in body["events"]} == {"qc", "override"}

    def test_it_filters_from_a_date(self, gw, client):
        seed_log(gw)
        body = client.get("/api/logs?since=2026-07-04").get_json()
        assert len(body["events"]) == 2

    def test_it_filters_to_a_date_inclusively(self, gw, client):
        """`until=2026-07-02` must include everything ON the 2nd."""
        seed_log(gw)
        body = client.get("/api/logs?until=2026-07-02").get_json()
        assert len(body["events"]) == 2

    def test_it_searches_text(self, gw, client):
        seed_log(gw)
        body = client.get("/api/logs?q=37043").get_json()
        assert len(body["events"]) == 1

    def test_the_search_covers_the_detail_blob(self, gw, client):
        seed_log(gw)
        body = client.get("/api/logs?q=sensor").get_json()
        assert [e["kind"] for e in body["events"]] == ["override"]

    def test_filters_combine(self, gw, client):
        seed_log(gw)
        body = client.get("/api/logs?machine=m1&kind=run").get_json()
        assert len(body["events"]) == 1

    def test_it_reports_the_kinds_present(self, gw, client):
        """So the page can offer real filters instead of a hard-coded list."""
        seed_log(gw)
        body = client.get("/api/logs").get_json()
        assert set(body["kinds"]) >= {"run", "qc", "status_change", "override"}

    def test_the_limit_is_capped(self, gw, client):
        seed_log(gw)
        body = client.get("/api/logs?limit=999999").get_json()
        assert len(body["events"]) <= 5

    def test_a_bad_date_is_ignored_rather_than_a_500(self, gw, client):
        seed_log(gw)
        r = client.get("/api/logs?since=not-a-date")
        assert r.status_code == 200

    def test_an_empty_log_is_not_an_error(self, client):
        r = client.get("/api/logs")
        assert r.status_code == 200 and r.get_json()["events"] == []

    def test_it_exports_csv(self, gw, client):
        seed_log(gw)
        r = client.get("/api/logs.csv?kind=qc")
        assert r.status_code == 200
        assert "text/csv" in r.headers["Content-Type"]
        assert "Cloud Point" in r.get_data(as_text=True)


# ── config changes: previously recorded nowhere at all ──────────────────────

class TestConfigAudit:
    def audit(self, client):
        return [e for e in client.get("/api/logs?kind=config").get_json()
                ["events"]]

    def test_saving_a_qc_spec_is_recorded(self, signed_in):
        signed_in.post("/api/qc-specs", json={
            "machine_uid": "m1", "test_name": "Cloud Point",
            "sample_id": "CP", "expected": -7.4, "std_dev": 2.8, "k": 1.0})
        entries = self.audit(signed_in)
        assert entries, "no audit entry for a QC spec change"
        assert entries[0]["by"] == "kaden"

    def test_the_entry_says_what_changed(self, signed_in):
        signed_in.post("/api/qc-specs", json={
            "machine_uid": "m1", "test_name": "Cloud Point",
            "sample_id": "CP", "expected": -7.4, "std_dev": 2.8, "k": 1.0})
        entry = self.audit(signed_in)[0]
        assert "qc-spec" in entry["action"]
        assert "Cloud Point" in json.dumps(entry["detail"])

    def test_deleting_a_qc_spec_is_recorded(self, signed_in):
        signed_in.delete("/api/qc-specs", json={"machine_uid": "m1",
                                                "test_name": "Cloud Point"})
        assert any("delete" in e["action"] for e in self.audit(signed_in))

    def test_saving_a_standard_is_recorded(self, signed_in):
        signed_in.post("/api/qc-samples", json={
            "name": "Cloud CRM", "sample_id_val": "CP", "tests": []})
        assert any("qc-sample" in e["action"] for e in self.audit(signed_in))

    def test_assigning_targets_is_recorded(self, signed_in):
        signed_in.post("/api/machines/m1/qc-targets",
                       json={"targets": [{"sample": "Cloud CRM",
                                          "test": "Cloud Point"}]})
        assert any("qc-target" in e["action"] for e in self.audit(signed_in))

    def test_deleting_a_machine_is_recorded(self, signed_in):
        signed_in.delete("/api/machines/m1")
        assert any("machine" in e["action"] and "delete" in e["action"]
                   for e in self.audit(signed_in))

    def test_changing_the_lab_hours_is_recorded(self, signed_in):
        signed_in.post("/api/schedule", json={"working_days": [0, 1, 2]})
        assert any("schedule" in e["action"] for e in self.audit(signed_in))

    def test_adding_maintenance_is_recorded(self, signed_in):
        signed_in.post("/api/machines/m1/maintenance",
                       json={"name": "Monthly PM", "kind": "pm",
                             "interval_days": 30})
        assert any("maintenance" in e["action"] for e in self.audit(signed_in))

    def test_an_unauthenticated_attempt_records_nothing(self, client):
        """It's refused before it happens, so there's nothing to audit."""
        client.post("/api/qc-specs", json={"machine_uid": "m1",
                                           "test_name": "X"})
        assert self.audit(client) == []

    def test_the_audit_does_not_break_the_action_it_records(self, gw,
                                                            signed_in,
                                                            monkeypatch):
        """A failing audit write must never fail the user's change."""
        real = gw.sql

        def flaky(sql, args=None, **kw):
            if "lem_machine_log" in sql and "INSERT" in sql:
                raise RuntimeError("log write failed")
            return real(sql, args, **kw)

        monkeypatch.setattr(gw, "sql", flaky)
        r = signed_in.post("/api/qc-specs", json={
            "machine_uid": "m1", "test_name": "Cloud Point",
            "sample_id": "CP", "expected": -7.4, "std_dev": 2.8, "k": 1.0})
        assert r.status_code == 200


# ── the page ────────────────────────────────────────────────────────────────

class TestTheLogsPage:
    def test_it_exists(self, client):
        assert client.get("/logs").status_code == 200

    def test_it_is_linked_from_the_floor(self, client):
        assert 'href="/logs"' in client.get("/floor").get_data(as_text=True)

    def test_it_can_get_back(self, client):
        assert 'href="/"' in client.get("/logs").get_data(as_text=True)

    def test_it_has_the_filters(self, client):
        body = client.get("/logs").get_data(as_text=True)
        for ident in ("fMachine", "fKindSel", "fSince", "fUntil", "fQuery"):
            assert f'id="{ident}"' in body, ident

    def test_it_offers_the_csv_export(self, client):
        assert "/api/logs.csv" in client.get("/logs").get_data(as_text=True)
