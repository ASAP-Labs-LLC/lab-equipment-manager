"""The record's Log and Bench sections, and the /logs page (ia-final §3.1 #6,
#7 and §3.6; piece 7).

Three promises are pinned here, each a version of the rule this codebase is
built around (a failed read is never an empty result):

* **A counter nobody reported is not 0.** "Filed to LabCore today",
  "Held, waiting for the sample" and "Replays not re-sent" come from the
  bench's own reports (protocol v2 stats and the result ledger it fills). A
  v3.9 bench reports none of them, so each reads "Not reported by this
  bench's module version" (§3.1 #7, §7). A 0 there would tell an analyst
  that the guard ran and found nothing, when nothing ran at all. A store
  that could not be read is a third sentence, never either of the others.
* **The log pages without losing or repeating a row.** "Load older" asks
  for the rows before the oldest on screen. Paging by a timestamp alone
  skips or repeats rows that share one (a batch of QC verdicts is one
  instant); the cursor is (ts, id), so a page boundary inside a batch is
  exact.
* **The kinds the transfer guard writes can be found.** `result_conflict`
  (shown as "Not re-sent") and `held_expired` are real rows the bench sync
  writes, but the logs filter only accepted the eight v3.9 kinds, so asking
  for them returned everything. The record's chips are groups (All ·
  Results · QC · Status · Setup), and the same names work on /api/logs, so
  a chip, its "Open in the full log" link and its CSV export all ask the
  same question.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

import pytest

import ui_log
from labcore_gateway import FakeLabCoreGateway

NOT_REPORTED = "Not reported by this bench's module version"


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


@pytest.fixture
def gw():
    return FakeLabCoreGateway()


@pytest.fixture
def app(gw, tmp_path):
    from web_app import create_app
    a = create_app(gw, authenticator=StubAuth(), secret="s", documents_root=str(tmp_path))
    a.config["TESTING"] = True
    return a


@pytest.fixture
def client(app):
    return app.test_client()


def _log(gw, uid, ts, kind, lab="", test="", value="", detail=None):
    res = gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, test_name, "
                 "value, detail) VALUES (?,?,?,?,?,?,?)",
                 [uid, ts, kind, lab, test, value, json.dumps(detail or {})])
    assert not (isinstance(res, dict) and res.get("error")), res


def _machine(gw, uid="m1", title="Agilent GC 1"):
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status (machine_uid TEXT PRIMARY KEY, "
           "title TEXT, status TEXT, reason TEXT, updated_at TEXT)")
    gw.sql("INSERT OR REPLACE INTO lem_machine_status (machine_uid, title, status, reason, "
           "updated_at) VALUES (?,?,?,?,?)", [uid, title, "GREEN", "ok", "2026-10-02T09:00:00"])


# ── the counters ────────────────────────────────────────────────────────────

class TestACounterNobodyReportedIsNotZero:
    def test_a_v39_bench_reports_none_of_the_three(self):
        got = ui_log.bench_counts(entry=None, cursor=None, filed_today=None)
        for key in ("filed_today", "held", "replays_not_resent"):
            assert got[key]["n"] is None, key
            assert got[key]["text"] == NOT_REPORTED, key
            assert got[key]["text"] != "0"

    def test_a_v2_bench_says_what_it_reported_and_zero_is_then_true(self):
        """0 is a fine answer when the bench said it: the v2 bench reported
        held = 0 and its ledger holds nothing filed today."""
        cursor = {"mode": "v2", "stats": {"held": 0, "unacked": 0}}
        got = ui_log.bench_counts(entry={"road": "lan"}, cursor=cursor, filed_today=0)
        assert got["held"]["n"] == 0 and got["held"]["text"] == "0"
        assert got["filed_today"]["n"] == 0 and got["filed_today"]["text"] == "0"

    def test_a_v2_bench_that_leaves_a_stat_out_has_not_reported_it(self):
        """The replay counter rides the transfer rework; today's v2 stats do
        not carry it, so even a v2 bench reads the words."""
        cursor = {"mode": "v2", "stats": {"held": 3}}
        got = ui_log.bench_counts(entry={"road": "lan"}, cursor=cursor, filed_today=12)
        assert got["held"]["text"] == "3 readings"
        assert got["filed_today"]["text"] == "12 results"
        assert got["replays_not_resent"]["n"] is None
        assert got["replays_not_resent"]["text"] == NOT_REPORTED

    def test_a_reported_replay_count_is_shown(self):
        cursor = {"mode": "v2", "stats": {"replays_not_resent": 43}}
        got = ui_log.bench_counts(entry={}, cursor=cursor, filed_today=0)
        assert got["replays_not_resent"]["text"] == "43 rows"

    @pytest.mark.parametrize("junk", [True, -1, "7", float("nan"), None, {}])
    def test_junk_in_a_stat_is_not_a_count(self, junk):
        cursor = {"mode": "v2", "stats": {"held": junk}}
        got = ui_log.bench_counts(entry={}, cursor=cursor, filed_today=0)
        assert got["held"]["n"] is None and got["held"]["text"] == NOT_REPORTED

    def test_a_store_that_did_not_answer_is_its_own_sentence(self):
        got = ui_log.bench_counts(entry={}, cursor=None, filed_today=None,
                                  error="database is locked")
        for key in ("filed_today", "held", "replays_not_resent"):
            assert got[key]["n"] is None
            assert got[key]["text"].startswith("Could not be read"), got[key]
            assert "database is locked" in got[key]["text"]


class TestTheBenchRoute:
    def test_a_v39_bench_answers_words_and_no_zero(self, gw, client):
        _machine(gw)
        r = client.get("/api/ui/instruments/m1/bench")
        assert r.status_code == 200
        body = r.get_json()
        for key in ("filed_today", "held", "replays_not_resent"):
            assert body[key]["n"] is None
            assert body[key]["text"] == NOT_REPORTED
        assert '"0"' not in r.get_data(as_text=True)

    def test_a_v2_bench_reads_its_stats_and_todays_ledger(self, gw, client):
        _machine(gw)
        today = datetime.now().strftime("%Y-%m-%d")
        gw.sql("INSERT INTO bench_cursor (machine_uid, bench_epoch, acked_seq, last_seen, "
               "mode, stats) VALUES (?,?,?,?,?,?)",
               ["m1", "e1", 5, today + "T09:00:00", "v2", json.dumps({"held": 2})])
        for lab, at in (("40301", today + "T08:00:00"), ("40302", today + "T08:05:00"),
                        ("40100", "2026-01-01T08:00:00")):
            gw.sql("INSERT INTO result_ledger (machine_uid, lab_id, test_name, value, filed_at, "
                   "bench_seq_ref) VALUES (?,?,?,?,?,?)", ["m1", lab, "IBP", "151.9", at, "e1:1"])
        body = client.get("/api/ui/instruments/m1/bench").get_json()
        assert body["filed_today"]["n"] == 2, "yesterday's (and January's) filings are not today's"
        assert body["held"]["n"] == 2
        assert body["replays_not_resent"]["text"] == NOT_REPORTED

    def test_an_unknown_instrument_is_404_not_words(self, tmp_path):
        """Only once the snapshot is read: before that LEM cannot tell "no
        such instrument" from "not read yet", and says the counters."""
        import demo_floor
        from web_app import create_app
        g = FakeLabCoreGateway()
        a = create_app(g, secret="s", documents_root=str(tmp_path))
        a.config["SNAPSHOTS"].ensure_schema()
        demo_floor.seed(g, documents_root=str(tmp_path))
        a.config["SNAPSHOTS"].refresh()
        c = a.test_client()
        assert c.get("/api/ui/instruments/nope/bench").status_code == 404
        uid = c.get("/api/ui/instruments").get_json()["instruments"][0]["uid"]
        assert c.get("/api/ui/instruments/%s/bench" % uid).status_code == 200


# ── /api/logs: groups, the transfer kinds, the cursor, the count ────────────

class TestTheLogQueryTheSectionsUse:
    def test_the_transfer_guards_kinds_can_be_asked_for(self, gw, client):
        _log(gw, "m1", "2026-10-02T09:10:00", "result_conflict", "38214", "IBP", "151.9",
             {"theirs": "151.6", "their_operator": "dana", "their_updated_at": "2026-10-02T09:10:00"})
        _log(gw, "m1", "2026-10-02T09:00:00", "run", "38214")
        body = client.get("/api/logs?kind=result_conflict").get_json()
        assert [e["kind"] for e in body["events"]] == ["result_conflict"]

    def test_a_chip_name_is_a_kind_group(self, gw, client):
        for i, kind in enumerate(("run", "qc", "status_change", "config", "held_expired",
                                  "result_conflict", "override", "pm")):
            _log(gw, "m1", "2026-10-02T09:%02d:00" % i, kind)
        kinds = lambda q: sorted(e["kind"] for e in client.get("/api/logs?" + q).get_json()["events"])
        assert kinds("kind=results") == ["held_expired", "result_conflict", "run"]
        assert kinds("kind=qc") == ["qc"]
        assert kinds("kind=status") == ["override", "status_change"]
        assert kinds("kind=setup") == ["config", "pm"]

    def test_equipment_is_the_address_bars_word_for_machine(self, gw, client):
        """§3.1 #3 links Change history to /logs?equipment=<uid>&kind=config;
        the page passes the address bar to the API, so the API takes it."""
        _log(gw, "m1", "2026-10-02T09:00:00", "run")
        _log(gw, "m2", "2026-10-02T09:01:00", "run")
        body = client.get("/api/logs?equipment=m1").get_json()
        assert {e["machine_uid"] for e in body["events"]} == {"m1"}

    def test_older_pages_neither_skip_nor_repeat_rows_that_share_a_time(self, gw, client):
        """Five QC verdicts in one batch share their ts. A page boundary
        inside the batch must hand the rest to the next page exactly once."""
        for i in range(5):
            _log(gw, "m1", "2026-10-02T09:00:00", "qc", "AF26", "T%d" % i, str(i))
        _log(gw, "m1", "2026-10-02T08:00:00", "run", "1")
        _log(gw, "m1", "2026-10-02T10:00:00", "run", "2")
        seen, cursor, pages = [], "", 0
        while True:
            body = client.get("/api/logs?equipment=m1&limit=3" + ("&before=" + cursor if cursor else "")).get_json()
            seen += [(e["ts"], e["test_name"], e["lab_id"]) for e in body["events"]]
            pages += 1
            cursor = body.get("next") or ""
            if not cursor:
                break
            assert pages < 10
        assert len(seen) == 7 and len(set(seen)) == 7, seen
        assert [s[0] for s in seen] == sorted([s[0] for s in seen], reverse=True)

    def test_a_short_page_says_there_is_no_older(self, gw, client):
        _log(gw, "m1", "2026-10-02T09:00:00", "run")
        body = client.get("/api/logs?equipment=m1&limit=20").get_json()
        assert body["next"] is None

    def test_a_cursor_that_is_not_one_is_refused_not_ignored(self, client):
        """Ignoring it would answer page one again under "older" (repeats)."""
        r = client.get("/api/logs?before=garbage")
        assert r.status_code == 400
        assert "before" in r.get_json()["error"]

    def test_the_count_header_has_a_total_and_says_who_keeps_the_log(self, gw, client):
        for i in range(7):
            _log(gw, "m1", "2026-10-02T09:0%d:00" % i, "run")
        body = client.get("/api/logs?limit=3").get_json()
        assert body["total"] == 7 and len(body["events"]) == 3
        assert body["source"]["kept_by"] == "LEM"
        assert body["source"]["complete_to"]

    def test_each_event_carries_its_id_so_the_sheet_can_name_it(self, gw, client):
        _log(gw, "m1", "2026-10-02T09:00:00", "run")
        e = client.get("/api/logs").get_json()["events"][0]
        assert isinstance(e["id"], int)


# ── the pages ───────────────────────────────────────────────────────────────

class TestTheRecordHasALogAndABench:
    def _record(self, tmp_path):
        import demo_floor
        from web_app import create_app
        gw = FakeLabCoreGateway()
        app = create_app(gw, secret="s", documents_root=str(tmp_path))
        app.config["TESTING"] = True
        app.config["SNAPSHOTS"].ensure_schema()
        demo_floor.seed(gw, documents_root=str(tmp_path))
        app.config["SNAPSHOTS"].refresh()
        c = app.test_client()
        uid = c.get("/api/ui/instruments").get_json()["instruments"][0]["uid"]
        return uid, c.get("/instruments/" + uid).get_data(as_text=True)

    def test_the_log_section_is_on_the_record_with_its_three_actions(self, tmp_path):
        uid, html = self._record(tmp_path)
        assert 'id="log"' in html and 'data-testid="section-log"' in html
        for chip in ("All", "Results", "QC", "Status", "Setup"):
            assert re.search(r'data-group="[a-z]+"[^>]*>%s<' % chip, html), chip
        assert 'id="log-older"' in html
        assert 'href="/logs?equipment=%s"' % uid in html, "Open in the full log"
        assert '/api/logs.csv?equipment=%s' % uid in html, "Export history (CSV)"

    def test_the_log_comes_before_bench_and_results_as_the_spec_orders(self, tmp_path):
        _uid, html = self._record(tmp_path)
        assert html.index('id="log"') < html.index('id="bench"')

    def test_the_record_carries_the_log_entry_sheet(self, tmp_path):
        _uid, html = self._record(tmp_path)
        assert re.search(r'<dialog class="sheet[^"]*" id="log-sheet"', html)

    def test_a_tile_may_link_to_the_log(self):
        import ui_record
        assert "log" in ui_record.SECTIONS


class TestTheLogsPageIsAShellPage:
    def test_it_is_drawn_on_the_shell_with_its_nav_item_current(self, client):
        html = client.get("/logs").get_data(as_text=True)
        assert 'id="sidebar"' in html or 'class="sidebar' in html
        assert re.search(r'class="nav-item active" href="/logs"', html)
        assert 'id="app-version"' in html

    def test_it_has_the_filters_the_address_bar_holds(self, client):
        html = client.get("/logs").get_data(as_text=True)
        for ident in ("f-q", "f-equipment", "f-kind", "f-range", "f-since", "f-until"):
            assert 'id="%s"' % ident in html, ident

    def test_it_offers_the_three_exports(self, client):
        html = client.get("/logs").get_data(as_text=True)
        for href in ("/api/logs.csv", "/api/export/qc.csv", "/api/export/corrective-actions.csv"):
            assert href in html, href

    def test_its_code_is_a_file_not_an_inline_script(self, client):
        html = client.get("/logs").get_data(as_text=True)
        inline = [s for s in re.findall(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)[^>]*>(.*?)</script>",
                                        html, re.S) if s.strip()]
        assert not inline, "page code lives in static/js (ia-final §2)"
        assert "/static/js/logs.js" in html and "/static/js/log_logic.js" in html

    def test_it_carries_the_log_entry_sheet(self, client):
        html = client.get("/logs").get_data(as_text=True)
        assert re.search(r'<dialog class="sheet[^"]*" id="log-sheet"', html)


class TestNoNativeDialogs:
    """The page code moved out of logs.html into static/js; the old source
    scans of logs.html's inline script now find nothing, so the rule they
    kept (no prompt/confirm/alert, ia-final §10) is kept here on the files."""

    @pytest.mark.parametrize("name", ["logs.js", "log_view.js", "log_logic.js", "record_log.js"])
    def test_none(self, name):
        from pathlib import Path
        code = (Path(__file__).resolve().parent.parent / "static" / "js" / name).read_text(encoding="utf-8")
        code = re.sub(r"/\*.*?\*/", " ", code, flags=re.S)
        code = re.sub(r"(?m)^\s*//.*$", " ", code)
        assert not re.findall(r"(?<![.\w])(?:window\.)?(prompt|confirm|alert)\s*\(", code)
        assert "contextmenu" not in code
