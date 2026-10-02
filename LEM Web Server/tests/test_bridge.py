"""The mixed fleet: server v4 while v3.9 benches are still on the floor
(transfer §10.4, §12.2; piece T-P9).

A v3.9 bench writes its readings and state into LabCore and reads its
configuration from LabCore; it has never heard of the LEM store. The bridge
carries both directions, and each test here pins one promise it makes, with
the number it is held to:

* **W4** — a pull killed on chunk 2 resumes to 250/250, each row once
  (unique on `legacy_key`).
* **W1** — with the bridge on, LabCore sees 1 read per 12 s (the state arms)
  plus 1 per 60 s (the log), and ONE watchdog kill costs **+0** reads. Today a
  kill costs +426: v3.9's snapshot falls back to 18 single-arm reads per
  refresh for 25 refreshes. Here a failed read is simply retried next cycle.
* **M2** (v4 server, v3.9 bench) and **M6** (v3.9 → v4 server, v4 bench in
  legacy projection) — 0 lost and 0 effective duplicates across the
  hand-over: the real v3.9.0 module's own INSERTs, a watchdog, an N3 double
  write, a replay burst and a server restart in the middle of it.
* **DG1** — a bench rolled back from v4 to the v3.9 module restarts from the
  offset the bridge mirrored, and replays at most 15 minutes of prints. The
  restart is the real v3.9.0 code (`build_config_fetch`,
  `machine_from_config_payload`, `tail_new_text`). Today it replays from the
  offset last saved by hand on 09-23/09-24.

Plus what keeps those numbers honest: state arms only for legacy-mode uids,
no LabCore op at all with the bridge off or the import unverified (S4), a
v3.9 replay burst arriving with a VISIBLE `replay_candidate` (never hidden),
and projection that never rewinds a v3.9 bench's cursor.
"""
import json
import os
from collections import Counter

import pytest

import bench_v2_kit as bk
import legacy_kit as kit


def make_store(tmp_path, name="lem.db"):
    from lem_store import LocalStoreGateway
    return LocalStoreGateway(str(tmp_path / name))


def verified_store(tmp_path, lab, name="lem.db"):
    """A store into which `lab`'s tables have been imported and verified."""
    import legacy_import
    store = make_store(tmp_path, name)
    out = legacy_import.Importer(store, lab, sleep=lambda s: None,
                                 chunk=1000).run()
    assert out["state"] == "verified", out
    return store


def bridge_for(store, lab, **kw):
    import bridge
    kw.setdefault("clock", lambda: 0.0)
    return bridge.Bridge(store, lab, **kw)


def empty_labcore():
    lab = kit.LabCore()
    kit.declare_log(lab)
    kit.seed_v39_tables(lab, checklist_rows=2)
    return lab


def v39_rows(n, uid="gc-2", start=0, ts_base="2026-10-02T09:"):
    """n v3.9-shaped run rows."""
    return [(uid, "%s%02d:%02d.%06d" % (ts_base, (start + i) // 60 % 60,
                                        (start + i) % 60, start + i),
             "run", "L-%05d" % (start + i), "", "",
             json.dumps({"values": {"Sulfur": "%.4f" % ((start + i) * 1e-3)}}))
            for i in range(n)]


def legacy_keys(store):
    return [r["legacy_key"] for r in kit.store_rows(
        store, "SELECT legacy_key FROM lem_machine_log "  # raw-log: test
               "WHERE legacy_key IS NOT NULL")]


# ── W4 ──────────────────────────────────────────────────────────────────────

class TestW4:
    def test_a_pull_killed_on_chunk_2_resumes_to_250_of_250_each_once(
            self, tmp_path):
        lab = empty_labcore()
        store = verified_store(tmp_path, lab)
        for vals in v39_rows(250):
            kit.append_log(lab, vals)
        br = bridge_for(store, lab, chunk=100)
        assert br.pull()["added"] == 100
        lab.fail_on("FROM lem_machine_log", nth=1)
        killed = br.pull()
        assert killed["ok"] is False and "8s" in killed["error"]
        assert len(legacy_keys(store)) == 100      # chunk 1 kept, nothing else
        while br.pull().get("added"):
            pass
        keys = legacy_keys(store)
        assert (len(keys), len(set(keys))) == (250, 250)
        assert kit.lost_and_dup(kit.lc_multiset(lab),
                                kit.store_multiset(store)) == (0, 0)


class TestRenumbering:
    def test_a_vacuum_under_the_bridge_is_noticed_and_re_walked_adding_0(
            self, tmp_path):
        """LabCore deletes 15 rows ('purge history' on a v3.9 server) and
        VACUUMs: every later rowid moves. The next pull finds the row at its
        cursor is not the row it holds there, starts a new numbering and
        walks the log again — adding nothing it already holds — and then
        carries on with rows written after the VACUUM."""
        lab = empty_labcore()
        store = verified_store(tmp_path, lab)
        for vals in v39_rows(120):
            kit.append_log(lab, vals)
        br = bridge_for(store, lab, chunk=50)
        while br.pull().get("rows"):
            pass
        held = len(legacy_keys(store))
        assert held == 120
        kit.vacuum_renumber(lab, delete_rowids=range(10, 25))
        out = br.pull()
        assert out.get("renumbered") is True
        while br.pull().get("rows"):
            pass
        assert len(legacy_keys(store)) == held          # +0
        for vals in v39_rows(5, start=500):
            kit.append_log(lab, vals)
        while br.pull().get("rows"):
            pass
        assert len(legacy_keys(store)) == held + 5
        assert br.status()["pull"]["renumbered"] == 1
        gens = kit.store_rows(store, "SELECT DISTINCT gen FROM legacy_index")
        assert [g["gen"] for g in gens] == [1]          # the old map is gone


# ── W1 ──────────────────────────────────────────────────────────────────────

class TestW1:
    def run_cycles(self, br, start, n=30, step=12.0):
        for i in range(n):
            br.cycle(now=start + i * step)

    def test_one_read_per_12_s_plus_one_per_60_s_and_a_kill_costs_0(
            self, tmp_path):
        lab = empty_labcore()
        store = verified_store(tmp_path, lab)
        br = bridge_for(store, lab)
        br.cycle(now=0.0)                           # first cycle: everything
        mark = len(lab.calls)
        self.run_cycles(br, 12.0)                   # 30 cycles = 6 minutes
        healthy = lab.calls[mark:]
        assert sum(k == "read" for k, _ in healthy) == 30 + 6
        assert sum(k == "write" for k, _ in healthy) == 0
        assert sum("UNION ALL" in s for _k, s in healthy) == 30
        assert sum("FROM lem_machine_log" in s for _k, s in healthy) == 6

        # One watchdog on a state read AND one on a pull: the same 36 reads
        # over the next 30 cycles. +0, where v3.9's snapshot paid +426.
        lab.fail_on("UNION ALL", nth=3)
        lab.fail_on("FROM lem_machine_log", nth=2)
        mark = len(lab.calls)
        self.run_cycles(br, 372.0)
        after = lab.calls[mark:]
        assert len(after) - len(healthy) == 0, (len(after), len(healthy))
        assert br.status()["state"]["last_error"] is None   # recovered

    def test_healthz_says_what_the_bridge_is_doing_from_memory(self, tmp_path):
        """§12.3: /healthz adds bridge{on, legacy_benches, outbox} — read
        from the bridge's memory, so asking costs LabCore nothing."""
        lab = empty_labcore()
        store = verified_store(tmp_path, lab)
        app = bk.make_app(store, labcore=lab)
        br = app.config["BRIDGE"]
        br.cycle(now=0.0)
        mark = len(lab.calls)
        h = app.test_client().get("/healthz").get_json()
        assert lab.calls[mark:] == []
        assert h["bridge"]["on"] is True
        assert h["bridge"]["legacy_benches"] == 2
        assert h["bridge"]["outbox"]["pending"] == 0
        assert h["store"]["import"]["state"] == "verified"

    def test_the_state_arms_ask_only_for_legacy_mode_benches(self, tmp_path):
        """gc-2 has synced v2: it reports its own state and is left out of
        the read. When every registered bench is v2 the read is not made."""
        lab = empty_labcore()
        store = verified_store(tmp_path, lab)
        store.sql("INSERT INTO bench_cursor (machine_uid, bench_epoch, "
                  "acked_seq, mode) VALUES ('gc-2', 'ep', 0, 'v2')")
        br = bridge_for(store, lab)
        mark = len(lab.calls)
        assert br.state_pull()["ok"]
        reads = [s for k, s in lab.calls[mark:] if k == "read"]
        assert len(reads) == 1
        assert br.legacy_benches == 1
        assert br.legacy_uids() == ["pac-flash-1"]
        store.sql("INSERT INTO bench_cursor (machine_uid, bench_epoch, "
                  "acked_seq, mode) VALUES ('pac-flash-1', 'ep', 0, 'v2')")
        mark = len(lab.calls)
        assert br.state_pull()["reads"] == 0
        assert lab.calls[mark:] == []

    def test_state_lands_and_a_failed_read_keeps_what_was_there(self, tmp_path):
        lab = empty_labcore()
        store = verified_store(tmp_path, lab)
        br = bridge_for(store, lab)
        lab.x("UPDATE lem_machine_status SET status = 'RED', reason = 'QC "
              "failed', updated_at = '2026-10-02T09:30:00' WHERE machine_uid "
              "= 'pac-flash-1'")
        lab.x("INSERT INTO lem_machine_specs (machine_uid, test_name, low, "
              "high) VALUES ('pac-flash-1', 'Flash Point', 61.6, 65.8)")
        assert br.state_pull()["updated"] == 2
        st = kit.store_rows(store, "SELECT status, reason FROM "
                                   "lem_machine_status WHERE machine_uid = "
                                   "'pac-flash-1'")
        assert (st[0]["status"], st[0]["reason"]) == ("RED", "QC failed")
        lab.fail_on("UNION ALL")
        assert br.state_pull()["ok"] is False
        st = kit.store_rows(store, "SELECT status FROM lem_machine_status "
                                   "WHERE machine_uid = 'pac-flash-1'")
        assert st[0]["status"] == "RED"            # never emptied by a failure
        assert len(kit.store_rows(store, "SELECT * FROM lem_machine_specs "
                                         "WHERE machine_uid = 'pac-flash-1'")) == 1


# ── off means off (S4) ──────────────────────────────────────────────────────

class TestOffMeansOff:
    def test_no_labcore_op_with_the_switch_off(self, tmp_path):
        lab = empty_labcore()
        store = verified_store(tmp_path, lab)
        store.sql("INSERT INTO store_meta (key, value) VALUES ('bridge', "
                  "'off')")
        br = bridge_for(store, lab)
        mark = len(lab.calls)
        for i in range(20):
            br.cycle(now=i * 60.0)
        assert lab.calls[mark:] == []
        assert br.status()["on"] is False

    def test_no_labcore_op_before_the_import_is_verified(self, tmp_path):
        lab = empty_labcore()
        store = make_store(tmp_path)
        br = bridge_for(store, lab)
        for i in range(20):
            br.cycle(now=i * 60.0)
        assert lab.calls == []
        assert "import" in br.status()["why_off"]


# ── replays from a v3.9 bench: visible, never hidden ────────────────────────

class TestReplayCandidates:
    def test_a_replay_burst_arrives_labelled_and_still_counted(self, tmp_path):
        lab = empty_labcore()
        store = verified_store(tmp_path, lab)
        br = bridge_for(store, lab)
        first = v39_rows(30)
        for vals in first:
            kit.append_log(lab, vals)
        br.pull()
        # The same 25 readings written again by one later poll (v3.9's
        # restart replay), and a genuine 3-reading re-test by another.
        replay_ts = "2026-10-02T11:00:00.000001"
        for vals in first[:25]:
            kit.append_log(lab, (vals[0], replay_ts) + vals[2:])
        retest_ts = "2026-10-02T11:05:00.000001"
        for vals in first[25:28]:
            kit.append_log(lab, (vals[0], retest_ts) + vals[2:])
        out = br.pull()
        assert out["candidates"] == 25
        labelled = kit.store_rows(
            store, "SELECT l.ts, a.label, a.dup_of FROM log_annotation a JOIN "
                   "lem_machine_log l ON l.id = a.log_id")  # raw-log: test
        assert len(labelled) == 25
        assert {r["label"] for r in labelled} == {"replay_candidate"}
        assert {r["ts"] for r in labelled} == {replay_ts}
        assert all(r["dup_of"] for r in labelled)
        # Visible: the effective record still holds every row LabCore holds.
        assert kit.lost_and_dup(kit.lc_multiset(lab),
                                kit.store_multiset(store)) == (0, 0)


# ── projection: store first, upsert first, prune last ───────────────────────

class TestProjection:
    def test_an_imported_store_sends_nothing(self, tmp_path):
        lab = empty_labcore()
        store = verified_store(tmp_path, lab)
        br = bridge_for(store, lab)
        assert br.project()["queued"] == 0

    def test_a_factor_change_reaches_labcore_and_a_refusal_is_retried(
            self, tmp_path):
        lab = empty_labcore()
        store = verified_store(tmp_path, lab)
        br = bridge_for(store, lab)
        store.sql("UPDATE lem_correction_factors SET correction = 0.75 WHERE "
                  "machine_uid = 'pac-flash-1'")
        assert br.project()["queued"] == 1
        lab.fail_on("lem_correction_factors", kind="write", answer=kit.BUSY)
        assert br.drain(now=100.0)["sent"] == 0
        row = kit.store_rows(store, "SELECT tries, landed_at, last_error FROM "
                                    "projection_outbox")[0]
        assert (row["tries"], row["landed_at"]) == (1, None)
        assert "busy" in row["last_error"]
        assert br.status()["outbox"]["pending"] == 1
        assert br.status_items()[0]["key"] == "bridge_outbox"
        # The cycle waits out the backoff (5 s after the first refusal) …
        assert "drain" not in br.cycle(now=101.0)
        # … and sends once it has passed.
        br.cycle(now=200.0)
        assert lab.q("SELECT correction FROM lem_correction_factors")[0][
            "correction"] == 0.75
        assert br.status()["outbox"]["pending"] == 0

    def test_upserts_go_before_prunes(self, tmp_path):
        lab = empty_labcore()
        store = verified_store(tmp_path, lab)
        br = bridge_for(store, lab)
        store.sql("DELETE FROM lem_machine_targets")
        store.sql("INSERT INTO lem_qc_samples (name, sample_id_val, tests) "
                  "VALUES ('AO25', 'STD-2', '[]')")
        br.project()
        order = [r["sql"].split()[0] for r in kit.store_rows(
            store, "SELECT sql FROM projection_outbox ORDER BY id")]
        assert order == ["INSERT", "DELETE"]
        br.drain(now=0.0)
        assert lab.q("SELECT COUNT(*) AS n FROM lem_machine_targets")[0]["n"] == 0
        assert lab.q("SELECT name FROM lem_qc_samples WHERE name = 'AO25'")

    def test_a_config_edit_never_rewinds_a_v39_benchs_cursor(self, tmp_path):
        """LabCore's config for pac-flash-1 carries last_position 5120, which
        a v3.9 bench saved there. The store's copy is edited on the web (a new
        title, a new interval) with whatever last_position it holds. LabCore
        gets the edit and keeps 5120: rewinding it would replay the file."""
        lab = empty_labcore()
        store = verified_store(tmp_path, lab)
        br = bridge_for(store, lab)
        lab.x("UPDATE lem_machine_config SET config = json_set(config, "
              "'$.last_position', 9000) WHERE machine_uid = 'pac-flash-1'")
        cfg = json.loads(kit.store_rows(store, "SELECT config FROM "
                                               "lem_machine_config WHERE "
                                               "machine_uid = 'pac-flash-1'")
                         [0]["config"])
        cfg.update(interval_seconds=60, last_position=0)
        store.sql("UPDATE lem_machine_config SET title = 'PAC Flash One', "
                  "config = ? WHERE machine_uid = 'pac-flash-1'",
                  [json.dumps(cfg)])
        assert br.project()["queued"] == 1
        br.drain(now=0.0)
        row = lab.q("SELECT title, config FROM lem_machine_config WHERE "
                    "machine_uid = 'pac-flash-1'")[0]
        got = json.loads(row["config"])
        assert row["title"] == "PAC Flash One"
        assert got["interval_seconds"] == 60
        assert got["last_position"] == 9000
        assert got["csv_path"] == "C:/data/flash.csv"


# ── jk linking and M6 ───────────────────────────────────────────────────────

def projected_rows(uid, epoch, seqs, rows_per_record=1):
    """What a v4 bench in legacy projection writes to LabCore for records
    `seqs`: the same log rows the v2 path makes (`bench_api.log_rows_for`),
    each detail carrying `jk = epoch:seq` (§7)."""
    import bench_api
    out = []
    for seq in seqs:
        body = bk.run_record(seq, epoch, uid=uid)
        if rows_per_record > 1:
            body["log"] = [[uid, "2026-10-01 09:00:%02d" % (seq % 60), "run",
                            body["lab_id"], "T%d" % i, str(seq + i),
                            json.dumps({"n": i})]
                           for i in range(rows_per_record)]
        for ts, kind, lab_id, test, value, detail in bench_api.log_rows_for(
                body):
            out.append((uid, ts, kind, lab_id, test, value,
                        bench_api._row_detail(detail, "%s:%d" % (epoch, seq))))
    return out


def bench_records(bench, seqs, rows_per_record=1):
    """Journal records `seqs` on the model bench, with the same bodies
    `projected_rows` projected (so the v2 sync and the projection agree)."""
    for seq in seqs:
        assert seq == len(bench.records) + 1
        body = bk.run_record(seq, bench.epoch, uid=bench.uid)
        if rows_per_record > 1:
            body["log"] = [[bench.uid, "2026-10-01 09:00:%02d" % (seq % 60),
                            "run", body["lab_id"], "T%d" % i, str(seq + i),
                            json.dumps({"n": i})]
                           for i in range(rows_per_record)]
        bench.records.append(body)
        bench._digests.append(bk.chain(bench._digests[-1], body))


class TestM6:
    def setup(self, tmp_path, n=40, per=2):
        lab = empty_labcore()                 # pac-flash-1 is registered
        for vals in projected_rows(bk.UID, "ep-1", range(1, n + 1), per):
            kit.append_log(lab, vals)
        store = verified_store(tmp_path, lab)
        app = bk.make_app(store, labcore=lab)
        client = app.test_client()
        bench = bk.Bench(client, bk.enroll(client))
        bench_records(bench, range(1, n + 1), per)
        return lab, store, bench

    def test_projected_records_then_v2_from_seq_1_are_not_doubled(
            self, tmp_path):
        """§10.3: on its first v2 sync the bench sends its epoch from seq 1.
        The server already holds those 40 records (80 rows) through the
        import, linked by jk, so it acks them and adds nothing."""
        lab, store, bench = self.setup(tmp_path)
        bench.drain()
        assert bench.acked == 40
        rows = bk.log_rows(store)
        assert len(rows) == 80, len(rows)
        assert Counter((r["bench_epoch"], r["bench_seq"]) for r in rows
                       if r["bench_seq"] is not None) == Counter(
            ("ep-1", s) for s in range(1, 41))
        assert len(bk.stored_seqs(store)) == 40      # custody of every record

    def test_v2_first_then_the_pull_sees_the_projection_adds_nothing(
            self, tmp_path):
        """The other order: records 41–60 were projected to LabCore AND synced
        v2 before the bridge's next pull reached them."""
        lab, store, bench = self.setup(tmp_path)
        for vals in projected_rows(bk.UID, "ep-1", range(41, 61), 2):
            kit.append_log(lab, vals)
        bench_records(bench, range(41, 61), 2)
        bench.drain()
        before = len(bk.log_rows(store))
        assert before == 120
        br = bridge_for(store, lab)
        out = br.pull()
        assert out["ok"] and out["added"] == 0 and out["linked"] == 40
        assert len(bk.log_rows(store)) == 120

    def test_a_pull_that_split_a_record_then_v2_then_the_rest(self, tmp_path):
        """A 2-row record cut in half by the pull's chunk boundary: the first
        row is pulled (it becomes the record's custody row), the v2 sync
        arrives, then the second row is pulled. One copy of each row."""
        lab, store, bench = self.setup(tmp_path, n=2)
        for vals in projected_rows(bk.UID, "ep-1", [3], 2):
            kit.append_log(lab, vals)
        br = bridge_for(store, lab, chunk=1)
        assert br.pull()["added"] == 1               # row 1 of record 3
        bench_records(bench, [3], 2)
        bench.drain()
        while br.pull().get("rows"):
            pass
        rows = [r for r in bk.log_rows(store)
                if json.loads(r["detail"]).get("jk") == "ep-1:3"]
        assert len(rows) == 2, rows


class TestDG2ServerHalf:
    def test_a_re_upgrade_after_a_rollback_back_fill_adds_0(self, tmp_path):
        """DG2's server half. Records 1–30 reached the store by v2. The server
        is rolled back to v3.9; on its 404 the bench projects its last 24 h
        (records 20–30) to LabCore and goes on projecting 31–35. The server
        is upgraded again: the pull meets the back-fill (held as bench
        records: linked, not added) and 31–35 (new: added), and the bench's
        next v2 sync of 31–35 adds nothing. (The back-fill itself is the
        module's, P5/P8.)"""
        lab = empty_labcore()
        store = verified_store(tmp_path, lab)
        client = bk.make_app(store, labcore=lab).test_client()
        bench = bk.Bench(client, bk.enroll(client))
        bench_records(bench, range(1, 31))
        bench.drain()
        assert len(bk.log_rows(store)) == 30
        for vals in projected_rows(bk.UID, "ep-1", range(20, 36)):
            kit.append_log(lab, vals)
        bench_records(bench, range(31, 36))
        out = bridge_for(store, lab).pull()
        assert (out["added"], out["linked"]) == (5, 11)
        bench.drain()
        assert bench.acked == 35
        rows = bk.log_rows(store)
        assert len(rows) == 35
        assert sorted(r["bench_seq"] for r in rows) == list(range(1, 36))


# ── M2: a v3.9 bench under a v4 server ──────────────────────────────────────

class TestM2:
    def test_v39_writes_reach_the_store_0_lost_0_dup_through_faults(
            self, tmp_path):
        """The real v3.9.0 module's INSERTs (`build_log_batch`,
        `build_log_insert`) into LabCore, pulled by the bridge in chunks of
        50 through: a watchdog on a pull, an N3 double write, a 25-row replay
        burst, and the server restarting (a new bridge on the same store) in
        the middle. Truth is LabCore's own table: the store's effective
        record must equal it, row for row."""
        from datetime import datetime, timedelta
        v39 = kit.v39_module()
        lab = empty_labcore()
        store = verified_store(tmp_path, lab)
        br = bridge_for(store, lab, chunk=50)
        t0 = datetime(2026, 10, 2, 8, 0, 0)
        written = []
        for poll in range(60):
            ts = t0 + timedelta(seconds=30 * poll, microseconds=poll)
            batch = [v39.build_log_insert(
                "gc-2", "run", ts, lab_id="L-%05d" % (poll * 3 + i),
                detail={"values": {"Sulfur": "%.4f" % (poll + i / 10)}})[1]
                for i in range(3)]
            sql, args = v39.build_log_batch(batch)
            lab.x(sql, args)
            written += batch
            if poll == 20:                          # N3: landed, answer lost
                lab.x(sql, args)
            if poll == 40:                          # a restart replays 25
                rts = t0 + timedelta(hours=2)
                again = [[a[0], rts.isoformat()] + a[2:] for a in written[:25]]
                lab.x(*v39.build_log_batch(again))
            if poll == 25:
                lab.fail_on("FROM lem_machine_log", nth=1)
            if poll == 33:
                br = bridge_for(store, lab, chunk=50)      # server restart
            if poll % 2 == 1:
                br.pull()
        while br.pull().get("rows"):
            pass
        truth = kit.lc_multiset(lab)
        assert sum(truth.values()) == 180 + 3 + 25
        assert kit.lost_and_dup(truth, kit.store_multiset(store)) == (0, 0)
        labels = kit.store_rows(store, "SELECT label, COUNT(*) AS n FROM "
                                       "log_annotation GROUP BY label")
        assert labels == [{"label": "replay_candidate", "n": 25}]


# ── DG1: the downgrade bound ────────────────────────────────────────────────

class TestDG1:
    UID = "pac-flash-1"

    def test_a_v4_to_v39_downgrade_replays_at_most_15_minutes(self, tmp_path):
        """A v2 bench prints every 30 s for 3 hours and syncs after every
        print, reporting its source offset. The bridge cycles every 12 s and
        mirrors that offset into LabCore with one json_set every 15 min. At
        twelve moments — including the second before a mirror is due — the
        bench is rolled back: the real v3.9.0 module reads its config from
        LabCore and tails the file from the stored offset. Everything it
        reads again is a replay. The oldest replayed print is never more than
        15 minutes old. With no mirror (today) the same restart starts at
        the 5,120 bytes saved on 09-23 and replays nearly everything."""
        import bridge as bridge_mod
        v39 = kit.v39_module()
        lab = empty_labcore()
        store = verified_store(tmp_path, lab)
        app = bk.make_app(store, labcore=lab)
        client = app.test_client()
        bench = bk.Bench(client, bk.enroll(client, uid=self.UID), uid=self.UID)
        clock = {"t": 0.0}
        br = bridge_for(store, lab, clock=lambda: clock["t"])
        path = str(tmp_path / "flash.csv")
        open(path, "w").close()
        printed = []                              # (end offset, print time)
        worst = []
        # Mirrors run at 0, 900, 1800 … s; 3594 and 5394 are the last cycles
        # before one, where the bound is tightest.
        checks = {600, 2100, 3570, 3594, 4500, 5394, 5400, 6300, 7170,
                  8100, 9000, 10794}
        t = 0.0
        while t <= 3 * 3600:
            if int(t) % 30 == 0:
                with open(path, "a") as f:
                    f.write("L-%05d,%.2f\n" % (len(printed), 60 + len(printed)
                                               % 7 / 10))
                printed.append((os.path.getsize(path), t))
                bench.journal(1)
                r = bench.sync(sources=[{"src": "C:/data/flash.csv",
                                         "cursor": {"offset": printed[-1][0]}}])
                assert r.status_code == 200, r.get_json()
            clock["t"] = t
            br.cycle(now=t)
            if int(t) in checks:
                worst.append(self.replayed_minutes(v39, lab, path, printed, t))
            t += 6.0
        print("DG1 replayed minutes at each downgrade:", worst)
        assert len(worst) == len(checks)
        assert max(worst) <= 15.0, worst
        assert bridge_mod.MIRROR_EVERY_S == 900.0

        # Today: no mirror. The offset is the one a person saved on 09-23.
        lab2 = empty_labcore()
        today = self.replayed_minutes(v39, lab2, path, printed, t - 6.0)
        print("DG1 today (no mirror):", today, "minutes")
        assert today > 170

    def replayed_minutes(self, v39, lab, path, printed, now):
        sql, args = v39.build_config_fetch(self.UID)
        row = lab.fake.read_sql(sql, args)["rows"][0]
        machine = v39.machine_from_config_payload(row["config"], self.UID)
        text, _pos = v39.tail_new_text(path, machine.last_position)
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if not lines:
            return 0.0
        done = [p for p in printed if p[0] <= os.path.getsize(path)]
        first_replayed = done[len(done) - len(lines)][1]
        return (now - first_replayed) / 60.0
