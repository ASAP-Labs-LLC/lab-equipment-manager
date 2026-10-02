"""`GET /api/machines` gains one additive field, `transfer`, and nothing else
(transfer spec §12.3).

`/api/machines` is a contract with another program: GC hub's Instruments page
reads it (`gc-hub/lem_machines.py`: `machine_uid`, `title`, `status`,
`closed_reason`, under a 1 MB cap and an 8 s budget). So:

* every existing key of every machine is unchanged — removing `transfer` gives
  back exactly what `merge_machines` produced before this piece;
* `transfer` is `{"road", "unacked", "last_sync_age_s"}` for a bench that has
  synced over v2, and `null` for one that never has (a v3.9 bench is not
  "0 waiting" — nobody has asked it);
* it is served from memory: 0 LabCore ops, 0 extra store reads per request;
* the payload stays ≤ 1.3 KB per machine on the REAL fleet — measured on
  production's own `/api/machines` of 2026-10-01 (17 machines,
  `fixtures/machines_live_2026-10-01.json`), with every machine given the
  largest `transfer` it can carry;
* GC hub's own parser reads the new payload exactly as it read the old one.
"""
import importlib.util
import json
import os

import pytest

import bench_v2_kit as kit
from bench_v2_kit import Bench, UID
from labcore_counter import CountingLabCore
from labcore_gateway import FakeLabCoreGateway

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "machines_live_2026-10-01.json")
GC_HUB = os.environ.get("LEM_GC_HUB_DIR", os.path.expanduser("~/Projects/gc-hub"))


def _compact(obj) -> bytes:
    # Flask's own JSON: compact separators, sorted keys, ASCII.
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode()


@pytest.fixture
def store():
    s = FakeLabCoreGateway()
    kit.seed_machine(s)
    kit.seed_machine(s, uid="v39-bench", title="Old Bench")
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


def _machines(client):
    return {m["machine_uid"]: m
            for m in client.get("/api/machines?fresh=1").get_json()["machines"]}


class TestTheField:
    def test_a_v2_bench_says_its_road_backlog_and_age(self, client):
        b = Bench(client, kit.enroll(client))
        b.journal(3).sync(stats={"unacked": 12, "road": "public",
                                 "digest": kit.DIGEST_ZERO})
        t = _machines(client)[UID]["transfer"]
        assert set(t) == {"road", "unacked", "last_sync_age_s"}
        assert t["road"] == "public" and t["unacked"] == 12
        assert 0 <= t["last_sync_age_s"] < 60

    def test_a_bench_that_never_synced_v2_is_null_not_zero(self, client):
        assert _machines(client)["v39-bench"]["transfer"] is None

    def test_nothing_else_changed(self, client, app):
        """Remove `transfer` and the payload is what it was before this piece:
        the snapshot's machines through `merge_machines`, key for key."""
        from live_presence import merge_machines
        from web_app import STATUS_COLORS
        b = Bench(client, kit.enroll(client))
        b.journal(1).sync()
        got = client.get("/api/machines?fresh=1").get_json()
        snap = app.config["SNAPSHOTS"].get()
        expected = merge_machines(snap["machines"], app.config["LIVE"],
                                  STATUS_COLORS)
        stripped = []
        for m in got["machines"]:
            m = dict(m)
            assert "transfer" in m
            m.pop("transfer")
            stripped.append(m)
        assert json.loads(json.dumps(expected)) == stripped
        assert [m["machine_uid"] for m in got["machines"]] == \
            [m["machine_uid"] for m in expected]

    def test_it_costs_labcore_nothing_and_the_store_nothing_per_request(
            self, client, store, lab, monkeypatch):
        b = Bench(client, kit.enroll(client))
        b.journal(1).sync()
        client.get("/api/machines?fresh=1")
        reads = {"n": 0}
        real = type(store).read_sql

        def counting(self, *a, **k):
            reads["n"] += 1
            return real(self, *a, **k)
        monkeypatch.setattr(type(store), "read_sql", counting)
        for _ in range(10):
            assert client.get("/api/machines").status_code == 200
        assert reads["n"] == 0
        assert lab.ops == 0


class TestSize:
    def test_the_real_fleet_stays_under_1_3_kb_per_machine(self):
        from bench_api import transfer_field
        with open(FIXTURE, encoding="utf-8") as f:
            live = json.load(f)
        machines = live["machines"]
        assert len(machines) == 17
        before = len(_compact(live))
        worst = transfer_field({"road": "public", "unacked": 99_999_999,
                                "seen": 0.0}, now=10**9)
        # the largest it can be: every value present and long
        assert None not in worst.values(), worst
        for m in machines:
            m["transfer"] = worst
        after = len(_compact(live))
        per_machine = after / len(machines)
        assert per_machine <= 1300, per_machine
        assert (after - before) / len(machines) <= 100
        assert after < 1024 * 1024                    # GC hub's MAX_BYTES


def _gc_hub_parser():
    path = os.path.join(GC_HUB, "lem_machines.py")
    if not os.path.isfile(path):
        pytest.skip("GC hub is not checked out at %s" % GC_HUB)
    import sys
    # GC hub's module imports its own `version` module from its root. Put
    # that root on the path only while loading, and take back anything the
    # load added, so no GC hub module is left where LEM's imports look.
    before = set(sys.modules)
    sys.path.insert(0, GC_HUB)
    try:
        spec = importlib.util.spec_from_file_location("gc_hub_lem_machines",
                                                      path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(GC_HUB)
        for name in set(sys.modules) - before:
            if name != "gc_hub_lem_machines":
                del sys.modules[name]
    return mod


class TestGcHubContract:
    def test_gc_hubs_parser_reads_the_new_payload(self, client, store):
        gc = _gc_hub_parser()
        b = Bench(client, kit.enroll(client))
        b.journal(1).sync()
        payload = client.get("/api/machines?fresh=1").get_json()
        rows = gc.normalise(payload)
        assert {r["uid"] for r in rows} == {UID, "v39-bench"}
        mine = [r for r in rows if r["uid"] == UID][0]
        assert mine["title"] == "PAC Flash 1" and mine["status"]
        assert gc.flags(payload)["labcore_online"] in (True, False)

    def test_gc_hubs_parser_reads_the_real_fleet_with_transfer(self):
        gc = _gc_hub_parser()
        from bench_api import transfer_field
        with open(FIXTURE, encoding="utf-8") as f:
            live = json.load(f)
        plain = gc.normalise(json.loads(json.dumps(live)))
        for m in live["machines"]:
            m["transfer"] = transfer_field({"road": "lan", "unacked": 3,
                                            "seen": 5.0}, now=10.0)
        assert gc.normalise(live) == plain
        assert len(plain) == 17
