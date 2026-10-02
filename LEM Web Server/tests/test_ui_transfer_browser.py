"""The data transfer's pages in a real browser (transfer §14, piece T-P12).

Three things a person can check by hand, checked the same way here:

* **Open on the three pages, LEM still looks idle.** The record's Data
  transfer section, /results/conflicts and Settings › Transfer each re-read
  on a timer. The unattended updater installs a release only while
  ``/healthz`` ``idle_seconds`` is high, so with all three open it must keep
  rising and ``last_activity`` must not move: every timed request is a GET.
  The pages' own polls are counted from the browser, so "nothing reset the
  clock" cannot pass by the pages simply not polling.
  ``LEM_UI_IDLE_SECONDS`` sets how long (45 s by default).
* **Keep and Send work end to end, and only after the server said yes.**
  Signed out, the button opens the sign-in sheet; signed in, the held result
  moves to Decided from the server's own re-read, and the bench's next sync
  answer carries the decision.
* **The foot says where the data is, and links to where it is fixed.**

Skipped without selenium or headless Chrome (run with GC hub's venv). The
port is ``LEM_UI_TRANSFER_PORT`` (default 5714); a busy port skips.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request

import pytest

webdriver = pytest.importorskip("selenium.webdriver")

import bench_v2_kit as kit  # noqa: E402
from bench_v2_kit import Bench, UID  # noqa: E402
from labcore_counter import CountingLabCore  # noqa: E402
from labcore_gateway import FakeLabCoreGateway  # noqa: E402
from web_app import create_app  # noqa: E402

PORT = int(os.environ.get("LEM_UI_TRANSFER_PORT", "5714"))
IDLE_SECONDS = float(os.environ.get("LEM_UI_IDLE_SECONDS", "45"))


def _conflict(seq, epoch, uid=UID):
    return {"seq": seq, "epoch": epoch, "uid": uid, "kind": "conflict",
            "ts": "2026-10-01T09:41:00-07:00", "module": "4.0.0",
            "of": ["%s:%d" % (epoch, seq - 1)],
            "cells": [["38214", "IBP", "151.9", "151.6", "2026-10-01T09:10:00", "dana"]]}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from werkzeug.serving import make_server
    from live_presence import LivePresence
    root = tmp_path_factory.mktemp("lem-transfer")
    store = FakeLabCoreGateway()
    kit.seed_machine(store)
    kit.seed_machine(store, uid="v39-bench", title="Old Bench")
    app = create_app(store, labcore=CountingLabCore(), authenticator=kit.StubAuth(),
                     secret="ui-test", live=LivePresence(), live_token=kit.SHARED_TOKEN,
                     documents_root=str(root))
    app.config["SNAPSHOTS"].ensure_schema()
    app.config["SNAPSHOTS"].refresh()
    c = app.test_client()
    bench = Bench(c, kit.enroll(c))
    bench.journal(1)
    bench.journal(1, make=_conflict)
    assert bench.sync().status_code == 200
    app.config["SNAPSHOTS"].refresh()
    app.config["WARM"]()
    try:
        srv = make_server("127.0.0.1", PORT, app, threaded=True)
    except OSError as exc:
        pytest.skip(f"port {PORT} is busy: {exc}")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    srv.base = f"http://127.0.0.1:{PORT}"
    srv.bench = bench
    yield srv
    srv.shutdown()


def _chrome():
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    for arg in ("--headless=new", "--no-sandbox", "--disable-gpu",
                "--force-device-scale-factor=1", "--window-size=1440,900"):
        opts.add_argument(arg)
    try:
        return webdriver.Chrome(options=opts)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"headless Chrome unavailable: {exc}")


@pytest.fixture
def drv(server):
    d = _chrome()
    d.get(server.base + "/healthz")
    d.execute_script("localStorage.clear();")
    yield d
    d.quit()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read().decode())


def _wait(fn, seconds):
    end = time.time() + seconds
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
    return False


def _js(d, script):
    return d.execute_script(script)


def test_the_foot_names_the_data_and_opens_transfer(server, drv):
    drv.get(server.base + "/results/conflicts")
    assert _wait(lambda: _js(drv, "return !document.getElementById('data-line').hidden"), 8)
    text = _js(drv, "return document.getElementById('data-text').textContent")
    assert text.startswith("Data · "), text
    label = _js(drv, "return document.getElementById('data-line').getAttribute('aria-label')")
    assert label.startswith("Data: ") and "waiting at the benches" in label, label
    drv.find_element("id", "data-line").click()
    assert _wait(lambda: drv.current_url.endswith("/settings#transfer"), 5)
    assert _wait(lambda: _js(drv, "return document.querySelectorAll('#tr-benches-body tr').length") >= 1, 8)


def test_three_open_pages_leave_lem_idle(server, drv):
    """The record, the conflicts and Settings › Transfer, open together."""
    drv.get(server.base + "/instruments/" + UID)
    for path in ("/results/conflicts", "/settings#transfer"):
        drv.switch_to.new_window("tab")
        drv.get(server.base + path)
    handles = drv.window_handles
    assert len(handles) == 3
    start = _get(server.base + "/healthz")
    seen = [start["idle_seconds"]]
    end = time.time() + IDLE_SECONDS
    while time.time() < end:
        time.sleep(min(5.0, max(0.1, end - time.time())))
        h = _get(server.base + "/healthz")
        assert h["idle_seconds"] > seen[-1], (seen, h)
        assert h["last_activity"] == start["last_activity"], h["last_activity"]
        seen.append(h["idle_seconds"])
    assert seen[-1] - seen[0] >= IDLE_SECONDS - 6, seen
    # The pages really read (a page that never re-read would pass the
    # above): the tab in front polls on its timer; the two behind it read
    # once on arrival and then nothing while hidden, and re-read the moment
    # they are in front again.
    count = lambda needle: _js(drv, "return performance.getEntriesByType('resource')"
                                    ".filter(e => e.name.includes(%r)).length" % needle)
    polls = {}
    for hdl, needle in zip(handles, ("/api/ui/transfer/", "/api/results/conflicts",
                                     "/api/transfer/overview")):
        drv.switch_to.window(hdl)
        polls[needle] = count(needle)
    assert polls["/api/transfer/overview"] >= 1 + int(IDLE_SECONDS // 30), polls
    assert polls["/api/ui/transfer/"] >= 1 and polls["/api/results/conflicts"] >= 1, polls
    print("idle_seconds %.1f -> %.1f over %d samples; polls %r"
          % (seen[0], seen[-1], len(seen), polls))


def test_a_tab_brought_back_re_reads_at_once(server, drv):
    drv.get(server.base + "/instruments/" + UID)
    assert _wait(lambda: _js(drv, "return performance.getEntriesByType('resource')"
                                  ".filter(e => e.name.includes('/api/ui/transfer/')).length") >= 1, 8)
    before = _js(drv, "return performance.getEntriesByType('resource')"
                      ".filter(e => e.name.includes('/api/ui/transfer/')).length")
    drv.execute_cdp_cmd("Emulation.setFocusEmulationEnabled", {"enabled": True})
    _js(drv, "Object.defineProperty(document, 'visibilityState', {value: 'visible', configurable: true});"
             "document.dispatchEvent(new Event('visibilitychange'));")
    assert _wait(lambda: _js(drv, "return performance.getEntriesByType('resource')"
                                  ".filter(e => e.name.includes('/api/ui/transfer/')).length") > before, 3)


def test_keep_signed_out_asks_to_sign_in_then_decides(server, drv):
    drv.get(server.base + "/results/conflicts")
    assert _wait(lambda: len(drv.find_elements("css selector", ".cf-row")) == 1, 8)
    drv.find_element("css selector", ".cf-row button[data-choice='keep']").click()
    assert _wait(lambda: _js(drv, "return document.getElementById('signin-sheet').open"), 5)
    drv.find_element("id", "signin-user").send_keys("ryan")
    drv.find_element("id", "signin-pass").send_keys("good")
    drv.find_element("css selector", "#signin-form button[type='submit']").click()
    # the decision goes ahead after sign-in, and lands in Decided from the
    # server's own re-read
    assert _wait(lambda: len(drv.find_elements("css selector", ".cf-row")) == 0, 10)
    assert _wait(lambda: "Kept LabCore's 151.6" in _js(
        drv, "return document.getElementById('cf-decided').innerText"), 5)
    assert not _js(drv, "return document.getElementById('cf-empty').hidden")
    ans = server.bench.journal(1).sync().get_json()
    assert [r["choice"] for r in ans["resolutions"]] == ["keep"]
