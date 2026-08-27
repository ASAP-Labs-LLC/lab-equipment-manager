#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`lem_uncertainty_estimates` — frozen, superseded, never recomputed in place.

Spec gap 4: *"An annual review needs 'as of 2026-08-25, from these 84 results,
u_c was X, approved by Y.' A number recomputed on every page load is not a
record — the inputs move under it."*

So: written once. A revision is a NEW row that sets `superseded_by` on the old
one, and an assessor walks backwards through the chain.
"""

import json
from datetime import datetime

import pytest

import labcore_result
import qc_series
import uncertainty
from labcore_gateway import FakeLabCoreGateway
from test_uncertainty_fixtures import qc_row, qc_rows

NOW = datetime(2026, 8, 27, 9, 0, 0)
UID = "mach-1"
TEST = "Cloud Point"

# 29 results over 15 days, two analysts, two calibration epochs — the healthy
# end of what this laboratory's log actually holds.
ENTRIES = [("2026-08-{:02d}T{:02d}:00:00".format(3 + (i % 15), 8 + (i % 8)),
            -7.4 + ((i % 7) - 3) * 0.4,
            "Ryan" if i % 2 else "Dana",
            "2026-08-01T09:00:00" if i < 14 else "2026-08-14T09:00:00")
           for i in range(29)]


@pytest.fixture
def gateway():
    gw = FakeLabCoreGateway()
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
           "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
           "detail TEXT)")
    for row in qc_rows(UID, TEST, ENTRIES, lab_id="CP"):
        gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
               "test_name, value, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
               [row["machine_uid"], row["ts"], row["kind"], row["lab_id"],
                row["test_name"], row["value"], row["detail"]])
    return gw


@pytest.fixture
def store(gateway):
    st = uncertainty.UncertaintyStore(gateway)
    st.ensure_schema()
    return st


def _interim(store, **kw):
    """The estimate this laboratory can actually make today."""
    kw.setdefault("rw_route", uncertainty.RW_TARGET_LIMITS)
    kw.setdefault("control_limit", 2.8)
    kw.setdefault("control_limit_k", 1.0)
    kw.setdefault("now", NOW)
    return store.compute(UID, TEST, **kw)


class _Refusing:
    """LabCore's evidenced refusal: an error dict, ANSWERED, never raised."""

    REFUSAL = {"error": "LabCore is busy, try again shortly", "busy": True,
               "retry_after": 5}

    def __init__(self, inner, on_write=True, on_read=False):
        self.inner, self.on_write, self.on_read = inner, on_write, on_read

    def sql(self, sql, args=None, **kw):
        if self.on_write and not sql.lstrip().upper().startswith("CREATE"):
            return dict(self.REFUSAL)
        return self.inner.sql(sql, args, **kw)

    def read_sql(self, sql, args=None, **kw):
        if self.on_read:
            return dict(self.REFUSAL)
        return self.inner.read_sql(sql, args, **kw)


class TestReadingTheSeries:

    def test_the_input_is_the_qc_rows_and_only_the_qc_rows(self, store, gateway):
        gateway.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, "
                    "lab_id, test_name, value, detail) VALUES (?,?,?,?,?,?,?)",
                    [UID, "2026-08-20T10:00:00", "pm", "", TEST, "0.0",
                     json.dumps({"low": 0, "high": 0.001})])
        got = store.read_series(UID, TEST)
        assert len(got.points) == 29
        assert all(p.test_name == TEST for p in got.points)

    def test_it_is_a_qc_series_QcSeries_and_not_a_second_type(self, store):
        assert isinstance(store.read_series(UID, TEST), qc_series.QcSeries)
        assert uncertainty.QcSeries is qc_series.QcSeries

    def test_a_window_narrows_it(self, store):
        got = store.read_series(UID, TEST,
                                window_start=datetime(2026, 8, 10),
                                window_end=datetime(2026, 8, 18))
        assert 0 < len(got.points) < 29
        assert all(datetime(2026, 8, 10) <= p.at < datetime(2026, 8, 18)
                   for p in got.points)

    def test_an_unknown_test_is_an_empty_series_not_an_error(self, store):
        assert store.read_series(UID, "Nothing Here").points == ()

    def test_a_failed_read_is_never_an_empty_series(self, gateway):
        st = uncertainty.UncertaintyStore(_Refusing(gateway, on_write=False,
                                                    on_read=True))
        with pytest.raises(labcore_result.LabCoreError):
            st.read_series(UID, TEST)


class TestFrozen:

    def test_a_saved_estimate_reads_back_identical(self, store):
        est = _interim(store)
        est_id = store.save(est, computed_by="ryan@asaplabs.com")
        back = store.get(est_id)
        assert back is not None
        before = est.to_dict()
        before["computed_by"] = "ryan@asaplabs.com"
        assert back.to_dict() == before

    def test_every_number_survives_the_round_trip_exactly(self, store):
        est = _interim(store, certificate=uncertainty.Certificate(
            value=-7.4, uncertainty=0.6, k=2.0, number="COA-1"),
            astm_r=5.56)
        back = store.get(store.save(est, computed_by="ryan"))
        for field in ("mean", "s", "u_rw", "bias", "u_cref", "u_bias", "u_c",
                      "u_expanded", "astm_r", "r_ratio", "control_limit"):
            assert getattr(back, field) == getattr(est, field), field

    def test_saving_the_same_estimate_twice_is_refused(self, store):
        est = _interim(store)
        store.save(est, computed_by="ryan")
        with pytest.raises(labcore_result.LabCoreError):
            store.save(est, computed_by="ryan")

    def test_the_store_issues_no_UPDATE_of_any_computed_field(self):
        """Written once. The only UPDATEs in this module are the three that
        record a HUMAN act: approval, supersession, and nothing else."""
        source = open(uncertainty.__file__, encoding="utf-8").read()
        updates = [line.strip() for line in source.splitlines()
                   if "UPDATE lem_uncertainty_estimates" in line]
        assert updates, "expected the approve/supersede statements"
        joined = " ".join(updates).lower()
        for frozen in ("u_c", "u_rw", "u_bias", "mean =", " s =", "u_expanded",
                       "r_ratio", "n ="):
            assert frozen not in joined, frozen
        assert "on conflict" not in source.lower()
        assert "insert or replace" not in source.lower()

    def test_computed_at_is_the_moment_of_computation_not_of_saving(self, store):
        est = _interim(store)
        assert est.computed_at.startswith("2026-08-27T09:00")
        back = store.get(store.save(est, computed_by="ryan"))
        assert back.computed_at == est.computed_at


class TestApproval:

    def test_approve_sets_both_fields(self, store):
        est_id = store.save(_interim(store), computed_by="ryan")
        store.approve(est_id, approved_by="tech.manager",
                      when=datetime(2026, 8, 28, 10, 0, 0))
        back = store.get(est_id)
        assert back.approved_by == "tech.manager"
        assert back.approved_at.startswith("2026-08-28T10:00")

    def test_an_unapproved_estimate_never_reports_as_current(self, store):
        store.save(_interim(store), computed_by="ryan")
        assert store.current_for(UID, TEST) is None
        assert store.list_current() == []

    def test_an_approved_estimate_does(self, store):
        est_id = store.save(_interim(store), computed_by="ryan")
        store.approve(est_id, approved_by="tech.manager")
        current = store.current_for(UID, TEST)
        assert current is not None and current.estimate_id == est_id
        assert [e.estimate_id for e in store.list_current()] == [est_id]

    def test_approving_twice_is_refused_rather_than_rewriting_the_signature(
            self, store):
        est_id = store.save(_interim(store), computed_by="ryan")
        store.approve(est_id, approved_by="tech.manager")
        with pytest.raises(uncertainty.EstimateRefused):
            store.approve(est_id, approved_by="somebody.else")
        assert store.get(est_id).approved_by == "tech.manager"

    def test_approving_something_that_is_not_there_says_so(self, store):
        with pytest.raises(uncertainty.EstimateRefused):
            store.approve("no-such-id", approved_by="tech.manager")

    def test_a_refused_approval_is_not_reported_as_done(self, store, gateway):
        est_id = store.save(_interim(store), computed_by="ryan")
        blocked = uncertainty.UncertaintyStore(_Refusing(gateway))
        with pytest.raises(labcore_result.LabCoreRefused):
            blocked.approve(est_id, approved_by="tech.manager")
        assert store.get(est_id).approved_by == ""


class TestSupersession:

    def test_a_new_estimate_sets_superseded_by_on_the_old_one(self, store):
        old = store.save(_interim(store), computed_by="ryan")
        store.approve(old, approved_by="tm")
        new = store.save(_interim(store, control_limit=3.0), computed_by="ryan")
        store.supersede(old, new)
        assert store.get(old).superseded_by == new
        assert store.get(new).superseded_by == ""

    def test_a_superseded_estimate_is_no_longer_current_even_if_approved(
            self, store):
        old = store.save(_interim(store), computed_by="ryan")
        store.approve(old, approved_by="tm")
        new = store.save(_interim(store, control_limit=3.0), computed_by="ryan")
        store.approve(new, approved_by="tm")
        store.supersede(old, new)
        assert store.current_for(UID, TEST).estimate_id == new

    def test_an_assessor_can_walk_backwards(self, store):
        first = store.save(_interim(store), computed_by="ryan")
        second = store.save(_interim(store, control_limit=3.0),
                            computed_by="ryan")
        third = store.save(_interim(store, control_limit=3.2),
                           computed_by="ryan")
        store.supersede(first, second)
        store.supersede(second, third)
        chain = store.history_for(UID, TEST)
        assert [e.estimate_id for e in chain] == [third, second, first]
        assert [e.estimate_id for e in store.predecessors(third)] == [
            second, first]
        assert [e.estimate_id for e in store.predecessors(second)] == [first]
        assert store.predecessors(first) == []

    def test_superseding_by_something_that_does_not_exist_is_refused(self, store):
        old = store.save(_interim(store), computed_by="ryan")
        with pytest.raises(uncertainty.EstimateRefused):
            store.supersede(old, "no-such-id")
        assert store.get(old).superseded_by == ""

    def test_an_estimate_cannot_supersede_itself(self, store):
        old = store.save(_interim(store), computed_by="ryan")
        with pytest.raises(uncertainty.EstimateRefused):
            store.supersede(old, old)


class TestExclusions:
    """SOP 2.9 / TR 537: a point is dropped only when its cause is known."""

    CAUSE = uncertainty.Exclusion(
        ts=ENTRIES[3][0], value=ENTRIES[3][1],
        cause="Autosampler vial was under-filled; confirmed on the tray photo.",
        ncr_ref="NCR-2026-014")

    def test_an_exclusion_without_a_cause_is_refused(self, store):
        est_id = store.save(_interim(store), computed_by="ryan")
        naked = uncertainty.Exclusion(ts=ENTRIES[3][0], value=ENTRIES[3][1],
                                      cause="", ncr_ref="NCR-2026-014")
        with pytest.raises(uncertainty.EstimateRefused) as caught:
            store.exclude(est_id, naked, computed_by="ryan", now=NOW)
        assert "cause" in str(caught.value).lower()

    def test_statistical_extremity_is_not_a_cause(self, store):
        est_id = store.save(_interim(store), computed_by="ryan")
        for excuse in ("outlier", "  outlier ", "3s", "> 3s", "Outlier."):
            bad = uncertainty.Exclusion(ts=ENTRIES[3][0], value=ENTRIES[3][1],
                                        cause=excuse, ncr_ref="NCR-1")
            with pytest.raises(uncertainty.EstimateRefused):
                store.exclude(est_id, bad, computed_by="ryan", now=NOW)

    def test_an_exclusion_without_a_nonconforming_work_reference_is_refused(
            self, store):
        est_id = store.save(_interim(store), computed_by="ryan")
        naked = uncertainty.Exclusion(
            ts=ENTRIES[3][0], value=ENTRIES[3][1],
            cause="Autosampler vial under-filled.", ncr_ref="")
        with pytest.raises(uncertainty.EstimateRefused) as caught:
            store.exclude(est_id, naked, computed_by="ryan", now=NOW)
        assert "7.10" in str(caught.value) or "nonconform" in str(
            caught.value).lower()

    def test_a_good_exclusion_creates_a_NEW_estimate_and_supersedes_the_old(
            self, store):
        old_id = store.save(_interim(store), computed_by="ryan")
        store.approve(old_id, approved_by="tm")
        new = store.exclude(old_id, self.CAUSE, computed_by="ryan", now=NOW)
        assert new.estimate_id != old_id
        assert store.get(old_id).superseded_by == new.estimate_id
        assert store.get(new.estimate_id) is not None

    def test_the_old_estimates_numbers_are_untouched(self, store):
        old_id = store.save(_interim(store), computed_by="ryan")
        before = store.get(old_id).to_dict()
        store.exclude(old_id, self.CAUSE, computed_by="ryan", now=NOW)
        after = store.get(old_id).to_dict()
        after["superseded_by"] = before["superseded_by"]
        assert after == before

    def test_the_new_estimate_is_a_draft_and_never_inherits_approval(self, store):
        old_id = store.save(_interim(store), computed_by="ryan")
        store.approve(old_id, approved_by="tm")
        new = store.exclude(old_id, self.CAUSE, computed_by="ryan", now=NOW)
        assert new.approved_by == "" and new.approved_at == ""
        assert store.current_for(UID, TEST) is None

    def test_the_excluded_point_is_out_of_the_statistics_and_in_the_record(
            self, store):
        old_id = store.save(
            store.compute(UID, TEST, now=NOW,
                          short_series_justification="interim, tested"),
            computed_by="ryan")
        old = store.get(old_id)
        new = store.exclude(old_id, self.CAUSE, computed_by="ryan", now=NOW)
        assert new.n == old.n - 1
        assert new.s != old.s
        assert len(new.exclusions) == 1
        kept = new.exclusions[0]
        assert kept["cause"] == self.CAUSE.cause
        assert kept["ncr_ref"] == "NCR-2026-014"
        assert kept["ts"] == self.CAUSE.ts

    def test_exclusions_accumulate_across_revisions(self, store):
        first_id = store.save(
            store.compute(UID, TEST, now=NOW,
                          short_series_justification="interim, tested"),
            computed_by="ryan")
        second = store.exclude(first_id, self.CAUSE, computed_by="ryan", now=NOW)
        another = uncertainty.Exclusion(
            ts=ENTRIES[9][0], value=ENTRIES[9][1],
            cause="Wrong standard loaded; confirmed against the bench log.",
            ncr_ref="NCR-2026-015")
        third = store.exclude(second.estimate_id, another, computed_by="ryan",
                              now=NOW)
        assert len(third.exclusions) == 2
        assert third.n == 27

    def test_the_same_point_cannot_be_excluded_twice(self, store):
        first_id = store.save(
            store.compute(UID, TEST, now=NOW,
                          short_series_justification="interim, tested"),
            computed_by="ryan")
        second = store.exclude(first_id, self.CAUSE, computed_by="ryan", now=NOW)
        with pytest.raises(uncertainty.EstimateRefused):
            store.exclude(second.estimate_id, self.CAUSE, computed_by="ryan",
                          now=NOW)

    def test_there_is_no_automatic_rejection_anywhere(self, store):
        """TR 537 and SOP 2.9 both forbid it. Candidates are FLAGGED."""
        source = open(uncertainty.__file__, encoding="utf-8").read()
        assert "def exclusion_candidates(" in source
        est = _interim(store)
        assert est.exclusions == []

    def test_candidates_are_offered_and_never_applied(self, store):
        rows = qc_rows(UID, TEST, ENTRIES) + [
            qc_row(UID, TEST, "2026-08-19T08:00:00", 99.0, operator="Ryan",
                   calibration_id="2026-08-14T09:00:00")]
        wide = qc_series.series_for(rows, UID, TEST)
        candidates = uncertainty.exclusion_candidates(wide)
        assert [c.value for c in candidates] == [99.0]
        assert "cause" in candidates[0].why.lower()
        assert "investigat" in candidates[0].why.lower()
        # And computing over the same series keeps the point.
        est = uncertainty.compute_from_series(
            wide, rw_route=uncertainty.RW_TARGET_LIMITS, control_limit=2.8,
            now=NOW)
        assert est.n == 30

    def test_a_clean_series_offers_no_candidates(self, store):
        assert uncertainty.exclusion_candidates(store.read_series(UID, TEST)) == []


class TestStaleness:
    """SOP 2.11. LEM already emits four of the seven as log rows."""

    def _trigger(self, gateway, kind, ts, action="", uid=UID):
        gateway.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, "
                    "lab_id, test_name, value, detail) VALUES (?,?,?,?,?,?,?)",
                    [uid, ts, kind, "", action, "",
                     json.dumps({"action": action}) if action else "{}"])

    @pytest.fixture
    def approved(self, store):
        est_id = store.save(_interim(store), computed_by="ryan")
        store.approve(est_id, approved_by="tm",
                      when=datetime(2026, 8, 27, 12, 0, 0))
        return est_id

    def test_a_calibration_newer_than_computed_at_makes_it_stale(
            self, store, gateway, approved):
        self._trigger(gateway, "calibration", "2026-08-28T08:00:00")
        stale = store.stale(now=datetime(2026, 8, 29))
        assert [s.estimate_id for s in stale] == [approved]
        assert stale[0].trigger == "calibration"
        assert stale[0].at == "2026-08-28T08:00:00"
        assert "calibration" in stale[0].sentence.lower()

    @pytest.mark.parametrize("kind", ["calibration", "pm", "config"])
    def test_all_three_log_kinds_are_triggers(self, store, gateway, approved,
                                              kind):
        self._trigger(gateway, kind, "2026-08-28T08:00:00")
        assert [s.trigger for s in store.stale(now=datetime(2026, 8, 29))] == [
            kind]

    def test_machine_replacement_arrives_as_a_config_row_and_is_named(
            self, store, gateway, approved):
        self._trigger(gateway, "config", "2026-08-28T08:00:00",
                      action="machine deleted")
        stale = store.stale(now=datetime(2026, 8, 29))
        assert "machine deleted" in stale[0].sentence

    def test_a_trigger_OLDER_than_the_estimate_does_not_make_it_stale(
            self, store, gateway, approved):
        self._trigger(gateway, "calibration", "2026-08-01T08:00:00")
        assert store.stale(now=datetime(2026, 8, 29)) == []

    def test_a_trigger_on_ANOTHER_machine_does_not(self, store, gateway,
                                                   approved):
        self._trigger(gateway, "calibration", "2026-08-28T08:00:00",
                      uid="mach-2")
        assert store.stale(now=datetime(2026, 8, 29)) == []

    def test_an_interim_estimate_past_its_replacement_date_is_stale(
            self, store, approved):
        stale = store.stale(now=datetime(2027, 9, 1))
        assert [s.trigger for s in stale] == [uncertainty.TRIGGER_REPLACE_BY]
        assert "replacement date" in stale[0].sentence.lower()

    def test_and_is_not_stale_before_it(self, store, approved):
        assert store.stale(now=datetime(2027, 8, 1)) == []

    def test_an_unapproved_estimate_is_not_reported_as_stale(
            self, store, gateway):
        store.save(_interim(store), computed_by="ryan")
        self._trigger(gateway, "calibration", "2026-08-28T08:00:00")
        assert store.stale(now=datetime(2026, 8, 29)) == []

    def test_a_superseded_estimate_is_not_reported_as_stale(
            self, store, gateway, approved):
        new = store.save(_interim(store, control_limit=3.0), computed_by="ryan")
        store.supersede(approved, new)
        self._trigger(gateway, "calibration", "2026-08-28T08:00:00")
        assert store.stale(now=datetime(2026, 8, 29)) == []

    def test_a_failed_read_is_never_an_empty_stale_list(self, store, gateway,
                                                        approved):
        blocked = uncertainty.UncertaintyStore(
            _Refusing(gateway, on_write=False, on_read=True))
        with pytest.raises(labcore_result.LabCoreError):
            blocked.stale(now=datetime(2026, 8, 29))


class TestAReadThatFailedIsNeverAnEmptyAnswer:

    @pytest.mark.parametrize("call", [
        lambda s: s.current_for(UID, TEST),
        lambda s: s.list_current(),
        lambda s: s.history_for(UID, TEST),
        lambda s: s.get("anything"),
    ])
    def test_every_read_raises_rather_than_reporting_nothing_on_file(
            self, gateway, call):
        blocked = uncertainty.UncertaintyStore(
            _Refusing(gateway, on_write=False, on_read=True))
        with pytest.raises(labcore_result.LabCoreError):
            call(blocked)

    def test_a_missing_table_IS_allowed_to_mean_nothing_on_file(self):
        """Nobody has computed one yet. That is a sentence an assessor acts on."""
        fresh = uncertainty.UncertaintyStore(FakeLabCoreGateway())
        assert fresh.list_current() == []
        assert fresh.current_for(UID, TEST) is None
        assert fresh.history_for(UID, TEST) == []

    def test_every_write_is_judged(self, gateway, store):
        blocked = uncertainty.UncertaintyStore(_Refusing(gateway))
        est = _interim(store)
        with pytest.raises(labcore_result.LabCoreRefused) as caught:
            blocked.save(est, computed_by="ryan")
        assert caught.value.busy is True
        assert caught.value.retry_after == 5

    def test_the_module_never_calls_sql_without_confirming_it(self):
        """One write seam, and nothing may reach around it.

        A pattern you must remember to apply is a pattern that will be
        forgotten — the lesson behind `check_write` existing at all. So there
        is exactly one `gateway.sql(` in this module, inside `_write`, and
        every call of `_write` is wrapped in `confirm_write`.
        """
        source = open(uncertainty.__file__, encoding="utf-8").read()
        assert source.count("gateway.sql(") == 1
        calls = [line for line in source.splitlines()
                 if "_write(" in line and not line.strip().startswith("def ")]
        assert calls
        for line in calls:
            assert "confirm_write(" in line, line


class TestTheRegisterEntry:
    """SOP 2.10 — the twelve-field entry that goes in the assessment file."""

    def test_it_has_exactly_twelve_fields(self, store):
        row = _interim(store).to_register_row()
        assert len(row) == 12
        assert list(row) == list(uncertainty.REGISTER_FIELDS)

    def test_every_field_is_answered_even_when_the_term_is_missing(self, store):
        row = _interim(store).to_register_row()
        for name, value in row.items():
            assert str(value).strip(), name

    def test_the_missing_bias_term_is_stated_and_not_omitted(self, store):
        row = _interim(store).to_register_row()
        assert "not established" in str(row["u_bias"]).lower()
        assert "certificate" in str(row["u_bias"]).lower()

    def test_the_combined_line_says_it_is_half_a_budget(self, store):
        """And says it in words that are true on the INTERIM route.

        "repeatability half" would be the spec's phrase and the wrong one here:
        Route 3's u(Rw) is a target somebody set, not a repeatability anybody
        measured.
        """
        line = str(_interim(store).to_register_row()["combined_and_expanded"])
        assert "u(Rw) half only" in line and "no bias term" in line
        assert "repeatability half" not in line

    def test_every_SOP_clause_the_spec_maps_is_named(self):
        clauses = set(uncertainty.REGISTER_FIELDS.values())
        for clause in ("2.1", "2.2", "2.3", "2.4", "2.5", "2.7", "2.8", "2.9",
                       "2.11"):
            assert any(clause in c for c in clauses), clause

    def test_the_route_and_its_replacement_date_are_both_on_the_entry(
            self, store):
        row = _interim(store).to_register_row()
        assert "target_limits" in str(row["u_rw"])
        assert "2027-08-27" in str(row["review"])
