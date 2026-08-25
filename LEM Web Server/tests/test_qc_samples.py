"""QC samples — the old LEM's model, carried forward.

A QC sample is a named standard (CRM) with a Lab ID and a list of test
specs: "Cloud CRM", lab id "CP", Cloud - D7689 = -7.4 ± 1·2.8. Defined ONCE
here and shared by every machine, exactly like V4's `SampleSpec`.

Station modules pull this library and self-detect: when a print's Lab ID
matches a QC sample, the parser runs QC on whatever methods it extracted.
"""
import json

import pytest

from labcore_gateway import FakeLabCoreGateway
from qc_samples import QcSample, QcSampleStore, QcSampleTest, import_v4_samples


@pytest.fixture
def gw():
    return FakeLabCoreGateway()


@pytest.fixture
def store(gw):
    return QcSampleStore(gw)


V4_CONFIG = {
    "samples": [
        {"name": "Cloud CRM", "sample_id_val": "CP",
         "tests": [{"name": "Cloud - D7689", "value_col": "Cloud Point, Au",
                    "expected": -7.4, "std_dev": 2.8, "k": 1.0, "units": "C"}]},
        {"name": "Pour CRM", "sample_id_val": "PP",
         "tests": [{"name": "Pour Point - D7346", "value_col": "Pour Point",
                    "expected": -18.3, "std_dev": 6.4, "k": 1.0}]},
        {"name": "Nameless", "sample_id_val": "", "tests": []},
    ]
}


class TestQcSampleStore:
    def test_save_and_list(self, store):
        store.save(QcSample("Cloud CRM", "CP", [
            QcSampleTest("Cloud - D7689", "Cloud Point, Au", -7.4, 2.8, 1.0, "C")]))
        samples = store.list_samples()
        assert len(samples) == 1
        s = samples[0]
        assert s.name == "Cloud CRM"
        assert s.sample_id_val == "CP"
        assert s.tests[0].expected == -7.4
        assert s.tests[0].k == 1.0
        assert s.tests[0].value_col == "Cloud Point, Au"

    def test_save_upserts_by_name(self, store):
        store.save(QcSample("Cloud CRM", "CP", []))
        store.save(QcSample("Cloud CRM", "CP-2", [
            QcSampleTest("Cloud - D7689", "Cloud", -8.0, 2.0)]))
        samples = store.list_samples()
        assert len(samples) == 1
        assert samples[0].sample_id_val == "CP-2"
        assert len(samples[0].tests) == 1

    def test_delete(self, store):
        store.save(QcSample("Cloud CRM", "CP", []))
        store.delete("Cloud CRM")
        assert store.list_samples() == []

    def test_blank_name_rejected(self, store):
        with pytest.raises(ValueError):
            store.save(QcSample("  ", "CP", []))

    def test_blank_lab_id_rejected(self, store):
        with pytest.raises(ValueError):
            store.save(QcSample("Cloud CRM", "  ", []))

    def test_negative_std_dev_rejected(self, store):
        with pytest.raises(ValueError):
            store.save(QcSample("X", "X1", [QcSampleTest("t", "c", 1.0, -1.0)]))

    def test_rows_are_json_and_survive_a_round_trip(self, gw, store):
        store.save(QcSample("Cloud CRM", "CP", [
            QcSampleTest("Cloud - D7689", "Cloud Point, Au", -7.4, 2.8, 1.0, "C")]))
        res = gw.read_sql("SELECT name, sample_id_val, tests FROM lem_qc_samples")
        row = res["rows"][0]
        assert row["sample_id_val"] == "CP"
        assert json.loads(row["tests"])[0]["name"] == "Cloud - D7689"


class TestImportV4:
    def test_imports_named_samples_with_specs(self, store):
        added = import_v4_samples(V4_CONFIG, store)
        assert added == 2                       # the nameless one is skipped
        names = sorted(s.name for s in store.list_samples())
        assert names == ["Cloud CRM", "Pour CRM"]

    def test_import_preserves_k_and_units(self, store):
        import_v4_samples(V4_CONFIG, store)
        cloud = next(s for s in store.list_samples() if s.name == "Cloud CRM")
        assert cloud.tests[0].k == 1.0
        assert cloud.tests[0].units == "C"
        assert cloud.limits("Cloud - D7689") == pytest.approx((-10.2, -4.6))

    def test_import_is_idempotent(self, store):
        import_v4_samples(V4_CONFIG, store)
        import_v4_samples(V4_CONFIG, store)
        assert len(store.list_samples()) == 2

    def test_import_ignores_junk(self, store):
        assert import_v4_samples({}, store) == 0
        assert import_v4_samples({"samples": "nope"}, store) == 0


class TestLookupForStationModules:
    """The shape the station modules consume."""

    def test_payload_lists_lab_ids_and_specs(self, store):
        import_v4_samples(V4_CONFIG, store)
        payload = store.as_payload()
        by_id = {s["sample_id_val"]: s for s in payload}
        assert set(by_id) == {"CP", "PP"}
        cloud = by_id["CP"]["tests"][0]
        assert cloud["name"] == "Cloud - D7689"
        assert cloud["value_col"] == "Cloud Point, Au"
        assert cloud["expected"] == -7.4


# ── Web API ─────────────────────────────────────────────────────────────────

class StubAuth:
    def login(self, username, password):
        return ("kaden", "tok", "") if password == "good" else (None, "", "bad")

    def logout(self, token):
        pass


@pytest.fixture
def client(gw):
    from web_app import create_app
    app = create_app(gw, authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client()


def login(client):
    client.post("/api/login", json={"username": "k", "password": "good"})


class TestQcSampleApi:
    def test_list_endpoint(self, gw, client):
        import_v4_samples(V4_CONFIG, QcSampleStore(gw))
        body = client.get("/api/qc-samples").get_json()
        assert {s["name"] for s in body["samples"]} == {"Cloud CRM", "Pour CRM"}

    def test_save_requires_auth(self, client):
        assert client.post("/api/qc-samples", json={}).status_code == 401

    def test_save_and_delete(self, client):
        login(client)
        r = client.post("/api/qc-samples", json={
            "name": "Cloud CRM", "sample_id_val": "CP",
            "tests": [{"name": "Cloud - D7689", "value_col": "Cloud Point",
                       "expected": -7.4, "std_dev": 2.8, "k": 1.0}]})
        assert r.status_code == 200
        assert len(client.get("/api/qc-samples").get_json()["samples"]) == 1
        assert client.delete("/api/qc-samples",
                             json={"name": "Cloud CRM"}).status_code == 200
        assert client.get("/api/qc-samples").get_json()["samples"] == []

    def test_invalid_sample_is_400(self, client):
        login(client)
        assert client.post("/api/qc-samples",
                           json={"name": "", "sample_id_val": "x"}).status_code == 400


# ── Changeover: a CRM lot runs out and a new lot takes over ─────────────────

class TestChangeover:
    """V4's Changeover QC. The new lot inherits the old lot's tests, and
    every machine that was checked against the old lot moves to the new one
    — otherwise a lot change silently stops QC across the lab."""

    def setup_lab(self, gw):
        from machine_map import QcTargetStore, WatchedTarget
        store = QcSampleStore(gw)
        store.save(QcSample("Cloud CRM", "CP", [
            QcSampleTest("Cloud Point", "Cloud Point", -7.4, 2.8, 1.0, "C"),
            QcSampleTest("Cloud Point, mini method", "Cloud Point, mini method",
                         -7.4, 2.8, 1.0, "C")]))
        targets = QcTargetStore(gw)
        targets.assign("m1", [WatchedTarget("Cloud CRM", "Cloud Point")])
        targets.assign("m2", [WatchedTarget("Cloud CRM", "Cloud Point, mini method"),
                              WatchedTarget("Pour CRM", "Pour Point")])
        targets.assign("m3", [WatchedTarget("Pour CRM", "Pour Point")])
        return store, targets

    def test_new_lot_inherits_the_tests(self, gw):
        from qc_samples import changeover
        store, _ = self.setup_lab(gw)
        changeover(gw, "Cloud CRM", "Cloud CRM AB26", "CP26")
        new = next(s for s in store.list_samples() if s.name == "Cloud CRM AB26")
        assert new.sample_id_val == "CP26"
        assert [t.name for t in new.tests] == ["Cloud Point",
                                               "Cloud Point, mini method"]
        assert new.tests[0].expected == -7.4

    def test_machines_move_to_the_new_lot(self, gw):
        from qc_samples import changeover
        from machine_map import QcTargetStore, WatchedTarget
        _, targets = self.setup_lab(gw)
        moved = changeover(gw, "Cloud CRM", "Cloud CRM AB26", "CP26")
        assert moved == 2                      # m1 and m2, not m3
        assert targets.targets("m1") == [WatchedTarget("Cloud CRM AB26",
                                                       "Cloud Point")]
        assert WatchedTarget("Cloud CRM AB26", "Cloud Point, mini method") \
            in targets.targets("m2")

    def test_unrelated_assignments_are_untouched(self, gw):
        from qc_samples import changeover
        from machine_map import WatchedTarget
        _, targets = self.setup_lab(gw)
        changeover(gw, "Cloud CRM", "Cloud CRM AB26", "CP26")
        assert WatchedTarget("Pour CRM", "Pour Point") in targets.targets("m2")
        assert targets.targets("m3") == [WatchedTarget("Pour CRM", "Pour Point")]

    def test_old_lot_is_kept_by_default_for_history(self, gw):
        from qc_samples import changeover
        store, _ = self.setup_lab(gw)
        changeover(gw, "Cloud CRM", "Cloud CRM AB26", "CP26")
        assert {s.name for s in store.list_samples()} == {"Cloud CRM",
                                                          "Cloud CRM AB26"}

    def test_old_lot_can_be_retired(self, gw):
        from qc_samples import changeover
        store, _ = self.setup_lab(gw)
        changeover(gw, "Cloud CRM", "Cloud CRM AB26", "CP26", retire_old=True)
        assert {s.name for s in store.list_samples()} == {"Cloud CRM AB26"}

    def test_duplicate_new_name_is_refused(self, gw):
        from qc_samples import changeover
        self.setup_lab(gw)
        with pytest.raises(ValueError, match="already exists"):
            changeover(gw, "Cloud CRM", "Cloud CRM", "CP26")

    def test_unknown_old_lot_is_refused(self, gw):
        from qc_samples import changeover
        self.setup_lab(gw)
        with pytest.raises(ValueError, match="not found"):
            changeover(gw, "Nope CRM", "New CRM", "N1")

    def test_blank_lab_id_is_refused(self, gw):
        from qc_samples import changeover
        self.setup_lab(gw)
        with pytest.raises(ValueError):
            changeover(gw, "Cloud CRM", "Cloud CRM AB26", "  ")


class TestChangeoverApi:
    def test_endpoint_performs_the_turnover(self, gw, client):
        from machine_map import QcTargetStore, WatchedTarget
        QcSampleStore(gw).save(QcSample("Cloud CRM", "CP", [
            QcSampleTest("Cloud Point", "Cloud Point", -7.4, 2.8, 1.0, "C")]))
        QcTargetStore(gw).assign("m1", [WatchedTarget("Cloud CRM", "Cloud Point")])
        login(client)
        r = client.post("/api/qc-samples/changeover", json={
            "old_name": "Cloud CRM", "new_name": "Cloud CRM AB26",
            "new_id_val": "CP26"})
        assert r.status_code == 200
        assert r.get_json()["moved"] == 1
        assert QcTargetStore(gw).targets("m1")[0].sample == "Cloud CRM AB26"

    def test_changeover_requires_auth(self, client):
        assert client.post("/api/qc-samples/changeover",
                           json={"old_name": "a", "new_name": "b",
                                 "new_id_val": "c"}).status_code == 401

    def test_bad_changeover_is_400(self, gw, client):
        login(client)
        r = client.post("/api/qc-samples/changeover", json={
            "old_name": "Nope", "new_name": "New", "new_id_val": "N"})
        assert r.status_code == 400
        assert "not found" in r.get_json()["error"]


# ── The refusal that answers ────────────────────────────────────────────────
#
# Every test in this module runs TWICE, once per refusal shape — see
# tests/refusal_shapes.py for which of the two is evidence and which is a
# fixture. In short: the error dict carrying `busy` is recorded from a real
# incident; the one with no "error" key is synthetic, kept because
# `{"error": ...}` is the ONE shape the old `if not res.get("error")` code
# already handled, so a suite refusing only that way proves nothing.

from labcore_result import LabCoreError, LabCoreRefused, LabCoreUnavailable
from qc_samples import (
    QcSampleRefused,
    QcSampleStoreError,
    QcSampleUnavailable,
)

import refusal_shapes                                   # noqa: E402

pytestmark = pytest.mark.usefixtures("both_refusal_shapes")

# SYNTHETIC — see refusal_shapes.
QUEUE_FULL = refusal_shapes.NO_ERROR_KEY
READ_BLIP = {"error": "HTTPSConnectionPool(host='labvision'): Read timed out"}


class QueueFullGateway:
    """A LabCore past its write-queue limit.

    Writes matching `refuse` are answered with the refusal and never reach the
    database, so "it raised" and "nothing changed" are separate assertions.
    Everything else goes through to a real fake, so the before-state is true.
    """

    def __init__(self, real=None, refuse=lambda sql: True, answer=None):
        self.real = real if real is not None else FakeLabCoreGateway()
        self.refuse = refuse
        # `None` means "whichever shape this run of the suite is driving".
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


def library_in(gw):
    """Read the table directly, past the store."""
    res = gw.read_sql("SELECT name, sample_id_val FROM lem_qc_samples "
                      "ORDER BY name")
    return res.get("rows") or []


def refuse_sample_writes(sql):
    return "lem_qc_samples" in sql and not sql.startswith("CREATE")


class TestARefusedWriteIsNeverReportedAsSaved:
    """One test per mutating method: it raises, and nothing changed.

    A standard is shared by every machine that runs it, so one dropped write
    here is not one instrument mis-judged — it is all of them.
    """

    def test_ensure_schema_refused_raises_and_does_not_remember_success(self):
        gw = QueueFullGateway()
        store = QcSampleStore(gw)
        with pytest.raises(QcSampleRefused):
            store.ensure_schema()
        assert store._schema_ready is False
        assert not gw.real.read_sql(
            "SELECT name FROM sqlite_master WHERE name='lem_qc_samples'"
        ).get("rows")

    def test_save_refused_raises(self):
        # Only the row write is refused. Refusing the CREATE too would make this
        # pass on `ensure_schema` raising, and it would go on passing with
        # `save`'s own confirmation deleted.
        store = QcSampleStore(QueueFullGateway(refuse=refuse_sample_writes))
        with pytest.raises(QcSampleRefused):
            store.save(QcSample("Cloud CRM", "CP", []))

    def test_save_refused_leaves_the_previous_definition_in_place(self):
        """The dangerous case: editing a band on a live standard. Dropped, the
        lab believes it widened a limit that every bench still judges by."""
        real = FakeLabCoreGateway()
        QcSampleStore(real).save(QcSample("Cloud CRM", "CP", [
            QcSampleTest("Cloud - D7689", "Cloud Point", -7.4, 2.8, 1.0)]))
        gw = QueueFullGateway(real, refuse=refuse_sample_writes)
        with pytest.raises(QcSampleRefused):
            QcSampleStore(gw).save(QcSample("Cloud CRM", "CP-2", []))
        assert library_in(real) == [{"name": "Cloud CRM",
                                     "sample_id_val": "CP"}]

    def test_delete_refused_raises_and_the_standard_is_still_listed(self):
        real = FakeLabCoreGateway()
        QcSampleStore(real).save(QcSample("Cloud CRM", "CP", []))
        gw = QueueFullGateway(real, refuse=refuse_sample_writes)
        with pytest.raises(QcSampleRefused):
            QcSampleStore(gw).delete("Cloud CRM")
        assert len(library_in(real)) == 1

    def test_import_does_not_swallow_a_refusal_as_a_bad_row(self):
        """`except ValueError` is for a half-defined V4 row. A row LabCore
        did not store is a different fact, and "imported 2" over an empty
        library is how a lab believes its standards came across."""
        gw = QueueFullGateway(refuse=refuse_sample_writes)
        with pytest.raises(QcSampleRefused):
            import_v4_samples(V4_CONFIG, QcSampleStore(gw))
        assert library_in(gw.real) == []


class TestChangeoverRefusesToReportAMoveItDidNotMake:
    """Changeover exists to stop a lot change silently ending QC. Reporting a
    turnover that did not happen is that failure wearing a success message."""

    def setup_lab(self, gw):
        from machine_map import QcTargetStore, WatchedTarget
        QcSampleStore(gw).save(QcSample("Cloud CRM", "CP", [
            QcSampleTest("Cloud Point", "Cloud Point", -7.4, 2.8, 1.0, "C")]))
        QcTargetStore(gw).assign("m1", [WatchedTarget("Cloud CRM",
                                                      "Cloud Point")])

    def test_a_refused_new_lot_raises_and_moves_nobody(self):
        from machine_map import QcTargetStore
        from qc_samples import changeover
        real = FakeLabCoreGateway()
        self.setup_lab(real)
        gw = QueueFullGateway(real, refuse=refuse_sample_writes)
        with pytest.raises(QcSampleRefused):
            changeover(gw, "Cloud CRM", "Cloud CRM AB26", "CP26")
        assert [r["name"] for r in library_in(real)] == ["Cloud CRM"]
        assert QcTargetStore(real).targets("m1")[0].sample == "Cloud CRM"

    def test_a_read_it_could_not_make_is_not_reported_as_not_found(self):
        """The read that DECIDES the write. Degraded to empty, a blip tells
        the operator their real lot does not exist — a 404 about something
        that is sitting on the bench — and would let a duplicate lot be
        created over a library it could not see."""
        real = FakeLabCoreGateway()
        self.setup_lab(real)
        gw = BlipGateway(real, fail=lambda s: "lem_qc_samples" in s)
        from qc_samples import changeover
        with pytest.raises(QcSampleUnavailable):
            changeover(gw, "Cloud CRM", "Cloud CRM AB26", "CP26")
        # and specifically NOT the "not found" it would have raised before
        with pytest.raises(QcSampleUnavailable):
            changeover(gw, "Cloud CRM", "Cloud CRM AB26", "CP26")


class TestSilenceIsNotSuccessEither:
    @pytest.mark.parametrize("answer", [None, {"ok": False}, "done"])
    def test_save(self, answer):
        store = QcSampleStore(QueueFullGateway(refuse=refuse_sample_writes,
                                               answer=answer))
        with pytest.raises(QcSampleRefused):
            store.save(QcSample("Cloud CRM", "CP", []))

    @pytest.mark.parametrize("answer", [None, {"ok": False}])
    def test_delete(self, answer):
        real = FakeLabCoreGateway()
        QcSampleStore(real).save(QcSample("Cloud CRM", "CP", []))
        gw = QueueFullGateway(real, refuse=refuse_sample_writes, answer=answer)
        with pytest.raises(QcSampleRefused):
            QcSampleStore(gw).delete("Cloud CRM")
        assert len(library_in(real)) == 1


class TestReadsDoNotInventAnEmptyLibrary:
    def test_list_samples_raises_on_a_blip(self):
        with pytest.raises(QcSampleUnavailable):
            QcSampleStore(BlipGateway()).list_samples()

    def test_the_payload_the_modules_pull_raises_too(self):
        """`/api/qc-samples` answering "this lab certifies nothing" is how a
        bench stops recognising its own standard's Lab ID and files a QC run
        as an ordinary customer sample."""
        with pytest.raises(QcSampleUnavailable):
            QcSampleStore(BlipGateway()).as_payload()
        with pytest.raises(QcSampleUnavailable):
            QcSampleStore(BlipGateway()).by_lab_id()

    def test_a_missing_table_is_still_an_empty_library(self, gw):
        store = QcSampleStore(gw)
        store._schema_ready = True          # pretend the CREATE never ran
        assert store.list_samples() == []

    def test_a_write_path_can_refuse_even_that(self, gw):
        store = QcSampleStore(gw)
        store._schema_ready = True
        with pytest.raises(QcSampleUnavailable):
            store.list_samples(missing_ok=False)


class TestTheExceptionsRoutesWillCatch:
    def test_a_route_can_catch_the_store_or_the_rule(self):
        assert issubclass(QcSampleRefused, QcSampleStoreError)
        assert issubclass(QcSampleRefused, LabCoreRefused)
        assert issubclass(QcSampleUnavailable, QcSampleStoreError)
        assert issubclass(QcSampleUnavailable, LabCoreUnavailable)
        assert issubclass(QcSampleStoreError, LabCoreError)

    def test_retryable_and_refused_stay_distinguishable(self):
        assert not issubclass(QcSampleRefused, LabCoreUnavailable)
        assert not issubclass(QcSampleUnavailable, LabCoreRefused)

    def test_the_message_names_the_standard(self):
        gw = QueueFullGateway(refuse=refuse_sample_writes)
        with pytest.raises(QcSampleRefused) as caught:
            QcSampleStore(gw).save(QcSample("Cloud CRM", "CP", []))
        assert "Cloud CRM" in str(caught.value)
        # And whatever detail the answer carried. This used to assert `"137"`,
        # pinning the message to the one field of the INVENTED shape.
        carried = [k for k in ("retry_after", "busy", "pending")
                   if k in refusal_shapes.current()]
        assert carried, "the shape under test carries no detail at all"
        assert any(k in str(caught.value) for k in carried)
