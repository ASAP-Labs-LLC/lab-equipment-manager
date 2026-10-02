"""S3 / T3 and the restore drill, end to end (transfer spec §3.4, §11).

The custody argument has three parties and only works if all three keep their
side of it:

1. the SERVER takes an hourly backup and, only once that copy is checked,
   tells each bench how far its records are `durable`;
2. the BENCH deletes a journal segment only when every record in it is at or
   below `durable` AND the segment is 30 days old — so for 30 days after any
   backup the bench is an independent second copy;
3. after a RESTORE the server is behind the bench. The bench sends from its own
   acked+1, the server answers 409 `cursor` with what it really holds, and the
   bench resends from there out of its journal.

So the bar is RPO 0 for bench records: backup, more ingest, restore, 409,
resend — and the store equals the pre-restore set, record for record. These
tests drive the REAL module journal (`lem_station_module.BenchJournal`) against
the real server and the real `custody` backup and restore, with the bench
pruning its journal in between, so a disagreement between the three parties
fails here rather than on the floor.

What is NOT RPO 0 is said too: rows the SERVER originates (a typed comment, a
correction, an audit row) exist only in the store, and a restore loses those
written since the backup — RPO <= 1 h, the backup interval (§11). The test
pins that boundary instead of letting the RPO 0 claim stretch over it.

The drill is the monthly proof that a backup is a backup: restore the newest
one to a scratch folder, re-verify its manifest, boot a real LEM on a scratch
port against it READ-ONLY (the candidate-boot shape, so the drill cannot
write), ask /healthz, compare counts, and write the result into `store_meta`,
where Settings shows it.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

import bench_v2_kit as kit
import custody
from bench_v2_kit import UID
from lem_store import LocalStoreGateway

MODULE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                          "LEM Station Module"))
#: The one port this piece may boot a server on (gauntlet assignment).
DRILL_PORT = int(os.environ.get("LEM_TEST_DRILL_PORT", "5705"))
T0 = datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def m():
    if MODULE_DIR not in sys.path:
        sys.path.insert(0, MODULE_DIR)
    import lem_station_module
    return lem_station_module


class RealBench:
    """The real module journal, and the uploader's one rule written out:
    send from acked+1 out of the journal; adopt the `acked` of any 200 or 409,
    and the `durable` of a 200."""

    def __init__(self, m, path, client, token):
        self.m = m
        self.j = m.BenchJournal(str(path), UID, segment_bytes=2048)
        self.client = client
        self.token = token
        self.n = 0
        self.answers = []

    def print_lines(self, k):
        recs = []
        for _ in range(k):
            self.n += 1
            n = self.n
            lab, val = "L-%05d" % n, "%.1f" % (40 + (n % 30) * 0.1)
            row = [list(self.m.build_log_insert(
                UID, "run", datetime(2026, 9, 30, 8, 0) + timedelta(minutes=n),
                lab_id=lab, detail={"values": {"Flash": val}})[1])]
            recs.append({"kind": "run", "origin": "live", "src": "file",
                         "pk": "pk-%d" % n, "lh": "lh-%d" % n, "lab_id": lab,
                         "values": {"Flash": val}, "raw": {},
                         "corrections": {}, "log": row})
        refs = self.j.append(recs)
        # Logged and filed: nothing in these is still owed to LabCore, so
        # only `durable` and age stand between them and retention.
        self.j.mark_projected(refs)
        self.j.mark_settled(refs)
        return self

    def _lines(self, start):
        out = []
        for name in sorted(os.listdir(self.j.dir)):
            if not name.startswith("seg-"):
                continue
            with open(os.path.join(self.j.dir, name), "rb") as f:
                for raw in f.read().splitlines():
                    if raw:
                        d = json.loads(raw)
                        if d["epoch"] == self.j.epoch and d["seq"] >= start:
                            out.append(d)
        return sorted(out, key=lambda d: d["seq"])

    def sync(self):
        from_seq = self.j.acked + 1
        doc = {"machine_uid": UID, "epoch": self.j.epoch, "proto": 2,
               "module_version": self.m.MODULE_VERSION, "from_seq": from_seq,
               "records": self._lines(from_seq)[:100],
               "stats": {"digest": self.j.digest(self.j.acked),
                         "records_total": self.j.next_seq() - 1}}
        r = self.client.post("/api/v2/bench/%s/sync" % UID, json=doc,
                             headers={"X-LEM-Bench-Token": self.token})
        self.answers.append(r)
        body = r.get_json()
        if r.status_code == 200:
            self.j.set_acked(body["acked"], body["durable"])
        elif r.status_code == 409:
            self.j.set_acked(body["acked"])
        else:
            raise AssertionError((r.status_code, body))
        return r

    def drain(self):
        for _ in range(1000):
            if self.j.acked >= self.j.next_seq() - 1:
                return
            self.sync()
        raise AssertionError("did not drain")

    def segments(self):
        return sorted(n for n in os.listdir(self.j.dir) if n.startswith("seg-"))

    def age_everything(self, days):
        """Make every segment look `days` old (the retention clock is the
        segment's mtime)."""
        t = (datetime.now() - timedelta(days=days)).timestamp()
        for n in self.segments():
            os.utime(os.path.join(self.j.dir, n), (t, t))


def _bench_set(store):
    """What the store holds of the bench, by identity AND content:
    every record the bench sent, and every log row made from them."""
    rec = store.read_sql("SELECT machine_uid, bench_epoch, bench_seq, kind, "
                         "body FROM bench_record ORDER BY bench_seq")
    assert "error" not in rec, rec
    log = store.read_sql(
        # raw-log: custody compares every row, hidden or not
        "SELECT machine_uid, ts, kind, lab_id, test_name, value, detail, "
        "origin, bench_epoch, bench_seq, content_key FROM lem_machine_log "
        "WHERE bench_epoch IS NOT NULL")
    assert "error" not in log, log
    return (Counter(tuple(r.values()) for r in rec["rows"]),
            Counter(tuple(r.values()) for r in log["rows"]))


def test_S3_T3_backup_ingest_restore_409_resend_equals_the_pre_restore_set(
        tmp_path, m):
    (tmp_path / "store").mkdir()
    path = str(tmp_path / "store" / "lem.db")
    store = LocalStoreGateway(path)
    kit.seed_machine(store)
    clock = lambda: T0                                  # noqa: E731
    cust = custody.Custody(store, backup_dir=str(tmp_path / "backup"),
                           clock=clock)
    cust.hydrate()
    client = kit.make_app(store).test_client()
    bench = RealBench(m, tmp_path / "journal", client, kit.enroll(client))

    # 1. Twelve polls (5 prints and 2 marks each), then the hourly backup.
    for _ in range(12):
        bench.print_lines(5).sync()
    at_backup = bench.j.acked
    assert at_backup == 84 and bench.j.durable == 0
    backup = cust.backup_now()
    assert backup["ok"], backup
    bench.print_lines(5).sync()                         # the next poll hears it
    assert bench.j.durable == at_backup                 # what the copy holds

    # 2. More ingest after the backup — bench records AND a server-made row.
    for _ in range(8):
        bench.print_lines(5).sync()
    total = bench.j.acked
    assert total == at_backup + 9 * 7
    store.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
              "test_name, value, detail) VALUES (?, '2026-10-01T10:00:00', "
              "'comment', '', '', 'typed after the backup', '{}')", [UID])
    pre_restore = _bench_set(store)
    assert sum(pre_restore[0].values()) == total

    # 3. The bench's retention runs: a month on, everything it may delete it
    #    deletes. Nothing above `durable` may go.
    segs_before = bench.segments()
    bench.age_everything(days=31)
    pruned = bench.j.prune(datetime.now())
    assert pruned >= 1, "the test must exercise a real prune"
    left = bench._lines(1)
    assert left[0]["seq"] <= at_backup + 1, "a record above durable was pruned"
    assert len(bench.segments()) < len(segs_before)

    # 4. The disk dies; the newest backup goes back.
    store.close()
    done = custody.restore(backup["path"], path, now=T0)
    assert done["ok"] and os.path.exists(done["kept"])
    restored = LocalStoreGateway(path)
    assert sum(_bench_set(restored)[0].values()) == at_backup

    # 5. The bench, which knows more than the server now, sends from total+1.
    bench.client = kit.make_app(restored).test_client()
    bench.print_lines(5)
    r = bench.sync()
    assert r.status_code == 409
    assert r.get_json()["acked"] == at_backup
    bench.drain()
    after = _bench_set(restored)

    # 6. Equal to the pre-restore set, plus only what was printed since.
    new_seqs = set(range(total + 1, bench.j.acked + 1))
    assert len(new_seqs) == 7
    rec_after = Counter({k: v for k, v in after[0].items()
                         if k[2] not in new_seqs})
    log_after = Counter({k: v for k, v in after[1].items()
                         if k[9] not in new_seqs})
    assert rec_after == pre_restore[0]
    assert log_after == pre_restore[1]
    lost = sum((pre_restore[0] - rec_after).values())
    dup = sum((rec_after - pre_restore[0]).values())
    assert (lost, dup) == (0, 0)
    cur = restored.read_sql("SELECT acked_seq, digest FROM bench_cursor"
                            )["rows"][0]
    assert cur["acked_seq"] == total + 7
    assert cur["digest"] == bench.j.digest(total + 7)

    # 7. The boundary, said plainly: a SERVER-made row from after the backup
    #    is gone (RPO <= 1 h for those; there is no second copy of them).
    typed = restored.read_sql(
        # raw-log: the test looks for one specific row
        "SELECT COUNT(*) AS n FROM lem_machine_log WHERE value = "
        "'typed after the backup'")["rows"][0]["n"]
    assert typed == 0
    restored.close()


def test_restore_after_a_backup_of_a_restored_store_still_converges(
        tmp_path, m):
    """A second restore cycle: durable is re-earned from the restored store's
    next backup, never inherited from the lost one's."""
    (tmp_path / "store").mkdir()
    path = str(tmp_path / "store" / "lem.db")
    store = LocalStoreGateway(path)
    kit.seed_machine(store)
    cust = custody.Custody(store, backup_dir=str(tmp_path / "backup"),
                           clock=lambda: T0)
    client = kit.make_app(store).test_client()
    bench = RealBench(m, tmp_path / "journal", client, kit.enroll(client))
    bench.print_lines(10).sync()
    first = bench.j.acked
    b1 = cust.backup_now()
    bench.print_lines(10).sync()
    assert bench.j.durable == first
    store.close()
    custody.restore(b1["path"], path, now=T0)
    s2 = LocalStoreGateway(path)
    bench.client = kit.make_app(s2).test_client()
    bench.print_lines(1).sync()                 # 409
    bench.drain()
    # The restored store's own cursor says durable 0 (its backup was taken
    # BEFORE it published any): the bench adopts that and holds everything.
    assert bench.j.durable == 0
    c2 = custody.Custody(s2, backup_dir=str(tmp_path / "backup"),
                         clock=lambda: T0 + timedelta(hours=1))
    assert c2.backup_now()["ok"]
    held = bench.j.acked
    bench.print_lines(1).sync()
    assert bench.j.durable == held
    s2.close()


# ── the restore drill ────────────────────────────────────────────────────────

def _port_free(port):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


@pytest.fixture
def drill_env(tmp_path):
    (tmp_path / "store").mkdir()
    store = LocalStoreGateway(str(tmp_path / "store" / "lem.db"))
    kit.seed_machine(store)
    cust = custody.Custody(store, backup_dir=str(tmp_path / "backup"),
                           clock=lambda: T0,
                           drill_dir=str(tmp_path / "drill"))
    cust.hydrate()
    app = kit.make_app(store)
    custody.attach(app, cust)
    client = app.test_client()
    bench = kit.Bench(client, kit.enroll(client))
    bench.journal(25).sync()
    yield store, cust, client
    store.close()


@pytest.mark.skipif(not _port_free(DRILL_PORT),
                    reason="the drill port is busy on this machine")
def test_the_drill_restores_boots_a_scratch_port_and_records_the_result(
        drill_env):
    store, cust, client = drill_env
    assert cust.backup_now()["ok"]
    out = cust.drill_now(port=DRILL_PORT)
    assert out["ok"] is True, out
    assert out["port"] == DRILL_PORT
    checks = {c["name"]: c for c in out["checks"]}
    assert checks["manifest"]["ok"] and checks["healthz"]["ok"]
    assert checks["counts"]["ok"]
    assert "lem_machine_log" in checks["counts"]["detail"]
    assert checks["healthz"]["read_only"] is True
    assert out["rto_s"] > 0
    row = store.read_sql("SELECT key, value FROM store_meta WHERE key LIKE "
                         "'drill_%'")["rows"]
    meta = {r["key"]: r["value"] for r in row}
    assert meta["drill_ok"] == "1"
    assert meta["drill_at"] == T0.isoformat()
    assert meta["drill_backup"] == os.path.basename(
        custody.list_backups(cust.backup_dir)[0]["path"])
    assert json.loads(meta["drill_detail"])["port"] == DRILL_PORT
    # the scratch server is gone and its copy cleaned up
    assert _port_free(DRILL_PORT)
    assert not os.listdir(cust.drill_dir)
    # Settings shows it
    html = client.get("/settings").get_data(as_text=True)
    assert "Never run" not in html


def test_a_drill_on_a_tampered_backup_fails_and_says_so(drill_env):
    store, cust, client = drill_env
    b = cust.backup_now()
    import sqlite3
    con = sqlite3.connect(b["path"])
    con.execute("DROP TRIGGER lem_log_no_update")
    con.execute("UPDATE lem_machine_log SET value = '1' WHERE id = 1")
    con.commit()
    con.close()
    out = cust.drill_now(port=DRILL_PORT)
    assert out["ok"] is False
    assert any("lem_machine_log" in p for p in out["problems"])
    meta = {r["key"]: r["value"] for r in store.read_sql(
        "SELECT key, value FROM store_meta")["rows"]}
    assert meta["drill_ok"] == "0"
    items = [i for i in cust.status_items() if i["key"].startswith("custody:drill")]
    assert items and items[0]["level"] == "error"


def test_a_drill_whose_port_is_taken_fails_fast_and_says_so(drill_env):
    store, cust, client = drill_env
    cust.backup_now()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    taken = s.getsockname()[1]
    try:
        out = cust.drill_now(port=taken)
    finally:
        s.close()
    assert out["ok"] is False
    assert any(str(taken) in p for p in out["problems"])


def test_a_drill_with_no_backup_is_a_failure_not_a_pass(tmp_path):
    (tmp_path / "store").mkdir()
    store = LocalStoreGateway(str(tmp_path / "store" / "lem.db"))
    cust = custody.Custody(store, backup_dir=str(tmp_path / "backup"),
                           clock=lambda: T0)
    out = cust.drill_now(port=DRILL_PORT)
    assert out["ok"] is False and "no backup" in out["problems"][0]
    store.close()
