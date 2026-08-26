"""A refusal is not a write.

LabCore serialises its write queue at roughly 1.5 ops/sec and turns new work
away past ~100 pending. It does NOT refuse by raising. It returns, normally,
with an error dict:

    {"error": "LabCore is busy — write queue is deep. Retry shortly.",
     "busy": true, "retry_after": 5}

Several endpoints in `web_app.py` called `gateway.sql(...)`, threw the answer
away, and told the client `200 {"ok": true}`. A supervisor could set a
correction factor, watch it succeed, and have it not exist — while the lab went
on reporting uncorrected results (ISO/IEC 17025 §7.8.2 requires the reported
result to BE the measurement result, and `corrected = raw + correction` is
applied to every reading the bench takes).

This is the same class as the failure in notes.md, where a bulk checklist
import "reported 'imported 3094' while nothing landed" because the loop counted
refusals as successes. `checklists.import_state` and `db_config_store` were
fixed then; the endpoints were not.

Every test here models the gateway LabCore actually is — one that says no by
answering, never by raising.
"""
import json
from datetime import datetime

import pytest

from labcore_gateway import FakeLabCoreGateway
from live_presence import LivePresence


TOKEN = "test-token"

# The refusal LabCore really sends, copied from LabCore.py `_reject_if_busy`.
BUSY = {"error": "LabCore is busy — write queue is deep. Retry shortly.",
        "busy": True, "retry_after": 5}

# The OTHER kind of no. A malformed statement is refused for good, and a client
# that retries it forever is its own kind of bug — so the two must not be
# reported the same way.
BROKEN = {"error": "OperationalError: no such column: correcton"}


def is_write(sql: str) -> bool:
    """A statement that changes rows, as opposed to one that declares a table.

    Schema DDL is let through by default so the READ side of an endpoint still
    works and the test is aimed at the write it is actually about. A test that
    wants the DDL refused too says so.
    """
    return sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))


class RefusingGateway(FakeLabCoreGateway):
    """A gateway that refuses the way LabCore refuses — an error dict,
    returned, never raised.

    The station module's suite has `BusyLabCore` for the same job; this one
    subclasses the real fake instead of standing alone, because the endpoints
    under test READ before they write (the correction save looks up the machine
    and the previous value first) and a gateway that cannot answer a SELECT
    would fail them for the wrong reason.

    `refuse` decides which statements are turned away, so a test can refuse
    statement 2 of 3 and check what the client is told about statement 1.
    """

    def __init__(self, refuse=is_write, answer=None):
        super().__init__()
        self._refuse = refuse
        self._answer = dict(answer or BUSY)
        self.refused = []
        self.accepted = []

    def sql(self, sql, args=None, **kw):
        if self._refuse(sql):
            self.refused.append(sql)
            return dict(self._answer)
        self.accepted.append(sql)
        return super().sql(sql, args, **kw)


def refuse_only(*fragments):
    """Refuse just the statements naming one of these tables/columns."""
    def decide(sql):
        return is_write(sql) and any(f in sql for f in fragments)
    return decide


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


def seeded(gateway):
    gateway.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
                "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
                "reason TEXT, updated_at TEXT)")
    for uid, title in (("pac-flash-2", "PAC Flash 2"),
                       ("multitek-ns", "Multitek NS")):
        gateway.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
                    [uid, title, "GREEN", "", "2026-08-05T13:00:00"])
    return gateway


def app_on(gateway):
    from web_app import create_app
    application = create_app(gateway, authenticator=StubAuth(), secret="s",
                             live=LivePresence(), live_token=TOKEN)
    application.config["TESTING"] = True
    return application


def signed_in_client(gateway):
    client = app_on(gateway).test_client()
    client.post("/api/login", json={"username": "k", "password": "good"})
    return client


def busy_bench(refuse=is_write, answer=None):
    """A signed-in client on a lab whose LabCore is refusing writes.

    Seeded BEFORE the refusal takes effect, so the machines exist and the only
    thing failing is the write under test.
    """
    gateway = RefusingGateway(refuse=lambda sql: False)
    seeded(gateway)
    gateway._refuse = refuse
    gateway._answer = dict(answer or BUSY)
    return gateway, signed_in_client(gateway)


def stale_of(response):
    body = response.get_json()
    assert isinstance(body, dict), f"expected a JSON object, got {body!r}"
    assert "stale" in body, f"no `stale` key in {body!r}"
    return set(body["stale"])


def push(client, uid="pac-flash-2"):
    return client.post("/api/live",
                       json={"machine_uid": uid, "status": "GREEN",
                             "reason": "", "at": "", "interval_seconds": 30},
                       headers={"X-LEM-Token": TOKEN})


def save_correction(client, uid="pac-flash-2", test="Flash Point",
                    value="-3.0"):
    return client.post(f"/api/machines/{uid}/corrections",
                       json={"test_name": test, "correction": value})


def set_override(client, uid="pac-flash-2", mode="SERVICE"):
    return client.post(f"/api/machines/{uid}/override",
                       json={"override": mode, "comment": "belt broke"})


# ── the seam ────────────────────────────────────────────────────────────────

class TestTheRefusalSeam:
    """One place that answers "did LabCore refuse this", rather than an
    ad-hoc `.get("error")` at every write site — which is exactly how the one
    site nobody remembered went unguarded. The station module already has this
    function; the web server gets the same shape so the two read alike."""

    def test_a_refusal_reads_as_a_refusal(self):
        from labcore_gateway import refusal_reason
        assert refusal_reason(BUSY) == BUSY["error"]

    def test_a_write_that_landed_reads_as_no_refusal(self):
        from labcore_gateway import refusal_reason
        assert refusal_reason({"ok": True, "rows_affected": 1}) == ""

    def test_nothing_at_all_is_not_a_refusal(self):
        """A store that returns None has not been told it failed."""
        from labcore_gateway import refusal_reason
        assert refusal_reason(None) == ""

    def test_busy_is_told_apart_from_broken(self):
        from labcore_gateway import is_busy
        assert is_busy(BUSY) is True
        assert is_busy(BROKEN) is False

    def test_the_retry_hint_is_read_off_the_refusal(self):
        from labcore_gateway import retry_after_seconds
        assert retry_after_seconds(BUSY) == 5.0

    def test_a_refusal_that_named_no_delay_says_so(self):
        """None, not a default. A wait this function invented would be
        indistinguishable from one LabCore asked for."""
        from labcore_gateway import retry_after_seconds
        assert retry_after_seconds(BROKEN) is None

    @pytest.mark.parametrize("junk", ["soon", None, True, -1, 0,
                                      float("nan"), float("inf")])
    def test_an_unusable_delay_reads_as_none(self, junk):
        from labcore_gateway import retry_after_seconds
        assert retry_after_seconds({"error": "no", "retry_after": junk}) is None


# ── correction factors: the write the benches act on ────────────────────────

class TestARefusedCorrectionSaveIsNotAnOk:
    """The headline defect. `corrected = raw + correction` is applied to every
    measurement before it is written, displayed or QC-judged, so a save that
    silently did not land leaves the lab reporting uncorrected results while
    the person who set the offset believes it is in force."""

    def test_it_is_not_a_2xx(self):
        _gw, client = busy_bench()
        assert save_correction(client).status_code >= 400

    def test_it_does_not_claim_ok(self):
        _gw, client = busy_bench()
        body = save_correction(client).get_json()
        assert "ok" not in body, body

    def test_it_says_what_went_wrong(self):
        _gw, client = busy_bench()
        body = save_correction(client).get_json()
        assert "busy" in body.get("error", "").lower(), body

    def test_a_busy_queue_is_a_503(self):
        """Transient. 503 is the status whose whole meaning is "come back"."""
        _gw, client = busy_bench()
        assert save_correction(client).status_code == 503

    def test_the_retry_hint_reaches_the_client(self):
        _gw, client = busy_bench()
        response = save_correction(client)
        assert response.get_json().get("retry_after") == 5
        assert response.headers.get("Retry-After") == "5"

    def test_a_client_is_told_it_is_worth_retrying(self):
        _gw, client = busy_bench()
        assert save_correction(client).get_json().get("retryable") is True

    def test_the_factor_really_did_not_change(self):
        """The point of the whole exercise: the answer the client gets and the
        state of `lem_correction_factors` agree."""
        gw, client = busy_bench()
        save_correction(client, value="-3.0")
        saved = client.get("/api/machines/pac-flash-2/corrections").get_json()
        assert saved["corrections"] == [], saved

    def test_it_leaves_no_stale_note(self):
        """A note sends the bench to LabCore to re-read a table that did not
        change — a pointless read on the exact queue that just said it is too
        deep, spent confirming nothing happened."""
        _gw, client = busy_bench()
        save_correction(client)
        assert stale_of(push(client)) == set()

    def test_it_is_not_written_into_the_audit_log(self):
        """`_audit` records "correction factor set" with who/from/to. Recording
        a change that did not happen puts a falsehood in the one log an
        assessor reads."""
        gw, client = busy_bench(refuse=refuse_only("lem_correction_factors"))
        save_correction(client)
        rows = gw.read_sql("SELECT test_name FROM lem_machine_log "
                           "WHERE kind = 'config'").get("rows") or []
        assert [r["test_name"] for r in rows] == [], rows


class TestABrokenCorrectionSaveIsNotWorthRetrying:
    """"LabCore is busy" and "this write is invalid" are different answers. A
    client that retries a permanently-bad write forever is its own kind of
    bug, so the transient one must be distinguishable without reading English
    out of a message."""

    def test_it_is_not_a_503(self):
        _gw, client = busy_bench(answer=BROKEN)
        assert save_correction(client).status_code != 503

    def test_it_is_still_not_a_2xx(self):
        _gw, client = busy_bench(answer=BROKEN)
        assert save_correction(client).status_code >= 400

    def test_it_says_not_to_retry(self):
        _gw, client = busy_bench(answer=BROKEN)
        assert save_correction(client).get_json().get("retryable") is False

    def test_it_offers_no_retry_delay(self):
        _gw, client = busy_bench(answer=BROKEN)
        response = save_correction(client)
        assert "retry_after" not in response.get_json()
        assert "Retry-After" not in response.headers


class TestARefusedCorrectionRemovalIsNotAnOk:
    """Removing an offset changes every future reading exactly as setting one
    does. A delete reported as done, that did not happen, leaves the bench
    quietly still applying it."""

    def setup_bench(self):
        gw = RefusingGateway(refuse=lambda sql: False)
        seeded(gw)
        client = signed_in_client(gw)
        assert save_correction(client, value="-3.0").status_code == 200
        gw._refuse = refuse_only("lem_correction_factors")
        return gw, client

    def test_it_is_not_a_2xx(self):
        _gw, client = self.setup_bench()
        response = client.delete(
            "/api/machines/pac-flash-2/corrections/Flash Point")
        assert response.status_code == 503, response.get_json()

    def test_it_does_not_claim_ok(self):
        _gw, client = self.setup_bench()
        body = client.delete(
            "/api/machines/pac-flash-2/corrections/Flash Point").get_json()
        assert "ok" not in body and "deleted" not in body, body

    def test_the_factor_is_still_there(self):
        _gw, client = self.setup_bench()
        client.delete("/api/machines/pac-flash-2/corrections/Flash Point")
        saved = client.get("/api/machines/pac-flash-2/corrections").get_json()
        assert [c["correction"] for c in saved["corrections"]] == [-3.0]

    def test_it_leaves_no_stale_note(self):
        _gw, client = self.setup_bench()
        push(client)                      # retire the note the save left
        push(client)
        client.delete("/api/machines/pac-flash-2/corrections/Flash Point")
        assert stale_of(push(client)) == set()


# ── manual override: the other write the benches act on ─────────────────────

class TestARefusedOverrideIsNotAnOk:
    """A manual override is how a bench is taken out of service. Reported as
    applied and not applied, the instrument stays green on the floor and
    somebody runs a customer sample on it."""

    def test_it_is_not_a_2xx(self):
        _gw, client = busy_bench()
        assert set_override(client).status_code >= 400

    def test_a_busy_queue_is_a_503(self):
        _gw, client = busy_bench()
        assert set_override(client).status_code == 503

    def test_it_does_not_claim_ok(self):
        _gw, client = busy_bench()
        assert "ok" not in set_override(client).get_json()

    def test_the_retry_hint_reaches_the_client(self):
        _gw, client = busy_bench()
        response = set_override(client)
        assert response.get_json().get("retry_after") == 5
        assert response.headers.get("Retry-After") == "5"

    def test_it_leaves_no_stale_note(self):
        """`mark_stale` here is unconditional today — it fires whether or not
        `lem_machine_control` took the row."""
        _gw, client = busy_bench()
        set_override(client)
        assert stale_of(push(client)) == set()

    def test_clearing_an_override_is_refused_the_same_way(self):
        """Clearing is as urgent as setting: a bench left on SERVICE because
        the clear was silently dropped is an instrument nobody can use."""
        _gw, client = busy_bench()
        response = set_override(client, mode="")
        assert response.status_code == 503
        assert stale_of(push(client)) == set()

    def test_a_state_nobody_defined_is_still_a_400(self):
        """Regression. An unknown override never reaches LabCore, so it is a
        bad request — not a transient one, and not something to retry."""
        _gw, client = busy_bench()
        response = client.post("/api/machines/pac-flash-2/override",
                               json={"override": "PURPLE", "comment": "x"})
        assert response.status_code == 400
        assert stale_of(push(client)) == set()


# ── multi-statement saves: no transaction, so no pretending ─────────────────

class TestARefusedDeleteReportsWhatActuallyLanded:
    """Retiring a machine is several statements and there is no transaction
    across queue ops. If the live status goes and the override row does not,
    the honest answer says so — reporting `{"ok": true}` leaves a control row
    for a machine that no longer exists, and reporting nothing at all leaves
    somebody to find it."""

    def delete_with(self, refuse):
        gw, client = busy_bench(refuse=refuse)
        response = client.delete("/api/machines/pac-flash-2",
                                 json={"confirm": True})
        return gw, client, response

    def test_a_refusal_partway_through_is_not_a_success(self):
        _gw, _c, response = self.delete_with(refuse_only("lem_machine_control"))
        assert response.status_code >= 400
        assert "ok" not in response.get_json()

    def test_the_client_is_told_which_step_failed(self):
        _gw, _c, response = self.delete_with(refuse_only("lem_machine_control"))
        body = response.get_json()
        assert body.get("partial") is True, body
        assert "manual override" in body.get("not_landed", []), body

    def test_the_client_is_told_what_did_land(self):
        _gw, _c, response = self.delete_with(refuse_only("lem_machine_control"))
        body = response.get_json()
        assert "live status" in body.get("landed", []), body
        assert "QC specs" in body.get("landed", []), body

    def test_a_refusal_on_the_first_step_lands_nothing(self):
        _gw, _c, response = self.delete_with(refuse_only("lem_machine_status"))
        body = response.get_json()
        assert body.get("landed") == [], body

    def test_it_stops_rather_than_pushing_into_a_full_queue(self):
        """LabCore has just said it is too deep. Firing the remaining
        statements at it is the load the refusal was asking to be spared —
        the station module's drain gives up its turn for the same reason."""
        gw, _c, _r = self.delete_with(refuse_only("lem_machine_status"))
        assert not any("lem_qc_specs" in s for s in gw.refused + gw.accepted
                       if is_write(s))

    def test_it_leaves_no_stale_note(self):
        _gw, client, _r = self.delete_with(refuse_only("lem_machine_control"))
        assert stale_of(push(client)) == set()

    def test_a_refused_purge_is_reported_even_though_the_rest_landed(self):
        """Erasing history is the most destructive half of this endpoint.
        "Deleted" while the log is untouched is the worst possible lie about
        it — in both directions."""
        gw, client = busy_bench(refuse=refuse_only("lem_machine_log"))
        response = client.delete("/api/machines/pac-flash-2",
                                 json={"confirm": True, "purge_history": True})
        assert response.status_code >= 400, response.get_json()
        assert "history" in response.get_json().get("not_landed", [])


class TestABulkImportNeverCountsARefusalAsAnImport:
    """notes.md, verbatim: a bulk import "reported 'imported 3094' while
    nothing landed", because the loop counted refusals as successes. The
    checklist importer was fixed; the maintenance importer was written the
    same way and was not."""

    CSV = ("equipment,task,kind,completed_date,performed_by,note\n"
           "PAC Flash 2,Annual cal,calibration,2026-01-05,kaden,done\n"
           "PAC Flash 2,Filter change,pm,2026-02-05,kaden,done\n"
           "Multitek NS,Annual cal,calibration,2026-03-05,kaden,done\n")

    def test_nothing_landed_means_nothing_created(self):
        _gw, client = busy_bench(refuse=refuse_only("lem_machine_log"))
        body = client.post("/api/maintenance-import",
                           json={"csv": self.CSV}).get_json()
        assert body["created"] == 0, body

    def test_a_wholly_refused_import_is_not_a_2xx(self):
        _gw, client = busy_bench(refuse=refuse_only("lem_machine_log"))
        response = client.post("/api/maintenance-import",
                               json={"csv": self.CSV})
        assert response.status_code >= 400, response.get_json()

    def test_the_count_matches_the_rows_that_really_landed(self):
        """One op through, then the queue fills. The report must say one."""
        gw, client = busy_bench(refuse=lambda sql: False)
        first = [True]

        def refuse(sql):
            if not (is_write(sql) and "lem_machine_log" in sql):
                return False
            if first[0]:
                first[0] = False
                return False
            return True

        gw._refuse = refuse
        body = client.post("/api/maintenance-import",
                           json={"csv": self.CSV}).get_json()
        assert body["created"] == 1, body
        assert body.get("refused") == 2, body


# ── the stores every one of those endpoints writes through ──────────────────

class TestARefusedQcSpecIsNotAnOk:
    """A QC spec is the expected value and the limits a reading is judged
    against. Saved-but-not-saved means the bench goes on judging against the
    old ones — the same compliance shape as a correction factor."""

    SPEC = {"machine_uid": "pac-flash-2", "test_name": "Flash Point",
            "sample_id": "CRM-1", "expected": 63.5, "std_dev": 1.0, "k": 2.0}

    def test_a_refused_save_is_not_a_2xx(self):
        _gw, client = busy_bench()
        response = client.post("/api/qc-specs", json=self.SPEC)
        assert response.status_code == 503, response.get_json()
        assert "ok" not in response.get_json()

    def test_a_refused_delete_is_not_a_2xx(self):
        _gw, client = busy_bench()
        response = client.delete("/api/qc-specs",
                                 json={"machine_uid": "pac-flash-2",
                                       "test_name": "Flash Point"})
        assert response.status_code == 503, response.get_json()

    def test_a_bad_spec_is_still_a_400(self):
        """Regression: validation refusals keep their own status. A spec that
        never reached LabCore is the client's problem to fix, not something to
        come back and try again."""
        _gw, client = busy_bench()
        bad = dict(self.SPEC, std_dev=-1.0)
        assert client.post("/api/qc-specs", json=bad).status_code == 400


class TestARefusedQcSampleIsNotAnOk:
    SAMPLE = {"name": "CRM-9", "sample_id_val": "L-9",
              "tests": [{"name": "Flash Point", "expected": 63.5,
                         "std_dev": 1.0, "k": 2.0}]}

    def test_a_refused_save_is_not_a_2xx(self):
        _gw, client = busy_bench()
        response = client.post("/api/qc-samples", json=self.SAMPLE)
        assert response.status_code == 503, response.get_json()
        assert "ok" not in response.get_json()

    def test_a_refused_delete_is_not_a_2xx(self):
        _gw, client = busy_bench()
        response = client.delete("/api/qc-samples", json={"name": "CRM-9"})
        assert response.status_code == 503, response.get_json()


class TestARefusedMachineConfigIsNotAnOk:
    """The config is the parser's mapping — which column is which method. A
    save reported as landed that did not means the bench keeps parsing with the
    old mapping, and the editor shows the new one."""

    def test_a_refused_save_is_not_a_2xx(self):
        _gw, client = busy_bench()
        response = client.post("/api/machine-configs/pac-flash-2",
                               json={"title": "PAC Flash 2",
                                     "config": {"mappings": []}})
        assert response.status_code == 503, response.get_json()
        assert "ok" not in response.get_json()

    def test_a_refused_create_is_not_a_2xx(self):
        _gw, client = busy_bench()
        response = client.post("/api/machine-configs", json={"title": "New"})
        assert response.status_code == 503, response.get_json()

    def test_a_refused_delete_is_not_a_2xx(self):
        _gw, client = busy_bench()
        response = client.delete("/api/machine-configs/pac-flash-2",
                                 json={"confirm": True})
        assert response.status_code == 503, response.get_json()


class TestARefusedMaintenanceWriteIsNotAnOk:
    def test_a_refused_task_save_is_not_a_2xx(self):
        _gw, client = busy_bench()
        response = client.post("/api/machines/pac-flash-2/maintenance",
                               json={"name": "Annual cal", "kind": "calibration",
                                     "interval_days": 365})
        assert response.status_code == 503, response.get_json()
        assert "ok" not in response.get_json()

    def test_a_refused_completion_is_not_a_2xx(self):
        gw, client = busy_bench(refuse=lambda sql: False)
        made = client.post("/api/machines/pac-flash-2/maintenance",
                           json={"name": "Annual cal", "kind": "calibration",
                                 "interval_days": 365}).get_json()
        uid = made["task"]["uid"]
        gw._refuse = is_write
        response = client.post(f"/api/maintenance/{uid}/complete",
                               json={"when": "2026-08-26"})
        assert response.status_code == 503, response.get_json()


class TestARefusedRoundIsNotAnOk:
    """A tick is the record that the round was done. Told it was saved when it
    was not, the archive shows a gap nobody can explain and the operator has no
    reason to tick it again."""

    def checklist(self, client):
        return client.post("/api/checklists",
                           json={"name": "Opening", "slot": "open",
                                 "due_time": "08:00",
                                 "items": [{"text": "Check the bath",
                                            "entry_type": "check"},
                                           {"text": "Bath temp",
                                            "entry_type": "number",
                                            "units": "C"}]}).get_json()

    def test_a_refused_definition_save_is_not_a_2xx(self):
        _gw, client = busy_bench()
        response = client.post("/api/checklists",
                               json={"name": "Opening", "slot": "open",
                                     "items": [{"text": "x"}]})
        assert response.status_code == 503, response.get_json()
        assert "ok" not in response.get_json()

    def test_a_refused_tick_is_not_a_2xx(self):
        gw, client = busy_bench(refuse=lambda sql: False)
        saved = self.checklist(client)["checklist"]
        gw._refuse = refuse_only("lem_checklist_state")
        response = client.post(f"/api/checklists/{saved['uid']}/toggle",
                               json={"item_uid": saved["items"][0]["uid"],
                                     "checked": True})
        assert response.status_code == 503, response.get_json()
        assert "ok" not in response.get_json()

    def test_a_refused_reading_is_not_a_2xx(self):
        gw, client = busy_bench(refuse=lambda sql: False)
        saved = self.checklist(client)["checklist"]
        gw._refuse = refuse_only("lem_checklist_state")
        response = client.post(f"/api/checklists/{saved['uid']}/value",
                               json={"item_uid": saved["items"][1]["uid"],
                                     "value": "21.5"})
        assert response.status_code == 503, response.get_json()

    def test_a_reading_that_is_not_a_number_is_still_a_400(self):
        gw, client = busy_bench(refuse=lambda sql: False)
        saved = self.checklist(client)["checklist"]
        gw._refuse = is_write
        response = client.post(f"/api/checklists/{saved['uid']}/value",
                               json={"item_uid": saved["items"][1]["uid"],
                                     "value": "about half"})
        assert response.status_code == 400


class TestARefusedScheduleChangeIsNotAnOk:
    """Opening hours decide whether a silent bench reads as STOPPED or as
    CLOSED. Saved-but-not-saved is a floor full of alarms nobody asked for, or
    none when they were wanted."""

    def test_a_refused_save_is_not_a_2xx(self):
        _gw, client = busy_bench()
        response = client.post("/api/schedule",
                               json={"working_days": [0, 1, 2, 3, 4],
                                     "opens": "07:00", "closes": "18:00"})
        assert response.status_code == 503, response.get_json()
        assert "ok" not in response.get_json()

    def test_a_refused_holiday_is_not_a_2xx(self):
        _gw, client = busy_bench()
        response = client.post("/api/holidays",
                               json={"day": "2026-12-25", "name": "Christmas"})
        assert response.status_code == 503, response.get_json()

    def test_a_bad_holiday_date_is_still_a_400(self):
        _gw, client = busy_bench()
        assert client.post("/api/holidays",
                           json={"day": "the 25th"}).status_code == 400


# ── everything above must cost the success path nothing ─────────────────────

class TestTheSuccessPathIsExactlyAsItWas:
    """The floor UI and possibly other clients read these shapes. A guard that
    changed what a write that WORKED says would be a worse bug than the one it
    fixes, and a slower one to find."""

    @pytest.fixture
    def client(self):
        gw = RefusingGateway(refuse=lambda sql: False)
        seeded(gw)
        return signed_in_client(gw)

    def test_a_correction_save_answers_exactly_as_before(self, client):
        response = save_correction(client, test="Flash Point", value="-3.0")
        assert response.status_code == 200
        assert response.get_json() == {"ok": True, "test_name": "Flash Point",
                                       "correction": -3.0}

    def test_a_correction_save_still_leaves_its_note(self, client):
        save_correction(client)
        assert stale_of(push(client)) == {"corrections"}

    def test_a_correction_removal_answers_exactly_as_before(self, client):
        save_correction(client, test="Flash Point")
        response = client.delete(
            "/api/machines/pac-flash-2/corrections/Flash Point")
        assert response.status_code == 200
        assert response.get_json() == {"ok": True, "deleted": "Flash Point"}

    def test_an_override_answers_exactly_as_before(self, client):
        response = set_override(client)
        assert response.status_code == 200
        assert response.get_json() == {"ok": True}

    def test_an_override_still_leaves_its_note(self, client):
        set_override(client)
        assert stale_of(push(client)) == {"override"}

    def test_a_delete_answers_exactly_as_before(self, client):
        response = client.delete("/api/machines/pac-flash-2",
                                 json={"confirm": True})
        assert response.status_code == 200
        assert response.get_json() == {"ok": True}

    def test_a_delete_still_leaves_its_note(self, client):
        client.delete("/api/machines/pac-flash-2", json={"confirm": True})
        assert stale_of(push(client)) == {"override"}

    def test_a_clean_import_still_reports_what_it_made(self, client):
        body = client.post("/api/maintenance-import", json={
            "csv": TestABulkImportNeverCountsARefusalAsAnImport.CSV
        }).get_json()
        assert body["created"] == 3, body
        assert body.get("refused", 0) == 0


# ── the floor has to SHOW it ────────────────────────────────────────────────

class TestTheFloorDoesNotSwallowARefusal:
    """A correct backend behind a UI that discards the answer is still a
    supervisor who thinks their correction landed.

    Source-scanned rather than driven through a browser, for the same reason
    `test_checklist_editor_ui.py` scans: the failure mode is a save handler
    written without an `r.ok` branch, and that is visible in the file. Seven of
    them were, including retiring an instrument — several LabCore statements
    with no transaction across them, and the reply thrown away entirely.
    """

    PAGES = ("templates/floor.html", "templates/checklists.html",
             "templates/maintenance.html")

    # Calls whose answer there is genuinely nothing to do with. Named one by
    # one, because "it's probably fine" is how the other seven got here.
    ALLOWED = (
        "/api/logout",          # no LabCore write behind it; nothing to report
        "const post =",         # the helper's own definition; callers assign it
        "/position",            # layout is cosmetic and deliberately best-effort
        "/api/map",             # so is the map lock — see the audit in the store
    )

    def write_calls(self, path):
        import re
        lines = open(path, encoding="utf-8").read().split("\n")
        for i, line in enumerate(lines):
            if "fetch(" not in line:
                continue
            window = "\n".join(lines[i:i + 5])
            if ("method: 'POST'" not in window
                    and "method: 'DELETE'" not in window):
                continue
            if any(a in line for a in self.ALLOWED):
                continue
            assigned = bool(re.search(r"=\s*(await\s+)?fetch\(", line))
            yield f"{path}:{i + 1}: {line.strip()[:90]}", assigned

    @pytest.mark.parametrize("page", PAGES)
    def test_every_write_keeps_the_answer_it_gets_back(self, page):
        dropped = [where for where, assigned in self.write_calls(page)
                   if not assigned]
        assert dropped == [], (
            "these saves throw the server's reply away, so a refused write "
            "looks exactly like one that worked:\n  " + "\n  ".join(dropped))

    def test_the_shared_formatter_exists_for_them_to_use(self):
        source = open("static/lem.js", encoding="utf-8").read()
        assert "function failure(" in source
        assert "failure: failure" in source, "not exported on window.LEM"

    def test_the_retry_hint_reaches_the_person_looking_at_the_screen(self):
        """`retry_after` survives LabCore → server → browser → the sentence.
        Dropped at the last step it may as well never have been sent."""
        source = open("static/lem.js", encoding="utf-8").read()
        assert "retry_after" in source
        assert "Try again in" in source

    def test_a_partial_save_is_spelled_out_rather_than_summarised(self):
        source = open("static/lem.js", encoding="utf-8").read()
        assert "not_landed" in source and "NOT saved" in source

    @pytest.mark.parametrize("page", ("templates/floor.html",
                                      "templates/checklists.html"))
    def test_the_pages_use_it(self, page):
        assert "LEM.failure(" in open(page, encoding="utf-8").read()

    def test_the_override_shows_labcores_reason_not_a_canned_one(self):
        """The write the benches act on. "Could not apply the override."
        cannot tell somebody whether to retry in five seconds or go and find
        help."""
        source = open("templates/floor.html", encoding="utf-8").read()
        assert "alert('Could not apply the override.');" not in source
        assert "'Could not apply the override.'" in source  # kept as fallback


class TestAHalfFinishedChangeoverSaysSo:
    """A lot changeover creates the new standard and then moves every
    instrument checked against the old one. Stopping halfway and saying nothing
    silently stops QC on the instruments that did not move — which is the exact
    failure `changeover` exists to prevent, arrived at from the inside."""

    def lab(self):
        gw = RefusingGateway(refuse=lambda sql: False)
        seeded(gw)
        client = signed_in_client(gw)
        client.post("/api/qc-samples",
                    json={"name": "CRM-8", "sample_id_val": "L-8",
                          "tests": [{"name": "Flash Point", "expected": 63.5,
                                     "std_dev": 1.0, "k": 2.0}]})
        for uid in ("pac-flash-2", "multitek-ns"):
            client.post(f"/api/machines/{uid}/qc-targets",
                        json={"targets": [{"sample": "CRM-8",
                                           "test": "Flash Point"}]})
        return gw, client

    def test_a_refused_move_is_not_reported_as_a_changeover(self):
        gw, client = self.lab()
        gw._refuse = refuse_only("lem_machine_targets")
        response = client.post("/api/qc-samples/changeover",
                               json={"old_name": "CRM-8", "new_name": "CRM-9",
                                     "new_id_val": "L-9"})
        assert response.status_code == 503, response.get_json()
        assert "ok" not in response.get_json()

    def test_it_names_the_lot_that_was_created_anyway(self):
        """There is no undo across queue ops, so the new lot really is there.
        Hiding that leaves somebody to find two lots and no explanation."""
        gw, client = self.lab()
        gw._refuse = refuse_only("lem_machine_targets")
        body = client.post("/api/qc-samples/changeover",
                           json={"old_name": "CRM-8", "new_name": "CRM-9",
                                 "new_id_val": "L-9"}).get_json()
        assert body.get("partial") is True, body
        assert any("CRM-9" in item for item in body.get("landed", [])), body

    def test_it_says_how_far_it_got(self):
        gw, client = self.lab()
        gw._refuse = refuse_only("lem_machine_targets")
        body = client.post("/api/qc-samples/changeover",
                           json={"old_name": "CRM-8", "new_name": "CRM-9",
                                 "new_id_val": "L-9"}).get_json()
        assert body.get("moved") == 0, body
        assert "Re-run the changeover" in body.get("error", ""), body

    def test_a_clean_changeover_answers_exactly_as_before(self):
        _gw, client = self.lab()
        response = client.post("/api/qc-samples/changeover",
                               json={"old_name": "CRM-8", "new_name": "CRM-9",
                                     "new_id_val": "L-9"})
        assert response.status_code == 200
        assert response.get_json() == {"ok": True, "moved": 2}


class TestAHalfRecordedGroupOfTicksSaysSo:
    """Ticking a parent item ticks its children, one statement each. A refusal
    partway leaves a parent ticked over children that are not, which reads on
    the page as a round that was done when part of it was not recorded."""

    def lab(self):
        gw = RefusingGateway(refuse=lambda sql: False)
        seeded(gw)
        client = signed_in_client(gw)
        saved = client.post("/api/checklists", json={
            "name": "Opening", "slot": "open", "due_time": "08:00",
            "items": [{"uid": "p1", "text": "Gas bottles"},
                      {"uid": "c1", "text": "Helium", "parent_uid": "p1"},
                      {"uid": "c2", "text": "Nitrogen", "parent_uid": "p1"}],
        }).get_json()["checklist"]
        return gw, client, saved

    def test_the_group_is_not_reported_as_recorded(self):
        gw, client, saved = self.lab()
        seen = {"n": 0}

        def refuse(sql):
            if not (is_write(sql) and "lem_checklist_state" in sql):
                return False
            seen["n"] += 1
            return seen["n"] > 1          # the parent lands, the children do not

        gw._refuse = refuse
        response = client.post(f"/api/checklists/{saved['uid']}/toggle",
                               json={"item_uid": "p1", "checked": True})
        assert response.status_code == 503, response.get_json()
        assert "ok" not in response.get_json()

    def test_it_names_which_items_landed(self):
        gw, client, saved = self.lab()
        seen = {"n": 0}

        def refuse(sql):
            if not (is_write(sql) and "lem_checklist_state" in sql):
                return False
            seen["n"] += 1
            return seen["n"] > 1

        gw._refuse = refuse
        body = client.post(f"/api/checklists/{saved['uid']}/toggle",
                           json={"item_uid": "p1", "checked": True}).get_json()
        assert body.get("landed") == ["p1"], body
        assert body.get("not_landed") == ["c1", "c2"], body

    def test_a_clean_toggle_answers_exactly_as_before(self):
        _gw, client, saved = self.lab()
        response = client.post(f"/api/checklists/{saved['uid']}/toggle",
                               json={"item_uid": "p1", "checked": True})
        assert response.status_code == 200
        assert response.get_json() == {"ok": True,
                                        "touched": ["p1", "c1", "c2"]}
