"""The release workflow must ship the web server alone, and stamp its tag.

LEM differs from COA Reviewer in one way that matters here: the app is a
**subdirectory** of its repository. The repo also carries the Qt station module,
the V4 predecessor kept as a rollback reference, docs and a scratchpad. A
release archive that rooted at the repository would unpack a tree in which
``web_server.pyw`` is not at the top, so the ``current`` junction would point at
a directory the app is not in — and it would ship hundreds of MB of material no
deployment needs.

What LEM has little of is *state*: its configuration lives in LabCore's
``lem_*`` tables and ``data/`` is regenerable cache. The one file worth not
shipping is ``restart.log``, which is a diagnostic of the machine that wrote it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent          # LEM Web Server/
REPO_ROOT = APP_DIR.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_release_workflow_exists() -> None:
    assert WORKFLOW.is_file(), "no .github/workflows/release.yml at the repo root"


def test_workflow_is_valid_yaml() -> None:
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(_text())
    assert isinstance(doc, dict) and "jobs" in doc


def test_triggers_on_tag_push() -> None:
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(_text())
    trigger = doc.get("on", doc.get(True))     # bare `on:` parses as True
    assert trigger and trigger["push"]["tags"]


def test_workflow_declares_contents_write_permission() -> None:
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(_text())
    perms = doc.get("permissions") or next(
        (j.get("permissions") for j in doc["jobs"].values() if j.get("permissions")),
        None,
    )
    assert perms and perms.get("contents") == "write"


def test_archive_is_rooted_at_the_web_server_directory() -> None:
    """Not the repo. `current` points at the release, and the app must be
    directly inside it."""
    assert "LEM Web Server" in _text()


@pytest.mark.parametrize(
    "excluded",
    ["restart.log", "data", ".venv", "__pycache__", ".pytest_cache", ".DS_Store"],
)
def test_junk_and_state_are_excluded(excluded: str) -> None:
    assert excluded in _text(), (
        f"{excluded} is not named in release.yml; it would ship inside the release"
    )


def test_the_macos_venv_is_excluded_by_name() -> None:
    """Both venvs must go. The repo has carried a macOS ``.venv`` next to a
    Windows ``.venv-win``; shipping either into a release is dead weight at
    best, and the wrong-platform one is actively confusing to find there."""
    text = _text()
    assert ".venv" in text and ".venv-win" in text


def test_version_file_is_written_from_the_tag() -> None:
    text = _text()
    assert "VERSION" in text
    assert "github.ref_name" in text


def test_checksum_is_published() -> None:
    assert "sha256" in _text().lower()
