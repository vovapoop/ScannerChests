"""Общие утилиты: JSON-доступ, пути, создание директорий."""
import contextlib
import copy
import datetime
import json
import ntpath
import os
import shutil
import tempfile
import threading
import time

from chest_scanner import paths
from chest_scanner.logging_setup import LOGS_DIR, logger

_JSON_LOCKS = {}
_JSON_LOCKS_GUARD = threading.Lock()
_RESULTS_HISTORY_LOCK = threading.RLock()
_PROFILE_DATA_LOCK = threading.RLock()
_PROCESS_FILE_LOCKS = {}
_PROCESS_FILE_LOCKS_GUARD = threading.Lock()
_PROCESS_FILE_LOCK_STATE = threading.local()


def profile_data_guard():
    """Serialize JSON writes with profile path changes and reloads."""
    return _PROFILE_DATA_LOCK


def results_history_lock():
    """Shared re-entrant lock for every mutation/rebuild of scan history."""
    return _RESULTS_HISTORY_LOCK


def _quarantine_corrupt_json(path):
    """Move an unreadable JSON file aside without overwriting older recovery data."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = f"{path}.corrupt-{stamp}"
    suffix = 1
    while os.path.exists(destination):
        destination = f"{path}.corrupt-{stamp}-{suffix}"
        suffix += 1
    os.replace(path, destination)
    return destination


def load_json_recover(path, default, validator, *, allow_empty=False):
    """Load JSON, restoring a validated .backup or quarantining corrupt data.

    If no valid recovery copy exists, fail closed unless allow_empty is set.
    The corrupt source is always preserved under a timestamped name.
    """
    def read_valid(candidate):
        with open(candidate, "r", encoding="utf-8") as stream:
            value = json.load(stream)
        if not validator(value):
            raise ValueError(f"Invalid JSON structure: {candidate}")
        return value

    if not os.path.exists(path):
        backup_path = f"{path}.backup"
        if os.path.isfile(backup_path):
            try:
                recovered = read_valid(backup_path)
            except (OSError, json.JSONDecodeError, UnicodeError, ValueError) as exc:
                logger.error(f"Invalid JSON recovery copy {backup_path}: {exc}")
            else:
                save_json(path, recovered)
                logger.warning(f"Recovered missing {path} from backup")
                return recovered
        directory = os.path.dirname(path) or "."
        damaged = []
        if os.path.isdir(directory):
            damaged = [
                name for name in os.listdir(directory)
                if name.startswith(os.path.basename(path) + ".corrupt-")]
        if damaged and not allow_empty:
            raise ValueError(
                f"Найдены повреждённые копии {path}; автоматическое создание пустого файла запрещено.")
        return copy.deepcopy(default)

    try:
        return read_valid(path)
    except (json.JSONDecodeError, UnicodeError, ValueError) as source_error:
        backup_path = f"{path}.backup"
        recovered = None
        if os.path.isfile(backup_path):
            try:
                recovered = read_valid(backup_path)
            except (OSError, json.JSONDecodeError, UnicodeError, ValueError) as backup_error:
                logger.error(f"Invalid JSON recovery copy {backup_path}: {backup_error}")

        quarantined = _quarantine_corrupt_json(path)
        if recovered is not None:
            save_json(path, recovered)
            logger.warning(
                f"Recovered {path} from backup; damaged source preserved at {quarantined}")
            return recovered

        logger.error(
            f"No valid backup for {path}; damaged source preserved at {quarantined}: "
            f"{source_error}")
        if allow_empty:
            return copy.deepcopy(default)
        raise ValueError(
            f"Файл повреждён и резервная копия недоступна: {path}. "
            f"Исходный файл сохранён: {quarantined}") from source_error


@contextlib.contextmanager
def _file_process_lock(lock_path):
    """A per-file inter-process lock that is reentrant within one thread."""
    key = os.path.normcase(os.path.abspath(lock_path))
    with _PROCESS_FILE_LOCKS_GUARD:
        thread_lock = _PROCESS_FILE_LOCKS.setdefault(key, threading.RLock())
    with thread_lock:
        held = getattr(_PROCESS_FILE_LOCK_STATE, "held", None)
        if held is None:
            held = {}
            _PROCESS_FILE_LOCK_STATE.held = held
        if key in held:
            held[key] += 1
            try:
                yield
            finally:
                held[key] -= 1
            return

        os.makedirs(os.path.dirname(key), exist_ok=True)
        with open(key, "a+b") as lock_file:
            if os.name == "nt":
                import msvcrt

                if os.path.getsize(key) == 0:
                    lock_file.write(b"0")
                    lock_file.flush()
                lock_file.seek(0)
                while True:
                    try:
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        time.sleep(0.05)
                held[key] = 1
                try:
                    yield
                finally:
                    held.pop(key, None)
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                held[key] = 1
                try:
                    yield
                finally:
                    held.pop(key, None)
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def results_history_guard(lock_path=None):
    """Serialize history operations across threads and application processes."""
    # Lock order is always profile-data -> history -> inter-process file lock.
    # save_json() also takes the profile-data lock, so acquiring it here first
    # prevents a scan writer (history -> profile-data) from deadlocking against
    # profile operations that already hold profile-data before taking history.
    with _PROFILE_DATA_LOCK:
        with _RESULTS_HISTORY_LOCK:
            target = lock_path or os.path.join(paths.BASE_DIR, "results.json")
            with _file_process_lock(os.path.abspath(target) + ".lock"):
                yield


def _json_path_lock(path):
    key = os.path.normcase(os.path.abspath(path))
    with _JSON_LOCKS_GUARD:
        return _JSON_LOCKS.setdefault(key, threading.RLock())




def ensure_directories():
    """Создаёт все служебные директории приложения."""
    for p in (paths.ASSETS_DIR, paths.DIGITS_DIR, paths.PROFILES_DIR, paths.PROFILE_DIR,
              paths.RESULTS_DIR, paths.HISTORY_DIR, paths.UNKNOWN_DIR, paths.ITEM_IMAGES_DIR,
              LOGS_DIR):
        os.makedirs(p, exist_ok=True)


def absolute_path(path):
    """Resolve application/profile-relative paths without mangling traversal.

    Item and unknown-item assets live in the active profile; other relative
    paths live beside the application. Both Windows and POSIX separators are
    accepted so profile data remains portable between operating systems.
    """
    if path is None or path == "":
        return None
    path = os.fsdecode(os.fspath(path))
    if not path:
        return None
    # Recognize Windows absolute paths even when inspecting data on POSIX.
    if os.path.isabs(path) or ntpath.isabs(path):
        return path

    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    parts = normalized.split("/")
    if parts[0] in ("items", "unknown_items"):
        return os.path.join(paths.PROFILE_DIR, *parts)
    return os.path.join(paths.BASE_DIR, *parts)


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
    """Save JSON atomically using a unique temporary file in the same folder.

    A unique temp name prevents simultaneous writers from sharing or deleting
    one another's in-progress file. The final replace remains atomic on the
    same filesystem.
    """
    with profile_data_guard(), _json_path_lock(path), _file_process_lock(os.path.abspath(path) + ".lock"):
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        prefix = f".{os.path.basename(path)}."
        fd, tmp = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                try:
                    os.fsync(stream.fileno())
                except OSError:
                    pass
            if backup and os.path.exists(path):
                backup_path = f"{path}.backup"
                backup_fd, backup_tmp = tempfile.mkstemp(
                    prefix=f".{os.path.basename(path)}.backup.",
                    suffix=".tmp", dir=directory)
                os.close(backup_fd)
                try:
                    shutil.copy2(path, backup_tmp)
                    with open(backup_tmp, "r+b") as backup_stream:
                        os.fsync(backup_stream.fileno())
                    os.replace(backup_tmp, backup_path)
                finally:
                    try:
                        if os.path.exists(backup_tmp):
                            os.remove(backup_tmp)
                    except OSError:
                        pass
            for attempt in range(5):
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.025 * (attempt + 1))
        finally:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
