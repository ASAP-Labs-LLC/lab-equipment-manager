"""Point every place LEM writes at one temp root, BEFORE anything is imported.

Phase 1 leaked `lem_latest_Bench b1.csv` into the real
~/AppData/Roaming/LabLink/apps/LabStation because `labstation_dir()` falls back
to the home directory when APPDATA is unset — which it always is on a Mac.
A gate that writes into the profile it runs from is a gate whose second run is
not its first. So this module sets, for the whole process:

  APPDATA, LOCALAPPDATA     -> <root>/AppData/{Roaming,Local}   (module data dir)
  LEM_JOURNAL_DIR           -> <root>/journal                   (v4 bench journal)
  LEM_STORE_PATH            -> <root>/store/lem_store.sqlite3   (v4 server store)
  LEM_DATA_DIR              -> <root>/data                      (server log, docs)
  HOME, XDG_*               -> <root>/home  (anything that still reaches for ~)
  TMPDIR / tempfile.tempdir -> <root>/tmp   (every mkdtemp in the baseline harness)

and records the REAL home first, so the gate can check afterwards that nothing
under it changed (`snapshot_tree` / `diff_snapshots`).

`activate()` is idempotent per process and returns the root.
"""
import os
import sys
import tempfile

_STATE = {}

REDIRECTED = ("APPDATA", "LOCALAPPDATA", "LEM_JOURNAL_DIR", "LEM_STORE_PATH",
              "LEM_DATA_DIR", "HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
              "XDG_CACHE_HOME", "TMPDIR")


def real_home():
    return _STATE.get("real_home") or os.path.expanduser("~")


def root():
    return _STATE.get("root")


def activate(base=None, prefix="lem-gate-"):
    if _STATE.get("root"):
        return _STATE["root"]
    _STATE["real_home"] = os.path.expanduser("~")
    base = base or os.environ.get("LEM_GATE_TMP") or tempfile.gettempdir()
    os.makedirs(base, exist_ok=True)
    r = os.path.realpath(tempfile.mkdtemp(prefix=prefix, dir=base))
    paths = {
        "APPDATA": os.path.join(r, "AppData", "Roaming"),
        "LOCALAPPDATA": os.path.join(r, "AppData", "Local"),
        "LEM_JOURNAL_DIR": os.path.join(r, "journal"),
        "LEM_DATA_DIR": os.path.join(r, "data"),
        "HOME": os.path.join(r, "home"),
        "XDG_CONFIG_HOME": os.path.join(r, "home", ".config"),
        "XDG_DATA_HOME": os.path.join(r, "home", ".local", "share"),
        "XDG_CACHE_HOME": os.path.join(r, "home", ".cache"),
        "TMPDIR": os.path.join(r, "tmp"),
    }
    for p in paths.values():
        os.makedirs(p, exist_ok=True)
    os.makedirs(os.path.join(r, "store"), exist_ok=True)
    paths["LEM_STORE_PATH"] = os.path.join(r, "store", "lem_store.sqlite3")
    os.environ.update(paths)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    # The module refuses a typo'd fault-point name under test.
    os.environ["LEM_FAULT_POINTS_STRICT"] = "1"
    # No LabCore URL is ever real here: port 9 is discard, and the harness
    # never makes an HTTP call to LabCore anyway (netguard refuses it).
    os.environ["LABCORE_URL"] = "http://127.0.0.1:9"
    tempfile.tempdir = paths["TMPDIR"]
    sys.dont_write_bytecode = True
    _STATE["root"] = r
    _STATE["paths"] = paths
    return r


def paths():
    return dict(_STATE.get("paths") or {})


# ── write audit ──────────────────────────────────────────────────────────────

def snapshot_tree(top, skip=()):
    """{relative path: (size, mtime_ns)} for every file under `top`. A missing
    `top` is an empty snapshot AND a flag, because "the folder was not there"
    is a fact the diff must be able to show (it appearing is a write)."""
    out = {}
    if not os.path.isdir(top):
        return {"__missing__": True}
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames[:] = [d for d in dirnames
                       if os.path.join(dirpath, d) not in skip
                       and d not in (".git",)]
        for f in filenames:
            p = os.path.join(dirpath, f)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            out[os.path.relpath(p, top)] = (st.st_size, st.st_mtime_ns)
    return out


def diff_snapshots(before, after):
    added = sorted(k for k in after if k not in before)
    removed = sorted(k for k in before if k not in after)
    changed = sorted(k for k in after if k in before and after[k] != before[k])
    return {"added": added, "removed": removed, "changed": changed}
