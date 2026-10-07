"""The machine log in a real browser: /logs and the record's Log and Bench
and results sections (ia-final §3.1 #6, #7, §3.6; piece 7).

What a person checks by looking is checked the same way here, in headless
Chrome against a real server on the dev seed:

* **No row is a dead end.** Every row on /logs opens the Log entry sheet,
  and the sheet's one button opens that instrument's record at #log (a
  lab-wide row opens the full log of its kind). Every row, not a sample:
  one row whose click did nothing is exactly the defect.
* **The address bar is the link.** An instrument, a kind chip and a search
  typed on /logs are in the URL; a reload shows the same filters and the
  same rows, and Back undoes the last choice.
* **A counter nobody reported is not 0.** On a bench whose module reports
  none of Filed today, Held and Replays not re-sent, the Bench and results
  section says "Not reported by this bench's module version" and no counter
  cell reads 0.
* **The transfer guard's rows read as English.** A `result_conflict` row is
  "Not re-sent", and its sheet says what LabCore holds, who changed it and
  that the bench's value was not sent again.

Skipped when selenium or headless Chrome is missing (LEM's own venv has no
selenium); run it with GC hub's venv. The port is ``LEM_UI_LOGS_PORT``
(default 5702); a busy port skips.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta

import pytest

webdriver = pytest.importorskip("selenium.webdriver")

import demo_floor  # noqa: E402
from labcore_gateway import FakeLabCoreGateway  # noqa: E402
from web_app import create_app  # noqa: E402

PORT = int(os.environ.get("LEM_UI_LOGS_PORT", "5702"))
NOT_REPORTED = "Not reported by this bench's module version"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from werkzeug.serving import make_server
    root = tmp_path_factory.mktemp("lem-logs")
    gw = FakeLabCoreGateway()
    app = create_app(gw, secret="ui-test", admin_password="ui-pass", documents_root=str(root))
    app.config["SNAPSHOTS"].ensure_schema()
    demo_floor.seed(gw, documents_root=str(root))
    now = datetime.now().replace(microsecond=0)
    # one held-back reading, as the bench sync writes it (bench_api.log_rows_for)
    gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, test_name, value, detail) "
           "VALUES (?,?,?,?,?,?,?)",
           ["gc-2", (now - timedelta(minutes=1)).isoformat(), "result_conflict", "38214", "Sulfur", "0.8392",
            json.dumps({"lab_id": "38214", "test_name": "Sulfur", "ours": "0.8392", "theirs": "0.8410",
                        "their_operator": "dana", "their_updated_at": (now - timedelta(minutes=5)).isoformat()})])
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


def _logs_ready(d):
    return _wait(lambda: d.execute_script(
        "const s = document.getElementById('logs-state');"
        "return document.querySelectorAll('#logs-rows tr').length > 0 || (s && s.dataset.state === 'empty');"))


ROWS = """
return Array.from(document.querySelectorAll('#logs-rows tr')).map(tr => {
  const a = tr.querySelector('.inst-link');
  return {uid: a ? decodeURIComponent(a.getAttribute('href').split('/instruments/')[1]) : '',
          kind: tr.dataset.kind, text: tr.textContent};
});
"""


class TestEveryLogsRowOpensTheSheetAndTheSheetGoesOn:
    def test_every_row_on_the_first_page(self, server, drv):
        drv.get(server.base + "/logs")
        assert _logs_ready(drv), "the log never drew"
        rows = drv.execute_script(ROWS)
        assert len(rows) >= 50, len(rows)
        dead = []
        for i, r in enumerate(rows):
            got = drv.execute_script("""
              const tr = document.querySelectorAll('#logs-rows tr')[arguments[0]];
              tr.querySelector('.c-test').click();          // the row, not its button
              const d = document.getElementById('log-sheet');
              const go = document.getElementById('log-sheet-go');
              const out = {open: d.open, href: go.getAttribute('href'), title: document.getElementById('log-sheet-title').textContent};
              d.close();
              return out;""", i)
            want = ("/instruments/%s#log" % r["uid"].replace(" ", "%20")) if r["uid"] else None
            if not got["open"] or not got["title"]:
                dead.append((i, "sheet did not open", r["text"][:60]))
            elif want and got["href"] != want:
                dead.append((i, got["href"], want))
            elif not want and not got["href"].startswith("/logs"):
                dead.append((i, "lab-wide row goes nowhere", got["href"]))
        assert not dead, dead[:5]

    def test_the_sheets_link_lands_on_the_records_log(self, server, drv):
        drv.get(server.base + "/logs")
        assert _logs_ready(drv)
        drv.execute_script("document.querySelector('#logs-rows tr .row-open').click()")
        href = drv.execute_script("return document.getElementById('log-sheet-go').href")
        drv.execute_script("document.getElementById('log-sheet-go').click()")
        assert _wait(lambda: drv.current_url == href), drv.current_url
        assert _wait(lambda: drv.execute_script(
            "return document.querySelectorAll('#log-rows tr').length > 0")), "the record's Log did not draw"

    def test_not_re_sent_reads_as_english(self, server, drv):
        drv.get(server.base + "/logs?kind=results")
        assert _logs_ready(drv)
        i = drv.execute_script(
            "return Array.from(document.querySelectorAll('#logs-rows tr')).findIndex(tr => tr.dataset.kind === 'result_conflict')")
        assert i >= 0, "the Results chip must include the held-back reading"
        kind = drv.execute_script("return document.querySelectorAll('#logs-rows tr')[arguments[0]].querySelector('.c-kind').textContent", i)
        assert kind.startswith("Not re-sent"), kind
        drv.execute_script("document.querySelectorAll('#logs-rows tr')[arguments[0]].querySelector('.row-open').click()", i)
        say = drv.execute_script("return document.getElementById('log-sheet-say').textContent")
        assert re.match(r"LabCore has 0\.8410, changed by dana \d\d:\d\d; the bench's 0\.8392 was not sent again\.$", say), say
        drv.execute_script("document.getElementById('log-sheet').close()")


class TestTheFiltersRoundTripThroughTheAddressBar:
    def test_choose_reload_and_go_back(self, server, drv):
        drv.get(server.base + "/logs")
        assert _logs_ready(drv)
        assert _wait(lambda: drv.execute_script("return document.getElementById('f-equipment').options.length > 2"))
        drv.execute_script("const s = document.getElementById('f-equipment'); s.value = 'gc-2'; s.dispatchEvent(new Event('change'));")
        assert _wait(lambda: "equipment=gc-2" in drv.current_url)
        drv.execute_script("document.querySelector('#f-kind [data-group=\"qc\"]').click()")
        assert _wait(lambda: "kind=qc" in drv.current_url)
        drv.execute_script("const q = document.getElementById('f-q'); q.value = 'STD'; q.dispatchEvent(new Event('input'));")
        assert _wait(lambda: "q=STD" in drv.current_url)
        url = drv.current_url
        assert _wait(lambda: all(r["uid"] == "gc-2" and r["kind"] == "qc" for r in drv.execute_script(ROWS))
                     and len(drv.execute_script(ROWS)) > 0), "the list follows the filters"

        drv.refresh()
        assert _logs_ready(drv)
        assert drv.current_url == url
        state = drv.execute_script("""return {
            eq: document.getElementById('f-equipment').value,
            kind: document.querySelector('#f-kind [aria-pressed="true"]').dataset.group,
            q: document.getElementById('f-q').value}""")
        assert state == {"eq": "gc-2", "kind": "qc", "q": "STD"}, state
        rows = drv.execute_script(ROWS)
        assert rows and all(r["uid"] == "gc-2" and r["kind"] == "qc" for r in rows), rows[:3]

        drv.back()        # the search was typed (replaceState); Back undoes the chip
        assert _wait(lambda: "kind=qc" not in drv.current_url and "equipment=gc-2" in drv.current_url), drv.current_url
        assert _wait(lambda: drv.execute_script(
            "return document.querySelector('#f-kind [aria-pressed=\"true\"]').dataset.group") == "all")

    def test_the_records_full_log_link_opens_the_same_view(self, server, drv):
        drv.get(server.base + "/instruments/gc-2#log")
        assert _wait(lambda: drv.execute_script("return document.querySelectorAll('#log-rows tr').length > 0"))
        drv.execute_script("document.querySelector('#log-chips [data-group=\"results\"]').click()")
        assert _wait(lambda: drv.execute_script(
            "return document.getElementById('log-full').getAttribute('href')") == "/logs?equipment=gc-2&kind=results")
        drv.get(server.base + "/logs?equipment=gc-2&kind=results")
        assert _logs_ready(drv)
        rows = drv.execute_script(ROWS)
        assert rows and all(r["uid"] == "gc-2" and r["kind"] in ("run", "result_conflict", "held_expired", "reread") for r in rows)


class TestTheRecordsLogAndBench:
    def test_no_counter_reads_zero_when_the_bench_reported_nothing(self, server, drv):
        drv.get(server.base + "/instruments/gc-2")
        assert _wait(lambda: NOT_REPORTED in drv.execute_script(
            "return document.getElementById('bench-counts').textContent")), "the counters never drew"
        cells = drv.execute_script(
            "return Array.from(document.querySelectorAll('#bench-counts .v')).map(v => v.textContent.trim())")
        assert cells and not any(re.fullmatch(r"0|0 \w+", c) for c in cells), cells
        assert not any(re.search(r"\b0\b", c) for c in cells), cells
        assert "Reading…" not in " ".join(cells)

    def test_a_record_row_opens_the_sheet_and_it_goes_to_the_full_log(self, server, drv):
        drv.get(server.base + "/instruments/gc-2#log")
        assert _wait(lambda: drv.execute_script("return document.querySelectorAll('#log-rows tr').length > 0"))
        drv.execute_script("document.querySelector('#log-rows tr .c-kind').click()")
        got = drv.execute_script("return {open: document.getElementById('log-sheet').open,"
                                 " href: document.getElementById('log-sheet-go').getAttribute('href')}")
        assert got == {"open": True, "href": "/logs?equipment=gc-2"}, got
        drv.execute_script("document.getElementById('log-sheet').close()")

    def test_load_older_appends_without_repeating(self, server, drv):
        drv.get(server.base + "/instruments/gc-2#log")
        assert _wait(lambda: drv.execute_script("return document.querySelectorAll('#log-rows tr').length === 20"))
        assert drv.execute_script("return !document.getElementById('log-older').hidden"), "20 of more: Load older shows"
        drv.execute_script("document.getElementById('log-older').click()")
        assert _wait(lambda: drv.execute_script("return document.querySelectorAll('#log-rows tr').length > 20"))
        ids = drv.execute_script("return Array.from(document.querySelectorAll('#log-rows tr')).map(t => t.dataset.id)")
        assert len(ids) == len(set(ids)), "a row was repeated across pages"
