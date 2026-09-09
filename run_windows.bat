@echo off
setlocal
cd /d "%~dp0"
echo.
echo ================================================
echo Senior Python Backend Assessment Platform
echo ================================================
echo.
where py >nul 2>nul
if %errorlevel%==0 (
  set "PY=py"
) else (
  set "PY=python"
)
echo Checking Python...
%PY% --version
if errorlevel 1 (
  echo.
  echo Python is not installed or not available in PATH.
  echo Install Python 3.10+ and run this file again.
  pause
  exit /b 1
)
echo.
echo Installing/updating required packages...
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Could not install required packages.
  pause
  exit /b 1
)
echo.
echo Starting assessment server...
echo Keep this window open while using the assessment.
echo Open: http://127.0.0.1:8000/
echo.
start "" "http://127.0.0.1:8000/"
%PY% -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
