"""Bench enrolment: one token per bench, kept only as a hash (spec §6.4).

v3.9 benches share one token, published in `lem_meta` — anything that can
read LabCore can speak as any bench. v2 gives each bench its own:

* `POST /api/v2/bench/<uid>/enroll` issues it. The server keeps only its
  SHA-256, so a copy of the store (a backup, the off-host copy) cannot be
  used to impersonate a bench.
* The shared live token is accepted as proof ONCE, for an EXISTING uid's
  first enrolment — the migration path for 17 benches that already hold it.
* Re-enrolling an enrolled uid, or enrolling a uid LEM has never seen, needs a
  person to approve it ("Eraspec asks to re-enrol"). Until then the answer is
  202 pending — not 404, which a bench reads as "this server has no v2".
* There is no clock window. A bench whose clock is off still enrols; v2 A's
  ±10 minute rule would have locked out a bench for a dead CMOS battery.
"""
import hashlib

import pytest

import bench_v2_kit as kit
from bench_v2_kit import Bench, UID
from labcore_gateway import FakeLabCoreGateway


@pytest.fixture
def store():
    s = FakeLabCoreGateway()
    kit.seed_machine(s)
    return s


@pytest.fixture
def app(store):
    return kit.make_app(store)


@pytest.fixture
def client(app):
    return app.test_client()


def _enroll(client, uid=UID, token=kit.SHARED_TOKEN, **body):
    payload = {"machine_uid": uid, "module_version": "4.0.0"}
    payload.update(body)
    return client.post("/api/v2/bench/%s/enroll" % uid, json=payload,
                       headers={"X-LEM-Token": token})


def _sign_in(client):
    r = client.post("/api/login", json={"username": "ryan", "password": "good"})
    assert r.status_code == 200


def _token_row(store, uid=UID):
    rows = store.read_sql("SELECT * FROM bench_token WHERE machine_uid = ?",
                          [uid])["rows"]
    return rows[0] if rows else None


class TestFirstEnrolment:
    def test_an_existing_uid_gets_a_token_and_the_store_keeps_only_its_hash(
            self, client, store):
        r = _enroll(client)
        assert r.status_code == 200
        token = r.get_json()["token"]
        assert len(token) >= 32
        row = _token_row(store)
        assert row["token_sha256"] == hashlib.sha256(token.encode()).hexdigest()
        dump = repr(store.read_sql("SELECT * FROM bench_token")["rows"])
        assert token not in dump
        b = Bench(client, token)
        b.journal(1)
        assert b.sync().status_code == 200

    def test_two_benches_get_different_tokens(self, client, store):
        kit.seed_machine(store, uid="other-1", title="Other")
        assert _enroll(client).get_json()["token"] != \
            _enroll(client, uid="other-1").get_json()["token"]

    def test_a_bench_known_only_by_its_reported_status_is_existing(
            self, client, store):
        """A v3.9 bench that has reported a status is a bench LEM holds,
        configuration row or not (the demo floor has exactly these)."""
        store.sql("INSERT INTO lem_machine_status (machine_uid, title, status, "
                  "reason, updated_at) VALUES ('status-only-1', 'Status Only', "
                  "'GREEN', '', '2026-10-01T08:00:00')")
        assert _enroll(client, uid="status-only-1").status_code == 200

    def test_a_retired_machine_does_not_enrol_on_the_shared_token(
            self, client, store):
        store.sql("UPDATE lem_machine_config SET retired_at = "
                  "'2026-10-01T08:30:00' WHERE machine_uid = ?", [UID])
        assert _enroll(client).status_code == 202

    def test_the_wrong_shared_token_is_401(self, client):
        assert _enroll(client, token="guess").status_code == 401

    def test_a_skewed_clock_is_no_reason_to_refuse(self, client):
        r = _enroll(client, bench_clock="1999-01-01T00:00:00+00:00")
        assert r.status_code == 200


class TestApproval:
    def test_enrolling_twice_needs_a_person_and_keeps_the_old_token(
            self, client, store):
        """A second enrolment is either a bench that lost its key or someone
        who has the shared token. Neither may silently replace a working
        bench's identity."""
        first = _enroll(client).get_json()["token"]
        r = _enroll(client)
        assert r.status_code == 202
        assert r.get_json()["state"] == "pending"
        assert "token" not in r.get_json()
        assert _token_row(store)["pending_reenrol_at"]
        b = Bench(client, first)                       # still works
        b.journal(1)
        assert b.sync().status_code == 200

    def test_an_approved_re_enrolment_issues_a_new_token_and_retires_the_old(
            self, client, store):
        first = _enroll(client).get_json()["token"]
        _enroll(client)                                # asks
        assert client.post("/api/transfer/benches/%s/approve" % UID
                           ).status_code == 401        # signed out
        _sign_in(client)
        r = client.post("/api/transfer/benches/%s/approve" % UID)
        assert r.status_code == 200, r.get_json()
        second = _enroll(client)
        assert second.status_code == 200
        new = second.get_json()["token"]
        assert new != first
        old_bench = Bench(client, first)
        old_bench.journal(1)
        assert old_bench.post_doc(old_bench.body()).status_code == 401
        new_bench = Bench(client, new)
        new_bench.journal(1)
        assert new_bench.sync().status_code == 200
        row = _token_row(store)
        assert row["pending_reenrol_at"] is None
        assert "ryan" in row["issued_by"]
        # and the approval is spent: a third enrolment asks again
        assert _enroll(client).status_code == 202

    def test_a_uid_lem_has_never_seen_waits_for_approval(self, client, store):
        r = _enroll(client, uid="brand-new-1")
        assert r.status_code == 202 and r.get_json()["state"] == "pending"
        _sign_in(client)
        assert client.post("/api/transfer/benches/brand-new-1/approve"
                           ).status_code == 200
        assert _enroll(client, uid="brand-new-1").status_code == 200

    def test_approving_a_bench_that_asked_nothing_is_refused(self, client):
        _sign_in(client)
        r = client.post("/api/transfer/benches/%s/approve" % UID)
        assert r.status_code == 409

    def test_the_pending_list_names_who_is_asking(self, client):
        _enroll(client)
        _enroll(client)
        _enroll(client, uid="brand-new-1")
        _sign_in(client)
        body = client.get("/api/transfer/benches").get_json()
        pending = {b["machine_uid"]: b for b in body["benches"]
                   if b["pending"]}
        assert set(pending) == {UID, "brand-new-1"}
        assert pending[UID]["title"] == "PAC Flash 1"
        assert pending["brand-new-1"]["known"] is False
