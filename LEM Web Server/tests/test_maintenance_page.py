"""One view that answers "what maintenance is overdue anywhere?".

Per-machine dialogs can't answer it — you'd have to open every instrument in
the lab. A manager opens it on Monday: everything due, worst first.

It used to be a page of its own (the old PM page). Since ia-final §1 it is the
Instruments list filtered to maintenance (`/instruments?filter=maintenance`,
which `/maintenance` redirects to), and its two jobs moved to where they
belong: a task is marked done in its instrument's record (Maintenance and
calibration), and PM history is imported in Settings › Imports. Piece 14
deleted the old page and its interim `/maintenance/classic` door. The fleet
endpoints below are unchanged and still tested here.
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

class TestTheMaintenanceView:
    VIEW = "/instruments?filter=maintenance"

    def test_it_exists(self, client):
        body = client.get(self.VIEW).get_data(as_text=True)
        assert 'data-testid="instruments-page"' in body

    def test_the_old_address_is_the_instruments_filter(self, client):
        """ia-final §1: PM and calibration are a view of Instruments. The old
        URL is kept as a redirect so a bookmark lands somewhere true."""
        r = client.get("/maintenance")
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/instruments?filter=maintenance")

    def test_the_interim_page_is_gone(self, client):
        assert client.get("/maintenance/classic").status_code == 404

    def test_it_says_where_its_two_jobs_are_done(self, client):
        """No dead end: the view's note names the record's section for
        marking a task done and links Settings › Imports for PM history,
        instead of the deleted page."""
        body = client.get(self.VIEW).get_data(as_text=True)
        note = body[body.index('id="maint-note"'):]
        note = note[:note.index("</p>")]
        assert "Maintenance and calibration" in note
        assert 'href="/settings#imports"' in note
        assert "/maintenance/classic" not in body

    def test_the_record_completes_a_task_in_place(self):
        import pathlib
        js = (pathlib.Path(__file__).resolve().parent.parent / "static" / "js"
              / "record_actions.js").read_text(encoding="utf-8")
        assert "'Mark done…'" in js

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
        assert app.test_client().get(self.VIEW).status_code == 200
