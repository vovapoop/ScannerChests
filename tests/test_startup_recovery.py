"""Application entry-point startup ordering tests."""

import io
import runpy
import sys
import types
from pathlib import Path

import pytest

from chest_scanner import app_bootstrap, config, instance_lock

MAIN_PATH = Path(__file__).resolve().parents[1] / "main.py"


def _run_main(monkeypatch, events, recovery_error=None):
    fake_app = types.ModuleType("chest_scanner.app")
    fake_app.main = lambda: events.append("app")
    monkeypatch.setitem(sys.modules, "chest_scanner.app", fake_app)
    monkeypatch.setattr(
        app_bootstrap, "_ensure_venv",
        lambda: events.append("venv"),
    )
    monkeypatch.setattr(
        instance_lock, "acquire_instance_lock",
        lambda: events.append("lock") or object(),
    )

    def recover():
        events.append("recovery")
        if recovery_error is not None:
            raise recovery_error

    monkeypatch.setattr(config, "recover_profile_operations", recover)
    monkeypatch.setattr(
        app_bootstrap, "_startup_error",
        lambda error: events.append(("startup_error", str(error))),
    )
    runpy.run_path(str(MAIN_PATH), run_name="__main__")


def test_startup_recovers_profiles_after_lock_and_before_gui(monkeypatch):
    events = []

    _run_main(monkeypatch, events)

    assert events == ["venv", "lock", "recovery", "app"]


def test_startup_does_not_launch_gui_when_profile_recovery_fails(monkeypatch):
    events = []
    error = RuntimeError("recovery failed")

    with pytest.raises(RuntimeError, match="recovery failed"):
        _run_main(monkeypatch, events, recovery_error=error)

    assert events == [
        "venv",
        "lock",
        "recovery",
        ("startup_error", "recovery failed"),
    ]


def test_venv_setup_lock_is_held_through_interpreter_relaunch(tmp_path, monkeypatch):
    vpy = tmp_path / "Scripts" / "python.exe"
    vpyw = tmp_path / "Scripts" / "pythonw.exe"
    vpy.parent.mkdir(parents=True)
    vpy.touch()
    vpyw.touch()
    monkeypatch.setattr(app_bootstrap, "IS_FROZEN", False)
    monkeypatch.setattr(app_bootstrap, "_venv_active", lambda: False)
    monkeypatch.setattr(app_bootstrap, "BASE_DIR_FOR_APP", str(tmp_path))
    monkeypatch.setattr(app_bootstrap, "_VENV_DIR", str(tmp_path / ".venv"))
    monkeypatch.setattr(app_bootstrap, "_venv_py", lambda: (str(vpy), str(vpyw)))
    monkeypatch.setattr(app_bootstrap, "_req_hash", lambda: None)
    real_acquire = instance_lock.acquire_instance_lock
    acquired_locks = []

    def acquire(*args, **kwargs):
        lock = real_acquire(*args, **kwargs)
        acquired_locks.append(lock)
        return lock

    monkeypatch.setattr(instance_lock, "acquire_instance_lock", acquire)

    def launch(*args, **kwargs):
        assert acquired_locks[0]._file_handle is not None
        assert acquired_locks[1]._file_handle is None
        return types.SimpleNamespace(stdin=io.StringIO(), poll=lambda: None)

    monkeypatch.setattr(app_bootstrap, "_launch_child_with_handshake", launch)
    monkeypatch.setattr(app_bootstrap, "_entry_script", lambda: str(tmp_path / "main.py"))
    with pytest.raises(SystemExit) as exit_info:
        app_bootstrap._ensure_venv()
    assert exit_info.value.code == 0
    assert len(acquired_locks) == 2
    assert acquired_locks[0]._file_handle is None
    assert acquired_locks[1]._file_handle is None


def test_venv_setup_lock_is_released_when_child_process_cannot_start(tmp_path, monkeypatch):
    _, vpy, vpyw = _prepare_venv_bootstrap(tmp_path, monkeypatch)
    vpy.touch()
    vpyw.touch()
    real_acquire = instance_lock.acquire_instance_lock
    acquired_locks = []

    def acquire(*args, **kwargs):
        lock = real_acquire(*args, **kwargs)
        acquired_locks.append(lock)
        return lock

    def failed_launch(*args, **kwargs):
        assert acquired_locks[0]._file_handle is not None
        raise OSError("simulated CreateProcess failure")

    monkeypatch.setattr(instance_lock, "acquire_instance_lock", acquire)
    monkeypatch.setattr(app_bootstrap, "_launch_child_with_handshake", failed_launch)
    with pytest.raises(OSError, match="CreateProcess failure"):
        app_bootstrap._ensure_venv()

    assert acquired_locks[0]._file_handle is None
    retry_lock = real_acquire(
        str(tmp_path), lock_name=".chestscanner-venv-setup.lock")
    assert retry_lock is not None
    retry_lock.close()


def test_running_instance_prevents_venv_repair_or_dependency_install(tmp_path, monkeypatch):
    _, vpy, vpyw = _prepare_venv_bootstrap(tmp_path, monkeypatch)
    vpy.touch()
    vpyw.touch()
    active_app = instance_lock.acquire_instance_lock(str(tmp_path))
    assert active_app is not None
    calls = []
    monkeypatch.setattr(app_bootstrap, "_req_hash", lambda: "new-requirements-hash")
    monkeypatch.setattr(app_bootstrap, "_read_hash", lambda: "old-requirements-hash")
    monkeypatch.setattr(app_bootstrap, "_run_cmd", lambda command: calls.append(command))

    def launch(*args, **kwargs):
        assert instance_lock.acquire_instance_lock(str(tmp_path)) is None
        return False

    monkeypatch.setattr(app_bootstrap, "_launch_child_with_handshake", launch)
    try:
        app_bootstrap._ensure_venv()
        assert calls == []
    finally:
        active_app.close()

def _prepare_venv_bootstrap(tmp_path, monkeypatch):
    venv_dir = tmp_path / ".venv"
    scripts = venv_dir / "Scripts"
    scripts.mkdir(parents=True)
    vpy = scripts / "python.exe"
    vpyw = scripts / "pythonw.exe"
    monkeypatch.setattr(app_bootstrap, "IS_FROZEN", False)
    monkeypatch.setattr(app_bootstrap, "_venv_active", lambda: False)
    monkeypatch.setattr(app_bootstrap, "_is_windows", lambda: True)
    monkeypatch.setattr(app_bootstrap, "BASE_DIR_FOR_APP", str(tmp_path))
    monkeypatch.setattr(app_bootstrap, "_VENV_DIR", str(venv_dir))
    monkeypatch.setattr(app_bootstrap, "_REQ_HASH", str(venv_dir / ".requirements.sha256"))
    monkeypatch.setattr(app_bootstrap, "_REQ_FILE", str(tmp_path / "requirements.txt"))
    monkeypatch.setattr(app_bootstrap, "_venv_py", lambda: (str(vpy), str(vpyw)))
    monkeypatch.setattr(app_bootstrap, "_entry_script", lambda: str(tmp_path / "main.py"))
    monkeypatch.setattr(app_bootstrap, "_launch_child_with_handshake", lambda *a, **k: types.SimpleNamespace(stdin=io.StringIO(), poll=lambda: None))
    return venv_dir, vpy, vpyw


def test_missing_pythonw_repairs_venv_with_base_interpreter(tmp_path, monkeypatch):
    _, vpy, vpyw = _prepare_venv_bootstrap(tmp_path, monkeypatch)
    vpy.touch()
    commands = []

    def run_command(command):
        commands.append(command)
        assert command[0] == sys.executable
        assert command[1:] == ["-m", "venv", "--clear", str(vpy.parent.parent)]
        vpyw.touch()

    monkeypatch.setattr(app_bootstrap, "_req_hash", lambda: None)
    monkeypatch.setattr(app_bootstrap, "_run_cmd", run_command)
    with pytest.raises(SystemExit) as exit_info:
        app_bootstrap._ensure_venv()
    assert exit_info.value.code == 0
    assert len(commands) == 1
    assert vpy.exists() and vpyw.exists()


def test_interrupted_pip_install_does_not_write_hash_and_retry_recovers(tmp_path, monkeypatch):
    venv_dir, vpy, vpyw = _prepare_venv_bootstrap(tmp_path, monkeypatch)
    vpy.touch()
    vpyw.touch()
    marker = venv_dir / ".requirements.sha256"
    marker.write_text("truncated", encoding="utf-8")
    expected_hash = "a" * 64
    monkeypatch.setattr(app_bootstrap, "_req_hash", lambda: expected_hash)
    attempts = []

    def interrupted(command):
        attempts.append(command)
        raise RuntimeError("simulated pip interruption")

    monkeypatch.setattr(app_bootstrap, "_run_cmd", interrupted)
    with pytest.raises(RuntimeError, match="simulated pip interruption"):
        app_bootstrap._ensure_venv()
    assert marker.read_text(encoding="utf-8") == "truncated"
    assert not (venv_dir / ".requirements.sha256.tmp").exists()
    setup_lock = instance_lock.acquire_instance_lock(
        str(tmp_path), lock_name=".chestscanner-venv-setup.lock")
    assert setup_lock is not None
    setup_lock.close()

    def successful(command):
        attempts.append(command)

    monkeypatch.setattr(app_bootstrap, "_run_cmd", successful)
    with pytest.raises(SystemExit) as exit_info:
        app_bootstrap._ensure_venv()
    assert exit_info.value.code == 0
    assert len(attempts) == 2
    assert marker.read_text(encoding="utf-8") == expected_hash
    assert not (venv_dir / ".requirements.sha256.tmp").exists()


def test_hash_marker_replace_failure_keeps_previous_marker_intact(tmp_path, monkeypatch):
    venv_dir = tmp_path / ".venv"
    venv_dir.mkdir()
    marker = venv_dir / ".requirements.sha256"
    marker.write_text("previous", encoding="utf-8")
    monkeypatch.setattr(app_bootstrap, "_VENV_DIR", str(venv_dir))
    monkeypatch.setattr(app_bootstrap, "_REQ_HASH", str(marker))
    real_replace = app_bootstrap.os.replace

    def fail_replace(source, destination):
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr(app_bootstrap.os, "replace", fail_replace)
    with pytest.raises(OSError, match="atomic replace failure"):
        app_bootstrap._write_hash("new-hash")
    assert marker.read_text(encoding="utf-8") == "previous"
    assert not (venv_dir / ".requirements.sha256.tmp").exists()
    monkeypatch.setattr(app_bootstrap.os, "replace", real_replace)
    app_bootstrap._write_hash("new-hash")
    assert marker.read_text(encoding="utf-8") == "new-hash"

def test_interrupted_venv_creation_retries_from_base_interpreter(tmp_path, monkeypatch):
    venv_dir, vpy, vpyw = _prepare_venv_bootstrap(tmp_path, monkeypatch)
    calls = []

    def interrupted(command):
        calls.append(command)
        raise RuntimeError("simulated venv creation interruption")

    monkeypatch.setattr(app_bootstrap, "_req_hash", lambda: None)
    monkeypatch.setattr(app_bootstrap, "_run_cmd", interrupted)
    with pytest.raises(RuntimeError, match="venv creation interruption"):
        app_bootstrap._ensure_venv()
    assert not vpy.exists()
    assert not vpyw.exists()
    lock = instance_lock.acquire_instance_lock(
        str(tmp_path), lock_name=".chestscanner-venv-setup.lock")
    assert lock is not None
    lock.close()

    def recovered(command):
        calls.append(command)
        assert command == [sys.executable, "-m", "venv", str(venv_dir)]
        vpy.touch()
        vpyw.touch()

    monkeypatch.setattr(app_bootstrap, "_run_cmd", recovered)
    with pytest.raises(SystemExit) as exit_info:
        app_bootstrap._ensure_venv()
    assert exit_info.value.code == 0
    assert len(calls) == 2
    assert vpy.exists() and vpyw.exists()

def test_parent_handshake_keeps_setup_lock_until_child_claims_instance_lock(
        tmp_path, monkeypatch):
    entry = tmp_path / "main.py"
    marker = tmp_path / "application-started"
    entry.write_text("\n".join([
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
    monkeypatch.setattr(app_bootstrap, "BASE_DIR_FOR_APP", str(tmp_path))
    monkeypatch.setattr(app_bootstrap, "_entry_script", lambda: str(entry))
    monkeypatch.setattr(app_bootstrap.sys, "argv", [str(entry), str(marker)])
    setup_lock = instance_lock.acquire_instance_lock(
        str(tmp_path), lock_name=".chestscanner-venv-setup.lock")
    assert setup_lock is not None
    child = None
    try:
        child = app_bootstrap._launch_child_with_handshake(sys.executable)
        assert child is not False
        assert instance_lock.acquire_instance_lock(str(tmp_path)) is None
        assert instance_lock.acquire_instance_lock(
            str(tmp_path), lock_name=".chestscanner-venv-setup.lock") is None
        setup_lock.close()
        child.stdin.write("COMMIT\n")
        child.stdin.flush()
        child.stdin.close()
        assert child.wait(timeout=15) == 0
        assert marker.read_text(encoding="utf-8") == "started"
    finally:
        setup_lock.close()
        if child is not None and child is not False:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
