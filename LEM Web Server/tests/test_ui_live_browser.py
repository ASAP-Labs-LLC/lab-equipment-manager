"""The live feed in a real browser: headless Chrome against a real server.

The bar for the live feed (ia-final §14, piece 2) is three sentences a
person can check by hand, so they are checked here the same way:

* **Three tabs open, and LEM still looks idle.** The updater only deploys
  when ``/healthz`` ``idle_seconds`` is high. Three open shell pages polling
  must leave it rising and ``last_activity`` untouched: no page may POST on a
  timer (constraints A.7). ``LEM_UI_IDLE_SECONDS`` sets how long (45 s by
  default; the bar's ten minutes is ``LEM_UI_IDLE_SECONDS=600``).
* **Kill the server.** Within 15 s the live row says "Reconnecting… · last
  update N s ago", the banner says LEM has not answered, and for the next
  95 s nothing ever says "Live" again. A wall that froze at green must not
  look like a wall that is green.
* **What the nav says is what the feed says.** The nav-meta, the rail
  badges and the bell are read off the page and compared with
  ``/api/ui/live`` itself.

Skipped when selenium or headless Chrome is missing (LEM's own venv has no
selenium); run it with a Python that has both, e.g. GC hub's venv. The port
is ``LEM_UI_LIVE_PORT`` (default 5701); a busy port skips rather than fails.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request

import pytest

webdriver = pytest.importorskip("selenium.webdriver")

import demo_floor  # noqa: E402
from labcore_gateway import FakeLabCoreGateway  # noqa: E402
from web_app import create_app  # noqa: E402

PORT = int(os.environ.get("LEM_UI_LIVE_PORT", "5701"))
IDLE_SECONDS = float(os.environ.get("LEM_UI_IDLE_SECONDS", "45"))


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from werkzeug.serving import make_server
    root = tmp_path_factory.mktemp("lem-live")
    gw = FakeLabCoreGateway()
    app = create_app(gw, secret="ui-test", admin_password="ui-pass", documents_root=str(root))
    app.config["SNAPSHOTS"].ensure_schema()
    demo_floor.seed(gw, documents_root=str(root))
    app.config["SNAPSHOTS"].refresh()
    app.config["WARM"]()
    try:
        srv = make_server("127.0.0.1", PORT, app, threaded=True)
    except OSError as exc:
        pytest.skip(f"port {PORT} is busy: {exc}")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    srv.base = f"http://127.0.0.1:{PORT}"
    yield srv
    try:
        srv.shutdown()
    except Exception:  # noqa: BLE001 - already killed by the test
        pass


def _chrome():
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    for arg in ("--headless=new", "--no-sandbox", "--disable-gpu", "--force-device-scale-factor=1",
                "--window-size=1440,900"):
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


def _text(d, ident):
    return d.execute_script("const e=document.getElementById(arguments[0]); return e ? e.textContent : null;", ident)


def _wait(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if pred():
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
    return False


def test_the_shell_says_what_the_feed_says(server, drv):
    drv.get(server.base + "/settings")
    assert _wait(lambda: (_text(drv, "live-text") or "").startswith("Live · updated"), 8), _text(drv, "live-text")
    live = _get(server.base + "/api/ui/live")
    navs = drv.execute_script(
        "return Object.fromEntries([...document.querySelectorAll('.nav-item')].map(a => [a.dataset.nav,"
        " [a.querySelector('.nav-meta').textContent, a.querySelector('.rail-badge').textContent,"
        "  a.getAttribute('aria-label')]]));")
    for key, (meta, badge, label) in navs.items():
        want = live["nav_meta"].get(key)
        assert meta == (want["text"] if want else ""), (key, meta, want)
        assert badge == (want["badge"] if want else ""), (key, badge, want)
        assert label.endswith(want["text"]) if want else "," not in label
    fleet = live["fleet"]
    assert _text(drv, "fleet-text") == "%d of %d benches checking in" % (fleet["checking_in"], fleet["total"])
    # the bell: its count is the items it lists, and each item goes somewhere
    count = int(_text(drv, "bell-count") or 0)
    assert count == live["notifications_unread"] > 0
    drv.execute_script("document.getElementById('bell').click();")
    notes = drv.execute_script(
        "return [...document.querySelectorAll('#bell-list li.note')].map(li => [li.querySelector('.note-level').textContent,"
        " li.querySelector('.note-msg').textContent, li.querySelector('.note-go') && li.querySelector('.note-go').getAttribute('href')]);")
    assert len(notes) == count
    assert all(level and msg and href and href.startswith("/") for level, msg, href in notes), notes
    assert any(m.startswith("Benches can't reach LEM directly.") for _l, m, _h in notes)
    # dismissing one hides it on this computer and the count follows
    drv.execute_script("document.querySelector('#bell-list li.note .note-dismiss').click();")
    assert _wait(lambda: int(_text(drv, "bell-count") or 0) == count - 1, 3)
    # the healthy page has no banner
    assert drv.execute_script("return document.getElementById('paused-banner').hidden;") is True


def test_the_rail_strip_says_it_in_words(server, drv):
    drv.execute_cdp_cmd("Emulation.setDeviceMetricsOverride",
                        {"width": 820, "height": 1180, "deviceScaleFactor": 1, "mobile": False})
    drv.get(server.base + "/settings")
    assert _wait(lambda: drv.execute_script(
        "return document.getElementById('rail-status').innerText;").startswith("Live · updated"), 8)
    strip = drv.execute_script("return document.getElementById('rail-status').innerText;")
    assert "benches checking in" in strip, strip


def test_three_open_tabs_leave_lem_idle(server, drv):
    """Three shell pages polling for IDLE_SECONDS: idle_seconds only rises."""
    drv.get(server.base + "/settings")
    for path in ("/help", "/settings"):
        drv.switch_to.new_window("tab")
        drv.get(server.base + path)
    handles = drv.window_handles
    assert len(handles) == 3
    for h in handles:                       # every tab is really polling
        drv.switch_to.window(h)
        assert _wait(lambda: (_text(drv, "live-text") or "").startswith("Live"), 8)
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
    calls = drv.execute_script("return performance.getEntriesByType('resource')"
                               ".filter(e => e.name.includes('/api/ui/live')).length;")
    print("idle_seconds %.1f -> %.1f over %d samples; last_activity %r; this tab's polls %d"
          % (seen[0], seen[-1], len(seen), start["last_activity"], calls))


def test_killing_the_server_is_said_within_15_s_and_never_live_again(server, drv):
    """Runs last: it stops the module's server."""
    drv.get(server.base + "/settings")
    assert _wait(lambda: (_text(drv, "live-text") or "").startswith("Live"), 8)
    server.shutdown()
    server.server_close()
    killed = time.time()
    assert _wait(lambda: (_text(drv, "live-text") or "").startswith("Reconnecting… · last update "), 15), \
        _text(drv, "live-text")
    took = time.time() - killed
    assert took <= 15, took
    assert _wait(lambda: (_text(drv, "paused-banner") or "").startswith("LEM has not answered for "), 20)
    words = set()
    while time.time() - killed < 95:
        t = _text(drv, "live-text")
        words.add(t.split(" · ")[0])
        assert not t.startswith("Live"), t
        strip = drv.execute_script("return document.getElementById('rs-record-text').textContent;")
        assert not strip.startswith("Live"), strip
        time.sleep(1)
    assert words == {"Reconnecting…"}, words
    banner = _text(drv, "paused-banner")
    assert "current" not in banner.lower()
    print("Reconnecting after %.1f s; at 95 s the row read %r and the banner %r"
          % (took, _text(drv, "live-text"), banner))
