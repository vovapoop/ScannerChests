"""Cross-process application instance lock tests."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from chest_scanner.instance_lock import acquire_instance_lock

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _wait_for(path, timeout=10):
    deadline = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    return path.exists()


def test_second_instance_is_rejected_and_lock_releases_after_crash(tmp_path):
    ready = tmp_path / "owner-ready"
    script = "\n".join([
        "import pathlib, sys, time",
        "from chest_scanner.instance_lock import acquire_instance_lock",
        "lock = acquire_instance_lock(sys.argv[1])",
        "assert lock is not None",
        "pathlib.Path(sys.argv[2]).write_text('ready')",
        "while True: time.sleep(0.05)",
    ])
    owner = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path), str(ready)],
        cwd=str(PROJECT_ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert _wait_for(ready), "first process did not acquire instance lock"
        assert acquire_instance_lock(str(tmp_path)) is None
        owner.kill()
        owner.wait(timeout=5)
        replacement = None
        deadline = time.monotonic() + 5
        while replacement is None and time.monotonic() < deadline:
            replacement = acquire_instance_lock(str(tmp_path))
            if replacement is None:
                time.sleep(0.05)
        assert replacement is not None, "OS lock remained held after owner process exited"
        replacement.close()
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)



def test_instance_lock_rejects_linked_lock_path(tmp_path, monkeypatch):
    import stat
    from types import SimpleNamespace

    from chest_scanner import instance_lock

    lock_path = tmp_path / ".chestscanner-instance.lock"
    linked = SimpleNamespace(st_mode=stat.S_IFLNK, st_dev=1, st_ino=2)
    real_lstat = instance_lock.os.lstat

    def fake_lstat(path):
        if os.fspath(path) == os.fspath(lock_path):
            return linked
        return real_lstat(path)

    monkeypatch.setattr(instance_lock.os, "lstat", fake_lstat)
    try:
        acquire_instance_lock(str(tmp_path))
    except OSError as exc:
        assert "regular file" in str(exc)
    else:
        raise AssertionError("linked instance lock path was accepted")


def test_instance_lock_rejects_file_swap_during_open(tmp_path, monkeypatch):
    from chest_scanner import instance_lock

    lock_path = tmp_path / ".chestscanner-instance.lock"
    other_path = tmp_path / "other-file"
    lock_path.write_bytes(b"lock")
    other_path.write_bytes(b"external")
    real_open = instance_lock.os.open

    def swapped_open(path, flags, mode=0o777):
        if os.fspath(path) == os.fspath(lock_path):
            return real_open(other_path, flags, mode)
        return real_open(path, flags, mode)

    monkeypatch.setattr(instance_lock.os, "open", swapped_open)
    try:
        acquire_instance_lock(str(tmp_path))
    except OSError as exc:
        assert "changed while opening" in str(exc)
    else:
        raise AssertionError("replaced instance lock path was accepted")
    assert other_path.read_bytes() == b"external"


def test_instance_lock_open_errors_are_not_misreported_as_existing_instance(
        tmp_path, monkeypatch):
    from chest_scanner import instance_lock

    def fail_open(*args, **kwargs):
        raise PermissionError("access denied")

    monkeypatch.setattr(instance_lock.os, "open", fail_open)
    try:
        acquire_instance_lock(str(tmp_path))
    except PermissionError:
        pass
    else:
        raise AssertionError("lock-file access error was hidden as lock contention")


def test_simultaneous_first_instance_lock_acquisition_has_one_owner(tmp_path):
    """Two processes released together must never both acquire the lock."""
    rounds = 5
    for round_index in range(rounds):
        case_dir = tmp_path / f"race-{round_index}"
        case_dir.mkdir()
        start = case_dir / "start"
        release = case_dir / "release"
        children = []
        script = chr(10).join([
            "import pathlib, sys, time",
            "from chest_scanner.instance_lock import acquire_instance_lock",
            "base, ready, start, result, release = map(pathlib.Path, sys.argv[1:])",
            "ready.write_text('ready')",
            "deadline = time.monotonic() + 30",
            "while not start.exists() and time.monotonic() < deadline: time.sleep(0.002)",
            "if not start.exists(): raise SystemExit(3)",
            "lock = acquire_instance_lock(str(base))",
            "result.write_text('acquired' if lock is not None else 'contended')",
            "if lock is not None:",
            "    deadline = time.monotonic() + 30",
            "    while not release.exists() and time.monotonic() < deadline: time.sleep(0.002)",
            "    lock.close()",
        ])
        try:
            for child_index in range(2):
                ready = case_dir / f"ready-{child_index}"
                result = case_dir / f"result-{child_index}"
                child = subprocess.Popen(
                    [sys.executable, "-c", script, str(case_dir), str(ready),
                     str(start), str(result), str(release)],
                    cwd=str(PROJECT_ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True)
                children.append((child, ready, result))
            assert all(_wait_for(ready, timeout=30) for _, ready, _ in children), (
                "child processes did not reach the start barrier")
            start.write_text("go")
            results_ready = all(_wait_for(result, timeout=30) for _, _, result in children)
            if not results_ready:
                diagnostics = []
                for child, _, result in children:
                    if child.poll() is None:
                        diagnostics.append(("still-running", result.exists()))
                    else:
                        stdout, stderr = child.communicate()
                        diagnostics.append((child.returncode, stdout, stderr))
                raise AssertionError(
                    f"child processes did not report lock acquisition results: {diagnostics!r}")
            outcomes = [result.read_text() for _, _, result in children]
            assert outcomes.count("acquired") == 1, (
                f"expected exactly one lock owner, got {outcomes!r}")
            assert outcomes.count("contended") == 1, (
                f"expected exactly one contending process, got {outcomes!r}")
        finally:
            release.write_text("release")
            for child, _, _ in children:
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)

def test_lock_releases_if_process_crashes_before_startup_ready_marker(tmp_path):
    """A crash after OS lock acquisition but before readiness must release it."""
    script = chr(10).join([
        "import sys, time",
        "from chest_scanner.instance_lock import acquire_instance_lock",
        "lock = acquire_instance_lock(sys.argv[1])",
        "assert lock is not None",
        "while True: time.sleep(0.02)",
    ])
    owner = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=str(PROJECT_ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            contender = acquire_instance_lock(str(tmp_path))
            if contender is None:
                break
            contender.close()
            time.sleep(0.02)
        else:
            raise AssertionError("child never acquired the instance lock")
        owner.kill()
        owner.wait(timeout=5)
        replacement = None
        deadline = time.monotonic() + 5
        while replacement is None and time.monotonic() < deadline:
            replacement = acquire_instance_lock(str(tmp_path))
            if replacement is None:
                time.sleep(0.02)
        assert replacement is not None, "lock remained held after pre-ready crash"
        replacement.close()
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)


def test_initialized_lock_file_can_be_acquired_after_crash_before_lock(tmp_path):
    """A byte left by a crash before locking is safe to reuse on next launch."""
    lock_path = tmp_path / ".chestscanner-instance.lock"
    lock_path.write_bytes(b"0")
    lock = acquire_instance_lock(str(tmp_path))
    assert lock is not None
    lock.close()

def test_named_setup_lock_is_independent_and_rejects_path_components(tmp_path):
    instance = acquire_instance_lock(str(tmp_path))
    setup = acquire_instance_lock(
        str(tmp_path), lock_name=".chestscanner-venv-setup.lock")
    assert instance is not None
    assert setup is not None
    with pytest.raises(ValueError, match="plain filename"):
        acquire_instance_lock(str(tmp_path), lock_name="..\\outside.lock")
    setup.close()
    instance.close()


def test_blocking_named_lock_waits_until_previous_setup_finishes(tmp_path):
    lock_name = ".chestscanner-venv-setup.lock"
    owner = acquire_instance_lock(str(tmp_path), lock_name=lock_name)
    assert owner is not None
    ready = tmp_path / "waiter-ready"
    acquired = tmp_path / "waiter-acquired"
    script = chr(10).join([
        "import pathlib, sys, time",
        "from chest_scanner.instance_lock import acquire_instance_lock",
        "base, ready, acquired = map(pathlib.Path, sys.argv[1:])",
        "ready.write_text('ready')",
        f"lock = acquire_instance_lock(str(base), lock_name={lock_name!r}, blocking=True)",
        "acquired.write_text('acquired')",
        "lock.close()",
    ])
    waiter = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path), str(ready), str(acquired)],
        cwd=str(PROJECT_ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        assert _wait_for(ready)
        time.sleep(0.15)
        assert not acquired.exists(), "blocking waiter bypassed the existing owner"
        owner.close()
        assert _wait_for(acquired), "waiter did not acquire after setup lock release"
        assert waiter.wait(timeout=5) == 0, waiter.stderr.read()
    finally:
        owner.close()
        if waiter.poll() is None:
            waiter.kill()
            waiter.wait(timeout=5)

def _start_bootstrap_handshake_child(base_dir):
    entry = base_dir / "dummy_main.py"
    marker = base_dir / "application-started"
    entry.write_text(chr(10).join([
        "import sys",
        "from pathlib import Path",
        "from chest_scanner.app_bootstrap import _ensure_venv, take_bootstrap_instance_lock",
        "from chest_scanner.instance_lock import acquire_instance_lock",
        "_ensure_venv()",
        "lock = take_bootstrap_instance_lock()",
        "assert lock is not None",
        "assert acquire_instance_lock(str(Path(__file__).parent)) is None",
        "Path(sys.argv[1]).write_text('started')",
    ]), encoding="utf-8")
    env = os.environ.copy()
    env["CHESTSCANNER_BOOTSTRAP_HANDSHAKE"] = "1"
    child = subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "bootstrap_runner.py"), str(entry), str(marker)],
        cwd=str(PROJECT_ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env)
    return child, marker


def test_child_handshake_claims_instance_lock_before_commit(tmp_path):
    child, marker = _start_bootstrap_handshake_child(tmp_path)
    try:
        assert child.stdout.readline().strip() == "CLAIMED"
        assert acquire_instance_lock(str(tmp_path)) is None
        child.stdin.write("COMMIT\n")
        child.stdin.flush()
        child.stdin.close()
        assert child.wait(timeout=15) == 0, child.stderr.read()
        assert marker.read_text(encoding="utf-8") == "started"
        replacement = acquire_instance_lock(str(tmp_path))
        assert replacement is not None
        replacement.close()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_child_handshake_releases_claim_when_parent_channel_closes(tmp_path):
    child, marker = _start_bootstrap_handshake_child(tmp_path)
    try:
        assert child.stdout.readline().strip() == "CLAIMED"
        assert acquire_instance_lock(str(tmp_path)) is None
        child.stdin.close()
        assert child.wait(timeout=10) == 0
        assert not marker.exists()
        replacement = acquire_instance_lock(str(tmp_path))
        assert replacement is not None
        replacement.close()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_child_handshake_reports_busy_when_another_instance_owns_lock(tmp_path):
    owner = acquire_instance_lock(str(tmp_path))
    assert owner is not None
    child, marker = _start_bootstrap_handshake_child(tmp_path)
    try:
        assert child.stdout.readline().strip() == "BUSY"
        assert child.wait(timeout=10) == 0
        assert not marker.exists()
        assert acquire_instance_lock(str(tmp_path)) is None
    finally:
        owner.close()
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_bootstrap_runner_logs_package_import_failure_and_releases_lock(tmp_path):
    import shutil

    package = tmp_path / "chest_scanner"
    package.mkdir()
    shutil.copy2(PROJECT_ROOT / "chest_scanner" / "instance_lock.py",
                 package / "instance_lock.py")
    (package / "__init__.py").write_text(
        "raise RuntimeError('simulated package import failure')\n", encoding="utf-8")
    runner = tmp_path / "bootstrap_runner.py"
    shutil.copy2(PROJECT_ROOT / "bootstrap_runner.py", runner)
    entry = tmp_path / "main.py"
    entry.write_text("raise AssertionError('application must not start')\n",
                     encoding="utf-8")
    env = os.environ.copy()
    env["CHESTSCANNER_BOOTSTRAP_HANDSHAKE"] = "1"
    child = subprocess.Popen(
        [sys.executable, str(runner), str(entry)], cwd=str(tmp_path),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env=env)
    try:
        assert child.stdout.readline().strip() == "CLAIMED"
        assert acquire_instance_lock(str(tmp_path)) is None
        child.stdin.write("COMMIT\n")
        child.stdin.flush()
        child.stdin.close()
        assert child.wait(timeout=10) != 0
        log = (tmp_path / "startup_error.log").read_text(encoding="utf-8")
        assert "simulated package import failure" in log
        replacement = acquire_instance_lock(str(tmp_path))
        assert replacement is not None
        replacement.close()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_bootstrap_runner_releases_transferred_lock_after_entry_failure(tmp_path):
    import shutil

    package = tmp_path / "chest_scanner"
    package.mkdir()
    shutil.copy2(PROJECT_ROOT / "chest_scanner" / "instance_lock.py",
                 package / "instance_lock.py")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "app_bootstrap.py").write_text(
        "_BOOTSTRAP_INSTANCE_LOCK = None\n"
        "def take_bootstrap_instance_lock():\n"
        "    global _BOOTSTRAP_INSTANCE_LOCK\n"
        "    lock = _BOOTSTRAP_INSTANCE_LOCK\n"
        "    _BOOTSTRAP_INSTANCE_LOCK = None\n"
        "    return lock\n", encoding="utf-8")
    runner = tmp_path / "bootstrap_runner.py"
    shutil.copy2(PROJECT_ROOT / "bootstrap_runner.py", runner)
    entry = tmp_path / "main.py"
    entry.write_text(
        "from chest_scanner.app_bootstrap import take_bootstrap_instance_lock\n"
        "_instance_lock = take_bootstrap_instance_lock()\n"
        "assert _instance_lock is not None\n"
        "raise RuntimeError('simulated entry failure')\n", encoding="utf-8")
    env = os.environ.copy()
    env["CHESTSCANNER_BOOTSTRAP_HANDSHAKE"] = "1"
    child = subprocess.Popen(
        [sys.executable, str(runner), str(entry)], cwd=str(tmp_path),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env=env)
    try:
        assert child.stdout.readline().strip() == "CLAIMED"
        child.stdin.write("COMMIT\n")
        child.stdin.flush()
        child.stdin.close()
        assert child.wait(timeout=10) != 0
        log = (tmp_path / "startup_error.log").read_text(encoding="utf-8")
        assert "simulated entry failure" in log
        replacement = acquire_instance_lock(str(tmp_path))
        assert replacement is not None
        replacement.close()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_concurrent_bootstrap_runners_have_one_claim_and_stale_setup_file_is_reusable(tmp_path):
    setup_path = tmp_path / ".chestscanner-venv-setup.lock"
    setup_path.write_bytes(b"0")
    setup_lock = acquire_instance_lock(
        str(tmp_path), lock_name=".chestscanner-venv-setup.lock")
    assert setup_lock is not None
    setup_lock.close()

    first, first_marker = _start_bootstrap_handshake_child(tmp_path)
    second, second_marker = _start_bootstrap_handshake_child(tmp_path)
    try:
        assert first.stdout.readline().strip() == "CLAIMED"
        assert second.stdout.readline().strip() == "BUSY"
        assert second.wait(timeout=10) == 0
        assert not first_marker.exists()
        assert not second_marker.exists()

        # Losing the parent before COMMIT must release the claim for a retry.
        first.stdin.close()
        assert first.wait(timeout=10) == 0
        retry, retry_marker = _start_bootstrap_handshake_child(tmp_path)
        try:
            assert retry.stdout.readline().strip() == "CLAIMED"
            retry.stdin.write("COMMIT\n")
            retry.stdin.flush()
            retry.stdin.close()
            assert retry.wait(timeout=15) == 0, retry.stderr.read()
            assert retry_marker.read_text(encoding="utf-8") == "started"
        finally:
            if retry.poll() is None:
                retry.kill()
                retry.wait(timeout=5)
    finally:
        for child in (first, second):
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
