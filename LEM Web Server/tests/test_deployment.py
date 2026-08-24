"""Deployment contract: a data directory, ``/healthz``, and a version stamp.

LEM is far less exposed here than COA Reviewer was. Its configuration lives in
LabCore's ``lem_*`` tables and the local ``data/`` directory holds regenerable
cache, so a release swap costs it nothing it cannot rebuild. The one thing it
genuinely writes beside its own code is ``restart.log`` — the only record of
*why* a restart failed, and, since a ``.pyw`` under pythonw.exe has no console,
the only way to find out at all. Writing that into a release directory means
losing it on the deploy you most want to read it after.

``/healthz`` is the updater's contract and matters more:

* **No auth**, because the updater calls it on a scratch port before the
  release is live.
* **No LabCore round-trip.** LabCore is a real internet hop behind Cloudflare,
  and this server exists to *stop* per-request LabCore load. SnapshotService
  already tracks reachability as a side effect of its own background reads;
  /healthz reports that, and must not add an op.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ── data directory ──────────────────────────────────────────────────────────

class TestDataDir:
    def test_defaults_to_the_code_directory(self, monkeypatch):
        """Unset, nothing changes for anyone running off the share."""
        import tray

        monkeypatch.delenv("LEM_DATA_DIR", raising=False)
        assert tray.data_dir("/code/here") == "/code/here"

    def test_follows_the_env_var(self, monkeypatch, tmp_path):
        import tray

        monkeypatch.setenv("LEM_DATA_DIR", str(tmp_path))
        assert tray.data_dir("/code/here") == str(tmp_path)

    def test_restart_log_lands_in_the_data_dir(self, monkeypatch, tmp_path):
        import tray

        monkeypatch.setenv("LEM_DATA_DIR", str(tmp_path))
        assert tray.log_path("/code/here") == os.path.join(str(tmp_path), "restart.log")

    def test_restart_log_defaults_beside_the_code(self, monkeypatch):
        import tray

        monkeypatch.delenv("LEM_DATA_DIR", raising=False)
        assert tray.log_path("/code/here") == os.path.join("/code/here", "restart.log")

    def test_note_writes_where_log_path_says(self, monkeypatch, tmp_path):
        """The whole point: a restart diagnostic must survive a release swap."""
        import tray

        monkeypatch.setenv("LEM_DATA_DIR", str(tmp_path))
        tray.note("restart failed for a reason worth keeping", root="/code/here")

        written = (tmp_path / "restart.log").read_text(encoding="utf-8")
        assert "restart failed for a reason worth keeping" in written
        assert not os.path.exists(os.path.join("/code/here", "restart.log"))

    def test_data_dir_is_created_if_missing(self, monkeypatch, tmp_path):
        import tray

        target = tmp_path / "not-there-yet"
        monkeypatch.setenv("LEM_DATA_DIR", str(target))
        tray.note("hello", root="/code/here")
        assert (target / "restart.log").is_file()


# ── version ─────────────────────────────────────────────────────────────────

class TestVersion:
    def test_missing_version_file_reports_dev(self, tmp_path):
        import web_app

        assert web_app.read_version(tmp_path) == "dev"

    def test_version_is_read_and_stripped(self, tmp_path):
        import web_app

        (tmp_path / "VERSION").write_text("v2.0.0\r\n", encoding="utf-8")
        assert web_app.read_version(tmp_path) == "v2.0.0"

    def test_unreadable_version_reports_dev_rather_than_raising(self, tmp_path):
        """A health check that 500s makes a good release look broken."""
        import web_app

        (tmp_path / "VERSION").write_bytes(b"\xff\xfe\x00nonsense")
        assert web_app.read_version(tmp_path) == "dev"


# ── /healthz ────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from labcore_gateway import FakeLabCoreGateway
    from web_app import create_app

    app = create_app(FakeLabCoreGateway())
    app.config["TESTING"] = True
    return app.test_client()


class TestHealthz:
    def test_returns_200_with_no_session(self, client):
        assert client.get("/healthz").status_code == 200

    def test_payload_shape(self, client):
        body = client.get("/healthz").get_json()
        assert body["status"] == "ok"
        assert set(body) >= {"status", "version", "labcore"}
        assert isinstance(body["version"], str) and body["version"]

    def test_labcore_is_one_of_the_three_known_values(self, client):
        assert client.get("/healthz").get_json()["labcore"] in {
            "reachable", "unreachable", "unknown"}

    def test_makes_no_labcore_call(self, client, monkeypatch):
        """The critical-path guarantee. This server exists to keep LabCore load
        independent of how many things are looking at it; a health check the
        updater hits every deploy must not become an op."""
        import labcore_gateway

        def explode(*a, **kw):  # pragma: no cover - must never run
            raise AssertionError("/healthz talked to LabCore")

        for name in ("read_sql", "write", "is_running"):
            if hasattr(labcore_gateway.FakeLabCoreGateway, name):
                monkeypatch.setattr(
                    labcore_gateway.FakeLabCoreGateway, name, explode, raising=False)

        assert client.get("/healthz").status_code == 200

    def test_reports_the_snapshot_reachability(self, client):
        """It must reflect what the snapshot already knows, not a fresh probe."""
        import web_app

        app = client.application
        snapshots = app.config.get("SNAPSHOTS")
        assert snapshots is not None, "create_app should still build a SnapshotService"

        snapshots._online = False
        assert client.get("/healthz").get_json()["labcore"] == "unreachable"

        snapshots._online = True
        assert client.get("/healthz").get_json()["labcore"] == "reachable"

    def test_is_not_behind_the_admin_password(self, client, monkeypatch):
        """LEM has an admin password; /healthz must not sit behind it."""
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"


# ── idleness ────────────────────────────────────────────────────────────────

class TestIdleReporting:
    """"Is anyone using LEM?" is not "has LEM had a request?".

    LEM is a wall display. The floor polls ``/api/machines`` and the blip
    endpoints every 2 seconds from every open browser, and the benches POST
    ``/api/live`` on every module poll. Counting those as activity would make
    LEM permanently busy and an idle-gated deploy could never fire — while
    counting nothing would deploy on top of someone mid-edit.

    So activity means a *person* doing something: anything that is not a
    background poll, a bench push, a health check or a static asset.
    """

    def test_healthz_reports_idle_seconds(self, client):
        body = client.get("/healthz").get_json()
        assert "idle_seconds" in body
        assert isinstance(body["idle_seconds"], (int, float))

    @pytest.mark.parametrize("path", [
        "/healthz",
        "/api/machines",
        # floor.html's load() fetches these two on the same 2s timer. They were
        # missing from the first version of the exclusion list, which pinned
        # LEM's idle time below one second against the real floor.
        "/api/me",
        "/api/map",
        "/api/events",
    ])
    def test_background_polling_is_not_activity(self, client, path):
        import web_app

        web_app._last_activity = 0.0
        client.get(path)
        assert web_app._last_activity == 0.0, (
            f"{path} counted as a person using LEM; the floor polls it every 2s"
        )

    def test_a_bench_push_is_not_activity(self, client):
        """A module pushing liveness is a machine, not a person."""
        import web_app

        web_app._last_activity = 0.0
        client.post("/api/live", json={"machine_uid": "x", "status": "GREEN"})
        assert web_app._last_activity == 0.0

    def test_a_real_page_view_is_activity(self, client):
        import web_app

        web_app._last_activity = 0.0
        client.get("/")
        assert web_app._last_activity > 0.0

    def test_a_write_is_activity(self, client):
        """Any mutation is a person, whatever the path."""
        import web_app

        web_app._last_activity = 0.0
        client.post("/api/machines/does-not-exist/position", json={"x": 1, "y": 2})
        assert web_app._last_activity > 0.0, (
            "a write did not count as activity; deploying on top of one would "
            "interrupt someone mid-edit"
        )


# ── health checks must not advertise themselves ─────────────────────────────

class TestNoPublish:
    """A release under test must not tell the benches where to push.

    ``start_live_channel`` writes this server's address and token into
    LabCore's ``lem_meta``, and every bench reads it from there. The updater
    health-checks a candidate release on a **scratch port** that closes seconds
    later — publishing that address would point the whole floor at a dead port
    until the next real boot republished it. The failover rule means the floor
    falls back to the LabCore record rather than going blank, so this degrades
    rather than breaks; it is still a production side effect caused by a health
    check, which is not a trade worth making for zero benefit.
    """

    def test_parser_accepts_no_publish(self):
        import web_server

        args = web_server.build_parser().parse_args(["--no-publish"])
        assert args.no_publish is True

    def test_no_publish_defaults_off(self):
        import web_server

        args = web_server.build_parser().parse_args([])
        assert args.no_publish is False

    def test_publishing_is_skipped_when_asked(self, monkeypatch):
        import live_presence

        called = []
        monkeypatch.setattr(live_presence, "publish_live_config",
                            lambda *a, **kw: called.append(a))

        import web_server

        assert web_server.publish_live(
            app=None, gateway=None, host="0.0.0.0", port=15557,
            no_publish=True) is None
        assert called == [], "a health check advertised itself to the benches"

    def test_publishing_happens_normally(self, monkeypatch):
        import live_presence
        import web_server

        called = []
        monkeypatch.setattr(web_server, "_start_live_channel",
                            lambda app, gw, host, port: called.append(port) or "url")

        got = web_server.publish_live(app=None, gateway=None, host="0.0.0.0",
                                      port=5557, no_publish=False)
        assert got == "url"
        assert called == [5557]
