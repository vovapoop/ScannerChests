@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo     Minecraft Chest Scanner
 echo ========================================
echo.

where py >nul 2>&1
if %errorlevel%==0 (
    py main.py
    goto :end
)

where python >nul 2>&1
if %errorlevel%==0 (
    python main.py
    goto :end
)

echo Python не найден.
echo Установите Python 3 и запустите start.bat снова.
pause

:end
if errorlevel 1 (
    echo.
    echo Программа завершилась с ошибкой.
    pause
)
