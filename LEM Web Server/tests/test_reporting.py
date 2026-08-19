"""QC trends and exports.

A QC system that can only say "green right now" is half a system. The lab
needs to see a control chart — where the last N results sat inside the band —
and to hand an auditor a file. Both read the machine log the modules already
write, so nothing new has to be recorded.
"""
import csv
import io
import json

import pytest

from labcore_gateway import FakeLabCoreGateway


@pytest.fixture
def gw():
    return FakeLabCoreGateway()


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


@pytest.fixture
def client(gw):
    from web_app import create_app
    app = create_app(gw, authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client()


def seed_log(gw):
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
           "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
           "detail TEXT)")
    rows = [
        ("m1", "2026-07-29T09:00:00", "qc", "CP", "Cloud Point", "-7.2",
         {"in_spec": True, "expected": -7.4, "low": -10.2, "high": -4.6}),
        ("m1", "2026-07-30T09:00:00", "qc", "CP", "Cloud Point", "-8.9",
         {"in_spec": True, "expected": -7.4, "low": -10.2, "high": -4.6}),
        ("m1", "2026-07-31T09:00:00", "qc", "CP", "Cloud Point", "-11.4",
         {"in_spec": False, "expected": -7.4, "low": -10.2, "high": -4.6}),
        ("m1", "2026-07-31T09:05:00", "qc", "PP", "Pour Point", "-19.0",
         {"in_spec": True, "expected": -18.3, "low": -24.7, "high": -11.9}),
        ("m1", "2026-07-31T10:00:00", "run", "37169", "", "",
         {"values": {"Sulfur": "5.3"}}),
        ("m2", "2026-07-31T09:00:00", "qc", "CP", "Cloud Point", "-7.0",
         {"in_spec": True, "expected": -7.4, "low": -10.2, "high": -4.6}),
    ]
    for uid, ts, kind, lab, test, val, detail in rows:
        gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
               [uid, ts, kind, lab, test, val, json.dumps(detail)])


# ── QC trend: the control chart behind a pill ───────────────────────────────

class TestQcTrend:
    def test_series_per_test_newest_last(self, gw, client):
        seed_log(gw)
        body = client.get("/api/machines/m1/qc-trend").get_json()
        series = {s["test_name"]: s for s in body["series"]}
        assert set(series) == {"Cloud Point", "Pour Point"}
        cloud = series["Cloud Point"]
        assert [p["value"] for p in cloud["points"]] == [-7.2, -8.9, -11.4]
        assert [p["in_spec"] for p in cloud["points"]] == [True, True, False]

    def test_band_comes_along_for_plotting(self, gw, client):
        seed_log(gw)
        cloud = next(s for s in client.get("/api/machines/m1/qc-trend")
                     .get_json()["series"] if s["test_name"] == "Cloud Point")
        assert cloud["low"] == -10.2 and cloud["high"] == -4.6
        assert cloud["expected"] == -7.4

    def test_failure_count_is_summarised(self, gw, client):
        seed_log(gw)
        cloud = next(s for s in client.get("/api/machines/m1/qc-trend")
                     .get_json()["series"] if s["test_name"] == "Cloud Point")
        assert cloud["failures"] == 1
        assert cloud["runs"] == 3

    def test_other_machines_are_not_mixed_in(self, gw, client):
        seed_log(gw)
        body = client.get("/api/machines/m2/qc-trend").get_json()
        assert len(body["series"]) == 1
        assert body["series"][0]["points"][0]["value"] == -7.0

    def test_runs_are_not_treated_as_qc(self, gw, client):
        seed_log(gw)
        body = client.get("/api/machines/m1/qc-trend").get_json()
        assert all(s["test_name"] for s in body["series"])

    def test_no_history_is_an_empty_series_not_an_error(self, gw, client):
        r = client.get("/api/machines/nobody/qc-trend")
        assert r.status_code == 200 and r.get_json()["series"] == []


# ── Export: hand an auditor a file ─────────────────────────────────────────

class TestExport:
    def test_machine_history_csv(self, gw, client):
        seed_log(gw)
        r = client.get("/api/machines/m1/export.csv")
        assert r.status_code == 200
        assert "text/csv" in r.headers["Content-Type"]
        assert "attachment" in r.headers["Content-Disposition"]
        rows = list(csv.reader(io.StringIO(r.get_data(as_text=True))))
        # raw_value/correction added 2026-08-03: a correction factor changes
        # pass/fail, so the export has to carry both what was measured and what
        # was added to it.
        assert rows[0] == ["timestamp", "kind", "lab_id", "test",
                           "value", "in_spec", "raw_value", "correction",
                           "detail"]
        assert len(rows) == 6                      # header + 5 events for m1

    def test_qc_rows_carry_the_verdict(self, gw, client):
        seed_log(gw)
        rows = list(csv.DictReader(io.StringIO(
            client.get("/api/machines/m1/export.csv").get_data(as_text=True))))
        fail = [r for r in rows if r["value"] == "-11.4"][0]
        assert fail["in_spec"] == "FAIL"
        ok = [r for r in rows if r["value"] == "-7.2"][0]
        assert ok["in_spec"] == "PASS"

    def test_export_can_be_limited_to_qc(self, gw, client):
        seed_log(gw)
        rows = list(csv.DictReader(io.StringIO(
            client.get("/api/machines/m1/export.csv?kind=qc")
            .get_data(as_text=True))))
        assert {r["kind"] for r in rows} == {"qc"}

    def test_filename_names_the_machine(self, gw, client):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
               "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
               "reason TEXT, updated_at TEXT)")
        gw.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
               ["m1", "OptiMPP 1", "GREEN", "ok", "2026-07-31T12:00:00"])
        seed_log(gw)
        r = client.get("/api/machines/m1/export.csv")
        assert "OptiMPP" in r.headers["Content-Disposition"]

    def test_empty_export_still_has_a_header(self, gw, client):
        r = client.get("/api/machines/nobody/export.csv")
        rows = list(csv.reader(io.StringIO(r.get_data(as_text=True))))
        assert rows[0][0] == "timestamp" and len(rows) == 1

    def test_fleet_qc_export_covers_every_machine(self, gw, client):
        seed_log(gw)
        rows = list(csv.DictReader(io.StringIO(
            client.get("/api/export/qc.csv").get_data(as_text=True))))
        assert {r["machine_uid"] for r in rows} == {"m1", "m2"}
        assert all(r["kind"] == "qc" for r in rows)
