"""The record's action sections (ia-final §3.1 table, piece 6).

The floor's 20-item right-click menu is gone. Everything it did that the lab
still needs is a visible button in a section of /instruments/<uid>, because a
tablet has no right-click and a hover is not a door (§0.5, lem-ui P4). Why
each part here is a test:

* **Every section the record promises exists, with its one sentence.** A tile
  or a bell line that links to `#corrections` and lands on nothing is the
  dead end §0.3 forbids, so the anchors are pinned.
* **The nine actions that were right-click-only are buttons.** lem-ui.md P4
  lists them: Correction factors…, Flag for service, Dead-line, Clear
  override, Move level, Reset position, Delete equipment…, Export history
  CSV, Export QC CSV. Each has a named, visible control on the record, and
  the page's markup carries no `contextmenu` listener anywhere, nor a native
  `prompt()`/`alert()` (which a tablet renders as a bare system box with no
  way to say why a write was refused).
* **Reset position has a route that does what it says.** The classic floor
  "reset" a bay by POSTing (0, 0), which stood the instrument in the corner
  of the plan and called that its default. Reset now forgets the stored
  position, so the map lists it as "Not on the map · Place it", and a refusal
  is reported as NOT saved, never as done.
* **Remove is the one action that cannot be undone from the page.** The
  server checks both halves of its gate, not just the sheet: the name typed
  must be the instrument's, and the person's password is checked again at
  the moment of removing (the admin unlock, the same check D7 uses for
  approvals). A wrong name or password removes nothing.
* **Correction factors say who and when.** §3.1: "test → offset, who and
  when". The table has always stored both; the read now returns them, and how
  many changes the audit trail holds, so "Change history (n)" is a fact, and
  an unreadable count is said as unknown, never as 0.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import demo_floor
import refusal_shapes
from labcore_gateway import FakeLabCoreGateway
from live_presence import LivePresence
from web_app import create_app

ROOT = Path(__file__).resolve().parent.parent
T = ROOT / "templates"
JS = ROOT / "static" / "js"


class StubAuth:
    """LabCore's login, stubbed: 'good' is Kaden's password."""

    def login(self, u, p):
        return ("Kaden Ortiz", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


class SwitchGateway(FakeLabCoreGateway):
    """The fake, with a switch that makes every row write refused the way
    LabCore refuses: an answer, returned, never raised. Which answer is
    `refusal_shapes.current()`, so a suite driving both shapes drives this."""

    refusing = False

    def sql(self, sql, args=None, **kw):
        if self.refusing and sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            return refusal_shapes.current()
        return super().sql(sql, args, **kw)


def _seeded(tmp_path):
    gw = SwitchGateway()
    app = create_app(gw, authenticator=StubAuth(), secret="s",
                     documents_root=str(tmp_path), live=LivePresence(),
                     live_token="test-token")
    app.config["TESTING"] = True
    app.config["SNAPSHOTS"].ensure_schema()
    demo_floor.seed(gw, documents_root=str(tmp_path))
    app.config["SNAPSHOTS"].refresh()
    return app, gw


def _signed_in(app):
    c = app.test_client()
    r = c.post("/api/login", json={"username": "kaden", "password": "good"})
    assert r.status_code == 200, r.get_data(as_text=True)
    return c


def _record(c, uid):
    r = c.get("/api/ui/instruments/" + uid)
    assert r.status_code == 200
    return r.get_json()


# ── the sections ────────────────────────────────────────────────────────────

SECTION_SENTENCES = {
    "maintenance": "Scheduled PM and calibration. A task is due on its date and overdue the day after.",
    "corrections": "Added to every reading of that test before QC, the LabCore write and display (17025 §7.8.2).",
    "actions": "Open → record → verify → close. Every step keeps who and when.",
    "documents": "Manuals, service reports and certificates for this instrument.",
    "placement": "Where this instrument sits on the floor map.",
}


class TestEverySectionExists:
    def test_the_record_has_every_action_section_with_its_one_sentence(self, tmp_path):
        app, _ = _seeded(tmp_path)
        html = app.test_client().get("/instruments/gc-1").get_data(as_text=True)
        for anchor, sentence in SECTION_SENTENCES.items():
            assert 'id="%s"' % anchor in html, anchor
            assert sentence in html.replace("&rarr;", "→"), anchor
        assert 'id="remove"' in html

    def test_sections_come_in_the_specs_order(self, tmp_path):
        """§3.1: QC · Maintenance and calibration · Correction factors ·
        Corrective actions · Documents · (Log) · Bench and results ·
        Placement · Remove. Remove is last: the destructive act is never
        between a person and the record they came to read."""
        app, _ = _seeded(tmp_path)
        html = app.test_client().get("/instruments/gc-1").get_data(as_text=True)
        order = ["qc", "maintenance", "corrections", "actions", "documents", "bench", "placement", "remove"]
        at = [html.index('<section class="sec" id="%s"' % a) for a in order]
        assert at == sorted(at), dict(zip(order, at))

    def test_the_tiles_may_link_to_every_section(self):
        import ui_record
        for s in ("qc", "maintenance", "corrections", "actions", "documents", "bench", "placement", "remove"):
            assert s in ui_record.SECTIONS, s

    def test_the_foot_no_longer_sends_people_to_the_classic_floor(self, tmp_path):
        """Round 9's foot said corrections, actions and documents were "still
        on the floor map's panel". They are here now; a pointer to a page
        piece 14 deletes is a future dead end."""
        app, _ = _seeded(tmp_path)
        html = app.test_client().get("/instruments/gc-1").get_data(as_text=True)
        assert "/floor/classic" not in html
        assert "/maintenance/classic" not in (JS / "record.js").read_text()
        assert "still on the floor map" not in html


# ── the nine former right-click-only actions ────────────────────────────────

class TestTheNineAreButtons:
    """lem-ui.md P4. The browser test walks them visibly; this pins the
    markup each one is drawn from, so a refactor cannot drop one quietly."""

    def test_each_has_a_named_control(self, tmp_path):
        app, _ = _seeded(tmp_path)
        c = _signed_in(app)
        # Clear override is the card's "Put back on line…", drawn while an
        # override is set: take one instrument off line first
        r = c.post("/api/machines/pac-flash-1/override", json={"override": "SERVICE", "comment": "pump"})
        assert r.status_code == 200, r.get_data(as_text=True)
        app.config["SNAPSHOTS"].refresh()
        html = c.get("/instruments/optimpp-1").get_data(as_text=True) + \
            c.get("/instruments/pac-flash-1").get_data(as_text=True)
        js = (JS / "record_actions.js").read_text() + (JS / "record.js").read_text()
        both = html + js
        checklist = {
            "Correction factors…": "Add a correction…",
            "Flag for service": 'value="SERVICE"',
            "Dead-line": 'value="DEAD-LINE"',
            "Clear override": "Put back on line…",
            "Move level": "Move to another level…",
            "Reset position": "Reset position",
            "Delete equipment…": "Remove from LEM…",
            "Export history CSV": "Export history (CSV)",
            "Export QC CSV": "Export QC (CSV)",
        }
        missing = [k for k, needle in checklist.items() if needle not in both]
        assert not missing, missing
        # Take off line… is the door to both overrides, and it is on the topbar
        assert 'id="topbar-online"' in html and "Take off line…" in html


class TestNoRightClickAnywhere:
    def test_no_contextmenu_listener_in_any_template_or_script(self):
        """§0.5 and §11's guard: nothing is reachable only by right-click.
        Every template and script, the classic floor included, because a
        guard with an exception list is a list somebody adds to."""
        hits = []
        for p in list(T.glob("*.html")) + list(JS.glob("*.js")) + [ROOT / "static" / "lem.js"]:
            if "contextmenu" in p.read_text():
                hits.append(p.name)
        assert not hits, hits

    def test_no_right_click_hint_survives(self):
        hits = []
        for p in list(T.glob("*.html")) + list(JS.glob("*.js")):
            for line in p.read_text().splitlines():
                if re.search(r"[Rr]ight-click\s*(→|&rarr;|for actions|the equipment)", line):
                    hits.append((p.name, line.strip()[:90]))
        assert not hits, hits

    # No exceptions since piece 14 deleted the four old templates that had
    # them (floor, dashboard, stations, maintenance: ia-final §10).
    def test_no_native_prompt_or_alert_on_any_page(self):
        hits = []
        for p in list(T.glob("*.html")) + list(JS.glob("*.js")) + [ROOT / "static" / "lem.js"]:
            for n, line in enumerate(p.read_text().splitlines(), 1):
                if re.search(r"(?<![\w.])(window\.)?(prompt|alert|confirm)\(", line):
                    hits.append("%s:%d %s" % (p.name, n, line.strip()[:80]))
        assert not hits, hits


# ── Placement ───────────────────────────────────────────────────────────────

class TestPlacement:
    def test_the_payload_says_where_it_stands_and_where_it_could_go(self, tmp_path):
        app, _ = _seeded(tmp_path)
        rec = _record(app.test_client(), "gc-1")
        p = rec["placement"]
        assert p["level"] == "Ground Floor" and p["placed"] is True
        assert [lv["name"] for lv in p["levels"]] == ["Ground Floor", "Mezzanine", "Upper Lab"]
        assert p["level_uid"] == next(lv["uid"] for lv in p["levels"] if lv["name"] == "Ground Floor")
        assert p["map"] == "/?view=map&arrange=1&focus=gc-1"

    def test_reset_position_forgets_it_so_the_map_lists_it_unplaced(self, tmp_path):
        app, _ = _seeded(tmp_path)
        c = _signed_in(app)
        assert _record(c, "gc-1")["placement"]["placed"] is True
        r = c.delete("/api/machines/gc-1/position")
        assert r.status_code == 200, r.get_data(as_text=True)
        assert r.get_json()["ok"] is True
        assert _record(c, "gc-1")["placement"]["placed"] is False

    def test_reset_position_needs_someone_signed_in(self, tmp_path):
        app, _ = _seeded(tmp_path)
        assert app.test_client().delete("/api/machines/gc-1/position").status_code == 401

    def test_a_frozen_map_refuses_a_reset_in_words(self, tmp_path):
        app, _ = _seeded(tmp_path)
        c = _signed_in(app)
        assert c.post("/api/map", json={"locked": True}).status_code == 200
        r = c.delete("/api/machines/gc-1/position")
        assert r.status_code == 409
        assert "locked" in r.get_json()["error"]
        assert _record(c, "gc-1")["placement"]["placed"] is True

    @pytest.mark.usefixtures("both_refusal_shapes")
    def test_a_refused_reset_is_not_saved(self, tmp_path):
        app, gw = _seeded(tmp_path)
        c = _signed_in(app)
        gw.refusing = True
        r = c.delete("/api/machines/gc-1/position")
        assert r.status_code in (502, 503), r.get_data(as_text=True)
        body = r.get_json()
        assert body["saved"] is False and "NOT saved" in body["error"]
        gw.refusing = False
        assert _record(c, "gc-1")["placement"]["placed"] is True


# ── Remove ──────────────────────────────────────────────────────────────────

class TestRemove:
    def test_the_payload_says_whether_a_bench_is_running_it(self, tmp_path):
        """The sheet warns before the typing starts: removing an instrument a
        module is running clears that module's configuration."""
        app, _ = _seeded(tmp_path)
        assert _record(app.test_client(), "gc-1")["remove"] == {"checking_in": True}

    def test_a_wrong_name_removes_nothing(self, tmp_path):
        app, _ = _seeded(tmp_path)
        c = _signed_in(app)
        r = c.post("/api/ui/instruments/gc-1/remove", json={"name": "GC 1", "password": "good"})
        assert r.status_code == 400
        assert "GC-1" in r.get_json()["error"]
        assert c.get("/api/ui/instruments/gc-1").status_code == 200

    def test_a_wrong_password_removes_nothing(self, tmp_path):
        app, _ = _seeded(tmp_path)
        c = _signed_in(app)
        r = c.post("/api/ui/instruments/gc-1/remove", json={"name": "GC-1", "password": "nope"})
        assert r.status_code == 403
        assert r.get_json()["error"].startswith("That password")
        assert c.get("/api/ui/instruments/gc-1").status_code == 200

    def test_signed_out_removes_nothing(self, tmp_path):
        app, _ = _seeded(tmp_path)
        r = app.test_client().post("/api/ui/instruments/gc-1/remove", json={"name": "GC-1", "password": "good"})
        assert r.status_code == 401

    def test_the_right_name_and_password_remove_it_and_keep_its_log(self, tmp_path):
        app, gw = _seeded(tmp_path)
        c = _signed_in(app)
        q = "SELECT COUNT(*) AS n FROM lem_machine_log WHERE machine_uid = 'gc-1' AND kind != 'config'"
        before = gw.read_sql(q)["rows"][0]["n"]
        assert before > 0, "the seed gives GC-1 a log to keep"
        r = c.post("/api/ui/instruments/gc-1/remove", json={"name": " GC-1 ", "password": "good"})
        assert r.status_code == 200, r.get_data(as_text=True)
        assert r.get_json()["ok"] is True
        assert gw.read_sql("SELECT COUNT(*) AS n FROM lem_machine_status WHERE machine_uid = 'gc-1'")["rows"][0]["n"] == 0
        # the record is kept: removing is not purging (transfer §5.2)
        assert gw.read_sql(q)["rows"][0]["n"] == before
        audit = gw.read_sql("SELECT detail FROM lem_machine_log WHERE kind = 'config' AND detail LIKE '%machine deleted%'")["rows"]
        assert audit, "the removal must leave its audit row"

    @pytest.mark.usefixtures("both_refusal_shapes")
    def test_a_refused_removal_says_how_far_it_got(self, tmp_path):
        app, gw = _seeded(tmp_path)
        c = _signed_in(app)
        gw.refusing = True
        r = c.post("/api/ui/instruments/gc-1/remove", json={"name": "GC-1", "password": "good"})
        assert r.status_code in (502, 503), r.get_data(as_text=True)
        body = r.get_json()
        assert body["saved"] is False and "NOT saved" in body["error"]
        assert "not_landed" in body


# ── Correction factors ──────────────────────────────────────────────────────

class TestCorrections:
    def test_the_read_says_who_and_when_and_how_many_changes(self, tmp_path):
        app, _ = _seeded(tmp_path)
        c = _signed_in(app)
        r = c.post("/api/machines/gc-1/corrections",
                   json={"test_name": "Flash Point", "correction": "-1.5", "units": "C", "reason": "bias study"})
        assert r.status_code == 200, r.get_data(as_text=True)
        body = c.get("/api/machines/gc-1/corrections").get_json()
        row = next(x for x in body["corrections"] if x["test_name"] == "Flash Point")
        assert row["correction"] == -1.5
        assert row["updated_by"] == "Kaden Ortiz" and row["updated_at"]
        assert body["history"] == 1

    def test_an_unreadable_history_count_is_unknown_not_zero(self, tmp_path, monkeypatch):
        app, gw = _seeded(tmp_path)
        real = gw.read_sql

        def broken(sql, args=None, **kw):
            if "lem_correction_audit" in sql:
                return {"error": "LabCore is busy, try again later", "busy": True}
            return real(sql, args, **kw)
        monkeypatch.setattr(gw, "read_sql", broken)
        body = app.test_client().get("/api/machines/pac-flash-2/corrections").get_json()
        assert body["corrections"], body
        assert body["history"] is None


# ── the page draws every action from data, never a guess ────────────────────

class TestTheRecordScriptRepaintsInPlace:
    def test_a_write_never_reloads_the_page(self):
        """§3.1: "After any action you stay on the section." A reload would
        lose the person's place and re-run every read."""
        src = (JS / "record_actions.js").read_text()
        assert "location.reload" not in src
        assert "location.href =" not in src and "location.assign" not in src

    def test_every_write_checks_the_answer(self):
        """A save that never looks at r.ok reports a refusal as done."""
        src = (JS / "record_actions.js").read_text()
        for m in re.finditer(r"fetch\(([^;]*?)method:\s*'(POST|DELETE)'", src, re.S):
            pytest.fail("a raw write fetch bypasses the shared send(): " + m.group(0)[:80])


class TestOneEmphasis:
    def test_the_sections_add_no_second_primary(self, tmp_path):
        """§0.1: the page's one .btn-primary is the card's. Every section
        button is a default or a ghost, and every sheet's go is .sheet-go."""
        for name in ("record_actions.js", "record_actions_logic.js"):
            assert "btn-primary" not in (JS / name).read_text(), name
        app, _ = _seeded(tmp_path)
        for uid in ("gc-1", "optimpp-1", "koehler-visc"):
            html = app.test_client().get("/instruments/" + uid).get_data(as_text=True)
            assert len(re.findall(r"\bbtn-primary\b", html)) == 1, uid

    def test_every_section_action_is_gated_by_sign_in(self):
        """A signed-out press opens the sign-in sheet titled for the act and
        continues after (piece 3), never a dead 401 in an error line."""
        html = (T / "instrument.html").read_text()
        for bid in ("mt-schedule", "corr-add", "ca-open", "doc-upload", "pl-level", "pl-reset", "rm-open"):
            tag = re.search(r'<button[^>]*id="%s"[^>]*>' % bid, html).group(0)
            assert "data-gated=" in tag and "gate-lock" in tag, bid
