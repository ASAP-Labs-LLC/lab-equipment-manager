"""TDD for DbConfigStore — LEM's config living in the central DB.

The store persists the full AppConfig into namespaced lem_* tables through the
LabCore write queue and loads it back. Round-trip fidelity is the invariant.
"""
import json


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


# ── the store the count of 37 missed (2026-08-25) ────────────────────────────
#
# `confirm-every-write` converted eight stores and left this one alone, still
# judging every write by `if "error" in res`. That test passes for the whole
# family of refusals LabCore's queue actually sends — most importantly the
# evidenced one, which DOES carry an "error" key but which the old `_check`
# only caught by accident, and the ones that do not carry one at all.
#
# What makes it the worst place in the app to have missed is not the reporting.
# `_rewrite_rows` was DELETE-the-whole-table then re-INSERT every row, with no
# transaction across the statements — LabCore's queue takes one at a time. So a
# refusal that landed between the two halves left the table EMPTY and answered
# `(True, "OK")`. Every QC standard, every box, every user in LEM's config,
# gone, reported saved.

REFUSAL = {"error": "LabCore is busy, try again later", "busy": True,
           "retry_after": 4}


class HalfWay:
    """A LabCore that accepts a while and then refuses.

    `after` writes go through; everything past that is ANSWERED with the
    measured refusal. That is the shape of a queue filling up under a bulk
    save, which is exactly when a full-table rewrite is in flight.
    """

    def __init__(self, real, after):
        self.real = real
        self.left = after
        self.refused = []

    def sql(self, sql, args=None, **kw):
        if self.left <= 0:
            self.refused.append(sql)
            return dict(REFUSAL)
        self.left -= 1
        return self.real.sql(sql, args, **kw)

    def read_sql(self, sql, args=None, **kw):
        return self.real.read_sql(sql, args, **kw)

    def is_running(self):
        return True


def _saved_names(gw, table="lem_samples"):
    res = gw.read_sql(f"SELECT data FROM {table}")
    return sorted(json.loads(r["data"])["name"] for r in res.get("rows") or [])


def test_a_refused_write_is_never_reported_as_saved():
    """The reporting half. `if "error" in res` is not the rule."""
    gw = FakeLabCoreGateway()
    store = DbConfigStore(gw)
    store.save(_rich_config())

    blocked = HalfWay(gw, after=0)
    blocked_store = DbConfigStore(blocked)
    blocked_store._schema_ready = True        # the tables already exist
    ok, msg = blocked_store.save(_rich_config())
    assert ok is False
    assert "busy" in msg.lower() or "not" in msg.lower(), msg


def test_a_refusal_part_way_through_never_empties_the_table():
    """THE DATA-LOSS SHAPE, and the reason this is a blocker rather than a
    reporting nit.

    A save that is refused mid-flight must leave the config it could not
    replace, not the empty table a DELETE-then-INSERT leaves behind. Losing the
    new values is a save the operator can repeat; losing the OLD ones is a
    lab's QC library gone, and nothing on the floor can put it back.
    """
    gw = FakeLabCoreGateway()
    DbConfigStore(gw).save(_rich_config())
    assert _saved_names(gw) == ["ContextA"]

    # A second, larger config. The queue accepts the first couple of statements
    # and then fills up — whatever "the first couple" happen to be.
    bigger = _rich_config()
    bigger.samples = list(bigger.samples) + [
        SampleSpec(name="ContextB", sample_id_val="STD-2", tests=[]),
        SampleSpec(name="ContextC", sample_id_val="STD-3", tests=[]),
    ]
    for accept in range(0, 8):
        fresh = FakeLabCoreGateway()
        DbConfigStore(fresh).save(_rich_config())
        blocked = HalfWay(fresh, after=accept)
        store = DbConfigStore(blocked)
        store._schema_ready = True
        ok, _msg = store.save(bigger)
        surviving = _saved_names(fresh)
        if ok:
            continue
        assert surviving, (
            f"with {accept} statements accepted, a refused save emptied "
            f"lem_samples — the old library is gone and the new one never "
            f"landed")


def test_a_save_that_is_accepted_still_removes_what_was_deleted():
    """The rewrite still has to be a REWRITE.

    Making it non-destructive must not make it non-authoritative: dropping a QC
    standard in the UI has to actually drop it, or the config file's whole
    "the list is the list" contract goes. This is what stops the fix above
    being "just never delete anything".
    """
    gw = FakeLabCoreGateway()
    store = DbConfigStore(gw)
    cfg = _rich_config()
    cfg.samples = list(cfg.samples) + [
        SampleSpec(name="ContextB", sample_id_val="STD-2", tests=[])]
    assert store.save(cfg)[0]
    assert _saved_names(gw) == ["ContextA", "ContextB"]

    cfg.samples = [s for s in cfg.samples if s.name == "ContextB"]
    assert store.save(cfg)[0]
    assert _saved_names(gw) == ["ContextB"]


def test_emptying_a_list_really_empties_it():
    """The edge the prune has to get right: no rows left means delete them all,
    which is a deliberate act and not the accident above."""
    gw = FakeLabCoreGateway()
    store = DbConfigStore(gw)
    assert store.save(_rich_config())[0]
    assert _saved_names(gw) == ["ContextA"]

    cfg = _rich_config()
    cfg.samples = []
    assert store.save(cfg)[0]
    assert _saved_names(gw) == []


def test_a_refusal_with_no_error_key_is_still_a_refusal():
    """A shape `if "error" in res` was blind to. Kept minimal on purpose —
    what matters is that the verdict comes from `labcore_result`, not from a
    key test re-derived here.

    SYNTHETIC (tests/refusal_shapes.py): chosen because it carries no "error"
    key, not because LabCore has been recorded sending it. `REFUSAL` at the top
    of this section is the evidenced one and every other test here drives it.
    """
    gw = FakeLabCoreGateway()
    DbConfigStore(gw).save(_rich_config())

    class NoErrorKey(HalfWay):
        def sql(self, sql, args=None, **kw):
            if self.left <= 0:
                return {"ok": False}
            self.left -= 1
            return self.real.sql(sql, args, **kw)

    store = DbConfigStore(NoErrorKey(gw, after=0))
    store._schema_ready = True
    ok, _msg = store.save(_rich_config())
    assert ok is False
