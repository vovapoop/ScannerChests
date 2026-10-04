@echo off
setlocal EnableExtensions
title Building Standalone Chest Scanner

echo ========================================
echo   Building standalone Chest Scanner
echo ========================================
echo.

REM Проверяем наличие Python
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    pause
    exit /b 1
)

REM Проверяем наличие PyInstaller
python -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo [INFO] Installing PyInstaller...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] Failed to install PyInstaller.
        pause
        exit /b 1
    )
)

REM Проверяем наличие зависимостей
echo [INFO] Checking dependencies...
python -m pip install opencv-python numpy pyautogui keyboard requests Pillow tzdata >nul 2>nul

echo.
echo [INFO] Building executable...
echo.

REM Удаляем старые сборки
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "ChestScanner.spec" del /q "ChestScanner.spec"

REM Собираем через PyInstaller
python -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onedir ^
    --name "ChestScanner" ^
    --windowed ^
    --icon=NONE ^
    --add-data "assets;assets" ^
    --add-data "config.json;." ^
    --hidden-import=cv2 ^
    --hidden-import=numpy ^
    --hidden-import=pyautogui ^
    --hidden-import=keyboard ^
    --hidden-import=requests ^
    --hidden-import=PIL ^
    --hidden-import=PIL._tkinter_finder ^
    --hidden-import=pkg_resources.py2_warn ^
    --hidden-import=tzdata ^
    --collect-all=cv2 ^
    --collect-all=pyautogui ^
    --collect-all=keyboard ^
    main.py

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. Check errors above.
    pause
    exit /b 1
)

echo.
echo [INFO] Copying additional files...

REM Копируем дополнительные файлы в папку сборки
if exist "items" xcopy /e /i /q "items" "dist\ChestScanner\items"
if exist "profiles" xcopy /e /i /q "profiles" "dist\ChestScanner\profiles"
if exist "unknown_items" xcopy /e /i /q "unknown_items" "dist\ChestScanner\unknown_items"
if exist "results" xcopy /e /i /q "results" "dist\ChestScanner\results"
if exist "discord_webhook.json" copy /q "discord_webhook.json" "dist\ChestScanner\" >nul
if exist "profiles.json" copy /q "profiles.json" "dist\ChestScanner\" >nul
if exist "items.json" copy /q "items.json" "dist\ChestScanner\" >nul

echo.
echo ========================================
echo   Build complete!
echo   Output: dist\ChestScanner\
echo ========================================
echo.
pause