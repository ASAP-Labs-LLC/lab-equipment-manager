"""Accessibility rules a page's source can answer (ia-final §9; piece 15).

The browser half is tests/test_ui_a11y_browser.py (axe-core, reflow, targets,
the phone menu, T1/T2/T4b at 390). These are the rules that hold or fail in
the templates and stylesheets themselves, so they run in LEM's own venv on
every commit, with no Chrome:

* **Skip to content goes somewhere.** The frame's first focusable element is
  the skip link, and every page it frames has exactly one ``id="main"`` for
  it to land on, focusable (``tabindex="-1"``) so the jump moves the caret.
* **Dimming never takes words under AA.** Half opacity was how LEM said
  "stale", "not in this filter" and "being read": it took every word in
  the block to 2:1 (axe found 62 on a stale floor wall). A greyed state is
  now ``filter: grayscale(1)`` plus words that say so; opacity under 1 is
  left only where WCAG exempts it or nothing is text.
* **Reduced motion is honoured** in the frame (transitions cut) and by both
  walls (rotation stops; Previous / Next remain).
* **The record's chart has its words and its table** (§9.1 rule 5).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import demo_floor
from labcore_gateway import FakeLabCoreGateway
from web_app import create_app

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "static" / "css"
TPL = ROOT / "templates"
JS = ROOT / "static" / "js"

PAGES = ["/", "/?view=map", "/instruments/gc-1", "/instruments/no-such-bench", "/checklists/opening",
         "/checklists/edit", "/checklists/edit/new?slot=opening", "/checklists/trends", "/quality",
         "/quality/standards", "/quality/standards/" + demo_floor.STANDARD, "/logs", "/settings", "/help",
         "/results/conflicts", "/floor", "/qc", "/wall"]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("lem-a11y-src")
    gw = FakeLabCoreGateway()
    app = create_app(gw, secret="a11y", documents_root=str(root))
    app.config["SNAPSHOTS"].ensure_schema()
    demo_floor.seed(gw, documents_root=str(root))
    app.config["SNAPSHOTS"].refresh()
    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = "Cody"
    return c


@pytest.mark.parametrize("path", PAGES)
def test_every_page_has_one_main_to_skip_to(client, path):
    r = client.get(path)
    assert r.status_code in (200, 404), (path, r.status_code)
    html = r.get_data(as_text=True)
    mains = re.findall(r'<main\b[^>]*\bid="main"[^>]*>', html)
    assert len(mains) == 1, (path, len(mains))
    if 'class="skip-link"' in html:
        assert 'tabindex="-1"' in mains[0], (path, mains[0])
        body = html[html.index("<body"):]
        first = re.search(r"<(a|button|input|select|textarea)\b[^>]*>", body[body.index(">") + 1:])
        assert 'class="skip-link"' in first.group(0) and 'href="#main"' in first.group(0), first.group(0)
    # one h1 per page; /wall alternates two views, one shown at a time, each
    # with its own (the hidden one is not in the accessibility tree)
    views = len(re.findall(r'<section class="wall-view', html)) or 1
    assert len(re.findall(r"<h1\b", html)) == views, (path, "one h1 per page (per view)")


#: opacity under 1 that cannot take a word under AA, and why
OPACITY_OK = {
    "dialog.sheet .sheet-go:disabled": "disabled (WCAG 1.4.3 exempts inactive controls)",
    ".tr-bridge .btn[disabled]": "disabled",
    ".rrow .tick svg": "the tick's mark before it is ticked: hidden, not dimmed",
    '.file-btn input[type="file"]': "the native picker under its labelled button",
    ".ed-item.dragging": "the ghost of a row while a finger drags it",
    ".qc-line": "the line between QC points; the points and the limits carry the reading",
}


def test_dimming_is_never_opacity_on_words():
    css = re.sub(r"/\*.*?\*/", "", (CSS / "lem.css").read_text(encoding="utf-8"), flags=re.S)
    dimmed = {}
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        m = re.search(r"(?<![-\w])opacity\s*:\s*([\d.]+)", body)
        if m and float(m.group(1)) < 1:
            dimmed[" ".join(sel.split())] = float(m.group(1))
    assert set(dimmed) <= set(OPACITY_OK), {k: v for k, v in dimmed.items() if k not in OPACITY_OK}
    for stale in (".wall-view.is-stale", ".ready-card.is-stale", ".q-card.is-stale", ".tr-kv.stale", ".bay.dim"):
        assert re.search(re.escape(stale) + r"[^{]*\{[^}]*grayscale", css), stale


def test_reduced_motion_is_honoured():
    shell = (CSS / "shell.css").read_text(encoding="utf-8")
    assert re.search(r"prefers-reduced-motion:\s*reduce\)\s*\{[^}]*transition-duration", shell)
    for wall in ("wall_floor.js", "wall_qc.js", "wall.js"):
        src = (JS / wall).read_text(encoding="utf-8")
        assert "prefers-reduced-motion: reduce" in src and "!reduced" in src, wall


def test_the_record_chart_has_words_and_a_table():
    tpl = (TPL / "instrument.html").read_text(encoding="utf-8")
    assert re.search(r'id="chart-cap"[^>]*role="status"', tpl)
    assert re.search(r'<button[^>]*id="chart-as"[^>]*aria-pressed="false"[^>]*>Show as a table<', tpl)
    js = (JS / "record.js").read_text(encoding="utf-8")
    assert "R.chartLast(" in js and "R.chartRows(" in js and "scope: 'col'" in js
