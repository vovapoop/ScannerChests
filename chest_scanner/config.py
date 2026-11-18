"""Профили приложения и валидация конфигурации."""
import hashlib
import json
import ntpath
import os
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from contextlib import nullcontext
from datetime import datetime
from functools import wraps

from chest_scanner import paths
from chest_scanner.commons import (
    ensure_directories,
    profile_data_guard,
    results_history_guard,
    save_json,
)
from chest_scanner.items import is_safe_icon_path, sanitize_filename
from chest_scanner.logging_setup import logger


def profile_operation_guard():
    """Shared lock for profile transitions and profile-data writes."""
    return profile_data_guard()


def _profile_operation_locked(function):
    """Serialize profile transactions within this process.

    The application-level instance lock prevents a second process from
    operating on the same installation; this reentrant lock also protects
    worker-thread calls from interleaving registry updates and the shared
    transaction journal.
    """
    @wraps(function)
    def wrapped(*args, **kwargs):
        with profile_operation_guard():
            return function(*args, **kwargs)
    return wrapped


def _validate_profile_registry(data):
    """Validate registry structure and profile paths before they are trusted."""
    def invalid(reason):
        raise ValueError(f"\u0420\u0435\u0435\u0441\u0442\u0440 \u043f\u0440\u043e\u0444\u0438\u043b\u0435\u0439 \u043f\u043e\u0432\u0440\u0435\u0436\u0434\u0451\u043d: {reason}")

    if not isinstance(data, dict):
        invalid("expected a JSON object")
    if data.get("version", 1) != 1 or isinstance(data.get("version", 1), bool):
        invalid("unsupported version")
    profiles = data.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        invalid("profiles must be a non-empty list")

    base = os.path.realpath(paths.BASE_DIR)
    names = set()
    directories = set()
    for index, entry in enumerate(profiles):
        if not isinstance(entry, dict):
            invalid(f"profile entry {index + 1} must be an object")
        name = entry.get("name")
        directory = entry.get("directory")
        if not isinstance(name, str) or not name or name != name.strip():
            invalid(f"invalid profile name at entry {index + 1}")
        if not isinstance(directory, str) or not directory:
            invalid(f"invalid directory for profile {name!r}")
        folded_name = name.casefold()
        if folded_name in names:
            invalid(f"duplicate profile name {name!r}")
        names.add(folded_name)

        normalized = directory.replace("\\\\", "/")
        if (ntpath.isabs(directory) or ntpath.splitdrive(directory)[0]
                or normalized.startswith("/")
                or any(part == ".." for part in normalized.split("/"))):
            invalid(f"\u041f\u0443\u0442\u044c \u043f\u0440\u043e\u0444\u0438\u043b\u044f {name!r} \u0432\u044b\u0445\u043e\u0434\u0438\u0442 \u0437\u0430 \u043f\u0440\u0435\u0434\u0435\u043b\u044b \u043a\u0430\u0442\u0430\u043b\u043e\u0433\u0430 \u043f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u044f")
        if normalized != "." and any(part in {"", "."} for part in normalized.split("/")):
            invalid(f"invalid directory for profile {name!r}")
        lexical_target = os.path.abspath(os.path.join(base, *normalized.split("/")))
        target = os.path.realpath(lexical_target)
        try:
            inside = os.path.commonpath((base, target)) == base
        except ValueError:
            inside = False
        if not inside or (normalized != "." and target == base):
            invalid(f"\u041f\u0443\u0442\u044c \u043f\u0440\u043e\u0444\u0438\u043b\u044f {name!r} \u0432\u044b\u0445\u043e\u0434\u0438\u0442 \u0437\u0430 \u043f\u0440\u0435\u0434\u0435\u043b\u044b \u043a\u0430\u0442\u0430\u043b\u043e\u0433\u0430 \u043f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u044f")
        if normalized != "." and _profile_path_has_linked_component(base, lexical_target):
            invalid(f"profile {name!r} path contains a symbolic link or junction")
        directory_key = os.path.normcase(os.path.normpath(target))
        if directory_key in directories:
            invalid(f"duplicate profile directory for {name!r}")
        directories.add(directory_key)

    active = data.get("active")
    if not isinstance(active, str) or active not in {
            entry["name"] for entry in profiles}:
        invalid("active profile does not exist in profiles")
    return data


@_profile_operation_locked
def load_profiles():
    if not os.path.exists(paths.PROFILES_FILE):
        data = {"version": 1, "active": "Default",
                "profiles": [{"name": "Default", "directory": "."}],
                "setup_complete": False}
        save_json(paths.PROFILES_FILE, data)
        return data
    try:
        with open(paths.PROFILES_FILE, "r", encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(
            f"\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043f\u0440\u043e\u0447\u0438\u0442\u0430\u0442\u044c \u0440\u0435\u0435\u0441\u0442\u0440 \u043f\u0440\u043e\u0444\u0438\u043b\u0435\u0439 {paths.PROFILES_FILE}; "
            "\u0438\u0441\u0445\u043e\u0434\u043d\u044b\u0439 \u0444\u0430\u0439\u043b \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d \u0431\u0435\u0437 \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0439.") from exc
    return _validate_profile_registry(data)


@_profile_operation_locked
def save_profiles(data):
    _validate_profile_registry(data)
    save_json(paths.PROFILES_FILE, data)


@_profile_operation_locked
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


@_profile_operation_locked
def activate_profile(name):
    """Switch profiles atomically from the application's point of view."""
    data = load_profiles()
    entry = next((item for item in data.get("profiles", [])
                  if item.get("name") == name), None)
    if entry is None:
        raise ValueError(f"Profile {name!r} is not registered.")
    d = entry.get("directory", ".")
    profile_dir = paths.BASE_DIR if d == "." else os.path.join(paths.BASE_DIR, d)
    # Activation must never recreate a missing profile: doing so could hide
    # accidental deletion and make an empty directory look like valid data.
    if not os.path.isdir(profile_dir):
        raise FileNotFoundError(
            f"Profile directory for {name!r} does not exist: {profile_dir}")

    path_names = (
        "PROFILE_DIR", "CONFIG_FILE", "ITEMS_FILE", "RESULTS_DIR",
        "UNKNOWN_DIR", "UNKNOWN_QUEUE_FILE", "ITEM_IMAGES_DIR",
        "RESULTS_FILE", "RESULTS_DB", "HISTORY_DIR",
        "DISCORD_IDS_FILE", "DISCORD_WEBHOOK_FILE",
    )
    previous_paths = {key: getattr(paths, key) for key in path_names}
    previous_active = data.get("active")
    paths.PROFILE_DIR = profile_dir
    paths.CONFIG_FILE = os.path.join(profile_dir, "config.json")
    paths.ITEMS_FILE = os.path.join(profile_dir, "items.json")
    paths.RESULTS_DIR = os.path.join(profile_dir, "results")
    paths.UNKNOWN_DIR = os.path.join(profile_dir, "unknown_items")
    paths.UNKNOWN_QUEUE_FILE = os.path.join(profile_dir, "unknown_items.json")
    paths.ITEM_IMAGES_DIR = os.path.join(profile_dir, "items")
    paths.RESULTS_FILE = os.path.join(paths.RESULTS_DIR, "chests.json")
    paths.RESULTS_DB = os.path.join(paths.RESULTS_DIR, "chests.db")
    paths.HISTORY_DIR = os.path.join(paths.RESULTS_DIR, "history")
    paths.DISCORD_IDS_FILE = os.path.join(paths.RESULTS_DIR, "discord_message_ids.json")
    paths.DISCORD_WEBHOOK_FILE = os.path.join(profile_dir, "discord_webhook.json")
    try:
        ensure_directories()
        data["active"] = name
        save_profiles(data)
    except Exception:
        for key, value in previous_paths.items():
            setattr(paths, key, value)
        if data.get("active") != previous_active:
            data["active"] = previous_active
            try:
                save_profiles(data)
            except Exception:
                logger.exception("Failed to roll back active profile registry")
        raise
    return entry


def _profile_path_has_linked_component(base, candidate):
    """Reject symlinks/junctions in every existing component below base."""
    base = os.path.realpath(base)
    candidate = os.path.abspath(candidate)
    try:
        if os.path.commonpath((base, candidate)) != base:
            return True
        relative = os.path.relpath(candidate, base)
    except ValueError:
        return True
    if relative == os.curdir:
        return False
    current = base
    is_junction = getattr(os.path, "isjunction", None)
    for part in relative.split(os.sep):
        current = os.path.join(current, part)
        try:
            if os.path.islink(current) or (is_junction and is_junction(current)):
                return True
        except OSError:
            return True
    return False


def _validate_profile_operation_paths(*candidates):
    """Recheck profile paths immediately before filesystem mutations."""
    base = os.path.realpath(paths.BASE_DIR)
    for candidate in candidates:
        candidate = os.path.abspath(candidate)
        try:
            contained = os.path.commonpath((base, os.path.realpath(candidate))) == base
        except ValueError:
            contained = False
        if not contained or _profile_path_has_linked_component(base, candidate):
            raise ValueError("Profile operation path contains a link or changed unexpectedly.")


def _profile_directory(entry):
    """Resolve a profile directory and reject paths escaping the app folder."""
    relative = entry.get("directory", ".")
    if not isinstance(relative, str) or not relative or os.path.isabs(relative):
        raise ValueError("Некорректный путь каталога профиля.")
    base = os.path.realpath(paths.BASE_DIR)
    lexical_target = os.path.abspath(os.path.join(base, relative))
    target = os.path.realpath(lexical_target)
    if (target == base or os.path.commonpath((base, target)) != base
            or _profile_path_has_linked_component(base, lexical_target)):
        raise ValueError("Путь профиля выходит за пределы каталога приложения или является ссылкой.")
    return lexical_target


@_profile_operation_locked
def rename_profile_data(name, new_name, directory=None):
    """Rename a profile directory and registry entry with rollback on failure."""
    data = load_profiles()
    entry = next((item for item in data["profiles"]
                  if item.get("name") == name), None)
    if entry is None:
        raise ValueError("Профиль не найден.")
    if new_name == "Default":
        raise ValueError("Имя Default зарезервировано.")
    if any(item.get("name", "").casefold() == new_name.casefold()
           and item is not entry for item in data["profiles"]):
        raise ValueError("Профиль с таким именем уже существует.")

    old_dir = _profile_directory(entry)
    relative = directory or os.path.join("profiles", sanitize_filename(new_name))
    new_dir = _profile_directory({"directory": relative})
    same_path = os.path.normcase(old_dir) == os.path.normcase(new_dir)
    if not same_path and os.path.lexists(new_dir):
        raise FileExistsError("Каталог с таким именем уже существует.")

    moved = False
    old_name, old_relative = entry.get("name"), entry.get("directory", ".")
    old_active = data.get("active")
    journal = {
        "version": 1, "operation": "rename", "phase": "prepared",
        "old_name": old_name, "new_name": new_name,
        "old_target": old_dir, "new_target": new_dir,
        "created_at": datetime.now().astimezone().isoformat(),
    }
    _write_profile_operation_journal(journal)
    try:
        if not same_path and os.path.isdir(old_dir):
            _validate_profile_operation_paths(old_dir, new_dir)
            os.makedirs(os.path.dirname(new_dir), exist_ok=True)
            _validate_profile_operation_paths(old_dir, new_dir)
            os.rename(old_dir, new_dir)
            moved = True
            journal["phase"] = "directory_moved"
            _write_profile_operation_journal(journal)
        entry["name"] = new_name
        entry["directory"] = relative.replace(os.sep, "/")
        if old_active == name:
            data["active"] = new_name
        save_profiles(data)
        try:
            journal["phase"] = "committed"
            _write_profile_operation_journal(journal)
            _clear_profile_operation_journal()
        except OSError:
            logger.exception("Не удалось завершить журнал переименования; операция сохранена")
    except Exception:
        entry["name"] = old_name
        entry["directory"] = old_relative
        if old_active is not None:
            data["active"] = old_active
        if moved:
            try:
                _validate_profile_operation_paths(new_dir, old_dir)
                os.rename(new_dir, old_dir)
                _clear_profile_operation_journal()
            except (OSError, ValueError):
                logger.exception("Не удалось вернуть каталог профиля после ошибки переименования")
        else:
            _clear_profile_operation_journal()
        raise
    return {"old_dir": old_dir, "new_dir": new_dir, "was_active": old_active == name}



def _validate_profile_copy_source(source):
    """Reject links and special files in the data selected for profile copying."""
    is_junction = getattr(os.path, "isjunction", None)

    def reject_link(path):
        try:
            return os.path.islink(path) or bool(is_junction and is_junction(path))
        except OSError:
            return True

    for filename in ("config.json", "items.json"):
        path = os.path.join(source, filename)
        if not os.path.lexists(path):
            continue
        if reject_link(path) or not os.path.isfile(path):
            raise ValueError(f"Unsafe profile copy source: {filename}")

    items_root = os.path.join(source, "items")
    if not os.path.lexists(items_root):
        return
    if reject_link(items_root) or not os.path.isdir(items_root):
        raise ValueError("Unsafe profile copy source: items directory")

    pending = [items_root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if reject_link(entry.path):
                        raise ValueError(
                            f"Unsafe profile copy source: linked item {entry.name}")
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(entry.path)
                    elif not entry.is_file(follow_symlinks=False):
                        raise ValueError(
                            f"Unsafe profile copy source: special item {entry.name}")
        except OSError as exc:
            raise ValueError("Unable to inspect profile copy source") from exc


@_profile_operation_locked
def create_profile_data(name, source_dir=None):
    """Create a profile by copying only settings, item data and item images.

    History, scan results, unknown-item queues and Discord credentials are
    intentionally private to each profile and are never copied.
    """
    name = str(name).strip()
    if not name or name == "Default":
        raise ValueError("Укажите допустимое имя профиля.")
    data = load_profiles()
    if any(item.get("name", "").casefold() == name.casefold()
           for item in data["profiles"]):
        raise ValueError("Профиль с таким именем уже существует.")

    safe_name = sanitize_filename(name)
    relative = os.path.join("profiles", safe_name)
    target = _profile_directory({"directory": relative})
    if os.path.lexists(target):
        raise FileExistsError("Папка с таким именем уже существует.")

    source = os.path.realpath(source_dir or paths.PROFILE_DIR)
    if not os.path.isdir(source):
        raise FileNotFoundError("Папка исходного профиля не найдена.")
    try:
        source_contains_target = (
            source != os.path.realpath(paths.BASE_DIR)
            and os.path.commonpath((source, target)) == source
        )
    except ValueError:
        source_contains_target = False
    if source_contains_target:
        raise ValueError("Нельзя создавать профиль внутри исходного профиля.")

    _validate_profile_copy_source(source)

    parent = os.path.dirname(target)
    os.makedirs(parent, exist_ok=True)
    staging = os.path.join(parent, f".profile-create-{uuid.uuid4().hex}")
    journal = {
        "version": 1, "operation": "create", "phase": "prepared",
        "profile": name, "target": target, "staging": staging,
        "created_at": datetime.now().astimezone().isoformat(),
    }
    _write_profile_operation_journal(journal)
    try:
        os.makedirs(staging, exist_ok=False)
        for filename in ("config.json", "items.json"):
            source_file = os.path.join(source, filename)
            if os.path.isfile(source_file):
                shutil.copy2(
                    source_file, os.path.join(staging, filename), follow_symlinks=False)
        source_items = os.path.join(source, "items")
        if os.path.isdir(source_items):
            shutil.copytree(
                source_items, os.path.join(staging, "items"), symlinks=True)

        # Recheck the staged copy so links introduced during copying are rejected.
        _validate_profile_copy_source(staging)
        journal["phase"] = "staged"
        _write_profile_operation_journal(journal)
        os.rename(staging, target)
        journal["phase"] = "directory_installed"
        _write_profile_operation_journal(journal)
        entry = {"name": name, "directory": relative.replace(os.sep, "/")}
        data["profiles"].append(entry)
        try:
            save_profiles(data)
            try:
                journal["phase"] = "committed"
                _write_profile_operation_journal(journal)
                _clear_profile_operation_journal()
            except OSError:
                logger.exception("Не удалось завершить журнал создания; профиль сохранён")
        except Exception:
            try:
                os.rename(target, staging)
            except OSError:
                logger.exception("Не удалось вернуть файлы после отмены создания профиля")
            else:
                _clear_profile_operation_journal()
            raise
        return entry
    except Exception:
        if not os.path.exists(target) and not os.path.exists(staging):
            _clear_profile_operation_journal()
        raise
    finally:
        if os.path.isdir(staging):
            shutil.rmtree(staging, ignore_errors=True)



_PROFILE_ARCHIVE_VERSION = 1
_PROFILE_ARCHIVE_MAX_BYTES = 128 * 1024 * 1024
_PROFILE_ARCHIVE_MAX_FILE_BYTES = 32 * 1024 * 1024
_PROFILE_ARCHIVE_MAX_ENTRIES = 4096
_PROFILE_ARCHIVE_MAX_PATH_LENGTH = 240
_PROFILE_ARCHIVE_MAX_COMPONENT_LENGTH = 128
_PROFILE_ARCHIVE_MAX_COMPRESSION_RATIO = 200
_PROFILE_ARCHIVE_MAX_MANIFEST_BYTES = 8 * 1024 * 1024


def _safe_portable_profile_path(path):
    if (not isinstance(path, str) or not path
            or len(path) > _PROFILE_ARCHIVE_MAX_PATH_LENGTH
            or path.startswith("/") or "\\" in path):
        return False
    parts = path.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"COM{i}" for i in range(1, 10)
    } | {f"LPT{i}" for i in range(1, 10)}
    if any(
        not part or part in {".", ".."}
        or len(part) > _PROFILE_ARCHIVE_MAX_COMPONENT_LENGTH
        or ":" in part or part.rstrip(" .") != part
        or part.split(".", 1)[0].upper() in reserved
        for part in parts
    ):
        return False
    return path in {"config.json", "items.json"} or (
        len(parts) >= 2 and parts[0] == "items"
    )


def _read_portable_profile_archive(archive_path):
    try:
        archive_context = zipfile.ZipFile(archive_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("Не удалось открыть архив профиля.") from exc
    with archive_context as archive:
        infos = archive.infolist()
        if len(infos) > _PROFILE_ARCHIVE_MAX_ENTRIES:
            raise ValueError("В архиве профиля слишком много файлов.")
        exact_names = set()
        folded_names = set()
        total = 0
        for info in infos:
            path = info.filename
            if path.casefold() in folded_names:
                raise ValueError("Архив содержит повторяющиеся имена файлов.")
            folded_names.add(path.casefold())
            exact_names.add(path)
            if info.is_dir() or not (
                path == "manifest.json" or _safe_portable_profile_path(path)
            ):
                raise ValueError("Архив содержит небезопасный путь или ссылку.")
            if info.flag_bits & 0x1:
                raise ValueError("Зашифрованные ZIP-записи не поддерживаются.")
            mode = (info.external_attr >> 16) & 0o170000
            if mode and mode != stat.S_IFREG:
                raise ValueError("Архив содержит ссылки или специальные файлы.")
            if info.file_size > _PROFILE_ARCHIVE_MAX_FILE_BYTES:
                raise ValueError(f"Файл архива превышает лимит 32 МБ: {path}")
            total += info.file_size
            if total > _PROFILE_ARCHIVE_MAX_BYTES:
                raise ValueError("Размер архива превышает лимит 128 МБ.")
            if info.file_size and (
                not info.compress_size
                or info.file_size / info.compress_size > _PROFILE_ARCHIVE_MAX_COMPRESSION_RATIO
            ):
                raise ValueError(f"Подозрительная степень сжатия файла: {path}")

        if "manifest.json" not in exact_names:
            raise ValueError("В архиве отсутствует манифест.")
        manifest_info = next(info for info in infos if info.filename == "manifest.json")
        if manifest_info.file_size > _PROFILE_ARCHIVE_MAX_MANIFEST_BYTES:
            raise ValueError("Манифест архива слишком большой.")
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile, EOFError) as exc:
            raise ValueError("Манифест архива повреждён.") from exc
        if (not isinstance(manifest, dict)
                or manifest.get("format") != "ChestScannerProfile"
                or manifest.get("version") != _PROFILE_ARCHIVE_VERSION
                or not isinstance(manifest.get("files"), dict)):
            raise ValueError("Формат или версия архива не поддерживается.")

        declared = manifest["files"]
        actual = exact_names - {"manifest.json"}
        if "config.json" not in actual or "items.json" not in actual:
            raise ValueError("В архиве должны присутствовать config.json и items.json.")
        if set(declared) != actual:
            raise ValueError("Состав файлов архива не соответствует манифесту.")

        info_by_name = {info.filename: info for info in infos}
        payload = {}
        for path, metadata in declared.items():
            if not _safe_portable_profile_path(path) or not isinstance(metadata, dict):
                raise ValueError("Некорректный путь или запись файла в манифесте.")
            size = metadata.get("size")
            digest = metadata.get("sha256")
            if (isinstance(size, bool) or not isinstance(size, int)
                    or size != info_by_name[path].file_size
                    or not isinstance(digest, str) or len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest.lower())):
                raise ValueError(f"Контрольная сумма или метаданные некорректны: {path}")
            try:
                content = archive.read(path)
            except (zipfile.BadZipFile, EOFError, RuntimeError) as exc:
                raise ValueError(f"Файл архива повреждён: {path}") from exc
            if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
                raise ValueError(f"Контрольная сумма не совпадает: {path}")
            payload[path] = content

    _validate_profile_backup_payload(payload)
    return manifest, payload


@_profile_operation_locked
def export_profile_archive(name, archive_path):
    """Export validated portable profile data, excluding history and credentials."""
    data = load_profiles()
    entry = next((item for item in data["profiles"]
                  if item.get("name") == name), None)
    if entry is None:
        raise ValueError("Профиль не найден.")
    source = (os.path.realpath(paths.BASE_DIR)
              if entry.get("directory", ".") == "."
              else _profile_directory(entry))
    if not os.path.isdir(source):
        raise FileNotFoundError("Каталог профиля не найден.")

    payload = {}
    for filename in ("config.json", "items.json"):
        path = os.path.join(source, filename)
        if os.path.islink(path) or not os.path.isfile(path):
            raise FileNotFoundError(f"В профиле отсутствует или небезопасен {filename}.")
        size = os.path.getsize(path)
        if size > _PROFILE_ARCHIVE_MAX_FILE_BYTES:
            raise ValueError(f"Файл профиля превышает лимит 32 МБ: {filename}")
        with open(path, "rb") as stream:
            content = stream.read(_PROFILE_ARCHIVE_MAX_FILE_BYTES + 1)
        if len(content) != size or len(content) > _PROFILE_ARCHIVE_MAX_FILE_BYTES:
            raise OSError(f"Файл профиля изменился во время экспорта: {filename}")
        payload[filename] = content

    image_root = os.path.join(source, "items")
    if os.path.lexists(image_root) and os.path.islink(image_root):
        raise ValueError("Каталог изображений профиля является символической ссылкой.")
    folded_payload_paths = {path.casefold() for path in payload}
    if os.path.isdir(image_root):
        for root, dirs, files in os.walk(image_root, followlinks=False):
            for dirname in dirs:
                if os.path.islink(os.path.join(root, dirname)):
                    raise ValueError("В каталоге изображений обнаружена символическая ссылка.")
            for filename in files:
                full_path = os.path.join(root, filename)
                relative = os.path.relpath(full_path, source).replace(os.sep, "/")
                if (os.path.islink(full_path) or not os.path.isfile(full_path)
                        or not _safe_portable_profile_path(relative)):
                    raise ValueError(f"Недопустимый файл изображения профиля: {relative}")
                if relative.casefold() in folded_payload_paths:
                    raise ValueError(f"Повторяющийся путь файла профиля: {relative}")
                size = os.path.getsize(full_path)
                if size > _PROFILE_ARCHIVE_MAX_FILE_BYTES:
                    raise ValueError(f"Файл изображения превышает лимит 32 МБ: {relative}")
                with open(full_path, "rb") as stream:
                    content = stream.read(_PROFILE_ARCHIVE_MAX_FILE_BYTES + 1)
                if len(content) != size or len(content) > _PROFILE_ARCHIVE_MAX_FILE_BYTES:
                    raise OSError(f"Изображение изменилось во время экспорта: {relative}")
                payload[relative] = content
                folded_payload_paths.add(relative.casefold())

    _validate_profile_backup_payload(payload)
    if len(payload) + 1 > _PROFILE_ARCHIVE_MAX_ENTRIES:
        raise ValueError("В профиле слишком много файлов для архива.")
    total = sum(len(content) for content in payload.values())
    manifest = {
        "format": "ChestScannerProfile",
        "version": _PROFILE_ARCHIVE_VERSION,
        "profile_name": name,
        "files": {
            path: {"size": len(content),
                   "sha256": hashlib.sha256(content).hexdigest()}
            for path, content in sorted(payload.items())
        },
    }
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, indent=2).encode("utf-8")
    if len(manifest_bytes) > _PROFILE_ARCHIVE_MAX_MANIFEST_BYTES:
        raise ValueError("Манифест архива слишком большой.")
    if total + len(manifest_bytes) > _PROFILE_ARCHIVE_MAX_BYTES:
        raise ValueError("Размер данных профиля превышает лимит архива 128 МБ.")

    destination = os.path.abspath(os.fspath(archive_path))
    os.makedirs(os.path.dirname(destination) or ".", exist_ok=True)
    temporary = destination + f".tmp-{uuid.uuid4().hex}"
    try:
        # Stored entries avoid creating extreme compression ratios in valid exports.
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("manifest.json", manifest_bytes)
            for path, content in payload.items():
                archive.writestr(path, content)
        _read_portable_profile_archive(temporary)
        with open(temporary, "r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    return destination


@_profile_operation_locked
def import_profile_archive(archive_path, name=None):
    """Validate, stage and register a portable profile as a journaled operation."""
    manifest, payload = _read_portable_profile_archive(archive_path)
    profile_name = str(name or manifest.get("profile_name", "")).strip()
    if (not profile_name or profile_name == "Default"
            or any(ord(char) < 32 or char in '/\\\\:*?"<>|'
                   for char in profile_name)):
        raise ValueError("Укажите допустимое имя импортируемого профиля.")
    data = load_profiles()
    if any(item.get("name", "").casefold() == profile_name.casefold()
           for item in data["profiles"]):
        raise ValueError("Профиль с таким именем уже существует.")

    relative = os.path.join("profiles", sanitize_filename(profile_name))
    target = _profile_directory({"directory": relative})
    if os.path.lexists(target):
        raise FileExistsError("Папка с таким именем уже существует.")
    parent = os.path.dirname(target)
    os.makedirs(parent, exist_ok=True)
    staging = os.path.join(parent, f".profile-import-{uuid.uuid4().hex}")
    journal = {
        "version": 1, "operation": "create", "phase": "prepared",
        "profile": profile_name, "target": target, "staging": staging,
        "created_at": datetime.now().astimezone().isoformat(),
    }
    _write_profile_operation_journal(journal)
    installed = False
    committed = False
    try:
        os.makedirs(staging, exist_ok=False)
        for path, content in payload.items():
            destination = os.path.abspath(os.path.join(staging, *path.split("/")))
            if os.path.commonpath((staging, destination)) != staging:
                raise ValueError("Путь файла выходит за пределы временного каталога.")
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with open(destination, "xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        journal["phase"] = "staged"
        _write_profile_operation_journal(journal)
        os.rename(staging, target)
        installed = True
        journal["phase"] = "directory_installed"
        _write_profile_operation_journal(journal)
        entry = {"name": profile_name,
                 "directory": relative.replace(os.sep, "/")}
        data["profiles"].append(entry)
        save_profiles(data)
        committed = True
        try:
            journal["phase"] = "committed"
            _write_profile_operation_journal(journal)
            _clear_profile_operation_journal()
        except OSError:
            logger.exception("Не удалось завершить журнал импорта; профиль сохранён")
        return entry
    except Exception:
        if installed and not committed and os.path.exists(target):
            try:
                os.rename(target, staging)
            except OSError:
                logger.exception("Не удалось откатить импорт профиля")
            else:
                _clear_profile_operation_journal()
        elif not os.path.exists(target):
            _clear_profile_operation_journal()
        raise
    finally:
        if os.path.isdir(staging):
            shutil.rmtree(staging, ignore_errors=True)


_PROFILE_BACKUP_VERSION = 1
_PROFILE_BACKUP_MAX_BYTES = 2 * 1024 * 1024 * 1024
_PROFILE_BACKUP_MAX_FILE_BYTES = 512 * 1024 * 1024
_PROFILE_BACKUP_MAX_ENTRIES = 16384
_PROFILE_BACKUP_MAX_PATH_LENGTH = 240
_PROFILE_BACKUP_MAX_COMPONENT_LENGTH = 128
_PROFILE_BACKUP_MAX_COMPRESSION_RATIO = 1000
_PROFILE_BACKUP_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_PROFILE_BACKUP_RETENTION = 10


def _safe_profile_backup_path(path):
    if (not isinstance(path, str) or not path
            or len(path) > _PROFILE_BACKUP_MAX_PATH_LENGTH
            or path.startswith("/") or "\\" in path):
        return False
    parts = path.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"COM{i}" for i in range(1, 10)
    } | {f"LPT{i}" for i in range(1, 10)}
    if any(
        not part or part in {".", ".."} or len(part) > _PROFILE_BACKUP_MAX_COMPONENT_LENGTH
        or ":" in part or part.rstrip(" .") != part
        or part.split(".", 1)[0].upper() in reserved
        for part in parts
    ):
        return False
    if path == "manifest.json" or path in {
        "config.json", "items.json", "unknown_items.json", "discord_webhook.json"
    }:
        return True
    return len(parts) >= 2 and parts[0] in {"items", "results", "unknown_items"}


def _validate_profile_backup_infos(infos):
    if len(infos) > _PROFILE_BACKUP_MAX_ENTRIES:
        raise ValueError("В резервной копии слишком много файлов.")
    folded_names = set()
    exact_names = set()
    total = 0
    for info in infos:
        path = info.filename
        folded = path.casefold()
        if folded in folded_names:
            raise ValueError("Резервная копия содержит повторяющиеся имена файлов.")
        folded_names.add(folded)
        exact_names.add(path)
        if not _safe_profile_backup_path(path) or info.is_dir():
            raise ValueError(f"Резервная копия содержит небезопасный путь: {path}")
        if info.flag_bits & 0x1:
            raise ValueError("Зашифрованные ZIP-записи не поддерживаются.")
        if info.file_size > _PROFILE_BACKUP_MAX_FILE_BYTES:
            raise ValueError(f"Файл резервной копии превышает лимит 512 МБ: {path}")
        total += info.file_size
        if total > _PROFILE_BACKUP_MAX_BYTES:
            raise ValueError("Размер резервной копии превышает лимит 2 ГБ.")
        if info.file_size and (
            not info.compress_size
            or info.file_size / info.compress_size > _PROFILE_BACKUP_MAX_COMPRESSION_RATIO
        ):
            raise ValueError(f"Подозрительная степень сжатия файла: {path}")
        mode = (info.external_attr >> 16) & 0o170000
        if mode and mode != stat.S_IFREG:
            raise ValueError("Резервная копия содержит ссылки или специальные файлы.")
    return exact_names


def _validate_profile_backup_payload(payload):
    """Validate profile JSON schemas and all image references before a restore."""
    parsed = {}
    if "items.json" not in payload:
        raise ValueError("В резервной копии отсутствует items.json.")
    for filename in ("config.json", "items.json"):
        if filename not in payload:
            continue
        try:
            value = json.loads(payload[filename].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Файл {filename} содержит некорректный JSON.") from exc
        if not isinstance(value, dict):
            raise ValueError(f"Файл {filename} должен содержать JSON-объект.")
        parsed[filename] = value

    items = parsed["items.json"].get("items")
    if not isinstance(items, list):
        raise ValueError("В items.json отсутствует корректный массив items.")
    referenced_images = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not str(item.get("name", "")).strip():
            raise ValueError(f"Некорректная запись предмета №{index + 1} в items.json.")
        images = item.get("images")
        if isinstance(images, str):
            images = [images]
        if images is None:
            images = []
        if not isinstance(images, list):
            raise ValueError(f"Некорректный список иконок у предмета №{index + 1}.")
        legacy = item.get("image")
        if legacy:
            images = [*images, legacy]
        for image_path in images:
            if not is_safe_icon_path(image_path):
                raise ValueError(f"Небезопасный путь иконки в items.json: {image_path!r}")
            referenced_images.add(image_path.replace("\\", "/"))

    for image_path in referenced_images:
        if image_path not in payload:
            raise ValueError(f"В резервной копии отсутствует иконка предмета: {image_path}")

    if "unknown_items.json" in payload:
        try:
            queue = json.loads(payload["unknown_items.json"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Очередь неизвестных предметов повреждена.") from exc
        if (not isinstance(queue, dict) or not isinstance(queue.get("items"), list)
                or not all(isinstance(entry, dict) for entry in queue["items"])):
            raise ValueError("Структура очереди неизвестных предметов некорректна.")
        for entry in queue["items"]:
            image_path = entry.get("image")
            if image_path:
                if not is_safe_icon_path(image_path):
                    raise ValueError(f"Небезопасный путь иконки в очереди: {image_path!r}")
                normalized = image_path.replace("\\", "/")
                if normalized not in payload:
                    raise ValueError(f"В резервной копии отсутствует изображение очереди: {normalized}")


def _profile_backup_root(name):
    safe_name = sanitize_filename(name)
    suffix = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    backup_parent = os.path.abspath(os.path.join(paths.BASE_DIR, ".profile_backups"))
    lexical_root = os.path.join(backup_parent, f"{safe_name}-{suffix}")
    # Reject links at both trust boundaries before resolving paths. Otherwise
    # a symlink can redirect backup writes while passing realpath containment.
    if os.path.islink(backup_parent) or os.path.islink(lexical_root):
        raise ValueError("Invalid profile backup directory.")
    base = os.path.realpath(backup_parent)
    root = os.path.realpath(lexical_root)
    try:
        inside = os.path.commonpath((base, root)) == base
    except ValueError:
        inside = False
    if not inside:
        raise ValueError("Invalid profile backup directory.")
    return root


def _snapshot_profile_sqlite_database(database_path):
    """Create a consistent SQLite snapshot, including any committed WAL pages."""
    fd, snapshot_path = tempfile.mkstemp(prefix="cs-profile-db-", suffix=".sqlite")
    os.close(fd)
    source = snapshot = None
    try:
        source = sqlite3.connect(database_path, timeout=10)
        snapshot = sqlite3.connect(snapshot_path, timeout=10)
        source.backup(snapshot)
        check = snapshot.execute("PRAGMA integrity_check").fetchone()
        if not check or check[0] != "ok":
            raise ValueError(f"SQLite-база профиля повреждена: {database_path}")
        snapshot.commit()
        snapshot.close()
        snapshot = None
        source.close()
        source = None
        with open(snapshot_path, "rb") as stream:
            content = stream.read(_PROFILE_BACKUP_MAX_FILE_BYTES + 1)
        if len(content) > _PROFILE_BACKUP_MAX_FILE_BYTES:
            raise ValueError("Снимок SQLite превышает лимит 512 МБ.")
        return content
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"Не удалось создать согласованный снимок SQLite: {database_path}") from exc
    finally:
        if snapshot is not None:
            snapshot.close()
        if source is not None:
            source.close()
        try:
            os.remove(snapshot_path)
        except FileNotFoundError:
            pass

@_profile_operation_locked
def create_profile_backup(name):
    """Create a verified local full backup, including private profile data."""
    data = load_profiles()
    entry = next((item for item in data["profiles"]
                  if item.get("name") == name), None)
    if entry is None:
        raise ValueError("Профиль не найден.")
    source = (os.path.realpath(paths.BASE_DIR)
              if entry.get("directory", ".") == "."
              else _profile_directory(entry))
    if not os.path.isdir(source):
        raise FileNotFoundError("Каталог профиля не найден.")

    payload = {}
    # Default lives beside application code; back up only profile-owned paths.
    profile_entries = (
        "config.json", "items.json", "unknown_items.json",
        "discord_webhook.json", "items", "results", "unknown_items",
    )
    results_dir = os.path.join(source, "results")
    guard = (results_history_guard(os.path.join(results_dir, "chests.json"))
             if os.path.isdir(results_dir) else nullcontext())
    with guard:
        for entry_name in profile_entries:
            entry_path = os.path.join(source, entry_name)
            if not os.path.lexists(entry_path):
                continue
            if os.path.islink(entry_path):
                raise ValueError("В профиле обнаружена символическая ссылка.")
            if os.path.isfile(entry_path):
                candidates = [(entry_path, entry_name)]
            elif os.path.isdir(entry_path):
                candidates = []
                for walk_root, dirs, files in os.walk(entry_path, followlinks=False):
                    for dirname in list(dirs):
                        if os.path.islink(os.path.join(walk_root, dirname)):
                            raise ValueError("В профиле обнаружена символическая ссылка.")
                    for filename in files:
                        full_path = os.path.join(walk_root, filename)
                        relative = os.path.relpath(full_path, source).replace(os.sep, "/")
                        candidates.append((full_path, relative))
            else:
                raise ValueError("В профиле обнаружен неподдерживаемый тип файла.")
            for full_path, relative in candidates:
                if relative == "results/chests.json.lock":
                    continue
                sidecar = next((suffix for suffix in ("-wal", "-shm", "-journal")
                                if relative.endswith(suffix)), None)
                if sidecar:
                    if not os.path.isfile(full_path[:-len(sidecar)]):
                        raise ValueError(f"SQLite sidecar без основной базы: {relative}")
                    continue
                if not _safe_profile_backup_path(relative):
                    raise ValueError(f"Недопустимый путь файла профиля: {relative}")
                if os.path.islink(full_path) or not os.path.isfile(full_path):
                    raise ValueError("Резервная копия не поддерживает ссылки и спецфайлы.")
                size = os.path.getsize(full_path)
                if size > _PROFILE_BACKUP_MAX_FILE_BYTES:
                    raise ValueError(f"Файл профиля превышает лимит 512 МБ: {relative}")
                if relative in payload:
                    raise ValueError(f"Повторяющийся путь файла профиля: {relative}")
                if relative.startswith("results/") and relative.lower().endswith(".db"):
                    content = _snapshot_profile_sqlite_database(full_path)
                    size = len(content)
                else:
                    with open(full_path, "rb") as stream:
                        content = stream.read(_PROFILE_BACKUP_MAX_FILE_BYTES + 1)
                if len(content) != size or len(content) > _PROFILE_BACKUP_MAX_FILE_BYTES:
                    raise OSError(f"Файл профиля изменился во время резервного копирования: {relative}")
                payload[relative] = content
                if len(payload) + 1 > _PROFILE_BACKUP_MAX_ENTRIES:
                    raise ValueError("В профиле слишком много файлов для резервной копии.")

    total = sum(len(content) for content in payload.values())
    if total > _PROFILE_BACKUP_MAX_BYTES:
        raise ValueError("Размер профиля превышает лимит резервной копии 2 ГБ.")
    if not payload:
        raise ValueError("В профиле нет файлов для резервного копирования.")
    _validate_profile_backup_payload(payload)

    manifest = {
        "format": "ChestScannerFullProfileBackup",
        "version": _PROFILE_BACKUP_VERSION,
        "profile_name": name,
        "created_at": datetime.now().astimezone().isoformat(),
        "files": {
            path: {"size": len(content),
                   "sha256": hashlib.sha256(content).hexdigest()}
            for path, content in sorted(payload.items())
        },
    }
    root = _profile_backup_root(name)
    os.makedirs(root, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = os.path.join(root, f"{stamp}.chestbackup")
    temporary = destination + f".tmp-{uuid.uuid4().hex}"
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(
                manifest, ensure_ascii=False, indent=2).encode("utf-8"))
            for path, content in payload.items():
                archive.writestr(path, content)
        # Verify the archive before making it visible as a completed backup.
        with zipfile.ZipFile(temporary, "r") as archive:
            infos = archive.infolist()
            _validate_profile_backup_infos(infos)
            if archive.testzip() is not None:
                raise ValueError("Не удалось проверить созданную резервную копию.")
            _read_profile_backup(temporary, name)
        with open(temporary, "r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)

    backups = sorted(
        (os.path.join(root, filename) for filename in os.listdir(root)
         if filename.endswith(".chestbackup")),
        key=lambda path: (os.path.getmtime(path), os.path.basename(path)),
        reverse=True)
    for obsolete in backups[_PROFILE_BACKUP_RETENTION:]:
        try:
            os.remove(obsolete)
        except OSError:
            logger.warning("Не удалось удалить старую резервную копию: %s", obsolete)
    return destination


@_profile_operation_locked
def list_profile_backups(name):
    """Return newest valid-looking backup paths for a profile."""
    root = _profile_backup_root(name)
    if not os.path.isdir(root):
        return []
    return sorted(
        (os.path.join(root, filename) for filename in os.listdir(root)
         if filename.endswith(".chestbackup")
         and os.path.isfile(os.path.join(root, filename))),
        key=lambda path: (os.path.getmtime(path), os.path.basename(path)),
        reverse=True)


def _read_profile_backup(backup_path, expected_name):
    try:
        archive_context = zipfile.ZipFile(backup_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("Не удалось открыть резервную копию профиля.") from exc
    with archive_context as archive:
        infos = archive.infolist()
        names = _validate_profile_backup_infos(infos)
        if "manifest.json" not in names:
            raise ValueError("В резервной копии отсутствует манифест.")
        manifest_info = next(info for info in infos if info.filename == "manifest.json")
        if manifest_info.file_size > _PROFILE_BACKUP_MAX_MANIFEST_BYTES:
            raise ValueError("Манифест резервной копии слишком большой.")
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
            raise ValueError("Манифест резервной копии повреждён.") from exc
        if (not isinstance(manifest, dict)
                or manifest.get("format") != "ChestScannerFullProfileBackup"
                or manifest.get("version") != _PROFILE_BACKUP_VERSION
                or manifest.get("profile_name") != expected_name
                or not isinstance(manifest.get("files"), dict)):
            raise ValueError("Резервная копия не подходит этому профилю или версии приложения.")

        declared = manifest["files"]
        actual = set(names) - {"manifest.json"}
        if "items.json" not in actual:
            raise ValueError("В резервной копии отсутствует items.json.")
        if not actual or set(declared) != actual:
            raise ValueError("Состав файлов не соответствует манифесту.")
        payload = {}
        info_by_name = {info.filename: info for info in infos}
        for path, metadata in declared.items():
            if not _safe_profile_backup_path(path) or not isinstance(metadata, dict):
                raise ValueError("Некорректный путь или запись в манифесте.")
            declared_size = metadata.get("size")
            digest = metadata.get("sha256")
            if (isinstance(declared_size, bool) or not isinstance(declared_size, int)
                    or declared_size != info_by_name[path].file_size
                    or not isinstance(digest, str) or len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest.lower())):
                raise ValueError(f"Контрольная сумма или метаданные файла некорректны: {path}")
            try:
                content = archive.read(path)
            except (zipfile.BadZipFile, EOFError, RuntimeError) as exc:
                raise ValueError(f"Файл резервной копии повреждён: {path}") from exc
            if (len(content) != declared_size
                    or hashlib.sha256(content).hexdigest() != digest):
                raise ValueError(f"Контрольная сумма не совпадает: {path}")
            payload[path] = content
    _validate_profile_backup_payload(payload)
    return payload


def _profile_operation_journal_path():
    return os.path.join(paths.BASE_DIR, ".profile-operation.json")


def _validate_profile_operation_journal_path(path=None):
    """Require the journal entry to be a regular, unlinked file in BASE_DIR."""
    base = os.path.realpath(paths.BASE_DIR)
    candidate = os.path.abspath(path or _profile_operation_journal_path())
    if _profile_path_has_linked_component(base, candidate):
        raise ValueError("Profile operation journal path contains a link or junction.")
    try:
        metadata = os.lstat(candidate)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("Profile operation journal is not a regular file.")
    return True


def _read_profile_operation_journal(path):
    """Read only after the opened handle is proven to match the checked file."""
    _validate_profile_operation_journal_path(path)
    before = os.lstat(path)
    descriptor = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        current = os.lstat(path)
        if (not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)):
            raise ValueError("Profile operation journal changed while opening.")
        _validate_profile_operation_journal_path(path)
        stream = os.fdopen(descriptor, "r", encoding="utf-8")
        descriptor = None
        with stream:
            return json.load(stream)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_profile_operation_journal(record):
    """Durably replace the small transaction journal."""
    destination = _profile_operation_journal_path()
    _validate_profile_operation_journal_path(destination)
    temporary = destination + f".tmp-{uuid.uuid4().hex}"
    try:
        with open(temporary, "x", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        _validate_profile_operation_journal_path(destination)
        _validate_profile_operation_journal_path(temporary)
        os.replace(temporary, destination)
    finally:
        if os.path.lexists(temporary):
            if _validate_profile_operation_journal_path(temporary):
                os.remove(temporary)


def _clear_profile_operation_journal():
    journal_path = _profile_operation_journal_path()
    if _validate_profile_operation_journal_path(journal_path):
        os.remove(journal_path)


def _registry_has_profile(name, directory):
    try:
        data = load_profiles()
        return any(item.get("name") == name
                   and os.path.normcase(_profile_directory(item))
                   == os.path.normcase(directory)
                   for item in data.get("profiles", []))
    except Exception:
        return False


@_profile_operation_locked
def recover_profile_operations():
    """Recover only transaction states whose registry/filesystem agree."""
    findings = []
    journal_path = _profile_operation_journal_path()
    if os.path.lexists(journal_path):
        try:
            if not _validate_profile_operation_journal_path(journal_path):
                raise ValueError("Profile operation journal disappeared during recovery.")
            record = _read_profile_operation_journal(journal_path)
            operation = record.get("operation") if isinstance(record, dict) else None
            if record.get("version") != 1 or operation not in {
                    "restore", "create", "rename", "delete"}:
                raise ValueError("Некорректная запись журнала операций")
            base = os.path.realpath(paths.BASE_DIR)
            resolved = {}
            for key in ("target", "staging", "rollback", "old_target", "new_target"):
                value = record.get(key)
                if value is None:
                    continue
                if not isinstance(value, str):
                    raise ValueError("Некорректный путь в журнале операций")
                candidate = os.path.abspath(value)
                if (os.path.commonpath((base, os.path.realpath(candidate))) != base
                        or _profile_path_has_linked_component(base, candidate)):
                    raise ValueError("Путь в журнале выходит за пределы приложения")
                resolved[key] = candidate

            phase = record.get("phase")
            if operation == "restore":
                if phase not in {"prepared", "old_moved", "new_installed", "committed"}:
                    raise ValueError("Invalid restore phase")
                target, staging, rollback = (
                    resolved[k] for k in ("target", "staging", "rollback"))
                if os.path.exists(rollback) and not os.path.exists(target):
                    if not os.path.isdir(rollback):
                        findings.append(
                            "\u041a\u0430\u0442\u0430\u043b\u043e\u0433 \u043e\u0442\u043a\u0430\u0442\u0430 \u043f\u0440\u043e\u0444\u0438\u043b\u044f \u043f\u043e\u0432\u0440\u0435\u0436\u0434\u0451\u043d; "
                            "\u0436\u0443\u0440\u043d\u0430\u043b \u0438 \u0434\u0430\u043d\u043d\u044b\u0435 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u044b \u0434\u043b\u044f \u0440\u0443\u0447\u043d\u043e\u0439 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0438.")
                    else:
                        os.rename(rollback, target)
                        findings.append(
                            f"\u0412\u043e\u0441\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d \u0438\u0441\u0445\u043e\u0434\u043d\u044b\u0439 \u043a\u0430\u0442\u0430\u043b\u043e\u0433: {os.path.basename(target)}")
                        _clear_profile_operation_journal()
                elif (phase == "committed" and record.get("preserve_rollback") is True
                      and os.path.isdir(target) and os.path.isdir(rollback)):
                    _clear_profile_operation_journal()
                    findings.append(
                        f"Original profile retained for manual recovery: {os.path.basename(rollback)}")
                elif (phase == "committed" and os.path.isdir(target)
                      and os.path.isdir(rollback)
                      and os.path.dirname(target) == os.path.dirname(rollback)
                      and os.path.basename(rollback).startswith(".profile-rollback-")):
                    shutil.rmtree(rollback)
                    _clear_profile_operation_journal()
                    findings.append(
                        f"\u0417\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u0430 \u043e\u0447\u0438\u0441\u0442\u043a\u0430 \u043e\u0442\u043a\u0430\u0442\u0430 \u043f\u0440\u043e\u0444\u0438\u043b\u044f: {os.path.basename(target)}")
                elif os.path.exists(target) and not os.path.exists(rollback):
                    if phase in {"new_installed", "committed"}:
                        _clear_profile_operation_journal()
                    elif phase == "prepared" and record.get("had_original") is False:
                        _clear_profile_operation_journal()
                        findings.append(
                            f"Restore installed for {os.path.basename(target)} before journal phase update.")
                    elif phase == "prepared":
                        _clear_profile_operation_journal()
                        findings.append(
                            f"\u0412\u043e\u0441\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u0435 \u043f\u0440\u043e\u0444\u0438\u043b\u044f {os.path.basename(target)} "
                            "\u043d\u0435 \u043d\u0430\u0447\u0430\u043b\u043e\u0441\u044c; \u0438\u0441\u0445\u043e\u0434\u043d\u044b\u0435 \u0434\u0430\u043d\u043d\u044b\u0435 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u044b.")
                elif (record.get("had_original") is False
                      and not os.path.lexists(target)
                      and not os.path.lexists(rollback)
                      and os.path.isdir(staging)):
                    _clear_profile_operation_journal()
                    findings.append(
                        f"Restore for {os.path.basename(target)} was not installed; staging retained.")
                else:
                    findings.append(
                        "\u0421\u043e\u0441\u0442\u043e\u044f\u043d\u0438\u0435 \u0432\u043e\u0441\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u044f \u043d\u0435\u043e\u0434\u043d\u043e\u0437\u043d\u0430\u0447\u043d\u043e; "
                        "\u0436\u0443\u0440\u043d\u0430\u043b \u0438 \u0434\u0430\u043d\u043d\u044b\u0435 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u044b.")

            elif operation == "rename":
                old_target, new_target = (resolved[k]
                                          for k in ("old_target", "new_target"))
                old_name, new_name = record.get("old_name"), record.get("new_name")
                data = load_profiles()
                new_registered = any(
                    item.get("name") == new_name
                    and os.path.normcase(_profile_directory(item))
                    == os.path.normcase(new_target) for item in data["profiles"])
                old_registered = any(
                    item.get("name") == old_name
                    and os.path.normcase(_profile_directory(item))
                    == os.path.normcase(old_target) for item in data["profiles"])
                if new_registered and os.path.isdir(new_target):
                    _clear_profile_operation_journal()
                    findings.append(f"\u041f\u0435\u0440\u0435\u0438\u043c\u0435\u043d\u043e\u0432\u0430\u043d\u0438\u0435 \u043f\u0440\u043e\u0444\u0438\u043b\u044f \u00ab{old_name}\u00bb \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u043e.")
                elif (old_registered and not os.path.lexists(old_target)
                      and os.path.isdir(new_target)):
                    _validate_profile_operation_paths(new_target, old_target)
                    os.makedirs(os.path.dirname(old_target), exist_ok=True)
                    _validate_profile_operation_paths(new_target, old_target)
                    os.rename(new_target, old_target)
                    _clear_profile_operation_journal()
                    findings.append(f"\u041e\u0442\u043c\u0435\u043d\u0435\u043d\u043e \u043d\u0435\u0437\u0430\u0432\u0435\u0440\u0448\u0451\u043d\u043d\u043e\u0435 \u043f\u0435\u0440\u0435\u0438\u043c\u0435\u043d\u043e\u0432\u0430\u043d\u0438\u0435 \u043f\u0440\u043e\u0444\u0438\u043b\u044f \u00ab{old_name}\u00bb.")
                elif (old_registered and (
                        os.path.normcase(old_target) == os.path.normcase(new_target)
                        or not os.path.lexists(new_target))):
                    _clear_profile_operation_journal()
                else:
                    findings.append(
                        "????????? ?????????????? ??????? ????????????; "
                        "???????? ? ?????? ?????????.")

            elif operation == "delete":
                name = record.get("profile")
                target, staging = resolved["target"], resolved["staging"]
                registered = any(item.get("name") == name
                                  for item in load_profiles()["profiles"])
                if (registered and os.path.isdir(staging)
                        and not os.path.lexists(target)):
                    _validate_profile_operation_paths(staging, target)
                    os.rename(staging, target)
                    _clear_profile_operation_journal()
                    findings.append(f"\u0412\u043e\u0437\u0432\u0440\u0430\u0449\u0451\u043d \u043a\u0430\u0442\u0430\u043b\u043e\u0433 \u043f\u0440\u043e\u0444\u0438\u043b\u044f \u00ab{name}\u00bb \u043f\u043e\u0441\u043b\u0435 \u0441\u0431\u043e\u044f \u0443\u0434\u0430\u043b\u0435\u043d\u0438\u044f.")
                elif (registered and os.path.isdir(target)
                      and not os.path.lexists(staging)):
                    _clear_profile_operation_journal()
                elif not registered:
                    # Registry removal is the commit point. Never erase staged user data
                    # during startup; leave it for manual inspection.
                    _clear_profile_operation_journal()
                    if os.path.exists(staging):
                        findings.append(
                            f"Профиль «{name}» удалён из списка, но его временные файлы "
                            "сохранены для ручной проверки.")
                else:
                    findings.append(
                        "Состояние удаления профиля неоднозначно; данные сохранены.")

            elif operation == "create":
                name = record.get("profile")
                target, staging = resolved["target"], resolved["staging"]
                if (not isinstance(name, str) or not name.strip()
                        or phase not in {"prepared", "staged", "directory_installed", "committed"}):
                    raise ValueError("Invalid profile creation journal state")
                registered = _registry_has_profile(name, target)
                if (registered and os.path.isdir(target)
                        and phase in {"directory_installed", "committed"}):
                    _clear_profile_operation_journal()
                    findings.append(
                        f"\u0421\u043e\u0437\u0434\u0430\u043d\u0438\u0435 \u043f\u0440\u043e\u0444\u0438\u043b\u044f \u00ab{name}\u00bb \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u043e.")
                elif not registered and not os.path.lexists(target):
                    # Incomplete staging is intentionally retained for audit.
                    _clear_profile_operation_journal()
                    if os.path.lexists(staging):
                        findings.append(
                            f"\u041d\u0435\u0437\u0430\u0432\u0435\u0440\u0448\u0451\u043d\u043d\u043e\u0435 \u0441\u043e\u0437\u0434\u0430\u043d\u0438\u0435 \u043f\u0440\u043e\u0444\u0438\u043b\u044f \u00ab{name}\u00bb; "
                            "\u0432\u0440\u0435\u043c\u0435\u043d\u043d\u044b\u0435 \u0434\u0430\u043d\u043d\u044b\u0435 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u044b \u0434\u043b\u044f \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0438.")
                else:
                    findings.append(
                        "\u0421\u043e\u0441\u0442\u043e\u044f\u043d\u0438\u0435 \u0441\u043e\u0437\u0434\u0430\u043d\u0438\u044f \u043f\u0440\u043e\u0444\u0438\u043b\u044f \u043d\u0435\u043e\u0434\u043d\u043e\u0437\u043d\u0430\u0447\u043d\u043e; \u0436\u0443\u0440\u043d\u0430\u043b \u0438 \u0434\u0430\u043d\u043d\u044b\u0435 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u044b.")
        except Exception as exc:
            logger.exception("Не удалось обработать журнал операций профилей")
            findings.append(f"Журнал операций профилей требует проверки: {exc}")

    base = os.path.realpath(paths.BASE_DIR)
    candidates = []
    seen_candidates = set()
    scan_roots = (base, os.path.join(base, "profiles"),
                  os.path.join(base, ".profile_backups"))
    is_junction = getattr(os.path, "isjunction", None)
    for parent in scan_roots:
        if (not os.path.isdir(parent)
                or _profile_path_has_linked_component(base, parent)):
            continue
        try:
            for current, directories, filenames in os.walk(parent, followlinks=False):
                def is_linked_directory(name):
                    candidate = os.path.join(current, name)
                    try:
                        return (os.path.islink(candidate)
                                or bool(is_junction and is_junction(candidate)))
                    except OSError:
                        return True

                directories[:] = [name for name in directories
                                  if not is_linked_directory(name)]
                for name in directories + filenames:
                    if (name.startswith((".profile-restore-", ".profile-rollback-",
                                         ".profile-delete-", ".profile-create-",
                                         ".profile-import-", ".profile-operation.json.tmp-"))
                            or ".chestbackup.tmp-" in name):
                        candidate = os.path.join(current, name)
                        identity = os.path.normcase(os.path.realpath(candidate))
                        if identity not in seen_candidates:
                            seen_candidates.add(identity)
                            candidates.append(candidate)
        except OSError:
            logger.exception("Не удалось проверить временные файлы профилей: %s", parent)
    if candidates:
        findings.append(
            f"Обнаружены временные данные профилей ({len(candidates)}); "
            "они не удалялись: " + ", ".join(os.path.basename(x) for x in candidates[:8]))
    for finding in findings:
        logger.warning("Profile recovery: %s", finding)
    return findings


@_profile_operation_locked
def restore_profile_backup(name, backup_path):
    """Replace a non-active profile with a verified backup and journal the swap."""
    data = load_profiles()
    if name == "Default" or data.get("active") == name:
        raise ValueError("Для восстановления сначала переключитесь на другой профиль.")
    entry = next((item for item in data["profiles"]
                  if item.get("name") == name), None)
    if entry is None:
        raise ValueError("Профиль не найден.")
    payload = _read_profile_backup(backup_path, name)
    target = _profile_directory(entry)
    if os.path.lexists(target) and not os.path.isdir(target):
        raise FileExistsError("Profile target exists and is not a directory.")
    had_original = os.path.isdir(target)
    parent = os.path.dirname(target)
    base = os.path.realpath(paths.BASE_DIR)
    if _profile_path_has_linked_component(base, target):
        raise ValueError("Profile path contains a symbolic link or junction.")
    staging = os.path.join(parent, f".profile-restore-{uuid.uuid4().hex}")
    rollback = os.path.join(parent, f".profile-rollback-{uuid.uuid4().hex}")
    journal = {
        "version": 1, "operation": "restore", "phase": "prepared",
        "profile": name, "target": target, "staging": staging,
        "rollback": rollback, "had_original": had_original,
        "preserve_rollback": False,
        "created_at": datetime.now().astimezone().isoformat(),
    }
    os.makedirs(staging, exist_ok=False)
    try:
        for path, content in payload.items():
            destination = os.path.abspath(os.path.join(staging, *path.split("/")))
            if os.path.commonpath((staging, destination)) != staging:
                raise ValueError("Путь файла выходит за пределы временного каталога.")
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with open(destination, "xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        if had_original:
            try:
                create_profile_backup(name)
            except ValueError:
                # Preserve exact bytes of damaged data when it cannot be
                # represented as a validated pre-restore backup.
                journal["preserve_rollback"] = True
                logger.exception(
                    "Current profile could not be validated for pre-restore backup; "
                    "the original directory will be retained for manual recovery")
        _write_profile_operation_journal(journal)
        if any(_profile_path_has_linked_component(base, path)
               for path in (target, staging, rollback)):
            raise ValueError("Profile restore path contains a symbolic link or junction.")
        if had_original:
            os.rename(target, rollback)
            journal["phase"] = "old_moved"
            _write_profile_operation_journal(journal)
        if any(_profile_path_has_linked_component(base, path)
               for path in (target, staging, rollback)):
            raise ValueError("Profile restore path contains a symbolic link or junction.")
        os.rename(staging, target)
        journal["phase"] = "new_installed"
        _write_profile_operation_journal(journal)
        journal["phase"] = "committed"
        _write_profile_operation_journal(journal)
        if journal["preserve_rollback"] and os.path.isdir(rollback):
            logger.warning(
                "Original profile directory retained for manual recovery: %s", rollback)
            _clear_profile_operation_journal()
        elif os.path.isdir(rollback):
            try:
                shutil.rmtree(rollback)
            except OSError:
                # Keep the committed journal so startup recovery can retry cleanup.
                logger.exception("Failed to remove profile rollback after restore")
            else:
                if not os.path.lexists(rollback):
                    _clear_profile_operation_journal()
                else:
                    logger.warning("Profile rollback directory remains after cleanup; journal retained")
        else:
            _clear_profile_operation_journal()
        return True
    except Exception:
        # Best-effort synchronous rollback; if the process dies, startup recovery
        # uses the durable journal and only acts when target is definitely absent.
        if os.path.exists(rollback) and not os.path.exists(target):
            try:
                os.rename(rollback, target)
                _clear_profile_operation_journal()
            except OSError:
                logger.exception("Не удалось вернуть исходный профиль после ошибки")
        elif os.path.exists(target) and not os.path.exists(rollback):
            # The first rename did not happen: the original target is untouched.
            try:
                _clear_profile_operation_journal()
            except OSError:
                logger.exception("Не удалось очистить журнал не начатой операции")
        elif not had_original and not os.path.lexists(target) and not os.path.lexists(rollback):
            try:
                _clear_profile_operation_journal()
            except OSError:
                logger.exception("Failed to clear an uninstalled missing-profile restore journal")
        raise
    finally:
        if os.path.isdir(staging):
            shutil.rmtree(staging, ignore_errors=True)



@_profile_operation_locked
def delete_profile_data(name):
    """Remove registry entry first; stage files so registry failures are reversible.

    Returns the leftover staging directory when physical cleanup fails, otherwise
    None. A leftover is deliberately retained rather than risking data loss.
    """
    data = load_profiles()
    if name == "Default" or data.get("active") == name:
        raise ValueError("Нельзя удалить профиль Default или активный профиль.")
    entry = next((item for item in data["profiles"]
                  if item.get("name") == name), None)
    if entry is None:
        return None

    target = _profile_directory(entry)
    if os.path.isdir(target):
        # A verified local backup must exist before the profile is removed.
        create_profile_backup(name)
    staging = os.path.join(
        os.path.realpath(paths.BASE_DIR),
        f".profile-delete-{uuid.uuid4().hex}")
    journal = {
        "version": 1, "operation": "delete", "phase": "prepared",
        "profile": name, "target": target, "staging": staging,
        "created_at": datetime.now().astimezone().isoformat(),
    }
    _write_profile_operation_journal(journal)
    moved = False
    if os.path.lexists(target):
        _validate_profile_operation_paths(target, staging)
        os.rename(target, staging)
        moved = True
        journal["phase"] = "directory_staged"
        _write_profile_operation_journal(journal)

    old_profiles = data["profiles"]
    data["profiles"] = [item for item in old_profiles if item is not entry]
    try:
        save_profiles(data)
        try:
            journal["phase"] = "registry_removed"
            _write_profile_operation_journal(journal)
            _clear_profile_operation_journal()
        except OSError:
            logger.exception("Не удалось завершить журнал удаления; профиль удалён из списка")
    except Exception:
        data["profiles"] = old_profiles
        if moved:
            try:
                _validate_profile_operation_paths(staging, target)
                os.rename(staging, target)
            except (OSError, ValueError):
                logger.exception("Не удалось восстановить каталог профиля после отмены удаления")
            else:
                _clear_profile_operation_journal()
        else:
            _clear_profile_operation_journal()
        raise

    if moved:
        try:
            _validate_profile_operation_paths(staging)
            shutil.rmtree(staging)
        except (OSError, ValueError):
            return staging
    return None


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
        else:
            # Проверка геометрии: при корректной калибровке ячейки примерно
            # квадратные. Резко вытянутая «сетка» ломает поиск заголовка.
            cw = (grid["x2"] - grid["x1"]) / max(1, grid["columns"])
            ch = (grid["y2"] - grid["y1"]) / max(1, grid["rows"])
            if not (0.4 <= cw / ch <= 2.5):
                cfg["chest_grid"] = paths.DEFAULT_CONFIG["chest_grid"]
                warnings.append("chest_grid: ячейки не квадратные — сброшено "
                                "на стандартную сетку, нужна перекалибровка")
    for field, lo, hi in [("chest_template_threshold", 0.01, 1.0),
                          ("chest_template_scale", 0.4, 2.5),
                          ("empty_slot_threshold", 1, 100000),
                          ("item_match_threshold", 1, 1000000),
                          ("item_match_margin", 0, 100000),
                          ("phash_threshold", 0, 64),
                          ("scan_interval_seconds", 0.1, 3600)]:
        v = cfg.get(field)
        if not isinstance(v, (int, float)) or not (lo <= v <= hi):
            cfg[field] = paths.DEFAULT_CONFIG[field]
            warnings.append(f"{field}={v!r} вне диапазона")
    # Миграция порога шаблона сундука: старое значение по умолчанию 0.82
    # допускало ложные срабатывания детекции на других интерфейсах и тёмных
    # участках мира; новый дефолт — 0.88 (см. plans/auto-detect-grid.md).
    if cfg.get("chest_template_threshold") == 0.82:
        cfg["chest_template_threshold"] = paths.DEFAULT_CONFIG["chest_template_threshold"]
        warnings.append("chest_template_threshold: 0.82 → 0.88 "
                        "(исправлены ложные срабатывания при закрытом сундуке)")
    for field in ("item_detect_cache", "tray_minimize_to_tray", "notify_on_scan"):
        if not isinstance(cfg.get(field), bool):
            cfg[field] = paths.DEFAULT_CONFIG[field]
            warnings.append(f"{field} повреждён (ожидался bool)")
    if cfg.get("capture_source") not in ("screen", "window"):
        cfg["capture_source"] = paths.DEFAULT_CONFIG["capture_source"]
        warnings.append("capture_source вне списка screen/window")
    if not isinstance(cfg.get("game_window_title"), str) or not cfg["game_window_title"].strip():
        cfg["game_window_title"] = paths.DEFAULT_CONFIG["game_window_title"]
        warnings.append("game_window_title повреждён")
    for roi in ("icon_roi_in_slot", "count_roi_in_slot"):
        r = cfg.get(roi)
        if not isinstance(r, list) or len(r) != 4 or not all(isinstance(x, (int, float)) for x in r):
            cfg[roi] = paths.DEFAULT_CONFIG[roi]
            warnings.append(f"{roi} повреждён")
    dm = cfg.get("discord_message")
    if not isinstance(dm, dict):
        cfg["discord_message"] = paths.DEFAULT_CONFIG["discord_message"]
        warnings.append("discord_message повреждён")
    # Примечание: legacy-ключ "github_db" из старых конфигов остаётся в cfg,
    # но больше нигде не используется и безвреден.
    hk_defaults = paths.DEFAULT_CONFIG["hotkeys"]
    hk = cfg.get("hotkeys")
    if not isinstance(hk, dict):
        cfg["hotkeys"] = dict(hk_defaults)
        warnings.append("hotkeys повреждён")
    else:
        fixed = {}
        from chest_scanner.hotkeys import is_valid_hotkey_name
        for action, default in hk_defaults.items():
            val = hk.get(action, default)
            if not isinstance(val, str) or not val.strip():
                fixed[action] = ""
            else:
                normalized = val.strip().lower()
                fixed[action] = normalized if is_valid_hotkey_name(normalized) else default
        cfg["hotkeys"] = fixed
    return cfg, warnings
