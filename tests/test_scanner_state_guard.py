"""Regression tests for GUI scanner-state reservations."""
import threading

import pytest

from chest_scanner.gui.main_window import _scanner_state_guard


class ReaderStub:
    def __init__(self, lock=None):
        self._scan_lock = lock


def test_guard_without_reader_lock_is_compatible():
    with _scanner_state_guard(object()) as acquired:
        assert acquired is True


def test_guard_acquires_and_releases_lock():
    lock = threading.Lock()
    reader = ReaderStub(lock)

    with _scanner_state_guard(reader) as acquired:
        assert acquired is True
        assert lock.locked()

    assert not lock.locked()


def test_guard_fails_fast_when_scan_owns_lock():
    lock = threading.Lock()
    reader = ReaderStub(lock)
    entered = threading.Event()
    release = threading.Event()

    def owner():
        with lock:
            entered.set()
            assert release.wait(5)

    worker = threading.Thread(target=owner)
    worker.start()
    assert entered.wait(5)
    try:
        with _scanner_state_guard(reader) as acquired:
            assert acquired is False
        assert lock.locked()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()


def test_guard_releases_lock_when_body_raises():
    lock = threading.Lock()
    reader = ReaderStub(lock)

    with pytest.raises(RuntimeError, match="expected"):
        with _scanner_state_guard(reader) as acquired:
            assert acquired is True
            raise RuntimeError("expected")

    assert not lock.locked()


def test_scanner_state_guard_is_reentrant_for_nested_operations():
    from chest_scanner.scanner.base import ScannerBaseMixin

    reader = object.__new__(ScannerBaseMixin)
    reader._scan_lock = threading.RLock()
    with reader._scanner_state_guard() as outer:
        assert outer is True
        with reader._scanner_state_guard(blocking=False) as inner:
            assert inner is True
    assert reader._scan_lock.acquire(blocking=False)
    reader._scan_lock.release()


def test_scanner_state_guard_nonblocking_rejects_other_thread():
    from chest_scanner.scanner.base import ScannerBaseMixin

    reader = object.__new__(ScannerBaseMixin)
    reader._scan_lock = threading.RLock()
    entered = threading.Event()
    release = threading.Event()

    def owner():
        with reader._scan_lock:
            entered.set()
            assert release.wait(5)

    worker = threading.Thread(target=owner)
    worker.start()
    assert entered.wait(5)
    try:
        with reader._scanner_state_guard(blocking=False) as acquired:
            assert acquired is False
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
