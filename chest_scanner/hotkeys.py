"""Глобальные горячие клавиши через WinAPI RegisterHotKey.

В отличие от библиотеки `keyboard`, системный хук RegisterHotKey не
требует прав администратора: регистрация выполняется через
message-only окно, сообщения обрабатываются отдельным потоком.

Колбэки вызываются в потоке сообщений менеджера, поэтому переключение
в GUI-поток (root.after) — ответственность подписчика.

Если регистрация недоступна (не Windows или ошибка), менеджер остаётся
неактивным и приложение откатывается на прежний polling (keyboard).
"""

import ctypes
import os
import threading
from ctypes import wintypes

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

HWND_MESSAGE = ctypes.c_void_p(-3)  # (HWND)-3

_KEY_ALIASES = {f"f{i}": 0x70 + i - 1 for i in range(1, 25)}

_MOD_FLAGS = (("ctrl", MOD_CONTROL), ("alt", MOD_ALT),
              ("shift", MOD_SHIFT), ("win", MOD_WIN), ("super", MOD_WIN))

_user32 = None
_kernel32 = None


def _vk_for_name(name):
    """Виртуальный код по имени клавиши (f1-f24, буква, цифра)."""
    n = (name or "").strip().lower()
    if not n:
        return None
    if n in _KEY_ALIASES:
        return _KEY_ALIASES[n]
    if len(n) == 1 and ("a" <= n <= "z" or "0" <= n <= "9"):
        return ord(n.upper())
    return None


class WinHotkeyManager:
    """Глобальные хоткеи Windows; потокобезопасный start/stop/register."""

    _WNDPROC = None

    def __init__(self):
        self._lock = threading.Lock()
        self._callbacks = {}       # hotkey_id -> callable
        self._names = {}           # нормализованное имя -> hotkey_id
        self._hwnd = None
        self._thread = None
        self._next_id = 1
        self._running = False

    @property
    def available(self):
        with self._lock:
            return os.name == "nt" and self._hwnd is not None

    @property
    def active(self):
        return self._running

    # ── служебное ──
    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_HOTKEY:
            hid = int(wparam)
            with self._lock:
                cb = self._callbacks.get(hid)
            if cb is not None:
                try:
                    cb()
                except Exception:
                    pass
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _create_window(self):
        try:
            if self._WNDPROC is None:
                WinHotkeyManager._WNDPROC = ctypes.WINFUNCTYPE(
                    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                    wintypes.WPARAM, wintypes.LPARAM)
            wc = wintypes.WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(wc)
            wc.lpfnWndProc = self._WNDPROC(self._proc)
            wc.hInstance = _kernel32.GetModuleHandleW(None)
            wc.lpszClassName = "ChestScannerHotkeyWindow"
            atom = _user32.RegisterClassExW(ctypes.byref(wc))
            if not atom:
                return None
            hwnd = _user32.CreateWindowExW(
                0, wc.lpszClassName, "ChestScannerHotkeys", 0,
                0, 0, 0, 0, HWND_MESSAGE, None, wc.hInstance, None)
            return hwnd if hwnd else None
        except Exception:
            return None

    # ── жизненный цикл ──
    def start(self):
        """Создаёт message-only окно и поток обработки сообщений."""
        global _user32, _kernel32
        if os.name != "nt":
            return False
        with self._lock:
            if self._running:
                return True
            try:
                _user32 = ctypes.windll.user32
                _kernel32 = ctypes.windll.kernel32
            except Exception:
                return False
            hwnd = self._create_window()
            if not hwnd:
                return False
            self._hwnd = hwnd
            self._running = True
            self._thread = threading.Thread(target=self._msg_loop, daemon=True)
            self._thread.start()
            return True

    def _msg_loop(self):
        msg = wintypes.MSG()
        while True:
            r = _user32.GetMessageW(ctypes.byref(msg), self._hwnd, 0, 0)
            if r in (0, -1):
                break
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
        with self._lock:
            self._running = False
            self._hwnd = None

    def stop(self):
        with self._lock:
            if not self._running:
                return
            hwnd = self._hwnd
            self._running = False
        if hwnd is not None:
            try:
                _user32.PostMessageW(hwnd, WM_QUIT, 0, 0)
            except Exception:
                pass
        t = self._thread
        if t is not None:
            t.join(timeout=2)
            self._thread = None
        with self._lock:
            self._hwnd = None
            self._callbacks.clear()
            self._names.clear()

    # ── регистрация ──
    def register(self, name, callback):
        """Регистрирует хоткей вида "f8", "ctrl+f8", "ctrl+shift+f9".

        Возвращает True при успехе, False при ошибке или занятой комбинации.
        """
        global _user32
        if _user32 is None:
            return False
        with self._lock:
            if not self._running or not self._hwnd:
                return False
            parts = [p.strip().lower() for p in (name or "").split("+") if p.strip()]
            if not parts:
                return False
            vk = _vk_for_name(parts[-1])
            if vk is None:
                return False
            mods = 0
            for m, flag in _MOD_FLAGS:
                if m in parts[:-1]:
                    mods |= flag
            hid = self._next_id
            self._next_id += 1
            ok = _user32.RegisterHotKey(self._hwnd, hid, mods | MOD_NOREPEAT, vk)
            if not ok:
                return False
            self._callbacks[hid] = callback
            self._names["+".join(parts)] = hid
            return True

    def unregister(self, name):
        with self._lock:
            if not self._hwnd:
                return
            parts = [p.strip().lower() for p in (name or "").split("+") if p.strip()]
            hid = self._names.pop("+".join(parts), None)
            if hid is None:
                return
            try:
                _user32.UnregisterHotKey(self._hwnd, hid)
            except Exception:
                pass
            self._callbacks.pop(hid, None)