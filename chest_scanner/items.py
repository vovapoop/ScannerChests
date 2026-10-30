"""Работа с базой предметов и изображениями: нормализация, переименование, копирование."""
import os
import re
import unicodedata
import uuid

import cv2

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
    if not path or not isinstance(path, str):
        return False
    n = path.replace("\\", "/").strip("/")
    if n != n.lstrip(".") and ".." in n.split("/"):
        return False
    parts = n.split("/")
    if not parts or any(p in ("", ".", "..") for p in parts):
        return False
    if os.path.isabs(path) or ":" in parts[0]:
        return False
    return parts[0] in ("items", "unknown_items")


def normalize_items_db(data):
    if not isinstance(data, dict):
        data = {"items": []}
    raw = data.get("items", [])
    if not isinstance(raw, list):
        raw = []
    out = []
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
        item["id"] = str(r.get("id") or uuid.uuid4())
        item["name"] = name
        item["images"] = images
        item["image"] = images[0] if images else ""
        try:
            item["stack_size"] = max(1, int(r.get("stack_size", 64)))
        except (TypeError, ValueError):
            item["stack_size"] = 64
        al = r.get("aliases", [])
        item["aliases"] = al if isinstance(al, list) else []
        item["enabled"] = bool(r.get("enabled", True))
        item["category"] = str(r.get("category", "uncategorized")).strip() or "uncategorized"
        tags = r.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        item["tags"] = tags if isinstance(tags, list) else []
        out.append(item)
    return {"version": 3, "items": out}
