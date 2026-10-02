"""Adoption on the real module, against a LabCore that keeps real rows.

`test_adoption_plan.py` pins the decision on lists. This pins what a bench
does at its first v4 start under today's server (legacy mode, §10.2 step 3:
indexed LabCore reads, at most 15), where every v3.9 bench on the floor will
be on the day module v4 ships (D8).

The world before each test: an instrument file that v3.9 has been reading,
and the `run` rows v3.9 logged from it — written here with the module's own
`run_log_events`/`build_log_insert`, which is the shape v3.9 wrote — plus the
cells it filed. Then the v4 module starts with a journal that has never
existed. The spec's rows:

  U1  a file v3.9 fully logged (30 lines): 0 new rows, 0 cell sends. A's
      prototype, which read the file from the top, gave 30 and 30.
  U2  one print made while LabStation was down for the upgrade: exactly 1
      `recovered` row, 0 lost, 0 duplicated, and it is not filed.
  U3  a correction factor changed since logging: 0 false recovered.
  U4  file lines older than LEM's first ingest: one adoption summary, no
      alarm.
  U5  all of it through at most 15 LabCore reads.
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta

import pytest

import lem_station_module as mod
from test_journal_custody import (Bench, Kill, T0, UID, lab_id, line,
                                  runs_in_journal)

LOGGED_AT = T0 - timedelta(days=2)


def legacy_world(b, logged, corrections=None, tests=()):
    """What v3.9 left: the file holds `logged` lines and the record holds a
    row for each, written as v3.9 wrote them (corrections applied, raw kept),
    with the cell filed. Returns nothing; the bench has not polled yet."""
    con = b.lab.con
    con.execute(mod.LOG_TABLE_DDL)
    for ddl in mod.LOG_INDEX_DDL:
        con.execute(ddl)
    con.execute("CREATE INDEX IF NOT EXISTS idx_lem_log_lab_ts ON "
                "lem_machine_log(lab_id, ts)")
    machine = b.m.machine()
    saved = (machine.corrections, machine.tests)
    machine.corrections = dict(corrections or {})
    machine.tests = list(tests)
    try:
        for k, text in enumerate(logged):
            at = LOGGED_AT + timedelta(minutes=k)
            result = mod.parse_print(machine, text)
            rows = mod.apply_row_corrections([result.to_row(at)],
                                             machine.corrections)
            for row, kind, lab, test, value, detail in mod.run_log_events(
                    machine, rows, "analyst", None):
                sql, args = mod.build_log_insert(UID, kind, at, lab_id=lab,
                                                 test_name=test, value=value,
                                                 detail=detail)
                con.execute(sql, args)
                if kind == "run":
                    con.execute("INSERT OR REPLACE INTO sample_tests VALUES "
                                "(?, 'Density', ?)", [lab, str(row["Density"])])
    finally:
        machine.corrections, machine.tests = saved
    con.commit()


def write_file(b, lines):
    with open(b.path, "a") as f:
        for text in lines:
            f.write(text + "\n")


def counting(b, monkeypatch):
    """Every SQL string the bench reads LabCore with, in order."""
    seen = []
    inner = b.lab.read_sql

    def read_sql(sql, args=None, timeout=None):
        seen.append(sql)
        return inner(sql, args, timeout)
    monkeypatch.setitem(mod.__dict__, "labcore_read_sql", read_sql)
    return seen


def adoption_reads(seen):
    return [s for s in seen if "MIN(ts)" in s or "lab_id IN" in s]


def log_rows(b):
    """The record's readings: `run` and `qc` rows (status changes and the
    like are not readings)."""
    res = b.lab.read_sql("SELECT kind, lab_id, detail FROM lem_machine_log "
                         "WHERE machine_uid = ? AND kind IN ('run', 'qc') "
                         "ORDER BY rowid", [UID])
    assert not res.get("error"), res
    return res["rows"]


def journal_records(kind):
    j = mod.BenchJournal(mod.journal_dir(UID), UID)
    return [r for r in j._scan() if r["kind"] == kind]


def polls(b, n=3):
    for _ in range(n):
        b.poll()


# ── U1 ────────────────────────────────────────────────────────────────────────

def test_U1_a_fully_logged_file_costs_no_row_and_no_cell(qapp, tmp_path, monkeypatch):
    """The upgrade replay closed. The file holds 30 lines and the record holds
    all 30; the stored offset is 0 (Agilent GC 2's, today). A bench reading
    from the top logged 30 rows and sent 30 cells (A's prototype). Adopted,
    it logs none and sends none, in at most 15 LabCore reads — here 2: when
    did LEM first record this bench, and the Lab IDs of the newest 20."""
    b = Bench(tmp_path, monkeypatch)
    texts = [line(i) for i in range(30)]
    legacy_world(b, texts)
    write_file(b, texts)
    seen = counting(b, monkeypatch)
    before = len(log_rows(b))
    polls(b)
    assert len(log_rows(b)) == before == 30
    assert b.lab.cell_sends == []
    assert runs_in_journal(b.journal()) == []
    (rec,) = journal_records("adoption")
    assert (rec["matched"], rec["presumed"], rec["recovered"]) == (20, 10, 0)
    assert rec["path_kind"] == "fast" and rec["history"] is True
    assert len(adoption_reads(seen)) == 2 <= mod.ADOPTION_MAX_READS


def test_U1_after_adoption_new_prints_are_read_as_ever(qapp, tmp_path, monkeypatch):
    """Adoption puts the cursor at the end of the file. The next print is a
    reading like any other: logged once, filed once."""
    b = Bench(tmp_path, monkeypatch)
    texts = [line(i) for i in range(30)]
    legacy_world(b, texts)
    write_file(b, texts)
    polls(b)
    write_file(b, [line(30)])
    polls(b, 2)
    assert [r["lab_id"] for r in log_rows(b)][30:] == [lab_id(30)]
    assert b.lab.cell_sends == [(lab_id(30), "Density", "0.8030")]


def test_U1_a_restart_after_adoption_does_not_adopt_again(qapp, tmp_path, monkeypatch):
    b = Bench(tmp_path, monkeypatch)
    texts = [line(i) for i in range(30)]
    legacy_world(b, texts)
    write_file(b, texts)
    polls(b)
    b.restart()
    seen = counting(b, monkeypatch)
    polls(b)
    assert adoption_reads(seen) == []
    assert len(journal_records("adoption")) == 1
    assert len(log_rows(b)) == 30 and b.lab.cell_sends == []


# ── U2 ────────────────────────────────────────────────────────────────────────

def test_U2_the_print_made_during_the_upgrade_is_recovered_once_and_not_filed(qapp, tmp_path, monkeypatch):
    """30 lines logged, then one print while LabStation was down. It is in
    the record exactly once, as a `run` with origin 'recovered'; nothing is
    lost, nothing doubled; its cell is NOT sent — a person files it from the
    instrument page through the guard — and the journal will not hand it to
    the results road after a restart either."""
    b = Bench(tmp_path, monkeypatch)
    texts = [line(i) for i in range(30)]
    legacy_world(b, texts)
    write_file(b, texts + [line(30)])
    polls(b)
    rows = log_rows(b)
    assert len(rows) == 31
    tail = rows[-1]
    assert tail["lab_id"] == lab_id(30)
    assert json.loads(tail["detail"])["origin"] == "recovered"
    assert sorted(r["lab_id"] for r in rows) == sorted(lab_id(i) for i in range(31))
    assert b.lab.cell_sends == []
    (run,) = runs_in_journal(b.journal())
    assert run["origin"] == "recovered"
    (rec,) = journal_records("adoption")
    assert (rec["recovered"], rec["path_kind"]) == (1, "full")
    b.restart()
    polls(b)
    assert b.lab.cell_sends == []
    assert len(log_rows(b)) == 31


def test_U2_a_recovered_qc_print_does_not_judge_todays_qc(qapp, tmp_path, monkeypatch):
    """A QC standard printed out of spec while LabStation was down is a
    reading from before the upgrade, not a verdict on the bench now: logged
    as a `run`, never as `qc`, and the bench's status does not go RED on it."""
    b = Bench(tmp_path, monkeypatch)
    machine = b.m.machine()
    machine.tests = [mod.TestSpec(name="Density", value_col="Density",
                                  expected=0.85, std_dev=0.001, k=2.0,
                                  sample_id="QC-D")]
    texts = [line(i) for i in range(10)]
    legacy_world(b, texts)
    write_file(b, texts + ["QC-D,0.9999"])
    polls(b)
    kinds = [(r["kind"], r["lab_id"]) for r in log_rows(b)]
    assert ("run", "QC-D") in kinds and ("qc", "QC-D") not in kinds
    assert b.m.evaluation().status != mod.STATUS_RED


# ── U3 ────────────────────────────────────────────────────────────────────────

def test_U3_a_factor_changed_since_logging_makes_no_false_recovery(qapp, tmp_path, monkeypatch):
    """v3.9 logged these readings with a +0.0100 correction; the factor is
    +0.0200 now. Matched on corrected values every line would look unrecorded
    (30 false recoveries); matched on the raw reading, none is."""
    b = Bench(tmp_path, monkeypatch)
    texts = [line(i) for i in range(30)]
    legacy_world(b, texts, corrections={"Density": 0.01})
    b.m.machine().corrections = {"Density": 0.02}
    write_file(b, texts)
    polls(b)
    assert runs_in_journal(b.journal()) == []
    assert len(log_rows(b)) == 30 and b.lab.cell_sends == []
    (rec,) = journal_records("adoption")
    assert rec["recovered"] == 0


QC_D = dict(name="Density", value_col="Density", expected=0.85, std_dev=0.05,
            k=2.0, sample_id="QC-D")


def _machine_factor_world(b, texts, then, now):
    """v3.9 logged `texts` on a bench whose correction is MACHINE-level
    (lem_correction_factors, applied at the parse boundary) with a QC spec
    that has no correction of its own; the factor is `now` at the v4 start."""
    spec = mod.TestSpec(**QC_D)
    legacy_world(b, texts, corrections=then, tests=[spec])
    machine = b.m.machine()
    machine.tests = [spec]
    machine.corrections = dict(now)
    # Where today's factor lives on a v3.9 floor, and where the poll reads it
    # from: the bench must judge the record on the factor it actually has.
    b.lab.con.execute(mod.CORRECTIONS_DDL)
    for test, value in now.items():
        b.lab.con.execute("INSERT OR REPLACE INTO lem_correction_factors "
                          "(machine_uid, test_name, correction) VALUES (?, ?, ?)",
                          [machine.uid, test, value])
    b.lab.con.commit()


def test_U3_a_qc_standard_logged_under_a_machine_factor_since_changed_is_not_recovered(qapp, tmp_path, monkeypatch):
    """The hole the round-1 critic found. v3.9's `qc_log_detail` writes
    `raw_value` only when the SPEC has a correction; under a machine-level
    factor the verdict row keeps only the corrected number (`value`). The
    QC-D print was logged at +0.0100 and the factor is +0.0200 now, so
    neither the raw reading (0.85) nor today's corrected one (0.87) is the
    0.86 the row holds — and keyed on either, the print looked unrecorded:
    1 false recovered where the bar is 0. A verdict row that kept no raw
    cannot say which reading it was, only that the standard was run for
    that test; it is matched on that, one row for one print."""
    b = Bench(tmp_path, monkeypatch)
    texts = [line(i) for i in range(30)] + ["QC-D,0.8500"]
    _machine_factor_world(b, texts, {"Density": 0.01}, {"Density": 0.02})
    rows = [r for r in log_rows(b) if r["lab_id"] == "QC-D"]
    assert [r["kind"] for r in rows] == ["qc"]
    assert "raw_value" not in json.loads(rows[0]["detail"])   # v3.9's shape
    write_file(b, texts)
    polls(b)
    (rec,) = journal_records("adoption")
    assert rec["recovered"] == 0
    assert len(log_rows(b)) == 31 and b.lab.cell_sends == []
    assert runs_in_journal(b.journal()) == []


def test_U2_of_two_qc_prints_the_one_the_record_lacks_is_the_one_recovered(qapp, tmp_path, monkeypatch):
    """A verdict that kept no raw, logged under a factor that has changed
    since, is no free pass: the standard was printed twice, the record holds
    one verdict, so exactly one print is recovered — the newer, the one made
    while LabStation was down — and the factor change does not hide it.

    (0.8300, not 0.8400: 0.8400 under today's +0.02 IS 0.86, the old
    verdict's value, and a one-test standard cannot tell those two prints
    apart — the newer one then matches exactly and the older is the one left.
    One reading in common is the limit of a one-test standard; a standard of
    several tests is pinned by the others, test_adoption_qc_values.)"""
    b = Bench(tmp_path, monkeypatch)
    logged = [line(i) for i in range(10)] + ["QC-D,0.8500"] + \
        [line(i) for i in range(10, 30)]
    _machine_factor_world(b, logged, {"Density": 0.01}, {"Density": 0.02})
    write_file(b, logged + ["QC-D,0.8300"])
    polls(b)
    (rec,) = journal_records("adoption")
    assert rec["recovered"] == 1
    tail = log_rows(b)[len(logged) - 1 + 1:]
    assert [(r["kind"], r["lab_id"]) for r in tail] == [("run", "QC-D")]
    # Recorded as v3.9 records a corrected run: today's factor applied, the
    # raw reading kept beside it.
    detail = json.loads(tail[0]["detail"])
    assert detail["raw"]["Density"] in ("0.8300", 0.83)


def test_U3_a_qc_standard_no_longer_assigned_is_still_matched_to_its_verdicts(qapp, tmp_path, monkeypatch):
    """The QC assignment is today's configuration; the verdicts are what
    v3.9 did then. QC-D was a standard on this bench when it was logged and
    is not now (or the QC library has not loaded yet): the print has no QC
    keys of its own, but the record says it holds Density verdicts for QC-D,
    and the print is matched to them — not recovered as a `run`."""
    b = Bench(tmp_path, monkeypatch)
    texts = [line(i) for i in range(30)] + ["QC-D,0.8500"]
    _machine_factor_world(b, texts, {"Density": 0.01}, {"Density": 0.02})
    b.m.machine().tests = []
    write_file(b, texts)
    polls(b)
    (rec,) = journal_records("adoption")
    # Every line accounted for and none recovered. The newest twenty match
    # exactly — the QC verdict too, on the +0.01 the run rows' corrections
    # show it was logged under — so this is now the fast path (20 matched,
    # 11 presumed) where it used to need the full match.
    assert rec["recovered"] == 0
    assert (rec["path_kind"], rec["matched"], rec["presumed"]) == ("fast", 20, 11)
    assert len(log_rows(b)) == 31 and b.lab.cell_sends == []


def test_a_line_whose_recorded_rows_cannot_be_read_is_not_recovered(qapp, tmp_path, monkeypatch):
    """The critic's worry about the real v3.9 rows: a row whose detail
    cannot be read makes its line unmatched, and unmatched-after-a-match
    means `recovered`. A row that cannot be read is not evidence of nothing
    — the record DOES hold something for that sample. Such lines are
    counted as `unreadable` (presumed recorded, said in the adoption record
    and the status line), never written to the record a second time."""
    b = Bench(tmp_path, monkeypatch)
    texts = [line(i) for i in range(30)]
    legacy_world(b, texts)
    b.lab.con.execute("UPDATE lem_machine_log SET detail = '{not json' "
                      "WHERE lab_id IN (?, ?)", [lab_id(12), lab_id(25)])
    b.lab.con.commit()
    write_file(b, texts)
    polls(b)
    (rec,) = journal_records("adoption")
    assert (rec["recovered"], rec["unreadable"]) == (0, 2)
    assert len(log_rows(b)) == 30 and b.lab.cell_sends == []


def _write_cr(b, lines):
    """The Eraspec's own LIMS export ends every print with a bare CR — the
    last print in the file too (backup of the Eraspec PC, lims.csv: 311 CRs,
    one LF)."""
    with open(b.path, "ab") as f:
        for text in lines:
            f.write(text.encode() + b"\r")


def test_a_cr_terminated_file_whose_last_print_is_recorded_costs_nothing(qapp, tmp_path, monkeypatch):
    """Found by the floor rehearsal. A trailing bare CR may be half of a
    CRLF, so the reader holds the last line back until the file is quiet —
    and adoption, scanning only "complete" lines, left that last print out
    of the seen-set. Once quiet, the reader took it as a NEW print: logged a
    second time and filed again, on every Eraspec at its first v4 start.
    Adoption waits for the file to hold still anyway, so a quiet file's last
    line is adopted with the rest."""
    b = Bench(tmp_path, monkeypatch)
    texts = [line(i) for i in range(30)]
    legacy_world(b, texts)
    _write_cr(b, texts)
    polls(b, 6)
    (rec,) = journal_records("adoption")
    assert rec["recovered"] == 0
    assert len(log_rows(b)) == 30 and b.lab.cell_sends == []
    assert runs_in_journal(b.journal()) == []


def test_a_print_made_during_the_upgrade_at_the_end_of_a_cr_file_is_recovered(qapp, tmp_path, monkeypatch):
    """The same file with one print the record lacks, at the very end: it
    is recovered (recorded once, not filed), not read later as a live print
    that is QC-judged and auto-filed."""
    b = Bench(tmp_path, monkeypatch)
    texts = [line(i) for i in range(30)]
    legacy_world(b, texts)
    _write_cr(b, texts + [line(30)])
    polls(b, 6)
    (rec,) = journal_records("adoption")
    assert rec["recovered"] == 1
    rows = log_rows(b)
    assert len(rows) == 31 and json.loads(rows[-1]["detail"]).get("origin") == "recovered"
    assert b.lab.cell_sends == []


# ── U4 ────────────────────────────────────────────────────────────────────────

def test_U4_lines_older_than_LEM_are_one_summary_and_no_alarm(qapp, tmp_path, monkeypatch):
    """The file starts with 12 lines from before this bench was on LEM —
    among them an out-of-spec QC standard — then 15 lines LEM recorded. The
    12 are history: counted once in the adoption record, never journaled,
    never logged, never judged."""
    b = Bench(tmp_path, monkeypatch)
    machine = b.m.machine()
    machine.tests = [mod.TestSpec(name="Density", value_col="Density",
                                  expected=0.85, std_dev=0.001, k=2.0,
                                  sample_id="QC-D")]
    old = ["%s,0.7%03d" % (lab_id(100 + i), i) for i in range(11)] + ["QC-D,0.9999"]
    logged = [line(i) for i in range(15)]
    legacy_world(b, logged)
    write_file(b, old + logged)
    polls(b)
    (rec,) = journal_records("adoption")
    assert (rec["pre_history_lines"], rec["recovered"]) == (12, 0)
    assert runs_in_journal(b.journal()) == []
    assert len(log_rows(b)) == 15
    assert b.m.evaluation().status != mod.STATUS_RED
    assert b.lab.cell_sends == []


# ── the boundary ─────────────────────────────────────────────────────────────

def test_lines_before_the_stored_offset_are_never_asked_about(qapp, tmp_path, monkeypatch):
    """§10.2 step 1. The stored offset sits on a line boundary inside the
    file: v3.9 logged everything before it, so adoption asks LabCore only
    about the Lab IDs after it — and a line after it that the record lacks is
    recovered, not history."""
    b = Bench(tmp_path, monkeypatch)
    texts = [line(i) for i in range(40)]
    legacy_world(b, texts[:39])
    write_file(b, texts)
    b.m.machine().last_position = sum(len(t) + 1 for t in texts[:35])
    seen = []
    inner = b.lab.read_sql

    def read_sql(sql, args=None, timeout=None):
        seen.append(list(args or []))
        return inner(sql, args, timeout)
    monkeypatch.setitem(mod.__dict__, "labcore_read_sql", read_sql)
    polls(b)
    asked = {a for args in seen for a in args}
    assert lab_id(34) not in asked and lab_id(35) in asked
    (rec,) = journal_records("adoption")
    assert rec["boundary"] > 0 and rec["presumed"] == 35
    assert [r["lab_id"] for r in runs_in_journal(b.journal())] == [lab_id(39)]


def test_an_offset_in_the_middle_of_a_line_is_no_boundary():
    data = b"A,1\nB,2\nC,3\n"
    assert mod.adoption_boundary(data, 4) == 4
    assert mod.adoption_boundary(data, 5) == 0
    assert mod.adoption_boundary(data, 99) == 0
    assert mod.adoption_boundary(b"A,1\r\nB,2\r\n", 4) == 0
    assert mod.adoption_boundary(b"A,1\r\nB,2\r\n", 5) == 5


# ── failed reads, budgets, kills ─────────────────────────────────────────────

def test_a_failed_read_holds_the_file_and_says_so(qapp, tmp_path, monkeypatch):
    """A failed read is never an empty result. LabCore busy at the first v4
    start must not turn 30 recorded lines into 30 recovered ones, nor read
    them as new: the source holds its bytes, the status line says why, and
    the next poll asks again."""
    b = Bench(tmp_path, monkeypatch)
    texts = [line(i) for i in range(30)]
    legacy_world(b, texts)
    write_file(b, texts)
    inner = b.lab.read_sql
    busy = {"on": True}

    def read_sql(sql, args=None, timeout=None):
        if busy["on"] and "lem_machine_log" in sql:
            return {"error": "LabCore is busy", "busy": True}
        return inner(sql, args, timeout)
    monkeypatch.setitem(mod.__dict__, "labcore_read_sql", read_sql)
    polls(b, 2)
    assert runs_in_journal(b.journal()) == [] and journal_records("adoption") == []
    assert "Adopting history" in b.m._status_label.text() + \
        b.m._status_label.toolTip()
    busy["on"] = False
    polls(b, 2)
    assert len(journal_records("adoption")) == 1
    assert runs_in_journal(b.journal()) == [] and len(log_rows(b)) == 30


def test_the_read_budget_holds_on_a_file_with_thousands_of_samples(qapp, tmp_path, monkeypatch):
    """Eraspec NIR has about 1,750 lines since its stored offset. Even 2,600
    distinct Lab IDs, none of them recorded at the tail, cost at most 15
    reads; the Lab IDs that did not fit — the OLDEST, because a print that
    missed the record is the newest one — are counted as unchecked: presumed
    recorded and said, never a flood of false recoveries."""
    b = Bench(tmp_path, monkeypatch, samples=10)
    old = ["X%05d,0.5" % i for i in range(2600)]
    legacy_world(b, old[-101:-1])         # the newest line never recorded
    write_file(b, old)
    seen = counting(b, monkeypatch)
    polls(b)
    assert len(adoption_reads(seen)) == mod.ADOPTION_MAX_READS
    (rec,) = journal_records("adoption")
    assert rec["labcore_reads"] == 15
    # 15 reads: when LEM first recorded the bench, the newest 20 Lab IDs (the
    # fast path), then 13 reads of 150 more, newest first.
    asked = mod.ADOPTION_FAST_LINES + 13 * mod.ADOPTION_LAB_IDS_PER_READ
    assert rec["unchecked"] == 2600 - asked
    assert (rec["matched"], rec["recovered"]) == (100, 1)
    assert rec["pre_history_lines"] == asked - 101


def test_a_kill_after_the_adoption_is_journaled_neither_adopts_again_nor_replays(qapp, tmp_path, monkeypatch):
    """§3.3 (a) then (b), for adoption: the record and the seen-set are in
    the journal, the cursor is not saved. On restart the journal says
    adoption is done; the file is read from the top and every line's key is
    already known — nothing logged, nothing sent, the recovered line once."""
    b = Bench(tmp_path, monkeypatch)
    texts = [line(i) for i in range(30)]
    legacy_world(b, texts)
    write_file(b, texts + [line(30)])
    fired = []

    def hook(point):
        if point == "after_journal_before_cursor" and not fired:
            fired.append(point)
            raise Kill(point)
    monkeypatch.setattr(mod, "fault_point", hook)
    polls(b, 4)
    assert fired
    assert len(journal_records("adoption")) == 1
    assert [r["lab_id"] for r in runs_in_journal(b.journal())] == [lab_id(30)]
    assert len(log_rows(b)) == 31 and b.lab.cell_sends == []


def test_the_ledger_is_seeded_so_a_rerun_of_a_v39_cell_is_not_a_conflict(qapp, tmp_path, monkeypatch):
    """§10.2 step 5. v3.9 filed 0.8005 for sample 5. After adoption the
    instrument re-runs it: 0.8100. The cell still holds v3.9's value, which
    LEM's ledger now knows as its own, so the re-run is written — not raised
    as a conflict with a person's edit (which is what an unseeded ledger
    would have to call it)."""
    b = Bench(tmp_path, monkeypatch)
    texts = [line(i) for i in range(10)]
    legacy_world(b, texts)
    write_file(b, texts)
    polls(b)
    assert b.journal().ledger_value(lab_id(5), "Density") == "0.8005"
    write_file(b, ["%s,0.8100" % lab_id(5)])
    polls(b, 2)
    assert b.lab.results()[lab_id(5)] == "0.8100"
    assert journal_records("conflict") == []


def test_a_bench_with_no_record_reads_its_file_from_the_top(qapp, tmp_path, monkeypatch):
    """A bench LEM has never recorded has no history to adopt: one read says
    so, and the file is read exactly as a v4 bench with no cursor reads it."""
    b = Bench(tmp_path, monkeypatch)
    b.lab.con.execute(mod.LOG_TABLE_DDL)
    write_file(b, [line(i) for i in range(3)])
    seen = counting(b, monkeypatch)
    b.poll()
    assert [r["lab_id"] for r in log_rows(b)] == [lab_id(i) for i in range(3)]
    assert len(adoption_reads(seen)) == 1
    (rec,) = journal_records("adoption")
    assert rec["history"] is False


def test_a_fresh_adoption_is_recorded_even_when_the_poll_s_journaling_fails(qapp, tmp_path, monkeypatch):
    """The `adoption` record of a bench with no history says nothing about
    any line, so it is journaled on its own, before the read. It used to ride
    on the poll's records. A poll whose readings then failed to reach the
    journal adopted again on every poll and re-read the whole file each time:
    in the gate (D1 under `journal_poll_off`) 4,800 prints per poll, until the
    harness gave up waiting. Now the readings are retried as any failed read
    is, and adoption happens once."""
    b = Bench(tmp_path, monkeypatch)
    b.lab.con.execute(mod.LOG_TABLE_DDL)
    write_file(b, [line(i) for i in range(3)])
    calls = []
    monkeypatch.setattr(mod.LEMStationModule, "_journal_poll",
                        lambda self, *a, **k: calls.append(1) or False,
                        raising=True)
    seen = counting(b, monkeypatch)
    polls(b, 3)
    (rec,) = journal_records("adoption")
    assert rec["history"] is False
    assert b.journal().adoption_due() is False
    assert len(adoption_reads(seen)) == 1          # asked once, not per poll


def test_a_serial_bench_has_nothing_to_adopt(qapp, tmp_path, monkeypatch):
    b = Bench(tmp_path, monkeypatch, source="serial")
    b.emit(1)
    b.poll()
    assert b.journal()._meta["adoption"] == "not_needed:serial"
    assert journal_records("adoption") == []


def test_the_lab_id_read_uses_the_lab_id_index():
    """§10.2 says `lab_id IN (≤150)` on idx_lem_log_lab_ts. With both of
    production's indexes present, SQLite must pick that one — not
    idx_lem_log_uid_kind_ts, which walks every row the bench ever logged."""
    con = sqlite3.connect(":memory:")
    con.execute(mod.LOG_TABLE_DDL)
    for ddl in mod.LOG_INDEX_DDL:
        con.execute(ddl)
    con.execute("CREATE INDEX idx_lem_log_lab_ts ON lem_machine_log(lab_id, ts)")
    sql, args = mod.build_adoption_lab_query(["L-1", "L-2"])
    plan = " ".join(str(r) for r in con.execute("EXPLAIN QUERY PLAN " + sql,
                                                args + ["b1"]))
    assert "idx_lem_log_lab_ts" in plan, plan
    sql, args = mod.build_first_ingest_query("b1")
    plan = " ".join(str(r) for r in con.execute("EXPLAIN QUERY PLAN " + sql, args))
    assert "idx_lem_log_uid_kind_ts" in plan, plan
    assert "SCAN lem_machine_log" not in plan, plan
