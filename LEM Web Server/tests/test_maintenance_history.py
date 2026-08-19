"""PM and calibration: completable from the floor, with a history you can read.

V4 could complete a task but kept its record in a per-machine CSV nobody
looked at, and V5 could complete one but then showed only "last done" — a single
date with no idea who did it or what they found. The completions are already in
`lem_machine_log` (kind `pm` / `calibration`); this exposes them.

Also a lab-wide view: a manager needs "what is overdue anywhere", not one
machine's dialog at a time.
"""
import json
from datetime import date, datetime

import pytest

from labcore_gateway import FakeLabCoreGateway
from maintenance_store import MaintenanceStore, MaintTaskRecord


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


def seed_completions(gw, uid="m1"):
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
           "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
           "detail TEXT)")
    rows = [
        ("m1", "2026-05-02T09:00:00", "pm", {"task": "Monthly PM",
         "completed": "2026-05-02", "note": "cleaned cell", "by": "kaden"}),
        ("m1", "2026-06-01T09:00:00", "pm", {"task": "Monthly PM",
         "completed": "2026-06-01", "note": "replaced filter", "by": "sam"}),
        ("m1", "2026-01-15T09:00:00", "calibration", {"task": "Annual cal",
         "completed": "2026-01-15", "note": "cert 8812", "by": "kaden"}),
        ("m2", "2026-06-01T09:00:00", "pm", {"task": "Other machine PM",
         "completed": "2026-06-01", "note": "", "by": "sam"}),
    ]
    for machine_uid, ts, kind, detail in rows:
        gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
               "test_name, value, detail) VALUES (?,?,?,'','','',?)",
               [machine_uid, ts, kind, json.dumps(detail)])


# ── per-machine completion history ──────────────────────────────────────────

class TestMachineHistory:
    def test_completions_come_back_newest_first(self, gw, client):
        seed_completions(gw)
        body = client.get("/api/machines/m1/maintenance-history").get_json()
        dates = [e["completed"] for e in body["history"]]
        assert dates == ["2026-06-01", "2026-05-02", "2026-01-15"]

    def test_each_entry_names_the_task_and_who_did_it(self, gw, client):
        seed_completions(gw)
        newest = client.get(
            "/api/machines/m1/maintenance-history").get_json()["history"][0]
        assert newest["task"] == "Monthly PM"
        assert newest["by"] == "sam"
        assert newest["note"] == "replaced filter"
        assert newest["kind"] == "pm"

    def test_calibrations_and_pms_are_both_included(self, gw, client):
        seed_completions(gw)
        kinds = {e["kind"] for e in client.get(
            "/api/machines/m1/maintenance-history").get_json()["history"]}
        assert kinds == {"pm", "calibration"}

    def test_it_can_be_filtered_to_one_kind(self, gw, client):
        seed_completions(gw)
        body = client.get(
            "/api/machines/m1/maintenance-history?kind=calibration").get_json()
        assert [e["task"] for e in body["history"]] == ["Annual cal"]

    def test_other_machines_are_not_mixed_in(self, gw, client):
        seed_completions(gw)
        tasks = [e["task"] for e in client.get(
            "/api/machines/m1/maintenance-history").get_json()["history"]]
        assert "Other machine PM" not in tasks

    def test_a_machine_with_no_history_is_empty_not_an_error(self, client):
        r = client.get("/api/machines/nobody/maintenance-history")
        assert r.status_code == 200 and r.get_json()["history"] == []

    def test_a_corrupt_detail_blob_does_not_break_the_list(self, gw, client):
        seed_completions(gw)
        gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
               "test_name, value, detail) VALUES ('m1','2026-07-01T09:00:00',"
               "'pm','','','','{not json')")
        body = client.get("/api/machines/m1/maintenance-history").get_json()
        assert len(body["history"]) == 4        # still listed, just blank-ish

    def test_runs_and_qc_are_not_treated_as_maintenance(self, gw, client):
        seed_completions(gw)
        gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
               "test_name, value, detail) VALUES ('m1','2026-07-02T09:00:00',"
               "'qc','CP','Cloud Point','-7.2','{}')")
        kinds = {e["kind"] for e in client.get(
            "/api/machines/m1/maintenance-history").get_json()["history"]}
        assert "qc" not in kinds


# ── lab-wide view: what is due anywhere ─────────────────────────────────────

class TestFleetMaintenance:
    def seed_tasks(self, gw):
        store = MaintenanceStore(gw)
        store.save(MaintTaskRecord(uid="t1", machine_uid="m1",
                                   name="Monthly PM", kind="pm",
                                   interval_days=30, last_done="2026-06-01"))
        store.save(MaintTaskRecord(uid="t2", machine_uid="m2",
                                   name="Annual cal", kind="calibration",
                                   interval_days=365, last_done=""))
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
               "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
               "reason TEXT, updated_at TEXT)")
        for uid, title in (("m1", "OptiMPP 1"), ("m2", "Multitek NS")):
            gw.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
                   [uid, title, "GREEN", "ok", "2026-08-03T09:00:00"])

    def test_every_machines_tasks_are_listed(self, gw, client):
        self.seed_tasks(gw)
        body = client.get("/api/maintenance").get_json()
        assert {t["machine_uid"] for t in body["tasks"]} == {"m1", "m2"}

    def test_tasks_carry_the_machine_name(self, gw, client):
        """A lab-wide list is useless if it only shows uids."""
        self.seed_tasks(gw)
        titles = {t["machine_title"] for t in
                  client.get("/api/maintenance").get_json()["tasks"]}
        assert titles == {"OptiMPP 1", "Multitek NS"}

    def test_a_never_completed_task_is_flagged(self, gw, client):
        self.seed_tasks(gw)
        cal = [t for t in client.get("/api/maintenance").get_json()["tasks"]
               if t["uid"] == "t2"][0]
        assert cal["status"] == "YELLOW"
        assert cal["last_done"] == ""

    def test_it_reports_how_many_need_attention(self, gw, client):
        self.seed_tasks(gw)
        body = client.get("/api/maintenance").get_json()
        assert body["due_count"] >= 1

    def test_no_tasks_anywhere_is_an_empty_list(self, client):
        body = client.get("/api/maintenance").get_json()
        assert body["tasks"] == [] and body["due_count"] == 0


# ── completing from the web ─────────────────────────────────────────────────

class TestCompletingOnTheWeb:
    def a_task(self, gw):
        return MaintenanceStore(gw).save(MaintTaskRecord(
            uid="t1", machine_uid="m1", name="Monthly PM", kind="pm",
            interval_days=30, last_done="2026-06-01"))

    def test_completing_needs_an_account(self, gw, client):
        self.a_task(gw)
        r = client.post("/api/maintenance/t1/complete", json={"note": "x"})
        assert r.status_code == 401

    def test_completing_records_who_did_it(self, gw, signed_in):
        self.a_task(gw)
        signed_in.post("/api/maintenance/t1/complete",
                       json={"note": "cleaned cell"})
        entry = signed_in.get(
            "/api/machines/m1/maintenance-history").get_json()["history"][0]
        assert entry["by"] == "kaden"
        assert entry["note"] == "cleaned cell"

    def test_the_schedule_moves_from_the_completion_date(self, gw, signed_in):
        """Ryan: the next due date is mathematical, off the entry date."""
        self.a_task(gw)
        signed_in.post("/api/maintenance/t1/complete",
                       json={"when": "2026-07-01", "note": "done"})
        task = MaintenanceStore(gw).get("t1")
        assert task.last_done == "2026-07-01"
        assert task.next_due() == date(2026, 7, 31)      # +30 days

    def test_completing_an_unknown_task_is_a_404(self, signed_in):
        r = signed_in.post("/api/maintenance/ghost/complete", json={})
        assert r.status_code == 404
