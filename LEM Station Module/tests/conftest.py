import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def _labstation_dir_in_tmp(tmp_path, monkeypatch):
    """Keep the latest-result file out of the developer's real
    AppData/LabLink trees during test runs."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """No test may reach the real network.

    Both roads off this bench are stdlib `urllib` — the live push and the
    floor's configuration GET — and both address whatever `lem_meta` published,
    which in these tests is a private LAN address nobody here is on. Left
    unguarded, every fake floor that patches `post_live` (and not `urlopen`)
    pays a full connect timeout per attempt, so a suite that runs in seconds
    quietly starts taking minutes and its timing depends on the developer's
    network. Worse, on a machine that IS on that subnet a test would talk to a
    real server.

    Tests that exercise the wire patch `urlopen` themselves; their monkeypatch
    is applied after this one and wins.
    """
    def refuse(*args, **kwargs):
        raise AssertionError(
            "a test tried to open a real network connection — patch "
            "urllib.request.urlopen (or post_live / fetch_floor_config)")

    monkeypatch.setattr("urllib.request.urlopen", refuse)
