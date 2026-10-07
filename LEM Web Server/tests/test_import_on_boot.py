"""A live v4 boot moves LEM's record by itself, and says "moving" until it has.

v4.0.0 went live on 2026-10-06 with an empty store and served it. The import
that carries the `lem_*` tables out of LabCore ran only when the server was
started with `--import-from-mirror`, and the updater starts LEM with its own
fixed arguments, so it never ran. For five minutes:

* the floor, `/api/machines` and GC hub showed **0 instruments**, and
* `/api/bench/<uid>/config` answered every v3.x bench **200 with empty
  lists**. An empty configuration is an instruction a bench acts on: it
  drops its correction factors, QC and override. A result filed in that
  window can be missing its correction (ISO/IEC 17025 §7.8.2).

Both are the same mistake: an empty store that has not been filled answered
as if it were the record. A failed read is never an empty result
(ASK-CLAUDE.md), and a record still being moved is not an empty record.

So a live boot (writable store, not --dev) now:

1. **starts the import on its own**, from the v3.x server's local log copy
   when one is there, otherwise by walking LabCore's log. Nobody has to know
   a flag the updater cannot pass.
2. **answers "moving" until the import is verified**: every API 503 with
   Retry-After and `"stale": true` (the bench contract's "I have nothing yet":
   a v3.x bench keeps what it has and asks LabCore), every page a 503 that
   says what is happening. `/healthz` and static files stay open, so the
   updater sees a healthy server and does not roll it back.
3. **opens by itself** the moment the import verifies, without a restart.

`create_app` stays free of this (tests and --dev never see the gate); the
boot turns it on, like the bridge and the import service.
"""
import os
import shutil
import time

import pytest

import bench_v2_kit as bk
import legacy_import
import legacy_kit as kit
import web_server
from lem_store import LocalStoreGateway


@pytest.fixture
def mirror(tmp_path):
    path = str(tmp_path / "data" / "log-mirror.sqlite3")
    os.makedirs(os.path.dirname(path))
    shutil.copyfile(kit.MIRROR_FIXTURE, path)
    return path


def _meta(store, key):
    rows = kit.store_rows(store, "SELECT value FROM store_meta WHERE key = ?",
                          [key])
    return rows[0]["value"] if rows else None


def _wait_verified(store, seconds=15.0):
    end = time.time() + seconds
    while time.time() < end:
        if _meta(store, "import_state") == "verified":
            return True
        time.sleep(0.05)
    return False


def _stop(app):
    for key in ("BRIDGE", "IMPORT_SERVICE"):
        svc = app.config.get(key)
        if svc is not None:
            svc.stop()


@pytest.fixture
def no_import_thread(monkeypatch):
    """Hold the import still, so the gate can be looked at before it lifts."""
    monkeypatch.setattr(legacy_import.ImportService, "start", lambda self: None)


# ── 1. the import starts on its own ─────────────────────────────────────────

def test_a_live_boot_imports_without_being_told_to(tmp_path, mirror):
    """No --import-from-mirror: the boot finds the v3.x log copy itself and
    the record arrives, verified, with the hold released."""
    store = LocalStoreGateway(str(tmp_path / "lem.db"))
    lab = kit.labcore_from_mirror(mirror)
    app = bk.make_app(store, labcore=lab)
    try:
        out = web_server.start_transfer(app, store, dev=False, retry_s=0.1,
                                        mirror_candidates=[mirror])
        assert out["held"] is True and out["importing"] is True
        assert _wait_verified(store), legacy_import.status(store)
        assert legacy_import.status(store)["tables_verified"] > 0
        assert not _meta(store, "sync_hold")
    finally:
        _stop(app)
        store.close()


def test_with_no_log_copy_anywhere_it_walks_labcore_instead(tmp_path, mirror):
    """A server that never kept a log copy still gets its record: the
    importer walks LabCore's log by rowid. Nothing is skipped for want of a
    file."""
    store = LocalStoreGateway(str(tmp_path / "lem.db"))
    lab = kit.labcore_from_mirror(mirror)
    app = bk.make_app(store, labcore=lab)
    try:
        out = web_server.start_transfer(
            app, store, dev=False, retry_s=0.1,
            mirror_candidates=[str(tmp_path / "nowhere" / "log-mirror.sqlite3")])
        assert out["importing"] is True
        assert _wait_verified(store), legacy_import.status(store)
    finally:
        _stop(app)
        store.close()


def test_the_first_existing_log_copy_is_the_one_used(tmp_path, mirror):
    missing = str(tmp_path / "gone" / "log-mirror.sqlite3")
    assert web_server.find_log_mirror([missing, mirror]) == mirror
    assert web_server.find_log_mirror([missing]) is None
    assert web_server.find_log_mirror([]) is None


def test_the_default_places_include_the_data_dir_copy(tmp_path, monkeypatch):
    """v3.x kept its copy under the server's data directory (LEM_DATA_DIR on
    the lab server). That place is searched by default."""
    monkeypatch.setenv("LEM_DATA_DIR", str(tmp_path / "lemdata"))
    places = web_server.default_log_mirror_candidates()
    assert os.path.join(str(tmp_path / "lemdata"), "log-mirror.sqlite3") in places


def test_an_explicit_mirror_still_wins(tmp_path, mirror):
    store = LocalStoreGateway(str(tmp_path / "lem.db"))
    lab = kit.labcore_from_mirror(mirror)
    app = bk.make_app(store, labcore=lab)
    try:
        out = web_server.start_transfer(app, store, dev=False, retry_s=0.1,
                                        import_mirror=mirror,
                                        mirror_candidates=[])
        assert out["importing"] is True
        assert app.config["IMPORT_SERVICE"].importer.mirror_path == mirror
        assert _wait_verified(store)
    finally:
        _stop(app)
        store.close()


def test_a_candidate_boot_and_dev_never_import_or_gate(tmp_path, mirror):
    """The updater's read-only candidate and --dev are left exactly alone."""
    LocalStoreGateway(str(tmp_path / "lem.db")).close()
    ro = LocalStoreGateway(str(tmp_path / "lem.db"), read_only=True)
    app = bk.make_app(ro, labcore=kit.LabCore())
    assert web_server.start_transfer(app, ro, dev=False,
                                     mirror_candidates=[mirror]) == {
        "held": False, "importing": False, "bridge": False}
    assert not app.config.get("IMPORT_GATE")
    ro.close()

    dev = LocalStoreGateway(str(tmp_path / "dev.db"))
    app = bk.make_app(dev, labcore=kit.LabCore())
    out = web_server.start_transfer(app, dev, dev=True,
                                    mirror_candidates=[mirror])
    assert out["importing"] is False and not app.config.get("IMPORT_GATE")
    dev.close()


# ── 2. "moving" until verified, never an empty record ───────────────────────

def test_until_verified_every_api_says_moving_never_empty(
        tmp_path, mirror, no_import_thread):
    store = LocalStoreGateway(str(tmp_path / "lem.db"))
    app = bk.make_app(store, labcore=kit.labcore_from_mirror(mirror))
    try:
        web_server.start_transfer(app, store, dev=False,
                                  mirror_candidates=[mirror])
        c = app.test_client()
        r = c.get("/api/machines")
        assert r.status_code == 503
        assert r.headers.get("Retry-After")
        body = r.get_json()
        assert body["stale"] is True and "moving" in body["error"].lower()

        # The bench road: a v3.x bench must get "I have nothing yet", which
        # it answers by keeping its config and asking LabCore. Never 200 [].
        r = c.get("/api/bench/pac-flash-1/config",
                  headers={"X-LEM-Token": bk.SHARED_TOKEN})
        assert r.status_code == 503 and r.get_json()["stale"] is True

        r = c.get("/")
        assert r.status_code == 503
        assert b"moving" in r.data.lower()
    finally:
        _stop(app)
        store.close()


def test_healthz_stays_ok_and_says_how_far_the_move_is(
        tmp_path, mirror, no_import_thread):
    """The updater re-checks /healthz after the switch and rolls back on a
    failure. Moving is not a failure, so /healthz answers 200 ok and carries
    the import's own state."""
    store = LocalStoreGateway(str(tmp_path / "lem.db"))
    app = bk.make_app(store, labcore=kit.labcore_from_mirror(mirror))
    try:
        web_server.start_transfer(app, store, dev=False,
                                  mirror_candidates=[mirror])
        r = app.test_client().get("/healthz")
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "ok"
        assert "idle_seconds" in body
        assert body["store"]["import"]["state"] != "verified"
    finally:
        _stop(app)
        store.close()


# ── 3. it opens by itself ───────────────────────────────────────────────────

def test_the_gate_lifts_by_itself_when_the_import_verifies(
        tmp_path, mirror, no_import_thread):
    """No restart: the import finishing is what opens LEM. Afterwards the
    floor lists the imported machines and a bench gets its real factors."""
    store = LocalStoreGateway(str(tmp_path / "lem.db"))
    lab = kit.labcore_from_mirror(mirror)
    app = bk.make_app(store, labcore=lab)
    try:
        web_server.start_transfer(app, store, dev=False,
                                  mirror_candidates=[mirror])
        c = app.test_client()
        assert c.get("/api/machines").status_code == 503

        out = legacy_import.Importer(store, lab, mirror_path=mirror,
                                     sleep=lambda s: None).run()
        assert out["state"] == "verified", out

        app.config["SNAPSHOTS"].refresh()
        r = c.get("/api/machines")
        assert r.status_code == 200
        body = r.get_json()
        machines = body["machines"] if isinstance(body, dict) else body
        uids = {m["machine_uid"] for m in machines}
        assert {"pac-flash-1", "gc-2"} <= uids

        r = c.get("/api/bench/pac-flash-1/config",
                  headers={"X-LEM-Token": bk.SHARED_TOKEN})
        assert r.status_code == 200
        assert r.get_json()["corrections"], r.get_json()
    finally:
        _stop(app)
        store.close()


def test_an_already_verified_store_is_never_gated(tmp_path, mirror):
    """Every boot after the first: the record is here, LEM opens at once."""
    store = LocalStoreGateway(str(tmp_path / "lem.db"))
    lab = kit.labcore_from_mirror(mirror)
    assert legacy_import.Importer(store, lab, mirror_path=mirror,
                                  sleep=lambda s: None).run()["state"] == "verified"
    app = bk.make_app(store, labcore=lab)
    try:
        out = web_server.start_transfer(app, store, dev=False,
                                        mirror_candidates=[mirror])
        assert out["held"] is False and out["importing"] is False
        app.config["SNAPSHOTS"].refresh()
        assert app.test_client().get("/api/machines").status_code == 200
    finally:
        _stop(app)
        store.close()
