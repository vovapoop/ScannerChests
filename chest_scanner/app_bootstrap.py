"""Application bootstrap: VENV management and dependency imports."""
import os
import sys
import subprocess
import hashlib


# ============================================================
# DPI AWARENESS (Windows)
# ============================================================
def _enable_dpi_awareness():
    """Включает DPI-осведомлённость процесса ДО создания Tk-окна и mss-грабов.

    При масштабировании Windows 125/150% координаты mss (физические пиксели)
    и Tkinter (логические точки) расходятся. PER_MONITOR_AWARE решает проблему;
    на старых системах — откат на SYSTEM_AWARE.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_enable_dpi_awareness()


# ============================================================
# .venv BOOTSTRAP
# ============================================================
IS_FROZEN = getattr(sys, "frozen", False)
BASE_DIR_FOR_APP = os.path.dirname(sys.executable) if IS_FROZEN else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_VENV_DIR = os.path.join(BASE_DIR_FOR_APP, ".venv")
_REQ_FILE = os.path.join(BASE_DIR_FOR_APP, "requirements.txt")
_REQ_HASH = os.path.join(_VENV_DIR, ".requirements.sha256")


def _venv_py():
    if os.name == "nt":
        return (os.path.join(_VENV_DIR, "Scripts", "python.exe"),
                os.path.join(_VENV_DIR, "Scripts", "pythonw.exe"))
    p = os.path.join(_VENV_DIR, "bin", "python")
    return p, p


def _venv_active():
    return os.path.abspath(sys.prefix) != os.path.abspath(sys.base_prefix)


def _req_hash():
    if not os.path.isfile(_REQ_FILE):
        return None
    d = hashlib.sha256()
    with open(_REQ_FILE, "rb") as f:
        d.update(f.read())
    return d.hexdigest()


def _read_hash():
    try:
        with open(_REQ_HASH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, UnicodeError):
        return None


def _write_hash(v):
    os.makedirs(_VENV_DIR, exist_ok=True)
    with open(_REQ_HASH, "w", encoding="utf-8") as f:
        f.write(v)


def _run_cmd(cmd):
    kw = {"cwd": BASE_DIR_FOR_APP, "stdout": subprocess.PIPE,
          "stderr": subprocess.STDOUT, "text": True,
          "encoding": "utf-8", "errors": "replace"}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    r = subprocess.run(cmd, **kw)
    if r.returncode != 0:
        log = os.path.join(BASE_DIR_FOR_APP, "venv_setup.log")
        try:
            with open(log, "w", encoding="utf-8") as f:
                f.write(r.stdout or "")
        except OSError:
            pass
        raise RuntimeError(f"Ошибка .venv. Лог: {log}")


def _startup_error(err):
    log = os.path.join(BASE_DIR_FOR_APP, "startup_error.log")
    msg = f"{type(err).__name__}: {err}"
    try:
        with open(log, "w", encoding="utf-8") as f:
            f.write(msg)
    except OSError:
        pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg + f"\n\n{log}",
                                              "Chest Scanner — ошибка", 0x10)
        except Exception:
            pass


def _entry_script():
    """Запускаемый скрипт (main.py), а не сам модуль бутстрапа."""
    if sys.argv and os.path.isfile(sys.argv[0]):
        return os.path.abspath(sys.argv[0])
    return os.path.abspath(__file__)


def _ensure_venv():
    if IS_FROZEN or _venv_active():
        return
    vpy, vpyw = _venv_py()
    setup = sys.executable
    if os.name == "nt" and os.path.basename(setup).lower() == "pythonw.exe":
        c = os.path.join(os.path.dirname(setup), "python.exe")
        if os.path.isfile(c):
            setup = c
    if not os.path.isfile(vpy):
        _run_cmd([setup, "-m", "venv", _VENV_DIR])
    if not os.path.isfile(vpy):
        raise RuntimeError(".venv создана, но python не найден.")
    if os.name == "nt" and not os.path.isfile(vpyw):
        _run_cmd([vpy, "-m", "venv", "--clear", _VENV_DIR])
    rh = _req_hash()
    if rh and _read_hash() != rh:
        _run_cmd([vpy, "-m", "pip", "install", "--no-cache-dir",
                  "-r", _REQ_FILE, "--disable-pip-version-check"])
        _write_hash(rh)
    if os.name == "nt" and os.path.isfile(vpyw):
        p = subprocess.Popen(
            [vpyw, _entry_script(), *sys.argv[1:]],
            cwd=BASE_DIR_FOR_APP, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if p.pid:
            sys.exit(0)
    os.execv(vpy, [vpy, _entry_script(), *sys.argv[1:]])


# ============================================================
# ЗАВИСИМОСТИ
# ============================================================
try:
    import cv2
    import json
    import time
    import uuid
    import threading
    import requests
    import numpy as np
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog, simpledialog
    from datetime import datetime
    from queue import Queue, Empty
    from mss import mss
    from concurrent.futures import ThreadPoolExecutor, as_completed

    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        ZoneInfo = None

    try:
        import keyboard
        KEYBOARD_AVAILABLE = True
    except Exception:
        keyboard = None
        KEYBOARD_AVAILABLE = False

    try:
        import winsound
        WINSOUND_AVAILABLE = True
    except Exception:
        winsound = None
        WINSOUND_AVAILABLE = False

except Exception as e:
    _startup_error(e)
    raise
