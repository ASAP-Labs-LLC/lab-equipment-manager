"""Importing years of PM/calibration history from a spreadsheet.

The lab has completed maintenance recorded in sheets that predate uids, so rows
are keyed by **equipment name**. Ryan's rules:

  1. **Exact name match only.** The lab already has `opimpp 1`, `Optimpp 1` and
     `OtpiMPP 2` as separate registrations, so fuzzy matching would file history
     against the wrong instrument. Unmatched rows are reported, never guessed.
  2. A **template** pre-filled with every active machine, so a row can't fail on
     a typo. Import fails **gracefully**: bad rows are reported, good rows land.
  3. **Only import changes** — an entry already present is skipped, so running
     the same file twice is a no-op.
  4. An imported completion **moves the schedule**, mathematically, from the
     completion date.
"""
import csv
import io
import json

import pytest

from labcore_gateway import FakeLabCoreGateway
from maintenance_import import (parse_import_csv, plan_import,
                                template_csv_rows)
from maintenance_store import MaintenanceStore, MaintTaskRecord

HEADER = "equipment,task,kind,completed_date,performed_by,note"


def csv_text(*rows):
    return HEADER + "\n" + "\n".join(rows) + "\n"


# ── parsing ─────────────────────────────────────────────────────────────────

class TestParsing:
    def test_a_good_row_parses(self):
        rows, errors = parse_import_csv(csv_text(
            "OptiMPP 1,Monthly PM,pm,2026-05-02,sam,cleaned cell"))
        assert not errors
        assert rows[0]["equipment"] == "OptiMPP 1"
        assert rows[0]["task"] == "Monthly PM"
        assert rows[0]["kind"] == "pm"
        assert rows[0]["completed_date"] == "2026-05-02"
        assert rows[0]["performed_by"] == "sam"
        assert rows[0]["note"] == "cleaned cell"

    def test_headers_are_case_and_space_insensitive(self):
        text = ("Equipment, Task , KIND,Completed Date,Performed By,Note\n"
                "OptiMPP 1,Monthly PM,pm,2026-05-02,sam,x\n")
        rows, errors = parse_import_csv(text)
        assert not errors and rows[0]["task"] == "Monthly PM"

    def test_calibration_is_recognised(self):
        rows, _ = parse_import_csv(csv_text(
            "OptiMPP 1,Annual cal,Calibration,2026-01-15,kaden,cert"))
        assert rows[0]["kind"] == "calibration"

    def test_an_unknown_kind_is_an_error_on_that_row_only(self):
        rows, errors = parse_import_csv(csv_text(
            "OptiMPP 1,Monthly PM,weekly,2026-05-02,sam,x",
            "OptiMPP 1,Annual cal,pm,2026-01-15,kaden,y"))
        assert len(rows) == 1                      # the good row survives
        assert rows[0]["task"] == "Annual cal"
        # The bad row is the FIRST data line, and its line number says so.
        assert len(errors) == 1 and errors[0]["line"] == 1

    def test_a_bad_date_is_an_error_on_that_row_only(self):
        rows, errors = parse_import_csv(csv_text(
            "OptiMPP 1,Monthly PM,pm,02/05/2026,sam,x",
            "OptiMPP 1,Annual cal,calibration,2026-01-15,kaden,y"))
        assert len(rows) == 1 and len(errors) == 1
        assert "date" in errors[0]["error"].lower()

    def test_a_missing_equipment_is_an_error(self):
        rows, errors = parse_import_csv(csv_text(",Monthly PM,pm,2026-05-02,,"))
        assert not rows and len(errors) == 1

    def test_a_missing_task_is_an_error(self):
        rows, errors = parse_import_csv(csv_text("OptiMPP 1,,pm,2026-05-02,,"))
        assert not rows and len(errors) == 1

    def test_blank_lines_are_skipped_silently(self):
        rows, errors = parse_import_csv(
            HEADER + "\n\nOptiMPP 1,Monthly PM,pm,2026-05-02,sam,x\n\n")
        assert len(rows) == 1 and not errors

    def test_a_file_with_no_header_is_rejected_clearly(self):
        rows, errors = parse_import_csv("OptiMPP 1,Monthly PM,pm,2026-05-02\n")
        assert not rows and errors
        assert "column" in errors[0]["error"].lower()

    def test_an_empty_file_is_rejected_clearly(self):
        rows, errors = parse_import_csv("")
        assert not rows and errors

    def test_the_template_round_trips_through_the_parser(self):
        """Whatever we hand out must be readable by what reads it back."""
        header, rows = template_csv_rows([
            {"machine_uid": "m1", "title": "OptiMPP 1"}])
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows(rows)
        parsed, errors = parse_import_csv(buf.getvalue())
        # The template's rows are blank examples, so they're skipped, not errors.
        assert not [e for e in errors if "column" in e["error"].lower()]


# ── the template ────────────────────────────────────────────────────────────

class TestTemplate:
    def test_it_lists_every_active_machine(self):
        _h, rows = template_csv_rows([
            {"machine_uid": "m1", "title": "OptiMPP 1"},
            {"machine_uid": "m2", "title": "Multitek NS"}])
        names = [r[0] for r in rows]
        assert "OptiMPP 1" in names and "Multitek NS" in names

    def test_the_header_matches_what_the_parser_wants(self):
        header, _rows = template_csv_rows([])
        assert [h.lower().replace(" ", "_") for h in header][:6] == [
            "equipment", "task", "kind", "completed_date", "performed_by",
            "note"]

    def test_it_carries_the_uid_so_a_name_is_never_ambiguous(self):
        """Three machines are called some variant of "Optimpp 1"; the uid is
        the only way to be sure which row is which."""
        header, rows = template_csv_rows([
            {"machine_uid": "m1", "title": "OptiMPP 1"}])
        assert any("uid" in h.lower() for h in header)
        assert "m1" in rows[0]

    def test_with_no_machines_it_is_still_a_usable_template(self):
        header, rows = template_csv_rows([])
        assert header and rows == []


# ── planning: match, dedupe, schedule ───────────────────────────────────────

def machines():
    return [{"machine_uid": "m1", "title": "OptiMPP 1"},
            {"machine_uid": "m2", "title": "Multitek NS"}]


class TestMatching:
    def test_an_exact_name_matches(self):
        rows, _ = parse_import_csv(csv_text(
            "OptiMPP 1,Monthly PM,pm,2026-05-02,sam,x"))
        plan = plan_import(rows, machines(), existing=set(), tasks={})
        assert plan["create"][0]["machine_uid"] == "m1"

    def test_a_near_miss_is_never_guessed(self):
        """`optimpp 1` is a DIFFERENT registration in this lab."""
        rows, _ = parse_import_csv(csv_text(
            "optimpp 1,Monthly PM,pm,2026-05-02,sam,x"))
        plan = plan_import(rows, machines(), existing=set(), tasks={})
        assert plan["create"] == []
        assert plan["unmatched"][0]["equipment"] == "optimpp 1"

    def test_surrounding_whitespace_is_forgiven(self):
        """A trailing space from a spreadsheet is a typo in the file, not a
        different instrument."""
        rows, _ = parse_import_csv(csv_text(
            "  OptiMPP 1  ,Monthly PM,pm,2026-05-02,sam,x"))
        plan = plan_import(rows, machines(), existing=set(), tasks={})
        assert plan["create"][0]["machine_uid"] == "m1"

    def test_unmatched_rows_do_not_stop_the_matched_ones(self):
        rows, _ = parse_import_csv(csv_text(
            "Ghost Machine,Monthly PM,pm,2026-05-02,sam,x",
            "OptiMPP 1,Monthly PM,pm,2026-05-03,sam,y"))
        plan = plan_import(rows, machines(), existing=set(), tasks={})
        assert len(plan["create"]) == 1 and len(plan["unmatched"]) == 1


class TestDedupe:
    def test_an_entry_already_present_is_skipped(self):
        rows, _ = parse_import_csv(csv_text(
            "OptiMPP 1,Monthly PM,pm,2026-05-02,sam,x"))
        existing = {("m1", "Monthly PM", "2026-05-02")}
        plan = plan_import(rows, machines(), existing=existing, tasks={})
        assert plan["create"] == [] and len(plan["skipped"]) == 1

    def test_the_same_file_twice_is_a_no_op(self):
        rows, _ = parse_import_csv(csv_text(
            "OptiMPP 1,Monthly PM,pm,2026-05-02,sam,x"))
        first = plan_import(rows, machines(), existing=set(), tasks={})
        keys = {(c["machine_uid"], c["task"], c["completed"])
                for c in first["create"]}
        second = plan_import(rows, machines(), existing=keys, tasks={})
        assert second["create"] == []

    def test_a_different_date_for_the_same_task_is_a_new_entry(self):
        rows, _ = parse_import_csv(csv_text(
            "OptiMPP 1,Monthly PM,pm,2026-06-02,sam,x"))
        existing = {("m1", "Monthly PM", "2026-05-02")}
        plan = plan_import(rows, machines(), existing=existing, tasks={})
        assert len(plan["create"]) == 1

    def test_duplicates_inside_one_file_are_collapsed(self):
        rows, _ = parse_import_csv(csv_text(
            "OptiMPP 1,Monthly PM,pm,2026-05-02,sam,x",
            "OptiMPP 1,Monthly PM,pm,2026-05-02,sam,x"))
        plan = plan_import(rows, machines(), existing=set(), tasks={})
        assert len(plan["create"]) == 1


class TestScheduleMoves:
    def tasks(self, last_done="2026-01-01"):
        return {"m1": [MaintTaskRecord(uid="t1", machine_uid="m1",
                                       name="Monthly PM", kind="pm",
                                       interval_days=30, last_done=last_done)]}

    def test_a_newer_completion_moves_the_schedule(self):
        rows, _ = parse_import_csv(csv_text(
            "OptiMPP 1,Monthly PM,pm,2026-05-02,sam,x"))
        plan = plan_import(rows, machines(), existing=set(),
                           tasks=self.tasks())
        assert plan["reschedule"] == [{"uid": "t1", "machine_uid": "m1",
                                       "task": "Monthly PM",
                                       "last_done": "2026-05-02"}]

    def test_the_latest_date_in_the_file_wins(self):
        rows, _ = parse_import_csv(csv_text(
            "OptiMPP 1,Monthly PM,pm,2026-05-02,sam,x",
            "OptiMPP 1,Monthly PM,pm,2026-07-02,sam,y",
            "OptiMPP 1,Monthly PM,pm,2026-06-02,sam,z"))
        plan = plan_import(rows, machines(), existing=set(),
                           tasks=self.tasks())
        assert plan["reschedule"][0]["last_done"] == "2026-07-02"

    def test_an_older_completion_does_not_drag_the_schedule_back(self):
        """Importing 2023 history must not make a current machine look overdue."""
        rows, _ = parse_import_csv(csv_text(
            "OptiMPP 1,Monthly PM,pm,2023-05-02,sam,x"))
        plan = plan_import(rows, machines(), existing=set(),
                           tasks=self.tasks(last_done="2026-07-01"))
        assert plan["reschedule"] == []

    def test_history_lands_even_with_no_matching_task(self):
        """A task that no longer exists still has real history worth keeping —
        it just can't be rescheduled."""
        rows, _ = parse_import_csv(csv_text(
            "OptiMPP 1,Retired PM,pm,2026-05-02,sam,x"))
        plan = plan_import(rows, machines(), existing=set(),
                           tasks=self.tasks())
        assert len(plan["create"]) == 1 and plan["reschedule"] == []


# ── over HTTP ───────────────────────────────────────────────────────────────

class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


@pytest.fixture
def gw():
    g = FakeLabCoreGateway()
    g.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
          "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
          "reason TEXT, updated_at TEXT)")
    for uid, title in (("m1", "OptiMPP 1"), ("m2", "Multitek NS")):
        g.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
              [uid, title, "GREEN", "ok", "2026-08-03T09:00:00"])
    return g


@pytest.fixture
def client(gw):
    from web_app import create_app
    app = create_app(gw, authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def signed_in(client):
    client.post("/api/login", json={"username": "k", "password": "good"})
    return client


class TestEndpoints:
    def test_the_template_downloads_with_the_machines_in_it(self, signed_in):
        r = signed_in.get("/api/maintenance-import/template.csv")
        assert r.status_code == 200
        assert "text/csv" in r.headers["Content-Type"]
        text = r.get_data(as_text=True)
        assert "OptiMPP 1" in text and "Multitek NS" in text

    def test_a_dry_run_changes_nothing(self, gw, signed_in):
        body = signed_in.post("/api/maintenance-import?dry_run=1", json={
            "csv": csv_text("OptiMPP 1,Monthly PM,pm,2026-05-02,sam,x")}
        ).get_json()
        assert body["create_count"] == 1
        assert signed_in.get(
            "/api/machines/m1/maintenance-history").get_json()["history"] == []

    def test_importing_records_the_history(self, signed_in):
        signed_in.post("/api/maintenance-import", json={
            "csv": csv_text("OptiMPP 1,Monthly PM,pm,2026-05-02,sam,cleaned")})
        hist = signed_in.get(
            "/api/machines/m1/maintenance-history").get_json()["history"]
        assert len(hist) == 1
        assert hist[0]["task"] == "Monthly PM"
        assert hist[0]["by"] == "sam"
        assert hist[0]["note"] == "cleaned"
        assert hist[0]["completed"] == "2026-05-02"

    def test_re_importing_the_same_file_adds_nothing(self, signed_in):
        payload = {"csv": csv_text(
            "OptiMPP 1,Monthly PM,pm,2026-05-02,sam,cleaned")}
        signed_in.post("/api/maintenance-import", json=payload)
        second = signed_in.post("/api/maintenance-import",
                                json=payload).get_json()
        assert second["created"] == 0 and second["skipped"] == 1
        hist = signed_in.get(
            "/api/machines/m1/maintenance-history").get_json()["history"]
        assert len(hist) == 1

    def test_it_moves_the_schedule(self, gw, signed_in):
        MaintenanceStore(gw).save(MaintTaskRecord(
            uid="t1", machine_uid="m1", name="Monthly PM", kind="pm",
            interval_days=30, last_done="2026-01-01"))
        signed_in.post("/api/maintenance-import", json={
            "csv": csv_text("OptiMPP 1,Monthly PM,pm,2026-05-02,sam,x")})
        assert MaintenanceStore(gw).get("t1").last_done == "2026-05-02"

    def test_unmatched_rows_come_back_named(self, signed_in):
        body = signed_in.post("/api/maintenance-import", json={
            "csv": csv_text("Ghost,Monthly PM,pm,2026-05-02,sam,x")}).get_json()
        assert body["unmatched"] and body["unmatched"][0]["equipment"] == "Ghost"

    def test_bad_rows_are_reported_and_good_ones_still_land(self, signed_in):
        body = signed_in.post("/api/maintenance-import", json={"csv": csv_text(
            "OptiMPP 1,Monthly PM,nonsense,2026-05-02,sam,x",
            "OptiMPP 1,Annual cal,calibration,2026-01-15,kaden,y")}).get_json()
        assert body["created"] == 1
        assert len(body["errors"]) == 1

    def test_importing_needs_an_account(self, client):
        r = client.post("/api/maintenance-import", json={"csv": csv_text(
            "OptiMPP 1,Monthly PM,pm,2026-05-02,sam,x")})
        assert r.status_code == 401

    def test_a_missing_csv_is_a_clear_400(self, signed_in):
        r = signed_in.post("/api/maintenance-import", json={})
        assert r.status_code == 400 and r.get_json()["error"]

    def test_the_import_is_audited(self, signed_in):
        signed_in.post("/api/maintenance-import", json={
            "csv": csv_text("OptiMPP 1,Monthly PM,pm,2026-05-02,sam,x")})
        entries = signed_in.get("/api/logs?kind=config").get_json()["events"]
        assert any("import" in e["action"] for e in entries)

    def test_the_page_offers_it(self, client):
        body = client.get("/maintenance").get_data(as_text=True)
        assert "maintenance-import" in body
        assert 'id="impFile"' in body
