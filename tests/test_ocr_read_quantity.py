"""Регрессионный smoke-тест: read_quantity() не должен падать с NameError.

Проверяет, что OcrMixin.read_quantity() корректно работает без реального
экрана: имитируется слот с загруженными шаблонами цифр (ветка кода, где
раньше возникало NameError: name 'crop_relative' is not defined).

Запуск:  python tests/test_ocr_read_quantity.py
"""
import os
import sys

import numpy as np

# Корень проекта, чтобы пакет chest_scanner был импортируем из любого места.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chest_scanner.scanner.ocr import OcrMixin


class StubOcr(OcrMixin):
    """Минимальный стенд: только то, что нужно read_quantity()."""

    def __init__(self):
        self.digit_templates = {"0": np.full((32, 24), 255, dtype=np.uint8)}
        self.config = {"count_roi_in_slot": [0.5, 0.5, 0.3, 0.3]}


def _run_case(slot_image, label):
    stub = StubOcr()
    q = stub.read_quantity(slot_image)
    print(f"[{label}] read_quantity -> {q}")
    assert q == 1, f"[{label}] ожидалось 1, получено {q}"


def main():
    # Чёрный слот: маска пустая, цифр нет.
    _run_case(np.zeros((120, 160, 3), dtype=np.uint8), "black slot")
    # Белый слот: один большой компонент, отфильтровывается по размеру.
    _run_case(np.full((120, 160, 3), 255, dtype=np.uint8), "white slot")
    # Случайный шум: компоненты есть, но не похожи на цифры.
    rng = np.random.default_rng(42)
    _run_case(rng.integers(0, 256, size=(120, 160, 3), dtype=np.uint8), "noise slot")
    print("SMOKE_OK: NameError не возникает, read_quantity() работает")


if __name__ == "__main__":
    main()