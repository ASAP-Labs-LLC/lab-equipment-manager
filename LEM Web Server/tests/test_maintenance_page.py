"""One page that answers "what maintenance is overdue anywhere?".

Per-machine dialogs can't answer it — you'd have to open every instrument in
the lab. This is the page a manager opens on Monday: everything due, worst
first, completable in place, with what was recently done underneath.
"""
import json

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
def page(client):
    return client.get("/maintenance").get_data(as_text=True)


def seed(gw):
    store = MaintenanceStore(gw)
    store.save(MaintTaskRecord(uid="t1", machine_uid="m1", name="Monthly PM",
                               kind="pm", interval_days=30,
                               last_done="2020-01-01"))          # overdue
    store.save(MaintTaskRecord(uid="t2", machine_uid="m2", name="Annual cal",
                               kind="calibration", interval_days=365,
                               last_done=""))                    # never done
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
           "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
           "reason TEXT, updated_at TEXT)")
    for uid, title in (("m1", "OptiMPP 1"), ("m2", "Multitek NS")):
        gw.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
               [uid, title, "GREEN", "ok", "2026-08-03T09:00:00"])
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
           "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
           "detail TEXT)")
    for uid, ts, kind, detail in [
        ("m1", "2026-07-01T09:00:00", "pm",
         {"task": "Monthly PM", "completed": "2026-07-01", "by": "sam",
          "note": "filter"}),
        ("m2", "2026-06-02T09:00:00", "calibration",
         {"task": "Annual cal", "completed": "2026-06-02", "by": "kaden",
          "note": "cert 8812"}),
    ]:
        gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
               "test_name, value, detail) VALUES (?,?,?,'','','',?)",
               [uid, ts, kind, json.dumps(detail)])


# ── the fleet-wide completion feed ──────────────────────────────────────────

class TestFleetHistory:
    def test_it_covers_every_machine(self, gw, client):
        seed(gw)
        body = client.get("/api/maintenance-history").get_json()
        assert {e["machine_uid"] for e in body["history"]} == {"m1", "m2"}

    def test_entries_carry_the_machine_name(self, gw, client):
        seed(gw)
        titles = {e["machine_title"] for e in
                  client.get("/api/maintenance-history").get_json()["history"]}
        assert titles == {"OptiMPP 1", "Multitek NS"}

    def test_newest_first(self, gw, client):
        seed(gw)
        dates = [e["completed"] for e in
                 client.get("/api/maintenance-history").get_json()["history"]]
        assert dates == sorted(dates, reverse=True)

    def test_it_can_be_filtered_to_calibrations(self, gw, client):
        seed(gw)
        body = client.get("/api/maintenance-history?kind=calibration").get_json()
        assert [e["task"] for e in body["history"]] == ["Annual cal"]

    def test_qc_and_runs_are_not_included(self, gw, client):
        seed(gw)
        gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
               "test_name, value, detail) VALUES ('m1','2026-07-09T09:00:00',"
               "'qc','CP','Cloud','-7.2','{}')")
        kinds = {e["kind"] for e in
                 client.get("/api/maintenance-history").get_json()["history"]}
        assert kinds <= {"pm", "calibration"}

    def test_nothing_done_anywhere_is_an_empty_list(self, client):
        assert client.get("/api/maintenance-history").get_json()["history"] == []


# ── the page ────────────────────────────────────────────────────────────────

class TestThePage:
    def test_it_exists(self, client):
        assert client.get("/maintenance").status_code == 200

    def test_it_is_reachable_from_the_mode_selector(self, client):
        assert 'href="/maintenance"' in client.get("/").get_data(as_text=True)

    def test_it_is_reachable_from_the_floor(self, client):
        assert 'href="/maintenance"' in client.get("/floor").get_data(as_text=True)

    def test_it_can_get_back(self, page):
        assert 'href="/"' in page

    def test_it_reads_the_fleet_endpoints(self, page):
        assert "/api/maintenance" in page
        assert "/api/maintenance-history" in page

    def test_it_can_filter_by_kind(self, page):
        assert 'id="fKind"' in page

    def test_tasks_are_completable_in_place(self, page):
        assert "data-done" in page

    def test_it_shows_what_was_recently_completed(self, page):
        assert 'id="doneList"' in page

    def test_it_survives_labcore_being_down(self):
        from web_app import create_app

        class Dead:
            base_url = "https://labcore.example"

            def is_running(self):
                return False

            def sql(self, *a, **k):
                return {"error": "unreachable"}

            def write(self, *a, **k):
                return {"error": "unreachable"}

            def read_sql(self, *a, **k):
                return {"error": "unreachable"}

            def get_samples(self, **k):
                return None

            def get_test_names(self, **k):
                return None

        app = create_app(Dead(), authenticator=StubAuth(), secret="s")
        app.config["TESTING"] = True
        assert app.test_client().get("/maintenance").status_code == 200
