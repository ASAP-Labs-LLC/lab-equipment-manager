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
