@echo off
REM Launch LEM V5 (LabCore-backed) on Windows.
REM Bootstraps an isolated .venv from requirements.txt if missing/incomplete,
REM then starts the web server. Pass extra args through, e.g.
REM   run.bat --port 5557           (live LabCore at labvision.asaplabs.net)
REM   run.bat --dev --seed          (offline demo, no LabCore needed)
REM   set LABCORE_URL=http://192.168.1.5:8089 & run.bat   (different LabCore)
setlocal
REM pushd (not cd) so this works when run from a UNC share -- cmd.exe cannot
REM use a \\server\share path as its current directory and would silently
REM fall back to C:\Windows.
pushd "%~dp0" || (
  echo [run] ERROR: could not switch to "%~dp0"
  pause
  exit /b 1
)

REM Windows gets its own venv: the shared .venv on this drive is a macOS venv
REM (bin/, Homebrew paths) created by run.sh and is not usable here.
set "VENV=.venv-win"
set "PY=%VENV%\Scripts\python.exe"

if not exist "%PY%" (
  echo [run] Creating virtual environment ^(%VENV%^)...
  py -3 -m venv "%VENV%" 2>NUL || python -m venv "%VENV%"
  if not exist "%PY%" (
    echo [run] ERROR: failed to create .venv -- is Python installed and on PATH?
    popd
    pause
    exit /b 1
  )
  "%PY%" -m pip install --upgrade pip
  "%PY%" -m pip install -r requirements.txt
)

REM Self-heal if the venv exists but Flask is missing (e.g. after a Python upgrade).
"%PY%" -c "import flask" >NUL 2>&1 || "%PY%" -m pip install -r requirements.txt

set "ENTRY=web_server.pyw"
if not exist "%ENTRY%" set "ENTRY=web_server.py"
echo [run] Starting LEM V5 on http://0.0.0.0:5557 ...
"%PY%" "%ENTRY%" %*
set "RC=%ERRORLEVEL%"

popd
REM Keep the window open if something went wrong, so the error is readable.
if not "%RC%"=="0" (
  echo.
  echo [run] Exited with code %RC%.
  pause
)

endlocal
exit /b %RC%
