"""The record's action sections in a real browser (piece 6).

What a person does is checked the way a person does it, in headless Chrome
against a real server on the dev seed, counting clicks, fields typed and
screens with ia-final §8's method:

* **T3: mark GC-1's Monthly PM done** (seeded, overdue). Row on Instruments
  → the card's "Mark the PM done…" → type the note → Mark it done. 3 clicks,
  1 typed, 3 screens (Instruments, the record, the sheet), and afterwards the
  address is still /instruments/gc-1#maintenance and the row reads
  Scheduled: the page repainted where it stands, with a toast.
* **T6: schedule an annual calibration** on Koehler K23000. Row →
  Schedule… → the "Annual calibration · 365 days" chip → Schedule it.
  4 clicks, 0 typed, 3 screens.
* **A refused write keeps its sheet open with the server's sentence, and no
  toast**, for both of the refusal shapes the suite drives
  (tests/refusal_shapes.py): the evidenced busy dict and the synthetic
  no-"error"-key answer. A toast for a write that did not land is the lie
  this codebase keeps finding; a closed sheet throws away what was typed.
* **The nine actions that were right-click-only are visible buttons**, and
  nothing on the page listens for a right-click.

Skipped when selenium or headless Chrome is missing; run it with GC hub's
venv. The port is ``LEM_UI_ACTIONS_PORT`` (default 5701); a busy port skips.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

webdriver = pytest.importorskip("selenium.webdriver")
from selenium.webdriver.common.by import By  # noqa: E402

import demo_floor  # noqa: E402
import refusal_shapes  # noqa: E402
from labcore_gateway import FakeLabCoreGateway  # noqa: E402
from web_app import create_app  # noqa: E402

PORT = int(os.environ.get("LEM_UI_ACTIONS_PORT", "5701"))


class SwitchGateway(FakeLabCoreGateway):
    """Refuses every row write while `refusing`, the way LabCore refuses:
    an answer returned, never raised."""

    refusing = False

    def sql(self, sql, args=None, **kw):
        if self.refusing and sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            return refusal_shapes.current()
        return super().sql(sql, args, **kw)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from werkzeug.serving import make_server
    root = tmp_path_factory.mktemp("lem-actions")
    gw = SwitchGateway()
    app = create_app(gw, secret="ui-test", admin_password="ui-pass", documents_root=str(root))
    app.config["SNAPSHOTS"].ensure_schema()
    demo_floor.seed(gw, documents_root=str(root))
    app.config["SNAPSHOTS"].refresh()
    try:
        srv = make_server("127.0.0.1", PORT, app, threaded=True)
    except OSError as exc:
        pytest.skip(f"port {PORT} is busy: {exc}")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
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
    d.set_window_size(1440, 900)
    d.get(server.base + "/")
    d.execute_script("return fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},"
                     "body:JSON.stringify({username:'ryan',password:'ui-pass'})})")
    time.sleep(0.3)
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


def _visible(d, el):
    return d.execute_script("const e=arguments[0]; return !!e && !e.closest('[hidden]') && e.getClientRects().length > 0;", el)


def _button(d, text, scope="main"):
    """The visible button or link whose words are `text`."""
    for el in d.find_elements(By.CSS_SELECTOR, scope + " button, " + scope + " a"):
        if el.text.strip() == text and _visible(d, el):
            return el
    return None


class Walk:
    """ia-final §8's counting: a click is any activation; a field typed in
    is 1; a screen is a page load or a sheet, the start screen counted."""

    def __init__(self, d):
        self.d, self.clicks, self.typed, self.screens = d, 0, 0, 0

    def go(self, url):
        self.d.get(url)
        self.screens += 1

    def click(self, el, screen=False):
        self.d.execute_script("arguments[0].scrollIntoView({block:'center'})", el)
        el.click()
        self.clicks += 1
        if screen:
            self.screens += 1

    def type(self, el, text):
        el.send_keys(text)
        self.typed += 1


def _toast(d):
    return d.execute_script("const t=document.getElementById('toast'); return t.classList.contains('show') ? t.textContent : '';")


def _record_ready(d):
    assert _wait(lambda: d.execute_script("return document.getElementById('ready-cap').textContent.length > 0"))
    assert _wait(lambda: not d.find_elements(By.CSS_SELECTOR, "#ca-body [aria-busy=true], #doc-body [aria-busy=true]"))


def test_t3_mark_gc1s_monthly_pm_done(server, drv):
    w = Walk(drv)
    w.go(server.base + "/")
    row = _wait(lambda: drv.find_element(By.CSS_SELECTOR, "a[href='/instruments/gc-1']"))
    w.click(row, screen=True)
    _record_ready(drv)
    w.click(drv.find_element(By.ID, "ready-primary"), screen=True)
    assert drv.find_element(By.ID, "ready-primary").text == "Mark the PM done…"
    assert _wait(lambda: drv.find_element(By.ID, "done-sheet").get_attribute("open") is not None)
    w.type(drv.find_element(By.ID, "done-note"), "Replaced inlet liner and septum")
    w.click(drv.find_element(By.ID, "done-go"))
    assert _wait(lambda: drv.find_element(By.ID, "done-sheet").get_attribute("open") is None)
    assert "Monthly PM marked done" in (_wait(lambda: _toast(drv)) or "")
    # repainted in place: the row now reads Scheduled, the card no longer
    # asks for the PM, and nobody navigated anywhere
    row_text = lambda: drv.find_element(By.CSS_SELECTOR, "tr[data-task='gc-1-pm']").text  # noqa: E731
    assert _wait(lambda: "Scheduled" in row_text(), timeout=10), row_text()
    assert _wait(lambda: "Replaced inlet liner" in drv.find_element(By.ID, "mt-body").text, timeout=10)
    assert drv.current_url.endswith("/instruments/gc-1#maintenance"), drv.current_url
    assert (w.clicks, w.typed, w.screens) == (3, 1, 3)


def test_t6_schedule_an_annual_calibration(server, drv):
    w = Walk(drv)
    w.go(server.base + "/")
    w.click(_wait(lambda: drv.find_element(By.CSS_SELECTOR, "a[href='/instruments/koehler-visc']")), screen=True)
    _record_ready(drv)
    before = len(drv.find_elements(By.CSS_SELECTOR, "#mt-body tr[data-task]"))
    w.click(_button(drv, "Schedule…"), screen=True)
    chip = _wait(lambda: _button(drv, "Annual calibration · 365 days", "#sched-sheet"))
    w.click(chip)
    assert drv.find_element(By.ID, "sched-name").get_attribute("value") == "Annual calibration"
    # it already has one: said before saving, not after
    assert "already has an Annual calibration" in drv.find_element(By.ID, "sched-dup").text
    w.click(drv.find_element(By.ID, "sched-go"))
    assert _wait(lambda: drv.find_element(By.ID, "sched-sheet").get_attribute("open") is None)
    assert "Annual calibration scheduled" in (_wait(lambda: _toast(drv)) or "")
    assert _wait(lambda: len(drv.find_elements(By.CSS_SELECTOR, "#mt-body tr[data-task]")) == before + 1, timeout=10)
    assert drv.current_url.endswith("#maintenance")
    assert (w.clicks, w.typed, w.screens) == (4, 0, 3)


# each refusal is driven through a different sheet, so the rule is the
# page's, not one sheet's
SHEETS = {
    "done": ("gc-2", lambda d: d.find_element(By.CSS_SELECTOR, "#mt-body [data-act='mt-done']"),
             lambda d: d.find_element(By.ID, "done-note").send_keys("Cleaned the cell"), "done-sheet", "done-go", "done-err"),
    "schedule": ("pac-flash-2", lambda d: _button(d, "Schedule…"), lambda d: None, "sched-sheet", "sched-go", "sched-err"),
    "offline": ("pac-flash-2", lambda d: d.find_element(By.ID, "topbar-online"),
                lambda d: d.find_element(By.ID, "online-comment").send_keys("Pump service"), "online-sheet", "online-go", "online-err"),
    "correction": ("pac-flash-2", lambda d: _button(d, "Add a correction…"),
                   lambda d: (d.find_element(By.ID, "corr-value").send_keys("-0.5"),
                              d.find_element(By.ID, "corr-reason").send_keys("bias study"),
                              d.find_element(By.ID, "corr-other").send_keys("Flash Point")
                              if d.find_element(By.ID, "corr-other").is_displayed() else None),
                   "corr-sheet", "corr-go", "corr-err"),
    "reset": ("gc-1", lambda d: _button(d, "Reset position"), lambda d: None, "confirm-sheet", "confirm-go", "confirm-err"),
    "step": ("optimpp-1", lambda d: d.find_element(By.CSS_SELECTOR, "[data-testid='ca-next']"),
             lambda d: d.find_element(By.ID, "step-text").send_keys("Recalibrated the cloud point sensor"), "step-sheet", "step-go", "step-err"),
}


@pytest.mark.usefixtures("both_refusal_shapes")
@pytest.mark.parametrize("sheet", sorted(SHEETS))
def test_a_refused_write_keeps_the_sheet_open_with_the_servers_sentence(server, drv, sheet):
    uid, opener, fill, dlg, go, err = SHEETS[sheet]
    drv.get(server.base + "/instruments/" + uid)
    _record_ready(drv)
    drv.execute_script("document.getElementById('toast').className='toast'")
    btn = _wait(lambda: opener(drv))
    drv.execute_script("arguments[0].scrollIntoView({block:'center'})", btn)
    btn.click()
    assert _wait(lambda: drv.find_element(By.ID, dlg).get_attribute("open") is not None), sheet
    fill(drv)
    # remember what the server said, to compare with what the sheet says
    drv.execute_script("""
        window.__said = null; const f = window.__realFetch || window.fetch; window.__realFetch = f;
        window.fetch = (u, o) => f(u, o).then(r => { if (o && o.method && o.method !== 'GET' && !r.ok)
            r.clone().json().then(b => { window.__said = b.error; }).catch(() => {}); return r; });""")
    server.gw.refusing = True
    try:
        drv.find_element(By.ID, go).click()
        line = _wait(lambda: drv.find_element(By.ID, err).text if drv.find_element(By.ID, err).is_displayed() else "")
    finally:
        server.gw.refusing = False
    said = _wait(lambda: drv.execute_script("return window.__said"))
    assert said and line == said, (sheet, line, said)
    # and the sentence says it did not land, in the person's words
    assert "NOT" in line or "not" in line, (sheet, line)
    assert drv.find_element(By.ID, dlg).get_attribute("open") is not None, sheet
    time.sleep(0.4)
    assert _toast(drv) == "", (sheet, _toast(drv))
    drv.find_element(By.CSS_SELECTOR, "#%s [data-close]" % dlg).click()


def test_the_nine_former_right_click_actions_are_visible_buttons(server, drv):
    """lem-ui.md P4's list, each found by its words on the record."""
    drv.get(server.base + "/instruments/optimpp-1")
    _record_ready(drv)
    for words in ("Add a correction…", "Take off line…", "Move to another level…", "Reset position",
                  "Remove from LEM…", "Export history (CSV)", "Export QC (CSV)"):
        assert _button(drv, words, "body") is not None, words
    # Flag for service and Dead-line are the two choices of Take off line…
    _button(drv, "Take off line…", "body").click()
    assert _wait(lambda: drv.find_element(By.ID, "online-sheet").get_attribute("open") is not None)
    labels = drv.find_element(By.ID, "online-kind").text
    assert "Out for service" in labels and "Dead line" in labels
    drv.find_element(By.CSS_SELECTOR, "#online-sheet [data-close]").click()
    # Clear override: the card's Put back on line… on an instrument off line
    with server.app.test_client() as c:
        c.post("/api/login", json={"username": "ryan", "password": "ui-pass"})
        assert c.post("/api/machines/pac-flash-1/override", json={"override": "DEAD-LINE", "comment": "x"}).status_code == 200
    server.app.config["SNAPSHOTS"].refresh()
    drv.get(server.base + "/instruments/pac-flash-1")
    _record_ready(drv)
    assert drv.find_element(By.ID, "ready-primary").text == "Put back on line…"
    assert _visible(drv, drv.find_element(By.ID, "ready-primary"))
    # and nothing on the page listens for a right-click
    assert drv.execute_script("""
        const ev = new MouseEvent('contextmenu', {bubbles: true, cancelable: true});
        return document.querySelector('#main').dispatchEvent(ev);""") is True


def test_remove_asks_for_the_name_and_the_password_then_says_it_is_gone(server, drv):
    drv.get(server.base + "/instruments/cetane-calc")
    _record_ready(drv)
    _button(drv, "Remove from LEM…").click()
    assert _wait(lambda: drv.find_element(By.ID, "remove-sheet").get_attribute("open") is not None)
    drv.find_element(By.ID, "remove-name").send_keys("Cetane")
    drv.find_element(By.ID, "remove-go").click()
    assert "exactly" in drv.find_element(By.ID, "remove-err").text
    drv.find_element(By.ID, "remove-name").clear()
    drv.find_element(By.ID, "remove-name").send_keys("Cetane Bench")
    drv.find_element(By.ID, "remove-pw").send_keys("wrong")
    drv.find_element(By.ID, "remove-go").click()
    assert _wait(lambda: "password was not accepted" in drv.find_element(By.ID, "remove-err").text)
    assert drv.find_element(By.ID, "remove-sheet").get_attribute("open") is not None
    drv.find_element(By.ID, "remove-pw").clear()
    drv.find_element(By.ID, "remove-pw").send_keys("ui-pass")
    drv.find_element(By.ID, "remove-go").click()
    assert _wait(lambda: drv.find_elements(By.CSS_SELECTOR, "[data-testid=removed]"))
    assert "removed from LEM" in drv.find_element(By.ID, "main").text
    assert _button(drv, "Back to Instruments").get_attribute("href").endswith("/")
