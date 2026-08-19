"""TDD for the V5 web layer (app factory) — UI + backend verification.

The dashboard (templates/dashboard.html) is static HTML+JS that polls the API,
so UI verification is: does `/` render, and do the JSON endpoints return the
documented shapes with live status computed from LabCore data.
"""

import json

import pytest

from labcore_gateway import FakeLabCoreGateway
from db_config_store import DbConfigStore
from models import AppConfig, BoxConfig, SampleSpec, SampleTestSpec, WatchedTarget
from web_app import create_app


def _seed_qc(gw, lab_id="STD-1", test_name="Flash Point", value="65", updated_at="2023-01-01 09:00:00"):
    gw.write("insert_sample", {"lab_id": lab_id, "customer": "QC"})
    gw.write("add_test", {"lab_id": lab_id, "test_name": test_name})
    gw.write("update_cell", {"lab_id": lab_id, "test_name": test_name, "value": value, "updated_at": updated_at})


def _seed_config(gw):
    sample = SampleSpec(name="ContextA", sample_id_val="STD-1",
                        tests=[SampleTestSpec(name="Flash", value_col="Flash Point", expected=65.0, std_dev=2.0)])
    box = BoxConfig(uid="box1", title="GC-1", csv_path="",
                    watched_targets=[WatchedTarget(sample="ContextA", test="Flash")])
    cfg = AppConfig(version=5, poll_minutes=5, map_locked=False,
                    sample_id_column="Lab ID", samples=[sample], boxes=[box])
    DbConfigStore(gw).save(cfg)


@pytest.fixture
def client(monkeypatch):
    gw = FakeLabCoreGateway()
    _seed_qc(gw)
    _seed_config(gw)
    app = create_app(gw, admin_password="Admin1", secret="test-secret")
    app.config.update(TESTING=True)
    return app.test_client()


def test_dashboard_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True).lower()
    assert "<!doctype html" in body or "<html" in body


def test_api_status_reports_live_green_from_labcore(client, mock_now):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "boxes" in data and "generated_at" in data
    box = data["boxes"][0]
    assert box["uid"] == "box1"
    assert box["status"] == "GREEN"
    assert box["status_color"]  # colour resolved
    assert box["results"][0]["value"] == 65.0
    assert box["results"][0]["in_spec"] is True


def test_api_status_reports_red_when_out_of_spec(monkeypatch):
    gw = FakeLabCoreGateway()
    _seed_qc(gw, value="200")  # far out of 65 ± 4
    _seed_config(gw)
    app = create_app(gw)
    resp = app.test_client().get("/api/status")
    assert resp.get_json()["boxes"][0]["status"] == "RED"


def test_api_config_is_db_backed(client):
    data = client.get("/api/config").get_json()
    assert [b["uid"] for b in data["boxes"]] == ["box1"]
    assert data["samples"][0]["sample_id_val"] == "STD-1"


def test_api_me_returns_auth_state(client):
    data = client.get("/api/me").get_json()
    assert data["authenticated"] is False


def test_add_box_requires_auth_then_persists(client):
    # Unauthenticated mutation is rejected.
    resp = client.post("/api/boxes", json={"title": "New Box"})
    assert resp.status_code in (401, 403)

    # Log in, then add.
    login = client.post("/api/login", json={"username": "admin", "password": "Admin1"})
    assert login.status_code == 200

    resp = client.post("/api/boxes", json={"title": "New Box"})
    assert resp.status_code == 200
    new_uid = resp.get_json()["box"]["uid"]

    cfg = client.get("/api/config").get_json()
    titles = {b["title"] for b in cfg["boxes"]}
    assert "New Box" in titles
    assert any(b["uid"] == new_uid for b in cfg["boxes"])


def test_refresh_endpoint_ok(client):
    assert client.post("/api/refresh").status_code == 200
