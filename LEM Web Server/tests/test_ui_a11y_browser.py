"""Tablet, phone and accessibility, walked in headless Chrome (ia-final §6, §9; piece 15).

Every page a person or a TV opens is loaded at the sizes people actually hold
it, in both themes, and checked by what a person would hit:

* **axe-core finds nothing serious or critical** (§9.1 rule 8), in Light and
  Dark, at 1440x900 and 820x1180, signed in (the gated controls drawn live)
  and signed out (the same controls drawn locked). The walls are also checked
  after they have gone stale: a wall that has not heard from LEM for 90 s
  greys out, and the old rule (half opacity) took its words under AA, the
  moment a person most needs to read "Not live · last update 13:15".
  axe is the vendored ``tests/vendor/axe-4.10.2.min.js`` (MPL-2.0, from
  cdnjs, sha256 b511cd9d…75e3), so the run never needs the network.
* **Nothing scrolls sideways at 390 px or 320 px** (§6: "nothing scrolls
  horizontally except a .tablewrap that says so"; §9.1 rule 6: 320 CSS px is
  WCAG's reflow width, and 640 px is a 1280 px screen at 200 % zoom).
* **Targets.** At least 24 px everywhere (WCAG 2.2 AA 2.5.8). At least
  44 px on the round and on the tablet and phone layouts of the record
  (§6, §9.1 rule 7), because those are the pages used with a gloved finger
  at the bench. A text link inside a sentence is exempt, as WCAG exempts it.
* **The phone's top bar and menu** (§2.1): a 56 px bar; the menu button
  opens the full sidebar, keeps focus inside it while open (the page behind
  is inert), and Escape puts focus back on the button.
* **T1, T2 and T4b at 390x844** within one click of the desktop counts
  (§8): the only extra click a phone may cost is opening the menu.
* **The record and the round at 390 have nothing clipped or overlapping**:
  no two pieces of text drawn over each other, no word cut by a box that
  does not say so with an ellipsis.
* **Reduced motion stops the walls' rotation** and leaves dots plus
  Previous / Next (§9.1 rule 6); without it the wall does rotate, so the
  check is not passing on a wall that never moves.
* **The record's QC chart says itself in words and as a table** (§9.1
  rule 5).

A check that could not run is a failure, never a pass: axe throwing, a theme
that did not apply, or a page that did not load all fail the test that asked.

Skipped without selenium or headless Chrome (LEM's own venv has no selenium;
run with GC hub's venv). In-process app on the fake LabCore and the demo floor
on ``LEM_A11Y_PORT`` (default 5701); a busy port fails rather than skips.
Nothing here can reach a real LabCore.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path

import pytest

webdriver = pytest.importorskip("selenium.webdriver")
from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.common.keys import Keys  # noqa: E402

import demo_floor  # noqa: E402
from labcore_gateway import FakeLabCoreGateway  # noqa: E402
from web_app import create_app  # noqa: E402

PORT = int(os.environ.get("LEM_A11Y_PORT", "5701"))
PASSWORD = "a11y-pass"
AXE = Path(__file__).with_name("vendor") / "axe-4.10.2.min.js"
AXE_SHA = "b511cd9dec01c76f4b2ad1723b66b6db37d4c2eb4ed199076e1829d9ee7b75e3"
ROUND = "a1b2c3d4e5f6"

#: Every page (ia-final §1), and the states of them a person meets: the
#: missing record, the sign-in page, a round's editor, a new standard.
PAGES = ["/", "/?view=map", "/instruments/gc-1", "/instruments/pac-flash-2", "/instruments/no-such-bench",
         "/checklists/opening", "/checklists/closing", "/checklists/edit", "/checklists/edit/" + ROUND,
         "/checklists/edit/new?slot=closing", "/checklists/trends",
         "/quality", "/quality/standards", "/quality/standards/" + demo_floor.STANDARD,
         "/logs", "/settings", "/help", "/results/conflicts",
         "/floor", "/qc", "/wall?show=floor,qc&every=60"]
WALLS = ["/floor", "/qc", "/wall?show=floor,qc&every=60"]


class Auth:
    def login(self, u, p):
        return (u or "card", "tok", "") if p == PASSWORD else (None, "", "Invalid username or password.")

    def logout(self, t):
        pass


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from werkzeug.serving import make_server
    root = tmp_path_factory.mktemp("lem-a11y")
    gw = FakeLabCoreGateway()
    app = create_app(gw, secret="a11y", authenticator=Auth(), documents_root=str(root))
    app.config["SNAPSHOTS"].ensure_schema()
    demo_floor.seed(gw, documents_root=str(root))
    app.config["SNAPSHOTS"].refresh()
    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = "setup"
    # T4b's round (ia-final §8): tick, a PSI reading, tick
    r = c.post("/api/checklists", json={
        "uid": ROUND, "name": "Opening round", "slot": "opening", "due_time": "23:59",
        "items": [{"uid": "i1", "text": "Lights and fume hoods on", "entry_type": "none"},
                  {"uid": "i2", "text": "Helium cylinder pressure", "entry_type": "number", "units": "PSI"},
                  {"uid": "i3", "text": "Nitrogen generator running", "entry_type": "none"}]})
    assert r.status_code == 200, r.get_data(as_text=True)
    try:
        srv = make_server("127.0.0.1", PORT, app, threaded=True)
    except OSError as exc:
        pytest.fail(f"port {PORT} is busy (set LEM_A11Y_PORT): {exc}")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"base": f"http://127.0.0.1:{PORT}", "app": app}
    srv.shutdown()


def _chrome(extra=()):
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    for arg in ("--headless=new", "--no-sandbox", "--disable-gpu", "--force-device-scale-factor=1",
                "--window-size=1440,900", *extra):
        opts.add_argument(arg)
    try:
        d = webdriver.Chrome(options=opts)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"headless Chrome unavailable: {exc}")
    d.set_script_timeout(90)
    return d


@pytest.fixture
def drv(server):
    # one browser per test: twenty pages with axe injected into each is a
    # lot for one renderer, and one test's crash must not fail the rest
    d = _chrome()
    yield d
    d.quit()


def _size(d, w, h):
    d.execute_cdp_cmd("Emulation.setDeviceMetricsOverride",
                      {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": w < 700})


def _media(d, **features):
    d.execute_cdp_cmd("Emulation.setEmulatedMedia",
                      {"features": [{"name": k.replace("_", "-"), "value": v} for k, v in features.items()]})


def _wait(pred, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if pred():
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.1)
    return bool(pred())


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


def _theme(d, base, theme):
    d.get(base + "/help")
    d.execute_script("localStorage.setItem('lem.theme', arguments[0]);", theme)


def _open(d, url, settle=0.9):
    d.get(url)
    assert _wait(lambda: d.execute_script("return document.readyState") == "complete"), url
    # the page's own first fetches (the live feed, the record's reads)
    time.sleep(settle)


# ── axe ─────────────────────────────────────────────────────────────────────
AXE_RUN = """
const done = arguments[arguments.length - 1];
if (!window.axe) { done('axe is not on the page'); return; }
axe.run(document, {resultTypes: ['violations']}).then(r => done(r.violations
    .filter(v => v.impact === 'serious' || v.impact === 'critical')
    .map(v => v.id + ' (' + v.impact + ', ' + v.nodes.length + '): ' + v.nodes.slice(0, 3).map(n =>
        n.target.join(' ') + ' :: ' + String(n.failureSummary || '').split('\\n').slice(1, 2).join(' ').trim()
    ).join(' | ')))).catch(e => done('axe failed: ' + e));
"""


def _axe(d):
    src = AXE.read_text(encoding="utf-8")
    d.execute_script(src)
    got = d.execute_async_script(AXE_RUN)
    # a failed run is not an empty result
    assert isinstance(got, list), got
    return got


def test_the_vendored_axe_is_the_one_named():
    assert hashlib.sha256(AXE.read_bytes()).hexdigest() == AXE_SHA


def test_axe_catches_what_it_should(drv, server):
    """The control: a page with a 2:1 grey word and an unlabelled button must
    be reported, or "0 findings" below would mean nothing."""
    _open(drv, server["base"] + "/help", 0.3)
    drv.execute_script("""
      const p = document.createElement('p'); p.textContent = 'faint words';
      p.style.cssText = 'color:#bbb;background:#fff'; document.getElementById('main').append(p);
      const b = document.createElement('button'); document.getElementById('main').append(b);""")
    got = " ".join(_axe(drv))
    assert "color-contrast" in got and "button-name" in got, got


COMBOS = [(t, w, h, who) for t in ("light", "dark") for (w, h) in ((1440, 900), (820, 1180))
          for who in ("Cody", "")]


@pytest.mark.parametrize("theme,w,h,who", COMBOS,
                         ids=[f"{t}-{w}-{'in' if who else 'out'}" for t, w, h, who in COMBOS])
def test_axe_finds_nothing_serious_on_any_page(drv, server, theme, w, h, who):
    _sign(drv, server["base"], who)
    _theme(drv, server["base"], theme)
    _size(drv, w, h)
    bad = {}
    for path in PAGES:
        _open(drv, server["base"] + path)
        assert drv.execute_script("return document.documentElement.getAttribute('data-theme')") == theme, path
        got = _axe(drv)
        if got:
            bad[path] = got
    assert bad == {}, "\n".join(f"{p}:\n  " + "\n  ".join(v) for p, v in bad.items())


#: Installed before any page script: the page's clock runs K times fast, so
#: the walls' 90 s stale rule and their rotations pass in a few real seconds.
FAST_CLOCK = """
(() => {
  const K = %d;
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
"""


@pytest.fixture
def fast(server):
    d = _chrome()
    d.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": FAST_CLOCK % 40})
    yield d
    d.quit()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_a_stale_wall_is_still_readable(fast, server, theme):
    """The live feed stops answering (every /api/ui/ call fails) and 90 page
    seconds pass: the wall says "Not live", greys out, and every word on it
    still passes AA."""
    d = fast
    _theme(d, server["base"], theme)
    bad = {}
    for (w, h) in ((1440, 900), (820, 1180)):
        _size(d, w, h)
        for path in WALLS:
            _open(d, server["base"] + path, 0.5)
            d.execute_script("""
              const F = window.fetch;
              window.fetch = (u, o) => String(u).includes('/api/ui/') ? Promise.reject(new TypeError('offline')) : F(u, o);""")
            assert _wait(lambda: d.execute_script(
                "return !!document.querySelector('.wall-view.is-stale:not([hidden])')"), 15), (path, "never went stale")
            got = _axe(d)
            if got:
                bad[f"{w} {path}"] = got
    assert bad == {}, bad


# ── reflow ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("w,h", [(390, 844), (320, 640), (640, 450)], ids=["phone-390", "reflow-320", "zoom-200"])
def test_nothing_scrolls_sideways(drv, server, w, h):
    _sign(drv, server["base"], "Cody")
    _theme(drv, server["base"], "light")
    _size(drv, w, h)
    bad = {}
    for path in PAGES:
        _open(drv, server["base"] + path, 0.7)
        sw, cw, who = drv.execute_script("""
          const W = document.documentElement.clientWidth;
          const out = [];
          for (const e of document.querySelectorAll('body *')) {
            const r = e.getBoundingClientRect();
            if (r.width && r.right > W + 1 && getComputedStyle(e).position !== 'fixed') {
              let cut = false;
              for (let p = e.parentElement; p; p = p.parentElement) {
                if (/(hidden|auto|scroll|clip)/.test(getComputedStyle(p).overflowX)) { cut = p.getBoundingClientRect().right <= W + 1; break; }
              }
              if (!cut) out.push((e.id ? '#' + e.id : e.tagName.toLowerCase() + '.' + String(e.className.baseVal ?? e.className).trim().replace(/\\s+/g, '.')) + ' ' + Math.round(r.right));
            }
          }
          return [document.documentElement.scrollWidth, W, out.slice(0, 4)];""")
        if sw > cw:
            bad[path] = f"{sw} > {cw}: {who}"
    assert bad == {}, bad


# ── targets ─────────────────────────────────────────────────────────────────
TARGETS_JS = """
const MIN = arguments[0], scope = arguments[1] ? document.querySelector(arguments[1]) : document;
const small = [];
for (const e of scope.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=radio], [role=tab]')) {
  let r = e.getBoundingClientRect();
  // a visually hidden box inside its label (the switch): the label is what a finger hits
  const lab = e.tagName === 'INPUT' && e.closest('label');
  if (lab && (r.width < 2 || r.height < 2)) r = lab.getBoundingClientRect();
  if (!r.width || !r.height) continue;
  const cs = getComputedStyle(e);
  if (cs.visibility === 'hidden' || e.closest('[inert], [hidden], .visually-hidden, .skip-link')) continue;
  // WCAG 2.5.8's inline exception: a link inside a sentence
  if (e.tagName === 'A' && cs.display === 'inline' && e.parentElement && /^(P|LI|SPAN|TD|DD)$/.test(e.parentElement.tagName)
      && e.parentElement.textContent.trim().length > e.textContent.trim().length + 8) continue;
  if (r.width < MIN || r.height < MIN)
    small.push((e.id ? '#' + e.id : e.tagName.toLowerCase() + '.' + String(e.className).trim().replace(/\\s+/g, '.'))
               + ' "' + (e.innerText || e.getAttribute('aria-label') || '').trim().slice(0, 24) + '" '
               + Math.round(r.width) + 'x' + Math.round(r.height));
}
return small;
"""


@pytest.mark.parametrize("w,h", [(1440, 900), (390, 844)])
def test_every_target_is_at_least_24px(drv, server, w, h):
    _sign(drv, server["base"], "Cody")
    _size(drv, w, h)
    bad = {}
    for path in PAGES:
        _open(drv, server["base"] + path, 0.7)
        got = drv.execute_script(TARGETS_JS, 24, None)
        if got:
            bad[path] = got[:8] + ([f"... {len(got)} in all"] if len(got) > 8 else [])
    assert bad == {}, bad


@pytest.mark.parametrize("w,h", [(820, 1180), (390, 844)])
@pytest.mark.parametrize("path", ["/checklists/opening", "/instruments/gc-1"])
def test_the_round_and_the_record_take_a_finger(drv, server, path, w, h):
    _sign(drv, server["base"], "Cody")
    _size(drv, w, h)
    _open(drv, server["base"] + path, 1.2)
    got = drv.execute_script(TARGETS_JS, 44, None)
    assert got == [], got


# ── the phone's top bar and menu ──────────────────────────────────────────────
def test_the_phone_bar_and_its_menu(drv, server):
    d = drv
    _sign(d, server["base"], "Cody")
    _size(d, 390, 844)
    _open(d, server["base"] + "/instruments/gc-1")
    bar = d.execute_script("const r = document.querySelector('.topbar').getBoundingClientRect(); return [r.top, r.height];")
    assert bar == [0, 56], bar
    # the sidebar is off screen and out of the tab order until asked for
    assert d.execute_script("return getComputedStyle(document.getElementById('sidebar')).visibility") == "hidden"
    btn = d.find_element(By.ID, "drawer-open")
    assert btn.get_attribute("aria-expanded") == "false"
    btn.send_keys(Keys.ENTER)
    assert _wait(lambda: d.execute_script(
        "return getComputedStyle(document.getElementById('sidebar')).visibility") == "visible")
    assert btn.get_attribute("aria-expanded") == "true"
    assert d.execute_script("return document.activeElement.closest('#sidebar') !== null"), "focus did not go into the menu"
    # the page behind is out of reach while the menu is open
    assert d.execute_script("return document.querySelector('.main-col').inert === true")
    for _ in range(30):
        d.switch_to.active_element.send_keys(Keys.TAB)
        assert d.execute_script("return !!document.activeElement.closest('#sidebar, .drawer-catch')"), \
            d.execute_script("return document.activeElement.outerHTML.slice(0, 120)")
    d.switch_to.active_element.send_keys(Keys.ESCAPE)
    assert _wait(lambda: d.execute_script("return !document.documentElement.hasAttribute('data-drawer')"))
    assert d.execute_script("return document.activeElement.id") == "drawer-open"
    assert d.execute_script("return document.querySelector('.main-col').inert") is False
    # every nav item in the menu still says its count in words
    labels = d.execute_script("return [...document.querySelectorAll('#sidebar .nav-item')].map(a => a.getAttribute('aria-label'))")
    assert labels[0].startswith("Instruments") and all(labels), labels


# ── T1, T2, T4b at 390 (§8: within +1 click of the desktop targets) ─────────
def _visible_text(d, sel):
    return d.execute_script("""const e = document.querySelector(arguments[0]); if (!e) return '';
      const r = e.getBoundingClientRect(); return r.width && r.right <= document.documentElement.clientWidth + 1 ? e.innerText : '';""", sel)


def test_t1_at_390_the_verdict_at_0_clicks_and_the_full_answer_at_1(drv, server):
    d = drv
    _sign(d, server["base"], "Cody")
    _size(d, 390, 844)
    _open(d, server["base"] + "/")
    row = 'tr.irow:has(a[href="/instruments/gc-2"])'
    assert _wait(lambda: _visible_text(d, row))
    text = _visible_text(d, row)
    # the verdict word and its reason are on the row, on screen, at 0 clicks
    assert "GC-2" in text and ("OK to run" in text or "Not OK" in text), text
    clicks = 0
    link = d.find_element(By.CSS_SELECTOR, row + ' a[href="/instruments/gc-2"]')
    d.execute_script("arguments[0].scrollIntoView({block: 'center'})", link)   # a scroll, not a click
    link.click()
    clicks += 1
    assert _wait(lambda: d.current_url.endswith("/instruments/gc-2"))
    assert _wait(lambda: _visible_text(d, "#readiness h2"))
    assert clicks == 1          # desktop: 1


def test_t2_at_390_latest_qc_verdict_and_chart_in_one_click(drv, server):
    d = drv
    _sign(d, server["base"], "Cody")
    _size(d, 390, 844)
    _open(d, server["base"] + "/")
    row = 'tr.irow:has(a[href="/instruments/pac-flash-2"])'
    assert _wait(lambda: _visible_text(d, row))
    link = d.find_element(By.CSS_SELECTOR, row + ' a[href="/instruments/pac-flash-2"]')
    d.execute_script("arguments[0].scrollIntoView({block: 'center'})", link)   # a scroll, not a click
    link.click()
    clicks = 1                  # desktop: 1
    assert _wait(lambda: d.execute_script("return !!document.querySelector('#chart-plot svg.qchart')"))
    # QC is the first section after the readiness card, its verdict in words
    first = d.execute_script("return document.querySelector('main section.sec').id")
    assert first == "qc", first
    verdict = _visible_text(d, "#qc-rows tr")
    assert verdict and "In spec" in verdict, verdict
    # the chart says itself in words, and ends on the newest run
    cap = d.find_element(By.ID, "chart-cap").text
    assert "outside the limits" in cap and " · last " in cap, cap
    assert clicks <= 2


def test_t4b_at_390_from_any_page_is_five_clicks_at_most(drv, server):
    """Desktop: nav Checklists (1) → tick (1) → tap PSI (1), type, Enter →
    tick (1) = 4. A phone adds the menu: 5, inside +1."""
    d = drv
    app = server["app"]
    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = "setup"
    for i in ("i1", "i2", "i3"):
        c.post(f"/api/checklists/{ROUND}/toggle", json={"item_uid": i, "checked": False})
    _sign(d, server["base"], "Cody")
    _size(d, 390, 844)
    _open(d, server["base"] + "/settings")
    clicks = 0
    d.find_element(By.ID, "drawer-open").click()
    clicks += 1
    assert _wait(lambda: d.find_element(By.CSS_SELECTOR, '#sidebar .nav-item[data-nav="checklists"]').is_displayed())
    d.find_element(By.CSS_SELECTOR, '#sidebar .nav-item[data-nav="checklists"]').click()
    clicks += 1
    rows = lambda: d.find_elements(By.CSS_SELECTOR, "#lists .item[data-item]")  # noqa: E731
    assert _wait(lambda: len(rows()) == 3)
    rows()[0].click()
    clicks += 1
    rows()[1].click()
    clicks += 1
    inp = rows()[1].find_element(By.CSS_SELECTOR, ".rinput")
    assert d.execute_script("return document.activeElement === arguments[0]", inp)
    inp.send_keys("2900")
    inp.send_keys(Keys.ENTER)
    rows()[2].click()
    clicks += 1
    assert clicks == 5
    assert _wait(lambda: d.find_element(By.ID, "round-pill-text").text.startswith("Done · 3 of 3"))
    names = [r.find_element(By.CSS_SELECTOR, ".rby").text for r in rows()]
    assert all(n.startswith("Cody · ") for n in names), names


# ── nothing clipped or overlapping at 390 ───────────────────────────────────
OVERLAP_JS = """
const W = document.documentElement.clientWidth;
const leaves = [];
const walk = document.createTreeWalker(document.querySelector('main') || document.body, NodeFilter.SHOW_TEXT);
const seen = new Set();
while (walk.nextNode()) {
  const t = walk.currentNode; if (!t.textContent.trim()) continue;
  const el = t.parentElement; if (!el || seen.has(el)) continue; seen.add(el);
  const cs = getComputedStyle(el);
  if (cs.visibility === 'hidden' || el.closest('[hidden], .visually-hidden, svg, dialog:not([open])')) continue;
  const range = document.createRange(); range.selectNodeContents(t);
  for (const r of range.getClientRects()) if (r.width > 1 && r.height > 1) leaves.push({el, r, s: t.textContent.trim().slice(0, 30)});
}
const bad = [];
for (let i = 0; i < leaves.length; i++) {
  const a = leaves[i];
  if (a.r.right > W + 1 || a.r.left < -1) bad.push('off screen: "' + a.s + '"');
  // clipped: drawn past an ancestor that hides overflow, without an ellipsis saying so
  for (let p = a.el; p && p !== document.body; p = p.parentElement) {
    const ps = getComputedStyle(p);
    if (/(hidden|clip)/.test(ps.overflowX + ps.overflowY)) {
      const pr = p.getBoundingClientRect();
      const cut = a.r.right > pr.right + 1 || a.r.bottom > pr.bottom + 2 || a.r.left < pr.left - 1 || a.r.top < pr.top - 2;
      if (cut && ps.textOverflow !== 'ellipsis' && !(ps.webkitLineClamp && ps.webkitLineClamp !== 'none')
          && pr.width > 0 && !p.matches('.tablewrap, .chart-table'))
        bad.push('clipped: "' + a.s + '" by ' + (p.id || p.className));
      break;
    }
  }
  for (let j = i + 1; j < leaves.length; j++) {
    const b = leaves[j];
    if (a.el === b.el || a.el.contains(b.el) || b.el.contains(a.el)) continue;
    const x = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left);
    const y = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top);
    if (x > 2 && y > 2) bad.push('overlap: "' + a.s + '" / "' + b.s + '"');
  }
}
return [...new Set(bad)].slice(0, 12);
"""


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("path", ["/instruments/gc-1", "/instruments/pac-flash-2", "/checklists/opening"])
def test_the_record_and_the_round_at_390_have_nothing_clipped_or_overlapping(drv, server, path, theme):
    _sign(drv, server["base"], "Cody")
    _theme(drv, server["base"], theme)
    _size(drv, 390, 844)
    _open(drv, server["base"] + path, 1.5)
    assert drv.execute_script(OVERLAP_JS) == []


# ── reduced motion ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("reduce", [True, False], ids=["reduced", "control"])
def test_reduced_motion_stops_the_qc_wall_and_leaves_dots_and_steps(fast, server, reduce):
    d = fast
    _size(d, 1440, 900)
    _media(d, prefers_reduced_motion="reduce" if reduce else "no-preference")
    # small enough that the QC cards take more than one page
    _size(d, 820, 600)
    _open(d, server["base"] + "/qc", 0.3)
    pages = d.execute_script("return document.querySelectorAll('#wq-pages i').length")
    assert pages >= 2, "the QC wall has one page here; nothing to rotate"
    on = "return [...document.querySelectorAll('#wq-pages i')].findIndex(i => i.classList.contains('on'))"
    first = d.execute_script(on)
    seen = set()
    end = time.time() + 2.0     # 80 page-seconds: five 15 s turns
    while time.time() < end:
        seen.add(d.execute_script(on))
        time.sleep(0.05)
    if not reduce:
        assert len(seen) > 1, "the control wall never turned a page, so a still wall proves nothing"
        assert "reduced motion" not in d.find_element(By.ID, "wq-rot").text
        return
    now = d.execute_script(on)
    assert seen == {first} and now == first, "the wall rotated with reduced motion on"
    assert d.find_element(By.ID, "wq-step").is_displayed(), "no Previous / Next while rotation is off"
    assert "reduced motion" in d.find_element(By.ID, "wq-rot").text
    d.find_element(By.ID, "wq-next").click()
    assert _wait(lambda: d.execute_script(
        "return [...document.querySelectorAll('#wq-pages i')].findIndex(i => i.classList.contains('on'))") != first)
