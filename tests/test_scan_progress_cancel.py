"""Тесты прогресса и отмены сканирования (без реального экрана)."""
import threading

import numpy as np

from chest_scanner.scanner.base import ScannerBaseMixin
from chest_scanner.scanner.workflow import WorkflowMixin


class ScanStub(ScannerBaseMixin, WorkflowMixin):
    """Минимальный стенд: внешние эффекты скана замоканы."""

    def __init__(self):
        self._scan_lock = threading.Lock()
        self._scan_cancel_event = threading.Event()
        self._scan_cancelled = False
        self.scan_progress_cb = None
        self.chest_was_open = False
        self.last_chest_hash = None
        self._last_chest_pos = None
        self.prompted_unknowns = set()
        self.saved = None
        self.chest_template = np.zeros((10, 10, 3), dtype=np.uint8)
        self.last_scan_reason = None

    # Служебные звуки и внешние проверки — no-op
    def play_scan_start_sound(self): ...
    def play_success_sound(self): ...

    def chest_is_open(self, screen):
        return True

    def get_grid_rect(self, screen):
        return {"x": 0, "y": 0, "width": 40, "height": 40}

    def get_slots(self, screen):
        return [{"row": i // 3, "column": i % 3} for i in range(9)]

    def _process_slot(self, s):
        return {"row": s["row"], "column": s["column"], "item": "Алмаз",
                "quantity": 1, "unknown": False,
                "similarity_score": 0, "category": "uncategorized"}

    def save_chest_result(self, cd):
        self.saved = cd

    def add_unknown_item(self, icon):
        return "unknown_x"

    def read_quantity(self, img):
        return 1


def _mock_screen(monkeypatch):
    monkeypatch.setattr("chest_scanner.scanner.workflow.screenshot_bgr",
                        lambda: np.zeros((40, 40, 3), dtype=np.uint8))


def test_scan_progress_callback(monkeypatch):
    _mock_screen(monkeypatch)
    stub = ScanStub()
    calls = []
    stub.scan_progress_cb = lambda done, total: calls.append((done, total))

    cd = stub.scan_chest(force=True)

    assert cd is not None
    assert len(calls) == 9
    assert calls[-1] == (9, 9)  # последний слот — 9/9
    assert stub.saved is not None


def test_scan_cancel_stops_processing(monkeypatch):
    _mock_screen(monkeypatch)
    stub = ScanStub()

    def _process_slot_cancel(s):
        stub.request_scan_cancel()
        return {"row": s["row"], "column": s["column"], "item": "Алмаз",
                "quantity": 1, "unknown": False,
                "similarity_score": 0, "category": "uncategorized"}

    stub._process_slot = _process_slot_cancel

    result = stub.scan_chest(force=True)

    assert result is None
    assert stub.scan_cancelled is True
    assert stub.saved is None  # результат не сохранён
    assert stub.last_scan_reason == "cancelled"


def test_scan_chest_reports_closed_chest(monkeypatch):
    """При закрытом сундуке скан вернёт None и причину chest_closed."""
    _mock_screen(monkeypatch)
    stub = ScanStub()
    stub.chest_is_open = lambda screen: False

    result = stub.scan_chest(force=True)

    assert result is None
    assert stub.saved is None
    assert stub.last_scan_reason == "chest_closed"


def test_scan_chest_reports_missing_template(monkeypatch):
    """Без шаблона сундука скан вернёт None и причину no_template."""
    _mock_screen(monkeypatch)
    stub = ScanStub()
    stub.chest_template = None

    result = stub.scan_chest(force=True)

    assert result is None
    assert stub.saved is None
    assert stub.last_scan_reason == "no_template"


def test_scan_chest_success_resets_reason(monkeypatch):
    """Успешный скан сбрасывает причину в None."""
    _mock_screen(monkeypatch)
    stub = ScanStub()
    stub.last_scan_reason = "chest_closed"

    cd = stub.scan_chest(force=True)

    assert cd is not None
    assert stub.last_scan_reason is None
