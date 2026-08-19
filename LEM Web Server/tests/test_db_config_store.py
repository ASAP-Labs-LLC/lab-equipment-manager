"""TDD for DbConfigStore — LEM's config living in the central DB.

The store persists the full AppConfig into namespaced lem_* tables through the
LabCore write queue and loads it back. Round-trip fidelity is the invariant.
"""

from db_config_store import DbConfigStore, CURRENT_VERSION
from labcore_gateway import FakeLabCoreGateway
from models import (
    AppConfig,
    BoxConfig,
    ChecklistItem,
    ChecklistSpec,
    SampleSpec,
    SampleTestSpec,
    UserSpec,
    WatchedTarget,
)


def _rich_config() -> AppConfig:
    sample = SampleSpec(
        name="ContextA", sample_id_val="STD-1",
        tests=[SampleTestSpec(name="Flash", value_col="Flash Point", expected=65.0, std_dev=2.0, k=2.0, units="C")],
    )
    box = BoxConfig(
        uid="box1", title="GC-1", csv_path="", qc_expire_hours=24.0,
        watched_targets=[WatchedTarget(sample="ContextA", test="Flash")],
        pos=(100.0, 200.0), size=(240.0, 130.0),
    )
    checklist = ChecklistSpec(
        uid="cl1", name="Daily", due_time="17:00",
        items=[ChecklistItem(text="Check gas", days_active=[0, 1, 2], uid="i1")],
    )
    return AppConfig(
        version=CURRENT_VERSION, poll_minutes=7, map_locked=True,
        sample_id_column="Lab ID", samples=[sample], boxes=[box],
        users=[UserSpec(username="alice", password="secret")],
        checklists=[checklist], theme_mode="dark", report_time="16:30",
        view_zoom=1.5,
    )


def test_load_on_empty_db_returns_default():
    gw = FakeLabCoreGateway()
    store = DbConfigStore(gw)
    cfg = store.load()
    assert isinstance(cfg, AppConfig)
    assert cfg.boxes == []
    assert cfg.samples == []
    assert cfg.version == CURRENT_VERSION


def test_save_then_load_round_trips():
    gw = FakeLabCoreGateway()
    store = DbConfigStore(gw)
    original = _rich_config()

    ok, msg = store.save(original)
    assert ok, msg

    loaded = store.load()
    assert loaded.serialize() == original.serialize()


def test_save_is_idempotent_full_rewrite():
    """Re-saving after removing a box must not leave the deleted box behind."""
    gw = FakeLabCoreGateway()
    store = DbConfigStore(gw)
    cfg = _rich_config()
    store.save(cfg)

    cfg.boxes = []
    store.save(cfg)

    loaded = store.load()
    assert loaded.boxes == []


def test_tables_created_through_gateway():
    gw = FakeLabCoreGateway()
    DbConfigStore(gw).save(_rich_config())
    for table in ("lem_meta", "lem_boxes", "lem_samples", "lem_users", "lem_checklists"):
        res = gw.read_sql(f"SELECT count(*) AS n FROM {table}")
        assert res.get("ok") is True, f"{table} not created: {res}"


def test_config_readable_by_another_program_via_plain_sql():
    """Other lab programs must be able to read LEM's equipment straight from SQL."""
    gw = FakeLabCoreGateway()
    DbConfigStore(gw).save(_rich_config())
    rows = gw.read_sql("SELECT uid FROM lem_boxes ORDER BY uid")["rows"]
    assert rows == [{"uid": "box1"}]
