"""Тесты SQLite-хранилища результатов и парсинга горячих клавиш."""
import json
import os
import sqlite3

from chest_scanner import paths, storage
from chest_scanner.config import validate_config
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


def test_hotkey_names_accept_only_single_keys():
    from chest_scanner.hotkeys import is_valid_hotkey_name
    assert is_valid_hotkey_name("f6")
    assert is_valid_hotkey_name("A")
    assert is_valid_hotkey_name("5")
    assert not is_valid_hotkey_name("ctrl+f6")
    assert not is_valid_hotkey_name("alt+f8")
    assert not is_valid_hotkey_name("f6+alt")
    assert not is_valid_hotkey_name("unknown")
    assert not is_valid_hotkey_name("")


def test_config_replaces_unsupported_modifier_hotkeys():
    cfg, _ = validate_config({"hotkeys": {"toggle_window": "ctrl+f6", "scan": "alt+f8", "exit": "f10"}})
    assert cfg["hotkeys"] == {"toggle_window": "f6", "scan": "f8", "exit": "f10"}


def test_hotkeys_config_has_only_supported_actions():
    assert set(paths.DEFAULT_CONFIG["hotkeys"]) == {"toggle_window", "scan", "exit"}



def test_init_db_quarantines_corrupt_database(tmp_path):
    db = os.path.join(str(tmp_path), "chests.db")
    with open(db, "wb") as stream:
        stream.write(b"this is not a sqlite database")

    storage.init_db(db)

    assert storage.count(db) == 0
    quarantined = list(tmp_path.glob("chests.db.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"this is not a sqlite database"


def test_sync_from_json_rebuilds_cache_and_deduplicates(tmp_path):
    db = os.path.join(str(tmp_path), "chests.db")
    storage.init_db(db)
    assert storage.add_chest(db, _cd("stale"))

    added = storage.sync_from_json(db, [
        _cd(1), _cd(1), {"no_id": True}, _cd(2), None,
    ])

    assert added == 2
    assert storage.count(db) == 2
    assert {chest["id"] for chest in storage.load_all(db)} == {"1", "2"}


def test_sync_from_json_recovers_corrupt_cache(tmp_path):
    db = os.path.join(str(tmp_path), "chests.db")
    with open(db, "wb") as stream:
        stream.write(b"broken sqlite bytes")

    added = storage.sync_from_json(db, [_cd(1), _cd(2)])

    assert added == 2
    assert storage.count(db) == 2
    assert len(list(tmp_path.glob("chests.db.corrupt-*"))) == 1



def test_init_db_quarantines_incompatible_schema(tmp_path):
    db = os.path.join(str(tmp_path), "chests.db")
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE chests (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
    conn.commit()
    conn.close()

    storage.init_db(db)

    assert storage.count(db) == 0
    assert len(list(tmp_path.glob("chests.db.corrupt-*"))) == 1


def test_stats_counts_items_with_unknown_quantity_as_present(tmp_path):
    db = os.path.join(str(tmp_path), "chests.db")
    storage.init_db(db)
    chest = _cd(1)
    chest["slots"] = [
        {"row": 1, "column": 1, "item": "item_unknown_qty", "quantity": None},
        {"row": 1, "column": 2, "item": "item_known_qty", "quantity": 4},
    ]
    chest["totals"] = {"item_known_qty": 4}
    chest["unique_items"] = 2
    chest["quantity_unknown_slots"] = 1
    assert storage.add_chest(db, chest)

    stats = storage.stats(db)

    assert stats["unique_items"] == 2
    assert stats["items_total"] == 4
    assert stats["top_qty"] == [("item_known_qty", 4)]
    assert dict(stats["top_freq"]) == {"item_unknown_qty": 1, "item_known_qty": 1}


def test_sync_from_json_keeps_existing_database_when_temp_build_fails(tmp_path, monkeypatch):
    db = os.path.join(str(tmp_path), "chests.db")
    storage.init_db(db)
    assert storage.add_chest(db, _cd("preserved"))
    before = storage.load_all(db)

    monkeypatch.setattr(storage, "add_chest", lambda *_args, **_kwargs: False)
    import pytest
    with pytest.raises(sqlite3.DatabaseError):
        storage.sync_from_json(db, [_cd("new")])

    assert storage.load_all(db) == before
    assert not list(tmp_path.glob(".chests-rebuild-*.db"))


def test_reconcile_from_json_repairs_missing_and_stale_sqlite_records(tmp_path):
    current = tmp_path / "chests.json"
    history = tmp_path / "history"
    history.mkdir()
    current.write_text(json.dumps({"chests": [_cd("current")]}), encoding="utf-8")
    (history / "report_1.json").write_text(
        json.dumps({"chests": [_cd("archived")]}), encoding="utf-8")
    db = str(tmp_path / "chests.db")
    storage.init_db(db)
    assert storage.add_chest(db, _cd("stale"))

    assert storage.reconcile_from_json(db, str(current), str(history)) is True
    assert {row["id"] for row in storage.load_all(db)} == {"current", "archived"}
    assert storage.is_synced_with_json(
        db, storage.load_json_history(str(current), str(history)))
    assert storage.reconcile_from_json(db, str(current), str(history)) is False


def test_reconcile_uses_current_json_before_archives_for_duplicate_ids(tmp_path):
    current = tmp_path / "chests.json"
    history = tmp_path / "history"
    history.mkdir()
    current_record = _cd("same")
    current.write_text(json.dumps({"chests": [current_record]}), encoding="utf-8")
    (history / "report_1.json").write_text(
        json.dumps({"chests": [{**_cd("same"), "timestamp": "archive"}, _cd("archived")]}),
        encoding="utf-8")
    db = str(tmp_path / "chests.db")

    assert storage.reconcile_from_json(db, str(current), str(history)) is True
    rows = storage.load_all(db)
    assert len(rows) == 2
    assert next(row for row in rows if row["id"] == "same") == current_record


def test_reconcile_refuses_corrupt_archive_without_replacing_sqlite(tmp_path):
    import pytest

    current = tmp_path / "chests.json"
    history = tmp_path / "history"
    history.mkdir()
    current.write_text(json.dumps({"chests": [_cd("current")]}), encoding="utf-8")
    (history / "report_bad.json").write_text("{broken", encoding="utf-8")
    db = str(tmp_path / "chests.db")
    storage.init_db(db)
    assert storage.add_chest(db, _cd("preserved"))
    before = storage.load_all(db)

    with pytest.raises(json.JSONDecodeError):
        storage.reconcile_from_json(db, str(current), str(history))
    assert storage.load_all(db) == before


def test_reconcile_repairs_corrupt_sqlite_from_json(tmp_path):
    current = tmp_path / "chests.json"
    history = tmp_path / "history"
    history.mkdir()
    current.write_text(json.dumps({"chests": [_cd("recover")]}), encoding="utf-8")
    db = tmp_path / "chests.db"
    db.write_bytes(b"corrupt sqlite")

    assert storage.reconcile_from_json(str(db), str(current), str(history)) is True
    assert [row["id"] for row in storage.load_all(str(db))] == ["recover"]



def test_stats_ignores_malformed_quantities_and_keeps_item_presence(tmp_path):
    db = str(tmp_path / "chests.db")
    storage.init_db(db)
    chest = {
        "id": "malformed-counts",
        "slots": [
            {"item": "unknown-count", "quantity": None},
            {"item": "float-count", "quantity": 1.5},
            {"item": "bool-count", "quantity": True},
            {"item": "negative-count", "quantity": -2},
        ],
        "totals": {
            "known": 4, "unknown-count": None, "float-count": 1.5,
            "bool-count": True, "negative-count": -2,
        },
    }
    assert storage.add_chest(db, chest)
    result = storage.stats(db)
    assert result["chests"] == 1
    assert result["items_total"] == 4
    assert result["unique_items"] == 5
    assert result["top_qty"] == [("known", 4)]
    assert dict(result["top_freq"]) == {
        "unknown-count": 1, "float-count": 1, "bool-count": 1,
        "negative-count": 1, "known": 1,
    }


def test_stats_top_lists_have_deterministic_ties_and_nonnegative_limit(tmp_path):
    db = str(tmp_path / "chests.db")
    storage.sync_from_json(db, [
        {"id": "b", "totals": {"zeta": 2, "alpha": 2}, "slots": []},
    ])
    result = storage.stats(db, top_n=2)
    assert result["top_qty"] == [("alpha", 2), ("zeta", 2)]
    assert storage.stats(db, top_n=-1)["top_qty"] == []


def test_sync_from_json_rejects_non_list_without_replacing_existing_cache(tmp_path):
    import pytest

    db = str(tmp_path / "chests.db")
    storage.init_db(db)
    assert storage.add_chest(db, _cd("preserve"))
    before = storage.load_all(db)

    with pytest.raises(ValueError, match="complete JSON record list"):
        storage.sync_from_json(db, None)

    assert storage.load_all(db) == before
    assert not list(tmp_path.glob(".chests-rebuild-*.db"))


def test_sync_from_json_restores_corrupt_original_if_publish_fails(tmp_path, monkeypatch):
    db = str(tmp_path / "chests.db")
    corrupt_bytes = b"preserve this corrupt cache for recovery"
    with open(db, "wb") as stream:
        stream.write(corrupt_bytes)

    real_replace = storage.os.replace
    failed = False

    def fail_publish_once(source, destination):
        nonlocal failed
        if destination == db and os.path.basename(source).startswith(".chests-rebuild-") and not failed:
            failed = True
            raise PermissionError("simulated publish lock")
        return real_replace(source, destination)

    monkeypatch.setattr(storage.os, "replace", fail_publish_once)
    import pytest
    with pytest.raises(PermissionError, match="publish lock"):
        storage.sync_from_json(db, [_cd("rebuilt")])

    assert failed
    assert open(db, "rb").read() == corrupt_bytes
    assert not list(tmp_path.glob("chests.db.corrupt-*"))
    assert not list(tmp_path.glob(".chests-rebuild-*.db"))
