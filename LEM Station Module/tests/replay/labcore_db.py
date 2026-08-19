"""A real sqlite LabCore, driven by LabCore's own batch handlers.

Nothing here reimplements a write. `LabCoreGateway.batch` looks each inner
operation up in LabCore's real `_BATCH_INNER_OPS` table and hands it a live
connection — exactly what `_wop_batch` (LabCore.py:7489-7514) does inside its
transaction. That matters for this investigation: the whole bug turns on
`_batch_update_cell` doing a bare INSERT when no `sample_tests` row matches
(LabCore.py:7519-7549) and there being no foreign key to stop it. A hand-rolled
stand-in would be free to be stricter than the real thing and would hide it.

The only verbatim copy is the schema DDL, taken from LabCore.py:2606-2626 (the
"create a new database" dialog) — it is inline in a Qt slot, so it cannot be
imported. Copied, not paraphrased, including the absence of any FOREIGN KEY.

── WHAT COUNTS AS DELIVERY (hardened 2026-08-11) ───────────────────────────────

The first version of this oracle asked "does SOME row whose lab_id shares a
suffix with the printed ID carry the value". That is not delivery, it is a
resemblance test, and it blesses the two worst outcomes a fix can produce:

  • a FORKED identity — a phantom sample "34566" minted beside the LIMS's
    "081126-34566", so the LIMS record's cell stays blank forever;
  • a CROSS-DAY COLLISION — yesterday's 081026-34566 answering for today's
    081126-34566, because both end in 34566.

So identity is now EXACT and the sample table is audited:

  `delivered(test, lab_id)` matches `s.lab_id == lab_id` and nothing else, and
  refuses (raises) if two rows would answer — an ambiguous grid is not a
  delivery.

  `phantom_samples(seeded)` is every samples row the LIMS did not create.
  `orphan_rows()` is every sample_tests row with no sample. Both must be empty:
  the LIMS owns sample identity, and a bench instrument printing a cup number
  cannot mint one.

The join also carries the REAL date filter now (in_range CASE + manual IDs,
LabStation.pyw:11589-11613) instead of a hardcoded `1 AS in_range`, so the
oracle can be run at the ResultsModule's shipped default ("Yesterday") and is
never more permissive than the grid it stands for.
"""
import sqlite3
from pathlib import Path

# ── verbatim from LabCore.py:2606-2626 (NewDatabaseDialog._create) ──────────
# Kept character-for-character so the harness inherits LabCore's real
# constraints — in particular that `sample_tests` has NO foreign key onto
# `samples`, which is what lets an orphan test row be accepted silently.
SAMPLES_DDL = (
    "CREATE TABLE IF NOT EXISTS samples ("
    "lab_id TEXT PRIMARY KEY, sample_date_key TEXT, order_id TEXT, "
    "sample_id TEXT, customer_name TEXT, tracking_number TEXT, "
    "rush_status TEXT, testing_package TEXT, fuel_type TEXT, "
    "po_number TEXT, work_order TEXT, tank_number TEXT, "
    "sample_from TEXT, collection_date TEXT, tank_capacity TEXT, "
    "site_location TEXT, notes TEXT, prep_tests TEXT, "
    "raw_json TEXT, first_seen_at TEXT, last_seen_at TEXT, "
    "Photos TEXT, PhotoNames TEXT, PhotosBlob BLOB, sample_received TEXT)"
)
SAMPLE_TESTS_DDL = (
    "CREATE TABLE IF NOT EXISTS sample_tests ("
    "lab_id TEXT NOT NULL, test_name TEXT NOT NULL, result TEXT, "
    "updated_at TEXT, operator TEXT, "
    "PRIMARY KEY (lab_id, test_name))"
)
SAMPLE_TEST_RESULTS_DDL = (
    "CREATE TABLE IF NOT EXISTS sample_test_results ("
    "lab_id TEXT NOT NULL, test_name TEXT NOT NULL, "
    "result_value TEXT, updated_at TEXT NOT NULL, "
    "source_workspace TEXT, PRIMARY KEY (lab_id, test_name))"
)

# The column LabCore's insert_sample stamps as `received_at`
# (LV_DB_SCHEMA, LabCore.py:5875) and the column the Results grid dates rows by
# (`db_date_column`, LabStation.pyw:8823 → "received_at" → same column).
RECEIVED_COL = "first_seen_at"


def build_grid_sql(tests, date_sql="", date_params=(), manual_ids=()):
    """The Results grid's OWN query, rebuilt the way it is rebuilt at
    LabStation.pyw:11589-11613 — including the per-row `in_range` flag and the
    "always include manually-added IDs" arm.

    The previous harness hardcoded `1 AS in_range` and dropped the date clause,
    which made the oracle strictly MORE permissive than the grid it claimed to
    be: a sample stamped `datetime.now()` by an invented insert_sample could
    never paint under the shipped "Yesterday" filter, yet was scored delivered.
    """
    tests = list(tests)
    manual_ids = sorted(manual_ids or [])
    select_params = []
    if date_sql:
        date_expr = date_sql.strip()
        if date_expr.upper().startswith("AND "):
            date_expr = date_expr[4:]
        in_range_sql = f"(CASE WHEN {date_expr} THEN 1 ELSE 0 END)"
        select_params += list(date_params)
    else:
        in_range_sql = "1"
    placeholders = ", ".join("?" * len(tests))
    sql = (
        'SELECT s."lab_id" AS lab_id, t."result" AS result, '
        't."test_name" AS test_name, '
        f'{in_range_sql} AS in_range '
        'FROM "samples" s JOIN "sample_tests" t '
        'ON t."lab_id" = s."lab_id" '
        f'WHERE t."test_name" IN ({placeholders})'
    )
    params = select_params + list(tests)
    if date_sql and manual_ids:
        date_expr = date_sql.strip()
        if date_expr.upper().startswith("AND "):
            date_expr = date_expr[4:]
        mph = ", ".join("?" * len(manual_ids))
        sql += f' AND ({date_expr} OR s."lab_id" IN ({mph}))'
        params += list(date_params) + list(manual_ids)
    else:
        sql += date_sql
        params += list(date_params)
    sql += ' ORDER BY s."lab_id", t."test_name"'
    return sql, params


def create_database(path: Path) -> Path:
    con = sqlite3.connect(str(path))
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(SAMPLES_DDL)
        con.execute(SAMPLE_TESTS_DDL)
        con.execute(SAMPLE_TEST_RESULTS_DDL)
        con.commit()
    finally:
        con.close()
    return path


def _suffix(value: str) -> str:
    """LabStation's own lab-ID suffix rule (ResultsModule._lab_id_suffix,
    LabStation.pyw:9002).

    Kept ONLY to explain a failure and to prove a collision — never to decide
    delivery. Two different real samples on two different days share a suffix,
    so a suffix match is not an identity.
    """
    cleaned = str(value).strip()
    if "-" in cleaned:
        return cleaned.split("-")[-1].strip()
    return cleaned


class AmbiguousDelivery(AssertionError):
    """Two grid rows would answer for one reading. Not a delivery."""


class Oracle:
    """Ground truth: what the Results grid can actually show, for the sample
    the LIMS actually created."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        # Set by the `seed` fixture: every sample identity the LIMS created.
        # Anything else in `samples` was invented by the software under test.
        self.seeded_samples = set()

    # ── the grid's own query ──────────────────────────────────────────────
    def rows(self, tests, date_sql="", date_params=(), manual_ids=(),
             in_range_only=False):
        tests = list(tests)
        if not tests:
            return []
        sql, params = build_grid_sql(tests, date_sql, date_params, manual_ids)
        con = sqlite3.connect(str(self.db_path))
        con.row_factory = sqlite3.Row
        try:
            out = [dict(r) for r in con.execute(sql, params).fetchall()]
        finally:
            con.close()
        if in_range_only:
            out = [r for r in out if r.get("in_range")]
        return out

    def delivered(self, test, lab_id, date_sql="", date_params=(),
                  manual_ids=(), in_range_only=False):
        """The value the grid would show for THIS sample, or None.

        Identity is exact. A reading that landed on a phantom "34566" beside
        the LIMS's "081126-34566" is not delivered to "081126-34566", and a
        reading on yesterday's 081026-34566 is not delivered to today's
        081126-34566 — the old suffix rule said yes to both.
        """
        hits = []
        for row in self.rows(test if isinstance(test, (list, tuple)) else [test],
                             date_sql, date_params, manual_ids, in_range_only):
            if row["test_name"] != test:
                continue
            if row["lab_id"] != lab_id:
                continue
            hits.append((row["result"] or "").strip())
        if len(hits) > 1 and len(set(hits)) > 1:
            raise AmbiguousDelivery(
                f"{len(hits)} grid rows answer for ({lab_id!r}, {test!r}): "
                f"{hits}")
        for value in hits:
            if value:
                return value
        return None

    # ── identity audit: the checks the first harness did not make ─────────
    def samples(self):
        con = sqlite3.connect(str(self.db_path))
        try:
            return {r[0] for r in con.execute('SELECT lab_id FROM "samples"')}
        finally:
            con.close()

    def sample_dates(self):
        con = sqlite3.connect(str(self.db_path))
        try:
            return {r[0]: r[1] for r in con.execute(
                f'SELECT lab_id, "{RECEIVED_COL}" FROM "samples"')}
        finally:
            con.close()

    def phantom_samples(self):
        """Sample identities NOBODY logged in — minted by the software under
        test from whatever the instrument happened to print.

        The LIMS owns sample identity. A bench printing a bare cup number
        cannot create a sample: the phantom never matches the LIMS record, the
        LIMS record's cell stays blank forever, and under the shipped date
        filter the phantom (stamped `datetime.now()` by insert_sample,
        LabCore.py:7558) is not even visible.
        """
        return sorted(self.samples() - set(self.seeded_samples))

    def orphan_rows(self):
        """sample_tests rows with no samples row — the shape of this bug. The
        grid's INNER JOIN can never return one."""
        con = sqlite3.connect(str(self.db_path))
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(
                'SELECT t.* FROM "sample_tests" t LEFT JOIN "samples" s '
                'ON s."lab_id" = t."lab_id" WHERE s."lab_id" IS NULL'
            ).fetchall()]
        finally:
            con.close()

    def raw_test_rows(self):
        con = sqlite3.connect(str(self.db_path))
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(
                'SELECT lab_id, test_name, result FROM "sample_tests"'
            ).fetchall()]
        finally:
            con.close()

    def cell(self, test, lab_id):
        """The stored sample_tests value regardless of readability — used to
        show WHERE a value went, never to decide that it arrived."""
        for row in self.raw_test_rows():
            if row["test_name"] == test and row["lab_id"] == lab_id:
                return (row["result"] or "").strip()
        return None


class WriteRefused(Exception):
    """LabCore's queue said no. Never raised into module code — the gateway
    turns it into the error DICT LabCore actually returns."""


class LabCoreGateway:
    """Stands in for LabCore's HTTP surface, backed by a real sqlite file and
    LabCore's own handlers.

    `ops` records every operation that reached the database layer, which is how
    the harness answers "was this value written by anybody at all", "did it go
    to the right TEST", and "did the same print get written twice".
    """

    # LabCore refuses a write past this many pending (see MEMORY:
    # labcore-write-queue-limits — it returns an error DICT, never raises).
    QUEUE_LIMIT = 100

    def __init__(self, db_path, labcore_mod, clock=None):
        self.db_path = Path(db_path)
        self.lc = labcore_mod
        self.clock = clock
        self.ops = []            # every inner op that was dispatched
        self.refused_ops = []    # ops rejected by the queue limit
        self.pending = 0         # simulated depth of LabCore's write queue
        self.sql_log = []
        self.read_log = []

    # ── queue pressure ────────────────────────────────────────────────────
    def set_pending(self, n):
        self.pending = int(n)

    def _queue_error(self):
        if self.pending >= self.QUEUE_LIMIT:
            return {"error": f"Write queue full ({self.pending} pending). "
                             "Try again shortly."}
        return None

    # ── writes ────────────────────────────────────────────────────────────
    def _dispatch(self, operations):
        results = []
        con = sqlite3.connect(str(self.db_path))
        try:
            for i, sub in enumerate(operations):
                name = sub.get("operation", "")
                params = sub.get("params", {}) or {}
                handler = self.lc._BATCH_INNER_OPS.get(name)
                if handler is None:
                    results.append({"index": i,
                                    "error": f"Operation {name} not batchable."})
                    continue
                try:
                    res = handler(con, params)
                except Exception as exc:          # pragma: no cover
                    res = {"error": str(exc)}
                self.ops.append({"operation": name, "params": dict(params),
                                 "result": res})
                results.append({"index": i, **res})
            con.commit()
        finally:
            con.close()
        return results

    def batch(self, operations):
        """The shape ResultsModule._dispatch_batch_push calls
        (context.labcore_client.batch)."""
        err = self._queue_error()
        if err:
            self.refused_ops.extend(operations)
            return err
        return {"ok": True, "results": self._dispatch(operations)}

    def write(self, operation, params=None, source=""):
        """The shape LEM's injected `labcore_write` is called with."""
        params = params or {}
        err = self._queue_error()
        if err:
            if operation == "batch":
                self.refused_ops.extend(params.get("operations") or [])
            else:
                self.refused_ops.append({"operation": operation,
                                         "params": params})
            return err
        if operation == "batch":
            return {"ok": True,
                    "results": self._dispatch(params.get("operations") or [])}
        return {"ok": True, "results": self._dispatch(
            [{"operation": operation, "params": params}])}

    # ── raw sql (LEM's lem_* housekeeping tables) ─────────────────────────
    def sql(self, statement, args=(), source=""):
        self.sql_log.append((statement, list(args or ())))
        err = self._queue_error()
        if err:
            return err
        con = sqlite3.connect(str(self.db_path))
        try:
            con.execute(statement, list(args or ()))
            con.commit()
            return {"ok": True}
        except Exception as exc:
            return {"error": str(exc)}
        finally:
            con.close()

    # ── reads ─────────────────────────────────────────────────────────────
    def read_sql(self, statement, params=None, timeout=None, source=""):
        self.read_log.append((statement, list(params or ())))
        con = sqlite3.connect(str(self.db_path))
        con.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in con.execute(
                statement, list(params or ())).fetchall()]
            return {"rows": rows}
        except Exception as exc:
            # Real LabCore reports a missing lem_* table exactly this way.
            return {"error": str(exc)}
        finally:
            con.close()

    # ── what the harness asks afterwards ──────────────────────────────────
    def value_writes(self, lab_id=None, test=None):
        out = []
        for op in self.ops:
            if op["operation"] != "update_cell":
                continue
            p = op["params"]
            if lab_id is not None and str(p.get("lab_id")) != lab_id:
                continue
            if test is not None and str(p.get("test_name")) != test:
                continue
            out.append((str(p.get("lab_id")), str(p.get("test_name")),
                        str(p.get("value"))))
        return out

    def inserted_samples(self):
        return [op["params"].get("lab_id") for op in self.ops
                if op["operation"] == "insert_sample"]

    def duplicate_value_writes(self):
        """(lab_id, test_name, value) triples emitted more than once.

        An unchanged value written a second time re-stamps updated_at and the
        operator, and costs a slot in a queue that refuses past 100 pending.
        """
        seen, dupes = set(), []
        for w in self.value_writes():
            if w in seen:
                dupes.append(w)
            seen.add(w)
        return dupes

    def mark(self):
        """A cursor into `ops`, so a test can talk about one poll's writes."""
        return len(self.ops)

    def ops_since(self, mark):
        return list(self.ops[mark:])
