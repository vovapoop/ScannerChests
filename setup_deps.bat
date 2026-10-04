@echo off
setlocal
cd /d "%~dp0"

set "LOG=%~dp0setup_deps.log"

echo [INFO] setup_deps started > "%LOG%"

set "SYS_PY="

where py >nul 2>nul
if not errorlevel 1 set "SYS_PY=py -3"

if not defined SYS_PY (
    where python >nul 2>nul
    if not errorlevel 1 set "SYS_PY=python"
)

if not defined SYS_PY (
    echo [ERROR] Python 3 is not found >> "%LOG%"
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Creating .venv >> "%LOG%"
    %SYS_PY% -m venv .venv >> "%LOG%" 2>&1

    if errorlevel 1 (
        echo [ERROR] Failed to create .venv >> "%LOG%"
        exit /b 1
    )
)

echo [INFO] Preparing pip >> "%LOG%"
".venv\Scripts\python.exe" -m ensurepip --upgrade >> "%LOG%" 2>&1
".venv\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check >> "%LOG%" 2>&1

echo [INFO] Installing dependencies >> "%LOG%"
".venv\Scripts\python.exe" -m pip install --no-cache-dir -r requirements.txt --disable-pip-version-check >> "%LOG%" 2>&1

if errorlevel 1 (
    echo [ERROR] Failed to install dependencies >> "%LOG%"
    exit /b 1
)

echo [OK] Dependencies installed >> "%LOG%"
exit /b 0