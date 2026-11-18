"""Application bootstrap: VENV management and dependency imports."""
import hashlib
import os
import subprocess
import sys
import traceback


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
    temp_path = _REQ_HASH + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8", newline="") as f:
            f.write(v)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, _REQ_HASH)
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


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
    """?????????? ???????????? ???? ? ????????????? ?????? ???????."""
    log = os.path.join(BASE_DIR_FOR_APP, "startup_error.log")
    msg = f"{type(err).__name__}: {err}"
    detail = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    try:
        with open(log, "w", encoding="utf-8") as f:
            f.write(detail if detail.strip() else msg)
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


def _is_windows():
    return os.name == "nt"


_BOOTSTRAP_INSTANCE_LOCK = None
_HANDSHAKE_ENV = "CHESTSCANNER_BOOTSTRAP_HANDSHAKE"
_HANDSHAKE_TIMEOUT = 20.0


def take_bootstrap_instance_lock():
    """Transfer a lock acquired by the child handshake to the application."""
    global _BOOTSTRAP_INSTANCE_LOCK
    lock = _BOOTSTRAP_INSTANCE_LOCK
    _BOOTSTRAP_INSTANCE_LOCK = None
    return lock


def _launch_child_with_handshake(vpy):
    """Wait for child lock ownership before releasing the setup lock."""
    import threading
    env = os.environ.copy()
    env[_HANDSHAKE_ENV] = "1"
    runner = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "bootstrap_runner.py")
    child = subprocess.Popen(
        [vpy, runner, _entry_script(), *sys.argv[1:]], cwd=BASE_DIR_FOR_APP,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if _is_windows() else 0)
    result = {}
    ready = threading.Event()

    def read_status():
        try:
            result["status"] = child.stdout.readline().strip()
            ready.set()
            # Continue draining output so the child cannot block on a full pipe.
            while child.stdout.readline():
                pass
        except (OSError, ValueError) as exc:
            result["error"] = exc
        finally:
            ready.set()

    threading.Thread(target=read_status, name="ChestScannerBootstrapHandshake", daemon=True).start()
    if not ready.wait(_HANDSHAKE_TIMEOUT):
        child.kill()
        child.wait(timeout=5)
        raise RuntimeError("Child bootstrap did not confirm instance-lock ownership.")
    status = result.get("status")
    if status == "BUSY":
        child.wait(timeout=5)
        return False
    if status != "CLAIMED":
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        raise RuntimeError("Child bootstrap failed before claiming the instance lock: "
                           + str(result.get("error") or status or "no response"))
    return child


def _ensure_venv():
    if IS_FROZEN:
        return
    if _BOOTSTRAP_INSTANCE_LOCK is not None:
        return
    if _venv_active():
        return
    from chest_scanner.instance_lock import acquire_instance_lock
    setup_lock = acquire_instance_lock(
        BASE_DIR_FOR_APP, lock_name=".chestscanner-venv-setup.lock", blocking=True)
    app_lock = None
    child = None
    committed = False
    try:
        vpy, vpyw = _venv_py()
        app_lock = acquire_instance_lock(BASE_DIR_FOR_APP, blocking=False)
        if app_lock is not None:
            setup = sys.executable
            if _is_windows() and os.path.basename(setup).lower() == "pythonw.exe":
                candidate = os.path.join(os.path.dirname(setup), "python.exe")
                if os.path.isfile(candidate):
                    setup = candidate
            if not os.path.isfile(vpy):
                _run_cmd([setup, "-m", "venv", _VENV_DIR])
            if not os.path.isfile(vpy):
                raise RuntimeError(".venv is not initialized: its Python interpreter is missing.")
            if _is_windows() and not os.path.isfile(vpyw):
                _run_cmd([setup, "-m", "venv", "--clear", _VENV_DIR])
            if not os.path.isfile(vpy):
                raise RuntimeError(".venv repair failed: python.exe is missing.")
            if _is_windows() and not os.path.isfile(vpyw):
                raise RuntimeError(".venv repair failed: pythonw.exe is missing.")
            requirements_hash = _req_hash()
            if requirements_hash and _read_hash() != requirements_hash:
                _run_cmd([vpy, "-m", "pip", "install", "--no-cache-dir",
                          "-r", _REQ_FILE, "--disable-pip-version-check"])
                _write_hash(requirements_hash)
            app_lock.close()
            app_lock = None
        child = _launch_child_with_handshake(vpy)
        if child is False:
            return
        setup_lock.close()
        child.stdin.write("COMMIT\n")
        child.stdin.flush()
        child.stdin.close()
        committed = True
    finally:
        if app_lock is not None:
            app_lock.close()
        setup_lock.close()
        if child not in (None, False) and not committed and child.poll() is None:
            child.kill()
            child.wait(timeout=5)
    if child is not False:
        raise SystemExit(0)

# ЗАВИСИМОСТИ
# ============================================================
try:
    import json
    import threading
    import time
    import uuid
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import datetime
    from queue import Empty, Queue

    import cv2
    import numpy as np
    import requests
    from mss import mss

    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        ZoneInfo = None

    # PySide6 (Qt) — графический интерфейс. Загрузка ленивая: сканер
    # и тесты работают без Qt, GUI-модули импортируют PySide6 сами.
    try:
        from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
        from PySide6.QtGui import (
            QAction,
            QCloseEvent,
            QColor,
            QIcon,
            QKeySequence,
            QPixmap,
        )
        from PySide6.QtWidgets import (
            QApplication,
            QCheckBox,
            QComboBox,
            QDialog,
            QFileDialog,
            QFrame,
            QHBoxLayout,
            QInputDialog,
            QLabel,
            QLineEdit,
            QListWidget,
            QMainWindow,
            QMenu,
            QMessageBox,
            QPlainTextEdit,
            QPushButton,
            QScrollArea,
            QSlider,
            QStackedWidget,
            QSystemTrayIcon,
            QTableWidget,
            QTableWidgetItem,
            QVBoxLayout,
            QWidget,
        )
        QT_AVAILABLE = True
    except Exception:
        QT_AVAILABLE = False
        # Атрибуты на случай, если код проверяет их наличие без импорта
        QObject = Qt = QTimer = Signal = Slot = None
        QApplication = QMainWindow = QDialog = QWidget = None

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
