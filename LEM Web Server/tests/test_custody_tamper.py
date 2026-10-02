"""A tampered backup must not restore (transfer spec §11, piece T-P11, round 2).

The first manifest checked the log day digests, the annotation digest, every
table's row COUNT, and each bench epoch's digest over `bench_record.body`. That
left the one row that decides what a bench resends unguarded: `bench_cursor`.
Raise `acked_seq` in a backup from 25 to 30 and the file still verified, still
restored, still passed the drill — and then the restored server answered the
bench's sync with 409 `acked = 30`, the bench adopted it (that is the rule, N3),
and seqs 26..30 were never sent again. Five bench records gone, in exactly the
case the manifest exists to catch. RPO 0 is a promise about that cursor.

So the manifest now pins three more things, and the restore asks one more
question:

* **every row of every table**, all columns, by a per-table SHA-256 — a bench
  record's `kind`, the cursor's `durable_seq`, a checklist someone typed, a
  user row: none of them is "checked by count only" any more;
* **the schema**: a dropped guard trigger, or one added, is a different file;
* **each epoch's own consistency**: the records LEM holds must be exactly
  1..acked and reproduce LEM's running digest. LEM writes the records and the
  cursor in ONE transaction, so a store where they disagree was not written by
  LEM — and restoring it is how records get lost. This holds even when the
  attacker recomputes the whole manifest to match;
* **a witness from outside the file**: the live store keeps a ledger of every
  backup's manifest digest, and the off-host folder keeps its own copy of each
  manifest it received. Someone who rewrites the backup AND its manifest
  together is caught by whichever witness exists, and a restore that nothing
  can witness is refused unless a person says, in so many words, to accept it.

`durable` follows the same rule: it is never published for an epoch whose
copy does not hold 1..acked intact, because a bench deletes its own copy on
the strength of that number.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone

import pytest

import bench_v2_kit as kit
import custody
from bench_v2_kit import Bench
from lem_store import LocalStoreGateway

T0 = datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc)


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
def cust(store, where):
    c = custody.Custody(store, backup_dir=where["backup"],
                        offsite_dir=where["offsite"], clock=lambda: T0)
    c.hydrate()
    return c


@pytest.fixture
def client(store, cust):
    a = kit.make_app(store)
    custody.attach(a, cust)
    return a.test_client()


@pytest.fixture
def backup(store, cust, client):
    """25 bench records, a typed comment, and a checked backup of it all."""
    bench = Bench(client, kit.enroll(client))
    bench.journal(25).sync()
    res = store.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, "
                    "lab_id, test_name, value, detail) VALUES (?, "
                    "'2026-10-01T10:00:00', 'comment', '', '', 'hello', '{}')",
                    [kit.UID])
    assert "error" not in res, res
    out = cust.backup_now()
    assert out["ok"], out
    assert custody.verify(out["path"])["ok"]
    return dict(out, bench=bench)


def _copy(src, folder):
    os.makedirs(folder, exist_ok=True)
    dst = os.path.join(folder, os.path.basename(src))
    shutil.copyfile(src, dst)
    shutil.copyfile(custody.manifest_path(src), custody.manifest_path(dst))
    return dst


def _tamper(path, fn):
    """Drop every trigger, change the file, put the triggers back exactly as
    they were, and re-stamp the manifest's file hash — so the only thing that
    can still see the change is the manifest's CONTENT."""
    con = sqlite3.connect(path)
    try:
        trig = con.execute("SELECT name, sql FROM sqlite_master WHERE "
                           "type = 'trigger'").fetchall()
        for name, _sql in trig:
            con.execute('DROP TRIGGER "%s"' % name)
        fn(con)
        con.commit()
        for _name, sql in trig:
            con.execute(sql)
        con.commit()
    finally:
        con.close()
    man = custody.read_manifest(path)
    man["sha256"] = custody.file_sha256(path)
    _write(path, man)


def _restamp_everything(path):
    """The stronger attacker: recompute the WHOLE manifest from the tampered
    file with custody's own code, so the file and its manifest agree."""
    man = custody.read_manifest(path)
    con = sqlite3.connect(path)
    try:
        man.update(custody.describe(con))
    finally:
        con.close()
    man["sha256"] = custody.file_sha256(path)
    man["bytes"] = os.path.getsize(path)
    _write(path, man)


def _write(path, man):
    with open(custody.manifest_path(path), "w", encoding="utf-8") as f:
        json.dump(man, f)


def _changed(value):
    if value is None:
        return "tampered"
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value + 0.5
    if isinstance(value, bytes):
        return value + b"x"
    return str(value) + "x"


# ── every row, every column ──────────────────────────────────────────────────

def _cells(path):
    """(table, column, rowid) for one row of every non-empty table, every
    column of it."""
    con = sqlite3.connect(path)
    try:
        out = []
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type = "
                                "'table' AND name NOT LIKE 'sqlite_%' "
                                "ORDER BY name").fetchall():
            row = con.execute('SELECT rowid FROM "%s" ORDER BY rowid LIMIT 1'
                              % t).fetchone()
            if row is None:
                continue
            for c in con.execute('PRAGMA table_info("%s")' % t).fetchall():
                out.append((t, c[1], row[0]))
        return out
    finally:
        con.close()


def test_a_change_to_any_cell_of_any_table_fails_reverification(
        backup, tmp_path):
    """Not a list of the columns someone thought of: every column of a row of
    every table that has one. A cell the manifest does not pin is a cell an
    attacker (or a bad disk) can change without anybody knowing."""
    cells = _cells(backup["path"])
    tables = {t for t, _c, _r in cells}
    # the ones the critic named, so this cannot pass by being empty
    assert {"bench_cursor", "bench_record", "bench_token", "lem_machine_config",
            "lem_machine_status", "lem_machine_log", "store_meta"} <= tables
    missed, tried = [], 0
    for i, (t, col, rowid) in enumerate(cells):
        bad = _copy(backup["path"], str(tmp_path / ("c%d" % i)))

        def edit(con, t=t, col=col, rowid=rowid):
            old = con.execute('SELECT "%s" FROM "%s" WHERE rowid = ?'
                              % (col, t), [rowid]).fetchone()[0]
            con.execute('UPDATE "%s" SET "%s" = ? WHERE rowid = ?'
                        % (t, col), [_changed(old), rowid])
        try:
            _tamper(bad, edit)
        except sqlite3.IntegrityError:
            continue                       # the schema itself refuses it
        tried += 1
        v = custody.verify(bad)
        if v["ok"] or not any(t in p for p in v["problems"]):
            missed.append("%s.%s" % (t, col))
        shutil.rmtree(os.path.dirname(bad))
    assert tried >= 40, tried
    assert missed == [], "changed and still verified: %s" % missed


def test_the_cursor_raised_from_25_to_30_fails_and_names_the_epoch(
        backup, tmp_path):
    bad = _copy(backup["path"], str(tmp_path / "bad"))
    _tamper(bad, lambda con: con.execute(
        "UPDATE bench_cursor SET acked_seq = 30"))
    v = custody.verify(bad)
    assert v["ok"] is False
    assert any("bench_cursor" in p for p in v["problems"]), v["problems"]
    assert any(kit.UID in p and "ep-1" in p for p in v["problems"]), \
        v["problems"]
    # and it says WHAT changed, in the epoch's own terms
    assert any("acked 30, the manifest says 25" in p for p in v["problems"]), \
        v["problems"]


def test_a_cursor_that_outruns_its_records_fails_even_with_a_matching_manifest(
        backup, tmp_path):
    """The attacker recomputes the whole manifest with custody's own code.
    The file and the manifest agree — but LEM writes a record and its cursor in
    one transaction, so a cursor at 30 over 25 records is a file LEM never
    wrote, and restoring it is exactly how 26..30 would be lost."""
    bad = _copy(backup["path"], str(tmp_path / "bad"))
    _tamper(bad, lambda con: con.execute(
        "UPDATE bench_cursor SET acked_seq = 30"))
    _restamp_everything(bad)
    v = custody.verify(bad)
    assert v["ok"] is False
    assert any("1..30" in p for p in v["problems"]), v["problems"]


def test_a_dropped_guard_trigger_fails_reverification(backup, tmp_path):
    bad = _copy(backup["path"], str(tmp_path / "bad"))
    con = sqlite3.connect(bad)
    con.execute("DROP TRIGGER bench_record_no_update")
    con.commit()
    con.close()
    man = custody.read_manifest(bad)
    man["sha256"] = custody.file_sha256(bad)
    _write(bad, man)
    v = custody.verify(bad)
    assert v["ok"] is False
    assert any("schema" in p for p in v["problems"]), v["problems"]


def test_an_old_format_manifest_is_refused_not_half_checked(backup, tmp_path):
    bad = _copy(backup["path"], str(tmp_path / "bad"))
    man = custody.read_manifest(bad)
    man["format"] = "lem-backup/1"
    _write(bad, man)
    v = custody.verify(bad)
    assert v["ok"] is False and "manifest" in v["problems"][0]


# ── witnesses outside the file ───────────────────────────────────────────────

def _consistent_rewrite(path):
    """Change a reading and recompute everything: verify() alone cannot see
    it, by construction. Only something outside the file can."""
    _tamper(path, lambda con: con.execute(
        "UPDATE lem_machine_log SET value = '99.9' WHERE id = 3"))
    _restamp_everything(path)
    assert custody.verify(path)["ok"] is True


def test_every_backup_is_entered_in_the_live_stores_ledger(store, backup):
    res = store.read_sql("SELECT value FROM store_meta WHERE key = "
                         "'backup_ledger'")
    ledger = json.loads(res["rows"][0]["value"])
    name = os.path.basename(backup["path"])
    assert ledger[name] == custody.manifest_digest(
        custody.read_manifest(backup["path"]))


def test_a_rewritten_backup_and_manifest_are_refused_by_the_stores_ledger(
        store, backup, where):
    _consistent_rewrite(backup["path"])
    store.close()
    before = custody.file_sha256(where["store"])
    with pytest.raises(custody.RestoreRefused) as exc:
        custody.restore(backup["path"], where["store"], now=T0)
    assert "ledger" in str(exc.value)
    assert custody.file_sha256(where["store"]) == before


def test_with_the_store_lost_the_off_host_manifest_is_the_witness(
        store, cust, backup, where):
    assert cust.offsite_now()["ok"]
    pristine = _copy(backup["path"], str(where["tmp"] / "pristine"))
    _consistent_rewrite(backup["path"])
    store.close()
    os.remove(where["store"])                     # the disk died
    with pytest.raises(custody.RestoreRefused) as exc:
        custody.restore(backup["path"], where["store"], now=T0,
                        offsite_dir=where["offsite"])
    assert "off-host" in str(exc.value)
    assert not os.path.exists(where["store"])
    # accepting "unwitnessed" does not override a witness that DISAGREES
    with pytest.raises(custody.RestoreRefused):
        custody.restore(backup["path"], where["store"], now=T0,
                        offsite_dir=where["offsite"], accept_unwitnessed=True)
    # and an untouched copy restores, witnessed by the off-host manifest
    done = custody.restore(pristine, where["store"], now=T0,
                           offsite_dir=where["offsite"])
    assert done["ok"]
    assert any("off-host" in w for w in done["witnesses"]), done


def test_a_restore_nothing_can_witness_is_refused_unless_accepted(
        store, backup, where):
    store.close()
    os.remove(where["store"])
    with pytest.raises(custody.RestoreRefused) as exc:
        custody.restore(backup["path"], where["store"], now=T0)
    assert "witness" in str(exc.value)
    done = custody.restore(backup["path"], where["store"], now=T0,
                           accept_unwitnessed=True)
    assert done["ok"] and done["witnesses"] == []
    assert done["unwitnessed"] is True


def test_an_honest_restore_names_its_witness(store, backup, where):
    store.close()
    done = custody.restore(backup["path"], where["store"], now=T0)
    assert done["ok"]
    assert any("ledger" in w for w in done["witnesses"]), done


def test_the_ledger_keeps_every_backup_not_just_the_newest(store, cust, backup,
                                                         where, client):
    """Each backup ADDS to the ledger (after a restore the store's ledger is
    the backup's older one, and the next backup must extend it, not reset
    it), so every retained backup stays witnessed."""
    second = cust.backup_now()
    names = {os.path.basename(backup["path"]), os.path.basename(second["path"])}
    res = store.read_sql("SELECT value FROM store_meta WHERE key = "
                         "'backup_ledger'")
    assert names <= set(json.loads(res["rows"][0]["value"]))


# ── durable is only what the copy holds intact ───────────────────────────────

def test_durable_is_not_published_for_an_epoch_the_copy_does_not_hold_whole(
        backup, tmp_path):
    bad = _copy(backup["path"], str(tmp_path / "bad"))
    _tamper(bad, lambda con: con.execute(
        "UPDATE bench_cursor SET acked_seq = 30"))
    con = sqlite3.connect(bad)
    try:
        desc = custody.describe(con)
    finally:
        con.close()
    assert desc["durable"] == []
    assert desc["epochs"][0]["intact"] is False


def test_a_ledger_that_cannot_be_read_is_not_reset_and_durable_waits(
        store, cust, backup, client, monkeypatch):
    """A failed read is never an empty result: writing "{} plus this backup"
    over a ledger that could not be read would erase every earlier backup's
    witness. The backup stays good, nothing is published from it, and the
    global status says why."""
    before = store.read_sql("SELECT value FROM store_meta WHERE key = "
                            "'backup_ledger'")["rows"][0]["value"]
    durable_before = store.read_sql("SELECT durable_seq FROM bench_cursor"
                                    )["rows"][0]["durable_seq"]
    backup["bench"].journal(3).sync()             # 28 acked, 25 durable
    real = store.read_sql

    def failing(sql, args=None):
        if "store_meta" in sql and "?" in sql and args == ["backup_ledger"]:
            return {"error": "disk I/O error"}
        return real(sql, args) if args is not None else real(sql)
    monkeypatch.setattr(store, "read_sql", failing)
    out = cust.backup_now()
    monkeypatch.undo()
    assert out["ok"] and out["durable"]           # the copy itself is fine
    assert store.read_sql("SELECT value FROM store_meta WHERE key = "
                          "'backup_ledger'")["rows"][0]["value"] == before
    rows = store.read_sql("SELECT bench_epoch, durable_seq FROM bench_cursor"
                          )["rows"]
    assert durable_before == 25
    assert {r["bench_epoch"]: r["durable_seq"] for r in rows} == {"ep-1": 25}
    msgs = [i["message"] for i in cust.status_items()]
    assert any("ledger" in m and "disk I/O error" in m for m in msgs), msgs


def test_the_command_line_restore_refuses_unwitnessed_and_says_who_witnessed(
        store, backup, where, capsys, monkeypatch):
    """The restore a person runs by hand (server stopped) applies the same
    rule and prints it: refused with nothing to witness, accepted only with
    --accept-unwitnessed, and an ordinary restore names its witness."""
    monkeypatch.delenv("LEM_BACKUP_OFFSITE", raising=False)
    fresh = str(where["tmp"] / "elsewhere" / "lem.db")
    os.makedirs(os.path.dirname(fresh))
    assert custody.main(["restore", backup["path"], "--store", fresh]) == 1
    assert "REFUSED" in capsys.readouterr().out
    assert not os.path.exists(fresh)
    assert custody.main(["restore", backup["path"], "--store", fresh,
                         "--accept-unwitnessed"]) == 0
    assert "NOTHING (accepted unwitnessed)" in capsys.readouterr().out
    store.close()
    assert custody.main(["restore", backup["path"], "--store",
                         where["store"]]) == 0
    assert "backup ledger" in capsys.readouterr().out
