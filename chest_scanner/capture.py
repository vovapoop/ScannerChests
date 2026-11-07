"""Захват изображения окна игры через Windows Graphics Capture (WGC).

Зачем: обычный снимок экрана (`mss`) видит только то, что нарисовано на
мониторе, поэтому окно программы, тултипы и любые окна поверх игры попадают
в кадр, а игра на втором мониторе не попадает вовсе. WGC снимает
поверхность самого окна, отдаёт клиентскую область независимо от
перекрытий и от того, на каком мониторе окно находится.

Ограничения, из-за которых захват окна может не сработать:
- окно свёрнуто (нет кадров);
- эксклюзивный полный экран (WGC его не видит);
- нет пакета windows-capture или Windows старше 10 1803.

Во всех этих случаях функция возвращает None, и вызывающий код должен
откатиться на обычный снимок экрана.
"""
import ctypes
import os
import threading
from ctypes import wintypes

import numpy as np

from chest_scanner.logging_setup import logger

# Сколько ждать первый кадр. WGC отдаёт кадр практически сразу, но при
# запуске сессии возможна задержка в сотни миллисекунд.
GRAB_TIMEOUT = 2.0

# Средняя яркость кадра ниже этого значения считается «пустой» (чёрный
# кадр бывает при защищённом контенте и при некоторых режимах игры).
BLACK_FRAME_MEAN = 3.0

_DWMWA_EXTENDED_FRAME_BOUNDS = 9

_user32 = ctypes.windll.user32 if os.name == "nt" else None


def _dwm_frame_bounds(hwnd):
    """Видимая рамка окна (без невидимых границ изменения размера)."""
    rect = wintypes.RECT()
    try:
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), _DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect), ctypes.sizeof(rect))
    except Exception:
        hr = -1
    if hr != 0 or rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def _window_rect(hwnd):
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def _client_bounds(hwnd):
    """Клиентская область окна в экранных координатах."""
    rect = wintypes.RECT()
    if not _user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    point = wintypes.POINT(0, 0)
    if not _user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(point)):
        return None
    return (point.x, point.y, rect.right, rect.bottom)


def _window_title(hwnd):
    n = _user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    _user32.GetWindowTextW(wintypes.HWND(hwnd), buf, n + 1)
    return buf.value


def list_windows():
    """Видимые окна верхнего уровня: [(hwnd, заголовок), ...]."""
    if os.name != "nt":
        return []
    out = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        try:
            if _user32.IsWindowVisible(hwnd):
                title = _window_title(hwnd)
                if title:
                    out.append((hwnd, title))
        except Exception:
            pass
        return True

    try:
        _user32.EnumWindows(callback_type(_cb), 0)
    except Exception as e:
        logger.debug(f"Захват окна: перечисление окон: {e}")
    return out


def find_game_window(title_substring):
    """Ищет окно игры по подстроке заголовка (без учёта регистра).

    Свёрнутые окна пропускаются: WGC не отдаёт для них кадры.
    """
    needle = (title_substring or "").strip().lower()
    if not needle or os.name != "nt":
        return None
    try:
        for hwnd, title in list_windows():
            if needle in title.lower() and not _user32.IsIconic(wintypes.HWND(hwnd)):
                return hwnd
    except Exception as e:
        logger.debug(f"Захват окна: поиск окна: {e}")
    return None


def _crop_to_client(img, hwnd):
    """Обрезает кадр WGC (окно с рамкой) до клиентской области."""
    client = _client_bounds(hwnd)
    bounds = _dwm_frame_bounds(hwnd) or _window_rect(hwnd)
    if client is None or bounds is None:
        return img
    cx, cy, cw, ch = client
    left, top, right, bottom = bounds
    h, w = img.shape[:2]
    bw, bh = right - left, bottom - top
    if bw <= 0 or bh <= 0:
        return img
    # Если размеры кадра и рамки разошлись (например, из-за DPI), масштабируем
    # пропорционально — так обрезка останется корректной.
    sx, sy = w / bw, h / bh
    x0 = int(round((cx - left) * sx))
    y0 = int(round((cy - top) * sy))
    x1 = x0 + int(round(cw * sx))
    y1 = y0 + int(round(ch * sy))
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 - x0 < 32 or y1 - y0 < 32:
        return img
    return img[y0:y1, x0:x1]


def grab_window(title_substring, timeout=GRAB_TIMEOUT):
    """Снимок клиентской области окна игры в BGR или None.

    None означает «взять обычный снимок экрана»: окно не найдено, свёрнуто,
    захват недоступен или вернул пустой кадр.
    """
    if os.name != "nt":
        return None
    hwnd = find_game_window(title_substring)
    if hwnd is None:
        return None
    try:
        from windows_capture import WindowsCapture
    except Exception as e:
        logger.debug(f"Захват окна: windows-capture недоступен: {e}")
        return None

    holder = {}
    done = threading.Event()
    callback_finished = threading.Event()
    callback_finished.set()
    callback_lock = threading.Lock()
    stop_lock = threading.Lock()
    stopped = False
    control = None

    def stop_capture():
        """Останавливает WGC-сессию не более одного раза."""
        nonlocal stopped
        with stop_lock:
            if stopped:
                return
            stopped = True
        try:
            if control is not None:
                control.stop()
        except Exception as e:
            logger.debug(f"Захват окна: остановка сессии: {e}")

    try:
        capture = WindowsCapture(window_hwnd=hwnd, cursor_capture=None,
                                 draw_border=None)
    except Exception as e:
        logger.debug(f"Захват окна: не удалось создать сессию: {e}")
        return None

    @capture.event
    def on_frame_arrived(frame, _control):
        # Callback может прийти повторно до того, как управляющий поток
        # остановит сессию. Берём только первый кадр.
        with callback_lock:
            if "frame" in holder:
                return
            callback_finished.clear()
        try:
            holder["frame"] = np.asarray(frame.frame_buffer)[:, :, :3].copy()
        except Exception as e:
            logger.debug(f"Захват окна: ошибка кадра: {e}")
        finally:
            callback_finished.set()
            done.set()

    @capture.event
    def on_closed():
        done.set()

    try:
        control = capture.start_free_threaded()
    except Exception as e:
        logger.warning(f"Захват окна: сессия не запустилась: {e}")
        return None

    try:
        done.wait(max(0.0, float(timeout)))
    finally:
        # Сессию останавливает только управляющий поток. Callback не вызывает
        # stop() параллельно, исключая двойное освобождение ресурсов WGC.
        stop_capture()
        # Если кадр уже обрабатывался, дождаться завершения копирования до
        # чтения holder и возврата из функции.
        callback_finished.wait(1.0)

    img = holder.get("frame")
    if img is None or img.size == 0:
        return None
    if float(img.mean()) < BLACK_FRAME_MEAN:
        logger.debug("Захват окна: пустой (чёрный) кадр")
        return None
    img = _crop_to_client(img, hwnd)
    if img.size == 0:
        return None
    return np.ascontiguousarray(img)
