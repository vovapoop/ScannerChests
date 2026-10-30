@echo off
setlocal
cd /d "%~dp0"

echo ================================================
echo   Chest Scanner - сборка автономного exe
echo ================================================

REM --- 1. Поиск Python -----------------------------------------------
where py >nul 2>&1
if %errorlevel%==0 (
    set "PY_LAUNCHER=py -3"
) else (
    where python >nul 2>&1
    if %errorlevel%==0 (
        set "PY_LAUNCHER=python"
    ) else (
        echo [ERROR] Python не найден. Для сборки нужен Python 3.10+.
        pause
        exit /b 1
    )
)

set "BUILD_VENV=.venv-build"
set "BUILD_PY=%BUILD_VENV%\Scripts\python.exe"
set "BUILD_PIP=%BUILD_VENV%\Scripts\pip.exe"

REM --- 2. Окружение сборки -------------------------------------------
if not exist "%BUILD_PY%" (
    echo [INFO] Создание окружения сборки: %BUILD_VENV% ...
    %PY_LAUNCHER% -m venv "%BUILD_VENV%"
    if errorlevel 1 (
        echo [ERROR] Не удалось создать %BUILD_VENV%.
        pause
        exit /b 1
    )
)

echo [INFO] Установка зависимостей для сборки...
"%BUILD_PY%" -m pip install --upgrade pip --disable-pip-version-check
if errorlevel 1 ( echo [ERROR] Обновление pip не удалось. & pause & exit /b 1 )
"%BUILD_PIP%" install -r requirements.txt pyinstaller --no-cache-dir --disable-pip-version-check
if errorlevel 1 ( echo [ERROR] Установка зависимостей не удалась. & pause & exit /b 1 )

REM --- 3. Иконка ------------------------------------------------------
if not exist "build\icon.ico" (
    if exist "Запуск.ico" (
        echo [INFO] Используем иконку Запуск.ico
        if not exist "build" mkdir "build"
        copy /Y "Запуск.ico" "build\icon.ico" >nul
        if errorlevel 1 ( echo [WARN] Копирование иконки не удалось. )
    ) else (
        echo [INFO] Генерация иконки из assets\chest_open.png ...
        "%BUILD_PY%" build\make_icon.py
        if errorlevel 1 ( echo [WARN] Не удалось сгенерировать иконку. )
    )
)

REM --- 4. PyInstaller -------------------------------------------------
echo [INFO] Сборка PyInstaller...
"%BUILD_PY%" -m PyInstaller ChestScanner.spec --noconfirm --clean
if errorlevel 1 ( echo [ERROR] Сборка PyInstaller не удалась. & pause & exit /b 1 )

REM --- 5. Ассеты и база предметов рядом с exe -------------------------
set "DIST=dist\ChestScanner"
echo [INFO] Копирование assets\ и items.json в %DIST% ...
if not exist "%DIST%\assets" mkdir "%DIST%\assets"
xcopy "assets" "%DIST%\assets\" /E /I /Y >nul
if errorlevel 1 ( echo [ERROR] Копирование assets не удалось. & pause & exit /b 1 )
copy /Y "items.json" "%DIST%\items.json" >nul
if errorlevel 1 ( echo [ERROR] Копирование items.json не удалось. & pause & exit /b 1 )
copy /Y "LICENSE" "%DIST%\LICENSE" >nul
if errorlevel 1 ( echo [ERROR] Копирование LICENSE не удалось. & pause & exit /b 1 )

REM --- 6. Портативный архив -------------------------------------------
echo [INFO] Создание ChestScanner-portable.zip ...
powershell -NoProfile -Command "Compress-Archive -Path '%DIST%' -DestinationPath 'dist\ChestScanner-portable.zip' -Force"
if errorlevel 1 ( echo [WARN] Создание zip не удалось. ) else ( echo [OK] dist\ChestScanner-portable.zip )

REM --- 7. Установщик Inno Setup ---------------------------------------
set "ISCC="
for %%i in (ISCC.exe) do set "ISCC=%%~$PATH:i"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if defined ISCC (
    echo [INFO] Сборка установщика Inno Setup...
    "%ISCC%" installer.iss
    if errorlevel 1 ( echo [WARN] Inno Setup завершился с ошибкой. ) else ( echo [OK] dist\ChestScannerSetup.exe )
) else (
    echo [INFO] Inno Setup не найден - установщик пропущен.
    echo         Для ChestScannerSetup.exe установите Inno Setup 6.
)

echo.
echo ================================================
echo   Готово!
echo     Портатив:   %DIST%
echo     Архив:      dist\ChestScanner-portable.zip
if exist "dist\ChestScannerSetup.exe" echo     Установщик: dist\ChestScannerSetup.exe
echo ================================================
pause
exit /b 0