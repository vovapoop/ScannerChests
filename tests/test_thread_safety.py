"""Тесты потокобезопасности: вызовы GUI из фоновых потоков и хоткеи."""
import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_safe_after_from_worker_runs_in_gui_thread(qapp):
    from PySide6.QtCore import QThread

    from chest_scanner.gui.main_window import ScannerGUI

    gui_thread = QThread.currentThread()
    seen = {}

    class _Window:
        # Достаточно метода _safe_after и шины сигналов из ScannerGUI.
        _safe_after = ScannerGUI._safe_after
        _run_in_gui = ScannerGUI._run_in_gui
        thread = lambda self: gui_thread  # noqa: E731

    from chest_scanner.gui.main_window import _GuiBus

    win = _Window()
    win._bus = _GuiBus()
    win._bus.call.connect(win._run_in_gui)

    def worker():
        def action():
            seen["thread"] = QThread.currentThread()
            seen["done"] = True

        win._safe_after(action)

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    deadline = time.time() + 3
    while not seen.get("done") and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    assert seen.get("done"), "функция из фонового потока не выполнилась"
    assert seen["thread"] is gui_thread


@pytest.mark.skipif(os.name != "nt", reason="RegisterHotKey доступен только на Windows")
def test_hotkey_manager_can_restart():
    from chest_scanner.hotkeys import WinHotkeyManager

    first = WinHotkeyManager()
    assert first.start()
    assert first.register("ctrl+alt+f13", lambda: None)
    first.stop()

    second = WinHotkeyManager()
    assert second.start(), "повторный запуск хоткеев после сохранения настроек"
    assert second.register("ctrl+alt+f13", lambda: None)
    assert second.available
    second.stop()


@pytest.mark.skipif(os.name != "nt", reason="WM_HOTKEY есть только на Windows")
def test_hotkey_message_runs_callback():
    import ctypes
    from ctypes import wintypes

    from chest_scanner.hotkeys import WM_HOTKEY, WinHotkeyManager

    hits = []
    mgr = WinHotkeyManager()
    assert mgr.start()
    assert mgr.register("ctrl+alt+f14", lambda: hits.append(1))
    hid = mgr._names["ctrl+alt+f14"]

    user32 = ctypes.windll.user32
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.PeekMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                    wintypes.UINT, wintypes.UINT, wintypes.UINT]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
    user32.DispatchMessageW.restype = ctypes.c_ssize_t
    user32.DispatchMessageW.argtypes = [ctypes.c_void_p]

    assert user32.PostMessageW(mgr._hwnd, WM_HOTKEY, hid, 0)
    msg = wintypes.MSG()
    while user32.PeekMessageW(ctypes.byref(msg), mgr._hwnd, 0, 0, 1):  # PM_REMOVE
        user32.DispatchMessageW(ctypes.byref(msg))
    mgr.stop()

    assert hits == [1], "WM_HOTKEY не дошёл до колбэка"
