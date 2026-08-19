"""The master view must offer a correction for every reported method too.

Ryan, 2026-08-04: corrections apply to every measurement (ISO/IEC 17025 §7). If they
can only be *set* on QC-assigned tests, that is unusable — QC is assignment-only, so
most reported methods have no spec, and those are exactly the customer results.

The method list needs no new plumbing: `lem_machine_config` already stores the whole
machine, mappings included, so the methods a bench reports can be read from what the
module already publishes.
"""
import json

import pytest

from labcore_gateway import FakeLabCoreGateway


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


AGILENT = {
    "uid": "gc1", "title": "Agilent GC",
    "mappings": [{"methods": ["IBP", "10%", "50%", "90%", "FBP"]}],
    "tests": [{"name": "IBP", "value_col": "IBP", "expected": 166.0,
               "std_dev": 3.4, "k": 2.0}],
}


@pytest.fixture
def gw():
    g = FakeLabCoreGateway()
    g.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
          "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
          "reason TEXT, updated_at TEXT)")
    g.sql("INSERT INTO lem_machine_status VALUES "
          "('gc1','Agilent GC','GREEN','ok','2026-08-04T09:00:00')")
    g.sql("CREATE TABLE IF NOT EXISTS lem_machine_config ("
          "machine_uid TEXT PRIMARY KEY, title TEXT, config TEXT, "
          "updated_at TEXT, updated_by TEXT)")
    g.sql("INSERT INTO lem_machine_config VALUES (?,?,?,?,?)",
          ["gc1", "Agilent GC", json.dumps(AGILENT), "2026-08-04T09:00:00", "k"])
    return g


@pytest.fixture
def client(gw):
    from web_app import create_app
    app = create_app(gw, authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client()


def methods(client, uid="gc1"):
    return client.get(f"/api/machines/{uid}/corrections").get_json().get("methods")


class TestTheMethodListIsOffered:
    def test_every_mapped_method_is_offered(self, client):
        assert methods(client) == ["10%", "50%", "90%", "FBP", "IBP"]

    def test_it_needs_no_extra_table(self, gw, client):
        """Read from the config the module already publishes."""
        reads = []
        real = gw.read_sql
        gw.read_sql = lambda s, a=None, **k: (reads.append(s), real(s, a, **k))[1]
        methods(client)
        assert any("lem_machine_config" in s for s in reads)

    def test_a_method_with_a_saved_correction_is_included(self, gw, client):
        """Even when no longer mapped, or it could never be found to remove."""
        gw.sql("CREATE TABLE IF NOT EXISTS lem_correction_factors ("
               "machine_uid TEXT NOT NULL, test_name TEXT NOT NULL, "
               "correction REAL NOT NULL DEFAULT 0.0, units TEXT, "
               "updated_at TEXT, updated_by TEXT, "
               "PRIMARY KEY (machine_uid, test_name))")
        gw.sql("INSERT INTO lem_correction_factors VALUES "
               "('gc1','Retired',1.0,'','x','k')")
        assert "Retired" in methods(client)

    def test_a_machine_with_no_config_yet_still_answers(self, gw, client):
        gw.sql("INSERT INTO lem_machine_status VALUES "
               "('new1','Fresh Bench','GREEN','ok','2026-08-04T09:00:00')")
        got = client.get("/api/machines/new1/corrections").get_json()
        assert got["methods"] == []
        assert got["corrections"] == []

    def test_unparseable_config_does_not_break_the_dialog(self, gw, client):
        gw.sql("UPDATE lem_machine_config SET config = 'not json' "
               "WHERE machine_uid = 'gc1'")
        assert methods(client) == []

    def test_a_correction_saves_for_a_method_with_no_qc(self, client):
        """The whole point — FBP has no spec."""
        client.post("/api/login", json={"username": "k", "password": "good"})
        r = client.post("/api/machines/gc1/corrections",
                        json={"test_name": "FBP", "correction": -5.57})
        assert r.status_code == 200
        got = {c["test_name"]: c["correction"] for c in
               client.get("/api/machines/gc1/corrections").get_json()["corrections"]}
        assert got["FBP"] == pytest.approx(-5.57)

    def test_all_five_agilent_offsets_save(self, client):
        client.post("/api/login", json={"username": "k", "password": "good"})
        want = {"IBP": -12.08, "10%": -5.25, "50%": -4.06, "90%": -3.46,
                "FBP": -5.57}
        for name, value in want.items():
            assert client.post("/api/machines/gc1/corrections",
                               json={"test_name": name,
                                     "correction": value}).status_code == 200
        got = {c["test_name"]: c["correction"] for c in
               client.get("/api/machines/gc1/corrections").get_json()["corrections"]}
        assert got == {k: pytest.approx(v) for k, v in want.items()}


class TestTheDialogUsesIt:
    def src(self):
        import pathlib
        return (pathlib.Path(__file__).resolve().parent.parent
                / "templates" / "floor.html").read_text(encoding="utf-8")

    def test_the_dialog_reads_the_method_list(self):
        s = self.src()
        block = s[s.index("async function renderCorrections"):]
        block = block[:block.index("\n}")]
        assert "methods" in block

    def test_the_dialog_says_corrections_apply_to_all_results(self):
        """An operator setting this must know it changes reported results, not just
        the QC verdict."""
        s = self.src()
        dlg = s[s.index('id="corrDlg"'):s.index('id="corrBody"')]
        assert "every" in dlg.lower() or "all " in dlg.lower()
