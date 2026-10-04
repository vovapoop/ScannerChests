@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"

echo === Chest Scanner dependency fix ===
echo Folder: %CD%
echo.

if not exist "%VENV_PY%" (
    echo .venv not found. Creating...
    py -3 -m venv .venv
    if errorlevel 1 (
        python -m venv .venv
    )
)

if not exist "%VENV_PY%" (
    echo [ERROR] Could not create .venv\Scripts\python.exe
    echo Install Python 3.10/3.11/3.12 64-bit from python.org and add it to PATH.
    pause
    exit /b 1
)

echo.
echo === Python inside .venv ===
"%VENV_PY%" --version

echo.
echo === Ensure pip ===
"%VENV_PY%" -m ensurepip --upgrade
"%VENV_PY%" -m pip install --upgrade pip --disable-pip-version-check

echo.
echo === Installing packages without cache ===
"%VENV_PY%" -m pip install --no-cache-dir --disable-pip-version-check opencv-python numpy pyautogui keyboard requests Pillow tzdata

echo.
echo === Import check ===
"%VENV_PY%" -c "import cv2, numpy, pyautogui, requests, keyboard; from PIL import Image; print('OK: all modules imported')"

if errorlevel 1 (
    echo.
    echo [ERROR] Import check failed. Copy the error above.
    pause
    exit /b 1
)

echo.
echo SUCCESS: dependencies installed into .venv.
echo Now run start.bat, launcher.vbs, or run_debug.bat.
pause