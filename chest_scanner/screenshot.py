"""Захват экрана через mss (поточный одноразовый объект)."""
import os
import threading
import time

import cv2
import numpy as np

from chest_scanner.app_bootstrap import mss
from chest_scanner.logging_setup import logger

_mss_local = threading.local()

# Источник изображения: обычный снимок экрана или клиентская область окна
# игры (Windows Graphics Capture). Оконные координаты не зависят от
# перекрытий и от того, на каком мониторе игра, но окно должно быть
# развёрнуто (не свёрнуто).
CAPTURE_SOURCE_SCREEN = "screen"
CAPTURE_SOURCE_WINDOW = "window"
CAPTURE_SOURCES = (CAPTURE_SOURCE_SCREEN, CAPTURE_SOURCE_WINDOW)

_capture = {"source": CAPTURE_SOURCE_SCREEN, "title": "Minecraft"}
_last_window_warning = 0.0


def configure_capture(source=None, title=None):
    """Задаёт источник изображения (вызывается при загрузке конфига)."""
    if source in CAPTURE_SOURCES:
        _capture["source"] = source
    if title is not None:
        _capture["title"] = str(title).strip() or "Minecraft"
    return dict(_capture)


def capture_settings():
    return dict(_capture)


def _window_screenshot():
    """Снимок окна игры или None, если захват недоступен."""
    global _last_window_warning
    try:
        from chest_scanner.capture import grab_window
    except Exception as e:
        logger.debug(f"Захват окна недоступен: {e}")
        return None
    img = grab_window(_capture["title"])
    if img is not None:
        return img
    now = time.monotonic()
    # Предупреждаем не чаще раза в 30 с: иначе логи забиваются на каждом кадре.
    if now - _last_window_warning > 30:
        _last_window_warning = now
        logger.warning(f"Захват окна «{_capture['title']}» недоступен "
                       "(окно свёрнуто или не найдено) — снят весь экран.")
    return None


def _get_sct():
    s = getattr(_mss_local, "sct", None)
    if s is None:
        s = mss()
        _mss_local.sct = s
    return s


def flush_compositor():
    """Дожидается, пока DWM соберёт кадр после hide()/show() окна.

    hide() возвращает управление сразу, а окно исчезает с экрана только
    после перерисовки композитором. Без этого снимок может успеть
    захватить ещё не убранное окно.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.dwmapi.DwmFlush()
    except Exception:
        pass


def screenshot_bgr():
    if _capture["source"] == CAPTURE_SOURCE_WINDOW:
        img = _window_screenshot()
        if img is not None:
            return img
    sct = _get_sct()
    mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
    raw = sct.grab(mon)
    return cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)


def screenshot_region_bgr(x, y, w, h):
    if _capture["source"] == CAPTURE_SOURCE_WINDOW:
        # В оконном режиме координаты тоже оконные — вырезаем из того же кадра.
        img = _window_screenshot()
        if img is not None:
            ih, iw = img.shape[:2]
            x0 = min(max(0, int(x)), iw - 1)
            y0 = min(max(0, int(y)), ih - 1)
            x1 = min(iw, x0 + max(1, int(w)))
            y1 = min(ih, y0 + max(1, int(h)))
            return img[y0:y1, x0:x1]
    sct = _get_sct()
    region = {"left": int(x), "top": int(y), "width": max(1, int(w)), "height": max(1, int(h))}
    raw = sct.grab(region)
    return cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)
