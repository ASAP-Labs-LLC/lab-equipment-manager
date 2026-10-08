"""The floor map (/?view=map) in a real browser: headless Chrome, real server.

Piece 12's bar has parts a person checks by looking, so they are checked
here the same way, on the demo floor AND on production's shape (16 placed in
a 7 by 5 box on one level, Agilent GC 2 not placed: baseline/prod, 1 Oct):

* **The plan is the box of the placed bays.** Every row and column of the
  grid holds an instrument; outside Arrange there is not one empty dashed
  cell; the plan card ends where the plan does (judge J1: "a half-width grid
  of empty dashed cells, and dead space below").
* **No bay text overflows its bay** at 1440x900 and 820x1180, in both
  themes: not the bay, and not one of its lines (scrollWidth against
  clientWidth on every line, and every line's box inside the bay's). A
  cut line ends in an ellipsis and the bay's title has the whole story.
* **Nothing can be dragged until Arrange is entered** (P15). A real pointer
  drag, signed in, in view mode, sends no request and moves nothing; the
  same drag inside Arrange saves the move, at the floor's 2.05 pitch.
* "Not on the map: … · Place it" puts the instrument on the floor in two
  clicks; ?focus= marks the bay and opens its level; the level seg shows
  only when more than one level holds instruments.

Skipped when selenium or headless Chrome is missing (LEM's own venv has no
selenium); run it with GC hub's venv. The port is ``LEM_UI_MAP_PORT``
(default 5704); a busy port skips.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

webdriver = pytest.importorskip("selenium.webdriver")

import demo_floor  # noqa: E402
from labcore_gateway import FakeLabCoreGateway  # noqa: E402
from levels import LevelStore  # noqa: E402
from web_app import create_app  # noqa: E402

PORT = int(os.environ.get("LEM_UI_MAP_PORT", "5704"))
PASSWORD = "ui-pass"

# production, 2026-10-01 (baseline/prod/lem_api_machines.json): the demo's
# thirteen stand where thirteen of production's seventeen stand
PROD = {
    "Viscocity": (-4.1, 0.0), "Aquamax 1": (-2.05, 0.0), "Aquamax 3": (-2.05, 4.1),
    "PAC Flash 1": (0.0, 0.0), "PAC Flash 2": (0.0, 2.05), "Mini Grabner": (0.0, 4.1),
    "Auto Grabner": (0.0, 6.15), "Eravap": (2.05, -2.05), "Aquamax 2": (2.05, 0.0),
    "OptiMPP 2": (4.1, 0.0), "OptiMPP 1": (4.1, 2.05), "Multitek S": (6.15, 0.0),
    "Multitek NS": (6.15, 2.05), "Agilent GC 1": (6.15, 4.1), "Eraspec": (8.2, 0.0),
}
# demo title -> the production bay it takes (the widest names on the narrowest bays)
PROD_FOR = {
    "Anton Paar DMA 4500": "Viscocity", "Pensky-Martens 1": "Aquamax 1", "Karl Fischer V20": "Aquamax 3",
    "PAC Flash 1": "PAC Flash 1", "PAC Flash 2": "PAC Flash 2", "Koehler K23000": "Eraspec",
    "Cetane Bench": "Auto Grabner", "GC-2": "Eravap", "Multitek S": "Multitek S",
    "OptiMPP 2": "OptiMPP 2", "OptiMPP 1": "OptiMPP 1", "GC-1": "Agilent GC 1",
}
UNPLACED = "Multitek NS"         # production's Agilent GC 2: on the level, not on the map


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from werkzeug.serving import make_server
    root = tmp_path_factory.mktemp("lem-map")
    gw = FakeLabCoreGateway()
    app = create_app(gw, secret="ui-test", admin_password=PASSWORD, documents_root=str(root))
    app.config["SNAPSHOTS"].ensure_schema()
    demo_floor.seed(gw, documents_root=str(root))
    app.config["SNAPSHOTS"].refresh()
    try:
        srv = make_server("127.0.0.1", PORT, app, threaded=True)
    except OSError as exc:
        pytest.skip(f"port {PORT} is busy: {exc}")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    srv.base = f"http://127.0.0.1:{PORT}"
    srv.app, srv.gw = app, gw
    srv.demo_layout = gw.read_sql("SELECT machine_uid, pos_x, pos_y FROM lem_machine_layout")["rows"]
    srv.demo_levels = LevelStore(gw).assignments()
    yield srv
    srv.shutdown()


def _machines(srv):
    return srv.app.config["SNAPSHOTS"].get()["machines"]


def shape(srv, kind):
    """Put the floor in one of two shapes: 'demo' (three levels, as seeded)
    or 'prod' (production's 7 by 5 box on one level, one not placed)."""
    gw = srv.gw
    gw.sql("DELETE FROM lem_machine_layout", [])
    store = LevelStore(gw)
    if kind == "demo":
        for r in srv.demo_layout:
            gw.sql("INSERT INTO lem_machine_layout (machine_uid, pos_x, pos_y) VALUES (?, ?, ?)",
                   [r["machine_uid"], r["pos_x"], r["pos_y"]])
        for uid, lv in srv.demo_levels.items():
            store.assign(uid, lv, by="test")
    else:
        ground = sorted(store.levels(), key=lambda lv: lv.rank)[0].uid
        for m in _machines(srv):
            store.assign(m["machine_uid"], ground, by="test")
            bay = PROD_FOR.get(m["title"])
            if bay:
                x, y = PROD[bay]
                gw.sql("INSERT INTO lem_machine_layout (machine_uid, pos_x, pos_y) VALUES (?, ?, ?)",
                       [m["machine_uid"], x, y])
    srv.app.config["SNAPSHOTS"].refresh()


@pytest.fixture(scope="module")
def drv(server):
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    for arg in ("--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars"):
        opts.add_argument(arg)
    opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    try:
        d = webdriver.Chrome(options=opts)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"headless Chrome unavailable: {exc}")
    yield d
    d.quit()


def _js(d, src, *args):
    return d.execute_script(src, *args)


def _wait(fn, timeout=6.0):
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


def _open(d, base, path="/?view=map", size=(1440, 900), theme="light", signed_in=False):
    d.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
        "width": size[0], "height": size[1], "deviceScaleFactor": 1, "mobile": size[0] < 700})
    d.get(base + "/help")
    d.execute_script("localStorage.setItem('lem.theme', arguments[0])", theme)
    want = "ok" if signed_in else "out"
    if signed_in:
        assert _js(d, "return fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},"
                      "body:JSON.stringify({username:'Cody',password:arguments[0]})}).then(r=>r.status);",
                   PASSWORD) == 200
    else:
        _js(d, "return fetch('/api/logout',{method:'POST'}).then(r=>r.status);")
    d.get(base + path)
    assert _wait(lambda: _js(d, "return document.querySelectorAll('#plan .bay').length")), "the plan never drew"
    assert _js(d, "return document.body.dataset.user") == ("Cody" if want == "ok" else "")
    time.sleep(0.15)


# what every line of every bay measures, and the plan's box
MEASURE = """
const out = {bays: [], cols: 0, rows: 0, cells: 0, dashed: 0};
const plan = document.getElementById('plan');
const cs = getComputedStyle(plan);
out.cols = cs.gridTemplateColumns.split(' ').length;
out.rows = cs.gridTemplateRows.split(' ').length;
out.cells = plan.querySelectorAll('.cell').length;
out.xs = [...new Set([...plan.querySelectorAll('.bay')].map(b => +b.dataset.x))].sort((a,b)=>a-b);
out.ys = [...new Set([...plan.querySelectorAll('.bay')].map(b => +b.dataset.y))].sort((a,b)=>a-b);
for (const b of plan.querySelectorAll('.bay')) {
  const br = b.getBoundingClientRect();
  const lines = [];
  for (const el of b.querySelectorAll('.b-name, .b-wtext, .b-detail')) {
    if (el.hidden) continue;     // a detail that stepped aside (the title keeps it) cannot overflow
    const r = el.getBoundingClientRect();
    lines.push({cls: el.className, text: el.textContent, sw: el.scrollWidth, cw: el.clientWidth,
                sh: el.scrollHeight, ch: el.clientHeight,
                inside: r.left >= br.left - 0.5 && r.right <= br.right + 0.5 && r.top >= br.top - 0.5 && r.bottom <= br.bottom + 0.5});
  }
  out.bays.push({uid: b.dataset.uid, title: b.title, sw: b.scrollWidth, cw: b.clientWidth,
                 sh: b.scrollHeight, ch: b.clientHeight, w: br.width, h: br.height, lines});
}
const card = document.getElementById('plan-card').getBoundingClientRect();
const pr = plan.getBoundingClientRect();
const last = Math.max(...[...plan.querySelectorAll('.bay')].map(b => b.getBoundingClientRect().bottom));
out.planBottomGap = pr.bottom - last;
out.cardBottom = card.bottom; out.planBottom = pr.bottom; out.vh = innerHeight;
out.pageScrollX = document.documentElement.scrollWidth - innerWidth;
return out;
"""


def _overflows(m):
    bad = []
    for b in m["bays"]:
        if b["sw"] > b["cw"] or b["sh"] > b["ch"] + 1:
            bad.append((b["uid"], "bay", b))
        for ln in b["lines"]:
            if ln["sw"] > ln["cw"] + 0.5 or not ln["inside"] or ("b-name" in ln["cls"] and ln["sh"] > ln["ch"] + 1):
                bad.append((b["uid"], ln["cls"], ln))
    return bad


@pytest.mark.parametrize("kind", ["demo", "prod"])
@pytest.mark.parametrize("size", [(1440, 900), (820, 1180)])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_no_bay_text_overflows_its_bay(drv, server, kind, size, theme):
    shape(server, kind)
    levels = [""] if kind == "prod" else [lv.uid for lv in LevelStore(server.gw).levels()]
    for lv in levels:
        _open(drv, server.base, "/?view=map" + ("&level=" + lv if lv else ""), size, theme)
        m = _js(drv, MEASURE)
        assert m["bays"], "nothing drawn"
        assert not _overflows(m), _overflows(m)
        assert m["pageScrollX"] <= 0, "the page scrolls sideways"
        # a cut line still has the whole story in the bay's title
        for b in m["bays"]:
            for ln in b["lines"]:
                if ln["text"].endswith("…") and "b-wtext" not in ln["cls"]:
                    assert ln["text"][:-1].rstrip() in b["title"], (ln, b["title"])


@pytest.mark.parametrize("kind", ["demo", "prod"])
@pytest.mark.parametrize("size", [(1440, 900), (820, 1180)])
def test_the_plan_is_the_box_of_the_placed_bays(drv, server, kind, size):
    """J1: no empty edge row or column, no dashed cells outside Arrange, and
    nothing below the last row of bays but the plan's own 12px padding."""
    shape(server, kind)
    _open(drv, server.base, size=size)
    m = _js(drv, MEASURE)
    assert m["xs"] == list(range(m["cols"])), m
    assert m["ys"] == list(range(m["rows"])), m
    assert m["cells"] == 0
    assert m["planBottomGap"] <= 12.5, m["planBottomGap"]        # the plan's own padding, no more
    # the card ends with the plan (and its "Not on the map" line), never in empty floor
    assert m["cardBottom"] - m["planBottom"] <= 24 + 60, (m["cardBottom"], m["planBottom"])


def test_production_fills_the_desk_screen_without_scrolling(drv, server):
    """At 1440x900, production's 7 by 5 floor and its Not-on-the-map line
    are whole on the first screen."""
    shape(server, "prod")
    _open(drv, server.base)
    m = _js(drv, MEASURE)
    assert (m["cols"], m["rows"]) == (7, 5)
    assert m["cardBottom"] <= m["vh"], m


def test_the_level_seg_shows_only_with_more_than_one_level(drv, server):
    shape(server, "prod")
    _open(drv, server.base)
    assert _js(drv, "return document.getElementById('level-seg').hidden") is True
    assert _js(drv, "return document.getElementById('plan-title').textContent") == "Ground Floor"
    shape(server, "demo")
    _open(drv, server.base)
    assert _js(drv, "return document.getElementById('level-seg').hidden") is False
    assert _js(drv, "return [...document.querySelectorAll('#level-seg a .lv-name')].map(a=>a.textContent)") == \
        ["Ground Floor", "Mezzanine", "Upper Lab"]


def _layout(srv):
    rows = srv.gw.read_sql("SELECT machine_uid, pos_x, pos_y FROM lem_machine_layout")["rows"]
    return {r["machine_uid"]: (r["pos_x"], r["pos_y"]) for r in rows}


COUNT_POSTS = """
window.__posts = [];
const f = window.fetch;
window.fetch = function (u, o) {
  if (o && o.method === 'POST') window.__posts.push(String(u));
  return f.apply(this, arguments);
};
"""


# the screen centre of a free bay inside the plan's box (view mode draws none)
FREE_SPOT = """
const l = LEMFloorMap.layout(), plan = document.getElementById('plan');
const r = plan.getBoundingClientRect(), cs = getComputedStyle(plan);
const gap = parseFloat(cs.columnGap), pad = parseFloat(cs.paddingLeft);
const cw = (r.width - 2 * pad - gap * (l.w - 1)) / l.w, ch = (r.height - 2 * pad - gap * (l.h - 1)) / l.h;
for (let y = 0; y < l.h; y++) for (let x = 0; x < l.w; x++) {
  if (LEMPlan.isFree(l, x, y)) return {x, y, cx: r.left + pad + x * (cw + gap) + cw / 2, cy: r.top + pad + y * (ch + gap) + ch / 2};
}
return null;
"""


def _drag(d, el, dx, dy):
    from selenium.webdriver.common.action_chains import ActionChains
    ActionChains(d).move_to_element(el).click_and_hold().move_by_offset(10, 10) \
        .move_by_offset(dx - 10, dy - 10).release().perform()


def test_drag_is_impossible_until_arrange_is_entered(drv, server):
    """P15. Signed in, viewing: a real drag of a bay moves nothing and sends
    nothing. Inside Arrange the same gesture saves the move."""
    from selenium.webdriver.common.by import By
    shape(server, "prod")                     # a 7 by 5 floor with free bays to drop on
    _open(drv, server.base, signed_in=True)
    before = _layout(server)
    _js(drv, COUNT_POSTS)
    bay = drv.find_element(By.CSS_SELECTOR, "#plan .bay")
    uid = bay.get_attribute("data-uid")
    assert bay.get_attribute("draggable") == "false"
    # onto the centre of a FREE bay of the plan: the one drop that would move it
    free = _js(drv, FREE_SPOT)
    assert free, "the test needs a free bay inside the plan"
    br = bay.rect
    _drag(drv, bay, free["cx"] - (br["x"] + br["width"] / 2), free["cy"] - (br["y"] + br["height"] / 2))
    time.sleep(0.6)
    assert _js(drv, "return window.__posts") == []
    assert _layout(server) == before
    assert "view=map" in drv.current_url, "a drag must not navigate either"
    assert _js(drv, "return document.querySelectorAll('#plan .cell, #plan .bay.dragging').length") == 0

    drv.find_element(By.ID, "arrange").click()
    assert _wait(lambda: not _js(drv, "return document.getElementById('arrange-bar').hidden"))
    assert _js(drv, "return document.querySelectorAll('#plan .cell').length") > 0
    bay = drv.find_element(By.CSS_SELECTOR, '#plan .bay[data-uid="%s"]' % uid)
    cell = drv.find_element(By.CSS_SELECTOR, "#plan .cell")
    cx, cy = int(cell.get_attribute("data-x")), int(cell.get_attribute("data-y"))
    want = _js(drv, "const l = LEMFloorMap.layout(); return LEMPlan.toSaved(arguments[0], arguments[1], l.origin);", cx, cy)
    br, cr = bay.rect, cell.rect
    _drag(drv, bay, (cr["x"] + cr["width"] / 2) - (br["x"] + br["width"] / 2),
          (cr["y"] + cr["height"] / 2) - (br["y"] + br["height"] / 2))
    assert _wait(lambda: uid in _layout(server) and _layout(server)[uid] != before[uid]), "the move was not saved"
    x, y = _layout(server)[uid]
    assert (x, y) == (want["x"], want["y"]), "saved somewhere other than the bay it was dropped on"
    for v in (x, y):
        assert abs(v / 2.05 - round(v / 2.05)) < 1e-6, (x, y)
    assert _js(drv, "return window.__posts.filter(u => u.indexOf('/position') >= 0).length") == 1
    assert _wait(lambda: _js(drv, "return !document.querySelector('#plan .bay.saving')"))

    drv.find_element(By.ID, "arrange-done").click()
    assert _wait(lambda: _js(drv, "return document.getElementById('arrange-bar').hidden"))
    assert _js(drv, "return document.querySelectorAll('#plan .cell').length") == 0
    assert _js(drv, "return [...document.querySelectorAll('#plan .bay')].every(b => b.tagName === 'A')")
    assert "arrange" not in drv.current_url


def test_signed_out_arrange_asks_to_sign_in_titled_for_the_act(drv, server):
    from selenium.webdriver.common.by import By
    shape(server, "demo")
    _open(drv, server.base)
    drv.find_element(By.ID, "arrange").click()
    assert _wait(lambda: _js(drv, "const s=document.getElementById('signin-sheet');return !!(s&&s.open);"))
    assert "arrange the floor" in _js(drv, "return document.getElementById('signin-sheet').textContent")
    assert _js(drv, "return document.getElementById('arrange-bar').hidden") is True


def test_a_frozen_floor_says_so_and_nothing_moves(drv, server):
    """The lab-wide freeze (/api/map) still holds: Arrange says the floor is
    frozen and offers to unfreeze; it does not pretend to be arranging."""
    from selenium.webdriver.common.by import By
    shape(server, "demo")
    _open(drv, server.base, signed_in=True)
    assert _js(drv, "return fetch('/api/map',{method:'POST',headers:{'Content-Type':'application/json'},"
                    "body:JSON.stringify({locked:true})}).then(r=>r.status);") == 200
    try:
        drv.find_element(By.ID, "arrange").click()
        assert _wait(lambda: not _js(drv, "return document.getElementById('arrange-bar').hidden"))
        assert "frozen" in _js(drv, "return document.getElementById('arrange-bar').textContent")
        assert _js(drv, "return document.querySelectorAll('#plan .cell').length") == 0
        drv.find_element(By.ID, "arrange-retry").click()          # Unfreeze and arrange
        assert _wait(lambda: _js(drv, "return document.querySelectorAll('#plan .cell').length") > 0)
    finally:
        _js(drv, "return fetch('/api/map',{method:'POST',headers:{'Content-Type':'application/json'},"
                 "body:JSON.stringify({locked:false})}).then(r=>r.status);")


def test_not_on_the_map_place_it(drv, server):
    """'Not on the map: Multitek NS · Place it': Place it opens Arrange with
    it picked; one click on an empty bay puts it there."""
    from selenium.webdriver.common.by import By
    shape(server, "prod")
    _open(drv, server.base, signed_in=True)
    foot = _js(drv, "return document.getElementById('plan-unplaced').textContent")
    assert foot.startswith("Not on the map:") and UNPLACED in foot and "Place it" in foot
    drv.find_element(By.CSS_SELECTOR, "[data-testid=place-it]").click()
    assert _wait(lambda: _js(drv, "return document.querySelector('.pu-pick.picked')?.textContent") == UNPLACED)
    drv.find_element(By.CSS_SELECTOR, "#plan .cell").click()
    uid = next(m["machine_uid"] for m in _machines(server) if m["title"] == UNPLACED)
    assert _wait(lambda: uid in _layout(server)), "Place it did not save"
    assert _wait(lambda: _js(drv, "return !!document.querySelector('#plan .bay[data-uid=\"%s\"]')" % uid))
    drv.find_element(By.ID, "arrange-done").click()


def test_focus_marks_the_bay_and_opens_its_level(drv, server):
    shape(server, "demo")
    m = next(m for m in _machines(server) if m["title"] == "GC-2")         # on Upper Lab
    _open(drv, server.base, "/?view=map&focus=" + m["machine_uid"])
    assert _js(drv, "return document.querySelector('#level-seg a[aria-current=page] .lv-name').textContent") == "Upper Lab"
    assert _js(drv, "return document.querySelector('#plan .bay.focus').dataset.uid") == m["machine_uid"]


def test_a_cause_steps_the_other_bays_back(drv, server):
    from selenium.webdriver.common.by import By
    shape(server, "demo")
    _open(drv, server.base)
    tile = drv.find_element(By.CSS_SELECTOR, '[data-testid=needs-tile][data-key="not_ok-qc"]')
    tile.click()
    assert _wait(lambda: "cause=not_ok-qc" in drv.current_url)
    assert _js(drv, "return document.querySelectorAll('#plan .bay:not(.dim)').length") >= 1
    assert _js(drv, "return [...document.querySelectorAll('#plan .bay:not(.dim)')]"
                    ".every(b => /Stop/.test(b.title))")
    assert _js(drv, "return document.querySelector('[data-key=\"not_ok-qc\"]').classList.contains('current')")


def test_a_bay_is_a_link_to_its_record(drv, server):
    shape(server, "demo")
    _open(drv, server.base)
    hrefs = _js(drv, "return [...document.querySelectorAll('#plan .bay')].map(b => b.getAttribute('href'))")
    assert hrefs and all(h and h != "#" for h in hrefs)


def test_no_console_errors(drv, server):
    shape(server, "demo")
    drv.get_log("browser")
    _open(drv, server.base)
    errs = [e for e in drv.get_log("browser") if e.get("level") == "SEVERE"
            and "favicon" not in e.get("message", "")]
    assert not errs, errs


# ── the map's levels are pages too (2026-10-07) ────────────────────────────
#
# v4.1.0 made the wall's levels swipeable pages with tabs marked by the worst
# state on each level. Ryan: "the map didn't get updated either". The map's
# level tabs now say the same (its worst state's glyph and how many there
# need you), a swipe or an arrow key turns the level, and nothing on a bay
# is cut to "…": a verdict is the app's whole word, a detail wraps to two
# lines and, if it still does not fit, is shortened by meaning ("Calibration
# overdue", not "Calibration…") or steps aside for the bay's title.

STATE_RANK = {"ok": 0, "off_line": 1, "no_qc": 2, "cant_tell": 2, "ok_but": 3, "not_ok": 4}

TABS = """
return [...document.querySelectorAll('#level-seg a')].map(a => ({
  uid: a.dataset.level, state: a.dataset.state || null,
  need: (a.querySelector('.lv-n') || {}).textContent || '',
  name: (a.querySelector('.lv-name') || a).textContent,
  current: a.getAttribute('aria-current') === 'page'}));
"""


def _marks(srv):
    import json
    import urllib.request
    d = json.load(urllib.request.urlopen(srv.base + "/api/ui/instruments"))
    known = {lv["uid"] for lv in d["levels"]}
    fallback = d["default_level"] if d["default_level"] in known else d["levels"][0]["uid"]
    out = {}
    for r in d["instruments"]:
        lv = r.get("level_uid") if r.get("level_uid") in known else fallback
        st = r["readiness"]["state"]
        m = out.setdefault(lv, {"state": st, "need": 0})
        if STATE_RANK[st] > STATE_RANK[m["state"]]:
            m["state"] = st
        m["need"] += 1 if r.get("needs_you") else 0
    return d["levels"], out


def test_level_tabs_carry_the_worst_state_and_how_many_need_you(drv, server):
    shape(server, "demo")
    levels, marks = _marks(server)
    _open(drv, server.base)
    tabs = _js(drv, TABS)
    assert [t["name"] for t in tabs] == [lv["name"] for lv in levels]
    assert [t["state"] for t in tabs] == [marks[lv["uid"]]["state"] for lv in levels]
    assert [t["need"] for t in tabs] == [str(marks[lv["uid"]]["need"]) if marks[lv["uid"]]["need"] else ""
                                         for lv in levels]


SWIPE = r"""
const el = document.getElementById('plan');
const b = el.querySelector('.bay').getBoundingClientRect();
const y = b.top + b.height / 2, x0 = b.left + b.width / 2, x1 = x0 + arguments[0];
const at = (type, x) => el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true,
  clientX: x, clientY: y, pointerId: 9, pointerType: 'touch', isPrimary: true, button: 0 }));
at('pointerdown', x0); at('pointermove', (x0 + x1) / 2); at('pointerup', x1);
const under = document.elementFromPoint(x1, y);
if (under) under.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, clientX: x1, clientY: y }));
"""


def _current(d):
    return _js(d, "const a = document.querySelector('#level-seg a[aria-current=\"page\"]'); return a && a.dataset.level")


def test_a_swipe_or_an_arrow_key_turns_the_level_and_opens_nothing(drv, server):
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.keys import Keys
    shape(server, "demo")
    levels, _ = _marks(server)
    uids = [lv["uid"] for lv in levels]
    _open(drv, server.base)
    assert _current(drv) == uids[0]
    ActionChains(drv).send_keys(Keys.ARROW_RIGHT).perform()
    assert _wait(lambda: _current(drv) == uids[1])
    assert "level=" + uids[1] in drv.current_url
    ActionChains(drv).send_keys(Keys.ARROW_LEFT).perform()
    assert _wait(lambda: _current(drv) == uids[0])
    _js(drv, SWIPE, -200)                       # finger right-to-left: the next level
    assert _wait(lambda: _current(drv) == uids[1])
    _js(drv, SWIPE, 200)
    assert _wait(lambda: _current(drv) == uids[0])
    time.sleep(0.3)
    assert "/instruments/" not in drv.current_url, "the swipe's end opened the bay under the finger"


def test_arrange_never_swipes(drv, server):
    """Dragging a bay sideways in Arrange is a move, never a level turn."""
    shape(server, "demo")
    levels, _ = _marks(server)
    _open(drv, server.base, signed_in=True)
    drv.find_element("id", "arrange").click()
    assert _wait(lambda: _js(drv, "return document.getElementById('floor-map').classList.contains('is-arranging')"))
    _js(drv, COUNT_POSTS)
    _js(drv, SWIPE, -200)
    time.sleep(0.4)
    assert _current(drv) == levels[0]["uid"]
    drv.find_element("id", "arrange-done").click()


CUT = """
return [...document.querySelectorAll('#plan .bay')].flatMap(b => [...b.querySelectorAll('.b-wtext, .b-detail')]
  .filter(e => !e.hidden && e.textContent.endsWith('…') && !/but…$/.test(e.textContent))
  .map(e => b.dataset.uid + ': ' + e.textContent));
"""


@pytest.mark.parametrize("kind", ["demo", "prod"])
@pytest.mark.parametrize("size", [(1440, 1000), (820, 1180), (390, 844)])
def test_no_word_on_the_map_is_cut(drv, server, kind, size):
    shape(server, kind)
    levels = [""] if kind == "prod" else [lv.uid for lv in LevelStore(server.gw).levels()]
    for lv in levels:
        _open(drv, server.base, "/?view=map" + ("&level=" + lv if lv else ""), size, "dark")
        assert _js(drv, CUT) == [], (size, lv)
        m = _js(drv, MEASURE)
        assert not _overflows(m), _overflows(m)
        assert m["pageScrollX"] <= 0


def test_on_a_phone_arrange_sits_on_the_tab_row(drv, server):
    shape(server, "demo")
    _open(drv, server.base, size=(390, 844), signed_in=True)
    seg, arr = _js(drv, "return [document.getElementById('level-seg').getBoundingClientRect().top,"
                        " document.getElementById('arrange').getBoundingClientRect().top]")
    assert abs(seg - arr) < 12, (seg, arr)
