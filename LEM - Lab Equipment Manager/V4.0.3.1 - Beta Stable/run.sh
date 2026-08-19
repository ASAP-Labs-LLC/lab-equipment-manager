#!/usr/bin/env bash
# Launch LEM V4 (CSV-based) on macOS/Linux, in CONSOLE mode.
#
# V4's normal entry runs a Windows system-tray loop (pystray) on the main
# thread; that doesn't serve on a headless/desktop Mac. This launcher starts the
# same Flask app + StatusEngine directly (no tray), reusing V4's own parse_args
# so --host/--port still work — without modifying the reference V4 source.
# On Windows use run.bat (native tray).
#
# Bootstraps an isolated .venv from requirements.txt if missing/incomplete.
# Examples: ./run.sh            ./run.sh --port 5557
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

echo "[run] Starting LEM V4 (console) on http://0.0.0.0:5557 ..."
exec "$PY" -c "
import runpy, sys
sys.argv = ['$ENTRY'] + sys.argv[1:]
ns = runpy.run_path('$ENTRY', run_name='lem_console')
args = ns['parse_args']()
ns['ENGINE'] = ns['StatusEngine']()
ns['app'].run(host=args.host, port=args.port, threaded=True, use_reloader=False)
" "$@"
