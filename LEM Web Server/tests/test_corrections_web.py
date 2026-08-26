"""Correction factors in the master view: set them, log them, export them.

Ryan asked where his correction factor was; the answer was nowhere. V4 could store
and log one but never applied it to a reading, and V5 had only a dead
`correction_factor_dir` field.

Agreed shape: an additive offset, `corrected = raw + correction`, default 0.0. The
module applies it (see the module suite) and the master view is where a supervisor
sets it — right-click an instrument. Every change is audited into
`lem_machine_log`, so "who moved this bench's numbers, when, and from what" is
answerable, and the corrected/raw pair reaches the CSV export.
"""
import csv
import io
import json

import pytest

from labcore_gateway import FakeLabCoreGateway


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
    g.sql("INSERT INTO lem_machine_status VALUES "
          "('m1','PAC Flash 1','GREEN','ok','2026-08-03T18:00:00')")
    return g


@pytest.fixture
def client(gw):
    from web_app import create_app
    app = create_app(gw, authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client()


def signed_in(client):
    client.post("/api/login", json={"username": "k", "password": "good"})
    return client


def set_corr(client, value=0.5, test="Flash", uid="m1"):
    return client.post(f"/api/machines/{uid}/corrections",
                       json={"test_name": test, "correction": value,
                             "units": "C"})


# ── setting one ─────────────────────────────────────────────────────────────

class TestSetting:
    def test_it_saves_and_reads_back(self, client):
        signed_in(client)
        assert set_corr(client).status_code == 200
        body = client.get("/api/machines/m1/corrections").get_json()
        row = body["corrections"][0]
        assert row["test_name"] == "Flash"
        assert row["correction"] == pytest.approx(0.5)

    def test_a_negative_offset_is_allowed(self, client):
        signed_in(client)
        set_corr(client, -1.2)
        got = client.get("/api/machines/m1/corrections").get_json()["corrections"]
        assert got[0]["correction"] == pytest.approx(-1.2)

    def test_updating_replaces_rather_than_duplicates(self, client):
        signed_in(client)
        set_corr(client, 0.5)
        set_corr(client, 0.9)
        got = client.get("/api/machines/m1/corrections").get_json()["corrections"]
        assert len(got) == 1 and got[0]["correction"] == pytest.approx(0.9)

    def test_zero_is_stored_not_treated_as_absent(self, client):
        """Explicitly setting 0.0 is a decision, and it must stick."""
        signed_in(client)
        set_corr(client, 0.5)
        set_corr(client, 0.0)
        got = client.get("/api/machines/m1/corrections").get_json()["corrections"]
        assert got and got[0]["correction"] == pytest.approx(0.0)

    def test_it_needs_an_account(self, client):
        assert set_corr(client).status_code == 401

    def test_a_missing_test_name_is_refused(self, client):
        signed_in(client)
        r = client.post("/api/machines/m1/corrections",
                        json={"correction": 0.5})
        assert r.status_code == 400
        assert r.get_json()["error"]

    def test_a_non_numeric_offset_is_refused(self, client):
        """One "about half" and every verdict on this bench is garbage."""
        signed_in(client)
        r = client.post("/api/machines/m1/corrections",
                        json={"test_name": "Flash", "correction": "a bit"})
        assert r.status_code == 400

    def test_an_unknown_machine_is_refused(self, client):
        signed_in(client)
        assert set_corr(client, uid="nope").status_code == 404

    def test_deleting_stops_the_correction(self, client):
        signed_in(client)
        set_corr(client)
        assert client.delete(
            "/api/machines/m1/corrections/Flash").status_code == 200
        assert client.get(
            "/api/machines/m1/corrections").get_json()["corrections"] == []

    def test_deleting_something_absent_is_a_404_not_a_silent_ok(self, client):
        signed_in(client)
        assert client.delete(
            "/api/machines/m1/corrections/Nope").status_code == 404


# ── the audit trail ─────────────────────────────────────────────────────────

class TestLogged:
    def config_events(self, gw, action=""):
        # `ORDER BY ts, rowid`, and the rowid is load-bearing. `_audit` stamps
        # `isoformat(timespec="seconds")`, so two changes made in the same
        # second carry the SAME ts and `ORDER BY ts` alone leaves their order to
        # the query planner. That was invisible while lem_machine_log had no
        # index and every read was a table scan in insertion order; the moment
        # idx_lem_log_ts existed, the tie broke the other way and these tests
        # started reading the first of two events as the last. The tests mean
        # "the order they were written in", so they now ask for it.
        res = gw.read_sql("SELECT machine_uid, test_name, detail FROM "
                          "lem_machine_log WHERE kind = 'config' "
                          "ORDER BY ts, rowid")
        out = []
        for r in res.get("rows") or []:
            detail = json.loads(r.get("detail") or "{}")
            if action and action not in str(detail.get("action", "")):
                continue
            out.append(detail)
        return out

    def test_setting_one_is_logged(self, gw, client):
        signed_in(client)
        set_corr(client, 0.5)
        events = self.config_events(gw, "correction")
        assert events, "a change to how every reading is judged went unrecorded"

    def test_the_log_says_who_and_what_it_changed_from(self, gw, client):
        signed_in(client)
        set_corr(client, 0.5)
        set_corr(client, 0.9)
        last = self.config_events(gw, "correction")[-1]
        assert last["by"] == "kaden"
        assert last["previous"] == pytest.approx(0.5)
        assert last["new"] == pytest.approx(0.9)
        assert last["test"] == "Flash"

    def test_the_first_one_records_a_previous_of_zero(self, gw, client):
        signed_in(client)
        set_corr(client, 0.5)
        assert self.config_events(gw, "correction")[0]["previous"] == \
            pytest.approx(0.0)

    def test_deleting_is_logged_too(self, gw, client):
        signed_in(client)
        set_corr(client, 0.5)
        client.delete("/api/machines/m1/corrections/Flash")
        last = self.config_events(gw, "correction")[-1]
        assert "removed" in str(last["action"]).lower()
        assert last["previous"] == pytest.approx(0.5)

    def test_it_shows_up_on_the_logs_page(self, gw, client):
        signed_in(client)
        set_corr(client, 0.5)
        events = client.get("/api/logs").get_json()["events"]
        assert any("correction" in json.dumps(e).lower() for e in events)


# ── the export ──────────────────────────────────────────────────────────────

class TestExport:
    def qc_event(self, gw, raw=65.0, correction=0.5, corrected=65.5):
        detail = {"in_spec": True, "expected": 63.72, "low": 61.62,
                  "high": 65.82, "raw_value": raw, "correction": correction}
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
               "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
               "detail TEXT)")
        gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
               ["m1", "2026-08-03T16:24:51", "qc", "AO25", "Flash",
                str(corrected), json.dumps(detail)])

    def rows(self, resp):
        return list(csv.reader(io.StringIO(resp.get_data(as_text=True))))

    def test_the_qc_export_has_correction_and_raw_columns(self, gw, client):
        self.qc_event(gw)
        head, *body = self.rows(client.get("/api/export/qc.csv"))
        assert "correction" in head and "raw_value" in head
        row = dict(zip(head, body[0]))
        assert float(row["raw_value"]) == pytest.approx(65.0)
        assert float(row["correction"]) == pytest.approx(0.5)
        assert float(row["value"]) == pytest.approx(65.5)

    def test_an_uncorrected_reading_leaves_the_columns_blank_not_wrong(
            self, gw, client):
        """Blank means "no correction applied". A 0 would be a claim."""
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
               "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
               "detail TEXT)")
        gw.sql("INSERT INTO lem_machine_log VALUES "
               "('m1','2026-08-03T16:00:00','qc','AO25','Flash','65.0',"
               "'{\"in_spec\": true, \"expected\": 63.72}')")
        head, *body = self.rows(client.get("/api/export/qc.csv"))
        row = dict(zip(head, body[0]))
        assert row["correction"] == "" and row["raw_value"] == ""

    def test_the_per_machine_export_has_them_too(self, gw, client):
        self.qc_event(gw)
        head, *body = self.rows(client.get("/api/machines/m1/export.csv"))
        assert "correction" in head and "raw_value" in head
        row = dict(zip(head, body[0]))
        assert float(row["correction"]) == pytest.approx(0.5)


# ── it reaches the floor UI ─────────────────────────────────────────────────

class TestTheFloorOffersIt:
    def src(self):
        import pathlib
        return (pathlib.Path(__file__).resolve().parent.parent
                / "templates" / "floor.html").read_text(encoding="utf-8")

    def test_the_right_click_menu_has_an_entry(self):
        assert 'data-act="corr"' in self.src()

    def test_there_is_a_dialog_to_edit_them(self):
        src = self.src()
        assert "corrDlg" in src

    def test_the_panel_shows_an_applied_correction(self):
        assert "s.correction" in self.src()


# ── reachable as soon as QC is assigned ─────────────────────────────────────

class TestReachableRightAfterAssigning:
    """Ryan, 2026-08-03: "I cant apply a correction factor until I manually assign
    a QC".

    Two halves to that. QC is now assignment-only (see the module suite), so the
    assignment is the right gate — but the dialog listed only `effective_specs`,
    which the module publishes on its NEXT poll. So there was a window where a test
    was assigned and the dialog still said there was nothing to correct.

    It now lists assigned targets too, so a correction can be set the moment the
    assignment is made.
    """

    def src(self):
        import pathlib
        return (pathlib.Path(__file__).resolve().parent.parent
                / "templates" / "floor.html").read_text(encoding="utf-8")

    def test_the_dialog_reads_assigned_targets_as_well(self):
        s = self.src()
        block = s[s.index("async function renderCorrections"):]
        block = block[:block.index("\n}")]
        assert "qc_targets" in block, \
            "only published specs were listed, so a fresh assignment showed none"

    def test_it_still_prefers_the_published_spec(self):
        """When both exist, the published spec carries the band to show."""
        s = self.src()
        block = s[s.index("async function renderCorrections"):]
        assert "effective_specs" in block[:block.index("\n}")]

    def test_a_correction_can_be_saved_for_an_assigned_but_unpublished_test(
            self, gw, client):
        """The backend never required a published spec — this pins that."""
        signed_in(client)
        r = client.post("/api/machines/m1/corrections",
                        json={"test_name": "ASTM D5453 - Sulfur",
                              "correction": -0.4})
        assert r.status_code == 200
        got = client.get("/api/machines/m1/corrections").get_json()["corrections"]
        assert got[0]["correction"] == pytest.approx(-0.4)


class TestNegativesAndPastedText:
    """PAC Flash 2's real correction is −3.0, so negatives are not an edge case.

    Plain `-3.0` always worked. What did not: a **Unicode minus** (U+2212), which
    is what you get pasting from a document, a spreadsheet or a chat message — it
    looks identical and `float()` refuses it. Same for a non-breaking space.
    """

    def post(self, client, value):
        return client.post("/api/machines/m1/corrections",
                           json={"test_name": "Flash", "correction": value})

    def saved(self, client):
        got = client.get("/api/machines/m1/corrections").get_json()["corrections"]
        return got[0]["correction"] if got else None

    @pytest.mark.parametrize("typed", ["-3.0", "-3", " -3.0 ", "-3.00"])
    def test_a_plain_negative_works(self, client, typed):
        signed_in(client)
        assert self.post(client, typed).status_code == 200
        assert self.saved(client) == pytest.approx(-3.0)

    @pytest.mark.parametrize("typed", ["−3.0", "–3.0", "—3.0"])
    def test_a_pasted_minus_sign_works(self, client, typed):
        """U+2212 minus, en dash, em dash — all read as a minus by a human."""
        signed_in(client)
        assert self.post(client, typed).status_code == 200, typed
        assert self.saved(client) == pytest.approx(-3.0)

    def test_a_non_breaking_space_does_not_break_it(self, client):
        signed_in(client)
        assert self.post(client, " -3.0 ").status_code == 200
        assert self.saved(client) == pytest.approx(-3.0)

    def test_the_flash_2_and_multitek_ns_values_round_trip(self, client):
        """The two Ryan asked about, from V4: Flash −3.0 and Sulfur +1.45."""
        signed_in(client)
        self.post(client, "-3.0")
        assert self.saved(client) == pytest.approx(-3.0)
        client.post("/api/machines/m1/corrections",
                    json={"test_name": "Sulfur", "correction": "1.45"})
        got = {c["test_name"]: c["correction"] for c in
               client.get("/api/machines/m1/corrections").get_json()["corrections"]}
        assert got == {"Flash": pytest.approx(-3.0), "Sulfur": pytest.approx(1.45)}

    def test_real_junk_is_still_refused(self, client):
        """Tolerating look-alike minus signs must not turn into guessing."""
        signed_in(client)
        for bad in ("about half", "-", "--3", "3-", ""):
            assert self.post(client, bad).status_code == 400, bad
