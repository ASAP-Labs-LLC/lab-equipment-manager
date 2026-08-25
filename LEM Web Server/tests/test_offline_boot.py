"""The master view must survive LabCore being down.

A monitoring dashboard that refuses to start when its backend is
unreachable is useless exactly when you need it. LEM should come up,
serve its pages, and say plainly that LabCore is offline — then pick the
data back up when LabCore returns.
"""
import pytest


class DeadGateway:
    """Every call fails, the way a 502 from LabCore's proxy looks."""

    base_url = "https://labcore.example"

    def is_running(self):
        return False

    # **kw, because that is the real gateway contract: both
    # HttpLabCoreGateway and FakeLabCoreGateway take a `timeout` (the
    # test-names fallback scans every result row and needs a generous one).
    def sql(self, sql, args=None, source=None, **kw):
        return {"error": "Expecting value: line 1 column 1 (char 0)"}

    def write(self, operation, params, source=None, **kw):
        return {"error": "unreachable"}

    def read_sql(self, sql, args=None, source=None, **kw):
        return {"error": "unreachable"}

    def get_samples(self, **kw):
        return None

    def get_test_names(self):
        return None


class StubAuth:
    def login(self, u, p):
        return (None, "", "LabCore is not connected.")

    def logout(self, t):
        pass


@pytest.fixture
def client():
    from web_app import create_app
    app = create_app(DeadGateway(), authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client()


class TestBootsWithoutLabCore:
    def test_app_builds_at_all(self, client):
        assert client is not None          # create_app must not raise

    def test_pages_still_serve(self, client):
        for path in ("/", "/floor"):
            assert client.get(path).status_code == 200, path

    def test_machines_endpoint_reports_offline_rather_than_500(self, client):
        r = client.get("/api/machines")
        assert r.status_code == 200
        body = r.get_json()
        assert body["labcore_online"] is False
        assert body["machines"] == []

    def test_status_snapshot_degrades_gracefully(self, client):
        r = client.get("/api/status")
        assert r.status_code == 200
        assert r.get_json()["labcore_online"] is False

    def test_qc_reads_return_empty_not_errors(self, client):
        assert client.get("/api/qc-samples").get_json()["samples"] == []
        assert client.get("/api/qc-specs").get_json()["specs"] == []
        assert client.get("/api/test-names").get_json()["tests"] == []

    def test_map_lock_defaults_to_locked_when_unknown(self, client):
        """Can't read the setting → don't invite edits that will fail.

        This test asserted `False` while its own name and comment argued for
        `True`, and it could: the store swallowed the failure and answered
        "unlocked" as if it had read it. Now the store raises, so the route has
        to CHOOSE — and unlocked is the wrong choice. It puts drag handles on
        every floor screen, and every drag would be refused by the same LabCore
        that just failed to answer this, so the operator rearranges the lab and
        keeps none of it.
        """
        r = client.get("/api/map")
        # Still 200: this is polled every 2s by every wall display, and the
        # answer given is usable. `known: false` is what says it is a fallback.
        assert r.status_code == 200
        body = r.get_json()
        assert body["locked"] is True
        assert body["known"] is False
        assert body["error"]

    def test_login_says_labcore_is_down(self, client):
        r = client.post("/api/login", json={"username": "k", "password": "p"})
        assert r.status_code == 401
        assert "LabCore" in r.get_json()["error"]


class FlakyGateway(DeadGateway):
    """Down at boot, back later — the ordinary case after a LabCore restart."""

    def __init__(self):
        self.alive = False
        self.created = []
        from labcore_gateway import FakeLabCoreGateway
        self._real = FakeLabCoreGateway()

    def is_running(self):
        return self.alive

    def sql(self, sql, args=None, source=None):
        if not self.alive:
            return {"error": "unreachable"}
        self.created.append(sql)
        return self._real.sql(sql, args)

    def read_sql(self, sql, args=None, source=None):
        if not self.alive:
            return {"error": "unreachable"}
        return self._real.read_sql(sql, args)

    def write(self, operation, params, source=None):
        if not self.alive:
            return {"error": "unreachable"}
        return self._real.write(operation, params)


class TestSelfHealsWhenLabCoreReturns:
    def test_schema_is_created_once_labcore_is_back(self):
        from db_config_store import DbConfigStore
        gw = FlakyGateway()
        store = DbConfigStore(gw)          # boots while LabCore is down
        assert gw.created == []

        gw.alive = True
        store.load()                       # first use after recovery
        assert any("CREATE TABLE" in s.upper() for s in gw.created)

    def test_schema_creation_is_not_repeated_forever(self):
        from db_config_store import DbConfigStore
        gw = FlakyGateway()
        gw.alive = True
        store = DbConfigStore(gw)
        first = len(gw.created)
        store.load(); store.load()
        assert len(gw.created) == first    # created once, then left alone


# ── The shell: mode selector at the root, floor at /floor ───────────────────

class TestSingleUi:
    @pytest.fixture
    def live(self):
        from labcore_gateway import FakeLabCoreGateway
        from web_app import create_app
        app = create_app(FakeLabCoreGateway(), authenticator=StubAuth(),
                         secret="s")
        app.config["TESTING"] = True
        return app.test_client()

    def test_root_serves_the_mode_selector(self, live):
        """The floor moved to /floor; the root chooses Map or Checklists."""
        body = live.get("/").get_data(as_text=True)
        assert "LAB FLOOR" not in body.upper()
        assert 'href="/floor"' in body

    def test_floor_path_still_works(self, live):
        assert live.get("/floor").status_code == 200

    def test_retired_pages_land_on_the_floor(self, live):
        for old in ("/stations", "/dashboard"):
            r = live.get(old)
            assert r.status_code in (301, 302), old
            landed = live.get(r.headers["Location"], follow_redirects=True)
            assert "LAB FLOOR" in landed.get_data(as_text=True).upper(), old

    def test_every_feature_endpoint_is_reachable(self, live):
        """Nothing the retired pages offered got lost with them."""
        for path in ("/api/machines", "/api/qc-samples", "/api/qc-specs",
                     "/api/test-names", "/api/map", "/api/events", "/api/me"):
            assert live.get(path).status_code == 200, path
