"""Vertical levels — the floor stacked, not just spread out.

Ryan: "I want vertical layers like levels if you will. These 'Levels' can be
renamed, in the UI you can cycle through them. There is in the settings also a
default level. You can move machines up and down a level, you can also create a
level, when you create a machine it will allow you to decide on which layer."

Two things these tests care about more than anything else, because they are the
lab's normal state and not edge cases:

* **The lab is running RIGHT NOW with no levels at all.** Every instrument on
  the floor today has no assignment and never will have one until somebody
  makes a level. Not one of them may vanish.
* **A level gets deleted with equipment standing on it.** LabCore has no
  foreign keys, so nothing stops it and nothing cleans up after it. The
  equipment has to land somewhere visible.

The third is the cost: the floor payload is rebuilt for the whole fleet on
every poll, so reading the assignments must be ONE query no matter how many
instruments there are. An N+1 here is paid forever.

The fourth arrived from review and is the sharpest of them: **the floor-wide
default is a view setting, not a placement.** It must never decide where an
unplaced instrument is drawn, or one person changing a drop-down in settings
teleports the entire fleet with not one row written. `TestTheDefaultIsAView`
is that guarantee.
"""
import json
import re
from datetime import datetime

import pytest

import levels as levels_mod
import snapshot_service
from labcore_gateway import FakeLabCoreGateway
from labcore_result import LabCoreRefused, LabCoreUnavailable
from levels import (DEFAULT_LEVEL_KEY, Level, LevelStore,
                    assignments_from_tables, cycle, default_level_from_tables,
                    ground_level_uid, levels_from_tables, machines_on,
                    placements, resolve_default)


def _alias_indices(sql: str):
    """The `c1…cN` aliases one UNION arm selects."""
    return {int(n) for n in re.findall(r"\bAS c(\d+)\b", sql)}


def _batched(arms) -> str:
    """The statement snapshot_service actually issues, spelled its way."""
    return "\n UNION ALL ".join(sql for _name, sql in arms)


@pytest.fixture
def bare():
    """LabCore before anybody wired the tables up — and the lab still runs."""
    return FakeLabCoreGateway()


@pytest.fixture
def gw(bare):
    """Tables declared exactly the way snapshot_service.ensure_schema declares
    them. levels.py never creates a table itself; this fixture stands in for
    the single writer that will."""
    for ddl in levels_mod.SCHEMA_DDL:
        bare.sql(ddl)
    return bare


@pytest.fixture
def store(gw):
    return LevelStore(gw)


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


class RefusingGateway(FakeLabCoreGateway):
    """LabCore's write queue past 100 pending: it returns an error dict rather
    than raising, which is precisely how a refused write becomes a silent lie."""

    def __init__(self, refuse_after: int = 0) -> None:
        super().__init__()
        self.refuse_after = refuse_after
        self.attempts = 0

    def sql(self, sql, args=None, **kw):
        if sql.strip().upper().startswith(("CREATE", "PRAGMA")):
            return super().sql(sql, args, **kw)
        self.attempts += 1
        if self.attempts > self.refuse_after:
            return {"error": "queue is full (100 pending)"}
        return super().sql(sql, args, **kw)


# ── the schema belongs to snapshot_service, not to this store ────────────────

class TestSchemaIsDeclaredNotCreated:
    """A bare CREATE TABLE in a store is the pattern that caused the outage:
    every arm of the batched machine read shares ONE statement, so a table or
    column LabCore has not got fails the whole read and drops the floor to the
    fallback path. New tables go in snapshot_service's central DDL tuple."""

    def test_the_store_never_issues_ddl(self):
        """Every assertion about the work is here on purpose: a store that had
        been gutted — every method a `pass` — would also issue no DDL, so
        "no CREATE" alone proves nothing. This walks the whole life of a level
        and checks the result of each step, THEN checks how it was done."""
        gw = CountingGateway()
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        gw.writes.clear()
        store = LevelStore(gw)
        level = store.create("Ground")
        assert [l.uid for l in store.levels()] == [level.uid]
        store.assign("m1", level.uid)
        assert store.assignments() == {"m1": level.uid}
        store.set_default_level(level.uid)
        assert store.stored_default_uid() == level.uid
        store.rename(level.uid, "Ground Floor")
        assert store.get(level.uid).name == "Ground Floor"
        store.delete(level.uid)
        assert store.levels() == [] and store.assignments() == {}
        assert gw.writes, "the store did the work without writing anything"
        forbidden = ("CREATE ", "ALTER ", "DROP ")
        assert not [w for w in gw.writes
                    if any(k in w.upper() for k in forbidden)]

    def test_the_ddl_parses_the_way_snapshot_service_parses_it(self):
        """snapshot_service.ensure_schema pulls the table name out with
        `ddl.split("IF NOT EXISTS", 1)[1].split("(", 1)[0].strip()`, so a DDL
        written any other way is declared but never skipped — and worse, never
        matched against what already exists."""
        names = [ddl.split("IF NOT EXISTS", 1)[1].split("(", 1)[0].strip()
                 for ddl in levels_mod.SCHEMA_DDL]
        assert names == ["lem_levels", "lem_machine_level", "lem_level_settings"]

    def test_every_arm_is_exactly_as_wide_as_the_ones_already_shipping(self):
        """Measured against `snapshot_service._ARMS`, never against a number
        typed in here. The failure RELEASING.md names is an arm of the WRONG
        WIDTH: every arm shares one statement, so nine columns where the rest
        have ten fails the entire read and drops the whole floor to the
        fallback path. A hardcoded `range(1, 10)` agrees with itself forever
        while the real arms grow an eleventh column."""
        reference = _alias_indices(snapshot_service._ARMS[0][1])
        assert reference, "the reference arm has no aliases to compare against"
        for name, sql in levels_mod.SNAPSHOT_ARMS:
            assert _alias_indices(sql) == reference, name

    def test_every_arm_names_its_own_src(self):
        """Each arm is also run ON ITS OWN in the fallback path, where an
        unaliased column comes back named after its expression and the parser
        finds no `src` at all. That silently emptied the floor's layout once."""
        for name, sql in levels_mod.SNAPSHOT_ARMS:
            assert f"'{name}' AS src" in sql

    def test_the_snapshot_arms_actually_run(self, gw):
        for _name, sql in levels_mod.SNAPSHOT_ARMS:
            assert not gw.read_sql(sql).get("error")

    def test_the_arms_union_with_the_ones_already_shipping(self, gw):
        """The real statement, not a rehearsal of it. This is the one that
        catches a wrong-width or wrong-typed arm the way production would —
        by failing the whole read — while a per-arm test would pass."""
        for ddl in snapshot_service.SCHEMA_DDL:
            gw.sql(ddl)
        res = gw.read_sql(_batched(snapshot_service._ARMS
                                   + levels_mod.SNAPSHOT_ARMS))
        assert not res.get("error"), res.get("error")

    def test_no_existing_table_is_touched(self):
        """RELEASING.md §2: a new or renamed lem_* column is a MAJOR release,
        because the station module on every bench reads these tables. Levels
        living entirely in their own tables is what makes this a MINOR."""
        blob = " ".join(levels_mod.SCHEMA_DDL)
        for existing in ("lem_machine_status", "lem_machine_layout",
                         "lem_machine_targets", "lem_qc_specs",
                         "lem_machine_specs", "lem_map_settings"):
            assert existing not in blob

    def test_machine_uid_is_the_wire_contract(self):
        """Benches key their lem_* rows on machine_uid and POST /api/live with
        it. LabCore has no foreign keys, so renaming it would not error — it
        would silently orphan every row forever."""
        assert "machine_uid TEXT" in levels_mod.LEVEL_ASSIGNMENT_DDL


# ── the lab as it stands today: no levels at all ─────────────────────────────

class TestALabWithNoLevels:
    """Nothing here has been configured, and nothing may disappear because of
    it. This is the state of the live floor right now."""

    def test_there_are_no_levels(self, store):
        assert store.levels() == []

    def test_there_are_no_assignments(self, store):
        assert store.assignments() == {}

    def test_every_instrument_still_has_a_place(self, store):
        fleet = ["m1", "m2", "m3"]
        placed = placements(fleet, store.assignments(), store.levels())
        assert placed == {"m1": "", "m2": "", "m3": ""}

    def test_every_instrument_still_shows_on_the_floor(self, store):
        fleet = ["m1", "m2", "m3"]
        placed = placements(fleet, store.assignments(), store.levels())
        assert machines_on("", placed) == ["m1", "m2", "m3"]

    def test_there_is_no_default_level_to_resolve(self, store):
        assert store.default_level_uid() == ""

    def test_moving_up_does_nothing_rather_than_inventing_a_level(self, store):
        assert store.move_up("m1") == ""
        assert store.assignments() == {}

    def test_cycling_has_nothing_to_cycle_through(self, store):
        assert cycle("", [], 1) == ""

    def test_the_tables_not_existing_yet_reads_as_no_levels(self, bare):
        """Before the single-writer phase registers the DDL — or on a LabCore
        that has never seen this release — the read comes back an error dict.
        An empty floor would be a lie; no levels is the truth."""
        store = LevelStore(bare)
        assert store.levels() == []
        assert store.assignments() == {}
        assert store.default_level_uid() == ""
        assert store.level_of("m1") == ""


# ── create, rename, delete ───────────────────────────────────────────────────

class TestCreateRenameDelete:
    def test_create_returns_the_level(self, store):
        level = store.create("Ground")
        assert isinstance(level, Level)
        assert level.name == "Ground"
        assert level.uid

    def test_a_created_level_is_read_back(self, store):
        store.create("Ground")
        assert [l.name for l in store.levels()] == ["Ground"]

    def test_names_are_trimmed(self, store):
        assert store.create("  Mezzanine  ").name == "Mezzanine"

    def test_a_level_needs_a_name(self, store):
        with pytest.raises(ValueError):
            store.create("   ")

    def test_two_levels_may_not_share_a_name(self, store):
        """The name is what a person cycles through. Two "Second Floor"s is a
        mistake at the keyboard, never a plan."""
        store.create("Second")
        with pytest.raises(ValueError):
            store.create("second")

    def test_rename(self, store):
        level = store.create("Grond")
        store.rename(level.uid, "Ground")
        assert store.get(level.uid).name == "Ground"

    def test_rename_keeps_the_uid_so_nothing_is_orphaned(self, store):
        level = store.create("Grond")
        store.assign("m1", level.uid)
        store.rename(level.uid, "Ground")
        assert store.level_of("m1") == level.uid

    def test_rename_keeps_the_rank(self, store):
        store.create("Ground")
        second = store.create("Second")
        store.rename(second.uid, "Upstairs")
        assert [l.name for l in store.levels()] == ["Ground", "Upstairs"]

    def test_rename_refuses_a_blank_name(self, store):
        level = store.create("Ground")
        with pytest.raises(ValueError):
            store.rename(level.uid, "  ")

    def test_rename_refuses_a_name_another_level_already_has(self, store):
        store.create("Ground")
        second = store.create("Second")
        with pytest.raises(ValueError):
            store.rename(second.uid, "Ground")

    def test_renaming_a_level_to_its_own_name_is_allowed(self, store):
        level = store.create("Ground")
        store.rename(level.uid, "Ground ")
        assert store.get(level.uid).name == "Ground"

    def test_rename_of_a_level_that_is_gone(self, store):
        with pytest.raises(ValueError):
            store.rename("nope", "Ground")

    def test_delete(self, store):
        level = store.create("Ground")
        store.delete(level.uid)
        assert store.levels() == []

    def test_delete_is_idempotent(self, store):
        """Two people on two screens pressing delete is not an error worth
        showing either of them."""
        level = store.create("Ground")
        store.delete(level.uid)
        store.delete(level.uid)
        assert store.levels() == []

    def test_get_of_an_unknown_uid_is_none(self, store):
        assert store.get("nope") is None


# ── the order: integer rank, and what a collision does ───────────────────────

class TestTheLadder:
    """Up and down mean nothing without an order. Rank is an integer, ascending
    from the ground, and it is a SORT HINT rather than an identity — see
    test_two_levels_colliding_on_a_rank."""

    def test_new_levels_stack_on_top_in_creation_order(self, store):
        store.create("Ground")
        store.create("Second")
        store.create("Third")
        assert [l.name for l in store.levels()] == ["Ground", "Second", "Third"]

    def test_rank_counts_up_from_the_ground(self, store):
        store.create("Ground")
        store.create("Second")
        assert [l.rank for l in store.levels()] == [0, 1]

    def test_a_level_can_be_created_in_the_middle(self, store):
        store.create("Ground")
        store.create("Third")
        store.create("Mezzanine", rank=1)
        assert [l.name for l in store.levels()] == ["Ground", "Mezzanine", "Third"]

    def test_inserting_in_the_middle_leaves_distinct_ranks(self, store):
        store.create("Ground")
        store.create("Third")
        store.create("Mezzanine", rank=1)
        ranks = [l.rank for l in store.levels()]
        assert len(set(ranks)) == len(ranks)

    def test_a_rank_past_the_top_lands_on_top(self, store):
        store.create("Ground")
        assert store.create("Sky", rank=99).rank == 1

    def test_a_negative_rank_lands_on_the_ground(self, store):
        store.create("Second")
        store.create("Basement", rank=-5)
        assert [l.name for l in store.levels()] == ["Basement", "Second"]

    def test_deleting_from_the_middle_keeps_the_order(self, store):
        """Deleting leaves a gap in the numbers on purpose: closing it would
        cost one queued write per level above, and order by rank does not care
        about gaps. The ladder is what matters, not the arithmetic."""
        store.create("Ground")
        mid = store.create("Second")
        store.create("Third")
        store.delete(mid.uid)
        assert [l.name for l in store.levels()] == ["Ground", "Third"]

    def test_two_levels_colliding_on_a_rank_still_have_one_order(self, gw, store):
        """Nothing prevents a collision — two clients creating at once, or a
        row edited by hand. The floor relayouts every two seconds, so an order
        that depends on the order rows come back is the "everytime this thing
        refreshes it changes layout" bug again. The tie-break is by name then
        uid, so the answer is the same every read."""
        gw.sql("INSERT INTO lem_levels (uid, name, rank) VALUES ('b','Beta',1)")
        gw.sql("INSERT INTO lem_levels (uid, name, rank) VALUES ('a','Alpha',1)")
        first = [l.uid for l in store.levels()]
        assert first == ["a", "b"]
        assert [l.uid for l in store.levels()] == first

    def test_a_collision_heals_on_the_next_insert(self, gw, store):
        gw.sql("INSERT INTO lem_levels (uid, name, rank) VALUES ('b','Beta',1)")
        gw.sql("INSERT INTO lem_levels (uid, name, rank) VALUES ('a','Alpha',1)")
        store.create("Ground", rank=0)
        ranks = [l.rank for l in store.levels()]
        assert ranks == sorted(set(ranks))

    def test_a_collision_does_not_move_equipment(self, gw, store):
        gw.sql("INSERT INTO lem_levels (uid, name, rank) VALUES ('b','Beta',1)")
        gw.sql("INSERT INTO lem_levels (uid, name, rank) VALUES ('a','Alpha',1)")
        store.assign("m1", "b")
        assert store.level_of("m1") == "b"


# ── the floor-wide default, stored the way `locked` is ───────────────────────

class TestTheDefaultLevel:
    def test_it_round_trips(self, store):
        level = store.create("Ground")
        store.set_default_level(level.uid)
        assert store.default_level_uid() == level.uid

    def test_it_is_one_key_in_a_settings_table(self, gw, store):
        """Same shape as MapSettingsStore's `locked`: one floor-wide switch,
        one row, shared by everyone looking at the map."""
        level = store.create("Ground")
        store.set_default_level(level.uid)
        rows = gw.read_sql("SELECT key, value FROM lem_level_settings")["rows"]
        assert rows == [{"key": DEFAULT_LEVEL_KEY, "value": level.uid}]

    def test_setting_it_twice_upserts(self, gw, store):
        one = store.create("Ground")
        two = store.create("Second")
        store.set_default_level(one.uid)
        store.set_default_level(two.uid)
        assert store.default_level_uid() == two.uid
        assert len(gw.read_sql("SELECT key FROM lem_level_settings")["rows"]) == 1

    def test_with_no_default_set_it_is_the_ground(self, store):
        """A lab that made levels and never opened settings still needs an
        answer, and the bottom of the ladder is the one nobody has to explain."""
        ground = store.create("Ground")
        store.create("Second")
        assert store.default_level_uid() == ground.uid

    def test_a_default_naming_a_deleted_level_falls_back(self, gw, store):
        """No foreign keys, so the row survives the level. Resolving on read is
        what keeps a stale pointer from hiding equipment."""
        ground = store.create("Ground")
        gone = store.create("Attic")
        gw.sql("INSERT INTO lem_level_settings (key, value) VALUES (?, ?)",
               [DEFAULT_LEVEL_KEY, gone.uid])
        gw.sql("DELETE FROM lem_levels WHERE uid = ?", [gone.uid])
        assert store.default_level_uid() == ground.uid

    def test_the_raw_setting_is_readable_too(self, store):
        level = store.create("Ground")
        store.create("Second")
        store.set_default_level(level.uid)
        assert store.stored_default_uid() == level.uid

    def test_clearing_the_default(self, store):
        ground = store.create("Ground")
        store.create("Second")
        store.set_default_level("")
        assert store.stored_default_uid() == ""
        assert store.default_level_uid() == ground.uid

    def test_a_default_must_name_a_real_level(self, store):
        with pytest.raises(ValueError):
            store.set_default_level("nope")

    def test_deleting_the_default_level_clears_the_setting(self, store):
        ground = store.create("Ground")
        second = store.create("Second")
        store.set_default_level(second.uid)
        store.delete(second.uid)
        assert store.stored_default_uid() == ""
        assert store.default_level_uid() == ground.uid


# ── assigning equipment to a level ───────────────────────────────────────────

class TestAssignment:
    def test_assign_and_read_back(self, store):
        level = store.create("Ground")
        store.assign("m1", level.uid)
        assert store.level_of("m1") == level.uid

    def test_an_instrument_stands_on_exactly_one_level(self, store):
        one = store.create("Ground")
        two = store.create("Second")
        store.assign("m1", one.uid)
        store.assign("m1", two.uid)
        assert store.assignments() == {"m1": two.uid}

    def test_assignments_are_scoped_per_instrument(self, store):
        one = store.create("Ground")
        two = store.create("Second")
        store.assign("m1", one.uid)
        store.assign("m2", two.uid)
        assert store.assignments() == {"m1": one.uid, "m2": two.uid}

    def test_unassign(self, store):
        level = store.create("Ground")
        store.assign("m1", level.uid)
        store.unassign("m1")
        assert store.assignments() == {}

    def test_assigning_nothing_is_unassigning(self, store):
        level = store.create("Ground")
        store.assign("m1", level.uid)
        store.assign("m1", "")
        assert store.assignments() == {}

    def test_forget_matches_the_other_stores(self, store):
        """`forget` is what MachineLayoutStore and QcTargetStore call the
        cleanup a deleted machine triggers; levels answer to the same name so
        the delete path does not have to remember a fourth verb."""
        level = store.create("Ground")
        store.assign("m1", level.uid)
        store.forget("m1")
        assert store.assignments() == {}

    def test_an_assignment_must_name_a_real_level(self, store):
        with pytest.raises(ValueError):
            store.assign("m1", "nope")

    def test_a_dangling_assignment_reads_as_unassigned(self, gw, store):
        """Written by an older release, or by a client racing a delete. With no
        foreign keys the row simply stays, and an instrument pointing at a
        level that is not there must fall back to the ground rather than be
        filtered off every level."""
        ground = store.create("Ground")
        gw.sql("INSERT INTO lem_machine_level (machine_uid, level_uid) "
               "VALUES ('m1', 'ghost')")
        placed = placements(["m1"], store.assignments(), store.levels())
        assert placed == {"m1": ground.uid}

    def test_an_instrument_with_no_assignment_stands_on_the_ground(self, store):
        """On the GROUND, not on whatever settings currently says. See
        TestTheDefaultIsAView — this is the same fact from the other side."""
        ground = store.create("Ground")
        second = store.create("Second")
        store.set_default_level(second.uid)
        placed = placements(["m1"], store.assignments(), store.levels())
        assert placed == {"m1": ground.uid}

    def test_every_instrument_lands_somewhere_visible(self, store):
        """The property that matters: whatever the levels and whatever the
        default, no instrument is placed on a level that is not in the list."""
        one = store.create("Ground")
        store.create("Second")
        store.assign("m2", one.uid)
        uids = {l.uid for l in store.levels()}
        placed = placements(["m1", "m2", "m3"], store.assignments(),
                            store.levels())
        assert set(placed) == {"m1", "m2", "m3"}
        assert set(placed.values()) <= uids

    def test_machines_on_a_level(self, store):
        one = store.create("Ground")
        two = store.create("Second")
        store.assign("m1", one.uid)
        store.assign("m2", two.uid)
        placed = placements(["m1", "m2"], store.assignments(), store.levels())
        assert machines_on(one.uid, placed) == ["m1"]
        assert machines_on(two.uid, placed) == ["m2"]


# ── the whole fleet in one query ─────────────────────────────────────────────

class TestOneQueryForTheWholeFleet:
    """The floor payload is rebuilt for every instrument on every poll, and the
    floor polls every 2s. One read per instrument would be paid forever."""

    def test_reading_every_assignment_costs_exactly_one_read(self):
        gw = CountingGateway()
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        store = LevelStore(gw)
        level = store.create("Ground")
        for i in range(50):
            store.assign(f"m{i}", level.uid)
        gw.reads.clear()
        assert len(store.assignments()) == 50
        assert len(gw.reads) == 1

    def test_the_ladder_costs_exactly_one_read(self):
        gw = CountingGateway()
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        store = LevelStore(gw)
        store.create("Ground")
        store.create("Second")
        gw.reads.clear()
        store.levels()
        assert len(gw.reads) == 1

    def test_placement_needs_no_gateway_at_all(self, store):
        """Pure, so the snapshot can place the whole fleet out of rows it has
        already read rather than asking again."""
        level = Level(uid="a", name="Ground", rank=0)
        assert placements(["m1"], {"m1": "a"}, [level]) == {"m1": "a"}


# ── moving one level up, one level down ──────────────────────────────────────

class TestMovingEquipment:
    @pytest.fixture
    def ladder(self, store):
        return [store.create("Ground"), store.create("Second"),
                store.create("Third")]

    def test_up_one(self, store, ladder):
        store.assign("m1", ladder[0].uid)
        assert store.move_up("m1") == ladder[1].uid
        assert store.level_of("m1") == ladder[1].uid

    def test_down_one(self, store, ladder):
        store.assign("m1", ladder[2].uid)
        assert store.move_down("m1") == ladder[1].uid

    def test_up_at_the_top_stays_put(self, store, ladder):
        """Clamped, not wrapped: pressing up on the top floor must not drop an
        instrument into the basement, which is what a wrap looks like to the
        person holding the mouse."""
        store.assign("m1", ladder[2].uid)
        assert store.move_up("m1") == ladder[2].uid
        assert store.level_of("m1") == ladder[2].uid

    def test_down_at_the_bottom_stays_put(self, store, ladder):
        store.assign("m1", ladder[0].uid)
        assert store.move_down("m1") == ladder[0].uid

    def test_moving_an_unassigned_instrument_starts_from_the_ground(
            self, store, ladder):
        """Up from nowhere is meaningless, so "nowhere" is read as the level it
        is already being drawn on — and `placements` draws it on the GROUND.

        Starting from the settings default instead is the blocker wearing a
        different hat: the operator sees the instrument on Ground, presses up
        once, and it lands two floors above where they were looking because
        somebody else moved a drop-down in settings last week.
        """
        store.set_default_level(ladder[2].uid)
        assert store.move_up("m1") == ladder[1].uid
        assert store.level_of("m1") == ladder[1].uid

    def test_a_no_op_move_writes_nothing(self):
        """Counted, not inferred. The old version of this test compared a row
        COUNT before and after — which an implementation that re-writes the
        same assignment on every press passes with room to spare, because an
        upsert of an identical row does not change the count. An operator
        leaning on the button must not fill a queue that serialises at 1.5/s."""
        gw = CountingGateway()
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        store = LevelStore(gw)
        ladder = [store.create("Ground"), store.create("Second"),
                  store.create("Third")]
        store.assign("m1", ladder[2].uid)
        gw.writes.clear()
        assert store.move_up("m1") == ladder[2].uid
        assert gw.writes == []

    def test_a_clamped_move_leaves_an_unassigned_instrument_unassigned(
            self, store, ladder):
        """Down from the ground is where an unassigned instrument already is,
        so nothing is written — rather than pinning it to today's ground,
        which is a change nobody asked for."""
        assert store.move_down("m1") == ladder[0].uid
        assert store.assignments() == {}

    def test_moving_off_a_dangling_assignment_recovers(self, gw, store, ladder):
        gw.sql("INSERT INTO lem_machine_level (machine_uid, level_uid) "
               "VALUES ('m1', 'ghost')")
        assert store.move_up("m1") == ladder[1].uid

    def test_move_with_one_level_is_a_no_op(self, store):
        only = store.create("Ground")
        store.assign("m1", only.uid)
        assert store.move_up("m1") == only.uid
        assert store.move_down("m1") == only.uid

    def test_a_bigger_step_is_still_clamped(self, store, ladder):
        store.assign("m1", ladder[0].uid)
        assert store.move("m1", 9) == ladder[2].uid


# ── a level is deleted with equipment standing on it ─────────────────────────

class TestDeletingALevelUnderEquipment:
    """Not an edge case. A level made by mistake is deleted while three
    instruments are on it, and LabCore will not stop it."""

    def test_the_equipment_survives_the_level(self, store):
        ground = store.create("Ground")
        doomed = store.create("Mezzanine")
        store.assign("m1", doomed.uid)
        store.assign("m2", doomed.uid)
        store.delete(doomed.uid)
        placed = placements(["m1", "m2"], store.assignments(), store.levels())
        assert placed == {"m1": ground.uid, "m2": ground.uid}

    def test_the_assignment_rows_go_with_it(self, store):
        """One DELETE, not one per instrument — and leaving them would mean a
        row nothing ever cleans up, pointing at a level nothing can show."""
        doomed = store.create("Mezzanine")
        store.create("Ground")
        store.assign("m1", doomed.uid)
        store.delete(doomed.uid)
        assert store.assignments() == {}

    def test_dropping_the_last_level_returns_the_floor_to_flat(self, store):
        only = store.create("Ground")
        store.assign("m1", only.uid)
        store.delete(only.uid)
        placed = placements(["m1"], store.assignments(), store.levels())
        assert placed == {"m1": ""}
        assert machines_on("", placed) == ["m1"]

    def test_deleting_one_level_leaves_the_others_alone(self, store):
        ground = store.create("Ground")
        doomed = store.create("Mezzanine")
        store.assign("m1", ground.uid)
        store.assign("m2", doomed.uid)
        store.delete(doomed.uid)
        assert store.assignments() == {"m1": ground.uid}


# ── cycling the view ─────────────────────────────────────────────────────────

class TestCyclingTheView:
    """Ryan: "in the UI you can cycle through them". The view wraps where
    equipment clamps — a viewer pressing past the top expects the ground
    again, an instrument pressing past the top expects to stay where it is."""

    def test_forward(self, store):
        a, b = store.create("Ground"), store.create("Second")
        assert cycle(a.uid, store.levels(), 1) == b.uid

    def test_forward_wraps(self, store):
        a, b = store.create("Ground"), store.create("Second")
        assert cycle(b.uid, store.levels(), 1) == a.uid

    def test_backward_wraps(self, store):
        a, b = store.create("Ground"), store.create("Second")
        assert cycle(a.uid, store.levels(), -1) == b.uid

    def test_from_nothing_selected(self, store):
        a = store.create("Ground")
        store.create("Second")
        assert cycle("", store.levels(), 1) == a.uid

    def test_from_a_level_that_was_deleted_under_the_viewer(self, store):
        a = store.create("Ground")
        assert cycle("ghost", store.levels(), 1) == a.uid


# ── resolve_default, on its own ──────────────────────────────────────────────

class TestResolveDefault:
    def test_an_existing_uid_is_kept(self):
        levels = [Level("a", "Ground", 0), Level("b", "Second", 1)]
        assert resolve_default(levels, "b") == "b"

    def test_a_stale_uid_falls_to_the_ground(self):
        levels = [Level("a", "Ground", 0), Level("b", "Second", 1)]
        assert resolve_default(levels, "ghost") == "a"

    def test_no_levels_resolves_to_nothing(self):
        assert resolve_default([], "a") == ""

    def test_it_is_idempotent(self):
        """Callers hold a resolved uid as often as a stored one; passing either
        back in has to give the same answer."""
        levels = [Level("a", "Ground", 0)]
        once = resolve_default(levels, "")
        assert resolve_default(levels, once) == once


# ── what a refused write does ────────────────────────────────────────────────

class TestWhenLabCoreRefuses:
    """The write queue serialises at roughly 1.5 ops/sec and refuses past 100
    pending, returning an error dict rather than raising. A store that ignores
    that reports a level it never made."""

    @pytest.fixture
    def refusing(self):
        gw = RefusingGateway(refuse_after=0)
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        return gw

    def test_a_refused_create_is_reported(self, refusing):
        with pytest.raises(LabCoreRefused):
            LevelStore(refusing).create("Ground")

    def test_a_refused_assign_is_reported(self):
        gw = RefusingGateway(refuse_after=1)
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        store = LevelStore(gw)
        level = store.create("Ground")
        with pytest.raises(LabCoreRefused):
            store.assign("m1", level.uid)

    def test_a_refused_default_is_reported(self):
        gw = RefusingGateway(refuse_after=1)
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        store = LevelStore(gw)
        level = store.create("Ground")
        with pytest.raises(LabCoreRefused):
            store.set_default_level(level.uid)

    def test_a_refused_rename_is_reported_and_does_not_lie(self):
        """The rename is one write, so there is no half-state to survive — but
        the store must not RETURN the new name for a row that still holds the
        old one. `_write` looks at the returned dict, not only at exceptions."""
        gw = RefusingGateway(refuse_after=1)
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        store = LevelStore(gw)
        level = store.create("Grond")
        with pytest.raises(LabCoreRefused):
            store.rename(level.uid, "Ground")
        assert store.get(level.uid).name == "Grond"

    def test_a_refused_unassign_is_reported_and_does_not_lie(self):
        """The one the delete path calls when a machine is removed. Reporting
        an instrument as unassigned while its row survives means the next
        person to create a machine with that uid inherits a level."""
        gw = RefusingGateway(refuse_after=2)
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        store = LevelStore(gw)
        level = store.create("Ground")
        store.assign("m1", level.uid)
        with pytest.raises(LabCoreRefused):
            store.unassign("m1")
        assert store.level_of("m1") == level.uid

    def test_a_refused_forget_is_reported_too(self):
        """`forget` is an alias, and an alias that swallowed the error would be
        a second, quieter unassign."""
        gw = RefusingGateway(refuse_after=2)
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        store = LevelStore(gw)
        level = store.create("Ground")
        store.assign("m1", level.uid)
        with pytest.raises(LabCoreRefused):
            store.forget("m1")

    def test_a_refused_clear_of_the_default_is_reported(self):
        """Clearing takes the DELETE branch of `set_default_level`, which is a
        different write from the upsert and needs its own check."""
        gw = RefusingGateway(refuse_after=2)
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        store = LevelStore(gw)
        level = store.create("Ground")
        store.set_default_level(level.uid)
        with pytest.raises(LabCoreRefused):
            store.set_default_level("")
        assert store.stored_default_uid() == level.uid

    def test_a_refused_renumber_still_leaves_one_stable_order(self):
        """The renumber pass is the one bounded loop here — at most one write
        per level, and levels are floors of a building. It is deliberately
        best-effort: if the queue refuses halfway, two levels share a rank, the
        name-then-uid tie-break still gives one order every read, and the next
        successful insert renumbers them apart."""
        gw = RefusingGateway(refuse_after=99)
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        store = LevelStore(gw)
        store.create("Ground")
        store.create("Third")
        gw.attempts, gw.refuse_after = 0, 1     # the insert lands, the shuffle does not
        store.create("Mezzanine", rank=1)
        names = [l.name for l in store.levels()]
        assert names == [l.name for l in store.levels()]
        assert set(names) == {"Ground", "Third", "Mezzanine"}

    def test_a_renumber_is_bounded_by_the_number_of_levels(self):
        gw = CountingGateway()
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        store = LevelStore(gw)
        for name in ("Ground", "Second", "Third", "Fourth"):
            store.create(name)
        gw.writes.clear()
        store.create("Mezzanine", rank=1)
        assert len(gw.writes) <= 5


# ── the default level is a VIEW, never a placement ───────────────────────────

class TestTheDefaultIsAView:
    """The blocker this file exists to hold shut.

    Ryan asked for "in the settings also a default level", so the UI WILL put
    this on a settings page as one drop-down. If that drop-down also decides
    where every unplaced instrument is DRAWN, then one person picking "Second"
    in settings moves the entire fleet up a floor for everybody — with
    `SELECT COUNT(*) FROM lem_machine_level` still zero. Nothing was moved.
    Nothing was recorded. Nobody can find out who did it or undo it per
    instrument. The map simply says something different tomorrow.

    So the fact is split in two:

    * `default_level_uid()` — what the picker OPENS on and what the
      create-equipment dialog PRESELECTS. A preference.
    * `unplaced_level_uid()` — where an instrument nobody has placed is drawn.
      The ground, always, and no setting reaches it.
    """

    def test_placements_cannot_be_handed_the_settings_default(self):
        """Structural, and deliberately so. A behavioural test only catches the
        callers that exist today; removing the parameter is what stops the
        NEXT caller from passing `stored_default_uid()` into it — which is
        exactly how this bug arrived, as a helpful-looking fourth argument."""
        with pytest.raises(TypeError):
            placements(["m1"], {}, [Level("a", "Ground", 0)], "b")

    def test_the_unplaced_level_is_the_ground(self, store):
        ground = store.create("Ground")
        store.create("Second")
        assert store.unplaced_level_uid() == ground.uid

    def test_setting_the_default_never_changes_the_unplaced_level(self, store):
        ground = store.create("Ground")
        second = store.create("Second")
        assert store.unplaced_level_uid() == ground.uid
        store.set_default_level(second.uid)
        assert store.unplaced_level_uid() == ground.uid
        assert store.default_level_uid() == second.uid

    def test_setting_the_default_moves_no_instrument(self, store):
        """The critic's reproduction, kept: two levels, five unassigned
        instruments, the default flipped to Second and back. Not one of the
        five may move, and `lem_machine_level` must stay empty throughout."""
        ground = store.create("Ground")
        second = store.create("Second")
        fleet = [f"m{i}" for i in range(5)]

        def drawn():
            return placements(fleet, store.assignments(), store.levels())

        before = drawn()
        assert before == {m: ground.uid for m in fleet}
        store.set_default_level(second.uid)
        assert drawn() == before
        store.set_default_level(ground.uid)
        assert drawn() == before
        store.set_default_level("")
        assert drawn() == before
        assert store.assignments() == {}

    def test_deleting_the_default_level_moves_no_instrument(self, store):
        """Same jump by the other route: with the default doing double duty,
        deleting the level it named re-resolved the fallback and every
        unassigned instrument on the floor changed places."""
        ground = store.create("Ground")
        second = store.create("Second")
        store.set_default_level(second.uid)
        fleet = ["m1", "m2", "m3"]
        before = placements(fleet, store.assignments(), store.levels())
        store.delete(second.uid)
        after = placements(fleet, store.assignments(), store.levels())
        assert before == after == {m: ground.uid for m in fleet}

    def test_a_default_pointing_at_a_deleted_level_still_resolves(self, store):
        """`resolve_default` keeps its job — it is the VIEW default, and a
        dangling one has to land on something the picker can open on."""
        ground = store.create("Ground")
        assert resolve_default(store.levels(), "ghost") == ground.uid

    def test_the_ground_is_the_bottom_of_the_ladder_not_the_first_row(self, store):
        """`ground_level_uid` sorts. A basement created after the ground floor
        comes back later from LabCore but is still the ground."""
        store.create("Second")
        basement = store.create("Basement", rank=0)
        assert ground_level_uid(store.levels()) == basement.uid
        assert store.unplaced_level_uid() == basement.uid

    def test_with_no_levels_there_is_no_ground(self, store):
        assert ground_level_uid([]) == ""
        assert store.unplaced_level_uid() == ""


# ── the snapshot arms are usable, not just present ───────────────────────────

class TestTheSnapshotArmsParseBack:
    """Arms without a parser are decoration: the only working API left is the
    store, which is 3 LabCore reads on every floor poll — against the zero-op
    rule that is the whole reason snapshot_service exists. These prove the
    round trip, so the floor can place the fleet out of rows it already has."""

    @pytest.fixture
    def loaded(self, gw, store):
        ground = store.create("Ground")
        second = store.create("Second")
        store.assign("m1", second.uid)
        store.assign("m2", ground.uid)
        store.set_default_level(second.uid)
        return ground, second

    def _tables(self, gw):
        rows = gw.read_sql(_batched(levels_mod.SNAPSHOT_ARMS))["rows"]
        return snapshot_service.split_batched(rows)

    def test_a_batched_result_splits_back_into_all_three_facts(self, gw, store,
                                                               loaded):
        tables = self._tables(gw)
        assert levels_from_tables(tables) == store.levels()
        assert assignments_from_tables(tables) == store.assignments()
        assert default_level_from_tables(tables) == store.stored_default_uid()

    def test_the_rank_survives_the_cast_to_text(self, gw, store, loaded):
        """The arm ships `CAST(rank AS TEXT)`, because a UNION column has one
        type across every arm. A parser that forgot to cast back would sort
        "10" before "2" and put the tenth floor under the second."""
        store.create("Tenth", rank=9)
        parsed = levels_from_tables(self._tables(gw))
        assert [l.rank for l in parsed] == [l.rank for l in store.levels()]
        assert all(isinstance(l.rank, int) for l in parsed)

    def test_ten_levels_sort_by_number_not_by_string(self, gw, store):
        for i in range(11):
            store.create(f"L{i}")
        parsed = levels_from_tables(self._tables(gw))
        assert [l.rank for l in parsed] == list(range(11))

    def test_each_arm_also_parses_on_its_own_in_the_fallback_path(self, gw,
                                                                  store, loaded):
        """When the UNION is rejected, snapshot_service runs each arm alone.
        Whatever comes back must still carry `src`, or the parser sees nothing
        and the floor goes flat without saying so."""
        for name, sql in levels_mod.SNAPSHOT_ARMS:
            tables = snapshot_service.split_batched(gw.read_sql(sql)["rows"])
            assert set(tables) == {name}, name

    def test_a_floor_poll_places_the_whole_fleet_with_no_further_reads(self):
        """The point of all of it: one batched read, then the placement of
        every instrument on the floor without touching LabCore again."""
        gw = CountingGateway()
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        store = LevelStore(gw)
        ground = store.create("Ground")
        second = store.create("Second")
        store.assign("m2", second.uid)
        rows = gw.read_sql(_batched(levels_mod.SNAPSHOT_ARMS))["rows"]
        gw.reads.clear()
        tables = snapshot_service.split_batched(rows)
        placed = placements(["m1", "m2", "m3"],
                            assignments_from_tables(tables),
                            levels_from_tables(tables))
        assert placed == {"m1": ground.uid, "m2": second.uid,
                          "m3": ground.uid}
        assert gw.reads == []

    def test_the_parsers_survive_a_lab_with_no_levels(self):
        """The live floor today. Empty tables, empty arms, and every parser
        has to answer "nothing" rather than raise on a missing key."""
        tables = {}
        assert levels_from_tables(tables) == []
        assert assignments_from_tables(tables) == {}
        assert default_level_from_tables(tables) == ""

    def test_an_unparseable_rank_does_not_lose_the_level(self, gw, store):
        """Hand-edited rows exist. A level whose rank is nonsense belongs on
        the ground, not off the map — the same rule the store's read follows,
        because two readings of one table is how a lab gets two answers."""
        gw.sql("INSERT INTO lem_levels (uid, name, rank) VALUES ('x','Odd','?')")
        parsed = levels_from_tables(self._tables(gw))
        assert [l.uid for l in parsed] == [l.uid for l in store.levels()]
        assert parsed[0].rank == 0

    def test_a_settings_row_that_is_not_the_default_is_ignored(self, gw, store):
        """`lem_level_settings` is a key/value table and will hold more than
        one switch before long. Reading the first row would make the default
        whatever was inserted first."""
        level = store.create("Ground")
        gw.sql("INSERT INTO lem_level_settings (key, value) VALUES (?, ?)",
               ["aardvark", "not-a-level"])
        store.set_default_level(level.uid)
        assert default_level_from_tables(self._tables(gw)) == level.uid


# ── a refused write must never be reported as done ───────────────────────────

class TestARefusedDeleteLeavesTheMapRight:
    """`delete` is the only operation here that needs TWO writes, so it is the
    only one that can half-happen. The queue refuses past 100 pending by
    RETURNING an error dict, so the second write failing is an ordinary
    Tuesday, not a disaster scenario."""

    @staticmethod
    def _loaded(refuse_after=99):
        gw = RefusingGateway(refuse_after=refuse_after)
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        store = LevelStore(gw)
        ground = store.create("Ground")
        doomed = store.create("Mezzanine")
        store.assign("m1", doomed.uid)
        store.assign("m2", doomed.uid)
        return gw, store, ground, doomed

    def test_the_level_goes_first_so_a_refusal_is_survivable(self):
        """Assignments-first is unrecoverable: the placements are gone
        permanently and the level is still on the map, so the operator presses
        delete again and it does nothing they can see. Level-first fails the
        other way — the level is off the map, the equipment is on the ground
        via the dangling-assignment fallback, and the leftover rows are
        cleaned up by pressing delete again."""
        gw, store, ground, doomed = self._loaded()
        gw.attempts, gw.refuse_after = 0, 1   # the first write lands, not the second
        with pytest.raises(LabCoreRefused):
            store.delete(doomed.uid)
        assert [l.uid for l in store.levels()] == [ground.uid]
        placed = placements(["m1", "m2"], store.assignments(), store.levels())
        assert placed == {"m1": ground.uid, "m2": ground.uid}

    def test_running_the_delete_again_clears_the_leftover_rows(self):
        gw, store, ground, doomed = self._loaded()
        gw.attempts, gw.refuse_after = 0, 1
        with pytest.raises(LabCoreRefused):
            store.delete(doomed.uid)
        gw.attempts, gw.refuse_after = 0, 99
        store.delete(doomed.uid)
        assert store.assignments() == {}
        assert [l.uid for l in store.levels()] == [ground.uid]

    def test_a_wholly_refused_delete_changes_nothing(self):
        """Nothing landed, so nothing may look like it did — the level is
        still there and so is every instrument on it."""
        gw, store, ground, doomed = self._loaded()
        gw.attempts, gw.refuse_after = 0, 0
        with pytest.raises(LabCoreRefused):
            store.delete(doomed.uid)
        assert {l.uid for l in store.levels()} == {ground.uid, doomed.uid}
        assert store.assignments() == {"m1": doomed.uid, "m2": doomed.uid}


# ── a LabCore blip is not an empty floor ─────────────────────────────────────

class BlipGateway(FakeLabCoreGateway):
    """LabCore is up and simply not answering in time.

    `snapshot_service.READ_TIMEOUT` documents this as routine — `read_sql` POSTs
    to the same queue every write in the lab is waiting in, and the batched read
    timed out at exactly 8.00s four times in six while six modules were
    publishing. It arrives as an error DICT, not an exception, which is what
    makes swallowing it so easy and so expensive.
    """

    def __init__(self) -> None:
        super().__init__()
        self.blip = False

    def read_sql(self, sql, args=None, **kw):
        if self.blip:
            return {"error": "HTTPSConnectionPool(host='labvision.asaplabs.net',"
                             " port=443): Read timed out. (read timeout=8)"}
        return super().read_sql(sql, args, **kw)


class TestABlipIsNotAnEmptyFloor:
    """The blocker, reproduced end to end.

    A read error used to become `[]`, and every consequence of that was a
    confident lie told to somebody standing in the lab:

      * the level picker emptied,
      * `placements` put the entire fleet on "" — the flat floor — so the level
        the operator was looking at drew ZERO instruments,
      * and `assign` said "That level is no longer there" about a level that is
        perfectly fine, because the check that validates it read `[]`.

    Empty must mean empty. `labcore_result.rows` is the one rule now, and the
    only error it is allowed to swallow is a table that does not exist.
    """

    @pytest.fixture
    def blipping(self):
        gw = BlipGateway()
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        return gw

    def test_the_ladder_says_it_could_not_be_read(self, blipping):
        store = LevelStore(blipping)
        store.create("Ground")
        blipping.blip = True
        with pytest.raises(LabCoreUnavailable):
            store.levels()

    def test_the_reason_survives_for_whoever_is_staring_at_the_screen(
            self, blipping):
        store = LevelStore(blipping)
        blipping.blip = True
        with pytest.raises(LabCoreUnavailable) as caught:
            store.levels()
        assert "Read timed out" in str(caught.value)

    def test_the_assignments_say_it_too(self, blipping):
        store = LevelStore(blipping)
        blipping.blip = True
        with pytest.raises(LabCoreUnavailable):
            store.assignments()

    def test_one_instrument_is_not_reported_as_unplaced(self, blipping):
        store = LevelStore(blipping)
        level = store.create("Ground")
        store.assign("m1", level.uid)
        blipping.blip = True
        with pytest.raises(LabCoreUnavailable):
            store.level_of("m1")

    def test_the_floor_does_not_draw_an_empty_level(self, blipping):
        """The exact chain from the review: two levels, two instruments, then a
        timeout. The old code answered `machines_on(second, ...) == []` — an
        operator looking at Second saw an empty room and every instrument on it
        redrawn on the ground."""
        store = LevelStore(blipping)
        ground = store.create("Ground")
        second = store.create("Second")
        store.assign("m1", second.uid)
        placed = placements(["m1", "m2"], store.assignments(), store.levels())
        assert machines_on(second.uid, placed) == ["m1"]
        assert placed["m2"] == ground.uid

        blipping.blip = True
        with pytest.raises(LabCoreUnavailable):
            placements(["m1", "m2"], store.assignments(), store.levels())

    def test_assigning_during_a_blip_does_not_blame_the_level(self, blipping):
        """The sharpest one. `assign` validates the target against the ladder,
        so a swallowed read turned a routine timeout into "That level is no
        longer there" — a lie about the data, told with total confidence, about
        a level still sitting in LabCore."""
        store = LevelStore(blipping)
        level = store.create("Ground")
        blipping.blip = True
        with pytest.raises(LabCoreUnavailable):
            store.assign("m1", level.uid)

    def test_the_default_is_not_silently_reset(self, blipping):
        store = LevelStore(blipping)
        level = store.create("Ground")
        store.set_default_level(level.uid)
        blipping.blip = True
        with pytest.raises(LabCoreUnavailable):
            store.default_level_uid()
        with pytest.raises(LabCoreUnavailable):
            store.stored_default_uid()

    def test_moving_during_a_blip_refuses_rather_than_guessing(self, blipping):
        store = LevelStore(blipping)
        store.create("Ground")
        store.create("Second")
        blipping.blip = True
        with pytest.raises(LabCoreUnavailable):
            store.move_up("m1")

    def test_creating_during_a_blip_does_not_skip_the_duplicate_check(
            self, blipping):
        """`create` refuses a name another level already has, and it learns the
        names from a read. A swallowed read makes every name unique."""
        store = LevelStore(blipping)
        store.create("Ground")
        blipping.blip = True
        with pytest.raises(LabCoreUnavailable):
            store.create("Ground")


class TestAMissingTableIsTheOtherFact:
    """The ONE error a read may honestly turn into "nothing".

    Every lem_* table is created centrally at boot, so a read that lands before
    that has run really is looking at nothing. That is the state of the live
    floor today — the wiring in this module's docstring has not landed — and the
    floor must draw exactly as it always has.

    But it is not a blanket rule, and the split is per path:

      * a path that DRAWS may degrade to empty. No levels is the truth.
      * a path that is about to WRITE may not. A create, an assign or a rename
        that reads `[]` from a table that was never created goes on to validate
        against nothing and then writes into nothing; "the schema is missing" is
        the news there, not a shrug.
    """

    def test_drawing_still_works_before_the_schema_lands(self, bare):
        store = LevelStore(bare)
        assert store.levels() == []
        assert store.assignments() == {}
        assert store.default_level_uid() == ""
        assert store.stored_default_uid() == ""
        assert store.level_of("m1") == ""
        placed = placements(["m1", "m2"], store.assignments(), store.levels())
        assert machines_on("", placed) == ["m1", "m2"]

    def test_a_write_path_says_the_schema_is_missing(self, bare):
        store = LevelStore(bare)
        with pytest.raises(LabCoreUnavailable) as caught:
            store.create("Ground")
        assert "no such table" in str(caught.value).lower()

    def test_assigning_before_the_schema_lands_says_why(self, bare):
        """Not "That level is no longer there" — nobody could act on that."""
        store = LevelStore(bare)
        with pytest.raises(LabCoreUnavailable):
            store.assign("m1", "anything")

    def test_renaming_before_the_schema_lands_says_why(self, bare):
        store = LevelStore(bare)
        with pytest.raises(LabCoreUnavailable):
            store.rename("anything", "Ground")

    def test_setting_a_default_before_the_schema_lands_says_why(self, bare):
        store = LevelStore(bare)
        with pytest.raises(LabCoreUnavailable):
            store.set_default_level("anything")

    def test_moving_before_the_schema_lands_says_why(self, bare):
        store = LevelStore(bare)
        with pytest.raises(LabCoreUnavailable):
            store.move_up("m1")


# ── a refusal that carries no error key is still a refusal ───────────────────

class QuietlyRefusingGateway(FakeLabCoreGateway):
    """LabCore's queue past 100 pending, answering the way the REAL client
    reports it: `resp.json()` verbatim, which need not contain an "error" key
    at all. A store that tests `if res.get("error")` calls this a success."""

    def sql(self, sql, args=None, **kw):
        if sql.strip().upper().startswith(("CREATE", "PRAGMA")):
            return super().sql(sql, args, **kw)
        return {"ok": False, "status": "rejected", "pending": 100}


class TestSilenceIsNotSuccess:
    def test_a_refusal_with_no_error_key_is_still_reported(self):
        gw = QuietlyRefusingGateway()
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        with pytest.raises(LabCoreRefused):
            LevelStore(gw).create("Ground")

    def test_and_the_level_really_was_never_made(self):
        gw = QuietlyRefusingGateway()
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        store = LevelStore(gw)
        with pytest.raises(LabCoreRefused):
            store.create("Ground")
        assert store.levels() == []


# ── moving an instrument leaves a record ─────────────────────────────────────

LOG_DDL = next(d for d in snapshot_service.SCHEMA_DDL if "lem_machine_log" in d)


@pytest.fixture
def logged(gw):
    """The levels tables plus `lem_machine_log`, declared the way
    snapshot_service declares it — this module writes history into the log the
    rest of the app already keeps, and creates neither."""
    gw.sql(LOG_DDL)
    return gw


def _log_rows(gw) -> list:
    return gw.read_sql(
        "SELECT machine_uid, ts, kind, test_name, detail FROM lem_machine_log "
        "ORDER BY ts")["rows"]


class TestAMoveLeavesARecord:
    """Every other mutation in this app is traceable, and an instrument that
    changed floors was not: no timestamp, no author, no log line. "Which level
    was the viscometer on in March, and who moved it?" had no answer at all,
    which is the same gap the corrective-action work is closing elsewhere.

    Two records, deliberately, because they answer different questions:

      * `lem_machine_level.moved_at` / `.moved_by` — the CURRENT placement's
        provenance, written in the same statement as the move, so it can never
        disagree with where the instrument is.
      * a `lem_machine_log` row — the HISTORY, next to runs, QC verdicts and
        the config audit, on the page people already read.
    """

    @pytest.fixture
    def ladder(self, logged):
        store = LevelStore(logged)
        return store, [store.create("Ground"), store.create("Second"),
                       store.create("Third")]

    def test_the_placement_carries_who_and_when(self, logged, ladder):
        store, levels = ladder
        store.assign("m1", levels[1].uid, by="ryan")
        row = logged.read_sql(
            "SELECT machine_uid, level_uid, moved_at, moved_by "
            "FROM lem_machine_level")["rows"][0]
        assert row["machine_uid"] == "m1"
        assert row["level_uid"] == levels[1].uid
        assert row["moved_by"] == "ryan"
        datetime.fromisoformat(str(row["moved_at"]))

    def test_a_move_up_carries_it_too(self, logged, ladder):
        store, levels = ladder
        store.assign("m1", levels[0].uid, by="ryan")
        store.move_up("m1", by="sam")
        row = logged.read_sql(
            "SELECT level_uid, moved_by FROM lem_machine_level")["rows"][0]
        assert row["level_uid"] == levels[1].uid
        assert row["moved_by"] == "sam"

    def test_the_move_lands_in_the_machines_own_history(self, logged, ladder):
        store, levels = ladder
        store.assign("m1", levels[0].uid, by="ryan")
        store.move_up("m1", by="sam")
        rows = [r for r in _log_rows(logged) if r["machine_uid"] == "m1"]
        assert len(rows) == 2
        last = rows[-1]
        assert last["kind"] == "config"
        assert last["test_name"] == levels_mod.LEVEL_MOVE_ACTION
        detail = json.loads(last["detail"])
        assert detail["by"] == "sam"
        assert detail["from"] == levels[0].uid
        assert detail["to"] == levels[1].uid
        datetime.fromisoformat(str(last["ts"]))

    def test_the_names_are_recorded_not_only_the_uids(self, logged, ladder):
        """Levels get renamed and deleted, and a history line that reads
        "moved to 4f2c91ab" a year later is not a record of anything."""
        store, levels = ladder
        store.assign("m1", levels[0].uid)
        store.move_up("m1")
        detail = json.loads([r for r in _log_rows(logged)][-1]["detail"])
        assert detail["from_name"] == "Ground"
        assert detail["to_name"] == "Second"

    def test_it_is_shaped_like_every_other_config_audit(self, logged, ladder):
        """web_app's `_audit` writes kind='config', the action in `test_name`
        and a JSON detail carrying `action` and `by`. Matching it exactly means
        the logs page and its filters need no new case."""
        store, levels = ladder
        store.assign("m1", levels[0].uid, by="ryan")
        row = _log_rows(logged)[-1]
        assert row["kind"] == "config"
        detail = json.loads(row["detail"])
        assert detail["action"] == levels_mod.LEVEL_MOVE_ACTION
        assert set(detail) >= {"action", "by", "from", "to"}

    def test_an_unattributed_move_is_still_a_dated_one(self, logged, ladder):
        """No session, no name — a script, or a page that never asked. The time
        is still recorded, and `by` is empty rather than invented."""
        store, levels = ladder
        store.assign("m1", levels[0].uid)
        row = logged.read_sql(
            "SELECT moved_at, moved_by FROM lem_machine_level")["rows"][0]
        assert row["moved_by"] == ""
        datetime.fromisoformat(str(row["moved_at"]))

    def test_taking_an_instrument_off_a_level_is_recorded(self, logged, ladder):
        store, levels = ladder
        store.assign("m1", levels[1].uid, by="ryan")
        store.unassign("m1", by="sam")
        detail = json.loads(_log_rows(logged)[-1]["detail"])
        assert detail["from"] == levels[1].uid
        assert detail["to"] == ""
        assert detail["by"] == "sam"

    def test_forgetting_a_deleted_machine_records_nothing(self, logged, ladder):
        """`forget` runs when the MACHINE is deleted, and web_app deletes that
        machine's log rows in the same breath. A history line about equipment
        that no longer exists is a row nothing will ever read and one more write
        in a queue that serialises at ~1.5 ops/sec."""
        store, levels = ladder
        store.assign("m1", levels[1].uid, by="ryan")
        before = len(_log_rows(logged))
        store.forget("m1")
        assert len(_log_rows(logged)) == before
        assert store.assignments() == {}

    def test_a_no_op_move_records_nothing(self, logged, ladder):
        store, levels = ladder
        store.assign("m1", levels[2].uid, by="ryan")
        before = len(_log_rows(logged))
        assert store.move_up("m1", by="sam") == levels[2].uid
        assert len(_log_rows(logged)) == before

    def test_a_refused_history_line_does_not_lose_the_move(self, ladder,
                                                           logged):
        """The record is best-effort ON PURPOSE, and only the log half of it.
        The provenance lives on the placement row and is written in the SAME
        statement as the move, so it cannot be lost separately. Failing the
        operator's move because the history line was queued behind somebody
        else's work would cost them the thing they actually asked for — the
        rule web_app's `_audit` already states: "an audit failure must not fail
        the change the operator actually asked for"."""
        store, levels = ladder
        logged.sql("DROP TABLE lem_machine_log")
        store.assign("m1", levels[0].uid, by="ryan")
        assert store.level_of("m1") == levels[0].uid
        row = logged.read_sql(
            "SELECT moved_by FROM lem_machine_level")["rows"][0]
        assert row["moved_by"] == "ryan"

    def test_the_provenance_is_readable_before_the_wiring_lands(self, logged,
                                                                ladder):
        """The store is the ONLY working API until the snapshot arms are wired
        in, so a move recorded down a path nothing can read yet would be a fact
        with no reader — an equipment panel able to record a move it could
        never show."""
        store, levels = ladder
        store.assign("m1", levels[1].uid, by="ryan")
        placed = store.placement_of("m1")
        assert placed.level_uid == levels[1].uid
        assert placed.moved_by == "ryan"
        datetime.fromisoformat(placed.moved_at)

    def test_an_instrument_nobody_placed_has_no_placement(self, logged, ladder):
        """`None`, not an empty `Placement`: "never placed" and "placed with no
        stamp" are different facts and the caller has to be able to tell."""
        store, _levels = ladder
        assert store.placement_of("m1") is None

    def test_a_row_written_before_this_shipped_still_reads(self, logged, ladder):
        """No stamps at all — a row from an older release, or one edited by
        hand. It is still a placement; it just has nothing to say about who."""
        store, levels = ladder
        logged.sql("INSERT INTO lem_machine_level (machine_uid, level_uid) "
                   "VALUES (?, ?)", ["m9", levels[1].uid])
        placed = store.placement_of("m9")
        assert placed.level_uid == levels[1].uid
        assert placed.moved_at == "" and placed.moved_by == ""

    def test_the_placement_provenance_is_readable_from_the_snapshot(
            self, logged, ladder):
        """Free with the rows the batched read already fetched — asking LabCore
        "when was this moved" per instrument on a floor that redraws every two
        seconds is the N+1 the snapshot exists to end."""
        store, levels = ladder
        store.assign("m1", levels[1].uid, by="ryan")
        rows = logged.read_sql(_batched(levels_mod.SNAPSHOT_ARMS))["rows"]
        tables = snapshot_service.split_batched(rows)
        moves = levels_mod.moves_from_tables(tables)
        assert moves["m1"].level_uid == levels[1].uid
        assert moves["m1"].moved_by == "ryan"
        datetime.fromisoformat(moves["m1"].moved_at)

    def test_the_snapshot_still_places_the_fleet_the_same_way(self, logged,
                                                              ladder):
        """The extra columns must not disturb the arm everything else reads."""
        store, levels = ladder
        store.assign("m1", levels[1].uid, by="ryan")
        rows = logged.read_sql(_batched(levels_mod.SNAPSHOT_ARMS))["rows"]
        tables = snapshot_service.split_batched(rows)
        assert assignments_from_tables(tables) == store.assignments()


# ── the cost of a move ───────────────────────────────────────────────────────

class TestMovingIsCheap:
    """The two already-cheap read paths have bound tests; `move` did not, and
    it cost THREE reads for one write — the ladder, the current placement, and
    then the ladder AGAIN inside `assign`, to re-validate a uid that had just
    been read out of that same ladder. On the floor's own poll interval that is
    a third of the reads this feature will ever make, spent on nothing."""

    @staticmethod
    def _store():
        gw = CountingGateway()
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        FakeLabCoreGateway.sql(gw, LOG_DDL)
        return gw, LevelStore(gw)

    def test_a_move_costs_two_reads_and_one_placement_write(self):
        gw, store = self._store()
        ladder = [store.create("Ground"), store.create("Second")]
        store.assign("m1", ladder[0].uid)
        gw.reads.clear()
        gw.writes.clear()
        assert store.move_up("m1") == ladder[1].uid
        assert len(gw.reads) == 2, gw.reads
        placements_written = [w for w in gw.writes if "lem_machine_level" in w]
        assert len(placements_written) == 1, gw.writes

    def test_a_move_is_bounded_by_the_ladder_not_by_the_fleet(self):
        """Fifty instruments already placed; moving one still costs the same."""
        gw, store = self._store()
        ladder = [store.create("Ground"), store.create("Second")]
        for i in range(50):
            store.assign(f"m{i}", ladder[0].uid)
        gw.reads.clear()
        store.move_up("m0")
        assert len(gw.reads) == 2, gw.reads


# ── the module is wired: the DDL, the arms and the parsers all landed ────────

class TestTheWiringIsDone:
    """"Declared but inert" and "working" must not look the same.

    This class used to hold the opposite gate: a tripwire asserting the wiring
    had NOT happened, plus a `strict` xfail on the end state. Both fired on the
    commit that wired it — the tripwire failed and the xfail XPASSed — which is
    exactly what they were for, and both were then removed. What is left is the
    end state, asserted plainly.

    `TestTheWiringIsNotDoneYet` is gone on purpose. Do not reinstate it: it now
    describes a lab where the three tables do not exist, and the wider gates on
    the wiring live in `tests/test_equipment_wiring.py`, which holds all three
    stores rather than only this one.
    """

    def test_the_schema_and_the_arms_landed_together(self):
        """The dangerous half. An arm naming a table the boot path does not
        declare fails the ONE statement every other arm shares and drops the
        whole floor to the fallback path."""
        for ddl in levels_mod.SCHEMA_DDL:
            assert ddl in snapshot_service.SCHEMA_DDL
        for arm in levels_mod.SNAPSHOT_ARMS:
            assert arm in snapshot_service._ARMS

    def test_a_wired_snapshot_carries_the_levels(self):
        """The end state, spelled as the thing a user would notice: a level
        made through the store comes back out of the ONE batched read the floor
        already does, with no extra LabCore op at all."""
        gw = FakeLabCoreGateway()
        snapshot_service.SnapshotService(gw).ensure_schema()
        store = LevelStore(gw)
        ground = store.create("Ground")
        store.assign("m1", ground.uid, by="ryan")
        res = gw.read_sql(snapshot_service.batched_machine_sql())
        assert not res.get("error"), res.get("error")
        tables = snapshot_service.split_batched(res.get("rows") or [])
        assert levels_from_tables(tables) == [ground]
        assert assignments_from_tables(tables) == {"m1": ground.uid}


# ── one reading of one row, one reading of one value ─────────────────────────

class TestOneReadingOfOneValue:
    def test_the_row_reader_agrees_with_snapshot_services_own(self):
        """`_f` is copied rather than imported (see the note above it), and a
        copy that drifts is exactly the "two readings of one value" this module
        argues against everywhere else. This is the gate that makes the copy
        safe: SQL NULL, the empty string and 0 must read identically in both."""
        cases = ({"c1": None}, {"c1": ""}, {"c1": 0}, {"c1": "x"}, {})
        for row in cases:
            assert levels_mod._f(row, "c1") == snapshot_service._f(row, "c1")
            assert (levels_mod._f(row, "c1", 0)
                    == snapshot_service._f(row, "c1", 0))


# ── the default level is floor-wide, and that is a departure ─────────────────

class TestTheDefaultIsFloorWideOnPurpose:
    """Ryan: "There is in the settings also a default level." Floor-wide, in
    settings, one drop-down — and his instruction outranks the comparison.

    It IS a departure worth naming, though. Desk- and room-booking products
    keep "default floor" as a per-user preference, because each person there
    has their own desk and their own floor. A lab has one floor plan, and the
    screen that matters most is the wall display: `/floor` takes no login at
    all (`authed()` gates writes, not the map), so "per user" would really mean
    "per browser" — a hidden preference nobody can inspect, that the wall
    display and the phone in your pocket would disagree about, and that no
    settings page could ever set for the lab.

    One shared value it is. These tests hold that shape.
    """

    def test_two_people_setting_it_share_one_row(self, gw, store):
        one = store.create("Ground")
        two = store.create("Second")
        store.set_default_level(one.uid)
        LevelStore(gw).set_default_level(two.uid)
        rows = gw.read_sql("SELECT key, value FROM lem_level_settings")["rows"]
        assert rows == [{"key": DEFAULT_LEVEL_KEY, "value": two.uid}]

    def test_it_is_keyed_by_the_setting_and_nothing_else(self):
        """No user column, no session column. The storage shape is the
        guarantee: a per-user default cannot be smuggled in as a value."""
        assert "key TEXT PRIMARY KEY" in levels_mod.LEVEL_SETTINGS_DDL
        for word in ("user", "session", "browser"):
            assert word not in levels_mod.LEVEL_SETTINGS_DDL.lower()

    def test_it_still_moves_nobody(self, store):
        """The floor-wide default is only louder if it is also harmless: it is
        what the picker OPENS on, never where equipment is DRAWN."""
        ground = store.create("Ground")
        second = store.create("Second")
        store.set_default_level(second.uid)
        assert placements(["m1"], store.assignments(),
                          store.levels()) == {"m1": ground.uid}


# ── a delete that landed is not a failure ────────────────────────────────────

class TestADeleteThatLandedIsNotReportedAsRefused:
    def test_the_cosmetic_settings_clear_does_not_fail_the_delete(self):
        """`delete`'s own comment says the settings clear "costs nothing
        visible" because a default naming a deleted level is resolved on read
        anyway — and then it raised, so the operator was told their delete
        failed about the one write that did not matter. Both real deletes had
        already landed."""
        gw = RefusingGateway(refuse_after=99)
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        store = LevelStore(gw)
        ground = store.create("Ground")
        doomed = store.create("Mezzanine")
        store.assign("m1", doomed.uid)
        store.set_default_level(doomed.uid)
        gw.attempts, gw.refuse_after = 0, 2   # both deletes land, the clear does not
        store.delete(doomed.uid)
        assert [l.uid for l in store.levels()] == [ground.uid]
        assert store.assignments() == {}
        assert store.default_level_uid() == ground.uid

    def test_a_blip_on_the_cosmetic_read_does_not_fail_it_either(self):
        gw = BlipGateway()
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        store = LevelStore(gw)
        doomed = store.create("Mezzanine")

        real_read = gw.read_sql
        seen = {"n": 0}

        def flaky(sql, args=None, **kw):
            if "lem_level_settings" in sql:
                seen["n"] += 1
                return {"error": "Read timed out"}
            return real_read(sql, args, **kw)

        gw.read_sql = flaky
        store.delete(doomed.uid)
        assert seen["n"] == 1
        gw.read_sql = real_read
        assert store.levels() == []


# ── a gateway that raises instead of answering ───────────────────────────────

class ThrowingGateway(FakeLabCoreGateway):
    """The REAL client, not the fake: `requests` raises on a socket error, a
    DNS failure or a server that hung up. The error-dict contract is what
    LabCore sends when it is talking to us at all."""

    def __init__(self, on_read: bool = False, on_write: bool = False) -> None:
        super().__init__()
        self.on_read = on_read
        self.on_write = on_write

    def read_sql(self, sql, args=None, **kw):
        if self.on_read:
            raise OSError("Connection reset by peer")
        return super().read_sql(sql, args, **kw)

    def sql(self, sql, args=None, **kw):
        if self.on_write and not sql.strip().upper().startswith(
                ("CREATE", "PRAGMA")):
            raise OSError("Connection reset by peer")
        return super().sql(sql, args, **kw)


class TestAGatewayThatRaises:
    """Both failure shapes end up in one exception family, so a caller has one
    thing to catch and cannot accidentally handle only the polite one."""

    @staticmethod
    def _gw(**kw):
        gw = ThrowingGateway(**kw)
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        return gw

    def test_a_raising_read_is_the_same_news_as_a_read_error(self):
        with pytest.raises(LabCoreUnavailable) as caught:
            LevelStore(self._gw(on_read=True)).levels()
        assert "Connection reset" in str(caught.value)

    def test_a_raising_write_is_never_reported_as_done(self):
        """`LabCoreRefused` rather than `LabCoreUnavailable`, deliberately. The
        cause is closer to a blip, but the CONSEQUENCE is what the class is for:
        a caller that reads "unavailable" as "try again" would press create
        twice, and `create` mints a fresh uid each time — two levels with the
        same name, which is the one thing `create` refuses to allow."""
        gw = self._gw(on_write=True)
        store = LevelStore(gw)
        with pytest.raises(LabCoreRefused):
            store.create("Ground")
        assert store.levels() == []

    def test_either_way_one_family_catches_it(self):
        from labcore_result import LabCoreError
        with pytest.raises(LabCoreError):
            LevelStore(self._gw(on_read=True)).levels()
        with pytest.raises(LabCoreError):
            LevelStore(self._gw(on_write=True)).create("Ground")


class TestAssigningWhereItAlreadyStandsChangesNothing:
    """Re-posting an equipment edit must not invent a move.

    `assign` had no no-op guard, so standing an instrument on the level it was
    already on still ran `_place`'s upsert — which does
    `SET moved_at=excluded.moved_at, moved_by=excluded.moved_by` — and then
    logged a history line reading "from X to X".

    This needs no LabCore failure to fire. An ordinary save from an equipment
    form that re-posts every field hits it, and the cost is the real record:
    whoever actually moved the instrument is replaced by whoever last pressed
    Save, and the date it actually moved is replaced by today.
    """

    def test_it_writes_nothing_at_all(self):
        gw = CountingGateway()
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        store = LevelStore(gw)
        ground = store.create("Ground")
        store.assign("m1", ground.uid, by="ryan")
        gw.writes.clear()
        store.assign("m1", ground.uid, by="")
        assert gw.writes == []

    def test_the_real_author_and_date_survive(self):
        gw = CountingGateway()
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        store = LevelStore(gw)
        ground = store.create("Ground")
        store.assign("m1", ground.uid, by="ryan")
        before = FakeLabCoreGateway.read_sql(
            gw, "SELECT moved_at, moved_by FROM lem_machine_level "
                "WHERE machine_uid = 'm1'")["rows"][0]
        store.assign("m1", ground.uid, by="")
        after = FakeLabCoreGateway.read_sql(
            gw, "SELECT moved_at, moved_by FROM lem_machine_level "
                "WHERE machine_uid = 'm1'")["rows"][0]
        assert after["moved_by"] == "ryan"
        assert after == before

    def test_no_from_x_to_x_line_reaches_the_log(self):
        gw = CountingGateway()
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        FakeLabCoreGateway.sql(
            gw, "CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
                "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
                "detail TEXT)")
        store = LevelStore(gw)
        ground = store.create("Ground")
        store.assign("m1", ground.uid, by="ryan")
        store.assign("m1", ground.uid, by="kaden")
        logged = FakeLabCoreGateway.read_sql(
            gw, "SELECT detail FROM lem_machine_log")["rows"]
        assert len(logged) == 1, "the second assign logged a move that never happened"


class TestADeleteThatLandedIsNotUndoneByABlip:
    """`unassign` reads the ladder AFTER the DELETE, to name the level in its
    history line. That read is not the operator's change — it is the receipt —
    so a blip while fetching it must not report the delete as having failed.

    The same mistake was found and fixed one method above in `delete()`; it was
    left standing here.
    """

    def test_a_failed_post_write_read_does_not_fail_the_unassign(self):
        class BlipAfterDelete(FakeLabCoreGateway):
            def __init__(self):
                super().__init__()
                self.deleted = False

            def sql(self, sql, args=None, **kw):
                if "DELETE FROM lem_machine_level" in sql:
                    self.deleted = True
                return super().sql(sql, args, **kw)

            def read_sql(self, sql, args=None, **kw):
                if self.deleted and "lem_levels" in sql:
                    return {"error": "HTTPSConnectionPool: Read timed out"}
                return super().read_sql(sql, args, **kw)

        gw = BlipAfterDelete()
        for ddl in levels_mod.SCHEMA_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        store = LevelStore(gw)
        ground = store.create("Ground")
        store.assign("m1", ground.uid, by="ryan")

        store.unassign("m1", by="ryan")          # must not raise

        left = FakeLabCoreGateway.read_sql(
            gw, "SELECT * FROM lem_machine_level WHERE machine_uid = 'm1'")["rows"]
        assert left == [], "the delete landed, so it must not be reported as refused"
