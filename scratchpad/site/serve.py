#!/usr/bin/env python3
"""serve.py — the live board for the LEM 3D floor build.

    python3 serve.py [--port 5610] [--host 0.0.0.0]

Two things in one process: a static server for `index.html`, and a background
thread that rebuilds `status.json` every few seconds from whatever the build has
actually left on disk. The page polls that file, so it is live without anyone
republishing anything.

Nothing here is authored by hand except `notes.json` — the running commentary.
Everything else is scraped:

  agents      the workflow journals under ~/.claude/projects/.../workflows/,
              one JSONL per run: a `started` line per agent, a `result` line
              when it finishes. Running = started minus finished.
  soak        the newest /tmp/soak*.json the harness wrote
  shots       the newest screenshots, with the fps/draw/triangle sidecars
  tests       the last recorded pytest count (written by the build, not run
              here — running the suite on a page load would be absurd)
"""
import argparse
import glob
import json
import os
import re
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SCRATCH = os.path.dirname(HERE)
HOME = os.path.expanduser("~")
WORKFLOW_GLOB = os.path.join(
    HOME, ".claude", "projects", "*", "*", "subagents", "workflows", "wf_*")
SHOTS = os.path.join(SCRATCH, "shots")
SITE_SHOTS = os.path.join(HERE, "shots")


def _read_journal(path):
    """One workflow run → what its agents are doing."""
    started, done, labels = 0, 0, []
    jl = os.path.join(path, "journal.jsonl")
    if not os.path.exists(jl):
        return None
    try:
        with open(jl) as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                kind = rec.get("type")
                if kind == "started":
                    started += 1
                elif kind == "result":
                    done += 1
    except OSError:
        return None
    # The script file next to the run carries the human-readable name.
    name = os.path.basename(path)
    scripts = glob.glob(os.path.join(
        HOME, ".claude", "projects", "*", "*", "workflows", "scripts",
        "*" + name + ".js"))
    title = ""
    if scripts:
        try:
            src = open(scripts[0]).read(4000)
            m = re.search(r"description:\s*'([^']+)'", src)
            if m:
                title = m.group(1)
            for lm in re.finditer(r"label:\s*'([^']+)'", src):
                labels.append(lm.group(1))
        except OSError:
            pass
    # Liveness must come from the AGENT transcripts, not the journal. The
    # journal is only appended to when an agent *finishes*, so a run whose
    # agents have been working for twenty minutes looks dead by that measure —
    # which made the board report zero running while three were mid-flight.
    # The per-agent .jsonl files are written continuously; take the newest of
    # anything in the run directory.
    mtime = os.path.getmtime(jl)
    try:
        for entry in os.scandir(path):
            if entry.is_file():
                mtime = max(mtime, entry.stat().st_mtime)
    except OSError:
        pass
    open_agents = max(0, started - done)
    return {"id": name, "title": title, "started": started, "done": done,
            "open": open_agents, "labels": labels[:12], "mtime": mtime}


def _newest_soak():
    best, best_t = None, 0
    for path in glob.glob("/tmp/soak*.json") + glob.glob("/tmp/s-*.json") + \
            glob.glob("/tmp/s.json"):
        try:
            t = os.path.getmtime(path)
        except OSError:
            continue
        if t > best_t:
            try:
                best, best_t = json.load(open(path)), t
            except (OSError, ValueError):
                continue
    if not best:
        return None
    return {"summary": best.get("summary"), "pass": best.get("pass"),
            "stats": best.get("stats"),
            "faults": (best.get("faults") or [])[:6],
            "structure": (best.get("structure") or [])[:6],
            "at": time.strftime("%H:%M:%S", time.localtime(best_t))}


def _newest_shots(n=6):
    """Copy the newest screenshots into the site so they can be served, and
    carry their measurements across."""
    os.makedirs(SITE_SHOTS, exist_ok=True)
    pngs = sorted(glob.glob(os.path.join(SHOTS, "*.png")),
                  key=os.path.getmtime, reverse=True)[:n]
    out = []
    for src in pngs:
        base = os.path.basename(src)
        dst = os.path.join(SITE_SHOTS, base)
        try:
            if not os.path.exists(dst) or \
                    os.path.getmtime(dst) < os.path.getmtime(src):
                with open(src, "rb") as a, open(dst, "wb") as b:
                    b.write(a.read())
        except OSError:
            continue
        meta = {}
        side = src[:-4] + ".json"
        if os.path.exists(side):
            try:
                d = json.load(open(side))
                meta = {"draws": d.get("drawCalls"), "tris": d.get("triangles"),
                        "fps": round(d.get("fpsMean") or 0),
                        "errors": len(d.get("errors") or [])}
            except (OSError, ValueError):
                pass
        out.append({"name": base, "meta": meta,
                    "at": time.strftime("%H:%M", time.localtime(
                        os.path.getmtime(src)))})
    return out


def _notes():
    path = os.path.join(HERE, "notes.json")
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return []


def build_status():
    runs = []
    for path in glob.glob(WORKFLOW_GLOB):
        r = _read_journal(path)
        if r:
            runs.append(r)
    runs.sort(key=lambda r: r["mtime"], reverse=True)
    # An agent with a `started` line and no `result` is only RUNNING if its
    # journal is still being written to. Several older runs were stopped
    # part-way and keep their unmatched `started` lines forever; counting those
    # made the board claim twelve agents at work when three were. A board that
    # overstates is the same failure as a check that silently passes.
    now = time.time()
    FRESH = 15 * 60
    for r in runs:
        stale = (now - r["mtime"]) > FRESH
        r["running"] = 0 if stale else r["open"]
        r["abandoned"] = r["open"] if stale else 0
        r.pop("mtime", None)
        r.pop("open", None)
    return {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "runs": runs[:8],
        "agentsRunning": sum(r["running"] for r in runs),
        "agentsAbandoned": sum(r["abandoned"] for r in runs),
        "soak": _newest_soak(),
        "shots": _newest_shots(),
        "notes": _notes(),
    }


def refresher(period):
    while True:
        try:
            status = build_status()
            tmp = os.path.join(HERE, ".status.tmp")
            with open(tmp, "w") as fh:
                json.dump(status, fh)
            os.replace(tmp, os.path.join(HERE, "status.json"))
        except Exception as exc:                    # never let the board die
            print("refresh failed:", exc, flush=True)
        time.sleep(period)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def end_headers(self):
        # The page polls; a cached status.json would freeze the board.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *a):
        pass                                        # quiet


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=5610)
    ap.add_argument("--period", type=float, default=4.0)
    args = ap.parse_args()

    threading.Thread(target=refresher, args=(args.period,), daemon=True).start()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"live board on http://{args.host}:{args.port}/  "
          f"(refreshing every {args.period}s)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
