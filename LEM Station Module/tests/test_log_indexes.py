"""`lem_machine_log` is the one table we create with no key and no index.

Why this is not a local performance problem. LabCore's database lives on an SMB
share and cannot move, so `read_sql` serialises through the WRITE queue
(`LabCore.py:13180`) — a slow read does not inconvenience the reader, it blocks
every write in the lab for as long as it runs. LabCore then INTERRUPTS any read
that outruns `read_watchdog_s` (8.0s by default), and its own comment names "an
unindexed scan over the SMB share" as the hazard it is guarding against.

That scan is ours. `lem_machine_log` has no primary key, is never pruned, and is
where every parsed run, QC verdict, status change, operator comment and PM tick
lands. This bench reads it with `LAST_QC_QUERY` on every configuration refresh,
and the web server scans it twice every twelve seconds. As the table grows the
read crosses eight seconds, LabCore kills it, clients retry, the queue deepens —
and it presents to the lab as "LabCore is offline" while LabCore is healthy.

So these tests do not assert that an index EXISTS. An index the planner declines
to use is worthless, and asserting its existence would not catch that. They seed
a real SQLite database and read `EXPLAIN QUERY PLAN` back.
"""
import sqlite3
from datetime import datetime, timedelta

import pytest

import lem_station_module as mod

NOW = datetime(2026, 8, 3, 12, 0, 0)

MACHINES = [f"m{i:02d}" for i in range(20)]
KINDS = ["run", "qc", "status_change", "comment", "pm"]
TESTS = ["Sulphur", "Density", "Viscosity", "Flash"]

# Enough rows that a planner with anything better than a scan available will
# take it, and few enough that the suite stays fast.
SEEDED_ROWS = 4000


@pytest.fixture
def db():
    """A real SQLite database carrying exactly the DDL this module declares.

    The declarations are read off the module's own constants rather than
    restated here, so a table or index that changes shape in production changes
    shape in this test too.
    """
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    for ddl in (mod.LOG_TABLE_DDL,) + tuple(mod.LOG_INDEX_DDL):
        con.execute(ddl)
    rows = []
    for i in range(SEEDED_ROWS):
        # The kind cycles on a different stride from the machine, so every
        # machine ends up with every kind. Keying both off `i % len(...)` with
        # co-prime lengths silently gives each machine exactly ONE kind, and a
        # bench with no `qc` rows at all makes an empty result look like a pass.
        kind = KINDS[(i // len(MACHINES)) % len(KINDS)]
        rows.append((
            MACHINES[i % len(MACHINES)],
            (NOW - timedelta(seconds=7 * i)).isoformat(),
            kind,
            f"L-{i}",
            TESTS[i % len(TESTS)] if kind == "qc" else "",
            "1.0",
            "{}",
        ))
    con.executemany("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    # Deliberately no ANALYZE. Nothing in LEM or LabCore ever runs one, so the
    # production database has no sqlite_stat1 for this table and the planner is
    # choosing on shape alone. Verified both ways at 100k rows: the plans below
    # are identical with and without statistics — but the test pins the case
    # production is actually in, so an index that only wins once somebody has
    # ANALYZEd cannot pass here.
    return con


def plan(con, sql, args=()):
    return [r["detail"] for r in con.execute("EXPLAIN QUERY PLAN " + sql, args)]


class TestTheQcHistoryReadUsesAnIndex:
    """`LAST_QC_QUERY`, the read this bench issues on every config refresh."""

    def test_it_never_reads_the_table_unaided(self, db):
        """`SCAN lem_machine_log` on its own — no index named — is the plan
        this whole change exists to remove: read every row off the share, on
        every configuration refresh, blocking every write in the lab while it
        runs. The word SCAN can legitimately appear with an index named after
        it (the planner walking an index in order rather than seeking into it),
        so this asserts the absence of the BARE form."""
        sql, args = mod.build_last_qc_query("m03")
        steps = plan(db, sql, args)
        assert "SCAN lem_machine_log" not in steps, steps

    def test_it_searches_the_composite_index(self, db):
        """`(machine_uid, kind, ts DESC)` — the exact shape of the WHERE plus
        the ORDER BY, so the seek lands on this machine's QC rows and walks
        them newest-first without a temp b-tree."""
        sql, args = mod.build_last_qc_query("m03")
        steps = plan(db, sql, args)
        assert any("idx_lem_log_uid_kind_ts" in s for s in steps), steps

    def test_it_sorts_off_the_index_rather_than_a_temp_btree(self, db):
        """The ORDER BY is the expensive half. Without `ts DESC` in the index
        the planner has to materialise every QC row for the machine and sort
        it, which is the work the LIMIT 400 was supposed to avoid."""
        sql, args = mod.build_last_qc_query("m03")
        steps = plan(db, sql, args)
        assert not any("TEMP B-TREE" in s.upper() for s in steps), steps


class TestTheQcPredicateIsSargable:
    """`TRIM(test_name) != ''` cannot be answered from an index — the planner
    has no index on an expression, so every candidate row has to be fetched and
    the function run on it. The rewrite says the same thing about the stored
    value instead."""

    def test_the_query_no_longer_calls_trim(self):
        sql, _args = mod.build_last_qc_query("m1")
        assert "TRIM(" not in sql.upper(), sql

    def test_it_still_excludes_the_empty_name(self, db):
        """The reason the predicate is there at all: a `run`, a `comment`, a
        `status_change` and a PM tick all carry an empty `test_name`, and a QC
        row without a method name names nothing."""
        db.execute("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
                   ("m99", NOW.isoformat(), "qc", "L-1", "", "1.0", "{}"))
        sql, args = mod.build_last_qc_query("m99")
        assert db.execute(sql, args).fetchall() == []

    def test_a_whitespace_only_verdict_never_reaches_the_table(self, db):
        """The one behaviour `TRIM(x) != ''` had that `x != ''` does not, kept
        end to end through the road that actually writes QC verdicts.

        The exclusion moved from the reader to the writer, so this goes through
        `build_log_insert` rather than inserting the row by hand: the guarantee
        being pinned is "no such row is ever created", not "such a row is
        filtered out".
        """
        sql, args = mod.build_log_insert("m98", "qc", NOW, lab_id="L-1",
                                         test_name="   ", value="1.0")
        db.execute(sql, args)
        read, read_args = mod.build_last_qc_query("m98")
        assert db.execute(read, read_args).fetchall() == []

    def test_a_row_that_predates_the_normalisation_is_still_dropped(self, db):
        """The rows already in the table, which no writer can go back and fix.

        Normalising on write only governs rows written from now on, so the
        honest question about dropping TRIM from the read is what happens to a
        whitespace-only `test_name` that is ALREADY there — written by an older
        build of this module, or by hand.

        The answer is that `last_qc_by_test` has always stripped the name and
        skipped the empty result, so the exclusion the SQL used to make is made
        again in Python one step later. The row is now returned by the query and
        discarded by the parser instead of never being returned; nothing
        downstream can tell the difference. That belt-and-braces is what makes
        this rewrite safe on a table with history in it, and this test is what
        stops somebody deleting it as redundant.
        """
        db.execute("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
                   ("m97", NOW.isoformat(), "qc", "L-1", "   ", "1.0", "{}"))
        sql, args = mod.build_last_qc_query("m97")
        rows = [dict(r) for r in db.execute(sql, args)]
        assert rows, "the legacy row is not what this test thinks it is"
        assert mod.last_qc_by_test(rows) == {}

    def test_a_real_name_still_comes_back(self, db):
        sql, args = mod.build_last_qc_query("m03")
        got = db.execute(sql, args).fetchall()
        assert got, "the index rewrite lost every verdict"
        assert all(r["test_name"].strip() for r in got)


class TestTheWriterNormalisesTheName:
    """Where the whitespace-only guarantee now lives.

    The read predicate can be index-friendly OR it can call TRIM; it cannot do
    both. Moving the normalisation to the single choke point every log row
    passes through — `build_log_insert` — means the two predicates are
    equivalent for every row this module has ever written, and the read gets to
    be sargable.
    """

    def test_a_whitespace_only_name_is_stored_as_empty(self):
        _sql, args = mod.build_log_insert("m1", "qc", NOW, test_name="   ")
        assert args[4] == ""

    def test_a_real_name_is_stored_verbatim(self):
        """Deliberately NOT `.strip()`.

        `carry_last_qc` looks the verdict back up by `spec.name`, and a spec
        name can carry padding (`specs_for_machine` takes the method string
        from the mapping as written). Stripping here would store "Sulphur"
        against a spec called " Sulphur " and the bench would lose the verdict
        it had just recorded. Only a name that is ENTIRELY whitespace — which
        no spec can be looked up by — is normalised.
        """
        _sql, args = mod.build_log_insert("m1", "qc", NOW,
                                          test_name=" Sulphur ")
        assert args[4] == " Sulphur "

    def test_an_absent_name_is_still_empty(self):
        _sql, args = mod.build_log_insert("m1", "run", NOW)
        assert args[4] == ""


class TestTheIndexesAreDeclaredWhereTheTableIs:
    def test_the_log_indexes_name_the_log_table(self):
        for ddl in mod.LOG_INDEX_DDL:
            assert "lem_machine_log" in ddl, ddl

    def test_they_are_idempotent(self):
        """Both programs declare these. Whichever starts first creates them and
        the other one's declaration must be a no-op, not an error that backs the
        whole block off."""
        for ddl in mod.LOG_INDEX_DDL:
            assert "IF NOT EXISTS" in ddl.upper(), ddl

    def test_the_module_does_not_index_a_table_it_never_creates(self):
        """`lem_maintenance` is the web server's table; this module only reads
        it. `CREATE INDEX ON lem_maintenance` from here would fail on a fresh
        LabCore that the web server has not started against yet — and a failed
        statement in `_declare_tables` backs the WHOLE block off, so this bench
        would never latch `_labcore_table_ready` and would re-fire its
        declarations for the life of the process. The index belongs in the
        program that creates the table."""
        for ddl in mod.LOG_INDEX_DDL:
            assert "lem_maintenance" not in ddl, ddl
