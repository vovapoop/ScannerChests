"""Application paths, constants, and default configuration."""
import os

from chest_scanner.app_bootstrap import BASE_DIR_FOR_APP

# ============================================================
# ПУТИ
# ============================================================
BASE_DIR = BASE_DIR_FOR_APP
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
ITEMS_FILE = os.path.join(BASE_DIR, "items.json")
PROFILE_DIR = BASE_DIR
PROFILES_DIR = os.path.join(BASE_DIR, "profiles")
PROFILES_FILE = os.path.join(BASE_DIR, "profiles.json")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
DIGITS_DIR = os.path.join(ASSETS_DIR, "digits")
RESULTS_DIR = os.path.join(PROFILE_DIR, "results")
UNKNOWN_DIR = os.path.join(PROFILE_DIR, "unknown_items")
ITEM_IMAGES_DIR = os.path.join(PROFILE_DIR, "items")
RESULTS_FILE = os.path.join(RESULTS_DIR, "chests.json")
HISTORY_DIR = os.path.join(RESULTS_DIR, "history")
DISCORD_IDS_FILE = os.path.join(RESULTS_DIR, "discord_message_ids.json")
DISCORD_WEBHOOK_FILE = os.path.join(PROFILE_DIR, "discord_webhook.json")

# ============================================================
# КАТЕГОРИИ
# ============================================================
ITEM_CATEGORIES = [
    ("uncategorized", "Без категории"),
    ("blocks", "Блоки"),
    ("ores", "Руды и минералы"),
    ("tools", "Инструменты"),
    ("weapons", "Оружие"),
    ("armor", "Броня"),
    ("food", "Еда"),
    ("potions", "Зелья"),
    ("redstone", "Редстоун"),
    ("mob_drops", "Дроп мобов"),
    ("rare", "Редкости"),
]
CATEGORY_KEYS = [c[0] for c in ITEM_CATEGORIES]
CATEGORY_NAMES = {c[0]: c[1] for c in ITEM_CATEGORIES}

# ============================================================
# КОНФИГ ПО УМОЛЧАНИЮ
# ============================================================
DEFAULT_CONFIG = {
    "profile_name": "Default",
    "setup_complete": False,
    "reference_screen_size": [1920, 1080],
    "chest_template": "assets/chest_open.png",
    "empty_slot_template": "assets/empty_slot.png",
    "chest_grid": {"x1": 798, "y1": 351, "x2": 1122, "y2": 567,
                    "columns": 9, "rows": 6},
    "icon_roi_in_slot": [0.10, 0.08, 0.80, 0.67],
    "count_roi_in_slot": [0.30, 0.40, 0.67, 0.58],
    "chest_template_threshold": 0.82,
    "empty_slot_threshold": 600,
    "item_match_threshold": 1500,
    "item_match_margin": 200,
    "scan_interval_seconds": 2,
    "print_item_candidates": False,
    "auto_scan_enabled": False,
    "discord_webhook_env": "DISCORD_WEBHOOK_URL",
    "discord_max_items_in_message": 25,
    "discord_delete_old_messages": True,
    "discord_message": {
        "header": "",
        "scanned_chests": "Отсканировано сундуков: **{chests}**",
        "last_scan": "Время последнего сканирования: **{last_scan}**",
        "items_title": "══════════Предметы══════════",
        "columns": "Название | Всего | Стаков",
        "item": "{name} | {total} | {stacks}",
        "stack_size": 64,
        "item_separator": "\n",
        "unknown_title": "══════════Неизвестные предметы══════════",
        "unknown_item": "• {name}",
        "table": {"name_width": 30, "total_width": 12, "stacks_width": 10},
    },
    "hotkeys": {"digits": "f6", "grid": "f7", "scan": "f8",
                "discord": "f9", "exit": "f10"},
}
