#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The floor's own read, judged by the shared rule — and paired with its errors.

Two defects in `SnapshotService.read_tables`, both in the most consequential
read in the app: it is what the whole floor is drawn from, every 12 seconds.

ONE — it judged the answer by hand. `if not (res or {}).get("error")` in the
one file that imports `refusal_of` and uses it only for DDL. Every refusal that
does not carry an "error" key therefore read as a SUCCESSFUL read of zero rows:
every arm keyed to `[]`, `_table_errors` cleared to "nothing went wrong", and a
lab drawn with no instruments, no PM, no schedule — reported as fact. That is
the branch's own bug, in the branch's own service.

TWO — `_table_errors` outlived the read whose rows were thrown away. A failed
spine read raises `SnapshotReadError`, `refresh()` keeps the PREVIOUS snapshot,
and the floor goes on drawing from it perfectly — but the per-arm errors from
the read that failed had already been stored. So `/api/maintenance` and
`/api/schedule`, which correctly refuse to invent an answer out of a failed
arm, went dark over rows that were sitting right there and were good enough for
every other page. The errors have to describe the read the rows came from.

Driven with the EVIDENCED refusal (an error dict carrying `busy`) and with a
refusal carrying no "error" key at all. The second is synthetic — a shape
chosen because it exercises the `ok`/`queued` half of the rule — not a shape
LabCore is recorded as sending.
"""
import pytest

from labcore_gateway import FakeLabCoreGateway
from snapshot_service import SnapshotReadError, SnapshotService

BUSY = {"error": "LabCore is busy, try again later", "busy": True,
        "retry_after": 4}
BLIP = {"error": "HTTPSConnectionPool(host='labvision'): Read timed out"}
# Synthetic: no "error" key, so it is only refused by the `ok` half of the
# rule. Chosen for that property, not measured.
NO_ERROR_KEY = {"ok": False}

UID = "m1"


class Readable:
    """A real lab whose reads can be switched to a fixed answer."""

    def __init__(self, real):
        self.real = real
        self.answer = None
        self.fail = lambda sql: True

    def sql(self, sql, args=None, **kw):
        return self.real.sql(sql, args, **kw)

    def read_sql(self, sql, args=None, **kw):
        if self.answer is not None and self.fail(sql):
            return dict(self.answer)
        return self.real.read_sql(sql, args, **kw)

    def is_running(self):
        return True

    def write(self, operation, params, **kw):
        return self.real.write(operation, params, **kw)

    def get_test_names(self):
        return self.real.get_test_names()

    def get_samples(self, **kw):
        return self.real.get_samples(**kw)


@pytest.fixture
def lab():
    """A built lab with a machine, a PM task and opening hours."""
    gw = FakeLabCoreGateway()
    SnapshotService(gw).ensure_schema()
    gw.sql("INSERT INTO lem_machine_status (machine_uid, title, status, "
           "reason, updated_at) VALUES (?, 'Multitek NS', 'GREEN', '', ?)",
           [UID, "2026-08-20T09:00:00"])
    gw.sql("INSERT INTO lem_maintenance (uid, machine_uid, name, kind, "
           "interval_days, last_done, note) VALUES "
           "('t1', ?, 'Annual calibration', 'calibration', 365, "
           "'2026-01-05', '')", [UID])
    return gw


class TestTheFloorsReadUsesTheSharedRule:
    def test_a_refusal_with_no_error_key_is_not_a_successful_empty_read(
            self, lab):
        """The worst possible reading of it: every arm `[]`, no error recorded,
        and a lab drawn as if it had no equipment at all."""
        gw = Readable(lab)
        gw.answer = NO_ERROR_KEY
        svc = SnapshotService(gw)
        with pytest.raises(SnapshotReadError):
            svc.read_tables()

    def test_an_evidenced_refusal_is_not_a_successful_empty_read(self, lab):
        gw = Readable(lab)
        gw.answer = BUSY
        svc = SnapshotService(gw)
        with pytest.raises(SnapshotReadError):
            svc.read_tables()

    def test_a_busy_refusal_does_not_abandon_the_one_op_read(self, lab):
        """Busy is transient. Giving up the cheap path because the queue was
        briefly full would make the next refresh cost fifteen reads instead of
        one — against the same queue."""
        gw = Readable(lab)
        gw.answer = BUSY
        svc = SnapshotService(gw)
        with pytest.raises(SnapshotReadError):
            svc.read_tables()
        assert svc.batched_ok is True

    def test_a_real_error_still_falls_back_to_the_slow_path(self, lab):
        """An older LabCore that cannot do UNION ALL, or a table that is
        genuinely missing: cost more ops rather than show nothing."""
        gw = Readable(lab)
        gw.answer = {"error": "OperationalError: no such table: lem_holidays"}
        gw.fail = lambda sql: "UNION ALL" in sql
        svc = SnapshotService(gw)
        tables = svc.read_tables()
        assert svc.batched_ok is False
        assert [r["c1"] for r in tables["status"]] == [UID]

    def test_a_healthy_read_is_unchanged(self, lab):
        tables = SnapshotService(lab).read_tables()
        assert [r["c1"] for r in tables["status"]] == [UID]
        assert tables["maint"]


class TestTheErrorsDescribeTheRowsBeingServed:
    """`table_error()` answers "why does this arm have no rows?" — about the
    rows the caller is holding, which are the ones from the last read that was
    KEPT."""

    def test_a_discarded_read_does_not_leave_its_errors_behind(self, lab):
        gw = Readable(lab)
        svc = SnapshotService(gw, builder=lambda t: {"machines": []})
        svc.refresh()
        assert svc.table_error("maint") == ""

        gw.answer = BLIP                      # the whole read fails
        svc.refresh()
        assert svc.tables()["maint"], "the good rows were kept, as designed"
        assert svc.table_error("maint") == "", (
            "the retained rows were reported as unreadable because the errors "
            "came from a read whose data was thrown away")

    def test_an_arm_that_really_failed_is_still_reported(self, lab):
        """The half that must not be lost: a maintenance read that timed out
        is not "nothing is scheduled anywhere"."""
        gw = Readable(lab)
        gw.fail = lambda sql: "lem_maintenance" in sql
        gw.answer = BLIP
        svc = SnapshotService(gw, builder=lambda t: {"machines": []})
        svc.batched_ok = False                # the per-table path
        svc.refresh()
        assert svc.table_error("maint")
        assert svc.table_error("status") == ""

    def test_the_fleet_pm_page_survives_a_blip_over_good_rows(self, lab):
        """End to end, because this is where it was visible: the floor kept
        drawing and `/api/maintenance` went dark, from the same snapshot."""
        from web_app import create_app

        gw = Readable(lab)
        app = create_app(gw, secret="s")
        app.config["TESTING"] = True
        client = app.test_client()
        snapshots = app.config["SNAPSHOTS"]
        snapshots.refresh()
        assert client.get("/api/maintenance").status_code == 200

        gw.answer = BLIP
        snapshots.refresh()
        res = client.get("/api/maintenance")
        assert res.status_code == 200, (
            "the PM page refused to render rows the floor was still drawing")
        assert [t["name"] for t in res.get_json()["tasks"]] == [
            "Annual calibration"]
