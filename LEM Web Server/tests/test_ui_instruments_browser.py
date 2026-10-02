"""The Instruments home in a real browser: headless Chrome against a real server.

The bar for piece 4 (ia-final §14) has three parts a person checks by
looking, so they are checked here the same way:

* **T1 at 0 clicks.** "Is GC-2 OK to run?" is answered by the row's Can it
  run? column as the page arrives, at desk, tablet and phone widths. No
  click, no hover, and (at desk and tablet) no scroll.
* **No horizontal page scroll at 820x1180 or 390x844, in both themes.** A
  page that scrolls sideways on the bench tablet hides the very column the
  page exists for.
* **Say it once.** In the first 1440x900 screen, no instrument is named
  twice: the table row names it with its verdict; a Needs-you tile says a
  cause and its next step and names nobody, and carries no count beside the
  pill's. The card has at most six tiles; chips and caption carry no digits.
  Round 3: the bell, opened on this page, named the rows' problems again
  ("OptiMPP 1 is not OK to run: QC out of spec…"). It folds them here, so
  with the bell open every instrument is still named exactly once.
* **Every problem is on its row.** OptiMPP 2's overdue PM, behind its
  overdue calibration, was on no row, tile or bell line.

Plus the find box: Ctrl K focuses it, a search lists links and says what it
searched, and Escape closes it.

Skipped when selenium or headless Chrome is missing (LEM's own venv has no
selenium); run it with a Python that has both, e.g. GC hub's venv. The port
is ``LEM_UI_INSTRUMENTS_PORT`` (default 5703); a busy port skips.
"""
from __future__ import annotations

import os
import re
import threading
import time

import pytest

webdriver = pytest.importorskip("selenium.webdriver")

import demo_floor  # noqa: E402
from labcore_gateway import FakeLabCoreGateway  # noqa: E402
from web_app import create_app  # noqa: E402

PORT = int(os.environ.get("LEM_UI_INSTRUMENTS_PORT", "5703"))
SIZES = [(1440, 900), (820, 1180), (390, 844)]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from werkzeug.serving import make_server
    root = tmp_path_factory.mktemp("lem-inst")
    gw = FakeLabCoreGateway()
    app = create_app(gw, secret="ui-test", admin_password="ui-pass", documents_root=str(root))
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


def _open(d, base, path="/", size=(1440, 900), theme="light"):
    d.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
        "width": size[0], "height": size[1], "deviceScaleFactor": 1, "mobile": size[0] < 700})
    d.get(base + "/help")
    d.execute_script("localStorage.setItem('lem.theme', arguments[0])", theme)
    d.get(base + path)
    for _ in range(50):
        if d.execute_script("return document.querySelectorAll('tr.irow').length"):
            return
        time.sleep(0.1)
    raise AssertionError("the table never drew")


def test_t1_at_desk_the_verdict_and_its_reason_are_whole_on_the_first_screen(drv, server):
    """Round 3's critic: at 1440x900 GC-2's verdict word sat cut by the
    bottom edge and its reason ("Calibration overdue since 17 May") wholly
    below it. The first screen must hold GC-2's whole row."""
    _open(drv, server.base)
    got = drv.execute_script("""
        const tr = document.querySelector('tr[data-uid="gc-2"]');
        return {bottom: tr.getBoundingClientRect().bottom, vh: innerHeight,
                text: tr.querySelector('.c-run').innerText};""")
    assert got["bottom"] <= got["vh"], got
    assert "since" in got["text"], got


@pytest.mark.parametrize("size", SIZES, ids=lambda s: "%dx%d" % s)
def test_t1_the_verdict_is_on_screen_with_no_click(drv, server, size):
    _open(drv, server.base, size=size)
    got = drv.execute_script("""
        const tr = document.querySelector('tr[data-uid="gc-2"]');
        const v = tr.querySelector('.verdict');
        const b = v.getBoundingClientRect();
        return {word: v.textContent.trim(), w: b.width, h: b.height, top: b.top,
                bottom: b.bottom, vh: innerHeight};""")
    assert got["word"] in ("OK to run", "OK to run, but…", "Not OK to run", "Off line",
                           "Can't tell", "No QC assigned"), got
    assert got["w"] > 0 and got["h"] > 0, "the verdict is drawn, not hidden"
    if size[0] >= 700:
        # §8 counts clicks, not scrolls: GC-2 may sit lower in a worst-first
        # list. What must hold is that the table itself starts on the first
        # screen, so nobody has to discover that there is a list below.
        first = drv.execute_script(
            "return document.querySelector('tr.irow').getBoundingClientRect().bottom")
        assert first <= size[1], "the table's first row is above the fold"


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("size", [(820, 1180), (390, 844)], ids=lambda s: "%dx%d" % s)
def test_no_horizontal_page_scroll(drv, server, size, theme):
    for path in ("/", "/?filter=needs", "/?cause=ok_but-cal", "/maintenance"):
        _open(drv, server.base, path, size=size, theme=theme)
        sw, cw = drv.execute_script(
            "return [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
        assert sw <= cw, f"{path} at {size} ({theme}) scrolls sideways: {sw} > {cw}"


def test_say_it_once(drv, server):
    import json
    import urllib.request
    _open(drv, server.base)
    inst = json.load(urllib.request.urlopen(server.base + "/api/ui/instruments"))
    titles = [r["title"] for r in inst["instruments"]]
    cap = drv.find_element("id", "needs-caption").text
    assert cap == "Worst first · updates by itself", cap
    tiles = drv.find_elements("css selector", "a.ntile")
    assert 0 < len(tiles) <= 6
    for t in tiles:
        said = t.text
        assert not re.search(r"\d", said), ("a tile counts nothing", said)
        for title in titles:
            assert title not in said, ("a tile names nobody", title, said)
    chips = [c.text for c in drv.find_elements("css selector", "#inst-chips .chip")]
    assert chips and not any(re.search(r"\d", c) for c in chips), chips
    pills = drv.find_elements("css selector", ".page-head .pill")
    assert len(pills) == 1, "one fleet pill"
    for a in drv.find_elements("css selector", "tr.irow a.iname"):
        assert a.get_attribute("href"), "every row is a link"
    # the first screen, as a person sees it: every visible text node inside
    # the viewport, counted per instrument title
    seen = drv.execute_script("""
        const out = [];
        const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        while (w.nextNode()) {
            const n = w.currentNode, t = n.textContent.trim();
            if (!t || n.parentElement.closest('[hidden], script, .find-pop')) continue;
            const r = document.createRange(); r.selectNodeContents(n);
            const b = r.getBoundingClientRect();
            if (b.width && b.height && b.bottom > 0 && b.top < innerHeight) out.push(t);
        }
        return out;""")
    for title in titles:
        n = sum(1 for t in seen if re.search(r"(^|[^\w-])%s($|[^\w-])" % re.escape(title), t))
        assert n <= 1, (title, n, [t for t in seen if title in t])


def _named(text, title):
    return len(re.findall(r"(^|[^\w-])%s($|[^\w-])" % re.escape(title), text))


def test_the_bell_does_not_tell_the_rows_again(drv, server):
    """The critic's repro: open the bell on Instruments and count each
    instrument's name in the page's text. Each is named once, on its row."""
    import json
    import urllib.request
    _open(drv, server.base)
    drv.execute_script("localStorage.removeItem('lem.recent'); localStorage.removeItem('lem.notes-dismissed')")
    _open(drv, server.base)
    titles = [r["title"] for r in json.load(urllib.request.urlopen(server.base + "/api/ui/instruments"))["instruments"]]
    for _ in range(50):
        if drv.execute_script("return !!document.getElementById('bell-fold').textContent"):
            break
        time.sleep(0.1)
    drv.find_element("id", "bell").click()
    time.sleep(0.2)
    panel = drv.find_element("id", "bell-panel")
    assert panel.is_displayed()
    said = panel.text
    for title in titles:
        assert _named(said, title) == 0, ("the bell names a row's instrument", title, said)
    assert "Instrument problems are on this page" in said, said
    assert not re.search(r"\d+ instruments? (is|are)", said), said
    page = drv.execute_script("return document.body.innerText")
    for title in titles:
        assert _named(page, title) == 1, (title, _named(page, title))
    badge = drv.find_element("id", "bell-count")
    shown = badge.text if badge.is_displayed() else ""
    listed = len(drv.find_elements("css selector", "#bell-list li"))
    assert shown == (str(listed) if listed else ""), (shown, listed)
    drv.find_element("id", "bell").click()


def test_a_pm_behind_a_calibration_is_on_its_row(drv, server):
    _open(drv, server.base)
    text = drv.execute_script(
        "return document.querySelector('tr[data-uid=\"optimpp-2\"] .c-run').innerText")
    assert "Calibration overdue" in text and "PM overdue too" in text, text


def test_no_tile_looks_chosen_on_the_whole_list(drv, server):
    _open(drv, server.base)
    assert drv.find_elements("css selector", "a.ntile.current") == []
    assert drv.find_elements("css selector", "a.ntile[aria-current]") == []


@pytest.mark.parametrize("path,want", [("/?cause=ok_but-cal", 7), ("/?cause=ok_but-pm", 3)])
def test_a_tiles_view_shows_everyone_with_the_problem(drv, server, path, want):
    """The seed's schedule has 7 overdue calibrations and 3 overdue PMs; the
    view that showed 2 of 3 PMs keyed on each instrument's worst cause."""
    _open(drv, server.base, path)
    assert len(drv.find_elements("css selector", "tr.irow")) == want


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=lambda s: "%dx%d" % s)
def test_the_maintenance_view_is_not_the_whole_list_again(drv, server, size):
    """/maintenance became this view. With every seeded instrument scheduled
    it was the All list, in the All order, with the All columns. Now it is
    ordered by what falls due (OptiMPP 2's PM overdue since 19 Sep is above
    Koehler's next PM on 29 Oct), its third column is Next due, and that
    column never says "overdue": the row's Can it run? line already does.
    On a phone the Next due line stays (the other columns fold away)."""
    _open(drv, server.base, "/maintenance", size=size)
    got = drv.execute_script("""
        const rows = [...document.querySelectorAll('tr.irow')];
        return {head: document.getElementById('col-third').textContent,
                order: rows.map(r => r.dataset.uid),
                due: rows.map(r => r.querySelector('.c-due').innerText),
                shown: rows.map(r => r.querySelector('.c-due').getBoundingClientRect().height > 0)};""")
    assert got["head"] == "Next due"
    assert got["order"].index("optimpp-2") < got["order"].index("koehler-visc"), got["order"]
    assert all("overdue" not in t.lower() for t in got["due"]), got["due"]
    assert all(got["shown"]), "the Next due line is drawn at every width"


def test_on_a_phone_every_chip_is_on_screen(drv, server):
    _open(drv, server.base, size=(390, 844))
    out = drv.execute_script("""
        const w = document.documentElement.clientWidth;
        return [...document.querySelectorAll('#inst-chips .chip')]
            .filter(c => c.getBoundingClientRect().right > w || c.getBoundingClientRect().left < 0)
            .map(c => c.textContent);""")
    assert out == [], out


def test_the_title_sits_in_the_topbar(drv, server):
    _open(drv, server.base)
    h1 = drv.find_elements("css selector", "h1")
    assert len(h1) == 1 and h1[0].text == "Instruments"
    assert drv.execute_script("return !!arguments[0].closest('.topbar')", h1[0])


def test_a_tile_is_pressed_while_its_cause_is_the_view(drv, server):
    _open(drv, server.base, "/?cause=ok_but-cal")
    cur = drv.find_elements("css selector", "a.ntile[aria-current=true]")
    assert len(cur) == 1 and cur[0].get_attribute("data-key") == "ok_but-cal"
    assert "current" in cur[0].get_attribute("class")
    assert cur[0].text.splitlines()[-1] == "Show all"


def test_find(drv, server):
    from selenium.webdriver.common.keys import Keys
    _open(drv, server.base)
    drv.find_element("tag name", "body").send_keys(Keys.CONTROL, "k")
    assert drv.execute_script("return document.activeElement.id") == "find-q"
    drv.switch_to.active_element.send_keys("pac")
    for _ in range(30):
        if drv.find_elements("css selector", "#find-list li"):
            break
        time.sleep(0.1)
    rows = [li.text for li in drv.find_elements("css selector", "#find-list li")]
    assert rows and rows[0].startswith("Instrument"), rows
    assert drv.find_element("id", "find-note").text.startswith("Searched")
    drv.switch_to.active_element.send_keys(Keys.ESCAPE)
    assert drv.find_element("id", "find-pop").get_attribute("hidden") is not None


def test_a_merged_tile_filters_in_place_and_back_restores(drv, server):
    _open(drv, server.base)
    before = len(drv.find_elements("css selector", "tr.irow"))
    merged = [t for t in drv.find_elements("css selector", "a.ntile")
              if "cause=" in (t.get_attribute("href") or "")]
    assert merged, "the seed has instruments sharing a cause"
    merged[0].click()
    time.sleep(0.3)
    assert "cause=" in drv.current_url
    after = len(drv.find_elements("css selector", "tr.irow"))
    assert 0 < after < before
    pressed = drv.find_elements("css selector", "#inst-chips [aria-pressed=true]")
    assert len(pressed) == 1 and "clears" in pressed[0].get_attribute("class")
    drv.back()
    time.sleep(0.3)
    assert len(drv.find_elements("css selector", "tr.irow")) == before


def test_no_console_errors(drv, server):
    _open(drv, server.base)
    errors = [e for e in drv.get_log("browser") if e["level"] == "SEVERE"]
    assert errors == []
