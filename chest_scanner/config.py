"""Профили приложения и валидация конфигурации."""
import os

from chest_scanner import paths
from chest_scanner.commons import ensure_directories, load_json, save_json
from chest_scanner.items import sanitize_filename


def load_profiles():
    data = load_json(paths.PROFILES_FILE, {})
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), list) or not data["profiles"]:
        data = {"version": 1, "active": "Default",
                "profiles": [{"name": "Default", "directory": "."}],
                "setup_complete": False}
        save_json(paths.PROFILES_FILE, data)
    return data


def save_profiles(data):
    save_json(paths.PROFILES_FILE, data)


def profile_entry(name, directory=None):
    data = load_profiles()
    for e in data.get("profiles", []):
        if e.get("name") == name:
            return e
    if directory is None:
        directory = os.path.join("profiles", sanitize_filename(name))
    entry = {"name": name, "directory": directory}
    data.setdefault("profiles", []).append(entry)
    save_profiles(data)
    return entry


def activate_profile(name):
    """Активирует профиль, пересчитывая пути приложения в модуле paths."""
    data = load_profiles()
    entry = profile_entry(name)
    d = entry.get("directory", ".")
    paths.PROFILE_DIR = paths.BASE_DIR if d == "." else os.path.join(paths.BASE_DIR, d)
    os.makedirs(paths.PROFILE_DIR, exist_ok=True)
    paths.CONFIG_FILE = os.path.join(paths.PROFILE_DIR, "config.json")
    paths.ITEMS_FILE = os.path.join(paths.PROFILE_DIR, "items.json")
    paths.RESULTS_DIR = os.path.join(paths.PROFILE_DIR, "results")
    paths.UNKNOWN_DIR = os.path.join(paths.PROFILE_DIR, "unknown_items")
    paths.ITEM_IMAGES_DIR = os.path.join(paths.PROFILE_DIR, "items")
    paths.RESULTS_FILE = os.path.join(paths.RESULTS_DIR, "chests.json")
    paths.HISTORY_DIR = os.path.join(paths.RESULTS_DIR, "history")
    paths.DISCORD_IDS_FILE = os.path.join(paths.RESULTS_DIR, "discord_message_ids.json")
    paths.DISCORD_WEBHOOK_FILE = os.path.join(paths.PROFILE_DIR, "discord_webhook.json")
    data["active"] = name
    save_profiles(data)
    ensure_directories()
    return entry


def deep_merge(base, user):
    result = base.copy()
    for k, v in user.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def validate_config(data):
    """Проверяет конфиг, заменяет повреждённые поля значениями по умолчанию."""
    warnings = []
    if not isinstance(data, dict):
        return paths.DEFAULT_CONFIG.copy(), ["Конфиг не является объектом — использованы стандартные значения."]
    cfg = deep_merge(paths.DEFAULT_CONFIG, data)
    ref = cfg.get("reference_screen_size")
    if not isinstance(ref, list) or len(ref) != 2 or not all(isinstance(x, (int, float)) and x > 0 for x in ref):
        cfg["reference_screen_size"] = paths.DEFAULT_CONFIG["reference_screen_size"]
        warnings.append("reference_screen_size повреждён")
    grid = cfg.get("chest_grid")
    if not isinstance(grid, dict):
        cfg["chest_grid"] = paths.DEFAULT_CONFIG["chest_grid"]
        warnings.append("chest_grid повреждён")
    else:
        # Миграция со старого формата
        if "x" in grid and "width" in grid and "x1" not in grid:
            cfg["chest_grid"] = {
                "x1": grid.get("x", 798), "y1": grid.get("y", 351),
                "x2": grid.get("x", 798) + grid.get("width", 324),
                "y2": grid.get("y", 351) + grid.get("height", 216),
                "columns": grid.get("columns", 9), "rows": grid.get("rows", 6)}
            warnings.append("chest_grid мигрирован из старого формата")
            grid = cfg["chest_grid"]
        for k in ("x1", "y1", "x2", "y2", "columns", "rows"):
            if k not in grid or not isinstance(grid[k], (int, float)):
                cfg["chest_grid"] = paths.DEFAULT_CONFIG["chest_grid"]
                warnings.append(f"chest_grid.{k} повреждён")
                break
    for field, lo, hi in [("chest_template_threshold", 0.01, 1.0),
                          ("empty_slot_threshold", 1, 100000),
                          ("item_match_threshold", 1, 1000000),
                          ("item_match_margin", 0, 100000),
                          ("scan_interval_seconds", 0.1, 3600)]:
        v = cfg.get(field)
        if not isinstance(v, (int, float)) or not (lo <= v <= hi):
            cfg[field] = paths.DEFAULT_CONFIG[field]
            warnings.append(f"{field}={v!r} вне диапазона")
    for roi in ("icon_roi_in_slot", "count_roi_in_slot"):
        r = cfg.get(roi)
        if not isinstance(r, list) or len(r) != 4 or not all(isinstance(x, (int, float)) for x in r):
            cfg[roi] = paths.DEFAULT_CONFIG[roi]
            warnings.append(f"{roi} повреждён")
    dm = cfg.get("discord_message")
    if not isinstance(dm, dict):
        cfg["discord_message"] = paths.DEFAULT_CONFIG["discord_message"]
        warnings.append("discord_message повреждён")
    hk_defaults = paths.DEFAULT_CONFIG["hotkeys"]
    hk = cfg.get("hotkeys")
    if not isinstance(hk, dict):
        cfg["hotkeys"] = dict(hk_defaults)
        warnings.append("hotkeys повреждён")
    else:
        fixed = {}
        for action, default in hk_defaults.items():
            val = hk.get(action, default)
            if not isinstance(val, str) or not val.strip():
                fixed[action] = ""
            else:
                fixed[action] = val.strip().lower()
        cfg["hotkeys"] = fixed
    return cfg, warnings
