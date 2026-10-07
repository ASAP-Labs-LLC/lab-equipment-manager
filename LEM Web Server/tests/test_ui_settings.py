"""Settings in a real browser: the import outcome a person actually reads.

tests/test_settings_page.py proves the routes answer `landed` / `not_landed`
under both refusal shapes, and tests/js/settings_logic.mjs proves the words.
This proves the wire between them: a PM history file chosen in the page,
previewed, imported against a LabCore that refuses — and the page says
"None landed", never "imported", and offers the same file again. Then the
same file against a LabCore that accepts, and the page says it all landed.
Both refusal shapes are driven, because the synthetic one (no `error` key) is
exactly the answer a lazy `if (body.error)` in a page would call a success.

Also, that the section states are drawn: signed out, a gated section shows its
unlock chip and its controls stay readable; a LabCore that cannot answer the
levels read gets a sentence and a Retry, never an empty table.

Skipped without selenium/headless Chrome (run with GC hub's venv). Runs the app
in-process on LEM_UI_TEST_PORT (default 5706) against the fake LabCore.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

webdriver = pytest.importorskip("selenium.webdriver")

import demo_floor  # noqa: E402
import refusal_shapes  # noqa: E402
from labcore_gateway import FakeLabCoreGateway  # noqa: E402
from web_app import create_app  # noqa: E402

PORT = int(os.environ.get("LEM_UI_TEST_PORT", "5706"))

CSV = ("equipment,task,kind,completed_date,performed_by,note\n"
       "Anton Paar DMA 4500,Annual cal,calibration,2026-01-05,kaden,done\n"
       "Anton Paar DMA 4500,Filter change,pm,2026-02-05,kaden,done\n"
       "Anton Paar DMA 4500,Seal check,pm,2026-03-05,kaden,done\n")


class Switchable(FakeLabCoreGateway):
    """Refuses log INSERTs (with the test's shape) while `refusing`; levels
    reads fail while `levels_down`."""
    refusing = None
    levels_down = False

    def sql(self, sql, args=None, **kw):
        s = str(sql)
        if self.refusing is not None and s.lstrip().upper().startswith("INSERT") and "lem_machine_log" in s:
            return dict(self.refusing)
        if self.levels_down and "lem_levels" in s and s.lstrip().upper().startswith("SELECT"):
            return {"error": "Query interrupted after 8s"}
        return super().sql(sql, args, **kw)

    def read_sql(self, sql, args=None, **kw):
        if self.levels_down and "lem_levels" in str(sql):
            return {"error": "Query interrupted after 8s"}
        return super().read_sql(sql, args, **kw)


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    from werkzeug.serving import make_server
    root = tmp_path_factory.mktemp("lem-settings-ui")
    gw = Switchable()
    app = create_app(gw, secret="ui-test", admin_password="ui-pass", documents_root=str(root))
    app.config["SNAPSHOTS"].ensure_schema()
    demo_floor.seed(gw, documents_root=str(root))
    app.config["SNAPSHOTS"].refresh()
    try:
        server = make_server("127.0.0.1", PORT, app, threaded=True)
    except OSError as exc:
        pytest.skip(f"port {PORT} is busy: {exc}")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{PORT}", gw, root
    server.shutdown()


@pytest.fixture
def drv(lab):
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    for arg in ("--headless=new", "--no-sandbox", "--disable-gpu", "--window-size=1440,1000"):
        opts.add_argument(arg)
    try:
        d = webdriver.Chrome(options=opts)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"headless Chrome unavailable: {exc}")
    yield d
    d.quit()


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


def _sign_in(d, base):
    d.get(base + "/healthz")
    assert d.execute_script(
        "return fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({username:'Kaden',password:'ui-pass'})}).then(r=>r.status)") == 200


def _import(d, base, path):
    d.get(base + "/settings#imports")
    d.find_element("id", "pm-file").send_keys(str(path))
    assert _wait(lambda: d.find_element("id", "pm-go-row").is_displayed()), \
        d.find_element("id", "pm-preview-out").text
    assert "Import 3 completions" in d.find_element("id", "pm-import-go").text
    d.find_element("id", "pm-import-go").click()
    assert _wait(lambda: d.find_element("id", "pm-outcome").is_displayed())
    return d.find_element("id", "pm-outcome").text


@pytest.mark.parametrize("shape", refusal_shapes.BOTH, ids=refusal_shapes.IDS)
def test_a_refused_import_is_never_called_imported(lab, drv, shape):
    base, gw, root = lab
    path = root / "pm.csv"
    path.write_text(CSV)
    _sign_in(drv, base)
    gw.refusing = shape
    try:
        said = _import(drv, base, path)
    finally:
        gw.refusing = None
    assert "None landed: 0 of 3 completions are in LabCore." in said, said
    assert "imported" not in said.lower(), said
    assert "nothing is duplicated" in said
    assert drv.find_element("id", "pm-import-go").text == "Run the same import again"


def test_the_same_file_then_lands_and_says_so(lab, drv):
    base, gw, root = lab
    path = root / "pm2.csv"
    path.write_text(CSV)
    _sign_in(drv, base)
    said = _import(drv, base, path)
    assert "All 3 completions landed in LabCore." in said, said
    assert not drv.find_element("id", "pm-go-row").is_displayed()


def test_signed_out_the_gated_sections_show_the_chip(lab, drv):
    base, _gw, _root = lab
    drv.get(base + "/settings")
    chips = [c for c in drv.find_elements("css selector", ".unlock-chip") if c.is_displayed()]
    # levels, hours, imports, and Transfer (T-P12: enrolment, retire, the
    # bridge switch, the journal import and dedupe approvals change the lab)
    assert len(chips) == 4
    assert _wait(lambda: drv.find_element("id", "levels-table").is_displayed())
    assert "Upper Lab" in drv.find_element("id", "levels-table").text


def test_a_failed_levels_read_is_a_sentence_and_a_retry(lab, drv):
    base, gw, _root = lab
    gw.levels_down = True
    try:
        drv.get(base + "/settings")
        st = drv.find_element("id", "levels-state")
        assert _wait(lambda: st.get_attribute("data-state") == "error")
        assert "could not be read" in st.text and "Retry" in st.text
        assert not drv.find_element("id", "levels-table").is_displayed()
    finally:
        gw.levels_down = False
    st.find_element("tag name", "button").click()
    assert _wait(lambda: drv.find_element("id", "levels-table").is_displayed())
