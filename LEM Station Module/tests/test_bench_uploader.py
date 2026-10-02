"""The bench's half of transfer v2 (spec §6): one thread talks to LEM.

Why this file exists. Until v4 every bench spent LabCore's one serialised
queue on LEM's own bookkeeping — an idle bench made 4.86 LabCore ops a minute
and the first poll of a module's life 8 reads + 15 writes (baseline 1e, 1f),
multiplied by every bench in the building. v4 moves that bookkeeping to the
LEM server over `POST /api/v2/bench/<uid>/sync`, and moves every LEM network
call OFF the poll: a separate daemon thread (the uploader) does all of it, so
a dark road can never stall the bench that reads the instrument.

What is pinned here, each in prose where it is tested:

* the poll thread never does LEM I/O (every request carries the thread that
  made it, and none is the poll's);
* two roads — LAN (1.5 s) and public (10 s) — each request naming itself
  `LEM-Station/<ver> (<uid>)`, because Cloudflare refuses urllib's default
  agent with 1010; a dark LAN costs one timeout per 10 minutes, not one per
  poll; both dark backs off 30 → 60 → 120 → 300 s;
* only a 404 means "an old server": a timeout or a 503 never sends a v2 bench
  back to writing its bookkeeping into LabCore;
* enrolment: the shared token proves a known bench once; bench.key keeps the
  per-bench token from then on;
* blind mode: a bench whose journal is gone does not re-read its file until
  LEM has said what it already holds;
* config.json: a restart binds from the cache, not from LabCore;
* the 60 s factor rule, confirmed by the sync's `config_rev` — with no LabCore
  replica (Ryan, D2), so while both LEM roads are dark results are HELD in the
  journal, and filed (with the factor in force) once a road returns;
* retirement only on an explicit `machine: "retired"`; an outage never.
"""
import os
import threading
from datetime import datetime, timedelta

import pytest

import lem_station_module as mod
from fake_lem_v2 import FakeLem
from test_module_qt import make_module

T0 = datetime(2026, 10, 1, 9, 0, 0)
POLL = timedelta(seconds=30)
UID = "m1"


class CountingLabCore:
    """The injected labcore_* helpers, counting every call. It holds the
    LIMS's samples (so results can be filed) and lem_meta's shared token
    (so a bench can enrol); every other read answers empty."""

    def __init__(self, shared="shared-token"):
        self.ops = []                # (kind, sql-or-operation, thread)
        self.samples = set()
        self.cells = {}              # (lab_id, test) -> value
        self.shared = shared
        self.configs = {}            # uid -> lem_machine_config.config JSON
        self.log_args = []           # args of every lem_machine_log insert

    def _note(self, kind, what):
        self.ops.append((kind, str(what), threading.get_ident()))

    def write(self, operation, params=None, source=""):
        self._note("write", operation)
        results = []
        for i, op in enumerate((params or {}).get("operations") or []):
            p = op.get("params") or {}
            self.cells[(p.get("lab_id"), p.get("test_name"))] = p.get("value")
            results.append({"index": i, "ok": True})
        # LabCore's _wop_batch answer, read per index by the guarded road.
        return {"ok": True, "results": results}

    def sql(self, sql, args=None, source=""):
        self._note("sql", sql)
        if str(sql).startswith("INSERT INTO lem_machine_log"):
            args = list(args or ())         # one or many 7-column rows
            for i in range(0, len(args), 7):
                self.log_args.append(tuple(args[i:i + 7]))
        return {"ok": True, "rows_affected": 1}

    def read_sql(self, sql, args=None, **kw):
        self._note("read", sql)
        if "lem_meta" in sql:
            return {"ok": True, "rows": [{"key": "live_token",
                                          "value": self.shared}]}
        if "lem_machine_config" in sql:
            uid = str((args or [""])[0])
            if uid in self.configs:
                return {"ok": True, "rows": [{"machine_uid": uid,
                                              "title": "Bench",
                                              "config": self.configs[uid]}]}
            return {"ok": True, "rows": []}
        if '"samples"' in sql or "sample_tests" in sql:
            return self._results_read(sql, args)
        return {"ok": True, "rows": []}

    def _results_read(self, sql, args):
        """The guarded results road's one read (identity LEFT JOIN the
        poll's cells, or cells by key): answered by real SQL over the
        samples and cells this fake holds, so the module's own query text
        is what is exercised, not a guess at its shape."""
        import sqlite3
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE "samples" (lab_id TEXT PRIMARY KEY)')
        db.execute("CREATE TABLE sample_tests (lab_id TEXT, test_name TEXT, "
                   "result TEXT, updated_at TEXT, operator TEXT)")
        db.executemany('INSERT INTO "samples" VALUES (?)',
                       [(x,) for x in self.samples])
        db.executemany("INSERT INTO sample_tests VALUES (?,?,?,?,?)",
                       [(k[0], k[1], v, "2026-10-01 09:00:00", "")
                        for k, v in self.cells.items()])
        try:
            rows = [dict(r) for r in db.execute(sql, list(args or []))]
        except sqlite3.Error as exc:
            return {"error": str(exc)}
        return {"ok": True, "rows": rows}

    def is_running(self):
        return True

    def count(self, since=0):
        return len(self.ops) - since


@pytest.fixture
def lab(monkeypatch):
    fake = CountingLabCore()
    monkeypatch.setattr(mod, "labcore_write", fake.write, raising=False)
    monkeypatch.setattr(mod, "labcore_sql", fake.sql, raising=False)
    monkeypatch.setattr(mod, "labcore_read_sql", fake.read_sql, raising=False)
    monkeypatch.setattr(mod, "labcore_is_running", fake.is_running,
                        raising=False)
    return fake


@pytest.fixture
def lem(monkeypatch):
    return FakeLem(uid=UID).install(monkeypatch)


@pytest.fixture
def journal_dir(tmp_path, monkeypatch):
    d = tmp_path / "journal"
    monkeypatch.setenv("LEM_JOURNAL_DIR", str(d))
    return d


def machine(tmp_path, source_type="single_csv", **kw):
    path = tmp_path / ("in.csv" if source_type != "multi_csv" else "drop")
    if source_type == "multi_csv":
        path.mkdir(exist_ok=True)
    elif not path.exists():
        path.write_text("")
    base = dict(uid=UID, title="Bench", source_type=source_type,
                csv_path=str(path), delimiter=",",
                lab_id=mod.Selector(mode="cell", index=0),
                mappings=[mod.MethodMapping(
                    methods=["Density"],
                    selector=mod.Selector(mode="cell", index=1))])
    base.update(kw)
    return mod.Machine(**base)


class Bench:
    """One module and its clock. `poll()` is one poll of the bench, then the
    time a real poll interval gives the uploader to do its work."""

    def __init__(self, qapp, tmp_path, state=None, source_type="single_csv",
                 bind=True):
        self.tmp = tmp_path
        self.k = 0
        self.m = make_module()
        self.machine = machine(tmp_path, source_type)
        if state is not None:
            self.m.restore_state(state)
        elif bind:
            self.m.set_machine(self.machine, publish=False)
        self.settle()

    @property
    def now(self):
        return T0 + self.k * POLL

    def settle(self):
        assert self.m._uploader_wait_idle(30.0), "the uploader never went idle"
        up = self.m._uploader
        # The uploader never lets an exception kill its thread — so a test
        # must look, or a crash in it would read as "LEM was unreachable".
        assert up is None or up.errors == 0, up.last_error

    def print_lines(self, *pairs):
        with open(self.machine.csv_path, "a") as f:
            for lab, value in pairs:
                f.write("%s,%s\n" % (lab, value))

    def poll(self, n=1):
        for _ in range(n):
            self.m.process_now(self.now)
            self.settle()
            self.k += 1

    def close(self):
        self.m.shutdown()


@pytest.fixture
def bench(qapp, tmp_path, lab, lem, journal_dir):
    benches = []

    def make(**kw):
        b = Bench(qapp, tmp_path, **kw)
        benches.append(b)
        return b
    yield make
    for b in benches:
        b.close()


# ── one thread does LEM I/O, and it is never the poll ────────────────────────

class TestTheUploaderThread:
    def test_the_poll_thread_never_talks_to_lem(self, bench, lem):
        """The poll runs here, on this thread. Every request LEM saw was made
        on another one — the uploader — and there were requests (an uploader
        that never ran would pass a weaker version of this test)."""
        b = bench()
        b.print_lines(("L-1", "0.8001"))
        b.poll(3)
        assert lem.requests, "nothing reached LEM at all"
        assert threading.get_ident() not in lem.lem_threads()
        assert b.m._transfer.wrong_thread == 0

    def test_a_slow_road_does_not_hold_up_the_poll(self, bench, lem):
        """The road can take its full 10 s; the poll must not wait for it."""
        b = bench()
        lem.modes = {"lan": "slow", "public": "slow"}
        lem.slow_seconds = 1.5
        started = datetime.now()
        b.m.process_now(b.now)
        took = (datetime.now() - started).total_seconds()
        assert took < 0.5, "the poll waited %.2f s for LEM" % took
        b.settle()

    def test_lem_io_off_the_uploader_thread_is_refused(self, bench, lem):
        """A programming error that calls LEM from the poll is caught and
        counted rather than allowed: that is the property, so it is enforced
        where it can be checked, not only promised."""
        b = bench()
        before = len(lem.requests)
        answer = b.m._lem("GET", "/api/v2/ping", b.now)
        assert answer is None
        assert len(lem.requests) == before
        assert b.m._transfer.wrong_thread == 1


# ── roads ────────────────────────────────────────────────────────────────────

class TestRoads:
    def test_every_request_names_itself(self, bench, lem):
        """Cloudflare answers urllib's default agent with error 1010 (A's
        probe, 2026-10-01). The fake public road does the same, so this bench
        only gets anything done because it says who it is — on every
        request, enrolment and ping included."""
        b = bench()
        b.poll(2)
        assert lem.requests
        for r in lem.requests:
            assert r["ua"].startswith("LEM-Station/%s" % mod.MODULE_VERSION), r
            assert UID in r["ua"]

    def test_lan_is_preferred_and_public_carries_it_when_lan_is_dark(
            self, bench, lem):
        b = bench()
        assert lem.road_requests("lan"), "the LAN road was never tried"
        lem.requests.clear()
        lem.modes["lan"] = "timeout"
        b.poll(40)                                  # 20 minutes
        # A dark LAN is re-probed once per 10 minutes, not once per poll:
        # in 20 minutes of polls, at 0 and 10 minutes.
        lan = lem.road_requests("lan")
        assert len(lan) == 2, [r["path"] for r in lan]
        # ... and the public road carried every sync of every poll.
        public = lem.road_requests("public", "/sync")
        assert len(public) == 40
        assert all(r["timeout"] == 1.5 for r in lan)
        assert all(r["timeout"] == 10.0 for r in lem.road_requests("public"))
        assert b.m._transfer.road == "public"

    def test_the_lan_is_taken_back_once_it_answers(self, bench, lem):
        b = bench()
        lem.modes["lan"] = "down"
        b.poll(3)
        assert b.m._transfer.road == "public"
        lem.modes["lan"] = "up"
        b.poll(25)                                  # past the 10-minute re-probe
        assert b.m._transfer.road == "lan"
        assert lem.road_requests("lan", "/sync")

    def test_both_roads_dark_backs_off_30_60_120_then_300(self, bench, lem):
        b = bench()
        lem.modes = {"lan": "down", "public": "down"}
        lem.requests.clear()
        b.poll(60)                                  # 30 minutes
        tries = sorted({r["at"] for r in b.m._transfer.attempt_log
                        if r["outcome"] == "roads_down"})
        gaps = [round(tries[i + 1] - tries[i]) for i in range(len(tries) - 1)]
        assert gaps[:4] == [30, 60, 120, 300], gaps
        assert set(gaps[4:]) <= {300}, gaps

    def test_the_bench_says_lem_is_unreachable_and_that_nothing_is_lost(
            self, bench, lem):
        """§14's bench card: a dark LEM is said, with what waits and the
        promise that is true — the journal holds it."""
        b = bench()
        lem.modes = {"lan": "down", "public": "down"}
        b.print_lines(("L-1", "0.8000"))
        b.poll(4)
        text = b.m._status_label.text()
        assert "LEM unreachable" in text and "nothing is lost" in text, text

    def test_records_wait_in_the_journal_and_drain_when_a_road_returns(
            self, bench, lem):
        b = bench()
        lem.modes = {"lan": "down", "public": "down"}
        for i in range(20):
            b.print_lines(("L-%d" % i, "0.8%03d" % i))
            b.poll()
        assert not lem.runs()
        lem.modes = {"lan": "down", "public": "up"}
        b.poll(11)                                   # ≤ 300 s of backoff
        assert sorted(r["lab_id"] for r in lem.runs()) == sorted(
            "L-%d" % i for i in range(20))
        assert lem.resent == 0


class TestOnlyA404MeansAnOldServer:
    @pytest.mark.parametrize("mode", ["timeout", "503", "down"])
    def test_a_failure_is_not_a_downgrade(self, bench, lem, lab, mode):
        """A v2 bench that cannot reach LEM keeps journaling. It must never
        conclude "old server" and start writing lem_* rows into LabCore —
        that is the exact load this release removes."""
        b = bench()
        b.poll(2)
        assert b.m._v2_active()
        lem.modes = {"lan": mode, "public": mode}
        since = lab.count()
        b.poll(10)
        assert b.m._v2_active()
        assert b.m._transfer.mode == "v2"
        assert [o for o in lab.ops[since:] if "lem_" in o[1].lower()] == []

    def test_a_404_is_an_old_server_and_v2_is_asked_again_in_15_minutes(
            self, bench, lem):
        b = bench()
        lem.modes = {"lan": "404", "public": "404"}
        b.poll(2)
        assert b.m._transfer.mode == "legacy"
        assert not b.m._v2_active()
        lem.modes = {"lan": "up", "public": "up"}
        b.poll(10)
        assert b.m._transfer.mode == "legacy"      # not yet: 5 minutes
        b.poll(25)
        assert b.m._transfer.mode == "v2"


# ── enrolment ────────────────────────────────────────────────────────────────

class TestEnrolment:
    def test_a_known_bench_enrols_once_and_keeps_its_token(
            self, bench, lem, journal_dir):
        b = bench()
        key = journal_dir / UID / mod.JOURNAL_KEY_NAME
        assert key.read_text().strip() == lem.token
        assert len(lem.road_requests(path_part="/enroll")) == 1
        b.poll(5)
        assert len(lem.road_requests(path_part="/enroll")) == 1

    def test_a_bench_lem_does_not_know_waits_for_a_person(self, bench, lem,
                                                          journal_dir):
        lem.known = False
        b = bench()
        b.poll(2)
        assert not (journal_dir / UID / mod.JOURNAL_KEY_NAME).exists()
        assert "person" in b.m._transfer.enrol or "know" in b.m._transfer.enrol
        # Waiting is not "old server": the bench holds on the v2 side (it
        # journals; nothing of LEM's goes to LabCore) until the approval.
        assert b.m._v2_active()
        assert b.m._transfer.mode != "legacy"
        assert not b.m._transfer.token
        lem.approved = True
        b.poll(12)
        assert (journal_dir / UID / mod.JOURNAL_KEY_NAME).exists()
        assert b.m._v2_active()

    def test_a_lost_enrolment_answer_is_collected_with_the_same_key(
            self, bench, lem, journal_dir, monkeypatch):
        """The server issued the token and the answer never arrived. Asking
        again with the same enroll_key collects it; a new key would make it
        "already enrolled; needs a person"."""
        real = lem.handle
        calls = {"n": 0}

        def lossy(method, path, query, headers, body):
            out = real(method, path, query, headers, body)
            if path.endswith("/enroll") and calls["n"] == 0:
                calls["n"] += 1
                raise_timeout = True
                if raise_timeout:
                    raise TimeoutError("answer lost")
            return out
        monkeypatch.setattr(lem, "handle", lossy)
        b = bench()
        b.poll(4)
        assert (journal_dir / UID / mod.JOURNAL_KEY_NAME).exists()
        assert b.m._v2_active()


# ── v2 mode costs LabCore nothing ────────────────────────────────────────────

class TestLabCoreIsLeftAlone:
    def test_idle_polls_and_pulses_cost_zero_labcore_ops(self, bench, lab):
        b = bench()
        since = lab.count()
        for k in range(120):
            b.poll()
            if k % 10 == 0:
                b.m._send_pulse(b.now)
                b.settle()
        assert lab.ops[since:] == []

    def test_the_first_poll_costs_zero(self, bench, lab):
        b = bench()
        since = lab.count()
        b.poll()
        assert lab.ops[since:] == []

    def test_a_print_costs_only_the_results_road(self, bench, lab, lem):
        b = bench()
        lab.samples.add("L-1")
        b.poll()                                   # confirm the factor
        since = lab.count()
        b.print_lines(("L-1", "0.8001"))
        b.poll()
        ops = lab.ops[since:]
        assert [o for o in ops if "lem_" in o[1].lower()] == []
        assert lab.cells[("L-1", "Density")] == "0.8001"
        assert [r["lab_id"] for r in lem.runs()] == ["L-1"]


# ── the 60 s factor rule, confirmed by the sync (D2: no replica) ─────────────

class TestFactorConfirmation:
    def test_results_are_held_while_both_roads_are_dark(self, bench, lab, lem):
        """Ryan, D2: when neither LEM road answers, results are HELD in the
        journal — not filed with a factor nobody has confirmed in 60 s — and
        the hold costs LabCore nothing."""
        b = bench()
        lab.samples.update("L-%d" % i for i in range(5))
        b.poll()
        lem.modes = {"lan": "down", "public": "down"}
        b.poll(3)                                   # the confirmation ages out
        since = lab.count()
        for i in range(5):
            b.print_lines(("L-%d" % i, "0.80%02d" % i))
            b.poll()
        assert lab.cells == {}
        assert lab.ops[since:] == []
        assert "held" in b.m._held_notice.lower()
        lem.modes = {"lan": "down", "public": "up"}
        b.poll(12)
        assert {k[0]: v for k, v in lab.cells.items()} == {
            "L-%d" % i: "0.80%02d" % i for i in range(5)}

    def test_a_factor_changed_while_dark_is_applied_before_filing(
            self, bench, lab, lem):
        """CF2's shape. The factor moves on the server while the bench
        cannot hear it; the readings in between wait, and when a road
        returns they are filed with the NEW factor."""
        b = bench()
        lab.samples.update(["L-1", "L-2"])
        b.poll()
        lem.modes = {"lan": "down", "public": "down"}
        b.poll(3)
        lem.corrections = [{"machine_uid": UID, "test_name": "Density",
                            "correction": 0.001}]
        lem.config_rev = "rev-2"
        b.print_lines(("L-1", "0.8000"), ("L-2", "0.8100"))
        b.poll()
        assert lab.cells == {}
        lem.modes = {"lan": "up", "public": "up"}
        b.poll(12)
        assert lab.cells[("L-1", "Density")] in ("0.801", "0.8010")
        assert lab.cells[("L-2", "Density")] in ("0.811", "0.8110")

    def test_a_confirmation_older_than_60_s_is_not_a_confirmation(
            self, bench, lab, lem):
        b = bench()
        b.poll()
        assert b.m._v2_factor_confirmed(b.now)
        assert not b.m._v2_factor_confirmed(b.now + timedelta(seconds=91))


# ── edits made at the bench go to LEM, not LabCore ───────────────────────────

class TestBenchEditsGoToLem:
    def test_the_setup_dialogs_save_is_a_config_record(self, bench, lab, lem):
        """A v2 bench writes nothing of its own into LabCore — its setup
        dialog's save included (it used to upsert lem_machine_config)."""
        b = bench()
        b.poll()
        since = lab.count()
        b.machine.title = "Bench, renamed"
        b.m._publish_config(b.machine)
        b.settle()
        assert lab.ops[since:] == []
        saved = [r for r in lem.records if r.get("kind") == "config"]
        assert saved and saved[-1]["machine"]["title"] == "Bench, renamed"

    def test_a_factor_saved_here_is_not_filed_until_lem_echoes_it(
            self, bench, lab, lem):
        """The operator sets a factor at the bench. LEM does not have it yet,
        so the configuration LEM confirms every sync is the one WITHOUT it:
        a result filed now would carry a factor LEM never confirmed. It waits
        for LEM's next configuration, then files with the new factor."""
        b = bench()
        lab.samples.add("L-1")
        b.poll()
        b.m._v2_save_corrections(b.machine, {"Density": 0.001})
        assert b.machine.corrections == {"Density": 0.001}
        b.print_lines(("L-1", "0.8000"))
        b.poll()
        assert lab.cells == {}, "filed before LEM confirmed the new factor"
        b.poll(3)
        assert lab.cells[("L-1", "Density")] in ("0.801", "0.8010")
        sent = [r for r in lem.records if r.get("kind") == "config"]
        assert sent[-1]["corrections"] == {"Density": 0.001}

    def test_an_override_set_here_reaches_lem_as_a_record(self, bench, lab,
                                                            lem):
        b = bench()
        b.poll()
        since = lab.count()
        b.m._log_event("override", detail={"status": "SERVICE",
                                           "comment": "lamp out"})
        b.settle()
        assert lab.ops[since:] == []
        assert [r["detail"]["status"] for r in lem.records
                if r.get("kind") == "override"] == ["SERVICE"]


# ── blind mode (journal wiped) ───────────────────────────────────────────────

class TestBlindMode:
    def _wipe(self, journal_dir):
        import shutil
        shutil.rmtree(journal_dir / UID)

    def test_a_wiped_journal_waits_for_lem_then_resumes_without_resending(
            self, bench, lem, lab, journal_dir):
        """T4's shape, at the bench. The journal folder is deleted (bench.key
        and cursor with it) and the canvas still says this bench had a v2
        journal. It must not read its file from the top while LEM is dark;
        once LEM answers (and a person has approved the re-enrolment) it
        resumes at the cursor LEM mirrored, and re-sends nothing."""
        b = bench()
        lab.configs[UID] = __import__("json").dumps(b.machine.to_dict())
        b.print_lines(*[("L-%d" % i, "0.8%03d" % i) for i in range(10)])
        b.poll(3)
        assert len(lem.runs()) == 10
        state = b.m.serialize_state()
        b.close()
        self._wipe(journal_dir)
        lem.modes = {"lan": "down", "public": "down"}
        b2 = bench(state=state)
        b2.print_lines(("L-new", "0.9000"))
        b2.k = b.k
        b2.poll(3)
        assert b2.m._transfer_blind()
        assert len(lem.runs()) == 10              # nothing read meanwhile
        lem.modes = {"lan": "up", "public": "up"}
        lem.approved = True                        # a person lets it re-enrol
        b2.poll(12)
        assert not b2.m._transfer_blind()
        labs = sorted(r["lab_id"] for r in lem.runs())
        assert labs == sorted(["L-%d" % i for i in range(10)] + ["L-new"])
        assert lem.resent == 0

    def test_serial_readings_are_kept_while_blind(self, bench, lem, lab,
                                                  journal_dir, tmp_path):
        """Blind mode holds back a FILE source, which still has the bytes. A
        serial frame has no other copy, so it is journaled regardless."""
        b = bench(source_type="serial")
        lab.configs[UID] = __import__("json").dumps(b.machine.to_dict())
        state = b.m.serialize_state()
        b.close()
        self._wipe(journal_dir)
        lem.modes = {"lan": "down", "public": "down"}
        b2 = bench(state=state, source_type="serial")
        b2.m._ingest = lambda machine: (machine, ["S-1,0.8000"], None)
        b2.poll()
        journal = b2.m._journal_for(b2.m.machine())
        runs = [r["rec"] for r in journal.open_runs()]
        assert [r.get("lab_id") for r in runs] == ["S-1"]


# ── config.json and retirement ───────────────────────────────────────────────

class TestConfigCacheAndRetirement:
    def test_a_restart_binds_from_config_json_not_labcore(self, bench, lab):
        b = bench()
        b.poll(2)
        state = b.m.serialize_state()
        b.close()
        since = lab.count()
        b2 = bench(state=state)
        assert b2.m.machine() is not None
        assert b2.m.machine().uid == UID
        b2.poll()
        assert lab.ops[since:] == []

    def test_an_outage_never_retires_a_bench(self, bench, lem):
        b = bench()
        lem.modes = {"lan": "down", "public": "down"}
        b.poll(30)
        assert b.m.machine() is not None

    def test_retired_is_said_explicitly_and_then_the_bench_stops(
            self, bench, lem):
        b = bench()
        b.poll()
        lem.retired = True
        b.poll(2)
        assert b.m.machine() is None
        assert "retired" in b.m._status_label.text().lower()


# ── round 2: a restart during the outage, and "unknown" is not "legacy" ─────

class TestARestartDuringTheOutage:
    def test_readings_journaled_before_a_restart_file_with_the_new_factor(
            self, bench, lab, lem):
        """CF2 with one LabStation restart while both roads are still dark.

        The readings made after the factor moved on the server are HELD
        (D2). The restart brings them back from the journal — and they come
        back carrying the correction they were PARSED with, the old one. If
        the recovered queue is filed as it stands, half the readings (the
        ones journaled before the restart) reach LabCore with a factor LEM
        had already replaced: a wrong result, silently. Every held reading
        must be re-corrected from its raw value with the factor LEM confirms
        when the road returns, whichever process journaled it."""
        b = bench()
        lab.samples.update("L-%d" % i for i in range(12))
        b.poll()
        lem.modes = {"lan": "down", "public": "down"}
        b.poll(3)
        lem.corrections = [{"machine_uid": UID, "test_name": "Density",
                            "correction": 0.001}]
        lem.config_rev = "rev-2"
        for i in range(6):                      # before the restart
            b.print_lines(("L-%d" % i, "0.8003"))
            b.poll()
        state = b.m.serialize_state()
        k = b.k
        b.close()
        b2 = bench(state=state)
        b2.k = k
        for i in range(6, 12):                  # after it
            b2.print_lines(("L-%d" % i, "0.8003"))
            b2.poll()
        assert lab.cells == {}, "filed while both roads were dark"
        lem.modes = {"lan": "up", "public": "up"}
        b2.poll(12)
        got = {k[0]: v for k, v in lab.cells.items()}
        stale = sorted(lab_id for lab_id, v in got.items()
                       if abs(float(v) - 0.8003) < 1e-9)
        assert stale == [], "filed with the OLD factor: %s" % stale
        assert got == {"L-%d" % i: "0.8013" for i in range(12)}


class TestUnknownIsNotLegacy:
    """§6.1 / §12.2: only a 404 means "old server". A bench whose v2 state
    is merely UNKNOWN — it bound while both roads were down, or LEM answered
    503, or its journal was wiped and it is waiting for a person to approve
    its re-enrolment — has not been told it talks to an old server, so it
    must not start doing an old server's bookkeeping in LabCore (heartbeat,
    status, DDL, log rows, config reads). It journals and holds, exactly as
    a v2 bench does in an outage; the first 404 is what sends it the old
    way, and then it projects what it journaled, losing nothing."""

    LEM_ONLY = ("lem_", "create table", "create index", "alter table")

    def _lem_ops(self, ops):
        """LEM's bookkeeping in LabCore — minus the ONE read §6.4 allows: the
        shared token in lem_meta that proves a known bench's first enrolment
        (made on the uploader thread, once, when LEM first answers)."""
        out = [o for o in ops if any(w in o[1].lower() for w in self.LEM_ONLY)]
        enrol = [o for o in out if o[0] == "read" and "lem_meta" in o[1].lower()]
        assert len(enrol) <= 1, enrol
        return [o for o in out if o not in enrol]

    @pytest.mark.parametrize("mode", ["down", "503", "timeout"])
    def test_a_bench_that_binds_with_lem_unreachable_writes_nothing(
            self, bench, lab, lem, mode):
        lem.modes = {"lan": mode, "public": mode}
        lab.samples.update("L-%d" % i for i in range(10))
        b = bench()
        since = lab.count()
        for i in range(10):
            b.print_lines(("L-%d" % i, "0.8000"))
            b.poll()
        assert b.m._transfer.mode != "legacy"
        assert lab.ops[since:] == [], lab.ops[since:]
        lem.modes = {"lan": "up", "public": "up"}
        b.poll(12)
        assert b.m._transfer.mode == "v2"
        assert self._lem_ops(lab.ops[since:]) == []
        assert sorted(r["lab_id"] for r in lem.runs()) == sorted(
            "L-%d" % i for i in range(10))
        assert {k[0] for k in lab.cells} == {"L-%d" % i for i in range(10)}

    def test_a_bench_waiting_for_approval_writes_nothing(self, bench, lab,
                                                         lem, journal_dir):
        """T4's wiped bench while its re-enrolment waits for a person: LEM
        answered (202), so it is certainly not an old server."""
        lem.known = False
        lab.samples.update(["L-1", "L-2"])
        b = bench()
        since = lab.count()
        b.print_lines(("L-1", "0.8000"), ("L-2", "0.8100"))
        b.poll(8)
        assert b.m._transfer.enrol
        assert lab.ops[since:] == [], lab.ops[since:]
        lem.approved = True
        b.poll(12)
        assert self._lem_ops(lab.ops[since:]) == []
        assert sorted(r["lab_id"] for r in lem.runs()) == ["L-1", "L-2"]
        assert {k[0] for k in lab.cells} == {"L-1", "L-2"}

    def test_the_first_404_sends_what_was_journaled_the_old_way(
            self, bench, lab, lem):
        """The other half of the rule: an unknown bench that then hears a 404
        goes legacy and projects every log row it journaled meanwhile —
        holding while unknown never loses a row on an old server."""
        lem.modes = {"lan": "down", "public": "down"}
        lab.samples.update(["L-1", "L-2"])
        b = bench()
        b.print_lines(("L-1", "0.8000"), ("L-2", "0.8100"))
        b.poll(3)
        lem.modes = {"lan": "404", "public": "404"}
        b.poll(12)
        assert b.m._transfer.mode == "legacy"
        logged = [o for o in lab.ops if "lem_machine_log" in o[1].lower()
                  and o[0] in ("sql", "write")
                  and "insert" in o[1].lower()]
        assert logged, "the journaled rows never reached LabCore"
        assert {k[0] for k in lab.cells} == {"L-1", "L-2"}

    def test_what_the_operator_did_while_unknown_reaches_an_old_server(
            self, bench, lab, lem):
        """Holding while unknown is only safe if a later 404 sends EVERYTHING
        the old way — not just the parsed runs. While LEM was unreachable the
        operator wrote a note, set an override and saved the machine's setup;
        on the v2 side those are journal records. When LEM turns out to be
        v3.9 they must reach LabCore as v3.9 would have written them: the log
        rows, the configuration row, and the status and specs the legacy sync
        publishes (the v2 side had already "published" those to its journal,
        so the legacy side must not think they are done). Exactly once: a
        second fall-back must not write them again."""
        lem.modes = {"lan": "down", "public": "down"}
        b = bench()
        b.poll()
        b.m._log_event("comment", detail={"note": "lamp replaced"})
        b.m._log_event("override", detail={"status": "SERVICE",
                                           "comment": "lamp out"})
        b.machine.title = "Bench, renamed"
        b.m._publish_config(b.machine)
        b.settle()
        b.poll(2)
        assert [o for o in lab.ops if "lem_machine_log" in o[1].lower()] == []
        lem.modes = {"lan": "404", "public": "404"}
        b.poll(12)
        assert b.m._transfer.mode == "legacy"
        args = [a for a in lab.log_args if a[2] in ("comment", "override")]
        assert sorted(a[2] for a in args) == ["comment", "override"], lab.log_args
        assert any("lem_machine_config" in o[1] and "INSERT" in o[1].upper()
                   for o in lab.ops)
        assert any("lem_machine_status" in o[1] and "INSERT" in o[1].upper()
                   for o in lab.ops)
        assert any("lem_machine_specs" in o[1] for o in lab.ops)
        # a second fall-back (v2 found, then rolled back) sends nothing again
        n = len(args)
        b.m._v2_fell_back(b.m.machine(), b.m._upl_journal, [])
        b.poll(2)
        assert len([a for a in lab.log_args
                    if a[2] in ("comment", "override")]) == n


class TestTheLegacyRoadNamesItselfToo:
    """§6.1 / spec row 96: EVERY module request to LEM sets the User-Agent —
    not only the v2 uploader's. A bench on the legacy road (an old server)
    still pushes /api/live and reads /api/bench/<uid>/config, and through the
    public road Cloudflare answers urllib's default agent with 1010; an
    unnamed request there is a request that never arrives."""

    def _capture(self, monkeypatch):
        seen = []

        def urlopen(req, timeout=None, **kw):
            seen.append(req.get_header("User-agent") or "")
            raise OSError("captured, not sent")
        monkeypatch.setattr("urllib.request.urlopen", urlopen)
        return seen

    def test_the_live_push_names_itself(self, monkeypatch):
        seen = self._capture(monkeypatch)
        mod.post_live("https://lem.asaplabs.net", "t", {"machine_uid": "m9"})
        assert seen == [mod.lem_user_agent("m9")]

    def test_the_floor_config_read_names_itself(self, monkeypatch):
        seen = self._capture(monkeypatch)
        mod.fetch_floor_config("https://lem.asaplabs.net", "t", "m9")
        assert seen == [mod.lem_user_agent("m9")]


class TestALongOutageLosesNoResult:
    def test_more_held_results_than_the_backlog_cap_all_file(
            self, bench, lab, lem, monkeypatch):
        """D2 holds results for as long as LEM is dark — an outage over a long
        weekend holds thousands of them. The identity backlog used to drop its
        OLDEST past a 5,000 cap, a silent loss for results D2 promised to
        file; that cap is retired (§3.2), and this pins that it stays so: far
        more held results than one poll's identity ceiling (here 2 chunks of
        5) all file once a road returns, none lost."""
        monkeypatch.setattr(mod, "IDENTITY_LOOKUP_CHUNK", 5)
        b = bench()
        labs = ["L-%02d" % i for i in range(60)]
        lab.samples.update(labs)
        b.poll()
        lem.modes = {"lan": "down", "public": "down"}
        b.poll(3)
        for i in range(0, 60, 6):
            b.print_lines(*[(x, "0.8000") for x in labs[i:i + 6]])
            b.poll()
        assert lab.cells == {}
        lem.modes = {"lan": "up", "public": "up"}
        b.poll(30)
        assert sorted(k[0] for k in lab.cells) == labs

    def test_and_a_restart_in_the_middle_of_it_loses_none_either(
            self, bench, lab, lem, monkeypatch):
        """The same, with LabStation restarted while still dark: the held
        results come back from the journal, and must come back into the
        results road, every one of them, however many polls the identity
        ceiling makes the drain take."""
        monkeypatch.setattr(mod, "IDENTITY_LOOKUP_CHUNK", 5)
        b = bench()
        labs = ["L-%02d" % i for i in range(60)]
        lab.samples.update(labs)
        b.poll()
        lem.modes = {"lan": "down", "public": "down"}
        b.poll(3)
        for i in range(0, 60, 6):
            b.print_lines(*[(x, "0.8000") for x in labs[i:i + 6]])
            b.poll()
        state, k = b.m.serialize_state(), b.k
        b.close()
        b2 = bench(state=state)
        b2.k = k
        b2.poll(2)
        assert lab.cells == {}
        lem.modes = {"lan": "up", "public": "up"}
        b2.poll(30)
        assert sorted(k[0] for k in lab.cells) == labs
