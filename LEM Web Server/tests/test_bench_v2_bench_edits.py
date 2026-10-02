"""What a person changes AT THE BENCH reaches LEM's store through the sync.

Transfer spec §2 (data ownership): configuration, the override and the
correction factors are LEM's, "written by people (server); module setup
dialog (`config` record)". A v4 bench on v2 writes nothing of its own into
LabCore any more, so before this file three bench-side edits had nowhere to
land: the setup dialog's save (it used to upsert `lem_machine_config` in
LabCore), the corrections dialog (`lem_correction_factors`) and the override
menu (`lem_machine_control`). Their records reached the machine log as text
and nothing else — the floor kept showing the old configuration, and worse,
the bench's next configuration fetch would hand it back the OLD factor, which
the 60 s rule (§6.6) would then confirm as current.

So the sync applies them to current state, in the same transaction as the
record, and only when the bench's change is not older than what the store
already holds (a record delayed by an outage must not undo a newer edit made
on the floor meanwhile).
"""
import json

import pytest

import bench_v2_kit as kit
from bench_v2_kit import UID, Bench


@pytest.fixture
def store(tmp_path):
    from lem_store import LocalStoreGateway
    s = LocalStoreGateway(str(tmp_path / "lem.db"))
    kit.seed_machine(s)
    return s


@pytest.fixture
def app(store):
    a = kit.make_app(store)
    a.config["SNAPSHOTS"].refresh()
    return a


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def token(client):
    return kit.enroll(client)


def _record(make_body):
    def make(seq, epoch, uid=UID):
        body = {"seq": seq, "epoch": epoch, "uid": uid, "module": "4.0.0",
                "ts": "2026-10-01T09:00:%02d" % (seq % 60)}
        body.update(make_body)
        return body
    return make


def _rows(store, sql, args=()):
    res = store.read_sql(sql, list(args))
    assert "error" not in res, res
    return res["rows"]


class TestTheSetupDialogsSave:
    def test_a_machine_record_becomes_the_stored_configuration(
            self, client, token, store):
        machine = {"uid": UID, "title": "PAC Flash 1 (bench)",
                   "source_type": "single_csv", "csv_path": "C:/x.csv"}
        b = Bench(client, token)
        b.journal(1, make=_record({"kind": "config", "machine": machine,
                                    "detail": {"action": "machine "
                                               "configuration saved"}}))
        assert b.sync().status_code == 200
        row = _rows(store, "SELECT title, config FROM lem_machine_config "
                           "WHERE machine_uid = ?", [UID])[0]
        assert row["title"] == "PAC Flash 1 (bench)"
        assert json.loads(row["config"])["csv_path"] == "C:/x.csv"

    def test_another_benchs_machine_is_never_stored_under_this_one(
            self, client, token, store):
        b = Bench(client, token)
        b.journal(1, make=_record({"kind": "config",
                                    "machine": {"uid": "someone-else",
                                                "title": "X"}}))
        assert b.sync().status_code == 200
        row = _rows(store, "SELECT title FROM lem_machine_config WHERE "
                           "machine_uid = ?", [UID])[0]
        assert row["title"] == "PAC Flash 1"
        assert not _rows(store, "SELECT 1 FROM lem_machine_config WHERE "
                                "machine_uid = 'someone-else'")


class TestTheCorrectionsDialog:
    def test_a_factor_set_at_the_bench_lands_and_moves_the_rev(
            self, client, token, store, app):
        rev = client.get("/api/v2/bench/%s/config" % UID, headers={
            "X-LEM-Bench-Token": token}).get_json()["config_rev"]
        b = Bench(client, token)
        b.journal(1, make=_record({"kind": "config",
                                    "corrections": {"Flash": 0.5},
                                    "detail": {"by": "kaden"}}))
        assert b.sync().status_code == 200
        row = _rows(store, "SELECT correction, updated_by FROM "
                           "lem_correction_factors WHERE machine_uid = ? AND "
                           "test_name = 'Flash'", [UID])[0]
        assert row["correction"] == 0.5 and row["updated_by"] == "kaden"
        rev2 = client.get("/api/v2/bench/%s/config" % UID, headers={
            "X-LEM-Bench-Token": token}).get_json()["config_rev"]
        assert rev2 != rev

    def test_a_cleared_factor_is_deleted(self, client, token, store):
        b = Bench(client, token)
        b.journal(1, make=_record({"kind": "config",
                                    "corrections": {"Flash": 0.5}}))
        b.journal(1, make=_record({"kind": "config",
                                    "corrections": {"Flash": 0}}))
        assert b.sync().status_code == 200
        assert not _rows(store, "SELECT 1 FROM lem_correction_factors WHERE "
                                "machine_uid = ?", [UID])

    def test_a_factor_that_is_not_a_number_is_not_applied(
            self, client, token, store):
        b = Bench(client, token)
        b.journal(1, make=_record({"kind": "config",
                                    "corrections": {"Flash": "Infinity"}}))
        assert b.sync().status_code == 200
        assert not _rows(store, "SELECT 1 FROM lem_correction_factors WHERE "
                                "machine_uid = ?", [UID])

    def test_a_late_record_never_undoes_a_newer_floor_edit(
            self, client, token, store):
        """The bench saved 0.5 at 09:00 and was offline; a supervisor set
        0.7 on the floor at 10:00. The bench's record arriving at 11:00 is
        stored in the log — and does not put 0.5 back."""
        store.sql("INSERT INTO lem_correction_factors (machine_uid, test_name, "
                  "correction, updated_at, updated_by) VALUES (?, 'Flash', 0.7, "
                  "'2026-10-01T10:00:00', 'ryan')", [UID])
        b = Bench(client, token)
        b.journal(1, make=_record({"kind": "config",
                                    "corrections": {"Flash": 0.5}}))
        assert b.sync().status_code == 200
        row = _rows(store, "SELECT correction FROM lem_correction_factors "
                           "WHERE machine_uid = ?", [UID])[0]
        assert row["correction"] == 0.7


class TestTheOverrideMenu:
    def test_an_override_set_at_the_bench_is_the_floors_override(
            self, client, token, store):
        b = Bench(client, token)
        b.journal(1, make=_record({"kind": "override",
                                    "detail": {"status": "SERVICE",
                                               "comment": "lamp out"}}))
        assert b.sync().status_code == 200
        row = _rows(store, "SELECT manual_override, comment FROM "
                           "lem_machine_control WHERE machine_uid = ?", [UID])[0]
        assert (row["manual_override"], row["comment"]) == ("SERVICE", "lamp out")

    def test_cleared_clears_it(self, client, token, store):
        b = Bench(client, token)
        b.journal(1, make=_record({"kind": "override",
                                    "detail": {"status": "SERVICE"}}))
        b.journal(1, make=_record({"kind": "override",
                                    "detail": {"status": "cleared"}}))
        assert b.sync().status_code == 200
        row = _rows(store, "SELECT manual_override FROM lem_machine_control "
                           "WHERE machine_uid = ?", [UID])[0]
        assert row["manual_override"] == ""

    def test_an_unknown_status_is_logged_and_not_applied(
            self, client, token, store):
        b = Bench(client, token)
        b.journal(1, make=_record({"kind": "override",
                                    "detail": {"status": "BANANA"}}))
        assert b.sync().status_code == 200
        assert not _rows(store, "SELECT 1 FROM lem_machine_control WHERE "
                                "machine_uid = ?", [UID])
        assert _rows(store, "SELECT 1 FROM lem_machine_log WHERE machine_uid "
                            "= ? AND kind = 'override'", [UID])
