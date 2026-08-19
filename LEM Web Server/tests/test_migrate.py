"""TDD for the one-shot JSON -> central DB migration."""

import json

from db_config_store import DbConfigStore
from labcore_gateway import FakeLabCoreGateway
from migrate_json_to_db import migrate_file


def _write_json(tmp_path):
    raw = {
        "version": 5,
        "poll_minutes": 5,
        "map_locked": False,
        "sample_id_column": "Lab ID",
        "samples": [
            {"name": "ContextA", "sample_id_val": "STD-1",
             "tests": [{"name": "Flash", "value_col": "Flash Point",
                        "expected": 65.0, "std_dev": 2.0, "k": 2.0, "units": "C"}]}
        ],
        "boxes": [
            {"uid": "box1", "title": "GC-1", "csv_path": "",
             "watched_targets": [{"sample": "ContextA", "test": "Flash"}]}
        ],
        "users": [{"username": "alice", "password": "secret"}],
    }
    path = tmp_path / "lab_manager_config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def test_migrate_file_populates_db(tmp_path):
    gw = FakeLabCoreGateway()
    path = _write_json(tmp_path)

    cfg = migrate_file(gw, str(path))

    assert len(cfg.boxes) == 1
    assert cfg.boxes[0].uid == "box1"

    # And the store now loads the same content back out of the DB.
    loaded = DbConfigStore(gw).load()
    assert loaded.boxes[0].title == "GC-1"
    assert loaded.samples[0].sample_id_val == "STD-1"
    assert loaded.samples[0].tests[0].value_col == "Flash Point"
    assert loaded.users[0].username == "alice"


def test_migrate_missing_file_raises(tmp_path):
    gw = FakeLabCoreGateway()
    try:
        migrate_file(gw, str(tmp_path / "nope.json"))
    except FileNotFoundError:
        return
    raise AssertionError("expected FileNotFoundError for missing config file")
