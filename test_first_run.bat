@echo off
setlocal
cd /d "%~dp0"

if exist profiles.json (
    copy /y profiles.json profiles.backup.json >nul
    echo [INFO] Backup created: profiles.backup.json
)

(
echo {
echo   "version": 1,
echo   "active": "Default",
echo   "profiles": [
echo     {
echo       "name": "Default",
echo       "directory": "."
echo     }
echo   ],
echo   "setup_complete": false
echo }
) > profiles.json

echo [INFO] profiles.json now has setup_complete=false
echo [INFO] Starting program in debug mode...

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py
) else (
    python main.py
)

pause