"""The equipment surface on the floor: levels, documents, history, and the
word "machine" leaving the screen.

The three stores shipped tested and reachable from nothing, then the routes
over them shipped and were reachable from nothing. This is the last leg — what
a person can actually see and press — and it is checked in two places, on
purpose:

* **Behaviour lives in `tests/js/floorboot.mjs`**, which executes the shipped
  floor script against a stub DOM: the ladder is sorted, the plan draws one
  level, switching level fires no request, an empty level paints its designed
  panel, a 40 MB upload is refused before it is sent. Those are claims about
  what functions DO and a template grep cannot make any of them — this repo has
  twice caught a "test" that passed with the implementation gutted.

* **This file holds what is genuinely a fact about the served page**: that the
  wire contract did not move, that the rendered words say "equipment", and
  cross-source facts a stub DOM cannot reach — that every route the page posts
  to exists in the Flask app, that no question is asked through a native box.
  A rename IS a property of strings, so a string is the honest way to check it
  — but it is checked against the page's VISIBLE text, with script, style and
  comments stripped, rather than against the file.

**2026-08-25 — the greps were audited and cut.** About twenty tests here were
`assert 'id="levelEmptyBody"' in floor` and `assert "at + '/record'" in floor`:
satisfied by a string existing anywhere in the file, comments included, and
green with the panel never populated and the transition never wired. Two were
named in review — `test_an_empty_level_is_a_designed_state` (five ids, passing
with none of them ever filled) and `test_every_step_of_the_lifecycle_is_reachable`
(five URLs, passing while five of those six transitions were `prompt()` boxes).

The rule that replaced them: **a grep may assert static markup, because there
the markup IS the implementation; it may not stand in for behaviour.** Every
behavioural grep either moved to floorboot as an assertion on RENDERED output
(the empty panel is now driven and read part by part; the five transitions are
driven and their POSTs captured) or was deleted. What remains here is markup
order, cross-source agreement, and words.
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


@pytest.fixture
def floor(client):
    return client.get("/floor").get_data(as_text=True)


PAGES = ("/", "/floor", "/maintenance", "/checklists", "/logs")


def visible_text(html):
    """The page with everything a person cannot read taken out.

    Scripts, styles and HTML comments go, then tags, then entities that would
    otherwise leave stray markup words behind. What is left is what is on
    screen — which is the only place the rename has to hold, because
    `machine_uid` is the wire contract and stays in the code underneath.
    """
    without = re.sub(r"<script\b.*?</script>", " ", html,
                     flags=re.S | re.I)
    without = re.sub(r"<style\b.*?</style>", " ", without, flags=re.S | re.I)
    without = re.sub(r"<!--.*?-->", " ", without, flags=re.S)
    # An attribute can carry visible words too (title=, aria-label=,
    # placeholder=), so those are kept and the rest of each tag dropped.
    kept = " ".join(re.findall(
        r"(?:title|aria-label|placeholder|alt)\s*=\s*\"([^\"]*)\"", without))
    without = re.sub(r"<[^>]+>", " ", without)
    return re.sub(r"\s+", " ", without + " " + kept)


class TestTheLevelSwitcher:
    """Ryan: "in the UI you can cycle through them."

    `levels.cycle()`'s docstring states the rule this class exists to hold: a
    next/previous stepper costs one press per level and so stops working as a
    lab grows, while a labelled dropdown costs an open and a pick however tall
    the building. **The stepper must never ship alone.** Both, or the control
    that always works has been traded for the one that only works small.
    """

    def test_the_picker_says_it_opens_a_menu(self, floor):
        block = floor[floor.index('id="btnLevel"') - 200:]
        assert 'aria-haspopup="menu"' in block[:600]
        assert 'aria-expanded' in block[:600]

    def test_the_switcher_lives_in_the_persistent_tool_strip(self, floor):
        """Not floating on the canvas and not in a dialog: it stays put, and it
        comes before the controls that are about anything other than where you
        are."""
        tools = floor[floor.index('<div class="tools">'):]
        tools = tools[:tools.index("</div>\n\n  <!--")]
        assert 'id="levelBar"' in tools
        assert tools.index('id="levelBar"') < tools.index('id="btnQc"')

    def test_the_empty_panel_is_hidden_by_the_attribute(self, floor):
        """Not by a CSS class. A browser holding a stylesheet from before this
        rule existed would otherwise paint the panel permanently across the
        floor — exactly what happened to the maximal-map exit button."""
        block = floor[floor.index('id="levelEmpty"'):][:80]
        assert "hidden" in block
        assert re.search(r"\.levelempty\[hidden\]\{display:none\}", floor)

    def test_the_chrome_does_not_disappear_on_an_empty_level(self, floor):
        """The panel is an overlay inside the stage, so the tally, the legend
        and the picker are all still there behind it."""
        stage = floor[floor.index('<main class="stage"'):]
        stage = stage[:stage.index("</main>")]
        assert 'id="levelEmpty"' in stage
        assert 'id="legend"' in stage


class TestLevelManagement:
    def test_a_flat_lab_can_still_make_its_first_level(self, floor):
        """The picker and the steppers are hidden when the ladder is empty —
        there is nothing to pick and nowhere to step. That makes "Levels…" the
        only way in, so it sits OUTSIDE the stepper group and cannot be hidden
        along with it.

        floorboot.mjs asserts `renderLevelBar()` does not hide it; only the
        served markup can say it is there at all, which is the half a stub DOM
        cannot falsify — every selector answers with an element.
        """
        assert 'id="btnLevels"' in floor
        between = floor[floor.index('id="levelBar"'):floor.index('id="btnLevels"')]
        assert "</div>" in between, (
            "Levels… must sit outside the level group, or a flat lab hides the "
            "only control that can create its first level")

    def test_creating_is_disabled_until_the_name_is_valid(self, floor):
        """Commit stays off rather than accepting a blank and answering with an
        error."""
        assert re.search(r'id="lvlAdd"[^>]*disabled', floor)



class TestDocumentsTab:
    def test_the_ceiling_matches_the_store(self, floor):
        """A client that thinks the limit is 50 MB is a client that uploads 40
        MB over lab wifi to be refused."""
        import equipment_documents
        assert equipment_documents.MAX_DOCUMENT_BYTES == 25 * 1024 * 1024
        assert "DOC_MAX_BYTES = 25 * 1024 * 1024" in floor



class TestHistoryTab:
    def test_the_assign_surfaces_are_offered_on_a_new_action(self, floor):
        for ident in ("oaWho", "oaDue", "oaPriority", "oaKind"):
            assert f'id="{ident}"' in floor, ident

    def test_assigning_an_existing_action_is_a_dialog_not_chained_prompts(
            self, floor):
        """Three prompt() boxes for one save is the fault reported off the
        bench about sign-in, with a different subject. And `assign` rewrites
        owner, due date and priority together, so all three have to be on
        screen at once or saving one blanks the others."""
        assert 'id="assignDlg"' in floor
        for ident in ("asWho", "asDue", "asPriority", "asGo"):
            assert f'id="{ident}"' in floor, ident
        block = re.search(r"async function saveAssignment\(\)\s*\{(.*?)\n\}",
                          floor, re.S)
        assert block, "saveAssignment() is gone"
        for field in ("assigned_to", "due_at", "priority"):
            assert field in block.group(1), field

    def test_the_fleet_wide_read_is_one_call_and_not_on_the_poll(self, floor):
        """`open_by_machine()` answers the whole lab at once. The rule that
        matters is where it is CALLED from: the floor repaints every two
        seconds from every screen in the building, and a LabCore read on that
        path is the N+1 the snapshot exists to prevent."""
        assert "/api/equipment/open-actions" in floor
        polls = re.findall(r"setInterval\(([^,]+),", floor)
        for called in polls:
            assert "loadOpenActions" not in called, (
                "the fleet's corrective actions must not be on a timer")
        loop = re.search(r"async function load\(\)\s*\{(.*?)\n\}\n", floor,
                         re.S)
        assert loop and "/api/equipment/" not in loop.group(1), (
            "the floor's poll must not reach an /api/equipment/ route")


class TestEveryRouteThePagePostsToExists:
    """The half floorboot cannot see, and the half a grep used to fake.

    `assert "at + '/record'" in floor` passed while the transition was a
    `prompt()` box, because the string was in the file. floorboot now DRIVES
    each transition and captures the POST — which proves the page asks, and
    nothing about whether anything answers.

    This is the other side: every corrective-action path the page posts to has
    a real rule in the Flask app. A typo in either half fails here, and neither
    half can be satisfied by the string existing.
    """

    LIFECYCLE = ("record", "verify", "close", "withdraw", "note", "assign")

    def test_the_page_posts_to_all_six(self, floor):
        for step in self.LIFECYCLE[:5]:
            assert f"'/{step}'" in floor, step
        assert "/assign" in floor

    @pytest.mark.parametrize("step", LIFECYCLE)
    def test_and_the_app_answers_on_that_path(self, client, step):
        rules = {str(r) for r in client.application.url_map.iter_rules()}
        wanted = f"/api/equipment/actions/<uid>/{step}"
        assert wanted in rules, f"{wanted} is not a route"

    def test_the_lifecycle_table_in_the_page_matches_the_store(self, floor):
        """The page disables a button the store would refuse. That is only
        kind if the two tables agree — and they are written twice, once in
        JavaScript and once in Python."""
        import equipment_history

        block = re.search(r"const LIFECYCLE_NEXT = \{(.*?)\};", floor, re.S)
        assert block, "the page lost its copy of the lifecycle"
        page = {}
        for state, moves in re.findall(r"(\w+):\s*\[([^\]]*)\]",
                                       block.group(1)):
            page[state] = {m.strip().strip("'\"")
                           for m in moves.split(",") if m.strip()}
        store = {k: set(v) for k, v in equipment_history.LIFECYCLE.items()}
        assert page == store, (
            "the floor and the store disagree about the lifecycle; the floor "
            "would offer or refuse a move the record does not")


class TestNothingIsAskedThroughANativeBox:
    """A `prompt()` cannot show what it is about, cannot show a refusal, and
    chains — this page's own sign-in was reported off the bench for exactly
    that. Five of the six lifecycle transitions were chained prompts and every
    destructive action was a `confirm()`.

    floorboot proves the paths it drives ask through the page's own sheet, by
    booby-trapping the stubs. This catches a NEW one added anywhere, including
    on a path nothing drives.
    """

    #: THE FLOOR, AND ONLY THE FLOOR — deliberately, and said out loud rather
    #: than left as a silent gap. `maintenance.html` still asks for a
    #: completion note through a `prompt()` and `checklists.html` still deletes
    #: through two `confirm()`s. Both pages need this sheet ported to them and
    #: neither has it yet; listing them here would make this a red test about
    #: work nobody has claimed, and quietly dropping them would make the class
    #: name a lie. When the sheet moves, they go in the tuple.
    SCRIPTED = ("floor.html", "home.html", "logs.html")

    @staticmethod
    def code(name):
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent / "templates"
                / name).read_text(encoding="utf-8")
        bodies = re.findall(r"<script\b[^>]*>(.*?)</script>", text, re.S | re.I)
        code = "\n".join(bodies)
        code = re.sub(r"/\*.*?\*/", " ", code, flags=re.S)
        return re.sub(r"(?m)^\s*//.*$", " ", code)

    @pytest.mark.parametrize("name", SCRIPTED)
    def test_no_page_asks_through_prompt_or_confirm(self, name):
        found = re.findall(r"(?<![.\w])(?:window\.)?(prompt|confirm)\s*\(",
                           self.code(name))
        assert not found, f"{name} still asks through a native {set(found)}"

    def test_the_scan_can_actually_see_the_scripts(self):
        """A `code()` returning "" would pass the whole class on any page."""
        assert "askSubmit" in self.code("floor.html")

    def test_the_floor_has_one_sheet_that_answers_all_of_them(self, floor):
        for ident in ("askDlg", "askTitle", "askRule", "askText", "askErr",
                      "askGo", "askCancel"):
            assert f'id="{ident}"' in floor, ident

    def test_the_sheet_hides_the_parts_it_is_not_using(self, floor):
        """An author `display` rule beats the UA's `[hidden]{display:none}` at
        equal specificity — the trap `.tool[hidden]` and `.levelempty[hidden]`
        are already in this file for. One dialog is reused for every question,
        so a control left showing from the last one is a control in this one:
        the retire sheet's tick sat, unlabelled, in all five lifecycle
        sheets."""
        assert re.search(r"\.askcheck\[hidden\]\{display:none\}", floor)


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
        page = client.get("/logs").get_data(as_text=True)
        code = re.sub(r"/\*.*?\*/", " ", page, flags=re.S)
        code = re.sub(r"(?m)^\s*//.*$", " ", code)
        assert "e.machine_uid ? esc(e.machine_title)" in code
        assert "Lab-wide" in code

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

    def test_the_rail_renders_the_label_when_there_is_one(self, floor):
        assert "e.test_label || e.test_name" in floor

    def test_the_logs_page_prints_the_sentence_and_not_the_blob(self, client):
        page = client.get("/logs").get_data(as_text=True)
        code = re.sub(r"/\*.*?\*/", " ", page, flags=re.S)
        code = re.sub(r"(?m)^\s*//.*$", " ", code)
        fn = re.search(r"function detailText\(e\)\s*\{(.*?)\n\}", code, re.S)
        assert fn, "detailText() is gone"
        assert "e.detail_text" in fn.group(1)
        assert "JSON.stringify" not in fn.group(1)


class TestTheRename:
    """Ryan: "all terms that say 'machine' will change to equipment" — and,
    2026-08-25, confirmed for "instrument" too. **One noun on screen.**

    "instrument" was this page's older word for the same thing, so the floor
    used to read "4 instruments" beside a maintenance page saying "machine
    names" and a rail saying "equipment". Three words for one thing across four
    pages is how an operator ends up asking which of them they are looking at.

    DISPLAY ONLY. `machine_uid` is the wire contract — every bench writes its
    `lem_*` rows on it and POSTs `/api/live` with it, and LabCore has no
    foreign keys, so renaming it in a table or a JSON key would not error, it
    would silently orphan every row forever.
    """

    #: The one noun, and the two it replaced. Checked case-insensitively
    #: against the page's VISIBLE text — never against the file, which is full
    #: of `machine_uid` and always will be.
    GONE = r"\bmachines?\b|\binstruments?\b"

    @pytest.mark.parametrize("path", PAGES)
    def test_no_page_says_machine_or_instrument_to_a_person(self, client, path):
        text = visible_text(client.get(path).get_data(as_text=True))
        found = re.findall(self.GONE, text, re.I)
        assert not found, f"{path} still says {found} where a person reads it"

    def test_the_wire_contract_did_not_move(self, floor):
        """The half that must NOT be renamed. If these ever go, benches stop
        being findable by the rows they have already written."""
        assert "machine_uid" in floor
        assert "/api/machines" in floor

    #: Every page whose SERVED markup names the thing on the bench at all.
    #: `/checklists` is not one: its static markup is about checklists, and
    #: what it says about equipment it renders from JavaScript — which
    #: `visible_text` strips on purpose, and which floorboot is for.
    NAMES_THE_THING = ("/", "/floor", "/maintenance", "/logs")

    @pytest.mark.parametrize("path", NAMES_THE_THING)
    def test_those_pages_use_the_new_word(self, client, path):
        """Not just "the old word is absent": a page that had every mention of
        its subject deleted would pass that on its own."""
        text = visible_text(client.get(path).get_data(as_text=True))
        assert "equipment" in text.lower(), f"{path} names the thing nowhere"

    @pytest.mark.parametrize("path", PAGES)
    def test_no_page_counts_an_uncountable_noun(self, client, path):
        """"Equipment" is uncountable, so a number can never sit against it.

        "4 equipments" and "4 equipment" are both wrong, and "8 equipment in
        the lab across 3 levels" on a floor plan reads as a broken translation
        rather than as a lab. The count has to land on something countable —
        `piece` — or the sentence has to be rebuilt so it does not count at
        all. This catches the mechanical find-and-replace that produces the
        first two.
        """
        text = visible_text(client.get(path).get_data(as_text=True))
        wrong = re.findall(r"\b\d+\s+equipments?\b", text, re.I)
        wrong += re.findall(r"\bequipments\b", text, re.I)
        assert not wrong, f"{path} reads ungrammatically: {wrong}"


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
        page = client.get("/logs").get_data(as_text=True)
        code = re.sub(r"/\*.*?\*/", " ", page, flags=re.S)
        code = re.sub(r"(?m)^\s*//.*$", " ", code)
        row = re.search(r"e\.kind === 'config'(.*?)</td>", code, re.S)
        assert row, "the config column is gone from the logs table"
        assert "action_label" in row.group(1)



class TestTheWordsBuiltInJavaScriptOnThePagesWithNoHarness:
    """The rename's blind spot, found by gutting it.

    `visible_text()` strips `<script>` on purpose — a page's JS is not what a
    person reads, and `machine_uid` lives in there. `tests/js/floorboot.mjs`
    covers the floor properly by EXECUTING it and reading back what it renders.
    Nothing covers the other four pages' JavaScript, and they build screen text
    too: the home tile's count, the logs table's rows, the maintenance
    schedule's empty state.

    Proof it was a hole rather than a worry: replacing home.html's count with
    `${n} equipments` — the exact output a mechanical find-and-replace
    produces — passed the entire suite.

    This is a source scan, and a source scan is the weaker instrument. It is
    used here because it is the only one that reaches these strings, and it is
    kept honest by stripping comments and the wire-contract identifiers first,
    so what it judges is the text the page will build.
    """

    #: floor.html is excluded — floorboot.mjs runs it and reads the markup it
    #: actually produces, which is strictly better than this.
    SCRIPTED = ("home.html", "logs.html", "maintenance.html",
                "checklists.html")

    #: Never prose. `machine` alone is a query-string key and a CSV column;
    #: the rest are identifiers the wire contract is written on.
    WIRE = re.compile(
        r"machine_uid|machine_configs?|machine_title|machines_named|"
        r"machine_state|machine_id|/api/machines?|\bMACHINES\b|"
        r"fMachine|machinesLoaded|lem_machine|\bmachines\b(?=\s*[.\[)])|"
        r"['\"]machines?['\"]|\.machines\b|\bmachine\b(?=\s*[,)])")

    def scripts(self, name):
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent / "templates"
                / name).read_text(encoding="utf-8")
        bodies = re.findall(r"<script\b[^>]*>(.*?)</script>", text, re.S | re.I)
        code = "\n".join(bodies)
        code = re.sub(r"/\*.*?\*/", " ", code, flags=re.S)
        code = re.sub(r"(?m)^\s*//.*$", " ", code)
        return code

    @pytest.mark.parametrize("name", SCRIPTED)
    def test_no_script_builds_a_sentence_in_the_old_noun(self, name):
        code = self.WIRE.sub(" ", self.scripts(name))
        found = re.findall(r"\bmachines?\b|\binstruments?\b", code, re.I)
        assert not found, f"{name}'s script still writes {set(found)}"

    @pytest.mark.parametrize("name", SCRIPTED)
    def test_no_script_counts_the_uncountable_noun(self, name):
        """The find-and-replace failure, in the shape it takes inside a
        template literal: a value interpolated straight onto the bare noun.
        `${n} equipment` is as wrong as "4 equipment", and neither the page nor
        `visible_text()` would ever have shown it."""
        code = self.scripts(name)
        wrong = re.findall(r"\$\{[^{}]*\}\s+equipments?\b", code, re.I)
        wrong += re.findall(r"\b\d+\s+equipments?\b", code, re.I)
        wrong += re.findall(r"\bequipments\b", code, re.I)
        assert not wrong, f"{name}'s script reads ungrammatically: {wrong}"

    def test_the_scan_is_actually_reading_the_scripts(self):
        """A `scripts()` that silently returned "" would make both tests above
        pass on any page at all."""
        code = self.scripts("home.html")
        assert "pieces" in code and "equipment" in code, (
            "the scan cannot see home.html's tile at all")
