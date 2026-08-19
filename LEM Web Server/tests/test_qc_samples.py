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
