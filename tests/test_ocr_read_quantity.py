"""Regression tests for safe quantity OCR failure handling."""
import os

import cv2
import numpy as np
import pytest

from chest_scanner import paths
from chest_scanner.scanner.ocr import OcrMixin


class StubOcr(OcrMixin):
    def __init__(self):
        self.digit_templates = {"0": np.ones((32, 24), dtype=np.uint8)}
        self.config = {"count_roi_in_slot": [0.5, 0.5, 0.3, 0.3]}

    def _digit_char_boxes(self, image):
        return [(1, 1, 5, 8)]


def _slot():
    return np.full((120, 160, 3), 180, dtype=np.uint8)


def test_missing_templates_returns_unknown(monkeypatch):
    reader = StubOcr()
    reader.digit_templates = {}
    assert reader.read_quantity(_slot()) is None


def test_empty_count_area_returns_unknown(monkeypatch):
    reader = StubOcr()
    monkeypatch.setattr("chest_scanner.scanner.ocr.crop_relative", lambda *_: None)
    assert reader.read_quantity(_slot()) is None


def test_blank_count_roi_means_one_item(monkeypatch):
    reader = StubOcr()
    monkeypatch.setattr(
        "chest_scanner.scanner.ocr.crop_relative",
        lambda *_: np.zeros((20, 20, 3), dtype=np.uint8),
    )
    reader._digit_char_boxes = lambda _image: []
    assert reader.read_quantity(_slot()) == 1


def test_visible_but_unsegmented_count_remains_unknown(monkeypatch):
    reader = StubOcr()
    visible = np.zeros((20, 20, 3), dtype=np.uint8)
    visible[5:15, 8:12] = 255
    monkeypatch.setattr(
        "chest_scanner.scanner.ocr.crop_relative", lambda *_: visible
    )
    reader._digit_char_boxes = lambda _image: []
    assert reader.read_quantity(_slot()) is None


def test_unreadable_digit_returns_unknown():
    reader = StubOcr()
    reader.match_digit = lambda *_: (None, 0.0)
    assert reader.read_quantity(_slot()) is None


def test_low_confidence_digit_returns_unknown():
    reader = StubOcr()
    reader.match_digit = lambda *_: ("6", 0.34)
    assert reader.read_quantity(_slot()) is None


def test_valid_digit_is_returned():
    reader = StubOcr()
    reader.match_digit = lambda *_: ("6", 0.80)
    assert reader.read_quantity(_slot()) == 6


def test_one_unreadable_character_does_not_get_dropped():
    reader = StubOcr()
    reader._digit_char_boxes = lambda _image: [(1, 1, 5, 8), (6, 1, 10, 8)]
    scores = iter([("6", 0.9), (None, 0.0)])
    reader.match_digit = lambda *_: next(scores)
    assert reader.read_quantity(_slot()) is None


def test_match_digit_rejects_ambiguous_templates():
    reader = StubOcr()
    shared = np.zeros((32, 24), dtype=np.uint8)
    shared[4:28, 8:16] = 255
    reader.digit_templates = {"3": shared, "8": shared.copy()}
    digit, score = reader.match_digit(shared)
    assert digit is None
    assert score > 0.5


def test_match_digit_accepts_clear_template_match():
    reader = StubOcr()
    reader.digit_templates = {}
    for digit in range(10):
        image = cv2.imread(
            os.path.join(paths.DIGITS_DIR, f"{digit}.png"),
            cv2.IMREAD_GRAYSCALE,
        )
        reader.digit_templates[str(digit)] = reader.prepare_digit(image)
    image = cv2.imread(os.path.join(paths.DIGITS_DIR, "6.png"), cv2.IMREAD_GRAYSCALE)
    assert reader.match_digit(image)[0] == "6"


def test_low_raw_similarity_is_rejected():
    reader = StubOcr()
    reader.digit_templates = {"6": np.zeros((32, 24), dtype=np.uint8)}
    assert reader.match_digit(np.zeros((10, 10), dtype=np.uint8))[0] is None


def test_split_component_separates_touching_digits_at_projection_valley():
    image = np.zeros((32, 40), dtype=np.uint8)
    image[2:30, 1:17] = 255
    image[2:30, 23:39] = 255
    image[14:18, 16:24] = 255  # narrow bridge joins the two glyphs
    parts = OcrMixin._split_component(image, 0, 0, 40, 32, 40)
    assert len(parts) == 2
    assert parts[0][0] < parts[1][0]
    assert all(part[2] >= 10 for part in parts)


def test_split_component_keeps_single_wide_glyph_intact():
    image = np.zeros((32, 30), dtype=np.uint8)
    image[2:30, 2:28] = 255
    parts = OcrMixin._split_component(image, 0, 0, 30, 32, 30)
    assert parts == [(0, 0, 30, 32)]


def test_split_component_keeps_glyph_when_no_clear_valley_exists():
    image = np.zeros((32, 40), dtype=np.uint8)
    image[2:30, 2:38] = 255
    parts = OcrMixin._split_component(image, 0, 0, 40, 32, 40)
    assert parts == [(0, 0, 40, 32)]


def test_find_digit_boxes_splits_connected_pair_end_to_end():
    image = np.zeros((40, 60), dtype=np.uint8)
    image[2:30, 5:21] = 255
    image[2:30, 29:45] = 255
    image[14:18, 20:30] = 255  # narrow bridge joins both glyphs

    boxes = OcrMixin._find_digit_boxes(OcrMixin.__new__(OcrMixin), image)

    assert len(boxes) == 2
    assert boxes[0][0] < boxes[1][0]
    assert all(12 <= (x2 - x1) <= 20 for x1, _, x2, _ in boxes)


@pytest.mark.parametrize("bad_image", [
    None,
    np.array([], dtype=np.uint8),
    np.zeros((10,), dtype=np.uint8),
    np.zeros((10, 10, 4), dtype=np.uint8),
    np.zeros((10, 10), dtype=np.float32),
    np.full((10, 10), np.nan, dtype=np.float32),
])
def test_invalid_slot_image_returns_unknown(bad_image):
    assert StubOcr().read_quantity(bad_image) is None


@pytest.mark.parametrize("roi", [
    [-0.1, 0.5, 0.3, 0.3],
    [0.8, 0.5, 0.3, 0.3],
    [0.5, 0.8, 0.3, 0.3],
    [0.5, 0.5, 0.0, 0.3],
    [0.5, 0.5, float("nan"), 0.3],
])
def test_invalid_or_clipped_count_roi_returns_unknown(roi):
    reader = StubOcr()
    reader.config["count_roi_in_slot"] = roi
    assert reader.read_quantity(_slot()) is None


def test_valid_roi_touching_slot_edge_is_allowed(monkeypatch):
    reader = StubOcr()
    reader.config["count_roi_in_slot"] = [0.7, 0.7, 0.3, 0.3]
    monkeypatch.setattr(
        "chest_scanner.scanner.ocr.crop_relative",
        lambda *_: np.zeros((20, 20, 3), dtype=np.uint8),
    )
    reader._digit_char_boxes = lambda _image: []
    assert reader.read_quantity(_slot()) == 1


def test_one_corrupt_digit_template_does_not_abort_remaining_load(monkeypatch):
    original_imread = cv2.imread

    def imread(path, flags):
        if path.endswith("\\3.png") or path.endswith("/3.png"):
            raise cv2.error("simulated corrupt template")
        if path.endswith("\\7.png") or path.endswith("/7.png"):
            return None
        return original_imread(path, flags)

    monkeypatch.setattr("chest_scanner.scanner.ocr.cv2.imread", imread)
    templates = StubOcr().load_digit_templates()

    assert "3" not in templates
    assert "7" not in templates
    assert set(templates) == set("01245689")
    assert all(image.shape == (32, 24) for image in templates.values())
