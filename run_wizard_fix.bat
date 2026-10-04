@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" wizard_fix.py
) else (
    python wizard_fix.py
)

pause