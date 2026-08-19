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
