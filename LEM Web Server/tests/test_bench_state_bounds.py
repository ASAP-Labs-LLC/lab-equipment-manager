"""What a v2 sync's RECORDS write into current state reaches the floor only
within bounds.

Round 2 bounded what a sync says ABOUT the bench (`stats`, the `live` block)
and called the hole closed. It was closed for those two roads only. A sync's
records have their own road into the same payload: `Ingest._state_state`
wrote a `state` record's `status`, `reason` and `sub.*` into
`lem_machine_status` / `lem_machine_substatus` exactly as sent, and
`/api/machines` serves those back as `status`, `reason` and `sub_statuses`.
The critic measured it over real HTTP: one gzipped sync of 4,284 B on the
wire, carrying a `sub.qc` of 4,000,000 characters, was answered 200 and made
`/api/machines` 4,000,725 B. GC hub's own `lem_machines.fetch` then failed
with "answer too large" (its cap is 1 MB) — the whole Instruments page, not
one machine. And the bad value stayed in the store across restarts until the
next state record overwrote it.

Looking for every other road of the same shape found four more, all reachable
from one sync:

* a record's `ts` that is not a date: `wall_ts` returned the raw text, which
  became `updated_at` (and so `last_activity`, read as MAX(ts) of the log);
* a `run`/`qc` record's `log` rows carried their own `ts` verbatim;
* the `live` block's `at`, which became the heartbeat's `last_poll`;
* a `specs` record: any number of specs, any length of name, and numbers that
  were not numbers (SQLite turns a 4,000-digit "number" into Infinity, and
  Infinity is not JSON).

Plus one the floor builds itself: `last_qc_superseded_by` is the `lab_id` of
a QC verdict out of the log, and a log row's `lab_id` is the bench's text.

The rule now is one sentence: **what a record puts in current state has the
same bound as the live block that carries the same fact**, applied where the
record enters (so the store holds the bounded value, and a restart reopens
nothing), and again where `/api/machines` is built (so a row an older build
or a v3.9 bench left there cannot cross either). The record itself is never
cut: `bench_record` holds the body exactly as sent, and the machine log row a
person reads still says the whole reason.

A spec set is different from a status: a spec's test name and sample are KEYS
the QC check matches on, and a clipped key is a band for a test nobody runs.
So a spec set that is over its bounds is not cut and not applied — the bench's
previous set stays in force, the record is parked in `unknown_records` with
the reason, and the sync answer's `notes` says so. The record is still held
(acked), so the bench is not wedged resending it.
"""
import gzip
import json
import math
import os
import threading
import urllib.request

import pytest

import bench_v2_kit as kit
from bench_v2_kit import Bench, UID
from labcore_counter import CountingLabCore
from labcore_gateway import FakeLabCoreGateway

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "machines_live_2026-10-01.json")
GC_HUB_MAX_BYTES = 1024 * 1024
PORT = int(os.environ.get("LEM_BENCH_BOUNDS_PORT", "5703"))
PER_MACHINE_BUDGET = 1300            # the transfer spec's <= 1.3 KB

# Production's longest reason on 2026-10-01 (two QC series, 148 characters).
REAL_REASON = ("QC out of spec: ASTM D2887/D86 - Distillation in Petroleum "
               "Products, 10% Recovery, ASTM D2887/D86 - Distillation in "
               "Petroleum Products, 50% Recovery")
REAL_TEST = "ASTM D2887/D86 - Distillation in Petroleum Products, 10% Recovery"


def _compact(obj) -> bytes:
    # Flask's own JSON: compact separators, sorted keys, ASCII.
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode()


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


def _entry(client, uid=UID):
    ms = client.get("/api/machines?fresh=1").get_json()["machines"]
    return [m for m in ms if m["machine_uid"] == uid][0]


def _state(**fields):
    def make(seq, epoch, uid=UID):
        body = {"seq": seq, "epoch": epoch, "uid": uid, "kind": "state",
                "ts": "2026-10-01T09:00:00-07:00", "module": "4.0.0"}
        body.update(fields)
        return body
    return make


def _specs(specs, ts="2026-10-01T09:00:00-07:00"):
    def make(seq, epoch, uid=UID):
        return {"seq": seq, "epoch": epoch, "uid": uid, "kind": "specs",
                "ts": ts, "module": "4.0.0", "specs": specs}
    return make


def _spec(name=REAL_TEST, **over):
    s = {"test_name": name, "sample_id": "AF26", "expected": 187.63,
         "std_dev": 1.29, "k": 2.0, "units": "C", "low": 185.05,
         "high": 190.21, "last_qc_at": "2026-09-30T15:04:24.712184",
         "last_qc_value": 191.83, "last_qc_in_spec": 0, "correction": 0.0}
    s.update(over)
    return s


#: A live block that does NOT carry a status, so the floor shows the
#: RECORD's state — the road under test. (A live status would overlay it,
#: and the live block is bounded on its own: test_bench_input_bounds.)
NO_LIVE = {"interval_seconds": 30}

HOSTILE_EMOJI = "\U0001F600" * 30_000     # 12 B each in ASCII JSON
HUGE = "q" * 200_000


def _send(client, bench, make, n=1, **extra):
    bench.journal(n, make=make)
    r = bench.sync(live=extra.pop("live", NO_LIVE), **extra)
    assert r.status_code == 200, r.get_json()
    return r


# ── the state record ─────────────────────────────────────────────────────────

class TestTheStateRecord:
    def test_an_ordinary_state_record_is_stored_exactly(self, client, store):
        """Bounds that change real data are a different bug. Production's
        longest reason, its status words and a real timestamp go through
        untouched."""
        b = Bench(client, kit.enroll(client))
        _send(client, b, _state(status="RED", reason=REAL_REASON,
                                sub={"qc": "RED", "pm": "GREEN",
                                     "calibration": "UNKNOWN"}))
        st = store.read_sql("SELECT status, reason, updated_at FROM "
                            "lem_machine_status WHERE machine_uid = ?",
                            [UID])["rows"][0]
        assert st == {"status": "RED", "reason": REAL_REASON,
                      "updated_at": "2026-10-01T09:00:00"}
        e = _entry(client)
        assert e["sub_statuses"] == {"qc": "RED", "pm": "GREEN",
                                     "calibration": "UNKNOWN"}
        assert e["reason"] == REAL_REASON and e["status"] == "RED"

    def test_every_field_is_bounded_by_its_encoded_size_in_the_store(
            self, client, store):
        """The critic's field, and its neighbours, each as long as a 256 KB
        body allows, in the characters that cost most in ASCII JSON."""
        from live_presence import LIVE_FIELD_BYTES
        b = Bench(client, kit.enroll(client))
        _send(client, b, _state(status=HOSTILE_EMOJI[:3000],
                                reason=HOSTILE_EMOJI[:3000],
                                sub={"qc": HOSTILE_EMOJI[:3000],
                                     "pm": "é" * 5000,
                                     "calibration": {"nested": "x" * 5000}},
                                ts="2" * 20_000))
        st = store.read_sql("SELECT status, reason, updated_at FROM "
                            "lem_machine_status WHERE machine_uid = ?",
                            [UID])["rows"][0]
        sub = store.read_sql("SELECT qc, pm, calibration FROM "
                             "lem_machine_substatus WHERE machine_uid = ?",
                             [UID])["rows"][0]

        def cost(s):
            return len(json.dumps(s)) - 2

        assert cost(st["status"]) <= LIVE_FIELD_BYTES["status"]
        assert cost(st["reason"]) <= LIVE_FIELD_BYTES["reason"]
        assert st["reason"].endswith("…")          # it says it was cut
        for k in ("qc", "pm", "calibration"):
            assert sub[k] and cost(sub[k]) <= LIVE_FIELD_BYTES["status"], k
        # A ts that is not a date is not a time: the server's own clock,
        # never the bench's text.
        from datetime import datetime
        datetime.fromisoformat(st["updated_at"])
        assert len(st["updated_at"]) <= 26

    def test_the_record_itself_keeps_every_character(self, client, store):
        """Custody is not current state. `bench_record` holds the body as
        sent, and the machine log a person reads keeps the whole reason."""
        b = Bench(client, kit.enroll(client))
        reason = "r" * 50_000
        _send(client, b, _state(status="RED", reason=reason,
                                sub={"qc": "Q" * 9000}))
        body = json.loads(store.read_sql(
            "SELECT body FROM bench_record WHERE machine_uid = ?",
            [UID])["rows"][0]["body"])
        assert body["reason"] == reason and body["sub"]["qc"] == "Q" * 9000
        detail = json.loads(kit.log_rows(store)[0]["detail"])
        assert detail["reason"] == reason

    def test_the_critics_reproduction_answers_200_and_stays_small(
            self, client, store, lab):
        """4,000,000 characters of sub.qc, gzipped to ~4 KB: answered 200
        (the record is good; refusing it would wedge the bench), stored
        bounded, and the machine's entry is within the 1.3 KB budget."""
        b = Bench(client, kit.enroll(client))
        b.journal(1, make=_state(status="RED", reason=REAL_REASON,
                                 sub={"qc": "q" * 4_000_000, "pm": "GREEN",
                                      "calibration": "GREEN"}))
        raw = json.dumps(b.body(live=NO_LIVE)).encode()
        wire = gzip.compress(raw)
        assert len(wire) < 8 * 1024 and len(raw) > 4_000_000 - 1
        r = client.post("/api/v2/bench/%s/sync" % UID, data=wire,
                        headers={"X-LEM-Bench-Token": b.token,
                                 "Content-Encoding": "gzip",
                                 "Content-Type": "application/json",
                                 "X-LEM-Proto": "2"})
        assert r.status_code == 200, r.get_json()
        e = _entry(client)
        assert len(_compact(e)) <= PER_MACHINE_BUDGET, len(_compact(e))
        assert lab.ops == 0


# ── the timestamps ───────────────────────────────────────────────────────────

class TestTimestamps:
    @pytest.mark.parametrize("sent", [
        "2" * 20_000, "not a date", "2026-13-45T99:00:00", "\U0001F600" * 10,
        12, ["2026-10-01"]], ids=lambda v: repr(v)[:16])
    def test_a_ts_that_is_not_a_date_is_the_fallback(self, sent):
        from bench_api import wall_ts
        assert wall_ts(sent, "FALLBACK") == "FALLBACK"

    @pytest.mark.parametrize("sent,held", [
        ("2026-10-01T09:00:00-07:00", "2026-10-01T09:00:00"),
        ("2026-10-01T09:00:00.348665", "2026-10-01T09:00:00.348665"),
        ("2026-10-01T16:00:00Z", "2026-10-01T16:00:00"),
        ("", "FALLBACK"), (None, "FALLBACK")])
    def test_a_real_ts_is_unchanged(self, sent, held):
        from bench_api import wall_ts
        assert wall_ts(sent, "FALLBACK") == held

    def test_a_log_rows_own_ts_is_a_date_or_the_records(self, client, store):
        """`log` rows carried their ts verbatim, and `last_activity` is
        MAX(ts) of the log: a 200 KB "ts" of nines sorts last and wins."""
        b = Bench(client, kit.enroll(client))

        def make(seq, epoch, uid=UID):
            return {"seq": seq, "epoch": epoch, "uid": uid, "kind": "qc",
                    "ts": "2026-10-01T09:00:00-07:00", "module": "4.0.0",
                    "log": [[uid, "9" * 200_000, "qc", "L-1", "Flash", "41.2",
                             "{}"],
                            [uid, "2026-10-01 08:59:58", "qc", "L-1", "Sulfur",
                             "9.8", "{}"]]}
        _send(client, b, make)
        ts = sorted(r["ts"] for r in kit.log_rows(store))
        # the real one exactly as v3.9 wrote it; the other the record's own
        assert ts == ["2026-10-01 08:59:58", "2026-10-01T09:00:00"]
        assert len(_compact(_entry(client)["last_activity"])) < 40

    def test_the_live_blocks_at_is_a_date_or_now(self, client, store):
        """`live.at` becomes the heartbeat's `last_poll`."""
        b = Bench(client, kit.enroll(client))
        _send(client, b, _state(status="GREEN"),
              live={"interval_seconds": 30, "at": "7" * 100_000})
        beat = store.read_sql("SELECT last_poll FROM lem_machine_heartbeat "
                              "WHERE machine_uid = ?", [UID])["rows"][0]
        assert len(beat["last_poll"]) <= 26
        assert len(_compact(_entry(client))) <= PER_MACHINE_BUDGET


# ── the specs record ─────────────────────────────────────────────────────────

def _held_specs(store):
    return store.read_sql("SELECT * FROM lem_machine_specs WHERE "
                          "machine_uid = ? ORDER BY test_name", [UID])["rows"]


class TestTheSpecsRecord:
    def test_an_ordinary_spec_set_is_applied_exactly(self, client, store):
        b = Bench(client, kit.enroll(client))
        _send(client, b, _specs([_spec()]))
        row = _held_specs(store)[0]
        for k, v in _spec().items():
            assert row[k] == v, k
        es = _entry(client)["effective_specs"]
        assert [s["test_name"] for s in es] == [REAL_TEST]

    def test_numbers_that_are_not_finite_numbers_are_not_said(
            self, client, store):
        """SQLite's REAL affinity turns a 4,000-digit "number" into
        Infinity; Python's json reads `Infinity` and `NaN` directly. None of
        them is a band edge. Each is stored as NULL ("not said"); the
        payload stays valid JSON."""
        b = Bench(client, kit.enroll(client))
        bad = _spec(expected="9" * 4000, low=math.inf, high=math.nan,
                    std_dev="wide", k=True, last_qc_value=-math.inf,
                    last_qc_in_spec="yes", correction=[1])
        doc_spec = _specs([bad])
        b.journal(1, make=doc_spec)
        raw = json.dumps(b.body(live=NO_LIVE))      # allow_nan
        r = client.post("/api/v2/bench/%s/sync" % UID, data=raw,
                        content_type="application/json",
                        headers={"X-LEM-Bench-Token": b.token})
        assert r.status_code == 200, r.get_json()
        row = _held_specs(store)[0]
        for k in ("expected", "low", "high", "std_dev", "k", "last_qc_value",
                  "last_qc_in_spec", "correction"):
            assert row[k] is None, (k, row[k])
        text = client.get("/api/machines?fresh=1").get_data(as_text=True)
        json.loads(text, parse_constant=lambda c: pytest.fail(c))

    @pytest.mark.parametrize("why,specs", [
        ("too many", [_spec("T%03d" % i) for i in range(500)]),
        ("a name too long", [_spec("T" * 5000)]),
        ("a sample too long", [_spec(sample_id="S" * 5000)]),
        ("a name that is not text", [_spec({"t": 1})]),
    ])
    def test_a_set_over_its_bounds_is_parked_not_cut(
            self, client, store, why, specs):
        """Test name and sample are the keys QC matches on. A clipped key is
        a band for a test nobody runs, so the set is not cut and not
        applied: the previous set stays in force, the record is held (the
        bench is not wedged) and parked with the reason, and the answer says
        so."""
        b = Bench(client, kit.enroll(client))
        _send(client, b, _specs([_spec()]))
        b.journal(1, make=_specs(specs))
        r = b.sync(live=NO_LIVE)
        assert r.status_code == 200 and r.get_json()["acked"] == 2
        assert [s["test_name"] for s in _held_specs(store)] == [REAL_TEST]
        notes = [n for n in r.get_json()["notes"] if n.get("kind") == "parked"]
        assert notes and notes[0]["seq"] == 2 and "spec" in notes[0]["why"]
        parked = store.read_sql("SELECT bench_seq FROM unknown_records WHERE "
                                "machine_uid = ?", [UID])["rows"]
        assert parked == [{"bench_seq": 2}]

    def test_units_are_display_only_and_are_clipped(self, client, store):
        from bench_api import SPEC_TEXT_BYTES
        b = Bench(client, kit.enroll(client))
        _send(client, b, _specs([_spec(units="\U0001F600" * 5000)]))
        units = _held_specs(store)[0]["units"]
        assert units and len(json.dumps(units)) - 2 <= SPEC_TEXT_BYTES["units"]

    def test_the_largest_spec_set_allowed_is_the_fleets_with_room(self):
        """Production's busiest bench publishes 18 specs (snapshot_service's
        own note); a SimDist cut list is ~21. The cap is well above that."""
        from bench_api import MAX_SPECS
        assert MAX_SPECS >= 40


# ── what the floor builds from the log ──────────────────────────────────────

class TestTheFloorBuilder:
    def test_a_superseding_lab_id_from_the_log_is_clipped(self, client, store):
        """`last_qc_superseded_by` is the lab_id of the QC verdict the
        bench's cached reading was made against — a log row's text."""
        from live_presence import LIVE_FIELD_BYTES
        b = Bench(client, kit.enroll(client))
        ts = "2026-10-01T09:00:00"
        _send(client, b, _specs([_spec(last_qc_at=ts)]))

        def qc(seq, epoch, uid=UID):
            return {"seq": seq, "epoch": epoch, "uid": uid, "kind": "qc",
                    "ts": ts, "module": "4.0.0", "lab_id": "L" * 100_000,
                    "test_name": REAL_TEST, "value": "191.83",
                    "verdict": "FAIL"}
        _send(client, b, qc)
        es = _entry(client)["effective_specs"][0]
        assert es["last_qc_superseded_by"].startswith("LLLL")
        assert len(json.dumps(es["last_qc_superseded_by"])) - 2 <= \
            LIVE_FIELD_BYTES["lab_id"]

    def test_a_row_an_older_writer_left_is_bounded_on_the_way_out(
            self, client, store):
        """A v3.9 bench writes these tables through LabCore with no bound,
        and a row written before this build is already there. The floor
        builder clips what it echoes, so no writer can cross GC hub's cap."""
        from bench_api import ensure_current_state_tables
        assert ensure_current_state_tables(store)
        for sql, args in (
                ("UPDATE lem_machine_status SET status = ?, reason = ?, "
                 "updated_at = ? WHERE machine_uid = ?",
                 ["S" * 90_000, "R" * 90_000, "U" * 90_000, UID]),
                ("INSERT INTO lem_machine_substatus (machine_uid, qc, pm, "
                 "calibration, updated_at) VALUES (?, ?, ?, ?, '')",
                 [UID, "Q" * 90_000, "P" * 90_000, "C" * 90_000]),
                ("INSERT INTO lem_machine_heartbeat (machine_uid, last_poll, "
                 "watching) VALUES (?, ?, ?)", [UID, "9" * 90_000,
                                                "W" * 90_000])):
            res = store.sql(sql, args)
            assert "error" not in res, res
        e = _entry(client)
        assert len(_compact(e)) <= 2 * PER_MACHINE_BUDGET, len(_compact(e))
        assert e["reason"].endswith("…")
        assert e["sub_statuses"]["qc"].startswith("QQQ")


# ── the whole floor, and GC hub's own fetch over real HTTP ──────────────────

def _worst_state_entry_growth():
    """What one machine's entry can grow by through the state road, from
    the code's own bounds: status, reason, three sub-statuses, updated_at
    (also last_activity when the log is empty)."""
    from live_presence import LIVE_FIELD_BYTES
    return (LIVE_FIELD_BYTES["status"] * 4 + LIVE_FIELD_BYTES["reason"]
            + 2 * 26)


class TestTheWholeFloor:
    def test_every_real_machine_at_its_worst_still_fits_gc_hubs_cap(self):
        """All 17 machines of production's /api/machines, each carrying the
        worst state the code can store AND the largest spec set it will
        apply, every field at its bound in the dearest characters. Measured
        against GC hub's 1 MB cap with the margin printed."""
        from bench_api import MAX_SPECS, SPEC_TEXT_BYTES
        from live_presence import LIVE_FIELD_BYTES, clip_text
        with open(FIXTURE, encoding="utf-8") as f:
            live = json.load(f)
        machines = live["machines"]
        e = "\U0001F600" * 1000

        def worst(n):
            return clip_text(e, n)
        worst_spec = {"test_name": worst(SPEC_TEXT_BYTES["test_name"]),
                      "sample_id": worst(SPEC_TEXT_BYTES["sample_id"]),
                      "units": worst(SPEC_TEXT_BYTES["units"]),
                      "low": -1.7976931348623157e308,
                      "high": -1.7976931348623157e308,
                      "expected": -1.7976931348623157e308,
                      "correction": -1.7976931348623157e308,
                      "last_qc_value": -1.7976931348623157e308,
                      "last_qc_at": "2026-10-01T09:00:00.000001",
                      "last_qc_in_spec": False,
                      "last_qc_superseded_by": worst(LIVE_FIELD_BYTES["lab_id"])}
        for m in machines:
            m["status"] = worst(LIVE_FIELD_BYTES["status"])
            m["reason"] = worst(LIVE_FIELD_BYTES["reason"])
            m["sub_statuses"] = {k: worst(LIVE_FIELD_BYTES["status"])
                                 for k in ("qc", "pm", "calibration")}
            m["effective_specs"] = [dict(worst_spec) for _ in range(MAX_SPECS)]
        total = len(_compact(live))
        # Measured 2026-10-02 at MAX_SPECS 48: 601,294 B for the 17 machines
        # (35,370 B each) — inside the cap with 447,282 B to spare.
        assert total < GC_HUB_MAX_BYTES, total
        assert total / len(machines) < 40_000, total / len(machines)


@pytest.fixture
def served(store, lab):
    """A real threaded werkzeug server on this piece's port, so GC hub's
    real fetch (its own socket, its own size cap) reads the answer."""
    from werkzeug.serving import make_server
    app = kit.make_app(store, labcore=lab)
    try:
        srv = make_server("127.0.0.1", PORT, app, threaded=True)
    except OSError as exc:            # pragma: no cover - environment
        pytest.fail("port %d is busy, so GC hub's fetch could not be run: %s"
                    % (PORT, exc))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    srv.base = "http://127.0.0.1:%d" % PORT
    srv.client = app.test_client()
    yield srv
    srv.shutdown()
    t.join(5)


class TestGcHubOverRealHttp:
    def test_the_critics_gzipped_sync_leaves_gc_hubs_fetch_working(
            self, served, lab):
        from test_machines_transfer import _gc_hub_parser
        gc = _gc_hub_parser()
        b = Bench(served.client, kit.enroll(served.client))
        # Every field the state road writes, as large as the 4 MB inflated cap
        # allows between them, in the dearest characters for the bigger ones.
        b.journal(1, make=_state(status="RED", reason="é" * 50_000,
                                 sub={"qc": "q" * 2_900_000,
                                      "pm": "\U0001F600" * 20_000,
                                      "calibration": "c" * 100_000},
                                 ts="9" * 100_000))
        raw = json.dumps(b.body(live={"interval_seconds": 30,
                                      "at": "8" * 100_000})).encode()
        assert len(raw) <= 4 * 1024 * 1024, len(raw)
        wire = gzip.compress(raw)
        assert len(wire) < 64 * 1024, len(wire)
        req = urllib.request.Request(
            served.base + "/api/v2/bench/%s/sync" % UID, data=wire,
            method="POST", headers={"X-LEM-Bench-Token": b.token,
                                    "Content-Encoding": "gzip",
                                    "Content-Type": "application/json",
                                    "X-LEM-Proto": "2"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            assert resp.status == 200
            assert json.loads(resp.read())["acked"] == 1
        # GC hub reads the cached snapshot; build it once, as a page would.
        urllib.request.urlopen(served.base + "/api/machines?fresh=1",
                               timeout=30).read()
        machines, flags = gc.fetch(served.base)
        assert [m["uid"] for m in machines] == [UID]
        assert machines[0]["status"] == "RED"
        with urllib.request.urlopen(served.base + "/api/machines",
                                    timeout=30) as resp:
            payload = json.loads(resp.read())
        entry = payload["machines"][0]
        assert len(_compact(entry)) <= PER_MACHINE_BUDGET, len(_compact(entry))
        assert lab.ops == 0
