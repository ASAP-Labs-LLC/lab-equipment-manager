"""Settings (ia-final §3.7, piece 11): what the page promises, by route and by source.

Settings is where a lab changes things that every screen then shows — its
levels, its hours — and where history comes in from outside. Four promises
matter more than the rest, and each has been broken somewhere in this codebase
before:

* **An import reports what LANDED, and never says "imported" over a refusal.**
  notes.md, verbatim: a bulk import "reported 'imported 3094' while nothing
  landed". It has happened twice. Both importers here (PM history and the old
  LEM's checklists) answer `landed` / `not_landed` in the unit the person
  imported, under BOTH refusal shapes (tests/refusal_shapes.py): the busy dict
  LabCore is evidenced to send, and a synthetic answer with no `error` key that
  a careless `if res.get("error")` would wave through.
* **The Developer tools do not exist in production.** The four "Simulate…"
  tools used to sit in the floor's right-click menu on the live lab, one click
  from a fake RED on a wall display. Under `--dev` they live in Settings ›
  Developer; without it the section is not in the HTML at all and its routes
  are 404, so there is nothing to un-hide.
* **Diagnostics costs LabCore nothing.** People open it when something is slow,
  which is exactly when the queue is deep. Every fact comes out of memory.
* **A failed read is never an empty file.** An export that cannot read its
  source answers 503; an empty register is a statement to an assessor.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import pytest

import refusal_shapes
from labcore_gateway import FakeLabCoreGateway
from live_presence import LivePresence
from web_app import create_app

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
JS = ROOT / "static" / "js"


class StubAuth:
    def login(self, u, p):
        return ("Kaden Ortiz", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


class CountingGateway(FakeLabCoreGateway):
    """Every road into LabCore, counted."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def sql(self, *a, **k):
        self.calls.append(("sql", str(a[0])[:60] if a else ""))
        return super().sql(*a, **k)

    def read_sql(self, *a, **k):
        self.calls.append(("read_sql", str(a[0])[:60] if a else ""))
        return super().read_sql(*a, **k)

    def write(self, *a, **k):
        self.calls.append(("write", ""))
        return super().write(*a, **k)

    def is_running(self, *a, **k):
        self.calls.append(("is_running", ""))
        return super().is_running(*a, **k)


def _app(gw=None, tmp_path=None, **kw):
    app = create_app(gw or FakeLabCoreGateway(), authenticator=StubAuth(), secret="s",
                     live=LivePresence(), live_token="t",
                     documents_root=str(tmp_path) if tmp_path else None, **kw)
    app.config["TESTING"] = True
    return app


def _seeded(tmp_path, gw=None, **kw):
    import demo_floor
    gw = gw or FakeLabCoreGateway()
    app = _app(gw, tmp_path, **kw)
    app.config["SNAPSHOTS"].ensure_schema()
    demo_floor.seed(gw, documents_root=str(tmp_path))
    app.config["SNAPSHOTS"].refresh()
    return app, gw


def _signed_in(app):
    c = app.test_client()
    assert c.post("/api/login", json={"username": "k", "password": "good"}).status_code == 200
    return c


def _section(page, sid):
    m = re.search(r'<section class="sec[^"]*" id="%s"(.*?)</section>' % sid, page, re.S)
    return m.group(1) if m else None


def _text(fragment):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment or "")).strip()


# ── the page ────────────────────────────────────────────────────────────────

class TestTheSections:
    """ia-final §3.7 names seven sections. Six are always there, in that
    order, and the sub-nav links exactly the ones on the page (no dead ends)."""

    ORDER = ["browser", "levels", "hours", "imports", "exports", "diagnostics"]

    def test_every_section_in_the_spec_order(self, tmp_path):
        page = _signed_in(_app(tmp_path=tmp_path)).get("/settings").get_data(as_text=True)
        found = re.findall(r'<section class="sec[^"]*" id="([\w-]+)"', page)
        assert found == self.ORDER, found
        nav = re.search(r'<nav class="set-nav"[^>]*>(.*?)</nav>', page, re.S).group(1)
        assert re.findall(r'href="#([\w-]+)"', nav) == self.ORDER

    def test_the_sections_that_change_the_lab_say_they_are_gated(self, tmp_path):
        """GC's lock mark in the sub-nav and an unlock chip in the section.

        LEM has one permission level today (ia-final open question 6): being
        signed in. So the chip's unlock IS the in-place sign-in — a separate
        admin password the server never checks would be a lock painted on an
        open door. Reading is never gated: signed out you still see the lab's
        hours, only the controls that change them ask you to sign in."""
        page = _app(tmp_path=tmp_path).test_client().get("/settings").get_data(as_text=True)
        nav = re.search(r'<nav class="set-nav"[^>]*>(.*?)</nav>', page, re.S).group(1)
        for sid in ("levels", "hours", "imports"):
            link = re.search(r'<a href="#%s"[^>]*>(.*?)</a>' % sid, nav, re.S).group(1)
            assert "lockmark" in link, sid
            sec = _section(page, sid)
            chip = re.search(r'<button[^>]*class="unlock-chip"[^>]*>', sec)
            assert chip and "data-gated" in chip.group(0), sid
        for sid in ("browser", "exports", "diagnostics"):
            assert "unlock-chip" not in _section(page, sid), sid

    def test_every_control_that_writes_is_gated(self, tmp_path):
        """signin.js catches a `data-gated` control signed out, opens the sheet
        titled for the act, and clicks it again once you are in. A write
        button without it would just answer 401 into a toast."""
        src = (TEMPLATES / "settings.html").read_text()
        for bid in ("level-add", "hours-save", "holiday-add", "pm-import-go", "v4-import-go"):
            m = re.search(r'<button[^>]*id="%s"[^>]*>' % bid, src)
            assert m, bid
            assert "data-gated=" in m.group(0), bid

    def test_rendering_the_page_costs_labcore_nothing(self, tmp_path):
        """Levels and hours are fetched by the page, so a slow LabCore shows as
        a loading row and then a sentence — never as a page that will not open."""
        gw = CountingGateway()
        app, _ = _seeded(tmp_path, gw)
        c = _signed_in(app)
        gw.calls.clear()
        assert c.get("/settings").status_code == 200
        assert gw.calls == [], gw.calls

    def test_no_inline_script_and_text_goes_in_as_text(self):
        src = (TEMPLATES / "settings.html").read_text()
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", src)
        for name in ("settings.js", "settings_logic.js"):
            js = (JS / name).read_text()
            assert ".innerHTML" not in js, name
            assert "prompt(" not in js and "alert(" not in js, name


# ── Developer: only under --dev ─────────────────────────────────────────────

class TestDeveloperOnlyUnderDev:
    SIM_WORDS = ("Simulate a parsed result", "Simulate 6 results", "Simulate status",
                 "Clear this instrument")

    def test_absent_from_the_html_without_dev(self, tmp_path):
        """Not hidden — absent. Anything hidden can be un-hidden, and the
        floor's old Debug menu put a fake RED one click away on the live lab."""
        page = _signed_in(_app(tmp_path=tmp_path)).get("/settings").get_data(as_text=True)
        assert 'id="developer"' not in page
        assert "#developer" not in page
        assert "settings_dev.js" not in page
        for word in self.SIM_WORDS + ("Simulat",):
            assert word not in page, word

    def test_its_routes_do_not_exist_without_dev(self, tmp_path):
        app, _ = _seeded(tmp_path)
        c = _signed_in(app)
        r = c.post("/api/dev/simulate", json={"machine_uid": "pac-flash-2", "action": "status",
                                             "status": "RED"})
        assert r.status_code == 404

    def test_present_under_dev_with_its_four_tools(self, tmp_path):
        page = _signed_in(_app(tmp_path=tmp_path, dev_tools=True)).get("/settings").get_data(as_text=True)
        sec = _section(page, "developer")
        assert sec is not None
        for word in self.SIM_WORDS:
            assert word in _text(sec), word
        assert "settings_dev.js" in page
        nav = re.search(r'<nav class="set-nav"[^>]*>(.*?)</nav>', page, re.S).group(1)
        assert re.findall(r'href="#([\w-]+)"', nav)[-1] == "developer"

    def test_dev_tools_never_attach_to_a_real_labcore(self, tmp_path):
        """`--dev` means the in-memory fake. Asked for against anything else
        (a mistake in a boot script), the tools stay off: the flag cannot be the
        only thing standing between a demo button and the live lab."""
        class NotFake:
            def __getattr__(self, name):
                raise AssertionError("touched the gateway: " + name)
        from web_app import dev_tools_allowed
        assert dev_tools_allowed(FakeLabCoreGateway(), True) is True
        assert dev_tools_allowed(FakeLabCoreGateway(), False) is False
        assert dev_tools_allowed(NotFake(), True) is False

    def test_the_server_turns_them_on_only_for_dev(self):
        import web_server
        assert web_server.app_options(web_server.build_parser().parse_args(["--dev"])) == {"dev_tools": True}
        assert web_server.app_options(web_server.build_parser().parse_args([])) == {"dev_tools": False}


class TestTheSimulateTools:
    """What the four tools do, under --dev only. A simulated STATUS goes on
    the live road (memory, gone on restart, and it says "Simulated" in its
    reason so a screenshot can never pass it off as real). Simulated RESULTS go
    into the fake LabCore's log as runs, so Logs and the record show them."""

    def _dev(self, tmp_path):
        app, gw = _seeded(tmp_path, dev_tools=True)
        return app, gw, _signed_in(app)

    def _machine(self, c, uid):
        return next(m for m in c.get("/api/machines").get_json()["machines"] if m["machine_uid"] == uid)

    def test_simulate_status_shows_everywhere_and_says_so(self, tmp_path):
        _app_, _gw, c = self._dev(tmp_path)
        uid = c.get("/api/machines").get_json()["machines"][0]["machine_uid"]
        r = c.post("/api/dev/simulate", json={"machine_uid": uid, "action": "status", "status": "RED"})
        assert r.status_code == 200, r.get_json()
        m = self._machine(c, uid)
        assert m["status"] == "RED"
        assert "Simulated" in m["reason"]

    def test_clear_puts_the_record_back(self, tmp_path):
        _app_, _gw, c = self._dev(tmp_path)
        before = c.get("/api/machines").get_json()["machines"][0]
        uid = before["machine_uid"]
        c.post("/api/dev/simulate", json={"machine_uid": uid, "action": "status", "status": "RED"})
        r = c.post("/api/dev/simulate", json={"machine_uid": uid, "action": "clear"})
        assert r.status_code == 200
        after = self._machine(c, uid)
        assert after["status"] == before["status"]
        assert "Simulated" not in after["reason"]

    def test_a_parsed_result_and_a_burst_of_six(self, tmp_path):
        _app_, gw, c = self._dev(tmp_path)
        uid = c.get("/api/machines").get_json()["machines"][0]["machine_uid"]

        def sims():
            res = gw.read_sql("SELECT lab_id FROM lem_machine_log WHERE machine_uid = ? "
                              "AND kind = 'run' AND lab_id LIKE 'SIM-%'", [uid])
            assert not res.get("error"), res
            return len(res["rows"]) if "rows" in res else len(res.get("data") or [])
        assert sims() == 0
        r = c.post("/api/dev/simulate", json={"machine_uid": uid, "action": "result"})
        assert r.status_code == 200 and r.get_json()["landed"] == 1, r.get_json()
        r = c.post("/api/dev/simulate", json={"machine_uid": uid, "action": "burst"})
        assert r.status_code == 200 and r.get_json()["landed"] == 6, r.get_json()
        assert sims() == 7

    def test_an_unknown_instrument_is_a_404_not_a_ghost(self, tmp_path):
        _app_, _gw, c = self._dev(tmp_path)
        r = c.post("/api/dev/simulate", json={"machine_uid": "nope", "action": "status", "status": "RED"})
        assert r.status_code == 404


# ── imports: landed / not_landed, under both refusal shapes ────────────────

def _refusing(tmp_path, refuse):
    """A seeded lab whose LabCore then refuses the statements `refuse` picks,
    answering the shape the running test is driving."""
    class Refusing(FakeLabCoreGateway):
        armed = False

        def sql(self, sql, args=None, **kw):
            if self.armed and refuse(sql):
                return refusal_shapes.current()
            return super().sql(sql, args, **kw)

        def write(self, op, params=None, **kw):
            if self.armed and refuse(op):
                return refusal_shapes.current()
            return super().write(op, params, **kw)

    gw = Refusing()
    app, _ = _seeded(tmp_path, gw)
    gw.armed = True
    return gw, _signed_in(app)


def _is_write(sql):
    return str(sql).lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))


@pytest.mark.usefixtures("both_refusal_shapes")
class TestThePmImportSaysWhatLanded:
    CSV = ("equipment,task,kind,completed_date,performed_by,note\n"
           "Anton Paar DMA 4500,Annual cal,calibration,2026-01-05,kaden,done\n"
           "Anton Paar DMA 4500,Filter change,pm,2026-02-05,kaden,done\n"
           "Anton Paar DMA 4500,Seal check,pm,2026-03-05,kaden,done\n")

    def test_a_wholly_refused_import_landed_nothing_and_is_not_a_2xx(self, tmp_path):
        _gw, c = _refusing(tmp_path, lambda s: _is_write(s) and "lem_machine_log" in s)
        r = c.post("/api/maintenance-import", json={"csv": self.CSV})
        body = r.get_json()
        assert r.status_code >= 400, body
        assert "ok" not in body
        assert body["landed"] == 0 and body["not_landed"] == 3, body
        assert body["incomplete"] is True

    def test_a_partial_import_counts_only_what_landed(self, tmp_path):
        first = [True]

        def refuse(sql):
            if not (_is_write(sql) and "lem_machine_log" in sql):
                return False
            if first[0]:
                first[0] = False
                return False
            return True
        _gw, c = _refusing(tmp_path, refuse)
        r = c.post("/api/maintenance-import", json={"csv": self.CSV})
        body = r.get_json()
        assert r.status_code >= 400
        assert (body["landed"], body["not_landed"]) == (1, 2), body

    def test_a_clean_import_says_all_landed(self, tmp_path):
        _gw, c = _refusing(tmp_path, lambda s: False)
        r = c.post("/api/maintenance-import", json={"csv": self.CSV})
        body = r.get_json()
        assert r.status_code == 200, body
        assert (body["landed"], body["not_landed"]) == (3, 0), body


V4_CONFIG = """{"checklists": [
  {"name": "Opening", "slot": "opening", "items": [{"text": "Gas on"}, {"text": "Lights"}]},
  {"name": "Closing", "slot": "closing", "items": [{"text": "Gas off"}]}
]}"""


@pytest.mark.usefixtures("both_refusal_shapes")
class TestTheChecklistImportSaysWhatLanded:
    def _found(self):
        from checklists import import_v4_checklists
        found = import_v4_checklists(V4_CONFIG)
        assert len(found) == 2, "the fixture must be a file the importer reads"
        return found

    def test_a_refused_import_landed_nothing_and_is_not_a_2xx(self, tmp_path):
        self._found()
        _gw, c = _refusing(tmp_path, lambda s: _is_write(s) and "lem_checklist" in s)
        r = c.post("/api/checklists/import-v4", json={"json": V4_CONFIG})
        body = r.get_json()
        assert r.status_code >= 400, body
        assert "ok" not in body
        assert body["landed"] == 0 and body["not_landed"] == 2, body
        assert body["incomplete"] is True

    def test_a_clean_import_says_all_landed(self, tmp_path):
        self._found()
        _gw, c = _refusing(tmp_path, lambda s: False)
        r = c.post("/api/checklists/import-v4", json={"json": V4_CONFIG})
        body = r.get_json()
        assert r.status_code == 200, body
        assert (body["landed"], body["not_landed"]) == (2, 0), body


class TestThePageNeverCallsARefusalAnImport:
    """The server's honesty is wasted on a page that reads only the status
    code — or only `created`. The words the page shows come from one pure
    function (settings_logic.js `importOutcome`), node-tested in
    tests/js/settings_logic.mjs; here, that the page uses it for both
    importers and reads `landed`, not `created`."""

    def test_both_importers_report_through_import_outcome(self):
        """One importer routine, configured twice: the outcome sentence can only
        come from importOutcome, and nothing reads `.created`."""
        js = (JS / "settings.js").read_text()
        assert js.count("importOutcome(") == 1
        routine = js[js.index("function importer("):js.index("function wireImports(")]
        assert "L.importOutcome(res.status, res.body, cfg.unit)" in routine
        wiring = js[js.index("function wireImports("):]
        assert wiring.count("importer({") == 2
        for url in ("'/api/maintenance-import'", "'/api/checklists/import-v4'"):
            assert url in wiring, url
        assert ".created" not in js


# ── Diagnostics ─────────────────────────────────────────────────────────────

class TestDiagnostics:
    def test_the_api_costs_labcore_nothing(self, tmp_path):
        gw = CountingGateway()
        app, _ = _seeded(tmp_path, gw)
        c = _signed_in(app)
        gw.calls.clear()
        r = c.get("/api/ui/diagnostics")
        assert r.status_code == 200
        assert gw.calls == [], gw.calls

    def test_it_carries_every_fact_the_spec_names(self, tmp_path):
        app, _ = _seeded(tmp_path)
        body = _signed_in(app).get("/api/ui/diagnostics").get_json()
        keys = [row["key"] for row in body["rows"]]
        for key in ("record", "labcore", "live_road", "audit_spool", "log_copy", "version"):
            assert key in keys, key
        health = _signed_in(app).get("/healthz").get_json()
        for field in ("version", "labcore", "schema", "audit_spool", "pid", "idle_seconds", "log"):
            assert field in body["healthz"], field
            assert body["healthz"][field] == health[field] or field == "idle_seconds", field
        assert body["summary"]["text"]

    def test_the_record_row_carries_its_age(self, tmp_path):
        app, _ = _seeded(tmp_path)
        row = {r["key"]: r for r in _signed_in(app).get("/api/ui/diagnostics").get_json()["rows"]}["record"]
        assert row["at"], "the page counts the age up from the read's own time"
        assert row["glyph"] == "final"

    def test_before_the_first_read_it_says_so_and_never_zero(self, tmp_path):
        """No snapshot yet: "Not read yet" and "Not known yet" — never
        "0 of 0 benches use it", which is a statement about the lab."""
        app = _app(tmp_path=tmp_path)
        body = _signed_in(app).get("/api/ui/diagnostics").get_json()
        rows = {r["key"]: r for r in body["rows"]}
        assert rows["record"]["value"] == "Not read yet"
        assert rows["live_road"]["value"] == "Not known yet"
        assert "0 of 0" not in str(body)

    def test_the_source_of_the_machine_log_is_named(self, tmp_path):
        """ia-final §7: the log's source is named in two places, and this is one."""
        app, _ = _seeded(tmp_path)
        row = {r["key"]: r for r in _signed_in(app).get("/api/ui/diagnostics").get_json()["rows"]}["log_copy"]
        assert "lem_machine_log" in row["note"]

    def test_refresh_now_reads_the_record_once_and_only_on_a_press(self):
        """Refresh now is a person asking: one snapshot read (the existing
        `/api/machines?fresh=1`), then the memory-only diagnostics. Never on
        a timer — a timer would make this page a load on the queue."""
        js = (JS / "settings.js").read_text()
        assert "/api/machines?fresh=1" in js
        assert "setInterval(" not in js.replace("setInterval(tickAges", "")


# ── Records and exports ─────────────────────────────────────────────────────

class TestExports:
    def test_the_page_offers_the_four_files(self, tmp_path):
        page = _app(tmp_path=tmp_path).test_client().get("/settings").get_data(as_text=True)
        sec = _section(page, "exports")
        for href in ("/api/export/equipment.csv", "/api/export/qc.csv",
                     "/api/export/corrective-actions.csv", "/api/export/uncertainty.csv"):
            assert 'href="%s"' % href in sec, href

    def test_the_equipment_register_comes_from_memory(self, tmp_path):
        gw = CountingGateway()
        app, _ = _seeded(tmp_path, gw)
        c = _signed_in(app)
        n = len(c.get("/api/machines").get_json()["machines"])
        gw.calls.clear()
        r = c.get("/api/export/equipment.csv")
        assert r.status_code == 200
        assert gw.calls == [], gw.calls
        rows = list(csv.reader(io.StringIO(r.get_data(as_text=True))))
        assert rows[0][:3] == ["machine_uid", "instrument", "level"]
        assert len(rows) - 1 == n

    def test_no_record_read_yet_is_a_503_never_an_empty_register(self, tmp_path):
        r = _app(tmp_path=tmp_path).test_client().get("/api/export/equipment.csv")
        assert r.status_code == 503

    def test_the_uncertainty_register_refuses_a_failed_read(self, tmp_path):
        class Down(FakeLabCoreGateway):
            def sql(self, sql, args=None, **kw):
                if "lem_uncertainty" in sql:
                    return {"error": "Query interrupted after 8s"}
                return super().sql(sql, args, **kw)

            def read_sql(self, sql, args=None, **kw):
                if "lem_uncertainty" in sql:
                    return {"error": "Query interrupted after 8s"}
                return super().read_sql(sql, args, **kw)
        app = _app(Down(), tmp_path)
        r = app.test_client().get("/api/export/uncertainty.csv")
        assert r.status_code == 503, r.get_data(as_text=True)[:200]

    def test_the_uncertainty_register_has_its_twelve_fields(self, tmp_path):
        import uncertainty
        app, _ = _seeded(tmp_path)
        r = app.test_client().get("/api/export/uncertainty.csv")
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
        header = next(csv.reader(io.StringIO(r.get_data(as_text=True))))
        for field in uncertainty.REGISTER_FIELDS:
            assert field in header, field
