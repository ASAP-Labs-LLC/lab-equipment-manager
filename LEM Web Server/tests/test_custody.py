"""Backup and custody (transfer spec §11, piece T-P11).

Moving the 17025 record out of LabCore moved it out of LabCore's backups too.
Until this file existed LEM's store was a single SQLite file on one disk, with
nothing behind it — the custody shift §11 states plainly: "the record is LESS
protected in the store than inside LabCore's backup". Each promise below is
one of the ways that would bite, and each test is written so it can fail:

* **A backup counts only once it is checked.** The online backup API gives a
  consistent copy, and `PRAGMA integrity_check` on that COPY decides whether
  it counts. A copy that fails is not a backup: nothing is published from it,
  and the failure is said, not swallowed (a failed backup is never "no backup
  needed").
* **`durable` is what the backup holds, not what the store holds.** A bench
  deletes its journal segments only at or below `durable` (and 30 days old).
  If the server published the LIVE `acked` after a backup, every record that
  arrived while the copy was being made would be marked safe while existing
  only on one disk — and the bench would be free to delete its own copy. So
  `durable_seq` is read out of the backup file itself.
* **A backup can prove it was not altered.** Each manifest carries, per day,
  the row count and a SHA-256 over that day's log rows by id (`log_digest`),
  plus the annotations and each bench epoch's running digest recomputed from
  what the bench sent. A row rewritten in a backup file — even by someone who
  drops the append-only trigger, edits, and puts the trigger back — fails
  re-verification and names its day. A restore of such a file is refused.
* **Off-host or no bridge-off.** Until an off-host copy has completed in the
  last 26 h, the bridge-off button refuses, in words, and changes nothing.
* **Every number here is reported from memory** on /healthz and in the global
  status, so the custody state is visible without anybody reading a log.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import bench_v2_kit as kit
import custody
from bench_v2_kit import Bench, UID
from lem_store import LocalStoreGateway

T0 = datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, at=T0):
        self.at = at

    def __call__(self):
        return self.at

    def advance(self, **kw):
        self.at = self.at + timedelta(**kw)
        return self.at


@pytest.fixture
def where(tmp_path):
    (tmp_path / "store").mkdir()
    return {"store": str(tmp_path / "store" / "lem.db"),
            "backup": str(tmp_path / "backup"),
            "offsite": str(tmp_path / "offsite"),
            "tmp": tmp_path}


@pytest.fixture
def store(where):
    s = LocalStoreGateway(where["store"])
    kit.seed_machine(s)
    yield s
    s.close()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def cust(store, where, clock):
    c = custody.Custody(store, backup_dir=where["backup"],
                        offsite_dir=where["offsite"], clock=clock)
    c.hydrate()
    return c


@pytest.fixture
def app(store, cust):
    a = kit.make_app(store)
    custody.attach(a, cust)
    return a


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def bench(client):
    return Bench(client, kit.enroll(client))


def _meta(store, key):
    res = store.read_sql("SELECT value FROM store_meta WHERE key = ?", [key])
    assert "error" not in res, res
    return res["rows"][0]["value"] if res["rows"] else None


def _cursor(store, epoch="ep-1"):
    res = store.read_sql("SELECT * FROM bench_cursor WHERE machine_uid = ? "
                         "AND bench_epoch = ?", [UID, epoch])
    assert "error" not in res, res
    return res["rows"][0]


def _server_row(store, value="x"):
    """A server-originated row (a comment someone typed)."""
    res = store.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, "
                    "lab_id, test_name, value, detail) VALUES (?, ?, "
                    "'comment', '', '', ?, '{}')",
                    [UID, "2026-09-30T14:00:00", value])
    assert "error" not in res, res


def _tamper(path, sql, args=()):
    """What someone with the bare file can do: drop the append-only guard,
    rewrite, and put the guard back exactly as it was."""
    con = sqlite3.connect(path)
    try:
        trig = dict(con.execute("SELECT name, sql FROM sqlite_master WHERE "
                                "type = 'trigger'").fetchall())
        for name in trig:
            con.execute('DROP TRIGGER "%s"' % name)
        con.execute(sql, list(args))
        for stmt in trig.values():
            con.execute(stmt)
        con.commit()
    finally:
        con.close()


# ── the backup itself ────────────────────────────────────────────────────────

class TestBackup:
    def test_a_backup_is_a_checked_copy_with_a_manifest(self, cust, store,
                                                        bench, where):
        bench.journal(12).sync()
        _server_row(store)
        out = cust.backup_now()
        assert out["ok"] is True, out
        db = out["path"]
        assert os.path.dirname(db) == where["backup"]
        man = custody.read_manifest(db)
        assert man["integrity_check"] == "ok"
        assert man["kind"] == "hourly"
        # the counts in the manifest are the copy's, and equal the store's
        assert man["tables"]["bench_record"] == 12
        assert man["tables"]["lem_machine_log"] == 13
        assert sum(d["rows"] for d in man["log_digest"].values()) == 13
        assert set(man["log_digest"]) == {"2026-10-01", "2026-09-30"}
        # the copy is ONE self-contained file: no -wal beside it to lose
        assert not os.path.exists(db + "-wal")
        con = sqlite3.connect(db)
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert con.execute("SELECT COUNT(*) FROM bench_record"
                           ).fetchone()[0] == 12
        con.close()
        assert _meta(store, "last_backup_ok") == "1"
        assert _meta(store, "last_backup_at") == T0.isoformat()
        assert _meta(store, "last_backup_file") == os.path.basename(db)

    def test_a_copy_that_fails_integrity_check_is_not_a_backup(
            self, cust, store, bench, where, monkeypatch):
        """And nothing is published from it: `durable` stays where it was,
        so no bench is told it may delete what this copy was meant to hold."""
        bench.journal(5).sync()
        monkeypatch.setattr(custody, "_integrity", lambda con: "*** in "
                            "database main ***\nPage 7: btreeInitPage() "
                            "returns error code 11")
        out = cust.backup_now()
        assert out["ok"] is False
        assert "integrity_check" in out["error"]
        assert _cursor(store)["durable_seq"] == 0
        assert _meta(store, "last_backup_ok") == "0"
        assert "btreeInitPage" in _meta(store, "last_backup_error")
        assert custody.list_backups(where["backup"]) == []
        assert not [n for n in os.listdir(where["backup"])
                    if n.endswith(".partial")]

    def test_a_backup_into_a_folder_that_cannot_be_written_is_said(
            self, cust, store, where):
        with open(where["backup"], "w") as f:      # a FILE where the folder goes
            f.write("x")
        out = cust.backup_now()
        assert out["ok"] is False and out["error"]
        assert _meta(store, "last_backup_ok") == "0"

    def test_a_read_only_store_takes_no_backup(self, where, store, clock):
        """The updater's candidate boot (§5.1): it runs no migration, ingest
        or backup. It must not even create the backup folder."""
        ro = LocalStoreGateway(where["store"], read_only=True)
        c = custody.Custody(ro, backup_dir=where["backup"], clock=clock)
        c.hydrate()
        assert c.tick() == []
        assert c.backup_now()["ok"] is False
        assert not os.path.exists(where["backup"])
        ro.close()


# ── durable: what a completed backup holds ───────────────────────────────────

class TestDurable:
    def test_durable_is_the_acked_the_backup_file_holds(self, cust, store,
                                                        bench):
        bench.journal(10).sync()
        assert bench.answers[-1].get_json()["durable"] == 0
        # Five more arrive WHILE the copy is being checked: the live store
        # holds 15, the copy 10. Publishing 15 would let the bench delete
        # five records that exist on one disk only.
        out = cust.backup_now(_after_copy=lambda: bench.journal(5).sync())
        assert out["ok"], out
        assert _cursor(store)["acked_seq"] == 15
        assert _cursor(store)["durable_seq"] == 10
        r = bench.journal(1).sync()
        assert r.get_json()["durable"] == 10
        assert r.get_json()["acked"] == 16
        cust.backup_now()
        assert _cursor(store)["durable_seq"] == 16

    def test_durable_never_goes_backwards_and_never_passes_acked(
            self, cust, store, bench, where):
        bench.journal(8).sync()
        cust.backup_now()
        assert _cursor(store)["durable_seq"] == 8
        # An OLDER backup file published again (a re-run, a copy put back)
        # cannot lower what a newer backup already made durable.
        old = custody.list_backups(where["backup"])[0]["path"]
        bench.journal(4).sync()
        cust.backup_now()
        assert _cursor(store)["durable_seq"] == 12
        cust.publish_durable(custody.read_manifest(old))
        assert _cursor(store)["durable_seq"] == 12
        # and a manifest claiming more than the store has acked is clamped
        fake = {"durable": [{"machine_uid": UID, "epoch": "ep-1",
                             "acked": 999}]}
        cust.publish_durable(fake)
        assert _cursor(store)["durable_seq"] == 12

    def test_a_failed_backup_publishes_nothing(self, cust, store, bench,
                                               monkeypatch):
        bench.journal(3).sync()
        cust.backup_now()
        bench.journal(3).sync()

        def boom(*a, **k):
            raise OSError("No space left on device")
        monkeypatch.setattr(custody, "_write_manifest", boom)
        out = cust.backup_now()
        assert out["ok"] is False and "No space" in out["error"]
        assert _cursor(store)["durable_seq"] == 3


# ── the daily digest and re-verification ────────────────────────────────────

class TestManifest:
    def test_log_digest_is_written_to_the_store_and_the_manifest(
            self, cust, store, bench):
        bench.journal(6).sync()
        _server_row(store, "a")
        _server_row(store, "b")
        out = cust.backup_now()
        man = custody.read_manifest(out["path"])
        res = store.read_sql("SELECT day, rows, sha256 FROM log_digest "
                             "ORDER BY day")
        assert {r["day"]: {"rows": r["rows"], "sha256": r["sha256"]}
                for r in res["rows"]} == man["log_digest"]
        assert man["log_digest"]["2026-09-30"]["rows"] == 2
        assert len(man["log_digest"]["2026-09-30"]["sha256"]) == 64

    def test_an_untouched_backup_reverifies(self, cust, bench):
        bench.journal(6).sync()
        out = cust.backup_now()
        v = custody.verify(out["path"])
        assert v["ok"] is True, v["problems"]
        assert v["problems"] == []

    def test_a_tampered_log_row_fails_reverification_and_names_its_day(
            self, cust, store, bench, tmp_path):
        bench.journal(6).sync()
        _server_row(store, "kept")
        out = cust.backup_now()
        bad = str(tmp_path / "copy.db")
        shutil.copyfile(out["path"], bad)
        shutil.copyfile(custody.manifest_path(out["path"]),
                        custody.manifest_path(bad))
        _tamper(bad, "UPDATE lem_machine_log SET value = '99.9' "
                     "WHERE kind = 'run' AND id = 3")
        # the guards are back exactly as declared: the schema audit sees
        # nothing wrong, so only the digest can catch it
        v = custody.verify(bad)
        assert v["ok"] is False
        assert any("2026-10-01" in p and "lem_machine_log" in p
                   for p in v["problems"]), v["problems"]
        # a re-stamped file hash does not hide it: the day digest still differs
        man = custody.read_manifest(bad)
        man["sha256"] = custody.file_sha256(bad)
        with open(custody.manifest_path(bad), "w") as f:
            json.dump(man, f)
        v2 = custody.verify(bad)
        assert v2["ok"] is False
        assert any("2026-10-01" in p for p in v2["problems"])

    def test_a_deleted_row_and_a_hidden_row_both_fail(self, cust, store, bench,
                                                     tmp_path):
        bench.journal(4).sync()
        out = cust.backup_now()
        for i, sql in enumerate((
                "DELETE FROM lem_machine_log WHERE id = 2",
                "INSERT INTO log_annotation (log_id, label, by, at) VALUES "
                "(2, 'replay_duplicate', 'someone', '2026-10-01')",
                "UPDATE bench_record SET body = replace(body, 'L-00003', "
                "'L-00009') WHERE bench_seq = 3")):
            bad = str(tmp_path / ("copy%d.db" % i))
            shutil.copyfile(out["path"], bad)
            shutil.copyfile(custody.manifest_path(out["path"]),
                            custody.manifest_path(bad))
            _tamper(bad, sql)
            v = custody.verify(bad)
            assert v["ok"] is False, sql
            assert v["problems"], sql

    def test_a_backup_with_no_manifest_is_not_a_backup(self, cust, bench,
                                                       where):
        bench.journal(2).sync()
        out = cust.backup_now()
        os.remove(custody.manifest_path(out["path"]))
        assert custody.list_backups(where["backup"]) == []
        v = custody.verify(out["path"])
        assert v["ok"] is False and "manifest" in v["problems"][0]

    def test_verify_never_opens_the_backup_in_place(self, cust, bench,
                                                    monkeypatch):
        """§11 Access: backups are copied, never opened in place. Opening a
        file with SQLite can write to it (a hot journal is rolled back)."""
        bench.journal(2).sync()
        out = cust.backup_now()
        opened = []
        real = sqlite3.connect

        def spy(target, *a, **k):
            opened.append(str(target))
            return real(target, *a, **k)
        monkeypatch.setattr(custody.sqlite3, "connect", spy)
        before = os.stat(out["path"]).st_mtime_ns
        assert custody.verify(out["path"])["ok"]
        assert opened and out["path"] not in opened
        assert not any(o.startswith("file:" + out["path"]) for o in opened)
        assert os.stat(out["path"]).st_mtime_ns == before


# ── restore ──────────────────────────────────────────────────────────────────

class TestRestore:
    def test_restore_refuses_a_backup_that_fails_verification(
            self, cust, store, bench, where, tmp_path):
        bench.journal(4).sync()
        out = cust.backup_now()
        _tamper(out["path"], "UPDATE lem_machine_log SET value = '0' "
                             "WHERE id = 1")
        store.close()
        before = custody.file_sha256(where["store"])
        with pytest.raises(custody.RestoreRefused) as exc:
            custody.restore(out["path"], where["store"])
        assert "2026-10-01" in str(exc.value)
        assert custody.file_sha256(where["store"]) == before

    def test_restore_keeps_the_file_it_replaces(self, cust, store, bench,
                                                where):
        bench.journal(4).sync()
        out = cust.backup_now()
        bench.journal(4).sync()
        store.close()
        done = custody.restore(out["path"], where["store"], now=T0)
        assert os.path.exists(done["kept"])
        con = sqlite3.connect(done["kept"])
        assert con.execute("SELECT COUNT(*) FROM bench_record"
                           ).fetchone()[0] == 8
        con.close()
        again = LocalStoreGateway(where["store"])
        assert len(kit.stored_seqs(again)) == 4
        again.close()


# ── retention 48 / 35 / 24 ───────────────────────────────────────────────────

class TestRetention:
    def _hourly(self, days):
        end = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
        return [end - timedelta(hours=h) for h in range(days * 24)]

    def test_the_plan_keeps_48_hourly_35_daily_24_monthly_and_nothing_else(
            self):
        times = self._hourly(800)
        items = [{"name": t.isoformat(), "at": t, "kind": "hourly"}
                 for t in times]
        keep = {i["name"] for i in custody.retention_keep(items)}
        newest = sorted(times, reverse=True)
        # 1. the 48 newest
        assert {t.isoformat() for t in newest[:48]} <= keep
        # 2. the newest of each of the 35 newest days
        days, months = {}, {}
        for t in newest:
            days.setdefault(t.date(), t)
            months.setdefault((t.year, t.month), t)
        day_keep = {t.isoformat() for d, t in sorted(days.items(),
                                                    reverse=True)[:35]}
        month_keep = {t.isoformat() for m, t in sorted(months.items(),
                                                      reverse=True)[:24]}
        assert day_keep <= keep and month_keep <= keep
        # 3. and nothing else
        assert keep == ({t.isoformat() for t in newest[:48]} | day_keep
                        | month_keep)
        assert len(keep) < 48 + 35 + 24

    def test_pre_migration_copies_are_never_pruned(self):
        times = self._hourly(400)
        items = [{"name": t.isoformat(), "at": t, "kind": "hourly"}
                 for t in times]
        old = datetime(2024, 1, 1, tzinfo=timezone.utc)
        items.append({"name": "pre", "at": old, "kind": "pre_migration"})
        keep = {i["name"] for i in custody.retention_keep(items)}
        assert "pre" in keep

    def test_pruning_removes_the_file_and_its_manifest_together(
            self, cust, store, bench, where, clock):
        bench.journal(1).sync()
        for _ in range(51):
            assert cust.backup_now()["ok"]
            clock.advance(hours=1)
        names = os.listdir(where["backup"])
        dbs = [n for n in names if n.endswith(".db")]
        mans = [n for n in names if n.endswith(".manifest.json")]
        # 51 hourly over ~2 days: 48 newest + the newest of the oldest day
        assert len(dbs) == len(mans)
        assert len(dbs) == len(custody.list_backups(where["backup"]))
        assert 48 <= len(dbs) < 51


# ── off-host ─────────────────────────────────────────────────────────────────

class TestOffsite:
    def test_the_newest_backup_is_copied_off_host_and_checked_there(
            self, cust, store, bench, where):
        bench.journal(3).sync()
        b = cust.backup_now()
        out = cust.offsite_now()
        assert out["ok"] is True, out
        there = os.path.join(where["offsite"], os.path.basename(b["path"]))
        assert custody.file_sha256(there) == custody.read_manifest(
            b["path"])["sha256"]
        assert os.path.exists(custody.manifest_path(there))
        assert _meta(store, "offsite_last_ok") == T0.isoformat()

    def test_with_no_target_named_nothing_is_claimed(self, store, where,
                                                     clock, bench):
        c = custody.Custody(store, backup_dir=where["backup"],
                            offsite_dir=None, clock=clock)
        c.hydrate()
        bench.journal(1).sync()
        c.backup_now()
        out = c.offsite_now()
        assert out["ok"] is False and "no off-host target" in out["error"]
        assert _meta(store, "offsite_last_ok") is None

    def test_a_copy_that_arrives_different_is_a_failure(
            self, cust, store, bench, where, monkeypatch):
        bench.journal(3).sync()
        cust.backup_now()
        real = shutil.copyfile

        def lossy(src, dst, *a, **k):
            real(src, dst, *a, **k)
            if str(src).endswith(".db"):
                with open(dst, "r+b") as f:
                    f.seek(200)
                    f.write(b"\x00\x01\x02")
            return dst
        monkeypatch.setattr(custody.shutil, "copyfile", lossy)
        out = cust.offsite_now()
        assert out["ok"] is False and "SHA-256" in out["error"]
        assert _meta(store, "offsite_last_ok") is None
        assert _meta(store, "offsite_last_error")
        # and the damaged copy is not left looking like a good one
        assert not [n for n in os.listdir(where["offsite"])
                    if n.endswith(".db")]

    def test_an_unreachable_target_is_said(self, cust, store, bench, where):
        bench.journal(1).sync()
        cust.backup_now()
        with open(where["offsite"], "w") as f:    # a FILE where the share is
            f.write("x")
        out = cust.offsite_now()
        assert out["ok"] is False
        assert _meta(store, "offsite_last_ok") is None


# ── reconciliation per epoch ─────────────────────────────────────────────────

class TestReconcile:
    def test_agreeing_epochs_reconcile(self, cust, store, bench):
        bench.journal(9).sync()
        out = cust.backup_now()
        rec = out["reconcile"]
        assert rec == [{"machine_uid": UID, "epoch": "ep-1", "acked": 9,
                        "held": 9, "records_total": None, "ok": True,
                        "problems": []}]
        assert cust.status_items() == [i for i in cust.status_items()
                                       if not i["key"].startswith("custody:reconcile")]

    def test_a_record_rewritten_in_the_store_is_caught_and_raised(
            self, cust, store, bench, where):
        bench.journal(9).sync()
        store.close()
        _tamper(where["store"], "UPDATE bench_record SET body = replace("
                                "body, 'L-00004', 'L-00007') WHERE bench_seq = 4")
        s2 = LocalStoreGateway(where["store"])
        c2 = custody.Custody(s2, backup_dir=where["backup"], clock=cust.clock)
        c2.hydrate()
        out = c2.backup_now()
        rec = out["reconcile"][0]
        assert rec["ok"] is False
        assert any("running digest" in p for p in rec["problems"])
        items = [i for i in c2.status_items() if i["key"].startswith(
            "custody:reconcile")]
        assert items and items[0]["level"] == "error"
        assert "PAC Flash 1" in items[0]["message"]
        assert items[0]["href"] == "/settings#backups"
        s2.close()

    def test_a_bench_that_counts_fewer_than_lem_holds_is_raised(
            self, cust, store, bench):
        bench.journal(5)
        r = bench.post_doc(bench.body(stats={"records_total": 3,
                                             "digest": kit.DIGEST_ZERO}))
        assert r.status_code == 200
        rec = cust.backup_now()["reconcile"][0]
        assert rec["ok"] is False and rec["records_total"] == 3
        assert any("3" in p and "5" in p for p in rec["problems"])

    def test_a_digest_mismatch_the_bench_reported_is_raised(self, cust,
                                                           store, bench):
        bench.journal(3).sync()
        bench.journal(1)
        bench.post_doc(bench.body(stats={"digest": "f" * 64}))
        rec = cust.backup_now()["reconcile"][0]
        assert rec["ok"] is False
        assert any("journal" in p for p in rec["problems"])


# ── bridge-off refusal ───────────────────────────────────────────────────────

def _sign_in(client):
    r = client.post("/api/login", json={"username": "ryan",
                                        "password": "good"})
    assert r.status_code == 200


class TestBridgeOff:
    def test_refuses_with_no_off_host_copy_ever(self, client, store):
        _sign_in(client)
        r = client.post("/api/transfer/bridge", json={"on": False})
        assert r.status_code == 409
        body = r.get_json()
        assert body["error"] == "refused"
        assert any("off-host" in s for s in body["refusals"])
        assert _meta(store, "bridge") is None

    def test_refuses_with_an_off_host_copy_older_than_26_h(
            self, client, store, cust, bench, clock):
        _sign_in(client)
        bench.journal(1).sync()
        cust.backup_now()
        assert cust.offsite_now()["ok"]
        clock.advance(hours=26, minutes=1)
        r = client.post("/api/transfer/bridge", json={"on": False})
        assert r.status_code == 409
        assert any("26 h" in s for s in r.get_json()["refusals"])
        assert _meta(store, "bridge") is None

    def test_allows_with_a_fresh_off_host_copy(self, client, store, cust,
                                               bench, clock):
        _sign_in(client)
        bench.journal(1).sync()
        cust.backup_now()
        cust.offsite_now()
        clock.advance(hours=25, minutes=59)
        r = client.post("/api/transfer/bridge", json={"on": False})
        assert r.status_code == 200, r.get_json()
        assert _meta(store, "bridge") == "off"
        assert _meta(store, "bridge_changed_by") == "ryan"
        # and back on is never refused: re-projection is idempotent (§10.4)
        r = client.post("/api/transfer/bridge", json={"on": True})
        assert r.status_code == 200 and _meta(store, "bridge") == "on"

    def test_the_refusal_at_26_h_1_min_says_the_age_without_contradiction(
            self, client, cust, bench, clock):
        """At 26 h 1 min "is 26 h old; only within 26 h" reads as if it
        should have been allowed. The age is said to the minute."""
        _sign_in(client)
        bench.journal(1).sync()
        cust.backup_now()
        cust.offsite_now()
        clock.advance(hours=26, minutes=1)
        text = client.post("/api/transfer/bridge",
                           json={"on": False}).get_json()["refusals"][0]
        assert "26 h 1 min old" in text, text
        assert "more than 26 h" in text, text

    def test_an_offsite_time_in_the_future_refuses(self, client, store, cust,
                                                   clock):
        """A clock set wrong, or an edited value, must not unlock bridge-off:
        a copy "made" in the future proves nothing about a copy made now."""
        _sign_in(client)
        future = (clock() + custody.timedelta(days=3650)).isoformat()
        store.sql("INSERT INTO store_meta (key, value) VALUES "
                  "('offsite_last_ok', ?)", [future])
        cust.hydrate()
        r = client.post("/api/transfer/bridge", json={"on": False})
        assert r.status_code == 409
        assert any("future" in s for s in r.get_json()["refusals"])
        assert _meta(store, "bridge") is None
        # a few seconds of skew between two clocks is not "the future"
        store.sql("UPDATE store_meta SET value = ? WHERE key = "
                  "'offsite_last_ok'",
                  [(clock() + custody.timedelta(seconds=30)).isoformat()])
        cust.hydrate()
        assert cust.bridge_off_refusals() == []

    def test_a_stored_offsite_time_that_cannot_be_read_refuses(
            self, client, store, cust):
        _sign_in(client)
        store.sql("INSERT INTO store_meta (key, value) VALUES "
                  "('offsite_last_ok', 'yesterday-ish')")
        cust.hydrate()
        r = client.post("/api/transfer/bridge", json={"on": False})
        assert r.status_code == 409

    def test_signed_out_is_401_and_changes_nothing(self, client, store):
        r = client.post("/api/transfer/bridge", json={"on": False})
        assert r.status_code == 401
        assert _meta(store, "bridge") is None

    def test_the_refusals_are_readable_before_pressing(self, client):
        r = client.get("/api/transfer/bridge")
        assert r.status_code == 200
        body = r.get_json()
        assert body["on"] is True and body["refusals"]


# ── what people see ──────────────────────────────────────────────────────────

class TestVisible:
    def test_healthz_reports_backup_and_offsite_from_memory(
            self, client, cust, bench, clock):
        h = client.get("/healthz").get_json()["store"]
        assert h["last_backup_at"] is None and h["last_backup_ok"] is None
        assert h["offsite_last_ok"] is None
        bench.journal(1).sync()
        cust.backup_now()
        cust.offsite_now()
        h = client.get("/healthz").get_json()["store"]
        assert h["last_backup_at"] == T0.isoformat()
        assert h["last_backup_ok"] is True
        assert h["offsite_last_ok"] == T0.isoformat()

    def test_a_backup_3_h_old_is_amber_in_the_global_status(
            self, cust, bench, clock):
        bench.journal(1).sync()
        cust.backup_now()
        cust.offsite_now()
        cust.record_drill({"ok": True, "backup": "x.db", "problems": []})
        assert cust.status_items() == []
        clock.advance(hours=3, minutes=5)
        items = cust.status_items()
        assert [i["message"] for i in items] == [
            "The newest backup of the LEM store is 3 h old."]
        assert items[0]["level"] == "warning"

    def test_a_failed_backup_is_red_and_says_why(self, cust, monkeypatch):
        monkeypatch.setattr(custody, "_integrity", lambda con: "bad page")
        cust.backup_now()
        items = cust.status_items()
        assert items[0]["level"] == "error"
        assert "bad page" in items[0]["message"]

    def test_the_global_status_carries_the_custody_items(self, client, cust,
                                                         monkeypatch):
        monkeypatch.setattr(custody, "_integrity", lambda con: "bad page")
        cust.backup_now()
        body = client.get("/api/ui/live").get_json()
        msgs = [n["message"] for n in body.get("notifications") or []]
        assert any("backup" in m.lower() for m in msgs), msgs

    def test_settings_shows_backups_offsite_drill_and_the_bridge(
            self, client, cust, bench):
        html = client.get("/settings").get_data(as_text=True)
        assert 'id="backups"' in html
        assert "No backup yet" in html
        assert "Never run" in html
        bench.journal(1).sync()
        cust.backup_now()
        html = client.get("/settings").get_data(as_text=True)
        assert "Checked" in html
        assert "No off-host copy yet" in html
        assert 'data-testid="bridge-off"' in html

    def test_api_custody_is_memory_and_disk_only(self, store, cust, bench):
        from labcore_counter import CountingLabCore
        lab = CountingLabCore()
        a = kit.make_app(store, labcore=lab)
        custody.attach(a, cust)
        c = a.test_client()
        bench.client = c
        bench.journal(1).sync()
        cust.backup_now()
        before = lab.ops
        r = c.get("/api/custody")
        assert r.status_code == 200
        assert r.get_json()["backups"]["count"] == 1
        assert lab.ops == before


# ── the schedule ─────────────────────────────────────────────────────────────

class TestSchedule:
    def test_hourly_backup_is_due_once_an_hour(self, cust, bench, clock):
        bench.journal(1).sync()
        assert "backup" in cust.tick()
        clock.advance(minutes=30)
        assert "backup" not in cust.tick()
        clock.advance(minutes=31)
        assert "backup" in cust.tick()

    def test_a_failed_backup_is_retried_in_minutes_not_an_hour(
            self, cust, clock, monkeypatch):
        monkeypatch.setattr(custody, "_integrity", lambda con: "bad")
        assert "backup" in cust.tick()
        clock.advance(minutes=6)
        assert "backup" in cust.tick()

    def test_offsite_runs_nightly(self, cust, bench, clock):
        clock.at = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
        bench.journal(1).sync()
        ran = cust.tick()
        assert "offsite" in ran          # never copied: as soon as possible
        clock.advance(hours=1)
        assert "offsite" not in cust.tick()
        clock.advance(hours=24)          # over a day since the last one
        assert "offsite" in cust.tick()

    def test_the_drill_is_due_monthly(self, cust, monkeypatch, clock, bench):
        calls = []
        monkeypatch.setattr(cust, "drill_now",
                            lambda **k: calls.append(1) or {"ok": True})
        bench.journal(1).sync()
        # Only a server's boot schedules the drill (it boots a second server);
        # an app, a test or a candidate boot never does it on its own.
        cust.tick()
        assert calls == []
        cust.schedule_drill = True
        clock.advance(hours=1)
        cust.tick()
        assert calls == [1]
        cust.record_drill({"ok": True, "backup": "x.db", "problems": []})
        clock.advance(days=29)
        cust.tick()
        assert calls == [1]
        clock.advance(days=2)
        cust.tick()
        assert calls == [1, 1]


# ── the pre-migration copy ───────────────────────────────────────────────────

def test_a_schema_upgrade_is_preceded_by_a_backup_that_is_never_pruned(
        tmp_path, monkeypatch):
    (tmp_path / "store").mkdir()
    path = str(tmp_path / "store" / "lem.db")
    s = LocalStoreGateway(path)
    kit.seed_machine(s)
    s.sql("UPDATE store_meta SET value = '0' WHERE key = 'schema_version'")
    s.close()
    monkeypatch.setenv("LEM_BACKUP_DIR", str(tmp_path / "bk"))
    s2 = LocalStoreGateway(path)
    s2.close()
    found = custody.list_backups(str(tmp_path / "bk"))
    assert [b["kind"] for b in found] == ["pre_migration"]
    man = custody.read_manifest(found[0]["path"])
    assert man["schema_version"] == "0"
    assert custody.verify(found[0]["path"])["ok"]


def test_the_default_backup_folder_is_beside_the_store_folder(tmp_path,
                                                             monkeypatch):
    monkeypatch.delenv("LEM_BACKUP_DIR", raising=False)
    assert custody.default_backup_dir(r"/srv/lem/store/lem.db") == \
        os.path.join("/srv/lem", "backup")
    # a store that is not in a `store` folder keeps its backups with it
    assert custody.default_backup_dir("/tmp/x/lem.db") == \
        os.path.join("/tmp/x", "backup")
