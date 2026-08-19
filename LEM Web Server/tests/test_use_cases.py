"""End-to-end use cases across the whole system.

Each test walks a real workflow the lab performs, exercising the server AND
the station-module contract in one go — the module's own queries are run
verbatim against the same gateway, so a break in continuity between the two
halves fails here rather than on the floor.
"""
import json
import sys
from pathlib import Path

import pytest

from labcore_gateway import FakeLabCoreGateway
from machine_map import QcTargetStore, WatchedTarget
from qc_samples import QcSample, QcSampleStore, QcSampleTest

# The station module lives in the LAB-lem project; import it so both halves
# of the system are checked against one another.
MODULE_DIR = Path("/Volumes/Labsharedrive/Ryan C/LAB-lem/LEM Station Module")
if MODULE_DIR.exists() and str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))
lem = pytest.importorskip("lem_station_module")


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
    c = app.test_client()
    c.post("/api/login", json={"username": "k", "password": "good"})
    return c


def register_machine(gw, uid, title):
    """What a station module does on its first poll."""
    gw.sql(lem.STATUS_TABLE_DDL)
    sql, args = lem.build_status_upsert(
        lem.Machine(uid=uid, title=title),
        lem.MachineEvaluation(status="UNKNOWN", reason="No valid QC data found."),
        __import__("datetime").datetime(2026, 7, 31, 8, 0))
    gw.sql(sql, args)


def module_pulls_specs(gw, machine):
    """Run the module's OWN queries against LabCore and derive its specs —
    this is the seam where server and module must agree."""
    samples = gw.read_sql(lem.QC_SAMPLES_QUERY)
    targets = gw.read_sql(lem.QC_TARGETS_QUERY, [machine.uid])
    library = lem.parse_qc_sample_rows(samples.get("rows") or [])
    assigned = [] if targets.get("error") else [
        {"sample": r["sample_name"], "test": r["test_name"]}
        for r in targets.get("rows") or []]
    return lem.specs_from_qc_samples(machine, library, targets=assigned)


# ── Use case 1: stand up a new instrument and get it into QC ────────────────

class TestBringAMachineIntoService:
    def test_full_path_from_registration_to_green(self, gw, client):
        # 1. the module registers itself on first poll
        register_machine(gw, "m1", "OptiMPP 1")
        machines = client.get("/api/machines?fresh=1").get_json()["machines"]
        assert [m["title"] for m in machines] == ["OptiMPP 1"]
        assert machines[0]["status"] == "UNKNOWN"
        assert machines[0]["sub_statuses"]["qc"] == "UNKNOWN"

        # 2. the lab defines the CRM it will be checked against
        r = client.post("/api/qc-samples", json={
            "name": "Cloud CRM", "sample_id_val": "CP",
            "tests": [{"name": "Cloud Point", "value_col": "Cloud Point",
                       "expected": -7.4, "std_dev": 2.8, "k": 1.0,
                       "units": "C"}]})
        assert r.status_code == 200

        # 3. and assigns it to this instrument
        assert client.post("/api/machines/m1/qc-targets", json={"targets": [
            {"sample": "Cloud CRM", "test": "Cloud Point"}]}).status_code == 200

        # 4. the module pulls that config back down and builds its spec
        machine = lem.Machine(uid="m1", title="OptiMPP 1", mappings=[
            lem.MethodMapping(methods=["Cloud Point"],
                              selector=lem.Selector(mode="cell", index=1))])
        machine.tests = module_pulls_specs(gw, machine)
        assert [s.name for s in machine.tests] == ["Cloud Point"]
        assert machine.tests[0].sample_id == "CP"
        assert machine.tests[0].expected == -7.4

        # 5. the CRM is run: parse → evaluate → publish
        from datetime import datetime
        now = datetime(2026, 7, 31, 9, 0)
        result = lem.parse_print(machine, "CP,-7.5")
        row = result.to_row(now)
        assert row["Lab ID"] == "CP" and row["Cloud Point"] == "-7.5"
        ev = lem.evaluate_machine(machine, [row], now)
        assert ev.status == "GREEN"
        assert ev.sub_statuses["qc"] == "GREEN"

        sql, args = lem.build_status_upsert(machine, ev, now)
        gw.sql(sql, args)
        gw.sql(lem.SUBSTATUS_TABLE_DDL)
        sql, args = lem.build_substatus_upsert(machine, ev, now)
        gw.sql(sql, args)

        # 6. the floor now shows it green, with the QC pill green
        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["status"] == "GREEN"
        assert m["sub_statuses"]["qc"] == "GREEN"
        assert m["qc_targets"] == [{"sample": "Cloud CRM", "test": "Cloud Point"}]


# ── Use case 2: a QC failure reaches the floor ──────────────────────────────

class TestQcFailureSurfaces:
    def test_out_of_spec_run_turns_the_machine_red(self, gw, client):
        from datetime import datetime
        register_machine(gw, "m1", "OptiMPP 1")
        client.post("/api/qc-samples", json={
            "name": "Cloud CRM", "sample_id_val": "CP",
            "tests": [{"name": "Cloud Point", "value_col": "Cloud Point",
                       "expected": -7.4, "std_dev": 2.8, "k": 1.0}]})
        # The assignment is required since 2026-08-03: QC is never applied
        # automatically, so an unassigned instrument has no specs to fail.
        client.post("/api/login", json={"username": "k", "password": "good"})
        client.post("/api/machines/m1/qc-targets", json={"targets": [
            {"sample": "Cloud CRM", "test": "Cloud Point"}]})
        machine = lem.Machine(uid="m1", title="OptiMPP 1", mappings=[
            lem.MethodMapping(methods=["Cloud Point"],
                              selector=lem.Selector(mode="cell", index=1))])
        machine.tests = module_pulls_specs(gw, machine)
        assert machine.tests, "no QC assigned, so nothing could go out of spec"

        now = datetime(2026, 7, 31, 9, 0)
        row = lem.parse_print(machine, "CP,-15.0").to_row(now)   # way low
        ev = lem.evaluate_machine(machine, [row], now)
        assert ev.status == "RED"
        sql, args = lem.build_status_upsert(machine, ev, now)
        gw.sql(sql, args)
        gw.sql(lem.SUBSTATUS_TABLE_DDL)
        sql, args = lem.build_substatus_upsert(machine, ev, now)
        gw.sql(sql, args)

        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["status"] == "RED"
        assert m["sub_statuses"]["qc"] == "RED"
        assert "Cloud Point" in m["reason"]


# ── Use case 3: the CRM lot runs out ───────────────────────────────────────

class TestLotChangeover:
    def test_machines_keep_qc_across_a_lot_change(self, gw, client):
        register_machine(gw, "m1", "OptiMPP 1")
        client.post("/api/qc-samples", json={
            "name": "Cloud CRM", "sample_id_val": "CP",
            "tests": [{"name": "Cloud Point", "value_col": "Cloud Point",
                       "expected": -7.4, "std_dev": 2.8, "k": 1.0}]})
        client.post("/api/machines/m1/qc-targets", json={"targets": [
            {"sample": "Cloud CRM", "test": "Cloud Point"}]})

        r = client.post("/api/qc-samples/changeover", json={
            "old_name": "Cloud CRM", "new_name": "Cloud CRM AB26",
            "new_id_val": "CP26"})
        assert r.get_json()["moved"] == 1

        # The module now checks the NEW lab id, with the inherited spec.
        machine = lem.Machine(uid="m1", mappings=[
            lem.MethodMapping(methods=["Cloud Point"],
                              selector=lem.Selector(mode="cell", index=1))])
        specs = module_pulls_specs(gw, machine)
        assert [s.sample_id for s in specs] == ["CP26"]
        assert specs[0].expected == -7.4        # inherited, not reset


# ── Use case 4: an operator takes a machine out of service ─────────────────

class TestOperatorOverride:
    def test_service_flag_reaches_the_module_and_back(self, gw, client):
        from datetime import datetime
        register_machine(gw, "m1", "OptiMPP 1")
        assert client.post("/api/machines/m1/override", json={
            "override": "SERVICE", "comment": "pump replaced"}).status_code == 200

        # the module reads the control channel on its next sync
        rows = gw.read_sql("SELECT machine_uid, manual_override FROM "
                           "lem_machine_control")
        overrides = lem.extract_overrides(rows.get("rows") or [])
        assert overrides["m1"] == "SERVICE"

        machine = lem.Machine(uid="m1", manual_override=overrides["m1"])
        ev = lem.evaluate_machine(machine, [], datetime(2026, 7, 31, 9, 0))
        assert ev.status == "SERVICE"
        assert "pump" not in ev.reason      # comment lives in the log, not here

    def test_clearing_the_override_returns_control(self, gw, client):
        register_machine(gw, "m1", "OptiMPP 1")
        client.post("/api/machines/m1/override",
                    json={"override": "SERVICE", "comment": "x"})
        client.post("/api/machines/m1/override",
                    json={"override": "", "comment": "back in service"})
        rows = gw.read_sql("SELECT machine_uid, manual_override FROM "
                           "lem_machine_control")
        assert lem.extract_overrides(rows.get("rows") or [])["m1"] == ""


# ── Use case 5: rearranging the floor, and freezing it ─────────────────────

class TestFloorLayout:
    def test_move_then_lock_then_refuse(self, gw, client):
        register_machine(gw, "m1", "OptiMPP 1")
        assert client.post("/api/machines/m1/position",
                           json={"x": 4.1, "y": 2.05}).status_code == 200
        assert client.get("/api/machines?fresh=1").get_json()["machines"][0]["pos"] \
            == [4.1, 2.05]

        client.post("/api/map", json={"locked": True})
        r = client.post("/api/machines/m1/position", json={"x": 0, "y": 0})
        assert r.status_code == 409
        assert client.get("/api/machines?fresh=1").get_json()["machines"][0]["pos"] \
            == [4.1, 2.05]                     # unchanged


# ── Use case 6: retiring a stale instrument ────────────────────────────────

class TestRetireMachine:
    def test_delete_clears_every_trace_but_keeps_history(self, gw, client):
        register_machine(gw, "m1", "Ghost")
        client.post("/api/machines/m1/position", json={"x": 1, "y": 1})
        client.post("/api/qc-samples", json={
            "name": "Cloud CRM", "sample_id_val": "CP", "tests": []})
        client.post("/api/machines/m1/qc-targets", json={"targets": [
            {"sample": "Cloud CRM", "test": "Cloud Point"}]})
        gw.sql(lem.LOG_TABLE_DDL)
        sql, args = lem.build_log_insert(
            "m1", "run", __import__("datetime").datetime(2026, 7, 31, 8, 0),
            lab_id="37100")
        gw.sql(sql, args)

        assert client.delete("/api/machines/m1").status_code == 200
        assert client.get("/api/machines?fresh=1").get_json()["machines"] == []
        assert QcTargetStore(gw).targets("m1") == []
        # history is deliberately preserved
        assert client.get("/api/machines/m1/events").get_json()["events"]


# ── Use case 7: PM and calibration drive the pills ─────────────────────────

class TestMaintenancePills:
    def test_overdue_calibration_shows_red_on_its_own_pill(self, gw, client):
        from datetime import datetime
        register_machine(gw, "m1", "OptiMPP 1")
        machine = lem.Machine(uid="m1", tests=[
            lem.TestSpec(name="Cloud Point", value_col="Cloud Point",
                         expected=-7.4, std_dev=2.8, k=1.0, sample_id="CP")],
            maintenance=[
                lem.MaintTask(uid="c", name="Annual cal", kind="calibration",
                              interval_days=365, last_done="2020-01-01"),
                lem.MaintTask(uid="p", name="Monthly PM", kind="pm",
                              interval_days=30, last_done="2026-07-25")])
        now = datetime(2026, 7, 31, 9, 0)
        row = {"Lab ID": "CP", "Cloud Point": "-7.5",
               "parsed_date": "2026-07-31", "parsed_time": "08:00:00"}
        ev = lem.evaluate_machine(machine, [row], now)
        assert ev.sub_statuses == {"qc": "GREEN", "pm": "GREEN",
                                   "calibration": "RED"}
        assert ev.status == "RED"           # overall follows the worst pill

        gw.sql(lem.SUBSTATUS_TABLE_DDL)
        sql, args = lem.build_substatus_upsert(machine, ev, now)
        gw.sql(sql, args)
        sql, args = lem.build_status_upsert(machine, ev, now)
        gw.sql(sql, args)
        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["sub_statuses"]["calibration"] == "RED"
        assert m["sub_statuses"]["qc"] == "GREEN"


# ── Use case 8: nothing is lost while LabCore is down ──────────────────────

class TestOutageContinuity:
    def test_module_keeps_evaluating_without_labcore(self):
        """The instrument must keep judging its own QC offline — the
        outage only delays publishing."""
        from datetime import datetime
        machine = lem.Machine(uid="m1", tests=[
            lem.TestSpec(name="Cloud Point", value_col="Cloud Point",
                         expected=-7.4, std_dev=2.8, k=1.0, sample_id="CP")],
            mappings=[lem.MethodMapping(
                methods=["Cloud Point"],
                selector=lem.Selector(mode="cell", index=1))])
        now = datetime(2026, 7, 31, 9, 0)
        row = lem.parse_print(machine, "CP,-7.5").to_row(now)
        ev = lem.evaluate_machine(machine, [row], now)
        assert ev.status == "GREEN"          # no LabCore involved at all
