from types import SimpleNamespace

from chest_scanner.gui import main_window
from chest_scanner.gui.main_window import ScannerGUI


class _Timer:
    def __init__(self, _parent):
        self.callback = None
        self.started = False
        self.stopped = False

    def setInterval(self, _interval):
        pass

    class _Signal:
        def __init__(self, timer):
            self.timer = timer

        def connect(self, callback):
            self.timer.callback = callback

    @property
    def timeout(self):
        return self._Signal(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class _Worker:
    name = "slow-worker"

    def __init__(self):
        self.alive = True

    def is_alive(self):
        return self.alive


class _Lock:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _shutdown_window(worker, monkeypatch):
    app = SimpleNamespace(quit_calls=0, quit=lambda: setattr(app, "quit_calls", app.quit_calls + 1))
    monkeypatch.setattr(main_window, "QTimer", _Timer)
    monkeypatch.setattr(main_window.QApplication, "instance", lambda: app)
    calls = []
    status_texts = []
    reader = SimpleNamespace(
        config={}, _auto_scan_thread=None,
        request_scan_cancel=lambda: calls.append("cancel"),
        stop_auto_scan=lambda timeout=0: calls.append(("stop", timeout)) or True,
    )
    status_texts = []
    window = SimpleNamespace(
        _shutdown_complete=False, _shutdown_started=False,
        _shutdown_timer=None, _shutdown_logged_workers=False,
        _shutdown_started_at=0.0, _shutdown_stall_warned=False,
        _ui_settings=SimpleNamespace(setValue=lambda *_: None, sync=lambda: None),
        saveGeometry=lambda: b"geometry", running=True,
        _key_timer=SimpleNamespace(stop=lambda: None),
        _log_timer=SimpleNamespace(stop=lambda: None),
        _prompt_state=None, reader=reader, _hotkey_manager=None, _tray=None,
        status_dot=SimpleNamespace(texts=status_texts, setText=lambda value: status_texts.append(value),
                                   setStyleSheet=lambda *_: None),
        setEnabled=lambda *_: None,
        _workers={worker}, _workers_lock=_Lock(),
    )
    window._poll_shutdown = lambda: ScannerGUI._poll_shutdown(window)
    return window, app, calls


def test_close_waits_for_active_worker_before_quitting(monkeypatch):
    worker = _Worker()
    window, app, calls = _shutdown_window(worker, monkeypatch)

    ScannerGUI.close(window, force=True)

    assert window._shutdown_started is True
    assert window._shutdown_complete is False
    assert app.quit_calls == 0
    assert ("stop", 0) in calls
    assert window._shutdown_timer.started is True

    worker.alive = False
    ScannerGUI._poll_shutdown(window)

    assert window._shutdown_complete is True
    assert window._shutdown_timer.stopped is True
    assert app.quit_calls == 1


def test_shutdown_poll_keeps_waiting_for_autoscan_even_if_active_flag_is_false(monkeypatch):
    worker = _Worker()
    window, app, _calls = _shutdown_window(worker, monkeypatch)
    auto_worker = _Worker()
    window.reader._auto_scan_thread = auto_worker
    window._workers = set()

    ScannerGUI._poll_shutdown(window)
    assert app.quit_calls == 0

    auto_worker.alive = False
    ScannerGUI._poll_shutdown(window)
    assert app.quit_calls == 1



def test_shutdown_warns_once_when_worker_exceeds_stall_threshold(monkeypatch):
    worker = _Worker()
    window, app, _calls = _shutdown_window(worker, monkeypatch)
    warnings = []
    monkeypatch.setattr(main_window.time, "monotonic", lambda: 20.0)
    monkeypatch.setattr(main_window.logger, "warning",
                        lambda *args, **kwargs: warnings.append(args))

    ScannerGUI._poll_shutdown(window)
    ScannerGUI._poll_shutdown(window)

    assert app.quit_calls == 0
    assert window._shutdown_stall_warned is True
    assert window.status_dot.texts == ["●  ОЖИДАНИЕ ОПЕРАЦИИ"]
    assert len(warnings) == 1

    worker.alive = False
    ScannerGUI._poll_shutdown(window)
    assert app.quit_calls == 1
