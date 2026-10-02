"""`StoreLogMirror`: the log mirror's API, answered by the store itself.

Transfer spec §5.3: "LogMirror is re-implemented over the store with the same
signatures." The old mirror copied LabCore's log into a local file every five
minutes because reading LabCore cost a queue slot; the store IS a local file,
so a copy would only be a second thing to be behind.

"Same signatures" is not enough on its own — every caller in `web_app`
(History's deep walk, the Logs search, the QC wall, the Lab ID lookup, the
floor's newest-verdict-per-standard) was written against the old mirror's
ANSWERS: newest first, ties broken by row id, the `ts|id` cursor, `%`/`_`
taken literally, a failure in a same-instant batch winning the tie. So these
tests fill one store, point BOTH implementations at it (the old one through
its normal pull), and require identical answers from every method. Then the
two deliberate differences: the store mirror hides what the effective view
hides, and it never claims to be part-filled.
"""

import json
import os

import pytest

from labcore_gateway import FakeLabCoreGateway
from labcore_result import LabCoreError
from log_mirror import LogMirror, StoreLogMirror


def _row(gw, uid, ts, kind="run", lab="", test="Flash Point", value="1",
         detail="{}"):
    res = gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
                 "test_name, value, detail) VALUES (?,?,?,?,?,?,?)",
                 [uid, ts, kind, lab, test, value, detail])
    assert "error" not in res, res


@pytest.fixture
def store():
    gw = FakeLabCoreGateway()
    # Same-second ties on purpose: the cursor and tie rules are the point.
    for i in range(30):
        _row(gw, "gc1" if i % 3 else "flash1",
             "2026-09-%02dT09:00:%02d" % (1 + i // 10, (i % 10) // 2),
             kind="qc" if i % 5 == 0 else "run", lab=str(38000 + i),
             value=str(i), detail=json.dumps({"in_spec": i % 7 != 0}))
    _row(gw, "gc1", "2026-09-04T10:00:00", lab="100%_odd", test="Cut_50%")
    # A batch at one instant with a failure in it (the Multitek NS shape).
    for v, ok in (("2.79", True), ("5.248", False), ("2.78", True)):
        _row(gw, "mt", "2026-09-03T17:05:14", kind="qc", lab="AF26",
             test="Sulfur", value=v, detail=json.dumps({"in_spec": ok}))
    return gw


@pytest.fixture
def both(store, tmp_path):
    old = LogMirror(store, path=str(tmp_path / "copy.sqlite3"))
    old.refresh()
    return old, StoreLogMirror(store)


def _same(a, b):
    assert a == b
    return a


class TestSameAnswersAsTheCopy:
    def test_events_whole_and_per_machine(self, both):
        old, new = both
        assert len(_same(old.events(), new.events())) == 34
        _same(old.events(machine_uid="gc1"), new.events(machine_uid="gc1"))
        _same(old.events(limit=7), new.events(limit=7))

    def test_walking_backwards_by_the_compound_cursor(self, both):
        old, new = both
        cursors = []
        for m in (old, new):
            seen, before = [], None
            while True:
                page = m.events(machine_uid="gc1", limit=4, before=before)
                if not page:
                    break
                seen += [r["rowid_src"] for r in page]
                before = "%s|%s" % (page[-1]["ts"], page[-1]["rowid_src"])
            cursors.append(seen)
        assert cursors[0] == cursors[1]
        assert len(cursors[0]) == len(set(cursors[0])) == 21

    def test_a_bare_timestamp_cursor_too(self, both):
        old, new = both
        _same(old.events(before="2026-09-02T09:00:02"),
              new.events(before="2026-09-02T09:00:02"))

    def test_by_lab_id(self, both):
        old, new = both
        assert _same(old.by_lab_id("38004"), new.by_lab_id("38004"))

    def test_search_and_its_literal_wildcards(self, both):
        old, new = both
        assert len(_same(old.search("380"), new.search("380"))) == 30
        assert len(_same(old.search("%_"), new.search("%_"))) == 1
        assert _same(old.search("  "), new.search("  ")) == []

    def test_query_with_every_filter(self, both):
        old, new = both
        for kw in ({"term": "Flash"}, {"machine_uid": "flash1"},
                   {"kind": "qc"}, {"since": "2026-09-02", "until": "2026-09-02"},
                   {"term": "in_spec", "kind": "qc", "limit": 3}):
            _same(old.query(**kw), new.query(**kw))

    def test_count_and_max_rowid(self, both):
        old, new = both
        assert _same(old.count(), new.count()) == 34
        _same(old.count("gc1"), new.count("gc1"))
        _same(old.max_rowid(), new.max_rowid())

    def test_latest_qc_including_the_failed_batch_rule(self, both):
        old, new = both
        got = _same(old.latest_qc(), new.latest_qc())
        assert got[("mt", "Sulfur", "AF26")]["value"] == "5.248"


class TestWhereItDiffersOnPurpose:
    def test_hidden_rows_are_hidden(self, store, both):
        _old, new = both
        # Hidden the only way the store allows: under an approval (D7).
        assert "error" not in store.sql(
            "INSERT INTO annotation_approval (machine_uid, rule, run_id, "
            "approved_by, approved_at, decision) SELECT DISTINCT machine_uid,"
            " 'replay_duplicate', 'test', 'test', 't', 'approved' "
            "FROM lem_machine_log_effective WHERE lab_id = '38004'")
        assert "error" not in store.sql(
            "INSERT INTO log_annotation (log_id, label, by, at, approval_id) "
            "SELECT l.id, 'replay_duplicate', 'test', 't', p.id "
            "FROM lem_machine_log_effective l JOIN annotation_approval p "
            "ON p.machine_uid = l.machine_uid WHERE l.lab_id = '38004'")
        assert new.by_lab_id("38004") == []
        assert new.count() == 33
        assert "38004" not in {r["lab_id"] for r in new.search("3800")}

    def test_it_is_never_part_filled(self, both):
        _old, new = both
        state = new.state()
        assert state["rows"] == 34 and state["filled_at"]
        assert state["stale_reason"] == "" and state["source"] == "store"
        assert new.refresh() == 0                       # nothing to pull

    def test_a_failed_read_raises_and_state_says_why(self, store):
        new = StoreLogMirror(store)
        real = store.read_sql
        store.read_sql = lambda s, a=None, **k: {"error": "disk I/O error"}
        try:
            with pytest.raises(LabCoreError):
                new.events()
            with pytest.raises(LabCoreError):
                new.latest_qc()
            state = new.state()
            # rows 0 sends every caller down its fallback, where the same
            # failure reaches the person — never "this lab has no history".
            assert state["rows"] == 0 and "disk I/O" in state["stale_reason"]
        finally:
            store.read_sql = real

    def test_an_answer_with_no_rows_key_is_not_an_empty_log(self, store):
        new = StoreLogMirror(store)
        store.read_sql = lambda s, a=None, **k: {"ok": True}
        with pytest.raises(LabCoreError):
            new.events()
