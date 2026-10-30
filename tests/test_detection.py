"""Тесты детекции открытого сундука: порог корреляции + геометрическая зона.

Детекция считает сундук открытым только если шаблон заголовка найден с
достаточной корреляцией И в узкой полосе над настроенной сеткой. Это
отсекает ложные срабатывания на других интерфейсах (инвентарь игрока,
другие контейнеры) и тёмных участках мира.
"""
import numpy as np

from chest_scanner import paths
from chest_scanner.scanner.detection import DetectionMixin
from chest_scanner.scanner.grid import GridMixin


class DetStub(GridMixin, DetectionMixin):
    """Стенд для DetectionMixin без тяжёлой инициализации."""

    def __init__(self):
        self.ref_w, self.ref_h = 1920, 1080
        self.grid = dict(paths.DEFAULT_CONFIG["chest_grid"])
        self.config = dict(paths.DEFAULT_CONFIG)
        self.chest_template = None


GRID = {"x": 798, "y": 351, "width": 324, "height": 216}


def _noise_screen(seed=42):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, size=(1080, 1920, 3), dtype=np.uint8)


def capture_template(screen, grid=GRID):
    """Снимает шаблон заголовка так же, как auto_generate_templates."""
    x, y, w, h = grid["x"], grid["y"], grid["width"], grid["height"]
    hh = max(30, int(h * 0.20))
    return screen[y - hh:y + 4, x - 4:x + w + 4].copy()


def _expected_template_pos(grid=GRID):
    """Ожидаемая позиция левого верхнего угла шаблона на экране."""
    x, y, _w, h = grid["x"], grid["y"], grid["width"], grid["height"]
    hh = max(30, int(h * 0.20))
    return x - 4, y - hh


def test_default_threshold_raised():
    assert paths.DEFAULT_CONFIG["chest_template_threshold"] == 0.88


def test_chest_open_at_expected_position():
    """Шаблон на ожидаемой позиции над сеткой — сундук открыт."""
    stub = DetStub()
    screen = _noise_screen()
    tx, ty = _expected_template_pos()
    tpl = capture_template(screen)
    screen[ty:ty + tpl.shape[0], tx:tx + tpl.shape[1]] = tpl
    stub.chest_template = tpl
    assert stub.chest_is_open(screen) is True


def test_chest_closed_when_template_elsewhere():
    """Высокая корреляция вне зоны сетки — ложное срабатывание, отклоняем."""
    stub = DetStub()
    screen = _noise_screen()
    tpl = capture_template(screen)
    # Имитируем «другой интерфейс» в дальнем углу экрана.
    screen[40:40 + tpl.shape[0], 60:60 + tpl.shape[1]] = tpl
    stub.chest_template = tpl
    assert stub.chest_is_open(screen) is False


def test_chest_closed_when_no_match():
    stub = DetStub()
    stub.chest_template = capture_template(_noise_screen(seed=1))
    assert stub.chest_is_open(_noise_screen(seed=2)) is False


def test_chest_is_open_fast_uses_expected_position(monkeypatch):
    stub = DetStub()
    screen = _noise_screen()
    tx, ty = _expected_template_pos()
    tpl = capture_template(screen)
    screen[ty:ty + tpl.shape[0], tx:tx + tpl.shape[1]] = tpl
    stub.chest_template = tpl
    stub._last_chest_pos = dict(GRID)

    def fake_region(x, y, w, h):
        return screen[y:y + h, x:x + w].copy()

    monkeypatch.setattr(
        "chest_scanner.scanner.detection.screenshot_region_bgr", fake_region)
    assert stub.chest_is_open_fast() is True


def test_chest_is_open_fast_rejects_match_outside_band(monkeypatch):
    """Матч рядом с сеткой, но правее полосы допуска — отклоняем."""
    stub = DetStub()
    screen = _noise_screen()
    tpl = capture_template(screen)
    # Та же строка экрана, что у ожидаемой позиции, но x=850 вне полосы
    # (band по x: 778..818) и при этом целиком внутри области захвата.
    tx, ty = 850, _expected_template_pos()[1]
    screen[ty:ty + tpl.shape[0], tx:tx + tpl.shape[1]] = tpl
    stub.chest_template = tpl
    stub._last_chest_pos = dict(GRID)

    def fake_region(x, y, w, h):
        return screen[y:y + h, x:x + w].copy()

    monkeypatch.setattr(
        "chest_scanner.scanner.detection.screenshot_region_bgr", fake_region)
    assert stub.chest_is_open_fast() is False


def test_chest_is_open_fast_falls_back_to_full_screen(monkeypatch):
    stub = DetStub()
    screen = _noise_screen()
    tx, ty = _expected_template_pos()
    tpl = capture_template(screen)
    screen[ty:ty + tpl.shape[0], tx:tx + tpl.shape[1]] = tpl
    stub.chest_template = tpl
    stub._last_chest_pos = None
    monkeypatch.setattr(
        "chest_scanner.scanner.detection.screenshot_bgr", lambda: screen)
    assert stub.chest_is_open_fast() is True


def test_band_scales_with_resolution():
    """Полоса допуска масштабируется вместе с сеткой (экран 4K)."""
    stub = DetStub()
    screen = np.zeros((2160, 3840, 3), dtype=np.uint8)
    grid = stub.get_grid_rect(screen)
    band = stub._title_band(grid["x"], grid["y"],
                            grid["width"], grid["height"])
    # Сетка 2x: (1596, 702, 648, 432); шаблон ожидается в ~(1588, 616).
    tx, ty = 2 * 798 - 8, 2 * 351 - 86
    assert band[0] <= tx <= band[2]
    assert band[1] <= ty <= band[3]
