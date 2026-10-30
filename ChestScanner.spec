# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: Chest Scanner (onedir, без консоли).

Особенности:
- console=False — GUI-режим (аналог pythonw).
- uac_admin=False — exe НЕ требует прав администратора: глобальные
  горячие клавиши работают через WinAPI RegisterHotKey (hotkeys.py);
  библиотека keyboard используется только как фоллбэк.
- tzdata подключается явно: на Windows нет базы часовых поясов IANA,
  без неё сломается ZoneInfo('Europe/Moscow') в logging_setup.
- assets/ и items.json НЕ бандлятся внутрь сборки — их копирует build.bat
  рядом с exe (код приложения ищет их в каталоге exe, а не в _MEIPASS).
"""
import os
import shutil
import subprocess
import sys

from PyInstaller.utils.hooks import collect_all

# SPEC — абсолютный путь к текущему spec-файлу (предоставляет PyInstaller)
PROJECT_ROOT = os.path.dirname(os.path.abspath(SPEC))


def _ensure_icon():
    """Возвращает путь к build/icon.ico, создавая его при необходимости.

    Приоритет: готовый build/icon.ico → Запуск.ico из корня → генерация
    из assets/chest_open.png через build/make_icon.py.
    """
    ico = os.path.join(PROJECT_ROOT, "build", "icon.ico")
    if os.path.isfile(ico):
        return ico

    src = os.path.join(PROJECT_ROOT, "Запуск.ico")
    os.makedirs(os.path.dirname(ico), exist_ok=True)
    if os.path.isfile(src):
        shutil.copyfile(src, ico)
    else:
        subprocess.run(
            [sys.executable, os.path.join(PROJECT_ROOT, "build", "make_icon.py")],
            cwd=PROJECT_ROOT, check=True)
    return ico


# --- tzdata (data-пакет для zoneinfo) --------------------------------
tz_datas, tz_binaries, tz_hidden = collect_all("tzdata")

a = Analysis(
    ["main.py"],
    pathex=[PROJECT_ROOT],
    binaries=tz_binaries,
    datas=tz_datas,
    # PySide6 собирают встроенные хуки PyInstaller (только нужные модули:
    # QtCore/QtGui/QtWidgets + QPA-плагин windows). collect_all("PySide6")
    # не используется: он тащит QtWebEngine и QtPdf (+сотни МБ).
    # item_pack импортируется внутри функций GUI (_export_item_db/_import_item_db),
    # поэтому его нужно указать явно, иначе PyInstaller не включит модуль в сборку.
    hiddenimports=[*tz_hidden, "chest_scanner.item_pack"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtQuick",
              "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.QtCharts",
              "PySide6.QtDataVisualization", "PySide6.QtGraphs",
              "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
              "PySide6.QtNetwork", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
              "PySide6.QtSvg", "PySide6.QtTest", "PySide6.QtUiTools",
              "PySide6.QtWebChannel", "PySide6.QtWebSockets"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ChestScanner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ensure_icon(),
    version=os.path.join(PROJECT_ROOT, "version_info.txt"),
    uac_admin=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ChestScanner",
)