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
import math
import os
import threading
from ctypes import wintypes

import numpy as np

from chest_scanner.logging_setup import logger

# Сколько ждать первый кадр. WGC отдаёт кадр практически сразу, но при
# запуске сессии возможна задержка в сотни миллисекунд.
GRAB_TIMEOUT = 2.0
WGC_STOP_TIMEOUT = 1.0

# The native WGC backend owns a dedicated capture thread. Keep sessions
# serialized so overlapping requests cannot contend for native resources.
_capture_lock = threading.Lock()

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


def _client_crop_rect(image_shape, client, bounds):
    """Map a screen-coordinate client rectangle into a WGC frame."""
    try:
        h, w = int(image_shape[0]), int(image_shape[1])
        cx, cy, cw, ch = (float(value) for value in client)
        left, top, right, bottom = (float(value) for value in bounds)
    except (IndexError, TypeError, ValueError, OverflowError):
        return None

    values = (cx, cy, cw, ch, left, top, right, bottom)
    if (w <= 0 or h <= 0 or not all(math.isfinite(v) for v in values)
            or cw <= 0 or ch <= 0 or right <= left or bottom <= top):
        return None

    # The client area must be contained in the window frame. Do not return an
    # un-cropped frame on invalid geometry: that would feed borders/title bars
    # to the grid detector and shift every calibrated slot.
    client_right, client_bottom = cx + cw, cy + ch
    epsilon = 1.0
    if (cx < left - epsilon or cy < top - epsilon
            or client_right > right + epsilon
            or client_bottom > bottom + epsilon):
        return None

    sx, sy = w / (right - left), h / (bottom - top)
    x0 = int(round((cx - left) * sx))
    y0 = int(round((cy - top) * sy))
    x1 = int(round((client_right - left) * sx))
    y1 = int(round((client_bottom - top) * sy))
    # Clamp only floating-point rounding at the outer edge, not real overflow.
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 - x0 < 32 or y1 - y0 < 32:
        return None
    return x0, y0, x1, y1


def _crop_to_client(img, hwnd):
    """Crop a WGC frame to the game client area, accounting for DPI scaling."""
    client = _client_bounds(hwnd)
    bounds = _dwm_frame_bounds(hwnd) or _window_rect(hwnd)
    if client is None or bounds is None:
        return None
    rect = _client_crop_rect(img.shape, client, bounds)
    if rect is None:
        return None
    x0, y0, x1, y1 = rect
    return img[y0:y1, x0:x1]



def grab_window(title_substring, timeout=GRAB_TIMEOUT):
    """Capture one window frame in BGR, or return None on failure/timeout."""
    if os.name != "nt":
        return None
    try:
        wait_timeout = max(0.0, float(timeout))
    except (TypeError, ValueError, OverflowError):
        wait_timeout = GRAB_TIMEOUT
    if not math.isfinite(wait_timeout):
        wait_timeout = GRAB_TIMEOUT

    hwnd = find_game_window(title_substring)
    if hwnd is None:
        return None
    try:
        from windows_capture import WindowsCapture
    except Exception as e:
        logger.debug(f"Захват окна: windows-capture недоступен: {e}")
        return None

    # Include contention with another capture request in the caller's timeout.
    if not _capture_lock.acquire(timeout=wait_timeout):
        logger.debug("WGC capture: previous session is still shutting down")
        return None

    lock_release_deferred = False
    native_shutdown_safe = True
    try:
        holder = {}
        done = threading.Event()
        callback_finished = threading.Event()
        callback_finished.set()
        callback_lock = threading.Lock()
        callback_in_progress = False
        stop_lock = threading.Lock()
        stopped = False
        control = None

        def stop_capture():
            """Request a graceful stop exactly once."""
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
            # Only one callback may map/copy a frame. The native buffer is valid
            # only during this callback, so copy it before returning.
            nonlocal callback_in_progress
            with callback_lock:
                if "frame" in holder or callback_in_progress:
                    return
                callback_in_progress = True
                callback_finished.clear()
            try:
                holder["frame"] = np.asarray(frame.frame_buffer)[:, :, :3].copy()
            except Exception as e:
                logger.debug(f"Захват окна: ошибка кадра: {e}")
            finally:
                with callback_lock:
                    callback_in_progress = False
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
            done.wait(wait_timeout)
        finally:
            # stop() requests shutdown; wait() joins the native capture thread
            # so callbacks and native frame resources are no longer in use.
            stop_capture()
            if control is not None:
                native_wait_done = threading.Event()

                def wait_for_native_shutdown():
                    try:
                        control.wait()
                    except Exception as e:
                        logger.debug(f"WGC capture: shutdown wait failed: {e}")
                    finally:
                        native_wait_done.set()
                        # Keep the capture lock held until the native session
                        # really exits; later requests then fall back to MSS.
                        _capture_lock.release()

                waiter = threading.Thread(
                    target=wait_for_native_shutdown,
                    name="WGC-shutdown",
                    daemon=True,
                )
                try:
                    # Transfer lock release to the daemon waiter before start:
                    # a stuck native wait must never block the scanner worker.
                    lock_release_deferred = True
                    waiter.start()
                except Exception as e:
                    lock_release_deferred = False
                    native_shutdown_safe = False
                    logger.warning("WGC shutdown monitor could not start: %s", e)

                if lock_release_deferred and not native_wait_done.wait(WGC_STOP_TIMEOUT):
                    logger.warning(
                        "WGC native session did not stop within %.1f s; "
                        "window capture is disabled until it exits",
                        WGC_STOP_TIMEOUT,
                    )
            callback_finished.wait(1.0)

        if not native_shutdown_safe:
            return None
        img = holder.get("frame")
        if img is None or img.size == 0:
            return None
        if float(img.mean()) < BLACK_FRAME_MEAN:
            logger.debug("Захват окна: пустой (чёрный) кадр")
            return None
        img = _crop_to_client(img, hwnd)
        if img is None or img.size == 0:
            return None
        return np.ascontiguousarray(img)
    finally:
        if not lock_release_deferred:
            _capture_lock.release()
