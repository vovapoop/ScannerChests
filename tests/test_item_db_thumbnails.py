"""Tests for bounded, size-aware GUI thumbnail loading."""

import cv2
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from chest_scanner.gui import item_db_view


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _write_image(path, width=400, height=200, value=90):
    image = np.full((height, width, 3), value, dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    path.write_bytes(encoded.tobytes())


def test_thumbnail_is_decoded_at_requested_size_and_reused(
        qapp, tmp_path, monkeypatch):
    image_path = tmp_path / "wide.png"
    _write_image(image_path)
    monkeypatch.setattr(item_db_view, "absolute_path", lambda _path: str(image_path))
    cache = item_db_view._ThumbnailCache(max_bytes=1024 * 1024)
    monkeypatch.setattr(item_db_view, "_thumbnail_cache", cache)

    first = item_db_view._thumb_pixmap("wide.png", 40)
    second = item_db_view._thumb_pixmap("wide.png", 40)

    assert first is not None
    assert first.width() == 40
    assert first.height() == 20
    assert second is first
    assert cache.memory_bytes == 40 * 20 * 4


def test_thumbnail_cache_is_bounded_and_lru_evicts_old_entries(
        qapp, tmp_path, monkeypatch):
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    _write_image(first_path, width=20, height=20)
    _write_image(second_path, width=20, height=20, value=120)
    monkeypatch.setattr(
        item_db_view, "absolute_path", lambda path: str(tmp_path / path))
    cache = item_db_view._ThumbnailCache(max_bytes=20 * 20 * 4)

    cache.get("first.png", 20)
    cache.get("first.png", 20)
    cache.get("second.png", 20)

    assert cache.memory_bytes == cache.max_bytes
    assert len(cache._cache) == 1
    assert next(iter(cache._cache))[0].endswith("second.png")


def test_thumbnail_file_change_uses_new_cache_entry(qapp, tmp_path, monkeypatch):
    image_path = tmp_path / "changing.png"
    _write_image(image_path, width=30, height=20, value=10)
    monkeypatch.setattr(item_db_view, "absolute_path", lambda _path: str(image_path))
    cache = item_db_view._ThumbnailCache(max_bytes=1024 * 1024)

    old = cache.get("changing.png", 20)
    _write_image(image_path, width=40, height=20, value=220)
    import os
    os.utime(image_path, ns=(image_path.stat().st_atime_ns,
                             image_path.stat().st_mtime_ns + 2_000_000))
    new = cache.get("changing.png", 20)

    assert old is not None and new is not None
    assert new is not old
    assert new.width() == 20
    assert new.height() == 10
    assert len(cache._cache) == 1
    assert cache.memory_bytes == 20 * 10 * 4


def test_file_change_invalidates_all_cached_thumbnail_sizes(
        qapp, tmp_path, monkeypatch):
    image_path = tmp_path / "multi-size.png"
    _write_image(image_path, width=80, height=40, value=20)
    monkeypatch.setattr(item_db_view, "absolute_path", lambda _path: str(image_path))
    cache = item_db_view._ThumbnailCache(max_bytes=1024 * 1024)

    old_small = cache.get("multi-size.png", 20)
    old_large = cache.get("multi-size.png", 40)
    assert len(cache._cache) == 2

    _write_image(image_path, width=100, height=50, value=230)
    import os
    os.utime(image_path, ns=(image_path.stat().st_atime_ns,
                             image_path.stat().st_mtime_ns + 2_000_000))
    new_small = cache.get("multi-size.png", 20)

    assert new_small is not None and new_small is not old_small
    assert new_small.toImage() != old_small.toImage()
    assert len(cache._cache) == 1
    assert cache.memory_bytes == 20 * 10 * 4
    assert old_large is not None


def test_deleted_image_releases_cached_thumbnail(qapp, tmp_path, monkeypatch):
    image_path = tmp_path / "deleted.png"
    _write_image(image_path, width=32, height=24)
    monkeypatch.setattr(item_db_view, "absolute_path", lambda _path: str(image_path))
    cache = item_db_view._ThumbnailCache(max_bytes=1024 * 1024)

    assert cache.get("deleted.png", 32) is not None
    assert cache.memory_bytes > 0
    image_path.unlink()

    assert cache.get("deleted.png", 32) is None
    assert cache.memory_bytes == 0
    assert not cache._cache


def test_explicit_invalidation_removes_all_size_variants(qapp, tmp_path, monkeypatch):
    image_path = tmp_path / "invalidate.png"
    _write_image(image_path, width=64, height=32)
    monkeypatch.setattr(item_db_view, "absolute_path", lambda _path: str(image_path))
    cache = item_db_view._ThumbnailCache(max_bytes=1024 * 1024)
    cache.get("invalidate.png", 16)
    cache.get("invalidate.png", 32)

    cache.invalidate("invalidate.png")

    assert not cache._cache
    assert cache.memory_bytes == 0


def test_corrupt_and_missing_images_fail_gracefully(qapp, tmp_path, monkeypatch):
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not an image")
    monkeypatch.setattr(
        item_db_view, "absolute_path", lambda path: str(tmp_path / path))
    cache = item_db_view._ThumbnailCache()

    assert cache.get("corrupt.png", 32) is None
    assert cache.get("missing.png", 32) is None
    assert cache.memory_bytes == 0
    assert not cache._cache


def test_thumbnail_cache_clear_releases_entries(qapp, tmp_path, monkeypatch):
    image_path = tmp_path / "clear.png"
    _write_image(image_path, width=24, height=24)
    monkeypatch.setattr(item_db_view, "absolute_path", lambda _path: str(image_path))
    cache = item_db_view._ThumbnailCache(max_bytes=1024 * 1024)
    cache.get("clear.png", 24)

    assert cache.memory_bytes > 0
    cache.clear()
    assert not cache._cache
    assert cache.memory_bytes == 0



def test_read_scaled_pixmap_never_decodes_above_requested_bounds(
        qapp, tmp_path):
    image_path = tmp_path / "large.png"
    _write_image(image_path, width=2400, height=1200)

    pixmap = item_db_view._read_scaled_pixmap(
        str(image_path), item_db_view.QSize(80, 80))

    assert pixmap is not None
    assert (pixmap.width(), pixmap.height()) == (80, 40)


def test_photo_validation_uses_small_scaled_decode(
        qapp, tmp_path, monkeypatch):
    image_path = tmp_path / "validation.png"
    _write_image(image_path, width=1600, height=800)
    monkeypatch.setattr(
        item_db_view, "absolute_path", lambda _path: str(image_path))
    requested_bounds = []
    original = item_db_view._read_scaled_pixmap

    def record_bounds(path, bounds):
        requested_bounds.append((bounds.width(), bounds.height()))
        return original(path, bounds)

    monkeypatch.setattr(item_db_view, "_read_scaled_pixmap", record_bounds)

    assert item_db_view._photo_problem("validation.png") is None
    assert requested_bounds == [(32, 32)]
