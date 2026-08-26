"""LabCore load must not scale with the number of people looking at the floor.

Measured: one refresh of the pages a lab leaves open cost **17 LabCore ops**, and
a single wall display polling every 30s meant ~34 ops/min. Three screens and the
shared write queue — the one LabStation and LabEntry also use — is deep enough to
start rejecting work. The web server was a bad neighbour.

So requests stop talking to LabCore at all. One background thread refreshes a
snapshot on a fixed interval and every read is served from memory:

  * LabCore load is **constant** — the same whether one screen is open or ten
  * the request path never blocks on a 1.35s round-trip
  * the machine tables are fetched in ONE op via UNION ALL, with a per-table
    fallback if that is ever rejected
"""
import threading
import time

import pytest

from labcore_gateway import FakeLabCoreGateway
from snapshot_service import SnapshotService, batched_machine_sql, split_batched


class Counting(FakeLabCoreGateway):
    def __init__(self, latency=0.0, fail_batched=False):
        super().__init__()
        self.reads = []
        self.latency = latency
        self.fail_batched = fail_batched
        self.lock = threading.Lock()

    def read_sql(self, sql, args=None, **kw):
        with self.lock:
            self.reads.append(sql)
        if self.fail_batched and "UNION ALL" in sql:
            return {"error": "not supported here"}
        if self.latency:
            time.sleep(self.latency)
        return super().read_sql(sql, args, **kw)

    def is_running(self):
        return True


def seed_every_table(gw):
    """One row in every table the snapshot reads, so a path that silently drops
    a table cannot pass as equivalent."""
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_heartbeat ("
           "machine_uid TEXT PRIMARY KEY, last_poll TEXT, watching TEXT)")
    gw.sql("INSERT INTO lem_machine_heartbeat VALUES ('m0','2026-08-03T09:00:00','csv')")
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_substatus ("
           "machine_uid TEXT PRIMARY KEY, qc TEXT, pm TEXT, calibration TEXT, "
           "updated_at TEXT)")
    gw.sql("INSERT INTO lem_machine_substatus VALUES ('m0','GREEN','GREEN','RED','x')")
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_layout ("
           "machine_uid TEXT PRIMARY KEY, pos_x REAL, pos_y REAL)")
    gw.sql("INSERT INTO lem_machine_layout VALUES ('m0', 4.1, 2.05)")
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_targets ("
           "machine_uid TEXT NOT NULL, sample_name TEXT NOT NULL, "
           "test_name TEXT NOT NULL, PRIMARY KEY (machine_uid, sample_name, test_name))")
    gw.sql("INSERT INTO lem_machine_targets VALUES ('m0','Cloud CRM','Cloud Point')")
    gw.sql("CREATE TABLE IF NOT EXISTS lem_qc_specs (machine_uid TEXT NOT NULL, "
           "test_name TEXT NOT NULL, sample_id TEXT, expected REAL, std_dev REAL, "
           "k REAL, units TEXT, PRIMARY KEY (machine_uid, test_name))")
    gw.sql("INSERT INTO lem_qc_specs VALUES ('m0','Cloud Point','CP',-7.4,2.8,1.0,'C')")
    gw.sql("CREATE TABLE IF NOT EXISTS lem_maintenance (uid TEXT PRIMARY KEY, "
           "machine_uid TEXT NOT NULL, name TEXT NOT NULL, kind TEXT, "
           "interval_days INTEGER, last_done TEXT, note TEXT)")
    gw.sql("INSERT INTO lem_maintenance VALUES ('t1','m0','Annual cal',"
           "'calibration',365,'2020-01-01','')")
    gw.sql("CREATE TABLE IF NOT EXISTS lem_lab_schedule (id INTEGER PRIMARY KEY, "
           "working_days TEXT, opens TEXT, closes TEXT)")
    gw.sql("INSERT INTO lem_lab_schedule VALUES (1,'[0,1,2,3,4]','07:00','18:00')")
    gw.sql("CREATE TABLE IF NOT EXISTS lem_lab_holidays (day TEXT PRIMARY KEY, name TEXT)")
    gw.sql("INSERT INTO lem_lab_holidays VALUES ('2026-12-25','Christmas')")
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, ts TEXT, "
           "kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, detail TEXT)")
    gw.sql("INSERT INTO lem_machine_log VALUES ('m0','2026-08-03T09:30:00','run','1','','','{}')")


def seed(gw, n=3):
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
           "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
           "reason TEXT, updated_at TEXT)")
    for i in range(n):
        gw.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
               [f"m{i}", f"Machine {i}", "GREEN", "ok", "2026-08-03T09:00:00"])


@pytest.fixture
def gw():
    g = Counting()
    seed(g)
    return g


def builder_for():
    """The real builder, wired the way web_app wires it."""
    from datetime import datetime
    from snapshot_service import build_machines
    from web_app import STATUS_COLORS, _beat_is_fresh
    return lambda tables: build_machines(tables, datetime.now(),
                                        _beat_is_fresh, STATUS_COLORS)


@pytest.fixture
def service(gw):
    svc = SnapshotService(gw, interval=0.2, builder=builder_for())
    yield svc
    svc.stop()


# ── the load is constant ────────────────────────────────────────────────────

class TestConstantLoad:
    def test_reading_the_snapshot_costs_no_labcore_ops(self, gw, service):
        service.refresh()
        before = len(gw.reads)
        for _ in range(50):
            service.get()
        assert len(gw.reads) == before, "serving a request hit LabCore"

    def test_ten_readers_cost_the_same_as_one(self, gw, service):
        service.refresh()
        before = len(gw.reads)
        threads = [threading.Thread(target=service.get) for _ in range(10)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert len(gw.reads) == before

    def test_the_background_thread_refreshes_on_its_own(self, gw):
        svc = SnapshotService(gw, interval=0.15, builder=builder_for())
        try:
            svc.start()
            time.sleep(0.7)
            assert svc.refreshes >= 2, "the poller never ran"
        finally:
            svc.stop()

    def test_stopping_it_stops_the_traffic(self, gw):
        svc = SnapshotService(gw, interval=0.1, builder=builder_for())
        svc.start()
        time.sleep(0.3)
        svc.stop()
        settled = len(gw.reads)
        time.sleep(0.4)
        assert len(gw.reads) == settled


# ── it must never make a request wait ───────────────────────────────────────

class TestNeverBlocks:
    def test_a_peek_never_waits(self, gw):
        """`build_if_missing=False` is the non-blocking contract: it reports
        "not ready" rather than paying for a build."""
        gw.latency = 0.4
        svc = SnapshotService(gw, interval=5, builder=builder_for())
        try:
            start = time.time()
            snap = svc.get(build_if_missing=False)
            assert time.time() - start < 0.15, "a peek waited for a refresh"
            assert snap["ready"] is False
            assert snap["machines"] == []
        finally:
            svc.stop()

    def test_the_very_first_caller_builds_rather_than_showing_nothing(self, gw):
        """Exactly one request pays for the first build; an empty floor at boot
        would be worse than a slow one."""
        svc = SnapshotService(gw, interval=5, builder=builder_for())
        try:
            assert svc.get()["ready"] is True
            assert len(svc.get()["machines"]) == 3
            assert svc.refreshes == 1, "built more than once"
        finally:
            svc.stop()

    def test_a_burst_at_boot_shares_one_build(self, gw):
        gw.latency = 0.15
        svc = SnapshotService(gw, interval=5, builder=builder_for())
        try:
            threads = [threading.Thread(target=svc.get) for _ in range(8)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            assert svc.refreshes == 1, f"{svc.refreshes} builds for one burst"
        finally:
            svc.stop()

    def test_it_says_when_the_data_is_from(self, gw, service):
        service.refresh()
        snap = service.get()
        assert snap["ready"] is True
        assert snap["age_seconds"] >= 0

    def test_a_refresh_failure_keeps_the_last_good_answer(self, gw, service):
        service.refresh()
        good = service.get()["machines"]
        assert good

        def boom(*a, **k):
            raise RuntimeError("LabCore down")

        gw.read_sql = boom
        service.refresh()
        snap = service.get()
        assert snap["machines"] == good, "lost the last good snapshot"
        assert snap["stale"] is True

    def test_a_refresh_that_raises_does_not_kill_the_poller(self, gw):
        svc = SnapshotService(gw, interval=0.1, builder=builder_for())

        calls = {"n": 0}
        real = gw.read_sql

        def flaky(sql, args=None, **kw):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("nope")
            return real(sql, args, **kw)

        gw.read_sql = flaky
        try:
            svc.start()
            time.sleep(0.8)
            assert svc.refreshes >= 2, "poller died on the first error"
        finally:
            svc.stop()


# ── the machine tables come back in ONE op ──────────────────────────────────

class TestBatchedRead:
    def test_the_batched_query_covers_every_machine_table(self):
        sql = batched_machine_sql()
        for table in ("lem_machine_status", "lem_machine_heartbeat",
                      "lem_machine_substatus", "lem_machine_layout",
                      "lem_machine_targets"):
            assert table in sql, table
        assert sql.count("UNION ALL") >= 4

    def test_every_arm_selects_the_same_number_of_columns(self, gw):
        """A mismatched arm makes the whole UNION fail, taking every table with
        it — so this is worth pinning rather than discovering at runtime."""
        # Run each arm and compare the shapes it actually returns. Counting
        # commas in the text was doing the same job until an arm needed
        # ORDER BY/LIMIT — legal inside a UNION only when wrapped in a subquery,
        # at which point `SELECT * FROM (…)` parses as one column and the check
        # failed on a statement LabCore accepts perfectly well.
        from snapshot_service import _ARMS, SnapshotService
        SnapshotService(gw).ensure_schema()
        gw.sql("INSERT INTO lem_machine_log VALUES "
               "('m1','2026-08-03T09:00:00','run','1','CP','-7.4','{}')")
        gw.sql("INSERT INTO lem_machine_status VALUES "
               "('m1','OptiMPP 1','GREEN','ok','2026-08-03T09:00:00')")
        gw.sql("INSERT INTO lem_machine_layout VALUES ('m1', 1.0, 2.0)")
        # The three arms the benches read their own configuration from. An arm
        # with no rows only proves the SQL parses; the shape check below can
        # only see arms that actually returned something, so each one is seeded.
        gw.sql("INSERT INTO lem_machine_control VALUES "
               "('m1','SERVICE','bulb','2026-08-03T09:00:00')")
        gw.sql("INSERT INTO lem_correction_factors VALUES "
               "('m1','Cloud Point',-0.4,'C','2026-08-03T09:00:00','ryan')")
        gw.sql("INSERT INTO lem_qc_samples VALUES ('CRM','L-1','[]')")
        shapes = {}
        for name, arm in _ARMS:
            res = gw.read_sql(arm)
            assert not res.get("error"), f"{name}: {res.get('error')}"
            for row in res.get("rows") or []:
                shapes.setdefault(tuple(sorted(row)), []).append(name)
        assert len(shapes) == 1, f"arms disagree on shape: {shapes}"
        assert set(list(shapes)[0]) == {"src", "c1", "c2", "c3", "c4", "c5",
                                        "c6", "c7", "c8", "c9"}
        # Named explicitly: the check above is a loop over whatever `_ARMS`
        # happens to hold, so an arm added without a row of its own would be
        # "covered" by never being exercised at all.
        covered = set(list(shapes.values())[0])
        assert {"control", "corr", "qcsample"} <= covered, covered

    def test_the_bench_configuration_tables_are_in_the_same_one_op(self):
        """Serving benches from memory is only free while it stays ONE op.

        Three more tables read as three more statements would trade a per-bench
        cost for a per-cycle one — smaller, but the wrong direction on a queue
        that runs at ~1.5 ops/sec.
        """
        sql = batched_machine_sql()
        for table in ("lem_machine_control", "lem_correction_factors",
                      "lem_qc_samples"):
            assert table in sql, table

    def test_the_batched_statement_is_accepted_whole(self, gw):
        """Each arm being fine on its own does not prove the UNION is."""
        from snapshot_service import SnapshotService
        SnapshotService(gw).ensure_schema()
        res = gw.read_sql(batched_machine_sql())
        assert not res.get("error"), res.get("error")

    def test_rows_are_split_back_by_source(self):
        rows = [{"src": "status", "c1": "m1", "c2": "OptiMPP 1"},
                {"src": "beat", "c1": "m1", "c2": "2026-08-03T09:00:00"},
                {"src": "status", "c1": "m2", "c2": "Multitek"}]
        out = split_batched(rows)
        assert len(out["status"]) == 2 and len(out["beat"]) == 1

    def test_an_unknown_source_is_ignored_not_fatal(self):
        out = split_batched([{"src": "who?", "c1": "x"}, None, "junk"])
        assert out.get("who?") is None or out["who?"]

    def test_one_op_is_used_when_batching_works(self, gw, service):
        # Identified by the batched read's own first arm, not by "UNION ALL".
        # That substring used to be unique to it; `_existing_indexes` now also
        # asks one question about two tables in one op, and matching on UNION
        # ALL counted a schema read as a second machine read. The arm marker
        # says what this test actually means.
        gw.reads.clear()
        service.refresh()
        batched = [r for r in gw.reads if "'status' AS src" in r]
        assert len(batched) == 1, gw.reads

    def test_it_falls_back_per_table_if_batching_is_rejected(self, gw):
        """Older LabCore, or a table that doesn't exist yet, must not blank the
        floor — it should just cost more ops."""
        gw.fail_batched = True
        svc = SnapshotService(gw, interval=5, builder=builder_for())
        try:
            svc.refresh()
            snap = svc.get()
            assert snap["ready"] is True
            assert len(snap["machines"]) == 3
        finally:
            svc.stop()

    def test_batched_and_per_table_agree(self, gw):
        """The optimisation is only safe if it produces the same answer.

        Seeds EVERY table on purpose. The first version of this test seeded only
        lem_machine_status, so both paths returned empty layouts and "agreed" —
        while the fallback was in fact dropping every non-status table because
        its arms had no column aliases.
        """
        seed_every_table(gw)
        fast = SnapshotService(gw, interval=5, builder=builder_for())
        fast.refresh()
        a = fast.get()["machines"]
        fast.stop()

        gw.fail_batched = True
        slow = SnapshotService(gw, interval=5, builder=builder_for())
        slow.refresh()
        b = slow.get()["machines"]
        slow.stop()
        assert a == b, "batched read disagrees with the per-table read"
        # and it is not agreeing on emptiness
        m0 = [m for m in a if m["machine_uid"] == "m0"][0]
        assert m0["pos"] == [4.1, 2.05]
        assert m0["qc_specs"] and m0["qc_targets"] and m0["maintenance"]
        assert m0["sub_statuses"]["calibration"] == "RED"


# ── a write shows up immediately ────────────────────────────────────────────

class TestWritesAreVisible:
    def test_refresh_soon_is_asynchronous_when_the_poller_runs(self, gw, service):
        gw.latency = 0.3
        service.start()
        time.sleep(0.05)
        start = time.time()
        service.refresh_soon()
        assert time.time() - start < 0.1, "refresh_soon blocked the caller"

    def test_with_no_poller_it_refreshes_inline_rather_than_never(self, gw,
                                                                 service):
        """A signal with nothing listening would mean the write silently never
        appears — worse than a slow response."""
        service.refresh()
        gw.sql("INSERT INTO lem_machine_status VALUES "
               "('m8','Inline','GREEN','ok','2026-08-03T10:00:00')")
        service.refresh_soon()                 # no poller started
        assert "Inline" in [m["title"] for m in service.get()["machines"]]

    def test_it_does_pick_the_change_up(self, gw, service):
        service.refresh()
        gw.sql("INSERT INTO lem_machine_status VALUES "
               "('m9','Newcomer','GREEN','ok','2026-08-03T10:00:00')")
        service.refresh()
        titles = [m["title"] for m in service.get()["machines"]]
        assert "Newcomer" in titles


class TestSchemaBootstrapIsCheap:
    """Declaring the ten tables cost ten WRITES on every start.

    `CREATE TABLE IF NOT EXISTS` is harmless but not free: it goes through the
    same serialised write queue as everything else in the lab, which lands about
    1.5 ops/sec — so seven seconds of queue, on every restart, for tables that
    almost always already exist. The tray restarts this server on every code edit.

    One read tells us which are genuinely missing.
    """

    def existing(self, gw):
        return {r["name"] for r in
                (gw.read_sql("SELECT name FROM sqlite_master WHERE type='table'")
                 .get("rows") or [])}

    def test_a_warm_database_is_declared_with_no_writes_at_all(self, gw):
        from snapshot_service import SCHEMA_DDL, SnapshotService
        for ddl in SCHEMA_DDL:                    # everything already there
            gw.sql(ddl)
        svc = SnapshotService(gw)
        writes = []
        real = gw.sql
        gw.sql = lambda s, a=None, **k: (writes.append(s), real(s, a, **k))[1]
        svc.ensure_schema()
        assert [w for w in writes if "CREATE TABLE" in w.upper()] == [], \
            f"{len(writes)} needless writes into the shared queue"

    def test_only_the_missing_tables_are_created(self, gw):
        from snapshot_service import SCHEMA_DDL, SnapshotService
        for ddl in SCHEMA_DDL:
            if "lem_machine_layout" not in ddl:
                gw.sql(ddl)
        svc = SnapshotService(gw)
        writes = []
        real = gw.sql
        gw.sql = lambda s, a=None, **k: (writes.append(s), real(s, a, **k))[1]
        svc.ensure_schema()
        created = [w for w in writes if "CREATE TABLE" in w.upper()]
        assert len(created) == 1
        assert "lem_machine_layout" in created[0]
        assert "lem_machine_layout" in self.existing(gw)

    def test_a_cold_database_still_gets_every_table(self, gw):
        from snapshot_service import SCHEMA_DDL, SnapshotService
        SnapshotService(gw).ensure_schema()
        names = self.existing(gw)
        for ddl in SCHEMA_DDL:
            # SCHEMA_DDL now also carries index declarations, whose name after
            # `IF NOT EXISTS` is the index's, not a table's. They are covered by
            # tests/test_log_indexes.py, which checks the thing that actually
            # matters about an index — that the planner USES it.
            if "CREATE INDEX" in ddl.upper():
                continue
            table = ddl.split("IF NOT EXISTS")[1].split("(")[0].strip()
            assert table in names, table

    def test_it_still_works_if_the_table_list_cannot_be_read(self, gw):
        """No listing means we cannot tell — so declare them all rather than
        skip and have the batched read fail on a missing table."""
        from snapshot_service import SnapshotService
        svc = SnapshotService(gw)

        def no_listing(sql, args=None, **kw):
            if "sqlite_master" in sql:
                return {"error": "not permitted"}
            return {"rows": []}

        gw.read_sql = no_listing
        writes = []
        real = gw.sql
        gw.sql = lambda s, a=None, **k: (writes.append(s), real(s, a, **k))[1]
        svc.ensure_schema()
        from snapshot_service import SCHEMA_DDL
        assert len([w for w in writes if "CREATE TABLE" in w.upper()]) \
            == len([d for d in SCHEMA_DDL if "CREATE TABLE" in d.upper()])
        # And the same rule for the indexes SCHEMA_DDL now also carries: a
        # listing we could not read means "cannot tell", and the only safe
        # reading of that is to declare. Skipping here would leave the log
        # unindexed — the scan that blocks every write in the lab — on exactly
        # the LabCore that was too busy to answer the question.
        assert len([w for w in writes if "CREATE INDEX" in w.upper()]) \
            == len([d for d in SCHEMA_DDL if "CREATE INDEX" in d.upper()])


class TestSchemaMigrations:
    """`CREATE TABLE IF NOT EXISTS` cannot add a column to a table that exists.

    Caught in production on 2026-08-03, by me, minutes after adding `correction`
    to `lem_machine_specs`: the table had already been created without it, so the
    new arm referenced a column LabCore did not have — and because the arms share
    one statement, **one missing column failed the entire batched read** and
    dropped the whole floor onto the fallback path with an empty spec list.

    So new columns need ALTER TABLE, applied once, only when actually missing —
    a failed ALTER every start would be a wasted write into a queue that
    serialises at ~1.5 ops/sec.
    """

    def columns(self, gw, table):
        res = gw.read_sql(f"SELECT name FROM pragma_table_info('{table}')")
        return {r["name"] for r in (res.get("rows") or [])}

    def test_an_older_table_gains_the_new_column(self, gw):
        from snapshot_service import SnapshotService
        gw.sql("CREATE TABLE lem_machine_specs ("
               "machine_uid TEXT NOT NULL, test_name TEXT NOT NULL, "
               "sample_id TEXT, expected REAL, std_dev REAL, k REAL, "
               "units TEXT, low REAL, high REAL, last_qc_at TEXT, "
               "last_qc_value REAL, last_qc_in_spec INTEGER, updated_at TEXT, "
               "PRIMARY KEY (machine_uid, test_name))")
        assert "correction" not in self.columns(gw, "lem_machine_specs")
        SnapshotService(gw).ensure_schema()
        assert "correction" in self.columns(gw, "lem_machine_specs")

    def test_the_batched_read_then_works(self, gw):
        """The symptom that mattered: the whole statement, not just one arm."""
        from snapshot_service import SnapshotService, batched_machine_sql
        gw.sql("CREATE TABLE lem_machine_specs ("
               "machine_uid TEXT NOT NULL, test_name TEXT NOT NULL, "
               "sample_id TEXT, expected REAL, std_dev REAL, k REAL, "
               "units TEXT, low REAL, high REAL, last_qc_at TEXT, "
               "last_qc_value REAL, last_qc_in_spec INTEGER, updated_at TEXT, "
               "PRIMARY KEY (machine_uid, test_name))")
        SnapshotService(gw).ensure_schema()
        res = gw.read_sql(batched_machine_sql())
        assert not res.get("error"), res.get("error")

    def test_an_up_to_date_table_costs_no_alter(self, gw):
        from snapshot_service import SnapshotService
        SnapshotService(gw).ensure_schema()          # creates it complete
        writes = []
        real = gw.sql
        gw.sql = lambda s, a=None, **k: (writes.append(s), real(s, a, **k))[1]
        SnapshotService(gw).ensure_schema()
        assert [w for w in writes if "ALTER" in w.upper()] == [], \
            "a pointless ALTER on every start"

    def test_a_failed_migration_does_not_stop_start_up(self):
        """LabCore unreachable at boot must not prevent the server coming up."""
        from snapshot_service import SnapshotService

        class Dead:
            def sql(self, *a, **k):
                raise RuntimeError("LabCore down")

            def read_sql(self, *a, **k):
                raise RuntimeError("LabCore down")

        SnapshotService(Dead()).ensure_schema()      # must not raise
