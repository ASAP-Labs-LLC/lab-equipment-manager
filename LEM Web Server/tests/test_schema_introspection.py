"""Asking LabCore which tables exist has to actually work.

`existing_tables()` ran `SELECT name FROM sqlite_master WHERE type='table'`. Against
production that **times out** — the client's read timeout is 8s and that query does
not come back inside it, even though `SELECT COUNT(*) FROM sqlite_master` answers
110 instantly. So the function returned None on every single boot, which meant:

  * the 15 `CREATE TABLE IF NOT EXISTS` writes were re-issued every start, into a
    queue that serialises at ~1.5 ops/sec — the exact cost the function exists to
    avoid, silently not avoided;
  * `_migrate()` fell through to its per-table PRAGMA check every time, so the
    column migrations only worked by luck rather than by design.

`pragma_table_list` answers the same question instantly (58 tables) and is available
from SQLite 3.37; production runs 3.49.
"""
import pytest

from labcore_gateway import FakeLabCoreGateway, existing_tables


@pytest.fixture
def gw():
    g = FakeLabCoreGateway()
    g.sql("CREATE TABLE IF NOT EXISTS lem_thing (a TEXT)")
    g.sql("CREATE TABLE IF NOT EXISTS lem_other (b TEXT)")
    return g


class TestItUsesAQueryThatReturns:
    def test_it_does_not_query_sqlite_master(self, gw):
        """The specific query that times out in production."""
        seen = []
        real = gw.read_sql
        gw.read_sql = lambda s, a=None, **k: (seen.append(s), real(s, a, **k))[1]
        existing_tables(gw)
        assert seen, "asked nothing at all"
        assert not any("sqlite_master" in s for s in seen), seen

    def test_it_lists_the_tables(self, gw):
        got = existing_tables(gw)
        assert got is not None
        assert "lem_thing" in got and "lem_other" in got

    def test_it_asks_once(self, gw):
        """One op — this runs on the boot path."""
        seen = []
        real = gw.read_sql
        gw.read_sql = lambda s, a=None, **k: (seen.append(s), real(s, a, **k))[1]
        existing_tables(gw)
        assert len(seen) == 1, seen


class TestFailureIsDistinguishable:
    def test_an_unreachable_backend_gives_none(self):
        class Dead:
            def read_sql(self, *a, **k):
                raise RuntimeError("LabCore down")

        assert existing_tables(Dead()) is None

    def test_a_timeout_gives_none_not_an_empty_set(self):
        """None means "could not tell, declare everything". An empty set would mean
        "nothing exists" — and acting on that wrongly is how a missing table ends up
        failing the whole batched read."""
        class Slow:
            def read_sql(self, *a, **k):
                return {"error": "HTTPSConnectionPool: read timed out"}

        assert existing_tables(Slow()) is None

    def test_an_older_sqlite_without_pragma_table_list_still_answers(self):
        """pragma_table_list needs SQLite 3.37. If it is missing, fall back rather
        than report "no tables" and re-create everything forever."""
        calls = []

        class Old:
            def read_sql(self, sql, args=None, **kw):
                calls.append(sql)
                if "pragma_table_list" in sql:
                    return {"error": "no such table: pragma_table_list"}
                return {"rows": [{"name": "lem_thing"}]}

        got = existing_tables(Old())
        assert got == {"lem_thing"}
        assert len(calls) == 2, calls

    def test_a_genuinely_empty_database_reports_an_empty_set(self):
        """Distinct from None: a fresh LabCore really has no lem_ tables yet."""
        class Fresh:
            def read_sql(self, *a, **k):
                return {"rows": []}

        assert existing_tables(Fresh()) == set()


class TestTheBootPathUsesIt:
    def test_the_schema_bootstrap_skips_existing_tables(self, gw):
        """The whole point: a warm database costs no writes."""
        from snapshot_service import SCHEMA_DDL, SnapshotService
        for ddl in SCHEMA_DDL:
            gw.sql(ddl)
        writes = []
        real = gw.sql
        gw.sql = lambda s, a=None, **k: (writes.append(s), real(s, a, **k))[1]
        SnapshotService(gw).ensure_schema()
        assert [w for w in writes if "CREATE TABLE" in w.upper()] == []

    def test_a_missing_table_is_still_created(self, gw):
        from snapshot_service import SCHEMA_DDL, SnapshotService
        for ddl in SCHEMA_DDL:
            if "lem_machine_layout" not in ddl:
                gw.sql(ddl)
        SnapshotService(gw).ensure_schema()
        assert "lem_machine_layout" in existing_tables(gw)
