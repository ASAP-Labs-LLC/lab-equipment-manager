#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Where `lem_uncertainty_estimates` is declared, and what it deliberately is not.

`snapshot_service` is the single writer of the schema and it IMPORTS the
constants rather than retyping them (see the LEM Web Server CLAUDE.md, "The
equipment record is wired up"). A retyped copy drifts, and a copy that drifts
here is an arm selecting a column the boot path never declared — which fails the
ONE statement every other arm shares and drops the whole floor to the fallback
path.

The other half of this file is what was NOT done, and why:

  * **no snapshot arm.** Every arm is bought with the whole floor's 2-second
    read. The uncertainty register is a page nobody polls.
  * **no `SCHEMA_MIGRATIONS` entry.** That tuple is for a column added to a
    table that already exists in the field. This is a new table, so
    `CREATE TABLE IF NOT EXISTS` is the whole of it — asserted here rather than
    assumed.
"""

import ast
import os
import re

import snapshot_service
import uncertainty
from labcore_gateway import FakeLabCoreGateway

TABLE = "lem_uncertainty_estimates"


class TestTheDeclaration:

    def test_snapshot_service_imports_the_constant_and_does_not_retype_it(self):
        assert uncertainty.UNCERTAINTY_DDL in snapshot_service.SCHEMA_DDL
        # Identity, not equality: a retyped copy would satisfy `in` and still
        # be free to drift on the next edit.
        assert any(ddl is uncertainty.UNCERTAINTY_DDL
                   for ddl in snapshot_service.SCHEMA_DDL)
        source = open(snapshot_service.__file__, encoding="utf-8").read()
        assert "CREATE TABLE IF NOT EXISTS " + TABLE not in source

    def test_it_declares_the_table_this_module_reads_and_writes(self):
        assert TABLE in uncertainty.UNCERTAINTY_DDL
        assert uncertainty.UNCERTAINTY_DDL.startswith(
            "CREATE TABLE IF NOT EXISTS")

    def test_every_column_the_dataclass_carries_is_in_the_DDL(self):
        for column in uncertainty.COLUMNS:
            assert re.search(r"\b{}\b".format(re.escape(column)),
                             uncertainty.UNCERTAINTY_DDL), column

    def test_the_spec_s_own_columns_are_all_present(self):
        """The schema in the design doc, field for field."""
        for column in (
                "estimate_id", "machine_uid", "test_name", "sample_name",
                "window_start", "window_end", "n", "n_operators", "n_days",
                "mean", "s", "rw_route", "u_rw", "bias_route", "cert_value",
                "u_cref", "bias", "u_bias", "u_c", "k", "u_expanded",
                "astm_r", "r_ratio", "bias_decision", "contributions",
                "exclusions", "notes", "computed_at", "computed_by",
                "approved_at", "approved_by", "superseded_by"):
            assert column in uncertainty.COLUMNS, column

    def test_the_boot_path_creates_it_on_a_fresh_LabCore(self):
        gateway = FakeLabCoreGateway()
        for ddl in snapshot_service.SCHEMA_DDL:
            gateway.sql(ddl)
        got = gateway.read_sql("SELECT COUNT(*) AS n FROM " + TABLE)
        assert got.get("ok") and got["rows"][0]["n"] == 0

    def test_a_store_that_declares_it_itself_lands_on_the_same_shape(self):
        """Whoever gets there first wins, so the two must be the same statement."""
        boot, own = FakeLabCoreGateway(), FakeLabCoreGateway()
        for ddl in snapshot_service.SCHEMA_DDL:
            boot.sql(ddl)
        uncertainty.UncertaintyStore(own).ensure_schema()
        shape = "SELECT sql FROM sqlite_master WHERE name = ?"
        assert (boot.read_sql(shape, [TABLE])["rows"]
                == own.read_sql(shape, [TABLE])["rows"])


class TestWhatWasDeliberatelyNotDone:

    def test_it_is_NOT_a_snapshot_arm(self):
        """Every arm is bought with the whole floor's 2-second read."""
        assert TABLE not in snapshot_service.batched_machine_sql()
        assert not any(TABLE in sql for _name, sql in snapshot_service._ARMS)
        assert "uncertain" not in [name for name, _ in snapshot_service._ARMS]

    def test_it_needs_no_SCHEMA_MIGRATIONS_entry_and_has_none(self):
        """Confirmed rather than assumed: a NEW table is fully described by its
        CREATE, and `SCHEMA_MIGRATIONS` is only for a column added to a table
        already in the field."""
        assert all(table != TABLE
                   for table, _column, _ddl in snapshot_service.SCHEMA_MIGRATIONS)
        # And nothing else moved: the migrations tuple is exactly what it was.
        assert snapshot_service.SCHEMA_MIGRATIONS == (
            ("lem_machine_specs", "correction",
             "ALTER TABLE lem_machine_specs ADD COLUMN correction REAL "
             "DEFAULT 0.0"),)

    def test_no_existing_lem_table_gains_a_column(self):
        """New table, no bench moves — this ships as a MINOR."""
        assert "ALTER TABLE" not in uncertainty.UNCERTAINTY_DDL
        source = open(uncertainty.__file__, encoding="utf-8").read()
        for other in ("lem_qc_samples", "lem_qc_specs", "lem_machine_specs",
                      "lem_machine_targets"):
            assert "ALTER TABLE " + other not in source

    def test_the_only_other_table_it_touches_is_the_log_and_only_to_READ(self):
        source = open(uncertainty.__file__, encoding="utf-8").read()
        for verb in ("INSERT INTO lem_machine_log", "UPDATE lem_machine_log",
                     "DELETE FROM lem_machine_log"):
            assert verb not in source


class TestNoPipDependencies:

    def test_it_imports_stdlib_and_two_local_modules_and_nothing_else(self):
        tree = ast.parse(open(uncertainty.__file__, encoding="utf-8").read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported.add((node.module or "").split(".")[0])
        assert imported <= {
            "__future__", "dataclasses", "datetime", "json", "math",
            "statistics", "typing", "uuid", "contextlib",
            "labcore_result", "qc_series"}


# The wiring tripwire that stood here has been removed, as its own docstring
# instructed: `uncertainty.py` shipped connected to nothing, and
# "declared-but-inert" and "working" look identical from the outside — the
# fourth store in this app to need saying so. The routes are mounted now and
# `tests/test_uncertainty_web.py` holds them, including the three cases the
# design doc named: auth on every route, compute never auto-approving, and the
# register carrying all twelve SOP 2.10 fields.

