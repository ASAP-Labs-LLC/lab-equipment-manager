"""Moving LEM's record out of LabCore: the import tool (transfer §10.1, T-P9).

Server v4's first boot has to carry 33 `lem_*` tables out of LabCore into the
LEM store — 258k machine-log rows among them — through a queue that
serialises the whole lab and kills any read past 8 s. The tool is built around
four facts, each pinned here:

* **The heavy read is not needed.** The v3.9 server already keeps a full
  local copy of the log (its log mirror). The store is seeded from a COPY of
  that file — never the file in place, which a running v3.9 may still be
  writing — so 258k rows cost LabCore nothing.
* **A copy is proven, not trusted.** One GROUP BY read over the covering
  index compares every (range, machine, kind) count with the store, and one
  read of 1,000 sampled rows compares them field by field. A range that
  disagrees is re-read by rowid and repaired. Only then is the log "verified".
* **A failed read is never an empty result** (ASK-CLAUDE.md). A watchdog on
  chunk n leaves that table NOT done — never "done with what we had" — and the
  next run resumes from `import_run` and the persisted cursor.
* **Identity is content, never a rowid.** LabCore's rowids are a cursor: a
  VACUUM after rows were deleted renumbers them. Each imported row carries
  `legacy_key = 'lc:' + H(uid, ts, kind, lab_id, test_name, value, detail) +
  ':' + occurrence`, so re-importing a renumbered log adds 0 rows, and two
  byte-identical rows (v3.9's N3 double write) stay two rows.

Until the import is verified, v2 benches are answered 503 + Retry-After: a
bench must not start adding to a record that is still being moved.
"""
import json
import os
import shutil

import pytest

import legacy_kit as kit
from labcore_gateway import FakeLabCoreGateway


def make_store(tmp_path, name="lem.db"):
    from lem_store import LocalStoreGateway
    return LocalStoreGateway(str(tmp_path / name))


def importer(store, lab, **kw):
    import legacy_import
    kw.setdefault("sleep", lambda s: None)
    return legacy_import.Importer(store, lab, **kw)


def meta(store, key):
    rows = kit.store_rows(store, "SELECT value FROM store_meta WHERE key = ?",
                          [key])
    return rows[0]["value"] if rows else None


def import_row(store, table):
    rows = kit.store_rows(store, "SELECT * FROM import_run WHERE "
                                 "table_name = ?", [table])
    return rows[0] if rows else None


@pytest.fixture
def mirror(tmp_path):
    path = str(tmp_path / "log-mirror.sqlite3")
    shutil.copyfile(kit.MIRROR_FIXTURE, path)
    return path


# ── the happy road ──────────────────────────────────────────────────────────

class TestSeedFromTheMirror:
    def test_the_whole_log_arrives_from_the_copy_and_is_proven_in_two_reads(
            self, tmp_path, mirror):
        """The phase-1 mirror copy holds 385 rows. All 385 reach the store,
        each exactly once, and proving that costs LabCore two log reads (the
        GROUP BY and the sample) — not 385 rows' worth of queue time."""
        lab = kit.labcore_from_mirror(mirror)
        store = make_store(tmp_path)
        out = importer(store, lab, mirror_path=mirror).run()
        assert out["state"] == "verified", out
        log_reads = [s for k, s in lab.calls
                     if k == "read" and "FROM lem_machine_log" in s]
        assert len(log_reads) == 2, log_reads
        assert kit.lost_and_dup(kit.lc_multiset(lab),
                                kit.store_multiset(store)) == (0, 0)
        row = import_row(store, "lem_machine_log")
        assert (row["source_rows"], row["copied"], row["max_rowid"]) == \
            (385, 385, 385)
        assert row["verified"] and row["finished_at"]

    def test_every_legacy_row_carries_a_unique_content_key_and_its_ts(
            self, tmp_path, mirror):
        lab = kit.labcore_from_mirror(mirror)
        store = make_store(tmp_path)
        importer(store, lab, mirror_path=mirror).run()
        rows = kit.store_rows(
            store,
            # raw-log: the import's own rows, every one
            "SELECT legacy_key, legacy_rowid, origin, ts FROM lem_machine_log")
        keys = [r["legacy_key"] for r in rows]
        assert len(keys) == 385 and len(set(keys)) == 385
        assert all(k.startswith("lc:") for k in keys)
        assert {r["origin"] for r in rows} == {"legacy_labcore"}
        # ts is unchanged: the same meaning in the same column (§7).
        assert sorted(r["ts"] for r in rows) == sorted(
            v[1] for _rid, v in kit.mirror_rows(mirror))

    def test_the_mirror_is_read_from_a_copy_and_left_byte_identical(
            self, tmp_path, mirror):
        """§10.1 seeds from 'a copy of the log mirror'. Opening the live file
        would take a read lock a running v3.9 mirror pull then waits on —
        and a WAL file left beside it. The file is byte-for-byte as it was,
        and no -wal or -journal appears next to it."""
        with open(mirror, "rb") as f:
            before = f.read()
        lab = kit.labcore_from_mirror(mirror)
        store = make_store(tmp_path)
        importer(store, lab, mirror_path=mirror).run()
        with open(mirror, "rb") as f:
            assert f.read() == before
        assert not [p for p in os.listdir(tmp_path)
                    if p.startswith("log-mirror.sqlite3-")]

    def test_the_other_tables_arrive_with_their_counts_and_an_ordered_sha(
            self, tmp_path, mirror):
        """'The other 32 tables: one SELECT * each … counts and an ordered
        SHA-256 are verified.' Every lem_* table LabCore has is in import_run
        with source_rows == copied and a sha, and the store answers with
        LabCore's rows."""
        lab = kit.labcore_from_mirror(mirror)
        store = make_store(tmp_path)
        out = importer(store, lab, mirror_path=mirror).run()
        assert out["state"] == "verified", out
        tables = [r["name"] for r in lab.q(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name "
            "LIKE 'lem_%' AND name <> 'lem_machine_log'")]
        assert len(tables) == 12
        for t in tables:
            row = import_row(store, t)
            n = lab.q("SELECT COUNT(*) AS n FROM %s" % t)[0]["n"]
            assert row is not None, t
            assert (row["source_rows"], row["copied"]) == (n, n), (t, row)
            assert len(row["verified"]) == 64, (t, row)
        assert kit.store_rows(store, "SELECT COUNT(*) AS n FROM "
                                     "lem_checklist_state")[0]["n"] == 40
        cfg = kit.store_rows(store, "SELECT config FROM lem_machine_config "
                                    "WHERE machine_uid = 'pac-flash-1'")
        assert json.loads(cfg[0]["config"])["csv_path"] == "C:/data/flash.csv"

    def test_the_cost_is_counted_where_healthz_reads_it(self, tmp_path, mirror):
        """§10.1: 'measured by /healthz.store.import.reads'. Every LabCore read
        the import made, and nothing else: 2 log reads, 1 schema read, 12
        table reads."""
        lab = kit.labcore_from_mirror(mirror)
        store = make_store(tmp_path)
        importer(store, lab, mirror_path=mirror).run()
        assert lab.reads == 15
        import legacy_import
        assert legacy_import.status(store)["reads"] == 15
        assert legacy_import.status(store)["state"] == "verified"


# ── a copy that is wrong is repaired, not believed ──────────────────────────

class TestRepair:
    def test_a_mirror_with_a_hole_and_a_changed_row_is_repaired_by_range(
            self, tmp_path):
        """A mirror is a cache: it can be behind, and a disk can lie. Here it
        lacks rows 100–149 and holds row 300 with a different value. The
        GROUP BY finds the hole's range, the sample finds the changed row
        (all 385 are sampled), and only those two ranges are re-read."""
        rows = kit.mirror_rows()
        broken = [(rid, v) for rid, v in rows if not 100 <= rid < 150]
        broken = [(rid, (v[:5] + ("9.999",) + v[6:]) if rid == 300 else v)
                  for rid, v in broken]
        path = kit.make_mirror(str(tmp_path / "m.sqlite3"), broken)
        lab = kit.labcore_from_mirror(kit.MIRROR_FIXTURE)
        store = make_store(tmp_path)
        out = importer(store, lab, mirror_path=path, bucket=50).run()
        assert out["state"] == "verified", out
        repairs = [s for k, s in lab.calls
                   if k == "read" and "rowid >= ?" in s]
        assert len(repairs) == 2, repairs           # buckets 2 and 6
        truth = kit.lc_multiset(lab)
        stored = kit.store_multiset(store)
        # Nothing LabCore holds is missing, and nothing is doubled …
        lost, dup = kit.lost_and_dup(truth, stored)
        assert lost == 0
        # … except the mirror's wrong row, which is KEPT (the record is
        # append-only) and simply not counted as LabCore's row 300 any more.
        assert dup == 1
        assert sum(stored.values()) == 386

    def test_a_range_that_will_not_verify_is_never_called_verified(
            self, tmp_path, mirror):
        """If LabCore keeps changing under the import faster than repairs can
        follow — here every repair read is refused — the log is not
        verified, the reason is said, and syncs stay held."""
        rows = [(rid, v) for rid, v in kit.mirror_rows() if rid != 7]
        path = kit.make_mirror(str(tmp_path / "m.sqlite3"), rows)
        lab = kit.labcore_from_mirror(kit.MIRROR_FIXTURE)
        lab.fail_on("rowid >= ?", nth=1, times=99)
        store = make_store(tmp_path)
        out = importer(store, lab, mirror_path=path, bucket=50).run()
        assert out["state"] != "verified"
        assert import_row(store, "lem_machine_log")["verified"] is None
        assert meta(store, "sync_hold")
        assert any("lem_machine_log" in p for p in out["problems"]), out


# ── S5: a failed chunk is never "done" ──────────────────────────────────────

class TestS5:
    def test_a_watchdog_on_chunk_n_never_marks_the_log_done_and_it_resumes(
            self, tmp_path):
        """No mirror: the log comes over in chunks. The watchdog kills chunk
        3 of 4. The table is NOT done (import_run.verified stays NULL, the
        state is not 'verified', syncs stay held); the rows of chunks 1–2
        are kept; the next run resumes at chunk 3 and does not re-read 1–2.
        Range counts then match LabCore's exactly."""
        lab = kit.labcore_from_mirror(kit.MIRROR_FIXTURE)
        store = make_store(tmp_path)
        lab.fail_on("rowid > ?", nth=3)
        first = importer(store, lab, chunk=100).run()
        assert first["state"] != "verified"
        assert import_row(store, "lem_machine_log") is None or \
            import_row(store, "lem_machine_log")["verified"] is None
        assert meta(store, "sync_hold")
        held = sum(kit.store_multiset(store).values())
        assert held == 200
        mark = len(lab.calls)
        second = importer(store, lab, chunk=100).run()
        assert second["state"] == "verified", second
        chunk_reads = [s for k, s in lab.calls[mark:] if "rowid > ?" in s]
        assert len(chunk_reads) == 2           # chunks 3 and 4, not 1 and 2
        assert kit.lost_and_dup(kit.lc_multiset(lab),
                                kit.store_multiset(store)) == (0, 0)
        # range counts: LabCore's per 100-rowid range against the store's
        lc = {r["b"]: r["n"] for r in lab.q(
            "SELECT rowid / 100 AS b, COUNT(*) AS n FROM lem_machine_log "
            "GROUP BY b")}
        st = {r["b"]: r["n"] for r in kit.store_rows(
            store,
            # raw-log: every imported row, by the LabCore rowid it came from
            "SELECT legacy_rowid / 100 AS b, COUNT(*) AS n FROM "
            "lem_machine_log WHERE origin = 'legacy_labcore' GROUP BY b")}
        assert lc == st, (lc, st)
        assert not meta(store, "sync_hold")

    def test_a_watchdog_on_one_tables_read_leaves_only_that_table_undone(
            self, tmp_path, mirror):
        lab = kit.labcore_from_mirror(mirror)
        lab.fail_on("FROM lem_checklist_state")
        store = make_store(tmp_path)
        first = importer(store, lab, mirror_path=mirror).run()
        assert first["state"] != "verified"
        assert import_row(store, "lem_checklist_state") is None or \
            import_row(store, "lem_checklist_state")["verified"] is None
        assert import_row(store, "lem_qc_samples")["verified"]
        assert any("lem_checklist_state" in p for p in first["problems"])
        mark = len(lab.calls)
        second = importer(store, lab, mirror_path=mirror).run()
        assert second["state"] == "verified", second
        again = [s for _k, s in lab.calls[mark:]]
        # Only the undone table is read again; the log is not re-verified.
        assert sum("FROM lem_checklist_state" in s for s in again) == 1
        assert not any("FROM lem_qc_samples" in s for s in again)
        assert not any("FROM lem_machine_log" in s for s in again)
        assert import_row(store, "lem_checklist_state")["copied"] == 40

    def test_a_refused_read_is_not_an_empty_table(self, tmp_path, mirror):
        """A table LabCore really has empty is imported as 0 rows and
        verified. The same table answering an error is NOT: 'no rows' and
        'could not ask' are different sentences."""
        lab = kit.labcore_from_mirror(mirror)
        lab.x("CREATE TABLE lem_lab_holidays (day TEXT PRIMARY KEY, name TEXT)")
        store = make_store(tmp_path)
        importer(store, lab, mirror_path=mirror).run()
        assert import_row(store, "lem_lab_holidays")["source_rows"] == 0
        assert import_row(store, "lem_lab_holidays")["verified"]

        lab2 = kit.labcore_from_mirror(mirror)
        lab2.x("CREATE TABLE lem_lab_holidays (day TEXT PRIMARY KEY, name TEXT)")
        lab2.fail_on("FROM lem_lab_holidays",
                     answer={"error": "database is locked"})
        store2 = make_store(tmp_path, "two.db")
        out = importer(store2, lab2, mirror_path=mirror).run()
        assert out["state"] != "verified"
        row = import_row(store2, "lem_lab_holidays")
        assert row is None or row["verified"] is None

    def test_busy_is_waited_out_as_labcore_asks(self, tmp_path, mirror):
        """A busy refusal carries `retry_after`; the tool waits that long and
        asks again rather than failing the table."""
        waits = []
        lab = kit.labcore_from_mirror(mirror)
        lab.fail_on("FROM lem_qc_samples", answer=kit.BUSY)
        store = make_store(tmp_path)
        out = importer(store, lab, mirror_path=mirror,
                       sleep=waits.append).run()
        assert out["state"] == "verified", out
        assert 5 in waits


# ── rows the store already had ──────────────────────────────────────────────

class TestStoreRowsAreNotOverwritten:
    def test_a_correction_saved_on_v4_before_the_import_finished_survives(
            self, tmp_path, mirror):
        """The server serves pages while the import runs. A supervisor who
        saved a correction factor in those minutes saved it to the store;
        LabCore's older copy must not silently replace it. The store keeps
        its row (and one LabCore never had), the table still verifies, and
        the bridge then sends both to LabCore rather than treating them as
        already there."""
        import bridge
        from snapshot_service import SCHEMA_DDL
        store = make_store(tmp_path)
        for ddl in SCHEMA_DDL:
            if "lem_correction_factors" in ddl.split("(")[0]:
                store.sql(ddl)
        store.sql("INSERT INTO lem_correction_factors (machine_uid, test_name, "
                  "correction, units, updated_at, updated_by) VALUES "
                  "('pac-flash-1', 'Flash Point', 0.9, 'C', "
                  "'2026-10-02T08:00:00', 'kaden')")
        store.sql("INSERT INTO lem_correction_factors (machine_uid, test_name, "
                  "correction, units, updated_at, updated_by) VALUES "
                  "('gc-2', 'Sulfur', 0.01, 'ppm', '2026-10-02T08:01:00', "
                  "'kaden')")
        lab = kit.labcore_from_mirror(mirror)
        assert importer(store, lab, mirror_path=mirror).run()["state"] == \
            "verified"
        got = {(r["machine_uid"], r["test_name"]): r["correction"]
               for r in kit.store_rows(store, "SELECT * FROM "
                                              "lem_correction_factors")}
        assert got == {("pac-flash-1", "Flash Point"): 0.9,
                       ("gc-2", "Sulfur"): 0.01}
        assert "1 newer, 1 not in LabCore" in import_row(
            store, "lem_correction_factors")["verified"]
        br = bridge.Bridge(store, lab, clock=lambda: 0.0)
        assert br.project()["queued"] == 2
        br.drain(now=0.0)
        assert {(r["machine_uid"], r["test_name"]): r["correction"]
                for r in lab.q("SELECT * FROM lem_correction_factors")} == got


# ── identity is content ─────────────────────────────────────────────────────

class TestContentIdentity:
    def test_a_reimport_after_vacuum_renumbering_adds_0_rows(self, tmp_path,
                                                            mirror):
        """LabCore's rowids are a cursor, never an identity. Delete 20 rows
        (v3.9's 'purge history' did) and VACUUM: every surviving row moves to
        a new rowid. Importing again from scratch adds nothing."""
        lab = kit.labcore_from_mirror(mirror)
        store = make_store(tmp_path)
        assert importer(store, lab, mirror_path=mirror).run()["state"] == \
            "verified"
        before = sum(kit.store_multiset(store, effective=False).values())
        kit.vacuum_renumber(lab, delete_rowids=range(30, 50))
        assert lab.q("SELECT MAX(rowid) AS m FROM lem_machine_log")[0]["m"] \
            == 365
        out = importer(store, lab, chunk=100).run(reimport=True)
        assert out["state"] == "verified", out
        after = sum(kit.store_multiset(store, effective=False).values())
        assert after - before == 0

    def test_two_identical_rows_are_two_rows_and_stay_two(self, tmp_path):
        """v3.9's N3: a write that landed and whose answer was lost is sent
        again — two byte-identical rows. They are two rows of LabCore's
        record and stay two in the store (occurrence 0 and 1), however often
        the log is imported."""
        row = ("gc-2", "2026-09-30T10:00:00.5", "run", "L-1", "", "",
               '{"values": {"S": "0.1"}}')
        lab = kit.LabCore()
        kit.load_log(lab, [(1, row), (2, row), (3, row[:3] + ("L-2",)
                                                + row[4:])])
        store = make_store(tmp_path)
        assert importer(store, lab, chunk=2).run()["state"] == "verified"
        keys = sorted(r["legacy_key"] for r in kit.store_rows(
            store, "SELECT legacy_key FROM lem_machine_log"))  # raw-log: test
        assert len(keys) == 3
        assert keys[0].rsplit(":", 1)[0] == keys[1].rsplit(":", 1)[0] or \
            keys[1].rsplit(":", 1)[0] == keys[2].rsplit(":", 1)[0]
        importer(store, lab, chunk=2).run(reimport=True)
        assert len(kit.store_rows(
            store, "SELECT id FROM lem_machine_log")) == 3   # raw-log: test

    def test_the_key_recipe_is_pinned(self):
        """`legacy_key` is a recipe two servers must agree on (§13: a new
        recipe needs a new prefix). Pinned to its bytes."""
        import legacy_import
        h = legacy_import.content_hash("gc-2", "2026-09-30T10:00:00", "run",
                                       "L-1", "", "", "{}")
        assert legacy_import.legacy_key(h, 0) == "lc:%s:0" % h
        assert h == legacy_import.content_hash(
            "gc-2", "2026-09-30T10:00:00", "run", "L-1", "", "", "{}")
        assert len(h) == 32
        assert h != legacy_import.content_hash(
            "gc-2", "2026-09-30T10:00:00", "run", "L-1", "", None, "{}")


# ── 503 until verified ──────────────────────────────────────────────────────

class TestSyncWaitsForTheImport:
    def test_v2_syncs_are_answered_503_until_the_log_is_verified(
            self, tmp_path, mirror):
        """§10.1: until verified, sync answers 503 with Retry-After and
        benches hold. The hold goes as soon as the import is verified."""
        import bench_v2_kit as bk
        store = make_store(tmp_path)
        bk.seed_machine(store)
        lab = kit.labcore_from_mirror(mirror)
        app = bk.make_app(store, labcore=lab)
        client = app.test_client()
        token = bk.enroll(client)
        bench = bk.Bench(client, token).journal(2)

        import legacy_import
        legacy_import.hold_until_verified(store)
        r = bench.sync()
        assert r.status_code == 503
        assert r.headers.get("Retry-After")
        assert "import" in r.get_json()["error"].lower()

        lab.fail_on("FROM lem_meta")
        assert importer(store, lab, mirror_path=mirror).run()["state"] != \
            "verified"
        assert bench.sync().status_code == 503

        assert importer(store, lab, mirror_path=mirror).run()["state"] == \
            "verified"
        r = bench.sync()
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["acked"] == 2

    def test_adoption_and_checkpoint_wait_for_the_import_too(self, tmp_path,
                                                            mirror):
        """A bench's first v4 start matches its file against `/adoption`,
        and a bench whose journal is gone asks `/checkpoint`. Answered from
        a half-imported record, both would be wrong in the dangerous
        direction: lines LabCore holds would read as never recorded. So they
        hold exactly as sync does."""
        import bench_v2_kit as bk
        import legacy_import
        store = make_store(tmp_path)
        bk.seed_machine(store)
        lab = kit.labcore_from_mirror(mirror)
        client = bk.make_app(store, labcore=lab).test_client()
        token = bk.enroll(client)
        legacy_import.hold_until_verified(store)
        hdr = {"X-LEM-Bench-Token": token}
        for path in ("adoption", "checkpoint"):
            r = client.get("/api/v2/bench/%s/%s" % (bk.UID, path), headers=hdr)
            assert r.status_code == 503, (path, r.status_code)
            assert r.headers.get("Retry-After")
        importer(store, lab, mirror_path=mirror).run()
        for path in ("adoption", "checkpoint"):
            r = client.get("/api/v2/bench/%s/%s" % (bk.UID, path), headers=hdr)
            assert r.status_code == 200, (path, r.get_json())

    def test_healthz_reports_the_import_and_its_reads(self, tmp_path, mirror):
        import bench_v2_kit as bk
        store = make_store(tmp_path)
        lab = kit.labcore_from_mirror(mirror)
        app = bk.make_app(store, labcore=lab)
        client = app.test_client()
        h = client.get("/healthz").get_json()
        assert h["store"]["import"]["state"] == "not started"
        assert h["store"]["import"]["reads"] == 0
        importer(store, lab, mirror_path=mirror).run()
        h = client.get("/healthz").get_json()
        assert h["store"]["import"]["state"] == "verified"
        assert h["store"]["import"]["reads"] == 15
        assert h["store"]["import"]["tables_verified"] == 13


# ── the production estimate ─────────────────────────────────────────────────

class TestProductionCost:
    def test_production_sized_import_costs_at_most_70_reads(self, tmp_path):
        """§10.1 predicts 40–70 `read_sql`, once. Measured on a LabCore the
        size of production's on 2026-10-01 (census.json): 257,996 log rows
        and 32 other lem_* tables, the largest 4,774 rows — with a mirror
        that is 5 minutes behind (the last 2,000 rows missing, which the
        bridge's first pull then fetches) and has one corrupt 5,000-row range
        (repaired). The count is read off `/healthz.store.import.reads`'s
        source, `legacy_import.status`."""
        import legacy_import
        rows = kit.synthetic_rows(257996)
        lab = kit.LabCore()
        kit.load_log(lab, rows)
        for i in range(32):
            name = "lem_t%02d" % i
            lab.x("CREATE TABLE %s (k TEXT PRIMARY KEY, v TEXT)" % name)
            n = 4774 if i == 0 else (i * 3) % 23
            for j in range(n):
                lab.x("INSERT INTO %s VALUES (?, ?)" % name, ["k%d" % j, "v"])
        mrows = [(rid, v) for rid, v in rows[:-2000]
                 if not 120000 <= rid < 120040]
        path = kit.make_mirror(str(tmp_path / "m.sqlite3"), mrows)
        store = make_store(tmp_path)
        out = importer(store, lab, mirror_path=path).run()
        assert out["state"] == "verified", out
        reads = legacy_import.status(store)["reads"]
        print("production-sized import: %d LabCore reads" % reads)
        assert reads <= 70
        assert reads == lab.reads
        assert kit.lost_and_dup(
            kit.Counter(v for _r, v in rows[:-2000]),
            kit.store_multiset(store)) == (0, 0)
        # The 2,000 rows the mirror was behind: the bridge's first pull.
        import bridge
        out = bridge.Bridge(store, lab, clock=lambda: 0.0).pull()
        assert out["added"] == 2000, out      # N3 doubles included: 2 rows
        assert lab.reads == reads + 1
        print("…plus the bridge's first pull: %d reads in all" % lab.reads)
        assert kit.lost_and_dup(kit.Counter(v for _r, v in rows),
                                kit.store_multiset(store)) == (0, 0)


# ── the server's boot ───────────────────────────────────────────────────────

class TestBoot:
    def test_a_live_boot_holds_syncs_and_imports_only_when_asked(
            self, tmp_path, mirror):
        """Boot (never create_app) decides. A live store whose import is not
        verified is held; nothing reads LabCore until --import-from-mirror
        names a mirror; then the import runs on a thread and the hold goes
        when it is verified. A candidate boot (read-only store) does
        nothing."""
        import time
        import bench_v2_kit as bk
        import web_server
        from lem_store import LocalStoreGateway
        store = make_store(tmp_path)
        lab = kit.labcore_from_mirror(mirror)
        app = bk.make_app(store, labcore=lab)
        out = web_server.start_transfer(app, store, dev=False)
        try:
            assert out == {"held": True, "importing": False, "bridge": True}
            assert meta(store, "sync_hold").startswith("importing")
            time.sleep(1.5)                      # the bridge thread is idle
            assert lab.calls == []
        finally:
            app.config["BRIDGE"].stop()

        app2 = bk.make_app(store, labcore=lab)
        out = web_server.start_transfer(app2, store, dev=False,
                                        import_mirror=mirror, retry_s=0.1)
        try:
            assert out["importing"] is True
            for _ in range(200):
                if meta(store, "import_state") == "verified":
                    break
                time.sleep(0.05)
            assert meta(store, "import_state") == "verified"
            for _ in range(100):
                if not meta(store, "sync_hold"):
                    break
                time.sleep(0.05)
            assert not meta(store, "sync_hold")
        finally:
            app2.config["BRIDGE"].stop()
            app2.config["IMPORT_SERVICE"].stop()

        ro = LocalStoreGateway(str(tmp_path / "lem.db"), read_only=True)
        app3 = bk.make_app(ro, labcore=kit.LabCore())
        assert web_server.start_transfer(app3, ro, dev=False,
                                         import_mirror=mirror) == {
            "held": False, "importing": False, "bridge": False}

    def test_the_command_line_refuses_production_without_saying_so(
            self, capsys):
        import legacy_import
        assert legacy_import.main(["--store", "/nonexistent/x.db"]) == 2
        assert "Ryan" in capsys.readouterr().out
