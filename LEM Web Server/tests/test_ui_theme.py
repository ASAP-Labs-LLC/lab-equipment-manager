"""System / Light / Dark, the rail and the phone bar, in a real browser.

Ported from GC hub's ``tests/test_ui_theme.py`` (headless Chrome, the OS
scheme emulated with CDP ``Emulation.setEmulatedMedia``) and extended with the
two promises of ia-final §2.1 that only a browser can check:

* **Theme.** Nobody has chosen: System, which follows the OS and switches
  live when the OS does — no reload, and the theme is on <html> before <body>
  exists, so a dark-mode tablet never flashes white. Picking Light or Dark
  stops following the OS; System follows again. The choice is kept in
  ``lem.theme``. Every visible text node passes AA in both themes.
* **Rail mode keeps its words.** At 820x1180 (an iPad, portrait) the sidebar
  is a 64px rail; GC's rail leaves unlabelled dots there, which every judge
  flagged. LEM shows no dot without a word: the record's age and the benches
  are said in the ``.rail-status`` strip, a count sits on the rail icon, and
  the strip never runs under the version stamp.
* **The phone.** Below 700px the top bar is 56px with the menu and the mark,
  and the menu slides the full sidebar in.

Skipped when selenium or headless Chrome is not available (LEM's own venv has
no selenium; run this file with a Python that does, for example
``/Users/rynatical/Projects/gc-hub/.venv/bin/python -m pytest tests/test_ui_theme.py``).
The app runs in-process against the fake LabCore with the demo floor, on
``LEM_UI_TEST_PORT`` (default 5700); nothing here can reach a real LabCore.
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

PORT = int(os.environ.get("LEM_UI_TEST_PORT", "5700"))

AT_BODY = """
window.__themeAtBody = 'no body yet';
new MutationObserver((_m, o) => {
  if (document.body) { window.__themeAtBody = document.documentElement.getAttribute('data-theme'); o.disconnect(); }
}).observe(document, {childList: true, subtree: true});
"""

# GC hub's rendered-page contrast check (test_ui_setup_pages_smoke.CONTRAST_JS).
CONTRAST_JS = r"""
function parse(c){const m=c.match(/rgba?\(([^)]+)\)/);if(!m)return null;const p=m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);return [p[0],p[1],p[2],p.length>3?p[3]:1];}
function over(t,b){const a=t[3];return [t[0]*a+b[0]*(1-a),t[1]*a+b[1]*(1-a),t[2]*a+b[2]*(1-a),1];}
function lum(c){const f=v=>{v/=255;return v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4)};return 0.2126*f(c[0])+0.7152*f(c[1])+0.0722*f(c[2]);}
function ratio(a,b){const x=lum(a),y=lum(b);return (Math.max(x,y)+0.05)/(Math.min(x,y)+0.05);}
function bgOf(el){const st=[];let e=el;while(e&&e.nodeType===1){const b=parse(getComputedStyle(e).backgroundColor);if(b&&b[3]>0)st.push(b);if(b&&b[3]>=1)break;e=e.parentElement;}
 let base=parse(getComputedStyle(document.body).backgroundColor)||[255,255,255,1];if(base[3]<1)base=[255,255,255,1];
 for(let i=st.length-1;i>=0;i--)base=over(st[i],base);return base;}
const out=[];const seen=new Set();const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
while(w.nextNode()){const t=w.currentNode;if(!t.textContent.trim())continue;const el=t.parentElement;if(!el||seen.has(el))continue;seen.add(el);
 const r=el.getBoundingClientRect();if(!r.width||!r.height)continue;const cs=getComputedStyle(el);if(cs.visibility==='hidden'||cs.display==='none'||el.closest('[hidden]'))continue;
 let op=1;let e=el;while(e&&e.nodeType===1){op*=parseFloat(getComputedStyle(e).opacity);e=e.parentElement;}
 const fg=parse(cs.color);const bg=bgOf(el);const cr=ratio(over([fg[0],fg[1],fg[2],fg[3]*op],bg),bg);
 const size=parseFloat(cs.fontSize);const large=size>=24||(size>=18.66&&parseInt(cs.fontWeight)>=700);
 if(cr<(large?3:4.5)&&!el.closest('[disabled]'))out.push([t.textContent.trim().slice(0,40),cs.color,op.toFixed(2),cr.toFixed(2)]);}
return out;
"""


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    from werkzeug.serving import make_server
    root = tmp_path_factory.mktemp("lem-ui")
    gw = FakeLabCoreGateway()
    app = create_app(gw, secret="ui-test", admin_password="ui-pass", documents_root=str(root))
    app.config["SNAPSHOTS"].ensure_schema()
    demo_floor.seed(gw, documents_root=str(root))
    app.config["SNAPSHOTS"].refresh()
    try:
        server = make_server("127.0.0.1", PORT, app, threaded=True)
    except OSError as exc:
        pytest.skip(f"port {PORT} is busy: {exc}")
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{PORT}"
    server.shutdown()


@pytest.fixture
def drv(base):
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    for arg in ("--headless=new", "--no-sandbox", "--disable-gpu", "--force-device-scale-factor=1",
                "--window-size=1440,1000"):
        opts.add_argument(arg)
    opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    try:
        d = webdriver.Chrome(options=opts)
    except Exception as exc:  # noqa: BLE001 - no Chrome / driver here
        pytest.skip(f"headless Chrome unavailable: {exc}")
    d.get(base + "/healthz")
    d.execute_script("localStorage.clear();")
    yield d
    d.quit()


def _js(d, script, *args):
    return d.execute_script(script, *args)


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if pred():
                return True
        except Exception:  # noqa: BLE001 - DOM not ready yet
            pass
        time.sleep(0.1)
    return bool(pred())


def _size(d, w, h, mobile=False):
    d.execute_cdp_cmd("Emulation.setDeviceMetricsOverride",
                      {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": mobile})


def _os(d, scheme):
    d.execute_cdp_cmd("Emulation.setEmulatedMedia",
                      {"features": [{"name": "prefers-color-scheme", "value": scheme}]})


def _load(d, url):
    d.get(url)
    assert _wait(lambda: _js(d, "return document.readyState;") == "complete")


def _theme(d):
    return _js(d, "return document.documentElement.dataset.theme;")


def _bg(d):
    return _js(d, "return getComputedStyle(document.body).backgroundColor;")


def _pick(d, choice):
    _js(d, "document.getElementById('user-chip').click();")
    _js(d, f"document.querySelector('#user-menu [data-theme-choice={choice}]').click();")
    _js(d, "document.body.click();")


def _checked(d):
    return _js(d, "return Array.from(document.querySelectorAll('#user-menu [data-theme-choice]'))"
                  ".filter(b => b.getAttribute('aria-checked') === 'true').map(b => b.dataset.themeChoice);")


def _errors(d):
    try:
        logs = d.get_log("browser")
    except Exception:  # noqa: BLE001
        return []
    return [e["message"] for e in logs if e["level"] == "SEVERE" and "favicon" not in e["message"]]


def _sign_in(d, base):
    assert _js(d, "return fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'},"
                  " body: JSON.stringify({username: 'Kaden Ortiz', password: 'ui-pass'})}).then(r => r.status);") == 200


def test_system_follows_the_os_live_and_never_flashes(drv, base):
    d = drv
    _sign_in(d, base)
    _size(d, 1440, 900)
    d.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": AT_BODY})
    _os(d, "dark")
    _load(d, base + "/settings")
    assert _theme(d) == "dark"
    assert _js(d, "return window.__themeAtBody;") == "dark", "the first paint was not dark"
    assert _bg(d) != "rgb(255, 255, 255)"
    labels = _js(d, "return Array.from(document.querySelectorAll('#user-menu [data-theme-choice]'))"
                    ".map(b => b.textContent.trim());")
    assert labels == ["System", "Light", "Dark"]
    assert _checked(d) == ["system"]
    assert _js(d, "return localStorage.getItem('lem.theme');") is None

    # the OS switches: the page follows at once, without a reload
    _js(d, "window.__same = true;")
    _os(d, "light")
    assert _wait(lambda: _theme(d) == "light")
    assert _bg(d) == "rgb(255, 255, 255)"
    assert _js(d, "return window.__same === true;"), "the page reloaded"
    assert _js(d, CONTRAST_JS) == []
    _os(d, "dark")
    assert _wait(lambda: _theme(d) == "dark")
    assert _js(d, "return window.__same === true;")
    assert _js(d, CONTRAST_JS) == []

    # Light stops following the OS
    _pick(d, "light")
    assert _theme(d) == "light"
    assert _js(d, "return localStorage.getItem('lem.theme');") == "light"
    _os(d, "light")
    _os(d, "dark")
    assert _theme(d) == "light"
    assert _checked(d) == ["light"]
    # ...and the Settings control shows the same choice: one control, two places
    assert _js(d, "return document.querySelector('#theme-choice [aria-checked=true]').dataset.choice;") == "light"

    # Dark, across a page load, painted dark before <body>
    _pick(d, "dark")
    _os(d, "light")
    _load(d, base + "/help")
    assert _theme(d) == "dark" and _js(d, "return window.__themeAtBody;") == "dark"
    assert _js(d, CONTRAST_JS) == []

    # System follows again
    _pick(d, "system")
    assert _theme(d) == "light"
    _os(d, "dark")
    assert _wait(lambda: _theme(d) == "dark")
    assert _js(d, "return localStorage.getItem('lem.theme');") == "system"
    assert _errors(d) == []


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_at_820_the_status_is_in_words(drv, base, scheme):
    d = drv
    _sign_in(d, base)
    _size(d, 820, 1180)
    _os(d, scheme)
    _load(d, base + "/settings")
    assert _js(d, "return document.getElementById('sidebar').getBoundingClientRect().width;") <= 72
    assert _js(d, "return document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1;")

    # no unlabelled dot: every visible status glyph in the sidebar is gone
    dots = _js(d, "return Array.from(document.querySelectorAll('#sidebar .live-dot, #sidebar .fleet-glyph'))"
                  ".filter(e => e.getBoundingClientRect().width > 0 && getComputedStyle(e).display !== 'none').length;")
    assert dots == 0
    # ...and the words are in the strip, next to a dot that has its word
    strip = _js(d, "const s = document.getElementById('rail-status'); const r = s.getBoundingClientRect();"
                   "return {shown: getComputedStyle(s).display !== 'none', text: s.innerText.replace(/\\s+/g, ' ').trim(),"
                   " bottom: r.bottom, h: r.height, vh: innerHeight};")
    # 36px in desktop rail mode; 44px on the tablet, where its two links are
    # finger targets (piece 15, §9.1 rule 7)
    assert strip["shown"] and strip["h"] == 44
    # ia-final 2.1 gives the strip's words: "Live · updated 4 s ago · 15 of 17
    # benches checking in". Piece 1 drew static placeholders ("Updated ...");
    # the live feed (piece 2) now writes the spec's sentence, so pin that.
    assert strip["text"].startswith("Live · updated "), strip["text"]
    assert " benches checking in" in strip["text"], strip["text"]
    assert abs(strip["bottom"] - strip["vh"]) <= 1, strip
    # the strip's words end before the version stamp starts
    gap = _js(d, "const t = document.getElementById('rs-fleet').getBoundingClientRect();"
                 "const v = document.getElementById('app-version').getBoundingClientRect();"
                 "return v.left - t.right;")
    assert gap > 0, gap

    # the QC count rides on the rail icon, and its words stay in the accessible name
    badge = _js(d, "const b = document.querySelector('.nav-item[data-nav=qc] .rail-badge');"
                   "return {shown: getComputedStyle(b).display !== 'none', n: b.textContent.trim(),"
                   " label: b.closest('a').getAttribute('aria-label')};")
    assert badge["shown"] and badge["n"].isdigit() and int(badge["n"]) > 0
    # "checks", said (test_ui_shell_pages: round 2's critic read "QC 3 out
    # of spec" as three standards)
    assert badge["label"] == "QC, %s %s out of spec" % (badge["n"], "check" if badge["n"] == "1" else "checks")
    # an item with nothing to count shows no badge at all (never "0")
    assert _js(d, "return getComputedStyle(document.querySelector('.nav-item[data-nav=log] .rail-badge')).display;") == "none"

    assert _js(d, "return document.getElementById('app-version').textContent;") == \
        _js(d, "return fetch('/healthz').then(r => r.json()).then(j => j.version);")
    assert _js(d, CONTRAST_JS) == []
    assert _errors(d) == []


def test_the_phone_has_a_56px_bar_and_a_drawer(drv, base):
    d = drv
    _sign_in(d, base)
    _size(d, 390, 844, mobile=True)
    _os(d, "light")
    _load(d, base + "/settings")
    bar = _js(d, "return document.querySelector('.topbar').getBoundingClientRect().height;")
    assert bar == 56
    assert _js(d, "return document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1;")
    # the sidebar is out of the way until asked for
    assert _js(d, "return document.getElementById('sidebar').getBoundingClientRect().right;") <= 0
    _js(d, "document.getElementById('drawer-open').click();")
    assert _wait(lambda: _js(d, "return document.getElementById('sidebar').getBoundingClientRect().left;") == 0)
    names = _js(d, "return Array.from(document.querySelectorAll('#sidebar .nav-item .sb-label'))"
                   ".filter(e => getComputedStyle(e).display !== 'none').map(e => e.textContent.trim());")
    assert names == ["Instruments", "Checklists", "QC", "Log", "Settings"]
    _js(d, "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}));")
    assert _wait(lambda: _js(d, "return document.getElementById('sidebar').getBoundingClientRect().right;") <= 0)
    assert _js(d, "return getComputedStyle(document.getElementById('rail-status')).display;") == "flex"
    assert _errors(d) == []


def test_the_sidebar_choice_overrides_the_width(drv, base):
    d = drv
    _size(d, 1440, 900)
    _load(d, base + "/settings")
    assert _js(d, "return document.getElementById('sidebar').getBoundingClientRect().width;") >= 250
    _js(d, "document.querySelector('#sidebar-choice [data-choice=rail]').click();")
    assert _wait(lambda: _js(d, "return document.getElementById('sidebar').getBoundingClientRect().width;") <= 72)
    assert _js(d, "return localStorage.getItem('lem.sidebar');") == "rail"
    assert _js(d, "return getComputedStyle(document.getElementById('rail-status')).display;") == "flex"
    _load(d, base + "/help")
    assert _js(d, "return document.getElementById('sidebar').getBoundingClientRect().width;") <= 72
    _js(d, "localStorage.removeItem('lem.sidebar');")
    _load(d, base + "/settings")
    assert _js(d, "return document.querySelector('#sidebar-choice [aria-checked=true]').dataset.choice;") == "auto"
