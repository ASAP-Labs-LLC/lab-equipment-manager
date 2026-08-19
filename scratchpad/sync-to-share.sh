#!/bin/zsh
# Mirror the local build back to the network share.
#
# Everything since the share was unmounted lives only on this laptop. This
# copies it back, and it is deliberately one-directional and dry-run-first:
# the share holds the copy the lab actually serves, and a careless rsync in the
# wrong direction would overwrite a night's work with a stale one.
#
#   ./sync-to-share.sh          show what WOULD change, touch nothing
#   ./sync-to-share.sh --go     actually copy
set -u
SRC="$HOME/LAB-lem"
DST="/Volumes/Labsharedrive/Ryan C/LAB-lem"

if [[ ! -d "$DST" ]]; then
  echo "share not mounted at: $DST"
  echo "mount it, then re-run."
  exit 1
fi

MODE=(--dry-run)
[[ "${1:-}" == "--go" ]] && MODE=()
[[ ${#MODE} -gt 0 ]] && echo "=== DRY RUN — nothing will be written. Pass --go to copy. ===" || echo "=== COPYING ==="

# The web server: source, templates, tests, and the vendored three.js.
# Excludes are the things that must NOT travel: virtualenvs are platform-built
# (there is a .venv-win here too), and __pycache__ from python 3.14 has bitten
# this project before on LabStation's install.
rsync -av $MODE \
  --exclude '.venv/' --exclude '.venv-win/' --exclude '__pycache__/' \
  --exclude '*.pyc' --exclude '.pytest_cache/' --exclude '.DS_Store' \
  "$SRC/LEM Web Server/" "$DST/LEM Web Server/"

# The harness and its notes. The shots directory is large and regenerable;
# REQUESTS.md and the NOTES-*.md files are the ones that carry reasoning
# forward and matter more than any binary here.
rsync -av $MODE \
  --exclude 'shots/' --exclude 'pairs/' --exclude '__pycache__/' \
  --exclude '.DS_Store' --exclude 'node_modules/' \
  "$SRC/scratchpad/" "$DST/scratchpad/"

echo
echo "reminder: the live LEM server must be restarted to pick up new static files,"
echo "and run_local_view-style tooling deliberately does NOT start the live channel,"
echo "so it cannot hijack the lab's push target."
