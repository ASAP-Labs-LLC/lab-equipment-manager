"""What a bench says about itself is echoed to the floor only within bounds.

`/api/machines` is a contract with another program. GC hub's Instruments page
fetches it under a 1 MB cap (`gc-hub/lem_machines.py`, `MAX_BYTES`), and a
payload over that cap fails the fetch for the whole floor, not for one
machine. The transfer spec budgets the new `transfer` field at ~90 B per
machine and the payload at <= 1.3 KB per machine on the real fleet.

Round 1 of T-P7 measured that budget with a value the test chose
(`road="public"`, `unacked=99_999_999`) and called it "the largest it can be".
It was not. The registry kept `stats.road` and `stats.unacked` exactly as the
bench sent them, and `transfer_field` echoed them: one sync carrying a
200,000-character road was answered 200 and made that machine's entry
204,737 B. Six such benches would push `/api/machines` past GC hub's cap.
`bench_cursor.road` already kept only 16 characters, so the store and the
memory disagreed about the same fact as well.

The fix is to decide what each echoed value CAN be, once, where it enters:

* `road` is one of the roads the spec names (`lan`, `public`, `folder`) or
  the word `other`; a road we have not named is still "some road", never the
  bench's own text. The store and the registry hold the same value.
* `unacked` and the LabCore counters are integers in 0..999,999,999. A
  negative count, a bool, NaN, or text is "not said" (None), not 0. JSON's
  `Infinity` (Python's json accepts it) is a number too big to be a count,
  and must not raise: an OverflowError inside the sync transaction would
  roll back every sync from that bench forever (503), and one after the
  commit would answer 500 for records that were in fact recorded.
* the live block (status, reason, timestamps, lab id) — the same block a
  v3.9 bench pushes on `/api/live` — is clipped by its ENCODED size, because
  `/api/machines` is ASCII JSON: one "é" costs 6 bytes, one emoji 12. A
  person still reads the whole reason in the record; the live road is a
  failover for freshness, not the record.

Each bound is pinned below against the worst a bench can send, and the real
fleet's size is measured with every machine carrying the worst value the
code can produce — taken from the code, not chosen by the test.
"""
import json
import math
from datetime import datetime, timezone
import os

import pytest

import bench_v2_kit as kit
from bench_v2_kit import Bench, UID
from labcore_counter import CountingLabCore
from labcore_gateway import FakeLabCoreGateway
from live_presence import LivePresence

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "machines_live_2026-10-01.json")
GC_HUB_MAX_BYTES = 1024 * 1024


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


HOSTILE_ROAD = "r" * 200_000
HOSTILE_COUNT = int("9" * 4000)          # Python's json reads it; 4000 digits


class TestRoad:
    @pytest.mark.parametrize("sent,held", [
        ("lan", "lan"), ("public", "public"), ("folder", "folder"),
        ("LAN", "lan"), (" public ", "public"),
        ("tunnel-2", "other"), (HOSTILE_ROAD, "other"), (12, "other"),
        (["lan"], "other"), ({"a": 1}, "other"),
        (None, None), ("", None)], ids=lambda v: repr(v)[:20])
    def test_a_road_is_a_named_road_or_other(self, sent, held):
        from bench_api import bench_road
        assert bench_road(sent) == held

    def test_the_store_and_the_floor_hold_the_same_road(self, client, store):
        """`bench_cursor.road` kept 16 characters while memory kept 200,000:
        one fact, two answers. Now both hold what `bench_road` says."""
        b = Bench(client, kit.enroll(client))
        r = b.journal(2).sync(stats={"road": HOSTILE_ROAD, "unacked": 1,
                                     "digest": kit.DIGEST_ZERO})
        assert r.status_code == 200, r.get_json()
        cur = store.read_sql("SELECT road FROM bench_cursor WHERE "
                             "machine_uid = ?", [UID])["rows"][0]
        assert cur["road"] == "other"
        assert _entry(client)["transfer"]["road"] == "other"


class TestCounts:
    @pytest.mark.parametrize("sent,held", [
        (0, 0), (12, 12), ("12", 12), (999_999_999, 999_999_999),
        (10**12, 999_999_999), (HOSTILE_COUNT, 999_999_999),
        (1e300, 999_999_999), (math.inf, 999_999_999),
        (-1, None), (-math.inf, None), (math.nan, None), (True, None),
        ("nine", None), (None, None), ([3], None), ("9" * 5000, None)],
        ids=lambda v: repr(v)[:20])
    def test_a_count_is_a_bounded_non_negative_int(self, sent, held):
        from bench_api import bench_count
        assert bench_count(sent) == held

    def test_infinity_from_a_bench_does_not_wedge_its_sync(self, client):
        """Python's json reads `Infinity`. Before, `int(inf)` raised
        OverflowError inside the sync transaction (records_total) — every
        sync from that bench rolled back as 503 until someone noticed."""
        b = Bench(client, kit.enroll(client))
        b.journal(3)
        doc = b.body(stats={"unacked": math.inf, "records_total": math.inf,
                            "road": "lan", "digest": kit.DIGEST_ZERO,
                            "labcore": {"failed": math.inf}})
        raw = json.dumps(doc)                      # allow_nan: 'Infinity'
        assert "Infinity" in raw
        r = client.post("/api/v2/bench/%s/sync" % UID, data=raw,
                        content_type="application/json",
                        headers={"X-LEM-Bench-Token": b.token,
                                 "User-Agent": "LEM-Station/4.0.0 (%s)" % UID,
                                 "X-LEM-Proto": "2"})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["acked"] == 3
        t = _entry(client)["transfer"]
        assert t["unacked"] == 999_999_999 and t["road"] == "lan"

    def test_healthz_sums_stay_bounded(self, client):
        b = Bench(client, kit.enroll(client))
        r = b.journal(1).sync(stats={
            "unacked": HOSTILE_COUNT, "road": "lan", "digest": kit.DIGEST_ZERO,
            "labcore": {"failed": HOSTILE_COUNT, "watchdog": HOSTILE_COUNT,
                        "reads": "x" * 10_000, "junk": "y" * 10_000}})
        assert r.status_code == 200
        benches = client.get("/healthz").get_json()["benches"]
        assert benches["unacked_total"] == 999_999_999
        assert benches["labcore_failed_5min"] == 999_999_999
        assert benches["labcore_watchdog_5min"] == 999_999_999
        assert len(_compact(benches)) < 400


class TestLiveBlock:
    def test_each_echoed_live_field_is_clipped_by_encoded_size(self):
        from live_presence import LIVE_FIELD_BYTES
        live = LivePresence()
        huge = {"status": "S" * 50_000, "reason": "\U0001F600" * 50_000,
                "at": "9" * 50_000, "last_parse_at": "é" * 50_000,
                "lab_id": " " * 50_000, "interval_seconds": 30}
        assert live.record(UID, huge)
        e = live.get(UID)
        for field, cap in LIVE_FIELD_BYTES.items():
            encoded = len(json.dumps(e[field]))
            assert encoded <= cap + 2, (field, encoded, cap)   # + quotes
            assert e[field], field                 # clipped, not dropped
        # a clipped reason says it was clipped
        assert e["reason"].endswith("…")
        # nothing left half a surrogate pair or half an escape
        json.loads(json.dumps(e))

    def test_an_ordinary_push_is_untouched(self):
        live = LivePresence()
        reason = ("QC out of spec: ASTM D2887/D86 - Distillation in Petroleum "
                  "Products, 10% Recovery, ASTM D2887/D86 - Distillation in "
                  "Petroleum Products, 50% Recovery")   # production's longest
        payload = {"status": "RED", "reason": reason,
                   "at": "2026-10-01T09:00:00.123456-07:00",
                   "last_parse_at": "2026-10-01T08:59:58-07:00",
                   "lab_id": "26-123456-01", "interval_seconds": 30}
        live.record(UID, payload)
        e = live.get(UID)
        for k in ("status", "reason", "at", "last_parse_at", "lab_id"):
            assert e[k] == payload[k]


class TestTheWorstABenchCanDo:
    def test_one_hostile_sync_grows_its_machine_by_a_pinned_amount(
            self, client, lab):
        """The critic's own reproduction, end to end: a 200,000-character
        road and a 4,000-digit backlog, plus every live field as long as the
        256 KB body allows. Answered 200 (the records are good), and the
        machine's entry grows by no more than the bounds allow."""
        honest = Bench(client, kit.enroll(client))
        honest.journal(1).sync()
        before = len(_compact(_entry(client)))

        b = honest
        b.journal(1)
        doc = b.body(stats={"unacked": HOSTILE_COUNT, "road": HOSTILE_ROAD[:120_000],
                            "digest": b.digest_through(b.acked),
                            "labcore": {"failed": HOSTILE_COUNT}},
                     live={"status": "S" * 5_000, "reason": "é" * 8_000,
                           "at": "2" * 5_000, "last_parse_at": "3" * 5_000,
                           "lab_id": "L" * 5_000, "interval_seconds": 30})
        assert len(json.dumps(doc)) < 256 * 1024
        r = b.post_doc(doc)
        assert r.status_code == 200, r.get_json()
        after = _entry(client)
        size = len(_compact(after))
        from live_presence import LIVE_FIELD_BYTES
        # status, reason and the three timestamps it fills (updated_at,
        # last_poll from `at`; last_activity from last_parse_at)
        allowed = (LIVE_FIELD_BYTES["status"] + LIVE_FIELD_BYTES["reason"]
                   + 2 * LIVE_FIELD_BYTES["at"]
                   + LIVE_FIELD_BYTES["last_parse_at"])
        assert size - before <= allowed, (before, size)
        assert after["transfer"]["road"] == "other"
        assert after["transfer"]["unacked"] == 999_999_999
        assert size < 2048, size                   # was 204,737 B
        assert lab.ops == 0

    def test_hydrate_bounds_what_an_older_build_left_in_the_store(self, store):
        """A row written before these bounds (or by hand) is read back
        through the same rules: a restart must not reopen the hole. Seen
        just now, so `/healthz` counts it as reporting."""
        kit.make_app(store)                        # declares the tables
        assert "error" not in store.sql(
            "INSERT INTO bench_cursor (machine_uid, bench_epoch, acked_seq, "
            "last_seen, road, module_version, stats) VALUES (?, 'ep', 1, ?, "
            "?, ?, ?)", [UID, datetime.now(timezone.utc).isoformat(), "x" * 5000,
                         "v" * 5000, json.dumps({
                             "unacked": "9" * 4000,
                             "labcore": {"watchdog": 10**30, "x": "y" * 9000}})])
        client = kit.make_app(store).test_client()
        t = _entry(client)["transfer"]
        assert t["road"] == "other" and t["unacked"] == 999_999_999
        assert len(_compact(t)) <= 80
        benches = client.get("/healthz").get_json()["benches"]
        assert benches["labcore_watchdog_5min"] == 999_999_999


class TestTheRealFleet:
    def _worst_transfer(self):
        """The longest `transfer` the code can produce, from the code: the
        longest named road, the largest count, the oldest age a float can
        print as from a seen-time of 0 (`/api/machines` uses wall time)."""
        from bench_api import BenchRegistry, transfer_field, ROADS
        reg = BenchRegistry(clock=lambda: 0.0)
        longest_road = max(list(ROADS) + ["other"], key=len)
        reg.note("u", road=longest_road, unacked=HOSTILE_COUNT, seen=0.0)
        return transfer_field(reg.get("u"), now=4_102_444_800.0)   # 2100

    def test_worst_transfer_on_every_machine_stays_under_1_3_kb(self):
        with open(FIXTURE, encoding="utf-8") as f:
            live = json.load(f)
        machines = live["machines"]
        assert len(machines) == 17
        before = len(_compact(live))
        worst = self._worst_transfer()
        assert None not in worst.values(), worst
        assert len(_compact(worst)) <= 80, worst
        for m in machines:
            m["transfer"] = worst
        after = len(_compact(live))
        assert after / 17 <= 1300, after / 17
        assert (after - before) / 17 <= 90           # the spec's ~90 B

    def test_every_bench_hostile_at_once_still_fits_gc_hubs_cap(self):
        """All 17 real machines, each overlaid by a bench sending the worst
        live block and the worst stats, through the real merge. The floor
        list comes from the record, so a bench cannot add machines; the cap
        holds with a margin of hundreds."""
        from live_presence import merge_machines
        from web_app import STATUS_COLORS
        with open(FIXTURE, encoding="utf-8") as f:
            live_payload = json.load(f)
        lp = LivePresence()
        for m in live_payload["machines"]:
            lp.record(m["machine_uid"], {
                "status": "\U0001F600" * 9000, "reason": "\U0001F600" * 9000,
                "at": "\U0001F600" * 9000, "last_parse_at": "é" * 9000,
                "lab_id": "x" * 9000, "interval_seconds": 30})
        plain = {m["machine_uid"]: len(_compact(m))
                 for m in live_payload["machines"]}
        merged = merge_machines(live_payload["machines"], lp, STATUS_COLORS)
        worst = self._worst_transfer()
        for m in merged:
            m["transfer"] = worst
        live_payload["machines"] = merged
        total = len(_compact(live_payload))
        from live_presence import LIVE_FIELD_BYTES
        # what the overlay may add: the live fields it fills, `state` and
        # `live`, the transfer field
        allowed = (LIVE_FIELD_BYTES["status"] + LIVE_FIELD_BYTES["reason"]
                   + 2 * LIVE_FIELD_BYTES["at"]
                   + LIVE_FIELD_BYTES["last_parse_at"]
                   + len(',"state":"running","live":true')
                   + len(',"transfer":') + len(_compact(worst)))
        for m in merged:
            assert len(_compact(m)) - plain[m["machine_uid"]] <= allowed, m
        # Measured 2026-10-01: 28,380 B for 17 machines (1,669 B each; the
        # plain fleet is 1,050 B each). 37x under GC hub's cap. The record
        # itself already carries a 2,683 B machine (a long QC reason), so the
        # 1.3 KB budget is the fleet's, with `transfer` — pinned above.
        assert total < GC_HUB_MAX_BYTES // 25, total
        assert total / 17 <= 1700, total / 17
