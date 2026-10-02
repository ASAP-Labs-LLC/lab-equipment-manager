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

These tests came with T-P3's inline v2 sync. That sync and T-P8's uploader
thread were the same road built twice; the merged module keeps the uploader
(the poll never does LEM I/O), so the tests here drive a poll and then give
the uploader the poll interval to do its work (`settle`), and talk to the
shared wire fake `fake_lem_v2.FakeLem` — which also answers /checkpoint and
the other paths the uploader uses, where a fake that 404'd them would read
as "an old server". Every rule below is unchanged.

The fake LEM speaks the server's wire contract (bench_api.py); the
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
from fake_lem_v2 import FakeLem as _WireLem
from lem_station_module import Machine

from test_module_qt import make_module

UID = "b1"
SHARED = "shared-live-token"
T0 = datetime(2026, 10, 1, 9, 0, 0)
LABS = ["100126-%05d" % (10000 + i) for i in range(300)]


def canonical(body):
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")


class FakeLem(_WireLem):
    """`fake_lem_v2.FakeLem`, with the views these tests read: every call as
    (road, method, path), LEM's run lab_ids, the sync count, the acked
    cursor, and "lose" as a road mode (LEM stores the sync, the answer never
    arrives — N3)."""

    def __init__(self):
        super().__init__(uid=UID, shared=SHARED)
        self._lose = False

    @property
    def calls(self):
        return [(r["road"], r["method"], r["path"]) for r in self.requests]

    def set_modes(self, **modes):
        lose = any(v == "lose" for v in modes.values())
        self._lose = lose
        self.modes.update({k: ("up" if v == "lose" else v)
                           for k, v in modes.items()})
        self.lost_responses = 10 ** 6 if lose else 0

    def acked(self, epoch):
        return self.cursor.get(epoch, 0)

    def syncs(self):
        return sum(1 for _r, m, p in self.calls
                   if m == "POST" and p.endswith("/sync"))

    def runs(self):
        return sorted(r["lab_id"] for r in super().runs())


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
    # A bind wakes the uploader at `bench_now()`; on the wall clock that is
    # a day after T0, and the roads' ten-minute re-probe would then read the
    # first poll as time running backwards. One clock, as the gate does.
    monkeypatch.setattr(mod, "bench_now", lambda: T0)
    lem = FakeLem()
    lem.install(monkeypatch)
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
        settle(m)
        return m

    def settle(m):
        assert m._uploader_wait_idle(30.0), "the uploader never went idle"
        up = m._uploader
        assert up is None or up.errors == 0, up.last_error

    def poll(m, k, prints=1):
        with open(w.path, "a") as f:
            for _ in range(prints):
                f.write("%s,0.%04d\n" % (LABS[w.n], 8000 + w.n))
                w.n += 1
        m.process_now(T0 + timedelta(seconds=30 * k))
        settle(m)
    w.bench, w.poll = bench, poll
    yield w
    for m in made:
        m.shutdown()


class TestNoTokenNoCall:
    def test_a_bench_that_cannot_prove_who_it_is_takes_todays_road(self, world):
        """No bench.key and no shared token in `lem_meta`: no v4 server has
        ever published itself to this LabCore, so this is today's road,
        exactly — results file, and LEM is not knocked on every poll (an
        unenrollable bench knocking is load for nothing).

        One question IS asked, once: LEM's ping, before the token read. The
        order is deliberate. A bench that binds while LEM is dark must not
        cost LabCore anything while it does not know what LEM is (N404/N503:
        0 LabCore ops while unknown), so the token is read only after LEM has
        answered; here it answers, the token read finds none, and that —
        a successful read of an empty lem_meta, never a FAILED read — is
        what sends the bench the old way. It asks again every 15 minutes,
        not every poll: 20 polls (10 minutes) cost one ping in all."""
        lab = LabCore(token=None)
        m = world.bench(lab)
        for k in range(20):
            world.poll(m, k)
        assert world.lem.calls == [("lan", "GET", "/api/v2/ping")]
        assert m._v2_active() is False
        assert sorted(lab.cells()) == LABS[:20]

    def test_a_failed_token_read_is_not_an_empty_one(self, world):
        """The same bench, but LabCore's read of lem_meta FAILS. That says
        nothing about whether a token exists, so the bench must not decide
        it is on an old fleet: it stays v2 (holding, as in any outage) and
        asks again later."""
        lab = LabCore(token=None)
        real = lab.read_sql

        def failing(sql, args=None, **kw):
            if "lem_meta" in sql:
                lab.calls.append(("read", sql))
                return {"error": "LabCore is busy", "busy": True}
            return real(sql, args, **kw)
        lab.read_sql = failing
        m = world.bench(lab)
        for k in range(3):
            world.poll(m, k)
        assert m._v2_active() is True
        assert lab.lem_calls() == [c for c in lab.calls if "lem_meta" in c[1]]


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
        world.lem.set_modes(lan="down", public="down")
        for k in range(1, 21):
            world.poll(m, k)
        assert lab.lem_calls(mark) == []
        world.lem.set_modes(lan="up", public="up")
        for k in range(21, 40):                       # past the 300 s backoff
            world.poll(m, k, prints=0)
        assert world.lem.runs() == LABS[:21]
        assert world.lem.resent == 0, "a record was sent to LEM twice"
        assert lab.lem_calls(mark) == []

    def test_D2_results_hold_while_LEM_cannot_confirm_the_factor(self, world):
        """Ryan's D2. LEM dark for more than a minute: the factor this bench
        holds is unconfirmed, so its results wait in the journal — not filed
        with a factor nobody confirmed — and file the poll LEM answers."""
        lab = LabCore()
        m = world.bench(lab)
        world.poll(m, 0)
        world.lem.set_modes(lan="down", public="down")
        for k in range(1, 6):                          # 150 s dark
            world.poll(m, k)
        held = [lab_id for lab_id in LABS[1:6] if lab_id not in lab.cells()]
        assert held, "nothing was held while LEM could not confirm the factor"
        assert set(held) >= set(LABS[3:6]), held
        world.lem.set_modes(lan="up", public="up")
        for k in range(6, 30):
            world.poll(m, k, prints=0)
        assert sorted(lab.cells()) == LABS[:6]

    def test_a_factor_changed_in_LEM_reaches_the_very_next_print(self, world):
        """The 60 s rule says a factor may be applied if LEM confirmed it
        within the last minute — but a minute-old confirmation says nothing
        about a factor a person saved in LEM ten seconds ago. With the roads
        UP, the critic (round 3) found the two readings printed straight
        after a factor change filed to LabCore with the factor LEM had
        already replaced: values LabCore keeps, that LEM no longer stands
        behind. A reading is now filed only on a confirmation made AFTER it
        was read — the uploader's sync that follows the poll — and with the
        factor that sync confirmed. That sync runs within the same poll
        interval, so nothing waits a poll longer, and the poll itself still
        never talks to LEM (`test_the_poll_thread_never_talks_to_lem`)."""
        lab = LabCore()
        m = world.bench(lab)
        world.poll(m, 0)
        world.poll(m, 1)
        assert sorted(lab.cells()) == LABS[:2]
        world.lem.corrections = [{"machine_uid": UID, "test_name": "Density",
                                  "correction": 0.001}]
        world.lem.config_rev = "rev-factor-saved-in-LEM"
        for k in range(2, 5):
            world.poll(m, k, prints=2)
            # filed in the poll interval it was read in, not one later
            assert set(LABS[:2 + 2 * (k - 1)]) <= set(lab.cells()), k
        cells = lab.cells()
        stale = [lab_id for lab_id in LABS[2:8]
                 if abs(float(cells[lab_id]) -
                        (0.8 + LABS.index(lab_id) / 10000) - 0.001) > 1e-9]
        assert stale == [], "filed with the factor LEM had replaced: %s" % (
            {k: cells[k] for k in stale})
        for lab_id in LABS[:2]:                        # before the change
            assert abs(float(cells[lab_id]) -
                       (0.8 + LABS.index(lab_id) / 10000)) < 1e-9

    def test_a_restart_in_v2_stays_v2_while_LEM_is_dark(self, world):
        """The journal remembers the handshake: a LabStation restart during an
        outage must not send the bench back to writing `lem_*` into LabCore."""
        lab = LabCore()
        m = world.bench(lab)
        world.poll(m, 0)
        m.shutdown()
        world.lem.set_modes(lan="down", public="down")
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
        world.lem.set_modes(lan="lose", public="lose")
        world.poll(m, 1, prints=3)                     # LEM stored them
        world.lem.set_modes(lan="up", public="up")
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
        world.lem.set_modes(lan="down", public="down")
        world.poll(m, 1, prints=2)                     # journaled, not acked
        world.lem.set_modes(lan="404", public="404")
        for k in range(12, 16):                        # past the backoff
            world.poll(m, k, prints=1)
        assert m._v2_active() is False
        landed = lab.log_runs()
        assert sorted(landed) == sorted(set(landed)), "a row landed twice"
        assert set(LABS[1:3]) <= set(landed), landed
        assert LABS[0] not in landed, (
            "a reading LEM had already acked was sent to LabCore as well")


    def test_a_bench_set_up_against_an_old_server_publishes_its_setup_there(
            self, qapp, world, monkeypatch):
        """M3 from the very first moment: a v4 bench set up on a floor whose
        LEM is v3.9 (it answers 404). The setup dialog's save happens while
        the bench's state is still UNKNOWN, so it is journaled as a `config`
        record — and the uploader's first probe hears the 404 before the
        first poll. The fall-back that projects journaled bookkeeping to
        LabCore used to fire only on a poll that SAW the bench go from v2 to
        legacy; a bench that was never seen as v2 by a poll skipped it, and
        its configuration reached LabCore never. On an old server LabCore's
        lem_machine_config is the only place the floor reads a bench's setup
        from, and the only place a restart that has lost config.json can bind
        from (the gate's A1j: "restart did not bind")."""
        lab = LabCore()
        world.lem.set_modes(lan="404", public="404")
        for name, fn in (("labcore_write", lab.write),
                         ("labcore_sql", lab.sql),
                         ("labcore_read_sql", lab.read_sql),
                         ("labcore_is_running", lab.is_running)):
            monkeypatch.setitem(mod.__dict__, name, fn)
        m = make_module()
        try:
            m.set_machine(Machine(
                uid=UID, title="Bench b1", source_type="single_csv",
                csv_path=str(world.path), delimiter=",",
                lab_id=mod.Selector(mode="cell", index=0),
                mappings=[mod.MethodMapping(
                    methods=["Density"],
                    selector=mod.Selector(mode="cell", index=1))]),
                publish=True)
            assert m._uploader_wait_idle(30.0)
            assert m._v2_active() is False, "the 404 was not heard at bind"
            world.poll(m, 1, prints=1)
            rows = lab.db.execute(
                "SELECT machine_uid, title FROM lem_machine_config").fetchall()
            assert [tuple(r) for r in rows] == [(UID, "Bench b1")]
            world.poll(m, 2, prints=0)
            assert len(lab.db.execute(
                "SELECT * FROM lem_machine_config").fetchall()) == 1
        finally:
            m.shutdown()


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

    def test_both_roads_down_back_off_30_60_120_then_300(self, world):
        """Measured on the bench's own attempts, not on a helper: with both
        roads refusing, the gaps between the uploader's tries are 30, 60,
        120, then 300 s, and stay at 300 — so a long outage costs LEM's
        roads one try per five minutes, not one per poll."""
        lab = LabCore()
        m = world.bench(lab)
        world.poll(m, 0, prints=0)
        world.lem.set_modes(lan="down", public="down")
        tries = []
        st = m._transfer
        for k in range(1, 60):                       # 30 minutes
            world.poll(m, k, prints=0)
            with st.lock:
                at = st.last_attempt
            if not tries or tries[-1] != at:
                tries.append(at)
        gaps = [round(b - a) for a, b in zip(tries, tries[1:])]
        assert gaps[:5] == [30, 60, 120, 300, 300], gaps

    def test_a_503_is_held_off_for_its_retry_after_not_a_fallback(self, world):
        """A 503 with Retry-After says "busy, come back then": the bench
        waits that long (the fake says 30 s) and stays a v2 bench."""
        lab = LabCore()
        m = world.bench(lab)
        world.poll(m, 0, prints=0)
        world.lem.set_modes(lan="503", public="503")
        world.poll(m, 1, prints=1)
        st = m._transfer
        with st.lock:
            gap = st.next_attempt - st.last_attempt
        assert gap == 30
        assert m._v2_active() is True
        assert lab.lem_calls() == [c for c in lab.calls if "lem_meta" in c[1]]

    def test_the_agent_names_the_bench(self):
        """Cloudflare answers urllib's default agent with 1010 (§6.2)."""
        assert mod.lem_user_agent(UID).startswith("LEM-Station/")


class TestJournalReadBack:
    def test_records_from_is_contiguous_and_bounded(self, tmp_path):
        """A sync carries records from acked+1 AS WRITTEN (crc included, which
        is what LEM checks), at most `limit` of them; nothing past the end."""
        j = mod.BenchJournal(str(tmp_path / "j"), UID)
        try:
            j.append([{"kind": "comment", "n": i} for i in range(7)])
            recs = j.records_from(3, limit=3)
            assert [r["seq"] for r in recs] == [3, 4, 5]
            assert all("crc" in r for r in recs)
            assert j.records_from(8) == []
        finally:
            j.close()

    def test_an_ack_marks_the_readings_projected(self, tmp_path):
        """LEM's ack is the projection: a reading at or below `acked` is in
        LEM's record, with no separate mark to write or lose."""
        j = mod.BenchJournal(str(tmp_path / "j"), UID)
        try:
            j.append([{"kind": "run", "lab_id": "A", "row": {}, "log": []},
                      {"kind": "run", "lab_id": "B", "row": {}, "log": []}])
            j.set_acked(1)
            assert [r["projected"] for r in j.open_runs()] == [True, False]
        finally:
            j.close()


class TestEventsBecomeRecords:
    def test_each_kind_lands_as_the_record_the_store_reads(self, world):
        """On a v2 bench `_log_event` journals a record LEM turns into the
        same machine-log row: a comment as `comment`, an expired held reading
        as `given_up`; a status change is NOT journaled on its own (the
        `state` record carries it), and none of it reaches LabCore."""
        lab = LabCore()
        m = world.bench(lab)
        world.poll(m, 0, prints=0)
        mark = len(lab.calls)
        before = len(world.lem.records)
        m._log_event("comment", detail={"note": "hi"}, now=T0)
        m._log_event("status_change", detail={"to": "RED"}, now=T0)
        m._log_event("held_expired", lab_id="L1", now=T0)
        world.poll(m, 1, prints=0)
        kinds = [r["kind"] for r in world.lem.records[before:]
                 if r["kind"] in ("comment", "given_up", "status_change")]
        assert kinds == ["comment", "given_up"]
        comment = next(r for r in world.lem.records[before:]
                       if r["kind"] == "comment")
        assert comment["detail"] == {"note": "hi"}
        assert lab.lem_calls(mark) == []
