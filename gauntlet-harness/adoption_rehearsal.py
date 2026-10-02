"""Rehearse §10.2's adoption on a COPY of a bench's instrument file.

    python adoption_rehearsal.py --file COPY.csv --rows LOG.sqlite3 --uid UID
                                 --config CONFIG.json [--last-position N]
                                 [--mtime ISO] [--json OUT]

Before module v4 starts on a single_csv bench for real, this says what its
first start will decide — with the module's OWN code (`adoption_boundary`,
`adoption_line`, `plan_adoption`, and the bench's `_adoption_plan` with its
read budget), not a re-implementation:

  * the boundary the stored `last_position` gives;
  * under today's v3.9 server (legacy): the fast path or the full match, the
    lines matched / recovered / history / presumed / unchecked, and how many
    LabCore reads adoption would make (the budget is 15);
  * under a v4 server (LEM's digest): the same plan with every key known.

Inputs are copies, never the live system:
  --file    a copy of the instrument file (e.g. Eraspec.csv off the bench PC);
  --rows    an SQLite file holding the bench's recorded rows — the LEM
            server's log mirror (`log` table) or a LabCore backup copy
            (`lem_machine_log`); only this uid's run/qc rows are used;
  --config  the bench's `lem_machine_config.config` JSON (the parser: Lab ID
            selector, mappings, QC specs, corrections);
  --mtime   the file's modification time on the bench (a copy's own mtime is
            the copy's), used only when nothing in the file matches.

Spec §10.2 predicts 0 recovered on Eraspec, Eraspec NIR and Agilent GC 1,
whose daily replays logged everything (G1). This tool is how that prediction
is checked on Ryan's copies of the live files and a LabCore backup copy; it
reads files and nothing else, and writes only --json. Until those copies
exist, `floor_rehearsal.py` runs it on the real instrument files this
machine holds and a record written by v3.9.0's own code.
"""
import argparse
import json
import os
import sqlite3
import sys
import types
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.join(os.path.dirname(HERE), "LEM Station Module")
WEB_DIR = os.path.join(os.path.dirname(HERE), "LEM Web Server")


def load_module():
    if MODULE_DIR not in sys.path:
        sys.path.insert(0, MODULE_DIR)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import lem_station_module as mod
    return mod


def load_rows(path, uid):
    """This uid's run/qc rows from a log-mirror or LabCore copy. A file that
    cannot be read raises: a failed read is never an empty record."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    con.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        table = "lem_machine_log" if "lem_machine_log" in tables else \
            "log" if "log" in tables else None
        if table is None:
            raise ValueError("%s has neither lem_machine_log nor log" % path)
        return [dict(r) for r in con.execute(
            "SELECT ts, kind, lab_id, test_name, value, detail FROM %s WHERE "
            "machine_uid = ? AND kind IN ('run', 'qc')" % table, [uid])]
    finally:
        con.close()


def _record_db(mod, rows, uid):
    """The rows as a LabCore-shaped lem_machine_log with production's
    indexes, so the bench's own queries run on them unchanged."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(mod.LOG_TABLE_DDL)
    for ddl in mod.LOG_INDEX_DDL:
        db.execute(ddl)
    db.execute("CREATE INDEX idx_lem_log_lab_ts ON lem_machine_log(lab_id, ts)")
    db.executemany("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
                   "test_name, value, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   [(uid, r["ts"], r["kind"], r["lab_id"], r["test_name"],
                     r["value"], r["detail"]) for r in rows])
    return db


def rehearse(file_path, machine, rows, last_position=0, mtime=None):
    mod = load_module()
    with open(file_path, "rb") as f:
        data = f.read()
    source = mod.SingleCsvSource(file_path, None)
    scan = source.scan_for_adoption()
    if scan.get("partial") is not None:
        # A copy holds still: the bench takes the last line whole, as here.
        scan = source.scan_for_adoption(final_complete=True)
    if mtime is not None:
        scan["mtime"] = mtime.timestamp()
    boundary = mod.adoption_boundary(data, last_position)
    # As the bench sees them: parsed after the boundary, presumed before it
    # (`plan_adoption` re-parses the few presumed lines that may be a QC
    # standard's print).
    lines = mod.adoption_lines(machine, scan["lines"], boundary)
    readings = [l for l in lines if l.run_key]
    firsts = sorted(str(r["ts"]) for r in rows if r.get("ts"))
    first = firsts[0] if firsts else None
    out = {"file": os.path.basename(file_path), "bytes": len(data),
           "lines": len(lines), "readings": len(readings),
           "boundary": boundary,
           "readings_after_boundary": sum(1 for l in readings
                                          if l.offset >= boundary),
           "recorded_rows": len(rows), "first_ingest": first,
           "history": bool(rows)}
    if not rows:
        out["legacy"] = out["v2"] = {"decision": "no history: the file is "
                                     "read from the top, every line live"}
        return out

    # ── legacy: the bench's own `_adoption_plan`, against the copy ──
    db = _record_db(mod, rows, machine.uid)
    reads = []

    def read_sql(sql, args=None, timeout=None):
        reads.append(sql)
        try:
            return {"rows": [dict(r) for r in db.execute(sql, list(args or []))]}
        except sqlite3.Error as exc:
            return {"error": str(exc)}
    saved = mod.__dict__.get("labcore_read_sql")
    mod.__dict__["labcore_read_sql"] = read_sql
    try:
        bench = types.SimpleNamespace()
        bench._adoption_ask = types.MethodType(
            mod.LEMStationModule._adoption_ask, bench)
        state = {"key": source.key, "reads": 1, "rows": [], "asked": set(),
                 "road": "labcore", "history": True, "first": first}
        plan = mod.LEMStationModule._adoption_plan(bench, machine, state, lines,
                                                   boundary, scan)
    finally:
        if saved is None:
            mod.__dict__.pop("labcore_read_sql", None)
        else:
            mod.__dict__["labcore_read_sql"] = saved
    out["legacy"] = _plan_out(plan, labcore_reads=state["reads"])
    if plan is None:
        out["legacy"]["error"] = state.get("error")

    # ── v2: the SERVER's digest (bench_api.adoption_digest, the endpoint's
    # own code), read by the bench exactly as `_adoption_history` reads it ──
    digest = json.loads(json.dumps(load_server().adoption_digest(
        rows, datetime.now())))          # as it crosses the wire
    problem = mod.adoption_digest_problem(digest)
    if problem:
        raise ValueError("the server's digest would not be adopted on: " + problem)
    state = dict(mod.adoption_state_from_digest(digest), key=source.key,
                 reads=0)
    out["v2"] = _plan_out(mod.LEMStationModule._adoption_plan(
        bench, machine, state, lines, boundary, scan), labcore_reads=0)
    out["unreadable_rows"] = sum(1 for r in rows
                                 if mod.legacy_row_adoption_key(r) is None)
    out["unreadable_labs"] = len(digest["unreadable_labs"])
    return out


def load_server():
    if WEB_DIR not in sys.path:
        sys.path.insert(0, WEB_DIR)
    import bench_api
    return bench_api


def _plan_out(plan, labcore_reads):
    if plan is None:
        return {"decision": "the record could not be asked; adoption waits",
                "labcore_reads": labcore_reads}
    return {"path": plan.kind, "matched": plan.matched,
            "recovered": len(plan.recovered),
            "pre_history_lines": plan.pre_history, "presumed": plan.presumed,
            "unchecked": plan.unchecked, "unreadable": plan.unreadable,
            "not_readings": plan.other,
            "labcore_reads": labcore_reads,
            "recovered_lines": [l.text for l in plan.recovered[:20]]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", required=True)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--uid", required=True)
    ap.add_argument("--config", required=True,
                    help="lem_machine_config.config JSON (file path)")
    ap.add_argument("--last-position", type=int, default=None,
                    help="default: the config's own last_position")
    ap.add_argument("--mtime", help="the file's mtime on the bench (ISO)")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    mod = load_module()
    with open(a.config, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("uid", a.uid)
    machine = mod.Machine.from_dict(cfg)
    pos = machine.last_position if a.last_position is None else a.last_position
    result = rehearse(a.file, machine, load_rows(a.rows, a.uid), pos,
                      datetime.fromisoformat(a.mtime) if a.mtime else None)
    text = json.dumps(result, indent=1, default=str)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
