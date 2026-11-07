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

from chest_scanner.scanner.detection import DetectionMixin
from chest_scanner.scanner.grid import GridMixin

SCREENS_DIR = os.path.join(os.path.dirname(__file__), "screenshots")


class GridStub(GridMixin):
    """Стенд без реального экрана: только пресет сетки."""

    def __init__(self, columns, rows):
        self.grid = {"columns": columns, "rows": rows}


class TitleStub(GridMixin, DetectionMixin):
    """Стенд с шаблоном заголовка сундука для проверки кандидатов."""

    def __init__(self, columns, rows):
        self.grid = {"columns": columns, "rows": rows}
        self.ref_w, self.ref_h = 1920, 1080
        self.config = {"chest_template_threshold": 0.88}
        self.chest_template = None


def _title_template(screen, rect):
    """Снимает полосу заголовка над сеткой (как auto_generate_templates)."""
    x, y, w, h = rect
    hh = max(30, int(h * 0.20))
    return screen[y - hh:y + 4, x - 4:x + w + 4].copy()


def _two_grids_with_titles():
    """Сундук 9×6 сверху и инвентарь 9×3 снизу, с разными заголовками."""
    top, top_rect = make_chest_screen(9, 6, offset=(700, 150))
    bottom, bottom_rect = make_chest_screen(9, 3, offset=(700, 750))
    screen = np.maximum(top, bottom)
    cv2.putText(screen, "CHEST", (top_rect[0] + 20, top_rect[1] - 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 20, 20), 2, cv2.LINE_AA)
    cv2.putText(screen, "INVENTORY", (bottom_rect[0] + 20, bottom_rect[1] - 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 20, 20), 2, cv2.LINE_AA)
    return screen, top_rect, bottom_rect


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


def test_candidate_title_validation_rejects_inventory():
    screen, top_rect, bottom_rect = _two_grids_with_titles()
    stub = TitleStub(9, 3)
    stub.chest_template = _title_template(screen, top_rect)
    top_info = {"x": top_rect[0], "y": top_rect[1],
                "width": top_rect[2], "height": top_rect[3]}
    bot_info = {"x": bottom_rect[0], "y": bottom_rect[1],
                "width": bottom_rect[2], "height": bottom_rect[3]}
    assert stub._candidate_has_chest_title(screen, top_info) is True
    assert stub._candidate_has_chest_title(screen, bot_info) is False


def test_detect_prefers_candidate_with_chest_title():
    # Пресет 9×3 совпадает с нижней сеткой (инвентарь), но заголовок
    # сундука есть только над верхней — выбираться должна она.
    screen, top_rect, _ = _two_grids_with_titles()
    stub = TitleStub(9, 3)
    stub.chest_template = _title_template(screen, top_rect)
    rect = stub.detect_grid_rect(screen)
    assert rect is not None
    assert rect["columns"] == 9 and rect["rows"] == 6


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


def test_regular_subset_drops_odd_peak():
    # Пропущенная линия сетки и лишний пик (край панели) не должны
    # ломать весь ряд: остаётся самый длинный равномерный участок.
    peaks = [19.0, 48.0, 76.5, 105.5, 134.5, 163.0, 212.5]
    assert GridStub._regular_subset(peaks) == [19.0, 48.0, 76.5, 105.5, 134.5, 163.0]
    # Ровный ряд не меняется.
    assert GridStub._regular_subset([0.0, 36.0, 72.0, 108.0]) == [0.0, 36.0, 72.0, 108.0]


def test_detect_grid_at_other_ui_scale():
    # Другой размер интерфейса (GUI Scale): панель сундука целиком
    # отмасштабирована, шаблон заголовка снят при старом размере.
    path = os.path.join(SCREENS_DIR, "double_chest_9x6_filled.png")
    if not os.path.exists(path):
        pytest.skip("Нет скриншота double_chest_9x6_filled.png")
    shot = cv2.imread(path)
    grid = {"x1": 798, "y1": 351, "x2": 1122, "y2": 567, "columns": 9, "rows": 6}
    template = shot[351 - 47:351 + 4, 794:1126].copy()
    panel = shot[290:600, 760:1160]
    for scale in (0.8, 1.25, 1.5):
        canvas = np.full_like(shot, 40)
        scaled = cv2.resize(panel, None, fx=scale, fy=scale,
                            interpolation=cv2.INTER_AREA)
        canvas[120:120 + scaled.shape[0], 700:700 + scaled.shape[1]] = scaled
        stub = TitleStub(9, 6)
        stub.grid = dict(grid)
        stub.chest_template = template.copy()
        rect = stub.detect_grid_rect(canvas)
        assert rect is not None, f"сетка не найдена при масштабе {scale}"
        assert (rect["columns"], rect["rows"]) == (9, 6)
        assert stub._chest_match_scale == pytest.approx(scale, abs=0.03)


def test_stale_template_does_not_block_calibration():
    """Устаревшая настроенная сетка не должна запрещать калибровку.

    Настроенная сетка хранится в reference-координатах и может быть
    снята при другом разрешении/GUI Scale; тогда её ширина не совпадает
    с реальной, шаблон заголовка тоже не совпадает — раньше такая пара
    блокировала автоопределение навсегда.
    """
    path = os.path.join(SCREENS_DIR, "double_chest_9x6_filled.png")
    if not os.path.exists(path):
        pytest.skip("Нет скриншота double_chest_9x6_filled.png")
    shot = cv2.imread(path)
    template = shot[351 - 47:351 + 4, 794:1126].copy()
    stub = TitleStub(9, 6)
    # Широкая сетка из прошлой калибровки (578 вместо реальных 324).
    stub.grid = {"x1": 673, "y1": 194, "x2": 1251, "y2": 594,
                 "columns": 9, "rows": 6}
    stub.chest_template = template.copy()
    rect = stub.detect_grid_rect(shot)
    assert rect is not None, "устаревший шаблон не должен блокировать калибровку"
    assert (rect["columns"], rect["rows"]) == (9, 6)
    assert abs(rect["x"] - 798) <= 2 and abs(rect["y"] - 351) <= 2
    # Масштаб запоминается, чтобы шаблон пересняли при новом размере.
    assert stub._chest_match_scale == pytest.approx(324 / 578, abs=0.03)

    # Та же ширина, но сохранённая сетка стоит не на месте.
    stub = TitleStub(9, 6)
    stub.grid = {"x1": 200, "y1": 194, "x2": 524, "y2": 410,
                 "columns": 9, "rows": 6}
    stub.chest_template = template.copy()
    rect = stub.detect_grid_rect(shot)
    assert rect is not None
    assert abs(rect["x"] - 798) <= 2 and abs(rect["y"] - 351) <= 2


def test_current_template_still_rejects_foreign_ui():
    """При актуальной сетке проверка заголовка продолжает отсекать чужой UI."""
    screen, top_rect, _ = _two_grids_with_titles()
    stub = TitleStub(9, 6)
    stub.grid = {"x1": top_rect[0], "y1": top_rect[1],
                 "x2": top_rect[0] + top_rect[2],
                 "y2": top_rect[1] + top_rect[3],
                 "columns": 9, "rows": 6}
    # Шаблон чужого интерфейса: над сеткой нет ничего похожего.
    stub.chest_template = np.full((47, 332, 3), 250, dtype=np.uint8)
    stub.chest_template[20:24, 30:300] = 10
    assert stub.detect_grid_rect(screen) is None
