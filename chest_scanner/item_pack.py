"""Безопасный экспорт и импорт базы предметов в ZIP-архив."""
import json
import os
import shutil
import tempfile
import uuid
import zipfile

import cv2
import numpy as np

from chest_scanner.commons import absolute_path
from chest_scanner.items import is_safe_icon_path, normalize_items_db

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 4096
MAX_COMPRESSION_RATIO = 200


def _db_icon_paths(db):
    out = []
    for item in db.get("items") or []:
        images = item.get("images") or ([item.get("image")] if item.get("image") else [])
        for path in images:
            path = str(path)
            if path and path not in out:
                out.append(path)
    return out


def _safe_windows_components(path):
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
    parts = path.split("/")
    return all(
        ":" not in part and part.rstrip(" .") == part
        and part.split(".", 1)[0].upper() not in reserved
        for part in parts
    )


def _resolved_icon_target(rel):
    target = absolute_path(rel)
    profile_root = absolute_path("items")
    if not target or not profile_root:
        raise ValueError(f"Не удалось определить путь изображения: {rel}")
    root = os.path.realpath(os.path.dirname(profile_root))
    resolved = os.path.realpath(target)
    try:
        if os.path.commonpath((root, resolved)) != root:
            raise ValueError(f"Путь изображения выходит за пределы профиля: {rel}")
    except ValueError as exc:
        raise ValueError(f"Путь изображения выходит за пределы профиля: {rel}") from exc
    return target


def _validate_db(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        raise ValueError("Структура items.json некорректна: ожидается объект с массивом items.")
    for index, item in enumerate(raw["items"]):
        if not isinstance(item, dict) or not str(item.get("name", "")).strip():
            raise ValueError(f"Некорректная запись предмета №{index + 1}.")
        images = item.get("images")
        if isinstance(images, str):
            images = [images]
        if images is None:
            images = []
        if not isinstance(images, list):
            raise ValueError(f"Некорректный список изображений у предмета «{item['name']}».")
        normalized_images = []
        for path in images:
            if not is_safe_icon_path(path):
                raise ValueError(f"Небезопасный путь изображения у предмета «{item['name']}»: {path!r}")
            normalized = path.replace("\\", "/")
            if not _safe_windows_components(normalized):
                raise ValueError(f"Недопустимый компонент пути изображения: {path!r}")
            normalized_images.append(normalized)
        item["images"] = normalized_images
        legacy = item.get("image")
        if legacy:
            if not is_safe_icon_path(legacy):
                raise ValueError(f"Небезопасный путь изображения у предмета «{item['name']}»: {legacy!r}")
            legacy = legacy.replace("\\", "/")
            if not _safe_windows_components(legacy):
                raise ValueError(f"Недопустимый компонент пути изображения: {legacy!r}")
            item["image"] = legacy
    return normalize_items_db(raw)


def _validate_image(data, rel_path):
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None or image.size == 0:
        raise ValueError(f"Изображение в архиве повреждено или имеет неверный формат: {rel_path}")


def export_item_db(db, zip_path):
    """Создаёт ZIP атомарно; отсутствующие/повреждённые иконки считаются ошибкой."""
    db = _validate_db(db or {"items": []})
    icon_paths = _db_icon_paths(db)
    if len(icon_paths) + 1 > MAX_ARCHIVE_ENTRIES:
        raise ValueError("В базе слишком много файлов для одного архива.")
    serialized_db = json.dumps(db, ensure_ascii=False, indent=2).encode("utf-8")
    if len(serialized_db) > MAX_FILE_BYTES:
        raise ValueError("Файл items.json превышает лимит 32 МБ.")
    total_bytes = len(serialized_db)
    temporary_dir = tempfile.mkdtemp(prefix="cs_itempack_")
    destination = os.path.abspath(os.fspath(zip_path))
    temporary_zip = destination + f".tmp-{uuid.uuid4().hex}"
    try:
        with open(os.path.join(temporary_dir, "items.json"), "wb") as stream:
            stream.write(serialized_db)
        for rel in icon_paths:
            source = _resolved_icon_target(rel)
            if os.path.islink(source) or not os.path.isfile(source):
                raise FileNotFoundError(f"Не найдена или небезопасна иконка предмета: {rel}")
            with open(source, "rb") as stream:
                content = stream.read(MAX_FILE_BYTES + 1)
            if len(content) > MAX_FILE_BYTES:
                raise ValueError(f"Файл изображения слишком большой: {rel}")
            _validate_image(content, rel)
            total_bytes += len(content)
            if total_bytes > MAX_ARCHIVE_BYTES:
                raise ValueError("Размер данных базы превышает лимит архива 128 МБ.")
            target = os.path.join(temporary_dir, *rel.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "xb") as stream:
                stream.write(content)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with zipfile.ZipFile(temporary_zip, "w", zipfile.ZIP_STORED) as archive:
            for root, _dirs, filenames in os.walk(temporary_dir):
                for filename in filenames:
                    full = os.path.join(root, filename)
                    relative = os.path.relpath(full, temporary_dir).replace(os.sep, "/")
                    archive.write(full, relative)
        with zipfile.ZipFile(temporary_zip, "r") as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError(f"Не удалось проверить созданный архив: {bad}")
        # Проверяем, что созданный файл полностью совместим с нашим импортёром.
        read_item_db_zip(temporary_zip)
        os.replace(temporary_zip, destination)
    finally:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        try:
            if os.path.exists(temporary_zip):
                os.remove(temporary_zip)
        except OSError:
            pass
    return zip_path


def read_item_db_zip(zip_path):
    """Полностью проверяет ZIP до чтения данных и возвращает (db, files)."""
    files = {}
    try:
        archive_context = zipfile.ZipFile(zip_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("Не удалось открыть ZIP-архив базы предметов.") from exc
    with archive_context as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise ValueError("В архиве слишком много файлов.")
        seen = set()
        total = 0
        for info in infos:
            name = info.filename.replace("\\", "/")
            if info.is_dir():
                directory_name = name.rstrip("/")
                directory_parts = directory_name.split("/")
                if (not directory_name or directory_name.startswith("/")
                        or directory_parts[0] not in ("items", "unknown_items")
                        or any(part in ("", ".", "..") for part in directory_parts)
                        or not _safe_windows_components(directory_name)):
                    raise ValueError(f"Архив содержит недопустимый каталог: {info.filename}")
                continue
            parts = name.split("/")
            mode = (info.external_attr >> 16) & 0o170000
            if (not name or name.startswith("/") or "\\" in info.filename
                    or any(part in ("", ".", "..") for part in parts)
                    or (len(parts) > 1 and parts[0] not in ("items", "unknown_items"))
                    or (len(parts) == 1 and name != "items.json")
                    or not _safe_windows_components(name)
                    or mode == 0o120000 or info.flag_bits & 0x1):
                raise ValueError(f"Архив содержит недопустимый путь или тип файла: {info.filename}")
            folded = name.casefold()
            if folded in seen:
                raise ValueError(f"Архив содержит повторяющиеся имена файлов: {name}")
            seen.add(folded)
            if info.file_size > MAX_FILE_BYTES:
                raise ValueError(f"Файл в архиве превышает лимит 32 МБ: {name}")
            total += info.file_size
            if total > MAX_ARCHIVE_BYTES:
                raise ValueError("Распакованный размер архива превышает лимит 128 МБ.")
            if info.file_size and (not info.compress_size or
                    info.file_size / info.compress_size > MAX_COMPRESSION_RATIO):
                raise ValueError(f"Подозрительная степень сжатия файла: {name}")
        if "items.json" not in seen:
            raise ValueError("В архиве нет items.json — это не файл базы предметов.")
        try:
            raw = archive.read(next(i for i in infos if i.filename == "items.json"))
            incoming = _validate_db(json.loads(raw.decode("utf-8")))
            for info in infos:
                name = info.filename.replace("\\", "/")
                if info.is_dir() or name == "items.json":
                    continue
                files[name] = archive.read(info)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("items.json в архиве повреждён.") from exc
        except (zipfile.BadZipFile, EOFError, RuntimeError) as exc:
            raise ValueError("Архив повреждён или не прошёл проверку CRC.") from exc

    referenced = set(_db_icon_paths(incoming))
    for rel in referenced:
        existing = _resolved_icon_target(rel)
        if rel not in files and not os.path.isfile(existing):
            raise ValueError(f"В архиве отсутствует изображение, указанное в базе: {rel}")
    for rel in referenced.intersection(files):
        _validate_image(files[rel], rel)
    return incoming, files


def _merge_by_id(local, incoming):
    merged = {}
    for item in local.get("items") or []:
        item_id = str(item.get("id") or "")
        if item_id:
            merged[item_id] = dict(item)
    for item in incoming.get("items") or []:
        item_id = str(item.get("id") or "")
        if item_id:
            merged[item_id] = dict(item)
    return {"version": 3, "items": list(merged.values())}


def import_item_db(zip_path, local_db, mode="merge", *, commit=None):
    """Проверяет архив, устанавливает новые иконки и фиксирует БД.

    При ошибке commit все созданные этим импортом файлы удаляются.
    """
    if mode not in ("merge", "replace"):
        raise ValueError("Неизвестный режим импорта базы.")
    incoming, files = read_item_db_zip(zip_path)
    result = incoming if mode == "replace" else normalize_items_db(
        _merge_by_id(normalize_items_db(local_db or {"items": []}), incoming))

    referenced = set(_db_icon_paths(result))
    staged = []
    installed = []
    try:
        for rel, data in files.items():
            if rel not in referenced:
                continue
            target = _resolved_icon_target(rel)
            if os.path.islink(target):
                raise ValueError(f"Путь изображения указывает на символическую ссылку: {rel}")
            if os.path.exists(target):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".item-import-", dir=os.path.dirname(target))
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            staged.append((temporary, target))
        for temporary, target in staged:
            if os.path.exists(target):
                continue
            try:
                os.link(temporary, target)
            except FileExistsError:
                continue
            installed.append(target)
            os.remove(temporary)
        if commit is not None:
            commit(result)
    except Exception:
        for target in reversed(installed):
            try:
                os.remove(target)
            except OSError:
                pass
        raise
    finally:
        for temporary, _target in staged:
            try:
                if os.path.exists(temporary):
                    os.remove(temporary)
            except OSError:
                pass
    return result
