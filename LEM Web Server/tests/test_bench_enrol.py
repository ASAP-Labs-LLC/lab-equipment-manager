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
* Except the same enrolment retried: a bench sends an `enroll_key` it made
  for this enrolment, and if the 200 carrying its token was lost, the retry
  with that key is answered with a token again — until the token carries
  its first sync (TestALostAnswer).
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


class TestALostAnswer:
    """The fault S2 survives for sync must not strand a bench at enrolment.

    Round 1: the first enrolment returned 200 with the token, and if that
    answer was lost (a timeout after the server committed, a dropped tunnel)
    the bench's retry got 202 "already enrolled; needs a person to approve".
    The bench never held the token it was "enrolled" with, so it sat dark
    until someone noticed and approved it — for a fault the sync road
    shrugs off.

    The fix is an `enroll_key`: a random secret the bench makes before its
    first attempt and sends with every retry of THAT enrolment. The server
    keeps only its hash. The same key again, before the token has carried a
    single sync, is the same enrolment retried: it gets a new token (the one
    it never saw stops working — nobody holds it). Once the token has been
    used, the bench evidently has it, the key is spent, and a further
    enrolment needs a person exactly as before. Holding the key is no more
    power than holding the token it was issued with, and it never outlives
    the token's first use.
    """

    KEY = "k" * 8 + "-bench-secret-0123456789"

    def test_a_retry_with_the_same_key_gets_a_token_not_pending(
            self, client, store):
        lost = _enroll(client, enroll_key=self.KEY)
        assert lost.status_code == 200
        never_seen = lost.get_json()["token"]
        retry = _enroll(client, enroll_key=self.KEY)
        assert retry.status_code == 200, retry.get_json()
        token = retry.get_json()["token"]
        assert token != never_seen
        row = _token_row(store)
        assert row["pending_reenrol_at"] is None
        assert self.KEY not in repr(row)               # only its hash
        b = Bench(client, token)
        b.journal(2)
        assert b.sync().status_code == 200
        ghost = Bench(client, never_seen)
        ghost.journal(1)
        assert ghost.post_doc(ghost.body()).status_code == 401

    def test_three_lost_answers_in_a_row_still_end_enrolled(self, client):
        for _ in range(3):
            assert _enroll(client, enroll_key=self.KEY).status_code == 200
        token = _enroll(client, enroll_key=self.KEY).get_json()["token"]
        b = Bench(client, token)
        b.journal(1)
        assert b.sync().status_code == 200

    def test_a_different_key_is_a_different_enrolment_and_waits(
            self, client, store):
        first = _enroll(client, enroll_key=self.KEY).get_json()["token"]
        r = _enroll(client, enroll_key="someone-else-entirely-0000")
        assert r.status_code == 202 and "token" not in r.get_json()
        b = Bench(client, first)                       # untouched
        b.journal(1)
        assert b.sync().status_code == 200

    def test_no_key_is_the_old_rule(self, client):
        assert _enroll(client, enroll_key=self.KEY).status_code == 200
        assert _enroll(client).status_code == 202

    def test_a_short_key_is_no_key(self, client):
        """A guessable key would let anyone with the shared token replace a
        fresh bench's identity; under 16 characters it is ignored."""
        assert _enroll(client, enroll_key="short").status_code == 200
        assert _enroll(client, enroll_key="short").status_code == 202

    def test_once_the_token_has_synced_the_key_is_spent(self, client, store):
        token = _enroll(client, enroll_key=self.KEY).get_json()["token"]
        b = Bench(client, token)
        b.journal(1)
        assert b.sync().status_code == 200
        assert _token_row(store)["enroll_key_sha256"] is None
        r = _enroll(client, enroll_key=self.KEY)
        assert r.status_code == 202
        b.journal(1)
        assert b.sync().status_code == 200             # still the bench's

    def test_an_approved_re_enrolment_survives_a_lost_answer_too(
            self, client):
        _enroll(client, enroll_key=self.KEY)
        _enroll(client)                                # asks again
        _sign_in(client)
        assert client.post("/api/transfer/benches/%s/approve" % UID
                           ).status_code == 200
        key2 = "second-enrolment-key-abcdef"
        assert _enroll(client, enroll_key=key2).status_code == 200   # lost
        r = _enroll(client, enroll_key=key2)
        assert r.status_code == 200, r.get_json()
        b = Bench(client, r.get_json()["token"])
        b.journal(1)
        assert b.sync().status_code == 200


def test_a_store_from_before_enroll_keys_gains_the_column(tmp_path):
    """A store file written by round 1 has `bench_token` without
    `enroll_key_sha256`. Opening it adds the column (NULL for every bench
    already enrolled: those keys were never sent) and keeps every token."""
    import sqlite3
    from lem_store import LocalStoreGateway
    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE bench_token (machine_uid TEXT PRIMARY KEY, "
                "token_sha256 TEXT, issued_at TEXT, issued_by TEXT, "
                "revoked_at TEXT, pending_reenrol_at TEXT)")
    con.execute("INSERT INTO bench_token (machine_uid, token_sha256) "
                "VALUES ('b1', 'abc')")
    con.commit()
    con.close()
    s = LocalStoreGateway(path)
    rows = s.read_sql("SELECT machine_uid, token_sha256, enroll_key_sha256 "
                      "FROM bench_token")["rows"]
    assert rows == [{"machine_uid": "b1", "token_sha256": "abc",
                     "enroll_key_sha256": None}]
