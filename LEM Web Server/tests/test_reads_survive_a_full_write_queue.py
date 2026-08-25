#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A full WRITE queue must not take down a READ-ONLY page.

This is the regression the confirm-every-write branch introduced, and it is the
exact inversion of the bug the branch exists to fix.

Making `ensure_schema()` strict was right: `_ready` set on an unread answer
meant a refused CREATE was remembered as done and every INSERT afterwards was
aimed at a table that was never made. What was wrong was leaving
`ensure_schema()` on the READ paths. `ChecklistStore.all()`, `state()` and
`values()`, `MaintenanceStore._rows()`, `MachineConfigStore.list()`/`get()`,
every read in `machine_map`, and `web_app._corrections()` all declared their
schema before selecting — so once the queue was past ~100 pending, a
`CREATE TABLE IF NOT EXISTS` for a table that had existed for months was
refused, the strict declaration raised, and the page 503'd.

Which means: during exactly the congestion this branch was written to survive,
the floor, the rounds, the PM dialogs and the corrections editor all went dark,
while the data they wanted to show was sitting in LabCore, readable, the whole
time. And each of those refused CREATEs was itself another write shovelled into
the queue that was already full.

THE RULE THESE TESTS PIN

    A read declares nothing. It selects, and if the table genuinely is not
    there yet the read itself answers "no such table" — the one error
    `labcore_result.rows()` is allowed to call empty, because a table nobody
    has created holds nothing. Every other read error still raises.

    Write paths keep the strict declaration. A write into a table that may not
    exist is the bug this branch fixed and it stays fixed —
    `test_writes_still_refuse_to_declare_a_schema_they_could_not_create` below
    holds that half in place, so the fix here cannot be "made to pass" by
    loosening `ensure_schema` itself.

The refusal shape driven here is the EVIDENCED one (notes.md,
lem_station_module.py:495): an error dict with `busy`, returned normally rather
than raised. Read paths are also driven with a plain read timeout, since a read
and a write fail in different ways and only one of them may degrade to empty.
"""
import pytest

from labcore_gateway import FakeLabCoreGateway
from web_app import create_app

# What LabCore actually answers when its queue is past ~100 pending: measured,
# not invented. See tests/refusal_shapes.py for the distinction.
BUSY = {"error": "LabCore is busy, try again later", "busy": True,
        "retry_after": 4}

UID = "m1"


class StubAuth:
    def login(self, username, password):
        return ("kaden", "tok", "")

    def logout(self, token):
        pass


class WriteQueueFull:
    """Reads work perfectly. Every write is ANSWERED with a refusal.

    Including `CREATE TABLE IF NOT EXISTS`, which is the whole point: a full
    queue does not spare DDL, and the tables in question already exist.
    """

    def __init__(self, real):
        self.real = real
        self.refused = []

    def sql(self, sql, args=None, **kw):
        self.refused.append(sql)
        return dict(BUSY)

    def read_sql(self, sql, args=None, **kw):
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


@pytest.fixture
def congested():
    """A real lab, fully seeded, whose write queue then fills up.

    Seeded through the healthy app so every table exists and holds something —
    the state a lab is actually in when the queue backs up. The client handed
    back can only read.
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
    healthy.post("/api/checklists", json={
        "uid": "cl1", "name": "Opening round", "slot": "opening",
        "items": [{"uid": "i1", "text": "Check the argon"},
                  {"uid": "i2", "text": "Bath temperature",
                   "entry_type": "number", "units": "°C"}]})
    healthy.post(f"/api/machines/{UID}/maintenance", json={
        "name": "Annual calibration", "kind": "calibration",
        "interval_days": 365, "last_done": "2026-01-05"})
    healthy.post(f"/api/machine-configs/{UID}", json={
        "title": "Multitek NS", "config": {"uid": UID, "mappings": [
            {"methods": ["Flash Point"]}]}})
    healthy.post(f"/api/machines/{UID}/corrections",
                 json={"test_name": "Flash Point", "correction": -3.0})

    blocked = WriteQueueFull(gw)
    return _client(blocked), blocked, gw


# Every GET a lab has open on a wall or in a dialog while the queue is full.
# `id` names each one the way an operator would.
READ_ONLY = [
    ("today's round", "/api/checklists"),
    ("a numeric item's trend", "/api/checklists/cl1/values?item=i2"),
    ("the checklist archive", "/api/checklists/history"),
    ("this bench's PM and calibration", f"/api/machines/{UID}/maintenance"),
    ("what is overdue anywhere", "/api/maintenance"),
    ("the machine-config picker", "/api/machine-configs"),
    ("one machine's configuration", f"/api/machine-configs/{UID}"),
    ("the corrections dialog", f"/api/machines/{UID}/corrections"),
    ("the QC bands", "/api/qc-specs"),
    ("the QC standards library", "/api/qc-samples"),
    ("the floor's machine list", "/api/machines"),
    ("the lab's opening hours", "/api/schedule"),
    ("the map lock", "/api/map"),
    ("the log", "/api/logs"),
]


@pytest.mark.parametrize("what,url", READ_ONLY, ids=[r[0] for r in READ_ONLY])
def test_a_read_only_page_still_answers_while_writes_are_refused(
        congested, what, url, open_for_business):
    """The regression, one route at a time.

    Nothing on this list writes. Every one of them was 503-ing because a
    `CREATE TABLE IF NOT EXISTS` for a table that already existed came back
    refused.
    """
    client, blocked, _gw = congested
    res = client.get(url)
    assert res.status_code == 200, (
        f"{what} ({url}) answered {res.status_code} because a WRITE was "
        f"refused. Nothing on this path writes.")


def test_the_round_still_has_its_items_not_just_a_200(congested,
                                                      open_for_business):
    """A 200 carrying an empty round would be the older, worse bug.

    So this asserts the DATA survives, not just the status: the whole point is
    that LabCore could be read the entire time.
    """
    client, _blocked, _gw = congested
    body = client.get("/api/checklists").get_json()
    names = [c["name"] for c in body["checklists"]]
    assert "Opening round" in names
    assert [i["text"] for i in body["checklists"][0]["items"]]


def test_the_pm_dialog_still_shows_the_calibration(congested):
    client, _blocked, _gw = congested
    body = client.get(f"/api/machines/{UID}/maintenance").get_json()
    assert [t["name"] for t in body["tasks"]] == ["Annual calibration"]


def test_the_corrections_dialog_still_shows_minus_three(congested):
    """The one that is a compliance control: showing 0.0 for a bench running at
    -3.0 invites the operator to type the offset in a second time."""
    client, _blocked, _gw = congested
    body = client.get(f"/api/machines/{UID}/corrections").get_json()
    saved = {c["test_name"]: c["correction"] for c in body["corrections"]}
    assert saved == {"Flash Point": -3.0}


def test_no_read_pushed_another_write_into_the_full_queue(congested,
                                                          open_for_business):
    """A refused CREATE is not free — it is one more op in a queue that is
    already refusing, issued once per read for the life of the process.

    The old code did this on every single read. Reads must now issue no writes
    at all.
    """
    client, blocked, _gw = congested
    for _what, url in READ_ONLY:
        client.get(url)
    assert blocked.refused == [], (
        "a read-only request wrote to LabCore: " + "; ".join(
            s.split("(")[0][:60] for s in blocked.refused))


class TestTheReadRuleIsStillHonest:
    """The fix must not become "reads swallow everything"."""

    def test_a_read_that_could_not_be_asked_still_fails(self, congested):
        """A read TIMEOUT is not a missing table and must never be empty.

        This is the half of the branch that has to survive the fix: "no PM
        scheduled" invented out of a read that never happened is how a
        calibration gets missed.
        """
        client, blocked, gw = congested

        def timeout(sql, args=None, **kw):
            return {"error": "HTTPSConnectionPool(host='labvision'): Read "
                             "timed out"}

        blocked.read_sql = timeout
        res = client.get(f"/api/machines/{UID}/maintenance")
        assert res.status_code in (502, 503)
        assert "tasks" not in res.get_json()

    def test_a_missing_table_reads_as_empty_rather_than_failing(self):
        """The one error a read may swallow. On a LabCore where nothing has
        ever been created, a picker with nothing in it IS the truth."""
        gw = FakeLabCoreGateway()          # no tables at all
        client = _client(WriteQueueFull(gw))
        res = client.get("/api/machine-configs")
        assert res.status_code == 200
        assert res.get_json()["configs"] == []

    def test_writes_still_refuse_to_declare_a_schema_they_could_not_create(
            self, congested):
        """The guard on the guard.

        If this fix were done by making `ensure_schema` swallow refusals, this
        test would fail: a save would report success against a table LabCore
        never agreed to make. The strict declaration belongs to writes and
        stays there.
        """
        client, _blocked, gw = congested
        res = client.post(f"/api/machines/{UID}/maintenance", json={
            "name": "Weekly PM", "kind": "pm", "interval_days": 7})
        assert res.status_code in (502, 503)
        assert res.get_json().get("saved") is False
        rows = gw.read_sql("SELECT name FROM lem_maintenance")["rows"]
        assert "Weekly PM" not in [r["name"] for r in rows]


class TestAReadCanBeREFUSEDAsWellAsUnanswerable:
    """The shape no read test in this branch was driving.

    The suite's read section drove only a TIMEOUT — `{"error": "Read timed
    out"}` — because the module docstring of test_route_write_confirmation.py
    reasoned that "a read cannot be refused with the queue-full shape". That
    reasoning is about ONE invented shape — one carrying no "error" key, which
    `rows()` correctly reads as an answered-but-empty read. It does not hold
    for the shape LabCore is actually recorded as sending:

        {"error": "LabCore is busy…", "busy": true, "retry_after": n}

    Reads and writes travel the same endpoint. A read behind a full queue gets
    turned away the same way a write does, and the difference from a timeout is
    not cosmetic: `_labcore_unreadable` answers 502 for a refusal and 503 for an
    outage, because one clears in seconds and the other means go and look.

    `labcore_result` has always got this right (`test_a_busy_read_is_unavailable
    _not_empty`). What was missing was any test that a ROUTE does.

    On the 502/503 question, see
    `test_a_refused_read_reports_as_unavailable_ON_PURPOSE` — a refused READ is
    503, and that is the shared rule rather than an oversight here.
    """

    @pytest.fixture
    def refusing_reads(self, congested):
        client, blocked, gw = congested

        def busy(sql, args=None, **kw):
            return dict(BUSY)

        blocked.read_sql = busy
        return client

    @pytest.mark.parametrize("what,url", [
        ("this bench's PM and calibration", f"/api/machines/{UID}/maintenance"),
        ("one machine's configuration", f"/api/machine-configs/{UID}"),
        ("the machine-config picker", "/api/machine-configs"),
        ("the corrections dialog", f"/api/machines/{UID}/corrections"),
        ("the QC bands", "/api/qc-specs"),
        ("the QC standards library", "/api/qc-samples"),
        ("today's round", "/api/checklists"),
    ])
    def test_a_refused_read_is_not_an_empty_answer(self, refusing_reads, what,
                                                   url, open_for_business):
        res = refusing_reads.get(url)
        assert res.status_code in (502, 503), (
            f"{what} answered {res.status_code} to a REFUSED read — a busy "
            f"queue must not read as an empty lab")

    def test_a_refused_read_reports_as_unavailable_ON_PURPOSE(self):
        """Documenting the rule rather than quietly changing it.

        `labcore_result.rows()` raises `LabCoreUnavailable` for EVERY read
        failure, refusals included, and its own suite pins that
        (`test_a_busy_read_is_unavailable_not_empty`). So a refused read is
        503, not the 502 a refused WRITE gets, and the first draft of this test
        asserted 502 and was wrong.

        It is not worth "tightening". The 502/503 split exists to tell an
        operator whether their work was saved — "LabCore said no" versus "I
        never got an answer". A READ saved nothing either way, and after a
        refused read the state of the world is equally unknown, which is
        precisely what `LabCoreUnavailable` means. Re-deriving a different
        verdict here, in one store, is how this app ended up with three
        different answers to one question in a single week.
        """
        gw = FakeLabCoreGateway()
        from snapshot_service import SnapshotService
        SnapshotService(gw).ensure_schema()
        blocked = WriteQueueFull(gw)
        blocked.read_sql = lambda sql, args=None, **kw: dict(BUSY)
        res = _client(blocked).get(f"/api/machines/{UID}/maintenance")
        assert res.status_code == 503
        assert res.get_json()["labcore"] == "unavailable"
        assert res.get_json()["retry"] is True

    def test_the_reason_reaches_the_page(self, refusing_reads):
        """"LabCore said no" sends someone to a log file. "LabCore said no,
        retry in 4s" tells them it is a queue and it will clear."""
        detail = refusing_reads.get(
            f"/api/machines/{UID}/maintenance").get_json()["detail"]
        assert "busy" in detail.lower()
        assert "retry_after" in detail
