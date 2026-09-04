"""The floor has to show what the module is actually checking.

Reported 2026-08-03: clicking a machine shows an empty "QC checks" panel. Against
live LabCore, `lem_qc_specs` held **0 rows** and `lem_machine_targets` 2 — while
PAC Flash 1 and 2 were both checking Flash Point against 63.72 ± 2·1.05. The
module resolves those specs at runtime from the shared standards and, until now,
published them nowhere. So the panel said "No QC assigned" about an instrument
being actively judged, and had no limits to draw a band from.

The module now writes `lem_machine_specs` — the effective spec, with its low/high
band and last reading. The floor reads it in the same one-op batched read as
everything else and prefers it, because it is the only one of the three that
reflects what is really being applied.
"""
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
          "('5fd04c0031f9','PAC Flash 1','GREEN','System nominal',"
          "'2026-08-03T18:25:57')")
    return g


@pytest.fixture
def client(gw):
    from web_app import create_app
    app = create_app(gw, authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client()


def publish(gw, **over):
    row = dict(machine_uid="5fd04c0031f9",
               test_name="ASTM D7236/D7094 - Flash Point Closed cup (small scale)",
               sample_id="AO25", expected=63.72, std_dev=1.05, k=2.0, units="C",
               low=61.62, high=65.82, last_qc_at="2026-08-03T16:24:51",
               last_qc_value=65.0, last_qc_in_spec=1, correction=0.0,
               updated_at="2026-08-03T18:25:00")
    row.update(over)
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_specs ("
           "machine_uid TEXT NOT NULL, test_name TEXT NOT NULL, sample_id TEXT, "
           "expected REAL, std_dev REAL, k REAL, units TEXT, low REAL, high REAL, "
           "last_qc_at TEXT, last_qc_value REAL, last_qc_in_spec INTEGER, "
           "correction REAL DEFAULT 0.0, updated_at TEXT, "
           "PRIMARY KEY (machine_uid, test_name))")
    gw.sql("INSERT OR REPLACE INTO lem_machine_specs VALUES "
           "(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", list(row.values()))


def flash(client):
    body = client.get("/api/machines?fresh=1").get_json()
    return [m for m in body["machines"] if m["machine_uid"] == "5fd04c0031f9"][0]


class TestEffectiveSpecsReachTheFloor:
    def test_they_are_in_the_payload(self, gw, client):
        publish(gw)
        m = flash(client)
        assert m["effective_specs"], "the floor still has nothing to draw"

    def test_the_band_is_there_so_min_and_max_can_be_shown(self, gw, client):
        publish(gw)
        spec = flash(client)["effective_specs"][0]
        assert spec["low"] == pytest.approx(61.62)
        assert spec["high"] == pytest.approx(65.82)
        assert spec["expected"] == pytest.approx(63.72)
        assert spec["units"] == "C"

    def test_the_last_reading_rides_along(self, gw, client):
        publish(gw)
        spec = flash(client)["effective_specs"][0]
        assert spec["last_qc_value"] == pytest.approx(65.0)
        assert spec["last_qc_in_spec"] is True
        assert spec["last_qc_at"].startswith("2026-08-03T16:24")

    def test_an_out_of_spec_reading_says_so(self, gw, client):
        publish(gw, last_qc_value=67.0, last_qc_in_spec=0)
        spec = flash(client)["effective_specs"][0]
        assert spec["last_qc_in_spec"] is False

    def test_a_machine_with_none_reports_an_empty_list_not_missing(self, gw, client):
        m = flash(client)
        assert m["effective_specs"] == []

    def test_the_test_name_survives_intact(self, gw, client):
        """These names are long and the panel keys off them."""
        publish(gw)
        assert flash(client)["effective_specs"][0]["test_name"] == \
            "ASTM D7236/D7094 - Flash Point Closed cup (small scale)"

    def test_it_costs_no_extra_labcore_op(self, gw, client):
        """It rides the existing one-op batched read, not a query of its own."""
        publish(gw)
        client.get("/api/machines?fresh=1")  # warm: schema is declared once
        reads = []
        real = gw.read_sql
        gw.read_sql = lambda s, a=None, **k: (reads.append(s), real(s, a, **k))[1]
        client.get("/api/machines?fresh=1")
        assert len(reads) == 1, [r[:40] for r in reads]

    def test_two_machines_do_not_mix(self, gw, client):
        gw.sql("INSERT INTO lem_machine_status VALUES "
               "('7e8304c31983','PAC Flash 2','RED','QC out of spec','x')")
        publish(gw)
        publish(gw, machine_uid="7e8304c31983", last_qc_value=67.0,
                last_qc_in_spec=0)
        body = client.get("/api/machines?fresh=1").get_json()["machines"]
        by_uid = {m["machine_uid"]: m for m in body}
        assert by_uid["5fd04c0031f9"]["effective_specs"][0]["last_qc_in_spec"] is True
        assert by_uid["7e8304c31983"]["effective_specs"][0]["last_qc_in_spec"] is False


class TestTheFloorPrefersThem:
    def test_the_panel_renders_them(self, client, gw):
        """The template has to actually read the new field, or the payload change
        is invisible."""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "templates" / "floor.html").read_text(encoding="utf-8")
        assert "effective_specs" in src

    def test_the_panel_shows_min_and_max_labels(self):
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "templates" / "floor.html").read_text(encoding="utf-8")
        assert "s.low" in src and "s.high" in src


# ── a verdict does not survive a change of standard ──────────────────────────
#
# Found live on 3 Sep 2026, on Multitek S. The sulfur standard moved AO25 ->
# AF26 on 2 Sep. `lem_machine_specs` then held the AF26 band (2.08..3.44) with
# `last_qc_value` 4.87 dated 24 Aug — an AO25 reading — and `last_qc_in_spec`
# 1, because it HAD been in spec against AO25's 4.73..7.13. The panel drew it
# as "4.87 mg/kg · sample AF26 · in spec", which is three true fields making
# one false sentence.
#
# The mechanism is on the bench: `carry_last_qc` copies the previous spec's
# last_qc_* onto the rebuilt spec list, matching on test name with no notion
# of which standard the verdict was made against, and `apply_last_qc` then
# never revisits a field that is already non-blank. Fixing that needs a module
# release onto every bench.
#
# THE SERVER DOES NOT HAVE TO WAIT FOR THAT. `lem_machine_log` records the
# `lab_id` every verdict was made against — all 907 QC rows carry one — so the
# floor can see for itself that the remembered reading belongs to a retired
# material, and decline to present it as this spec's control result. The log
# is the record; the field on the spec is a cache.
#
# What it must NOT do is invent a verdict of its own. The spec reads as never
# measured against the standard now in force, which is the honest state and
# the same grey "no QC on file" the floor already has a shape for.

def log_qc(gw, ts, lab_id, value, test_name=None):
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log ("
           "machine_uid TEXT, ts TEXT, kind TEXT, lab_id TEXT, "
           "test_name TEXT, value TEXT, detail TEXT)")
    gw.sql("INSERT INTO lem_machine_log VALUES (?,?,'qc',?,?,?,'{}')",
           ["5fd04c0031f9", ts, lab_id,
            test_name or "ASTM D7236/D7094 - Flash Point Closed cup "
                         "(small scale)", str(value)])


class TestAVerdictDoesNotSurviveAChangeOfStandard:
    def test_a_reading_from_the_retired_standard_is_not_shown_as_this_ones(self, gw, client):
        # The Multitek S shape exactly: the spec is on AF26, the remembered
        # reading was made against AO25.
        publish(gw, sample_id="AF26", low=2.08, high=3.44, expected=2.76,
                last_qc_at="2026-08-24T19:58:10", last_qc_value=4.87,
                last_qc_in_spec=1)
        log_qc(gw, "2026-08-24T19:58:10", "AO25", 4.87)
        spec = flash(client)["effective_specs"][0]
        assert spec["last_qc_value"] is None
        assert spec["last_qc_in_spec"] is None
        assert spec["last_qc_at"] == ""

    def test_the_band_and_the_standard_still_reach_the_floor(self, gw, client):
        # Only the stale VERDICT is withheld. An instrument with QC assigned
        # and none run yet still has a band to draw and a standard to name —
        # blanking the whole spec would put it back to "No QC assigned", which
        # is a different and equally wrong sentence.
        publish(gw, sample_id="AF26", low=2.08, high=3.44, expected=2.76,
                last_qc_at="2026-08-24T19:58:10", last_qc_value=4.87,
                last_qc_in_spec=1)
        log_qc(gw, "2026-08-24T19:58:10", "AO25", 4.87)
        spec = flash(client)["effective_specs"][0]
        assert spec["sample_id"] == "AF26"
        assert (spec["low"], spec["high"]) == (pytest.approx(2.08),
                                               pytest.approx(3.44))

    def test_a_reading_against_the_current_standard_is_left_alone(self, gw, client):
        # The ordinary case, which must not regress: the remembered verdict
        # was made against the standard the spec names, so it stands.
        publish(gw, sample_id="AF26", low=2.08, high=3.44, expected=2.76,
                last_qc_at="2026-09-02T17:06:41", last_qc_value=2.875,
                last_qc_in_spec=1)
        log_qc(gw, "2026-09-02T17:06:41", "AF26", 2.875)
        spec = flash(client)["effective_specs"][0]
        assert spec["last_qc_value"] == pytest.approx(2.875)
        assert spec["last_qc_in_spec"] is True

    def test_a_verdict_the_log_cannot_account_for_is_left_alone(self, gw, client):
        # NO LOG ROW AT THAT TIMESTAMP IS NOT EVIDENCE OF A MISMATCH. The log
        # is capped and a verdict can be older than the window, so silence
        # here means unknown. Withholding the reading on unknown would blank
        # the panel for every instrument whose QC predates the cap — the
        # failed-read-is-not-an-empty-result rule, in its display form.
        publish(gw, sample_id="AF26", low=2.08, high=3.44, expected=2.76,
                last_qc_at="2026-08-24T19:58:10", last_qc_value=4.87,
                last_qc_in_spec=1)
        log_qc(gw, "2026-09-02T17:06:41", "AF26", 2.875)   # a DIFFERENT row
        spec = flash(client)["effective_specs"][0]
        assert spec["last_qc_value"] == pytest.approx(4.87)

    def test_a_spec_naming_no_standard_is_not_second_guessed(self, gw, client):
        # A spec with a blank sample_id cannot disagree with anything.
        publish(gw, sample_id="", last_qc_at="2026-08-24T19:58:10",
                last_qc_value=65.0, last_qc_in_spec=1)
        log_qc(gw, "2026-08-24T19:58:10", "AO25", 65.0)
        spec = flash(client)["effective_specs"][0]
        assert spec["last_qc_value"] == pytest.approx(65.0)


class TestTheLocalCopyIsWhatAnswersInProduction:
    """The event arm cannot answer this, and believing it could would have
    shipped a fix that never fired.

    `EVENT_LIMIT` is 60 — the newest sixty rows LAB-WIDE, every kind, mostly
    `run`. A QC verdict from nine days ago is not in it, so on the real floor
    `_verdict_standard` would have returned None every time and every stale
    reading would have sailed through. It was caught because the end-to-end
    check was run against real production rows rather than against the
    fixtures, which held nothing but QC.

    The log mirror holds the WHOLE log locally and refreshes every five
    minutes, so it is what answers. A `GROUP BY` in the batched read was the
    other candidate and is rejected on purpose: filtering `kind='qc'` cannot
    use `(machine_uid, kind, ts DESC)`, so it is a full scan of 220k rows over
    the SMB share — the pattern LabCore's 8-second watchdog kills and that
    blocks every write in the building while it runs.
    """

    def mirror_for(self, gw, rows):
        import os, tempfile
        from log_mirror import LogMirror
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log ("
               "machine_uid TEXT, ts TEXT, kind TEXT, lab_id TEXT, "
               "test_name TEXT, value TEXT, detail TEXT)")
        for ts, lab_id, value in rows:
            gw.sql("INSERT INTO lem_machine_log VALUES (?,?,'qc',?,?,?,'{}')",
                   ["5fd04c0031f9", ts, lab_id,
                    "ASTM D7236/D7094 - Flash Point Closed cup (small scale)",
                    str(value)])
        m = LogMirror(gw, path=os.path.join(tempfile.mkdtemp(), "m.sqlite3"))
        m.refresh()
        return m

    def test_it_reads_the_standard_out_of_the_local_copy(self, gw, client):
        publish(gw, sample_id="AF26", low=2.08, high=3.44, expected=2.76,
                last_qc_at="2026-08-24T19:58:10", last_qc_value=4.87,
                last_qc_in_spec=1)
        client.application.config["LOG_MIRROR"] = self.mirror_for(
            gw, [("2026-08-24T19:58:10", "AO25", 4.87)])
        spec = flash(client)["effective_specs"][0]
        assert spec["last_qc_value"] is None
        assert spec["last_qc_superseded_by"] == "AO25"

    def test_a_verdict_far_older_than_the_event_cap_is_still_caught(self):
        # The whole point. `latest_qc` walks the entire copy, so depth is not
        # a limit the way it is on the snapshot's sixty-row window.
        import os, tempfile
        from labcore_gateway import FakeLabCoreGateway
        from log_mirror import LogMirror
        g = FakeLabCoreGateway()
        g.sql("CREATE TABLE IF NOT EXISTS lem_machine_log ("
              "machine_uid TEXT, ts TEXT, kind TEXT, lab_id TEXT, "
              "test_name TEXT, value TEXT, detail TEXT)")
        # 400 unrelated run rows sit on top of the verdict we need.
        for i in range(400):
            g.sql("INSERT INTO lem_machine_log VALUES "
                  "('m1', ?, 'run', '1', 'Sulfur', '1.0', '{}')",
                  ["2026-09-03T%02d:%02d:00" % (i // 60, i % 60)])
        g.sql("INSERT INTO lem_machine_log VALUES "
              "('m1','2026-08-24T19:58:10','qc','AO25','Sulfur','4.87','{}')")
        m = LogMirror(g, path=os.path.join(tempfile.mkdtemp(), "m.sqlite3"))
        m.refresh()
        assert ("m1", "Sulfur", "AO25") in m.latest_qc()

    def test_an_unfilled_copy_leaves_every_spec_exactly_as_published(self, gw, client):
        # A cold mirror must not read as "no QC has ever been run", which
        # would blank the QC panel for the whole lab on every restart.
        publish(gw, sample_id="AF26", low=2.08, high=3.44, expected=2.76,
                last_qc_at="2026-08-24T19:58:10", last_qc_value=4.87,
                last_qc_in_spec=1)
        spec = flash(client)["effective_specs"][0]
        assert spec["last_qc_value"] == pytest.approx(4.87)


class TestTheLogIsTheRecordAndTheSpecIsACache:
    """What the panel shows is the last verdict against the standard the spec
    NAMES, taken from the log — not the bench's cached field.

    Withholding a mismatched verdict was not enough, and finding that out took
    three passes over real production rows. Multitek S's NEWEST verdict really
    was on AF26 (2.875, 2 Sep), so nothing looked mismatched — while the field
    the bench had cached, and the panel drew, was the 24-Aug AO25 reading of
    4.87. Two earlier versions of this check agreed with the bug because the
    fixtures agreed with it too.
    """

    def mirror(self, gw, rows):
        import os, tempfile
        from log_mirror import LogMirror
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log ("
               "machine_uid TEXT, ts TEXT, kind TEXT, lab_id TEXT, "
               "test_name TEXT, value TEXT, detail TEXT)")
        for ts, lab_id, value, detail in rows:
            gw.sql("INSERT INTO lem_machine_log VALUES (?,?,'qc',?,?,?,?)",
                   ["5fd04c0031f9", ts, lab_id,
                    "ASTM D7236/D7094 - Flash Point Closed cup (small scale)",
                    str(value), detail])
        m = LogMirror(gw, path=os.path.join(tempfile.mkdtemp(), "m.sqlite3"))
        m.refresh()
        return m

    def test_a_stale_cache_is_replaced_by_the_real_reading(self, gw, client):
        # The Multitek S case exactly.
        publish(gw, sample_id="AF26", low=2.08, high=3.44, expected=2.76,
                last_qc_at="2026-08-24T19:58:10", last_qc_value=4.87,
                last_qc_in_spec=1)
        client.application.config["LOG_MIRROR"] = self.mirror(gw, [
            ("2026-08-24T19:58:10", "AO25", 4.87, '{"in_spec": true}'),
            ("2026-09-02T17:06:41", "AF26", 2.875, '{"in_spec": true}')])
        spec = flash(client)["effective_specs"][0]
        assert spec["last_qc_value"] == pytest.approx(2.875)
        assert spec["last_qc_at"].startswith("2026-09-02")

    def test_nothing_run_against_the_new_standard_shows_no_reading(self, gw, client):
        publish(gw, sample_id="AF26", low=2.08, high=3.44, expected=2.76,
                last_qc_at="2026-08-24T19:58:10", last_qc_value=4.87,
                last_qc_in_spec=1)
        client.application.config["LOG_MIRROR"] = self.mirror(gw, [
            ("2026-08-24T19:58:10", "AO25", 4.87, '{"in_spec": true}')])
        spec = flash(client)["effective_specs"][0]
        assert spec["last_qc_value"] is None
        assert spec["last_qc_superseded_by"] == "AO25"

    def test_a_failure_in_the_batch_is_what_the_panel_says(self, gw, client):
        # A bench logs a whole print at ONE instant. Multitek NS wrote seven
        # readings at 17:05:14 on 3 Sep — six around 2.79 and one at 5.248
        # that put it on RED. Whichever row happened to be last would have
        # made the panel read "2.78 · in spec" under a red dot, about the very
        # batch that failed.
        publish(gw, sample_id="AF26", low=2.08, high=3.44, expected=2.76)
        at = "2026-09-03T17:05:14"
        client.application.config["LOG_MIRROR"] = self.mirror(gw, [
            (at, "AF26", 2.789, '{"in_spec": true}'),
            (at, "AF26", 5.248, '{"in_spec": false}'),
            (at, "AF26", 2.780, '{"in_spec": true}')])
        spec = flash(client)["effective_specs"][0]
        assert spec["last_qc_value"] == pytest.approx(5.248)
        assert spec["last_qc_in_spec"] is False

    def test_an_unreadable_detail_keeps_the_reading_and_loses_the_verdict(self, gw, client):
        publish(gw, sample_id="AF26", low=2.08, high=3.44, expected=2.76)
        client.application.config["LOG_MIRROR"] = self.mirror(gw, [
            ("2026-09-02T17:06:41", "AF26", 2.875, "{not json")])
        spec = flash(client)["effective_specs"][0]
        assert spec["last_qc_value"] == pytest.approx(2.875)
        assert spec["last_qc_in_spec"] is None

    def test_a_test_the_copy_has_never_seen_leaves_the_bench_alone(self, gw, client):
        # Silence is not evidence: the copy fills incrementally.
        publish(gw, sample_id="AF26", low=2.08, high=3.44, expected=2.76,
                last_qc_at="2026-09-02T17:06:41", last_qc_value=2.875,
                last_qc_in_spec=1)
        client.application.config["LOG_MIRROR"] = self.mirror(gw, [
            ("2026-09-02T17:06:41", "AF26", 9.9, '{"in_spec": true}')])
        # …but for a DIFFERENT test, so this spec has no evidence either way.
        gw.sql("UPDATE lem_machine_log SET test_name = 'Something Else'")
        client.application.config["LOG_MIRROR"] = self.mirror(gw, [])
        spec = flash(client)["effective_specs"][0]
        assert spec["last_qc_value"] == pytest.approx(2.875)
