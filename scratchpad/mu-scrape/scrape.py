#!/usr/bin/env python3
"""Scrape QC-standard runs out of ASAP Lab Results for uncertainty estimation."""
import csv, json, os, re, sys, glob, datetime

SHARE = os.path.expanduser("~/mnt/Labsharedrive")
CFG   = os.path.join(SHARE, "Ryan C/LEM - Lab Equipment Manager/V4.0.3.1 - Beta Stable/lab_manager_config.json")
OUT   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qc_rows.jsonl")

cfg = json.load(open(CFG))
SAMPLES = {}
for s in cfg.get("samples", []):
    sid = str(s.get("sample_id_val") or "").strip().upper()
    if sid:
        SAMPLES[sid] = {"name": s.get("name"), "tests": s.get("tests", [])}
BOXES = [b for b in cfg.get("boxes", []) if b.get("csv_path")]

def unc(p):
    """//asapserver/Labsharedrive/X -> local mount"""
    p = p.replace("\\", "/")
    m = re.search(r"/Labsharedrive/(.*)$", p, re.I)
    return os.path.join(SHARE, m.group(1)) if m else None

DATE_PATS = [
    (re.compile(r"(20\d\d)[-_.](\d{1,2})[-_.](\d{1,2})"), lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3)))),
    (re.compile(r"(\d{1,2})[-_.](\d{1,2})[-_.](20\d\d)"), lambda m: (int(m.group(3)), int(m.group(1)), int(m.group(2)))),
    (re.compile(r"(\d{1,2})[-_.](\d{1,2})[-_.](\d{2})(?!\d)"), lambda m: (2000+int(m.group(3)), int(m.group(1)), int(m.group(2)))),
]
def date_from_name(fn):
    for pat, f in DATE_PATS:
        m = pat.search(fn)
        if m:
            try:
                y, a, b = f(m)
                return datetime.date(y, a, b).isoformat()
            except Exception:
                pass
    return ""

OP = re.compile(r"Operator\s*[:=]\s*([A-Za-z][A-Za-z .'-]{0,24})")
NUM = re.compile(r"^-?\d+(?:\.\d+)?$")

def files_for(base):
    d = os.path.dirname(base)
    out = []
    if os.path.isfile(base):
        out.append(base)
    for sub in ("Past Data", "Past Data Raw"):
        out += sorted(glob.glob(os.path.join(d, sub, "*.csv")))
    out += sorted(glob.glob(os.path.join(d, "*.csv")))
    seen, uniq = set(), []
    for f in out:
        if f not in seen:
            seen.add(f); uniq.append(f)
    return uniq

def scrape(box, fh):
    base = unc(box["csv_path"])
    if not base:
        return 0, 0
    title = box.get("title") or box.get("uid")
    n_files = n_rows = 0
    for path in files_for(base):
        n_files += 1
        fdate = date_from_name(os.path.basename(path))
        try:
            with open(path, newline="", encoding="utf-8", errors="replace") as fp:
                rd = csv.reader(fp)
                try:
                    hdr = next(rd)
                except StopIteration:
                    continue
                cols = [h.strip() for h in hdr]
                low = [c.lower() for c in cols]
                if "lab id" not in low:
                    continue
                li = low.index("lab id")
                for row in rd:
                    if li >= len(row):
                        continue
                    sid = str(row[li]).strip().upper()
                    if sid not in SAMPLES:
                        continue
                    rec = {}
                    for i, c in enumerate(cols):
                        if i < len(row) and c and row[i] != "":
                            rec[c] = row[i]
                    joined = " ".join(str(v) for v in row)
                    mo = OP.search(joined)
                    fh.write(json.dumps({
                        "machine": title, "uid": box.get("uid"), "sample": sid,
                        "file": os.path.relpath(path, SHARE),
                        "file_date": fdate,
                        "date": rec.get("parsed_date", ""), "time": rec.get("parsed_time", ""),
                        "operator": (mo.group(1).strip() if mo else ""),
                        "row": rec,
                    }) + "\n")
                    n_rows += 1
        except Exception as e:
            print(f"    !! {os.path.basename(path)}: {e}", file=sys.stderr)
    return n_files, n_rows

if __name__ == "__main__":
    want = sys.argv[1:] if len(sys.argv) > 1 else None
    mode = "a" if want else "w"
    with open(OUT, mode) as fh:
        for b in BOXES:
            t = b.get("title") or ""
            if want and t not in want:
                continue
            nf, nr = scrape(b, fh)
            print(f"{t:18s} files={nf:5d} qc_rows={nr}")
