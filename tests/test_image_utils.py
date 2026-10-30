"""Тесты утилит обработки изображений (image_utils)."""
import cv2
import numpy as np

from chest_scanner import image_utils as iu


def _gradient():
    x = np.linspace(0, 255, 48, dtype=np.float32)
    g = np.tile(x, (48, 1))
    return np.stack([g] * 3, axis=-1).astype(np.uint8)


def _checker(cell=6):
    rows, cols = 48 // cell, 48 // cell
    base = np.indices((rows, cols)).sum(axis=0) % 2
    img = np.kron(base, np.ones((cell, cell), dtype=np.uint8))
    return np.stack([img * 255] * 3, axis=-1).astype(np.uint8)


def test_hamming_basic():
    assert iu.hamming(0, 0) == 0
    assert iu.hamming(1, 0) == 1
    assert iu.hamming(0b1111, 0b0000) == 4


def test_phash_of_same_image_is_equal():
    img = _checker()
    assert iu.phash(img) == iu.phash(img.copy())


def test_phash_close_images_similar():
    a = _checker()
    b = cv2.GaussianBlur(_checker(), (3, 3), 0)
    assert iu.hamming(iu.phash(a), iu.phash(b)) <= 10


def test_phash_different_images_distant():
    assert iu.hamming(iu.phash(_checker()), iu.phash(_gradient())) >= 4


def test_phash_invalid_returns_zero():
    assert iu.phash(None) == 0
    assert iu.phash(np.zeros((0, 0, 3), dtype=np.uint8)) == 0


def test_preprocess_icon_shape_preserved():
    img = _checker()
    out = iu.preprocess_icon(img)
    assert out is not None and out.shape == img.shape


def test_preprocess_icon_invalid_returns_input():
    img = np.zeros((0, 0, 3), dtype=np.uint8)
    assert iu.preprocess_icon(img) is img


def test_resize_and_crop():
    img = np.zeros((100, 120, 3), dtype=np.uint8)
    rs = iu.resize_image(img, 48, 48)
    assert rs.shape[:2] == (48, 48)
    cr = iu.crop_relative(img, [0.25, 0.25, 0.5, 0.5])
    assert cr.shape[:2] == (50, 60)