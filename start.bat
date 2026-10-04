@echo off
setlocal
cd /d "%~dp0"

set "VENV_DIR=.venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "REQUIREMENTS=requirements.txt"
set "DEPS_MARKER=%VENV_DIR%\.deps_installed_v2"

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

REM Install dependencies once
if not exist "%DEPS_MARKER%" (
    echo [INFO] Installing base dependencies...

    "%PYTHON_EXE%" -m pip install --upgrade pip --disable-pip-version-check

    "%PYTHON_EXE%" -m pip install --no-cache-dir --disable-pip-version-check opencv-python numpy pyautogui keyboard requests Pillow tzdata
    if errorlevel 1 (
        echo [ERROR] Failed to install base dependencies.
        pause
        exit /b 1
    )

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

REM Launch hidden via VBS
if exist "%~dp0launcher.vbs" (
    wscript.exe //nologo "%~dp0launcher.vbs"
) else (
    start "" /min "%PYTHON_EXE%" "%~dp0main.py"
)

exit /b 0