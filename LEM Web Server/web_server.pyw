#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
web_server.pyw — V5 entry point (LabCore-backed Lab Equipment Manager).

Wires the injected LabCore gateway into the Flask app factory and serves the
dashboard. In production it talks to a live LabCore over HTTP; --dev spins up an
in-memory FakeLabCoreGateway (optionally seeded) so the UI runs with no LabCore.

Runs in the system tray by default, like the old LEM: right-click for Open in
browser / Show-Hide console / Restart / Exit, and it restarts itself when the
code changes so editing a template needs no manual bounce. Falls back to a plain
console server when a tray isn't possible.

Usage:
    python web_server.pyw --host 0.0.0.0 --port 5557
    python web_server.pyw --dev --seed          # offline demo with sample data
    python web_server.pyw --no-tray             # console only
    python web_server.pyw --no-reload           # tray, but don't watch the code

Env:
    LABCORE_URL                   — LabCore base URL (default https://labvision.asaplabs.net)
    LABMGR_ADMIN_PASSWORD         — admin password (default Admin1)
    LABMGR_SECRET                 — Flask session secret
"""

from __future__ import annotations

import argparse
import os
import sys
import threading


def _build_gateway(dev: bool, seed: bool):
    if dev:
        from labcore_gateway import FakeLabCoreGateway

        gw = FakeLabCoreGateway()
        if seed:
            _seed_demo(gw)
        return gw, "fake (dev)"

    from labcore_gateway import HttpLabCoreGateway

    gw = HttpLabCoreGateway()  # resolves LABCORE_URL → https://labvision.asaplabs.net
    return gw, gw.base_url


def _seed_demo(gw) -> None:
    """Populate a fake gateway with one machine and QC data for a live demo.

    Every answer is read, like every other write in this app. These three were
    the last unjudged `gw.write(...)` calls in the tree — and while a demo
    seeder is the lowest-stakes place in it, an example of the habit is
    exactly how the habit spreads. A half-seeded demo also produces the
    confusing empty floor the seeder exists to avoid, so failing loudly here
    is worth four lines.
    """
    from datetime import datetime

    from db_config_store import DbConfigStore
    from labcore_result import LabCoreError, confirm_write
    from models import AppConfig, BoxConfig, SampleSpec, SampleTestSpec, WatchedTarget

    def seed(operation, params):
        try:
            confirm_write(gw.write(operation, params))
        except LabCoreError as exc:
            raise RuntimeError(
                "--seed could not write the demo data ({0}: {1}). The "
                "dashboard would come up empty and look broken.".format(
                    operation, exc)) from exc

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    seed("insert_sample", {"lab_id": "STD-1", "customer": "QC Standard"})
    seed("add_test", {"lab_id": "STD-1", "test_name": "Flash Point"})
    seed("update_cell", {"lab_id": "STD-1", "test_name": "Flash Point",
                         "value": "65", "updated_at": now})

    sample = SampleSpec(name="Diesel QC", sample_id_val="STD-1",
                        tests=[SampleTestSpec(name="Flash", value_col="Flash Point",
                                              expected=65.0, std_dev=2.0, units="C")])
    box = BoxConfig(uid="gc1", title="GC-1 (demo)", csv_path="", pos=(80.0, 80.0),
                    watched_targets=[WatchedTarget(sample="Diesel QC", test="Flash")])
    cfg = AppConfig(version=5, poll_minutes=5, map_locked=False,
                    sample_id_column="Lab ID", samples=[sample], boxes=[box])
    ok, why = DbConfigStore(gw).save(cfg)
    if not ok:
        raise RuntimeError(
            "--seed could not save the demo configuration ({0}). The "
            "dashboard would come up with no instruments on it.".format(why))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LEM V5 — LabCore-backed dashboard")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5557)
    parser.add_argument("--dev", action="store_true", help="use an in-memory fake LabCore")
    parser.add_argument("--seed", action="store_true", help="seed demo data (with --dev)")
    parser.add_argument("--no-tray", action="store_true",
                        help="console only, no system tray icon")
    parser.add_argument("--no-reload", action="store_true",
                        help="don't restart when the code changes")
    parser.add_argument("--no-publish", action="store_true",
                        help="do not publish this server's address to LabCore. "
                             "For deployment health checks, which run on a "
                             "scratch port that closes moments later — "
                             "advertising it would point every bench at a dead "
                             "port until the next real boot.")
    return parser


def _start_live_channel(app, gateway, host, port) -> str:
    from live_presence import start_live_channel

    return start_live_channel(app, gateway, host, port)


def publish_live(*, app, gateway, host, port, no_publish: bool):
    """Tell LabCore where the benches should push, unless asked not to.

    Returns the published URL, or ``None`` when publishing was skipped.
    """
    if no_publish:
        return None
    return _start_live_channel(app, gateway, host, port)


def main(argv) -> int:
    args = build_parser().parse_args(argv)

    from web_app import create_app

    gateway, where = _build_gateway(args.dev, args.seed)
    if not args.dev and not gateway.is_running():
        print(f"WARNING: LabCore not reachable at {where}. "
              f"Writes will fail until it is running.", file=sys.stderr)

    app = create_app(gateway)
    # The server — not the app factory — owns the background refresher, so
    # requests are served from memory and LabCore sees one reader, not one per
    # screen. Started before serving so the first page has something to show.
    snapshots = app.config["SNAPSHOTS"]
    snapshots.start()
    # Tell LabCore where the benches should push their liveness, and with what
    # token. Boot, not create_app: the factory must stay side-effect free. Never
    # fatal — with no live config, modules simply skip the push and the floor
    # falls back to the record, which is how it worked before this existed.
    live_where = publish_live(app=app, gateway=gateway, host=args.host,
                              port=args.port, no_publish=args.no_publish)
    if live_where:
        print(f"Live channel published to LabCore as {live_where}")
    else:
        print("Live channel NOT published (--no-publish): benches keep the "
              "address they already have.")
    # Warm the caches off the request path. The first visitor to the checklist
    # page used to wait 7.5s for a cold cache and a busy LabCore; a thread can
    # wait instead. Daemon so it never holds up a shutdown or a restart.
    threading.Thread(target=app.config["WARM"], daemon=True,
                     name="lem-warm").start()

    import tray

    def serve() -> None:
        # A restart spawns this process while the old one is still listening, so
        # binding immediately fails with "Address already in use" and the new
        # server dies — which looks exactly like the restart having closed the
        # program. Wait for the handover first.
        if not tray.port_is_free(args.host, args.port):
            waited = tray.wait_for_port_free(args.host, args.port)
            if not waited:
                holder = tray.who_holds_port(args.port)
                tray.note(
                    f"port {args.port} is still in use"
                    + (f" by {holder}" if holder else "")
                    + " — not starting. Free it with:  "
                    + tray.how_to_free_port(args.port))
                return
        # use_reloader=False always: our own watcher owns restarting, and
        # Flask's re-execs the process, which would take the tray icon with it.
        app.run(host=args.host, port=args.port, threaded=True,
                use_reloader=False)

    if not args.no_tray and tray.tray_available():
        print(f"LEM V5 on http://{args.host}:{args.port}  (LabCore: {where})")
        tray.run_tray(args.host, args.port, serve,
                      root=os.path.dirname(os.path.abspath(__file__)),
                      watch=not args.no_reload)
        return 0

    if not args.no_tray:
        print("NOTE: no system tray available here (needs pystray + Pillow "
              "and a desktop session). Running in the console.")
    print(f"LEM V5 serving on http://{args.host}:{args.port}  (LabCore: {where})")
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
