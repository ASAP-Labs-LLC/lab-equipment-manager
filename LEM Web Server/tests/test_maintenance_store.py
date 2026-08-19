"""PM and calibration, managed centrally.

V4 tracked maintenance per machine with repeat intervals and a completion
log. The station module already evaluates tasks and drives the PM/CAL
pills — but until now they could only be created on the LabStation itself,
which is the wrong place for a lab manager to work. These live in LabCore
so they can be set from the floor and read by every module.
"""
import pytest

from labcore_gateway import FakeLabCoreGateway
from maintenance_store import MaintTaskRecord, MaintenanceStore


@pytest.fixture
def gw():
    return FakeLabCoreGateway()


@pytest.fixture
def store(gw):
    return MaintenanceStore(gw)


def task(**kw):
    base = dict(uid="t1", machine_uid="m1", name="Annual calibration",
                kind="calibration", interval_days=365, last_done="2026-01-15")
    base.update(kw)
    return MaintTaskRecord(**base)


class TestMaintenanceStore:
    def test_save_and_list(self, store):
        store.save(task())
        tasks = store.for_machine("m1")
        assert len(tasks) == 1
        assert tasks[0].name == "Annual calibration"
        assert tasks[0].kind == "calibration"
        assert tasks[0].interval_days == 365

    def test_scoped_per_machine(self, store):
        store.save(task())
        store.save(task(uid="t2", machine_uid="m2", name="Monthly PM",
                        kind="pm", interval_days=30))
        assert [t.name for t in store.for_machine("m1")] == ["Annual calibration"]
        assert [t.name for t in store.for_machine("m2")] == ["Monthly PM"]

    def test_save_is_an_upsert(self, store):
        store.save(task())
        store.save(task(interval_days=180))
        tasks = store.for_machine("m1")
        assert len(tasks) == 1 and tasks[0].interval_days == 180

    def test_complete_stamps_the_date_and_note(self, store):
        store.save(task(last_done=""))
        store.complete("t1", "2026-07-31", "filters swapped")
        t = store.for_machine("m1")[0]
        assert t.last_done == "2026-07-31"
        assert t.note == "filters swapped"

    def test_delete(self, store):
        store.save(task())
        store.delete("t1")
        assert store.for_machine("m1") == []

    def test_forget_clears_a_retired_machine(self, store):
        store.save(task())
        store.save(task(uid="t2", name="Monthly PM", kind="pm"))
        store.forget("m1")
        assert store.for_machine("m1") == []

    def test_blank_name_refused(self, store):
        with pytest.raises(ValueError):
            store.save(task(name="  "))

    def test_interval_must_be_positive(self, store):
        with pytest.raises(ValueError):
            store.save(task(interval_days=0))

    def test_kind_is_normalised(self, store):
        store.save(task(kind="Calibration"))
        assert store.for_machine("m1")[0].kind == "calibration"
        store.save(task(uid="t2", kind="anything else"))
        assert store.for_machine("m1")[1].kind == "pm"

    def test_missing_table_is_empty(self, gw):
        assert MaintenanceStore(gw).for_machine("m1") == []

    def test_due_dates_are_computed(self, store):
        from datetime import date
        store.save(task(last_done="2026-01-15", interval_days=365))
        t = store.for_machine("m1")[0]
        assert t.next_due() == date(2027, 1, 15)
        assert t.status(date(2026, 7, 31))[0] == "GREEN"
        assert t.status(date(2027, 2, 1))[0] == "RED"
        assert t.status(date(2027, 1, 15))[0] == "YELLOW"

    def test_never_completed_is_yellow_and_has_no_due_date(self, store):
        from datetime import date
        store.save(task(last_done=""))
        t = store.for_machine("m1")[0]
        assert t.next_due() is None
        assert t.status(date(2026, 7, 31))[0] == "YELLOW"

    def test_rows_match_what_the_module_reads(self, gw, store):
        store.save(task())
        res = gw.read_sql("SELECT uid, machine_uid, name, kind, interval_days, "
                          "last_done, note FROM lem_maintenance")
        assert not res.get("error")
        row = res["rows"][0]
        assert row["machine_uid"] == "m1" and row["kind"] == "calibration"


# ── Web API ─────────────────────────────────────────────────────────────────

class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


@pytest.fixture
def client(gw):
    from web_app import create_app
    app = create_app(gw, authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client()


def login(c):
    c.post("/api/login", json={"username": "k", "password": "good"})


class TestMaintenanceApi:
    def test_list_endpoint(self, gw, client):
        MaintenanceStore(gw).save(task())
        body = client.get("/api/machines/m1/maintenance").get_json()
        assert body["tasks"][0]["name"] == "Annual calibration"
        assert body["tasks"][0]["status"] in ("GREEN", "YELLOW", "RED")
        assert body["tasks"][0]["next_due"] == "2027-01-15"

    def test_create_requires_auth(self, client):
        assert client.post("/api/machines/m1/maintenance",
                           json={"name": "PM"}).status_code == 401

    def test_create_and_delete(self, gw, client):
        login(client)
        r = client.post("/api/machines/m1/maintenance", json={
            "name": "Monthly PM", "kind": "pm", "interval_days": 30})
        assert r.status_code == 200
        uid = r.get_json()["task"]["uid"]
        assert len(client.get("/api/machines/m1/maintenance").get_json()["tasks"]) == 1
        assert client.delete(f"/api/maintenance/{uid}").status_code == 200
        assert client.get("/api/machines/m1/maintenance").get_json()["tasks"] == []

    def test_complete_endpoint(self, gw, client):
        login(client)
        MaintenanceStore(gw).save(task(last_done=""))
        r = client.post("/api/maintenance/t1/complete",
                        json={"note": "pump replaced"})
        assert r.status_code == 200
        t = MaintenanceStore(gw).for_machine("m1")[0]
        assert t.last_done and t.note == "pump replaced"

    def test_invalid_task_is_400(self, client):
        login(client)
        assert client.post("/api/machines/m1/maintenance",
                           json={"name": "", "interval_days": 30}).status_code == 400

    def test_machines_payload_carries_maintenance_rollup(self, gw, client):
        MaintenanceStore(gw).save(task(last_done="2020-01-01"))   # overdue
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
               "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
               "reason TEXT, updated_at TEXT)")
        gw.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
               ["m1", "OptiMPP 1", "UNKNOWN", "r", "2026-07-31T12:00:00"])
        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["maintenance_due"] == 1
        assert m["maintenance"][0]["status"] == "RED"

    def test_deleting_a_machine_clears_its_tasks(self, gw, client):
        login(client)
        MaintenanceStore(gw).save(task())
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
               "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
               "reason TEXT, updated_at TEXT)")
        gw.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
               ["m1", "X", "UNKNOWN", "r", "2026-07-31T12:00:00"])
        client.delete("/api/machines/m1")
        assert MaintenanceStore(gw).for_machine("m1") == []
