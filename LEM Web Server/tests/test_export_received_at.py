"""Both exports say when a reading was measured AND when LEM received it.

Ryan, 5 Oct 2026, on AF26: LEM's export had "completely different
temperatures" from GC-1's. The values were right; the dates were not. A bench
stamped every reading with the moment it parsed the line, and re-read old
lines after each restart, so the same injection sat in the export under
several dates, and matching by date found the wrong one.

A bench that marks a result-time cell now logs `ts` as the instrument's time
and puts the moment it read the line in the detail as `received_at`. The
exports carry that as its own column. It goes on the END of each file, so a
spreadsheet that reads these columns by position keeps working. Blank means
the bench dates readings when it reads them, and then `timestamp` already is
that moment.
"""
import csv
import io
import json

import pytest

from labcore_gateway import FakeLabCoreGateway


@pytest.fixture
def gw():
    g = FakeLabCoreGateway()
    g.sql("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
          "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
          "detail TEXT)")
    return g


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


def log(gw, ts, detail):
    # Columns named: on the integration branch the suite's gateway is LEM's
    # store, whose lem_machine_log carries more than these seven columns, so
    # a bare positional INSERT is refused (returned as an error, not raised)
    # and the export would be tested against an empty log.
    res = gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
                 "test_name, value, detail) VALUES (?,?,?,?,?,?,?)",
                 ["bf8e64b59f12", ts, "qc", "AF26",
                  "ASTM D2887/D86 - Distillation in Petroleum Products, "
                  "50% Recovery",
                  "251.08", json.dumps(detail)])
    assert not (isinstance(res, dict) and res.get("error")), res


def rows(resp):
    return list(csv.DictReader(io.StringIO(resp.get_data(as_text=True))))


DATED = {"in_spec": True, "expected": 251.37, "low": 249.29, "high": 253.45,
         "instrument_time": "2026-10-05T15:20:11",
         "received_at": "2026-10-05T16:00:00"}
UNDATED = {"in_spec": True, "expected": 251.37, "low": 249.29, "high": 253.45}


@pytest.mark.parametrize("url", ["/api/export/qc.csv",
                                 "/api/machines/bf8e64b59f12/export.csv"])
class TestReceivedAt:
    def test_it_is_the_last_column(self, gw, client, url):
        log(gw, "2026-10-05T15:20:11", DATED)
        head = next(csv.reader(io.StringIO(
            client.get(url).get_data(as_text=True))))
        assert head[-1] == "received_at"

    def test_a_dated_reading_shows_both_times(self, gw, client, url):
        log(gw, "2026-10-05T15:20:11", DATED)
        (row,) = rows(client.get(url))
        assert row["timestamp"] == "2026-10-05T15:20:11"
        assert row["received_at"] == "2026-10-05T16:00:00"

    def test_an_undated_reading_leaves_it_blank(self, gw, client, url):
        log(gw, "2026-10-05T16:00:00", UNDATED)
        (row,) = rows(client.get(url))
        assert row["received_at"] == ""
