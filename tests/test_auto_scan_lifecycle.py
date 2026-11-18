import threading
from types import SimpleNamespace

import pytest

from chest_scanner.scanner.workflow import WorkflowMixin


def _reader(interval):
    return SimpleNamespace(
        _auto_scan_active=True,
        _auto_scan_thread=threading.current_thread(),
        _auto_scan_state_lock=threading.Lock(),
        _scan_state_lock=threading.Lock(),
        _auto_scan_stop_event=threading.Event(),
        config={"scan_interval_seconds": interval},
        chest_was_open=False,
        last_chest_hash=None,
        _last_chest_pos=None,
        chest_is_open_fast=lambda: False,
    )


def test_auto_scan_uses_fallback_for_invalid_interval_and_cleans_up(monkeypatch):
    reader = _reader("not-a-number")
    sleeps = []

    def stop_after_first_wait(seconds):
        sleeps.append(seconds)
        reader._auto_scan_active = False

    monkeypatch.setattr("chest_scanner.scanner.workflow.time.sleep", stop_after_first_wait)

    WorkflowMixin._auto_scan_loop(reader)

    assert sleeps == [0.2]
    assert reader._auto_scan_active is False
    assert reader._auto_scan_thread is None


def test_auto_scan_cleans_up_after_scan_check_error(monkeypatch):
    reader = _reader(2)

    def fail_check():
        raise RuntimeError("capture unavailable")

    reader.chest_is_open_fast = fail_check

    def stop_after_first_wait(_seconds):
        reader._auto_scan_active = False

    monkeypatch.setattr("chest_scanner.scanner.workflow.time.sleep", stop_after_first_wait)

    WorkflowMixin._auto_scan_loop(reader)

    assert reader._auto_scan_active is False
    assert reader._auto_scan_thread is None


def test_auto_scan_cleans_up_if_waiting_raises(monkeypatch):
    reader = _reader(2)

    def fail_wait(_seconds):
        raise RuntimeError("wait failed")

    monkeypatch.setattr("chest_scanner.scanner.workflow.time.sleep", fail_wait)

    with pytest.raises(RuntimeError, match="wait failed"):
        WorkflowMixin._auto_scan_loop(reader)

    assert reader._auto_scan_active is False
    assert reader._auto_scan_thread is None


class _AliveThread:
    def __init__(self, alive):
        self.alive = alive
        self.joined = False

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.joined = True


def test_stop_auto_scan_reports_timeout_and_keeps_thread_reference():
    thread = _AliveThread(True)
    reader = SimpleNamespace(_auto_scan_active=True, _auto_scan_thread=thread,
                             _auto_scan_state_lock=threading.Lock(),
                             _scan_state_lock=threading.Lock(),
                             _auto_scan_stop_event=threading.Event())

    stopped = WorkflowMixin.stop_auto_scan(reader, timeout=0)

    assert stopped is False
    assert reader._auto_scan_active is False
    assert reader._auto_scan_thread is thread
    assert thread.joined is True


def test_stop_auto_scan_reports_success_when_thread_has_exited():
    thread = _AliveThread(False)
    reader = SimpleNamespace(_auto_scan_active=True, _auto_scan_thread=thread,
                             _auto_scan_state_lock=threading.Lock(),
                             _scan_state_lock=threading.Lock(),
                             _auto_scan_stop_event=threading.Event())

    stopped = WorkflowMixin.stop_auto_scan(reader, timeout=0)

    assert stopped is True
    assert reader._auto_scan_active is False
    assert reader._auto_scan_thread is None



def test_start_auto_scan_rolls_back_state_when_thread_start_fails(monkeypatch):
    class _FailedStartThread:
        def __init__(self, *args, **kwargs):
            self.started = False

        def start(self):
            self.started = True
            raise RuntimeError("thread creation failed")

    reader = SimpleNamespace(
        _auto_scan_active=False, _auto_scan_thread=None,
        _auto_scan_state_lock=threading.Lock(), _scan_state_lock=threading.Lock(),
        _auto_scan_stop_event=threading.Event(), _auto_scan_loop=lambda: None
    )
    monkeypatch.setattr(
        "chest_scanner.scanner.workflow.threading.Thread", _FailedStartThread)

    with pytest.raises(RuntimeError, match="thread creation failed"):
        WorkflowMixin.start_auto_scan(reader)

    assert reader._auto_scan_active is False
    assert reader._auto_scan_thread is None


def test_concurrent_auto_scan_starts_create_only_one_worker(monkeypatch):
    real_thread = threading.Thread
    created = []

    class _StartedThread:
        def __init__(self, *args, **kwargs):
            self.started = False
            created.append(self)

        def start(self):
            self.started = True

        def is_alive(self):
            return self.started

    reader = SimpleNamespace(
        _auto_scan_active=False, _auto_scan_thread=None,
        _auto_scan_state_lock=threading.Lock(), _scan_state_lock=threading.Lock(),
        _auto_scan_stop_event=threading.Event(), _auto_scan_loop=lambda: None,
    )
    monkeypatch.setattr(
        "chest_scanner.scanner.workflow.threading.Thread", _StartedThread)
    barrier = threading.Barrier(3)
    errors = []

    def start():
        try:
            barrier.wait()
            WorkflowMixin.start_auto_scan(reader)
        except Exception as exc:
            errors.append(exc)

    callers = [real_thread(target=start) for _ in range(2)]
    for caller in callers:
        caller.start()
    barrier.wait()
    for caller in callers:
        caller.join(timeout=2)

    assert not errors
    assert all(not caller.is_alive() for caller in callers)
    assert len(created) == 1
    assert reader._auto_scan_active is True
    assert reader._auto_scan_thread is created[0]


def test_auto_scan_stop_during_open_check_prevents_scan_start(monkeypatch):
    reader = _reader(2)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def blocked_open_check():
        entered.set()
        assert release.wait(3)
        return True

    reader.chest_is_open_fast = blocked_open_check
    reader.scan_chest = lambda **kwargs: calls.append(kwargs)

    def stop_after_first_wait(_seconds):
        reader._auto_scan_active = False

    monkeypatch.setattr("chest_scanner.scanner.workflow.time.sleep", stop_after_first_wait)
    # Run one loop iteration in a real worker so stop can race the capture check.
    def stop_from_controller():
        assert entered.wait(3)
        reader._auto_scan_active = False
        with reader._scan_state_lock:
            reader._auto_scan_stop_event.set()
        release.set()

    controller = threading.Thread(target=stop_from_controller)
    controller.start()
    WorkflowMixin._auto_scan_loop(reader)
    controller.join(timeout=3)

    assert not controller.is_alive()
    assert calls == []
    assert reader._auto_scan_stop_event.is_set()


def test_auto_scan_start_guard_does_not_clear_pending_stop():
    reader = SimpleNamespace(
        _scan_lock=threading.Lock(), _scan_state_lock=threading.Lock(),
        _scan_cancel_event=threading.Event(), _scan_cancelled=False,
        _scan_running=False, _auto_scan_stop_event=threading.Event(),
        reset_scan_cancel=lambda: reader._scan_cancel_event.clear(),
    )
    reader._auto_scan_stop_event.set()
    result = WorkflowMixin.scan_chest(reader, auto_scan=True)
    assert result is None
    assert reader._auto_scan_stop_event.is_set()
    assert reader._scan_running is False
    assert reader._scan_lock.acquire(blocking=False)
    reader._scan_lock.release()
