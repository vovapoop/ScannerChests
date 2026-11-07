"""Тесты проверки настроек областей иконки и цифр (diagnose_roi_settings)."""
import numpy as np

from chest_scanner.scanner.grid import GridMixin
from chest_scanner.scanner.ocr import OcrMixin

ICON_ROI = [0.0, 0.0, 0.5, 0.5]
COUNT_ROI = [0.55, 0.55, 0.4, 0.4]


class RoiStub(GridMixin, OcrMixin):
    """Минимальный ридер: сетка 2×1 на экране 1920×1080."""

    def __init__(self):
        self.ref_w, self.ref_h = 1920, 1080
        self.grid = {"x1": 0, "y1": 0, "x2": 100, "y2": 50,
                     "columns": 2, "rows": 1}
        self.config = {"icon_roi_in_slot": ICON_ROI,
                       "count_roi_in_slot": COUNT_ROI}
        self.digit_templates = {}

    def read_quantity(self, slot_image):
        return 64

    def is_empty_slot(self, slot_image):
        return float(slot_image.std()) < 2.0


def _screen_with_items():
    """Слот 0 заполнен: иконка слева сверху, цифры — в области количества."""
    screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
    # Слот r0 c0: x 1..49, y 1..49 (рамки сетки отрезают по 1 px).
    screen[5:25, 5:25] = 200    # «иконка»
    screen[32:44, 32:44] = 255  # «цифры» внутри count ROI
    return screen


def test_diagnose_ok_reports_filled_slot():
    stub = RoiStub()
    stub.digit_templates = {"1": np.zeros((32, 24), dtype=np.uint8)}
    report = stub.diagnose_roi_settings(screen=_screen_with_items())
    assert len(report["slots"]) == 1
    slot = report["slots"][0]
    assert (slot["row"], slot["column"]) == (1, 1)
    assert slot["icon"] is not None and slot["icon"].size > 0
    assert slot["binary"] is not None and int(np.count_nonzero(slot["binary"])) > 0
    assert slot["quantity"] == 64
    assert report["overlay"] is not None
    assert any("Проблем не обнаружено" in s for s in report["issues"])


def test_diagnose_ok_with_digit_templates_missing_is_warning_only():
    stub = RoiStub()
    report = stub.diagnose_roi_settings(screen=_screen_with_items())
    assert any("Шаблоны цифр не загружены" in s for s in report["issues"])
    assert any("Области заданы корректно" in s for s in report["issues"])


def test_diagnose_rejects_roi_out_of_slot():
    stub = RoiStub()
    report = stub.diagnose_roi_settings(
        screen=_screen_with_items(),
        icon_roi=[0.5, 0.5, 0.8, 0.8], count_roi=COUNT_ROI)
    assert not report["slots"]
    assert report["overlay"] is None
    assert any("выходят за пределы слота" in s for s in report["issues"])


def test_diagnose_flags_missing_digits_when_count_roi_wrong():
    stub = RoiStub()
    stub.digit_templates = {"1": np.zeros((32, 24), dtype=np.uint8)}
    # Область цифр сдвинута в пустой нижний угол — светлых пикселей там нет.
    report = stub.diagnose_roi_settings(
        screen=_screen_with_items(),
        icon_roi=ICON_ROI, count_roi=[0.0, 0.5, 0.2, 0.2])
    assert len(report["slots"]) == 1
    assert any("в области цифр нет светлых пикселей" in s
               for s in report["issues"])


def test_diagnose_empty_chest_reports_no_filled_slots():
    stub = RoiStub()
    report = stub.diagnose_roi_settings(screen=np.zeros((1080, 1920, 3), dtype=np.uint8))
    assert not report["slots"]
    assert any("Заполненных слотов не найдено" in s for s in report["issues"])


def test_diagnose_warns_on_missing_digit_templates():
    stub = RoiStub()
    report = stub.diagnose_roi_settings(screen=np.zeros((1080, 1920, 3), dtype=np.uint8))
    assert any("Шаблоны цифр не загружены" in s for s in report["issues"])
    assert any("Заполненных слотов не найдено" in s for s in report["issues"])


def test_diagnose_honors_grid_override():
    """Предпросмотр может подменить сетку без мутации reader.grid."""
    stub = RoiStub()
    report = stub.diagnose_roi_settings(
        screen=_screen_with_items(),
        grid={"x1": 500, "y1": 0, "x2": 600, "y2": 50, "columns": 2, "rows": 1})
    # В переопределённой сетке слоты пустые.
    assert not report["slots"]
    assert any("Заполненных слотов не найдено" in s for s in report["issues"])
    # self.grid не изменился.
    assert stub.grid["x1"] == 0
