"""Тесты отката последнего сканирования (undo_last_scan)."""
import json
import os

import pytest

from chest_scanner import paths, storage
from chest_scanner.scanner.workflow import WorkflowMixin


class UndoStub(WorkflowMixin):
    """Только workflow-логика, без экрана и захвата."""


@pytest.fixture()
def store(tmp_path, monkeypatch):
    results = tmp_path / "results"
    (results / "history").mkdir(parents=True)
    monkeypatch.setattr(paths, "RESULTS_DIR", str(results))
    monkeypatch.setattr(paths, "RESULTS_FILE", str(results / "chests.json"))
    monkeypatch.setattr(paths, "RESULTS_DB", str(results / "chests.db"))
    monkeypatch.setattr(paths, "HISTORY_DIR", str(results / "history"))
    # В приложении базу создаёт старт ридера (ScannerBaseMixin.__init__).
    storage.init_db(paths.RESULTS_DB)
    return results


def _cd(i, minute=0):
    return {"id": str(i), "timestamp": f"01.01.2026 12:{minute:02d}:00",
            "occupied_slots": i, "unique_items": 1, "unknown_items": [],
            "screen_resolution": [1920, 1080], "slots": [],
            "totals": {"Алмаз": i}, "categories": {}}


def test_delete_last_returns_newest_and_empties_db(tmp_path):
    db = str(tmp_path / "chests.db")
    storage.init_db(db)
    assert storage.add_chest(db, _cd(1, minute=1))
    assert storage.add_chest(db, _cd(2, minute=2))
    assert storage.delete_last(db)["id"] == "2"
    assert storage.count(db) == 1
    assert storage.delete_last(db)["id"] == "1"
    assert storage.delete_last(db) is None
    assert storage.count(db) == 0


def test_undo_removes_last_from_json_and_db(store):
    stub = UndoStub()
    stub.save_chest_result(_cd(1, minute=1))
    stub.save_chest_result(_cd(2, minute=2))
    removed = stub.undo_last_scan()
    assert removed["id"] == "2"
    data = json.loads(store.joinpath("chests.json").read_text(encoding="utf-8"))
    assert [c["id"] for c in data["chests"]] == ["1"]
    assert storage.count(paths.RESULTS_DB) == 1
    assert storage.load_all(paths.RESULTS_DB)[0]["id"] == "1"


def test_undo_picks_chest_by_id_from_json(store):
    """Последний в БД — не обязательно последний в chests.json (гонки записи)."""
    stub = UndoStub()
    stub.save_chest_result(_cd(1, minute=5))
    stub.save_chest_result(_cd(2, minute=1))
    removed = stub.undo_last_scan()
    assert removed["id"] == "1"
    data = json.loads(store.joinpath("chests.json").read_text(encoding="utf-8"))
    assert [c["id"] for c in data["chests"]] == ["2"]


def test_undo_falls_back_to_newest_archive(store):
    """Запись уже заархивирована — откат удаляет её из новейшего архива."""
    db = paths.RESULTS_DB
    storage.init_db(db)
    storage.add_chest(db, _cd(1, minute=1))
    storage.add_chest(db, _cd(2, minute=2))
    arch = os.path.join(paths.HISTORY_DIR, "report_2026-01-01.json")
    with open(arch, "w", encoding="utf-8") as f:
        json.dump({"chests": [_cd(1, minute=1), _cd(2, minute=2)]}, f,
                  ensure_ascii=False)
    with open(paths.RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump({"chests": []}, f)

    removed = UndoStub().undo_last_scan()
    assert removed["id"] == "2"
    with open(arch, encoding="utf-8") as f:
        data = json.load(f)
    assert [c["id"] for c in data["chests"]] == ["1"]
    assert storage.count(db) == 1


def test_undo_on_empty_store_is_noop(store):
    stub = UndoStub()
    assert stub.undo_last_scan() is None
    assert stub.undo_last_scan() is None



def test_latest_scan_and_undo_include_archived_results(store):
    import json

    stub = UndoStub()
    older = _cd(1, minute=1)
    current = _cd(2, minute=2)
    archived_newest = _cd(3, minute=3)
    stub.save_chest_result(older)
    stub.save_chest_result(current)
    archive = os.path.join(paths.HISTORY_DIR, "report_2026-01-01.json")
    with open(archive, "w", encoding="utf-8") as stream:
        json.dump({"chests": [archived_newest]}, stream)

    assert stub.latest_scan()["id"] == "3"
    removed = stub.undo_last_scan()

    assert removed["id"] == "3"
    with open(archive, encoding="utf-8") as stream:
        assert json.load(stream)["chests"] == []
    assert {scan["id"] for scan in storage.load_all(paths.RESULTS_DB)} == {"1", "2"}


def test_undo_does_not_delete_sqlite_entry_when_json_save_fails(store, monkeypatch):
    from chest_scanner.scanner import workflow as workflow_mod

    stub = UndoStub()
    stub.save_chest_result(_cd(1, minute=1))
    before = storage.load_all(paths.RESULTS_DB)

    def fail_save(*args, **kwargs):
        raise OSError("simulated disk error")

    monkeypatch.setattr(workflow_mod, "save_json", fail_save)

    assert stub.undo_last_scan() is None
    assert storage.load_all(paths.RESULTS_DB) == before
    data = json.loads(store.joinpath("chests.json").read_text(encoding="utf-8"))
    assert [scan["id"] for scan in data["chests"]] == ["1"]


def test_latest_scan_uses_timestamp_not_json_array_order(store):
    import json

    stub = UndoStub()
    first = _cd(1, minute=5)
    second = _cd(2, minute=1)
    store.joinpath("chests.json").write_text(
        json.dumps({"chests": [second, first]}), encoding="utf-8")

    assert stub.latest_scan()["id"] == "1"
    assert stub.undo_last_scan()["id"] == "1"
    data = json.loads(store.joinpath("chests.json").read_text(encoding="utf-8"))
    assert [scan["id"] for scan in data["chests"]] == ["2"]


def test_undo_refuses_to_change_history_when_archive_is_unreadable(store):
    stub = UndoStub()
    stub.save_chest_result(_cd(1, minute=1))
    archive = store / "history" / "report_broken.json"
    archive.write_text("{broken", encoding="utf-8")
    before = storage.load_all(paths.RESULTS_DB)

    assert stub.latest_scan() is None
    assert stub.undo_last_scan() is None
    assert storage.load_all(paths.RESULTS_DB) == before
    data = json.loads(store.joinpath("chests.json").read_text(encoding="utf-8"))
    assert [scan["id"] for scan in data["chests"]] == ["1"]


def test_invalid_timestamp_is_older_than_valid_and_is_not_undone(store):
    stub = UndoStub()
    valid = _cd(1, minute=1)
    invalid = _cd(2, minute=2)
    invalid["timestamp"] = "not-a-date"
    store.joinpath("chests.json").write_text(
        json.dumps({"chests": [valid, invalid]}), encoding="utf-8")

    assert stub.latest_scan()["id"] == "1"
    assert stub.undo_last_scan()["id"] == "1"
    remaining = json.loads(store.joinpath("chests.json").read_text(encoding="utf-8"))
    assert [scan["id"] for scan in remaining["chests"]] == ["2"]


def test_undo_tie_break_matches_history_source_order(store):
    stub = UndoStub()
    current = _cd(1, minute=5)
    archived = _cd(2, minute=5)
    store.joinpath("chests.json").write_text(
        json.dumps({"chests": [current]}), encoding="utf-8")
    archive = store / "history" / "report_2026-01-01.json"
    archive.write_text(json.dumps({"chests": [archived]}), encoding="utf-8")

    assert stub.latest_scan()["id"] == "1"
    assert stub.undo_last_scan()["id"] == "1"


def test_undo_refuses_to_remove_different_scan_than_confirmed(store):
    stub = UndoStub()
    first = _cd(1, minute=1)
    second = _cd(2, minute=2)
    stub.save_chest_result(first)
    confirmed = stub.latest_scan()
    stub.save_chest_result(second)

    assert stub.undo_last_scan(expected_scan=confirmed) is None
    data = json.loads(store.joinpath("chests.json").read_text(encoding="utf-8"))
    assert [scan["id"] for scan in data["chests"]] == ["1", "2"]


def test_undo_confirmed_scan_with_same_timestamp_uses_id(store):
    stub = UndoStub()
    first = _cd(1, minute=5)
    second = _cd(2, minute=5)
    stub.save_chest_result(first)
    stub.save_chest_result(second)
    confirmed = stub.latest_scan()

    assert stub.undo_last_scan(expected_scan=confirmed)["id"] == "1"


def test_history_dialog_order_is_chronological_and_invalid_dates_are_last(store):
    from chest_scanner.gui.dialogs import load_all_chests

    december = _cd(1)
    december["timestamp"] = "31.12.2025 23:00:00"
    january = _cd(2)
    january["timestamp"] = "01.01.2026 00:00:00"
    invalid = _cd(3)
    invalid["timestamp"] = "unknown"
    store.joinpath("chests.json").write_text(
        json.dumps({"chests": [december, invalid, january]}), encoding="utf-8")

    assert [scan["id"] for scan in load_all_chests()] == ["2", "1", "3"]


def test_sqlite_latest_order_places_invalid_timestamp_before_valid(store):
    valid = _cd(1, minute=1)
    invalid = _cd(2, minute=2)
    invalid["timestamp"] = "not-a-date"
    assert storage.add_chest(paths.RESULTS_DB, valid)
    assert storage.add_chest(paths.RESULTS_DB, invalid)

    assert storage.delete_last(paths.RESULTS_DB)["id"] == "1"
