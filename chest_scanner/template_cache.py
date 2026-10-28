"""Кэш шаблонов предметов: сырые изображения и ресайзнутые копии."""
import os
import threading

import cv2

from chest_scanner.commons import absolute_path
from chest_scanner.image_utils import resize_image


class TemplateCache:
    def __init__(self):
        self._cache = {}
        self._mtime = {}
        self._resized = {}
        self._lock = threading.RLock()

    def get_raw(self, path):
        with self._lock:
            full = absolute_path(path)
            if not full or not os.path.exists(full):
                return None
            try:
                mt = os.path.getmtime(full)
            except OSError:
                return None
            if path in self._cache and self._mtime.get(path) == mt:
                return self._cache[path]
            img = cv2.imread(full, cv2.IMREAD_COLOR)
            if img is None:
                return None
            self._cache[path] = img
            self._mtime[path] = mt
            for k in [k for k in self._resized if k[0] == path]:
                del self._resized[k]
            return img

    def get_resized(self, path, w, h):
        with self._lock:
            w, h = int(w), int(h)
            if w <= 0 or h <= 0:
                return None
            raw = self.get_raw(path)
            if raw is None:
                return None
            key = (path, w, h)
            if key in self._resized:
                return self._resized[key]
            resized = resize_image(raw, w, h)
            if resized is not None:
                self._resized[key] = resized
            return resized

    def invalidate(self, path=None):
        with self._lock:
            if path:
                self._cache.pop(path, None)
                self._mtime.pop(path, None)
                for k in [k for k in self._resized if k[0] == path]:
                    del self._resized[k]
            else:
                self._cache.clear()
                self._mtime.clear()
                self._resized.clear()

    @property
    def size(self):
        with self._lock:
            return len(self._cache)


template_cache = TemplateCache()
