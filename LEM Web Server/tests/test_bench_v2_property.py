"""S2 and T3: exactly once, under every way a network can misbehave.

S2 (the critic's bar for this piece): **10,000 records**, sent in random chunks,
with resends from old cursors, lost answers (the server committed, the bench
never heard), duplicated deliveries and requests overtaking each other, give a
stored set EQUAL to the sent set with 0 duplicates — in the custody table and
in the 17025 log both, and with the server's running digest equal to the
bench's.

The tally is the §9.4 one, with no term zeroed: lost = Σ max(0, sent − stored),
dup = Σ max(0, stored − sent), over multisets of `(epoch, seq)`.

T3: the store is restored from a backup ten polls old. The bench, which knows
more than the server now does, sends from its own acked+1; the server answers
409 `cursor` with what it really holds, and the bench resends from there.
Afterwards the store equals the pre-restore set: 0 lost, 0 dup — RPO 0 for
bench records, because the bench never let go of them.

The property is checked over several seeds so a lucky ordering cannot pass it,
and each seed's adversary is recorded so a failure can be replayed exactly.
"""
import random
import sqlite3
import threading

import pytest

import bench_v2_kit as kit
from bench_v2_kit import Bench, UID
from labcore_counter import CountingLabCore
from lem_store import LocalStoreGateway

N_RECORDS = 10_000


def _store(tmp_path, name="lem.db"):
    s = LocalStoreGateway(str(tmp_path / name))
    kit.seed_machine(s)
    return s


class Adversary:
    """A bench uploader on a hostile network. Every request it makes is one of:

    * a normal sync from acked+1 (chunk 1..100);
    * the same, but the answer is lost after the server committed;
    * the same request delivered twice (a retry the network duplicated);
    * a resend from an OLD cursor (anywhere in the last 300 seqs);
    * two requests in flight, delivered in the wrong order — the later chunk
      first, which the server must refuse (409) without storing past a gap.
    """

    def __init__(self, bench, rng):
        self.b = bench
        self.rng = rng
        self.counts = {"normal": 0, "lost": 0, "dup_delivery": 0,
                       "old_resend": 0, "reorder": 0, "409": 0}

    def _ok(self, r):
        assert r.status_code in (200, 409), (r.status_code, r.get_json())
        if r.status_code == 409:
            self.counts["409"] += 1
        return r

    def step(self):
        b, rng = self.b, self.rng
        limit = rng.randint(1, 100)
        roll = rng.random()
        if roll < 0.15:
            self.counts["lost"] += 1
            b.sync(limit=limit, lose_response=True)
        elif roll < 0.25:
            self.counts["dup_delivery"] += 1
            doc = b.body(limit=limit)
            self._ok(b.post_doc(doc))
            r = self._ok(b.post_doc(doc))
            b.acked = int(r.get_json()["acked"])
        elif roll < 0.35 and b.acked > 0:
            self.counts["old_resend"] += 1
            start = rng.randint(max(1, b.acked - 300), b.acked)
            r = self._ok(b.sync(from_seq=start, limit=limit))
        elif roll < 0.50:
            self.counts["reorder"] += 1
            first = b.body(limit=limit)
            second_from = b.acked + 1 + len(first["records"])
            second = b.body(from_seq=second_from, limit=rng.randint(1, 100)) \
                if second_from <= len(b.records) else None
            if second is not None:
                late = self._ok(b.post_doc(second))     # overtakes
                # The server took it only if it already held everything
                # before it (a lost answer can have put it ahead of the
                # bench); otherwise it refused it whole, storing nothing
                # past the gap.
                held = int(late.get_json()["acked"])
                if late.status_code == 409:
                    assert held + 1 < second_from
                else:
                    assert held >= second_from - 1
            r = self._ok(b.post_doc(first))
            b.acked = int(r.get_json()["acked"])
        else:
            self.counts["normal"] += 1
            self._ok(b.sync(limit=limit))

    def run(self, max_steps=20000):
        for _ in range(max_steps):
            if self.b.acked >= len(self.b.records):
                return
            self.step()
        raise AssertionError("did not converge: %r" % self.counts)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_S2_ten_thousand_records_on_a_hostile_network_land_exactly_once(
        tmp_path, seed):
    store = _store(tmp_path)
    lab = CountingLabCore()
    client = kit.make_app(store, labcore=lab).test_client()
    bench = Bench(client, kit.enroll(client))
    rng = random.Random(seed)
    adv = Adversary(bench, rng)
    # Journaled in bursts while the uploader runs, as a real bench does.
    while len(bench.records) < N_RECORDS:
        bench.journal(min(rng.randint(1, 400), N_RECORDS - len(bench.records)))
        for _ in range(rng.randint(1, 6)):
            if bench.acked < len(bench.records):
                adv.step()
    adv.run()

    sent = [("ep-1", s) for s in range(1, N_RECORDS + 1)]
    lost, dup = kit.dup_and_lost(sent, kit.stored_seqs(store))
    assert (lost, dup) == (0, 0), adv.counts
    lost, dup = kit.dup_and_lost(sent, kit.log_custody_seqs(store))
    assert (lost, dup) == (0, 0), adv.counts
    n_log = store.read_sql(
        # raw-log: the tally counts every row, hidden or not
        "SELECT COUNT(*) AS n FROM lem_machine_log WHERE machine_uid = ?",
        [UID])["rows"][0]["n"]
    assert n_log == N_RECORDS
    cur = store.read_sql("SELECT acked_seq, digest FROM bench_cursor")["rows"]
    assert cur == [{"acked_seq": N_RECORDS,
                    "digest": bench.digest_through(N_RECORDS)}]
    # The adversary really did all of it, more than a token amount.
    for what in ("lost", "dup_delivery", "old_resend", "reorder", "409"):
        assert adv.counts[what] >= 10, adv.counts
    assert lab.ops == 0


def test_S2_concurrent_uploaders_on_one_epoch_never_double_a_record(tmp_path):
    """Four threads post overlapping ranges of one epoch at once — two
    uploaders after a restart race, or a proxy retries while the first is
    still in flight. One `BEGIN IMMEDIATE` per request is what serialises
    them: each sees the cursor the previous one committed."""
    store = _store(tmp_path)
    app = kit.make_app(store)
    token = kit.enroll(app.test_client())
    model = Bench(app.test_client(), token)
    model.journal(2000)
    errors = []

    def worker(seed):
        rng = random.Random(seed)
        client = app.test_client()
        for _ in range(150):
            start = rng.randint(1, 2000)
            doc = model.body(from_seq=start, limit=rng.randint(1, 100))
            r = client.post("/api/v2/bench/%s/sync" % UID, json=doc,
                            headers={"X-LEM-Bench-Token": token})
            if r.status_code not in (200, 409):
                errors.append((r.status_code, r.get_json()))

    threads = [threading.Thread(target=worker, args=(s,)) for s in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    model.drain()
    sent = [("ep-1", s) for s in range(1, 2001)]
    assert kit.dup_and_lost(sent, kit.stored_seqs(store)) == (0, 0)
    assert kit.dup_and_lost(sent, kit.log_custody_seqs(store)) == (0, 0)


def _backup(store, dest):
    """SQLite's online backup — the same API T-P11's hourly backup uses."""
    src = sqlite3.connect(store.path)
    out = sqlite3.connect(str(dest))
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()


def test_T3_a_store_restored_ten_polls_back_answers_409_and_loses_nothing(
        tmp_path):
    path = tmp_path / "lem.db"
    store = _store(tmp_path)
    client = kit.make_app(store).test_client()
    token = kit.enroll(client)
    bench = Bench(client, token)
    for _ in range(20):                        # 20 polls, 7 prints each
        bench.journal(7).sync()
    _backup(store, tmp_path / "backup.db")
    held_at_backup = bench.acked
    for _ in range(10):                        # 10 more polls after it
        bench.journal(7).sync()
    before = sorted(kit.stored_seqs(store))
    assert len(before) == 210 and bench.acked == 210
    store.close()

    # The restore: yesterday's file over today's.
    for suffix in ("", "-wal", "-shm"):
        try:
            (tmp_path / ("lem.db" + suffix)).unlink()
        except FileNotFoundError:
            pass
    (tmp_path / "backup.db").rename(path)
    restored = LocalStoreGateway(str(path))
    assert len(kit.stored_seqs(restored)) == held_at_backup == 140

    bench.client = kit.make_app(restored).test_client()
    bench.journal(7)                           # the next poll
    r = bench.sync()
    assert r.status_code == 409
    assert r.get_json() == {"error": "cursor", "acked": 140, "epoch": "ep-1",
                            "message": r.get_json()["message"]}
    assert bench.acked == 140                  # adopted
    bench.drain()
    sent = [("ep-1", s) for s in range(1, 218)]
    assert kit.dup_and_lost(sent, kit.stored_seqs(restored)) == (0, 0)
    assert kit.dup_and_lost(sent, kit.log_custody_seqs(restored)) == (0, 0)
    assert sorted(kit.stored_seqs(restored))[:210] == before
    cur = restored.read_sql("SELECT digest FROM bench_cursor")["rows"][0]
    assert cur["digest"] == bench.digest_through(217)
