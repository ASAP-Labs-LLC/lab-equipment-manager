"""A local copy of `lem_machine_log`, so the whole record is readable.

Ryan: *"I dont like that the history is cut off … make it actually show the
entire database"*, then *"cant it pull it every 5 minutes? and just keep it
local?"* — which is the better design, and this is it.

**Why a mirror rather than a bigger LIMIT.** Measured against live LabCore on
27 Aug: the whole table is 41,903 rows and reads in 1.00 s / 18.9 MB; one
instrument's 26,106 rows take 2.23 s. Both are inside LabCore's 8 s interrupt,
so the database was never the constraint. But every one of those reads occupies
the queue that serialises the whole lab's WRITES at about 1.5 ops/sec, and the
History tab is opened by people, repeatedly, from several screens. Paying it
once every five minutes instead of once per click is the difference between a
feature and a load problem.

**The pull is incremental and exact.** `rowid` is visible through the gateway
and monotonic for appends (checked: max 41,903, descending order intact), so
`WHERE rowid > ?` picks up exactly what is new. Deliberately NOT `ts >`:
`_audit` stamps to whole seconds, ties are real and documented in CLAUDE.md
(two reporting queries say `ORDER BY ts, rowid` because of it), and a
timestamp cursor silently drops every row sharing the last second it saw.

**It is a cache, never the authority.** CLAUDE.md's rule that nothing is
measured "from local disk" is about where a VERDICT comes from; LabCore stays
the record and the mirror is deletable at any moment. RELEASING.md already
calls `data/` regenerable cache, which is where this lives.

The tests below are mostly about the two ways a cache lies: reporting a partial
copy as the whole record, and shrinking or emptying when a pull fails.
"""

import pytest

import snapshot_service
from labcore_gateway import FakeLabCoreGateway
from log_mirror import LogMirror

UID = "agilent-gc-1"
OTHER = "optimpp-1"


def _log(gw, ts, uid=UID, kind="run"):
    gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
           "test_name, value, detail) VALUES (?, ?, ?, '', 'Flash Point', "
           "'1.0', '{}')", [uid, ts, kind])


def _many(gw, n, uid=UID, start=0):
    for i in range(start, start + n):
        _log(gw, "2026-08-%02dT%02d:%02d:00"
             % (1 + i // 1440, (i // 60) % 24, i % 60), uid=uid)


@pytest.fixture
def gw():
    g = FakeLabCoreGateway()
    snapshot_service.SnapshotService(g).ensure_schema()
    return g


@pytest.fixture
def mirror(gw, tmp_path):
    return LogMirror(gw, path=str(tmp_path / "mirror.sqlite3"))


class TestItCopiesTheWholeTable:
    def test_a_first_refresh_takes_everything(self, gw, mirror):
        _many(gw, 250)
        assert mirror.refresh() == 250
        assert mirror.state()["rows"] == 250

    def test_and_the_rows_come_back_as_they_went_in(self, gw, mirror):
        _log(gw, "2026-08-01T09:00:00")
        mirror.refresh()
        row = mirror.events(machine_uid=UID)[0]
        assert row["machine_uid"] == UID
        assert row["ts"] == "2026-08-01T09:00:00"
        assert row["kind"] == "run"
        assert row["test_name"] == "Flash Point"

    def test_an_empty_lab_mirrors_to_an_empty_mirror(self, mirror):
        assert mirror.refresh() == 0
        assert mirror.state()["rows"] == 0


class TestTheSecondPullIsIncremental:
    def test_only_new_rows_are_fetched(self, gw, mirror):
        _many(gw, 100)
        mirror.refresh()
        _many(gw, 5, start=100)
        assert mirror.refresh() == 5
        assert mirror.state()["rows"] == 105

    def test_a_refresh_with_nothing_new_fetches_nothing(self, gw, mirror):
        _many(gw, 40)
        mirror.refresh()
        assert mirror.refresh() == 0
        assert mirror.state()["rows"] == 40

    def test_rows_sharing_a_timestamp_are_not_dropped(self, gw, mirror):
        """The reason the cursor is `rowid` and not `ts`.

        `_audit` stamps to whole seconds, so several rows landing in the same
        second is ordinary rather than exotic. A `ts >` cursor keeps the newest
        of them and silently loses the rest — a hole in the record that nothing
        would ever report, because the counts still look right."""
        for _ in range(5):
            _log(gw, "2026-08-02T10:00:00")
        mirror.refresh()
        for _ in range(5):
            _log(gw, "2026-08-02T10:00:00")     # the SAME second, again
        assert mirror.refresh() == 5
        assert mirror.state()["rows"] == 10

    def test_it_does_not_duplicate_a_row_it_already_holds(self, gw, mirror):
        _many(gw, 30)
        mirror.refresh()
        mirror.refresh()
        mirror.refresh()
        assert mirror.state()["rows"] == 30


class TestAFailedPullNeverShrinksTheMirror:
    """The failure mode that matters. A cache that empties itself on a blip
    reports a lab with no history, which is a statement about the record."""

    def test_a_refused_read_leaves_what_was_already_held(self, gw, mirror):
        _many(gw, 120)
        mirror.refresh()

        def refusing(sql, args=None, **kw):
            return {"error": "LabCore is busy", "busy": True, "retry_after": 5}

        gw.read_sql = refusing
        with pytest.raises(Exception):
            mirror.refresh()
        assert mirror.state()["rows"] == 120

    def test_and_the_failure_is_reported_rather_than_swallowed(self, gw,
                                                              mirror):
        """A mirror that quietly stops updating is worse than one that stops
        loudly: the rows on screen still look current."""
        _many(gw, 10)
        mirror.refresh()

        def refusing(sql, args=None, **kw):
            return {"error": "LabCore is busy", "busy": True}

        gw.read_sql = refusing
        with pytest.raises(Exception):
            mirror.refresh()
        assert mirror.state()["stale_reason"]

    def test_a_recovered_pull_clears_the_complaint(self, gw, mirror):
        _many(gw, 10)
        mirror.refresh()
        broken = {"n": 0}
        real = gw.read_sql

        def sometimes(sql, args=None, **kw):
            if broken["n"] == 0:
                broken["n"] = 1
                return {"error": "busy", "busy": True}
            return real(sql, args, **kw)

        gw.read_sql = sometimes
        with pytest.raises(Exception):
            mirror.refresh()
        mirror.refresh()
        assert not mirror.state()["stale_reason"]


class TestItSurvivesARestart:
    """The point of a file rather than a dict: a web server restart must not
    cost a full 19 MB re-read of LabCore."""

    def test_a_new_mirror_on_the_same_file_keeps_the_rows(self, gw, tmp_path):
        path = str(tmp_path / "m.sqlite3")
        _many(gw, 200)
        LogMirror(gw, path=path).refresh()
        again = LogMirror(gw, path=path)
        assert again.state()["rows"] == 200

    def test_and_only_pulls_what_arrived_while_it_was_down(self, gw, tmp_path):
        path = str(tmp_path / "m.sqlite3")
        _many(gw, 200)
        LogMirror(gw, path=path).refresh()
        _many(gw, 3, start=200)
        assert LogMirror(gw, path=path).refresh() == 3


class TestReadingItBack:
    def test_newest_first(self, gw, mirror):
        _many(gw, 50)
        mirror.refresh()
        got = [e["ts"] for e in mirror.events(machine_uid=UID)]
        assert got == sorted(got, reverse=True)

    def test_one_instrument_only(self, gw, mirror):
        _many(gw, 20, uid=UID)
        _many(gw, 20, uid=OTHER)
        mirror.refresh()
        got = mirror.events(machine_uid=UID)
        assert got and all(e["machine_uid"] == UID for e in got)

    def test_the_whole_lab_when_no_instrument_is_named(self, gw, mirror):
        _many(gw, 20, uid=UID)
        _many(gw, 20, uid=OTHER)
        mirror.refresh()
        assert len(mirror.events()) == 40

    def test_before_walks_backwards_without_repeating_a_row(self, gw, mirror):
        _many(gw, 300)
        mirror.refresh()
        first = mirror.events(machine_uid=UID, limit=100)
        cursor = first[-1]["ts"]
        second = mirror.events(machine_uid=UID, limit=100, before=cursor)
        assert second
        assert all(e["ts"] < cursor for e in second)

    def test_a_walk_reaches_every_row_exactly_once(self, gw, mirror):
        _many(gw, 250)
        mirror.refresh()
        seen, cursor = [], None
        while True:
            page = mirror.events(machine_uid=UID, limit=100, before=cursor)
            if not page:
                break
            seen.extend(e["ts"] for e in page)
            cursor = page[-1]["ts"]
        assert len(seen) == 250 and len(set(seen)) == 250

    def test_no_limit_means_everything(self, gw, mirror):
        _many(gw, 1200)
        mirror.refresh()
        assert len(mirror.events(machine_uid=UID)) == 1200


class TestItKnowsWhetherItIsTheWholeRecord:
    """"That is everything" is the sentence this exists to be able to say, so
    it has to be earned rather than assumed."""

    def test_a_mirror_that_has_never_pulled_says_so(self, mirror):
        assert mirror.state()["filled_at"] is None

    def test_a_filled_mirror_says_when(self, gw, mirror):
        _many(gw, 5)
        mirror.refresh()
        assert mirror.state()["filled_at"]

    def test_it_reports_how_far_behind_labcore_it_may_be(self, gw, mirror):
        _many(gw, 5)
        mirror.refresh()
        st = mirror.state()
        assert st["max_rowid"] > 0
        assert st["rows"] == 5
