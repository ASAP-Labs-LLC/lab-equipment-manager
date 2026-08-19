"""Tests for the LabCore sync layer, v2 model: parsed prints → LabCore,
machine status → lem_machine_status, QC specs ← lem_qc_specs, and
overrides ← lem_machine_control. The module never defines QC tests itself.
"""
from datetime import datetime

import pytest

import lem_station_module as mod
from lem_station_module import (
    STATUS_DEAD,
    STATUS_GREEN,
    STATUS_SERVICE,
    Machine,
    MachineEvaluation,
    build_result_cells,
    build_status_upsert,
    extract_overrides,
)

from test_module_qt import FakeContext, make_module, sample_machine

NOW = datetime(2026, 7, 27, 12, 0, 0)


def machine():
    return Machine(uid="m1", title="Eraspec", csv_path="/tmp/in.csv")


# ── build_result_cells (rows are method-keyed — no CSV headers) ──────────────
#
# The only builder of LabCore result ops there is. It replaced
# build_labcore_batch, whose first op per row was an `insert_sample` under the
# Lab ID the INSTRUMENT printed — which minted a phantom "34566" beside the
# LIMS's "081126-34566", left the LIMS record blank forever, and made the
# reading invisible under the shipped date filter. The Lab ID here is the one
# LabCore itself answered with, and a row LabCore could not place produces no
# op at all: the caller holds it.

class TestBuildResultCells:
    def test_update_cells_per_row_under_the_resolved_identity(self):
        rows = [{"Lab ID": "34566", "RON": "91.2", "MON": "90.1",
                 "parsed_date": "2026-07-27", "parsed_time": "11:00:00"}]
        ops = build_result_cells(rows, {"34566": "081126-34566"})
        assert not [op for op in ops if op["operation"] != "update_cell"], (
            "LEM must never invent a sample identity")
        assert {"operation": "update_cell",
                "params": {"lab_id": "081126-34566", "test_name": "RON",
                           "value": "91.2"}} in ops
        assert {"operation": "update_cell",
                "params": {"lab_id": "081126-34566", "test_name": "MON",
                           "value": "90.1"}} in ops

    def test_skips_lab_id_and_timestamp_keys(self):
        rows = [{"Lab ID": "25-001", "RON": "91.2",
                 "parsed_date": "2026-07-27", "parsed_time": "11:00:00"}]
        touched = {op["params"].get("test_name")
                   for op in build_result_cells(rows, {"25-001": "25-001"})
                   if op["operation"] == "update_cell"}
        assert touched == {"RON"}

    def test_skips_rows_without_lab_id_empty_values_and_unplaced_samples(self):
        rows = [
            {"Lab ID": "", "RON": "91.2"},
            {"Lab ID": "25-002", "RON": "", "MON": "89.9"},
            {"Lab ID": "25-003", "RON": "88.0"},      # not in `samples` yet
        ]
        ops = build_result_cells(rows, {"25-002": "25-002"})
        assert ops == [{"operation": "update_cell",
                        "params": {"lab_id": "25-002", "test_name": "MON",
                                   "value": "89.9"}}]

    def test_empty_rows_give_empty_batch(self):
        assert build_result_cells([], {}) == []


# ── build_status_upsert ──────────────────────────────────────────────────────

class TestBuildStatusUpsert:
    def test_upsert_sql_and_args(self):
        ev = MachineEvaluation(status=STATUS_GREEN, reason="System nominal")
        sql, args = build_status_upsert(machine(), ev, NOW)
        assert "lem_machine_status" in sql
        assert "ON CONFLICT" in sql.upper()
        assert args == ["m1", "Eraspec", STATUS_GREEN, "System nominal",
                        "2026-07-27T12:00:00"]


# ── extract_overrides ────────────────────────────────────────────────────────

class TestExtractOverrides:
    def test_maps_uid_to_valid_override(self):
        rows = [{"machine_uid": "m1", "manual_override": STATUS_SERVICE},
                {"machine_uid": "m2", "manual_override": ""},
                {"machine_uid": "m3", "manual_override": STATUS_DEAD}]
        assert extract_overrides(rows) == {
            "m1": STATUS_SERVICE, "m2": "", "m3": STATUS_DEAD}

    def test_ignores_invalid_values_and_missing_uid(self):
        rows = [{"machine_uid": "m1", "manual_override": "BANANA"},
                {"machine_uid": "", "manual_override": STATUS_SERVICE},
                {"manual_override": STATUS_DEAD}]
        assert extract_overrides(rows) == {}


# ── Module-level sync behavior (fake injected labcore helpers) ───────────────

class FakeLabCore:
    """The injected labcore_* helpers, with a `samples` table.

    It serves one because the results road asks LabCore which sample a printed
    Lab ID actually is, and a fake that answered "no such table" would drop
    every test in this file onto the one road that does not: the last-resort map
    for a gateway that has no samples at all. That is a real road and it is
    covered on purpose below — but it must not be the road the whole suite
    silently runs on.
    """

    def __init__(self):
        self.writes = []       # (operation, params, source)
        self.sqls = []         # (sql, args, source)
        self.control_rows = []
        self.qc_spec_rows = []
        # The identities the LIMS has logged in.
        self.samples = ["QC1"]
        self.samples_error = ""

    def write(self, operation, params, source="LabStation"):
        self.writes.append((operation, params, source))
        return {"ok": True}

    def sql(self, sql, args=None, source="LabStation"):
        self.sqls.append((sql, args, source))
        return {"ok": True, "rows_affected": 1}

    def read_sql(self, sql, args=None):
        if "lem_machine_control" in sql:
            return {"ok": True, "rows": list(self.control_rows),
                    "columns": ["machine_uid", "manual_override"]}
        if "lem_qc_specs" in sql:
            return {"ok": True, "rows": list(self.qc_spec_rows),
                    "columns": ["machine_uid", "test_name", "sample_id",
                                "expected", "std_dev", "k", "units"]}
        if 'FROM "samples"' in sql:
            if self.samples_error:
                return {"error": self.samples_error}
            keys = {str(a).lower() for a in (args or [])}
            return {"ok": True, "columns": ["lab_id"],
                    "rows": [{"lab_id": s} for s in self.samples
                             if s.lower() in keys
                             or s.lower().lstrip("0") in keys
                             or any(s.lower().endswith("-" + k) for k in keys)]}
        return {"error": "no such table"}

    def is_running(self):
        return True


@pytest.fixture
def fake_labcore(monkeypatch):
    fake = FakeLabCore()
    monkeypatch.setattr(mod, "labcore_write", fake.write, raising=False)
    monkeypatch.setattr(mod, "labcore_sql", fake.sql, raising=False)
    monkeypatch.setattr(mod, "labcore_read_sql", fake.read_sql, raising=False)
    monkeypatch.setattr(mod, "labcore_is_running", fake.is_running, raising=False)
    return fake


class TestModuleLabCoreSync:
    def test_parsed_prints_are_pushed_as_batch(self, qapp, tmp_path,
                                               fake_labcore):
        """The reading goes out under the identity LabCore confirmed, and
        nothing mints a sample.

        This used to assert the opposite — that the batch contained an
        `insert_sample` for the printed Lab ID — and it passed only because the
        fake answered the samples lookup with an error. That is the phantom
        sample the whole results road exists to stop.
        """
        m = make_module()
        (tmp_path / "in.csv").write_text("QC1,91.2\n")
        m.set_machine(sample_machine(tmp_path))
        m.process_now(now=NOW)
        batches = [w for w in fake_labcore.writes if w[0] == "batch"]
        assert len(batches) == 1
        ops = batches[0][1]["operations"]
        assert [op["operation"] for op in ops] == ["update_cell"], (
            "LEM must emit update_cell and nothing else — no invented samples")
        assert ops[0]["params"] == {"lab_id": "QC1", "test_name": "RON",
                                    "value": "91.2"}
        assert batches[0][2] == "LEM Station"
        m.shutdown()

    def test_status_upserted_to_lem_machine_status(self, qapp, tmp_path,
                                                   fake_labcore):
        m = make_module()
        (tmp_path / "in.csv").write_text("QC1,91.2\n")
        # LabCore is the source of truth for specs — serve the RON spec.
        fake_labcore.qc_spec_rows = [
            {"machine_uid": "m1", "test_name": "RON", "sample_id": "QC1",
             "expected": 91.0, "std_dev": 0.5, "k": 2.0, "units": ""}]
        m.set_machine(sample_machine(tmp_path))
        m.process_now(now=NOW)
        upserts = [s for s in fake_labcore.sqls if "lem_machine_status" in s[0]
                   and "INSERT" in s[0].upper()]
        assert upserts, "expected a status upsert"
        assert STATUS_GREEN in upserts[-1][1]
        m.shutdown()

    def test_qc_specs_are_pulled_from_labcore(self, qapp, tmp_path,
                                              fake_labcore):
        # Machine starts with NO specs — they must come from LabCore.
        m = make_module()
        mach = sample_machine(tmp_path, tests=[])
        (tmp_path / "in.csv").write_text("QC1,91.2\n")
        m.set_machine(mach)
        fake_labcore.qc_spec_rows = [
            {"machine_uid": "m1", "test_name": "RON", "sample_id": "QC1",
             "expected": 91.0, "std_dev": 0.5, "k": 2.0, "units": ""}]
        m.process_now(now=NOW)
        assert [t.name for t in mach.tests] == ["RON"]
        assert m.evaluation().status == STATUS_GREEN
        assert [r.test_name() for r in m.card().qc_rows()] == ["RON"]
        m.shutdown()

    def test_control_override_from_master_view_is_applied(self, qapp, tmp_path,
                                                          fake_labcore):
        m = make_module()
        mach = sample_machine(tmp_path)
        (tmp_path / "in.csv").write_text("QC1,91.2\n")
        m.set_machine(mach)
        fake_labcore.control_rows = [
            {"machine_uid": mach.uid, "manual_override": STATUS_DEAD}]
        m.process_now(now=NOW)
        assert mach.manual_override == STATUS_DEAD
        assert m.evaluation().status == STATUS_DEAD
        m.shutdown()

    def test_status_written_only_when_it_changes(self, qapp, tmp_path,
                                                 fake_labcore):
        m = make_module()
        (tmp_path / "in.csv").write_text("QC1,91.2\n")
        fake_labcore.qc_spec_rows = [
            {"machine_uid": "m1", "test_name": "RON", "sample_id": "QC1",
             "expected": 91.0, "std_dev": 0.5, "k": 2.0, "units": ""}]
        m.set_machine(sample_machine(tmp_path))
        m.process_now(now=NOW)

        def status_writes():
            return [s for s in fake_labcore.sqls
                    if "lem_machine_status" in s[0] and "INSERT" in s[0].upper()]

        assert len(status_writes()) == 1
        # Idle sync ticks: nothing new arrived, status unchanged → NO writes.
        m.process_now(now=NOW)
        m.process_now(now=NOW)
        assert len(status_writes()) == 1
        # A new out-of-spec result changes the status → one more write.
        from datetime import timedelta
        with open(tmp_path / "in.csv", "a") as f:
            f.write("QC1,93.5\n")
        m.process_now(now=NOW + timedelta(minutes=5))
        assert m.evaluation().status == mod.STATUS_RED
        assert len(status_writes()) == 2
        m.shutdown()

    def test_no_labcore_helpers_is_graceful(self, qapp, tmp_path):
        # No injected helpers: cached specs still evaluate locally.
        m = make_module()
        (tmp_path / "in.csv").write_text("QC1,91.2\n")
        m.set_machine(sample_machine(tmp_path))
        m.process_now(now=NOW)  # must not raise
        assert m.evaluation().status == STATUS_GREEN
        m.shutdown()


# ── Background sync: LabCore traffic must run OFF the UI thread ──────────────

class DeferredRunner:
    """Stand-in for LabStation's _run_in_thread that lets a test control
    exactly when the worker and its callback execute."""

    def __init__(self):
        self.jobs = []

    def __call__(self, fn, callback):
        self.jobs.append((fn, callback))

    def run_all(self):
        while self.jobs:
            fn, callback = self.jobs.pop(0)
            callback(fn())


class TestBackgroundSync:
    def test_poll_does_labcore_traffic_in_worker_and_ui_in_callback(
            self, qapp, tmp_path, fake_labcore, monkeypatch):
        runner = DeferredRunner()
        monkeypatch.setattr(mod, "_run_in_thread", runner, raising=False)
        fake_labcore.qc_spec_rows = [
            {"machine_uid": "m1", "test_name": "RON", "sample_id": "QC1",
             "expected": 91.0, "std_dev": 0.5, "k": 2.0, "units": ""}]
        ctx = FakeContext()
        m = make_module(ctx)
        (tmp_path / "in.csv").write_text("QC1,91.2\n")
        m.set_machine(sample_machine(tmp_path))
        # set_machine publishes the config to LabCore in the worker; that job
        # is not what this test is about.
        runner.jobs.clear()

        m.poll_now()
        assert len(runner.jobs) == 1          # dispatched, not yet run
        assert m.evaluation() is None         # UI untouched so far
        batch_writes = lambda: [w for w in fake_labcore.writes
                                if w[0] == "batch"]
        assert batch_writes() == []

        fn, callback = runner.jobs.pop(0)
        payload = fn()                        # ← the worker half
        assert batch_writes(), "HTTP writes must happen inside the worker"
        assert m.evaluation() is None         # still no UI work

        callback(payload)                     # ← the main-thread half
        assert m.evaluation().status == STATUS_GREEN
        assert ("QC1", "RON", "91.2", "LEM Station") in ctx.results
        assert not m._polling
        m.shutdown()

    def test_worker_exceptions_do_not_wedge_polling(self, qapp, tmp_path,
                                                    monkeypatch):
        runner = DeferredRunner()
        monkeypatch.setattr(mod, "_run_in_thread", runner, raising=False)
        m = make_module()
        m.set_machine(sample_machine(tmp_path))
        runner.jobs.clear()                   # drop the config-publish job
        monkeypatch.setattr(m, "_ingest",
                            lambda mach: (_ for _ in ()).throw(RuntimeError("boom")))
        m.poll_now()
        fn, callback = runner.jobs.pop(0)
        callback(fn())                        # work() must swallow the error
        assert not m._polling                 # next poll can still run
        assert m.evaluation().status == mod.STATUS_UNKNOWN
        m.shutdown()

    def test_dialog_opens_without_blocking_on_method_fetch(
            self, qapp, tmp_path, monkeypatch):
        reads = []

        def recording_read(sql, args=None):
            reads.append(sql)
            if "test_name" in sql and "sample_tests" in sql:
                return {"ok": True, "rows": [{"test_name": "RON"}]}
            return {"error": "no such table"}

        monkeypatch.setattr(mod, "labcore_read_sql", recording_read,
                            raising=False)
        runner = DeferredRunner()
        monkeypatch.setattr(mod, "_run_in_thread", runner, raising=False)

        d = mod._MachineDialog(Machine(uid="x", template="a,b"), None)
        assert reads == []                    # construction did NOT block
        assert d._methods_loaded is False
        runner.run_all()                      # background fetch completes
        assert d._methods == ["RON"]
        assert d._methods_loaded is True
