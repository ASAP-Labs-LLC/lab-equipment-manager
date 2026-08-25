"""Per-machine equipment record on the floor map — V4's model, kept.

V4 gave every box a position on the map and a list of *watched targets*:
which QC sample + test that machine is checked against. Both survive here,
stored in LabCore so the station modules and every viewer agree.
"""
import pytest

from labcore_gateway import FakeLabCoreGateway
from machine_map import MachineLayoutStore, QcTargetStore, WatchedTarget


@pytest.fixture
def gw():
    return FakeLabCoreGateway()


# ── where each instrument stands on the floor ────────────────────────────────

class TestMachineLayoutStore:
    def test_position_round_trip(self, gw):
        store = MachineLayoutStore(gw)
        store.save_position("m1", 2.0, 3.5)
        assert store.positions() == {"m1": (2.0, 3.5)}

    def test_position_is_upserted(self, gw):
        store = MachineLayoutStore(gw)
        store.save_position("m1", 1, 1)
        store.save_position("m1", 4, 0)
        assert store.positions() == {"m1": (4.0, 0.0)}

    def test_missing_table_is_empty_not_an_error(self, gw):
        assert MachineLayoutStore(gw).positions() == {}

    def test_forget_drops_a_machine(self, gw):
        store = MachineLayoutStore(gw)
        store.save_position("m1", 1, 1)
        store.forget("m1")
        assert store.positions() == {}


# ── which QC sample + test each instrument is checked against ────────────────

class TestQcTargetStore:
    def test_assign_and_list(self, gw):
        store = QcTargetStore(gw)
        store.assign("m1", [WatchedTarget("Cloud CRM", "Cloud Point")])
        assert store.targets("m1") == [WatchedTarget("Cloud CRM", "Cloud Point")]

    def test_assign_replaces_the_whole_set(self, gw):
        store = QcTargetStore(gw)
        store.assign("m1", [WatchedTarget("Cloud CRM", "Cloud Point"),
                            WatchedTarget("Cloud CRM", "Cloud Point, mini method")])
        store.assign("m1", [WatchedTarget("Pour CRM", "Pour Point")])
        assert store.targets("m1") == [WatchedTarget("Pour CRM", "Pour Point")]

    def test_targets_are_scoped_per_machine(self, gw):
        store = QcTargetStore(gw)
        store.assign("m1", [WatchedTarget("Cloud CRM", "Cloud Point")])
        store.assign("m2", [WatchedTarget("Pour CRM", "Pour Point")])
        assert store.targets("m1")[0].sample == "Cloud CRM"
        assert store.targets("m2")[0].sample == "Pour CRM"

    def test_clearing_assignments(self, gw):
        store = QcTargetStore(gw)
        store.assign("m1", [WatchedTarget("Cloud CRM", "Cloud Point")])
        store.assign("m1", [])
        assert store.targets("m1") == []

    def test_blank_entries_are_dropped(self, gw):
        store = QcTargetStore(gw)
        store.assign("m1", [WatchedTarget("", "Cloud Point"),
                            WatchedTarget("Cloud CRM", "  "),
                            WatchedTarget("Cloud CRM", "Cloud Point")])
        assert store.targets("m1") == [WatchedTarget("Cloud CRM", "Cloud Point")]

    def test_all_returns_every_machine(self, gw):
        store = QcTargetStore(gw)
        store.assign("m1", [WatchedTarget("Cloud CRM", "Cloud Point")])
        store.assign("m2", [WatchedTarget("Pour CRM", "Pour Point")])
        assert set(store.all()) == {"m1", "m2"}

    def test_missing_table_is_empty(self, gw):
        assert QcTargetStore(gw).targets("m1") == []


# ── Web API ─────────────────────────────────────────────────────────────────

class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


@pytest.fixture
def client(gw):
    from web_app import create_app
    app = create_app(gw, authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client()


def login(c):
    c.post("/api/login", json={"username": "k", "password": "good"})


class TestMapApi:
    def seed(self, gw, uid="m1"):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
               "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
               "reason TEXT, updated_at TEXT)")
        gw.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
               [uid, "OptiMPP 1", "UNKNOWN", "r", "2026-07-30T12:00:00"])

    def test_machines_payload_carries_position_and_targets(self, gw, client):
        self.seed(gw)
        login(client)
        client.post("/api/machines/m1/position", json={"x": 3, "y": 2})
        client.post("/api/machines/m1/qc-targets", json={"targets": [
            {"sample": "Cloud CRM", "test": "Cloud Point"}]})
        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["pos"] == [3.0, 2.0]
        assert m["qc_targets"] == [{"sample": "Cloud CRM", "test": "Cloud Point"}]

    def test_position_requires_auth(self, gw, client):
        self.seed(gw)
        assert client.post("/api/machines/m1/position",
                           json={"x": 1, "y": 1}).status_code == 401

    def test_qc_targets_require_auth(self, gw, client):
        assert client.post("/api/machines/m1/qc-targets",
                           json={"targets": []}).status_code == 401

    def test_bad_position_is_400(self, gw, client):
        login(client)
        assert client.post("/api/machines/m1/position",
                           json={"x": "over there"}).status_code == 400

    def test_deleting_a_machine_clears_its_map_record(self, gw, client):
        self.seed(gw)
        login(client)
        client.post("/api/machines/m1/position", json={"x": 3, "y": 2})
        client.post("/api/machines/m1/qc-targets", json={"targets": [
            {"sample": "Cloud CRM", "test": "Cloud Point"}]})
        client.delete("/api/machines/m1")
        assert MachineLayoutStore(gw).positions() == {}
        assert QcTargetStore(gw).targets("m1") == []


# ── the map lock: V4's map_locked, now shared by everyone viewing ───────────

class TestMapSettings:
    def test_defaults_to_unlocked(self, gw):
        from machine_map import MapSettingsStore
        assert MapSettingsStore(gw).locked() is False

    def test_lock_round_trips(self, gw):
        from machine_map import MapSettingsStore
        s = MapSettingsStore(gw)
        s.set_locked(True)
        assert s.locked() is True
        s.set_locked(False)
        assert s.locked() is False

    def test_lock_is_global_not_per_session(self, gw):
        from machine_map import MapSettingsStore
        MapSettingsStore(gw).set_locked(True)
        assert MapSettingsStore(gw).locked() is True   # a fresh reader agrees


class TestMapLockApi:
    def test_lock_state_is_reported(self, gw, client):
        assert client.get("/api/map").get_json()["locked"] is False

    def test_locking_requires_auth(self, client):
        assert client.post("/api/map", json={"locked": True}).status_code == 401

    def test_lock_then_positions_are_refused(self, gw, client):
        login(client)
        client.post("/api/map", json={"locked": True})
        assert client.get("/api/map").get_json()["locked"] is True
        r = client.post("/api/machines/m1/position", json={"x": 1, "y": 2})
        assert r.status_code == 409          # the map is locked for everyone
        assert "locked" in r.get_json()["error"].lower()

    def test_unlocking_allows_moves_again(self, gw, client):
        login(client)
        client.post("/api/map", json={"locked": True})
        client.post("/api/map", json={"locked": False})
        assert client.post("/api/machines/m1/position",
                           json={"x": 1, "y": 2}).status_code == 200


# ── QC / PM / CAL pills published by the station modules ────────────────────

class TestSubStatusApi:
    def seed(self, gw, uid="m1"):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
               "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
               "reason TEXT, updated_at TEXT)")
        gw.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
               [uid, "OptiMPP 1", "RED", "QC out of spec", "2026-07-30T12:00:00"])

    def test_pills_are_exposed(self, gw, client):
        self.seed(gw)
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_substatus ("
               "machine_uid TEXT PRIMARY KEY, qc TEXT, pm TEXT, "
               "calibration TEXT, updated_at TEXT)")
        gw.sql("INSERT INTO lem_machine_substatus VALUES (?,?,?,?,?)",
               ["m1", "RED", "GREEN", "YELLOW", "2026-07-30T12:00:00"])
        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["sub_statuses"] == {"qc": "RED", "pm": "GREEN",
                                     "calibration": "YELLOW"}

    def test_missing_substatus_row_defaults_to_unknown(self, gw, client):
        self.seed(gw)
        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["sub_statuses"] == {"qc": "UNKNOWN", "pm": "UNKNOWN",
                                     "calibration": "UNKNOWN"}


class TestLastActivity:
    """Status rows are only rewritten when the status CHANGES, so a healthy
    machine's updated_at goes stale while it is happily working. Last
    activity must come from the log, or the floor lies about liveness."""

    def seed(self, gw, uid="m1", status_ts="2026-07-28T16:00:00"):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
               "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
               "reason TEXT, updated_at TEXT)")
        gw.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
               [uid, "Multitek NS", "GREEN", "System nominal", status_ts])
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log ("
               "machine_uid TEXT, ts TEXT, kind TEXT, lab_id TEXT, "
               "test_name TEXT, value TEXT, detail TEXT)")

    def log(self, gw, uid, ts, kind="run"):
        gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
               [uid, ts, kind, "37157", "", "", "{}"])

    def test_last_activity_comes_from_the_log(self, gw, client):
        self.seed(gw)
        self.log(gw, "m1", "2026-07-30T14:21:24")
        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["last_activity"] == "2026-07-30T14:21:24"
        assert m["updated_at"] == "2026-07-28T16:00:00"   # status unchanged

    def test_machine_with_no_events_falls_back_to_status_time(self, gw, client):
        self.seed(gw)
        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["last_activity"] == "2026-07-28T16:00:00"

    def test_newest_event_wins(self, gw, client):
        self.seed(gw)
        self.log(gw, "m1", "2026-07-30T09:00:00")
        self.log(gw, "m1", "2026-07-30T14:21:24")
        self.log(gw, "m1", "2026-07-29T08:00:00")
        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["last_activity"] == "2026-07-30T14:21:24"


class TestModuleLiveness:
    """Three distinct states the floor must tell apart:
    module beating + data      → working
    module beating, no data    → instrument idle (fine)
    no beat                    → module stopped / never ran (actionable)
    """

    def seed(self, gw, uid="m1", title="Multitek S"):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
               "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
               "reason TEXT, updated_at TEXT)")
        gw.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
               [uid, title, "UNKNOWN", "No valid QC data found.",
                "2026-07-28T16:23:58"])

    def beat(self, gw, uid, ts, watching="serial COM4 @9600"):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_heartbeat ("
               "machine_uid TEXT PRIMARY KEY, last_poll TEXT, watching TEXT)")
        gw.sql("INSERT INTO lem_machine_heartbeat VALUES (?,?,?)",
               [uid, ts, watching])

    def test_heartbeat_is_exposed(self, gw, client):
        self.seed(gw)
        self.beat(gw, "m1", "2026-07-31T14:00:00")
        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["last_poll"] == "2026-07-31T14:00:00"
        assert m["watching"] == "serial COM4 @9600"

    def test_module_that_never_checked_in_is_flagged(self, gw, client):
        self.seed(gw)                       # registered, never beat
        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["last_poll"] is None
        assert m["module_running"] is False

    def test_recent_beat_counts_as_running(self, gw, client):
        import datetime
        self.seed(gw)
        self.beat(gw, "m1", datetime.datetime.now().isoformat())
        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["module_running"] is True

    def test_stale_beat_is_not_running(self, gw, client):
        self.seed(gw)
        self.beat(gw, "m1", "2026-01-01T00:00:00")
        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["module_running"] is False


class TestLivenessFallback:
    """Modules built before the heartbeat existed never check in. Recent
    DATA is proof of life too — claiming those are 'not running' would be
    a false alarm on every instrument until each LabStation is updated."""

    def seed(self, gw, uid="m1"):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
               "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
               "reason TEXT, updated_at TEXT)")
        gw.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
               [uid, "Multitek NS", "UNKNOWN", "r", "2026-07-28T16:00:00"])
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log ("
               "machine_uid TEXT, ts TEXT, kind TEXT, lab_id TEXT, "
               "test_name TEXT, value TEXT, detail TEXT)")

    def log(self, gw, uid, ts):
        gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
               [uid, ts, "run", "37169", "", "", "{}"])

    def test_recent_data_counts_as_running_without_a_heartbeat(self, gw, client):
        import datetime
        self.seed(gw)
        self.log(gw, "m1", datetime.datetime.now().isoformat())
        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["module_running"] is True
        assert m["module_state"] == "running"
        assert m["last_poll"] is None          # honest: it never checked in

    def test_no_heartbeat_and_stale_data_is_UNKNOWN_not_stopped(self, gw, client):
        """It may simply be an older module build, or an idle bench. Saying
        'stopped' here would be a false alarm."""
        self.seed(gw)
        self.log(gw, "m1", "2026-07-28T16:23:00")
        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["module_state"] == "unknown"
        assert m["module_running"] is False

    def test_a_module_that_beat_and_then_stopped_is_STOPPED(self, gw, client, open_for_business):
        """Here we have proof: it was checking in, and no longer is."""
        self.seed(gw)
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_heartbeat ("
               "machine_uid TEXT PRIMARY KEY, last_poll TEXT, watching TEXT)")
        gw.sql("INSERT INTO lem_machine_heartbeat VALUES (?,?,?)",
               ["m1", "2026-01-01T00:00:00", "serial COM4 @9600"])
        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["module_state"] == "stopped"

    def test_nothing_at_all_is_unknown(self, gw, client):
        self.seed(gw)
        m = client.get("/api/machines?fresh=1").get_json()["machines"][0]
        assert m["module_state"] == "unknown"
        assert m["module_running"] is False


# ── the write queue answers "no", and the answer must be read ────────────────
#
# LabCore's write queue serialises at ~1.5 writes/sec and REFUSES past ~100
# pending by ANSWERING rather than raising.
# Every test in this module runs TWICE, once per refusal shape — see
# tests/refusal_shapes.py for which of the two is evidence and which is a
# fixture. In short: the error dict carrying `busy` is recorded from a real
# incident; the one with no "error" key is synthetic, kept because
# `{"error": ...}` is the ONE shape the old `if not res.get("error")` code
# already handled, so a suite refusing only that way proves nothing.

from labcore_result import LabCoreError, LabCoreRefused, LabCoreUnavailable
from machine_map import (MachineMapError, MapReadUnavailable, MapSettingsStore,
                         MapWriteRefused)

import refusal_shapes                                   # noqa: E402

pytestmark = pytest.mark.usefixtures("both_refusal_shapes")

# SYNTHETIC — a dict that is not an error and not an acknowledgement either.
# Chosen for that property; not a body anyone has seen. See refusal_shapes.
QUEUE_FULL = refusal_shapes.NO_ERROR_KEY


class QueueFullGateway:
    """A real gateway with a full write queue in front of it.

    Reads still answer — the queue serialises writes, not reads — which is what
    lets each test assert the row was NEVER WRITTEN rather than merely that a
    call raised. `refuse_when` defaults to "everything except CREATE", so the
    schema still forms and the store under test is exercised past ensure_schema.
    """

    def __init__(self, inner, refuse_when=None, answer=None):
        self.inner = inner
        # `None` means "whichever shape this run of the suite is driving".
        self.answer = answer
        self.refuse_when = refuse_when or (
            lambda sql: not sql.lstrip().upper().startswith("CREATE"))
        self.refused = []

    def sql(self, sql, args=None, **kw):
        if self.refuse_when(sql):
            self.refused.append(sql)
            if self.answer is None:
                return refusal_shapes.current()
            return self.answer
        return self.inner.sql(sql, args, **kw)

    def read_sql(self, sql, args=None, **kw):
        return self.inner.read_sql(sql, args, **kw)


class BlipGateway:
    """LabCore cannot be asked at all. Writes go through so state can be set up
    before the blip; reads time out the way this repo documents as routine."""

    def __init__(self, inner):
        self.inner = inner
        self.blind = False

    def sql(self, sql, args=None, **kw):
        return self.inner.sql(sql, args, **kw)

    def read_sql(self, sql, args=None, **kw):
        if self.blind:
            return {"error": "ReadTimeout: read timed out"}
        return self.inner.read_sql(sql, args, **kw)


class TestRefusedLayoutWrites:
    def test_a_refused_position_raises_and_saves_nothing(self, gw):
        """The floor silently refusing to remember a drag — the whole bug."""
        MachineLayoutStore(gw).save_position("m1", 1.0, 1.0)
        blocked = MachineLayoutStore(QueueFullGateway(gw))
        with pytest.raises(MapWriteRefused):
            blocked.save_position("m1", 9.0, 9.0)
        assert MachineLayoutStore(gw).positions() == {"m1": (1.0, 1.0)}

    def test_a_refused_forget_raises_and_the_row_survives(self, gw):
        MachineLayoutStore(gw).save_position("m1", 1.0, 1.0)
        blocked = MachineLayoutStore(QueueFullGateway(gw))
        with pytest.raises(MapWriteRefused):
            blocked.forget("m1")
        assert MachineLayoutStore(gw).positions() == {"m1": (1.0, 1.0)}

    def test_a_refused_create_is_not_remembered_as_done(self, gw):
        """`_ready` used to be set whatever LabCore said, so one refusal at
        boot poisoned the store for the life of the process."""
        blocking = QueueFullGateway(gw, refuse_when=lambda sql: True)
        store = MachineLayoutStore(blocking)
        with pytest.raises(MapWriteRefused):
            store.save_position("m1", 1.0, 1.0)
        blocking.refuse_when = lambda sql: False
        store.save_position("m1", 2.0, 3.0)          # retried, not stuck
        assert MachineLayoutStore(gw).positions() == {"m1": (2.0, 3.0)}

    @pytest.mark.parametrize("answer", [None, {"ok": False},
                                        refusal_shapes.NO_ERROR_KEY])
    def test_silence_is_never_success(self, gw, answer):
        """None, {} and a queue refusal all mean the row was never written."""
        blocked = MachineLayoutStore(QueueFullGateway(gw, answer=answer))
        with pytest.raises(MapWriteRefused):
            blocked.save_position("m1", 1.0, 1.0)
        assert MachineLayoutStore(gw).positions() == {}


class TestRefusedTargetWrites:
    def test_a_refused_assign_raises_and_leaves_the_old_set(self, gw):
        """An instrument the lab believes is being checked, and is not."""
        QcTargetStore(gw).assign("m1", [WatchedTarget("Cloud CRM", "Cloud Point")])
        blocked = QcTargetStore(QueueFullGateway(gw))
        with pytest.raises(MapWriteRefused):
            blocked.assign("m1", [WatchedTarget("Pour CRM", "Pour Point")])
        assert QcTargetStore(gw).targets("m1") == [
            WatchedTarget("Cloud CRM", "Cloud Point")]

    def test_a_refused_insert_after_a_good_delete_still_raises(self, gw):
        """The queue can fill mid-assignment. There is no transaction, so the
        honest answer is a raise that says the set is now incomplete — never a
        return that lets the route reply "ok"."""
        blocked = QcTargetStore(QueueFullGateway(
            gw, refuse_when=lambda sql: sql.lstrip().upper().startswith("INSERT")))
        with pytest.raises(MapWriteRefused) as caught:
            blocked.assign("m1", [WatchedTarget("Cloud CRM", "Cloud Point")])
        assert "re-applied" in str(caught.value)
        assert QcTargetStore(gw).targets("m1") == []

    def test_a_refused_forget_raises_and_the_assignment_survives(self, gw):
        QcTargetStore(gw).assign("m1", [WatchedTarget("Cloud CRM", "Cloud Point")])
        blocked = QcTargetStore(QueueFullGateway(gw))
        with pytest.raises(MapWriteRefused):
            blocked.forget("m1")
        assert QcTargetStore(gw).targets("m1") == [
            WatchedTarget("Cloud CRM", "Cloud Point")]


class TestRefusedLockWrites:
    def test_a_refused_lock_raises_and_the_map_stays_unlocked(self, gw):
        blocked = MapSettingsStore(QueueFullGateway(gw))
        with pytest.raises(MapWriteRefused):
            blocked.set_locked(True)
        assert MapSettingsStore(gw).locked() is False

    def test_a_refused_unlock_raises_and_the_map_stays_locked(self, gw):
        MapSettingsStore(gw).set_locked(True)
        blocked = MapSettingsStore(QueueFullGateway(gw))
        with pytest.raises(MapWriteRefused):
            blocked.set_locked(False)
        assert MapSettingsStore(gw).locked() is True


class TestReadsDoNotInventAnEmptyFloor:
    def test_a_blip_does_not_unlock_the_map(self, gw):
        """This read GATES A WRITE — api_machine_position asks it before
        saving a drag. Answering False during a blip would silently unlock a
        map the lab froze."""
        blip = BlipGateway(gw)
        store = MapSettingsStore(blip)
        store.set_locked(True)
        blip.blind = True
        with pytest.raises(MapReadUnavailable):
            store.locked()

    def test_a_blip_does_not_empty_the_floor(self, gw):
        blip = BlipGateway(gw)
        store = MachineLayoutStore(blip)
        store.save_position("m1", 1.0, 1.0)
        blip.blind = True
        with pytest.raises(MapReadUnavailable):
            store.positions()

    def test_a_blip_does_not_report_no_qc_assigned(self, gw):
        """"No QC assigned" is a verdict the floor paints grey. Painting it
        because a read timed out says an instrument is unchecked when it may be
        checked and failing."""
        blip = BlipGateway(gw)
        store = QcTargetStore(blip)
        store.assign("m1", [WatchedTarget("Cloud CRM", "Cloud Point")])
        blip.blind = True
        with pytest.raises(MapReadUnavailable):
            store.targets("m1")

    def test_a_blip_does_not_tell_a_changeover_that_nobody_is_assigned(self, gw):
        """qc_samples.changeover() walks all() to decide who to move. An empty
        answer means "0 moved" and stops QC across the lab."""
        blip = BlipGateway(gw)
        store = QcTargetStore(blip)
        store.assign("m1", [WatchedTarget("Cloud CRM", "Cloud Point")])
        blip.blind = True
        with pytest.raises(MapReadUnavailable):
            store.all()


class TestOneNameToCatch:
    def test_a_refusal_is_catchable_both_ways(self, gw):
        blocked = MachineLayoutStore(QueueFullGateway(gw))
        for expected in (MachineMapError, LabCoreRefused, LabCoreError):
            with pytest.raises(expected):
                blocked.save_position("m1", 1.0, 1.0)

    def test_a_blip_is_catchable_both_ways(self, gw):
        blip = BlipGateway(gw)
        store = MachineLayoutStore(blip)
        store.ensure_schema()
        blip.blind = True
        for expected in (MachineMapError, LabCoreUnavailable, LabCoreError):
            with pytest.raises(expected):
                store.positions()

    def test_the_message_names_what_was_lost(self, gw):
        """A queue refusal carries no hint of what it dropped; an operator
        needs to know which drag to redo."""
        blocked = MachineLayoutStore(QueueFullGateway(gw))
        with pytest.raises(MapWriteRefused) as caught:
            blocked.save_position("OptiMPP-1", 1.0, 1.0)
        assert "OptiMPP-1" in str(caught.value)
