"""Общие утилиты: JSON-доступ, пути, создание директорий."""
import json
import os
import shutil

from chest_scanner import paths
from chest_scanner.logging_setup import LOGS_DIR, logger


def ensure_directories():
    """Создаёт все служебные директории приложения."""
    for p in (paths.ASSETS_DIR, paths.DIGITS_DIR, paths.PROFILES_DIR, paths.PROFILE_DIR,
              paths.RESULTS_DIR, paths.HISTORY_DIR, paths.UNKNOWN_DIR, paths.ITEM_IMAGES_DIR,
              LOGS_DIR):
        os.makedirs(p, exist_ok=True)


def absolute_path(path):
    """Превращает относительный путь в абсолютный.

    Пути "items/..." и "unknown_items/..." резолвятся внутри профиля,
    остальные — относительно базовой директории приложения.
    """
    if not path:
        return None
    if os.path.isabs(path):
        return path
    n = path.replace("\\", "/").lstrip("./")
    if n == "items" or n.startswith("items/") or n == "unknown_items" or n.startswith("unknown_items/"):
        return os.path.join(paths.PROFILE_DIR, n)
    return os.path.join(paths.BASE_DIR, path)


def load_json(path, default):
    """Загружает JSON; при ошибке возвращает default (копию, если возможно)."""
    if not os.path.exists(path):
        return default.copy() if hasattr(default, "copy") else default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"JSON ошибка {path}: {e}")
        return default.copy() if hasattr(default, "copy") else default


def save_json(path, data, backup=False):
    """Сохраняет данные в JSON атомарно: temp-файл + fsync + rename.

    Позволяет избежать повреждения файла при сбое/прерывании записи.
    Если backup=True, предыдущая версия копируется в "<path>.backup".
    """
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    if backup and os.path.exists(path):
        try:
            shutil.copy2(path, f"{path}.backup")
        except OSError:
            pass
    os.replace(tmp, path)
