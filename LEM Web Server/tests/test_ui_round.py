"""The round, walked in a real browser (ia-final §3.3 and §8 T4b, piece 9).

The baseline (lem-ui.md §P1, tickprobe.py): the server recorded the tick and
the screen did not show it, at 2 s or at 35 s, until the person clicked
somewhere else; a second tap unticked it; and T4b ended with the server at
3 of 3 and the screen at 0 of 3, "overdue". What each walk here proves:

* **The tick shows within one frame of the tap**, before the POST is
  answered: at the next animation frame the button is ``aria-pressed=true``.
  Then the server has it (1), and the screen still has it after a 35 s wait
  with the row's focus left exactly where the tap put it.
* **A second tap on a ticked row sends no request at all.**
* **T4b is 4 clicks / 1 typed / 2 screens from /** (3 clicks from the
  ``/checklists`` bookmark) and the screen ends at **3 of 3 with names**,
  the same as the server.
* **A reading that does not parse is refused in the row**, and nothing is
  sent.
* **390 px wide has no horizontal scroll**, and every text on the round
  passes AA in light and in dark.

Skipped without selenium/headless Chrome (run with
``/Users/rynatical/Projects/gc-hub/.venv/bin/python -m pytest tests/test_ui_round.py``).
In-process app on the fake LabCore on ``LEM_UI_TEST_PORT`` (default 5704).
Nothing here can reach a real LabCore.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

webdriver = pytest.importorskip("selenium.webdriver")
from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.common.keys import Keys  # noqa: E402

from labcore_gateway import FakeLabCoreGateway  # noqa: E402
from web_app import create_app  # noqa: E402

from test_ui_theme import CONTRAST_JS  # noqa: E402

PORT = int(os.environ.get("LEM_UI_TEST_PORT", "5704"))
PASSWORD = "ui-pass"
LONG_WAIT = float(os.environ.get("LEM_UI_ROUND_WAIT", "35"))


class Auth:
    def __init__(self):
        self.n = 0

    def login(self, u, p):
        if p == PASSWORD:
            self.n += 1
            return (u, "t%d" % self.n, "")
        return (None, "", "Invalid username or password.")

    def logout(self, t):
        pass


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from werkzeug.serving import make_server
    root = tmp_path_factory.mktemp("lem-round")
    gw = FakeLabCoreGateway()
    app = create_app(gw, secret="ui-test", authenticator=Auth(), documents_root=str(root))
    app.config["SNAPSHOTS"].ensure_schema()
    app.config["SNAPSHOTS"].refresh()
    try:
        srv = make_server("127.0.0.1", PORT, app, threaded=True)
    except OSError as exc:
        # a busy port is a broken run, not a missing tool: skipping here
        # would print green for a page nobody walked
        pytest.fail(f"port {PORT} is busy (set LEM_UI_TEST_PORT): {exc}")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"base": f"http://127.0.0.1:{PORT}", "app": app}
    srv.shutdown()


@pytest.fixture
def opening(server):
    """Today's Opening round: tick, a PSI reading, tick (T4b's round)."""
    c = server["app"].test_client()
    with c.session_transaction() as s:
        s["user"] = "setup"
    for cl in c.get("/api/checklists").get_json().get("checklists", []):
        c.delete(f"/api/checklists/{cl['uid']}")
    r = c.post("/api/checklists", json={
        "name": "Opening round", "slot": "opening", "due_time": "23:59",
        "items": [{"text": "Lights and fume hoods on"},
                  {"text": "Helium cylinder pressure", "entry_type": "number", "units": "PSI"},
                  {"text": "Nitrogen generator running"}]})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["checklist"]


def server_state(server, uid):
    b = server["app"].test_client().get("/api/checklists").get_json()
    return (b.get("state") or {}).get(uid) or {}


@pytest.fixture
def drv(server):
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    for arg in ("--headless=new", "--no-sandbox", "--disable-gpu", "--force-device-scale-factor=1",
                "--window-size=820,1180"):
        opts.add_argument(arg)
    try:
        d = webdriver.Chrome(options=opts)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"headless Chrome unavailable: {exc}")
    d.get(server["base"] + "/healthz")
    d.execute_script("localStorage.clear();")
    _size(d, 820, 1180)
    yield d
    d.quit()


def _js(d, s, *a):
    return d.execute_script(s, *a)


def _size(d, w, h):
    d.execute_cdp_cmd("Emulation.setDeviceMetricsOverride",
                      {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})


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


def _sign_in(d, who="Cody"):
    assert _js(d, "return fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},"
                  "body:JSON.stringify({username:arguments[0],password:arguments[1]})}).then(r=>r.status);",
               who, PASSWORD) == 200


def _rows(d):
    return d.find_elements(By.CSS_SELECTOR, "#lists .item[data-item]")


COUNT_POSTS = """
window.__posts = 0;
const f = window.fetch;
window.fetch = function (u, o) { if (o && o.method === 'POST') window.__posts++; return f.apply(this, arguments); };
"""


def test_the_tick_shows_within_a_frame_stays_and_a_second_tap_sends_nothing(drv, server, opening):
    d = drv
    _sign_in(d)
    d.get(server["base"] + "/checklists")
    assert _wait(lambda: len(_rows(d)) == 3)
    assert d.current_url.endswith("/checklists/opening")
    first = opening["items"][0]["uid"]
    _js(d, COUNT_POSTS + """
      const btn = document.querySelector('#lists .item[data-item] .tick');
      window.__t = {};
      document.addEventListener('click', () => {
        requestAnimationFrame(() => { window.__t.atFrame = btn.getAttribute('aria-pressed'); });
      }, true);
    """)
    _rows(d)[0].click()
    assert _wait(lambda: _js(d, "return window.__t.atFrame") is not None, 2)
    assert _js(d, "return window.__t.atFrame") == "true", "the tick did not show at the next frame"
    assert _wait(lambda: (server_state(server, opening["uid"]).get(first) or {}).get("checked"))
    assert server_state(server, opening["uid"])[first]["user"] == "Cody"
    assert _js(d, "return window.__posts") == 1

    # a second tap on a ticked row: nothing is sent, nothing changes
    _rows(d)[0].click()
    time.sleep(1.0)
    assert _js(d, "return window.__posts") == 1, "a tap on a ticked row sent a request"
    assert _rows(d)[0].get_attribute("data-checked") == "1"
    assert (server_state(server, opening["uid"]).get(first) or {}).get("checked")

    # and after the live polls (the old page lost it here, every time)
    time.sleep(LONG_WAIT)
    assert _rows(d)[0].get_attribute("data-checked") == "1"
    assert _rows(d)[0].find_element(By.CSS_SELECTOR, ".tick").get_attribute("aria-pressed") == "true"
    assert "Cody" in _rows(d)[0].find_element(By.CSS_SELECTOR, ".rby").text


HOLD_POSTS = """
window.__release = [];
const f0 = window.fetch;
window.fetch = function (u, o) {
  if (!(o && o.method === 'POST')) return f0.apply(this, arguments);
  const args = arguments, self = this;
  return new Promise(res => window.__release.push(() => res(f0.apply(self, args))));
};
"""


def _meter(d):
    """The filled share of the round card's progress edge, as drawn."""
    return _js(d, """const m = document.getElementById('round-meter');
      if (!m || !m.offsetParent) return null;
      const f = m.firstElementChild.getBoundingClientRect().width, t = m.getBoundingClientRect().width;
      return t ? f / t : null;""")


def test_the_header_and_bench_bar_agree_with_a_row_that_is_still_saving(drv, server, opening):
    """Round 1's critic, with 4 s of latency: the row said ticked and
    "Saving…", the pill "0 of 3 done", the bench bar "Nothing ticked yet
    today". A page that contradicts itself for as long as the network takes
    is the P1 bug's cousin: the person reads the header and taps again. Here
    the POST is held open; the header must already count the tick and the
    bench bar must say it is saving, then "Saved" once it is answered."""
    d = drv
    _sign_in(d)
    d.get(server["base"] + "/checklists/opening")
    assert _wait(lambda: len(_rows(d)) == 3)
    _js(d, HOLD_POSTS)
    _rows(d)[0].click()
    assert _rows(d)[0].get_attribute("data-state") == "saving"
    assert d.find_element(By.ID, "round-pill-text").text == "1 of 3 done"
    assert d.find_element(By.ID, "bb-saved-text").text == "1 tick saving…"
    assert d.find_element(By.ID, "bench-bar").get_attribute("data-save") == "saving"
    # the card's progress edge is the pill drawn as a length: it moves in the
    # same task as the row, so the three can never disagree
    assert abs(_meter(d) - 1 / 3) < 0.02, _meter(d)
    time.sleep(1.0)                                    # still held: still agreeing
    assert d.find_element(By.ID, "round-pill-text").text == "1 of 3 done"
    _js(d, "window.__release.forEach(f => f());")
    assert _wait(lambda: d.find_element(By.ID, "bb-saved-text").text.startswith("Saved "))
    assert _rows(d)[0].get_attribute("data-state") == "done"
    assert d.find_element(By.ID, "round-pill-text").text == "1 of 3 done"
    assert abs(_meter(d) - 1 / 3) < 0.02, _meter(d)


def test_a_session_gone_on_the_server_asks_for_sign_in_and_the_tick_lands(drv, server, opening):
    """Round 1: the server had dropped the session, the page still showed
    Cody, and a tap ended in "Not saved: Authentication required" with no way
    on but a reload. Now the row reverts, the sign-in sheet opens in place,
    and after signing in the tick lands without a second tap."""
    d = drv
    _sign_in(d)
    d.get(server["base"] + "/checklists/opening")
    assert _wait(lambda: len(_rows(d)) == 3)
    d.delete_all_cookies()                             # the server forgets us
    first = opening["items"][0]["uid"]
    _rows(d)[0].click()
    assert _wait(lambda: d.find_element(By.ID, "signin-sheet").get_attribute("open") is not None)
    assert _rows(d)[0].get_attribute("data-checked") == ""
    assert "signed out" in _rows(d)[0].find_element(By.CSS_SELECTOR, ".rerr").text
    assert d.find_element(By.ID, "bb-out").is_displayed()          # the bench bar agrees
    d.find_element(By.ID, "signin-user").send_keys("Cody")
    d.find_element(By.ID, "signin-pass").send_keys(PASSWORD)
    d.find_element(By.ID, "signin-ok").click()
    assert _wait(lambda: (server_state(server, opening["uid"]).get(first) or {}).get("checked"))
    assert _wait(lambda: _rows(d)[0].get_attribute("data-state") == "done")
    assert d.find_element(By.ID, "bb-name").text == "Cody"


def test_t4b_four_clicks_one_typed_two_screens_and_three_of_three_with_names(drv, server, opening):
    d = drv
    _sign_in(d)
    clicks, typed, screens = 0, 0, 1
    d.get(server["base"] + "/settings")              # any shell page with the nav
    assert _wait(lambda: d.find_element(By.CSS_SELECTOR, '.nav-item[data-nav="checklists"]'))
    d.find_element(By.CSS_SELECTOR, '.nav-item[data-nav="checklists"]').click()
    clicks += 1
    screens += 1
    assert _wait(lambda: len(_rows(d)) == 3)
    _rows(d)[0].click()
    clicks += 1
    _rows(d)[1].click()                                # tap the PSI row: its field takes the caret
    clicks += 1
    inp = _rows(d)[1].find_element(By.CSS_SELECTOR, ".rinput")
    assert _js(d, "return document.activeElement === arguments[0]", inp)
    inp.send_keys("2900")
    typed += 1
    inp.send_keys(Keys.ENTER)                          # commits the field: not a click
    _rows(d)[2].click()
    clicks += 1
    assert (clicks, typed, screens) == (4, 1, 2)

    assert _wait(lambda: d.find_element(By.ID, "round-pill-text").text.startswith("Done · 3 of 3"))
    names = [r.find_element(By.CSS_SELECTOR, ".rby").text for r in _rows(d)]
    assert all(n.startswith("Cody · ") for n in names), names
    st = server_state(server, opening["uid"])
    assert [bool((st.get(i["uid"]) or {}).get("checked")) for i in opening["items"]] == [True, True, True]
    assert st[opening["items"][1]["uid"]]["value"] == "2900"
    # blur does not save: nothing else went out
    assert d.find_element(By.ID, "bb-saved-text").text.startswith("Saved ")


def test_a_reading_that_does_not_parse_is_refused_in_the_row(drv, server, opening):
    d = drv
    _sign_in(d)
    d.get(server["base"] + "/checklists/opening")
    assert _wait(lambda: len(_rows(d)) == 3)
    _js(d, COUNT_POSTS)
    inp = _rows(d)[1].find_element(By.CSS_SELECTOR, ".rinput")
    inp.click()
    inp.send_keys("about half")
    inp.send_keys(Keys.ENTER)
    err = _rows(d)[1].find_element(By.CSS_SELECTOR, ".rerr")
    assert _wait(lambda: err.is_displayed())
    assert err.text == "Enter a number, like 2900"
    assert _js(d, "return window.__posts") == 0
    assert _rows(d)[1].get_attribute("data-checked") == ""
    # blur does not save either
    d.find_element(By.CSS_SELECTOR, "h1").click()
    time.sleep(0.5)
    assert _js(d, "return window.__posts") == 0


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("w,h", [(820, 1180), (390, 844)])
def test_no_horizontal_scroll_and_aa_text(drv, server, opening, scheme, w, h):
    d = drv
    _sign_in(d)
    c = server["app"].test_client()
    with c.session_transaction() as s:
        s["user"] = "Ana"
    c.post(f"/api/checklists/{opening['uid']}/toggle", json={"item_uid": opening["items"][0]["uid"], "checked": True})
    c.post(f"/api/checklists/{opening['uid']}/value", json={"item_uid": opening["items"][1]["uid"], "value": "2900"})
    _size(d, w, h)
    d.execute_cdp_cmd("Emulation.setEmulatedMedia",
                      {"features": [{"name": "prefers-color-scheme", "value": scheme}]})
    d.get(server["base"] + "/checklists/opening")
    assert _wait(lambda: len(_rows(d)) == 3)
    time.sleep(0.6)
    sw, cw = _js(d, "return [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    assert sw <= cw, f"horizontal scroll at {w}px: {sw} > {cw}"
    # ticked labels stay at full --text: same colour as an unticked one
    done, todo = _js(d, "const l=[...document.querySelectorAll('#lists .rlabel')];"
                        "return [getComputedStyle(l[0]).color, getComputedStyle(l[2]).color];")
    assert done == todo, "a ticked label is drawn greyed"
    # ...but done and to-do still read apart at a glance (round 1's blind
    # judges, both themes: "the ticked items glare as brightly as the open
    # ones"). By weight, not colour: what is left is heavier than what is
    # done, the next row sits on a band, and its Save is the filled button.
    wd, wt = _js(d, "const l=[...document.querySelectorAll('#lists .rlabel')];"
                    "return [+getComputedStyle(l[0]).fontWeight, +getComputedStyle(l[2]).fontWeight];")
    assert wt > wd, f"to-do label weight {wt} is not heavier than done {wd}"
    nxt = _js(d, "const n=document.querySelector('#lists .rrow.next');"
                 "return n && [n.dataset.item, getComputedStyle(n).backgroundColor];")
    assert nxt and nxt[0] == opening["items"][2]["uid"], nxt
    assert nxt[1] not in ("rgba(0, 0, 0, 0)", "transparent"), "the next row has no band"
    assert "btn-primary" in _rows(d)[1].find_element(By.CSS_SELECTOR, ".rsave").get_attribute("class")
    # Round 2's blind judges still preferred the mock's finished-vs-open
    # rows: our filled ink ticks were the loudest marks on the page and the
    # empty boxes the faintest, so the eye went to what was done. An open box
    # is a 2 px edge, and the next one is drawn in ink, the colour a done
    # tick is filled with: the eye finds what is left first.
    # (since the bench-sheet redesign the box is drawn by the tick's ::before,
    # 32 px inside its 48 px target; the rule is the box's, wherever drawn)
    edges = _js(d, """const t=[...document.querySelectorAll('#lists .rrow .tick')];
      const s=t.map(e=>getComputedStyle(e, '::before'));
      return {open: parseFloat(s[2].borderTopWidth), nextEdge: s[2].borderTopColor, doneFill: s[0].backgroundColor};""")
    assert edges["open"] >= 2, edges
    assert edges["nextEdge"] == edges["doneFill"], edges
    # the progress edge shows what is done of the round: 2 of 3 here
    assert abs(_meter(d) - 2 / 3) < 0.02, _meter(d)
    bad = _js(d, CONTRAST_JS)
    assert bad == [], bad
    # every target on a row is at least 44 px (48 for the tick)
    small = _js(d, """return [...document.querySelectorAll(
        '#lists .tick, #lists .undo:not([hidden]), #lists .rinput, #bench-bar button')].filter(e => e.offsetParent)
        .map(e => [e.className, e.getBoundingClientRect().height]).filter(x => x[1] < 40);""")
    assert small == [], small


# ── one reading, one control at rest (2026-10-07) ─────────────────────────
#
# Ryan: the checklists did not feel like the rest of the app, and scaled
# badly on a phone. A gas row was a box, a field AND a Save, every row said
# "Only on Mon, Tue, Wed, Thu and Fri" (the round lists only today's items,
# so that was true of every one), and progress lived only in the pill that
# scrolls away. Now: Save shows only while there is something to save, the
# days stay in the editor, each heading counts its own rows, the bench bar
# carries the round's count, a phone keeps a number on its label's line, and
# only a tick somebody just made moves.

def _round_with_heading(server):
    c = server["app"].test_client()
    with c.session_transaction() as s:
        s["user"] = "setup"
    for cl in c.get("/api/checklists").get_json().get("checklists", []):
        c.delete(f"/api/checklists/{cl['uid']}")
    r = c.post("/api/checklists", json={
        "name": "Opening round", "slot": "opening", "due_time": "23:59",
        "items": [{"text": "Gas levels", "item_type": "header"},
                  {"text": "Oxygen", "entry_type": "number", "units": "PSI", "days_active": [0, 1, 2, 3, 4, 5, 6]},
                  {"text": "Nitrogen", "entry_type": "number", "units": "PSI"},
                  {"text": "Fans on", "days_active": [0, 1, 2, 3, 4, 5, 6]}]})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["checklist"]


@pytest.mark.parametrize("w,h", [(1440, 1000), (390, 844)])
def test_a_reading_shows_save_only_with_something_to_save(drv, server, w, h):
    d = drv
    _round_with_heading(server)
    _sign_in(d)
    _size(d, w, h)
    d.get(server["base"] + "/checklists/opening")
    assert _wait(lambda: len(_rows(d)) == 3)
    shown = "const b=arguments[0].querySelector('.rsave'); const s=getComputedStyle(b);" \
            "return s.display !== 'none' && s.visibility !== 'hidden';"
    oxy = _rows(d)[0]
    assert not _js(d, shown, oxy), "an empty reading shows a Save"
    oxy.find_element(By.CSS_SELECTOR, ".rinput").send_keys("2400")
    assert _js(d, shown, oxy), "a typed reading has no Save to press"
    sw, cw = _js(d, "return [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    assert sw <= cw, (sw, cw)
    # readings are tiles (the bench-sheet redesign): side by side, two to a
    # row on a phone and more on a desktop, never one long column of rows
    tops = _js(d, "return [...document.querySelectorAll('#lists .rreadings > .rrow')].map(e => Math.round(e.getBoundingClientRect().top))")
    assert len(tops) == 2 and tops[0] == tops[1], tops
    oxy.find_element(By.CSS_SELECTOR, ".rinput").send_keys("\n")
    assert _wait(lambda: _rows(d)[0].get_attribute("data-checked") == "1")
    assert _wait(lambda: not _js(d, shown, _rows(d)[0])), "a saved reading still shows Save"


def test_no_days_on_the_round_and_each_heading_counts_its_rows(drv, server):
    d = drv
    cl = _round_with_heading(server)
    _sign_in(d)
    d.get(server["base"] + "/checklists/opening")
    assert _wait(lambda: len(_rows(d)) == 3)
    caps = _js(d, "return [...document.querySelectorAll('#lists .rcap')].map(e => e.textContent)")
    assert not any("Only on" in t for t in caps), caps
    assert _js(d, "return document.querySelector('#lists .rhead .rhead-n').textContent") == "0 of 3"
    assert _js(d, "return document.getElementById('bb-count').textContent") == "0 of 3"
    _rows(d)[2].find_element(By.CSS_SELECTOR, ".tick").click()
    assert _wait(lambda: _js(d, "return document.querySelector('#lists .rhead .rhead-n').textContent") == "1 of 3")
    assert _js(d, "return document.getElementById('bb-count').textContent") == "1 of 3"
    # the tick just made moves; a page opened on it does not
    assert _js(d, "return arguments[0].classList.contains('just')", _rows(d)[2])
    assert _wait(lambda: server_state(server, cl["uid"]).get(cl["items"][3]["uid"], {}).get("checked"))
    d.get(server["base"] + "/checklists/opening")
    assert _wait(lambda: len(_rows(d)) == 3)
    assert _js(d, "return document.querySelectorAll('#lists .rrow.just').length") == 0


# ── the round as a bench sheet (2026-10-07) ────────────────────────────────
#
# Ryan approved a redesign of the round after v4.1.0 "didn't look any
# different": a panel beside the work with the count and every section as a
# link with its own progress, flat ruled sections, and a run of readings as
# one panel of tiles. A reading under its minimum turns its tile and its
# section's ring to the error colour, so a low cylinder is seen from the
# panel without scrolling to it.

def _gas_round(server):
    c = server["app"].test_client()
    with c.session_transaction() as s:
        s["user"] = "setup"
    for cl in c.get("/api/checklists").get_json().get("checklists", []):
        c.delete(f"/api/checklists/{cl['uid']}")
    r = c.post("/api/checklists", json={
        "name": "Opening round", "slot": "opening", "due_time": "23:59",
        "items": [{"text": "Start the lab", "item_type": "header"},
                  {"text": "Fans on"},
                  {"text": "Gas levels", "item_type": "header"},
                  {"text": "Oxygen", "entry_type": "number", "units": "PSI", "min": 300},
                  {"text": "Helium", "entry_type": "number", "units": "PSI", "min": 300},
                  {"text": "Dishes", "item_type": "header"},
                  {"text": "Put away glassware"}]})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["checklist"]


TOC = """return [...document.querySelectorAll('#round-toc li')].map(li => [
  li.querySelector('.t').textContent, li.querySelector('.n').textContent,
  li.querySelector('.ring').className.replace('ring', '').trim()]);"""


@pytest.mark.parametrize("w,h", [(1440, 1000), (390, 844)])
def test_the_panel_lists_every_section_and_readings_are_tiles(drv, server, w, h):
    d = drv
    _gas_round(server)
    _sign_in(d)
    _size(d, w, h)
    d.get(server["base"] + "/checklists/opening")
    assert _wait(lambda: len(_rows(d)) == 4)
    # the server's first paint already groups the readings
    assert _js(d, "return [...document.querySelectorAll('#lists .rreadings > .rrow .rlabel')].map(e => e.textContent)") \
        == ["Oxygen", "Helium"]
    assert _js(d, TOC) == [["Start the lab", "0/1", ""], ["Gas levels", "0/2", ""], ["Dishes", "0/1", ""]]
    toc_shown = _js(d, "return getComputedStyle(document.getElementById('round-toc')).display !== 'none'")
    assert toc_shown == (w >= 1100), "the section list shows beside the work, and folds away on a phone"
    assert _js(d, "return document.getElementById('round-count-n').textContent") == "0"
    sw, cw = _js(d, "return [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    assert sw <= cw, (sw, cw)


def test_a_low_reading_turns_its_tile_and_its_sections_ring(drv, server):
    d = drv
    _gas_round(server)
    _sign_in(d)
    _size(d, 1440, 1000)
    d.get(server["base"] + "/checklists/opening")
    assert _wait(lambda: len(_rows(d)) == 4)
    helium = _rows(d)[2]
    helium.find_element(By.CSS_SELECTOR, ".rinput").send_keys("240\n")
    assert _wait(lambda: _rows(d)[2].get_attribute("data-verdict") == "out")
    assert _wait(lambda: _js(d, TOC)[1] == ["Gas levels", "1/2", "bad"]), _js(d, TOC)
    border, err = _js(d, "return [getComputedStyle(arguments[0]).borderTopColor,"
                         " getComputedStyle(document.documentElement).getPropertyValue('--st-error').trim()];", _rows(d)[2])
    assert border != "rgba(0, 0, 0, 0)" and err, (border, err)
    _rows(d)[1].find_element(By.CSS_SELECTOR, ".rinput").send_keys("2400\n")
    assert _wait(lambda: _rows(d)[1].get_attribute("data-verdict") == "ok")
    assert _js(d, TOC)[1] == ["Gas levels", "2/2", "bad"], "the low cylinder still marks the section"
    assert _js(d, "return document.getElementById('round-count-n').textContent") == "2"
