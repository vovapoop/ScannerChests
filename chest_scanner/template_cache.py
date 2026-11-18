"""Кэш шаблонов предметов: сырые изображения и ресайзнутые копии."""
import os
import threading
from collections import OrderedDict

import cv2
import numpy as np

from chest_scanner.commons import absolute_path
from chest_scanner.image_utils import resize_image


class TemplateCache:
    """Thread-safe image cache with independent raw/resized memory limits."""

    DEFAULT_MAX_RAW_BYTES = 128 * 1024 * 1024
    DEFAULT_MAX_RESIZED_BYTES = 32 * 1024 * 1024

    def __init__(self, max_raw_bytes=DEFAULT_MAX_RAW_BYTES,
                 max_resized_bytes=DEFAULT_MAX_RESIZED_BYTES):
        self.max_raw_bytes = max(0, int(max_raw_bytes))
        self.max_resized_bytes = max(0, int(max_resized_bytes))
        self._cache = OrderedDict()
        self._mtime = {}
        self._resized = OrderedDict()
        self._lock = threading.RLock()

    @staticmethod
    def _image_bytes(image):
        return int(getattr(image, "nbytes", 0))

    def _raw_bytes(self):
        return sum(self._image_bytes(image) for image in self._cache.values())

    def _resized_bytes(self):
        return sum(self._image_bytes(image) for image in self._resized.values())

    def _remove_resized_for(self, path):
        for key in [key for key in self._resized if key[0] == path]:
            del self._resized[key]

    def get_raw(self, path):
        with self._lock:
            full = absolute_path(path)
            if not full or not os.path.exists(full):
                # A previously cached image must not survive deletion or a
                # temporarily unavailable path. Otherwise a later file with
                # matching metadata could be mistaken for the old image.
                if path:
                    self.invalidate(path)
                return None
            try:
                stat = os.stat(full)
                mt = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                self.invalidate(path)
                return None
            if path in self._cache and self._mtime.get(path) == mt:
                self._cache.move_to_end(path)
                return self._cache[path]
            self._cache.pop(path, None)
            self._mtime.pop(path, None)
            self._remove_resized_for(path)
            try:
                img = cv2.imdecode(np.fromfile(full, dtype=np.uint8), cv2.IMREAD_COLOR)
            except Exception:
                img = cv2.imread(full, cv2.IMREAD_COLOR)
            if img is None:
                return None
            image_bytes = self._image_bytes(img)
            if self.max_raw_bytes and image_bytes <= self.max_raw_bytes:
                self._cache[path] = img
                self._mtime[path] = mt
                while self._cache and self._raw_bytes() > self.max_raw_bytes:
                    oldest, _ = self._cache.popitem(last=False)
                    self._mtime.pop(oldest, None)
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
                self._resized.move_to_end(key)
                return self._resized[key]
            resized = resize_image(raw, w, h)
            image_bytes = self._image_bytes(resized)
            if (resized is not None and self.max_resized_bytes
                    and image_bytes <= self.max_resized_bytes):
                self._resized[key] = resized
                while self._resized and self._resized_bytes() > self.max_resized_bytes:
                    self._resized.popitem(last=False)
            return resized

    def invalidate(self, path=None):
        with self._lock:
            if path:
                self._cache.pop(path, None)
                self._mtime.pop(path, None)
                self._remove_resized_for(path)
            else:
                self._cache.clear()
                self._mtime.clear()
                self._resized.clear()

    @property
    def size(self):
        with self._lock:
            return len(self._cache)

    @property
    def memory_bytes(self):
        """Approximate NumPy image memory currently retained by both caches."""
        with self._lock:
            return self._raw_bytes() + self._resized_bytes()


template_cache = TemplateCache()
