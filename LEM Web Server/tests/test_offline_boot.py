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

    def test_status_snapshot_says_it_could_not_read_rather_than_inventing_one(
            self, client):
        """CHANGED 2026-08-25, and not to make a fix pass.

        This asserted `200` + `labcore_online: False` over a snapshot built
        from a config read that never happened — `boxes: []` for a lab full of
        instruments. `DbConfigStore.load()` raises now, because the SAME object
        is what `/api/boxes` saves back and the save prunes each table to match
        it: an empty config read is an instruction to delete the QC library.

        The dashboard is better off, not worse. `refresh()` fetches config and
        status with `Promise.allSettled` and keeps the previous value when one
        rejects, so a 503 leaves the last good floor on screen where a 200 with
        `boxes: []` wiped it. "The master view survives LabCore being down" is
        held by `test_pages_still_serve` — the page comes up and says so.
        """
        r = client.get("/api/status")
        assert r.status_code in (502, 503)
        body = r.get_json()
        assert body["retry"] is True
        assert "boxes" not in body, (
            "an empty floor must not be served as if it were read")

    def test_qc_reads_return_empty_not_errors(self, client):
        assert client.get("/api/qc-samples").get_json()["samples"] == []
        assert client.get("/api/qc-specs").get_json()["specs"] == []

    def test_the_method_list_says_it_could_not_be_read(self, client):
        """USED TO ASSERT `{"tests": []}` HERE, and that was the degrade.

        Same correction as `test_status_snapshot_says_it_could_not_read_rather_
        than_inventing_one` above, for the same reason. These are LabCore's
        test METHODS, and LEM has no test names of its own (CLAUDE.md) — so an
        empty list is not a neutral fallback, it is the app saying "this lab
        has no methods", on the picker that decides what a QC standard may be
        checked against.

        The page is better off, not worse: `loadTests` keeps the list it
        already had rather than blanking it, which an empty 200 forced it to
        do.
        """
        res = client.get("/api/test-names")
        assert res.status_code in (502, 503)
        assert res.get_json()["retry"] is True

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
    """The store must pick the tables up when LabCore comes back — on the first
    WRITE, which is the first moment they are needed.

    CHANGED 2026-08-25: this used to drive the recovery through `load()`,
    because `load()` declared the schema. A read declares nothing now. The
    declaration was cheap on a lab whose tables exist and not cheap in the two
    cases that matter — tables genuinely missing, or `existing_tables` unable
    to answer — where a path that only wanted to READ pushed five CREATEs into
    the queue, and on a full queue five refusals. A read that finds no table
    reads as an empty config, which on a LabCore where LEM has never saved one
    is the truth.
    """

    def _config(self):
        from models import AppConfig
        return AppConfig(version=5, poll_minutes=5, map_locked=False,
                         samples=[], boxes=[])

    def test_a_read_during_the_outage_does_not_declare_anything(self):
        from db_config_store import DbConfigStore
        from labcore_result import LabCoreError
        import pytest as _pytest

        gw = FlakyGateway()
        store = DbConfigStore(gw)          # boots while LabCore is down
        assert gw.created == []
        with _pytest.raises(LabCoreError):
            store.load()                   # and says so, rather than "no config"
        assert gw.created == []

    def test_schema_is_created_once_labcore_is_back(self):
        from db_config_store import DbConfigStore
        gw = FlakyGateway()
        store = DbConfigStore(gw)          # boots while LabCore is down
        assert gw.created == []

        gw.alive = True
        ok, why = store.save(self._config())    # first WRITE after recovery
        assert ok, why
        assert any("CREATE TABLE" in s.upper() for s in gw.created)

    def test_schema_creation_is_not_repeated_forever(self):
        from db_config_store import DbConfigStore
        gw = FlakyGateway()
        gw.alive = True
        store = DbConfigStore(gw)
        store.save(self._config())
        creates = len([s for s in gw.created if "CREATE TABLE" in s.upper()])
        store.save(self._config())
        store.save(self._config())
        assert len([s for s in gw.created
                    if "CREATE TABLE" in s.upper()]) == creates


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
