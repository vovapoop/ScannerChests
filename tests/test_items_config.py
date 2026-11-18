"""Тесты предметов (items) и конфигурации (config)."""
import pytest

from chest_scanner import paths
from chest_scanner.config import validate_config
from chest_scanner.items import is_safe_icon_path, normalize_items_db, sanitize_filename


def test_sanitize_filename_removes_bad_chars():
    assert sanitize_filename('Ал:маз*?') == 'Ал маз'
    assert sanitize_filename('  name  ') == 'name'


def test_sanitize_reserved_names():
    assert sanitize_filename('CON').startswith('_')


def test_sanitize_empty_returns_item():
    assert sanitize_filename('   ') == 'item'


def test_threshold_migration_from_0_82():
    """Старый дефолт порога шаблона сундука мигрирует на 0.88."""
    cfg, warns = validate_config({"chest_template_threshold": 0.82})
    assert cfg["chest_template_threshold"] == 0.88
    assert any("chest_template_threshold" in w for w in warns)


def test_threshold_custom_value_not_migrated():
    """Явно настроенный пользователем порог (не 0.82) не трогаем."""
    cfg, warns = validate_config({"chest_template_threshold": 0.95})
    assert cfg["chest_template_threshold"] == 0.95
    assert not any("chest_template_threshold" in w for w in warns)


def test_is_safe_icon_path():
    assert is_safe_icon_path('items/Алмаз_1.png')
    assert is_safe_icon_path('unknown_items/x.png')
    assert not is_safe_icon_path('../evil.png')
    assert not is_safe_icon_path('items/../../evil.png')
    assert not is_safe_icon_path('C:/x.png')
    assert not is_safe_icon_path('assets/something.png')
    assert not is_safe_icon_path('')
    assert not is_safe_icon_path(None)


def test_is_safe_icon_path_rejects_symlink_escape(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    outside = tmp_path / "outside"
    (profile / "items").mkdir(parents=True)
    outside.mkdir()
    (outside / "secret.png").write_bytes(b"not-an-image")
    monkeypatch.setattr(paths, "PROFILE_DIR", str(profile))
    link = profile / "items" / "external.png"
    try:
        link.symlink_to(outside / "secret.png")
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are unavailable on this platform")

    assert not is_safe_icon_path("items/external.png")


def test_is_safe_icon_path_rejects_symlinked_asset_directory(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    outside = tmp_path / "outside"
    profile.mkdir()
    outside.mkdir()
    (outside / "icon.png").write_bytes(b"not-an-image")
    monkeypatch.setattr(paths, "PROFILE_DIR", str(profile))
    try:
        (profile / "items").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are unavailable on this platform")

    assert not is_safe_icon_path("items/icon.png")


def test_is_safe_icon_path_allows_missing_photo_inside_profile(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    (profile / "items").mkdir(parents=True)
    monkeypatch.setattr(paths, "PROFILE_DIR", str(profile))

    assert is_safe_icon_path("items/missing.png")
    normalized = normalize_items_db({"items": [{
        "id": "keep", "name": "Keep me", "image": "items/missing.png"
    }]})
    assert normalized["items"][0]["id"] == "keep"
    assert normalized["items"][0]["image"] == "items/missing.png"



def test_normalize_items_db_filters_unsafe_paths():
    db = {"items": [{
        "id": "1", "name": "Тест",
        "images": ["items/ok.png", "../../evil.png", "assets/other.png"],
        "image": "items/ok.png", "stack_size": 64,
    }]}
    out = normalize_items_db(db)
    assert out["items"][0]["images"] == ["items/ok.png"]
    assert out["items"][0]["image"] == "items/ok.png"


def test_normalize_items_db_repairs_missing_fields():
    assert normalize_items_db({"items": [{"name": "  "}]})["items"] == []
    out = normalize_items_db({"items": [{"name": "Алмаз", "stack_size": "x"}]})
    assert out["items"][0]["stack_size"] == 64
    assert out["items"][0]["id"]
    assert "aliases" not in out["items"][0]


def test_validate_config_repairs_broken_values():
    cfg, warns = validate_config({
        "chest_template_threshold": 42,
        "reference_screen_size": [0, 0],
        "hotkeys": {"scan": "  "},
        "tray_minimize_to_tray": "yes",
    })
    assert cfg["chest_template_threshold"] == paths.DEFAULT_CONFIG["chest_template_threshold"]
    assert cfg["reference_screen_size"] == paths.DEFAULT_CONFIG["reference_screen_size"]
    assert cfg["hotkeys"]["scan"] == ""
    assert cfg["tray_minimize_to_tray"] is False
    assert warns


def test_validate_config_merges_defaults():
    cfg, _ = validate_config({})
    for key in ("phash_threshold", "item_detect_cache",
                "tray_minimize_to_tray", "notify_on_scan"):
        assert key in cfg


def test_default_hotkeys_use_toggle_window():
    hk = paths.DEFAULT_CONFIG["hotkeys"]
    assert hk["toggle_window"] == "f6"
    assert hk["scan"] == "f8"
    assert "discord" not in hk
    assert hk["exit"] == "f10"
    assert "digits" not in hk and "grid" not in hk


def test_validate_config_drops_old_debug_hotkeys():
    cfg, _ = validate_config({"hotkeys": {"digits": "f6", "grid": "f7",
                                          "scan": "f8"}})
    hk = cfg["hotkeys"]
    assert "digits" not in hk and "grid" not in hk
    assert hk["toggle_window"] == "f6"
    assert hk["scan"] == "f8"


def test_normalize_items_db_makes_duplicate_ids_unique_without_losing_items():
    db = {"items": [
        {"id": "shared", "name": "First", "stack_size": 64},
        {"id": "shared", "name": "Second", "stack_size": 16},
        {"id": "third", "name": "Third", "stack_size": 1},
    ]}

    out = normalize_items_db(db)
    items = out["items"]

    assert [item["name"] for item in items] == ["First", "Second", "Third"]
    assert items[0]["id"] == "shared"
    assert items[1]["id"] != "shared"
    assert items[2]["id"] == "third"
    assert len({item["id"] for item in items}) == len(items)
    assert [item["stack_size"] for item in items] == [64, 16, 1]


def test_normalize_items_db_generates_unique_ids_for_missing_and_blank_ids():
    out = normalize_items_db({"items": [
        {"id": "", "name": "First"},
        {"name": "Second"},
    ]})

    ids = [item["id"] for item in out["items"]]
    assert all(ids)
    assert len(set(ids)) == 2
