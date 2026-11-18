"""Profile isolation and repeated-switch regression tests."""
import json
import os
from pathlib import Path

import pytest

from chest_scanner import config as config_module
from chest_scanner import paths
from chest_scanner.commons import save_json
from chest_scanner.scanner.reader import ChestReader
from chest_scanner.template_cache import template_cache


def _isolate_profile_paths(monkeypatch):
    for name in (
        "PROFILE_DIR", "CONFIG_FILE", "ITEMS_FILE", "RESULTS_DIR",
        "UNKNOWN_DIR", "UNKNOWN_QUEUE_FILE", "ITEM_IMAGES_DIR",
        "RESULTS_FILE", "RESULTS_DB", "HISTORY_DIR",
        "DISCORD_IDS_FILE", "DISCORD_WEBHOOK_FILE",
    ):
        monkeypatch.setattr(paths, name, getattr(paths, name))


def _profile_config(title):
    cfg = json.loads(json.dumps(paths.DEFAULT_CONFIG))
    cfg["game_window_title"] = title
    return cfg



def test_legacy_flat_install_without_registry_keeps_default_profile_data(
        tmp_path, monkeypatch):
    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(paths, "PROFILES_FILE", str(tmp_path / "profiles.json"))
    monkeypatch.setattr(config_module, "ensure_directories", lambda: None)
    (tmp_path / "config.json").write_text(
        json.dumps(_profile_config("legacy-flat")), encoding="utf-8")
    (tmp_path / "items.json").write_text(
        json.dumps({"items": [{"name": "legacy-item"}]}), encoding="utf-8")

    # Before profile support, the active profile lived in the app root.
    assert not (tmp_path / "profiles.json").exists()
    registry = config_module.load_profiles()
    assert registry["version"] == 1
    assert registry["active"] == "Default"
    assert registry["profiles"] == [{"name": "Default", "directory": "."}]

    config_module.activate_profile("Default")
    assert Path(paths.PROFILE_DIR) == tmp_path
    assert json.loads(Path(paths.CONFIG_FILE).read_text(
        encoding="utf-8"))["game_window_title"] == "legacy-flat"
    assert json.loads(Path(paths.ITEMS_FILE).read_text(
        encoding="utf-8"))["items"][0]["name"] == "legacy-item"


def test_legacy_v1_profile_registry_keeps_existing_profile_directories(
        tmp_path, monkeypatch):
    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(paths, "PROFILES_FILE", str(tmp_path / "profiles.json"))
    monkeypatch.setattr(config_module, "ensure_directories", lambda: None)
    folder = tmp_path / "profiles" / "OldProfile"
    folder.mkdir(parents=True)
    (folder / "config.json").write_text(
        json.dumps(_profile_config("legacy-v1")), encoding="utf-8")
    (folder / "items.json").write_text(
        json.dumps({"items": [{"name": "old-item"}]}), encoding="utf-8")
    (tmp_path / "profiles.json").write_text(json.dumps({
        "version": 1,
        "active": "OldProfile",
        "profiles": [
            {"name": "Default", "directory": "."},
            {"name": "OldProfile", "directory": "profiles/OldProfile"},
        ],
    }), encoding="utf-8")

    registry = config_module.load_profiles()
    assert registry["active"] == "OldProfile"
    assert config_module.profile_entry("OldProfile") == {
        "name": "OldProfile", "directory": "profiles/OldProfile"}
    config_module.activate_profile("OldProfile")
    assert Path(paths.PROFILE_DIR) == folder
    assert json.loads(Path(paths.CONFIG_FILE).read_text(
        encoding="utf-8"))["game_window_title"] == "legacy-v1"
    assert json.loads(Path(paths.ITEMS_FILE).read_text(
        encoding="utf-8"))["items"][0]["name"] == "old-item"


@pytest.mark.parametrize("raw", [
    "{broken json",
    "[]",
    "null",
    '{"version": 1}',
    '{"version": 1, "active": "Default", "profiles": "Default"}',
    '{"version": 1, "active": "Default", "profiles": []}',
    '{"version": 1, "active": "Default", "profiles": [null]}',
    '{"version": 1, "active": "Default", "profiles": [{"directory": "."}]}',
    '{"version": 1, "active": "Default", "profiles": [{"name": "Default", "directory": 7}]}',
    '{"version": 2, "active": "Default", "profiles": [{"name": "Default", "directory": "."}]}',
    '{"version": 1, "active": "Missing", "profiles": [{"name": "Default", "directory": "."}]}',
    '{"version": 1, "active": "Default", "profiles": [{"name": "Default", "directory": "."}, {"name": "default", "directory": "profiles/Other"}]}',
    '{"version": 1, "active": "Default", "profiles": [{"name": "Default", "directory": "."}, {"name": "Other", "directory": "./"}]}',
    '{"version": 1, "active": "Outside", "profiles": [{"name": "Outside", "directory": "../outside"}]}',
    '{"version": 1, "active": "Outside", "profiles": [{"name": "Outside", "directory": "C:/outside"}]}',
    '{"version": 1, "active": "Outside", "profiles": [{"name": "Outside", "directory": "\\\\server\\share"}]}',
])
def test_invalid_profile_registry_is_rejected_without_modifying_source(
        tmp_path, monkeypatch, raw):
    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    registry = tmp_path / "profiles.json"
    monkeypatch.setattr(paths, "PROFILES_FILE", str(registry))
    registry.write_text(raw, encoding="utf-8")
    original = registry.read_bytes()

    with pytest.raises(ValueError):
        config_module.load_profiles()

    assert registry.read_bytes() == original
    assert list(tmp_path.iterdir()) == [registry]
    assert not (tmp_path.parent / "outside").exists()


def test_valid_default_and_nested_profile_registry_is_accepted(tmp_path, monkeypatch):
    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    registry = tmp_path / "profiles.json"
    monkeypatch.setattr(paths, "PROFILES_FILE", str(registry))
    data = {
        "version": 1, "active": "Nested",
        "profiles": [
            {"name": "Default", "directory": "."},
            {"name": "Nested", "directory": "profiles/Nested"},
        ],
    }
    registry.write_text(json.dumps(data), encoding="utf-8")

    assert config_module.load_profiles() == data
    assert registry.read_text(encoding="utf-8") == json.dumps(data)



def test_save_profiles_rejects_invalid_registry_without_overwriting_source(
        tmp_path, monkeypatch):
    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    registry = tmp_path / "profiles.json"
    monkeypatch.setattr(paths, "PROFILES_FILE", str(registry))
    original = b'{"version": 1, "active": "Default", "profiles": []}'
    registry.write_bytes(original)

    with pytest.raises(ValueError):
        config_module.save_profiles({
            "version": 1,
            "active": "Outside",
            "profiles": [{"name": "Outside", "directory": "../outside"}],
        })

    assert registry.read_bytes() == original
    assert list(tmp_path.iterdir()) == [registry]
    assert not (tmp_path.parent / "outside").exists()


def test_profile_registry_rejects_parent_symlink_escape(tmp_path, monkeypatch):
    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    registry = tmp_path / "profiles.json"
    monkeypatch.setattr(paths, "PROFILES_FILE", str(registry))
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    link = tmp_path / "profiles"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")
    registry.write_text(json.dumps({
        "version": 1,
        "active": "Escaped",
        "profiles": [{"name": "Escaped", "directory": "profiles/child"}],
    }), encoding="utf-8")
    original = registry.read_bytes()

    with pytest.raises(ValueError, match="??????? ?? ???????"):
        config_module.load_profiles()

    assert registry.read_bytes() == original
    assert list(outside.iterdir()) == []
    assert link.is_symlink()

def test_missing_profile_registry_still_creates_legacy_default(tmp_path, monkeypatch):
    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    registry = tmp_path / "profiles.json"
    monkeypatch.setattr(paths, "PROFILES_FILE", str(registry))

    data = config_module.load_profiles()

    assert data["active"] == "Default"
    assert data["profiles"] == [{"name": "Default", "directory": "."}]
    assert json.loads(registry.read_text(encoding="utf-8")) == data

def test_profile_switch_round_trip_keeps_config_items_and_queue_separate(
        tmp_path, monkeypatch):
    registry = tmp_path / "profiles.json"
    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(paths, "PROFILES_FILE", str(registry))
    monkeypatch.setattr(config_module, "ensure_directories", lambda: None)
    registry.write_text(json.dumps({
        "version": 1, "active": "A",
        "profiles": [
            {"name": "A", "directory": "profiles/A"},
            {"name": "B", "directory": "profiles/B"},
        ],
    }), encoding="utf-8")

    for name, marker in (("A", "alpha"), ("B", "bravo")):
        (tmp_path / "profiles" / name).mkdir(parents=True)
        config_module.activate_profile(name)
        assert Path(paths.UNKNOWN_QUEUE_FILE) == (
            tmp_path / "profiles" / name / "unknown_items.json")
        (tmp_path / "profiles" / name / "config.json").write_text(
            json.dumps(_profile_config(marker)), encoding="utf-8")
        (tmp_path / "profiles" / name / "items.json").write_text(
            json.dumps({"items": [{"name": marker, "stack_size": 16}]}),
            encoding="utf-8")
        (tmp_path / "profiles" / name / "unknown_items.json").write_text(
            json.dumps({"items": [{"id": marker}]}), encoding="utf-8")

    for name, marker in (("A", "alpha"), ("B", "bravo"), ("A", "alpha"),
                         ("B", "bravo"), ("A", "alpha")):
        config_module.activate_profile(name)
        assert json.loads(Path(paths.CONFIG_FILE).read_text(encoding="utf-8"))[
            "game_window_title"] == marker
        assert json.loads(Path(paths.ITEMS_FILE).read_text(encoding="utf-8"))[
            "items"][0]["name"] == marker
        assert json.loads(Path(paths.UNKNOWN_QUEUE_FILE).read_text(encoding="utf-8"))[
            "items"][0]["id"] == marker



@pytest.mark.parametrize("failure_stage", ["ensure_directories", "save_profiles"])
def test_failed_profile_switch_restores_all_runtime_paths_and_active_registry(
        tmp_path, monkeypatch, failure_stage):
    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(paths, "PROFILES_FILE", str(tmp_path / "profiles.json"))
    registry = tmp_path / "profiles.json"
    registry.write_text(json.dumps({
        "version": 1, "active": "A",
        "profiles": [
            {"name": "A", "directory": "profiles/A"},
            {"name": "B", "directory": "profiles/B"},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(config_module, "ensure_directories", lambda: None)
    (tmp_path / "profiles" / "A").mkdir(parents=True)
    config_module.activate_profile("A")
    (tmp_path / "profiles" / "A" / "config.json").write_text(
        json.dumps(_profile_config("A-data")), encoding="utf-8")
    (tmp_path / "profiles" / "A" / "items.json").write_text(
        '{"items":[{"name":"A-item"}]}', encoding="utf-8")
    (tmp_path / "profiles" / "B").mkdir(parents=True)
    (tmp_path / "profiles" / "B" / "config.json").write_text(
        json.dumps(_profile_config("B-data")), encoding="utf-8")
    (tmp_path / "profiles" / "B" / "items.json").write_text(
        '{"items":[{"name":"B-item"}]}', encoding="utf-8")
    previous_paths = {
        key: getattr(paths, key) for key in (
            "PROFILE_DIR", "CONFIG_FILE", "ITEMS_FILE", "RESULTS_DIR",
            "UNKNOWN_DIR", "UNKNOWN_QUEUE_FILE", "ITEM_IMAGES_DIR",
            "RESULTS_FILE", "RESULTS_DB", "HISTORY_DIR",
            "DISCORD_IDS_FILE", "DISCORD_WEBHOOK_FILE")
    }

    def fail_operation():
        raise OSError("simulated directory setup failure")

    def fail_save(_data):
        raise OSError("simulated registry publication failure")

    if failure_stage == "ensure_directories":
        monkeypatch.setattr(config_module, "ensure_directories", fail_operation)
        expected_error = "directory setup"
    else:
        monkeypatch.setattr(config_module, "save_profiles", fail_save)
        expected_error = "registry publication"

    with pytest.raises(OSError, match=expected_error):
        config_module.activate_profile("B")

    assert {key: getattr(paths, key) for key in previous_paths} == previous_paths
    assert json.loads(registry.read_text(encoding="utf-8"))["active"] == "A"
    assert json.loads((tmp_path / "profiles" / "A" / "config.json").read_text(
        encoding="utf-8"))["game_window_title"] == "A-data"
    assert json.loads((tmp_path / "profiles" / "A" / "items.json").read_text(
        encoding="utf-8"))["items"][0]["name"] == "A-item"
    assert json.loads((tmp_path / "profiles" / "B" / "config.json").read_text(
        encoding="utf-8"))["game_window_title"] == "B-data"
    assert json.loads((tmp_path / "profiles" / "B" / "items.json").read_text(
        encoding="utf-8"))["items"][0]["name"] == "B-item"


def test_activate_missing_registered_profile_does_not_recreate_directory(
        tmp_path, monkeypatch):
    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    registry = tmp_path / "profiles.json"
    monkeypatch.setattr(paths, "PROFILES_FILE", str(registry))
    registry.write_text(json.dumps({
        "version": 1, "active": "Missing",
        "profiles": [
            {"name": "Default", "directory": "."},
            {"name": "Missing", "directory": "profiles/Missing"},
        ],
    }), encoding="utf-8")
    orphan = tmp_path / "profiles" / "Unregistered"
    orphan.mkdir(parents=True)
    marker = orphan / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")
    original_registry = registry.read_bytes()

    with pytest.raises(FileNotFoundError, match="Profile directory"):
        config_module.activate_profile("Missing")

    assert not (tmp_path / "profiles" / "Missing").exists()
    assert marker.read_text(encoding="utf-8") == "preserve"
    assert registry.read_bytes() == original_registry


def test_reader_startup_does_not_recreate_missing_active_profile(
        tmp_path, monkeypatch):
    from chest_scanner.scanner import base as base_module

    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    registry = tmp_path / "profiles.json"
    monkeypatch.setattr(paths, "PROFILES_FILE", str(registry))
    registry.write_text(json.dumps({
        "version": 1, "active": "Missing",
        "profiles": [
            {"name": "Default", "directory": "."},
            {"name": "Missing", "directory": "profiles/Missing"},
        ],
    }), encoding="utf-8")
    original_registry = registry.read_bytes()
    ensure_called = []
    monkeypatch.setattr(base_module, "ensure_directories",
                        lambda: ensure_called.append(True))

    with pytest.raises(FileNotFoundError, match="Profile directory"):
        ChestReader()

    assert not (tmp_path / "profiles" / "Missing").exists()
    assert registry.read_bytes() == original_registry
    assert not ensure_called


def test_reader_startup_restores_active_profile_capture_and_clears_template_cache(
        tmp_path, monkeypatch):
    from chest_scanner import screenshot
    from chest_scanner.scanner import base as base_module

    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(paths, "PROFILES_FILE", str(tmp_path / "profiles.json"))
    monkeypatch.setattr(base_module, "ensure_directories", lambda: None)
    monkeypatch.setattr(base_module.storage, "init_db", lambda _path: None)
    monkeypatch.setattr(base_module.storage, "reconcile_from_json",
                        lambda *_args: False)
    monkeypatch.setattr(ChestReader, "read_image", lambda self, _path: None)
    monkeypatch.setattr(ChestReader, "load_digit_templates", lambda self: [])
    monkeypatch.setattr(ChestReader, "print_startup_status", lambda self: None)

    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles.json").write_text(json.dumps({
        "version": 1, "active": "B",
        "profiles": [
            {"name": "A", "directory": "profiles/A"},
            {"name": "B", "directory": "profiles/B"},
        ],
    }), encoding="utf-8")
    for name in ("A", "B"):
        folder = tmp_path / "profiles" / name
        folder.mkdir(parents=True)
        cfg = _profile_config(name)
        cfg["capture_source"] = "window"
        (folder / "config.json").write_text(json.dumps(cfg),
                                             encoding="utf-8")
        (folder / "items.json").write_text(json.dumps({
            "items": [{"name": f"item-{name}", "stack_size": 16}]
        }), encoding="utf-8")

    original_capture = screenshot.capture_settings()
    template_cache.invalidate()
    template_cache._cache["items/shared.png"] = object()
    template_cache._mtime["items/shared.png"] = 1
    try:
        reader = ChestReader()

        assert config_module.load_profiles()["active"] == "B"
        assert Path(paths.PROFILE_DIR) == tmp_path / "profiles" / "B"
        assert reader.config["game_window_title"] == "B"
        assert reader.items_db["items"][0]["name"] == "item-B"
        assert screenshot.capture_settings() == {
            "source": "window", "title": "B"}
        assert "items/shared.png" not in template_cache._cache
        assert "items/shared.png" not in template_cache._mtime
    finally:
        screenshot.configure_capture(original_capture["source"],
                                     original_capture["title"])
        template_cache.invalidate()


def test_reader_startup_continues_when_sqlite_cache_is_locked(
        tmp_path, monkeypatch):
    import sqlite3

    from chest_scanner.scanner import base as base_module

    _prepare_reader_startup_for_config_recovery(tmp_path, monkeypatch,
                                                base_module)

    def locked_database(_path):
        raise sqlite3.OperationalError("database is locked")

    def unexpected_reconcile(*_args):
        raise AssertionError("reconciliation must be skipped when init_db fails")

    monkeypatch.setattr(base_module.storage, "init_db", locked_database)
    monkeypatch.setattr(base_module.storage, "reconcile_from_json",
                        unexpected_reconcile)

    reader = ChestReader()

    assert reader.config["game_window_title"] == "Minecraft"


def _prepare_reader_startup_for_config_recovery(tmp_path, monkeypatch,
                                                  base_module):
    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(paths, "PROFILES_FILE", str(tmp_path / "profiles.json"))
    monkeypatch.setattr(base_module, "ensure_directories", lambda: None)
    monkeypatch.setattr(base_module.storage, "init_db", lambda _path: None)
    monkeypatch.setattr(base_module.storage, "reconcile_from_json",
                        lambda *_args: False)
    monkeypatch.setattr(ChestReader, "read_image", lambda self, _path: None)
    monkeypatch.setattr(ChestReader, "load_digit_templates", lambda self: [])
    monkeypatch.setattr(ChestReader, "print_startup_status", lambda self: None)
    folder = tmp_path / "profiles" / "Default"
    folder.mkdir(parents=True)
    (tmp_path / "profiles.json").write_text(json.dumps({
        "version": 1, "active": "Default",
        "profiles": [{"name": "Default", "directory": "profiles/Default"}],
    }), encoding="utf-8")
    (folder / "items.json").write_text(json.dumps({"items": []}),
                                         encoding="utf-8")
    return folder


def test_reader_startup_fails_closed_and_preserves_corrupt_config(
        tmp_path, monkeypatch):
    from chest_scanner.scanner import base as base_module

    folder = _prepare_reader_startup_for_config_recovery(
        tmp_path, monkeypatch, base_module)
    config_path = folder / "config.json"
    config_path.write_text('{"capture_source": ', encoding="utf-8")
    damaged_bytes = config_path.read_bytes()

    with pytest.raises(ValueError, match="config.json"):
        ChestReader()

    assert not config_path.exists()
    quarantined = list(folder.glob("config.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == damaged_bytes
    assert json.loads((folder / "items.json").read_text(
        encoding="utf-8")) == {"items": []}


def test_reader_startup_repairs_duplicate_item_ids_and_preserves_backup(
        tmp_path, monkeypatch):
    from chest_scanner.scanner import base as base_module

    folder = _prepare_reader_startup_for_config_recovery(
        tmp_path, monkeypatch, base_module)
    items_path = folder / "items.json"
    original = {"version": 3, "items": [
        {"id": "duplicate", "name": "First", "stack_size": 64},
        {"id": "duplicate", "name": "Second", "stack_size": 16},
        {"id": "kept", "name": "Third", "stack_size": 1},
        None,
    ]}
    items_path.write_text(json.dumps(original), encoding="utf-8")

    reader = ChestReader()

    items = reader.items_db["items"]
    assert [item["name"] for item in items] == ["First", "Second", "Third"]
    assert items[0]["id"] == "duplicate"
    assert items[1]["id"] != "duplicate"
    assert items[2]["id"] == "kept"
    assert len({item["id"] for item in items}) == 3
    assert [item["stack_size"] for item in items] == [64, 16, 1]
    assert json.loads(items_path.read_text(encoding="utf-8")) == reader.items_db
    assert json.loads((folder / "items.json.backup").read_text(
        encoding="utf-8")) == original



def test_reader_startup_recovers_corrupt_config_from_valid_backup(
        tmp_path, monkeypatch):
    from chest_scanner.scanner import base as base_module

    folder = _prepare_reader_startup_for_config_recovery(
        tmp_path, monkeypatch, base_module)
    config_path = folder / "config.json"
    recovered_config = _profile_config("Recovered profile")
    (folder / "config.json.backup").write_text(
        json.dumps(recovered_config), encoding="utf-8")
    config_path.write_text('{"capture_source": ', encoding="utf-8")

    reader = ChestReader()

    assert reader.config["game_window_title"] == "Recovered profile"
    assert json.loads(config_path.read_text(encoding="utf-8"))[
        "game_window_title"] == "Recovered profile"
    assert list(folder.glob("config.json.corrupt-*"))
    assert json.loads((folder / "config.json.backup").read_text(
        encoding="utf-8"))["game_window_title"] == "Recovered profile"


def test_reader_reload_profile_reloads_profile_specific_items_and_config(
        tmp_path, monkeypatch):
    from chest_scanner import screenshot

    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(paths, "PROFILES_FILE", str(tmp_path / "profiles.json"))
    monkeypatch.setattr(config_module, "ensure_directories", lambda: None)
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles.json").write_text(json.dumps({
        "version": 1, "active": "A",
        "profiles": [
            {"name": "A", "directory": "profiles/A"},
            {"name": "B", "directory": "profiles/B"},
        ],
    }), encoding="utf-8")

    for name in ("A", "B"):
        folder = tmp_path / "profiles" / name
        folder.mkdir(parents=True)
        (folder / "config.json").write_text(json.dumps(_profile_config(name)),
                                             encoding="utf-8")
        (folder / "items.json").write_text(json.dumps({
            "items": [{"name": f"item-{name}", "stack_size": 16}]
        }), encoding="utf-8")

    reader = ChestReader.__new__(ChestReader)
    reader.template_cache = template_cache
    reader.prompted_unknowns = set()
    reader._detect_cache = {}
    monkeypatch.setattr(ChestReader, "read_image", lambda self, _path: None)
    monkeypatch.setattr(ChestReader, "load_digit_templates", lambda self: [])
    monkeypatch.setattr(ChestReader, "print_startup_status", lambda self: None)
    monkeypatch.setattr(template_cache, "get_raw", lambda _path: None)
    original_capture = screenshot.capture_settings()
    try:
        for name in ("A", "B", "A", "B", "A"):
            config_module.activate_profile(name)
            reader.reload_profile()
            assert reader.config["game_window_title"] == name
            assert reader.items_db["items"][0]["name"] == f"item-{name}"
            assert reader.get_item_stack_size(f"item-{name}") == 16
            assert screenshot.capture_settings()["title"] == name
    finally:
        screenshot.configure_capture(original_capture["source"],
                                     original_capture["title"])
        template_cache.invalidate()



def test_failed_profile_reload_rolls_back_paths_registry_and_reader_state(
        tmp_path, monkeypatch):
    from chest_scanner import screenshot

    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(paths, "PROFILES_FILE", str(tmp_path / "profiles.json"))
    monkeypatch.setattr(config_module, "ensure_directories", lambda: None)
    (tmp_path / "profiles.json").write_text(json.dumps({
        "version": 1, "active": "A",
        "profiles": [
            {"name": "A", "directory": "profiles/A"},
            {"name": "B", "directory": "profiles/B"},
        ],
    }), encoding="utf-8")
    for name in ("A", "B"):
        folder = tmp_path / "profiles" / name
        folder.mkdir(parents=True)
        (folder / "config.json").write_text(
            json.dumps(_profile_config(name)), encoding="utf-8")
        (folder / "items.json").write_text(json.dumps({
            "items": [{"name": f"item-{name}", "stack_size": 16}]
        }), encoding="utf-8")

    config_module.activate_profile("A")
    reader = ChestReader.__new__(ChestReader)
    reader.template_cache = template_cache
    reader.config = {"game_window_title": "A"}
    reader.items_db = {"items": [{"name": "item-A"}]}
    reader.ref_w, reader.ref_h = 1920, 1080
    reader.grid = {"columns": 9}
    reader.chest_template = "old-chest"
    reader.empty_slot_template = "old-empty"
    reader.digit_templates = ["old-digit"]
    reader.last_chest_hash = "old-hash"
    reader.chest_was_open = True
    reader.prompted_unknowns = {"old-prompt"}
    reader._last_chest_pos = (1, 2)
    reader._stack_cache = {"item-A": 16}
    reader._phash_entries = [{"name": "item-A"}]
    reader._detect_cache = {"old": "cache"}
    original_paths = {field: getattr(paths, field) for field in (
        "PROFILE_DIR", "CONFIG_FILE", "ITEMS_FILE", "RESULTS_DIR",
        "UNKNOWN_DIR", "UNKNOWN_QUEUE_FILE", "ITEM_IMAGES_DIR",
        "RESULTS_FILE", "RESULTS_DB", "HISTORY_DIR",
        "DISCORD_IDS_FILE", "DISCORD_WEBHOOK_FILE",
    )}
    original_capture = screenshot.capture_settings()

    def fail_after_partial_reload():
        reader.config = {"game_window_title": "BROKEN"}
        reader.items_db = {"items": [{"name": "broken"}]}
        reader.prompted_unknowns.clear()
        reader._detect_cache.clear()
        raise OSError("simulated profile file read failure")

    monkeypatch.setattr(reader, "reload_profile", fail_after_partial_reload)
    try:
        import pytest
        with pytest.raises(OSError, match="simulated"):
            reader.switch_profile("B")

        assert load_active_profile_name() == "A"
        for field, value in original_paths.items():
            assert getattr(paths, field) == value
        assert reader.config == {"game_window_title": "A"}
        assert reader.items_db["items"][0]["name"] == "item-A"
        assert reader.prompted_unknowns == {"old-prompt"}
        assert reader._detect_cache == {"old": "cache"}
        assert reader._stack_cache == {"item-A": 16}
        assert screenshot.capture_settings() == original_capture
    finally:
        screenshot.configure_capture(original_capture["source"],
                                     original_capture["title"])
        template_cache.invalidate()


def load_active_profile_name():
    return config_module.load_profiles().get("active")


def _prepare_profile_registry(tmp_path, monkeypatch):
    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(paths, "PROFILES_FILE", str(tmp_path / "profiles.json"))
    data = {
        "version": 1,
        "active": "Default",
        "profiles": [
            {"name": "Default", "directory": "."},
            {"name": "Alpha", "directory": "profiles/Alpha"},
        ],
    }
    Path(paths.PROFILES_FILE).write_text(json.dumps(data), encoding="utf-8")
    folder = tmp_path / "profiles" / "Alpha"
    folder.mkdir(parents=True)
    monkeypatch.setattr(paths, "PROFILE_DIR", str(folder))
    (folder / "items.json").write_text('{"items":[{"name":"valuable"}]}',
                                       encoding="utf-8")
    return folder


def _replace_profiles_parent_with_junction(tmp_path):
    import subprocess

    if not hasattr(os.path, "isjunction"):
        pytest.skip("junction detection is unavailable")
    profiles = tmp_path / "profiles"
    redirected = tmp_path / "redirected-profiles"
    profiles.rename(redirected)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(profiles), str(redirected)],
        capture_output=True, text=True, check=False)
    if result.returncode != 0 or not os.path.isjunction(profiles):
        pytest.skip(f"directory junctions are unavailable: {result.stderr}")
    return profiles, redirected


def test_rename_rechecks_parent_junction_immediately_before_move(
        tmp_path, monkeypatch):
    folder = _prepare_profile_registry(tmp_path, monkeypatch)
    original_write = config_module._write_profile_operation_journal

    def write_then_redirect(record):
        original_write(record)
        if record.get("operation") == "rename" and record.get("phase") == "prepared":
            _replace_profiles_parent_with_junction(tmp_path)

    monkeypatch.setattr(config_module, "_write_profile_operation_journal",
                        write_then_redirect)
    with pytest.raises(ValueError):
        config_module.rename_profile_data("Alpha", "Renamed")

    assert folder.is_dir()
    assert (folder / "items.json").is_file()
    assert not (tmp_path / "redirected-profiles" / "Renamed").exists()
    registry = json.loads(Path(paths.PROFILES_FILE).read_text(encoding="utf-8"))
    assert any(item["name"] == "Alpha" for item in registry["profiles"])


def test_delete_rechecks_parent_junction_immediately_before_move(
        tmp_path, monkeypatch):
    folder = _prepare_profile_registry(tmp_path, monkeypatch)
    original_write = config_module._write_profile_operation_journal

    def write_then_redirect(record):
        original_write(record)
        if record.get("operation") == "delete" and record.get("phase") == "prepared":
            _replace_profiles_parent_with_junction(tmp_path)

    monkeypatch.setattr(config_module, "_write_profile_operation_journal",
                        write_then_redirect)
    with pytest.raises(ValueError):
        config_module.delete_profile_data("Alpha")

    assert folder.is_dir()
    assert (folder / "items.json").is_file()
    registry = json.loads(Path(paths.PROFILES_FILE).read_text(encoding="utf-8"))
    assert any(item["name"] == "Alpha" for item in registry["profiles"])
    assert (tmp_path / ".profile-operation.json").is_file()


def test_rename_profile_moves_data_and_updates_registry_atomically(
        tmp_path, monkeypatch):
    folder = _prepare_profile_registry(tmp_path, monkeypatch)
    result = config_module.rename_profile_data("Alpha", "Renamed")
    new_folder = tmp_path / "profiles" / "Renamed"
    assert not folder.exists()
    assert (new_folder / "items.json").read_text(encoding="utf-8") == (
        '{"items":[{"name":"valuable"}]}')
    data = config_module.load_profiles()
    entry = next(item for item in data["profiles"]
                 if item["name"] == "Renamed")
    assert entry["directory"] == "profiles/Renamed"
    assert result["new_dir"] == str(new_folder)



def test_rename_profile_keeps_all_photo_references_resolvable(
        tmp_path, monkeypatch):
    from chest_scanner import commons

    folder = _prepare_profile_registry(tmp_path, monkeypatch)
    first = "items/nested folder/\u0410\u043b\u043c\u0430\u0437 1.png"
    second = "items/\u0420\u0435\u0434\u043a\u043e\u0435.png"
    first_bytes = b"primary-photo"
    second_bytes = b"secondary-photo"
    for relative, content in ((first, first_bytes), (second, second_bytes)):
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (folder / "items.json").write_text(json.dumps({
        "version": 3,
        "items": [{"name": "\u0410\u043b\u043c\u0430\u0437",
                   "images": [first, second], "image": first}],
    }, ensure_ascii=False), encoding="utf-8")

    result = config_module.rename_profile_data("Alpha", "Renamed")
    new_folder = tmp_path / "profiles" / "Renamed"
    imported = json.loads((new_folder / "items.json").read_text(encoding="utf-8"))
    item = imported["items"][0]

    assert result["new_dir"] == str(new_folder)
    assert item["images"] == [first, second]
    assert item["image"] == first
    assert (new_folder / first).read_bytes() == first_bytes
    assert (new_folder / second).read_bytes() == second_bytes
    monkeypatch.setattr(paths, "PROFILE_DIR", str(new_folder))
    for relative in item["images"]:
        assert Path(commons.absolute_path(relative)) == new_folder / relative


def test_create_profile_copy_keeps_photo_references_and_primary_order(
        tmp_path, monkeypatch):
    source = _prepare_profile_registry(tmp_path, monkeypatch)
    first = "items/nested/\u0424\u043e\u0442\u043e 1.png"
    second = "items/\u0424\u043e\u0442\u043e 2.png"
    for relative, content in ((first, b"one"), (second, b"two")):
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (source / "items.json").write_text(json.dumps({
        "items": [{"name": "Photo item", "images": [first, second],
                   "image": first}],
    }), encoding="utf-8")

    config_module.create_profile_data("Photo Copy")
    target = tmp_path / "profiles" / "Photo Copy"
    copied = json.loads((target / "items.json").read_text(encoding="utf-8"))
    item = copied["items"][0]

    assert item["images"] == [first, second]
    assert item["image"] == first
    assert (target / first).read_bytes() == b"one"
    assert (target / second).read_bytes() == b"two"


def test_rename_profile_registry_failure_restores_original_directory(
        tmp_path, monkeypatch):
    folder = _prepare_profile_registry(tmp_path, monkeypatch)

    def fail_save(_data):
        raise OSError("registry write failed")

    monkeypatch.setattr(config_module, "save_profiles", fail_save)
    import pytest
    with pytest.raises(OSError, match="registry write failed"):
        config_module.rename_profile_data("Alpha", "Renamed")
    assert folder.is_dir()
    assert (folder / "items.json").exists()
    assert not (tmp_path / "profiles" / "Renamed").exists()


def test_delete_profile_registry_failure_restores_profile_files(
        tmp_path, monkeypatch):
    folder = _prepare_profile_registry(tmp_path, monkeypatch)

    def fail_save(_data):
        raise OSError("registry write failed")

    monkeypatch.setattr(config_module, "save_profiles", fail_save)
    import pytest
    with pytest.raises(OSError, match="registry write failed"):
        config_module.delete_profile_data("Alpha")
    assert folder.is_dir()
    assert (folder / "items.json").exists()
    assert any(item["name"] == "Alpha"
               for item in config_module.load_profiles()["profiles"])


def test_delete_profile_removes_registry_then_data(tmp_path, monkeypatch):
    folder = _prepare_profile_registry(tmp_path, monkeypatch)
    leftover = config_module.delete_profile_data("Alpha")
    assert leftover is None
    assert not folder.exists()
    assert all(item["name"] != "Alpha"
               for item in config_module.load_profiles()["profiles"])


def test_profile_path_traversal_is_rejected_for_rename_and_delete(
        tmp_path, monkeypatch):
    _prepare_profile_registry(tmp_path, monkeypatch)
    data = config_module.load_profiles()
    next(item for item in data["profiles"]
         if item["name"] == "Alpha")["directory"] = "../outside"
    Path(paths.PROFILES_FILE).write_text(json.dumps(data), encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="Путь профиля"):
        config_module.rename_profile_data("Alpha", "Renamed")
    with pytest.raises(ValueError, match="Путь профиля"):
        config_module.delete_profile_data("Alpha")



def test_delete_cleanup_failure_retains_recoverable_profile_data(
        tmp_path, monkeypatch):
    folder = _prepare_profile_registry(tmp_path, monkeypatch)

    def fail_cleanup(_path):
        raise PermissionError("file is in use")

    monkeypatch.setattr(config_module.shutil, "rmtree", fail_cleanup)
    leftover = config_module.delete_profile_data("Alpha")
    assert leftover is not None
    leftover_path = Path(leftover)
    assert leftover_path.is_dir()
    assert (leftover_path / "items.json").exists()
    assert not folder.exists()
    assert all(item["name"] != "Alpha"
               for item in config_module.load_profiles()["profiles"])


def test_create_profile_copies_only_settings_items_and_images(
        tmp_path, monkeypatch):
    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("source")), encoding="utf-8")
    (source / "items").mkdir()
    (source / "items" / "diamond.png").write_bytes(b"image-bytes")
    (source / "results").mkdir()
    (source / "results" / "chests.json").write_text(
        '[{"id":"private-history"}]', encoding="utf-8")
    (source / "discord_webhook.json").write_text(
        '{"url":"private-webhook"}', encoding="utf-8")
    (source / "unknown_items.json").write_text(
        '{"items":["private-queue"]}', encoding="utf-8")

    entry = config_module.create_profile_data("New Profile")
    target = tmp_path / "profiles" / "New Profile"
    assert entry == {"name": "New Profile", "directory": "profiles/New Profile"}
    assert json.loads((target / "config.json").read_text(
        encoding="utf-8"))["game_window_title"] == "source"
    assert json.loads((target / "items.json").read_text(
        encoding="utf-8"))["items"][0]["name"] == "valuable"
    assert (target / "items" / "diamond.png").read_bytes() == b"image-bytes"
    assert not (target / "results").exists()
    assert not (target / "discord_webhook.json").exists()
    assert not (target / "unknown_items.json").exists()
    assert config_module.load_profiles()["active"] == "Default"


@pytest.mark.parametrize("link_kind", ["file", "directory", "items_root", "config"])
def test_create_profile_rejects_linked_copy_sources_without_copying_external_data(
        tmp_path, monkeypatch, link_kind):
    source = _prepare_profile_registry(tmp_path, monkeypatch)
    outside = tmp_path.parent / f"{tmp_path.name}-external-{link_kind}"
    outside.mkdir()
    external_file = outside / "secret.png"
    external_file.write_bytes(b"external-private-data")

    if link_kind == "file":
        (source / "items").mkdir()
        link = source / "items" / "external.png"
        link.write_bytes(b"link-placeholder")
    elif link_kind == "directory":
        (source / "items").mkdir()
        link = source / "items" / "external-dir"
        link.mkdir()
    elif link_kind == "items_root":
        link = source / "items"
        link.mkdir()
    else:
        link = source / "config.json"
        link.unlink(missing_ok=True)
        link.write_bytes(b"config-placeholder")

    real_islink = config_module.os.path.islink
    linked_path = os.path.normcase(os.fspath(link))

    def report_selected_link(path):
        if os.path.normcase(os.fspath(path)) == linked_path:
            return True
        return real_islink(path)

    monkeypatch.setattr(config_module.os.path, "islink", report_selected_link)
    with pytest.raises(ValueError, match="Unsafe profile copy source"):
        config_module.create_profile_data("Linked Source", source)

    assert external_file.read_bytes() == b"external-private-data"
    assert link.exists()
    assert not (tmp_path / "profiles" / "Linked Source").exists()
    assert not list((tmp_path / "profiles").glob(".profile-create-*"))



def test_create_profile_registry_failure_removes_staged_profile(
        tmp_path, monkeypatch):
    source = _prepare_profile_registry(tmp_path, monkeypatch)

    def fail_save(_data):
        raise OSError("registry write failed")

    monkeypatch.setattr(config_module, "save_profiles", fail_save)
    import pytest
    with pytest.raises(OSError, match="registry write failed"):
        config_module.create_profile_data("Failed")
    assert source.is_dir()
    assert not (tmp_path / "profiles" / "Failed").exists()
    assert not list((tmp_path / "profiles").glob(".profile-create-*"))


def test_create_profile_copy_failure_leaves_no_partial_directory(
        tmp_path, monkeypatch):
    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "items").mkdir()
    original_copytree = config_module.shutil.copytree

    def fail_copytree(*_args, **_kwargs):
        raise OSError("image copy failed")

    monkeypatch.setattr(config_module.shutil, "copytree", fail_copytree)
    import pytest
    with pytest.raises(OSError, match="image copy failed"):
        config_module.create_profile_data("Failed")
    assert not (tmp_path / "profiles" / "Failed").exists()
    assert not list((tmp_path / "profiles").glob(".profile-create-*"))
    monkeypatch.setattr(config_module.shutil, "copytree", original_copytree)


def test_create_profile_rejects_duplicate_names_and_existing_directories(
        tmp_path, monkeypatch):
    _prepare_profile_registry(tmp_path, monkeypatch)
    import pytest
    with pytest.raises(ValueError, match="уже существует"):
        config_module.create_profile_data("alpha")
    (tmp_path / "profiles" / "Orphan").mkdir()
    with pytest.raises(FileExistsError):
        config_module.create_profile_data("Orphan")



def test_create_profile_from_default_profile_directory(tmp_path, monkeypatch):
    _prepare_profile_registry(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, "PROFILE_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps(_profile_config("default")), encoding="utf-8")
    (tmp_path / "items.json").write_text(
        '{"items":[{"name":"default-item"}]}', encoding="utf-8")
    config_module.create_profile_data("From Default")
    target = tmp_path / "profiles" / "From Default"
    assert json.loads((target / "config.json").read_text(
        encoding="utf-8"))["game_window_title"] == "default"
    assert json.loads((target / "items.json").read_text(
        encoding="utf-8"))["items"][0]["name"] == "default-item"


def test_profile_archive_round_trip_excludes_private_data(
        tmp_path, monkeypatch):
    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("portable")), encoding="utf-8")
    (source / "items").mkdir()
    (source / "items" / "diamond.png").write_bytes(b"image-data")
    (source / "results").mkdir()
    (source / "results" / "chests.json").write_text(
        '[{"id":"private"}]', encoding="utf-8")
    (source / "discord_webhook.json").write_text(
        '{"url":"secret"}', encoding="utf-8")
    (source / "unknown_items.json").write_text(
        '{"items":["secret"]}', encoding="utf-8")

    archive = tmp_path / "portable.chestprofile"
    config_module.export_profile_archive("Alpha", archive)
    import zipfile
    with zipfile.ZipFile(archive) as zipped:
        names = set(zipped.namelist())
        assert names == {"manifest.json", "config.json", "items.json",
                         "items/diamond.png"}
    entry = config_module.import_profile_archive(archive, "Imported")
    target = tmp_path / "profiles" / "Imported"
    assert entry["name"] == "Imported"
    assert (target / "items" / "diamond.png").read_bytes() == b"image-data"
    assert config_module.load_profiles()["active"] == "Default"
    assert not (target / "results").exists()
    assert not (target / "discord_webhook.json").exists()
    assert not (target / "unknown_items.json").exists()



def test_portable_profile_archive_moves_photos_between_independent_installations(
        tmp_path, monkeypatch):
    """Portable profile images stay relative and resolve under the new install."""
    from chest_scanner import commons

    source_root = tmp_path / "old-install"
    source_profile = source_root / "profiles" / "Alpha"
    (source_profile / "items" / "nested folder").mkdir(parents=True)
    monkeypatch.setattr(paths, "BASE_DIR", str(source_root))
    monkeypatch.setattr(paths, "PROFILES_FILE", str(source_root / "profiles.json"))
    monkeypatch.setattr(paths, "PROFILE_DIR", str(source_profile))
    source_root.mkdir(parents=True, exist_ok=True)
    (source_profile / "config.json").write_text(
        json.dumps(_profile_config("portable")), encoding="utf-8")

    first = "items/nested folder/камень №1.png"
    second = "items/второе.png"
    first_bytes = b"first-photo-content"
    second_bytes = b"second-photo-content"
    (source_profile / first).parent.mkdir(parents=True, exist_ok=True)
    (source_profile / first).write_bytes(first_bytes)
    (source_profile / second).write_bytes(second_bytes)
    legacy_reference = second.replace("/", "\\")
    source_items = {
        "version": 3,
        "items": [
            {"name": "Камень", "images": [first, second],
             "image": first},
            {"name": "Legacy", "image": legacy_reference},
        ],
    }
    (source_profile / "items.json").write_text(
        json.dumps(source_items, ensure_ascii=False), encoding="utf-8")
    (source_root / "profiles.json").write_text(json.dumps({
        "version": 1, "active": "Default",
        "profiles": [
            {"name": "Default", "directory": "."},
            {"name": "Alpha", "directory": "profiles/Alpha"},
        ],
    }), encoding="utf-8")

    archive = tmp_path / "transfer.chestprofile"
    config_module.export_profile_archive("Alpha", archive)

    # Import as if the archive was opened by a completely separate installation.
    destination_root = tmp_path / "new-install"
    destination_root.mkdir()
    monkeypatch.setattr(paths, "BASE_DIR", str(destination_root))
    monkeypatch.setattr(paths, "PROFILES_FILE", str(destination_root / "profiles.json"))
    monkeypatch.setattr(paths, "PROFILE_DIR", str(destination_root))
    (destination_root / "profiles.json").write_text(json.dumps({
        "version": 1, "active": "Default",
        "profiles": [{"name": "Default", "directory": "."}],
    }), encoding="utf-8")

    entry = config_module.import_profile_archive(archive, "Imported")
    imported_profile = destination_root / entry["directory"]
    imported_items = json.loads((imported_profile / "items.json").read_text(
        encoding="utf-8"))
    assert imported_items == source_items
    assert (imported_profile / first).read_bytes() == first_bytes
    assert (imported_profile / second).read_bytes() == second_bytes
    assert str(source_root) not in json.dumps(imported_items)

    config_module.activate_profile("Imported")
    assert Path(commons.absolute_path(first)) == imported_profile / first
    assert Path(commons.absolute_path(second)) == imported_profile / second
    assert Path(commons.absolute_path(legacy_reference)) == imported_profile / second
    assert Path(imported_items["items"][0]["image"]).name == Path(first).name
    assert imported_items["items"][0]["images"] == [first, second]
    assert imported_items["items"][1]["image"] == legacy_reference


def test_profile_archive_checksum_corruption_is_rejected_without_changes(
        tmp_path, monkeypatch):
    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("portable")), encoding="utf-8")
    archive = tmp_path / "portable.zip"
    config_module.export_profile_archive("Alpha", archive)
    import zipfile
    corrupt = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(archive) as original, zipfile.ZipFile(
            corrupt, "w") as damaged:
        for info in original.infolist():
            content = original.read(info.filename)
            if info.filename == "config.json":
                content += b" "
            damaged.writestr(info.filename, content)
    import pytest
    with pytest.raises(ValueError, match="Контрольная сумма"):
        config_module.import_profile_archive(corrupt, "Broken")
    assert not (tmp_path / "profiles" / "Broken").exists()
    assert all(item["name"] != "Broken"
               for item in config_module.load_profiles()["profiles"])


def test_portable_profile_paths_reject_windows_backslashes():
    assert not config_module._safe_portable_profile_path(r"items/sub\..\..\outside.txt")
    assert not config_module._safe_portable_profile_path(r"items/sub\photo.png")
    assert config_module._safe_portable_profile_path("items/sub/photo.png")


def test_profile_archive_rejects_traversal_before_writing(
        tmp_path, monkeypatch):
    _prepare_profile_registry(tmp_path, monkeypatch)
    import zipfile
    archive = tmp_path / "traversal.zip"
    manifest = {
        "format": "ChestScannerProfile", "version": 1,
        "profile_name": "Evil",
        "files": {"../outside.txt": {"size": 1, "sha256": "0" * 64}},
    }
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("manifest.json", json.dumps(manifest))
        zipped.writestr("../outside.txt", b"x")
    import pytest
    with pytest.raises(ValueError, match="небезопасный путь"):
        config_module.import_profile_archive(archive)
    assert not (tmp_path.parent / "outside.txt").exists()


def test_profile_archive_registry_failure_rolls_back_imported_files(
        tmp_path, monkeypatch):
    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("portable")), encoding="utf-8")
    archive = tmp_path / "portable.zip"
    config_module.export_profile_archive("Alpha", archive)

    def fail_save(_data):
        raise OSError("registry unavailable")

    monkeypatch.setattr(config_module, "save_profiles", fail_save)
    import pytest
    with pytest.raises(OSError, match="registry unavailable"):
        config_module.import_profile_archive(archive, "Failed")
    assert not (tmp_path / "profiles" / "Failed").exists()
    assert not list((tmp_path / "profiles").glob(".profile-import-*"))



def test_default_profile_can_be_exported(tmp_path, monkeypatch):
    _prepare_profile_registry(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, "PROFILE_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps(_profile_config("default")), encoding="utf-8")
    (tmp_path / "items.json").write_text(
        '{"items":[{"name":"default-item"}]}', encoding="utf-8")
    archive = tmp_path / "default.chestprofile"
    config_module.export_profile_archive("Default", archive)
    assert archive.is_file()
    entry = config_module.import_profile_archive(archive, "Default Copy")
    assert entry["name"] == "Default Copy"


def test_profile_import_rejects_unsafe_profile_name(tmp_path, monkeypatch):
    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("portable")), encoding="utf-8")
    archive = tmp_path / "portable.zip"
    config_module.export_profile_archive("Alpha", archive)
    import pytest
    with pytest.raises(ValueError, match="допустимое имя"):
        config_module.import_profile_archive(archive, "../Bad")
    assert not (tmp_path / "profiles" / "Bad").exists()



def test_full_profile_backup_preserves_private_data_and_retains_ten(
        tmp_path, monkeypatch):
    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("backup")), encoding="utf-8")
    (source / "results").mkdir()
    (source / "results" / "chests.json").write_text(
        '[{"id":"history"}]', encoding="utf-8")
    (source / "discord_webhook.json").write_text(
        '{"url":"private"}', encoding="utf-8")
    first = config_module.create_profile_backup("Alpha")
    assert Path(first).is_file()
    import zipfile
    with zipfile.ZipFile(first) as archive:
        names = set(archive.namelist())
        assert "results/chests.json" in names
        assert "discord_webhook.json" in names
        assert "manifest.json" in names
    for _ in range(11):
        config_module.create_profile_backup("Alpha")
    assert len(config_module.list_profile_backups("Alpha")) == 10


def test_profile_backup_retention_breaks_equal_mtime_ties_by_timestamp_name(
        tmp_path, monkeypatch):
    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("backup")), encoding="utf-8")

    backup_root = Path(config_module._profile_backup_root("Alpha"))
    real_listdir = config_module.os.listdir
    monkeypatch.setattr(config_module.os.path, "getmtime", lambda _path: 1.0)

    def sorted_backup_listing(path):
        entries = real_listdir(path)
        if Path(path) == backup_root:
            return sorted(entries)
        return entries

    monkeypatch.setattr(config_module.os, "listdir", sorted_backup_listing)

    created = [config_module.create_profile_backup("Alpha") for _ in range(11)]

    retained = config_module.list_profile_backups("Alpha")
    assert len(retained) == 10
    assert created[0] not in retained
    assert created[-1] in retained
    assert retained[0] == created[-1]



def test_profile_backup_listing_waits_for_profile_operations(tmp_path, monkeypatch):
    import threading

    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("backup")), encoding="utf-8")
    backup = config_module.create_profile_backup("Alpha")

    started = threading.Event()
    finished = threading.Event()
    result = []

    def list_backups():
        started.set()
        try:
            result.extend(config_module.list_profile_backups("Alpha"))
        finally:
            finished.set()

    with config_module.profile_operation_guard():
        worker = threading.Thread(target=list_backups)
        worker.start()
        assert started.wait(2)
        assert not finished.wait(0.05)

    worker.join(2)
    assert not worker.is_alive()
    assert finished.is_set()
    assert result == [backup]


def test_profile_restore_replaces_data_and_keeps_pre_restore_backup(
        tmp_path, monkeypatch):
    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("before")), encoding="utf-8")
    (source / "items.json").write_text(
        '{"items":[{"name":"before"}]}', encoding="utf-8")
    backup = config_module.create_profile_backup("Alpha")
    (source / "config.json").write_text(
        json.dumps(_profile_config("after")), encoding="utf-8")
    (source / "items.json").write_text(
        '{"items":[{"name":"after"}]}', encoding="utf-8")
    config_module.restore_profile_backup("Alpha", backup)
    assert json.loads((source / "config.json").read_text(
        encoding="utf-8"))["game_window_title"] == "before"
    assert json.loads((source / "items.json").read_text(
        encoding="utf-8"))["items"][0]["name"] == "before"
    assert len(config_module.list_profile_backups("Alpha")) == 2



def test_profile_restore_recreates_missing_registered_directory_from_backup(
        tmp_path, monkeypatch):
    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("restorable")), encoding="utf-8")
    backup = config_module.create_profile_backup("Alpha")
    source.rename(tmp_path / "profiles" / "Alpha-missing")
    (tmp_path / "profiles" / "Alpha-missing").rename(
        tmp_path / "preserved-outside-target")

    config_module.restore_profile_backup("Alpha", backup)

    target = tmp_path / "profiles" / "Alpha"
    assert target.is_dir()
    assert json.loads((target / "config.json").read_text(
        encoding="utf-8"))["game_window_title"] == "restorable"
    assert json.loads((target / "items.json").read_text(
        encoding="utf-8"))["items"][0]["name"] == "valuable"
    assert (tmp_path / "preserved-outside-target").is_dir()
    assert not (tmp_path / ".profile-operation.json").exists()


def test_profile_restore_from_backup_preserves_unvalidatable_original_directory(
        tmp_path, monkeypatch):
    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("verified")), encoding="utf-8")
    backup = config_module.create_profile_backup("Alpha")
    damaged = b'{"game_window_title": '
    (source / "config.json").write_bytes(damaged)

    config_module.restore_profile_backup("Alpha", backup)

    target = tmp_path / "profiles" / "Alpha"
    assert json.loads((target / "config.json").read_text(
        encoding="utf-8"))["game_window_title"] == "verified"
    retained = list((tmp_path / "profiles").glob(".profile-rollback-*"))
    assert len(retained) == 1
    assert (retained[0] / "config.json").read_bytes() == damaged
    assert not (tmp_path / ".profile-operation.json").exists()


def test_missing_profile_restore_crash_after_install_is_recovered(
        tmp_path, monkeypatch):
    import pytest

    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("restored-after-crash")), encoding="utf-8")
    backup = config_module.create_profile_backup("Alpha")
    source.rename(tmp_path / "profiles" / "Alpha-removed")
    real_rename = config_module.os.rename

    def interrupt_after_install(src, dst):
        result = real_rename(src, dst)
        if Path(dst) == tmp_path / "profiles" / "Alpha":
            raise SystemExit(81)
        return result

    monkeypatch.setattr(config_module.os, "rename", interrupt_after_install)
    with pytest.raises(SystemExit):
        config_module.restore_profile_backup("Alpha", backup)

    target = tmp_path / "profiles" / "Alpha"
    assert target.is_dir()
    journal = json.loads((tmp_path / ".profile-operation.json").read_text(
        encoding="utf-8"))
    assert journal["phase"] == "prepared"
    monkeypatch.setattr(config_module.os, "rename", real_rename)

    findings = config_module.recover_profile_operations()

    assert target.is_dir()
    assert not (tmp_path / ".profile-operation.json").exists()
    assert any("Restore installed" in item for item in findings)



def test_restore_recovery_keeps_invalid_original_rollback_directory(
        tmp_path, monkeypatch):
    _prepare_profile_registry(tmp_path, monkeypatch)
    target = tmp_path / "profiles" / "Alpha"
    rollback = tmp_path / "profiles" / ".profile-rollback-preserved"
    target.rename(rollback)
    target.mkdir()
    (target / "items.json").write_text('{"items":[]}', encoding="utf-8")
    _write_transaction_journal(tmp_path, {
        "version": 1, "operation": "restore", "phase": "committed",
        "profile": "Alpha", "target": str(target),
        "staging": str(tmp_path / "profiles" / ".profile-restore-unused"),
        "rollback": str(rollback), "had_original": True,
        "preserve_rollback": True, "created_at": "test",
    })

    findings = config_module.recover_profile_operations()

    assert target.is_dir() and rollback.is_dir()
    assert not (tmp_path / ".profile-operation.json").exists()
    assert any("Original profile retained" in item for item in findings)


def test_restore_refuses_non_directory_target_without_overwriting_it(
        tmp_path, monkeypatch):
    import shutil

    _prepare_profile_registry(tmp_path, monkeypatch)
    backup = config_module.create_profile_backup("Alpha")
    target = tmp_path / "profiles" / "Alpha"
    shutil.rmtree(target)
    target.write_bytes(b"unrelated user file")
    with pytest.raises(FileExistsError):
        config_module.restore_profile_backup("Alpha", backup)
    assert target.read_bytes() == b"unrelated user file"


def test_profile_backup_restore_preserves_photo_files_and_order(
        tmp_path, monkeypatch):
    source = _prepare_profile_registry(tmp_path, monkeypatch)
    photos = source / "items"
    photos.mkdir()
    first_name = "\u0410\u043b\u043c\u0430\u0437 \u043f\u0435\u0440\u0432\u044b\u0439.png"
    second_name = "\u0410\u043b\u043c\u0430\u0437 \u0432\u0442\u043e\u0440\u043e\u0439.png"
    first = photos / first_name
    second = photos / second_name
    first_bytes = b"first-photo-content"
    second_bytes = b"second-photo-content"
    first.write_bytes(first_bytes)
    second.write_bytes(second_bytes)
    first_reference = "items/" + first_name
    second_reference = "items/" + second_name
    original_database = {
        "items": [{
            "name": "\u0410\u043b\u043c\u0430\u0437",
            "images": [second_reference, first_reference],
            "image": second_reference,
        }],
    }
    (source / "items.json").write_text(
        json.dumps(original_database, ensure_ascii=False), encoding="utf-8")

    backup = config_module.create_profile_backup("Alpha")

    # Simulate edits/removals after the backup was created.
    first.unlink()
    second.write_bytes(b"changed-photo-content")
    (source / "items.json").write_text(
        json.dumps({"items": [{"name": "Changed"}]}), encoding="utf-8")

    config_module.restore_profile_backup("Alpha", backup)

    restored = json.loads((source / "items.json").read_text(encoding="utf-8"))
    item = restored["items"][0]
    assert item["images"] == [second_reference, first_reference]
    assert item["image"] == second_reference
    assert (photos / first_name).read_bytes() == first_bytes
    assert (photos / second_name).read_bytes() == second_bytes
    assert (source / item["image"]).is_file()



def test_restore_rejects_backup_removed_after_listing_without_mutating_profile(
        tmp_path, monkeypatch):
    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("current")), encoding="utf-8")
    backup = config_module.create_profile_backup("Alpha")

    listed = config_module.list_profile_backups("Alpha")
    assert listed == [backup]
    Path(backup).unlink()

    with pytest.raises(ValueError):
        config_module.restore_profile_backup("Alpha", listed[0])

    assert json.loads((source / "config.json").read_text(
        encoding="utf-8"))["game_window_title"] == "current"
    assert source.is_dir()
    assert config_module.list_profile_backups("Alpha") == []
    assert not list((tmp_path / "profiles").glob(".profile-restore-*"))
    assert not list((tmp_path / "profiles").glob(".profile-rollback-*"))


def test_profile_restore_rejects_corrupted_backup_without_changing_data(
        tmp_path, monkeypatch):
    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("current")), encoding="utf-8")
    backup = config_module.create_profile_backup("Alpha")
    import zipfile
    corrupt = tmp_path / "corrupt.chestbackup"
    with zipfile.ZipFile(backup) as original, zipfile.ZipFile(
            corrupt, "w") as damaged:
        for info in original.infolist():
            content = original.read(info.filename)
            if info.filename == "config.json":
                content += b" "
            damaged.writestr(info.filename, content)
    import pytest
    with pytest.raises(ValueError, match="Контрольная сумма"):
        config_module.restore_profile_backup("Alpha", corrupt)
    assert json.loads((source / "config.json").read_text(
        encoding="utf-8"))["game_window_title"] == "current"
    assert len(config_module.list_profile_backups("Alpha")) == 1


def test_delete_profile_creates_backup_before_removing_data(
        tmp_path, monkeypatch):
    folder = _prepare_profile_registry(tmp_path, monkeypatch)
    (folder / "discord_webhook.json").write_text(
        '{"url":"private"}', encoding="utf-8")
    config_module.delete_profile_data("Alpha")
    backups = list((tmp_path / ".profile_backups").glob(
        "Alpha-*/*.chestbackup"))
    assert len(backups) == 1
    import zipfile
    with zipfile.ZipFile(backups[0]) as archive:
        assert "discord_webhook.json" in archive.namelist()


def test_delete_profile_backup_preserves_nested_photo_files_and_references(
        tmp_path, monkeypatch):
    folder = _prepare_profile_registry(tmp_path, monkeypatch)
    first = "items/nested folder/\u0424\u043e\u0442\u043e 1.png"
    second = "items/\u0410\u0440\u0445\u0438\u0432 2.png"
    first_bytes, second_bytes = b"first-photo", b"second-photo"
    for relative, content in ((first, first_bytes), (second, second_bytes)):
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (folder / "items.json").write_text(json.dumps({
        "items": [{"name": "Photo item", "images": [first, second],
                   "image": first}],
    }, ensure_ascii=False), encoding="utf-8")

    config_module.delete_profile_data("Alpha")

    assert not folder.exists()
    assert all(item["name"] != "Alpha"
               for item in config_module.load_profiles()["profiles"])
    backups = config_module.list_profile_backups("Alpha")
    assert len(backups) == 1
    payload = config_module._read_profile_backup(backups[0], "Alpha")
    restored_database = json.loads(payload["items.json"].decode("utf-8"))
    assert restored_database["items"][0]["images"] == [first, second]
    assert restored_database["items"][0]["image"] == first
    assert payload[first] == first_bytes
    assert payload[second] == second_bytes


def test_renamed_profile_backup_restore_keeps_photo_references_resolvable(
        tmp_path, monkeypatch):
    from chest_scanner import commons

    source = _prepare_profile_registry(tmp_path, monkeypatch)
    first = "items/nested/\u0420\u0435\u0434\u043a\u043e\u0435 1.png"
    second = "items/\u0420\u0435\u0434\u043a\u043e\u0435 2.png"
    first_bytes, second_bytes = b"renamed-primary", b"renamed-secondary"
    for relative, content in ((first, first_bytes), (second, second_bytes)):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (source / "items.json").write_text(json.dumps({
        "items": [{"name": "Renamed photo item", "images": [second, first],
                   "image": second}],
    }, ensure_ascii=False), encoding="utf-8")

    config_module.rename_profile_data("Alpha", "Renamed")
    target = tmp_path / "profiles" / "Renamed"
    monkeypatch.setattr(paths, "PROFILE_DIR", str(target))
    backup = config_module.create_profile_backup("Renamed")

    (target / first).unlink()
    (target / second).write_bytes(b"modified")
    (target / "items.json").write_text('{"items":[]}', encoding="utf-8")
    config_module.restore_profile_backup("Renamed", backup)

    restored = json.loads((target / "items.json").read_text(encoding="utf-8"))
    item = restored["items"][0]
    assert item["images"] == [second, first]
    assert item["image"] == second
    assert (target / first).read_bytes() == first_bytes
    assert (target / second).read_bytes() == second_bytes
    for relative in item["images"]:
        resolved = Path(commons.absolute_path(relative))
        assert resolved == target / relative
        assert resolved.is_file()


def test_restore_active_and_default_profiles_is_rejected(
        tmp_path, monkeypatch):
    _prepare_profile_registry(tmp_path, monkeypatch)
    import pytest
    with pytest.raises(ValueError, match="переключитесь"):
        config_module.restore_profile_backup("Default", "unused")



def test_default_backup_contains_profile_data_not_application_files(
        tmp_path, monkeypatch):
    _prepare_profile_registry(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, "PROFILE_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps(_profile_config("default")), encoding="utf-8")
    (tmp_path / "items.json").write_text(
        '{"items":[{"name":"default-item"}]}', encoding="utf-8")
    (tmp_path / "main.py").write_text("application source", encoding="utf-8")
    (tmp_path / "profiles").mkdir(exist_ok=True)
    (tmp_path / "profiles" / "unrelated.txt").write_text(
        "not profile data", encoding="utf-8")
    backup = config_module.create_profile_backup("Default")
    import zipfile
    with zipfile.ZipFile(backup) as archive:
        names = set(archive.namelist())
    assert "config.json" in names
    assert "items.json" in names
    assert "main.py" not in names
    assert "profiles/unrelated.txt" not in names


def test_profile_restore_rolls_back_if_directory_swap_fails(
        tmp_path, monkeypatch):
    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("before")), encoding="utf-8")
    backup = config_module.create_profile_backup("Alpha")
    (source / "config.json").write_text(
        json.dumps(_profile_config("current")), encoding="utf-8")
    real_rename = config_module.os.rename
    calls = 0

    def fail_second_rename(src, dst):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("swap failed")
        return real_rename(src, dst)

    monkeypatch.setattr(config_module.os, "rename", fail_second_rename)
    import pytest
    with pytest.raises(OSError, match="swap failed"):
        config_module.restore_profile_backup("Alpha", backup)
    assert json.loads((source / "config.json").read_text(
        encoding="utf-8"))["game_window_title"] == "current"
    assert source.is_dir()



def test_restore_crash_after_moving_original_before_journal_update_recovers(
        tmp_path, monkeypatch):
    import pytest

    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("restored")), encoding="utf-8")
    backup = config_module.create_profile_backup("Alpha")
    (source / "config.json").write_text(
        json.dumps(_profile_config("current")), encoding="utf-8")

    real_rename = config_module.os.rename
    calls = 0

    def interrupt_after_original_move(src, dst):
        nonlocal calls
        result = real_rename(src, dst)
        calls += 1
        if calls == 1:
            raise SystemExit(78)
        return result

    monkeypatch.setattr(config_module.os, "rename", interrupt_after_original_move)
    with pytest.raises(SystemExit):
        config_module.restore_profile_backup("Alpha", backup)

    target = tmp_path / "profiles" / "Alpha"
    rollback_dirs = list((tmp_path / "profiles").glob(".profile-rollback-*"))
    assert not target.exists()
    assert len(rollback_dirs) == 1
    assert json.loads((rollback_dirs[0] / "config.json").read_text(
        encoding="utf-8"))["game_window_title"] == "current"
    journal = json.loads((tmp_path / ".profile-operation.json").read_text(
        encoding="utf-8"))
    assert journal["phase"] == "prepared"

    monkeypatch.setattr(config_module.os, "rename", real_rename)
    findings = config_module.recover_profile_operations()
    assert target.is_dir() and not rollback_dirs[0].exists()
    assert json.loads((target / "config.json").read_text(
        encoding="utf-8"))["game_window_title"] == "current"
    assert not (tmp_path / ".profile-operation.json").exists()
    assert any("\u0412\u043e\u0441\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d" in item
               for item in findings)


def test_restore_startup_recovery_rolls_back_when_target_is_missing(
        tmp_path, monkeypatch):
    _prepare_profile_registry(tmp_path, monkeypatch)
    target = tmp_path / "profiles" / "Alpha"
    rollback = tmp_path / "profiles" / ".profile-rollback-crash"
    target.rename(rollback)
    staging = tmp_path / "profiles" / ".profile-restore-crash"
    staging.mkdir()
    journal = {
        "version": 1, "operation": "restore", "phase": "old_moved",
        "profile": "Alpha", "target": str(target), "staging": str(staging),
        "rollback": str(rollback), "created_at": "test",
    }
    (tmp_path / ".profile-operation.json").write_text(
        json.dumps(journal), encoding="utf-8")

    findings = config_module.recover_profile_operations()

    assert target.is_dir()
    assert not rollback.exists()
    assert not (tmp_path / ".profile-operation.json").exists()
    assert any("Восстановлен исходный каталог" in item for item in findings)
    # Incomplete staging is intentionally retained for inspection.
    assert staging.is_dir()


def test_restore_startup_recovery_clears_prepared_journal_before_first_rename(
        tmp_path, monkeypatch):
    target = _prepare_profile_registry(tmp_path, monkeypatch)
    staging = tmp_path / "profiles" / ".profile-restore-prepared"
    staging.mkdir()
    (staging / "items.json").write_text('{"items":[]}', encoding="utf-8")
    journal_path = tmp_path / ".profile-operation.json"
    _write_transaction_journal(tmp_path, {
        "version": 1, "operation": "restore", "phase": "prepared",
        "profile": "Alpha", "target": str(target), "staging": str(staging),
        "rollback": str(tmp_path / "profiles" / ".profile-rollback-prepared"),
        "created_at": "test",
    })

    findings = config_module.recover_profile_operations()

    assert target.is_dir()
    assert json.loads((target / "items.json").read_text(
        encoding="utf-8"))["items"][0]["name"] == "valuable"
    assert not journal_path.exists()
    # Retain staging for conservative manual cleanup; only the stale journal
    # is retired because the original target is demonstrably untouched.
    assert staging.is_dir()
    assert any("\u043d\u0435 \u043d\u0430\u0447\u0430\u043b\u043e\u0441\u044c" in item for item in findings)


def test_restore_startup_recovery_does_not_guess_when_both_dirs_exist(
        tmp_path, monkeypatch):
    _prepare_profile_registry(tmp_path, monkeypatch)
    target = tmp_path / "profiles" / "Alpha"
    rollback = tmp_path / "profiles" / ".profile-rollback-ambiguous"
    target.rename(rollback)
    target.mkdir()
    journal = {
        "version": 1, "operation": "restore", "phase": "new_installed",
        "profile": "Alpha", "target": str(target),
        "staging": str(tmp_path / "profiles" / ".profile-restore-x"),
        "rollback": str(rollback), "created_at": "test",
    }
    journal_path = tmp_path / ".profile-operation.json"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    findings = config_module.recover_profile_operations()

    assert target.is_dir() and rollback.is_dir()
    assert journal_path.is_file()
    assert any("неоднозначно" in item for item in findings)


def test_restore_recovery_rejects_junction_rollback_even_when_target_is_inside_base(
        tmp_path, monkeypatch):
    import subprocess

    if not hasattr(os.path, "isjunction"):
        pytest.skip("junction detection is unavailable")

    _prepare_profile_registry(tmp_path, monkeypatch)
    target = tmp_path / "profiles" / "Alpha"
    assert target.is_dir()
    real_rollback = tmp_path / "profiles" / ".profile-rollback-real"
    real_rollback.mkdir()
    sentinel = real_rollback / "preserve.txt"
    sentinel.write_text("outside-sensitive-data", encoding="utf-8")
    rollback = tmp_path / "profiles" / ".profile-rollback-junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(rollback), str(real_rollback)],
        capture_output=True, text=True, check=False)
    if result.returncode != 0 or not os.path.isjunction(rollback):
        pytest.skip(f"directory junctions are unavailable: {result.stderr}")

    journal_path = tmp_path / ".profile-operation.json"
    journal_path.write_text(json.dumps({
        "version": 1, "operation": "restore", "phase": "committed",
        "profile": "Alpha", "target": str(target),
        "staging": str(tmp_path / "profiles" / ".profile-restore-x"),
        "rollback": str(rollback), "created_at": "test",
    }), encoding="utf-8")

    findings = config_module.recover_profile_operations()

    assert target.is_dir()
    assert os.path.isjunction(rollback)
    assert sentinel.read_text(encoding="utf-8") == "outside-sensitive-data"
    assert journal_path.is_file()
    assert findings


def test_restore_recovery_rmtree_rejects_rollback_replaced_by_junction(
        tmp_path, monkeypatch):
    import subprocess

    if not hasattr(os.path, "isjunction"):
        pytest.skip("junction detection is unavailable")

    _prepare_profile_registry(tmp_path, monkeypatch)
    target = tmp_path / "profiles" / "Alpha"
    rollback = tmp_path / "profiles" / ".profile-rollback-race"
    target.rename(rollback)
    target.mkdir()
    held = tmp_path / "profiles" / ".profile-rollback-held"
    outside = tmp_path / "outside-sensitive"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("must-survive", encoding="utf-8")
    _write_transaction_journal(tmp_path, {
        "version": 1, "operation": "restore", "phase": "committed",
        "profile": "Alpha", "target": str(target),
        "staging": str(tmp_path / "profiles" / ".profile-restore-x"),
        "rollback": str(rollback), "created_at": "test",
    })

    real_rmtree = config_module.shutil.rmtree
    swapped = False

    def swap_to_junction_before_delete(path, *args, **kwargs):
        nonlocal swapped
        if Path(path) == rollback and not swapped:
            swapped = True
            rollback.rename(held)
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(rollback), str(outside)],
                capture_output=True, text=True, check=False)
            assert result.returncode == 0, result.stderr
            assert os.path.isjunction(rollback)
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(config_module.shutil, "rmtree", swap_to_junction_before_delete)
    findings = config_module.recover_profile_operations()

    assert swapped
    assert target.is_dir()
    assert held.is_dir()
    assert os.path.isjunction(rollback)
    assert sentinel.read_text(encoding="utf-8") == "must-survive"
    assert (tmp_path / ".profile-operation.json").is_file()
    assert findings


def test_profile_directory_rejects_parent_junction_inside_base(
        tmp_path, monkeypatch):
    import subprocess

    if not hasattr(os.path, "isjunction"):
        pytest.skip("junction detection is unavailable")
    source = _prepare_profile_registry(tmp_path, monkeypatch)
    profiles = tmp_path / "profiles"
    redirected = tmp_path / "redirected-profiles"
    profiles.rename(redirected)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(profiles), str(redirected)],
        capture_output=True, text=True, check=False)
    if result.returncode != 0 or not os.path.isjunction(profiles):
        pytest.skip(f"directory junctions are unavailable: {result.stderr}")
    assert not os.path.isjunction(source)
    with pytest.raises(ValueError):
        config_module._profile_directory({"directory": "profiles/Alpha"})


def test_restore_recovery_rejects_parent_junction_inside_base(
        tmp_path, monkeypatch):
    import subprocess

    if not hasattr(os.path, "isjunction"):
        pytest.skip("junction detection is unavailable")
    _prepare_profile_registry(tmp_path, monkeypatch)
    profiles = tmp_path / "profiles"
    redirected = tmp_path / "redirected-profiles"
    profiles.rename(redirected)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(profiles), str(redirected)],
        capture_output=True, text=True, check=False)
    if result.returncode != 0 or not os.path.isjunction(profiles):
        pytest.skip(f"directory junctions are unavailable: {result.stderr}")

    target = profiles / "Alpha"
    rollback = profiles / ".profile-rollback-parent-junction"
    rollback.mkdir()
    sentinel = rollback / "preserve.txt"
    sentinel.write_text("must-survive", encoding="utf-8")
    journal_path = tmp_path / ".profile-operation.json"
    _write_transaction_journal(tmp_path, {
        "version": 1, "operation": "restore", "phase": "committed",
        "profile": "Alpha", "target": str(target),
        "staging": str(profiles / ".profile-restore-x"),
        "rollback": str(rollback), "created_at": "test",
    })

    findings = config_module.recover_profile_operations()

    assert target.is_dir()
    assert sentinel.read_text(encoding="utf-8") == "must-survive"
    assert journal_path.is_file()
    assert findings


def test_restore_startup_recovery_rejects_journal_path_escape(
        tmp_path, monkeypatch):
    _prepare_profile_registry(tmp_path, monkeypatch)
    outside = tmp_path.parent / "outside-profile"
    outside.mkdir()
    journal = {
        "version": 1, "operation": "restore", "phase": "old_moved",
        "profile": "Alpha", "target": str(outside),
        "staging": str(tmp_path / "profiles" / ".profile-restore-x"),
        "rollback": str(outside), "created_at": "test",
    }
    journal_path = tmp_path / ".profile-operation.json"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    findings = config_module.recover_profile_operations()

    assert outside.is_dir()
    assert journal_path.is_file()
    assert any("требует проверки" in item for item in findings)



def test_profile_recovery_reports_orphan_artifacts_without_deleting(
        tmp_path, monkeypatch):
    _prepare_profile_registry(tmp_path, monkeypatch)
    orphan = tmp_path / "profiles" / ".profile-create-interrupted"
    orphan.mkdir()
    backup_root = tmp_path / ".profile_backups" / "Alpha-hash"
    backup_root.mkdir(parents=True)
    partial = backup_root / "partial.chestbackup.tmp-interrupted"
    partial.write_bytes(b"partial archive")

    findings = config_module.recover_profile_operations()

    assert orphan.is_dir()
    assert partial.read_bytes() == b"partial archive"
    assert any("временные данные профилей" in item for item in findings)


def test_profile_recovery_does_not_walk_junction_directories(
        tmp_path, monkeypatch):
    _prepare_profile_registry(tmp_path, monkeypatch)
    linked_dir = tmp_path / "profiles" / "linked-external"
    linked_dir.mkdir()
    external_artifact = linked_dir / ".profile-create-outside"
    external_artifact.mkdir()
    monkeypatch.setattr(
        os.path, "isjunction",
        lambda path: os.path.normcase(os.path.abspath(path))
        == os.path.normcase(os.path.abspath(linked_dir)), raising=False)

    findings = config_module.recover_profile_operations()

    assert external_artifact.is_dir()
    assert not any("linked-external" in finding for finding in findings)


def test_profile_recovery_rejects_linked_journal_path_without_reading_target(
        tmp_path, monkeypatch):
    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    journal_path = tmp_path / ".profile-operation.json"
    journal_path.write_text('{"version": 99}', encoding="utf-8")
    external = tmp_path.parent / f"{tmp_path.name}-external-journal.json"
    external.write_text('{"version": 1, "operation": "create"}', encoding="utf-8")
    original_islink = config_module.os.path.islink

    def simulated_link(path):
        if os.path.abspath(path) == os.path.abspath(journal_path):
            return True
        return original_islink(path)

    monkeypatch.setattr(config_module.os.path, "islink", simulated_link)
    findings = config_module.recover_profile_operations()

    assert findings
    assert journal_path.read_text(encoding="utf-8") == '{"version": 99}'
    assert external.read_text(encoding="utf-8") == '{"version": 1, "operation": "create"}'
    with pytest.raises(ValueError, match="link or junction"):
        config_module._clear_profile_operation_journal()
    with pytest.raises(ValueError, match="link or junction"):
        config_module._write_profile_operation_journal({"version": 1})
    assert external.exists()


def test_profile_operation_journal_rejects_directory_and_special_file(
        tmp_path, monkeypatch):
    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    journal_path = tmp_path / ".profile-operation.json"
    journal_path.mkdir()
    with pytest.raises(ValueError, match="not a regular file"):
        config_module._validate_profile_operation_journal_path()
    with pytest.raises(ValueError, match="not a regular file"):
        config_module._clear_profile_operation_journal()

    import stat
    from types import SimpleNamespace
    journal_path.rmdir()
    original_lstat = config_module.os.lstat

    def simulated_fifo(path):
        if os.path.abspath(path) == os.path.abspath(journal_path):
            return SimpleNamespace(st_mode=stat.S_IFIFO)
        return original_lstat(path)

    monkeypatch.setattr(config_module.os, "lstat", simulated_fifo)
    with pytest.raises(ValueError, match="not a regular file"):
        config_module._validate_profile_operation_journal_path()


def test_profile_recovery_rejects_journal_swap_between_check_and_open(
        tmp_path, monkeypatch):
    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    journal_path = tmp_path / ".profile-operation.json"
    journal_path.write_text('{"version": 99}', encoding="utf-8")
    external = tmp_path.parent / f"{tmp_path.name}-race-journal.json"
    external.write_text('{"version": 1, "operation": "create"}', encoding="utf-8")
    original_open = config_module.os.open
    redirected = False

    def swapped_open(path, flags, *args, **kwargs):
        nonlocal redirected
        if os.path.abspath(path) == os.path.abspath(journal_path) and not redirected:
            redirected = True
            return original_open(str(external), flags, *args, **kwargs)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(config_module.os, "open", swapped_open)
    findings = config_module.recover_profile_operations()

    assert redirected
    assert findings
    assert journal_path.read_text(encoding="utf-8") == '{"version": 99}'
    assert external.read_text(encoding="utf-8") == '{"version": 1, "operation": "create"}'


def test_profile_recovery_keeps_journal_when_open_fails(tmp_path, monkeypatch):
    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    journal_path = tmp_path / ".profile-operation.json"
    original = '{"version": 99, "operation": "unknown"}'
    journal_path.write_text(original, encoding="utf-8")
    original_open = config_module.os.open

    def fail_open(path, flags, *args, **kwargs):
        if os.path.abspath(path) == os.path.abspath(journal_path):
            raise PermissionError("simulated journal read denial")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(config_module.os, "open", fail_open)
    findings = config_module.recover_profile_operations()

    assert findings
    assert journal_path.read_text(encoding="utf-8") == original


def test_profile_journal_partial_write_preserves_previous_record(
        tmp_path, monkeypatch):
    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    journal_path = tmp_path / ".profile-operation.json"
    original = {"version": 1, "operation": "create", "phase": "prepared"}
    journal_path.write_text(json.dumps(original), encoding="utf-8")
    original_dump = config_module.json.dump

    def partial_dump(record, stream, **kwargs):
        stream.write('{"version":')
        stream.flush()
        raise OSError("simulated partial journal write")

    monkeypatch.setattr(config_module.json, "dump", partial_dump)
    with pytest.raises(OSError, match="partial journal write"):
        config_module._write_profile_operation_journal({"version": 1})

    assert json.loads(journal_path.read_text(encoding="utf-8")) == original
    assert not list(tmp_path.glob(".profile-operation.json.tmp-*"))
    monkeypatch.setattr(config_module.json, "dump", original_dump)


def test_profile_recovery_reports_invalid_journal_without_modifying_paths(
        tmp_path, monkeypatch):
    _prepare_profile_registry(tmp_path, monkeypatch)
    journal_path = tmp_path / ".profile-operation.json"
    journal_path.write_text('{"version": 99, "operation": "unknown"}',
                            encoding="utf-8")

    findings = config_module.recover_profile_operations()

    assert journal_path.is_file()
    assert any("требует проверки" in item for item in findings)



def _write_transaction_journal(tmp_path, record):
    (tmp_path / ".profile-operation.json").write_text(
        json.dumps(record), encoding="utf-8")



@pytest.mark.parametrize("operation", ["rename", "delete", "create"])
def test_profile_recovery_is_idempotent_when_repeated(
        tmp_path, monkeypatch, operation):
    folder = _prepare_profile_registry(tmp_path, monkeypatch)
    if operation == "rename":
        moved = tmp_path / "profiles" / "Renamed"
        folder.rename(moved)
        record = {
            "version": 1, "operation": "rename", "phase": "directory_moved",
            "old_name": "Alpha", "new_name": "Renamed",
            "old_target": str(folder), "new_target": str(moved),
        }
    elif operation == "delete":
        staging = tmp_path / ".profile-delete-repeat"
        folder.rename(staging)
        record = {
            "version": 1, "operation": "delete", "phase": "directory_staged",
            "profile": "Alpha", "target": str(folder), "staging": str(staging),
        }
    else:
        data = config_module.load_profiles()
        target = tmp_path / "profiles" / "NewProfile"
        target.mkdir()
        (target / "items.json").write_text(
            '{"items":[{"name":"created"}]}', encoding="utf-8")
        data["profiles"].append({
            "name": "NewProfile", "directory": "profiles/NewProfile"})
        config_module.save_profiles(data)
        record = {
            "version": 1, "operation": "create", "phase": "directory_installed",
            "profile": "NewProfile", "target": str(target),
            "staging": str(tmp_path / "profiles" / ".profile-create-repeat"),
        }
    journal = tmp_path / ".profile-operation.json"
    _write_transaction_journal(tmp_path, record)

    first_findings = config_module.recover_profile_operations()
    registry_after_first = config_module.load_profiles()
    tree_after_first = sorted(
        (str(path.relative_to(tmp_path)), path.read_bytes() if path.is_file() else None)
        for path in tmp_path.rglob("*") if path.is_file() and path.name != ".profile-operation.json")
    assert not journal.exists()

    second_findings = config_module.recover_profile_operations()
    registry_after_second = config_module.load_profiles()
    tree_after_second = sorted(
        (str(path.relative_to(tmp_path)), path.read_bytes() if path.is_file() else None)
        for path in tmp_path.rglob("*") if path.is_file() and path.name != ".profile-operation.json")

    assert first_findings
    assert second_findings == []
    assert registry_after_second == registry_after_first
    assert tree_after_second == tree_after_first
    if operation == "rename":
        assert folder.is_dir() and not moved.exists()
    elif operation == "delete":
        assert folder.is_dir() and not staging.exists()
    else:
        assert target.is_dir()
        assert json.loads((target / "items.json").read_text(
            encoding="utf-8"))["items"][0]["name"] == "created"


def test_profile_recovery_retries_transient_rollback_failure_idempotently(
        tmp_path, monkeypatch):
    folder = _prepare_profile_registry(tmp_path, monkeypatch)
    rollback = tmp_path / "profiles" / ".profile-rollback-retry"
    folder.rename(rollback)
    staging = tmp_path / "profiles" / ".profile-restore-retry"
    staging.mkdir()
    (rollback / "items" / "nested").mkdir(parents=True)
    photo = rollback / "items" / "nested" / "photo.png"
    photo.write_bytes(b"retry-photo-bytes")
    _write_transaction_journal(tmp_path, {
        "version": 1, "operation": "restore", "phase": "old_moved",
        "profile": "Alpha", "target": str(folder), "staging": str(staging),
        "rollback": str(rollback), "created_at": "test",
    })

    real_rename = config_module.os.rename
    calls = 0

    def fail_once(source, destination):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("temporary recovery rename failure")
        return real_rename(source, destination)

    monkeypatch.setattr(config_module.os, "rename", fail_once)
    first_findings = config_module.recover_profile_operations()
    assert rollback.is_dir() and not folder.exists()
    assert (tmp_path / ".profile-operation.json").is_file()
    assert any("temporary recovery rename failure" in item for item in first_findings)

    second_findings = config_module.recover_profile_operations()
    assert folder.is_dir() and not rollback.exists()
    restored_photo = folder / "items" / "nested" / "photo.png"
    assert restored_photo.read_bytes() == b"retry-photo-bytes"
    assert not (tmp_path / ".profile-operation.json").exists()
    assert second_findings

    final_files = sorted(
        str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*") if path.is_file())
    final_registry = config_module.load_profiles()
    third_findings = config_module.recover_profile_operations()
    # The intentionally retained staging directory remains reportable, but
    # overlapping scan roots must not duplicate the same artifact.
    assert third_findings == [second_findings[-1]]
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")
                  if path.is_file()) == final_files
    assert config_module.load_profiles() == final_registry
    assert restored_photo.read_bytes() == b"retry-photo-bytes"

def test_startup_recovery_rolls_back_interrupted_rename(
        tmp_path, monkeypatch):
    folder = _prepare_profile_registry(tmp_path, monkeypatch)
    moved = tmp_path / "profiles" / "Renamed"
    folder.rename(moved)
    _write_transaction_journal(tmp_path, {
        "version": 1, "operation": "rename", "phase": "directory_moved",
        "old_name": "Alpha", "new_name": "Renamed",
        "old_target": str(folder), "new_target": str(moved),
    })

    findings = config_module.recover_profile_operations()

    assert folder.is_dir()
    assert not moved.exists()
    assert any("Отменено незавершённое переименование" in x for x in findings)
    assert not (tmp_path / ".profile-operation.json").exists()


def test_rename_recovery_keeps_journal_when_committed_directory_is_missing(
        tmp_path, monkeypatch):
    folder = _prepare_profile_registry(tmp_path, monkeypatch)
    moved = tmp_path / "profiles" / "Renamed"
    folder.rename(moved)
    import shutil
    shutil.rmtree(moved)
    data = config_module.load_profiles()
    entry = next(x for x in data["profiles"] if x["name"] == "Alpha")
    entry.update(name="Renamed", directory="profiles/Renamed")
    config_module.save_profiles(data)
    _write_transaction_journal(tmp_path, {
        "version": 1, "operation": "rename", "phase": "directory_moved",
        "old_name": "Alpha", "new_name": "Renamed",
        "old_target": str(folder), "new_target": str(moved),
    })

    findings = config_module.recover_profile_operations()

    assert not folder.exists() and not moved.exists()
    assert (tmp_path / ".profile-operation.json").is_file()
    assert findings


def test_delete_recovery_keeps_journal_when_staging_is_not_directory(
        tmp_path, monkeypatch):
    folder = _prepare_profile_registry(tmp_path, monkeypatch)
    staging = tmp_path / ".profile-delete-corrupt"
    folder.rename(staging)
    for child in staging.iterdir():
        if child.is_dir():
            import shutil
            shutil.rmtree(child)
        else:
            child.unlink()
    staging.rmdir()
    staging.write_text("unexpected file", encoding="utf-8")
    _write_transaction_journal(tmp_path, {
        "version": 1, "operation": "delete", "phase": "directory_staged",
        "profile": "Alpha", "target": str(folder), "staging": str(staging),
    })

    findings = config_module.recover_profile_operations()

    assert not folder.exists()
    assert staging.is_file() and staging.read_text(encoding="utf-8") == "unexpected file"
    assert (tmp_path / ".profile-operation.json").is_file()
    assert findings


def test_startup_recovery_keeps_staged_deleted_profile_when_registry_removed(
        tmp_path, monkeypatch):
    folder = _prepare_profile_registry(tmp_path, monkeypatch)
    staging = tmp_path / ".profile-delete-interrupted"
    folder.rename(staging)
    data = config_module.load_profiles()
    data["profiles"] = [x for x in data["profiles"] if x["name"] != "Alpha"]
    config_module.save_profiles(data)
    _write_transaction_journal(tmp_path, {
        "version": 1, "operation": "delete", "phase": "directory_staged",
        "profile": "Alpha", "target": str(folder), "staging": str(staging),
    })

    findings = config_module.recover_profile_operations()

    assert staging.is_dir()
    assert not folder.exists()
    assert not (tmp_path / ".profile-operation.json").exists()
    assert any("временные файлы сохранены" in x for x in findings)


def test_startup_recovery_restores_staged_profile_if_delete_not_committed(
        tmp_path, monkeypatch):
    folder = _prepare_profile_registry(tmp_path, monkeypatch)
    staging = tmp_path / ".profile-delete-interrupted"
    folder.rename(staging)
    _write_transaction_journal(tmp_path, {
        "version": 1, "operation": "delete", "phase": "directory_staged",
        "profile": "Alpha", "target": str(folder), "staging": str(staging),
    })

    findings = config_module.recover_profile_operations()

    assert folder.is_dir()
    assert not staging.exists()
    assert any("Возвращён каталог профиля" in x for x in findings)


def test_startup_recovery_reports_unregistered_created_profile_without_deleting(
        tmp_path, monkeypatch):
    _prepare_profile_registry(tmp_path, monkeypatch)
    target = tmp_path / "profiles" / "NewProfile"
    target.mkdir()
    (target / "items.json").write_text('{"items":[]}', encoding="utf-8")
    staging = tmp_path / "profiles" / ".profile-create-interrupted"
    _write_transaction_journal(tmp_path, {
        "version": 1, "operation": "create", "phase": "directory_installed",
        "profile": "NewProfile", "target": str(target), "staging": str(staging),
    })

    findings = config_module.recover_profile_operations()

    assert target.is_dir()
    assert (tmp_path / ".profile-operation.json").exists()
    assert any("неоднозначно" in x for x in findings)


@pytest.mark.parametrize("phase", ["prepared", "staged"])
def test_create_recovery_keeps_journal_when_registry_precedes_install_phase(
        tmp_path, monkeypatch, phase):
    _prepare_profile_registry(tmp_path, monkeypatch)
    target = tmp_path / "profiles" / "NewProfile"
    target.mkdir()
    data = config_module.load_profiles()
    data["profiles"].append({
        "name": "NewProfile", "directory": "profiles/NewProfile"})
    config_module.save_profiles(data)
    journal = tmp_path / ".profile-operation.json"
    _write_transaction_journal(tmp_path, {
        "version": 1, "operation": "create", "phase": phase,
        "profile": "NewProfile", "target": str(target),
        "staging": str(tmp_path / "profiles" / ".profile-create-old"),
    })

    findings = config_module.recover_profile_operations()

    assert target.is_dir()
    assert journal.is_file()
    assert findings


def test_create_recovery_keeps_journal_when_registered_target_is_not_directory(
        tmp_path, monkeypatch):
    _prepare_profile_registry(tmp_path, monkeypatch)
    target = tmp_path / "profiles" / "NewProfile"
    target.write_text("not a profile directory", encoding="utf-8")
    data = config_module.load_profiles()
    data["profiles"].append({
        "name": "NewProfile", "directory": "profiles/NewProfile"})
    config_module.save_profiles(data)
    journal = tmp_path / ".profile-operation.json"
    _write_transaction_journal(tmp_path, {
        "version": 1, "operation": "create", "phase": "committed",
        "profile": "NewProfile", "target": str(target),
        "staging": str(tmp_path / "profiles" / ".profile-create-old"),
    })

    findings = config_module.recover_profile_operations()

    assert target.is_file()
    assert journal.is_file()
    assert findings


def test_startup_recovery_finishes_committed_create_journal(
        tmp_path, monkeypatch):
    _prepare_profile_registry(tmp_path, monkeypatch)
    target = tmp_path / "profiles" / "NewProfile"
    target.mkdir()
    data = config_module.load_profiles()
    data["profiles"].append({
        "name": "NewProfile", "directory": "profiles/NewProfile"})
    config_module.save_profiles(data)
    _write_transaction_journal(tmp_path, {
        "version": 1, "operation": "create", "phase": "directory_installed",
        "profile": "NewProfile", "target": str(target),
        "staging": str(tmp_path / "profiles" / ".profile-create-old"),
    })

    findings = config_module.recover_profile_operations()

    assert target.is_dir()
    assert not (tmp_path / ".profile-operation.json").exists()
    assert any("Создание профиля" in x for x in findings)


def test_startup_recovery_finishes_committed_rename_journal(
        tmp_path, monkeypatch):
    folder = _prepare_profile_registry(tmp_path, monkeypatch)
    moved = tmp_path / "profiles" / "Renamed"
    folder.rename(moved)
    data = config_module.load_profiles()
    entry = next(x for x in data["profiles"] if x["name"] == "Alpha")
    entry.update(name="Renamed", directory="profiles/Renamed")
    config_module.save_profiles(data)
    _write_transaction_journal(tmp_path, {
        "version": 1, "operation": "rename", "phase": "directory_moved",
        "old_name": "Alpha", "new_name": "Renamed",
        "old_target": str(folder), "new_target": str(moved),
    })

    config_module.recover_profile_operations()

    assert moved.is_dir()
    assert not (tmp_path / ".profile-operation.json").exists()


def test_recovery_of_interrupted_rename_preserves_nested_photos(
        tmp_path, monkeypatch):
    folder = _prepare_profile_registry(tmp_path, monkeypatch)
    relative = "items/nested/\u0410\u043b\u043c\u0430\u0437 1.png"
    photo = folder / relative
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"rename-recovery-photo")
    (folder / "items.json").write_text(json.dumps({
        "items": [{"name": "Diamond", "images": [relative],
                   "image": relative}],
    }, ensure_ascii=False), encoding="utf-8")

    moved = tmp_path / "profiles" / "Renamed"
    folder.rename(moved)
    _write_transaction_journal(tmp_path, {
        "version": 1, "operation": "rename", "phase": "directory_moved",
        "old_name": "Alpha", "new_name": "Renamed",
        "old_target": str(folder), "new_target": str(moved),
    })

    findings = config_module.recover_profile_operations()

    assert folder.is_dir() and not moved.exists()
    assert (folder / relative).read_bytes() == b"rename-recovery-photo"
    restored = json.loads((folder / "items.json").read_text(encoding="utf-8"))
    assert restored["items"][0]["image"] == relative
    assert not (tmp_path / ".profile-operation.json").exists()
    assert findings


def test_recovery_of_interrupted_delete_restores_photo_tree(
        tmp_path, monkeypatch):
    folder = _prepare_profile_registry(tmp_path, monkeypatch)
    relative = "items/\u041c\u0435\u0442\u0435\u043e\u0440\u0438\u0442.png"
    photo = folder / relative
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"delete-recovery-photo")
    (folder / "items.json").write_text(json.dumps({
        "items": [{"name": "Meteorite", "images": [relative],
                   "image": relative}],
    }, ensure_ascii=False), encoding="utf-8")
    staging = tmp_path / ".profile-delete-interrupted"
    folder.rename(staging)
    _write_transaction_journal(tmp_path, {
        "version": 1, "operation": "delete", "phase": "directory_staged",
        "profile": "Alpha", "target": str(folder), "staging": str(staging),
    })

    config_module.recover_profile_operations()

    assert folder.is_dir() and not staging.exists()
    assert (folder / relative).read_bytes() == b"delete-recovery-photo"
    restored = json.loads((folder / "items.json").read_text(encoding="utf-8"))
    assert restored["items"][0]["images"] == [relative]
    assert not (tmp_path / ".profile-operation.json").exists()


def test_recovery_of_interrupted_restore_rolls_back_original_photo_tree(
        tmp_path, monkeypatch):
    folder = _prepare_profile_registry(tmp_path, monkeypatch)
    relative = "items/nested/\u0420\u0435\u0434\u043a\u043e\u0435.png"
    photo = folder / relative
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"original-photo")
    (folder / "items.json").write_text(json.dumps({
        "items": [{"name": "Original", "images": [relative],
                   "image": relative}],
    }, ensure_ascii=False), encoding="utf-8")
    rollback = tmp_path / "profiles" / ".profile-rollback-crash"
    folder.rename(rollback)
    staging = tmp_path / "profiles" / ".profile-restore-crash"
    staging.mkdir()
    (staging / "items.json").write_text('{"items":[]}', encoding="utf-8")
    _write_transaction_journal(tmp_path, {
        "version": 1, "operation": "restore", "phase": "old_moved",
        "profile": "Alpha", "target": str(folder), "staging": str(staging),
        "rollback": str(rollback), "created_at": "test",
    })

    config_module.recover_profile_operations()

    assert folder.is_dir() and not rollback.exists()
    assert (folder / relative).read_bytes() == b"original-photo"
    restored = json.loads((folder / "items.json").read_text(encoding="utf-8"))
    assert restored["items"][0]["image"] == relative
    # Incomplete staging is intentionally retained for inspection.
    assert staging.is_dir()
    assert not (tmp_path / ".profile-operation.json").exists()


def test_recovery_preserves_non_directory_restore_rollback(tmp_path, monkeypatch):
    _prepare_profile_registry(tmp_path, monkeypatch)
    target = tmp_path / "profiles" / "Alpha"
    original = tmp_path / "profiles" / ".profile-original-preserved"
    target.rename(original)
    rollback = tmp_path / "profiles" / ".profile-rollback-corrupt"
    rollback.write_text("preserve-corrupt-rollback", encoding="utf-8")
    staging = tmp_path / "profiles" / ".profile-restore-incomplete"
    staging.mkdir()
    (staging / "items.json").write_text('{"items":[]}', encoding="utf-8")
    _write_transaction_journal(tmp_path, {
        "version": 1, "operation": "restore", "phase": "old_moved",
        "profile": "Alpha", "target": str(target), "staging": str(staging),
        "rollback": str(rollback), "created_at": "test",
    })

    findings = config_module.recover_profile_operations()

    assert not target.exists()
    assert original.is_dir()
    assert rollback.read_text(encoding="utf-8") == "preserve-corrupt-rollback"
    assert staging.is_dir()
    assert (tmp_path / ".profile-operation.json").is_file()
    assert any("\u043f\u043e\u0432\u0440\u0435\u0436\u0434\u0451\u043d" in finding for finding in findings)


def _write_profile_backup_archive(path, files, profile_name="Alpha"):
    import hashlib
    import zipfile

    manifest = {
        "format": "ChestScannerFullProfileBackup",
        "version": config_module._PROFILE_BACKUP_VERSION,
        "profile_name": profile_name,
        "created_at": "test",
        "files": {
            name: {"size": len(content),
                   "sha256": hashlib.sha256(content).hexdigest()}
            for name, content in files.items()
        },
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, content in files.items():
            archive.writestr(name, content)


def test_profile_backup_rejects_case_insensitive_duplicate_names(tmp_path):
    import pytest

    archive = tmp_path / "case-duplicate.chestbackup"
    _write_profile_backup_archive(archive, {
        "items.json": b'{"items":[]}',
        "results/Scan.json": b"one",
        "results/scan.json": b"two",
    })
    with pytest.raises(ValueError, match="повторяющиеся"):
        config_module._read_profile_backup(archive, "Alpha")


def test_profile_backup_rejects_zip_slip_and_windows_special_paths(tmp_path):
    import pytest

    for unsafe in ("../outside.txt", "results/CON.txt", "results/file.txt:stream",
                   "results/trailing./file.txt", "items\\\\..\\\\outside.png",
                   "results"):
        archive = tmp_path / f"unsafe-{len(unsafe)}.chestbackup"
        _write_profile_backup_archive(archive, {
            "items.json": b'{"items":[]}',
            unsafe: b"unsafe",
        })
        with pytest.raises(ValueError, match="небезопасный|путь"):
            config_module._read_profile_backup(archive, "Alpha")


def test_profile_backup_rejects_excessive_file_count(tmp_path, monkeypatch):
    import pytest

    archive = tmp_path / "too-many.chestbackup"
    _write_profile_backup_archive(archive, {
        "items.json": b'{"items":[]}',
        "results/a.json": b"a",
        "results/b.json": b"b",
    })
    monkeypatch.setattr(config_module, "_PROFILE_BACKUP_MAX_ENTRIES", 3)
    with pytest.raises(ValueError, match="слишком много"):
        config_module._read_profile_backup(archive, "Alpha")


def test_profile_backup_rejects_oversized_entry(tmp_path, monkeypatch):
    import pytest

    archive = tmp_path / "oversized.chestbackup"
    _write_profile_backup_archive(archive, {
        "items.json": b'{"items":[]}',
        "results/large.bin": b"x" * 100,
    })
    monkeypatch.setattr(config_module, "_PROFILE_BACKUP_MAX_FILE_BYTES", 64)
    with pytest.raises(ValueError, match="512 МБ"):
        config_module._read_profile_backup(archive, "Alpha")


def test_profile_backup_rejects_suspicious_compression_ratio(tmp_path, monkeypatch):
    import pytest

    archive = tmp_path / "zip-bomb.chestbackup"
    _write_profile_backup_archive(archive, {
        "items.json": b'{"items":[]}',
        "results/repetitive.bin": b"0" * 10000,
    })
    monkeypatch.setattr(config_module, "_PROFILE_BACKUP_MAX_COMPRESSION_RATIO", 2)
    with pytest.raises(ValueError, match="сжатия файла"):
        config_module._read_profile_backup(archive, "Alpha")


def test_profile_backup_rejects_invalid_items_schema_and_missing_icon(tmp_path):
    import pytest

    invalid = tmp_path / "invalid-items.chestbackup"
    _write_profile_backup_archive(invalid, {"items.json": b'{"items":{}}'})
    with pytest.raises(ValueError, match="массив items"):
        config_module._read_profile_backup(invalid, "Alpha")

    missing_icon = tmp_path / "missing-icon.chestbackup"
    _write_profile_backup_archive(missing_icon, {
        "items.json": json.dumps({
            "items": [{"name": "Stone", "images": ["items/stone.png"]}],
        }).encode("utf-8"),
    })
    with pytest.raises(ValueError, match="отсутствует иконка"):
        config_module._read_profile_backup(missing_icon, "Alpha")


def test_profile_backup_rejects_invalid_unknown_queue(tmp_path):
    import pytest

    archive = tmp_path / "invalid-queue.chestbackup"
    _write_profile_backup_archive(archive, {
        "items.json": b'{"items":[]}',
        "unknown_items.json": b'{"items":"not-a-list"}',
    })
    with pytest.raises(ValueError, match="очереди"):
        config_module._read_profile_backup(archive, "Alpha")


def test_failed_backup_does_not_prune_previous_verified_backups(
        tmp_path, monkeypatch):
    import pytest

    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("before")), encoding="utf-8")
    first = config_module.create_profile_backup("Alpha")
    (source / "items.json").write_text('{"items":{}}', encoding="utf-8")
    with pytest.raises(ValueError, match="массив items"):
        config_module.create_profile_backup("Alpha")
    assert config_module.list_profile_backups("Alpha") == [first]
    assert config_module._read_profile_backup(first, "Alpha")


def test_profile_restore_interruption_after_new_directory_install_preserves_both(
        tmp_path, monkeypatch):
    import pytest

    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("restored")), encoding="utf-8")
    backup = config_module.create_profile_backup("Alpha")
    (source / "config.json").write_text(
        json.dumps(_profile_config("current")), encoding="utf-8")

    real_rename = config_module.os.rename
    calls = 0

    def interrupt_after_install(src, dst):
        nonlocal calls
        calls += 1
        result = real_rename(src, dst)
        if calls == 2:
            raise SystemExit(77)
        return result

    monkeypatch.setattr(config_module.os, "rename", interrupt_after_install)
    with pytest.raises(SystemExit):
        config_module.restore_profile_backup("Alpha", backup)

    target = tmp_path / "profiles" / "Alpha"
    rollback_dirs = list((tmp_path / "profiles").glob(".profile-rollback-*"))
    assert target.is_dir()
    assert len(rollback_dirs) == 1
    assert json.loads((target / "config.json").read_text(
        encoding="utf-8"))["game_window_title"] == "restored"
    assert json.loads((rollback_dirs[0] / "config.json").read_text(
        encoding="utf-8"))["game_window_title"] == "current"

    monkeypatch.setattr(config_module.os, "rename", real_rename)
    findings = config_module.recover_profile_operations()
    assert target.is_dir() and rollback_dirs[0].is_dir()
    assert (tmp_path / ".profile-operation.json").is_file()
    assert any("неоднозначно" in item for item in findings)


def test_profile_restore_startup_finishes_committed_rollback_cleanup(
        tmp_path, monkeypatch):
    import pytest

    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("restored")), encoding="utf-8")
    backup = config_module.create_profile_backup("Alpha")
    (source / "config.json").write_text(
        json.dumps(_profile_config("current")), encoding="utf-8")

    real_rmtree = config_module.shutil.rmtree

    def interrupt_rollback_cleanup(path, *args, **kwargs):
        if Path(path).name.startswith(".profile-rollback-"):
            raise SystemExit(79)
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(config_module.shutil, "rmtree", interrupt_rollback_cleanup)
    with pytest.raises(SystemExit):
        config_module.restore_profile_backup("Alpha", backup)

    target = tmp_path / "profiles" / "Alpha"
    rollback_dirs = list((tmp_path / "profiles").glob(".profile-rollback-*"))
    assert target.is_dir() and len(rollback_dirs) == 1
    assert json.loads((target / "config.json").read_text(
        encoding="utf-8"))["game_window_title"] == "restored"
    assert json.loads((rollback_dirs[0] / "config.json").read_text(
        encoding="utf-8"))["game_window_title"] == "current"
    journal_path = tmp_path / ".profile-operation.json"
    assert json.loads(journal_path.read_text(encoding="utf-8"))["phase"] == "committed"

    monkeypatch.setattr(config_module.shutil, "rmtree", real_rmtree)
    findings = config_module.recover_profile_operations()

    assert target.is_dir() and not rollback_dirs[0].exists()
    assert json.loads((target / "config.json").read_text(
        encoding="utf-8"))["game_window_title"] == "restored"
    assert not journal_path.exists()
    assert any("\u0417\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u0430" in item
               for item in findings)


def test_profile_restore_partial_rollback_cleanup_keeps_journal_for_retry(
        tmp_path, monkeypatch):

    source = _prepare_profile_registry(tmp_path, monkeypatch)
    old_nested = source / "results" / "nested" / "old-result.json"
    old_nested.parent.mkdir(parents=True)
    old_nested.write_text("old-result", encoding="utf-8")
    (source / "config.json").write_text(
        json.dumps(_profile_config("restored")), encoding="utf-8")
    backup = config_module.create_profile_backup("Alpha")
    (source / "config.json").write_text(
        json.dumps(_profile_config("current")), encoding="utf-8")

    real_rmtree = config_module.shutil.rmtree
    interrupted = False

    def partially_remove_rollback(path, *args, **kwargs):
        nonlocal interrupted
        candidate = Path(path)
        if candidate.name.startswith(".profile-rollback-") and not interrupted:
            interrupted = True
            files = [item for item in candidate.rglob("*") if item.is_file()]
            assert files
            files[0].unlink()
            raise OSError("simulated partial rollback cleanup")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(config_module.shutil, "rmtree", partially_remove_rollback)
    assert config_module.restore_profile_backup("Alpha", backup) is True

    target = tmp_path / "profiles" / "Alpha"
    rollback_dirs = list((tmp_path / "profiles").glob(".profile-rollback-*"))
    journal_path = tmp_path / ".profile-operation.json"
    assert target.is_dir() and len(rollback_dirs) == 1
    assert json.loads((target / "config.json").read_text(
        encoding="utf-8"))["game_window_title"] == "restored"
    assert rollback_dirs[0].is_dir()
    assert json.loads(journal_path.read_text(encoding="utf-8"))["phase"] == "committed"

    monkeypatch.setattr(config_module.shutil, "rmtree", real_rmtree)
    findings = config_module.recover_profile_operations()

    assert target.is_dir() and not rollback_dirs[0].exists()
    assert json.loads((target / "config.json").read_text(
        encoding="utf-8"))["game_window_title"] == "restored"
    assert not journal_path.exists()
    assert any("\u0417\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u0430" in item
               for item in findings)


def test_backup_creation_failure_keeps_previous_backup_and_cleans_temp(
        tmp_path, monkeypatch):
    import pytest

    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("first")), encoding="utf-8")
    first = config_module.create_profile_backup("Alpha")
    (source / "config.json").write_text(
        json.dumps(_profile_config("second")), encoding="utf-8")

    def fail_replace(_source, _destination):
        raise OSError("simulated atomic publish failure")

    monkeypatch.setattr(config_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        config_module.create_profile_backup("Alpha")
    assert config_module.list_profile_backups("Alpha") == [first]
    assert not list(Path(first).parent.glob("*.tmp-*"))
    assert config_module._read_profile_backup(first, "Alpha")



def test_profile_backup_rejects_case_insensitive_path_collision(tmp_path):
    archive = tmp_path / "case-distinct.chestbackup"
    _write_profile_backup_archive(archive, {
        "items.json": b'{"items":[]}',
        "results/Scan.JSON": b"upper",
        "results/scan.json": b"lower",
    })
    import pytest
    with pytest.raises(ValueError, match="повторяющиеся"):
        config_module._read_profile_backup(archive, "Alpha")



def test_profile_backup_rejects_malformed_config_json(tmp_path):
    import pytest

    archive = tmp_path / "bad-config.chestbackup"
    _write_profile_backup_archive(archive, {
        "config.json": b"[]",
        "items.json": b'{"items":[]}',
    })
    with pytest.raises(ValueError, match="config.json"):
        config_module._read_profile_backup(archive, "Alpha")


def test_profile_backup_rejects_corrupted_zip_crc(tmp_path):
    import zipfile

    import pytest

    archive = tmp_path / "crc.chestbackup"
    _write_profile_backup_archive(archive, {
        "items.json": b'{"items":[]}',
        "results/payload.bin": b"CRC-protected-data",
    })
    with zipfile.ZipFile(archive) as zipped:
        info = zipped.getinfo("results/payload.bin")
    raw = bytearray(archive.read_bytes())
    data_offset = info.header_offset + 30 + len(info.filename.encode("utf-8")) + len(info.extra)
    raw[data_offset + 2] ^= 0x20
    archive.write_bytes(raw)
    with pytest.raises(ValueError, match="повреждён|Контрольная сумма"):
        config_module._read_profile_backup(archive, "Alpha")


def test_profile_backup_rejects_missing_required_items_json(tmp_path):
    import pytest

    archive = tmp_path / "no-items.chestbackup"
    _write_profile_backup_archive(archive, {"config.json": b'{"ok":true}'})
    with pytest.raises(ValueError, match="items.json"):
        config_module._read_profile_backup(archive, "Alpha")



def test_profile_backup_uses_consistent_sqlite_snapshot_and_omits_sidecars(
        tmp_path, monkeypatch):
    import sqlite3
    import zipfile

    source = _prepare_profile_registry(tmp_path, monkeypatch)
    results = source / "results"
    results.mkdir()
    db_path = results / "chests.db"
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO snapshots (id) VALUES (1)")
    connection.commit()

    backup = config_module.create_profile_backup("Alpha")
    connection.execute("INSERT INTO snapshots (id) VALUES (2)")
    connection.commit()
    connection.close()

    payload = config_module._read_profile_backup(backup, "Alpha")
    snapshot_path = tmp_path / "restored-snapshot.db"
    snapshot_path.write_bytes(payload["results/chests.db"])
    snapshot = sqlite3.connect(snapshot_path)
    try:
        assert snapshot.execute(
            "SELECT id FROM snapshots ORDER BY id").fetchall() == [(1,)]
        assert snapshot.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        snapshot.close()

    with zipfile.ZipFile(backup) as archive:
        names = set(archive.namelist())
    assert "results/chests.db-wal" not in names
    assert "results/chests.db-shm" not in names
    assert "results/chests.json.lock" not in names


def test_profile_backup_refuses_missing_item_icon_before_publishing(
        tmp_path, monkeypatch):
    import pytest

    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "items.json").write_text(json.dumps({
        "items": [{"name": "Missing icon", "images": ["items/missing.png"]}],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="отсутствует иконка"):
        config_module.create_profile_backup("Alpha")
    assert config_module.list_profile_backups("Alpha") == []


def test_profile_operation_journal_replace_failure_preserves_previous_record(
        tmp_path, monkeypatch):
    import pytest

    _prepare_profile_registry(tmp_path, monkeypatch)
    journal_path = tmp_path / ".profile-operation.json"
    original = {"version": 1, "operation": "restore", "phase": "prepared",
                "profile": "Alpha", "marker": "previous"}
    journal_path.write_text(json.dumps(original), encoding="utf-8")
    real_replace = config_module.os.replace

    def fail_journal_replace(source, destination):
        if str(destination) == str(journal_path):
            raise OSError("journal replace failed")
        return real_replace(source, destination)

    monkeypatch.setattr(config_module.os, "replace", fail_journal_replace)
    with pytest.raises(OSError, match="journal replace failed"):
        config_module._write_profile_operation_journal({
            "version": 1, "operation": "restore", "phase": "old_moved",
            "profile": "Alpha", "marker": "new"})

    assert json.loads(journal_path.read_text(encoding="utf-8")) == original
    assert not list(tmp_path.glob(".profile-operation.json.tmp-*"))


def test_restore_cleans_staging_when_staging_file_fsync_fails(
        tmp_path, monkeypatch):
    import pytest

    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("restored")), encoding="utf-8")
    backup = config_module.create_profile_backup("Alpha")
    (source / "config.json").write_text(
        json.dumps(_profile_config("current")), encoding="utf-8")
    backups_before = config_module.list_profile_backups("Alpha")

    def fail_fsync(_descriptor):
        raise OSError("staging fsync failed")

    monkeypatch.setattr(config_module.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="staging fsync failed"):
        config_module.restore_profile_backup("Alpha", backup)

    assert json.loads((source / "config.json").read_text(
        encoding="utf-8"))["game_window_title"] == "current"
    assert source.is_dir()
    assert config_module.list_profile_backups("Alpha") == backups_before
    assert not list((tmp_path / "profiles").glob(".profile-restore-*"))
    assert not list((tmp_path / "profiles").glob(".profile-rollback-*"))
    assert not (tmp_path / ".profile-operation.json").exists()


def test_restore_leaves_profile_untouched_if_pre_restore_backup_fails(
        tmp_path, monkeypatch):
    import pytest

    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("restored")), encoding="utf-8")
    backup = config_module.create_profile_backup("Alpha")
    (source / "config.json").write_text(
        json.dumps(_profile_config("current")), encoding="utf-8")

    def fail_backup(_name):
        raise OSError("cannot create safety backup")

    monkeypatch.setattr(config_module, "create_profile_backup", fail_backup)
    with pytest.raises(OSError, match="safety backup"):
        config_module.restore_profile_backup("Alpha", backup)
    assert json.loads((source / "config.json").read_text(
        encoding="utf-8"))["game_window_title"] == "current"
    assert source.is_dir()
    assert not list((tmp_path / "profiles").glob(".profile-restore-*"))


def _write_portable_profile_archive(path, files, profile_name="Portable"):
    import hashlib
    import zipfile

    manifest = {
        "format": "ChestScannerProfile",
        "version": config_module._PROFILE_ARCHIVE_VERSION,
        "profile_name": profile_name,
        "files": {
            name: {"size": len(content),
                   "sha256": hashlib.sha256(content).hexdigest()}
            for name, content in files.items()
        },
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, content in files.items():
            archive.writestr(name, content)


def test_portable_profile_archive_rejects_case_insensitive_duplicates(tmp_path):
    import pytest

    archive = tmp_path / "case-duplicate.chestprofile"
    _write_portable_profile_archive(archive, {
        "config.json": b"{}",
        "items.json": b'{"items":[]}',
        "items/Stone.png": b"one",
        "items/stone.png": b"two",
    })
    with pytest.raises(ValueError, match="повторяющиеся"):
        config_module._read_portable_profile_archive(archive)


def test_portable_profile_archive_rejects_windows_and_traversal_paths(tmp_path):
    import pytest

    for unsafe in ("../outside.txt", "items/CON.png", "items/file.png:stream",
                   "items/trailing./x.png", "items\\\\..\\\\outside.png"):
        archive = tmp_path / f"portable-unsafe-{len(unsafe)}.zip"
        _write_portable_profile_archive(archive, {
            "config.json": b"{}",
            "items.json": b'{"items":[]}',
            unsafe: b"x",
        })
        with pytest.raises(ValueError, match="небезопасный путь"):
            config_module._read_portable_profile_archive(archive)


def test_portable_profile_archive_enforces_entry_file_and_ratio_limits(
        tmp_path, monkeypatch):
    import pytest

    archive = tmp_path / "portable-limits.zip"
    _write_portable_profile_archive(archive, {
        "config.json": b"{}",
        "items.json": b'{"items":[]}',
        "items/a.bin": b"a" * 1000,
    })
    monkeypatch.setattr(config_module, "_PROFILE_ARCHIVE_MAX_FILE_BYTES", 128)
    with pytest.raises(ValueError, match="32 МБ"):
        config_module._read_portable_profile_archive(archive)

    monkeypatch.setattr(config_module, "_PROFILE_ARCHIVE_MAX_FILE_BYTES", 1024 * 1024)
    monkeypatch.setattr(config_module, "_PROFILE_ARCHIVE_MAX_COMPRESSION_RATIO", 1)
    monkeypatch.setattr(config_module, "_PROFILE_ARCHIVE_MAX_ENTRIES", 3)
    too_many = tmp_path / "portable-many.zip"
    _write_portable_profile_archive(too_many, {
        "config.json": b"{}",
        "items.json": b'{"items":[]}',
        "items/one.bin": b"1",
    })
    with pytest.raises(ValueError, match="слишком много"):
        config_module._read_portable_profile_archive(too_many)
    monkeypatch.setattr(config_module, "_PROFILE_ARCHIVE_MAX_ENTRIES", 4096)
    compressed = tmp_path / "portable-ratio.zip"
    import zipfile
    with zipfile.ZipFile(compressed, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr("manifest.json", "{}")
        zipped.writestr("config.json", "{}")
        zipped.writestr("items.json", '{"items":[]}')
        zipped.writestr("items/repetitive.bin", b"0" * 10000)
    with pytest.raises(ValueError, match="сжатия файла"):
        config_module._read_portable_profile_archive(compressed)


def test_portable_profile_archive_rejects_invalid_items_and_missing_icons(tmp_path):
    import pytest

    invalid = tmp_path / "portable-invalid-items.zip"
    _write_portable_profile_archive(invalid, {
        "config.json": b"{}",
        "items.json": b'{"items":{}}',
    })
    with pytest.raises(ValueError, match="массив items"):
        config_module._read_portable_profile_archive(invalid)

    missing = tmp_path / "portable-missing-icon.zip"
    _write_portable_profile_archive(missing, {
        "config.json": b"{}",
        "items.json": json.dumps({
            "items": [{"name": "Stone", "images": ["items/stone.png"]}],
        }).encode("utf-8"),
    })
    with pytest.raises(ValueError, match="отсутствует иконка"):
        config_module._read_portable_profile_archive(missing)


def test_portable_profile_export_refuses_missing_icons_without_replacing_archive(
        tmp_path, monkeypatch):
    import pytest

    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("portable")), encoding="utf-8")
    (source / "items.json").write_text(json.dumps({
        "items": [{"name": "Stone", "images": ["items/missing.png"]}],
    }), encoding="utf-8")
    destination = tmp_path / "existing.chestprofile"
    destination.write_bytes(b"previous archive")
    with pytest.raises(ValueError, match="отсутствует иконка"):
        config_module.export_profile_archive("Alpha", destination)
    assert destination.read_bytes() == b"previous archive"


def test_portable_profile_export_atomic_publish_failure_preserves_old_archive(
        tmp_path, monkeypatch):
    import pytest

    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("portable")), encoding="utf-8")
    destination = tmp_path / "existing.chestprofile"
    destination.write_bytes(b"old")

    def fail_replace(_source, _destination):
        raise OSError("portable publish failure")

    monkeypatch.setattr(config_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="portable publish"):
        config_module.export_profile_archive("Alpha", destination)
    assert destination.read_bytes() == b"old"
    assert not list(tmp_path.glob("existing.chestprofile.tmp-*"))


def test_portable_profile_import_crash_after_install_keeps_journal_and_data(
        tmp_path, monkeypatch):
    import pytest

    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("portable")), encoding="utf-8")
    archive = tmp_path / "portable.chestprofile"
    config_module.export_profile_archive("Alpha", archive)

    original_save_profiles = config_module.save_profiles

    def crash_after_install(_data):
        raise SystemExit(77)

    monkeypatch.setattr(config_module, "save_profiles", crash_after_install)
    with pytest.raises(SystemExit):
        config_module.import_profile_archive(archive, "Imported")

    target = tmp_path / "profiles" / "Imported"
    assert target.is_dir()
    assert (target / "items.json").is_file()
    assert (tmp_path / ".profile-operation.json").is_file()

    monkeypatch.setattr(config_module, "save_profiles", original_save_profiles)
    findings = config_module.recover_profile_operations()
    assert target.is_dir()
    assert (tmp_path / ".profile-operation.json").is_file()
    assert any("неоднозначно" in item for item in findings)


def test_portable_profile_archive_rejects_corrupted_crc(tmp_path):
    import zipfile

    import pytest

    archive = tmp_path / "portable-crc.zip"
    _write_portable_profile_archive(archive, {
        "config.json": b"{}",
        "items.json": b'{"items":[]}',
        "items/payload.bin": b"CRC-protected-content",
    })
    with zipfile.ZipFile(archive) as zipped:
        info = zipped.getinfo("items/payload.bin")
    raw = bytearray(archive.read_bytes())
    data_offset = info.header_offset + 30 + len(info.filename.encode("utf-8")) + len(info.extra)
    raw[data_offset + 2] ^= 0x40
    archive.write_bytes(raw)
    with pytest.raises(ValueError, match="повреждён|Контрольная сумма"):
        config_module._read_portable_profile_archive(archive)


def test_portable_profile_archive_round_trips_unusual_photo_filenames(
        tmp_path, monkeypatch):
    """Unicode and punctuation in photo names survive repeated transfers."""
    from chest_scanner import commons

    root_a = tmp_path / "install-a"
    profile_a = root_a / "profiles" / "Alpha"
    profile_a.mkdir(parents=True)
    monkeypatch.setattr(paths, "BASE_DIR", str(root_a))
    monkeypatch.setattr(paths, "PROFILES_FILE", str(root_a / "profiles.json"))
    monkeypatch.setattr(paths, "PROFILE_DIR", str(profile_a))
    (profile_a / "config.json").write_text(
        json.dumps(_profile_config("portable")), encoding="utf-8")

    photo_paths = [
        "items/.hidden # (100%) - \u0444\u043e\u0442\u043e.png",
        "items/nested \u043f\u0430\u043f\u043a\u0430/\u00e9tude (\u03b2) #2%.webp",
    ]
    photo_bytes = [b"hidden-photo-bytes", b"nested-photo-bytes"]
    for relative, content in zip(photo_paths, photo_bytes):
        destination = profile_a / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    item_data = {
        "version": 3,
        "items": [
            {"name": "First", "images": [photo_paths[0], photo_paths[1]],
             "image": photo_paths[0]},
            {"name": "Legacy", "image": photo_paths[1].replace("/", "\\")},
        ],
    }
    (profile_a / "items.json").write_text(
        json.dumps(item_data, ensure_ascii=False), encoding="utf-8")
    (root_a / "profiles.json").write_text(json.dumps({
        "version": 1, "active": "Default",
        "profiles": [
            {"name": "Default", "directory": "."},
            {"name": "Alpha", "directory": "profiles/Alpha"},
        ],
    }), encoding="utf-8")

    archive_ab = tmp_path / "a-to-b.chestprofile"
    config_module.export_profile_archive("Alpha", archive_ab)

    root_b = tmp_path / "install-b"
    root_b.mkdir()
    monkeypatch.setattr(paths, "BASE_DIR", str(root_b))
    monkeypatch.setattr(paths, "PROFILES_FILE", str(root_b / "profiles.json"))
    monkeypatch.setattr(paths, "PROFILE_DIR", str(root_b))
    (root_b / "profiles.json").write_text(json.dumps({
        "version": 1, "active": "Default",
        "profiles": [{"name": "Default", "directory": "."}],
    }), encoding="utf-8")
    entry_b = config_module.import_profile_archive(archive_ab, "Imported B")
    profile_b = root_b / entry_b["directory"]
    config_module.activate_profile("Imported B")

    archive_bc = tmp_path / "b-to-c.chestprofile"
    config_module.export_profile_archive("Imported B", archive_bc)

    root_c = tmp_path / "install-c"
    root_c.mkdir()
    monkeypatch.setattr(paths, "BASE_DIR", str(root_c))
    monkeypatch.setattr(paths, "PROFILES_FILE", str(root_c / "profiles.json"))
    monkeypatch.setattr(paths, "PROFILE_DIR", str(root_c))
    (root_c / "profiles.json").write_text(json.dumps({
        "version": 1, "active": "Default",
        "profiles": [{"name": "Default", "directory": "."}],
    }), encoding="utf-8")
    entry_c = config_module.import_profile_archive(archive_bc, "Imported C")
    profile_c = root_c / entry_c["directory"]
    config_module.activate_profile("Imported C")
    imported_items = json.loads((profile_c / "items.json").read_text(
        encoding="utf-8"))

    assert imported_items == item_data
    assert imported_items["items"][0]["images"] == photo_paths
    assert imported_items["items"][0]["image"] == photo_paths[0]
    assert imported_items["items"][1]["image"] == photo_paths[1].replace("/", "\\")
    for relative, content in zip(photo_paths, photo_bytes):
        assert (profile_b / relative).read_bytes() == content
        assert (profile_c / relative).read_bytes() == content
        assert Path(commons.absolute_path(relative)) == profile_c / relative
    assert str(root_a) not in json.dumps(imported_items)
    assert str(root_b) not in json.dumps(imported_items)



@pytest.mark.parametrize("damage", ["missing_manifest", "missing_photo", "bad_photo_digest"])
def test_damaged_portable_import_is_atomic_and_preserves_existing_profile(
        tmp_path, monkeypatch, damage):
    import zipfile

    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("safe-existing")), encoding="utf-8")
    (source / "items" / "important.png").parent.mkdir(parents=True, exist_ok=True)
    (source / "items" / "important.png").write_bytes(b"trusted-photo")
    (source / "items.json").write_text(json.dumps({
        "items": [{"name": "Valuable", "image": "items/important.png"}],
    }), encoding="utf-8")
    valid = tmp_path / "valid.chestprofile"
    config_module.export_profile_archive("Alpha", valid)

    damaged = tmp_path / f"damaged-{damage}.chestprofile"
    with zipfile.ZipFile(valid, "r") as original:
        entries = [(info.filename, original.read(info.filename))
                   for info in original.infolist()]
    with zipfile.ZipFile(damaged, "w", compression=zipfile.ZIP_STORED) as output:
        for filename, content in entries:
            if damage == "missing_manifest" and filename == "manifest.json":
                continue
            if damage == "missing_photo" and filename == "items/important.png":
                continue
            if damage == "bad_photo_digest" and filename == "items/important.png":
                content = b"tampered-photo"
            output.writestr(filename, content)

    registry_path = Path(paths.PROFILES_FILE)
    registry_before = registry_path.read_bytes()
    config_before = (source / "config.json").read_bytes()
    items_before = (source / "items.json").read_bytes()
    photo_before = (source / "items" / "important.png").read_bytes()
    outside = tmp_path / "outside-sentinel.txt"
    outside.write_bytes(b"must-not-change")

    with pytest.raises(ValueError):
        config_module.import_profile_archive(damaged, "Damaged Import")

    assert registry_path.read_bytes() == registry_before
    assert (source / "config.json").read_bytes() == config_before
    assert (source / "items.json").read_bytes() == items_before
    assert (source / "items" / "important.png").read_bytes() == photo_before
    assert outside.read_bytes() == b"must-not-change"
    assert not (tmp_path / "profiles" / "Damaged Import").exists()
    assert not list((tmp_path / "profiles").glob(".profile-import-*"))
    assert all(entry["name"] != "Damaged Import"
               for entry in config_module.load_profiles()["profiles"])


def test_concurrent_profile_creations_keep_both_registry_entries(
        tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("concurrent")), encoding="utf-8")
    (source / "items.json").write_text(
        json.dumps({"items": []}), encoding="utf-8")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(config_module.create_profile_data, name, source)
                   for name in ("Concurrent A", "Concurrent B")]
        created = [future.result(timeout=10) for future in futures]

    assert {entry["name"] for entry in created} == {"Concurrent A", "Concurrent B"}
    registry = config_module.load_profiles()
    registered = {entry["name"] for entry in registry["profiles"]}
    assert {"Concurrent A", "Concurrent B"} <= registered
    assert all((tmp_path / entry["directory"]).is_dir() for entry in created)
    assert not (tmp_path / ".profile-operation.json").exists()


def test_concurrent_creation_of_same_profile_is_serialized(
        tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    source = _prepare_profile_registry(tmp_path, monkeypatch)
    (source / "config.json").write_text(
        json.dumps(_profile_config("same-name")), encoding="utf-8")

    def create():
        try:
            return config_module.create_profile_data("Concurrent", source)
        except ValueError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result(timeout=10)
                   for future in [executor.submit(create), executor.submit(create)]]

    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    registry = config_module.load_profiles()
    assert sum(entry["name"] == "Concurrent" for entry in registry["profiles"]) == 1
    assert (tmp_path / "profiles" / "Concurrent").is_dir()
    assert not (tmp_path / ".profile-operation.json").exists()



def test_concurrent_runtime_profile_switches_keep_paths_and_registry_consistent(
        tmp_path, monkeypatch):
    import threading
    import time

    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(paths, "PROFILES_FILE", str(tmp_path / "profiles.json"))
    registry = {
        "version": 1, "active": "Default",
        "profiles": [
            {"name": "Default", "directory": "."},
            {"name": "Alpha", "directory": "profiles/Alpha"},
        ],
    }
    (tmp_path / "profiles.json").write_text(
        json.dumps(registry), encoding="utf-8")
    (tmp_path / "profiles" / "Alpha").mkdir(parents=True)
    reader = ChestReader.__new__(ChestReader)
    reader.template_cache = template_cache
    first_reload_entered = threading.Event()
    release_first_reload = threading.Event()
    second_call_started = threading.Event()
    observed = []
    failures = []

    def controlled_reload():
        active = config_module.load_profiles()["active"]
        observed.append((active, paths.PROFILE_DIR))
        if active == "Alpha":
            first_reload_entered.set()
            if not release_first_reload.wait(5):
                raise TimeoutError("first profile reload was not released")
            # The second switch must not publish its paths while this reload
            # is still reading the first profile.
            observed.append((config_module.load_profiles()["active"],
                             paths.PROFILE_DIR))

    monkeypatch.setattr(reader, "reload_profile", controlled_reload)

    def switch(name):
        try:
            if name == "Default":
                second_call_started.set()
            reader.switch_profile(name)
        except Exception as exc:  # surfaced in the main test thread below
            failures.append(exc)

    first = threading.Thread(target=switch, args=("Alpha",))
    second = threading.Thread(target=switch, args=("Default",))
    first.start()
    assert first_reload_entered.wait(5)
    second.start()
    assert second_call_started.wait(5)
    time.sleep(0.1)
    assert json.loads((tmp_path / "profiles.json").read_text(
        encoding="utf-8"))["active"] == "Alpha"
    assert Path(paths.CONFIG_FILE).parent == tmp_path / "profiles" / "Alpha"
    release_first_reload.set()
    first.join(5)
    second.join(5)

    assert not first.is_alive() and not second.is_alive()
    assert not failures
    assert observed[0][0] == "Alpha"
    assert observed[1][0] == "Alpha"
    assert config_module.load_profiles()["active"] == "Default"
    assert paths.PROFILE_DIR == str(tmp_path)


def test_json_save_waits_for_profile_switch_to_finish(tmp_path, monkeypatch):
    import threading
    import time

    _isolate_profile_paths(monkeypatch)
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(paths, "PROFILES_FILE", str(tmp_path / "profiles.json"))
    registry = {
        "version": 1, "active": "Default",
        "profiles": [
            {"name": "Default", "directory": "."},
            {"name": "Alpha", "directory": "profiles/Alpha"},
        ],
    }
    (tmp_path / "profiles.json").write_text(
        json.dumps(registry), encoding="utf-8")
    (tmp_path / "profiles" / "Alpha").mkdir(parents=True)
    reader = ChestReader.__new__(ChestReader)
    reader.template_cache = template_cache
    reload_entered = threading.Event()
    release_reload = threading.Event()
    save_finished = threading.Event()
    failures = []

    def controlled_reload():
        reload_entered.set()
        if not release_reload.wait(5):
            raise TimeoutError("profile reload was not released")

    monkeypatch.setattr(reader, "reload_profile", controlled_reload)

    def switch():
        try:
            reader.switch_profile("Alpha")
        except Exception as exc:
            failures.append(exc)

    def save_profile_config():
        try:
            target = paths.CONFIG_FILE
            save_json(target, {"profile_name": "Alpha", "saved": True})
        except Exception as exc:
            failures.append(exc)
        finally:
            save_finished.set()

    switch_thread = threading.Thread(target=switch)
    switch_thread.start()
    assert reload_entered.wait(5)
    target_path = paths.CONFIG_FILE
    save_thread = threading.Thread(target=save_profile_config)
    save_thread.start()
    time.sleep(0.1)
    assert not save_finished.is_set()
    assert not Path(target_path).exists()

    release_reload.set()
    switch_thread.join(5)
    save_thread.join(5)

    assert not switch_thread.is_alive() and not save_thread.is_alive()
    assert not failures
    assert save_finished.is_set()
    assert json.loads(Path(target_path).read_text(encoding="utf-8")) == {
        "profile_name": "Alpha", "saved": True}
    assert not (tmp_path / "config.json").exists()
    assert config_module.load_profiles()["active"] == "Alpha"


def test_profile_backup_root_rejects_symlinked_backup_parent(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    backup_parent = tmp_path / ".profile_backups"
    real_islink = config_module.os.path.islink

    def report_parent_as_link(path):
        if Path(path) == backup_parent:
            return True
        return real_islink(path)

    monkeypatch.setattr(config_module.os.path, "islink", report_parent_as_link)
    with pytest.raises(ValueError, match="Invalid profile backup directory"):
        config_module._profile_backup_root("Alpha")


def test_profile_backup_root_rejects_symlinked_profile_leaf(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    backup_parent = tmp_path / ".profile_backups"
    backup_parent.mkdir()
    expected_root = Path(config_module._profile_backup_root("Alpha"))
    other_root = backup_parent / "Other-profile-backups"
    other_root.mkdir()
    real_islink = config_module.os.path.islink

    def report_leaf_as_link(path):
        if Path(path) == expected_root:
            return True
        return real_islink(path)

    monkeypatch.setattr(config_module.os.path, "islink", report_leaf_as_link)
    with pytest.raises(ValueError, match="Invalid profile backup directory"):
        config_module._profile_backup_root("Alpha")
    assert list(other_root.iterdir()) == []


def test_profile_switch_waits_for_active_scan_lock():
    import threading
    import time

    from chest_scanner.scanner.base import ScannerBaseMixin

    reader = object.__new__(type("Reader", (ScannerBaseMixin,), {}))
    reader._scan_lock = threading.Lock()
    reader._scan_lock.acquire()
    switched = threading.Event()
    reader._switch_profile_locked = lambda name: switched.set()
    worker = threading.Thread(target=reader.switch_profile, args=("Other",))
    worker.start()
    time.sleep(0.05)
    assert not switched.is_set()

    reader._scan_lock.release()
    worker.join(5)
    assert not worker.is_alive()
    assert switched.is_set()
