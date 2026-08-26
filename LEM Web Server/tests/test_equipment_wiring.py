"""The single-writer wiring: three stores that were connected to nothing.

`levels.py`, `equipment_documents.py` and `equipment_history.py` each shipped
their own DDL, their own tests and — in levels' case — their own snapshot arms,
and not one statement of any of it was applied by anything. Declared-but-inert
and working look identical from the outside, which is why levels shipped a
tripwire; these tests are the same idea for all three, held against the wired
state rather than the unwired one.

Three things are being held here and they fail in different ways:

1. **The DDL and the arms must land TOGETHER.** Every arm of the batched read
   shares ONE statement, so an arm naming a table LabCore has not got fails the
   entire read and drops the whole floor to the fallback path. That has happened
   in production once (CLAUDE.md, `correction` on `lem_machine_specs`).

2. **A floor poll must still cost ZERO LabCore operations.** The whole point of
   `snapshot_service` is that load does not scale with how many screens are
   open. Placing the fleet on levels out of rows the snapshot already holds is
   free; asking `LevelStore` per poll would be three reads every two seconds,
   forever.

3. **The settings default must never move equipment.** `placements` takes three
   arguments and deliberately no fourth, so the floor payload cannot hand it the
   preference even by accident. Asserted here at the call site, because the
   signature only protects the caller that tries.
"""

import inspect

import pytest

import equipment_documents
import equipment_history
import levels as levels_mod
import snapshot_service
import web_app
from labcore_gateway import FakeLabCoreGateway
from levels import LevelStore
from web_app import create_app


class CountingGateway(FakeLabCoreGateway):
    """Records what was asked of LabCore, so a test can prove how much."""

    def __init__(self) -> None:
        super().__init__()
        self.reads = []
        self.writes = []

    def read_sql(self, sql, args=None, **kw):
        self.reads.append(sql)
        return super().read_sql(sql, args, **kw)

    def sql(self, sql, args=None, **kw):
        self.writes.append(sql)
        return super().sql(sql, args, **kw)


@pytest.fixture
def gw():
    gateway = FakeLabCoreGateway()
    snapshot_service.SnapshotService(gateway).ensure_schema()
    return gateway


def _declared_tables():
    """The TABLE names in the central DDL, parsed the way `ensure_schema` does.

    `ensure_schema` has two parsers, not one. `CREATE INDEX x ON t(...)` is
    matched against the index list by its OWN name, and everything else against
    the table list by the word after IF NOT EXISTS. A helper carrying only the
    second parser reads an index's `ON` clause as part of a table name, which
    is a fact about the helper and not about the schema.
    """
    return [ddl.split("IF NOT EXISTS", 1)[1].split("(", 1)[0].strip()
            for ddl in snapshot_service.SCHEMA_DDL
            if "CREATE INDEX" not in ddl.upper()]


def _declared_indexes():
    """The INDEX names, parsed the way `ensure_schema` parses them."""
    return [ddl.split("IF NOT EXISTS", 1)[1].split(" ON ", 1)[0].strip()
            for ddl in snapshot_service.SCHEMA_DDL
            if "CREATE INDEX" in ddl.upper()]


# ── 1. the schema ────────────────────────────────────────────────────────────

class TestEveryStoresTableIsDeclaredCentrally:
    def test_the_levels_ddl_is_the_constant_not_a_retyped_copy(self):
        """A copy drifts. The wiring is an import, so a column added in
        `levels.py` cannot be missing from what boot declares."""
        for ddl in levels_mod.SCHEMA_DDL:
            assert ddl in snapshot_service.SCHEMA_DDL

    def test_the_documents_ddl_is_the_constant(self):
        assert equipment_documents.DOCUMENTS_DDL in snapshot_service.SCHEMA_DDL

    def test_the_history_ddl_is_the_constant(self):
        for ddl in equipment_history.HISTORY_DDL:
            assert ddl in snapshot_service.SCHEMA_DDL

    def test_every_new_table_is_declared_exactly_once(self):
        """`ensure_schema` skips a table it can see already exists, so a
        duplicate entry is a wasted write on every boot where the probe fails —
        and two spellings of one table is how the columns diverge."""
        names = _declared_tables()
        for table in ("lem_levels", "lem_machine_level", "lem_level_settings",
                      "lem_equipment_documents", "lem_corrective_actions",
                      "lem_correction_audit", "lem_action_events"):
            assert names.count(table) == 1, table

    def test_the_ddl_still_parses_the_way_ensure_schema_parses_it(self):
        """`ensure_schema` pulls the name out by splitting on "IF NOT EXISTS",
        so a DDL written any other way is declared on every start instead of
        being skipped.

        Both halves are held, because there are two probes: a table is looked
        up in the table list and an index in the index list, and a name that
        parses into either the wrong shape or the wrong list is re-declared
        forever — two writes into a ~1.5 ops/sec queue on every restart, and
        the tray restarts this server on every code edit."""
        for name in _declared_tables():
            assert name.startswith("lem_"), name
            assert " " not in name and "(" not in name, name
        for name in _declared_indexes():
            assert name.startswith("idx_"), name
            assert " " not in name and "(" not in name, name

    def test_nothing_new_went_into_schema_migrations(self):
        """Every table here is NEW, so `CREATE TABLE IF NOT EXISTS` is enough.
        SCHEMA_MIGRATIONS is for columns added to a table that already exists in
        the field."""
        migrated = {table for table, _col, _ddl
                    in snapshot_service.SCHEMA_MIGRATIONS}
        assert migrated == {"lem_machine_specs"}

    def test_no_existing_lem_table_grew_a_column(self):
        """RELEASING.md §2: a new or renamed `lem_*` column on a table the
        benches read is a MAJOR release. This wiring is a MINOR."""
        blob = " ".join(levels_mod.SCHEMA_DDL) + " " \
            + equipment_documents.DOCUMENTS_DDL + " " \
            + " ".join(equipment_history.HISTORY_DDL)
        for existing in ("lem_machine_status", "lem_machine_layout",
                         "lem_machine_targets", "lem_qc_specs",
                         "lem_machine_specs", "lem_machine_heartbeat",
                         "lem_machine_log", "lem_correction_factors"):
            assert existing not in blob

    def test_boot_actually_creates_them(self, gw):
        """Behavioural, not a grep: the stores are used against a LabCore whose
        only schema is what `ensure_schema` declared."""
        LevelStore(gw).create("Ground")
        equipment_history.CorrectiveActionStore(gw).open_action(
            "m1", what_happened="QC out of spec", uid="CA-1")
        equipment_history.CorrectionAuditStore(gw).record("m1", "Sulfur", 0.0,
                                                          -0.4)
        assert gw.read_sql(
            "SELECT COUNT(*) AS n FROM lem_equipment_documents"
        )["rows"][0]["n"] == 0


# ── 2. the batched read ──────────────────────────────────────────────────────

class TestTheArmsLandedWithTheDdl:
    def test_the_level_arms_are_in_the_batched_read(self):
        names = [name for name, _sql in snapshot_service._ARMS]
        for arm in ("level", "levelof", "levelset"):
            assert arm in names
            assert names.count(arm) == 1

    def test_the_arms_are_the_constants_from_levels(self):
        for arm in levels_mod.SNAPSHOT_ARMS:
            assert arm in snapshot_service._ARMS

    def test_every_arm_is_the_same_width(self):
        """The dangerous edit. An arm of the wrong width fails the ENTIRE
        statement and takes every other table with it."""
        import re

        def aliases(sql):
            return {int(n) for n in re.findall(r"\bAS c(\d+)\b", sql)}

        reference = aliases(snapshot_service._ARMS[0][1])
        assert reference
        for name, sql in snapshot_service._ARMS:
            assert aliases(sql) == reference, name

    def test_every_arm_names_its_own_src(self):
        for name, sql in snapshot_service._ARMS:
            assert f"'{name}' AS src" in sql, name

    def test_the_whole_statement_runs_against_a_freshly_declared_labcore(self,
                                                                        gw):
        """The real failure mode, reproduced the way production would meet it:
        one statement, every arm, against exactly the schema boot creates."""
        res = gw.read_sql(snapshot_service.batched_machine_sql())
        assert not res.get("error"), res.get("error")

    def test_every_arm_also_runs_on_its_own(self, gw):
        """The fallback path runs each arm separately, and that is where an
        unaliased column comes back nameless."""
        for name, sql in snapshot_service._ARMS:
            res = gw.read_sql(sql)
            assert not res.get("error"), (name, res.get("error"))

    def test_the_documents_table_is_not_an_arm(self):
        """Deliberate: documents are per-equipment and read on demand. An extra
        arm buys a tab badge with the whole floor's read."""
        sql = snapshot_service.batched_machine_sql()
        assert "lem_equipment_documents" not in sql

    def test_the_history_tables_are_not_arms(self):
        """A timeline is opened by a person, not polled by the floor."""
        sql = snapshot_service.batched_machine_sql()
        for table in ("lem_corrective_actions", "lem_correction_audit",
                      "lem_action_events"):
            assert table not in sql

    def test_a_level_made_through_the_store_comes_back_out_of_the_one_read(
            self, gw):
        """The end state the levels xfail described: no extra LabCore op at
        all."""
        store = LevelStore(gw)
        ground = store.create("Ground")
        store.assign("m1", ground.uid, by="ryan")
        store.set_default_level(ground.uid)
        res = gw.read_sql(snapshot_service.batched_machine_sql())
        assert not res.get("error"), res.get("error")
        tables = snapshot_service.split_batched(res.get("rows") or [])
        assert levels_mod.levels_from_tables(tables) == [ground]
        assert levels_mod.assignments_from_tables(tables) == {"m1": ground.uid}
        assert levels_mod.default_level_from_tables(tables) == ground.uid


# ── 3. the floor payload ─────────────────────────────────────────────────────

def _seed_machine(gw, uid="m1", title="GC-1"):
    gw.sql("INSERT INTO lem_machine_status (machine_uid, title, status, "
           "reason, updated_at) VALUES (?, ?, 'GREEN', '', '2026-08-01T09:00:00')",
           [uid, title])


class TestTheFloorPlacesTheWholeFleet:
    def test_a_flat_lab_still_draws(self, gw):
        """The live floor today: no levels at all, and not one instrument may
        vanish or grow a level it does not stand on."""
        _seed_machine(gw)
        built = _build(gw)
        assert [m["machine_uid"] for m in built["machines"]] == ["m1"]
        assert built["machines"][0]["level_uid"] == ""
        assert built["levels"] == []
        assert built["default_level"] == ""

    def test_an_unplaced_instrument_stands_on_the_ground(self, gw):
        _seed_machine(gw)
        store = LevelStore(gw)
        ground = store.create("Ground")
        store.create("Second")
        built = _build(gw)
        assert built["machines"][0]["level_uid"] == ground.uid
        assert [l["name"] for l in built["levels"]] == ["Ground", "Second"]

    def test_a_placed_instrument_stands_where_it_was_put(self, gw):
        _seed_machine(gw)
        store = LevelStore(gw)
        store.create("Ground")
        second = store.create("Second")
        store.assign("m1", second.uid, by="ryan")
        built = _build(gw)
        machine = built["machines"][0]
        assert machine["level_uid"] == second.uid
        assert machine["level_moved_by"] == "ryan"
        assert machine["level_moved_at"]

    def test_a_deleted_level_drops_its_equipment_to_the_ground(self, gw):
        """LabCore has no foreign keys. The rows dangle; the instrument must
        not fall off the map."""
        _seed_machine(gw)
        store = LevelStore(gw)
        ground = store.create("Ground")
        second = store.create("Second")
        store.assign("m1", second.uid)
        gw.sql("DELETE FROM lem_levels WHERE uid = ?", [second.uid])
        built = _build(gw)
        assert built["machines"][0]["level_uid"] == ground.uid

    def test_the_settings_default_never_moves_the_fleet(self, gw):
        """The bug this whole split exists to prevent: flipping the settings
        drop-down moved every unplaced instrument up a floor with not one row
        written in `lem_machine_level`."""
        _seed_machine(gw)
        store = LevelStore(gw)
        ground = store.create("Ground")
        second = store.create("Second")
        before = _build(gw)["machines"][0]["level_uid"]
        store.set_default_level(second.uid)
        after = _build(gw)
        assert after["machines"][0]["level_uid"] == before == ground.uid
        # …and the preference IS reported, because the picker opens on it.
        assert after["default_level"] == second.uid
        assert after["ground_level"] == ground.uid

    def test_the_default_resolves_when_it_names_a_level_that_is_gone(self, gw):
        _seed_machine(gw)
        store = LevelStore(gw)
        ground = store.create("Ground")
        second = store.create("Second")
        store.set_default_level(second.uid)
        gw.sql("DELETE FROM lem_levels WHERE uid = ?", [second.uid])
        assert _build(gw)["default_level"] == ground.uid

    def test_placements_is_never_handed_the_preference(self):
        """Stronger than a comment: `placements` takes three arguments and
        deliberately no fourth, and the floor payload must not have grown a way
        around that."""
        params = list(inspect.signature(levels_mod.placements).parameters)
        assert params == ["machine_uids", "assignments", "levels"]
        source = inspect.getsource(snapshot_service.build_machines)
        assert "placements(" in source
        assert "default_level_from_tables" in source
        # The preference is read for the payload, never fed to the placer.
        placer = source.split("placements(", 1)[1].split(")", 1)[0]
        assert "default" not in placer


def _build(gw):
    tables = snapshot_service.SnapshotService(gw).read_tables()
    return snapshot_service.build_machines(tables, web_app._now(),
                                           web_app._beat_is_fresh,
                                           web_app.STATUS_COLORS)


# ── 4. the zero-op gate ──────────────────────────────────────────────────────

class TestAFloorPollCostsNothing:
    def test_polling_the_floor_with_levels_present_reads_labcore_zero_times(
            self):
        """The gate. Levels are placed out of rows the snapshot already holds,
        so a floor poll is served from memory — the same as before this landed.
        Asking `LevelStore` here instead would be three reads every two seconds
        from every open screen."""
        gw = CountingGateway()
        snapshot_service.SnapshotService(gw).ensure_schema()
        _seed_machine(gw)
        store = LevelStore(gw)
        ground = store.create("Ground")
        store.create("Second")
        store.assign("m1", ground.uid, by="ryan")
        store.set_default_level(ground.uid)

        app = create_app(gw, secret="t")
        app.config.update(TESTING=True)
        client = app.test_client()
        client.get("/api/machines")             # the first build pays for itself
        gw.reads.clear()
        gw.writes.clear()
        for _ in range(10):
            resp = client.get("/api/machines")
            assert resp.status_code == 200
        assert gw.reads == [], gw.reads
        assert gw.writes == [], gw.writes

    def test_the_poll_still_carries_the_levels(self):
        gw = FakeLabCoreGateway()
        snapshot_service.SnapshotService(gw).ensure_schema()
        _seed_machine(gw)
        store = LevelStore(gw)
        ground = store.create("Ground")
        second = store.create("Second")
        store.assign("m1", second.uid, by="ryan")
        app = create_app(gw, secret="t")
        app.config.update(TESTING=True)
        body = app.test_client().get("/api/machines?fresh=1").get_json()
        assert [l["uid"] for l in body["levels"]] == [ground.uid, second.uid]
        assert body["default_level"] == ground.uid
        assert body["machines"][0]["level_uid"] == second.uid

    def test_the_live_overlay_does_not_lose_the_level(self):
        """`merge_machines` rebuilds each machine dict from the live entry when
        a bench has spoken; a field it does not know about must survive that."""
        gw = FakeLabCoreGateway()
        snapshot_service.SnapshotService(gw).ensure_schema()
        _seed_machine(gw)
        store = LevelStore(gw)
        store.create("Ground")
        second = store.create("Second")
        store.assign("m1", second.uid)
        app = create_app(gw, secret="t", live_token="tok")
        app.config.update(TESTING=True)
        client = app.test_client()
        client.post("/api/live", headers={"X-LEM-Token": "tok"},
                    json={"machine_uid": "m1", "status": "RED",
                          "reason": "QC", "interval_seconds": 40})
        body = client.get("/api/machines?fresh=1").get_json()
        machine = body["machines"][0]
        assert machine["status"] == "RED"
        assert machine["level_uid"] == second.uid
