@echo off
setlocal
cd /d "%~dp0"

REM Основной запуск — через Запуск.vbs, без консольного окна.
if not exist "%~dp0Запуск.vbs" (
    echo [ERROR] Запуск.vbs не найден.
    pause
    exit /b 1
)

wscript.exe //nologo "%~dp0Запуск.vbs"
exit /b 0
