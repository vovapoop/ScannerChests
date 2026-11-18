import threading
from types import SimpleNamespace

from chest_scanner.scanner.items import ItemsMixin


def test_process_slot_skips_work_when_cancelled_before_start():
    reader = SimpleNamespace(_scan_cancel_event=threading.Event())
    reader._scan_cancel_event.set()
    result = ItemsMixin._process_slot(reader, {"image": object()})
    assert result is None


def test_process_slot_stops_between_expensive_steps_after_cancel():
    image = SimpleNamespace(size=1)
    reader = SimpleNamespace(_scan_cancel_event=threading.Event())
    calls = []

    def check_empty(_image):
        calls.append("empty-check")
        reader._scan_cancel_event.set()
        return False

    reader.is_empty_slot = check_empty
    reader.extract_icon = lambda _image: calls.append("extract")
    reader.detect_item = lambda _icon: calls.append("detect")

    result = ItemsMixin._process_slot(reader, {"image": image})

    assert result is None
    assert calls == ["empty-check"]
