"""Rehearse the first v4 start of the four single_csv benches, end to end.

    python floor_rehearsal.py [--only NAME,...] [--json OUT] [--tmp DIR]

Spec §10.2 predicts 0 recovered on Eraspec, Eraspec NIR and Agilent GC 1. A
prediction about the floor is only as good as the two things it is made of:
the instrument files, and the rows v3.9 left in LabCore. Neither the live
files (two on the Eraspec PC, two on Labsharedrive) nor production LabCore
may be read from here, so this rehearsal builds both as faithfully as this
machine allows, and says exactly what each one is:

  * The FILES are real instrument output where a real copy exists on this
    machine (`SOURCES`): the Eraspec's own LIMS export from the Eraspec PC
    backup (';'-separated, CR-only line ends, 311 prints, two identical
    re-prints, two Gasoline prints of a different shape, a "Test print"), and
    two copies of the GC D2887 webapp's `distill_results.csv` (CRLF, a
    header, 14 AF26 QC standard prints, 39 blanks). The Eraspec's current
    comma template exists here only as a 2-line sample; it is rendered from
    the real 2023 readings and labelled `rendered`.
  * The RECORD is written by the tagged v3.9.0 module's own code
    (`v39_history.py`, in a subprocess): its tail reader, parser,
    corrections, QC verdicts and log inserts, driven over a growing copy of
    the file with the floor's habits measured in phase 1 (G1): a first
    ingest, prints polled one at a time through the day, a LabStation
    restart every morning that replays everything after the stored
    `last_position` (Eraspec: one print in; Agilent GC 1: a stale marker
    ~85 % in; Agilent GC 2: 0, never saved), machine-level correction
    factors that change over the history, and AF26 judged as QC.

Then the module v4's OWN adoption decides, through both roads
(`adoption_rehearsal.rehearse`: the bench's `_adoption_plan` against the
record under today's server, and the server's `adoption_digest` under v4),
in two worlds per bench:

  as-is     v3.9 logged everything up to the upgrade  -> expect 0 recovered
  downtime  one more print made while LabStation was down -> expect exactly 1
  no-qc-lib (Agilent GC 1) as-is, but the bench's QC library has not loaded
            (or AF26 is no longer assigned): its verdicts still match -> 0
  reprocessed (Agilent GC 1) the GC webapp re-processed one injection (a
            Blank) after the last restart and rewrote its row in place
            (distill.py does exactly that); v3.9's tail never saw the new
            numbers, so the record genuinely lacks them -> exactly 1, not 0
  reprocessed-qc (Agilent GC 1) the same for an AF26 injection -> 0, and
            this is a KNOWN LIMIT, not a pass: v3.9 kept no raw reading on a
            verdict under a machine-level factor, so such verdicts are matched
            by count per (standard, test), and the original's verdict
            vouches for the re-processed print

It reads the source files and writes only under --tmp.
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

HOME = os.path.expanduser("~")
ERASPEC_2023 = os.path.join(
    HOME, "Library/Mobile Documents/com~apple~CloudDocs/SERVER/BACK UPS/"
    "Eraspec/Eraspec drivers/lims.csv")
ERASPEC_2024_SAMPLE = os.path.join(
    HOME, "Projects/data-handler/Data Handler 1.05 ISO/csv_parser_configs/"
    "Eraspec/Raw Data/Eraspec_raw_data.csv")
GC_SHARE = os.path.join(HOME, "Projects/gc-share-snapshot-2026-09-25/"
                        "webapp-live/distill_results.csv")
GC_DATA = os.path.join(HOME, "Projects/gc-data/GC2025/GC 2026 GAS/webapp/"
                       "distill_results.csv")

D2887 = "ASTM D2887/D86 - Distillation in Petroleum Products, "
# Baseline (prod/lem_api_machines.json, 2026-10-01): Agilent GC 1's AF26
# specs, no spec-level correction.
AF26 = [("IBP", "2887 IBP", 155.01, 3.43), ("10% Recovery", "2887 T10", 187.63, 1.29),
        ("50% Recovery", "2887 T50", 251.37, 1.04),
        ("90% Recovery", "2887 T90", 334.9, 1.71), ("FBP", "2887 FBP", 363.58, 2.31)]


def cell(i):
    return {"mode": "cell", "index": i, "pattern": "", "clean": []}


def mapping(method, i):
    return {"methods": [method], "selector": cell(i)}


def eraspec_semicolon_config(uid, title):
    """The 2023 LIMS template: label;value pairs. Lab ID cell 5."""
    return {"uid": uid, "title": title, "source_type": "single_csv",
            "delimiter": ";", "lab_id": cell(5),
            "mappings": [mapping("FAME", 14), mapping("D86 IBP", 24),
                         mapping("D86 T10", 27), mapping("D86 T50", 30),
                         mapping("D86 T90", 36), mapping("D86 FBP", 42)]}


def eraspec_comma_config(uid, title):
    """The current comma template (Data Handler's Eraspec parser reorders
    from these cells): Lab ID cell 5, readings from cell 9 on."""
    return {"uid": uid, "title": title, "source_type": "single_csv",
            "delimiter": ",", "lab_id": cell(5),
            "mappings": [mapping("API", 9), mapping("Density", 11),
                         mapping("FAME", 13), mapping("D86 IBP", 20),
                         mapping("D86 T10", 21), mapping("D86 T50", 22),
                         mapping("D86 T90", 23), mapping("D86 FBP", 24)]}


def gc_config(uid, title, header):
    """Lab ID cell 0; the D2887 (or, on the gas webapp, D7096) IBP / T10 /
    T50 / T90 / FBP columns, and D86 T50 where the file has it."""
    cols = header.split(",")
    method = cols[2].split(" ")[0]                  # "2887" or "D7096"
    maps = [mapping(D2887 + name, cols.index(col.replace("2887", method)))
            for name, col, _e, _s in AF26]
    if "D86 T50" in cols:
        maps.append(mapping("D86 T50", cols.index("D86 T50")))
    return {"uid": uid, "title": title, "source_type": "single_csv",
            "delimiter": ",", "lab_id": cell(0), "mappings": maps}


def af26_tests():
    return [{"name": D2887 + name, "value_col": D2887 + name, "expected": e,
             "std_dev": s, "k": 2.0, "sample_id": "AF26"}
            for name, _c, e, s in AF26]


_END = re.compile(rb"\r\n|\r|\n")


def print_ends(data):
    """Byte offsets just past each line end — where v3.9 can stop reading."""
    return [m.end() for m in _END.finditer(data)]


def render_comma_eraspec(src_2023, sample):
    """The 2023 readings in the current comma template (labelled rendered)."""
    with open(sample, "rb") as f:
        lines = f.read().decode("utf-8").splitlines()
    proto = next(l for l in lines if l.startswith("ERASPEC,Diesel")).split(",")
    with open(src_2023, "rb") as f:
        old = [l.decode("utf-8") for l in f.read().split(b"\r") if l.strip()]
    out = ["RawData"]
    for line in old:
        c = line.split(";")
        if len(c) != 47:
            continue
        row = list(proto)
        row[3], row[4], row[5], row[6] = c[3], c[4], c[5], c[6]
        row[13] = c[14].strip().rjust(5)
        for dst, srcc in ((20, 24), (21, 27), (22, 30), (23, 36), (24, 42)):
            row[dst] = c[srcc]
        out.append(",".join(row))
    return ("\r\n".join(out) + "\r\n").encode("utf-8")


def history_steps(data, *, days, save_after, factors=(), tests=None,
                  start_prints=1):
    """v3.9 on the floor over `days` days: the first ingest of the first
    `start_prints` prints, then each day a restart (replay from the stored
    marker) and the day's prints polled one at a time. `save_after`: the
    print count after which Settings → OK stored the marker (None: never).
    `factors`: [(print_count, {test: offset})] machine-level factor changes."""
    ends = print_ends(data)
    if not ends or ends[-1] != len(data):
        ends.append(len(data))
    steps = []
    if tests:
        steps.append(["tests", tests])
    fchanges = dict(factors)
    if 0 in fchanges:
        steps.append(["factor", fchanges[0]])
    steps += [["grow", ends[start_prints - 1]], ["poll"]]
    if save_after is not None and save_after <= start_prints:
        steps.append(["save"])
    rest = list(range(start_prints, len(ends)))
    per_day = max(1, -(-len(rest) // days))
    for d in range(0, len(rest), per_day):
        steps += [["clock", 60 * 14], ["restart"], ["poll"]]
        for i in rest[d:d + per_day]:
            if i in fchanges:
                steps.append(["factor", fchanges[i]])
            steps += [["clock", 5], ["grow", ends[i]], ["poll"]]
            if save_after is not None and i + 1 == save_after:
                steps.append(["save"])
    return steps


def write_record(code_root, source, config, steps, work_dir):
    """Run v39_history.py under the v3.9.0 tree. Returns (db path, info)."""
    plan = {"module_dir": os.path.join(code_root, "LEM Station Module"),
            "source": source, "work": os.path.join(work_dir, "v39-view.csv"),
            "db": os.path.join(work_dir, "labcore-copy.sqlite3"),
            "config": config, "start": "2026-07-15T08:00:00", "steps": steps}
    if os.path.exists(plan["db"]):
        os.remove(plan["db"])
    plan_path = os.path.join(work_dir, "plan.json")
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
    out = subprocess.run([sys.executable, os.path.join(HERE, "v39_history.py"),
                          plan_path], capture_output=True, text=True, env=env,
                         cwd=work_dir, timeout=600)
    if out.returncode != 0:
        raise RuntimeError("v3.9 history failed (exit %d): %s"
                           % (out.returncode, (out.stderr or out.stdout)[-800:]))
    return plan["db"], json.loads(out.stdout.strip().splitlines()[-1])


def v39_tree(tmp):
    from gharness import target
    root = os.path.join(tmp, "v3.9.0")
    if not os.path.isdir(os.path.join(root, "LEM Station Module")):
        os.makedirs(root, exist_ok=True)
        target.extract_tag("v3.9.0", root)
    return root


def benches():
    """name -> how its file and its v3.9 history are made."""
    def gc_header(path):
        with open(path, "rb") as f:
            return f.read().split(b"\r\n", 1)[0].decode("utf-8")
    out = {}
    if os.path.exists(ERASPEC_2023):
        out["Eraspec (2023 LIMS export, real)"] = dict(
            source=ERASPEC_2023, real=True,
            config=eraspec_semicolon_config("ae05c9c117d7", "Eraspec"),
            history=dict(days=8, save_after=1))
    if os.path.exists(ERASPEC_2023) and os.path.exists(ERASPEC_2024_SAMPLE):
        out["Eraspec NIR (comma template, rendered)"] = dict(
            render=lambda: render_comma_eraspec(ERASPEC_2023, ERASPEC_2024_SAMPLE),
            real=False,
            config=eraspec_comma_config("5345176988c2", "Eraspec NIR"),
            history=dict(days=8, save_after=4))
    if os.path.exists(GC_SHARE):
        out["Agilent GC 1 (distill_results.csv, share copy)"] = dict(
            source=GC_SHARE, real=True,
            config=gc_config("bf8e64b59f12", "Agilent GC 1", gc_header(GC_SHARE)),
            history=dict(days=10, save_after=68, tests=af26_tests(),
                         factors=[(0, {"D86 T50": 0.5}),
                                  (40, {"D86 T50": 1.0,
                                        D2887 + "10% Recovery": 0.4})]),
            today=dict(corrections={"D86 T50": 1.5, D2887 + "10% Recovery": 0.7},
                       tests=af26_tests()),
            extra_worlds=("no-qc-lib", "reprocessed", "reprocessed-qc"),
            reprocess_row=80, reprocess_qc_row=75)
    if os.path.exists(GC_DATA):
        out["Agilent GC 2 (distill_results.csv, gc-data copy)"] = dict(
            source=GC_DATA, real=True,
            config=gc_config("3afa991a66e9", "Agilent GC 2", gc_header(GC_DATA)),
            history=dict(days=4, save_after=None))
    return out


def downtime_print(data, config):
    """One more print made while LabStation was down: the file's last
    READING again (its own line end), under a new Lab ID, appended."""
    sep = config["delimiter"].encode()
    idx = config["lab_id"]["index"]
    starts = [0] + print_ends(data)
    for i in range(len(starts) - 2, -1, -1):
        piece = data[starts[i]:starts[i + 1]]
        m = _END.search(piece)
        body, nl = (piece[:m.start()], m.group()) if m else (piece, b"\n")
        cells = body.split(sep)
        if len(cells) > max(idx, 6):
            cells[idx] = b"DOWNTIME-0001"
            tail = data if data.endswith((b"\n", b"\r")) else data + nl
            return tail + sep.join(cells) + nl
    raise ValueError("no reading in the file to copy")


EXPECT = {"as-is": 0, "downtime": 1, "no-qc-lib": 0, "reprocessed": 1,
          "reprocessed-qc": 0}


def reprocess_row(data, index):
    """distill.py's re-process: the row of that injection is replaced and the
    whole file rewritten (same header, same order, CRLF), new numbers."""
    lines = data.split(b"\r\n")
    cells = lines[index].split(b",")
    cells[2:15] = [(b"%.2f" % (float(c) + 0.37)) if c.strip() else c
                   for c in cells[2:15]]
    lines[index] = b",".join(cells)
    return b"\r\n".join(lines)


def run_bench(name, spec, code_root, tmp, world):
    import adoption_rehearsal as R
    mod = R.load_module()
    work = os.path.join(tmp, re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-"), world)
    os.makedirs(work, exist_ok=True)
    if spec.get("render"):
        data = spec["render"]()
    else:
        with open(spec["source"], "rb") as f:
            data = f.read()
    logged = os.path.join(work, "logged-by-v39.csv")
    with open(logged, "wb") as f:
        f.write(data)
    db, info = write_record(code_root, logged, spec["config"],
                            history_steps(data, **spec["history"]), work)
    if world == "downtime":
        final = downtime_print(data, spec["config"])
    elif world == "reprocessed":
        final = reprocess_row(data, spec["reprocess_row"])
    elif world == "reprocessed-qc":
        final = reprocess_row(data, spec["reprocess_qc_row"])
    else:
        final = data
    path = os.path.join(work, "at-upgrade.csv")
    with open(path, "wb") as f:
        f.write(final)
    machine = mod.Machine.from_dict(dict(spec["config"], csv_path=path))
    today = spec.get("today") or {}
    machine.corrections = dict(today.get("corrections") or {})
    machine.tests = [] if world == "no-qc-lib" else \
        [mod.TestSpec.from_dict(t) for t in today.get("tests") or ()]
    rows = R.load_rows(db, machine.uid)
    out = R.rehearse(path, machine, rows, info["stored_position"])
    kinds = {}
    con = sqlite3.connect(db)
    for k, n in con.execute("SELECT kind, COUNT(*) FROM lem_machine_log GROUP BY kind"):
        kinds[k] = n
    con.close()
    out.update(world=world, bench=name, real_file=spec["real"],
               v39=dict(info, rows_by_kind=kinds),
               expect_recovered=EXPECT[world])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only")
    ap.add_argument("--json")
    ap.add_argument("--tmp")
    a = ap.parse_args(argv)
    tmp = a.tmp or tempfile.mkdtemp(prefix="floor-rehearsal-")
    code_root = v39_tree(tmp)
    results, bad = [], 0
    for name, spec in benches().items():
        if a.only and not any(o.strip() in name for o in a.only.split(",")):
            continue
        for world in ("as-is", "downtime") + tuple(spec.get("extra_worlds", ())):
            r = run_bench(name, spec, code_root, tmp, world)
            results.append(r)
            ok = all((r[road].get("recovered") == r["expect_recovered"])
                     for road in ("legacy", "v2"))
            bad += not ok
            print("%-50s %-8s %s  v39 rows %-6s legacy %s/%s/%s reads %s | v2 %s/%s/%s"
                  % (name, world, "OK " if ok else "BAD", r["recorded_rows"],
                     r["legacy"].get("path"), r["legacy"].get("matched"),
                     r["legacy"].get("recovered"), r["legacy"].get("labcore_reads"),
                     r["v2"].get("path"), r["v2"].get("matched"),
                     r["v2"].get("recovered")))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=1, default=str)
    print("benches x worlds: %d, off the prediction: %d" % (len(results), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
