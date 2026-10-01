"""Sign in where you are, walked in a real browser (ia-final §8 T0, piece 3).

The counting rule is the spec's (§8): a click is any activation, typed is one
per field typed into, and a screen is a view you re-orient in (a page load or
a sheet). The baseline was 4 clicks / 2 typed / +2 screens, because Sign in on
every page but the map sent you to /floor with no dialog open and never
brought you back; a gated button signed out only printed "Sign in to …".

What each walk proves, and why it matters at the bench:

* **T0 from /checklists: 2 clicks / 2 typed / +1 screen.** The same document
  (no navigation: a marker on ``window`` survives), the same URL and the same
  scroll. A tablet mid-round scrolled to item 9 must still be on item 9.
* **Gated tick: +1 / +2 / +1, and the tick lands.** Signed out, the first tick
  opens "Sign in to tick"; after sign-in the tick is applied, by that person,
  on the server, without a second tap. Otherwise people tap twice and untick.
* **Gated Mark done: +1 / +2 / +1, and the Mark done sheet opens by itself.**
* **Cancel drops the pending act.** Signing in later from the header does not
  fire a tick somebody walked away from.
* **A wrong password keeps the sheet and the pending act.** Retyping is the
  fix; losing the tick because of a typo is not.
* **Switch person** asks in place; Cancel leaves the last person signed in.
* **Any ``data-gated`` control on a shell page** is caught before its own
  handler, and its handler runs exactly once after sign-in.
* The sheet passes AA in both themes on a shell page.

Skipped when selenium or headless Chrome is not available (run with
``/Users/rynatical/Projects/gc-hub/.venv/bin/python -m pytest tests/test_ui_signin.py``).
In-process app on the fake LabCore with the demo floor, on ``LEM_UI_TEST_PORT``
(default 5702). Nothing here can reach a real LabCore.
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

from test_ui_theme import CONTRAST_JS  # noqa: E402

PORT = int(os.environ.get("LEM_UI_TEST_PORT", "5702"))
PASSWORD = "ui-pass"


class Auth:
    """LabCore's login, faked: any name with the right password; 'down' is
    LabCore not answering. Tokens are unique per login, as LabCore's are."""
    def __init__(self):
        self.n = 0
        self.logged_out = []

    def login(self, u, p):
        if p == PASSWORD:
            self.n += 1
            return (u or "card", "t%d" % self.n, "")
        if p == "down":
            return (None, "", "Connection error: timed out")
        return (None, "", "Invalid username or password.")

    def logout(self, t):
        self.logged_out.append(t)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from werkzeug.serving import make_server
    root = tmp_path_factory.mktemp("lem-signin")
    gw = FakeLabCoreGateway()
    app = create_app(gw, secret="ui-test", authenticator=Auth(), documents_root=str(root))
    app.config["SNAPSHOTS"].ensure_schema()
    demo_floor.seed(gw, documents_root=str(root))
    app.config["SNAPSHOTS"].refresh()
    try:
        srv = make_server("127.0.0.1", PORT, app, threaded=True)
    except OSError as exc:
        pytest.skip(f"port {PORT} is busy: {exc}")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"base": f"http://127.0.0.1:{PORT}", "app": app}
    srv.shutdown()


@pytest.fixture
def base(server):
    return server["base"]


@pytest.fixture
def round_uid(server):
    """A fresh Opening round of 14 ticks (tall enough to scroll), made through
    the API as a signed-in person would."""
    c = server["app"].test_client()
    with c.session_transaction() as s:
        s["user"] = "setup"
    # one Opening round at a time: the page shows every round in the slot
    for cl in c.get("/api/checklists").get_json().get("checklists", []):
        c.delete(f"/api/checklists/{cl['uid']}")
    r = c.post("/api/checklists", json={
        "name": "Opening round", "slot": "opening", "due_time": "09:30",
        "items": [{"text": f"Item {i:02d}"} for i in range(1, 15)]})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["checklist"]


def server_state(server, uid):
    b = server["app"].test_client().get("/api/checklists").get_json()
    return (b.get("state") or {}).get(uid) or {}


@pytest.fixture
def drv(base):
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    for arg in ("--headless=new", "--no-sandbox", "--disable-gpu", "--force-device-scale-factor=1",
                "--window-size=1440,900"):
        opts.add_argument(arg)
    opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    try:
        d = webdriver.Chrome(options=opts)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"headless Chrome unavailable: {exc}")
    d.get(base + "/healthz")
    d.execute_script("localStorage.clear();")
    yield d
    d.quit()


def _js(d, s, *a):
    return d.execute_script(s, *a)


def _wait(pred, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if pred():
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.1)
    return bool(pred())


def _size(d, w, h):
    d.execute_cdp_cmd("Emulation.setDeviceMetricsOverride",
                      {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})


def _os(d, scheme):
    d.execute_cdp_cmd("Emulation.setEmulatedMedia",
                      {"features": [{"name": "prefers-color-scheme", "value": scheme}]})


def _load(d, url):
    d.get(url)
    assert _wait(lambda: _js(d, "return document.readyState;") == "complete")
    # a marker that only survives if the document is never replaced
    _js(d, "window.__sameDoc = true;")


def _sign_in_api(d, who):
    assert _js(d, "return fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},"
                  "body:JSON.stringify({username:arguments[0],password:arguments[1]})}).then(r=>r.status);",
               who, PASSWORD) == 200


def _me(d):
    return _js(d, "return fetch('/api/me').then(r=>r.json());")


def _sheet_open(d):
    return _js(d, "const s=document.getElementById('signin-sheet');return !!(s&&s.open);")


class Walk:
    """The spec's counter: clicks, typed fields, screens (the start counts)."""
    def __init__(self, d):
        self.d, self.clicks, self.typed, self.screens = d, 0, 0, 1

    def click(self, css, opens_screen=False):
        el = self.d.find_element(By.CSS_SELECTOR, css)
        el.click()
        self.clicks += 1
        if opens_screen:
            self.screens += 1
        return el

    def click_el(self, el, opens_screen=False):
        el.click()
        self.clicks += 1
        if opens_screen:
            self.screens += 1

    def type(self, css, text):
        el = self.d.find_element(By.CSS_SELECTOR, css)
        el.clear()
        el.send_keys(text)
        self.typed += 1

    def counts(self):
        return (self.clicks, self.typed, self.screens)


# ── T0 ──────────────────────────────────────────────────────────────────────

def test_t0_from_checklists_two_clicks_same_url_same_scroll(drv, base, round_uid):
    d = drv
    _size(d, 1440, 600)                       # short enough that the round scrolls
    _load(d, base + "/checklists")
    assert _wait(lambda: len(d.find_elements(By.CSS_SELECTOR, ".item[data-item]")) == 14)
    assert _js(d, "return document.body.classList.contains('anon');")
    main_scroller = "const m=document.querySelector('main');"
    _js(d, main_scroller + "m.scrollTop = 260; window.scrollTo(0, 0);")
    y0 = _js(d, main_scroller + "return m.scrollTop;")
    assert y0 > 100, "the round must actually be scrolled for this to prove anything"
    url0 = d.current_url

    w = Walk(d)
    w.click("#btnAuth", opens_screen=True)
    assert _wait(lambda: _sheet_open(d))
    assert d.find_element(By.ID, "signin-title").text == "Sign in"
    assert _js(d, "return document.activeElement.id;") == "signin-user", "the cursor waits in the name field"
    w.type("#signin-user", "Cody")
    w.type("#signin-pass", PASSWORD)
    w.click("#signin-ok")
    assert _wait(lambda: not _sheet_open(d))
    assert _wait(lambda: "Cody" in d.find_element(By.ID, "who").text)

    assert w.counts() == (2, 2, 2), f"T0 walked {w.counts()}; target 2 / 2 / +1"
    assert d.current_url == url0
    assert _js(d, "return window.__sameDoc === true;"), "the page was reloaded or replaced"
    assert _js(d, main_scroller + "return m.scrollTop;") == y0, "the scroll moved"
    assert not _js(d, "return document.body.classList.contains('anon');")
    assert _me(d) == {"authenticated": True, "user": "Cody"}
    assert d.find_element(By.ID, "btnAuth").get_attribute("textContent").strip() == "Sign out"


# ── gated tick ──────────────────────────────────────────────────────────────

def test_a_signed_out_tick_opens_sign_in_to_tick_and_lands(drv, base, server, round_uid):
    d = drv
    _load(d, base + "/checklists")
    assert _wait(lambda: len(d.find_elements(By.CSS_SELECTOR, ".item[data-item]")) == 14)
    third = round_uid["items"][2]["uid"]
    w = Walk(d)
    w.click(f'.item[data-item="{third}"]', opens_screen=True)     # the tick (the task's own click)
    assert _wait(lambda: _sheet_open(d))
    assert d.find_element(By.ID, "signin-title").text == "Sign in to tick"
    assert d.find_element(By.ID, "signin-ok").text == "Sign in and tick"
    w.type("#signin-user", "Cody")
    w.type("#signin-pass", PASSWORD)
    w.click("#signin-ok")
    assert _wait(lambda: not _sheet_open(d))
    # the tick continues by itself: on the server, by Cody, and on screen
    assert _wait(lambda: (server_state(server, round_uid["uid"]).get(third) or {}).get("checked")), \
        (third, server["app"].test_client().get("/api/checklists").get_json())
    assert server_state(server, round_uid["uid"])[third].get("user") == "Cody"
    assert _wait(lambda: _js(d, f"return document.querySelector('.item[data-item=\"{third}\"]').dataset.checked;") == "1")
    # the overhead over a signed-in tick (1 click, 0 typed, 1 screen)
    c, t, s = w.counts()
    assert (c - 1, t, s - 1) == (1, 2, 1), f"gated tick overhead {(c - 1, t, s - 1)}; target +1 / +2 / +1"
    assert _js(d, "return window.__sameDoc === true;")
    # exactly one tick: nothing else on the round moved
    ticked = [k for k, v in server_state(server, round_uid["uid"]).items() if v.get("checked")]
    assert ticked == [third]


def test_cancel_drops_the_pending_tick(drv, base, server, round_uid):
    d = drv
    _load(d, base + "/checklists")
    assert _wait(lambda: len(d.find_elements(By.CSS_SELECTOR, ".item[data-item]")) == 14)
    first = round_uid["items"][0]["uid"]
    d.find_element(By.CSS_SELECTOR, f'.item[data-item="{first}"]').click()
    assert _wait(lambda: _sheet_open(d))
    d.find_element(By.ID, "signin-cancel").click()
    assert _wait(lambda: not _sheet_open(d))
    d.find_element(By.ID, "btnAuth").click()
    assert _wait(lambda: _sheet_open(d))
    assert d.find_element(By.ID, "signin-title").text == "Sign in", "a cancelled act must not keep its title"
    d.find_element(By.ID, "signin-user").send_keys("Cody")
    d.find_element(By.ID, "signin-pass").send_keys(PASSWORD)
    d.find_element(By.ID, "signin-ok").click()
    assert _wait(lambda: not _sheet_open(d))
    time.sleep(0.8)
    assert not any(v.get("checked") for v in server_state(server, round_uid["uid"]).values()), \
        "a tick somebody walked away from was applied"


def test_a_wrong_password_keeps_the_sheet_and_the_act(drv, base, server, round_uid):
    d = drv
    _load(d, base + "/checklists")
    assert _wait(lambda: len(d.find_elements(By.CSS_SELECTOR, ".item[data-item]")) == 14)
    second = round_uid["items"][1]["uid"]
    d.find_element(By.CSS_SELECTOR, f'.item[data-item="{second}"]').click()
    assert _wait(lambda: _sheet_open(d))
    d.find_element(By.ID, "signin-user").send_keys("Cody")
    d.find_element(By.ID, "signin-pass").send_keys("wrong")
    d.find_element(By.ID, "signin-ok").click()
    assert _wait(lambda: d.find_element(By.ID, "signin-error").is_displayed())
    assert "not accepted" in d.find_element(By.ID, "signin-error").text
    assert _sheet_open(d)
    assert d.find_element(By.ID, "signin-title").text == "Sign in to tick"
    # LabCore down reads differently from a wrong password
    pw = d.find_element(By.ID, "signin-pass")
    pw.clear()
    pw.send_keys("down")
    d.find_element(By.ID, "signin-ok").click()
    assert _wait(lambda: "LabCore did not answer" in d.find_element(By.ID, "signin-error").text)
    pw.clear()
    pw.send_keys(PASSWORD)
    d.find_element(By.ID, "signin-ok").click()
    assert _wait(lambda: (server_state(server, round_uid["uid"]).get(second) or {}).get("checked"))


# ── gated Mark done ─────────────────────────────────────────────────────────

def test_a_signed_out_mark_done_signs_in_then_opens_its_own_sheet(drv, base):
    d = drv
    _load(d, base + "/maintenance/classic")
    assert _wait(lambda: len(d.find_elements(By.CSS_SELECTOR, "[data-done]")) > 0)
    btn = d.find_element(By.CSS_SELECTOR, "[data-done]")
    uid = btn.get_attribute("data-done")
    # dimmed, not dead
    assert _js(d, "return getComputedStyle(arguments[0]).pointerEvents;", btn) != "none"
    assert float(_js(d, "return getComputedStyle(arguments[0]).opacity;", btn)) < 1

    w = Walk(d)
    w.click_el(btn, opens_screen=True)              # Mark done (the task's own click)
    assert _wait(lambda: _sheet_open(d))
    assert d.find_element(By.ID, "signin-title").text == "Sign in to mark done"
    assert d.find_element(By.ID, "signin-ok").text == "Sign in and mark done"
    w.type("#signin-user", "Cody")
    w.type("#signin-pass", PASSWORD)
    w.click("#signin-ok", opens_screen=True)        # ...and the Mark done sheet is the next screen
    assert _wait(lambda: not _sheet_open(d))
    assert _wait(lambda: _js(d, "return document.getElementById('done-sheet').open;")), \
        "the Mark done sheet must open by itself"
    assert _js(d, "return document.activeElement.id;") == "done-note"
    c, t, s = w.counts()
    # signed in, Mark done is 1 click and 1 screen (its sheet); the rest is sign-in
    assert (c - 1, t, s - 2) == (1, 2, 1), f"gated Mark done overhead {(c - 1, t, s - 2)}; target +1 / +2 / +1"
    # and it completes, recorded as Cody
    d.find_element(By.ID, "done-note").send_keys("Replaced filter")
    d.find_element(By.ID, "done-ok").click()
    assert _wait(lambda: not _js(d, "return document.getElementById('done-sheet').open;"))
    hist = _js(d, "return fetch('/api/maintenance-history').then(r=>r.json());")["history"]
    assert any(h.get("note") == "Replaced filter" and h.get("by") == "Cody" for h in hist), hist[:3]
    assert _js(d, "return window.__sameDoc === true;")
    assert uid


def test_mark_done_needs_a_note(drv, base):
    d = drv
    _load(d, base + "/maintenance/classic")
    _sign_in_api(d, "Cody")
    _load(d, base + "/maintenance/classic")
    assert _wait(lambda: len(d.find_elements(By.CSS_SELECTOR, "[data-done]")) > 0)
    d.find_element(By.CSS_SELECTOR, "[data-done]").click()
    assert _wait(lambda: _js(d, "return document.getElementById('done-sheet').open;"))
    assert not _sheet_open(d), "signed in, Mark done goes straight to its sheet"
    d.find_element(By.ID, "done-ok").click()
    time.sleep(0.4)
    assert _js(d, "return document.getElementById('done-sheet').open;"), "an empty note was accepted"


# ── the shell ───────────────────────────────────────────────────────────────

def test_on_a_shell_page_the_chip_signs_in_in_place(drv, base):
    d = drv
    _load(d, base + "/settings")
    _js(d, "window.scrollTo(0, 120);")
    y0 = _js(d, "return window.scrollY;")
    w = Walk(d)
    w.click("#user-chip", opens_screen=True)
    assert _wait(lambda: _sheet_open(d))
    w.type("#signin-user", "Cody")
    w.type("#signin-pass", PASSWORD)
    w.click("#signin-ok")
    assert _wait(lambda: not _sheet_open(d))
    assert w.counts() == (2, 2, 2)
    assert _wait(lambda: d.find_element(By.ID, "user-name").text == "Cody")
    assert _js(d, "return document.getElementById('user-initials').textContent;") == "C"
    assert _js(d, "return window.__sameDoc === true;") and _js(d, "return window.scrollY;") == y0
    # signed in, the chip is the menu again, and it offers Switch person and Sign out
    d.find_element(By.ID, "user-chip").click()
    assert _wait(lambda: d.find_element(By.ID, "user-menu").is_displayed())
    assert d.find_element(By.ID, "menu-switch").is_displayed()
    assert d.find_element(By.ID, "menu-signout").is_displayed()
    assert not d.find_elements(By.CSS_SELECTOR, "#menu-signin:not([hidden])")


def test_signed_out_the_menu_is_still_reachable(drv, base):
    """The chip signs in; Theme, Settings and Help still have a door."""
    d = drv
    _load(d, base + "/settings")
    d.find_element(By.ID, "user-more").click()
    assert _wait(lambda: d.find_element(By.ID, "user-menu").is_displayed())
    assert not _sheet_open(d)


def test_switch_person_in_place_and_cancel_keeps_the_last(drv, base):
    d = drv
    _load(d, base + "/settings")
    _sign_in_api(d, "Cody")
    _load(d, base + "/settings")
    d.find_element(By.ID, "user-chip").click()
    d.find_element(By.ID, "menu-switch").click()
    assert _wait(lambda: _sheet_open(d))
    assert d.find_element(By.ID, "signin-title").text == "Switch person"
    assert "Cody" in d.find_element(By.ID, "signin-why").text
    d.find_element(By.ID, "signin-cancel").click()
    assert _wait(lambda: not _sheet_open(d))
    assert _me(d)["user"] == "Cody", "Cancel signed the last person out"
    d.find_element(By.ID, "user-chip").click()
    d.find_element(By.ID, "menu-switch").click()
    assert _wait(lambda: _sheet_open(d))
    d.find_element(By.ID, "signin-user").send_keys("Ryan")
    d.find_element(By.ID, "signin-pass").send_keys(PASSWORD)
    d.find_element(By.ID, "signin-ok").click()
    assert _wait(lambda: d.find_element(By.ID, "user-name").text == "Ryan")
    assert _me(d)["user"] == "Ryan"
    assert _js(d, "return window.__sameDoc === true;")


def test_any_gated_control_on_a_shell_page_waits_and_runs_once(drv, base):
    d = drv
    _load(d, base + "/settings")
    _js(d, """
      const b = document.createElement('button');
      b.className = 'btn'; b.id = 'probe'; b.dataset.gated = 'mark done'; b.textContent = 'Mark done';
      window.__ran = 0; b.addEventListener('click', () => { window.__ran++; });
      document.querySelector('main').prepend(b);
    """)
    probe = d.find_element(By.ID, "probe")
    assert _js(d, "return getComputedStyle(arguments[0]).pointerEvents;", probe) != "none"
    probe.click()
    assert _wait(lambda: _sheet_open(d))
    assert _js(d, "return window.__ran;") == 0, "the gated handler ran signed out"
    assert d.find_element(By.ID, "signin-title").text == "Sign in to mark done"
    d.find_element(By.ID, "signin-user").send_keys("Cody")
    d.find_element(By.ID, "signin-pass").send_keys(PASSWORD)
    d.find_element(By.ID, "signin-ok").click()
    assert _wait(lambda: _js(d, "return window.__ran;") == 1)
    time.sleep(0.3)
    assert _js(d, "return window.__ran;") == 1
    probe.click()                                   # signed in now: straight through
    assert _js(d, "return window.__ran;") == 2 and not _sheet_open(d)


def test_no_js_road_puts_you_back(drv, base):
    d = drv
    d.get(base + "/signin?next=/checklists")
    d.find_element(By.CSS_SELECTOR, "input[name=username]").send_keys("Cody")
    d.find_element(By.CSS_SELECTOR, "input[name=password]").send_keys(PASSWORD)
    d.find_element(By.CSS_SELECTOR, "form[action='/signin'] button[type=submit]").click()
    assert _wait(lambda: d.current_url.endswith("/checklists"))
    assert _wait(lambda: _js(d, "return document.readyState;") == "complete")
    assert _wait(lambda: _me(d)["user"] == "Cody")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_sheet_reads_in_both_themes(drv, base, scheme):
    d = drv
    _os(d, scheme)
    _load(d, base + "/settings")
    d.find_element(By.ID, "user-chip").click()
    assert _wait(lambda: _sheet_open(d))
    d.find_element(By.ID, "signin-pass").send_keys("wrong")
    d.find_element(By.ID, "signin-ok").click()
    assert _wait(lambda: d.find_element(By.ID, "signin-error").is_displayed())
    assert _js(d, "return document.documentElement.dataset.theme;") == scheme
    bad = _js(d, "const inside=(e)=>e.closest('#signin-sheet');" + CONTRAST_JS.replace(
        "const out=[];", "const out=[];const __keep=inside;").replace(
        "if(cr<(large?3:4.5)", "if(inside(el)&&cr<(large?3:4.5)"))
    assert bad == [], bad
