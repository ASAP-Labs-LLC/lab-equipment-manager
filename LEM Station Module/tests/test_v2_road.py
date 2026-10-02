"""Protocol v2 on the bench (transfer v4 §6): the bench's own world goes to
LEM, LabCore carries the results road and nothing else.

Why. The gate measured this module, before it spoke v2, at 5.43 LabCore ops
per filing poll (row E1), 2.43 of them paid by an IDLE bench every poll —
heartbeat, config/override/QC-library/maintenance reads, status and spec
writes — and one more for the reading's machine-log row. LabCore's queue
serialises every bench's reads and writes at about 1.5 ops/s, so this is the
cost that multiplies by every bench Ryan adds. A v2 bench journals those
records and sends them in ONE `POST /api/v2/bench/<uid>/sync` per poll.

The rules each test below pins, and the failure it guards against:

  * no token, no call — a bench that cannot prove who it is behaves exactly
    as v3.9 did (no request to anyone);
  * once a bench has completed a v2 handshake, LEM being dark NEVER sends
    `lem_*` traffic to LabCore: records wait in the journal and every one of
    them reaches LEM when a road returns, once;
  * Ryan's D2: no result is filed to LabCore with a correction factor LEM has
    not confirmed current within 60 s — both roads dark means results HOLD in
    the journal and file when a road returns;
  * a 404 is the ONLY signal that sends a bench back to LabCore (an old
    server), and the readings LEM never acked then land in LabCore's log once;
  * an answer that was lost after LEM stored the records (N3) costs nothing
    twice: the bench resends from its acked+1, LEM keeps one, and the bench
    adopts LEM's acked;
  * roads are sticky, a dark LAN sits out ten minutes, and both roads down
    back off 30, 60, 120, then 300 s.

The fake LEM below speaks the server's wire contract (bench_api.py); the
server side is held to the same contract by the web server's own tests, and
`LEM Web Server/tests/test_bench_v2_module_road.py` runs THIS module against
the REAL server.
"""
import io
import json
import os
import socket
import sqlite3
import urllib.error
import urllib.parse
import zlib
from datetime import datetime, timedelta

import pytest

import lem_station_module as mod
from lem_station_module import Machine

from test_module_qt import make_module

UID = "b1"
SHARED = "shared-live-token"
T0 = datetime(2026, 10, 1, 9, 0, 0)
LABS = ["100126-%05d" % (10000 + i) for i in range(300)]


def canonical(body):
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")


class FakeLem:
    """LEM's v2 bench API, in memory: enrol, sync, config. `mode` per road:
    up / down (refused, nothing reaches LEM) / 404 (an old server) / lose
    (LEM stores the sync, the answer never arrives — N3)."""

    def __init__(self):
        self.modes = {"lan": "up", "public": "up"}
        self.calls = []                 # (road, method, path)
        self.records = {}               # (epoch, seq) -> body
        self.received = []              # every (epoch, seq) ever sent
        self.rev = "rev-1"
        self.corrections = []
        self.token = "bench-token-1"
        self.bad_crc = 0

    def road_of(self, url):
        return "lan" if urllib.parse.urlsplit(url).netloc == "192.168.1.5:5557" \
            else "public"

    def acked(self, epoch):
        n = 0
        while (epoch, n + 1) in self.records:
            n += 1
        return n

    def syncs(self):
        return sum(1 for _r, m, p in self.calls if m == "POST" and p.endswith("/sync"))

    def urlopen(self, req, data=None, timeout=None, **kw):
        url = req.full_url
        road = self.road_of(url)
        method = req.get_method()
        path = urllib.parse.urlsplit(url).path
        self.calls.append((road, method, path))
        mode = self.modes[road]
        if mode == "down":
            raise urllib.error.URLError(ConnectionRefusedError(61, "refused"))
        if mode == "404":
            raise urllib.error.HTTPError(url, 404, "NOT FOUND", {},
                                         io.BytesIO(b'{"error":"not found"}'))
        headers = {k.lower(): v for k, v in req.header_items()}
        assert headers.get("user-agent", "").startswith("LEM-Station/"), headers
        status, body = self.handle(method, path, headers, req.data)
        if mode == "lose":
            raise socket.timeout("timed out")
        raw = json.dumps(body).encode("utf-8")
        if status >= 400:
            raise urllib.error.HTTPError(url, status, "ERR", {}, io.BytesIO(raw))

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        r = Resp(raw)
        r.status = status
        return r

    def handle(self, method, path, headers, data):
        if path.endswith("/enroll"):
            if headers.get("x-lem-token") != SHARED:
                return 401, {"error": "Not authorised."}
            return 200, {"token": self.token, "machine_uid": UID}
        if headers.get("x-lem-bench-token") != self.token:
            return 401, {"error": "bad token"}
        if path.endswith("/config"):
            return 200, {"machine_uid": UID, "snapshot_age_seconds": 1.0,
                         "corrections": list(self.corrections),
                         "qc_samples": [], "qc_targets": [], "qc_specs": [],
                         "maintenance": [], "override": "",
                         "config_rev": self.rev, "last_qc": []}
        if path.endswith("/adoption"):
            # §10.2's digest: this fake LEM has recorded nothing before the
            # bench's first v4 start, so there is no history to adopt.
            return 200, {"machine_uid": UID, "counts": {}, "rows": 0,
                         "first_ts": None, "recent": []}
        if path.endswith("/sync"):
            doc = json.loads(data.decode("utf-8"))
            epoch = doc["epoch"]
            acked = self.acked(epoch)
            if doc["from_seq"] > acked + 1:
                return 409, {"error": "cursor", "acked": acked}
            for i, rec in enumerate(doc["records"]):
                assert rec["seq"] == doc["from_seq"] + i, "not contiguous"
                body = {k: v for k, v in rec.items() if k != "crc"}
                if rec.get("crc") != "%08x" % (zlib.crc32(canonical(body)) & 0xffffffff):
                    self.bad_crc += 1
                    return 400, {"error": "crc"}
                self.received.append((epoch, rec["seq"]))
                self.records.setdefault((epoch, rec["seq"]), body)
            return 200, {"epoch": epoch, "acked": self.acked(epoch),
                         "durable": 0, "notes": [], "config_rev": self.rev,
                         "resolutions": [], "machine": "active",
                         "need_snapshot": []}
        return 404, {"error": "no route"}

    def runs(self):
        return sorted(b["lab_id"] for b in self.records.values()
                      if b.get("kind") == "run")


class LabCore:
    """LabCore for a bench: samples, sample_tests, `lem_meta` (with the shared
    token the server's boot step publishes), every call recorded."""

    def __init__(self, token=SHARED):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE "samples" (lab_id TEXT PRIMARY KEY)')
        self.db.execute("CREATE TABLE sample_tests (lab_id TEXT, test_name "
                        "TEXT, result TEXT, updated_at TEXT, operator TEXT, "
                        "PRIMARY KEY (lab_id, test_name))")
        self.db.execute("CREATE TABLE lem_meta (key TEXT PRIMARY KEY, value TEXT)")
        self.db.execute("CREATE TABLE lem_machine_log (id INTEGER PRIMARY KEY, "
                        "machine_uid TEXT, ts TEXT, kind TEXT, lab_id TEXT, "
                        "test_name TEXT, value TEXT, detail TEXT)")
        if token:
            self.db.execute("INSERT INTO lem_meta VALUES ('live_token', ?)", [token])
        for lab in LABS:
            self.db.execute('INSERT INTO "samples" VALUES (?)', [lab])
        self.calls = []

    def lem_calls(self, since=0):
        return [c for c in self.calls[since:] if "LEM_" in c[1].upper()]

    def read_sql(self, sql, args=None, **kw):
        self.calls.append(("read", sql))
        try:
            return {"ok": True, "rows": [dict(r) for r in
                                         self.db.execute(sql, list(args or []))]}
        except sqlite3.Error as exc:
            return {"error": str(exc)}

    def sql(self, sql, args=None, **kw):
        self.calls.append(("sql", sql))
        try:
            cur = self.db.execute(sql, list(args or []))
            return {"ok": True, "rows_affected": cur.rowcount}
        except sqlite3.Error as exc:
            return {"ok": True}          # the other lem_* tables: accepted

    def write(self, operation, params=None, source=""):
        self.calls.append(("write", operation))
        results = []
        for i, o in enumerate((params or {}).get("operations") or []):
            p = o["params"]
            self.db.execute(
                "INSERT INTO sample_tests VALUES (?,?,?,?,?) ON CONFLICT(lab_id,"
                " test_name) DO UPDATE SET result=excluded.result",
                [p["lab_id"], p["test_name"], p["value"], "", ""])
            results.append({"index": i, "ok": True})
        return {"ok": True, "results": results}

    def is_running(self):
        return True

    def cells(self):
        return {r["lab_id"]: r["result"] for r in
                self.db.execute("SELECT lab_id, result FROM sample_tests")}

    def log_runs(self):
        return [r["lab_id"] for r in self.db.execute(
            "SELECT lab_id FROM lem_machine_log WHERE kind = 'run'")]


@pytest.fixture
def world(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("LEM_JOURNAL_DIR", str(tmp_path / "journal"))
    monkeypatch.setattr(mod, "_in_thread", lambda fn, cb: cb(fn()))
    lem = FakeLem()
    monkeypatch.setattr("urllib.request.urlopen", lem.urlopen)
    made = []

    class World:
        pass
    w = World()
    w.lem = lem
    w.n = 0
    w.path = tmp_path / "f.csv"
    w.path.write_text("")

    def bench(labcore):
        for name, fn in (("labcore_write", labcore.write),
                         ("labcore_sql", labcore.sql),
                         ("labcore_read_sql", labcore.read_sql),
                         ("labcore_is_running", labcore.is_running)):
            monkeypatch.setitem(mod.__dict__, name, fn)
        m = make_module()
        m.set_machine(Machine(
            uid=UID, title="Bench b1", source_type="single_csv",
            csv_path=str(w.path), delimiter=",",
            lab_id=mod.Selector(mode="cell", index=0),
            mappings=[mod.MethodMapping(
                methods=["Density"], selector=mod.Selector(mode="cell", index=1))]),
            publish=False)
        made.append(m)
        return m

    def poll(m, k, prints=1):
        with open(w.path, "a") as f:
            for _ in range(prints):
                f.write("%s,0.%04d\n" % (LABS[w.n], 8000 + w.n))
                w.n += 1
        m.process_now(T0 + timedelta(seconds=30 * k))
    w.bench, w.poll = bench, poll
    yield w
    for m in made:
        m.shutdown()


class TestNoTokenNoCall:
    def test_a_bench_that_cannot_prove_who_it_is_calls_nobody(self, world):
        """No bench.key and no shared token in `lem_meta`: today's road,
        exactly, and not one request — an unenrollable bench knocking every
        poll is load for nothing."""
        lab = LabCore(token=None)
        m = world.bench(lab)
        for k in range(3):
            world.poll(m, k)
        assert world.lem.calls == []
        assert m._v2_active() is False
        assert sorted(lab.cells()) == LABS[:3]


class TestV2Mode:
    def test_after_the_handshake_labcore_sees_the_results_road_only(self, world):
        lab = LabCore()
        m = world.bench(lab)
        world.poll(m, 0)
        assert m._v2_active()
        mark = len(lab.calls)
        for k in range(1, 11):
            world.poll(m, k)
        assert lab.lem_calls(mark) == []
        assert {c[0] for c in lab.calls[mark:]} == {"read", "write"}
        assert sorted(lab.cells()) == LABS[:11]
        assert world.lem.runs() == LABS[:11], "every reading is in LEM's record"
        assert world.lem.syncs() <= 11 + 1, "one sync per poll (plus the handshake)"

    def test_LEM_dark_sends_nothing_to_labcore_and_every_record_arrives_once(
            self, world):
        """The outage: both roads refuse for ten minutes. Nothing about the
        bench's world goes to LabCore instead (§6.1: never falls back), and
        when a road returns every journaled record reaches LEM exactly once."""
        lab = LabCore()
        m = world.bench(lab)
        world.poll(m, 0)
        mark = len(lab.calls)
        world.lem.modes.update(lan="down", public="down")
        for k in range(1, 21):
            world.poll(m, k)
        assert lab.lem_calls(mark) == []
        world.lem.modes.update(lan="up", public="up")
        for k in range(21, 40):                       # past the 300 s backoff
            world.poll(m, k, prints=0)
        assert world.lem.runs() == LABS[:21]
        assert len(world.lem.received) == len(set(world.lem.received)), \
            "a record was sent to LEM twice"
        assert lab.lem_calls(mark) == []

    def test_D2_results_hold_while_LEM_cannot_confirm_the_factor(self, world):
        """Ryan's D2. LEM dark for more than a minute: the factor this bench
        holds is unconfirmed, so its results wait in the journal — not filed
        with a factor nobody confirmed — and file the poll LEM answers."""
        lab = LabCore()
        m = world.bench(lab)
        world.poll(m, 0)
        world.lem.modes.update(lan="down", public="down")
        for k in range(1, 6):                          # 150 s dark
            world.poll(m, k)
        held = [lab_id for lab_id in LABS[1:6] if lab_id not in lab.cells()]
        assert held, "nothing was held while LEM could not confirm the factor"
        assert set(held) >= set(LABS[3:6]), held
        world.lem.modes.update(lan="up", public="up")
        for k in range(6, 30):
            world.poll(m, k, prints=0)
        assert sorted(lab.cells()) == LABS[:6]

    def test_a_restart_in_v2_stays_v2_while_LEM_is_dark(self, world):
        """The journal remembers the handshake: a LabStation restart during an
        outage must not send the bench back to writing `lem_*` into LabCore."""
        lab = LabCore()
        m = world.bench(lab)
        world.poll(m, 0)
        m.shutdown()
        world.lem.modes.update(lan="down", public="down")
        m2 = world.bench(lab)
        mark = len(lab.calls)
        for k in range(1, 5):
            world.poll(m2, k)
        assert m2._v2_active()
        assert lab.lem_calls(mark) == []

    def test_N3_a_lost_answer_is_adopted_not_doubled(self, world):
        lab = LabCore()
        m = world.bench(lab)
        world.poll(m, 0)
        world.lem.modes.update(lan="lose", public="lose")
        world.poll(m, 1, prints=3)                     # LEM stored them
        world.lem.modes.update(lan="up", public="up")
        for k in range(2, 20):
            world.poll(m, k, prints=0)
        assert world.lem.runs() == LABS[:4]
        journal = m._journal_for(m._machine)
        assert journal.acked == world.lem.acked(journal.epoch)

    def test_a_404_sends_the_bench_back_to_labcore_and_owed_rows_land_once(
            self, world):
        """An old server (a rollback): the ONLY fallback signal. The readings
        LEM never acked go to LabCore's machine log once each."""
        lab = LabCore()
        m = world.bench(lab)
        world.poll(m, 0)
        world.lem.modes.update(lan="down", public="down")
        world.poll(m, 1, prints=2)                     # journaled, not acked
        world.lem.modes.update(lan="404", public="404")
        for k in range(12, 16):                        # past the backoff
            world.poll(m, k, prints=1)
        assert m._v2_active() is False
        landed = lab.log_runs()
        assert sorted(landed) == sorted(set(landed)), "a row landed twice"
        assert set(LABS[1:3]) <= set(landed), landed
        assert LABS[0] not in landed, (
            "a reading LEM had already acked was sent to LabCore as well")


class TestRoads:
    def test_a_dark_lan_sits_out_ten_minutes(self, world):
        lab = LabCore()
        world.lem.modes["lan"] = "down"
        m = world.bench(lab)
        for k in range(10):
            world.poll(m, k, prints=0)
        lan = [c for c in world.lem.calls if c[0] == "lan"]
        assert len(lan) == 1, "the dark LAN was retried within ten minutes"
        assert m._v2_active()

    def test_both_roads_down_back_off_30_60_120_then_300(self):
        link = mod.V2Link(UID, mod.v2_roads(""))
        waits = []
        for _ in range(5):
            link.backoff(T0)
            waits.append((link.retry_at - T0).total_seconds())
        assert waits == [30, 60, 120, 300, 300]
        link.backoff(T0, retry_after=900)
        assert (link.retry_at - T0).total_seconds() == 900

    def test_the_agent_names_the_bench(self):
        """Cloudflare answers urllib's default agent with 1010 (§6.2)."""
        assert mod.lem_user_agent(UID).startswith("LEM-Station/")


class TestJournalReadBack:
    def test_records_after_is_contiguous_and_bounded(self, tmp_path):
        j = mod.BenchJournal(str(tmp_path / "j"), UID)
        try:
            j.append([{"kind": "comment", "n": i} for i in range(7)])
            recs = j.records_after(2, limit=3)
            assert [r["seq"] for r in recs] == [3, 4, 5]
            assert all("crc" in r for r in recs)
            assert j.records_after(7) == []
        finally:
            j.close()

    def test_an_ack_marks_the_readings_projected(self, tmp_path):
        j = mod.BenchJournal(str(tmp_path / "j"), UID)
        try:
            j.append([{"kind": "run", "lab_id": "A", "row": {}, "log": []},
                      {"kind": "run", "lab_id": "B", "row": {}, "log": []}])
            j.mark_projected_through(1)
            assert [r["projected"] for r in j.open_runs()] == [True, False]
        finally:
            j.close()


class TestEventsBecomeRecords:
    def test_each_kind_lands_as_the_record_the_store_reads(self):
        def ev(kind, **kw):
            return mod.build_log_insert(UID, kind, T0, **kw)
        recs, kept = mod.journal_events_as_records([
            ev("comment", detail={"note": "hi"}),
            ev("status_change", detail={"to": "RED"}),
            ev("held_expired", lab_id="L1"),
            mod._log_entry(ev("run", lab_id="L2")[1], "e:1")])
        assert kept == []
        assert [r["kind"] for r in recs] == ["comment", "given_up"], (
            "status_change rides the `state` record; a reading's own rows "
            "ride its `run` record")
        assert recs[0]["detail"] == {"note": "hi"}
