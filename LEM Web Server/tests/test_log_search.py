"""The Logs page searches the log, not the page of it that was fetched.

Ryan, 31 Aug 2026: *"the log searchng still sucks. Also the flash logs arent
showing."*

Both are the same defect, reproduced against the live lab before anything was
changed: typing `Flash` into the Logs filter returned **2 events** out of a
214,714-row log that holds thousands of flash-point rows.

The cause is the order of operations. `_log_entries` asks LabCore for the
newest `limit` rows and *then* filters them in Python:

    for row in _log_rows(args):        # <- the newest 500, already chosen
        ...
        if needle not in hay: continue # <- searched only those 500

So the search was never a search of the log. It was a search of whichever page
happened to be fetched, and every match older than that page did not exist as
far as the page was concerned. That is also why the flash instruments looked
absent: their rows are there in their thousands, just not in the newest 500 of
a lab where the Eraspec NIR writes constantly.

The fix is the one already used by the floor's search box: ask the local
mirror, which holds every row and is indexed, instead of grepping a page. The
filters — machine, kind, dates — go into that query too, so narrowing by
instrument narrows the SEARCH rather than narrowing what gets grepped.
"""

import pytest

import snapshot_service
from labcore_gateway import FakeLabCoreGateway
from web_app import create_app


@pytest.fixture
def gw():
    g = FakeLabCoreGateway()
    snapshot_service.SnapshotService(g).ensure_schema()
    return g


def _log(gw, uid, ts, kind="run", lab="1", test="Flash Point", value="60"):
    gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
           "test_name, value, detail) VALUES (?, ?, ?, ?, ?, ?, '{}')",
           [uid, ts, kind, lab, test, value])


def _busy_lab(gw, old_flash=3, noise=400):
    """A lab shaped like the real one: one instrument writing constantly, and
    the thing being looked for buried underneath it."""
    for i in range(old_flash):
        _log(gw, "pac-flash-1", "2026-07-%02dT09:00:00" % (i + 1),
             lab=str(100 + i), test="ASTM D7236 - Flash Point")
    for i in range(noise):
        _log(gw, "eraspec-nir", "2026-08-%02dT%02d:%02d:00"
             % (10 + i // 200, (i // 8) % 24, i % 60),
             lab=str(9000 + i), test="Density and API Gravity", value="0.84")


def _client(gw, tmp_path, fill=True):
    app = create_app(gw, secret="t", documents_root=str(tmp_path))
    app.config.update(TESTING=True)
    if fill:
        app.config["LOG_MIRROR"].refresh()
    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = "ryan"
    return c


class TestASearchReachesPastTheFetchedPage:
    def test_a_match_older_than_the_page_is_found(self, gw, tmp_path):
        """The bug, exactly: three flash rows under four hundred newer ones,
        with the page capped below that."""
        _busy_lab(gw)
        c = _client(gw, tmp_path)
        body = c.get("/api/logs?q=Flash&limit=50").get_json()
        got = body.get("events") or []
        assert len(got) == 3, (
            "found %d of 3 — the search only looked at the fetched page"
            % len(got))

    def test_it_says_it_searched_everything(self, gw, tmp_path):
        _busy_lab(gw)
        body = _client(gw, tmp_path).get("/api/logs?q=Flash&limit=50").get_json()
        assert body.get("searched_all_time") is True

    def test_a_term_that_matches_nothing_is_still_nothing(self, gw, tmp_path):
        _busy_lab(gw)
        body = _client(gw, tmp_path).get(
            "/api/logs?q=zzzz-nope&limit=50").get_json()
        assert (body.get("events") or []) == []

    def test_no_query_is_unchanged(self, gw, tmp_path):
        """Without a search term this is a paged listing and must stay one —
        the whole log is not an answer to "show me the log"."""
        _busy_lab(gw)
        body = _client(gw, tmp_path).get("/api/logs?limit=50").get_json()
        assert len(body.get("events") or []) == 50
        assert not body.get("searched_all_time")


class TestTheFiltersNarrowTheSearchNotThePage:
    def test_searching_within_one_instrument(self, gw, tmp_path):
        _busy_lab(gw)
        _log(gw, "pac-flash-2", "2026-07-01T09:00:00", lab="500",
             test="ASTM D7236 - Flash Point")
        c = _client(gw, tmp_path)
        body = c.get(
            "/api/logs?q=Flash&machine=pac-flash-1&limit=50").get_json()
        got = body.get("events") or []
        assert got and all(e["machine_uid"] == "pac-flash-1" for e in got)
        assert len(got) == 3

    def test_searching_within_one_kind(self, gw, tmp_path):
        _busy_lab(gw)
        _log(gw, "pac-flash-1", "2026-07-09T09:00:00", kind="qc", lab="600",
             test="ASTM D7236 - Flash Point")
        c = _client(gw, tmp_path)
        body = c.get("/api/logs?q=Flash&kind=qc&limit=50").get_json()
        got = body.get("events") or []
        assert len(got) == 1 and got[0]["kind"] == "qc"

    def test_searching_within_a_date_range(self, gw, tmp_path):
        _busy_lab(gw)
        c = _client(gw, tmp_path)
        body = c.get("/api/logs?q=Flash&since=2026-07-02&limit=50").get_json()
        got = body.get("events") or []
        assert len(got) == 2, [e["ts"] for e in got]

    def test_the_limit_still_binds(self, gw, tmp_path):
        _busy_lab(gw, old_flash=40)
        body = _client(gw, tmp_path).get("/api/logs?q=Flash&limit=10").get_json()
        assert len(body.get("events") or []) == 10


class TestItStillWorksWithNoMirror:
    """The mirror is a cache. A cold one must degrade to the old behaviour,
    not to an empty page."""

    def test_a_cold_mirror_still_answers(self, gw, tmp_path):
        _busy_lab(gw)
        body = _client(gw, tmp_path, fill=False).get(
            "/api/logs?q=Flash&limit=500").get_json()
        assert body.get("events"), "a cold mirror emptied the page"

    def test_a_failed_read_is_not_an_empty_result(self, gw, tmp_path):
        _busy_lab(gw)
        app = create_app(gw, secret="t", documents_root=str(tmp_path))
        app.config.update(TESTING=True)

        def blind(sql, args=None, **kw):
            if "lem_machine_log" in sql:
                return {"error": "LabCore is busy", "busy": True}
            return {"ok": True, "rows": []}

        gw.read_sql = blind
        c = app.test_client()
        with c.session_transaction() as s:
            s["user"] = "ryan"
        body = c.get("/api/logs?q=Flash").get_json()
        assert str(body.get("error", "")).strip(), (
            "a failed read served as a clean empty search result")
