#!/usr/bin/env bash
# Launch LEM V5 (LabCore-backed) on macOS/Linux.
# Bootstraps an isolated .venv from requirements.txt if missing/incomplete,
# then starts the web server. Pass extra args through, e.g.
#   ./run.sh --port 5557                 (live LabCore at labvision.asaplabs.net)
#   ./run.sh --dev --seed                (offline demo, no LabCore needed)
#   LABCORE_URL=http://192.168.1.5:8089 ./run.sh   (point at a different LabCore)
set -e
cd "$(dirname "$0")"

PY=".venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "[run] Creating virtual environment (.venv)..."
  python3 -m venv .venv
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet -r requirements.txt
fi

# Self-heal if the venv exists but Flask is missing (e.g. after a Python upgrade).
if ! "$PY" -c "import flask" >/dev/null 2>&1; then
  echo "[run] Installing dependencies..."
  "$PY" -m pip install --quiet -r requirements.txt
fi

ENTRY="web_server.pyw"; [ -f "$ENTRY" ] || ENTRY="web_server.py"
echo "[run] Starting LEM V5 on http://0.0.0.0:5557 ..."
exec "$PY" "$ENTRY" "$@"
