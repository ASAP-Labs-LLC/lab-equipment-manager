"""Equipment configuration lives on the server, not on this PC.

A machine's setup — source, mappings, QC wiring, PM/CAL — used to exist only
inside this module instance. Reinstall LabStation and it was gone; there was no
way to re-purpose one for a second identical instrument. Export/import files
were the workaround and a second source of truth, so they go.

LabCore's `lem_machine_config` is the store now. On startup the module offers a
real choice: adopt an existing machine, duplicate one, or create a new one.

Pure logic only here — the SQL these build, and the Machine they produce. The
Qt side is checked in test_module_qt.py.
"""
import json
from datetime import datetime

import pytest

import lem_station_module as mod
from lem_station_module import (
    CONFIG_RUNTIME_KEYS,
    CONFIG_TABLE_DDL,
    Machine,
    MaintTask,
    MethodMapping,
    Selector,
    TestSpec,
    build_config_delete,
    build_config_fetch,
    build_config_list_query,
    build_config_upsert,
    config_choices,
    duplicated_machine,
    machine_from_config_payload,
    new_machine_config,
)


def a_machine():
    return Machine(
        uid="m1", title="OptiMPP 1", source_type="single_csv",
        csv_path="C:/prints/optimpp.csv",
        lab_id=Selector(mode="detect", pattern=r"Lab ID\s*:\s*(\S+)"),
        mappings=[MethodMapping(methods=["Cloud Point"], csv_header="Cloud")],
        tests=[TestSpec(name="Cloud Point", value_col="Cloud Point",
                        expected=-7.4, std_dev=2.8)],
        maintenance=[MaintTask(name="Annual cal", kind="calibration")],
        # runtime
        last_position=8172, last_mtime=1754.5,
        last_result_file="lem_latest_optimpp.csv",
        manual_override="SERVICE", override_comment="sensor swap")


# ── the table this shares with the web server ───────────────────────────────

class TestSchema:
    def test_the_ddl_names_the_shared_table(self):
        assert "lem_machine_config" in CONFIG_TABLE_DDL
        assert "IF NOT EXISTS" in CONFIG_TABLE_DDL

    def test_it_keys_on_the_machine_uid(self):
        assert "machine_uid TEXT PRIMARY KEY" in CONFIG_TABLE_DDL

    def test_runtime_keys_are_named_the_same_as_the_server(self):
        """machine_configs.RUNTIME_KEYS on the server must agree with this;
        a mismatch means a duplicate silently carries state across."""
        assert CONFIG_RUNTIME_KEYS == frozenset({
            "last_position", "last_mtime", "last_result_file",
            "manual_override", "override_comment"})


# ── pushing this machine's config up ────────────────────────────────────────

class TestUpsert:
    def test_it_upserts_on_the_uid(self):
        sql, args = build_config_upsert(a_machine(), datetime(2026, 8, 3, 9, 0))
        assert "INSERT INTO lem_machine_config" in sql
        assert "ON CONFLICT(machine_uid) DO UPDATE" in sql

    def test_the_uid_and_title_are_columns_of_their_own(self):
        """The picker lists machines without parsing every blob."""
        sql, args = build_config_upsert(a_machine(), datetime(2026, 8, 3, 9, 0))
        assert args[0] == "m1" and args[1] == "OptiMPP 1"

    def test_the_config_travels_as_json(self):
        sql, args = build_config_upsert(a_machine(), datetime(2026, 8, 3, 9, 0))
        blob = json.loads(args[2])
        assert blob["csv_path"] == "C:/prints/optimpp.csv"
        assert blob["mappings"][0]["csv_header"] == "Cloud"

    def test_it_stamps_when_and_who(self):
        sql, args = build_config_upsert(a_machine(),
                                       datetime(2026, 8, 3, 9, 0), by="kaden")
        assert args[3].startswith("2026-08-03T09:00")
        assert args[4] == "kaden"

    def test_a_machine_with_no_uid_is_refused(self):
        with pytest.raises(ValueError):
            build_config_upsert(Machine(uid="", title="x"), datetime.now())

    def test_a_machine_with_no_title_is_refused(self):
        with pytest.raises(ValueError):
            build_config_upsert(Machine(uid="m1", title="  "), datetime.now())

    def test_this_machines_own_position_is_kept_when_it_saves_itself(self):
        """Runtime state is stripped when a config is COPIED, not when a
        machine stores its own — losing its file offset would re-parse the
        whole print history."""
        sql, args = build_config_upsert(a_machine(), datetime.now())
        assert json.loads(args[2])["last_position"] == 8172


# ── reading configs back ────────────────────────────────────────────────────

class TestQueries:
    def test_the_listing_query_leaves_the_blob_alone(self):
        sql = build_config_list_query()
        assert "machine_uid" in sql and "title" in sql
        assert "config" not in sql.replace("lem_machine_config", "")

    def test_the_listing_is_ordered_for_a_human(self):
        assert "ORDER BY title" in build_config_list_query()

    def test_fetching_one_selects_the_blob(self):
        sql, args = build_config_fetch("m1")
        assert "config" in sql and args == ["m1"]

    def test_deleting_one_is_scoped_to_it(self):
        sql, args = build_config_delete("m1")
        assert sql.startswith("DELETE FROM lem_machine_config")
        assert args == ["m1"]


class TestChoices:
    def test_rows_become_something_a_picker_can_show(self):
        rows = [{"machine_uid": "m1", "title": "OptiMPP 1",
                 "updated_at": "2026-08-03T09:00:00", "updated_by": "kaden"}]
        got = config_choices(rows)
        assert got[0]["machine_uid"] == "m1"
        assert got[0]["title"] == "OptiMPP 1"

    def test_a_config_with_no_title_still_identifies_itself(self):
        got = config_choices([{"machine_uid": "m9", "title": ""}])
        assert "m9" in got[0]["title"]

    def test_junk_rows_are_dropped_rather_than_crashing_the_picker(self):
        assert config_choices([{"title": "no uid"}, None, "nonsense"]) == []

    def test_nothing_registered_is_an_empty_list(self):
        assert config_choices([]) == []
        assert config_choices(None) == []


class TestLoadingAConfig:
    def test_a_json_blob_becomes_a_machine(self):
        blob = json.dumps(a_machine().to_dict())
        loaded = machine_from_config_payload(blob, "m1")
        assert isinstance(loaded, Machine)
        assert loaded.csv_path == "C:/prints/optimpp.csv"
        assert loaded.mappings[0].methods == ["Cloud Point"]

    def test_a_dict_works_too(self):
        loaded = machine_from_config_payload(a_machine().to_dict(), "m1")
        assert loaded.title == "OptiMPP 1"

    def test_the_uid_given_wins_over_whatever_the_blob_says(self):
        """Adopting a config must bind it to the row it came from."""
        loaded = machine_from_config_payload(a_machine().to_dict(), "m-other")
        assert loaded.uid == "m-other"

    def test_a_corrupt_blob_is_a_clear_error_not_a_crash(self):
        with pytest.raises(ValueError):
            machine_from_config_payload("{not json", "m1")

    def test_a_non_object_blob_is_refused(self):
        with pytest.raises(ValueError):
            machine_from_config_payload("[1,2,3]", "m1")

    def test_an_empty_blob_yields_a_usable_blank_machine(self):
        """A machine registered from the floor has no config yet."""
        loaded = machine_from_config_payload("{}", "m5")
        assert loaded.uid == "m5" and loaded.mappings == []


# ── the three ways a module can start ───────────────────────────────────────

class TestNewMachine:
    def test_it_gets_a_title_and_a_fresh_uid(self):
        made = new_machine_config("Multitek 2")
        assert made.title == "Multitek 2" and made.uid

    def test_two_new_machines_never_collide(self):
        assert new_machine_config("A").uid != new_machine_config("B").uid

    def test_it_starts_with_nothing_configured(self):
        made = new_machine_config("Multitek 2")
        assert made.mappings == [] and made.template == ""

    def test_a_new_machine_needs_a_name(self):
        with pytest.raises(ValueError):
            new_machine_config("   ")


class TestDuplicate:
    def test_the_mappings_come_across(self):
        copy = duplicated_machine(a_machine(), "OptiMPP 3")
        assert copy.mappings[0].methods == ["Cloud Point"]
        assert copy.csv_path == "C:/prints/optimpp.csv"

    def test_the_qc_and_pm_setup_comes_across(self):
        copy = duplicated_machine(a_machine(), "OptiMPP 3")
        assert copy.tests[0].name == "Cloud Point"
        assert copy.maintenance[0].kind == "calibration"

    def test_it_is_a_different_machine(self):
        original = a_machine()
        copy = duplicated_machine(original, "OptiMPP 3")
        assert copy.uid and copy.uid != original.uid
        assert copy.title == "OptiMPP 3"

    def test_the_original_is_not_mutated(self):
        original = a_machine()
        duplicated_machine(original, "OptiMPP 3")
        assert original.uid == "m1" and original.title == "OptiMPP 1"
        assert original.last_position == 8172

    def test_no_ingest_position_travels(self):
        """Inheriting a byte offset would skip a new machine's whole file."""
        copy = duplicated_machine(a_machine(), "OptiMPP 3")
        assert copy.last_position == 0 and copy.last_mtime == 0.0
        assert copy.last_result_file == ""

    def test_no_standing_override_travels(self):
        """A copy of a machine flagged for service must not start dead."""
        copy = duplicated_machine(a_machine(), "OptiMPP 3")
        assert copy.manual_override == "" and copy.override_comment == ""

    def test_a_duplicate_needs_a_name(self):
        with pytest.raises(ValueError):
            duplicated_machine(a_machine(), "")


# ── export/import are gone ──────────────────────────────────────────────────

class TestConfigFilesAreRetired:
    def test_the_export_helper_is_gone(self):
        assert not hasattr(mod, "export_machine_config")

    def test_the_import_helper_is_gone(self):
        assert not hasattr(mod, "import_machine_config")

    def test_the_dialog_no_longer_offers_them(self):
        source = open(mod.__file__, encoding="utf-8").read()
        assert "Export config" not in source
        assert "Import config" not in source


# ── the pulse must outlive the watch ────────────────────────────────────────

class TestIdleHeartbeat:
    def test_a_watching_module_says_what_it_watches(self):
        sql, args = mod.build_heartbeat_upsert(
            Machine(uid="m1", title="X", csv_path="C:/p.csv"),
            datetime(2026, 8, 3, 9, 0), polling=True)
        assert args[0] == "m1"
        assert "single_csv" in args[2] and "idle" not in args[2]

    def test_an_idle_module_still_beats_and_says_so(self):
        """Stopping the watch used to stop the pulse, so a loaded module was
        indistinguishable from a crashed one."""
        sql, args = mod.build_heartbeat_upsert(
            Machine(uid="m1", title="X", csv_path="C:/p.csv"),
            datetime(2026, 8, 3, 9, 0), polling=False)
        assert args[1].startswith("2026-08-03T09:00")
        assert "idle (not watching)" in args[2]
        assert "C:/p.csv" in args[2]

    def test_it_still_defaults_to_watching(self):
        """The poll pipeline calls this without the flag."""
        _sql, args = mod.build_heartbeat_upsert(
            Machine(uid="m1", title="X"), datetime(2026, 8, 3, 9, 0))
        assert "idle" not in args[2]


# ── LabCore owns the config: if it's gone, the module clears ────────────────
#
# Nothing is stored locally. So a config deleted on the server means this
# module has no configuration and must stop parsing. The dangerous half is
# telling "the row is gone" apart from "I couldn't ask" — treating an outage as
# a deletion would wipe every module's setup in the lab at once.

class TestConfigDeletedOnTheServer:
    def test_a_definite_empty_answer_means_it_was_deleted(self):
        assert mod.config_was_deleted({"ok": True, "rows": []}) is True

    def test_a_row_coming_back_means_it_is_still_there(self):
        assert mod.config_was_deleted(
            {"ok": True, "rows": [{"machine_uid": "m1", "config": "{}"}]}) is False

    def test_an_error_is_not_a_deletion(self):
        """A LabCore outage must never look like a delete."""
        assert mod.config_was_deleted({"error": "LabCore unreachable"}) is False

    def test_no_answer_at_all_is_not_a_deletion(self):
        assert mod.config_was_deleted(None) is False
        assert mod.config_was_deleted({}) is False

    def test_a_result_without_the_ok_flag_is_not_trusted(self):
        """Only an explicitly successful read can justify clearing."""
        assert mod.config_was_deleted({"rows": []}) is False

    def test_a_malformed_result_is_not_a_deletion(self):
        assert mod.config_was_deleted("nonsense") is False
        assert mod.config_was_deleted({"ok": True, "rows": "nope"}) is False


# ── the picker needs to know which configs a parser is already on ───────────

class TestLiveness:
    def test_the_heartbeat_query_asks_for_what_it_needs(self):
        sql = mod.build_heartbeat_query()
        assert "lem_machine_heartbeat" in sql
        assert "machine_uid" in sql and "last_poll" in sql

    def test_a_recent_beat_is_live(self):
        now = datetime(2026, 8, 3, 12, 0, 0)
        rows = [{"machine_uid": "m1",
                 "last_poll": datetime(2026, 8, 3, 11, 58).isoformat()}]
        assert mod.live_uids(rows, now) == {"m1"}

    def test_an_old_beat_is_not_live(self):
        now = datetime(2026, 8, 3, 12, 0, 0)
        rows = [{"machine_uid": "m1",
                 "last_poll": datetime(2026, 8, 3, 9, 0).isoformat()}]
        assert mod.live_uids(rows, now) == set()

    def test_a_beat_from_the_future_is_not_trusted(self):
        """Clock skew between benches must not mark everything live."""
        now = datetime(2026, 8, 3, 12, 0, 0)
        rows = [{"machine_uid": "m1",
                 "last_poll": datetime(2026, 8, 4, 12, 0).isoformat()}]
        assert mod.live_uids(rows, now) == set()

    def test_junk_rows_are_ignored(self):
        now = datetime(2026, 8, 3, 12, 0, 0)
        rows = [None, "x", {}, {"machine_uid": "m2", "last_poll": "not a date"}]
        assert mod.live_uids(rows, now) == set()

    def test_no_rows_is_nothing_live(self):
        assert mod.live_uids([], datetime.now()) == set()
        assert mod.live_uids(None, datetime.now()) == set()

    def test_choices_can_be_marked_in_use(self):
        rows = [{"machine_uid": "m1", "title": "Live"},
                {"machine_uid": "m2", "title": "Idle"}]
        got = {c["title"]: c for c in config_choices(rows, live={"m1"})}
        assert got["Live"]["in_use"] is True
        assert got["Idle"]["in_use"] is False

    def test_choices_default_to_not_in_use(self):
        got = config_choices([{"machine_uid": "m1", "title": "X"}])
        assert got[0]["in_use"] is False
