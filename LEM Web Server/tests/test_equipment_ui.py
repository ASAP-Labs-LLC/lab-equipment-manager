"""The equipment surface: levels, documents, history, and how the record
reads its audit trail.

The three stores shipped tested and reachable from nothing, then the routes
over them shipped and were reachable from nothing. This is the last leg — what
a person can actually see and press.

**Piece 14 (2026-10).** Most of this file read the old floor page and
its harness (tests/js/floorboot.mjs), which executed the floor's script
against a stub DOM. Both were deleted with the page. Where the surfaces are
now, and where they are tested:

* levels: Settings › Floor and levels, and the record's Placement section
  (tests/test_settings_page.py, test_ui_record_actions.py);
* documents and corrective actions: the record's sections
  (tests/test_ui_record_actions.py, tests/js/record_actions.mjs);
* "no question asked through a native box": every shipped script and
  template is scanned by tests/test_ia_guards.py;
* the old rename ("machine" → "equipment" on every page) is superseded by
  ia-final §4: one vocabulary, whose noun is "instrument".

What stays here is cross-source: every corrective-action step the record
can send has a route that answers it, and the record's table of steps agrees
with the store's lifecycle. Both are checked by RUNNING the record's own
logic (static/js/record_actions_logic.js) in node, not by grepping for its
strings: "a grep may assert static markup, because there the markup IS the
implementation; it may not stand in for behaviour" (2026-08-25).
"""
import re

import pytest

from labcore_gateway import FakeLabCoreGateway
from web_app import create_app


@pytest.fixture
def gw():
    return FakeLabCoreGateway()


@pytest.fixture
def client():
    app = create_app(FakeLabCoreGateway(), secret="s")
    app.config["TESTING"] = True
    return app.test_client()




def _js(name):
    """A static/js file with its comments stripped FIRST, so a test is never
    satisfied by the comment that explains the code."""
    from pathlib import Path
    code = (Path(__file__).resolve().parent.parent / "static" / "js" / name).read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", " ", code, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", " ", code)


def _record_actions_logic(expr: str):
    """Evaluate `expr` against the record's shipped action logic in node and
    return the JSON it prints. The real file, run, not read."""
    import json
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    src = Path(__file__).resolve().parent.parent / "static" / "js" / "record_actions_logic.js"
    prog = ("const fs = require('fs'); const root = {};"
            "new Function('window', 'module', fs.readFileSync(%s, 'utf8'))(root, undefined);"
            "const A = root.LEMRecordActions; console.log(JSON.stringify(%s));"
            % (json.dumps(str(src)), expr))
    done = subprocess.run([node, "-e", prog], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


class TestEveryRouteThePagePostsToExists:
    """Every corrective-action step the record can send has a real rule in
    the Flask app, and the record offers exactly the moves the store allows.

    The record builds each request with `stepRequest` and decides which steps
    to offer with `actionSteps` (record_actions_logic.js). Both are RUN here.
    A typo in either half fails, and neither half can be satisfied by a
    string existing (the old floor's version of this passed while five of
    the six transitions were `prompt()` boxes).
    """

    LIFECYCLE = ("record", "verify", "close", "withdraw", "note", "assign")
    #: what each step does to the state, in the store's words
    MOVES_TO = {"record": "actioned", "verify": "verified", "close": "closed",
                "withdraw": "withdrawn"}

    def test_the_record_sends_every_step_to_its_route(self, client):
        got = _record_actions_logic(
            "%s.map(s => A.stepRequest(s, 'ca-1', {text: 'x', who: 'Ana', due: ''})[0])"
            % list(self.LIFECYCLE))
        rules = {str(r) for r in client.application.url_map.iter_rules()}
        for step, url in zip(self.LIFECYCLE, got):
            assert url == "/api/equipment/actions/ca-1/" + step, (step, url)
            assert "/api/equipment/actions/<uid>/" + step in rules, step

    @pytest.mark.parametrize("step", LIFECYCLE)
    def test_and_the_app_answers_on_that_path(self, client, step):
        rules = {str(r) for r in client.application.url_map.iter_rules()}
        wanted = f"/api/equipment/actions/<uid>/{step}"
        assert wanted in rules, f"{wanted} is not a route"

    def test_the_steps_the_record_offers_match_the_store(self):
        """The record offers a button only for a move the store accepts.
        That is only kind if the two tables agree, and they are written
        twice, once in JavaScript and once in Python."""
        import equipment_history
        states = sorted(equipment_history.LIFECYCLE)
        offered = _record_actions_logic("%s.map(s => A.actionSteps(s).steps)" % states)
        for state, steps in zip(states, offered):
            moves = {self.MOVES_TO[s] for s in steps if s in self.MOVES_TO}
            assert moves == set(equipment_history.LIFECYCLE[state]), (state, steps)


class TestTheDetailBlobIsASentence:
    """The Logs page printed `detail` as raw JSON — on a level move that is a
    bare uid on screen with the readable name two keys away in the same blob.
    """

    def test_a_move_between_two_levels_reads_as_one(self):
        from web_app import describe_detail
        said = describe_detail("level_move", {
            "action": "level_move", "by": "kaden",
            "from": "aaa", "from_name": "Ground Floor",
            "to": "1fbb3672d4", "to_name": "Second Floor"})
        assert said == "Moved from Ground Floor to Second Floor."
        assert "1fbb3672d4" not in said

    def test_a_first_placement_says_placed_rather_than_moved(self):
        from web_app import describe_detail
        assert describe_detail("level_move", {
            "from": "", "from_name": "", "to": "x",
            "to_name": "Ground Floor"}) == "Placed on Ground Floor."

    def test_an_unassignment_says_where_it_went(self):
        from web_app import describe_detail
        said = describe_detail("level_move", {
            "from": "x", "from_name": "Roof", "to": "", "to_name": ""})
        assert "Taken off Roof" in said and "ground" in said

    def test_a_level_that_has_since_been_deleted_is_named_as_that(self):
        """The uid is the fallback nobody can read. A name that is gone is a
        FACT worth printing, not a reason to print the identifier."""
        from web_app import describe_detail
        said = describe_detail("level_move", {
            "from": "deadbeef", "from_name": "", "to": "y",
            "to_name": "Ground Floor"})
        assert "deadbeef" not in said
        assert "no longer exists" in said

    def test_creating_and_renaming_a_level_read_as_sentences(self):
        from web_app import describe_detail
        assert describe_detail("level created", {
            "level": {"uid": "u", "name": "Mezzanine", "rank": 1}}) == (
            "Created the level Mezzanine.")
        assert "now called Mezzanine" in describe_detail("level renamed", {
            "level": {"uid": "u", "name": "Mezzanine", "rank": 1}})

    def test_anything_else_is_pairs_a_person_can_read_not_json(self):
        from web_app import describe_detail
        said = describe_detail("qc-spec saved",
                               {"test_name": "Cloud Point", "expected": -9.0})
        assert "{" not in said and '"' not in said
        assert "test name: Cloud Point" in said

    def test_an_empty_detail_says_nothing_rather_than_an_empty_object(self):
        from web_app import describe_detail
        assert describe_detail("x", {}) == ""
        assert describe_detail("x", {"action": "x", "by": "ryan"}) == ""
        assert describe_detail("x", None) == ""

    def test_the_stored_constant_never_reaches_the_screen(self):
        from web_app import display_action
        assert display_action("level_move") == "level moved"

    def test_and_the_stored_value_is_untouched(self, gw):
        """Same rule as "machine deleted": rows written before today have to
        keep matching a filter that spans them."""
        import json

        from snapshot_service import SnapshotService
        SnapshotService(gw).ensure_schema()
        gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
               "test_name, value, detail) VALUES (?, ?, 'config', '', ?, '', ?)",
               ["m1", "2026-08-24 09:00:00", "level_move",
                json.dumps({"action": "level_move", "by": "kaden",
                            "from": "", "from_name": "",
                            "to": "1fbb3672d4", "to_name": "Ground Floor"})])
        app = create_app(gw, secret="s")
        app.config["TESTING"] = True
        row = [e for e in app.test_client().get("/api/logs").get_json()["events"]
               if e["kind"] == "config"][0]
        assert row["action"] == "level_move"          # stored, untouched
        assert row["action_label"] == "level moved"   # read
        assert row["detail_text"] == "Placed on Ground Floor."

    def test_the_history_tab_gets_the_same_sentence(self, gw):
        """The per-equipment timeline builds its own summary out of the stored
        action, so it printed `level_move` beside a row reading "level
        created". Translated on the way out, like the Logs page."""
        import json

        from snapshot_service import SnapshotService
        SnapshotService(gw).ensure_schema()
        gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
               "test_name, value, detail) VALUES (?, ?, 'config', '', ?, '', ?)",
               ["m1", "2026-08-24 09:00:00", "level_move",
                json.dumps({"action": "level_move", "by": "kaden",
                            "from": "a", "from_name": "Ground Floor",
                            "to": "b", "to_name": "Second Floor"})])
        app = create_app(gw, secret="s")
        app.config["TESTING"] = True
        body = app.test_client().get("/api/equipment/m1/history").get_json()
        entry = [e for e in body["entries"] if e["kind"] == "config"][0]
        assert "level_move" not in entry["summary"]
        assert entry["summary"].startswith("level moved")
        assert "Ground Floor to Second Floor" in entry["summary"]

    def test_a_lab_wide_row_is_not_served_as_a_blank_equipment_cell(self, gw):
        """"level created" happens to the lab. An empty cell in an EQUIPMENT
        column reads as a row whose equipment nobody recorded."""
        import json

        from snapshot_service import SnapshotService
        SnapshotService(gw).ensure_schema()
        gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
               "test_name, value, detail) VALUES ('', ?, 'config', '', ?, '', ?)",
               ["2026-08-24 09:00:00", "level created",
                json.dumps({"action": "level created", "by": "ryan",
                            "level": {"uid": "u", "name": "Mezzanine",
                                      "rank": 1}})])
        app = create_app(gw, secret="s")
        app.config["TESTING"] = True
        client = app.test_client()
        row = [e for e in client.get("/api/logs").get_json()["events"]
               if e["kind"] == "config"][0]
        assert row["machine_uid"] == ""
        assert row["detail_text"] == "Created the level Mezzanine."
        # And the page renders that emptiness as a fact rather than a gap.
        # (piece 7: the rows are drawn by static/js/log_view.js)
        code = _js("log_view.js")
        assert "e.machine_uid\n                ? h('a'" in code
        assert "'Lab-wide'" in code

    def test_the_run_history_rail_gets_it_too(self, gw):
        """The record's own right rail printed `level_move` raw beside rows
        reading as English, because it renders `test_name` off
        /api/machines/<uid>/events — a third road out of the same table."""
        import json

        from snapshot_service import SnapshotService
        SnapshotService(gw).ensure_schema()
        gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
               "test_name, value, detail) VALUES (?, ?, 'config', '', ?, '', ?)",
               ["m1", "2026-08-24 09:00:00", "level_move",
                json.dumps({"action": "level_move", "by": "kaden"})])
        gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
               "test_name, value, detail) VALUES (?, ?, 'qc', 'L1', ?, '1', '')",
               ["m1", "2026-08-23 09:00:00", "Cloud Point"])
        app = create_app(gw, secret="s")
        app.config["TESTING"] = True
        events = app.test_client().get(
            "/api/machines/m1/events").get_json()["events"]
        cfg = [e for e in events if e["kind"] == "config"][0]
        assert cfg["test_name"] == "level_move"        # stored, untouched
        assert cfg["test_label"] == "level moved"      # read
        # A QC row's test_name IS the LabCore method and must not be relabelled
        # — LEM has no test names of its own (CLAUDE.md).
        qc = [e for e in events if e["kind"] == "qc"][0]
        assert "test_label" not in qc
        assert qc["test_name"] == "Cloud Point"


    def test_the_logs_page_prints_the_sentence_and_not_the_blob(self, client):
        # piece 7: the sentence is log_logic.js summary(); the rest of the
        # detail is the sheet's key/value rows, never JSON
        code = _js("log_logic.js")
        fn = re.search(r"function summary\(e\)\s*\{(.*?)\n    \}", code, re.S)
        assert fn, "summary() is gone"
        assert "e.detail_text" in fn.group(1)
        assert "JSON.stringify" not in code


class TestTheAuditTrailReadsInTheNewWordWithoutBeingRewritten:
    """The Logs page prints an audit row's `action` — and two of them are
    literally "machine deleted" and "machine delete incomplete".

    Those are STORED values. `_audit()` writes them into `lem_machine_log`, and
    rows written months ago hold them; changing what goes into the table would
    fork the record in two — everything before this date saying one word,
    everything after saying another, and any filter spanning them broken. It is
    the same rule that keeps `machine_uid` out of the rename.

    So the swap happens on the way OUT, which also brings the rows already in
    the table into the one noun rather than leaving a seam at whatever date
    this shipped.
    """

    def test_the_stored_word_is_translated_for_the_screen(self):
        from web_app import display_action
        assert display_action("machine deleted") == "equipment deleted"
        assert (display_action("machine delete incomplete")
                == "equipment delete incomplete")

    def test_it_leaves_every_other_action_alone(self):
        from web_app import display_action
        for action in ("level created", "qc-spec saved", "document uploaded",
                       "corrective action opened", "correction factor set"):
            assert display_action(action) == action

    def test_a_missing_action_is_empty_rather_than_the_word_none(self):
        from web_app import display_action
        assert display_action(None) == ""
        assert display_action("") == ""

    def test_a_row_written_before_the_rename_reads_in_the_new_word(self, gw):
        """The one that matters: a row ALREADY in the table, written with the
        old word, served to the Logs page. Written straight into the log the
        way `_audit()` writes it, so this is not a test of a helper in
        isolation."""
        import json

        from snapshot_service import SnapshotService
        SnapshotService(gw).ensure_schema()
        gw.sql(
            "INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
            "test_name, value, detail) VALUES (?, ?, 'config', '', ?, '', ?)",
            ["m1", "2026-01-04 09:00:00", "machine deleted",
             json.dumps({"action": "machine deleted", "by": "ryan"})])

        app = create_app(gw, secret="s")
        app.config["TESTING"] = True
        body = app.test_client().get("/api/logs").get_json()
        rows = [e for e in body["events"] if e["kind"] == "config"]
        assert rows, "the seeded audit row did not come back"
        assert rows[0]["action_label"] == "equipment deleted"

    def test_and_the_stored_value_still_comes_back_untouched(self, gw):
        """Anything filtering or grouping on `action` must keep matching what
        was written. Serving only the translated word would break that
        silently."""
        import json

        from snapshot_service import SnapshotService
        SnapshotService(gw).ensure_schema()
        gw.sql(
            "INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
            "test_name, value, detail) VALUES (?, ?, 'config', '', ?, '', ?)",
            ["m1", "2026-01-04 09:00:00", "machine deleted",
             json.dumps({"action": "machine deleted", "by": "ryan"})])

        app = create_app(gw, secret="s")
        app.config["TESTING"] = True
        body = app.test_client().get("/api/logs").get_json()
        rows = [e for e in body["events"] if e["kind"] == "config"]
        assert rows[0]["action"] == "machine deleted"

    def test_the_logs_page_prints_the_label_and_not_the_raw_action(self, client):
        """The wiring. `display_action` being right is worth nothing if the
        column still renders `e.action`.

        Comments are stripped FIRST. Without that this passed with the call
        removed, because the block comment explaining the call still sat inside
        the span being searched — a test satisfied by its own documentation.
        """
        code = _js("log_logic.js")       # piece 7: rowWhat() draws the Test cell
        row = re.search(r"if \(e\.kind === 'config'\)(.*?);", code, re.S)
        assert row, "the config column is gone from the logs table"
        assert "action_label" in row.group(1)

