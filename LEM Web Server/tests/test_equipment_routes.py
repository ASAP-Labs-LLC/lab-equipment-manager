"""`/api/equipment/…` — the routes over the three stores.

Everything here is a store that already worked and was reachable from nothing.
What these tests hold is the part a store cannot: the boundary.

Four rules, each with a bug behind it somewhere in this tree:

* **A blip reads as a blip.** Never an empty tab, never a bare 500. "This
  instrument has no documents", "nothing is open against it" and "no history"
  are sentences an operator acts on, and inventing them out of a timed-out read
  is the failure `labcore_result` was extracted to end.
* **The equipment is validated before anything is written.** LabCore has no
  foreign keys, so a document or a corrective action filed against a uid that
  does not exist is accepted and then unreachable forever. And the gate has to
  tell "there is no such instrument" from "I could not ask" — answering 404 on
  a blip sends somebody to look for a bench that is standing right there.
* **Every mutating call carries `by`.** The session user, not a default. An
  action nobody can be shown to have taken is the gap the whole corrective
  action record exists to close.
* **The fleet-wide answers are ONE read each.** A badge per instrument, asked
  per instrument, is the N+1 the snapshot design forbids.
"""

import io
import json

import pytest

import equipment_documents
from labcore_gateway import FakeLabCoreGateway
from snapshot_service import SnapshotService
from web_app import create_app

PDF = b"%PDF-1.4\nhello certificate\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 40


def _seed_machine(gw, uid="m1", title="GC-1"):
    gw.sql("INSERT INTO lem_machine_status (machine_uid, title, status, "
           "reason, updated_at) VALUES (?, ?, 'GREEN', '', "
           "'2026-08-01T09:00:00')", [uid, title])


@pytest.fixture
def gw():
    gateway = FakeLabCoreGateway()
    SnapshotService(gateway).ensure_schema()
    _seed_machine(gateway)
    _seed_machine(gateway, "m2", "GC-2")
    return gateway


@pytest.fixture
def app(gw, tmp_path):
    application = create_app(gw, admin_password="Admin1", secret="t",
                             documents_root=str(tmp_path / "docs"))
    application.config.update(TESTING=True)
    return application


@pytest.fixture
def anon(app):
    return app.test_client()


@pytest.fixture
def client(app):
    """Signed in, because every write here is gated on a session."""
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user"] = "ryan"
    return c


class BlippingGateway(FakeLabCoreGateway):
    """LabCore stops answering reads after the app has warmed up.

    A read that fails must not become an empty answer anywhere in this file.
    """

    def __init__(self) -> None:
        super().__init__()
        self.blip = False

    def read_sql(self, sql, args=None, **kw):
        if self.blip and "lem_machine_status" not in sql:
            return {"error": "OperationalError: timed out"}
        return super().read_sql(sql, args, **kw)

    def sql(self, sql, args=None, **kw):
        if self.blip and not sql.strip().upper().startswith(
                ("CREATE", "PRAGMA")):
            return {"error": "LabCore is busy, try again later",
                    "busy": True, "retry_after": 4}
        return super().sql(sql, args, **kw)


@pytest.fixture
def blipping(tmp_path):
    gateway = BlippingGateway()
    SnapshotService(gateway).ensure_schema()
    _seed_machine(gateway)
    application = create_app(gateway, secret="t",
                             documents_root=str(tmp_path / "docs"))
    application.config.update(TESTING=True)
    c = application.test_client()
    with c.session_transaction() as sess:
        sess["user"] = "ryan"
    c.get("/api/machines")          # build the snapshot while LabCore answers
    gateway.blip = True
    return c, gateway


class CountingGateway(FakeLabCoreGateway):
    def __init__(self) -> None:
        super().__init__()
        self.reads = []

    def read_sql(self, sql, args=None, **kw):
        self.reads.append(sql)
        return super().read_sql(sql, args, **kw)


# ── levels ──────────────────────────────────────────────────────────────────

class TestLevelRoutes:
    def test_a_flat_lab_lists_no_levels_without_failing(self, anon):
        body = anon.get("/api/equipment/levels").get_json()
        assert body["levels"] == []
        assert body["default_level"] == ""
        assert body["ground_level"] == ""

    def test_create_rename_and_delete(self, client):
        made = client.post("/api/equipment/levels", json={"name": "Ground"})
        assert made.status_code == 200
        uid = made.get_json()["level"]["uid"]

        listed = client.get("/api/equipment/levels").get_json()
        assert [l["name"] for l in listed["levels"]] == ["Ground"]

        renamed = client.post(f"/api/equipment/levels/{uid}/rename",
                              json={"name": "Ground Floor"})
        assert renamed.get_json()["level"]["name"] == "Ground Floor"

        gone = client.delete(f"/api/equipment/levels/{uid}")
        assert gone.status_code == 200
        assert client.get("/api/equipment/levels").get_json()["levels"] == []

    def test_a_duplicate_name_is_refused_as_a_request_not_an_outage(self,
                                                                   client):
        client.post("/api/equipment/levels", json={"name": "Ground"})
        again = client.post("/api/equipment/levels", json={"name": "ground"})
        assert again.status_code == 400
        assert "already a level" in again.get_json()["error"]

    def test_a_nameless_level_is_refused(self, client):
        resp = client.post("/api/equipment/levels", json={"name": "  "})
        assert resp.status_code == 400

    def test_writes_need_a_session(self, anon):
        for call in (lambda: anon.post("/api/equipment/levels",
                                       json={"name": "x"}),
                     lambda: anon.delete("/api/equipment/levels/abc"),
                     lambda: anon.post("/api/equipment/default-level",
                                       json={"level_uid": ""}),
                     lambda: anon.post("/api/equipment/m1/level",
                                       json={"level_uid": ""}),
                     lambda: anon.post("/api/equipment/m1/level/up")):
            assert call().status_code == 401

    def test_setting_the_default_moves_nothing(self, client):
        ground = client.post("/api/equipment/levels",
                             json={"name": "Ground"}).get_json()["level"]
        second = client.post("/api/equipment/levels",
                             json={"name": "Second"}).get_json()["level"]
        client.post("/api/equipment/default-level",
                    json={"level_uid": second["uid"]})
        floor = client.get("/api/machines?fresh=1").get_json()
        assert floor["default_level"] == second["uid"]
        # …and every unplaced instrument is still on the ground.
        assert {m["level_uid"] for m in floor["machines"]} == {ground["uid"]}

    def test_the_default_must_name_a_real_level(self, client):
        client.post("/api/equipment/levels", json={"name": "Ground"})
        resp = client.post("/api/equipment/default-level",
                           json={"level_uid": "nope"})
        assert resp.status_code == 400

    def test_assigning_records_who_moved_it(self, client):
        second = client.post("/api/equipment/levels",
                             json={"name": "Ground"}).get_json()["level"]
        top = client.post("/api/equipment/levels",
                          json={"name": "Second"}).get_json()["level"]
        resp = client.post("/api/equipment/m1/level",
                           json={"level_uid": top["uid"]})
        assert resp.status_code == 200
        assert resp.get_json()["level_uid"] == top["uid"]
        machine = next(m for m in client.get("/api/machines?fresh=1")
                       .get_json()["machines"] if m["machine_uid"] == "m1")
        assert machine["level_uid"] == top["uid"]
        assert machine["level_moved_by"] == "ryan"
        assert second["uid"] != top["uid"]

    def test_up_and_down_clamp_at_the_ends(self, client):
        ground = client.post("/api/equipment/levels",
                             json={"name": "Ground"}).get_json()["level"]
        second = client.post("/api/equipment/levels",
                             json={"name": "Second"}).get_json()["level"]
        assert client.post("/api/equipment/m1/level/up").get_json()[
            "level_uid"] == second["uid"]
        assert client.post("/api/equipment/m1/level/up").get_json()[
            "level_uid"] == second["uid"]
        assert client.post("/api/equipment/m1/level/down").get_json()[
            "level_uid"] == ground["uid"]
        assert client.post("/api/equipment/m1/level/down").get_json()[
            "level_uid"] == ground["uid"]

    def test_an_unreadable_instrument_list_is_not_a_missing_instrument(
            self, tmp_path):
        """The gate's other half, and the one that is easy to get backwards.

        404 here tells somebody to go and look for a bench that is standing in
        front of them, and it is indistinguishable from the instrument really
        having been retired. "Could not ask" is a different sentence with a
        different instruction, and only one of them is true during a blip.

        The snapshot has NEVER built here, so the machine list has to be read
        live — which is the only state in which this can be got wrong.
        """
        class Mute(FakeLabCoreGateway):
            def read_sql(self, sql, args=None, **kw):
                return {"error": "OperationalError: timed out"}

        app = create_app(Mute(), secret="t",
                         documents_root=str(tmp_path / "docs"))
        app.config.update(TESTING=True)
        c = app.test_client()
        with c.session_transaction() as sess:
            sess["user"] = "ryan"
        for call in (lambda: c.post("/api/equipment/m1/level/up"),
                     lambda: c.post("/api/equipment/m1/level",
                                    json={"level_uid": "x"}),
                     lambda: _open(c),
                     lambda: _upload(c)):
            resp = call()
            assert resp.status_code in (502, 503), resp.status_code
            assert resp.get_json()["retry"] is True

    def test_a_move_is_refused_for_equipment_that_does_not_exist(self, client):
        client.post("/api/equipment/levels", json={"name": "Ground"})
        resp = client.post("/api/equipment/nope/level/up")
        assert resp.status_code == 404
        assert "No such equipment" in resp.get_json()["error"]

    def test_assigning_a_level_that_is_gone_is_a_request_error(self, client):
        client.post("/api/equipment/levels", json={"name": "Ground"})
        resp = client.post("/api/equipment/m1/level",
                           json={"level_uid": "vanished"})
        assert resp.status_code == 400

    def test_a_blip_is_a_blip_not_an_empty_ladder(self, blipping):
        client, _gw = blipping
        resp = client.get("/api/equipment/levels")
        assert resp.status_code in (502, 503)
        body = resp.get_json()
        assert body["retry"] is True
        assert "levels" not in body

    def test_a_refused_write_is_never_reported_as_saved(self, blipping):
        client, _gw = blipping
        resp = client.post("/api/equipment/levels", json={"name": "Ground"})
        assert resp.status_code in (502, 503)
        assert resp.get_json()["saved"] is False


# ── documents ───────────────────────────────────────────────────────────────

def _upload(client, uid="m1", name="cert.pdf", data=PDF):
    return client.post(f"/api/equipment/{uid}/documents",
                       data={"file": (io.BytesIO(data), name)},
                       content_type="multipart/form-data")


class TestDocumentRoutes:
    def test_an_instrument_with_no_documents_says_so(self, anon):
        body = anon.get("/api/equipment/m1/documents").get_json()
        assert body["documents"] == []

    def test_upload_list_download_delete(self, client):
        up = _upload(client)
        assert up.status_code == 200, up.get_json()
        doc = up.get_json()["document"]
        assert doc["filename"] == "cert.pdf"
        assert doc["uploaded_by"] == "ryan"

        listed = client.get("/api/equipment/m1/documents").get_json()
        assert [d["uid"] for d in listed["documents"]] == [doc["uid"]]

        got = client.get(f"/api/equipment/documents/{doc['uid']}/download")
        assert got.status_code == 200
        assert got.data == PDF
        assert "cert.pdf" in got.headers["Content-Disposition"]
        assert got.headers["Content-Type"].startswith("application/pdf")

        gone = client.delete(f"/api/equipment/documents/{doc['uid']}")
        assert gone.status_code == 200
        assert client.get("/api/equipment/m1/documents").get_json()[
            "documents"] == []

    def test_uploading_needs_a_session(self, anon):
        assert _upload(anon).status_code == 401

    def test_a_document_cannot_be_filed_against_nothing(self, client):
        """LabCore has no foreign keys: the row would be accepted and then
        unreachable forever."""
        resp = _upload(client, uid="ghost")
        assert resp.status_code == 404
        assert "No such equipment" in resp.get_json()["error"]

    def test_a_wrong_kind_of_file_is_refused_as_a_request(self, client):
        resp = _upload(client, name="payload.py", data=b"import os\n")
        assert resp.status_code == 400
        assert "not a kind of document" in resp.get_json()["error"]

    def test_a_renamed_executable_is_refused_on_its_bytes(self, client):
        resp = _upload(client, name="cert.pdf", data=b"MZ\x90\x00still an exe")
        assert resp.status_code == 400

    def test_an_empty_file_is_refused(self, client):
        resp = _upload(client, data=b"")
        assert resp.status_code == 400

    def test_no_file_part_is_a_request_error(self, client):
        resp = client.post("/api/equipment/m1/documents", data={},
                           content_type="multipart/form-data")
        assert resp.status_code == 400

    def test_the_same_bytes_twice_is_one_document(self, client):
        first = _upload(client).get_json()["document"]
        second = _upload(client).get_json()["document"]
        assert first["uid"] == second["uid"]
        assert len(client.get("/api/equipment/m1/documents")
                   .get_json()["documents"]) == 1

    def test_downloading_a_uid_that_is_not_there_is_404(self, client):
        assert client.get(
            "/api/equipment/documents/deadbeef/download").status_code == 404

    def test_deleting_a_uid_that_is_not_there_is_404(self, client):
        assert client.delete(
            "/api/equipment/documents/deadbeef").status_code == 404

    def test_the_fleet_badge_is_one_read(self):
        gw = CountingGateway()
        SnapshotService(gw).ensure_schema()
        _seed_machine(gw)
        _seed_machine(gw, "m2", "GC-2")
        _seed_machine(gw, "m3", "GC-3")
        app = create_app(gw, secret="t")
        app.config.update(TESTING=True)
        client = app.test_client()
        client.get("/api/machines")
        gw.reads.clear()
        body = client.get("/api/equipment/document-counts").get_json()
        assert body["counts"] == {"m1": 0, "m2": 0, "m3": 0}
        documents = [s for s in gw.reads if "lem_equipment_documents" in s]
        assert len(documents) == 1, documents

    def test_the_badge_counts_what_was_uploaded(self, client):
        _upload(client)
        _upload(client, name="trace.png", data=PNG)
        counts = client.get("/api/equipment/document-counts").get_json()
        assert counts["counts"]["m1"] == 2
        assert counts["counts"]["m2"] == 0

    def test_a_blip_does_not_empty_the_tab(self, blipping):
        client, _gw = blipping
        resp = client.get("/api/equipment/m1/documents")
        assert resp.status_code in (502, 503)
        assert "documents" not in resp.get_json()

    def test_a_refused_upload_is_not_reported_as_saved(self, blipping):
        client, _gw = blipping
        resp = _upload(client)
        assert resp.status_code in (502, 503)
        assert resp.get_json()["saved"] is False

    def test_retiring_an_instrument_takes_its_documents_and_its_level(
            self, client, gw):
        """Both, in one test, because LabCore has no foreign keys and a row
        left behind RE-ATTACHES if that uid is ever registered again — the
        instrument comes back standing on a level nobody put it on, with
        certificates nobody uploaded to it."""
        _upload(client)
        ground = client.post("/api/equipment/levels",
                             json={"name": "Ground"}).get_json()["level"]
        client.post("/api/equipment/m1/level", json={"level_uid": ground["uid"]})
        assert client.delete("/api/machines/m1",
                             json={}).get_json()["complete"] is True
        for table in ("lem_equipment_documents", "lem_machine_level"):
            left = gw.read_sql(
                f"SELECT COUNT(*) AS n FROM {table} "
                "WHERE machine_uid = 'm1'")["rows"][0]["n"]
            assert left == 0, table


# ── corrective actions and the timeline ─────────────────────────────────────

def _open(client, uid="m1", **body):
    payload = {"what_happened": "QC out of spec", "trigger_kind": "qc_fail"}
    payload.update(body)
    return client.post(f"/api/equipment/{uid}/actions", json=payload)


class TestHistoryRoutes:
    def test_an_instrument_with_no_history_still_answers(self, anon):
        body = anon.get("/api/equipment/m1/history").get_json()
        assert body["entries"] == []
        assert body["truncated"] is False

    def test_the_whole_lifecycle(self, client):
        made = _open(client, assigned_to="kaden", due_at="2026-09-01",
                     priority="high")
        assert made.status_code == 200
        action = made.get_json()["action"]
        assert action["opened_by"] == "ryan"
        assert action["assigned_to"] == "kaden"
        uid = action["uid"]

        assert client.post(f"/api/equipment/actions/{uid}/record",
                           json={"action_taken": "Replaced the cell"}
                           ).get_json()["action"]["action_by"] == "ryan"
        assert client.post(f"/api/equipment/actions/{uid}/verify",
                           json={"note": "Re-ran the standard"}
                           ).get_json()["action"]["verified_by"] == "ryan"
        closed = client.post(f"/api/equipment/actions/{uid}/close",
                             json={"note": "done"})
        assert closed.get_json()["action"]["outcome"] == "closed"
        floor = client.get("/api/equipment/open-actions").get_json()
        assert floor["total"] == 0 and floor["by_machine"] == {}

    def test_opening_an_action_is_readable_afterwards(self, client):
        uid = _open(client).get_json()["action"]["uid"]
        client.post(f"/api/equipment/actions/{uid}/note",
                    json={"note": "chased the vendor"})
        body = client.get(f"/api/equipment/actions/{uid}").get_json()
        assert body["action"]["uid"] == uid
        assert [e["kind"] for e in body["events"]] == ["note"]
        assert body["events"][0]["by_user"] == "ryan"

    def test_assigning_records_who_did_it(self, client):
        uid = _open(client).get_json()["action"]["uid"]
        resp = client.post(f"/api/equipment/actions/{uid}/assign",
                           json={"assigned_to": "kaden", "priority": "critical"})
        assert resp.get_json()["action"]["assigned_to"] == "kaden"
        events = client.get(f"/api/equipment/actions/{uid}").get_json()["events"]
        assert events[0]["kind"] == "assigned"
        assert events[0]["by_user"] == "ryan"

    def test_withdrawing_is_not_a_delete(self, client):
        uid = _open(client).get_json()["action"]["uid"]
        resp = client.post(f"/api/equipment/actions/{uid}/withdraw",
                           json={"reason": "wrong instrument"})
        assert resp.get_json()["action"]["outcome"] == "withdrawn"
        assert client.get(f"/api/equipment/actions/{uid}").status_code == 200

    def test_an_illegal_move_is_refused_with_a_reason(self, client):
        uid = _open(client).get_json()["action"]["uid"]
        resp = client.post(f"/api/equipment/actions/{uid}/close", json={})
        assert resp.status_code == 409
        assert "verify" in resp.get_json()["error"].lower()

    def test_an_action_cannot_be_filed_against_nothing(self, client):
        resp = _open(client, uid="ghost")
        assert resp.status_code == 404

    def test_an_action_needs_a_description(self, client):
        resp = _open(client, what_happened="   ")
        assert resp.status_code == 400

    def test_an_unknown_action_uid_is_404(self, client):
        assert client.get(
            "/api/equipment/actions/nope").status_code == 404
        assert client.post("/api/equipment/actions/nope/verify",
                           json={}).status_code == 404

    def test_writes_need_a_session(self, anon):
        assert _open(anon).status_code == 401
        assert anon.post("/api/equipment/actions/x/close",
                         json={}).status_code == 401

    def test_the_fleet_badge_is_one_read(self):
        gw = CountingGateway()
        SnapshotService(gw).ensure_schema()
        _seed_machine(gw)
        _seed_machine(gw, "m2", "GC-2")
        app = create_app(gw, secret="t")
        app.config.update(TESTING=True)
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user"] = "ryan"
        _open(client)
        _open(client, uid="m2")
        client.get("/api/machines")
        gw.reads.clear()
        body = client.get("/api/equipment/open-actions").get_json()
        assert sorted(body["by_machine"]) == ["m1", "m2"]
        assert body["counts"] == {"m1": 1, "m2": 1}
        reads = [s for s in gw.reads if "lem_corrective_actions" in s]
        assert len(reads) == 1, reads

    def test_the_timeline_carries_the_run_log_and_the_actions(self, client, gw):
        gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
               "test_name, value, detail) VALUES ('m1', "
               "'2026-08-01T08:00:00', 'qc', 'STD-1', 'Sulfur', '0.4', '{}')")
        _open(client)
        body = client.get("/api/equipment/m1/history").get_json()
        sources = {e["source"] for e in body["entries"]}
        assert "corrective_action" in sources and "log" in sources

    def test_the_timeline_says_when_it_is_truncated(self, client, gw):
        for i in range(6):
            gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, "
                   "lab_id, test_name, value, detail) VALUES ('m1', ?, 'run', "
                   "'', '', '', '{}')", [f"2026-08-0{i + 1}T08:00:00"])
        body = client.get("/api/equipment/m1/history?limit=3").get_json()
        assert len(body["entries"]) == 3
        assert body["truncated"] is True
        assert body["note"]

    def test_a_blip_never_reads_as_a_clean_history(self, blipping):
        """A supervisor reading "nothing is open" off a timed-out read is the
        failure the whole rule exists to prevent — nobody re-checks a list that
        says the floor is clean."""
        client, _gw = blipping
        for path in ("/api/equipment/m1/history",
                     "/api/equipment/m1/actions",
                     "/api/equipment/open-actions"):
            resp = client.get(path)
            assert resp.status_code in (502, 503), path
            assert resp.get_json()["retry"] is True

    def test_a_refused_write_is_never_reported_as_filed(self, blipping):
        client, _gw = blipping
        resp = _open(client)
        assert resp.status_code in (502, 503)
        assert resp.get_json()["saved"] is False


# ── the routes that were already there must not have moved ──────────────────

class TestNothingExistingBroke:
    def test_the_machine_routes_still_answer(self, client):
        for path in ("/api/machines", "/api/status", "/api/map", "/api/events",
                     "/api/schedule", "/api/maintenance", "/healthz"):
            assert client.get(path).status_code == 200, path

    def test_machine_uid_is_still_the_key_in_every_new_payload(self, client):
        client.post("/api/equipment/levels", json={"name": "Ground"})
        _upload(client)
        _open(client)
        for path in ("/api/equipment/m1/documents",
                     "/api/equipment/m1/actions",
                     "/api/equipment/open-actions"):
            assert "machine_uid" in json.dumps(client.get(path).get_json()), \
                path
        # The fleet answers are KEYED by machine_uid rather than carrying it as
        # a field, which is the same contract said in fewer bytes.
        counts = client.get(
            "/api/equipment/document-counts").get_json()["counts"]
        assert set(counts) == {"m1", "m2"}
        assert set(client.get("/api/equipment/open-actions")
                   .get_json()["counts"]) == {"m1"}
