"""Don't let someone delete a config out from under a running parser.

A config row is just data, but a module may be actively running it — parsing
prints against it right now. Deleting that silently leaves an instrument
recording into a configuration that no longer exists.

So the config listing reports whether a module is live on each one, and delete
refuses until the caller confirms while naming what it is about to break.
Liveness is the heartbeat, the same signal the floor already uses.
"""
from datetime import datetime, timedelta

import pytest

from labcore_gateway import FakeLabCoreGateway
from machine_configs import MachineConfigStore


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


def beat(gw, uid, when):
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_heartbeat ("
           "machine_uid TEXT PRIMARY KEY, last_poll TEXT, watching TEXT)")
    gw.sql("INSERT INTO lem_machine_heartbeat (machine_uid, last_poll, "
           "watching) VALUES (?, ?, ?) ON CONFLICT(machine_uid) DO UPDATE SET "
           "last_poll=excluded.last_poll",
           [uid, when.isoformat(), "single_csv C:/p.csv"])


def a_config(uid="m1"):
    return {"uid": uid, "title": "OptiMPP 1", "csv_path": "C:/p.csv"}


# ── the listing tells you what is live ──────────────────────────────────────

class TestListingReportsLiveness:
    def test_a_config_with_a_fresh_beat_is_in_use(self, gw, client):
        MachineConfigStore(gw).save("m1", "OptiMPP 1", a_config())
        beat(gw, "m1", datetime.now())
        row = client.get("/api/machine-configs").get_json()["configs"][0]
        assert row["in_use"] is True
        assert row["last_poll"]

    def test_a_config_whose_module_went_quiet_is_not_in_use(self, gw, client):
        MachineConfigStore(gw).save("m1", "OptiMPP 1", a_config())
        beat(gw, "m1", datetime.now() - timedelta(hours=3))
        row = client.get("/api/machine-configs").get_json()["configs"][0]
        assert row["in_use"] is False

    def test_a_config_no_module_ever_ran_is_not_in_use(self, gw, client):
        MachineConfigStore(gw).save("m1", "OptiMPP 1", a_config())
        row = client.get("/api/machine-configs").get_json()["configs"][0]
        assert row["in_use"] is False and row["last_poll"] in (None, "")

    def test_liveness_is_per_config(self, gw, client):
        store = MachineConfigStore(gw)
        store.save("m1", "Live one", a_config("m1"))
        store.save("m2", "Quiet one", a_config("m2"))
        beat(gw, "m1", datetime.now())
        rows = {c["title"]: c for c in
                client.get("/api/machine-configs").get_json()["configs"]}
        assert rows["Live one"]["in_use"] is True
        assert rows["Quiet one"]["in_use"] is False


# ── delete has to be confirmed while a parser is on it ──────────────────────

class TestGuardedDelete:
    def test_deleting_an_in_use_config_is_refused_without_confirmation(
            self, gw, client):
        MachineConfigStore(gw).save("m1", "OptiMPP 1", a_config())
        beat(gw, "m1", datetime.now())
        r = client.delete("/api/machine-configs/m1")
        assert r.status_code == 409
        body = r.get_json()
        assert body["in_use"] is True
        assert "OptiMPP 1" in body["error"]      # name what is about to break
        assert MachineConfigStore(gw).get("m1") is not None

    def test_confirming_deletes_it(self, gw, client):
        MachineConfigStore(gw).save("m1", "OptiMPP 1", a_config())
        beat(gw, "m1", datetime.now())
        r = client.delete("/api/machine-configs/m1", json={"confirm": True})
        assert r.status_code == 200
        assert MachineConfigStore(gw).get("m1") is None

    def test_an_idle_config_needs_no_confirmation(self, gw, client):
        """Only a live parser earns the extra step."""
        MachineConfigStore(gw).save("m1", "OptiMPP 1", a_config())
        assert client.delete("/api/machine-configs/m1").status_code == 200
        assert MachineConfigStore(gw).get("m1") is None

    def test_deleting_still_needs_an_account(self, gw):
        from web_app import create_app
        app = create_app(gw, authenticator=StubAuth(), secret="s")
        app.config["TESTING"] = True
        MachineConfigStore(gw).save("m1", "OptiMPP 1", a_config())
        assert app.test_client().delete(
            "/api/machine-configs/m1").status_code == 401

    def test_deleting_the_whole_machine_is_guarded_the_same_way(
            self, gw, client):
        """`DELETE /api/machines/<uid>` drops the config too, so it must not
        become a way around the guard."""
        MachineConfigStore(gw).save("m1", "OptiMPP 1", a_config())
        beat(gw, "m1", datetime.now())
        r = client.delete("/api/machines/m1")
        assert r.status_code == 409
        assert MachineConfigStore(gw).get("m1") is not None

    def test_confirming_deletes_the_whole_machine(self, gw, client):
        MachineConfigStore(gw).save("m1", "OptiMPP 1", a_config())
        beat(gw, "m1", datetime.now())
        r = client.delete("/api/machines/m1", json={"confirm": True})
        assert r.status_code == 200
        assert MachineConfigStore(gw).get("m1") is None
