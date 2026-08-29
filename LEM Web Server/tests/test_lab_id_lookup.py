"""Looking a sample up by Lab ID, on a log that is now five times bigger.

Ryan, 28 Aug 2026: *"the UI is not showing it … it looks like we didnt run
anything for like 2-3 days but the data is in labcore which means it got
parsed which means its in there."*

He was right, and the cause was the history import. `lem_machine_log` went from
41,905 rows to 214,714 — five times — and `lab_id` has never been indexed. The
two indexes that exist are on `ts` and on `(machine_uid, kind, ts)`, so a
lookup by Lab ID is a full scan of the whole table across an SMB share.
Measured against the live lab immediately after the import:

    SELECT ... FROM lem_machine_log WHERE lab_id = '38145'
    -> Read cancelled after 8s to protect the write queue
       (query too slow — likely an unindexed scan)

CLAUDE.md already names this exact hazard for this exact table: *"LabCore
interrupts any read over 8s and its comment names 'an unindexed scan over the
SMB share' as the hazard — so this was heading for a cliff, not a slope."* The
import is what walked it off the cliff.

Two separate defects, and both are needed. Either alone leaves a lie on screen:

**The index.** Without it the query cannot finish, however well the failure is
reported.

**The failure has to reach the screen as a failure.** A cancelled read returns
no rows, and every caller that reads `res["rows"] or []` turns that into "this
sample was never tested" — which is the sentence Ryan was actually shown. I
made the identical mistake in my own diagnostic five minutes earlier and told
him lab 38145 did not exist, so this is not a hypothetical failure mode.
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


def _client(gw, tmp_path):
    app = create_app(gw, secret="t", documents_root=str(tmp_path))
    app.config.update(TESTING=True)
    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = "ryan"
    return c


class TestLabIdIsIndexed:
    """The fix that makes the query possible at all."""

    def test_the_index_is_declared(self):
        """Declared centrally, beside the two that already exist, so a fresh
        LabCore gets it at boot rather than the first time somebody searches."""
        src = open(snapshot_service.__file__, encoding="utf-8").read()
        assert "idx_lem_log_lab_ts" in src
        assert "ON lem_machine_log(lab_id" in src.replace("\n", " ")

    def test_it_is_created_by_ensure_schema(self, gw):
        rows = gw.read_sql(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='lem_machine_log'")["rows"]
        names = {r["name"] for r in rows}
        assert any("lab" in n for n in names), names

    def test_the_existing_indexes_are_still_there(self, gw):
        """The two that exist carry the floor's snapshot and the history reads.
        Adding one must not be a rename of another."""
        rows = gw.read_sql(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='lem_machine_log'")["rows"]
        names = {r["name"] for r in rows}
        assert "idx_lem_log_ts" in names
        assert "idx_lem_log_uid_kind_ts" in names


class TestASampleCanBeLookedUp:
    def test_a_lab_id_returns_its_rows(self, gw, tmp_path):
        for m in ("multitek-s", "agilent-gc-1"):
            gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
                   "test_name, value, detail) VALUES (?, '2026-08-22T02:53:00', "
                   "'run', '38145', 'Sulfur', '1.131', '{}')", [m])
        body = _client(gw, tmp_path).get(
            "/api/logs?q=38145&limit=all").get_json()
        got = [e for e in (body.get("events") or [])
               if str(e.get("lab_id")) == "38145"]
        assert len(got) == 2, body.get("events")

    def test_a_lab_id_nobody_ran_is_an_empty_answer(self, gw, tmp_path):
        body = _client(gw, tmp_path).get(
            "/api/logs?q=39999&limit=all").get_json()
        assert (body.get("events") or []) == []
        assert not body.get("error")


class TestACancelledReadIsNotAnEmptyResult:
    """The half that decides what the operator is TOLD.

    "This sample was never tested" and "the search could not finish" are
    different sentences, and only one of them is a statement about the record.
    Ryan was shown the first when the second was true — and so was I, by my own
    query, which is how the wrong answer got as far as him.
    """

    @staticmethod
    def _cancelling(real):
        def go(sql, args=None, **kw):
            if "lem_machine_log" in sql and "lab_id" in sql:
                return {"error": "Read cancelled after 8s to protect the write "
                                 "queue (query too slow — likely an unindexed "
                                 "scan).", "busy": True}
            return real(sql, args, **kw)
        return go

    def test_the_logs_search_says_it_failed(self, gw, tmp_path):
        gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
               "test_name, value, detail) VALUES ('m', '2026-08-22T02:53:00', "
               "'run', '38145', 'Sulfur', '1.131', '{}')")
        c = _client(gw, tmp_path)
        gw.read_sql = self._cancelling(gw.read_sql)
        body = c.get("/api/logs?q=38145&limit=all").get_json()
        assert str(body.get("error", "")).strip(), (
            "a cancelled read was served as a clean empty result, which reads "
            "as 'this sample was never tested'")

    def test_and_does_not_serve_a_confident_empty_list(self, gw, tmp_path):
        c = _client(gw, tmp_path)
        gw.read_sql = self._cancelling(gw.read_sql)
        body = c.get("/api/logs?q=38145&limit=all").get_json()
        # Either it refuses outright, or it answers WITH the error alongside.
        # What it may not do is return [] and nothing else.
        assert body.get("error") or body.get("events") is None

    def test_the_lab_wide_search_reports_it_too(self, gw, tmp_path):
        """`/api/search` is the floor's search box — the other way somebody
        types a Lab ID into LEM. It already reports `corpus.stale`, and this
        pins that it keeps doing so rather than answering `no_match`."""
        c = _client(gw, tmp_path)
        gw.read_sql = self._cancelling(gw.read_sql)
        body = c.get("/api/search?q=38145").get_json() or {}
        assert body.get("state") != "no_match" or (body.get("corpus") or {}).get("stale"), (
            "a cancelled corpus read answered 'no match', which is a statement "
            "about the lab made from a failure to read it")


class TestASampleOlderThanTheSearchWindowIsStillFound:
    """The defect the import created, and the reason Ryan could not find a
    sample he had run.

    The search corpus is the newest `SEARCH_CORPUS_ROWS` (20,000) rows of
    `lem_machine_log`. That was most of the table at 41,905 rows. After the
    history import it is 214,714 rows, so the same 20,000 reaches back only ten
    days — measured on the live lab, to 2026-08-18. Every sample older than
    that answered `no_match`, which reads as "this sample was never tested".

    A Lab ID is not a fuzzy search term. It is an exact key, and an exact key
    should be answered by looking it up, not by hoping it fell inside a rolling
    window. That is what the index is for.
    """

    @staticmethod
    def _old_sample(gw, lab_id="30001", n_recent=60):
        gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
               "test_name, value, detail) VALUES ('multitek-s', "
               "'2025-03-04T09:00:00', 'run', ?, 'Sulfur', '1.131', '{}')",
               [lab_id])
        for i in range(n_recent):          # newer traffic that crowds it out
            gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, "
                   "lab_id, test_name, value, detail) VALUES ('agilent-gc-1', "
                   "?, 'run', ?, 'Dist - IBP', '160.0', '{}')",
                   ["2026-08-2%dT%02d:00:00" % (i % 8, i % 24), str(40000 + i)])

    def test_a_lab_id_outside_the_window_is_found(self, gw, tmp_path,
                                                  monkeypatch):
        import web_app
        monkeypatch.setattr(web_app, "SEARCH_CORPUS_ROWS", 10)
        self._old_sample(gw)
        body = _client(gw, tmp_path).get("/api/search?q=30001").get_json() or {}
        assert body.get("state") == "ok", body.get("state")
        hits = body.get("hits") or body.get("results") or []
        assert hits, "an exact Lab ID fell outside the corpus and was denied"

    def test_and_a_lab_id_that_truly_does_not_exist_still_says_so(
            self, gw, tmp_path, monkeypatch):
        """The direct lookup must not turn every miss into a maybe."""
        import web_app
        monkeypatch.setattr(web_app, "SEARCH_CORPUS_ROWS", 10)
        self._old_sample(gw)
        body = _client(gw, tmp_path).get("/api/search?q=99999").get_json() or {}
        assert body.get("state") == "no_match", body.get("state")

    def test_a_non_numeric_query_does_not_trigger_a_lookup(self, gw, tmp_path):
        """Only an exact Lab ID earns a direct read. "flash" is a fuzzy term
        and must stay inside the corpus, or every keystroke becomes a query
        against a 200,000-row table."""
        self._old_sample(gw)
        hits = {"n": 0}
        real = gw.read_sql

        def counted(sql, args=None, **kw):
            if "lab_id = ?" in sql:
                hits["n"] += 1
            return real(sql, args, **kw)

        gw.read_sql = counted
        _client(gw, tmp_path).get("/api/search?q=flash")
        assert hits["n"] == 0, "a word search did a Lab ID lookup"
