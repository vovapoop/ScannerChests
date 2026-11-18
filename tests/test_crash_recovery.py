"""Process-crash recovery tests for the JSON history and SQLite cache."""
import json
import os
import subprocess
import sys

from chest_scanner import storage


def _record(record_id):
    return {"id": record_id, "timestamp": "10.11.2026 03:00:00",
            "slots": [], "totals": {"Stone": 3}, "unknown_items": []}


def _crash(code, script, *args):
    result = subprocess.run(
        [sys.executable, "-c", script, *map(str, args)],
        cwd=os.getcwd(), capture_output=True, text=True, timeout=20)
    assert result.returncode == code, (result.returncode, result.stdout, result.stderr)


def test_startup_reconcile_recovers_after_process_dies_after_json_commit(tmp_path):
    results = tmp_path / "chests.json"
    history = tmp_path / "history"
    history.mkdir()
    db = str(tmp_path / "chests.db")
    original = _record("before-crash")
    added = _record("json-committed")
    results.write_text(json.dumps({"chests": [original]}), encoding="utf-8")
    storage.sync_from_json(db, [original])

    script = (
        "import json, os, sys; "
        "from chest_scanner.commons import save_json; "
        "path, records = sys.argv[1], json.loads(sys.argv[2]); "
        "save_json(path, {'chests': records}); os._exit(71)"
    )
    _crash(71, script, results, json.dumps([original, added]))

    assert json.loads(results.read_text(encoding="utf-8"))["chests"] == [original, added]
    assert storage.reconcile_from_json(db, str(results), str(history)) is True
    assert storage.is_synced_with_json(
        db, storage.load_json_history(str(results), str(history)))
    assert {row["id"] for row in storage.load_all(db)} == {"before-crash", "json-committed"}


def test_startup_reconcile_recovers_after_process_dies_during_sqlite_temp_build(tmp_path):
    results = tmp_path / "chests.json"
    history = tmp_path / "history"
    history.mkdir()
    db = str(tmp_path / "chests.db")
    original = _record("stable-cache")
    results.write_text(json.dumps({"chests": [original]}), encoding="utf-8")
    storage.sync_from_json(db, [original])
    incoming = _record("new-from-json")
    results.write_text(json.dumps({"chests": [original, incoming]}), encoding="utf-8")

    script = "\n".join([
        "import os, sys",
        "from chest_scanner import storage",
        "db = sys.argv[1]",
        "record = {'id': 'new-from-json', 'timestamp': '10.11.2026 03:00:00', 'slots': [], 'totals': {'Stone': 3}, 'unknown_items': []}",
        "real = storage.add_chest",
        "def crash(path, chest):",
        "    result = real(path, chest)",
        "    if '.chests-rebuild-' in path: os._exit(72)",
        "    return result",
        "storage.add_chest = crash",
        "storage.sync_from_json(db, [record])",
    ])
    _crash(72, script, db)

    # The previous committed cache is intact; the interrupted temporary DB is ignored.
    assert {row["id"] for row in storage.load_all(db)} == {"stable-cache"}
    assert list(tmp_path.glob(".chests-rebuild-*.db"))
    assert storage.reconcile_from_json(db, str(results), str(history)) is True
    assert {row["id"] for row in storage.load_all(db)} == {"stable-cache", "new-from-json"}
    assert storage.is_synced_with_json(
        db, storage.load_json_history(str(results), str(history)))


def test_process_death_after_archive_commit_is_safe_to_retry(tmp_path, monkeypatch):
    results = tmp_path / "chests.json"
    history = tmp_path / "history"
    history.mkdir()
    db = tmp_path / "chests.db"
    original = _record("archive-before-crash")
    retry = _record("retry-after-crash")
    results.write_text(json.dumps({"chests": [original]}), encoding="utf-8")

    script = chr(10).join([
        "import os, sys",
        "from chest_scanner import paths",
        "paths.RESULTS_FILE, paths.RESULTS_DB, paths.HISTORY_DIR = sys.argv[1:4]",
        "from chest_scanner.scanner import workflow",
        "real_save = workflow.save_json",
        "def crash_after_archive(path, data, *args, **kwargs):",
        "    if path == paths.RESULTS_FILE and data == {'chests': []}:",
        "        os._exit(74)",
        "    return real_save(path, data, *args, **kwargs)",
        "workflow.save_json = crash_after_archive",
        "class Worker(workflow.WorkflowMixin): MAX_RESULTS_FILE_BYTES = 1",
        "Worker().save_chest_result({'id': 'retry-after-crash', 'timestamp': '10.11.2026 03:00:00', 'slots': [], 'totals': {'Stone': 3}, 'unknown_items': []})",
    ])
    _crash(74, script, results, db, history)

    archives = list(history.glob("report_*.json"))
    assert len(archives) == 1
    assert json.loads(archives[0].read_text(encoding="utf-8"))["chests"] == [
        original]
    assert json.loads(results.read_text(encoding="utf-8"))["chests"] == [
        original]

    from chest_scanner import paths as paths_mod
    from chest_scanner.scanner.workflow import WorkflowMixin
    monkeypatch.setattr(paths_mod, "RESULTS_FILE", str(results))
    monkeypatch.setattr(paths_mod, "RESULTS_DB", str(db))
    monkeypatch.setattr(paths_mod, "HISTORY_DIR", str(history))
    worker = WorkflowMixin()
    worker.MAX_RESULTS_FILE_BYTES = 1
    assert worker.save_chest_result(retry) is True
    assert len(list(history.glob("report_*.json"))) == 1
    assert json.loads(archives[0].read_text(encoding="utf-8"))["chests"] == [
        original]
    assert json.loads(results.read_text(encoding="utf-8"))["chests"] == [
        retry]
    assert storage.reconcile_from_json(str(db), str(results), str(history)) is True
    assert {record["id"] for record in storage.load_all(str(db))} == {
        "archive-before-crash", "retry-after-crash"}
    assert storage.is_synced_with_json(
        str(db), storage.load_json_history(str(results), str(history)))


def test_process_death_before_json_replace_keeps_previous_file_and_cache(tmp_path):
    results = tmp_path / "chests.json"
    history = tmp_path / "history"
    history.mkdir()
    db = str(tmp_path / "chests.db")
    original = _record("committed-before-crash")
    results.write_text(json.dumps({"chests": [original]}), encoding="utf-8")
    storage.sync_from_json(db, [original])

    script = "\n".join([
        "import json, os, sys",
        "import chest_scanner.commons as commons",
        "path = sys.argv[1]",
        "def crash_before_replace(*args, **kwargs): os._exit(73)",
        "commons.os.replace = crash_before_replace",
        "commons.save_json(path, {'chests': [{'id': 'never-committed'}]})",
    ])
    _crash(73, script, results)

    assert json.loads(results.read_text(encoding="utf-8"))["chests"] == [original]
    assert {row["id"] for row in storage.load_all(db)} == {"committed-before-crash"}
    assert storage.reconcile_from_json(db, str(results), str(history)) is False


def test_cleanup_removes_only_old_rebuild_databases_and_sidecars(tmp_path):
    import os
    import sqlite3
    import time

    old = tmp_path / ".chests-rebuild-abandoned.db"
    recent = tmp_path / ".chests-rebuild-active.db"
    unrelated = tmp_path / "user-backup.db"
    for path in (old, recent, unrelated):
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.commit()
        connection.close()
    (tmp_path / (old.name + "-wal")).write_bytes(b"wal")
    old_time = time.time() - 120
    os.utime(old, (old_time, old_time))

    removed = storage.cleanup_stale_rebuild_files(
        str(tmp_path), max_age_seconds=60)

    assert removed == 1
    assert not old.exists()
    assert not (tmp_path / (old.name + "-wal")).exists()
    assert recent.exists()
    assert unrelated.exists()


def test_reconcile_is_idempotent_and_does_not_rebuild_synced_database(tmp_path, monkeypatch):
    results = tmp_path / "chests.json"
    history = tmp_path / "history"
    history.mkdir()
    db = str(tmp_path / "chests.db")
    record = _record("already-synced")
    results.write_text(json.dumps({"chests": [record]}), encoding="utf-8")
    storage.sync_from_json(db, [record])

    def unexpected_rebuild(*args, **kwargs):
        raise AssertionError("synced database must not be rebuilt")

    monkeypatch.setattr(storage, "sync_from_json", unexpected_rebuild)
    assert storage.reconcile_from_json(db, str(results), str(history)) is False
    assert storage.reconcile_from_json(db, str(results), str(history)) is False


def test_history_guard_serializes_separate_processes(tmp_path):
    import threading
    import time

    from chest_scanner.commons import results_history_guard

    history_path = tmp_path / "results.json"
    ready = tmp_path / "child-ready"
    release = tmp_path / "child-release"
    script = "\n".join([
        "import pathlib, sys, time",
        "from chest_scanner.commons import results_history_guard",
        "target, ready, release = map(pathlib.Path, sys.argv[1:])",
        "with results_history_guard(str(target)):",
        "    ready.write_text('ready')",
        "    while not release.exists(): time.sleep(0.01)",
    ])
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(history_path), str(ready), str(release)],
        cwd=os.getcwd(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "child process did not acquire the history lock"

        acquired = threading.Event()
        def enter_guard():
            with results_history_guard(str(history_path)):
                acquired.set()

        worker = threading.Thread(target=enter_guard, daemon=True)
        worker.start()
        assert not acquired.wait(0.2), "second process bypassed the inter-process lock"
        release.write_text("release")
        assert acquired.wait(5), "lock was not released after child process exited critical section"
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert child.wait(timeout=5) == 0
    finally:
        release.write_text("release")
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)

def test_history_guard_holds_profile_data_lock_before_history(tmp_path):
    import threading

    from chest_scanner.commons import profile_data_guard, results_history_guard

    acquired = threading.Event()

    def acquire_profile_guard():
        with profile_data_guard():
            acquired.set()

    with results_history_guard(str(tmp_path / "chests.json")):
        worker = threading.Thread(target=acquire_profile_guard, daemon=True)
        worker.start()
        assert not acquired.wait(0.1), "history guard did not acquire profile-data lock first"

    assert acquired.wait(2), "profile-data lock was not released after history guard"
    worker.join(timeout=2)
    assert not worker.is_alive()
