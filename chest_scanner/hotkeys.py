"""Глобальные горячие клавиши через WinAPI RegisterHotKey.

В отличие от библиотеки `keyboard`, системный хук RegisterHotKey не
требует прав администратора: регистрация выполняется через message-only
окно, а WM_HOTKEY разбирает штатный цикл сообщений потока, создавшего
менеджер (в приложении это цикл событий Qt). Отдельный поток не нужен и
вреден: сообщения окна доставляются только в очередь создавшего потока.

Колбэки вызываются в том же потоке, что и `start()`.

Если регистрация недоступна (не Windows или ошибка), менеджер остаётся
неактивным и приложение откатывается на прежний polling (keyboard).
"""

import ctypes
import os
import threading
from ctypes import wintypes


class _WNDCLASSEXW(ctypes.Structure):
    """wintypes.WNDCLASSEXW в стандартной библиотеке нет."""

    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


CLASS_NAME = "ChestScannerHotkeyWindow"
ERROR_CLASS_ALREADY_EXISTS = 1410

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
_api_ready = False


def _init_api():
    """Явные прототипы WinAPI: без них ctypes обрезает 64-битные handle.

    GetModuleHandleW с restype по умолчанию (c_int) возвращает усечённый
    HMODULE, а CreateWindowExW с усечённым HWND даёт access violation.
    """
    global _api_ready
    if _api_ready:
        return
    _user32.RegisterClassExW.restype = wintypes.WORD
    _user32.RegisterClassExW.argtypes = [ctypes.c_void_p]
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    _user32.DestroyWindow.restype = wintypes.BOOL
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.DefWindowProcW.restype = ctypes.c_ssize_t
    _user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                       ctypes.c_size_t, ctypes.c_size_t]
    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int,
                                       wintypes.UINT, wintypes.UINT]
    _user32.UnregisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetLastError.restype = wintypes.DWORD
    _kernel32.GetLastError.argtypes = []
    _api_ready = True


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


_managers = {}
_managers_lock = threading.Lock()


def _class_proc(hwnd, msg, wparam, lparam):
    """Оконная процедура класса: находит менеджер по его окну.

    Класс регистрируется один раз на процесс, поэтому привязывать его к
    конкретному экземпляру нельзя: после пересоздания хоткеев сообщения
    ушли бы в уже остановленный менеджер.
    """
    with _managers_lock:
        mgr = _managers.get(int(hwnd or 0))
    if mgr is not None:
        return mgr._proc(hwnd, msg, wparam, lparam)
    return _user32.DefWindowProcW(hwnd, msg, ctypes.c_size_t(wparam),
                                  ctypes.c_size_t(lparam))


def _ensure_class():
    """Регистрирует класс окна один раз на процесс."""
    if WinHotkeyManager._class_ready:
        return True
    wndproc = WinHotkeyManager._WNDPROC(_class_proc)
    WinHotkeyManager._WNDPROC_THUNKS.append(wndproc)
    wc = _WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(wc)
    wc.lpfnWndProc = ctypes.cast(wndproc, ctypes.c_void_p)
    wc.hInstance = _kernel32.GetModuleHandleW(None)
    wc.lpszClassName = CLASS_NAME
    if not _user32.RegisterClassExW(ctypes.byref(wc)) \
            and _kernel32.GetLastError() != ERROR_CLASS_ALREADY_EXISTS:
        return False
    WinHotkeyManager._class_ready = True
    return True


class WinHotkeyManager:
    """Глобальные хоткеи Windows; потокобезопасный start/stop/register."""

    _WNDPROC = None
    # Windows хранит указатель на оконную процедуру до конца процесса,
    # поэтому thunk нельзя отдавать сборщику мусора.
    _WNDPROC_THUNKS = []
    _class_ready = False

    def __init__(self):
        self._lock = threading.Lock()
        self._callbacks = {}       # hotkey_id -> callable
        self._names = {}           # нормализованное имя -> hotkey_id
        self._hwnd = None
        self._thread = None
        self._next_id = 1
        self._running = False
        self._wndproc = None

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
        # wparam/lparam — беззнаковые: без приведения отрицательные
        # (старший бит) значения не пролезают в аргумент по умолчанию.
        return _user32.DefWindowProcW(hwnd, msg,
                                      ctypes.c_size_t(wparam),
                                      ctypes.c_size_t(lparam))

    def _create_window(self):
        try:
            if self._WNDPROC is None:
                WinHotkeyManager._WNDPROC = ctypes.WINFUNCTYPE(
                    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                    wintypes.WPARAM, wintypes.LPARAM)
            if not _ensure_class():
                return None
            hwnd = _user32.CreateWindowExW(
                0, CLASS_NAME, "ChestScannerHotkeys", 0,
                0, 0, 0, 0, HWND_MESSAGE, None,
                _kernel32.GetModuleHandleW(None), None)
            if not hwnd:
                return None
            with _managers_lock:
                _managers[int(hwnd)] = self
            return hwnd
        except Exception:
            return None

    # ── жизненный цикл ──
    def start(self):
        """Создаёт message-only окно; сообщения разбирает цикл вызвавшего потока."""
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
            _init_api()
            hwnd = self._create_window()
            if not hwnd:
                return False
            self._hwnd = hwnd
            self._running = True
            return True

    def stop(self):
        with self._lock:
            if not self._running:
                return
            hwnd = self._hwnd
            self._running = False
            ids = list(self._callbacks)
            self._callbacks.clear()
            self._names.clear()
            self._hwnd = None
        for hid in ids:
            try:
                _user32.UnregisterHotKey(hwnd, hid)
            except Exception:
                pass
        # Окно создано в потоке вызывающего: уничтожаем его здесь же,
        # иначе следующая регистрация класса и создание окна дадут мусор.
        if hwnd is not None:
            with _managers_lock:
                _managers.pop(int(hwnd), None)
            try:
                _user32.DestroyWindow(hwnd)
            except Exception:
                pass

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