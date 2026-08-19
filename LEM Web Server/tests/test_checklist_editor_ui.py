"""The checklist editor: honest wording, the shared theme, and drag to reorder.

Three things reported 2026-08-03:

1. **"What does 'edit this round' mean? It is permanent is it not?"** — Yes. The
   button opens the editor for the checklist *definition*, and Save POSTs to
   `/api/checklists`, which rewrites it for every day, past and future. "This
   round" reads like it edits today's copy. Renamed, and the dialog says so.

2. **"still outside the HTML theme"** — `select` was given the dark palette but not
   `appearance:none`, so macOS and Windows keep drawing their own capsule and most
   of that styling is ignored. Plus a full-colour 📅 emoji, which is the same thing
   Ryan asked to remove from the nav icons ("white symbols no color").

3. **"let me click and drag to rearrange, moving an arrow a million times is
   tedious"** — a 36-item closing round moved one row at a time. Rows are now
   draggable by a grip. The arrows stay for keyboard users.
"""
import pathlib

import pytest


def src():
    return (pathlib.Path(__file__).resolve().parent.parent
            / "templates" / "checklists.html").read_text(encoding="utf-8")


def css():
    return (pathlib.Path(__file__).resolve().parent.parent
            / "static" / "lem.css").read_text(encoding="utf-8")


# ── 1. say what it actually does ────────────────────────────────────────────

class TestHonestWording:
    def test_the_button_no_longer_says_this_round(self):
        assert "Edit this round" not in src()

    def test_it_says_it_edits_the_checklist(self):
        s = src()
        assert 'id="btnEdit"' in s
        label = s[s.index('id="btnEdit"'):s.index('id="btnEdit"') + 90]
        assert "checklist" in label.lower()

    def test_the_dialog_states_that_changes_are_permanent(self):
        """Editing rewrites the definition for every day, including days already
        recorded — that has to be said before someone deletes an item."""
        s = src()
        dlg = s[s.index('id="editDlg"'):s.index('id="edItems"')]
        low = dlg.lower()
        assert "every day" in low or "all days" in low
        assert "past" in low or "already" in low or "recorded" in low


# ── 2. the shared theme ─────────────────────────────────────────────────────

class TestItMatchesTheTheme:
    def test_selects_do_not_keep_the_native_control(self):
        """Without appearance:none the OS draws its own capsule and the palette
        set right beside it is ignored."""
        sheet = css()
        rule = sheet[sheet.index("input:not([type=checkbox])"):]
        rule = rule[:rule.index("}")]
        assert "appearance:none" in rule.replace(" ", "")

    def test_a_select_still_shows_a_caret(self):
        """appearance:none removes the arrow; without a replacement a select looks
        like a text box."""
        sheet = css()
        assert "background-image" in sheet[sheet.index("input:not([type=checkbox])"):]

    def test_no_emoji_buttons_left_in_the_editor(self):
        """Emoji arrive full-colour and OS-specific — the same reason the nav icons
        became line SVGs."""
        s = src()
        for emoji in ("📅", "🗓", "⬆", "⬇", "🔼", "🔽"):
            assert emoji not in s, emoji

    def test_the_days_control_is_a_line_svg(self):
        s = src()
        assert 'data-days=' in s
        block = s[s.index("data-days="): s.index("data-days=") + 400]
        assert "<svg" in block, "still a glyph rather than a drawn icon"

    def test_the_editor_icons_take_the_current_text_colour(self):
        """So they follow the palette and the hover state instead of being a
        fixed colour, which is what the emoji were."""
        s = src()
        rule = s[s.index(".mvic{"):]
        assert "stroke:currentColor" in rule[:rule.index("}")]


# ── 3. drag to reorder ──────────────────────────────────────────────────────

class TestDragToReorder:
    def test_rows_are_draggable(self):
        s = src()
        assert 'draggable="true"' in s

    def test_there_is_a_grip_to_drag_by(self):
        """Dragging by the row itself would fight text selection in the inputs."""
        assert "grip" in src()

    def test_the_drop_handler_reorders_the_items(self):
        s = src()
        assert "dragstart" in s and "dragover" in s and "drop" in s

    def test_it_moves_the_model_not_just_the_dom(self):
        """Re-rendering from EDIT.items after a DOM-only move would snap rows back."""
        s = src()
        fn = s[s.index("function moveItem"):]
        fn = fn[:fn.index("\n}")]
        assert "EDIT.items" in fn and "splice" in fn

    def test_the_arrows_are_still_there_for_keyboards(self):
        """Drag-and-drop is not keyboard reachable; removing the arrows would make
        reordering impossible without a mouse."""
        s = src()
        assert 'data-move="up"' in s and 'data-move="down"' in s

    def test_a_subtask_dragged_above_its_parent_does_not_orphan_itself(self):
        """Parents can only be rows above, so a move has to re-check that."""
        s = src()
        fn = s[s.index("function moveItem"):]
        fn = fn[:fn.index("\n}")]
        assert "parent" in fn.lower()


class TestDragActuallyStarts:
    """It didn't. `draggable="true"` was on the row, so `dragstart` fired with the
    ROW as its target — the drag source node always is — and the guard
    `if (!e.target.closest('.grip')) preventDefault()` therefore matched nothing
    and cancelled every drag. It could not have worked once.

    The grip itself is the draggable element now, and the row is found from it.
    """

    def test_the_grip_is_the_draggable_element(self):
        s = src()
        grip = s[s.index('class="grip"') - 60:s.index('class="grip"') + 160]
        assert "draggable" in grip

    def test_the_row_is_not_draggable(self):
        """A draggable row also makes the text inputs inside it awkward to select.
        Checks the row's own opening tag, not the grip nested inside it."""
        s = src()
        at = s.index('<div class="edrow')
        tag = s[at:s.index(">", at)]
        assert "draggable" not in tag, tag

    def handler(self, name):
        """The body of one drag listener — anchored on the registration so a
        comment mentioning the event name cannot be mistaken for the code."""
        s = src()
        at = s.index(f"addEventListener('{name}'")
        return s[at:s.index("});", at)]

    def test_dragstart_does_not_cancel_itself(self):
        assert "preventDefault" not in self.handler("dragstart"), \
            "the guard cancelled every drag"

    def test_dragstart_resolves_the_row_from_the_grip(self):
        assert "edrow" in self.handler("dragstart")

    def test_an_untyped_row_is_not_dropped_mid_drag(self):
        """harvestEditor filters out blank rows, so harvesting during a drag made a
        freshly added item disappear underneath the pointer. It runs on drop."""
        assert "harvestEditor" not in self.handler("dragstart")
        assert "harvestEditor" in self.handler("drop")
