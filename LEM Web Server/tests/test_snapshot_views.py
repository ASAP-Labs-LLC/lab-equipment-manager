"""The floor stopped costing LabCore ops. The other pages hadn't.

`/api/machines` is served from the background snapshot, but `/api/schedule` and
`/api/maintenance` were still doing their own round-trips on every request —
**re-reading rows the snapshot had already fetched seconds earlier**. Two ops
each, ~0.5s each on the live system, multiplied by every open screen.

So the snapshot keeps the raw rows it read, not just the machine payload it built
from them, and those pages render from the same rows. The `sched`, `holiday`,
`maint` and `status` tables are already in the one-op batched read: serving these
pages from them is free.

What is deliberately NOT snapshotted: anything computed from `now`. The schedule's
`open_now` and a task's RED/YELLOW status are worked out at request time from the
snapshotted rows, so a lab that opens at 07:00 says so at 07:00 — not up to one
refresh interval later.
"""
import threading
from datetime import datetime

import pytest

from labcore_gateway import FakeLabCoreGateway


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


class Counting(FakeLabCoreGateway):
    def __init__(self):
        super().__init__()
        self.reads = []
        self.lock = threading.Lock()

    def read_sql(self, sql, args=None, **kw):
        with self.lock:
            self.reads.append(" ".join(sql.split()))
        return super().read_sql(sql, args, **kw)

    def is_running(self):
        return True


@pytest.fixture
def gw():
    g = Counting()
    g.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
          "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
          "reason TEXT, updated_at TEXT)")
    g.sql("INSERT INTO lem_machine_status VALUES "
          "('m1','OptiMPP 1','GREEN','ok','2026-08-03T09:00:00')")
    return g


@pytest.fixture
def client(gw):
    from web_app import create_app
    app = create_app(gw, authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client()


def signed_in(client):
    client.post("/api/login", json={"username": "k", "password": "good"})
    return client


# ── the schedule page ───────────────────────────────────────────────────────

class TestScheduleIsServedFromTheSnapshot:
    def test_it_costs_no_labcore_ops(self, gw, client):
        client.get("/api/machines")           # build the snapshot once
        gw.reads.clear()
        for _ in range(10):
            assert client.get("/api/schedule").status_code == 200
        assert gw.reads == [], f"{len(gw.reads)} ops for 10 schedule reads"

    def test_the_answer_is_still_right(self, gw, client):
        signed_in(client)
        client.post("/api/schedule", json={"working_days": [0, 1, 2],
                                           "opens": "06:00", "closes": "22:00"})
        client.post("/api/holidays", json={"day": "2026-12-25",
                                           "name": "Christmas"})
        body = client.get("/api/schedule").get_json()
        assert body["working_days"] == [0, 1, 2]
        assert body["opens"] == "06:00" and body["closes"] == "22:00"
        assert body["holidays"] == {"2026-12-25": "Christmas"}

    def test_open_now_follows_the_clock_not_the_refresh_interval(
            self, gw, client, monkeypatch):
        """A lab that opens at 07:00 must say so at 07:00, not up to twelve
        seconds later — so open_now is computed per request, never snapshotted."""
        import web_app
        signed_in(client)
        client.post("/api/schedule", json={"working_days": [0, 1, 2, 3, 4],
                                           "opens": "07:00", "closes": "18:00"})
        monkeypatch.setattr(web_app, "_now", lambda: datetime(2026, 8, 3, 6, 59))
        assert client.get("/api/schedule").get_json()["open_now"] is False
        gw.reads.clear()
        monkeypatch.setattr(web_app, "_now", lambda: datetime(2026, 8, 3, 7, 1))
        assert client.get("/api/schedule").get_json()["open_now"] is True
        assert gw.reads == [], "recomputing the clock should not re-read LabCore"

    def test_defaults_still_apply_before_anything_is_saved(self, client):
        body = client.get("/api/schedule").get_json()
        assert body["working_days"] == [0, 1, 2, 3, 4]
        assert body["holidays"] == {}


# ── the fleet maintenance page ──────────────────────────────────────────────

class TestMaintenanceIsServedFromTheSnapshot:
    def add_task(self, client, **kw):
        payload = {"name": "Annual calibration", "kind": "calibration",
                   "interval_days": 365, "last_done": "2020-01-01", "note": ""}
        payload.update(kw)
        return client.post("/api/machines/m1/maintenance", json=payload)

    def test_it_costs_no_labcore_ops(self, gw, client):
        signed_in(client)
        self.add_task(client)
        client.get("/api/maintenance")
        gw.reads.clear()
        for _ in range(10):
            assert client.get("/api/maintenance").status_code == 200
        assert gw.reads == [], f"{len(gw.reads)} ops for 10 maintenance reads"

    def test_the_task_and_its_machine_name_are_there(self, gw, client):
        signed_in(client)
        self.add_task(client)
        body = client.get("/api/maintenance").get_json()
        row = [t for t in body["tasks"] if t["name"] == "Annual calibration"][0]
        assert row["machine_title"] == "OptiMPP 1"
        assert row["status"] == "RED"          # last done in 2020
        assert body["due_count"] >= 1

    def test_a_new_task_appears_without_waiting_for_the_interval(self, gw, client):
        signed_in(client)
        client.get("/api/maintenance")
        self.add_task(client, name="Weekly clean", kind="pm", interval_days=7)
        names = [t["name"] for t in client.get("/api/maintenance").get_json()["tasks"]]
        assert "Weekly clean" in names

    def test_a_deleted_task_disappears(self, gw, client):
        signed_in(client)
        self.add_task(client)
        tasks = client.get("/api/maintenance").get_json()["tasks"]
        client.delete(f"/api/maintenance/{tasks[0]['uid']}")
        assert client.get("/api/maintenance").get_json()["tasks"] == []

    def test_overdue_is_judged_against_today_not_snapshot_time(
            self, gw, client, monkeypatch):
        """Interval status is worked out per request, so a task that falls due
        overnight is red in the morning without a restart."""
        import web_app
        signed_in(client)
        monkeypatch.setattr(web_app, "_now", lambda: datetime(2026, 8, 3, 9, 0))
        self.add_task(client, name="Weekly clean", kind="pm",
                      interval_days=7, last_done="2026-08-03")
        first = client.get("/api/maintenance").get_json()["tasks"][0]
        assert first["status"] == "GREEN"
        gw.reads.clear()
        monkeypatch.setattr(web_app, "_now", lambda: datetime(2026, 9, 3, 9, 0))
        later = client.get("/api/maintenance").get_json()["tasks"][0]
        assert later["status"] == "RED"
        assert gw.reads == [], "re-judging the date should not re-read LabCore"

    def test_ordering_puts_the_overdue_first(self, gw, client):
        signed_in(client)
        self.add_task(client, name="Fine", kind="pm", interval_days=3650,
                      last_done="2026-08-01")
        self.add_task(client, name="Overdue", kind="pm", interval_days=1,
                      last_done="2020-01-01")
        tasks = client.get("/api/maintenance").get_json()["tasks"]
        assert tasks[0]["name"] == "Overdue"


# ── the snapshot keeps what it read ─────────────────────────────────────────

class TestSnapshotKeepsRawRows:
    def test_the_tables_are_available_next_to_the_machines(self, gw):
        from snapshot_service import SnapshotService, build_machines
        from web_app import STATUS_COLORS, _beat_is_fresh, _now
        svc = SnapshotService(gw, builder=lambda t: build_machines(
            t, _now(), _beat_is_fresh, STATUS_COLORS))
        svc.refresh()
        tables = svc.tables()
        assert "status" in tables and "sched" in tables and "maint" in tables

    def test_a_failed_refresh_keeps_the_last_good_tables(self, gw):
        from snapshot_service import SnapshotService, build_machines
        from web_app import STATUS_COLORS, _beat_is_fresh, _now
        svc = SnapshotService(gw, builder=lambda t: build_machines(
            t, _now(), _beat_is_fresh, STATUS_COLORS))
        svc.refresh()
        assert svc.tables().get("status")

        def dead(*a, **k):
            raise RuntimeError("LabCore down")

        gw.read_sql = dead
        svc.refresh()
        assert svc.tables().get("status"), "lost the rows the pages render from"
