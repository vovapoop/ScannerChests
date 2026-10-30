"""Тесты предметов (items) и конфигурации (config)."""
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
    assert isinstance(out["items"][0]["aliases"], list)


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
    assert hk["toggle_window"] == "f6"  # вместо отладочных F6/F7
    assert hk["scan"] == "f8"
    assert hk["discord"] == "f9"
    assert hk["exit"] == "f10"
    assert "digits" not in hk and "grid" not in hk


def test_validate_config_drops_old_debug_hotkeys():
    cfg, _ = validate_config({"hotkeys": {"digits": "f6", "grid": "f7",
                                          "scan": "f8"}})
    hk = cfg["hotkeys"]
    assert "digits" not in hk and "grid" not in hk
    assert hk["toggle_window"] == "f6"
    assert hk["scan"] == "f8"