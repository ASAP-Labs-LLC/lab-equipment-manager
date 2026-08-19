"""Run the editor's reorder routine for real, in node.

The markup tests in test_checklist_editor_ui.py can prove the drag is *wired*;
they cannot prove a reorder produces the right list. And reading the code was not
enough last time — the first drag implementation was cancelled by its own guard on
every attempt and looked fine in every static check.

So `tests/js/reorder.mjs` pulls `moveItem` straight out of checklists.html and
exercises it, including the two cases most likely to be wrong: the selected row
following its item, and a subtask dragged above its parent (which must detach,
because a parent may only sit above its child).
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "js" / "reorder.mjs"
LAYOUT = Path(__file__).parent / "js" / "layout.mjs"
LEMJS = Path(__file__).parent / "js" / "lemjs.mjs"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_the_reorder_routine_behaves():
    proc = subprocess.run(["node", str(SCRIPT)], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_it_is_reading_the_shipped_code():
    """If moveItem were renamed or removed, the script must fail loudly rather
    than quietly test nothing."""
    src = (Path(__file__).parent.parent / "templates" / "checklists.html")
    assert "function moveItem(" in src.read_text(encoding="utf-8")
    assert "checklists.html" in SCRIPT.read_text(encoding="utf-8")


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_the_floor_layout_is_order_independent():
    """Ryan: "everytime this thing refreshes it changes layout".

    Two machines are saved on the same bay, and placement used to depend on payload
    order — so whichever reported last claimed the square and the other became
    invisible. This runs the shipped `layout()` over the real floor, shuffles the
    payload, and requires identical bays every time.
    """
    proc = subprocess.run(["node", str(LAYOUT)], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_the_layout_test_is_reading_the_shipped_code():
    """The rule moved into the 3D world with the floor. It stays a pure
    exported function precisely so this test can keep running the real one."""
    src = (Path(__file__).parent.parent / "static" / "world" / "index.js")
    assert "export function claimBays(" in src.read_text(encoding="utf-8")


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_the_client_cache_only_repaints_on_real_change():
    """`live()` compared whole JSON, and /api/machines carries `age_seconds` — which
    moves every request. So the comparison was never equal and the page repainted
    every time, which is the flicker Ryan reported. Runs the shipped lem.js."""
    proc = subprocess.run(["node", str(LEMJS)], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_the_floor_script_actually_boots():
    """Serving 200 is not the same as working.

    Replacing the SVG renderer removed a block of the page that happened to
    contain `esc()` and `col()`, while 96 calls to them stayed. Every Python
    test still passed — the markup was right — and the page threw on load, so
    every listener registered after that point was dead: sign-in, lab hours,
    the debug panel, the map lock, the poll loop. It looked completely normal.

    `tests/js/floorboot.mjs` runs the shipped script against a stub DOM and
    then puts a live-shaped instrument through every render path, which is the
    only thing that catches this class. A static check does not: the first
    attempt passed with `col` deleted, because `col` is also a local inside
    another function.
    """
    script = Path(__file__).parent / "js" / "floorboot.mjs"
    proc = subprocess.run(["node", str(script)], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_whole_floor_arrangements_are_stable():
    """Ryan: "also give the ability to re-organize the machines."

    The Arrange bar writes a position per instrument through the same endpoint a
    drag uses. Its layouts must be a function of the INSTRUMENT, never of the
    order the payload arrived in — an arrangement that shuffles when a machine
    reports is not somewhere an operator can put things. Same rule, and same
    reason, as `claimBays`.
    """
    script = Path(__file__).parent / "js" / "arrange.mjs"
    proc = subprocess.run(["node", str(script)], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_floor_registers_one_resize_listener():
    """Two identical listeners meant every resize redrew the whole SVG twice."""
    src = (Path(__file__).parent.parent / "templates" / "floor.html").read_text(
        encoding="utf-8")
    assert src.count("window.addEventListener('resize'") == 1
