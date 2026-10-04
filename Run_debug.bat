@echo off
setlocal
cd /d "%~dp0"

echo Running Chest Scanner with visible console...
echo.

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py
) else (
    python main.py
)

echo.
echo Exit code: %ERRORLEVEL%
echo.

if exist startup_error.log (
    echo === startup_error.log ===
    type startup_error.log
    echo.
)

pause