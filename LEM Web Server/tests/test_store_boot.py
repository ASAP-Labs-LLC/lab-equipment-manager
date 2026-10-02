"""Booting on the store: which store, opened how, by which kind of boot.

Transfer spec §5.1. Three boots, three answers:

* **The live server** opens `LEM_STORE_PATH` (production default
  `C:\\ASAPApps\\lem\\store\\lem.db`, outside the release folder and outside
  `data/`) read-write, and creates it on first start.
* **The updater's candidate** — `--no-publish`, on a scratch port, health-
  checked and thrown away — opens the SAME store `mode=ro`. It must not
  migrate, declare, ingest or write anything into the record the live server
  is using; if the store is not there it says so on every read rather than
  creating an empty one and reporting an empty lab.
* **`--dev`** boots an in-memory LabCore and a fresh scratch store, and seeds
  each with what is THEIRS: `samples`/`sample_tests` into LabCore, the floor
  into the store.

`web_server.pyw` is imported through conftest's `.pyw` finder; importing it
starts nothing.
"""

import os

import pytest

import web_server
from lem_store import LocalStoreGateway


def _tables(gw):
    res = gw.read_sql("SELECT name FROM sqlite_master WHERE type='table'")
    assert "error" not in res, res
    return {r["name"] for r in res["rows"]}


class TestTheLiveServer:
    def test_opens_the_store_read_write_and_labcore_over_http(self, tmp_path):
        path = str(tmp_path / "store" / "lem.db")
        store, labcore, where = web_server.build_gateways(
            dev=False, seed=False, no_publish=False, store_path=path)
        try:
            assert isinstance(store, LocalStoreGateway)
            assert store.read_only is False and store.path == path
            assert os.path.exists(path)
            from labcore_gateway import HttpLabCoreGateway
            assert isinstance(labcore, HttpLabCoreGateway)
            assert where == labcore.base_url
            assert "lem_machine_log" in _tables(store)
        finally:
            store.close()

    def test_the_path_comes_from_LEM_STORE_PATH(self, tmp_path, monkeypatch):
        path = str(tmp_path / "elsewhere.db")
        monkeypatch.setenv("LEM_STORE_PATH", path)
        store, _lab, _w = web_server.build_gateways(
            dev=False, seed=False, no_publish=False)
        try:
            assert store.path == path
        finally:
            store.close()

    def test_the_production_default_is_outside_the_release(self, monkeypatch):
        import lem_store
        monkeypatch.delenv("LEM_STORE_PATH", raising=False)
        monkeypatch.setattr(lem_store.os, "name", "nt")
        assert lem_store.default_store_path() == r"C:\ASAPApps\lem\store\lem.db"


class TestTheCandidateBoot:
    def test_no_publish_opens_the_live_store_read_only(self, tmp_path):
        path = str(tmp_path / "lem.db")
        live = LocalStoreGateway(path)
        live.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind) "
                 "VALUES ('m1', 't', 'run')")
        store, _lab, _w = web_server.build_gateways(
            dev=False, seed=False, no_publish=True, store_path=path)
        try:
            assert store.read_only is True
            assert store.read_sql("SELECT COUNT(*) n FROM lem_machine_log"
                                  )["rows"][0]["n"] == 1
            assert "read-only" in store.sql(
                "INSERT INTO lem_machine_log (machine_uid) VALUES ('x')")["error"]
        finally:
            store.close()
            live.close()

    def test_a_candidate_never_creates_a_store(self, tmp_path):
        path = str(tmp_path / "missing" / "lem.db")
        store, _lab, _w = web_server.build_gateways(
            dev=False, seed=False, no_publish=True, store_path=path)
        try:
            assert not os.path.exists(path)
            assert "error" in store.read_sql("SELECT 1 AS one")
        finally:
            store.close()

    def test_a_candidate_app_answers_healthz_and_says_read_only(self, tmp_path):
        from web_app import create_app
        path = str(tmp_path / "lem.db")
        LocalStoreGateway(path).close()
        store, labcore, _w = web_server.build_gateways(
            dev=False, seed=False, no_publish=True, store_path=path)
        try:
            app = create_app(store, labcore=labcore, secret="s",
                             documents_root=str(tmp_path))
            app.config["SNAPSHOTS"].refresh()
            body = app.test_client().get("/healthz").get_json()
            assert body["status"] == "ok"
            assert body["store"]["read_only"] is True
            assert body["store"]["path"] == path
            # LabCore is a different gateway and nothing probes it.
            assert body["labcore"] == "unknown"
        finally:
            store.close()

    def test_a_candidate_snapshot_writes_nothing(self, tmp_path):
        """The refresh declares no table and writes nothing: every write it
        could attempt would be refused, and the file must be byte-for-byte
        what the live server left."""
        from snapshot_service import SnapshotService
        path = str(tmp_path / "lem.db")
        live = LocalStoreGateway(path)
        SnapshotService(live).ensure_schema()
        live.close()
        before = os.path.getmtime(path), os.path.getsize(path)
        store = LocalStoreGateway(path, read_only=True)
        writes = []
        real = store.sql
        store.sql = lambda s, a=None, **k: (writes.append(s), real(s, a, **k))[1]
        try:
            snaps = SnapshotService(store)
            snaps.refresh()
            assert snaps.get()["ready"]
            assert writes == []
        finally:
            store.close()
        assert (os.path.getmtime(path), os.path.getsize(path)) == before


class TestDev:
    def test_dev_is_a_labcore_and_a_store_each_seeded_with_its_own(self, tmp_path):
        store, labcore, where = web_server.build_gateways(
            dev=True, seed=True, no_publish=True,
            store_path=str(tmp_path / "dev.db"))
        try:
            assert where == "fake (dev)"
            # --no-publish does not make a dev store read-only: there is no
            # live server whose record it could be protecting.
            assert store.read_only is False
            assert labcore is not store
            lab = labcore.read_sql("SELECT lab_id FROM samples")["rows"]
            assert {"lab_id": "STD-1"} in lab
            assert not [t for t in _tables(labcore) if t.startswith("lem_")]
            n = store.read_sql("SELECT COUNT(*) n FROM lem_machine_status"
                               )["rows"][0]["n"]
            assert n > 0
            assert "samples" not in _tables(store)
        finally:
            store.close()

    def test_seeding_an_already_seeded_dev_store_adds_nothing(self, tmp_path):
        path = str(tmp_path / "dev.db")
        a, _l, _w = web_server.build_gateways(dev=True, seed=True,
                                              no_publish=False, store_path=path)
        n1 = a.read_sql("SELECT COUNT(*) n FROM lem_machine_log")["rows"][0]["n"]
        a.close()
        b, _l, _w = web_server.build_gateways(dev=True, seed=True,
                                              no_publish=False, store_path=path)
        try:
            n2 = b.read_sql("SELECT COUNT(*) n FROM lem_machine_log"
                            )["rows"][0]["n"]
            assert n2 == n1
        finally:
            b.close()


class TestDiagnosticsNameTheRightDatabase:
    """Settings › Diagnostics used to read "LabCore: Reachable" off the
    snapshot's reachability. The snapshot reads the STORE now, so that
    sentence would be about the wrong database; on a split app LabCore is
    "not asked in the background" and the store gets a row of its own."""

    def test_split_app(self, tmp_path):
        from labcore_counter import CountingLabCore
        from web_app import create_app
        store = LocalStoreGateway(str(tmp_path / "lem.db"))
        try:
            app = create_app(store, labcore=CountingLabCore(), secret="s",
                             documents_root=str(tmp_path))
            app.config["SNAPSHOTS"].refresh()
            page = app.test_client().get("/settings").get_data(as_text=True)
            assert "Not asked in the background" in page
            assert "LEM store" in page and "Read-write" in page
            assert "Machine log" in page
            assert "filling from LabCore" not in page
        finally:
            store.close()
