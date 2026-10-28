@echo off
setlocal
cd /d "%~dp0"

set "VENV_DIR=.venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "REQUIREMENTS=requirements.txt"
set "DEPS_MARKER=%VENV_DIR%\.deps_installed_v3"

REM Create .venv if missing
if not exist "%PYTHON_EXE%" (
    echo [INFO] .venv not found. Creating virtual environment...

    py -3 -m venv "%VENV_DIR%"
    if errorlevel 1 (
        python -m venv "%VENV_DIR%"
        if errorlevel 1 (
            echo [ERROR] Failed to create .venv.
            echo Install Python 3 and add it to PATH.
            pause
            exit /b 1
        )
    )

    echo [OK] .venv created.
)

REM Install dependencies once (single source: requirements.txt)
if not exist "%DEPS_MARKER%" (
    echo [INFO] Installing dependencies...

    "%PYTHON_EXE%" -m pip install --upgrade pip --disable-pip-version-check

    if exist "%REQUIREMENTS%" (
        echo [INFO] Installing requirements.txt...
        "%PYTHON_EXE%" -m pip install --no-cache-dir -r "%REQUIREMENTS%" --disable-pip-version-check
        if errorlevel 1 (
            echo [ERROR] Failed to install requirements.txt.
            pause
            exit /b 1
        )
    )

    echo. > "%DEPS_MARKER%"
    echo [OK] Dependencies installed.
)

REM Launch hidden without console window (pythonw)
if exist "%VENV_DIR%\Scripts\pythonw.exe" (
    start "" "%VENV_DIR%\Scripts\pythonw.exe" "%~dp0main.py"
) else (
    start "" /min "%PYTHON_EXE%" "%~dp0main.py"
)

exit /b 0