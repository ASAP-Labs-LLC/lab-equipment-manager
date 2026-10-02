"""The adoption rehearsal: what a bench's first v4 start will decide, run on
copies before it runs on the floor.

§10.2 predicts 0 recovered on Eraspec, Eraspec NIR and Agilent GC 1 because
their daily replays logged every line (G1). The prediction can only be
checked on copies of their files and of their recorded rows, and the check
is only worth anything if it runs the module's OWN decision — so these tests
pin that the tool agrees with the bench, including the legacy read budget,
and that a replay-heavy record (each line logged several times over) predicts
0 recovered, while one print the record lacks predicts exactly 1.
"""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bench(mod, path):
    return mod.Machine(uid="b1", title="Bench b1", source_type="single_csv",
                       csv_path=str(path), delimiter=",",
                       lab_id=mod.Selector(mode="cell", index=0),
                       mappings=[mod.MethodMapping(
                           methods=["Density"],
                           selector=mod.Selector(mode="cell", index=1))])


def _copies(mod, tmp_path, n, logged, replays=1):
    """A file of `n` lines and a log-mirror copy (`log` table) holding the
    first `logged` of them, each `replays` times — the daily re-reads."""
    path = tmp_path / "Eraspec.csv"
    texts = ["100126-%05d,0.%04d" % (10000 + i, 8000 + i % 1000) for i in range(n)]
    path.write_text("".join(t + "\n" for t in texts))
    machine = _bench(mod, path)
    db = tmp_path / "log-mirror.sqlite3"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE log (rowid_src INTEGER PRIMARY KEY, machine_uid "
                "TEXT, ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value "
                "TEXT, detail TEXT)")
    at = datetime(2026, 9, 1, 8, 0, 0)
    for r in range(replays):
        for k, text in enumerate(texts[:logged]):
            when = at + timedelta(days=r, seconds=k)
            row = mod.parse_print(machine, text).to_row(when)
            for _row, kind, lab, test, value, detail in mod.run_log_events(
                    machine, [row], None, None):
                sql, args = mod.build_log_insert("b1", kind, when, lab_id=lab,
                                                 test_name=test, value=value,
                                                 detail=detail)
                con.execute("INSERT INTO log (machine_uid, ts, kind, lab_id, "
                            "test_name, value, detail) VALUES (?,?,?,?,?,?,?)",
                            args)
    con.commit()
    con.close()
    return path, db, machine


def test_a_replayed_record_predicts_no_recovery_in_either_mode(loaded, tmp_path):
    """The Agilent's shape: 600 lines, each logged three times over by the
    daily replays, a stale offset of 0. Nothing is recovered, the legacy
    fast path costs 2 reads, and the v2 digest agrees."""
    import adoption_rehearsal as R
    mod = loaded[3]
    path, db, machine = _copies(mod, tmp_path, 600, 600, replays=3)
    out = R.rehearse(str(path), machine, R.load_rows(str(db), "b1"))
    assert out["legacy"]["recovered"] == 0 and out["v2"]["recovered"] == 0
    assert out["legacy"]["path"] == "fast"
    assert out["legacy"]["labcore_reads"] == 2
    assert out["recorded_rows"] == 1800 and out["unreadable_rows"] == 0


def test_one_print_the_record_lacks_is_predicted_as_one(loaded, tmp_path):
    import adoption_rehearsal as R
    mod = loaded[3]
    path, db, machine = _copies(mod, tmp_path, 400, 399, replays=2)
    out = R.rehearse(str(path), machine, R.load_rows(str(db), "b1"))
    assert out["legacy"]["recovered"] == 1 == out["v2"]["recovered"]
    assert out["legacy"]["recovered_lines"] == ["100126-10399,0.8399"]
    assert out["legacy"]["labcore_reads"] <= 15


def test_a_missing_record_copy_is_an_error_not_an_empty_record(loaded, tmp_path):
    import adoption_rehearsal as R
    with pytest.raises(FileNotFoundError):
        R.load_rows(str(tmp_path / "nope.sqlite3"), "b1")


def test_the_command_line_writes_its_answer(loaded, tmp_path):
    mod = loaded[3]
    path, db, machine = _copies(mod, tmp_path, 50, 50)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(machine.to_dict()))
    out = tmp_path / "out.json"
    p = subprocess.run([sys.executable, "-B",
                        os.path.join(HERE, "adoption_rehearsal.py"),
                        "--file", str(path), "--rows", str(db), "--uid", "b1",
                        "--config", str(cfg), "--json", str(out)],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=120)
    assert p.returncode == 0, p.stdout.decode(errors="replace")
    got = json.loads(out.read_text())
    assert got["legacy"]["recovered"] == 0 and got["boundary"] == 0


# ── the floor rehearsal: a record written by v3.9.0's own code ──────────────
#
# `floor_rehearsal.py` stands in for production LabCore (which nothing here
# may read) with a record the TAGGED v3.9.0 module writes itself, replays
# and all, over a growing copy of a file. These pin that the stand-in is what
# it says: v3.9.0's code, v3.9.0's replay habit, and a prediction that moves
# when the world does — 0 recovered as-is, exactly 1 with a downtime print.

@pytest.fixture(scope="module")
def v39_root(tmp_path_factory):
    import floor_rehearsal as F
    return F.v39_tree(str(tmp_path_factory.mktemp("v39")))


def _cr_bench(tmp_path):
    """A CR-terminated file in the Eraspec's 2023 LIMS shape, 40 prints."""
    path = tmp_path / "lims.csv"
    rows = ["ERASPEC;Diesel;ESF1;11/30/2023;13:%02d;11-30-23-%03d   ;Op ;FAME=; 0.%02d;V%%"
            % (i % 60, i, i) for i in range(40)]
    path.write_bytes(("\r".join(rows) + "\r").encode())
    cfg = {"uid": "er1", "title": "Eraspec", "source_type": "single_csv",
           "delimiter": ";", "lab_id": {"mode": "cell", "index": 5},
           "mappings": [{"methods": ["FAME"],
                         "selector": {"mode": "cell", "index": 8}}]}
    return path, cfg


def test_the_record_is_written_by_v390_and_carries_its_replays(v39_root, tmp_path):
    """8 days, a marker stored after print 1, every restart replaying the
    rest: the record holds each print several times over, as the Eraspec's
    does (G1: the same 223 rows once a day)."""
    import floor_rehearsal as F
    path, cfg = _cr_bench(tmp_path)
    data = path.read_bytes()
    db, info = F.write_record(v39_root, str(path), cfg,
                              F.history_steps(data, days=8, save_after=1),
                              str(tmp_path))
    assert info["stored_position"] == data.index(b"\r") + 1
    assert info["restarts"] == 8
    con = sqlite3.connect(db)
    n, distinct = con.execute("SELECT COUNT(*), COUNT(DISTINCT lab_id) FROM "
                              "lem_machine_log WHERE kind = 'run'").fetchone()
    con.close()
    assert distinct == 40 and n > 3 * 40      # replayed, not logged once


def test_the_floor_prediction_moves_with_the_world(v39_root, tmp_path):
    import floor_rehearsal as F
    path, cfg = _cr_bench(tmp_path)
    spec = dict(source=str(path), real=False, config=cfg,
                history=dict(days=8, save_after=1))
    asis = F.run_bench("cr", spec, v39_root, str(tmp_path), "as-is")
    down = F.run_bench("cr", spec, v39_root, str(tmp_path), "downtime")
    assert asis["legacy"]["recovered"] == 0 == asis["v2"]["recovered"]
    assert down["legacy"]["recovered"] == 1 == down["v2"]["recovered"]
    assert down["legacy"]["recovered_lines"][0].split(";")[5] == "DOWNTIME-0001"
