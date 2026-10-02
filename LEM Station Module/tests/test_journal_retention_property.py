"""Journal retention is gated on the server's backups (transfer §3.4, T-P11).

The bench is the second copy of every reading. It may delete a journal
segment only when BOTH hold:

  * every record in the segment is at or below `durable` — the highest seq the
    LEM server has confirmed is inside a COMPLETED, CHECKED backup (not merely
    received: received is one disk), and
  * the segment is more than 30 days old,

so for 30 days after any backup a lost server disk costs nothing: restore,
409, resend (T3). A segment deleted one record too early is a reading that
exists nowhere if the server's disk dies that afternoon.

That is a property over every interleaving of appends, acks, backups, ageing,
re-minted epochs and prunes, so it is checked as one: a couple of hundred random
sequences, each replayable from its seed, with the invariant asserted at EVERY
prune against what the segment held and how old it was at that instant —
read off the disk, not off the journal's own bookkeeping, which is the thing
under test.

The disk-pressure path is checked separately, because it is deliberately
different: under 1 GB free or 1 GB unacknowledged, §3.4 lets the bench prune
DURABLE segments before they are 30 days old rather than stop recording. The
age rule is lifted there by design; the durable rule never is.

And one rule the property found missing: the bench adopts `durable` from the
server's answer, so an answer claiming more durable than acked (a bug, a
restored server, a hostile proxy) must not let the bench delete records the
server does not even hold. `durable` is clamped to the `acked` it came with.
"""
import json
import os
import random
from datetime import datetime, timedelta

import pytest

import lem_station_module as mod

DAY = 86400.0
SEEDS = range(150)


def _segments(j):
    return sorted(n for n in os.listdir(j.dir)
                  if n.startswith("seg-") and n.endswith(".jsonl"))


def _on_disk(j):
    """name -> (records [(epoch, seq)], mtime), from the files themselves."""
    out = {}
    for name in _segments(j):
        path = os.path.join(j.dir, name)
        recs = []
        with open(path, "rb") as f:
            for line in f.read().splitlines(True):
                body = mod.parse_journal_line(line)
                if body is not None:
                    recs.append((body["epoch"], body["seq"]))
        out[name] = (recs, os.path.getmtime(path))
    return out


class World:
    def __init__(self, tmp_path, rng):
        self.dir = str(tmp_path / "j" / "b1")
        self.rng = rng
        self.j = self._open()
        self.now = datetime.now()
        self.pruned = 0
        self.log = []

    def reopen(self, new_epoch=False):
        self.j.close()
        if new_epoch:
            os.remove(os.path.join(self.dir, mod.JOURNAL_META_NAME))
        self.j = self._open()

    def _open(self):
        # fsync is not what is under test here (test_bench_journal pins it);
        # skipping it keeps 250 runs of 120 steps to a few seconds each batch.
        return mod.BenchJournal(self.dir, "b1", segment_bytes=400,
                                fsync=lambda fd: None)

    def step(self):
        j, rng = self.j, self.rng
        op = rng.choices(["append", "ack", "age", "prune", "reopen", "epoch"],
                         weights=[40, 20, 15, 20, 3, 2])[0]
        self.log.append(op)
        if op == "append":
            refs = j.append([{"kind": "run", "pk": "k%d" % rng.random(),
                              "pad": "z" * rng.randint(10, 150)}
                             for _ in range(rng.randint(1, 4))])
            if rng.random() < 0.85:
                j.mark_projected(refs)
            if rng.random() < 0.85:
                j.mark_settled(refs)
        elif op == "ack":
            top = j.next_seq() - 1
            acked = rng.randint(0, top) if top else 0
            # The server's answer: usually durable <= acked, sometimes a
            # broken answer claiming more than it acked.
            durable = rng.randint(0, acked + (5 if rng.random() < 0.2 else 0))
            j.set_acked(acked, durable)
            assert j.durable <= j.acked, "durable passed acked"
        elif op == "age":
            for name in _segments(j):
                t = (self.now - timedelta(days=rng.uniform(0, 60))).timestamp()
                os.utime(os.path.join(self.dir, name), (t, t))
        elif op == "prune":
            self.prune(early=False)
        elif op == "reopen":
            self.reopen()
        elif op == "epoch":
            self.reopen(new_epoch=True)

    def prune(self, early):
        j = self.j
        before = _on_disk(j)
        durable, epoch = j.durable, j.epoch
        limit = self.now.timestamp() - mod.JOURNAL_RETENTION_DAYS * DAY
        j.prune(self.now, early=early)
        after = _on_disk(j)
        for name, (recs, mtime) in before.items():
            if name in after:
                continue
            self.pruned += 1
            # Every record in a deleted segment was durable — in THIS epoch.
            above = [r for r in recs if r[0] != epoch or r[1] > durable]
            assert not above, ("deleted %s holding %s above durable %d"
                               % (name, above[:3], durable))
            if not early:
                assert mtime <= limit, (
                    "deleted %s at %.1f days old" % (
                        name, (self.now.timestamp() - mtime) / DAY))
        # and nothing that survived lost a line
        for name, (recs, _m) in after.items():
            assert before[name][0] == recs


@pytest.mark.parametrize("seed", SEEDS)
def test_no_segment_is_deleted_above_durable_or_under_30_days(tmp_path, seed):
    rng = random.Random(seed)
    w = World(tmp_path, rng)
    for _ in range(120):
        w.step()
    # finish with the most permissive state, so every run prunes something
    # if anything at all is prunable
    w.j.set_acked(w.j.next_seq() - 1, w.j.next_seq() - 1)
    w.prune(early=False)


def test_the_property_runs_actually_delete_segments(tmp_path):
    """A property that never sees a deletion proves nothing: across the
    seeds, real segments must have been pruned under the rule."""
    total = 0
    for seed in range(40):
        rng = random.Random(seed)
        w = World(tmp_path / str(seed), rng)
        for _ in range(120):
            w.step()
        w.j.set_acked(w.j.next_seq() - 1, w.j.next_seq() - 1)
        for name in _segments(w.j):
            t = (w.now - timedelta(days=45)).timestamp()
            os.utime(os.path.join(w.dir, name), (t, t))
        w.prune(early=False)
        total += w.pruned
    assert total >= 40


@pytest.mark.parametrize("seed", range(60))
def test_under_disk_pressure_only_the_age_rule_is_lifted(tmp_path, seed):
    rng = random.Random(10_000 + seed)
    w = World(tmp_path, rng)
    for _ in range(80):
        w.step()
        if rng.random() < 0.2:
            w.prune(early=True)
    w.prune(early=True)


def test_a_durable_claim_beyond_acked_is_clamped(tmp_path):
    j = mod.BenchJournal(str(tmp_path / "j"), "b1")
    j.append([{"kind": "run"} for _ in range(9)])
    j.set_acked(5, durable=9)
    assert (j.acked, j.durable) == (5, 5)
    with open(os.path.join(j.dir, mod.JOURNAL_META_NAME)) as f:
        assert json.load(f)["durable"] == 5


def test_a_lower_durable_from_a_restored_server_is_adopted(tmp_path):
    """After a restore the server's backups hold less than the bench was
    told; the bench believes the lower number and keeps more."""
    j = mod.BenchJournal(str(tmp_path / "j"), "b1")
    j.append([{"kind": "run"} for _ in range(9)])
    j.set_acked(9, durable=9)
    j.set_acked(6, durable=4)
    assert (j.acked, j.durable) == (6, 4)
