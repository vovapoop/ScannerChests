"""Safe JSON history restore regression tests."""
import json
import threading
from pathlib import Path

import pytest

from chest_scanner.commons import results_history_lock
from chest_scanner.history_restore import apply_history_restore, prepare_history_restore


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_restore_adds_only_new_ids_and_preserves_unknown_quantities(tmp_path):
    current_path = tmp_path / "chests.json"
    backup_path = tmp_path / "export.json"
    current = {"chests": [{"id": "same", "timestamp": "newer", "totals": {"Stone": 5}}]}
    incoming = [
        {"id": "same", "timestamp": "older", "totals": {"Stone": 1}},
        {"id": "new", "timestamp": "backup", "slots": [{"item": "Mystery", "quantity": None}],
         "totals": {"Mystery": None}, "unknown_items": ["Mystery"]},
        {"id": "new", "timestamp": "duplicate"},
    ]
    _write(current_path, current)
    _write(backup_path, incoming)

    plan = prepare_history_restore(str(backup_path), str(current_path), current["chests"])
    assert (plan["added"], plan["skipped_existing"], plan["skipped_duplicates"]) == (1, 1, 1)
    backup_copy = apply_history_restore(plan)

    saved = json.loads(current_path.read_text(encoding="utf-8"))
    assert saved["chests"][0] == current["chests"][0]
    assert saved["chests"][1]["totals"]["Mystery"] is None
    assert saved["chests"][1]["slots"][0]["quantity"] is None
    assert backup_copy and Path(backup_copy).exists()
    assert json.loads(Path(backup_copy).read_text(encoding="utf-8")) == current


def test_restore_accepts_internal_chests_wrapper_and_legacy_existing_without_id(tmp_path):
    current_path = tmp_path / "chests.json"
    backup_path = tmp_path / "backup.json"
    _write(current_path, {"chests": [{"timestamp": "legacy"}]})
    _write(backup_path, {"chests": [{"id": "added", "totals": {}}]})

    plan = prepare_history_restore(str(backup_path), str(current_path), [{"timestamp": "legacy"}])
    assert plan["added"] == 1
    apply_history_restore(plan)
    assert len(json.loads(current_path.read_text(encoding="utf-8"))["chests"]) == 2


@pytest.mark.parametrize("payload", [None, {"items": []}, {"chests": "bad"}, [None], [{"id": ""}]])
def test_restore_rejects_malformed_backup_without_touching_current(tmp_path, payload):
    current_path = tmp_path / "chests.json"
    backup_path = tmp_path / "backup.json"
    original = {"chests": [{"id": "keep"}]}
    _write(current_path, original)
    _write(backup_path, payload)

    with pytest.raises(ValueError):
        prepare_history_restore(str(backup_path), str(current_path), original["chests"])
    assert json.loads(current_path.read_text(encoding="utf-8")) == original


def test_restore_rejects_destination_changed_after_preview(tmp_path):
    current_path = tmp_path / "chests.json"
    backup_path = tmp_path / "backup.json"
    _write(current_path, {"chests": [{"id": "old"}]})
    _write(backup_path, [{"id": "new"}])
    plan = prepare_history_restore(str(backup_path), str(current_path), [{"id": "old"}])
    _write(current_path, {"chests": [{"id": "old"}, {"id": "concurrent"}]})

    with pytest.raises(ValueError, match="изменилась"):
        apply_history_restore(plan)
    assert [row["id"] for row in json.loads(current_path.read_text(encoding="utf-8"))["chests"]] == [
        "old", "concurrent"]


def test_restore_rejects_backup_changed_after_preview(tmp_path):
    current_path = tmp_path / "chests.json"
    backup_path = tmp_path / "backup.json"
    _write(current_path, {"chests": []})
    _write(backup_path, [{"id": "new"}])
    plan = prepare_history_restore(str(backup_path), str(current_path), [])
    _write(backup_path, [{"id": "changed"}])

    with pytest.raises(ValueError, match="резервной копии изменился"):
        apply_history_restore(plan)
    assert json.loads(current_path.read_text(encoding="utf-8")) == {"chests": []}


def test_restore_waits_for_in_progress_scan_history_mutation(tmp_path):
    current_path = tmp_path / "chests.json"
    backup_path = tmp_path / "backup.json"
    _write(current_path, {"chests": [{"id": "existing"}]})
    _write(backup_path, [{"id": "restored"}])
    plan = prepare_history_restore(str(backup_path), str(current_path), [{"id": "existing"}])
    entered = threading.Event()
    finished = threading.Event()
    errors = []

    def restore():
        entered.set()
        try:
            apply_history_restore(plan)
        except Exception as exc:  # pragma: no cover - assertion reports worker failure
            errors.append(exc)
        finally:
            finished.set()

    with results_history_lock():
        worker = threading.Thread(target=restore)
        worker.start()
        assert entered.wait(2)
        assert not finished.is_set()
        assert [row["id"] for row in json.loads(current_path.read_text(encoding="utf-8"))["chests"]] == [
            "existing"]
    worker.join(2)
    assert not worker.is_alive()
    assert not errors
    assert [row["id"] for row in json.loads(current_path.read_text(encoding="utf-8"))["chests"]] == [
        "existing", "restored"]


def test_restore_json_commit_survives_sqlite_sync_failure_and_can_be_rebuilt(tmp_path, monkeypatch):
    from chest_scanner import storage

    current_path = tmp_path / "chests.json"
    backup_path = tmp_path / "backup.json"
    db_path = str(tmp_path / "chests.db")
    _write(current_path, {"chests": [{"id": "existing"}]})
    _write(backup_path, [{"id": "restored"}])
    storage.init_db(db_path)
    assert storage.add_chest(db_path, {"id": "existing", "timestamp": "old"})
    plan = prepare_history_restore(str(backup_path), str(current_path), [{"id": "existing"}])
    real_sync = storage.sync_from_json

    def fail_sync(*_args, **_kwargs):
        raise OSError("simulated SQLite replacement failure")

    monkeypatch.setattr(storage, "sync_from_json", fail_sync)
    apply_history_restore(plan)
    with pytest.raises(OSError, match="simulated"):
        storage.sync_from_json(db_path, json.loads(current_path.read_text(encoding="utf-8"))["chests"])
    assert [row["id"] for row in json.loads(current_path.read_text(encoding="utf-8"))["chests"]] == [
        "existing", "restored"]
    assert storage.count(db_path) == 1

    monkeypatch.setattr(storage, "sync_from_json", real_sync)
    assert storage.sync_from_json(
        db_path, json.loads(current_path.read_text(encoding="utf-8"))["chests"]) == 2
    assert {row["id"] for row in storage.load_all(db_path)} == {"existing", "restored"}


def test_restore_json_write_failure_does_not_change_destination(tmp_path, monkeypatch):
    from chest_scanner import history_restore

    current_path = tmp_path / "chests.json"
    backup_path = tmp_path / "backup.json"
    original = {"chests": [{"id": "existing"}]}
    _write(current_path, original)
    _write(backup_path, [{"id": "restored"}])
    plan = prepare_history_restore(str(backup_path), str(current_path), original["chests"])

    def fail_write(*_args, **_kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(history_restore, "save_json", fail_write)
    with pytest.raises(OSError, match="simulated"):
        apply_history_restore(plan)
    assert json.loads(current_path.read_text(encoding="utf-8")) == original


def test_restore_rejects_archive_history_changed_after_preview(tmp_path):
    current_path = tmp_path / "chests.json"
    backup_path = tmp_path / "backup.json"
    _write(current_path, {"chests": [{"id": "current"}]})
    _write(backup_path, [{"id": "restore"}])
    plan = prepare_history_restore(
        str(backup_path), str(current_path), [{"id": "current"}, {"id": "archive-old"}])

    with pytest.raises(ValueError):
        apply_history_restore(
            plan, existing_chests=[{"id": "current"}, {"id": "archive-old"},
                                   {"id": "archive-new"}])
    assert [row["id"] for row in json.loads(current_path.read_text(encoding="utf-8"))["chests"]] == [
        "current"]
