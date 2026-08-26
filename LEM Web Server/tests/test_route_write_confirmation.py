"""Every route that writes must hear LabCore say yes, and say so if it didn't.

The bug these tests exist for, stated once:

    LabCore's write queue serialises at roughly 1.5 writes a second and refuses
    past ~100 pending BY ANSWERING rather than raising. So `self.gateway.sql(
    ...)` whose answer nobody reads reports success for a write that never
    happened, and the dialog closes on "Saved".

Every write test below runs TWICE, once per refusal shape, and the two are not
the same kind of thing (see tests/refusal_shapes.py):

  * the EVIDENCED refusal — `{"error": "LabCore is busy…", "busy": true,
    "retry_after": n}`, an error dict returned normally. notes.md and
    lem_station_module.py:495 both record it from a real incident;
  * a SYNTHETIC shape carrying no "error" key at all. Worth driving because
    `{"error": ...}` is the one shape the old `if not res.get("error")` code
    already coped with, so a suite that refuses only that way proves nothing
    about the bug — but it is a fixture, not a measurement.

This suite used to drive ONLY the synthetic one, while its docstring called it
"the REAL refusal shape". That is how an invention became a fact three rounds
of work relied on.

And every write test asserts TWO things, because the whole failure was the gap
between them:

    1. the response is a failure — not a 200 that lies, and not a bare 500
       either, since "Internal Server Error" tells nobody whether to press Save
       again;
    2. LabCore's tables are unchanged, read directly past the app.

Reads get their own section, driven with a real timeout. Their rule is the
sibling of the write rule: "no QC assigned", "nothing scheduled", "no rounds
recorded" and "no such configuration" are all answers an operator acts on, and
none of them may be invented out of a read that never happened.

CORRECTION (2026-08-25). This paragraph used to go on: "a read cannot be
refused with the queue-full shape". That reasoning is about ONE shape —
a shape carrying no "error" key, which `labcore_result.rows` therefore reads
as an answered-but-empty read, correctly.
It does not hold for the shape LabCore is actually recorded as sending,
`{"error": "LabCore is busy…", "busy": true, …}`, and reads travel the same
endpoint as writes. Standing there unchallenged, it was why no read test in
this branch drove a refusal at all. That case now lives in
tests/test_reads_survive_a_full_write_queue.py
(`TestAReadCanBeREFUSEDAsWellAsUnanswerable`), along with the regression where
a full WRITE queue took down read-only pages.

The synthetic shape is named honestly in tests/refusal_shapes.py: it was
INVENTED during this work and then cited by later work as if measured. It still
refuses correctly, so the tests using it stand — but it is not evidence, and no
more of it should be added.
"""
import json
import os

import pytest

import refusal_shapes
from labcore_gateway import FakeLabCoreGateway
from web_app import create_app

# Every test in this module runs once per refusal shape.
pytestmark = pytest.mark.usefixtures("both_refusal_shapes")

# Kept as a name for the tests that pass an explicit `answer=`. SYNTHETIC —
# see refusal_shapes.
QUEUE_FULL = refusal_shapes.NO_ERROR_KEY
# A read waiting behind that queue until the client gives up.
READ_BLIP = {"error": "HTTPSConnectionPool(host='labvision'): Read timed out"}


class StubAuth:
    def login(self, username, password):
        return ("kaden", "tok", "")

    def logout(self, token):
        pass


class Refusing:
    """A LabCore whose write queue is full, or whose reads time out.

    `refuse` picks which writes are ANSWERED with the refusal instead of being
    run; `fail_read` picks which reads time out. Everything else passes through
    to a real fake, so "did the route report a failure" and "did the row change"
    stay two separate questions with two separate answers.

    The default refuses every statement EXCEPT `CREATE TABLE`. Not because a
    full queue would spare a CREATE — it would not — but because the schema is
    declared once at boot on a running server, and letting it through here
    aims each test at the statement it is actually about rather than at
    `ensure_schema`. `TestEvenTheSchemaCanBeRefused` covers the other case.
    """

    def __init__(self, real, refuse=None, fail_read=lambda sql: False):
        self.real = real
        self.refuse = refuse or (lambda sql: "CREATE TABLE" not in sql.upper())
        self.fail_read = fail_read
        self.refused = []

    def sql(self, sql, args=None, **kw):
        if self.refuse(sql):
            self.refused.append(sql)
            # Whichever shape this run of the suite is driving.
            return refusal_shapes.current()
        return self.real.sql(sql, args, **kw)

    def read_sql(self, sql, args=None, **kw):
        if self.fail_read(sql):
            return dict(READ_BLIP)
        return self.real.read_sql(sql, args, **kw)

    def write(self, operation, params, **kw):
        return self.real.write(operation, params, **kw)

    def is_running(self):
        return True

    def get_test_names(self):
        return self.real.get_test_names()

    def get_samples(self, **kw):
        return self.real.get_samples(**kw)


def _client(gateway):
    app = create_app(gateway, authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/api/login", json={"username": "kaden", "password": "p"})
    return c


UID = "m1"
CHECKLIST = {"uid": "cl1", "name": "Opening round", "slot": "opening",
             "items": [{"uid": "i1", "text": "Check the argon"},
                       {"uid": "i2", "text": "Bath temperature",
                        "entry_type": "number", "units": "°C"}]}


@pytest.fixture
def lab():
    """A lab with something in every table, seeded through the healthy app.

    Seeding through the real routes matters: it proves the happy path still
    works, and it leaves LabCore holding exactly what the refused attempt below
    must fail to change.
    """
    gw = FakeLabCoreGateway()
    healthy = _client(gw)

    from snapshot_service import SnapshotService
    SnapshotService(gw).ensure_schema()
    gw.sql("INSERT INTO lem_machine_status (machine_uid, title, status, "
           "reason, updated_at) VALUES (?, 'Multitek NS', 'GREEN', '', ?)",
           [UID, "2026-08-20T09:00:00"])

    healthy.post("/api/qc-samples", json={
        "name": "Diesel - AO25", "sample_id_val": "STD-1",
        "tests": [{"name": "Flash Point", "expected": 63.72,
                   "std_dev": 1.05, "k": 2.0, "units": "°C"}]})
    healthy.post("/api/qc-specs", json={
        "machine_uid": UID, "test_name": "Flash Point", "sample_id": "STD-1",
        "expected": 63.72, "std_dev": 1.05, "k": 2.0, "units": "°C"})
    healthy.post(f"/api/machines/{UID}/qc-targets",
                 json={"targets": [{"sample": "Diesel - AO25",
                                    "test": "Flash Point"}]})
    healthy.post(f"/api/machines/{UID}/position", json={"x": 3.0, "y": 1.0})
    healthy.post("/api/checklists", json=CHECKLIST)
    healthy.post(f"/api/checklists/{CHECKLIST['uid']}/toggle",
                 json={"item_uid": "i1", "checked": True})
    healthy.post(f"/api/machines/{UID}/maintenance",
                 json={"uid": "t1", "name": "Annual calibration",
                       "kind": "calibration", "interval_days": 365,
                       "last_done": "2026-01-02"})
    healthy.post("/api/schedule", json={"working_days": [0, 1, 2, 3, 4],
                                        "opens": "07:00", "closes": "17:00"})
    healthy.post("/api/holidays", json={"day": "2026-12-25", "name": "Christmas"})
    healthy.post(f"/api/machines/{UID}/corrections",
                 json={"test_name": "Flash Point", "correction": -3.0})
    healthy.post("/api/machine-configs", json={"title": "Multitek NS"})
    gw.sql("INSERT INTO lem_machine_config (machine_uid, title, config, "
           "updated_at, updated_by) VALUES (?, 'Multitek NS', ?, ?, 'kaden') "
           "ON CONFLICT(machine_uid) DO UPDATE SET config=excluded.config",
           [UID, json.dumps({"mappings": [{"methods": ["Flash Point"]}]}),
            "2026-08-20T09:00:00"])
    return gw


def rows(gw, sql, args=None):
    """Read LabCore directly, past every store, so the assertion is about what
    LabCore holds rather than about what the app believes it holds."""
    return gw.read_sql(sql, args or []).get("rows") or []


def refused(response):
    """Assert this is an honest "not saved", and hand back the body.

    REWRITTEN FOR THE SURVIVING CONTRACT. This used to require `saved: false`,
    `retry: true` and `labcore: refused|unavailable` on every refusal. Those
    keys exist only on the routes that CATCH the exception themselves
    (`_labcore_failed`); a refusal that reaches `refusal_response` — the one
    error handler, which is the whole point of the seam — carries `error`,
    `retryable` and `retry_after`, because that is what `LEM.failure()` in
    static/lem.js actually reads. Demanding the other spelling here would mean
    demanding that every route keep its own handler, which is the pattern the
    error handler replaced.

    What the two shapes agree on, and what is held here instead:

      * NOT a 2xx and NO `ok` key — "not saved" said the way every save
        handler on the floor reads it (`r.ok`);
      * 502 or 503, never a bare 500;
      * a sentence. A failure with no message is still a silent failure.

    "Worth retrying" is asserted where it is the point of the test rather than
    on every refusal: `retry`/`retryable` mean different things (see
    `_labcore_failed`) and only one of them is on both shapes.
    """
    assert response.status_code in (502, 503), response.status_code
    body = response.get_json()
    assert body["error"], "a failure with no message is still a silent failure"
    assert "ok" not in body, "a refusal must not carry the success flag"
    return body


def unreadable(response):
    """Assert this is an honest "could not be read", and hand back the body."""
    assert response.status_code in (502, 503), response.status_code
    body = response.get_json()
    assert body["error"]
    assert body.get("retry") is True
    return body


# ── writes: a refusal is never reported as saved ───────────────────────────

class TestTheFloorMap:
    def test_a_refused_lock_is_not_reported_as_locked(self, lab):
        c = _client(Refusing(lab))
        r = c.post("/api/map", json={"locked": True})
        refused(r)
        # A lock that never took leaves the floor draggable for everyone.
        assert rows(lab, "SELECT value FROM lem_map_settings "
                         "WHERE key = 'locked'") == []

    def test_a_refused_drag_does_not_move_the_instrument(self, lab):
        before = rows(lab, "SELECT pos_x, pos_y FROM lem_machine_layout "
                           "WHERE machine_uid = ?", [UID])
        c = _client(Refusing(lab))
        r = c.post(f"/api/machines/{UID}/position", json={"x": 9.0, "y": 9.0})
        refused(r)
        assert rows(lab, "SELECT pos_x, pos_y FROM lem_machine_layout "
                         "WHERE machine_uid = ?", [UID]) == before

    def test_an_unreadable_lock_refuses_the_drag_rather_than_assuming_open(
            self, lab):
        # The lock is a permission check. Reading it as "unlocked" during a
        # blip lets the floor be rearranged underneath a lab that froze it.
        c = _client(Refusing(lab, refuse=lambda sql: False,
                             fail_read=lambda sql: "lem_map_settings" in sql))
        r = c.post(f"/api/machines/{UID}/position", json={"x": 9.0, "y": 9.0})
        assert refused(r)["labcore"] == "unavailable"
        assert rows(lab, "SELECT pos_x FROM lem_machine_layout "
                         "WHERE machine_uid = ?", [UID])[0]["pos_x"] == 3.0


class TestQcAssignment:
    def test_a_refused_assignment_says_the_set_must_be_re_applied(self, lab):
        c = _client(Refusing(lab))
        r = c.post(f"/api/machines/{UID}/qc-targets",
                   json={"targets": [{"sample": "Diesel - AO25",
                                      "test": "Cloud Point"}]})
        body = refused(r)
        # `assign` is a DELETE then an INSERT per target and cannot be atomic,
        # so "press Save again" is not enough advice on its own.
        assert "re-apply" in body["error"].lower()
        assert [x["test_name"] for x in
                rows(lab, "SELECT test_name FROM lem_machine_targets "
                          "WHERE machine_uid = ?", [UID])] == ["Flash Point"]

    def test_a_refused_qc_band_is_not_a_400_about_the_numbers(self, lab):
        c = _client(Refusing(lab))
        r = c.post("/api/qc-specs", json={
            "machine_uid": UID, "test_name": "Cloud Point",
            "sample_id": "STD-1", "expected": -7.4, "std_dev": 0.5, "k": 2.0})
        # 400 would send the operator re-typing a band that was perfectly good.
        assert r.status_code != 400
        refused(r)
        assert [x["test_name"] for x in
                rows(lab, "SELECT test_name FROM lem_qc_specs "
                          "WHERE machine_uid = ?", [UID])] == ["Flash Point"]

    def test_a_refused_band_deletion_leaves_the_band_in_place(self, lab):
        c = _client(Refusing(lab))
        r = c.delete("/api/qc-specs", json={"machine_uid": UID,
                                            "test_name": "Flash Point"})
        refused(r)
        assert rows(lab, "SELECT test_name FROM lem_qc_specs "
                         "WHERE machine_uid = ?", [UID])


class TestQcLibrary:
    def test_a_refused_standard_is_not_reported_as_saved(self, lab):
        c = _client(Refusing(lab))
        r = c.post("/api/qc-samples", json={
            "name": "Diesel - AO26", "sample_id_val": "STD-2",
            "tests": [{"name": "Flash Point", "expected": 64.0,
                       "std_dev": 1.0, "k": 2.0}]})
        assert r.status_code != 400
        refused(r)
        assert [x["name"] for x in
                rows(lab, "SELECT name FROM lem_qc_samples ORDER BY name")] \
            == ["Diesel - AO25"]

    def test_a_refused_standard_deletion_leaves_it_in_the_library(self, lab):
        c = _client(Refusing(lab))
        refused(c.delete("/api/qc-samples", json={"name": "Diesel - AO25"}))
        assert rows(lab, "SELECT name FROM lem_qc_samples")

    def test_a_refused_changeover_does_not_claim_machines_moved(self, lab):
        c = _client(Refusing(lab))
        r = c.post("/api/qc-samples/changeover",
                   json={"old_name": "Diesel - AO25",
                         "new_name": "Diesel - AO26", "new_id_val": "STD-2"})
        assert r.status_code != 400          # not "QC sample not found"
        refused(r)
        # Every instrument still points at the lot it was pointing at. A
        # changeover reported as "1 moved" that moved none stops QC across the
        # lab silently, which is the failure changeover exists to prevent.
        assert [x["sample_name"] for x in
                rows(lab, "SELECT sample_name FROM lem_machine_targets "
                          "WHERE machine_uid = ?", [UID])] == ["Diesel - AO25"]

    def test_an_unreadable_library_is_not_a_404_about_a_lot_that_exists(
            self, lab):
        c = _client(Refusing(lab, refuse=lambda sql: False,
                             fail_read=lambda sql: "lem_qc_samples" in sql))
        r = c.post("/api/qc-samples/changeover",
                   json={"old_name": "Diesel - AO25",
                         "new_name": "Diesel - AO26", "new_id_val": "STD-2"})
        assert r.status_code == 503
        assert "not found" not in r.get_json()["error"].lower()


class TestTheOverride:
    def test_a_refused_override_is_never_reported_as_applied(self, lab):
        c = _client(Refusing(lab))
        r = c.post(f"/api/machines/{UID}/override",
                   json={"override": "SERVICE", "comment": "sensor swap"})
        refused(r)
        # This is a COMMAND to the bench: the module polls lem_machine_control
        # and takes itself out of service. Dropped and reported as applied, the
        # floor shows an instrument stopped while it keeps running samples.
        assert rows(lab, "SELECT manual_override FROM lem_machine_control "
                         "WHERE machine_uid = ?", [UID]) == []


class TestCorrectionFactors:
    def test_a_refused_correction_leaves_the_old_one_in_force(self, lab):
        c = _client(Refusing(lab))
        r = c.post(f"/api/machines/{UID}/corrections",
                   json={"test_name": "Flash Point", "correction": "-9.0"})
        assert r.status_code != 400          # not "that is not a number"
        refused(r)
        held = rows(lab, "SELECT correction FROM lem_correction_factors "
                         "WHERE machine_uid = ? AND test_name = 'Flash Point'",
                    [UID])
        assert held[0]["correction"] == -3.0

    def test_a_refused_removal_leaves_the_correction_applying(self, lab):
        c = _client(Refusing(lab))
        refused(c.delete(
            f"/api/machines/{UID}/corrections/Flash%20Point"))
        assert rows(lab, "SELECT correction FROM lem_correction_factors "
                         "WHERE machine_uid = ?", [UID])[0]["correction"] == -3.0

    def test_an_unreadable_instrument_list_is_not_no_such_instrument(self, lab):
        c = _client(Refusing(
            lab, refuse=lambda sql: False,
            fail_read=lambda sql: "lem_machine_status" in sql))
        r = c.post(f"/api/machines/{UID}/corrections",
                   json={"test_name": "Flash Point", "correction": "-9.0"})
        assert r.status_code == 503          # not 404 about a running bench


class TestTheOpeningHours:
    def test_a_refused_schedule_is_not_reported_as_saved(self, lab):
        c = _client(Refusing(lab))
        refused(c.post("/api/schedule", json={"opens": "05:00"}))
        assert rows(lab, "SELECT opens FROM lem_lab_schedule")[0]["opens"] \
            == "07:00"

    def test_a_degraded_read_never_becomes_the_saved_schedule(self, lab):
        # The read fills in every field the operator did not type. Degraded, it
        # would reset the working days and post back a form with no holidays.
        c = _client(Refusing(lab, refuse=lambda sql: False,
                             fail_read=lambda sql: "lem_lab_schedule" in sql))
        r = c.post("/api/schedule", json={"opens": "05:00"})
        assert r.status_code == 503
        assert rows(lab, "SELECT opens FROM lem_lab_schedule")[0]["opens"] \
            == "07:00"

    def test_a_refused_holiday_does_not_close_the_lab(self, lab):
        c = _client(Refusing(lab))
        refused(c.post("/api/holidays", json={"day": "2026-07-04",
                                              "name": "Independence Day"}))
        assert [x["day"] for x in
                rows(lab, "SELECT day FROM lem_lab_holidays")] == ["2026-12-25"]

    def test_a_refused_holiday_removal_leaves_the_lab_closed(self, lab):
        c = _client(Refusing(lab))
        refused(c.delete("/api/holidays/2026-12-25"))
        assert rows(lab, "SELECT day FROM lem_lab_holidays")


class TestTheRounds:
    def test_a_refused_checklist_is_not_a_400_about_its_contents(self, lab):
        c = _client(Refusing(lab))
        r = c.post("/api/checklists", json={"uid": "cl2", "name": "Closing",
                                            "slot": "closing",
                                            "items": [{"uid": "z",
                                                       "text": "Vent"}]})
        assert r.status_code != 400
        refused(r)
        assert [x["uid"] for x in
                rows(lab, "SELECT uid FROM lem_checklist_defs")] == ["cl1"]

    def test_a_refused_tick_is_not_reported_as_recorded(self, lab):
        # Aimed at the tick itself rather than at the schema: the definition
        # still reads fine, so what is refused is exactly the row that records
        # the round having been done.
        c = _client(Refusing(lab, refuse=lambda sql: "INSERT" in sql.upper()
                                                     and "state" in sql))
        r = c.post(f"/api/checklists/{CHECKLIST['uid']}/toggle",
                   json={"item_uid": "i2", "checked": True})
        refused(r)
        ticked = [x["item_uid"] for x in
                  rows(lab, "SELECT item_uid FROM lem_checklist_state "
                            "WHERE checked = 1")]
        assert ticked == ["i1"]

    def test_a_refused_reading_leaves_no_gap_claimed_as_recorded(self, lab):
        c = _client(Refusing(lab, refuse=lambda sql: "INSERT" in sql.upper()
                                                     and "state" in sql))
        r = c.post(f"/api/checklists/{CHECKLIST['uid']}/value",
                   json={"item_uid": "i2", "value": "21.5"})
        assert r.status_code != 400          # not "that is not a number"
        refused(r)
        assert rows(lab, "SELECT value FROM lem_checklist_state "
                         "WHERE item_uid = 'i2' AND value != ''") == []

    def test_a_refused_deletion_leaves_the_round_in_place(self, lab):
        c = _client(Refusing(lab))
        refused(c.delete(f"/api/checklists/{CHECKLIST['uid']}"))
        assert rows(lab, "SELECT uid FROM lem_checklist_defs")

    def test_an_unreadable_checklist_is_not_no_such_checklist(self, lab):
        c = _client(Refusing(lab, refuse=lambda sql: False,
                             fail_read=lambda sql: "lem_checklist_defs" in sql))
        r = c.post(f"/api/checklists/{CHECKLIST['uid']}/toggle",
                   json={"item_uid": "i2", "checked": True})
        assert r.status_code == 503          # not 404

    def test_a_partly_refused_v4_import_reports_how_far_it_got(self, lab):
        # An import is many writes into a queue that refuses past 100 pending,
        # so stopping part-way is the ordinary outcome, not an edge case.
        c = _client(Refusing(lab))
        r = c.post("/api/checklists/import-v4", json={"json": json.dumps({
            "checklists": [{"uid": "v1", "name": "Opening checks",
                            "items": [{"text": "Argon"}]}]})})
        body = refused(r)
        assert body["count"] == 0
        assert body["incomplete"] is True
        assert "again" in body["error"].lower()
        assert [x["uid"] for x in
                rows(lab, "SELECT uid FROM lem_checklist_defs")] == ["cl1"]


class TestPmAndCalibration:
    def test_a_refused_task_is_not_reported_as_scheduled(self, lab):
        c = _client(Refusing(lab))
        r = c.post(f"/api/machines/{UID}/maintenance",
                   json={"name": "Pump service", "kind": "pm",
                         "interval_days": 90})
        assert r.status_code != 400
        refused(r)
        assert [x["name"] for x in
                rows(lab, "SELECT name FROM lem_maintenance "
                          "WHERE machine_uid = ?", [UID])] \
            == ["Annual calibration"]

    def test_a_refused_completion_does_not_move_the_due_date(self, lab):
        c = _client(Refusing(lab))
        refused(c.post("/api/maintenance/t1/complete", json={"note": "done"}))
        assert rows(lab, "SELECT last_done FROM lem_maintenance "
                         "WHERE uid = 't1'")[0]["last_done"] == "2026-01-02"

    def test_a_completion_whose_history_line_is_refused_says_so(self, lab):
        # The reschedule and the history line are two writes. The first landed
        # and cannot be taken back, so this is a 200 — but the PM/CAL record is
        # what an auditor reads, and only the person standing here can add the
        # entry back.
        c = _client(Refusing(
            lab, refuse=lambda sql: "lem_machine_log" in sql
                                    and "INSERT" in sql.upper()))
        r = c.post("/api/maintenance/t1/complete", json={"note": "done"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["logged"] is False
        assert body["warning"]
        # The completion itself really did happen.
        assert rows(lab, "SELECT last_done FROM lem_maintenance "
                         "WHERE uid = 't1'")[0]["last_done"] != "2026-01-02"

    def test_an_unreadable_task_is_not_no_such_task(self, lab):
        c = _client(Refusing(lab, refuse=lambda sql: False,
                             fail_read=lambda sql: "lem_maintenance" in sql))
        r = c.post("/api/maintenance/t1/complete", json={"note": "done"})
        assert r.status_code == 503          # not 404 about a task that exists

    def test_a_refused_task_deletion_leaves_it_scheduled(self, lab):
        c = _client(Refusing(lab))
        refused(c.delete("/api/maintenance/t1"))
        assert rows(lab, "SELECT uid FROM lem_maintenance WHERE uid = 't1'")

    def test_a_partly_refused_history_import_counts_only_what_landed(self, lab):
        csv_text = ("equipment,task,kind,completed_date,performed_by,note\n"
                    "Multitek NS,Annual calibration,cal,2026-03-01,kaden,ok\n")
        c = _client(Refusing(
            lab, refuse=lambda sql: "lem_machine_log" in sql
                                    and "INSERT" in sql.upper()))
        r = c.post("/api/maintenance-import", json={"csv": csv_text})
        body = refused(r)
        assert body["created"] == 0
        assert body["incomplete"] is True
        assert rows(lab, "SELECT ts FROM lem_machine_log "
                         "WHERE kind = 'calibration'") == []

    def test_an_unreadable_equipment_list_imports_nothing(self, lab):
        # `plan_import` matches the sheet against this list and then WRITES
        # what it matched, so a degraded [] would report every historic PM as
        # "unmatched equipment" about machines that exist.
        csv_text = ("equipment,task,kind,completed_date,performed_by,note\n"
                    "Multitek NS,Annual calibration,cal,2026-03-01,kaden,ok\n")
        c = _client(Refusing(
            lab, refuse=lambda sql: False,
            fail_read=lambda sql: "lem_machine_status" in sql))
        r = c.post("/api/maintenance-import", json={"csv": csv_text})
        assert r.status_code == 503
        assert "unmatched" not in (r.get_json().get("error") or "").lower()
        assert rows(lab, "SELECT ts FROM lem_machine_log "
                         "WHERE kind = 'calibration'") == []


class TestMachineConfigurations:
    def test_a_refused_config_save_loses_no_work_silently(self, lab):
        c = _client(Refusing(lab))
        r = c.post(f"/api/machine-configs/{UID}",
                   json={"title": "Multitek NS",
                         "config": {"mappings": [{"methods": ["Cloud Point"]}]}})
        refused(r)
        held = rows(lab, "SELECT config FROM lem_machine_config "
                         "WHERE machine_uid = ?", [UID])[0]["config"]
        assert "Cloud Point" not in held

    def test_a_refused_create_does_not_appear_in_the_picker(self, lab):
        c = _client(Refusing(lab))
        before = len(rows(lab, "SELECT machine_uid FROM lem_machine_config"))
        refused(c.post("/api/machine-configs", json={"title": "New bench"}))
        assert len(rows(lab, "SELECT machine_uid FROM lem_machine_config")) \
            == before

    def test_a_refused_duplicate_is_not_a_404(self, lab):
        c = _client(Refusing(lab))
        r = c.post(f"/api/machine-configs/{UID}/duplicate",
                   json={"title": "Multitek NS copy"})
        assert r.status_code != 404
        refused(r)

    def test_a_refused_config_delete_leaves_it_offering_itself(self, lab):
        c = _client(Refusing(lab))
        refused(c.delete(f"/api/machine-configs/{UID}",
                         json={"confirm": True}))
        assert rows(lab, "SELECT machine_uid FROM lem_machine_config "
                         "WHERE machine_uid = ?", [UID])


class TestRetiringAMachine:
    def test_a_refused_retirement_stops_and_says_where(self, lab):
        c = _client(Refusing(lab))
        r = c.delete(f"/api/machines/{UID}", json={"confirm": True})
        body = refused(r)
        # `partial`, not `complete`. The surviving contract states what LANDED
        # rather than negating what did not — `LEM.failure()` renders `landed`
        # / `not_landed` off it — and nothing landed here, so the machine is
        # exactly as it was.
        assert body["partial"] is False
        assert body["landed"] == []
        assert body["stopped_at"]
        assert "again" in body["error"].lower()
        # Nothing was removed, so the machine is exactly as it was.
        assert rows(lab, "SELECT machine_uid FROM lem_machine_status "
                         "WHERE machine_uid = ?", [UID])
        assert rows(lab, "SELECT machine_uid FROM lem_machine_config "
                         "WHERE machine_uid = ?", [UID])

    def test_it_reports_the_parts_that_did_go(self, lab):
        # The status row goes, the QC bands are refused. A "retired" reply here
        # would leave a half-retired machine that nobody knows to finish.
        c = _client(Refusing(lab, refuse=lambda sql: "lem_qc_specs" in sql))
        r = c.delete(f"/api/machines/{UID}", json={"confirm": True})
        body = refused(r)
        # One label vocabulary for the whole sequence, shared by `removed`,
        # `stopped_at`, `landed` and `not_landed` — two spellings of the same
        # ten steps is how a client matches on one list and renders the other.
        assert body["removed"] == ["live status"]
        assert body["stopped_at"] == "QC specs"
        assert body["landed"] == ["live status"]
        assert "QC specs" in body["not_landed"]
        assert rows(lab, "SELECT machine_uid FROM lem_qc_specs "
                         "WHERE machine_uid = ?", [UID])


class TestEvenTheSchemaCanBeRefused:
    """A boot while the queue is full cannot leave a store believing its table
    exists. That was the shape of the original bug in `ensure_schema`."""

    def test_a_refused_create_is_not_a_saved_lock(self, lab):
        c = _client(Refusing(lab, refuse=lambda sql: True))
        refused(c.post("/api/map", json={"locked": True}))

    def test_the_next_attempt_still_tries(self, lab):
        gw = Refusing(lab, refuse=lambda sql: True)
        c = _client(gw)
        c.post("/api/map", json={"locked": True})
        first = len(gw.refused)
        c.post("/api/map", json={"locked": True})
        # A store that gave up would send nothing the second time, and the lock
        # would stay unwritable for the life of the process.
        assert len(gw.refused) > first


# ── reads: "could not ask" is never served as "there is nothing" ───────────

class TestAnUnreadableAnswerIsNotAnEmptyOne:
    def test_the_qc_bands(self, lab):
        c = _client(Refusing(lab, fail_read=lambda sql: "lem_qc_specs" in sql))
        r = c.get("/api/qc-specs")
        unreadable(r)
        # The key stays so nothing downstream has to guard for it; the STATUS
        # is what says this is not an answer.
        assert r.get_json()["specs"] == []

    def test_the_qc_library(self, lab):
        c = _client(Refusing(lab,
                             fail_read=lambda sql: "lem_qc_samples" in sql))
        r = c.get("/api/qc-samples")
        unreadable(r)
        assert r.get_json()["samples"] == []

    def test_the_configuration_picker(self, lab):
        c = _client(Refusing(
            lab, fail_read=lambda sql: "lem_machine_config" in sql))
        unreadable(c.get("/api/machine-configs"))

    def test_one_configuration_is_a_503_not_a_404(self, lab):
        c = _client(Refusing(
            lab, fail_read=lambda sql: "lem_machine_config" in sql))
        r = c.get(f"/api/machine-configs/{UID}")
        assert r.status_code == 503, \
            "'could not ask' served as 'does not exist' is how a save " \
            "becomes a 404 about a machine that is running"

    def test_a_configuration_that_really_is_missing_is_still_a_404(self, lab):
        assert _client(lab).get("/api/machine-configs/nope").status_code == 404

    def test_todays_round(self, lab):
        c = _client(Refusing(lab,
                             fail_read=lambda sql: "lem_checklist" in sql))
        unreadable(c.get("/api/checklists"))

    def test_the_checklist_archive(self, lab):
        c = _client(Refusing(
            lab, fail_read=lambda sql: "lem_checklist_state" in sql))
        unreadable(c.get("/api/checklists/history"))

    def test_one_items_readings(self, lab):
        c = _client(Refusing(lab,
                             fail_read=lambda sql: "lem_checklist" in sql))
        unreadable(c.get(f"/api/checklists/{CHECKLIST['uid']}/values?item=i2"))

    def test_an_instruments_pm_tasks(self, lab):
        c = _client(Refusing(lab,
                             fail_read=lambda sql: "lem_maintenance" in sql))
        unreadable(c.get(f"/api/machines/{UID}/maintenance"))

    def test_an_instruments_pm_history(self, lab):
        c = _client(Refusing(lab,
                             fail_read=lambda sql: "lem_machine_log" in sql))
        unreadable(c.get(f"/api/machines/{UID}/maintenance-history"))

    def test_the_labs_pm_history(self, lab):
        c = _client(Refusing(lab,
                             fail_read=lambda sql: "lem_machine_log" in sql))
        unreadable(c.get("/api/maintenance-history"))

    def test_the_qc_control_chart(self, lab):
        c = _client(Refusing(lab,
                             fail_read=lambda sql: "lem_machine_log" in sql))
        unreadable(c.get(f"/api/machines/{UID}/qc-trend"))

    def test_an_instruments_run_history(self, lab):
        c = _client(Refusing(lab,
                             fail_read=lambda sql: "lem_machine_log" in sql))
        unreadable(c.get(f"/api/machines/{UID}/events?limit=60"))

    def test_a_deep_events_request(self, lab):
        c = _client(Refusing(lab,
                             fail_read=lambda sql: "lem_machine_log" in sql))
        unreadable(c.get("/api/events?limit=4000"))

    def test_the_corrections_in_force(self, lab):
        c = _client(Refusing(
            lab, fail_read=lambda sql: "lem_correction_factors" in sql))
        r = c.get(f"/api/machines/{UID}/corrections")
        unreadable(r)
        # Showing 0.0 for a bench running at -3.0 makes an operator type the
        # offset in again over one that is already there.
        assert "corrections" not in r.get_json()

    def test_the_qc_export_an_assessor_asks_for(self, lab):
        c = _client(Refusing(lab,
                             fail_read=lambda sql: "lem_machine_log" in sql))
        r = c.get("/api/export/qc.csv")
        unreadable(r)
        # A CSV with a header row and nothing under it leaves the building.
        assert "text/csv" not in r.headers.get("Content-Type", "")

    def test_an_instruments_export(self, lab):
        c = _client(Refusing(lab,
                             fail_read=lambda sql: "lem_machine_log" in sql))
        unreadable(c.get(f"/api/machines/{UID}/export.csv"))

    def test_the_pm_import_template(self, lab):
        c = _client(Refusing(
            lab, fail_read=lambda sql: "lem_machine_status" in sql))
        # A template listing no equipment is filled in by hand and matches
        # nothing, which is the one error this format cannot recover from.
        unreadable(c.get("/api/maintenance-import/template.csv"))

    def test_the_log_export_downloads_nothing_rather_than_a_short_file(
            self, lab):
        # The JSON route can carry a banner next to a partial list. A file
        # cannot — so an incomplete export is refused outright rather than
        # filed as the lab's history.
        c = _client(Refusing(lab,
                             fail_read=lambda sql: "lem_machine_log" in sql))
        r = c.get("/api/logs.csv")
        unreadable(r)
        assert "text/csv" not in r.headers.get("Content-Type", "")

    def test_the_kind_filter_never_caches_a_failed_read(self, lab):
        """`_page` keeps whatever it is given, and only a config change drops
        this key — so one timed-out DISTINCT could sit in the filter for days.

        UPDATED 2026-08-25. This asserted `kinds == []` on the failure, which
        pinned in place the very conflation the route now avoids: an empty list
        is what a lab with no log yet honestly has, and answering it to a
        failed read is the same "could not ask" served as a fact that the rest
        of this suite is about. The failure now falls back to the vocabulary
        this app writes and flags `kinds_known: false`. What this test is
        actually for — that the fallback is not cached — is unchanged and
        asserted below.
        """
        gw = Refusing(lab, fail_read=lambda sql: "DISTINCT kind" in sql)
        c = _client(gw)
        body = c.get("/api/logs").get_json()
        assert body["kinds_known"] is False
        assert body["kinds"], "the filter went blank because a read failed"
        gw.fail_read = lambda sql: False
        healed = c.get("/api/logs").get_json()
        assert healed["kinds_known"] is True
        assert healed["kinds"] != []

    def test_the_log_reports_the_blip_in_the_shape_the_page_reads(self, lab):
        # This route already had the right answer shape, so it keeps its 200 and
        # its banner rather than becoming a 503 the page cannot render.
        c = _client(Refusing(lab,
                             fail_read=lambda sql: "lem_machine_log" in sql))
        r = c.get("/api/logs")
        assert r.status_code == 200
        body = r.get_json()
        assert body["error"] and body["events"] == []
        assert isinstance(body["kinds"], list)


class TestTheMapLockDegradesTowardsLocked:
    """The one route that still answers 200 on a LabCore failure, and why."""

    def test_it_holds_the_floor_locked_rather_than_erroring(self, lab):
        c = _client(Refusing(
            lab, fail_read=lambda sql: "lem_map_settings" in sql))
        r = c.get("/api/map")
        # Every open floor screen polls this every two seconds. A 503 would put
        # a banner on every wall display in the lab for a blip.
        assert r.status_code == 200
        body = r.get_json()
        assert body["locked"] is True
        assert body["known"] is False and body["error"]

    def test_a_readable_lock_is_marked_as_known(self, lab):
        body = _client(lab).get("/api/map").get_json()
        assert body["known"] is True


# ── the panels: a failure the page ignores is still a silent failure ───────

def _tpl(name):
    import pathlib
    return (pathlib.Path(__file__).resolve().parent.parent / "templates"
            / name).read_text(encoding="utf-8")


class TestThePagesReadTheStatusTheyGetBack:
    """Making a route honest changes nothing if the panel drops the answer.

    Several of these read `.then(r => r.json())` with no `r.ok` check, which was
    harmless while every route answered 200 — and is exactly wrong now, because
    a 503 carries a JSON body too. `{error: …}` then flowed in as data and
    rendered as an empty schedule, an empty archive, a bench with no
    corrections.

    WHAT THIS CLASS IS AND IS NOT (2026-08-25). Everything below greps the
    template for a string. That is honest coverage for "does this call site
    exist" — a call site either is written or is not, and a rename or a
    refactor that drops one is exactly what these catch. It is NOT coverage of
    BEHAVIOUR, and it was standing in for behaviour coverage of `failure()`,
    which is the single function every write on the floor is judged by.

    Demonstrated, not assumed: replacing the body of `failure()` with

        const unused = b.error || '…';
        return null;

    leaves every string these tests look for in the file, so all twelve pass —
    while every dialog on the floor closes on "Saved" for a write LabCore
    refused, which is the whole branch undone. `test_the_floor_actually_runs`
    below runs the function instead and fails on it in three cases.
    """

    def test_the_floor_actually_runs_its_reader(self):
        """Execute `failure()`, do not grep for it.

        tests/js/floorboot.mjs pulls the page's classic script into a `vm`
        context against a stub DOM and calls `failure()` with response objects
        shaped like the ones `_labcore_failed` and `_labcore_unreadable` send.
        A grep cannot tell a working function from a gutted one; the engine
        can, and it is the same engine the browser uses.
        """
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            pytest.skip("node is not installed; run tests/js/floorboot.mjs "
                        "directly on a machine that has it")
        here = os.path.dirname(os.path.abspath(__file__))
        done = subprocess.run(
            [node, os.path.join(here, "js", "floorboot.mjs")],
            capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, done.stdout + done.stderr
        # And the assertion this test exists for actually ran, rather than the
        # harness quietly skipping it after an earlier failure.
        assert "failure() judges" in done.stdout, done.stdout

    def test_the_floor_has_one_reader_for_a_refused_write(self):
        src = _tpl("floor.html")
        assert "async function failure(r" in src
        # Kept as a NAME check only. What the body does is settled by
        # test_the_floor_actually_runs_its_reader above; this one catches the
        # rename, which that one reports differently ("failure() is gone").
        #
        # It no longer reads `b.error` itself. The formatting moved into
        # `LEM.failure` (static/lem.js) so every page says the same thing about
        # a refusal — including `landed`/`not_landed` and the retry hint, which
        # this local copy never carried. ONE reader that DELEGATES is what is
        # held; the arity is not, because a caller with a better fallback
        # sentence of its own passes it in.
        body = src.split("async function failure(r", 1)[1].split("\n}", 1)[0]
        assert "if (r.ok) return null;" in body
        assert "LEM.failure(" in body

    @pytest.mark.parametrize("call", [
        # every write on the floor that used to be fire-and-forget
        "await failure(gone)",           # renaming a QC standard
        "await failure(r)",              # the rest
    ])
    def test_the_floor_checks_before_it_closes_a_dialog(self, call):
        assert call in _tpl("floor.html")

    def test_the_floor_never_swallows_a_qc_sample_delete(self):
        src = _tpl("floor.html")
        # Both delete paths (the rename's tidy-up and the explicit button) run
        # through `failure`, so neither can leave two lots under one Lab ID.
        assert src.count("'/api/qc-samples', {method: 'DELETE'") == 2
        # The explicit delete asks in the page's own sheet now, not a native
        # confirm(), so its refusal is RETURNED to that sheet rather than
        # painted behind it. Either way it is read and shown, which is the
        # thing this test exists to hold.
        assert "$('#sampleErr').textContent = bad" in src or (
            "askConfirm({" in src and "return bad;" in src)

    def test_the_qc_assignment_sheet_stays_open_on_a_failure(self):
        src = _tpl("floor.html")
        head = src.split("$('#qcSave')", 1)[1][:900]
        assert "alert(bad)" in head and "return;" in head

    def test_a_partial_completion_reaches_the_person(self):
        # The reschedule landed and the history line did not. Both pages that
        # can complete a task have to show that, or the audit gap is invisible.
        assert "logged === false" in _tpl("floor.html")
        assert "logged === false" in _tpl("maintenance.html")

    def test_the_map_lock_says_when_it_is_a_fallback(self):
        src = _tpl("floor.html")
        assert "LOCK_KNOWN" in src
        assert "mp.known !== false" in src

    def test_the_corrections_dialog_never_falls_back_to_zero(self):
        src = _tpl("floor.html")
        assert "{corrections: [], methods: []}" not in src, \
            "showing 0.0 for a bench running at -3.0 is the failure itself"

    def test_the_pm_panels_do_not_render_a_blip_as_nothing_scheduled(self):
        src = _tpl("floor.html")
        assert "{tasks: []}" not in src
        assert "{history: []}" not in src

    def test_the_checklist_archive_tells_unreadable_from_empty(self):
        src = _tpl("checklists.html")
        assert "This is not an empty archive" in src

    def test_the_maintenance_page_tells_unreadable_from_nothing_done(self):
        assert "This is not an empty record" in _tpl("maintenance.html")

    def test_the_logs_page_still_shows_its_banner(self):
        # /api/logs keeps its 200 + `error` shape, so this must stay.
        assert "b.error" in _tpl("logs.html")
