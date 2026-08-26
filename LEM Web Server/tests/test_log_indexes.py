"""The snapshot scans `lem_machine_log` twice every twelve seconds, forever.

`lem_machine_log` is a table LEM creates. It has no primary key, no index, and
no retention rule, and it is where every parsed run, QC verdict, status change,
operator comment and PM tick lands. Two of the snapshot's arms read it on every
refresh — the `event` arm's `ORDER BY ts DESC LIMIT n` and the `activity` arm's
`GROUP BY machine_uid` — which at the 12s default is 14,400 full-table scans a
day, growing without bound.

Why that is a lab-wide problem and not a slow page. LabCore's SQLite file lives
on an SMB share and cannot move; WAL is unusable there, so the journal mode is
DELETE, so a reader blocks the writer's commit — which is why LabCore serialises
`read_sql` through its WRITE queue (`LabCore.py:13180`). Our slow read therefore
blocks every write in the building while it runs. LabCore then interrupts any
read past `read_watchdog_s` (8.0s), and the comment on that watchdog names "an
unindexed scan over the SMB share" as the exact hazard. That is us.

These tests do not assert that an index EXISTS. An index the planner declines to
use is worthless, and asserting its existence would not catch that — so they
seed a real SQLite database through the same gateway the app uses and read
`EXPLAIN QUERY PLAN` back for each of the three reads.
"""
from datetime import datetime, timedelta

import pytest

import snapshot_service as snap
from labcore_gateway import FakeLabCoreGateway
from snapshot_service import SnapshotService

NOW = datetime(2026, 8, 3, 12, 0, 0)

MACHINES = [f"m{i:02d}" for i in range(20)]
KINDS = ["run", "qc", "status_change", "comment", "pm"]

# Enough rows that a planner with anything better than a scan available will
# take it, and few enough that the suite stays fast.
SEEDED_ROWS = 4000

MAINTENANCE_READ = (
    "SELECT uid, machine_uid, name, kind, interval_days, last_done, "
    "note FROM lem_maintenance WHERE machine_uid = ? ORDER BY kind, name")


def arm(name: str) -> str:
    """One arm of the batched read, exactly as production sends it.

    Taken off `_ARMS` rather than restated, so an arm that changes shape changes
    shape here too. Each arm is also run ON ITS OWN in the fallback path, so a
    single arm is a real production query and not a fragment.
    """
    for got, sql in snap._ARMS:
        if got == name:
            return sql
    raise AssertionError(f"no {name!r} arm — the batched read was restructured")


@pytest.fixture
def gw():
    """A gateway carrying the schema the service declares, with a log in it."""
    gateway = FakeLabCoreGateway()
    SnapshotService(gateway).ensure_schema()
    rows = []
    for i in range(SEEDED_ROWS):
        # The kind cycles on a different stride from the machine, so every
        # machine ends up with every kind rather than exactly one of them.
        kind = KINDS[(i // len(MACHINES)) % len(KINDS)]
        rows.append((
            MACHINES[i % len(MACHINES)],
            (NOW - timedelta(seconds=7 * i)).isoformat(),
            kind, f"L-{i}", "Sulphur" if kind == "qc" else "", "1.0", "{}",
        ))
    for row in rows:
        gateway.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
                    list(row))
    for i in range(200):
        gateway.sql("INSERT INTO lem_maintenance VALUES (?,?,?,?,?,?,?)",
                    [f"u{i}", MACHINES[i % len(MACHINES)], f"task{i}",
                     "pm", 30, "", ""])
    # Deliberately no ANALYZE. Nothing in LEM or LabCore ever runs one, so the
    # production database has no sqlite_stat1 for these tables and the planner
    # is choosing on shape alone. Verified both ways at 100k rows: the plans
    # below are identical with and without statistics — but the test pins the
    # case production is actually in, so an index that only wins once somebody
    # has ANALYZEd cannot pass here.
    return gateway


def plan(gw, sql, args=None):
    res = gw.read_sql("EXPLAIN QUERY PLAN " + sql, args or [])
    assert not res.get("error"), res
    return [r["detail"] for r in res["rows"]]


class TestTheEventArm:
    """`ORDER BY ts DESC LIMIT 60` — the newest sixty entries, every 12s."""

    def test_it_never_reads_the_table_unaided(self, gw):
        """`SCAN lem_machine_log` on its own — no index named — is the plan
        this whole change exists to remove: read every row off the share.

        Note that the plan for the FIXED query still says SCAN, because the
        planner walks the index in order rather than seeking into it. That word
        is not the problem; reading the table with nothing to guide it is. So
        this asserts the absence of the bare form specifically, not the absence
        of the word.
        """
        steps = plan(gw, arm("event"))
        assert "SCAN lem_machine_log" not in steps, steps

    def test_it_reads_the_newest_rows_off_the_ts_index(self, gw):
        """This is the whole trick: with `(ts DESC)` the planner walks sixty
        index entries and stops — the LIMIT bounds the walk. Without it, it
        reads every row in the table and sorts the lot to throw all but sixty
        away, twice a minute, through the queue every write in the lab is
        waiting in."""
        steps = plan(gw, arm("event"))
        assert any("idx_lem_log_ts" in s for s in steps), steps

    def test_it_does_not_sort(self, gw):
        steps = plan(gw, arm("event"))
        assert not any("TEMP B-TREE" in s.upper() for s in steps), steps

    def test_it_still_returns_the_newest_rows_first(self, gw):
        res = gw.read_sql(arm("event"))
        got = [r["c2"] for r in res["rows"]]
        assert got == sorted(got, reverse=True)
        assert len(got) == snap.EVENT_LIMIT


class TestTheActivityArm:
    """`MAX(ts) GROUP BY machine_uid` — when each bench was last heard from."""

    def test_it_does_not_touch_the_table(self, gw):
        """A COVERING index scan is the win here and it is not the same thing
        as no scan at all. `MAX(ts)` per machine has to consider every entry
        whatever it does; what the index removes is (a) reading the seven-column
        row off the share for each one, and (b) the temp b-tree it had to build
        to group them, because the entries already arrive grouped by
        machine_uid. So the assertion is that it never reads the TABLE."""
        steps = plan(gw, arm("activity"))
        assert any("COVERING INDEX idx_lem_log_uid_kind_ts" in s
                   for s in steps), steps

    def test_it_does_not_build_a_temp_btree_to_group(self, gw):
        steps = plan(gw, arm("activity"))
        assert not any("TEMP B-TREE" in s.upper() for s in steps), steps

    def test_it_still_reports_the_newest_stamp_per_machine(self, gw):
        res = gw.read_sql(arm("activity"))
        got = {r["c1"]: r["c2"] for r in res["rows"]}
        assert set(got) == set(MACHINES)
        newest = gw.read_sql(
            "SELECT machine_uid, MAX(ts) AS ts FROM lem_machine_log "
            "GROUP BY machine_uid")
        assert got == {r["machine_uid"]: r["ts"] for r in newest["rows"]}


class TestTheMaintenanceRead:
    """`lem_maintenance` is keyed on `uid` and always read by `machine_uid`."""

    def test_it_searches_rather_than_scans(self, gw):
        steps = plan(gw, MAINTENANCE_READ, ["m03"])
        assert any("idx_lem_maint_machine" in s for s in steps), steps
        assert not any(s.startswith("SCAN lem_maintenance") for s in steps), steps

    def test_it_still_returns_that_machine_s_tasks(self, gw):
        res = gw.read_sql(MAINTENANCE_READ, ["m03"])
        assert res["rows"]
        assert {r["machine_uid"] for r in res["rows"]} == {"m03"}


class TestTheDeclarations:
    def test_every_index_is_declared(self, gw):
        made = gw.read_sql(
            "SELECT name FROM sqlite_master WHERE type = 'index'")
        names = {r["name"] for r in made["rows"]}
        for wanted in ("idx_lem_log_ts", "idx_lem_log_uid_kind_ts",
                       "idx_lem_maint_machine"):
            assert wanted in names, sorted(names)

    def test_they_are_idempotent(self):
        """Both programs declare the log's indexes, and whichever starts first
        creates them. A second, differently-spelled declaration would either
        fail or — worse — quietly be the one the whole lab lived with."""
        for ddl in snap.SCHEMA_DDL:
            if "CREATE INDEX" in ddl.upper():
                assert "IF NOT EXISTS" in ddl.upper(), ddl

    def test_each_index_is_declared_after_its_table(self):
        """`CREATE INDEX` on a table that does not exist is an error. The
        tables are in this same tuple, so the order inside it is the guarantee
        — on a fresh LabCore the index statement runs moments after the CREATE
        TABLE that makes it possible."""
        for ddl in snap.SCHEMA_DDL:
            if "CREATE INDEX" not in ddl.upper():
                continue
            table = ddl.upper().split(" ON ", 1)[1].split("(", 1)[0].strip()
            made_at = [i for i, d in enumerate(snap.SCHEMA_DDL)
                       if "CREATE TABLE" in d.upper()
                       and table in d.upper().split("(", 1)[0]]
            assert made_at, f"{table} is indexed but never created"
            assert made_at[0] < snap.SCHEMA_DDL.index(ddl), ddl


class TestStartUpStaysCheap:
    """Every declaration is a write into a queue that serialises at roughly 1.5
    ops/sec for the whole lab, and the tray restarts this server on every code
    edit. `ensure_schema` asks what already exists precisely so a warm database
    costs nothing; three index declarations that ignored that answer would put
    two seconds of the lab's write queue back on every boot."""

    def test_a_warm_database_declares_no_indexes(self, gw):
        seen = []
        real = gw.sql
        gw.sql = lambda s, a=None, **k: (seen.append(s), real(s, a, **k))[1]
        SnapshotService(gw).ensure_schema()
        assert [s for s in seen if "CREATE INDEX" in s.upper()] == [], seen

    def test_a_cold_database_still_gets_them(self):
        gateway = FakeLabCoreGateway()
        SnapshotService(gateway).ensure_schema()
        made = gateway.read_sql(
            "SELECT name FROM sqlite_master WHERE type = 'index'")
        names = {r["name"] for r in made["rows"]}
        assert "idx_lem_log_ts" in names, sorted(names)

    def test_a_dropped_index_is_created_again(self, gw):
        gw.sql("DROP INDEX idx_lem_log_ts")
        SnapshotService(gw).ensure_schema()
        made = gw.read_sql("SELECT name FROM sqlite_master WHERE type = 'index'")
        assert "idx_lem_log_ts" in {r["name"] for r in made["rows"]}

    def test_a_backend_that_cannot_answer_declares_everything(self):
        """None means "could not tell", and the only safe reading of that is to
        declare. An empty set would mean "nothing exists", which is the same
        answer a fresh database gives — getting those two confused the other way
        round skips creating an index that really is missing."""
        gateway = FakeLabCoreGateway()
        service = SnapshotService(gateway)
        service._existing_indexes = lambda: None
        service.ensure_schema()
        made = gateway.read_sql(
            "SELECT name FROM sqlite_master WHERE type = 'index'")
        assert "idx_lem_log_ts" in {r["name"] for r in made["rows"]}

    def test_asking_what_exists_never_raises(self):
        class Dead:
            def read_sql(self, *a, **k):
                raise RuntimeError("LabCore down")

            def sql(self, *a, **k):
                raise RuntimeError("LabCore down")

        SnapshotService(Dead()).ensure_schema()      # must not raise
