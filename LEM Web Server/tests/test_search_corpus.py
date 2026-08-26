"""What `/api/search` can actually see, and what "not found" is allowed to mean.

The floor's snapshot carries the newest `EVENT_LIMIT` (60) rows of
`lem_machine_log` — the right size for the activity feed it was built for, and
the wrong size for the feature Ryan asked for. Measured on the demo floor, which
has 77 events, HALF the Lab IDs in the log came back `no_match`:

    L-37006 -> no_match      L-37053 -> ok
    L-37023 -> no_match      L-37065 -> ok
    L-37058 -> no_match      L-37080 -> ok

On screen `no_match` reads as *that sample does not exist*. A search box that
confidently denies a record the laboratory holds is worse than no search box,
and this is the one an assessor types into during a PJLA assessment.

Two things are held here, and the second matters as much as the first:

1. **The corpus is wide enough to answer the question.** A Lab ID that is in
   the log is findable, not merely one from the last hour.
2. **A bound that binds is reported.** The corpus is still finite. At its
   ceiling, or before its first read lands, "no such sample" really means "not
   in what I can see" — and the answer has to say so, because silent truncation
   reading as "I searched everything" is the failure this codebase names
   repeatedly.

The corpus deliberately is NOT a snapshot arm: every arm is bought with the
whole floor's two-second poll, and twenty thousand rows on that path would be
paid for by every open screen forever. It rides the poller's own thread on a
slower clock — one read for the building, however many people are typing.
"""

import json

import pytest

import snapshot_service
from labcore_gateway import FakeLabCoreGateway
from web_app import create_app


def _log_row(gw, machine_uid, ts, lab_id, test_name="Flash Point", value="63.7"):
    gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
           "test_name, value, detail) VALUES (?, ?, 'qc', ?, ?, ?, ?)",
           [machine_uid, ts, lab_id, test_name, value,
            json.dumps({"low": 61.6, "high": 65.8, "in_spec": True})])


@pytest.fixture
def lab():
    """One instrument and a hundred QC runs — comfortably past EVENT_LIMIT."""
    gw = FakeLabCoreGateway()
    snapshot_service.SnapshotService(gw).ensure_schema()
    gw.sql("INSERT INTO lem_machine_status (machine_uid, title, status, "
           "reason, updated_at) VALUES ('pac-flash-1', 'PAC Flash 1', "
           "'GREEN', '', '2026-08-26T09:00:00')")
    for n in range(100):
        _log_row(gw, "pac-flash-1",
                 "2026-06-%02dT%02d:00:00" % (n // 24 + 1, n % 24),
                 "L-%05d" % (37000 + n))
    return gw


def _client(gw):
    app = create_app(gw, secret="t")
    app.config.update(TESTING=True)
    return app.test_client()


def _search(client, query):
    return client.get("/api/search?q=%s" % query).get_json()


class TestTheOldestSampleIsStillFindable:
    def test_the_very_first_lab_id_is_found(self, lab):
        """`L-37000` is the OLDEST of a hundred runs — far outside the sixty
        rows the floor's snapshot carries. This is the test that fails against
        the snapshot-only corpus."""
        client = _client(lab)
        answer = _search(client, "L-37000")
        assert answer["state"] == "ok", answer
        assert any(hit["label"] == "L-37000" for hit in answer["results"])

    def test_every_lab_id_in_the_log_is_findable(self, lab):
        """Not a spot check: a search that finds the recent half and denies the
        rest is exactly the bug, and a single spot check can pass over it."""
        client = _client(lab)
        missing = [n for n in range(100)
                   if _search(client, "L-%05d" % (37000 + n))["state"] != "ok"]
        assert missing == [], "unfindable: %s" % ["L-%05d" % (37000 + n)
                                                  for n in missing[:10]]

    def test_a_lab_id_that_was_never_run_is_still_no_match(self, lab):
        """The other direction. A corpus wide enough to find everything is also
        wide enough to invent nothing — `no_match` has to stay reachable or the
        fix has simply made the box answer yes."""
        assert _search(_client(lab), "L-99999")["state"] == "no_match"


class TestWhatNotFoundIsAllowedToMean:
    def test_the_answer_says_how_much_it_searched(self, lab):
        answer = _search(_client(lab), "L-37000")
        corpus = answer["corpus"]
        assert corpus["rows"] >= 100
        assert corpus["truncated"] is False
        assert corpus["partial"] is False

    def test_a_capped_corpus_says_it_was_capped(self, lab, monkeypatch):
        """At the ceiling the oldest records really are absent, so "not found"
        means "not in the last N records". The caller must be able to tell.

        The cap is moved for real here rather than asserted around. An earlier
        draft of this test monkeypatched a name that did not exist and passed
        on the "or it found it" branch — a test that cannot fail, which is the
        exact thing three reviewers spent today finding in this codebase.
        """
        import web_app

        monkeypatch.setattr(web_app, "SEARCH_CORPUS_ROWS", 10)
        client = _client(lab)
        answer = _search(client, "L-37000")   # the oldest of a hundred

        assert answer["corpus"]["rows"] == 10
        assert answer["corpus"]["truncated"] is True
        # And the point of the flag: the sample IS in the lab's log, so a bare
        # "no_match" here would be a denial. The caller has what it needs to
        # say "not in the last 10 records" instead.
        assert answer["state"] == "no_match"

    def test_an_uncapped_corpus_does_not_claim_it_was_capped(self, lab):
        """The other half. A flag that is always True is worse than no flag."""
        answer = _search(_client(lab), "L-37000")
        assert answer["corpus"]["truncated"] is False
        assert answer["state"] == "ok"


class TestTheCorpusCostsTheRequestNothing:
    def test_typing_does_not_read_labcore(self, lab):
        """One LabCore read per keystroke per viewer is strictly worse than the
        load pattern `snapshot_service` exists to end. The corpus rides the
        poller; a request must never trigger it."""
        client = _client(lab)
        _search(client, "flash")          # warm the snapshot and the index

        reads = []
        original = lab.read_sql
        lab.read_sql = lambda sql, args=None, **kw: (
            reads.append(sql), original(sql, args, **kw))[1]
        for _ in range(20):
            _search(client, "L-37000")
        assert reads == [], reads

    def test_the_index_is_not_rebuilt_per_request(self, lab):
        """`build_index` is O(rows); rebuilding it per keystroke is the same
        mistake one layer up from the read."""
        import lab_search

        client = _client(lab)
        _search(client, "flash")

        builds = []
        original = lab_search.build_index

        def counted(*a, **kw):
            builds.append(1)
            return original(*a, **kw)

        lab_search.build_index = counted
        try:
            for _ in range(20):
                _search(client, "L-37000")
        finally:
            lab_search.build_index = original
        assert builds == [], "index rebuilt %d times" % len(builds)
