@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "VENV=%~dp0.venv"
set "PYTHON_EXE=%VENV%\Scripts\python.exe"
set "PYTHONW_EXE=%VENV%\Scripts\pythonw.exe"

if not exist "%~dp0main.py" (
    msg * "main.py не найден рядом с start.bat."
    exit /b 1
)

REM Find system Python only when the virtual environment does not exist.
if not exist "%PYTHON_EXE%" (
    set "SYSTEM_PYTHON="
    where py >nul 2>&1
    if not errorlevel 1 set "SYSTEM_PYTHON=py -3"

    if not defined SYSTEM_PYTHON (
        where python >nul 2>&1
        if not errorlevel 1 set "SYSTEM_PYTHON=python"
    )

    if not defined SYSTEM_PYTHON (
        msg * "Python 3 не найден. Установите Python и запустите Запуск.vbs снова."
        exit /b 1
    )

    "%SYSTEM_PYTHON%" -m venv "%VENV%" >nul 2>&1
    if errorlevel 1 (
        msg * "Не удалось создать виртуальное окружение .venv."
        exit /b 1
    )
)

if not exist "%PYTHON_EXE%" (
    msg * "Python в .venv не найден."
    exit /b 1
)

REM Install/update required packages inside the virtual environment.
"%PYTHON_EXE%" -m pip install --disable-pip-version-check --no-cache-dir -q opencv-python keyboard numpy pyautogui requests pillow
if errorlevel 1 (
    msg * "Не удалось установить необходимые библиотеки."
    exit /b 1
)

REM Run without a console window.
if exist "%PYTHONW_EXE%" (
    start "" /b "%PYTHONW_EXE%" "%~dp0main.py"
) else (
    start "" /b "%PYTHON_EXE%" "%~dp0main.py"
)

exit /b 0
