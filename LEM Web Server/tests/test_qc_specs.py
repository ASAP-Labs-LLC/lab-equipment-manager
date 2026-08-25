"""QC bridge between the LEM master view and the LEM Station modules.

The station modules READ their QC specs from `lem_qc_specs` and WRITE their
live state to `lem_machine_status` / `lem_machine_log`; the master view owns
the specs and pushes operator commands through `lem_machine_control`.
These tests pin that contract from the server's side.
"""
import pytest

from labcore_gateway import FakeLabCoreGateway
from qc_specs import (
    QcSpec,
    QcSpecStore,
    MachineStateReader,
)


@pytest.fixture
def gw():
    return FakeLabCoreGateway()


@pytest.fixture
def store(gw):
    return QcSpecStore(gw)


# ── lem_qc_specs: the table the station modules read ─────────────────────────

class TestQcSpecStore:
    def test_ensure_schema_creates_the_table(self, gw, store):
        store.ensure_schema()
        res = gw.read_sql("SELECT name FROM sqlite_master WHERE name='lem_qc_specs'")
        assert res.get("rows")

    def test_save_then_list(self, store):
        store.save(QcSpec(machine_uid="m1", test_name="Cloud Point",
                          sample_id="QC-CP-1", expected=-9.0, std_dev=0.5,
                          k=2.0, units="C"))
        specs = store.list_specs()
        assert len(specs) == 1
        spec = specs[0]
        assert spec.machine_uid == "m1"
        assert spec.test_name == "Cloud Point"
        assert spec.sample_id == "QC-CP-1"
        assert spec.expected == -9.0
        assert spec.std_dev == 0.5
        assert spec.k == 2.0
        assert spec.units == "C"

    def test_save_is_an_upsert_on_machine_and_test(self, store):
        store.save(QcSpec("m1", "Cloud Point", "QC-CP-1", -9.0, 0.5))
        store.save(QcSpec("m1", "Cloud Point", "QC-CP-2", -8.5, 0.4, k=3.0))
        specs = store.list_specs()
        assert len(specs) == 1                 # replaced, not duplicated
        assert specs[0].sample_id == "QC-CP-2"
        assert specs[0].expected == -8.5
        assert specs[0].k == 3.0

    def test_specs_scoped_to_one_machine(self, store):
        store.save(QcSpec("m1", "Cloud Point", "QC-CP-1", -9.0, 0.5))
        store.save(QcSpec("m2", "Pour Point", "QC-PP-1", -31.0, 1.0))
        assert [s.test_name for s in store.list_specs("m1")] == ["Cloud Point"]
        assert [s.test_name for s in store.list_specs("m2")] == ["Pour Point"]

    def test_delete_spec(self, store):
        store.save(QcSpec("m1", "Cloud Point", "QC-CP-1", -9.0, 0.5))
        store.delete("m1", "Cloud Point")
        assert store.list_specs() == []

    def test_rows_match_what_the_station_module_expects(self, gw, store):
        # The module runs exactly this query and reads these column names.
        store.save(QcSpec("m1", "Cloud Point", "QC-CP-1", -9.0, 0.5, 2.0, "C"))
        res = gw.read_sql(
            "SELECT machine_uid, test_name, sample_id, expected, std_dev, k, "
            "units FROM lem_qc_specs")
        assert not res.get("error")
        row = res["rows"][0]
        assert row["machine_uid"] == "m1"
        assert row["test_name"] == "Cloud Point"
        assert float(row["expected"]) == -9.0
        assert float(row["k"]) == 2.0

    def test_blank_test_name_is_rejected(self, store):
        with pytest.raises(ValueError):
            store.save(QcSpec("m1", "  ", "QC", 1.0, 0.1))

    def test_negative_std_dev_is_rejected(self, store):
        with pytest.raises(ValueError):
            store.save(QcSpec("m1", "Cloud Point", "QC", 1.0, -0.1))


# ── Reading what the station modules published ───────────────────────────────

class TestMachineStateReader:
    def seed_status(self, gw, uid, title, status, reason, ts):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
               "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
               "reason TEXT, updated_at TEXT)")
        gw.sql("INSERT INTO lem_machine_status (machine_uid, title, status, "
               "reason, updated_at) VALUES (?, ?, ?, ?, ?)",
               [uid, title, status, reason, ts])

    def test_lists_machines_newest_first(self, gw):
        self.seed_status(gw, "m1", "OptiMPP 1", "GREEN", "System nominal",
                         "2026-07-28T10:00:00")
        self.seed_status(gw, "m2", "Multitek S", "RED", "QC out of spec: Flash",
                         "2026-07-28T12:00:00")
        machines = MachineStateReader(gw).machines()
        assert [m["title"] for m in machines] == ["Multitek S", "OptiMPP 1"]
        assert machines[0]["status"] == "RED"
        assert machines[0]["status_color"]        # dashboard needs a color

    def test_missing_table_gives_empty_list_not_an_error(self, gw):
        assert MachineStateReader(gw).machines() == []

    def test_events_for_a_machine(self, gw):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log ("
               "machine_uid TEXT, ts TEXT, kind TEXT, lab_id TEXT, "
               "test_name TEXT, value TEXT, detail TEXT)")
        for i, kind in enumerate(("run", "qc", "status_change")):
            gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, "
                   "lab_id, test_name, value, detail) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?)",
                   ["m1", f"2026-07-28T1{i}:00:00", kind, "37037",
                    "Cloud Point", "-9.1", "{}"])
        events = MachineStateReader(gw).events("m1")
        assert [e["kind"] for e in events] == ["status_change", "qc", "run"]
        assert events[0]["lab_id"] == "37037"

    def test_events_missing_table_is_empty(self, gw):
        assert MachineStateReader(gw).events("m1") == []


# ── lem_machine_control: overrides pushed to a station module ────────────────

class TestMachineControl:
    def test_set_override_round_trips(self, gw, store):
        reader = MachineStateReader(gw)
        reader.set_override("m1", "SERVICE", "pump replaced")
        res = gw.read_sql("SELECT machine_uid, manual_override FROM "
                          "lem_machine_control")
        assert res["rows"] == [{"machine_uid": "m1",
                                "manual_override": "SERVICE"}]

    def test_override_is_upserted_not_duplicated(self, gw):
        reader = MachineStateReader(gw)
        reader.set_override("m1", "SERVICE", "a")
        reader.set_override("m1", "", "back in service")
        res = gw.read_sql("SELECT machine_uid, manual_override FROM "
                          "lem_machine_control")
        assert res["rows"] == [{"machine_uid": "m1", "manual_override": ""}]

    def test_invalid_override_rejected(self, gw):
        with pytest.raises(ValueError):
            MachineStateReader(gw).set_override("m1", "BANANA", "why")


# ── Web API: the master view's QC endpoints ─────────────────────────────────

@pytest.fixture
def client(gw):
    from web_app import create_app
    app = create_app(gw, admin_password="pw", secret="s")
    app.config["TESTING"] = True
    return app.test_client()


def login(client):
    return client.post("/api/login", json={"password": "pw"})


class TestQcApi:
    def seed_machine(self, gw, uid="m1", title="OptiMPP 1"):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
               "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
               "reason TEXT, updated_at TEXT)")
        gw.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
               [uid, title, "UNKNOWN", "No valid QC data found.",
                "2026-07-28T12:00:00"])

    def test_machines_endpoint_lists_station_modules(self, gw, client):
        self.seed_machine(gw)
        body = client.get("/api/machines?fresh=1").get_json()
        assert body["machines"][0]["title"] == "OptiMPP 1"
        assert body["machines"][0]["status"] == "UNKNOWN"

    def test_qc_specs_endpoint_round_trip(self, gw, client):
        login(client)
        r = client.post("/api/qc-specs", json={
            "machine_uid": "m1", "test_name": "Cloud Point",
            "sample_id": "QC-CP-1", "expected": -9.0, "std_dev": 0.5,
            "k": 2.0, "units": "C"})
        assert r.status_code == 200
        specs = client.get("/api/qc-specs").get_json()["specs"]
        assert specs[0]["test_name"] == "Cloud Point"
        assert specs[0]["low"] == -10.0 and specs[0]["high"] == -8.0

    def test_qc_spec_write_requires_auth(self, client):
        r = client.post("/api/qc-specs", json={"machine_uid": "m1",
                                               "test_name": "X",
                                               "expected": 1, "std_dev": 1})
        assert r.status_code == 401

    def test_invalid_spec_returns_400(self, client):
        login(client)
        r = client.post("/api/qc-specs", json={"machine_uid": "m1",
                                               "test_name": "",
                                               "expected": 1, "std_dev": 1})
        assert r.status_code == 400

    def test_delete_spec(self, gw, client):
        login(client)
        client.post("/api/qc-specs", json={"machine_uid": "m1",
                                           "test_name": "Cloud Point",
                                           "expected": -9.0, "std_dev": 0.5})
        r = client.delete("/api/qc-specs",
                          json={"machine_uid": "m1", "test_name": "Cloud Point"})
        assert r.status_code == 200
        assert client.get("/api/qc-specs").get_json()["specs"] == []

    def test_machine_events_endpoint(self, gw, client):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log ("
               "machine_uid TEXT, ts TEXT, kind TEXT, lab_id TEXT, "
               "test_name TEXT, value TEXT, detail TEXT)")
        gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
               ["m1", "2026-07-28T12:00:00", "run", "37037", "", "", "{}"])
        body = client.get("/api/machines/m1/events").get_json()
        assert body["events"][0]["lab_id"] == "37037"

    def test_override_endpoint_requires_auth_and_comment(self, gw, client):
        assert client.post("/api/machines/m1/override",
                           json={"override": "SERVICE",
                                 "comment": "x"}).status_code == 401
        login(client)
        assert client.post("/api/machines/m1/override",
                           json={"override": "SERVICE",
                                 "comment": ""}).status_code == 400
        r = client.post("/api/machines/m1/override",
                        json={"override": "SERVICE", "comment": "pump"})
        assert r.status_code == 200
        rows = gw.read_sql("SELECT manual_override FROM lem_machine_control")
        assert rows["rows"][0]["manual_override"] == "SERVICE"

    def test_test_names_endpoint_offers_labcore_methods(self, gw, client):
        gw.write("insert_sample", {"lab_id": "L1"})
        gw.write("add_test", {"lab_id": "L1", "test_name": "Cloud Point"})
        body = client.get("/api/test-names").get_json()
        assert "Cloud Point" in body["tests"]


# ── The refusal that answers ────────────────────────────────────────────────
#
# LabCore's write queue serialises at ~1.5 writes/sec and refuses past ~100
# pending BY ANSWERING rather than raising. Every test in this module runs
# TWICE, once per refusal shape — see tests/refusal_shapes.py for which of the
# two is evidence and which is a fixture. In short: the error dict carrying
# `busy` is recorded from a real incident; the one with no "error" key is
# synthetic, kept because `{"error": ...}` is the ONE shape the old
# `if not res.get("error")` code already handled and a suite that refuses only
# that way proves nothing about the bug being fixed.

from labcore_result import LabCoreError, LabCoreRefused, LabCoreUnavailable
from qc_specs import (
    QcSpecRefused,
    QcSpecStoreError,
    QcSpecUnavailable,
)

import refusal_shapes                                   # noqa: E402

pytestmark = pytest.mark.usefixtures("both_refusal_shapes")

# SYNTHETIC — see refusal_shapes. Kept as a name for the tests that pass an
# explicit `answer=`.
QUEUE_FULL = refusal_shapes.NO_ERROR_KEY
READ_BLIP = {"error": "HTTPSConnectionPool(host='labvision'): Read timed out"}


class QueueFullGateway:
    """A LabCore whose write queue is past its limit.

    Writes matching `refuse` are ANSWERED with the queue's refusal and never
    reach the database, so "did it raise" and "did the row change" are two
    separate assertions rather than one. Everything else passes through to a
    real fake, so state can be seeded truthfully first.
    """

    def __init__(self, real=None, refuse=lambda sql: True, answer=None):
        self.real = real if real is not None else FakeLabCoreGateway()
        self.refuse = refuse
        # `None` means "whichever shape this run of the suite is driving";
        # an explicit answer is for the tests that are about one shape.
        self.answer = answer
        self.refused = []

    def sql(self, sql, args=None, **kw):
        if self.refuse(sql):
            self.refused.append(sql)
            if self.answer is None:
                return refusal_shapes.current()
            return dict(self.answer) if isinstance(self.answer, dict) \
                else self.answer
        return self.real.sql(sql, args, **kw)

    def read_sql(self, sql, args=None, **kw):
        return self.real.read_sql(sql, args, **kw)

    def write(self, operation, params, **kw):
        return self.real.write(operation, params, **kw)

    def get_test_names(self):
        return self.real.get_test_names()


class BlipGateway:
    """LabCore cannot be asked. Reads matching `fail` time out."""

    def __init__(self, real=None, fail=lambda sql: True):
        self.real = real if real is not None else FakeLabCoreGateway()
        self.fail = fail

    def sql(self, sql, args=None, **kw):
        return self.real.sql(sql, args, **kw)

    def read_sql(self, sql, args=None, **kw):
        if self.fail(sql):
            return dict(READ_BLIP)
        return self.real.read_sql(sql, args, **kw)

    def write(self, operation, params, **kw):
        return self.real.write(operation, params, **kw)


def specs_in(gw):
    """Read the table directly, past the store, so the assertion is about
    what LabCore holds and not about what the store believes."""
    res = gw.read_sql("SELECT machine_uid, test_name, expected FROM "
                      "lem_qc_specs ORDER BY test_name")
    return res.get("rows") or []


class TestARefusedWriteIsNeverReportedAsSaved:
    """One test per mutating method: it raises, and nothing changed."""

    def test_ensure_schema_refused_raises_and_does_not_remember_success(self):
        gw = QueueFullGateway()
        store = QcSpecStore(gw)
        with pytest.raises(QcSpecRefused):
            store.ensure_schema()
        # The flag is the second half of the bug: cached on an unread answer,
        # a refused CREATE is remembered as done for the life of the process.
        assert store._schema_ready is False
        assert not gw.real.read_sql(
            "SELECT name FROM sqlite_master WHERE name='lem_qc_specs'"
        ).get("rows")

    def test_save_refused_raises(self):
        # Only the row write is refused. Refusing the CREATE as well would make
        # this pass on `ensure_schema` raising, and it would still pass with
        # `save`'s own confirmation removed — a test arranged so the bug it is
        # named for cannot be observed.
        store = QcSpecStore(QueueFullGateway(
            refuse=lambda s: not s.startswith("CREATE")))
        with pytest.raises(QcSpecRefused):
            store.save(QcSpec("m1", "Cloud Point", "QC-CP-1", -9.0, 0.5))

    def test_save_refused_leaves_the_previous_band_in_place(self):
        """The dangerous case: an EDIT that is dropped. The operator is told
        the band moved to -8.0 and the bench keeps judging against -9.0."""
        real = FakeLabCoreGateway()
        QcSpecStore(real).save(QcSpec("m1", "Cloud Point", "QC-CP-1", -9.0, 0.5))
        gw = QueueFullGateway(real, refuse=lambda s: s.startswith("INSERT"))
        with pytest.raises(QcSpecRefused):
            QcSpecStore(gw).save(QcSpec("m1", "Cloud Point", "QC-CP-1",
                                        -8.0, 0.5))
        assert [r["expected"] for r in specs_in(real)] == [-9.0]

    def test_delete_refused_raises_and_the_band_is_still_there(self):
        real = FakeLabCoreGateway()
        QcSpecStore(real).save(QcSpec("m1", "Cloud Point", "QC-CP-1", -9.0, 0.5))
        gw = QueueFullGateway(real, refuse=lambda s: s.startswith("DELETE"))
        with pytest.raises(QcSpecRefused):
            QcSpecStore(gw).delete("m1", "Cloud Point")
        assert len(specs_in(real)) == 1

    def test_set_override_refused_raises(self):
        gw = QueueFullGateway(refuse=lambda s: not s.startswith("CREATE"))
        with pytest.raises(QcSpecRefused):
            MachineStateReader(gw).set_override("m1", "SERVICE", "pump")

    def test_set_override_refused_leaves_the_machine_as_it_was(self):
        """A command TO a bench. Dropped, the floor shows an instrument out of
        service while it keeps running samples — or keeps a healthy one locked
        out when the 'clear' is the write that goes missing."""
        real = FakeLabCoreGateway()
        MachineStateReader(real).set_override("m1", "SERVICE", "pump")
        gw = QueueFullGateway(real, refuse=lambda s: s.startswith("INSERT"))
        with pytest.raises(QcSpecRefused):
            MachineStateReader(gw).set_override("m1", "", "back in service")
        rows_now = real.read_sql(
            "SELECT manual_override FROM lem_machine_control")["rows"]
        assert rows_now == [{"manual_override": "SERVICE"}]

    def test_the_control_table_create_is_confirmed_too(self):
        """The table is made to EXIST first, so the refused CREATE is the only
        thing that can raise.

        Without that the test passes for the wrong reason: an unconfirmed CREATE
        leaves no table, the INSERT then fails with "no such table", and the
        exception looks like the one being asserted. Seeded this way, dropping
        `confirm_write` from the CREATE lets the override write straight through
        and the test fails — which is what a test of that line has to do.
        """
        real = FakeLabCoreGateway()
        MachineStateReader(real).set_override("m1", "SERVICE", "pump")
        gw = QueueFullGateway(real, refuse=lambda s: s.startswith("CREATE"))
        with pytest.raises(QcSpecRefused):
            MachineStateReader(gw).set_override("m1", "", "back in service")
        # and the command itself was never issued
        assert real.read_sql("SELECT manual_override FROM lem_machine_control"
                             )["rows"] == [{"manual_override": "SERVICE"}]


class TestSilenceIsNotSuccessEither:
    """`None` and `{}` are what a gateway returns when it has stopped
    answering. Both used to pass the `"error" in res` test."""

    NOT_CREATE = staticmethod(lambda s: not s.startswith("CREATE"))

    @pytest.mark.parametrize("answer", [None, {"ok": False}, "done"])
    def test_save(self, answer):
        store = QcSpecStore(QueueFullGateway(refuse=self.NOT_CREATE,
                                             answer=answer))
        with pytest.raises(QcSpecRefused):
            store.save(QcSpec("m1", "Cloud Point", "QC-CP-1", -9.0, 0.5))

    @pytest.mark.parametrize("answer", [None, {"ok": False}])
    def test_set_override(self, answer):
        gw = QueueFullGateway(refuse=self.NOT_CREATE, answer=answer)
        with pytest.raises(QcSpecRefused):
            MachineStateReader(gw).set_override("m1", "SERVICE", "x")


class TestReadsDoNotInventEmpty:
    """"Could not ask" served as "there is nothing" is the other half of the
    same bug: the floor reporting no QC, no machines and no history about a
    lab that has all three."""

    def test_list_specs_raises_on_a_blip(self):
        with pytest.raises(QcSpecUnavailable):
            QcSpecStore(BlipGateway()).list_specs()

    def test_list_specs_still_reads_a_missing_table_as_no_qc_assigned(self, gw):
        """The one honest empty: nothing has ever saved a band."""
        store = QcSpecStore(gw)
        store._schema_ready = True          # pretend the CREATE never ran
        assert store.list_specs() == []

    def test_a_write_path_can_refuse_even_that(self, gw):
        store = QcSpecStore(gw)
        store._schema_ready = True
        with pytest.raises(QcSpecUnavailable):
            store.list_specs(missing_ok=False)

    def test_machines_raises_rather_than_reporting_an_empty_lab(self):
        with pytest.raises(QcSpecUnavailable):
            MachineStateReader(BlipGateway()).machines()

    def test_heartbeats_raises_rather_than_clearing_the_delete_guard(self):
        """`in_use` is read off these beats. An empty map during a blip says
        no parser is live anywhere and opens every config up to deletion."""
        with pytest.raises(QcSpecUnavailable):
            MachineStateReader(BlipGateway()).heartbeats()

    def test_events_raises_rather_than_showing_a_blank_history(self):
        with pytest.raises(QcSpecUnavailable):
            MachineStateReader(BlipGateway()).events("m1")

    def test_recent_events_raises(self):
        with pytest.raises(QcSpecUnavailable):
            MachineStateReader(BlipGateway()).recent_events(500)

    def test_sub_statuses_raises(self):
        with pytest.raises(QcSpecUnavailable):
            MachineStateReader(BlipGateway()).sub_statuses()

    def test_last_activity_raises(self):
        with pytest.raises(QcSpecUnavailable):
            MachineStateReader(BlipGateway()).last_activity()

    def test_every_missing_table_read_is_still_empty(self, gw):
        """Each of these tables is created by someone else — the modules, or
        the boot-time schema — so "not there yet" is a real answer."""
        reader = MachineStateReader(gw)
        assert reader.machines() == []
        assert reader.heartbeats() == {}
        assert reader.sub_statuses() == {}
        assert reader.last_activity() == {}
        assert reader.events("m1") == []
        assert reader.recent_events() == []


class TestTheExceptionsRoutesWillCatch:
    def test_a_route_can_catch_the_store_or_the_rule(self):
        assert issubclass(QcSpecRefused, QcSpecStoreError)
        assert issubclass(QcSpecRefused, LabCoreRefused)
        assert issubclass(QcSpecUnavailable, QcSpecStoreError)
        assert issubclass(QcSpecUnavailable, LabCoreUnavailable)
        assert issubclass(QcSpecStoreError, LabCoreError)

    def test_retryable_and_refused_stay_distinguishable(self):
        """A route answers 503 for one and 502 for the other; collapsing them
        is how "try again in a moment" becomes "your band is invalid"."""
        assert not issubclass(QcSpecRefused, LabCoreUnavailable)
        assert not issubclass(QcSpecUnavailable, LabCoreRefused)

    def test_the_message_says_what_failed_not_just_that_it_did(self):
        store = QcSpecStore(QueueFullGateway(
            refuse=lambda s: s.startswith("INSERT")))
        with pytest.raises(QcSpecRefused) as caught:
            store.save(QcSpec("m1", "Cloud Point", "QC-CP-1", -9.0, 0.5))
        assert "Cloud Point" in str(caught.value)
        # And the operational detail the answer carried reaches the operator.
        # This used to assert `"137"`, which pinned the message to the one
        # field of the INVENTED shape — a test that could only pass against a
        # protocol nobody has measured.
        carried = [k for k in ("retry_after", "busy", "pending")
                   if k in refusal_shapes.current()]
        assert carried, "the shape under test carries no detail at all"
        assert any(k in str(caught.value) for k in carried)
