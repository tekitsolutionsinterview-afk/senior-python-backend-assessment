@echo off
setlocal
cd /d "%~dp0"
echo ==========================================
echo Assessment Platform Diagnostics
echo ==========================================
echo.
where py
if %errorlevel%==0 (
  echo.
  echo Python 3.12 check:
  py -3.12 --version
) else (
  echo Python launcher (py) not found.
)
echo.
echo Python executable:
where python
echo.
echo Installed FastAPI packages:
py -3.12 -m pip show fastapi uvicorn pydantic 2>nul
echo.
echo Compiling application:
py -3.12 -m py_compile app\main.py
if errorlevel 1 (
  echo Backend compile FAILED.
) else (
  echo Backend compile PASSED.
)
echo.
pause
