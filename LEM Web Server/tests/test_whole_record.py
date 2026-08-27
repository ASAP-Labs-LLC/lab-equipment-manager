"""The whole record has to be reachable, not just the newest page of it.

Ryan, 27 Aug: *"I dont like that the history is cut off, please at least in the
history tab and in the logs tab make it actually show the entire database."*

Two caps stood in the way, and only one of them was ever adjustable:

    equipment_history.LOG_LIMIT = 200        # a CEILING, not a default
    /api/logs   min(limit or 300, 2000)      # a clamp

`LOG_LIMIT` is the worse of the two because a caller cannot argue with it.
`timeline(limit=5000)` returned 200 log rows and reported `truncated`, so the
History tab could not show more of the record no matter what it asked for.

**The database is not the constraint.** Measured against live LabCore, 27 Aug:

    lem_machine_log                     41,903 rows
    Agilent GC 1, every row              2.23 s   26,106 rows   13.8 MB
    the whole table                      1.00 s   41,903 rows   18.9 MB

Both are well inside LabCore's 8 s read interrupt. What cannot take it is the
browser: 19 MB of JSON and 42,000 DOM rows is not a page anybody can use, and
this is the same request that has to stay off the polled paths.

So the cap becomes a DEFAULT and the record becomes walkable: `limit=all`
serves everything for anyone who genuinely wants it (the CSV export, a
scripted reader), and the page walks backwards with `before` until it reaches
the start — which it must be able to SAY it has reached. "There is no more"
and "we stopped asking" are different sentences and only one of them is a
statement about the record; that distinction is the whole point of this file.
"""

import pytest

import equipment_history
import snapshot_service
from labcore_gateway import FakeLabCoreGateway
from web_app import create_app

UID = "agilent-gc-1"


def _log(gw, ts, kind="run", uid=UID):
    gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
           "test_name, value, detail) VALUES (?, ?, ?, '', 'Flash Point', "
           "'1.0', '{}')", [uid, ts, kind])


def _many(gw, n, uid=UID):
    """`n` rows, oldest first, one a minute — deep enough to cross LOG_LIMIT."""
    for i in range(n):
        _log(gw, "2026-08-%02dT%02d:%02d:00"
             % (1 + i // 1440, (i // 60) % 24, i % 60), uid=uid)


@pytest.fixture
def gw():
    g = FakeLabCoreGateway()
    snapshot_service.SnapshotService(g).ensure_schema()
    return g


def _client(gw, tmp_path=None):
    """`documents_root` is where the log mirror's file lands, so every test
    needs its own. Without it they share one file under `LEM Web Server/data/`
    and one test's 500 rows are still there for the next one — which is how
    "an unfilled mirror falls back" passed against a mirror somebody else
    filled."""
    app = create_app(gw, secret="t",
                     documents_root=str(tmp_path) if tmp_path else None)
    app.config.update(TESTING=True)
    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = "ryan"
    return c


class TestTheLogCapIsADefaultNotACeiling:
    """`LOG_LIMIT` could not be argued with. Asking for more got 200."""

    def test_asking_for_more_than_the_default_gets_more(self, gw, tmp_path):
        _many(gw, 500)
        body = _client(gw, tmp_path).get(
            "/api/equipment/%s/history?limit=400" % UID).get_json()
        assert len(body["entries"]) == 400, len(body["entries"])

    def test_the_default_is_still_the_default(self, gw, tmp_path):
        """Nothing that does not ask changes behaviour — the panel opens on a
        page, not on twenty-six thousand rows."""
        _many(gw, 500)
        body = _client(gw, tmp_path).get("/api/equipment/%s/history" % UID).get_json()
        assert len(body["entries"]) == equipment_history.EquipmentHistory.LOG_DEFAULT
        assert body["truncated"] is True

    def test_all_means_all(self, gw, tmp_path):
        _many(gw, 500)
        body = _client(gw, tmp_path).get(
            "/api/equipment/%s/history?limit=all" % UID).get_json()
        assert len(body["entries"]) == 500
        assert body["truncated"] is False

    def test_and_says_so_rather_than_leaving_it_to_be_inferred(self, gw, tmp_path):
        """Reaching the beginning of the record is the answer to the question
        the operator actually has. It must be stated, not deduced from a count
        that came back smaller than the one that was asked for."""
        _many(gw, 500)
        body = _client(gw, tmp_path).get(
            "/api/equipment/%s/history?limit=all" % UID).get_json()
        assert body.get("complete") is True

    def test_a_partial_page_does_not_claim_to_be_the_whole_record(self, gw, tmp_path):
        _many(gw, 500)
        body = _client(gw, tmp_path).get(
            "/api/equipment/%s/history?limit=100" % UID).get_json()
        assert body["truncated"] is True
        assert not body.get("complete")


class TestTheRecordCanBeWalkedBackwards:
    """19 MB in one response is not a page anybody can use. `before` is how the
    tab reaches the start without ever holding the whole table."""

    def test_before_returns_older_entries_than_the_cursor(self, gw, tmp_path):
        _many(gw, 500)
        first = _client(gw, tmp_path).get(
            "/api/equipment/%s/history?limit=100" % UID).get_json()
        oldest = first["entries"][-1]["at"]
        page = _client(gw, tmp_path).get(
            "/api/equipment/%s/history?limit=100&before=%s" % (UID, oldest)
        ).get_json()
        assert page["entries"], "no second page"
        assert all(e["at"] < oldest for e in page["entries"]), (
            "a page that repeats the cursor row shows a duplicate on screen, "
            "and one that skips it loses a row of the record")

    def test_walking_to_the_end_reaches_every_entry_exactly_once(self, gw, tmp_path):
        _many(gw, 450)
        client, seen, cursor = _client(gw, tmp_path), [], None
        for _ in range(20):                       # a bound, not an expectation
            url = "/api/equipment/%s/history?limit=100" % UID
            if cursor:
                url += "&before=%s" % cursor
            body = client.get(url).get_json()
            seen.extend(e["at"] for e in body["entries"])
            if body.get("complete"):
                break
            cursor = body["entries"][-1]["at"]
        assert len(seen) == 450, len(seen)
        assert len(set(seen)) == 450, "the walk repeated or dropped rows"

    def test_the_last_page_says_it_is_the_last(self, gw, tmp_path):
        _many(gw, 150)
        body = _client(gw, tmp_path).get(
            "/api/equipment/%s/history?limit=100&before=%s" % (
                UID, "2026-08-01T01:00:00")).get_json()
        assert body.get("complete") is True


class TestTheLogsPageCanReachTheWholeTable:
    def test_all_is_served(self, gw, tmp_path):
        _many(gw, 2500)
        body = _client(gw, tmp_path).get("/api/logs?limit=all").get_json()
        assert len(body["events"]) == 2500, len(body["events"])

    def test_the_old_clamp_no_longer_caps_a_deliberate_ask(self, gw, tmp_path):
        """2000 was the ceiling. A lab with more than that in its log could
        not see the rest from this page at all."""
        _many(gw, 2500)
        body = _client(gw, tmp_path).get("/api/logs?limit=2400").get_json()
        assert len(body["events"]) == 2400

    def test_a_plain_open_is_still_a_page(self, gw, tmp_path):
        _many(gw, 2500)
        body = _client(gw, tmp_path).get("/api/logs").get_json()
        assert len(body["events"]) < 2500


class TestReachingTheEndIsNeverConfusedWithFailing:
    """The one that turns into a finding. "That is the whole record" said
    about a read that died halfway is worse than any amount of truncation."""

    def test_a_failed_deep_read_is_not_the_end_of_the_record(self, gw, tmp_path):
        _many(gw, 500)
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
        r = c.get("/api/equipment/%s/history?limit=all" % UID)
        assert r.status_code >= 400, r.get_json()
        assert "complete" not in (r.get_json() or {})

    def test_an_empty_instrument_is_complete_rather_than_truncated(self, gw, tmp_path):
        _many(gw, 10, uid="somebody-else")
        body = _client(gw, tmp_path).get(
            "/api/equipment/%s/history?limit=all" % UID).get_json()
        assert body["entries"] == []
        assert body.get("complete") is True
        assert body["truncated"] is False


class TestTheDeepReadsComeFromTheLocalCopy:
    """Where the mirror is used, and where it deliberately is not.

    A plain open of either page still reads LabCore, so what appears when the
    panel opens is current to the second. The mirror serves the WALK — `before`
    and `limit=all` — which is where the cost actually was: 26,106 rows at
    2.23 s, and every one of those seconds is a write slot the benches are
    queued behind. Trading freshness on the first page, which nobody complained
    about, would buy nothing.
    """

    def _app(self, gw, tmp_path):
        app = create_app(gw, secret="t", documents_root=str(tmp_path))
        app.config.update(TESTING=True)
        return app

    def _signed_in(self, app):
        c = app.test_client()
        with c.session_transaction() as s:
            s["user"] = "ryan"
        return c

    def test_a_plain_open_still_asks_labcore(self, gw, tmp_path):
        _many(gw, 50)
        app = self._app(gw, tmp_path)
        app.config["LOG_MIRROR"].refresh()
        hits = {"n": 0}
        real = gw.read_sql

        def counted(sql, args=None, **kw):
            if "lem_machine_log" in sql:
                hits["n"] += 1
            return real(sql, args, **kw)

        gw.read_sql = counted
        self._signed_in(app).get("/api/equipment/%s/history" % UID)
        assert hits["n"] >= 1, "the first page went stale-by-default"

    def test_a_deep_walk_does_not(self, gw, tmp_path):
        """The whole reason for the mirror. Reading LabCore here is what made
        'show me everything' a load problem instead of a feature."""
        _many(gw, 500)
        app = self._app(gw, tmp_path)
        app.config["LOG_MIRROR"].refresh()
        hits = {"n": 0}
        real = gw.read_sql

        def counted(sql, args=None, **kw):
            if "lem_machine_log" in sql:
                hits["n"] += 1
            return real(sql, args, **kw)

        gw.read_sql = counted
        body = self._signed_in(app).get(
            "/api/equipment/%s/history?limit=all" % UID).get_json()
        assert len(body["entries"]) == 500
        assert hits["n"] == 0, (
            "the deep read went to LabCore; the mirror exists so it does not")

    def test_an_unfilled_mirror_falls_back_rather_than_reporting_nothing(
            self, gw, tmp_path):
        """A cold start must not answer "this instrument has no history".
        The mirror is a cache; LabCore is still the record."""
        _many(gw, 300)
        app = self._app(gw, tmp_path)          # never refreshed
        body = self._signed_in(app).get(
            "/api/equipment/%s/history?limit=all" % UID).get_json()
        assert len(body["entries"]) == 300

    def test_the_answer_says_when_the_copy_was_taken(self, gw, tmp_path):
        """Up to five minutes stale is fine and invisible on a record that
        goes back months — but it may not be silent."""
        _many(gw, 300)
        app = self._app(gw, tmp_path)
        app.config["LOG_MIRROR"].refresh()
        body = self._signed_in(app).get(
            "/api/equipment/%s/history?limit=all" % UID).get_json()
        assert body.get("source") == "mirror"
        assert body.get("mirrored_at")


class TestAMirroredPageKnowsWhetherItIsTheLast:
    """Caught against live LabCore, not by a test — which is why it is here.

    Walking Agilent GC 1's 26,106 rows, page two came back with a full 200
    entries and `complete: true`. The walk stopped there and the page would
    have said "This is the start of the record" with twenty-five thousand rows
    still behind it. That is the exact sentence this whole feature exists to
    make true, said falsely.

    The cause: the LabCore path asks for one row more than it will show and
    reports the extra as `truncated`. The mirror path was handed rows and told
    `log_cut = False` unconditionally, so a mirrored page could never report
    itself short. The boundary case — a page holding exactly `limit` rows — is
    the one that matters, and it is the one that is easiest to get wrong in
    both directions.
    """

    def _client(self, gw, tmp_path, fill=True):
        app = create_app(gw, secret="t", documents_root=str(tmp_path))
        app.config.update(TESTING=True)
        if fill:
            app.config["LOG_MIRROR"].refresh()
        c = app.test_client()
        with c.session_transaction() as s:
            s["user"] = "ryan"
        return c

    def test_a_full_mirrored_page_does_not_claim_to_be_the_last(self, gw,
                                                               tmp_path):
        _many(gw, 500)
        c = self._client(gw, tmp_path)
        first = c.get("/api/equipment/%s/history?limit=100" % UID).get_json()
        page = c.get("/api/equipment/%s/history?limit=100&before=%s"
                     % (UID, first["entries"][-1]["at"])).get_json()
        assert page["source"] == "mirror", page.get("source")
        assert len(page["entries"]) == 100
        assert page["complete"] is False, (
            "a full page said it was the start of the record")

    def test_the_genuinely_last_mirrored_page_does(self, gw, tmp_path):
        _many(gw, 150)
        c = self._client(gw, tmp_path)
        first = c.get("/api/equipment/%s/history?limit=100" % UID).get_json()
        page = c.get("/api/equipment/%s/history?limit=100&before=%s"
                     % (UID, first["entries"][-1]["at"])).get_json()
        assert len(page["entries"]) == 50
        assert page["complete"] is True

    def test_a_page_holding_exactly_the_limit_and_nothing_more(self, gw,
                                                              tmp_path):
        """The boundary. 200 rows read 100 at a time ends on a page that is
        full AND last, and only asking for one more row can tell them apart."""
        _many(gw, 200)
        c = self._client(gw, tmp_path)
        first = c.get("/api/equipment/%s/history?limit=100" % UID).get_json()
        page = c.get("/api/equipment/%s/history?limit=100&before=%s"
                     % (UID, first["entries"][-1]["at"])).get_json()
        assert len(page["entries"]) == 100
        assert page["complete"] is True, (
            "the last page was full, so it was reported as having more behind "
            "it; the walk never ends and the button never retires")

    def test_the_walk_still_reaches_every_row_through_the_mirror(self, gw,
                                                                tmp_path):
        _many(gw, 450)
        c, seen, cursor = self._client(gw, tmp_path), [], None
        for _ in range(20):
            url = "/api/equipment/%s/history?limit=100" % UID
            if cursor:
                url += "&before=%s" % cursor
            body = c.get(url).get_json()
            seen.extend(e["at"] for e in body["entries"])
            if body.get("complete"):
                break
            cursor = body["entries"][-1]["at"]
        assert len(seen) == 450 and len(set(seen)) == 450, len(seen)


class TestTheWalkLosesNothingToASharedTimestamp:
    """Found against the live lab, and it is the worst kind of bug this feature
    could have had: the walk reported reaching the start of the record having
    silently skipped a sixth of it.

        limit=all                       26,107 entries
        walked in pages of 200          21,854 entries, complete: true

    The cursor was a bare `ts` and the page asked for rows STRICTLY older, so
    every row sharing the last second of a page was stepped over — and
    `lem_machine_log` is full of shared seconds, because `_audit` stamps to
    whole seconds and an instrument reporting five cuts off one injection
    writes five rows in the same instant. CLAUDE.md already records this for
    the reporting queries, which is why two of them say `ORDER BY ts, rowid`.

    Nothing on screen could have shown it. The pages were full, the order was
    right, the count went up, and the walk ended saying it had reached the
    beginning. The only way to see it is to compare against a read that does
    not page — which is what these tests do.
    """

    def _client(self, gw, tmp_path):
        app = create_app(gw, secret="t", documents_root=str(tmp_path))
        app.config.update(TESTING=True)
        app.config["LOG_MIRROR"].refresh()
        c = app.test_client()
        with c.session_transaction() as s:
            s["user"] = "ryan"
        return c

    @staticmethod
    def _same_second(gw, n, per_second=7):
        """Rows crowded onto few timestamps, the way a real log looks."""
        for i in range(n):
            _log(gw, "2026-08-05T09:%02d:00" % (i // per_second))

    def test_a_walk_finds_exactly_what_limit_all_finds(self, gw, tmp_path):
        self._same_second(gw, 420)
        c = self._client(gw, tmp_path)
        whole = c.get("/api/equipment/%s/history?limit=all" % UID).get_json()

        seen, cursor = [], None
        for _ in range(50):
            url = "/api/equipment/%s/history?limit=100" % UID
            if cursor:
                url += "&before=%s" % cursor
            body = c.get(url).get_json()
            seen.extend(body["entries"])
            if body.get("complete"):
                break
            cursor = body.get("next_before") or body["entries"][-1]["at"]
        assert len(seen) == len(whole["entries"]), (
            "the walk found %d of %d — rows sharing a second were stepped over"
            % (len(seen), len(whole["entries"])))

    def test_and_shows_none_of_them_twice(self, gw, tmp_path):
        """The other way to get a cursor wrong. `ts <=` would repeat instead of
        skipping, which is just as untrue and much easier to notice."""
        self._same_second(gw, 420)
        c = self._client(gw, tmp_path)
        seen, cursor = [], None
        for _ in range(50):
            url = "/api/equipment/%s/history?limit=100" % UID
            if cursor:
                url += "&before=%s" % cursor
            body = c.get(url).get_json()
            seen.extend((e["at"], e.get("summary"), e.get("kind"))
                        for e in body["entries"])
            if body.get("complete"):
                break
            cursor = body.get("next_before") or body["entries"][-1]["at"]
        assert len(seen) == 420, len(seen)

    def test_every_row_of_one_crowded_second_is_reached(self, gw, tmp_path):
        """A whole page's worth of rows on ONE timestamp — the boundary must
        fall inside that second and come back to it."""
        for _ in range(250):
            _log(gw, "2026-08-06T11:00:00")
        c = self._client(gw, tmp_path)
        seen, cursor = 0, None
        for _ in range(20):
            url = "/api/equipment/%s/history?limit=100" % UID
            if cursor:
                url += "&before=%s" % cursor
            body = c.get(url).get_json()
            seen += len(body["entries"])
            if body.get("complete"):
                break
            nxt = body.get("next_before")
            assert nxt, "no cursor that can step inside a shared second"
            assert nxt != cursor, "the walk stopped making progress"
            cursor = nxt
        assert seen == 250, seen
