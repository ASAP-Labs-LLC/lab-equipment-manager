"""QC bridge between the LEM master view and the LEM Station modules.

The station modules READ their QC specs from `lem_qc_specs` and WRITE their
live state to `lem_machine_status` / `lem_machine_log`; the master view owns
the specs and pushes operator commands through `lem_machine_control`.
These tests pin that contract from the server's side.
"""
import pytest

from labcore_gateway import FakeLabCoreGateway
from qc_specs import (
    QcSpec,
    QcSpecStore,
    MachineStateReader,
)


@pytest.fixture
def gw():
    return FakeLabCoreGateway()


@pytest.fixture
def store(gw):
    return QcSpecStore(gw)


# ── lem_qc_specs: the table the station modules read ─────────────────────────

class TestQcSpecStore:
    def test_ensure_schema_creates_the_table(self, gw, store):
        store.ensure_schema()
        res = gw.read_sql("SELECT name FROM sqlite_master WHERE name='lem_qc_specs'")
        assert res.get("rows")

    def test_save_then_list(self, store):
        store.save(QcSpec(machine_uid="m1", test_name="Cloud Point",
                          sample_id="QC-CP-1", expected=-9.0, std_dev=0.5,
                          k=2.0, units="C"))
        specs = store.list_specs()
        assert len(specs) == 1
        spec = specs[0]
        assert spec.machine_uid == "m1"
        assert spec.test_name == "Cloud Point"
        assert spec.sample_id == "QC-CP-1"
        assert spec.expected == -9.0
        assert spec.std_dev == 0.5
        assert spec.k == 2.0
        assert spec.units == "C"

    def test_save_is_an_upsert_on_machine_and_test(self, store):
        store.save(QcSpec("m1", "Cloud Point", "QC-CP-1", -9.0, 0.5))
        store.save(QcSpec("m1", "Cloud Point", "QC-CP-2", -8.5, 0.4, k=3.0))
        specs = store.list_specs()
        assert len(specs) == 1                 # replaced, not duplicated
        assert specs[0].sample_id == "QC-CP-2"
        assert specs[0].expected == -8.5
        assert specs[0].k == 3.0

    def test_specs_scoped_to_one_machine(self, store):
        store.save(QcSpec("m1", "Cloud Point", "QC-CP-1", -9.0, 0.5))
        store.save(QcSpec("m2", "Pour Point", "QC-PP-1", -31.0, 1.0))
        assert [s.test_name for s in store.list_specs("m1")] == ["Cloud Point"]
        assert [s.test_name for s in store.list_specs("m2")] == ["Pour Point"]

    def test_delete_spec(self, store):
        store.save(QcSpec("m1", "Cloud Point", "QC-CP-1", -9.0, 0.5))
        store.delete("m1", "Cloud Point")
        assert store.list_specs() == []

    def test_rows_match_what_the_station_module_expects(self, gw, store):
        # The module runs exactly this query and reads these column names.
        store.save(QcSpec("m1", "Cloud Point", "QC-CP-1", -9.0, 0.5, 2.0, "C"))
        res = gw.read_sql(
            "SELECT machine_uid, test_name, sample_id, expected, std_dev, k, "
            "units FROM lem_qc_specs")
        assert not res.get("error")
        row = res["rows"][0]
        assert row["machine_uid"] == "m1"
        assert row["test_name"] == "Cloud Point"
        assert float(row["expected"]) == -9.0
        assert float(row["k"]) == 2.0

    def test_blank_test_name_is_rejected(self, store):
        with pytest.raises(ValueError):
            store.save(QcSpec("m1", "  ", "QC", 1.0, 0.1))

    def test_negative_std_dev_is_rejected(self, store):
        with pytest.raises(ValueError):
            store.save(QcSpec("m1", "Cloud Point", "QC", 1.0, -0.1))


# ── Reading what the station modules published ───────────────────────────────

class TestMachineStateReader:
    def seed_status(self, gw, uid, title, status, reason, ts):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
               "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
               "reason TEXT, updated_at TEXT)")
        gw.sql("INSERT INTO lem_machine_status (machine_uid, title, status, "
               "reason, updated_at) VALUES (?, ?, ?, ?, ?)",
               [uid, title, status, reason, ts])

    def test_lists_machines_newest_first(self, gw):
        self.seed_status(gw, "m1", "OptiMPP 1", "GREEN", "System nominal",
                         "2026-07-28T10:00:00")
        self.seed_status(gw, "m2", "Multitek S", "RED", "QC out of spec: Flash",
                         "2026-07-28T12:00:00")
        machines = MachineStateReader(gw).machines()
        assert [m["title"] for m in machines] == ["Multitek S", "OptiMPP 1"]
        assert machines[0]["status"] == "RED"
        assert machines[0]["status_color"]        # dashboard needs a color

    def test_missing_table_gives_empty_list_not_an_error(self, gw):
        assert MachineStateReader(gw).machines() == []

    def test_events_for_a_machine(self, gw):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log ("
               "machine_uid TEXT, ts TEXT, kind TEXT, lab_id TEXT, "
               "test_name TEXT, value TEXT, detail TEXT)")
        for i, kind in enumerate(("run", "qc", "status_change")):
            gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, "
                   "lab_id, test_name, value, detail) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?)",
                   ["m1", f"2026-07-28T1{i}:00:00", kind, "37037",
                    "Cloud Point", "-9.1", "{}"])
        events = MachineStateReader(gw).events("m1")
        assert [e["kind"] for e in events] == ["status_change", "qc", "run"]
        assert events[0]["lab_id"] == "37037"

    def test_events_missing_table_is_empty(self, gw):
        assert MachineStateReader(gw).events("m1") == []


# ── lem_machine_control: overrides pushed to a station module ────────────────

class TestMachineControl:
    def test_set_override_round_trips(self, gw, store):
        reader = MachineStateReader(gw)
        reader.set_override("m1", "SERVICE", "pump replaced")
        res = gw.read_sql("SELECT machine_uid, manual_override FROM "
                          "lem_machine_control")
        assert res["rows"] == [{"machine_uid": "m1",
                                "manual_override": "SERVICE"}]

    def test_override_is_upserted_not_duplicated(self, gw):
        reader = MachineStateReader(gw)
        reader.set_override("m1", "SERVICE", "a")
        reader.set_override("m1", "", "back in service")
        res = gw.read_sql("SELECT machine_uid, manual_override FROM "
                          "lem_machine_control")
        assert res["rows"] == [{"machine_uid": "m1", "manual_override": ""}]

    def test_invalid_override_rejected(self, gw):
        with pytest.raises(ValueError):
            MachineStateReader(gw).set_override("m1", "BANANA", "why")


# ── Web API: the master view's QC endpoints ─────────────────────────────────

@pytest.fixture
def client(gw):
    from web_app import create_app
    app = create_app(gw, admin_password="pw", secret="s")
    app.config["TESTING"] = True
    return app.test_client()


def login(client):
    return client.post("/api/login", json={"password": "pw"})


class TestQcApi:
    def seed_machine(self, gw, uid="m1", title="OptiMPP 1"):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
               "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
               "reason TEXT, updated_at TEXT)")
        gw.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
               [uid, title, "UNKNOWN", "No valid QC data found.",
                "2026-07-28T12:00:00"])

    def test_machines_endpoint_lists_station_modules(self, gw, client):
        self.seed_machine(gw)
        body = client.get("/api/machines?fresh=1").get_json()
        assert body["machines"][0]["title"] == "OptiMPP 1"
        assert body["machines"][0]["status"] == "UNKNOWN"

    def test_qc_specs_endpoint_round_trip(self, gw, client):
        login(client)
        r = client.post("/api/qc-specs", json={
            "machine_uid": "m1", "test_name": "Cloud Point",
            "sample_id": "QC-CP-1", "expected": -9.0, "std_dev": 0.5,
            "k": 2.0, "units": "C"})
        assert r.status_code == 200
        specs = client.get("/api/qc-specs").get_json()["specs"]
        assert specs[0]["test_name"] == "Cloud Point"
        assert specs[0]["low"] == -10.0 and specs[0]["high"] == -8.0

    def test_qc_spec_write_requires_auth(self, client):
        r = client.post("/api/qc-specs", json={"machine_uid": "m1",
                                               "test_name": "X",
                                               "expected": 1, "std_dev": 1})
        assert r.status_code == 401

    def test_invalid_spec_returns_400(self, client):
        login(client)
        r = client.post("/api/qc-specs", json={"machine_uid": "m1",
                                               "test_name": "",
                                               "expected": 1, "std_dev": 1})
        assert r.status_code == 400

    def test_delete_spec(self, gw, client):
        login(client)
        client.post("/api/qc-specs", json={"machine_uid": "m1",
                                           "test_name": "Cloud Point",
                                           "expected": -9.0, "std_dev": 0.5})
        r = client.delete("/api/qc-specs",
                          json={"machine_uid": "m1", "test_name": "Cloud Point"})
        assert r.status_code == 200
        assert client.get("/api/qc-specs").get_json()["specs"] == []

    def test_machine_events_endpoint(self, gw, client):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log ("
               "machine_uid TEXT, ts TEXT, kind TEXT, lab_id TEXT, "
               "test_name TEXT, value TEXT, detail TEXT)")
        gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
               ["m1", "2026-07-28T12:00:00", "run", "37037", "", "", "{}"])
        body = client.get("/api/machines/m1/events").get_json()
        assert body["events"][0]["lab_id"] == "37037"

    def test_override_endpoint_requires_auth_and_comment(self, gw, client):
        assert client.post("/api/machines/m1/override",
                           json={"override": "SERVICE",
                                 "comment": "x"}).status_code == 401
        login(client)
        assert client.post("/api/machines/m1/override",
                           json={"override": "SERVICE",
                                 "comment": ""}).status_code == 400
        r = client.post("/api/machines/m1/override",
                        json={"override": "SERVICE", "comment": "pump"})
        assert r.status_code == 200
        rows = gw.read_sql("SELECT manual_override FROM lem_machine_control")
        assert rows["rows"][0]["manual_override"] == "SERVICE"

    def test_test_names_endpoint_offers_labcore_methods(self, gw, client):
        gw.write("insert_sample", {"lab_id": "L1"})
        gw.write("add_test", {"lab_id": "L1", "test_name": "Cloud Point"})
        body = client.get("/api/test-names").get_json()
        assert "Cloud Point" in body["tests"]
