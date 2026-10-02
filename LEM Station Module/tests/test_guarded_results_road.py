"""The guarded results road (transfer v4 §8): never overwrite a cell a person
changed, and never send a cell twice when once was enough.

What this road used to do, measured on the real v3.9.0 module by the gate:

  * A1 — an analyst corrects five filed cells, LabStation restarts, the file is
    read again from the top: all five corrections are overwritten. A3 — the
    analyst types a cell while the bench is holding the reading for its sample:
    the bench files over the analyst the moment the sample appears. In both the
    bench never LOOKED at the cell before writing it.
  * K4 / N4 — the batch landed and the bench never heard so: every cell is sent
    again (33 and 3 wasted sends).
  * F5 — LabCore refuses writes for half an hour: 10,100 cell sends, and 400
    readings lost to the retry cap.
  * B1 — LabCore answers `ok` with one sub-operation's error inside `results`;
    the bench read only `ok` and called the cell filed. It was not.

The road now reads before it writes — ONE read that answers both "which sample
is this?" and "what is in the cell now?" — and decides each cell from that read
and from LEM's own ledger of what it filed (§8.3):

    cell empty / no row          write
    cell == this reading         no write: it landed earlier          (filed)
    cell == LEM's last filing    write: a re-run supersedes LEM's own value
    anything else                no write: a person changed it        (conflict)

Ryan's decision D1: a genuine re-sent reading that hits a cell a person edited
becomes a CONFLICT shown in LEM. It is never overwritten.

Each test says which of those failures it pins.
"""
import ast
import inspect
import json
import os
import sqlite3
from datetime import datetime, timedelta

import pytest

import lem_station_module as mod
from lem_station_module import JOURNAL_KEY, LAB_ID_KEY, Machine

from test_module_qt import make_module

NOW = datetime(2026, 10, 1, 9, 0, 0)
UID = "b1"


def row(lab_id, when=NOW, **values):
    r = {LAB_ID_KEY: lab_id,
         "parsed_date": when.strftime("%Y-%m-%d"),
         "parsed_time": when.strftime("%H:%M:%S")}
    r.update(values)
    return r


class LabCore:
    """LabCore's results road, in sqlite: `samples`, `sample_tests` with
    `updated_at` and `operator` (LabCore_main._batch_update_cell writes both),
    and `batch` answering exactly as `_wop_batch` does — `{"ok": true,
    "results": [{"index": i, ...}]}`, `ok` even when one sub-operation failed.

    Every call is recorded, so a test can count LabCore operations the way the
    gate does, and can see every parameter the bench sent."""

    def __init__(self, samples=()):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE "samples" (lab_id TEXT PRIMARY KEY)')
        self.db.execute(
            "CREATE TABLE sample_tests (lab_id TEXT NOT NULL, test_name TEXT "
            "NOT NULL, result TEXT, updated_at TEXT, operator TEXT, "
            "PRIMARY KEY (lab_id, test_name))")
        # The legacy held-results mirror a first poll reads back once.
        self.db.execute("CREATE TABLE lem_held_results (machine_uid TEXT "
                        "PRIMARY KEY, updated_at TEXT, held TEXT)")
        for s in samples:
            self.db.execute('INSERT INTO "samples" VALUES (?)', [s])
        self.reads = []          # (sql, args, kwargs)
        self.batches = []        # (operations, kwargs)
        self.refuse_reads = 0
        self.refuse_writes = 0
        self.raise_after_batch = 0
        self.index_errors = {}   # (lab, test) -> error string, every time
        self.before_batch = None  # callable run after the bench's read

    # ── a person at LabEntry ──
    def analyst(self, lab, test, value, who="kim"):
        self.db.execute(
            "INSERT INTO sample_tests VALUES (?,?,?,?,?) ON CONFLICT(lab_id, "
            "test_name) DO UPDATE SET result=excluded.result, updated_at="
            "excluded.updated_at, operator=excluded.operator",
            [lab, test, value, "2026-10-01 09:05:00", who])

    def cell(self, lab, test):
        r = self.db.execute("SELECT result FROM sample_tests WHERE lab_id=? "
                            "AND test_name=?", [lab, test]).fetchone()
        return None if r is None else r["result"]

    # ── the injected helpers ──
    def read_sql(self, sql, args=None, **kw):
        self.reads.append((sql, list(args or []), dict(kw)))
        if self.refuse_reads:
            self.refuse_reads -= 1
            return {"error": "LabCore is busy — write queue is deep.",
                    "busy": True, "retry_after": 5}
        try:
            rows = [dict(r) for r in self.db.execute(sql, list(args or []))]
        except sqlite3.Error as exc:
            return {"error": str(exc)}
        return {"ok": True, "rows": rows}

    def sql(self, sql, args=None, **kw):
        return {"ok": True}

    def write(self, operation, params=None, source=""):
        ops = (params or {}).get("operations") or []
        self.batches.append((ops, {"source": source}))
        if self.refuse_writes:
            self.refuse_writes -= 1
            return {"error": "LabCore is busy — write queue is deep.",
                    "busy": True, "retry_after": 5}
        if self.before_batch is not None:
            hook, self.before_batch = self.before_batch, None
            hook()
        results = []
        for i, o in enumerate(ops):
            p = o.get("params") or {}
            key = (p.get("lab_id"), p.get("test_name"))
            if key in self.index_errors:
                results.append({"index": i, "error": self.index_errors[key]})
                continue
            self.db.execute(
                "INSERT INTO sample_tests VALUES (?,?,?,?,?) ON CONFLICT(lab_id,"
                " test_name) DO UPDATE SET result=excluded.result, updated_at="
                "excluded.updated_at, operator=excluded.operator",
                [p["lab_id"], p["test_name"], p["value"],
                 "2026-10-01 09:00:00", p.get("operator") or ""])
            results.append({"index": i, "ok": True})
        if self.raise_after_batch:
            self.raise_after_batch -= 1
            raise TimeoutError("read timed out (the batch landed)")
        return {"ok": True, "results": results}

    def sent(self):
        return [(o["params"]["lab_id"], o["params"]["test_name"],
                 o["params"]["value"])
                for ops, _kw in self.batches for o in ops]


@pytest.fixture
def road(qapp, monkeypatch, tmp_path):
    """A bench with a journal (in tmp) wired to `LabCore`."""
    monkeypatch.setenv("LEM_JOURNAL_DIR", str(tmp_path / "journal"))
    made = []

    def build(labcore, uid=UID):
        module = make_module()
        monkeypatch.setitem(mod.__dict__, "labcore_write", labcore.write)
        monkeypatch.setitem(mod.__dict__, "labcore_sql", labcore.sql)
        monkeypatch.setitem(mod.__dict__, "labcore_read_sql", labcore.read_sql)
        module._machine = Machine(uid=uid, title="Densimeter")
        made.append(module)
        return module

    yield build
    for module in made:
        module._release_journals()


def journaled(module, *rows):
    """Journal readings exactly as a poll does, so they carry their record."""
    journal = module._journal_for(module._machine)
    refs = journal.append([{"kind": "run", "lab_id": r[LAB_ID_KEY],
                            "row": dict(r), "log": []} for r in rows])
    for r, ref in zip(rows, refs):
        r[JOURNAL_KEY] = ref
    return list(rows)


def poll(module, labcore, rows=(), now=NOW):
    messages = []
    out = module._store_results(module._machine, list(rows), labcore.read_sql,
                                labcore.sql, labcore.write, messages, now)
    return out, messages


def records(module, kind):
    journal = module._journal_for(module._machine)
    return [r for r in journal._scan() if r.get("kind") == kind]


# ── the decision, as a pure function ────────────────────────────────────────

class TestTheDecisionTable:
    """§8.3, one row each. `cur` is what the guard read found in the cell, `L`
    what LEM itself last filed there."""

    def test_an_empty_cell_is_written(self):
        for cur in ([], [{"result": None}], [{"result": ""}]):
            assert mod.decide_cell(cur, "0.8000", None)[0] == "write"

    def test_a_cell_already_holding_the_reading_is_not_written_again(self):
        """K4 and N4: the batch landed and the bench never heard. Sending it
        again re-stamps updated_at and costs a queue slot for nothing."""
        assert mod.decide_cell([{"result": "0.8000"}], "0.8000", None)[0] == "landed"
        assert mod.decide_cell([{"result": "0.80"}], "0.8000", None)[0] == "landed", (
            "the same number in a different spelling is the same reading")

    def test_a_cell_holding_lems_own_last_value_is_superseded(self):
        """A4 / R6: the instrument re-ran the sample. The cell holds what LEM
        filed, so nobody has touched it since, and the new reading replaces
        LEM's own value."""
        verdict, expect = mod.decide_cell([{"result": "0.8000"}], "0.8888",
                                          "0.8000")
        assert (verdict, expect) == ("write", "0.8000")

    def test_anything_else_is_a_person_and_is_never_overwritten(self):
        """D1. The cell holds a value LEM did not file — an analyst's
        correction, or a value typed while the bench was holding the reading
        (A3, where LEM never filed anything so there is no `L` at all)."""
        assert mod.decide_cell([{"result": "0.7000"}], "0.8000", "0.8000")[0] \
            == "conflict"
        assert mod.decide_cell([{"result": "0.7000"}], "0.8000", None)[0] \
            == "conflict"

    def test_two_different_values_in_one_cell_is_not_something_to_guess_at(self):
        assert mod.decide_cell([{"result": "0.7"}, {"result": "0.8"}], "0.8",
                               "0.8")[0] == "conflict"


class TestBackoff:
    def test_the_wait_doubles_from_thirty_seconds_and_stops_at_five_minutes(self):
        assert [mod.road_backoff_seconds(n) for n in range(1, 7)] == \
            [30, 60, 120, 240, 300, 300]

    def test_labcores_own_retry_after_wins_when_it_is_longer(self):
        assert mod.road_backoff_seconds(1, retry_after=90) == 90
        assert mod.road_backoff_seconds(1, retry_after=5) == 30


# ── one read, before every filing ───────────────────────────────────────────

class TestOneReadBeforeEveryFiling:
    def test_a_filing_poll_costs_one_read_and_one_batch(self, road):
        """1g: the guard must not add a read. The identity question and the
        cell question are one query, so a filing poll is exactly what it was
        before the guard existed — one read, one write."""
        lc = LabCore(samples=["S1", "S2"])
        module = road(lc)
        # The first poll of a module's life also reads the legacy held-results
        # mirror back, once; that is not the filing's cost.
        poll(module, lc)
        lc.reads.clear()
        poll(module, lc, journaled(module, row("S1", Density="0.8001"),
                                   row("S2", Density="0.8002")),
             now=NOW + timedelta(seconds=30))
        assert len(lc.reads) == 1 and len(lc.batches) == 1
        assert sorted(lc.sent()) == [("S1", "Density", "0.8001"),
                                     ("S2", "Density", "0.8002")]

    def test_a_cached_identity_is_guarded_with_a_primary_key_lookup(self, road):
        """A dated Lab ID, once proved, is never re-asked (the identity cache).
        Its cell still has to be read before it is written, and that read must
        not scan `samples`: it is a lookup on sample_tests' own key."""
        lc = LabCore(samples=["081126-34566"])
        module = road(lc)
        poll(module, lc, journaled(module, row("34566", Density="0.8001")))
        lc.reads.clear()
        poll(module, lc, journaled(module, row("34566", Density="0.8002")),
             now=NOW + timedelta(seconds=30))
        assert len(lc.reads) == 1
        sql = lc.reads[0][0]
        assert '"samples"' not in sql and "sample_tests" in sql
        assert lc.cell("081126-34566", "Density") == "0.8002"

    def test_no_read_on_this_road_carries_a_source(self, road):
        """§8.2 / A.5: LabCore queues a read that names a source behind the
        write queue. Every read the results road makes goes without one."""
        lc = LabCore(samples=["S1"])
        module = road(lc)
        poll(module, lc, journaled(module, row("S1", Density="0.8001")))
        assert lc.reads and all("source" not in kw for _s, _a, kw in lc.reads)

    def test_and_no_read_anywhere_in_the_module_is_written_with_one(self):
        """The same rule as a grep over the source, so a read added later on
        another road cannot quietly bring `source=` back."""
        tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", "")
                if name == "read_sql" and any(k.arg == "source" for k in node.keywords):
                    bad.append(node.lineno)
        assert bad == []

    def test_a_failed_read_is_not_an_empty_cell(self, road):
        """The rule this codebase is built around. A refused read must not be
        mistaken for "the cell is empty, write it": nothing is sent, and the
        reading stays in the bench's custody."""
        lc = LabCore(samples=["S1"])
        module = road(lc)
        poll(module, lc)                      # the once-a-life mirror read
        lc.analyst("S1", "Density", "0.7000")
        lc.refuse_reads = 1
        rows = journaled(module, row("S1", Density="0.8001"))
        poll(module, lc, rows)
        assert lc.batches == []
        assert any(r.get(JOURNAL_KEY) == rows[0][JOURNAL_KEY]
                   for r in module._held_rows + module._identity_backlog)
        assert lc.cell("S1", "Density") == "0.7000"


# ── the guard ───────────────────────────────────────────────────────────────

class TestAPersonsCellIsNeverOverwritten:
    def test_a3_the_analyst_typed_the_cell_while_the_bench_held_the_reading(self, road):
        """A3, today 3 of 3 overwritten. The reading waits for its sample; the
        sample is logged in and the analyst types the result; then the bench
        finds the sample. LEM never filed this cell, the cell is not empty, so
        it is somebody's: a conflict, recorded and said, and not a write."""
        lc = LabCore(samples=[])
        module = road(lc)
        rows = journaled(module, row("S9", Density="0.8001"))
        poll(module, lc, rows)                       # held: no sample yet
        lc.db.execute('INSERT INTO "samples" VALUES (?)', ["S9"])
        lc.analyst("S9", "Density", "0.6000")
        out, messages = poll(module, lc, now=NOW + timedelta(minutes=2))
        assert lc.sent() == []
        assert lc.cell("S9", "Density") == "0.6000"
        conflicts = records(module, "conflict")
        assert len(conflicts) == 1
        assert conflicts[0]["of"] == [rows[0][JOURNAL_KEY]]
        lab, test, ours, theirs, at, who = conflicts[0]["cells"][0]
        assert (lab, test, ours, theirs, who) == ("S9", "Density", "0.8001",
                                                  "0.6000", "kim")
        assert any("decision" in m for m in messages), messages

    def test_a_conflict_is_decided_once_not_every_poll(self, road):
        """A conflict settles the reading: it is a deliberate state waiting for
        a person, not a retry. Re-deciding it every poll would journal a fresh
        conflict every twelve seconds."""
        lc = LabCore(samples=["S9"])
        lc.analyst("S9", "Density", "0.6000")
        module = road(lc)
        poll(module, lc, journaled(module, row("S9", Density="0.8001")))
        for k in range(1, 4):
            poll(module, lc, now=NOW + timedelta(seconds=30 * k))
        assert len(records(module, "conflict")) == 1
        assert lc.sent() == []

    def test_a1_a_replay_over_a_correction_is_a_conflict(self, road):
        """A1's shape, at the road: LEM filed 0.8001, the analyst corrected it
        to 0.7001, and the same reading comes round again (a restart re-read
        it). The cell is neither empty, nor this reading, nor LEM's own value."""
        lc = LabCore(samples=["S1"])
        module = road(lc)
        poll(module, lc, journaled(module, row("S1", Density="0.8001")))
        lc.analyst("S1", "Density", "0.7001")
        poll(module, lc, journaled(module, row("S1", Density="0.8001")),
             now=NOW + timedelta(seconds=30))
        assert lc.cell("S1", "Density") == "0.7001"
        assert len(lc.sent()) == 1
        assert len(records(module, "conflict")) == 1

    def test_a4_a_rerun_over_lems_own_value_lands(self, road):
        lc = LabCore(samples=["S1"])
        module = road(lc)
        poll(module, lc, journaled(module, row("S1", Density="0.8001")))
        poll(module, lc, journaled(module, row("S1", Density="0.8888")),
             now=NOW + timedelta(seconds=30))
        assert lc.cell("S1", "Density") == "0.8888"
        assert records(module, "conflict") == []

    def test_the_ledger_is_rebuilt_from_filed_records_after_a_restart(self, road):
        """`L` is not memory. A re-run after LabStation restarted must still
        recognise LEM's own earlier value — otherwise every re-run after a
        restart would be reported as a conflict with ourselves."""
        lc = LabCore(samples=["S1"])
        first = road(lc)
        poll(first, lc, journaled(first, row("S1", Density="0.8001")))
        first._release_journals()
        second = road(lc)
        poll(second, lc, journaled(second, row("S1", Density="0.8888")),
             now=NOW + timedelta(minutes=5))
        assert lc.cell("S1", "Density") == "0.8888"
        assert records(second, "conflict") == []

    def test_two_readings_of_one_cell_in_one_poll_both_count(self, road):
        """The second print of the same sample in one poll is a re-run of the
        first: it supersedes LEM's own (just-planned) value, it is not a
        conflict with it."""
        lc = LabCore(samples=["S1"])
        module = road(lc)
        poll(module, lc, journaled(module, row("S1", Density="0.8001"),
                                   row("S1", Density="0.8002")))
        assert lc.cell("S1", "Density") == "0.8002"
        assert records(module, "conflict") == []


class TestWhatTheBatchCarries:
    def test_expect_and_source_ride_on_every_update_cell(self, road):
        """`expect` is what the guard read saw. Today's LabCore ignores it; it
        is the evidence for the A5 audit (an edit that lands between the read
        and the batch) and it is ready for compare-and-set. `source` names the
        bench, and LabCore already writes it into result history."""
        lc = LabCore(samples=["S1", "S2"])
        module = road(lc)
        poll(module, lc, journaled(module, row("S1", Density="0.8001")))
        poll(module, lc, journaled(module, row("S1", Density="0.8888"),
                                   row("S2", Density="0.8002")),
             now=NOW + timedelta(seconds=30))
        params = [o["params"] for ops, _ in lc.batches for o in ops]
        assert all(p["source"] == "LEM Station:" + UID for p in params)
        by_lab = {(p["lab_id"], p["value"]): p["expect"] for p in params}
        assert by_lab[("S1", "0.8888")] == "0.8001"
        assert by_lab[("S2", "0.8002")] == ""
        filed = records(module, "filed")
        cells = [tuple(c) for r in filed for c in r["cells"]]
        assert ("S1", "Density", "0.8888", "0.8001") in cells

    def test_op_id_only_when_labcore_write_can_take_one(self, road, monkeypatch):
        """LabStation's labcore_write has no op_id parameter today, and passing
        an unknown keyword to it would raise on every batch."""
        lc = LabCore(samples=["S1", "S2"])
        module = road(lc)
        poll(module, lc, journaled(module, row("S1", Density="0.8001")))
        assert all("op_id" not in kw for _ops, kw in lc.batches)

        seen = []

        def write_with_op_id(operation, params=None, source="", op_id=None):
            seen.append(op_id)
            return lc.write(operation, params, source)
        monkeypatch.setitem(mod.__dict__, "labcore_write", write_with_op_id)
        out = module._store_results(
            module._machine, journaled(module, row("S2", Density="0.8002")),
            lc.read_sql, lc.sql, write_with_op_id, [],
            NOW + timedelta(seconds=30))
        assert len(seen) == 1 and seen[0] and isinstance(seen[0], str)
        assert any(r.get("op_id") == seen[0] for r in records(module, "filed"))

    def test_op_id_names_the_batch_not_the_moment(self, road, monkeypatch):
        """An op_id exists so LabCore can recognise a RETRY of a batch it
        already applied. The same batch re-sent after a refusal must carry the
        same id; a different batch — a probe that carries only part of what
        was refused — must not, or LabCore would skip cells it never wrote."""
        lc = LabCore(samples=["S%02d" % i for i in range(30)])
        module = road(lc)
        seen = []

        def write_with_op_id(operation, params=None, source="", op_id=None):
            seen.append((op_id, len(params["operations"])))
            return lc.write(operation, params, source)
        lc.refuse_writes = 2
        rows = journaled(module, *[row("S%02d" % i, Density="0.8%03d" % i)
                                   for i in range(30)])
        # Refused at 0 s (wait 30 s), the probe refused at 31 s (wait 60 s),
        # the same probe again at 92 s.
        for at, rows_k in ((0, rows), (31, ()), (92, ())):
            module._store_results(module._machine, list(rows_k), lc.read_sql,
                                  lc.sql, write_with_op_id, [],
                                  NOW + timedelta(seconds=at))
        (first, n1), (probe, n2), (again, n3) = seen
        assert (n1, n2, n3) == (30, 20, 20)
        assert probe != first, "a probe of 20 is not the batch of 30"
        assert again == probe, "the same 20 re-sent is the same batch"


# ── what came back ──────────────────────────────────────────────────────────

class TestTheAnswerIsReadPerIndex:
    def test_b1_a_sub_error_inside_an_ok_batch_is_not_filed(self, road):
        """`ok: true` is LabCore saying the batch ran, not that every cell
        landed. The failed index stays pending; the others are filed."""
        lc = LabCore(samples=["S1", "S2"])
        lc.index_errors[("S1", "Density")] = "CHECK constraint failed"
        module = road(lc)
        poll(module, lc, journaled(module, row("S1", Density="0.8001"),
                                   row("S2", Density="0.8002")))
        filed = [tuple(c[:2]) for r in records(module, "filed") for c in r["cells"]]
        assert filed == [("S2", "Density")]
        assert any(r[LAB_ID_KEY] == "S1" for r in module._held_rows)

    def test_b1_it_is_tried_three_times_then_parked_as_rejected(self, road):
        lc = LabCore(samples=["S1"])
        lc.index_errors[("S1", "Density")] = "CHECK constraint failed"
        module = road(lc)
        rows = journaled(module, row("S1", Density="0.8001"))
        messages = []
        for k in range(10):
            _out, said = poll(module, lc, rows if k == 0 else (),
                              now=NOW + timedelta(seconds=30 * k))
            messages += said
        assert lc.sent().count(("S1", "Density", "0.8001")) == 3
        rejected = records(module, "rejected")
        assert len(rejected) == 1
        assert rejected[0]["tries"] == 3
        assert rejected[0]["error"] == "CHECK constraint failed"
        assert records(module, "filed") == []
        assert any("LabCore rejected" in m for m in messages), messages
        assert not any(r[LAB_ID_KEY] == "S1" for r in module._held_rows)

    def test_a_batch_that_landed_with_its_answer_lost_is_not_sent_again(self, road):
        """N4: the read after the lost answer finds the reading already in the
        cell, journals it filed, and sends nothing."""
        lc = LabCore(samples=["S1"])
        lc.raise_after_batch = 1
        module = road(lc)
        rows = journaled(module, row("S1", Density="0.8001"))
        poll(module, lc, rows)
        poll(module, lc, now=NOW + timedelta(seconds=30))
        assert lc.sent() == [("S1", "Density", "0.8001")]
        assert len(records(module, "filed")) == 1


class TestRefusalBacksOffAndProbes:
    def test_while_backing_off_nothing_is_read_or_sent(self, road):
        lc = LabCore(samples=["S1"])
        lc.refuse_writes = 1
        module = road(lc)
        poll(module, lc, journaled(module, row("S1", Density="0.8001")))
        reads, sends = len(lc.reads), len(lc.batches)
        poll(module, lc, now=NOW + timedelta(seconds=12))
        assert (len(lc.reads), len(lc.batches)) == (reads, sends)
        poll(module, lc, now=NOW + timedelta(seconds=31))
        assert lc.cell("S1", "Density") == "0.8001"

    def test_the_first_try_after_a_refusal_is_a_probe_of_twenty_cells(self, road):
        """F5: re-sending the whole queue into a LabCore that is refusing is
        what cost 10,100 sends. After a refusal the bench sends at most twenty
        cells, and only once that lands does it send the rest."""
        lc = LabCore(samples=["S%03d" % i for i in range(60)])
        lc.refuse_writes = 1
        module = road(lc)
        rows = journaled(module, *[row("S%03d" % i, Density="0.8%03d" % i)
                                   for i in range(60)])
        poll(module, lc, rows)
        assert len(lc.batches[0][0]) == 60
        poll(module, lc, now=NOW + timedelta(seconds=30))
        assert len(lc.batches[1][0]) == 20
        poll(module, lc, now=NOW + timedelta(seconds=60))
        assert len(lc.batches[2][0]) == 40
        assert all(lc.cell("S%03d" % i, "Density") for i in range(60))

    def test_nothing_is_dropped_however_long_labcore_refuses(self, road):
        """No count caps (§3.2). The hundred-reading held cap and the
        two-hundred-op retry cap were what F3/F4/F5 lost readings to."""
        lc = LabCore(samples=["S%03d" % i for i in range(400)])
        module = road(lc)
        lc.refuse_writes = 10 ** 6
        for k in range(40):
            poll(module, lc, journaled(module, *[
                row("S%03d" % (k * 10 + j), Density="0.8%03d" % (k * 10 + j))
                for j in range(10)]), now=NOW + timedelta(seconds=30 * k))
        lc.refuse_writes = 0
        for k in range(40, 60):
            poll(module, lc, now=NOW + timedelta(seconds=30 * k))
        assert all(lc.cell("S%03d" % i, "Density") == "0.8%03d" % i
                   for i in range(400))


class TestGivingUp:
    def test_a_reading_with_no_sample_after_seven_days_is_given_up(self, road):
        lc = LabCore(samples=[])
        module = road(lc)
        rows = journaled(module, row("S1", when=NOW, Density="0.8001"))
        poll(module, lc, rows)
        poll(module, lc, now=NOW + timedelta(days=8))
        given = records(module, "given_up")
        assert [g["of"] for g in given] == [[rows[0][JOURNAL_KEY]]]
        assert module._held_rows == []
