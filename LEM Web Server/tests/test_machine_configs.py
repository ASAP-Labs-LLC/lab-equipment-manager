"""Equipment configuration belongs to the lab, not to one PC.

A machine's config — its source, its capture-and-map mappings, its QC wiring —
lived only inside the LabStation module instance on that bench. Reinstall
LabStation and it was gone; there was no way to reuse one, and no way to clean
up the ones nobody wanted. Export/import files were the workaround, and a
second source of truth.

So configs move into LabCore (`lem_machine_config`). The station module reads
and writes this table directly through its injected labcore_* helpers — the
same shape as every other seam — and this master view exposes it over HTTP so
the floor can list, duplicate and delete them.

Runtime state must never travel with a config: a duplicate that carried the
source uid, file position or a standing override would corrupt both machines.
"""
import json

import pytest

from labcore_gateway import FakeLabCoreGateway
from machine_configs import RUNTIME_KEYS, MachineConfigStore


@pytest.fixture
def gw():
    return FakeLabCoreGateway()


@pytest.fixture
def store(gw):
    return MachineConfigStore(gw)


def a_config(**over):
    """Roughly what the station module serialises."""
    base = {
        "uid": "m1", "title": "OptiMPP 1", "source_type": "single_csv",
        "csv_path": "C:/prints/optimpp.csv", "delimiter": ",",
        "mappings": [{"methods": ["Cloud Point"], "csv_header": "Cloud"}],
        "tests": [{"name": "Cloud Point", "qc_sample_id": "CP"}],
        "maint_tasks": [{"name": "Annual cal", "kind": "calibration"}],
        # runtime — must not survive a duplicate
        "last_position": 8172, "last_mtime": 1754.5,
        "last_result_file": "lem_latest_optimpp.csv",
        "manual_override": "SERVICE", "override_comment": "sensor swap",
    }
    base.update(over)
    return base


class TestSaveAndLoad:
    def test_nothing_saved_reads_as_none(self, store):
        assert store.get("nobody") is None

    def test_a_config_round_trips(self, store):
        store.save("m1", "OptiMPP 1", a_config(), by="kaden")
        got = store.get("m1")
        assert got["title"] == "OptiMPP 1"
        assert got["config"]["csv_path"] == "C:/prints/optimpp.csv"
        assert got["updated_by"] == "kaden"
        assert got["updated_at"]

    def test_saving_again_replaces_rather_than_duplicates(self, store):
        store.save("m1", "OptiMPP 1", a_config())
        store.save("m1", "OptiMPP 1 (rebuilt)", a_config(delimiter=";"))
        assert len(store.list()) == 1
        got = store.get("m1")
        assert got["title"] == "OptiMPP 1 (rebuilt)"
        assert got["config"]["delimiter"] == ";"

    def test_a_config_needs_a_uid(self, store):
        with pytest.raises(ValueError):
            store.save("", "No uid", a_config())

    def test_a_config_needs_a_title(self, store):
        with pytest.raises(ValueError):
            store.save("m1", "   ", a_config())

    def test_the_listing_omits_the_blob(self, store):
        """The picker needs names, not every mapping in the lab."""
        store.save("m1", "OptiMPP 1", a_config())
        row = store.list()[0]
        assert row["machine_uid"] == "m1" and row["title"] == "OptiMPP 1"
        assert "config" not in row

    def test_the_listing_is_ordered_by_title(self, store):
        store.save("b", "Zeta", a_config())
        store.save("a", "Alpha", a_config())
        assert [r["title"] for r in store.list()] == ["Alpha", "Zeta"]

    def test_a_corrupt_blob_does_not_take_the_floor_down(self, store, gw):
        store.save("m1", "OptiMPP 1", a_config())
        gw.sql("UPDATE lem_machine_config SET config = ? WHERE machine_uid = ?",
               ["{not json", "m1"])
        got = store.get("m1")
        assert got is not None and got["config"] == {}

    def test_delete_removes_it(self, store):
        store.save("m1", "OptiMPP 1", a_config())
        store.delete("m1")
        assert store.get("m1") is None and store.list() == []


class TestDuplicate:
    def test_it_copies_the_mappings(self, store):
        store.save("m1", "OptiMPP 1", a_config())
        made = store.duplicate("m1", "OptiMPP 3")
        copy = store.get(made["machine_uid"])
        assert copy["config"]["mappings"] == a_config()["mappings"]
        assert copy["config"]["csv_path"] == "C:/prints/optimpp.csv"

    def test_it_gets_a_new_uid(self, store):
        store.save("m1", "OptiMPP 1", a_config())
        made = store.duplicate("m1", "OptiMPP 3")
        assert made["machine_uid"] and made["machine_uid"] != "m1"

    def test_the_original_survives_untouched(self, store):
        store.save("m1", "OptiMPP 1", a_config())
        store.duplicate("m1", "OptiMPP 3")
        assert store.get("m1")["title"] == "OptiMPP 1"
        assert len(store.list()) == 2

    def test_the_new_title_is_used(self, store):
        store.save("m1", "OptiMPP 1", a_config())
        made = store.duplicate("m1", "OptiMPP 3")
        assert made["title"] == "OptiMPP 3"
        assert store.get(made["machine_uid"])["config"]["title"] == "OptiMPP 3"

    @pytest.mark.parametrize("key", sorted(RUNTIME_KEYS))
    def test_no_runtime_state_travels(self, store, key):
        """A duplicate carrying the source's file position or override would
        corrupt both machines."""
        store.save("m1", "OptiMPP 1", a_config())
        made = store.duplicate("m1", "OptiMPP 3")
        config = store.get(made["machine_uid"])["config"]
        assert config.get(key) in (None, "", 0, 0.0), key

    def test_the_uid_inside_the_blob_matches_the_new_row(self, store):
        store.save("m1", "OptiMPP 1", a_config())
        made = store.duplicate("m1", "OptiMPP 3")
        got = store.get(made["machine_uid"])
        assert got["config"]["uid"] == made["machine_uid"]

    def test_duplicating_nothing_is_an_error(self, store):
        with pytest.raises(LookupError):
            store.duplicate("ghost", "New")

    def test_a_duplicate_needs_a_title(self, store):
        store.save("m1", "OptiMPP 1", a_config())
        with pytest.raises(ValueError):
            store.duplicate("m1", "  ")


class TestBlankConfigForANewMachine:
    def test_a_new_machine_starts_empty_but_registered(self, store):
        made = store.create("Multitek 2")
        assert made["machine_uid"]
        got = store.get(made["machine_uid"])
        assert got["title"] == "Multitek 2"
        assert got["config"]["uid"] == made["machine_uid"]
        assert got["config"]["title"] == "Multitek 2"

    def test_two_new_machines_do_not_collide(self, store):
        a = store.create("One")
        b = store.create("Two")
        assert a["machine_uid"] != b["machine_uid"]

    def test_a_new_machine_needs_a_title(self, store):
        with pytest.raises(ValueError):
            store.create("")


# ── over HTTP, for the floor ────────────────────────────────────────────────

class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


class TestEndpoints:
    @pytest.fixture
    def client(self, gw):
        from web_app import create_app
        app = create_app(gw, authenticator=StubAuth(), secret="s")
        app.config["TESTING"] = True
        return app.test_client()

    @pytest.fixture
    def signed_in(self, client):
        client.post("/api/login", json={"username": "k", "password": "good"})
        return client

    def test_listing_is_readable_without_an_account(self, client):
        r = client.get("/api/machine-configs")
        assert r.status_code == 200 and r.get_json()["configs"] == []

    def test_a_saved_config_appears(self, signed_in, gw):
        MachineConfigStore(gw).save("m1", "OptiMPP 1", a_config())
        body = signed_in.get("/api/machine-configs").get_json()
        assert [c["title"] for c in body["configs"]] == ["OptiMPP 1"]

    def test_one_config_can_be_fetched_whole(self, signed_in, gw):
        MachineConfigStore(gw).save("m1", "OptiMPP 1", a_config())
        body = signed_in.get("/api/machine-configs/m1").get_json()
        assert body["config"]["csv_path"] == "C:/prints/optimpp.csv"

    def test_a_missing_config_is_a_404(self, signed_in):
        assert signed_in.get("/api/machine-configs/ghost").status_code == 404

    def test_creating_needs_an_account(self, client):
        r = client.post("/api/machine-configs", json={"title": "New"})
        assert r.status_code == 401

    def test_creating_a_blank_machine(self, signed_in):
        r = signed_in.post("/api/machine-configs", json={"title": "Multitek 2"})
        assert r.status_code == 200
        assert r.get_json()["machine_uid"]

    def test_creating_without_a_title_is_refused(self, signed_in):
        r = signed_in.post("/api/machine-configs", json={"title": ""})
        assert r.status_code == 400 and r.get_json()["error"]

    def test_duplicating_over_http(self, signed_in, gw):
        MachineConfigStore(gw).save("m1", "OptiMPP 1", a_config())
        r = signed_in.post("/api/machine-configs/m1/duplicate",
                           json={"title": "OptiMPP 3"})
        assert r.status_code == 200
        uid = r.get_json()["machine_uid"]
        got = signed_in.get(f"/api/machine-configs/{uid}").get_json()
        assert got["config"]["mappings"] == a_config()["mappings"]
        assert "last_position" not in got["config"]
        assert "manual_override" not in got["config"]

    def test_duplicating_a_ghost_is_a_404(self, signed_in):
        r = signed_in.post("/api/machine-configs/ghost/duplicate",
                           json={"title": "X"})
        assert r.status_code == 404

    def test_deleting_needs_an_account(self, client, gw):
        MachineConfigStore(gw).save("m1", "OptiMPP 1", a_config())
        assert client.delete("/api/machine-configs/m1").status_code == 401

    def test_deleting_over_http(self, signed_in, gw):
        MachineConfigStore(gw).save("m1", "OptiMPP 1", a_config())
        assert signed_in.delete("/api/machine-configs/m1").status_code == 200
        assert signed_in.get("/api/machine-configs/m1").status_code == 404

    def test_deleting_a_machine_also_drops_its_config(self, signed_in, gw):
        """`DELETE /api/machines/<uid>` already clears status, specs and
        control; a stranded config would come back to life on the next pick."""
        MachineConfigStore(gw).save("m1", "OptiMPP 1", a_config())
        signed_in.delete("/api/machines/m1")
        assert MachineConfigStore(gw).get("m1") is None


# ── the write queue answers "no", and the answer must be read ────────────────
#
# LabCore's queue refuses past ~100 pending BY ANSWERING rather than raising.
# Every test in this module runs TWICE, once per refusal shape — see
# tests/refusal_shapes.py for which of the two is evidence and which is a
# fixture. In short: the error dict carrying `busy` is recorded from a real
# incident; the one with no "error" key is synthetic, kept because
# `{"error": ...}` is the ONE shape the old `if not res.get("error")` code
# already handled, so a suite refusing only that way proves nothing.

from labcore_result import LabCoreError, LabCoreRefused, LabCoreUnavailable
from machine_configs import (ConfigReadUnavailable, ConfigWriteRefused,
                             MachineConfigError)

import refusal_shapes                                   # noqa: E402

pytestmark = pytest.mark.usefixtures("both_refusal_shapes")

# SYNTHETIC — see refusal_shapes.
QUEUE_FULL = refusal_shapes.NO_ERROR_KEY


class QueueFullGateway:
    """A real gateway with a full write queue in front of it.

    Reads pass through, so every test can assert the config store's actual
    contents rather than just that a call raised. CREATE is allowed by default
    so the store is exercised past ensure_schema.
    """

    def __init__(self, inner, refuse_when=None, answer=None):
        self.inner = inner
        # `None` means "whichever shape this run of the suite is driving".
        self.answer = answer
        self.refuse_when = refuse_when or (
            lambda sql: not sql.lstrip().upper().startswith("CREATE"))

    def sql(self, sql, args=None, **kw):
        if self.refuse_when(sql):
            if self.answer is None:
                return refusal_shapes.current()
            return self.answer
        return self.inner.sql(sql, args, **kw)

    def read_sql(self, sql, args=None, **kw):
        return self.inner.read_sql(sql, args, **kw)


class BlipGateway:
    """Writes work (so a test can arrange state), reads time out."""

    def __init__(self, inner):
        self.inner = inner
        self.blind = False

    def sql(self, sql, args=None, **kw):
        return self.inner.sql(sql, args, **kw)

    def read_sql(self, sql, args=None, **kw):
        if self.blind:
            return {"error": "ReadTimeout: read timed out"}
        return self.inner.read_sql(sql, args, **kw)


class TestRefusedConfigWrites:
    def test_a_refused_save_raises_and_keeps_the_old_config(self, gw, store):
        """A whole bench's mappings, QC wiring and PM tasks, lost while the
        floor says saved."""
        store.save("m1", "OptiMPP 1", a_config(), by="kaden")
        blocked = MachineConfigStore(QueueFullGateway(gw))
        with pytest.raises(ConfigWriteRefused):
            blocked.save("m1", "OptiMPP 1 renamed", a_config(csv_path="X:/new"))
        got = store.get("m1")
        assert got["title"] == "OptiMPP 1"
        assert got["config"]["csv_path"] == "C:/prints/optimpp.csv"

    def test_a_refused_create_registers_nothing(self, gw, store):
        blocked = MachineConfigStore(QueueFullGateway(gw))
        with pytest.raises(ConfigWriteRefused):
            blocked.create("Multitek S")
        assert store.list() == []

    def test_a_refused_duplicate_leaves_one_config(self, gw, store):
        store.save("m1", "OptiMPP 1", a_config())
        blocked = MachineConfigStore(QueueFullGateway(gw))
        with pytest.raises(ConfigWriteRefused):
            blocked.duplicate("m1", "OptiMPP 2")
        assert [c["title"] for c in store.list()] == ["OptiMPP 1"]

    def test_a_refused_delete_raises_and_the_config_survives(self, gw, store):
        """A config the floor reported as deleted still offers itself in the
        module's picker, so a bench adopts a machine that was retired."""
        store.save("m1", "OptiMPP 1", a_config())
        blocked = MachineConfigStore(QueueFullGateway(gw))
        with pytest.raises(ConfigWriteRefused):
            blocked.delete("m1")
        assert store.get("m1") is not None

    def test_a_refused_create_table_is_not_remembered_as_done(self, gw, store):
        """`_ready` used to be set whatever LabCore answered, so one refusal at
        boot left the store writing into a table that was never made."""
        blocking = QueueFullGateway(gw, refuse_when=lambda sql: True)
        blocked = MachineConfigStore(blocking)
        with pytest.raises(ConfigWriteRefused):
            blocked.save("m1", "OptiMPP 1", a_config())
        blocking.refuse_when = lambda sql: False
        blocked.save("m1", "OptiMPP 1", a_config())   # retried, not stuck
        assert store.get("m1")["title"] == "OptiMPP 1"

    @pytest.mark.parametrize("answer", [None, {"ok": False},
                                        refusal_shapes.NO_ERROR_KEY])
    def test_silence_is_never_success(self, gw, store, answer):
        blocked = MachineConfigStore(QueueFullGateway(gw, answer=answer))
        with pytest.raises(ConfigWriteRefused):
            blocked.save("m1", "OptiMPP 1", a_config())
        assert store.get("m1") is None


class TestReadsDoNotInventAMissingConfig:
    def test_a_blip_is_not_a_404_about_a_real_machine(self, gw, store):
        """get() returning None on a read error made the route answer "No
        configuration for that machine" about a machine running right now."""
        store.save("m1", "OptiMPP 1", a_config())
        blip = BlipGateway(gw)
        blind = MachineConfigStore(blip)
        blind.ensure_schema()
        blip.blind = True
        with pytest.raises(ConfigReadUnavailable):
            blind.get("m1")

    def test_a_blip_does_not_empty_the_picker(self, gw, store):
        """An empty picker invites a second config for a machine that has one."""
        store.save("m1", "OptiMPP 1", a_config())
        blip = BlipGateway(gw)
        blind = MachineConfigStore(blip)
        blind.ensure_schema()
        blip.blind = True
        with pytest.raises(ConfigReadUnavailable):
            blind.list()

    def test_a_blip_does_not_make_duplicate_deny_the_source(self, gw, store):
        """duplicate() reads through get(); "could not ask" served as "does not
        exist" would refuse to copy a config that plainly exists."""
        store.save("m1", "OptiMPP 1", a_config())
        blip = BlipGateway(gw)
        blind = MachineConfigStore(blip)
        blind.ensure_schema()
        blip.blind = True
        with pytest.raises(ConfigReadUnavailable):
            blind.duplicate("m1", "OptiMPP 2")

    def test_a_genuinely_absent_config_is_still_None(self, store):
        """The distinction has to cut both ways or it is just noise."""
        assert store.get("nobody") is None


class TestOneNameToCatch:
    def test_a_refusal_is_catchable_both_ways(self, gw):
        blocked = MachineConfigStore(QueueFullGateway(gw))
        for expected in (MachineConfigError, LabCoreRefused, LabCoreError):
            with pytest.raises(expected):
                blocked.save("m1", "OptiMPP 1", a_config())

    def test_a_blip_is_catchable_both_ways(self, gw, store):
        blip = BlipGateway(gw)
        blind = MachineConfigStore(blip)
        blind.ensure_schema()
        blip.blind = True
        for expected in (MachineConfigError, LabCoreUnavailable, LabCoreError):
            with pytest.raises(expected):
                blind.list()

    def test_the_message_names_the_configuration(self, gw):
        blocked = MachineConfigStore(QueueFullGateway(gw))
        with pytest.raises(ConfigWriteRefused) as caught:
            blocked.save("m1", "OptiMPP 1", a_config())
        assert "OptiMPP 1" in str(caught.value)
