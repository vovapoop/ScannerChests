"""Экспорт и импорт базы предметов в zip-файл (перенос между компьютерами).

Формат файла: zip, содержащий `items.json` (нормализованная база) и файлы
иконок по путям из `images`/`image` (например `items/Алмаз_1.png`).

Экспорт:
    export_item_db(db, zip_path) — собирает архив из текущей базы.

Импорт:
    import_item_db(zip_path, local_db, mode="merge") — копирует иконки
    в профиль и возвращает новую базу:
    - "merge"   — слияние по id: предметы из файла перезаписывают локальные
                  с тем же id, новые добавляются, локальные без пары остаются;
    - "replace" — локальная база полностью заменяется импортированной.
"""

import json
import os
import shutil
import tempfile
import zipfile

from chest_scanner.commons import absolute_path
from chest_scanner.items import normalize_items_db


def _db_icon_paths(db):
    """Все относительные пути иконок базы (из images/image)."""
    out = []
    for item in db.get("items") or []:
        images = item.get("images") or ([item.get("image")] if item.get("image") else [])
        for p in images:
            p = str(p)
            if p and p not in out:
                out.append(p)
    return out


def export_item_db(db, zip_path):
    """Сохраняет базу в zip: items.json + существующие иконки.

    Пропускает файлы, которых нет на диске. Возвращает zip_path.
    """
    db = normalize_items_db(db or {"items": []})
    tmp = tempfile.mkdtemp(prefix="cs_itempack_")
    try:
        with open(os.path.join(tmp, "items.json"), "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
        for rel in _db_icon_paths(db):
            rel = rel.replace("\\", "/")
            ap = absolute_path(rel)
            if not ap or not os.path.isfile(ap):
                continue
            dst = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(ap, dst)
        os.makedirs(os.path.dirname(os.path.abspath(zip_path)), exist_ok=True)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(tmp):
                for fn in files:
                    full = os.path.join(root, fn)
                    rel = os.path.relpath(full, tmp).replace(os.sep, "/")
                    zf.write(full, rel)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return zip_path


def read_item_db_zip(zip_path):
    """Читает zip и возвращает (db, files), где files: {rel_path: bytes}."""
    files = {}
    with zipfile.ZipFile(zip_path, "r") as zf:
        try:
            raw = zf.read("items.json")
        except KeyError:
            raise ValueError("В архиве нет items.json — это не файл базы предметов")
        try:
            db = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("items.json в архиве повреждён")
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if name == "items.json":
                continue
            files[name] = zf.read(info)
    return db, files


def _restore_icons(files):
    """Копирует иконки из архива в профиль (только отсутствующие)."""
    restored = 0
    for rel, data in files.items():
        rel = rel.replace("\\", "/")
        parts = rel.split("/")
        if any(p in ("", ".", "..") for p in parts[:-1]):
            continue  # защита от путей с выходом из профиля
        if not (rel.startswith("items/") or rel.startswith("unknown_items/")):
            continue
        ap = absolute_path(rel)
        if not ap:
            continue
        if os.path.exists(ap):
            continue  # файл уже есть — не перезаписываем
        try:
            os.makedirs(os.path.dirname(ap), exist_ok=True)
            with open(ap, "wb") as f:
                f.write(data)
            restored += 1
        except OSError:
            pass
    return restored


def _merge_by_id(local, incoming):
    """Слияние по id: предметы из incoming перезаписывают локальные."""
    merged = {}
    for it in local.get("items") or []:
        iid = str(it.get("id") or "")
        if iid:
            merged[iid] = dict(it)
    for it in incoming.get("items") or []:
        iid = str(it.get("id") or "")
        if not iid:
            continue
        merged[iid] = dict(it)  # предмет из файла побеждает
    return {"version": 3, "items": list(merged.values())}


def import_item_db(zip_path, local_db, mode="merge"):
    """Импортирует базу из zip и возвращает новую базу.

    Иконки из архива копируются в профиль (если их ещё нет).
    mode="merge" — слияние по id; mode="replace" — полная замена.
    """
    incoming_raw, files = read_item_db_zip(zip_path)
    _restore_icons(files)
    incoming = normalize_items_db(incoming_raw)
    if mode == "replace":
        return incoming
    local = normalize_items_db(local_db or {"items": []})
    return normalize_items_db(_merge_by_id(local, incoming))
