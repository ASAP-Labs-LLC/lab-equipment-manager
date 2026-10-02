"""The v2 bench's reads: config, checkpoint, adoption digest, ping, and the
source snapshot it uploads only when asked (transfer spec §6.1, §6.5, §10.2).

Each one is answered from the LEM store and memory at **0 LabCore ops** — a
bench reading its own world must not cost the queue the whole lab writes
through, which is the load v3.9's per-bench polling put there.

And each one keeps the rule this codebase is built around: a failed read is
never an empty result. A checkpoint that could not read the store must not
answer "you have sent nothing" — a bench told that would re-send its whole
file (the T4 flood) — so it answers 503 and the bench waits.
"""
import base64
import gzip
from datetime import datetime, timedelta
import hashlib
import json

import pytest

import bench_v2_kit as kit
from bench_v2_kit import Bench, UID
from labcore_counter import CountingLabCore
from labcore_gateway import FakeLabCoreGateway


@pytest.fixture
def store():
    s = FakeLabCoreGateway()
    kit.seed_machine(s)
    s.sql("UPDATE lem_machine_config SET config = ? WHERE machine_uid = ?",
          [json.dumps({"title": "PAC Flash 1", "source_type": "single_csv"}), UID])
    return s


@pytest.fixture
def lab():
    return CountingLabCore()


@pytest.fixture
def app(store, lab):
    a = kit.make_app(store, labcore=lab)
    a.config["SNAPSHOTS"].refresh()
    return a


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def token(client):
    return kit.enroll(client)


def _get(client, path, token, **kw):
    return client.get(path, headers={"X-LEM-Bench-Token": token}, **kw)


def _qc(store, test, value, ts, lab_id="AF26", verdict="PASS"):
    res = store.sql(
        "INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
        "test_name, value, detail) VALUES (?, ?, 'qc', ?, ?, ?, ?)",
        [UID, ts, lab_id, test, value, json.dumps({"verdict": verdict})])
    assert "error" not in res, res


class TestPing:
    def test_ping_names_the_protocols_and_needs_no_token(self, client, lab):
        r = client.get("/api/v2/ping")
        assert r.status_code == 200
        body = r.get_json()
        assert body["proto"] == [2] and "version" in body and "server_time" in body
        assert lab.ops == 0


class TestConfigV2:
    def test_it_is_v1s_body_plus_rev_machine_config_and_last_qc(
            self, client, token, store, lab):
        _qc(store, "Flash", "41.0", "2026-10-01T08:00:00")
        _qc(store, "Flash", "42.0", "2026-10-01T09:00:00", verdict="FAIL")
        _qc(store, "Sulfur", "9.0", "2026-10-01T07:00:00")
        v1 = client.get("/api/bench/%s/config" % UID,
                        headers={"X-LEM-Token": kit.SHARED_TOKEN}).get_json()
        r = _get(client, "/api/v2/bench/%s/config" % UID, token)
        assert r.status_code == 200, r.get_json()
        v2 = r.get_json()
        for key, value in v1.items():
            if key != "snapshot_age_seconds":
                assert v2[key] == value, key
        assert isinstance(v2["config_rev"], str) and len(v2["config_rev"]) >= 16
        assert v2["machine_config"]["source_type"] == "single_csv"
        last = {(q["test_name"], q["lab_id"]): q for q in v2["last_qc"]}
        assert last[("Flash", "AF26")]["value"] == "42.0"
        assert last[("Flash", "AF26")]["verdict"] == "FAIL"
        assert last[("Sulfur", "AF26")]["value"] == "9.0"
        assert lab.ops == 0

    def test_last_qc_says_in_spec_for_the_rows_the_module_writes(
            self, client, token, store):
        """The station module records a QC verdict as `detail.in_spec`, not
        `verdict` (qc_log_detail). A v2 bench rebuilds its QC memory from this
        list after a restart — it no longer reads LabCore's log, which no
        longer holds its rows — so without `in_spec` a passed QC came back
        as "assigned but not yet run"."""
        for value, ok, ts in (("41.0", True, "2026-10-01T08:00:00"),
                              ("44.0", False, "2026-10-01T09:00:00")):
            res = store.sql(
                "INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
                "test_name, value, detail) VALUES (?, ?, 'qc', 'QC1', 'Flash', "
                "?, ?)", [UID, ts, value, json.dumps({"in_spec": ok})])
            assert "error" not in res, res
        last = _get(client, "/api/v2/bench/%s/config" % UID, token).get_json()[
            "last_qc"]
        assert [(q["value"], q["in_spec"]) for q in last] == [("44.0", False)]

    def test_the_rev_moves_when_the_configuration_does_and_only_then(
            self, client, token, store, app):
        rev1 = _get(client, "/api/v2/bench/%s/config" % UID, token).get_json()[
            "config_rev"]
        rev1b = _get(client, "/api/v2/bench/%s/config" % UID, token).get_json()[
            "config_rev"]
        assert rev1 == rev1b
        store.sql("INSERT INTO lem_correction_factors (machine_uid, test_name, "
                  "correction) VALUES (?, 'Flash', 0.5)", [UID])
        app.config["SNAPSHOTS"].refresh()
        rev2 = _get(client, "/api/v2/bench/%s/config" % UID, token).get_json()[
            "config_rev"]
        assert rev2 != rev1

    def test_the_sync_answer_carries_the_same_rev(self, client, token):
        rev = _get(client, "/api/v2/bench/%s/config" % UID, token).get_json()[
            "config_rev"]
        b = Bench(client, token)
        b.journal(1)
        assert b.sync().get_json()["config_rev"] == rev

    def test_a_snapshot_that_never_built_is_503_never_an_empty_config(
            self, store):
        """An empty configuration is an instruction (clear the QC, drop the
        override). "I have nothing yet" is not that instruction."""
        app = kit.make_app(store)            # no refresh
        client = app.test_client()
        tok = kit.enroll(client)
        r = _get(client, "/api/v2/bench/%s/config" % UID, tok)
        assert r.status_code == 503

    def test_it_needs_the_benchs_own_token(self, client):
        r = client.get("/api/v2/bench/%s/config" % UID,
                       headers={"X-LEM-Bench-Token": "wrong"})
        assert r.status_code == 401


class TestCheckpoint:
    def test_a_known_bench_learns_what_the_server_holds(self, client, token,
                                                        store, lab):
        b = Bench(client, token)
        b.journal(4).drain()
        snap = hashlib.sha256(b"line-hashes").hexdigest()
        b.sync(sources=[{"src": "C:/data/flash.csv",
                         "cursor": {"offset": 120, "head_hash": "h",
                                    "tail_hash": "t", "file_id": [1, 2],
                                    "lineage": "lin-1"},
                         "snapshot_sha": snap}])
        r = client.put("/api/v2/bench/%s/source-snapshot" % UID,
                       query_string={"src": "C:/data/flash.csv", "sha": snap},
                       data=b"line-hashes",
                       headers={"X-LEM-Bench-Token": token})
        assert r.status_code == 200, r.get_json()
        store.sql("INSERT INTO result_ledger (machine_uid, lab_id, test_name, "
                  "value, filed_at, bench_seq_ref) VALUES (?, 'L-1', 'Flash', "
                  "'41.0', strftime('%Y-%m-%dT%H:%M:%S','now'), 'ep-1:1')", [UID])
        _qc(store, "Flash", "41.0", "2026-10-01T09:00:00")
        cp = _get(client, "/api/v2/bench/%s/checkpoint" % UID, token)
        assert cp.status_code == 200, cp.get_json()
        body = cp.get_json()
        assert body["epochs"][0]["epoch"] == "ep-1"
        assert body["epochs"][0]["acked"] == 4
        src = body["sources"][0]
        assert src["src"] == "C:/data/flash.csv"
        assert src["cursor"]["offset"] == 120 and src["lineage"] == "lin-1"
        assert base64.b64decode(src["snapshot"]) == b"line-hashes"
        assert src["snapshot_sha"] == snap
        assert [(x["lab_id"], x["value"]) for x in body["result_ledger"]] == [
            ("L-1", "41.0")]
        assert body["last_qc"][0]["test_name"] == "Flash"
        assert lab.ops == 0

    def test_an_unreadable_store_is_503_never_you_sent_nothing(
            self, client, token, store, monkeypatch):
        real = type(store).read_sql

        def broken(self, sql, args=None, **kw):
            if "bench_source" in sql:
                return {"error": "database is locked", "busy": True}
            return real(self, sql, args, **kw)
        monkeypatch.setattr(type(store), "read_sql", broken)
        r = _get(client, "/api/v2/bench/%s/checkpoint" % UID, token)
        assert r.status_code == 503
        assert "sources" not in (r.get_json() or {})


class TestSourceSnapshot:
    def test_the_server_asks_for_a_snapshot_once_per_change(self, client, token):
        b = Bench(client, token)
        b.journal(1)
        src = {"src": "f.csv", "cursor": {"offset": 1}, "snapshot_sha": "a" * 64}
        r = b.sync(sources=[src])
        assert r.get_json()["need_snapshot"] == ["f.csv"]
        payload = b"x" * 32
        sha = hashlib.sha256(payload).hexdigest()
        src["snapshot_sha"] = sha
        assert b.sync(sources=[src]).get_json()["need_snapshot"] == ["f.csv"]
        up = client.put("/api/v2/bench/%s/source-snapshot" % UID,
                        query_string={"src": "f.csv", "sha": sha},
                        data=gzip.compress(payload),
                        headers={"X-LEM-Bench-Token": token,
                                 "Content-Encoding": "gzip"})
        assert up.status_code == 200, up.get_json()
        assert b.sync(sources=[src]).get_json()["need_snapshot"] == []

    def test_a_snapshot_whose_bytes_do_not_match_its_sha_is_refused(
            self, client, token):
        r = client.put("/api/v2/bench/%s/source-snapshot" % UID,
                       query_string={"src": "f.csv", "sha": "0" * 64},
                       data=b"something else",
                       headers={"X-LEM-Bench-Token": token})
        assert r.status_code == 400 and "sha" in r.get_json()["error"]


class TestAdoptionDigest:
    def test_it_is_the_multiset_of_lab_id_and_raw_values(self, client, token,
                                                          store, lab):
        """§10.2 step 3: the bench matches the lines after its boundary to
        legacy rows on (lab_id, RAW values), by multiset — the k-th identical
        line matches the k-th identical row — so a correction factor changed
        since cannot unmatch a line (U3). `raw` where a correction applied,
        otherwise `values`."""
        def row(lab_id, detail, kind="run", test=""):
            res = store.sql(
                "INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
                "test_name, value, detail) VALUES (?, '2026-09-30T10:00:00', "
                "?, ?, ?, '', ?)", [UID, kind, lab_id, test, json.dumps(detail)])
            assert "error" not in res
        row("L-1", {"values": {"Flash": "41.0"}})
        row("L-1", {"values": {"Flash": "41.0"}})            # a genuine repeat
        row("L-2", {"values": {"Flash": "42.5", "Pour": "-3"},
                    "raw": {"Flash": 42.0}, "corrections": {"Flash": 0.5}})
        r = _get(client, "/api/v2/bench/%s/adoption" % UID, token,
                 query_string={"src": "f.csv", "boundary": "0"})
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        # Numbers in one canonical form ("41.0" and 41 are one reading: the
        # file says "41.0", a corrected row's raw is the float 41.0), and a
        # corrected row keyed on its raw laid over the values it did not
        # correct — so the line "L-2,42.0,-3" matches it whatever the factor
        # is now.
        h1 = hashlib.sha256(json.dumps(["L-1", {"Flash": "41"}],
                                       sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False).encode()
                            ).hexdigest()[:32]
        h2 = hashlib.sha256(json.dumps(["L-2", {"Flash": "42", "Pour": "-3"}],
                                       sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False).encode()
                            ).hexdigest()[:32]
        assert body["counts"] == {h1: 2, h2: 1}
        assert body["rows"] == 3 and body["recipe"].startswith("sha256")
        assert body["first_ts"] == "2026-09-30T10:00:00"
        assert lab.ops == 0

    def test_rows_it_cannot_read_are_named_by_lab_id_not_dropped(
            self, client, token, store):
        """A recorded row whose detail cannot be read used to be skipped
        ("evidence of nothing"), and its line then looked unrecorded to the
        bench: a false `recovered`. It is evidence of SOMETHING — the record
        holds a row for that sample — so the digest names its Lab ID, and
        the bench presumes such lines recorded and says so. A QC verdict that
        kept no raw is not unreadable: it is keyed on its standard and test."""
        def row(lab_id, detail, kind="run", test="", value=""):
            res = store.sql(
                "INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
                "test_name, value, detail) VALUES (?, '2026-09-30T10:00:00', "
                "?, ?, ?, ?, ?)", [UID, kind, lab_id, test, value, detail])
            assert "error" not in res
        row("L-1", json.dumps({"values": {"Flash": "41.0"}}))
        row("L-2", "{not json")
        row("L-3", json.dumps({"no_values": True}))
        row("QC-1", json.dumps({"in_spec": True}), kind="qc", test="Flash",
            value="41.5")
        body = _get(client, "/api/v2/bench/%s/adoption" % UID, token).get_json()
        assert sorted(body["unreadable_labs"]) == ["L-2", "L-3"]
        h = hashlib.sha256(json.dumps(["QC-1", {"Flash": "(no raw)"}],
                                      sort_keys=True, separators=(",", ":")
                                      ).encode()).hexdigest()[:32]
        assert body["counts"][h] == 1 and sum(body["counts"].values()) == 2
        assert body["rows"] == 4
        # Which tests each Lab ID holds verdicts of: a QC print is matched on
        # what was judged THEN, whatever today's QC assignment is.
        assert body["qc_tests"] == {"QC-1": ["Flash"]}
        # And what each verdict judged. A verdict that kept no raw is matched
        # on its VALUE: a count per (standard, test) is inflated by every
        # restart's replay and matched a standard printed during the upgrade
        # (round-2 critic, Agilent GC 1).
        assert body["qc_verdicts"] == {
            "QC-1": {"Flash": {"raw": {}, "value": {"41.5": 1}}}}

    def test_it_says_when_this_bench_was_first_recorded_and_nothing_is_empty(
            self, client, token, store):
        """The bench needs "has LEM EVER recorded me, and since when" (§10.2:
        pre-LEM lines are those older than the first ingest). A bench with no
        rows says rows 0 and first_ts null — a statement, not a failure."""
        r = _get(client, "/api/v2/bench/%s/adoption" % UID, token)
        assert r.status_code == 200
        body = r.get_json()
        assert (body["rows"], body["counts"], body["first_ts"]) == (0, {}, None)
        assert body["recent"] == []

    def test_recent_run_rows_carry_the_values_that_were_filed(self, client,
                                                              token, store):
        """§10.2 step 5 seeds the results guard's ledger from the matched
        legacy rows of the last 30 days: for those the bench needs the
        CORRECTED values v3.9 filed, keyed by the same hash. Older rows and
        qc rows are not part of it."""
        now = datetime.now()

        def row(lab_id, ts, detail, kind="run"):
            res = store.sql(
                "INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
                "test_name, value, detail) VALUES (?, ?, ?, ?, '', '', ?)",
                [UID, ts, kind, lab_id, json.dumps(detail)])
            assert "error" not in res
        recent = (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        old = (now - timedelta(days=45)).strftime("%Y-%m-%d %H:%M:%S")
        row("L-1", recent, {"values": {"Flash": "41.5"}, "raw": {"Flash": 41.0}})
        row("L-2", old, {"values": {"Flash": "40.0"}})
        body = _get(client, "/api/v2/bench/%s/adoption" % UID, token).get_json()
        assert len(body["recent"]) == 1
        (r,) = body["recent"]
        assert (r["lab_id"], r["values"], r["ts"]) == ("L-1", {"Flash": "41.5"},
                                                       recent)
        h = hashlib.sha256(json.dumps(["L-1", {"Flash": "41"}], sort_keys=True,
                                      separators=(",", ":")).encode()
                           ).hexdigest()[:32]
        assert r["h"] == h and body["counts"][h] == 1

    def test_a_large_answer_is_gzipped_when_the_bench_accepts_it(
            self, client, token, store):
        for n in range(300):
            store.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, "
                      "lab_id, test_name, value, detail) VALUES (?, "
                      "'2026-09-30T10:00:00', 'run', ?, '', '', ?)",
                      [UID, "L-%d" % n, json.dumps({"values": {"F": str(n)}})])
        r = client.get("/api/v2/bench/%s/adoption" % UID,
                       headers={"X-LEM-Bench-Token": token,
                                "Accept-Encoding": "gzip"})
        assert r.status_code == 200
        assert r.headers.get("Content-Encoding") == "gzip"
        body = json.loads(gzip.decompress(r.data))
        assert body["rows"] == 300
