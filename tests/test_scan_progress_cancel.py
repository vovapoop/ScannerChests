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
        self._scan_state_lock = threading.Lock()
        self._scan_running = False
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
        return True

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
    assert stub.last_chest_hash is not None


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
    assert stub.last_chest_hash is None


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


def test_scan_does_not_save_partial_result_when_slot_processing_fails(monkeypatch):
    _mock_screen(monkeypatch)
    stub = ScanStub()

    def _process_slot_with_error(slot):
        if slot["row"] == 0 and slot["column"] == 0:
            raise RuntimeError("simulated slot failure")
        return {"row": slot["row"], "column": slot["column"], "item": "test_item",
                "quantity": 1, "unknown": False,
                "similarity_score": 0, "category": "uncategorized"}

    stub._process_slot = _process_slot_with_error

    result = stub.scan_chest(force=True)

    assert result is None
    assert stub.saved is None
    assert stub.last_scan_reason == "processing_error"
    assert stub.last_chest_hash is None


def test_failed_scan_can_retry_same_unchanged_chest(monkeypatch):
    _mock_screen(monkeypatch)
    stub = ScanStub()
    original = stub._process_slot
    failed = {"once": False}

    def fail_once(slot):
        if slot["row"] == 0 and slot["column"] == 0 and not failed["once"]:
            failed["once"] = True
            raise RuntimeError("temporary OCR failure")
        return original(slot)

    stub._process_slot = fail_once
    first = stub.scan_chest(force=False)

    assert first is None
    assert stub.saved is None
    assert stub.last_scan_reason == "processing_error"
    assert stub.last_chest_hash is None

    stub._process_slot = original
    second = stub.scan_chest(force=False)

    assert second is not None
    assert stub.saved is second
    assert stub.last_chest_hash is not None


def test_scan_aggregates_duplicate_items_and_keeps_unknown_quantities_out(monkeypatch):
    _mock_screen(monkeypatch)
    stub = ScanStub()
    slots = [
        {"row": 0, "column": 0},
        {"row": 0, "column": 1},
        {"row": 0, "column": 2},
        {"row": 1, "column": 0},
    ]
    stub.get_slots = lambda screen: slots
    values = {
        (0, 0): ("?????", 16, False, "gems"),
        (0, 1): ("?????", 32, False, "gems"),
        (0, 2): ("?????", 7, False, "other"),
        (1, 0): ("unknown_abc", 5, True, "uncategorized"),
    }

    def process(slot):
        name, quantity, unknown, category = values[(slot["row"], slot["column"])]
        return {"row": slot["row"] + 1, "column": slot["column"] + 1,
                "item": name, "quantity": quantity, "unknown": unknown,
                "similarity_score": 0, "category": category}

    stub._process_slot = process
    result = stub.scan_chest(force=True)

    assert result["occupied_slots"] == 4
    assert result["unique_items"] == 2
    assert result["totals"] == {"?????": 55, "unknown_abc": 5}
    assert result["categories"] == {
        "gems": {"?????": 48}, "other": {"?????": 7},
        "uncategorized": {"unknown_abc": 5}}
    assert result["unknown_items"] == ["unknown_abc"]


def test_scan_preserves_unknown_quantity_without_guessing(monkeypatch):
    _mock_screen(monkeypatch)
    stub = ScanStub()
    slots = [{"row": 0, "column": 0}, {"row": 0, "column": 1}]
    stub.get_slots = lambda screen: slots

    def process(slot):
        quantity = None if slot["column"] == 0 else 4
        return {"row": 1, "column": slot["column"] + 1,
                "item": "?????", "quantity": quantity, "unknown": False,
                "similarity_score": 0, "category": "gems"}

    stub._process_slot = process
    result = stub.scan_chest(force=True)

    assert result["slots"][0]["quantity"] is None
    assert result["slots"][1]["quantity"] == 4
    assert result["quantity_unknown_slots"] == 1
    assert result["totals"] == {"?????": 4}
    assert result["categories"] == {"gems": {"?????": 4}}
    assert result["unique_items"] == 1


def test_scan_rejects_invalid_quantities_from_slot_results(monkeypatch):
    _mock_screen(monkeypatch)
    stub = ScanStub()
    slots = [{"row": 0, "column": 0}, {"row": 0, "column": 1},
             {"row": 0, "column": 2}]
    stub.get_slots = lambda screen: slots
    quantities = [0, True, 3.5]

    def process(slot):
        return {"row": 1, "column": slot["column"] + 1,
                "item": "?????", "quantity": quantities[slot["column"]],
                "unknown": False, "similarity_score": 0, "category": "gems"}

    stub._process_slot = process
    result = stub.scan_chest(force=True)

    assert [slot["quantity"] for slot in result["slots"]] == [None, None, None]
    assert result["quantity_unknown_slots"] == 3
    assert result["totals"] == {}
    assert result["categories"] == {}


def test_scan_can_retry_after_cancellation_and_releases_lock(monkeypatch):
    _mock_screen(monkeypatch)
    stub = ScanStub()
    original = stub._process_slot
    cancelled = {"once": False}

    def cancel_once(slot):
        if not cancelled["once"]:
            cancelled["once"] = True
            stub.request_scan_cancel()
        return original(slot)

    stub._process_slot = cancel_once
    first = stub.scan_chest(force=False)

    assert first is None
    assert stub.scan_cancelled is True
    assert stub.last_scan_reason == "cancelled"
    assert stub.saved is None
    assert stub._scan_lock.acquire(blocking=False) is True
    stub._scan_lock.release()

    stub._process_slot = original
    second = stub.scan_chest(force=False)

    assert second is not None
    assert stub.scan_cancelled is False
    assert stub.last_scan_reason is None
    assert stub.saved is second
    assert stub.last_chest_hash is not None


def test_cancel_request_during_scan_initialization_is_not_lost(monkeypatch):
    _mock_screen(monkeypatch)
    stub = ScanStub()
    entered = threading.Event()
    release = threading.Event()
    cancel_started = threading.Event()
    cancel_finished = threading.Event()
    original_reset = stub.reset_scan_cancel

    def pause_after_reset():
        original_reset()
        assert stub._scan_running is True
        entered.set()
        assert release.wait(5)

    def request_cancel():
        cancel_started.set()
        stub.request_scan_cancel()
        cancel_finished.set()

    stub.reset_scan_cancel = pause_after_reset
    results = []
    worker = threading.Thread(target=lambda: results.append(stub.scan_chest(force=True)))
    worker.start()
    assert entered.wait(5)
    cancel_worker = threading.Thread(target=request_cancel)
    cancel_worker.start()
    assert cancel_started.wait(5)
    assert not cancel_finished.wait(0.1)
    release.set()
    cancel_worker.join(5)
    worker.join(5)

    assert not cancel_worker.is_alive()
    assert not worker.is_alive()
    assert cancel_finished.is_set()
    assert results == [None]
    assert stub.scan_cancelled is True
    assert stub.last_scan_reason == "cancelled"
    assert stub._scan_running is False
