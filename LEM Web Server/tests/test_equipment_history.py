"""The one history a piece of equipment has — actions taken, and factors changed.

Two records that were never written down anywhere:

1. **What a person did about a failure.** A machine went RED, or a QC check
   failed, and someone did something about it. Nothing recorded what, or who, or
   whether anyone went back and checked it worked.

2. **What a correction factor used to be.** `lem_correction_factors` is
   overwrite-only — `_corrections()` in web_app.py upserts on
   (machine_uid, test_name) — so changing PAC Flash 2's -3.0 to -2.5 destroys the
   -3.0 and everything about who decided it. A correction changes every reading
   the bench reports, so that is an ISO/IEC 17025 §7.8.2 gap, not a nicety.

Ryan asked for these as ONE timeline per piece of equipment, not two panels: the
question a supervisor actually asks is "what has happened to this instrument",
and the answer interleaves both — a factor change is very often the corrective
action, and reading them apart hides that.

So the merge is the load-bearing part, and most of what is tested here is
ordering: entries are timestamped by machines that do not share a clock.
"""
from datetime import datetime

import pytest

from labcore_gateway import FakeLabCoreGateway
from equipment_history import (
    HISTORY_DDL,
    ActionLifecycleError,
    CorrectiveAction,
    CorrectiveActionStore,
    CorrectionAuditStore,
    EquipmentHistory,
    HistoryEntry,
    HistoryWriteError,
    action_entries,
    correction_entries,
    log_entries,
    maintenance_entries,
    merge_timeline,
)


@pytest.fixture
def bare():
    """A gateway with none of this feature's tables — a field LabCore on the
    day the release lands, before anything has declared them."""
    return FakeLabCoreGateway()


@pytest.fixture
def gw(bare):
    """Schema applied the way the single writer will apply it.

    Deliberately NOT a helper on the store. New tables go in
    snapshot_service.SCHEMA_DDL (RELEASING.md §2); a per-store `ensure_schema()`
    is the pattern that put a bare CREATE TABLE on the write path in the first
    place. The test does here exactly what `SnapshotService.ensure_schema()`
    will do there, so the store is exercised against the real arrangement.
    """
    for ddl in HISTORY_DDL:
        bare.sql(ddl)
    return bare


def entry(at, uid, source="log", kind="run", caused_by="", machine="m1"):
    return HistoryEntry(at=at, uid=uid, source=source, kind=kind,
                        machine_uid=machine, summary=uid, caused_by=caused_by)


# ── the schema is declared, never created on the write path ─────────────────

class TestSchemaShape:
    def test_only_new_tables_are_declared(self):
        """MAJOR vs MINOR turns on this. RELEASING.md §2: a new or renamed
        `lem_*` column on a table the benches already read is MAJOR, because
        every station module has to move with it. Two brand-new tables and no
        ALTER means this ships MINOR and no bench changes."""
        joined = " ".join(HISTORY_DDL)
        assert "ALTER" not in joined.upper()
        for name in ("lem_correction_factors", "lem_machine_log",
                     "lem_machine_status", "lem_maintenance",
                     "lem_qc_specs", "lem_machine_specs"):
            assert name not in joined
        # Every statement creates, and creates something new.
        assert all(d.upper().startswith("CREATE TABLE IF NOT EXISTS")
                   for d in HISTORY_DDL)

    def test_every_table_is_lem_prefixed_and_keyed_on_machine_uid(self):
        """`machine_uid` is the wire contract the benches share. LabCore has no
        foreign keys, so a different key here would not error — it would
        silently orphan every row ever written."""
        joined = " ".join(HISTORY_DDL)
        for table in ("lem_corrective_actions", "lem_correction_audit",
                      "lem_action_events"):
            assert table in joined
        assert joined.count("machine_uid") >= len(HISTORY_DDL)

    def test_the_store_does_not_declare_its_own_tables(self, bare):
        """A read against a table nobody has declared is empty, not an error —
        but the store must not answer that by issuing DDL of its own."""
        assert CorrectiveActionStore(bare).for_machine("m1") == []
        assert CorrectionAuditStore(bare).history("m1") == []
        res = bare.read_sql("SELECT name FROM pragma_table_list "
                            "WHERE name LIKE 'lem_%'")
        assert res.get("rows") == []


# ── corrective actions: what a person did about a failure ───────────────────

class TestOpeningAnAction:
    def test_round_trip(self, gw):
        store = CorrectiveActionStore(gw)
        action = store.open_action(
            "m1", what_happened="Sulfur QC read 12.4 against 9.9 +/- 0.6",
            trigger_kind="qc_fail", trigger_ref="081124-4417",
            test_name="Sulfur", by="kaden",
            when="2026-08-11T09:14:00")
        again = store.get(action.uid)
        assert again.machine_uid == "m1"
        assert again.what_happened.startswith("Sulfur QC read")
        assert again.trigger_kind == "qc_fail"
        assert again.trigger_ref == "081124-4417"
        assert again.test_name == "Sulfur"
        assert again.opened_by == "kaden"
        assert again.opened_at == "2026-08-11T09:14:00"

    def test_uid_is_generated_and_unique(self, gw):
        store = CorrectiveActionStore(gw)
        a = store.open_action("m1", what_happened="one")
        b = store.open_action("m1", what_happened="two")
        assert a.uid and b.uid and a.uid != b.uid

    def test_an_explicit_uid_is_honoured(self, gw):
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="CA-1")
        assert store.get("CA-1").uid == "CA-1"

    def test_opened_at_defaults_to_now(self, gw):
        from datetime import datetime
        store = CorrectiveActionStore(gw)
        action = store.open_action("m1", what_happened="one")
        assert action.opened_at[:10] == datetime.now().date().isoformat()

    def test_a_datetime_is_accepted_as_well_as_a_string(self, gw):
        from datetime import datetime
        store = CorrectiveActionStore(gw)
        action = store.open_action("m1", what_happened="one",
                                   when=datetime(2026, 8, 11, 9, 14, 0))
        assert action.opened_at == "2026-08-11T09:14:00"

    def test_an_action_needs_an_instrument(self, gw):
        with pytest.raises(ValueError):
            CorrectiveActionStore(gw).open_action("  ", what_happened="one")

    def test_an_action_needs_a_description(self, gw):
        """A row saying only that somebody clicked is not a record of anything."""
        with pytest.raises(ValueError):
            CorrectiveActionStore(gw).open_action("m1", what_happened="   ")

    def test_an_unknown_trigger_is_refused(self, gw):
        with pytest.raises(ValueError):
            CorrectiveActionStore(gw).open_action(
                "m1", what_happened="one", trigger_kind="vibes")

    def test_trigger_defaults_to_other(self, gw):
        action = CorrectiveActionStore(gw).open_action("m1", what_happened="one")
        assert action.trigger_kind == "other"

    def test_a_refused_write_is_not_reported_as_saved(self, bare):
        """LabCore's queue refuses past 100 pending and returns an error dict
        rather than raising, and a missing table comes back the same way. A
        corrective action that the queue dropped, reported as filed, is a
        compliance record that does not exist."""
        with pytest.raises(HistoryWriteError):
            CorrectiveActionStore(bare).open_action("m1", what_happened="one")


class TestTheLifeOfAnAction:
    def open(self, gw, **kw):
        store = CorrectiveActionStore(gw)
        kw.setdefault("what_happened", "Sulfur QC out of spec")
        kw.setdefault("when", "2026-08-11T09:00:00")
        return store, store.open_action("m1", **kw)

    def test_a_new_action_is_open(self, gw):
        _store, action = self.open(gw)
        assert action.state == "open"

    def test_recording_what_was_done(self, gw):
        store, action = self.open(gw)
        store.record_action(action.uid, "Reconditioned the cell and reran",
                            by="kaden", when="2026-08-11T11:30:00")
        again = store.get(action.uid)
        assert again.action_taken == "Reconditioned the cell and reran"
        assert again.action_by == "kaden"
        assert again.action_at == "2026-08-11T11:30:00"
        assert again.state == "actioned"

    def test_verifying_it_worked(self, gw):
        store, action = self.open(gw)
        store.record_action(action.uid, "Reconditioned the cell",
                            when="2026-08-11T11:30:00")
        store.verify(action.uid, by="ryan", note="Two QC runs back in band",
                     when="2026-08-12T08:05:00")
        again = store.get(action.uid)
        assert again.verified_at == "2026-08-12T08:05:00"
        assert again.verified_by == "ryan"
        assert again.verification == "Two QC runs back in band"
        assert again.state == "verified"

    def test_closing_it(self, gw):
        store, action = self.open(gw)
        store.record_action(action.uid, "Reconditioned the cell")
        store.verify(action.uid, by="ryan")
        store.close(action.uid, by="ryan", note="Back in service",
                    when="2026-08-12T08:10:00")
        again = store.get(action.uid)
        assert again.closed_at == "2026-08-12T08:10:00"
        assert again.outcome == "closed"
        assert again.state == "closed"

    def test_closing_an_unverified_action_is_refused(self, gw):
        """ISO/IEC 17025 §8.7.1 asks whether the action was effective. Closing
        without answering that is the exact box-tick the record exists to
        prevent — and an action opened by mistake has `withdraw`, which does not
        claim anyone checked anything."""
        store, action = self.open(gw)
        store.record_action(action.uid, "Reconditioned the cell")
        with pytest.raises(ValueError):
            store.close(action.uid, by="ryan")
        assert store.get(action.uid).state == "actioned"

    def test_withdrawing_an_action_opened_by_mistake(self, gw):
        store, action = self.open(gw)
        store.withdraw(action.uid, by="kaden", reason="Wrong instrument",
                       when="2026-08-11T09:05:00")
        again = store.get(action.uid)
        assert again.state == "withdrawn"
        assert again.outcome == "withdrawn"
        assert again.closed_at == "2026-08-11T09:05:00"
        assert again.verified_at == ""          # nothing was ever verified

    def test_an_unknown_action_is_refused_not_silently_ignored(self, gw):
        store = CorrectiveActionStore(gw)
        with pytest.raises(KeyError):
            store.record_action("nope", "did a thing")
        with pytest.raises(KeyError):
            store.verify("nope", by="ryan")
        with pytest.raises(KeyError):
            store.close("nope", by="ryan")

    def test_get_of_an_unknown_uid_is_none(self, gw):
        assert CorrectiveActionStore(gw).get("nope") is None


class TestListingActions:
    def test_scoped_per_machine(self, gw):
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one")
        store.open_action("m2", what_happened="two")
        assert [a.machine_uid for a in store.for_machine("m1")] == ["m1"]
        assert [a.machine_uid for a in store.for_machine("m2")] == ["m2"]

    def test_ordered_oldest_first(self, gw):
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="b", uid="B",
                          when="2026-08-11T12:00:00")
        store.open_action("m1", what_happened="a", uid="A",
                          when="2026-08-10T12:00:00")
        assert [a.uid for a in store.for_machine("m1")] == ["A", "B"]

    def test_open_actions_across_the_floor(self, gw):
        """What a supervisor wants on a Monday: what is still hanging open."""
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="still open", uid="OPEN")
        done = store.open_action("m2", what_happened="finished", uid="DONE")
        store.record_action(done.uid, "fixed")
        store.verify(done.uid, by="ryan")
        store.close(done.uid, by="ryan")
        assert [a.uid for a in store.unresolved()] == ["OPEN"]

    def test_a_withdrawn_action_is_not_unresolved(self, gw):
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="mistake", uid="X")
        store.withdraw("X", by="kaden", reason="wrong instrument")
        assert store.unresolved() == []

    def test_a_missing_table_reads_empty(self, bare):
        assert CorrectiveActionStore(bare).unresolved() == []


# ── the audit trail lem_correction_factors never kept ───────────────────────

class TestCorrectionAudit:
    def test_round_trip(self, gw):
        store = CorrectionAuditStore(gw)
        store.record("m1", "Flash Point", previous=0.0, new_value=-3.0,
                     by="ryan", reason="Round robin bias", units="C",
                     when="2026-08-04T10:00:00")
        row = store.history("m1")[0]
        assert row["test_name"] == "Flash Point"
        assert row["previous"] == 0.0
        assert row["new_value"] == -3.0
        assert row["changed_by"] == "ryan"
        assert row["reason"] == "Round robin bias"
        assert row["units"] == "C"
        assert row["changed_at"] == "2026-08-04T10:00:00"

    def test_it_is_append_only(self, gw):
        """The whole point. The factors table upserts on
        (machine_uid, test_name) and the previous value is gone; two changes to
        one factor must leave two rows here."""
        store = CorrectionAuditStore(gw)
        store.record("m1", "Flash Point", 0.0, -3.0, when="2026-08-04T10:00:00")
        store.record("m1", "Flash Point", -3.0, -2.5, when="2026-08-19T10:00:00")
        rows = store.history("m1")
        assert [(r["previous"], r["new_value"]) for r in rows] == [
            (0.0, -3.0), (-3.0, -2.5)]

    def test_scoped_per_machine_and_per_test(self, gw):
        store = CorrectionAuditStore(gw)
        store.record("m1", "Flash Point", 0.0, -3.0)
        store.record("m1", "Sulfur", 0.0, 0.2)
        store.record("m2", "Flash Point", 0.0, 1.0)
        assert len(store.history("m1")) == 2
        assert len(store.history("m1", test_name="Sulfur")) == 1
        assert len(store.history("m2")) == 1

    def test_removal_is_a_change_to_zero_not_a_hole(self, gw):
        """`api_delete_correction` drops the row from the factors table; the
        readings after it are corrected by 0.0, and the audit has to say so."""
        store = CorrectionAuditStore(gw)
        store.record("m1", "Flash Point", previous=-3.0, new_value=0.0,
                     reason="removed")
        assert store.history("m1")[0]["new_value"] == 0.0

    def test_a_non_numeric_value_is_refused(self, gw):
        """Same rule as `api_save_correction`: coercing "a bit" to 0.0 writes a
        confident claim that no correction was in force."""
        store = CorrectionAuditStore(gw)
        with pytest.raises(ValueError):
            store.record("m1", "Flash Point", 0.0, "a bit")
        with pytest.raises(ValueError):
            store.record("m1", "Flash Point", "  ", 1.0)

    def test_a_pasted_unicode_minus_is_read_as_a_minus(self, gw):
        """PAC Flash 2 really runs at -3.0, so negatives are routine and these
        are typed and pasted by people. U+2212 is indistinguishable from a
        hyphen on screen and `float()` refuses it."""
        store = CorrectionAuditStore(gw)
        store.record("m1", "Flash Point", "0.0", "−3.0")
        assert store.history("m1")[0]["new_value"] == -3.0

    def test_the_number_rule_agrees_with_the_one_in_web_app(self, gw):
        """Duplicated on purpose — importing web_app here would make the
        compliance store need Flask on the path, and web_app will import this
        module. Duplicated on purpose is fine; drifting apart is not, so this
        holds them together the way test_qc_window.py holds `qc_is_stale`."""
        import web_app
        import equipment_history

        assert (equipment_history._MINUS_LOOKALIKES
                == web_app._MINUS_LOOKALIKES)
        for text in ("−3.0", "–2.5", "  1.5  ", "0"):
            assert (equipment_history._number(text, "x")
                    == float(web_app.normalise_number_text(text)))

    def test_a_change_needs_a_test_name(self, gw):
        with pytest.raises(ValueError):
            CorrectionAuditStore(gw).record("m1", "  ", 0.0, 1.0)

    def test_ordered_oldest_first(self, gw):
        store = CorrectionAuditStore(gw)
        store.record("m1", "Sulfur", 0.0, 1.0, when="2026-08-19T10:00:00")
        store.record("m1", "Sulfur", 0.0, 2.0, when="2026-08-04T10:00:00")
        assert [r["new_value"] for r in store.history("m1")] == [2.0, 1.0]

    def test_a_missing_table_reads_empty(self, bare):
        assert CorrectionAuditStore(bare).history("m1") == []

    def test_a_refused_write_raises(self, bare):
        with pytest.raises(HistoryWriteError):
            CorrectionAuditStore(bare).record("m1", "Sulfur", 0.0, 1.0)

    def test_it_never_touches_the_factors_table(self, gw):
        """Constraint the release rides on: this feature adds tables and alters
        none, so the benches do not have to move."""
        CorrectionAuditStore(gw).record("m1", "Sulfur", 0.0, 1.0)
        res = gw.read_sql("SELECT name FROM pragma_table_list "
                          "WHERE name = 'lem_correction_factors'")
        assert res.get("rows") == []


# ── adapters: every source becomes the same small record ────────────────────

class TestAdapters:
    def test_an_action_becomes_one_entry_per_thing_that_happened(self, gw):
        store = CorrectiveActionStore(gw)
        a = store.open_action("m1", what_happened="Sulfur out of spec",
                              uid="CA-1", by="kaden",
                              when="2026-08-11T09:00:00")
        store.record_action("CA-1", "Reconditioned", by="kaden",
                            when="2026-08-11T11:00:00")
        store.verify("CA-1", by="ryan", when="2026-08-12T08:00:00")
        store.close("CA-1", by="ryan", when="2026-08-12T08:10:00")
        entries = action_entries([store.get("CA-1")])
        assert [e.kind for e in entries] == ["opened", "actioned", "verified",
                                             "closed"]
        assert all(e.source == "corrective_action" for e in entries)
        assert all(e.machine_uid == "m1" for e in entries)
        assert a.uid == "CA-1"

    def test_the_later_entries_point_back_at_the_opening(self, gw):
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="CA-1")
        store.record_action("CA-1", "did it")
        opened, actioned = action_entries([store.get("CA-1")])
        assert actioned.caused_by == opened.uid

    def test_an_open_action_is_a_single_entry(self, gw):
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="CA-1")
        assert [e.kind for e in action_entries([store.get("CA-1")])] == ["opened"]

    def test_entry_uids_are_unique_across_an_action(self, gw):
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="CA-1")
        store.record_action("CA-1", "did it")
        store.verify("CA-1", by="ryan")
        uids = [e.uid for e in action_entries([store.get("CA-1")])]
        assert len(set(uids)) == len(uids)

    def test_a_correction_change_reads_as_from_and_to(self, gw):
        store = CorrectionAuditStore(gw)
        store.record("m1", "Flash Point", -3.0, -2.5, by="ryan",
                     when="2026-08-19T10:00:00")
        e = correction_entries(store.history("m1"))[0]
        assert e.source == "correction_factor"
        assert e.kind == "changed"
        assert e.at == "2026-08-19T10:00:00"
        assert e.who == "ryan"
        assert e.detail["previous"] == -3.0
        assert e.detail["new_value"] == -2.5
        assert "Flash Point" in e.summary

    def test_log_rows_come_through_unchanged_in_meaning(self):
        rows = [{"machine_uid": "m1", "ts": "2026-08-11T09:00:00", "kind": "qc",
                 "lab_id": "081124-4417", "test_name": "Sulfur",
                 "value": "12.4", "detail": '{"in_spec": false}'}]
        e = log_entries(rows)[0]
        assert e.source == "log"
        assert e.kind == "qc"
        assert e.at == "2026-08-11T09:00:00"
        assert e.machine_uid == "m1"
        assert e.detail["lab_id"] == "081124-4417"

    def test_a_maintenance_completion_reaches_the_same_shape(self):
        """web_app writes PM/Cal completions into lem_machine_log with
        kind='pm'; the fleet view reads lem_maintenance. Both adapt."""
        logged = log_entries([{"machine_uid": "m1", "ts": "2026-08-01T09:00:00",
                               "kind": "pm", "detail":
                               '{"task": "Lamp change", "by": "kaden"}'}])[0]
        assert logged.kind == "pm"
        assert logged.who == "kaden"
        tasked = maintenance_entries([{"uid": "T1", "machine_uid": "m1",
                                       "name": "Lamp change", "kind": "pm",
                                       "last_done": "2026-08-01",
                                       "note": "annual"}])[0]
        assert tasked.source == "maintenance"
        assert tasked.machine_uid == "m1"
        assert "Lamp change" in tasked.summary

    def test_a_maintenance_task_never_done_yields_nothing(self):
        assert maintenance_entries([{"uid": "T1", "machine_uid": "m1",
                                     "name": "Lamp change", "last_done": ""}]) == []

    def test_every_entry_serialises_to_the_same_keys(self, gw):
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="CA-1")
        audit = CorrectionAuditStore(gw)
        audit.record("m1", "Sulfur", 0.0, 1.0)
        shapes = {frozenset(e.to_dict())
                  for e in action_entries([store.get("CA-1")])
                  + correction_entries(audit.history("m1"))
                  + log_entries([{"machine_uid": "m1", "ts": "x", "kind": "run"}])}
        assert len(shapes) == 1


# ── ordering: the part that is actually hard ────────────────────────────────

class TestMergeOrdering:
    def test_newest_first_by_default(self):
        out = merge_timeline([entry("2026-08-01T09:00:00", "old")],
                             [entry("2026-08-11T09:00:00", "new")])
        assert [e.uid for e in out] == ["new", "old"]

    def test_oldest_first_on_request(self):
        out = merge_timeline([entry("2026-08-01T09:00:00", "old")],
                             [entry("2026-08-11T09:00:00", "new")],
                             newest_first=False)
        assert [e.uid for e in out] == ["old", "new"]

    def test_an_empty_merge_is_an_empty_list(self):
        assert merge_timeline() == []
        assert merge_timeline([], []) == []

    def test_differing_precision_still_compares(self):
        """The bench writes `datetime.now().isoformat()` (microseconds); the
        server writes `isoformat(timespec="seconds")`. Compared as strings,
        '...T09:00:00.500000' sorts after '...T09:00:01'."""
        out = merge_timeline([entry("2026-08-11T09:00:00.500000", "half"),
                              entry("2026-08-11T09:00:01", "one")],
                             newest_first=False)
        assert [e.uid for e in out] == ["half", "one"]

    def test_a_space_separated_stamp_parses(self):
        out = merge_timeline([entry("2026-08-11 09:00:00", "space"),
                              entry("2026-08-11T10:00:00", "tee")],
                             newest_first=False)
        assert [e.uid for e in out] == ["space", "tee"]

    def test_an_offset_bearing_stamp_is_not_dropped(self):
        out = merge_timeline([entry("2026-08-11T09:00:00Z", "zulu"),
                              entry("2026-08-11T09:00:00", "naive")])
        assert {e.uid for e in out} == {"zulu", "naive"}

    def test_an_unreadable_stamp_sinks_to_the_bottom_and_is_kept(self):
        """Dropping it would delete a record because a clock wrote nonsense."""
        out = merge_timeline([entry("", "blank"),
                              entry("not a date", "junk"),
                              entry("2026-08-11T09:00:00", "real")])
        assert out[0].uid == "real"
        assert {e.uid for e in out[1:]} == {"blank", "junk"}

    def test_the_recorded_stamp_is_never_rewritten(self):
        """§7.5.1: the record must let the measurement be reconstructed. We
        order by a parsed copy and hand back the string as it was written."""
        out = merge_timeline([entry("2026-08-11 09:00:00", "space")])
        assert out[0].at == "2026-08-11 09:00:00"

    def test_a_tie_puts_the_event_before_the_response_to_it(self):
        """The bench logs the QC failure and a supervisor opens the action in
        the same second. Read oldest-first, the failure comes first: an action
        cannot precede what it answers."""
        out = merge_timeline(
            [entry("2026-08-11T09:00:00", "fail", source="log", kind="qc")],
            [entry("2026-08-11T09:00:00", "act", source="corrective_action",
                   kind="opened")],
            newest_first=False)
        assert [e.uid for e in out] == ["fail", "act"]

    def test_a_tie_within_one_source_breaks_on_uid(self):
        out = merge_timeline([entry("2026-08-11T09:00:00", "b"),
                              entry("2026-08-11T09:00:00", "a")],
                             newest_first=False)
        assert [e.uid for e in out] == ["a", "b"]

    def test_the_order_does_not_depend_on_the_order_the_sources_arrive(self):
        """Two viewers must see the same history, and one refresh must not
        reshuffle it. The sort key is total, so input order cannot leak in."""
        import random
        items = [entry("2026-08-11T09:00:00", f"u{i}",
                       source=["log", "corrective_action", "correction_factor",
                               "maintenance"][i % 4])
                 for i in range(12)]
        first = [e.uid for e in merge_timeline(items)]
        for _ in range(5):
            shuffled = items[:]
            random.shuffle(shuffled)
            assert [e.uid for e in merge_timeline(shuffled)] == first

    def test_limit_keeps_the_newest(self):
        items = [entry(f"2026-08-{d:02d}T09:00:00", f"d{d}") for d in
                 range(1, 11)]
        out = merge_timeline(items, limit=3)
        assert [e.uid for e in out] == ["d10", "d9", "d8"]

    def test_limit_keeps_the_newest_even_reading_oldest_first(self):
        items = [entry(f"2026-08-{d:02d}T09:00:00", f"d{d}") for d in
                 range(1, 11)]
        out = merge_timeline(items, limit=3, newest_first=False)
        assert [e.uid for e in out] == ["d8", "d9", "d10"]


class TestClocksThatDisagree:
    """The benches and the server do not share a clock, and nothing here can
    make them. What the merge can do is refuse to let a wrong clock reorder a
    chain whose order we already know for certain."""

    def test_an_action_never_sorts_above_its_own_opening(self):
        """A bench PC an hour behind stamps its verification before the
        opening. The link is proof of order; the timestamps are not."""
        out = merge_timeline(
            [entry("2026-08-11T09:00:00", "opened", source="corrective_action",
                   kind="opened"),
             entry("2026-08-11T08:00:00", "verified", source="corrective_action",
                   kind="verified", caused_by="opened")],
            newest_first=False)
        assert [e.uid for e in out] == ["opened", "verified"]

    def test_a_whole_chain_is_held_together(self):
        out = merge_timeline(
            [entry("2026-08-11T09:00:00", "a", caused_by=""),
             entry("2026-08-11T07:00:00", "c", caused_by="b"),
             entry("2026-08-11T08:00:00", "b", caused_by="a")],
            newest_first=False)
        assert [e.uid for e in out] == ["a", "b", "c"]

    def test_a_cause_outside_this_timeline_is_ignored(self):
        """A machine filter, or a limit, can cut the cause off. The entry is
        still shown where its own stamp puts it — never dropped."""
        out = merge_timeline([entry("2026-08-11T09:00:00", "orphan",
                                    caused_by="gone")])
        assert [e.uid for e in out] == ["orphan"]

    def test_a_cycle_neither_hangs_nor_loses_an_entry(self):
        out = merge_timeline([entry("2026-08-11T09:00:00", "a", caused_by="b"),
                              entry("2026-08-11T10:00:00", "b", caused_by="a")])
        assert {e.uid for e in out} == {"a", "b"}

    def test_a_causal_link_does_not_reorder_unrelated_neighbours(self):
        out = merge_timeline(
            [entry("2026-08-11T09:00:00", "open", source="corrective_action"),
             entry("2026-08-11T08:00:00", "verify", source="corrective_action",
                   caused_by="open"),
             entry("2026-08-11T08:30:00", "other", source="log")],
            newest_first=False)
        # `other` keeps its own place; only the linked pair is pinned.
        assert out[0].uid == "other"
        assert [e.uid for e in out[1:]] == ["open", "verify"]


# ── the read side: one instrument's whole history ───────────────────────────

class CountingGateway:
    """Every read counted. This timeline is NOT served from the snapshot, so
    the op cost of one supervisor opening it has to stay small and known."""

    def __init__(self, inner):
        self.inner = inner
        self.reads = 0
        self.writes = 0

    def sql(self, *a, **k):
        self.writes += 1
        return self.inner.sql(*a, **k)

    def read_sql(self, *a, **k):
        self.reads += 1
        return self.inner.read_sql(*a, **k)

    def is_running(self):
        return True


class TestEquipmentHistoryTimeline:
    def seed_log(self, gw):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
               "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
               "detail TEXT)")
        gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
               ["m1", "2026-08-11T08:59:00", "qc", "081124-4417", "Sulfur",
                "12.4", '{"in_spec": false}'])

    def test_the_three_sources_arrive_in_one_list(self, gw):
        self.seed_log(gw)
        CorrectiveActionStore(gw).open_action(
            "m1", what_happened="Sulfur out of spec", uid="CA-1",
            when="2026-08-11T09:10:00")
        CorrectionAuditStore(gw).record("m1", "Sulfur", 0.0, -0.4,
                                        when="2026-08-11T09:20:00")
        out = EquipmentHistory(gw).timeline("m1")
        assert [e.source for e in out] == ["correction_factor",
                                           "corrective_action", "log"]

    def test_scoped_to_one_instrument(self, gw):
        self.seed_log(gw)
        CorrectiveActionStore(gw).open_action("m2", what_happened="elsewhere")
        CorrectionAuditStore(gw).record("m2", "Sulfur", 0.0, 1.0)
        out = EquipmentHistory(gw).timeline("m1")
        assert {e.machine_uid for e in out} == {"m1"}

    def test_nothing_recorded_is_an_empty_timeline(self, gw):
        assert EquipmentHistory(gw).timeline("m1") == []

    def test_missing_tables_read_empty_rather_than_failing(self, bare):
        assert EquipmentHistory(bare).timeline("m1") == []

    def test_limit_is_applied_across_the_merge_not_per_source(self, gw):
        self.seed_log(gw)
        CorrectiveActionStore(gw).open_action(
            "m1", what_happened="one", uid="CA-1", when="2026-08-11T09:10:00")
        CorrectionAuditStore(gw).record("m1", "Sulfur", 0.0, -0.4,
                                        when="2026-08-11T09:20:00")
        assert len(EquipmentHistory(gw).timeline("m1", limit=2)) == 2

    # The op count moved to four when maintenance was wired in — see
    # TestMaintenanceReachesTheTimeline::test_one_timeline_costs_four_reads.
    # Four sources, four reads, still nothing polled and still no write.


# ═══════════════════════════════════════════════════════════════════════════
# Round two — what the critic found.
# ═══════════════════════════════════════════════════════════════════════════


class RefusingGateway:
    """LabCore's write queue past 100 pending.

    It refuses by **returning an error dict**, not by raising, and reads keep
    working the whole time — which is exactly what makes the failure easy to
    miss. A store that only catches exceptions reports work it never did.
    """

    def __init__(self, inner):
        self.inner = inner
        self.refusing = False

    def sql(self, *a, **k):
        if self.refusing:
            return {"error": "queue is full (100 pending)"}
        return self.inner.sql(*a, **k)

    def read_sql(self, *a, **k):
        return self.inner.read_sql(*a, **k)

    def is_running(self):
        return True


# ── BLOCKER: the lifecycle is a lifecycle, not one guarded step ─────────────

class TestTheLifecycleIsEnforced:
    """Only `close` was guarded. Everything else overwrote a finished record.

    A corrective action is the evidence that a lab noticed a problem and dealt
    with it (ISO/IEC 17025 §8.7). If a later call can quietly reopen it,
    re-close it with a different date, or rewrite the outcome, then what the
    row says today is not what happened — it is whatever was written last.
    """

    def opened(self, gw, uid="CA-1"):
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="Sulfur out of spec", uid=uid,
                          when="2026-08-11T09:00:00")
        return store

    def closed(self, gw, uid="CA-1"):
        store = self.opened(gw, uid)
        store.record_action(uid, "Reconditioned the cell",
                            when="2026-08-11T11:00:00")
        store.verify(uid, by="ryan", note="Two QC runs back in band",
                     when="2026-08-12T08:00:00")
        store.close(uid, by="ryan", note="Back in service",
                    when="2026-08-12T08:10:00")
        return store

    def test_the_legal_transitions_are_stated_in_one_place(self):
        """A lifecycle scattered across four `if`s is one nobody can read."""
        from equipment_history import LIFECYCLE
        assert LIFECYCLE["closed"] == frozenset()
        assert LIFECYCLE["withdrawn"] == frozenset()
        assert "withdrawn" in LIFECYCLE["open"]
        assert "verified" in LIFECYCLE["actioned"]
        assert "closed" in LIFECYCLE["verified"]

    def test_a_closed_action_cannot_be_reopened_or_rewritten(self, gw):
        from equipment_history import ActionLifecycleError
        store = self.closed(gw)
        for call in (lambda: store.record_action("CA-1", "actually I redid it"),
                     lambda: store.verify("CA-1", by="someone"),
                     lambda: store.close("CA-1", by="someone", note="again"),
                     lambda: store.withdraw("CA-1", by="someone",
                                            reason="never mind")):
            with pytest.raises(ActionLifecycleError):
                call()
        again = store.get("CA-1")
        assert again.state == "closed"
        assert again.closed_at == "2026-08-12T08:10:00"
        assert again.closed_by == "ryan"
        assert again.closed_note == "Back in service"
        assert again.outcome == "closed"
        assert again.action_taken == "Reconditioned the cell"
        assert again.verification == "Two QC runs back in band"

    def test_a_withdrawn_action_is_terminal_too(self, gw):
        from equipment_history import ActionLifecycleError
        store = self.opened(gw)
        store.withdraw("CA-1", by="kaden", reason="Wrong instrument",
                       when="2026-08-11T09:05:00")
        with pytest.raises(ActionLifecycleError):
            store.record_action("CA-1", "did something after all")
        with pytest.raises(ActionLifecycleError):
            store.verify("CA-1", by="ryan")
        assert store.get("CA-1").state == "withdrawn"
        assert store.get("CA-1").verified_at == ""

    def test_verifying_an_action_nobody_has_taken_is_refused(self, gw):
        """§8.7.1 asks whether the action was effective. With no action
        recorded there is nothing whose effectiveness could have been checked,
        and a `verified_at` on an empty action is the strongest possible false
        claim this table can hold."""
        from equipment_history import ActionLifecycleError
        store = self.opened(gw)
        with pytest.raises(ActionLifecycleError):
            store.verify("CA-1", by="ryan")
        assert store.get("CA-1").state == "open"
        assert store.get("CA-1").verified_at == ""

    def test_re_verifying_is_refused(self, gw):
        from equipment_history import ActionLifecycleError
        store = self.opened(gw)
        store.record_action("CA-1", "Reconditioned")
        store.verify("CA-1", by="ryan", note="Back in band",
                     when="2026-08-12T08:00:00")
        with pytest.raises(ActionLifecycleError):
            store.verify("CA-1", by="kaden", note="looks fine to me",
                         when="2026-08-13T08:00:00")
        again = store.get("CA-1")
        assert again.verified_by == "ryan"
        assert again.verification == "Back in band"
        assert again.verified_at == "2026-08-12T08:00:00"

    def test_what_was_done_cannot_be_rewritten_after_it_was_verified(self, gw):
        """The verification attests to the action *as recorded*. Editing the
        note afterwards leaves a verification pointing at work nobody checked."""
        from equipment_history import ActionLifecycleError
        store = self.opened(gw)
        store.record_action("CA-1", "Reconditioned the cell")
        store.verify("CA-1", by="ryan")
        with pytest.raises(ActionLifecycleError):
            store.record_action("CA-1", "Actually we replaced the whole cell")
        assert store.get("CA-1").action_taken == "Reconditioned the cell"

    def test_the_note_is_still_editable_before_it_is_verified(self, gw):
        """Kept on purpose: the first note is typed mid-job and finished after,
        and a second row would read as a second action taken."""
        store = self.opened(gw)
        store.record_action("CA-1", "Started reconditioning")
        store.record_action("CA-1", "Reconditioned the cell and reran")
        assert store.get("CA-1").action_taken == "Reconditioned the cell and reran"

    def test_a_duplicate_can_still_be_withdrawn_after_verification(self, gw):
        """Withdrawal is the only way to say 'this record should not exist',
        and a duplicate is very often spotted by the second person going to
        verify it. Legal from every state that is not already finished."""
        store = self.opened(gw)
        store.record_action("CA-1", "Reconditioned")
        store.verify("CA-1", by="ryan")
        store.withdraw("CA-1", by="ryan", reason="Duplicate of CA-0")
        assert store.get("CA-1").state == "withdrawn"

    def test_the_refusal_says_what_it_refused_and_why(self, gw):
        from equipment_history import ActionLifecycleError
        store = self.closed(gw)
        with pytest.raises(ActionLifecycleError) as caught:
            store.verify("CA-1", by="ryan")
        message = str(caught.value)
        assert "closed" in message
        assert "CA-1" in message
        assert "new" in message.lower()      # …open a new action instead

    def test_a_lifecycle_refusal_is_a_valueerror(self, gw):
        """`close` already refused an unverified action with ValueError and a
        route above catches that. Narrowing the type must not widen the hole."""
        from equipment_history import ActionLifecycleError
        assert issubclass(ActionLifecycleError, ValueError)

    def test_an_unknown_uid_still_raises_keyerror_first(self, gw):
        store = CorrectiveActionStore(gw)
        with pytest.raises(KeyError):
            store.withdraw("nope", by="ryan")


# ── MAJOR: two failure paths that were prose only ──────────────────────────

class TestFailurePathsThatWereOnlyAsserted:
    def test_a_refused_update_is_not_reported_as_recorded(self, gw):
        """The mutation that survived the suite: delete `_written` from
        `_update` and every test still passed. The queue refuses by RETURNING
        an error dict, so an operator is told "saved" about a row LabCore
        dropped, and nobody ever looks again."""
        inner = CorrectiveActionStore(gw)
        inner.open_action("m1", what_happened="one", uid="CA-1")
        gate = RefusingGateway(gw)
        store = CorrectiveActionStore(gate)
        gate.refusing = True
        with pytest.raises(HistoryWriteError):
            store.record_action("CA-1", "did it")
        gate.refusing = False
        assert store.get("CA-1").action_taken == ""

    def test_every_step_of_the_life_refuses_the_same_way(self, gw):
        gate = RefusingGateway(gw)
        store = CorrectiveActionStore(gate)
        store.open_action("m1", what_happened="one", uid="CA-1")
        store.record_action("CA-1", "did it")
        gate.refusing = True
        with pytest.raises(HistoryWriteError):
            store.verify("CA-1", by="ryan")
        gate.refusing = False
        store.verify("CA-1", by="ryan")
        gate.refusing = True
        with pytest.raises(HistoryWriteError):
            store.close("CA-1", by="ryan")
        with pytest.raises(HistoryWriteError):
            store.withdraw("CA-1", by="ryan", reason="no")
        gate.refusing = False
        assert store.get("CA-1").state == "verified"

    def test_a_table_that_is_not_there_yet_is_no_action_a_blip_is_not(self, bare):
        """`get()` swallowed EVERY read error and returned None, so a timeout
        read as "no such action" — a sentence an operator acts on. Only the one
        error that honestly means empty may still do that, and only on a read:
        a write path is told the table is missing, because it is about to
        report a compliance record as filed. See TestCouldNotAskIsNever...
        """
        store = CorrectiveActionStore(bare)
        assert store.get("CA-1") is None            # nothing was ever recorded
        with pytest.raises(LabCoreUnavailable):     # not "no such action"
            store.record_action("CA-1", "did it")

    def test_an_unreadable_detail_blob_is_kept_as_evidence(self):
        """`lem_machine_log.detail` is written by the modules. Discarding one
        that will not parse throws away the only thing that says what went
        wrong."""
        broken = log_entries([{"machine_uid": "m1", "ts": "2026-08-11T09:00:00",
                               "kind": "run", "detail": "{not json"}])[0]
        assert broken.detail["text"] == "{not json"
        listed = log_entries([{"machine_uid": "m1", "ts": "2026-08-11T09:00:00",
                               "kind": "run", "detail": "[1, 2]"}])[0]
        assert listed.detail["value"] == [1, 2]

    def test_an_offset_bearing_stamp_lands_on_the_same_axis(self):
        """Prose said "converted to local rather than dropped"; the old test
        only checked that both survived, which a `return None` also satisfies —
        it would just sink the stamped one to the unreadable end."""
        import equipment_history as eh
        from datetime import datetime, timedelta, timezone
        local = datetime(2026, 8, 11, 9, 0, 0)
        aware = local.astimezone().astimezone(timezone(timedelta(hours=-7)))
        assert eh.parse_stamp(aware.isoformat()) == local
        out = merge_timeline([entry(aware.isoformat(), "zulu"),
                              entry("2026-08-11T08:00:00", "before"),
                              entry("2026-08-11T10:00:00", "after")],
                             newest_first=False)
        assert [e.uid for e in out] == ["before", "zulu", "after"]

    def test_two_log_rows_in_one_second_are_two_entries(self):
        """The uid was built from (machine, ts, kind, test, lab_id) only, so
        two prints in the same second with different values collided — one uid
        for two events, in a merge whose ordering comment claims a total
        order."""
        rows = [{"machine_uid": "m1", "ts": "2026-08-11T09:00:00", "kind": "run",
                 "value": "12.4"},
                {"machine_uid": "m1", "ts": "2026-08-11T09:00:00", "kind": "run",
                 "value": "9.8"}]
        uids = [e.uid for e in log_entries(rows)]
        assert len(set(uids)) == 2
        # byte-identical rows really are one event, and still share a uid
        same = log_entries([rows[0], dict(rows[0])])
        assert same[0].uid == same[1].uid


# ── MAJOR: maintenance belongs in "the whole history" ──────────────────────

def seed_maintenance(gw, uid="T1", machine="m1", last_done="2026-08-01"):
    gw.sql("CREATE TABLE IF NOT EXISTS lem_maintenance (uid TEXT PRIMARY KEY, "
           "machine_uid TEXT NOT NULL, name TEXT NOT NULL, kind TEXT, "
           "interval_days INTEGER, last_done TEXT, note TEXT)")
    gw.sql("INSERT INTO lem_maintenance VALUES (?,?,?,?,?,?,?)",
           [uid, machine, "Lamp change", "pm", 365, last_done, "annual"])


class TestMaintenanceReachesTheTimeline:
    def test_a_pm_completion_is_part_of_the_whole_history(self, gw):
        """`maintenance_entries` existed, was tested as an adapter, and was
        never called: "one instrument's whole history" silently excluded every
        PM and calibration."""
        seed_maintenance(gw)
        out = EquipmentHistory(gw).timeline("m1")
        assert "maintenance" in {e.source for e in out}
        assert any("Lamp change" in e.summary for e in out)

    def test_maintenance_is_scoped_to_the_instrument(self, gw):
        seed_maintenance(gw, uid="T2", machine="m2")
        assert EquipmentHistory(gw).timeline("m1") == []

    def test_a_task_never_done_adds_nothing(self, gw):
        seed_maintenance(gw, last_done="")
        assert EquipmentHistory(gw).timeline("m1") == []

    def test_a_missing_maintenance_table_reads_empty(self, bare):
        assert EquipmentHistory(bare).timeline("m1") == []

    def test_one_timeline_costs_one_read_per_source(self, gw):
        """Five sources, five reads, and still nothing polled: a person opens
        one instrument's history and reads it. The fifth is the action-event
        trail (assignments and notes), read PER MACHINE rather than per action
        — which is why that table carries `machine_uid` as well as the action
        it belongs to. LabCore has no foreign keys, and a read per open action
        is the N-reads-per-page pattern the snapshot design forbids."""
        seed_maintenance(gw)
        counted = CountingGateway(gw)
        EquipmentHistory(counted).timeline("m1")
        assert counted.reads == 5
        assert counted.writes == 0


# ── MAJOR: a history that stops has to say it stopped ──────────────────────

def seed_log_rows(gw, count, machine="m1"):
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
           "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
           "detail TEXT)")
    for i in range(count):
        gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
               [machine, f"2026-08-11T09:{i // 60:02d}:{i % 60:02d}", "run",
                "", "Sulfur", str(i), "{}"])


class TestTruncationIsAnnounced:
    def test_under_the_limit_is_whole(self, gw):
        seed_log_rows(gw, 5)
        out = EquipmentHistory(gw).timeline("m1")
        assert len(out) == 5
        assert out.truncated is False
        assert out.note == ""

    def test_exactly_the_limit_is_still_whole(self, gw):
        """The boundary nothing pinned. Reading exactly LOG_LIMIT rows out of a
        `LIMIT LOG_LIMIT` query cannot tell you whether there were more, so the
        read asks for one extra."""
        seed_log_rows(gw, EquipmentHistory.LOG_LIMIT)
        out = EquipmentHistory(gw).timeline("m1")
        assert len(out) == EquipmentHistory.LOG_LIMIT
        assert out.truncated is False

    def test_one_over_the_limit_says_so(self, gw):
        seed_log_rows(gw, EquipmentHistory.LOG_LIMIT + 1)
        out = EquipmentHistory(gw).timeline("m1")
        assert len(out) == EquipmentHistory.LOG_LIMIT
        assert out.truncated is True
        assert str(EquipmentHistory.LOG_LIMIT) in out.note
        assert out.to_dict()["truncated"] is True

    def test_the_newest_are_the_ones_kept(self, gw):
        seed_log_rows(gw, EquipmentHistory.LOG_LIMIT + 1)
        out = EquipmentHistory(gw).timeline("m1")
        assert out[0].detail["value"] == str(EquipmentHistory.LOG_LIMIT)

    def test_an_explicit_limit_also_says_so(self, gw):
        seed_log_rows(gw, 10)
        out = EquipmentHistory(gw).timeline("m1", limit=3)
        assert len(out) == 3
        assert out.truncated is True
        assert "3" in out.note

    def test_a_timeline_is_still_a_list(self, gw):
        """Every caller that only renders entries keeps working; the flag is
        there for the one that draws the footer."""
        seed_log_rows(gw, 2)
        out = EquipmentHistory(gw).timeline("m1")
        assert isinstance(out, list)
        assert len(out) == 2
        assert EquipmentHistory(gw).timeline("nobody") == []


# ── the trigger link the schema already paid for ───────────────────────────

class TestTheTriggerIsLinkedToTheAction:
    def test_an_action_points_back_at_the_qc_failure_it_answers(self, gw):
        """`trigger_ref` held the Lab ID of the failing run and nothing ever
        read it, so the one link this feature exists to draw could not be
        drawn — and a server clock behind the bench put the response above its
        cause.

        The ref is now the identity of the EVENT, captured when the action is
        opened. A Lab ID names the standard, not the run of it, and resolving
        one by search re-dated the action every time the bench printed — see
        TestTheTriggerNamesOneEvent.
        """
        from equipment_history import log_event_ref
        row = {"machine_uid": "m1", "ts": "2026-08-11T09:00:00", "kind": "qc",
               "lab_id": "081124-4417", "test_name": "Sulfur", "value": "12.4",
               "detail": '{"in_spec": false}'}
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
               "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
               "detail TEXT)")
        gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
               [row["machine_uid"], row["ts"], row["kind"], row["lab_id"],
                row["test_name"], row["value"], row["detail"]])
        CorrectiveActionStore(gw).open_action(
            "m1", what_happened="Sulfur QC out of spec", uid="CA-1",
            trigger_kind="qc_fail", trigger_ref=log_event_ref(row),
            when="2026-08-11T08:30:00")            # server clock half an hour behind
        out = EquipmentHistory(gw).timeline("m1", newest_first=False)
        assert [e.kind for e in out] == ["qc", "opened"]
        opened = out[1]
        assert opened.caused_by == out[0].uid

    def test_a_trigger_naming_nothing_here_is_left_alone(self, gw):
        CorrectiveActionStore(gw).open_action(
            "m1", what_happened="one", uid="CA-1", trigger_kind="qc_fail",
            trigger_ref="never-logged")
        out = EquipmentHistory(gw).timeline("m1")
        assert [e.uid for e in out] == ["CA-1"]
        assert out[0].caused_by == ""


# ── MINORS ─────────────────────────────────────────────────────────────────

class TestMinorRepairs:
    def test_a_stored_value_that_cannot_be_read_is_not_quietly_zero(self, gw):
        """`record()` refuses "a bit" because coercing it writes a confident
        claim that no correction was in force — and then `history()` did that
        exact coercion on the way back out."""
        gw.sql("INSERT INTO lem_correction_audit (uid, machine_uid, test_name, "
               "previous, new_value, units, changed_at, changed_by, reason) "
               "VALUES (?,?,?,?,?,?,?,?,?)",
               ["X", "m1", "Sulfur", "a bit", 1.0, "", "2026-08-11T09:00:00",
                "", ""])
        row = CorrectionAuditStore(gw).history("m1")[0]
        assert row["previous"] != 0.0
        assert row["previous"] == "a bit"
        assert row["unreadable"] == ["previous"]

    def test_a_written_row_and_a_read_row_are_the_same_shape(self, gw):
        store = CorrectionAuditStore(gw)
        written = store.record("m1", "Sulfur", 0.0, 1.0)
        assert set(written) == set(store.history("m1")[0])

    def test_a_readable_row_says_nothing_is_unreadable(self, gw):
        CorrectionAuditStore(gw).record("m1", "Sulfur", 0.0, 1.0)
        row = CorrectionAuditStore(gw).history("m1")[0]
        assert row["unreadable"] == []
        assert row["new_value"] == 1.0

    def test_a_timestamp_that_is_not_a_date_is_refused(self, gw):
        """`_stamp` took any non-empty string, so "soon" or a half-typed date
        went into the record and then sorted to the unreadable end forever."""
        store = CorrectiveActionStore(gw)
        with pytest.raises(ValueError):
            store.open_action("m1", what_happened="one", when="soon")
        with pytest.raises(ValueError):
            CorrectionAuditStore(gw).record("m1", "Sulfur", 0.0, 1.0,
                                            when="2026-13-45")

    def test_a_date_alone_is_still_a_timestamp(self, gw):
        action = CorrectiveActionStore(gw).open_action(
            "m1", what_happened="one", when="2026-08-11")
        assert action.opened_at == "2026-08-11"

    def test_it_never_touches_the_factors_table(self, gw):
        """Rewritten: the old version passed against an empty implementation,
        because it only asserted that a table nobody had created did not exist.
        Now the table is there with a row in it, and the audit still has to do
        its own job without touching it."""
        gw.sql("CREATE TABLE IF NOT EXISTS lem_correction_factors ("
               "machine_uid TEXT NOT NULL, test_name TEXT NOT NULL, "
               "correction REAL NOT NULL DEFAULT 0.0, units TEXT, "
               "updated_at TEXT, updated_by TEXT, "
               "PRIMARY KEY (machine_uid, test_name))")
        gw.sql("INSERT INTO lem_correction_factors VALUES (?,?,?,?,?,?)",
               ["m1", "Sulfur", -3.0, "C", "2026-08-01T09:00:00", "ryan"])
        before = gw.read_sql("SELECT * FROM lem_correction_factors")["rows"]
        CorrectionAuditStore(gw).record("m1", "Sulfur", -3.0, -2.5, by="ryan")
        after = gw.read_sql("SELECT * FROM lem_correction_factors")["rows"]
        assert after == before
        assert len(CorrectionAuditStore(gw).history("m1")) == 1

    def test_the_log_read_is_the_one_in_qc_specs(self, gw, monkeypatch):
        """It was copied verbatim, in a file that carefully defends its one
        other duplication. Two copies of a query drift, and this one decides
        what a compliance timeline contains.

        Behavioural, not a grep over the source: the old version asserted that
        the string "FROM lem_machine_log" did not appear, which a module that
        had stopped reading the log at all would also satisfy. This replaces
        the shared reader and insists the timeline came through it.
        """
        import equipment_history
        seed_log_rows(gw, 2)
        seen = {}
        real = equipment_history.MachineStateReader.events

        def spy(self, machine_uid, limit=100):
            seen["machine_uid"], seen["limit"] = machine_uid, limit
            return real(self, machine_uid, limit)

        monkeypatch.setattr(equipment_history.MachineStateReader, "events", spy)
        out = EquipmentHistory(gw).timeline("m1")
        assert seen["machine_uid"] == "m1"
        assert seen["limit"] == EquipmentHistory.LOG_LIMIT + 1
        assert [e.source for e in out] == ["log", "log"]


# ── DECIDED SCOPE: assignment, due dates, priority ─────────────────────────

class TestPriorityIsAClosedSet:
    def test_the_set_is_named_and_ranked(self):
        from equipment_history import PRIORITIES, PRIORITY_RANK, DEFAULT_PRIORITY
        assert DEFAULT_PRIORITY in PRIORITIES
        assert set(PRIORITY_RANK) == set(PRIORITIES)
        assert (PRIORITY_RANK["critical"] > PRIORITY_RANK["high"]
                > PRIORITY_RANK["normal"] > PRIORITY_RANK["low"])

    def test_it_defaults_rather_than_being_blank(self, gw):
        from equipment_history import DEFAULT_PRIORITY
        action = CorrectiveActionStore(gw).open_action("m1",
                                                       what_happened="one")
        assert action.priority == DEFAULT_PRIORITY

    def test_anything_outside_the_set_is_refused(self, gw):
        store = CorrectiveActionStore(gw)
        for bad in ("urgent", "P1", "very high", "9"):
            with pytest.raises(ValueError):
                store.open_action("m1", what_happened="one", priority=bad)

    def test_case_and_padding_are_forgiven(self, gw):
        action = CorrectiveActionStore(gw).open_action(
            "m1", what_happened="one", priority="  Critical ")
        assert action.priority == "critical"


class TestAssignmentAndDueDates:
    def test_they_round_trip(self, gw):
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="CA-1",
                          assigned_to="kaden", due_at="2026-08-15T17:00:00",
                          priority="high", when="2026-08-11T09:00:00")
        again = store.get("CA-1")
        assert again.assigned_to == "kaden"
        assert again.due_at == "2026-08-15T17:00:00"
        assert again.priority == "high"
        assert again.to_dict()["assigned_to"] == "kaden"
        assert again.to_dict()["due_at"] == "2026-08-15T17:00:00"
        assert again.to_dict()["priority"] == "high"

    def test_unassigned_is_a_first_class_value(self, gw):
        """LabCore has no user directory and no foreign keys, so `assigned_to`
        is a label, not a reference. Nothing may filter it out."""
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="CA-1")
        assert store.get("CA-1").assigned_to == ""
        assert [a.uid for a in store.open_actions()] == ["CA-1"]

    def test_a_departed_user_never_makes_an_action_vanish(self, gw):
        """The failure mode this rule exists to prevent: a rename or a leaver
        quietly emptying somebody's open list, so the action is never done."""
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="GONE",
                          assigned_to="someone-who-left")
        store.open_action("m1", what_happened="two", uid="HERE",
                          assigned_to="kaden")
        assert {a.uid for a in store.open_actions()} == {"GONE", "HERE"}
        assert {a.uid for a in store.open_actions("m1")} == {"GONE", "HERE"}
        assert {a.uid for a in store.open_by_machine()["m1"]} == {"GONE", "HERE"}

    def test_an_unreadable_due_date_is_refused(self, gw):
        store = CorrectiveActionStore(gw)
        with pytest.raises(ValueError):
            store.open_action("m1", what_happened="one", due_at="next week")

    def test_no_due_date_is_allowed(self, gw):
        action = CorrectiveActionStore(gw).open_action("m1",
                                                       what_happened="one")
        assert action.due_at == ""
        assert action.is_overdue(datetime(2030, 1, 1)) is False

    def test_reassigning_and_extending(self, gw):
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="CA-1",
                          assigned_to="someone-who-left",
                          due_at="2026-08-15", priority="low")
        store.assign("CA-1", assigned_to="kaden", due_at="2026-08-20",
                     priority="high", by="ryan")
        again = store.get("CA-1")
        assert (again.assigned_to, again.due_at, again.priority) == (
            "kaden", "2026-08-20", "high")

    def test_assignment_leaves_what_it_was_not_given_alone(self, gw):
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="CA-1",
                          assigned_to="kaden", due_at="2026-08-15",
                          priority="high")
        store.assign("CA-1", due_at="2026-08-20")
        again = store.get("CA-1")
        assert (again.assigned_to, again.priority) == ("kaden", "high")
        assert again.due_at == "2026-08-20"

    def test_a_finished_action_cannot_be_reassigned(self, gw):
        from equipment_history import ActionLifecycleError
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="CA-1")
        store.withdraw("CA-1", by="ryan", reason="duplicate")
        with pytest.raises(ActionLifecycleError):
            store.assign("CA-1", assigned_to="kaden")

    def test_a_refused_assignment_write_raises(self, gw):
        gate = RefusingGateway(gw)
        store = CorrectiveActionStore(gate)
        store.open_action("m1", what_happened="one", uid="CA-1")
        gate.refusing = True
        with pytest.raises(HistoryWriteError):
            store.assign("CA-1", assigned_to="kaden")


class TestOverdue:
    """"Overdue" is a stored date compared with a clock, and this lab's clocks
    do not agree. The comparison is defined against the SERVER's clock."""

    def test_overdue_against_the_given_clock(self, gw):
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="CA-1",
                          due_at="2026-08-15T17:00:00")
        action = store.get("CA-1")
        assert action.is_overdue(datetime(2026, 8, 15, 16, 59)) is False
        assert action.is_overdue(datetime(2026, 8, 15, 17, 1)) is True

    def test_a_date_alone_is_due_at_the_end_of_that_day(self, gw):
        """A person typing "2026-08-15" means by the end of the 15th. Read as
        midnight it would be overdue for the whole day it is due."""
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="CA-1",
                          due_at="2026-08-15")
        action = store.get("CA-1")
        assert action.is_overdue(datetime(2026, 8, 15, 9, 0)) is False
        assert action.is_overdue(datetime(2026, 8, 15, 23, 59, 59)) is False
        assert action.is_overdue(datetime(2026, 8, 16, 0, 0, 1)) is True

    def test_a_finished_action_is_never_overdue(self, gw):
        """Nothing is owed on it. A closed action left in an overdue list is a
        red badge nobody can clear."""
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="CA-1",
                          due_at="2026-08-15")
        store.record_action("CA-1", "did it")
        store.verify("CA-1", by="ryan")
        store.close("CA-1", by="ryan")
        assert store.get("CA-1").is_overdue(datetime(2030, 1, 1)) is False
        store.open_action("m1", what_happened="two", uid="CA-2",
                          due_at="2026-08-15")
        store.withdraw("CA-2", by="ryan", reason="duplicate")
        assert store.get("CA-2").is_overdue(datetime(2030, 1, 1)) is False

    def test_an_unreadable_stored_due_date_is_not_overdue(self, gw):
        """Written by something that is not this store. Guessing is worse than
        saying nothing: an invented red badge sends someone to an instrument
        that is fine."""
        gw.sql("UPDATE lem_corrective_actions SET due_at = 'whenever' "
               "WHERE uid = ?", ["nope"])
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="CA-1")
        gw.sql("UPDATE lem_corrective_actions SET due_at = 'whenever' "
               "WHERE uid = ?", ["CA-1"])
        assert store.get("CA-1").is_overdue(datetime(2030, 1, 1)) is False

    def test_the_whole_fleet_in_one_read(self, gw):
        """The equipment card badges every instrument on one page. N reads for
        N instruments is exactly the pattern the snapshot design forbids."""
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="A",
                          due_at="2026-08-01")
        store.open_action("m2", what_happened="two", uid="B",
                          due_at="2030-01-01")
        counted = CountingGateway(gw)
        grouped = CorrectiveActionStore(counted).open_by_machine()
        assert counted.reads == 1
        assert set(grouped) == {"m1", "m2"}
        counted.reads = 0
        late = CorrectiveActionStore(counted).overdue(
            now=datetime(2026, 8, 24, 9, 0))
        assert counted.reads == 1
        assert [a.uid for a in late] == ["A"]

    def test_open_actions_for_one_instrument_in_one_read(self, gw):
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="A")
        store.open_action("m2", what_happened="two", uid="B")
        counted = CountingGateway(gw)
        mine = CorrectiveActionStore(counted).open_actions("m1")
        assert counted.reads == 1
        assert [a.uid for a in mine] == ["A"]

    def test_open_actions_lead_with_the_most_urgent(self, gw):
        """What the list is for: the thing to do first is at the top. Priority
        first, then the soonest due, then oldest — a stored word cannot be
        ordered by SQL, so the rank lives with the constant that names it."""
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="a", uid="LOW", priority="low",
                          due_at="2026-08-12", when="2026-08-01T09:00:00")
        store.open_action("m1", what_happened="b", uid="CRIT",
                          priority="critical", due_at="2026-09-01",
                          when="2026-08-02T09:00:00")
        store.open_action("m1", what_happened="c", uid="HIGH-SOON",
                          priority="high", due_at="2026-08-12",
                          when="2026-08-03T09:00:00")
        store.open_action("m1", what_happened="d", uid="HIGH-LATER",
                          priority="high", due_at="2026-08-20",
                          when="2026-08-04T09:00:00")
        store.open_action("m1", what_happened="e", uid="HIGH-NONE",
                          priority="high", when="2026-08-05T09:00:00")
        assert [a.uid for a in store.open_actions("m1")] == [
            "CRIT", "HIGH-SOON", "HIGH-LATER", "HIGH-NONE", "LOW"]

    def test_a_closed_action_is_not_open(self, gw):
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="CA-1")
        store.record_action("CA-1", "did it")
        store.verify("CA-1", by="ryan")
        store.close("CA-1", by="ryan")
        assert store.open_actions() == []
        assert store.open_by_machine() == {}
        assert store.overdue(now=datetime(2030, 1, 1)) == []

    def test_to_dict_answers_overdue_at_the_moment_it_is_asked(self, gw):
        """CLAUDE.md's rule for anything derived from `now`: computed at
        request time, never stored — a stored copy is stale the next minute."""
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="CA-1",
                          due_at="2026-08-15")
        action = store.get("CA-1")
        assert action.to_dict(now=datetime(2026, 8, 14))["overdue"] is False
        assert action.to_dict(now=datetime(2026, 8, 20))["overdue"] is True

    def test_a_missing_table_answers_empty(self, bare):
        store = CorrectiveActionStore(bare)
        assert store.open_actions() == []
        assert store.open_by_machine() == {}
        assert store.overdue(now=datetime(2030, 1, 1)) == []


class TestTheDdlCarriesTheNewColumns:
    def test_the_columns_are_in_the_new_table_not_an_alter(self):
        """They go in NOW because these tables have not reached a field
        LabCore. After they ship, the same three columns are a SCHEMA_MIGRATIONS
        ALTER and a MAJOR release (RELEASING.md §2)."""
        joined = " ".join(HISTORY_DDL)
        assert "ALTER" not in joined.upper()
        actions = [d for d in HISTORY_DDL if "lem_corrective_actions" in d][0]
        for column in ("assigned_to TEXT", "due_at TEXT", "priority TEXT"):
            assert column in actions

    def test_the_column_list_and_the_dataclass_cannot_drift(self, gw):
        """They were kept in step by hand, with a literal `19` placeholders
        next to them."""
        import equipment_history
        from dataclasses import fields
        names = [f.strip() for f in
                 equipment_history._ACTION_COLUMNS.split(",")]
        assert names == [f.name for f in fields(CorrectiveAction)]

    def test_machine_uid_is_still_the_key(self):
        joined = " ".join(HISTORY_DDL)
        assert "machine_uid" in joined


# ═══════════════════════════════════════════════════════════════════════════
# Round three — the fresh critic. Every gateway answer now goes through
# `labcore_result`, the one rule this app has for "what did LabCore tell me?".
# ═══════════════════════════════════════════════════════════════════════════

from labcore_result import (  # noqa: E402
    LabCoreError, LabCoreRefused, LabCoreUnavailable)


class BlindGateway:
    """LabCore answering the way it does when it could not answer.

    The client returns the SAME `{"error": ...}` shape for a ConnectionError,
    for the 8-second read timeout this repo documents as routine, and for a
    queue refusal, as it does for a table that does not exist. That is the
    whole reason a store must not judge `res.get("error")` itself: it cannot
    tell "there is nothing there" from "I could not ask", and the second one
    read as the first is how a compliance record reads as clean.
    """

    def __init__(self, inner, table: str = ""):
        self.inner = inner
        self.table = table          # blind to one table, or "" for every read
        self.blind = False

    def read_sql(self, sql, args=None, **kw):
        if self.blind and (not self.table or self.table in sql):
            return {"error": "HTTPSConnectionPool(host='labvision.asaplabs."
                             "net', port=443): Read timed out."}
        return self.inner.read_sql(sql, args, **kw)

    def sql(self, sql, args=None, **kw):
        return self.inner.sql(sql, args, **kw)

    def is_running(self):
        return True


class RacingGateway:
    """A second operator whose whole call lands between this one's read and
    its write — the interleaving a read-then-write guard cannot survive."""

    def __init__(self, inner):
        self.inner = inner
        self.pending = None

    def sql(self, sql, args=None, **kw):
        if self.pending is not None and str(sql).strip().upper().startswith(
                "UPDATE"):
            run, self.pending = self.pending, None
            run()                   # the other operator finishes first
        return self.inner.sql(sql, args, **kw)

    def read_sql(self, *a, **k):
        return self.inner.read_sql(*a, **k)

    def is_running(self):
        return True


class UncountedGateway:
    """A LabCore that acknowledges a write without saying how many rows it
    matched.

    `rows_affected` is in the fake's contract, but the real client returns
    `resp.json()` verbatim from LabCore's queue. A compare-and-set that reads
    "0 rows matched" as "somebody beat me to it" would refuse every single
    update against a host that simply does not count — so the miss has to be
    confirmed against the record, not assumed from a missing key.
    """

    def __init__(self, inner):
        self.inner = inner

    def sql(self, *a, **k):
        res = dict(self.inner.sql(*a, **k))
        res.pop("rows_affected", None)
        return res

    def read_sql(self, *a, **k):
        return self.inner.read_sql(*a, **k)

    def is_running(self):
        return True


# ── BLOCKER: "could not ask" was being recorded as "nothing ever happened" ──

class TestCouldNotAskIsNeverNothingRecorded:
    """The critic's run, reproduced.

    With reads of `lem_corrective_actions` failing, a critical overdue assigned
    action yielded `open_actions() == []`, `overdue() == []`,
    `open_by_machine() == {}`, a timeline holding only the QC failure with
    `truncated=False` and `note=""`, and `close()` telling the operator
    `KeyError: No corrective action 'CA-1'`. Every one of those is this server
    certifying an emptiness it never established.
    """

    def seed(self, gw):
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="Sulfur QC read 12.4",
                          uid="CA-1", priority="critical", assigned_to="kaden",
                          due_at="2026-08-15", when="2026-08-11T09:00:00")
        return store

    def blinded(self, gw, table=""):
        blind = BlindGateway(gw, table)
        blind.blind = True
        return blind

    def test_the_open_list_says_it_could_not_be_read(self, gw):
        self.seed(gw)
        store = CorrectiveActionStore(self.blinded(gw))
        with pytest.raises(LabCoreUnavailable):
            store.open_actions()
        with pytest.raises(LabCoreUnavailable):
            store.unresolved()
        with pytest.raises(LabCoreUnavailable):
            store.open_by_machine()
        with pytest.raises(LabCoreUnavailable):
            store.overdue(now=datetime(2026, 8, 24, 9, 0))
        with pytest.raises(LabCoreUnavailable):
            store.for_machine("m1")

    def test_a_blip_is_not_no_such_action(self, gw):
        """The worst of them: an operator finishing a real corrective action is
        told the action does not exist, which is a sentence they will act on."""
        self.seed(gw)
        store = CorrectiveActionStore(self.blinded(gw))
        with pytest.raises(LabCoreUnavailable):
            store.get("CA-1")
        for call in (lambda: store.close("CA-1", by="ryan"),
                     lambda: store.record_action("CA-1", "did it"),
                     lambda: store.verify("CA-1", by="ryan"),
                     lambda: store.withdraw("CA-1", by="ryan", reason="dup"),
                     lambda: store.assign("CA-1", assigned_to="ryan")):
            with pytest.raises(LabCoreUnavailable):
                call()

    def test_the_correction_trail_says_so_too(self, gw):
        CorrectionAuditStore(gw).record("m1", "Sulfur", 0.0, -0.4)
        blind = self.blinded(gw, "lem_correction_audit")
        with pytest.raises(LabCoreUnavailable):
            CorrectionAuditStore(blind).history("m1")

    def test_the_timeline_never_certifies_a_degraded_read(self, gw):
        """`truncated` and `note` are a claim about completeness. A timeline
        that lost a source to a timeout and still says `truncated=False` is
        that claim made falsely, which about a compliance record is a lie."""
        self.seed(gw)
        TestEquipmentHistoryTimeline().seed_log(gw)
        seed_maintenance(gw)
        CorrectionAuditStore(gw).record("m1", "Sulfur", 0.0, -0.4)
        for table in ("lem_corrective_actions", "lem_correction_audit",
                      "lem_machine_log", "lem_maintenance",
                      "lem_action_events"):
            blind = self.blinded(gw, table)
            with pytest.raises(LabCoreUnavailable):
                EquipmentHistory(blind).timeline("m1")

    def test_the_machine_log_read_is_judged_here_not_by_the_reader(self, gw):
        """The whole app judges a read the same way now, and this test records
        that it did not always.

        `MachineStateReader.events` used to answer a failed read with `[]` — its
        own private rule, in a module this one does not own — so the timeline
        borrowed its QUERY and took the VERDICT back with `_JudgedRead`, or a
        blip would have read as an instrument with no history and the timeline
        would have certified that emptiness as complete.

        qc_specs was converted with the rest of the app, so the reader now
        raises on its own. `_JudgedRead` is therefore belt-and-braces rather
        than load-bearing — deliberately kept, because this module's guarantee
        is about ITS timeline and must not depend on another module continuing
        to agree. Both halves are asserted so that if either regresses, this
        fails and says which."""
        TestEquipmentHistoryTimeline().seed_log(gw)
        blind = self.blinded(gw, "lem_machine_log")
        from qc_specs import MachineStateReader
        with pytest.raises(LabCoreError):
            MachineStateReader(blind).events("m1", 5)            # now its rule too
        with pytest.raises(LabCoreUnavailable):
            EquipmentHistory(blind).timeline("m1")               # and ours

    def test_the_one_error_a_read_may_still_swallow(self, bare):
        """A table nobody has declared yet genuinely holds nothing — every
        `lem_*` table is created centrally at boot, so a read before that has
        run is looking at nothing rather than failing to look."""
        assert CorrectiveActionStore(bare).open_actions() == []
        assert CorrectiveActionStore(bare).open_by_machine() == {}
        assert CorrectiveActionStore(bare).for_machine("m1") == []
        assert CorrectiveActionStore(bare).get("CA-1") is None
        assert CorrectionAuditStore(bare).history("m1") == []
        assert EquipmentHistory(bare).timeline("m1") == []

    def test_a_write_against_a_table_that_is_not_there_says_which(self, bare):
        """The one place a missing table must NOT degrade: a write path. The
        operator is about to be told their corrective action was filed."""
        store = CorrectiveActionStore(bare)
        with pytest.raises(LabCoreUnavailable) as caught:
            store.record_action("CA-1", "did it")
        assert "lem_corrective_actions" in str(caught.value)

    def test_there_is_exactly_one_rule_in_the_codebase(self):
        """`HistoryWriteError` is a NAME for the shared refusal, not a second
        implementation of it, and the module's private judges are gone."""
        import equipment_history as eh
        assert eh.HistoryWriteError is LabCoreRefused
        assert not hasattr(eh, "_written")
        assert not hasattr(eh.CorrectiveActionStore, "_read")

    def test_a_queue_refusal_with_no_error_key_is_not_success(self, gw):
        """The shape LabCore's queue sends past 100 pending. `if not
        res.get("error")` reads it as done and tells the operator it was
        filed."""
        class Rejecting:
            def __init__(self, inner):
                self.inner = inner

            def sql(self, *a, **k):
                return {"ok": False, "status": "rejected", "pending": 100}

            def read_sql(self, *a, **k):
                return self.inner.read_sql(*a, **k)

            def is_running(self):
                return True

        with pytest.raises(HistoryWriteError):
            CorrectiveActionStore(Rejecting(gw)).open_action(
                "m1", what_happened="one")
        with pytest.raises(HistoryWriteError):
            CorrectionAuditStore(Rejecting(gw)).record("m1", "Sulfur", 0.0, 1.0)


# ── BLOCKER: a trigger that re-dates itself every time the bench prints ────

class TestTheTriggerNamesOneEvent:
    """`trigger_ref` was resolved against a QC standard's Lab ID.

    That Lab ID is on EVERY run of that standard — it is the standard's
    identity, not the run's — so the action was pinned to whichever run of it
    happened to be earliest in the window, and moved to a different one as
    older rows aged out. A compliance record that silently re-dates itself is
    worse than one carrying no link at all.
    """

    def log_row(self, gw, ts, value="12.4", lab_id="081124-4417"):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
               "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
               "detail TEXT)")
        gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
               ["m1", ts, "qc", lab_id, "Sulfur", value,
                '{"in_spec": false}'])
        return {"machine_uid": "m1", "ts": ts, "kind": "qc", "lab_id": lab_id,
                "test_name": "Sulfur", "value": value,
                "detail": '{"in_spec": false}'}

    def test_a_lab_id_never_draws_the_link(self, gw):
        """It cannot: it names the standard, and the standard is run daily."""
        from equipment_history import log_event_ref
        self.log_row(gw, "2026-08-10T09:00:00", value="9.9")
        self.log_row(gw, "2026-08-11T09:00:00", value="12.4")
        CorrectiveActionStore(gw).open_action(
            "m1", what_happened="Sulfur QC out of spec", uid="CA-1",
            trigger_kind="qc_fail", trigger_ref="081124-4417",
            when="2026-08-11T09:30:00")
        out = EquipmentHistory(gw).timeline("m1", newest_first=False)
        opened = [e for e in out if e.kind == "opened"][0]
        assert opened.caused_by == ""
        # and the ref is still shown as the evidence it is
        assert opened.detail["trigger_ref"] == "081124-4417"
        assert log_event_ref(self.log_row(gw, "2026-08-12T09:00:00")) != \
            "081124-4417"

    def test_the_link_is_made_at_open_time_from_the_event_itself(self, gw):
        from equipment_history import log_event_ref
        failing = self.log_row(gw, "2026-08-11T09:00:00", value="12.4")
        CorrectiveActionStore(gw).open_action(
            "m1", what_happened="Sulfur QC out of spec", uid="CA-1",
            trigger_kind="qc_fail", trigger_ref=log_event_ref(failing),
            when="2026-08-11T08:30:00")     # server clock half an hour behind
        out = EquipmentHistory(gw).timeline("m1", newest_first=False)
        assert [e.kind for e in out] == ["qc", "opened"]
        assert out[1].caused_by == out[0].uid

    def test_the_link_does_not_move_when_the_bench_prints_again(self, gw):
        """The defect stated as a test: the same standard runs again, and the
        action must still answer the run it was opened about."""
        from equipment_history import log_event_ref
        failing = self.log_row(gw, "2026-08-11T09:00:00", value="12.4")
        CorrectiveActionStore(gw).open_action(
            "m1", what_happened="Sulfur QC out of spec", uid="CA-1",
            trigger_kind="qc_fail", trigger_ref=log_event_ref(failing),
            when="2026-08-11T09:30:00")
        before = [e for e in EquipmentHistory(gw).timeline("m1")
                  if e.kind == "opened"][0].caused_by
        self.log_row(gw, "2026-08-09T09:00:00", value="9.8")   # an older run
        self.log_row(gw, "2026-08-12T09:00:00", value="9.9")   # a newer one
        after = [e for e in EquipmentHistory(gw).timeline("m1")
                 if e.kind == "opened"][0].caused_by
        assert after == before

    def test_the_ref_is_the_uid_a_reader_already_has(self, gw):
        """The UI stores the identity of the entry the operator clicked, so
        nothing has to be searched for afterwards."""
        from equipment_history import log_event_ref
        row = self.log_row(gw, "2026-08-11T09:00:00")
        assert log_entries([row])[0].uid == log_event_ref(row)

    def test_two_runs_a_second_apart_are_two_refs(self, gw):
        from equipment_history import log_event_ref
        a = self.log_row(gw, "2026-08-11T09:00:00", value="12.4")
        b = self.log_row(gw, "2026-08-11T09:00:01", value="12.4")
        c = self.log_row(gw, "2026-08-11T09:00:00", value="9.9")
        assert len({log_event_ref(a), log_event_ref(b), log_event_ref(c)}) == 3

    def test_a_ref_naming_nothing_in_this_window_is_left_alone(self, gw):
        from equipment_history import log_event_ref
        row = self.log_row(gw, "2020-01-01T09:00:00")
        CorrectiveActionStore(gw).open_action(
            "m1", what_happened="one", uid="CA-1", trigger_kind="qc_fail",
            trigger_ref=log_event_ref(row))
        gw.sql("DELETE FROM lem_machine_log")       # aged out of the window
        out = EquipmentHistory(gw).timeline("m1")
        assert [e.uid for e in out] == ["CA-1"]
        assert out[0].caused_by == ""

    def test_the_lab_id_search_is_gone(self):
        """Not deprecated — removed. A heuristic that is right most of the time
        is exactly what an auditor reads as a false link."""
        import equipment_history as eh
        assert not hasattr(eh, "trigger_index")


# ── MAJOR: reassignment left no record at all ─────────────────────────────

class TestReassignmentLeavesARecord:
    """`assign()` was overwrite-only, wrote no audit, appeared nowhere in the
    timeline, and silently discarded its own `by` argument — the precise defect
    this module's docstring cites as its reason to exist, committed inside it.
    """

    def opened(self, gw):
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="Sulfur QC out of spec",
                          uid="CA-1", assigned_to="kaden", due_at="2026-08-15",
                          priority="low", when="2026-08-11T09:00:00")
        return store

    def test_who_reassigned_it_and_from_what(self, gw):
        store = self.opened(gw)
        store.assign("CA-1", assigned_to="ryan", priority="critical",
                     by="supervisor", when="2026-08-12T09:00:00")
        events = store.events("CA-1")
        assert len(events) == 1
        record = events[0]
        assert record["kind"] == "assigned"
        assert record["by_user"] == "supervisor"
        assert record["at"] == "2026-08-12T09:00:00"
        assert "kaden" in record["note"] and "ryan" in record["note"]
        assert record["detail"]["assigned_to"] == ["kaden", "ryan"]
        assert record["detail"]["priority"] == ["low", "critical"]

    def test_every_reassignment_leaves_its_own_row(self, gw):
        """Append-only: the second reassignment does not replace the record of
        the first, or "who has had this" becomes "who has it"."""
        store = self.opened(gw)
        store.assign("CA-1", assigned_to="ryan", by="a",
                     when="2026-08-12T09:00:00")
        store.assign("CA-1", assigned_to="kaden", by="b",
                     when="2026-08-13T09:00:00")
        assert [e["by_user"] for e in store.events("CA-1")] == ["a", "b"]
        assert [e["detail"]["assigned_to"] for e in store.events("CA-1")] == [
            ["kaden", "ryan"], ["ryan", "kaden"]]

    def test_it_reaches_the_timeline(self, gw):
        store = self.opened(gw)
        store.assign("CA-1", due_at="2026-08-20", by="supervisor",
                     when="2026-08-12T09:00:00")
        out = EquipmentHistory(gw).timeline("m1", newest_first=False)
        assert [e.kind for e in out] == ["opened", "assigned"]
        assert out[1].who == "supervisor"
        assert out[1].caused_by == "CA-1"
        assert "2026-08-20" in out[1].summary

    def test_a_change_that_changes_nothing_records_nothing(self, gw):
        """Recording an assignment nobody made would put noise in the one
        place a supervisor goes to read what changed."""
        store = self.opened(gw)
        store.assign("CA-1", by="supervisor")
        assert store.events("CA-1") == []

    def test_it_validates_before_it_writes_anything(self, gw):
        """The gutted-implementation hole: `assign` accepting a priority the
        list cannot sort by, or a date nothing can read, and writing it."""
        store = self.opened(gw)
        for bad in ({"priority": "urgent"}, {"priority": "P1"},
                    {"due_at": "next week"}, {"due_at": "2026-13-45"}):
            with pytest.raises(ValueError):
                store.assign("CA-1", by="supervisor", **bad)
        again = store.get("CA-1")
        assert (again.assigned_to, again.due_at, again.priority) == (
            "kaden", "2026-08-15", "low")
        assert store.events("CA-1") == []

    def test_a_refused_audit_write_is_not_silent(self, gw):
        """Two ops for one operator action: the change, then the record of it.
        If the record is refused the operator is TOLD, because an unaudited
        reassignment is the thing this table exists to prevent."""
        class RefuseTheAudit:
            def __init__(self, inner):
                self.inner = inner

            def sql(self, sql, args=None, **kw):
                if "lem_action_events" in sql:
                    return {"error": "queue is full (100 pending)"}
                return self.inner.sql(sql, args, **kw)

            def read_sql(self, *a, **k):
                return self.inner.read_sql(*a, **k)

            def is_running(self):
                return True

        self.opened(gw)
        store = CorrectiveActionStore(RefuseTheAudit(gw))
        with pytest.raises(HistoryWriteError):
            store.assign("CA-1", assigned_to="ryan", by="supervisor")


class TestSomethingCanBeSaidAboutAnActionInFlight:
    """MINOR: there was nowhere to record anything about an action after it was
    verified and before it was closed — no note, no comment, no follow-up."""

    def opened(self, gw):
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="CA-1",
                          when="2026-08-11T09:00:00")
        return store

    def test_a_note_between_verified_and_closed(self, gw):
        store = self.opened(gw)
        store.record_action("CA-1", "Reconditioned", when="2026-08-11T10:00:00")
        store.verify("CA-1", by="ryan", when="2026-08-12T08:00:00")
        store.add_note("CA-1", "Waiting on the second QC run before closing",
                       by="ryan", when="2026-08-12T09:00:00")
        assert [e["note"] for e in store.events("CA-1")] == [
            "Waiting on the second QC run before closing"]
        assert store.get("CA-1").state == "verified"

    def test_a_note_never_restates_what_happened(self, gw):
        """It is append-only and it is its own row: nothing it holds can
        overwrite a date, a name or an outcome. That is why it is allowed on a
        finished action, where a cross-reference to a recurrence belongs."""
        store = self.opened(gw)
        store.withdraw("CA-1", by="kaden", reason="duplicate")
        store.add_note("CA-1", "See CA-9", by="ryan")
        again = store.get("CA-1")
        assert again.state == "withdrawn"
        assert again.closed_note == "duplicate"
        assert [e["note"] for e in store.events("CA-1")] == ["See CA-9"]

    def test_an_empty_note_is_refused(self, gw):
        store = self.opened(gw)
        with pytest.raises(ValueError):
            store.add_note("CA-1", "   ", by="ryan")
        assert store.events("CA-1") == []

    def test_a_note_on_an_action_that_does_not_exist_is_refused(self, gw):
        with pytest.raises(KeyError):
            CorrectiveActionStore(gw).add_note("nope", "hello", by="ryan")

    def test_notes_reach_the_timeline_in_order(self, gw):
        store = self.opened(gw)
        store.add_note("CA-1", "first", by="ryan", when="2026-08-11T10:00:00")
        store.add_note("CA-1", "second", by="ryan", when="2026-08-11T11:00:00")
        out = EquipmentHistory(gw).timeline("m1", newest_first=False)
        assert [e.summary for e in out][1:] == ["Note — first", "Note — second"]

    def test_notes_are_scoped_to_the_instrument(self, gw):
        store = self.opened(gw)
        store.open_action("m2", what_happened="elsewhere", uid="CA-2")
        store.add_note("CA-2", "not m1's", by="ryan")
        assert EquipmentHistory(gw).timeline("m1") == [
            e for e in EquipmentHistory(gw).timeline("m1")
            if e.machine_uid == "m1"]
        assert all(e.machine_uid == "m1"
                   for e in EquipmentHistory(gw).timeline("m1"))

    def test_a_refused_note_raises(self, gw):
        gate = RefusingGateway(gw)
        store = CorrectiveActionStore(gate)
        store.open_action("m1", what_happened="one", uid="CA-9")
        gate.refusing = True
        with pytest.raises(HistoryWriteError):
            store.add_note("CA-9", "hello", by="ryan")


# ── MAJOR: the lifecycle guard could not survive two operators ─────────────

class TestTwoOperatorsAtOnce:
    """The guard was read-then-write with NO precondition on the UPDATE, so
    both operators passed the check and the second overwrote a finished
    compliance record — the exact overwrite the lifecycle exists to refuse,
    reachable by two people clicking at the same time."""

    def verified(self, gw):
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="Sulfur QC out of spec",
                          uid="CA-1", when="2026-08-11T09:00:00")
        store.record_action("CA-1", "Reconditioned", when="2026-08-11T10:00:00")
        store.verify("CA-1", by="ryan", note="Back in band",
                     when="2026-08-11T11:00:00")
        return store

    def test_the_second_close_loses_and_is_told(self, gw):
        first = self.verified(gw)
        race = RacingGateway(gw)
        second = CorrectiveActionStore(race)
        race.pending = lambda: first.close(
            "CA-1", by="ryan", note="Back in service",
            when="2026-08-12T08:00:00")
        with pytest.raises(ActionLifecycleError):
            second.close("CA-1", by="kaden", note="closed by me",
                         when="2026-08-12T09:00:00")
        again = first.get("CA-1")
        assert (again.closed_by, again.closed_note, again.closed_at) == (
            "ryan", "Back in service", "2026-08-12T08:00:00")

    def test_a_withdrawal_racing_a_close(self, gw):
        first = self.verified(gw)
        race = RacingGateway(gw)
        second = CorrectiveActionStore(race)
        race.pending = lambda: first.withdraw(
            "CA-1", by="ryan", reason="duplicate of CA-0",
            when="2026-08-12T08:00:00")
        with pytest.raises(ActionLifecycleError):
            second.close("CA-1", by="kaden", when="2026-08-12T09:00:00")
        assert first.get("CA-1").outcome == "withdrawn"

    def test_an_edit_racing_a_verification(self, gw):
        """The verification attests to the action AS RECORDED. An edit landing
        after it leaves a verification pointing at work nobody checked."""
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="CA-1",
                          when="2026-08-11T09:00:00")
        store.record_action("CA-1", "Reconditioned", when="2026-08-11T10:00:00")
        race = RacingGateway(gw)
        second = CorrectiveActionStore(race)
        race.pending = lambda: store.verify("CA-1", by="ryan",
                                            when="2026-08-11T11:00:00")
        with pytest.raises(ActionLifecycleError):
            second.record_action("CA-1", "Actually we replaced the cell",
                                 when="2026-08-11T11:30:00")
        assert store.get("CA-1").action_taken == "Reconditioned"

    def test_a_reassignment_racing_a_close(self, gw):
        first = self.verified(gw)
        race = RacingGateway(gw)
        second = CorrectiveActionStore(race)
        race.pending = lambda: first.close("CA-1", by="ryan",
                                           when="2026-08-12T08:00:00")
        with pytest.raises(ActionLifecycleError):
            second.assign("CA-1", assigned_to="kaden", by="supervisor")
        assert first.get("CA-1").assigned_to == ""
        assert second.events("CA-1") == []      # and nothing was audited

    def test_the_loser_is_told_what_it_is_now(self, gw):
        first = self.verified(gw)
        race = RacingGateway(gw)
        second = CorrectiveActionStore(race)
        race.pending = lambda: first.close("CA-1", by="ryan",
                                           when="2026-08-12T08:00:00")
        with pytest.raises(ActionLifecycleError) as caught:
            second.close("CA-1", by="kaden")
        message = str(caught.value)
        assert "CA-1" in message
        assert "closed" in message

    def test_a_labcore_that_does_not_count_rows_still_works(self, gw):
        """`rows_affected` is the fake's contract; the real client hands back
        whatever the queue sent. Reading "no count" as "somebody beat me to it"
        would refuse every update in the lab."""
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="CA-1",
                          when="2026-08-11T09:00:00")
        quiet = CorrectiveActionStore(UncountedGateway(gw))
        quiet.record_action("CA-1", "Reconditioned", when="2026-08-11T10:00:00")
        quiet.verify("CA-1", by="ryan", when="2026-08-11T11:00:00")
        quiet.close("CA-1", by="ryan", when="2026-08-11T12:00:00")
        assert store.get("CA-1").state == "closed"

    def test_an_uncounting_labcore_still_loses_the_race(self, gw):
        """And the confirmation is against the RECORD, so it does not become a
        way to win a race by not counting."""
        first = self.verified(gw)
        race = RacingGateway(UncountedGateway(gw))
        second = CorrectiveActionStore(race)
        race.pending = lambda: first.close("CA-1", by="ryan",
                                           note="Back in service",
                                           when="2026-08-12T08:00:00")
        with pytest.raises(ActionLifecycleError):
            second.close("CA-1", by="kaden", when="2026-08-12T09:00:00")
        assert first.get("CA-1").closed_by == "ryan"


# ── MINORS ────────────────────────────────────────────────────────────────

class TestTheOverdueClockClaimIsHonest:
    """`is_overdue` claimed "no bench writes a corrective action, so a bench
    clock cannot make one overdue" — a guarantee nothing enforces. LabCore has
    no foreign keys and no writer table; anything with the gateway can write
    this row. What IS true is what the code does, and that is testable."""

    def test_an_offset_bearing_due_date_is_read_on_the_servers_axis(self, gw):
        from datetime import timedelta, timezone
        store = CorrectiveActionStore(gw)
        local = datetime(2026, 8, 15, 17, 0, 0)
        elsewhere = local.astimezone().astimezone(timezone(timedelta(hours=-7)))
        store.open_action("m1", what_happened="one", uid="CA-1",
                          due_at=elsewhere.isoformat())
        action = store.get("CA-1")
        assert action.due_datetime() == local
        assert action.is_overdue(local - timedelta(minutes=1)) is False
        assert action.is_overdue(local + timedelta(minutes=1)) is True

    def test_a_due_date_written_by_something_else_is_still_compared(self, gw):
        """Not refused, not repaired, not assumed to be the server's: read on
        the only axis this process has."""
        store = CorrectiveActionStore(gw)
        store.open_action("m1", what_happened="one", uid="CA-1")
        gw.sql("UPDATE lem_corrective_actions SET due_at = ? WHERE uid = ?",
               ["2026-08-15T17:00:00.123456", "CA-1"])
        assert store.get("CA-1").is_overdue(datetime(2026, 8, 15, 18)) is True


class TestTheDeclarationIsHonestAndComplete:
    """The DDL is applied by NOTHING today. The tests that guarded it were
    text greps over the strings, which a gutted implementation survives."""

    def test_the_tables_the_stores_actually_write_are_the_ones_declared(
            self, bare):
        """Behavioural: apply exactly HISTORY_DDL, then use every write path.
        A statement missing from the tuple fails here, which a grep cannot."""
        for ddl in HISTORY_DDL:
            bare.sql(ddl)
        store = CorrectiveActionStore(bare)
        store.open_action("m1", what_happened="one", uid="CA-1",
                          assigned_to="kaden", due_at="2026-08-15",
                          priority="high")
        store.assign("CA-1", assigned_to="ryan", by="supervisor")
        store.add_note("CA-1", "a note", by="ryan")
        CorrectionAuditStore(bare).record("m1", "Sulfur", 0.0, -0.4)
        assert store.get("CA-1").assigned_to == "ryan"
        assert len(store.events("CA-1")) == 2
        assert len(CorrectionAuditStore(bare).history("m1")) == 1

    def test_every_dataclass_field_is_a_real_column(self, bare):
        """The INSERT names its columns and then binds values positionally, so
        a name in one and not the other is a shifted row that SQLite accepts
        without a word. Names, not order: the column list is generated from the
        dataclass, so the two orders are free to differ and the SELECT names
        them too."""
        import equipment_history as eh
        for ddl in HISTORY_DDL:
            bare.sql(ddl)
        res = bare.read_sql(
            "SELECT name FROM pragma_table_info('lem_corrective_actions')")
        assert set(r["name"] for r in res["rows"]) == set(eh._ACTION_FIELDS)

    def test_declaring_it_twice_changes_nothing(self, gw):
        """It is `IF NOT EXISTS` for a reason: the wiring phase pastes it into
        a boot path that runs on every restart, and the tray restarts this
        server on every code edit."""
        CorrectiveActionStore(gw).open_action("m1", what_happened="one",
                                              uid="CA-1")
        for ddl in HISTORY_DDL:
            gw.sql(ddl)
        assert CorrectiveActionStore(gw).get("CA-1") is not None

    def test_nothing_here_applies_it(self, gw):
        """Stated in the present tense in the docstring, so it is stated here
        too: reading a history writes nothing, including no DDL."""
        counted = CountingGateway(gw)
        history = EquipmentHistory(counted)
        history.timeline("m1")
        history.actions.open_actions()
        history.corrections.history("m1")
        assert counted.writes == 0


class TestAmendingWhatWasDoneKeepsWhoSaidItFirst:
    """`record_action` is rewritable on purpose — the note is typed mid-job and
    finished later. What it must not do is erase the previous author and time.

    Reproduced: kaden records "Reconditioned the cell" at 10:00, ryan records
    "Replaced the cell" at 15:00, and the record afterwards says only that ryan
    replaced it at 15:00. Kaden's account of what was done is gone, and
    `events()` is empty, so nothing anywhere says it ever existed.

    This is the identical defect the module docstring cites about
    `lem_correction_factors` as its whole reason to exist, committed inside it —
    and it is the one field where it matters most, because "what was actually
    done" is the sentence an auditor reads.
    """

    def _opened(self, gw):
        store = CorrectiveActionStore(gw)
        return store, store.open_action("m1", "Cloud Point out of band",
                                        trigger_kind="qc_fail", by="kaden")

    def test_the_first_recording_audits_nothing(self, gw):
        store, action = self._opened(gw)
        store.record_action(action.uid, "Reconditioned the cell", by="kaden",
                            when="2026-08-11T10:00:00")
        amendments = [e for e in store.events(action.uid)
                      if e["kind"] == "amended"]
        assert amendments == [], "nothing was overwritten, so nothing to record"

    def test_an_amendment_keeps_the_previous_account(self, gw):
        store, action = self._opened(gw)
        store.record_action(action.uid, "Reconditioned the cell", by="kaden",
                            when="2026-08-11T10:00:00")
        store.record_action(action.uid, "Replaced the cell", by="ryan",
                            when="2026-08-11T15:00:00")

        amendments = [e for e in store.events(action.uid)
                      if e["kind"] == "amended"]
        assert len(amendments) == 1, "the overwrite left no trace"
        was = amendments[0]["detail"]
        assert was["action_taken"]["from"] == "Reconditioned the cell"
        assert was["action_by"]["from"] == "kaden"
        assert was["action_at"]["from"] == "2026-08-11T10:00:00"
        assert amendments[0]["by_user"] == "ryan"

    def test_the_current_record_is_still_the_latest(self, gw):
        store, action = self._opened(gw)
        store.record_action(action.uid, "Reconditioned the cell", by="kaden",
                            when="2026-08-11T10:00:00")
        store.record_action(action.uid, "Replaced the cell", by="ryan",
                            when="2026-08-11T15:00:00")
        now = store.get(action.uid)
        assert now.action_taken == "Replaced the cell"
        assert now.action_by == "ryan"

    def test_retyping_the_same_text_records_nothing(self, gw):
        """Re-posting an unchanged form is not an amendment."""
        store, action = self._opened(gw)
        store.record_action(action.uid, "Reconditioned the cell", by="kaden",
                            when="2026-08-11T10:00:00")
        store.record_action(action.uid, "Reconditioned the cell", by="kaden",
                            when="2026-08-11T10:00:00")
        amendments = [e for e in store.events(action.uid)
                      if e["kind"] == "amended"]
        assert amendments == []

    def test_the_amendment_reaches_the_timeline(self, gw):
        store, action = self._opened(gw)
        store.record_action(action.uid, "Reconditioned the cell", by="kaden",
                            when="2026-08-11T10:00:00")
        store.record_action(action.uid, "Replaced the cell", by="ryan",
                            when="2026-08-11T15:00:00")
        said = " ".join(e["note"] for e in store.events(action.uid))
        assert "Reconditioned the cell" in said, \
            "the superseded account must be readable, not just flagged"


class TestARefusedAuditSaysWhatActuallyHappened:
    """Two tables and no transaction: `assign` changes the row, then records who
    made the change. If the second write is refused the first has already
    landed, and the bare error reads as "nothing happened".

    It is not nothing. A retry finds the values already current, computes no
    changes, and returns success having written nothing — so the reassignment is
    permanently unaudited and no message anywhere says so. The error has to name
    that, because it is the only moment anyone can act on it.
    """

    def test_the_error_says_the_change_landed(self, gw):
        class RefuseTheAudit(FakeLabCoreGateway):
            def sql(self, sql, args=None, **kw):
                if "INSERT INTO lem_action_events" in sql:
                    return {"error": "queue full"}
                return super().sql(sql, args, **kw)

        for ddl in HISTORY_DDL:
            FakeLabCoreGateway.sql(gw, ddl)
        refusing = RefuseTheAudit()
        for ddl in HISTORY_DDL:
            FakeLabCoreGateway.sql(refusing, ddl)
        store = CorrectiveActionStore(refusing)
        action = store.open_action("m1", "Cloud Point out of band", by="kaden")

        with pytest.raises(HistoryWriteError) as caught:
            store.assign(action.uid, assigned_to="ryan", by="kaden")

        said = str(caught.value).lower()
        assert "saved" in said and "unaudited" in said, \
            "the operator must be told the change is live but unrecorded"

    def test_the_reassignment_really_did_land(self, gw):
        class RefuseTheAudit(FakeLabCoreGateway):
            def sql(self, sql, args=None, **kw):
                if "INSERT INTO lem_action_events" in sql:
                    return {"error": "queue full"}
                return super().sql(sql, args, **kw)

        refusing = RefuseTheAudit()
        for ddl in HISTORY_DDL:
            FakeLabCoreGateway.sql(refusing, ddl)
        store = CorrectiveActionStore(refusing)
        action = store.open_action("m1", "Cloud Point out of band", by="kaden")
        with pytest.raises(HistoryWriteError):
            store.assign(action.uid, assigned_to="ryan", by="kaden")
        assert store.get(action.uid).assigned_to == "ryan", \
            "the error must describe reality, not the opposite of it"
