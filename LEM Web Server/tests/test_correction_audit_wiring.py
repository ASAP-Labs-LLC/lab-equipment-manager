"""Closing the 17025 gap: the thing that destroys the old value records it.

`CorrectionAuditStore` exists for one reason. `lem_correction_factors` is an
UPSERT keyed on (machine_uid, test_name), so saving a new offset overwrites the
old one and there is nowhere left that says what it used to be, when it changed,
or who changed it — while ISO/IEC 17025 §7.8.2 makes that number part of every
result the bench reports and §7.5.1 requires the measurement to be
reconstructible.

The store shipped, tested, wired to nothing. The gap does not close until the
routes that destroy the value call it, so that is what is asserted here — over
the routes, not over the store.

The `lem_machine_log` line `_audit` already writes is not the same record and
does not replace it: it is a config-audit line in a table the log page filters
and the machine-delete path can PURGE. `lem_correction_audit` is append-only,
typed (previous/new as numbers, not text in a JSON blob) and queryable per test.
Both are written; only one of them is the compliance trail.

And a refused audit row is not allowed to vanish. LabCore's write queue refuses
past ~100 pending by answering, on an ordinary busy afternoon, and the operator's
change has already happened by then — so the row is spooled, retried, and
reported on `/healthz` until it lands.
"""

import pytest

import refusal_shapes
from labcore_gateway import FakeLabCoreGateway
from equipment_history import CorrectionAuditStore
from snapshot_service import SnapshotService
from web_app import create_app

pytestmark = pytest.mark.usefixtures("both_refusal_shapes")


def _seed_machine(gw, uid="m1", title="GC-1"):
    gw.sql("INSERT INTO lem_machine_status (machine_uid, title, status, "
           "reason, updated_at) VALUES (?, ?, 'GREEN', '', "
           "'2026-08-01T09:00:00')", [uid, title])


class RefusingAudit(FakeLabCoreGateway):
    """LabCore takes the correction and refuses the audit row.

    The exact shape of a busy queue: it serialises, so one statement lands and
    the next is turned away. Nothing here raises — that is the whole point.
    """

    def __init__(self) -> None:
        super().__init__()
        self.refuse = False

    def sql(self, sql, args=None, **kw):
        if self.refuse and "lem_correction_audit" in sql:
            return refusal_shapes.current()
        return super().sql(sql, args, **kw)


@pytest.fixture
def gw():
    gateway = FakeLabCoreGateway()
    SnapshotService(gateway).ensure_schema()
    _seed_machine(gateway)
    return gateway


@pytest.fixture
def client(gw):
    app = create_app(gw, secret="t")
    app.config.update(TESTING=True)
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user"] = "ryan"
    return c


class TestSavingACorrectionRecordsTheChange:
    def test_the_first_factor_is_recorded_as_a_change_from_zero(self, client,
                                                                gw):
        resp = client.post("/api/machines/m1/corrections",
                           json={"test_name": "Sulfur", "correction": -3.0,
                                 "units": "%"})
        assert resp.status_code == 200
        trail = CorrectionAuditStore(gw).history("m1")
        assert len(trail) == 1
        assert trail[0]["previous"] == 0.0
        assert trail[0]["new_value"] == -3.0
        assert trail[0]["test_name"] == "Sulfur"
        assert trail[0]["units"] == "%"

    def test_the_person_who_changed_it_is_on_the_row(self, client, gw):
        client.post("/api/machines/m1/corrections",
                    json={"test_name": "Sulfur", "correction": -3.0})
        assert CorrectionAuditStore(gw).history("m1")[0]["changed_by"] == "ryan"

    def test_the_value_it_replaced_survives_the_overwrite(self, client, gw):
        """The whole gap. The UPSERT destroys the old number; this is the only
        place left that says what it was."""
        client.post("/api/machines/m1/corrections",
                    json={"test_name": "Sulfur", "correction": -3.0})
        client.post("/api/machines/m1/corrections",
                    json={"test_name": "Sulfur", "correction": -1.5})
        trail = CorrectionAuditStore(gw).history("m1", "Sulfur")
        # Compared as a SET. `_stamp` writes seconds, and `history()` orders on
        # (changed_at, uid) — so two changes made inside one second are ordered
        # by a random hex uid. That is the store's, not this route's, and it is
        # invisible at human speed; asserting a sequence here would be a test
        # that passes on a slow machine and fails on a fast one. What matters
        # is that BOTH rows exist and the overwritten value is in one of them.
        assert sorted((r["previous"], r["new_value"]) for r in trail) == \
            sorted([(0.0, -3.0), (-3.0, -1.5)])

    def test_removing_a_factor_is_a_change_to_zero_not_a_hole(self, client, gw):
        """The readings after it really are corrected by nothing, and a gap in
        the trail cannot say when that started."""
        client.post("/api/machines/m1/corrections",
                    json={"test_name": "Sulfur", "correction": -3.0})
        assert client.delete(
            "/api/machines/m1/corrections/Sulfur").status_code == 200
        trail = CorrectionAuditStore(gw).history("m1", "Sulfur")
        assert sorted((r["previous"], r["new_value"]) for r in trail) == \
            sorted([(0.0, -3.0), (-3.0, 0.0)])
        assert {r["changed_by"] for r in trail} == {"ryan"}

    def test_a_reason_travels_with_the_change_when_one_is_given(self, client,
                                                               gw):
        client.post("/api/machines/m1/corrections",
                    json={"test_name": "Sulfur", "correction": -3.0,
                          "reason": "Annual calibration against SRM 2724b"})
        assert "SRM 2724b" in CorrectionAuditStore(gw).history("m1")[0]["reason"]

    def test_the_config_log_line_is_still_written_too(self, client, gw):
        """Two records, on purpose: the log line is what the logs page and the
        machine's own history show; the audit table is the typed, append-only
        trail an auditor queries. Neither replaces the other."""
        client.post("/api/machines/m1/corrections",
                    json={"test_name": "Sulfur", "correction": -3.0})
        rows = gw.read_sql("SELECT test_name FROM lem_machine_log WHERE "
                           "machine_uid = 'm1' AND kind = 'config'")["rows"]
        assert [r["test_name"] for r in rows] == ["correction factor set"]

    def test_a_refused_correction_records_no_audit_row(self, gw):
        """The order matters: nothing may claim a change that did not happen."""
        class RefuseTheFactor(FakeLabCoreGateway):
            def sql(self, sql, args=None, **kw):
                if "lem_correction_factors" in sql and "INSERT" in sql:
                    return refusal_shapes.current()
                return super().sql(sql, args, **kw)

        gateway = RefuseTheFactor()
        SnapshotService(gateway).ensure_schema()
        _seed_machine(gateway)
        app = create_app(gateway, secret="t")
        app.config.update(TESTING=True)
        c = app.test_client()
        with c.session_transaction() as sess:
            sess["user"] = "ryan"
        resp = c.post("/api/machines/m1/corrections",
                      json={"test_name": "Sulfur", "correction": -3.0})
        assert resp.status_code in (502, 503)
        assert CorrectionAuditStore(gateway).history("m1") == []


class TestARefusedAuditRowIsNotLost:
    """A busy queue is an ordinary Tuesday, and the operator's change has
    already landed by the time the audit row is refused. Failing the change
    would be a lie in one direction; dropping the record is a lie in the
    other."""

    def _app(self):
        gateway = RefusingAudit()
        SnapshotService(gateway).ensure_schema()
        _seed_machine(gateway)
        app = create_app(gateway, secret="t")
        app.config.update(TESTING=True)
        c = app.test_client()
        with c.session_transaction() as sess:
            sess["user"] = "ryan"
        return app, c, gateway

    def test_the_change_still_succeeds_and_says_the_record_was_lost(self):
        app, client, gw = self._app()
        gw.refuse = True
        resp = client.post("/api/machines/m1/corrections",
                           json={"test_name": "Sulfur", "correction": -3.0})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body.get("audit") is False
        assert body.get("warning")
        # …and the factor really is in force.
        assert gw.read_sql("SELECT correction FROM lem_correction_factors "
                           "WHERE machine_uid='m1'")["rows"][0][
                               "correction"] == -3.0

    def test_healthz_reports_the_spool(self):
        app, client, gw = self._app()
        gw.refuse = True
        client.post("/api/machines/m1/corrections",
                    json={"test_name": "Sulfur", "correction": -3.0})
        health = client.get("/healthz").get_json()
        assert health["audit_spool"] == 1
        assert health["audit_spool_oldest"]

    def test_the_spool_drains_when_labcore_comes_back(self):
        app, client, gw = self._app()
        gw.refuse = True
        client.post("/api/machines/m1/corrections",
                    json={"test_name": "Sulfur", "correction": -3.0})
        gw.refuse = False
        # The background thread drains it; a second change drains it too, which
        # is what makes this testable without one.
        client.post("/api/machines/m1/corrections",
                    json={"test_name": "Flash", "correction": 1.0})
        trail = CorrectionAuditStore(gw).history("m1")
        assert sorted(r["test_name"] for r in trail) == ["Flash", "Sulfur"]
        assert client.get("/healthz").get_json()["audit_spool"] == 0

    def test_the_spooled_row_keeps_its_own_timestamp_and_author(self):
        app, client, gw = self._app()
        gw.refuse = True
        client.post("/api/machines/m1/corrections",
                    json={"test_name": "Sulfur", "correction": -3.0})
        spooled_at = app.config["AUDIT_SPOOL"].pending()[0]["when"]
        gw.refuse = False
        assert app.config["AUDIT_SPOOL"].drain() == 1
        row = CorrectionAuditStore(gw).history("m1")[0]
        assert row["changed_at"] == spooled_at
        assert row["changed_by"] == "ryan"

    def test_draining_twice_does_not_write_the_row_twice(self):
        app, client, gw = self._app()
        gw.refuse = True
        client.post("/api/machines/m1/corrections",
                    json={"test_name": "Sulfur", "correction": -3.0})
        gw.refuse = False
        app.config["AUDIT_SPOOL"].drain()
        app.config["AUDIT_SPOOL"].drain()
        assert len(CorrectionAuditStore(gw).history("m1")) == 1

    def test_the_spool_is_bounded(self):
        app, client, gw = self._app()
        gw.refuse = True
        spool = app.config["AUDIT_SPOOL"]
        for i in range(spool.MAX + 20):
            spool.add({"machine_uid": "m1", "test_name": f"T{i}",
                       "previous": 0.0, "new_value": 1.0, "units": "",
                       "by": "ryan", "reason": "",
                       "when": "2026-08-01T09:00:00", "uid": f"u{i}"})
        assert len(spool.pending()) == spool.MAX
        # The OLDEST are dropped, because the newest are the ones somebody is
        # still standing at the bench waiting to see land.
        assert spool.pending()[-1]["test_name"] == f"T{spool.MAX + 19}"

    def test_a_write_whose_ANSWER_was_lost_is_not_recorded_twice(self):
        """The case the kept uid exists for, and the only one that can produce
        a duplicate compliance row.

        LabCore takes the INSERT and then answers a refusal — a dropped
        response, a proxy that gave up, a queue that acknowledged late. The
        rule says refuse on a positive failure signal, so the row is spooled
        and retried; the row is already in the table. Minting a fresh uid on
        the retry writes the same factor change a second time, and an audit
        trail that says a correction was changed twice when it was changed once
        is a worse record than one that is short.

        The uid is minted ONCE and kept, so the retry collides on the primary
        key and `_already_recorded` reads that as done.
        """
        class WroteItThenSaidNo(FakeLabCoreGateway):
            def __init__(self):
                super().__init__()
                self.swallow = False

            def sql(self, sql, args=None, **kw):
                answer = super().sql(sql, args, **kw)
                if self.swallow and "lem_correction_audit" in sql:
                    # The row LANDED. Only the acknowledgement was lost.
                    return refusal_shapes.current()
                return answer

        gw = WroteItThenSaidNo()
        SnapshotService(gw).ensure_schema()
        _seed_machine(gw)
        app = create_app(gw, secret="t")
        app.config.update(TESTING=True)
        c = app.test_client()
        with c.session_transaction() as sess:
            sess["user"] = "ryan"
        gw.swallow = True
        c.post("/api/machines/m1/corrections",
               json={"test_name": "Sulfur", "correction": -3.0})
        assert len(app.config["AUDIT_SPOOL"].pending()) == 1
        gw.swallow = False
        app.config["AUDIT_SPOOL"].drain()
        trail = CorrectionAuditStore(gw).history("m1", "Sulfur")
        assert len(trail) == 1, trail
        assert app.config["AUDIT_SPOOL"].pending() == []

    def test_the_background_cycle_drains_it(self):
        """The snapshot poller is the thread that is already awake and already
        talking to LabCore; a spool that only drains on the next correction
        would sit there until somebody happened to change another factor."""
        app, client, gw = self._app()
        snapshots = app.config["SNAPSHOTS"]
        gw.refuse = True
        client.post("/api/machines/m1/corrections",
                    json={"test_name": "Sulfur", "correction": -3.0})
        gw.refuse = False
        assert snapshots.on_cycle is not None
        snapshots.on_cycle()
        assert len(CorrectionAuditStore(gw).history("m1")) == 1
        assert client.get("/healthz").get_json()["audit_spool"] == 0
