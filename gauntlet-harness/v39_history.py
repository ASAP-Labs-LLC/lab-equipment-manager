"""Write the record a v3.9.0 bench would have left, with v3.9.0's OWN code.

    python v39_history.py PLAN.json         (run with the v3.9.0 tree on sys.path)

The floor rehearsal (`floor_rehearsal.py`) needs the rows LabCore holds for
a file bench in the exact shape v3.9.0 wrote them — including its replays
after every LabStation restart, its decoding of each tail chunk, and its QC
verdict rows. Re-implementing any of that would rehearse the re-implementation,
so this script imports the TAGGED v3.9.0 module and drives its functions in
the order its poll does (`_ingest_single` → `parse_print` → `to_row` →
`apply_row_corrections` → `_queue_run_events` → `_log_event` →
`build_log_insert`), against a growing copy of the instrument file.

PLAN.json:
  {"module_dir": ".../LEM Station Module",      # the v3.9.0 tree
   "source": "copy of the final instrument file",
   "work": "path of the growing file v3.9 reads",
   "db": "sqlite file to write lem_machine_log into",
   "config": {... lem_machine_config.config ...},
   "start": "2026-07-15T08:00:00",
   "steps": [["grow", bytes_total], ["poll"], ["restart"], ["save"],
             ["factor", {"test": offset}], ["tests", [spec dicts]],
             ["clock", minutes]]}

Prints {"stored_position": N, "rows": n, "polls": p, "restarts": r} as JSON.
It reads only the plan's files and writes only `work` and `db`.
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta


def main(plan_path):
    with open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)
    sys.path.insert(0, plan["module_dir"])
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import lem_station_module as v39
    where = os.path.realpath(v39.__file__)
    if not where.startswith(os.path.realpath(plan["module_dir"])):
        raise SystemExit("v3.9 module loaded from %s, not the tagged tree" % where)

    with open(plan["source"], "rb") as f:
        final = f.read()
    machine = v39.Machine.from_dict(plan["config"])
    machine.csv_path = plan["work"]
    machine.last_position = 0
    machine.corrections = {}
    stored = 0
    now = datetime.fromisoformat(plan["start"])
    pending = []

    class Bench:                       # what `_queue_run_events` reaches
        _calibration_epoch = None

        def _current_operator(self):
            return None

        def _log_event(self, kind, lab_id="", test_name="", value="",
                       detail=None, now=None):
            pending.append(v39.build_log_insert(
                machine.uid, kind, now or datetime.now(), lab_id=lab_id,
                test_name=test_name, value=value, detail=detail))

    bench = Bench()
    db = sqlite3.connect(plan["db"])
    db.execute("CREATE TABLE IF NOT EXISTS lem_machine_log (id INTEGER "
               "PRIMARY KEY AUTOINCREMENT, machine_uid TEXT, ts TEXT, kind "
               "TEXT, lab_id TEXT, test_name TEXT, value TEXT, detail TEXT)")
    with open(plan["work"], "wb"):
        pass
    polls = restarts = 0
    for step in plan["steps"]:
        op = step[0]
        if op == "grow":
            with open(plan["work"], "wb") as f:
                f.write(final[:int(step[1])])
        elif op == "clock":
            now += timedelta(minutes=float(step[1]))
        elif op == "factor":
            machine.corrections = dict(step[1])
        elif op == "tests":
            machine.tests = [v39.TestSpec.from_dict(t) for t in step[1]]
        elif op == "save":                    # Settings → OK: _publish_config
            stored = machine.last_position
        elif op == "restart":                 # LabStation restarts: the marker
            machine.last_position = stored    # comes back from LabCore
            restarts += 1
        elif op == "poll":
            polls += 1
            _m, prints, error = v39.LEMStationModule._ingest_single(bench, machine)
            if error:
                raise SystemExit("v3.9 poll: " + error)
            rows = []
            for text in prints:
                result = v39.parse_print(machine, text)
                if not result.lab_id and not result.values:
                    continue
                rows.append(result.to_row(now))
            rows = v39.apply_row_corrections(rows, machine.corrections)
            if rows:
                v39.LEMStationModule._queue_run_events(bench, machine, rows, now)
            for sql, args in pending:
                db.execute(sql, args)
            pending.clear()
        else:
            raise SystemExit("unknown step %r" % (step,))
    db.commit()
    n = db.execute("SELECT COUNT(*) FROM lem_machine_log").fetchone()[0]
    db.close()
    print(json.dumps({"stored_position": stored, "rows": n, "polls": polls,
                      "restarts": restarts}))


if __name__ == "__main__":
    main(sys.argv[1])
