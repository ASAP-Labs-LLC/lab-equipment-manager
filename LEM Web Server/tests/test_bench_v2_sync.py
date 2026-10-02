"""`POST /api/v2/bench/<uid>/sync` — how a bench's journal reaches the record.

Transfer spec §6.1. One rule carries the whole protocol: **the bench sends
from acked+1, and adopts whatever `acked` the server answers** — even one
ahead of its own (the N3 rule). Everything the server does exists to make
that one rule safe:

* records are numbered `(uid, epoch, seq)` and each is held exactly once, so a
  resend, a lost answer, a network retry or two requests overtaking each other
  can never write a reading twice (today, N3 writes it twice: 3 of 3);
* `acked` is the highest CONTIGUOUS seq held, so a bench is never told a gap
  is safe;
* a `from_seq` past acked+1 is the one shape a correct bench sends only when
  the server has gone BACK (restored from a backup): it answers 409 with its
  `acked`, and the bench resends from there — 0 lost, 0 dup (T3);
* the reconciliation digest is compared, and a disagreement is said out loud
  but NEVER blocks ingest: a server that refuses a bench's readings because
  their history looks odd has turned a question into a loss;
* 404 means only "this server has no v2" — the bench's sole cue to fall back
  to legacy projection — so no v2 route ever answers 404 for a uid it does not
  know (spec §6.1, "the only old-server signal");
* nothing here costs LabCore an op.

`bench_v2_kit.Bench` is a model bench that speaks the protocol exactly as the
spec says a v4 module must; `TestTheRealJournalSpeaksIt` runs the REAL module
journal (`lem_station_module.BenchJournal`) against the server, so a drift
between the kit and the module fails here.
"""
import gzip
import json
import os
import sys

import pytest

import bench_v2_kit as kit
from bench_v2_kit import Bench, UID
from labcore_counter import CountingLabCore
from labcore_gateway import FakeLabCoreGateway


@pytest.fixture
def store():
    s = FakeLabCoreGateway()
    kit.seed_machine(s)
    return s


@pytest.fixture
def lab():
    return CountingLabCore()


@pytest.fixture
def app(store, lab):
    return kit.make_app(store, labcore=lab)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def bench(client):
    return Bench(client, kit.enroll(client))


def _cursor(store, epoch="ep-1", uid=UID):
    res = store.read_sql("SELECT * FROM bench_cursor WHERE machine_uid = ? "
                         "AND bench_epoch = ?", [uid, epoch])
    assert "error" not in res, res
    return res["rows"][0] if res["rows"] else None


# ── the happy road ──────────────────────────────────────────────────────────

class TestDelivery:
    def test_a_sync_stores_every_record_once_and_acks_the_last(self, bench, store):
        bench.journal(5)
        r = bench.sync()
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["acked"] == 5 and body["epoch"] == "ep-1"
        assert body["machine"] == "active"
        assert sorted(kit.stored_seqs(store)) == [("ep-1", s) for s in range(1, 6)]
        assert sorted(kit.log_custody_seqs(store)) == [("ep-1", s)
                                                       for s in range(1, 6)]

    def test_the_answer_carries_every_field_the_bench_reads(self, bench):
        """§6.1's 200 body. A bench that finds a field missing cannot tell
        "no notes" from "this server does not send notes"."""
        bench.journal(1)
        body = bench.sync().get_json()
        for key in ("epoch", "acked", "durable", "notes", "config_rev",
                    "resolutions", "machine", "need_snapshot"):
            assert key in body, key
        assert isinstance(body["notes"], list)
        assert isinstance(body["resolutions"], list)
        assert isinstance(body["need_snapshot"], list)

    def test_an_empty_sync_is_a_heartbeat_and_acks_what_is_held(self, bench):
        bench.journal(3).drain()
        r = bench.sync()
        assert r.status_code == 200 and r.get_json()["acked"] == 3

    def test_a_run_record_lands_as_a_run_row_with_its_values(self, bench, store):
        bench.journal(1)
        bench.sync()
        row = kit.log_rows(store)[0]
        assert row["kind"] == "run" and row["lab_id"] == "L-00001"
        assert row["origin"] == "bench"
        assert row["bench_epoch"] == "ep-1" and row["bench_seq"] == 1
        assert row["content_key"] == bench.records[0]["lh"]
        detail = json.loads(row["detail"])
        assert detail["values"] == {"Flash": bench.records[0]["values"]["Flash"]}
        assert detail["jk"] == "ep-1:1"

    def test_the_modules_own_log_rows_land_exactly_as_v3_9_wrote_them(
            self, bench, store):
        """The real module journals each reading WITH the log rows it would
        have written (`"log"`: today's seven columns). Those land as they are —
        same kind, lab_id, test, value and detail — plus the record's identity,
        so every reader of `lem_machine_log` sees exactly what v3.9 gave it. A
        QC print makes one row PER verdict: the first carries the custody key,
        the rest carry `jk` in their detail and are written in the same
        transaction, so they can only ever exist together."""
        def make(seq, epoch, uid=UID):
            body = kit.run_record(seq, epoch, uid=uid, lab_id="AF26")
            body["log"] = [
                [uid, "2026-10-01T09:00:00", "qc", "AF26", "Flash", "41.2",
                 json.dumps({"verdict": "PASS", "raw": 41.2})],
                [uid, "2026-10-01T09:00:00", "qc", "AF26", "Sulfur", "9.8",
                 json.dumps({"verdict": "FAIL", "raw": 9.8})],
            ]
            return body
        bench.journal(1, make=make)
        assert bench.sync().status_code == 200
        rows = kit.log_rows(store)
        assert [(r["kind"], r["test_name"], r["value"]) for r in rows] == \
            [("qc", "Flash", "41.2"), ("qc", "Sulfur", "9.8")]
        assert [r["ts"] for r in rows] == ["2026-10-01T09:00:00"] * 2
        assert rows[0]["bench_seq"] == 1 and rows[1]["bench_seq"] is None
        assert all(r["bench_epoch"] == "ep-1" for r in rows)
        assert [json.loads(r["detail"])["verdict"] for r in rows] == ["PASS", "FAIL"]
        assert {json.loads(r["detail"])["jk"] for r in rows} == {"ep-1:1"}
        # resent: still two rows, not four
        bench.sync(from_seq=1)
        assert len(kit.log_rows(store)) == 2

    def test_a_status_change_lands_in_the_log_and_the_current_state(
            self, bench, store):
        def make(seq, epoch, uid=UID):
            return {"seq": seq, "epoch": epoch, "uid": uid, "kind": "state",
                    "ts": "2026-10-01T09:00:00-07:00", "module": "4.0.0",
                    "status": "RED", "reason": "QC out",
                    "sub": {"qc": "RED", "pm": "GREEN", "calibration": "GREEN"}}
        bench.journal(1, make=make)
        assert bench.sync().status_code == 200
        row = kit.log_rows(store)[0]
        assert row["kind"] == "status_change"
        assert json.loads(row["detail"])["to"] == "RED"
        st = store.read_sql("SELECT status, reason FROM lem_machine_status "
                            "WHERE machine_uid = ?", [UID])["rows"]
        assert st == [{"status": "RED", "reason": "QC out"}]
        sub = store.read_sql("SELECT qc FROM lem_machine_substatus "
                             "WHERE machine_uid = ?", [UID])["rows"]
        assert sub == [{"qc": "RED"}]

    def test_a_specs_record_replaces_the_benchs_effective_specs_whole(
            self, bench, store):
        def specs(names):
            def make(seq, epoch, uid=UID):
                return {"seq": seq, "epoch": epoch, "uid": uid, "kind": "specs",
                        "ts": "2026-10-01T09:00:00-07:00", "module": "4.0.0",
                        "specs": [{"test_name": n, "sample_id": "AF26",
                                   "expected": 1.0, "std_dev": 0.1, "k": 2.0,
                                   "units": "C", "low": 0.8, "high": 1.2}
                                  for n in names]}
            return make
        bench.journal(1, make=specs(["A", "B"])).drain()
        bench.journal(1, make=specs(["C"])).drain()
        got = store.read_sql("SELECT test_name FROM lem_machine_specs "
                             "WHERE machine_uid = ?", [UID])["rows"]
        assert [r["test_name"] for r in got] == ["C"]

    def test_a_fresh_sync_makes_the_bench_live_on_the_floor(self, bench, client):
        """§12.3: `live:true` is also set by a fresh v2 sync — the bench is
        talking to us, which is the liveness signal."""
        bench.journal(1)
        bench.sync(live={"status": "YELLOW", "reason": "PM due",
                         "at": "2026-10-01T09:00:00-07:00",
                         "interval_seconds": 30})
        machines = client.get("/api/machines?fresh=1").get_json()["machines"]
        mine = [m for m in machines if m["machine_uid"] == UID][0]
        assert mine["live"] is True and mine["status"] == "YELLOW"

    def test_a_gzipped_body_is_read(self, bench, client):
        """§6.1: gzip above 64 KB. A server that cannot read it would hold a
        busy bench's backlog forever."""
        bench.journal(100)
        raw = json.dumps(bench.body()).encode()
        r = client.post("/api/v2/bench/%s/sync" % UID, data=gzip.compress(raw),
                        headers={"X-LEM-Bench-Token": bench.token,
                                 "Content-Encoding": "gzip",
                                 "Content-Type": "application/json"})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["acked"] == 100


# ── N3 and every other way an answer goes missing ──────────────────────────

class TestNothingTwice:
    def test_N3_a_lost_answer_writes_nothing_twice(self, bench, store):
        """baseline N3, v3.9.0: 6 printed, 9 log rows, **3 dup** — the log
        INSERT landed, the answer was lost, and the bench wrote it again.
        Here: the same 6 prints, the second poll's answer lost; the bench
        resends from its old acked+1 and adopts the server's ack. 6 rows."""
        bench.journal(3).sync()
        bench.journal(3).sync(lose_response=True)       # landed, never answered
        assert bench.acked == 3
        r = bench.sync()                                 # resends 4..6
        assert r.status_code == 200 and r.get_json()["acked"] == 6
        sent = [("ep-1", s) for s in range(1, 7)]
        assert kit.dup_and_lost(sent, kit.stored_seqs(store)) == (0, 0)
        assert kit.dup_and_lost(sent, kit.log_custody_seqs(store)) == (0, 0)
        assert len(kit.log_rows(store)) == 6             # v3.9: 9

    def test_the_bench_adopts_an_ack_ahead_of_its_own(self, bench):
        bench.journal(4).sync(lose_response=True)
        r = bench.sync(from_seq=1)
        assert r.get_json()["acked"] == 4 and bench.acked == 4

    def test_the_same_request_twice_is_one_set_of_rows(self, bench, store):
        bench.journal(10)
        doc = bench.body()
        assert bench.post_doc(doc).status_code == 200
        assert bench.post_doc(doc).status_code == 200
        assert len(kit.stored_seqs(store)) == 10
        assert len(kit.log_rows(store)) == 10

    def test_a_resend_that_overlaps_the_held_range_adds_only_the_new(
            self, bench, store):
        bench.journal(10)
        bench.sync(limit=6)
        bench.sync(from_seq=3, limit=8)                  # 3..10
        assert sorted(kit.stored_seqs(store)) == [("ep-1", s) for s in range(1, 11)]

    def test_a_record_whose_body_changed_on_resend_keeps_the_first(
            self, bench, store):
        """The record is append-only: what was received first is the record.
        A changed body under a held seq cannot rewrite it, and the digest is
        what says the two sides now disagree (see TestDigest)."""
        bench.journal(1).sync()
        bench.records[0]["values"] = {"Flash": "99.9"}
        bench.sync(from_seq=1)
        detail = json.loads(kit.log_rows(store)[0]["detail"])
        assert detail["values"]["Flash"] != "99.9"


# ── T3: the server went back ────────────────────────────────────────────────

class TestCursor:
    def test_a_from_seq_past_the_held_range_is_409_cursor(self, bench, store):
        bench.journal(10).sync(limit=4)
        r = bench.sync(from_seq=8)
        assert r.status_code == 409
        assert r.get_json()["error"] == "cursor"
        assert r.get_json()["acked"] == 4
        # nothing past the gap was stored: acked is contiguous
        assert sorted(kit.stored_seqs(store)) == [("ep-1", s) for s in range(1, 5)]

    def test_after_409_the_resend_fills_the_gap_exactly(self, bench, store):
        bench.journal(10).sync(limit=4)
        bench.acked = 7                                   # bench believes 7
        assert bench.sync().status_code == 409            # adopts 4
        bench.drain()
        sent = [("ep-1", s) for s in range(1, 11)]
        assert kit.dup_and_lost(sent, kit.stored_seqs(store)) == (0, 0)

    def test_a_new_epoch_starts_at_1(self, client, bench, store):
        bench.journal(3).drain()
        other = Bench(client, bench.token, epoch="ep-2")
        other.journal(2)
        assert other.sync().get_json()["acked"] == 2
        assert sorted(kit.stored_seqs(store)) == [
            ("ep-1", 1), ("ep-1", 2), ("ep-1", 3), ("ep-2", 1), ("ep-2", 2)]
        assert _cursor(store, "ep-1")["acked_seq"] == 3
        assert _cursor(store, "ep-2")["acked_seq"] == 2

    def test_a_new_epoch_that_does_not_start_at_1_is_409(self, client, bench):
        other = Bench(client, bench.token, epoch="ep-9")
        other.journal(5)
        r = other.sync(from_seq=3)
        assert r.status_code == 409 and r.get_json()["acked"] == 0


# ── the digest: said out loud, never a reason to refuse ─────────────────────

class TestDigest:
    def test_the_server_keeps_the_same_running_digest_as_the_bench(
            self, bench, store):
        bench.journal(25).drain(limit=7)
        assert _cursor(store)["digest"] == bench.digest_through(25)

    def test_a_disagreeing_digest_is_noted_and_ingest_goes_on(self, bench, store):
        bench.journal(3).drain()
        bench.journal(3)
        r = bench.sync(stats={"digest": "f" * 64, "unacked": 3,
                              "road": "public"})
        assert r.status_code == 200
        assert r.get_json()["acked"] == 6                 # not blocked
        assert any(n.get("kind") == "digest_mismatch"
                   for n in r.get_json()["notes"])
        mismatch = json.loads(_cursor(store)["digest_mismatch"])
        assert mismatch["through"] == 3
        assert mismatch["bench"] == "f" * 64
        assert mismatch["server"] == bench.digest_through(3)

    def test_an_agreeing_digest_notes_nothing(self, bench, store):
        bench.journal(3).drain()
        bench.journal(1)
        r = bench.sync()
        assert not [n for n in r.get_json()["notes"]
                    if n.get("kind") == "digest_mismatch"]
        assert _cursor(store)["digest_mismatch"] is None


# ── what is not a reading ───────────────────────────────────────────────────

class TestKinds:
    def test_an_unknown_kind_is_parked_in_unknown_records_not_dropped(
            self, bench, store):
        """§13: a new record kind is a MINOR change because an older server
        parks it in `unknown_records`. Parked, held in custody, acked — never
        dropped, and never guessed into the 17025 log."""
        def make(seq, epoch, uid=UID):
            return {"seq": seq, "epoch": epoch, "uid": uid,
                    "kind": "from_the_future", "ts": "2026-10-01T09:00:00-07:00",
                    "module": "4.1.0", "payload": {"x": 1}}
        bench.journal(1, make=make).journal(1)
        r = bench.sync()
        assert r.status_code == 200 and r.get_json()["acked"] == 2
        unk = store.read_sql("SELECT bench_epoch, bench_seq, body FROM "
                             "unknown_records WHERE machine_uid = ?", [UID])["rows"]
        assert [(u["bench_epoch"], u["bench_seq"]) for u in unk] == [("ep-1", 1)]
        assert json.loads(unk[0]["body"])["payload"] == {"x": 1}
        assert [r_["bench_seq"] for r_ in kit.log_rows(store)] == [2]
        bench.sync(from_seq=1)                            # resent: still one
        assert len(store.read_sql("SELECT 1 FROM unknown_records")["rows"]) == 1

    def test_bench_bookkeeping_is_held_in_custody_and_not_logged(
            self, bench, store):
        """`consumed`, `frame`, `projected`, `settled` are the journal's own
        bookkeeping. They are records — the digest runs over them — so the
        server holds them, but they are not readings and do not enter the
        machine log a person reads."""
        def make(seq, epoch, uid=UID):
            return {"seq": seq, "epoch": epoch, "uid": uid, "kind": "consumed",
                    "ts": "2026-10-01T09:00:00-07:00", "module": "4.0.0",
                    "pks": ["a", "b"]}
        bench.journal(1, make=make).journal(1).drain()
        assert sorted(kit.stored_seqs(store)) == [("ep-1", 1), ("ep-1", 2)]
        assert [r["kind"] for r in kit.log_rows(store)] == ["run"]
        assert store.read_sql("SELECT COUNT(*) AS n FROM unknown_records"
                              )["rows"][0]["n"] == 0

    def test_an_ambiguous_line_lands_with_a_visible_label(self, bench, store):
        """§4.2: an ambiguous repeat is recorded, never dropped, and labelled
        `ambiguous_repeat` — a label that hides nothing."""
        def make(seq, epoch, uid=UID):
            body = kit.run_record(seq, epoch, uid=uid)
            body["origin"] = "ambiguous"
            return body
        bench.journal(1, make=make).drain()
        row = kit.log_rows(store)[0]
        assert row["origin"] == "ambiguous"
        ann = store.read_sql("SELECT label FROM log_annotation WHERE log_id = ?",
                             [row["id"]])["rows"]
        assert [a["label"] for a in ann] == ["ambiguous_repeat"]
        eff = store.read_sql("SELECT COUNT(*) AS n FROM lem_machine_log_effective "
                             "WHERE id = ?", [row["id"]])["rows"][0]["n"]
        assert eff == 1

    def test_a_new_epoch_repeating_an_old_line_is_a_visible_candidate(
            self, client, bench, store):
        """§4.4, the server's second line of defence: a record under a NEW
        epoch whose line hash is already covered by an earlier epoch is
        inserted (never dropped) with a visible `replay_candidate`."""
        bench.journal(2).drain()
        again = Bench(client, bench.token, epoch="ep-2")
        again.journal(1, make=lambda seq, epoch, uid=UID: kit.run_record(
            seq, epoch, uid=uid, lh=bench.records[0]["lh"]))
        again.journal(1, make=lambda seq, epoch, uid=UID: kit.run_record(
            seq, epoch, uid=uid, lh="fresh-line"))
        again.drain()
        labels = store.read_sql(
            "SELECT l.bench_epoch, l.bench_seq, a.label FROM log_annotation a "
            # raw-log: the test reads every row it wrote
            "JOIN lem_machine_log l ON l.id = a.log_id")["rows"]
        assert [(x["bench_epoch"], x["bench_seq"], x["label"]) for x in labels] \
            == [("ep-2", 1, "replay_candidate")]
        assert len(kit.log_rows(store)) == 4              # inserted, all of them


# ── refusals: each one says which, and 404 means only "no v2" ───────────────

class TestRefusals:
    def test_no_token_or_a_wrong_one_is_401(self, bench):
        bench.journal(1)
        assert bench.post_doc(bench.body(), token="").status_code == 401
        assert bench.post_doc(bench.body(), token="nope").status_code == 401

    def test_the_shared_live_token_is_not_a_bench_token(self, bench):
        bench.journal(1)
        r = bench.post_doc(bench.body(), token=kit.SHARED_TOKEN)
        assert r.status_code == 401

    def test_a_token_for_another_bench_is_401(self, client, store, bench):
        kit.seed_machine(store, uid="other-1", title="Other")
        other_token = kit.enroll(client, uid="other-1")
        bench.journal(1)
        assert bench.post_doc(bench.body(), token=other_token).status_code == 401

    def test_an_unknown_uid_is_never_404(self, client):
        """404 is the bench's cue that the server has no v2 at all, and it
        switches to legacy projection for 15 minutes. An unknown uid is an
        enrolment question (401), not an old server."""
        r = client.post("/api/v2/bench/nobody-here/sync", json={},
                        headers={"X-LEM-Bench-Token": "x"})
        assert r.status_code == 401

    @pytest.mark.parametrize("mutate,why", [
        (lambda d: d.update(records="nope"), "records"),
        (lambda d: d.update(from_seq=0), "from_seq"),
        (lambda d: d.update(from_seq="1"), "from_seq"),
        (lambda d: d.update(epoch=""), "epoch"),
        (lambda d: d.update(proto=1), "proto"),
        (lambda d: d.update(machine_uid="someone-else"), "machine_uid"),
        (lambda d: d["records"][1].update(seq=7), "contiguous"),
        (lambda d: d["records"][0].update(epoch="ep-x"), "epoch"),
        (lambda d: d["records"][0].update(uid="other"), "uid"),
        (lambda d: d["records"][0].update(value="tampered"), "crc"),
        (lambda d: d["records"][0].pop("crc"), "crc"),
        (lambda d: d["records"][0].pop("kind"), "kind"),
    ])
    def test_a_malformed_body_is_400_and_stores_nothing(self, bench, store,
                                                        mutate, why):
        bench.journal(3)
        doc = bench.body()
        mutate(doc)
        r = bench.post_doc(doc)
        assert r.status_code == 400, (why, r.get_json())
        assert why in r.get_json()["error"]
        assert kit.stored_seqs(store) == []

    def test_more_than_100_records_is_400(self, bench):
        bench.journal(101)
        r = bench.post_doc(bench.body(limit=101))
        assert r.status_code == 400 and "100" in r.get_json()["error"]

    def test_a_body_that_is_not_json_is_400(self, bench, client):
        r = client.post("/api/v2/bench/%s/sync" % UID, data=b"{not json",
                        headers={"X-LEM-Bench-Token": bench.token,
                                 "Content-Type": "application/json"})
        assert r.status_code == 400

    def test_a_read_only_store_is_503_with_retry_after(self, tmp_path):
        """The updater's candidate boot opens the store read-only (§5.1). A
        bench that reached it must HOLD, not be told its records are in."""
        from lem_store import LocalStoreGateway
        path = str(tmp_path / "lem.db")
        rw = LocalStoreGateway(path)
        kit.seed_machine(rw)
        token = kit.enroll(kit.make_app(rw).test_client())
        rw.close()
        ro = LocalStoreGateway(path, read_only=True)
        client = kit.make_app(ro).test_client()
        b = Bench(client, token)
        b.journal(1)
        r = b.post_doc(b.body())
        assert r.status_code == 503
        assert r.headers.get("Retry-After")

    def test_a_store_on_hold_is_503_with_retry_after(self, bench, store):
        """§10.1: until the import is verified, sync answers 503 and benches
        hold. The hold is a row in `store_meta`, set by whoever is moving the
        record."""
        store.sql("INSERT INTO store_meta (key, value) VALUES ('sync_hold', "
                  "'importing the record from LabCore')")
        bench.journal(1)
        r = bench.post_doc(bench.body())
        assert r.status_code == 503 and r.headers.get("Retry-After")
        assert "importing" in r.get_json()["error"]
        assert kit.stored_seqs(store) == []

    def test_a_store_that_refuses_mid_sync_stores_nothing_and_says_503(
            self, bench, store, monkeypatch):
        """One transaction: a write that fails half way through a sync rolls
        the whole sync back, and the answer is 503 (try again), never a 200
        acking records the store does not hold."""
        bench.journal(5)
        real_sql = type(store).sql
        calls = {"n": 0}

        def flaky(self, sql, args=None, **kw):
            if "INSERT INTO lem_machine_log" in sql:
                calls["n"] += 1
                if calls["n"] == 3:
                    return {"error": "disk I/O error"}
            return real_sql(self, sql, args, **kw)
        monkeypatch.setattr(type(store), "sql", flaky)
        r = bench.post_doc(bench.body())
        assert r.status_code == 503, r.get_json()
        monkeypatch.setattr(type(store), "sql", real_sql)
        assert kit.stored_seqs(store) == []
        assert kit.log_rows(store) == []
        assert _cursor(store) is None
        assert bench.sync().get_json()["acked"] == 5

    def test_a_skewed_bench_clock_is_recorded_never_refused(self, bench, store):
        """§6.4: no time-window rejection. A bench whose clock is an hour out
        still delivers; the skew is written down for the instrument page."""
        bench.journal(1)
        r = bench.sync(bench_clock="2020-01-01T00:00:00+00:00")
        assert r.status_code == 200
        skew = _cursor(store)["clock_skew_s"]
        assert skew is not None and abs(skew) > 3600


# ── the bench's report on itself is kept ───────────────────────────────────

class TestCursorRow:
    def test_road_version_and_stats_are_recorded(self, bench, store):
        bench.journal(2)
        bench.sync(stats={"unacked": 2, "road": "lan", "records_total": 2,
                          "digest": kit.DIGEST_ZERO,
                          "labcore": {"reads": 4, "writes": 1, "failed": 2,
                                      "timeouts": 1, "watchdog": 1}})
        cur = _cursor(store)
        assert cur["road"] == "lan" and cur["module_version"] == "4.0.0"
        assert cur["records_total"] == 2 and cur["mode"] == "v2"
        assert cur["labcore_failures_5min"] == 2
        assert json.loads(cur["stats"])["labcore"]["watchdog"] == 1

    def test_a_retired_machine_is_told_so_and_its_records_still_land(
            self, bench, store):
        """Only the explicit answer counts as a retirement (§6.6). The records
        it sends are still the record: custody first."""
        store.sql("UPDATE lem_machine_config SET retired_at = "
                  "'2026-10-01T08:30:00' WHERE machine_uid = ?", [UID])
        bench.journal(1)
        r = bench.sync()
        assert r.status_code == 200 and r.get_json()["machine"] == "retired"
        assert kit.stored_seqs(store) == [("ep-1", 1)]


# ── it costs LabCore nothing ────────────────────────────────────────────────

def test_a_day_of_syncs_costs_labcore_nothing(bench, lab):
    for _ in range(50):
        bench.journal(3)
        bench.sync()
    bench.sync(from_seq=1)
    assert lab.ops == 0, lab.calls


# ── the real module's journal speaks the same protocol ─────────────────────

MODULE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                          "LEM Station Module"))


class TestTheRealJournalSpeaksIt:
    """`BenchJournal` writes the lines; the server reads them as sent. If the
    module's canonical body, CRC or digest recipe drifts from the server's,
    this fails — the kit could drift with the server and never notice."""

    @pytest.fixture
    def journal(self, tmp_path):
        if MODULE_DIR not in sys.path:
            sys.path.insert(0, MODULE_DIR)
        import lem_station_module as m
        j = m.BenchJournal(str(tmp_path / "journal"), UID)
        yield m, j
        j.close()

    def test_lines_from_the_real_journal_land_and_the_digests_agree(
            self, journal, client, store):
        m, j = journal
        token = kit.enroll(client)
        for n in range(3):
            row_log = [list(m.build_log_insert(
                UID, "run", m.datetime(2026, 10, 1, 9, n), lab_id="L-%d" % n,
                detail={"values": {"Flash": "4%d.0" % n}})[1])]
            j.append([{"kind": "run", "origin": "live", "src": "file",
                       "pk": "pk-%d" % n, "lh": "lh-%d" % n, "lab_id": "L-%d" % n,
                       "values": {"Flash": "4%d.0" % n}, "raw": {},
                       "corrections": {}, "log": row_log}])
        j.append([{"kind": "consumed", "pks": ["hdr"]}])
        lines = []
        for seg in sorted(os.listdir(j.dir)):
            if seg.startswith("seg-"):
                with open(os.path.join(j.dir, seg), "rb") as f:
                    lines += [json.loads(x) for x in f.read().splitlines() if x]
        doc = {"machine_uid": UID, "epoch": j.epoch, "proto": 2,
               "module_version": m.MODULE_VERSION, "from_seq": 1,
               "records": lines, "stats": {"digest": j.digest(0)}}
        r = client.post("/api/v2/bench/%s/sync" % UID, json=doc,
                        headers={"X-LEM-Bench-Token": token})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["acked"] == 4
        assert _cursor(store, j.epoch)["digest"] == j.digest(4)
        rows = kit.log_rows(store)
        assert [(r_["kind"], r_["lab_id"]) for r_ in rows] == [
            ("run", "L-0"), ("run", "L-1"), ("run", "L-2")]
        assert rows[0]["ts"] == "2026-10-01T09:00:00"
        # The next sync compares digests through 4: they agree.
        j.set_acked(4)
        doc2 = dict(doc, from_seq=5, records=[], stats={"digest": j.digest()})
        r2 = client.post("/api/v2/bench/%s/sync" % UID, json=doc2,
                         headers={"X-LEM-Bench-Token": token})
        assert r2.status_code == 200
        assert _cursor(store, j.epoch)["digest_mismatch"] is None
