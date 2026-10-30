"""Тесты автоопределения границ сундука (без реального экрана).

Синтетические экраны имитируют интерфейс Minecraft: тёмный фон,
светло-серая панель GUI и тёмные линии ячеек; часть ячеек может быть
заполнена «предметами». Интеграционные тесты используют реальные
скриншоты из tests/screenshots/ (пропускаются, если файлов нет).
"""
import os

import cv2
import numpy as np
import pytest

from chest_scanner.scanner.grid import GridMixin

SCREENS_DIR = os.path.join(os.path.dirname(__file__), "screenshots")


class GridStub(GridMixin):
    """Стенд без реального экрана: только пресет сетки."""

    def __init__(self, columns, rows):
        self.grid = {"columns": columns, "rows": rows}


def make_chest_screen(columns, rows, cell=36, offset=(400, 300),
                      filled=(), shape=(1080, 1920)):
    """Рисует экран с одной панелью сундука.

    Возвращает (изображение BGR, ожидаемый прямоугольник сетки
    (x, y, w, h) — координаты крайних линий).
    """
    img = np.full((shape[0], shape[1], 3), 40, dtype=np.uint8)
    ox, oy = offset
    gw, gh = columns * cell, rows * cell
    # Панель GUI: светло-серая, с толстой тёмной рамкой (blackhat её не
    # выделяет — рамка шире ядра).
    cv2.rectangle(img, (ox - 12, oy - 44), (ox + gw + 12, oy + gh + 44),
                  (110, 110, 110), 10)
    cv2.rectangle(img, (ox - 12, oy - 44), (ox + gw + 12, oy + gh + 44),
                  (205, 205, 205), -1)
    # Линии ячеек.
    for i in range(columns + 1):
        cv2.line(img, (ox + i * cell, oy), (ox + i * cell, oy + gh),
                 (55, 55, 55), 2)
    for j in range(rows + 1):
        cv2.line(img, (ox, oy + j * cell), (ox + gw, oy + j * cell),
                 (55, 55, 55), 2)
    # Заполненные ячейки: цветные квадраты внутри.
    for idx in filled:
        r, c = divmod(idx, columns)
        x1 = ox + c * cell + 4
        y1 = oy + r * cell + 4
        color = ((idx * 53) % 256, (idx * 97) % 256, (idx * 31) % 256)
        cv2.rectangle(img, (x1, y1), (x1 + cell - 8, y1 + cell - 8), color, -1)
    return img, (ox, oy, gw, gh)


def assert_close(actual, expected, tol=2):
    assert abs(actual["x"] - expected[0]) <= tol
    assert abs(actual["y"] - expected[1]) <= tol
    assert abs(actual["width"] - expected[2]) <= tol
    assert abs(actual["height"] - expected[3]) <= tol


def test_detect_rect_9x6():
    screen, expected = make_chest_screen(9, 6)
    rect = GridStub(9, 6).detect_grid_rect(screen)
    assert rect is not None
    assert rect["columns"] == 9 and rect["rows"] == 6
    assert_close(rect, expected)


def test_detect_rect_9x3():
    screen, expected = make_chest_screen(9, 3, offset=(700, 350))
    rect = GridStub(9, 3).detect_grid_rect(screen)
    assert rect is not None
    assert rect["columns"] == 9 and rect["rows"] == 3
    assert_close(rect, expected)


def test_detect_scaled_4k():
    screen, expected = make_chest_screen(9, 6, cell=72, offset=(800, 600),
                                         shape=(2160, 3840))
    rect = GridStub(9, 6).detect_grid_rect(screen)
    assert rect is not None
    assert rect["columns"] == 9 and rect["rows"] == 6
    assert_close(rect, expected)


def test_detect_with_items():
    screen, expected = make_chest_screen(9, 6, filled=(0, 5, 17, 26, 33, 40))
    rect = GridStub(9, 6).detect_grid_rect(screen)
    assert rect is not None
    assert rect["columns"] == 9 and rect["rows"] == 6
    assert_close(rect, expected)


def test_detect_no_chest():
    # Ровный тёмный экран с шумом — сетки нет.
    rng = np.random.default_rng(42)
    screen = rng.integers(30, 60, size=(1080, 1920, 3), dtype=np.uint8)
    assert GridStub(9, 6).detect_grid_rect(screen) is None


def test_upper_candidate_preferred():
    # Две сетки 9×3: верхняя (сундук) и нижняя (инвентарь игрока).
    top, _ = make_chest_screen(9, 3, offset=(700, 250))
    bottom, bottom_rect = make_chest_screen(9, 3, offset=(700, 700))
    screen = np.maximum(top, bottom)
    rect = GridStub(9, 3).detect_grid_rect(screen)
    assert rect is not None
    assert rect["y"] < bottom_rect[1]  # выбрана верхняя


def test_preset_priority():
    # Сверху сетка 9×6, снизу 9×3; пресет 9×3 → выбирается нижняя.
    top, _ = make_chest_screen(9, 6, offset=(700, 150))
    bottom, bottom_rect = make_chest_screen(9, 3, offset=(700, 750))
    screen = np.maximum(top, bottom)
    rect = GridStub(9, 3).detect_grid_rect(screen)
    assert rect is not None
    assert rect["columns"] == 9 and rect["rows"] == 3
    assert abs(rect["y"] - bottom_rect[1]) <= 2


def test_extend_peaks():
    # Крайние линии достраиваются по регулярному шагу.
    peaks = GridStub._extend_peaks([36.0, 72.0, 108.0], 0.0, 144.0)
    assert peaks[0] == pytest.approx(0.0)
    assert peaks[-1] == pytest.approx(144.0)
    # Если шаг не помещается в границы — пик не добавляется.
    peaks = GridStub._extend_peaks([36.0, 72.0, 108.0], 10.0, 120.0)
    assert peaks[0] == pytest.approx(36.0) and peaks[-1] == pytest.approx(108.0)


REAL_CASES = [
    ("double_chest_9x6_empty.png", 9, 6),
    ("double_chest_9x6_filled.png", 9, 6),
    ("single_chest_9x3_empty.png", 9, 3),
    ("single_chest_9x3_filled.png", 9, 3),
]


@pytest.mark.parametrize("fname,cols,rows", REAL_CASES)
def test_real_screenshots(fname, cols, rows):
    path = os.path.join(SCREENS_DIR, fname)
    if not os.path.exists(path):
        pytest.skip(f"Нет скриншота: {fname}")
    img = cv2.imread(path)
    assert img is not None
    rect = GridStub(9, rows).detect_grid_rect(img)
    assert rect is not None, f"Сетка не найдена на {fname}"
    assert rect["columns"] == cols, fname
    assert rect["rows"] == rows, fname
