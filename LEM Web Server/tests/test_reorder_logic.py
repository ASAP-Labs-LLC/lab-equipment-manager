"""Run the editor's reorder routine for real, in node.

The markup tests in test_round_editor.py can prove the drag is *wired*;
they cannot prove a reorder produces the right list. And reading the code was not
enough last time — the first drag implementation was cancelled by its own guard on
every attempt and looked fine in every static check.

So `tests/js/round_edit.mjs` runs `moveItem` from the shipped round editor and
exercises it, including the two cases most likely to be wrong: the selected row
following its item, and a subtask dragged above its parent (which must detach,
because a parent may only sit above its child).
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "js" / "round_edit.mjs"
LAYOUT = Path(__file__).parent / "js" / "plan.mjs"
LEMJS = Path(__file__).parent / "js" / "lemjs.mjs"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_the_reorder_routine_behaves():
    """The old editor's moveItem lived in checklists.html; the round editor
    (piece 10) carries it as LEMRoundEditLogic.moveItem, and
    tests/js/round_edit.mjs runs the same cases against the shipped file."""
    proc = subprocess.run(["node", str(SCRIPT)], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_it_is_reading_the_shipped_code():
    """If moveItem were renamed or removed, the script must fail loudly rather
    than quietly test nothing."""
    src = (Path(__file__).parent.parent / "static" / "js" / "round_edit.js")
    assert "function moveItem(" in src.read_text(encoding="utf-8")
    assert "round_edit.js" in SCRIPT.read_text(encoding="utf-8")
    assert "moveItem(" in SCRIPT.read_text(encoding="utf-8")


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_the_floor_layout_is_order_independent():
    """Ryan: "everytime this thing refreshes it changes layout".

    Two machines are saved on the same bay, and placement used to depend on payload
    order — so whichever reported last claimed the square and the other became
    invisible. This runs the shipped `layout()` (static/js/plan.js, since the 3D
    site and its claimBays were deleted) over production's floor and a clash,
    reversed, and requires identical bays.
    """
    proc = subprocess.run(["node", str(LAYOUT)], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_the_layout_test_is_reading_the_shipped_code():
    """The rule lives in plan.js, the one renderer for the map and the wall.
    It stays a pure function precisely so this test can run the real one."""
    src = (Path(__file__).parent.parent / "static" / "js" / "plan.js")
    assert "function layout(" in src.read_text(encoding="utf-8")
    assert "static/js/plan.js" in LAYOUT.read_text(encoding="utf-8")


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_the_client_cache_only_repaints_on_real_change():
    """`live()` compared whole JSON, and /api/machines carries `age_seconds` — which
    moves every request. So the comparison was never equal and the page repainted
    every time, which is the flicker Ryan reported. Runs the shipped lem.js."""
    proc = subprocess.run(["node", str(LEMJS)], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# `test_the_floor_script_actually_boots` ran tests/js/floorboot.mjs against
# the old floor page; piece 14 deleted both. The new pages' scripts are loaded by a
# real browser in tests/test_ui_*_browser.py, which fails on a page that
# throws on load in the same way.


def test_every_drawing_registers_one_resize_listener():
    """Two identical listeners meant every resize redrew the whole SVG twice
    (the old floor). The plan is drawn by the map view and the floor wall
    now, and the chart by the record and the QC wall: each registers one."""
    js = Path(__file__).parent.parent / "static" / "js"
    counts = {p.name: p.read_text(encoding="utf-8").count("window.addEventListener('resize'")
              for p in sorted(js.glob("*.js"))}
    for name in ("floor_map.js", "wall_floor.js", "wall_qc.js", "record.js"):
        assert counts[name] == 1, (name, counts[name])
    assert all(n <= 1 for n in counts.values()), counts
