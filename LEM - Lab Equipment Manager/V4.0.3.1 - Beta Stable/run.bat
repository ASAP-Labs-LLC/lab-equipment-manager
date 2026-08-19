@echo off
REM Launch LEM V4 (CSV-based) on Windows.
REM Bootstraps an isolated .venv from requirements.txt if missing/incomplete,
REM then starts the web server. Pass extra args through, e.g. run.bat --port 5557
setlocal
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo [run] Creating virtual environment ^(.venv^)...
  py -3 -m venv .venv 2>NUL || python -m venv .venv
  "%PY%" -m pip install --upgrade pip
  "%PY%" -m pip install -r requirements.txt
)

REM Self-heal if the venv exists but Flask is missing (e.g. after a Python upgrade).
"%PY%" -c "import flask" >NUL 2>&1 || "%PY%" -m pip install -r requirements.txt

set "ENTRY=web_server.pyw"
if not exist "%ENTRY%" set "ENTRY=web_server.py"
echo [run] Starting LEM V4 on http://0.0.0.0:5557 ...
"%PY%" "%ENTRY%" %*

endlocal
