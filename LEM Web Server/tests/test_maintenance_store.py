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


# ── the write queue says no, and the store must not say yes ─────────────────
#
# LabCore's HTTP write queue serialises at roughly 1.5 writes a second and
# refuses past ~100 pending BY ANSWERING. No exception is raised, and the answer
# does not necessarily carry an "error" key. Every test below refuses in that
# real shape, because {"error": ...} is the one shape the old code already
# handled and proving anything with it would be arranging the case the bug
# cannot occur in.

from labcore_result import LabCoreError, LabCoreUnavailable
from maintenance_store import MaintenanceWriteError


REFUSAL = {"queued": False, "pending": 137}


class QueueFull:
    """A real gateway until it refuses — then LabCore's actual refusal shape.

    Reads keep working on purpose: a test that a WRITE raised is only worth
    anything if it can then look at the stored state and show nothing changed.
    """

    def __init__(self, refuse_after=None):
        self.real = FakeLabCoreGateway()
        self.refusing = False
        self.refuse_after = refuse_after
        self.writes = 0

    def refuse(self):
        self.refusing = True

    def allow(self):
        self.refusing = False
        self.refuse_after = None

    def sql(self, sql, args=None, **kw):
        self.writes += 1
        if self.refusing or (self.refuse_after is not None
                             and self.writes > self.refuse_after):
            return dict(REFUSAL)
        return self.real.sql(sql, args)

    def read_sql(self, sql, args=None, **kw):
        return self.real.read_sql(sql, args)


class Unreadable:
    """LabCore is up enough to be asked and not up enough to answer.

    The routine 8s read timeout this repo documents, not a missing table.
    """

    def __init__(self):
        self.real = FakeLabCoreGateway()

    def sql(self, sql, args=None, **kw):
        return self.real.sql(sql, args)

    def read_sql(self, sql, args=None, **kw):
        return {"error": "HTTPSConnectionPool(...): Read timed out"}


@pytest.fixture
def queue_full():
    return QueueFull()


def warm(gw):
    """A store with its schema declared and one saved task, then refusing."""
    store = MaintenanceStore(gw)
    store.save(task())
    gw.refuse()
    return store


class TestARefusedWriteIsNeverReportedAsSaved:
    def test_save_raises_and_writes_nothing(self, queue_full):
        store = warm(queue_full)
        with pytest.raises(MaintenanceWriteError):
            store.save(task(uid="t2", name="Monthly PM", kind="pm",
                            interval_days=30))
        queue_full.allow()
        assert [t.uid for t in store.for_machine("m1")] == ["t1"]

    def test_editing_an_existing_task_raises_and_leaves_it_alone(self,
                                                                queue_full):
        store = warm(queue_full)
        with pytest.raises(MaintenanceWriteError):
            store.save(task(interval_days=7))
        queue_full.allow()
        assert store.for_machine("m1")[0].interval_days == 365

    def test_complete_raises_and_the_calibration_is_still_outstanding(self):
        """The most expensive write in this file to lose: an annual calibration
        ticked off on the floor that LabCore never recorded stays overdue in the
        record every station module reads."""
        gw = QueueFull()
        store = MaintenanceStore(gw)
        store.save(task(last_done=""))
        gw.refuse()
        with pytest.raises(MaintenanceWriteError):
            store.complete("t1", "2026-07-31", "filters swapped")
        gw.allow()
        t = store.for_machine("m1")[0]
        assert t.last_done == "" and t.note == ""

    def test_delete_raises_and_the_task_is_still_there(self, queue_full):
        store = warm(queue_full)
        with pytest.raises(MaintenanceWriteError):
            store.delete("t1")
        queue_full.allow()
        assert [t.uid for t in store.for_machine("m1")] == ["t1"]

    def test_forget_raises_and_the_retired_machine_keeps_its_rows(self,
                                                                  queue_full):
        """Silently orphaned PM rows re-attach if that uid is ever registered
        again, and show up on "what is overdue anywhere" for a machine nobody
        can find."""
        store = warm(queue_full)
        with pytest.raises(MaintenanceWriteError):
            store.forget("m1")
        queue_full.allow()
        assert len(store.for_machine("m1")) == 1

    def test_a_refused_create_table_raises_rather_than_latching_ready(self):
        """`_ready` used to be set whatever LabCore answered, so one refused
        CREATE meant every INSERT for the rest of the process aimed at a table
        that did not exist — and reported success."""
        gw = QueueFull()
        gw.refuse()
        store = MaintenanceStore(gw)
        with pytest.raises(MaintenanceWriteError):
            store.ensure_schema()
        assert store._ready is False

    def test_the_store_recovers_once_the_queue_drains(self):
        """The consequence of not latching: the next call re-declares and
        works, instead of failing forever with a "ready" flag."""
        gw = QueueFull()
        gw.refuse()
        store = MaintenanceStore(gw)
        with pytest.raises(MaintenanceWriteError):
            store.save(task())
        gw.allow()
        store.save(task())
        assert [t.uid for t in store.for_machine("m1")] == ["t1"]

    def test_the_refusal_is_catchable_as_one_labcore_error(self):
        gw = QueueFull()
        gw.refuse()
        with pytest.raises(LabCoreError):
            MaintenanceStore(gw).save(task())


class TestCouldNotAskIsNotDoesNotExist:
    def test_get_raises_rather_than_answering_none(self):
        """`None` here is a 404 from the completion route. A task that exists,
        reported as "no such task", is a save turned into a lie about the data."""
        store = MaintenanceStore(Unreadable())
        with pytest.raises(LabCoreUnavailable):
            store.get("t1")

    def test_for_machine_does_not_report_an_empty_schedule(self):
        store = MaintenanceStore(Unreadable())
        with pytest.raises(LabCoreUnavailable):
            store.for_machine("m1")

    def test_all_does_not_hand_the_csv_importer_an_empty_lab(self):
        """`plan_import` diffs the CSV against this. An empty answer during a
        blip plans a fresh duplicate of every task in the building."""
        store = MaintenanceStore(Unreadable())
        with pytest.raises(LabCoreUnavailable):
            store.all()
