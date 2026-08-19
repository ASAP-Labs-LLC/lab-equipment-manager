"""Where the real files live.

The harness imports the REAL LabStation.pyw, the REAL LabCore.py and the REAL
lem_station_module.py — never a copy. If the LabLink clone isn't on this
machine the whole replay suite skips instead of failing, so the normal LEM
test run stays green on a bench that only has this folder.

Override with LABLINK_ROOT=/path/to/LabLink-1 when the clone lives elsewhere.
"""
import os
from pathlib import Path

LEM_FILE = Path(__file__).resolve().parents[2] / "lem_station_module.py"

_DEFAULT_ROOTS = (
    Path("/Users/rynatical/Documents/Projects/ASAP Labs/Lab Link/LabLink-1"),
    Path.home() / "Documents" / "Projects" / "ASAP Labs" / "Lab Link" / "LabLink-1",
)


def _roots():
    env = os.environ.get("LABLINK_ROOT", "").strip()
    if env:
        # Explicit wins outright — otherwise a typo would silently fall back to
        # a clone on the dev machine and the suite would look like it ran.
        yield Path(env)
        return
    for root in _DEFAULT_ROOTS:
        yield root


def labstation_pyw():
    for root in _roots():
        p = root / "apps" / "LabStation" / "src" / "LabStation.pyw"
        if p.is_file():
            return p
    return None


def labcore_py():
    for root in _roots():
        p = root / "apps" / "LabCore" / "src" / "LabCore.py"
        if p.is_file():
            return p
    return None


def missing_reason():
    """Human-readable skip reason, or "" when everything is present."""
    gaps = []
    if labstation_pyw() is None:
        gaps.append("LabStation.pyw")
    if labcore_py() is None:
        gaps.append("LabCore.py")
    if not LEM_FILE.is_file():
        gaps.append("lem_station_module.py")
    if not gaps:
        return ""
    return ("LabLink clone not present (missing: " + ", ".join(gaps) +
            "). Set LABLINK_ROOT to run the replay harness.")
