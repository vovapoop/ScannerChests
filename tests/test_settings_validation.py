import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QWidget

from chest_scanner import paths
from chest_scanner.commons import load_json
from chest_scanner.gui.settings import NAV_ITEMS, SettingsDialog

_APP = QApplication.instance() or QApplication([])


class _Reader:
    def __init__(self):
        self.config = load_json(paths.CONFIG_FILE, paths.DEFAULT_CONFIG)

    @staticmethod
    def get_discord_webhook_url():
        return ""


class _Main(QWidget):
    def __init__(self):
        super().__init__()
        self.reader = _Reader()

    @staticmethod
    def _apply_settings_runtime():
        pass


def _dialog():
    main = _Main()
    dialog = SettingsDialog(main, main)
    dialog._pending_apply = False
    dialog._save = lambda: False
    return dialog


def test_settings_build_all_pages_and_hotkey_help_is_current():
    dialog = _dialog()
    try:
        assert dialog.stack.count() == len(NAV_ITEMS) == 6
        labels = [label.text() for label in dialog.stack.widget(0).findChildren(QLabel)]
        help_text = next(text for text in labels if "F1" in text)
        assert "F24" in help_text
        assert "Ctrl" in help_text and "Alt" in help_text
    finally:
        dialog.reject()


def test_hotkey_fields_trigger_autosave(monkeypatch):
    dialog = _dialog()
    calls = []
    monkeypatch.setattr(dialog, "_save", lambda: calls.append(True) or True)
    try:
        dialog.show()
        dialog._vars["hk_scan"].setText("F9")
        QTest.qWait(700)
        assert calls
    finally:
        dialog.reject()


def test_integer_fields_reject_fractional_values():
    dialog = _dialog()
    try:
        with pytest.raises(ValueError):
            dialog._parse_int(QLineEdit("16.5"), "Quantity")
        assert dialog._parse_int(QLineEdit("16"), "Quantity") == 16
    finally:
        dialog.reject()



def _prepare_grid_preview(dialog):
    grid_row = next(i for i, item in enumerate(NAV_ITEMS) if item[0] == "grid")
    dialog.show()
    dialog.nav.setCurrentRow(grid_row)
    for name in ("_roi_timer", "_roi_debounce_timer"):
        timer = getattr(dialog, name, None)
        if timer is not None:
            timer.stop()
    dialog._read_roi_fields = lambda _prefix: [0.1, 0.1, 0.5, 0.5]
    dialog._grid_from_fields = lambda: {
        "x1": 0, "y1": 0, "x2": 100, "y2": 100,
        "columns": 9, "rows": 3,
    }


def _wait_for_roi_idle(dialog, timeout_ms=1500):
    """Wait for the queued worker result without assuming scheduler timing."""
    elapsed = 0
    while dialog._roi_busy and elapsed < timeout_ms:
        QTest.qWait(10)
        elapsed += 10
    assert not dialog._roi_busy


def test_roi_preview_runs_in_background_and_applies_result_on_gui_thread(monkeypatch):
    dialog = _dialog()
    completed = threading.Event()
    gui_thread = threading.current_thread()
    applied_threads = []

    def diagnose(**_kwargs):
        completed.set()
        return {"overlay": None, "slots": [], "issues": []}

    monkeypatch.setattr(dialog, "_capture_for_preview", lambda: object())
    dialog.reader.diagnose_roi_settings = diagnose
    original_apply = dialog._apply_roi_preview_result

    def record_apply(*args):
        applied_threads.append(threading.current_thread())
        return original_apply(*args)

    dialog._roi_signals.finished.disconnect(original_apply)
    dialog._roi_signals.finished.connect(record_apply)
    try:
        _prepare_grid_preview(dialog)
        dialog._refresh_roi_preview()
        assert dialog._roi_busy
        assert completed.wait(1.0)
        _wait_for_roi_idle(dialog)
        assert applied_threads == [gui_thread]
        assert dialog.roi_status.text() == "??????????? ?????? ?? ?????."
    finally:
        dialog.reject()


def test_roi_preview_result_is_ignored_after_leaving_grid(monkeypatch):
    dialog = _dialog()
    release_capture = threading.Event()
    completed = threading.Event()

    def capture():
        release_capture.wait(1.0)
        completed.set()
        return object()

    monkeypatch.setattr(dialog, "_capture_for_preview", capture)
    dialog.reader.diagnose_roi_settings = lambda **_kwargs: {
        "overlay": None, "slots": [], "issues": [],
    }
    try:
        _prepare_grid_preview(dialog)
        dialog.roi_status.setText("unchanged")
        dialog._refresh_roi_preview()
        assert dialog._roi_busy
        other_row = next(i for i, item in enumerate(NAV_ITEMS) if item[0] != "grid")
        dialog.nav.setCurrentRow(other_row)
        release_capture.set()
        assert completed.wait(1.0)
        _wait_for_roi_idle(dialog)
        assert dialog.roi_status.text() == "unchanged"
        for name in ("_roi_timer", "_roi_debounce_timer"):
            timer = getattr(dialog, name, None)
            assert timer is None or not timer.isActive()
    finally:
        release_capture.set()
        dialog.reject()


class _FailingThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        raise OSError("thread resources unavailable")


def test_roi_preview_recovers_when_worker_thread_cannot_start(monkeypatch):
    from chest_scanner.gui import settings

    dialog = _dialog()
    _prepare_grid_preview(dialog)
    monkeypatch.setattr(settings.threading, "Thread", _FailingThread)
    try:
        dialog._refresh_roi_preview()
        assert dialog._roi_busy is False
    finally:
        dialog.reject()


def test_webhook_status_recovers_when_worker_thread_cannot_start(monkeypatch):
    from chest_scanner.gui import settings

    dialog = _dialog()
    dialog._vars["webhook"].setText("https://discord.com/api/webhooks/123/token")
    monkeypatch.setattr(settings.threading, "Thread", _FailingThread)
    try:
        dialog._test_webhook()
        assert dialog.status_lbl.text().startswith(
            "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0437\u0430\u043f\u0443\u0441\u0442\u0438\u0442\u044c \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0443:")
    finally:
        dialog.reject()


def test_webhook_worker_reports_unexpected_exception(monkeypatch):
    from chest_scanner.gui import settings

    dialog = _dialog()
    dialog._vars["webhook"].setText("https://discord.com/api/webhooks/123/token")
    captured = {}

    class _InlineThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(settings.threading, "Thread", _InlineThread)
    monkeypatch.setattr(settings.requests, "get",
                        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("unexpected")))
    dialog._webhook_signals.finished.connect(
        lambda ok, message: captured.update(ok=ok, message=message))
    try:
        dialog._test_webhook()
        assert captured == {"ok": False, "message": "unexpected"}
    finally:
        dialog.reject()
