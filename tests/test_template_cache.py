"""Memory-bound and invalidation tests for the image template cache."""

import cv2
import numpy as np

from chest_scanner.template_cache import TemplateCache


def _write_image(path, value=80, shape=(10, 10, 3)):
    image = np.full(shape, value, dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    path.write_bytes(encoded.tobytes())
    return image.nbytes


def test_raw_cache_is_lru_bounded_and_does_not_cache_oversized_images(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        "chest_scanner.template_cache.absolute_path",
        lambda path: str(tmp_path / path))
    first_bytes = _write_image(tmp_path / "first.png")
    _write_image(tmp_path / "second.png", value=100)
    cache = TemplateCache(max_raw_bytes=first_bytes, max_resized_bytes=0)

    first = cache.get_raw("first.png")
    assert first is not None
    assert cache.get_raw("first.png") is first  # hit promotes the LRU entry
    assert cache.get_raw("second.png") is not None
    assert "first.png" not in cache._cache
    assert "second.png" in cache._cache
    assert cache.memory_bytes <= cache.max_raw_bytes

    oversized = TemplateCache(max_raw_bytes=first_bytes - 1)
    assert oversized.get_raw("first.png") is not None
    assert oversized.size == 0
    assert oversized.memory_bytes == 0


def test_resized_cache_is_bounded_and_path_invalidation_clears_both(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        "chest_scanner.template_cache.absolute_path",
        lambda path: str(tmp_path / path))
    _write_image(tmp_path / "sample.png")
    cache = TemplateCache(max_raw_bytes=10_000, max_resized_bytes=10 * 10 * 3)

    small = cache.get_resized("sample.png", 10, 10)
    assert small is not None
    cache.get_resized("sample.png", 9, 9)
    assert cache._resized_bytes() <= cache.max_resized_bytes

    cache.invalidate("sample.png")
    assert "sample.png" not in cache._cache
    assert "sample.png" not in cache._mtime
    assert not cache._resized
    assert cache.memory_bytes == 0


def test_changed_file_replaces_stale_raw_and_resized_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "chest_scanner.template_cache.absolute_path",
        lambda path: str(tmp_path / path))
    path = tmp_path / "sample.png"
    _write_image(path, value=20)
    cache = TemplateCache(max_raw_bytes=10_000, max_resized_bytes=10_000)
    old = cache.get_raw("sample.png")
    cache.get_resized("sample.png", 5, 5)

    _write_image(path, value=220, shape=(12, 12, 3))
    # Ensure the metadata change is observable even on coarse timestamp filesystems.
    import os
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 2_000_000))
    new = cache.get_raw("sample.png")

    assert new is not old
    assert new.shape == (12, 12, 3)
    assert not cache._resized


def test_missing_file_invalidates_raw_and_resized_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "chest_scanner.template_cache.absolute_path",
        lambda path: str(tmp_path / path))
    path = tmp_path / "sample.png"
    _write_image(path, value=90)
    cache = TemplateCache(max_raw_bytes=10_000, max_resized_bytes=10_000)

    assert cache.get_raw("sample.png") is not None
    assert cache.get_resized("sample.png", 5, 5) is not None
    assert cache.memory_bytes > 0

    path.unlink()

    assert cache.get_raw("sample.png") is None
    assert "sample.png" not in cache._cache
    assert "sample.png" not in cache._mtime
    assert not cache._resized
    assert cache.memory_bytes == 0
