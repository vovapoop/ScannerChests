"""Работа с базой предметов и изображениями: нормализация, переименование, копирование."""
import os
import re
import unicodedata
import uuid

import cv2

from chest_scanner import paths
from chest_scanner.commons import absolute_path


def sanitize_filename(name):
    v = unicodedata.normalize("NFKC", str(name)).strip()
    v = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", v)
    v = re.sub(r"\s+", " ", v).strip(" .")
    if not v:
        v = "item"
    res = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
    if v.upper() in res:
        v = f"_{v}"
    return v[:120]


def next_image_path(name, ext=".png"):
    safe = sanitize_filename(name)
    i = 1
    while True:
        rel = f"items/{safe}_{i}{ext}"
        if not os.path.exists(absolute_path(rel)):
            return rel
        i += 1


def copy_image_as_png(src, rel_target):
    img = cv2.imread(src, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Не удалось открыть: {src}")
    tgt = absolute_path(rel_target)
    os.makedirs(os.path.dirname(tgt), exist_ok=True)
    if not cv2.imwrite(tgt, img):
        raise OSError(f"Не удалось сохранить: {tgt}")
    return tgt


def is_safe_icon_path(path):
    """Проверяет, что относительный путь иконки безопасен.

    Путь должен вести внутрь профиля (items/ или unknown_items/),
    не содержать ".." и абсолютных/UNC-компонентов.
    """
    if not path or not isinstance(path, str) or "\x00" in path:
        return False
    n = path.replace("\\", "/")
    parts = n.split("/")
    if not parts or any(p in ("", ".", "..") for p in parts):
        return False
    if os.path.isabs(path) or n.startswith("/") or ":" in parts[0]:
        return False
    if parts[0] not in ("items", "unknown_items"):
        return False

    # Lexical validation is not enough: a symlink inside the asset folder may
    # point outside the active profile. Resolve both roots and target before
    # accepting the reference. Non-existent image files remain valid refs so
    # the UI can report them without dropping the corresponding item.
    profile_root = os.path.realpath(paths.PROFILE_DIR)
    asset_root = os.path.realpath(os.path.join(profile_root, parts[0]))
    target = os.path.realpath(absolute_path(path))
    try:
        if os.path.commonpath((profile_root, asset_root)) != profile_root:
            return False
        if os.path.commonpath((asset_root, target)) != asset_root:
            return False
    except (OSError, ValueError):
        return False
    return True


def normalize_items_db(data):
    if not isinstance(data, dict):
        data = {"items": []}
    raw = data.get("items", [])
    if not isinstance(raw, list):
        raw = []
    out = []
    used_ids = set()
    for r in raw:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name", "")).strip()
        if not name:
            continue
        images = r.get("images")
        if isinstance(images, str):
            images = [images]
        if not isinstance(images, list):
            images = []
        old = r.get("image")
        if old and old not in images:
            images.insert(0, old)
        images = [str(x) for x in images if x and is_safe_icon_path(x)]
        item = dict(r)
        item_id = str(r.get("id") or "").strip()
        if not item_id or item_id in used_ids:
            item_id = str(uuid.uuid4())
            while item_id in used_ids:
                item_id = str(uuid.uuid4())
        used_ids.add(item_id)
        item["id"] = item_id
        item["name"] = name
        item["images"] = images
        item["image"] = images[0] if images else ""
        try:
            item["stack_size"] = max(1, int(r.get("stack_size", 64)))
        except (TypeError, ValueError):
            item["stack_size"] = 64
        item.pop("aliases", None)
        item["enabled"] = bool(r.get("enabled", True))
        item["category"] = str(r.get("category", "uncategorized")).strip() or "uncategorized"
        item.pop("tags", None)
        out.append(item)
    return {"version": 3, "items": out}
