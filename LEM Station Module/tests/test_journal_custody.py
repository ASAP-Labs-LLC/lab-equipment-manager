"""The journal as the bench's custody: what a kill or a restart can no longer take.

`test_bench_journal.py` pins the file. This pins what the module does with it,
on the real module against a LabCore that keeps real rows:

  * ORDER. A poll's readings are in the journal, fsync'd, before the first
    byte of them goes to LabCore — the log row, the result, anything. A kill
    at any later point therefore cannot lose them (spec §3.3).
  * RE-DELIVERY. A reading the journal holds whose log row never landed, or
    whose result was never filed, is delivered after a restart — once.
  * SUPPRESSION. A line the journal already holds is not a new reading when a
    restarted bench re-reads its file from a stale offset. Phase 1 measured
    that replay at 30 duplicate rows and 30 duplicate cell sends for a clean
    restart (K6); it is the 92 % replay share of today's `run` rows.
  * SERIAL. A frame is journaled by the reader as it completes — on the
    reader's own code path, before any poll takes it — because a serial frame
    has no other copy. A kill between the frame completing and its fsync loses
    that one frame and no other (K8r: the stated residual, exactly 1).

The fake LabCore here is SQLite in memory with LabStation's real helper
signatures. The module itself never imports sqlite3: LabStation intercepts it
(transfer-map §5), which is why the journal is plain files.
"""
import ast
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta

import pytest

import lem_station_module as mod
from test_module_qt import make_module

T0 = datetime(2026, 10, 1, 9, 0, 0)
UID = "b1"


class Kill(BaseException):
    """The process dies. BaseException, as a real kill is not catchable."""


class Lab:
    """LabCore: samples, results and whatever lem_* tables the bench declares,
    in one SQLite database, behind LabStation's helper signatures. `plan`
    injects a fault: plan(kind, sql_or_operation) -> None | "kill_before".
    `on_log_insert` is called before the first machine-log INSERT executes."""

    def __init__(self, samples=()):
        self.con = sqlite3.connect(":memory:", check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.con.execute("CREATE TABLE samples (lab_id TEXT PRIMARY KEY, "
                         "first_seen_at TEXT)")
        self.con.execute("CREATE TABLE sample_tests (lab_id TEXT, test_name "
                         "TEXT, result TEXT, PRIMARY KEY (lab_id, test_name))")
        self.seed(samples)
        self.plan = None
        self.on_log_insert = None
        self.cell_sends = []
        self.ops = 0

    def seed(self, lab_ids):
        with self.lock:
            for lid in lab_ids:
                self.con.execute("INSERT OR IGNORE INTO samples VALUES (?, ?)",
                                 [lid, "2026-10-01 08:00:00"])

    def _fault(self, kind, what):
        if self.plan and self.plan(kind, what) == "kill_before":
            self.plan = None
            raise Kill(what)

    def sql(self, sql, args=None, source="LabStation", timeout=None):
        self.ops += 1
        if "INSERT INTO lem_machine_log" in sql:
            if self.on_log_insert:
                hook, self.on_log_insert = self.on_log_insert, None
                hook()
            self._fault("log", sql)
        if "lem_held_results" in sql and sql.lstrip().upper().startswith("INSERT"):
            self._fault("held", sql)
        try:
            with self.lock:
                cur = self.con.execute(sql, args or [])
                self.con.commit()
                return {"ok": True, "rowcount": cur.rowcount}
        except sqlite3.Error as exc:
            return {"error": str(exc)}

    def read_sql(self, sql, args=None, timeout=None):
        self.ops += 1
        try:
            with self.lock:
                rows = [dict(r) for r in self.con.execute(sql, args or [])]
            return {"rows": rows}
        except sqlite3.Error as exc:
            return {"error": str(exc)}

    def write(self, operation, params=None, source=""):
        self.ops += 1
        self._fault("write", operation)
        for op in (params or {}).get("operations", []):
            p = op.get("params") or {}
            self.cell_sends.append((p.get("lab_id"), p.get("test_name"),
                                    p.get("value")))
            with self.lock:
                self.con.execute("INSERT OR REPLACE INTO sample_tests VALUES "
                                 "(?, ?, ?)", [p["lab_id"], p["test_name"],
                                               p["value"]])
                self.con.commit()
        return {"ok": True}

    def is_running(self):
        return True

    # ── ground truth ──
    def log_runs(self):
        res = self.read_sql("SELECT lab_id FROM lem_machine_log WHERE kind='run' "
                            "AND machine_uid=? ORDER BY rowid", [UID])
        assert not res.get("error"), res
        return [r["lab_id"] for r in res["rows"]]

    def results(self):
        res = self.read_sql("SELECT lab_id, result FROM sample_tests "
                            "WHERE test_name='Density'")
        assert not res.get("error"), res
        return {r["lab_id"]: r["result"] for r in res["rows"]}


def lab_id(i):
    return "100126-%05d" % (10000 + i)


def line(i):
    return "%s,0.%04d" % (lab_id(i), 8000 + i)


def density_machine(path, source_type="single_csv"):
    return mod.Machine(uid=UID, title="Bench b1", source_type=source_type,
                       csv_path=str(path), delimiter=",",
                       lab_id=mod.Selector(mode="cell", index=0),
                       mappings=[mod.MethodMapping(
                           methods=["Density"],
                           selector=mod.Selector(mode="cell", index=1))])


class FakePort:
    """A serial port the reader reads from instead of the OS."""

    def __init__(self):
        self.chunks = []

    def read(self):
        if self.chunks:
            return self.chunks.pop(0)
        time.sleep(0.005)
        return b""

    def close(self):
        pass


class Bench:
    """One LabStation process at a time over one LabCore and one journal."""

    def __init__(self, tmp_path, monkeypatch, source="single_csv", samples=50):
        self.tmp = tmp_path
        self.mp = monkeypatch
        monkeypatch.setenv("LEM_JOURNAL_DIR", str(tmp_path / "journal"))
        monkeypatch.setattr(mod, "_in_thread", lambda fn, cb: cb(fn()))
        monkeypatch.setattr(mod, "post_live", lambda *a, **k: None)
        monkeypatch.setattr(mod, "fetch_floor_config", lambda *a, **k: None)
        self.lab = Lab([lab_id(i) for i in range(samples)])
        for name in ("sql", "read_sql", "write", "is_running"):
            monkeypatch.setitem(mod.__dict__, "labcore_" + name,
                                getattr(self.lab, name))
        self.source = source
        self.path = tmp_path / "inst.csv"
        self.path.write_text("")
        self.k = 0
        self.n = 0
        self.frames = []
        self.m = make_module()
        self.m.set_machine(density_machine(self.path, source), publish=True)
        self._attach()

    def _attach(self):
        if self.source == "serial":
            self.port = FakePort()
            self.reader = self.m._open_serial_reader(self.m.machine(),
                                                     port=self.port)
            self.m._serial_reader = self.reader
            self.t = 0.0

    def emit(self, count):
        for _ in range(count):
            text = line(self.n)
            self.n += 1
            if self.source == "serial":
                self.frames.append(text)
            else:
                with open(self.path, "a") as f:
                    f.write(text + "\n")

    def deliver_frames(self):
        """The reader's own code path, driven synchronously: each frame's
        bytes, then an idle gap, which is what completes a frame."""
        while self.frames:
            text = self.frames.pop(0)
            self.t += 10.0
            self.reader._on_bytes(text.encode(), self.t)
            self.t += 10.0
            self.reader._on_idle(self.t)

    def poll(self):
        try:
            if self.source == "serial":
                self.deliver_frames()
            self.m.process_now(T0 + timedelta(seconds=30 * self.k))
        except Kill:
            self.restart()
        self.k += 1

    def restart(self):
        try:
            self.m.shutdown()
        except Exception:
            pass
        self.m = make_module()
        self.m.restore_state({"machine_uid": UID, "poll_seconds": 30})
        assert self.m.machine() is not None
        self._attach()

    def journal(self):
        return mod.BenchJournal(mod.journal_dir(UID), UID)


def runs_in_journal(j):
    return [r for r in j._scan() if r["kind"] == "run"]


# ── order ─────────────────────────────────────────────────────────────────────

def test_readings_are_journaled_before_the_first_labcore_write(qapp, tmp_path, monkeypatch):
    """§3.3's custody argument rests on one ordering: journal append + fsync,
    THEN anything else. Checked at the instant the machine-log INSERT arrives
    at LabCore — the journal on disk must already hold the poll's readings."""
    b = Bench(tmp_path, monkeypatch)
    seen = {}

    def at_log_write():
        seen["runs"] = [r["lab_id"] for r in runs_in_journal(b.journal())]
    b.lab.on_log_insert = at_log_write
    b.emit(3)
    b.poll()
    assert seen["runs"] == [lab_id(0), lab_id(1), lab_id(2)]


def test_an_idle_poll_writes_nothing(qapp, tmp_path, monkeypatch):
    """No readings, no append, no fsync: the bench's disk is not a cost of
    being switched on."""
    b = Bench(tmp_path, monkeypatch)
    b.emit(2)
    b.poll()
    j = b.m._journal
    calls = []
    j._fsync = lambda fd: calls.append(fd)
    sizes = {n: os.path.getsize(os.path.join(j.dir, n)) for n in os.listdir(j.dir)}
    for _ in range(5):
        b.poll()
    assert calls == []
    assert sizes == {n: os.path.getsize(os.path.join(j.dir, n))
                     for n in os.listdir(j.dir)}


def test_the_journal_key_never_leaves_the_bench(qapp, tmp_path, monkeypatch):
    """A row carries its journal ref so the results road can say which record
    it settled. That key is bookkeeping: it must not become a "method" in a
    result cell, a value in the log detail, or a column in the latest-result
    CSV. RESERVED_ROW_KEYS is the one place every consumer already filters."""
    assert mod.JOURNAL_KEY in mod.RESERVED_ROW_KEYS
    b = Bench(tmp_path, monkeypatch)
    b.emit(2)
    b.poll()
    assert all(test == "Density" for _l, test, _v in b.lab.cell_sends)
    res = b.lab.read_sql("SELECT detail FROM lem_machine_log WHERE kind='run'")
    for r in res["rows"]:
        assert mod.JOURNAL_KEY not in r["detail"]


def test_the_module_never_imports_sqlite3():
    """LabStation intercepts `import sqlite3` in custom modules. The journal is
    plain files for that reason; this keeps it so."""
    src = open(mod.__file__, encoding="utf-8").read()
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert "sqlite3" not in names


# ── re-delivery ──────────────────────────────────────────────────────────────

def test_a_kill_before_the_log_write_is_redelivered_once_after_restart(qapp, tmp_path, monkeypatch):
    """K1/K8's shape on a file bench: the readings were journaled, the process
    died before LabCore heard of them. Today that is 12 duplicate rows (the
    file is re-read from a stale offset) or 3 lost ones (serial). Now the
    restart re-delivers them from the journal and suppresses the re-read."""
    b = Bench(tmp_path, monkeypatch)
    for _ in range(4):
        b.emit(3)
        b.poll()
    b.emit(3)
    b.lab.plan = lambda kind, what: "kill_before" if kind == "log" else None
    b.poll()                                   # dies before the log write
    for _ in range(3):
        b.poll()
    assert sorted(b.lab.log_runs()) == [lab_id(i) for i in range(15)]
    assert b.lab.results() == {lab_id(i): "0.%04d" % (8000 + i) for i in range(15)}
    assert len(b.lab.cell_sends) == len(set(b.lab.cell_sends)) == 15


def test_a_clean_restart_does_not_replay_the_file(qapp, tmp_path, monkeypatch):
    """K6: the bench restarts and its stored offset is the one from setup, so
    it reads the whole file again. Every line the journal already holds is
    the same line at the same offset — not a new reading."""
    b = Bench(tmp_path, monkeypatch)
    for _ in range(10):
        b.emit(3)
        b.poll()
    assert b.m._journal_suppressed == 0      # an advancing offset re-reads nothing
    b.restart()
    for _ in range(3):
        b.poll()
    assert sorted(b.lab.log_runs()) == [lab_id(i) for i in range(30)]
    assert len(b.lab.cell_sends) == 30
    # and the replay that did not happen is counted, so it can be seen
    assert b.m._journal_suppressed == 30


def test_a_genuine_reprint_is_still_a_new_reading(qapp, tmp_path, monkeypatch):
    """Suppression is by (file, offset, line hash), never by content alone: an
    instrument that prints the same sample with the same value again has
    produced a second reading, at a new offset, and it must be recorded."""
    b = Bench(tmp_path, monkeypatch)
    b.emit(1)
    b.poll()
    with open(b.path, "a") as f:
        f.write(line(0) + "\n")
    b.poll()
    assert b.lab.log_runs() == [lab_id(0), lab_id(0)]


def test_a_new_file_under_the_same_name_is_not_the_old_one(qapp, tmp_path, monkeypatch):
    """X2's shape: the instrument rotates its file and the new one starts with
    the same QC standards, same values, at the same offsets as the old one's
    first lines. They are new prints. The key carries the file's identity
    (device, inode, creation time), not just its path, so a rotated-in file is
    never mistaken for the one it replaced."""
    b = Bench(tmp_path, monkeypatch)
    b.emit(3)
    b.poll()
    old = open(b.path).read().splitlines(True)[:2]
    os.replace(b.path, str(b.path) + ".1")
    with open(b.path, "w") as f:
        f.writelines(old)
    b.poll()
    assert sorted(b.lab.log_runs()) == sorted(
        [lab_id(0), lab_id(1), lab_id(2), lab_id(0), lab_id(1)])


def test_held_readings_survive_a_kill_before_the_mirror_write(qapp, tmp_path, monkeypatch):
    """K9: readings for samples the LIMS has not logged in yet are held at the
    bench. Phase 1 lost all three when the process died before the held mirror
    reached LabCore. The journal holds them unsettled; the restart takes them
    back into the results road; they file once the sample appears."""
    b = Bench(tmp_path, monkeypatch, source="serial", samples=3)
    b.emit(3)
    b.poll()
    b.emit(3)                                  # 3..5: no sample yet
    b.lab.plan = lambda kind, what: "kill_before" if kind == "held" else None
    b.poll()
    b.lab.seed([lab_id(3), lab_id(4), lab_id(5)])
    for _ in range(4):
        b.poll()
    assert b.lab.results() == {lab_id(i): "0.%04d" % (8000 + i) for i in range(6)}
    assert sorted(b.lab.log_runs()) == [lab_id(i) for i in range(6)]
    assert len(b.lab.cell_sends) == 6


def test_readings_still_waiting_for_their_sample_are_not_settled(qapp, tmp_path, monkeypatch):
    """K9r's shape, and the other half of SETTLED: a reading held because its
    sample is not logged in yet is still owed. The journal must not call it
    settled, or a clean restart — which no longer reads the LabCore held
    mirror back on top of the journal — would forget it."""
    b = Bench(tmp_path, monkeypatch, source="serial", samples=3)
    b.emit(3)
    b.poll()
    b.emit(3)                                  # 3..5: held
    b.poll()
    owed = {r["rec"]["lab_id"] for r in b.journal().open_runs()
            if not r["settled"]}
    assert owed == {lab_id(3), lab_id(4), lab_id(5)}
    b.restart()
    b.lab.seed([lab_id(3), lab_id(4), lab_id(5)])
    for _ in range(3):
        b.poll()
    assert b.lab.results() == {lab_id(i): "0.%04d" % (8000 + i) for i in range(6)}
    assert len(b.lab.cell_sends) == 6
    assert not [r for r in b.journal().open_runs() if not r["settled"]]


# ── serial ───────────────────────────────────────────────────────────────────

def test_a_frame_is_journaled_by_the_reader_before_any_poll_takes_it(qapp, tmp_path, monkeypatch):
    b = Bench(tmp_path, monkeypatch, source="serial")
    b.emit(1)
    b.deliver_frames()
    j = b.journal()
    assert [text for _pk, text in j.pending_frames()] == [line(0)]
    frames = b.reader.take_frames()
    assert frames == [line(0)] and frames[0].pk == j.pending_frames()[0][0]


def test_the_reader_thread_itself_journals_frames(qapp, tmp_path, monkeypatch):
    """The synchronous drive above calls the reader's handlers directly. This
    runs the REAL `_run` loop on its thread, over a fake port, and waits for
    the frame to reach the journal — the path a bench PC takes."""
    b = Bench(tmp_path, monkeypatch, source="serial")
    machine = b.m.machine()
    machine.idle_gap = 0.05
    port = FakePort()
    reader = b.m._open_serial_reader(machine, port=port)
    port.chunks.append(line(7).encode())
    worker = threading.Thread(target=reader._run, daemon=True)
    worker.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if [t for _pk, t in b.journal().pending_frames()] == [line(7)]:
            break
        time.sleep(0.01)
    reader.close()
    worker.join(2)
    assert [t for _pk, t in b.journal().pending_frames()] == [line(7)]


def test_a_kill_between_frame_completion_and_fsync_loses_exactly_that_frame(qapp, tmp_path, monkeypatch):
    """K8r, the stated residual: the one frame whose journaling the kill
    interrupts is gone. The frames before it are journaled; the ones after it
    complete after the restart and are recorded. Exactly 1 lost, 0 doubled."""
    b = Bench(tmp_path, monkeypatch, source="serial")
    for _ in range(2):
        b.emit(3)
        b.poll()
    b.emit(3)
    hits = {"n": 0}

    def hook(name):
        if name == "serial_frame_complete_before_fsync":
            hits["n"] += 1
            if hits["n"] == 1:
                raise Kill(name)
    monkeypatch.setattr(mod, "fault_point", hook)
    b.poll()
    monkeypatch.setattr(mod, "fault_point", lambda name: None)
    for _ in range(3):
        b.poll()
    logged = b.lab.log_runs()
    assert sorted(logged) == sorted(set(logged))
    assert set(logged) == {lab_id(i) for i in range(9)} - {lab_id(6)}


def test_a_frame_journaled_but_never_polled_is_processed_after_a_restart(qapp, tmp_path, monkeypatch):
    """The reader journaled the frame; the process died before a poll took it
    from memory. The journal says it was never consumed, so it is."""
    b = Bench(tmp_path, monkeypatch, source="serial")
    b.emit(1)
    b.poll()
    b.emit(1)
    b.deliver_frames()                         # journaled, not polled
    b.restart()
    b.poll()
    b.poll()
    assert sorted(b.lab.log_runs()) == [lab_id(0), lab_id(1)]


# ── torn tail through the module ─────────────────────────────────────────────

def test_a_torn_append_is_repaired_and_the_reading_read_again(qapp, tmp_path, monkeypatch):
    """T5: power fails mid-append. The process died after writing and before
    fsync, and the disk kept only part of the last line. On restart the torn
    line is cut off; the readings whose lines survived are re-delivered from
    the journal and the torn one is read again from the instrument file. 0
    lost, 0 doubled."""
    b = Bench(tmp_path, monkeypatch)
    for _ in range(2):
        b.emit(3)
        b.poll()
    b.emit(3)
    hits = {"n": 0}

    def hook(name):
        if name == "after_journal_before_fsync":
            hits["n"] += 1
            if hits["n"] == 1:
                raise Kill(name)
    monkeypatch.setattr(mod, "fault_point", hook)
    try:
        b.m.process_now(T0)
    except Kill:
        pass
    monkeypatch.setattr(mod, "fault_point", lambda name: None)
    j = b.m._journal
    seg = sorted(n for n in os.listdir(j.dir) if n.startswith("seg-"))[-1]
    p = os.path.join(j.dir, seg)
    data = open(p, "rb").read()
    last = data.splitlines(True)[-1]
    with open(p, "r+b") as f:
        f.truncate(len(data) - len(last) // 2)
    b.restart()
    assert b.m._journal_for(b.m.machine()).repairs
    for _ in range(3):
        b.poll()
    assert sorted(b.lab.log_runs()) == [lab_id(i) for i in range(9)]
    assert len(b.lab.cell_sends) == len(set(b.lab.cell_sends)) == 9


# ── when the journal cannot keep custody ─────────────────────────────────────

def test_without_a_journal_the_bench_works_as_before_and_says_so(qapp, tmp_path, monkeypatch):
    """A journal that cannot be opened is not an empty journal — and it is not
    a reason to stop the lab's results either. The bench carries on the way it
    did before v4 (straight to LabCore, no copy kept) and the status line says
    so on every poll, so nobody mistakes the degraded mode for custody."""
    blocker = tmp_path / "journal"
    blocker.write_text("not a folder")
    b = Bench(tmp_path, monkeypatch)
    b.emit(3)
    b.poll()
    assert b.m._journal is None and b.m._journal_error
    assert sorted(b.lab.log_runs()) == [lab_id(i) for i in range(3)]
    text = b.m._status_label.text()
    assert "journal unavailable" in text.lower() and "NO copy" in text
    b.emit(1)
    b.poll()
    assert "journal unavailable" in b.m._status_label.text().lower()


def test_disk_pressure_pauses_file_ingest_without_moving_the_offset(qapp, tmp_path, monkeypatch):
    b = Bench(tmp_path, monkeypatch)
    b.emit(3)
    b.poll()
    j = b.m._journal
    j.limits["pause_unacked"] = 1                # anything unacked is too much
    b.m._journal_disk_checked = None
    pos = b.m.machine().last_position
    b.emit(3)
    b.poll()
    assert b.m.machine().last_position == pos
    assert len(b.lab.log_runs()) == 3
    assert "paused" in b.m._status_label.text()
    j.limits["pause_unacked"] = 10 ** 12
    b.m._journal_disk_checked = None
    b.poll()
    assert len(b.lab.log_runs()) == 6


# ── one journal per instrument per process ───────────────────────────────────

def test_two_modules_on_one_instrument_share_one_journal(qapp, tmp_path, monkeypatch):
    """Two module instances on one canvas bound to the same instrument must
    not number records from two counters into the same files: two records
    with one seq is a record the server's (uid, epoch, seq) key would drop.
    They share one journal — one counter, one lock — and the second
    instance's read of the same file is suppressed instead of logged twice."""
    b = Bench(tmp_path, monkeypatch)
    other = make_module()
    other.set_machine(density_machine(b.path), publish=False)
    b.emit(3)
    b.poll()
    other.process_now(T0 + timedelta(seconds=10))
    b.emit(2)
    other.process_now(T0 + timedelta(seconds=20))
    b.poll()
    assert other._journal is b.m._journal
    seqs = [r["seq"] for r in b.journal()._scan()]
    assert seqs == list(range(1, len(seqs) + 1))
    assert sorted(b.lab.log_runs()) == [lab_id(i) for i in range(5)]
    other.shutdown()


def test_shutdown_releases_the_journal_so_a_new_process_recovers(qapp, tmp_path, monkeypatch):
    """The shared journal is released at shutdown; the next module to bind
    opens it fresh and re-delivers what was owed — exactly what a new process
    does."""
    b = Bench(tmp_path, monkeypatch)
    b.emit(2)
    b.lab.plan = lambda kind, what: "kill_before" if kind == "log" else None
    try:
        b.m.process_now(T0)
    except Kill:
        pass
    old = b.m._journal
    b.restart()                                  # shutdown, then a new module
    b.poll()
    assert b.m._journal is not old
    assert sorted(b.lab.log_runs()) == [lab_id(0), lab_id(1)]
