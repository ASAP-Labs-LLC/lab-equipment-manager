"""`GET /api/bench/<uid>/config` — a bench reading its configuration from us.

The rule that made the floor cheap is being extended from screens to benches.
`/api/live` proved a request never has to talk to LabCore; this proves the same
for the one road that was still open. Today every module asks LabCore directly
for its own QC samples, targets, specs, maintenance, corrections and manual
override, so **LabCore load grows with the number of benches** — and that is the
load that is knocking the shared write queue over. The database sits on an SMB
share and cannot move, so `read_sql` will always consume a write-queue slot;
the only lever left is asking fewer times.

This server already refreshes those tables in ONE `UNION ALL` every 12s at
constant cost, and it is co-located with LabCore. So the bench reads them from
memory here and LabCore sees nothing. Bench count stops mattering.

Two things this file pins hard, because both fail SILENTLY:

  * **zero LabCore ops.** `CountingGateway` is the same technique
    `test_live_endpoint.py` uses on the push path, and for the same reason.
  * **the exact row shapes.** The module feeds these lists straight into
    `parse_correction_rows`, `parse_qc_sample_rows`, `parse_qc_specs`,
    `parse_maint_rows` and `extract_overrides`, which read rows by column name.
    The snapshot flattens every table into `c1..c9`, so a row served as it is
    stored parses to nothing at all — no error, no log line, just a bench that
    quietly believes it has no QC. Key names are asserted literally.
"""
from datetime import datetime, timedelta

import pytest

from labcore_gateway import FakeLabCoreGateway
from live_presence import LivePresence


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


class CountingGateway(FakeLabCoreGateway):
    """A gateway that reports how often anything reached LabCore."""

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


def seed(gw):
    """Two machines' worth of configuration, plus the shared QC library.

    Two on purpose: a payload that is correct for one machine and also carries
    the other machine's rows is the failure that puts a bench on somebody else's
    QC limits, and a single-machine fixture cannot see it.
    """
    from snapshot_service import SnapshotService
    SnapshotService(gw).ensure_schema()          # every table the arms name

    gw.sql("INSERT INTO lem_machine_status VALUES "
           "('pac-flash-2','PAC Flash 2','GREEN','','2026-08-26T09:00:00')")
    gw.sql("INSERT INTO lem_machine_status VALUES "
           "('multitek-ns','Multitek NS','GREEN','','2026-08-26T09:00:00')")

    gw.sql("INSERT INTO lem_machine_control VALUES "
           "('pac-flash-2','SERVICE','bulb','2026-08-26T09:00:00')")
    gw.sql("INSERT INTO lem_machine_control VALUES "
           "('multitek-ns','DEAD','','2026-08-26T09:00:00')")

    gw.sql("INSERT INTO lem_correction_factors VALUES "
           "('pac-flash-2','Flash Point',-3.0,'C','2026-08-26T09:00:00','ryan')")
    gw.sql("INSERT INTO lem_correction_factors VALUES "
           "('multitek-ns','Sulfur',0.5,'ppm','2026-08-26T09:00:00','ryan')")

    gw.sql("INSERT INTO lem_qc_samples VALUES "
           "('Flash CRM','L-9001','[{\"name\": \"Flash Point\"}]')")

    gw.sql("INSERT INTO lem_machine_targets VALUES "
           "('pac-flash-2','Flash CRM','Flash Point')")
    gw.sql("INSERT INTO lem_machine_targets VALUES "
           "('multitek-ns','Sulfur CRM','Sulfur')")

    gw.sql("INSERT INTO lem_qc_specs VALUES "
           "('pac-flash-2','Flash Point','L-9001',62.5,0.8,2.0,'C')")
    gw.sql("INSERT INTO lem_qc_specs VALUES "
           "('multitek-ns','Sulfur','L-9002',10.0,0.4,2.0,'ppm')")

    gw.sql("INSERT INTO lem_maintenance VALUES "
           "('t-1','pac-flash-2','Annual cal','calibration',365,"
           "'2026-01-04','send out')")
    gw.sql("INSERT INTO lem_maintenance VALUES "
           "('t-2','multitek-ns','Lamp change','pm',90,'2026-06-01','')")


def populate(app, gw):
    """Seed LabCore and let the snapshot read it once, as the poller would."""
    seed(gw)
    app.config["SNAPSHOTS"].refresh()


def fetch(client, uid="pac-flash-2", token="test-token"):
    headers = {} if token is None else {"X-LEM-Token": token}
    return client.get(f"/api/bench/{uid}/config", headers=headers)


# ── the whole point: LabCore is not in the request path ─────────────────────

class TestServingAConfigNeverTouchesLabCore:
    def test_a_config_read_costs_zero_labcore_operations(self, app, client, gw):
        """The reason this endpoint exists at all.

        If serving a bench costs even one op, the load simply moves from LabCore
        to LabCore-via-us and still scales with bench count — which is the crash
        being fixed, not a fix for it.
        """
        populate(app, gw)
        gw.calls = 0
        for _ in range(20):
            assert fetch(client).status_code == 200
        assert gw.calls == 0, (
            "the bench config path reached LabCore — that puts LabCore load "
            "back on a per-bench footing, which is the crash this replaces")

    def test_a_refused_request_costs_nothing_either(self, app, client, gw):
        populate(app, gw)
        gw.calls = 0
        fetch(client, token="guess")
        assert gw.calls == 0

    def test_an_unknown_machine_costs_nothing_either(self, app, client, gw):
        """Otherwise a mis-typed uid on one bench becomes a live read per poll,
        forever, which is exactly the pattern being removed."""
        populate(app, gw)
        gw.calls = 0
        fetch(client, uid="never-heard-of-it")
        assert gw.calls == 0


# ── the door: the same token /api/live checks ───────────────────────────────

class TestTheTokenIsChecked:
    def test_no_token_is_refused(self, app, client, gw):
        populate(app, gw)
        assert fetch(client, token=None).status_code == 401

    def test_a_wrong_token_is_refused(self, app, client, gw):
        populate(app, gw)
        assert fetch(client, token="guess").status_code == 401

    def test_a_blank_token_is_refused(self, app, client, gw):
        populate(app, gw)
        assert fetch(client, token="").status_code == 401

    def test_the_refusal_looks_like_the_live_one(self, app, client, gw):
        """Benches do not log in, so both doors answer the same way — a module
        that already handles /api/live's 401 needs no new branch."""
        populate(app, gw)
        assert fetch(client, token="guess").get_json() == {
            "error": "Not authorised."}

    def test_a_refused_request_carries_no_configuration(self, app, client, gw):
        populate(app, gw)
        body = fetch(client, token="guess").get_json()
        assert "corrections" not in body and "qc_specs" not in body

    def test_the_token_is_never_echoed_back(self, app, client, gw):
        populate(app, gw)
        assert "test-token" not in fetch(
            client, token="guess").get_data(as_text=True)


# ── the row shapes the module's parsers read by name ────────────────────────

class TestTheRowsArriveInTheShapeLabCoreReturns:
    """Every list must look exactly like `read_sql` on that table.

    The module feeds them straight to its existing parsers, which read columns
    by name. A wrong or missing key parses to nothing and says nothing.
    """

    @pytest.fixture
    def body(self, app, client, gw):
        populate(app, gw)
        return fetch(client).get_json()

    def test_the_payload_carries_every_section(self, body):
        assert set(body) == {"machine_uid", "snapshot_age_seconds", "override",
                             "corrections", "qc_samples", "qc_targets",
                             "qc_specs", "maintenance"}

    def test_the_machine_it_is_about_is_named_back(self, body):
        assert body["machine_uid"] == "pac-flash-2"

    # `build_corrections_query`: SELECT test_name, correction
    def test_a_correction_row_is_shaped_like_labcore(self, body):
        assert [set(r) for r in body["corrections"]] == [
            {"test_name", "correction"}]

    def test_a_correction_carries_its_value_as_a_number(self, body):
        assert body["corrections"][0] == {"test_name": "Flash Point",
                                          "correction": -3.0}

    # QC_SAMPLES_QUERY: SELECT name, sample_id_val, tests
    def test_a_qc_sample_row_is_shaped_like_labcore(self, body):
        assert [set(r) for r in body["qc_samples"]] == [
            {"name", "sample_id_val", "tests"}]

    def test_the_qc_sample_tests_stay_the_json_text_labcore_stores(self, body):
        """`parse_qc_sample_rows` calls `json.loads` on this. Handing it a list
        already parsed would raise TypeError and the whole library would come
        back empty."""
        assert body["qc_samples"][0]["tests"] == '[{"name": "Flash Point"}]'
        assert body["qc_samples"][0]["sample_id_val"] == "L-9001"

    # QC_TARGETS_QUERY: SELECT sample_name, test_name
    def test_a_qc_target_row_is_shaped_like_labcore(self, body):
        assert [set(r) for r in body["qc_targets"]] == [
            {"sample_name", "test_name"}]

    def test_a_qc_target_carries_the_assignment(self, body):
        assert body["qc_targets"][0] == {"sample_name": "Flash CRM",
                                         "test_name": "Flash Point"}

    # QC_SPECS_QUERY: SELECT machine_uid, test_name, sample_id, expected,
    #                        std_dev, k, units
    def test_a_qc_spec_row_is_shaped_like_labcore(self, body):
        assert [set(r) for r in body["qc_specs"]] == [
            {"machine_uid", "test_name", "sample_id", "expected", "std_dev",
             "k", "units"}]

    def test_a_qc_spec_keeps_its_scope_column(self, body):
        """`parse_qc_specs` drops any row whose `machine_uid` names another
        machine, so the column has to survive the trip or the guard cannot
        run."""
        assert body["qc_specs"][0]["machine_uid"] == "pac-flash-2"

    def test_a_qc_spec_band_arrives_as_numbers(self, body):
        """`parse_qc_specs` does `float(row.get("expected"))` and skips the spec
        on TypeError — a band flattened to text by the snapshot must be
        reconstituted or the bench reads as "No QC assigned"."""
        spec = body["qc_specs"][0]
        assert (spec["expected"], spec["std_dev"], spec["k"]) == (62.5, 0.8, 2.0)
        assert spec["units"] == "C" and spec["sample_id"] == "L-9001"

    # MAINTENANCE_QUERY: SELECT uid, name, kind, interval_days, last_done, note
    def test_a_maintenance_row_is_shaped_like_labcore(self, body):
        assert [set(r) for r in body["maintenance"]] == [
            {"uid", "name", "kind", "interval_days", "last_done", "note"}]

    def test_a_maintenance_row_carries_its_interval_as_a_number(self, body):
        assert body["maintenance"][0] == {
            "uid": "t-1", "name": "Annual cal", "kind": "calibration",
            "interval_days": 365, "last_done": "2026-01-04",
            "note": "send out"}

    # SELECT machine_uid, manual_override FROM lem_machine_control
    def test_the_override_is_this_machines_own_value(self, body):
        assert body["override"] == "SERVICE"


class TestAnEmptyConfigurationIsStillTheRightShape:
    def test_a_machine_with_nothing_configured_gets_empty_lists(self, app,
                                                                client, gw):
        populate(app, gw)
        gw.sql("INSERT INTO lem_machine_status VALUES "
               "('bare-1','Bare','GREEN','','2026-08-26T09:00:00')")
        app.config["SNAPSHOTS"].refresh()
        body = fetch(client, uid="bare-1").get_json()
        assert body["corrections"] == [] and body["qc_targets"] == []
        assert body["qc_specs"] == [] and body["maintenance"] == []

    def test_a_machine_with_no_override_row_gets_the_empty_string(self, app,
                                                                  client, gw):
        """Not null and not absent: `extract_overrides` compares the value
        against a fixed vocabulary, and "" is the real, valid 'no override'."""
        populate(app, gw)
        gw.sql("INSERT INTO lem_machine_status VALUES "
               "('bare-1','Bare','GREEN','','2026-08-26T09:00:00')")
        app.config["SNAPSHOTS"].refresh()
        assert fetch(client, uid="bare-1").get_json()["override"] == ""

    def test_the_shared_standards_still_arrive(self, app, client, gw):
        """QC samples are the lab's library, not the machine's — a bench with
        no assignments of its own still detects against them."""
        populate(app, gw)
        gw.sql("INSERT INTO lem_machine_status VALUES "
               "('bare-1','Bare','GREEN','','2026-08-26T09:00:00')")
        app.config["SNAPSHOTS"].refresh()
        assert len(fetch(client, uid="bare-1").get_json()["qc_samples"]) == 1


class TestAnUnknownMachineIsANormalState:
    """A machine registered but not yet configured is ordinary, and so is a
    bench whose uid the floor has never seen. 404 would push it onto the
    LabCore fallback for good — the exact load being removed."""

    def test_an_unknown_uid_is_not_a_404(self, app, client, gw):
        populate(app, gw)
        assert fetch(client, uid="never-heard-of-it").status_code == 200

    def test_an_unknown_uid_gets_empty_lists_and_no_override(self, app, client,
                                                             gw):
        populate(app, gw)
        body = fetch(client, uid="never-heard-of-it").get_json()
        assert body["override"] == ""
        assert body["corrections"] == [] and body["qc_targets"] == []
        assert body["qc_specs"] == [] and body["maintenance"] == []

    def test_an_unknown_uid_is_named_back_unchanged(self, app, client, gw):
        populate(app, gw)
        body = fetch(client, uid="never-heard-of-it").get_json()
        assert body["machine_uid"] == "never-heard-of-it"


# ── one bench never receives another's configuration ────────────────────────

class TestOneBenchNeverSeesAnothers:
    @pytest.fixture
    def body(self, app, client, gw):
        populate(app, gw)
        return fetch(client, uid="pac-flash-2").get_json()

    def test_another_machines_corrections_do_not_come_through(self, body):
        assert [r["test_name"] for r in body["corrections"]] == ["Flash Point"]

    def test_another_machines_qc_targets_do_not_come_through(self, body):
        assert [r["test_name"] for r in body["qc_targets"]] == ["Flash Point"]

    def test_another_machines_qc_specs_do_not_come_through(self, body):
        assert [r["machine_uid"] for r in body["qc_specs"]] == ["pac-flash-2"]

    def test_another_machines_maintenance_does_not_come_through(self, body):
        assert [r["uid"] for r in body["maintenance"]] == ["t-1"]

    def test_another_machines_override_does_not_come_through(self, body):
        assert body["override"] == "SERVICE"

    def test_the_other_bench_gets_its_own(self, app, client, gw):
        populate(app, gw)
        body = fetch(client, uid="multitek-ns").get_json()
        assert body["override"] == "DEAD"
        assert [r["test_name"] for r in body["corrections"]] == ["Sulfur"]
        assert [r["machine_uid"] for r in body["qc_specs"]] == ["multitek-ns"]
        assert [r["uid"] for r in body["maintenance"]] == ["t-2"]


# ── the age has to be the snapshot's own ────────────────────────────────────

class TestTheAgeIsTheSnapshotsOwn:
    """The module refuses configuration that is too old and falls back to
    LabCore. A missing or invented age silently defeats that safety net — the
    bench would trust an hour-old override forever."""

    def test_the_age_is_reported(self, app, client, gw):
        populate(app, gw)
        age = fetch(client).get_json()["snapshot_age_seconds"]
        assert isinstance(age, float)
        assert 0 <= age < 30

    def test_the_age_grows_with_the_snapshot(self, app, client, gw):
        """Aged by moving the snapshot's own stamp rather than sleeping: this
        must read the SAME clock `/api/machines` reports, not a second one
        started when the request arrived."""
        populate(app, gw)
        snapshots = app.config["SNAPSHOTS"]
        snapshots._at = datetime.now() - timedelta(seconds=300)
        age = fetch(client).get_json()["snapshot_age_seconds"]
        assert 299 <= age <= 302

    def test_the_age_matches_what_the_floor_is_told(self, app, client, gw):
        populate(app, gw)
        app.config["SNAPSHOTS"]._at = datetime.now() - timedelta(seconds=42)
        served = fetch(client).get_json()["snapshot_age_seconds"]
        floor = app.config["SNAPSHOTS"].get()["age_seconds"]
        assert abs(served - floor) < 1.0


class TestASnapshotThatHasNeverBuiltSaysSo:
    """503, not an empty 200. An empty configuration is a real state a bench
    acts on — it would clear its QC and drop its override — so "I have nothing
    yet" must never be served as "there is nothing"."""

    def test_a_cold_snapshot_is_a_503(self, client):
        assert fetch(client).status_code == 503

    def test_a_cold_snapshot_says_it_is_stale(self, client):
        body = fetch(client).get_json()
        assert body["stale"] is True and body["error"]

    def test_a_cold_snapshot_carries_no_configuration(self, client):
        body = fetch(client).get_json()
        assert "qc_specs" not in body and "override" not in body

    def test_a_cold_snapshot_does_not_go_and_read_labcore(self, client, gw):
        """The tempting fix — build the snapshot for this caller — is what turns
        a lab full of benches restarting after an outage into a stampede on the
        queue that is already down. It waits for the poller instead."""
        gw.calls = 0
        fetch(client)
        assert gw.calls == 0

    def test_the_token_is_still_checked_first(self, client):
        assert fetch(client, token="guess").status_code == 401
