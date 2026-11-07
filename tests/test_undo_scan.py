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
