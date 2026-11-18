@echo off
chcp 65001 >nul
setlocal
echo ================================================
echo   Chest Scanner - portable version updater
echo ================================================
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0update_portable.ps1" -Source "%~dp0ChestScanner"
set "RESULT=%ERRORLEVEL%"
echo.
if "%RESULT%"=="0" (
    echo [OK] Update completed.
) else (
    echo [ERROR] Update failed. See the message above.
)
pause
exit /b %RESULT%
