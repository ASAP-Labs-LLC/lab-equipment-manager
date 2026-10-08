"""Two §11 guards that only a running page can answer, in headless Chrome.

* **No timer-driven POST on any page (idle-safety, A.7).** The updater
  deploys a release only when nobody has WRITTEN for five minutes
  (`/healthz` `idle_seconds`). A page that POSTs on a timer is a person who
  never stops typing: the lab never goes idle, and no release ever lands,
  silently. The old floor did exactly this class of thing more than once.
  A source scan cannot see it (a POST can sit three calls away from the
  setInterval that reaches it), so every page is loaded, left alone, and
  everything it sends is recorded. Its clock runs 30 times fast (timers,
  `Date` and `performance.now`), so 8 s of waiting is 4 minutes of the
  page's own time: past the 90 s stale rule, the walls' rotations and their
  60 s refresh, the live feed's backoff. Anything but GET and HEAD fails.
  Signed out AND signed in, because some timers only run for a person.
* **Every drawn list row opens a record.** The Instruments table and
  /quality's Latest checks, as drawn: each row carries a link to
  `/instruments/<uid>`. (The API's hrefs are checked in test_ia_guards.py;
  this is the page that a person clicks.)

The recorder is checked before it is trusted: a page made to POST on a
timer must be caught, or every idle page would pass on a recorder that
records nothing.

Skipped when selenium or headless Chrome is missing (LEM's own venv has no
selenium); run it with GC hub's venv. Port ``LEM_UI_GUARDS_PORT`` (default
5700); a busy port skips. In-process app on the fake LabCore with the demo
floor: nothing here can reach a real LabCore.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

webdriver = pytest.importorskip("selenium.webdriver")

import demo_floor  # noqa: E402
from labcore_gateway import FakeLabCoreGateway  # noqa: E402
from web_app import create_app  # noqa: E402

PORT = int(os.environ.get("LEM_UI_GUARDS_PORT", "5700"))
PASSWORD = "ui-pass"
SPEED = 30          # page clock multiplier
IDLE_S = 8.0        # real seconds each page is left alone (= 4 page-minutes)

#: Every page a person or a TV opens (ia-final §1).
PAGES = ["/", "/?view=map", "/instruments/gc-1", "/checklists/opening",
         "/checklists/edit", "/checklists/trends", "/quality", "/quality/standards",
         "/quality/standards/" + demo_floor.STANDARD, "/logs", "/settings", "/help",
         "/results/conflicts", "/floor", "/qc", "/wall?show=floor,qc&every=60"]

#: Installed before any page script runs. Records every request that is not
#: a GET (fetch, XHR, sendBeacon, a form submit) and speeds the clock up.
RECORDER = """
(() => {
  const K = %d;
  const sent = window.__sent = [];
  const note = (how, method, url) => {
    method = String(method || 'GET').toUpperCase();
    if (method !== 'GET' && method !== 'HEAD') sent.push(how + ' ' + method + ' ' + url);
  };
  const F = window.fetch;
  window.fetch = function (input, init) {
    const m = (init && init.method) || (input && typeof input === 'object' && input.method) || 'GET';
    note('fetch', m, (input && input.url) || input);
    return F.apply(this, arguments);
  };
  const O = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (m, u) { note('xhr', m, u); return O.apply(this, arguments); };
  if (navigator.sendBeacon) {
    const B = navigator.sendBeacon.bind(navigator);
    navigator.sendBeacon = (u, d) => { note('beacon', 'POST', u); return B(u, d); };
  }
  document.addEventListener('submit', (e) => {
    const f = e.target; note('form', f.method || 'GET', f.action);
  }, true);
  const S = HTMLFormElement.prototype.submit;
  HTMLFormElement.prototype.submit = function () { note('form.submit', this.method || 'GET', this.action); return S.apply(this, arguments); };
  // the clock, K times fast
  const ST = window.setTimeout, SI = window.setInterval;
  window.setTimeout = (fn, ms, ...a) => ST(fn, Math.max(0, (Number(ms) || 0) / K), ...a);
  window.setInterval = (fn, ms, ...a) => SI(fn, Math.max(4, (Number(ms) || 0) / K), ...a);
  const RD = Date, t0 = RD.now();
  const fake = () => t0 + (RD.now() - t0) * K;
  class D extends RD {
    constructor(...a) { if (a.length) super(...a); else super(fake()); }
    static now() { return fake(); }
  }
  window.Date = D;
  const PN = performance.now.bind(performance), p0 = PN();
  performance.now = () => p0 + (PN() - p0) * K;
})();
""" % SPEED


class Auth:
    def login(self, u, p):
        return (u or "card", "tok", "") if p == PASSWORD else (None, "", "bad")

    def logout(self, t):
        pass


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from werkzeug.serving import make_server
    root = tmp_path_factory.mktemp("lem-guards")
    gw = FakeLabCoreGateway()
    app = create_app(gw, secret="ui-guards", authenticator=Auth(), documents_root=str(root))
    app.config["SNAPSHOTS"].ensure_schema()
    demo_floor.seed(gw, documents_root=str(root))
    app.config["SNAPSHOTS"].refresh()
    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = "setup"
    # a round to tick, so the round page has rows and its timers run
    assert c.post("/api/checklists", json={
        "uid": "a1b2c3d4e5f6", "name": "Opening round", "slot": "opening", "due_time": "09:30",
        "items": [{"uid": "i1", "text": "Nitrogen", "entry_type": "none"}]}).status_code == 200
    try:
        srv = make_server("127.0.0.1", PORT, app, threaded=True)
    except OSError as exc:
        pytest.skip(f"port {PORT} is busy: {exc}")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.base = f"http://127.0.0.1:{PORT}"
    yield srv
    srv.shutdown()


@pytest.fixture(scope="module")
def drv(server):
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    for arg in ("--headless=new", "--no-sandbox", "--disable-gpu", "--window-size=1440,900"):
        opts.add_argument(arg)
    try:
        d = webdriver.Chrome(options=opts)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"headless Chrome unavailable: {exc}")
    d.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": RECORDER})
    yield d
    d.quit()


def _sign(d, base, who):
    d.get(base + "/help")
    if who:
        st = d.execute_script(
            "return fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},"
            "body:JSON.stringify({username:arguments[0],password:arguments[1]})}).then(r=>r.status);",
            who, PASSWORD)
        assert st == 200
    else:
        d.execute_script("return fetch('/api/logout',{method:'POST'}).then(r=>r.status);")


def _idle(d, url):
    d.get(url)
    end = time.time() + 10
    while time.time() < end and d.execute_script("return document.readyState") != "complete":
        time.sleep(0.1)
    assert d.execute_script("return Array.isArray(window.__sent)"), "the recorder is not installed"
    time.sleep(IDLE_S)
    return d.execute_script("return {sent: window.__sent.slice(), href: location.href,"
                            " ticks: Date.now() - performance.timeOrigin};")


def test_the_recorder_catches_a_timer_post(drv, server):
    """The control: a page that POSTs from a timer is caught, through each
    road a page could use, and the clock really runs fast."""
    drv.get(server.base + "/help")
    drv.execute_script("""
        setTimeout(() => fetch('/api/me', {method: 'POST'}).catch(() => {}), 60000);
        setInterval(() => { const x = new XMLHttpRequest(); x.open('DELETE', '/nope'); x.send(); }, 120000);
        window.__t0 = Date.now();""")
    time.sleep(5)
    got = drv.execute_script("return {sent: window.__sent.slice(), dt: Date.now() - window.__t0};")
    assert any(s.startswith("fetch POST") for s in got["sent"]), got
    assert any(s.startswith("xhr DELETE") for s in got["sent"]), got
    assert got["dt"] > 100000, "the page clock is not running fast"


@pytest.mark.parametrize("who", ["", "Cody"], ids=["signed-out", "signed-in"])
def test_an_idle_page_sends_nothing_but_gets(drv, server, who):
    _sign(drv, server.base, who)
    bad = {}
    for path in PAGES:
        got = _idle(drv, server.base + path)
        if got["sent"]:
            bad[path] = got["sent"]
        assert got["href"].startswith(server.base), (path, got["href"])
    assert bad == {}, "a page wrote on its own while nobody touched it: %r" % bad


def test_every_drawn_row_opens_a_record(drv, server):
    _sign(drv, server.base, "")
    for path, rows in (("/", "#inst-cards .irow"), ("/quality", "#q-rows tr")):
        drv.get(server.base + path)
        got = None
        for _ in range(60):
            got = drv.execute_script("""
                const rows = [...document.querySelectorAll(arguments[0])];
                return {n: rows.length, bad: rows.filter(r => !r.querySelector('a[href^="/instruments/"]'))
                                                   .map(r => r.innerText.slice(0, 60))};""", rows)
            if got["n"]:
                break
            time.sleep(0.1)
        assert got["n"] >= 5, (path, got)
        assert got["bad"] == [], (path, got["bad"])
