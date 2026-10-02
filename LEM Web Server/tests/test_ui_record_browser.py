"""The instrument record (/instruments/<uid>) in a real browser.

The parts of the record a person checks by looking are checked here the same
way, in headless Chrome against a real server on the dev seed:

* **A refresh that fails is said, never swallowed.** Round 3's critic made
  the record's own refresh answer 503 six times in a row and the page kept
  the old verdict with no mark at all: a failed read presented as an answer,
  the one thing ASK-CLAUDE.md forbids. Now the card keeps what it last read,
  dimmed, under a line that says it could not refresh, why, and as of when,
  and "Try again" brings it back the moment LEM answers.
* **The QC intro says how long a pass counts** ("A passing check counts for
  24 h", §3.1), the sentence that explains why a pass from August reads QC
  due today.
* **A check with no runs on file offers no history control**: "24 runs ·
  90 days · All" over an empty plot was a control that does nothing.

Skipped when selenium or headless Chrome is missing (LEM's own venv has no
selenium); run it with GC hub's venv. The port is ``LEM_UI_RECORD_PORT``
(default 5700); a busy port skips.
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

PORT = int(os.environ.get("LEM_UI_RECORD_PORT", "5700"))


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from werkzeug.serving import make_server
    root = tmp_path_factory.mktemp("lem-record")
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
    srv.app = app
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


def _rows(srv):
    with srv.app.test_client() as c:
        return c.get("/api/ui/instruments").get_json()["instruments"]


def _open(d, srv, uid):
    d.get(srv.base + "/instruments/" + uid)
    assert _wait(lambda: d.execute_script(
        "return document.getElementById('ready-cap').textContent.length > 0")), "the card never drew"


def _visible(d, sel):
    return d.execute_script(
        "const e = document.querySelector(arguments[0]);"
        "return !!e && !e.hidden && getComputedStyle(e).display !== 'none' && e.getClientRects().length > 0;", sel)


# Answer every refresh of the record with 503, as LEM does when it could not
# read LabCore; everything else passes through. Restored by FAIL_OFF.
FAIL_ON = """
window.__realFetch = window.__realFetch || window.fetch;
window.fetch = function (url, o) {
  if (String(url).indexOf('/api/ui/instruments/') >= 0) {
    window.__fails = (window.__fails || 0) + 1;
    return Promise.resolve(new Response(JSON.stringify({state: 'unreadable', error: 'LabCore did not answer the first read: LabCore is not running'}),
      {status: 503, headers: {'Content-Type': 'application/json'}}));
  }
  return window.__realFetch(url, o);
};
"""
FAIL_OFF = "window.fetch = window.__realFetch;"


def test_a_refresh_that_fails_is_said_not_swallowed(server, drv):
    uid = next(r["uid"] for r in _rows(server) if r["readiness"]["state"] == "ok")
    _open(drv, server, uid)
    assert not _visible(drv, "#ready-stale"), "a page that read fine says nothing about failing"
    word = drv.find_element("id", "ready-word").text
    drv.execute_script(FAIL_ON)
    # a new snapshot makes the live tick ask the record to refresh
    server.app.config["SNAPSHOTS"].refresh()
    assert _wait(lambda: _visible(drv, "#ready-stale"), timeout=12), \
        "a 503 on refresh left the old verdict with no mark (fails: %s)" % drv.execute_script("return window.__fails")
    text = drv.find_element("id", "ready-stale-text").text
    assert text.startswith("Couldn't refresh this instrument (LabCore did not answer the first read: LabCore is not running).")
    assert "Shown as of" in text and "may be out of date" in text
    assert "is-stale" in drv.find_element("id", "readiness").get_attribute("class")
    # the last answer stays (a blank card answers nothing), dimmed
    assert drv.find_element("id", "ready-word").text == word
    assert float(drv.execute_script(
        "return getComputedStyle(document.querySelector('#readiness .rtiles')).opacity")) < 1
    # the line's button is not a second primary
    assert drv.execute_script("return document.querySelector('#ready-stale .btn-primary')") is None
    drv.execute_script(FAIL_OFF)
    drv.find_element("id", "ready-stale-retry").click()
    assert _wait(lambda: not _visible(drv, "#ready-stale")), "LEM answered again and the line stayed"
    assert "is-stale" not in drv.find_element("id", "readiness").get_attribute("class")


def test_the_qc_intro_says_how_long_a_pass_counts(server, drv):
    uid = next(r["uid"] for r in _rows(server) if r["readiness"]["state"] == "ok")
    _open(drv, server, uid)
    intro = drv.find_element("id", "qc-intro").text
    assert "A passing check counts for 24 h." in intro, intro
    assert intro.endswith("The bench reads these off the instrument; nobody types QC here.")


def test_the_head_leads_with_the_pill_and_the_bench(server, drv):
    """§3.1's meta line: pill, "Bench checking in · 13:15", last result,
    level, uid."""
    uid = next(r["uid"] for r in _rows(server) if r["readiness"]["state"] == "ok")
    _open(drv, server, uid)
    pill = drv.find_element("css selector", "#rec-meta [data-testid=record-pill]")
    assert pill.text == "OK to run"
    meta = drv.find_element("id", "rec-meta").text
    assert meta.index("OK to run") < meta.index("Bench checking in") < meta.index(uid)
    # the pill's fill is neutral: colour only in its glyph (§0.1)
    bg = drv.execute_script("return getComputedStyle(arguments[0]).backgroundColor", pill)
    r, g, b = [int(x) for x in bg[bg.index("(") + 1:bg.index(")")].split(",")[:3]]
    assert max(r, g, b) - min(r, g, b) < 16, bg


def test_the_chart_never_says_no_runs_under_a_row_that_shows_one(server, drv):
    """Round 4's critic: PAC Flash 2 on this very seed showed "Last 63.6 ·
    09:13" in its QC row and, right under it, "No runs of this check on file
    yet". The row reads the bench's status; the chart reads LEM's QC log,
    which the seed (like a freshly started bench) holds nothing of. A page
    that shows a run and says there is none has presented an empty read as
    "never run". Every record whose selected check has a result must draw
    that result, and say where it came from."""
    seen = 0
    for r in _rows(server):
        with server.app.test_client() as c:
            rec = c.get("/api/ui/instruments/" + r["uid"]).get_json()
        if rec.get("state") != "ready" or not rec["qc"]["checks"]:
            continue
        sel = next(x for x in rec["qc"]["checks"] if x["test"] == rec["qc"]["selected"])
        if sel["value"] is None or sel["low"] is None:
            continue
        seen += 1
        _open(drv, server, r["uid"])
        assert _wait(lambda: drv.execute_script(
            "return !!document.querySelector('#chart-plot svg.qchart, #chart-plot .chart-note')")), r["title"]
        plot = drv.find_element("id", "chart-plot").text
        assert "No runs" not in plot, "%s: %r under a row showing %s" % (r["title"], plot, sel["value"])
        assert drv.execute_script("return document.querySelectorAll('#chart-plot .qpt').length") >= 1, r["title"]
        cap = drv.find_element("id", "chart-cap").text
        if drv.execute_script("return document.querySelectorAll('#chart-plot .qpt.status').length"):
            # named as LabCore's: the bench may never have checked in (round 5)
            assert "LabCore" in cap and "bench" not in cap, cap
            if drv.execute_script("return document.querySelectorAll('#chart-plot .qpt:not(.status)').length") == 0:
                # nothing logged: a 24 runs / 90 days / All seg would choose between nothing
                assert not _visible(drv, "#chart-range"), r["title"]
    assert seen >= 3, "the seed should have records with results to check (%d)" % seen


def test_every_time_on_the_record_names_its_day(server, drv):
    """Round 4's critic: the QC row's When read "07:30" with no day, beside a
    head reading "Thu 07:49", and the head's "Last result" was older than the
    row. A bare HH:MM is never printed, and the head's last result is the
    newest time on the page."""
    import re
    uid = next(r["uid"] for r in _rows(server) if r["readiness"]["state"] == "ok")
    _open(drv, server, uid)
    for cell in drv.find_elements("css selector", "#qc-rows td.c-when"):
        t = cell.get_attribute("textContent").strip()
        assert not re.fullmatch(r"\d{2}:\d{2}", t), "a bare time in When: %r" % t
    meta = drv.find_element("id", "rec-meta").text
    assert not re.search(r"Last result \d{2}:\d{2}", meta), meta


def test_the_qc_table_fits_its_card(server, drv):
    """A time that names its day ("Today 09:53") is wider than a bare one.
    The card clips what overflows it (overflow: hidden), so a When column
    pushed past the edge is cut mid-word with no scroll to find it: checked
    on every record with checks, at the two widths the bar names."""
    try:
        for w, h in ((1440, 900), (820, 1180)):
            drv.set_window_size(w, h)
            for r in _rows(server):
                _open(drv, server, r["uid"])
                over = drv.execute_script(
                    "const t=document.getElementById('qc-tbl'), c=document.getElementById('qc-card');"
                    "if (!t || t.hidden) return 0;"
                    "return Math.ceil(t.getBoundingClientRect().right - c.getBoundingClientRect().right);")
                assert over <= 0, "%s at %d: the QC table runs %dpx past its card" % (r["title"], w, over)
    finally:
        drv.set_window_size(1440, 900)
