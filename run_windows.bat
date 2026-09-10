@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

title Senior Python Backend Assessment Platform - Python 3.12

echo.
echo ============================================================
echo   SENIOR PYTHON BACKEND ASSESSMENT PLATFORM
echo   Python 3.12.x
echo ============================================================
echo.

set "PYEXE="

REM 1) Prefer Python Launcher with Python 3.12.
where py >nul 2>nul
if %errorlevel%==0 (
    py -3.12 --version >nul 2>nul
    if !errorlevel! == 0 set "PYEXE=py -3.12"
)

REM 2) Fallback to python.exe only if it is Python 3.12.
if not defined PYEXE (
    where python >nul 2>nul
    if !errorlevel! == 0 (
        for /f "tokens=2" %%V in ('python --version 2^>^&1') do set "PYVER=%%V"
        echo Detected: Python !PYVER!
        echo !PYVER! | findstr /b "3.12." >nul
        if !errorlevel! == 0 set "PYEXE=python"
    )
)

if not defined PYEXE (
    echo.
    echo ERROR: Python 3.12.x is not installed.
    echo.
    echo Install Python 3.12 from:
    echo https://www.python.org/downloads/release/python-31210/
    echo.
    echo During installation select:
    echo   [X] Add python.exe to PATH
    echo   [X] Install py launcher
    echo.
    echo Then restart this BAT file.
    echo.
    pause
    exit /b 1
)

echo Python:
%PYEXE% --version

echo.
echo [1/3] Installing/checking required packages...
%PYEXE% -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: Package installation failed.
    echo Check your internet connection, then run this file again.
    echo.
    pause
    exit /b 1
)

echo.
echo [2/3] Starting FastAPI server...
echo Server log: server.log
echo Server error log: server_error.log
echo.

REM Kill only a process listening on our selected port if possible.
set "PORT=8000"

REM Start server in a separate window and redirect logs.
start "Senior Assessment Server" /min cmd /c "%PYEXE% -m uvicorn app.main:app --host 127.0.0.1 --port %PORT% 1>server.log 2>server_error.log"

echo Waiting for the server to become ready...
set "READY="

for /l %%N in (1,1,30) do (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:%PORT%/health' -TimeoutSec 1; if($r.StatusCode -eq 200){exit 0}else{exit 1} } catch { exit 1 }" >nul 2>nul
    if !errorlevel! == 0 (
        set "READY=1"
        goto :server_ready
    )
    timeout /t 1 /nobreak >nul
)

:server_ready
if defined READY (
    echo.
    echo ============================================================
    echo   SERVER IS RUNNING
    echo ============================================================
    echo.
    echo Admin URL:
    echo http://127.0.0.1:%PORT%/
    echo.
    echo [3/3] Opening Admin page...
    start "" "http://127.0.0.1:%PORT%/"
    echo.
    echo Keep the server window open while using the platform.
    echo.
    echo You can close this launcher window.
    echo.
    pause
    exit /b 0
)

echo.
echo ============================================================
echo   SERVER DID NOT START
echo ============================================================
echo.
echo The browser was NOT opened because the backend is not ready.
echo.
echo Check these files in this folder:
echo   server.log
echo   server_error.log
echo.
echo ----- server_error.log -----
if exist server_error.log type server_error.log
echo.
echo ----------------------------
echo.
echo Press any key to close.
pause
exit /b 1
