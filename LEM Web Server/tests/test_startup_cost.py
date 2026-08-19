"""Starting up should be cheap, and nobody should wait for a cold cache.

The tray restarts this server on every code edit, so start-up cost is paid
constantly during development — and once per deploy in the lab, at the moment
somebody is standing in front of a screen waiting for it.

Two problems, both measured against the live system:

* **Fifteen `CREATE TABLE IF NOT EXISTS` writes**, ten from the snapshot's schema
  and five from the config store, into a queue that serialises at about 1.5
  ops/sec. Ten seconds of queue for tables that already exist. One read of
  `sqlite_master` answers it instead.

* **The first visitor to the checklist page waited 7.5 seconds.** The cache was
  cold and LabCore was busy. Nothing was wrong with the cache — it just had
  nobody to warm it, so the cost landed on a person instead of a thread.
"""
import threading
import time

import pytest

from labcore_gateway import FakeLabCoreGateway, existing_tables


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


class Counting(FakeLabCoreGateway):
    def __init__(self):
        super().__init__()
        self.reads, self.writes = [], []

    def read_sql(self, sql, args=None, **kw):
        self.reads.append(" ".join(sql.split()))
        return super().read_sql(sql, args, **kw)

    def sql(self, sql, args=None, **kw):
        self.writes.append(" ".join(sql.split()))
        return super().sql(sql, args, **kw)

    def is_running(self):
        return True


@pytest.fixture
def gw():
    return Counting()


def ddl(gw):
    return [w for w in gw.writes if "CREATE TABLE" in w.upper()]


# ── asking what exists ──────────────────────────────────────────────────────

class TestExistingTables:
    def test_it_lists_what_is_there(self, gw):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_thing (a TEXT)")
        assert "lem_thing" in existing_tables(gw)

    def test_an_unreadable_backend_gives_none_not_an_empty_set(self):
        """None means "could not tell" and callers must declare everything.
        An empty set would mean "nothing exists", which is the same answer for
        a fresh database — and getting that wrong the other way round would skip
        creating a table that really is missing."""
        class Dead:
            def read_sql(self, *a, **k):
                raise RuntimeError("LabCore down")

        assert existing_tables(Dead()) is None

    def test_an_error_dict_also_gives_none(self):
        class Refuses:
            def read_sql(self, *a, **k):
                return {"error": "LabCore is busy", "busy": True}

        assert existing_tables(Refuses()) is None


# ── the config store's schema ───────────────────────────────────────────────

class TestConfigStoreSchema:
    def test_a_warm_database_costs_no_writes(self, gw):
        from db_config_store import DbConfigStore
        DbConfigStore(gw)                    # first run creates them
        gw.writes.clear()
        DbConfigStore(gw)                    # a restart
        assert ddl(gw) == [], f"{len(ddl(gw))} needless writes on restart"

    def test_a_cold_database_still_gets_its_tables(self, gw):
        from db_config_store import DbConfigStore, _SCHEMA
        DbConfigStore(gw)
        names = existing_tables(gw)
        for stmt in _SCHEMA:
            if "CREATE TABLE" not in stmt.upper():
                continue
            table = stmt.split("IF NOT EXISTS", 1)[1].split("(", 1)[0].strip()
            assert table in names, table

    def test_a_missing_table_is_still_created(self, gw):
        from db_config_store import DbConfigStore
        DbConfigStore(gw)
        gw.sql("DROP TABLE lem_boxes")
        gw.writes.clear()
        DbConfigStore(gw)
        assert any("lem_boxes" in w for w in ddl(gw))

    def test_it_still_comes_up_when_labcore_is_unreachable(self):
        """LEM has to start and SAY LabCore is down, not refuse to start."""
        class Dead:
            def sql(self, *a, **k):
                raise RuntimeError("LabCore down")

            def read_sql(self, *a, **k):
                raise RuntimeError("LabCore down")

        from db_config_store import DbConfigStore
        DbConfigStore(Dead())            # must not raise


# ── nobody waits for a cold cache ───────────────────────────────────────────

class TestPrewarm:
    @pytest.fixture
    def app(self, gw):
        from web_app import create_app
        app = create_app(gw, authenticator=StubAuth(), secret="s")
        app.config["TESTING"] = True
        return app

    def test_the_server_exposes_a_warm_up(self, app):
        assert callable(app.config.get("WARM"))

    def test_after_warming_the_checklist_page_reads_nothing(self, gw, app):
        client = app.test_client()
        client.post("/api/login", json={"username": "k", "password": "good"})
        client.post("/api/checklists", json={
            "name": "Opening", "slot": "opening", "due_time": "09:30",
            "items": [{"text": "Check helium", "entry_type": "number"}]})
        app.config["WARM"]()
        gw.reads.clear()
        body = client.get("/api/checklists").get_json()
        assert gw.reads == [], "the first visitor still paid for the cold cache"
        assert body["checklists"][0]["name"] == "Opening"

    def test_warming_covers_the_archive_too(self, gw, app):
        client = app.test_client()
        app.config["WARM"]()
        gw.reads.clear()
        client.get("/api/checklists/history")
        assert gw.reads == []

    def test_warming_covers_the_floor(self, gw, app):
        client = app.test_client()
        app.config["WARM"]()
        gw.reads.clear()
        client.get("/api/machines")
        assert gw.reads == []

    def test_it_never_raises_however_broken_labcore_is(self):
        """It runs on a background thread at start-up. An exception there would
        be invisible, and a server that dies while warming a cache is worse than
        a slow first page."""
        class Dead:
            def sql(self, *a, **k):
                raise RuntimeError("LabCore down")

            def read_sql(self, *a, **k):
                raise RuntimeError("LabCore down")

            def is_running(self):
                return False

        from web_app import create_app
        app = create_app(Dead(), authenticator=StubAuth(), secret="s")
        app.config["TESTING"] = True
        app.config["WARM"]()             # must not raise

    def test_it_is_safe_to_call_twice(self, gw, app):
        app.config["WARM"]()
        app.config["WARM"]()
        client = app.test_client()
        assert client.get("/api/checklists").status_code == 200
