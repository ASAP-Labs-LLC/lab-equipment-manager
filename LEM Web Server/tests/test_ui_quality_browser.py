"""The QC pages in a real browser: headless Chrome, real server (piece 8).

What a person checks by looking is checked here the same way:

* **T5, walked** (ia-final §8): from Instruments, signed in, add a standard
  with one check and check it on GC-2. The bar is 5 clicks, 6 fields typed
  and 4 screens (start, QC, the sheet, the standard's page), and the page
  Save lands on says "GC-2 · Flash Point · No verdict yet" straight away.
  Counted the spec's way: a click is a pointer activation, a field typed
  into is one typed, a screen is a view you re-orient in.
* **One verdict column** on every QC table as drawn (judge J3), and no
  column named for control.
* **Nothing scrolls sideways** at 1440, 820 and 390, in both themes; a
  table at 820 folds rather than overflowing (§6).
* **No red fills or borders** (§0.1): status colour only as glyph + word
  (glyphs and the band track's marks are where it belongs, so not counted).
* **Signed out, New standard asks you to sign in first**, titled for the
  act, rather than opening a form that cannot be saved.
* **"Who reports this test" that could not be read is said**, and every
  instrument is still offered, by name.

Skipped when selenium or headless Chrome is missing (LEM's own venv has no
selenium); run it with GC hub's venv. The port is ``LEM_UI_QUALITY_PORT``
(default 5703, piece 8's port); a busy port skips.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

webdriver = pytest.importorskip("selenium.webdriver")
from selenium.webdriver.common.by import By  # noqa: E402

import demo_floor  # noqa: E402
from labcore_gateway import FakeLabCoreGateway  # noqa: E402
from web_app import create_app  # noqa: E402

PORT = int(os.environ.get("LEM_UI_QUALITY_PORT", "5703"))


class StubAuth:
    def login(self, u, p):
        return ("cody", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from werkzeug.serving import make_server
    root = tmp_path_factory.mktemp("lem-quality")
    gw = FakeLabCoreGateway()
    app = create_app(gw, authenticator=StubAuth(), secret="ui-test", documents_root=str(root))
    app.config["SNAPSHOTS"].ensure_schema()
    demo_floor.seed(gw, documents_root=str(root))
    # LabCore's catalogue of tests, as `--dev --seed` gives it (and two more,
    # so the type-ahead has something to rank)
    gw.write("insert_sample", {"lab_id": "STD-1", "customer": "QC Standard"})
    for test in ("Flash Point", "Sulfur", "ASTM D93 - Flash Point by Pensky-Martens Closed Cup"):
        gw.write("add_test", {"lab_id": "STD-1", "test_name": test})
    app.config["SNAPSHOTS"].refresh()
    try:
        srv = make_server("127.0.0.1", PORT, app, threaded=True)
    except OSError as exc:
        pytest.skip(f"port {PORT} is busy: {exc}")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    srv.base = f"http://127.0.0.1:{PORT}"
    srv.app, srv.gw = app, gw
    yield srv
    srv.shutdown()


@pytest.fixture(scope="module")
def drv(server):
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    for arg in ("--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars"):
        opts.add_argument(arg)
    try:
        d = webdriver.Chrome(options=opts)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"headless Chrome unavailable: {exc}")
    yield d
    d.quit()


def _wait(fn, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = fn()
        except Exception:  # noqa: BLE001
            v = None
        if v:
            return v
        time.sleep(0.05)
    return None


def _size(d, w, h):
    d.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})


def _session(d, base, signed_in, theme="light"):
    d.get(base + "/healthz")
    d.delete_all_cookies()
    d.get(base + "/help")
    d.execute_script("localStorage.setItem('lem.theme', arguments[0])", theme)
    if signed_in:
        st = d.execute_script("return fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},"
                              "body:JSON.stringify({username:'cody',password:'good'})}).then(r=>r.status)")
        assert st == 200


def _open(d, base, path):
    d.get(base + path)
    assert _wait(lambda: d.execute_script(
        "return !!document.querySelector('#q-rows tr, #s-rows tr, #uon-list li, #uon-none:not([hidden])')")), path
    time.sleep(0.2)


class Walk:
    def __init__(self, d):
        self.d, self.clicks, self.typed, self.screens = d, 0, 0, []

    def screen(self, name):
        self.screens.append(name)

    def click(self, el):
        self.d.execute_script("arguments[0].scrollIntoView({block:'center'})", el)
        el.click()
        self.clicks += 1

    def type(self, el, text):
        self.d.execute_script("arguments[0].focus()", el)
        el.send_keys(text)
        self.typed += 1


def test_t5_add_a_standard_and_check_it_on_gc2(server, drv):
    _size(drv, 1440, 900)
    _session(drv, server.base, signed_in=True)
    w = Walk(drv)
    drv.get(server.base + "/")
    assert _wait(lambda: drv.find_element(By.CSS_SELECTOR, "#inst-cards .irow"))
    w.screen("Instruments")
    w.click(drv.find_element(By.CSS_SELECTOR, "a.nav-item[data-nav='qc']"))
    assert _wait(lambda: drv.find_element(By.CSS_SELECTOR, "#q-rows tr"))
    w.screen("QC")
    w.click(drv.find_element(By.ID, "new-open"))
    assert _wait(lambda: drv.find_element(By.ID, "new-sheet").get_attribute("open") is not None)
    w.screen("New standard")
    w.type(drv.find_element(By.ID, "new-name"), "Flash CRM lot 7")
    w.type(drv.find_element(By.ID, "new-lab"), "FCRM7")
    w.type(drv.find_element(By.ID, "new-test"), "Flash")
    opt = _wait(lambda: next((e for e in drv.find_elements(By.CSS_SELECTOR, "#new-test-list li")
                              if e.is_displayed() and e.text.startswith("Flash Point")), None))
    assert opt, "the type-ahead offered no Flash Point"
    w.click(opt)
    w.type(drv.find_element(By.ID, "new-exp"), "60.0")
    w.type(drv.find_element(By.ID, "new-sd"), "1.0")
    w.type(drv.find_element(By.ID, "new-units"), "C")
    # the testers that already report Flash Point come first
    chips = _wait(lambda: [c for c in drv.execute_script(
        "return [...document.querySelectorAll('#new-chips > *')].map(e => e.textContent)") if c])
    assert chips[0] == "Report this test" and set(chips[1:4]) == {"PAC Flash 1", "PAC Flash 2", "Pensky-Martens 1"}, chips
    gc2 = next(e for e in drv.find_elements(By.CSS_SELECTOR, "#new-chips button") if e.text.strip() == "GC-2")
    w.click(gc2)
    assert drv.find_element(By.ID, "new-sum").text == "Saves Flash CRM lot 7 and checks Flash Point on GC-2."
    assert drv.find_element(By.ID, "new-band").text == "Passes 58.0 – 60.0 – 62.0 °C"
    w.click(drv.find_element(By.ID, "new-go"))
    assert _wait(lambda: "/quality/standards/Flash%20CRM%20lot%207" in drv.current_url)
    assert _wait(lambda: drv.find_element(By.CSS_SELECTOR, "#uon-list li"))
    w.screen("Flash CRM lot 7")
    line = drv.find_element(By.CSS_SELECTOR, "#uon-list .uon-line").text
    assert line == "GC-2 · Flash Point · No verdict yet", line
    assert (w.clicks, w.typed, len(w.screens)) == (5, 6, 4), (w.clicks, w.typed, w.screens)
    assert drv.find_element(By.CSS_SELECTOR, "[data-testid=std-pill]").text == "Certificate needed"


ONE_VERDICT = r"""
const heads = [...document.querySelectorAll('table thead th')].map(t => t.textContent.trim());
const cells = [...document.querySelectorAll('#q-rows tr')].map(tr => tr.querySelectorAll('.verdict').length);
return { verdicts: heads.filter(t => t === 'Verdict').length, control: heads.filter(t => /control/i.test(t)),
         rows: cells.length, perRow: [...new Set(cells)] };
"""


def test_latest_checks_has_one_verdict_column_as_drawn(server, drv):
    _size(drv, 1440, 900)
    _session(drv, server.base, signed_in=False)
    _open(drv, server.base, "/quality")
    got = drv.execute_script(ONE_VERDICT)
    assert got["verdicts"] == 1 and got["control"] == [], got
    assert got["rows"] >= 10 and got["perRow"] == [1], got
    # worst first: the three stops lead
    words = drv.execute_script("return [...document.querySelectorAll('#q-rows .c-verdict .verdict')].map(e => e.textContent)")
    assert words[:3] == ["Out of spec"] * 3, words


RED = r"""
const isRed = (c) => { const m = c.match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?/); if (!m) return false;
  const r = +m[1], g = +m[2], b = +m[3], a = m[4] === undefined ? 1 : +m[4]; if (a < 0.05) return false;
  return r >= 150 && g <= 120 && b <= 120 && r - g > 70; };
const hits = [];
for (const e of document.querySelectorAll('main *')) {
  // a glyph and the band track's marks are where status colour belongs
  if (e.closest('.glyph, .tglyph, .track')) continue;
  const r = e.getBoundingClientRect(); if (!r.width || !r.height) continue;
  const s = getComputedStyle(e); if (s.visibility === 'hidden' || s.display === 'none') continue;
  if (isRed(s.backgroundColor) && r.width > 14) hits.push(['bg', e.tagName, e.className && String(e.className)]);
  for (const side of ['Top', 'Right', 'Bottom', 'Left'])
    if (parseFloat(s['border' + side + 'Width']) > 0 && s['border' + side + 'Style'] !== 'none' && isRed(s['border' + side + 'Color'])) { hits.push(['border', e.tagName, String(e.className)]); break; }
}
return { hits, sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth };
"""


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("size", [(1440, 900), (820, 1180), (390, 844)])
def test_no_sideways_scroll_and_no_red_fills(server, drv, size, theme):
    _size(drv, *size)
    _session(drv, server.base, signed_in=False, theme=theme)
    for path in ("/quality", "/quality/standards", "/quality/standards/Diesel%20-%20AO25"):
        _open(drv, server.base, path)
        got = drv.execute_script(RED)
        assert got["sw"] <= got["cw"], (path, size, theme, got)
        assert got["hits"] == [], (path, size, theme, got["hits"])


def test_signed_out_new_standard_asks_to_sign_in_first(server, drv):
    _size(drv, 1440, 900)
    _session(drv, server.base, signed_in=False)
    _open(drv, server.base, "/quality/standards")
    drv.find_element(By.ID, "new-open").click()
    sheet = _wait(lambda: next((d for d in drv.find_elements(By.CSS_SELECTOR, "dialog[open]")), None))
    assert sheet is not None and sheet.get_attribute("id") != "new-sheet"
    assert "add a QC standard" in sheet.text.lower() or "sign in" in sheet.text.lower(), sheet.text


def test_who_reports_a_test_that_could_not_be_read_is_said(server, drv):
    class Broken:
        def reported_tests(self):
            from labcore_result import LabCoreUnavailable
            raise LabCoreUnavailable("the LEM store is locked")
    real = server.app.config.get("LOG_MIRROR")
    server.app.config["LOG_MIRROR"] = Broken()
    server.app.config["QC_REPORTING"]["pairs"] = None      # forget what T5 read
    try:
        _size(drv, 1440, 900)
        _session(drv, server.base, signed_in=True)
        _open(drv, server.base, "/quality")
        drv.find_element(By.ID, "new-open").click()
        assert _wait(lambda: drv.find_element(By.ID, "new-sheet").get_attribute("open") is not None)
        drv.find_element(By.ID, "new-test").send_keys("Flash")
        opt = _wait(lambda: next((e for e in drv.find_elements(By.CSS_SELECTOR, "#new-test-list li") if e.is_displayed()), None))
        opt.click()
        note = _wait(lambda: drv.find_element(By.ID, "new-chip-note").text)
        assert note.startswith("Couldn't read which instruments report this test") and "locked" in note, note
        names = drv.execute_script("return [...document.querySelectorAll('#new-chips button')].map(b => b.textContent)")
        assert len(names) == 13 and names == sorted(names, key=str.lower), names
    finally:
        server.app.config["LOG_MIRROR"] = real
        server.app.config["QC_REPORTING"]["pairs"] = None


# ── Trends ──────────────────────────────────────────────────────────────────
TRENDS = """
const groups = [...document.querySelectorAll('#t-groups .t-group')];
const tiles = [...document.querySelectorAll('#t-groups .t-tile')];
const cols = new Set(tiles.map(t => Math.round(t.getBoundingClientRect().left))).size;
const bad = [];
for (const t of tiles) {
  const r = t.getBoundingClientRect();
  for (const el of t.querySelectorAll('.t-check, .t-word, .t-last, .t-limits')) {
    if (el.scrollWidth > el.clientWidth + 1) bad.push(el.textContent);
  }
}
return { titles: groups.map(g => g.querySelector('.t-name').textContent),
         perGroup: groups.map(g => g.querySelectorAll('.t-tile').length),
         tiles: tiles.length, charts: document.querySelectorAll('#t-groups .t-tile svg').length, cols, bad,
         sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth };
"""


def _open_trends(d, base, q=""):
    d.get(base + "/quality/trends" + q)
    assert _wait(lambda: d.execute_script("return document.querySelectorAll('#t-groups .t-tile').length")), "no tiles"
    time.sleep(0.2)


@pytest.mark.parametrize("size", [(1440, 900), (3840, 2160), (390, 844)])
def test_trends_are_every_check_by_instrument_on_one_page(server, drv, size):
    _size(drv, *size)
    _session(drv, server.base, signed_in=False)
    _open_trends(drv, server.base)
    got = drv.execute_script(TRENDS)
    wall = drv.execute_script("return fetch('/api/ui/wall/qc').then(r => r.json())")
    names = []
    for c in wall["cards"]:
        if c["title"] not in names:
            names.append(c["title"])
    # one group per instrument, every check, no pages
    assert sorted(got["titles"]) == sorted(names), got
    assert got["tiles"] == len(wall["cards"]), got
    assert got["charts"] >= 5, got
    assert got["bad"] == [], got
    assert got["sw"] <= got["cw"], got
    # worst first: the first group holds an out-of-spec check
    outs = {c["title"] for c in wall["cards"] if c["verdict"]["key"] == "out"}
    assert got["titles"][0] in outs, got
    # the grid follows the screen, and instruments share rows (no row of one tile and a gap)
    lefts = drv.execute_script("return [...document.querySelectorAll('#t-groups .t-group')].map(g => Math.round(g.getBoundingClientRect().left))")
    if size[0] >= 1440:
        assert len(set(lefts)) > 1, lefts
    if size[0] == 390:
        assert got["cols"] == 1, got
    if size[0] == 3840:
        # a big screen with room: bigger tiles, every chart on one screen
        w = drv.execute_script("return document.querySelector('#t-groups .t-tile').getBoundingClientRect().width")
        assert w > 400, w
        assert drv.execute_script("return document.documentElement.scrollHeight <= innerHeight + 1"), "4K scrolls"
        assert got["cols"] >= 3, got


def test_trends_range_is_in_the_url_and_redraws(server, drv):
    _size(drv, 1440, 900)
    _session(drv, server.base, signed_in=False)
    _open_trends(drv, server.base, "?range=30")
    assert drv.execute_script("return document.querySelector('#t-range [aria-pressed=true]').dataset.range") == "30"
    drv.find_element("css selector", "#t-range [data-range='180']").click()
    assert _wait(lambda: "range=180" in drv.current_url)
    assert drv.execute_script("return document.querySelector('#t-range [aria-pressed=true]').dataset.range") == "180"


def test_trends_no_red_fills_and_no_sideways_scroll(server, drv):
    for theme in ("light", "dark"):
        _size(drv, 1440, 900)
        _session(drv, server.base, signed_in=False, theme=theme)
        _open_trends(drv, server.base)
        got = drv.execute_script(RED)
        assert got["sw"] <= got["cw"] and got["hits"] == [], (theme, got)
