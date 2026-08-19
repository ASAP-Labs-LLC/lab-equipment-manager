"""LabCore holds the configuration; this module holds only the binding.

Nothing is stored on this PC any more. What LabStation saves with the canvas is
the machine's uid — which instrument this module *is* — and the config is pulled
from `lem_machine_config` on load. Consequences:

  * a config deleted from the floor clears this module (it has none)
  * a LabStation reinstall keeps the setup, because the setup was never here
  * an existing canvas saved with the old inline config still has to work, so
    the first load adopts it and publishes it up

The one thing that must never happen: a LabCore outage looking like a delete.
"""
import json
import re
from datetime import datetime

import pytest
from PySide6 import QtWidgets

import lem_station_module as mod
from test_module_qt import FakeContext, make_module, sample_machine


class FakeLabCore:
    """Records SQL and answers reads from a dict of canned results."""

    def __init__(self, config_rows=None, fail=False):
        self.statements = []
        self.config_rows = config_rows
        self.fail = fail

    def sql(self, sql, args=None, **kw):
        self.statements.append((sql, list(args or [])))
        return {"ok": True}

    def read_sql(self, sql, args=None, **kw):
        if self.fail:
            return {"error": "LabCore unreachable"}
        if "lem_machine_config" in sql:
            return {"ok": True, "rows": list(self.config_rows or [])}
        return {"ok": True, "rows": []}

    def is_running(self):
        return not self.fail


@pytest.fixture
def labcore(monkeypatch):
    """Install the injected labcore_* helpers the module expects."""
    fake = FakeLabCore()
    monkeypatch.setitem(mod.__dict__, "labcore_sql",
                        lambda sql, args=None, **kw: fake.sql(sql, args, **kw))
    monkeypatch.setitem(mod.__dict__, "labcore_read_sql",
                        lambda sql, args=None, **kw: fake.read_sql(sql, args, **kw))
    monkeypatch.setitem(mod.__dict__, "labcore_is_running", fake.is_running)
    monkeypatch.setitem(mod.__dict__, "labcore_write",
                        lambda op, params, **kw: {"ok": True})
    return fake


# ── what gets saved with the canvas ─────────────────────────────────────────

class TestOnlyTheBindingIsLocal:
    def test_state_carries_the_uid(self, qapp, tmp_path, labcore):
        m = make_module()
        m.set_machine(sample_machine(tmp_path))
        state = m.serialize_state()
        assert state["machine_uid"] == "m1"
        m.shutdown()

    def test_state_does_not_carry_the_config(self, qapp, tmp_path, labcore):
        """The whole point: mappings live in LabCore, not in the canvas file."""
        m = make_module()
        m.set_machine(sample_machine(tmp_path))
        state = m.serialize_state()
        assert "machine" not in state
        assert "mappings" not in json.dumps(state)
        m.shutdown()

    def test_an_unbound_module_saves_no_uid(self, qapp, labcore):
        m = make_module()
        assert not m.serialize_state().get("machine_uid")
        m.shutdown()

    def test_poll_interval_is_still_remembered(self, qapp, tmp_path, labcore):
        """That is a per-bench preference, not lab configuration."""
        m = make_module()
        m._poll_seconds = 120
        assert m.serialize_state()["poll_seconds"] == 120
        m.shutdown()


class TestRestoringABinding:
    def test_a_saved_uid_pulls_its_config_from_labcore(self, qapp, tmp_path,
                                                      labcore):
        stored = sample_machine(tmp_path, uid="m1", title="Eraspec")
        labcore.config_rows = [{"machine_uid": "m1", "title": "Eraspec",
                                "config": json.dumps(stored.to_dict())}]
        m = make_module()
        m.restore_state({"machine_uid": "m1"})
        assert m.machine() is not None
        assert m.machine().uid == "m1"
        assert m.machine().mappings[0].methods == ["RON"]
        m.shutdown()

    def test_a_legacy_inline_config_is_adopted_and_published(self, qapp,
                                                            tmp_path, labcore):
        """Existing canvases were saved with the config inline. They must not
        lose their setup — adopt it, then push it up so LabCore owns it."""
        legacy = sample_machine(tmp_path, uid="old1", title="Legacy Bench")
        m = make_module()
        m.restore_state({"machine": legacy.to_dict()})
        assert m.machine() is not None and m.machine().uid == "old1"
        published = [s for s, _a in labcore.statements
                     if "lem_machine_config" in s and "INSERT" in s]
        assert published, "the legacy config was not published to LabCore"
        m.shutdown()

    def test_an_empty_state_leaves_the_module_unconfigured(self, qapp, labcore):
        m = make_module()
        m.restore_state({})
        assert m.machine() is None
        m.shutdown()


# ── publishing changes up ───────────────────────────────────────────────────

class TestPublishing:
    def test_setting_a_machine_publishes_its_config(self, qapp, tmp_path,
                                                    labcore):
        m = make_module()
        m.set_machine(sample_machine(tmp_path))
        rows = [(s, a) for s, a in labcore.statements
                if "lem_machine_config" in s and "INSERT" in s]
        assert rows, "no config upsert was issued"
        _sql, args = rows[-1]
        assert args[0] == "m1" and args[1] == "Eraspec"
        assert json.loads(args[2])["mappings"][0]["methods"] == ["RON"]
        m.shutdown()

    def test_publishing_survives_labcore_being_down(self, qapp, tmp_path,
                                                    monkeypatch):
        """A bench must still come up when LabCore is unreachable."""
        def boom(*a, **kw):
            raise RuntimeError("LabCore unreachable")

        monkeypatch.setitem(mod.__dict__, "labcore_sql", boom)
        monkeypatch.setitem(mod.__dict__, "labcore_is_running", lambda: False)
        m = make_module()
        m.set_machine(sample_machine(tmp_path))       # must not raise
        assert m.machine() is not None
        m.shutdown()


# ── a config deleted on the floor clears this module ────────────────────────

class TestClearingWhenDeleted:
    def test_a_definite_deletion_clears_the_machine(self, qapp, tmp_path,
                                                   labcore):
        m = make_module()
        m.set_machine(sample_machine(tmp_path))
        labcore.config_rows = []                     # gone from LabCore
        m._check_config_still_exists()
        assert m.machine() is None
        m.shutdown()

    def test_it_stops_parsing_when_cleared(self, qapp, tmp_path, labcore):
        m = make_module()
        m.set_machine(sample_machine(tmp_path))
        m.start_polling() if hasattr(m, "start_polling") else None
        labcore.config_rows = []
        m._check_config_still_exists()
        assert m.machine() is None
        assert not m._timer.isActive()
        m.shutdown()

    def test_an_outage_does_not_clear_anything(self, qapp, tmp_path, labcore):
        """The failure that would wipe every bench in the lab at once."""
        m = make_module()
        m.set_machine(sample_machine(tmp_path))
        labcore.fail = True
        m._check_config_still_exists()
        assert m.machine() is not None
        assert m.machine().uid == "m1"
        m.shutdown()

    def test_a_config_still_present_is_left_alone(self, qapp, tmp_path,
                                                 labcore):
        stored = sample_machine(tmp_path)
        labcore.config_rows = [{"machine_uid": "m1", "title": "Eraspec",
                                "config": json.dumps(stored.to_dict())}]
        m = make_module()
        m.set_machine(stored)
        m._check_config_still_exists()
        assert m.machine() is not None
        m.shutdown()

    def test_an_unbound_module_checks_nothing(self, qapp, labcore):
        m = make_module()
        m._check_config_still_exists()               # must not raise
        assert m.machine() is None
        m.shutdown()


# ── the startup picker ──────────────────────────────────────────────────────

def picker(choices, adopt_answer="adopt", name="Copy"):
    """A picker with both modal prompts stubbed out."""
    d = mod._MachinePickerDialog(choices)
    d.confirm_in_use = lambda title: adopt_answer
    d.ask_name = lambda prompt, default="": name
    return d


CHOICES = [
    {"machine_uid": "m1", "title": "OptiMPP 1", "in_use": False,
     "updated_at": "2026-08-01T09:00:00"},
    {"machine_uid": "m2", "title": "Busy Bench", "in_use": True,
     "updated_at": "2026-08-02T09:00:00"},
]


class TestPickerLists:
    def test_it_lists_every_registered_machine(self, qapp):
        d = picker(CHOICES)
        assert d._list.count() == 2
        assert "OptiMPP 1" in d._list.item(0).text()

    def test_an_in_use_machine_is_marked(self, qapp):
        d = picker(CHOICES)
        assert "another LabStation" in d._list.item(1).text()

    def test_with_nothing_registered_only_new_is_offered(self, qapp):
        d = picker([])
        assert d._list.count() == 0
        assert d._new_btn.isEnabled()
        assert not d._adopt_btn.isEnabled()
        assert not d._dup_btn.isEnabled()


class TestPickerOutcomes:
    def test_adopting_an_idle_machine(self, qapp):
        d = picker(CHOICES)
        d._list.setCurrentRow(0)
        d._on_adopt()
        assert d.outcome == ("adopt", "m1", "OptiMPP 1")

    def test_duplicating_carries_the_new_name(self, qapp):
        d = picker(CHOICES, name="OptiMPP 3")
        d._list.setCurrentRow(0)
        d._on_duplicate()
        assert d.outcome == ("duplicate", "m1", "OptiMPP 3")

    def test_a_new_machine_just_needs_a_name(self, qapp):
        d = picker(CHOICES, name="Multitek 2")
        d._on_new()
        assert d.outcome == ("new", "", "Multitek 2")

    def test_declining_to_name_something_cancels_it(self, qapp):
        d = picker(CHOICES, name="")
        d._on_new()
        assert d.outcome is None

    def test_adopting_an_in_use_machine_warns_first(self, qapp):
        """Ryan's call: warn, but let it through."""
        d = picker(CHOICES, adopt_answer="adopt")
        d._list.setCurrentRow(1)
        d._on_adopt()
        assert d.outcome == ("adopt", "m2", "Busy Bench")

    def test_the_warning_can_send_you_to_duplicate_instead(self, qapp):
        d = picker(CHOICES, adopt_answer="duplicate", name="Busy Bench 2")
        d._list.setCurrentRow(1)
        d._on_adopt()
        assert d.outcome == ("duplicate", "m2", "Busy Bench 2")

    def test_the_warning_can_be_cancelled(self, qapp):
        d = picker(CHOICES, adopt_answer="cancel")
        d._list.setCurrentRow(1)
        d._on_adopt()
        assert d.outcome is None

    def test_an_idle_machine_is_never_warned_about(self, qapp):
        d = mod._MachinePickerDialog(CHOICES)
        d.confirm_in_use = lambda title: pytest.fail("should not warn")
        d._list.setCurrentRow(0)
        d._on_adopt()
        assert d.outcome[0] == "adopt"


class TestFetchingChoices:
    def test_it_merges_configs_with_liveness(self, qapp, monkeypatch, labcore):
        labcore.config_rows = [{"machine_uid": "m1", "title": "OptiMPP 1"}]

        def read(sql, args=None, **kw):
            if "lem_machine_heartbeat" in sql:
                return {"ok": True, "rows": [
                    {"machine_uid": "m1",
                     "last_poll": datetime.now().isoformat()}]}
            return labcore.read_sql(sql, args, **kw)

        monkeypatch.setitem(mod.__dict__, "labcore_read_sql", read)
        m = make_module()
        got = m.fetch_config_choices()
        assert got[0]["title"] == "OptiMPP 1"
        assert got[0]["in_use"] is True
        m.shutdown()

    def test_no_labcore_means_an_empty_picker_not_a_crash(self, qapp,
                                                         monkeypatch):
        monkeypatch.setitem(mod.__dict__, "labcore_read_sql", None)
        m = make_module()
        assert m.fetch_config_choices() == []
        m.shutdown()


# ── the injected helpers' signatures are NOT interchangeable ────────────────
#
# LabStation.pyw:
#     def labcore_sql(sql, args=None, source="LabStation", timeout=None)
#     def labcore_read_sql(sql, args=None, timeout=None)          ← no source!
#
# Passing source= to the reader raises TypeError, which the broad `except` around
# every LabCore call swallows — so the startup picker just came back empty and
# no config would load. The fakes below take the REAL signatures so a test can
# never again pass where production fails.

class StrictLabCore:
    """Mirrors LabStation's injected helpers exactly."""

    def __init__(self):
        self.reads = []

    def sql(self, sql, args=None, source="LabStation", timeout=None):
        return {"ok": True}

    def read_sql(self, sql, args=None, timeout=None):
        self.reads.append(sql)
        if "lem_machine_config" in sql:
            return {"ok": True, "rows": [
                {"machine_uid": "m1", "title": "OptiMPP 1",
                 "config": json.dumps({"uid": "m1", "title": "OptiMPP 1"})}]}
        return {"ok": True, "rows": []}

    def is_running(self):
        return True


@pytest.fixture
def strict(monkeypatch):
    fake = StrictLabCore()
    monkeypatch.setitem(mod.__dict__, "labcore_sql", fake.sql)
    monkeypatch.setitem(mod.__dict__, "labcore_read_sql", fake.read_sql)
    monkeypatch.setitem(mod.__dict__, "labcore_is_running", fake.is_running)
    monkeypatch.setitem(mod.__dict__, "labcore_write",
                        lambda op, params, **kw: {"ok": True})
    return fake


class TestAgainstTheRealSignatures:
    def test_no_read_ever_passes_source(self):
        """The mistake, caught at the source rather than at runtime."""
        src = open(mod.__file__, encoding="utf-8").read()
        for call in re.findall(r"read_sql\((?:[^()]|\([^()]*\))*\)", src):
            assert "source=" not in call, call

    def test_the_picker_actually_lists_machines(self, qapp, strict):
        m = make_module()
        choices = m.fetch_config_choices()
        assert [c["title"] for c in choices] == ["OptiMPP 1"]
        m.shutdown()

    def test_a_bound_config_actually_loads(self, qapp, strict):
        m = make_module()
        m.restore_state({"machine_uid": "m1"})
        assert m.machine() is not None and m.machine().title == "OptiMPP 1"
        m.shutdown()

    def test_publishing_still_works_with_the_real_writer(self, qapp, tmp_path,
                                                        strict):
        """`labcore_sql` DOES take source=, so writes must keep passing it."""
        m = make_module()
        m.set_machine(sample_machine(tmp_path))
        assert m.machine() is not None
        m.shutdown()


# ── the binding must survive a LabCore that isn't up yet ────────────────────

class TestABindingIsNotLostBecauseLabCoreWasSlow:
    """Reported 2026-08-17: "the modules upon labstation restart keep
    forgetting the machine that they are watching".

    They had not forgotten it. `_adopt_config` cannot tell "this config was
    deleted" from "I could not ask" — it parks the uid in `_pending_uid` and
    shows "Waiting for this machine's configuration from LabCore…" — and
    nothing ever read `_pending_uid` again. It was set in one place and cleared
    in one place, and no code path retried it, so the wait was permanent and
    the operator had to re-pick the instrument by hand.

    Restart is exactly when this bites: LabStation restores its layout through a
    QTimer chain at start-up, one module at a time, and the reads go through the
    same LabCore queue the whole lab shares — the one that has been backing up.
    A bench that came up a second before LabCore was ready lost its binding for
    the rest of the session.
    """

    def test_the_uid_is_remembered_when_labcore_cannot_answer(self, qapp,
                                                              labcore):
        labcore.fail = True
        m = make_module()
        m.restore_state({"machine_uid": "m1"})
        assert m.machine() is None, "nothing to bind to yet"
        assert m._pending_uid == "m1", "the binding was thrown away"
        m.shutdown()

    def test_it_binds_once_labcore_comes_up(self, qapp, tmp_path, labcore):
        """The fix: the parked uid is retried, and the bench comes back on its
        own without anybody touching it."""
        stored = sample_machine(tmp_path, uid="m1", title="Eraspec")
        labcore.fail = True
        m = make_module()
        m.restore_state({"machine_uid": "m1"})
        assert m.machine() is None

        labcore.fail = False
        labcore.config_rows = [{"machine_uid": "m1", "title": "Eraspec",
                                "config": json.dumps(stored.to_dict())}]
        m._retry_pending_bind()
        assert m.machine() is not None, (
            "the module never retried the binding it had parked")
        assert m.machine().uid == "m1"
        assert not m._pending_uid, "the pending uid was not cleared on success"
        m.shutdown()

    def test_a_retry_is_actually_scheduled(self, qapp, labcore):
        """Not just retryable — retried. Nobody is going to call it by hand."""
        labcore.fail = True
        m = make_module()
        m.restore_state({"machine_uid": "m1"})
        assert m._bind_retry_timer.isActive(), (
            "no retry was scheduled, so the bench waits forever")
        m.shutdown()

    def test_it_keeps_retrying_while_labcore_stays_down(self, qapp, labcore):
        labcore.fail = True
        m = make_module()
        m.restore_state({"machine_uid": "m1"})
        for _ in range(3):
            m._retry_pending_bind()
        assert m._pending_uid == "m1"
        assert m._bind_retry_timer.isActive(), "the retry gave up"
        m.shutdown()

    def test_the_wait_backs_off_rather_than_hammering_the_queue(self, qapp,
                                                               labcore):
        """The reads go through the queue that is already congested — that is
        why the bind failed. Retrying every second would make it worse."""
        labcore.fail = True
        m = make_module()
        m.restore_state({"machine_uid": "m1"})
        first = m._bind_retry_timer.interval()
        for _ in range(4):
            m._retry_pending_bind()
        assert m._bind_retry_timer.interval() > first, "the retry never backed off"
        assert m._bind_retry_timer.interval() <= mod.BIND_RETRY_MAX_SECONDS * 1000

    def test_an_operator_picking_a_machine_stops_the_retry(self, qapp, tmp_path,
                                                          labcore):
        """Otherwise a retry lands later and swaps the instrument underneath
        somebody who has just chosen one."""
        labcore.fail = True
        m = make_module()
        m.restore_state({"machine_uid": "m1"})
        labcore.fail = False
        m.set_machine(sample_machine(tmp_path, uid="m2", title="Other"))
        assert not m._pending_uid
        assert not m._bind_retry_timer.isActive()
        assert m.machine().uid == "m2"
        m.shutdown()

    def test_a_definitively_deleted_config_still_clears(self, qapp, labcore):
        """The guard that must not be lost: an explicit empty answer is a
        delete, and retrying forever would hide it."""
        labcore.fail = False
        labcore.config_rows = []
        m = make_module()
        m.restore_state({"machine_uid": "gone"})
        assert m.machine() is None
        m.shutdown()

    def test_the_timer_itself_brings_the_bench_back(self, qapp, tmp_path,
                                                    labcore):
        """End to end through the real timer, not by calling the retry by hand:
        LabCore is down at restore, comes up, and the bench binds itself."""
        stored = sample_machine(tmp_path, uid="m1", title="Eraspec")
        labcore.fail = True
        m = make_module()
        m.restore_state({"machine_uid": "m1"})
        assert m.machine() is None

        labcore.fail = False
        labcore.config_rows = [{"machine_uid": "m1", "title": "Eraspec",
                                "config": json.dumps(stored.to_dict())}]
        m._bind_retry_timer.setInterval(0)      # fire on the next event loop
        m._bind_retry_timer.start()
        for _ in range(50):
            QtWidgets.QApplication.processEvents()
            if m.machine() is not None:
                break
        assert m.machine() is not None, (
            "the scheduled retry never ran, so the bench stayed unbound")
        assert m.machine().uid == "m1"
        m.shutdown()
