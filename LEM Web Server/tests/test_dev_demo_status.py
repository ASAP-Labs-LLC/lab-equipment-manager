"""`--dev --seed` must not show a lab in trouble that it made up.

The whole-app integration critic booted `--dev --seed` and found two status
lines that were false about the demo itself:

* **"Data · 0 of 13 reporting", with the held glyph, on a fresh boot.** The
  foot counts a v3.9 bench as reporting when it is checking in AND its road
  into LEM works. A split app (store + LabCore) builds the mixed-fleet bridge,
  and the foot then takes the bridge as that road. Under --dev the bridge can
  never be on (the import it waits for is never verified on a scratch store),
  so every seeded bench counted as not reporting. But the demo benches were
  never behind a bridge: the seeder writes their records straight into the
  store, which is exactly the "no bridge: the store is the road" case. Under
  --dev the bridge is not kept, and the foot tells the truth.
* **Every seeded bench aged into "No data · Bench stopped" ~10 min after
  boot.** The seeder writes one heartbeat per bench at boot and nothing keeps
  it, so the demo decayed into a lab whose benches had all stopped. Under
  --dev --seed a keeper rides the snapshot poller and keeps the seeded,
  checking-in benches' heartbeats current (the "offline" story bench stays
  silent, as seeded).

Production must not change: both fixes are behind `dev` (and the keeper
behind `seed` too), and the last tests here prove the non-dev paths keep the
bridge and leave the poller's hook exactly as they found it.
"""
import pytest

import web_server
from web_app import create_app


class StubAuth:
    def login(self, u, p):
        return ("Kaden Ortiz", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


def _dev_app(tmp_path):
    store, labcore, _w = web_server.build_gateways(
        dev=True, seed=True, no_publish=True, store_path=str(tmp_path / "dev.db"))
    app = create_app(store, labcore=labcore, authenticator=StubAuth(), secret="s",
                     documents_root=str(tmp_path / "docs"), dev_tools=True)
    app.config["TESTING"] = True
    return store, app


def _stop(app):
    # A live (non-dev) boot also starts the import (v4.0.1); stop it before
    # the store is closed under it.
    for key in ("BRIDGE", "IMPORT_SERVICE"):
        svc = app.config.get(key)
        if svc is not None:
            svc.stop()


def _running(app):
    snaps = app.config["SNAPSHOTS"]
    snaps.refresh()
    return {m["machine_uid"]: bool(m.get("module_running"))
            for m in snaps.get()["machines"]}


def test_a_fresh_dev_seed_boot_counts_its_checking_in_benches_as_reporting(tmp_path):
    store, app = _dev_app(tmp_path)
    try:
        web_server.start_transfer(app, store, dev=True)
        app.config["BENCH_REGISTRY"].hydrate(store)    # the boot's warm-up does this
        running = _running(app)
        n_in = sum(running.values())
        assert n_in > 0
        line = app.test_client().get("/api/ui/live").get_json()["transfer"]
        assert line["text"].startswith("Data · %d of %d" % (n_in, len(running))), line
        assert not line["text"].startswith("Data · 0 of"), line
    finally:
        _stop(app)
        store.close()


def test_seeded_benches_keep_checking_in_after_their_boot_heartbeat_ages(tmp_path, monkeypatch):
    import web_app
    from datetime import datetime, timedelta
    store, app = _dev_app(tmp_path)
    try:
        fresh = _running(app)
        alive = {u for u, r in fresh.items() if r}
        assert alive and len(alive) < len(fresh)       # the offline bench stays silent
        # twenty minutes on (past the 15-min heartbeat grace), with nothing
        # touching the heartbeats: the decay the critic saw
        later = datetime.now() + timedelta(minutes=20)
        monkeypatch.setattr(web_app, "_now", lambda: later)
        assert not any(_running(app).values())
        assert web_server.attach_demo_keepers(app, store, dev=True, seed=True,
                                              clock=lambda: later) is True
        app.config["SNAPSHOTS"].on_cycle()             # one poller cycle
        after = _running(app)
        assert {u for u, r in after.items() if r} == alive
    finally:
        store.close()


@pytest.mark.parametrize("dev, seed", [(False, False), (False, True), (True, False)])
def test_the_keeper_is_attached_only_under_dev_and_seed(tmp_path, dev, seed):
    """Production (dev False) and an unseeded dev store get no keeper: the
    poller's hook is left exactly as it was."""
    store, app = _dev_app(tmp_path)
    try:
        snaps = app.config["SNAPSHOTS"]
        before = getattr(snaps, "on_cycle", None)
        assert web_server.attach_demo_keepers(app, store, dev=dev, seed=seed) is False
        assert getattr(snaps, "on_cycle", None) is before
    finally:
        store.close()


def test_production_keeps_the_bridge(tmp_path):
    """The bridge is dropped under --dev only. A non-dev split boot keeps it
    and starts it, as before (test_legacy_import holds the rest of that)."""
    from labcore_counter import CountingLabCore
    from lem_store import LocalStoreGateway
    store = LocalStoreGateway(str(tmp_path / "lem.db"))
    app = create_app(store, labcore=CountingLabCore(), authenticator=StubAuth(),
                     secret="s", documents_root=str(tmp_path / "docs"))
    try:
        out = web_server.start_transfer(app, store, dev=False)
        assert out["bridge"] is True and app.config.get("BRIDGE") is not None
    finally:
        _stop(app)
        store.close()
