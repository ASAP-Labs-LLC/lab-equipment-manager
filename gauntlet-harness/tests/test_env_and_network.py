"""Nothing the gate runs may write to the real profile or reach the network.

Phase 1 left `lem_latest_Bench b1.csv` in the real
~/AppData/Roaming/LabLink/apps/LabStation, because the module's
`labstation_dir()` falls back to the home directory when APPDATA is unset —
always, on a Mac. These tests check the redirect against the MODULE'S OWN
path functions, not against what we hoped the variables would do, and check
that a lookup of production LabCore is refused and recorded.
"""
import os
import socket

import pytest

from gharness import env, netguard

ROOT = env.root()


def under(path):
    return os.path.realpath(path).startswith(os.path.realpath(ROOT) + os.sep)


def test_every_redirected_variable_points_under_the_temp_root():
    for name in env.REDIRECTED:
        assert under(os.environ[name]), name


def test_the_real_home_is_remembered_and_is_not_the_redirected_one():
    assert not under(env.real_home())
    assert os.path.expanduser("~") != env.real_home()


def test_the_modules_data_dir_resolves_under_the_temp_root(loaded):
    lh, rf, rw, mod, GG = loaded
    assert under(mod.labstation_dir())


def test_a_world_gets_its_own_appdata_journal_and_store(loaded):
    from gharness.world import fresh_world_dirs
    a = fresh_world_dirs()
    first = (os.environ["APPDATA"], os.environ["LEM_JOURNAL_DIR"], os.environ["LEM_STORE_PATH"])
    b = fresh_world_dirs()
    second = (os.environ["APPDATA"], os.environ["LEM_JOURNAL_DIR"], os.environ["LEM_STORE_PATH"])
    assert a != b and all(x != y for x, y in zip(first, second))
    assert all(under(p) for p in first + second)


def test_snapshot_diff_sees_added_changed_and_removed(tmp_path):
    (tmp_path / "keep").write_text("1")
    (tmp_path / "gone").write_text("1")
    before = env.snapshot_tree(str(tmp_path))
    (tmp_path / "keep").write_text("22")
    (tmp_path / "gone").unlink()
    (tmp_path / "new").write_text("1")
    d = env.diff_snapshots(before, env.snapshot_tree(str(tmp_path)))
    assert d == {"added": ["new"], "removed": ["gone"], "changed": ["keep"]}


def test_a_folder_appearing_is_a_write(tmp_path):
    missing = str(tmp_path / "AppData")
    before = env.snapshot_tree(missing)
    os.makedirs(missing)
    (tmp_path / "AppData" / "x.csv").write_text("1")
    assert any(env.diff_snapshots(before, env.snapshot_tree(missing)).values())


def test_production_lookup_is_refused_and_recorded():
    netguard.install()
    before = len(netguard.violations())
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("labvision.asaplabs.net", 443)
    assert netguard.violations()[before:] == [("getaddrinfo", "labvision.asaplabs.net")]


def test_a_raw_connect_is_refused():
    netguard.install()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            s.connect(("127.0.0.1", 9))
    finally:
        s.close()
