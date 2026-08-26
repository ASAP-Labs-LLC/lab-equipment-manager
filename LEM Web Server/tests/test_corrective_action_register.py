"""The corrective-action register — the fleet-wide view an assessor asks for.

The lifecycle underneath this is already audit-grade: `lem_corrective_actions`
keeps a column per state rather than one `status` word, so the record says *when*
each thing happened and *who* did it, and `lem_action_events` is append-only so
nothing can be tidied away later.

What was missing is the way an assessor reads it. Every fleet-wide answer this
module could give was about what is **open** — `open_actions`, `open_by_machine`,
`overdue`, `/api/equipment/open-actions`. That is the Monday supervisor question.
The assessment question is the opposite one: *show me every corrective action in
the last twelve months, what triggered it, what was done about it, who verified
it, and whether it came back.* A closed action is the interesting one at an
assessment, because closing it is the evidence that the system works — and there
was no way to list one across the fleet at all.

Two things are held here:

1. **The register spans the whole fleet over a window and includes resolved
   actions.** Anything that silently drops closed rows answers the supervisor's
   question wearing the assessor's clothes.
2. **Recurrence is reported.** "Has this happened before on this instrument for
   this test?" is the question that separates a lab that closes tickets from one
   with a working corrective-action system, and it is the one nobody can answer
   by eye across a year of rows.
"""

from datetime import datetime, timedelta

import pytest

import snapshot_service
from equipment_history import CorrectiveActionStore
from labcore_gateway import FakeLabCoreGateway
from web_app import create_app

NOW = datetime(2026, 8, 26, 9, 0)


@pytest.fixture
def store():
    gw = FakeLabCoreGateway()
    snapshot_service.SnapshotService(gw).ensure_schema()
    return CorrectiveActionStore(gw)


def _stamp(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


def _opened(store, machine_uid, what, when, test_name="", trigger_kind="other"):
    return store.open_action(machine_uid, what, by="ryan", when=_stamp(when),
                             test_name=test_name, trigger_kind=trigger_kind)


# ── the window spans the fleet, and closed actions are the point ─────────────

class TestTheRegisterIsNotJustWhatIsOpen:
    def test_a_closed_action_still_appears(self, store):
        """The whole reason the register exists. `open_actions` cannot answer
        this and an assessor asks for nothing else."""
        action = _opened(store, "pac-flash-1", "Flash Point failed", NOW)
        store.record_action(action.uid, "Replaced thermocouple", by="ryan")
        store.verify(action.uid, by="sam", note="Two standards in band")
        store.close(action.uid, by="sam", note="Back in control")

        uids = [a.uid for a in store.register()]
        assert action.uid in uids, "a closed action vanished from the register"

    def test_open_and_closed_arrive_together(self, store):
        # The full lifecycle, because the store enforces it: an action cannot
        # be closed straight from `actioned` (§8.7.1 — somebody has to go back
        # and check the fix worked before it counts as finished).
        closed = _opened(store, "pac-flash-1", "Flash drift", NOW)
        store.record_action(closed.uid, "Recalibrated", by="ryan")
        store.verify(closed.uid, by="sam", note="Standard back in band")
        store.close(closed.uid, by="ryan")
        still_open = _opened(store, "optimpp-1", "Cloud Point low", NOW)

        uids = {a.uid for a in store.register()}
        assert {closed.uid, still_open.uid} <= uids

    def test_it_spans_every_instrument(self, store):
        for uid in ("a", "b", "c"):
            _opened(store, uid, "something", NOW)
        assert {a.machine_uid for a in store.register()} == {"a", "b", "c"}

    def test_one_instrument_can_be_asked_for_on_its_own(self, store):
        _opened(store, "pac-flash-1", "one", NOW)
        _opened(store, "optimpp-1", "two", NOW)
        got = store.register(machine_uid="pac-flash-1")
        assert [a.machine_uid for a in got] == ["pac-flash-1"]


class TestTheWindow:
    def test_actions_before_the_window_are_excluded(self, store):
        old = _opened(store, "m1", "last year", NOW - timedelta(days=400))
        recent = _opened(store, "m1", "this month", NOW - timedelta(days=3))
        got = [a.uid for a in store.register(start=_stamp(NOW - timedelta(days=365)))]
        assert recent.uid in got
        assert old.uid not in got

    def test_actions_after_the_window_are_excluded(self, store):
        inside = _opened(store, "m1", "june", datetime(2026, 6, 1))
        after = _opened(store, "m1", "august", datetime(2026, 8, 1))
        got = [a.uid for a in store.register(start="2026-05-01", end="2026-07-01")]
        assert inside.uid in got
        assert after.uid not in got

    def test_no_window_means_everything(self, store):
        old = _opened(store, "m1", "ancient", datetime(2019, 1, 1))
        assert old.uid in [a.uid for a in store.register()]

    def test_a_date_alone_includes_the_whole_of_that_day(self, store):
        """`end="2026-08-26"` must not exclude an action opened at 09:00 that
        morning. A bare date compared as a string against a full timestamp is
        the classic way a register quietly loses its most recent day."""
        today = _opened(store, "m1", "this morning", NOW)
        got = [a.uid for a in store.register(start="2026-08-26", end="2026-08-26")]
        assert today.uid in got

    def test_newest_first(self, store):
        first = _opened(store, "m1", "older", NOW - timedelta(days=10))
        second = _opened(store, "m1", "newer", NOW - timedelta(days=1))
        assert [a.uid for a in store.register()][:2] == [second.uid, first.uid]


# ── recurrence: did it come back? ────────────────────────────────────────────

class TestRecurrence:
    def test_the_same_fault_twice_on_one_instrument_is_a_recurrence(self, store):
        _opened(store, "pac-flash-1", "Flash failed", NOW - timedelta(days=60),
                test_name="Flash Point", trigger_kind="qc_fail")
        _opened(store, "pac-flash-1", "Flash failed again", NOW,
                test_name="Flash Point", trigger_kind="qc_fail")
        groups = store.recurrences()
        assert ("pac-flash-1", "Flash Point") in groups
        assert len(groups[("pac-flash-1", "Flash Point")]) == 2

    def test_one_occurrence_is_not_a_recurrence(self, store):
        _opened(store, "pac-flash-1", "Flash failed", NOW,
                test_name="Flash Point", trigger_kind="qc_fail")
        assert store.recurrences() == {}

    def test_the_same_test_on_a_different_instrument_is_not_a_recurrence(self, store):
        """Two benches failing the same method is a method problem, not a
        repeat fault on one instrument. Grouping them would invent a recurrence
        that the record does not show."""
        _opened(store, "pac-flash-1", "Flash failed", NOW,
                test_name="Flash Point", trigger_kind="qc_fail")
        _opened(store, "pensky-1", "Flash failed", NOW,
                test_name="Flash Point", trigger_kind="qc_fail")
        assert store.recurrences() == {}

    def test_actions_with_no_test_named_are_never_grouped(self, store):
        """A blank `test_name` is missing information, not a shared key. Two
        unrelated general faults on one bench are not the same fault twice."""
        _opened(store, "m1", "odd noise", NOW)
        _opened(store, "m1", "loose door", NOW)
        assert store.recurrences() == {}


# ── the export ───────────────────────────────────────────────────────────────

class TestTheRegisterExport:
    def test_the_csv_carries_the_whole_lifecycle(self, store):
        action = _opened(store, "pac-flash-1", "Flash Point failed", NOW,
                         test_name="Flash Point", trigger_kind="qc_fail")
        store.record_action(action.uid, "Replaced thermocouple", by="ryan")
        store.verify(action.uid, by="sam", note="Two standards in band")
        store.close(action.uid, by="sam", note="Back in control")

        app = create_app(store.gateway, secret="t")
        app.config.update(TESTING=True)
        resp = app.test_client().get("/api/export/corrective-actions.csv")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)

        # Every state's WHO and WHEN has to be in the file. A register that
        # says "closed" without naming who closed it is not a record.
        for needed in ("Flash Point failed", "Replaced thermocouple",
                       "ryan", "sam", "qc_fail"):
            assert needed in body, needed

    def test_the_csv_names_its_columns(self, store):
        _opened(store, "m1", "something", NOW)
        app = create_app(store.gateway, secret="t")
        app.config.update(TESTING=True)
        header = app.test_client().get(
            "/api/export/corrective-actions.csv").get_data(as_text=True).splitlines()[0]
        for column in ("machine_uid", "opened_at", "opened_by", "action_taken",
                       "verified_by", "closed_at", "outcome", "priority"):
            assert column in header, column

    def test_an_empty_register_still_exports_a_header(self, store):
        """A lab with no corrective actions is a legitimate answer and the file
        must still be a file. A zero-byte download reads as a broken export."""
        app = create_app(store.gateway, secret="t")
        app.config.update(TESTING=True)
        body = app.test_client().get(
            "/api/export/corrective-actions.csv").get_data(as_text=True)
        assert "machine_uid" in body
