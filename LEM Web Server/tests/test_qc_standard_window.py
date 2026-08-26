"""A QC standard carries its own staleness window, and it reaches the bench.

Ryan, 2026-08-26: *"make the QC staleness adjustable in the QC sample library."*

**Why it belongs on the standard.** A control's usable life is a property of the
MATERIAL. A working standard degrades; an ampoule opened this morning is not good
for a week. Until now the only places that could say so were per-INSTRUMENT — the
machine default (`BoxConfig.qc_expire_hours`) and the bench mapping
(`MethodMapping.qc_expire_hours`) — so the same fact had to be re-typed on every
bench that runs the standard, and a lot change could not carry it.

The precedence chain, most specific first:

    MethodMapping override  (an explicit human act on this instrument)
      -> the standard's own window                                  (NEW)
        -> the machine default
          -> 24.0

**Zero means "fall through" at every level**, exactly as `MethodMapping` and
`TestSpec` already behave. That rule is the whole safety story here: a standard
that says nothing about its own life must not make every reading instantly stale,
and neither must a bench on an older build that never sends the field at all.

**No migration.** `lem_qc_samples.tests` is a JSON TEXT column
(`qc_samples.QC_SAMPLES_DDL`) and the window rides INSIDE it, so no `lem_*`
column is added or renamed and `SCHEMA_MIGRATIONS` gains nothing. This file
proves that rather than asserting it: it reads the DDL and the snapshot arm and
checks neither names the field.

Fixtures here are built by the REAL writer — `QcSampleStore.save` — and read back
through the real snapshot and the real endpoint. Nothing in this file types a
JSON blob by hand and calls it the shape LabCore holds.
"""
import json

import pytest

from labcore_gateway import FakeLabCoreGateway
from live_presence import LivePresence


class CountingGateway(FakeLabCoreGateway):
    """Reports how often anything reached LabCore — the guard
    `test_bench_config.py` and `test_status_gutter.py` both use."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def sql(self, *a, **k):
        self.calls += 1
        return super().sql(*a, **k)

    def read_sql(self, *a, **k):
        self.calls += 1
        return super().read_sql(*a, **k)

    def write(self, *a, **k):
        self.calls += 1
        return super().write(*a, **k)


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


@pytest.fixture
def gw():
    return CountingGateway()


@pytest.fixture
def app(gw):
    from web_app import create_app
    application = create_app(gw, authenticator=StubAuth(), secret="s",
                             live=LivePresence(), live_token="test-token")
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


# ── 1. the field exists on the standard's test spec ─────────────────────────

class TestTheStandardCanStateItsOwnWindow:
    def test_a_test_spec_carries_a_window(self):
        from qc_samples import QcSampleTest
        t = QcSampleTest(name="Flash Point", value_col="Flash Point",
                         expected=62.5, std_dev=0.8, qc_expire_hours=8.0)
        assert t.qc_expire_hours == 8.0

    def test_saying_nothing_is_zero_which_means_fall_through(self):
        """NOT "instantly stale". Every other level in this codebase reads 0 as
        "no opinion", and a standard is the level most likely to say nothing."""
        from qc_samples import QcSampleTest
        assert QcSampleTest(name="Flash Point").qc_expire_hours == 0.0

    def test_the_window_survives_a_round_trip_through_the_dict(self):
        from qc_samples import QcSampleTest
        t = QcSampleTest(name="Flash Point", qc_expire_hours=8.0)
        assert t.to_dict()["qc_expire_hours"] == 8.0
        assert QcSampleTest.from_dict(t.to_dict()).qc_expire_hours == 8.0

    def test_an_older_row_with_no_window_reads_as_fall_through(self):
        """The rows already in LabCore have no such key. Absence is 0.0 —
        never a crash, never a window of zero hours."""
        from qc_samples import QcSampleTest
        old = {"name": "Flash Point", "value_col": "Flash Point",
               "expected": 62.5, "std_dev": 0.8, "k": 2.0, "units": "C"}
        assert QcSampleTest.from_dict(old).qc_expire_hours == 0.0

    def test_a_nonsense_window_is_fall_through_not_an_exception(self):
        from qc_samples import QcSampleTest
        for junk in ("", None, "banana", float("nan"), float("inf"), -5):
            assert QcSampleTest.from_dict(
                {"name": "x", "qc_expire_hours": junk}).qc_expire_hours == 0.0


class TestNoSchemaChangeIsNeeded:
    """`tests` is a JSON TEXT column, so the window is additive.

    Checked, not assumed. A column added to a shared `lem_*` table needs a
    `SCHEMA_MIGRATIONS` entry, and one that LabCore has not got fails the ENTIRE
    batched read and drops the floor to the fallback path — that has happened in
    production. If this ever stops being true, these fail.
    """

    def test_the_table_still_has_exactly_three_columns(self):
        from qc_samples import QC_SAMPLES_DDL
        assert QC_SAMPLES_DDL.count(",") == 2
        assert "qc_expire_hours" not in QC_SAMPLES_DDL

    def test_the_snapshot_arm_names_no_new_column(self):
        import snapshot_service
        arm = dict(snapshot_service._ARMS)["qcsample"]
        assert "qc_expire_hours" not in arm
        assert "tests AS c3" in arm

    def test_the_migration_list_is_untouched(self):
        from snapshot_service import SCHEMA_MIGRATIONS
        assert not [m for m in SCHEMA_MIGRATIONS if "qc_expire" in str(m)]

    def test_the_window_really_is_stored_inside_the_json_text(self, gw):
        """The writer's own output, read straight out of the column."""
        from qc_samples import QcSample, QcSampleTest, QcSampleStore
        store = QcSampleStore(gw)
        store.save(QcSample(name="Flash CRM", sample_id_val="L-9001",
                            tests=[QcSampleTest(name="Flash Point",
                                                value_col="Flash Point",
                                                expected=62.5, std_dev=0.8,
                                                qc_expire_hours=8.0)]))
        res = gw.read_sql("SELECT tests FROM lem_qc_samples WHERE name = ?",
                          ["Flash CRM"])
        raw = list(res["rows"])[0]["tests"]
        assert isinstance(raw, str)
        assert json.loads(raw)[0]["qc_expire_hours"] == 8.0

    def test_the_store_reads_the_window_back(self, gw):
        from qc_samples import QcSample, QcSampleTest, QcSampleStore
        store = QcSampleStore(gw)
        store.save(QcSample(name="Flash CRM", sample_id_val="L-9001",
                            tests=[QcSampleTest(name="Flash Point",
                                                qc_expire_hours=8.0)]))
        [back] = store.list_samples()
        assert back.tests[0].qc_expire_hours == 8.0


# ── 2. the chain, decided in one place ──────────────────────────────────────

class TestThePrecedenceChain:
    """`resolve_qc_window` is the ONE implementation in this tree.

    It answers with the number AND the level that supplied it, because a window
    silently assumed is a colour nobody can check — the same argument the status
    gutter already makes about reporting `qc_expire_source`.
    """

    def test_the_most_specific_level_wins(self):
        from qc_samples import resolve_qc_window
        assert resolve_qc_window(
            (("mapping", 4.0), ("standard", 8.0), ("machine", 12.0))
        ) == (4.0, "mapping")

    def test_zero_falls_through_to_the_next_level(self):
        from qc_samples import resolve_qc_window
        assert resolve_qc_window(
            (("mapping", 0.0), ("standard", 8.0), ("machine", 12.0))
        ) == (8.0, "standard")

    def test_zero_all_the_way_down_reaches_the_machine(self):
        from qc_samples import resolve_qc_window
        assert resolve_qc_window(
            (("mapping", 0.0), ("standard", 0.0), ("machine", 12.0))
        ) == (12.0, "machine")

    def test_nothing_at_all_is_the_shared_default_and_says_so(self):
        from qc_samples import QC_WINDOW_DEFAULT_HOURS, resolve_qc_window
        assert QC_WINDOW_DEFAULT_HOURS == 24.0
        assert resolve_qc_window(
            (("mapping", 0.0), ("standard", 0.0), ("machine", 0.0))
        ) == (24.0, "default")
        assert resolve_qc_window(()) == (24.0, "default")

    def test_an_absent_level_is_not_a_window_of_zero_hours(self):
        """The failure this whole feature could cause: absence read as 0 hours
        makes every reading in the lab instantly stale."""
        from qc_samples import resolve_qc_window
        for junk in (None, "", "banana", float("nan"), float("inf"), -1.0):
            assert resolve_qc_window(
                (("standard", junk), ("machine", 12.0))) == (12.0, "machine")

    def test_a_caller_may_stop_short_of_the_default(self):
        """Spec-building resolves mapping-vs-standard only; the machine level
        has not been consulted yet, so 24.0 would be a wrong answer, not a
        default."""
        from qc_samples import resolve_qc_window
        assert resolve_qc_window(
            (("mapping", 0.0), ("standard", 0.0)), default_hours=0.0
        ) == (0.0, "default")


class TestTheWindowTheAssignedStandardsSay:
    """`window_from_standards` — the library + this machine's assignments.

    It reads the SAME two lists the bench reads on the config road, so the floor
    and the bench cannot come to different conclusions about what the library
    says.
    """

    LIB = [{"name": "Flash CRM", "sample_id_val": "L-9001",
            "tests": json.dumps([{"name": "Flash Point", "qc_expire_hours": 8.0},
                                 {"name": "Density", "qc_expire_hours": 36.0}])},
           {"name": "Sulfur CRM", "sample_id_val": "L-9002",
            "tests": json.dumps([{"name": "Sulfur", "qc_expire_hours": 2.0}])}]

    def test_an_assigned_standards_window_is_found(self):
        from qc_samples import window_from_standards
        hours, what = window_from_standards(
            self.LIB, [{"sample_name": "Flash CRM", "test_name": "Flash Point"}])
        assert hours == 8.0
        assert "Flash CRM" in what and "Flash Point" in what

    def test_a_standard_this_machine_does_not_run_is_ignored(self):
        """Otherwise one tight standard somewhere in the library would shorten
        the window of every instrument in the lab."""
        from qc_samples import window_from_standards
        hours, _ = window_from_standards(
            self.LIB, [{"sample_name": "Flash CRM", "test_name": "Density"}])
        assert hours == 36.0

    def test_the_tightest_assigned_window_decides(self):
        """One window colours the whole instrument, and QC goes stale as soon as
        the shortest-lived control does — the same "any stale test" rule
        `evaluate_machine` applies."""
        from qc_samples import window_from_standards
        hours, what = window_from_standards(
            self.LIB, [{"sample_name": "Flash CRM", "test_name": "Flash Point"},
                       {"sample_name": "Flash CRM", "test_name": "Density"}])
        assert hours == 8.0
        assert "Flash Point" in what

    def test_no_assignment_says_nothing(self):
        from qc_samples import window_from_standards
        assert window_from_standards(self.LIB, []) == (0.0, "")

    def test_a_library_with_no_windows_says_nothing(self):
        from qc_samples import window_from_standards
        lib = [{"name": "Flash CRM", "sample_id_val": "L-9001",
                "tests": '[{"name": "Flash Point"}]'}]
        assert window_from_standards(
            lib, [{"sample_name": "Flash CRM",
                   "test_name": "Flash Point"}]) == (0.0, "")

    def test_unparseable_tests_json_says_nothing_rather_than_raising(self):
        from qc_samples import window_from_standards
        lib = [{"name": "Flash CRM", "sample_id_val": "L-9001",
                "tests": "{not json"}]
        assert window_from_standards(
            lib, [{"sample_name": "Flash CRM",
                   "test_name": "Flash Point"}]) == (0.0, "")

    def test_an_already_parsed_tests_list_works_too(self):
        """`QcSampleStore.as_payload` hands lists; the config road hands TEXT.
        One function reads both rather than two that can drift."""
        from qc_samples import window_from_standards
        lib = [{"name": "Flash CRM", "sample_id_val": "L-9001",
                "tests": [{"name": "Flash Point", "qc_expire_hours": 6.0}]}]
        assert window_from_standards(
            lib, [{"sample_name": "Flash CRM",
                   "test_name": "Flash Point"}])[0] == 6.0

    def test_a_target_matches_on_the_measurement_column_too(self):
        """`specs_from_qc_samples` matches a target on `value_col` OR `name`;
        this has to agree with it or the two roads disagree about the same
        assignment."""
        from qc_samples import window_from_standards
        lib = [{"name": "Flash CRM", "sample_id_val": "L-9001",
                "tests": [{"name": "Flash - D93", "value_col": "Flash Point",
                           "qc_expire_hours": 6.0}]}]
        assert window_from_standards(
            lib, [{"sample_name": "Flash CRM",
                   "test_name": "Flash Point"}])[0] == 6.0


# ── 3. it reaches the bench, at zero LabCore ops ────────────────────────────

def seed(gw, hours=8.0):
    """One machine, one standard, one assignment — written by the real stores."""
    from qc_samples import QcSample, QcSampleTest, QcSampleStore
    from snapshot_service import SnapshotService

    SnapshotService(gw).ensure_schema()
    gw.sql("INSERT INTO lem_machine_status VALUES "
           "('pac-flash-2','PAC Flash 2','GREEN','','2026-08-26T09:00:00')")
    gw.sql("INSERT INTO lem_machine_targets VALUES "
           "('pac-flash-2','Flash CRM','Flash Point')")
    tests = [QcSampleTest(name="Flash Point", value_col="Flash Point",
                          expected=62.5, std_dev=0.8, k=2.0, units="C")]
    if hours is not None:
        tests[0].qc_expire_hours = hours
    QcSampleStore(gw).save(QcSample(name="Flash CRM", sample_id_val="L-9001",
                                    tests=tests))


def populate(app, gw, hours=8.0):
    seed(gw, hours)
    app.config["SNAPSHOTS"].refresh()


def bench(client, uid="pac-flash-2"):
    r = client.get(f"/api/bench/{uid}/config",
                   headers={"X-LEM-Token": "test-token"})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


class TestTheNumberTravelsTheConfigRoad:
    def test_the_bench_payload_carries_the_standards_window(self, app, gw,
                                                            client):
        populate(app, gw)
        body = bench(client)
        [sample] = body["qc_samples"]
        assert json.loads(sample["tests"])[0]["qc_expire_hours"] == 8.0

    def test_the_tests_column_is_still_the_json_text_labcore_stores(
            self, app, gw, client):
        """`parse_qc_sample_rows` calls `json.loads` on it. Handing over a list
        would raise there and empty the whole library."""
        populate(app, gw)
        assert isinstance(bench(client)["qc_samples"][0]["tests"], str)

    def test_the_payload_still_has_exactly_the_same_sections(self, app, gw,
                                                             client):
        populate(app, gw)
        assert set(bench(client)) == {
            "machine_uid", "snapshot_age_seconds", "override", "corrections",
            "qc_samples", "qc_targets", "qc_specs", "maintenance"}

    def test_serving_it_costs_zero_labcore_ops(self, app, gw, client):
        populate(app, gw)
        gw.calls = 0
        for _ in range(5):
            bench(client)
        assert gw.calls == 0

    def test_a_standard_that_says_nothing_ships_a_fall_through(self, app, gw,
                                                               client):
        populate(app, gw, hours=None)
        [sample] = bench(client)["qc_samples"]
        assert json.loads(sample["tests"])[0]["qc_expire_hours"] == 0.0

    def test_a_library_row_written_before_this_shipped_travels_untouched(
            self, app, gw, client):
        """The rows already in LabCore. The column is JSON TEXT and the arm
        passes it through verbatim, so the key is simply absent — which the
        bench reads as fall-through. Nothing on this road invents a 0."""
        from snapshot_service import SnapshotService
        SnapshotService(gw).ensure_schema()
        gw.sql("INSERT INTO lem_qc_samples VALUES "
               "('Old CRM','L-8000','[{\"name\": \"Flash Point\"}]')")
        app.config["SNAPSHOTS"].refresh()
        [sample] = [s for s in bench(client)["qc_samples"]
                    if s["name"] == "Old CRM"]
        assert sample["tests"] == '[{"name": "Flash Point"}]'


# ── 4. the bench actually judges by it (the module, loaded as LabStation does)

def station_module():
    """The station module, loaded the way `test_qc_window.py` loads it."""
    import importlib.util
    import pathlib
    path = (pathlib.Path(__file__).resolve().parent.parent.parent
            / "LEM Station Module" / "lem_station_module.py")
    if not path.exists():
        pytest.skip("station module not present next to the web server")
    spec = importlib.util.spec_from_file_location("_lem_mod_for_window_test",
                                                  path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestTheBenchJudgesByTheStandardsWindow:
    """End to end, across both trees, with nothing hand-shaped in between.

    The library is written by `QcSampleStore`, read by the snapshot, served by
    `/api/bench/<uid>/config`, and then fed to the module's OWN parsers exactly
    as `_refresh_config` feeds them.
    """

    def parse(self, app, gw, client, hours=8.0):
        populate(app, gw, hours)
        mod = station_module()
        body = bench(client)
        results = mod.floor_config_results(body, "pac-flash-2")
        assert results is not None, "the floor's answer was refused whole"
        library = mod.parse_qc_sample_rows(results["qc_samples"]["rows"])
        machine = mod.Machine(
            uid="pac-flash-2", title="PAC Flash 2",
            mappings=[mod.MethodMapping(methods=["Flash Point"])])
        # The same `sample_name` -> `sample` mapping `_labcore_sync` does on
        # the rows it hands `specs_from_qc_samples`.
        targets = [{"sample": r.get("sample_name"), "test": r.get("test_name")}
                   for r in results["targets"]["rows"]]
        specs = mod.specs_from_qc_samples(machine, library, targets)
        return mod, machine, specs

    def test_the_standards_window_lands_on_the_bench_spec(self, app, gw,
                                                          client):
        _mod, _machine, specs = self.parse(app, gw, client)
        assert [s.name for s in specs] == ["Flash Point"]
        assert specs[0].qc_expire_hours == 8.0

    def test_the_bench_can_say_which_level_supplied_it(self, app, gw, client):
        mod, machine, specs = self.parse(app, gw, client)
        assert mod.qc_window_for(specs[0], machine) == (8.0, "standard")

    def test_an_un_upgraded_library_falls_through_to_the_machine(
            self, app, gw, client):
        """The standard says nothing, so the machine's own 24h stands. It must
        NOT arrive as a zero-hour window."""
        mod, machine, specs = self.parse(app, gw, client, hours=None)
        assert specs[0].qc_expire_hours == 0.0
        assert mod.qc_window_for(specs[0], machine) == (24.0, "machine")


# ── 5. the status gutter stops guessing ─────────────────────────────────────

class TestTheGutterUsesTheStandardsWindow:
    """`GET /api/machines/<uid>/status-timeline` reported `"default"` because
    this server held no per-machine window. Now the standard's is reachable from
    the snapshot it already reads, so it uses it and names the source."""

    @pytest.fixture
    def seeded(self, app, gw):
        import test_status_gutter as gutter_fixture
        gutter_fixture.seed(gw, "pac-flash-2")
        return app, gw

    def gutter(self, client, query=""):
        r = client.get(
            f"/api/machines/pac-flash-2/status-timeline{query}")
        assert r.status_code == 200, r.get_json()
        return r.get_json()

    def test_with_no_standard_window_it_still_says_default(self, seeded,
                                                           client):
        """The existing contract, unchanged: nothing configured anywhere means
        the shared 24h, reported as the default."""
        app, gw = seeded
        app.config["SNAPSHOTS"].refresh()
        body = self.gutter(client)
        assert body["qc_expire_hours"] == 24.0
        assert body["qc_expire_source"] == "default"

    def test_the_standards_window_is_used_and_named(self, seeded, client):
        app, gw = seeded
        seed(gw, hours=8.0)
        app.config["SNAPSHOTS"].refresh()
        body = self.gutter(client)
        assert body["qc_expire_hours"] == 8.0
        assert body["qc_expire_source"] == "standard"
        assert "Flash CRM" in body["qc_expire_from"]

    def test_the_narrower_window_moves_the_colours(self, seeded, client):
        """Not decoration. 37246 sits 48h after the passing QC; at 8 hours it is
        stale where at 24 it was not."""
        from models import STATUS_GREEN, STATUS_YELLOW
        app, gw = seeded
        app.config["SNAPSHOTS"].refresh()
        was = {e["lab_id"]: e["status"] for e in self.gutter(client)["events"]}
        assert was["37248"] == STATUS_GREEN

        seed(gw, hours=1.5)
        app.config["SNAPSHOTS"].refresh()
        now = {e["lab_id"]: e["status"] for e in self.gutter(client)["events"]}
        assert now["37248"] == STATUS_YELLOW

    def test_an_explicit_request_still_wins(self, seeded, client):
        app, gw = seeded
        seed(gw, hours=8.0)
        app.config["SNAPSHOTS"].refresh()
        body = self.gutter(client, "?qc_expire_hours=48")
        assert body["qc_expire_hours"] == 48.0
        assert body["qc_expire_source"] == "request"

    def test_it_still_costs_zero_labcore_ops(self, seeded, client):
        app, gw = seeded
        seed(gw, hours=8.0)
        app.config["SNAPSHOTS"].refresh()
        gw.calls = 0
        self.gutter(client)
        assert gw.calls == 0


# ── 6. it is editable, and the box is never bare ────────────────────────────

class TestTheStandardsEditorCanSetIt:
    def test_the_route_stores_a_window_sent_with_a_test(self, app, gw, client):
        client.post("/api/login", json={"username": "k", "password": "good"})
        r = client.post("/api/qc-samples", json={
            "name": "Flash CRM", "sample_id_val": "L-9001",
            "tests": [{"name": "Flash Point", "value_col": "Flash Point",
                       "expected": 62.5, "std_dev": 0.8, "k": 2.0,
                       "units": "C", "qc_expire_hours": 8.0}]})
        assert r.status_code == 200, r.get_json()
        from qc_samples import QcSampleStore
        [back] = QcSampleStore(gw).list_samples()
        assert back.tests[0].qc_expire_hours == 8.0

    def test_the_listing_hands_the_window_back_to_the_editor(self, app, gw,
                                                             client):
        seed(gw, hours=8.0)
        body = client.get("/api/qc-samples").get_json()
        assert body["samples"][0]["tests"][0]["qc_expire_hours"] == 8.0

    def test_the_listing_says_what_the_default_would_be(self, app, gw, client):
        """A bare empty box is a question the operator cannot answer. The
        library states the fall-through so the UI can label it."""
        seed(gw, hours=None)
        body = client.get("/api/qc-samples").get_json()
        assert body["default_qc_expire_hours"] == 24.0
        assert body["samples"][0]["tests"][0]["qc_expire_hours"] == 0.0

    def test_a_negative_window_is_refused_rather_than_stored(self, app, gw,
                                                             client):
        """`std_dev < 0` and `k <= 0` are already refused here. A negative
        window is the same class of nonsense and would be read as
        fall-through, which silently discards what the operator typed."""
        from qc_samples import (QcSample, QcSampleTest, QcSampleStore)
        with pytest.raises(ValueError):
            QcSampleStore(gw).save(QcSample(
                name="Flash CRM", sample_id_val="L-9001",
                tests=[QcSampleTest(name="Flash Point",
                                    qc_expire_hours=-1.0)]))

    def test_a_changeover_carries_the_window_to_the_new_lot(self, app, gw):
        """A new lot inherits every spec from the old one. Losing the window
        here would quietly restore the 24h default across the lab on the day
        somebody changes lots — the exact failure changeover exists to
        prevent."""
        from qc_samples import (QcSample, QcSampleTest, QcSampleStore,
                                changeover)
        from snapshot_service import SnapshotService
        SnapshotService(gw).ensure_schema()      # changeover reads assignments
        store = QcSampleStore(gw)
        store.save(QcSample(name="Flash CRM", sample_id_val="L-9001",
                            tests=[QcSampleTest(name="Flash Point",
                                                qc_expire_hours=8.0)]))
        changeover(gw, "Flash CRM", "Flash CRM lot 2", "L-9002")
        new = {s.name: s for s in store.list_samples()}["Flash CRM lot 2"]
        assert new.tests[0].qc_expire_hours == 8.0


class TestTheEditorUiOffersTheField:
    """`templates/stations.html`, RENDERED through the app's own Jinja
    environment rather than read off disk.

    Note what this page is: `/stations` is a retired route that redirects to
    `/floor`, so this template is no longer served. It is still the QC-standards
    editor of record in this tree and is kept in step; the LIVE editor is the
    same dialog inside `templates/floor.html`, which is being rewritten by
    somebody else right now — see `TestTheLiveFloorEditorStillHasToCatchUp`.
    """

    @pytest.fixture
    def page(self, app):
        return app.jinja_env.get_template("stations.html").render(
            active="/stations")

    def test_the_tests_grid_has_a_window_column(self, page):
        assert "c-win" in page

    def test_the_saved_payload_includes_the_window(self, page):
        assert "qc_expire_hours" in page

    def test_the_column_is_labelled_with_the_fall_through_default(self, page):
        """Blank means "use the default", and the header says what that is
        rather than leaving an unexplained empty box."""
        assert "Expires (h)" in page
        assert "default_qc_expire_hours" in page

    def test_the_page_is_still_the_retired_one_it_was(self, client):
        """Guards the sentence above. If `/stations` is ever served again this
        goes red and the class docstring stops being true."""
        assert client.get("/stations").status_code in (301, 302)


class TestTheLiveFloorEditorStillHasToCatchUp:
    """A HANDOFF TRIPWIRE, not a feature test.

    `templates/floor.html` holds the QC-standards dialog people actually use,
    and its save builds each test row by hand:

        {name: a, value_col: a, expected: …, std_dev: …, k: …, units: …}

    with no `qc_expire_hours`. `QcSampleTest.from_dict` reads that absence as
    0.0 — correctly, as fall-through — so **editing any standard from the floor
    clears a window somebody set**. It is deliberately not fixed here: the floor
    renderer is being rewritten in parallel and this file may not touch it.

    That is NOT worked around on the server. Making a save that omits the key
    silently inherit the stored value would mean no client could ever clear a
    window, and it would hide this gap instead of reporting it.

    The two tests below are the handoff. When the floor gains the field, the
    first goes red and the second (strict xfail) goes red too — flip them into
    one ordinary assertion and delete this class docstring.
    """

    @pytest.fixture
    def floor_source(self):
        import pathlib
        return (pathlib.Path(__file__).resolve().parent.parent
                / "templates" / "floor.html").read_text(encoding="utf-8")

    def test_the_floor_save_still_drops_the_window(self, floor_source):
        head, _, tail = floor_source.partition("#sampleTests .trow")
        assert head, "the floor's QC-standard save moved — re-aim this tripwire"
        assert "qc_expire_hours" not in tail.split("/api/qc-samples")[0]

    @pytest.mark.xfail(strict=True, reason="the floor editor has not been "
                                           "given the window field yet")
    def test_the_floor_editor_offers_the_window(self, floor_source):
        assert "qc_expire_hours" in floor_source
