"""Захват экрана через mss (поточный одноразовый объект)."""
import threading

import cv2
import numpy as np

from chest_scanner.app_bootstrap import mss

_mss_local = threading.local()


def _get_sct():
    s = getattr(_mss_local, "sct", None)
    if s is None:
        s = mss()
        _mss_local.sct = s
    return s


def screenshot_bgr():
    sct = _get_sct()
    mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
    raw = sct.grab(mon)
    return cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)


def screenshot_region_bgr(x, y, w, h):
    sct = _get_sct()
    region = {"left": int(x), "top": int(y), "width": max(1, int(w)), "height": max(1, int(h))}
    raw = sct.grab(region)
    return cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)
