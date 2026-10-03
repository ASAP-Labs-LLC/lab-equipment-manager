"""The source readers on the real module: where a file bench is up to, kept
at the bench, and what it does when the file does something other than grow.

`test_rewrite_resolver.py` pins the decision on lists of hashes. This pins
the module around it (transfer spec §4.1, §4.3), against a LabCore that keeps
real rows:

  * THE CURSOR. Offset, head and tail hashes, file identity and lineage, in
    cursor.json, plus the ordered line hashes in snapshot.bin — written after
    the journal's fsync on every poll that consumed anything. Until v4 the
    only saved offset was `last_position` in LabCore, written when somebody
    pressed OK in Settings; every clean restart re-read the file from that
    stale point (K6: 30 duplicate rows for one restart). It no longer says
    where the bench is: only adoption reads it, once, at the first v4 start
    (`test_adoption_module.py`).
  * WHOLE LINES. A line the instrument has half written waits; a last line
    with no newline is taken once it has been quiet for two polls and 10 s
    (R5: v3.9 logged "100126-10" as a Lab ID and lost the reading).
  * QUIESCENCE. A file that changed in a way the cursor cannot explain is
    only resolved once it has stopped changing, so a poll that lands in the
    middle of a whole-file rewrite is never mistaken for a trim (Q1).
  * ROTATION. When the file under the name is a different file, the renamed
    old one is found by its identity and drained first (X4).
  * multi_csv. A file is journaled before it is moved to processed/ (K7: v3.9
    moved it first and a kill in between lost it), keyed by name, size,
    mtime_ns and content.
"""
import json
import os
from datetime import timedelta

import pytest

import lem_station_module as mod
from test_journal_custody import (Bench, Kill, T0, UID, lab_id, line,
                                  density_machine, runs_in_journal)
from test_module_qt import make_module


def kill_at(monkeypatch, name):
    """The process dies the first time the module reaches fault point `name`."""
    fired = []

    def hook(point):
        if point == name and not fired:
            fired.append(point)
            raise Kill(point)
    monkeypatch.setattr(mod, "fault_point", hook)
    return fired


def cursor_of(b):
    with open(os.path.join(mod.journal_dir(UID), mod.CURSOR_NAME)) as f:
        doc = json.load(f)
    (cur,) = doc["sources"].values()
    return cur


def details(b):
    res = b.lab.read_sql("SELECT lab_id, detail FROM lem_machine_log "
                         "WHERE kind='run' ORDER BY rowid")
    assert not res.get("error"), res
    return [(r["lab_id"], json.loads(r["detail"])) for r in res["rows"]]


# ── the cursor ───────────────────────────────────────────────────────────────

def test_the_cursor_is_kept_at_the_bench_and_a_restart_reads_nothing_again(qapp, tmp_path, monkeypatch):
    """K6's mechanism, closed at the source rather than absorbed by the keys:
    after a clean restart the bench knows exactly where it was, so it does not
    even re-read — `_journal_suppressed` stays 0."""
    b = Bench(tmp_path, monkeypatch)
    for _ in range(10):
        b.emit(3)
        b.poll()
    cur = cursor_of(b)
    assert cur["offset"] == os.path.getsize(b.path)
    assert cur["file_id"] and cur["lineage"] and cur["head_hash"] and cur["tail_hash"]
    assert os.path.getsize(os.path.join(mod.journal_dir(UID), mod.SNAPSHOT_NAME)) > 30 * 16
    b.restart()
    for _ in range(3):
        b.poll()
    assert sorted(b.lab.log_runs()) == [lab_id(i) for i in range(30)]
    assert len(b.lab.cell_sends) == 30
    assert b.m._journal_suppressed == 0


def test_without_the_cursor_the_keys_still_stop_the_replay(qapp, tmp_path, monkeypatch):
    """The second line of defence: cursor.json gone, the file is read from
    the top, and every line the journal holds is the same key — suppressed and
    counted, never logged twice."""
    b = Bench(tmp_path, monkeypatch)
    for _ in range(10):
        b.emit(3)
        b.poll()
    b.restart()
    os.remove(os.path.join(mod.journal_dir(UID), mod.CURSOR_NAME))
    b.m._sources.clear()
    for _ in range(3):
        b.poll()
    assert sorted(b.lab.log_runs()) == [lab_id(i) for i in range(30)]
    assert b.m._journal_suppressed == 30


def test_the_cursor_is_saved_only_after_the_journal_holds_the_readings(qapp, tmp_path, monkeypatch):
    """§3.3 (a) then (b). At the instant the journal's append returns, the
    cursor on disk must still be the previous poll's: a kill there re-reads
    the lines, and the journal's keys drop them (K1b)."""
    b = Bench(tmp_path, monkeypatch)
    b.emit(3)
    b.poll()
    before = cursor_of(b)["offset"]
    seen = {}

    def hook(point):
        if point == "after_journal_before_cursor":
            seen["offset"] = cursor_of(b)["offset"]
            seen["runs"] = len(runs_in_journal(b.journal()))
    monkeypatch.setattr(mod, "fault_point", hook)
    b.emit(3)
    b.poll()
    assert seen == {"offset": before, "runs": 6}
    assert cursor_of(b)["offset"] == os.path.getsize(b.path)


def test_a_kill_between_the_journal_and_the_cursor_loses_and_doubles_nothing(qapp, tmp_path, monkeypatch):
    b = Bench(tmp_path, monkeypatch)
    for _ in range(3):
        b.emit(3)
        b.poll()
    b.emit(3)
    fired = kill_at(monkeypatch, "after_journal_before_cursor")
    b.poll()
    assert fired
    for _ in range(3):
        b.poll()
    assert sorted(b.lab.log_runs()) == [lab_id(i) for i in range(12)]
    assert b.m._journal_suppressed == 3


def test_last_position_is_no_longer_read(qapp, tmp_path, monkeypatch):
    """`lem_machine_config.last_position` dates from whenever Settings was last
    saved (the stored ones are from 09-23 and 09-24). A value pointing into
    the middle of a line used to start the read there and log the tail of a
    line as a reading. A bench with no cursor starts at the top of its file —
    and from then on the cursor, not the config, says where it is. (A mid-line
    offset is no adoption boundary either, and this bench has no record to
    adopt, so its first start reads from the top.)"""
    b = Bench(tmp_path, monkeypatch)
    b.emit(3)
    b.m.machine().last_position = 9            # mid-line, and stale
    b.poll()
    assert b.lab.log_runs() == [lab_id(0), lab_id(1), lab_id(2)]
    b.m.machine().last_position = 10 ** 9       # nonsense: ignored too
    b.emit(1)
    b.poll()
    assert b.lab.log_runs() == [lab_id(i) for i in range(4)]
    assert b.lab.results()[lab_id(3)] == "0.8003"


def test_the_offset_is_still_published_for_a_downgrade(qapp, tmp_path, monkeypatch):
    """Not read, but still MIRRORED onto the machine: a bench rolled back to
    v3.9 reads its offset from the published config, and a sensible one costs
    nothing to keep."""
    b = Bench(tmp_path, monkeypatch)
    b.emit(3)
    b.poll()
    assert b.m.machine().last_position == os.path.getsize(b.path)


def test_an_unreadable_cursor_is_said_and_set_aside_never_taken_as_empty(qapp, tmp_path, monkeypatch):
    """A failed read is never an empty result. A cursor.json that will not
    parse is not "no cursor": it is kept aside for a person, the status line
    says so, and the re-read that follows is absorbed by the journal's keys."""
    b = Bench(tmp_path, monkeypatch)
    for _ in range(3):
        b.emit(3)
        b.poll()
    b.restart()
    path = os.path.join(mod.journal_dir(UID), mod.CURSOR_NAME)
    with open(path, "w") as f:
        f.write("{not json")
    b.m._sources.clear()
    b.poll()
    assert any(n.startswith(mod.CURSOR_NAME + ".bad-")
               for n in os.listdir(mod.journal_dir(UID)))
    assert "cursor" in b.m._status_label.toolTip().lower()
    assert sorted(b.lab.log_runs()) == [lab_id(i) for i in range(9)]


# ── whole lines ──────────────────────────────────────────────────────────────

def test_half_a_line_waits_for_the_rest(qapp, tmp_path, monkeypatch):
    """R5: the poll lands while the instrument has flushed "100126-10" of a
    line. v3.9 logged that as a Lab ID (a stray) and then lost the reading."""
    b = Bench(tmp_path, monkeypatch)
    b.emit(3)
    b.poll()
    text = line(3) + "\n"
    with open(b.path, "a") as f:
        f.write(text[:9])
    b.poll()
    assert b.lab.log_runs() == [lab_id(i) for i in range(3)]
    with open(b.path, "a") as f:
        f.write(text[9:])
    b.poll()
    assert b.lab.log_runs() == [lab_id(i) for i in range(4)]
    assert b.lab.results()[lab_id(3)] == "0.8003"


def test_a_last_line_with_no_newline_is_taken_once_it_has_been_quiet(qapp, tmp_path, monkeypatch):
    """Some instruments never terminate their last line. It is taken when the
    file has not changed across two polls at least 10 s apart — and only once:
    when the newline arrives later, nothing is read twice."""
    b = Bench(tmp_path, monkeypatch)
    with open(b.path, "a") as f:
        f.write(line(0))
    b.poll()
    assert b.lab.log_runs() == []
    b.poll()
    assert b.lab.log_runs() == [lab_id(0)]
    with open(b.path, "a") as f:
        f.write("\n" + line(1) + "\n")
    b.poll()
    assert b.lab.log_runs() == [lab_id(0), lab_id(1)]


def test_quiet_is_ten_seconds_on_the_bench_clock_not_two_polls(qapp, tmp_path, monkeypatch):
    b = Bench(tmp_path, monkeypatch)
    with open(b.path, "a") as f:
        f.write(line(0))
    b.m.process_now(T0)
    b.m.process_now(T0 + timedelta(seconds=5))
    assert b.lab.log_runs() == []
    b.m.process_now(T0 + timedelta(seconds=11))
    assert b.lab.log_runs() == [lab_id(0)]


# ── quiescence and rewrites ──────────────────────────────────────────────────

def test_a_poll_in_the_middle_of_a_rewrite_reads_nothing(qapp, tmp_path, monkeypatch):
    """Q1: the instrument rewrites its whole file and the poll sees only the
    first seven lines so far. That is not a trim. Nothing is decided until the
    file stops changing; when the rewrite completes, the one new line is the
    one new reading."""
    b = Bench(tmp_path, monkeypatch)
    for _ in range(5):
        b.emit(3)
        b.poll()
    old = open(b.path).read().splitlines(True)
    with open(b.path, "w") as f:
        f.writelines(old[:7])
    b.poll()
    with open(b.path, "w") as f:
        f.writelines(old + [line(15) + "\n"])
    for _ in range(3):
        b.poll()
    assert sorted(b.lab.log_runs()) == [lab_id(i) for i in range(16)]


def test_a_rewrite_is_resolved_only_once_the_file_is_quiet(qapp, tmp_path, monkeypatch):
    b = Bench(tmp_path, monkeypatch)
    for _ in range(10):
        b.emit(3)
        b.poll()
    tail = open(b.path).read().splitlines(True)[-5:]
    with open(b.path, "w") as f:
        f.writelines(tail + [line(30) + "\n"])
    b.poll()
    assert len(b.lab.log_runs()) == 30            # first sight: waits
    b.poll()
    assert sorted(b.lab.log_runs()) == [lab_id(i) for i in range(31)]
    for _ in range(3):
        b.poll()
    assert len(b.lab.log_runs()) == 31


def test_a_newest_first_file_is_read_in_the_order_it_was_printed(qapp, tmp_path, monkeypatch):
    """R2: v3.9 lost 12 of 15 readings and logged 12 twice. Every reading is
    now logged once, oldest first, and the cursor remembers the file's
    direction."""
    b = Bench(tmp_path, monkeypatch)
    for k in range(5):
        with open(b.path, "w") as f:
            f.writelines(line(i) + "\n" for i in reversed(range(3 * k + 3)))
        b.poll()
    for _ in range(2):
        b.poll()
    runs = b.lab.log_runs()
    assert sorted(runs) == [lab_id(i) for i in range(15)]
    assert runs[3:] == [lab_id(i) for i in range(3, 15)]
    assert cursor_of(b)["newest_first"] is True


def test_the_slow_rescan_finds_an_edit_outside_the_head_and_tail_windows(qapp, tmp_path, monkeypatch):
    """R6deep: a same-size edit 6,000 bytes into an 8,000-byte file is outside
    both the 4,096-byte head window and the 256-byte tail window, so the
    cursor's hashes cannot see it. Every 15 minutes the whole file is hashed
    against the snapshot; the corrected line is then a new reading."""
    b = Bench(tmp_path, monkeypatch, samples=500)
    b.emit(400)
    b.poll()
    lines = open(b.path).read().splitlines(True)
    k = 300
    assert sum(len(x) for x in lines[:k]) > mod.CURSOR_HEAD_BYTES
    lab, val = lines[k].strip().split(",")
    lines[k] = "%s,%s\n" % (lab, val.replace("0.8", "0.9", 1))
    with open(b.path, "w") as f:
        f.writelines(lines)
    b.poll()
    assert len(b.lab.log_runs()) == 400           # the hashes cannot see it
    for _ in range(31):                            # 15 minutes of polls
        b.poll()
    assert len(b.lab.log_runs()) == 401
    assert b.lab.results()[lab] == val.replace("0.8", "0.9", 1)


# ── rotation ─────────────────────────────────────────────────────────────────

def test_a_rotated_file_is_drained_from_its_new_name_first(qapp, tmp_path, monkeypatch):
    """X4: two lines were appended after the last poll, then the instrument
    renamed the file away and started a new one. The bench finds the old file
    by its identity, reads the two lines from where it was, then the new file."""
    b = Bench(tmp_path, monkeypatch)
    b.emit(3)
    b.poll()
    b.emit(2)
    os.replace(b.path, str(b.path) + ".1")
    with open(b.path, "w") as f:
        f.write(line(5) + "\n" + line(6) + "\n")
    for _ in range(3):
        b.poll()
    assert sorted(b.lab.log_runs()) == [lab_id(i) for i in range(7)]


def test_an_ambiguous_overlap_is_recorded_labelled_and_journaled(qapp, tmp_path, monkeypatch):
    """X3: the new file starts with the old file's last three lines. All are
    recorded; the three are `origin: ambiguous` in the journal and in the log
    detail, so the record shows them as possible repeats; one `ambiguity`
    record says what was decided."""
    b = Bench(tmp_path, monkeypatch)
    b.emit(6)
    b.poll()
    tail = open(b.path).read().splitlines(True)[-3:]
    tmp = str(b.path) + ".tmp"
    with open(tmp, "w") as f:
        f.writelines(tail + [line(6) + "\n", line(7) + "\n"])
    os.replace(tmp, b.path)
    for _ in range(3):
        b.poll()
    rows = details(b)
    assert len(rows) == 11
    labelled = [lab for lab, d in rows if d.get("origin") == "ambiguous"]
    assert labelled == [lab_id(3), lab_id(4), lab_id(5)]
    recs = b.journal()._scan()
    amb = [r for r in recs if r["kind"] == "ambiguity"]
    assert len(amb) == 1 and amb[0]["ambiguity"] == "rotation_overlap" and amb[0]["lines"] == 3
    assert [r["origin"] for r in recs if r["kind"] == "run"][-5:] == \
        ["ambiguous"] * 3 + ["live"] * 2


def test_a_new_days_file_repeating_the_qc_triplet_then_a_sample_loses_nothing(qapp, tmp_path, monkeypatch):
    """The critic's X2 variant, on the real module. Day 1 opens with a QC
    triplet and runs three samples; the instrument renames the file away and
    day 2 opens with the same triplet (same values, printed again for real)
    and a sample. Round 1 read the day-2 file as a correction of day 1's tail
    and recorded only the sample: 3 lost, 0 labelled. All four are recorded
    now; the three that match day 1 carry `origin: ambiguous`, and one
    ambiguity record says why."""
    b = Bench(tmp_path, monkeypatch)
    b.emit(6)                                      # "QC" 0..2, samples 3..5
    b.poll()
    os.replace(b.path, str(b.path) + ".1")
    with open(b.path, "w") as f:
        f.writelines(line(i) + "\n" for i in (0, 1, 2, 6))
    for _ in range(3):
        b.poll()
    rows = details(b)
    assert sorted(lab for lab, _d in rows) == sorted(
        [lab_id(i) for i in range(7)] + [lab_id(0), lab_id(1), lab_id(2)])
    assert [lab for lab, d in rows if d.get("origin") == "ambiguous"] == \
        [lab_id(0), lab_id(1), lab_id(2)]
    amb = [r for r in b.journal()._scan() if r["kind"] == "ambiguity"]
    assert len(amb) == 1 and amb[0]["lines"] == 3


def test_a_new_file_holding_all_of_a_short_old_one_is_a_rotation_if_the_old_file_is_still_there(qapp, tmp_path, monkeypatch):
    """Day 1 printed only its triplet; day 2 opens with it again and a sample.
    The old file is in the folder as inst.csv.1, so this is a rotation, not a
    temp-file copy of the same history: all four are recorded."""
    b = Bench(tmp_path, monkeypatch)
    b.emit(3)
    b.poll()
    os.replace(b.path, str(b.path) + ".1")
    with open(b.path, "w") as f:
        f.writelines(line(i) + "\n" for i in (0, 1, 2, 3))
    for _ in range(3):
        b.poll()
    assert sorted(b.lab.log_runs()) == sorted(
        [lab_id(i) for i in range(4)] + [lab_id(0), lab_id(1), lab_id(2)])


def test_a_short_file_rewritten_through_a_temp_file_is_still_a_continuation(qapp, tmp_path, monkeypatch):
    """The same day-2 bytes with NO old file left behind: an instrument that
    saves its history plus one line through a temp file. One new reading,
    nothing labelled."""
    b = Bench(tmp_path, monkeypatch)
    b.emit(3)
    b.poll()
    tmp = str(b.path) + ".tmp"
    with open(tmp, "w") as f:
        f.writelines(line(i) + "\n" for i in range(4))
    os.replace(tmp, b.path)
    for _ in range(3):
        b.poll()
    assert sorted(b.lab.log_runs()) == [lab_id(i) for i in range(4)]
    assert not [d for _l, d in details(b) if d.get("origin") == "ambiguous"]


# ── a file that never goes quiet ─────────────────────────────────────────────
#
# Quiescence (Q1) holds a change the cursor cannot explain until the file has
# stopped changing. An instrument that writes on every poll never stops: the
# critic drove one that saves its whole history through a temp file every
# 20 s against a bench polling every 20 s, and round 1 delivered 0 of 90
# readings in 30 minutes with nothing on the status line. Nothing was lost —
# it all arrived once the instrument stopped — but a bench that says "nothing
# new" while it holds unread data is the empty result the rules forbid.
#
# The rule now: after QUIET_MAX_WAIT of waiting, the bench reads the part of
# the file that has held still — byte-identical to what an earlier poll at
# least QUIET_SECONDS ago saw, at the top of the file (or, newest-first, at the
# bottom) — and says on the status line that it is doing so. That is the quiet
# rule applied to the region the writer has finished with, not a guess.

def _src(tmp_path, name="inst.csv"):
    path = tmp_path / name
    jd = tmp_path / ("j-" + name)
    jd.mkdir()
    return str(path), mod.SingleCsvSource(str(path), mod.CursorStore(str(jd)))


def _take(src, now):
    r = src.read(now)
    r.commit()
    return [str(p) for p in r.prints]


def _save_via_temp(path, lines):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.writelines(lines)
    os.replace(tmp, path)


def test_a_file_saved_through_a_temp_file_on_every_poll_is_still_read(tmp_path):
    path, src = _src(tmp_path)
    hist = ["R%04d,1.00\n" % i for i in range(50)]
    _save_via_temp(path, hist)
    assert len(_take(src, T0)) == 50
    got = []
    notes = []
    for k in range(1, 91):                         # 30 minutes, 20 s apart
        hist.append("N%04d,1.00\n" % k)
        _save_via_temp(path, hist)
        got += _take(src, T0 + timedelta(seconds=20 * k))
        notes.append(src.status_note())
    during = len(got)
    for k in range(91, 95):                        # the instrument stops
        got += _take(src, T0 + timedelta(seconds=20 * k))
    assert sorted(got) == sorted(h.strip() for h in hist[50:])   # once each
    assert during >= 84, during                    # round 1: 0
    assert any("has not stopped changing" in n for n in notes)
    # said once as an event (the operator's notice list), and standing on the
    # status line for as long as it lasts
    assert sum("has not stopped changing" in n for n in src.notices) == 1
    assert src.status_note() == ""                 # quiet again: said nothing


def test_a_continuous_printer_after_an_early_edit_keeps_being_read(tmp_path):
    """The critic's other probe: a same-size edit inside the first 4 KB, then
    one print per 30 s poll for an hour. Round 1 delivered 1 of 120 until the
    printing stopped. The corrected line is a new reading; every print
    arrives within the hour."""
    path, src = _src(tmp_path)
    with open(path, "w") as f:
        for i in range(400):
            f.write("S%04d,%d.00\n" % (i, i % 7))
    assert len(_take(src, T0)) == 400
    got = []
    for k in range(1, 121):
        if k == 2:
            data = open(path, "rb").read().replace(b"S0010,3.00", b"S0010,9.00", 1)
            open(path, "wb").write(data)
        with open(path, "a") as f:
            f.write("N%04d,1.00\n" % k)
        got += _take(src, T0 + timedelta(seconds=30 * k))
    during = list(got)
    for k in range(121, 125):
        got += _take(src, T0 + timedelta(seconds=30 * k))
    want = ["N%04d,1.00" % k for k in range(1, 121)] + ["S0010,9.00"]
    assert sorted(got) == sorted(want)
    assert len(during) >= 118, len(during)


def test_a_newest_first_file_rewritten_on_every_poll_is_still_read(tmp_path):
    """R2's instrument, printing on every poll: its new line is at the TOP, so
    what holds still is the bottom of the file."""
    path, src = _src(tmp_path)
    rows = ["R%04d,1.00\n" % i for i in range(10)]
    with open(path, "w") as f:
        f.writelines(reversed(rows))
    _take(src, T0)
    rows.append("R0010,1.00\n")
    with open(path, "w") as f:
        f.writelines(reversed(rows))
    got = []
    for k in (1, 2):                               # detected while quiet
        got += _take(src, T0 + timedelta(seconds=20 * k))
    assert got == ["R0010,1.00"]
    for k in range(3, 63):                         # 20 minutes, never quiet
        rows.append("R%04d,1.00\n" % (k + 8))
        with open(path, "w") as f:
            f.writelines(reversed(rows))
        got += _take(src, T0 + timedelta(seconds=20 * k))
    during = len(got)
    for k in range(63, 67):
        got += _take(src, T0 + timedelta(seconds=20 * k))
    assert sorted(got) == sorted(r.strip() for r in rows[10:])
    assert during >= 55, during


def test_a_file_where_nothing_holds_still_waits_and_says_so(tmp_path):
    """Every byte changes on every poll (a whole-file rewrite of different
    content): there is no part the writer has finished with, so nothing is
    read — and the status line says the bench is waiting, and that nothing
    is lost by it."""
    path, src = _src(tmp_path)
    with open(path, "w") as f:
        f.write("A,1\nB,2\n")
    _take(src, T0)
    for k in range(1, 8):
        with open(path, "w") as f:
            f.write("Z%d,%d\nY%d,%d\n" % (k, k, k, k))
        assert _take(src, T0 + timedelta(seconds=20 * k)) == []
    note = src.status_note()
    assert "has not stopped changing" in note and "nothing" in note.lower()


def test_a_held_region_with_no_whole_line_is_not_taken(tmp_path):
    """The part that held still may be only half a line: here a slow writer
    rewrites the file with the same three lines, a few bytes per poll. Taking
    that half line would resolve an empty file against the snapshot, set the
    cursor to 0, and the finished file would then be read again from the top
    as three new readings. It keeps waiting instead; the finished file is the
    one already read, and nothing is new."""
    path, src = _src(tmp_path)
    full = "A,1\nB,2\nC,3\n"
    with open(path, "w") as f:
        f.write(full)
    _take(src, T0)
    for k in range(1, 8):                          # "A", "A,", "A,1" ... slowly
        with open(path, "w") as f:
            f.write(full[:min(k, 3)] if k < 7 else full[:3])
        assert _take(src, T0 + timedelta(seconds=20 * k)) == []
    with open(path, "w") as f:
        f.write(full)
    got = []
    for k in range(8, 12):
        got += _take(src, T0 + timedelta(seconds=20 * k))
    assert got == []


def test_a_mid_rewrite_poll_still_waits_inside_the_cap(tmp_path):
    """Q1 is not weakened: a poll that sees a prefix of a rewrite, then the
    completed rewrite 30 s later, still reads only the one new line once the
    file is quiet — no prefix region is taken inside QUIET_MAX_WAIT."""
    path, src = _src(tmp_path)
    old = ["L%02d,1\n" % i for i in range(15)]
    with open(path, "w") as f:
        f.writelines(old)
    _take(src, T0)
    with open(path, "w") as f:
        f.writelines(old[:7])
    assert _take(src, T0 + timedelta(seconds=30)) == []
    with open(path, "w") as f:
        f.writelines(old + ["L15,1\n"])
    got = []
    for k in (2, 3, 4):
        got += _take(src, T0 + timedelta(seconds=30 * k))
    assert got == ["L15,1"]
    assert mod.QUIET_MAX_WAIT >= timedelta(seconds=60)


def test_the_waiting_note_reaches_the_status_line(qapp, tmp_path, monkeypatch):
    b = Bench(tmp_path, monkeypatch)
    b.emit(3)
    b.poll()
    for k in range(6):
        with open(b.path, "w") as f:
            f.write("X%d,0.1\n" % k)
        b.poll()
    (source,) = b.m._sources.values()
    assert "has not stopped changing" in b.m._source_notes(b.m.machine())
    assert source.status_note()                    # stays until it settles


# ── multi_csv ────────────────────────────────────────────────────────────────

class Drop:
    """A multi_csv bench: one file per print in a watched folder."""

    def __init__(self, tmp_path, monkeypatch):
        self.b = Bench(tmp_path, monkeypatch)
        self.folder = tmp_path / "drop"
        self.folder.mkdir()
        self.b.m.set_machine(density_machine(self.folder, "multi_csv"), publish=True)
        self.n = 0

    def emit(self, count):
        for _ in range(count):
            (self.folder / ("r%05d.csv" % self.n)).write_text(line(self.n) + "\n")
            self.n += 1

    def processed(self):
        d = self.folder / mod.PROCESSED_DIRNAME
        return sorted(p.name for p in d.iterdir()) if d.exists() else []

    def waiting(self):
        return sorted(p.name for p in self.folder.iterdir() if p.is_file())


def test_multi_csv_journals_a_file_before_it_moves_it(qapp, tmp_path, monkeypatch):
    """K7: v3.9 moved the file to processed/ and THEN wrote the log; a kill in
    between lost the reading (3 lost). Now the journal holds it before the
    move, and a kill between the two leaves a file the restart reads again —
    and the journal's key drops."""
    d = Drop(tmp_path, monkeypatch)
    d.emit(3)
    d.b.poll()
    d.emit(3)
    fired = kill_at(monkeypatch, "after_journal_before_cursor")
    d.b.poll()
    assert fired
    assert len(d.processed()) == 3                    # not moved yet
    for _ in range(2):
        d.b.poll()
    assert sorted(d.b.lab.log_runs()) == [lab_id(i) for i in range(6)]
    assert len(d.processed()) == 6 and d.waiting() == []
    assert d.b.m._journal_suppressed == 3


def test_multi_csv_a_kill_before_the_journal_loses_nothing(qapp, tmp_path, monkeypatch):
    d = Drop(tmp_path, monkeypatch)
    d.emit(3)
    fired = kill_at(monkeypatch, "before_journal")
    d.b.poll()
    assert fired
    for _ in range(2):
        d.b.poll()
    assert sorted(d.b.lab.log_runs()) == [lab_id(i) for i in range(3)]
    assert len(d.processed()) == 3


def test_multi_csv_key_is_name_size_mtime_and_content():
    """A re-read before the move has the same mtime and dedupes; an instrument
    that exports identical bytes under the same name AGAIN has a new mtime and
    it is a new reading."""
    k = mod.multi_file_key("b1", "r1.csv", 20, 1000, "ab" * 32)
    assert k == mod.multi_file_key("b1", "r1.csv", 20, 1000, "ab" * 32)
    assert k != mod.multi_file_key("b1", "r1.csv", 20, 1001, "ab" * 32)
    assert k != mod.multi_file_key("b1", "r2.csv", 20, 1000, "ab" * 32)
    assert k != mod.multi_file_key("b1", "r1.csv", 20, 1000, "cd" * 32)
    assert k != mod.multi_file_key("b2", "r1.csv", 20, 1000, "ab" * 32)


def test_multi_csv_identical_bytes_exported_again_are_a_new_reading(qapp, tmp_path, monkeypatch):
    d = Drop(tmp_path, monkeypatch)
    (d.folder / "run.csv").write_text(line(0) + "\n")
    d.b.poll()
    p = d.folder / "run.csv"
    p.write_text(line(0) + "\n")
    st = os.stat(p)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))
    d.b.poll()
    assert d.b.lab.log_runs() == [lab_id(0), lab_id(0)]


# ── the store check comes before QC evaluation ───────────────────────────────

def test_a_re_read_line_never_reaches_the_parser_or_the_evaluation(qapp, tmp_path, monkeypatch):
    """§3.3: a key the journal already holds is dropped BEFORE evaluation, so
    a replayed QC print can never renew QC freshness. Observed at the parser:
    with the cursor gone, the re-read file's lines never get parsed."""
    b = Bench(tmp_path, monkeypatch)
    b.emit(5)
    b.poll()
    b.restart()
    os.remove(os.path.join(mod.journal_dir(UID), mod.CURSOR_NAME))
    b.m._sources.clear()
    parsed = []
    real = mod.parse_print
    monkeypatch.setattr(mod, "parse_print",
                        lambda m, text: parsed.append(text) or real(m, text))
    b.emit(1)
    b.poll()
    assert parsed == [line(5)]


# ── re-delivery after a kill: what already landed is not sent again ─────────

def _kill_after_nth_log_write(b, n):
    """LabCore accepts the n-th machine-log write, and the process dies before
    it hears back — the write LANDED and the journal never marked it."""
    real = b.lab.sql
    seen = {"n": 0}

    def sql(sql, args=None, source="LabStation", timeout=None):
        res = real(sql, args, source, timeout)
        if "INSERT INTO lem_machine_log" in sql:
            seen["n"] += 1
            if seen["n"] == n:
                b.lab.sql = real
                mod.__dict__["labcore_sql"] = real
                raise Kill("after log write %d" % n)
        return res
    b.lab.sql = sql
    mod.__dict__["labcore_sql"] = sql


def test_rows_that_landed_before_a_kill_are_not_logged_again(qapp, tmp_path, monkeypatch):
    """K2: a 250-print poll goes out in three batches; the process dies right
    after LabCore accepted the second. Phase 1 logged 203 rows twice; the
    journal alone still logged the 100 of the second batch twice, because the
    mark saying they had landed never got written. Every owed row now names
    its journal record (`detail.jk`) and goes in through the exact key
    (legacy projection, §10.3), so the restart sends all 250 again and the
    200 already there add nothing."""
    b = Bench(tmp_path, monkeypatch, samples=300)
    b.emit(3)
    b.poll()
    b.emit(250)
    _kill_after_nth_log_write(b, 2)
    b.poll()
    for _ in range(3):
        b.poll()
    runs = b.lab.log_runs()
    assert len(runs) == 253 and sorted(runs) == [lab_id(i) for i in range(253)]
    assert not [r for r in b.journal().open_runs() if not r["projected"]]


def test_two_identical_prints_in_one_poll_are_both_redelivered(qapp, tmp_path, monkeypatch):
    """The check counts rows, it does not ask "does one exist": two genuine
    prints of the same sample and value in one poll are two identical rows,
    and when neither landed both are sent."""
    b = Bench(tmp_path, monkeypatch)
    b.emit(1)
    b.poll()
    with open(b.path, "a") as f:
        f.write(line(5) + "\n" + line(5) + "\n")
    b.lab.plan = lambda kind, what: "kill_before" if kind == "log" else None
    b.poll()
    for _ in range(2):
        b.poll()
    assert sorted(b.lab.log_runs()) == [lab_id(0), lab_id(5), lab_id(5)]


def test_a_restart_resends_what_it_owes_without_asking_labcore_first(qapp, tmp_path, monkeypatch):
    """Until the exact key, a restarted bench READ LabCore's log at the owed
    rows' timestamps before re-sending (`_journal_verify_owed`), and when that
    read failed it had to hold everything ("a failed read is never an empty
    result"). The key made the read unnecessary: rows that landed match
    themselves. So the restart costs LabCore no read of lem_machine_log at
    all — and there is no failed read left to mistake for an empty one."""
    b = Bench(tmp_path, monkeypatch)
    b.emit(3)
    b.poll()
    b.emit(3)
    _kill_after_nth_log_write(b, 1)
    b.poll()
    assert len(b.lab.log_runs()) == 6            # landed, then the kill
    reads = []
    real = b.lab.read_sql

    def read_sql(sql, args=None, timeout=None):
        if "lem_machine_log" in sql and " ts IN" in sql:
            reads.append(sql)
        return real(sql, args, timeout)
    mod.__dict__["labcore_read_sql"] = read_sql
    b.poll()
    b.poll()
    assert reads == []
    assert sorted(b.lab.log_runs()) == [lab_id(i) for i in range(6)]


def test_rows_owed_from_many_polls_go_back_in_statements_of_at_most_100(qapp, tmp_path, monkeypatch):
    """A bench that died after a long LabCore outage can owe rows from
    hundreds of polls. They go back through the same batching as any other
    rows — at most LOG_BATCH_ROWS a statement, 7 bound values a row — so the
    resend can never be one statement over SQLite's variable limit, refused
    every time with the rows kept at the bench for ever."""
    b = Bench(tmp_path, monkeypatch, samples=400)
    for _ in range(5):
        b.lab.plan = lambda kind, what: "kill_before" if kind == "log" else None
        b.emit(60)
        b.poll()                                 # dies before its log write
    sizes = []
    real = b.lab.sql

    def sql(sql, args=None, source="LabStation", timeout=None):
        if "INSERT INTO lem_machine_log" in sql:
            sizes.append(len(args or []))
        return real(sql, args, source, timeout)
    mod.__dict__["labcore_sql"] = sql
    b.poll()
    assert sizes and max(sizes) <= 7 * mod.LOG_BATCH_ROWS
    assert sorted(b.lab.log_runs()) == sorted(lab_id(i) for i in range(300))


def test_a_file_left_in_flight_is_delivered_even_without_a_journal(qapp, tmp_path, monkeypatch):
    """A multi_csv file moved aside by a run that then died, on a bench whose
    journal will not open now: it must not be stranded in .lem_inflight."""
    d = Drop(tmp_path, monkeypatch)
    inflight = d.folder / mod.INFLIGHT_DIRNAME
    inflight.mkdir()
    (inflight / "r00000.csv").write_text(line(0) + "\n")
    monkeypatch.setattr(mod.LEMStationModule, "_journal_for", lambda self, m: None)
    d.b.poll()
    assert d.b.lab.log_runs() == [lab_id(0)]
    assert d.processed() == ["r00000.csv"]
    assert list(inflight.iterdir()) == []
