"""Тесты SQLite-хранилища результатов и парсинга горячих клавиш."""
import os

from chest_scanner import storage
from chest_scanner.hotkeys import _vk_for_name


def _cd(i):
    return {"id": str(i), "timestamp": f"01.0{i}.2026 12:00:00",
            "occupied_slots": 1, "unique_items": 1, "unknown_items": [],
            "screen_resolution": [1920, 1080],
            "slots": [], "totals": {"Алмаз": i}, "categories": {}}


def test_add_load_count(tmp_path):
    db = os.path.join(str(tmp_path), "chests.db")
    storage.init_db(db)
    assert storage.add_chest(db, _cd(2))
    assert storage.add_chest(db, _cd(3))
    assert storage.count(db) == 2
    ids = [c["id"] for c in storage.load_all(db)]
    assert ids == ["2", "3"]


def test_add_chest_idempotent(tmp_path):
    db = os.path.join(str(tmp_path), "chests.db")
    storage.init_db(db)
    assert storage.add_chest(db, _cd(1))
    assert storage.add_chest(db, _cd(1))  # UPDATE по первичному ключу id
    assert storage.count(db) == 1


def test_add_chest_invalid_ignored(tmp_path):
    db = os.path.join(str(tmp_path), "chests.db")
    storage.init_db(db)
    assert not storage.add_chest(db, {"no_id": True})
    assert storage.count(db) == 0


def test_stats_aggregates(tmp_path):
    db = os.path.join(str(tmp_path), "chests.db")
    storage.init_db(db)
    storage.add_chest(db, _cd(5))
    storage.add_chest(db, _cd(3))
    st = storage.stats(db)
    assert st["chests"] == 2
    assert st["items_total"] == 8
    assert st["top_qty"][0] == ("Алмаз", 8)


def test_sync_from_json(tmp_path):
    db = os.path.join(str(tmp_path), "chests.db")
    added = storage.sync_from_json(db, [_cd(1), _cd(2)])
    assert added == 2
    assert storage.count(db) == 2


def test_vk_for_name():
    assert _vk_for_name("f6") == 0x75  # «Показать окно» (toggle_window)
    assert _vk_for_name("f8") == 0x77
    assert _vk_for_name("F1") == 0x70
    assert _vk_for_name("a") == ord("A")
    assert _vk_for_name("5") == ord("5")
    assert _vk_for_name("") is None
    assert _vk_for_name("bogus") is None
    assert _vk_for_name(None) is None